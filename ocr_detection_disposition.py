"""Bounded same-call OCR lineage observations, not accuracy or completeness.

The outer IO composition validates legacy reports/receipts and their exact file
bindings. This leaf reconciles their selected views with detached observations;
it never executes OCR, reads files, or invokes callbacks named by input data.
"""

from __future__ import annotations

from dataclasses import dataclass
import copy
import hashlib
import json
import math
import re
import struct
from types import MappingProxyType
from typing import Callable

from ocr_engine_limits import EngineLimits, working_dimensions


RECIPE_ID = "same-call-full-route-disposition-v1"
SCOPE = "same_execution_component_return_lineage_not_completeness_or_accuracy"
LIMITS = MappingProxyType({
    "max_document_pages": 5000, "max_calls": 20,
    "max_detections_per_call": 1000, "max_detections_total": 20000,
    "max_codepoints_per_text": 4096, "max_codepoints_per_call": 100000,
    "max_codepoints_total": 1000000, "max_polygon_vertices": 128,
    "max_vertices_per_call": 20000, "max_vertices_total": 100000,
    "max_raster_bytes_per_call": 300000000, "max_raster_bytes_total": 1500000000,
    "max_serialized_bytes": 16777216,
})
# Input snapshots include a separate raw-return vector as well as original and
# remapped boxes. They are not the final16MiB companion. A bounded larger input
# allows per-item output fallback rather than rejecting healthy OCR beforehand.
MAX_OBSERVATION_BYTES = 64 * 1024 * 1024
MAX_OBSERVATION_NODES = 4_000_000
_SHA = re.compile(r"[0-9a-f]{64}")
_ROLES = ("detection", "classification", "recognition")
_STAGES = ("detector", "crops", "classifier", "recognizer", "formatter", "score_filter")
_DIAGNOSTIC_KEYS = {"state", "reason", "settings", "raster", "roles", "stages", "mapping", "detection_count", "detections"}
_SNAPSHOT_KEYS = {"item_id", "attempt_index", "dispatch", "diagnostic_state", "diagnostic_reason", "settings", "raster",
                  "roles", "stages", "detection_count", "detections", "raw_output", "work"}
_DETECTION_KEYS = {"id", "ordinal", "detector_box", "detector_score", "detector_score_alignment", "crop_state",
                   "classification", "recognition", "engine_input_box", "engine_mapping"}
_FINAL_DETECTION_KEYS = _DETECTION_KEYS | {"terminal", "output_index", "candidate_disposition", "candidate_index", "physical"}
_ITEM_KEYS = {"item_id", "page_number", "region_id", "selection", "explicitly_requested", "attempt_index", "legacy_status",
              "legacy_record_sha256", "dispatch", "candidate", "diagnostic", "work"}
_BINDINGS = {"request_sha256", "report_sha256", "execution_sha256", "producer_generation_sha256", "source_sha256"}
_REASONS = {"unsupported_route", "unsupported_recipe", "callback_failed", "lineage_invalid", "budget_exhausted"}
_PHYSICAL_REASONS = {"report_geometry_unavailable", "engine_mapping_unobserved", "no_positive_source_support", "mapping_limit", "mapping_failed"}
_TEXT_KINDS = {"empty", "whitespace_only", "nonempty"}
_EXHAUSTION = {
    "detections_per_call": 1000, "detections_total": 20000, "codepoints_per_text": 4096,
    "codepoints_per_call": 100000, "codepoints_total": 1000000, "vertices_per_call": 20000,
    "vertices_total": 100000, "polygon_vertices": 128, "raster_bytes_per_call": 300000000,
    "raster_bytes_total": 1500000000, "serialized_bytes": 16777216,
}
_WORK_CAPS = {"detections_reserved": 1000, "codepoints_charged": 100000,
              "vertices_charged": 20000, "raster_bytes_hashed": 300000000}
_ERRORS = {"retry_limit_or_validation", "retry_runtime_unavailable", "retry_runtime_failed"}
_ITEM_SLOT = 2048
_TOP_SLOT = 65536


@dataclass(frozen=True)
class DispositionDerivation:
    """One internal, source-bound derivation generation; never JSON callbacks."""

    replay_engine_box: Callable
    map_source_polygon: Callable

    def __post_init__(self):
        if not callable(self.replay_engine_box) or not callable(self.map_source_polygon):
            raise ValueError("invalid disposition derivation binding")


def _fail():
    raise ValueError("invalid or contradictory OCR disposition evidence")


def _fields(value, keys):
    if type(value) is not dict or set(value) != set(keys):
        _fail()
    return value


def _int(value, minimum=0, maximum=100000):
    if type(value) is not int or not minimum <= value <= maximum:
        _fail()
    return value


def _number(value, minimum=0, maximum=12000):
    if type(value) not in (int, float) or not minimum <= value <= maximum or not math.isfinite(value):
        _fail()
    return value


def _sha(value):
    if type(value) is not str or _SHA.fullmatch(value) is None:
        _fail()
    return value


def _list(value, maximum, minimum=0):
    if type(value) is not list or not minimum <= len(value) <= maximum:
        _fail()
    return value


def _enum(value, choices):
    if type(value) is not str or value not in choices:
        _fail()
    return value


def _canonical(value):
    return json.dumps(value, sort_keys=True, ensure_ascii=False, allow_nan=False, separators=(",", ":"))


def canonical_sha256(value):
    """Canonical plain-data hash; artifact byte hashes are supplied by IO."""
    _preflight(value)
    return hashlib.sha256(_canonical(value).encode("utf-8")).hexdigest()


def _same(left, right):
    return _canonical(left) == _canonical(right)


def _preflight(value, maximum=16777216, *, maximum_nodes=2000000):
    # Validate before deepcopy/JSON so arbitrary objects and recursive graphs
    # cannot invoke hooks or exhaust recursion. Aliases are charged per visit.
    stack, active, nodes = [(value, 0, False)], set(), 0
    while stack:
        node, depth, leaving = stack.pop()
        if leaving:
            active.remove(id(node))
            continue
        nodes += 1
        if nodes > maximum_nodes or depth > 32:
            _fail()
        kind = type(node)
        if kind in (dict, list):
            if id(node) in active or len(node) > 100000:
                _fail()
            active.add(id(node))
            stack.append((node, depth, True))
            if kind is dict:
                if any(type(key) is not str or len(key) > 128 for key in node):
                    _fail()
                stack.extend((item, depth + 1, False) for item in node.values())
            else:
                stack.extend((item, depth + 1, False) for item in node)
        elif kind is str:
            if len(node) > 1000000 or any(0xD800 <= ord(char) <= 0xDFFF for char in node):
                _fail()
        elif kind is int:
            if node.bit_length() > 64:
                _fail()
        elif kind is float:
            if not math.isfinite(node):
                _fail()
        elif node is not None and kind is not bool:
            _fail()
    size = 1
    for part in json.JSONEncoder(sort_keys=True, ensure_ascii=False, allow_nan=False, separators=(",", ":")).iterencode(value):
        size += len(part.encode("utf-8"))
        if size > maximum:
            _fail()
    return size


def _quad(value, width, height, *, positive=False, dtype=None):
    for point in _list(value, 4, 4):
        _list(point, 2, 2)
        for coordinate, limit in zip(point, (width, height)):
            _number(coordinate, maximum=limit)
            if dtype == "float32" and struct.unpack("!f", struct.pack("!f", coordinate))[0] != coordinate:
                _fail()
    if positive and sum(value[i][0]*value[(i+1)%4][1] - value[(i+1)%4][0]*value[i][1] for i in range(4)) == 0:
        _fail()
    return value


