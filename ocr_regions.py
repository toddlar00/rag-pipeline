"""Source-bound, independent region OCR candidates with explicit page geometry.

Region reports are not page recovery reports and never replace or concatenate
source text. A saved recovery generation provides preserved page context only.
"""

from __future__ import annotations

import copy
import json
import math
import os
from pathlib import Path
import re

import artifact_io
from evaluation_inputs import _hex_digest, _read_snapshot
import ocr_recovery
from ocr_recovery_comparison import _load, validate_recovery_report
from resource_lease import PathLease
import storage_policy


MAX_REGIONS = 20
MAX_PIXELS = 25_000_000
MAX_SIDE = 6000
MAX_TOTAL_PIXELS = 100_000_000
MAX_RECOVERY_BYTES = 64 * 1024 * 1024
MAX_PLAN_BYTES = 1024 * 1024
MAX_REPORT_BYTES = 128 * 1024 * 1024
_REGION_ID = re.compile(r"[A-Za-z0-9][A-Za-z0-9._-]{0,63}")
_COORDINATES = "original_page_display_fraction"
_SCOPE = "independent region candidates only; saved page context is not a region reference or a replacement"


def _number(value: object) -> float:
    if type(value) not in (int, float):
        raise ValueError("region geometry requires finite numbers")
    try:
        value = float(value)
    except (OverflowError, TypeError, ValueError):
        raise ValueError("region geometry requires finite numbers") from None
    if not math.isfinite(value):
        raise ValueError("region geometry requires finite numbers")
    return value


def _fields(value: object, fields: set[str]) -> dict:
    if not isinstance(value, dict) or set(value) != fields:
        raise ValueError("invalid region artifact fields")
    return value


def _dpi(value: object) -> int:
    if type(value) is not int or value not in (300, 400):
        raise ValueError("region DPI must be 300 or 400")
    return value


def validate_region_plan(payload: object, *, source_sha256: str,
                         recovery_sha256: str, page_count: int) -> dict:
    """Detach one explicit source/recovery-bound plan in original display space."""
    _hex_digest(source_sha256, label="region source digest")
    _hex_digest(recovery_sha256, label="region recovery digest")
    if type(page_count) is not int or not 1 <= page_count <= 5000:
        raise ValueError("invalid region PDF page count")
    plan = _fields(payload, {"schema_version", "source_sha256", "recovery_sha256",
                             "coordinate_system", "regions"})
    if type(plan["schema_version"]) is not int or plan["schema_version"] != 1:
        raise ValueError("unsupported region plan version")
    for key, expected in (("source_sha256", source_sha256), ("recovery_sha256", recovery_sha256)):
        _hex_digest(plan[key], label="region plan digest")
        if plan[key] != expected:
            raise ValueError("region plan belongs to a different source or recovery")
    if plan["coordinate_system"] != _COORDINATES:
        raise ValueError("region plan requires original page-display fractions")
    regions = plan["regions"]
    if not isinstance(regions, list) or not 1 <= len(regions) <= MAX_REGIONS:
        raise ValueError("region plan requires 1..20 regions")
    result, seen = [], set()
    for region in regions:
        region = _fields(region, {"region_id", "page_number", "bbox"})
        identifier, number, bbox = region["region_id"], region["page_number"], region["bbox"]
        if (not isinstance(identifier, str) or _REGION_ID.fullmatch(identifier) is None
                or identifier in seen):
            raise ValueError("region IDs must be unique bounded ASCII identifiers")
        if type(number) is not int or not 1 <= number <= page_count:
            raise ValueError("region page is outside the PDF")
        if not isinstance(bbox, list) or len(bbox) != 4:
            raise ValueError("region requires four page-display fractions")
        bounds = [_number(value) for value in bbox]
        if (any(not 0 <= value <= 1 for value in bounds)
                or not bounds[0] < bounds[2] or not bounds[1] < bounds[3]):
            raise ValueError("region fractions must be ordered within the page")
        seen.add(identifier)
        result.append({"region_id": identifier, "page_number": number, "bbox": bounds})
    return {"schema_version": 1, "source_sha256": source_sha256,
            "recovery_sha256": recovery_sha256, "coordinate_system": _COORDINATES,
            "regions": sorted(result, key=lambda region: (region["page_number"], region["region_id"]))}


