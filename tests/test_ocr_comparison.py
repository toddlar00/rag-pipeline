from __future__ import annotations

import copy
import json
from pathlib import Path
import subprocess
import sys

import pytest

import ocr_comparison as comparison
import ocr_evaluation


PROJECT_ROOT = Path(__file__).resolve().parents[1]


def _record(identifier="synthetic", reference="alpha beta", prediction="alpha", critical_tokens=None):
    result = {"id": identifier, "reference": reference, "prediction": prediction}
    if critical_tokens is not None:
        result["critical_tokens"] = critical_tokens
    return result


def _payload(*records):
    return {"schema_version": 1, "records": list(records or [_record()])}


def _pair(reference, baseline, retry, *, critical_tokens=None):
    return (
        _payload(_record(reference=reference, prediction=baseline, critical_tokens=critical_tokens)),
        _payload(_record(reference=reference, prediction=retry, critical_tokens=critical_tokens)),
    )


def test_known_omission_improvement_has_retry_minus_baseline_metrics():
    report = comparison.compare_ocr(*_pair("alpha beta", "alpha", "alpha beta", critical_tokens=["beta"]))
    assert report["schema_version"] == 1
    assert report["kind"] == "ocr_accuracy_comparison"
    assert report["delta_direction"] == "retry_minus_baseline"
    record = report["records"][0]
    assert record["outcome"] == "improved"
    assert record["delta"]["character"]["deletions"] == -5
    assert record["delta"]["character"]["edit_distance"] == -5
    assert record["delta"]["character"]["error_rate"] == -0.5
    assert record["delta"]["word"]["deletions"] == -1
    assert record["delta"]["word"]["error_rate"] == -0.5
    assert record["delta"]["exact_match"] == 1
    assert record["delta"]["critical_tokens"][0]["missing_occurrences"] == -1
    assert record["delta"]["critical_tokens"][0]["occurrence_recall"] == 1
    assert report["summary"]["delta"]["exact_match_count"] == 1
    assert report["summary"]["delta"]["exact_match_rate"] == 1
    assert report["summary"]["outcomes"] == {"improved": 1, "unchanged": 0, "regressed": 0, "mixed": 0}
    assert report["regression_detected"] is False
    assert report["acceptance"] == "manual_review_required"


def test_regression_reports_positive_error_deltas():
    report = comparison.compare_ocr(*_pair("alpha beta", "alpha beta", "alpha", critical_tokens=["beta"]))
    record = report["records"][0]
    assert record["outcome"] == "regressed"
    assert record["regression_detected"] is True
    assert record["delta"]["character"]["edit_distance"] == 5
    assert record["delta"]["word"]["edit_distance"] == 1
    assert record["delta"]["exact_match"] == -1
    assert report["summary"]["regression_record_count"] == 1


def test_different_predictions_with_equal_tracked_counts_are_unchanged():
    report = comparison.compare_ocr(*_pair("cat", "bat", "hat"))
    assert report["records"][0]["outcome"] == "unchanged"
    assert report["regression_detected"] is False
    assert report["summary"]["delta"]["word"]["edit_distance"] == 0
    assert "counts are unchanged" in report["outcome_semantics"]
    assert report["acceptance"] == "manual_review_required"


def test_fewer_character_errors_but_more_word_errors_is_mixed():
    report = comparison.compare_ocr(*_pair("alphabet beta", "x beta", "alphabex betx"))
    record = report["records"][0]
    assert record["delta"]["character"]["edit_distance"] < 0
    assert record["delta"]["word"]["edit_distance"] > 0
    assert record["outcome"] == "mixed"
    assert report["regression_detected"] is True


def test_newly_lost_critical_token_is_not_hidden_by_other_critical_fixes():
    report = comparison.compare_ocr(*_pair(
        "CITATION REPORTER not", "xxxxxxxx yyyyyyyy not", "CITATION REPORTER",
        critical_tokens=["CITATION", "REPORTER", "not"],
    ))
    record = report["records"][0]
    for unit in ("character", "word"):
        assert record["delta"][unit]["edit_distance"] < 0
    assert record["delta"]["critical_occurrence_totals"]["missing_occurrences"] == -1
    assert record["delta"]["critical_tokens"][2]["missing_occurrences"] == 1
    assert record["outcome"] == "mixed"
    assert report["regression_detected"] is True


def test_new_extra_critical_occurrences_are_regression_signals():
    report = comparison.compare_ocr(*_pair("not", "not", "not not", critical_tokens=["not"]))
    record = report["records"][0]
    assert record["delta"]["critical_tokens"][0]["extra_occurrences"] == 1
    assert record["delta"]["critical_tokens"][0]["occurrence_recall"] == 0
    assert record["outcome"] == "regressed"


