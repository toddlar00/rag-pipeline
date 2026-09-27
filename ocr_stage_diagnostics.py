"""Strict, bounded diagnostics separating detector output from recognition.

Gold lines and cell containers are operator declarations, not authenticated
ground truth. Geometry overlap is a diagnostic proxy; it cannot establish that
all text in a scan was annotated. Existing recovery candidates are deliberately
not accepted as raw detector observations.
"""

from __future__ import annotations

import copy
import math
import re
import struct

from evaluation_inputs import _hex_digest
from ocr_evaluation import evaluate_ocr, normalize_text


MAX_PAGES = 8
MAX_LINES_PER_PAGE = 200
MAX_TOTAL_LINES = 1000
MAX_RECOGNITION_REGIONS = 64
MAX_DETECTIONS_PER_PAGE = 2000
MAX_LINE_CHARACTERS = 4096
MAX_TOTAL_CHARACTERS = 200_000
MAX_PAIR_COMPARISONS = 4_000_000
MAX_CALLS = 2 * MAX_PAGES + MAX_RECOGNITION_REGIONS
MAX_RASTER_SIDE = 6000
MAX_PAGE_PIXELS = 25_000_000
MAX_TOTAL_PIXELS = 100_000_000
MAX_ALIGNMENT_CELLS = 20_000_000
DEFAULT_CONFIGURATION = {
    "dpi": 300, "preprocessing": "none",
    "detection_mode": "postprocessed_pre_recognition",
    "gold_crop_mode": "detector_and_classifier_bypassed",
    "crop_policy": "outward_integer_bounds",
    "max_normalized_recognition_width": 4096,
}
_ID = re.compile(r"[A-Za-z0-9][A-Za-z0-9._:-]{0,63}")
_REASONS = {"render_failed", "resource_limit", "stage_failed", "invalid_stage_output", "geometry_unavailable"}
_FLAGS = {"detection": (True, False, False), "full_page": (True, True, True),
          "gold_crop": (False, False, True)}
_ROLES = ("detection", "classification", "recognition")
_OVERLAP_FRACTION = 0.5
_MATCH_IOU = 0.5


def _object(value: object, fields: set[str]) -> dict:
    if not isinstance(value, dict) or set(value) != fields:
        raise ValueError("invalid stage diagnostic fields")
    return value


def _integer(value: object, low: int, high: int) -> int:
    if type(value) is not int or not low <= value <= high:
        raise ValueError("invalid stage diagnostic integer")
    return value


def _number(value: object, low: float, high: float) -> float:
    if type(value) not in (int, float):
        raise ValueError("invalid stage diagnostic number")
    try:
        result = float(value)
    except (OverflowError, ValueError):
        raise ValueError("invalid stage diagnostic number") from None
    if not math.isfinite(result) or not low <= result <= high:
        raise ValueError("invalid stage diagnostic number")
    return result


def _identifier(value: object) -> str:
    if not isinstance(value, str) or _ID.fullmatch(value) is None:
        raise ValueError("invalid stage diagnostic identifier")
    return value


def _text(value: object, *, nonempty: bool = False) -> str:
    if not isinstance(value, str) or len(value) > MAX_LINE_CHARACTERS:
        raise ValueError("invalid stage diagnostic text")
    try:
        value.encode("utf-8")
    except UnicodeError:
        raise ValueError("invalid stage diagnostic Unicode") from None
    if nonempty and not value.strip():
        raise ValueError("gold lines require nonempty text")
    return value


def _list(value: object, maximum: int, *, minimum: int = 0) -> list:
    if not isinstance(value, list) or not minimum <= len(value) <= maximum:
        raise ValueError("stage diagnostic collection exceeds bounds")
    return value


def _bbox(value: object) -> list:
    result = _list(value, 4, minimum=4)
    numbers = [_number(item, 0, 1) for item in result]
    if (not numbers[0] < numbers[2] or not numbers[1] < numbers[3]
            or (numbers[2] - numbers[0]) * (numbers[3] - numbers[1]) <= 0):
        raise ValueError("invalid stage reference rectangle")
    return result


