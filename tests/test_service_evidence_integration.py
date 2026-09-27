"""Generated artifact/adapter integration, with explicitly synthetic vectors.

Portable tests do not pretend an injected function survived process exec. A
separate retained offline smoke qualifies the native embedding/worker path.
"""

from __future__ import annotations

import copy
from contextlib import contextmanager
from dataclasses import replace
import hashlib
import itertools
import json
import os
from pathlib import Path
import socket
import subprocess
import sys
import threading
import time

import pytest

import ai_pipeline_client
import application_composition
import rag
import release_security
import retrieval_core
import service_contracts
import service_evidence_contracts
import service_evidence_search
import service_http
import service_runtime
from tests.service_evidence_fixtures import build_bundle, physical_binding


POLICY = release_security.ReleaseSecurityPolicy()
READER_TOKEN = "synthetic-reader-" + "r" * 40
ADMIN_TOKEN = "synthetic-admin-" + "a" * 40
AUTH = {"Authorization": f"Bearer {READER_TOKEN}"}
PROJECT_ROOT = Path(__file__).resolve().parents[1]


def _response(records, *, request_mode="vector"):
    return retrieval_core.SearchResponse(
        hits=[retrieval_core.SearchHit(
            text=record["text"], metadata={key: value for key, value in rag._qdrant_payload(
                record, rag._chunk_id(record)).items() if key != "text"},
            score=0.75, source_id=rag._chunk_id(record)) for record in records],
        backend="qdrant", requested_mode=request_mode, effective_mode="vector",
        reranker_applied=False)


def _execute(bundle, *, records=None, binding=None):
    selected = bundle.records if records is None else records
    binding = physical_binding(search_index=lambda *a, **k: _response(selected)) if binding is None else binding
    return service_evidence_search.execute_evidence_search(
        bundle.config, bundle.evidence_config,
        service_contracts.SearchRequest("synthetic evidence", limit=20, mode="vector"),
        "integration-request", binding=binding, security_policy=POLICY)


def _application(bundle, *, enabled=True, internal_search=None, evidence_runner=None,
                 max_searches=2, enforce_peer=False):
    retrieve = (lambda *a, **k: _response(bundle.records, request_mode="vector")) if internal_search is None else internal_search

    def ordinary(config, request, request_id):
        return service_runtime._execute_search(config, request, request_id,
            search_index_fn=retrieve, security_policy=POLICY)

    def evidence(config, evidence_config, request, request_id, **kwargs):
        assert kwargs["security_policy"] == POLICY
        return service_evidence_search.execute_evidence_search(config, evidence_config, request, request_id,
            binding=physical_binding(search_index=retrieve), security_policy=kwargs["security_policy"])

    composition = replace(application_composition.default_service_application_composition(),
                          evidence_runner=evidence if evidence_runner is None else evidence_runner)
    return application_composition.create_service_application(
        {bundle.config.corpus_id: bundle.config}, service_http.ServiceCredentials(READER_TOKEN, ADMIN_TOKEN),
        composition=composition, job_root=bundle.root / "jobs", working_directory=bundle.root,
        output_root=bundle.root / "output", service_state_root=bundle.root / "service-state",
        search_runner=ordinary, security_policy=POLICY, max_concurrent_searches=max_searches,
        reconcile_interval_seconds=3600, enforce_peer_loopback=enforce_peer,
        **({"evidence_configs": {bundle.config.corpus_id: bundle.evidence_config}} if enabled else {}))


@contextmanager
def _client(app):
    pytest.importorskip("fastapi")
    from fastapi.testclient import TestClient
    with TestClient(app, base_url="http://127.0.0.1") as client:
        yield client


def _search_http(client, *, evidence=True, payload=None, headers=None, suffix="", corpus="synthetic"):
    return client.post(f"{'/evidence/v1' if evidence else '/v1'}/corpora/{corpus}/search{suffix}",
                       json={"query": "synthetic", "mode": "vector", "limit": 20} if payload is None else payload,
                       headers=AUTH if headers is None else headers)


def _assert_secure(response):
    assert response.headers["cache-control"] == "no-store"
    assert response.headers["x-content-type-options"] == "nosniff"
    assert response.headers["referrer-policy"] == "no-referrer"
    assert "access-control-allow-origin" not in response.headers


@pytest.mark.parametrize("with_table,with_multipage,excluded_frontmatter,preprocessed",
                         list(itertools.product((False, True), repeat=4)))
