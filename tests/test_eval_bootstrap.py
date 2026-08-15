"""Paired-bootstrap significance wiring for eval compare mode."""
from __future__ import annotations

import pytest

import eval as retrieval_eval
import evaluation_metrics


def _detail(index, sha, **metrics):
    return {"query_index": index, "query_sha256": sha,
            "metrics": metrics}


def test_paired_metric_values_match_by_identity():
    baseline = [_detail(1, "a", mrr=1.0), _detail(2, "b", mrr=0.0),
                _detail(3, "c", mrr=0.5)]
    candidate = [_detail(3, "c", mrr=1.0), _detail(1, "a", mrr=0.0),
                 _detail(2, "MISMATCH", mrr=1.0)]
    base_values, cand_values, excluded = (
        retrieval_eval._paired_metric_values(baseline, candidate, "mrr"))
    assert base_values == [1.0, 0.5]
    assert cand_values == [0.0, 1.0]
    assert excluded == 1


def test_paired_metric_values_exclude_missing_and_bad_values():
    baseline = [_detail(1, "a", mrr=1.0), _detail(2, "b"),
                _detail(3, "c", mrr=True), {"metrics": {"mrr": 1.0}}]
    candidate = [_detail(1, "a", mrr=0.25), _detail(2, "b", mrr=1.0),
                 _detail(3, "c", mrr=1.0)]
    base_values, cand_values, excluded = (
        retrieval_eval._paired_metric_values(baseline, candidate, "mrr"))
    assert base_values == [1.0]
    assert cand_values == [0.25]
    assert excluded == 3


def test_compare_significance_shape_and_determinism():
    reports = [
        {"label": "Vector only", "query_details": [
            _detail(1, "a", mrr=0.0), _detail(2, "b", mrr=0.0)]},
        {"label": "Hybrid (BM25+vector)", "query_details": [
            _detail(1, "a", mrr=1.0), _detail(2, "b", mrr=1.0)]},
        {"label": "Broken", "error": {"type": "RuntimeError"}},
    ]
    block = retrieval_eval._compare_significance(reports, ["mrr", "absent"])
    assert block["baseline"] == "Vector only"
    assert block["seed"] == (
        evaluation_metrics.PAIRED_BOOTSTRAP_DEFAULT_SEED)
    assert block["comparison_count"] == 1
    assert block["multiplicity"] == "uncorrected per-metric tests"
    [comparison] = block["comparisons"]
    assert comparison["candidate"] == "Hybrid (BM25+vector)"
    assert comparison["metrics"]["mrr"]["mean_delta"] == 1.0
    assert comparison["metrics"]["mrr"]["excluded_pairs"] == 0
    assert comparison["metrics"]["absent"]["unavailable"]
    assert block == retrieval_eval._compare_significance(
        reports, ["mrr", "absent"])


def test_compare_significance_without_baseline_is_unavailable():
    reports = [
        {"label": "Vector only", "error": {"type": "RuntimeError"}},
        {"label": "Hybrid (BM25+vector)", "query_details": [
            _detail(1, "a", mrr=1.0)]},
    ]
    block = retrieval_eval._compare_significance(reports, ["mrr"])
    assert block["unavailable"] == "baseline configuration failed"
    assert block["comparisons"] == []


def test_print_compare_significance_is_ascii_and_marks(capsys):
    reports = [
        {"label": "Vector only", "query_details": [
            _detail(index, str(index), mrr=0.0)
            for index in range(1, 31)]},
        {"label": "Hybrid (BM25+vector)", "query_details": [
            _detail(index, str(index), mrr=1.0)
            for index in range(1, 31)]},
    ]
    block = retrieval_eval._compare_significance(reports, ["mrr"])
    retrieval_eval._print_compare_significance(block)
    output = capsys.readouterr().out
    assert "Paired bootstrap vs Vector only" in output
    assert "delta +1.000" in output
    assert " *" in output
    assert "markers: uncorrected per-metric tests (1 comparisons)" in output
    assert output.isascii()


def test_bootstrap_flag_requires_compare(tmp_path, capsys):
    chunks = tmp_path / "chunks.jsonl"
    chunks.write_text("", encoding="utf-8")
    with pytest.raises(SystemExit) as excinfo:
        retrieval_eval.main([
            "--queries", str(chunks), "--chunks", str(chunks),
            "--bootstrap"])
    assert excinfo.value.code == 2
    assert "--bootstrap requires --compare" in capsys.readouterr().err
