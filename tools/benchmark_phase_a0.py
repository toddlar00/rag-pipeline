#!/usr/bin/env python3
"""Run content-free Phase A0 architecture performance probes.

The benchmark intentionally separates portable behavioral contracts from
machine-dependent timing and memory diagnostics.  Every operation runs in a
fresh, credential-scrubbed Python process rooted in an empty temporary working
directory, so the report cannot depend on an operator's private corpus.
"""

from __future__ import annotations

import argparse
import contextlib
import ctypes
import hashlib
import importlib
import io
import json
import math
import os
import platform
import re
import statistics
import subprocess
import sys
import tempfile
import time
from collections.abc import Callable, Mapping, Sequence
from pathlib import Path
from typing import Any


PROJECT_ROOT = Path(__file__).resolve().parents[1]
if str(PROJECT_ROOT) not in sys.path:
    sys.path.insert(0, str(PROJECT_ROOT))

REPORT_SCHEMA_VERSION = 1
BENCHMARK_NAME = "phase-a0"
DEFAULT_REPETITIONS = 5
MIN_REPETITIONS = 5
MAX_REPETITIONS = 50
DEFAULT_TIMEOUT_SECONDS = 30.0
MAX_PROBE_OUTPUT_BYTES = 1_000_000

SCENARIOS = (
    "cold_import_rag",
    "cli_help",
    "cli_info_empty",
    "service_composition",
    "worker_imports",
    "offline_export_retrieval",
)

_SAFE_CHILD_ENVIRONMENT = (
    "COMSPEC",
    "LANG",
    "LC_ALL",
    "LD_LIBRARY_PATH",
    "PATH",
    "PATHEXT",
    "SYSTEMROOT",
    "WINDIR",
)
_SHA256_RE = re.compile(r"[0-9a-f]{64}")
_COMMIT_RE = re.compile(r"[0-9a-f]{40}")


class PhaseA0BenchmarkError(RuntimeError):
    """A probe, report, or comparison invariant failed."""


def _canonical_bytes(value: object) -> bytes:
    return json.dumps(
        value,
        ensure_ascii=False,
        sort_keys=True,
        separators=(",", ":"),
        allow_nan=False,
    ).encode("utf-8")


def _sha256_bytes(value: bytes) -> str:
    return hashlib.sha256(value).hexdigest()


def _file_identity(path: Path) -> dict[str, object]:
    digest = hashlib.sha256()
    size = 0
    with path.open("rb") as handle:
        while True:
            block = handle.read(1024 * 1024)
            if not block:
                break
            size += len(block)
            digest.update(block)
    return {
        "name": path.name,
        "bytes": size,
        "sha256": digest.hexdigest(),
    }


def dependency_identities(root: Path = PROJECT_ROOT) -> list[dict[str, object]]:
    """Return stable names and byte identities for release input locks."""
    root = root.resolve(strict=True)
    paths = sorted(root.glob("requirements*.lock"), key=lambda path: path.name)
    model_lock = root / "model-artifacts.lock.json"
    if model_lock.is_file():
        paths.append(model_lock)
    paths.sort(key=lambda path: path.name)
    if not paths:
        raise PhaseA0BenchmarkError("no dependency or model locks were found")
    names = [path.name for path in paths]
    if len(names) != len(set(names)):
        raise PhaseA0BenchmarkError("duplicate lock names were found")
    return [_file_identity(path) for path in paths]


def _run_git(root: Path, arguments: Sequence[str]) -> bytes:
    result = subprocess.run(
        ["git", *arguments],
        cwd=root,
        check=False,
        stdout=subprocess.PIPE,
        stderr=subprocess.PIPE,
    )
    if result.returncode != 0:
        raise PhaseA0BenchmarkError("repository identity could not be resolved")
    return result.stdout


def repository_identity(root: Path = PROJECT_ROOT) -> dict[str, object]:
    """Bind a report to a commit and the content-free tracked-diff identity."""
    root = root.resolve(strict=True)
    commit = _run_git(root, ("rev-parse", "HEAD")).decode("ascii").strip()
    if _COMMIT_RE.fullmatch(commit) is None:
        raise PhaseA0BenchmarkError("Git returned an invalid commit identity")
    status = _run_git(
        root, ("status", "--porcelain=v1", "--untracked-files=all", "-z"))
    unstaged = _run_git(root, ("diff", "--binary", "HEAD", "--"))
    staged = _run_git(root, ("diff", "--cached", "--binary", "HEAD", "--"))
    return {
        "commit": commit,
        "worktree_clean": not bool(status),
        "tracked_diff_sha256": _sha256_bytes(unstaged + b"\0" + staged),
    }


def _bounded_public_text(value: object, *, limit: int = 160) -> str:
    text = " ".join(str(value or "unknown").split())
    text = "".join(char for char in text if char.isprintable())
    return text[:limit] or "unknown"


