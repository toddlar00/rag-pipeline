from __future__ import annotations

import contextlib
from copy import deepcopy
import json
import os
from pathlib import Path
import stat
import sys
import threading
from types import SimpleNamespace

import pytest

from tools import benchmark_phase_a0 as benchmark


def _summary(value: int) -> dict:
    return {
        "minimum": value,
        "median": value,
        "p95": value,
        "maximum": value,
    }


def _roots(names: list[str]) -> dict:
    return {
        "count": len(names),
        "roots": names,
        "sha256": benchmark._sha256_bytes("\n".join(names).encode("utf-8")),
    }


def _diagnostics(
        *, wall: float, rss: int | None = 100,
        loaded: int = 20, output: dict | None = None,
) -> dict:
    first = _roots(["rag", "tools"])
    third = _roots(["requests"])
    stdlib = _roots(["json", "os"])
    return {
        "wall_time_ms": wall,
        "peak_rss_bytes": rss,
        "loaded_module_count": loaded,
        "first_party_loaded_count": first["count"],
        "first_party_loaded_roots": first["roots"],
        "first_party_loaded_sha256": first["sha256"],
        "third_party_loaded_count": third["count"],
        "third_party_loaded_roots": third["roots"],
        "third_party_loaded_sha256": third["sha256"],
        "stdlib_loaded_count": stdlib["count"],
        "stdlib_loaded_roots": stdlib["roots"],
        "stdlib_loaded_sha256": stdlib["sha256"],
        "output": output,
    }


def _noop_contract() -> dict:
    negative_kinds = {
        "conversion": "parameter_mismatch",
        "chunks": "source_mismatch",
        "quality": "parameter_mismatch",
        "export": "artifact_mismatch",
    }
    validators = {
        name: {
            "valid": {
                "arguments_sha256": valid_character * 64,
                "result": True,
            },
            "negative": {
                "kind": negative_kinds[name],
                "arguments_sha256": negative_character * 64,
                "result": False,
            },
            "pipeline": {
                "arguments_sha256": valid_character * 64,
                "result": True,
            },
        }
        for name, valid_character, negative_character in zip(
            ("conversion", "chunks", "quality", "export"),
            ("1", "2", "3", "4"),
            ("a", "b", "c", "d"),
            strict=True,
        )
    }
    lock_control = {
        "arguments_sha256": "6" * 64,
        "exit_code": 0,
        "guarded_processes": 1,
        "cleanup_confirmed": True,
    }
    return {
        "record_count": 1,
        "completion_validators": validators,
        "vector_lock": {
            "calls": 1,
            "entered": 1,
            "exited": 1,
            "clean_exit": True,
            "arguments_sha256": "5" * 64,
            "held_control": {**lock_control, "result": "busy"},
            "released_control": {**lock_control, "result": "acquired"},
        },
        "index_revalidation": {
            "calls": 1,
            "arguments_sha256": "7" * 64,
            "under_active_lock": True,
            "outcome": {
                "backend": "chroma",
                "disposition": "unchanged",
                "total_records": 1,
                "changed_records": 0,
                "unchanged_records": 1,
                "removed_records": 0,
                "upserted_records": 0,
                "batch_count": 0,
                "physical_count": 1,
                "committed": True,
            },
        },
        "publication_commit": {
            "calls": 1,
            "arguments_sha256": "9" * 64,
            "after_index": True,
            "gate_count": 5,
        },
        "returned_result": {
            "paths_match": True,
            "collection_match": True,
            "db_dir_match": True,
            "db_backend_match": True,
            "outcome_match": True,
            "publication_receipt_match": True,
        },
        "skipped_physical_calls": {
            "convert": 0,
            "chunk": 0,
            "quality": 0,
            "export": 0,
        },
        "artifact_set_sha256": "8" * 64,
        "physical_indexing_exercised": False,
        "partial_repair_exercised": False,
    }


def _valid_contract(name: str) -> dict:
    contracts = {
        "cold_import_rag": {
            "module": "rag",
            "required_symbols": {
                "PipelinePaths": True,
                "export_markdown": True,
                "main": True,
                "search_index": True,
            },
        },
        "cli_help": {
            "entrypoint": "rag.py",
            "execution_mode": "supervised_child",
            "exit_code": 0,
            "stdout_present": True,
            "stderr_empty": True,
            "required_markers": {
                "usage:": True,
                "info": True,
                "preprocess": True,
            },
        },
        "cli_info_empty": {
            "entrypoint": "rag.py",
            "execution_mode": "outer_supervised_process",
            "exit_code": 0,
            "stdout_present": True,
            "stderr_empty": True,
            "required_markers": {
                "Pipeline Output Status": True,
                "No output directory found": True,
            },
            "supervision": {
                "outer_processes": 1,
                "supervised_children": 1,
                "parent_child_link": True,
                "guarded_processes": 2,
                "cleanup_confirmed": True,
            },
        },
        "service_composition": {
            "composition_type": "ServiceApplicationComposition",
            "runtime_factory": "service_runtime.RagApplicationService",
            "http_factory": "service_http.create_app",
            "rag_loaded": False,
            "eval_loaded": False,
            "ui_loaded": False,
        },
        "worker_imports": {
            "worker_module": "service_search_worker",
            "ui_module": "ui",
            "worker_has_main": True,
            "ui_has_vector_worker": True,
        },
        "worker_entrypoints": {
            "ui": {
                "entrypoint": "ui.py",
                "exit_code": 0,
                "guarded_processes": 1,
                "cleanup_confirmed": True,
                "envelope": {
                    "ok": False,
                    "error_type": "ValueError",
                    "message": (
                        "Unsupported UI vector action: 'phase_a0_invalid'"),
                },
            },
            "service": {
                "entrypoint": "service_search_worker.py",
                "exit_code": 1,
                "guarded_processes": 1,
                "cleanup_confirmed": True,
                "envelope": {
                    "schema_version": 2,
                    "kind": "service_search_result",
                    "ok": False,
                    "error_code": "service_unavailable",
                },
            },
        },
        "isolation_guard": {
            "home_resolution_denied": True,
            "tilde_expansion_denied": True,
            "socket_creation_denied": True,
            "dns_resolution_denied": True,
            "descendant_home_resolution_denied": True,
            "descendant_network_denied": True,
            "guarded_processes": 2,
            "parent_child_link": True,
            "cleanup_confirmed": True,
            "native_executable_egress_covered": False,
        },
        "noop_resume": _noop_contract(),
        "offline_export_retrieval": {
            "record_count": 3,
            "stable_id_count": 3,
            "context_segment_count": 2,
            "context_characters": 100,
            "markdown_bytes": 50,
            "result_sha256": "8" * 64,
        },
    }
    return deepcopy(contracts[name])


def _report_diagnostics(output: dict | None = None) -> dict:
    first = _roots(["rag", "tools"])
    third = _roots(["requests"])
    stdlib = _roots(["json", "os"])
    return {
        "wall_time_ms": {
            "minimum": 1.0, "median": 2.0,
            "p95": 3.0, "maximum": 3.0,
        },
        "peak_rss_bytes": {
            "available": True,
            "scope": benchmark._RSS_SCOPE,
            "minimum": 10, "median": 20,
            "p95": 30, "maximum": 30,
        },
        "loaded_module_count": _summary(20),
        "first_party_loaded_count": _summary(first["count"]),
        "first_party_loaded_roots": first["roots"],
        "first_party_loaded_sha256": first["sha256"],
        "third_party_loaded_count": _summary(third["count"]),
        "third_party_loaded_roots": third["roots"],
        "third_party_loaded_sha256": third["sha256"],
        "stdlib_loaded_count": _summary(stdlib["count"]),
        "stdlib_loaded_roots": stdlib["roots"],
        "stdlib_loaded_sha256": stdlib["sha256"],
        "output": output,
    }