def _geometry(value: object) -> dict:
    geometry = _object(value, {"width_points", "height_points", "rotation"})
    _number(geometry["width_points"], 0.01, 100_000)
    _number(geometry["height_points"], 0.01, 100_000)
    if type(geometry["rotation"]) is not int or geometry["rotation"] not in (0, 90, 180, 270):
        raise ValueError("invalid stage page rotation")
    return geometry


def raster_dimensions(geometry: object, dpi: int) -> tuple[int, int]:
    """MuPDF zero-origin float32 scale and fz_round_rect's 0.001f bias.

    This characterizes the current unprocessed renderer; runtime additionally
    compares the actual page Rect/Matrix/Pixmap, rejecting nonzero origins.
    """
    geometry = _geometry(geometry)
    _integer(dpi, 72, 600)

    def f32(value):
        return struct.unpack("!f", struct.pack("!f", value))[0]

    scale, bias = f32(dpi / 72), f32(0.001)
    return tuple(math.ceil(f32(f32(f32(geometry[key]) * scale) - bias))
                 for key in ("width_points", "height_points"))


def _inside(inner: list, outer: list) -> bool:
    return outer[0] <= inner[0] < inner[2] <= outer[2] and outer[1] <= inner[1] < inner[3] <= outer[3]


def _rect_overlap(first: list, second: list) -> bool:
    return min(first[2], second[2]) > max(first[0], second[0]) and min(first[3], second[3]) > max(first[1], second[1])


def validate_stage_reference(payload: object) -> dict:
    """Validate complete-selected-page line annotations and separate cells."""
    value = _object(payload, {"schema_version", "kind", "source_sha256", "page_count", "scope",
                              "coordinate_system", "annotation_provenance", "pages"})
    if (_integer(value["schema_version"], 1, 1) != 1 or value["kind"] != "ocr_stage_reference"
            or value["scope"] != "operator_declared_complete_selected_pages"
            or value["coordinate_system"] != "original_page_display_fraction"
            or value["annotation_provenance"] not in ("operator_declared", "synthetic_generator")):
        raise ValueError("invalid stage reference header")
    _hex_digest(value["source_sha256"], label="stage source")
    page_count = _integer(value["page_count"], 1, 5000)
    total_lines = total_text = total_crops = 0
    line_ids, cell_ids, pages_seen = set(), set(), []
    for page in _list(value["pages"], MAX_PAGES, minimum=1):
        page = _object(page, {"page_number", "geometry", "lines", "cells", "recognition_region_ids"})
        pages_seen.append(_integer(page["page_number"], 1, page_count))
        _geometry(page["geometry"])
        cells = {}
        for cell in _list(page["cells"], MAX_LINES_PER_PAGE):
            cell = _object(cell, {"cell_id", "bbox", "table_id", "row", "column", "row_span", "column_span"})
            cid = _identifier(cell["cell_id"])
            if cid in cell_ids:
                raise ValueError("stage cell identifiers repeat")
            cell_ids.add(cid)
            _identifier(cell["table_id"])
            _bbox(cell["bbox"])
            row = _integer(cell["row"], 0, 999)
            column = _integer(cell["column"], 0, 999)
            row_span = _integer(cell["row_span"], 1, 1000 - row)
            column_span = _integer(cell["column_span"], 1, 1000 - column)
            for previous in cells.values():
                if previous["table_id"] != cell["table_id"]:
                    continue
                grid_overlap = (row < previous["row"] + previous["row_span"]
                                and previous["row"] < row + row_span
                                and column < previous["column"] + previous["column_span"]
                                and previous["column"] < column + column_span)
                if grid_overlap or _rect_overlap(previous["bbox"], cell["bbox"]):
                    raise ValueError("stage table cells overlap")
            cells[cid] = cell
        local_ids = []
        for index, line in enumerate(_list(page["lines"], MAX_LINES_PER_PAGE)):
            line = _object(line, {"region_id", "bbox", "text", "order", "cell_id"})
            rid = _identifier(line["region_id"])
            if rid in line_ids:
                raise ValueError("stage line identifiers repeat")
            line_ids.add(rid)
            local_ids.append(rid)
            _bbox(line["bbox"])
            total_text += len(_text(line["text"], nonempty=True))
            if _integer(line["order"], 0, MAX_LINES_PER_PAGE - 1) != index:
                raise ValueError("stage line order must be complete and canonical")
            if line["cell_id"] is not None:
                cid = _identifier(line["cell_id"])
                if cid not in cells or not _inside(line["bbox"], cells[cid]["bbox"]):
                    raise ValueError("stage line is detached from its cell")
        selected = _list(page["recognition_region_ids"], MAX_RECOGNITION_REGIONS)
        for rid in selected:
            _identifier(rid)
        if len(set(selected)) != len(selected) or any(rid not in local_ids for rid in selected):
            raise ValueError("stage recognition selections are invalid")
        total_crops += len(selected)
        total_lines += len(local_ids)
    if (pages_seen != sorted(set(pages_seen)) or total_lines > MAX_TOTAL_LINES
            or total_text > MAX_TOTAL_CHARACTERS or total_crops > MAX_RECOGNITION_REGIONS):
        raise ValueError("stage reference ordering or work budget is invalid")
    return copy.deepcopy(value)


