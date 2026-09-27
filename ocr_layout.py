"""Explicit two-column ordering proposals over existing OCR line records.

No OCR, PDF parsing, inferred table classification or recovery-schema changes.
Plans use fractions of each current candidate raster and bind the exact input
report. Geometry can reject a plan; it cannot establish that a page is prose.
"""

from __future__ import annotations

import copy
import math

from evaluation_inputs import _hex_digest
from ocr_comparison import compare_ocr
from ocr_recovery_comparison import validate_recovery_report, validate_references


ALGORITHM = "explicit-two-column-order-v1"
MAX_LAYOUT_LINES = 2000
_PARAMETERS = {
    "max_lines_per_page": MAX_LAYOUT_LINES, "minimum_lines_per_column": 2,
    "max_horizontal_edge_slope": 0.10, "max_vertical_edge_slope": 0.25,
}


def _fraction_pair(value: object, *, interior: bool) -> list[float]:
    if not isinstance(value, list) or len(value) != 2:
        raise ValueError("layout bounds must be two finite fractions")
    if any(type(number) not in {int, float} or not 0 <= number <= 1
           or not math.isfinite(number) for number in value):
        raise ValueError("layout bounds must be finite fractions in 0..1")
    low, high = value
    if not low < high or interior and not 0 < low < high < 1:
        raise ValueError("layout bounds must be ordered with an interior gutter")
    return [float(low), float(high)]


def validate_layout_plan(payload: object, *, source_sha256: str,
                         recovery_sha256: str, page_count: int) -> dict:
    _hex_digest(source_sha256, label="layout source digest")
    _hex_digest(recovery_sha256, label="layout recovery digest")
    if type(page_count) is not int or not 1 <= page_count <= 5000:
        raise ValueError("layout page count must be an integer in 1..5000")
    if not isinstance(payload, dict) or set(payload) != {
        "schema_version", "source_sha256", "recovery_sha256", "coordinate_system", "pages",
    }:
        raise ValueError("invalid OCR layout plan fields")
    if type(payload["schema_version"]) is not int or payload["schema_version"] != 1:
        raise ValueError("unsupported OCR layout plan version")
    for key, expected in (("source_sha256", source_sha256), ("recovery_sha256", recovery_sha256)):
        _hex_digest(payload[key], label="layout input digest")
        if payload[key] != expected:
            raise ValueError("layout plan belongs to a different source or recovery report")
    if payload["coordinate_system"] != "candidate_raster_fraction":
        raise ValueError("layout plans require current candidate-raster fractions")
    if not isinstance(payload["pages"], list) or not 1 <= len(payload["pages"]) <= 20:
        raise ValueError("layout plan requires 1..20 explicit pages")
    pages, seen = [], set()
    for page in payload["pages"]:
        if not isinstance(page, dict) or set(page) != {"page_number", "body_band", "gutter", "order"}:
            raise ValueError("invalid layout page fields")
        number = page["page_number"]
        if type(number) is not int or not 1 <= number <= page_count or number in seen:
            raise ValueError("layout page numbers must be unique and within the PDF")
        if page["order"] != "left_then_right":
            raise ValueError("layout supports only explicitly declared left-then-right columns")
        seen.add(number)
        pages.append({
            "page_number": number, "body_band": _fraction_pair(page["body_band"], interior=False),
            "gutter": _fraction_pair(page["gutter"], interior=True), "order": "left_then_right",
        })
    return {**{key: payload[key] for key in payload if key != "pages"},
            "pages": sorted(pages, key=lambda page: page["page_number"])}


