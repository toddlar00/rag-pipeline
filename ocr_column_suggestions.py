"""Unconfirmed two-column hypotheses from saved OCR geometry, never prose proof.

No reference text/order, source pixels, files, models or recognition are read.
An indistinguishable table can yield the same hypothesis as prose. Preserving
every observed line cannot establish that the original scan had no omissions.
"""

from __future__ import annotations

import hashlib
import json
import statistics

from evaluation_inputs import _hex_digest
import ocr_layout
from ocr_recovery_comparison import validate_recovery_report


MAX_PAGES = 20
MAX_LINES = 2000
MAX_GEOMETRY_WORK = 1_000_000
MAX_OUTPUT_BYTES = 1024 * 1024
ALGORITHM = "unconfirmed-geometry-two-column-v1"
_DIGEST_POLICY = {
    "algorithm": "sha256", "domain": "rag-pipeline:ocr-column-candidate:v1",
    "serialization": "utf8-sorted-compact-json", "is_file_digest": False,
}
_ATTESTATIONS = {
    "requires_attention": True, "operator_confirmation_required": True,
    "automatic_application_supported": False, "accuracy_verified": False,
    "full_page_coverage_verified": False, "canonical_extraction_modified": False,
    "recognition_rerun": False,
}


def _parameters() -> dict:
    return {
        "max_requested_pages": MAX_PAGES, "max_lines_per_page": MAX_LINES,
        "max_geometry_work_units": MAX_GEOMETRY_WORK,
        "geometry_work_units": "sum(12*n+3*n*ceil(log2(n+1)))_for_eligible_pages",
        "vertical_band_gap_in_median_heights": 2.5,
        "minimum_dense_band_lines": 6, "minimum_lines_per_column": 3,
        "minimum_gutter_in_median_heights": 2.0,
        "minimum_gutter_in_page_width": 0.015,
        "supported_candidate_coordinate_system": "rendered_image_pixels",
        "permutation_guard": "ocr_layout._proposal_explicit_two_column_v1",
    }


def _canonical(value: object) -> bytes:
    return json.dumps(value, sort_keys=True, ensure_ascii=False, allow_nan=False,
                      separators=(",", ":")).encode("utf-8")


def _candidate_digest(candidate: dict) -> str:
    return hashlib.sha256(_DIGEST_POLICY["domain"].encode("ascii") + b"\0" + _canonical(candidate)).hexdigest()


def _requested(value: object, page_count: int) -> list[int]:
    if (type(value) is not list or not 1 <= len(value) <= MAX_PAGES
            or any(type(number) is not int or not 1 <= number <= page_count for number in value)
            or len(set(value)) != len(value)):
        raise ValueError("column suggestions require 1..20 unique explicit source pages")
    return sorted(value)


def _eligible(candidate: dict | None) -> bool:
    return bool(candidate is not None and candidate["text"].strip()
                and candidate["raster"]["coordinate_system"] == "rendered_image_pixels"
                and 0 < len(candidate["lines"]) <= MAX_LINES)


