"""Coordinator preview ownership with inert controllers and thread handles.

Source/request preflight is real, using the existing synthetic non-PDF fixture.
No image library, PDF decoder, worker, browser, native OCR or model is executed
here. A dependency-free image stand-in only identifies the delegated return
value; raster validation and containment belong to the separate controller
tests, not these coordinator controls.
"""

from contextlib import contextmanager
import hashlib
import json
import threading
from types import SimpleNamespace as NS

import pytest

import ocr_crop_preview_supervision as preview_backend
import ocr_crop_review_runtime as crop_runtime
import ocr_recovery
import ocr_review_execution as execution
from ocr_review_runtime import ReviewWorkspace
from test_ocr_detection_disposition_io import case as case, FailedReader


def _lock_free(coordinator):
    acquired = coordinator._lock.acquire(blocking=False)
    assert acquired, "external operation ran under the coordinator lock"
    coordinator._lock.release()


class InertImage:
    mode = "RGB"
    size = (2, 1)

    def close(self):
        pass


class InertPreview:
    def __init__(self, workspace):
        self.workspace = workspace
        self.private_root = workspace.output_dir / "owned-preview-staging"
        self.cleanup_uncertain = False
        self.coordinator = None
        self.image = InertImage()
        self.events = []
        self.on_render = None
        self.on_request_close = None
        self.on_close = None

    def render(self, scope, *, cancel_requested=None, preview_profile):
        _lock_free(self.coordinator)
        assert type(preview_profile) is str
        assert preview_profile in ("fit", "dpi288", "dpi576")
        self.events.append(("render", scope, cancel_requested, preview_profile))
        if self.on_render is not None:
            return self.on_render(scope, cancel_requested)
        return self.image

    def request_close(self):
        _lock_free(self.coordinator)
        self.events.append(("preview-cancel",))
        if self.on_request_close is not None:
            return self.on_request_close()

    def close(self, *, timeout_seconds):
        _lock_free(self.coordinator)
        self.events.append(("preview-close", timeout_seconds))
        if self.on_close is not None:
            return self.on_close(timeout_seconds)
        return True


@pytest.fixture
def preview_host(case, tmp_path, monkeypatch):
    controllers = []

    def controller(workspace):
        result = InertPreview(workspace)
        controllers.append(result)
        return result

    def forbidden(*_args, **_kwargs):
        pytest.fail("coordinator used an in-process render or real worker")

    monkeypatch.setattr(preview_backend, "CropPreviewController", controller)
    monkeypatch.setattr(crop_runtime, "render_crop_scope", forbidden)
    monkeypatch.setattr(execution.process_supervision, "_run_cli_with_deadline", forbidden)
    baseline = ocr_recovery.build_recovery_report(FailedReader(case.source),
        source_sha256=hashlib.sha256(case.source.read_bytes()).hexdigest(),
        policy=ocr_recovery.RetryPolicy(), requested_pages=(1, 3))
    baseline["evidence_sha256"] = None
    recovery_path = tmp_path / "recovery.json"
    recovery_path.write_text(json.dumps(baseline), encoding="utf-8")
    workspace = ReviewWorkspace(case.source, recovery_path, tmp_path)
    monkeypatch.setattr(workspace, "render", forbidden)
    coordinator = execution.ReviewRunCoordinator(workspace)
    preview = controllers[0]
    preview.coordinator = coordinator
    # This declared input is only an argument-identity control. The inert
    # controller does not claim to validate physical PDF geometry.
    scope = {"source_sha256": workspace.document.source_sha256, "page_number": 1}
    result = NS(coordinator=coordinator, workspace=workspace, preview=preview,
                controllers=controllers, scope=scope)
    yield result
    # No real thread or private preview directory exists in this fixture.
    coordinator._runs.clear()
    coordinator._active = None
    preview.on_close = preview.on_request_close = None
    coordinator.close()
    preview.image.close()


@contextmanager
def _error(code):
    with pytest.raises(crop_runtime.CropPreviewError,
                       match="original crop preview unavailable or inputs changed") as caught:
        yield caught
    assert caught.value.code == code


@pytest.mark.parametrize("profile", [None, "fit", "dpi288", "dpi576"])
def test_one_fixed_controller_owns_preview_and_private_root(preview_host, profile):
    host = preview_host
    source = host.workspace.pdf_path.read_bytes()
    recovery = host.workspace.recovery_path.read_bytes()
    assert len(host.controllers) == 1
    assert host.preview.workspace is host.workspace
    assert host.coordinator.preview_private_root == host.preview.private_root
    options = {} if profile is None else {"preview_profile": profile}
    image = host.coordinator.render_crop_preview(host.scope, **options)
    assert image is host.preview.image
    assert image.mode == "RGB" and image.size == (2, 1)
    assert host.preview.events[0][1] is host.scope
    assert host.preview.events[0][3] == ("fit" if profile is None else profile)
    assert host.workspace.pdf_path.read_bytes() == source
    assert host.workspace.recovery_path.read_bytes() == recovery
    assert not host.preview.private_root.exists()


