import builtins
import json
import os
import stat
import subprocess
import sys
import threading
from concurrent.futures import ThreadPoolExecutor
from dataclasses import FrozenInstanceError
from pathlib import Path
from types import SimpleNamespace

import pytest

pytest.importorskip("fastapi")

from fastapi.testclient import TestClient

import job_manager
import job_runtime
import service_api
import service_contracts
import service_runtime
import storage_policy


READER_TOKEN = "r" * 48
ADMIN_TOKEN = "a" * 48
READER_AUTH = {"Authorization": f"Bearer {READER_TOKEN}"}
ADMIN_AUTH = {"Authorization": f"Bearer {ADMIN_TOKEN}"}


def _config(tmp_path):
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
        collection_name="property",
        embedding_model="test-model",
        reindex_timeout_seconds=30,
    )


def _launch_starting(store, job_id, *, ready_timeout):
    del ready_timeout
    execution = store.load_execution(job_id)
    summary = store.transition_job(
        job_id, "starting",
        attempt_token=execution.attempt_token,
        expected_revision=execution.revision)
    return job_manager.LaunchResult(
        job_id=job_id,
        status=summary.status,
        attempt_number=summary.attempt_number,
        ready=True,
    )


def _runtime(tmp_path, *, search_runner=None):
    config = _config(tmp_path)
    options = {}
    if search_runner is not None:
        options["search_runner"] = search_runner
    return service_runtime.RagApplicationService(
        {"property": config},
        job_root=tmp_path / "jobs",
        working_directory=tmp_path,
        output_root=tmp_path / "output",
        service_state_root=tmp_path / "service-state",
        launcher=_launch_starting,
        **options,
    )


def _client(runtime):
    app = service_api.create_app(
        runtime,
        service_api.ServiceCredentials(READER_TOKEN, ADMIN_TOKEN),
        host="127.0.0.1",
        reconcile_interval_seconds=3600,
        enforce_peer_loopback=False,
    )
    return TestClient(app, base_url="http://127.0.0.1")


def _advance_failed(store, job_id):
    execution = store.load_execution(job_id)
    if execution.status == "queued":
        starting = store.transition_job(
            job_id, "starting", attempt_token=execution.attempt_token,
            expected_revision=execution.revision)
    else:
        starting = store.get_job(job_id)
    running = store.transition_job(
        job_id, "running", attempt_token=execution.attempt_token,
        expected_revision=starting.revision)
    return store.transition_job(
        job_id, "failed", attempt_token=execution.attempt_token,
        expected_revision=running.revision)


def _submit_service_job(runtime, *, full_reindex=False, job_id=None):
    config = runtime.corpora["property"]
    request = service_contracts.ReindexRequest(full_reindex)
    return runtime.store.submit_job(
        "index", runtime._reindex_argv(config, request),
        timeout_seconds=config.reindex_timeout_seconds,
        job_id=job_id,
        working_directory=runtime.working_directory,
        output_root=runtime.output_root,
    )


def _assert_security_headers(response):
    assert response.headers["cache-control"] == "no-store"
    assert response.headers["x-content-type-options"] == "nosniff"
    assert response.headers["referrer-policy"] == "no-referrer"
    assert response.headers["x-frame-options"] == "DENY"
    assert response.headers["content-security-policy"] == "default-src 'none'"
    assert "access-control-allow-origin" not in response.headers


def test_health_host_boundary_and_security_headers(tmp_path):
    runtime = _runtime(tmp_path)
    with _client(runtime) as client:
        live = client.get("/health/live")
        assert live.status_code == 200
        assert live.json() == {"status": "live"}
        _assert_security_headers(live)

        ready = client.get("/health/ready")
        assert ready.status_code == 200
        assert ready.json() == {"status": "ready"}

        rejected = client.get("/health/live", headers={"Host": "evil.example"})
        assert rejected.status_code == 400
        assert rejected.json()["code"] == "invalid_request"
        assert "evil.example" not in rejected.text
        _assert_security_headers(rejected)


def test_authentication_roles_and_request_ids(tmp_path):
    runtime = _runtime(tmp_path)
    with _client(runtime) as client:
        missing = client.get("/v1/corpora")
        assert missing.status_code == 401
        assert missing.headers["www-authenticate"] == "Bearer"
        assert missing.json()["code"] == "unauthorized"

        invalid = client.get(
            "/v1/corpora",
            headers={"Authorization": "Bearer " + "x" * 48})
        assert invalid.status_code == 401

        reader = client.get(
            "/v1/corpora",
            headers={**READER_AUTH, "X-Request-ID": "caller-17"})
        assert reader.status_code == 200
        assert reader.headers["x-request-id"] == "caller-17"
        assert reader.json()["items"][0]["corpus_id"] == "property"
        assert str(tmp_path) not in reader.text

        forbidden = client.get("/v1/jobs", headers=READER_AUTH)
        assert forbidden.status_code == 403
        assert forbidden.json()["code"] == "forbidden"

        admin_reader = client.get("/v1/corpora", headers=ADMIN_AUTH)
        assert admin_reader.status_code == 200

        bad_request_id = client.get(
            "/v1/corpora",
            headers={**READER_AUTH, "X-Request-ID": "contains spaces"})
        assert bad_request_id.status_code == 400
        assert bad_request_id.json()["request_id"] != "contains spaces"