def _infer(boxes: list, width: int, height: int) -> dict:
    """Geometry-only inference; no text, score, reference or semantic label seam."""
    result = {"status": "abstained", "reason": None, "plan": None,
              "line_order": None, "band_counts": [], "column_counts": []}

    def abstain(reason):
        result["reason"] = reason
        return result

    rectangles = []
    for box in boxes:
        rectangle = ocr_layout._rectangle(box)
        if rectangle is None:
            return abstain("ambiguous_geometry")
        rectangles.append(rectangle)
    typical_height = statistics.median(rectangle[3] - rectangle[1] for rectangle in rectangles)
    gap_limit = typical_height * 2.5
    # Track each band's bottom incrementally: no quadratic rescan of a dense
    # band. Sorting is bounded by the whole-cohort work preflight.
    bands = []
    band_bottom = None
    for index in sorted(range(len(boxes)), key=lambda i: (rectangles[i][1], rectangles[i][3], i)):
        if band_bottom is None or rectangles[index][1] - band_bottom > gap_limit:
            bands.append([index])
            band_bottom = rectangles[index][3]
        else:
            bands[-1].append(index)
            band_bottom = max(band_bottom, rectangles[index][3])
    result["band_counts"] = [len(band) for band in bands]
    dense = [index for index, band in enumerate(bands) if len(band) >= 6]
    if len(dense) != 1:
        return abstain("no_unique_dense_body_band")
    body_index = dense[0]
    body = bands[body_index]
    components = []
    for index in sorted(body, key=lambda i: (rectangles[i][0], rectangles[i][2], i)):
        if not components or rectangles[index][0] > components[-1]["right"]:
            components.append({"left": rectangles[index][0], "right": rectangles[index][2], "lines": [index]})
        else:
            components[-1]["right"] = max(components[-1]["right"], rectangles[index][2])
            components[-1]["lines"].append(index)
    result["column_counts"] = [len(component["lines"]) for component in components]
    if len(components) != 2:
        return abstain("not_exactly_two_disjoint_x_components")
    left, right = components
    if min(result["column_counts"]) < 3:
        return abstain("insufficient_column_support")
    gutter_width = right["left"] - left["right"]
    if gutter_width < max(typical_height * 2.0, width * 0.015):
        return abstain("gutter_support_too_narrow")
    upper = min(rectangles[i][1] for i in body)
    lower = max(rectangles[i][3] for i in body)
    upper_neighbor = max(rectangles[i][3] for i in bands[body_index - 1]) if body_index else 0
    lower_neighbor = min(rectangles[i][1] for i in bands[body_index + 1]) if body_index + 1 < len(bands) else height
    plan = {
        "body_band": [(upper_neighbor + upper) / (2 * height), (lower + lower_neighbor) / (2 * height)],
        "gutter": [(left["right"] + gutter_width / 3) / width,
                   (right["left"] - gutter_width / 3) / width],
        "order": "left_then_right",
    }
    # The inherited guard receives no real text or scores. Its empty-text
    # guard was already handled by admission; line identity remains positional.
    geometry = {"text": "geometry only", "lines": [{"box": box} for box in boxes],
                "raster": {"width": width, "height": height}}
    order, status, reason = ocr_layout._proposal(geometry, plan)
    if status == "abstained":
        return abstain(reason)
    if sorted(order) != list(range(len(boxes))):
        raise ValueError("column hypothesis does not preserve every observed line")
    result.update(status="unconfirmed_hypothesis", plan=plan, line_order=order,
                  reason="already_engine_ordered_hypothesis" if status == "unchanged" else "geometry_two_column_hypothesis")
    return result


def _page(number: int, page: dict | None, *, deferred: bool, work_allowed: bool) -> dict:
    candidate = page["candidate"] if page is not None else None
    state = page["status"] if page is not None else "deferred" if deferred else "not_selected"
    result = {
        "page_number": number, "candidate_state": state,
        "status": "unavailable", "reason": state,
        "candidate_sha256": _candidate_digest(candidate) if candidate is not None else None,
        "line_count": len(candidate["lines"]) if candidate is not None else None,
        "candidate_coordinate_system": candidate["raster"]["coordinate_system"] if candidate is not None else None,
        "plan": None, "line_order": None, "band_counts": [], "column_counts": [],
    }
    if candidate is None:
        return result
    if not candidate["text"].strip():
        result.update(status="empty_candidate", reason="empty_candidate")
        return result
    result["status"] = "abstained"
    if candidate["raster"]["coordinate_system"] != "rendered_image_pixels":
        result["reason"] = "unsupported_preprocessed_candidate"
    elif len(candidate["lines"]) > MAX_LINES:
        result["reason"] = "line_limit"
    elif not work_allowed:
        result["reason"] = "geometry_work_limit"
    else:
        result.update(_infer([line["box"] for line in candidate["lines"]],
                             candidate["raster"]["width"], candidate["raster"]["height"]))
        if result["plan"] is not None:
            result["plan"] = {"page_number": number, **result["plan"]}
    return result


