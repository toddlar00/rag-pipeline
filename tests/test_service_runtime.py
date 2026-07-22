import json
import os
import stat
import threading
from concurrent.futures import ThreadPoolExecutor
from pathlib import Path

import pytest

import job_manager
import job_runtime
import retention
import retrieval_core
import service_contracts
import service_runtime
import storage_policy


def _config(tmp_path: Path) -> service_contracts.CorpusConfig:
    db_path = tmp_path / "qdrant"
    db_path.mkdir(exist_ok=True)
    chunks_path = tmp_path / "chunks.jsonl"
    chunks_path.write_text(
        '{"text":"holding","metadata":{"content_type":"case_opinion"}}\n',
        encoding="utf-8")
    return service_contracts.CorpusConfig(
        corpus_id="property",
        db_path=db_path.resolve(),
        chunks_path=chunks_path.resolve(),
        collection_name="property_collection",
        embedding_model="test-model",
        search_timeout_seconds=2,
        db_lock_timeout_seconds=1,
        reindex_timeout_seconds=30,
    )


def _empty_search_response(config, request, request_id):
    return {
        "schema_version": 1,
        "request_id": request_id,
        "corpus_id": config.corpus_id,
        "backend": "qdrant",
        "requested_mode": request.mode,
        "effective_mode": "vector",
        "reranker_applied": False,
        "warnings": [],
        "hits": [],
    }


def _launch_starting(store, job_id, *, ready_timeout):
    del ready_timeout
    execution = store.load_execution(job_id)
    if execution.status == "queued":
        summary = store.transition_job(
            job_id, "starting",
            attempt_token=execution.attempt_token,
            expected_revision=execution.revision)
    else:
        summary = store.get_job(job_id)
    return job_manager.LaunchResult(
        job_id=job_id,
        status=summary.status,
        attempt_number=summary.attempt_number,
        ready=True,
    )


def _service(tmp_path, *, search_runner=None, launcher=_launch_starting,
             state_name="service-state", max_searches=2):
    config = _config(tmp_path)
    options = {}
    if search_runner is not None:
        options["search_runner"] = search_runner
    service = service_runtime.RagApplicationService(
        {config.corpus_id: config},
        job_root=tmp_path / "jobs",
        working_directory=tmp_path,
        output_root=tmp_path / "output",
        service_state_root=tmp_path / state_name,
        max_concurrent_searches=max_searches,
        launcher=launcher,
        **options,
    )
    service.start()
    return service


def _advance_failed(store, job_id):
    execution = store.load_execution(job_id)
    starting = store.transition_job(
        job_id, "starting", attempt_token=execution.attempt_token,
        expected_revision=execution.revision)
    running = store.transition_job(
        job_id, "running", attempt_token=execution.attempt_token,
        expected_revision=starting.revision)
    return store.transition_job(
        job_id, "failed", attempt_token=execution.attempt_token,
        expected_revision=running.revision)


def _submit_service_job(service, *, full_reindex=False, job_id=None):
    config = service.corpora["property"]
    request = service_contracts.ReindexRequest(full_reindex)
    return service.store.submit_job(
        "index", service._reindex_argv(config, request),
        timeout_seconds=config.reindex_timeout_seconds,
        job_id=job_id,
        working_directory=service.working_directory,
        output_root=service.output_root,
    )


def test_registry_loader_is_strict_private_and_resolves_relative_paths(
        tmp_path):
    config_path = tmp_path / "service.json"
    config_path.write_text(json.dumps({
        "schema_version": 1,
        "corpora": [{
            "corpus_id": "property",
            "backend": "qdrant",
            "db_path": "db",
            "chunks_path": "chunks.jsonl",
            "collection_name": "property",
            "embedding_model": "test-model",
        }],
    }), encoding="utf-8")

    registry = service_runtime.load_corpus_registry(config_path)

    assert registry["property"].db_path == (tmp_path / "db").resolve()
    assert registry["property"].chunks_path == (
        tmp_path / "chunks.jsonl").resolve()
    if os.name != "nt":
        assert stat.S_IMODE(config_path.stat().st_mode) == 0o600
    assert str(tmp_path) not in json.dumps(
        registry["property"].public_dict())


def test_committed_service_config_example_matches_registry_contract(tmp_path):
    source = Path(__file__).resolve().parents[1] / "service-config.example.json"
    config_path = tmp_path / "service.json"
    config_path.write_bytes(source.read_bytes())

    registry = service_runtime.load_corpus_registry(config_path)

    assert tuple(registry) == ("civil_procedure",)
    assert registry["civil_procedure"].collection_name == "civil_procedure"
    assert registry["civil_procedure"].embedding_model == "embo-01"