def test_search_body_is_strict_bounded_and_reader_authorized(tmp_path):
    observed = []

    def search_runner(config, request, request_id):
        observed.append((config.corpus_id, request, request_id))
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

    runtime = _runtime(tmp_path, search_runner=search_runner)
    with _client(runtime) as client:
        accepted = client.post(
            "/v1/corpora/property/search",
            headers=READER_AUTH,
            json={
                "query": "minimum contacts",
                "limit": 3,
                "mode": "vector",
                "filters": {"chapter_num": 4},
            })
        assert accepted.status_code == 200
        assert accepted.json()["request_id"] == accepted.headers["x-request-id"]
        assert observed[0][1].filters.chapter_num == 4

        invalid_corpus = client.post(
            "/v1/corpora/INVALID/search",
            headers=READER_AUTH,
            json={"query": "ok"})
        missing_corpus = client.post(
            "/v1/corpora/missing/search",
            headers=READER_AUTH,
            json={"query": "ok"})
        assert invalid_corpus.status_code == 400
        assert missing_corpus.status_code == 404
        assert runtime.healthy
        assert client.get("/health/ready").status_code == 200

        duplicate = client.post(
            "/v1/corpora/property/search",
            headers={**READER_AUTH, "Content-Type": "application/json"},
            content=b'{"query":"one","query":"two"}')
        assert duplicate.status_code == 400
        assert duplicate.json()["code"] == "invalid_json"

        unknown = client.post(
            "/v1/corpora/property/search",
            headers=READER_AUTH,
            json={"query": "ok", "db_path": "C:/private"})
        assert unknown.status_code == 400
        assert "private" not in unknown.text

        wrong_type = client.post(
            "/v1/corpora/property/search",
            headers={**READER_AUTH, "Content-Type": "text/plain"},
            content=b'{"query":"ok"}')
        assert wrong_type.status_code == 415

        oversized = client.post(
            "/v1/corpora/property/search",
            headers={**READER_AUTH, "Content-Type": "application/json"},
            content=b' ' * (service_contracts.MAX_REQUEST_BYTES + 1))
        assert oversized.status_code == 413


def test_reindex_requires_admin_idempotency_and_replays_one_job(tmp_path):
    runtime = _runtime(tmp_path)
    headers = {
        **ADMIN_AUTH,
        "Idempotency-Key": "semester-reindex-1234",
    }
    with _client(runtime) as client:
        reader = client.post(
            "/v1/corpora/property/reindex",
            headers={
                **READER_AUTH,
                "Idempotency-Key": "semester-reindex-1234",
            },
            json={})
        assert reader.status_code == 403

        missing = client.post(
            "/v1/corpora/property/reindex",
            headers=ADMIN_AUTH,
            json={})
        assert missing.status_code == 428

        created = client.post(
            "/v1/corpora/property/reindex", headers=headers, json={})
        assert created.status_code == 202
        assert created.headers["location"].startswith("/v1/jobs/")
        assert created.headers["etag"] == created.json()["job"]["etag"]
        assert created.json()["idempotent_replay"] is False

        replay = client.post(
            "/v1/corpora/property/reindex", headers=headers, json={})
        assert replay.status_code == 200
        assert replay.json()["idempotent_replay"] is True
        assert replay.json()["job"]["job_id"] == created.json()["job"]["job_id"]

        conflict = client.post(
            "/v1/corpora/property/reindex",
            headers=headers,
            json={"full_reindex": True})
        assert conflict.status_code == 409
        assert conflict.json()["code"] == "conflict"


def test_jobs_list_get_cancel_and_method_failures_are_stable(tmp_path):
    runtime = _runtime(tmp_path)
    with _client(runtime) as client:
        submitted = _submit_service_job(runtime)
        listed = client.get("/v1/jobs?limit=1", headers=ADMIN_AUTH)
        assert listed.status_code == 200
        assert listed.json()["items"][0]["job_id"] == submitted.job_id
        assert "private" not in listed.text

        fetched = client.get(
            f"/v1/jobs/{submitted.job_id}", headers=ADMIN_AUTH)
        assert fetched.status_code == 200
        assert fetched.headers["etag"] == fetched.json()["etag"]

        missing_precondition = client.post(
            f"/v1/jobs/{submitted.job_id}/cancel", headers=ADMIN_AUTH)
        assert missing_precondition.status_code == 428

        accepted = client.post(
            f"/v1/jobs/{submitted.job_id}/cancel",
            headers={**ADMIN_AUTH, "If-Match": fetched.headers["etag"]})
        assert accepted.status_code == 202
        assert accepted.json()["job"]["job_id"] == submitted.job_id

        duplicate_query = client.get(
            "/v1/jobs?limit=1&limit=2", headers=ADMIN_AUTH)
        assert duplicate_query.status_code == 400

        method = client.put(
            f"/v1/jobs/{submitted.job_id}", headers=ADMIN_AUTH)
        assert method.status_code == 405
        assert method.json()["code"] == "method_not_allowed"