def _rectangle(box: list) -> tuple[float, float, float, float] | None:
    edges = [(box[(i + 1) % 4][0] - box[i][0],
              box[(i + 1) % 4][1] - box[i][1]) for i in range(4)]
    turns = [edges[i][0] * edges[(i + 1) % 4][1]
             - edges[i][1] * edges[(i + 1) % 4][0] for i in range(4)]
    if not (all(turn > 0 for turn in turns) or all(turn < 0 for turn in turns)):
        return None
    horizontal = sum(dx != 0 and abs(dy) <= abs(dx) * _PARAMETERS["max_horizontal_edge_slope"]
                     for dx, dy in edges)
    vertical = sum(dy != 0 and abs(dx) <= abs(dy) * _PARAMETERS["max_vertical_edge_slope"]
                   for dx, dy in edges)
    if horizontal != 2 or vertical != 2:
        return None
    return (min(point[0] for point in box), min(point[1] for point in box),
            max(point[0] for point in box), max(point[1] for point in box))


def _proposal(candidate: dict, plan: dict) -> tuple[list[int], str, str]:
    lines = candidate["lines"]
    identity = list(range(len(lines)))

    def abstain(reason: str) -> tuple[list[int], str, str]:
        return identity, "abstained", reason

    if not candidate["text"].strip():
        return abstain("empty_candidate")
    if len(lines) > MAX_LAYOUT_LINES:
        return abstain("line_limit")
    width, height = candidate["raster"]["width"], candidate["raster"]["height"]
    top, bottom = (bound * height for bound in plan["body_band"])
    gutter_left, gutter_right = (bound * width for bound in plan["gutter"])
    headers, left, right, footers, groups, rectangles = [], [], [], [], [], []
    for index, line in enumerate(lines):
        rectangle = _rectangle(line["box"])
        if rectangle is None:
            return abstain("ambiguous_geometry")
        rectangles.append(rectangle)
        x0, y0, x1, y1 = rectangle
        if y1 <= top:
            headers.append(index)
            groups.append(0)
        elif y0 >= bottom:
            footers.append(index)
            groups.append(2)
        elif y0 < top or y1 > bottom:
            return abstain("body_boundary_crossed")
        else:
            groups.append(1)
            if x1 <= gutter_left:
                left.append(index)
            elif x0 >= gutter_right:
                right.append(index)
            else:
                return abstain("gutter_crossed")
    if groups != sorted(groups):
        return abstain("noncontiguous_body")
    if min(len(left), len(right)) < _PARAMETERS["minimum_lines_per_column"]:
        return abstain("insufficient_columns")
    for column in (left, right):
        # Preserve engine order inside a column. Never invent a within-column
        # repair: overlaps or reversed lines need explicit source review.
        if any(rectangles[current][1] < rectangles[previous][3]
               for previous, current in zip(column, column[1:])):
            return abstain("overlapping_or_reversed_lines")
    order = headers + left + right + footers
    if sorted(order) != identity:
        raise RuntimeError("layout proposal is not a complete line permutation")
    return (order, "unchanged", "already_ordered") if order == identity else (
        order, "reordered", "explicit_columns")


def _review_page(number: int, page: dict | None, plan: dict | None, *, deferred: bool) -> dict:
    original_status = page["status"] if page is not None else "deferred" if deferred else "not_selected"
    result = {
        "page_number": number, "planned": plan is not None, "original_status": original_status,
        "status": "unavailable", "reason": original_status,
        "original_text": None, "proposed_text": None, "line_order": None,
        "lines": None, "raster": None,
    }
    if page is None or page["candidate"] is None:
        return result
    candidate = page["candidate"]
    order, status, reason = (
        _proposal(candidate, plan) if plan is not None else (
            list(range(len(candidate["lines"]))), "unchanged", "not_requested"))
    result.update({
        "status": status, "reason": reason, "original_text": candidate["text"],
        "proposed_text": "\n".join(candidate["lines"][index]["text"] for index in order),
        "line_order": order, "lines": copy.deepcopy(candidate["lines"]),
        "raster": dict(candidate["raster"]),
    })
    return result


