"""Model-free dispatch-capability misuse and teardown ownership controls.

These tests observe the inert raw engine from the guard harness, not OCR
accuracy, native execution, or an authenticated execution receipt.
"""
from __future__ import annotations

import threading
from types import SimpleNamespace as NS

import pytest

from ocr_engine_guard import EngineAllocationLimit, EngineGuardError, RapidOCREngineGuard
from ocr_execution_receipt import ExecutionRecorder
from ocr_stage_runtime import MAX_CALLS, StageRecorder
from test_ocr_engine_guard import harness as harness, state


def _timed(engine):
    class Reader:
        def _load_engine(self):
            return engine

    recorder = ExecutionRecorder()
    reader = recorder.reader_factory(Reader)()
    return recorder, reader._load_engine()


def _raise(error):
    raise error


def _invoke_stage(recorder, engine, pixels, *, page_number=1):
    return recorder.invoke(engine, pixels, stage="full_page", page_number=page_number)


def test_observer_surrounds_only_raw_dispatch_and_returns_exact_object(harness):
    raw, pixels = harness.raw, harness.pixels
    original, source = state(raw), pixels.copy()
    guard, observed = RapidOCREngineGuard(raw), []

    def observer(dispatch):
        assert raw.calls == 0 and guard._lock.locked()
        assert state(raw) != original
        result = dispatch()
        assert raw.calls == 1 and guard._lock.locked()
        assert state(raw) != original
        observed.append(result)
        return result

    result = guard.invoke_observed(observer, pixels)
    assert result is observed[0] and result.txts == ("ok",)
    assert state(raw) == original and not guard._lock.locked()
    assert harness.np.array_equal(pixels, source)
    assert tuple(len(component.session.calls) for component in original[1]) == (1, 1, 1)


@pytest.mark.parametrize("observer", [None, False, 0, "", object()])
def test_noncallable_observer_never_installs_or_dispatches(harness, observer):
    original = state(harness.raw)
    guard = RapidOCREngineGuard(harness.raw)
    with pytest.raises(EngineGuardError):
        guard.invoke_observed(observer, harness.pixels)
    assert harness.raw.calls == 0 and state(harness.raw) == original
    assert not guard._lock.locked()


@pytest.mark.parametrize("replacement", [None, False, NS()])
def test_omitted_dispatch_cannot_pass_even_with_none_result(harness, replacement):
    saved, original = [], state(harness.raw)
    guard = RapidOCREngineGuard(harness.raw)

    def observer(dispatch):
        saved.append(dispatch)
        return replacement

    with pytest.raises(EngineGuardError):
        guard.invoke_observed(observer, harness.pixels)
    assert harness.raw.calls == 0 and state(harness.raw) == original
    with pytest.raises(EngineGuardError):
        saved[0]()
    assert harness.raw.calls == 0


@pytest.mark.parametrize("suppress_second", [False, True])
def test_dispatch_is_single_use_even_when_observer_catches_rejection(harness, suppress_second):
    original = state(harness.raw)

    def observer(dispatch):
        result = dispatch()
        if suppress_second:
            with pytest.raises(EngineGuardError):
                dispatch()
        else:
            dispatch()
        return result

    with pytest.raises(EngineGuardError):
        RapidOCREngineGuard(harness.raw).invoke_observed(observer, harness.pixels)
    assert harness.raw.calls == 1 and state(harness.raw) == original


def test_equal_but_distinct_raw_result_is_rejected(harness):
    original = state(harness.raw)

    def observer(dispatch):
        result = dispatch()
        replacement = NS(**vars(result))
        assert replacement is not result
        return replacement

    with pytest.raises(EngineGuardError):
        RapidOCREngineGuard(harness.raw).invoke_observed(observer, harness.pixels)
    assert harness.raw.calls == 1 and state(harness.raw) == original