def _area(points: list) -> float:
    return abs(sum(p[0] * q[1] - q[0] * p[1] for p, q in zip(points, points[1:] + points[:1]))) / 2


def _quad(value: object, width: int, height: int) -> list:
    points = _list(value, 4, minimum=4)
    checked = []
    for point in points:
        _list(point, 2, minimum=2)
        checked.append([_number(point[0], 0, width), _number(point[1], 0, height)])
    crosses = []
    for index in range(4):
        a, b, c = checked[index], checked[(index + 1) % 4], checked[(index + 2) % 4]
        crosses.append((b[0] - a[0]) * (c[1] - b[1]) - (b[1] - a[1]) * (c[0] - b[0]))
    if _area(checked) <= 0 or not (all(cross > 0 for cross in crosses) or all(cross < 0 for cross in crosses)):
        raise ValueError("stage detector quadrilateral is degenerate or not convex")
    return checked


def _status(value: dict, *, crop: bool = False) -> None:
    status, reason = value["status"], value["reason"]
    if status not in ("available", "empty", "unavailable"):
        raise ValueError("invalid stage availability")
    if status == "unavailable":
        if not isinstance(reason, str) or reason not in _REASONS:
            raise ValueError("unavailable stage needs a fixed reason")
    elif reason is not None or value["call_id"] is None:
        raise ValueError("available stage needs an actual call")
    if value["call_id"] is not None and (not isinstance(value["call_id"], str)
                                        or re.fullmatch(r"call-[0-9]{4}", value["call_id"]) is None):
        raise ValueError("invalid stage call identifier")
    if crop:
        if status == "unavailable":
            if value["text"] is not None:
                raise ValueError("unavailable crop cannot fabricate text")
        else:
            text = _text(value["text"])
            if bool(text.strip()) != (status == "available"):
                raise ValueError("crop text and availability disagree")


