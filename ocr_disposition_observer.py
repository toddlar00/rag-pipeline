"""Fixed inward, optional same-call diagnostic frames; no report or reader imports.

This is local observation, not authentication. Ordinary diagnostic failures
disable only the ledger. Cancellation is never swallowed. Image/model objects
are not retained; detached bounded geometry and text fingerprints are retained.
"""
from __future__ import annotations

import copy
import hashlib
import importlib.metadata
import json
import math
import re
import threading


_PINS = {
    "main.py": "c2ae17098dde838ac3d2933eec5b218d5c4404ded9fe5d32df7c24fd0e54aa39",
    "ch_ppocr_det/main.py": "a56c0f51fd6a8c03a5abf2f0a5843b382b8f3257d1bfacf8d5ac6305d5e63024",
    "ch_ppocr_det/utils.py": "01d25a0b1bbdcdd4aba70a23ae96714c5408df93b295c43ca194952e279adb9e",
    "ch_ppocr_cls/main.py": "a48b3197d5588f035668f5adfc6d60006fcd851049a840ad03718c57cead2c45",
    "ch_ppocr_cls/utils.py": "bcadd799e971fe42a2935c2568ad9a0299e24b6e8c55f57abced42eaac324cd9",
    "ch_ppocr_rec/main.py": "84b7a55a8972d14a92800b66facc73976b8d0b06bd8888e551414dbec8d6d326",
    "ch_ppocr_rec/typings.py": "02b88178edab41b275d14bf8f61ce7148c13328f79f330b78f7c3cb77f7c4737",
    "utils/process_img.py": "abaf2ed615878f618a372cba6157bc6c41a494e05f6c41bf9f7c5c2ba1ee5772",
    "utils/utils.py": "86f8687db6424714be5a6fbd283f6e3b7b198691fb292efd12622b5b40197582",
    "utils/output.py": "8467bbb4b3c140d1f1e8f214043baa7449a1d30b853af3da24fe26238a436314",
}
_ROLES = ("detection", "classification", "recognition")
_STAGES = ("detector", "crops", "classifier", "recognizer", "formatter", "score_filter")
_RECIPE_THRESHOLDS = (0.0, 0.0, 0.9)
_CALL_IDS = tuple(f"call-{number:04d}" for number in range(1, 21))
_CAPS = {"detections": (1000, 20000), "codepoints": (100000, 1000000),
         "raster_bytes": (300000000, 1500000000)}
_FIELDS = {"detections": "detections_reserved", "codepoints": "codepoints_charged",
           "raster_bytes": "raster_bytes_hashed"}


def canonical_sha256(value):
    return hashlib.sha256(json.dumps(value, ensure_ascii=False, allow_nan=False,
                                    sort_keys=True, separators=(",", ":")).encode("utf-8")).hexdigest()


def verified_recipe():
    """Bounded fixed installed-source check; no models constructed or loaded."""
    distribution = importlib.metadata.distribution("rapidocr")
    if distribution.version != "3.9.2" or importlib.metadata.version("numpy") != "2.5.2":
        return False
    for name, expected in _PINS.items():
        with distribution.locate_file("rapidocr/" + name).open("rb") as stream:
            data = stream.read(1024 * 1024 + 1)
        if not 0 < len(data) <= 1024 * 1024 or hashlib.sha256(data).hexdigest() != expected:
            return False
    return True


def _zero_work():
    return {"detections_reserved": 0, "codepoints_charged": 0, "vertices_charged": 0,
            "raster_bytes_hashed": 0, "exhausted": None}


def _stage():
    return {"state": "not_run", "input_count": None, "output_count": None,
            "box_input": None, "box_output": None}


def _number(value, *, maximum=1):
    # Explicitly allow the pinned NumPy numeric scalar outputs, never bools.
    import numpy as np
    if type(value) not in (int, float, np.float32, np.float64):
        raise ValueError("unsupported diagnostic number")
    if not 0 <= value <= maximum or not math.isfinite(value):
        raise ValueError("invalid diagnostic number")
    return float(value)


def _sequence(value, count=None):
    import numpy as np
    if type(value) not in (list, tuple, np.ndarray) or len(value) > 1000:
        raise ValueError("unbounded diagnostic sequence")
    if type(value) is np.ndarray and value.ndim != 1:
        raise ValueError("diagnostic sequence rank differs")
    if count is not None and len(value) != count:
        raise ValueError("diagnostic sequence count differs")
    return value


