"""Bounded review-only proposals from saved Docling structure, not inferred truth.

This policy reads no paths, loads no Docling models, and never copies source text
into its output. The outer adapter owns completion/source authenticity checks.
"""

from __future__ import annotations

import copy
import math
import re

from evaluation_inputs import _hex_digest
from ocr_layout import _rectangle
from ocr_recovery_comparison import validate_recovery_report


ALGORITHM = "saved-docling-body-order-v1"
MAX_ITEMS = 20_000
MAX_REGIONS = 500
MAX_LINES = 2000
MAX_CELLS = 2000
_COLLECTIONS = ("texts", "tables", "pictures", "groups", "key_value_items", "form_items")
_REF = re.compile(r"#/(?:texts|tables|pictures|groups|key_value_items|form_items)/(?:0|[1-9][0-9]{0,4})")
_REASONS = (
    "effective_input_preprocessed", "candidate_preprocessed", "candidate_unavailable",
    "empty_candidate", "missing_page_geometry", "page_geometry_mismatch",
    "missing_provenance", "invalid_region_geometry", "multi_page_item",
    "unattached_items", "region_limit", "table_structure_unavailable",
    "no_regions", "line_limit", "ambiguous_line_geometry", "unassigned_lines",
    "ambiguous_line_assignment",
)
_PARAMETERS = {"max_items": MAX_ITEMS, "max_regions_per_page": MAX_REGIONS,
               "max_lines_per_page": MAX_LINES, "max_cells_per_table": MAX_CELLS,
               "raster_rounding_tolerance_pixels": 2, "spanning_heading_min_width": 0.65}


def _fields(value: object, keys: set[str]) -> dict:
    if not isinstance(value, dict) or set(value) != keys:
        raise ValueError("invalid Docling proposal fields")
    return value


def _exact(value: object, expected: object) -> bool:
    """Type-strict bounded-shape equality without serializing untrusted trees."""
    pending = [(value, expected)]
    while pending:
        actual, wanted = pending.pop()
        if type(actual) is not type(wanted):
            return False
        if isinstance(wanted, dict):
            if set(actual) != set(wanted):
                return False
            pending.extend((actual[key], wanted[key]) for key in wanted)
        elif isinstance(wanted, list):
            if len(actual) != len(wanted):
                return False
            pending.extend(zip(actual, wanted))
        elif actual != wanted:
            return False
    return True


def _integer(value: object, low: int, high: int) -> int:
    if type(value) is not int or not low <= value <= high:
        raise ValueError("invalid bounded Docling integer")
    return value


def _number(value: object, low: float, high: float) -> float:
    if type(value) not in {int, float} or not low <= value <= high or not math.isfinite(value):
        raise ValueError("invalid bounded Docling coordinate")
    return float(value)


def _bounds(value: object) -> list[float]:
    if not isinstance(value, list) or len(value) != 4:
        raise ValueError("invalid Docling rectangle")
    bounds = [_number(n, 0, 1) for n in value]
    if not bounds[0] < bounds[2] or not bounds[1] < bounds[3]:
        raise ValueError("invalid Docling rectangle area")
    return bounds


def _box(value: object, width: float, height: float) -> list[float]:
    value = _fields(value, {"l", "t", "r", "b", "coord_origin"})
    left, right = (_number(value[k], 0, width) / width for k in ("l", "r"))
    top, bottom = (_number(value[k], 0, height) / height for k in ("t", "b"))
    if value["coord_origin"] == "BOTTOMLEFT":
        top, bottom = 1 - top, 1 - bottom
    elif value["coord_origin"] != "TOPLEFT":
        raise ValueError("unsupported Docling coordinate origin")
    return _bounds([left, top, right, bottom])


