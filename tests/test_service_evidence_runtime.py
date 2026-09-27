from __future__ import annotations

from concurrent.futures import ThreadPoolExecutor
from contextlib import nullcontext
from dataclasses import replace
import json
from pathlib import Path
import subprocess
import sys
import threading

import pytest

import release_security
import service_contracts as base
import service_evidence_contracts as contracts
import service_evidence_runtime as transport
import service_runtime
import service_runtime_binding
import storage_policy


def config_for(root):
    return base.CorpusConfig(corpus_id="book", db_path=root / "db",
                            chunks_path=root / "chunks.jsonl", collection_name="book",
                            embedding_model="test-model", search_timeout_seconds=2)


def empty_result(corpus_id="book", request_id="request-1", mode="vector"):
    digests = {name: None for name in contracts.GENERATION_DIGEST_FIELDS}
    digests.update(index_manifest_sha256="a" * 64, chunks_sha256="b" * 64)
    return {
        "schema_version": 1, "kind": "service_evidence_search",
        "search": {"schema_version": 1, "request_id": request_id, "corpus_id": corpus_id,
                   "backend": "qdrant", "requested_mode": mode, "effective_mode": "vector",
                   "reranker_applied": False, "warnings": [], "hits": []},
        "generation": {**digests, "generation_id": contracts.generation_id(digests)},
        "evidence": [],
    }


def _request():
    return base.parse_search_request({"query": "private query", "mode": "vector"})


def _payload(root):
    return transport._request_payload(config_for(root), contracts.EvidenceCorpusConfig("book"),
                                      _request(), "request-1", security_policy=None)


def _binding(supervisor):
    return replace(service_runtime_binding.default_service_runtime_binding(), supervisor=supervisor)


def test_contained_roundtrip_exact_binding_and_private_request_cleanup(tmp_path):
    captured = {}
    policy = release_security.ReleaseSecurityPolicy()

    def execute(config, evidence, request, request_id, *, security_policy):
        captured.update(config=config, evidence=evidence, request=request, policy=security_policy)
        return empty_result(config.corpus_id, request_id, request.mode)

    def supervisor(script, args, **kwargs):
        assert script == service_runtime_binding.WORKER_SCRIPT_PATH
        assert args[0] == "_evidence_search_worker" and len(args) == 3
        assert all("private query" not in arg for arg in args)
        assert kwargs["timeout"] == 2 and kwargs["operation"] == "service evidence search"
        assert kwargs["stdout_target"] is kwargs["stderr_target"]
        payload = json.loads(Path(args[1]).read_bytes())
        assert payload["search_request"] == service_runtime._search_request_payload(
            config_for(tmp_path), _request(), "request-1", security_policy=policy)
        return transport.evidence_worker_main(Path(args[1]), Path(args[2]), execute_fn=execute)

    result = transport.supervised_evidence_search(
        config_for(tmp_path), contracts.EvidenceCorpusConfig("book"), _request(), "request-1",
        temporary_root=tmp_path / "requests", security_policy=policy, runtime_binding=_binding(supervisor))
    assert result == empty_result()
    assert captured["policy"] == policy and captured["request"] == _request()
    assert list((tmp_path / "requests").iterdir()) == []


@pytest.mark.parametrize("mutation", [
    lambda p: p.update(schema_version=True),
    lambda p: p.update(unexpected="private"),
    lambda p: p["search_request"].update(schema_version=True),
    lambda p: p["evidence"].update(corpus_id="other"),
    lambda p: p["evidence"].update(extra="private"),
])
def test_worker_rejects_malformed_envelopes_before_execution(tmp_path, mutation):
    payload = _payload(tmp_path)
    mutation(payload)
    request, result = tmp_path / "request.json", tmp_path / "result.json"
    storage_policy.atomic_write_private_json(request, payload)
    assert transport.evidence_worker_main(
        request, result, execute_fn=lambda *a, **k: pytest.fail("must not execute")) == 1
    assert json.loads(result.read_bytes()) == transport._FAILURE


@pytest.mark.parametrize("error", [ValueError("private path/token"), KeyboardInterrupt("private")])
def test_worker_redacts_all_executor_failures(tmp_path, error):
    request, result = tmp_path / "request.json", tmp_path / "result.json"
    storage_policy.atomic_write_private_json(request, _payload(tmp_path))

    def execute(*args, **kwargs):
        raise error

    assert transport.evidence_worker_main(request, result, execute_fn=execute) == 1
    assert json.loads(result.read_bytes()) == transport._FAILURE
    assert b"private" not in result.read_bytes()