@pytest.mark.parametrize("error_type", [ValueError, RuntimeError, EngineAllocationLimit])
def test_suppressed_raw_failure_is_rethrown_with_exact_identity(harness, error_type):
    error, original = error_type("inert raw failure"), state(harness.raw)
    harness.raw.before_call = lambda: _raise(error)

    def observer(dispatch):
        try:
            return dispatch()
        except Exception as caught:
            assert caught is error
            return None

    with pytest.raises(error_type) as caught:
        RapidOCREngineGuard(harness.raw).invoke_observed(observer, harness.pixels)
    assert caught.value is error and error.__cause__ is not error
    assert harness.raw.calls == 1 and state(harness.raw) == original


@pytest.mark.parametrize("mode", ["success", "error_before", "error_after"])
def test_escaped_dispatch_is_closed_after_observer_returns_or_raises(harness, mode):
    saved, original = [], state(harness.raw)
    error = RuntimeError("inert observer failure")
    guard = RapidOCREngineGuard(harness.raw)

    def observer(dispatch):
        saved.append(dispatch)
        if mode == "error_before":
            raise error
        result = dispatch()
        if mode == "error_after":
            raise error
        return result

    if mode == "success":
        assert guard.invoke_observed(observer, harness.pixels).txts == ("ok",)
    else:
        with pytest.raises(RuntimeError) as caught:
            guard.invoke_observed(observer, harness.pixels)
        assert caught.value is error
    calls = 0 if mode == "error_before" else 1
    assert harness.raw.calls == calls and state(harness.raw) == original
    with pytest.raises(EngineGuardError):
        saved[0]()
    assert harness.raw.calls == calls and state(harness.raw) == original


@pytest.mark.parametrize("error_type", [KeyboardInterrupt, SystemExit])
@pytest.mark.parametrize("reaction", ["suppress", "translate", "reraise"])
def test_observer_cannot_suppress_or_translate_raw_cancellation(harness, error_type, reaction):
    error, original = error_type("inert cancellation"), state(harness.raw)
    translated = RuntimeError("inert translated cancellation")
    harness.raw.before_call = lambda: _raise(error)
    guard = RapidOCREngineGuard(harness.raw)

    def observer(dispatch):
        try:
            return dispatch()
        except BaseException as caught:
            assert caught is error
            if reaction == "translate":
                raise translated
            if reaction == "reraise":
                raise
            return None

    with pytest.raises(error_type) as caught:
        guard.invoke_observed(observer, harness.pixels)
    assert caught.value is error and error.__cause__ is not error
    if reaction == "translate":
        assert error.__cause__ is translated
    assert harness.raw.calls == 1 and state(harness.raw) == original
    assert not guard._lock.locked()


@pytest.mark.parametrize("error_type", [KeyboardInterrupt, SystemExit])
def test_observer_cancellation_without_raw_dispatch_preserves_identity(harness, error_type):
    error, saved = error_type("inert observer cancellation"), []
    original = state(harness.raw)

    def observer(dispatch):
        saved.append(dispatch)
        raise error

    with pytest.raises(error_type) as caught:
        RapidOCREngineGuard(harness.raw).invoke_observed(observer, harness.pixels)
    assert caught.value is error
    assert harness.raw.calls == 0 and state(harness.raw) == original
    with pytest.raises(EngineGuardError):
        saved[0]()
    assert harness.raw.calls == 0


@pytest.mark.parametrize("error_type", [KeyboardInterrupt, SystemExit])
def test_raw_swallowed_role_cancellation_survives_completed_observer(harness, error_type):
    error, original = error_type("inert session cancellation"), state(harness.raw)
    harness.raw.catch_all = True
    harness.raw.text_rec.session.error = error
    completed = []

    def observer(dispatch):
        result = dispatch()
        completed.append(result)
        return result

    with pytest.raises(error_type) as caught:
        RapidOCREngineGuard(harness.raw).invoke_observed(observer, harness.pixels)
    assert caught.value is error and len(completed) == 1
    assert harness.raw.calls == 1 and state(harness.raw) == original


