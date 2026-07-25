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
import runpy
import stat
import statistics
import subprocess
import sys
import tempfile
import time
from collections.abc import Callable, Mapping, Sequence
from pathlib import Path
from types import SimpleNamespace
from typing import Any


PROJECT_ROOT = Path(__file__).resolve().parents[1]
if str(PROJECT_ROOT) not in sys.path:
    sys.path.insert(0, str(PROJECT_ROOT))

REPORT_SCHEMA_VERSION = 2
BENCHMARK_NAME = "phase-a0"
DEFAULT_REPETITIONS = 5
MIN_REPETITIONS = 5
MAX_REPETITIONS = 50
DEFAULT_TIMEOUT_SECONDS = 30.0
MAX_PROBE_OUTPUT_BYTES = 1_000_000
CANONICAL_IMPLEMENTATION = "CPython"
CANONICAL_PYTHON_MAJOR_MINOR = "3.12"
CANONICAL_POINTER_BITS = 64
_CONTAINED_CHILD_ENV = "RAG_PHASE_A0_CONTAINED_CHILD"
_CONTAINED_RUN_ID_ENV = "RAG_PHASE_A0_CONTAINED_RUN_ID"
_GUARD_TRACE_ENV = "RAG_PHASE_A0_GUARD_TRACE"
_GUARD_ROLE_ENV = "RAG_PHASE_A0_GUARD_ROLE"
_TEST_BREAK_SUPERVISION_ENV = "RAG_PHASE_A0_TEST_ONLY_BREAK_SUPERVISION"
_TEST_BREAK_SUPERVISION_VALUE = "phase-a0-audit-mutation-v1"
_GUARD_VERSION = 1
_RSS_SCOPE = "process_only_excludes_descendants"

SCENARIOS = (
    "cold_import_rag",
    "cli_help",
    "cli_info_empty",
    "service_composition",
    "worker_imports",
    "worker_entrypoints",
    "isolation_guard",
    "noop_resume",
    "offline_export_retrieval",
)

