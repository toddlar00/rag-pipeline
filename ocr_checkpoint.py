"""Strict page-checkpoint records, distinct from completed OCR review reports.

These local, editable records are consistency evidence, not authenticated
attestation. An interrupted start consumes the generation's single attempt.
"""

from __future__ import annotations

from dataclasses import asdict
import hashlib
import json
import re

from evaluation_inputs import _hex_digest
import ocr_recovery
from ocr_recovery_comparison import validate_recovery_report


MAX_SEGMENTS = 64
MAX_PAGE_BYTES = 8 * 1024 * 1024
MAX_PLAN_BYTES = 16 * 1024 * 1024
MAX_REPORT_BYTES = 64 * 1024 * 1024
MAX_GENERATION_BYTES = 256 * 1024 * 1024
MAX_GENERATION_FILES = 256
SCOPE = ("Local page execution checkpoints; interrupted starts consume one attempt. "
         "Origin receipts are per page, not attributed to the resuming process. "
         "No accuracy certification, signed attestation, or automatic acceptance.")
_BASELINE_KEYS = {
    "page_number", "reasons", "original_text", "original_error", "baseline_kind",
    "low_grade", "original_ocr_confidence",
}
_INPUTS = {"source_sha256", "dependency_full_sha256", "dependency_test_sha256",
           "dependency_tools_sha256", "model_policy_sha256", "model_lock_sha256",
           "configuration_sha256", "identity_sha256"}


def json_bytes(value: object, *, limit: int = MAX_REPORT_BYTES) -> bytes:
    """Match the private JSON writer exactly, including UTF-8 and final LF."""
    parts, size = [], 1
    for part in json.JSONEncoder(ensure_ascii=False, sort_keys=True, indent=2,
                                 allow_nan=False).iterencode(value):
        encoded = part.encode("utf-8")
        size += len(encoded)
        if size > limit:
            raise ValueError("checkpoint JSON exceeds its byte budget")
        parts.append(encoded)
    return b"".join(parts) + b"\n"


def digest(value: object) -> str:
    return hashlib.sha256(json_bytes(value)).hexdigest()


def same(left: object, right: object) -> bool:
    """JSON comparison retains boolean/integer/float distinctions."""
    return json_bytes(left) == json_bytes(right)


def fields(value: object, expected: set[str]) -> dict:
    if not isinstance(value, dict) or set(value) != expected:
        raise ValueError("checkpoint record has invalid fields")
    return value


def integer(value: object, lower: int, upper: int) -> int:
    if type(value) is not int or not lower <= value <= upper:
        raise ValueError("checkpoint integer is outside bounds")
    return value


def record(value: object, kind: str, keys: set[str]) -> dict:
    value = fields(value, keys | {"schema_version", "kind"})
    integer(value["schema_version"], 1, 1)
    if value["kind"] != kind:
        raise ValueError("checkpoint record has the wrong kind")
    return value


def validate_identity(value: object) -> dict:
    identity = fields(value, {"environment_sha256", "python_sha256", "base_python_sha256",
                              "runtime_sha256", "producer_sources", "model_artifacts"})
    for name in ("environment_sha256", "python_sha256", "base_python_sha256", "runtime_sha256"):
        _hex_digest(identity[name], label="checkpoint identity")
    sources = identity["producer_sources"]
    if not isinstance(sources, dict) or not 1 <= len(sources) <= 512:
        raise ValueError("checkpoint producer inventory exceeds bounds")
    for name, value in sources.items():
        if not isinstance(name, str) or re.fullmatch(r"(?:tools/)?[A-Za-z0-9_]+\.py", name) is None:
            raise ValueError("checkpoint producer identity is invalid")
        _hex_digest(value, label="checkpoint producer digest")
    models = identity["model_artifacts"]
    if not isinstance(models, list) or len(models) != 3:
        raise ValueError("checkpoint requires all approved model roles")
    roles = []
    for model in models:
        fields(model, {"id", "sha256", "bytes"})
        roles.append(model["id"])
        _hex_digest(model["sha256"], label="checkpoint model digest")
        integer(model["bytes"], 1, 64 * 1024 * 1024)
    if roles != ["classification", "detection", "recognition"]:
        raise ValueError("checkpoint model roles are invalid")
    return identity