@pytest.mark.parametrize("mutator", [
    lambda payload: payload.update({"extra": True}),
    lambda payload: payload.update({"schema_version": 2}),
    lambda payload: payload.update({"corpora": []}),
    lambda payload: payload["corpora"].append(dict(payload["corpora"][0])),
    lambda payload: payload["corpora"][0].update({"backend": "chroma"}),
])
def test_registry_loader_rejects_unknown_versions_duplicates_and_chroma(
        tmp_path, mutator):
    payload = {
        "schema_version": 1,
        "corpora": [{
            "corpus_id": "property",
            "backend": "qdrant",
            "db_path": "db",
            "chunks_path": "chunks.jsonl",
            "collection_name": "property",
            "embedding_model": "test-model",
        }],
    }
    mutator(payload)
    path = tmp_path / "bad.json"
    path.write_text(json.dumps(payload), encoding="utf-8")
    with pytest.raises(service_contracts.ServiceContractError):
        service_runtime.load_corpus_registry(path)


def test_registry_loader_rejects_lexical_symlink_before_resolution(tmp_path):
    target = tmp_path / "target.json"
    storage_policy.atomic_write_private_json(target, {
        "schema_version": 1,
        "corpora": [{
            "corpus_id": "property",
            "backend": "qdrant",
            "db_path": "db",
            "chunks_path": "chunks.jsonl",
            "collection_name": "property",
            "embedding_model": "test-model",
        }],
    })
    link = tmp_path / "service.json"
    try:
        link.symlink_to(target)
    except (OSError, NotImplementedError) as exc:
        pytest.skip(f"symlink creation unavailable: {exc}")

    with pytest.raises(storage_policy.StoragePolicyError):
        service_runtime.load_corpus_registry(link)


def test_service_start_reconciles_once_and_holds_single_instance(
        monkeypatch, tmp_path):
    calls = []
    real_reconcile = service_runtime.RagApplicationService._reconcile_service_jobs

    def track_reconcile(self, **options):
        calls.append(self.store.root)
        return real_reconcile(self, **options)

    monkeypatch.setattr(
        service_runtime.RagApplicationService, "_reconcile_service_jobs",
        track_reconcile)
    first = _service(tmp_path)
    second = service_runtime.RagApplicationService(
        first.corpora,
        job_root=first.store.root,
        working_directory=tmp_path,
        output_root=first.output_root,
        service_state_root=first.service_state_root,
        launcher=_launch_starting,
    )
    try:
        assert calls == [first.store.root]
        assert first.healthy
        assert first.readiness()
        with pytest.raises(service_runtime.ServiceRuntimeError) as raised:
            second.start()
        assert raised.value.code == "service_unavailable"
    finally:
        first.close()

    second.start()
    second.close()


def test_default_service_job_root_is_dedicated_and_ready_timeout_is_bounded(
        tmp_path):
    config = _config(tmp_path)
    service = service_runtime.RagApplicationService(
        {config.corpus_id: config},
        working_directory=tmp_path,
        output_root=tmp_path / "output",
        service_state_root=tmp_path / "service-state",
        launcher=_launch_starting,
    )
    assert service.store.root.name == ".rag-service-jobs"

    with pytest.raises(service_contracts.ServiceContractError):
        service_runtime.RagApplicationService(
            {config.corpus_id: config},
            working_directory=tmp_path,
            output_root=tmp_path / "other-output",
            service_state_root=tmp_path / "other-state",
            ready_timeout_seconds=(
                service_contracts.MAX_READY_TIMEOUT_SECONDS + 1),
            launcher=_launch_starting,
        )


def test_unowned_nonempty_job_root_and_mismatched_service_state_fail_closed(
        tmp_path):
    config = _config(tmp_path)
    unowned = service_runtime.RagApplicationService(
        {config.corpus_id: config},
        job_root=tmp_path / "unowned-jobs",
        working_directory=tmp_path,
        output_root=tmp_path / "output",
        service_state_root=tmp_path / "unowned-state",
        launcher=_launch_starting,
    )
    unowned.store.submit_job(
        "export", ["--chunks", "foreign.jsonl"],
        working_directory=tmp_path, output_root=unowned.output_root)
    with pytest.raises(service_runtime.ServiceRuntimeError) as raised:
        unowned.start()
    assert raised.value.fatal is True

    first = _service(tmp_path, state_name="owned-state")
    first.close()
    mismatched = service_runtime.RagApplicationService(
        first.corpora,
        job_root=first.store.root,
        working_directory=tmp_path,
        output_root=first.output_root,
        service_state_root=tmp_path / "different-state",
        launcher=_launch_starting,
    )
    with pytest.raises(service_runtime.ServiceRuntimeError) as raised:
        mismatched.start()
    assert raised.value.fatal is True

    restarted = _service(tmp_path, state_name="owned-state")
    restarted.close()


