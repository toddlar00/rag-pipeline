"""Bounded, dependency-light OCR transcription evaluation.

This measures a transcription against human-reviewed reference text. It does
not infer correctness from an OCR engine's confidence. NFC normalization and
whitespace collapsing preserve case, punctuation, and reading order. Character
units are Unicode code points; word units are whitespace-delimited tokens.
"""

from __future__ import annotations

import os
import re
import unicodedata
from dataclasses import dataclass
from pathlib import Path
from typing import Sequence

import evaluation_inputs
import storage_policy


SCHEMA_VERSION = 1
MAX_INPUT_BYTES = 16 * 1024 * 1024
MAX_RECORDS = 256
MAX_TEXT_CHARACTERS = 20_000
MAX_CRITICAL_TOKENS = 64
MAX_CRITICAL_CHARACTERS = 256
MAX_RECORD_ALIGNMENT_CELLS = 25_000_000
MAX_TOTAL_ALIGNMENT_CELLS = 250_000_000
_ID_RE = re.compile(r"[A-Za-z0-9][A-Za-z0-9._:-]{0,127}")


@dataclass(frozen=True)
class _Record:
    identifier: str
    reference: str
    prediction: str
    critical_tokens: tuple[str, ...]


def normalize_text(text: str) -> str:
    """Use NFC and collapse Unicode whitespace, preserving case/punctuation."""
    return " ".join(unicodedata.normalize("NFC", text).split())


def _text(value: object, *, label: str, maximum: int) -> str:
    if not isinstance(value, str) or len(value) > maximum:
        raise ValueError(f"{label} must be a string of at most {maximum} characters")
    try:
        value.encode("utf-8")
    except UnicodeError as exc:
        raise ValueError(f"{label} must contain valid Unicode text") from exc
    normalized = normalize_text(value)
    if len(normalized) > maximum:
        raise ValueError(f"{label} exceeds the normalized character limit")
    return normalized


def _occurrences(words: Sequence[str], phrase: Sequence[str]) -> int:
    """Count exact contiguous token sequences, including overlapping matches."""
    size = len(phrase)
    return sum(
        words[index:index + size] == phrase
        for index in range(len(words) - size + 1)
    )


def _validate(payload: object) -> tuple[_Record, ...]:
    if not isinstance(payload, dict) or set(payload) != {"schema_version", "records"}:
        raise ValueError("OCR input must contain only schema_version and records")
    version = payload["schema_version"]
    if type(version) is not int or version != SCHEMA_VERSION:
        raise ValueError("OCR input schema_version must be 1")
    records = payload["records"]
    if not isinstance(records, list) or not 1 <= len(records) <= MAX_RECORDS:
        raise ValueError(f"OCR records must be a list of 1 to {MAX_RECORDS} records")
    result = []
    identifiers = set()
    for index, record in enumerate(records):
        label = f"OCR record {index + 1}"
        required = {"id", "reference", "prediction"}
        if (not isinstance(record, dict) or not required <= set(record)
                or set(record) - required - {"critical_tokens"}):
            raise ValueError(
                f"{label} requires id, reference, prediction and optional critical_tokens")
        identifier = record["id"]
        if not isinstance(identifier, str) or _ID_RE.fullmatch(identifier) is None:
            raise ValueError(f"{label} id must be a safe ASCII identifier of 1 to 128 characters")
        if identifier in identifiers:
            raise ValueError(f"{label} has a duplicate id")
        identifiers.add(identifier)
        reference = _text(
            record["reference"], label=f"{label} reference", maximum=MAX_TEXT_CHARACTERS)
        prediction = _text(
            record["prediction"], label=f"{label} prediction", maximum=MAX_TEXT_CHARACTERS)
        raw_critical = record.get("critical_tokens", [])
        if not isinstance(raw_critical, list) or len(raw_critical) > MAX_CRITICAL_TOKENS:
            raise ValueError(f"{label} critical_tokens must be a list of at most {MAX_CRITICAL_TOKENS} strings")
        critical = []
        words = reference.split()
        for token_index, value in enumerate(raw_critical):
            token = _text(
                value, label=f"{label} critical token {token_index + 1}",
                maximum=MAX_CRITICAL_CHARACTERS)
            if not token or token in critical:
                raise ValueError(f"{label} critical tokens must be nonempty and unique after normalization")
            if not _occurrences(words, token.split()):
                raise ValueError(f"{label} critical token {token_index + 1} must occur in its reference")
            critical.append(token)
        result.append(_Record(identifier, reference, prediction, tuple(critical)))
    return tuple(result)


