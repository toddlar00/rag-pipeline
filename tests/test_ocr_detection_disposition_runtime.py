"""Actual pinned Python formatting, inert model/session and generated arrays.

No PDF, model constructor, native OCR or downloads. These controls characterize
same-call bookkeeping and optional-observer isolation, not recognition accuracy.
"""
from __future__ import annotations

import hashlib
import threading
from types import SimpleNamespace as NS

import pytest

import ocr_disposition_observer as observer
from ocr_detection_disposition_runtime import DispositionRecorder
from ocr_engine_guard import EngineGuardError, RapidOCREngineGuard
from ocr_execution_receipt import ExecutionRecorder
from test_ocr_engine_guard import harness as harness, state
from test_ocr_disposition_characterization import upstream as upstream


@pytest.fixture
def stack(harness, upstream, monkeypatch):
    """Real upstream formatter/run-steps, explicitly inert component delegates."""
    raw, np, main = harness.raw, harness.np, upstream.main
    assert observer.verified_recipe()
    # Actual classes/methods were byte-verified before constructors were disabled.
    for name in ("run_ocr_steps", "update_params", "build_final_output", "filter_by_text_score",
                 "detect_and_crop", "cls_and_rotate", "recognize_txt"):
        monkeypatch.setattr(harness.Raw, name, getattr(main.RapidOCR, name), raising=False)
    original_det = type(raw.text_det).__call__
    original_cls = type(raw.text_cls).__call__
    original_rec = type(raw.text_rec).__call__
    raw.text_cls.cls_thresh = 0.9
    raw.cfg.Global.font_path = None
    raw.cfg.Rec = NS(lang_type="en")
    raw.load_img = lambda image: image
    texts, scores = ["hello"], [0.75]

    def detected(self, image):
        result = original_det(self, image)
        return main.TextDetOutput(boxes=result.boxes, scores=None if result.boxes is None else [0.91] * len(result.boxes), elapse=0.0)

    def classified(self, images):
        result = original_cls(self, images)
        return main.TextClsOutput(img_list=result.img_list, cls_res=[("0", 0.95)] * len(images), elapse=0.0)

    def recognized(self, value):
        original_rec(self, value)
        return main.TextRecOutput(imgs=list(value.img), txts=tuple(texts), scores=list(scores),
                                  word_results=[None] * len(texts), elapse=0.0)

    def called(self, pixels, **options):
        self.calls += 1
        return main.RapidOCR.__call__(self, pixels, **options)

    monkeypatch.setattr(type(raw.text_det), "__call__", detected)
    monkeypatch.setattr(type(raw.text_cls), "__call__", classified)
    monkeypatch.setattr(type(raw.text_rec), "__call__", recognized)
    monkeypatch.setattr(harness.Raw, "__call__", called)
    monkeypatch.setattr(main, "VisRes", lambda **kwargs: None)
    monkeypatch.setattr(main, "map_img_to_original", lambda crops, rh, rw: crops)
    import ocr_engine_guard
    monkeypatch.setattr(ocr_engine_guard, "_runtime", lambda: (harness.Raw, main.RapidOCRError, np))
    guard = RapidOCREngineGuard(raw)

    class Reader:
        min_score = 0.0

        def _load_engine(self):
            return getattr(self, "_engine", guard)

        def retry(self, number):
            engine = self._load_engine()
            engine.prepare(harness.pixels)
            return engine(harness.pixels)

        def retry_region(self, number, bbox):
            return Reader.retry(self, number)

        def retry_hardscan(self, number, bbox, recipe):
            return Reader.retry(self, number)

    def create(operation="pages", plan=None):
        disposition = DispositionRecorder(operation, "a" * 64, plan)
        execution = ExecutionRecorder(disposition_binding=disposition.dispatch_binding)
        reader = execution.reader_factory(disposition.reader_factory(Reader))()
        return NS(disposition=disposition, execution=execution, reader=reader)

    return NS(raw=raw, np=np, pixels=harness.pixels, texts=texts, scores=scores,
              create=create, guard=guard, Reader=Reader, main=main, harness=harness)


def _snapshot(pair):
    observations = pair.disposition.observations()
    assert set(observations) == {"schema_version", "kind", "operation", "request_sha256", "attempts"}
    return observations["attempts"][-1]


