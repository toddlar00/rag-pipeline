"""Compare source-bound OCR review candidates on a fixed reference-page cohort.

Input hashes bind supplied artifacts, not their authenticity. No PDF, model,
canonical extraction, or index is opened or modified by this adapter.
"""

from __future__ import annotations

import os
from pathlib import Path

from evaluation_inputs import _hex_digest, _read_snapshot, _strict_json_bytes
from ocr_comparison import compare_ocr
import ocr_evaluation
import ocr_recovery
from resource_lease import PathLease
import storage_policy


MAX_RECOVERY_BYTES = 64 * 1024 * 1024
MAX_REFERENCE_BYTES = 16 * 1024 * 1024
_REASONS = (
    "requested", "extraction_unavailable", "empty_text", "corrupt_text",
    "short_text", "poor_quality_grade",
)
_PAGE_FIELDS = {
    "page_number", "reasons", "original_text", "original_error", "baseline_kind",
    "low_grade", "original_ocr_confidence", "status", "candidate", "error_code",
}


def _fields(value: object, expected: set[str], label: str) -> dict:
    if not isinstance(value, dict) or set(value) != expected:
        raise ValueError(f"invalid {label} fields")
    return value


def _integer(value: object, lower: int, upper: int, label: str) -> int:
    if type(value) is not int or not lower <= value <= upper:
        raise ValueError(f"invalid {label}")
    return value


def _page_numbers(values: object, page_count: int) -> list[int]:
    if not isinstance(values, list) or len(values) > page_count:
        raise ValueError("invalid page-number list")
    result = [_integer(n, 1, page_count, "page number") for n in values]
    if len(set(result)) != len(result):
        raise ValueError("duplicate page number")
    return result


def _reasons(value: object, *, requested: bool) -> None:
    if (not isinstance(value, list) or not value or len(value) > len(_REASONS)
            or any(not isinstance(reason, str) or reason not in _REASONS for reason in value)
            or value != [reason for reason in _REASONS if reason in value]
            or ("requested" in value) != requested):
        raise ValueError("invalid recovery selection reasons")
    text_reasons = set(value) & {"extraction_unavailable", "empty_text", "corrupt_text", "short_text"}
    if text_reasons & {"extraction_unavailable", "empty_text"} and len(text_reasons) != 1:
        raise ValueError("contradictory recovery selection reasons")