def test_real_artifact_graph_validates_all_generated_scope_variants(
        tmp_path, with_table, with_multipage, excluded_frontmatter, preprocessed):
    bundle = build_bundle(tmp_path, with_table=with_table, with_multipage=with_multipage,
                          excluded_frontmatter=excluded_frontmatter, preprocessed=preprocessed)
    result = _execute(bundle)
    assert bundle.quality["status"] == "pass"
    assert len(result["evidence"]) == len(bundle.records)
    assert result == service_evidence_contracts.validate_evidence_response(
        result, expected_corpus_id="synthetic", expected_request_id="integration-request")
    assert all(entry["text_accuracy"] == "not_verified" and entry["review_scope"] == "unmapped"
               for entry in result["evidence"])
    for entry, record in zip(result["evidence"], bundle.records):
        scope = entry["source_scope"]
        expected_pages = sorted({span["page"] for item in record["metadata"]["source_items"]
                                 for span in item["spans"]
                                 if span["provenance_index"] in item["scope"]["provenance_indexes"]})
        assert scope["pages"] == expected_pages
        assert scope["page_space"] == ("conversion_input" if preprocessed else "original_pdf")
        if record["metadata"].get("retrieval_role") == "table_child":
            assert scope["provenance_scope"] == "table_parent_source_items"
            assert "table_parent_scope" in entry["issues"]
        if preprocessed:
            assert entry["related_page_ocr"] == {
                "status": "unavailable", "reason": "original_page_mapping_unavailable", "pages": []}
    if excluded_frontmatter:
        assert bundle.structural_ranges == {(1, 1)}
        assert all(1 not in entry["source_scope"]["pages"] for entry in result["evidence"])


def test_false_negation_correction_and_five_page_states_never_mean_adopted(tmp_path):
    bundle = build_bundle(tmp_path)
    result = _execute(bundle)
    assert " is liable " in result["search"]["hits"][0]["text"]
    assert " is not liable " in bundle.report["pages"][0]["candidate"]["text"]
    assert [entry["related_page_ocr"]["pages"][0]["status"] for entry in result["evidence"]] == [
        "review_required", "empty_candidate", "retry_failed", "deferred", "not_selected"]
    assert all(entry["review_scope"] == "unmapped" and entry["text_accuracy"] == "not_verified"
               for entry in result["evidence"])
    serialized = json.dumps(result)
    assert " is not liable " not in serialized
    assert str(tmp_path) not in serialized
    assert "synthetic-no-execution" not in serialized
    assert "synthetic-source.bin" not in serialized


def test_fixture_prepares_final_records_before_any_artifact_binding(tmp_path):
    calls = []

    def prepare(records):
        assert not (tmp_path / "synthetic.json").exists()
        assert all("stable_id" in record["metadata"] for record in records)
        assert sum(record["metadata"].get("retrieval_role") == "table_child" for record in records) == 4
        calls.append(len(records))
        for record in records:
            record["metadata"]["embedding_token_count"] = 101

    bundle = build_bundle(tmp_path, with_table=True, with_multipage=True,
                          excluded_frontmatter=True, prepare_records=prepare)
    assert calls == [12]
    saved = [json.loads(line) for line in bundle.chunks_path.read_text().splitlines()]
    assert saved == bundle.records
    assert all(record["metadata"]["embedding_token_count"] == 101 for record in saved)
    assert len(_execute(bundle)["evidence"]) == 12


def test_fixture_preparation_failure_does_not_publish_partial_bound_artifacts(tmp_path):
    def fail(_records):
        raise ValueError("synthetic preparation failure")

    with pytest.raises(ValueError, match="synthetic preparation failure"):
        build_bundle(tmp_path, prepare_records=fail)
    assert list(tmp_path.iterdir()) == [tmp_path / "qdrant"]


