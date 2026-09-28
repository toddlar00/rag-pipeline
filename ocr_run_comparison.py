"""Compare actual OCR candidates from two source-bound recovery runs.

References define a fixed page universe. Missing candidates remain visible in
two-sided coverage; native extraction is never substituted for a candidate.
This layer performs no image processing, model loading, or automatic acceptance.
"""

from __future__ import annotations

import copy
import os
from pathlib import Path

from evaluation_inputs import _read_snapshot
from ocr_comparison import compare_ocr
import ocr_recovery
import ocr_recovery_comparison
from resource_lease import PathLease
import storage_policy


MAX_RECOVERY_BYTES = ocr_recovery_comparison.MAX_RECOVERY_BYTES
MAX_REFERENCE_BYTES = ocr_recovery_comparison.MAX_REFERENCE_BYTES
_AVAILABLE = {"candidate_available", "empty_candidate"}
_SETTING_FIELDS = (
    ("selection", "requested_pages"), ("selection", "max_pages"),
    ("selection", "min_chars"), ("selection", "order"),
    ("retry_configuration", "dpi"), ("retry_configuration", "max_pixels"),
    ("retry_configuration", "max_side"), ("retry_configuration", "attempts_per_page"),
    ("retry_configuration", "preprocessing"),
)


def _settings(report: dict) -> dict:
    settings = {
        "selection": copy.deepcopy(report["selection"]),
        "retry_configuration": dict(report["retry_configuration"]),
    }
    settings["retry_configuration"].setdefault("preprocessing", "none")
    return settings


def _status(number: int, selected: dict, deferred: set[int]) -> str:
    if number in selected:
        status = selected[number]["status"]
        return "candidate_available" if status == "review_required" else status
    return "deferred" if number in deferred else "not_selected"


def _coverage(selected: dict, deferred: set[int], reference_pages: set[int]) -> dict:
    return {
        "selected_pages": sorted(selected),
        "failed_pages": sorted(n for n, page in selected.items() if page["status"] == "retry_failed"),
        "deferred_pages": sorted(deferred),
        "empty_candidate_pages": sorted(n for n, page in selected.items() if page["status"] == "empty_candidate"),
        "unreferenced_selected_pages": sorted(set(selected) - reference_pages),
        "unreferenced_deferred_pages": sorted(deferred - reference_pages),
    }


