from __future__ import annotations

import asyncio
import hashlib
import json
import logging
import pickle
import threading
from concurrent.futures import ThreadPoolExecutor
from contextvars import ContextVar
from dataclasses import FrozenInstanceError

import pytest

pytest.importorskip("fastapi")

from fastapi.testclient import TestClient

import service_api
import service_http


READER_TOKEN = "r" * 48
ADMIN_TOKEN = "a" * 48
ADMIN_AUTH = {"Authorization": f"Bearer {ADMIN_TOKEN}"}


class AdapterRuntimeError(Exception):
    def __init__(
            self, code="service_unavailable", *, job_id=None, fatal=False):
        self.code = code
        self.job_id = job_id
        self.fatal = fatal
        super().__init__("private adapter failure")


class ReconcileAbort(BaseException):
    pass


class StructuralRuntime:
    def __init__(self):
        self.started = False
        self.closed = False
        self.marked_unhealthy = 0
        self.list_limits = []
        self.list_error = None
        self.start_error = None
        self.close_error = None
        self.reconcile_error = None
        self.reconciled = threading.Event()

    def start(self):
        if self.start_error is not None:
            raise self.start_error
        self.started = True

    def close(self):
        self.closed = True
        if self.close_error is not None:
            raise self.close_error

    def reconcile_jobs(self):
        self.reconciled.set()
        if self.reconcile_error is not None:
            raise self.reconcile_error
        return []

    def mark_unhealthy(self):
        self.marked_unhealthy += 1

    def readiness(self):
        return True

    def list_jobs(self, *, status, limit, cursor):
        self.list_limits.append((status, limit, cursor))
        if self.list_error is not None:
            raise self.list_error
        return {
            "schema_version": 1,
            "items": [],
            "next_cursor": None,
        }


def _binding(*, default=7, maximum=8):
    return service_http.ServiceHttpBinding(
        runtime_error_type=AdapterRuntimeError,
        default_job_page_limit=default,
        max_job_page_limit=maximum,
    )


def _app(runtime, *, binding=None, interval=3600):
    return service_http.create_app(
        runtime,
        service_http.ServiceCredentials(READER_TOKEN, ADMIN_TOKEN),
        http_binding=_binding() if binding is None else binding,
        host="127.0.0.1",
        reconcile_interval_seconds=interval,
        enforce_peer_loopback=False,
    )


def test_facade_exports_are_exact_and_credentials_pickle_through_legacy_path():
    assert service_api.ServiceCredentials is service_http.ServiceCredentials
    assert service_api.require_reader is service_http.require_reader
    assert service_api.require_admin is service_http.require_admin
    assert (
        service_api.service_openapi_document
        is service_http.service_openapi_document)
    assert service_http.ServiceCredentials.__module__ == "service_api"

    credentials = service_http.ServiceCredentials(READER_TOKEN, ADMIN_TOKEN)
    restored = pickle.loads(pickle.dumps(credentials))

    assert type(restored) is service_api.ServiceCredentials
    assert restored == credentials


def test_http_binding_is_frozen_slotted_and_accepts_any_exception_subclass():
    binding = _binding()

    assert not hasattr(binding, "__dict__")
    assert binding.runtime_error_type is AdapterRuntimeError
    with pytest.raises(FrozenInstanceError):
        binding.default_job_page_limit = 4

    for invalid in (AdapterRuntimeError(), BaseException, object, 1):
        with pytest.raises(TypeError, match="Exception type"):
            service_http.ServiceHttpBinding(invalid, 1, 2)


@pytest.mark.parametrize("default,maximum", [
    (True, 2),
    (1, False),
    (0, 2),
    (-1, 2),
    (1.0, 2),
    (1, 2.0),
])
def test_http_binding_rejects_non_positive_or_non_integer_limits(
        default, maximum):
    with pytest.raises(TypeError, match="positive integer"):
        service_http.ServiceHttpBinding(
            AdapterRuntimeError, default, maximum)


def test_http_binding_rejects_default_above_maximum():
    with pytest.raises(ValueError, match="cannot exceed"):
        service_http.ServiceHttpBinding(AdapterRuntimeError, 3, 2)


