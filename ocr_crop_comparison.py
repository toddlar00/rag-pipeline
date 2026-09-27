"""Reviewed, occurrence-bound same-physical-crop OCR comparisons.

This adapter does not read files, render scans, run OCR or publish corrections.
Raw report digests are caller-supplied provenance; a file/host boundary must
obtain them from strict snapshots. Complete report validators and canonical
record hashes bind the selected declared occurrence, not source authenticity.
All text scoring is delegated unchanged to :func:`ocr_comparison.compare_ocr`.
"""

from __future__ import annotations

import hashlib
import json
import math
import re

from ocr_comparison import compare_ocr
import ocr_evaluation
from ocr_hardscan_io import validate_report as validate_hardscan_report
from ocr_recovery_comparison import validate_recovery_report
from ocr_regions import validate_region_review


MAX_REPORT_BYTES = 128 * 1024 * 1024
MAX_REFERENCE_BYTES = 256 * 1024
MAX_RESULT_BYTES = 2 * 1024 * 1024
MAX_JSON_NODES = 2_000_000
MAX_JSON_DEPTH = 32
MAX_SCALAR_CHARACTERS = 100_000  # Existing recovery text limit; references remain20k.
MAX_KEY_CHARACTERS = 128
_SHA = re.compile(r"[0-9a-f]{64}")
_REGION_ID = re.compile(r"[A-Za-z0-9][A-Za-z0-9._-]{0,63}")
_AVAILABLE = {"candidate_available", "empty_candidate"}
_REFERENCE_KEYS = {"schema_version", "kind", "reference_id", "scope", "anchor", "reference",
                   "critical_tokens", "review", "reference_sha256"}
_EDGE_TOLERANCE = 1e-12  # Disclosure only, never used to equate requested scopes.


def _fail():
    raise ValueError("invalid crop comparison input or binding")


def _preflight_json(value, limit, *, max_nodes, max_depth, max_key_characters,
                    max_scalar_characters, max_integer_bits, finite_floats, fail):
    """Shared crop JSON traversal; callers retain their limits and errors."""
    nodes = 0
    string_bytes = 0

    def check_string(text, maximum):
        nonlocal string_bytes
        if len(text) > maximum:
            fail()
        # Exact ensure_ascii=False JSON string bytes, including quotes. Check
        # before JSONEncoder can allocate a complete escaped scalar fragment.
        size = 2
        for character in text:
            code = ord(character)
            if 0xD800 <= code <= 0xDFFF:
                fail()
            size += (2 if character in ('"', "\\", "\b", "\f", "\n", "\r", "\t") else
                     6 if code < 32 else 1 if code < 128 else 2 if code < 2048 else 3 if code < 65536 else 4)
            if size > limit:
                fail()
        string_bytes += size
        if string_bytes > limit:
            fail()

    def check(item, depth):
        nonlocal nodes
        nodes += 1
        if nodes > max_nodes or depth > max_depth:
            fail()
        kind = type(item)
        if kind is dict:
            if len(item) > max_nodes:
                fail()
            for key, child in item.items():
                if type(key) is not str:
                    fail()
                check_string(key, max_key_characters)
                check(child, depth + 1)
        elif kind is list:
            if len(item) > max_nodes:
                fail()
            for child in item:
                check(child, depth + 1)
        elif kind is str:
            check_string(item, max_scalar_characters)
        elif kind is int:
            if item.bit_length() > max_integer_bits:
                fail()
        elif kind is float and finite_floats:
            if not math.isfinite(item):
                fail()
        elif kind not in ((bool, type(None)) if finite_floats else (float, bool, type(None))):
            fail()

    check(value, 0)


def _bytes(value, limit):
    """Bound primitive traversal before JSON serialization or validator copies."""
    try:
        _preflight_json(value, limit, max_nodes=MAX_JSON_NODES, max_depth=MAX_JSON_DEPTH,
            max_key_characters=MAX_KEY_CHARACTERS, max_scalar_characters=MAX_SCALAR_CHARACTERS,
            max_integer_bits=4096, finite_floats=False, fail=_fail)
        blocks, size = [], 0
        encoder = json.JSONEncoder(ensure_ascii=False, allow_nan=False, sort_keys=True, separators=(",", ":"))
        for fragment in encoder.iterencode(value):
            block = fragment.encode("utf-8")
            size += len(block)
            if size > limit:
                _fail()
            blocks.append(block)
        return b"".join(blocks)
    except (ValueError, TypeError, UnicodeError, OverflowError, RecursionError):
        _fail()