def _total_memory_bytes() -> int | None:
    if os.name == "nt":
        class _MemoryStatus(ctypes.Structure):
            _fields_ = [
                ("length", ctypes.c_ulong),
                ("memory_load", ctypes.c_ulong),
                ("total_physical", ctypes.c_ulonglong),
                ("available_physical", ctypes.c_ulonglong),
                ("total_page_file", ctypes.c_ulonglong),
                ("available_page_file", ctypes.c_ulonglong),
                ("total_virtual", ctypes.c_ulonglong),
                ("available_virtual", ctypes.c_ulonglong),
                ("available_extended_virtual", ctypes.c_ulonglong),
            ]

        status = _MemoryStatus()
        status.length = ctypes.sizeof(status)
        try:
            kernel32 = ctypes.WinDLL("kernel32", use_last_error=True)
            global_memory_status = kernel32.GlobalMemoryStatusEx
            global_memory_status.argtypes = [ctypes.POINTER(_MemoryStatus)]
            global_memory_status.restype = ctypes.c_int
            if not global_memory_status(ctypes.byref(status)):
                return None
        except (AttributeError, OSError):
            return None
        return int(status.total_physical)
    try:
        page_size = os.sysconf("SC_PAGE_SIZE")
        page_count = os.sysconf("SC_PHYS_PAGES")
    except (AttributeError, OSError, ValueError):
        return None
    if (isinstance(page_size, int) and isinstance(page_count, int)
            and page_size > 0 and page_count > 0):
        return page_size * page_count
    return None


def machine_identity() -> dict[str, object]:
    """Describe capacity-relevant public hardware without host/user identity."""
    return {
        "os": _bounded_public_text(platform.system()),
        "os_release": _bounded_public_text(platform.release()),
        "machine": _bounded_public_text(platform.machine()),
        "processor": _bounded_public_text(platform.processor()),
        "logical_cpu_count": os.cpu_count(),
        "total_memory_bytes": _total_memory_bytes(),
    }


def runtime_identity() -> dict[str, object]:
    return {
        "implementation": _bounded_public_text(platform.python_implementation()),
        "python_version": platform.python_version(),
        "pointer_bits": ctypes.sizeof(ctypes.c_void_p) * 8,
    }


def _peak_rss_bytes() -> int | None:
    """Return this probe's high-water RSS when the platform exposes it."""
    if os.name == "nt":
        class _ProcessMemoryCounters(ctypes.Structure):
            _fields_ = [
                ("cb", ctypes.c_ulong),
                ("page_fault_count", ctypes.c_ulong),
                ("peak_working_set_size", ctypes.c_size_t),
                ("working_set_size", ctypes.c_size_t),
                ("quota_peak_paged_pool_usage", ctypes.c_size_t),
                ("quota_paged_pool_usage", ctypes.c_size_t),
                ("quota_peak_non_paged_pool_usage", ctypes.c_size_t),
                ("quota_non_paged_pool_usage", ctypes.c_size_t),
                ("pagefile_usage", ctypes.c_size_t),
                ("peak_pagefile_usage", ctypes.c_size_t),
            ]

        counters = _ProcessMemoryCounters()
        counters.cb = ctypes.sizeof(counters)
        try:
            kernel32 = ctypes.WinDLL("kernel32", use_last_error=True)
            psapi = ctypes.WinDLL("psapi", use_last_error=True)
            get_current_process = kernel32.GetCurrentProcess
            get_current_process.argtypes = []
            get_current_process.restype = ctypes.c_void_p
            get_process_memory_info = psapi.GetProcessMemoryInfo
            get_process_memory_info.argtypes = [
                ctypes.c_void_p,
                ctypes.POINTER(_ProcessMemoryCounters),
                ctypes.c_ulong,
            ]
            get_process_memory_info.restype = ctypes.c_int
            succeeded = get_process_memory_info(
                get_current_process(), ctypes.byref(counters), counters.cb)
        except (AttributeError, OSError):
            return None
        return int(counters.peak_working_set_size) if succeeded else None
    try:
        import resource

        maximum = resource.getrusage(resource.RUSAGE_SELF).ru_maxrss
    except (ImportError, OSError, ValueError):
        return None
    if not isinstance(maximum, (int, float)) or maximum < 0:
        return None
    multiplier = 1 if sys.platform == "darwin" else 1024
    return int(maximum * multiplier)


def _output_identity(value: str) -> dict[str, object]:
    encoded = value.encode("utf-8")
    return {
        "bytes": len(encoded),
        "lines": len(value.splitlines()),
        "sha256": _sha256_bytes(encoded),
    }


def _capture(call: Callable[[], object]) -> tuple[int, str, str, object]:
    stdout = io.StringIO()
    stderr = io.StringIO()
    outcome: object = None
    exit_code = 0
    with contextlib.redirect_stdout(stdout), contextlib.redirect_stderr(stderr):
        try:
            outcome = call()
        except SystemExit as exc:
            if exc.code is None:
                exit_code = 0
            elif isinstance(exc.code, int) and not isinstance(exc.code, bool):
                exit_code = exc.code
            else:
                exit_code = 1
    return exit_code, stdout.getvalue(), stderr.getvalue(), outcome


def _root_module_names() -> set[str]:
    return {path.stem for path in PROJECT_ROOT.glob("*.py") if path.is_file()}


