"""Source-bound, bounded OCR retry reports; never mutate canonical extraction.

Selection is a review heuristic, not an accuracy claim. Optional evidence is
operator-supplied, hash-bound page text/grades; it is not a publication receipt.
Heavy PDF/model dependencies are imported only by the runtime adapter.
"""

from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path
import json
import math
import os
import re
from typing import Protocol

import artifact_io
from evaluation_inputs import _hex_digest, _read_snapshot, _strict_json_bytes
from ocr_preprocessing import validate_metadata, validate_mode
from resource_lease import PathLease
import storage_policy


MAX_EVIDENCE_BYTES = 16 * 1024 * 1024
MAX_TEXT_CHARS = 100_000
MAX_DOCUMENT_PAGES = 5_000


class ReportCleanupError(RuntimeError):
    """The report was linked successfully but its staging-name cleanup failed."""


class PageReader(Protocol):
    page_count: int

    def native_text(self, page_number: int) -> str: ...

    def retry(self, page_number: int) -> dict: ...


@dataclass(frozen=True)
class RetryPolicy:
    dpi: int = 300
    max_pages: int = 5
    min_chars: int = 40
    max_pixels: int = 25_000_000
    max_side: int = 6_000
    preprocessing: str = "none"

    def __post_init__(self) -> None:
        validate_mode(self.preprocessing)
        for name, lower, upper in (
            ("dpi", 72, 600), ("max_pages", 1, 20),
            ("min_chars", 1, 10_000), ("max_pixels", 1, 25_000_000),
            ("max_side", 32, 6_000),
        ):
            value = getattr(self, name)
            if type(value) is not int or not lower <= value <= upper:
                raise ValueError(f"{name} must be an integer in {lower}..{upper}")


def _page_number(value: object) -> int:
    if type(value) is not int or not 1 <= value <= MAX_DOCUMENT_PAGES:
        raise ValueError("page_number must be a one-based integer in 1..5000")
    return value


def _page_text(value: object) -> str:
    if not isinstance(value, str) or len(value) > MAX_TEXT_CHARS:
        raise ValueError("page text must be a string of at most 100000 characters")
    # Reject unpaired surrogates before a report reaches the UTF-8 writer.
    try:
        value.encode("utf-8")
    except UnicodeError:
        raise ValueError("page text must contain valid Unicode") from None
    return value


def validate_evidence(payload: dict) -> dict:
    """Validate the explicit v1 page-evidence contract without coercions."""
    if not isinstance(payload, dict) or set(payload) != {
        "schema_version", "source_sha256", "pages"
    }:
        raise ValueError("evidence requires schema_version, source_sha256 and pages")
    if type(payload["schema_version"]) is not int or payload["schema_version"] != 1:
        raise ValueError("unsupported OCR evidence schema_version")
    _hex_digest(payload["source_sha256"], label="evidence source_sha256")
    pages = payload["pages"]
    if not isinstance(pages, list) or len(pages) > MAX_DOCUMENT_PAGES:
        raise ValueError("evidence pages must be a list of at most 5000 records")
    seen = set()
    for page in pages:
        if not isinstance(page, dict) or not {"page_number", "text"} <= set(page):
            raise ValueError("each evidence page requires page_number and text")
        if set(page) - {"page_number", "text", "low_grade", "ocr_confidence"}:
            raise ValueError("unknown evidence page field")
        number = _page_number(page["page_number"])
        if number in seen:
            raise ValueError("duplicate evidence page_number")
        seen.add(number)
        _page_text(page["text"])
        grade = page.get("low_grade")
        if grade is not None and (
            not isinstance(grade, str) or grade not in {"POOR", "FAIR", "GOOD", "EXCELLENT"}
        ):
            raise ValueError("low_grade must be POOR, FAIR, GOOD, EXCELLENT or null")
        score = page.get("ocr_confidence")
        if score is not None and (
            type(score) not in {int, float} or not 0 <= score <= 1
            or not math.isfinite(score)
        ):
            raise ValueError("ocr_confidence must be finite in 0..1 or null")
    return payload


def page_reasons(text: str | None, *, requested: bool, low_grade: str | None,
                 min_chars: int) -> list[str]:
    reasons = ["requested"] if requested else []
    if text is None:
        reasons.append("extraction_unavailable")
    elif not text.strip():
        reasons.append("empty_text")
    else:
        if "\ufffd" in text or re.search(r"\(cid:\d+\)", text, re.IGNORECASE):
            reasons.append("corrupt_text")
        if len("".join(text.split())) < min_chars:
            reasons.append("short_text")
    if low_grade == "POOR":
        reasons.append("poor_quality_grade")
    return reasons


