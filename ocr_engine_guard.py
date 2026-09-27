"""Instance-local allocation checks for the reviewed RapidOCR 3.9.2 recipe.

The caller must construct the raw engine using the existing verified model
loader. This adapter checks allocation-relevant settings and selected image,
crop, batch and remap boundaries; it does not authenticate model/native bytes or
bound model weights, arbitrary native intermediates, decoder work or process RSS.
No settings, pixels, module globals or cv2 functions are changed by this guard.
"""

from __future__ import annotations

from importlib.metadata import version
import math
import sys
import threading

from ocr_engine_limits import (
    EngineAllocationLimit, EngineLimits, bounded_sequence, crop_shapes,
    detector_dimensions, image_dimensions, remapped_crop_shapes,
    validate_crop_shapes, validate_tensor_shape, working_dimensions,
)
from ocr_disposition_observer import DispositionFrame


class EngineGuardError(RuntimeError):
    """The reviewed execution path failed or suppressed a component failure."""


def _runtime():
    if version("rapidocr") != "3.9.2":
        raise EngineGuardError("OCR guard requires the reviewed engine version")
    from rapidocr import RapidOCR
    from rapidocr.main import RapidOCRError
    import numpy as np

    return RapidOCR, RapidOCRError, np


def _equal(actual, expected):
    if type(expected) in (bool, int, float, str):
        return type(actual) is type(expected) and actual == expected
    try:
        actual = bounded_sequence(actual, maximum=len(expected))
    except EngineAllocationLimit:
        return False
    return len(actual) == len(expected) and all(_equal(a, b) for a, b in zip(actual, expected))


class _Proxy:
    def __init__(self, wrapped, invoke):
        object.__setattr__(self, "_wrapped", wrapped)
        object.__setattr__(self, "_invoke", invoke)

    def __getattr__(self, name):
        return getattr(self._wrapped, name)

    def __setattr__(self, name, value):
        setattr(self._wrapped, name, value)

    def __delattr__(self, name):
        delattr(self._wrapped, name)

    def __call__(self, *args, **kwargs):
        return self._invoke(self._wrapped, *args, **kwargs)