def _loaded_module_diagnostics() -> dict[str, object]:
    first_party = sorted(_root_module_names().intersection(sys.modules))
    return {
        "loaded_module_count": len(sys.modules),
        "first_party_loaded_count": len(first_party),
        "first_party_loaded_sha256": _sha256_bytes(
            "\n".join(first_party).encode("utf-8")),
    }


def _probe_cold_import_rag() -> tuple[dict[str, object], dict[str, object]]:
    module = importlib.import_module("rag")
    required = (
        "PipelinePaths",
        "export_markdown",
        "main",
        "search_index",
    )
    contract = {
        "module": module.__name__,
        "required_symbols": {
            name: hasattr(module, name) for name in required},
    }
    return contract, _loaded_module_diagnostics()


def _probe_rag_cli(
        arguments: Sequence[str], *, required_markers: Sequence[str],
) -> tuple[dict[str, object], dict[str, object]]:
    rag = importlib.import_module("rag")
    exit_code, stdout, stderr, _ = _capture(lambda: rag.main(list(arguments)))
    contract = {
        "exit_code": exit_code,
        "stdout_present": bool(stdout),
        "stderr_empty": not bool(stderr),
        "required_markers": {
            marker: marker in stdout for marker in required_markers},
    }
    diagnostics = {
        **_loaded_module_diagnostics(),
        "output": {
            "stdout": _output_identity(stdout),
            "stderr": _output_identity(stderr),
        },
    }
    if (exit_code != 0 or not stdout or stderr
            or not all(contract["required_markers"].values())):
        raise PhaseA0BenchmarkError("CLI probe contract was not satisfied")
    return contract, diagnostics


def _probe_cli_help() -> tuple[dict[str, object], dict[str, object]]:
    return _probe_rag_cli(
        ("--help",), required_markers=("usage:", "info", "preprocess"))


def _probe_cli_info_empty() -> tuple[dict[str, object], dict[str, object]]:
    return _probe_rag_cli(
        ("info",),
        required_markers=("Pipeline Output Status", "No output directory found"),
    )


def _probe_service_composition(
) -> tuple[dict[str, object], dict[str, object]]:
    composition_module = importlib.import_module("application_composition")
    composition = composition_module.default_service_application_composition()
    contract = {
        "composition_type": type(composition).__name__,
        "runtime_factory": (
            f"{composition.runtime_factory.__module__}."
            f"{composition.runtime_factory.__qualname__}"),
        "http_factory": (
            f"{composition.http_factory.__module__}."
            f"{composition.http_factory.__qualname__}"),
        "rag_loaded": "rag" in sys.modules,
        "eval_loaded": "eval" in sys.modules,
        "ui_loaded": "ui" in sys.modules,
    }
    return contract, _loaded_module_diagnostics()


def _probe_worker_imports() -> tuple[dict[str, object], dict[str, object]]:
    worker = importlib.import_module("service_search_worker")
    ui = importlib.import_module("ui")
    contract = {
        "worker_module": worker.__name__,
        "ui_module": ui.__name__,
        "worker_has_main": callable(getattr(worker, "main", None)),
        "ui_has_vector_worker": callable(
            getattr(ui, "_vector_worker_main", None)),
    }
    return contract, _loaded_module_diagnostics()


def _synthetic_record(text: str, index: int) -> dict[str, object]:
    return {
        "text": text,
        "metadata": {
            "chunk_index": index,
            "source_file": "cc0-phase-a0.json",
            "chapter_num": 1,
            "chapter_title": "Synthetic Procedure",
            "content_type": "author_narrative",
            "page_start": index + 1,
            "page_end": index + 1,
            "page_range": str(index + 1),
            "section_path": "Chapter 1 -> Synthetic Evidence",
        },
    }


def _probe_offline_export_retrieval(
) -> tuple[dict[str, object], dict[str, object]]:
    rag = importlib.import_module("rag")
    retrieval = importlib.import_module("retrieval_core")
    records = [
        _synthetic_record("Alpha synthetic rule.", 0),
        _synthetic_record("Beta synthetic application.", 1),
        _synthetic_record("Gamma synthetic conclusion.", 2),
    ]
    stable_ids = retrieval._attach_retrieval_linkage(records)
    middle = records[1]
    hit = retrieval.SearchHit(
        str(middle["text"]),
        dict(middle["metadata"]),
        0.75,
        source_id=stable_ids[1],
    )
    response = retrieval.SearchResponse(
        hits=[hit],
        backend="synthetic",
        requested_mode="offline",
        effective_mode="offline",
        reranker_applied=False,
    )
    retrieval._assemble_retrieval_context(
        response, records, context_window=1,
        max_characters=4_000, segment_characters=400)
    markdown = rag._assemble_markdown(records)
    evidence_payload = {
        "stable_ids": stable_ids,
        "markdown": markdown,
        "context_source_ids": [
            segment.source_id for segment in hit.context_segments],
    }
    contract = {
        "record_count": len(records),
        "stable_id_count": len(stable_ids),
        "context_segment_count": len(hit.context_segments),
        "context_characters": response.context_characters,
        "markdown_bytes": len(markdown.encode("utf-8")),
        "result_sha256": _sha256_bytes(_canonical_bytes(evidence_payload)),
    }
    return contract, _loaded_module_diagnostics()