_SAFE_CHILD_ENVIRONMENT = (
    "COMSPEC",
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


def _strict_json_bytes(raw: bytes, *, description: str) -> object:
    """Decode one bounded UTF-8 JSON value without ambiguous extensions."""
    if not raw:
        raise PhaseA0BenchmarkError(f"{description} was empty")

    def reject_constant(_value: str) -> None:
        raise ValueError("non-standard JSON number")

    def finite_float(value: str) -> float:
        parsed = float(value)
        if not math.isfinite(parsed):
            raise ValueError("non-finite JSON number")
        return parsed

    def reject_duplicates(pairs: list[tuple[str, object]]) -> dict:
        value = {}
        for key, item in pairs:
            if key in value:
                raise ValueError("duplicate JSON field")
            value[key] = item
        return value

    try:
        return json.loads(
            raw.decode("utf-8"),
            parse_constant=reject_constant,
            parse_float=finite_float,
            object_pairs_hook=reject_duplicates,
        )
    except (UnicodeError, json.JSONDecodeError, ValueError) as exc:
        raise PhaseA0BenchmarkError(f"{description} was invalid JSON") from exc


def _read_bounded_file(
        path: Path, *, max_bytes: int, description: str,
) -> bytes:
    """Stat before reading and reject non-regular or changing files."""
    try:
        before = path.stat()
    except OSError as exc:
        raise PhaseA0BenchmarkError(f"{description} was unavailable") from exc
    if (not stat.S_ISREG(before.st_mode)
            or before.st_size <= 0 or before.st_size > max_bytes):
        raise PhaseA0BenchmarkError(f"{description} size was invalid")
    try:
        with path.open("rb") as handle:
            opened = os.fstat(handle.fileno())
            raw = handle.read(max_bytes + 1)
            after = os.fstat(handle.fileno())
    except OSError as exc:
        raise PhaseA0BenchmarkError(f"{description} could not be read") from exc
    stable_fields = ("st_dev", "st_ino", "st_size", "st_mtime_ns")
    if (len(raw) != before.st_size
            or any(getattr(opened, name) != getattr(before, name)
                   for name in stable_fields)
            or any(getattr(after, name) != getattr(opened, name)
                   for name in stable_fields)):
        raise PhaseA0BenchmarkError(f"{description} changed while being read")
    return raw


_SITECUSTOMIZE_SOURCE = r'''"""Generated Phase A0 isolation guard."""
import json
import os
from pathlib import Path
import sys

_DENIAL = "phase-a0 isolation guard denied operator-home or network access"
_original_path_expanduser = Path.expanduser
_original_expanduser = os.path.expanduser

def _deny_home(_path_type):
    raise RuntimeError(_DENIAL)

def _guard_path_expanduser(value):
    if os.fspath(value).startswith("~"):
        raise RuntimeError(_DENIAL)
    return _original_path_expanduser(value)

def _guard_expanduser(value):
    if os.fspath(value).startswith("~"):
        raise RuntimeError(_DENIAL)
    return _original_expanduser(value)

def _deny_network(event, _arguments):
    if event.startswith("socket."):
        raise RuntimeError(_DENIAL)

Path.home = classmethod(_deny_home)
Path.expanduser = _guard_path_expanduser
os.path.expanduser = _guard_expanduser
sys.addaudithook(_deny_network)

trace_path = os.environ.get("RAG_PHASE_A0_GUARD_TRACE")
if trace_path:
    event = {
        "guard_version": 1,
        "pid": os.getpid(),
        "ppid": os.getppid(),
        "production_supervised": (
            os.environ.get("RAG_PIPELINE_SUPERVISED_CHILD") == "1"),
        "contained": (
            os.environ.get("RAG_PHASE_A0_CONTAINED_CHILD") == "1"),
        "role": os.environ.get("RAG_PHASE_A0_GUARD_ROLE", "unspecified"),
    }
    descriptor = os.open(
        trace_path, os.O_WRONLY | os.O_CREAT | os.O_APPEND, 0o600)
    try:
        os.write(descriptor, (
            json.dumps(event, sort_keys=True, separators=(",", ":")) + "\n"
        ).encode("utf-8"))
    finally:
        os.close(descriptor)

if (os.environ.get("RAG_PHASE_A0_TEST_ONLY_BREAK_SUPERVISION")
        == "phase-a0-audit-mutation-v1"
        and os.environ.get("RAG_PIPELINE_SUPERVISED_CHILD") != "1"):
    import process_supervision

    def _bypass_supervision(argv=None, **kwargs):
        cli_args = list(sys.argv[1:] if argv is None else argv)
        kwargs["main_fn"](cli_args)
        return 0

    process_supervision._run_supervised_entrypoint = _bypass_supervision
'''


def _write_private_generated_text(path: Path, value: str) -> None:
    path.parent.mkdir(mode=0o700, parents=True, exist_ok=True)
    flags = os.O_WRONLY | os.O_CREAT | os.O_EXCL
    if hasattr(os, "O_BINARY"):
        flags |= os.O_BINARY
    descriptor = os.open(path, flags, 0o600)
    try:
        os.write(descriptor, value.encode("utf-8"))
    finally:
        os.close(descriptor)
    try:
        path.chmod(0o600)
    except OSError:
        pass


def _install_sitecustomize_guard(root: Path) -> Path:
    guard_root = root / "phase-a0-isolation"
    guard_root.mkdir(mode=0o700, parents=True, exist_ok=False)
    _write_private_generated_text(
        guard_root / "sitecustomize.py", _SITECUSTOMIZE_SOURCE)
    return guard_root


def _exact_environment_overrides(
        environment: Mapping[str, str],
) -> dict[str, str | None]:
    forbidden = {"home", "codex_home"}
    if any(name.casefold() in forbidden for name in environment):
        raise PhaseA0BenchmarkError(
            "contained environment included a forbidden home variable")
    overrides: dict[str, str | None] = {
        name: None for name in os.environ
        if name.casefold() != _CONTAINED_CHILD_ENV.casefold()}
    overrides.update(environment)
    return overrides


def _run_contained_process(
        script_path: Path, arguments: Sequence[str], *, cwd: Path,
        environment: Mapping[str, str], timeout_seconds: float,
        max_output_bytes: int = MAX_PROBE_OUTPUT_BYTES,
) -> SimpleNamespace:
    """Run one Python tree with bounded files and confirmed OS cleanup."""
    if (isinstance(max_output_bytes, bool)
            or not isinstance(max_output_bytes, int)
            or max_output_bytes < 1):
        raise ValueError("max_output_bytes must be a positive integer")
    process_supervision = importlib.import_module("process_supervision")
    config = process_supervision.SupervisionConfig(
        supervised_child_env=_CONTAINED_CHILD_ENV,
        run_id_env=_CONTAINED_RUN_ID_ENV,
        terminate_grace=2.0,
        poll_interval=0.02,
        start_gate_timeout=10.0,
    )
    try:
        with tempfile.TemporaryDirectory(
                prefix="phase-a0-capture-", dir=cwd) as capture_name:
            capture_root = Path(capture_name)
            stdout_path = capture_root / "stdout.bin"
            stderr_path = capture_root / "stderr.bin"
            with stdout_path.open("x+b") as stdout_handle, \
                    stderr_path.open("x+b") as stderr_handle:
                for path in (stdout_path, stderr_path):
                    try:
                        path.chmod(0o600)
                    except OSError:
                        pass

                def enforce_output_bound(_process) -> None:
                    if any(os.fstat(handle.fileno()).st_size > max_output_bytes
                           for handle in (stdout_handle, stderr_handle)):
                        raise PhaseA0BenchmarkError(
                            "contained process exceeded its output bound")

                exit_code = process_supervision._run_cli_with_deadline(
                    Path(script_path),
                    list(arguments),
                    operation="Phase A0 contained probe",
                    timeout=float(timeout_seconds),
                    config=config,
                    working_directory=Path(cwd),
                    environment_overrides=_exact_environment_overrides(
                        environment),
                    heartbeat=enforce_output_bound,
                    stdout_target=stdout_handle,
                    stderr_target=stderr_handle,
                    warn_fn=lambda _message: None,
                )
                enforce_output_bound(None)
                stdout_handle.flush()
                stderr_handle.flush()
            stdout = (
                b"" if stdout_path.stat().st_size == 0 else _read_bounded_file(
                    stdout_path, max_bytes=max_output_bytes,
                    description="contained stdout"))
            stderr = (
                b"" if stderr_path.stat().st_size == 0 else _read_bounded_file(
                    stderr_path, max_bytes=max_output_bytes,
                    description="contained stderr"))
    except PhaseA0BenchmarkError:
        raise
    except BaseException as exc:
        raise PhaseA0BenchmarkError(
            "contained process failed with confirmed cleanup") from exc
    return SimpleNamespace(
        returncode=exit_code,
        stdout=stdout,
        stderr=stderr,
        cleanup_confirmed=True,
    )


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


def execution_profile() -> dict[str, object]:
    implementation = platform.python_implementation()
    major_minor = f"{sys.version_info.major}.{sys.version_info.minor}"
    pointer_bits = ctypes.sizeof(ctypes.c_void_p) * 8
    return {
        "implementation": implementation,
        "python_major_minor": major_minor,
        "pointer_bits": pointer_bits,
        "canonical": (
            implementation == CANONICAL_IMPLEMENTATION
            and major_minor == CANONICAL_PYTHON_MAJOR_MINOR
            and pointer_bits == CANONICAL_POINTER_BITS
        ),
    }


def _scenario_set_identity(names: Sequence[str]) -> dict[str, object]:
    selected = list(names)
    return {
        "names": selected,
        "sha256": _sha256_bytes(_canonical_bytes(selected)),
        "authoritative": tuple(selected) == SCENARIOS,
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
    names = {
        path.stem for path in PROJECT_ROOT.glob("*.py") if path.is_file()}
    names.update(
        path.name for path in PROJECT_ROOT.iterdir()
        if path.is_dir() and (
            (path / "__init__.py").is_file() or path.name == "tools"))
    return names


def _module_root_identity(names: Sequence[str]) -> tuple[list[str], str]:
    roots = sorted(set(names))
    return roots, _sha256_bytes("\n".join(roots).encode("utf-8"))


def _loaded_module_diagnostics() -> dict[str, object]:
    loaded_roots = {
        name.partition(".")[0] for name in sys.modules
        if name and not name.startswith("_")}
    ignored = {"builtins", "sitecustomize", "usercustomize"}
    loaded_roots.difference_update(ignored)
    first_party, first_party_sha256 = _module_root_identity(
        loaded_roots.intersection(_root_module_names()))
    stdlib, stdlib_sha256 = _module_root_identity(
        loaded_roots.intersection(sys.stdlib_module_names))
    third_party, third_party_sha256 = _module_root_identity(
        loaded_roots.difference(first_party).difference(stdlib))
    return {
        "loaded_module_count": len(sys.modules),
        "first_party_loaded_count": len(first_party),
        "first_party_loaded_roots": first_party,
        "first_party_loaded_sha256": first_party_sha256,
        "third_party_loaded_count": len(third_party),
        "third_party_loaded_roots": third_party,
        "third_party_loaded_sha256": third_party_sha256,
        "stdlib_loaded_count": len(stdlib),
        "stdlib_loaded_roots": stdlib,
        "stdlib_loaded_sha256": stdlib_sha256,
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
    rag_path = PROJECT_ROOT / "rag.py"
    supervision = importlib.import_module("runtime_supervision")
    supervised_child_env = supervision.SUPERVISED_CHILD_ENV
    original_argv = sys.argv[:]
    original_supervised_child = os.environ.get(supervised_child_env)

    def run_entrypoint() -> object:
        try:
            sys.argv = [str(rag_path), *arguments]
            os.environ[supervised_child_env] = "1"
            return runpy.run_path(str(rag_path), run_name="__main__")
        finally:
            sys.argv = original_argv
            if original_supervised_child is None:
                os.environ.pop(supervised_child_env, None)
            else:
                os.environ[supervised_child_env] = original_supervised_child

    exit_code, stdout, stderr, _ = _capture(run_entrypoint)
    contract = {
        "entrypoint": rag_path.name,
        "execution_mode": "supervised_child",
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


def _normalized_process_text(value: bytes) -> str:
    try:
        text = value.decode("utf-8")
    except UnicodeError as exc:
        raise PhaseA0BenchmarkError(
            "contained process output was not UTF-8") from exc
    text = text.replace("\r\n", "\n")
    if "\r" in text:
        raise PhaseA0BenchmarkError(
            "contained process output used unsupported line endings")
    return text


def _guarded_child_environment(
        temporary_root: Path, guard_root: Path, *, trace_path: Path,
        role: str, break_outer_supervision: bool = False,
) -> dict[str, str]:
    environment = safe_child_environment(
        temporary_root, sitecustomize_root=guard_root)
    environment[_GUARD_TRACE_ENV] = str(trace_path)
    environment[_GUARD_ROLE_ENV] = role
    environment.pop("RAG_PIPELINE_SUPERVISED_CHILD", None)
    if break_outer_supervision:
        environment[_TEST_BREAK_SUPERVISION_ENV] = (
            _TEST_BREAK_SUPERVISION_VALUE)
    return environment


def _read_guard_trace(path: Path, *, max_events: int = 8) -> list[dict]:
    raw = _read_bounded_file(
        path, max_bytes=max_events * 512,
        description="isolation guard trace")
    lines = raw.splitlines()
    if not lines or len(lines) > max_events:
        raise PhaseA0BenchmarkError("isolation guard trace count was invalid")
    events = []
    for line in lines:
        event = _strict_json_bytes(line, description="isolation guard event")
        if (not isinstance(event, dict)
                or set(event) != {
                    "guard_version", "pid", "ppid", "production_supervised",
                    "contained", "role"}
                or event["guard_version"] != _GUARD_VERSION
                or not _valid_count(event["pid"], positive=True)
                or not _valid_count(event["ppid"], positive=True)
                or not isinstance(event["production_supervised"], bool)
                or event["contained"] is not True
                or not isinstance(event["role"], str)
                or not event["role"]
                or len(event["role"]) > 64):
            raise PhaseA0BenchmarkError(
                "isolation guard event schema was invalid")
        events.append(event)
    return events


def _probe_cli_info_empty(
        *, _test_break_outer_supervision: bool = False,
) -> tuple[dict[str, object], dict[str, object]]:
    fixture_root = Path.cwd() / "generated-cli-info"
    fixture_root.mkdir(mode=0o700)
    guard_root = _install_sitecustomize_guard(fixture_root)
    trace_path = fixture_root / "guard-trace.jsonl"
    environment = _guarded_child_environment(
        fixture_root,
        guard_root,
        trace_path=trace_path,
        role="rag-info",
        break_outer_supervision=_test_break_outer_supervision,
    )
    result = _run_contained_process(
        PROJECT_ROOT / "rag.py",
        ("info",),
        cwd=fixture_root,
        environment=environment,
        timeout_seconds=DEFAULT_TIMEOUT_SECONDS,
    )
    events = _read_guard_trace(trace_path)
    if (len(events) != 2
            or events[0]["role"] != "rag-info"
            or events[1]["role"] != "rag-info"
            or events[0]["production_supervised"] is not False
            or events[1]["production_supervised"] is not True
            or events[1]["ppid"] != events[0]["pid"]):
        raise PhaseA0BenchmarkError(
            "rag outer-supervision trace was not satisfied")
    stdout = _normalized_process_text(result.stdout)
    stderr = _normalized_process_text(result.stderr)
    markers = ("Pipeline Output Status", "No output directory found")
    contract = {
        "entrypoint": "rag.py",
        "execution_mode": "outer_supervised_process",
        "exit_code": result.returncode,
        "stdout_present": bool(stdout),
        "stderr_empty": not bool(stderr),
        "required_markers": {marker: marker in stdout for marker in markers},
        "supervision": {
            "outer_processes": 1,
            "supervised_children": 1,
            "parent_child_link": True,
            "guarded_processes": len(events),
            "cleanup_confirmed": result.cleanup_confirmed,
        },
    }
    diagnostics = {
        **_loaded_module_diagnostics(),
        "output": {
            "stdout": _output_identity(stdout),
            "stderr": _output_identity(stderr),
        },
    }
    if (result.returncode != 0 or not stdout or stderr
            or not all(contract["required_markers"].values())):
        raise PhaseA0BenchmarkError(
            "supervised CLI probe contract was not satisfied")
    return contract, diagnostics


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


def _read_generated_json(path: Path, *, max_bytes: int = 16_384) -> dict:
    raw = _read_bounded_file(
        path, max_bytes=max_bytes,
        description="generated entrypoint envelope")
    payload = _strict_json_bytes(
        raw, description="generated entrypoint envelope")
    if not isinstance(payload, dict):
        raise PhaseA0BenchmarkError(
            "generated entrypoint envelope was not an object")
    return payload


def _launch_generated_entrypoint(
        script_name: str, arguments: Sequence[str], *, cwd: Path, role: str,
) -> SimpleNamespace:
    launch_root = cwd / f"{role}-launch"
    launch_root.mkdir(mode=0o700)
    guard_root = _install_sitecustomize_guard(launch_root)
    trace_path = launch_root / "guard-trace.jsonl"
    environment = _guarded_child_environment(
        launch_root, guard_root, trace_path=trace_path, role=role)
    completed = _run_contained_process(
        PROJECT_ROOT / script_name,
        arguments,
        cwd=cwd,
        environment=environment,
        timeout_seconds=DEFAULT_TIMEOUT_SECONDS,
    )
    events = _read_guard_trace(trace_path)
    if (len(events) != 1 or events[0]["role"] != role
            or events[0]["production_supervised"] is not False):
        raise PhaseA0BenchmarkError(
            "generated entrypoint isolation trace was invalid")
    if completed.stdout or completed.stderr:
        raise PhaseA0BenchmarkError(
            "generated entrypoint emitted unexpected console output")
    completed.guarded_processes = len(events)
    return completed


def _probe_worker_entrypoints(
) -> tuple[dict[str, object], dict[str, object]]:
    storage_policy = importlib.import_module("storage_policy")
    fixture_root = Path.cwd() / "generated-worker-fixtures"
    storage_policy.ensure_private_directory(fixture_root)

    ui_request = fixture_root / "ui-request.json"
    ui_result = fixture_root / "ui-result.json"
    storage_policy.atomic_write_private_json(
        ui_request,
        {"action": "phase_a0_invalid", "config": {}},
    )
    ui_process = _launch_generated_entrypoint(
        "ui.py",
        ("--vector-worker", str(ui_request), str(ui_result)),
        cwd=fixture_root,
        role="ui-worker",
    )
    ui_envelope = _read_generated_json(ui_result)

    service_request = fixture_root / "service-request.json"
    service_result = fixture_root / "service-result.json"
    storage_policy.atomic_write_private_json(service_request, {})
    service_process = _launch_generated_entrypoint(
        "service_search_worker.py",
        ("_search_worker", str(service_request), str(service_result)),
        cwd=fixture_root,
        role="service-worker",
    )
    service_envelope = _read_generated_json(service_result)

    contract = {
        "ui": {
            "entrypoint": "ui.py",
            "exit_code": ui_process.returncode,
            "guarded_processes": ui_process.guarded_processes,
            "cleanup_confirmed": ui_process.cleanup_confirmed,
            "envelope": ui_envelope,
        },
        "service": {
            "entrypoint": "service_search_worker.py",
            "exit_code": service_process.returncode,
            "guarded_processes": service_process.guarded_processes,
            "cleanup_confirmed": service_process.cleanup_confirmed,
            "envelope": service_envelope,
        },
    }
    return contract, _loaded_module_diagnostics()


_ISOLATION_PROBE_SOURCE = r'''import json
import os
from pathlib import Path
import socket
import subprocess
import sys

def denied(call):
    try:
        value = call()
    except RuntimeError:
        return True
    if hasattr(value, "close"):
        value.close()
    return False

def checks():
    return {
        "home_resolution_denied": denied(Path.home),
        "tilde_expansion_denied": denied(lambda: Path("~/x").expanduser()),
        "socket_creation_denied": denied(socket.socket),
        "dns_resolution_denied": denied(
            lambda: socket.getaddrinfo(None, 0)),
    }

if len(sys.argv) == 3 and sys.argv[1] == "--child":
    Path(sys.argv[2]).write_text(
        json.dumps(checks(), sort_keys=True, separators=(",", ":")),
        encoding="utf-8")
    raise SystemExit(0)

child_result = Path(sys.argv[1])
completed = subprocess.run(
    [sys.executable, __file__, "--child", str(child_result)],
    env=dict(os.environ), stdin=subprocess.DEVNULL,
    stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL,
    check=False, timeout=10)
payload = {
    "parent": checks(),
    "child": json.loads(child_result.read_text(encoding="utf-8")),
    "child_exit_code": completed.returncode,
}
print(json.dumps(payload, sort_keys=True, separators=(",", ":")))
'''


def _probe_isolation_guard() -> tuple[dict[str, object], dict[str, object]]:
    fixture_root = Path.cwd() / "generated-isolation-probe"
    fixture_root.mkdir(mode=0o700)
    script_path = fixture_root / "isolation_probe.py"
    child_result = fixture_root / "child-result.json"
    _write_private_generated_text(script_path, _ISOLATION_PROBE_SOURCE)
    guard_root = _install_sitecustomize_guard(fixture_root)
    trace_path = fixture_root / "guard-trace.jsonl"
    environment = _guarded_child_environment(
        fixture_root, guard_root,
        trace_path=trace_path, role="isolation-probe")
    completed = _run_contained_process(
        script_path,
        (str(child_result),),
        cwd=fixture_root,
        environment=environment,
        timeout_seconds=DEFAULT_TIMEOUT_SECONDS,
    )
    if completed.returncode != 0 or completed.stderr:
        raise PhaseA0BenchmarkError("isolation guard probe failed")
    payload = _strict_json_bytes(
        completed.stdout, description="isolation guard result")
    expected_checks = {
        "home_resolution_denied": True,
        "tilde_expansion_denied": True,
        "socket_creation_denied": True,
        "dns_resolution_denied": True,
    }
    if (not isinstance(payload, dict)
            or payload != {
                "parent": expected_checks,
                "child": expected_checks,
                "child_exit_code": 0,
            }):
        raise PhaseA0BenchmarkError(
            "isolation guard did not deny every operation")
    events = _read_guard_trace(trace_path)
    if (len(events) != 2
            or any(event["role"] != "isolation-probe" for event in events)
            or any(event["production_supervised"] for event in events)
            or events[1]["ppid"] != events[0]["pid"]):
        raise PhaseA0BenchmarkError(
            "isolation guard descendant trace was invalid")
    contract = {
        **expected_checks,
        "descendant_home_resolution_denied": payload["child"][
            "home_resolution_denied"],
        "descendant_network_denied": (
            payload["child"]["socket_creation_denied"]
            and payload["child"]["dns_resolution_denied"]),
        "guarded_processes": len(events),
        "parent_child_link": True,
        "cleanup_confirmed": completed.cleanup_confirmed,
        "native_executable_egress_covered": False,
    }
    return contract, _loaded_module_diagnostics()


def _resume_probe_args(rag) -> SimpleNamespace:
    return SimpleNamespace(
        collection=None,
        db_backend="chroma",
        embedding_model="phase-a0-local",
        structure_profile=rag.DEFAULT_STRUCTURE_PROFILE,
        full_reindex=False,
        batch_size=None,
        backend="auto",
        force=False,
        no_preprocess=False,
        max_tokens=512,
        min_words=1,
        dedup_threshold=0.9,
        llm_classify=False,
        zeroshot_classify=False,
        contextualize=False,
        reconstruct_headings=False,
        quality_score=False,
        cloud_url="",
        cloud_model="",
        cloud_key="",
        ollama_url="",
        ollama_model="",
        gemini_key="",
        llm_workers=1,
        thinking=False,
        split_chapters=False,
        raptor=False,
        db_lock_timeout=1.0,
    )


def _write_resume_conversion_fixture(
        rag, *, pdf: Path, paths: Mapping[str, Path], args: SimpleNamespace,
) -> dict:
    document = {
        "pages": {"1": {}},
        "texts": [{
            "self_ref": "#/texts/0",
            "label": "text",
            "content_layer": "body",
            "text": "A generated discussion of synthetic procedure.",
            "prov": [{"page_no": 1}],
        }],
    }
    rag._atomic_write_json(paths["doc"], document)
    rag._atomic_write_text(
        paths["converted_markdown"], "# Generated conversion\n")
    parameters = rag._conversion_parameters(
        batch_size_override=args.batch_size,
        backend=args.backend,
        auto_preprocess=not args.no_preprocess,
        ocr=None,
        watermark=None,
    )
    source_sha256 = rag._cached_artifact_sha256(pdf)
    source_size = pdf.stat().st_size
    rag._write_artifact_completion(
        rag._artifact_completion_path(paths["doc"], stage="conversion"),
        stage="conversion",
        source_sha256=source_sha256,
        source_name=pdf.name,
        source_record_count=None,
        parameters=parameters,
        outputs={
            "docling_json": paths["doc"],
            "docling_markdown": paths["converted_markdown"],
        },
        schema_version=rag.CONVERSION_COMPLETION_SCHEMA_VERSION,
        extra_fields={
            "source": {
                "name": pdf.name,
                "size": source_size,
                "sha256": source_sha256,
                "capture_policy": "stream-copy-v1",
            },
            "effective_input": {
                "kind": "original",
                "name": pdf.name,
                "size": source_size,
                "sha256": source_sha256,
            },
        },
    )
    return parameters


def _resume_record(rag) -> dict:
    record = {
        "text": "A generated discussion of synthetic procedure.",
        "metadata": {
            "chunk_index": 0,
            "source_file": "phase-a0.json",
            "source_lineage_schema_version": 1,
            "source_items": [{
                "ref": "#/texts/0",
                "label": "text",
                "parent_refs": [],
                "spans": [{"page": 1}],
            }],
            "page_start": 1,
            "page_end": 1,
            "page_range": "pp.1-1",
            "chapter_num": 1,
            "chapter_title": "Generated",
            "section_path": "Chapter 1",
            "content_type": "author_narrative",
            "content_source": "body",
            "token_count": 7,
            "embedding_token_count": 7,
            "case_names": [],
            "primary_case": None,
        },
    }
    rag._retrieval_core._attach_retrieval_linkage([record])
    return record


def _write_resume_chunk_fixture(
        rag, *, pdf: Path, paths: Mapping[str, Path], args: SimpleNamespace,
) -> dict:
    llm_kwargs = rag._llm_kwargs_from_args(
        args, include_workers=True, resolve_credentials=False)
    llm_kwargs.setdefault(
        "security_policy", rag._effective_security_policy(None))
    profile = rag._document_profiles.get_profile(args.structure_profile)
    parameters = rag._chunk_parameters(
        embedding_model=args.embedding_model,
        max_tokens=args.max_tokens,
        min_words=args.min_words,
        dedup_threshold=args.dedup_threshold,
        watermark=None,
        llm_classify=False,
        zeroshot_classify=False,
        contextualize=False,
        reconstruct_headings=False,
        quality_score=False,
        llm_scaffold=False,
        table_children=False,
        structure_profile=profile,
        **llm_kwargs,
    )
    rag._atomic_write_jsonl(paths["chunks"], [_resume_record(rag)])
    document_raw = paths["doc"].read_bytes()
    document_sha256 = _sha256_bytes(document_raw)
    conversion = rag._load_conversion_source_binding(
        paths["doc"],
        document_sha256=document_sha256,
        document_size=len(document_raw),
    )
    if conversion is None or not conversion.capture_verified:
        raise PhaseA0BenchmarkError(
            "generated conversion binding did not verify")
    conversion_input = {
        "name": conversion.manifest_path.name,
        "sha256": conversion.manifest_sha256,
        "schema_version": conversion.schema_version,
    }
    source_sha256 = rag._cached_artifact_sha256(pdf)
    inputs = {
        "docling_json": {
            "name": paths["doc"].name,
            "size": len(document_raw),
            "sha256": document_sha256,
        },
        "conversion_manifest": conversion_input,
        "table_recovery": {
            "pdf": {
                "name": pdf.name,
                "size": pdf.stat().st_size,
                "sha256": source_sha256,
                "capture_policy": "stream-copy-v1",
            },
            "conversion_manifest": conversion_input,
            "discovery": "explicit",
        },
    }
    receipt = parameters["structure_profile"]
    rag._write_artifact_completion(
        rag._artifact_completion_path(paths["chunks"], stage="chunking"),
        stage="chunking",
        source_sha256=document_sha256,
        source_record_count=None,
        parameters=parameters,
        outputs={"chunks_jsonl": paths["chunks"]},
        schema_version=rag.CHUNK_COMPLETION_SCHEMA_VERSION,
        extra_fields={
            "inputs": inputs,
            "structure_profile": receipt,
            "structure_profile_parameters_sha256": (
                rag._structure_profile_parameters_binding(
                    rag._artifact_parameters_sha256(parameters), receipt)),
        },
    )
    rag._publish_corpus_quality_report(
        paths["doc"],
        paths["chunks"],
        parameters=parameters,
        structural_ranges=set(),
    )
    rag.export_markdown(
        paths["chunks"],
        paths["export"],
        security_policy=llm_kwargs["security_policy"],
    )
    return parameters


def _resume_artifact_identity(rag, paths: Mapping[str, Path]) -> str:
    artifacts = (
        paths["doc"],
        paths["converted_markdown"],
        rag._artifact_completion_path(paths["doc"], stage="conversion"),
        paths["chunks"],
        rag._artifact_completion_path(paths["chunks"], stage="chunking"),
        rag._quality_core.quality_report_path(paths["chunks"]),
        paths["export"],
        rag._artifact_completion_path(paths["export"], stage="unified_export"),
    )
    identities = [
        _file_identity(path) for path in sorted(artifacts, key=lambda p: p.name)
    ]
    return _sha256_bytes(_canonical_bytes(identities))


def _probe_noop_resume() -> tuple[dict[str, object], dict[str, object]]:
    """Exercise completed-artifact resume without parsing, models, or vectors.

    Production completion validators, collection locking, skip decisions, and
    the mandatory index-revalidation call are exercised.  Physical vector
    indexing is deliberately replaced by a dependency-free ``IndexOutcome``;
    PDF conversion, chunk generation, partial repair, and model loading are not
    part of this no-op fixture.
    """
    rag = importlib.import_module("rag")
    operation_contracts = importlib.import_module("operation_contracts")
    fixture_root = Path.cwd() / "generated-resume-fixture"
    fixture_root.mkdir(mode=0o700)
    pdf = fixture_root / "phase-a0.pdf"
    pdf.write_bytes(b"%PDF-1.4\n% generated phase a0 fixture\n%%EOF\n")
    args = _resume_probe_args(rag)
    original_output_dir = rag.OUTPUT_DIR
    originals = None
    try:
        rag.OUTPUT_DIR = fixture_root / "output"
        paths = rag._output_paths_for_name("PhaseA0")
        paths["doc"].parent.mkdir(parents=True)
        conversion_parameters = _write_resume_conversion_fixture(
            rag, pdf=pdf, paths=paths, args=args)
        chunk_parameters = _write_resume_chunk_fixture(
            rag, pdf=pdf, paths=paths, args=args)
        export_parameters = rag._markdown_export_parameters(
            include_types=None,
            exclude_types=None,
            chapters=None,
            split_chapters=False,
        )
        if not (
            rag._converted_outputs_complete(
                pdf,
                paths["doc"],
                paths["converted_markdown"],
                parameters=conversion_parameters,
                preprocessed_output=paths["preprocessed"],
            )
            and rag._chunks_complete(
                paths["doc"],
                paths["chunks"],
                parameters=chunk_parameters,
                source_pdf_path=pdf,
            )
            and rag._quality_report_complete(
                paths["doc"],
                paths["chunks"],
                parameters=chunk_parameters,
            )
            and rag._unified_export_complete(
                paths["chunks"],
                paths["export"],
                parameters=export_parameters,
            )
        ):
            raise PhaseA0BenchmarkError(
                "generated resume fixture did not verify")

        physical_calls = {
            "convert": 0,
            "chunk": 0,
            "quality": 0,
            "export": 0,
        }
        originals = {
            "convert_pdf": rag.convert_pdf,
            "chunk_document": rag.chunk_document,
            "_publish_corpus_quality_report": (
                rag._publish_corpus_quality_report),
            "export_markdown": rag.export_markdown,
            "_converted_outputs_complete": rag._converted_outputs_complete,
            "_chunks_complete": rag._chunks_complete,
            "_quality_report_complete": rag._quality_report_complete,
            "_unified_export_complete": rag._unified_export_complete,
            "_vector_store_lock": rag._vector_store_lock,
            "_index_chunks_for_backend": rag._index_chunks_for_backend,
        }
        expected_policy = rag._effective_security_policy(None)

        def relative(value: Path) -> str:
            return Path(value).relative_to(fixture_root).as_posix()

        def identity_sha256(value: Mapping[str, object]) -> str:
            return _sha256_bytes(_canonical_bytes(value))

        expected_validator_arguments = {
            "conversion": {
                "pdf": relative(pdf),
                "doc": relative(paths["doc"]),
                "markdown": relative(paths["converted_markdown"]),
                "preprocessed": relative(paths["preprocessed"]),
                "parameters_sha256": identity_sha256(conversion_parameters),
            },
            "chunks": {
                "doc": relative(paths["doc"]),
                "chunks": relative(paths["chunks"]),
                "source_pdf": relative(pdf),
                "parameters_sha256": identity_sha256(chunk_parameters),
            },
            "quality": {
                "doc": relative(paths["doc"]),
                "chunks": relative(paths["chunks"]),
                "parameters_sha256": identity_sha256(chunk_parameters),
            },
            "export": {
                "chunks": relative(paths["chunks"]),
                "export": relative(paths["export"]),
                "parameters_sha256": identity_sha256(export_parameters),
            },
        }
        validator_observations = {
            name: [] for name in expected_validator_arguments}

        def observe_validator(
                name: str, identity: Mapping[str, object],
                delegate: Callable[[], bool]) -> bool:
            if identity != expected_validator_arguments[name]:
                raise PhaseA0BenchmarkError(
                    "resume completion validator wiring changed")
            result = delegate()
            validator_observations[name].append({
                "arguments_sha256": identity_sha256(identity),
                "result": result,
            })
            return result

        def observe_conversion(
                pdf_path, doc_path, markdown_path, *, parameters,
                preprocessed_output=None):
            identity = {
                "pdf": relative(pdf_path),
                "doc": relative(doc_path),
                "markdown": relative(markdown_path),
                "preprocessed": relative(preprocessed_output),
                "parameters_sha256": identity_sha256(parameters),
            }
            return observe_validator(
                "conversion", identity,
                lambda: originals["_converted_outputs_complete"](
                    pdf_path, doc_path, markdown_path,
                    parameters=parameters,
                    preprocessed_output=preprocessed_output))

        def observe_chunks(
                doc_path, chunks_path, *, parameters,
                source_pdf_path=None):
            identity = {
                "doc": relative(doc_path),
                "chunks": relative(chunks_path),
                "source_pdf": relative(source_pdf_path),
                "parameters_sha256": identity_sha256(parameters),
            }
            return observe_validator(
                "chunks", identity,
                lambda: originals["_chunks_complete"](
                    doc_path, chunks_path, parameters=parameters,
                    source_pdf_path=source_pdf_path))

        def observe_quality(doc_path, chunks_path, *, parameters):
            identity = {
                "doc": relative(doc_path),
                "chunks": relative(chunks_path),
                "parameters_sha256": identity_sha256(parameters),
            }
            return observe_validator(
                "quality", identity,
                lambda: originals["_quality_report_complete"](
                    doc_path, chunks_path, parameters=parameters))

        def observe_export(chunks_path, export_path, *, parameters):
            identity = {
                "chunks": relative(chunks_path),
                "export": relative(export_path),
                "parameters_sha256": identity_sha256(parameters),
            }
            return observe_validator(
                "export", identity,
                lambda: originals["_unified_export_complete"](
                    chunks_path, export_path, parameters=parameters))

        expected_lock_identity = {
            "db_dir": relative(paths["chroma"]),
            "backend": "chroma",
            "collection_name": paths["collection"],
            "operation": "pipeline chunk/index transition",
            "timeout": 1.0,
        }
        lock_observation = {
            "calls": 0,
            "entered": 0,
            "exited": 0,
            "clean_exit": False,
            "active": False,
            "arguments_sha256": "",
            "reacquired_after_exit": False,
        }

        def observe_lock(
                db_dir, *, backend, collection_name, operation, timeout):
            identity = {
                "db_dir": relative(db_dir),
                "backend": backend,
                "collection_name": collection_name,
                "operation": operation,
                "timeout": timeout,
            }
            if identity != expected_lock_identity:
                raise PhaseA0BenchmarkError("resume vector-lock wiring changed")
            lock_observation["calls"] += 1
            lock_observation["arguments_sha256"] = identity_sha256(identity)
            delegate = originals["_vector_store_lock"](
                db_dir, backend=backend, collection_name=collection_name,
                operation=operation, timeout=timeout)

            @contextlib.contextmanager
            def tracked_lock():
                with delegate as lease:
                    lock_observation["entered"] += 1
                    lock_observation["active"] = True
                    try:
                        yield lease
                    except BaseException:
                        lock_observation["clean_exit"] = False
                        raise
                    else:
                        lock_observation["clean_exit"] = True
                    finally:
                        lock_observation["active"] = False
                        lock_observation["exited"] += 1

            return tracked_lock()

        expected_outcome = operation_contracts.IndexOutcome(
            backend="chroma",
            disposition="unchanged",
            total_records=1,
            changed_records=0,
            unchanged_records=1,
            removed_records=0,
            upserted_records=0,
            batch_count=0,
            physical_count=1,
            committed=True,
        )
        index_observations = []

        def validate_index(*call_args, **call_kwargs):
            expected_keys = {
                "db_backend", "collection_name", "embedding_model",
                "full_reindex", "security_policy", "lock_timeout",
                "_active_update_token", "_operation_observer"}
            observer = call_kwargs.get("_operation_observer")
            valid_observer = (
                callable(observer)
                and getattr(observer, "__name__", None) == "update"
                and isinstance(getattr(observer, "__self__", None), dict))
            if (call_args != (paths["chunks"], paths["chroma"])
                    or set(call_kwargs) != expected_keys
                    or call_kwargs["db_backend"] != "chroma"
                    or call_kwargs["collection_name"] != paths["collection"]
                    or call_kwargs["embedding_model"] != args.embedding_model
                    or call_kwargs["full_reindex"] is not False
                    or call_kwargs["security_policy"] is not expected_policy
                    or call_kwargs["lock_timeout"] != 1.0
                    or call_kwargs["_active_update_token"] is not None
                    or not valid_observer
                    or lock_observation["active"] is not True
                    or index_observations):
                raise PhaseA0BenchmarkError(
                    "resume index revalidation wiring changed")
            identity = {
                "chunks": relative(call_args[0]),
                "db_dir": relative(call_args[1]),
                "db_backend": call_kwargs["db_backend"],
                "collection_name": call_kwargs["collection_name"],
                "embedding_model": call_kwargs["embedding_model"],
                "full_reindex": call_kwargs["full_reindex"],
                "security_policy": "release-local-only-cache-only",
                "lock_timeout": call_kwargs["lock_timeout"],
                "active_update_token": call_kwargs["_active_update_token"],
                "operation_observer_wired": valid_observer,
            }
            index_observations.append({
                "arguments_sha256": identity_sha256(identity),
                "under_active_lock": lock_observation["active"],
            })
            return expected_outcome

        def forbidden_stage(stage: str):
            def fail(*_args, **_kwargs):
                physical_calls[stage] += 1
                raise PhaseA0BenchmarkError(
                    "completed resume unexpectedly executed a physical stage")
            return fail

        rag.convert_pdf = forbidden_stage("convert")
        rag.chunk_document = forbidden_stage("chunk")
        rag._publish_corpus_quality_report = forbidden_stage("quality")
        rag.export_markdown = forbidden_stage("export")
        rag._converted_outputs_complete = observe_conversion
        rag._chunks_complete = observe_chunks
        rag._quality_report_complete = observe_quality
        rag._unified_export_complete = observe_export
        rag._vector_store_lock = observe_lock
        rag._index_chunks_for_backend = validate_index
        result = rag._run_pipeline_stages(
            pdf, paths, args, resume=True, watermark=None)
        expected_counts = {
            name: 1 for name in expected_validator_arguments}
        observed_counts = {
            name: len(records)
            for name, records in validator_observations.items()}
        if (observed_counts != expected_counts
                or any(records[0]["result"] is not True
                       for records in validator_observations.values())
                or lock_observation["calls"] != 1
                or lock_observation["entered"] != 1
                or lock_observation["exited"] != 1
                or lock_observation["clean_exit"] is not True
                or lock_observation["active"] is not False
                or len(index_observations) != 1
                or set(result) != {
                    "paths", "collection", "db_dir", "db_backend",
                    "index_outcome"}
                or result["paths"] is not paths
                or result["collection"] != paths["collection"]
                or result["db_dir"] != paths["chroma"]
                or result["db_backend"] != "chroma"
                or result["index_outcome"] is not expected_outcome):
            raise PhaseA0BenchmarkError(
                "resume pipeline observations were incomplete")
        with originals["_vector_store_lock"](
                paths["chroma"], backend="chroma",
                collection_name=paths["collection"],
                operation="pipeline chunk/index transition", timeout=1.0):
            lock_observation["reacquired_after_exit"] = True
        outcome_fields = {
            name: getattr(expected_outcome, name)
            for name in (
                "backend", "disposition", "total_records",
                "changed_records", "unchanged_records", "removed_records",
                "upserted_records", "batch_count", "physical_count",
                "committed")
        }
        contract = {
            "record_count": 1,
            "completion_validators": {
                name: records[0]
                for name, records in validator_observations.items()
            },
            "vector_lock": {
                name: lock_observation[name]
                for name in (
                    "calls", "entered", "exited", "clean_exit",
                    "arguments_sha256", "reacquired_after_exit")
            },
            "index_revalidation": {
                "calls": len(index_observations),
                **index_observations[0],
                "outcome": outcome_fields,
            },
            "returned_result": {
                "paths_match": result["paths"] is paths,
                "collection_match": result["collection"] == paths["collection"],
                "db_dir_match": result["db_dir"] == paths["chroma"],
                "db_backend_match": result["db_backend"] == "chroma",
                "outcome_match": result["index_outcome"] is expected_outcome,
            },
            "skipped_physical_calls": {
                name: physical_calls[name]
                for name in ("convert", "chunk", "quality", "export")
            },
            "artifact_set_sha256": _resume_artifact_identity(rag, paths),
            "physical_indexing_exercised": False,
            "partial_repair_exercised": False,
        }
        return contract, _loaded_module_diagnostics()
    finally:
        if originals is not None:
            for name, value in originals.items():
                setattr(rag, name, value)
        rag.OUTPUT_DIR = original_output_dir


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
    "worker_entrypoints": _probe_worker_entrypoints,
    "isolation_guard": _probe_isolation_guard,
    "noop_resume": _probe_noop_resume,
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


def _valid_module_roots(value: object) -> bool:
    return (
        isinstance(value, list)
        and value == sorted(value)
        and len(value) == len(set(value))
        and all(
            isinstance(name, str)
            and re.fullmatch(r"[A-Za-z0-9_]+", name) is not None
            for name in value)
    )


def _validate_probe_diagnostics(value: object) -> None:
    if not isinstance(value, dict):
        raise PhaseA0BenchmarkError("probe diagnostics were invalid")
    required = {
        "loaded_module_count", "first_party_loaded_count",
        "first_party_loaded_roots", "first_party_loaded_sha256",
        "third_party_loaded_count", "third_party_loaded_roots",
        "third_party_loaded_sha256", "stdlib_loaded_count",
        "stdlib_loaded_roots", "stdlib_loaded_sha256", "peak_rss_bytes"}
    if frozenset(value) not in {
            frozenset(required), frozenset(required | {"output"})}:
        raise PhaseA0BenchmarkError("probe diagnostic fields were invalid")
    if (not _valid_count(value["loaded_module_count"], positive=True)
            or not _valid_count(value["first_party_loaded_count"])
            or not _valid_module_roots(value["first_party_loaded_roots"])
            or value["first_party_loaded_count"] != len(
                value["first_party_loaded_roots"])
            or _SHA256_RE.fullmatch(
                str(value["first_party_loaded_sha256"])) is None
            or value["first_party_loaded_sha256"] != _sha256_bytes(
                "\n".join(value["first_party_loaded_roots"]).encode("utf-8"))
            or not _valid_count(value["third_party_loaded_count"])
            or not _valid_module_roots(value["third_party_loaded_roots"])
            or value["third_party_loaded_count"] != len(
                value["third_party_loaded_roots"])
            or value["third_party_loaded_sha256"] != _sha256_bytes(
                "\n".join(value["third_party_loaded_roots"]).encode("utf-8"))
            or not _valid_count(value["stdlib_loaded_count"])
            or not _valid_module_roots(value["stdlib_loaded_roots"])
            or value["stdlib_loaded_count"] != len(
                value["stdlib_loaded_roots"])
            or value["stdlib_loaded_sha256"] != _sha256_bytes(
                "\n".join(value["stdlib_loaded_roots"]).encode("utf-8"))
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
    elif name == "cli_help":
        expected_markers = {"usage:", "info", "preprocess"}
        markers = value.get("required_markers")
        valid = (
            set(value) == {
                "entrypoint", "execution_mode", "exit_code",
                "stdout_present", "stderr_empty", "required_markers"}
            and value["entrypoint"] == "rag.py"
            and value["execution_mode"] == "supervised_child"
            and value["exit_code"] == 0
            and value["stdout_present"] is True
            and value["stderr_empty"] is True
            and isinstance(markers, dict)
            and set(markers) == expected_markers
            and all(item is True for item in markers.values())
        )
    elif name == "cli_info_empty":
        markers = value.get("required_markers")
        valid = (
            set(value) == {
                "entrypoint", "execution_mode", "exit_code",
                "stdout_present", "stderr_empty", "required_markers",
                "supervision"}
            and value["entrypoint"] == "rag.py"
            and value["execution_mode"] == "outer_supervised_process"
            and value["exit_code"] == 0
            and value["stdout_present"] is True
            and value["stderr_empty"] is True
            and markers == {
                "Pipeline Output Status": True,
                "No output directory found": True}
            and value["supervision"] == {
                "outer_processes": 1,
                "supervised_children": 1,
                "parent_child_link": True,
                "guarded_processes": 2,
                "cleanup_confirmed": True,
            }
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
    elif name == "worker_entrypoints":
        valid = value == {
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
        }
    elif name == "isolation_guard":
        valid = value == {
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
        }
    elif name == "noop_resume":
        validators = value.get("completion_validators")
        vector_lock = value.get("vector_lock")
        validator_valid = (
            isinstance(validators, dict)
            and set(validators) == {"conversion", "chunks", "quality", "export"}
            and all(
                isinstance(item, dict)
                and set(item) == {"arguments_sha256", "result"}
                and _SHA256_RE.fullmatch(
                    str(item["arguments_sha256"])) is not None
                and item["result"] is True
                for item in validators.values()))
        valid = (
            set(value) == {
                "record_count", "completion_validators", "vector_lock",
                "index_revalidation", "returned_result",
                "skipped_physical_calls",
                "artifact_set_sha256", "physical_indexing_exercised",
                "partial_repair_exercised"}
            and value["record_count"] == 1
            and validator_valid
            and isinstance(vector_lock, dict)
            and set(vector_lock) == {
                "calls", "entered", "exited", "clean_exit",
                "arguments_sha256", "reacquired_after_exit"}
            and vector_lock["calls"] == 1
            and vector_lock["entered"] == 1
            and vector_lock["exited"] == 1
            and vector_lock["clean_exit"] is True
            and vector_lock["reacquired_after_exit"] is True
            and _SHA256_RE.fullmatch(str(
                vector_lock["arguments_sha256"])) is not None
            and isinstance(value["index_revalidation"], dict)
            and set(value["index_revalidation"]) == {
                "calls", "arguments_sha256", "under_active_lock", "outcome"}
            and value["index_revalidation"]["calls"] == 1
            and _SHA256_RE.fullmatch(str(
                value["index_revalidation"]["arguments_sha256"])) is not None
            and value["index_revalidation"]["under_active_lock"] is True
            and value["index_revalidation"]["outcome"] == {
                "backend": "chroma", "disposition": "unchanged",
                "total_records": 1, "changed_records": 0,
                "unchanged_records": 1, "removed_records": 0,
                "upserted_records": 0, "batch_count": 0,
                "physical_count": 1, "committed": True}
            and value["returned_result"] == {
                "paths_match": True, "collection_match": True,
                "db_dir_match": True, "db_backend_match": True,
                "outcome_match": True}
            and value["skipped_physical_calls"] == {
                "convert": 0, "chunk": 0, "quality": 0, "export": 0}
            and _SHA256_RE.fullmatch(
                str(value["artifact_set_sha256"])) is not None
            and value["physical_indexing_exercised"] is False
            and value["partial_repair_exercised"] is False
        )
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
        "first_party_loaded_count", "first_party_loaded_roots",
        "first_party_loaded_sha256", "third_party_loaded_count",
        "third_party_loaded_roots", "third_party_loaded_sha256",
        "stdlib_loaded_count", "stdlib_loaded_roots",
        "stdlib_loaded_sha256", "output"}
    if not isinstance(value, dict) or set(value) != expected:
        raise PhaseA0BenchmarkError("report diagnostic fields were invalid")
    _validate_numeric_summary(value["wall_time_ms"], integer=False)
    _validate_numeric_summary(value["loaded_module_count"], integer=True)
    _validate_numeric_summary(value["first_party_loaded_count"], integer=True)
    _validate_numeric_summary(value["third_party_loaded_count"], integer=True)
    _validate_numeric_summary(value["stdlib_loaded_count"], integer=True)
    for category in ("first_party", "third_party", "stdlib"):
        roots = value[f"{category}_loaded_roots"]
        digest = value[f"{category}_loaded_sha256"]
        summary = value[f"{category}_loaded_count"]
        if (not _valid_module_roots(roots)
                or summary != {
                    "minimum": len(roots), "median": len(roots),
                    "p95": len(roots), "maximum": len(roots)}
                or digest != _sha256_bytes(
                    "\n".join(roots).encode("utf-8"))):
            raise PhaseA0BenchmarkError("report import identity was invalid")
    if any(_SHA256_RE.fullmatch(
            str(value[f"{category}_loaded_sha256"])) is None
            for category in ("first_party", "third_party", "stdlib")):
        raise PhaseA0BenchmarkError("report import identity was invalid")
    peak = value["peak_rss_bytes"]
    if not isinstance(peak, dict) or not isinstance(peak.get("available"), bool):
        raise PhaseA0BenchmarkError("report peak RSS evidence was invalid")
    if peak["available"]:
        if set(peak) != {
                "available", "scope", "minimum", "median", "p95", "maximum"}:
            raise PhaseA0BenchmarkError("report peak RSS fields were invalid")
        if peak["scope"] != _RSS_SCOPE:
            raise PhaseA0BenchmarkError("report peak RSS scope was invalid")
        _validate_numeric_summary(
            {key: peak[key] for key in (
                "minimum", "median", "p95", "maximum")}, integer=True)
    elif set(peak) != {"available", "scope"} or peak["scope"] != _RSS_SCOPE:
        raise PhaseA0BenchmarkError("report unavailable RSS fields were invalid")
    if value["output"] is not None:
        _validate_output_pair(value["output"])


def _probe_main(name: str) -> int:
    probe = _PROBES.get(name)
    if probe is None:
        print(json.dumps({"ok": False, "error_type": "UnknownScenario"}))
        return 2
    try:
        original_home = Path.__dict__["home"]

        def reject_home_fallback(_path_type) -> Path:
            raise PhaseA0BenchmarkError(
                "probe attempted to resolve an operator home directory")

        Path.home = classmethod(reject_home_fallback)  # type: ignore[method-assign]
        try:
            contract, diagnostics = probe()
        finally:
            Path.home = original_home  # type: ignore[method-assign]
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


def safe_child_environment(
        temporary_root: Path, *, sitecustomize_root: Path | None = None,
) -> dict[str, str]:
    """Build a minimal environment without provider credentials or proxies."""
    environment = {
        name: os.environ[name]
        for name in _SAFE_CHILD_ENVIRONMENT
        if os.environ.get(name)
    }
    environment.update({
        "LANG": "C.UTF-8",
        "LC_ALL": "C.UTF-8",
        "NO_COLOR": "1",
        "PYTHONDONTWRITEBYTECODE": "1",
        "PYTHONHASHSEED": "0",
        "PYTHONIOENCODING": "utf-8",
        "PYTHONNOUSERSITE": "1",
        "PYTHONPATH": os.pathsep.join(
            [str(sitecustomize_root), str(PROJECT_ROOT)]
            if sitecustomize_root is not None else [str(PROJECT_ROOT)]),
        "PYTHONUTF8": "1",
        "RAG_LLM_CACHE_DIR": str(temporary_root / "llm-cache"),
        "RAG_MODEL_ARTIFACT_CACHE": str(
            temporary_root / "model-artifacts"),
        "RAG_PIPELINE_OUTPUT_ROOT": str(temporary_root / "output"),
        "TEMP": str(temporary_root),
        "TMP": str(temporary_root),
        "TMPDIR": str(temporary_root),
        "TZ": "UTC",
        "XDG_CACHE_HOME": str(temporary_root / "xdg-cache"),
        "XDG_CONFIG_HOME": str(temporary_root / "xdg-config"),
        "XDG_DATA_HOME": str(temporary_root / "xdg-data"),
        "XDG_STATE_HOME": str(temporary_root / "xdg-state"),
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
    payload = _strict_json_bytes(raw, description="probe response")
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
        guard_root = _install_sitecustomize_guard(temporary_root)
        trace_path = temporary_root / "probe-guard-trace.jsonl"
        environment = _guarded_child_environment(
            temporary_root, guard_root,
            trace_path=trace_path, role="benchmark-probe")
        started = time.perf_counter_ns()
        try:
            result = _run_contained_process(
                Path(__file__).resolve(),
                ("--_probe", scenario),
                cwd=temporary_root,
                environment=environment,
                timeout_seconds=float(timeout_seconds),
            )
        except PhaseA0BenchmarkError:
            raise PhaseA0BenchmarkError(
                "probe failed with contained cleanup") from None
        wall_time_ms = (time.perf_counter_ns() - started) / 1_000_000
        if result.returncode == 124:
            raise PhaseA0BenchmarkError("probe exceeded its deadline")
        if result.returncode != 0 or result.stderr:
            raise PhaseA0BenchmarkError(
                "probe failed without publishing evidence")
        events = _read_guard_trace(trace_path)
        if (len(events) != 1 or events[0]["role"] != "benchmark-probe"
                or events[0]["production_supervised"] is not False):
            raise PhaseA0BenchmarkError(
                "probe isolation guard attestation failed")
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
    import_evidence = {}
    for category in ("first_party", "third_party", "stdlib"):
        counts = [
            value.get(f"{category}_loaded_count") for value in diagnostics]
        roots = [
            value.get(f"{category}_loaded_roots") for value in diagnostics]
        digests = [
            value.get(f"{category}_loaded_sha256") for value in diagnostics]
        if (any(not _valid_count(value) for value in counts)
                or any(value != counts[0] for value in counts[1:])
                or any(not _valid_module_roots(value) for value in roots)
                or any(value != roots[0] for value in roots[1:])
                or any(_SHA256_RE.fullmatch(str(value)) is None
                       for value in digests)
                or any(value != digests[0] for value in digests[1:])
                or counts[0] != len(roots[0])
                or digests[0] != _sha256_bytes(
                    "\n".join(roots[0]).encode("utf-8"))):
            raise PhaseA0BenchmarkError(
                f"{category.replace('_', '-')} import identity changed "
                "between repetitions")
        import_evidence[category] = {
            "count": _numeric_summary(counts),
            "roots": roots[0],
            "sha256": digests[0],
        }
    outputs = [value.get("output") for value in diagnostics]
    output = None
    if any(value is not None for value in outputs):
        if any(value != outputs[0] for value in outputs[1:]):
            raise PhaseA0BenchmarkError("scenario output changed between repetitions")
        output = outputs[0]

    rss_values = [value.get("peak_rss_bytes") for value in diagnostics]
    if all(value is None for value in rss_values):
        rss_summary: dict[str, object] = {
            "available": False, "scope": _RSS_SCOPE}
    elif any(value is None for value in rss_values):
        raise PhaseA0BenchmarkError("peak RSS availability changed between runs")
    else:
        rss_summary = {
            "available": True,
            "scope": _RSS_SCOPE,
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
            "first_party_loaded_roots": import_evidence["first_party"]["roots"],
            "first_party_loaded_sha256": import_evidence[
                "first_party"]["sha256"],
            "third_party_loaded_count": import_evidence["third_party"]["count"],
            "third_party_loaded_roots": import_evidence["third_party"]["roots"],
            "third_party_loaded_sha256": import_evidence[
                "third_party"]["sha256"],
            "stdlib_loaded_count": import_evidence["stdlib"]["count"],
            "stdlib_loaded_roots": import_evidence["stdlib"]["roots"],
            "stdlib_loaded_sha256": import_evidence["stdlib"]["sha256"],
            "output": output,
        },
    }
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
    source_before = repository_identity(root)
    inputs_before = dependency_identities(root)
    scenario_reports = []
    for name in selected:
        runs = [
            run_probe(name, timeout_seconds=timeout_seconds)
            for _ in range(repetitions)
        ]
        scenario_reports.append(summarize_runs(name, runs))
    source_after = repository_identity(root)
    inputs_after = dependency_identities(root)
    if source_after != source_before or inputs_after != inputs_before:
        raise PhaseA0BenchmarkError(
            "repository source or lock inputs changed during benchmark")
    report: dict[str, Any] = {
        "schema_version": REPORT_SCHEMA_VERSION,
        "benchmark": BENCHMARK_NAME,
        "profile": execution_profile(),
        "scenario_set": _scenario_set_identity(selected),
        "source": source_before,
        "runtime": runtime_identity(),
        "system": machine_identity(),
        "inputs": inputs_before,
        "repetitions": repetitions,
        "scenarios": scenario_reports,
    }
    report["report_sha256"] = _sha256_bytes(_canonical_bytes(report))
    validate_report(report)
    return report


def validate_report(report: object) -> dict[str, Any]:
    """Strictly validate a report and its self-digest."""
    if not isinstance(report, dict) or set(report) != {
        "schema_version", "benchmark", "profile", "scenario_set", "source",
            "runtime", "system", "inputs", "repetitions", "scenarios",
            "report_sha256"}:
        raise PhaseA0BenchmarkError("report fields were invalid")
    if (report["schema_version"] != REPORT_SCHEMA_VERSION
            or report["benchmark"] != BENCHMARK_NAME):
        raise PhaseA0BenchmarkError("report identity was invalid")
    profile = report["profile"]
    if (not isinstance(profile, dict)
            or set(profile) != {
                "implementation", "python_major_minor", "pointer_bits",
                "canonical"}
            or not isinstance(profile["implementation"], str)
            or not profile["implementation"]
            or re.fullmatch(
                r"[1-9][0-9]*\.[0-9]+",
                str(profile["python_major_minor"])) is None
            or profile["pointer_bits"] not in {32, 64}
            or not isinstance(profile["canonical"], bool)
            or profile["canonical"] != (
                profile["implementation"] == CANONICAL_IMPLEMENTATION
                and profile["python_major_minor"]
                == CANONICAL_PYTHON_MAJOR_MINOR
                and profile["pointer_bits"] == CANONICAL_POINTER_BITS)):
        raise PhaseA0BenchmarkError("report execution profile was invalid")
    scenario_set = report["scenario_set"]
    if (not isinstance(scenario_set, dict)
            or set(scenario_set) != {"names", "sha256", "authoritative"}
            or not isinstance(scenario_set["names"], list)
            or not scenario_set["names"]
            or scenario_set != _scenario_set_identity(scenario_set["names"])):
        raise PhaseA0BenchmarkError("report scenario-set identity was invalid")
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
            or runtime["pointer_bits"] not in {32, 64}
            or runtime["implementation"] != profile["implementation"]
            or ".".join(runtime["python_version"].split(".")[:2])
            != profile["python_major_minor"]
            or runtime["pointer_bits"] != profile["pointer_bits"]):
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
    if scenario_set != _scenario_set_identity(names):
        raise PhaseA0BenchmarkError("report scenario-set contents did not match")
    if _SHA256_RE.fullmatch(str(report["report_sha256"])) is None:
        raise PhaseA0BenchmarkError("report digest was invalid")
    expected_digest = _sha256_bytes(_canonical_bytes(_report_without_digest(report)))
    if report["report_sha256"] != expected_digest:
        raise PhaseA0BenchmarkError("report digest did not match its contents")
    return report


def compare_contracts(report: object, baseline: object) -> None:
    """Compare portable deterministic evidence and exact lock inputs."""
    current = validate_report(report)
    expected = validate_report(baseline)
    authoritative_set = _scenario_set_identity(SCENARIOS)
    canonical_profile = {
        "implementation": CANONICAL_IMPLEMENTATION,
        "python_major_minor": CANONICAL_PYTHON_MAJOR_MINOR,
        "pointer_bits": CANONICAL_POINTER_BITS,
        "canonical": True,
    }
    if (current["scenario_set"] != authoritative_set
            or expected["scenario_set"] != authoritative_set):
        raise PhaseA0BenchmarkError(
            "Phase A0 comparison requires the authoritative scenario set")
    if (current["profile"] != canonical_profile
            or expected["profile"] != canonical_profile):
        raise PhaseA0BenchmarkError(
            "Phase A0 comparison requires the canonical CPython 3.12 profile")
    if current["inputs"] != expected["inputs"]:
        raise PhaseA0BenchmarkError("benchmark lock identities changed")

    def portable_evidence(validated: Mapping[str, Any]) -> dict[str, object]:
        evidence = {}
        for item in validated["scenarios"]:
            diagnostics = item["diagnostics"]
            evidence[item["name"]] = {
                "contract": item["contract"],
                "first_party_loaded_count": diagnostics[
                    "first_party_loaded_count"],
                "first_party_loaded_roots": diagnostics[
                    "first_party_loaded_roots"],
                "first_party_loaded_sha256": diagnostics[
                    "first_party_loaded_sha256"],
                "third_party_loaded_count": diagnostics[
                    "third_party_loaded_count"],
                "third_party_loaded_roots": diagnostics[
                    "third_party_loaded_roots"],
                "third_party_loaded_sha256": diagnostics[
                    "third_party_loaded_sha256"],
                "output": diagnostics["output"],
            }
        return evidence

    if portable_evidence(current) != portable_evidence(expected):
        raise PhaseA0BenchmarkError(
            "Phase A0 portable deterministic evidence changed")


def _read_report(path: Path) -> dict[str, Any]:
    raw = _read_bounded_file(
        path, max_bytes=10_000_000, description="baseline report")
    payload = _strict_json_bytes(raw, description="baseline report")
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
        help=(
            "Refuse to publish evidence with staged, unstaged, or untracked "
            "worktree changes"))
    return parser


def main(argv: Sequence[str] | None = None) -> int:
    arguments = list(sys.argv[1:] if argv is None else argv)
    if len(arguments) == 2 and arguments[0] == "--_probe":
        return _probe_main(arguments[1])
    args = _parser().parse_args(arguments)
    try:
        selected = tuple(args.scenario) if args.scenario else SCENARIOS
        baseline = None
        if args.check is not None:
            if selected != SCENARIOS:
                raise PhaseA0BenchmarkError(
                    "baseline comparison requires every Phase A0 scenario")
            if execution_profile()["canonical"] is not True:
                raise PhaseA0BenchmarkError(
                    "baseline comparison requires the canonical profile")
            if not repository_identity()["worktree_clean"]:
                raise PhaseA0BenchmarkError(
                    "baseline comparison requires no staged, unstaged, or "
                    "untracked changes")
            baseline = _read_report(args.check)
            if (baseline["profile"]["canonical"] is not True
                    or baseline["scenario_set"]
                    != _scenario_set_identity(SCENARIOS)):
                raise PhaseA0BenchmarkError(
                    "baseline was not authoritative canonical evidence")
        report = run_benchmark(
            scenarios=selected,
            repetitions=args.repetitions,
            timeout_seconds=args.timeout_seconds,
        )
        if ((args.require_clean or args.check is not None)
                and not report["source"]["worktree_clean"]):
            raise PhaseA0BenchmarkError(
                "worktree has staged, unstaged, or untracked changes")
        if baseline is not None:
            compare_contracts(report, baseline)
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