def _valid_report(
        *, contract: dict | None = None, marker: str = "a",
        scenario_name: str = "cold_import_rag", output: dict | None = None,
        authoritative: bool = False, platform_name: str = "windows",
) -> dict:
    names = list(benchmark.SCENARIOS) if authoritative else [scenario_name]
    scenarios = []
    for name in names:
        selected_contract = (
            deepcopy(contract)
            if name == scenario_name and contract is not None
            else _valid_contract(name))
        scenario = {
            "name": name,
            "repetitions": 5,
            "contract": selected_contract,
            "contract_sha256": benchmark._sha256_bytes(
                benchmark._canonical_bytes(selected_contract)),
            "diagnostics": _report_diagnostics(
                output if name == scenario_name else None),
        }
        scenarios.append(scenario)
    report = {
        "schema_version": benchmark.REPORT_SCHEMA_VERSION,
        "benchmark": benchmark.BENCHMARK_NAME,
        "profile": {
            "implementation": "CPython",
            "python_major_minor": "3.12",
            "pointer_bits": 64,
            "platform": platform_name,
            "canonical": True,
        },
        "scenario_set": benchmark._scenario_set_identity(names),
        "source": {
            "commit": marker * 40,
            "worktree_clean": True,
            "tracked_diff_sha256": benchmark._sha256_bytes(b"\0"),
        },
        "runtime": {
            "implementation": "CPython",
            "python_version": "3.12.1",
            "pointer_bits": 64,
        },
        "system": {
            "os": {
                "windows": "Windows",
                "linux": "Linux",
                "macos": "Darwin",
            }.get(platform_name, platform_name),
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
        "scenarios": scenarios,
    }
    report["report_sha256"] = benchmark._sha256_bytes(
        benchmark._canonical_bytes(report))
    return report


def _rehash_report(report: dict) -> None:
    for scenario in report["scenarios"]:
        scenario["contract_sha256"] = benchmark._sha256_bytes(
            benchmark._canonical_bytes(scenario["contract"]))
    report["report_sha256"] = benchmark._sha256_bytes(
        benchmark._canonical_bytes(benchmark._report_without_digest(report)))


def _scenario(report: dict, name: str) -> dict:
    return next(item for item in report["scenarios"] if item["name"] == name)


def _set_import_roots(
        report: dict, category: str, names: list[str],
        *, scenario_name: str = "cold_import_rag",
) -> None:
    identity = _roots(names)
    diagnostics = _scenario(report, scenario_name)["diagnostics"]
    diagnostics[f"{category}_loaded_count"] = _summary(identity["count"])
    diagnostics[f"{category}_loaded_roots"] = identity["roots"]
    diagnostics[f"{category}_loaded_sha256"] = identity["sha256"]


def test_numeric_summary_uses_nearest_rank_p95_and_rejects_bad_samples():
    assert benchmark._numeric_summary([5, 1, 4, 2, 3]) == {
        "minimum": 1, "median": 3, "p95": 5, "maximum": 5}
    assert benchmark._numeric_summary([1.1119, 2.2229]) == {
        "minimum": 1.112, "median": 1.667,
        "p95": 2.223, "maximum": 2.223}
    for values in ([], [True], [-1], [float("nan")], [float("inf")]):
        with pytest.raises(benchmark.PhaseA0BenchmarkError):
            benchmark._numeric_summary(values)


def test_summarize_runs_separates_portable_evidence_from_diagnostics():
    output = {
        "stdout": {"bytes": 3, "lines": 1, "sha256": "a" * 64},
        "stderr": {"bytes": 0, "lines": 0, "sha256": "b" * 64},
    }
    runs = [{
        "contract": {"exit_code": 0},
        "diagnostics": _diagnostics(
            wall=float(value), rss=value * 10,
            loaded=20 + value, output=output),
    } for value in range(1, 6)]

    summary = benchmark.summarize_runs("cli_help", runs)

    assert summary["repetitions"] == 5
    assert summary["diagnostics"]["wall_time_ms"] == {
        "minimum": 1.0, "median": 3.0,
        "p95": 5.0, "maximum": 5.0}
    assert summary["diagnostics"]["peak_rss_bytes"] == {
        "available": True,
        "scope": benchmark._RSS_SCOPE,
        "minimum": 10, "median": 30,
        "p95": 50, "maximum": 50,
    }
    assert summary["diagnostics"]["third_party_loaded_roots"] == ["requests"]
    assert summary["diagnostics"]["output"] == output


@pytest.mark.parametrize(("mutation", "message"), [
    (lambda runs: runs[1].update(contract={"exit_code": 1}), "contract changed"),
    (lambda runs: runs[1]["diagnostics"].update(
        output={"private": "changed"}), "output changed"),
    (lambda runs: runs[1]["diagnostics"].update(
        peak_rss_bytes=None), "RSS availability"),
    (lambda runs: runs[1]["diagnostics"].update(
        first_party_loaded_sha256="f" * 64), "first-party import identity"),
    (lambda runs: runs[1]["diagnostics"].update(
        first_party_loaded_count=3), "first-party import identity"),
    (lambda runs: runs[1]["diagnostics"].update(
        third_party_loaded_roots=["fastapi"]), "third-party import identity"),
    (lambda runs: runs[1]["diagnostics"].update(
        stdlib_loaded_sha256="f" * 64), "stdlib import identity"),
])
def test_summarize_runs_fails_on_unstable_evidence(mutation, message):
    runs = [{
        "contract": {"exit_code": 0},
        "diagnostics": _diagnostics(wall=float(index)),
    } for index in range(5)]
    mutation(runs)
    with pytest.raises(benchmark.PhaseA0BenchmarkError, match=message):
        benchmark.summarize_runs("cli_help", runs)


def test_platform_normalization_is_stable():
    assert benchmark._normalized_platform_name("Windows") == "windows"
    assert benchmark._normalized_platform_name("Linux") == "linux"
    assert benchmark._normalized_platform_name("Darwin") == "macos"
    assert benchmark._normalized_platform_name("FreeBSD 14") == "freebsd"


def test_validate_report_checks_v3_profile_scenario_set_and_digest():
    report = _valid_report()
    assert benchmark.validate_report(report) is report

    tampered = deepcopy(report)
    tampered["schema_version"] = 1
    _rehash_report(tampered)
    with pytest.raises(benchmark.PhaseA0BenchmarkError, match="identity"):
        benchmark.validate_report(tampered)

    tampered = deepcopy(report)
    tampered["profile"]["canonical"] = False
    _rehash_report(tampered)
    with pytest.raises(benchmark.PhaseA0BenchmarkError, match="profile"):
        benchmark.validate_report(tampered)

    tampered = deepcopy(report)
    tampered["profile"]["platform"] = "Windows"
    _rehash_report(tampered)
    with pytest.raises(benchmark.PhaseA0BenchmarkError, match="profile"):
        benchmark.validate_report(tampered)

    tampered = deepcopy(report)
    tampered["scenario_set"]["sha256"] = "0" * 64
    _rehash_report(tampered)
    with pytest.raises(benchmark.PhaseA0BenchmarkError, match="scenario-set"):
        benchmark.validate_report(tampered)

    tampered = deepcopy(report)
    tampered["source"]["commit"] = "not-a-commit"
    _rehash_report(tampered)
    with pytest.raises(benchmark.PhaseA0BenchmarkError, match="source"):
        benchmark.validate_report(tampered)

    tampered = deepcopy(report)
    tampered["source"]["tracked_diff_sha256"] = "b" * 64
    _rehash_report(tampered)
    with pytest.raises(benchmark.PhaseA0BenchmarkError, match="source"):
        benchmark.validate_report(tampered)

    tampered = deepcopy(report)
    tampered["report_sha256"] = "0" * 64
    with pytest.raises(benchmark.PhaseA0BenchmarkError, match="digest"):
        benchmark.validate_report(tampered)


def test_contract_comparison_ignores_machine_diagnostics_but_pins_inputs():
    baseline = _valid_report(authoritative=True)
    current = deepcopy(baseline)
    current["runtime"]["python_version"] = "3.12.9"
    current["system"]["logical_cpu_count"] = 64
    diagnostics = _scenario(current, "cold_import_rag")["diagnostics"]
    diagnostics["loaded_module_count"] = {
        "minimum": 400, "median": 500,
        "p95": 600, "maximum": 700}
    diagnostics["peak_rss_bytes"] = {
        "available": False, "scope": benchmark._RSS_SCOPE}
    diagnostics["wall_time_ms"] = {
        "minimum": 100.0, "median": 200.0,
        "p95": 300.0, "maximum": 400.0}
    _set_import_roots(current, "stdlib", ["json"])
    _rehash_report(current)

    benchmark.compare_contracts(current, baseline)

    changed_input = deepcopy(current)
    changed_input["inputs"][0]["sha256"] = "f" * 64
    _rehash_report(changed_input)
    with pytest.raises(benchmark.PhaseA0BenchmarkError, match="lock identities"):
        benchmark.compare_contracts(changed_input, baseline)

    changed_contract = deepcopy(current)
    _scenario(changed_contract, "offline_export_retrieval")[
        "contract"]["context_characters"] = 101
    _rehash_report(changed_contract)
    with pytest.raises(
            benchmark.PhaseA0BenchmarkError,
            match="portable deterministic evidence"):
        benchmark.compare_contracts(changed_contract, baseline)


@pytest.mark.parametrize("mutation", ["first_party", "third_party", "output"])
def test_contract_comparison_rejects_portable_evidence_mutations(mutation):
    baseline = _valid_report(authoritative=True)
    current = deepcopy(baseline)
    if mutation == "first_party":
        _set_import_roots(current, "first_party", ["rag", "tools", "ui"])
    elif mutation == "third_party":
        _set_import_roots(current, "third_party", ["fastapi", "requests"])
    else:
        _scenario(current, "cold_import_rag")["diagnostics"]["output"] = {
            "stdout": {"bytes": 1, "lines": 1, "sha256": "1" * 64},
            "stderr": {"bytes": 0, "lines": 0, "sha256": "2" * 64},
        }
    _rehash_report(current)

    with pytest.raises(
            benchmark.PhaseA0BenchmarkError,
            match="portable deterministic evidence"):
        benchmark.compare_contracts(current, baseline)


def test_contract_comparison_rejects_subsets_and_noncanonical_profiles():
    canonical = _valid_report(authoritative=True)
    subset = _valid_report()
    with pytest.raises(benchmark.PhaseA0BenchmarkError, match="authoritative"):
        benchmark.compare_contracts(subset, subset)

    noncanonical = deepcopy(canonical)
    noncanonical["profile"] = {
        "implementation": "CPython",
        "python_major_minor": "3.14",
        "pointer_bits": 64,
        "platform": "windows",
        "canonical": False,
    }
    noncanonical["runtime"]["python_version"] = "3.14.1"
    _rehash_report(noncanonical)
    assert benchmark.validate_report(noncanonical) is noncanonical
    with pytest.raises(benchmark.PhaseA0BenchmarkError, match="CPython 3.12"):
        benchmark.compare_contracts(noncanonical, canonical)


@pytest.mark.parametrize("dirty_operand", ["current", "baseline"])
def test_contract_comparison_rejects_dirty_source_evidence(dirty_operand):
    current = _valid_report(authoritative=True)
    baseline = deepcopy(current)
    dirty = current if dirty_operand == "current" else baseline
    dirty["source"]["worktree_clean"] = False
    _rehash_report(dirty)

    assert benchmark.validate_report(dirty) is dirty
    with pytest.raises(benchmark.PhaseA0BenchmarkError, match="clean"):
        benchmark.compare_contracts(current, baseline)


def test_contract_comparison_rejects_cross_platform_baseline():
    current = _valid_report(authoritative=True, platform_name="windows")
    baseline = _valid_report(authoritative=True, platform_name="linux")

    with pytest.raises(benchmark.PhaseA0BenchmarkError, match="platform"):
        benchmark.compare_contracts(current, baseline)


def test_safe_child_environment_is_fixed_and_drops_ambient_state(
        monkeypatch, tmp_path):
    monkeypatch.setenv("gEmInI_aPi_KeY", "provider-secret")
    monkeypatch.setenv("HTTPS_PROXY", "https://private-proxy.example")
    monkeypatch.setenv("HF_HOME", str(tmp_path / "private-cache"))
    monkeypatch.setenv("LD_LIBRARY_PATH", str(tmp_path / "private-libraries"))
    monkeypatch.setenv("PYTHONUSERBASE", str(tmp_path / "private-user-base"))
    monkeypatch.setenv("__PYVENV_LAUNCHER__", str(tmp_path / "untrusted-launcher"))
    monkeypatch.setenv("RAG_LLM_CACHE_DIR", str(tmp_path / "operator-llm-cache"))
    monkeypatch.setenv("PATH", "safe-path")
    guard = tmp_path / "guard"

    environment = benchmark.safe_child_environment(
        tmp_path, sitecustomize_root=guard)

    assert environment["PATH"] == "safe-path"
    assert environment["LANG"] == "C.UTF-8"
    assert environment["LC_ALL"] == "C.UTF-8"
    assert environment["PYTHONHASHSEED"] == "0"
    assert environment["PYTHONIOENCODING"] == "utf-8"
    assert environment["PYTHONPATH"] == os.pathsep.join(
        [str(guard), str(benchmark.PROJECT_ROOT)])
    assert environment["PYTHONUSERBASE"] == str(tmp_path / "python-user-base")
    assert environment["RAG_LLM_CACHE_DIR"] == str(tmp_path / "llm-cache")
    assert environment["RAG_MODEL_ARTIFACT_CACHE"] == str(
        tmp_path / "model-artifacts")
    assert environment["RAG_PIPELINE_OUTPUT_ROOT"] == str(tmp_path / "output")
    assert environment["XDG_CACHE_HOME"] == str(tmp_path / "xdg-cache")
    assert environment["XDG_CONFIG_HOME"] == str(tmp_path / "xdg-config")
    assert environment["XDG_DATA_HOME"] == str(tmp_path / "xdg-data")
    assert environment["XDG_STATE_HOME"] == str(tmp_path / "xdg-state")
    forbidden = {
        "home", "codex_home", "gemini_api_key", "https_proxy", "hf_home",
        "ld_library_path", "__pyvenv_launcher__"}
    assert not {name.casefold() for name in environment}.intersection(forbidden)
    assert "provider-secret" not in json.dumps(environment)
    assert "private-user-base" not in json.dumps(environment)
    if os.name == "nt":
        assert environment["USERPROFILE"] == str(tmp_path)
        assert environment["LOCALAPPDATA"] == str(tmp_path)


def test_guarded_environment_imports_sysconfig_without_home_resolution(tmp_path):
    script = tmp_path / "sysconfig_probe.py"
    benchmark._write_private_generated_text(script, """\
import json
import os
from pathlib import Path
import sysconfig

try:
    Path.home()
except RuntimeError:
    home_denied = True
else:
    home_denied = False

print(json.dumps({
    "home_denied": home_denied,
    "sysconfig_loaded": bool(sysconfig.get_paths()),
    "user_base_redirected": (
        sysconfig.get_config_var("userbase") == os.environ["PYTHONUSERBASE"]),
}, sort_keys=True, separators=(",", ":")))
""")
    guard = benchmark._install_sitecustomize_guard(tmp_path)
    environment = benchmark._guarded_child_environment(
        tmp_path, guard, trace_path=tmp_path / "trace.jsonl",
        role="sysconfig-probe")

    result = benchmark._run_contained_process(
        script, (), cwd=tmp_path, environment=environment,
        timeout_seconds=10)

    assert result.returncode == 0
    assert result.stderr == b""
    assert json.loads(result.stdout) == {
        "home_denied": True,
        "sysconfig_loaded": True,
        "user_base_redirected": True,
    }


def _startup_hook(monkeypatch, tmp_path, delegate, *, ambient=None):
    """Execute only the generated forwarding hook, never an in-process guard."""
    module = SimpleNamespace(_run_supervised_entrypoint=delegate)
    monkeypatch.setitem(sys.modules, "process_supervision", module)
    environment = ({"RAG_PHASE_A0_GUARD_ROLE": "rag-info"}
                   if ambient is None else ambient)
    for name in ("RAG_PHASE_A0_GUARD_ROLE", "RAG_PIPELINE_SUPERVISED_CHILD"):
        if name in environment:
            monkeypatch.setenv(name, environment[name])
        else:
            monkeypatch.delenv(name, raising=False)
    namespace = {}
    source = benchmark._rag_info_startup_source(tmp_path, tmp_path / "guard")
    exec(compile(source, "generated-startup-hook", "exec"), namespace)
    return module._run_supervised_entrypoint, namespace


def _startup_arguments():
    return {
        "script_path": benchmark.PROJECT_ROOT / "rag.py",
        "config": SimpleNamespace(
            supervised_child_env="RAG_PIPELINE_SUPERVISED_CHILD"),
        "unrelated_callback": object(),
    }


def test_generated_startup_mapping_is_fixed_not_ambient(monkeypatch, tmp_path):
    for name in ("PYTHONPATH", "PYTHONUSERBASE", "pYtHoNoPtImIzE",
                 "_PYTHON_SYSCONFIGDATA_NAME", "__PYVENV_LAUNCHER__"):
        monkeypatch.setenv(name, "hostile-startup-selector")
    _, namespace = _startup_hook(monkeypatch, tmp_path, lambda *a, **kw: 0)
    assert namespace["_phase_a0_rag_info_startup"] == {
        "PYTHONDONTWRITEBYTECODE": "1", "PYTHONHASHSEED": "0",
        "PYTHONIOENCODING": "utf-8", "PYTHONNOUSERSITE": "1",
        "PYTHONPATH": os.pathsep.join([
            str(tmp_path / "guard"), str(benchmark.PROJECT_ROOT)]),
        "PYTHONUSERBASE": str(tmp_path / "python-user-base"),
        "PYTHONUTF8": "1",
    }
    assert "hostile-startup-selector" not in repr(namespace["_phase_a0_rag_info_startup"])


def test_default_guard_does_not_install_startup_forwarder(tmp_path):
    guard = benchmark._install_sitecustomize_guard(tmp_path)
    source = (guard / "sitecustomize.py").read_text(encoding="utf-8")
    assert source == benchmark._SITECUSTOMIZE_SOURCE
    assert "_forward_rag_info_startup" not in source


def test_generated_startup_delegates_once_preserving_inputs(monkeypatch, tmp_path):
    calls = []
    result = object()

    def delegate(argv, **kwargs):
        calls.append((argv, kwargs))
        return result

    hook, namespace = _startup_hook(monkeypatch, tmp_path, delegate)
    arguments = ["info"]
    options = _startup_arguments()
    explicit = {"KEEP": "value", "REMOVE": None, "PYTHONHASHSEED": "0"}
    original = dict(explicit)
    assert hook(arguments, environment_overrides=explicit, **options) is result
    assert len(calls) == 1 and calls[0][0] is arguments
    assert explicit == original
    forwarded = calls[0][1].pop("environment_overrides")
    assert calls[0][1] == options
    assert forwarded is not explicit
    assert forwarded == {**explicit, **namespace["_phase_a0_rag_info_startup"]}


@pytest.mark.parametrize("override", [
    {"PYTHONPATH": "untrusted"}, {"PYTHONUSERBASE": None},
    {"PYTHONHASHSEED": "7"}, {"PYTHONIOENCODING": "ascii"},
    {"PYTHONDONTWRITEBYTECODE": "0"}, {"PYTHONNOUSERSITE": "0"},
    {"PYTHONUTF8": "0"}, {"pythonpath": "untrusted"},
    {"pYtHoNoPtImIzE": "2"}, {"_PYTHON_SYSCONFIGDATA_NAME": "untrusted"},
    {"__PyVENV_LAUNCHER__": "untrusted"},
])
def test_generated_startup_refuses_conflicting_selectors(monkeypatch, tmp_path, override):
    def forbidden(*_args, **_kwargs):
        pytest.fail("conflicting startup override reached real entrypoint")

    hook, _ = _startup_hook(monkeypatch, tmp_path, forbidden)
    original = dict(override)
    with pytest.raises(RuntimeError):
        hook(["info"], environment_overrides=override, **_startup_arguments())
    assert override == original


@pytest.mark.parametrize("scope", ["role", "inner", "target", "config"])
def test_generated_startup_does_not_forward_outside_cli_info_scope(monkeypatch, tmp_path, scope):
    ambient = {"RAG_PHASE_A0_GUARD_ROLE": "rag-info"}
    options = _startup_arguments()
    if scope == "role":
        ambient["RAG_PHASE_A0_GUARD_ROLE"] = "worker"
    elif scope == "inner":
        ambient["RAG_PIPELINE_SUPERVISED_CHILD"] = "1"
    elif scope == "target":
        options["script_path"] = tmp_path / "different.py"
    else:
        options["config"] = SimpleNamespace(supervised_child_env="OTHER_CHILD")
    calls = []

    def delegate(argv, **kwargs):
        calls.append((argv, kwargs))
        return 42

    hook, _ = _startup_hook(monkeypatch, tmp_path, delegate, ambient=ambient)
    explicit = {"PYTHONPATH": "unrelated-host-owned"}
    arguments = ["info"]
    assert hook(arguments, environment_overrides=explicit, **options) == 42
    assert len(calls) == 1 and calls[0][0] is arguments
    assert calls[0][1] == {**options, "environment_overrides": explicit}
    assert calls[0][1]["environment_overrides"] is explicit


def test_generated_startup_preserves_delegate_exception(monkeypatch, tmp_path):
    failure = RuntimeError("delegate failed")

    def delegate(*_args, **_kwargs):
        raise failure

    hook, _ = _startup_hook(monkeypatch, tmp_path, delegate)
    with pytest.raises(RuntimeError) as raised:
        hook(["info"], **_startup_arguments())
    assert raised.value is failure


def test_generated_startup_reaches_nested_guard_and_sysconfig(tmp_path):
    """Real two-hop control; the existing CLI test separately exercises rag."""
    guard = benchmark._install_sitecustomize_guard(
        tmp_path, forward_rag_info_startup=True)
    child = tmp_path / "nested_sysconfig.py"
    parent = tmp_path / "outer_entrypoint.py"
    expected_startup = {
        "PYTHONDONTWRITEBYTECODE": "1", "PYTHONHASHSEED": "0",
        "PYTHONIOENCODING": "utf-8", "PYTHONNOUSERSITE": "1",
        "PYTHONPATH": os.pathsep.join([str(guard), str(benchmark.PROJECT_ROOT)]),
        "PYTHONUSERBASE": str(tmp_path / "python-user-base"), "PYTHONUTF8": "1",
    }
    benchmark._write_private_generated_text(child, """\
import json
import os
from pathlib import Path
import socket
import sysconfig

def denied(action):
    try:
        action()
    except RuntimeError:
        return True
    return False

observed = {
    "home_denied": denied(Path.home),
    "tilde_denied": denied(lambda: Path("~").expanduser()),
    "socket_denied": denied(socket.socket),
    "dns_denied": denied(lambda: socket.getaddrinfo("localhost", 80)),
    "sysconfig_loaded": bool(sysconfig.get_paths()),
    "user_base_redirected": sysconfig.get_config_var("userbase") == EXPECTED["PYTHONUSERBASE"],
    "selectors_equal": all(os.environ.get(k) == v for k, v in EXPECTED.items()),
    "production_supervised": os.environ.get("RAG_PIPELINE_SUPERVISED_CHILD") == "1",
}
print(json.dumps(observed, sort_keys=True))
""".replace("import json\n", "import json\nEXPECTED = " + repr(expected_startup) + "\n", 1))
    benchmark._write_private_generated_text(parent, """\
from pathlib import Path
import process_supervision as ps

config = ps.SupervisionConfig("RAG_PIPELINE_SUPERVISED_CHILD", "RAG_RUN_ID", 1., .01, 5.)

def actual_supervisor(script, args, **kwargs):
    assert script == RAG_SCRIPT and args == ["info"]
    return ps._run_cli_with_deadline(CHILD_SCRIPT, [], config=config, **kwargs)

def unexpected(*_args):
    raise AssertionError("nested supervision was bypassed")

raise SystemExit(ps._run_supervised_entrypoint(
    ["info"], config=config, script_path=RAG_SCRIPT,
    command_resolver=lambda args: args[0], operation_timeouts={"info": 10.},
    telemetry_options_fn=lambda *_args: {"run_id": None, "events_path": None, "report_path": None},
    timeout_fn=lambda *_args: 10., run_id_factory=lambda: "generated-nested-control",
    menu_fn=unexpected, main_fn=unexpected, supervisor_fn=actual_supervisor))
""".replace("import process_supervision as ps\n", "import process_supervision as ps\n"
            + "RAG_SCRIPT = Path(" + repr(str(benchmark.PROJECT_ROOT / "rag.py")) + ")\n"
            + "CHILD_SCRIPT = Path(" + repr(str(child)) + ")\n", 1))
    trace = tmp_path / "trace.jsonl"
    environment = benchmark._guarded_child_environment(
        tmp_path, guard, trace_path=trace, role="rag-info")
    result = benchmark._run_contained_process(
        parent, (), cwd=tmp_path, environment=environment, timeout_seconds=20)
    assert result.returncode == 0 and result.stderr == b"" and result.cleanup_confirmed
    assert json.loads(result.stdout) == {
        "home_denied": True, "tilde_denied": True, "socket_denied": True,
        "dns_denied": True, "sysconfig_loaded": True, "user_base_redirected": True,
        "selectors_equal": True, "production_supervised": True,
    }
    events = benchmark._read_guard_trace(trace)
    assert len(events) == 2
    assert [item["role"] for item in events] == ["rag-info", "rag-info"]
    assert [item["production_supervised"] for item in events] == [False, True]
    assert events[1]["ppid"] == events[0]["pid"]


def test_contained_runner_uses_exact_environment_and_bounded_files(
        monkeypatch, tmp_path):
    import process_supervision

    monkeypatch.setenv("MiXeD_SECRET", "private")
    observed = {}

    def fake_supervisor(script, arguments, **kwargs):
        observed.update({"script": script, "arguments": arguments, **kwargs})
        observed["stdout_is_regular"] = stat.S_ISREG(
            os.fstat(kwargs["stdout_target"].fileno()).st_mode)
        observed["stderr_is_regular"] = stat.S_ISREG(
            os.fstat(kwargs["stderr_target"].fileno()).st_mode)
        kwargs["stdout_target"].write(b"bounded")
        return 0

    monkeypatch.setattr(
        process_supervision, "_run_cli_with_deadline", fake_supervisor)
    environment = benchmark.safe_child_environment(tmp_path)

    result = benchmark._run_contained_process(
        tmp_path / "probe.py", ("one",), cwd=tmp_path,
        environment=environment, timeout_seconds=3, max_output_bytes=32)

    assert result.stdout == b"bounded"
    assert result.stderr == b""
    assert result.cleanup_confirmed is True
    assert observed["config"].supervised_child_env == (
        benchmark._CONTAINED_CHILD_ENV)
    assert observed["working_directory"] == tmp_path
    assert observed["heartbeat"] is not None
    removed_environment = {
        name.casefold(): value
        for name, value in observed["environment_overrides"].items()
    }
    assert removed_environment["mixed_secret"] is None
    assert benchmark._CONTAINED_CHILD_ENV not in observed[
        "environment_overrides"]
    assert observed["stdout_is_regular"] is True
    assert observed["stderr_is_regular"] is True


def test_contained_runner_kills_noisy_descendant_tree(tmp_path):
    script = tmp_path / "noisy.py"
    benchmark._write_private_generated_text(script, """\
import os
import subprocess
import sys
import time
subprocess.Popen([sys.executable, '-c', 'import time; time.sleep(60)'])
os.write(1, b'x' * 65536)
time.sleep(60)
""")
    guard = benchmark._install_sitecustomize_guard(tmp_path)
    trace = tmp_path / "trace.jsonl"
    environment = benchmark._guarded_child_environment(
        tmp_path, guard, trace_path=trace, role="noisy-probe")

    with pytest.raises(
            benchmark.PhaseA0BenchmarkError, match="output bound"):
        benchmark._run_contained_process(
            script, (), cwd=tmp_path, environment=environment,
            timeout_seconds=10, max_output_bytes=1024)


def _capture_roots(root: Path) -> list[Path]:
    return sorted(path for path in root.iterdir()
                  if path.name.startswith("phase-a0-capture-"))


def _exceed_output_bound(kwargs: dict, observed: dict) -> None:
    """Drive the runner's own heartbeat past its bound, as a real probe does."""
    kwargs["stdout_target"].write(b"x" * 64)
    kwargs["stdout_target"].flush()
    try:
        kwargs["heartbeat"](None)
    except benchmark.PhaseA0BenchmarkError as exc:
        observed["primary"] = exc
        raise
    raise AssertionError("heartbeat did not enforce the output bound")


def test_contained_runner_capture_root_is_private_and_removed(
        monkeypatch, tmp_path):
    import process_supervision

    observed = {}

    def fake_supervisor(_script, _arguments, **kwargs):
        capture_root = Path(kwargs["stdout_target"].name).parent
        observed.update({
            "capture_root": capture_root,
            "capture_mode": stat.S_IMODE(capture_root.stat().st_mode),
            "file_modes": [
                stat.S_IMODE(os.fstat(kwargs[name].fileno()).st_mode)
                for name in ("stdout_target", "stderr_target")],
            "names": sorted(path.name for path in capture_root.iterdir()),
        })
        kwargs["stderr_target"].write(b"diagnostic")
        return 3

    monkeypatch.setattr(
        process_supervision, "_run_cli_with_deadline", fake_supervisor)
    result = benchmark._run_contained_process(
        tmp_path / "probe.py", (), cwd=tmp_path,
        environment=benchmark.safe_child_environment(tmp_path),
        timeout_seconds=3, max_output_bytes=32)

    assert vars(result) == {
        "returncode": 3, "stdout": b"", "stderr": b"diagnostic",
        "cleanup_confirmed": True}
    capture_root = observed["capture_root"]
    assert capture_root.parent == tmp_path
    assert capture_root.name.startswith("phase-a0-capture-")
    assert observed["names"] == ["stderr.bin", "stdout.bin"]
    if os.name != "nt":
        assert observed["capture_mode"] == 0o700
        assert observed["file_modes"] == [0o600, 0o600]
    assert not capture_root.exists()
    assert _capture_roots(tmp_path) == []


def test_contained_runner_reraises_primary_error_after_removing_capture(
        monkeypatch, tmp_path):
    import process_supervision

    observed = {}
    context = TimeoutError("simulated deadline context")

    def fake_supervisor(_script, _arguments, **kwargs):
        try:
            raise context
        except TimeoutError:
            _exceed_output_bound(kwargs, observed)

    monkeypatch.setattr(
        process_supervision, "_run_cli_with_deadline", fake_supervisor)
    with pytest.raises(
            benchmark.PhaseA0BenchmarkError, match="output bound") as raised:
        benchmark._run_contained_process(
            tmp_path / "probe.py", (), cwd=tmp_path,
            environment=benchmark.safe_child_environment(tmp_path),
            timeout_seconds=3, max_output_bytes=32)

    assert raised.value is observed["primary"]
    assert raised.value.__cause__ is None
    assert raised.value.__context__ is context
    assert raised.value.diagnostic_code is None
    assert _capture_roots(tmp_path) == []


@pytest.mark.parametrize(
    "failure", [RuntimeError("boom"), KeyboardInterrupt(), SystemExit(7)],
    ids=["runtime-error", "keyboard-interrupt", "system-exit"])
def test_contained_runner_wraps_non_phase_failures_after_cleanup(
        monkeypatch, tmp_path, failure):
    import process_supervision

    def fake_supervisor(_script, _arguments, **_kwargs):
        raise failure

    monkeypatch.setattr(
        process_supervision, "_run_cli_with_deadline", fake_supervisor)
    with pytest.raises(
            benchmark.PhaseA0BenchmarkError,
            match="^contained process failed with confirmed cleanup$",
    ) as raised:
        benchmark._run_contained_process(
            tmp_path / "probe.py", (), cwd=tmp_path,
            environment=benchmark.safe_child_environment(tmp_path),
            timeout_seconds=3)

    assert raised.value.__cause__ is failure
    assert raised.value.diagnostic_code is None
    assert _capture_roots(tmp_path) == []


def test_contained_runner_wraps_capture_creation_failure(
        monkeypatch, tmp_path):
    import process_supervision

    def unexpected(*_args, **_kwargs):
        raise AssertionError("no process may start without capture files")

    monkeypatch.setattr(
        process_supervision, "_run_cli_with_deadline", unexpected)
    missing = tmp_path / "missing"
    with pytest.raises(
            benchmark.PhaseA0BenchmarkError,
            match="^contained process failed with confirmed cleanup$",
    ) as raised:
        benchmark._run_contained_process(
            tmp_path / "probe.py", (), cwd=missing,
            environment=benchmark.safe_child_environment(tmp_path),
            timeout_seconds=3)

    assert isinstance(raised.value.__cause__, FileNotFoundError)
    assert not missing.exists()


@pytest.mark.skipif(
    os.name != "nt", reason="only Windows refuses to delete an open file")
@pytest.mark.xfail(
    strict=True,
    reason="the capture cleanup error replaces the primary error")
def test_contained_runner_keeps_primary_error_through_transient_capture_hold(
        monkeypatch, tmp_path):
    import process_supervision

    observed = {}

    def fake_supervisor(_script, _arguments, **kwargs):
        # A killed descendant's inherited handle can outlive the confirmed
        # tree kill briefly; model it with a handle released on a timer.
        holder = open(kwargs["stdout_target"].name, "rb")
        observed["release"] = threading.Timer(0.25, holder.close)
        observed["release"].start()
        _exceed_output_bound(kwargs, observed)

    monkeypatch.setattr(
        process_supervision, "_run_cli_with_deadline", fake_supervisor)
    try:
        with pytest.raises(
                benchmark.PhaseA0BenchmarkError,
                match="output bound") as raised:
            benchmark._run_contained_process(
                tmp_path / "probe.py", (), cwd=tmp_path,
                environment=benchmark.safe_child_environment(tmp_path),
                timeout_seconds=3, max_output_bytes=32)
    finally:
        observed["release"].join()

    assert raised.value is observed["primary"]
    assert _capture_roots(tmp_path) == []


def test_probe_main_fails_closed_if_code_resolves_the_operator_home(
        monkeypatch, capsys):
    original_local_home = vars(Path).get("home")
    home_was_local = "home" in vars(Path)
    original_home = Path.home

    def probe():
        Path.home()
        raise AssertionError("unreachable")

    monkeypatch.setitem(benchmark._PROBES, "cold_import_rag", probe)
    assert benchmark._probe_main("cold_import_rag") == 1
    assert Path.home == original_home
    assert ("home" in vars(Path)) is home_was_local
    if home_was_local:
        assert vars(Path)["home"] is original_local_home
    assert json.loads(capsys.readouterr().out) == {
        "error_type": "PhaseA0BenchmarkError", "ok": False}


def test_probe_main_restores_an_inherited_home_descriptor(monkeypatch, capsys):
    class PathBase:
        @classmethod
        def home(cls):
            return cls("operator-home")

    class InheritedHomePath(PathBase):
        pass

    def probe():
        benchmark.Path.home()
        raise AssertionError("unreachable")

    assert "home" not in vars(InheritedHomePath)
    original_home = InheritedHomePath.home
    monkeypatch.setattr(benchmark, "Path", InheritedHomePath)
    monkeypatch.setitem(benchmark._PROBES, "cold_import_rag", probe)

    assert benchmark._probe_main("cold_import_rag") == 1
    assert "home" not in vars(InheritedHomePath)
    assert InheritedHomePath.home == original_home
    assert json.loads(capsys.readouterr().out) == {
        "error_type": "PhaseA0BenchmarkError", "ok": False}


def test_strict_probe_payload_rejects_duplicates_nonfinite_and_trailing_data():
    output = {
        "stdout": {"bytes": 0, "lines": 0, "sha256": "a" * 64},
        "stderr": {"bytes": 0, "lines": 0, "sha256": "b" * 64},
    }
    valid = {
        "ok": True,
        "scenario": "cold_import_rag",
        "contract": _valid_contract("cold_import_rag"),
        "diagnostics": {
            key: value for key, value in _diagnostics(
                wall=1.0, output=output).items()
            if key != "wall_time_ms"
        },
    }
    encoded = benchmark._canonical_bytes(valid)
    assert benchmark._strict_probe_payload(
        encoded, scenario="cold_import_rag") == valid

    mutations = (
        b"",
        b"not-json",
        encoded.replace(b'"ok":true', b'"ok":true,"ok":true', 1),
        encoded.replace(b'"loaded_module_count":20',
                        b'"loaded_module_count":NaN', 1),
        encoded.replace(b'"loaded_module_count":20',
                        b'"loaded_module_count":1e999', 1),
        encoded + b" trailing",
    )
    for payload in mutations:
        with pytest.raises(benchmark.PhaseA0BenchmarkError):
            benchmark._strict_probe_payload(
                payload, scenario="cold_import_rag")


def test_report_reader_is_strict_and_stats_before_reading(monkeypatch, tmp_path):
    report = _valid_report()
    encoded = benchmark._canonical_bytes(report)
    duplicate = tmp_path / "duplicate.json"
    duplicate.write_bytes(encoded.replace(
        b'"benchmark":"phase-a0"',
        b'"benchmark":"phase-a0","benchmark":"phase-a0"', 1))
    overflow = tmp_path / "overflow.json"
    overflow.write_bytes(encoded.replace(b'"bytes":10', b'"bytes":1e999', 1))
    for path in (duplicate, overflow):
        with pytest.raises(benchmark.PhaseA0BenchmarkError, match="invalid JSON"):
            benchmark._read_report(path)

    oversized = tmp_path / "oversized.bin"
    oversized.write_bytes(b"12345")

    def forbidden_open(*_args, **_kwargs):
        raise AssertionError("oversized file should be rejected before open")

    monkeypatch.setattr(Path, "open", forbidden_open)
    with pytest.raises(benchmark.PhaseA0BenchmarkError, match="size"):
        benchmark._read_bounded_file(
            oversized, max_bytes=4, description="oversized fixture")


def test_run_probe_redacts_contained_failure_and_timeout(monkeypatch, tmp_path):
    private = str(tmp_path / "Private Ethics.pdf")

    monkeypatch.setattr(
        benchmark, "_run_contained_process",
        lambda *_args, **_kwargs: SimpleNamespace(
            returncode=1, stdout=b"", stderr=private.encode(),
            cleanup_confirmed=True))
    with pytest.raises(benchmark.PhaseA0BenchmarkError) as error:
        benchmark.run_probe("cold_import_rag")
    assert private not in str(error.value)

    monkeypatch.setattr(
        benchmark, "_run_contained_process",
        lambda *_args, **_kwargs: SimpleNamespace(
            returncode=124, stdout=b"", stderr=b"",
            cleanup_confirmed=True))
    with pytest.raises(benchmark.PhaseA0BenchmarkError, match="deadline"):
        benchmark.run_probe("cold_import_rag")


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
        "markdown_bytes": 180,
        "result_sha256": (
            "16623b13d968ffb973fa066bd4b22deeba7db1c506db71d74b71b55a6e84d4ae"),
    }
    assert first_diagnostics == second_diagnostics
    assert str(benchmark.PROJECT_ROOT) not in json.dumps(first_contract)