@pytest.mark.parametrize("prepared", [False, True])
def test_real_index_count_materialization_requires_exact_source_counts(monkeypatch, tmp_path, prepared):
    pytest.importorskip("qdrant_client")
    monkeypatch.setattr(rag, "_count_embedding_text_tokens", lambda texts, _model: ([7] * len(texts), True))
    monkeypatch.setattr(rag, "_embed_texts", lambda texts, *_a, **_kw: [[1.0, 0.5, 0.25, 0.125] for _ in texts])

    def prepare(records):
        rag._validate_embedding_token_counts(records, "nomic-ai/nomic-embed-text-v2-moe", recompute=True)

    bundle = build_bundle(tmp_path, prepare_records=prepare if prepared else None)
    before = bundle.chunks_path.read_bytes()
    outcome = rag.index_chunks_qdrant(bundle.chunks_path, bundle.db_path,
        collection_name=bundle.config.collection_name, embedding_model=bundle.config.embedding_model,
        full_reindex=True, security_policy=POLICY)
    assert outcome.committed and bundle.chunks_path.read_bytes() == before
    if prepared:
        assert len(_execute(bundle, binding=physical_binding())["evidence"]) == 5
    else:
        with pytest.raises(service_contracts.ServiceContractError) as error:
            _execute(bundle, binding=physical_binding())
        assert error.value.code == "service_unavailable"


def test_actual_qdrant_payloads_bind_to_actual_artifacts_without_ocr_or_real_models(monkeypatch, tmp_path):
    pytest.importorskip("qdrant_client")
    bundle = build_bundle(tmp_path)
    observed = []

    def embed(texts, model, **kwargs):
        observed.append((len(texts), model, kwargs.get("input_type", "document")))
        return [[1.0, 0.5, 0.25, 0.125] for _ in texts]

    monkeypatch.setattr(rag, "_embed_texts", embed)
    monkeypatch.setattr(rag, "_validate_embedding_token_counts", lambda *a, **k: None)
    rag.index_chunks_qdrant(bundle.chunks_path, bundle.db_path,
                           collection_name=bundle.config.collection_name,
                           embedding_model=bundle.config.embedding_model, security_policy=POLICY)
    result = _execute(bundle, binding=physical_binding())
    assert len(result["evidence"]) == 5
    assert {entry["source_scope"]["pages"][0] for entry in result["evidence"]} == {1, 2, 3, 4, 5}
    # The index producer also probes the embedding dimension before batching.
    assert [role for _, _, role in observed] == ["document", "document", "query"]
    assert all(model == bundle.config.embedding_model for _, model, _ in observed)
    assert len({entry["record_sha256"] for entry in result["evidence"]}) == 5


def test_public_truncation_keeps_full_record_correspondence_before_serializing(tmp_path):
    prefix = "Synthetic shared prefix about source fidelity. " * 800
    bundle = build_bundle(tmp_path, texts=(prefix + "Alpha final value.", prefix + "Beta final value."),
                          embedding_model="synthetic-long-test-model")
    result = _execute(bundle)
    first, second = result["search"]["hits"]
    assert first["text_truncated"] and second["text_truncated"]
    assert first["text"] == second["text"]
    assert result["evidence"][0]["text_sha256"] != result["evidence"][1]["text_sha256"]
    assert result["evidence"][0]["record_sha256"] != result["evidence"][1]["record_sha256"]
    for record, entry in zip(bundle.records, result["evidence"]):
        assert entry["text_sha256"] == hashlib.sha256(record["text"].encode()).hexdigest()
    corrupted = copy.deepcopy(bundle.records)
    corrupted[0]["text"] = corrupted[1]["text"]
    # The stored ID stays fixed while a backend substitutes the other full text.
    response = _response(bundle.records)
    response.hits[0].text = corrupted[0]["text"]
    with pytest.raises(service_contracts.ServiceContractError):
        _execute(bundle, binding=physical_binding(search_index=lambda *a, **k: response))


@pytest.mark.parametrize("enabled", [False, True])
def test_composed_http_retains_v1_openapi_and_search_without_evidence_widening(tmp_path, enabled):
    bundle = build_bundle(tmp_path)
    app = _application(bundle, enabled=enabled)
    with _client(app) as client:
        schema = client.get("/v1/openapi.json", headers=AUTH)
        assert schema.status_code == 200
        normalized = (json.dumps(schema.json(), sort_keys=True, ensure_ascii=False, separators=(",", ":")) + "\n").encode()
        assert hashlib.sha256(normalized).hexdigest() == ai_pipeline_client.OPENAPI_SHA256
        ordinary = _search_http(client, evidence=False)
        assert ordinary.status_code == 200
        _assert_secure(ordinary)
        assert set(ordinary.json()) == {"schema_version", "request_id", "corpus_id", "backend",
            "requested_mode", "effective_mode", "reranker_applied", "warnings", "hits"}
        companion = _search_http(client)
        assert companion.status_code == (200 if enabled else 404)
        _assert_secure(companion)
        if enabled:
            actual = companion.json()
            direct = _execute(bundle)
            direct["search"]["request_id"] = actual["search"]["request_id"]
            assert actual == direct
        else:
            assert companion.json()["code"] == "not_found"
    assert not app.state.runtime.started


