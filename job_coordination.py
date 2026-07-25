"""Detached execution and conservative recovery for durable RAG jobs.

The manager is intentionally a thin adapter over :mod:`job_runtime` and a
frozen :mod:`runtime_supervision` binding.  It owns no pipeline logic: one
manager holds the job lease, publishes private attempt metadata, and maps the
supervised worker plus run telemetry into the durable job state machine.
"""

from __future__ import annotations

import argparse
import ctypes
import hashlib
import json
import math
import os
import signal
import stat
import subprocess
import sys
import time
from dataclasses import dataclass, field, replace
from pathlib import Path
from typing import Any, Callable, Sequence
from uuid import uuid4

import attempt_reporting
from job_coordination_contracts import (
    JobManagerCorruptError,
    JobManagerError,
    JobManagerLaunchError,
    JobManagerValidationError,
    LaunchResult,
    MANAGER_SCHEMA_VERSION,
    ManagerResult,
    ServiceJobCoordinationBinding,
)
import job_runtime
import run_telemetry
import runtime_supervision
import storage_policy


JOB_ROOT_ENV = job_runtime.JOB_ROOT_ENV
JOB_ID_ENV = job_runtime.JOB_ID_ENV
JOB_ATTEMPT_TOKEN_ENV = job_runtime.JOB_ATTEMPT_TOKEN_ENV
OUTPUT_ROOT_ENV = job_runtime.OUTPUT_ROOT_ENV
MANAGER_SCRIPT_PATH = Path(__file__).with_name("job_manager.py").resolve()

_READY_NONCE_ENV = "RAG_PIPELINE_MANAGER_READY_NONCE"
_ATTEMPTS_DIRECTORY = "attempts"
_RUNTIME_NAME = "runtime.json"
_READY_NAME = "ready.json"
_LOG_NAME = "worker.log"
_EVENTS_NAME = "run.events.jsonl"
_REPORT_NAME = "run.report.json"
_ATTEMPT_REPORT_NAME = "attempt.report.json"
_MAX_MANAGER_JSON_BYTES = 64 * 1024
_MAX_RUN_REPORT_BYTES = 8 * 1024 * 1024
_MAX_WORKER_LOG_BYTES = 8 * 1024 * 1024
_HEARTBEAT_INTERVAL = 1.0
_RECOVERY_TERMINATE_GRACE = 3.0
_GENERIC_BACKGROUND_TIMEOUT = 4 * 60 * 60.0
_TERMINAL_TELEMETRY = frozenset({
    "succeeded", "partial", "failed", "cancelled",
})
_RUNTIME_PHASES = frozenset({
    "starting", "running", "terminal", "reconciled",
})
_default_runtime_binding = runtime_supervision.default_runtime_binding


@dataclass(frozen=True, slots=True)
class _AttemptPaths:
    directory: Path = field(repr=False)
    runtime: Path = field(repr=False)
    ready: Path = field(repr=False)
    log: Path = field(repr=False)
    events: Path = field(repr=False)
    report: Path = field(repr=False)
    attempt_report: Path = field(repr=False)


@dataclass(frozen=True, slots=True)
class RuntimeMetadata:
    """Private attempt runtime record used only for recovery."""

    job_id: str
    attempt_number: int
    attempt_token_sha256: str = field(repr=False)
    run_id: str
    phase: str
    job_status: str
    manager_pid: int
    manager_birth: str | None
    worker_pid: int | None
    worker_birth: str | None
    heartbeat_at: float
    cleanup_confirmed: bool | None
    exit_code: int | None
    updated_at: float


RuntimeMetadata.__module__ = "job_manager"


def _token_digest(token: str) -> str:
    return hashlib.sha256(token.encode("ascii")).hexdigest()


def _attempt_paths(store: job_runtime.JobStore, job_id: str,
                   attempt_number: int, *, create: bool) -> _AttemptPaths:
    if (isinstance(attempt_number, bool) or not isinstance(attempt_number, int)
            or attempt_number < 1):
        raise JobManagerValidationError("attempt number is invalid")
    job_directory = store.root / job_id
    attempts = job_directory / _ATTEMPTS_DIRECTORY
    directory = attempts / f"{attempt_number:06d}"
    if create:
        storage_policy.ensure_private_directory(attempts)
        storage_policy.ensure_private_directory(directory)
    return _AttemptPaths(
        directory=directory,
        runtime=directory / _RUNTIME_NAME,
        ready=directory / _READY_NAME,
        log=directory / _LOG_NAME,
        events=directory / _EVENTS_NAME,
        report=directory / _REPORT_NAME,
        attempt_report=directory / _ATTEMPT_REPORT_NAME,
    )


def _runtime_payload(metadata: RuntimeMetadata) -> dict:
    return {
        "schema_version": MANAGER_SCHEMA_VERSION,
        "kind": "job_attempt_runtime",
        "job_id": metadata.job_id,
        "attempt_number": metadata.attempt_number,
        "attempt_token_sha256": metadata.attempt_token_sha256,
        "run_id": metadata.run_id,
        "phase": metadata.phase,
        "job_status": metadata.job_status,
        "manager_pid": metadata.manager_pid,
        "manager_birth": metadata.manager_birth,
        "worker_pid": metadata.worker_pid,
        "worker_birth": metadata.worker_birth,
        "heartbeat_at": metadata.heartbeat_at,
        "cleanup_confirmed": metadata.cleanup_confirmed,
        "exit_code": metadata.exit_code,
        "updated_at": metadata.updated_at,
    }


def _write_runtime(path: Path, metadata: RuntimeMetadata) -> None:
    storage_policy.atomic_write_private_json(path, _runtime_payload(metadata))


def _attempt_run_id(execution: job_runtime.JobExecution) -> str:
    return f"{execution.job_id}.a{execution.attempt_number}"


def _write_attempt_report(
        path: Path, report: attempt_reporting.AttemptReport) -> None:
    attempt_reporting.publish(path, report)


def _load_attempt_report(
        paths: _AttemptPaths, *, execution: job_runtime.JobExecution,
        missing_ok: bool = False) -> attempt_reporting.AttemptReport | None:
    payload = _read_private_json(
        paths.attempt_report, missing_ok=missing_ok,
        maximum_bytes=attempt_reporting.MAX_REPORT_BYTES)
    if payload is None:
        return None
    try:
        return attempt_reporting.parse_report(
            payload,
            expected_job_id=execution.job_id,
            expected_attempt_number=execution.attempt_number,
            expected_run_id=_attempt_run_id(execution),
            expected_operation=execution.command,
        )
    except (TypeError, ValueError) as exc:
        raise JobManagerCorruptError(
            "attempt outcome report is invalid") from exc


def _cap_open_worker_log(handle) -> None:
    """Keep a live inherited log descriptor within a bounded rolling window."""
    if os.fstat(handle.fileno()).st_size <= _MAX_WORKER_LOG_BYTES:
        return
    os.ftruncate(handle.fileno(), 0)
    handle.write(b"[earlier worker output truncated at private log limit]\n")
    handle.flush()


def _cap_closed_worker_log(path: Path) -> None:
    """Retain only the final bounded tail after a worker closes its handle."""
    storage_policy.assert_no_link_components(path)
    before = os.lstat(path)
    if before.st_size <= _MAX_WORKER_LOG_BYTES:
        return
    if not stat.S_ISREG(before.st_mode) or before.st_nlink != 1:
        raise JobManagerCorruptError("worker log is not one regular file")
    flags = os.O_RDONLY | getattr(os, "O_NOFOLLOW", 0)
    flags |= getattr(os, "O_BINARY", 0)
    descriptor = os.open(path, flags)
    try:
        opened = os.fstat(descriptor)
        identity = (int(opened.st_dev), int(opened.st_ino))
        if (not stat.S_ISREG(opened.st_mode) or opened.st_nlink != 1
                or identity != (int(before.st_dev), int(before.st_ino))):
            raise JobManagerCorruptError("worker log changed while opening")
        os.lseek(descriptor, -_MAX_WORKER_LOG_BYTES, os.SEEK_END)
        chunks = []
        remaining = _MAX_WORKER_LOG_BYTES
        while remaining:
            chunk = os.read(descriptor, min(remaining, 64 * 1024))
            if not chunk:
                break
            chunks.append(chunk)
            remaining -= len(chunk)
        after = os.fstat(descriptor)
        named = os.lstat(path)
        if ((int(after.st_dev), int(after.st_ino)) != identity
                or (int(named.st_dev), int(named.st_ino)) != identity
                or after.st_nlink != 1 or named.st_nlink != 1):
            raise JobManagerCorruptError("worker log changed while reading")
    finally:
        os.close(descriptor)
    marker = b"[earlier worker output truncated at private log limit]\n"
    tail = b"".join(chunks)
    if len(marker) + len(tail) > _MAX_WORKER_LOG_BYTES:
        tail = tail[len(marker):]
    storage_policy.atomic_write_private(
        path, lambda output: output.write(marker + tail), text=False)


def _unique_object(pairs):
    result = {}
    for key, value in pairs:
        if key in result:
            raise ValueError("duplicate JSON key")
        result[key] = value
    return result


def _reject_constant(value: str):
    raise ValueError(f"invalid JSON constant {value}")