def test_foreign_jobs_are_hidden_and_never_mutated_by_api(tmp_path):
    runtime = _runtime(tmp_path)
    with _client(runtime) as client:
        foreign = runtime.store.submit_job(
            "export", ["--chunks", "C:/private/foreign.jsonl"],
            working_directory=tmp_path, output_root=runtime.output_root)
        etag = service_contracts.job_etag(
            foreign.attempt_number, foreign.revision)

        listed = client.get("/v1/jobs", headers=ADMIN_AUTH)
        fetched = client.get(
            f"/v1/jobs/{foreign.job_id}", headers=ADMIN_AUTH)
        cancelled = client.post(
            f"/v1/jobs/{foreign.job_id}/cancel",
            headers={**ADMIN_AUTH, "If-Match": etag})

        assert listed.status_code == 200
        assert listed.json()["items"] == []
        assert fetched.status_code == 404
        assert cancelled.status_code == 404
        assert runtime.store.get_job(foreign.job_id).status == "queued"
        assert not (
            runtime.store.root / foreign.job_id / ".job.lock").exists()
        assert runtime.healthy


def test_resume_uses_exact_etag_and_stale_retry_is_412(tmp_path):
    runtime = _runtime(tmp_path)
    with _client(runtime) as client:
        submitted = _submit_service_job(runtime)
        failed = _advance_failed(runtime.store, submitted.job_id)
        etag = service_contracts.job_etag(
            failed.attempt_number, failed.revision)

        resumed = client.post(
            f"/v1/jobs/{submitted.job_id}/resume",
            headers={**ADMIN_AUTH, "If-Match": etag})
        assert resumed.status_code == 202
        assert resumed.json()["job"]["attempt_number"] == 2
        assert resumed.headers["etag"] != etag

        stale = client.post(
            f"/v1/jobs/{submitted.job_id}/resume",
            headers={**ADMIN_AUTH, "If-Match": etag})
        assert stale.status_code == 412
        assert stale.json()["code"] == "precondition_failed"


def test_delete_is_dry_run_first_confirmed_and_redacted(tmp_path):
    runtime = _runtime(tmp_path)
    with _client(runtime) as client:
        submitted = _submit_service_job(runtime)
        failed = _advance_failed(runtime.store, submitted.job_id)
        etag = service_contracts.job_etag(
            failed.attempt_number, failed.revision)

        plan = client.post(
            f"/v1/jobs/{submitted.job_id}/deletion-plan",
            headers=ADMIN_AUTH)
        assert plan.status_code == 200
        assert plan.json()["apply_required"] is True
        assert "private" not in plan.text
        assert str(tmp_path) not in plan.text
        assert "fingerprint" not in plan.text

        missing = client.delete(
            f"/v1/jobs/{submitted.job_id}",
            headers={**ADMIN_AUTH, "If-Match": etag})
        assert missing.status_code == 428

        wrong = client.delete(
            f"/v1/jobs/{submitted.job_id}",
            headers={
                **ADMIN_AUTH,
                "If-Match": etag,
                "X-Confirm-Job-ID": "f" * 32,
            })
        assert wrong.status_code == 412

        deleted = client.delete(
            f"/v1/jobs/{submitted.job_id}",
            headers={
                **ADMIN_AUTH,
                "If-Match": etag,
                "X-Confirm-Job-ID": submitted.job_id,
            })
        assert deleted.status_code == 200
        assert deleted.json()["deleted"] is True
        with pytest.raises(job_runtime.JobNotFoundError):
            runtime.store.get_job(submitted.job_id)


def test_runtime_errors_never_expose_exception_or_config(tmp_path):
    def fail(_config, _request, _request_id):
        raise RuntimeError("C:/private/book.pdf API_KEY=secret")

    runtime = _runtime(tmp_path, search_runner=fail)
    with _client(runtime) as client:
        failed = client.post(
            "/v1/corpora/property/search",
            headers=READER_AUTH,
            json={"query": "private query"})
        assert failed.status_code == 503
        assert failed.json()["code"] == "service_unavailable"
        assert failed.headers["retry-after"] == "1"
        assert "RuntimeError" not in failed.text
        assert "API_KEY" not in failed.text
        assert str(tmp_path) not in failed.text

        refused = client.post(
            "/v1/corpora/property/search",
            headers=READER_AUTH,
            json={"query": "another"})
        assert refused.status_code == 503