def test_foreign_jobs_are_hidden_unmodified_and_skipped_across_restart(
        tmp_path):
    service = _service(tmp_path)
    foreign = service.store.submit_job(
        "export", ["--chunks", "C:/private/foreign.jsonl"],
        working_directory=tmp_path, output_root=service.output_root)
    foreign_lock = service.store.root / foreign.job_id / ".job.lock"
    try:
        assert service.list_jobs()["items"] == []
        assert service.reconcile_jobs() == []
        assert service.store.get_job(foreign.job_id).status == "queued"
        assert not foreign_lock.exists()
        for operation in (
                lambda: service.get_job(foreign.job_id),
                lambda: service.cancel_job(
                    foreign.job_id,
                    attempt_number=foreign.attempt_number,
                    revision=foreign.revision),
                lambda: service.resume_job(
                    foreign.job_id,
                    attempt_number=foreign.attempt_number,
                    revision=foreign.revision),
                lambda: service.deletion_plan(foreign.job_id),
                lambda: service.delete_job(
                    foreign.job_id,
                    attempt_number=foreign.attempt_number,
                    revision=foreign.revision),
        ):
            with pytest.raises(service_runtime.ServiceRuntimeError) as raised:
                operation()
            assert raised.value.code == "not_found"
        assert service.store.get_job(foreign.job_id).status == "queued"
        assert not foreign_lock.exists()
        assert service.healthy
    finally:
        service.close()

    restarted = _service(tmp_path)
    try:
        assert restarted.store.get_job(foreign.job_id).status == "queued"
        assert restarted.list_jobs()["items"] == []
        assert not foreign_lock.exists()
    finally:
        restarted.close()


def test_owner_marker_tamper_fails_fatal_and_marks_running_service_unhealthy(
        tmp_path):
    service = _service(tmp_path)
    storage_policy.atomic_write_private_json(
        service._job_root_owner_path,
        {"schema_version": 1, "kind": "tampered"},
    )
    try:
        with pytest.raises(service_runtime.ServiceRuntimeError) as raised:
            service.list_jobs()
        assert raised.value.code == "service_unavailable"
        assert raised.value.fatal is True
        assert not service.healthy
    finally:
        service.close()


def test_public_corpora_and_readiness_never_expose_paths(tmp_path):
    service = _service(tmp_path)
    try:
        rendered = json.dumps(service.public_corpora())
        assert "property" in rendered
        assert str(tmp_path) not in rendered
        service.corpora["property"].chunks_path.unlink()
        assert service.readiness() is False
    finally:
        service.close()


def test_retryable_reconcile_contention_does_not_poison_health(
        monkeypatch, tmp_path):
    service = _service(tmp_path)
    monkeypatch.setattr(
        service.store, "list_jobs",
        lambda: (_ for _ in ()).throw(
            job_runtime.JobBusyError("busy")))
    try:
        with pytest.raises(service_runtime.ServiceRuntimeError) as raised:
            service.reconcile_jobs()
        assert raised.value.fatal is False
        assert service.healthy
    finally:
        service.close()


def test_retryable_job_listing_contention_does_not_poison_health(
        monkeypatch, tmp_path):
    service = _service(tmp_path)
    monkeypatch.setattr(
        service.store, "list_jobs",
        lambda: (_ for _ in ()).throw(job_runtime.JobBusyError("busy")))
    try:
        with pytest.raises(service_runtime.ServiceRuntimeError) as raised:
            service.list_jobs()
        assert raised.value.code == "service_unavailable"
        assert raised.value.fatal is False
        assert service.healthy
    finally:
        service.close()


def test_expected_job_contention_does_not_poison_health(
        monkeypatch, tmp_path):
    service = _service(tmp_path)
    submitted = _submit_service_job(service)
    monkeypatch.setattr(
        service_runtime.job_manager, "reconcile_job",
        lambda *_args, **_kwargs: (_ for _ in ()).throw(
            job_runtime.JobBusyError("busy")))
    try:
        with pytest.raises(service_runtime.ServiceRuntimeError) as raised:
            service.cancel_job(
                submitted.job_id,
                attempt_number=submitted.attempt_number,
                revision=submitted.revision)
        assert raised.value.code == "service_unavailable"
        assert raised.value.fatal is False
        assert service.healthy
    finally:
        service.close()