@pytest.mark.parametrize("headers,status,code", [({}, 401, "unauthorized"),
    ({"Authorization": "Bearer " + "x" * 48}, 401, "unauthorized"),
    ({"Authorization": f"Bearer {ADMIN_TOKEN}"}, 200, None),
    ({**AUTH, "Host": "localhost"}, 400, "invalid_request")])
def test_evidence_route_reuses_base_authentication_and_host_policy(tmp_path, headers, status, code):
    app = _application(build_bundle(tmp_path))
    with _client(app) as client:
        response = _search_http(client, headers=headers)
        assert response.status_code == status
        _assert_secure(response)
        if code is not None:
            assert response.json()["code"] == code
            assert READER_TOKEN not in response.text and ADMIN_TOKEN not in response.text


@pytest.mark.parametrize("payload,suffix,corpus", [
    ({"query": "synthetic", "path": "PRIVATE/PDF"}, "", "synthetic"),
    ({"query": "synthetic", "limit": True}, "", "synthetic"),
    ({"query": "synthetic", "filters": {"page": 1}}, "", "synthetic"),
    ({"query": "synthetic"}, "?crop=PRIVATE", "synthetic"),
    ({"query": "synthetic"}, "", "unconfigured"),
])
def test_unconfigured_corpus_and_new_authority_fields_fail_before_retrieval(tmp_path, payload, suffix, corpus):
    calls = []
    app = _application(build_bundle(tmp_path), internal_search=lambda *a, **k: calls.append(1))
    with _client(app) as client:
        response = _search_http(client, payload=payload, suffix=suffix, corpus=corpus)
        assert response.status_code == (404 if corpus == "unconfigured" else 400)
        assert not calls
        assert "PRIVATE" not in response.text
        _assert_secure(response)


@pytest.mark.parametrize("corruption", ["text", "metadata", "boolean", "duplicate", "correction"])
def test_full_payload_forgery_fails_across_composition_http_before_public_results(tmp_path, corruption):
    bundle = build_bundle(tmp_path)
    response = _response(bundle.records)
    if corruption == "text":
        response.hits[0].text += " PRIVATE substituted ending"
    elif corruption == "metadata":
        response.hits[0].metadata["source_file"] = "PRIVATE-forged.json"
    elif corruption == "boolean":
        response.hits[0].metadata["chapter_num"] = True
    elif corruption == "duplicate":
        response.hits[1] = copy.deepcopy(response.hits[0])
    else:
        response.hits[0].text = bundle.report["pages"][0]["candidate"]["text"]
    app = _application(bundle, internal_search=lambda *a, **k: response)
    with _client(app) as client:
        actual = _search_http(client)
        assert actual.status_code == 503
        assert actual.json()["code"] == "service_unavailable"
        assert "PRIVATE" not in actual.text and "liable" not in actual.text
        assert "hits" not in actual.json()
        _assert_secure(actual)


@pytest.mark.parametrize("operation", ["ordinary", "evidence"])
def test_evidence_and_ordinary_search_share_capacity_and_shutdown_waits(tmp_path, operation):
    bundle = build_bundle(tmp_path)
    entered, release, closed = threading.Event(), threading.Event(), threading.Event()

    def held(config, evidence_config, request, request_id, **kwargs):
        entered.set()
        assert release.wait(10)
        return service_evidence_search.execute_evidence_search(config, evidence_config, request, request_id,
            binding=physical_binding(search_index=lambda *a, **k: _response(bundle.records)),
            security_policy=kwargs["security_policy"])

    app = _application(bundle, evidence_runner=held, max_searches=1)
    runtime = app.state.runtime
    started, begin_close = threading.Event(), threading.Event()
    result, errors = [], []

    def lifecycle():
        try:
            runtime.start()
            started.set()
            assert begin_close.wait(10)
            runtime.close()
            closed.set()
        except BaseException as exc:
            errors.append(type(exc).__name__)

    def search():
        try:
            result.append(runtime.search_evidence("synthetic", service_contracts.SearchRequest("synthetic", mode="vector"),
                                                  "held-integration"))
        except BaseException as exc:
            errors.append(type(exc).__name__)

    thread = threading.Thread(target=search, daemon=True)
    # PathLease itself is thread-affine. Keep its owner stable while checking
    # the runtime's cross-operation capacity and draining semantics.
    closing = threading.Thread(target=lifecycle, daemon=True)
    try:
        closing.start()
        assert started.wait(5)
        thread.start()
        assert entered.wait(5)
        call = runtime.search if operation == "ordinary" else runtime.search_evidence
        with pytest.raises(service_runtime.ServiceRuntimeError) as caught:
            call("synthetic", service_contracts.SearchRequest("synthetic"), "limited-integration")
        assert caught.value.code == "concurrency_limited"
        begin_close.set()
        assert not closed.wait(0.05)
        assert runtime._active_searches == 1
    finally:
        release.set()
        begin_close.set()
        if thread.ident is not None:
            thread.join(10)
        closing.join(10)
    assert not thread.is_alive() and not closing.is_alive()
    assert closed.is_set() and runtime._active_searches == 0
    assert not errors and len(result) == 1