@pytest.mark.parametrize("failure_type", [RuntimeError, KeyboardInterrupt])
def test_controller_construction_failure_cannot_choose_a_local_fallback(preview_host, monkeypatch, failure_type):
    host = preview_host
    failure = failure_type("synthetic controller construction failure")
    calls = []

    def construct(workspace):
        assert workspace is host.workspace
        calls.append(workspace)
        raise failure

    monkeypatch.setattr(preview_backend, "CropPreviewController", construct)
    with pytest.raises(failure_type) as caught:
        execution.ReviewRunCoordinator(host.workspace)
    assert caught.value is failure and calls == [host.workspace]
    assert host.preview.events == []


@pytest.mark.parametrize("value", [False, True, 0, 1, "cancel", [], {}])
def test_noncallable_cancellation_is_refused_before_controller(preview_host, value):
    with _error("preview_unavailable") as caught:
        preview_host.coordinator.render_crop_preview(preview_host.scope, cancel_requested=value)
    assert caught.value.code == "preview_unavailable"
    assert preview_host.preview.events == []


def test_precancel_is_refused_without_controller_or_lock(preview_host):
    host = preview_host
    calls = []

    def cancelled():
        _lock_free(host.coordinator)
        calls.append(True)
        return True

    with _error("preview_cancelled") as caught:
        host.coordinator.render_crop_preview(host.scope, cancel_requested=cancelled)
    assert caught.value.code == "preview_cancelled"
    assert calls == [True] and host.preview.events == []


def test_wrapped_callback_runs_without_lock_before_during_and_after_render(preview_host):
    host = preview_host
    calls = []

    def cancelled():
        _lock_free(host.coordinator)
        calls.append(True)
        return False

    def render(scope, callback):
        assert scope is host.scope and callback is not cancelled
        assert callback() is False
        return host.preview.image

    host.preview.on_render = render
    assert host.coordinator.render_crop_preview(host.scope, cancel_requested=cancelled) is host.preview.image
    assert len(calls) == 3


@pytest.mark.parametrize("where", ["callback", "backend"])
@pytest.mark.parametrize("failure", [RuntimeError, ValueError])
def test_ordinary_external_errors_are_static_and_never_fall_back(preview_host, where, failure):
    host = preview_host

    def fail(*_args):
        _lock_free(host.coordinator)
        raise failure("PRIVATE exception details")

    options = {"cancel_requested": fail} if where == "callback" else {}
    if where == "backend":
        host.preview.on_render = fail
    with _error("preview_unavailable") as caught:
        host.coordinator.render_crop_preview(host.scope, **options)
    assert caught.value.code == "preview_unavailable"
    assert "PRIVATE" not in str(caught.value)
    assert caught.value.__cause__ is None and caught.value.__suppress_context__
    assert sum(event[0] == "render" for event in host.preview.events) == int(where == "backend")


@pytest.mark.parametrize("where", ["during", "after"])
def test_late_callback_failure_discards_preview_without_exposing_details(preview_host, where):
    host = preview_host
    calls = []

    def cancelled():
        _lock_free(host.coordinator)
        calls.append(True)
        if len(calls) == 2:
            raise RuntimeError("PRIVATE late callback failure")
        return False

    def render(_scope, callback):
        if where == "during":
            callback()
        return host.preview.image

    host.preview.on_render = render
    with _error("preview_unavailable") as caught:
        host.coordinator.render_crop_preview(host.scope, cancel_requested=cancelled)
    assert caught.value.code == "preview_unavailable"
    assert "PRIVATE" not in str(caught.value)
    assert len(calls) == 2 and len(host.preview.events) == 1


def test_expected_preview_error_is_rebuilt_with_only_its_fixed_code(preview_host):
    failure = crop_runtime.CropPreviewError(code="preview_timeout")
    failure.args = ("PRIVATE altered error message",)

    def render(*_args):
        raise failure

    preview_host.preview.on_render = render
    with _error("preview_timeout") as caught:
        preview_host.coordinator.render_crop_preview(preview_host.scope)
    assert caught.value is not failure and caught.value.code == "preview_timeout"
    assert caught.value.__cause__ is None and caught.value.__suppress_context__