def test_unexpected_exception_is_contained_without_raw_logging(
        tmp_path, caplog, capsys):
    marker = "C:/private/raw-exception-marker API_KEY=secret"
    runtime = _runtime(tmp_path)
    app = service_api.create_app(
        runtime,
        service_api.ServiceCredentials(READER_TOKEN, ADMIN_TOKEN),
        reconcile_interval_seconds=3600,
        enforce_peer_loopback=False,
    )

    @app.get("/_test/unexpected")
    async def unexpected():
        raise RuntimeError(marker)

    with TestClient(app, base_url="http://127.0.0.1") as client:
        response = client.get("/_test/unexpected")
        assert response.status_code == 500
        assert response.json()["code"] == "internal_error"
        assert marker not in response.text
        assert client.get("/health/ready").status_code == 503

    captured = capsys.readouterr()
    assert marker not in captured.out
    assert marker not in captured.err
    assert marker not in caplog.text


def test_versioned_openapi_is_authenticated_and_docs_are_disabled(tmp_path):
    runtime = _runtime(tmp_path)
    with _client(runtime) as client:
        assert client.get("/docs").status_code == 404
        assert client.get("/openapi.json").status_code == 404
        assert client.get("/v1/openapi.json").status_code == 401
        document = client.get(
            "/v1/openapi.json", headers=READER_AUTH)
        assert document.status_code == 200
        assert document.json() == service_api.service_openapi_document()
        assert "/v1/corpora/{corpus_id}/search" in document.json()["paths"]


def test_openapi_snapshot_resolves_refs_and_documents_strict_boundaries():
    document = service_api.service_openapi_document()
    snapshot = json.loads((
        Path(__file__).resolve().parents[1] / "service-openapi-v1.json"
    ).read_text(encoding="utf-8"))
    assert document == snapshot

    def resolve(reference):
        assert reference.startswith("#/")
        value = document
        for component in reference[2:].split("/"):
            value = value[component]
        return value

    def walk(value):
        if isinstance(value, dict):
            if "$ref" in value:
                resolve(value["$ref"])
            for nested in value.values():
                walk(nested)
        elif isinstance(value, list):
            for nested in value:
                walk(nested)

    walk(document)
    schemas = document["components"]["schemas"]
    assert schemas["SearchRequest"]["additionalProperties"] is False
    assert schemas["SearchResponse"]["additionalProperties"] is False
    assert schemas["Problem"]["additionalProperties"] is False
    reindex = document["paths"][
        "/v1/corpora/{corpus_id}/reindex"]["post"]
    parameter_names = {
        item["$ref"].rsplit("/", 1)[1] for item in reindex["parameters"]}
    assert {"RequestID", "CorpusID", "IdempotencyKey"} <= parameter_names
    deletion = document["paths"]["/v1/jobs/{job_id}"]["delete"]
    deletion_parameters = {
        item["$ref"].rsplit("/", 1)[1]
        for item in deletion["parameters"]}
    assert {"IfMatch", "ConfirmJobID", "JobID"} <= deletion_parameters
    assert "application/problem+json" in document[
        "components"]["responses"]["Problem503"]["content"]
    assert document["servers"] == [{"url": "/"}]
    for path_item in document["paths"].values():
        for operation in path_item.values():
            assert {"400", "429", "500"} <= operation["responses"].keys()
    assert document["paths"]["/v1/corpora"]["get"]["security"] == [
        {"readerBearer": []}, {"adminBearer": []}]


def test_token_files_are_distinct_private_and_never_printed(
        monkeypatch, tmp_path, capsys):
    reader_path = tmp_path / "tokens" / "reader.token"
    admin_path = tmp_path / "tokens" / "admin.token"
    # URL-safe random data can begin with a non-alphanumeric byte.  Generated
    # credentials must still satisfy the service's strict bearer-token grammar.
    monkeypatch.setattr(
        service_api.secrets, "token_urlsafe", lambda _size: "-" * 64)

    assert service_api.main([
        "init-tokens",
        "--reader-token-file", str(reader_path),
        "--admin-token-file", str(admin_path),
    ]) == 0

    reader = service_api.load_token_file(reader_path)
    admin = service_api.load_token_file(admin_path)
    assert reader != admin
    assert len(reader) >= 32
    assert len(admin) >= 32
    output = capsys.readouterr().out
    assert reader not in output
    assert admin not in output
    if os.name != "nt":
        assert stat.S_IMODE(reader_path.stat().st_mode) == 0o600
        assert stat.S_IMODE(admin_path.stat().st_mode) == 0o600
        assert stat.S_IMODE(reader_path.parent.stat().st_mode) == 0o700

    with pytest.raises(FileExistsError):
        service_api.initialize_token_files(reader_path, admin_path)
    assert service_api.load_token_file(reader_path) == reader
    assert service_api.load_token_file(admin_path) == admin