@contextmanager
def _live_server(app):
    uvicorn = pytest.importorskip("uvicorn")
    listener = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
    listener.bind(("127.0.0.1", 0))
    listener.listen(128)
    port = listener.getsockname()[1]
    server = uvicorn.Server(uvicorn.Config(app, host="127.0.0.1", port=port,
        access_log=False, proxy_headers=False, server_header=False, log_level="critical", lifespan="on"))
    thread = threading.Thread(target=server.run, kwargs={"sockets": [listener]}, daemon=True)
    thread.start()
    try:
        deadline = time.monotonic() + 10
        while thread.is_alive() and not server.started and time.monotonic() < deadline:
            time.sleep(0.01)
        assert server.started and thread.is_alive()
        yield port
    finally:
        server.should_exit = True
        thread.join(10)
        listener.close()
    assert not thread.is_alive()


def test_actual_qdrant_through_authenticated_loopback_reader_and_cli(monkeypatch, tmp_path):
    pytest.importorskip("qdrant_client")
    bundle = build_bundle(tmp_path)
    monkeypatch.setattr(rag, "_embed_texts", lambda texts, *a, **k: [[1.0, 0.5, 0.25, 0.125] for _ in texts])
    monkeypatch.setattr(rag, "_validate_embedding_token_counts", lambda *a, **k: None)
    rag.index_chunks_qdrant(bundle.chunks_path, bundle.db_path,
                           collection_name=bundle.config.collection_name,
                           embedding_model=bundle.config.embedding_model, security_policy=POLICY)
    app = _application(bundle, internal_search=rag.search_index, enforce_peer=True)
    with _live_server(app) as port:
        reader = ai_pipeline_client.PipelineReader(port=port, token=READER_TOKEN)
        payload = {"query": "synthetic", "mode": "vector", "limit": 5}
        result = reader.search_evidence("synthetic", payload)
        ordinary = reader.search("synthetic", payload)
        assert len(result["evidence"]) == 5
        assert result["search"]["hits"] == ordinary["hits"]
        assert reader.schema() == service_http.service_openapi_document()
        command = [sys.executable, str(PROJECT_ROOT / "tools/query_pipeline.py"),
                   "--port", str(port), "search-evidence", "--corpus", "synthetic"]
        environment = dict(os.environ)
        environment[ai_pipeline_client.TOKEN_ENVIRONMENT_VARIABLE] = READER_TOKEN
        process = subprocess.run(command, input=json.dumps(payload), text=True,
                                 capture_output=True, timeout=20, env=environment, cwd=PROJECT_ROOT)
        assert process.returncode == 0 and process.stderr == ""
        actual = json.loads(process.stdout)
        actual["search"]["request_id"] = result["search"]["request_id"]
        assert actual == result
        assert str(tmp_path) not in process.stdout and READER_TOKEN not in process.stdout
    assert app.state.runtime.started is False


def test_default_composition_host_imports_do_not_load_retrieval_or_http_facade():
    source = """
import sys
import application_composition as root
assert 'service_http' not in sys.modules and 'fastapi' not in sys.modules
root.default_service_application_composition()
assert not {'rag', 'service_api', 'job_manager', 'qdrant_client', 'sentence_transformers', 'torch'} & set(sys.modules)
print('cold-host-approved')
"""
    process = subprocess.run([sys.executable, "-c", source], cwd=PROJECT_ROOT,
                             capture_output=True, text=True, timeout=20)
    assert process.returncode == 0 and process.stdout.strip() == "cold-host-approved"
