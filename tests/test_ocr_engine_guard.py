"""Generated arrays and inert sessions; never construct OCR models."""
from __future__ import annotations

import copy
import math
from pathlib import Path
import subprocess
import sys
from types import SimpleNamespace as NS

import pytest

import ocr_engine_guard as guard_module
from ocr_engine_guard import EngineAllocationLimit, EngineGuardError, RapidOCREngineGuard


class EmptyError(Exception):
    pass


@pytest.fixture
def harness(monkeypatch):
    np = pytest.importorskip("numpy")

    class Session:
        def __init__(self):
            self.calls = []
            self.error = None

        def __call__(self, tensor):
            self.calls.append(tensor.shape)
            if self.error is not None:
                raise self.error
            return None

    class Detector:
        def __init__(self):
            self.session = Session()
            self.limit_type, self.limit_side_len = "min", 736
            self.mean = self.std = [0.5, 0.5, 0.5]
            self.postprocess_op = NS(max_candidates=1000, box_thresh=0.5, unclip_ratio=1.6)
            self.boxes = np.array([[[1, 1], [32, 1], [32, 16], [1, 16]]], dtype=np.float32)
            self.tensor_dtype = np.float32
            self.tensor_override = None
            self.skip_session = False
            self.error = None

        def __call__(self, image):
            if self.error is not None:
                raise self.error
            h, w = image.shape[:2]
            ratio = max(1., 736 / min(h, w))
            dw, dh = (int(round(int(n * ratio) / 32) * 32) for n in (w, h))
            shape = self.tensor_override or (1, 3, dh, dw)
            if not self.skip_session:
                self.session(np.zeros(shape, dtype=self.tensor_dtype))
            return NS(boxes=self.boxes, scores=None)

    class Classifier:
        def __init__(self):
            self.session = Session()
            self.cls_image_shape, self.cls_batch_num = [3, 48, 192], 6
            self.entered = 0

        def __call__(self, images):
            self.entered += 1
            images = copy.deepcopy(images)
            for start in range(0, len(images), 6):
                self.session(np.zeros((min(6, len(images) - start), 3, 48, 192), dtype=np.float32))
            return NS(img_list=images)

    class Recognizer:
        def __init__(self):
            self.session = Session()
            self.rec_image_shape, self.rec_batch_num = [3, 48, 320], 6
            self.entered = 0

        def __call__(self, value):
            self.entered += 1
            images = sorted(value.img, key=lambda image: image.shape[1] / image.shape[0])
            for start in range(0, len(images), 6):
                group = images[start:start + 6]
                width = int(48 * max(320 / 48, *(image.shape[1] / image.shape[0] for image in group)))
                self.session(np.zeros((len(group), 3, 48, width), dtype=np.float32))
            return NS(txts=tuple("ok" for _ in images))

    class Raw:
        def __init__(self):
            self.min_side_len, self.max_side_len = 30, 6000
            self.min_height, self.width_height_ratio = 30, 8
            self.return_word_box = self.return_single_char_box = False
            self.text_score = 0.0
            self.use_det = self.use_cls = self.use_rec = True
            self.cfg = NS(Global=NS(use_preprocess_img=True, use_vertical_padding=True,
                return_word_box=False, return_single_char_box=False, text_score=0.0,
                min_side_len=30, max_side_len=6000))
            self.text_det, self.text_cls, self.text_rec = Detector(), Classifier(), Recognizer()
            self.calls = self.crop_calls = self.final_calls = 0
            self.before_call = None
            self.extra_crops = None
            self.record_change = None
            self.catch_all = False

        def preprocess_img(self, image):
            h, w = image.shape[:2]
            if min(h, w) < 30:
                ratio = 30 / min(h, w)
                nw, nh = (int(round(int(n * ratio) / 32) * 32) for n in (w, h))
                image = np.zeros((nh, nw, 3), dtype=np.uint8)
            else:
                nw, nh = w, h
            return image, {"preprocess": {"ratio_h": h / nh, "ratio_w": w / nw}}

        def detect_and_crop(self, image, record):
            h, w = image.shape[:2]
            padding = int(abs(max(int(w / 8), 30) * 2 - h) / 2) if h <= 30 or w / h > 8 else 0
            if padding:
                image = np.zeros((h + 2 * padding, w, 3), dtype=np.uint8)
            record["padding_1"] = {"top": padding, "left": 0}
            result = self.text_det(image)
            if result.boxes is None:
                raise EmptyError("normal empty detector")
            return self.crop_text_regions(image, result.boxes), result

        def crop_text_regions(self, image, boxes):
            self.crop_calls += 1
            if self.extra_crops is not None:
                return self.extra_crops
            result = []
            for box in boxes:
                w = int(max(np.linalg.norm(box[0] - box[1]), np.linalg.norm(box[2] - box[3])))
                h = int(max(np.linalg.norm(box[0] - box[3]), np.linalg.norm(box[1] - box[2])))
                crop = np.zeros((h, w, 3), dtype=np.uint8)
                result.append(np.rot90(crop) if h / w >= 1.5 else crop)
            return result

        def cls_and_rotate(self, images):
            result = self.text_cls(images)
            if result.img_list is None:
                raise EmptyError("missing classifier output")
            return result.img_list, result

        def recognize_txt(self, images):
            result = self.text_rec(NS(img=images, return_word_box=self.return_word_box))
            if result.txts is None:
                raise EmptyError("missing recognizer output")
            return result

        def build_final_output(self, image, detected, classified, recognized, crops, record):
            self.final_calls += 1
            return NS(txts=recognized.txts, boxes=detected.boxes, image=image, crops=crops)

        def __call__(self, image, **options):
            self.calls += 1
            for name in ("use_det", "use_cls", "use_rec"):
                if options.get(name) is not None:
                    setattr(self, name, options[name])
            if self.before_call:
                self.before_call()
            image_original = image
            image, record = self.preprocess_img(image)
            detected, classified, recognized, crops = NS(boxes=None), NS(img_list=None), NS(txts=None), []
            try:
                if self.use_det:
                    crops, detected = self.detect_and_crop(image, record)
                else:
                    crops = [image]
                cls_images = crops
                if self.use_cls:
                    cls_images, classified = self.cls_and_rotate(crops)
                if self.use_rec:
                    recognized = self.recognize_txt(cls_images)
            except EmptyError:
                crops = []
            except BaseException:
                if not self.catch_all:
                    raise
            if self.record_change:
                self.record_change(record)
            return self.build_final_output(image_original, detected, classified, recognized, crops, record)

    monkeypatch.setattr(guard_module, "_runtime", lambda: (Raw, EmptyError, np))
    raw = Raw()
    return NS(np=np, raw=raw, Raw=Raw, pixels=np.zeros((64, 64, 3), dtype=np.uint8))


