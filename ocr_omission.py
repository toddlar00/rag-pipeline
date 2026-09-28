"""Conservative saved-region OCR omission warnings, never source certification."""

from __future__ import annotations

import copy
import hashlib
import json

from evaluation_inputs import _hex_digest
from ocr_docling import _REF, _exact, validate_docling_proposals
from ocr_layout import _rectangle
from ocr_recovery_comparison import validate_recovery_report


MAX_PAGES = 20
MAX_LINES = 2000
MAX_REGIONS = 500
MAX_PAIR_COMPARISONS = 20_000_000
MAX_DECISIONS = 10_000
OVERLAP_ROUNDOFF_FRACTION = 1e-12
TARGET_KINDS = ("text", "heading", "table")
WARNING_STATUSES = ("no_candidate_line_overlap", "empty_text_only", "boundary_or_ambiguous_overlap")
DECISIONS = ("suspected_missing_text", "not_text", "false_alarm")
_COUNTS = ("nonempty_contained_lines", "nonempty_boundary_overlap_lines",
           "ambiguous_geometry_overlap_lines", "empty_text_overlap_lines")
_INCOMPLETE = {"missing_provenance", "invalid_region_geometry", "multi_page_item", "unattached_items",
               "region_limit", "table_structure_unavailable"}
_ATTESTATIONS = {"accuracy_verified": False, "full_page_coverage_verified": False,
                 "recognition_rerun": False, "canonical_extraction_modified": False}
_DIGEST_POLICY = {"algorithm": "sha256", "domain": "rag-pipeline:ocr-omission-diagnostics:v1",
                  "serialization": "utf8-sorted-compact-json", "is_file_digest": False}


def _fields(value: object, names: set[str]) -> dict:
    if type(value) is not dict or set(value) != names:
        raise ValueError("invalid OCR omission fields")
    return value


def _integer(value: object, low: int, high: int) -> int:
    if type(value) is not int or not low <= value <= high:
        raise ValueError("invalid bounded OCR omission integer")
    return value


def _parameters() -> dict:
    return {"max_candidate_pages": MAX_PAGES, "max_lines_per_page": MAX_LINES,
            "max_regions_per_page": MAX_REGIONS, "max_pair_comparisons": MAX_PAIR_COMPARISONS,
            "geometry_policy": "aabb_overlap_roundoff_guard_strict_rectangle_containment_v1",
            "overlap_roundoff_fraction": OVERLAP_ROUNDOFF_FRACTION,
            "target_kinds": list(TARGET_KINDS), "furniture_included": True}


def _line_geometry(candidate: dict) -> list[tuple]:
    width, height = candidate["raster"]["width"], candidate["raster"]["height"]
    result = []
    for line in candidate["lines"]:
        points = line["box"]
        bounds = (min(p[0] for p in points) / width, min(p[1] for p in points) / height,
                  max(p[0] for p in points) / width, max(p[1] for p in points) / height)
        result.append((bounds, _rectangle(points) is not None, bool(line["text"].strip())))
    return result


def _region(region: dict, lines: list[tuple] | None) -> dict:
    result = {key: copy.deepcopy(region[key]) for key in ("ref", "kind", "tree", "bbox", "order")}
    if region["kind"] not in TARGET_KINDS or lines is None:
        return {**result, "status": "not_target_region" if region["kind"] not in TARGET_KINDS else "unevaluated",
                **dict.fromkeys(_COUNTS)}
    counts = dict.fromkeys(_COUNTS, 0)
    left, top, right, bottom = region["bbox"]
    for (x0, y0, x1, y1), rectangular, has_text in lines:
        # Closed boxes deliberately retain boundary touches as uncertain hits.
        # Only absence gets a small declared roundoff guard; containment is
        # never expanded. Thus numeric conversion noise cannot prove absence.
        # AABB overlap is not proof of polygon overlap or recognized source text.
        if (left - x1 > OVERLAP_ROUNDOFF_FRACTION or x0 - right > OVERLAP_ROUNDOFF_FRACTION
                or top - y1 > OVERLAP_ROUNDOFF_FRACTION or y0 - bottom > OVERLAP_ROUNDOFF_FRACTION):
            continue
        if not has_text:
            key = "empty_text_overlap_lines"
        elif not rectangular:
            key = "ambiguous_geometry_overlap_lines"
        elif left <= x0 and top <= y0 and x1 <= right and y1 <= bottom:
            key = "nonempty_contained_lines"
        else:
            key = "nonempty_boundary_overlap_lines"
        counts[key] += 1
    status = ("has_nonempty_line_geometry" if counts["nonempty_contained_lines"] else
              "boundary_or_ambiguous_overlap" if counts["nonempty_boundary_overlap_lines"]
              or counts["ambiguous_geometry_overlap_lines"] else
              "empty_text_only" if counts["empty_text_overlap_lines"] else "no_candidate_line_overlap")
    return {**result, "status": status, **counts}


