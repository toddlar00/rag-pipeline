"""Bounded, observed RapidOCR stage calls on one original displayed raster.

The detector observation is postprocessed, before recognition filtering. Gold
line crops use the public recognition-only route, including its normalization;
they are not claims about raw neural tensors or independently attested execution.
"""

from __future__ import annotations

import hashlib
import math
from pathlib import Path
import struct
import sys
import time

from ocr_engine_guard import RapidOCREngineGuard
from ocr_engine_limits import EngineAllocationLimit, working_dimensions as _engine_working_dimensions
from ocr_recovery_runtime import RapidOCRPageReader, _finite_number, _sequence


MAX_SIDE = 6000
MAX_PAGE_PIXELS = 25_000_000
MAX_TOTAL_PIXELS = 100_000_000
MAX_BOXES = 2000
MAX_TEXT = 100_000
MAX_LINE_TEXT = 4096
MAX_CALLS = 80
_ROLES = {"detection": "text_det", "classification": "text_cls", "recognition": "text_rec"}
_MODES = {"detection": (True, False, False), "full_page": (True, True, True),
          "gold_crop": (False, False, True)}


class StageResourceLimit(ValueError):
    """A planned native allocation exceeds this opt-in recipe's bounds."""


def working_dimensions(width: int, height: int, *, detection: bool) -> dict:
    """Preflight pinned Global resize/padding and detector min-side expansion.

    This mirrors integer/32-pixel rounding before any native array allocation.
    Original rasters already satisfy max_side, so Global reduce_max_side cannot
    run; increase_min_side must still be checked independently afterward.
    """
    try:
        return _engine_working_dimensions(width, height, detection=detection)
    except EngineAllocationLimit as error:
        raise StageResourceLimit("stage working raster exceeds bounds") from error


def pixel_digest(pixels) -> str:
    """Domain-separated BGR uint8 array-content digest, not a file hash."""
    import numpy as np

    if (not isinstance(pixels, np.ndarray) or pixels.dtype != np.uint8
            or pixels.ndim != 3 or pixels.shape[2] != 3 or not pixels.flags.c_contiguous):
        raise ValueError("stage raster must be contiguous BGR uint8")
    height, width = pixels.shape[:2]
    if not 1 <= width <= MAX_SIDE or not 1 <= height <= MAX_SIDE or width * height > MAX_PAGE_PIXELS:
        raise ValueError("stage raster exceeds bounds")
    digest = hashlib.sha256(b"ocr-stage-bgr-uint8-v1\0" + struct.pack(">II", width, height))
    digest.update(memoryview(pixels).cast("B"))
    return digest.hexdigest()


def _quad(value, width: int, height: int) -> list[list[float]]:
    points = _sequence(value, "stage box", maximum=4)
    if len(points) != 4:
        raise ValueError("stage box requires four points")
    result = []
    for raw in points:
        point = _sequence(raw, "stage point", maximum=2)
        if len(point) != 2:
            raise ValueError("stage point requires two coordinates")
        x, y = (_finite_number(item, "stage coordinate") for item in point)
        if not 0 <= x <= width or not 0 <= y <= height:
            raise ValueError("stage box is outside its raster")
        result.append([x, y])
    turns = [((result[(i + 1) % 4][0] - result[i][0])
              * (result[(i + 2) % 4][1] - result[(i + 1) % 4][1])
              - (result[(i + 1) % 4][1] - result[i][1])
              * (result[(i + 2) % 4][0] - result[(i + 1) % 4][0])) for i in range(4)]
    if not (all(turn > 0 for turn in turns) or all(turn < 0 for turn in turns)):
        raise ValueError("stage box must be a nondegenerate convex quadrilateral")
    return result


def _boxes(result, width: int, height: int) -> list:
    if not hasattr(result, "boxes"):
        raise ValueError("detector output is unavailable")
    boxes = result.boxes
    if boxes is None:
        return []
    return [_quad(box, width, height) for box in _sequence(boxes, "stage detections", maximum=MAX_BOXES)]