def test_real_cli_info_proves_outer_supervision_and_empty_output_root():
    result = benchmark.run_probe("cli_info_empty", timeout_seconds=30)
    assert result["contract"] == _valid_contract("cli_info_empty")
    assert result["diagnostics"]["output"]["stdout"]["bytes"] > 0
    assert result["diagnostics"]["output"]["stderr"]["bytes"] == 0


def test_real_cli_info_rejects_outer_supervision_bypass(monkeypatch, tmp_path):
    monkeypatch.chdir(tmp_path)
    with pytest.raises(
            benchmark.PhaseA0BenchmarkError, match="outer-supervision trace"):
        benchmark._probe_cli_info_empty(_test_break_outer_supervision=True)


def test_real_worker_entrypoints_are_guarded_and_strict():
    result = benchmark.run_probe("worker_entrypoints", timeout_seconds=30)
    assert result["contract"] == _valid_contract("worker_entrypoints")
    assert "output" not in result["diagnostics"]


def test_worker_import_probe_gates_real_import_footprint():
    result = benchmark.run_probe("worker_imports", timeout_seconds=30)
    assert result["contract"] == _valid_contract("worker_imports")
    assert result["diagnostics"]["first_party_loaded_count"] > 0
    assert result["diagnostics"]["third_party_loaded_count"] > 0


