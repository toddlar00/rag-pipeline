import json
import socket
import threading
import time
from urllib import error, request

import pytest

pytest.importorskip("fastapi")
uvicorn = pytest.importorskip("uvicorn")

import job_manager  # noqa: E402
import service_api  # noqa: E402
import service_contracts  # noqa: E402
import service_runtime  # noqa: E402


READER_TOKEN = "r" * 48
ADMIN_TOKEN = "a" * 48


def _launch_starting(store, job_id, *, ready_timeout):
    del ready_timeout
    execution = store.load_execution(job_id)
    summary = store.transition_job(
        job_id, "starting",
        attempt_token=execution.attempt_token,
        expected_revision=execution.revision,
    )
    return job_manager.LaunchResult(
        job_id=job_id,
        status=summary.status,
        attempt_number=summary.attempt_number,
        ready=True,
    )


def _runtime(tmp_path):
    db_path = tmp_path / "qdrant"
    db_path.mkdir(exist_ok=True)
    chunks_path = tmp_path / "chunks.jsonl"
    chunks_path.write_text(
        '{"text":"holding","metadata":{"content_type":"case_opinion"}}\n',
        encoding="utf-8",
    )
    config = service_contracts.CorpusConfig(
        corpus_id="property",
        db_path=db_path.resolve(),
        chunks_path=chunks_path.resolve(),
        collection_name="property",
        embedding_model="service-test-model",
    )

    def search_runner(bound_config, search_request, request_id):
        return {
            "schema_version": 1,
            "request_id": request_id,
            "corpus_id": bound_config.corpus_id,
            "backend": "qdrant",
            "requested_mode": search_request.mode,
            "effective_mode": "vector",
            "reranker_applied": False,
            "warnings": [],
            "hits": [],
        }

    return service_runtime.RagApplicationService(
        {config.corpus_id: config},
        job_root=tmp_path / "jobs",
        working_directory=tmp_path,
        output_root=tmp_path / "output",
        service_state_root=tmp_path / "service-state",
        search_runner=search_runner,
        launcher=_launch_starting,
    )


def _url_request(port, path, *, token=None, payload=None, request_id=None):
    headers = {}
    if token is not None:
        headers["Authorization"] = f"Bearer {token}"
    if request_id is not None:
        headers["X-Request-ID"] = request_id
    data = None
    if payload is not None:
        data = json.dumps(payload).encode("utf-8")
        headers["Content-Type"] = "application/json"
    return request.Request(
        f"http://127.0.0.1:{port}{path}",
        data=data,
        headers=headers,
        method="POST" if data is not None else "GET",
    )


def test_live_uvicorn_loopback_contract_and_graceful_lease_release(tmp_path):
    runtime = _runtime(tmp_path)
    app = service_api.create_app(
        runtime,
        service_api.ServiceCredentials(READER_TOKEN, ADMIN_TOKEN),
        host="127.0.0.1",
        reconcile_interval_seconds=3600,
    )
    listener = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
    listener.setsockopt(socket.SOL_SOCKET, socket.SO_REUSEADDR, 1)
    listener.bind(("127.0.0.1", 0))
    listener.listen(128)
    port = listener.getsockname()[1]
    server = uvicorn.Server(uvicorn.Config(
        app,
        host="127.0.0.1",
        port=port,
        access_log=False,
        proxy_headers=False,
        server_header=False,
        log_level="critical",
        lifespan="on",
    ))
    thread = threading.Thread(
        target=server.run,
        kwargs={"sockets": [listener]},
        name="service-live-test",
        daemon=True,
    )
    thread.start()
    deadline = time.monotonic() + 10
    try:
        while not server.started and thread.is_alive():
            if time.monotonic() >= deadline:
                pytest.fail("live Uvicorn server did not start")
            time.sleep(0.01)
        assert thread.is_alive()

        with request.urlopen(
                _url_request(port, "/health/live"), timeout=5) as response:
            assert response.status == 200
            assert json.load(response) == {"status": "live"}
            assert response.headers["Cache-Control"] == "no-store"
            assert response.headers["X-Content-Type-Options"] == "nosniff"
            assert response.headers.get("Access-Control-Allow-Origin") is None
            assert response.headers.get("Server") is None

        with pytest.raises(error.HTTPError) as unauthorized:
            request.urlopen(_url_request(port, "/v1/corpora"), timeout=5)
        assert unauthorized.value.code == 401
        problem = json.load(unauthorized.value)
        assert problem["code"] == "unauthorized"
        assert "detail" not in problem

        with request.urlopen(
                _url_request(
                    port,
                    "/v1/corpora/property/search",
                    token=READER_TOKEN,
                    payload={"query": "minimum contacts", "mode": "vector"},
                    request_id="live-request-1",
                ),
                timeout=5,
        ) as response:
            payload = json.load(response)
            assert response.status == 200
            assert response.headers["X-Request-ID"] == "live-request-1"
            assert payload["request_id"] == "live-request-1"
            assert payload["corpus_id"] == "property"
            assert payload["hits"] == []
    finally:
        server.should_exit = True
        thread.join(timeout=10)
        try:
            listener.close()
        except OSError:
            pass

    assert not thread.is_alive()
    assert runtime.started is False

    successor = _runtime(tmp_path)
    successor.start()
    successor.close()
