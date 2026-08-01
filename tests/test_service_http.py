from __future__ import annotations

import hashlib
import json
import pickle
import threading
from concurrent.futures import ThreadPoolExecutor
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