_PROBES: Mapping[
    str, Callable[[], tuple[dict[str, object], dict[str, object]]]
] = {
    "cold_import_rag": _probe_cold_import_rag,
    "cli_help": _probe_cli_help,
    "cli_info_empty": _probe_cli_info_empty,
    "service_composition": _probe_service_composition,
    "worker_imports": _probe_worker_imports,
    "offline_export_retrieval": _probe_offline_export_retrieval,
}


def _valid_count(value: object, *, positive: bool = False) -> bool:
    return (
        not isinstance(value, bool)
        and isinstance(value, int)
        and value >= (1 if positive else 0)
    )


def _validate_output_identity(value: object) -> None:
    if (not isinstance(value, dict)
            or set(value) != {"bytes", "lines", "sha256"}
            or not _valid_count(value["bytes"])
            or not _valid_count(value["lines"])
            or _SHA256_RE.fullmatch(str(value["sha256"])) is None):
        raise PhaseA0BenchmarkError("probe output identity was invalid")


def _validate_output_pair(value: object) -> None:
    if not isinstance(value, dict) or set(value) != {"stdout", "stderr"}:
        raise PhaseA0BenchmarkError("probe output schema was invalid")
    _validate_output_identity(value["stdout"])
    _validate_output_identity(value["stderr"])


def _validate_probe_diagnostics(value: object) -> None:
    if not isinstance(value, dict):
        raise PhaseA0BenchmarkError("probe diagnostics were invalid")
    required = {
        "loaded_module_count", "first_party_loaded_count",
        "first_party_loaded_sha256", "peak_rss_bytes"}
    if frozenset(value) not in {
            frozenset(required), frozenset(required | {"output"})}:
        raise PhaseA0BenchmarkError("probe diagnostic fields were invalid")
    if (not _valid_count(value["loaded_module_count"], positive=True)
            or not _valid_count(value["first_party_loaded_count"], positive=True)
            or _SHA256_RE.fullmatch(
                str(value["first_party_loaded_sha256"])) is None
            or (value["peak_rss_bytes"] is not None
                and not _valid_count(value["peak_rss_bytes"], positive=True))):
        raise PhaseA0BenchmarkError("probe diagnostic value was invalid")
    if "output" in value:
        _validate_output_pair(value["output"])


def _validate_contract(name: str, value: object) -> None:
    if (not isinstance(value, dict)
            or len(_canonical_bytes(value)) > 100_000):
        raise PhaseA0BenchmarkError("scenario contract was invalid")
    if name == "cold_import_rag":
        expected_symbols = {
            "PipelinePaths", "export_markdown", "main", "search_index"}
        symbols = value.get("required_symbols")
        valid = (
            set(value) == {"module", "required_symbols"}
            and value["module"] == "rag"
            and isinstance(symbols, dict)
            and set(symbols) == expected_symbols
            and all(item is True for item in symbols.values())
        )
    elif name in {"cli_help", "cli_info_empty"}:
        expected_markers = (
            {"usage:", "info", "preprocess"}
            if name == "cli_help"
            else {"Pipeline Output Status", "No output directory found"})
        markers = value.get("required_markers")
        valid = (
            set(value) == {
                "exit_code", "stdout_present", "stderr_empty",
                "required_markers"}
            and value["exit_code"] == 0
            and value["stdout_present"] is True
            and value["stderr_empty"] is True
            and isinstance(markers, dict)
            and set(markers) == expected_markers
            and all(item is True for item in markers.values())
        )
    elif name == "service_composition":
        valid = value == {
            "composition_type": "ServiceApplicationComposition",
            "runtime_factory": "service_runtime.RagApplicationService",
            "http_factory": "service_http.create_app",
            "rag_loaded": False,
            "eval_loaded": False,
            "ui_loaded": False,
        }
    elif name == "worker_imports":
        valid = value == {
            "worker_module": "service_search_worker",
            "ui_module": "ui",
            "worker_has_main": True,
            "ui_has_vector_worker": True,
        }
    elif name == "offline_export_retrieval":
        valid = (
            set(value) == {
                "record_count", "stable_id_count", "context_segment_count",
                "context_characters", "markdown_bytes", "result_sha256"}
            and value["record_count"] == 3
            and value["stable_id_count"] == 3
            and value["context_segment_count"] == 2
            and _valid_count(value["context_characters"], positive=True)
            and _valid_count(value["markdown_bytes"], positive=True)
            and _SHA256_RE.fullmatch(str(value["result_sha256"])) is not None
        )
    else:
        valid = False
    if not valid:
        raise PhaseA0BenchmarkError("scenario contract was invalid")


