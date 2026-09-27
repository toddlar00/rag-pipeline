"""Strict hard-scan checkpoint records with consumed attempts and vertex budgets.

Local editable consistency records are not authenticated execution evidence.
An interrupted geometry abstention is not an observed zero-call commitment.
"""

from __future__ import annotations

import copy
import hashlib

from evaluation_inputs import _hex_digest
from ocr_checkpoint import (digest, fields, integer, json_bytes, record, same,
                            validate_identity)
import ocr_hardscan as policy
import ocr_hardscan_io as hardscan
from ocr_region_checkpoint import (MAX_MANIFEST_BYTES, MAX_RESULT_BYTES, MAX_SEGMENTS,
                                   validate_segment_order)


MAX_REGIONS = policy.MAX_REGIONS
MAX_TOTAL_VERTICES = policy.MAX_TOTAL_VERTICES
MAX_PLAN_BYTES = hardscan.MAX_PLAN_BYTES
MAX_REPORT_BYTES = hardscan.MAX_REPORT_BYTES
SCOPE = ("Local hard-scan execution checkpoints; interrupted starts consume one attempt. "
         "Origin receipts are per region, not attributed to the resuming process. "
         "An interrupted geometry abstention does not establish zero calls. "
         "No accuracy certification, signed attestation, or automatic acceptance.")
_INPUTS = {"source_sha256", "recovery_sha256", "plan_sha256", "dependency_full_sha256",
           "dependency_test_sha256", "dependency_tools_sha256", "model_policy_sha256",
           "model_lock_sha256", "configuration_sha256", "identity_sha256"}
_ITEM = {"ordinal", "region_id", "page_number"}
_RETAINED = {"region_id", "page_number", "bbox", "recipe", "geometry", "transform"}
_OUTCOME = _RETAINED | {"status", "candidate", "processing", "source_polygons",
                        "error_code", "abstention_reason"}


def configuration(dpi: int) -> dict:
    return {"operation": "hardscan", "retry_configuration": hardscan._configuration(dpi)}


def validate_request(value: object) -> dict:
    request = record(value, "ocr_hardscan_checkpoint_request", {
        "operation", "inputs", "configuration", "identity"})
    if request["operation"] != "hardscan":
        raise ValueError("hard-scan checkpoints require a hardscan operation")
    config = fields(request["configuration"], {"operation", "retry_configuration"})
    recipe = fields(config["retry_configuration"], set(configuration(300)["retry_configuration"]))
    if not same(config, configuration(recipe["dpi"])):
        raise ValueError("hard-scan checkpoint recipe differs from ordinary hard-scan retry")
    inputs = request["inputs"]
    if (not isinstance(inputs, dict) or not _INPUTS <= set(inputs)
            or set(inputs) - _INPUTS - {"installation_sha256"}):
        raise ValueError("hard-scan checkpoint input bindings are invalid")
    for item in inputs.values():
        _hex_digest(item, label="hard-scan checkpoint input digest")
    validate_identity(request["identity"])
    if (inputs["configuration_sha256"] != digest(config)
            or inputs["identity_sha256"] != digest(request["identity"])):
        raise ValueError("hard-scan checkpoint derived bindings disagree")
    json_bytes(request, limit=MAX_MANIFEST_BYTES)
    return request


def validate_plan(value: object, request: dict, request_sha256: str, *,
                  recovery: dict, region_plan: dict) -> dict:
    """Join derived transforms to once-loaded, validated original hard-scan inputs."""
    plan = record(value, "ocr_hardscan_checkpoint_plan", {
        "request_sha256", "page_count", "coordinate_system", "regions", "planned_pixels"})
    json_bytes(plan, limit=MAX_PLAN_BYTES)
    if plan["request_sha256"] != request_sha256 or request_sha256 != digest(request):
        raise ValueError("hard-scan geometry plan belongs to a different request")
    integer(plan["page_count"], 1, 5000)
    if (plan["page_count"] != recovery["page_count"]
            or recovery["source_sha256"] != request["inputs"]["source_sha256"]):
        raise ValueError("hard-scan geometry plan differs from original recovery")
    expected = policy.validate_plan(
        region_plan, source_sha256=request["inputs"]["source_sha256"],
        recovery_sha256=request["inputs"]["recovery_sha256"], page_count=plan["page_count"])
    if not same(region_plan, expected) or plan["coordinate_system"] != expected["coordinate_system"]:
        raise ValueError("hard-scan geometry plan requires the canonical original plan")
    entries = plan["regions"]
    if not isinstance(entries, list) or len(entries) != len(expected["regions"]):
        raise ValueError("hard-scan geometry plan does not cover the original plan")
    dpi = request["configuration"]["retry_configuration"]["dpi"]
    for ordinal, (entry, original) in enumerate(zip(entries, expected["regions"]), 1):
        fields(entry, _ITEM | {"bbox", "recipe", "geometry", "transform"})
        integer(entry["ordinal"], 1, MAX_REGIONS)
        integer(entry["page_number"], 1, 5000)
        if entry["ordinal"] != ordinal or not same({key: entry[key] for key in original}, original):
            raise ValueError("hard-scan geometry entry differs from canonical ordinal or identity")
        geometry = hardscan._validate_geometry(entry["geometry"], original["bbox"], dpi=dpi)
        raster = geometry["raster"]
        policy.validate_transform(entry["transform"], width=raster["width"],
                                  height=raster["height"], recipe=original["recipe"])
    total = hardscan._planned_pixels(entries)
    integer(plan["planned_pixels"], 1, policy.MAX_TOTAL_PIXELS)
    if plan["planned_pixels"] != total:
        raise ValueError("hard-scan geometry plan pixel total differs")
    return plan