@pytest.mark.parametrize("mutation", [
    lambda p: p["search_request"]["search"].update(query="different private query"),
    lambda p: p["search_request"]["corpus"].update(collection_name="other"),
    lambda p: p["evidence"].update(docling_path="changed-docling.json"),
])
def test_response_binds_full_request_not_only_request_id(tmp_path, mutation):
    def supervisor(script, args, **kwargs):
        request_path = Path(args[1])
        payload = json.loads(request_path.read_bytes())
        mutation(payload)
        storage_policy.atomic_write_private_json(request_path, payload)
        return transport.evidence_worker_main(request_path, Path(args[2]),
                                             execute_fn=lambda *a, **k: empty_result())

    with pytest.raises(service_runtime.ServiceRuntimeError, match="unavailable"):
        transport.supervised_evidence_search(
            config_for(tmp_path), contracts.EvidenceCorpusConfig("book"), _request(), "request-1",
            temporary_root=tmp_path / "requests", runtime_binding=_binding(supervisor))
    assert list((tmp_path / "requests").iterdir()) == []


@pytest.mark.parametrize("exit_code", [124, 1, 2])
def test_supervisor_failure_never_returns_success(tmp_path, exit_code):
    def supervisor(script, args, **kwargs):
        transport.evidence_worker_main(Path(args[1]), Path(args[2]),
                                       execute_fn=lambda *a, **k: empty_result())
        return exit_code

    with pytest.raises(service_runtime.ServiceRuntimeError) as error:
        transport.supervised_evidence_search(
            config_for(tmp_path), contracts.EvidenceCorpusConfig("book"), _request(), "request-1",
            temporary_root=tmp_path / "requests", runtime_binding=_binding(supervisor))
    assert error.value.code == ("deadline_exceeded" if exit_code == 124 else "service_unavailable")
    assert list((tmp_path / "requests").iterdir()) == []


def test_uncertain_process_cleanup_is_fatal(tmp_path):
    binding = service_runtime_binding.default_service_runtime_binding()

    def supervisor(*args, **kwargs):
        raise binding.cleanup_error_type("private failure")

    with pytest.raises(service_runtime.ServiceRuntimeError) as error:
        transport.supervised_evidence_search(
            config_for(tmp_path), contracts.EvidenceCorpusConfig("book"), _request(), "request-1",
            temporary_root=tmp_path / "requests", runtime_binding=replace(binding, supervisor=supervisor))
    assert error.value.fatal


def test_private_size_bounds_and_bad_config_precede_io(tmp_path, monkeypatch):
    monkeypatch.setattr(transport, "MAX_PRIVATE_REQUEST_BYTES", 1)
    with pytest.raises(base.ServiceContractError):
        transport.supervised_evidence_search(
            config_for(tmp_path), contracts.EvidenceCorpusConfig("book"), _request(), "request-1",
            temporary_root=tmp_path / "never-created")
    assert not (tmp_path / "never-created").exists()


def _runtime(root, runner, **kwargs):
    return service_runtime.RagApplicationService(
        {"book": config_for(root)}, working_directory=root, output_root=root / "output",
        evidence_configs={"book": contracts.EvidenceCorpusConfig("book")},
        evidence_runner=runner, **kwargs)


@pytest.mark.parametrize("configs,runner", [({}, lambda: None),
    ({"unknown": contracts.EvidenceCorpusConfig("unknown")}, lambda: None),
    ({"book": contracts.EvidenceCorpusConfig("book")}, None),
    (None, lambda: None), ([], lambda: None)])
def test_runtime_rejects_incomplete_or_outside_allowlist_before_files(tmp_path, configs, runner):
    with pytest.raises(base.ServiceContractError):
        service_runtime.RagApplicationService(
            {"book": config_for(tmp_path)}, working_directory=tmp_path,
            output_root=tmp_path / "never-created", evidence_configs=configs, evidence_runner=runner)
    assert not (tmp_path / "never-created").exists()


def test_runtime_captures_configuration_and_policy_generation(tmp_path):
    captures = []
    config = config_for(tmp_path)
    evidence = contracts.EvidenceCorpusConfig("book")
    registry, evidence_registry = {"book": config}, {"book": evidence}
    policy = release_security.ReleaseSecurityPolicy()

    def runner(*args, **kwargs):
        captures.append((args, kwargs))
        return empty_result()

    runtime = service_runtime.RagApplicationService(
        registry, working_directory=tmp_path, output_root=tmp_path / "output",
        evidence_configs=evidence_registry, evidence_runner=runner, security_policy=policy)
    original_binding = runtime._runtime_binding
    registry.clear()
    evidence_registry.clear()
    runtime.start()
    try:
        assert runtime.search_evidence("book", _request(), "request-1") == empty_result()
        assert captures[0][0] == (config, evidence, _request(), "request-1")
        assert captures[0][1]["security_policy"] is policy
        assert captures[0][1]["runtime_binding"] is original_binding
    finally:
        runtime.close()