def test_real_isolation_guard_covers_parent_and_descendant():
    result = benchmark.run_probe("isolation_guard", timeout_seconds=30)
    assert result["contract"] == _valid_contract("isolation_guard")


def test_generated_isolation_descendant_uses_shared_python_launch(
        monkeypatch, tmp_path, capsys):
    import ast
    import process_supervision

    calls = []
    command = ["synthetic-base-python", "synthetic-script"]
    environment = {"SAFE": "child-only"}

    def launch(arguments, inherited):
        calls.append((arguments, inherited))
        return command, environment

    def run(actual_command, **kwargs):
        assert actual_command is command
        assert kwargs == {
            "env": environment, "stdin": benchmark.subprocess.DEVNULL,
            "stdout": benchmark.subprocess.DEVNULL, "stderr": benchmark.subprocess.DEVNULL,
            "check": False, "timeout": 10,
        }
        return SimpleNamespace(returncode=0)

    monkeypatch.setattr(process_supervision, "python_worker_launch", launch)
    # Execute the actual generated import and two launch statements, without
    # launching a process or weakening the real probe's home/network guard.
    parsed = ast.parse(benchmark._ISOLATION_PROBE_SOURCE)
    statements = [node for node in parsed.body if (
        isinstance(node, ast.ImportFrom) and node.module == "process_supervision"
        or isinstance(node, ast.Assign) and any(
            isinstance(target, ast.Tuple) and any(isinstance(item, ast.Name) and item.id == "command" for item in target.elts)
            or isinstance(target, ast.Name) and target.id == "completed"
            for target in node.targets))]
    assert len(statements) == 3
    namespace = {"os": SimpleNamespace(environ={"SAFE": "parent"}),
                 "subprocess": SimpleNamespace(run=run, DEVNULL=benchmark.subprocess.DEVNULL),
                 "__file__": "synthetic-script", "child_result": tmp_path / "child-result.json"}
    exec(compile(ast.Module(body=statements, type_ignores=[]), "generated-launch", "exec"), namespace)
    assert calls == [(["synthetic-script", "--child", str(tmp_path / "child-result.json")], {"SAFE": "parent"})]
    assert namespace["completed"].returncode == 0
    assert not capsys.readouterr().out