def _boxes(value):
    import numpy as np
    if value is None:
        return {"state": "none", "dtype": None, "shape": None}
    if (type(value) is not np.ndarray or value.dtype not in (np.float32, np.float64)
            or not (value.shape == (0,) and value.dtype == np.float64
                    or value.ndim == 3 and value.shape[1:] == (4, 2) and len(value) <= 1000)):
        raise ValueError("invalid diagnostic box array")
    return {"state": "array", "dtype": str(value.dtype), "shape": list(value.shape)}


def _quad(box, width, height, *, positive=False):
    if box.shape != (4, 2):
        raise ValueError("invalid diagnostic quad")
    result = [[_number(x, maximum=width), _number(y, maximum=height)] for x, y in box]
    if positive and sum(result[i][0] * result[(i+1) % 4][1] -
                        result[(i+1) % 4][0] * result[i][1] for i in range(4)) == 0:
        raise ValueError("degenerate detector quad")
    return result


class _BudgetExceeded(Exception):
    pass


class DispatchBinding:
    """Exact-type internal owner shared by the reader and execution recorder."""

    def __init__(self, request_sha256):
        if type(request_sha256) is not str or re.fullmatch("[0-9a-f]{64}", request_sha256) is None:
            raise ValueError("invalid disposition request digest")
        self.request_sha256 = request_sha256
        self.frames = []
        self.active = None
        self.owner_thread = threading.get_ident()
        self.totals = {key: 0 for key in _CAPS}
        self.receipt_owner = None

    def begin(self, item_id, reader, min_score):
        if threading.get_ident() != self.owner_thread or self.active is not None:
            raise ValueError("disposition reader is already active")
        if len(self.frames) >= 20 or any(frame.item_id == item_id for frame in self.frames):
            raise ValueError("disposition attempt schedule differs")
        frame = DispositionFrame(self, item_id, len(self.frames) + 1, reader, min_score)
        self.frames.append(frame)
        self.active = frame
        return frame

    def end(self, frame):
        # Fixed primitive teardown, including while reader cancellation unwinds.
        if self.active is frame:
            self.active = None
            frame.open = False
            frame.reader = None
            frame._detached_boxes = None
            if frame.ticket is not None:
                frame.ticket.guard = None

    def reserve(self, receipt_owner, reader, guard, ordinal):
        """Bind a preallocated ticket before the timed dispatch try/finally.

        No hashing, image access or diagnostic event runs here. The exported
        digest is later derived from these fixed ownership fields, never from
        output text, geometry or elapsed-time matching.
        """
        frame = self.active
        if frame is None:
            return None
        try:
            if (threading.get_ident() != self.owner_thread or not frame.open
                    or frame.reader is not reader or frame.ticket.ordinal is not None
                    or type(ordinal) is not int or not 1 <= ordinal <= 20
                    or self.receipt_owner is not None and self.receipt_owner is not receipt_owner):
                raise ValueError("disposition ticket ownership differs")
            self.receipt_owner = receipt_owner
            ticket = frame.ticket
            ticket.guard, ticket.ordinal, ticket.call_id = guard, ordinal, _CALL_IDS[ordinal - 1]
            return ticket
        except Exception:
            frame.disable("callback_failed")
            return None


class DispatchTicket:
    """Not exported as a capability. Raw status is committed by the recorder."""

    def __init__(self, frame):
        self.frame, self.guard, self.ordinal, self.call_id = frame, None, None, None
        self.raw_status = None
        self.used = False