def test_foreign_thread_cannot_dispatch_or_make_missing_call_look_successful(harness):
    original, failures = state(harness.raw), []
    guard = RapidOCREngineGuard(harness.raw)

    def observer(dispatch):
        def foreign():
            try:
                dispatch()
            except BaseException as error:
                failures.append(error)

        worker = threading.Thread(target=foreign, daemon=True)
        worker.start()
        worker.join(timeout=5)
        assert not worker.is_alive()
        # In particular, a refused foreign attempt must not count as a raw
        # dispatch whose result was None: this outer call must also fail.
        return None

    with pytest.raises(EngineGuardError):
        guard.invoke_observed(observer, harness.pixels)
    assert len(failures) == 1 and isinstance(failures[0], EngineGuardError)
    assert harness.raw.calls == 0 and state(harness.raw) == original
    assert not guard._lock.locked()


def test_timed_budget_is_checked_before_guard_preparation(harness, monkeypatch):
    guard = RapidOCREngineGuard(harness.raw)
    recorder, timed = _timed(guard)
    recorder.calls = [{} for _ in range(100)]

    def forbidden_prepare(*args, **kwargs):
        pytest.fail("exhausted call budget entered guard preparation")

    monkeypatch.setattr(RapidOCREngineGuard, "_prepare", forbidden_prepare)
    with pytest.raises(ValueError, match="budget"):
        timed(harness.pixels)
    assert len(recorder.calls) == 100 and harness.raw.calls == 0


def test_stage_budget_is_checked_before_shape_copy_or_guard_setup(harness, monkeypatch):
    guard, recorder = RapidOCREngineGuard(harness.raw), StageRecorder()
    recorder.calls = [{} for _ in range(MAX_CALLS)]

    def forbidden_prepare(*args, **kwargs):
        pytest.fail("exhausted stage budget entered guard preparation")

    monkeypatch.setattr(RapidOCREngineGuard, "_prepare", forbidden_prepare)
    with pytest.raises(ValueError, match="schedule"):
        _invoke_stage(recorder, guard, object())
    assert len(recorder.calls) == MAX_CALLS and not recorder.receipt_calls
    assert harness.raw.calls == 0 and recorder._active is None


@pytest.mark.parametrize("failure", [False, True])
def test_generic_callable_spoof_observer_is_not_trusted(failure):
    result, error = NS(value="synthetic"), RuntimeError("inert callable failure")

    class Generic:
        def __init__(self):
            self.calls = []

        def invoke_observed(self, *args, **kwargs):
            pytest.fail("untrusted observer attribute was invoked")

        def __call__(self, *args, **kwargs):
            self.calls.append((args, kwargs))
            if failure:
                raise error
            return result

    generic = Generic()
    recorder, timed = _timed(generic)
    if failure:
        with pytest.raises(RuntimeError) as caught:
            timed("inert input", option=7)
        assert caught.value is error
    else:
        assert timed("inert input", option=7) is result
    assert generic.calls == [(("inert input",), {"option": 7})]
    assert [call["status"] for call in recorder.calls] == ["failed" if failure else "completed"]


def test_stage_generic_spoof_observer_keeps_existing_role_path(harness):
    harness.raw.invoke_observed = lambda *args, **kwargs: pytest.fail("spoof observer was invoked")
    original, recorder = state(harness.raw), StageRecorder()
    result, call = _invoke_stage(recorder, harness.raw, harness.pixels)
    assert result.txts == ("ok",) and call["status"] == "completed"
    assert all(role == {"attempted": 1, "completed": 1, "failed": 0} for role in call["roles"].values())
    assert len(recorder.calls) == len(recorder.receipt_calls) == harness.raw.calls == 1
    assert state(harness.raw) == original