def test_service_credentials_are_frozen_slotted_opaque_and_role_aware():
    credentials = service_api.ServiceCredentials(READER_TOKEN, ADMIN_TOKEN)

    assert not hasattr(credentials, "__dict__")
    assert repr(credentials) == "ServiceCredentials()"
    assert READER_TOKEN not in repr(credentials)
    assert ADMIN_TOKEN not in repr(credentials)
    assert credentials.role_for(READER_TOKEN) == "reader"
    assert credentials.role_for(ADMIN_TOKEN) == "admin"
    assert credentials.role_for("x" * 48) is None
    with pytest.raises(FrozenInstanceError):
        credentials.reader_token = "z" * 48
    with pytest.raises(service_contracts.ServiceContractError):
        service_api.ServiceCredentials(READER_TOKEN, READER_TOKEN)


def test_main_serve_preserves_construction_order_and_uvicorn_options(
        monkeypatch, tmp_path):
    events = []
    config_path = tmp_path / "corpora.json"
    reader_path = tmp_path / "reader.token"
    admin_path = tmp_path / "admin.token"
    job_root = tmp_path / "jobs"
    working_directory = tmp_path / "work"
    output_root = tmp_path / "output"
    service_state_root = tmp_path / "state"
    registry = object()
    security_policy = object()
    credentials = object()
    runtime = SimpleNamespace(
        start=lambda: pytest.fail("main must leave startup to ASGI lifespan"))
    app = object()
    runtime_binding = object()
    job_coordination_binding = object()
    http_binding = object()

    def load_registry(path):
        events.append(("registry", path))
        return registry

    def create_security_policy(**kwargs):
        events.append(("security_policy", kwargs))
        return security_policy

    def load_token(path):
        events.append(("token", path))
        return {
            reader_path: READER_TOKEN,
            admin_path: ADMIN_TOKEN,
        }[path]

    def create_credentials(reader_token, admin_token):
        events.append(("credentials", reader_token, admin_token))
        return credentials

    def create_runtime(runtime_registry, **kwargs):
        events.append(("runtime", runtime_registry, kwargs))
        return runtime

    def create_app(runtime_arg, credentials_arg, **kwargs):
        events.append(("app", runtime_arg, credentials_arg, kwargs))
        return app

    def run_uvicorn(app_arg, **kwargs):
        events.append(("uvicorn", app_arg, kwargs))

    monkeypatch.setattr(
        service_api.service_runtime, "load_corpus_registry", load_registry)
    monkeypatch.setattr(
        service_api.release_security,
        "ReleaseSecurityPolicy",
        SimpleNamespace(from_values=create_security_policy),
    )
    monkeypatch.setattr(service_api, "load_token_file", load_token)
    monkeypatch.setattr(service_api, "ServiceCredentials", create_credentials)
    composition = (
        service_api.application_composition.ServiceApplicationComposition(
            runtime_factory=create_runtime,
            http_factory=create_app,
            runtime_binding=runtime_binding,
            job_coordination_binding=job_coordination_binding,
            http_binding=http_binding,
        ))
    monkeypatch.setattr(
        service_api.application_composition,
        "default_service_application_composition",
        lambda: composition,
    )
    monkeypatch.setitem(
        sys.modules, "uvicorn", SimpleNamespace(run=run_uvicorn))

    result = service_api.main([
        "serve",
        "--config", str(config_path),
        "--reader-token-file", str(reader_path),
        "--admin-token-file", str(admin_path),
        "--host", "::1",
        "--port", "9443",
        "--job-root", str(job_root),
        "--working-directory", str(working_directory),
        "--output-root", str(output_root),
        "--service-state-root", str(service_state_root),
        "--ready-timeout", "17.5",
        "--reconcile-interval", "23.5",
        "--max-concurrent-searches", "4",
        "--security-profile", "development",
        "--release-security-policy-version", "7",
        "--network-policy", "allow-cloud",
        "--model-download-policy", "allow-reviewed-sync",
        "--llm-cache-namespace", "characterization",
        "--trust-environment-network",
    ])

    assert result == 0
    assert events == [
        ("registry", config_path),
        ("security_policy", {
            "profile": "development",
            "network_policy": "allow-cloud",
            "model_download_policy": "allow-reviewed-sync",
            "cache_namespace": "characterization",
            "trust_environment_network": True,
            "schema_version": 7,
        }),
        ("token", reader_path),
        ("token", admin_path),
        ("credentials", READER_TOKEN, ADMIN_TOKEN),
        ("runtime", registry, {
            "job_root": job_root,
            "working_directory": working_directory,
            "output_root": output_root,
            "service_state_root": service_state_root,
            "ready_timeout_seconds": 17.5,
            "max_concurrent_searches": 4,
            "security_policy": security_policy,
            "runtime_binding": runtime_binding,
            "job_coordination_binding": job_coordination_binding,
        }),
        ("app", runtime, credentials, {
            "http_binding": http_binding,
            "host": "::1",
            "reconcile_interval_seconds": 23.5,
            "max_http_concurrency": 64,
            "enforce_peer_loopback": True,
        }),
        ("uvicorn", app, {
            "host": "::1",
            "port": 9443,
            "workers": 1,
            "access_log": False,
            "proxy_headers": False,
            "server_header": False,
            "timeout_keep_alive": 5,
        }),
    ]