def test_real_noop_resume_observes_validators_lock_index_and_result():
    contract = benchmark.run_probe(
        "noop_resume", timeout_seconds=30)["contract"]
    assert set(contract["completion_validators"]) == {
        "conversion", "chunks", "quality", "export"}
    assert all(
        item["valid"]["result"] is True
        and item["negative"]["result"] is False
        and item["pipeline"]["result"] is True
        for item in contract["completion_validators"].values())
    assert contract["vector_lock"]["entered"] == 1
    assert contract["vector_lock"]["exited"] == 1
    assert contract["vector_lock"]["held_control"]["result"] == "busy"
    assert contract["vector_lock"]["released_control"]["result"] == "acquired"
    assert contract["index_revalidation"]["under_active_lock"] is True
    assert contract["publication_commit"]["after_index"] is True
    assert contract["publication_commit"]["gate_count"] == 5
    assert contract["returned_result"] == {
        "paths_match": True,
        "collection_match": True,
        "db_dir_match": True,
        "db_backend_match": True,
        "outcome_match": True,
        "publication_receipt_match": True,
    }
    assert contract["skipped_physical_calls"] == {
        "convert": 0, "chunk": 0, "quality": 0, "export": 0}


@pytest.mark.parametrize("validator_name", [
    "_converted_outputs_complete",
    "_chunks_complete",
    "_quality_report_complete",
    "_unified_export_complete",
])
def test_noop_resume_rejects_unconditional_completion_validator(
        monkeypatch, tmp_path, validator_name):
    import rag

    monkeypatch.chdir(tmp_path)
    monkeypatch.setattr(
        rag, validator_name, lambda *_args, **_kwargs: True)
    with pytest.raises(
            benchmark.PhaseA0BenchmarkError,
            match="completion validator negative control"):
        benchmark._probe_noop_resume()