def validate_request(value: object) -> dict:
    request = record(value, "ocr_checkpoint_request", {"operation", "inputs", "configuration", "identity"})
    if request["operation"] != "pages":
        raise ValueError("checkpoints currently support page operations only")
    config = fields(request["configuration"], {"operation", "policy", "requested_pages"})
    if config["operation"] != "pages":
        raise ValueError("checkpoint configuration is not a page operation")
    fields(config["policy"], set(asdict(ocr_recovery.RetryPolicy())))
    policy = ocr_recovery.RetryPolicy(**config["policy"])
    if not same(config["policy"], asdict(policy)):
        raise ValueError("checkpoint policy is not canonical")
    pages = config["requested_pages"]
    if not isinstance(pages, list) or len(pages) > 20:
        raise ValueError("checkpoint requested pages exceed bounds")
    for page in pages:
        integer(page, 1, ocr_recovery.MAX_DOCUMENT_PAGES)
    if pages != sorted(set(pages)):
        raise ValueError("checkpoint requested pages are not canonical")
    inputs = request["inputs"]
    if (not isinstance(inputs, dict) or not _INPUTS <= set(inputs)
            or set(inputs) - _INPUTS - {"evidence_sha256", "installation_sha256"}):
        raise ValueError("checkpoint input bindings are invalid")
    for value in inputs.values():
        _hex_digest(value, label="checkpoint input digest")
    validate_identity(request["identity"])
    if inputs["configuration_sha256"] != digest(config) or inputs["identity_sha256"] != digest(request["identity"]):
        raise ValueError("checkpoint derived bindings disagree")
    json_bytes(request, limit=128 * 1024)
    return request


def failed_page(baseline: dict) -> dict:
    return {**baseline, "status": "retry_failed", "candidate": None,
            "error_code": "retry_runtime_failed"}


def assemble_report(request: dict, plan: dict, pages: list[dict]) -> dict:
    config = request["configuration"]
    report = ocr_recovery._assemble_recovery_report(
        source_sha256=request["inputs"]["source_sha256"],
        policy=ocr_recovery.RetryPolicy(**config["policy"]), count=plan["page_count"],
        requested=set(config["requested_pages"]), selected=pages, deferred=plan["deferred_pages"])
    report["evidence_sha256"] = request["inputs"].get("evidence_sha256")
    validate_recovery_report(report)
    json_bytes(report)
    return report


def validate_plan(value: object, request: dict, request_sha256: str) -> dict:
    plan = record(value, "ocr_checkpoint_plan", {"request_sha256", "page_count", "selected_pages", "deferred_pages"})
    if plan["request_sha256"] != request_sha256:
        raise ValueError("checkpoint plan belongs to a different request")
    integer(plan["page_count"], 1, ocr_recovery.MAX_DOCUMENT_PAGES)
    selected = plan["selected_pages"]
    if not isinstance(selected, list) or len(selected) > 20:
        raise ValueError("checkpoint selection exceeds bounds")
    for page in selected:
        fields(page, _BASELINE_KEYS)
    # Reuse the strict closed-report policy, but never persist these validation
    # placeholders as claimed attempts or expose them as a completed report.
    assemble_report(request, plan, [failed_page(page) for page in selected])
    json_bytes(plan, limit=MAX_PLAN_BYTES)
    return plan


def validate_segment(value: object, *, request_sha256: str, number: int, finished: bool) -> dict:
    keys = {"request_sha256", "segment_number"} | ({"status"} if finished else set())
    value = record(value, "ocr_checkpoint_segment_end" if finished else "ocr_checkpoint_segment_start", keys)
    integer(value["segment_number"], 1, MAX_SEGMENTS)
    if value["request_sha256"] != request_sha256 or value["segment_number"] != number:
        raise ValueError("checkpoint segment binding differs")
    if finished and value["status"] not in ("finished", "interrupted"):
        raise ValueError("checkpoint segment status is invalid")
    return value