def _page(number: int, candidate: dict | None, proposal: dict | None, *, candidate_state: str) -> dict:
    reasons = list(proposal["reasons"]) if proposal else []
    regions = proposal["regions"] if proposal else []
    if candidate is None:
        state = "candidate_unavailable"
    elif set(reasons) & {"effective_input_preprocessed", "candidate_preprocessed"}:
        state = "unsupported_preprocessed"
    elif proposal is None or proposal["page_geometry"] is None or set(reasons) & {
            "missing_page_geometry", "page_geometry_mismatch"}:
        state = "missing_or_mismatched_geometry"
    elif not regions:
        state = "no_saved_regions"
    elif len(candidate["lines"]) > MAX_LINES:
        state = "line_limit"
    elif not any(region["kind"] in TARGET_KINDS for region in regions):
        state = "no_target_regions"
    else:
        state = "supported"
    return {"page_number": number, "candidate_state": candidate_state, "layout_state": state,
            "proposal_reasons": reasons, "layout_evidence_incomplete": bool(set(reasons) & _INCOMPLETE),
            "regions": regions}


def build_omission_diagnostics(recovery: object, proposals: object, *, recovery_sha256: str,
                               proposals_sha256: str) -> dict:
    """Inspect every source page state and known region without reading source pixels.

    Potential omissions and ambiguous hits are warnings. A recognized line
    somewhere in a region cannot establish completeness of that region.
    """
    recovery = validate_recovery_report(recovery)
    proposals = validate_docling_proposals(proposals, recovery=recovery, recovery_sha256=recovery_sha256)
    _hex_digest(proposals_sha256, label="omission proposals digest")
    selected = {page["page_number"]: page for page in recovery["pages"]}
    saved = {page["page_number"]: page for page in proposals["pages"]}
    deferred = {page["page_number"] for page in recovery["deferred_pages"]}
    if len(selected) > MAX_PAGES or any(len(page["regions"]) > MAX_REGIONS for page in saved.values()):
        raise ValueError("OCR omission page or region budget exceeded")
    pages = []
    for number in range(1, recovery["page_count"] + 1):
        candidate = selected[number]["candidate"] if number in selected else None
        state = ("available" if candidate["text"].strip() else "empty") if candidate is not None else (
            "retry_failed" if number in selected else "deferred" if number in deferred else "not_selected")
        pages.append(_page(number, candidate, saved.get(number), candidate_state=state))
    work = sum(len(selected[page["page_number"]]["candidate"]["lines"]) * len(page["regions"])
               for page in pages if page["layout_state"] == "supported")
    # Preflight the whole supported cohort; never truncate at a budget boundary.
    for page in pages:
        if page["layout_state"] == "supported" and work > MAX_PAIR_COMPARISONS:
            page["layout_state"] = "work_limit"
        lines = (_line_geometry(selected[page["page_number"]]["candidate"])
                 if page["layout_state"] == "supported" else None)
        page["regions"] = [_region(region, lines) for region in page["regions"]]
    regions = [region for page in pages for region in page["regions"]]
    warning = [region for region in regions if region["status"] in WARNING_STATUSES]
    summary = {"source_pages": len(pages), "evaluated_pages": sum(p["layout_state"] == "supported" for p in pages),
        "unevaluated_pages": sum(p["layout_state"] != "supported" for p in pages),
        **{key: sum(p["candidate_state"] == state for p in pages) for key, state in (
            ("available_candidate_pages", "available"), ("empty_candidate_pages", "empty"),
            ("failed_candidate_pages", "retry_failed"), ("deferred_candidate_pages", "deferred"),
            ("not_selected_pages", "not_selected"))},
        "saved_regions": len(regions), "target_regions": sum(r["kind"] in TARGET_KINDS for r in regions),
        "attention_regions": len(warning), "body_attention_regions": sum(r["tree"] == "body" for r in warning),
        "furniture_attention_regions": sum(r["tree"] == "furniture" for r in warning),
        **{state: sum(r["status"] == state for r in regions) for state in (*WARNING_STATUSES, "has_nonempty_line_geometry")},
        "not_target_regions": sum(r["status"] == "not_target_region" for r in regions),
        "unevaluated_regions": sum(r["status"] == "unevaluated" for r in regions),
        "incomplete_layout_pages": sum(p["layout_evidence_incomplete"] for p in pages)}
    return {"schema_version": 1, "kind": "ocr_omission_diagnostics", "algorithm": "saved_docling_region_geometry_v1",
            "source_sha256": recovery["source_sha256"], "recovery_sha256": recovery_sha256,
            "proposals_sha256": proposals_sha256, "docling_sha256": proposals["docling_sha256"],
            "manifest_sha256": proposals["manifest_sha256"], "page_count": recovery["page_count"],
            "coordinate_system": "candidate_raster_fraction", "parameters": _parameters(), "pages": pages,
            "summary": summary, "scope": "saved_docling_regions_only_not_source_completeness",
            "requires_attention": bool(warning or summary["unevaluated_pages"] or summary["incomplete_layout_pages"]),
            **_ATTESTATIONS}