def validate_stage_observation(payload: object, reference: object, reference_sha256: str) -> dict:
    """Check stage identity, complete coverage, geometry and actual-call joins.

    The IO layer additionally joins outer calls to the runtime execution receipt.
    This validator alone does not authenticate an artifact's author or runtime.
    """
    reference = validate_stage_reference(reference)
    _hex_digest(reference_sha256, label="stage reference")
    value = _object(payload, {"schema_version", "kind", "source_sha256", "reference_sha256", "page_count",
                              "configuration", "pages", "calls", "canonical_extraction_modified", "requires_attention"})
    if (_integer(value["schema_version"], 1, 1) != 1 or value["kind"] != "ocr_stage_observation"
            or value["source_sha256"] != reference["source_sha256"]
            or value["reference_sha256"] != reference_sha256
            or _integer(value["page_count"], 1, 5000) != reference["page_count"]
            or value["canonical_extraction_modified"] is not False or value["requires_attention"] is not True):
        raise ValueError("stage observation is detached from its inputs")
    config = _object(value["configuration"], set(DEFAULT_CONFIGURATION))
    _integer(config["dpi"], 72, 600)
    for key, expected in DEFAULT_CONFIGURATION.items():
        if key != "dpi" and (type(config[key]) is not type(expected) or config[key] != expected):
            raise ValueError("unsupported stage configuration")
    calls = {}
    for index, call in enumerate(_list(value["calls"], MAX_CALLS), 1):
        call = _object(call, {"id", "stage", "page_number", "region_id", "use_det", "use_cls", "use_rec", "status", "roles"})
        if call["id"] != f"call-{index:04d}" or not isinstance(call["stage"], str) or call["stage"] not in _FLAGS:
            raise ValueError("stage calls are not canonical")
        _integer(call["page_number"], 1, reference["page_count"])
        if call["stage"] == "gold_crop":
            _identifier(call["region_id"])
        elif call["region_id"] is not None:
            raise ValueError("page stage cannot claim a crop identifier")
        for key, expected in zip(("use_det", "use_cls", "use_rec"), _FLAGS[call["stage"]]):
            if call[key] is not expected:
                raise ValueError("stage call did not explicitly select the expected engine mode")
        if call["status"] not in ("completed", "failed"):
            raise ValueError("invalid stage call outcome")
        roles = _object(call["roles"], set(_ROLES))
        for role, enabled in zip(_ROLES, _FLAGS[call["stage"]]):
            counts = _object(roles[role], {"attempted", "completed", "failed"})
            for count in counts.values():
                _integer(count, 0, 1)
            if counts["attempted"] != counts["completed"] + counts["failed"] or (not enabled and counts["attempted"]):
                raise ValueError("invalid actual role-call accounting")
        calls[call["id"]] = call
    pages = _list(value["pages"], MAX_PAGES)
    if len(pages) != len(reference["pages"]):
        raise ValueError("stage observation omits selected pages")
    used_calls, text_count, total_pixels = [], 0, 0
    for page, gold in zip(pages, reference["pages"]):
        page = _object(page, {"page_number", "geometry", "raster", "detection", "full_page", "gold_crops"})
        if type(page["page_number"]) is not int or page["page_number"] != gold["page_number"]:
            raise ValueError("stage page sequence differs from reference")
        if page["geometry"] is not None and _geometry(page["geometry"]) != gold["geometry"]:
            raise ValueError("stage reference geometry differs from source")
        raster = page["raster"]
        width = height = 0
        if raster is not None:
            _object(raster, {"width", "height", "pixel_sha256"})
            width = _integer(raster["width"], 1, MAX_RASTER_SIDE)
            height = _integer(raster["height"], 1, MAX_RASTER_SIDE)
            total_pixels += width * height
            if width * height > MAX_PAGE_PIXELS or total_pixels > MAX_TOTAL_PIXELS or page["geometry"] is None:
                raise ValueError("stage raster exceeds its bounds")
            if (width, height) != raster_dimensions(page["geometry"], config["dpi"]):
                raise ValueError("stage raster differs from source geometry and DPI")
            _hex_digest(raster["pixel_sha256"], label="stage raster")
        for stage, array_key in (("detection", "boxes"), ("full_page", "lines")):
            result = _object(page[stage], {"status", "reason", "call_id", array_key})
            _status(result)
            items = _list(result[array_key], MAX_DETECTIONS_PER_PAGE)
            if bool(items) != (result["status"] == "available"):
                raise ValueError("stage geometry and availability disagree")
            if items and raster is None:
                raise ValueError("stage boxes lack an observed raster")
            for item in items:
                box = item
                if stage == "full_page":
                    _object(item, {"text", "box"})
                    text_count += len(_text(item["text"], nonempty=True))
                    box = item["box"]
                _quad(box, width, height)
            _join_call(result, stage, page["page_number"], None, calls, used_calls, raster)
        crops = _list(page["gold_crops"], MAX_RECOGNITION_REGIONS)
        if len(crops) != len(gold["recognition_region_ids"]):
            raise ValueError("stage observation omits selected gold crops")
        for crop, rid in zip(crops, gold["recognition_region_ids"]):
            crop = _object(crop, {"region_id", "status", "reason", "call_id", "raster_bbox", "pixel_sha256", "text"})
            if crop["region_id"] != rid:
                raise ValueError("stage gold crop sequence differs from reference")
            _status(crop, crop=True)
            if crop["text"] is not None:
                text_count += len(crop["text"])
            if (crop["raster_bbox"] is None) != (crop["pixel_sha256"] is None):
                raise ValueError("stage crop pixel binding is incomplete")
            if crop["raster_bbox"] is not None:
                if raster is None:
                    raise ValueError("stage crop lacks page raster")
                bounds = _list(crop["raster_bbox"], 4, minimum=4)
                for item, bound in zip(bounds, (width, height, width, height)):
                    _integer(item, 0, bound)
                if not bounds[0] < bounds[2] or not bounds[1] < bounds[3]:
                    raise ValueError("stage crop is empty")
                line = next(line for line in gold["lines"] if line["region_id"] == rid)
                expected = [math.floor(line["bbox"][0] * width), math.floor(line["bbox"][1] * height),
                            math.ceil(line["bbox"][2] * width), math.ceil(line["bbox"][3] * height)]
                if bounds != expected:
                    raise ValueError("stage crop does not match its gold rectangle")
                if (crop["call_id"] is not None and max(320, math.ceil(48 * (bounds[2] - bounds[0]) / (bounds[3] - bounds[1])))
                        > config["max_normalized_recognition_width"]):
                    raise ValueError("called gold crop exceeds normalized recognition width")
                _hex_digest(crop["pixel_sha256"], label="stage crop")
            if crop["call_id"] is not None and crop["raster_bbox"] is None:
                raise ValueError("called crop lacks observed pixels")
            _join_call(crop, "gold_crop", page["page_number"], rid, calls, used_calls, raster)
    if used_calls != list(calls) or text_count > MAX_TOTAL_CHARACTERS:
        raise ValueError("stage calls are detached, duplicated, unordered or over budget")
    return copy.deepcopy(value)


