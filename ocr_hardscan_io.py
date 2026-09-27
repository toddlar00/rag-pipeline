"""Create-only private hard-scan review reports with exact input generation binding."""

from __future__ import annotations

import copy
import json
import os
from pathlib import Path

import artifact_io
from evaluation_inputs import _hex_digest, _read_snapshot
import ocr_hardscan as policy
import ocr_recovery
from ocr_recovery_comparison import _load, validate_recovery_report
from ocr_regions import _dpi, _validate_geometry
from resource_lease import PathLease
import storage_policy


MAX_RECOVERY_BYTES = 64 * 1024 * 1024
MAX_PLAN_BYTES = 1024 * 1024
MAX_REPORT_BYTES = 128 * 1024 * 1024
_SCOPE = "independent hard-scan candidates; saved recovery is context, not region ground truth"
_STATUSES = ("review_required", "empty_candidate", "retry_failed", "abstained")


def _configuration(dpi: int) -> dict:
    return {"dpi": _dpi(dpi), "max_regions": policy.MAX_REGIONS, "max_pixels": policy.MAX_PIXELS,
            "max_side": policy.MAX_SIDE, "max_total_pixels": policy.MAX_TOTAL_PIXELS,
            "max_lines_per_region": policy.MAX_LINES_PER_REGION,
            "max_vertices_per_region": policy.MAX_VERTICES_PER_REGION, "max_total_vertices": policy.MAX_TOTAL_VERTICES,
            "attempts_per_region": 1, "min_score": 0.0,
            "transform_order": ["orientation", "bow", "illumination"], "algorithm": policy.ALGORITHM}


def _candidate(payload: object, transform: dict, *, dpi: int) -> dict:
    if not isinstance(payload, dict) or not isinstance(payload.get("raster"), dict):
        raise ValueError("invalid hard-scan candidate")
    if not isinstance(payload.get("lines"), list) or len(payload["lines"]) > policy.MAX_LINES_PER_REGION:
        raise ValueError("hard-scan candidate exceeds line budget")
    expected = {**transform["processed_raster"], "dpi": dpi, "coordinate_system": "hardscan_image_pixels"}
    if payload["raster"] != expected:
        raise ValueError("hard-scan candidate differs from processed geometry")
    # Validate bounded arrays before the shared validator detaches them. A
    # shallow raster-only adapter avoids copying arbitrary unvalidated boxes.
    checked = dict(payload)
    checked["raster"] = dict(payload["raster"])
    checked["raster"]["coordinate_system"] = "rendered_image_pixels"
    checked = ocr_recovery._candidate_snapshot(checked)
    ocr_recovery._validate_candidate_policy(checked, ocr_recovery.RetryPolicy(dpi=dpi))
    if checked["engine"]["min_score"] != 0.0:
        raise ValueError("hard-scan candidate has an unrequested score threshold")
    checked["raster"]["coordinate_system"] = "hardscan_image_pixels"
    return checked


def _source_polygons(candidate: dict, geometry: dict, transform: dict, *, vertex_budget: int) -> list:
    matrix = geometry["crop_to_page_fraction"]
    original = transform["original_raster"]
    # Outward pixel rounding may place a crop boundary fractionally beyond
    # the physical page. Split at those intersections too, before sampling.
    clip = [max(0., -matrix[0][2]/matrix[0][0]), max(0., -matrix[1][2]/matrix[1][1]),
            min(original["width"], (1-matrix[0][2])/matrix[0][0]),
            min(original["height"], (1-matrix[1][2])/matrix[1][1])]
    results, used = [], 0
    budget = min(policy.MAX_VERTICES_PER_REGION, vertex_budget)
    for line in candidate["lines"]:
        polygon = policy.source_polygon(line["box"], transform, source_clip=clip)
        used += len(polygon)
        if used > budget:
            raise ValueError("hard-scan source polygons exceed vertex budget")
        results.append([[max(0., min(1., row[0]*x+row[1]*y+row[2])) for row in matrix] for x, y in polygon])
    return results


def _planned_pixels(records: list) -> int:
    total = 0
    for record in records:
        raw, transform = record["geometry"]["raster"], record["transform"]
        total += raw["width"]*raw["height"]
        if transform["eligibility"] == "ready":
            output = transform["processed_raster"]
            total += output["width"]*output["height"]
    if total > policy.MAX_TOTAL_PIXELS:
        raise ValueError("hard-scan plan exceeds aggregate raster work budget")
    return total