def _validate(snapshot):
    import ocr_detection_disposition as leaf
    diag = {"state": snapshot["diagnostic_state"], "reason": snapshot["diagnostic_reason"],
            **{key: snapshot[key] for key in ("settings", "raster", "roles", "stages", "detection_count", "detections")}, "mapping": None}
    item = {"dispatch": snapshot["dispatch"], "attempt_index": snapshot["attempt_index"]}
    leaf._diagnostic(diag, item, {"max_side": 6000, "max_pixels": 25000000})
    if diag["state"] == "complete":
        leaf._complete(diag)


def test_full_route_exact_raw_output_ticket_pixels_and_restoration(stack):
    pair = stack.create()
    original, pixels = state(stack.raw), stack.pixels.copy()
    result = pair.reader.retry(2)
    snapshot = _snapshot(pair)
    assert result.txts == ("hello",) and snapshot["diagnostic_state"] == "complete"
    assert snapshot["dispatch"] == {"call_id": "call-0001", "ordinal": 1, "raw_status": "completed",
        "ticket_sha256": observer.canonical_sha256({"request_sha256": "a" * 64, "item_id": "page-00002",
            "attempt_index": 1, "call_id": "call-0001", "dispatch_ordinal": 1})}
    assert snapshot["raster"]["pixel_sha256"] == hashlib.sha256(memoryview(pixels).cast("B")).hexdigest()
    assert snapshot["detections"][0]["detector_score"] is None
    assert snapshot["raw_output"]["lines"][0]["text_sha256"] == hashlib.sha256(b"hello").hexdigest()
    assert state(stack.raw) == original and "filter_by_text_score" not in vars(stack.raw)
    assert stack.np.array_equal(pixels, stack.pixels) and stack.raw.calls == 1
    _validate(snapshot)
    snapshot["detections"].clear()
    assert len(_snapshot(pair)["detections"]) == 1


@pytest.mark.parametrize("texts,scores,expected", [
    (["", " \t", "\u2003"], [0.9, 0.8, 0.7], ()),
    (["below", "also"], [0.1, 0.49], ()),
    (["", " same ", "same", "same", "\u200b"], [1, .49, .5, .75, .5], ("same", "same", "\u200b")),
    ([" \thello\u00a0 "], [.5], (" \thello\u00a0 ",)),
])
def test_lower_seam_nonzero_filter_lineage_retains_exact_ordinal_multiplicity(stack, monkeypatch, texts, scores, expected):
    # Deliberately characterize the lower emitter seam, not a supported v1
    # workflow: production admits only the immutable 0/0/.9 thresholds.
    monkeypatch.setattr(observer, "_RECIPE_THRESHOLDS", (.5, 0., .9))
    stack.texts[:], stack.scores[:] = texts, scores
    stack.raw.text_score = stack.raw.cfg.Global.text_score = .5
    box = stack.raw.text_det.boxes[0]
    stack.raw.text_det.boxes = stack.np.array([box.copy() for _ in texts], dtype=stack.np.float32)
    pair = stack.create()
    result = pair.reader.retry(1)
    snapshot = _snapshot(pair)
    assert (result.txts or ()) == expected
    assert snapshot["diagnostic_state"] == "complete", snapshot
    assert [d["id"] for d in snapshot["detections"]] == [f"d{i:04d}" for i in range(1, len(texts)+1)]
    assert snapshot["stages"]["score_filter"]["input_count"] == sum(bool(t.strip()) for t in texts)
    assert snapshot["stages"]["score_filter"]["output_count"] == len(expected)
    if not expected:
        assert snapshot["stages"]["score_filter"]["box_output"] == {"state": "array", "dtype": "float64", "shape": [0]}
        assert snapshot["raw_output"] == {"boxes": {"state": "none", "dtype": None, "shape": None}, "lines": []}
    import ocr_detection_disposition as leaf
    leaf._complete({"detection_count": snapshot["detection_count"], "stages": snapshot["stages"],
        "roles": snapshot["roles"], "raster": snapshot["raster"], "settings": snapshot["settings"], "detections": snapshot["detections"]})


def test_true_empty_detector_is_one_session_not_no_call(stack):
    stack.raw.text_det.boxes = None
    pair = stack.create()
    assert pair.reader.retry(1).boxes is None
    snapshot = _snapshot(pair)
    assert snapshot["diagnostic_state"] == "complete" and snapshot["detection_count"] == 0
    assert snapshot["roles"]["detection"]["session_completed"] == 1
    assert snapshot["roles"]["recognition"]["attempted"] == 0
    _validate(snapshot)


@pytest.mark.parametrize("event", ["preprocess", "detector", "crop_entry", "crop_return", "classifier_entry", "classifier_return",
                                    "recognizer_entry", "recognizer_return", "formatter_entry", "filter_entry", "filter_return", "formatter_return", "guard_result"])