def _document(payload: object, page_count: int) -> tuple[dict, list[tuple[str, dict, str]], bool]:
    if (not isinstance(payload, dict) or payload.get("schema_name") != "DoclingDocument"
            or not isinstance(payload.get("version"), str)
            or re.fullmatch(r"1\.[0-9]{1,3}\.[0-9]{1,3}", payload["version"]) is None):
        raise ValueError("unsupported saved Docling document")
    pages = payload.get("pages")
    if not isinstance(pages, dict) or len(pages) > page_count:
        raise ValueError("invalid Docling pages")
    for key, page in pages.items():
        if (not isinstance(key, str) or not key.isascii() or not key.isdecimal()
                or str(int(key)) != key or not 1 <= int(key) <= page_count
                or not isinstance(page, dict) or page.get("page_no") != int(key)
                or type(page.get("page_no")) is not int):
            raise ValueError("invalid Docling page identity")
    catalog = {}
    for collection in _COLLECTIONS:
        values = payload.get(collection, [])
        if not isinstance(values, list) or len(values) + len(catalog) > MAX_ITEMS:
            raise ValueError("Docling item limit exceeded")
        for index, item in enumerate(values):
            ref = f"#/{collection}/{index}"
            if not isinstance(item, dict) or item.get("self_ref") != ref:
                raise ValueError("invalid Docling item reference")
            provenance = item.get("prov")
            if isinstance(provenance, list):
                for entry in provenance:
                    if isinstance(entry, dict) and type(entry.get("page_no")) is int:
                        _integer(entry["page_no"], 1, page_count)
            catalog[ref] = item
    ordered, seen = [], set()
    for root in ("body", "furniture"):
        node = payload.get(root)
        if not isinstance(node, dict) or node.get("self_ref") != f"#/{root}":
            raise ValueError("missing Docling structure root")
        stack = [(node, 0, None)]
        while stack:
            item, depth, ref = stack.pop()
            if depth > 64:
                raise ValueError("Docling structure depth exceeded")
            if ref is not None:
                if ref in seen:
                    raise ValueError("cyclic or repeated Docling structure reference")
                seen.add(ref)
                if not ref.startswith("#/groups/"):
                    ordered.append((ref, item, root))
            children = item.get("children", [])
            if not isinstance(children, list) or len(children) > MAX_ITEMS:
                raise ValueError("invalid Docling children")
            for child in reversed(children):
                # RefItem.cref has the "$ref" alias. model_dump defaults to
                # Python field names; export_to_dict/save_as_json use aliases.
                if not isinstance(child, dict) or set(child) not in ({"$ref"}, {"cref"}):
                    raise ValueError("invalid Docling child reference fields")
                child = child["$ref"] if "$ref" in child else child["cref"]
                if not isinstance(child, str) or child not in catalog:
                    raise ValueError("dangling Docling structure reference")
                stack.append((catalog[child], depth + 1, child))
            if len(stack) > MAX_ITEMS:
                raise ValueError("Docling structure breadth exceeded")
    unattached = any(ref not in seen and not ref.startswith("#/groups/") for ref in catalog)
    return pages, ordered, unattached


def _table(item: dict, width: float, height: float) -> dict | None:
    data = item.get("data")
    try:
        if not isinstance(data, dict):
            return None
        rows = _integer(data.get("num_rows"), 1, MAX_CELLS)
        columns = _integer(data.get("num_cols"), 1, MAX_CELLS)
        raw = data.get("table_cells")
        if not isinstance(raw, list) or not 1 <= len(raw) <= MAX_CELLS:
            return None
        cells, occupied = [], set()
        for cell in raw:
            if not isinstance(cell, dict):
                return None
            r0, r1 = (_integer(cell.get(k), 0, rows) for k in ("start_row_offset_idx", "end_row_offset_idx"))
            c0, c1 = (_integer(cell.get(k), 0, columns) for k in ("start_col_offset_idx", "end_col_offset_idx"))
            if (not r0 < r1 or not c0 < c1 or (r1-r0)*(c1-c0) > MAX_CELLS
                    or len(occupied) + (r1-r0)*(c1-c0) > MAX_CELLS):
                return None
            positions = {(r, c) for r in range(r0, r1) for c in range(c0, c1)}
            if positions & occupied:
                return None
            occupied.update(positions)
            cells.append({"row_start": r0, "row_end": r1, "column_start": c0, "column_end": c1,
                          "bbox": _box(cell["bbox"], width, height) if cell.get("bbox") is not None else None})
        return {"rows": rows, "columns": columns, "cells": cells}
    except (ValueError, KeyError, TypeError):
        return None