def validate_start(value: object, *, request_sha256: str, plan_sha256: str,
                   page_number: int, segments: dict) -> dict:
    start = record(value, "ocr_checkpoint_page_start", {
        "request_sha256", "plan_sha256", "page_number", "origin_segment", "segment_sha256"})
    integer(start["page_number"], 1, 5000)
    integer(start["origin_segment"], 1, MAX_SEGMENTS)
    segment = segments.get(start["origin_segment"])
    if (start["request_sha256"] != request_sha256 or start["plan_sha256"] != plan_sha256
            or start["page_number"] != page_number or segment is None
            or start["segment_sha256"] != segment["start_sha256"]):
        raise ValueError("checkpoint page start binding differs")
    return start


def validate_result(value: object, *, request: dict, plan: dict, plan_sha256: str,
                    baseline: dict, start: dict, start_sha256: str, segments: dict) -> dict:
    result = record(value, "ocr_checkpoint_page_result", {
        "request_sha256", "plan_sha256", "page_number", "origin_segment", "start_sha256",
        "state", "sealed_by_segment", "page", "receipt"})
    integer(result["page_number"], 1, 5000)
    integer(result["origin_segment"], 1, MAX_SEGMENTS)
    integer(result["sealed_by_segment"], 1, MAX_SEGMENTS)
    if (result["request_sha256"] != plan["request_sha256"] or result["plan_sha256"] != plan_sha256
            or result["page_number"] != baseline["page_number"]
            or result["origin_segment"] != start["origin_segment"]
            or result["start_sha256"] != start_sha256 or result["sealed_by_segment"] not in segments):
        raise ValueError("checkpoint result binding differs")
    page = result["page"]
    if not isinstance(page, dict) or not same({key: page.get(key) for key in _BASELINE_KEYS}, baseline):
        raise ValueError("checkpoint result changed the retained baseline")
    assemble_report(request, plan, [page if item["page_number"] == baseline["page_number"]
                                    else failed_page(item) for item in plan["selected_pages"]])
    if result["state"] == "interrupted":
        if (not same(page, failed_page(baseline)) or result["receipt"] is not None
                or result["sealed_by_segment"] <= result["origin_segment"]
                or segments[result["origin_segment"]]["status"] != "interrupted"):
            raise ValueError("checkpoint interrupted outcome is inconsistent")
    elif result["state"] == "committed":
        if result["sealed_by_segment"] != result["origin_segment"] or not isinstance(result["receipt"], dict):
            raise ValueError("checkpoint committed outcome lacks origin evidence")
        receipt = result["receipt"]
        expected_inputs = {**request["inputs"], "request_sha256": plan["request_sha256"],
                           "plan_sha256": plan_sha256, "page_start_sha256": start_sha256}
        if (receipt.get("operation") != "pages" or not same(receipt.get("inputs"), expected_inputs)
                or receipt.get("output_sha256") != digest(page)):
            raise ValueError("checkpoint receipt differs from its page bindings")
        calls = receipt.get("calls")
        if (not isinstance(calls, list) or len(calls) > 1
                or any(not isinstance(call, dict) or call.get("id") != "call-0001" for call in calls)):
            raise ValueError("checkpoint page exceeds its actual-call budget")
        if page["candidate"] is not None:
            observation = receipt.get("engine_observation")
            if (len(calls) != 1 or calls[0].get("status") != "completed"
                    or not isinstance(observation, dict) or not isinstance(observation.get("engine"), dict)
                    or not same(observation["engine"], {key: page["candidate"]["engine"][key] for key in ("name", "version")})):
                raise ValueError("checkpoint candidate lacks its completed engine call")
    else:
        raise ValueError("checkpoint result state is invalid")
    json_bytes(result, limit=MAX_PAGE_BYTES)
    return result