def test_ready_probe_runs_its_blocking_walk_off_the_event_loop():
    """readiness() touches the filesystem; it must not stall other requests."""
    runtime = StructuralRuntime()
    entered = threading.Event()
    release = threading.Event()

    def blocking_readiness():
        entered.set()
        assert release.wait(timeout=10)
        return True

    runtime.readiness = blocking_readiness
    app = _app(runtime)

    with TestClient(app, base_url="http://127.0.0.1") as client:
        with ThreadPoolExecutor(max_workers=1) as pool:
            pending = pool.submit(client.get, "/health/ready")
            try:
                assert entered.wait(timeout=10)
                live = client.get("/health/live")
                assert live.status_code == 200
                assert live.json() == {"status": "live"}
            finally:
                release.set()
            assert pending.result(timeout=10).status_code == 200


def test_structural_runtime_uses_captured_page_and_error_policy():
    runtime = StructuralRuntime()
    binding = _binding(default=7, maximum=8)
    app = _app(runtime, binding=binding)

    with TestClient(app, base_url="http://127.0.0.1") as client:
        default_page = client.get("/v1/jobs", headers=ADMIN_AUTH)
        maximum_page = client.get("/v1/jobs?limit=8", headers=ADMIN_AUTH)
        too_large = client.get("/v1/jobs?limit=9", headers=ADMIN_AUTH)
        runtime.list_error = AdapterRuntimeError(
            "service_unavailable", job_id="a" * 32)
        mapped_error = client.get("/v1/jobs", headers=ADMIN_AUTH)

    assert runtime.started
    assert runtime.closed
    assert runtime.list_limits == [
        (None, 7, None),
        (None, 8, None),
        (None, 7, None),
    ]
    assert default_page.status_code == 200
    assert maximum_page.status_code == 200
    assert too_large.status_code == 400
    assert mapped_error.status_code == 503
    assert mapped_error.json()["code"] == "service_unavailable"
    assert mapped_error.headers["Location"] == f"/v1/jobs/{'a' * 32}"
    assert "private adapter failure" not in mapped_error.text


def test_facade_create_app_forwards_the_exact_legacy_surface(monkeypatch):
    runtime = object()
    credentials = object()
    app = object()
    observed = {}

    def create_http_app(*args, **kwargs):
        observed.update(args=args, kwargs=kwargs)
        return app

    monkeypatch.setattr(
        service_api.application_composition,
        "create_service_http_app",
        create_http_app,
    )

    result = service_api.create_app(
        runtime,
        credentials,
        host="::1",
        reconcile_interval_seconds=4.5,
        max_http_concurrency=17,
        enforce_peer_loopback=False,
    )

    assert result is app
    assert observed == {
        "args": (runtime, credentials),
        "kwargs": {
            "host": "::1",
            "reconcile_interval_seconds": 4.5,
            "max_http_concurrency": 17,
            "enforce_peer_loopback": False,
        },
    }


def test_openapi_normalized_bytes_retain_the_versioned_contract_digest():
    document = service_http.service_openapi_document()
    normalized = (
        json.dumps(
            document,
            ensure_ascii=False,
            allow_nan=False,
            sort_keys=True,
            separators=(",", ":"),
        ) + "\n").encode("utf-8")

    assert hashlib.sha256(normalized).hexdigest() == (
        "169b09c0a820c72483ff74da3e8a48175371b1c6d5ee9dbb4371f5d1953b02ce")


def test_lifespan_redacts_startup_and_shutdown_failures():
    startup_runtime = StructuralRuntime()
    startup_runtime.start_error = ValueError("private startup detail")

    with pytest.raises(RuntimeError) as raised:
        with TestClient(
                _app(startup_runtime),
                base_url="http://127.0.0.1"):
            pytest.fail("startup failure must prevent the application body")
    assert str(raised.value) == "local service startup failed"
    assert not startup_runtime.closed

    shutdown_runtime = StructuralRuntime()
    shutdown_runtime.close_error = ValueError("private shutdown detail")
    with pytest.raises(RuntimeError) as raised:
        with TestClient(
                _app(shutdown_runtime),
                base_url="http://127.0.0.1") as client:
            assert client.get("/health/live").status_code == 200
    assert str(raised.value) == "local service shutdown failed"
    assert shutdown_runtime.closed