def test_search_is_bounded_by_nonblocking_concurrency(tmp_path):
    entered = threading.Event()
    release = threading.Event()

    def runner(config, request, request_id):
        assert config.corpus_id == "property"
        assert request.query == "minimum contacts"
        entered.set()
        assert release.wait(timeout=5)
        return _empty_search_response(config, request, request_id)

    service = _service(
        tmp_path, search_runner=runner, max_searches=1)
    request = service_contracts.SearchRequest("minimum contacts")
    try:
        with ThreadPoolExecutor(max_workers=1) as pool:
            first = pool.submit(service.search, "property", request, "req-1")
            assert entered.wait(timeout=2)
            with pytest.raises(service_runtime.ServiceRuntimeError) as raised:
                service.search("property", request, "req-2")
            assert raised.value.code == "concurrency_limited"
            release.set()
            assert first.result(timeout=2)["request_id"] == "req-1"
    finally:
        release.set()
        service.close()


def test_supervised_search_keeps_query_out_of_arguments_and_cleans_temp(
        monkeypatch, tmp_path):
    config = _config(tmp_path)
    request = service_contracts.SearchRequest(
        "minimum contacts secret query", mode="hybrid")
    observed = {}
    response = retrieval_core.SearchResponse(
        hits=[retrieval_core.SearchHit(
            text="holding", metadata={
                "content_type": "case_opinion",
                "source_file": "C:/private/book.pdf",
            }, score=0.9, source_id="chunk_abc")],
        backend="qdrant",
        requested_mode="hybrid",
        effective_mode="vector",
        reranker_applied=False,
        warnings=["Hybrid search failed at C:/private; used vector search."],
    )
    monkeypatch.setattr(service_runtime.rag, "search_index", lambda *a, **k: response)

    def run(script_path, argv, *, operation, timeout, **kwargs):
        observed.update({
            "script": script_path,
            "argv": list(argv),
            "operation": operation,
            "timeout": timeout,
            "temporary": Path(argv[1]).parent,
            "stdout_target": kwargs["stdout_target"],
            "stderr_target": kwargs["stderr_target"],
        })
        request_path = Path(argv[1])
        if os.name != "nt":
            assert stat.S_IMODE(request_path.stat().st_mode) == 0o600
            assert stat.S_IMODE(request_path.parent.stat().st_mode) == 0o700
        return service_runtime.search_worker_main(
            request_path, Path(argv[2]))

    monkeypatch.setattr(service_runtime.rag, "_run_cli_with_deadline", run)

    result = service_runtime.supervised_search(config, request, "req-1")

    assert result["hits"][0]["source_id"] == "chunk_abc"
    assert result["warnings"] == ["hybrid_failed"]
    assert "source_file" not in json.dumps(result)
    assert request.query not in " ".join(map(str, observed["argv"]))
    assert observed["argv"][0] == service_runtime._SEARCH_WORKER_ACTION
    assert observed["operation"] == "service search"
    assert observed["timeout"] == config.search_timeout_seconds
    assert observed["stdout_target"] is observed["stderr_target"]
    assert observed["stdout_target"].name == os.devnull
    assert not observed["temporary"].exists()


def test_search_worker_returns_only_stable_error_without_exception_text(
        monkeypatch, tmp_path):
    config = _config(tmp_path)
    request_path = tmp_path / "request.json"
    result_path = tmp_path / "result.json"
    storage_policy.atomic_write_private_json(
        request_path,
        service_runtime._search_request_payload(
            config, service_contracts.SearchRequest("secret query"), "req-1"))
    monkeypatch.setattr(
        service_runtime.rag, "search_index",
        lambda *a, **k: (_ for _ in ()).throw(
            RuntimeError("C:/private/book.pdf API_KEY=secret")))

    assert service_runtime.search_worker_main(request_path, result_path) == 1
    payload = json.loads(result_path.read_text(encoding="utf-8"))
    encoded = json.dumps(payload)
    assert payload["error_code"] == "service_unavailable"
    assert "private" not in encoded
    assert "secret" not in encoded
    assert "RuntimeError" not in encoded


def test_supervised_search_maps_deadline_and_malformed_envelopes(
        monkeypatch, tmp_path):
    config = _config(tmp_path)
    request = service_contracts.SearchRequest("query")
    monkeypatch.setattr(
        service_runtime.rag, "_run_cli_with_deadline",
        lambda *a, **k: 124)
    with pytest.raises(service_runtime.ServiceRuntimeError) as raised:
        service_runtime.supervised_search(config, request, "req-1")
    assert raised.value.code == "deadline_exceeded"

    def malformed(_script, argv, **_kwargs):
        storage_policy.atomic_write_private_json(
            Path(argv[2]), {"private": "C:/secret"})
        return 0

    monkeypatch.setattr(
        service_runtime.rag, "_run_cli_with_deadline", malformed)
    with pytest.raises((service_runtime.ServiceRuntimeError,
                        service_contracts.ServiceContractError)):
        service_runtime.supervised_search(config, request, "req-1")