def test_global_improvement_cannot_hide_regression_on_one_page():
    baseline = _payload(
        _record("large", "alpha beta gamma", "x y z"),
        _record("negation", "not", "not", ["not"]),
    )
    retry = _payload(
        _record("large", "alpha beta gamma", "alpha beta gamma"),
        _record("negation", "not", "no", ["not"]),
    )
    report = comparison.compare_ocr(baseline, retry)
    assert report["summary"]["delta"]["character"]["error_rate"] < 0
    assert report["summary"]["delta"]["word"]["error_rate"] < 0
    assert report["summary"]["outcomes"]["improved"] == 1
    assert report["summary"]["outcomes"]["regressed"] == 1
    assert report["summary"]["regression_record_count"] == 1
    assert report["regression_detected"] is True


@pytest.mark.parametrize(("baseline", "retry", "outcome", "word_delta"), [
    ("", "", "unchanged", 0), ("", "invented text", "regressed", 2),
    ("invented text", "", "improved", -2),
    ("invented text", "invented", "improved", -1),
])
def test_empty_reference_comparison_uses_insertions_without_nonfinite_rates(baseline, retry, outcome, word_delta):
    report = comparison.compare_ocr(*_pair(" \n", baseline, retry))
    record = report["records"][0]
    assert record["outcome"] == outcome
    assert record["delta"]["word"]["insertions"] == word_delta
    assert record["delta"]["word"]["error_rate"] is None
    assert report["summary"]["delta"]["character"]["error_rate"] is None
    assert report["summary"]["delta"]["critical_occurrence_totals"]["occurrence_recall"] is None
    json.dumps(report, allow_nan=False)


def test_mixed_empty_and_nonempty_references_use_micro_denominators():
    baseline = _payload(_record("text", "one two", "one two"), _record("blank", "", ""))
    retry = _payload(_record("text", "one two", "one two"), _record("blank", "", "invented"))
    report = comparison.compare_ocr(baseline, retry)
    assert report["summary"]["delta"]["word"]["error_rate"] == 0.5
    assert report["records"][0]["delta"]["word"]["error_rate"] is None
    assert report["regression_detected"] is True


def test_ids_are_sorted_and_both_input_orders_produce_identical_reports():
    baseline = _payload(_record("z"), _record("a", "one two", "one"))
    retry = _payload(_record("a", "one two", "one two"), _record("z", prediction="alpha beta"))
    expected = comparison.compare_ocr(baseline, retry)
    assert [record["id"] for record in expected["records"]] == ["a", "z"]
    baseline["records"].reverse()
    retry["records"].reverse()
    assert comparison.compare_ocr(baseline, retry) == expected


def test_reference_and_critical_matching_uses_existing_normalization():
    baseline = _payload(_record(reference="  cafe\u0301\n rule ", prediction="café rule", critical_tokens=["cafe\u0301\t rule"]))
    retry = _payload(_record(reference="café   rule", prediction="café rule", critical_tokens=["café rule"]))
    report = comparison.compare_ocr(baseline, retry)
    assert report["records"][0]["outcome"] == "unchanged"
    assert report["records"][0]["baseline"]["exact_match"] is True
    assert report["normalization"] == ocr_evaluation.evaluate_ocr(baseline)["normalization"]


def test_omitted_and_explicit_empty_critical_lists_match():
    baseline = _payload(_record())
    retry = _payload(_record(critical_tokens=[]))
    assert comparison.compare_ocr(baseline, retry)["records"][0]["delta"]["critical_tokens"] == []


@pytest.mark.parametrize("retry", [
    _payload(_record("different")),
    _payload(_record(), _record("additional")),
])
def test_different_id_sets_are_rejected_without_intersection(retry):
    with pytest.raises(ValueError, match="identical record ID sets"):
        comparison.compare_ocr(_payload(_record()), retry)


@pytest.mark.parametrize("reference", ["Alpha beta", "alpha beta.", "alpha gamma"])
def test_case_punctuation_or_content_reference_changes_are_rejected(reference):
    with pytest.raises(ValueError, match="mismatched normalized references"):
        comparison.compare_ocr(_payload(), _payload(_record(reference=reference)))


@pytest.mark.parametrize("critical", [["beta", "alpha"], ["alpha"], []])
def test_critical_list_order_and_membership_must_match(critical):
    baseline = _payload(_record(critical_tokens=["alpha", "beta"]))
    retry = _payload(_record(critical_tokens=critical))
    with pytest.raises(ValueError, match="mismatched ordered critical"):
        comparison.compare_ocr(baseline, retry)


@pytest.mark.parametrize("invalid", [
    None, [], {}, {"schema_version": True, "records": [_record()]},
    {"schema_version": 1, "records": []},
    {"schema_version": 1, "records": [_record()], "PRIVATE_FIELD": "PRIVATE_VALUE"},
    _payload(_record(), _record()),
    _payload(_record(prediction=None)),
    _payload(_record(critical_tokens=["absent"])),
])
@pytest.mark.parametrize("side", ["baseline", "retry"])
def test_both_sides_reuse_strict_benchmark_validation(invalid, side):
    args = (invalid, _payload()) if side == "baseline" else (_payload(), invalid)
    with pytest.raises(ValueError) as error:
        comparison.compare_ocr(*args)
    assert "PRIVATE_FIELD" not in str(error.value)
    assert "PRIVATE_VALUE" not in str(error.value)
    assert "absent" not in str(error.value)