@pytest.mark.parametrize("error_type", [RuntimeError, MemoryError])
def test_diagnostic_faults_never_change_raw_return_receipt_or_restore(stack, monkeypatch, event, error_type):
    original = observer.DispositionFrame._event
    seen = []
    def injected(self, tag, values):
        if tag == event:
            seen.append(tag)
            raise error_type("diagnostic only")
        return original(self, tag, values)
    monkeypatch.setattr(observer.DispositionFrame, "_event", injected)
    pair = stack.create()
    before = state(stack.raw)
    assert pair.reader.retry(1).txts == ("hello",)
    snapshot = _snapshot(pair)
    assert seen == [event] and snapshot["diagnostic_state"] == "unavailable"
    assert snapshot["diagnostic_reason"] == "callback_failed"
    assert snapshot["detections"] == [] and snapshot["detection_count"] is None
    assert snapshot["dispatch"]["raw_status"] == pair.execution.calls[0]["status"] == "completed"
    assert state(stack.raw) == before and stack.raw.calls == 1
    _validate(snapshot)


@pytest.mark.parametrize("event,status,count", [("admit", None, 0), ("preprocess", "failed", 1),
                                                ("filter_return", "failed", 1), ("guard_result", "completed", 1)])
@pytest.mark.parametrize("error_type", [KeyboardInterrupt, SystemExit, GeneratorExit])
def test_cancellation_identity_and_raw_accounting_boundary(stack, monkeypatch, event, status, count, error_type):
    original = observer.DispositionFrame._event
    error = error_type("generated cancellation")
    def injected(self, tag, values):
        if tag == event:
            raise error
        return original(self, tag, values)
    monkeypatch.setattr(observer.DispositionFrame, "_event", injected)
    pair = stack.create()
    before = state(stack.raw)
    with pytest.raises(error_type) as caught:
        pair.reader.retry(1)
    assert caught.value is error and stack.raw.calls == count
    assert [c["status"] for c in pair.execution.calls] == ([] if status is None else [status])
    assert state(stack.raw) == before and pair.disposition.dispatch_binding.active is None


def test_optional_filter_hook_partial_install_failure_has_no_call_and_peer_id_one(stack, monkeypatch):
    pair = stack.create()
    before, fired = state(stack.raw), []
    def setter(self, name, value):
        object.__setattr__(self, name, value)
        if name == "filter_by_text_score" and not fired:
            fired.append(True)
            raise RuntimeError("partial hook install")
    monkeypatch.setattr(stack.harness.Raw, "__setattr__", setter)
    with pytest.raises(RuntimeError):
        pair.reader.retry(1)
    assert pair.execution.calls == [] and _snapshot(pair)["diagnostic_state"] == "not_run"
    assert state(stack.raw) == before and "filter_by_text_score" not in vars(stack.raw)
    pair.reader.retry(2)
    assert _snapshot(pair)["dispatch"]["call_id"] == "call-0001"


def test_raw_session_failure_is_not_diagnostic_failure(stack):
    pair = stack.create()
    error = RuntimeError("inert session failed")
    stack.raw.text_rec.session.error = error
    with pytest.raises(RuntimeError) as caught:
        pair.reader.retry(1)
    assert caught.value is error
    snapshot = _snapshot(pair)
    assert snapshot["diagnostic_state"] == "partial" and snapshot["detection_count"] == 1
    assert snapshot["roles"]["recognition"] == {"state": "failed", "attempted": 1, "completed": 0,
        "failed": 1, "session_attempted": 1, "session_completed": 0, "session_failed": 1}
    assert snapshot["dispatch"]["raw_status"] == "failed"
    _validate(snapshot)


def test_swallowed_role_failure_remains_completed_raw_rejected_guard_and_partial(stack):
    pair = stack.create()
    stack.raw.text_rec.session.error = stack.main.RapidOCRError("inert upstream-caught failure")
    with pytest.raises(EngineGuardError):
        pair.reader.retry(1)
    snapshot = _snapshot(pair)
    assert snapshot["dispatch"]["raw_status"] == "completed" and snapshot["diagnostic_state"] == "partial"
    assert snapshot["roles"]["recognition"]["failed"] == 1 and snapshot["detection_count"] == 1
    _validate(snapshot)


@pytest.mark.parametrize("attribute,value", [("use_cls", False), ("use_rec", False)])
def test_unsupported_route_is_diagnostic_unavailability_not_option_mutation(stack, attribute, value):
    setattr(stack.raw, attribute, value)
    pair = stack.create()
    pair.reader.retry(1)
    snapshot = _snapshot(pair)
    assert snapshot["diagnostic_reason"] == "unsupported_route" and snapshot["settings"][attribute] is value
    assert getattr(stack.raw, attribute) is value and stack.raw.calls == 1
    _validate(snapshot)