def _join_call(result: dict, stage: str, page: int, region: str | None,
               calls: dict, used: list, raster: dict | None) -> None:
    cid = result["call_id"]
    if cid is None:
        return
    if cid not in calls or raster is None:
        raise ValueError("stage result lacks an actual invocation or raster")
    call = calls[cid]
    if (call["stage"], call["page_number"], call["region_id"]) != (stage, page, region):
        raise ValueError("stage result points to a different invocation")
    if result["status"] != "unavailable":
        if call["status"] != "completed" or any(role["failed"] for role in call["roles"].values()):
            raise ValueError("successful stage points to a failed invocation")
        required = ("recognition",) if stage == "gold_crop" else ("detection",)
        if stage == "full_page" and result["status"] == "available":
            required = ("detection", "classification", "recognition")
        if any(call["roles"][role]["completed"] != 1 for role in required):
            raise ValueError("stage evidence did not observe its required components")
        if (stage == "full_page" and result["status"] == "empty"
                and tuple(call["roles"][role]["completed"] for role in _ROLES) not in ((1, 0, 0), (1, 1, 1))):
            raise ValueError("empty full-page output has an incomplete component route")
    elif (result["reason"] == "stage_failed" and call["status"] != "failed"
          and not any(role["failed"] for role in call["roles"].values())):
        raise ValueError("failed stage contradicts observed outer call")
    used.append(cid)