def test_evidence_and_v1_share_capacity_and_close_waits(tmp_path):
    entered, release, closing = threading.Event(), threading.Event(), threading.Event()

    def runner(*args, **kwargs):
        entered.set()
        assert release.wait(10)
        return empty_result()

    # This test isolates accounting, not the thread-affine physical path lease.
    binding = replace(service_runtime_binding.default_service_runtime_binding(),
                      instance_lease_factory=lambda path: nullcontext())
    runtime = _runtime(tmp_path, runner, max_concurrent_searches=1, runtime_binding=binding)
    runtime.start()
    with ThreadPoolExecutor(max_workers=2) as pool:
        pending = pool.submit(runtime.search_evidence, "book", _request(), "request-1")
        try:
            assert entered.wait(5)
            for operation in (runtime.search, runtime.search_evidence):
                with pytest.raises(service_runtime.ServiceRuntimeError) as error:
                    operation("book", _request(), "request-2")
                assert error.value.code == "concurrency_limited"

            def close():
                closing.set()
                runtime.close()

            closed = pool.submit(close)
            assert closing.wait(5)
            assert not closed.done()
        finally:
            release.set()
            runtime.close()
        assert pending.result(timeout=5) == empty_result()
        closed.result(timeout=5)
    assert runtime._active_searches == 0 and not runtime.started


@pytest.mark.parametrize("fatal", [False, True])
def test_runtime_error_health_policy(tmp_path, fatal):
    def runner(*args, **kwargs):
        raise service_runtime.ServiceRuntimeError("service_unavailable", fatal=fatal)

    runtime = _runtime(tmp_path, runner)
    runtime.start()
    try:
        with pytest.raises(service_runtime.ServiceRuntimeError):
            runtime.search_evidence("book", _request(), "request-1")
        assert runtime.healthy is (not fatal)
        assert runtime._active_searches == 0
    finally:
        runtime.close()


def test_runtime_invalid_output_marks_unhealthy(tmp_path):
    runtime = _runtime(tmp_path, lambda *a, **k: {"secret": "private"})
    runtime.start()
    try:
        with pytest.raises(service_runtime.ServiceRuntimeError) as error:
            runtime.search_evidence("book", _request(), "request-1")
        assert error.value.fatal and not runtime.healthy
    finally:
        runtime.close()


def test_hidden_evidence_worker_actual_subprocess_is_redacted(tmp_path):
    request, result = tmp_path / "request.json", tmp_path / "result.json"
    storage_policy.atomic_write_private_json(request, {"private": "secret"})
    completed = subprocess.run(
        [sys.executable, str(service_runtime_binding.WORKER_SCRIPT_PATH),
         transport.EVIDENCE_WORKER_ACTION, str(request), str(result)],
        capture_output=True, timeout=30, check=False)
    assert completed.returncode == 1 and completed.stdout == completed.stderr == b""
    assert json.loads(result.read_bytes()) == transport._FAILURE


def test_private_evidence_registry_is_separate_and_never_reads_source_paths(tmp_path):
    path = tmp_path / "evidence.json"
    storage_policy.atomic_write_private_json(path, {
        "schema_version": 1, "kind": "service_evidence_config",
        "corpora": [{"corpus_id": "book", "docling_path": "not-present.json", "recovery_path": None}],
    })
    registry = service_runtime.load_evidence_registry(path, {"book": config_for(tmp_path)})
    assert registry == {"book": contracts.EvidenceCorpusConfig("book", tmp_path / "not-present.json")}
    assert not (tmp_path / "not-present.json").exists()
    with pytest.raises(base.ServiceContractError):
        service_runtime.load_evidence_registry(path, {"other": replace(config_for(tmp_path), corpus_id="other")})


def test_worker_rejects_oversized_private_input_without_running(tmp_path):
    request, result = tmp_path / "request.json", tmp_path / "result.json"
    storage_policy.atomic_write_private_json(request, {"oversized": "x" * transport.MAX_PRIVATE_REQUEST_BYTES})
    assert transport.evidence_worker_main(
        request, result, execute_fn=lambda *a, **k: pytest.fail("must not execute")) == 1
    assert json.loads(result.read_bytes()) == transport._FAILURE


def test_worker_output_budget_failure_is_failure_exit_not_success(tmp_path, monkeypatch):
    request, result = tmp_path / "request.json", tmp_path / "result.json"
    storage_policy.atomic_write_private_json(request, _payload(tmp_path))
    monkeypatch.setattr(service_runtime, "MAX_SEARCH_RESULT_BYTES", 1)
    assert transport.evidence_worker_main(
        request, result, execute_fn=lambda *a, **k: empty_result()) == 1
    assert json.loads(result.read_bytes()) == transport._FAILURE