@pytest.mark.parametrize("error_type", [
    OSError,
    service_contracts.ServiceContractError,
    service_runtime.ServiceRuntimeError,
    storage_policy.StoragePolicyError,
    service_api.release_security.ReleaseSecurityError,
])
def test_main_redacts_caught_configuration_errors_before_app_or_uvicorn(
        monkeypatch, tmp_path, capsys, error_type):
    secret = "private-path-and-token-material"
    if error_type is service_contracts.ServiceContractError:
        error = error_type()
    elif error_type is service_runtime.ServiceRuntimeError:
        error = error_type("internal_error")
    else:
        error = error_type(secret)
    error.args = (secret,)
    app_calls = []
    uvicorn_imports = []
    real_import = builtins.__import__

    def fail_registry(_path):
        raise error

    def track_import(name, *args, **kwargs):
        if name == "uvicorn":
            uvicorn_imports.append(name)
        return real_import(name, *args, **kwargs)

    monkeypatch.setattr(
        service_api.service_runtime, "load_corpus_registry", fail_registry)
    monkeypatch.setattr(
        service_api.application_composition,
        "create_service_application",
        lambda *_args, **_kwargs: app_calls.append(True),
    )
    monkeypatch.setattr(builtins, "__import__", track_import)

    with pytest.raises(SystemExit) as raised:
        service_api.main([
            "serve",
            "--config", str(tmp_path / "corpora.json"),
            "--reader-token-file", str(tmp_path / "reader.token"),
            "--admin-token-file", str(tmp_path / "admin.token"),
        ])

    assert raised.value.code == 2
    stderr = capsys.readouterr().err
    assert f"service configuration failed ({error_type.__name__})" in stderr
    assert secret not in stderr
    assert app_calls == []
    assert uvicorn_imports == []


def test_uvicorn_is_lazy_and_missing_dependency_error_is_redacted(
        monkeypatch, tmp_path, capsys):
    uvicorn_imports = []
    real_import = builtins.__import__

    def block_uvicorn(name, *args, **kwargs):
        if name == "uvicorn":
            uvicorn_imports.append(name)
            raise ImportError("private uvicorn import detail")
        return real_import(name, *args, **kwargs)

    monkeypatch.setattr(builtins, "__import__", block_uvicorn)
    monkeypatch.setattr(
        service_api, "initialize_token_files", lambda *_args: None)
    monkeypatch.setattr(
        service_api.application_composition,
        "create_service_application",
        lambda *_args, **_kwargs: pytest.fail(
            "init-tokens must not resolve service composition"),
    )

    assert service_api.main([
        "init-tokens",
        "--reader-token-file", str(tmp_path / "reader.token"),
        "--admin-token-file", str(tmp_path / "admin.token"),
    ]) == 0
    assert uvicorn_imports == []
    capsys.readouterr()

    monkeypatch.setattr(
        service_api.service_runtime, "load_corpus_registry",
        lambda _path: object(),
    )
    monkeypatch.setattr(
        service_api.release_security,
        "ReleaseSecurityPolicy",
        SimpleNamespace(from_values=lambda **_kwargs: object()),
    )
    tokens = iter((READER_TOKEN, ADMIN_TOKEN))
    monkeypatch.setattr(
        service_api, "load_token_file", lambda _path: next(tokens))
    monkeypatch.setattr(
        service_api.application_composition,
        "create_service_application",
        lambda *_args, **_kwargs: object(),
    )

    with pytest.raises(SystemExit) as raised:
        service_api.main([
            "serve",
            "--config", str(tmp_path / "corpora.json"),
            "--reader-token-file", str(tmp_path / "reader.token"),
            "--admin-token-file", str(tmp_path / "admin.token"),
        ])

    assert raised.value.code == 2
    stderr = capsys.readouterr().err
    assert "uvicorn is required by the service dependency profile" in stderr
    assert "private uvicorn import detail" not in stderr
    assert uvicorn_imports == ["uvicorn"]