def _validate_geometry(payload: object, bbox: list[float], *, dpi: int) -> dict:
    geometry = _fields(payload, {"page", "raster", "pixel_bounds", "crop_to_page_fraction", "boundary_policy"})
    page = _fields(geometry["page"], {"display_rect_points", "cropbox_points", "rotation_degrees"})
    rectangles = []
    for key in ("display_rect_points", "cropbox_points"):
        raw = page[key]
        if not isinstance(raw, list) or len(raw) != 4:
            raise ValueError("invalid region page rectangle")
        rect = [_number(value) for value in raw]
        if rect[0] >= rect[2] or rect[1] >= rect[3]:
            raise ValueError("invalid region page rectangle")
        rectangles.append(rect)
    rect, cropbox = rectangles
    rotation = page["rotation_degrees"]
    if type(rotation) is not int or rotation not in (0, 90, 180, 270):
        raise ValueError("invalid intrinsic PDF rotation")
    width, height = rect[2] - rect[0], rect[3] - rect[1]
    crop_width, crop_height = cropbox[2] - cropbox[0], cropbox[3] - cropbox[1]
    if rotation in (90, 270):
        crop_width, crop_height = crop_height, crop_width
    if not (math.isclose(width, crop_width, rel_tol=1e-6, abs_tol=1e-6)
            and math.isclose(height, crop_height, rel_tol=1e-6, abs_tol=1e-6)):
        raise ValueError("region page display dimensions disagree with cropbox and rotation")
    raster = _fields(geometry["raster"], {"width", "height", "dpi", "coordinate_system"})
    if (raster["dpi"] != dpi or type(raster["dpi"]) is not int
            or raster["coordinate_system"] != "rendered_image_pixels"
            or any(type(raster[key]) is not int or not 1 <= raster[key] <= MAX_SIDE
                   for key in ("width", "height"))
            or raster["width"] * raster["height"] > MAX_PIXELS):
        raise ValueError("region raster exceeds its declared budget")
    pixels = geometry["pixel_bounds"]
    if (not isinstance(pixels, list) or len(pixels) != 4
            or any(type(value) is not int for value in pixels)
            or pixels[2] - pixels[0] != raster["width"]
            or pixels[3] - pixels[1] != raster["height"]):
        raise ValueError("invalid region raster origin")
    for value in pixels:
        _number(value)
    scale = dpi / 72
    expected = [(rect[0] + bbox[0] * width) * scale, (rect[1] + bbox[1] * height) * scale,
                (rect[0] + bbox[2] * width) * scale, (rect[1] + bbox[3] * height) * scale]
    # MuPDF rounds outward with a small near-integer tolerance. At most one
    # boundary pixel may lie outside the requested continuous rectangle.
    if any(not math.isfinite(value) for value in expected) or any(
        not (-1.001 <= pixel - value <= 0.001 if index < 2
             else -0.001 <= pixel - value <= 1.001)
        for index, (pixel, value) in enumerate(zip(pixels, expected))
    ):
        raise ValueError("region crop does not match requested page fractions")
    matrix = geometry["crop_to_page_fraction"]
    expected_matrix = [[1 / (scale * width), 0.0, (pixels[0] / scale - rect[0]) / width],
                       [0.0, 1 / (scale * height), (pixels[1] / scale - rect[1]) / height]]
    if (not isinstance(matrix, list) or len(matrix) != 2
            or any(not isinstance(row, list) or len(row) != 3 for row in matrix)
            or any(not math.isclose(_number(value), target, rel_tol=1e-12, abs_tol=1e-12)
                   for row, expected_row in zip(matrix, expected_matrix)
                   for value, target in zip(row, expected_row))
            or geometry["boundary_policy"] != "clip_to_page_bounds"):
        raise ValueError("invalid crop-to-page coordinate mapping")
    return copy.deepcopy(geometry)


def _page_boxes(candidate: dict, geometry: dict) -> list:
    matrix = geometry["crop_to_page_fraction"]
    return [[[max(0.0, min(1.0, row[0] * x + row[1] * y + row[2])) for row in matrix]
             for x, y in line["box"]] for line in candidate["lines"]]


