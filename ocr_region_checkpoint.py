"""Strict region checkpoint records, separate from page journals and reports.

Local editable consistency records are not authenticated execution evidence.
A durable start consumes one attempt, even if its raw call count is unknown.
"""

from __future__ import annotations

import copy
import hashlib

from evaluation_inputs import _hex_digest
from ocr_checkpoint import (digest, fields, integer, json_bytes, record, same,
                            validate_identity)
import ocr_regions as regions


MAX_REGIONS = 20
MAX_SEGMENTS = 64
MAX_RESULT_BYTES = 8 * 1024 * 1024
MAX_PLAN_BYTES = 1024 * 1024
MAX_REPORT_BYTES = 128 * 1024 * 1024
MAX_MANIFEST_BYTES = 128 * 1024
SCOPE = ("Local region execution checkpoints; interrupted starts consume one attempt. "
         "Origin receipts are per region, not attributed to the resuming process. "
         "No accuracy certification, signed attestation, or automatic acceptance.")
_INPUTS = {"source_sha256", "recovery_sha256", "plan_sha256", "dependency_full_sha256",
           "dependency_test_sha256", "dependency_tools_sha256", "model_policy_sha256",
           "model_lock_sha256", "configuration_sha256", "identity_sha256"}
_ITEM = {"ordinal", "region_id", "page_number"}
_OUTCOME = {"region_id", "page_number", "bbox", "geometry", "status", "candidate",
            "page_boxes", "error_code"}


def configuration(dpi: int) -> dict:
    """The existing region recipe, independent of page policy serialization."""
    return {"operation": "regions", "retry_configuration": {
        "dpi": regions._dpi(dpi), "max_regions": regions.MAX_REGIONS,
        "max_pixels": regions.MAX_PIXELS, "max_side": regions.MAX_SIDE,
        "max_total_pixels": regions.MAX_TOTAL_PIXELS, "attempts_per_region": 1,
        "min_score": 0.0, "preprocessing": "none"}}


def validate_request(value: object) -> dict:
    request = record(value, "ocr_region_checkpoint_request", {
        "operation", "inputs", "configuration", "identity"})
    if request["operation"] != "regions":
        raise ValueError("region checkpoints require a region operation")
    config = fields(request["configuration"], {"operation", "retry_configuration"})
    recipe = fields(config["retry_configuration"], set(configuration(300)["retry_configuration"]))
    if not same(config, configuration(recipe["dpi"])):
        raise ValueError("region checkpoint recipe differs from ordinary region retry")
    inputs = request["inputs"]
    if (not isinstance(inputs, dict) or not _INPUTS <= set(inputs)
            or set(inputs) - _INPUTS - {"installation_sha256"}):
        raise ValueError("region checkpoint input bindings are invalid")
    for item in inputs.values():
        _hex_digest(item, label="region checkpoint input digest")
    validate_identity(request["identity"])
    if (inputs["configuration_sha256"] != digest(config)
            or inputs["identity_sha256"] != digest(request["identity"])):
        raise ValueError("region checkpoint derived bindings disagree")
    json_bytes(request, limit=MAX_MANIFEST_BYTES)
    return request