def test_temporary_cleanup_failure_overrides_success(tmp_path, monkeypatch):
    def supervisor(script, args, **kwargs):
        return transport.evidence_worker_main(Path(args[1]), Path(args[2]),
                                             execute_fn=lambda *a, **k: empty_result())

    original = service_runtime._remove_search_directory

    def cleanup(path, identity):
        original(path, identity)
        raise OSError("synthetic cleanup verification uncertainty")

    monkeypatch.setattr(service_runtime, "_remove_search_directory", cleanup)
    with pytest.raises(service_runtime.ServiceRuntimeError) as error:
        transport.supervised_evidence_search(
            config_for(tmp_path), contracts.EvidenceCorpusConfig("book"), _request(), "request-1",
            temporary_root=tmp_path / "requests", runtime_binding=_binding(supervisor))
    assert error.value.fatal and "uncertainty" not in str(error.value)


@pytest.mark.parametrize("mutation", [
    lambda p: p.update(schema_version=True), lambda p: p.update(ok=1),
    lambda p: p.update(private="secret"), lambda p: p["result"]["search"].update(request_id="other"),
    lambda p: p["result"]["search"].update(corpus_id="other"),
    lambda p: p.update(request_sha256="0" * 64),
])
def test_parent_rejects_malformed_or_wrong_generation_worker_success(tmp_path, mutation):
    def supervisor(script, args, **kwargs):
        result_path = Path(args[2])
        assert transport.evidence_worker_main(Path(args[1]), result_path,
                                              execute_fn=lambda *a, **k: empty_result()) == 0
        payload = json.loads(result_path.read_bytes())
        mutation(payload)
        storage_policy.atomic_write_private_json(result_path, payload)
        return 0

    with pytest.raises(service_runtime.ServiceRuntimeError):
        transport.supervised_evidence_search(
            config_for(tmp_path), contracts.EvidenceCorpusConfig("book"), _request(), "request-1",
            temporary_root=tmp_path / "requests", runtime_binding=_binding(supervisor))


def test_worker_composes_exact_frozen_physical_callbacks(monkeypatch, tmp_path):
    import service_evidence_search
    import service_search_worker

    captured = {}

    def execute(*args, **kwargs):
        captured.update(args=args, kwargs=kwargs)
        return empty_result()

    monkeypatch.setattr(service_evidence_search, "execute_evidence_search", execute)
    policy = release_security.ReleaseSecurityPolicy()
    assert service_search_worker._execute_evidence(
        config_for(tmp_path), contracts.EvidenceCorpusConfig("book"), _request(), "request-1",
        security_policy=policy) == empty_result()
    binding = captured["kwargs"]["binding"]
    assert isinstance(binding, service_evidence_search.EvidenceSearchBinding)
    facade = service_search_worker.rag
    for name, physical in {"search_index": "search_index", "vector_store_lock": "_vector_store_lock",
                           "chunk_output_lease": "_chunk_output_lease", "chunk_id": "_chunk_id",
                           "chunk_hash": "_chunk_hash", "validated_quality_report_binding": "_validated_quality_report_binding",
                           "load_conversion_source_binding": "_load_conversion_source_binding",
                           "identify_book_sections": "_identify_book_sections",
                           "book_structural_ranges": "_book_structural_ranges"}.items():
        assert getattr(binding, name) is getattr(facade, physical)
    assert captured["kwargs"]["security_policy"] is policy


def test_worker_and_runtime_refuse_valid_envelope_with_wrong_requested_mode(tmp_path):
    request, result = tmp_path / "request.json", tmp_path / "result.json"
    storage_policy.atomic_write_private_json(request, _payload(tmp_path))
    def runner(*args, **kwargs):
        return empty_result(mode="hybrid")

    assert transport.evidence_worker_main(request, result, execute_fn=runner) == 1
    assert json.loads(result.read_bytes()) == transport._FAILURE
    runtime = _runtime(tmp_path, runner)
    runtime.start()
    try:
        with pytest.raises(service_runtime.ServiceRuntimeError) as error:
            runtime.search_evidence("book", _request(), "request-1")
        assert error.value.fatal
    finally:
        runtime.close()


@pytest.mark.parametrize("search_request", [base.SearchRequest(""), base.SearchRequest("query", limit=True),
                                     base.SearchRequest("query", filters=None)])
def test_invalid_embedded_request_is_rejected_before_runner(tmp_path, search_request):
    runtime = _runtime(tmp_path, lambda *a, **k: pytest.fail("must not execute"))
    runtime.start()
    try:
        with pytest.raises(base.ServiceContractError):
            runtime.search_evidence("book", search_request, "request-1")
        assert runtime.healthy and runtime._active_searches == 0
    finally:
        runtime.close()