def _context(recovery: dict, numbers: set[int]) -> list[dict]:
    selected = {page["page_number"]: page for page in recovery["pages"]}
    deferred = {page["page_number"] for page in recovery["deferred_pages"]}
    return [{"page_number": number,
             "status": selected[number]["status"] if number in selected else "deferred" if number in deferred else "not_selected",
             "recovery_page": copy.deepcopy(selected.get(number))}
            for number in sorted(numbers)]


def _describe_regions(reader, recovery: dict, plan: dict, *, dpi: int) -> tuple[list, int]:
    if type(reader.page_count) is not int or reader.page_count != recovery["page_count"]:
        raise ValueError("region PDF page count differs from saved recovery")
    geometries = [_validate_geometry(reader.describe_region(region["page_number"], region["bbox"]),
                                    region["bbox"], dpi=dpi) for region in plan["regions"]]
    total = sum(item["raster"]["width"] * item["raster"]["height"] for item in geometries)
    if total > MAX_TOTAL_PIXELS:
        raise ValueError("region plan exceeds aggregate raster budget")
    return geometries, total


def _retry_region(reader, region: dict, geometry: dict, *, dpi: int) -> dict:
    record = {**copy.deepcopy(region), "geometry": geometry}
    try:
        result = reader.retry_region(region["page_number"], region["bbox"])
        _fields(result, {"geometry", "candidate"})
        if result["geometry"] != geometry:
            raise ValueError("region geometry changed after preflight")
        candidate = ocr_recovery._candidate_snapshot(result["candidate"])
        ocr_recovery._validate_candidate_policy(candidate, ocr_recovery.RetryPolicy(dpi=dpi))
        if candidate["raster"] != geometry["raster"] or candidate["engine"]["min_score"] != 0.0:
            raise ValueError("region candidate disagrees with crop geometry")
    except Exception as error:
        record.update(status="retry_failed", candidate=None, page_boxes=None,
                      error_code="retry_limit_or_validation" if isinstance(error, ValueError)
                      else "retry_runtime_unavailable" if isinstance(error, ImportError)
                      else "retry_runtime_failed")
    else:
        record.update(status="review_required" if candidate["text"].strip() else "empty_candidate",
                      candidate=candidate, page_boxes=_page_boxes(candidate, geometry), error_code=None)
    return record


def _assemble_region_review(recovery: dict, plan: dict, regions: list[dict],
                            total: int, *, dpi: int) -> dict:
    return {
        "schema_version": 1, "kind": "ocr_region_review",
        "source_sha256": recovery["source_sha256"], "recovery_sha256": plan["recovery_sha256"],
        "page_count": recovery["page_count"], "coordinate_system": _COORDINATES, "plan": plan,
        "retry_configuration": {"dpi": dpi, "max_regions": MAX_REGIONS, "max_pixels": MAX_PIXELS,
                                "max_side": MAX_SIDE, "max_total_pixels": MAX_TOTAL_PIXELS,
                                "attempts_per_region": 1, "min_score": 0.0, "preprocessing": "none"},
        "page_contexts": _context(recovery, {region["page_number"] for region in regions}),
        "regions": regions,
        "summary": {"requested": len(regions), "planned_pixels": total,
                    **{key: sum(region["status"] == status for region in regions)
                       for key, status in (("candidates", "review_required"), ("empty", "empty_candidate"),
                                           ("failed", "retry_failed"))}},
        "requires_attention": True, "acceptance": "manual_review_required",
        "canonical_extraction_modified": False, "recognition_rerun": True,
        "scope": _SCOPE,
    }



def _build(reader, recovery: dict, plan: dict, *, dpi: int) -> dict:
    geometries, total = _describe_regions(reader, recovery, plan, dpi=dpi)
    outcomes = []
    for region, geometry in zip(plan["regions"], geometries):
        outcomes.append(_retry_region(reader, region, geometry, dpi=dpi))
    return _assemble_region_review(recovery, plan, outcomes, total, dpi=dpi)