def test_supervised_search_surfaces_and_marks_cleanup_failure(
        monkeypatch, tmp_path):
    config = _config(tmp_path)
    request = service_contracts.SearchRequest("private query")
    observed = {}

    def run(_script, argv, **_kwargs):
        result = _empty_search_response(config, request, "req-1")
        storage_policy.atomic_write_private_json(
            Path(argv[2]), {
                "schema_version": 1,
                "kind": "service_search_result",
                "ok": True,
                "result": result,
            })
        return 0

    real_remove = service_runtime._remove_search_directory

    def fail_remove(path, identity):
        observed.update(path=path, identity=identity)
        raise OSError("injected cleanup failure")

    monkeypatch.setattr(service_runtime.rag, "_run_cli_with_deadline", run)
    monkeypatch.setattr(
        service_runtime, "_remove_search_directory", fail_remove)

    with pytest.raises(service_runtime.ServiceRuntimeError) as raised:
        service_runtime.supervised_search(
            config, request, "req-1",
            temporary_root=tmp_path / "search-tmp")

    assert raised.value.code == "service_unavailable"
    assert raised.value.fatal is True
    assert (observed["path"] / "request.json").is_file()
    real_remove(observed["path"], observed["identity"])


def test_start_removes_crash_left_private_search_directory(tmp_path):
    config = _config(tmp_path)
    service = service_runtime.RagApplicationService(
        {config.corpus_id: config},
        job_root=tmp_path / "jobs",
        working_directory=tmp_path,
        output_root=tmp_path / "output",
        service_state_root=tmp_path / "service-state",
        launcher=_launch_starting,
    )
    stale = storage_policy.ensure_private_directory(
        service.search_temporary_root / "request-crashed")
    storage_policy.atomic_write_private_text(
        stale / "request.json", "private query")

    service.start()
    try:
        assert not stale.exists()
        assert list(service.search_temporary_root.iterdir()) == []
    finally:
        service.close()


def test_reindex_is_structured_idempotent_and_detects_key_reuse(tmp_path):
    launches = []

    def launch(store, job_id, *, ready_timeout):
        launches.append((job_id, ready_timeout))
        return _launch_starting(store, job_id, ready_timeout=ready_timeout)

    service = _service(tmp_path, launcher=launch)
    try:
        first = service.reindex(
            "property", service_contracts.ReindexRequest(False),
            idempotency_key="request-1234")
        replay = service.reindex(
            "property", service_contracts.ReindexRequest(False),
            idempotency_key="request-1234")

        assert first.idempotent_replay is False
        assert replay.idempotent_replay is True
        assert replay.job["job_id"] == first.job["job_id"]
        assert len(launches) == 1
        execution = service.store.load_execution(first.job["job_id"])
        assert execution.command == "index"
        assert "--full-reindex" not in execution.argv
        assert execution.working_directory == tmp_path.resolve()
        assert execution.output_root == (tmp_path / "output").resolve()

        with pytest.raises(service_runtime.ServiceRuntimeError) as raised:
            service.reindex(
                "property", service_contracts.ReindexRequest(True),
                idempotency_key="request-1234")
        assert raised.value.code == "conflict"
        assert len(launches) == 1
    finally:
        service.close()


def test_reindex_replay_maps_manager_corruption_to_fatal_unavailability(
        monkeypatch, tmp_path):
    service = _service(tmp_path)
    config = service.corpora["property"]
    request = service_contracts.ReindexRequest()
    job_id = service_contracts.job_id_for_idempotency(
        "property", "corrupt-replay-1234")
    service.store.submit_job(
        "index", service._reindex_argv(config, request),
        timeout_seconds=config.reindex_timeout_seconds,
        job_id=job_id,
        working_directory=service.working_directory,
        output_root=service.output_root,
    )
    monkeypatch.setattr(
        service_runtime.job_manager, "reconcile_job",
        lambda *_args, **_kwargs: (_ for _ in ()).throw(
            service_runtime.job_manager.JobManagerCorruptError("corrupt")))
    try:
        with pytest.raises(service_runtime.ServiceRuntimeError) as raised:
            service.reindex(
                "property", request,
                idempotency_key="corrupt-replay-1234")
        assert raised.value.code == "service_unavailable"
        assert raised.value.fatal is True
        assert not service.healthy
    finally:
        service.close()


def test_reindex_replay_contention_is_retryable_without_poisoning_health(
        monkeypatch, tmp_path):
    service = _service(tmp_path)
    config = service.corpora["property"]
    request = service_contracts.ReindexRequest()
    job_id = service_contracts.job_id_for_idempotency(
        "property", "busy-replay-1234")
    service.store.submit_job(
        "index", service._reindex_argv(config, request),
        timeout_seconds=config.reindex_timeout_seconds,
        job_id=job_id,
        working_directory=service.working_directory,
        output_root=service.output_root,
    )
    monkeypatch.setattr(
        service_runtime.job_manager, "reconcile_job",
        lambda *_args, **_kwargs: (_ for _ in ()).throw(
            job_runtime.JobBusyError("busy")))
    try:
        with pytest.raises(service_runtime.ServiceRuntimeError) as raised:
            service.reindex(
                "property", request,
                idempotency_key="busy-replay-1234")
        assert raised.value.code == "service_unavailable"
        assert raised.value.fatal is False
        assert service.healthy
    finally:
        service.close()


