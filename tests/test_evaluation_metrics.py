import json
import os

import pytest

import evaluation_metrics
from llm_runtime import (
    LLMRequest,
    LLMRuntime,
    LLMRuntimeConfig,
    ProviderSpec,
)


def test_token_estimate_and_percentiles_are_deterministic():
    assert evaluation_metrics.estimate_text_tokens("") == 0
    assert evaluation_metrics.estimate_text_tokens("abcde") == 2
    assert evaluation_metrics.percentile([1, 2, 9], 0.5) == 2
    assert evaluation_metrics.percentile([1, 3], 0.5) == 2
    assert evaluation_metrics.percentile([], 0.95) == 0


@pytest.mark.parametrize("quantile", [-0.1, 1.1, float("nan"), True])
def test_percentile_rejects_invalid_quantiles(quantile):
    with pytest.raises(ValueError, match="quantile"):
        evaluation_metrics.percentile([1], quantile)


def test_path_measurement_does_not_follow_symlinks(tmp_path):
    root = tmp_path / "index"
    root.mkdir()
    (root / "one.bin").write_bytes(b"1234")
    nested = root / "nested"
    nested.mkdir()
    (nested / "two.bin").write_bytes(b"567")
    outside = tmp_path / "outside.bin"
    outside.write_bytes(b"not-counted")
    link = root / "outside-link"
    try:
        link.symlink_to(outside)
    except (OSError, NotImplementedError):
        link = None

    measured = evaluation_metrics.measure_path(root)

    assert measured["bytes"] == 7
    assert measured["file_count"] == 2
    assert measured["directory_count"] == 2
    if link is not None:
        assert measured["skipped_symlinks"] == 1


def test_path_measurement_is_bounded(tmp_path):
    for index in range(3):
        (tmp_path / f"{index}.bin").write_bytes(b"x")

    measured = evaluation_metrics.measure_path(tmp_path, max_entries=2)

    assert measured["truncated"] is True
    assert measured["file_count"] == 2


def test_measurement_collector_uses_injected_clock_and_rss():
    clock_values = iter([1.0, 1.1, 1.4, 1.5])
    rss_values = iter([100, 150, 125])
    collector = evaluation_metrics.MeasurementCollector(
        clock=lambda: next(clock_values),
        rss_reader=lambda: next(rss_values),
        trace_python_memory=False,
    )

    collector.start()
    started = collector.begin_query()
    latency = collector.end_query(started)
    measured = collector.finish()

    assert latency == pytest.approx(300)
    assert measured["query_latency_ms"] == {
        "count": 1, "mean": 300.0, "p50": 300.0,
        "p95": 300.0, "max": 300.0,
    }
    assert measured["total_wall_ms"] == 500.0
    assert measured["sampled_peak_rss_bytes"] == 150


def test_parse_llm_report_and_project_explicit_rates(tmp_path):
    report = tmp_path / "llm-report.json"
    report.write_text(json.dumps({
        "schema_version": 2,
        "counts": {
            "exact_prompt_tokens": 100,
            "exact_completion_tokens": 20,
            "estimated_prompt_tokens": 40,
            "estimated_completion_tokens": 10,
            "exact_usage_attempts": 1,
            "estimated_usage_attempts": 1,
            "unknown_usage_attempts": 0,
        },
    }), encoding="utf-8")

    usage = evaluation_metrics.parse_llm_usage_report(report)
    costs = evaluation_metrics.project_costs(
        ["abcdefgh", "1234"],
        embedding_rate_per_million=2.0,
        llm_usage=usage,
        llm_input_rate_per_million=3.0,
        llm_output_rate_per_million=6.0,
    )

    assert usage["input_tokens"] == 140
    assert usage["output_tokens"] == 30
    assert len(usage["report_sha256"]) == 64
    assert costs["rates_source"] == "caller_supplied"
    assert costs["embedding"]["input_tokens"] == 3
    assert costs["embedding"]["projected_usd"] == 0.000006
    assert costs["llm"]["status"] == "estimated"
    assert costs["llm"]["projected_usd"] == 0.0006


