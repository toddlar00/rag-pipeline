"""Raw-dispatch accounting regressions through existing supported recorder APIs.

The real allocation guard and recorders wrap inert components. No PDF renderer,
model constructor, model download or native OCR invocation is used here.
"""

from types import SimpleNamespace

import pytest

from ocr_engine_guard import EngineGuardError, RapidOCREngineGuard
from ocr_execution_receipt import ExecutionRecorder
from ocr_stage_runtime import StageRecorder, collect_stage_observation
import ocr_stage_diagnostics as policy
from test_ocr_engine_guard import harness as harness, state as hook_state
from test_ocr_stage_runtime import Reader as SyntheticStageReader, SOURCE, REFERENCE, reference


def _stack(harness, topology):
    guard = RapidOCREngineGuard(harness.raw)
    if topology == "timed":
        class Reader:
            def _load_engine(self):
                return getattr(self, "_engine", guard)

        recorder = ExecutionRecorder()
        timed = recorder.reader_factory(Reader)()._load_engine()

        def invoke():
            return timed(harness.pixels)

        def prepare():
            return timed.prepare(harness.pixels)

        receipts = recorder.calls
    else:
        recorder = StageRecorder()

        def invoke():
            return recorder.invoke(guard, harness.pixels, stage="full_page", page_number=1)

        def prepare():
            return guard.prepare(harness.pixels, use_det=True, use_cls=True, use_rec=True)

        receipts = recorder.receipt_calls
    return SimpleNamespace(guard=guard, recorder=recorder, invoke=invoke, prepare=prepare,
                           receipts=receipts, topology=topology)


def _session_counts(harness):
    return {role: len(getattr(harness.raw, attribute).session.calls) for role, attribute in (
        ("detection", "text_det"), ("classification", "text_cls"), ("recognition", "text_rec"))}


def _assert_calls(stack, statuses):
    expected = [(f"call-{number:04d}", status) for number, status in enumerate(statuses, 1)]
    assert [(call["id"], call["status"]) for call in stack.recorder.calls] == expected
    assert [(call["id"], call["status"]) for call in stack.receipts] == expected
    assert all(call["elapsed_seconds"] >= 0 for call in stack.receipts)


def _assert_completed_roles(stack, index):
    if stack.topology == "stage":
        assert stack.recorder.calls[index]["roles"] == {
            role: {"attempted": 1, "completed": 1, "failed": 0}
            for role in ("detection", "classification", "recognition")}


@pytest.mark.parametrize("topology", ["timed", "stage"])
@pytest.mark.parametrize("error_type", [RuntimeError, KeyboardInterrupt])
@pytest.mark.parametrize("after_write", [False, True])
def test_partial_guard_installation_never_manufactures_dispatch_and_peer_keeps_first_id(
        harness, monkeypatch, topology, error_type, after_write):
    stack = _stack(harness, topology)
    before = hook_state(harness.raw)
    stack.prepare()
    failure, fired = error_type("synthetic setup failure"), []

    def setter(self, name, value):
        if name == "crop_text_regions" and not fired:
            fired.append(True)
            if after_write:
                object.__setattr__(self, name, value)
            raise failure
        object.__setattr__(self, name, value)

    monkeypatch.setattr(harness.Raw, "__setattr__", setter)
    with pytest.raises(error_type) as caught:
        stack.invoke()
    assert caught.value is failure
    assert harness.raw.calls == 0 and _session_counts(harness) == dict.fromkeys(_session_counts(harness), 0)
    assert hook_state(harness.raw) == before and not stack.guard._lock.locked()
    _assert_calls(stack, [])
    stack.prepare()
    stack.invoke()
    assert harness.raw.calls == 1 and _session_counts(harness) == dict.fromkeys(_session_counts(harness), 1)
    _assert_calls(stack, ["completed"])
    _assert_completed_roles(stack, 0)
    assert hook_state(harness.raw) == before


@pytest.mark.parametrize("topology", ["timed", "stage"])
@pytest.mark.parametrize("setting", ["global", "recognition"])
def test_repeated_prepare_rejects_changed_configuration_without_a_dispatch(harness, topology, setting):
    stack = _stack(harness, topology)
    stack.prepare()
    owner, name = ((harness.raw, "min_height") if setting == "global"
                   else (harness.raw.text_rec, "rec_batch_num"))
    previous = getattr(owner, name)
    setattr(owner, name, True)
    with pytest.raises(EngineGuardError):
        stack.invoke()
    assert harness.raw.calls == 0 and all(count == 0 for count in _session_counts(harness).values())
    _assert_calls(stack, [])
    setattr(owner, name, previous)
    stack.prepare()
    stack.invoke()
    _assert_calls(stack, ["completed"])
    _assert_completed_roles(stack, 0)