def _line_order(candidate: dict, regions: list[dict]) -> tuple[list[int] | None, list[str]]:
    if not candidate["text"].strip():
        return None, ["empty_candidate"]
    if len(candidate["lines"]) > MAX_LINES:
        return None, ["line_limit"]
    raster = candidate["raster"]
    assigned = [[] for _ in regions]
    reasons = set()
    for index, line in enumerate(candidate["lines"]):
        box = _rectangle(line["box"])
        if box is None:
            reasons.add("ambiguous_line_geometry")
            continue
        x0, y0, x1, y1 = [n / raster["width" if i % 2 == 0 else "height"] for i, n in enumerate(box)]
        matches = [i for i, region in enumerate(regions)
                   if region["bbox"][0] <= x0 and region["bbox"][1] <= y0
                   and x1 <= region["bbox"][2] and y1 <= region["bbox"][3]]
        if len(matches) != 1:
            reasons.add("unassigned_lines" if not matches else "ambiguous_line_assignment")
        else:
            assigned[matches[0]].append(index)
    if reasons:
        return None, [reason for reason in _REASONS if reason in reasons]
    # Keep OCR order within each region (including tables); only move whole
    # assignments according to the saved tree. No cell-order inference.
    body_order = [index for region, group in zip(regions, assigned) if region["tree"] == "body" for index in group]
    body_positions = set(body_order)
    replacement = iter(body_order)
    # Furniture has no position in the body's reading-order tree. Retain its
    # exact engine slots instead of inventing header/footer placement.
    return [next(replacement) if index in body_positions else index
            for index in range(len(candidate["lines"]))], []