def _intersection_area(quad: list, rectangle: list) -> float:
    """Clip one convex detector polygon to a gold axis-aligned rectangle."""
    points = quad
    for axis, bound, keep_greater in ((0, rectangle[0], True), (0, rectangle[2], False),
                                     (1, rectangle[1], True), (1, rectangle[3], False)):
        if not points:
            return 0.0
        output = []
        previous = points[-1]
        previous_inside = previous[axis] >= bound if keep_greater else previous[axis] <= bound
        for current in points:
            inside = current[axis] >= bound if keep_greater else current[axis] <= bound
            if inside != previous_inside:
                fraction = (bound - previous[axis]) / (current[axis] - previous[axis])
                point = [previous[k] + fraction * (current[k] - previous[k]) for k in (0, 1)]
                point[axis] = bound
                output.append(point)
            if inside:
                output.append(current)
            previous, previous_inside = current, inside
        points = output
    return _area(points) if len(points) >= 3 else 0.0


def _graph(gold: dict, boxes: list, raster: dict | None, *, available: bool) -> dict:
    if not available or raster is None:
        return {"status": "unavailable", "reference_lines": len(gold["lines"]),
                "observed_boxes": None, "matched_lines": None, "no_overlap_lines": None,
                "split_candidates": None, "merge_candidates": None, "unmatched_boxes": None,
                "weak_geometry_pairs": None, "unambiguous_line_recall": None,
                "unambiguous_box_precision": None, "order_inversions": None,
                "order_comparable_pairs": None, "order_inversion_rate": None,
                "lines": [], "boxes": []}
    width, height = raster["width"], raster["height"]
    scaled = [[line["bbox"][0] * width, line["bbox"][1] * height,
               line["bbox"][2] * width, line["bbox"][3] * height] for line in gold["lines"]]
    edges = [[] for _ in scaled]
    reverse = [[] for _ in boxes]
    ious = {}
    areas = [_area(box) for box in boxes]
    for left, rectangle in enumerate(scaled):
        gold_area = (rectangle[2] - rectangle[0]) * (rectangle[3] - rectangle[1])
        for right, quad in enumerate(boxes):
            overlap = min(gold_area, areas[right], _intersection_area(quad, rectangle))
            if overlap > 0 and overlap / min(gold_area, areas[right]) >= _OVERLAP_FRACTION:
                edges[left].append(right)
                reverse[right].append(left)
                ious[(left, right)] = overlap / (gold_area + areas[right] - overlap)
    lines, matched, weak = [], [], 0
    for index, (line, candidates) in enumerate(zip(gold["lines"], edges)):
        status, matched_index, iou = "no_overlap", None, None
        if len(candidates) > 1:
            status = "split_or_ambiguous"
        elif len(candidates) == 1:
            candidate = candidates[0]
            iou = ious[(index, candidate)]
            if len(reverse[candidate]) != 1:
                status = "merge_or_ambiguous"
            elif iou < _MATCH_IOU:
                status = "weak_geometry"
                weak += 1
            else:
                status, matched_index = "one_to_one", candidate
                matched.append((index, candidate))
        lines.append({"region_id": line["region_id"], "status": status,
                      "overlapping_box_indexes": candidates, "matched_box_index": matched_index, "iou": iou})
    inversions = sum(second[1] < first[1] for i, first in enumerate(matched) for second in matched[i + 1:])
    pairs = len(matched) * (len(matched) - 1) // 2
    return {"status": "evaluated", "reference_lines": len(scaled), "observed_boxes": len(boxes),
            "matched_lines": len(matched), "no_overlap_lines": sum(not values for values in edges),
            "split_candidates": sum(len(values) > 1 for values in edges),
            "merge_candidates": sum(len(values) > 1 for values in reverse),
            "unmatched_boxes": sum(not values for values in reverse), "weak_geometry_pairs": weak,
            "unambiguous_line_recall": len(matched) / len(scaled) if scaled else None,
            "unambiguous_box_precision": len(matched) / len(boxes) if boxes else None,
            "order_inversions": inversions, "order_comparable_pairs": pairs,
            "order_inversion_rate": inversions / pairs if pairs else None, "lines": lines,
            "boxes": [{"box_index": index, "overlapping_region_ids": [gold["lines"][i]["region_id"] for i in values]}
                      for index, values in enumerate(reverse)]}