def test_restart_terminalizes_reindex_crash_gap_for_explicit_resume(tmp_path):
    first = _service(tmp_path)
    config = first.corpora["property"]
    job_id = service_contracts.job_id_for_idempotency(
        "property", "restart-gap-1234")
    submitted = first.store.submit_job(
        "index",
        first._reindex_argv(config, service_contracts.ReindexRequest()),
        timeout_seconds=config.reindex_timeout_seconds,
        job_id=job_id,
        working_directory=first.working_directory,
        output_root=first.output_root,
    )
    assert submitted.status == "queued"
    first.close()

    second = _service(tmp_path)
    try:
        recovered = second.store.get_job(job_id)
        assert recovered.status == "failed"
        assert recovered.attempt_number == 1
        assert recovered.status in job_runtime.RESUMABLE_JOB_STATUSES
    finally:
        second.close()


def test_restart_terminalizes_resume_crash_gap_without_auto_launch(tmp_path):
    first = _service(tmp_path)
    submitted = _submit_service_job(first)
    failed = _advance_failed(first.store, submitted.job_id)
    resumed = first.store.prepare_resume(
        submitted.job_id, expected_revision=failed.revision)
    assert resumed.status == "queued"
    assert resumed.attempt_number == 2
    first.close()

    second = _service(tmp_path)
    try:
        recovered = second.store.get_job(submitted.job_id)
        assert recovered.status == "failed"
        assert recovered.attempt_number == 2
        assert recovered.status in job_runtime.RESUMABLE_JOB_STATUSES
    finally:
        second.close()


def test_concurrent_idempotent_reindex_launches_exactly_once(tmp_path):
    launch_entered = threading.Event()
    release_launch = threading.Event()
    launches = []

    def launch(store, job_id, *, ready_timeout):
        launches.append(job_id)
        launch_entered.set()
        assert release_launch.wait(timeout=5)
        return _launch_starting(store, job_id, ready_timeout=ready_timeout)

    service = _service(tmp_path, launcher=launch)
    request = service_contracts.ReindexRequest()
    try:
        with ThreadPoolExecutor(max_workers=2) as pool:
            first = pool.submit(
                service.reindex, "property", request,
                idempotency_key="concurrent-1234")
            assert launch_entered.wait(timeout=2)
            second = pool.submit(
                service.reindex, "property", request,
                idempotency_key="concurrent-1234")
            replay = second.result(timeout=2)
            release_launch.set()
            created = first.result(timeout=2)
        assert created.idempotent_replay is False
        assert replay.idempotent_replay is True
        assert created.job["job_id"] == replay.job["job_id"]
        assert len(launches) == 1
    finally:
        release_launch.set()
        service.close()


def test_job_listing_is_redacted_cursor_paginated_and_read_only(
        monkeypatch, tmp_path):
    service = _service(tmp_path)
    try:
        ids = []
        for _index in range(3):
            summary = _submit_service_job(service)
            ids.append(summary.job_id)
        monkeypatch.setattr(
            service_runtime.job_manager, "reconcile_job",
            lambda *_args, **_kwargs: pytest.fail(
                "GET listing must not reconcile"))

        first = service.list_jobs(limit=2)
        assert len(first["items"]) == 2
        assert first["next_cursor"]
        cursor = service_contracts.decode_job_cursor(first["next_cursor"])
        second = service.list_jobs(limit=2, cursor=cursor)
        assert len(second["items"]) == 1
        rendered = json.dumps([first, second])
        assert "C:/private" not in rendered
        assert "argv" not in rendered
        assert {item["job_id"] for item in first["items"] + second["items"]} == set(ids)
    finally:
        service.close()


def test_stale_cancel_cannot_target_resumed_attempt(tmp_path):
    service = _service(tmp_path)
    try:
        submitted = _submit_service_job(service)
        first_failed = _advance_failed(service.store, submitted.job_id)
        resumed = service.store.prepare_resume(
            submitted.job_id, expected_revision=first_failed.revision)

        with pytest.raises(service_runtime.ServiceRuntimeError) as raised:
            service.cancel_job(
                submitted.job_id,
                attempt_number=first_failed.attempt_number,
                revision=first_failed.revision)
        assert raised.value.code == "precondition_failed"
        second = service.store.load_execution(submitted.job_id)
        assert second.attempt_number == resumed.attempt_number
        assert not service.store.is_cancel_requested(
            submitted.job_id, second.attempt_token)

        accepted = service.cancel_job(
            submitted.job_id,
            attempt_number=resumed.attempt_number,
            revision=resumed.revision)
        assert accepted.job["attempt_number"] == resumed.attempt_number
        assert service.store.is_cancel_requested(
            submitted.job_id, second.attempt_token)
    finally:
        service.close()