def _full_lines(result, width: int, height: int) -> list:
    texts, boxes = getattr(result, "txts", None), getattr(result, "boxes", None)
    if texts is None and boxes is None:
        return []
    if texts is None or boxes is None:
        raise ValueError("full stage output is incomplete")
    texts = _sequence(texts, "stage text", maximum=MAX_BOXES)
    boxes = _sequence(boxes, "stage boxes", maximum=MAX_BOXES)
    if len(texts) != len(boxes):
        raise ValueError("full stage text and box counts differ")
    total, output = 0, []
    for text, box in zip(texts, boxes):
        if not isinstance(text, str) or not text.strip() or len(text) > MAX_LINE_TEXT:
            raise ValueError("stage line text exceeds bounds")
        total += len(text)
        if total > MAX_TEXT:
            raise ValueError("stage page text exceeds bounds")
        output.append({"text": text, "box": _quad(box, width, height)})
    return output


def _crop_text(result) -> str:
    texts = getattr(result, "txts", None)
    if texts is None:
        raise ValueError("recognition output is unavailable")
    texts = _sequence(texts, "gold recognition", maximum=1)
    if len(texts) != 1 or not isinstance(texts[0], str) or len(texts[0]) > MAX_LINE_TEXT:
        raise ValueError("gold recognition requires exactly one bounded string")
    return texts[0]


def _recognition_inputs(value) -> None:
    images = _sequence(getattr(value, "img", None), "recognition inputs", maximum=MAX_BOXES)
    total = 0
    for image in images:
        if not hasattr(image, "shape") or len(image.shape) != 3:
            raise ValueError("recognition input is not an image")
        height, width = image.shape[:2]
        if (not 1 <= width <= MAX_SIDE or not 1 <= height <= MAX_SIDE
                or max(320, math.ceil(48 * width / height)) > 4096):
            raise ValueError("recognition tensor width exceeds bounds")
        total += width * height
        if total > MAX_TOTAL_PIXELS:
            raise ValueError("recognition crop pixels exceed bounds")


def _detected_crop_budget(result, image) -> None:
    """Preflight crops before the public engine allocates rectified arrays."""
    height, width = image.shape[:2]
    boxes = _boxes(result, width, height)
    total = 0
    for box in boxes:
        crop_width = int(max(math.dist(box[0], box[1]), math.dist(box[2], box[3])))
        crop_height = int(max(math.dist(box[0], box[3]), math.dist(box[1], box[2])))
        if not 1 <= crop_width <= MAX_SIDE or not 1 <= crop_height <= MAX_SIDE:
            raise ValueError("detected crop exceeds bounds")
        total += crop_width * crop_height
        if total > MAX_TOTAL_PIXELS:
            raise ValueError("detected crop aggregate exceeds bounds")


def _empty(stage: str, reason: str, *, region_id=None) -> dict:
    result = {"status": "unavailable", "reason": reason, "call_id": None}
    if stage == "gold_crop":
        result.update(region_id=region_id, raster_bbox=None, pixel_sha256=None, text=None)
    else:
        result["boxes" if stage == "detection" else "lines"] = []
    return result