class RapidOCREngineGuard:
    """Wrap a raw engine before timing/observation decorators are installed.

    ``prepare`` is a side-effect-free preflight for readers to call before an
    outer execution counter. Every ``__call__`` repeats it. Component attribute
    writes forward to the raw engine so StageRecorder decorators remain real.
    Concurrent/reentrant invocation and mutation during a call are rejected.
    """

    def __init__(self, raw_engine, *, max_side=6000, max_pixels=25_000_000):
        limits = EngineLimits(max_side=max_side, max_pixels=max_pixels)
        engine_class, empty_error, np = _runtime()
        if type(raw_engine) is not engine_class:
            raise EngineGuardError("OCR guard requires a raw reviewed engine instance")
        object.__setattr__(self, "_raw", raw_engine)
        object.__setattr__(self, "_limits", limits)
        object.__setattr__(self, "_np", np)
        object.__setattr__(self, "_empty_error", empty_error)
        object.__setattr__(self, "_lock", threading.Lock())
        object.__setattr__(self, "_failure", None)
        object.__setattr__(self, "_broken", False)
        object.__setattr__(self, "_components", {
            "detection": raw_engine.text_det, "classification": raw_engine.text_cls,
            "recognition": raw_engine.text_rec,
        })
        self._settings()

    @property
    def raw_engine(self):
        """Explicit raw ownership for trusted recorder composition, not a gate."""
        return self._raw

    def __getattr__(self, name):
        return getattr(self._raw, name)

    def __setattr__(self, name, value):
        if name.startswith("_") or name == "raw_engine" or self._lock.locked():
            raise EngineGuardError("OCR guard state cannot be changed during execution")
        setattr(self._raw, name, value)

    def __delattr__(self, name):
        if name.startswith("_") or name == "raw_engine" or self._lock.locked():
            raise EngineGuardError("OCR guard state cannot be changed during execution")
        delattr(self._raw, name)

    def _settings(self):
        raw, components = self._raw, self._components
        try:
            checks = [
                (raw.min_side_len, 30), (raw.max_side_len, self._limits.max_side),
                (raw.min_height, 30), (raw.width_height_ratio, 8),
                (raw.cfg.Global.max_side_len, self._limits.max_side), (raw.cfg.Global.min_side_len, 30),
                (raw.cfg.Global.use_preprocess_img, True), (raw.cfg.Global.use_vertical_padding, True),
                (raw.return_word_box, False), (raw.return_single_char_box, False),
                (raw.cfg.Global.return_word_box, False), (raw.cfg.Global.return_single_char_box, False),
                (components["detection"].limit_type, "min"), (components["detection"].limit_side_len, 736),
                (components["detection"].postprocess_op.max_candidates, 1000),
                (components["detection"].mean, (0.5, 0.5, 0.5)),
                (components["detection"].std, (0.5, 0.5, 0.5)),
                (components["classification"].cls_image_shape, (3, 48, 192)),
                (components["classification"].cls_batch_num, 6),
                (components["recognition"].rec_image_shape, (3, 48, 320)),
                (components["recognition"].rec_batch_num, 6),
            ]
            score = raw.text_score
            if (type(score) not in (int, float) or not 0 <= score <= 1 or not math.isfinite(score)
                    or type(raw.cfg.Global.text_score) is not type(score) or raw.cfg.Global.text_score != score
                    or not all(_equal(a, b) for a, b in checks)):
                raise EngineGuardError("OCR allocation settings differ from the reviewed recipe")
            for role, attribute in (("detection", "text_det"), ("classification", "text_cls"), ("recognition", "text_rec")):
                component = getattr(raw, attribute)
                # StageRecorder's bounded, read-forwarding decorators retain
                # their delegate in 'wrapped'. Never rely on isinstance spoofing.
                for _ in range(5):
                    if component is components[role]:
                        break
                    component = vars(component).get("wrapped")
                else:
                    raise EngineGuardError("OCR component ownership changed")
                if component is not components[role]:
                    raise EngineGuardError("OCR component ownership changed")
                if not callable(component) or not callable(component.session):
                    raise EngineGuardError("OCR component session is unavailable")
        except (AttributeError, TypeError, ValueError) as error:
            if isinstance(error, EngineGuardError):
                raise
            raise EngineGuardError("OCR allocation settings are unavailable") from None

    def _image(self, image, *, contiguous=False):
        np = self._np
        if (type(image) is not np.ndarray or image.dtype != np.uint8
                or (contiguous and not image.flags.c_contiguous)):
            raise EngineAllocationLimit("OCR requires a bounded BGR uint8 array")
        width, height = image_dimensions(image.shape, limits=self._limits)
        if image.nbytes != width * height * 3:
            raise EngineAllocationLimit("OCR image byte layout differs")
        return width, height

    def _images(self, images, *, role, expected_count=None):
        if type(images) is self._np.ndarray:
            images = (images,)
        elif type(images) not in (list, tuple):
            raise EngineAllocationLimit("OCR crops require a bounded materialized list")
        images = bounded_sequence(images, maximum=2000)
        for image in images:
            self._image(image)
        return validate_crop_shapes((image.shape for image in images), role=role,
                                    limits=self._limits, expected_count=expected_count)

    def _prepare(self, pixels, use_det, use_cls, use_rec):
        if self._broken:
            raise EngineGuardError("OCR guard requires a fresh engine after restoration failure")
        self._settings()
        flags = {}
        for name, supplied in (("use_det", use_det), ("use_cls", use_cls), ("use_rec", use_rec)):
            value = getattr(self._raw, name) if supplied is None else supplied
            if type(value) is not bool:
                raise EngineAllocationLimit("OCR stage selection requires booleans")
            flags[name] = value
        if not any(flags.values()):
            raise EngineAllocationLimit("OCR requires an explicitly enabled stage")
        width, height = self._image(pixels, contiguous=True)
        dimensions = working_dimensions(width, height, detection=flags["use_det"], limits=self._limits)
        if not flags["use_det"]:
            w, h = dimensions["global"]
            for role, enabled in (("classification", flags["use_cls"]), ("recognition", flags["use_rec"])):
                if enabled:
                    validate_crop_shapes(((h, w, 3),), role=role, limits=self._limits)
        return {"source": [width, height], "dimensions": dimensions, "flags": flags}

    def prepare(self, pixels, *, use_det=None, use_cls=None, use_rec=None):
        if not self._lock.acquire(blocking=False):
            raise EngineGuardError("OCR guard is already active")
        try:
            return self._prepare(pixels, use_det, use_cls, use_rec)
        finally:
            self._lock.release()

    def _remember(self, error):
        if self._failure is None:
            object.__setattr__(self, "_failure", error)

    def __call__(self, pixels, *, use_det=None, use_cls=None, use_rec=None, **options):
        if "disposition_frame" in options:
            raise EngineGuardError("OCR disposition requires its trusted recorder")
        return self._invoke(None, pixels, use_det=use_det, use_cls=use_cls, use_rec=use_rec, **options)

    def invoke_observed(self, observer, pixels, *, use_det=None, use_cls=None, use_rec=None,
                        disposition_frame=None, **options):
        """Invoke a trusted synchronous observer after all guard setup succeeds.

        The observer must call its single-use zero-argument dispatch on the
        current thread and return that exact result. Timing surrounds raw
        RapidOCR dispatch, including in-call guards, not setup or restoration.
        This is an internal composition capability, never a CLI callback.
        """
        if not callable(observer):
            raise EngineGuardError("OCR dispatch observer must be callable")
        if disposition_frame is not None and type(disposition_frame) is not DispositionFrame:
            raise EngineGuardError("OCR disposition frame requires fixed internal ownership")
        return self._invoke(observer, pixels, use_det=use_det, use_cls=use_cls, use_rec=use_rec,
                            disposition_frame=disposition_frame, **options)

    def _invoke(self, observer, pixels, *, use_det=None, use_cls=None, use_rec=None,
                disposition_frame=None, **options):
        if not self._lock.acquire(blocking=False):
            raise EngineGuardError("OCR guard is already active")
        saved = []
        object.__setattr__(self, "_failure", None)
        try:
            prepared = self._prepare(pixels, use_det, use_cls, use_rec)
            allowed = {"return_word_box", "return_single_char_box", "text_score", "box_thresh", "unclip_ratio"}
            if set(options) - allowed:
                raise EngineGuardError("OCR call options are unsupported")
            for name, value in options.items():
                if value is None:
                    continue
                expected = (False if name.startswith("return_") else self._raw.text_score if name == "text_score"
                            else getattr(self._components["detection"].postprocess_op, name))
                if not _equal(value, expected):
                    raise EngineGuardError("OCR call cannot alter the reviewed settings")
            raw, dimensions = self._raw, prepared["dimensions"]
            frame = disposition_frame
            if frame is not None and not frame.admit(self, raw, prepared):
                frame = None
            role_state = {}
            detector_empty = False

            def install(owner, name, value):
                local = name in vars(owner)
                original = vars(owner).get(name) if local else getattr(owner, name)
                saved.append((owner, name, local, original))
                setattr(owner, name, value)

            def boundary(function, *, empty_ok=False):
                def wrapped(*args, **kwargs):
                    try:
                        return function(*args, **kwargs)
                    except BaseException as error:
                        if not (empty_ok and detector_empty and type(error) is self._empty_error):
                            self._remember(error)
                        raise
                return wrapped

            def method_call(stage, original, *args):
                # Fixed scalar bookkeeping, never an emitter/callback during
                # native/method failure or cancellation unwinding.
                counts = frame.method_counts[stage]
                counts[0] += 1
                try:
                    result = original(*args)
                except BaseException:
                    counts[2] += 1
                    raise
                counts[1] += 1
                return result

            def session_call(role, original, tensor, *args, **kwargs):
                try:
                    state = role_state.get(role)
                    if state is None or state["offset"] >= len(state["batches"]):
                        raise EngineGuardError("OCR session exceeded its planned call count")
                    if type(tensor) is not self._np.ndarray or tensor.dtype != self._np.float32:
                        raise EngineAllocationLimit("OCR normalized tensor dtype differs")
                    validate_tensor_shape(tensor.shape, role=role, expected=state["batches"][state["offset"]], limits=self._limits)
                    state["offset"] += 1
                    if frame is None:
                        return original(tensor, *args, **kwargs)
                    counts = frame.role_counts[role]
                    counts[3] += 1
                    try:
                        result = original(tensor, *args, **kwargs)
                    except BaseException:
                        counts[5] += 1
                        raise
                    counts[4] += 1
                    return result
                except BaseException as error:
                    self._remember(error)
                    raise

            def role_call(role, original, value, *args, **kwargs):
                nonlocal detector_empty
                try:
                    if role in role_state:
                        raise EngineGuardError("OCR component exceeded its single-call schedule")
                    if role == "detection":
                        width, height = self._image(value)
                        if [width, height] != dimensions.get("padded"):
                            raise EngineAllocationLimit("OCR padded detector input differs")
                        dw, dh = detector_dimensions(width, height, limits=self._limits)
                        batches = ((1, 3, dh, dw),)
                    else:
                        images = value
                        if role == "recognition":
                            if getattr(value, "return_word_box", None) is not False:
                                raise EngineGuardError("OCR word-box work is not admitted")
                            images = value.img
                        batches = self._images(images, role=role)["batches"]
                    role_state[role] = {"batches": batches, "offset": 0}
                    if frame is None:
                        result = original(value, *args, **kwargs)
                    else:
                        counts = frame.role_counts[role]
                        counts[0] += 1
                        if role == "detection":
                            frame.method_counts["detector"][0] += 1
                        try:
                            result = original(value, *args, **kwargs)
                        except BaseException:
                            counts[2] += 1
                            if role == "detection":
                                frame.method_counts["detector"][2] += 1
                            raise
                        counts[1] += 1
                        if role == "detection":
                            frame.method_counts["detector"][1] += 1
                    if role_state[role]["offset"] != len(batches):
                        raise EngineGuardError("OCR component omitted a planned session call")
                    if role == "detection":
                        detector_empty = getattr(result, "boxes", None) is None
                        if frame is not None:
                            frame.emit("detector", result)
                    return result
                except BaseException as error:
                    self._remember(error)
                    raise

            original_preprocess = raw.preprocess_img
            def preprocess(image):
                if list(self._image(image)) != prepared["source"]:
                    raise EngineAllocationLimit("OCR loaded source dimensions differ")
                result = original_preprocess(image)
                if type(result) is not tuple or len(result) != 2 or list(self._image(result[0])) != dimensions["global"]:
                    raise EngineAllocationLimit("OCR Global preprocessing dimensions differ")
                self._ratios(result[1], prepared)
                if frame is not None:
                    frame.emit("preprocess", image, result)
                return result

            original_detect = raw.detect_and_crop
            def detect(image, record):
                if list(self._image(image)) != dimensions["global"] or not prepared["flags"]["use_det"]:
                    raise EngineAllocationLimit("OCR detector route differs")
                self._ratios(record, prepared)
                return original_detect(image, record)

            original_crop = raw.crop_text_regions
            def crop(image, boxes):
                width, height = self._image(image)
                if (type(boxes) is not self._np.ndarray or boxes.dtype not in (self._np.float32, self._np.float64)
                        or boxes.ndim != 3 or boxes.shape[1:] != (4, 2) or len(boxes) > 1000):
                    raise EngineAllocationLimit("OCR detector crop geometry is unsupported")
                upper = crop_shapes(boxes, width=width, height=height, limits=self._limits)
                if frame is not None:
                    frame.emit("crop_entry", boxes)
                images = original_crop(image, boxes) if frame is None else method_call("crops", original_crop, image, boxes)
                actual = self._images(images, role="crops", expected_count=len(upper))
                for (h, w, _), (uw, uh) in zip(actual["shapes"], upper):
                    if not ((w <= uw and h <= uh) or (w <= uh and h <= uw)):
                        raise EngineAllocationLimit("OCR rectification exceeded its preflight")
                if frame is not None:
                    frame.emit("crop_return", images)
                return images

            original_cls = raw.cls_and_rotate
            def classify(images):
                before = self._images(images, role="classification")
                if frame is not None:
                    frame.emit("classifier_entry", images)
                result = original_cls(images) if frame is None else method_call("classifier", original_cls, images)
                self._images(result[0], role="classification", expected_count=len(before["shapes"]))
                if frame is not None:
                    frame.emit("classifier_return", result)
                return result

            original_rec = raw.recognize_txt
            def recognize(images):
                self._images(images, role="recognition")
                if frame is not None:
                    frame.emit("recognizer_entry", images)
                result = original_rec(images) if frame is None else method_call("recognizer", original_rec, images)
                if frame is not None:
                    frame.emit("recognizer_return", result)
                return result

            original_final = raw.build_final_output
            def final(image, det_result, cls_result, rec_result, crops, record):
                if list(self._image(image)) != prepared["source"]:
                    raise EngineAllocationLimit("OCR final source dimensions differ")
                ratios = self._ratios(record, prepared)
                metadata = self._images(crops, role="crops")
                if getattr(det_result, "boxes", None) is not None:
                    remapped_crop_shapes(metadata["shapes"], ratio_h=ratios[0], ratio_w=ratios[1], limits=self._limits)
                if frame is not None:
                    frame.emit("formatter_entry", det_result, rec_result, record)
                result = (original_final(image, det_result, cls_result, rec_result, crops, record) if frame is None else
                          method_call("formatter", original_final, image, det_result, cls_result, rec_result, crops, record))
                if frame is not None:
                    frame.emit("formatter_return", result)
                return result

            for role, attribute in (("detection", "text_det"), ("classification", "text_cls"), ("recognition", "text_rec")):
                component = self._components[role]
                install(component, "session", _Proxy(component.session, lambda original, *args, _role=role, **kwargs: session_call(_role, original, *args, **kwargs)))
                install(raw, attribute, _Proxy(getattr(raw, attribute), lambda original, *args, _role=role, **kwargs: role_call(_role, original, *args, **kwargs)))
            for name, function, empty_ok in (("preprocess_img", preprocess, False), ("detect_and_crop", detect, True),
                                             ("crop_text_regions", crop, False), ("cls_and_rotate", classify, False),
                                             ("recognize_txt", recognize, False), ("build_final_output", final, False)):
                install(raw, name, boundary(function, empty_ok=empty_ok))
            if frame is not None:
                original_filter = raw.filter_by_text_score
                def score_filter(result):
                    frame.emit("filter_entry", result)
                    result = method_call("score_filter", original_filter, result)
                    frame.emit("filter_return", result)
                    return result
                install(raw, "filter_by_text_score", boundary(score_filter))
            dispatch_attempts, dispatch_result, dispatch_error = 0, None, None
            dispatch_open, dispatch_thread = True, threading.get_ident()

            def dispatch():
                nonlocal dispatch_attempts, dispatch_result, dispatch_error
                if threading.get_ident() != dispatch_thread:
                    raise EngineGuardError("OCR raw dispatch must remain on its observer thread")
                dispatch_attempts += 1
                if not dispatch_open or dispatch_attempts != 1:
                    raise EngineGuardError("OCR raw dispatch is single-use within its observer")
                try:
                    dispatch_result = raw(pixels, use_det=use_det, use_cls=use_cls, use_rec=use_rec, **options)
                    return dispatch_result
                except BaseException as error:
                    dispatch_error = error
                    raise

            try:
                result = dispatch() if observer is None else observer(dispatch)
            except BaseException as observer_error:
                if (dispatch_error is not None and not isinstance(dispatch_error, Exception)
                        and observer_error is not dispatch_error):
                    raise dispatch_error from observer_error
                raise
            finally:
                dispatch_open = False
            if dispatch_error is not None:
                raise dispatch_error
            if dispatch_attempts != 1 or result is not dispatch_result:
                raise EngineGuardError("OCR dispatch observer omitted or replaced the raw result")
            if self._failure is not None:
                if not isinstance(self._failure, Exception):
                    raise self._failure
                if isinstance(self._failure, EngineAllocationLimit):
                    raise EngineAllocationLimit("OCR engine suppressed an allocation failure") from self._failure
                raise EngineGuardError("OCR engine suppressed a guarded failure") from self._failure
            if frame is not None:
                frame.emit("guard_result", result)
            return result
        finally:
            primary_error = sys.exc_info()[1]
            restore_error = None
            for owner, name, local, original in reversed(saved):
                try:
                    if local:
                        setattr(owner, name, original)
                    elif name in vars(owner):
                        delattr(owner, name)
                except BaseException as error:
                    # Continue every restoration, but never let a later ordinary
                    # cleanup error erase a cancellation already observed here.
                    if restore_error is None or isinstance(restore_error, Exception):
                        restore_error = error
            object.__setattr__(self, "_failure", None)
            if restore_error is not None:
                object.__setattr__(self, "_broken", True)
            self._lock.release()
            if restore_error is not None:
                if primary_error is not None and not isinstance(primary_error, Exception):
                    raise primary_error from restore_error
                if not isinstance(restore_error, Exception):
                    raise restore_error
                raise EngineGuardError("OCR guard could not restore instance hooks") from restore_error

    def _ratios(self, record, prepared):
        if type(record) is not dict or set(record) - {"preprocess", "padding_1"} or "preprocess" not in record:
            raise EngineAllocationLimit("OCR remap record is invalid")
        value = record["preprocess"]
        if type(value) is not dict or set(value) != {"ratio_h", "ratio_w"}:
            raise EngineAllocationLimit("OCR remap record is invalid")
        sw, sh = prepared["source"]
        gw, gh = prepared["dimensions"]["global"]
        expected = (sh / gh, sw / gw)
        actual = (value["ratio_h"], value["ratio_w"])
        if any(type(item) not in (int, float) or item != goal or not math.isfinite(item) for item, goal in zip(actual, expected)):
            raise EngineAllocationLimit("OCR remap ratios differ from actual preprocessing")
        if "padding_1" in record:
            pw, ph = prepared["dimensions"].get("padded", (gw, gh))
            if record["padding_1"] != {"top": (ph - gh) // 2, "left": 0} or pw != gw:
                raise EngineAllocationLimit("OCR padding record differs")
        return actual