def test_unverified_recipe_disables_only_diagnostics(stack, monkeypatch):
    monkeypatch.setattr(observer, "verified_recipe", lambda: False)
    pair = stack.create()
    assert pair.reader.retry(1).txts == ("hello",)
    snapshot = _snapshot(pair)
    assert snapshot["diagnostic_reason"] == "unsupported_recipe" and snapshot["work"]["raster_bytes_hashed"] == 0
    _validate(snapshot)


def test_text_budget_exhaustion_never_clips_legacy_output(stack):
    stack.texts[:] = ["x" * 4097]
    pair = stack.create()
    assert pair.reader.retry(1).txts == tuple(stack.texts)
    snapshot = _snapshot(pair)
    assert snapshot["diagnostic_reason"] == "budget_exhausted"
    assert snapshot["work"]["exhausted"] == {"limit": "codepoints_per_text", "used_before": 0, "requested_units": 4097}
    assert snapshot["detection_count"] is None and snapshot["dispatch"]["raw_status"] == "completed"
    _validate(snapshot)


def test_wrong_ordinal_vector_disables_without_mutating_formatter(stack, monkeypatch):
    original = stack.harness.Raw.filter_by_text_score
    def altered(self, result):
        result.txts = tuple("changed" for _ in result.txts)
        return original(self, result)
    monkeypatch.setattr(stack.harness.Raw, "filter_by_text_score", altered)
    pair = stack.create()
    assert pair.reader.retry(1).txts == ("changed",)
    snapshot = _snapshot(pair)
    assert snapshot["diagnostic_reason"] == "lineage_invalid" and snapshot["detection_count"] is None
    _validate(snapshot)


@pytest.mark.parametrize("operation", ["regions", "hardscan"])
def test_same_page_duplicate_selectors_have_separate_plan_ordinals(stack, operation):
    recipe = None
    if operation == "hardscan":
        from ocr_hardscan import validate_recipe
        # Use the existing authoritative default recipe shape.
        recipe = validate_recipe({"orientation_clockwise": 0, "illumination": "none", "bow_fraction": 0.0,
                                  "bow_assumption": "none"})
    rows = [{"region_id": name, "page_number": 1, "bbox": [0., 0., 1., 1.],
             **({"recipe": recipe} if operation == "hardscan" else {})} for name in ("a", "b")]
    pair = stack.create(operation, {"regions": rows})
    for row in rows:
        if operation == "regions":
            pair.reader.retry_region(1, row["bbox"])
        else:
            pair.reader.retry_hardscan(1, row["bbox"], recipe)
    snapshots = pair.disposition.observations()["attempts"]
    assert [s["item_id"] for s in snapshots] == ["region-0001", "region-0002"]
    assert [s["dispatch"]["call_id"] for s in snapshots] == ["call-0001", "call-0002"]
    for snapshot in snapshots:
        _validate(snapshot)


def test_reader_rejection_after_raw_return_does_not_relabel_status(stack, monkeypatch):
    original = stack.Reader.retry
    def rejected(self, number):
        original(self, number)
        raise ValueError("final reader rejection")
    monkeypatch.setattr(stack.Reader, "retry", rejected)
    pair = stack.create()
    with pytest.raises(ValueError):
        pair.reader.retry(1)
    assert _snapshot(pair)["dispatch"]["raw_status"] == "completed"
    assert _snapshot(pair)["diagnostic_state"] == "complete"


def test_missing_wrong_type_and_escaped_binding_rejected_before_observation(stack):
    with pytest.raises(ValueError):
        ExecutionRecorder(disposition_binding=object())
    pair = stack.create()
    frame = pair.disposition.dispatch_binding.begin("page-00001", pair.reader, 0.0)
    with pytest.raises(ValueError):
        pair.disposition.observations()
    pair.disposition.dispatch_binding.end(frame)
    assert pair.disposition.dispatch_binding.reserve(pair.execution, pair.reader, stack.guard, 1) is None
    with pytest.raises(EngineGuardError):
        stack.guard(stack.pixels, disposition_frame=frame)
    assert stack.raw.calls == 0