def _box_array(value, count=None):
    if value is None:
        return
    _fields(value, {"state", "dtype", "shape"})
    if value["state"] == "none":
        if value["dtype"] is not None or value["shape"] is not None or count not in (None, 0):
            _fail()
    elif value["state"] == "array":
        _enum(value["dtype"], {"float32", "float64"})
        shape = _list(value["shape"], 3, 1)
        for number in shape:
            _int(number, maximum=1000)
        if shape != [0] and (len(shape) != 3 or shape[1:] != [4, 2]):
            _fail()
        if shape == [0] and value["dtype"] != "float64":
            _fail()
        if count is not None and shape[0] != count:
            _fail()
    else:
        _fail()


def _physical(reason):
    return {"state": "unavailable", "reason": reason, "polygon": None,
            "coordinate_system": "original_page_display_fraction", "edge_model": None, "max_error_source_pixels": None}


def _physical_checked(value):
    _fields(value, {"state", "reason", "polygon", "coordinate_system", "edge_model", "max_error_source_pixels"})
    if value["coordinate_system"] != "original_page_display_fraction":
        _fail()
    if value["state"] == "unavailable":
        _enum(value["reason"], _PHYSICAL_REASONS)
        if any(value[key] is not None for key in ("polygon", "edge_model", "max_error_source_pixels")):
            _fail()
        return value
    if value["state"] != "available" or value["reason"] is not None:
        _fail()
    edge = _enum(value["edge_model"], {"linear", "sampled_nonlinear_inverse"})
    if type(value["max_error_source_pixels"]) not in (int, float) or value["max_error_source_pixels"] != (0.0 if edge == "linear" else 0.5):
        _fail()
    points = _list(value["polygon"], 128, 3 if edge == "linear" else 4)
    for point in points:
        for coordinate in _list(point, 2, 2):
            _number(coordinate, maximum=1)
    if sum(points[i][0]*points[(i+1)%len(points)][1] - points[(i+1)%len(points)][0]*points[i][1] for i in range(len(points))) == 0:
        _fail()
    return value


def zero_work():
    return {**dict.fromkeys(_WORK_CAPS, 0), "exhausted": None}


def _work(value):
    _fields(value, set(_WORK_CAPS) | {"exhausted"})
    for name, cap in _WORK_CAPS.items():
        _int(value[name], maximum=cap)
    if value["exhausted"] is not None:
        exhausted = _fields(value["exhausted"], {"limit", "used_before", "requested_units"})
        cap = _EXHAUSTION[_enum(exhausted["limit"], _EXHAUSTION)]
        used = _int(exhausted["used_before"], maximum=cap)
        requested = _int(exhausted["requested_units"], 1, cap + 1)
        if requested <= cap - used:
            _fail()
        if exhausted["limit"] in {"codepoints_per_text", "polygon_vertices"} and used:
            _fail()
    return value


def _not_run(reason):
    return {"state": "not_run", "reason": reason, "settings": None, "raster": None, "roles": None,
            "stages": None, "mapping": None, "detection_count": None, "detections": []}


def _unavailable(value, reason):
    result = _not_run(reason)
    result.update(state="unavailable", settings=value.get("settings"), raster=value.get("raster"))
    return result


def _context(request, report, execution, bindings):
    for value, maximum in ((request, 1048576), (report, 134217728), (execution, 4194304), (bindings, 4096)):
        _preflight(value, maximum)
    _fields(bindings, _BINDINGS)
    for digest in bindings.values():
        _sha(digest)
    _fields(request, {"schema_version", "kind", "operation", "configuration", "inputs", "recipe_id", "limits", "producer_generation_sha256"})
    if (type(request["schema_version"]) is not int or request["schema_version"] != 1 or request["kind"] != "ocr_disposition_request"
            or request["recipe_id"] != RECIPE_ID or not _same(request["limits"], dict(LIMITS))
            or request["producer_generation_sha256"] != bindings["producer_generation_sha256"]
            or canonical_sha256(request) != bindings["request_sha256"]):
        _fail()
    operation = _enum(request["operation"], {"pages", "regions", "hardscan"})
    configuration = _fields(request["configuration"], {"operation", "policy", "requested_pages"})
    if configuration["operation"] != operation:
        _fail()
    inputs = request["inputs"]
    required = {"source_sha256", "dependency_full_sha256", "dependency_test_sha256", "dependency_tools_sha256",
                "model_policy_sha256", "model_lock_sha256", "configuration_sha256"}
    allowed = required | {"installation_sha256"}
    if operation == "pages":
        allowed.add("evidence_sha256")
    else:
        required |= {"recovery_sha256", "plan_sha256"}
        allowed |= {"recovery_sha256", "plan_sha256"}
    if type(inputs) is not dict or not required <= set(inputs) <= allowed:
        _fail()
    for digest in inputs.values():
        _sha(digest)
    if (inputs["source_sha256"] != bindings["source_sha256"] or inputs["configuration_sha256"] != canonical_sha256(configuration)
            or execution.get("operation") != operation or not _same(execution.get("inputs"), inputs)
            or execution.get("kind") != "ocr_execution_receipt" or type(execution.get("schema_version")) is not int
            or execution["schema_version"] != 1 or execution.get("output_sha256") != bindings["report_sha256"]
            or report.get("source_sha256") != bindings["source_sha256"]):
        _fail()
    count = _int(report.get("page_count"), 1, 5000)
    calls = _list(execution.get("calls"), 20)
    for i, call in enumerate(calls, 1):
        _fields(call, {"id", "status", "elapsed_seconds"})
        if call["id"] != f"call-{i:04d}":
            _fail()
        _enum(call["status"], {"completed", "failed"})
        _number(call["elapsed_seconds"], maximum=86400)
    policy = configuration["policy"]
    if operation == "pages":
        _fields(policy, {"dpi", "max_pages", "min_chars", "max_pixels", "max_side", "preprocessing"})
        for key, low, high in (("dpi", 72, 600), ("max_pages", 1, 20), ("min_chars", 1, 10000), ("max_pixels", 1, 25000000), ("max_side", 32, 6000)):
            _int(policy[key], low, high)
        _enum(policy["preprocessing"], {"none", "deskew", "contrast", "deskew-contrast"})
        if report.get("kind") != "ocr_recovery_review":
            _fail()
        if (any(not _same(report["retry_configuration"].get(key), policy[key]) for key in ("dpi", "max_pixels", "max_side"))
                or report["retry_configuration"].get("preprocessing", "none") != policy["preprocessing"]
                or any(not _same(report["selection"].get(key), policy[key]) for key in ("max_pages", "min_chars"))):
            _fail()
        requested = _list(configuration["requested_pages"], 20)
        for number in requested:
            _int(number, 1, count)
        if len(set(requested)) != len(requested) or report["selection"]["requested_pages"] != sorted(requested):
            _fail()
        selected, deferred = {}, {}
        for rows, target, cap in ((report["pages"], selected, 20), (report["deferred_pages"], deferred, 5000)):
            for row in _list(rows, cap):
                number = _int(row["page_number"], 1, count)
                if number in selected or number in deferred:
                    _fail()
                target[number] = row
        if not set(requested) <= set(selected) | set(deferred):
            _fail()
        ordered = sorted(selected, key=lambda number: (number not in requested, number))
        if list(selected) != ordered or len(selected) > policy["max_pages"]:
            _fail()
        attempts = [f"page-{number:05d}" for number in ordered]
        entries = [(f"page-{number:05d}", number, None, "selected" if number in selected else "deferred" if number in deferred else "not_selected",
                    number in requested, selected.get(number, deferred.get(number))) for number in range(1, count + 1)]
    else:
        _fields(policy, {"dpi"})
        _int(policy["dpi"], 300, 400)
        if policy["dpi"] not in (300, 400) or configuration["requested_pages"] != []:
            _fail()
        if report.get("kind") != ("ocr_region_review" if operation == "regions" else "ocr_hardscan_review") or report.get("recovery_sha256") != inputs["recovery_sha256"]:
            _fail()
        if not _same(report["retry_configuration"].get("dpi"), policy["dpi"]):
            _fail()
        plan = _list(report["plan"]["regions"], 20, 1)
        records = _list(report["regions"], 20, 1)
        if len(plan) != len(records):
            _fail()
        entries, attempts, identities = [], [], []
        for i, (planned, row) in enumerate(zip(plan, records), 1):
            number = _int(planned["page_number"], 1, count)
            identifier = planned["region_id"]
            if (type(identifier) is not str or re.fullmatch(r"[A-Za-z0-9][A-Za-z0-9._-]{0,63}", identifier) is None
                    or any(not _same(row.get(key), val) for key, val in planned.items())):
                _fail()
            identity = (number, identifier)
            if any(identifier == prior[1] for prior in identities):
                _fail()
            identities.append(identity)
            item_id = f"region-{i:04d}"
            entries.append((item_id, number, identifier, "planned", True, row))
            if operation == "regions" or row["transform"]["eligibility"] == "ready":
                attempts.append(item_id)
        if identities != sorted(identities):
            _fail()
    items, records = [], {}
    for item_id, number, identifier, selection, explicitly, row in entries:
        status = row["status"] if selection in ("selected", "planned") else selection
        _enum(status, {"review_required", "empty_candidate", "retry_failed", "abstained", "deferred", "not_selected"})
        candidate = row.get("candidate") if row else None
        accepted = status in ("review_required", "empty_candidate")
        if accepted != (candidate is not None):
            _fail()
        candidate_view = {"state": "accepted" if accepted else "rejected" if status == "retry_failed" else "abstained" if status == "abstained" else "not_attempted",
                          "sha256": canonical_sha256(candidate) if accepted else None,
                          "line_count": len(_list(candidate["lines"], 10000)) if accepted else None,
                          "error_code": row.get("error_code") if status == "retry_failed" else None,
                          "join": "unavailable" if accepted else "not_applicable"}
        if status == "retry_failed":
            _enum(candidate_view["error_code"], _ERRORS)
        item = {"item_id": item_id, "page_number": number, "region_id": identifier, "selection": selection,
                "explicitly_requested": explicitly, "attempt_index": attempts.index(item_id) + 1 if item_id in attempts else None,
                "legacy_status": status, "legacy_record_sha256": canonical_sha256(row) if row is not None else None,
                "dispatch": None, "candidate": candidate_view,
                "diagnostic": _not_run("not_attempted"), "work": zero_work()}
        items.append(item)
        records[item_id] = row
    return operation, count, items, attempts, calls, records