def _summary(records: list) -> dict:
    return {"requested": len(records), "planned_pixels": _planned_pixels(records),
            **{key: sum(record["status"] == status for record in records)
               for key, status in (("candidates", "review_required"), ("empty", "empty_candidate"),
                                    ("failed", "retry_failed"), ("abstained", "abstained"))}}


def _validate_hardscan_region(record: dict, region: dict, *, dpi: int, vertex_budget: int) -> int:
    """Validate one outcome without copying the full report; return its vertex usage."""
    used_vertices = 0
    policy.fields(record, {"region_id", "page_number", "bbox", "recipe", "geometry", "transform",
                           "status", "candidate", "processing", "source_polygons", "error_code", "abstention_reason"})
    if (any(record[key] != value for key, value in region.items())
            or type(record["page_number"]) is not int or not isinstance(record["bbox"], list)):
        raise ValueError("hard-scan result differs from its requested region")
    for value in record["bbox"]:
        policy.number(value)
    policy.validate_recipe(record["recipe"])
    geometry = _validate_geometry(record["geometry"], region["bbox"], dpi=dpi)
    raster = geometry["raster"]
    transform = policy.validate_transform(record["transform"], width=raster["width"],
                                           height=raster["height"], recipe=region["recipe"])
    status = record["status"]
    if not isinstance(status, str) or status not in _STATUSES:
        raise ValueError("unsupported hard-scan result status")
    if status == "retry_failed":
        if (transform["eligibility"] != "ready" or record["candidate"] is not None
                or record["processing"] is not None or record["source_polygons"] is not None
                or record["abstention_reason"] is not None or record["error_code"] not in
                ("retry_limit_or_validation", "retry_runtime_unavailable", "retry_runtime_failed")):
            raise ValueError("invalid failed hard-scan result")
    elif status == "abstained":
        if record["candidate"] is not None or record["source_polygons"] is not None or record["error_code"] is not None:
            raise ValueError("hard-scan abstention has fabricated OCR data")
        if transform["eligibility"] == "abstained":
            if record["processing"] is not None or record["abstention_reason"] != transform["reason"]:
                raise ValueError("hard-scan geometry abstention disagrees")
        else:
            processing = policy.validate_processing(record["processing"], recipe=region["recipe"])
            if processing["status"] != "abstained" or record["abstention_reason"] != processing["reason"]:
                raise ValueError("hard-scan image abstention disagrees")
    else:
        processing = policy.validate_processing(record["processing"], recipe=region["recipe"])
        if (transform["eligibility"] != "ready" or processing["status"] != "completed"
                or record["error_code"] is not None or record["abstention_reason"] is not None):
            raise ValueError("hard-scan candidate lacks completed processing")
        candidate = _candidate(record["candidate"], transform, dpi=dpi)
        polygons = _source_polygons(candidate, geometry, transform,
                                     vertex_budget=vertex_budget)
        used_vertices += sum(len(polygon) for polygon in polygons)
        if (bool(candidate["text"].strip()) != (status == "review_required")
                or record["source_polygons"] != polygons):
            raise ValueError("hard-scan candidate status or nonlinear mapping disagrees")
        for polygon in record["source_polygons"]:
            for point in polygon:
                for value in point:
                    policy.number(value)
    return used_vertices


