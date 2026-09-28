"""Explicit, bounded human resolution of saved Docling-to-OCR assignments.

Geometry is evidence, never changed here. Operator choices are declarations,
not authenticated approval or measured recognition accuracy.
"""

from __future__ import annotations

import copy

from evaluation_inputs import _hex_digest
from ocr_docling import MAX_LINES, _exact, validate_docling_proposals
from ocr_layout import _rectangle
from ocr_recovery_comparison import validate_recovery_report


MAX_PAGES = 20
MAX_HISTORY = 512
MAX_SEQUENCE = 1_000_000_000
MAX_DIAGNOSTIC_MATCHES = 20_000
_ACTIONS = {"line_assigned", "line_retained", "line_cleared", "region_order_changed", "region_order_reset"}
_ATTESTATIONS = {"requires_attention": True, "accuracy_verified": False,
                 "canonical_extraction_modified": False, "recognition_rerun": False}


def _fields(value: object, fields: set[str]) -> dict:
    if type(value) is not dict or set(value) != fields:
        raise ValueError("invalid layout assignment fields")
    return value


def _integer(value: object, low: int, high: int) -> int:
    if type(value) is not int or not low <= value <= high:
        raise ValueError("invalid layout assignment integer")
    return value


def _context(*, recovery: object, recovery_sha256: str, proposals: object,
             proposals_sha256: str) -> tuple[dict, dict, dict]:
    recovery = validate_recovery_report(recovery)
    proposals = validate_docling_proposals(proposals, recovery=recovery, recovery_sha256=recovery_sha256)
    _hex_digest(proposals_sha256, label="layout proposals digest")
    binding = {"source_sha256": recovery["source_sha256"], "recovery_sha256": recovery_sha256,
               "proposals_sha256": proposals_sha256, "docling_sha256": proposals["docling_sha256"],
               "manifest_sha256": proposals["manifest_sha256"], "page_count": recovery["page_count"]}
    return recovery, proposals, binding


def _page_context(number: object, recovery: dict, proposals: dict) -> tuple[dict, dict]:
    number = _integer(number, 1, recovery["page_count"])
    candidate = next((p["candidate"] for p in recovery["pages"] if p["page_number"] == number), None)
    page = next((p for p in proposals["pages"] if p["page_number"] == number), None)
    if (candidate is None or page is None or not candidate["text"].strip()
            or not 1 <= len(candidate["lines"]) <= MAX_LINES
            or proposals["effective_input_kind"] != "original" or "preprocessing" in candidate
            or page["page_geometry"] is None or set(page["reasons"]) & {
                "effective_input_preprocessed", "candidate_preprocessed", "missing_page_geometry", "page_geometry_mismatch"}):
        raise ValueError("layout assignment page has unsupported candidate geometry")
    return candidate, page


def _geometry(line: dict, raster: dict, regions: list[dict]) -> tuple[bool, list[str], bool]:
    # A whole polygon is contained iff its extrema are. No added tolerance or
    # change to the original OCR box; nonrectangular lines are not auto-seeded.
    points = line["box"]
    bounds = [min(p[0] for p in points) / raster["width"], min(p[1] for p in points) / raster["height"],
              max(p[0] for p in points) / raster["width"], max(p[1] for p in points) / raster["height"]]
    matches = [r["ref"] for r in regions if r["bbox"][0] <= bounds[0] and r["bbox"][1] <= bounds[1]
               and bounds[2] <= r["bbox"][2] and bounds[3] <= r["bbox"][3]]
    furniture = any(r["tree"] == "furniture" and r["ref"] in matches for r in regions)
    return _rectangle(points) is not None, matches, furniture


def suggest_assignment_page(page_number: int, *, recovery: object, recovery_sha256: str,
                            proposals: object, proposals_sha256: str) -> dict:
    """Seed only unique whole-line containment; all ambiguous lines stay open."""
    recovery, proposals, _ = _context(recovery=recovery, recovery_sha256=recovery_sha256,
                                      proposals=proposals, proposals_sha256=proposals_sha256)
    candidate, page = _page_context(page_number, recovery, proposals)
    assignments, match_count = [], 0
    for index, line in enumerate(candidate["lines"]):
        rectangular, matches, _ = _geometry(line, candidate["raster"], page["regions"])
        match_count += len(matches)
        if match_count > MAX_DIAGNOSTIC_MATCHES:
            raise ValueError("layout assignment overlap diagnostic limit exceeded")
        assignments.append({"line_index": index, "region_ref": matches[0] if rectangular and len(matches) == 1 else None,
                            "retain_engine_slot": False})
    return {"page_number": page_number, "assignments": assignments, "body_region_order": None}