def _validate_region_outcome(region: dict, geometry: dict, *, dpi: int) -> None:
    """Validate only outcome payload; callers bind identity/geometry first."""
    if region["status"] == "retry_failed":
        if (region["candidate"] is not None or region["page_boxes"] is not None
                or not isinstance(region["error_code"], str)
                or region["error_code"] not in {"retry_limit_or_validation", "retry_runtime_unavailable", "retry_runtime_failed"}):
            raise ValueError("invalid failed region candidate")
    elif region["status"] in {"review_required", "empty_candidate"}:
        candidate = ocr_recovery._candidate_snapshot(region["candidate"])
        ocr_recovery._validate_candidate_policy(candidate, ocr_recovery.RetryPolicy(dpi=dpi))
        if (region["error_code"] is not None or candidate["raster"] != geometry["raster"]
                or candidate["engine"]["min_score"] != 0.0
                or bool(candidate["text"].strip()) != (region["status"] == "review_required")
                or region["page_boxes"] != _page_boxes(candidate, geometry)):
            raise ValueError("region candidate or mapped boxes disagree")
        for box in region["page_boxes"]:
            for point in box:
                for value in point:
                    _number(value)
    else:
        raise ValueError("unsupported region candidate status")


def validate_region_review(payload: object) -> dict:
    """Validate a new region report without admitting it as page recovery data."""
    report = _fields(payload, {
        "schema_version", "kind", "source_sha256", "recovery_sha256", "page_count",
        "coordinate_system", "plan", "retry_configuration", "page_contexts", "regions",
        "summary", "requires_attention", "acceptance", "canonical_extraction_modified",
        "recognition_rerun", "scope", "inputs",
    })
    if (type(report["schema_version"]) is not int or report["schema_version"] != 1
            or report["kind"] != "ocr_region_review"
            or report["coordinate_system"] != _COORDINATES
            or report["requires_attention"] is not True or report["recognition_rerun"] is not True
            or report["canonical_extraction_modified"] is not False
            or report["acceptance"] != "manual_review_required" or report["scope"] != _SCOPE):
        raise ValueError("invalid region report contract")
    plan = validate_region_plan(report["plan"], source_sha256=report["source_sha256"],
                                recovery_sha256=report["recovery_sha256"], page_count=report["page_count"])
    if report["plan"] != plan:
        raise ValueError("region plan order is not canonical")
    config = _fields(report["retry_configuration"], {
        "dpi", "max_regions", "max_pixels", "max_side", "max_total_pixels",
        "attempts_per_region", "min_score", "preprocessing",
    })
    dpi = _dpi(config["dpi"])
    expected = {"max_regions": MAX_REGIONS, "max_pixels": MAX_PIXELS, "max_side": MAX_SIDE,
                "max_total_pixels": MAX_TOTAL_PIXELS, "attempts_per_region": 1,
                "min_score": 0.0, "preprocessing": "none"}
    if any(type(config[key]) is not type(value) or config[key] != value for key, value in expected.items()):
        raise ValueError("unsupported region configuration")
    inputs = _fields(report["inputs"], {"pdf_sha256", "recovery_sha256", "plan_sha256"})
    for value in inputs.values():
        _hex_digest(value, label="region input digest")
    if inputs["pdf_sha256"] != report["source_sha256"] or inputs["recovery_sha256"] != report["recovery_sha256"]:
        raise ValueError("region input digests disagree")
    regions = report["regions"]
    if not isinstance(regions, list) or len(regions) != len(plan["regions"]):
        raise ValueError("region results do not cover the plan")
    total = 0
    for region, requested in zip(regions, plan["regions"]):
        _fields(region, {"region_id", "page_number", "bbox", "geometry", "status",
                         "candidate", "page_boxes", "error_code"})
        if (type(region["page_number"]) is not int
                or not isinstance(region["bbox"], list)
                or any(region[key] != requested[key] for key in requested)):
            raise ValueError("region result differs from its plan")
        for value in region["bbox"]:
            _number(value)
        if not isinstance(region["status"], str):
            raise ValueError("invalid region status")
        geometry = _validate_geometry(region["geometry"], requested["bbox"], dpi=dpi)
        total += geometry["raster"]["width"] * geometry["raster"]["height"]
        if total > MAX_TOTAL_PIXELS:
            raise ValueError("region report exceeds aggregate pixel budget")
        _validate_region_outcome(region, geometry, dpi=dpi)
    summary = _fields(report["summary"], {"requested", "planned_pixels", "candidates", "empty", "failed"})
    expected = {"requested": len(regions), "planned_pixels": total,
                **{key: sum(region["status"] == status for region in regions)
                   for key, status in (("candidates", "review_required"), ("empty", "empty_candidate"),
                                       ("failed", "retry_failed"))}}
    if any(type(summary[key]) is not int or summary[key] != value for key, value in expected.items()):
        raise ValueError("region summary disagrees with results")
    contexts = report["page_contexts"]
    numbers = sorted({region["page_number"] for region in regions})
    if not isinstance(contexts, list) or len(contexts) != len(numbers):
        raise ValueError("region page contexts do not cover requested pages")
    for context, number in zip(contexts, numbers):
        _fields(context, {"page_number", "status", "recovery_page"})
        if type(context["page_number"]) is not int or context["page_number"] != number:
            raise ValueError("invalid region page context")
        if not isinstance(context["status"], str):
            raise ValueError("invalid region page context status")
        page = context["recovery_page"]
        if context["status"] in {"deferred", "not_selected"}:
            if page is not None:
                raise ValueError("unavailable page context has fabricated text")
        elif context["status"] in {"review_required", "empty_candidate", "retry_failed"}:
            from ocr_recovery_comparison import _PAGE_FIELDS, _reasons

            _fields(page, _PAGE_FIELDS)
            if (type(page["page_number"]) is not int or page["page_number"] != number
                    or page["status"] != context["status"]):
                raise ValueError("page context does not match its recovery page")
            original = page["original_text"]
            if original is None:
                if page["original_error"] != "native_extraction_failed":
                    raise ValueError("missing original page context error")
            else:
                ocr_recovery._page_text(original)
                if page["original_error"] is not None:
                    raise ValueError("page context text contradicts extraction error")
            ocr_recovery.validate_evidence({
                "schema_version": 1, "source_sha256": report["source_sha256"],
                "pages": [{"page_number": number, "text": original or "",
                           "low_grade": page["low_grade"], "ocr_confidence": page["original_ocr_confidence"]}],
            })
            if page["baseline_kind"] == "pdf_native":
                if page["low_grade"] is not None or page["original_ocr_confidence"] is not None:
                    raise ValueError("native page context has unsupported evidence scores")
            elif page["baseline_kind"] == "operator_evidence":
                if original is None:
                    raise ValueError("operator page context lacks text")
            else:
                raise ValueError("unsupported page context baseline")
            if not isinstance(page["reasons"], list):
                raise ValueError("invalid page context reasons")
            _reasons(page["reasons"], requested="requested" in page["reasons"])
            if page["status"] == "retry_failed":
                if (page["candidate"] is not None or page["error_code"] not in (
                        "retry_limit_or_validation", "retry_runtime_unavailable", "retry_runtime_failed")):
                    raise ValueError("failed page context has a candidate")
            else:
                candidate = page["candidate"]
                metadata = candidate.get("preprocessing") if isinstance(candidate, dict) else None
                preprocessing = metadata.get("mode", "none") if isinstance(metadata, dict) else "none"
                checked = ocr_recovery._candidate_snapshot(candidate, preprocessing=preprocessing)
                if page["error_code"] is not None or bool(checked["text"].strip()) != (page["status"] == "review_required"):
                    raise ValueError("page context status disagrees with its candidate")
        else:
            raise ValueError("unsupported region page context")
    return copy.deepcopy(report)