def state(raw):
    names = ("preprocess_img", "detect_and_crop", "crop_text_regions", "cls_and_rotate", "recognize_txt", "build_final_output")
    return ({name: vars(raw).get(name) for name in names},
            tuple(getattr(raw, name) for name in ("text_det", "text_cls", "text_rec")),
            tuple(getattr(raw, name).session for name in ("text_det", "text_cls", "text_rec")))


def test_dependency_light_import():
    code = "import ocr_engine_guard,sys; assert not ({'numpy','rapidocr','cv2','ocr_recovery_runtime','ocr_stage_runtime'} & sys.modules.keys())"
    result = subprocess.run([sys.executable, "-B", "-c", code], cwd=Path(__file__).resolve().parents[1], capture_output=True)
    assert result.returncode == 0, result.stderr


def test_prepare_does_not_invoke_or_install_hooks(harness):
    raw = harness.raw
    original = state(raw)
    guard = RapidOCREngineGuard(raw)
    prepared = guard.prepare(harness.pixels)
    assert prepared == {"source": [64, 64], "dimensions": {"global": [64, 64], "padded": [64, 64], "detector": [736, 736]},
                        "flags": {"use_det": True, "use_cls": True, "use_rec": True}}
    assert state(raw) == original and raw.calls == 0
    prepared["flags"]["use_det"] = False
    assert guard.prepare(harness.pixels)["flags"]["use_det"] is True