def test_noop_resume_rejects_noop_vector_lock(monkeypatch, tmp_path):
    import rag

    @contextlib.contextmanager
    def noop_lock(*_args, **_kwargs):
        yield None

    monkeypatch.chdir(tmp_path)
    monkeypatch.setattr(rag, "_vector_store_lock", noop_lock)
    with pytest.raises(
            (benchmark.PhaseA0BenchmarkError, RuntimeError),
            match="mutual exclusion control"):
        benchmark._probe_noop_resume()


def test_noop_resume_rejects_replaced_pipeline(monkeypatch, tmp_path):
    import rag

    monkeypatch.chdir(tmp_path)
    monkeypatch.setattr(rag, "_run_pipeline_stages", lambda *_args, **_kwargs: {})
    with pytest.raises(
            benchmark.PhaseA0BenchmarkError,
            match="observations were incomplete"):
        benchmark._probe_noop_resume()


def test_noop_resume_index_stub_rejects_wrong_wiring(monkeypatch, tmp_path):
    import rag

    def broken_pipeline(*_args, **_kwargs):
        rag._index_chunks_for_backend("WRONG", "WRONG", nonsense=True)
        return {}

    monkeypatch.chdir(tmp_path)
    monkeypatch.setattr(rag, "_run_pipeline_stages", broken_pipeline)
    with pytest.raises(
            benchmark.PhaseA0BenchmarkError,
            match="index revalidation wiring"):
        benchmark._probe_noop_resume()