def _validate_numeric_summary(value: object, *, integer: bool) -> None:
    if not isinstance(value, dict) or set(value) != {
            "minimum", "median", "p95", "maximum"}:
        raise PhaseA0BenchmarkError("diagnostic summary fields were invalid")
    samples = [value[name] for name in ("minimum", "median", "p95", "maximum")]
    if any(
            isinstance(sample, bool)
            or not isinstance(sample, int if integer else (int, float))
            or (not integer and not math.isfinite(sample))
            or sample < 0
            for sample in samples):
        raise PhaseA0BenchmarkError("diagnostic summary value was invalid")
    if not samples[0] <= samples[1] <= samples[2] <= samples[3]:
        raise PhaseA0BenchmarkError("diagnostic summary order was invalid")


def _validate_report_diagnostics(value: object) -> None:
    expected = {
        "wall_time_ms", "peak_rss_bytes", "loaded_module_count",
        "first_party_loaded_count", "first_party_loaded_sha256", "output"}
    if not isinstance(value, dict) or set(value) != expected:
        raise PhaseA0BenchmarkError("report diagnostic fields were invalid")
    _validate_numeric_summary(value["wall_time_ms"], integer=False)
    _validate_numeric_summary(value["loaded_module_count"], integer=True)
    _validate_numeric_summary(value["first_party_loaded_count"], integer=True)
    if _SHA256_RE.fullmatch(
            str(value["first_party_loaded_sha256"])) is None:
        raise PhaseA0BenchmarkError("report import identity was invalid")
    peak = value["peak_rss_bytes"]
    if not isinstance(peak, dict) or not isinstance(peak.get("available"), bool):
        raise PhaseA0BenchmarkError("report peak RSS evidence was invalid")
    if peak["available"]:
        if set(peak) != {
                "available", "minimum", "median", "p95", "maximum"}:
            raise PhaseA0BenchmarkError("report peak RSS fields were invalid")
        _validate_numeric_summary(
            {key: peak[key] for key in (
                "minimum", "median", "p95", "maximum")}, integer=True)
    elif set(peak) != {"available"}:
        raise PhaseA0BenchmarkError("report unavailable RSS fields were invalid")
    if value["output"] is not None:
        _validate_output_pair(value["output"])


def _probe_main(name: str) -> int:
    probe = _PROBES.get(name)
    if probe is None:
        print(json.dumps({"ok": False, "error_type": "UnknownScenario"}))
        return 2
    try:
        contract, diagnostics = probe()
        payload = {
            "ok": True,
            "scenario": name,
            "contract": contract,
            "diagnostics": {
                **diagnostics,
                "peak_rss_bytes": _peak_rss_bytes(),
            },
        }
        encoded = _canonical_bytes(payload)
    except BaseException as exc:
        payload = {
            "ok": False,
            "error_type": type(exc).__name__,
        }
        encoded = _canonical_bytes(payload)
    sys.stdout.buffer.write(encoded + b"\n")
    return 0 if payload.get("ok") else 1


def safe_child_environment(temporary_root: Path) -> dict[str, str]:
    """Build a minimal environment without provider credentials or proxies."""
    environment = {
        name: os.environ[name]
        for name in _SAFE_CHILD_ENVIRONMENT
        if os.environ.get(name)
    }
    environment.update({
        "NO_COLOR": "1",
        "PYTHONDONTWRITEBYTECODE": "1",
        "PYTHONIOENCODING": "utf-8",
        "PYTHONNOUSERSITE": "1",
        "PYTHONPATH": str(PROJECT_ROOT),
        "PYTHONUTF8": "1",
        "TEMP": str(temporary_root),
        "TMP": str(temporary_root),
        "TMPDIR": str(temporary_root),
    })
    if os.name == "nt":
        environment.update({
            "APPDATA": str(temporary_root),
            "LOCALAPPDATA": str(temporary_root),
            "USERPROFILE": str(temporary_root),
        })
    return environment


def _strict_probe_payload(raw: bytes, *, scenario: str) -> dict[str, Any]:
    if not raw or len(raw) > MAX_PROBE_OUTPUT_BYTES:
        raise PhaseA0BenchmarkError("probe emitted an invalid response size")
    try:
        payload = json.loads(raw.decode("utf-8"))
    except (UnicodeError, json.JSONDecodeError) as exc:
        raise PhaseA0BenchmarkError("probe emitted invalid JSON") from exc
    if not isinstance(payload, dict) or set(payload) != {
            "ok", "scenario", "contract", "diagnostics"}:
        raise PhaseA0BenchmarkError("probe response schema was invalid")
    if payload["ok"] is not True or payload["scenario"] != scenario:
        raise PhaseA0BenchmarkError("probe did not report successful completion")
    if not isinstance(payload["contract"], dict):
        raise PhaseA0BenchmarkError("probe contract was invalid")
    if not isinstance(payload["diagnostics"], dict):
        raise PhaseA0BenchmarkError("probe diagnostics were invalid")
    _validate_contract(scenario, payload["contract"])
    _validate_probe_diagnostics(payload["diagnostics"])
    return payload