@pytest.mark.parametrize("where", ["callback", "backend"])
def test_keyboard_interrupt_propagates_without_fallback(preview_host, where):
    failure = KeyboardInterrupt("synthetic cancellation")

    def fail(*_args):
        raise failure

    options = {"cancel_requested": fail} if where == "callback" else {}
    if where == "backend":
        preview_host.preview.on_render = fail
    with pytest.raises(KeyboardInterrupt) as caught:
        preview_host.coordinator.render_crop_preview(preview_host.scope, **options)
    assert caught.value is failure


@pytest.mark.parametrize("change", ["external", "closed", "coordinator_uncertain"])
def test_changed_cancellation_refuses_even_a_returned_image(preview_host, change):
    host = preview_host
    external = threading.Event()

    def render(_scope, callback):
        assert callback() is False
        if change == "external":
            external.set()
        elif change == "closed":
            host.coordinator._closed = True
        else:
            host.coordinator._cleanup_uncertain = True
        assert callback() is True
        return host.preview.image

    host.preview.on_render = render
    with _error("preview_cancelled") as caught:
        host.coordinator.render_crop_preview(host.scope, cancel_requested=external.is_set)
    assert caught.value.code == "preview_cancelled"
    assert len(host.preview.events) == 1


@pytest.mark.parametrize("outcome", ["returns", "raises", "interrupts"])
def test_uncertain_render_latches_and_revokes_ocr_consent(preview_host, outcome):
    host = preview_host
    prepared = host.coordinator.prepare(pages=[1])

    def render(*_args):
        host.preview.cleanup_uncertain = True
        if outcome == "raises":
            raise crop_runtime.CropPreviewError(code="cleanup_unconfirmed")
        if outcome == "interrupts":
            raise KeyboardInterrupt("synthetic cancellation")
        return host.preview.image

    host.preview.on_render = render
    expected = KeyboardInterrupt if outcome == "interrupts" else crop_runtime.CropPreviewError
    with pytest.raises(expected) as caught:
        host.coordinator.render_crop_preview(host.scope)
    if outcome != "interrupts":
        assert caught.value.code == "cleanup_unconfirmed"
    assert host.coordinator._cleanup_uncertain is True and host.coordinator._pending is None
    # Clearing the controller property cannot revive the coordinator latch.
    host.preview.cleanup_uncertain = False
    with pytest.raises(ValueError):
        host.coordinator.prepare(pages=[1])
    with pytest.raises(ValueError):
        host.coordinator.start(prepared["intent_id"], prepared["approval_token"], confirmed=True)
    assert not host.coordinator._runs


@pytest.mark.parametrize("state", ["closed", "coordinator_uncertain", "preview_uncertain"])
def test_initial_closed_or_uncertain_state_refuses_preview_and_ocr(preview_host, state):
    host = preview_host
    prepared = host.coordinator.prepare(pages=[1])
    if state == "closed":
        host.coordinator.close()
        host.preview.events.clear()
    elif state == "coordinator_uncertain":
        host.coordinator._cleanup_uncertain = True
    else:
        host.preview.cleanup_uncertain = True
    with _error("preview_cancelled" if state == "closed" else "cleanup_unconfirmed") as caught:
        host.coordinator.render_crop_preview(host.scope)
    assert caught.value.code == ("preview_cancelled" if state == "closed" else "cleanup_unconfirmed")
    assert host.preview.events == []
    with pytest.raises(ValueError):
        host.coordinator.prepare(pages=[1])
    with pytest.raises(ValueError):
        host.coordinator.start(prepared["intent_id"], prepared["approval_token"], confirmed=True)
    assert not host.coordinator._runs


class InertThread:
    def __init__(self, host, name, *, cost=0., clock=None, failure=None, remains_alive=False):
        self.host, self.name = host, name
        self.cost, self.clock, self.failure = cost, clock, failure
        self.remains_alive = remains_alive

    def join(self, timeout):
        _lock_free(self.host.coordinator)
        self.host.preview.events.append((self.name, timeout))
        if self.clock is not None:
            self.clock.now += self.cost
        if self.failure is not None:
            raise self.failure("synthetic join failure")

    def is_alive(self):
        return self.remains_alive


def _owned_run(host, name, *, active=False, uncertain=False, **thread_options):
    thread = InertThread(host, name, **thread_options)
    run = NS(thread=thread, start_uncertain=uncertain, cancel=threading.Event())
    host.coordinator._runs[name] = run
    if active:
        host.coordinator._active = name
    return run


def test_close_requests_both_owners_before_join_and_holds_no_external_lock(preview_host):
    host = preview_host
    host.coordinator.prepare(pages=[1])
    run = _owned_run(host, "ocr-join", active=True)

    def request():
        assert run.cancel.is_set()
        assert host.coordinator._closed and host.coordinator._pending is None
        assert [event[0] for event in host.preview.events] == ["preview-cancel"]

    host.preview.on_request_close = request
    result = host.coordinator.close()
    assert result == {"closed": True, "cleanup_confirmed": True, "notice": "closed"}
    assert [event[0] for event in host.preview.events] == ["preview-cancel", "ocr-join", "preview-close"]