def test_mismatch_errors_do_not_echo_ids_or_transcriptions():
    baseline = _payload(_record("PRIVATE_ID", "PRIVATE_REFERENCE", "PRIVATE_BASELINE"))
    retry = _payload(_record("PRIVATE_ID", "OTHER_PRIVATE_REFERENCE", "PRIVATE_RETRY"))
    with pytest.raises(ValueError) as error:
        comparison.compare_ocr(baseline, retry)
    assert "PRIVATE_" not in str(error.value)


def test_combined_budget_is_checked_before_either_side_is_scored(monkeypatch):
    # Each side needs 3*3 character cells and one word cell: 20 together.
    monkeypatch.setattr(comparison, "MAX_COMPARISON_ALIGNMENT_CELLS", 19)
    monkeypatch.setattr(ocr_evaluation, "evaluate_ocr", lambda *_: pytest.fail("scoring must not start"))
    with pytest.raises(ValueError, match="combined alignment cell budget"):
        comparison.compare_ocr(*_pair("abc", "xyz", "uvw"))


def test_combined_budget_includes_all_records_and_both_sides(monkeypatch):
    monkeypatch.setattr(comparison, "MAX_COMPARISON_ALIGNMENT_CELLS", 39)
    baseline = _payload(_record("a", "abc", "xyz"), _record("b", "def", "uvw"))
    retry = _payload(_record("a", "abc", "pqr"), _record("b", "def", "stu"))
    monkeypatch.setattr(ocr_evaluation, "evaluate_ocr", lambda *_: pytest.fail("scoring must not start"))
    with pytest.raises(ValueError, match="combined alignment cell budget"):
        comparison.compare_ocr(baseline, retry)


def test_existing_per_record_budget_is_preserved_before_scoring(monkeypatch):
    monkeypatch.setattr(ocr_evaluation, "MAX_RECORD_ALIGNMENT_CELLS", 9)
    monkeypatch.setattr(ocr_evaluation, "evaluate_ocr", lambda *_: pytest.fail("scoring must not start"))
    with pytest.raises(ValueError, match="per-side alignment budget"):
        comparison.compare_ocr(*_pair("abc", "abc", "xyz"))


def test_combined_budget_exact_ceiling_and_trimmed_equal_ends_are_accepted(monkeypatch):
    monkeypatch.setattr(comparison, "MAX_COMPARISON_ALIGNMENT_CELLS", 4)
    reference = "a" * 5000 + "x"
    report = comparison.compare_ocr(*_pair(reference, "a" * 5000 + "y", "a" * 5000 + "z"))
    assert report["records"][0]["outcome"] == "unchanged"


def test_pair_contract_is_checked_before_scoring(monkeypatch):
    monkeypatch.setattr(ocr_evaluation, "evaluate_ocr", lambda *_: pytest.fail("scoring must not start"))
    with pytest.raises(ValueError, match="mismatched normalized references"):
        comparison.compare_ocr(_payload(), _payload(_record(reference="different")))


def test_output_reuses_exact_scorer_metrics_without_private_text_or_mutation():
    baseline, retry = _pair("PRIVATE_REFERENCE not", "PRIVATE_BASELINE not", "PRIVATE_RETRY not", critical_tokens=["not"])
    baseline_original, retry_original = copy.deepcopy(baseline), copy.deepcopy(retry)
    report = comparison.compare_ocr(baseline, retry)
    baseline_metrics = ocr_evaluation.evaluate_ocr(baseline)
    retry_metrics = ocr_evaluation.evaluate_ocr(retry)
    assert report["summary"]["baseline"] == baseline_metrics["summary"]
    assert report["summary"]["retry"] == retry_metrics["summary"]
    assert report["records"][0]["baseline"] == {
        key: value for key, value in baseline_metrics["records"][0].items() if key != "id"}
    assert report["records"][0]["retry"] == {
        key: value for key, value in retry_metrics["records"][0].items() if key != "id"}
    assert "PRIVATE_" not in json.dumps(report, allow_nan=False)
    assert "positions and context are not verified" in report["critical_occurrence_semantics"]
    assert baseline == baseline_original and retry == retry_original


def test_comparison_import_has_no_ocr_or_ml_runtime_side_effects():
    result = subprocess.run(
        [sys.executable, "-c", "import sys; import ocr_comparison; assert not ({'rag','torch','numpy','docling','rapidocr','pymupdf','requests'} & set(sys.modules))"],
        cwd=PROJECT_ROOT, capture_output=True, text=True, timeout=30,
    )
    assert result.returncode == 0, result.stderr