def _candidate_snapshot(candidate: object, *, preprocessing: str = "none") -> dict:
    """Detach a bounded, finite candidate before retaining it for publication."""
    validate_mode(preprocessing)
    expected_fields = {
        "text", "lines", "mean_confidence", "raster", "engine"
    } | ({"preprocessing"} if preprocessing != "none" else set())
    if not isinstance(candidate, dict) or set(candidate) != expected_fields:
        raise ValueError("invalid OCR candidate fields")
    _page_text(candidate["text"])
    lines = candidate["lines"]
    if not isinstance(lines, list) or len(lines) > 10_000:
        raise ValueError("invalid OCR candidate line count")
    total_chars = 0
    for line in lines:
        if not isinstance(line, dict) or set(line) != {"text", "score", "box"}:
            raise ValueError("invalid OCR candidate line")
        total_chars += len(_page_text(line["text"]))
        if total_chars > MAX_TEXT_CHARS:
            raise ValueError("OCR candidate lines exceed text budget")
        score = line["score"]
        if type(score) not in {int, float} or not 0 <= score <= 1:
            raise ValueError("invalid OCR candidate score")
        box = line["box"]
        if not isinstance(box, list) or len(box) != 4:
            raise ValueError("invalid OCR candidate box")
        for point in box:
            if not isinstance(point, list) or len(point) != 2 or any(
                type(value) not in {int, float} or not 0 <= value <= 12_000
                for value in point
            ):
                raise ValueError("invalid OCR candidate box coordinate")
        area_twice = sum(
            box[index][0] * box[(index + 1) % 4][1]
            - box[(index + 1) % 4][0] * box[index][1]
            for index in range(4)
        )
        if area_twice == 0:
            raise ValueError("OCR candidate box must have positive area")
    mean = candidate["mean_confidence"]
    if mean is not None and (type(mean) not in {int, float} or not 0 <= mean <= 1):
        raise ValueError("invalid OCR candidate mean confidence")
    raster = candidate["raster"]
    if not isinstance(raster, dict) or set(raster) != {"width", "height", "dpi", "coordinate_system"}:
        raise ValueError("invalid OCR candidate raster")
    for key, maximum in (("width", 12_000), ("height", 12_000), ("dpi", 600)):
        if type(raster[key]) is not int or not 1 <= raster[key] <= maximum:
            raise ValueError("invalid OCR candidate raster dimension")
    expected_coordinates = (
        "rendered_image_pixels" if preprocessing == "none" else "preprocessed_image_pixels")
    if raster["coordinate_system"] != expected_coordinates:
        raise ValueError("unknown OCR candidate coordinate system")
    engine = candidate["engine"]
    if not isinstance(engine, dict) or set(engine) != {"name", "version", "min_score", "max_side"}:
        raise ValueError("invalid OCR candidate engine")
    if engine["name"] != "rapidocr" or not isinstance(engine["version"], str) or len(engine["version"]) > 128:
        raise ValueError("invalid OCR candidate engine identity")
    _page_text(engine["version"])
    if type(engine["min_score"]) not in {int, float} or not 0 <= engine["min_score"] <= 1:
        raise ValueError("invalid OCR candidate minimum score")
    if type(engine["max_side"]) is not int or not 32 <= engine["max_side"] <= 12_000:
        raise ValueError("invalid OCR candidate side limit")
    if candidate["text"] != "\n".join(line["text"] for line in lines):
        raise ValueError("OCR candidate text differs from its lines")
    expected_mean = sum(line["score"] for line in lines) / len(lines) if lines else None
    if (mean is None) != (expected_mean is None) or (
        mean is not None and not math.isclose(mean, expected_mean, rel_tol=1e-12, abs_tol=1e-12)
    ):
        raise ValueError("OCR candidate mean differs from its line scores")
    if any(
        point[0] > raster["width"] or point[1] > raster["height"]
        for line in lines for point in line["box"]
    ):
        raise ValueError("OCR candidate box exceeds its raster")
    if preprocessing != "none":
        metadata = validate_metadata(
            candidate["preprocessing"], width=raster["width"], height=raster["height"])
        if metadata["mode"] != preprocessing:
            raise ValueError("OCR candidate preprocessing mode differs from requested mode")
    return json.loads(json.dumps(candidate, allow_nan=False, ensure_ascii=True))