class _AlignmentBudget:
    """One conservative DP-cell allowance shared by every diagnostic view."""

    def __init__(self):
        self.used_cells = 0

    def reserve(self, pairs: list) -> bool:
        cells = sum(len(left) * len(right) + len(left.split()) * len(right.split()) for _, left, right in pairs)
        if self.used_cells + cells > MAX_ALIGNMENT_CELLS:
            return False
        self.used_cells += cells
        return True


def _metrics(pairs: list[tuple[str, str, str]], budget: _AlignmentBudget) -> dict:
    if not pairs:
        return {"status": "unavailable", "reason": "no_comparable_observations", "metrics": None}
    pairs = [(rid, normalize_text(left), normalize_text(right)) for rid, left, right in pairs]
    if (len(pairs) > 256 or any(max(len(left), len(right)) > 20_000 for _, left, right in pairs)
            or not budget.reserve(pairs)):
        return {"status": "unavailable", "reason": "alignment_limit", "metrics": None}
    result = evaluate_ocr({"schema_version": 1, "records": [
        {"id": rid, "reference": left, "prediction": right, "critical_tokens": []}
        for rid, left, right in pairs]})
    return {"status": "evaluated", "reason": None, "metrics": result}


def _cell_diagnostics(gold: dict, full_graph: dict, full_lines: list, budget: _AlignmentBudget) -> list:
    by_id = {line["region_id"]: line for line in gold["lines"]}
    mapped = {line["region_id"]: line["matched_box_index"] for line in full_graph["lines"]}
    cells = []
    for cell in gold["cells"]:
        line_ids = [rid for rid, line in by_id.items() if line["cell_id"] == cell["cell_id"]]
        complete = bool(line_ids) and all(mapped.get(rid) is not None for rid in line_ids)
        expected = "\n".join(by_id[rid]["text"] for rid in line_ids)
        predicted = "\n".join(full_lines[mapped[rid]]["text"] for rid in line_ids) if complete else None
        cells.append({"cell_id": cell["cell_id"], "table_id": cell["table_id"],
                      "row": cell["row"], "column": cell["column"], "row_span": cell["row_span"],
                      "column_span": cell["column_span"], "reference_line_count": len(line_ids),
                      "one_to_one_line_count": sum(mapped.get(rid) is not None for rid in line_ids),
                      "text": _metrics([(cell["cell_id"], expected, predicted)], budget) if complete else {
                          "status": "unavailable", "reason": "empty_cell_or_incomplete_line_mapping", "metrics": None}})
    return cells