def _read_private_json(
        path: Path, *, missing_ok: bool = False,
        maximum_bytes: int = _MAX_MANAGER_JSON_BYTES) -> dict | None:
    descriptor = -1
    try:
        storage_policy.assert_no_link_components(path)
        before = os.lstat(path)
    except FileNotFoundError:
        if missing_ok:
            return None
        raise JobManagerCorruptError("required attempt metadata is missing")
    except (OSError, storage_policy.StoragePolicyError) as exc:
        raise JobManagerCorruptError("attempt metadata path is unsafe") from exc
    try:
        if (not stat.S_ISREG(before.st_mode) or before.st_nlink != 1
                or before.st_size <= 0
                or before.st_size > maximum_bytes):
            raise JobManagerCorruptError(
                "attempt metadata is not one bounded regular file")
        if os.name == "nt":
            private = storage_policy.windows_path_is_private(
                path, directory=False)
        else:
            private = (stat.S_IMODE(before.st_mode)
                       == storage_policy.PRIVATE_FILE_MODE)
        if not private:
            raise JobManagerCorruptError("attempt metadata is not private")
        flags = os.O_RDONLY | getattr(os, "O_NOFOLLOW", 0)
        flags |= getattr(os, "O_BINARY", 0)
        descriptor = os.open(path, flags)
        opened = os.fstat(descriptor)
        if (not stat.S_ISREG(opened.st_mode) or opened.st_nlink != 1
                or (opened.st_dev, opened.st_ino, opened.st_size)
                != (before.st_dev, before.st_ino, before.st_size)):
            raise JobManagerCorruptError(
                "attempt metadata changed while opening")
        remaining = int(opened.st_size)
        chunks = []
        while remaining:
            chunk = os.read(descriptor, min(remaining, 64 * 1024))
            if not chunk:
                raise JobManagerCorruptError(
                    "attempt metadata ended before its declared size")
            chunks.append(chunk)
            remaining -= len(chunk)
        after = os.fstat(descriptor)
        if (after.st_dev, after.st_ino, after.st_size, after.st_mtime_ns,
                after.st_ctime_ns, after.st_nlink) != (
                opened.st_dev, opened.st_ino, opened.st_size,
                opened.st_mtime_ns, opened.st_ctime_ns, opened.st_nlink):
            raise JobManagerCorruptError(
                "attempt metadata changed while being read")
        payload = json.loads(
            b"".join(chunks).decode("utf-8"),
            object_pairs_hook=_unique_object,
            parse_constant=_reject_constant,
        )
        if not isinstance(payload, dict):
            raise JobManagerCorruptError(
                "attempt metadata must contain one JSON object")
        return payload
    except JobManagerCorruptError:
        raise
    except (OSError, UnicodeError, ValueError, RecursionError,
            storage_policy.StoragePolicyError) as exc:
        raise JobManagerCorruptError(
            "attempt metadata could not be parsed safely") from exc
    finally:
        if descriptor >= 0:
            os.close(descriptor)


def _valid_timestamp(value: Any, name: str) -> float:
    if (isinstance(value, bool) or not isinstance(value, (int, float))
            or not math.isfinite(float(value)) or float(value) < 0
            or float(value) > attempt_reporting.MAX_TIMESTAMP):
        raise JobManagerCorruptError(f"runtime {name} is invalid")
    return float(value)


def _valid_pid(value: Any, name: str, *, optional: bool) -> int | None:
    if value is None and optional:
        return None
    if (isinstance(value, bool) or not isinstance(value, int)
            or value <= 0):
        raise JobManagerCorruptError(f"runtime {name} is invalid")
    return value


def _valid_birth(value: Any, name: str, *, optional: bool) -> str | None:
    if value is None and optional:
        return None
    if (not isinstance(value, str) or not value or len(value) > 256
            or any(character in value for character in "\r\n\x00")):
        raise JobManagerCorruptError(f"runtime {name} is invalid")
    return value


def _load_runtime(path: Path, *, execution: job_runtime.JobExecution
                  ) -> RuntimeMetadata:
    payload = _read_private_json(path)
    expected = {
        "schema_version", "kind", "job_id", "attempt_number",
        "attempt_token_sha256", "run_id", "phase", "job_status",
        "manager_pid", "manager_birth", "worker_pid", "worker_birth",
        "heartbeat_at", "cleanup_confirmed", "exit_code", "updated_at",
    }
    if set(payload) != expected:
        raise JobManagerCorruptError(
            "runtime metadata has missing or unknown fields")
    if (type(payload["schema_version"]) is not int
            or payload["schema_version"] != MANAGER_SCHEMA_VERSION
            or payload["kind"] != "job_attempt_runtime"):
        raise JobManagerCorruptError("runtime metadata schema is unsupported")
    if (payload["job_id"] != execution.job_id
            or payload["attempt_number"] != execution.attempt_number
            or payload["attempt_token_sha256"] !=
            _token_digest(execution.attempt_token)):
        raise JobManagerCorruptError(
            "runtime metadata does not bind the active attempt")
    phase = payload["phase"]
    status = payload["job_status"]
    if phase not in _RUNTIME_PHASES or status not in job_runtime.JOB_STATUSES:
        raise JobManagerCorruptError("runtime phase or job status is invalid")
    cleanup = payload["cleanup_confirmed"]
    if cleanup is not None and not isinstance(cleanup, bool):
        raise JobManagerCorruptError("runtime cleanup status is invalid")
    exit_code = payload["exit_code"]
    if (exit_code is not None
            and (isinstance(exit_code, bool) or not isinstance(exit_code, int))):
        raise JobManagerCorruptError("runtime exit code is invalid")
    attempt_number = payload["attempt_number"]
    if (isinstance(attempt_number, bool) or not isinstance(attempt_number, int)
            or attempt_number < 1):
        raise JobManagerCorruptError("runtime attempt number is invalid")
    run_id = payload["run_id"]
    if (not isinstance(run_id, str) or not run_id or len(run_id) > 128
            or any(character not in
                   "ABCDEFGHIJKLMNOPQRSTUVWXYZabcdefghijklmnopqrstuvwxyz"
                   "0123456789_.-" for character in run_id)):
        raise JobManagerCorruptError("runtime run ID is invalid")
    return RuntimeMetadata(
        job_id=execution.job_id,
        attempt_number=attempt_number,
        attempt_token_sha256=payload["attempt_token_sha256"],
        run_id=run_id,
        phase=phase,
        job_status=status,
        manager_pid=_valid_pid(
            payload["manager_pid"], "manager_pid", optional=False),
        manager_birth=_valid_birth(
            payload["manager_birth"], "manager_birth", optional=True),
        worker_pid=_valid_pid(
            payload["worker_pid"], "worker_pid", optional=True),
        worker_birth=_valid_birth(
            payload["worker_birth"], "worker_birth", optional=True),
        heartbeat_at=_valid_timestamp(
            payload["heartbeat_at"], "heartbeat_at"),
        cleanup_confirmed=cleanup,
        exit_code=exit_code,
        updated_at=_valid_timestamp(payload["updated_at"], "updated_at"),
    )


_WINDOWS_DEFINITIVE_GONE_ERRORS = frozenset({
    87,    # ERROR_INVALID_PARAMETER: no process has this PID.
    1168,  # ERROR_NOT_FOUND.
})


class _WindowsProcessReference:
    """A live process-object reference that prevents PID reuse while held."""

    __slots__ = ("_kernel32", "_handle")

    def __init__(self, kernel32: Any, handle: Any):
        self._kernel32 = kernel32
        self._handle = handle

    def snapshot(self) -> tuple[str, str | None]:
        """Return ``live`` with birth identity, ``gone``, or unverifiable."""
        from ctypes import wintypes

        if self._handle is None:
            return "unverifiable", None
        get_exit_code = self._kernel32.GetExitCodeProcess
        get_exit_code.argtypes = [
            wintypes.HANDLE, ctypes.POINTER(wintypes.DWORD),
        ]
        get_exit_code.restype = wintypes.BOOL
        exit_code = wintypes.DWORD()
        if not get_exit_code(self._handle, ctypes.byref(exit_code)):
            return "unverifiable", None
        if exit_code.value != 259:  # STILL_ACTIVE
            return "gone", None

        get_times = self._kernel32.GetProcessTimes
        get_times.argtypes = [
            wintypes.HANDLE,
            ctypes.POINTER(wintypes.FILETIME),
            ctypes.POINTER(wintypes.FILETIME),
            ctypes.POINTER(wintypes.FILETIME),
            ctypes.POINTER(wintypes.FILETIME),
        ]
        get_times.restype = wintypes.BOOL
        creation = wintypes.FILETIME()
        exit_time = wintypes.FILETIME()
        kernel_time = wintypes.FILETIME()
        user_time = wintypes.FILETIME()
        if not get_times(
                self._handle, ctypes.byref(creation), ctypes.byref(exit_time),
                ctypes.byref(kernel_time), ctypes.byref(user_time)):
            return "unverifiable", None
        value = ((int(creation.dwHighDateTime) << 32)
                 | int(creation.dwLowDateTime))
        return "live", f"windows:{value}"

    def close(self) -> None:
        from ctypes import wintypes

        if self._handle is None:
            return
        handle, self._handle = self._handle, None
        close_handle = self._kernel32.CloseHandle
        close_handle.argtypes = [wintypes.HANDLE]
        close_handle.restype = wintypes.BOOL
        close_handle(handle)

    def __enter__(self) -> _WindowsProcessReference:
        return self

    def __exit__(self, _exc_type, _exc, _traceback) -> None:
        self.close()


def _open_windows_process_reference(
        pid: int) -> tuple[str, _WindowsProcessReference | None]:
    """Open a query handle, distinguishing absent from inaccessible PIDs."""
    from ctypes import wintypes

    kernel32 = ctypes.WinDLL("kernel32", use_last_error=True)
    open_process = kernel32.OpenProcess
    open_process.argtypes = [wintypes.DWORD, wintypes.BOOL, wintypes.DWORD]
    open_process.restype = wintypes.HANDLE
    handle = open_process(0x1000, False, pid)
    if not handle:
        error = ctypes.get_last_error()
        status = (
            "gone" if error in _WINDOWS_DEFINITIVE_GONE_ERRORS
            else "unverifiable"
        )
        return status, None
    return "open", _WindowsProcessReference(kernel32, handle)


def _windows_process_identity_probe(pid: int) -> tuple[str, str | None]:
    """Return one fail-closed Windows liveness and birth snapshot."""
    status, reference = _open_windows_process_reference(pid)
    if reference is None:
        return status, None
    with reference:
        return reference.snapshot()