def _digest(value, limit=MAX_REFERENCE_BYTES):
    return hashlib.sha256(_bytes(value, limit)).hexdigest()


def _hex(value):
    if type(value) is not str or _SHA.fullmatch(value) is None:
        _fail()
    return value


def _id(value):
    if type(value) is not str or _REGION_ID.fullmatch(value) is None:
        _fail()
    return value


def _report(value):
    _bytes(value, MAX_REPORT_BYTES)
    if type(value) is not dict:
        _fail()
    try:
        if value.get("kind") == "ocr_region_review":
            return "regions", validate_region_review(value)
        if value.get("kind") == "ocr_hardscan_review":
            return "hardscan", validate_hardscan_report(value)
        # A valid whole-page report is an explicitly unavailable crop input,
        # never a source from which candidate lines/text are clipped heuristically.
        return "pages", validate_recovery_report(value)
    except (ValueError, TypeError, KeyError, OverflowError, RecursionError):
        _fail()


def _row(report, operation, identifier):
    _id(identifier)
    if operation == "pages":
        return None
    return next((row for row in report["regions"] if row["region_id"] == identifier), None)


def _coordinate(value):
    number = float(value)
    return 0.0 if number == 0.0 else number


def _scope(report, row):
    page = row["geometry"]["page"]
    scope = {"source_sha256": report["source_sha256"], "page_count": report["page_count"],
        "page_number": row["page_number"], "coordinate_system": "original_page_display_fraction",
        "bbox": [_coordinate(value) for value in row["bbox"]], "page_geometry": {
            "display_rect_points": [_coordinate(value) for value in page["display_rect_points"]],
            "cropbox_points": [_coordinate(value) for value in page["cropbox_points"]],
            "rotation_degrees": page["rotation_degrees"]}}
    scope["scope_sha256"] = _digest(scope)
    return scope


def validate_crop_scope(payload, *, source_sha256, page_count):
    """Validate a canonical nominal scope without a PDF parser or crop report.

    A renderer must additionally check the actual fixed source/page geometry.
    This validates declared geometry and its digest, not physical authenticity.
    """
    _hex(source_sha256)
    _bytes(payload, MAX_REFERENCE_BYTES)
    if (type(page_count) is not int or not 1 <= page_count <= 5000
            or type(payload) is not dict or set(payload) != {"source_sha256", "page_count", "page_number",
                "coordinate_system", "bbox", "page_geometry", "scope_sha256"}
            or payload["source_sha256"] != source_sha256
            or type(payload["page_count"]) is not int or payload["page_count"] != page_count
            or type(payload["page_number"]) is not int or not 1 <= payload["page_number"] <= page_count
            or payload["coordinate_system"] != "original_page_display_fraction"):
        _fail()
    _hex(payload["scope_sha256"])

    def rectangle(value, *, fractions=False):
        if type(value) is not list or len(value) != 4:
            _fail()
        result = []
        for number in value:
            if type(number) not in (int, float):
                _fail()
            try:
                number = _coordinate(number)
            except OverflowError:
                _fail()
            if not math.isfinite(number) or fractions and not 0 <= number <= 1:
                _fail()
            result.append(number)
        if (not result[0] < result[2] or not result[1] < result[3]
                or not all(math.isfinite(result[i + 2] - result[i]) for i in (0, 1))):
            _fail()
        return result

    page = payload["page_geometry"]
    if (type(page) is not dict or set(page) != {"display_rect_points", "cropbox_points", "rotation_degrees"}
            or type(page["rotation_degrees"]) is not int or page["rotation_degrees"] not in (0, 90, 180, 270)):
        _fail()
    display = rectangle(page["display_rect_points"])
    cropbox = rectangle(page["cropbox_points"])
    size = cropbox[2] - cropbox[0], cropbox[3] - cropbox[1]
    if page["rotation_degrees"] in (90, 270):
        size = size[::-1]
    if any(not math.isclose(display[index + 2] - display[index], size[index], rel_tol=1e-6, abs_tol=1e-6)
           for index in (0, 1)):
        _fail()
    expected = {"source_sha256": source_sha256, "page_count": page_count, "page_number": payload["page_number"],
                "coordinate_system": "original_page_display_fraction", "bbox": rectangle(payload["bbox"], fractions=True),
                "page_geometry": {"display_rect_points": display, "cropbox_points": cropbox,
                                  "rotation_degrees": page["rotation_degrees"]}}
    expected["scope_sha256"] = _digest(expected)
    if _bytes(expected, MAX_REFERENCE_BYTES) != _bytes(payload, MAX_REFERENCE_BYTES):
        _fail()
    return expected