@pytest.mark.parametrize("failure,expected_marks", [
    (AdapterRuntimeError(fatal=False), 0),
    (AdapterRuntimeError(fatal=True), 1),
    (ValueError("private reconcile detail"), 1),
])
def test_reconcile_policy_marks_unhealthy_only_for_fatal_or_unknown_failures(
        failure, expected_marks):
    runtime = StructuralRuntime()
    runtime.reconcile_error = failure

    with TestClient(
            _app(runtime, interval=0.1),
            base_url="http://127.0.0.1"):
        assert runtime.reconciled.wait(timeout=2)

    assert runtime.marked_unhealthy == expected_marks
    assert runtime.closed


def test_reconcile_base_exception_propagates_and_still_closes_runtime():
    runtime = StructuralRuntime()
    marker = ReconcileAbort("base marker")
    runtime.reconcile_error = marker

    with pytest.raises(ReconcileAbort) as raised:
        with TestClient(
                _app(runtime, interval=0.1),
                base_url="http://127.0.0.1"):
            assert runtime.reconciled.wait(timeout=2)

    assert raised.value is marker
    assert runtime.marked_unhealthy == 0
    assert runtime.closed


class ThreadOwnedRuntime(StructuralRuntime):
    def __init__(self):
        super().__init__()
        self.lifecycle_threads = []
        self.owner_state = threading.local()
        self.start_entered = threading.Event()
        self.start_release = threading.Event()
        self.start_release.set()
        self.close_entered = threading.Event()
        self.close_release = threading.Event()
        self.close_release.set()
        self.calls = []

    def start(self):
        self.calls.append("start")
        self.lifecycle_threads.append(threading.get_ident())
        self.owner_state.acquired = True
        self.start_entered.set()
        assert self.start_release.wait(5)
        super().start()

    def close(self):
        self.calls.append("close")
        self.lifecycle_threads.append(threading.get_ident())
        assert self.owner_state.acquired
        self.close_entered.set()
        assert self.close_release.wait(5)
        super().close()


async def _event(event):
    # Do not consume the default executor: the ownership regression deliberately
    # saturates it and must keep the ASGI event loop independently responsive.
    for _ in range(500):
        if event.is_set():
            return
        await asyncio.sleep(0.01)
    pytest.fail("bounded synthetic lifecycle event did not occur")


def test_lifespan_pins_owner_thread_even_when_default_executor_is_busy():
    runtime = ThreadOwnedRuntime()
    app = _app(runtime)
    busy, release = threading.Event(), threading.Event()
    busy_threads = []

    def blocking_default_work():
        busy_threads.append(threading.get_ident())
        busy.set()
        assert release.wait(5)

    async def exercise():
        loop = asyncio.get_running_loop()
        loop.set_default_executor(ThreadPoolExecutor(max_workers=1))
        context = app.router.lifespan_context(app)
        await context.__aenter__()
        worker = asyncio.create_task(asyncio.to_thread(blocking_default_work))
        try:
            await _event(busy)
            # Close must not queue behind the occupied default worker.
            await asyncio.wait_for(context.__aexit__(None, None, None), timeout=1)
            assert runtime.closed and not release.is_set()
        finally:
            release.set()
            await worker

    asyncio.run(exercise())
    assert runtime.calls == ["start", "close"]
    assert runtime.lifecycle_threads[0] == runtime.lifecycle_threads[1]
    assert runtime.lifecycle_threads[0] not in busy_threads
    assert not any(thread.name.startswith("service-lifecycle") for thread in threading.enumerate())


@pytest.mark.parametrize("stage", ["start", "close"])
@pytest.mark.parametrize("repeat_cancel", [False, True])
def test_lifespan_cancellation_drains_owned_operation_and_releases_same_thread(stage, repeat_cancel):
    runtime = ThreadOwnedRuntime()
    app = _app(runtime)
    entered = runtime.start_entered if stage == "start" else runtime.close_entered
    release = runtime.start_release if stage == "start" else runtime.close_release
    release.clear()

    async def exercise():
        context = app.router.lifespan_context(app)
        if stage == "close":
            await context.__aenter__()
        operation = context.__aenter__() if stage == "start" else context.__aexit__(None, None, None)
        task = asyncio.create_task(operation)
        try:
            await _event(entered)
            task.cancel()
            await asyncio.sleep(0.01)
            assert not task.done()
            if repeat_cancel:
                task.cancel()
                await asyncio.sleep(0.01)
                assert not task.done()
        finally:
            release.set()
        with pytest.raises(asyncio.CancelledError):
            await asyncio.wait_for(task, timeout=2)
        assert runtime.closed

    asyncio.run(exercise())
    assert runtime.calls == ["start", "close"]
    assert len(set(runtime.lifecycle_threads)) == 1
    assert not any(thread.name.startswith("service-lifecycle") for thread in threading.enumerate())