def test_known_root_failure_is_redacted_before_uvicorn_import(
        monkeypatch, tmp_path, capsys):
    secret = "private composed-runtime detail"
    uvicorn_imports = []
    real_import = builtins.__import__

    def track_import(name, *args, **kwargs):
        if name == "uvicorn":
            uvicorn_imports.append(name)
        return real_import(name, *args, **kwargs)

    monkeypatch.setattr(
        service_api.service_runtime,
        "load_corpus_registry",
        lambda _path: object(),
    )
    monkeypatch.setattr(
        service_api.release_security,
        "ReleaseSecurityPolicy",
        SimpleNamespace(from_values=lambda **_kwargs: object()),
    )
    tokens = iter((READER_TOKEN, ADMIN_TOKEN))
    monkeypatch.setattr(
        service_api, "load_token_file", lambda _path: next(tokens))
    failure = service_runtime.ServiceRuntimeError("service_unavailable")
    failure.args = (secret,)
    monkeypatch.setattr(
        service_api.application_composition,
        "create_service_application",
        lambda *_args, **_kwargs: (_ for _ in ()).throw(failure),
    )
    monkeypatch.setattr(builtins, "__import__", track_import)

    with pytest.raises(SystemExit) as raised:
        service_api.main([
            "serve",
            "--config", str(tmp_path / "corpora.json"),
            "--reader-token-file", str(tmp_path / "reader.token"),
            "--admin-token-file", str(tmp_path / "admin.token"),
        ])

    assert raised.value.code == 2
    stderr = capsys.readouterr().err
    assert "service configuration failed (ServiceRuntimeError)" in stderr
    assert secret not in stderr
    assert uvicorn_imports == []


@pytest.mark.parametrize("failure_type", [RuntimeError, KeyboardInterrupt])
def test_unexpected_root_failures_propagate_without_uvicorn_import(
        monkeypatch, tmp_path, failure_type):
    marker = failure_type("unexpected root marker")
    uvicorn_imports = []
    real_import = builtins.__import__

    def track_import(name, *args, **kwargs):
        if name == "uvicorn":
            uvicorn_imports.append(name)
        return real_import(name, *args, **kwargs)

    monkeypatch.setattr(
        service_api.service_runtime,
        "load_corpus_registry",
        lambda _path: object(),
    )
    monkeypatch.setattr(
        service_api.release_security,
        "ReleaseSecurityPolicy",
        SimpleNamespace(from_values=lambda **_kwargs: object()),
    )
    tokens = iter((READER_TOKEN, ADMIN_TOKEN))
    monkeypatch.setattr(
        service_api, "load_token_file", lambda _path: next(tokens))
    monkeypatch.setattr(
        service_api.application_composition,
        "create_service_application",
        lambda *_args, **_kwargs: (_ for _ in ()).throw(marker),
    )
    monkeypatch.setattr(builtins, "__import__", track_import)

    with pytest.raises(failure_type) as raised:
        service_api.main([
            "serve",
            "--config", str(tmp_path / "corpora.json"),
            "--reader-token-file", str(tmp_path / "reader.token"),
            "--admin-token-file", str(tmp_path / "admin.token"),
        ])

    assert raised.value is marker
    assert uvicorn_imports == []


def test_flat_service_facade_help_smoke():
    completed = subprocess.run(
        [sys.executable, str(Path(service_api.__file__).resolve()), "--help"],
        stdin=subprocess.DEVNULL,
        stdout=subprocess.PIPE,
        stderr=subprocess.PIPE,
        text=True,
        timeout=30,
        check=False,
    )

    assert completed.returncode == 0, completed.stderr
    assert "Authenticated loopback-only RAG service" in completed.stdout
    assert "{init-tokens,serve}" in completed.stdout


def test_malformed_bounded_inputs_never_poison_readiness(tmp_path):
    runtime = _runtime(tmp_path)
    job_id = "a" * 32
    with _client(runtime) as client:
        huge_integer = client.post(
            "/v1/corpora/property/search",
            headers={**READER_AUTH, "Content-Type": "application/json"},
            content=b'{"query":' + b"9" * 5000 + b"}",
        )
        huge_content_length = client.post(
            "/v1/corpora/property/search",
            headers={
                **READER_AUTH,
                "Content-Type": "application/json",
                "Content-Length": "9" * 5000,
            },
            content=b"{}",
        )
        huge_limit = client.get(
            "/v1/jobs?limit=" + "9" * 5000,
            headers=ADMIN_AUTH,
        )
        huge_etag = client.post(
            f"/v1/jobs/{job_id}/cancel",
            headers={**ADMIN_AUTH, "If-Match": (
                '"rag-job-a' + "9" * 5000 + '-r0"')},
        )
        nonascii_confirmation = client.delete(
            f"/v1/jobs/{job_id}",
            headers=[
                (b"Authorization", f"Bearer {ADMIN_TOKEN}".encode("ascii")),
                (b"If-Match", b'"rag-job-a1-r0"'),
                (b"X-Confirm-Job-ID", b"\xff" * 32),
            ],
        )

        assert huge_integer.status_code == 400
        assert huge_content_length.status_code == 413
        assert huge_limit.status_code == 400
        assert huge_etag.status_code == 412
        assert nonascii_confirmation.status_code == 412
        assert runtime.healthy
        assert client.get("/health/ready").status_code == 200