def test_full_route_restores_every_hook_and_preserves_pixels(harness):
    raw, pixels = harness.raw, harness.pixels
    original, source = state(raw), pixels.copy()
    result = RapidOCREngineGuard(raw)(pixels)
    assert result.txts == ("ok",) and result.image is pixels
    assert state(raw) == original and harness.np.array_equal(pixels, source)
    assert raw.calls == raw.crop_calls == raw.final_calls == 1
    assert raw.text_det.session.calls == [(1, 3, 736, 736)]
    assert raw.text_cls.session.calls == [(1, 3, 48, 192)]
    assert raw.text_rec.session.calls == [(1, 3, 48, 320)]


@pytest.mark.parametrize("flags,counts", [((True, False, False), (1, 0, 0)), ((False, False, True), (0, 0, 1)), ((False, True, False), (0, 1, 0))])
def test_explicit_stage_routes(harness, flags, counts):
    guard = RapidOCREngineGuard(harness.raw)
    kwargs = dict(zip(("use_det", "use_cls", "use_rec"), flags))
    guard.prepare(harness.pixels, **kwargs)
    guard(harness.pixels, **kwargs)
    assert tuple(len(component.session.calls) for component in guard._components.values()) == counts


@pytest.mark.parametrize("shape", [(1, 6000, 3), (6000, 32, 3), (32, 32, 1), (64, 64), (0, 64, 3)])
def test_invalid_or_amplified_shape_never_enters_engine(harness, shape):
    guard = RapidOCREngineGuard(harness.raw)
    with pytest.raises(EngineAllocationLimit):
        guard(harness.np.zeros(shape, dtype=harness.np.uint8))
    assert harness.raw.calls == harness.raw.crop_calls == harness.raw.final_calls == 0


@pytest.mark.parametrize("value", ["private-path", b"encoded bytes", None, [], object()])
def test_nonarray_inputs_rejected_without_loader(harness, value):
    with pytest.raises(EngineAllocationLimit):
        RapidOCREngineGuard(harness.raw)(value)
    assert harness.raw.calls == 0


def test_array_dtype_subclass_and_noncontiguous_refused(harness):
    class Spoof(harness.np.ndarray):
        pass
    guard = RapidOCREngineGuard(harness.raw)
    for image in (harness.pixels.astype(harness.np.float32), harness.pixels.view(Spoof), harness.pixels[:, ::-1]):
        with pytest.raises(EngineAllocationLimit):
            guard.prepare(image)


@pytest.mark.parametrize("name,value", [("min_side_len", 31), ("max_side_len", 2000), ("min_height", True),
                                      ("width_height_ratio", 8.), ("return_word_box", True), ("return_single_char_box", True),
                                      ("text_score", math.nan)])
def test_changed_global_recipe_rejected_before_engine(harness, name, value):
    guard = RapidOCREngineGuard(harness.raw)
    setattr(harness.raw, name, value)
    with pytest.raises(EngineGuardError):
        guard(harness.pixels)
    assert harness.raw.calls == 0


@pytest.mark.parametrize("role,name,value", [("text_det", "limit_type", "max"), ("text_det", "limit_side_len", 960),
    ("text_det", "mean", [0., 0., 0.]), ("text_cls", "cls_image_shape", [3, 80, 160]),
    ("text_cls", "cls_batch_num", 7), ("text_rec", "rec_image_shape", [3, 32, 320]), ("text_rec", "rec_batch_num", True)])
def test_changed_component_recipe_rejected(harness, role, name, value):
    guard = RapidOCREngineGuard(harness.raw)
    setattr(getattr(harness.raw, role), name, value)
    with pytest.raises(EngineGuardError):
        guard.prepare(harness.pixels)


@pytest.mark.parametrize("options", [{"use_det": 1}, {"use_rec": "true"}, {"return_word_box": True},
    {"return_single_char_box": True}, {"text_score": 0.1}, {"box_thresh": 0.9}, {"unclip_ratio": 3.},
    {"unknown": None}, {"use_det": False, "use_cls": False, "use_rec": False}])
def test_invalid_call_options_never_invoke(harness, options):
    with pytest.raises((EngineAllocationLimit, EngineGuardError)):
        RapidOCREngineGuard(harness.raw)(harness.pixels, **options)
    assert harness.raw.calls == 0