@pytest.mark.parametrize("stage", ["start", "close"])
def test_lifespan_preserves_base_exception_identity_without_thread_leak(stage):
    runtime = ThreadOwnedRuntime()
    marker = ReconcileAbort("private lifecycle base marker")
    if stage == "start":
        runtime.start_error = marker
    else:
        runtime.close_error = marker
    app = _app(runtime)

    async def exercise():
        with pytest.raises(ReconcileAbort) as caught:
            async with app.router.lifespan_context(app):
                assert stage == "close"
        assert caught.value is marker

    asyncio.run(exercise())
    assert runtime.calls == (["start"] if stage == "start" else ["start", "close"])
    assert len(set(runtime.lifecycle_threads)) == 1
    assert not any(thread.name.startswith("service-lifecycle") for thread in threading.enumerate())


def test_cancelled_start_that_fails_does_not_fabricate_success_or_close():
    runtime = ThreadOwnedRuntime()
    runtime.start_release.clear()
    runtime.start_error = ValueError("private startup failure after cancellation")
    app = _app(runtime)

    async def exercise():
        context = app.router.lifespan_context(app)
        task = asyncio.create_task(context.__aenter__())
        try:
            await _event(runtime.start_entered)
            task.cancel()
            await asyncio.sleep(0.01)
            assert not task.done()
        finally:
            runtime.start_release.set()
        with pytest.raises(RuntimeError, match="^local service startup failed$"):
            await task

    asyncio.run(exercise())
    assert runtime.calls == ["start"] and not runtime.closed
    assert not any(thread.name.startswith("service-lifecycle") for thread in threading.enumerate())


def test_cancelled_close_failure_retains_sanitized_cleanup_failure_precedence():
    runtime = ThreadOwnedRuntime()
    runtime.close_release.clear()
    runtime.close_error = OSError("private close error")
    app = _app(runtime)

    async def exercise():
        context = app.router.lifespan_context(app)
        await context.__aenter__()
        task = asyncio.create_task(context.__aexit__(None, None, None))
        try:
            await _event(runtime.close_entered)
            task.cancel()
            await asyncio.sleep(0.01)
            assert not task.done()
        finally:
            runtime.close_release.set()
        with pytest.raises(RuntimeError, match="^local service shutdown failed$"):
            await task

    asyncio.run(exercise())
    assert runtime.calls == ["start", "close"] and runtime.closed
    assert not any(thread.name.startswith("service-lifecycle") for thread in threading.enumerate())


def test_owned_lifecycle_retains_per_call_context_variable_propagation():
    runtime = ThreadOwnedRuntime()
    context = ContextVar("synthetic_lifecycle_context", default="unset")
    observed = []
    original_start, original_close = runtime.start, runtime.close

    def start():
        observed.append(context.get())
        original_start()

    def close():
        observed.append(context.get())
        original_close()

    runtime.start, runtime.close = start, close
    app = _app(runtime)

    async def exercise():
        context.set("startup")
        async with app.router.lifespan_context(app):
            context.set("shutdown")

    asyncio.run(exercise())
    assert observed == ["startup", "shutdown"]
    assert len(set(runtime.lifecycle_threads)) == 1


def test_cancelled_real_service_start_releases_instance_lease_for_successor(tmp_path):
    # The original bug occurred in the canonical PathLease, not merely in a
    # mock lifecycle. These files contain generated-only text and no index.
    import service_contracts
    import service_runtime

    chunks = tmp_path / "synthetic.jsonl"
    chunks.write_text('{"text":"Synthetic text","metadata":{}}\n', encoding="utf-8")
    database = tmp_path / "qdrant"
    database.mkdir()
    config = service_contracts.CorpusConfig(corpus_id="synthetic", db_path=database,
        chunks_path=chunks, collection_name="synthetic", embedding_model="synthetic-no-model")

    def create():
        return service_runtime.RagApplicationService({"synthetic": config},
            working_directory=tmp_path, output_root=tmp_path / "output",
            job_root=tmp_path / "jobs", service_state_root=tmp_path / "state")

    runtime = create()
    entered, release = threading.Event(), threading.Event()
    original_start = runtime.start

    def blocked_start():
        original_start()
        entered.set()
        assert release.wait(5)

    runtime.start = blocked_start
    binding = service_http.ServiceHttpBinding(service_runtime.ServiceRuntimeError, 20, 100)
    app = service_http.create_app(runtime, service_http.ServiceCredentials(READER_TOKEN, ADMIN_TOKEN),
                                  http_binding=binding, enforce_peer_loopback=False)

    async def exercise():
        context = app.router.lifespan_context(app)
        task = asyncio.create_task(context.__aenter__())
        try:
            await _event(entered)
            assert runtime.started
            task.cancel()
            await asyncio.sleep(0.01)
            assert not task.done()
        finally:
            release.set()
        with pytest.raises(asyncio.CancelledError):
            await task

    asyncio.run(exercise())
    assert runtime.started is False and runtime._instance_lease is None
    successor = create()
    successor.start()
    successor.close()