def process_birth_identity(pid: int) -> str | None:
    """Return an OS process birth identity where the platform exposes one."""
    if isinstance(pid, bool) or not isinstance(pid, int) or pid <= 0:
        raise JobManagerValidationError("PID must be a positive integer")
    if os.name == "nt":
        status, identity = _windows_process_identity_probe(pid)
        return identity if status == "live" else None

    proc_stat = Path("/proc") / str(pid) / "stat"
    try:
        raw = proc_stat.read_text(encoding="utf-8")
        closing = raw.rfind(")")
        fields = raw[closing + 2:].split()
        start_ticks = fields[19]
        boot_id = Path(
            "/proc/sys/kernel/random/boot_id").read_text(
                encoding="ascii").strip()
        if closing < 0 or not start_ticks.isdigit() or not boot_id:
            return None
        return f"linux:{boot_id}:{start_ticks}"
    except (OSError, UnicodeError, IndexError):
        return None


def _pid_exists(pid: int) -> bool:
    if os.name == "nt":
        status, _identity = _windows_process_identity_probe(pid)
        return status != "gone"
    try:
        os.kill(pid, 0)
    except ProcessLookupError:
        return False
    except PermissionError:
        return True
    except OSError:
        return False
    return True


def probe_process_identity(pid: int, expected_birth: str | None) -> str:
    """Return ``match``, ``mismatch``, ``gone``, or ``unverifiable``."""
    if os.name == "nt":
        status, current = _windows_process_identity_probe(pid)
        if status != "live":
            return status
        if expected_birth is None:
            return "unverifiable"
        return "match" if current == expected_birth else "mismatch"
    if not _pid_exists(pid):
        return "gone"
    current = process_birth_identity(pid)
    if current is None:
        return "unverifiable"
    if expected_birth is None:
        return "unverifiable"
    return "match" if current == expected_birth else "mismatch"


def _process_group_gone(pid: int) -> bool:
    if os.name == "nt":
        raise RuntimeError("POSIX process groups are unavailable")
    try:
        os.killpg(pid, 0)
    except ProcessLookupError:
        return True
    except PermissionError:
        return False
    return False


def _worker_tree_gone(pid: int | None, birth: str | None) -> bool:
    if pid is None:
        return True
    if os.name != "nt":
        return _process_group_gone(pid)
    return probe_process_identity(pid, birth) in {"gone", "mismatch"}


def _confirm_worker_tree_gone(
        pid: int | None, birth: str | None, *, timeout: float = 1.0) -> bool:
    """Allow asynchronous OS Job/process-group teardown to become visible."""
    deadline = time.monotonic() + timeout
    while True:
        if _worker_tree_gone(pid, birth):
            return True
        if time.monotonic() >= deadline:
            return False
        time.sleep(0.025)


def _terminate_recovered_worker(pid: int, birth: str | None) -> bool:
    """Terminate only an exact live worker; never signal a PID mismatch."""
    if os.name == "nt":
        status, reference = _open_windows_process_reference(pid)
        if status != "open" or reference is None:
            return False
        with reference:
            live_status, current_birth = reference.snapshot()
            if (live_status != "live" or birth is None
                    or current_birth != birth):
                return False
            # Retaining this process-object handle prevents the verified PID
            # from being recycled between the birth check and taskkill.
            creationflags = getattr(subprocess, "CREATE_NO_WINDOW", 0)
            completed = subprocess.run(
                ["taskkill", "/PID", str(pid), "/T", "/F"],
                stdin=subprocess.DEVNULL, stdout=subprocess.DEVNULL,
                stderr=subprocess.DEVNULL, check=False,
                timeout=_RECOVERY_TERMINATE_GRACE,
                creationflags=creationflags,
            )
            deadline = time.monotonic() + _RECOVERY_TERMINATE_GRACE
            while time.monotonic() < deadline:
                live_status, _current_birth = reference.snapshot()
                if live_status == "gone":
                    return completed.returncode == 0
                if live_status == "unverifiable":
                    return False
                time.sleep(0.05)
            return False

    if probe_process_identity(pid, birth) != "match":
        return False

    try:
        if os.getpgid(pid) != pid:
            return False
    except (ProcessLookupError, PermissionError):
        return False
    if probe_process_identity(pid, birth) != "match":
        return False
    try:
        os.killpg(pid, signal.SIGTERM)
    except ProcessLookupError:
        return True
    deadline = time.monotonic() + _RECOVERY_TERMINATE_GRACE
    while time.monotonic() < deadline:
        if _process_group_gone(pid):
            return True
        time.sleep(0.05)
    # Recheck birth immediately before escalation so PID reuse fails closed.
    if probe_process_identity(pid, birth) != "match":
        return False
    try:
        os.killpg(pid, signal.SIGKILL)
    except ProcessLookupError:
        return True
    deadline = time.monotonic() + _RECOVERY_TERMINATE_GRACE
    while time.monotonic() < deadline:
        if _process_group_gone(pid):
            return True
        time.sleep(0.05)
    return False


def _inject_telemetry_arguments(
        execution: job_runtime.JobExecution, paths: _AttemptPaths,
        run_id: str) -> list[str]:
    arguments = list(execution.argv)
    try:
        terminator = arguments.index("--")
    except ValueError:
        terminator = len(arguments)
    if (execution.attempt_number > 1
            and execution.command in {"full", "batch"}
            and "--resume" not in arguments[:terminator]):
        arguments.insert(terminator, "--resume")
        terminator += 1
    telemetry = [
        "--run-id", run_id,
        "--run-events", str(paths.events),
        "--run-report", str(paths.report),
    ]
    arguments[terminator:terminator] = telemetry
    return [execution.command, *arguments]


def _operation_timeout(
        execution: job_runtime.JobExecution,
        runtime: runtime_supervision.PipelineRuntimeBinding) -> float:
    if execution.timeout_seconds is not None:
        return execution.timeout_seconds
    return float(runtime.operation_timeouts.get(
        execution.command, _GENERIC_BACKGROUND_TIMEOUT))


def _report_status(path: Path, *, operation: str,
                   run_id: str) -> str | None:
    payload = _read_private_json(
        path, missing_ok=True, maximum_bytes=_MAX_RUN_REPORT_BYTES)
    if payload is None:
        return None
    if (type(payload.get("schema_version")) is not int
            or payload.get("schema_version") not in
            run_telemetry.SUPPORTED_REPORT_SCHEMA_VERSIONS
            or payload.get("operation") != operation
            or payload.get("run_id") != run_id):
        raise JobManagerCorruptError("run report identity or status is invalid")
    status = payload.get("status")
    if status == "running":
        return None
    if status not in _TERMINAL_TELEMETRY:
        raise JobManagerCorruptError("run report identity or status is invalid")
    return status


def _telemetry_status(paths: _AttemptPaths, *, operation: str,
                      run_id: str) -> str | None:
    # Read the terminal report first and never give recovery permission to
    # rewrite it. An absent/empty event stream causes RunTelemetry.recover()
    # to publish a fresh running snapshot when a report path is supplied.
    report_status = _report_status(
        paths.report, operation=operation, run_id=run_id)
    event_status = None
    try:
        events_present = paths.events.stat().st_size > 0
    except OSError:
        events_present = False
    if events_present:
        try:
            recovered = run_telemetry.RunTelemetry.recover(
                operation, run_id=run_id, events_path=paths.events,
                report_path=None)
            if recovered.finished:
                event_status = recovered.report_payload()["status"]
        except (OSError, UnicodeError, ValueError):
            event_status = None
    if (event_status is not None and report_status is not None
            and event_status != report_status):
        raise JobManagerCorruptError(
            "run event and report terminal statuses disagree")
    return event_status or report_status


def _synthesize_success(paths: _AttemptPaths, *, operation: str,
                        run_id: str) -> str | None:
    try:
        telemetry = run_telemetry.RunTelemetry.recover(
            operation, run_id=run_id, events_path=paths.events,
            report_path=paths.report)
        if not telemetry.finished:
            telemetry.finish("succeeded")
        return telemetry.report_payload()["status"]
    except (OSError, UnicodeError, ValueError):
        return None


def _classify_terminal(*, exit_code: int | None, cancel_observed: bool,
                       cleanup_confirmed: bool,
                       telemetry_status: str | None,
                       manager_error: bool) -> str:
    if not cleanup_confirmed:
        return "orphaned"
    if telemetry_status in {"succeeded", "partial"}:
        return telemetry_status
    if cancel_observed:
        return "cancelled" if telemetry_status == "cancelled" else "interrupted"
    if telemetry_status == "failed":
        return "failed"
    if telemetry_status == "cancelled":
        return "failed"
    if manager_error:
        return "failed"
    if exit_code == 0:
        return "succeeded"
    return "failed"


def _result_reason(status: str, exit_code: int | None,
                   cancel_observed: bool) -> str:
    if status in {"succeeded", "partial"}:
        return "completed"
    if status == "cancelled":
        return "cancelled"
    if status == "orphaned":
        return "cleanup_unconfirmed"
    if status == "interrupted":
        return "outcome_unconfirmed"
    if exit_code == 124:
        return "timeout"
    if cancel_observed:
        return "cancellation_failed"
    return "worker_failed"


def _write_ready(paths: _AttemptPaths, *, execution: job_runtime.JobExecution,
                 nonce: str | None, manager_pid: int,
                 manager_birth: str | None) -> None:
    if nonce is None:
        return
    storage_policy.atomic_write_private_json(paths.ready, {
        "schema_version": MANAGER_SCHEMA_VERSION,
        "kind": "job_manager_ready",
        "job_id": execution.job_id,
        "attempt_number": execution.attempt_number,
        "attempt_token_sha256": _token_digest(execution.attempt_token),
        "nonce_sha256": hashlib.sha256(nonce.encode("ascii")).hexdigest(),
        "manager_pid": manager_pid,
        "manager_birth": manager_birth,
        "ready_at": time.time(),
    })


def _transition_terminal(
        store: job_runtime.JobStore, execution: job_runtime.JobExecution,
        status: str, lease: job_runtime.JobLease) -> job_runtime.JobSummary:
    current = store.get_job(execution.job_id)
    if current.terminal:
        return current
    return store.transition_job(
        execution.job_id, status,
        attempt_token=execution.attempt_token,
        expected_revision=current.revision,
        lease=lease,
    )