def run_probe(
        scenario: str, *, timeout_seconds: float = DEFAULT_TIMEOUT_SECONDS,
) -> dict[str, Any]:
    """Run one scenario in an empty, credential-free subprocess."""
    if scenario not in _PROBES:
        raise PhaseA0BenchmarkError("unknown Phase A0 scenario")
    if (isinstance(timeout_seconds, bool)
            or not isinstance(timeout_seconds, (int, float))
            or not math.isfinite(timeout_seconds)
            or not 0.1 <= timeout_seconds <= 300.0):
        raise ValueError("timeout_seconds must be finite and between 0.1 and 300")
    with tempfile.TemporaryDirectory(prefix="rag-phase-a0-") as temp_name:
        temporary_root = Path(temp_name)
        started = time.perf_counter_ns()
        try:
            result = subprocess.run(
                [
                    sys.executable,
                    str(Path(__file__).resolve()),
                    "--_probe",
                    scenario,
                ],
                cwd=temporary_root,
                env=safe_child_environment(temporary_root),
                check=False,
                stdout=subprocess.PIPE,
                stderr=subprocess.PIPE,
                timeout=float(timeout_seconds),
            )
        except subprocess.TimeoutExpired as exc:
            raise PhaseA0BenchmarkError("probe exceeded its deadline") from exc
        wall_time_ms = (time.perf_counter_ns() - started) / 1_000_000
    if result.returncode != 0 or result.stderr:
        raise PhaseA0BenchmarkError("probe failed without publishing evidence")
    payload = _strict_probe_payload(result.stdout, scenario=scenario)
    payload["diagnostics"]["wall_time_ms"] = round(wall_time_ms, 3)
    return payload


def _numeric_summary(values: Sequence[int | float]) -> dict[str, int | float]:
    if not values:
        raise PhaseA0BenchmarkError("diagnostic samples were empty")
    normalized: list[float] = []
    all_integer = True
    for value in values:
        if (isinstance(value, bool) or not isinstance(value, (int, float))
                or not math.isfinite(value) or value < 0):
            raise PhaseA0BenchmarkError("diagnostic sample was invalid")
        normalized.append(float(value))
        all_integer = all_integer and isinstance(value, int)
    ordered = sorted(normalized)
    percentile_index = max(0, math.ceil(0.95 * len(ordered)) - 1)
    summary: dict[str, int | float] = {
        "minimum": ordered[0],
        "median": statistics.median(ordered),
        "p95": ordered[percentile_index],
        "maximum": ordered[-1],
    }
    if all_integer:
        return {name: int(value) for name, value in summary.items()}
    return {name: round(float(value), 3) for name, value in summary.items()}


def summarize_runs(name: str, runs: Sequence[Mapping[str, Any]]) -> dict[str, Any]:
    """Require deterministic contracts and summarize diagnostic variability."""
    if not runs:
        raise PhaseA0BenchmarkError("scenario runs were empty")
    contracts = [run.get("contract") for run in runs]
    if any(not isinstance(contract, dict) for contract in contracts):
        raise PhaseA0BenchmarkError("scenario contract was invalid")
    canonical_contract = _canonical_bytes(contracts[0])
    if any(_canonical_bytes(contract) != canonical_contract
           for contract in contracts[1:]):
        raise PhaseA0BenchmarkError("scenario contract changed between repetitions")

    diagnostics = [run.get("diagnostics") for run in runs]
    if any(not isinstance(value, dict) for value in diagnostics):
        raise PhaseA0BenchmarkError("scenario diagnostics were invalid")
    outputs = [value.get("output") for value in diagnostics]
    output = None
    if any(value is not None for value in outputs):
        if any(value != outputs[0] for value in outputs[1:]):
            raise PhaseA0BenchmarkError("scenario output changed between repetitions")
        output = outputs[0]

    rss_values = [value.get("peak_rss_bytes") for value in diagnostics]
    if all(value is None for value in rss_values):
        rss_summary: dict[str, object] = {"available": False}
    elif any(value is None for value in rss_values):
        raise PhaseA0BenchmarkError("peak RSS availability changed between runs")
    else:
        rss_summary = {
            "available": True,
            **_numeric_summary(rss_values),  # type: ignore[arg-type]
        }

    result = {
        "name": name,
        "repetitions": len(runs),
        "contract": contracts[0],
        "contract_sha256": _sha256_bytes(canonical_contract),
        "diagnostics": {
            "wall_time_ms": _numeric_summary([
                value.get("wall_time_ms") for value in diagnostics]),
            "peak_rss_bytes": rss_summary,
            "loaded_module_count": _numeric_summary([
                value.get("loaded_module_count") for value in diagnostics]),
            "first_party_loaded_count": _numeric_summary([
                value.get("first_party_loaded_count")
                for value in diagnostics]),
            "first_party_loaded_sha256": diagnostics[0].get(
                "first_party_loaded_sha256"),
            "output": output,
        },
    }
    first_party_digests = {
        value.get("first_party_loaded_sha256") for value in diagnostics}
    if (len(first_party_digests) != 1
            or _SHA256_RE.fullmatch(str(next(iter(first_party_digests)))) is None):
        raise PhaseA0BenchmarkError(
            "first-party import identity changed between repetitions")
    return result


def _report_without_digest(report: Mapping[str, Any]) -> dict[str, Any]:
    return {key: value for key, value in report.items() if key != "report_sha256"}