def _validate_candidate_policy(candidate: dict, policy: RetryPolicy) -> None:
    """Bind a structurally validated candidate to the declared retry settings."""
    raster = candidate["raster"]
    if (raster["dpi"] != policy.dpi or candidate["engine"]["max_side"] != policy.max_side
            or max(raster["width"], raster["height"]) > policy.max_side
            or raster["width"] * raster["height"] > policy.max_pixels):
        raise ValueError("candidate raster contradicts retry configuration")
    if policy.preprocessing != "none":
        from ocr_preprocessing import validate_metadata

        validate_metadata(
            candidate["preprocessing"], width=raster["width"], height=raster["height"],
            max_pixels=policy.max_pixels, max_side=policy.max_side)


def _inspect_recovery_pages(
    reader: PageReader, *, source_sha256: str, policy: RetryPolicy,
    requested_pages: tuple[int, ...] = (), evidence: dict | None = None,
) -> tuple[int, set[int], list[dict], list[dict]]:
    """Inspect and select without retrying; retain the established page order."""
    _hex_digest(source_sha256, label="source_sha256")
    count = _page_number(reader.page_count)
    requested = {_page_number(number) for number in requested_pages}
    if any(number > count for number in requested):
        raise ValueError("requested page is outside this PDF")
    evidence_pages = {}
    if evidence is not None:
        validate_evidence(evidence)
        if evidence["source_sha256"] != source_sha256:
            raise ValueError("evidence belongs to a different PDF generation")
        evidence_pages = {page["page_number"]: page for page in evidence["pages"]}
        if any(number > count for number in evidence_pages):
            raise ValueError("evidence page is outside this PDF")

    selected, deferred = [], []
    order = sorted(requested) + [n for n in range(1, count + 1) if n not in requested]
    for number in order:
        supplied = evidence_pages.get(number)
        baseline_error = None
        try:
            baseline = _page_text(
                supplied["text"] if supplied is not None else reader.native_text(number))
        except Exception:
            baseline = None
            baseline_error = "native_extraction_failed"
        grade = supplied.get("low_grade") if supplied is not None else None
        reasons = page_reasons(
            baseline, requested=number in requested, low_grade=grade,
            min_chars=policy.min_chars)
        if not reasons:
            continue
        if len(selected) >= policy.max_pages:
            deferred.append({"page_number": number, "reasons": reasons})
            continue
        selected.append({
            "page_number": number, "reasons": reasons,
            "original_text": baseline, "original_error": baseline_error,
            "baseline_kind": "operator_evidence" if supplied is not None else "pdf_native",
            "low_grade": grade,
            "original_ocr_confidence": supplied.get("ocr_confidence") if supplied else None,
        })

    return count, requested, selected, deferred


def _retry_recovery_page(reader: PageReader, page: dict, policy: RetryPolicy) -> dict:
    """Produce one detached outcome; exceptions stay local, cancellation escapes."""
    result = dict(page)
    try:
        candidate = _candidate_snapshot(
            reader.retry(page["page_number"]), preprocessing=policy.preprocessing)
        _validate_candidate_policy(candidate, policy)
    except Exception as error:
        result.update({
            "status": "retry_failed", "candidate": None,
            "error_code": (
                "retry_limit_or_validation" if isinstance(error, ValueError)
                else "retry_runtime_unavailable" if isinstance(error, ImportError)
                else "retry_runtime_failed"
            ),
        })
    else:
        result.update({
            "status": "review_required" if candidate["text"].strip() else "empty_candidate",
            "candidate": candidate, "error_code": None,
        })
    return result


def _assemble_recovery_report(*, source_sha256: str, policy: RetryPolicy,
                              count: int, requested: set[int], selected: list[dict],
                              deferred: list[dict]) -> dict:
    """Assemble the unchanged closed report after every selected outcome exists."""
    report = {
        "schema_version": 1, "kind": "ocr_recovery_review",
        "source_sha256": source_sha256, "page_count": count,
        "selection": {
            "requested_pages": sorted(requested), "max_pages": policy.max_pages,
            "min_chars": policy.min_chars, "order": "requested_then_page_number",
        },
        "retry_configuration": {
            "dpi": policy.dpi, "max_pixels": policy.max_pixels,
            "max_side": policy.max_side, "attempts_per_page": 1,
        },
        "pages": selected, "deferred_pages": deferred,
        "summary": {
            "inspected": count, "selected": len(selected), "deferred": len(deferred),
            "failed": sum(p["status"] == "retry_failed" for p in selected),
            "empty": sum(p["status"] == "empty_candidate" for p in selected),
            "review_required": sum(p["status"] == "review_required" for p in selected),
        },
        "canonical_extraction_modified": False,
        "accuracy_verified": False,
        "reading_order": "OCR engine order; not validated layout or table structure",
    }
    if policy.preprocessing != "none":
        report["schema_version"] = 2
        report["retry_configuration"]["preprocessing"] = policy.preprocessing
    return report