def _item(value: dict, entry: dict) -> None:
    integer(value["ordinal"], 1, MAX_REGIONS)
    integer(value["page_number"], 1, 5000)
    if (not isinstance(value["region_id"], str)
            or not same({key: value[key] for key in _ITEM}, {key: entry[key] for key in _ITEM})):
        raise ValueError("hard-scan checkpoint item differs from its geometry-plan entry")


def validate_start(value: object, *, request_sha256: str, checkpoint_plan_sha256: str,
                   entry: dict, segments: dict) -> dict:
    start = record(value, "ocr_hardscan_checkpoint_start", _ITEM | {
        "request_sha256", "checkpoint_plan_sha256", "origin_segment", "segment_sha256"})
    _item(start, entry)
    integer(start["origin_segment"], 1, MAX_SEGMENTS)
    segment = segments.get(start["origin_segment"])
    if (start["request_sha256"] != request_sha256
            or start["checkpoint_plan_sha256"] != checkpoint_plan_sha256
            or segment is None or start["segment_sha256"] != segment["start_sha256"]):
        raise ValueError("hard-scan start request, plan or origin segment differs")
    json_bytes(start, limit=MAX_MANIFEST_BYTES)
    return start


def interrupted_region(entry: dict) -> dict:
    """Project known geometry only; the checkpoint still has no origin receipt."""
    outcome = {**copy.deepcopy({key: entry[key] for key in _RETAINED}),
               "status": "retry_failed", "candidate": None, "processing": None,
               "source_polygons": None, "error_code": "retry_runtime_failed", "abstention_reason": None}
    if entry["transform"]["eligibility"] == "abstained":
        outcome.update(status="abstained", error_code=None, abstention_reason=entry["transform"]["reason"])
    return outcome


def outcome_digest(outcome: object) -> str:
    return hashlib.sha256(json_bytes(outcome, limit=MAX_RESULT_BYTES)).hexdigest()