def _page(number: int, candidate: dict | None, page: dict | None, ordered: list,
          *, effective_input_kind: str, unattached: bool) -> dict:
    result = {"page_number": number, "status": "abstained", "reasons": [], "regions": [],
              "spanning_heading_refs": [], "line_order": None, "page_geometry": None}
    reasons = set()
    if candidate is None:
        result.update(status="unavailable", reasons=["candidate_unavailable"])
        return result
    if effective_input_kind != "original":
        reasons.add("effective_input_preprocessed")
    if "preprocessing" in candidate:
        reasons.add("candidate_preprocessed")
    try:
        size = page["size"] if page is not None else None
        if not isinstance(size, dict):
            raise ValueError("missing page size")
        width, height = (_number(size.get(k), 0.01, 100_000) for k in ("width", "height"))
        raster = candidate["raster"]
        result["page_geometry"] = {"width_points": width, "height_points": height,
                                   "raster_width": raster["width"], "raster_height": raster["height"],
                                   "dpi": raster["dpi"]}
        if any(abs(actual - expected * raster["dpi"] / 72) > 2 for actual, expected in (
                (raster["width"], width), (raster["height"], height))):
            reasons.add("page_geometry_mismatch")
    except (ValueError, KeyError, TypeError):
        reasons.add("missing_page_geometry")
    if reasons:
        result["reasons"] = [reason for reason in _REASONS if reason in reasons]
        return result
    if unattached:
        reasons.add("unattached_items")
    cell_count = 0
    for ref, item, root in ordered:
        provenance = item.get("prov")
        if not isinstance(provenance, list) or not provenance or len(provenance) > 20:
            reasons.add("missing_provenance")
            continue
        if any(not isinstance(prov, dict) or type(prov.get("page_no")) is not int for prov in provenance):
            reasons.add("missing_provenance")
            continue
        matching = [prov for prov in provenance if prov["page_no"] == number]
        if not matching:
            continue
        if len(provenance) != 1:
            reasons.add("multi_page_item")
            continue
        try:
            bounds = _box(matching[0].get("bbox"), width, height)
        except (ValueError, KeyError, TypeError):
            reasons.add("invalid_region_geometry")
            continue
        if len(result["regions"]) >= MAX_REGIONS:
            reasons.add("region_limit")
            continue
        label = item.get("label")
        kind = ("table" if ref.startswith("#/tables/") else "picture" if ref.startswith("#/pictures/")
                else "heading" if label in ("title", "section_header")
                else "text" if ref.startswith("#/texts/") else "other")
        table = _table(item, width, height) if kind == "table" else None
        if table is not None and any(cell["bbox"] is not None and not (
                bounds[0] <= cell["bbox"][0] < cell["bbox"][2] <= bounds[2]
                and bounds[1] <= cell["bbox"][1] < cell["bbox"][3] <= bounds[3]) for cell in table["cells"]):
            table = None
        if table is not None:
            if cell_count + len(table["cells"]) > MAX_CELLS:
                table = None
            else:
                cell_count += len(table["cells"])
        if kind == "table" and table is None:
            reasons.add("table_structure_unavailable")
        region = {"ref": ref, "kind": kind, "bbox": bounds, "tree": root,
                  "order": len(result["regions"]), "table": table}
        result["regions"].append(region)
        if kind == "heading" and bounds[2] - bounds[0] >= _PARAMETERS["spanning_heading_min_width"]:
            result["spanning_heading_refs"].append(ref)
    if not result["regions"]:
        reasons.add("no_regions")
    order, assignment_reasons = _line_order(candidate, result["regions"])
    reasons.update(assignment_reasons)
    result["reasons"] = [reason for reason in _REASONS if reason in reasons]
    if not reasons:
        result.update(status="proposed", line_order=order)
    return result


def build_docling_proposals(docling: object, recovery: object, *, recovery_sha256: str,
                            docling_sha256: str, manifest_sha256: str,
                            effective_input_kind: str) -> dict:
    """Return unaccepted source-bound regions/order, with conservative abstention."""
    recovery = validate_recovery_report(recovery)
    digests = {"source_sha256": recovery["source_sha256"], "recovery_sha256": recovery_sha256,
               "docling_sha256": docling_sha256, "manifest_sha256": manifest_sha256}
    for digest in digests.values():
        _hex_digest(digest, label="Docling proposal digest")
    if effective_input_kind not in ("original", "preprocessed"):
        raise ValueError("unsupported Docling effective input")
    pages, ordered, unattached = _document(docling, recovery["page_count"])
    selected = {page["page_number"]: page for page in recovery["pages"]}
    deferred = sorted(page["page_number"] for page in recovery["deferred_pages"])
    proposals = [_page(number, selected[number]["candidate"], pages.get(str(number)), ordered,
                       effective_input_kind=effective_input_kind, unattached=unattached)
                 for number in sorted(selected)]
    result = {
        "schema_version": 1, "kind": "ocr_docling_proposals", "algorithm": ALGORITHM,
        **digests, "effective_input_kind": effective_input_kind, "page_count": recovery["page_count"],
        "coordinate_system": "candidate_raster_fraction", "parameters": dict(_PARAMETERS),
        "pages": proposals, "coverage": {"selected_pages": sorted(selected), "deferred_pages": deferred,
            "not_selected_pages": [n for n in range(1, recovery["page_count"] + 1) if n not in selected and n not in deferred]},
        "summary": {"pages": len(proposals), **{status: sum(p["status"] == status for p in proposals)
                    for status in ("proposed", "abstained", "unavailable")},
                    "regions": sum(len(p["regions"]) for p in proposals)},
        "requires_attention": True, "acceptance": "manual_review_required",
        "canonical_extraction_modified": False, "recognition_rerun": False,
    }
    return validate_docling_proposals(result, recovery=recovery, recovery_sha256=recovery_sha256)