def _trim_equal_ends(reference: Sequence[str], prediction: Sequence[str]):
    start = 0
    bound = min(len(reference), len(prediction))
    while start < bound and reference[start] == prediction[start]:
        start += 1
    end_reference, end_prediction = len(reference), len(prediction)
    while (end_reference > start and end_prediction > start
           and reference[end_reference - 1] == prediction[end_prediction - 1]):
        end_reference -= 1
        end_prediction -= 1
    return reference[start:end_reference], prediction[start:end_prediction]


def _alignment_counts(reference: Sequence[str], prediction: Sequence[str]) -> dict:
    """Exact Levenshtein counts using linear row storage.

    Equal prefixes/suffixes are matched first. Remaining minimum-cost ties use
    substitution, deletion, then insertion, in that order. Counts from another
    equally optimal alignment may differ, but total edit distance will not.
    """
    reference, prediction = _trim_equal_ends(reference, prediction)
    if not reference:
        return {"substitutions": 0, "insertions": len(prediction), "deletions": 0}
    if not prediction:
        return {"substitutions": 0, "insertions": 0, "deletions": len(reference)}
    previous = [(index, 0, index, 0) for index in range(len(prediction) + 1)]
    for row, expected in enumerate(reference, 1):
        current = [(row, 0, 0, row)]
        for column, observed in enumerate(prediction, 1):
            diagonal = previous[column - 1]
            if expected == observed:
                current.append(diagonal)
                continue
            deletion = previous[column]
            insertion = current[column - 1]
            if diagonal[0] <= deletion[0] and diagonal[0] <= insertion[0]:
                current.append((diagonal[0] + 1, diagonal[1] + 1, diagonal[2], diagonal[3]))
            elif deletion[0] <= insertion[0]:
                current.append((deletion[0] + 1, deletion[1], deletion[2], deletion[3] + 1))
            else:
                current.append((insertion[0] + 1, insertion[1], insertion[2] + 1, insertion[3]))
        previous = current
    _, substitutions, insertions, deletions = previous[-1]
    return {"substitutions": substitutions, "insertions": insertions, "deletions": deletions}


def _error_metrics(reference: Sequence[str], prediction: Sequence[str]) -> dict:
    counts = _alignment_counts(reference, prediction)
    distance = sum(counts.values())
    return {
        "reference_units": len(reference), "prediction_units": len(prediction),
        **counts, "edit_distance": distance,
        "error_rate": distance / len(reference) if reference else None,
    }


def _critical_counts(reference_occurrences: int, prediction_occurrences: int) -> dict:
    matched = min(reference_occurrences, prediction_occurrences)
    return {
        "reference_occurrences": reference_occurrences,
        "prediction_occurrences": prediction_occurrences,
        "matched_occurrences": matched,
        "missing_occurrences": reference_occurrences - matched,
        "extra_occurrences": prediction_occurrences - matched,
        "occurrence_recall": matched / reference_occurrences if reference_occurrences else None,
    }


def _sum_critical(items: Sequence[dict]) -> dict:
    keys = ("reference_occurrences", "prediction_occurrences", "matched_occurrences",
            "missing_occurrences", "extra_occurrences")
    totals = {key: sum(item[key] for item in items) for key in keys}
    reference = totals["reference_occurrences"]
    totals["occurrence_recall"] = totals["matched_occurrences"] / reference if reference else None
    return totals