def test_stage_reserves_active_call_until_every_component_restore_finishes(harness, monkeypatch):
    raw, recorder = harness.raw, StageRecorder()
    original, rec_component = state(raw), raw.text_rec
    guard, reentry = RapidOCREngineGuard(raw), []

    def set_attribute(self, name, value):
        # Guard teardown restores the StageRecorder probe, whereas the later
        # StageRecorder teardown restores this original component identity.
        if name == "text_rec" and value is rec_component and self.calls and not reentry:
            try:
                _invoke_stage(recorder, guard, harness.pixels, page_number=2)
            except BaseException as error:
                reentry.append(error)
            else:
                reentry.append(None)
        object.__setattr__(self, name, value)

    monkeypatch.setattr(harness.Raw, "__setattr__", set_attribute)
    result, call = _invoke_stage(recorder, guard, harness.pixels)
    assert result.txts == ("ok",) and call["status"] == "completed"
    assert len(reentry) == 1 and isinstance(reentry[0], ValueError)
    assert len(recorder.calls) == len(recorder.receipt_calls) == raw.calls == 1
    assert state(raw) == original and recorder._active is None and not recorder._broken


@pytest.mark.parametrize("error_type", [KeyboardInterrupt, SystemExit])
def test_stage_cancellation_wins_over_restore_error_and_poison_prevents_later_call(harness, monkeypatch, error_type):
    raw, recorder = harness.raw, StageRecorder()
    original, rec_component = state(raw), raw.text_rec
    error = error_type("inert raw cancellation")
    raw.before_call = lambda: _raise(error)
    guard, restored = RapidOCREngineGuard(raw), []

    def set_attribute(self, name, value):
        if name in ("text_det", "text_cls", "text_rec") and self.calls:
            if value is rec_component:
                raise RuntimeError("inert stage restore failure")
            if value in original[1]:
                restored.append(name)
        object.__setattr__(self, name, value)

    monkeypatch.setattr(harness.Raw, "__setattr__", set_attribute)
    with pytest.raises(error_type) as caught:
        _invoke_stage(recorder, guard, harness.pixels)
    assert caught.value is error and isinstance(error.__cause__, RuntimeError)
    assert restored == ["text_cls", "text_det"]
    assert recorder._active is None and recorder._broken
    assert [call["status"] for call in recorder.calls] == ["failed"]
    assert [call["status"] for call in recorder.receipt_calls] == ["failed"]
    with pytest.raises(ValueError, match="fresh ownership"):
        _invoke_stage(recorder, guard, harness.pixels, page_number=2)
    assert raw.calls == 1


@pytest.mark.parametrize("error_type", [KeyboardInterrupt, SystemExit])
def test_stage_cleanup_cancellation_keeps_completed_raw_record(harness, monkeypatch, error_type):
    raw, recorder = harness.raw, StageRecorder()
    original, rec_component = state(raw), raw.text_rec
    error = error_type("inert cleanup cancellation")
    guard, restored = RapidOCREngineGuard(raw), []

    def set_attribute(self, name, value):
        if name in ("text_det", "text_cls", "text_rec") and self.calls:
            if value is rec_component:
                raise error
            if value in original[1]:
                restored.append(name)
        object.__setattr__(self, name, value)

    monkeypatch.setattr(harness.Raw, "__setattr__", set_attribute)
    with pytest.raises(error_type) as caught:
        _invoke_stage(recorder, guard, harness.pixels)
    assert caught.value is error
    assert restored == ["text_cls", "text_det"]
    assert recorder._active is None and recorder._broken
    assert [call["status"] for call in recorder.calls] == ["completed"]
    assert [call["status"] for call in recorder.receipt_calls] == ["completed"]
    with pytest.raises(ValueError, match="fresh ownership"):
        _invoke_stage(recorder, guard, harness.pixels, page_number=2)
    assert raw.calls == 1