def validate_docling_proposals(payload: object, *, recovery: object, recovery_sha256: str) -> dict:
    """Validate editor imports against current candidate geometry and permutations.

    Hashes bind artifacts, not trust in Docling's predictions. This deliberately
    does not interpret arbitrary text or accept an accuracy assertion.
    """
    recovery = validate_recovery_report(recovery)
    _hex_digest(recovery_sha256, label="recovery digest")
    report = _fields(payload, {"schema_version", "kind", "algorithm", "source_sha256", "recovery_sha256",
        "docling_sha256", "manifest_sha256", "effective_input_kind", "page_count", "coordinate_system",
        "parameters", "pages", "coverage", "summary", "requires_attention", "acceptance",
        "canonical_extraction_modified", "recognition_rerun"})
    if (type(report["schema_version"]) is not int or report["schema_version"] != 1
            or report["kind"] != "ocr_docling_proposals" or report["algorithm"] != ALGORITHM
            or report["source_sha256"] != recovery["source_sha256"] or report["recovery_sha256"] != recovery_sha256
            or type(report["page_count"]) is not int or report["page_count"] != recovery["page_count"]
            or report["coordinate_system"] != "candidate_raster_fraction"
            or report["effective_input_kind"] not in ("original", "preprocessed")
            or report["requires_attention"] is not True or report["acceptance"] != "manual_review_required"
            or report["canonical_extraction_modified"] is not False or report["recognition_rerun"] is not False
            or not _exact(report["parameters"], _PARAMETERS)):
        raise ValueError("invalid Docling proposal binding or attestation")
    for key in ("source_sha256", "recovery_sha256", "docling_sha256", "manifest_sha256"):
        _hex_digest(report[key], label="proposal digest")
    selected = {p["page_number"]: p for p in recovery["pages"]}
    deferred = sorted(p["page_number"] for p in recovery["deferred_pages"])
    expected_coverage = {"selected_pages": sorted(selected), "deferred_pages": deferred,
                        "not_selected_pages": [n for n in range(1, recovery["page_count"]+1)
                                               if n not in selected and n not in deferred]}
    if not _exact(report["coverage"], expected_coverage):
        raise ValueError("Docling proposal coverage differs from recovery")
    if not isinstance(report["pages"], list) or len(report["pages"]) != len(selected):
        raise ValueError("invalid Docling proposal pages")
    numbers = []
    for page in report["pages"]:
        _fields(page, {"page_number", "status", "reasons", "regions", "spanning_heading_refs", "line_order", "page_geometry"})
        number = _integer(page["page_number"], 1, recovery["page_count"])
        if number not in selected:
            raise ValueError("Docling proposal page is not selected")
        numbers.append(number)
        candidate = selected[number]["candidate"]
        reasons = page["reasons"]
        if (not isinstance(reasons, list) or any(not isinstance(r, str) for r in reasons)
                or reasons != [r for r in _REASONS if r in reasons]):
            raise ValueError("invalid Docling proposal reasons")
        if page["status"] not in ("proposed", "abstained", "unavailable"):
            raise ValueError("invalid Docling proposal status")
        regions = page["regions"]
        if not isinstance(regions, list) or len(regions) > MAX_REGIONS:
            raise ValueError("invalid Docling proposal region count")
        refs, cell_count = [], 0
        for order, region in enumerate(regions):
            _fields(region, {"ref", "kind", "bbox", "tree", "order", "table"})
            if (not isinstance(region["ref"], str) or _REF.fullmatch(region["ref"]) is None
                    or region["ref"] in refs or region["kind"] not in ("text", "heading", "table", "picture", "other")
                    or region["tree"] not in ("body", "furniture")
                    or type(region["order"]) is not int or region["order"] != order):
                raise ValueError("invalid Docling proposal region identity")
            refs.append(region["ref"])
            _bounds(region["bbox"])
            if region["table"] is not None:
                if region["kind"] != "table":
                    raise ValueError("non-table proposal has table cells")
                table = _fields(region["table"], {"rows", "columns", "cells"})
                raw_cells = []
                if not isinstance(table["cells"], list) or not 1 <= len(table["cells"]) <= MAX_CELLS:
                    raise ValueError("invalid proposal table cells")
                cell_count += len(table["cells"])
                if cell_count > MAX_CELLS:
                    raise ValueError("proposal page table cell limit exceeded")
                for cell in table["cells"]:
                    _fields(cell, {"row_start", "row_end", "column_start", "column_end", "bbox"})
                    bounds = _bounds(cell["bbox"]) if cell["bbox"] is not None else None
                    if bounds is not None and not (region["bbox"][0] <= bounds[0] < bounds[2] <= region["bbox"][2]
                                                  and region["bbox"][1] <= bounds[1] < bounds[3] <= region["bbox"][3]):
                        raise ValueError("proposal table cell lies outside table")
                    raw_cells.append({"start_row_offset_idx": cell["row_start"], "end_row_offset_idx": cell["row_end"],
                        "start_col_offset_idx": cell["column_start"], "end_col_offset_idx": cell["column_end"],
                        "bbox": dict(zip(("l", "t", "r", "b"), bounds), coord_origin="TOPLEFT") if bounds else None})
                if _table({"data": {"num_rows": table["rows"], "num_cols": table["columns"], "table_cells": raw_cells}}, 1, 1) != table:
                    raise ValueError("invalid proposal table geometry")
        headings = [r["ref"] for r in regions if r["kind"] == "heading"
                    and r["bbox"][2] - r["bbox"][0] >= _PARAMETERS["spanning_heading_min_width"]]
        if page["spanning_heading_refs"] != headings:
            raise ValueError("invalid spanning heading proposal")
        geometry = page["page_geometry"]
        if geometry is not None:
            _fields(geometry, {"width_points", "height_points", "raster_width", "raster_height", "dpi"})
            if candidate is None:
                raise ValueError("unavailable candidate has proposal geometry")
            for key, raster_key in (("raster_width", "width"), ("raster_height", "height"), ("dpi", "dpi")):
                if type(geometry[key]) is not int or geometry[key] != candidate["raster"][raster_key]:
                    raise ValueError("proposal raster differs from recovery")
            for key in ("width_points", "height_points"):
                _number(geometry[key], .01, 100_000)
        geometry_reasons = set()
        if candidate is not None:
            if report["effective_input_kind"] != "original":
                geometry_reasons.add("effective_input_preprocessed")
            if "preprocessing" in candidate:
                geometry_reasons.add("candidate_preprocessed")
            if geometry is None:
                geometry_reasons.add("missing_page_geometry")
            elif any(abs(geometry[a] - geometry[b] * geometry["dpi"] / 72) > 2 for a, b in (
                    ("raster_width", "width_points"), ("raster_height", "height_points"))):
                geometry_reasons.add("page_geometry_mismatch")
        declared_geometry_reasons = set(reasons) & {
            "effective_input_preprocessed", "candidate_preprocessed", "missing_page_geometry", "page_geometry_mismatch"}
        if declared_geometry_reasons != geometry_reasons or regions and geometry_reasons:
            raise ValueError("Docling regions or abstentions contradict supported geometry")
        if page["status"] == "proposed":
            if (reasons or not regions or candidate is None or geometry is None or "preprocessing" in candidate
                    or report["effective_input_kind"] != "original"
                    or any(abs(geometry[a] - geometry[b] * geometry["dpi"] / 72) > 2 for a, b in (
                        ("raster_width", "width_points"), ("raster_height", "height_points")))):
                raise ValueError("unsupported Docling proposal geometry")
            order, errors = _line_order(candidate, regions)
            if errors or page["line_order"] != order or any(type(n) is not int for n in page["line_order"]):
                raise ValueError("proposal order differs from complete candidate assignment")
        elif page["line_order"] is not None or not reasons:
            raise ValueError("abstained proposal cannot carry an order")
        if (candidate is None) != (page["status"] == "unavailable"):
            raise ValueError("proposal availability differs from recovery")
        if candidate is None and (regions or geometry is not None or reasons != ["candidate_unavailable"]):
            raise ValueError("unavailable proposal contains geometry")
    if numbers != sorted(selected):
        raise ValueError("duplicate or unordered Docling proposal pages")
    expected = {"pages": len(numbers), **{status: sum(p["status"] == status for p in report["pages"])
                for status in ("proposed", "abstained", "unavailable")},
                "regions": sum(len(p["regions"]) for p in report["pages"])}
    if report["summary"] != expected or any(type(n) is not int for n in report["summary"].values()):
        raise ValueError("invalid Docling proposal summary")
    return copy.deepcopy(report)