def compare_recovery_runs(baseline: object, retry: object, references: object) -> dict:
    """Compare paired candidate text while retaining every reference-page status.

    Both complete v1/v2 reports are validated before pairing. Sources and page
    counts must match; differing experiment settings are disclosed and allowed.
    True empty candidates are scored as empty text. Failed, deferred, and
    unselected candidates are never represented as empty predictions. The shared
    comparison core enforces its combined alignment budget and detects a
    regression on any paired page or critical occurrence, even if totals improve.
    """
    baseline = ocr_recovery_comparison.validate_recovery_report(baseline)
    retry = ocr_recovery_comparison.validate_recovery_report(retry)
    if baseline["source_sha256"] != retry["source_sha256"]:
        raise ValueError("OCR run comparison requires matching source digests")
    if baseline["page_count"] != retry["page_count"]:
        raise ValueError("OCR run comparison requires matching PDF page counts")
    references = ocr_recovery_comparison.validate_references(references, page_count=baseline["page_count"])
    if references["source_sha256"] != baseline["source_sha256"]:
        raise ValueError("OCR run comparison references must match the source digest")

    selected = [{page["page_number"]: page for page in report["pages"]} for report in (baseline, retry)]
    deferred = [{page["page_number"] for page in report["deferred_pages"]} for report in (baseline, retry)]
    reference_pages = {page["page_number"] for page in references["pages"]}
    records = [[], []]
    pairs, unpaired, engine_differences, library_differences = [], [], [], []
    for reference in sorted(references["pages"], key=lambda page: page["page_number"]):
        number = reference["page_number"]
        statuses = [_status(number, pages, delayed) for pages, delayed in zip(selected, deferred)]
        if any(status not in _AVAILABLE for status in statuses):
            unpaired.append({
                "page_number": number, "baseline_status": statuses[0], "retry_status": statuses[1],
            })
            continue
        record = {
            "id": f"page-{number:05d}", "reference": reference["reference"],
            "critical_tokens": reference.get("critical_tokens", []),
        }
        for target, pages in zip(records, selected):
            target.append({**record, "prediction": pages[number]["candidate"]["text"]})
        pairs.append({"page_number": number, "record_id": record["id"]})
        before, after = (pages[number]["candidate"] for pages in selected)
        if before["engine"] != after["engine"]:
            engine_differences.append(number)
        if ("preprocessing" in before and "preprocessing" in after
                and before["preprocessing"]["libraries"] != after["preprocessing"]["libraries"]):
            library_differences.append(number)

    comparison = compare_ocr(
        {"schema_version": 1, "records": records[0]},
        {"schema_version": 1, "records": records[1]},
    ) if pairs else None
    side_coverage = [_coverage(pages, delayed, reference_pages) for pages, delayed in zip(selected, deferred)]
    baseline_settings, retry_settings = _settings(baseline), _settings(retry)
    differences = [
        f"{section}.{field}" for section, field in _SETTING_FIELDS
        if baseline_settings[section][field] != retry_settings[section][field]
    ]
    unresolved = any(
        side[key] for side in side_coverage
        for key in ("unreferenced_selected_pages", "deferred_pages", "empty_candidate_pages")
    )
    return {
        "schema_version": 1, "kind": "ocr_run_comparison",
        "source_sha256": baseline["source_sha256"], "page_count": baseline["page_count"],
        "coverage": {
            "reference_pages": len(reference_pages), "paired_pages": len(pairs),
            "unpaired_pages": unpaired, "baseline": side_coverage[0], "retry": side_coverage[1],
        },
        "paired_pages": pairs, "comparison": comparison,
        "run_settings": {"baseline": baseline_settings, "retry": retry_settings},
        "setting_differences": differences,
        "confounders": {
            "evidence_binding_differs": baseline["evidence_sha256"] != retry["evidence_sha256"],
            "paired_engine_settings_differ": bool(engine_differences),
            "paired_engine_difference_pages": engine_differences,
            "paired_preprocessing_libraries_differ": bool(library_differences),
            "paired_preprocessing_library_difference_pages": library_differences,
        },
        "requires_attention": bool(
            not pairs or unpaired or unresolved or comparison and comparison["regression_detected"]),
        "scope": "paired referenced recovery candidates only; not whole-document accuracy or a causal attribution to one setting",
        "reference_attestation": "operator supplied; source hashes match, authenticity not verified",
        "acceptance": "manual_review_required", "canonical_extraction_modified": False,
    }


def compare_recovery_run_files(
        baseline_path: Path, retry_path: Path, reference_path: Path, output_path: Path) -> dict:
    """Compare three strict snapshots and create one private no-clobber report."""
    paths = [Path(path).absolute() for path in (baseline_path, retry_path, reference_path, output_path)]
    for path in paths:
        storage_policy.assert_no_link_components(path)
    for index, path in enumerate(paths):
        for other in paths[index + 1:]:
            if path.resolve() == other.resolve() or (
                path.exists() and other.exists() and os.path.samefile(path, other)
            ):
                raise ValueError("OCR run comparison inputs and output must be distinct")
    baseline_path, retry_path, reference_path, output_path = paths
    with PathLease(
        output_path, backend="ocr-comparison", collection_name="report", operation="compare",
        timeout=0, resource_description="OCR run comparison report",
    ):
        if output_path.exists():
            raise FileExistsError("OCR run comparison output exists; choose a new path")
        input_specs = (
            (baseline_path, "baseline_recovery_sha256", "baseline OCR recovery report", MAX_RECOVERY_BYTES),
            (retry_path, "retry_recovery_sha256", "retry OCR recovery report", MAX_RECOVERY_BYTES),
            (reference_path, "references_sha256", "OCR references", MAX_REFERENCE_BYTES),
        )
        snapshots = [
            ocr_recovery_comparison._load(path, label=label, limit=limit)
            for path, _, label, limit in input_specs
        ]
        result = compare_recovery_runs(*(payload for payload, _ in snapshots))
        result["inputs"] = {spec[1]: digest for spec, (_, digest) in zip(input_specs, snapshots)}
        for (path, _, _, limit), (_, digest) in zip(input_specs, snapshots):
            try:
                _, current_digest = _read_snapshot(path, label="OCR run comparison input", max_bytes=limit)
            except ValueError:
                raise ValueError("OCR run comparison input became an invalid bounded artifact") from None
            if current_digest != digest:
                raise RuntimeError("OCR run comparison input changed before publication")
        storage_policy.atomic_write_private_json(
            output_path, result, indent=2, replace_fn=ocr_recovery._publish_new_report)
    return result