def validate_report(payload: object) -> dict:
    report = policy.fields(payload, {
        "schema_version", "kind", "source_sha256", "recovery_sha256", "page_count", "plan",
        "baseline_recovery", "retry_configuration", "regions", "summary", "inputs", "requires_attention",
        "canonical_extraction_modified", "recognition_attempt_workflow", "scope", "acceptance",
        "source_polygon_contract",
    })
    if (type(report["schema_version"]) is not int or report["schema_version"] != 1
            or report["kind"] != "ocr_hardscan_review" or report["requires_attention"] is not True
            or report["canonical_extraction_modified"] is not False or report["recognition_attempt_workflow"] is not True
            or report["scope"] != _SCOPE or report["acceptance"] != "manual_review_required"
            or report["source_polygon_contract"] != {
                "coordinate_system": policy.COORDINATES, "edge_model": "sampled_nonlinear_inverse",
                "max_error_crop_pixels": 0.5, "boundary_policy": "clip_to_source_crop_and_physical_page"}):
        raise ValueError("invalid hard-scan report contract")
    polygon_contract = report["source_polygon_contract"]
    policy.number(polygon_contract["max_error_crop_pixels"])
    recovery = validate_recovery_report(report["baseline_recovery"])
    if (report["source_sha256"] != recovery["source_sha256"]
            or type(report["page_count"]) is not int or report["page_count"] != recovery["page_count"]):
        raise ValueError("hard-scan baseline context differs from source")
    plan = policy.validate_plan(report["plan"], source_sha256=report["source_sha256"],
                                recovery_sha256=report["recovery_sha256"], page_count=report["page_count"])
    if report["plan"] != plan:
        raise ValueError("hard-scan plan order is not canonical")
    config = report["retry_configuration"]
    if not isinstance(config, dict) or "dpi" not in config:
        raise ValueError("invalid hard-scan configuration")
    expected = _configuration(config["dpi"])
    if config != expected or any(type(config[key]) is not type(value) for key, value in expected.items()):
        raise ValueError("unsupported hard-scan configuration")
    inputs = policy.fields(report["inputs"], {"pdf_sha256", "recovery_sha256", "plan_sha256"})
    for digest in inputs.values():
        _hex_digest(digest, label="hard-scan input digest")
    if inputs["pdf_sha256"] != report["source_sha256"] or inputs["recovery_sha256"] != report["recovery_sha256"]:
        raise ValueError("hard-scan input digests disagree")
    records = report["regions"]
    if not isinstance(records, list) or len(records) != len(plan["regions"]):
        raise ValueError("hard-scan results do not cover their complete plan")
    used_vertices = 0
    for record, region in zip(records, plan["regions"]):
        used_vertices += _validate_hardscan_region(
            record, region, dpi=config["dpi"], vertex_budget=policy.MAX_TOTAL_VERTICES-used_vertices)
    summary = _summary(records)
    if (not isinstance(report["summary"], dict) or report["summary"] != summary
            or any(type(report["summary"][key]) is not int for key in summary)):
        raise ValueError("hard-scan summary disagrees with coverage")
    return copy.deepcopy(report)


def _describe_hardscan_regions(reader, recovery: dict, plan: dict, *, dpi: int) -> list:
    if type(reader.page_count) is not int or reader.page_count != recovery["page_count"]:
        raise ValueError("hard-scan PDF page count differs from saved context")
    records = []
    for region in plan["regions"]:
        described = reader.describe_hardscan(region["page_number"], region["bbox"], region["recipe"])
        policy.fields(described, {"geometry", "transform"})
        geometry = _validate_geometry(described["geometry"], region["bbox"], dpi=dpi)
        transform = policy.validate_transform(described["transform"], width=geometry["raster"]["width"],
                                               height=geometry["raster"]["height"], recipe=region["recipe"])
        records.append({**copy.deepcopy(region), "geometry": geometry, "transform": transform})
    _planned_pixels(records)  # Every crop/expanded geometry precedes any render/model call.
    return records


def _retry_hardscan_region(reader, record: dict, *, dpi: int, vertex_budget: int) -> int:
    """Update the already detached preflight record; return its vertex usage."""
    used_vertices = 0
    record.update(candidate=None, processing=None, source_polygons=None, error_code=None, abstention_reason=None)
    if record["transform"]["eligibility"] == "abstained":
        record.update(status="abstained", abstention_reason=record["transform"]["reason"])
        return 0
    try:
        result = reader.retry_hardscan(record["page_number"], record["bbox"], record["recipe"])
        policy.fields(result, {"geometry", "transform", "processing", "candidate"})
        if result["geometry"] != record["geometry"] or result["transform"] != record["transform"]:
            raise ValueError("hard-scan geometry changed after preflight")
        processing = policy.validate_processing(result["processing"], recipe=record["recipe"])
        if processing["status"] == "abstained":
            if result["candidate"] is not None:
                raise ValueError("hard-scan abstention supplied a candidate")
            record.update(status="abstained", processing=processing, abstention_reason=processing["reason"])
        else:
            candidate = _candidate(result["candidate"], record["transform"], dpi=dpi)
            polygons = _source_polygons(candidate, record["geometry"], record["transform"],
                                         vertex_budget=vertex_budget)
            used_vertices += sum(len(polygon) for polygon in polygons)
            record.update(status="review_required" if candidate["text"].strip() else "empty_candidate",
                          candidate=candidate, processing=processing,
                          source_polygons=polygons)
    except Exception as error:
        record.update(status="retry_failed", candidate=None, processing=None, source_polygons=None,
                      abstention_reason=None, error_code="retry_limit_or_validation" if isinstance(error, ValueError)
                      else "retry_runtime_unavailable" if isinstance(error, ImportError) else "retry_runtime_failed")
    return used_vertices