def test_resume_has_one_exact_winner_and_launches_new_attempt(tmp_path):
    launches = []

    def launch(store, job_id, *, ready_timeout):
        launches.append(job_id)
        return _launch_starting(store, job_id, ready_timeout=ready_timeout)

    service = _service(tmp_path, launcher=launch)
    try:
        submitted = _submit_service_job(service)
        failed = _advance_failed(service.store, submitted.job_id)
        resumed = service.resume_job(
            submitted.job_id,
            attempt_number=failed.attempt_number,
            revision=failed.revision)
        assert resumed.job["attempt_number"] == 2
        assert resumed.job["status"] == "starting"
        assert launches == [submitted.job_id]

        with pytest.raises(service_runtime.ServiceRuntimeError) as raised:
            service.resume_job(
                submitted.job_id,
                attempt_number=failed.attempt_number,
                revision=failed.revision)
        assert raised.value.code == "precondition_failed"
        assert launches == [submitted.job_id]
    finally:
        service.close()


def test_deletion_plan_is_redacted_and_delete_requires_exact_etag(tmp_path):
    service = _service(tmp_path)
    try:
        submitted = _submit_service_job(service)
        failed = _advance_failed(service.store, submitted.job_id)
        plan = service.deletion_plan(submitted.job_id)
        rendered = json.dumps(plan)
        assert plan["apply_required"] is True
        assert plan["candidate_count"] == 1
        assert "private" not in rendered
        assert str(tmp_path) not in rendered
        assert "fingerprint" not in rendered

        with pytest.raises(service_runtime.ServiceRuntimeError) as raised:
            service.delete_job(
                submitted.job_id,
                attempt_number=failed.attempt_number,
                revision=failed.revision + 1)
        assert raised.value.code == "precondition_failed"
        assert service.store.get_job(submitted.job_id).status == "failed"

        outcome = service.delete_job(
            submitted.job_id,
            attempt_number=failed.attempt_number,
            revision=failed.revision)
        assert outcome["deleted"] is True
        assert outcome["job_id"] == submitted.job_id
        with pytest.raises(job_runtime.JobNotFoundError):
            service.store.get_job(submitted.job_id)
    finally:
        service.close()


def test_concurrent_duplicate_delete_has_one_winner_and_stays_healthy(
        tmp_path):
    service = _service(tmp_path)
    submitted = _submit_service_job(service)
    failed = _advance_failed(service.store, submitted.job_id)
    barrier = threading.Barrier(2)

    def remove():
        barrier.wait(timeout=5)
        try:
            outcome = service.delete_job(
                submitted.job_id,
                attempt_number=failed.attempt_number,
                revision=failed.revision)
            return "deleted", outcome["deleted"]
        except service_runtime.ServiceRuntimeError as exc:
            return "error", exc.code

    try:
        with ThreadPoolExecutor(max_workers=2) as pool:
            results = [future.result(timeout=5) for future in (
                pool.submit(remove), pool.submit(remove))]
        assert results.count(("deleted", True)) == 1
        assert results.count(("error", "not_found")) == 1
        assert service.healthy
    finally:
        service.close()


def test_active_job_deletion_plan_is_conflict_without_poisoning_health(
        tmp_path):
    service = _service(tmp_path)
    submitted = _submit_service_job(service)
    try:
        with pytest.raises(service_runtime.ServiceRuntimeError) as raised:
            service.deletion_plan(submitted.job_id)
        assert raised.value.code == "conflict"
        assert raised.value.fatal is False
        assert service.healthy
    finally:
        service.close()


@pytest.mark.parametrize(("cause", "code", "fatal", "healthy"), [
    (job_runtime.JobBusyError("busy"),
     "service_unavailable", False, True),
    (job_runtime.JobNotFoundError("vanished"), "not_found", False, True),
    (job_runtime.JobStateError("changed"), "conflict", False, True),
    (job_runtime.JobCorruptError("corrupt"),
     "service_unavailable", True, False),
])
def test_deletion_plan_classifies_wrapped_job_failures(
        monkeypatch, tmp_path, cause, code, fatal, healthy):
    service = _service(tmp_path)
    submitted = _submit_service_job(service)
    _advance_failed(service.store, submitted.job_id)

    def fail_plan(*_args, **_kwargs):
        raise retention.RetentionError("wrapped job failure") from cause

    monkeypatch.setattr(
        service_runtime.retention, "plan_background_job_deletion", fail_plan)
    try:
        with pytest.raises(service_runtime.ServiceRuntimeError) as raised:
            service.deletion_plan(submitted.job_id)
        assert raised.value.code == code
        assert raised.value.fatal is fatal
        assert service.healthy is healthy
    finally:
        service.close()


