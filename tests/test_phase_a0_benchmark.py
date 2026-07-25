from __future__ import annotations

from copy import deepcopy
import json
import os
from pathlib import Path
import subprocess

import pytest

from tools import benchmark_phase_a0 as benchmark


def _diagnostics(
        *, wall: float, rss: int | None = 100,
        loaded: int = 20, first_party: int = 5,
        output: dict | None = None,
) -> dict:
    return {
        "wall_time_ms": wall,
        "peak_rss_bytes": rss,
        "loaded_module_count": loaded,
        "first_party_loaded_count": first_party,
        "first_party_loaded_sha256": "c" * 64,
        "output": output,
    }


def _valid_report(
        *, contract: dict | None = None, marker: str = "a",
        scenario_name: str = "cold_import_rag",
) -> dict:
    selected_contract = ({
        "module": "rag",
        "required_symbols": {
            "PipelinePaths": True,
            "export_markdown": True,
            "main": True,
            "search_index": True,
        },
    } if contract is None else contract)
    scenario = {
        "name": scenario_name,
        "repetitions": 5,
        "contract": selected_contract,
        "contract_sha256": benchmark._sha256_bytes(
            benchmark._canonical_bytes(selected_contract)),
        "diagnostics": {
            "wall_time_ms": {
                "minimum": 1.0, "median": 2.0,
                "p95": 3.0, "maximum": 3.0,
            },
            "peak_rss_bytes": {
                "available": True,
                "minimum": 10, "median": 20,
                "p95": 30, "maximum": 30,
            },
            "loaded_module_count": {
                "minimum": 4, "median": 4,
                "p95": 4, "maximum": 4,
            },
            "first_party_loaded_count": {
                "minimum": 2, "median": 2,
                "p95": 2, "maximum": 2,
            },
            "first_party_loaded_sha256": "d" * 64,
            "output": None,
        },
    }
    report = {
        "schema_version": benchmark.REPORT_SCHEMA_VERSION,
        "benchmark": benchmark.BENCHMARK_NAME,
        "source": {
            "commit": marker * 40,
            "worktree_clean": True,
            "tracked_diff_sha256": "b" * 64,
        },
        "runtime": {
            "implementation": "CPython",
            "python_version": "3.12.1",
            "pointer_bits": 64,
        },
        "system": {
            "os": "TestOS",
            "os_release": "1",
            "machine": "test",
            "processor": "test",
            "logical_cpu_count": 4,
            "total_memory_bytes": 1024,
        },
        "inputs": [{
            "name": "model-artifacts.lock.json",
            "bytes": 10,
            "sha256": "e" * 64,
        }],
        "repetitions": 5,
        "scenarios": [scenario],
    }
    report["report_sha256"] = benchmark._sha256_bytes(
        benchmark._canonical_bytes(report))
    return report


def test_numeric_summary_uses_nearest_rank_p95_and_rejects_bad_samples():
    assert benchmark._numeric_summary([5, 1, 4, 2, 3]) == {
        "minimum": 1,
        "median": 3,
        "p95": 5,
        "maximum": 5,
    }
    assert benchmark._numeric_summary([1.1119, 2.2229]) == {
        "minimum": 1.112,
        "median": 1.667,
        "p95": 2.223,
        "maximum": 2.223,
    }
    for values in ([], [True], [-1], [float("nan")], [float("inf")]):
        with pytest.raises(benchmark.PhaseA0BenchmarkError):
            benchmark._numeric_summary(values)


def test_summarize_runs_separates_contract_from_diagnostic_variability():
    output = {
        "stdout": {"bytes": 3, "lines": 1, "sha256": "a" * 64},
        "stderr": {"bytes": 0, "lines": 0, "sha256": "b" * 64},
    }
    runs = [
        {
            "contract": {"exit_code": 0},
            "diagnostics": _diagnostics(
                wall=float(value), rss=value * 10,
                loaded=20 + value, output=output),
        }
        for value in range(1, 6)
    ]

    summary = benchmark.summarize_runs("cli_help", runs)

    assert summary["repetitions"] == 5
    assert summary["contract"] == {"exit_code": 0}
    assert summary["diagnostics"]["wall_time_ms"] == {
        "minimum": 1.0, "median": 3.0,
        "p95": 5.0, "maximum": 5.0,
    }
    assert summary["diagnostics"]["peak_rss_bytes"] == {
        "available": True,
        "minimum": 10, "median": 30,
        "p95": 50, "maximum": 50,
    }
    assert summary["diagnostics"]["output"] == output