def test_run_benchmark_rejects_source_identity_drift(monkeypatch):
    source = {
        "commit": "a" * 40,
        "worktree_clean": True,
        "tracked_diff_sha256": "b" * 64,
    }
    changed = {**source, "tracked_diff_sha256": "c" * 64}
    calls = iter((source, changed))
    monkeypatch.setattr(
        benchmark, "repository_identity", lambda _root: next(calls))
    monkeypatch.setattr(
        benchmark, "dependency_identities", lambda _root: [{
            "name": "requirements-core.lock", "bytes": 1,
            "sha256": "d" * 64}])
    monkeypatch.setattr(
        benchmark, "run_probe", lambda *_args, **_kwargs: {
            "contract": _valid_contract("cold_import_rag"),
            "diagnostics": _diagnostics(wall=1.0)})
    with pytest.raises(benchmark.PhaseA0BenchmarkError, match="changed"):
        benchmark.run_benchmark(
            scenarios=("cold_import_rag",), repetitions=5)


def test_run_benchmark_rejects_lock_identity_drift(monkeypatch):
    source = {
        "commit": "a" * 40,
        "worktree_clean": True,
        "tracked_diff_sha256": "b" * 64,
    }
    first = [{
        "name": "requirements-core.lock", "bytes": 1,
        "sha256": "c" * 64}]
    second = [{**first[0], "sha256": "d" * 64}]
    inputs = iter((first, second))
    monkeypatch.setattr(benchmark, "repository_identity", lambda _root: source)
    monkeypatch.setattr(
        benchmark, "dependency_identities", lambda _root: next(inputs))
    monkeypatch.setattr(
        benchmark, "run_probe", lambda *_args, **_kwargs: {
            "contract": _valid_contract("cold_import_rag"),
            "diagnostics": _diagnostics(wall=1.0)})
    with pytest.raises(benchmark.PhaseA0BenchmarkError, match="changed"):
        benchmark.run_benchmark(
            scenarios=("cold_import_rag",), repetitions=5)