def test_close_races_an_active_inert_render_without_deadlock_or_image_acceptance(preview_host):
    host = preview_host
    entered, release = threading.Event(), threading.Event()
    outcomes = []

    def render(_scope, callback):
        entered.set()
        assert release.wait(5)
        assert callback() is True
        return host.preview.image

    def render_call():
        try:
            outcomes.append(host.coordinator.render_crop_preview(host.scope))
        except BaseException as failure:
            outcomes.append(failure)

    thread = threading.Thread(target=render_call, daemon=True)

    def request_close():
        assert host.coordinator._closed is True
        release.set()

    def close(timeout):
        assert 0 <= timeout <= execution.CLOSE_TIMEOUT_SECONDS
        thread.join(min(timeout, 5))
        return not thread.is_alive()

    host.preview.on_render = render
    host.preview.on_request_close = request_close
    host.preview.on_close = close
    thread.start()
    try:
        assert entered.wait(5)
        assert host.coordinator.close()["cleanup_confirmed"] is True
    finally:
        release.set()
        thread.join(5)
    assert not thread.is_alive()
    assert len(outcomes) == 1 and isinstance(outcomes[0], crop_runtime.CropPreviewError)
    assert outcomes[0].code == "preview_cancelled"
    assert [event[0] for event in host.preview.events] == ["render", "preview-cancel", "preview-close"]


@pytest.mark.parametrize("fault", ["request", "close", "join", "alive", "false", "truthy"])
def test_close_attempts_both_owners_on_ordinary_failure_and_latches(preview_host, fault):
    host = preview_host
    run = _owned_run(host, "ocr-join", active=True,
                     failure=RuntimeError if fault == "join" else None, remains_alive=fault == "alive")

    def fail(*_args):
        raise RuntimeError("PRIVATE cleanup error")

    if fault == "request":
        host.preview.on_request_close = fail
    elif fault == "close":
        host.preview.on_close = fail
    elif fault in ("false", "truthy"):
        host.preview.on_close = lambda _timeout: False if fault == "false" else 1
    result = host.coordinator.close()
    assert run.cancel.is_set()
    assert [event[0] for event in host.preview.events] == ["preview-cancel", "ocr-join", "preview-close"]
    assert result == {"closed": True, "cleanup_confirmed": False, "notice": "cleanup_unconfirmed"}
    assert "PRIVATE" not in json.dumps(result)
    host.preview.on_request_close = host.preview.on_close = None
    host.coordinator._runs.clear()
    host.coordinator._active = None
    assert host.coordinator.close()["cleanup_confirmed"] is False
    with pytest.raises(ValueError):
        host.coordinator.prepare(pages=[1])


@pytest.mark.parametrize("costs, expected", [((3., 7., 11.), [37., 30., 19.]),
                                           ((45., 0., 0.), [0., 0., 0.])])
def test_close_shares_one_deadline_across_request_all_joins_and_preview(preview_host, monkeypatch, costs, expected):
    host = preview_host
    clock = NS(now=100.)
    monkeypatch.setattr(execution.time, "monotonic", lambda: clock.now)
    monkeypatch.setattr(execution, "CLOSE_TIMEOUT_SECONDS", 40.)

    def request():
        clock.now += costs[0]

    host.preview.on_request_close = request
    _owned_run(host, "active-join", active=True, clock=clock, cost=costs[1])
    _owned_run(host, "uncertain-start-join", uncertain=True, clock=clock, cost=costs[2])
    _owned_run(host, "terminal-not-joined")
    assert host.coordinator.close()["cleanup_confirmed"] is True
    assert [event[0] for event in host.preview.events] == [
        "preview-cancel", "active-join", "uncertain-start-join", "preview-close"]
    assert [event[1] for event in host.preview.events[1:]] == expected


@pytest.mark.parametrize("where", ["request", "close"])
def test_close_keyboard_interrupt_remains_an_interruption(preview_host, where):
    host = preview_host
    failure = KeyboardInterrupt("synthetic close interruption")
    run = _owned_run(host, "ocr-join", active=True)

    def interrupt(*_args):
        raise failure

    if where == "request":
        host.preview.on_request_close = interrupt
    else:
        host.preview.on_close = interrupt
    with pytest.raises(KeyboardInterrupt) as caught:
        host.coordinator.close()
    assert caught.value is failure
    assert host.coordinator._closed and run.cancel.is_set()
    assert host.coordinator._pending is None