def test_current_runtime_report_is_accepted_by_evaluation_parser(tmp_path):
    report = tmp_path / "llm-report.json"
    runtime = LLMRuntime(LLMRuntimeConfig(
        cache_mode="off", cache_dir=tmp_path / "cache",
        report_path=report))
    runtime.execute(
        LLMRequest(prompt="abcd", operation="test.eval_report"),
        [ProviderSpec(
            name="test", model="model", endpoint_id="test-endpoint",
            invoke=lambda _request: "answer")],
    )
    runtime.write_report()

    usage = evaluation_metrics.parse_llm_usage_report(report)

    assert evaluation_metrics.LLM_RUNTIME_REPORT_SCHEMA_VERSION == 5
    assert usage["input_tokens"] > 0
    assert usage["output_tokens"] > 0
    assert usage["estimated_usage_attempts"] == 1


def test_costs_are_null_without_rates_and_offline_embeddings_are_not_used():
    costs = evaluation_metrics.project_costs(
        ["query"], embedding_requests=False)

    assert costs["embedding"]["status"] == "not_used"
    assert costs["embedding"]["requests"] == 0
    assert costs["embedding"]["projected_usd"] is None
    assert costs["llm"]["status"] == "not_run"
    assert costs["llm"]["projected_usd"] is None


def test_llm_zero_attempt_report_is_not_mislabeled_exact(tmp_path):
    report = tmp_path / "empty-llm-report.json"
    report.write_text(json.dumps({
        "schema_version": 2,
        "counts": {
            "exact_prompt_tokens": 0,
            "exact_completion_tokens": 0,
            "estimated_prompt_tokens": 0,
            "estimated_completion_tokens": 0,
            "exact_usage_attempts": 0,
            "estimated_usage_attempts": 0,
            "unknown_usage_attempts": 0,
        },
    }), encoding="utf-8")

    usage = evaluation_metrics.parse_llm_usage_report(report)
    costs = evaluation_metrics.project_costs(
        [], llm_usage=usage, llm_input_rate_per_million=1,
        llm_output_rate_per_million=2)

    assert costs["llm"]["status"] == "not_run"
    assert costs["llm"]["projected_usd"] is None


def test_unknown_llm_usage_never_emits_a_misleading_partial_price():
    costs = evaluation_metrics.project_costs(
        [],
        llm_usage={
            "input_tokens": 100,
            "output_tokens": 20,
            "exact_usage_attempts": 1,
            "estimated_usage_attempts": 0,
            "unknown_usage_attempts": 1,
        },
        llm_input_rate_per_million=1,
        llm_output_rate_per_million=2,
    )

    assert costs["llm"]["status"] == "partial_unknown_usage"
    assert costs["llm"]["projected_usd"] is None


@pytest.mark.parametrize("payload", [
    {"schema_version": 1, "counts": {}},
    {"schema_version": 2, "counts": {}},
])
def test_llm_usage_report_requires_current_schema_and_all_counts(
        tmp_path, payload):
    report = tmp_path / "bad-report.json"
    report.write_text(json.dumps(payload), encoding="utf-8")

    with pytest.raises(ValueError, match="schema version|missing count"):
        evaluation_metrics.parse_llm_usage_report(report)


def test_llm_usage_report_rejects_tokens_without_matching_attempts(tmp_path):
    report = tmp_path / "inconsistent-report.json"
    report.write_text(json.dumps({
        "schema_version": 2,
        "counts": {
            "exact_prompt_tokens": 1,
            "exact_completion_tokens": 0,
            "estimated_prompt_tokens": 0,
            "estimated_completion_tokens": 0,
            "exact_usage_attempts": 0,
            "estimated_usage_attempts": 0,
            "unknown_usage_attempts": 0,
        },
    }), encoding="utf-8")

    with pytest.raises(ValueError, match="tokens without exact attempts"):
        evaluation_metrics.parse_llm_usage_report(report)