def build_docling_review(proposals: object, *, recovery: object, recovery_sha256: str,
                         proposals_sha256: str, confirmed_pages: object) -> dict:
    """Record explicit operator-declared review as a separate derived artifact.

    This declaration is not authenticated approval, measured accuracy, or
    authorization to replace canonical extraction. Original candidates persist.
    """
    recovery = validate_recovery_report(recovery)
    proposals = validate_docling_proposals(proposals, recovery=recovery, recovery_sha256=recovery_sha256)
    _hex_digest(proposals_sha256, label="Docling proposals digest")
    if not isinstance(confirmed_pages, list) or not 1 <= len(confirmed_pages) <= 20:
        raise ValueError("confirm 1..20 explicit proposed pages")
    numbers = [_integer(n, 1, recovery["page_count"]) for n in confirmed_pages]
    if len(numbers) != len(set(numbers)):
        raise ValueError("duplicate confirmed Docling page")
    available = {p["page_number"]: p for p in proposals["pages"] if p["status"] == "proposed"}
    candidates = {p["page_number"]: p["candidate"] for p in recovery["pages"]}
    if not set(numbers) <= available.keys():
        raise ValueError("only complete proposed Docling orders can be confirmed")
    pages = []
    for number in sorted(numbers):
        order, candidate = available[number]["line_order"], candidates[number]
        pages.append({"page_number": number, "original_text": candidate["text"],
                      "proposed_text": "\n".join(candidate["lines"][index]["text"] for index in order),
                      "line_order": list(order)})
    return {"schema_version": 1, "kind": "ocr_docling_review", "source_sha256": recovery["source_sha256"],
            "recovery_sha256": recovery_sha256, "proposals_sha256": proposals_sha256,
            "page_count": recovery["page_count"], "confirmed_pages": sorted(numbers), "pages": pages,
            "operator_review": "declared_by_local_operator_not_authenticated",
            "acceptance": "reviewed_proposal_not_canonical", "requires_attention": True,
            "accuracy_verified": False, "canonical_extraction_modified": False, "recognition_rerun": False}


def validate_docling_review(payload: object, *, proposals: object, recovery: object,
                            recovery_sha256: str, proposals_sha256: str) -> dict:
    """Rebuild exact review text/order from bound candidates, never trust edits."""
    if not isinstance(payload, dict):
        raise ValueError("invalid Docling review")
    expected = build_docling_review(proposals, recovery=recovery, recovery_sha256=recovery_sha256,
                                    proposals_sha256=proposals_sha256, confirmed_pages=payload.get("confirmed_pages"))
    if not _exact(payload, expected):
        raise ValueError("Docling review differs from bound source candidates or declaration")
    return expected