def validate_recovery_report(payload: object) -> dict:
    """Validate original v1 and opt-in preprocessing v2 recovery reports."""
    report = _fields(payload, {
        "schema_version", "kind", "source_sha256", "evidence_sha256", "page_count",
        "selection", "retry_configuration", "pages", "deferred_pages", "summary",
        "canonical_extraction_modified", "accuracy_verified", "reading_order",
    }, "recovery report")
    version = _integer(report["schema_version"], 1, 2, "recovery schema version")
    if report["kind"] != "ocr_recovery_review":
        raise ValueError("invalid recovery report kind")
    digest = _hex_digest(report["source_sha256"], label="recovery source digest")
    if report["evidence_sha256"] is not None:
        _hex_digest(report["evidence_sha256"], label="recovery evidence digest")
    if report["canonical_extraction_modified"] is not False or report["accuracy_verified"] is not False:
        raise ValueError("unsupported recovery attestation")
    if report["reading_order"] != "OCR engine order; not validated layout or table structure":
        raise ValueError("unsupported recovery reading-order declaration")
    count = _integer(report["page_count"], 1, 5_000, "PDF page count")
    selection = _fields(report["selection"], {
        "requested_pages", "max_pages", "min_chars", "order",
    }, "recovery selection")
    config_fields = {
        "dpi", "max_pixels", "max_side", "attempts_per_page",
    }
    if version == 2:
        config_fields.add("preprocessing")
    config = _fields(report["retry_configuration"], config_fields, "retry configuration")
    preprocessing = config["preprocessing"] if version == 2 else "none"
    if version == 2 and (
        not isinstance(preprocessing, str)
        or preprocessing not in {"deskew", "contrast", "deskew-contrast"}
    ):
        raise ValueError("v2 recovery requires an enabled preprocessing mode")
    policy = ocr_recovery.RetryPolicy(
        dpi=config["dpi"], max_pages=selection["max_pages"], min_chars=selection["min_chars"],
        max_pixels=config["max_pixels"], max_side=config["max_side"],
        preprocessing=preprocessing)
    _integer(config["attempts_per_page"], 1, 1, "retry attempt count")
    requested = _page_numbers(selection["requested_pages"], count)
    if requested != sorted(requested) or selection["order"] != "requested_then_page_number":
        raise ValueError("invalid recovery selection order")
    requested_set = set(requested)
    selected, deferred = report["pages"], report["deferred_pages"]
    if not isinstance(selected, list) or len(selected) > min(count, policy.max_pages):
        raise ValueError("invalid selected-page count")
    if not isinstance(deferred, list) or len(deferred) > count:
        raise ValueError("invalid deferred-page count")
    selected_numbers, deferred_numbers = [], []
    for page in selected:
        _fields(page, _PAGE_FIELDS, "selected page")
        number = _integer(page["page_number"], 1, count, "selected page number")
        selected_numbers.append(number)
        original = page["original_text"]
        if original is None:
            if page["original_error"] != "native_extraction_failed":
                raise ValueError("missing original extraction error")
        else:
            ocr_recovery._page_text(original)
            if page["original_error"] is not None:
                raise ValueError("original text contradicts extraction error")
        ocr_recovery.validate_evidence({
            "schema_version": 1, "source_sha256": digest,
            "pages": [{"page_number": number, "text": original or "",
                       "low_grade": page["low_grade"], "ocr_confidence": page["original_ocr_confidence"]}],
        })
        if page["baseline_kind"] == "pdf_native":
            if page["low_grade"] is not None or page["original_ocr_confidence"] is not None:
                raise ValueError("native baseline has unsupported evidence scores")
        elif page["baseline_kind"] == "operator_evidence":
            if report["evidence_sha256"] is None or original is None:
                raise ValueError("operator baseline lacks evidence binding or text")
        else:
            raise ValueError("unknown recovery baseline kind")
        _reasons(page["reasons"], requested=number in requested_set)
        if page["reasons"] != ocr_recovery.page_reasons(
            original, requested=number in requested_set, low_grade=page["low_grade"], min_chars=policy.min_chars
        ):
            raise ValueError("recovery selection reasons disagree with page evidence")
        if page["status"] == "retry_failed":
            if page["candidate"] is not None or page["error_code"] not in (
                "retry_limit_or_validation", "retry_runtime_unavailable", "retry_runtime_failed"
            ):
                raise ValueError("invalid failed retry")
        elif page["status"] in ("review_required", "empty_candidate"):
            candidate = ocr_recovery._candidate_snapshot(
                page["candidate"], preprocessing=preprocessing)
            if page["error_code"] is not None or bool(candidate["text"].strip()) != (page["status"] == "review_required"):
                raise ValueError("retry status contradicts candidate text")
            ocr_recovery._validate_candidate_policy(candidate, policy)
        else:
            raise ValueError("unsupported recovery status")
    for page in deferred:
        _fields(page, {"page_number", "reasons"}, "deferred page")
        number = _integer(page["page_number"], 1, count, "deferred page number")
        deferred_numbers.append(number)
        _reasons(page["reasons"], requested=number in requested_set)
    numbers = _page_numbers(selected_numbers + deferred_numbers, count)
    if (not requested_set <= set(numbers)
            or numbers != sorted(numbers, key=lambda n: (n not in requested_set, n))
            or deferred and len(selected) != policy.max_pages):
        raise ValueError("recovery pages contradict deterministic selection")
    expected_summary = {
        "inspected": count, "selected": len(selected), "deferred": len(deferred),
        "failed": sum(p["status"] == "retry_failed" for p in selected),
        "empty": sum(p["status"] == "empty_candidate" for p in selected),
        "review_required": sum(p["status"] == "review_required" for p in selected),
    }
    summary = _fields(report["summary"], set(expected_summary), "recovery summary")
    if any(type(summary[key]) is not int or summary[key] != value for key, value in expected_summary.items()):
        raise ValueError("recovery summary disagrees with page records")
    return report


def validate_references(payload: object, *, page_count: int) -> dict:
    _integer(page_count, 1, 5_000, "PDF page count")
    manifest = _fields(payload, {"schema_version", "source_sha256", "pages"}, "reference manifest")
    _integer(manifest["schema_version"], 1, 1, "reference schema version")
    _hex_digest(manifest["source_sha256"], label="reference source digest")
    pages = manifest["pages"]
    if not isinstance(pages, list) or not 1 <= len(pages) <= ocr_evaluation.MAX_RECORDS:
        raise ValueError("references must contain 1..256 pages")
    numbers, records = [], []
    for page in pages:
        if not isinstance(page, dict) or not {"page_number", "reference"} <= set(page) or set(page) - {
            "page_number", "reference", "critical_tokens"
        }:
            raise ValueError("invalid reference-page fields")
        number = _integer(page["page_number"], 1, page_count, "reference page number")
        numbers.append(number)
        records.append({
            "id": f"page-{number:05d}", "reference": page["reference"], "prediction": "",
            "critical_tokens": page.get("critical_tokens", []),
        })
    _page_numbers(numbers, page_count)
    # Shared semantic checks without alignment work (including critical phrases).
    ocr_evaluation._validate({"schema_version": 1, "records": records})
    return manifest