def run_job(
        store: job_runtime.JobStore, job_id: str, *,
        script_path: Path | None = None,
        supervisor: Callable[..., int] | None = None,
        ready_nonce: str | None = None) -> ManagerResult:
    """Run one queued attempt synchronously while holding its manager lease."""
    runtime_binding = _default_runtime_binding()
    script_path = (
        runtime_binding.script_path
        if script_path is None else Path(script_path))
    supervisor = (
        runtime_binding.supervisor if supervisor is None else supervisor)
    with store.lease(job_id, timeout=0) as lease:
        execution = store.load_execution(job_id, lease=lease)
        if execution.status != "queued":
            raise job_runtime.JobStateError(
                "only a queued job can start a manager attempt")
        initial = store.get_job(job_id)
        paths = _attempt_paths(
            store, job_id, execution.attempt_number, create=True)
        run_id = _attempt_run_id(execution)
        now = time.time()
        manager_pid = os.getpid()
        manager_birth = process_birth_identity(manager_pid)
        attempt_report = attempt_reporting.new_report(
            job_id=job_id,
            attempt_number=execution.attempt_number,
            run_id=run_id,
            operation=execution.command,
            status="queued",
            submitted_at=initial.updated_at,
            observed_at=now,
            manager_started_at=now,
        )
        _write_attempt_report(paths.attempt_report, attempt_report)
        try:
            job_runtime.validate_execution_directories(execution)
        except job_runtime.JobRuntimeError:
            current = store.transition_job(
                job_id, "failed", attempt_token=execution.attempt_token,
                expected_revision=execution.revision, lease=lease)
            finished = max(time.time(), attempt_report.updated_at)
            attempt_report = attempt_reporting.advance(
                attempt_report,
                observed_at=finished,
                status=current.status,
                trigger="manager_error",
                cleanup_confirmed=True,
                cleanup_confirmed_at=finished,
                finished_at=finished,
                terminal_reason="execution_validation_failed",
                manager_error=True,
            )
            _write_attempt_report(paths.attempt_report, attempt_report)
            raise
        runtime = RuntimeMetadata(
            job_id=job_id,
            attempt_number=execution.attempt_number,
            attempt_token_sha256=_token_digest(execution.attempt_token),
            run_id=run_id,
            phase="starting",
            job_status="queued",
            manager_pid=manager_pid,
            manager_birth=manager_birth,
            worker_pid=None,
            worker_birth=None,
            heartbeat_at=now,
            cleanup_confirmed=None,
            exit_code=None,
            updated_at=now,
        )
        _write_runtime(paths.runtime, runtime)
        current = store.transition_job(
            job_id, "starting", attempt_token=execution.attempt_token,
            expected_revision=execution.revision, lease=lease)
        runtime = replace(
            runtime, job_status=current.status, updated_at=time.time())
        _write_runtime(paths.runtime, runtime)
        attempt_report = attempt_reporting.advance(
            attempt_report,
            observed_at=max(time.time(), attempt_report.updated_at),
            status=current.status,
        )
        _write_attempt_report(paths.attempt_report, attempt_report)

        # Cancellation requested before launch never creates a worker.
        if store.is_cancel_requested(job_id, execution.attempt_token):
            cancel_requested_at = store.cancel_requested_at(
                job_id, execution.attempt_token)
            assert cancel_requested_at is not None
            cancel_observed_at = max(
                time.time(), attempt_report.updated_at, cancel_requested_at)
            current = store.transition_job(
                job_id, "cancel_requested",
                attempt_token=execution.attempt_token,
                expected_revision=current.revision, lease=lease)
            attempt_report = attempt_reporting.advance(
                attempt_report,
                observed_at=cancel_observed_at,
                status=current.status,
                trigger="cancel",
                cancel_requested=True,
                cancel_requested_at=cancel_requested_at,
                cancel_observed=True,
                cancel_observed_at=cancel_observed_at,
            )
            _write_attempt_report(paths.attempt_report, attempt_report)
            _write_ready(
                paths, execution=execution, nonce=ready_nonce,
                manager_pid=manager_pid, manager_birth=manager_birth)
            current = store.transition_job(
                job_id, "cancelled", attempt_token=execution.attempt_token,
                expected_revision=current.revision, lease=lease)
            finished = max(time.time(), attempt_report.updated_at)
            runtime = replace(
                runtime, phase="terminal", job_status=current.status,
                cleanup_confirmed=True, exit_code=130,
                heartbeat_at=finished, updated_at=finished)
            _write_runtime(paths.runtime, runtime)
            attempt_report = attempt_reporting.advance(
                attempt_report,
                observed_at=finished,
                status=current.status,
                cleanup_confirmed=True,
                cleanup_confirmed_at=finished,
                finished_at=finished,
                terminal_reason="cancelled",
                exit_code=130,
            )
            _write_attempt_report(paths.attempt_report, attempt_report)
            return ManagerResult(
                job_id=job_id, status="cancelled",
                attempt_number=execution.attempt_number, exit_code=130,
                cleanup_confirmed=True, reason="cancelled")

        worker: Any | None = None
        worker_birth: str | None = None
        cancel_observed = False
        last_heartbeat = 0.0
        exit_code: int | None = None
        manager_error = False
        supervisor_cleanup_unconfirmed = False
        active_log_handle = None

        def child_started(process) -> None:
            nonlocal worker, worker_birth, runtime, current, attempt_report
            worker = process
            worker_birth = process_birth_identity(int(process.pid))
            worker_started_at = max(time.time(), attempt_report.updated_at)
            runtime = replace(
                runtime, worker_pid=int(process.pid),
                worker_birth=worker_birth, heartbeat_at=worker_started_at,
                updated_at=worker_started_at)
            _write_runtime(paths.runtime, runtime)
            current = store.transition_job(
                job_id, "running", attempt_token=execution.attempt_token,
                expected_revision=current.revision, lease=lease)
            runtime = replace(
                runtime, phase="running", job_status=current.status,
                updated_at=worker_started_at)
            _write_runtime(paths.runtime, runtime)
            attempt_report = attempt_reporting.advance(
                attempt_report,
                observed_at=worker_started_at,
                status=current.status,
                worker_started=True,
                worker_started_at=worker_started_at,
            )
            _write_attempt_report(paths.attempt_report, attempt_report)
            _write_ready(
                paths, execution=execution, nonce=ready_nonce,
                manager_pid=manager_pid, manager_birth=manager_birth)

        def cancellation_requested() -> bool:
            nonlocal cancel_observed, current, runtime, attempt_report
            requested = store.is_cancel_requested(
                job_id, execution.attempt_token)
            if requested and not cancel_observed:
                cancel_requested_at = store.cancel_requested_at(
                    job_id, execution.attempt_token)
                assert cancel_requested_at is not None
                cancel_observed_at = max(
                    time.time(), attempt_report.updated_at,
                    cancel_requested_at)
                cancel_observed = True
                current = store.transition_job(
                    job_id, "cancel_requested",
                    attempt_token=execution.attempt_token,
                    expected_revision=current.revision, lease=lease)
                runtime = replace(
                    runtime, job_status=current.status,
                    heartbeat_at=cancel_observed_at,
                    updated_at=cancel_observed_at)
                _write_runtime(paths.runtime, runtime)
                attempt_report = attempt_reporting.advance(
                    attempt_report,
                    observed_at=cancel_observed_at,
                    status=current.status,
                    trigger="cancel",
                    cancel_requested=True,
                    cancel_requested_at=cancel_requested_at,
                    cancel_observed=True,
                    cancel_observed_at=cancel_observed_at,
                )
                _write_attempt_report(paths.attempt_report, attempt_report)
            return requested

        def heartbeat(_process) -> None:
            nonlocal last_heartbeat, runtime
            now_heartbeat = time.time()
            if now_heartbeat - last_heartbeat < _HEARTBEAT_INTERVAL:
                return
            last_heartbeat = now_heartbeat
            if active_log_handle is not None:
                _cap_open_worker_log(active_log_handle)
            runtime = replace(
                runtime, heartbeat_at=now_heartbeat,
                updated_at=now_heartbeat)
            _write_runtime(paths.runtime, runtime)

        worker_argv = _inject_telemetry_arguments(
            execution, paths, run_id)
        environment = {
            JOB_ROOT_ENV: str(store.root),
            JOB_ID_ENV: job_id,
            JOB_ATTEMPT_TOKEN_ENV: execution.attempt_token,
            OUTPUT_ROOT_ENV: str(execution.output_root),
            _READY_NONCE_ENV: None,
        }
        try:
            with storage_policy.open_private_append(paths.log) as log_handle:
                active_log_handle = log_handle
                exit_code = int(supervisor(
                    script_path, worker_argv,
                    operation=execution.command,
                    timeout=_operation_timeout(execution, runtime_binding),
                    working_directory=execution.working_directory,
                    environment_overrides=environment,
                    run_id=run_id,
                    run_events=paths.events,
                    run_report=paths.report,
                    cancel_requested=cancellation_requested,
                    on_child_started=child_started,
                    heartbeat=heartbeat,
                    stdout_target=log_handle,
                    stderr_target=log_handle,
                ))
        except runtime_binding.cleanup_error_type:
            manager_error = True
            supervisor_cleanup_unconfirmed = True
        except BaseException:
            manager_error = True
        finally:
            active_log_handle = None
        try:
            _cap_closed_worker_log(paths.log)
        except BaseException:
            manager_error = True

        worker_pid = int(worker.pid) if worker is not None else None
        cleanup_confirmed = (
            False if supervisor_cleanup_unconfirmed else
            _confirm_worker_tree_gone(worker_pid, worker_birth)
        )
        try:
            telemetry_status = _telemetry_status(
                paths, operation=execution.command, run_id=run_id)
        except JobManagerCorruptError:
            telemetry_status = None
            manager_error = True
        if (exit_code == 0 and cleanup_confirmed
                and telemetry_status is None and not manager_error):
            telemetry_status = _synthesize_success(
                paths, operation=execution.command, run_id=run_id)
        terminal = _classify_terminal(
            exit_code=exit_code,
            cancel_observed=cancel_observed,
            cleanup_confirmed=cleanup_confirmed,
            telemetry_status=telemetry_status,
            manager_error=manager_error,
        )
        current = _transition_terminal(store, execution, terminal, lease)
        finished = max(time.time(), attempt_report.updated_at)
        runtime = replace(
            runtime, phase="terminal", job_status=current.status,
            heartbeat_at=finished, cleanup_confirmed=cleanup_confirmed,
            exit_code=exit_code, updated_at=finished)
        _write_runtime(paths.runtime, runtime)
        reason = _result_reason(
            current.status, exit_code, cancel_observed)
        if cancel_observed:
            trigger = "cancel"
        elif exit_code == 124:
            trigger = "timeout"
        elif manager_error:
            trigger = "manager_error"
        else:
            trigger = "normal"
        # The terminal transition and runtime write above have already
        # committed. Damaged cancellation evidence must not abort the attempt
        # report and the manager result; the marker stays on disk and is
        # reported by the next reconciliation, which owns the repair fields
        # this non-recovery report is not permitted to carry.
        final_cancel_requested_at, _cancel_evidence_corrupt = (
            _recovery_cancel_evidence(store, execution))
        finished = max(finished, final_cancel_requested_at or 0.0)
        cancel_requested = (
            attempt_report.cancel_requested
            or final_cancel_requested_at is not None)
        attempt_report = attempt_reporting.advance(
            attempt_report,
            observed_at=finished,
            status=current.status,
            trigger=trigger,
            cancel_requested=cancel_requested,
            cancel_requested_at=(
                attempt_report.cancel_requested_at
                if attempt_report.cancel_requested_at is not None
                else final_cancel_requested_at),
            cleanup_confirmed=cleanup_confirmed,
            cleanup_confirmed_at=(finished if cleanup_confirmed else None),
            finished_at=finished,
            worker_telemetry_status=telemetry_status,
            terminal_reason=reason,
            exit_code=exit_code,
            manager_error=manager_error,
        )
        _write_attempt_report(paths.attempt_report, attempt_report)
        return ManagerResult(
            job_id=job_id,
            status=current.status,
            attempt_number=execution.attempt_number,
            exit_code=exit_code,
            cleanup_confirmed=cleanup_confirmed,
            reason=reason,
        )