@pytest.mark.parametrize("mutation, message", [
    (lambda runs: runs[1].update(contract={"exit_code": 1}), "contract changed"),
    (lambda runs: runs[1]["diagnostics"].update(
        output={"private": "changed"}), "output changed"),
    (lambda runs: runs[1]["diagnostics"].update(
        peak_rss_bytes=None), "RSS availability"),
    (lambda runs: runs[1]["diagnostics"].update(
        first_party_loaded_sha256="f" * 64), "import identity"),
])
def test_summarize_runs_fails_on_unstable_contract_evidence(mutation, message):
    runs = [
        {
            "contract": {"exit_code": 0},
            "diagnostics": _diagnostics(wall=float(index)),
        }
        for index in range(5)
    ]
    mutation(runs)
    with pytest.raises(benchmark.PhaseA0BenchmarkError, match=message):
        benchmark.summarize_runs("cli_help", runs)


def test_validate_report_checks_self_digest_contract_and_lock_schema():
    report = _valid_report()
    assert benchmark.validate_report(report) is report

    tampered = deepcopy(report)
    tampered["scenarios"][0]["contract"]["ok"] = False
    with pytest.raises(benchmark.PhaseA0BenchmarkError, match="scenario"):
        benchmark.validate_report(tampered)

    tampered = deepcopy(report)
    tampered["source"]["commit"] = "not-a-commit"
    with pytest.raises(benchmark.PhaseA0BenchmarkError, match="source"):
        benchmark.validate_report(tampered)

    tampered = deepcopy(report)
    tampered["inputs"][0]["name"] = "../private.lock"
    with pytest.raises(benchmark.PhaseA0BenchmarkError, match="lock identity"):
        benchmark.validate_report(tampered)

    tampered = deepcopy(report)
    tampered["report_sha256"] = "0" * 64
    with pytest.raises(benchmark.PhaseA0BenchmarkError, match="digest"):
        benchmark.validate_report(tampered)


def test_contract_comparison_ignores_machine_timings_but_pins_inputs():
    baseline_contract = {
        "record_count": 3,
        "stable_id_count": 3,
        "context_segment_count": 2,
        "context_characters": 100,
        "markdown_bytes": 50,
        "result_sha256": "1" * 64,
    }
    baseline = _valid_report(
        contract=baseline_contract,
        scenario_name="offline_export_retrieval")
    current = deepcopy(baseline)
    current["runtime"]["python_version"] = "3.14.0"
    current["system"]["logical_cpu_count"] = 64
    current["scenarios"][0]["diagnostics"]["wall_time_ms"] = {
        "minimum": 100.0,
        "median": 200.0,
        "p95": 300.0,
        "maximum": 400.0,
    }
    current["report_sha256"] = benchmark._sha256_bytes(
        benchmark._canonical_bytes(benchmark._report_without_digest(current)))

    benchmark.compare_contracts(current, baseline)

    changed_input = deepcopy(current)
    changed_input["inputs"][0]["sha256"] = "f" * 64
    changed_input["report_sha256"] = benchmark._sha256_bytes(
        benchmark._canonical_bytes(
            benchmark._report_without_digest(changed_input)))
    with pytest.raises(benchmark.PhaseA0BenchmarkError, match="lock identities"):
        benchmark.compare_contracts(changed_input, baseline)

    changed_contract = deepcopy(current)
    changed_contract["scenarios"][0]["contract"]["context_characters"] = 101
    changed_contract["scenarios"][0]["contract_sha256"] = (
        benchmark._sha256_bytes(benchmark._canonical_bytes(
            changed_contract["scenarios"][0]["contract"])))
    changed_contract["report_sha256"] = benchmark._sha256_bytes(
        benchmark._canonical_bytes(
            benchmark._report_without_digest(changed_contract)))
    with pytest.raises(benchmark.PhaseA0BenchmarkError, match="behavioral"):
        benchmark.compare_contracts(changed_contract, baseline)


def test_safe_child_environment_drops_credentials_proxies_and_user_cache(
        monkeypatch, tmp_path):
    monkeypatch.setenv("GEMINI_API_KEY", "provider-secret")
    monkeypatch.setenv("HTTPS_PROXY", "https://private-proxy.example")
    monkeypatch.setenv("HF_HOME", str(tmp_path / "private-cache"))
    monkeypatch.setenv("PATH", "safe-path")

    environment = benchmark.safe_child_environment(tmp_path)

    assert environment["PATH"] == "safe-path"
    assert environment["PYTHONNOUSERSITE"] == "1"
    assert environment["TEMP"] == str(tmp_path)
    assert "GEMINI_API_KEY" not in environment
    assert "HTTPS_PROXY" not in environment
    assert "HF_HOME" not in environment
    assert "provider-secret" not in json.dumps(environment)
    if os.name == "nt":
        assert environment["USERPROFILE"] == str(tmp_path)
        assert environment["LOCALAPPDATA"] == str(tmp_path)