def run_benchmark(
        *, root: Path = PROJECT_ROOT,
        scenarios: Sequence[str] = SCENARIOS,
        repetitions: int = DEFAULT_REPETITIONS,
        timeout_seconds: float = DEFAULT_TIMEOUT_SECONDS,
) -> dict[str, Any]:
    if (isinstance(repetitions, bool) or not isinstance(repetitions, int)
            or not MIN_REPETITIONS <= repetitions <= MAX_REPETITIONS):
        raise ValueError(
            f"repetitions must be from {MIN_REPETITIONS} to {MAX_REPETITIONS}")
    requested = tuple(scenarios)
    if (not requested or len(requested) != len(set(requested))
            or any(name not in SCENARIOS for name in requested)):
        raise ValueError("scenarios must be unique known Phase A0 names")
    selected_names = set(requested)
    selected = tuple(name for name in SCENARIOS if name in selected_names)
    scenario_reports = []
    for name in selected:
        runs = [
            run_probe(name, timeout_seconds=timeout_seconds)
            for _ in range(repetitions)
        ]
        scenario_reports.append(summarize_runs(name, runs))
    report: dict[str, Any] = {
        "schema_version": REPORT_SCHEMA_VERSION,
        "benchmark": BENCHMARK_NAME,
        "source": repository_identity(root),
        "runtime": runtime_identity(),
        "system": machine_identity(),
        "inputs": dependency_identities(root),
        "repetitions": repetitions,
        "scenarios": scenario_reports,
    }
    report["report_sha256"] = _sha256_bytes(_canonical_bytes(report))
    validate_report(report)
    return report


def validate_report(report: object) -> dict[str, Any]:
    """Strictly validate a report and its self-digest."""
    if not isinstance(report, dict) or set(report) != {
        "schema_version", "benchmark", "source", "runtime", "system",
            "inputs", "repetitions", "scenarios", "report_sha256"}:
        raise PhaseA0BenchmarkError("report fields were invalid")
    if (report["schema_version"] != REPORT_SCHEMA_VERSION
            or report["benchmark"] != BENCHMARK_NAME):
        raise PhaseA0BenchmarkError("report identity was invalid")
    repetitions = report["repetitions"]
    if (isinstance(repetitions, bool) or not isinstance(repetitions, int)
            or not MIN_REPETITIONS <= repetitions <= MAX_REPETITIONS):
        raise PhaseA0BenchmarkError("report repetition count was invalid")
    source = report["source"]
    if (not isinstance(source, dict)
            or set(source) != {
                "commit", "worktree_clean", "tracked_diff_sha256"}
            or _COMMIT_RE.fullmatch(str(source.get("commit"))) is None
            or not isinstance(source.get("worktree_clean"), bool)
            or _SHA256_RE.fullmatch(
                str(source.get("tracked_diff_sha256"))) is None):
        raise PhaseA0BenchmarkError("report source identity was invalid")
    runtime = report["runtime"]
    if (not isinstance(runtime, dict)
            or set(runtime) != {
                "implementation", "python_version", "pointer_bits"}
            or not all(isinstance(runtime[name], str) and runtime[name]
                       for name in ("implementation", "python_version"))
            or runtime["pointer_bits"] not in {32, 64}):
        raise PhaseA0BenchmarkError("report runtime/system identity was invalid")
    system = report["system"]
    if (not isinstance(system, dict)
            or set(system) != {
                "os", "os_release", "machine", "processor",
                "logical_cpu_count", "total_memory_bytes"}
            or not all(
                isinstance(system[name], str) and system[name]
                and len(system[name]) <= 160
                and "\n" not in system[name] and "\r" not in system[name]
                for name in ("os", "os_release", "machine", "processor"))
            or (system["logical_cpu_count"] is not None and (
                isinstance(system["logical_cpu_count"], bool)
                or not isinstance(system["logical_cpu_count"], int)
                or system["logical_cpu_count"] < 1))
            or (system["total_memory_bytes"] is not None and (
                isinstance(system["total_memory_bytes"], bool)
                or not isinstance(system["total_memory_bytes"], int)
                or system["total_memory_bytes"] < 1))):
        raise PhaseA0BenchmarkError("report runtime/system identity was invalid")
    inputs = report["inputs"]
    if not isinstance(inputs, list) or not inputs:
        raise PhaseA0BenchmarkError("report lock identities were invalid")
    input_names = []
    for item in inputs:
        if (not isinstance(item, dict)
                or set(item) != {"name", "bytes", "sha256"}
                or not isinstance(item["name"], str)
                or Path(item["name"]).name != item["name"]
                or isinstance(item["bytes"], bool)
                or not isinstance(item["bytes"], int)
                or item["bytes"] < 0
                or _SHA256_RE.fullmatch(str(item["sha256"])) is None):
            raise PhaseA0BenchmarkError("report lock identity was invalid")
        input_names.append(item["name"])
    if input_names != sorted(input_names) or len(input_names) != len(set(input_names)):
        raise PhaseA0BenchmarkError("report lock order was invalid")

    scenarios = report["scenarios"]
    if not isinstance(scenarios, list) or not scenarios:
        raise PhaseA0BenchmarkError("report scenarios were invalid")
    names = []
    for scenario in scenarios:
        if (not isinstance(scenario, dict)
                or set(scenario) != {
                    "name", "repetitions", "contract", "contract_sha256",
                    "diagnostics"}
                or scenario["name"] not in SCENARIOS
                or scenario["repetitions"] != repetitions
                or not isinstance(scenario["contract"], dict)
                or _SHA256_RE.fullmatch(
                    str(scenario["contract_sha256"])) is None
                or scenario["contract_sha256"] != _sha256_bytes(
                    _canonical_bytes(scenario["contract"]))
                or not isinstance(scenario["diagnostics"], dict)):
            raise PhaseA0BenchmarkError("report scenario was invalid")
        _validate_contract(scenario["name"], scenario["contract"])
        _validate_report_diagnostics(scenario["diagnostics"])
        names.append(scenario["name"])
    if (len(names) != len(set(names))
            or names != [name for name in SCENARIOS if name in set(names)]):
        raise PhaseA0BenchmarkError("report scenario names were duplicated")
    if _SHA256_RE.fullmatch(str(report["report_sha256"])) is None:
        raise PhaseA0BenchmarkError("report digest was invalid")
    expected_digest = _sha256_bytes(_canonical_bytes(_report_without_digest(report)))
    if report["report_sha256"] != expected_digest:
        raise PhaseA0BenchmarkError("report digest did not match its contents")
    return report