# FastAPI 0.142 instruments every app by default and, at lifespan startup, adds
# OTLP exporters selected by OTEL_* environment variables. The service turns
# all of it off; these variables must therefore change nothing.
_FASTAPI_TELEMETRY_OFF = {
    "auto_configure": False,
    "tracing": False,
    "metrics": False,
    "logs": False,
    "operation_spans": False,
}
_OTEL_EXPORT_ENVIRONMENT = {
    "OTEL_EXPORTER_OTLP_ENDPOINT": "http://127.0.0.1:9",
    "OTEL_EXPORTER_OTLP_TRACES_ENDPOINT": "http://127.0.0.1:9/v1/traces",
    "OTEL_EXPORTER_OTLP_METRICS_ENDPOINT": "http://127.0.0.1:9/v1/metrics",
    "OTEL_EXPORTER_OTLP_LOGS_ENDPOINT": "http://127.0.0.1:9/v1/logs",
}


def _otel_global_providers():
    from opentelemetry import _logs, metrics, trace

    return (
        trace.get_tracer_provider(),
        metrics.get_meter_provider(),
        _logs.get_logger_provider(),
    )


def test_service_app_is_constructed_with_fastapi_telemetry_off(monkeypatch):
    observed = []

    class RecordingFastAPI(service_http.FastAPI):
        def __init__(self, **kwargs):
            observed.append(kwargs.get("telemetry"))
            super().__init__(**kwargs)

    monkeypatch.setattr(service_http, "FastAPI", RecordingFastAPI)

    _app(StructuralRuntime())

    assert observed == [_FASTAPI_TELEMETRY_OFF]


def test_service_lifespan_does_not_configure_export_from_otel_environment(
        monkeypatch, caplog):
    for name, value in _OTEL_EXPORT_ENVIRONMENT.items():
        monkeypatch.setenv(name, value)
    for name in (
            "OTEL_SDK_DISABLED", "OTEL_PYTHON_TRACER_PROVIDER",
            "OTEL_PYTHON_METER_PROVIDER", "OTEL_PYTHON_LOGGER_PROVIDER"):
        monkeypatch.delenv(name, raising=False)
    providers = _otel_global_providers()
    runtime = StructuralRuntime()

    with caplog.at_level(logging.DEBUG, logger="fastapi"):
        with TestClient(_app(runtime), base_url="http://127.0.0.1") as client:
            assert client.get("/health/live").status_code == 200

    assert runtime.started and runtime.closed
    # No automatic-configuration attempt (it would warn, or install exporters
    # if an OTLP exporter distribution were ever present).
    assert [record.getMessage() for record in caplog.records
            if record.name.startswith("fastapi")] == []
    assert all(
        after is before
        for after, before in zip(_otel_global_providers(), providers))


def test_service_requests_never_resolve_ambient_otel_providers(monkeypatch):
    # A default FastAPI 0.142 app resolves the global providers on every
    # request, so an unloadable OTEL_PYTHON_*_PROVIDER fails each one.
    for signal in ("TRACER", "METER", "LOGGER"):
        monkeypatch.setenv(
            f"OTEL_PYTHON_{signal}_PROVIDER", "rag_pipeline_missing_provider")
    runtime = StructuralRuntime()

    with TestClient(_app(runtime), base_url="http://127.0.0.1") as client:
        live = client.get("/health/live")
        unauthenticated = client.get("/v1/jobs")

    assert live.status_code == 200
    assert live.json() == {"status": "live"}
    assert unauthenticated.status_code == 401