def compare_recovery_report(recovery: object, references: object) -> dict:
    """Use references as the fixed universe; compare only explicitly paired rows."""
    report = validate_recovery_report(recovery)
    manifest = validate_references(references, page_count=report["page_count"])
    if manifest["source_sha256"] != report["source_sha256"]:
        raise ValueError("reference and recovery source digests differ")
    selected = {p["page_number"]: p for p in report["pages"]}
    deferred = {p["page_number"] for p in report["deferred_pages"]}
    reference_pages = {p["page_number"] for p in manifest["pages"]}
    baseline_records, retry_records, unpaired, pairs = [], [], [], []
    for reference in sorted(manifest["pages"], key=lambda p: p["page_number"]):
        number = reference["page_number"]
        page = selected.get(number)
        baseline_available = page is not None and page["original_text"] is not None
        retry_available = page is not None and page["status"] != "retry_failed"
        reasons = []
        if page is None:
            reasons.append("deferred" if number in deferred else "not_selected")
        else:
            if not baseline_available:
                reasons.append("original_unavailable")
            if not retry_available:
                reasons.append("retry_failed")
        if reasons:
            unpaired.append({"page_number": number, "reasons": reasons,
                             "baseline_available": baseline_available, "retry_available": retry_available})
            continue
        record = {"id": f"page-{number:05d}", "reference": reference["reference"],
                  "critical_tokens": reference.get("critical_tokens", [])}
        baseline_records.append({**record, "prediction": page["original_text"]})
        retry_records.append({**record, "prediction": page["candidate"]["text"]})
        pairs.append({"page_number": number, "record_id": record["id"], "baseline_kind": page["baseline_kind"]})
    comparison = compare_ocr(
        {"schema_version": 1, "records": baseline_records},
        {"schema_version": 1, "records": retry_records},
    ) if pairs else None
    missing_references = sorted(set(selected) - reference_pages)
    empty = sorted(n for n, p in selected.items() if p["status"] == "empty_candidate")
    coverage = {
        "reference_pages": len(reference_pages), "paired_pages": len(pairs),
        "unpaired_pages": unpaired, "unreferenced_selected_pages": missing_references,
        "unreferenced_deferred_pages": sorted(deferred - reference_pages),
        "deferred_pages": sorted(deferred), "empty_candidate_pages": empty,
    }
    return {
        "schema_version": 1, "kind": "ocr_recovery_comparison",
        "source_sha256": report["source_sha256"], "page_count": report["page_count"],
        "coverage": coverage, "paired_pages": pairs, "comparison": comparison,
        "requires_attention": bool(
            not pairs or unpaired or missing_references or deferred or empty
            or comparison and comparison["regression_detected"]),
        "scope": "paired referenced retry pages only; not whole-document accuracy",
        "reference_attestation": "operator supplied; source hashes match, authenticity not verified",
        "acceptance": "manual_review_required", "canonical_extraction_modified": False,
    }


def _load(path: Path, *, label: str, limit: int) -> tuple[dict, str]:
    try:
        raw, digest = _read_snapshot(path, label=label, max_bytes=limit)
        payload = _strict_json_bytes(raw, label=label, max_bytes=limit)
    except ValueError:
        # Shared parser errors may contain arbitrary field names or filenames.
        raise ValueError(f"{label} must be bounded strict UTF-8 JSON") from None
    return payload, digest


def compare_recovery_file(recovery_path: Path, reference_path: Path, output_path: Path) -> dict:
    """Compare two strict snapshots; create a new private text-free report."""
    paths = [Path(p).absolute() for p in (recovery_path, reference_path, output_path)]
    for path in paths:
        storage_policy.assert_no_link_components(path)
    for index, path in enumerate(paths):
        for other in paths[index + 1:]:
            if path.resolve() == other.resolve() or (
                path.exists() and other.exists() and os.path.samefile(path, other)
            ):
                raise ValueError("comparison inputs and output must be distinct")
    recovery_path, reference_path, output_path = paths
    with PathLease(
        output_path, backend="ocr-comparison", collection_name="report", operation="compare",
        timeout=0, resource_description="OCR comparison report",
    ):
        if output_path.exists():
            raise FileExistsError("comparison output exists; choose a new path")
        recovery, recovery_digest = _load(recovery_path, label="OCR recovery report", limit=MAX_RECOVERY_BYTES)
        references, reference_digest = _load(reference_path, label="OCR references", limit=MAX_REFERENCE_BYTES)
        result = compare_recovery_report(recovery, references)
        result["inputs"] = {"recovery_sha256": recovery_digest, "references_sha256": reference_digest}
        for path, digest, limit in (
            (recovery_path, recovery_digest, MAX_RECOVERY_BYTES),
            (reference_path, reference_digest, MAX_REFERENCE_BYTES),
        ):
            _, current = _read_snapshot(path, label="OCR comparison input", max_bytes=limit)
            if current != digest:
                raise RuntimeError("OCR comparison input changed before publication")
        storage_policy.atomic_write_private_json(
            output_path, result, indent=2, replace_fn=ocr_recovery._publish_new_report)
    return result