def load_region_review(path: Path) -> dict:
    payload, _ = _load(path, label="OCR region report", limit=MAX_REPORT_BYTES)
    return validate_region_review(payload)


def verify_region_review_request(payload: object, *, pdf_path: Path, recovery_path: Path,
                                 plan_path: Path, dpi: int) -> dict:
    """Bind a completed worker report to current inputs without opening a PDF parser."""
    dpi = _dpi(dpi)
    report = validate_region_review(payload)
    if report["retry_configuration"]["dpi"] != dpi:
        raise ValueError("region report differs from requested DPI")
    pdf_path, recovery_path, plan_path = [Path(path).absolute()
                                          for path in (pdf_path, recovery_path, plan_path)]
    for path in (pdf_path, recovery_path, plan_path):
        storage_policy.assert_no_link_components(path)
    recovery, recovery_digest = _load(recovery_path, label="OCR recovery report", limit=MAX_RECOVERY_BYTES)
    recovery = validate_recovery_report(recovery)
    plan, plan_digest = _load(plan_path, label="OCR region plan", limit=MAX_PLAN_BYTES)
    plan = validate_region_plan(plan, source_sha256=recovery["source_sha256"],
                                recovery_sha256=recovery_digest, page_count=recovery["page_count"])
    if (report["inputs"] != {"pdf_sha256": recovery["source_sha256"],
                            "recovery_sha256": recovery_digest, "plan_sha256": plan_digest}
            or report["page_count"] != recovery["page_count"] or report["plan"] != plan
            or report["page_contexts"] != _context(recovery, {item["page_number"] for item in plan["regions"]})):
        raise ValueError("region report differs from requested input generations")
    artifact_io.hash_file_generation(pdf_path, expected_sha256=report["source_sha256"])
    for path, digest, limit in ((recovery_path, recovery_digest, MAX_RECOVERY_BYTES),
                                (plan_path, plan_digest, MAX_PLAN_BYTES)):
        _, current = _read_snapshot(path, label="OCR region input", max_bytes=limit)
        if current != digest:
            raise RuntimeError("region input changed while checking worker report")
    return report