def _page(value: object, recovery: dict, proposals: dict) -> dict:
    value = _fields(value, {"page_number", "assignments", "body_region_order"})
    candidate, page = _page_context(value["page_number"], recovery, proposals)
    regions = {r["ref"]: r for r in page["regions"]}
    assignments = value["assignments"]
    if type(assignments) is not list or len(assignments) != len(candidate["lines"]):
        raise ValueError("layout assignment must preserve every original line slot")
    result, match_count = [], 0
    for index, assignment in enumerate(assignments):
        assignment = _fields(assignment, {"line_index", "region_ref", "retain_engine_slot"})
        if type(assignment["line_index"]) is not int or assignment["line_index"] != index:
            raise ValueError("layout assignment line indices must be exact original order")
        ref, retained = assignment["region_ref"], assignment["retain_engine_slot"]
        if type(retained) is not bool or (ref is not None and (type(ref) is not str or ref not in regions)):
            raise ValueError("invalid layout assignment region or retention")
        if retained and ref is not None:
            raise ValueError("retained engine slot cannot also claim a region assignment")
        _, matches, locked = _geometry(candidate["lines"][index], candidate["raster"], page["regions"])
        match_count += len(matches)
        if match_count > MAX_DIAGNOSTIC_MATCHES:
            raise ValueError("layout assignment overlap diagnostic limit exceeded")
        if locked and ref is not None and regions[ref]["tree"] != "furniture":
            raise ValueError("saved furniture line must retain its original engine slot")
        result.append(dict(assignment))
    order = value["body_region_order"]
    body = [r["ref"] for r in page["regions"] if r["tree"] == "body"]
    if order is not None and (type(order) is not list or len(order) != len(body)
                             or any(type(ref) is not str for ref in order)
                             or len(set(order)) != len(order) or set(order) != set(body)):
        raise ValueError("human region order must contain every saved body region exactly once")
    return {"page_number": value["page_number"], "assignments": result,
            "body_region_order": list(order) if order is not None else None}


def _history(value: object, page_count: int) -> list[dict]:
    if type(value) is not list or len(value) > MAX_HISTORY:
        raise ValueError("layout assignment history limit exceeded")
    result, previous = [], 0
    for entry in value:
        entry = _fields(entry, {"sequence", "page_number", "action"})
        sequence = _integer(entry["sequence"], previous + 1, MAX_SEQUENCE)
        _integer(entry["page_number"], 1, page_count)
        if type(entry["action"]) is not str or entry["action"] not in _ACTIONS:
            raise ValueError("invalid layout assignment history action")
        result.append(dict(entry))
        previous = sequence
    return result


def build_assignment_plan(pages: object, *, recovery: object, recovery_sha256: str,
                          proposals: object, proposals_sha256: str, history: object = None) -> dict:
    """Build an unconfirmed partial or complete plan without mutating its inputs."""
    recovery, proposals, binding = _context(recovery=recovery, recovery_sha256=recovery_sha256,
                                            proposals=proposals, proposals_sha256=proposals_sha256)
    if type(pages) is not list or not 1 <= len(pages) <= MAX_PAGES:
        raise ValueError("layout assignment requires 1..20 explicit pages")
    validated = [_page(page, recovery, proposals) for page in pages]
    numbers = [page["page_number"] for page in validated]
    if len(set(numbers)) != len(numbers):
        raise ValueError("duplicate layout assignment page")
    return {"schema_version": 1, "kind": "ocr_layout_assignment_plan", **binding,
            "coordinate_system": "candidate_raster_fraction", "pages": sorted(validated, key=lambda p: p["page_number"]),
            "history": _history([] if history is None else history, recovery["page_count"]),
            "history_authenticity": "operator_declared_not_authenticated", "acceptance": "manual_review_required",
            **_ATTESTATIONS}


def validate_assignment_plan(payload: object, *, recovery: object, recovery_sha256: str,
                             proposals: object, proposals_sha256: str) -> dict:
    if type(payload) is not dict:
        raise ValueError("invalid layout assignment plan")
    expected = build_assignment_plan(payload.get("pages"), recovery=recovery, recovery_sha256=recovery_sha256,
                                     proposals=proposals, proposals_sha256=proposals_sha256, history=payload.get("history"))
    if not _exact(payload, expected):
        raise ValueError("layout assignment plan differs from bound inputs or strict schema")
    return expected