def validate_plan(value: object, request: dict, request_sha256: str, *,
                  recovery: dict, region_plan: dict) -> dict:
    """Join derived geometry to once-loaded, validated original inputs."""
    plan = record(value, "ocr_region_checkpoint_plan", {
        "request_sha256", "page_count", "coordinate_system", "regions", "planned_pixels"})
    json_bytes(plan, limit=MAX_PLAN_BYTES)
    if plan["request_sha256"] != request_sha256 or request_sha256 != digest(request):
        raise ValueError("region geometry plan belongs to a different request")
    integer(plan["page_count"], 1, 5000)
    if (plan["page_count"] != recovery["page_count"]
            or recovery["source_sha256"] != request["inputs"]["source_sha256"]):
        raise ValueError("region geometry plan differs from original recovery")
    expected = regions.validate_region_plan(
        region_plan, source_sha256=request["inputs"]["source_sha256"],
        recovery_sha256=request["inputs"]["recovery_sha256"], page_count=plan["page_count"])
    if not same(region_plan, expected) or plan["coordinate_system"] != expected["coordinate_system"]:
        raise ValueError("region geometry plan requires the canonical original plan")
    entries = plan["regions"]
    if not isinstance(entries, list) or len(entries) != len(expected["regions"]):
        raise ValueError("region geometry plan does not cover the original plan")
    total = 0
    dpi = request["configuration"]["retry_configuration"]["dpi"]
    for ordinal, (entry, original) in enumerate(zip(entries, expected["regions"]), 1):
        fields(entry, _ITEM | {"bbox", "geometry"})
        integer(entry["ordinal"], 1, MAX_REGIONS)
        integer(entry["page_number"], 1, 5000)
        if entry["ordinal"] != ordinal or not same({key: entry[key] for key in original}, original):
            raise ValueError("region geometry entry differs from canonical ordinal or identity")
        geometry = regions._validate_geometry(entry["geometry"], original["bbox"], dpi=dpi)
        total += geometry["raster"]["width"] * geometry["raster"]["height"]
        if total > regions.MAX_TOTAL_PIXELS:
            raise ValueError("region geometry plan exceeds aggregate raster budget")
    integer(plan["planned_pixels"], 1, regions.MAX_TOTAL_PIXELS)
    if plan["planned_pixels"] != total:
        raise ValueError("region geometry plan pixel total differs")
    return plan


def validate_segment_order(segments: dict) -> dict:
    """Region chronology only; common segment envelopes retain the page schema."""
    if not isinstance(segments, dict) or len(segments) > MAX_SEGMENTS:
        raise ValueError("region checkpoint segments exceed bounds")
    for number in segments:
        integer(number, 1, MAX_SEGMENTS)
    if sorted(segments) != list(range(1, len(segments) + 1)):
        raise ValueError("region checkpoint segments must be contiguous")
    for number in sorted(segments):
        segment = fields(segments[number], {"start_sha256", "status", "end_sha256"})
        _hex_digest(segment["start_sha256"], label="region segment start digest")
        if segment["status"] == "open":
            if number != len(segments) or segment["end_sha256"] is not None:
                raise ValueError("only the last region segment can be open")
        elif segment["status"] in ("interrupted", "finished"):
            _hex_digest(segment["end_sha256"], label="region segment end digest")
            if segment["status"] == "finished" and number != len(segments):
                raise ValueError("region work cannot follow a finished segment")
        else:
            raise ValueError("region segment status is invalid")
    return segments


def _item(value: dict, entry: dict) -> None:
    integer(value["ordinal"], 1, MAX_REGIONS)
    integer(value["page_number"], 1, 5000)
    if (not isinstance(value["region_id"], str)
            or not same({key: value[key] for key in _ITEM}, {key: entry[key] for key in _ITEM})):
        raise ValueError("region checkpoint item differs from its geometry-plan entry")


def validate_start(value: object, *, request_sha256: str, checkpoint_plan_sha256: str,
                   entry: dict, segments: dict) -> dict:
    start = record(value, "ocr_region_checkpoint_start", _ITEM | {
        "request_sha256", "checkpoint_plan_sha256", "origin_segment", "segment_sha256"})
    _item(start, entry)
    integer(start["origin_segment"], 1, MAX_SEGMENTS)
    segment = segments.get(start["origin_segment"])
    if (start["request_sha256"] != request_sha256
            or start["checkpoint_plan_sha256"] != checkpoint_plan_sha256
            or segment is None or start["segment_sha256"] != segment["start_sha256"]):
        raise ValueError("region start request, plan or origin segment differs")
    json_bytes(start, limit=MAX_MANIFEST_BYTES)
    return start


def failed_region(entry: dict) -> dict:
    return {**copy.deepcopy({key: entry[key] for key in ("region_id", "page_number", "bbox", "geometry")}),
            "status": "retry_failed", "candidate": None, "page_boxes": None,
            "error_code": "retry_runtime_failed"}