def compare_contracts(report: object, baseline: object) -> None:
    """Compare portable contracts and exact lock inputs, never timing values."""
    current = validate_report(report)
    expected = validate_report(baseline)
    if current["inputs"] != expected["inputs"]:
        raise PhaseA0BenchmarkError("benchmark lock identities changed")
    current_contracts = {
        item["name"]: item["contract"] for item in current["scenarios"]}
    expected_contracts = {
        item["name"]: item["contract"] for item in expected["scenarios"]}
    if current_contracts != expected_contracts:
        raise PhaseA0BenchmarkError("Phase A0 behavioral contract changed")


def _read_report(path: Path) -> dict[str, Any]:
    raw = path.read_bytes()
    if not raw or len(raw) > 10_000_000:
        raise PhaseA0BenchmarkError("baseline report size was invalid")
    try:
        payload = json.loads(raw.decode("utf-8"))
    except (UnicodeError, json.JSONDecodeError) as exc:
        raise PhaseA0BenchmarkError("baseline report was invalid JSON") from exc
    return validate_report(payload)


def _write_report(path: Path, report: Mapping[str, Any]) -> None:
    encoded = json.dumps(
        report, ensure_ascii=False, sort_keys=True, indent=2,
        allow_nan=False).encode("utf-8") + b"\n"
    path.parent.mkdir(parents=True, exist_ok=True)
    temporary = path.with_name(f".{path.name}.{os.getpid()}.tmp")
    try:
        with temporary.open("xb") as handle:
            handle.write(encoded)
            handle.flush()
            os.fsync(handle.fileno())
        os.replace(temporary, path)
    finally:
        try:
            temporary.unlink()
        except FileNotFoundError:
            pass


def _parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        description="Run content-free RAG Phase A0 architecture benchmarks")
    parser.add_argument("--output", type=Path)
    parser.add_argument("--check", type=Path, metavar="BASELINE")
    parser.add_argument(
        "--scenario", action="append", choices=SCENARIOS,
        help="Run only this scenario (repeatable; default: all)")
    parser.add_argument(
        "--repetitions", type=int, default=DEFAULT_REPETITIONS)
    parser.add_argument(
        "--timeout-seconds", type=float, default=DEFAULT_TIMEOUT_SECONDS)
    parser.add_argument(
        "--require-clean", action="store_true",
        help="Refuse to publish evidence from a tracked dirty worktree")
    return parser


def main(argv: Sequence[str] | None = None) -> int:
    arguments = list(sys.argv[1:] if argv is None else argv)
    if len(arguments) == 2 and arguments[0] == "--_probe":
        return _probe_main(arguments[1])
    args = _parser().parse_args(arguments)
    try:
        report = run_benchmark(
            scenarios=(tuple(args.scenario) if args.scenario else SCENARIOS),
            repetitions=args.repetitions,
            timeout_seconds=args.timeout_seconds,
        )
        if args.require_clean and not report["source"]["worktree_clean"]:
            raise PhaseA0BenchmarkError("tracked worktree is not clean")
        if args.check is not None:
            compare_contracts(report, _read_report(args.check))
        if args.output is None:
            sys.stdout.buffer.write(_canonical_bytes(report) + b"\n")
        else:
            _write_report(args.output, report)
            print(
                f"Phase A0 benchmark passed: {len(report['scenarios'])} "
                f"scenarios x {report['repetitions']} repetitions.")
    except (OSError, ValueError, PhaseA0BenchmarkError):
        print("Phase A0 benchmark failed.", file=sys.stderr)
        return 1
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