def _preview_page(value: dict, candidate: dict, page: dict) -> dict:
    region_map = {region["ref"]: region for region in page["regions"]}
    body_order = value["body_region_order"]
    if body_order is None:
        body_order = [r["ref"] for r in page["regions"] if r["tree"] == "body"]
    groups = {ref: [] for ref in body_order}
    unresolved, retained, locked_lines, diagnostics = [], [], [], []
    for assignment, line in zip(value["assignments"], candidate["lines"]):
        index, ref = assignment["line_index"], assignment["region_ref"]
        rectangular, matches, locked = _geometry(line, candidate["raster"], page["regions"])
        if locked:
            locked_lines.append(index)
        if assignment["retain_engine_slot"]:
            retained.append(index)
            relation = "retained_engine_slot"
        elif ref is None:
            unresolved.append(index)
            relation = "unresolved"
        else:
            if region_map[ref]["tree"] == "furniture":
                retained.append(index)
            else:
                groups[ref].append(index)
            relation = ("ambiguous_line_geometry" if not rectangular else "outside_region" if ref not in matches
                        else "unique_containment" if len(matches) == 1 else "ambiguous_containment")
        diagnostics.append({"line_index": index, "region_ref": ref, "relation": relation,
                            "matching_region_refs": matches, "furniture_locked": locked})
    line_order = None
    if not unresolved:
        moving = [index for ref in body_order for index in groups[ref]]
        movable_slots, replacement = set(moving), iter(moving)
        line_order = [next(replacement) if index in movable_slots else index for index in range(len(candidate["lines"]))]
        # A defense against future policy changes; retained lines occupy slots,
        # not just equal-text positions, so duplicate OCR text stays distinct.
        if sorted(line_order) != list(range(len(candidate["lines"]))) or any(line_order[i] != i for i in retained):
            raise ValueError("layout assignment failed exact line preservation")
    return {"page_number": value["page_number"], "status": "partial" if unresolved else "complete",
            "line_order": line_order, "original_text": candidate["text"],
            "proposed_text": None if line_order is None else "\n".join(candidate["lines"][i]["text"] for i in line_order),
            "unresolved_lines": unresolved, "retained_engine_slots": retained, "furniture_locked_lines": locked_lines,
            "line_diagnostics": diagnostics, "body_region_order": list(body_order),
            "order_source": "saved_docling" if value["body_region_order"] is None else "operator_declared",
            "proposal_reasons": list(page["reasons"])}


def preview_assignment_plan(plan: object, *, recovery: object, recovery_sha256: str,
                            proposals: object, proposals_sha256: str) -> dict:
    """Derive a deterministic preview; incomplete pages never get fake text."""
    recovery, proposals, binding = _context(recovery=recovery, recovery_sha256=recovery_sha256,
                                            proposals=proposals, proposals_sha256=proposals_sha256)
    plan = validate_assignment_plan(plan, recovery=recovery, recovery_sha256=recovery_sha256,
                                    proposals=proposals, proposals_sha256=proposals_sha256)
    pages = [_preview_page(page, *_page_context(page["page_number"], recovery, proposals)) for page in plan["pages"]]
    return {"schema_version": 1, "kind": "ocr_layout_assignment_preview", **binding, "pages": pages,
            "summary": {"pages": len(pages), "complete": sum(p["status"] == "complete" for p in pages),
                        "partial": sum(p["status"] == "partial" for p in pages),
                        "unresolved_lines": sum(len(p["unresolved_lines"]) for p in pages),
                        "retained_engine_slots": sum(len(p["retained_engine_slots"]) for p in pages)},
            **_ATTESTATIONS}


def build_assignment_review(plan: object, *, recovery: object, recovery_sha256: str,
                            proposals: object, proposals_sha256: str, confirmed_pages: object,
                            plan_sha256: str | None = None) -> dict:
    """Export only explicitly confirmed complete pages, never canonical text."""
    plan = validate_assignment_plan(plan, recovery=recovery, recovery_sha256=recovery_sha256,
                                    proposals=proposals, proposals_sha256=proposals_sha256)
    preview = preview_assignment_plan(plan, recovery=recovery, recovery_sha256=recovery_sha256,
                                      proposals=proposals, proposals_sha256=proposals_sha256)
    if plan_sha256 is not None:
        _hex_digest(plan_sha256, label="layout assignment plan digest")
    if type(confirmed_pages) is not list or not 1 <= len(confirmed_pages) <= MAX_PAGES:
        raise ValueError("confirm 1..20 complete layout assignment pages")
    numbers = [_integer(n, 1, plan["page_count"]) for n in confirmed_pages]
    available = {page["page_number"]: page for page in preview["pages"]}
    if (len(set(numbers)) != len(numbers) or not set(numbers) <= available.keys()
            or any(available[n]["status"] != "complete" for n in numbers)):
        raise ValueError("only complete assignment pages can be explicitly confirmed")
    return {"schema_version": 1, "kind": "ocr_layout_assignment_review",
            **{key: plan[key] for key in ("source_sha256", "recovery_sha256", "proposals_sha256", "docling_sha256", "manifest_sha256", "page_count")},
            "plan_sha256": plan_sha256, "plan": copy.deepcopy(plan), "confirmed_pages": sorted(numbers),
            "pages": [available[n] for n in sorted(numbers)],
            "operator_review": "declared_by_local_operator_not_authenticated",
            "acceptance": "reviewed_assignment_not_canonical", **_ATTESTATIONS}


def validate_assignment_review(payload: object, *, recovery: object, recovery_sha256: str,
                               proposals: object, proposals_sha256: str) -> dict:
    if type(payload) is not dict:
        raise ValueError("invalid layout assignment review")
    expected = build_assignment_review(payload.get("plan"), recovery=recovery, recovery_sha256=recovery_sha256,
        proposals=proposals, proposals_sha256=proposals_sha256, confirmed_pages=payload.get("confirmed_pages"),
        plan_sha256=payload.get("plan_sha256"))
    if not _exact(payload, expected):
        raise ValueError("layout assignment review differs from bound original lines or declaration")
    return expected