def outcome_digest(outcome: object) -> str:
    return hashlib.sha256(json_bytes(outcome, limit=MAX_RESULT_BYTES)).hexdigest()


def validate_result(value: object, *, request: dict, plan: dict,
                    checkpoint_plan_sha256: str, entry: dict, start: dict,
                    start_sha256: str, segments: dict) -> dict:
    """Validate one outcome/receipt join without rebuilding recovery contexts.

    The coordinator also validates every nonnull receipt with the existing
    runtime-aware validate_execution_receipt; this function checks local joins.
    """
    result = record(value, "ocr_region_checkpoint_result", _ITEM | {
        "request_sha256", "checkpoint_plan_sha256", "origin_segment", "start_sha256",
        "state", "sealed_by_segment", "region", "receipt"})
    json_bytes(result, limit=MAX_RESULT_BYTES)
    _item(result, entry)
    integer(result["origin_segment"], 1, MAX_SEGMENTS)
    integer(result["sealed_by_segment"], 1, MAX_SEGMENTS)
    if (result["request_sha256"] != plan["request_sha256"]
            or result["checkpoint_plan_sha256"] != checkpoint_plan_sha256
            or result["origin_segment"] != start["origin_segment"]
            or result["start_sha256"] != start_sha256
            or result["origin_segment"] not in segments or result["sealed_by_segment"] not in segments):
        raise ValueError("region result request, plan, start or segment differs")
    outcome = fields(result["region"], _OUTCOME)
    if not same({key: outcome[key] for key in ("region_id", "page_number", "bbox", "geometry")},
                {key: entry[key] for key in ("region_id", "page_number", "bbox", "geometry")}):
        raise ValueError("region result changed its retained identity or geometry")
    if not isinstance(outcome["status"], str):
        raise ValueError("invalid region result status")
    dpi = request["configuration"]["retry_configuration"]["dpi"]
    geometry = regions._validate_geometry(outcome["geometry"], entry["bbox"], dpi=dpi)
    regions._validate_region_outcome(outcome, geometry, dpi=dpi)
    if result["state"] == "interrupted":
        if (not same(outcome, failed_region(entry)) or result["receipt"] is not None
                or result["sealed_by_segment"] <= result["origin_segment"]
                or segments[result["origin_segment"]]["status"] != "interrupted"):
            raise ValueError("interrupted region outcome contradicts its origin")
    elif result["state"] == "committed":
        receipt = result["receipt"]
        if result["sealed_by_segment"] != result["origin_segment"] or not isinstance(receipt, dict):
            raise ValueError("committed region outcome requires its origin receipt")
        expected_inputs = {**request["inputs"], "checkpoint_request_sha256": plan["request_sha256"],
                           "checkpoint_plan_sha256": checkpoint_plan_sha256,
                           "region_start_sha256": start_sha256}
        if (receipt.get("operation") != "regions" or not same(receipt.get("inputs"), expected_inputs)
                or receipt.get("output_sha256") != outcome_digest(outcome)):
            raise ValueError("region receipt differs from exact input/start/outcome bindings")
        calls = receipt.get("calls")
        if (not isinstance(calls, list) or len(calls) > 1
                or any(not isinstance(call, dict) or call.get("id") != "call-0001" for call in calls)):
            raise ValueError("region receipt exceeds its actual-call budget")
        if outcome["candidate"] is not None:
            observation = receipt.get("engine_observation")
            if (len(calls) != 1 or calls[0].get("status") != "completed"
                    or not isinstance(observation, dict) or not isinstance(observation.get("engine"), dict)
                    or not same(observation["engine"], {key: outcome["candidate"]["engine"][key]
                                                       for key in ("name", "version")})):
                raise ValueError("region candidate lacks its completed engine observation")
    else:
        raise ValueError("region checkpoint result state is invalid")
    return result


def report_bytes(report: object) -> bytes:
    return json_bytes(report, limit=MAX_REPORT_BYTES)