@pytest.mark.parametrize("topology", ["no_active_frame", "wrong_reader", "reused_ticket", "wrong_receipt_owner"])
def test_enabled_capture_without_reserved_ticket_refuses_before_guard_or_raw(stack, monkeypatch, topology):
    pair = stack.create()
    engine = pair.reader._load_engine()
    binding = pair.disposition.dispatch_binding
    frame = None
    original = state(stack.raw)
    if topology != "no_active_frame":
        frame = binding.begin("page-00001", object() if topology == "wrong_reader" else pair.reader, 0.)
        if topology == "reused_ticket":
            assert binding.reserve(pair.execution, pair.reader, stack.guard, 1) is frame.ticket
        elif topology == "wrong_receipt_owner":
            binding.receipt_owner = object()
    entered = []
    prepare = RapidOCREngineGuard._prepare
    def tracked_prepare(self, *args):
        entered.append(True)
        return prepare(self, *args)
    monkeypatch.setattr(RapidOCREngineGuard, "_prepare", tracked_prepare)
    try:
        with pytest.raises(ValueError, match="reserved.*ticket"):
            engine(stack.pixels)
    finally:
        if frame is not None:
            binding.end(frame)
    assert entered == [] and stack.raw.calls == 0 and pair.execution.calls == []
    assert state(stack.raw) == original and not stack.guard._lock.locked()
    if frame is not None:
        assert _snapshot(pair)["dispatch"] is None


def test_no_active_frame_refusal_does_not_consume_later_valid_attempt(stack):
    pair = stack.create()
    with pytest.raises(ValueError, match="reserved.*ticket"):
        pair.reader._load_engine()(stack.pixels)
    assert pair.disposition.observations()["attempts"] == []
    assert pair.reader.retry(3).txts == ("hello",)
    snapshot = _snapshot(pair)
    assert snapshot["attempt_index"] == snapshot["dispatch"]["ordinal"] == 1
    assert snapshot["dispatch"]["call_id"] == "call-0001" and stack.raw.calls == 1


@pytest.mark.parametrize("explicit_none", [False, True])
def test_disabled_disposition_still_allows_existing_direct_timed_guard_call(stack, explicit_none):
    recorder = ExecutionRecorder(disposition_binding=None) if explicit_none else ExecutionRecorder()
    reader = recorder.reader_factory(stack.Reader)()
    assert reader._load_engine()(stack.pixels).txts == ("hello",)
    assert stack.raw.calls == 1 and recorder.calls[0]["status"] == "completed"
    assert "filter_by_text_score" not in vars(stack.raw)


def test_foreign_thread_cannot_begin_attempt(stack):
    pair = stack.create()
    errors = []
    def work():
        try:
            pair.reader.retry(1)
        except Exception as error:
            errors.append(error)
    thread = threading.Thread(target=work)
    thread.start()
    thread.join(timeout=5)
    assert len(errors) == 1 and isinstance(errors[0], ValueError)
    assert stack.raw.calls == 0 and pair.execution.calls == []


def test_baseline_no_observer_installs_no_filter_hook_or_recipe_checks(stack, monkeypatch):
    def forbidden(*args, **kwargs):
        raise AssertionError("disabled diagnostics must do no optional work")
    monkeypatch.setattr(observer, "verified_recipe", forbidden)
    monkeypatch.setattr(observer.DispositionFrame, "__init__", forbidden)
    execution = ExecutionRecorder()
    reader = execution.reader_factory(stack.Reader)()
    assert reader.retry(1).txts == ("hello",)
    assert len(execution.calls) == stack.raw.calls == 1
    assert "filter_by_text_score" not in vars(stack.raw)


@pytest.mark.parametrize("role", ["detection", "classification", "recognition"])
def test_actual_role_failure_preserves_identity_and_partial_counts(stack, role):
    component = getattr(stack.raw, {"detection": "text_det", "classification": "text_cls", "recognition": "text_rec"}[role])
    failure = RuntimeError("inert session error")
    component.session.error = failure
    pair = stack.create()
    with pytest.raises(RuntimeError) as caught:
        pair.reader.retry(1)
    assert caught.value is failure
    snapshot = _snapshot(pair)
    assert snapshot["diagnostic_state"] == "partial"
    assert snapshot["roles"][role]["failed"] == snapshot["roles"][role]["session_failed"] == 1
    name = {"detection": "detector", "classification": "classifier", "recognition": "recognizer"}[role]
    assert snapshot["stages"][name]["state"] == "failed"
    assert snapshot["stages"][name]["output_count"] is None
    _validate(snapshot)