def _ready_matches(path: Path, *, execution: job_runtime.JobExecution,
                   nonce: str, manager_pid: int,
                   manager_birth: str | None) -> bool:
    payload = _read_private_json(path, missing_ok=True)
    if payload is None:
        return False
    expected_keys = {
        "schema_version", "kind", "job_id", "attempt_number",
        "attempt_token_sha256", "nonce_sha256", "manager_pid",
        "manager_birth", "ready_at",
    }
    if set(payload) != expected_keys:
        raise JobManagerCorruptError("ready marker has invalid fields")
    return (
        type(payload["schema_version"]) is int
        and payload["schema_version"] == MANAGER_SCHEMA_VERSION
        and payload["kind"] == "job_manager_ready"
        and payload["job_id"] == execution.job_id
        and payload["attempt_number"] == execution.attempt_number
        and payload["attempt_token_sha256"] ==
        _token_digest(execution.attempt_token)
        and payload["nonce_sha256"] ==
        hashlib.sha256(nonce.encode("ascii")).hexdigest()
        and payload["manager_pid"] == manager_pid
        and (manager_birth is None
             or payload["manager_birth"] == manager_birth)
    )


def launch_detached(
        store: job_runtime.JobStore, job_id: str, *,
        script_path: Path | None = None,
        ready_timeout: float = 10.0) -> LaunchResult:
    """Launch the script entrypoint and wait for its exact ready handshake."""
    if (isinstance(ready_timeout, bool)
            or not isinstance(ready_timeout, (int, float))
            or not math.isfinite(float(ready_timeout))
            or float(ready_timeout) <= 0):
        raise JobManagerValidationError(
            "ready_timeout must be a finite positive number")
    execution = store.load_execution(job_id)
    if execution.status != "queued":
        raise job_runtime.JobStateError(
            "only a queued job can be launched")
    try:
        job_runtime.validate_execution_directories(execution)
    except job_runtime.JobRuntimeError as exc:
        current = reconcile_job(store, job_id, fail_queued=True)
        raise JobManagerLaunchError(
            "background job submission roots failed validation "
            f"(job status {current.status})") from exc
    runtime_binding = _default_runtime_binding()
    script_path = (
        runtime_binding.script_path
        if script_path is None else Path(script_path))
    paths = _attempt_paths(
        store, job_id, execution.attempt_number, create=False)
    nonce = uuid4().hex
    environment = os.environ.copy()
    for name in (
            JOB_ROOT_ENV, JOB_ID_ENV, JOB_ATTEMPT_TOKEN_ENV,
            OUTPUT_ROOT_ENV):
        environment.pop(name, None)
    environment[_READY_NONCE_ENV] = nonce
    command = [
        sys.executable, "-u", str(MANAGER_SCRIPT_PATH), "_manage",
        "--root", str(store.root), "--job-id", job_id,
        "--script", str(script_path.resolve()),
    ]
    options = {
        "stdin": subprocess.DEVNULL,
        "stdout": subprocess.DEVNULL,
        "stderr": subprocess.DEVNULL,
        "env": environment,
        "cwd": str(execution.working_directory),
        "close_fds": True,
    }
    if os.name == "nt":
        options["creationflags"] = (
            getattr(subprocess, "CREATE_NO_WINDOW", 0)
            | getattr(subprocess, "DETACHED_PROCESS", 0)
            | getattr(subprocess, "CREATE_NEW_PROCESS_GROUP", 0)
        )
    else:
        options["start_new_session"] = True
    try:
        process = subprocess.Popen(command, **options)
    except (OSError, subprocess.SubprocessError) as exc:
        current = reconcile_job(store, job_id, fail_queued=True)
        raise JobManagerLaunchError(
            "background manager could not be started "
            f"(job status {current.status})") from exc
    manager_birth = process_birth_identity(process.pid)
    deadline = time.monotonic() + float(ready_timeout)
    while time.monotonic() < deadline:
        try:
            if _ready_matches(
                    paths.ready, execution=execution, nonce=nonce,
                    manager_pid=process.pid, manager_birth=manager_birth):
                current = store.get_job(job_id)
                return LaunchResult(
                    job_id=job_id, status=current.status,
                    attempt_number=current.attempt_number, ready=True)
        except JobManagerCorruptError:
            # A prior/stale marker is not this nonce. A matching malformed
            # marker still cannot be accepted as ready.
            pass
        if process.poll() is not None:
            current = reconcile_job(store, job_id, fail_queued=True)
            raise JobManagerLaunchError(
                "background manager exited before its ready handshake "
                f"(job status {current.status})")
        time.sleep(0.05)

    try:
        process.terminate()
        process.wait(timeout=5)
    except (OSError, subprocess.TimeoutExpired):
        try:
            process.kill()
            process.wait(timeout=5)
        except (OSError, subprocess.TimeoutExpired):
            pass
    current = reconcile_job(store, job_id, fail_queued=True)
    raise JobManagerLaunchError(
        "background manager exceeded its ready-handshake deadline "
        f"(job status {current.status})")


def _reconstructed_attempt_report(
        execution: job_runtime.JobExecution,
        summary: job_runtime.JobSummary,
        runtime: RuntimeMetadata | None, *,
        cancel_requested_at: float | None,
        observed_at: float) -> attempt_reporting.AttemptReport:
    """Build an explicitly marked fallback after report loss/corruption."""
    candidates = [summary.updated_at, observed_at]
    if execution.attempt_number == 1:
        candidates.append(summary.created_at)
    if runtime is not None:
        candidates.append(runtime.updated_at)
    if cancel_requested_at is not None:
        candidates.append(cancel_requested_at)
    submitted_at = min(candidates)
    status = execution.status
    needs_worker = bool(
        runtime is not None and runtime.worker_pid is not None)
    needs_worker = needs_worker or status in {
        "running", "succeeded", "partial",
    }
    needs_manager = (
        runtime is not None or needs_worker
        or status in {"starting", "running", "interrupted"}
    )
    manager_started_at = submitted_at if needs_manager else None
    report = attempt_reporting.new_report(
        job_id=execution.job_id,
        attempt_number=execution.attempt_number,
        run_id=_attempt_run_id(execution),
        operation=execution.command,
        status="queued",
        submitted_at=submitted_at,
        observed_at=observed_at,
        manager_started_at=manager_started_at,
    )
    if needs_manager:
        report = attempt_reporting.advance(
            report, observed_at=observed_at, status="starting")
    if needs_worker:
        if report.status == "queued":
            report = attempt_reporting.advance(
                report,
                observed_at=observed_at,
                status="starting",
                manager_started_at=submitted_at,
            )
        report = attempt_reporting.advance(
            report,
            observed_at=observed_at,
            status="running",
            worker_started=True,
            worker_started_at=report.manager_started_at,
        )
    cancel_observed = status in {"cancel_requested", "cancelled"}
    if cancel_requested_at is not None:
        cancel_observed_at = None
        if cancel_observed:
            cancel_observed_at = max(
                cancel_requested_at,
                report.worker_started_at
                or report.manager_started_at
                or report.submitted_at,
            )
        report = attempt_reporting.advance(
            report,
            observed_at=observed_at,
            status=("cancel_requested" if cancel_observed
                    else report.status),
            trigger=("cancel" if cancel_observed else report.trigger),
            cancel_requested=True,
            cancel_requested_at=cancel_requested_at,
            cancel_observed=cancel_observed,
            cancel_observed_at=cancel_observed_at,
        )
    return report


