"""Content-free, paired comparisons of OCR benchmark transcriptions.

Both sides must describe the same records, normalized reference transcriptions,
and ordered critical entries. Comparison never accepts candidates or updates
source artifacts. It reports changes in measured counts, not an OCR confidence
estimate or a claim that critical text appears in the right position/context.
"""

from __future__ import annotations

import ocr_evaluation


MAX_COMPARISON_ALIGNMENT_CELLS = ocr_evaluation.MAX_TOTAL_ALIGNMENT_CELLS
_OUTCOMES = ("improved", "unchanged", "regressed", "mixed")


def _alignment_cells(record: ocr_evaluation._Record) -> int:
    cells = 0
    for reference, prediction in (
        (record.reference, record.prediction),
        (record.reference.split(), record.prediction.split()),
    ):
        reference, prediction = ocr_evaluation._trim_equal_ends(reference, prediction)
        cells += len(reference) * len(prediction)
    return cells


def _paired_inputs(baseline_payload: object, retry_payload: object) -> tuple[dict, dict]:
    baseline = {record.identifier: record for record in ocr_evaluation._validate(baseline_payload)}
    retry = {record.identifier: record for record in ocr_evaluation._validate(retry_payload)}
    if set(baseline) != set(retry):
        raise ValueError("OCR comparison requires identical record ID sets on both sides")
    baseline_records, retry_records = [], []
    cells = 0
    for index, identifier in enumerate(sorted(baseline), 1):
        before, after = baseline[identifier], retry[identifier]
        if before.reference != after.reference:
            raise ValueError(f"OCR comparison record {index} has mismatched normalized references")
        if before.critical_tokens != after.critical_tokens:
            raise ValueError(f"OCR comparison record {index} has mismatched ordered critical entries")
        for record, target in ((before, baseline_records), (after, retry_records)):
            record_cells = _alignment_cells(record)
            if record_cells > ocr_evaluation.MAX_RECORD_ALIGNMENT_CELLS:
                raise ValueError(f"OCR comparison record {index} exceeds a per-side alignment budget; split it into smaller regions")
            cells += record_cells
            if cells > MAX_COMPARISON_ALIGNMENT_CELLS:
                raise ValueError("OCR comparison exceeds the combined alignment cell budget; compare smaller batches")
            target.append({
                "id": record.identifier, "reference": record.reference,
                "prediction": record.prediction, "critical_tokens": list(record.critical_tokens),
            })
    return (
        {"schema_version": 1, "records": baseline_records},
        {"schema_version": 1, "records": retry_records},
    )


def _numeric_deltas(baseline: dict, retry: dict) -> dict:
    return {
        key: None if before is None or retry[key] is None else retry[key] - before
        for key, before in baseline.items()
    }


def _metric_deltas(baseline: dict, retry: dict) -> dict:
    return {
        key: _numeric_deltas(baseline[key], retry[key])
        for key in ("character", "word", "critical_occurrence_totals")
    }


def _record_comparison(baseline: dict, retry: dict) -> dict:
    deltas = _metric_deltas(baseline, retry)
    deltas["exact_match"] = int(retry["exact_match"]) - int(baseline["exact_match"])
    critical_deltas = []
    directions = [deltas[unit]["edit_distance"] for unit in ("character", "word")]
    for before, after in zip(baseline["critical_tokens"], retry["critical_tokens"]):
        critical = {
            "token_index": before["token_index"],
            **_numeric_deltas(
                {key: value for key, value in before.items() if key != "token_index"},
                {key: value for key, value in after.items() if key != "token_index"},
            ),
        }
        critical_deltas.append(critical)
        directions.extend(critical[key] for key in ("missing_occurrences", "extra_occurrences"))
    deltas["critical_tokens"] = critical_deltas
    better = any(value < 0 for value in directions)
    worse = any(value > 0 for value in directions)
    outcome = "mixed" if better and worse else "regressed" if worse else "improved" if better else "unchanged"
    return {
        "id": baseline["id"], "outcome": outcome, "regression_detected": worse,
        "baseline": {key: value for key, value in baseline.items() if key != "id"},
        "retry": {key: value for key, value in retry.items() if key != "id"},
        "delta": deltas,
    }


def compare_ocr(baseline_payload: object, retry_payload: object) -> dict:
    """Compare two strict v1 OCR benchmarks without silently dropping records.

    References and ordered critical entries must match after the scorer's NFC
    and whitespace normalization. IDs are sorted for deterministic pairing.
    Both sides share one 250-million alignment-cell budget, with the scorer's
    25-million per-record ceiling applied separately to each side. Work is
    counted after equal-end trimming, including character and word alignments,
    and checked for the entire pair before either side is scored.

    Deltas are retry minus baseline. Outcomes compare character/word edit
    distances and each critical entry's missing/extra occurrence counts. Any
    worsening raises regression_detected, even when aggregate rates improve.
    Unchanged means these counts are unchanged, not that predictions are equal.
    Empty-reference rate deltas remain null; insertion counts still matter.
    """
    baseline_input, retry_input = _paired_inputs(baseline_payload, retry_payload)
    baseline = ocr_evaluation.evaluate_ocr(baseline_input)
    retry = ocr_evaluation.evaluate_ocr(retry_input)
    records = [
        _record_comparison(before, after)
        for before, after in zip(baseline["records"], retry["records"])
    ]
    summary_delta = _metric_deltas(baseline["summary"], retry["summary"])
    for key in ("exact_match_count", "exact_match_rate"):
        summary_delta[key] = retry["summary"][key] - baseline["summary"][key]
    regressions = sum(record["regression_detected"] for record in records)
    return {
        "schema_version": 1, "kind": "ocr_accuracy_comparison",
        "delta_direction": "retry_minus_baseline",
        "normalization": baseline["normalization"],
        "critical_occurrence_semantics": baseline["critical_occurrence_semantics"],
        "outcome_semantics": "directions of edit distances and per-critical-entry missing/extra counts; unchanged means those counts are unchanged",
        "regression_detected": bool(regressions),
        "acceptance": "manual_review_required",
        "summary": {
            "record_count": len(records), "regression_record_count": regressions,
            "outcomes": {outcome: sum(record["outcome"] == outcome for record in records) for outcome in _OUTCOMES},
            "baseline": baseline["summary"], "retry": retry["summary"],
            "delta": summary_delta,
        },
        "records": records,
    }