@pytest.mark.parametrize("error_type", [RuntimeError, KeyboardInterrupt, SystemExit])
@pytest.mark.parametrize("mutate_before_error", [False, True])
def test_stage_partial_probe_install_restores_without_recording_dispatch(
    harness, monkeypatch, error_type, mutate_before_error
):
    raw, recorder = harness.raw, StageRecorder()
    original = state(raw)
    components = dict(zip(("text_det", "text_cls", "text_rec"), original[1]))
    guard, error = RapidOCREngineGuard(raw), error_type("inert probe installation failure")
    triggered, restorations = [], []

    def set_attribute(self, name, value):
        if name == "text_rec" and value is not components[name] and not triggered:
            triggered.append(name)
            if mutate_before_error:
                object.__setattr__(self, name, value)
            raise error
        if triggered and name in components and value is components[name]:
            restorations.append(name)
        object.__setattr__(self, name, value)

    monkeypatch.setattr(harness.Raw, "__setattr__", set_attribute)
    with pytest.raises(error_type) as caught:
        _invoke_stage(recorder, guard, harness.pixels)
    assert caught.value is error
    assert triggered == ["text_rec"]
    assert restorations == ["text_rec", "text_cls", "text_det"]
    assert raw.calls == 0 and not recorder.calls and not recorder.receipt_calls
    assert tuple(len(component.session.calls) for component in original[1]) == (0, 0, 0)
    assert state(raw) == original and recorder._active is None and not recorder._broken
    assert not guard._lock.locked()

    # Ownership is reusable only because every restoration succeeded. This
    # direct second invocation is not an orchestrator swallowing cancellation.
    result, call = _invoke_stage(recorder, guard, harness.pixels, page_number=2)
    assert result.txts == ("ok",) and call["page_number"] == 2
    assert call["id"] == "call-0001" and call["status"] == "completed"
    assert raw.calls == len(recorder.calls) == len(recorder.receipt_calls) == 1
    assert state(raw) == original


@pytest.mark.parametrize("setup_type", [RuntimeError, KeyboardInterrupt])
@pytest.mark.parametrize("restore_type", [RuntimeError, KeyboardInterrupt])
@pytest.mark.parametrize("mutate_before_error", [False, True])
def test_stage_partial_install_with_failed_restore_drains_poison_and_preserves_cancellation(
    harness, monkeypatch, setup_type, restore_type, mutate_before_error
):
    raw, recorder = harness.raw, StageRecorder()
    original = state(raw)
    components = dict(zip(("text_det", "text_cls", "text_rec"), original[1]))
    guard = RapidOCREngineGuard(raw)
    setup_error = setup_type("inert probe installation failure")
    restore_error = restore_type("inert earlier probe restoration failure")
    triggered, restorations = [], []

    def set_attribute(self, name, value):
        if name == "text_rec" and value is not components[name] and not triggered:
            triggered.append(name)
            if mutate_before_error:
                object.__setattr__(self, name, value)
            raise setup_error
        if triggered and name in components and value is components[name]:
            restorations.append(name)
            if name == "text_cls":
                raise restore_error
        object.__setattr__(self, name, value)

    monkeypatch.setattr(harness.Raw, "__setattr__", set_attribute)
    expected_type = KeyboardInterrupt if KeyboardInterrupt in (setup_type, restore_type) else RuntimeError
    with pytest.raises(expected_type) as caught:
        _invoke_stage(recorder, guard, harness.pixels)
    if setup_type is KeyboardInterrupt:
        assert caught.value is setup_error and caught.value.__cause__ is restore_error
    elif restore_type is KeyboardInterrupt:
        assert caught.value is restore_error
    else:
        assert caught.value is not setup_error and caught.value.__cause__ is restore_error
    assert triggered == ["text_rec"]
    assert restorations == ["text_rec", "text_cls", "text_det"]
    assert raw.text_rec is components["text_rec"] and raw.text_det is components["text_det"]
    assert raw.text_cls is not components["text_cls"]
    assert state(raw)[0] == original[0]
    assert tuple(len(component.session.calls) for component in original[1]) == (0, 0, 0)
    assert raw.calls == 0 and not recorder.calls and not recorder.receipt_calls
    assert recorder._active is None and recorder._broken and not guard._lock.locked()
    with pytest.raises(ValueError, match="fresh ownership"):
        _invoke_stage(recorder, guard, harness.pixels, page_number=2)
    assert raw.calls == 0 and restorations == ["text_rec", "text_cls", "text_det"]