def test_default_embedded_app_rejects_non_loopback_peer_scope(tmp_path):
    runtime = _runtime(tmp_path)
    app = service_api.create_app(
        runtime,
        service_api.ServiceCredentials(READER_TOKEN, ADMIN_TOKEN),
        reconcile_interval_seconds=3600,
    )
    with TestClient(app, base_url="http://127.0.0.1") as client:
        response = client.get("/v1/corpora", headers=READER_AUTH)
    assert response.status_code == 400


def test_http_concurrency_limit_uses_stable_problem_contract(tmp_path):
    entered = threading.Event()
    release = threading.Event()

    def search_runner(config, search_request, request_id):
        entered.set()
        assert release.wait(timeout=5)
        return {
            "schema_version": 1,
            "request_id": request_id,
            "corpus_id": config.corpus_id,
            "backend": "qdrant",
            "requested_mode": search_request.mode,
            "effective_mode": "vector",
            "reranker_applied": False,
            "warnings": [],
            "hits": [],
        }

    runtime = _runtime(tmp_path, search_runner=search_runner)
    app = service_api.create_app(
        runtime,
        service_api.ServiceCredentials(READER_TOKEN, ADMIN_TOKEN),
        reconcile_interval_seconds=3600,
        max_http_concurrency=1,
        enforce_peer_loopback=False,
    )
    try:
        with TestClient(app, base_url="http://127.0.0.1") as client:
            with ThreadPoolExecutor(max_workers=1) as pool:
                first = pool.submit(
                    client.post,
                    "/v1/corpora/property/search",
                    headers=READER_AUTH,
                    json={"query": "minimum contacts"},
                )
                assert entered.wait(timeout=2)
                limited = client.get("/health/live")
                assert limited.status_code == 429
                assert limited.json()["code"] == "concurrency_limited"
                assert limited.headers["Retry-After"] == "1"
                assert limited.headers["Cache-Control"] == "no-store"
                release.set()
                assert first.result(timeout=2).status_code == 200
    finally:
        release.set()


def test_token_creation_removes_exact_file_after_post_write_failure(
        monkeypatch, tmp_path):
    path = tmp_path / "tokens" / "reader.token"
    monkeypatch.setattr(
        service_api.storage_policy, "_fsync_parent_directory",
        lambda _path: (_ for _ in ()).throw(
            OSError("injected parent flush failure")))

    with pytest.raises(OSError, match="parent flush failure"):
        service_api._create_token_file(path, READER_TOKEN)

    assert not path.exists()


def test_token_pair_rollback_preserves_replacement_generation(
        monkeypatch, tmp_path):
    reader_path = tmp_path / "tokens" / "reader.token"
    admin_path = tmp_path / "tokens" / "admin.token"
    real_create = service_api._create_token_file
    calls = 0

    def replace_then_fail(path, token):
        nonlocal calls
        calls += 1
        if calls == 1:
            return real_create(path, token)
        replacement = reader_path.parent / "replacement.token"
        replacement.write_text("replacement-generation", encoding="utf-8")
        os.replace(replacement, reader_path)
        raise OSError("injected admin creation failure")

    monkeypatch.setattr(service_api, "_create_token_file", replace_then_fail)

    with pytest.raises(service_contracts.ServiceContractError):
        service_api.initialize_token_files(reader_path, admin_path)

    assert reader_path.read_text(encoding="utf-8") == "replacement-generation"
    assert not admin_path.exists()


def test_token_loader_rejects_lexical_symlink_before_resolution(tmp_path):
    target = tmp_path / "target.token"
    storage_policy.atomic_write_private_text(target, READER_TOKEN + "\n")
    link = tmp_path / "reader.token"
    try:
        link.symlink_to(target)
    except (OSError, NotImplementedError) as exc:
        pytest.skip(f"symlink creation unavailable: {exc}")

    with pytest.raises(storage_policy.StoragePolicyError):
        service_api.load_token_file(link)


@pytest.mark.parametrize("host", ["0.0.0.0", "localhost", "192.168.1.2"])
def test_nonliteral_loopback_bind_is_rejected(host):
    with pytest.raises(SystemExit):
        service_api._parser().parse_args([
            "serve",
            "--config", "config.json",
            "--reader-token-file", "reader.token",
            "--admin-token-file", "admin.token",
            "--host", host,
        ])