def _assemble_hardscan_review(recovery: dict, plan: dict, records: list, *, dpi: int) -> dict:
    return {"schema_version": 1, "kind": "ocr_hardscan_review", "source_sha256": recovery["source_sha256"],
            "recovery_sha256": plan["recovery_sha256"], "page_count": recovery["page_count"], "plan": plan,
            "baseline_recovery": recovery, "retry_configuration": _configuration(dpi), "regions": records,
            "summary": _summary(records), "requires_attention": True, "canonical_extraction_modified": False,
            "recognition_attempt_workflow": True, "scope": _SCOPE, "acceptance": "manual_review_required",
            "source_polygon_contract": {"coordinate_system": policy.COORDINATES,
                                         "edge_model": "sampled_nonlinear_inverse", "max_error_crop_pixels": 0.5,
                                         "boundary_policy": "clip_to_source_crop_and_physical_page"}}


def _build(reader, recovery: dict, plan: dict, *, dpi: int) -> dict:
    records = _describe_hardscan_regions(reader, recovery, plan, dpi=dpi)
    used_vertices = 0
    for record in records:
        used_vertices += _retry_hardscan_region(
            reader, record, dpi=dpi, vertex_budget=policy.MAX_TOTAL_VERTICES-used_vertices)
    return _assemble_hardscan_review(recovery, plan, records, dpi=dpi)


def _inputs(recovery_path: Path, plan_path: Path) -> tuple:
    recovery, digest = _load(recovery_path, label="hard-scan saved recovery", limit=MAX_RECOVERY_BYTES)
    recovery = validate_recovery_report(recovery)
    plan, plan_digest = _load(plan_path, label="hard-scan plan", limit=MAX_PLAN_BYTES)
    plan = policy.validate_plan(plan, source_sha256=recovery["source_sha256"],
                                recovery_sha256=digest, page_count=recovery["page_count"])
    return recovery, plan, {"pdf_sha256": recovery["source_sha256"], "recovery_sha256": digest, "plan_sha256": plan_digest}


def _recheck(pdf: Path, recovery: Path, plan: Path, inputs: dict) -> None:
    storage_policy.assert_no_link_components(pdf)
    artifact_io.hash_file_generation(pdf, expected_sha256=inputs["pdf_sha256"])
    for path, key, limit in ((recovery, "recovery_sha256", MAX_RECOVERY_BYTES), (plan, "plan_sha256", MAX_PLAN_BYTES)):
        _, digest = _read_snapshot(path, label="hard-scan input", max_bytes=limit)
        if digest != inputs[key]:
            raise RuntimeError("hard-scan input changed before publication")


def load_report(path: Path) -> dict:
    report, _ = _load(path, label="hard-scan review", limit=MAX_REPORT_BYTES)
    return validate_report(report)