def _mapping(operation, row):
    candidate = row.get("candidate") if row else None
    if operation == "pages":
        if candidate is None:
            return None
        if "preprocessing" in candidate:
            return {"kind": "preprocessed_page", "metadata": copy.deepcopy(candidate["preprocessing"])}
        return {"kind": "page", "raster": {key: candidate["raster"][key] for key in ("width", "height")}}
    if operation == "regions":
        return {"kind": "region", "geometry": copy.deepcopy(row["geometry"])}
    return {"kind": "hardscan", "geometry": copy.deepcopy(row["geometry"]), "transform": copy.deepcopy(row["transform"])}


def _settings(value):
    _fields(value, {"use_det", "use_cls", "use_rec", "return_word_box", "return_single_char_box",
                    "engine_text_score", "reader_min_score", "classification_threshold"})
    for key in ("use_det", "use_cls", "use_rec", "return_word_box", "return_single_char_box"):
        if type(value[key]) is not bool:
            _fail()
    for key in ("engine_text_score", "reader_min_score", "classification_threshold"):
        _number(value[key], maximum=1)
    return all(value[key] is True for key in ("use_det", "use_cls", "use_rec")) and not value["return_word_box"] and not value["return_single_char_box"]


def _settings_recipe_matches(value):
    return (value["engine_text_score"] == 0.0 and value["reader_min_score"] == 0.0
            and value["classification_threshold"] == 0.9)