def test_repository_runner_publishes_full_five_repetition_smoke_report():
    pytest.importorskip("fastapi")
    report = benchmark.run_benchmark(
        scenarios=benchmark.SCENARIOS, repetitions=5,
        timeout_seconds=45)
    assert benchmark.validate_report(report) is report
    assert report["scenario_set"] == benchmark._scenario_set_identity(
        benchmark.SCENARIOS)
    assert report["profile"] == benchmark.execution_profile()
    assert [scenario["name"] for scenario in report["scenarios"]] == list(
        benchmark.SCENARIOS)
    assert all(scenario["repetitions"] == 5 for scenario in report["scenarios"])
    encoded = json.dumps(report, sort_keys=True)
    assert str(benchmark.PROJECT_ROOT) not in encoded
    assert "USERPROFILE" not in encoded
    assert "API_KEY" not in encoded


def test_check_preflight_requires_clean_worktree_before_running(
        monkeypatch, tmp_path, capsys):
    monkeypatch.setattr(
        benchmark, "execution_profile", lambda: {
            "implementation": "CPython", "python_major_minor": "3.12",
            "pointer_bits": 64, "platform": "windows",
            "canonical": True})
    monkeypatch.setattr(
        benchmark, "repository_identity", lambda: {
            "commit": "a" * 40, "worktree_clean": False,
            "tracked_diff_sha256": "b" * 64})
    monkeypatch.setattr(
        benchmark, "run_benchmark",
        lambda **_kwargs: pytest.fail("dirty preflight must not run benchmark"))

    assert benchmark.main(["--check", str(tmp_path / "baseline.json")]) == 1
    assert capsys.readouterr().err == (
        "Phase A0 benchmark failed during baseline preflight "
        "(phase-a0-dirty-worktree).\n")


@pytest.mark.parametrize("invalid_baseline", ["dirty", "cross_platform"])
def test_check_preflight_rejects_non_authoritative_baseline_before_running(
        monkeypatch, tmp_path, capsys, invalid_baseline):
    baseline = _valid_report(
        authoritative=True,
        platform_name=("linux" if invalid_baseline == "cross_platform"
                       else "windows"),
    )
    if invalid_baseline == "dirty":
        baseline["source"]["worktree_clean"] = False
        _rehash_report(baseline)
    monkeypatch.setattr(
        benchmark, "execution_profile", lambda: {
            "implementation": "CPython", "python_major_minor": "3.12",
            "pointer_bits": 64, "platform": "windows",
            "canonical": True})
    monkeypatch.setattr(
        benchmark, "repository_identity", lambda: {
            "commit": "a" * 40, "worktree_clean": True,
            "tracked_diff_sha256": benchmark._sha256_bytes(b"\0")})
    monkeypatch.setattr(benchmark, "_read_report", lambda _path: baseline)
    monkeypatch.setattr(
        benchmark, "run_benchmark",
        lambda **_kwargs: pytest.fail(
            "invalid baseline preflight must not run benchmark"))

    assert benchmark.main(["--check", str(tmp_path / "baseline.json")]) == 1
    expected_code = {
        "dirty": "phase-a0-dirty-baseline",
        "cross_platform": "phase-a0-platform-mismatch",
    }[invalid_baseline]
    assert capsys.readouterr().err == (
        "Phase A0 benchmark failed during baseline preflight "
        f"({expected_code}).\n")


def test_cli_rejects_under_sampled_evidence_without_writing(tmp_path, capsys):
    output = tmp_path / "report.json"
    assert benchmark.main([
        "--scenario", "cold_import_rag",
        "--repetitions", "4",
        "--output", str(output),
    ]) == 1
    assert not output.exists()
    assert capsys.readouterr().err == (
        "Phase A0 benchmark failed during benchmark execution.\n")


def test_cli_rejects_baseline_output_alias_without_overwriting(
        monkeypatch, tmp_path, capsys):
    baseline = tmp_path / "baseline.json"
    original = b"owner-approved baseline\n"
    baseline.write_bytes(original)
    monkeypatch.setattr(
        benchmark, "run_benchmark",
        lambda **_kwargs: pytest.fail("aliased paths must fail before running"))

    assert benchmark.main([
        "--check", str(baseline), "--output", str(baseline),
    ]) == 1
    assert baseline.read_bytes() == original
    assert capsys.readouterr().err == (
        "Phase A0 benchmark failed during argument validation "
        "(phase-a0-report-path-alias).\n")


def test_failed_comparison_retains_content_free_candidate_report(
        monkeypatch, tmp_path, capsys):
    baseline = _valid_report(authoritative=True)
    candidate = deepcopy(baseline)
    output = tmp_path / "candidate.json"
    monkeypatch.setattr(
        benchmark, "execution_profile", lambda: baseline["profile"])
    monkeypatch.setattr(
        benchmark, "repository_identity", lambda: baseline["source"])
    monkeypatch.setattr(benchmark, "_read_report", lambda _path: baseline)
    monkeypatch.setattr(
        benchmark, "run_benchmark", lambda **_kwargs: candidate)

    def reject_comparison(_current, _baseline):
        raise benchmark.PhaseA0BenchmarkError(
            "private internal comparison detail",
            diagnostic_code="phase-a0-portable-evidence-drift")

    monkeypatch.setattr(benchmark, "compare_contracts", reject_comparison)

    assert benchmark.main([
        "--check", str(tmp_path / "baseline.json"),
        "--output", str(output),
    ]) == 1
    published = json.loads(output.read_text(encoding="utf-8"))
    assert benchmark.validate_report(published) == candidate
    stderr = capsys.readouterr().err
    assert stderr == (
        "Phase A0 benchmark failed during baseline comparison "
        "(phase-a0-portable-evidence-drift).\n")
    assert "private internal comparison detail" not in stderr


def test_cli_failure_does_not_expose_raw_exception_text(
        monkeypatch, tmp_path, capsys):
    private_detail = str(tmp_path / "private-corpus" / "ethics.pdf")

    def fail_benchmark(**_kwargs):
        raise OSError(private_detail)

    monkeypatch.setattr(benchmark, "run_benchmark", fail_benchmark)

    assert benchmark.main(["--output", str(tmp_path / "report.json")]) == 1
    stderr = capsys.readouterr().err
    assert stderr == "Phase A0 benchmark failed during benchmark execution.\n"
    assert private_detail not in stderr


def test_require_clean_help_mentions_untracked_changes():
    assert "untracked" in benchmark._parser().format_help()


def test_atomic_report_writer_and_strict_reader_round_trip(tmp_path):
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


def test_dependency_identities_reject_worktree_bytes_that_differ_from_head(
        monkeypatch, tmp_path):
    lock = tmp_path / "requirements-core.lock"
    lock.write_bytes(b"distribution==1.0\r\n")
    monkeypatch.setattr(
        benchmark, "_run_git",
        lambda _root, _arguments: b"distribution==1.0\n")

    with pytest.raises(benchmark.PhaseA0BenchmarkError) as raised:
        benchmark.dependency_identities(tmp_path)

    assert raised.value.diagnostic_code == (
        "phase-a0-noncanonical-input-bytes")