def test_dynamic_limits_preserve_reader_settings(harness):
    raw = harness.raw
    raw.max_side_len = raw.cfg.Global.max_side_len = 12000
    guard = RapidOCREngineGuard(raw, max_side=12000, max_pixels=100_000_000)
    assert guard.prepare(harness.pixels)["source"] == [64, 64]
    assert guard.max_side_len == 12000 and guard.raw_engine is raw


def test_normal_completed_empty_detector_is_not_failure(harness):
    harness.raw.text_det.boxes = None
    original = state(harness.raw)
    result = RapidOCREngineGuard(harness.raw)(harness.pixels)
    assert result.txts is None and result.boxes is None
    assert state(harness.raw) == original
    assert len(harness.raw.text_det.session.calls) == 1
    assert harness.raw.text_cls.entered == harness.raw.text_rec.entered == 0


@pytest.mark.parametrize("error", [EmptyError("swallowed"), ValueError("rejected"), KeyboardInterrupt(), SystemExit(17)])
@pytest.mark.parametrize("role", ["text_det", "text_cls", "text_rec"])
def test_component_failures_restore_and_cannot_become_empty(harness, role, error):
    raw = harness.raw
    original = state(raw)
    getattr(raw, role).session.error = error
    guard = RapidOCREngineGuard(raw)
    expected = EngineGuardError if isinstance(error, EmptyError) else type(error)
    with pytest.raises(expected):
        guard(harness.pixels)
    assert state(raw) == original
    getattr(raw, role).session.error = None
    assert guard(harness.pixels).txts == ("ok",)


def test_swallowed_cancellation_is_rethrown(harness):
    harness.raw.catch_all = True
    harness.raw.text_rec.session.error = KeyboardInterrupt()
    original = state(harness.raw)
    with pytest.raises(KeyboardInterrupt):
        RapidOCREngineGuard(harness.raw)(harness.pixels)
    assert state(harness.raw) == original


@pytest.mark.parametrize("boundary", ["session_tensor", "detected_crop"])
def test_swallowed_allocation_failure_keeps_valueerror_classification(harness, boundary):
    raw = harness.raw
    raw.catch_all = True
    if boundary == "session_tensor":
        raw.text_det.tensor_dtype = harness.np.float64
    else:
        raw.text_det.boxes[0, 1, 0] = 1000
    original = state(raw)
    with pytest.raises(EngineAllocationLimit) as caught:
        RapidOCREngineGuard(raw)(harness.pixels)
    assert isinstance(caught.value, ValueError)
    assert isinstance(caught.value.__cause__, EngineAllocationLimit)
    assert state(raw) == original
    assert raw.text_cls.entered == raw.text_rec.entered == 0


@pytest.mark.parametrize("change", ["dtype", "shape", "omitted_session"])
def test_actual_detector_tensor_and_schedule_checked(harness, change):
    raw = harness.raw
    if change == "dtype":
        raw.text_det.tensor_dtype = harness.np.float64
    elif change == "shape":
        raw.text_det.tensor_override = (1, 3, 32, 32)
    else:
        raw.text_det.skip_session = True
    with pytest.raises((EngineGuardError, EngineAllocationLimit)):
        RapidOCREngineGuard(raw)(harness.pixels)
    assert raw.text_det.session.calls == []
    assert raw.crop_calls == raw.text_cls.entered == raw.text_rec.entered == 0


@pytest.mark.parametrize("kind", ["outside", "nan", "degenerate", "integer", "too_many"])
def test_bad_boxes_rejected_before_rectification(harness, kind):
    boxes = harness.raw.text_det.boxes
    if kind == "outside":
        boxes[0, 1, 0] = 1000
    elif kind == "nan":
        boxes[0, 1, 0] = math.nan
    elif kind == "degenerate":
        boxes[:] = 1
    elif kind == "integer":
        harness.raw.text_det.boxes = boxes.astype(harness.np.int32)
    else:
        harness.raw.text_det.boxes = harness.np.repeat(boxes, 1001, axis=0)
    with pytest.raises(EngineAllocationLimit):
        RapidOCREngineGuard(harness.raw)(harness.pixels)
    assert harness.raw.crop_calls == harness.raw.text_cls.entered == 0