def crop_scope(report, *, region_id):
    """Return a detached nominal physical scope, independent of DPI/recipe.

    Matching uses exact normalized declared coordinates, not overlap/tolerance.
    Missing crop IDs and whole-page reports cannot author a crop reference.
    """
    operation, report = _report(report)
    row = _row(report, operation, region_id)
    if row is None:
        _fail()
    return _scope(report, row)


def _recipe(operation, row):
    return None if operation == "regions" else row["recipe"]


def _reference(report, operation, row, report_digest, reference, critical_tokens):
    _hex(report_digest)
    if type(reference) is not str or type(critical_tokens) is not list:
        _fail()
    # Shared validation only: this does not score a fabricated blank candidate.
    try:
        ocr_evaluation._validate({"schema_version": 1, "records": [{
            "id": "reference-validation", "reference": reference, "prediction": "",
            "critical_tokens": critical_tokens}]})
    except (ValueError, TypeError, OverflowError, RecursionError):
        _fail()
    scope = _scope(report, row)
    anchor = {"report_sha256": report_digest, "region_id": row["region_id"],
              "record_sha256": _digest(row, MAX_REPORT_BYTES), "operation": operation,
              "recipe_sha256": _digest(_recipe(operation, row))}
    # Identical rectangles in two distinct planned rows are different authored
    # occurrences. Editing text keeps the occurrence ID but changes its digest.
    reference_id = "crop-" + _digest({"scope": scope, "anchor": anchor})
    payload = {"schema_version": 1, "kind": "ocr_crop_reference", "reference_id": reference_id,
               "scope": scope, "anchor": anchor, "reference": reference,
               "critical_tokens": list(critical_tokens), "review": "operator_checked_requested_crop"}
    payload["reference_sha256"] = _digest(payload)
    return json.loads(_bytes(payload, MAX_REFERENCE_BYTES))


def build_crop_reference(anchor_report, *, anchor_report_sha256, anchor_region_id,
                         reference, critical_tokens, confirmed):
    """Bind operator-reviewed transcription to one requested crop occurrence.

    Never prefill this transcription from OCR or immutable saved page context.
    Empty reviewed references are allowed; their error rates remain undefined
    while insertion counts are meaningful under the existing scorer.
    """
    if confirmed is not True or type(confirmed) is not bool:
        _fail()
    _hex(anchor_report_sha256)
    operation, report = _report(anchor_report)
    row = _row(report, operation, anchor_region_id)
    if row is None:
        _fail()
    return _reference(report, operation, row, anchor_report_sha256, reference, critical_tokens)


def validate_crop_reference(payload, *, anchor_report, anchor_report_sha256):
    """Rebuild exact occurrence, raw transcription revision and ordered tokens."""
    _bytes(payload, MAX_REFERENCE_BYTES)
    if (type(payload) is not dict or set(payload) != _REFERENCE_KEYS
            or type(payload["schema_version"]) is not int or payload["schema_version"] != 1
            or payload["kind"] != "ocr_crop_reference" or type(payload["anchor"]) is not dict
            or "region_id" not in payload["anchor"]):
        _fail()
    expected = build_crop_reference(anchor_report, anchor_report_sha256=anchor_report_sha256,
        anchor_region_id=payload["anchor"]["region_id"], reference=payload["reference"],
        critical_tokens=payload["critical_tokens"], confirmed=True)
    if _bytes(payload, MAX_REFERENCE_BYTES) != _bytes(expected, MAX_REFERENCE_BYTES):
        _fail()
    return expected