def build_column_suggestions(recovery: object, *, recovery_sha256: str,
                             requested_pages: list[int]) -> dict:
    """Return unconfirmed observed-line permutations for every requested page.

    In-memory digests are caller declarations; the fixed-file adapter verifies
    actual file bytes. No approval, layout semantics or missing-text inference
    follows from a supported geometry hypothesis.
    """
    _hex_digest(recovery_sha256, label="column recovery digest")
    recovery = validate_recovery_report(recovery)
    requested = _requested(requested_pages, recovery["page_count"])
    selected = {page["page_number"]: page for page in recovery["pages"]}
    deferred = {page["page_number"] for page in recovery["deferred_pages"]}
    lengths = [len(selected[number]["candidate"]["lines"]) for number in requested
               if number in selected and _eligible(selected[number]["candidate"])]
    work = sum(12 * count + 3 * count * count.bit_length() for count in lengths)
    pages = [_page(number, selected.get(number), deferred=number in deferred,
                   work_allowed=work <= MAX_GEOMETRY_WORK) for number in requested]
    # Every emitted page plan passes the unchanged explicit-layout contract.
    plans = [page["plan"] for page in pages if page["plan"] is not None]
    if plans:
        validated = ocr_layout.validate_layout_plan({
            "schema_version": 1, "source_sha256": recovery["source_sha256"], "recovery_sha256": recovery_sha256,
            "coordinate_system": "candidate_raster_fraction", "pages": plans},
            source_sha256=recovery["source_sha256"], recovery_sha256=recovery_sha256, page_count=recovery["page_count"])
        for page, plan in zip((page for page in pages if page["plan"] is not None), validated["pages"]):
            page["plan"] = plan
    coverage = {
        "requested_pages": requested,
        "unrequested_source_pages": sorted(set(range(1, recovery["page_count"] + 1)) - set(requested)),
        **{key: [page["page_number"] for page in pages if page["status"] == status] for key, status in (
            ("suggested_pages", "unconfirmed_hypothesis"), ("abstained_pages", "abstained"),
            ("unavailable_pages", "unavailable"), ("empty_candidate_pages", "empty_candidate"))},
    }
    result = {
        "schema_version": 1, "kind": "ocr_column_suggestions", "algorithm": ALGORITHM,
        "source_sha256": recovery["source_sha256"], "recovery_sha256": recovery_sha256,
        "page_count": recovery["page_count"], "coordinate_system": "candidate_raster_fraction",
        "parameters": _parameters(), "candidate_digest_policy": dict(_DIGEST_POLICY),
        "pages": pages, "coverage": coverage,
        "summary": {"source_pages": recovery["page_count"],
                    **{key: len(value) for key, value in coverage.items() if key != "unrequested_source_pages"}},
        "scope": "observed_candidate_geometry_only", "semantic_role": "unknown",
        "acceptance": "manual_review_required", **_ATTESTATIONS,
    }
    if len(_canonical(result)) > MAX_OUTPUT_BYTES:
        raise ValueError("column suggestion output exceeds byte bound")
    return result


def validate_column_suggestions(payload: object, *, recovery: object, recovery_sha256: str) -> dict:
    """Strictly rebuild all fields before serializing a bounded expected shape."""
    if type(payload) is not dict or type(payload.get("coverage")) is not dict:
        raise ValueError("invalid column suggestion object")
    expected = build_column_suggestions(recovery, recovery_sha256=recovery_sha256,
                                        requested_pages=payload["coverage"].get("requested_pages"))
    pending = [(payload, expected)]
    while pending:
        actual, wanted = pending.pop()
        if type(actual) is not type(wanted):
            raise ValueError("column suggestion field type differs")
        if type(wanted) is dict:
            if set(actual) != set(wanted):
                raise ValueError("column suggestion fields differ")
            pending.extend((actual[key], wanted[key]) for key in wanted)
        elif type(wanted) is list:
            if len(actual) != len(wanted):
                raise ValueError("column suggestion field length differs")
            pending.extend(zip(actual, wanted))
        elif actual != wanted:
            raise ValueError("column suggestion differs from its fixed input")
    if _canonical(payload) != _canonical(expected):
        raise ValueError("column suggestion canonical content differs")
    return expected