def _check_report_size(result: dict) -> None:
    size = 1  # private JSON writer adds a final newline
    for chunk in json.JSONEncoder(ensure_ascii=False, sort_keys=True, indent=2, allow_nan=False).iterencode(result):
        size += len(chunk.encode("utf-8"))
        if size > MAX_REPORT_BYTES:
            raise ValueError("region report exceeds output byte budget")


def recover_regions(pdf_path: Path, recovery_path: Path, plan_path: Path, output_path: Path, *,
                    dpi: int = 300, reader_factory=None) -> dict:
    """Create a private, immutable-source-bound region review without changing old reports."""
    dpi = _dpi(dpi)
    paths = [Path(path).absolute() for path in (pdf_path, recovery_path, plan_path, output_path)]
    for path in paths:
        storage_policy.assert_no_link_components(path)
    for index, path in enumerate(paths):
        for other in paths[index + 1:]:
            if path.resolve() == other.resolve() or (path.exists() and other.exists() and os.path.samefile(path, other)):
                raise ValueError("region inputs and output must be distinct")
    pdf_path, recovery_path, plan_path, output_path = paths
    with PathLease(output_path, backend="ocr-regions", collection_name="report", operation="retry",
                   timeout=0, resource_description="OCR region review report"):
        if output_path.exists():
            raise FileExistsError("region output already exists")
        recovery, recovery_digest = _load(recovery_path, label="OCR recovery report", limit=MAX_RECOVERY_BYTES)
        recovery = validate_recovery_report(recovery)
        plan, plan_digest = _load(plan_path, label="OCR region plan", limit=MAX_PLAN_BYTES)
        plan = validate_region_plan(plan, source_sha256=recovery["source_sha256"],
                                    recovery_sha256=recovery_digest, page_count=recovery["page_count"])
        if reader_factory is None:
            from ocr_region_runtime import RapidOCRRegionReader

            reader_factory = RapidOCRRegionReader
        with artifact_io.immutable_file_snapshot(
                pdf_path, snapshot_name="source.pdf", expected_sha256=recovery["source_sha256"]) as snapshot:
            with reader_factory(snapshot.path, dpi=dpi, max_pixels=MAX_PIXELS, max_side=MAX_SIDE) as reader:
                result = _build(reader, recovery, plan, dpi=dpi)
            result["inputs"] = {"pdf_sha256": snapshot.sha256, "recovery_sha256": recovery_digest,
                                "plan_sha256": plan_digest}
            result = validate_region_review(result)
            _check_report_size(result)

            def commit(temporary, destination):
                snapshot.verify()
                storage_policy.assert_no_link_components(pdf_path)
                artifact_io.hash_file_generation(pdf_path, expected_sha256=snapshot.sha256)
                for path, digest, limit in ((recovery_path, recovery_digest, MAX_RECOVERY_BYTES),
                                            (plan_path, plan_digest, MAX_PLAN_BYTES)):
                    _, current = _read_snapshot(path, label="OCR region input", max_bytes=limit)
                    if current != digest:
                        raise RuntimeError("region input changed before publication")
                ocr_recovery._publish_new_report(temporary, destination)

            storage_policy.atomic_write_private_json(
                output_path, result, indent=2, replace_fn=commit)
    return result