def test_delete_apply_contention_is_retryable_without_poisoning_health(
        monkeypatch, tmp_path):
    service = _service(tmp_path)
    submitted = _submit_service_job(service)
    failed = _advance_failed(service.store, submitted.job_id)

    def fail_apply(_plan):
        raise retention.RetentionError("wrapped job failure") from (
            job_runtime.JobBusyError("busy"))

    monkeypatch.setattr(
        service_runtime.retention, "apply_retention_plan", fail_apply)
    try:
        with pytest.raises(service_runtime.ServiceRuntimeError) as raised:
            service.delete_job(
                submitted.job_id,
                attempt_number=failed.attempt_number,
                revision=failed.revision)
        assert raised.value.code == "service_unavailable"
        assert raised.value.fatal is False
        assert service.healthy
    finally:
        service.close()


def test_retryable_search_failure_does_not_poison_service_readiness(
        tmp_path):
    attempts = []

    def fail(_config, _request, _request_id):
        attempts.append(True)
        raise service_runtime.ServiceRuntimeError("service_unavailable")

    service = _service(tmp_path, search_runner=fail)
    request = service_contracts.SearchRequest("query")
    try:
        with pytest.raises(service_runtime.ServiceRuntimeError):
            service.search("property", request, "req-1")
        assert service.healthy
        with pytest.raises(service_runtime.ServiceRuntimeError):
            service.search("property", request, "req-2")
        assert attempts == [True, True]
    finally:
        service.close()


def test_fatal_search_failure_marks_service_unready_and_refuses_more(tmp_path):
    attempts = []

    def fail(_config, _request, _request_id):
        attempts.append(True)
        raise service_runtime.ServiceRuntimeError(
            "service_unavailable", fatal=True)

    service = _service(tmp_path, search_runner=fail)
    request = service_contracts.SearchRequest("query")
    try:
        with pytest.raises(service_runtime.ServiceRuntimeError):
            service.search("property", request, "req-1")
        assert not service.healthy
        with pytest.raises(service_runtime.ServiceRuntimeError):
            service.search("property", request, "req-2")
        assert attempts == [True]
    finally:
        service.close()


def test_injected_search_runner_cannot_bypass_public_response_allowlist(
        tmp_path):
    def leak(config, request, request_id):
        result = _empty_search_response(config, request, request_id)
        result["db_path"] = "C:/private/qdrant"
        return result

    service = _service(tmp_path, search_runner=leak)
    try:
        with pytest.raises(service_runtime.ServiceRuntimeError) as raised:
            service.search(
                "property", service_contracts.SearchRequest("query"),
                "req-1")
        assert raised.value.fatal is True
        assert not service.healthy
    finally:
        service.close()


def test_service_search_uses_real_local_qdrant_contract(
        monkeypatch, tmp_path):
    pytest.importorskip("qdrant_client")
    chunks_path = tmp_path / "real-chunks.jsonl"
    chunks_path.write_text(json.dumps({
        "text": "Minimum contacts support personal jurisdiction.",
        "metadata": {
            "chunk_index": 0,
            "content_type": "case_opinion",
            "chapter_num": 2,
        },
    }) + "\n", encoding="utf-8")
    db_path = tmp_path / "real-qdrant"
    monkeypatch.setattr(
        service_runtime.rag, "_embed_texts",
        lambda texts, *_args, **_kwargs: [
            [1.0, 0.5, 0.25, 0.125] for _text in texts])
    monkeypatch.setattr(
        service_runtime.rag, "_validate_embedding_token_counts",
        lambda *_args, **_kwargs: None)
    service_runtime.rag.index_chunks_qdrant(
        chunks_path,
        db_path,
        collection_name="service_parity",
        embedding_model="service-test-model",
    )
    config = service_contracts.CorpusConfig(
        corpus_id="property",
        db_path=db_path.resolve(),
        chunks_path=chunks_path.resolve(),
        collection_name="service_parity",
        embedding_model="service-test-model",
    )

    result = service_runtime._execute_search(
        config,
        service_contracts.SearchRequest(
            "minimum contacts", mode="vector",
            filters=service_contracts.SearchFilters(chapter_num=2)),
        "req-real-qdrant",
    )

    assert result["backend"] == "qdrant"
    assert result["effective_mode"] == "vector"
    assert len(result["hits"]) == 1
    assert result["hits"][0]["text"].startswith("Minimum contacts")
    assert result["hits"][0]["metadata"]["chapter_num"] == 2