def _align_attempt_report(
        report: attempt_reporting.AttemptReport,
        execution: job_runtime.JobExecution,
        runtime: RuntimeMetadata | None, *,
        cancel_requested_at: float | None,
        observed_at: float) -> attempt_reporting.AttemptReport:
    """Advance a valid snapshot to the authoritative active job state."""
    if report.status in job_runtime.TERMINAL_JOB_STATUSES:
        raise ValueError("terminal report conflicts with active job state")
    if report.cancel_requested and cancel_requested_at is None:
        raise ValueError("attempt report references a missing cancel marker")
    if (report.cancel_requested_at is not None
            and report.cancel_requested_at != cancel_requested_at):
        raise ValueError("attempt report cancel time conflicts with its marker")
    if cancel_requested_at is not None and not report.cancel_requested:
        report = attempt_reporting.advance(
            report,
            observed_at=observed_at,
            cancel_requested=True,
            cancel_requested_at=cancel_requested_at,
        )

    desired = execution.status
    if desired == "queued":
        if report.status != "queued":
            raise ValueError("attempt report is ahead of queued state")
        return report

    manager_started_at = report.manager_started_at or observed_at
    if report.status == "queued" and desired in {"starting", "running"}:
        report = attempt_reporting.advance(
            report,
            observed_at=observed_at,
            status="starting",
            manager_started_at=manager_started_at,
        )
    worker_expected = (
        runtime is not None and runtime.worker_pid is not None)
    if desired == "starting" and not worker_expected:
        if report.status != "starting":
            raise ValueError("attempt report conflicts with starting state")
        return report

    if desired == "running" or worker_expected:
        if report.status == "starting":
            worker_started_at = max(
                report.manager_started_at or report.submitted_at,
                min(observed_at, runtime.updated_at)
                if runtime is not None else observed_at,
            )
            report = attempt_reporting.advance(
                report,
                observed_at=observed_at,
                status="running",
                worker_started=True,
                worker_started_at=worker_started_at,
            )
        elif report.status == "cancel_requested" and not report.worker_started:
            worker_started_at = (
                report.manager_started_at or report.submitted_at)
            report = attempt_reporting.advance(
                report,
                observed_at=observed_at,
                worker_started=True,
                worker_started_at=worker_started_at,
            )
    if desired == "starting":
        return report
    if desired == "running":
        if report.status != "running":
            raise ValueError("attempt report conflicts with running state")
        return report

    if desired != "cancel_requested":
        raise ValueError("attempt report cannot align to this job state")
    if cancel_requested_at is None:
        raise ValueError("cancel-requested state lacks a marker")
    if report.status != "cancel_requested":
        cancel_observed_at = max(
            cancel_requested_at,
            report.worker_started_at
            or report.manager_started_at
            or report.submitted_at,
        )
        if report.recovery_started_at is not None:
            cancel_observed_at = report.recovery_started_at
        report = attempt_reporting.advance(
            report,
            observed_at=observed_at,
            status="cancel_requested",
            trigger=("cancel" if report.trigger == "normal"
                     else report.trigger),
            cancel_requested=True,
            cancel_requested_at=cancel_requested_at,
            cancel_observed=True,
            cancel_observed_at=cancel_observed_at,
        )
    return report


def _recovery_cancel_evidence(
        store: job_runtime.JobStore,
        execution: job_runtime.JobExecution) -> tuple[float | None, bool]:
    """Read cancellation evidence without making it a cleanup dependency."""
    try:
        return (
            store.cancel_requested_at(
                execution.job_id, execution.attempt_token),
            False,
        )
    except job_runtime.JobCorruptError:
        return None, True


def _merge_unobserved_cancel_marker(
        store: job_runtime.JobStore,
        execution: job_runtime.JobExecution,
        report: attempt_reporting.AttemptReport,
        ) -> tuple[attempt_reporting.AttemptReport, bool]:
    """Merge a newly durable request without claiming it was observed."""
    cancel_requested_at, corrupt = _recovery_cancel_evidence(
        store, execution)
    if corrupt:
        if not report.report_repaired or not report.timing_reconstructed:
            report = attempt_reporting.advance(
                report,
                observed_at=max(time.time(), report.updated_at),
                report_repaired=True,
                timing_reconstructed=True,
            )
        return report, True
    if cancel_requested_at is None:
        return report, False
    if report.cancel_requested_at is not None:
        if report.cancel_requested_at != cancel_requested_at:
            raise JobManagerCorruptError(
                "attempt report conflicts with its cancel marker")
        return report, False
    try:
        return (
            attempt_reporting.advance(
                report,
                observed_at=max(
                    time.time(), report.updated_at, cancel_requested_at),
                cancel_requested=True,
                cancel_requested_at=cancel_requested_at,
            ),
            False,
        )
    except ValueError as exc:
        raise JobManagerCorruptError(
            "late cancel marker conflicts with attempt report") from exc


def _begin_recovery_report(
        store: job_runtime.JobStore,
        execution: job_runtime.JobExecution,
        summary: job_runtime.JobSummary,
        paths: _AttemptPaths,
        runtime: RuntimeMetadata | None, *,
        cancel_requested_at: float | None,
        cancel_evidence_corrupt: bool,
        runtime_missing: bool,
        trigger: str,
        force_report_repaired: bool = False,
        ) -> attempt_reporting.AttemptReport:
    observed_at = max(
        time.time(), summary.updated_at,
        runtime.updated_at if runtime is not None else 0.0,
        cancel_requested_at or 0.0,
    )
    report_repaired = force_report_repaired or cancel_evidence_corrupt
    timing_reconstructed = cancel_evidence_corrupt
    try:
        report = _load_attempt_report(
            paths, execution=execution, missing_ok=True)
    except JobManagerCorruptError:
        report = None
        report_repaired = True
        timing_reconstructed = True
    if report is None:
        report_repaired = True
        timing_reconstructed = True
        report = _reconstructed_attempt_report(
            execution, summary, runtime,
            cancel_requested_at=cancel_requested_at,
            observed_at=observed_at,
        )
    else:
        observed_at = max(observed_at, report.updated_at)
        try:
            if execution.status in job_runtime.TERMINAL_JOB_STATUSES:
                if report.status in job_runtime.TERMINAL_JOB_STATUSES:
                    raise ValueError(
                        "terminal attempt report conflicts with job state")
                if (report.cancel_requested
                        and cancel_requested_at is None):
                    raise ValueError(
                        "attempt report references a missing cancel marker")
                if (report.cancel_requested_at is not None
                        and report.cancel_requested_at
                        != cancel_requested_at):
                    raise ValueError(
                        "attempt report cancel time conflicts with its marker")
                if (cancel_requested_at is not None
                        and not report.cancel_requested):
                    report = attempt_reporting.advance(
                        report,
                        observed_at=observed_at,
                        cancel_requested=True,
                        cancel_requested_at=cancel_requested_at,
                    )
                if (execution.status == "cancelled"
                        and report.status != "cancel_requested"):
                    if cancel_requested_at is None:
                        raise ValueError(
                            "cancelled attempt lacks a cancel marker")
                    cancel_observed_at = max(
                        cancel_requested_at,
                        report.worker_started_at
                        or report.manager_started_at
                        or report.submitted_at,
                    )
                    report = attempt_reporting.advance(
                        report,
                        observed_at=observed_at,
                        status="cancel_requested",
                        trigger=("cancel" if report.trigger == "normal"
                                 else report.trigger),
                        cancel_requested=True,
                        cancel_requested_at=cancel_requested_at,
                        cancel_observed=True,
                        cancel_observed_at=cancel_observed_at,
                    )
            else:
                report = _align_attempt_report(
                    report, execution, runtime,
                    cancel_requested_at=cancel_requested_at,
                    observed_at=observed_at,
                )
        except ValueError:
            report_repaired = True
            timing_reconstructed = True
            report = _reconstructed_attempt_report(
                execution, summary, runtime,
                cancel_requested_at=cancel_requested_at,
                observed_at=observed_at,
            )

    observed_at = max(observed_at, report.updated_at)

    if report.recovery_action == "none":
        report = attempt_reporting.advance(
            report,
            observed_at=observed_at,
            trigger=(trigger if report.trigger == "normal"
                     else report.trigger),
            finalized_by="recovery",
            recovery_started_at=observed_at,
            recovery_action="pending",
            runtime_missing=runtime_missing,
            report_repaired=report_repaired,
            timing_reconstructed=timing_reconstructed,
        )
    else:
        report = attempt_reporting.advance(
            report,
            observed_at=observed_at,
            runtime_missing=(report.runtime_missing or runtime_missing),
            report_repaired=(report.report_repaired or report_repaired),
            timing_reconstructed=(
                report.timing_reconstructed or timing_reconstructed),
        )
    try:
        _write_attempt_report(paths.attempt_report, report)
    except Exception:
        # Recovery safety and authoritative state terminalization take
        # precedence. The final publication is retried after cleanup.
        pass
    return report


def _recovery_terminal_reason(
        status: str, *, exit_code: int | None,
        cancel_observed: bool, runtime_missing: bool,
        worker_started: bool) -> str:
    if status in {"succeeded", "partial"}:
        return "completed"
    if status == "cancelled":
        return "cancelled"
    if status == "orphaned":
        return "cleanup_unconfirmed"
    if status == "interrupted":
        return "outcome_unconfirmed"
    if exit_code == 124:
        return "timeout"
    if cancel_observed:
        return "cancellation_failed"
    if runtime_missing and not worker_started:
        return "manager_start_missing"
    return "worker_failed"