def evaluate_ocr(payload: object) -> dict:
    """Return content-free metrics for a validated v1 benchmark object.

    Rates are micro-averages and are null when their reference denominator is
    zero; insertion and empty-page exact-match counts still expose hallucination.
    Critical occurrence recall is a bag-of-occurrences diagnostic, not evidence
    that a token is correct in its position or context.
    """
    records = _validate(payload)
    total_cells = 0
    for index, record in enumerate(records):
        record_cells = 0
        for reference, prediction in (
                (record.reference, record.prediction),
                (record.reference.split(), record.prediction.split())):
            remaining_reference, remaining_prediction = _trim_equal_ends(reference, prediction)
            record_cells += len(remaining_reference) * len(remaining_prediction)
        total_cells += record_cells
        if record_cells > MAX_RECORD_ALIGNMENT_CELLS:
            raise ValueError(f"OCR record {index + 1} exceeds the alignment cell budget; split it into smaller regions")
        if total_cells > MAX_TOTAL_ALIGNMENT_CELLS:
            raise ValueError("OCR input exceeds the total alignment cell budget; evaluate smaller batches")

    results = []
    for record in records:
        reference_words, prediction_words = record.reference.split(), record.prediction.split()
        critical = [
            {"token_index": index, **_critical_counts(
                _occurrences(reference_words, token.split()),
                _occurrences(prediction_words, token.split()))}
            for index, token in enumerate(record.critical_tokens)
        ]
        results.append({
            "id": record.identifier,
            "exact_match": record.reference == record.prediction,
            "character": _error_metrics(record.reference, record.prediction),
            "word": _error_metrics(reference_words, prediction_words),
            "critical_tokens": critical,
            "critical_occurrence_totals": _sum_critical(critical),
        })
    exact_count = sum(item["exact_match"] for item in results)
    summary = {
        "record_count": len(results), "exact_match_count": exact_count,
        "exact_match_rate": exact_count / len(results),
    }
    for unit in ("character", "word"):
        totals = {key: sum(item[unit][key] for item in results) for key in (
            "reference_units", "prediction_units", "substitutions", "insertions",
            "deletions", "edit_distance")}
        denominator = totals["reference_units"]
        totals["error_rate"] = totals["edit_distance"] / denominator if denominator else None
        summary[unit] = totals
    summary["critical_occurrence_totals"] = _sum_critical([
        item["critical_occurrence_totals"] for item in results])
    return {
        "schema_version": SCHEMA_VERSION,
        "normalization": {
            "unicode": "NFC", "whitespace": "collapse",
            "case_sensitive": True, "punctuation_sensitive": True,
            "character_unit": "Unicode code point", "word_unit": "whitespace-delimited token",
        },
        "critical_occurrence_semantics": "exact token-sequence counts; positions and context are not verified",
        "summary": summary, "records": results,
    }


def evaluate_ocr_file(input_path: Path, output_path: Path) -> dict:
    """Read one strict snapshot and atomically publish private text-free metrics."""
    input_path, output_path = Path(input_path), Path(output_path)
    storage_policy.assert_no_link_components(input_path)
    storage_policy.assert_no_link_components(output_path)
    if (os.path.normcase(str(input_path.resolve(strict=False)))
            == os.path.normcase(str(output_path.resolve(strict=False)))
            or (input_path.exists() and output_path.exists()
                and os.path.samefile(input_path, output_path))):
        raise ValueError("OCR input and output paths must be distinct")
    try:
        raw, digest = evaluation_inputs._read_snapshot(
            input_path, label="OCR benchmark input", max_bytes=MAX_INPUT_BYTES)
    except ValueError as exc:
        # The shared snapshot reader may include a private filename in errors.
        raise ValueError(f"OCR input exceeds {MAX_INPUT_BYTES} bytes or is not a valid bounded artifact") from exc
    try:
        payload = evaluation_inputs._strict_json_bytes(
            raw, label="OCR benchmark input", max_bytes=MAX_INPUT_BYTES)
    except ValueError as exc:
        # Parser diagnostics can contain arbitrary duplicate JSON field names.
        raise ValueError("OCR input must be one bounded strict UTF-8 JSON object; duplicate fields and non-finite numbers are invalid") from exc
    report = evaluate_ocr(payload)
    report["input_sha256"] = digest
    storage_policy.atomic_write_private_json(output_path, report, indent=2)
    return report