class DispositionFrame:
    def __init__(self, binding, item_id, attempt_index, reader, min_score):
        self.binding, self.item_id, self.attempt_index = binding, item_id, attempt_index
        self.reader, self.min_score, self.open = reader, min_score, True
        self.ticket = DispatchTicket(self)
        self.disabled = False
        self.reason = None
        self.settings = self.raster = self.prepared = None
        self.work = _zero_work()
        self.stages = {name: _stage() for name in _STAGES}
        self.method_counts = {name: [0, 0, 0] for name in _STAGES}
        self.role_counts = {name: [0, 0, 0, 0, 0, 0] for name in _ROLES}
        self.detection_count = None
        self.detections = []
        self.raw_output = None
        self._detached_boxes = None
        self._filter_expected = None
        self._full_formatter = False

    def disable(self, reason):
        self.disabled, self.reason = True, reason
        self.detections = []
        self.detection_count = None
        self.raw_output = self._detached_boxes = self._filter_expected = None

    def charge(self, kind, units, *, single_limit=None):
        cap, total_cap = _CAPS[kind]
        field = _FIELDS[kind]
        if single_limit is not None and units > single_limit:
            limit, used, requested = "codepoints_per_text", 0, min(units, single_limit + 1)
        elif self.work[field] + units > cap:
            limit, used, requested = kind + "_per_call", self.work[field], min(units, cap + 1)
        elif self.binding.totals[kind] + units > total_cap:
            limit, used, requested = kind + "_total", self.binding.totals[kind], min(units, total_cap + 1)
        else:
            self.work[field] += units
            self.binding.totals[kind] += units
            return
        self.work["exhausted"] = {"limit": limit, "used_before": used, "requested_units": requested}
        raise _BudgetExceeded

    def emit(self, event, *values):
        """All diagnostic reads/copies/hash work stay inside this fixed catch."""
        if self.disabled:
            return
        try:
            if not self.open or threading.get_ident() != self.binding.owner_thread:
                raise ValueError("expired disposition frame")
            self._event(event, values)
        except _BudgetExceeded:
            self.disable("budget_exhausted")
        except ValueError:
            self.disable("lineage_invalid" if event != "admit" else "unsupported_recipe")
        except Exception:
            self.disable("callback_failed" if event != "admit" else "unsupported_recipe")

    def admit(self, guard, raw, prepared):
        ticket = self.ticket
        if (type(ticket) is not DispatchTicket or ticket.guard is not guard or ticket.used
                or ticket.frame is not self or ticket.raw_status is not None):
            self.disable("callback_failed")
            return False
        ticket.used = True
        self.emit("admit", raw, prepared)
        return not self.disabled

    def _text(self, text, score):
        if type(text) is not str:
            raise ValueError("invalid diagnostic text")
        self.charge("codepoints", len(text), single_limit=4096)
        return {"text_kind": "empty" if not text else "nonempty" if text.strip() else "whitespace_only",
                "text_sha256": hashlib.sha256(text.encode("utf-8")).hexdigest(),
                "codepoints": len(text), "score": _number(score)}

    def _operations(self, record):
        if type(record) is not dict or list(record) not in (["preprocess"], ["preprocess", "padding_1"]):
            raise ValueError("diagnostic remap order differs")
        expected = [{"kind": "preprocess", "ratio_h": self.raster["ratio_h"], "ratio_w": self.raster["ratio_w"]},
                    {"kind": "padding_1", "top": self.raster["padding_top"], "left": 0}]
        for name, operation in zip(record, expected):
            if type(record[name]) is not dict or record[name] != {k: v for k, v in operation.items() if k != "kind"}:
                raise ValueError("diagnostic remap values differ")
        self.raster["operations"] = expected[:len(record)]

    def _output(self, result):
        boxes = result.boxes
        metadata = _boxes(boxes)
        if boxes is None:
            if result.txts is not None or result.scores is not None:
                raise ValueError("partial diagnostic output")
            return {"boxes": metadata, "lines": []}
        texts, scores = _sequence(result.txts, len(boxes)), _sequence(result.scores, len(boxes))
        lines = [{**self._text(text, score), "box": _quad(box, self.raster["width"], self.raster["height"])}
                 for box, text, score in zip(boxes, texts, scores)]
        return {"boxes": metadata, "lines": lines}

    def _expected(self, *, filtered=False):
        result = []
        for detection in self.detections:
            rec = detection["recognition"]
            if rec["state"] != "completed" or detection["engine_mapping"] != "verified":
                raise ValueError("incomplete diagnostic lineage")
            if rec["text_kind"] != "nonempty" or filtered and rec["score"] < self.settings["engine_text_score"]:
                continue
            result.append({k: rec[k] for k in ("text_sha256", "codepoints", "text_kind", "score")} |
                          {"box": detection["engine_input_box"]})
        return result

    def _event(self, event, values):
        if event == "admit":
            raw, prepared = values
            self.settings = {**prepared["flags"], "return_word_box": raw.return_word_box,
                "return_single_char_box": raw.return_single_char_box, "engine_text_score": _number(raw.text_score),
                "reader_min_score": _number(self.min_score), "classification_threshold": _number(raw.text_cls.cls_thresh)}
            if (any(self.settings[k] is not True for k in ("use_det", "use_cls", "use_rec"))
                    or any(self.settings[k] is not False for k in ("return_word_box", "return_single_char_box"))):
                self.disable("unsupported_route")
            elif tuple(self.settings[k] for k in ("engine_text_score", "reader_min_score", "classification_threshold")) != _RECIPE_THRESHOLDS:
                self.disable("unsupported_recipe")
            elif not verified_recipe():
                self.disable("unsupported_recipe")
            else:
                self.prepared = copy.deepcopy(prepared)
            return
        if event == "preprocess":
            image, result = values
            width, height = self.prepared["source"]
            import numpy as np
            if (type(image) is not np.ndarray or image.dtype != np.uint8 or not image.flags.c_contiguous
                    or image.shape != (height, width, 3)):
                raise ValueError("diagnostic raster differs")
            self.charge("raster_bytes", image.nbytes)
            digest = hashlib.sha256(memoryview(image).cast("B")).hexdigest()
            gw, gh = self.prepared["dimensions"]["global"]
            pw, ph = self.prepared["dimensions"]["padded"]
            self.raster = {"width": width, "height": height, "pixel_sha256": digest,
                "coordinate_system": "engine_input_bgr_uint8_pixels", "global_width": gw, "global_height": gh,
                "padded_width": pw, "padded_height": ph, "ratio_h": height / gh, "ratio_w": width / gw,
                "padding_top": (ph - gh) // 2, "padding_left": 0, "operations": None, "detector_box_dtype": None}
            self._operations(result[1])
        elif event == "detector":
            result, = values
            boxes = result.boxes
            import numpy as np
            if boxes is not None and (type(boxes) is not np.ndarray or boxes.ndim != 3 or boxes.shape[1:] != (4, 2)):
                raise ValueError("invalid diagnostic detector array")
            count = 0 if boxes is None else len(boxes)
            if boxes is not None and not count:
                raise ValueError("empty detector requires its actual None result")
            self.charge("detections", count)
            metadata = _boxes(boxes)
            self.detection_count = count
            self.raster["detector_box_dtype"] = metadata["dtype"]
            self._detached_boxes = None if boxes is None else boxes.copy()
            self.detections = [{"id": f"d{i:04d}", "ordinal": i,
                "detector_box": _quad(box, self.raster["padded_width"], self.raster["padded_height"], positive=True),
                "detector_score": None, "detector_score_alignment": "upstream_alignment_unavailable",
                "crop_state": "not_run", "classification": {"state": "not_run", "label": None, "score": None, "rotated_180": None},
                "recognition": {"state": "not_run", "text_kind": None, "text_sha256": None, "codepoints": None, "score": None},
                "engine_input_box": None, "engine_mapping": "unobserved"}
                for i, box in enumerate(() if boxes is None else boxes, 1)]
            self.stages["detector"].update(state="completed", output_count=count, box_output=metadata)
        elif event == "crop_entry":
            boxes, = values
            import numpy as np
            if (_boxes(boxes) != self.stages["detector"]["box_output"]
                    or not np.array_equal(boxes, self._detached_boxes)):
                raise ValueError("crop ordinal geometry differs")
            self.stages["crops"].update(state="unobserved", input_count=self.detection_count)
            for detection in self.detections:
                detection["crop_state"] = "unobserved"
        elif event == "crop_return":
            images, = values
            _sequence(images, self.detection_count)
            self.stages["crops"].update(state="completed", output_count=self.detection_count)
            for detection in self.detections:
                detection["crop_state"] = "completed"
        elif event in ("classifier_entry", "recognizer_entry"):
            stage = event.removesuffix("_entry")
            _sequence(values[0], self.detection_count)
            self.stages[stage].update(state="unobserved", input_count=self.detection_count)
            key = "classification" if stage == "classifier" else "recognition"
            for detection in self.detections:
                detection[key]["state"] = "unobserved"
        elif event == "classifier_return":
            result, = values
            _sequence(result[0], self.detection_count)
            classifications = _sequence(result[1].cls_res, self.detection_count)
            detached = []
            for item in classifications:
                if type(item) not in (list, tuple) or len(item) != 2 or type(item[0]) is not str or item[0] not in ("0", "180"):
                    raise ValueError("classifier ordinal result differs")
                score = _number(item[1])
                detached.append({"state": "completed", "label": item[0], "score": score,
                                 "rotated_180": item[0] == "180" and score > self.settings["classification_threshold"]})
            for detection, observation in zip(self.detections, detached):
                detection["classification"] = observation
            self.stages["classifier"].update(state="completed", output_count=self.detection_count)
        elif event == "recognizer_return":
            result, = values
            texts, scores = _sequence(result.txts, self.detection_count), _sequence(result.scores, self.detection_count)
            detached = [{"state": "completed", **self._text(text, score)} for text, score in zip(texts, scores)]
            for detection, observation in zip(self.detections, detached):
                detection["recognition"] = observation
            self.stages["recognizer"].update(state="completed", output_count=self.detection_count)
        elif event == "formatter_entry":
            det, rec, record = values
            self._operations(record)
            metadata = _boxes(det.boxes)
            self.stages["formatter"].update(state="unobserved", input_count=0 if det.boxes is None else len(det.boxes), box_input=metadata)
            self._full_formatter = (self.detection_count == 0 or self.detection_count is not None
                and all(self.stages[k]["state"] == "completed" for k in ("crops", "classifier", "recognizer")))
            if not self._full_formatter:
                return
            if metadata != self.stages["detector"]["box_output"]:
                raise ValueError("formatter detector metadata differs")
            if self.detection_count:
                import numpy as np
                if not np.array_equal(det.boxes, self._detached_boxes) or det.scores is None:
                    raise ValueError("formatter blank branch unavailable")
                _sequence(det.scores, self.detection_count)
                texts, scores = _sequence(rec.txts, self.detection_count), _sequence(rec.scores, self.detection_count)
                actual = [{"state": "completed", **self._text(text, score)} for text, score in zip(texts, scores)]
                if actual != [d["recognition"] for d in self.detections]:
                    raise ValueError("formatter recognition operands changed")
                mapped = self._detached_boxes.copy()
                for operation in reversed(self.raster["operations"]):
                    if operation["kind"] == "padding_1":
                        mapped[:, :, 0] -= operation["left"]
                        mapped[:, :, 1] -= operation["top"]
                    else:
                        mapped[:, :, 0] *= operation["ratio_w"]
                        mapped[:, :, 1] *= operation["ratio_h"]
                mapped = np.where(mapped < 0, 0, mapped)
                mapped[:, :, 0] = np.where(mapped[:, :, 0] > self.raster["width"], self.raster["width"], mapped[:, :, 0])
                mapped[:, :, 1] = np.where(mapped[:, :, 1] > self.raster["height"], self.raster["height"], mapped[:, :, 1])
                for detection, box in zip(self.detections, mapped):
                    detection["engine_input_box"] = _quad(box, self.raster["width"], self.raster["height"])
                    detection["engine_mapping"] = "verified"
        elif event == "filter_entry":
            observed = self._output(values[0])
            if observed["lines"] != self._expected():
                raise ValueError("actual nonblank vector differs")
            self.stages["score_filter"].update(state="unobserved", input_count=len(observed["lines"]), box_input=observed["boxes"])
        elif event == "filter_return":
            observed = self._output(values[0])
            if observed["lines"] != self._expected(filtered=True):
                raise ValueError("actual retained vector differs")
            self.stages["score_filter"].update(state="completed", output_count=len(observed["lines"]), box_output=observed["boxes"])
        elif event == "formatter_return":
            if not self._full_formatter:
                return
            observed = self._output(values[0])
            if observed["lines"] != self._expected(filtered=True):
                raise ValueError("actual formatter vector differs")
            self.stages["formatter"].update(state="completed", output_count=len(observed["lines"]), box_output=observed["boxes"])
        elif event == "guard_result":
            if self.ticket.raw_status != "completed":
                raise ValueError("raw result lacks a committed ticket")
            self.raw_output = self._output(values[0])
        else:
            raise ValueError("unknown fixed diagnostic event")

    def _dispatch_snapshot(self):
        ticket = self.ticket
        if ticket.raw_status is None:
            return None
        digest = canonical_sha256({"request_sha256": self.binding.request_sha256,
            "item_id": self.item_id, "attempt_index": self.attempt_index,
            "call_id": ticket.call_id, "dispatch_ordinal": ticket.ordinal})
        return {"call_id": ticket.call_id, "ordinal": ticket.ordinal,
                "ticket_sha256": digest, "raw_status": ticket.raw_status}

    def fallback_snapshot(self):
        """No deep-copy, text, geometry or callback work in this bounded fallback.

        Like every report hash/write, its tiny fixed envelope still requires a
        functioning Python runtime; it is not a global-memory-exhaustion promise.
        """
        dispatch = self._dispatch_snapshot()
        return {"item_id": self.item_id, "attempt_index": self.attempt_index, "dispatch": dispatch,
            "diagnostic_state": "unavailable" if dispatch is not None else "not_run",
            "diagnostic_reason": "callback_failed" if dispatch is not None else "no_raw_dispatch",
            "settings": None, "raster": None, "roles": None, "stages": None,
            "detection_count": None, "detections": [], "raw_output": None,
            "work": ({key: dict(value) if key == "exhausted" and value is not None else value
                      for key, value in self.work.items()} if dispatch is not None else _zero_work())}

    def snapshot(self):
        try:
            return self._snapshot()
        except Exception:
            return self.fallback_snapshot()

    def _snapshot(self):
        ticket = self.ticket
        dispatched = ticket is not None and ticket.raw_status is not None
        base = {"item_id": self.item_id, "attempt_index": self.attempt_index, "dispatch": None,
            "diagnostic_state": "not_run", "diagnostic_reason": "no_raw_dispatch", "settings": None,
            "raster": None, "roles": None, "stages": None, "detection_count": None, "detections": [],
            "raw_output": None, "work": _zero_work()}
        if not dispatched:
            return base
        base.update(dispatch=self._dispatch_snapshot(),
                    settings=self.settings, raster=self.raster, work=self.work)
        if self.disabled:
            base.update(diagnostic_state="unavailable", diagnostic_reason=self.reason)
        else:
            stages = copy.deepcopy(self.stages)
            detections = copy.deepcopy(self.detections)
            for name, counts in self.method_counts.items():
                stage = stages[name]
                if counts[0] == 0:
                    stages[name] = _stage()
                elif counts[2]:
                    stage.update(state="failed", output_count=None, box_output=None)
                elif stage["state"] != "completed":
                    stage.update(state="unobserved", output_count=None, box_output=None)
            for key, name in (("crop_state", "crops"), ("classification", "classifier"), ("recognition", "recognizer")):
                status = stages[name]["state"]
                state = "completed" if status == "completed" else "not_run" if status == "not_run" else "unobserved"
                for detection in detections:
                    if key == "crop_state":
                        detection[key] = state
                    elif state != "completed":
                        detection[key] = {k: state if k == "state" else None for k in detection[key]}
            roles = {}
            for name, counts in self.role_counts.items():
                attempted, completed, failed, sa, sc, sf = counts
                roles[name] = {"state": "failed" if failed else "completed" if completed else "not_run",
                    "attempted": attempted, "completed": completed, "failed": failed,
                    "session_attempted": sa, "session_completed": sc, "session_failed": sf}
            count = self.detection_count
            complete = (self.raw_output is not None and stages["formatter"]["state"] == "completed"
                        and self.detection_count is not None and ticket.raw_status == "completed"
                        and all((row["attempted"], row["completed"], row["failed"], row["session_attempted"],
                                 row["session_completed"], row["session_failed"]) ==
                                ((1, 1, 0, 1, 1, 0) if name == "detection" else
                                 (1, 1, 0, (count + 5) // 6, (count + 5) // 6, 0) if count else (0, 0, 0, 0, 0, 0))
                                for name, row in roles.items())
                        and (count == 0 or all(stages[k]["state"] == "completed"
                            for k in ("crops", "classifier", "recognizer", "score_filter"))))
            base.update(diagnostic_state="complete" if complete else "partial",
                        diagnostic_reason=None if complete else "ocr_path_incomplete", roles=roles,
                        stages=stages, detection_count=self.detection_count, detections=detections,
                        raw_output=self.raw_output if ticket.raw_status == "completed" else None)
        return copy.deepcopy(base)