@pytest.mark.parametrize("rate", [-1, float("inf"), True, "1"])
def test_cost_projection_rejects_ambiguous_rates(rate):
    with pytest.raises(ValueError, match="rate"):
        evaluation_metrics.project_costs(
            ["query"], embedding_rate_per_million=rate)


def test_process_rss_is_none_or_nonnegative_integer():
    rss = evaluation_metrics.process_rss_bytes()
    if os.name == "nt":
        assert isinstance(rss, int) and rss > 0
        return
    assert rss is None or (
        isinstance(rss, int) and not isinstance(rss, bool) and rss >= 0)


@pytest.mark.skipif(os.name == "nt", reason="symlink permissions vary on Windows")
def test_measuring_a_symlink_never_counts_its_target(tmp_path):
    target = tmp_path / "target"
    target.write_bytes(b"secret")
    link = tmp_path / "link"
    link.symlink_to(target)

    measured = evaluation_metrics.measure_path(link)

    assert measured["bytes"] == 0
    assert measured["file_count"] == 0
    assert measured["skipped_symlinks"] == 1


def test_paired_bootstrap_is_deterministic():
    baseline = [0.0, 0.0, 1.0, 0.0, 1.0, 0.0]
    candidate = [1.0, 1.0, 1.0, 0.0, 1.0, 1.0]
    first = evaluation_metrics.paired_bootstrap(baseline, candidate)
    second = evaluation_metrics.paired_bootstrap(baseline, candidate)
    assert first == second
    assert first["pairs"] == 6
    assert first["resamples"] == (
        evaluation_metrics.PAIRED_BOOTSTRAP_DEFAULT_RESAMPLES)
    assert first["seed"] == (
        evaluation_metrics.PAIRED_BOOTSTRAP_DEFAULT_SEED)
    assert first["mean_delta"] == pytest.approx(3 / 6, abs=1e-6)
    assert first["ci_low"] <= first["mean_delta"] <= first["ci_high"]
    assert 0.0 <= first["p_value"] <= 1.0


def test_paired_bootstrap_identical_inputs_are_null():
    result = evaluation_metrics.paired_bootstrap(
        [0.5, 0.25, 1.0], [0.5, 0.25, 1.0])
    assert result["mean_delta"] == 0.0
    assert result["ci_low"] == 0.0 and result["ci_high"] == 0.0
    assert result["p_value"] == 1.0


def test_paired_bootstrap_clear_improvement_is_significant():
    baseline = [0.0] * 30
    candidate = [1.0] * 30
    result = evaluation_metrics.paired_bootstrap(baseline, candidate)
    assert result["mean_delta"] == 1.0
    assert result["ci_low"] == 1.0 and result["ci_high"] == 1.0
    assert result["p_value"] < 0.05


def test_paired_bootstrap_rejects_bad_inputs():
    with pytest.raises(ValueError):
        evaluation_metrics.paired_bootstrap([1.0], [1.0, 0.0])
    with pytest.raises(ValueError):
        evaluation_metrics.paired_bootstrap([], [])
    with pytest.raises(ValueError):
        evaluation_metrics.paired_bootstrap([float("nan")], [0.0])
    with pytest.raises(ValueError):
        evaluation_metrics.paired_bootstrap([1.0], [0.0], resamples=0)
    with pytest.raises(ValueError):
        evaluation_metrics.paired_bootstrap([1.0], [0.0], resamples=True)
    with pytest.raises(ValueError):
        evaluation_metrics.paired_bootstrap([1.0], [0.0], confidence=1.0)
    with pytest.raises(ValueError):
        evaluation_metrics.paired_bootstrap([1.0], [0.0], confidence=0.0)