class StageRecorder:
    """Observe raw guarded dispatch and real top-level component calls.

    Known guards exclude setup/teardown from call counts and elapsed time.
    Generic engines retain their submitted-callable scope. Completed dispatch
    need not imply an accepted candidate; role failures remain separate.
    Counts are local observations, not authenticated execution attestations.
    """

    def __init__(self):
        self.calls = []
        self.receipt_calls = []
        self.observation = None
        self._active = None
        self._broken = False
        self.failure_reasons = {}
        self.recognition_returned = {}

    def invoke(self, engine, pixels, *, stage: str, page_number: int, region_id=None):
        if self._broken:
            raise ValueError("stage recorder requires fresh ownership after restoration failure")
        if len(self.calls) >= MAX_CALLS or self._active is not None:
            raise ValueError("stage call schedule exceeds bounds")
        flags = _MODES[stage]
        working_dimensions(int(pixels.shape[1]), int(pixels.shape[0]), detection=flags[0])
        call = {"id": f"call-{len(self.calls) + 1:04d}", "stage": stage,
                "page_number": page_number, "region_id": region_id,
                **dict(zip(("use_det", "use_cls", "use_rec"), flags)), "status": "failed",
                "roles": {role: {"attempted": 0, "completed": 0, "failed": 0} for role in _ROLES}}
        recorder, originals = self, {}

        class Probe:
            def __init__(self, wrapped, role):
                self.wrapped, self.role = wrapped, role

            def __getattr__(self, name):
                return getattr(self.wrapped, name)

            def __call__(self, *args, **kwargs):
                counts = recorder._active["roles"][self.role]
                if counts["attempted"]:
                    raise ValueError("stage component exceeded its single-call budget")
                if self.role == "recognition":
                    try:
                        _recognition_inputs(args[0])
                    except Exception:
                        recorder.failure_reasons[call["id"]] = "resource_limit"
                        raise
                counts["attempted"] += 1
                try:
                    result = self.wrapped(*args, **kwargs)
                except BaseException:
                    counts["failed"] += 1
                    raise
                counts["completed"] += 1
                if self.role == "recognition":
                    # The full-page formatter discards all-empty recognized
                    # strings and returns a default output. Distinguish that
                    # observed emptiness from a default missing recognizer result.
                    texts = getattr(result, "txts", None)
                    try:
                        texts = _sequence(texts, "recognition output", maximum=MAX_BOXES)
                        valid = (len(texts) == len(args[0].img)
                                 and all(isinstance(text, str) and len(text) <= MAX_LINE_TEXT for text in texts)
                                 and sum(map(len, texts)) <= MAX_TEXT)
                    except Exception:
                        valid = False
                    recorder.recognition_returned[call["id"]] = valid
                if self.role == "detection":
                    try:
                        _detected_crop_budget(result, args[0])
                    except Exception:
                        recorder.failure_reasons[call["id"]] = "resource_limit"
                        raise
                return result

        def observe(dispatch):
            started = time.monotonic()
            try:
                result = dispatch()
                call["status"] = "completed"
                return result
            finally:
                elapsed = time.monotonic() - started
                # Persist actual dispatch even if guard/probe restoration later
                # fails. Setup failures never enter this observer.
                self.calls.append(call)
                self.receipt_calls.append({"id": call["id"], "status": call["status"], "elapsed_seconds": elapsed})

        # Reserve ownership through setup and teardown without recording a call.
        self._active = call
        try:
            for role, attribute in _ROLES.items():
                originals[attribute] = getattr(engine, attribute)
                setattr(engine, attribute, Probe(originals[attribute], role))
            if type(engine) is RapidOCREngineGuard:
                result = engine.invoke_observed(observe, pixels.copy(),
                                                use_det=flags[0], use_cls=flags[1], use_rec=flags[2])
            else:
                result = observe(lambda: engine(pixels.copy(),
                                                use_det=flags[0], use_cls=flags[1], use_rec=flags[2]))
            return result, call
        finally:
            primary_error, restore_error = sys.exc_info()[1], None
            try:
                for attribute, original in reversed(list(originals.items())):
                    try:
                        setattr(engine, attribute, original)
                    except BaseException as error:
                        if restore_error is None or isinstance(restore_error, Exception):
                            restore_error = error
            finally:
                self._active = None
            if restore_error is not None:
                self._broken = True
                if primary_error is not None and not isinstance(primary_error, Exception):
                    raise primary_error from restore_error
                if not isinstance(restore_error, Exception):
                    raise restore_error
                raise RuntimeError("stage recorder could not restore component hooks") from restore_error