def test_crop_aggregate_refused_before_any_crop_allocation(harness):
    np, raw = harness.np, harness.raw
    box = np.array([[[0, 0], [400, 0], [400, 400], [0, 400]]], dtype=np.float32)
    raw.text_det.boxes = np.repeat(box, 1000, axis=0)
    with pytest.raises(EngineAllocationLimit):
        RapidOCREngineGuard(raw)(np.zeros((400, 400, 3), dtype=np.uint8))
    assert raw.crop_calls == raw.text_cls.entered == 0


@pytest.mark.parametrize("kind", ["extra", "dtype", "oversize", "aspect"])
def test_actual_crops_checked_before_copy_and_normalization(harness, kind):
    np, raw = harness.np, harness.raw
    if kind == "extra":
        raw.extra_crops = [np.zeros((15, 31, 3), np.uint8)] * 2
    elif kind == "dtype":
        raw.extra_crops = [np.zeros((15, 31, 3), np.float32)]
    elif kind == "oversize":
        raw.extra_crops = [np.zeros((32, 32, 3), np.uint8)]
    else:
        raw.text_det.boxes = np.array([[[0, 0], [6000, 0], [6000, 4], [0, 4]]], dtype=np.float32)
    image = np.zeros((736, 6000, 3), np.uint8) if kind == "aspect" else harness.pixels
    with pytest.raises(EngineAllocationLimit):
        RapidOCREngineGuard(raw)(image)
    assert raw.text_rec.entered == 0
    if kind != "aspect":
        assert raw.text_cls.entered == 0


def test_final_remap_invalid_before_copy(harness):
    harness.raw.record_change = lambda record: record["preprocess"].update(ratio_h=1e8)
    with pytest.raises(EngineAllocationLimit):
        RapidOCREngineGuard(harness.raw)(harness.pixels)
    assert harness.raw.final_calls == 0


def test_zero_size_final_remap_is_refused(harness):
    np, raw = harness.np, harness.raw
    raw.text_det.boxes = np.array([[[1, 1], [2, 1], [2, 2], [1, 2]]], dtype=np.float32)
    with pytest.raises(EngineAllocationLimit):
        RapidOCREngineGuard(raw)(np.zeros((3, 3, 3), np.uint8))
    assert raw.final_calls == 0


def test_component_stage_decorators_and_writes_are_real(harness):
    guard = RapidOCREngineGuard(harness.raw)
    original = guard.text_det
    seen = []
    class Probe:
        def __init__(self, wrapped):
            self.wrapped = wrapped
        def __getattr__(self, name):
            return getattr(self.wrapped, name)
        def __call__(self, *args, **kwargs):
            seen.append("detection")
            return self.wrapped(*args, **kwargs)
    wrapper = Probe(original)
    guard.text_det = wrapper
    assert harness.raw.text_det is wrapper
    guard(harness.pixels)
    assert harness.raw.text_det is wrapper and seen == ["detection"]
    assert guard.text_det.session is original.session
    guard.text_det = original


def test_component_ownership_cannot_be_spoofed(harness):
    guard = RapidOCREngineGuard(harness.raw)
    replacement = harness.Raw().text_det
    replacement.session = harness.raw.text_det.session
    guard.text_det = replacement
    with pytest.raises(EngineGuardError):
        guard.prepare(harness.pixels)


def test_existing_instance_method_restored_exactly(harness):
    raw = harness.raw
    bound = raw.preprocess_img
    raw.preprocess_img = lambda image: bound(image)
    exact = raw.preprocess_img
    RapidOCREngineGuard(raw)(harness.pixels)
    assert raw.preprocess_img is exact


def test_reentrancy_and_active_mutation_fail_closed(harness):
    guard = RapidOCREngineGuard(harness.raw)
    for callback in (lambda: guard.prepare(harness.pixels), lambda: guard(harness.pixels), lambda: setattr(guard, "text_det", object())):
        harness.raw.before_call = callback
        original = state(harness.raw)
        with pytest.raises(EngineGuardError):
            guard(harness.pixels)
        assert state(harness.raw) == original