@pytest.mark.parametrize("topology", ["timed", "stage"])
@pytest.mark.parametrize("error_type", [RuntimeError, KeyboardInterrupt])
@pytest.mark.parametrize("location", ["raw", "detection", "classification", "recognition"])
def test_actual_raw_failure_records_one_failed_dispatch_and_later_peer_is_distinct(
        harness, topology, error_type, location):
    stack = _stack(harness, topology)
    before = hook_state(harness.raw)
    failure = error_type("synthetic dispatch failure")
    if location == "raw":
        def fail():
            raise failure
        harness.raw.before_call = fail
    else:
        component = getattr(harness.raw, {"detection": "text_det", "classification": "text_cls",
                                        "recognition": "text_rec"}[location])
        component.session.error = failure
    stack.prepare()
    with pytest.raises(error_type) as caught:
        stack.invoke()
    assert caught.value is failure and harness.raw.calls == 1
    _assert_calls(stack, ["failed"])
    roles = ("detection", "classification", "recognition")
    failed_offset = -1 if location == "raw" else roles.index(location)
    assert _session_counts(harness) == {role: int(index <= failed_offset) for index, role in enumerate(roles)}
    if topology == "stage":
        assert stack.recorder.calls[0]["roles"] == {
            role: {"attempted": int(index <= failed_offset), "completed": int(index < failed_offset),
                   "failed": int(index == failed_offset)} for index, role in enumerate(roles)}
    assert hook_state(harness.raw) == before and not stack.guard._lock.locked()
    harness.raw.before_call = None
    if location != "raw":
        component.session.error = None
    stack.prepare()
    stack.invoke()
    assert harness.raw.calls == 2
    _assert_calls(stack, ["failed", "completed"])
    _assert_completed_roles(stack, 1)
    assert _session_counts(harness) == {role: 1 + int(index <= failed_offset) for index, role in enumerate(roles)}


@pytest.mark.parametrize("topology", ["timed", "stage"])
@pytest.mark.parametrize("cleanup_type", [RuntimeError, KeyboardInterrupt])
def test_completed_raw_dispatch_stays_completed_when_guard_cleanup_rejects_result(
        harness, monkeypatch, topology, cleanup_type):
    stack = _stack(harness, topology)
    failure = cleanup_type("synthetic guard cleanup failure")

    def delete(self, name):
        if name == "preprocess_img":
            raise failure
        object.__delattr__(self, name)

    monkeypatch.setattr(harness.Raw, "__delattr__", delete)
    expected = EngineGuardError if cleanup_type is RuntimeError else cleanup_type
    with pytest.raises(expected):
        stack.invoke()
    assert harness.raw.calls == 1 and all(count == 1 for count in _session_counts(harness).values())
    _assert_calls(stack, ["completed"])
    _assert_completed_roles(stack, 0)
    assert stack.guard._broken and not stack.guard._lock.locked()
    # A poisoned guard never starts another dispatch, including through the
    # recorder. A fresh engine is required; no hidden recovery or retry occurs.
    with pytest.raises(EngineGuardError, match="fresh engine"):
        stack.invoke()
    assert harness.raw.calls == 1
    _assert_calls(stack, ["completed"])


@pytest.mark.parametrize("failure_kind", ["cleanup", "recognizer_latch"])
def test_complete_stage_cohort_keeps_post_return_rejection_and_later_work_explicit(
        harness, monkeypatch, failure_kind):
    guard = RapidOCREngineGuard(harness.raw)
    if failure_kind == "cleanup":
        def delete(self, name):
            if name == "preprocess_img":
                raise RuntimeError("synthetic cleanup failure")
            object.__delattr__(self, name)
        monkeypatch.setattr(harness.Raw, "__delattr__", delete)
    else:
        recognizer_type = type(harness.raw.text_rec)
        original, fired = recognizer_type.__call__, []

        def recognize(self, value):
            result = original(self, value)
            if not fired:
                fired.append(True)
                result.txts = None
            return result

        monkeypatch.setattr(recognizer_type, "__call__", recognize)

    class Reader(SyntheticStageReader):
        def __enter__(self):
            self._engine = guard
            return self

    ref = reference()
    observed, recorder = collect_stage_observation(
        SOURCE, ref, reference_sha256=REFERENCE, configuration=dict(policy.DEFAULT_CONFIGURATION),
        reader_factory=Reader)
    assert policy.validate_stage_observation(observed, ref, REFERENCE) == observed
    page, calls = observed["pages"][0], recorder.calls
    affected = page["detection" if failure_kind == "cleanup" else "full_page"]
    assert affected["status"] == "unavailable" and affected["reason"] == "invalid_stage_output"
    affected_call = calls[0 if failure_kind == "cleanup" else 1]
    assert affected["call_id"] == affected_call["id"] and affected_call["status"] == "completed"
    assert all(role["failed"] == 0 for role in affected_call["roles"].values())
    assert len(page["gold_crops"]) == 1 and observed["canonical_extraction_modified"] is False
    if failure_kind == "cleanup":
        assert harness.raw.calls == len(calls) == len(recorder.receipt_calls) == 1
        assert page["full_page"]["status"] == page["gold_crops"][0]["status"] == "unavailable"
        assert page["full_page"]["call_id"] is page["gold_crops"][0]["call_id"] is None
        assert page["gold_crops"][0]["text"] is None and guard._broken
    else:
        assert harness.raw.calls == len(calls) == len(recorder.receipt_calls) == 3
        assert page["detection"]["status"] == page["gold_crops"][0]["status"] == "available"
        assert page["gold_crops"][0]["text"] == "ok" and not guard._broken
        assert page["gold_crops"][0]["call_id"] == "call-0003"
        assert _session_counts(harness) == {"detection": 2, "classification": 1, "recognition": 2}