def _status(operation, row):
    if operation == "pages":
        return "whole_page_unsupported"
    if row is None:
        return "region_missing"
    return "candidate_available" if row["status"] == "review_required" else row["status"]


def _occurrence(operation, row, identifier, report_digest):
    return {"report_sha256": report_digest, "region_id": identifier, "status": _status(operation, row),
            "record_sha256": None if row is None else _digest(row, MAX_REPORT_BYTES),
            "candidate_sha256": None if row is None or row["candidate"] is None else
            _digest(row["candidate"], MAX_REPORT_BYTES)}


def _edges(row):
    if row is None:
        return None
    geometry = row["geometry"]
    width, height = (geometry["raster"][key] for key in ("width", "height"))
    a, b = geometry["crop_to_page_fraction"]
    edge = [a[2], b[2], a[0] * width + a[1] * height + a[2],
            b[0] * width + b[1] * height + b[2]]
    if any(not math.isfinite(value) for value in edge):
        _fail()
    physical = [min(1., max(0., value)) for value in edge]
    difference = [value - requested for value, requested in zip(physical, row["bbox"])]
    return {"dpi": geometry["raster"]["dpi"], "raster_dimensions": {"width": width, "height": height},
            "pixel_bounds": list(geometry["pixel_bounds"]), "raster_edge_bbox": edge,
            "physical_edge_bbox": physical, "difference_from_requested": difference,
            "boundary_expansion": any(value < -_EDGE_TOLERANCE if index < 2 else value > _EDGE_TOLERANCE
                                      for index, value in enumerate(difference)),
            "edge_scope_differs": any(abs(value) > _EDGE_TOLERANCE for value in difference),
            "boundary_policy": geometry["boundary_policy"]}


def _settings(operation, report, row):
    return {"operation": operation, "dpi": report["retry_configuration"]["dpi"],
            "recipe": None if row is None or operation != "hardscan" else dict(row["recipe"])}


def _differences(settings):
    before, after = settings["baseline"], settings["retry"]
    result = [key for key in ("operation", "dpi") if before[key] != after[key]]
    if _bytes(before["recipe"], MAX_REFERENCE_BYTES) != _bytes(after["recipe"], MAX_REFERENCE_BYTES):
        result.append("recipe")
    return result