def test_rejects_outer_proxy_or_subclass_at_construction(harness):
    class Sub(harness.Raw):
        pass
    for raw in (Sub(), NS(**vars(harness.raw))):
        with pytest.raises(EngineGuardError):
            RapidOCREngineGuard(raw)


def test_timed_engine_prepare_does_not_fabricate_invocations(harness):
    from ocr_execution_receipt import ExecutionRecorder
    guard = RapidOCREngineGuard(harness.raw)
    class Reader:
        def _load_engine(self):
            return guard
    recorder = ExecutionRecorder()
    reader = recorder.reader_factory(Reader)()
    timed = reader._load_engine()
    with pytest.raises(EngineAllocationLimit):
        timed.prepare(harness.np.zeros((1, 6000, 3), harness.np.uint8))
    assert recorder.calls == [] and harness.raw.calls == 0
    timed.prepare(harness.pixels)
    assert recorder.calls == []
    assert timed(harness.pixels).txts == ("ok",)
    assert len(recorder.calls) == 1 and recorder.calls[0]["status"] == "completed"
    assert timed.text_det.session is harness.raw.text_det.session


def test_actual_stage_recorder_component_hooks_reach_raw_engine(harness):
    from ocr_stage_runtime import StageRecorder
    raw = harness.raw
    original = state(raw)
    recorder = StageRecorder()
    result, call = recorder.invoke(RapidOCREngineGuard(raw), harness.pixels, stage="full_page", page_number=1)
    assert result.txts == ("ok",) and call["status"] == "completed"
    assert call["roles"] == {name: {"attempted": 1, "completed": 1, "failed": 0}
                             for name in ("detection", "classification", "recognition")}
    assert state(raw) == original


@pytest.mark.parametrize("after_write", [False, True])
def test_cancelled_partial_install_restores_all_prior_hooks(harness, monkeypatch, after_write):
    raw = harness.raw
    original = state(raw)
    guard = RapidOCREngineGuard(raw)
    fired = []
    def setter(self, name, value):
        if name == "crop_text_regions" and not fired:
            fired.append(True)
            if after_write:
                object.__setattr__(self, name, value)
            raise KeyboardInterrupt()
        object.__setattr__(self, name, value)
    monkeypatch.setattr(harness.Raw, "__setattr__", setter)
    with pytest.raises(KeyboardInterrupt):
        guard(harness.pixels)
    assert state(raw) == original and raw.calls == 0
    assert guard(harness.pixels).txts == ("ok",)


def test_failed_restore_poisoned_guard_still_restores_other_hooks(harness, monkeypatch):
    raw = harness.raw
    original = state(raw)
    guard = RapidOCREngineGuard(raw)
    original_delete = object.__delattr__
    def delete(self, name):
        if name == "preprocess_img":
            raise RuntimeError("simulated restoration failure")
        original_delete(self, name)
    monkeypatch.setattr(harness.Raw, "__delattr__", delete)
    with pytest.raises(EngineGuardError, match="restore"):
        guard(harness.pixels)
    assert state(raw)[1:] == original[1:]
    assert set(vars(raw)) & {"detect_and_crop", "crop_text_regions", "cls_and_rotate", "recognize_txt", "build_final_output"} == set()
    with pytest.raises(EngineGuardError, match="fresh engine"):
        guard.prepare(harness.pixels)
    assert guard._failure is None


@pytest.mark.parametrize("cancel_type", [KeyboardInterrupt, SystemExit])
@pytest.mark.parametrize("restore_type", [RuntimeError, KeyboardInterrupt, SystemExit])
def test_primary_cancellation_survives_cleanup_failure(harness, monkeypatch, cancel_type, restore_type):
    raw = harness.raw
    original = state(raw)
    guard = RapidOCREngineGuard(raw)
    cancellation = cancel_type("synthetic cancellation")
    raw.text_rec.session.error = cancellation

    def delete(self, name):
        if name == "preprocess_img":
            raise restore_type("synthetic restoration failure")
        object.__delattr__(self, name)

    monkeypatch.setattr(harness.Raw, "__delattr__", delete)
    with pytest.raises(cancel_type) as caught:
        guard(harness.pixels)
    assert caught.value is cancellation
    assert state(raw)[1:] == original[1:]
    assert not guard._lock.locked() and guard._failure is None
    with pytest.raises(EngineGuardError, match="fresh engine"):
        guard.prepare(harness.pixels)