def validate_omission_diagnostics(payload: object, recovery: object, proposals: object, *,
                                  recovery_sha256: str, proposals_sha256: str) -> dict:
    """Rebuild every count/status/binding; reject forged and deep extra fields."""
    expected = build_omission_diagnostics(recovery, proposals, recovery_sha256=recovery_sha256,
                                          proposals_sha256=proposals_sha256)
    if not _exact(payload, expected):
        raise ValueError("OCR omission diagnostics differ from fixed source evidence")
    return expected


def validate_omission_decisions(decisions: object, diagnostics: object) -> list[dict]:
    """Bind operator decisions to known regions of a derived diagnostic.

    This validates decisions, not the diagnostic's authenticity. Imported
    diagnostics require validate_omission_diagnostics against fixed inputs.
    """
    if (type(diagnostics) is not dict or diagnostics.get("kind") != "ocr_omission_diagnostics"
            or type(diagnostics.get("schema_version")) is not int or diagnostics["schema_version"] != 1):
        raise ValueError("invalid OCR omission diagnostic contract")
    count = _integer(diagnostics.get("page_count"), 1, 5000)
    pages = diagnostics.get("pages")
    if type(pages) is not list or len(pages) != count:
        raise ValueError("invalid OCR omission page coverage")
    known = set()
    for number, page in enumerate(pages, 1):
        if type(page) is not dict or type(page.get("page_number")) is not int or page["page_number"] != number:
            raise ValueError("invalid OCR omission page identity")
        regions = page.get("regions")
        if type(regions) is not list or len(regions) > MAX_REGIONS:
            raise ValueError("invalid OCR omission region budget")
        for region in regions:
            if (type(region) is not dict or type(region.get("ref")) is not str
                    or len(region["ref"]) > 64 or _REF.fullmatch(region["ref"]) is None):
                raise ValueError("invalid OCR omission region identity")
            key = (number, region["ref"])
            if key in known or len(known) >= MAX_DECISIONS:
                raise ValueError("duplicate or excessive OCR omission regions")
            known.add(key)
    if type(decisions) is not list or len(decisions) > MAX_DECISIONS:
        raise ValueError("invalid OCR omission decision budget")
    result, seen = [], set()
    for decision in decisions:
        decision = _fields(decision, {"page_number", "region_ref", "decision"})
        number = _integer(decision["page_number"], 1, count)
        ref, value = decision["region_ref"], decision["decision"]
        if type(ref) is not str or type(value) is not str or value not in DECISIONS:
            raise ValueError("invalid OCR omission decision")
        key = (number, ref)
        if key not in known or key in seen:
            raise ValueError("unknown or duplicate OCR omission decision region")
        seen.add(key)
        result.append(dict(decision))
    return sorted(result, key=lambda d: (d["page_number"], d["region_ref"]))


def build_omission_review(recovery: object, proposals: object, *, recovery_sha256: str,
                          proposals_sha256: str, decisions: object, confirmed: object) -> dict:
    """Record partial reviewed assessments without deleting unresolved warnings."""
    if confirmed is not True:
        raise ValueError("explicit OCR omission assessment confirmation is required")
    diagnostics = build_omission_diagnostics(recovery, proposals, recovery_sha256=recovery_sha256,
                                             proposals_sha256=proposals_sha256)
    decisions = validate_omission_decisions(decisions, diagnostics)
    resolved = {(decision["page_number"], decision["region_ref"]) for decision in decisions}
    warning = {(page["page_number"], region["ref"]) for page in diagnostics["pages"]
               for region in page["regions"] if region["status"] in WARNING_STATUSES}
    raw = json.dumps(diagnostics, sort_keys=True, separators=(",", ":"), ensure_ascii=False, allow_nan=False).encode("utf-8")
    digest = hashlib.sha256(_DIGEST_POLICY["domain"].encode("ascii") + b"\0" + raw).hexdigest()
    return {"schema_version": 1, "kind": "ocr_omission_review", "diagnostics": diagnostics,
            "diagnostics_content_sha256": digest, "diagnostics_digest_policy": dict(_DIGEST_POLICY),
            "decisions": decisions, "confirmed": True,
            "summary": {"recorded_decisions": len(decisions),
                        "unresolved_regions": diagnostics["summary"]["saved_regions"] - len(decisions),
                        "unresolved_attention_regions": len(warning - resolved),
                        **{value: sum(d["decision"] == value for d in decisions) for value in DECISIONS}},
            "operator_review": "declared_by_local_operator_not_authenticated",
            "acceptance": "reviewed_assessments_not_complete_source_verification",
            "requires_attention": True, **_ATTESTATIONS}


def validate_omission_review(payload: object, recovery: object, proposals: object, *,
                             recovery_sha256: str, proposals_sha256: str) -> dict:
    if type(payload) is not dict:
        raise ValueError("invalid OCR omission review")
    expected = build_omission_review(recovery, proposals, recovery_sha256=recovery_sha256,
        proposals_sha256=proposals_sha256, decisions=payload.get("decisions"), confirmed=payload.get("confirmed"))
    if not _exact(payload, expected):
        raise ValueError("OCR omission review differs from fixed evidence or declared decisions")
    return expected