def validate_result(value: object, *, request: dict, plan: dict,
                    checkpoint_plan_sha256: str, entry: dict, start: dict,
                    start_sha256: str, segments: dict, vertex_budget: int) -> int:
    """Validate one outcome/receipt join and return its canonical vertex usage.

    The caller supplies validated request/plan/start/segment state and remaining
    prefix budget, and also applies runtime-aware validate_execution_receipt.
    """
    result = record(value, "ocr_hardscan_checkpoint_result", _ITEM | {
        "request_sha256", "checkpoint_plan_sha256", "origin_segment", "start_sha256",
        "state", "sealed_by_segment", "region", "receipt"})
    json_bytes(result, limit=MAX_RESULT_BYTES)
    integer(vertex_budget, 0, MAX_TOTAL_VERTICES)
    _item(result, entry)
    integer(result["origin_segment"], 1, MAX_SEGMENTS)
    integer(result["sealed_by_segment"], 1, MAX_SEGMENTS)
    if (result["request_sha256"] != plan["request_sha256"]
            or result["checkpoint_plan_sha256"] != checkpoint_plan_sha256
            or result["origin_segment"] != start["origin_segment"]
            or result["start_sha256"] != start_sha256
            or result["origin_segment"] not in segments or result["sealed_by_segment"] not in segments):
        raise ValueError("hard-scan result request, plan, start or segment differs")
    outcome = fields(result["region"], _OUTCOME)
    if not same({key: outcome[key] for key in _RETAINED}, {key: entry[key] for key in _RETAINED}):
        raise ValueError("hard-scan result changed its retained identity, recipe or geometry")
    dpi = request["configuration"]["retry_configuration"]["dpi"]
    used_vertices = hardscan._validate_hardscan_region(
        outcome, {key: entry[key] for key in ("region_id", "page_number", "bbox", "recipe")},
        dpi=dpi, vertex_budget=vertex_budget)
    if result["state"] == "interrupted":
        if (not same(outcome, interrupted_region(entry)) or result["receipt"] is not None
                or result["sealed_by_segment"] <= result["origin_segment"]
                or segments[result["origin_segment"]]["status"] != "interrupted"):
            raise ValueError("interrupted hard-scan outcome contradicts its origin")
    elif result["state"] == "committed":
        receipt = result["receipt"]
        if result["sealed_by_segment"] != result["origin_segment"] or not isinstance(receipt, dict):
            raise ValueError("committed hard-scan outcome requires its origin receipt")
        expected_inputs = {**request["inputs"], "checkpoint_request_sha256": plan["request_sha256"],
                           "checkpoint_plan_sha256": checkpoint_plan_sha256,
                           "hardscan_start_sha256": start_sha256}
        if (receipt.get("operation") != "hardscan" or not same(receipt.get("inputs"), expected_inputs)
                or receipt.get("output_sha256") != outcome_digest(outcome)):
            raise ValueError("hard-scan receipt differs from exact input/start/outcome bindings")
        calls = receipt.get("calls")
        if (not isinstance(calls, list) or len(calls) > 1
                or any(not isinstance(call, dict) or call.get("id") != "call-0001" for call in calls)):
            raise ValueError("hard-scan receipt exceeds its actual-call budget")
        if outcome["status"] == "abstained" and calls:
            raise ValueError("hard-scan abstention requires a zero-call origin receipt")
        if outcome["candidate"] is not None:
            observation = receipt.get("engine_observation")
            if (len(calls) != 1 or calls[0].get("status") != "completed"
                    or not isinstance(observation, dict) or not isinstance(observation.get("engine"), dict)
                    or not same(observation["engine"], {key: outcome["candidate"]["engine"][key]
                                                       for key in ("name", "version")})):
                raise ValueError("hard-scan candidate lacks its completed engine observation")
    else:
        raise ValueError("hard-scan checkpoint result state is invalid")
    return used_vertices


def report_bytes(report: object) -> bytes:
    return json_bytes(report, limit=MAX_REPORT_BYTES)


def report_digest(report: object) -> str:
    return hashlib.sha256(report_bytes(report)).hexdigest()


def report_same(left: object, right: object) -> bool:
    return report_bytes(left) == report_bytes(right)


def assemble_report(request: dict, plan: dict, recovery: dict, region_plan: dict,
                    outcomes: list[dict]) -> dict:
    """Assemble once, then retain the complete ordinary hard-scan validation."""
    if not isinstance(outcomes, list) or len(outcomes) != len(plan["regions"]):
        raise ValueError("hard-scan outcomes do not cover the geometry plan")
    for entry, outcome in zip(plan["regions"], outcomes):
        if not isinstance(outcome, dict) or not same(
                {key: outcome.get(key) for key in _RETAINED}, {key: entry[key] for key in _RETAINED}):
            raise ValueError("hard-scan report outcome differs from its geometry plan")
    report = hardscan._assemble_hardscan_review(
        recovery, region_plan, outcomes, dpi=request["configuration"]["retry_configuration"]["dpi"])
    report["inputs"] = {"pdf_sha256": request["inputs"]["source_sha256"],
                        "recovery_sha256": request["inputs"]["recovery_sha256"],
                        "plan_sha256": request["inputs"]["plan_sha256"]}
    report = hardscan.validate_report(report)
    report_bytes(report)
    return report


def completion(request: dict, state: dict, report: dict) -> dict:
    """Derive a completion from already validated state, without outcome replay."""
    validate_request(request)
    plan, starts, results, segments = state["plan"], state["starts"], state["results"], state["segments"]
    if plan is not None:
        record(plan, "ocr_hardscan_checkpoint_plan", {
            "request_sha256", "page_count", "coordinate_system", "regions", "planned_pixels"})
    validate_segment_order(segments)
    if plan is None or not segments or any(item["status"] == "open" for item in segments.values()):
        raise ValueError("hard-scan checkpoint generation is incomplete")
    ordinals = [entry["ordinal"] for entry in plan["regions"]]
    if (set(starts) != set(ordinals) or set(results) != set(ordinals)
            or state["request_sha256"] != digest(request)
            or state["checkpoint_plan_sha256"] != hashlib.sha256(json_bytes(plan, limit=MAX_PLAN_BYTES)).hexdigest()):
        raise ValueError("hard-scan completion does not cover its exact request/plan")
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
    manifest = {"schema_version": 1, "kind": "ocr_hardscan_checkpoint_completion", "operation": "hardscan",
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