def _compare_pages(pages: list[dict], references: dict, selected: set[int], planned: set[int],
                   deferred: set[int]) -> tuple[dict | None, dict]:
    mapped = {page["page_number"]: page for page in pages}
    records = ([], [])
    unpaired = []
    numbers = {page["page_number"] for page in references["pages"]}
    for reference in sorted(references["pages"], key=lambda page: page["page_number"]):
        number = reference["page_number"]
        page = mapped.get(number)
        if page is None or page["proposed_text"] is None:
            unpaired.append({"page_number": number, "status": (
                page["original_status"] if page else "deferred" if number in deferred else "not_selected")})
            continue
        for target, field in zip(records, ("original_text", "proposed_text")):
            target.append({
                "id": f"page-{number:05d}", "reference": reference["reference"],
                "prediction": page[field], "critical_tokens": reference.get("critical_tokens", []),
            })
    comparison = compare_ocr(*({"schema_version": 1, "records": side} for side in records)) if records[0] else None
    return comparison, {
        "reference_pages": len(numbers), "paired_pages": len(records[0]), "unpaired_pages": unpaired,
        "unreferenced_selected_pages": sorted(selected - numbers),
        "unreferenced_planned_pages": sorted(planned - numbers),
    }


def build_layout_review(recovery: object, plan: object, *, recovery_sha256: str,
                        references: object | None = None) -> dict:
    """Propose only explicit, conservative permutations and optionally score them.

    Failed/unselected/deferred candidates are absent, not fabricated blanks.
    True empty candidates remain available to the scorer and require attention.
    The file adapter supplies the exact recovery snapshot digest; declarations
    passed directly by in-memory callers are not an authenticity attestation.
    """
    _hex_digest(recovery_sha256, label="layout recovery digest")
    recovery = copy.deepcopy(validate_recovery_report(recovery))
    plan = validate_layout_plan(
        plan, source_sha256=recovery["source_sha256"], recovery_sha256=recovery_sha256,
        page_count=recovery["page_count"])
    selected = {page["page_number"]: page for page in recovery["pages"]}
    planned = {page["page_number"]: page for page in plan["pages"]}
    deferred = {page["page_number"] for page in recovery["deferred_pages"]}
    pages = [_review_page(number, selected.get(number), planned.get(number), deferred=number in deferred)
             for number in sorted(set(selected) | set(planned))]
    summary = {
        "review_pages": len(pages), "planned_pages": len(planned),
        **{status: sum(page["status"] == status for page in pages)
           for status in ("reordered", "unchanged", "abstained", "unavailable")},
    }
    coverage = {
        "selected_pages": sorted(selected), "requested_pages": sorted(planned),
        "planned_unavailable_pages": [{"page_number": page["page_number"], "status": page["original_status"]}
                                      for page in pages if page["planned"] and page["status"] == "unavailable"],
        "deferred_pages": sorted(deferred),
        "failed_pages": sorted(n for n, page in selected.items() if page["status"] == "retry_failed"),
        "empty_candidate_pages": sorted(n for n, page in selected.items() if page["status"] == "empty_candidate"),
    }
    comparison, reference_coverage = None, None
    if references is not None:
        references = validate_references(references, page_count=recovery["page_count"])
        if references["source_sha256"] != recovery["source_sha256"]:
            raise ValueError("layout references belong to a different source")
        comparison, reference_coverage = _compare_pages(pages, references, set(selected), set(planned), deferred)
    attention = bool(
        references is None or comparison is None or summary["abstained"] or summary["unavailable"]
        or deferred or coverage["empty_candidate_pages"]
        or reference_coverage and any(reference_coverage[key] for key in (
            "unpaired_pages", "unreferenced_selected_pages", "unreferenced_planned_pages"))
        or comparison and comparison["regression_detected"])
    return {
        "schema_version": 1, "kind": "ocr_layout_review", "algorithm": ALGORITHM,
        "source_sha256": recovery["source_sha256"], "recovery_sha256": recovery_sha256,
        "page_count": recovery["page_count"], "plan": plan, "parameters": dict(_PARAMETERS),
        "pages": pages, "summary": summary, "coverage": coverage,
        "comparison": comparison, "reference_coverage": reference_coverage,
        "requires_attention": attention, "acceptance": "manual_review_required",
        "canonical_extraction_modified": False, "recognition_rerun": False,
        "scope": "operator-declared two-column permutation only; not table classification or source authenticity",
    }