def test_strict_probe_payload_rejects_raw_or_malformed_envelopes():
    valid = {
        "ok": True,
        "scenario": "cold_import_rag",
        "contract": _valid_report()["scenarios"][0]["contract"],
        "diagnostics": {
            "loaded_module_count": 10,
            "first_party_loaded_count": 2,
            "first_party_loaded_sha256": "a" * 64,
            "peak_rss_bytes": None,
        },
    }
    assert benchmark._strict_probe_payload(
        benchmark._canonical_bytes(valid),
        scenario="cold_import_rag") == valid

    for payload in (
            b"", b"not-json", benchmark._canonical_bytes({**valid, "raw": "x"}),
            benchmark._canonical_bytes({**valid, "scenario": "cli_help"})):
        with pytest.raises(benchmark.PhaseA0BenchmarkError):
            benchmark._strict_probe_payload(
                payload, scenario="cold_import_rag")


def test_run_probe_redacts_subprocess_failure_and_timeout(monkeypatch, tmp_path):
    private = str(tmp_path / "Private Ethics.pdf")

    def fail(*_args, **_kwargs):
        return subprocess.CompletedProcess(
            [], 1, b'{"error_type":"RuntimeError"}',
            private.encode("utf-8"))

    monkeypatch.setattr(benchmark.subprocess, "run", fail)
    with pytest.raises(benchmark.PhaseA0BenchmarkError) as error:
        benchmark.run_probe("cold_import_rag")
    assert private not in str(error.value)

    def timeout(*_args, **_kwargs):
        raise subprocess.TimeoutExpired(["python"], 1, output=private)

    monkeypatch.setattr(benchmark.subprocess, "run", timeout)
    with pytest.raises(benchmark.PhaseA0BenchmarkError, match="deadline") as error:
        benchmark.run_probe("cold_import_rag")
    assert private not in str(error.value)


def test_offline_probe_is_deterministic_generated_evidence_only():
    first_contract, first_diagnostics = (
        benchmark._probe_offline_export_retrieval())
    second_contract, second_diagnostics = (
        benchmark._probe_offline_export_retrieval())

    assert first_contract == second_contract == {
        "record_count": 3,
        "stable_id_count": 3,
        "context_segment_count": 2,
        "context_characters": 668,
        "markdown_bytes": 149,
        "result_sha256": (
            "32f8cf511b3619a4cfe06655cfa678b4ee1f5fe00d92810ec7cd142e25af3ea3"),
    }
    assert first_diagnostics == second_diagnostics
    assert str(benchmark.PROJECT_ROOT) not in json.dumps(first_contract)


def test_real_child_probe_uses_empty_working_directory_and_safe_contract():
    result = benchmark.run_probe("cli_info_empty", timeout_seconds=30)

    assert result["contract"] == {
        "exit_code": 0,
        "stdout_present": True,
        "stderr_empty": True,
        "required_markers": {
            "Pipeline Output Status": True,
            "No output directory found": True,
        },
    }
    assert result["diagnostics"]["output"]["stdout"]["bytes"] > 0
    assert result["diagnostics"]["output"]["stderr"]["bytes"] == 0


def test_repository_runner_publishes_five_repetition_content_free_report():
    report = benchmark.run_benchmark(
        scenarios=("cold_import_rag",), repetitions=5,
        timeout_seconds=30)

    assert benchmark.validate_report(report) is report
    assert report["repetitions"] == 5
    assert report["scenarios"][0]["repetitions"] == 5
    assert report["scenarios"][0]["contract"]["required_symbols"] == {
        "PipelinePaths": True,
        "export_markdown": True,
        "main": True,
        "search_index": True,
    }
    encoded = json.dumps(report, sort_keys=True)
    assert str(benchmark.PROJECT_ROOT) not in encoded
    assert "USERPROFILE" not in encoded
    assert "API_KEY" not in encoded


def test_cli_rejects_under_sampled_evidence_without_writing(
        tmp_path, capsys):
    output = tmp_path / "report.json"

    assert benchmark.main([
        "--scenario", "cold_import_rag",
        "--repetitions", "4",
        "--output", str(output),
    ]) == 1

    assert not output.exists()
    assert capsys.readouterr().err == "Phase A0 benchmark failed.\n"


def test_atomic_report_writer_and_reader_round_trip(tmp_path):
    report = _valid_report()
    output = tmp_path / "nested" / "report.json"

    benchmark._write_report(output, report)

    assert benchmark._read_report(output) == report
    assert not list(output.parent.glob("*.tmp"))


def test_dependency_identities_are_sorted_basename_only():
    identities = benchmark.dependency_identities()

    names = [item["name"] for item in identities]
    assert names == sorted(names)
    assert "model-artifacts.lock.json" in names
    assert "requirements-core.lock" in names
    assert all(Path(name).name == name for name in names)
    assert all(item["bytes"] > 0 for item in identities)
    assert all(len(item["sha256"]) == 64 for item in identities)