def create_plan(recovery_path: Path, output_path: Path, regions: list, *, confirmed: bool = False) -> dict:
    """Bind explicit recipes to a saved report without manual hash copying or OCR.

    Confirmation is a caller declaration, not authenticated human approval.
    This does not inspect the PDF or infer a recipe from its contents.
    """
    if confirmed is not True:
        raise ValueError("hard-scan plan requires explicit operator confirmation")
    recovery_path, output_path = Path(recovery_path).absolute(), Path(output_path).absolute()
    for path in (recovery_path, output_path):
        storage_policy.assert_no_link_components(path)
    if (recovery_path.resolve() == output_path.resolve()
            or output_path.exists() and os.path.samefile(recovery_path, output_path)):
        raise ValueError("hard-scan plan output must differ from saved recovery")
    with PathLease(output_path, backend="ocr-hardscan", collection_name="plan", operation="plan",
                   timeout=0, resource_description="hard-scan recipe plan"):
        if output_path.exists():
            raise FileExistsError("hard-scan plan output already exists")
        recovery, digest = _load(recovery_path, label="hard-scan saved recovery", limit=MAX_RECOVERY_BYTES)
        recovery = validate_recovery_report(recovery)
        plan = policy.validate_plan({"schema_version": 1, "kind": "ocr_hardscan_plan",
                                     "source_sha256": recovery["source_sha256"], "recovery_sha256": digest,
                                     "coordinate_system": policy.COORDINATES, "approval": "operator_approved",
                                     "regions": regions}, source_sha256=recovery["source_sha256"],
                                    recovery_sha256=digest, page_count=recovery["page_count"])

        def commit(temporary, destination):
            _, current = _read_snapshot(recovery_path, label="hard-scan saved recovery", max_bytes=MAX_RECOVERY_BYTES)
            if current != digest:
                raise RuntimeError("saved recovery changed before plan publication")
            ocr_recovery._publish_new_report(temporary, destination)

        storage_policy.atomic_write_private_json(output_path, plan, indent=2, replace_fn=commit)
    return plan


def verify_request(payload: object, *, pdf_path: Path, recovery_path: Path, plan_path: Path, dpi: int) -> dict:
    dpi = _dpi(dpi)
    report = validate_report(payload)
    recovery, plan, inputs = _inputs(Path(recovery_path), Path(plan_path))
    if (report["retry_configuration"] != _configuration(dpi) or report["inputs"] != inputs
            or report["plan"] != plan or report["baseline_recovery"] != recovery):
        raise ValueError("hard-scan worker report differs from the requested generation")
    _recheck(Path(pdf_path), Path(recovery_path), Path(plan_path), inputs)
    return report


def recover_hardscan(pdf_path: Path, recovery_path: Path, plan_path: Path, output_path: Path, *,
                     dpi: int = 300, reader_factory=None) -> dict:
    dpi = _dpi(dpi)
    paths = [Path(path).absolute() for path in (pdf_path, recovery_path, plan_path, output_path)]
    for path in paths:
        storage_policy.assert_no_link_components(path)
    for index, path in enumerate(paths):
        for other in paths[index+1:]:
            if path.resolve() == other.resolve() or (path.exists() and other.exists() and os.path.samefile(path, other)):
                raise ValueError("hard-scan inputs and output must be distinct")
    pdf_path, recovery_path, plan_path, output_path = paths
    with PathLease(output_path, backend="ocr-hardscan", collection_name="review", operation="retry",
                   timeout=0, resource_description="hard-scan review report"):
        if output_path.exists():
            raise FileExistsError("hard-scan output already exists")
        recovery, plan, inputs = _inputs(recovery_path, plan_path)
        if reader_factory is None:
            from ocr_hardscan_runtime import RapidOCRHardScanReader

            reader_factory = RapidOCRHardScanReader
        with artifact_io.immutable_file_snapshot(pdf_path, snapshot_name="source.pdf",
                                                 expected_sha256=inputs["pdf_sha256"]) as snapshot:
            with reader_factory(snapshot.path, dpi=dpi, max_pixels=policy.MAX_PIXELS, max_side=policy.MAX_SIDE) as reader:
                report = _build(reader, recovery, plan, dpi=dpi)
            report["inputs"] = inputs
            report = validate_report(report)
            size = 1
            for chunk in json.JSONEncoder(ensure_ascii=False, sort_keys=True, indent=2, allow_nan=False).iterencode(report):
                size += len(chunk.encode("utf-8"))
                if size > MAX_REPORT_BYTES:
                    raise ValueError("hard-scan review exceeds output byte budget")

            def commit(temporary, destination):
                snapshot.verify()
                _recheck(pdf_path, recovery_path, plan_path, inputs)
                ocr_recovery._publish_new_report(temporary, destination)

            storage_policy.atomic_write_private_json(output_path, report, indent=2, replace_fn=commit)
    return report