class RapidOCRStageReader(RapidOCRPageReader):
    """Use the existing verified model loader, but keep stage observations apart."""

    def __init__(self, source: bytes, *, dpi: int = 300, recorder: StageRecorder):
        if not isinstance(source, bytes) or not 1 <= len(source) <= 256 * 1024 * 1024:
            raise ValueError("stage source snapshot exceeds bounds")
        super().__init__(Path("stage-source.pdf"), dpi=dpi, max_pixels=MAX_PAGE_PIXELS, max_side=MAX_SIDE)
        self._source_bytes = source
        self.recorder = recorder

    def __enter__(self):
        if self._document is not None:
            raise RuntimeError("stage reader is already open")
        import pymupdf

        document = pymupdf.open(stream=self._source_bytes, filetype="pdf")
        try:
            if not document.is_pdf or document.needs_pass or not 1 <= len(document) <= 5000:
                raise ValueError("stage source requires a bounded unlocked PDF")
        except BaseException:
            document.close()
            raise
        self._document, self._pymupdf, self._page_count = document, pymupdf, len(document)
        return self

    def close(self):
        try:
            if self._engine is not None:
                self.recorder.observation = self.execution_observation()
        finally:
            super().close()

    def _load_engine(self):
        engine = super()._load_engine()
        if (list(engine.text_rec.rec_image_shape) != [3, 48, 320]
                or engine.text_rec.rec_batch_num != 6
                or engine.min_side_len != 30 or engine.max_side_len != MAX_SIDE
                or engine.min_height != 30 or engine.width_height_ratio != 8
                or engine.cfg.Global.use_preprocess_img is not True
                or engine.cfg.Global.use_vertical_padding is not True
                or engine.text_det.limit_type != "min" or engine.text_det.limit_side_len != 736
                or engine.text_det.postprocess_op.max_candidates != 1000):
            raise ValueError("stage recognizer normalization differs from bounded recipe")
        return engine

    def preflight(self, reference: dict) -> list[dict]:
        if self.page_count != reference["page_count"]:
            raise ValueError("stage source page count differs from gold")
        plans, total_pixels = [], 0
        for gold in reference["pages"]:
            plan = {"gold": gold, "geometry": None, "reason": None, "width": None, "height": None}
            try:
                page = self._page(gold["page_number"])
                geometry = {"width_points": _finite_number(page.rect.width, "page width"),
                            "height_points": _finite_number(page.rect.height, "page height"),
                            "rotation": page.rotation}
            except Exception:
                plan["reason"] = "geometry_unavailable"
                plans.append(plan)
                continue
            if geometry != gold["geometry"] or type(geometry["rotation"]) is not int:
                raise ValueError("actual stage page geometry differs from gold")
            plan["geometry"] = geometry
            rect = (page.rect * self._pymupdf.Matrix(self.dpi / 72, self.dpi / 72)).irect
            width, height = rect.width, rect.height
            if (rect.x0 != 0 or rect.y0 != 0 or not 1 <= width <= MAX_SIDE
                    or not 1 <= height <= MAX_SIDE or width * height > MAX_PAGE_PIXELS):
                plan["reason"] = "resource_limit"
            else:
                try:
                    working_dimensions(width, height, detection=True)
                except StageResourceLimit:
                    plan["reason"] = "resource_limit"
                else:
                    plan.update(width=width, height=height)
                    total_pixels += width * height
            plans.append(plan)
        if total_pixels > MAX_TOTAL_PIXELS:
            raise ValueError("stage selected pages exceed aggregate raster budget")
        return plans

    def render(self, plan: dict):
        import numpy as np

        width, height = plan["width"], plan["height"]
        pixmap = self._page(plan["gold"]["page_number"]).get_pixmap(
            matrix=self._pymupdf.Matrix(self.dpi / 72, self.dpi / 72),
            colorspace=self._pymupdf.csRGB, alpha=False)
        if (pixmap.width != width or pixmap.height != height or pixmap.n != 3
                or pixmap.stride != width * 3 or len(pixmap.samples_mv) != width * height * 3):
            raise ValueError("stage renderer returned an unexpected raster")
        return np.frombuffer(pixmap.samples_mv, dtype=np.uint8).reshape(height, width, 3)[:, :, ::-1].copy()

    def stage(self, pixels, *, stage: str, page_number: int, region_id=None) -> dict:
        output = _empty(stage, "stage_failed", region_id=region_id)
        count = len(self.recorder.calls)
        try:
            working_dimensions(int(pixels.shape[1]), int(pixels.shape[0]), detection=stage != "gold_crop")
            engine = self._load_engine()
            flags = _MODES[stage]
            engine.prepare(pixels, use_det=flags[0], use_cls=flags[1], use_rec=flags[2])
            result, call = self.recorder.invoke(engine, pixels, stage=stage,
                                                page_number=page_number, region_id=region_id)
        except (StageResourceLimit, EngineAllocationLimit):
            output["reason"] = "resource_limit"
            if len(self.recorder.calls) > count:
                output["call_id"] = self.recorder.calls[-1]["id"]
            return output
        except Exception:
            if len(self.recorder.calls) > count:
                call = self.recorder.calls[-1]
                output["call_id"] = call["id"]
                # A normally returned raw call is not a failed invocation when
                # post-dispatch guarding/cleanup prevents accepting its output.
                reason = ("invalid_stage_output" if call["status"] == "completed"
                          and not any(role["failed"] for role in call["roles"].values()) else "stage_failed")
                output["reason"] = self.recorder.failure_reasons.get(output["call_id"], reason)
            return output
        output["call_id"] = call["id"]
        if any(role["failed"] for role in call["roles"].values()):
            return output
        expected = {"detection": (1, 0, 0), "gold_crop": (0, 0, 1)}.get(stage)
        counts = tuple(call["roles"][role]["completed"] for role in _ROLES)
        if (expected is not None and counts != expected) or (stage == "full_page" and counts[0] != 1):
            output["reason"] = "invalid_stage_output"
            return output
        try:
            height, width = pixels.shape[:2]
            if stage == "detection":
                value, key = _boxes(result, width, height), "boxes"
            elif stage == "full_page":
                value, key = _full_lines(result, width, height), "lines"
                if value and counts != (1, 1, 1):
                    raise ValueError("full text lacks stage execution evidence")
                if not value and counts not in ((1, 0, 0), (1, 1, 1)):
                    raise ValueError("empty full output lacks a valid completed route")
                if counts == (1, 1, 1) and not self.recorder.recognition_returned.get(call["id"], False):
                    raise ValueError("missing recognition is not an empty result")
            else:
                value, key = _crop_text(result), "text"
            output.update(status="available" if (value.strip() if isinstance(value, str) else value) else "empty",
                          reason=None)
            output[key] = value
        except Exception:
            output["reason"] = "invalid_stage_output"
        return output