def _finish_recovery_report(
        paths: _AttemptPaths,
        report: attempt_reporting.AttemptReport, *,
        status: str,
        cleanup_confirmed: bool,
        recovery_action: str,
        telemetry_status: str | None,
        exit_code: int | None) -> attempt_reporting.AttemptReport:
    finished_at = max(time.time(), report.updated_at)
    recovered_milestone_at = report.recovery_started_at or finished_at
    if status in {"succeeded", "partial"}:
        if report.status == "queued":
            report = attempt_reporting.advance(
                report,
                observed_at=finished_at,
                status="starting",
                manager_started_at=recovered_milestone_at,
            )
        if report.status == "starting":
            report = attempt_reporting.advance(
                report,
                observed_at=finished_at,
                status="running",
                worker_started=True,
                worker_started_at=recovered_milestone_at,
            )
    elif status == "cancelled" and report.status != "cancel_requested":
        if not report.cancel_observed:
            raise JobManagerCorruptError(
                "cancelled attempt lacks durable cancellation evidence")
        report = attempt_reporting.advance(
            report, observed_at=finished_at, status="cancel_requested")
    elif status == "interrupted" and report.status == "queued":
        report = attempt_reporting.advance(
            report,
            observed_at=finished_at,
            status="starting",
            manager_started_at=recovered_milestone_at,
        )

    reason = _recovery_terminal_reason(
        status,
        exit_code=exit_code,
        cancel_observed=report.cancel_observed,
        runtime_missing=report.runtime_missing,
        worker_started=report.worker_started,
    )
    trigger = report.trigger
    if reason == "timeout":
        trigger = "timeout"
    elif reason == "manager_start_missing" and trigger == "normal":
        trigger = "recovery"
    manager_error = report.manager_error or reason == "manager_start_missing"
    report = attempt_reporting.advance(
        report,
        observed_at=finished_at,
        status=status,
        trigger=trigger,
        cleanup_confirmed=cleanup_confirmed,
        cleanup_confirmed_at=(finished_at if cleanup_confirmed else None),
        finished_at=finished_at,
        reconciled=True,
        recovery_action=recovery_action,
        worker_telemetry_status=telemetry_status,
        terminal_reason=reason,
        exit_code=exit_code,
        manager_error=manager_error,
    )
    _write_attempt_report(paths.attempt_report, report)
    return report


def _repair_terminal_attempt_report(
        store: job_runtime.JobStore,
        execution: job_runtime.JobExecution, *,
        lease: job_runtime.JobLease) -> job_runtime.JobSummary:
    """Backfill a missing/incomplete terminal snapshot without changing state."""
    del lease  # The caller's active lease is the serialization boundary.
    summary = store.get_job(execution.job_id)
    if summary.status == "deleting":
        return summary
    paths = _attempt_paths(
        store, execution.job_id, execution.attempt_number, create=True)
    try:
        existing = _load_attempt_report(
            paths, execution=execution, missing_ok=True)
    except JobManagerCorruptError:
        existing = None
    cancel_requested_at, cancel_evidence_corrupt = (
        _recovery_cancel_evidence(store, execution))
    if (existing is not None and existing.status == summary.status
            and existing.finished_at is not None):
        if cancel_evidence_corrupt:
            return summary
        if existing.cancel_requested_at == cancel_requested_at:
            return summary
        if (existing.cancel_requested_at is None
                and cancel_requested_at is not None):
            try:
                repaired = attempt_reporting.repair_terminal_cancel_request(
                    existing,
                    cancel_requested_at=cancel_requested_at,
                    observed_at=max(time.time(), cancel_requested_at),
                )
            except ValueError as exc:
                raise JobManagerCorruptError(
                    "late cancel marker conflicts with terminal report") from exc
            _write_attempt_report(paths.attempt_report, repaired)
            return summary
        raise JobManagerCorruptError(
            "terminal attempt report conflicts with its cancel marker")
    prior_recovery = (
        existing is not None and existing.recovery_action != "none")

    runtime = None
    try:
        runtime_payload = _read_private_json(
            paths.runtime, missing_ok=True)
        if runtime_payload is not None:
            runtime = _load_runtime(paths.runtime, execution=execution)
    except JobManagerCorruptError:
        runtime = None
    runtime_missing = runtime is None
    exit_code = runtime.exit_code if runtime is not None else (
        130 if summary.status == "cancelled" else None)
    trigger = "normal" if (
        summary.status == "failed" and exit_code == 124) else "recovery"
    report = _begin_recovery_report(
        store,
        execution,
        summary,
        paths,
        runtime,
        cancel_requested_at=cancel_requested_at,
        cancel_evidence_corrupt=cancel_evidence_corrupt,
        runtime_missing=runtime_missing,
        trigger=trigger,
        force_report_repaired=True,
    )
    telemetry_status = None
    try:
        telemetry_status = _telemetry_status(
            paths,
            operation=execution.command,
            run_id=(runtime.run_id if runtime is not None
                    else _attempt_run_id(execution)),
        )
    except JobManagerCorruptError:
        telemetry_status = None
    if (telemetry_status in {"succeeded", "partial"}
            and telemetry_status != summary.status):
        telemetry_status = None
    cleanup_confirmed = (
        runtime.cleanup_confirmed
        if runtime is not None and runtime.cleanup_confirmed is not None
        else summary.status != "orphaned"
    )
    if report.recovery_action not in {"none", "pending"}:
        recovery_action = report.recovery_action
    elif prior_recovery or (
            runtime is not None and runtime.phase == "reconciled"):
        if not cleanup_confirmed:
            recovery_action = "cleanup_unconfirmed"
        elif not report.worker_started:
            recovery_action = "unstarted_terminalized"
        else:
            recovery_action = "confirmed_gone"
    else:
        recovery_action = "not_required"
    _finish_recovery_report(
        paths,
        report,
        status=summary.status,
        cleanup_confirmed=cleanup_confirmed,
        recovery_action=recovery_action,
        telemetry_status=telemetry_status,
        exit_code=exit_code,
    )
    return summary


def _terminal_report_repair_needed(
        store: job_runtime.JobStore,
        summary: job_runtime.JobSummary) -> bool:
    if summary.status == "deleting":
        return False
    try:
        execution = store.load_execution(summary.job_id)
        paths = _attempt_paths(
            store, summary.job_id, execution.attempt_number, create=False)
        report = _load_attempt_report(
            paths, execution=execution, missing_ok=True)
    except (JobManagerCorruptError, job_runtime.JobRuntimeError):
        return True
    cancel_requested_at, cancel_evidence_corrupt = (
        _recovery_cancel_evidence(store, execution))
    if cancel_evidence_corrupt:
        return report is None or report.status != summary.status
    return (report is None or report.status != summary.status
            or report.cancel_requested_at != cancel_requested_at)