def build_recovery_report(
    reader: PageReader, *, source_sha256: str, policy: RetryPolicy,
    requested_pages: tuple[int, ...] = (), evidence: dict | None = None,
) -> dict:
    """Inspect all bounded pages, prioritize requested pages, retry once each.

    Candidate errors remain local to a page. Cancellation propagates. Only
    selected pages retain text; deferred pages retain reasons, not excerpts.
    """
    count, requested, selected, deferred = _inspect_recovery_pages(
        reader, source_sha256=source_sha256, policy=policy,
        requested_pages=requested_pages, evidence=evidence)
    outcomes = [_retry_recovery_page(reader, page, policy) for page in selected]
    return _assemble_recovery_report(
        source_sha256=source_sha256, policy=policy, count=count,
        requested=requested, selected=outcomes, deferred=deferred)


def _publish_new_report(temporary: Path, destination: Path) -> None:
    """Atomic no-clobber commit, including against noncooperating writers.

    Both names are on the same filesystem (the private writer stages beside
    the output). Filesystems without hard-link support fail closed.
    """
    os.link(temporary, destination)
    try:
        Path(temporary).unlink()
    except OSError:
        # Do not retry the commit: destination now exists and belongs to this
        # generation. The private writer will also attempt staging cleanup.
        raise ReportCleanupError("report created but staging cleanup failed") from None


def recover_pdf(
    pdf_path: Path, output_path: Path, *, policy: RetryPolicy | None = None,
    requested_pages: tuple[int, ...] = (), evidence_path: Path | None = None,
    reader_factory=None,
) -> dict:
    """Snapshot inputs, perform retries, reverify sources, publish a private report.

    Reports are create-only under a shared path lease. Existing files, including
    canonical outputs and earlier reports, are never overwritten. Publication
    needs hard-link support on the output filesystem; otherwise it fails closed.
    """
    policy = RetryPolicy() if policy is None else policy
    source, output = Path(pdf_path).absolute(), Path(output_path).absolute()
    evidence_path = Path(evidence_path).absolute() if evidence_path is not None else None
    for path in (source, output, evidence_path):
        if path is not None:
            storage_policy.assert_no_link_components(path)
    if output.resolve() in {source.resolve(), evidence_path.resolve() if evidence_path else None}:
        raise ValueError("report path must differ from input paths")
    if not source.is_file():
        raise ValueError("PDF input must be an existing regular file")
    with PathLease(
        output, backend="ocr-review", collection_name="report", operation="retry",
        timeout=0, resource_description="OCR review report",
    ):
        if output.exists():
            raise FileExistsError("report already exists; choose a new output path")
        evidence = None
        evidence_digest = None
        if evidence_path is not None:
            raw, evidence_digest = _read_snapshot(
                evidence_path, label="OCR evidence", max_bytes=MAX_EVIDENCE_BYTES)
            evidence = validate_evidence(_strict_json_bytes(
                raw, label="OCR evidence", max_bytes=MAX_EVIDENCE_BYTES))
        if reader_factory is None:
            from ocr_recovery_runtime import RapidOCRPageReader
            reader_factory = RapidOCRPageReader
        with artifact_io.immutable_file_snapshot(source, snapshot_name="source.pdf") as snapshot:
            options = {"dpi": policy.dpi, "max_pixels": policy.max_pixels, "max_side": policy.max_side}
            if policy.preprocessing != "none":
                options["preprocessing"] = policy.preprocessing
            with reader_factory(snapshot.path, **options) as reader:
                report = build_recovery_report(
                    reader, source_sha256=snapshot.sha256, policy=policy,
                    requested_pages=requested_pages, evidence=evidence)
            report["evidence_sha256"] = evidence_digest
            snapshot.verify()
            storage_policy.assert_no_link_components(source)
            artifact_io.hash_file_generation(source, expected_sha256=snapshot.sha256)
            if evidence_path is not None:
                _, current_digest = _read_snapshot(
                    evidence_path, label="OCR evidence", max_bytes=MAX_EVIDENCE_BYTES)
                if current_digest != evidence_digest:
                    raise RuntimeError("OCR evidence changed during retries")
            if output.exists():
                raise FileExistsError("report appeared during retries; refusing to overwrite")
            storage_policy.atomic_write_private_json(
                output, report, indent=2, replace_fn=_publish_new_report)
    return report