def report_digest(report: object) -> str:
    return hashlib.sha256(report_bytes(report)).hexdigest()


def report_same(left: object, right: object) -> bool:
    return report_bytes(left) == report_bytes(right)


def assemble_report(request: dict, plan: dict, recovery: dict, region_plan: dict,
                    outcomes: list[dict]) -> dict:
    """Assemble page contexts once, then apply the unchanged closed validator."""
    if not isinstance(outcomes, list) or len(outcomes) != len(plan["regions"]):
        raise ValueError("region outcomes do not cover the geometry plan")
    for entry, outcome in zip(plan["regions"], outcomes):
        if not same({key: outcome.get(key) for key in ("region_id", "page_number", "bbox", "geometry")},
                    {key: entry[key] for key in ("region_id", "page_number", "bbox", "geometry")}):
            raise ValueError("region report outcome differs from its geometry plan")
    report = regions._assemble_region_review(
        recovery, region_plan, outcomes, plan["planned_pixels"],
        dpi=request["configuration"]["retry_configuration"]["dpi"])
    report["inputs"] = {"pdf_sha256": request["inputs"]["source_sha256"],
                        "recovery_sha256": request["inputs"]["recovery_sha256"],
                        "plan_sha256": request["inputs"]["plan_sha256"]}
    report = regions.validate_region_review(report)
    report_bytes(report)
    return report


def completion(request: dict, state: dict, report: dict) -> dict:
    """Derive a completion from already validated state, without outcome replay."""
    plan, starts, results, segments = state["plan"], state["starts"], state["results"], state["segments"]
    validate_segment_order(segments)
    if plan is None or not segments or any(item["status"] == "open" for item in segments.values()):
        raise ValueError("region checkpoint generation is incomplete")
    ordinals = [entry["ordinal"] for entry in plan["regions"]]
    if (set(starts) != set(ordinals) or set(results) != set(ordinals)
            or state["request_sha256"] != digest(request)
            or state["checkpoint_plan_sha256"] != hashlib.sha256(json_bytes(plan, limit=MAX_PLAN_BYTES)).hexdigest()):
        raise ValueError("region completion does not cover its exact request/plan")
    items = []
    for entry in plan["regions"]:
        ordinal = entry["ordinal"]
        result, result_sha256 = results[ordinal]
        items.append({**{key: entry[key] for key in _ITEM}, "state": result["state"],
                      "origin_segment": result["origin_segment"], "sealed_by_segment": result["sealed_by_segment"],
                      "start_sha256": starts[ordinal][1], "result_sha256": result_sha256})
    segment_rows = []
    for number in sorted(segments):
        origin = [result for result, _ in results.values() if result["origin_segment"] == number]
        segment_rows.append({"segment_number": number, **segments[number],
            "origin_ordinals": sorted(result["ordinal"] for result in origin),
            "sealed_ordinals": sorted(result["ordinal"] for result, _ in results.values()
                                      if result["sealed_by_segment"] == number),
            "observed_calls": sum(len(result["receipt"]["calls"]) for result in origin if result["receipt"] is not None),
            "unobserved_interrupted_ordinals": sorted(result["ordinal"] for result in origin
                                                     if result["state"] == "interrupted")})
    manifest = {"schema_version": 1, "kind": "ocr_region_checkpoint_completion", "operation": "regions",
                "request_sha256": state["request_sha256"], "checkpoint_plan_sha256": state["checkpoint_plan_sha256"],
                "report_sha256": report_digest(report), "inputs": request["inputs"],
                "configuration": request["configuration"], "regions": items, "segments": segment_rows,
                "coverage": {"planned_ordinals": ordinals,
                             "committed_ordinals": [item["ordinal"] for item in items if item["state"] == "committed"],
                             "interrupted_ordinals": [item["ordinal"] for item in items if item["state"] == "interrupted"],
                             "unstarted_ordinals": []},
                "requires_attention": True, "scope": SCOPE}
    json_bytes(manifest, limit=MAX_MANIFEST_BYTES)
    return manifest