def test_primitive_failure_unwind_never_calls_diagnostic_emitter(stack, monkeypatch):
    original = observer.DispositionFrame._event
    failing = []
    original_session = type(stack.raw.text_rec.session).__call__
    failure = KeyboardInterrupt("primary native-style cancellation")
    def session(self, tensor):
        if self is stack.raw.text_rec.session._wrapped:
            failing.append(True)
            raise failure
        return original_session(self, tensor)
    def event(self, tag, values):
        assert not failing, "no diagnostic callback may run during raw error unwinding"
        return original(self, tag, values)
    monkeypatch.setattr(type(stack.raw.text_rec.session), "__call__", session)
    monkeypatch.setattr(observer.DispositionFrame, "_event", event)
    pair = stack.create()
    with pytest.raises(KeyboardInterrupt) as caught:
        pair.reader.retry(1)
    assert caught.value is failure
    assert pair.execution.calls[0]["status"] == "failed"


def test_restoration_failure_poison_does_not_erase_completed_receipt(stack, monkeypatch):
    pair = stack.create()
    original = stack.harness.Raw.__delattr__
    def deleted(self, name):
        if name == "filter_by_text_score":
            raise RuntimeError("generated teardown failure")
        return original(self, name)
    monkeypatch.setattr(stack.harness.Raw, "__delattr__", deleted)
    with pytest.raises(EngineGuardError):
        pair.reader.retry(1)
    assert _snapshot(pair)["dispatch"]["raw_status"] == "completed"
    assert _snapshot(pair)["diagnostic_state"] == "complete"
    assert stack.guard._broken and not stack.guard._lock.locked()
    with pytest.raises(EngineGuardError):
        pair.reader.retry(2)
    assert _snapshot(pair)["diagnostic_state"] == "not_run" and len(pair.execution.calls) == 1


def test_ticket_reservation_cancellation_precedes_timed_observer(stack, monkeypatch):
    pair = stack.create()
    failure = KeyboardInterrupt("ticket construction cancelled")
    def stopped(self, *args):
        raise failure
    monkeypatch.setattr(observer.DispatchTicket, "__init__", stopped)
    with pytest.raises(KeyboardInterrupt) as caught:
        pair.reader.retry(1)
    assert caught.value is failure and pair.execution.calls == [] and stack.raw.calls == 0
    assert pair.disposition.observations()["attempts"] == []


def test_no_digest_callback_between_raw_return_and_committed_receipt(stack, monkeypatch):
    pair = stack.create()
    original = observer.canonical_sha256
    seen = []
    def digest(value):
        seen.append(len(pair.execution.calls))
        assert pair.execution.calls[0]["status"] == "completed"
        return original(value)
    monkeypatch.setattr(observer, "canonical_sha256", digest)
    pair.reader.retry(1)
    assert seen == []
    assert _snapshot(pair)["dispatch"]["call_id"] == "call-0001"
    assert seen == [1]


@pytest.mark.parametrize("role", ["detection", "classification", "recognition"])
def test_component_probe_wrappers_and_actual_sessions_remain_composable(stack, role):
    seen = []
    class Probe:
        def __init__(self, wrapped):
            self.wrapped = wrapped
        def __getattr__(self, name):
            return getattr(self.wrapped, name)
        def __call__(self, *args, **kwargs):
            seen.append(role)
            return self.wrapped(*args, **kwargs)
    attribute = {"detection": "text_det", "classification": "text_cls", "recognition": "text_rec"}[role]
    wrapped = Probe(getattr(stack.guard, attribute))
    setattr(stack.guard, attribute, wrapped)
    pair = stack.create()
    assert pair.reader.retry(1).txts == ("hello",)
    assert seen == [role] and getattr(stack.guard, attribute) is wrapped
    snapshot = _snapshot(pair)
    assert snapshot["roles"][role]["session_completed"] == 1
    _validate(snapshot)


def test_preflight_retry_failure_keeps_attempt_and_does_not_shift_dispatch(stack, monkeypatch):
    original = stack.Reader.retry
    def maybe(self, page_number):
        if page_number == 1:
            raise ValueError("generated preflight refusal")
        return original(self, page_number)
    monkeypatch.setattr(stack.Reader, "retry", maybe)
    pair = stack.create()
    with pytest.raises(ValueError):
        pair.reader.retry(1)
    pair.reader.retry(3)
    observations = pair.disposition.observations()["attempts"]
    assert [s["item_id"] for s in observations] == ["page-00001", "page-00003"]
    assert observations[0]["dispatch"] is None and observations[1]["dispatch"]["call_id"] == "call-0001"
    assert observations[1]["attempt_index"] == 2
    for snapshot in observations:
        _validate(snapshot)