@pytest.mark.parametrize("cancel_type", [KeyboardInterrupt, SystemExit])
@pytest.mark.parametrize("later_failure", [False, True])
def test_cancellation_during_cleanup_is_not_a_local_retry_failure(harness, monkeypatch, cancel_type, later_failure):
    raw = harness.raw
    original = state(raw)
    guard = RapidOCREngineGuard(raw)
    cancellation = cancel_type("synthetic cleanup cancellation")

    def delete(self, name):
        if name == ("build_final_output" if later_failure else "preprocess_img"):
            raise cancellation
        if later_failure and name == "preprocess_img":
            raise RuntimeError("synthetic later cleanup failure")
        object.__delattr__(self, name)

    monkeypatch.setattr(harness.Raw, "__delattr__", delete)
    with pytest.raises(cancel_type) as caught:
        guard(harness.pixels)
    assert caught.value is cancellation
    assert state(raw)[1:] == original[1:]
    assert not guard._lock.locked() and guard._failure is None
    with pytest.raises(EngineGuardError, match="fresh engine"):
        guard.prepare(harness.pixels)


@pytest.mark.parametrize("score", [10**1000, -(10**1000)], ids=["huge-positive", "huge-negative"])
def test_huge_integer_score_fails_statically_before_engine_work(harness, score):
    guard = RapidOCREngineGuard(harness.raw)
    harness.raw.text_score = harness.raw.cfg.Global.text_score = score
    with pytest.raises(EngineGuardError, match="allocation settings"):
        guard.prepare(harness.pixels)
    assert harness.raw.calls == 0


@pytest.mark.parametrize("ratio", [10**1000, -(10**1000)], ids=["huge-positive", "huge-negative"])
def test_huge_integer_remap_ratio_keeps_allocation_classification(harness, ratio):
    harness.raw.record_change = lambda record: record["preprocess"].update(ratio_h=ratio)
    original = state(harness.raw)
    with pytest.raises(EngineAllocationLimit, match="remap ratios"):
        RapidOCREngineGuard(harness.raw)(harness.pixels)
    assert state(harness.raw) == original and harness.raw.final_calls == 0


def test_installed_global_and_detector_empty_route_without_models(monkeypatch):
    np = pytest.importorskip("numpy")
    rapidocr = pytest.importorskip("rapidocr")
    if guard_module.version("rapidocr") != "3.9.2":
        pytest.skip("installed RapidOCR is outside the reviewed adapter version")
    from rapidocr.ch_ppocr_det.main import TextDetector
    from rapidocr.ch_ppocr_det.utils import DBPostProcess
    # __new__ avoids every model constructor; actual Global/detector methods
    # and actual OpenCV preprocessing/postprocessing still execute.
    raw = rapidocr.RapidOCR.__new__(rapidocr.RapidOCR)
    raw.cfg = raw._load_config(None, {"Global.max_side_len": 6000, "Global.text_score": 0.0})
    for name in ("min_side_len", "max_side_len", "min_height", "width_height_ratio", "return_word_box", "return_single_char_box", "text_score", "use_det", "use_cls", "use_rec"):
        setattr(raw, name, getattr(raw.cfg.Global, name))
    from rapidocr.utils.load_image import LoadImage
    raw.load_img = LoadImage()
    det = TextDetector.__new__(TextDetector)
    det.limit_side_len, det.limit_type, det.mean, det.std = 736, "min", [0.5] * 3, [0.5] * 3
    det.postprocess_op = DBPostProcess(max_candidates=1000)
    called = []
    def session(tensor):
        called.append(tensor.shape)
        return np.zeros((1, 1, tensor.shape[2], tensor.shape[3]), np.float32)
    det.session = session
    class Unused:
        def __call__(self, *_args, **_kwargs):
            pytest.fail("disabled component called")
    raw.text_det, raw.text_cls, raw.text_rec = det, Unused(), Unused()
    raw.text_cls.cls_image_shape, raw.text_cls.cls_batch_num = [3, 48, 192], 6
    raw.text_rec.rec_image_shape, raw.text_rec.rec_batch_num = [3, 48, 320], 6
    raw.text_cls.session = raw.text_rec.session = session
    guard = RapidOCREngineGuard(raw)
    pixels = np.zeros((64, 64, 3), np.uint8)
    before = pixels.copy()
    result = guard(pixels, use_det=True, use_cls=False, use_rec=False)
    assert result.boxes is None and called == [(1, 3, 736, 736)]
    assert np.array_equal(before, pixels) and raw.text_det is det and det.session is session