def evaluate_stage_diagnostics(reference: object, observation: object,
                               reference_sha256: str, observation_sha256: str) -> dict:
    """Report detector geometry, retained-line layout and bypassed-crop OCR.

    Recognition on gold crops is a controlled diagnostic with different input
    geometry, not an additive decomposition of full-page error or a deployable
    accuracy gain. Missing stages never enter denominators as successful text.
    """
    reference = validate_stage_reference(reference)
    observation = validate_stage_observation(observation, reference, reference_sha256)
    _hex_digest(observation_sha256, label="stage observation")
    work = sum(len(gold["lines"]) * (len(page["detection"]["boxes"]) + len(page["full_page"]["lines"]))
               for gold, page in zip(reference["pages"], observation["pages"]))
    if work > MAX_PAIR_COMPARISONS:
        raise ValueError("stage geometry exceeds pair-work budget")
    pages, gold_pairs, paired_full, paired_gold, page_pairs = [], [], [], [], []
    budget = _AlignmentBudget()
    for gold, page in zip(reference["pages"], observation["pages"]):
        detector = _graph(gold, page["detection"]["boxes"], page["raster"],
                          available=page["detection"]["status"] != "unavailable")
        full = _graph(gold, [line["box"] for line in page["full_page"]["lines"]], page["raster"],
                      available=page["full_page"]["status"] != "unavailable")
        if page["full_page"]["status"] != "unavailable":
            page_pairs.append((f"page-{page['page_number']}", "\n".join(line["text"] for line in gold["lines"]),
                               "\n".join(line["text"] for line in page["full_page"]["lines"])))
        line_map = {line["region_id"]: line for line in gold["lines"]}
        full_map = {line["region_id"]: line["matched_box_index"] for line in full["lines"]}
        detector_map = {line["region_id"]: line["status"] for line in detector["lines"]}
        crops = []
        for crop in page["gold_crops"]:
            rid = crop["region_id"]
            expected = line_map[rid]["text"]
            available = crop["status"] != "unavailable"
            metric = _metrics([(rid, expected, crop["text"])], budget) if available else {
                "status": "unavailable", "reason": crop["reason"], "metrics": None}
            if available:
                gold_pairs.append((rid, expected, crop["text"]))
            full_index = full_map.get(rid)
            if available and full_index is not None:
                paired_full.append((rid, expected, page["full_page"]["lines"][full_index]["text"]))
                paired_gold.append((rid, expected, crop["text"]))
            crops.append({"region_id": rid, "status": crop["status"], "reason": crop["reason"],
                          "detector_geometry": detector_map.get(rid, "unavailable"),
                          "full_page_box_index": full_index, "recognition": metric})
        pages.append({"page_number": page["page_number"], "detection": detector, "retained_full_page_lines": full,
                      "gold_crops": crops, "cells": _cell_diagnostics(gold, full, page["full_page"]["lines"], budget)})
    selected_pages = [page["page_number"] for page in reference["pages"]]
    return {"schema_version": 1, "kind": "ocr_stage_diagnostics", "source_sha256": reference["source_sha256"],
            "reference_sha256": reference_sha256, "observation_sha256": observation_sha256,
            "annotation_provenance": reference["annotation_provenance"], "pages": pages,
            "coverage": {"source_pages": reference["page_count"], "selected_pages": selected_pages,
                         "unselected_page_count": reference["page_count"] - len(selected_pages),
                         "reference_lines": sum(len(page["lines"]) for page in reference["pages"]),
                         "selected_gold_crops": sum(len(page["gold_crops"]) for page in observation["pages"]),
                         "available_gold_crops": len(gold_pairs), "paired_full_and_gold_crops": len(paired_gold),
                         "available_full_pages": len(page_pairs)},
            "metrics": {"full_pages": _metrics(page_pairs, budget), "gold_crop_recognition": _metrics(gold_pairs, budget),
                        "paired_full_page_lines": _metrics(paired_full, budget), "paired_gold_crop_recognition": _metrics(paired_gold, budget)},
            "alignment_work": {"maximum_cells": MAX_ALIGNMENT_CELLS, "reserved_cells": budget.used_cells,
                               "accounting": "normalized character plus word worst-case DP cells, shared by all localized and aggregate views",
                               "priority": "reference page order: selected crops then cells; followed by complete aggregate views"},
            "geometry_policy": {"edge_minimum_smaller_area_overlap": _OVERLAP_FRACTION,
                                "one_to_one_minimum_iou": _MATCH_IOU,
                                "matching": "unique bilateral overlap only; no forced ambiguous assignment",
                                "order": "inversions only among one-to-one matched lines; coverage reported separately",
                                "cell_scope": "matched line text joined in gold order; not within-cell reading-order or predicted table-structure accuracy"},
            "limitations": ["Selected-page annotation completeness is declared, not independently established.",
                            "Detector output is postprocessed pre-recognition geometry, not raw model logits.",
                            "Gold-crop recognition bypasses detection and classification; crop context differs from full-page OCR.",
                            "Full-page versus gold-crop errors are not an additive causal decomposition or representative gain.",
                            "Detector and OCR agreement cannot establish correctness; unavailable work is not blank text."],
            "canonical_extraction_modified": False, "requires_attention": True}