def collect_stage_observation(source: bytes, reference: dict, *, reference_sha256: str,
                              configuration: dict, reader_factory=RapidOCRStageReader) -> tuple[dict, StageRecorder]:
    """Render each page once; fresh full OCR and detector-only calls share pixels."""
    from ocr_stage_diagnostics import DEFAULT_CONFIGURATION, validate_stage_observation, validate_stage_reference

    reference = validate_stage_reference(reference)
    if (not isinstance(source, bytes) or not 1 <= len(source) <= 256 * 1024 * 1024
            or hashlib.sha256(source).hexdigest() != reference["source_sha256"]):
        raise ValueError("stage source snapshot differs from gold")
    if configuration != DEFAULT_CONFIGURATION or any(type(configuration[key]) is not type(value)
                                                     for key, value in DEFAULT_CONFIGURATION.items()):
        raise ValueError("stage runtime requires the fixed bounded recipe")
    recorder, pages = StageRecorder(), []
    with reader_factory(source, dpi=configuration["dpi"], recorder=recorder) as reader:
        plans = reader.preflight(reference)  # Entire job admitted before models/rendering.
        for plan in plans:
            gold, reason = plan["gold"], plan["reason"]
            page = {"page_number": gold["page_number"], "geometry": plan["geometry"], "raster": None,
                    "detection": _empty("detection", reason or "render_failed"),
                    "full_page": _empty("full_page", reason or "render_failed"), "gold_crops": []}
            pixels = None
            if reason is None:
                try:
                    pixels = reader.render(plan)
                    page["raster"] = {"width": plan["width"], "height": plan["height"],
                                      "pixel_sha256": pixel_digest(pixels)}
                except Exception:
                    reason = "render_failed"
            if pixels is not None and reason is None:
                for stage in ("detection", "full_page"):
                    page[stage] = reader.stage(pixels, stage=stage, page_number=gold["page_number"])
            lines = {line["region_id"]: line for line in gold["lines"]}
            for region_id in gold["recognition_region_ids"]:
                crop_result = _empty("gold_crop", reason or "resource_limit", region_id=region_id)
                if pixels is not None and reason is None:
                    left, top, right, bottom = lines[region_id]["bbox"]
                    width, height = plan["width"], plan["height"]
                    rect = [math.floor(left * width), math.floor(top * height),
                            math.ceil(right * width), math.ceil(bottom * height)]
                    crop_width, crop_height = rect[2] - rect[0], rect[3] - rect[1]
                    if (crop_width > 0 and crop_height > 0
                            and max(320, math.ceil(48 * crop_width / crop_height))
                            <= configuration["max_normalized_recognition_width"]):
                        try:
                            working_dimensions(crop_width, crop_height, detection=False)
                        except StageResourceLimit:
                            pass
                        else:
                            crop = pixels[rect[1]:rect[3], rect[0]:rect[2]].copy()
                            crop_hash = pixel_digest(crop)
                            crop_result = reader.stage(crop, stage="gold_crop", page_number=gold["page_number"], region_id=region_id)
                            crop_result.update(raster_bbox=rect, pixel_sha256=crop_hash)
                page["gold_crops"].append(crop_result)
            pages.append(page)
    observation = {"schema_version": 1, "kind": "ocr_stage_observation",
                   "source_sha256": reference["source_sha256"], "reference_sha256": reference_sha256,
                   "page_count": reference["page_count"], "configuration": dict(configuration),
                   "pages": pages, "calls": recorder.calls, "canonical_extraction_modified": False,
                   "requires_attention": True}
    return validate_stage_observation(observation, reference, reference_sha256), recorder