def _raster(value, settings, policy):
    _fields(value, {"width", "height", "pixel_sha256", "coordinate_system", "global_width", "global_height",
                    "padded_width", "padded_height", "ratio_h", "ratio_w", "padding_top", "padding_left", "operations", "detector_box_dtype"})
    width, height = (_int(value[key], 1, 12000) for key in ("width", "height"))
    _sha(value["pixel_sha256"])
    if value["coordinate_system"] != "engine_input_bgr_uint8_pixels":
        _fail()
    dimensions = working_dimensions(width, height, detection=True,
        limits=EngineLimits(max_side=policy.get("max_side", 6000), max_pixels=policy.get("max_pixels", 25000000)))
    for prefix, expected in (("global", dimensions["global"]), ("padded", dimensions["padded"])):
        actual = [_int(value[prefix + "_" + key], 1, 12000) for key in ("width", "height")]
        if actual != expected:
            _fail()
    for key, expected in (("ratio_h", height / dimensions["global"][1]), ("ratio_w", width / dimensions["global"][0])):
        if _number(value[key], maximum=12000) != expected or value[key] <= 0:
            _fail()
    if (_int(value["padding_left"], maximum=0) != 0
            or _int(value["padding_top"], maximum=12000) != (dimensions["padded"][1] - dimensions["global"][1]) // 2):
        _fail()
    if value["detector_box_dtype"] is not None:
        _enum(value["detector_box_dtype"], {"float32", "float64"})
    operations = value["operations"]
    if operations is not None:
        _list(operations, 2, 1)
        expected = [{"kind": "preprocess", "ratio_h": value["ratio_h"], "ratio_w": value["ratio_w"]},
                    {"kind": "padding_1", "top": value["padding_top"], "left": 0}]
        if not _same(operations, expected[:len(operations)]):
            _fail()


def _role(value):
    keys = {"state", "attempted", "completed", "failed", "session_attempted", "session_completed", "session_failed"}
    _fields(value, keys)
    status = _enum(value["state"], {"not_run", "completed", "failed", "unobserved"})
    if status == "unobserved":
        if any(value[key] is not None for key in keys - {"state"}):
            _fail()
        return
    for key in keys - {"state"}:
        _int(value[key], maximum=167 if key.startswith("session_") else 1)
    if value["attempted"] != value["completed"] + value["failed"] or value["session_attempted"] != value["session_completed"] + value["session_failed"]:
        _fail()
    expected = {"not_run": (0, 0, 0), "completed": (1, 1, 0), "failed": (1, 0, 1)}[status]
    if tuple(value[key] for key in ("attempted", "completed", "failed")) != expected:
        _fail()
    if status == "not_run" and any(value[key] for key in keys - {"state"}):
        _fail()
    if value["session_failed"] > 1 or (status == "completed" and value["session_failed"]):
        _fail()


def _stage(value, name):
    _fields(value, {"state", "input_count", "output_count", "box_input", "box_output"})
    status = _enum(value["state"], {"not_run", "completed", "failed", "unobserved"})
    for key in ("input_count", "output_count"):
        if value[key] is not None:
            _int(value[key], maximum=1000)
    if status in {"failed", "unobserved"} and (value["output_count"] is not None or value["box_output"] is not None):
        _fail()
    if status == "completed" and (value["output_count"] is None or (name != "detector" and value["input_count"] is None)):
        _fail()
    if status == "not_run" and any(value[key] is not None for key in ("input_count", "output_count", "box_input", "box_output")):
        _fail()
    if name == "detector" and (value["input_count"] is not None or value["box_input"] is not None):
        _fail()
    if name in {"crops", "classifier", "recognizer"} and (value["box_input"] is not None or value["box_output"] is not None):
        _fail()
    _box_array(value["box_input"], value["input_count"])
    _box_array(value["box_output"], value["output_count"])


def _recognition(value):
    _fields(value, {"state", "text_kind", "text_sha256", "codepoints", "score"})
    status = _enum(value["state"], {"completed", "not_run", "unobserved"})
    if status != "completed":
        if any(value[key] is not None for key in ("text_kind", "text_sha256", "codepoints", "score")):
            _fail()
        return
    kind = _enum(value["text_kind"], _TEXT_KINDS)
    _sha(value["text_sha256"])
    size = _int(value["codepoints"], maximum=4096)
    _number(value["score"], maximum=1)
    if (kind == "empty") != (size == 0) or (size == 0 and value["text_sha256"] != hashlib.sha256(b"").hexdigest()):
        _fail()


def _detection(value, index, raster, settings, *, final=False):
    _fields(value, _FINAL_DETECTION_KEYS if final else _DETECTION_KEYS)
    if value["id"] != f"d{index:04d}" or _int(value["ordinal"], 1, 1000) != index or value["detector_score"] is not None:
        _fail()
    if value["detector_score_alignment"] != "upstream_alignment_unavailable" or raster["detector_box_dtype"] is None:
        _fail()
    _quad(value["detector_box"], raster["padded_width"], raster["padded_height"], positive=True, dtype=raster["detector_box_dtype"])
    _enum(value["crop_state"], {"completed", "not_run", "unobserved"})
    cls = _fields(value["classification"], {"state", "label", "score", "rotated_180"})
    _enum(cls["state"], {"completed", "not_run", "unobserved"})
    if cls["state"] == "completed":
        _enum(cls["label"], {"0", "180"})
        _number(cls["score"], maximum=1)
        if type(cls["rotated_180"]) is not bool or cls["rotated_180"] != (cls["label"] == "180" and cls["score"] > settings["classification_threshold"]):
            _fail()
    elif any(cls[key] is not None for key in ("label", "score", "rotated_180")):
        _fail()
    _recognition(value["recognition"])
    _enum(value["engine_mapping"], {"verified", "unobserved"})
    if value["engine_mapping"] == "verified":
        if raster["operations"] is None or len(raster["operations"]) != 2:
            _fail()
        _quad(value["engine_input_box"], raster["width"], raster["height"])
    elif value["engine_input_box"] is not None:
        _fail()


def _dispatch(value, item, calls, bindings, ordinal):
    if value is None:
        if item["candidate"]["state"] == "accepted":
            _fail()
        return ordinal
    _fields(value, {"call_id", "ordinal", "ticket_sha256", "raw_status"})
    if item["attempt_index"] is None or ordinal >= len(calls):
        _fail()
    call = calls[ordinal]
    ordinal += 1
    if (_int(value["ordinal"], 1, 20) != ordinal or value["call_id"] != call["id"]
            or value["raw_status"] != call["status"]):
        _fail()
    if item["candidate"]["state"] == "accepted" and value["raw_status"] != "completed":
        _fail()
    expected = {"request_sha256": bindings["request_sha256"], "item_id": item["item_id"],
                "attempt_index": item["attempt_index"], "call_id": call["id"], "dispatch_ordinal": ordinal}
    if _sha(value["ticket_sha256"]) != canonical_sha256(expected):
        _fail()
    return ordinal


def _diagnostic(value, item, policy, *, final=False, building=False):
    _fields(value, _DIAGNOSTIC_KEYS)
    state = _enum(value["state"], {"complete", "partial", "unavailable", "not_run"})
    if item["attempt_index"] is None and item["dispatch"] is not None:
        _fail()
    if state == "not_run":
        reason = "not_attempted" if item["attempt_index"] is None else "no_raw_dispatch"
        if item["dispatch"] is not None or not _same(value, _not_run(reason)):
            _fail()
        return
    if item["dispatch"] is None:
        _fail()
    settings, raster = value["settings"], value["raster"]
    supported = _settings(settings) if settings is not None else None
    if raster is not None:
        _raster(raster, settings, policy)
    if state == "unavailable":
        _enum(value["reason"], _REASONS)
        if any(value[key] is not None for key in ("roles", "stages", "mapping", "detection_count")) or value["detections"] != []:
            _fail()
        if value["reason"] == "unsupported_route" and supported is not False:
            _fail()
        return
    if state == "complete":
        if value["reason"] is not None or item["dispatch"]["raw_status"] != "completed":
            _fail()
    elif value["reason"] != "ocr_path_incomplete":
        _fail()
    if supported is not True:
        _fail()
    if not building and not _settings_recipe_matches(settings):
        _fail()
    for role, observation in _fields(value["roles"], _ROLES).items():
        _role(observation)
        if role == "detection" and observation["session_attempted"] is not None and observation["session_attempted"] > 1:
            _fail()
    for stage, observation in _fields(value["stages"], _STAGES).items():
        _stage(observation, stage)
    records = _list(value["detections"], 1000)
    count = value["detection_count"]
    if count is None:
        if records or state == "complete" or value["stages"]["detector"]["state"] == "completed":
            _fail()
    else:
        _int(count, maximum=1000)
        if len(records) != count or raster is None or value["stages"]["detector"]["state"] != "completed" or value["stages"]["detector"]["output_count"] != count:
            _fail()
        detector = value["roles"]["detection"]
        if detector["state"] != "completed" or (detector["session_attempted"], detector["session_completed"], detector["session_failed"]) != (1, 1, 0):
            _fail()
        expected_boxes = ({"state": "array", "dtype": raster["detector_box_dtype"], "shape": [count, 4, 2]}
                          if count else {"state": "none", "dtype": None, "shape": None})
        if not _same(value["stages"]["detector"]["box_output"], expected_boxes):
            _fail()
        for index, detection in enumerate(records, 1):
            _detection(detection, index, raster, settings, final=final)
            for key, stage in (("crop_state", "crops"), ("classification", "classifier"), ("recognition", "recognizer")):
                status = detection[key] if key == "crop_state" else detection[key]["state"]
                stage_status = value["stages"][stage]["state"]
                expected = "completed" if stage_status == "completed" else "not_run" if stage_status == "not_run" else "unobserved"
                if status != expected:
                    _fail()
    if sum(d["recognition"]["codepoints"] or 0 for d in records) > 100000:
        _fail()
    _stage_prerequisites(value)


def _stage_prerequisites(value):
    """Established downstream returns require their actual upstream returns."""
    stages, roles, count = value["stages"], value["roles"], value["detection_count"]
    for name, role in (("detector", "detection"), ("classifier", "classification"), ("recognizer", "recognition")):
        stage, observed = stages[name], roles[role]
        # Detector stage surrounds the component itself. Classifier/recognizer
        # stages surround broader methods: guard refusal before the component,
        # or a failure after its normal return, must not invent a failed role.
        if (stage["state"] == "completed" or (name == "detector" and stage["state"] == "failed")) and observed["state"] != stage["state"]:
            _fail()
        if stage["state"] == "not_run" and observed["state"] in {"completed", "failed"}:
            _fail()
    for name, previous in (("crops", "detector"), ("classifier", "crops"), ("recognizer", "classifier"), ("score_filter", "recognizer")):
        stage = stages[name]
        if stage["state"] in {"completed", "failed"}:
            if count in (None, 0) or stages[previous]["state"] != "completed":
                _fail()
            if name != "score_filter" and stage["input_count"] != count:
                _fail()
            if name in {"crops", "classifier", "recognizer"} and stage["state"] == "completed" and stage["output_count"] != count:
                _fail()
    for name, role in (("classifier", "classification"), ("recognizer", "recognition")):
        previous = "crops" if name == "classifier" else "classifier"
        if roles[role]["state"] in {"completed", "failed"}:
            if count in (None, 0) or stages[previous]["state"] != "completed" or roles[role]["session_attempted"] > (count + 5) // 6:
                _fail()
        if stages[name]["state"] == "completed" or roles[role]["state"] == "completed":
            sessions = (count + 5) // 6
            if tuple(roles[role][key] for key in ("session_attempted", "session_completed", "session_failed")) != (sessions, sessions, 0):
                _fail()
    if stages["formatter"]["state"] in {"completed", "failed"}:
        # Pinned run_ocr_steps replaces the detector operand with its default
        # after a caught detector/crop RapidOCRError. A subsequently failing
        # formatter observed that actual None operand, independently of any N
        # already established by a successful detector return. This proves no
        # formatter output and cannot establish complete terminal lineage.
        formatter = stages["formatter"]
        failed_default = (value["state"] == "partial" and formatter["state"] == "failed"
                          and formatter["input_count"] == 0
                          and _same(formatter["box_input"], {"state": "none", "dtype": None, "shape": None})
                          and (count is None or (count > 0 and stages["crops"]["state"] == "failed")))
        if failed_default:
            return
        if count is None or stages["formatter"]["input_count"] != count:
            _fail()
        expected = stages["detector"]["box_output"]
        if not _same(stages["formatter"]["box_input"], expected):
            _fail()


def _complete(value):
    count = value["detection_count"]
    if type(count) is not int:
        _fail()
    stages, roles = value["stages"], value["roles"]
    expected_sessions = {"detection": 1, "classification": (count + 5) // 6, "recognition": (count + 5) // 6}
    for role, sessions in expected_sessions.items():
        observation = roles[role]
        if (observation["state"] != ("completed" if sessions else "not_run")
                or (observation["session_attempted"], observation["session_completed"], observation["session_failed"]) != (sessions, sessions, 0)):
            _fail()
    if stages["formatter"]["state"] != "completed" or stages["formatter"]["input_count"] != count:
        _fail()
    if count == 0:
        if any(stages[name]["state"] != "not_run" for name in ("crops", "classifier", "recognizer", "score_filter")):
            _fail()
        if stages["formatter"]["output_count"] != 0 or stages["formatter"]["box_output"] != {"state": "none", "dtype": None, "shape": None}:
            _fail()
        return []
    for name in ("crops", "classifier", "recognizer"):
        if stages[name]["state"] != "completed" or (stages[name]["input_count"], stages[name]["output_count"]) != (count, count):
            _fail()
    terminals = []
    for record in value["detections"]:
        rec = record["recognition"]
        if rec["state"] != "completed" or record["engine_mapping"] != "verified":
            _fail()
        terminals.append("removed_blank" if rec["text_kind"] != "nonempty" else
                         "removed_score" if rec["score"] < value["settings"]["engine_text_score"] else "retained")
    nonblank = count - terminals.count("removed_blank")
    retained = terminals.count("retained")
    filtering = stages["score_filter"]
    if filtering["state"] != "completed" or (filtering["input_count"], filtering["output_count"]) != (nonblank, retained):
        _fail()
    dtype = value["raster"]["detector_box_dtype"]
    box_input = {"state": "array", "dtype": dtype, "shape": [nonblank, 4, 2]}
    box_output = {"state": "array", "dtype": dtype if retained else "float64", "shape": [retained, 4, 2] if retained else [0]}
    final_boxes = box_output if retained else {"state": "none", "dtype": None, "shape": None}
    if (not _same(filtering["box_input"], box_input) or not _same(filtering["box_output"], box_output)
            or not _same(stages["formatter"]["box_output"], final_boxes) or stages["formatter"]["output_count"] != retained):
        _fail()
    return terminals


def _line_fingerprint(text, score, box):
    if type(text) is not str or len(text) > 4096 or any(0xD800 <= ord(char) <= 0xDFFF for char in text):
        _fail()
    return {"text_sha256": hashlib.sha256(text.encode("utf-8")).hexdigest(), "codepoints": len(text),
            "text_kind": "empty" if not text else "nonempty" if text.strip() else "whitespace_only",
            "score": float(score), "box": [[float(coordinate) for coordinate in point] for point in box]}


def _observed_line(record):
    rec = record["recognition"]
    return {key: rec[key] for key in ("text_sha256", "codepoints", "text_kind")} | {
        "score": float(rec["score"]), "box": [[float(coordinate) for coordinate in point] for point in record["engine_input_box"]]}


def _raw_output(value, raster):
    if value is None:
        return
    _fields(value, {"boxes", "lines"})
    lines = _list(value["lines"], 1000)
    _box_array(value["boxes"], len(lines))
    if value["boxes"] is None:
        _fail()
    for line in lines:
        _fields(line, {"text_sha256", "codepoints", "text_kind", "score", "box"})
        _recognition({"state": "completed", **{key: line[key] for key in ("text_sha256", "codepoints", "text_kind", "score")}})
        _quad(line["box"], raster["width"], raster["height"])
    if lines and value["boxes"]["state"] != "array":
        _fail()


class _LineageFailure(ValueError):
    pass


def _derive_record(record, raster, mapping, derivation, *, validating, remaining, prior_mapping_failed=False):
    if record["engine_mapping"] == "unobserved":
        return _physical("engine_mapping_unobserved"), 0
    operands = {"dtype": raster["detector_box_dtype"], "box": copy.deepcopy(record["detector_box"]), "raster": copy.deepcopy(raster)}
    before = _canonical(operands)
    try:
        expected = derivation.replay_engine_box(**operands)
        _quad(expected, raster["width"], raster["height"])
        if _canonical(operands) != before or not _same(expected, record["engine_input_box"]):
            raise _LineageFailure("OCR disposition remap differs")
    except Exception as error:
        raise _LineageFailure("OCR disposition remap is unavailable") from error
    if mapping is None:
        return _physical("report_geometry_unavailable"), 0
    if remaining < 128:
        return _physical("mapping_limit"), 0
    # A persisted negative claim has no positive polygon to regenerate. The
    # engine remap above and exact final-report mapping binding are still
    # required; the original failed output-capacity reservation is retained.
    if validating and prior_mapping_failed:
        return _physical("mapping_failed"), 128
    operands = {"engine_box": copy.deepcopy(expected), "mapping": copy.deepcopy(mapping)}
    before = _canonical(operands)
    try:
        result = derivation.map_source_polygon(**operands)
        _preflight(result, 16384)
        _physical_checked(result)
        if _canonical(operands) != before:
            _fail()
        return copy.deepcopy(result), len(result["polygon"]) if result["state"] == "available" else 128
    except Exception as error:
        if validating:
            raise _LineageFailure("OCR disposition physical mapping is unavailable") from error
        return _physical("mapping_failed"), 128


def _reconcile(diag, item, row, derivation, *, raw_output=None, validating=False, total_vertices=0):
    if diag["state"] not in {"complete", "partial"}:
        return diag, 0
    count = diag["detection_count"]
    terminals = _complete(diag) if diag["state"] == "complete" else ["unresolved"] * (count or 0)
    expected, output, item_vertices = [], [], 0
    for original, terminal in zip(diag["detections"], terminals):
        record = {key: copy.deepcopy(original[key]) for key in _DETECTION_KEYS}
        physical, charged = _derive_record(record, diag["raster"], diag["mapping"], derivation, validating=validating,
            remaining=min(20000 - item_vertices, 100000 - total_vertices - item_vertices),
            prior_mapping_failed=validating and original["physical"]["state"] == "unavailable" and original["physical"]["reason"] == "mapping_failed")
        item_vertices += charged
        if not validating:
            item["work"]["vertices_charged"] += charged
        if physical["reason"] == "mapping_limit" and not validating and item["work"]["exhausted"] is None:
            if charged == 0 and 20000 - item_vertices < 128:
                refusal = {"limit": "vertices_per_call", "used_before": item_vertices, "requested_units": 128}
            elif charged == 0 and 100000 - total_vertices - item_vertices < 128:
                refusal = {"limit": "vertices_total", "used_before": total_vertices + item_vertices, "requested_units": 128}
            else:
                refusal = {"limit": "polygon_vertices", "used_before": 0, "requested_units": 129}
            item["work"]["exhausted"] = refusal
        record.update(terminal=terminal, output_index=len(output) + 1 if terminal == "retained" else None,
                      candidate_disposition="not_retained" if terminal != "retained" else "candidate_unavailable",
                      candidate_index=None, physical=physical)
        expected.append(record)
        if terminal == "retained":
            output.append(record)
    if diag["state"] == "complete":
        expected_output = {"boxes": diag["stages"]["formatter"]["box_output"], "lines": [_observed_line(record) for record in output]}
        if not validating:
            _raw_output(raw_output, diag["raster"])
            if not _same(raw_output, expected_output):
                raise _LineageFailure("OCR raw output differs from observed lineage")
        if item["candidate"]["state"] == "accepted":
            candidate = row["candidate"]
            if candidate["engine"]["min_score"] != diag["settings"]["reader_min_score"]:
                raise _LineageFailure("OCR reader threshold differs")
            # Validate *all* raw output, even an eventual threshold exclusion.
            try:
                for record in output:
                    _quad(record["engine_input_box"], diag["raster"]["width"], diag["raster"]["height"], positive=True)
                accepted = [record for record in output if record["recognition"]["score"] >= diag["settings"]["reader_min_score"]]
                actual = []
                for line in candidate["lines"]:
                    _fields(line, {"text", "score", "box"})
                    _number(line["score"], maximum=1)
                    _quad(line["box"], diag["raster"]["width"], diag["raster"]["height"], positive=True)
                    actual.append(_line_fingerprint(line["text"], line["score"], line["box"]))
                if not _same(actual, [_observed_line(record) for record in accepted]):
                    raise _LineageFailure("OCR candidate differs from observed retained lines")
            except ValueError as error:
                raise _LineageFailure("OCR candidate join is invalid") from error
            accepted_index = 0
            for record in output:
                if record["recognition"]["score"] >= diag["settings"]["reader_min_score"]:
                    accepted_index += 1
                    record.update(candidate_disposition="accepted", candidate_index=accepted_index)
                    if diag["mapping"] and diag["mapping"]["kind"] == "hardscan" and record["physical"]["state"] == "available":
                        if not _same(record["physical"]["polygon"], row["source_polygons"][accepted_index-1]):
                            raise _LineageFailure("OCR hard-scan source polygon differs")
                else:
                    record["candidate_disposition"] = "reader_score_filtered"
            item["candidate"]["join"] = "verified_ordered"
    elif item["candidate"]["state"] == "accepted":
        for record in output:
            record["candidate_disposition"] = "unobserved"
    if validating and not _same(diag["detections"], expected):
        _fail()
    if validating and item["work"]["vertices_charged"] < item_vertices:
        _fail()
    diag["detections"] = expected
    return diag, item_vertices


def _mapping_raster(mapping, raster):
    if mapping is None or raster is None:
        return
    kind = mapping["kind"]
    expected = (mapping["raster"] if kind == "page" else mapping["metadata"]["processed_raster"] if kind == "preprocessed_page"
                else mapping["geometry"]["raster"] if kind == "region" else mapping["transform"]["processed_raster"])
    if [raster["width"], raster["height"]] != [expected["width"], expected["height"]]:
        _fail()


def _summary(items, attempts, dispatches):
    result = {"covered_items": len(items)}
    for key, selection in (("selected_items", "selected"), ("deferred_items", "deferred"), ("not_selected_items", "not_selected"), ("planned_items", "planned")):
        result[key] = sum(item["selection"] == selection for item in items)
    result.update(explicitly_requested_items=sum(item["explicitly_requested"] for item in items), retry_attempts=len(attempts),
                  raw_calls=len(dispatches), raw_completed=sum(item["dispatch"] is not None and item["dispatch"]["raw_status"] == "completed" for item in items),
                  raw_failed=sum(item["dispatch"] is not None and item["dispatch"]["raw_status"] == "failed" for item in items))
    for key, state in (("accepted_candidates", "accepted"), ("rejected_candidates", "rejected"), ("abstained_candidates", "abstained"), ("unattempted_candidates", "not_attempted")):
        result[key] = sum(item["candidate"]["state"] == state for item in items)
    for state in ("complete", "partial", "unavailable", "not_run"):
        result["diagnostics_" + state] = sum(item["diagnostic"]["state"] == state for item in items)
    known = sum(item["diagnostic"]["detection_count"] is not None for item in items)
    records = [record for item in items for record in item["diagnostic"]["detections"]]
    result.update(detections_known_items=known, detections_unknown_items=len(items)-known, known_detections=len(records))
    for status in ("removed_blank", "removed_score", "retained", "unresolved"):
        result[status] = sum(record["terminal"] == status for record in records)
    for key, status in (("joined_candidate_lines", "accepted"), ("reader_score_filtered", "reader_score_filtered"),
                         ("candidate_unavailable", "candidate_unavailable"), ("candidate_unobserved", "unobserved")):
        result[key] = sum(record["candidate_disposition"] == status for record in records)
    for state in ("available", "unavailable"):
        result["physical_" + state] = sum(record["physical"]["state"] == state for record in records)
    return result


def _totals(items, dispatches):
    by_id = {item["item_id"]: item for item in items}
    totals = dict.fromkeys(_WORK_CAPS, 0)
    global_caps = dict(zip(_WORK_CAPS, (20000, 1000000, 100000, 1500000000)))
    limit_fields = {"detections": "detections_reserved", "codepoints": "codepoints_charged", "vertices": "vertices_charged", "raster_bytes": "raster_bytes_hashed"}
    for identifier in dispatches + [item["item_id"] for item in items if item["dispatch"] is None]:
        item = by_id[identifier]
        work, diag = _work(item["work"]), item["diagnostic"]
        if item["dispatch"] is None and not _same(work, zero_work()):
            _fail()
        if (work["detections_reserved"] < (diag["detection_count"] or 0)
                or work["codepoints_charged"] < sum(d["recognition"]["codepoints"] or 0 for d in diag["detections"])
                or work["vertices_charged"] < sum(len(d["physical"]["polygon"] or []) for d in diag["detections"])):
            _fail()
        if diag["raster"] is not None and work["raster_bytes_hashed"] != diag["raster"]["width"] * diag["raster"]["height"] * 3:
            _fail()
        exhausted = work["exhausted"]
        if diag["reason"] == "budget_exhausted" and exhausted is None:
            _fail()
        limited_geometry = any(d["physical"]["reason"] == "mapping_limit" for d in diag["detections"])
        if limited_geometry and (exhausted is None or exhausted["limit"] not in {"vertices_per_call", "vertices_total", "polygon_vertices"}):
            _fail()
        if exhausted is not None:
            name = exhausted["limit"]
            if diag["state"] != "unavailable" and not limited_geometry:
                _fail()
            for prefix, field in limit_fields.items():
                if name == prefix + "_per_call" and exhausted["used_before"] != work[field]:
                    _fail()
                if name == prefix + "_total" and exhausted["used_before"] != totals[field] + work[field]:
                    _fail()
        for key in totals:
            totals[key] += work[key]
            if totals[key] > global_caps[key]:
                _fail()
    return totals


def _top(operation, count, items, attempts, dispatches, bindings):
    return {"schema_version": 1, "kind": "ocr_detection_disposition", "operation": operation, "recipe_id": RECIPE_ID,
            "page_count": count, "bindings": dict(bindings), "limits": dict(LIMITS), "attempt_order": list(attempts),
            "dispatch_order": list(dispatches), "items": items, "summary": _summary(items, attempts, dispatches),
            "requires_attention": True, "canonical_extraction_modified": False, "accuracy_verified": False, "scope": SCOPE}


def _maximal_envelope_item():
    """Per-field maxima, intentionally including mutually exclusive values.

    All variable strings in an envelope are ASCII enums/IDs or hex digests;
    reference/OCR text and arbitrary library strings are never envelope fields.
    Simultaneously nonnull candidate/dispatch fields bound every final status.
    """
    return {"item_id": "x" * 11, "page_number": 5000, "region_id": "x" * 64,
            "selection": "x" * 12, "explicitly_requested": False, "attempt_index": 20,
            "legacy_status": "x" * 15, "legacy_record_sha256": "f" * 64,
            "dispatch": {"call_id": "call-0020", "ordinal": 20, "ticket_sha256": "f" * 64, "raw_status": "completed"},
            "candidate": {"state": "not_attempted", "sha256": "f" * 64, "line_count": 10000,
                          "error_code": max(_ERRORS, key=len), "join": "verified_ordered"},
            "diagnostic": _unavailable({}, "budget_exhausted"),
            "work": {**dict(_WORK_CAPS), "exhausted": {"limit": "raster_bytes_per_call", "used_before": 1500000000,
                                                        "requested_units": 1500000001}}}


def reserve_envelope(operation, *, item_count=5000):
    """Proven pre-OCR compact-JSON + LF reservation for the complete cohort.

    Page callers may reserve the full 5,000-page bound before opening a source.
    Crop callers must provide their complete 1..20-entry approved plan size.
    The writer must use sort_keys=True, ensure_ascii=False, allow_nan=False,
    separators=(',', ':') and exactly one final LF. No final status is assumed.
    """
    _enum(operation, {"pages", "regions", "hardscan"})
    _int(item_count, 1, 5000 if operation == "pages" else 20)
    # Each item JSON plus a comma is no larger than its JSON plus LF charge.
    # The top slot includes final LF, bindings, every maximal-width summary
    # count, both twenty-ID arrays and all fixed fields, but no item contents.
    _preflight(_maximal_envelope_item(), _ITEM_SLOT)
    top = _top("hardscan", 5000, [], ["page-05000"] * 20, ["page-05000"] * 20,
               dict.fromkeys(_BINDINGS, "f" * 64))
    top["summary"] = dict.fromkeys(top["summary"], 20000)
    _preflight(top, _TOP_SLOT)
    reserve = _TOP_SLOT + item_count * _ITEM_SLOT
    if reserve > LIMITS["max_serialized_bytes"]:
        _fail()
    return reserve


def observation_representation_bound():
    """Conservative bytes for every admitted20x1000 plain capture snapshot.

    A finite Python binary64 JSON number needs at most32 ASCII bytes. Quoted
    32-character stand-ins therefore overbound all numeric operands below;
    nullable fields are made simultaneously nonnull and enum lengths maximal.
    The fixed16KiB per-attempt allowance covers raster/settings/roles/stages,
    work and dispatch (each closed and independent of detection count).
    This is an input representation bound, not a changed output/work policy.
    """
    scalar = "x" * 32
    quad = [[scalar, scalar] for _ in range(4)]
    rec = {"state": "unobserved", "text_kind": "whitespace_only", "text_sha256": "f" * 64,
           "codepoints": scalar, "score": scalar}
    detection = {"id": "d1000", "ordinal": scalar, "detector_box": quad, "detector_score": None,
                 "detector_score_alignment": "upstream_alignment_unavailable", "crop_state": "unobserved",
                 "classification": {"state": "unobserved", "label": "180", "score": scalar, "rotated_180": False},
                 "recognition": rec, "engine_input_box": quad, "engine_mapping": "unobserved"}
    raw_line = {key: rec[key] for key in ("text_kind", "text_sha256", "codepoints", "score")} | {"box": quad}
    array = {"state": "array", "dtype": "float64", "shape": [scalar] * 3}
    observed_role = {"state": "unobserved", **dict.fromkeys(("attempted", "completed", "failed", "session_attempted",
                                                            "session_completed", "session_failed"), scalar)}
    observed_stage = {"state": "unobserved", "input_count": scalar, "output_count": scalar,
                      "box_input": array, "box_output": array}
    raster = dict.fromkeys(("width", "height", "global_width", "global_height", "padded_width", "padded_height",
                            "ratio_h", "ratio_w", "padding_top", "padding_left"), scalar)
    raster.update(pixel_sha256="f" * 64, coordinate_system="engine_input_bgr_uint8_pixels", detector_box_dtype="float64",
                  operations=[{"kind": "preprocess", "ratio_h": scalar, "ratio_w": scalar},
                              {"kind": "padding_1", "top": scalar, "left": scalar}])
    envelope = {"item_id": "x" * 11, "attempt_index": scalar,
                "dispatch": {"call_id": "call-0020", "ordinal": scalar, "ticket_sha256": "f" * 64, "raw_status": "completed"},
                "diagnostic_state": "unavailable", "diagnostic_reason": "unsupported_recipe",
                "settings": {**dict.fromkeys(("use_det", "use_cls", "use_rec", "return_word_box", "return_single_char_box"), False),
                             **dict.fromkeys(("engine_text_score", "reader_min_score", "classification_threshold"), scalar)},
                "raster": raster, "roles": dict.fromkeys(_ROLES, observed_role), "stages": dict.fromkeys(_STAGES, observed_stage),
                "detection_count": scalar, "detections": [], "raw_output": {"boxes": array, "lines": []},
                "work": {**dict.fromkeys(_WORK_CAPS, scalar), "exhausted": {"limit": "raster_bytes_per_call",
                                                                          "used_before": scalar, "requested_units": scalar}}}
    _preflight(envelope, 16384)
    _preflight({"schema_version": 1, "kind": "ocr_disposition_observations", "operation": "hardscan",
                "request_sha256": "f" * 64, "attempts": []}, 32768)
    # Their +LF charge covers each separating comma.32KiB is the fixed root
    # envelope allowance; all root fields/20 attempt separators are bounded.
    result = 32768 + 20 * 16384 + 20000 * (_preflight(detection, 4096) + _preflight(raw_line, 2048))
    if result > MAX_OBSERVATION_BYTES:
        _fail()
    return result


def _fallback(item, reason):
    value = copy.deepcopy({**item, "diagnostic": None})
    value["diagnostic"] = _unavailable({}, reason)
    if value["candidate"]["state"] == "accepted":
        value["candidate"]["join"] = "unavailable"
    _preflight(value, _ITEM_SLOT)
    return value


def _serialization_ledger(items, operation):
    """Validate reservation order; discarded byte counts are local claims."""
    used = reserve_envelope(operation, item_count=len(items))
    for item in items:
        size = _preflight(item)
        exhausted = item["work"]["exhausted"]
        if exhausted is not None and exhausted["limit"] == "serialized_bytes":
            if (item["diagnostic"]["state"] != "unavailable" or item["diagnostic"]["reason"] != "budget_exhausted"
                    or item["diagnostic"]["settings"] is not None or item["diagnostic"]["raster"] is not None
                    or size > _ITEM_SLOT or exhausted["used_before"] != used):
                _fail()
        else:
            used += max(0, size - _ITEM_SLOT)
            if used > LIMITS["max_serialized_bytes"]:
                _fail()
    return used


def _build(observations, request, report, execution, bindings, derivation):
    if type(derivation) is not DispositionDerivation:
        _fail()
    observation_representation_bound()
    _preflight(observations, MAX_OBSERVATION_BYTES, maximum_nodes=MAX_OBSERVATION_NODES)
    _fields(observations, {"schema_version", "kind", "operation", "request_sha256", "attempts"})
    operation, count, items, attempts, calls, records = _context(request, report, execution, bindings)
    if (type(observations["schema_version"]) is not int or observations["schema_version"] != 1
            or observations["kind"] != "ocr_disposition_observations" or observations["operation"] != operation
            or observations["request_sha256"] != bindings["request_sha256"]):
        _fail()
    snapshots = _list(observations["attempts"], 20)
    if len(snapshots) != len(attempts):
        _fail()
    # Bound total captured work before any independent geometry regeneration.
    charges = dict.fromkeys(_WORK_CAPS, 0)
    for snapshot in snapshots:
        _fields(snapshot, _SNAPSHOT_KEYS)
        work = _work(snapshot["work"])
        if work["vertices_charged"]:
            _fail()
        for key, maximum in zip(_WORK_CAPS, (20000, 1000000, 100000, 1500000000)):
            charges[key] += work[key]
            if charges[key] > maximum:
                _fail()
    by_id = {item["item_id"]: item for item in items}
    ordinal, dispatches, vertices = 0, [], 0
    for index, (identifier, snapshot) in enumerate(zip(attempts, snapshots), 1):
        _fields(snapshot, _SNAPSHOT_KEYS)
        if snapshot["item_id"] != identifier or _int(snapshot["attempt_index"], 1, 20) != index:
            _fail()
        item, row = by_id[identifier], records[identifier]
        item["dispatch"] = copy.deepcopy(snapshot["dispatch"])
        ordinal = _dispatch(item["dispatch"], item, calls, bindings, ordinal)
        if item["dispatch"] is not None:
            dispatches.append(identifier)
        diag = {"state": snapshot["diagnostic_state"], "reason": snapshot["diagnostic_reason"],
                **{key: copy.deepcopy(snapshot[key]) for key in ("settings", "raster", "roles", "stages", "detection_count", "detections")},
                "mapping": _mapping(operation, row) if snapshot["diagnostic_state"] in {"complete", "partial"} else None}
        _diagnostic(diag, item, request["configuration"]["policy"], building=True)
        _mapping_raster(diag["mapping"], diag["raster"])
        item["work"] = copy.deepcopy(_work(snapshot["work"]))
        if item["work"]["vertices_charged"] != 0:
            _fail()  # Geometry is finalized only here, after the report exists.
        if item["dispatch"] is None or item["dispatch"]["raw_status"] == "failed":
            if snapshot["raw_output"] is not None:
                _fail()
        elif snapshot["raw_output"] is not None:
            if diag["raster"] is None:
                _fail()
            _raw_output(snapshot["raw_output"], diag["raster"])
        if diag["state"] in {"complete", "partial"} and not _settings_recipe_matches(diag["settings"]):
            diag = _unavailable(diag, "unsupported_recipe")
        try:
            diag, _ = _reconcile(diag, item, row, derivation, raw_output=snapshot["raw_output"], total_vertices=vertices)
        except _LineageFailure:
            diag = _unavailable(diag, "lineage_invalid")
            if item["candidate"]["state"] == "accepted":
                item["candidate"]["join"] = "unavailable"
        item["diagnostic"] = diag
        vertices += item["work"]["vertices_charged"]
    if ordinal != len(calls):
        _fail()
    used = reserve_envelope(operation, item_count=len(items))
    for position, item in enumerate(items):
        if item["dispatch"] is None:
            _preflight(item, _ITEM_SLOT)
            continue
        # Validate the maximal local fallback (including the exhaustion object)
        # before using its fixed slot. Optional complete ledger replaces a slot.
        fallback = _fallback(item, "budget_exhausted")
        fallback["work"]["exhausted"] = {"limit": "serialized_bytes", "used_before": LIMITS["max_serialized_bytes"],
                                          "requested_units": LIMITS["max_serialized_bytes"] + 1}
        _preflight(fallback, _ITEM_SLOT)
        size = _preflight(item)
        extra = max(0, size - _ITEM_SLOT)
        if used + extra > LIMITS["max_serialized_bytes"]:
            fallback["work"]["exhausted"] = {"limit": "serialized_bytes", "used_before": used, "requested_units": min(extra, LIMITS["max_serialized_bytes"] + 1)}
            items[position] = fallback
        else:
            used += extra
    _totals(items, dispatches)
    _serialization_ledger(items, operation)
    result = _top(operation, count, items, attempts, dispatches, bindings)
    _preflight(result)
    return copy.deepcopy(result)


def build_disposition(observations, *, request, report, execution, bindings, derivation):
    """Finalize strict plain observations against the actual final report.

    An ordinary diagnostic remap/join fault disables its ledger, never changes
    the existing candidate. Cancellation from internal capabilities propagates.
    """
    try:
        return _build(observations, request, report, execution, bindings, derivation)
    except (KeyError, IndexError, TypeError, OverflowError, RecursionError) as error:
        raise ValueError("invalid OCR disposition inputs") from error


def validate_disposition(payload, *, request, report, execution, bindings, derivation):
    """Reject contradictory submissions; never repair malformed lineage."""
    try:
        if type(derivation) is not DispositionDerivation:
            _fail()
        _preflight(payload)
        operation, count, expected_items, attempts, calls, records = _context(request, report, execution, bindings)
        expected_top = _top(operation, count, [], attempts, [], bindings)
        _fields(payload, set(expected_top))
        for key in set(expected_top) - {"items", "summary", "dispatch_order"}:
            if not _same(payload[key], expected_top[key]):
                _fail()
        submitted = _list(payload["items"], 5000)
        if len(submitted) != len(expected_items):
            _fail()
        items = copy.deepcopy(submitted)
        for key, maximum in zip(_WORK_CAPS, (20000, 1000000, 100000, 1500000000)):
            if sum(_work(item["work"])[key] for item in items) > maximum:
                _fail()
        by_id, ordinal, dispatches, vertices = {}, 0, [], 0
        for item, expected in zip(items, expected_items):
            _fields(item, _ITEM_KEYS)
            for key in _ITEM_KEYS - {"dispatch", "candidate", "diagnostic", "work"}:
                if not _same(item[key], expected[key]):
                    _fail()
            _fields(item["candidate"], {"state", "sha256", "line_count", "error_code", "join"})
            for key in set(item["candidate"]) - {"join"}:
                if not _same(item["candidate"][key], expected["candidate"][key]):
                    _fail()
            _enum(item["candidate"]["join"], {"verified_ordered", "unavailable", "not_applicable"})
            by_id[item["item_id"]] = item
        for identifier in attempts:
            item = by_id[identifier]
            ordinal = _dispatch(item["dispatch"], item, calls, bindings, ordinal)
            if item["dispatch"] is not None:
                dispatches.append(identifier)
        if ordinal != len(calls) or not _same(payload["dispatch_order"], dispatches):
            _fail()
        for identifier in attempts + [item["item_id"] for item in items if item["attempt_index"] is None]:
            item, row = by_id[identifier], records[identifier]
            diag = item["diagnostic"]
            _diagnostic(diag, item, request["configuration"]["policy"], final=True)
            mapping = _mapping(operation, row) if diag["state"] in {"complete", "partial"} else None
            if not _same(diag["mapping"], mapping):
                _fail()
            _mapping_raster(mapping, diag["raster"])
            for record in diag["detections"]:
                _physical_checked(record["physical"])
            proposed_join = item["candidate"]["join"]
            item["candidate"]["join"] = "unavailable" if item["candidate"]["state"] == "accepted" else "not_applicable"
            _reconcile(diag, item, row, derivation, validating=True, total_vertices=vertices)
            vertices += item["work"]["vertices_charged"]
            if item["candidate"]["join"] != proposed_join:
                _fail()
        _totals(items, dispatches)
        _serialization_ledger(items, operation)
        if not _same(payload["summary"], _summary(items, attempts, dispatches)):
            _fail()
        return copy.deepcopy(payload)
    except (KeyError, IndexError, TypeError, OverflowError, RecursionError) as error:
        raise ValueError("invalid OCR disposition inputs") from error


__all__ = ["RECIPE_ID", "LIMITS", "SCOPE", "MAX_OBSERVATION_BYTES", "DispositionDerivation", "canonical_sha256", "zero_work", "reserve_envelope", "observation_representation_bound",
           "build_disposition", "validate_disposition"]