def test_duplicate_attempt_and_call_budget_refused_before_raw(stack):
    pair = stack.create()
    pair.reader.retry(1)
    with pytest.raises(ValueError):
        pair.reader.retry(1)
    # Admission is bounded before constructing any next frame.
    binding = pair.disposition.dispatch_binding
    binding.frames.extend([binding.frames[0]] * 19)
    with pytest.raises(ValueError):
        pair.reader.retry(2)
    assert stack.raw.calls == 1


def test_call_scalar_thresholds_are_independent_and_exact(stack):
    stack.raw.text_score = stack.raw.cfg.Global.text_score = .5
    stack.Reader.min_score = .8
    stack.raw.text_cls.cls_thresh = .85
    pair = stack.create()
    pair.reader.retry(1)
    settings = _snapshot(pair)["settings"]
    assert [settings[key] for key in ("engine_text_score", "reader_min_score", "classification_threshold")] == [.5, .8, .85]
    assert _snapshot(pair)["diagnostic_reason"] == "unsupported_recipe"
    assert stack.raw.text_score == .5 and stack.Reader.min_score == .8 and stack.raw.text_cls.cls_thresh == .85


@pytest.mark.parametrize("field,value", [("cls_thresh", float("nan")), ("cls_thresh", True), ("cls_thresh", 10**400)])
def test_unsupported_diagnostic_threshold_does_not_change_original_route(stack, field, value):
    setattr(stack.raw.text_cls, field, value)
    pair = stack.create()
    assert pair.reader.retry(1).txts == ("hello",)
    snapshot = _snapshot(pair)
    assert snapshot["diagnostic_state"] == "unavailable" and snapshot["diagnostic_reason"] == "unsupported_recipe"
    _validate(snapshot)


@pytest.mark.parametrize("method,stage,role", [("cls_and_rotate", "classifier", "classification"),
                                               ("recognize_txt", "recognizer", "recognition")])
@pytest.mark.parametrize("when", ["before_component", "after_component"])
def test_full_method_failure_is_independent_of_component_failure(stack, monkeypatch, method, stage, role, when):
    original = getattr(stack.harness.Raw, method)
    failure = RuntimeError("generated full-method failure")
    def failed(self, *args):
        if when == "after_component":
            original(self, *args)
        raise failure
    monkeypatch.setattr(stack.harness.Raw, method, failed)
    pair = stack.create()
    with pytest.raises(RuntimeError) as caught:
        pair.reader.retry(1)
    assert caught.value is failure
    snapshot = _snapshot(pair)
    assert snapshot["diagnostic_state"] == "partial" and snapshot["detection_count"] == 1
    assert snapshot["stages"][stage]["state"] == "failed"
    assert snapshot["roles"][role]["state"] == ("completed" if when == "after_component" else "not_run")
    assert snapshot["detections"][0][role]["state"] == "unobserved"
    assert snapshot["stages"][stage]["output_count"] is snapshot["stages"][stage]["box_output"] is None
    _validate(snapshot)


@pytest.mark.parametrize("method,stage", [("crop_text_regions", "crops"), ("build_final_output", "formatter"),
                                         ("filter_by_text_score", "score_filter")])
def test_full_method_failure_has_no_invented_completed_prefix(stack, monkeypatch, method, stage):
    failure = RuntimeError("generated original-method failure")
    def failed(self, *args):
        raise failure
    monkeypatch.setattr(stack.harness.Raw, method, failed)
    pair = stack.create()
    with pytest.raises(RuntimeError) as caught:
        pair.reader.retry(1)
    assert caught.value is failure
    snapshot = _snapshot(pair)
    assert snapshot["diagnostic_state"] == "partial" and snapshot["detection_count"] == 1
    assert snapshot["stages"][stage]["state"] == "failed"
    assert snapshot["stages"][stage]["output_count"] is snapshot["stages"][stage]["box_output"] is None
    assert snapshot["raw_output"] is None
    _validate(snapshot)