def test_installed_full_crop_classification_recognition_and_remap_without_models():
    np = pytest.importorskip("numpy")
    rapidocr = pytest.importorskip("rapidocr")
    if guard_module.version("rapidocr") != "3.9.2":
        pytest.skip("installed RapidOCR is outside the reviewed adapter version")
    from rapidocr.ch_ppocr_det.main import TextDetector
    from rapidocr.ch_ppocr_det.utils import DBPostProcess
    from rapidocr.ch_ppocr_cls.main import TextClassifier
    from rapidocr.ch_ppocr_cls.utils import ClsPostProcess
    from rapidocr.ch_ppocr_rec.main import TextRecognizer
    from rapidocr.utils.load_image import LoadImage
    raw = rapidocr.RapidOCR.__new__(rapidocr.RapidOCR)
    raw.cfg = raw._load_config(None, {"Global.max_side_len": 6000, "Global.text_score": 0.0})
    raw.cfg.Rec.font_path = None
    for name in ("min_side_len", "max_side_len", "min_height", "width_height_ratio", "return_word_box", "return_single_char_box", "text_score", "use_det", "use_cls", "use_rec"):
        setattr(raw, name, getattr(raw.cfg.Global, name))
    raw.load_img = LoadImage()
    det, cls, rec = (kind.__new__(kind) for kind in (TextDetector, TextClassifier, TextRecognizer))
    calls = {"detection": [], "classification": [], "recognition": []}
    def detection(tensor):
        calls["detection"].append(tensor.shape)
        result = np.zeros((1, 1, tensor.shape[2], tensor.shape[3]), np.float32)
        result[0, 0, 150:220, 150:500] = 1
        return result
    def classification(tensor):
        calls["classification"].append(tensor.shape)
        return np.array([[1., 0.]] * tensor.shape[0], np.float32)
    def recognition(tensor):
        calls["recognition"].append(tensor.shape)
        return np.zeros((tensor.shape[0], 2, 2), np.float32)
    det.limit_side_len, det.limit_type, det.mean, det.std = 736, "min", [0.5] * 3, [0.5] * 3
    det.postprocess_op = DBPostProcess(max_candidates=1000)
    det.session = detection
    cls.cls_image_shape, cls.cls_batch_num, cls.cls_thresh = [3, 48, 192], 6, 0.9
    cls.postprocess_op, cls.session = ClsPostProcess(["0", "180"]), classification
    rec.rec_image_shape, rec.rec_batch_num = [3, 48, 320], 6
    rec.cfg, rec.RTL_LANGS, rec.session = raw.cfg.Rec, set(), recognition
    rec.postprocess_op = lambda tensor, *_args, **_kwargs: ([("inert-session", 1.)] * tensor.shape[0], [None] * tensor.shape[0])
    raw.text_det, raw.text_cls, raw.text_rec = det, cls, rec
    pixels = np.zeros((64, 64, 3), np.uint8)
    pixels[20:30, 10:50, 1] = 93
    source = pixels.copy()
    baseline = raw(pixels)
    before = state(raw)
    result = RapidOCREngineGuard(raw)(pixels)
    assert result.txts == baseline.txts == ("inert-session",)
    assert result.scores == baseline.scores
    assert np.array_equal(result.boxes, baseline.boxes) and np.array_equal(pixels, source)
    assert state(raw) == before
    assert all(len(values) == 2 and values[0] == values[1] for values in calls.values())