def _reconcile_job_with_lease(
        store: job_runtime.JobStore, job_id: str, *,
        lease: job_runtime.JobLease,
        fail_queued: bool) -> job_runtime.JobSummary:
    execution = store.load_execution(job_id, lease=lease)
    if execution.status in job_runtime.TERMINAL_JOB_STATUSES:
        return _repair_terminal_attempt_report(
            store, execution, lease=lease)
    summary = store.get_job(job_id)
    paths = _attempt_paths(
        store, job_id, execution.attempt_number, create=True)
    try:
        runtime_payload = _read_private_json(
            paths.runtime, missing_ok=True)
    except JobManagerCorruptError:
        runtime_payload = None
    runtime = None
    if runtime_payload is not None:
        try:
            runtime = _load_runtime(paths.runtime, execution=execution)
        except JobManagerCorruptError:
            runtime = None
    runtime_missing = runtime is None
    cancel_requested_at, cancel_evidence_corrupt = (
        _recovery_cancel_evidence(store, execution))
    if (execution.status == "queued" and runtime is None
            and cancel_requested_at is None and not fail_queued):
        # The caller observed a manager-owned report without a runtime, or a
        # corrupt runtime. Either is evidence that startup was attempted; an
        # untouched queued job is filtered before this lease is acquired.
        fail_queued = True

    recovery_trigger = (
        "cancel" if (execution.status == "cancel_requested"
                     and not cancel_evidence_corrupt) else "recovery")
    report = _begin_recovery_report(
        store, execution, summary, paths, runtime,
        cancel_requested_at=cancel_requested_at,
        cancel_evidence_corrupt=cancel_evidence_corrupt,
        runtime_missing=runtime_missing,
        trigger=recovery_trigger,
    )

    cleanup_confirmed = False
    recovery_action = "cleanup_unconfirmed"
    telemetry_status = None
    if runtime is None:
        if execution.status == "queued":
            cleanup_confirmed = cancel_requested_at is not None or fail_queued
            target = (
                "cancelled" if cancel_requested_at is not None else "failed")
            recovery_action = "unstarted_terminalized"
        else:
            target = "orphaned"
    elif execution.status == "queued":
        cleanup_confirmed = runtime.worker_pid is None
        if cleanup_confirmed:
            target = (
                "cancelled" if cancel_requested_at is not None else "failed")
            recovery_action = "unstarted_terminalized"
        else:
            target = "orphaned"
            recovery_action = "refused_identity"
    else:
        if runtime.worker_pid is None:
            cleanup_confirmed = execution.status in {
                "starting", "cancel_requested"}
            recovery_action = (
                "unstarted_terminalized" if cleanup_confirmed
                else "cleanup_unconfirmed")
        else:
            probe = probe_process_identity(
                runtime.worker_pid, runtime.worker_birth)
            if probe == "match":
                cleanup_confirmed = _terminate_recovered_worker(
                    runtime.worker_pid, runtime.worker_birth)
                recovery_action = (
                    "terminated_exact_worker" if cleanup_confirmed
                    else "cleanup_unconfirmed")
            elif probe == "gone":
                cleanup_confirmed = _confirm_worker_tree_gone(
                    runtime.worker_pid, runtime.worker_birth)
                recovery_action = (
                    "confirmed_gone" if cleanup_confirmed
                    else "cleanup_unconfirmed")
            else:
                recovery_action = "refused_identity"
        try:
            telemetry_status = _telemetry_status(
                paths, operation=execution.command,
                run_id=runtime.run_id)
        except JobManagerCorruptError:
            telemetry_status = None
        if not cleanup_confirmed:
            target = "orphaned"
        elif telemetry_status in {"succeeded", "partial", "failed"}:
            target = telemetry_status
        elif (telemetry_status == "cancelled"
              and execution.status == "cancel_requested"
              and not cancel_evidence_corrupt):
            target = "cancelled"
        else:
            target = "interrupted"

    report, late_cancel_corrupt = _merge_unobserved_cancel_marker(
        store, execution, report)
    cancel_evidence_corrupt = (
        cancel_evidence_corrupt or late_cancel_corrupt)
    if report.recovery_action not in {"none", "pending"}:
        recovery_action = report.recovery_action
    elif (cleanup_confirmed and recovery_action in {
            "unstarted_terminalized", "confirmed_gone",
            "terminated_exact_worker"}):
        report = attempt_reporting.advance(
            report,
            observed_at=max(time.time(), report.updated_at),
            recovery_action=recovery_action,
        )
    try:
        _write_attempt_report(paths.attempt_report, report)
    except Exception:
        # The cleanup decision remains in memory for the final publication;
        # authoritative terminalization must not be blocked by observability.
        pass

    transition_revision = execution.revision
    if (execution.status == "queued"
            and cancel_requested_at is not None):
        requested = store.transition_job(
            job_id, "cancel_requested",
            attempt_token=execution.attempt_token,
            expected_revision=transition_revision, lease=lease)
        transition_revision = requested.revision
        execution = store.load_execution(job_id, lease=lease)
        aligned_at = max(time.time(), report.updated_at, cancel_requested_at)
        report = _align_attempt_report(
            report, execution, runtime,
            cancel_requested_at=cancel_requested_at,
            observed_at=aligned_at,
        )
        try:
            _write_attempt_report(paths.attempt_report, report)
        except Exception:
            pass
    if (execution.status == "starting"
            and target in {"succeeded", "partial"}):
        # A worker can commit telemetry after its PID was recorded but before
        # the manager durably published ``running``. Preserve the legal
        # lifecycle while honoring that committed terminal result.
        running = store.transition_job(
            job_id, "running", attempt_token=execution.attempt_token,
            expected_revision=transition_revision, lease=lease)
        transition_revision = running.revision
        execution = store.load_execution(job_id, lease=lease)
        report = _align_attempt_report(
            report, execution, runtime,
            cancel_requested_at=cancel_requested_at,
            observed_at=max(time.time(), report.updated_at),
        )
    current = store.transition_job(
        job_id, target, attempt_token=execution.attempt_token,
        expected_revision=transition_revision, lease=lease)
    exit_code = runtime.exit_code if runtime is not None else (
        130 if current.status == "cancelled" else None)
    if runtime is not None:
        now = time.time()
        reconciled = replace(
            runtime, phase="reconciled", job_status=current.status,
            manager_pid=os.getpid(),
            manager_birth=process_birth_identity(os.getpid()),
            heartbeat_at=now, cleanup_confirmed=cleanup_confirmed,
            updated_at=now)
        _write_runtime(paths.runtime, reconciled)
    report, late_cancel_corrupt = _merge_unobserved_cancel_marker(
        store, execution, report)
    cancel_evidence_corrupt = (
        cancel_evidence_corrupt or late_cancel_corrupt)
    final_report = _finish_recovery_report(
        paths,
        report,
        status=current.status,
        cleanup_confirmed=cleanup_confirmed,
        recovery_action=recovery_action,
        telemetry_status=telemetry_status,
        exit_code=exit_code,
    )
    latest_cancel_requested_at, _late_cancel_corrupt = (
        _recovery_cancel_evidence(store, execution))
    if (latest_cancel_requested_at is not None
            and final_report.cancel_requested_at is None):
        try:
            final_report = attempt_reporting.repair_terminal_cancel_request(
                final_report,
                cancel_requested_at=latest_cancel_requested_at,
                observed_at=max(time.time(), latest_cancel_requested_at),
            )
        except ValueError as exc:
            raise JobManagerCorruptError(
                "late cancel marker conflicts with terminal report") from exc
        _write_attempt_report(paths.attempt_report, final_report)
    return current


def _queued_reconciliation_needed(
        store: job_runtime.JobStore, summary: job_runtime.JobSummary, *,
        fail_queued: bool) -> bool:
    """Avoid competing with startup for an untouched queued attempt."""
    if summary.status != "queued" or fail_queued:
        return True
    execution = store.load_execution(summary.job_id)
    if execution.status != "queued":
        return True
    try:
        if store.is_cancel_requested(
                summary.job_id, execution.attempt_token):
            return True
    except job_runtime.JobCorruptError:
        return True
    paths = _attempt_paths(
        store, summary.job_id, execution.attempt_number, create=False)
    try:
        if _read_private_json(paths.runtime, missing_ok=True) is not None:
            return True
    except JobManagerCorruptError:
        return True
    try:
        os.lstat(paths.attempt_report)
    except FileNotFoundError:
        return False
    except OSError:
        return True
    return True


def reconcile_job(store: job_runtime.JobStore,
                  job_id: str, *,
                  fail_queued: bool = False,
                  lease: job_runtime.JobLease | None = None
                  ) -> job_runtime.JobSummary:
    """Conservatively reconcile a managerless nonterminal attempt.

    Live workers are terminated only when both PID and birth identity match.
    Mismatches and unverifiable identities are never signalled. No state is
    automatically resumed.
    """
    owns_lease = lease is None
    try:
        if owns_lease:
            with store.store_lease():
                initial = store.get_job(job_id)
                if (initial.terminal
                        and not _terminal_report_repair_needed(store, initial)):
                    return initial
                if (not initial.terminal
                        and not _queued_reconciliation_needed(
                            store, initial, fail_queued=fail_queued)):
                    return initial
                try:
                    lease = store.lease(job_id, timeout=0).acquire()
                except job_runtime.JobBusyError:
                    return initial
        assert lease is not None
        return _reconcile_job_with_lease(
            store, job_id, lease=lease, fail_queued=fail_queued)
    finally:
        if owns_lease and lease is not None and lease.active:
            lease.release()


def reconcile_all_jobs(
        store: job_runtime.JobStore, *,
        fail_queued: bool = False) -> list[job_runtime.JobSummary]:
    """Snapshot and reconcile managerless attempts.

    ``fail_queued`` is reserved for an exclusive service startup/reconciliation
    boundary: it converts a crash-left, managerless queued attempt to a
    resumable failure instead of auto-launching external work.
    """
    pending: list[tuple[int, str, job_runtime.JobLease]] = []
    try:
        with store.store_lease() as root_lease:
            results = store.list_jobs(lease=root_lease)
            for index, summary in enumerate(results):
                if (summary.terminal
                        and not _terminal_report_repair_needed(store, summary)):
                    continue
                if (not summary.terminal
                        and not _queued_reconciliation_needed(
                            store, summary, fail_queued=fail_queued)):
                    continue
                try:
                    job_lease = store.lease(
                        summary.job_id, timeout=0).acquire()
                except job_runtime.JobBusyError:
                    continue
                pending.append((index, summary.job_id, job_lease))
        for index, job_id, job_lease in pending:
            try:
                results[index] = reconcile_job(
                    store, job_id, lease=job_lease,
                    fail_queued=fail_queued)
            finally:
                job_lease.release()
    finally:
        for _index, _job_id, job_lease in pending:
            if job_lease.active:
                job_lease.release()
    return results


_DEFAULT_SERVICE_JOB_COORDINATION_BINDING = ServiceJobCoordinationBinding(
    launch_detached=launch_detached,
    reconcile_job=reconcile_job,
    corrupt_error_type=JobManagerCorruptError,
)


def default_service_job_coordination_binding(
        ) -> ServiceJobCoordinationBinding:
    """Return the immutable production service coordination generation."""
    return _DEFAULT_SERVICE_JOB_COORDINATION_BINDING


def _exit_for_status(status: str) -> int:
    if status in {"succeeded", "partial"}:
        return 0
    if status == "cancelled":
        return 130
    if status == "interrupted":
        return 2
    return 1


def main(argv: Sequence[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="Durable RAG job manager")
    subparsers = parser.add_subparsers(dest="action", required=True)
    manage = subparsers.add_parser(
        "_manage", help=argparse.SUPPRESS)
    manage.add_argument("--root", type=Path, required=True)
    manage.add_argument("--job-id", required=True)
    manage.add_argument("--script", type=Path, required=True)
    reconcile = subparsers.add_parser("reconcile")
    reconcile.add_argument("--root", type=Path, required=True)
    reconcile.add_argument("--job-id", required=True)
    args = parser.parse_args(argv)

    # These values are capabilities for the worker only. A detached manager is
    # deliberately launched without them and removes accidental inherited
    # values before it constructs the exact child environment.
    for name in (
            JOB_ROOT_ENV, JOB_ID_ENV, JOB_ATTEMPT_TOKEN_ENV,
            OUTPUT_ROOT_ENV):
        os.environ.pop(name, None)
    store = job_runtime.JobStore(args.root)
    if args.action == "_manage":
        nonce = os.environ.pop(_READY_NONCE_ENV, None)
        result = run_job(
            store, args.job_id, script_path=args.script,
            ready_nonce=nonce)
        return _exit_for_status(result.status)
    summary = reconcile_job(store, args.job_id)
    print(json.dumps(summary.as_dict(), sort_keys=True))
    return _exit_for_status(summary.status)


if __name__ == "__main__":
    raise SystemExit(main())


__all__ = [
    "JOB_ATTEMPT_TOKEN_ENV",
    "JOB_ID_ENV",
    "JOB_ROOT_ENV",
    "OUTPUT_ROOT_ENV",
    "JobManagerCorruptError",
    "JobManagerError",
    "JobManagerLaunchError",
    "JobManagerValidationError",
    "LaunchResult",
    "MANAGER_SCRIPT_PATH",
    "MANAGER_SCHEMA_VERSION",
    "ManagerResult",
    "RuntimeMetadata",
    "ServiceJobCoordinationBinding",
    "default_service_job_coordination_binding",
    "launch_detached",
    "main",
    "probe_process_identity",
    "process_birth_identity",
    "reconcile_all_jobs",
    "reconcile_job",
    "run_job",
]