def compare_crop_candidates(baseline_report, retry_report, crop_reference, *, baseline_report_sha256,
                            retry_report_sha256, baseline_region_id, retry_region_id):
    """Compare one explicit candidate pair against one baseline-anchored crop.

    Different resolution and hard-scan recipes are allowed on the same requested
    physical scope. Rounded edge differences are disclosed; extra neighbouring
    text still counts as insertion, never removed from predictions. Critical
    occurrences are exact token-sequence counts, not localized/contextual proof.
    No candidate/reference text is included in the result.
    """
    _hex(baseline_report_sha256)
    _hex(retry_report_sha256)
    _id(baseline_region_id)
    _id(retry_region_id)
    before_operation, before = _report(baseline_report)
    after_operation, after = _report(retry_report)
    if (before["source_sha256"] != after["source_sha256"] or before["page_count"] != after["page_count"]):
        _fail()
    rows = [_row(before, before_operation, baseline_region_id), _row(after, after_operation, retry_region_id)]
    scopes = [None if row is None else _scope(report, row) for report, row in zip((before, after), rows)]
    if all(scope is not None for scope in scopes) and scopes[0] != scopes[1]:
        _fail()
    reference = None
    if crop_reference is not None:
        reference = validate_crop_reference(crop_reference, anchor_report=before,
                                            anchor_report_sha256=baseline_report_sha256)
        if reference["anchor"]["region_id"] != baseline_region_id or reference["scope"] != scopes[0]:
            _fail()
    operations = before_operation, after_operation
    statuses = [_status(operation, row) for operation, row in zip(operations, rows)]
    paired = all(status in _AVAILABLE for status in statuses)
    reason = ("whole_page_unsupported" if "whole_page_unsupported" in statuses else
              "region_missing" if "region_missing" in statuses else
              "candidate_unavailable" if not paired else "reference_required" if reference is None else None)
    comparison = None
    if reason is None:
        common = {"id": reference["reference_id"], "reference": reference["reference"],
                  "critical_tokens": reference["critical_tokens"]}
        inputs = [{"schema_version": 1, "records": [{**common, "prediction": row["candidate"]["text"]}]}
                  for row in rows]
        try:
            comparison = compare_ocr(*inputs)
        except ValueError:
            # Full report/reference schemas already passed. A scorer text/work
            # limit leaves the real candidates available, not blank or failed.
            reason = "metric_limit"
    settings = {key: _settings(operation, report, row) for key, operation, report, row in
                zip(("baseline", "retry"), operations, (before, after), rows)}
    edges = {key: _edges(row) for key, row in zip(("baseline", "retry"), rows)}
    warnings = []
    if any(edge is not None and edge["edge_scope_differs"] for edge in edges.values()):
        warnings.append("requested_and_effective_edges_differ")
    if all(edge is not None for edge in edges.values()) and any(
            abs(a - b) > _EDGE_TOLERANCE for a, b in zip(edges["baseline"]["physical_edge_bbox"],
                                                       edges["retry"]["physical_edge_bbox"])):
        warnings.append("effective_edges_differ_between_runs")
    differences = _differences(settings)
    if "dpi" in differences:
        warnings.append("resolution_differs")
    if "recipe" in differences or "operation" in differences:
        warnings.append("recipe_or_route_differs")
    engines = [None if row is None or row["candidate"] is None else row["candidate"]["engine"] for row in rows]
    engine_differs = all(engine is not None for engine in engines) and _bytes(engines[0], MAX_REFERENCE_BYTES) != _bytes(engines[1], MAX_REFERENCE_BYTES)
    libraries = [row.get("processing", {}).get("libraries") if row is not None and row.get("processing") else None for row in rows]
    libraries_differs = all(value is not None for value in libraries) and _bytes(libraries[0], MAX_REFERENCE_BYTES) != _bytes(libraries[1], MAX_REFERENCE_BYTES)
    result = {"schema_version": 1, "kind": "ocr_crop_comparison", "source_sha256": before["source_sha256"],
        "page_count": before["page_count"], "scope": scopes[0] or scopes[1],
        "reference_id": None if reference is None else reference["reference_id"],
        "reference_sha256": None if reference is None else reference["reference_sha256"],
        "pair": {key: _occurrence(operation, row, identifier, digest) for key, operation, row, identifier, digest in
                 zip(("baseline", "retry"), operations, rows, (baseline_region_id, retry_region_id),
                     (baseline_report_sha256, retry_report_sha256))},
        "coverage": {"requested_pairs": 1, "scope_matched": all(scope is not None for scope in scopes),
                     "paired_candidates": int(paired), "reference_occurrences": int(reference is not None),
                     "scored_pairs": int(comparison is not None)},
        "status": "compared" if comparison is not None else "unavailable", "reason": reason,
        "comparison": comparison, "run_settings": settings, "setting_differences": differences,
        "edge_scopes": edges, "warnings": sorted(warnings),
        "confounders": {"engine_settings_differ": bool(engine_differs),
                        "processing_libraries_differ": bool(libraries_differs),
                        "saved_context_differs": before.get("recovery_sha256") != after.get("recovery_sha256")},
        "requires_attention": True, "acceptance": "manual_review_required", "canonical_extraction_modified": False,
        "reference_attestation": "operator supplied; declared source and crop occurrence match, authenticity not verified",
        "scope_notice": "one requested physical crop only; extra edge text remains scored; not whole-page accuracy or causal attribution",
        "critical_notice": "exact token-sequence occurrence counts, not localized or contextual correctness"}
    return json.loads(_bytes(result, MAX_RESULT_BYTES))