@pytest.mark.parametrize("early", ["detector", "crops"])
def test_failed_formatter_after_actual_upstream_default_fallback_preserves_unknowns(stack, monkeypatch, early):
    """Real run_ocr_steps catches the early failure and discards its det output.

    The later formatter entry therefore observes None, even when the earlier
    detector return established one occurrence. Neither failure is success or
    a fabricated zero-detection observation. Components remain inert.
    """
    original_state = state(stack.raw)
    if early == "detector":
        stack.raw.text_det.error = stack.main.RapidOCRError("generated detector failure")
    else:
        def crop_failed(*args):
            raise stack.main.RapidOCRError("generated crop failure")
        monkeypatch.setattr(stack.harness.Raw, "crop_text_regions", crop_failed)
    failure = RuntimeError("generated formatter failure")
    def formatter_failed(*args):
        raise failure
    monkeypatch.setattr(stack.harness.Raw, "build_final_output", formatter_failed)
    pair = stack.create()
    with pytest.raises(RuntimeError) as caught:
        pair.reader.retry(1)
    assert caught.value is failure
    snapshot = _snapshot(pair)
    assert snapshot["diagnostic_state"] == "partial"
    assert snapshot["dispatch"]["raw_status"] == pair.execution.calls[0]["status"] == "failed"
    assert len(pair.execution.calls) == stack.raw.calls == 1
    assert snapshot["raw_output"] is None
    assert snapshot["detection_count"] == (None if early == "detector" else 1)
    assert snapshot["stages"]["detector"]["state"] == ("failed" if early == "detector" else "completed")
    assert snapshot["stages"]["crops"]["state"] == ("not_run" if early == "detector" else "failed")
    assert snapshot["stages"]["formatter"] == {
        "state": "failed", "input_count": 0, "output_count": None,
        "box_input": {"state": "none", "dtype": None, "shape": None}, "box_output": None,
    }
    assert all(record["crop_state"] == "unobserved" for record in snapshot["detections"])
    assert all(record["recognition"]["state"] == "not_run" for record in snapshot["detections"])
    assert state(stack.raw) == original_state
    _validate(snapshot)


@pytest.mark.parametrize("target", ["_snapshot", "snapshot", "deepcopy"])
@pytest.mark.parametrize("error_type", [RuntimeError, MemoryError])
def test_diagnostic_finalization_failure_uses_minimal_actual_call_fallback(stack, monkeypatch, target, error_type):
    pair = stack.create()
    assert pair.reader.retry(1).txts == ("hello",)
    def failed(*args, **kwargs):
        raise error_type("diagnostic serialization failed")
    if target == "deepcopy":
        monkeypatch.setattr(observer.copy, "deepcopy", failed)
    else:
        monkeypatch.setattr(observer.DispositionFrame, target, failed)
    snapshot = _snapshot(pair)
    assert snapshot["diagnostic_state"] == "unavailable" and snapshot["diagnostic_reason"] == "callback_failed"
    assert snapshot["dispatch"]["raw_status"] == pair.execution.calls[0]["status"] == "completed"
    assert snapshot["detection_count"] is None and snapshot["detections"] == []
    assert snapshot["raster"] is snapshot["settings"] is snapshot["raw_output"] is None
    assert snapshot["work"]["detections_reserved"] == 1 and snapshot["work"]["raster_bytes_hashed"] == 64*64*3
    _validate(snapshot)


@pytest.mark.parametrize("error_type", [KeyboardInterrupt, SystemExit, GeneratorExit])
def test_finalization_cancellation_never_relabels_the_completed_raw_record(stack, monkeypatch, error_type):
    pair = stack.create()
    pair.reader.retry(1)
    failure = error_type("finalization cancelled")
    def failed(*args):
        raise failure
    monkeypatch.setattr(observer.DispositionFrame, "_snapshot", failed)
    with pytest.raises(error_type) as caught:
        pair.disposition.observations()
    assert caught.value is failure
    assert pair.execution.calls[0]["status"] == "completed" and stack.raw.calls == 1


def test_enabled_disposition_refuses_generic_loader_before_prepare_or_dispatch():
    called = []
    class Generic:
        def prepare(self, *args):
            called.append("prepare")
        def __call__(self, *args):
            called.append("call")
            return object()
    generic = Generic()
    class Reader:
        min_score = 0.
        def _load_engine(self):
            return generic
        def retry(self, number):
            engine = self._load_engine()
            engine.prepare(None)
            return engine(None)
    disposition = DispositionRecorder("pages", "a" * 64)
    execution = ExecutionRecorder(disposition_binding=disposition.dispatch_binding)
    reader = execution.reader_factory(disposition.reader_factory(Reader))()
    with pytest.raises(ValueError, match="requires a guarded"):
        reader.retry(1)
    snapshot = disposition.observations()["attempts"][0]
    assert called == [] and execution.calls == [] and snapshot["dispatch"] is None
    assert snapshot["diagnostic_state"] == "not_run"
    # Default generic recording remains the original supported callable path.
    ordinary = ExecutionRecorder()
    assert ordinary.reader_factory(Reader)().retry(1) is not None
    assert called == ["prepare", "call"] and ordinary.calls[0]["status"] == "completed"
