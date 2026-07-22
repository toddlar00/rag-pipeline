"""Detached execution and conservative recovery for durable RAG jobs.

The manager is intentionally a thin adapter over :mod:`job_runtime` and
``rag._run_cli_with_deadline``.  It owns no pipeline logic: one manager holds
the job lease, publishes private attempt metadata, and maps the supervised
worker plus run telemetry into the durable job state machine.
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

import job_runtime
import rag
import run_telemetry
import storage_policy


MANAGER_SCHEMA_VERSION = 1
JOB_ROOT_ENV = job_runtime.JOB_ROOT_ENV
JOB_ID_ENV = job_runtime.JOB_ID_ENV
JOB_ATTEMPT_TOKEN_ENV = job_runtime.JOB_ATTEMPT_TOKEN_ENV

_READY_NONCE_ENV = "RAG_PIPELINE_MANAGER_READY_NONCE"
_ATTEMPTS_DIRECTORY = "attempts"
_RUNTIME_NAME = "runtime.json"
_READY_NAME = "ready.json"
_LOG_NAME = "worker.log"
_EVENTS_NAME = "run.events.jsonl"
_REPORT_NAME = "run.report.json"
_MAX_MANAGER_JSON_BYTES = 64 * 1024
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


class JobManagerError(Exception):
    """Base class for detached manager failures."""


class JobManagerValidationError(JobManagerError, ValueError):
    """Raised before unsafe manager input is used."""


class JobManagerCorruptError(JobManagerError):
    """Raised when private attempt metadata cannot be trusted."""


@dataclass(frozen=True, slots=True)
class ManagerResult:
    """Redacted terminal manager result safe for public adapters."""

    job_id: str
    status: str
    attempt_number: int
    exit_code: int | None
    cleanup_confirmed: bool
    reason: str

    def as_dict(self) -> dict:
        return {
            "schema_version": MANAGER_SCHEMA_VERSION,
            "job_id": self.job_id,
            "status": self.status,
            "attempt_number": self.attempt_number,
            "exit_code": self.exit_code,
            "cleanup_confirmed": self.cleanup_confirmed,
            "reason": self.reason,
        }


@dataclass(frozen=True, slots=True)
class LaunchResult:
    """Redacted detached-launch handshake result."""

    job_id: str
    status: str
    attempt_number: int
    ready: bool

    def as_dict(self) -> dict:
        return {
            "schema_version": MANAGER_SCHEMA_VERSION,
            "job_id": self.job_id,
            "status": self.status,
            "attempt_number": self.attempt_number,
            "ready": self.ready,
        }


@dataclass(frozen=True, slots=True)
class _AttemptPaths:
    directory: Path = field(repr=False)
    runtime: Path = field(repr=False)
    ready: Path = field(repr=False)
    log: Path = field(repr=False)
    events: Path = field(repr=False)
    report: Path = field(repr=False)


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


def _read_private_json(path: Path, *, missing_ok: bool = False) -> dict | None:
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
                or before.st_size > _MAX_MANAGER_JSON_BYTES):
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
            or not math.isfinite(float(value)) or float(value) < 0):
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


def process_birth_identity(pid: int) -> str | None:
    """Return an OS process birth identity where the platform exposes one."""
    if isinstance(pid, bool) or not isinstance(pid, int) or pid <= 0:
        raise JobManagerValidationError("PID must be a positive integer")
    if os.name == "nt":
        from ctypes import wintypes

        kernel32 = ctypes.WinDLL("kernel32", use_last_error=True)
        open_process = kernel32.OpenProcess
        open_process.argtypes = [wintypes.DWORD, wintypes.BOOL, wintypes.DWORD]
        open_process.restype = wintypes.HANDLE
        get_times = kernel32.GetProcessTimes
        get_times.argtypes = [
            wintypes.HANDLE,
            ctypes.POINTER(wintypes.FILETIME),
            ctypes.POINTER(wintypes.FILETIME),
            ctypes.POINTER(wintypes.FILETIME),
            ctypes.POINTER(wintypes.FILETIME),
        ]
        get_times.restype = wintypes.BOOL
        close_handle = kernel32.CloseHandle
        close_handle.argtypes = [wintypes.HANDLE]
        close_handle.restype = wintypes.BOOL
        handle = open_process(0x1000, False, pid)
        if not handle:
            return None
        try:
            creation = wintypes.FILETIME()
            exit_time = wintypes.FILETIME()
            kernel_time = wintypes.FILETIME()
            user_time = wintypes.FILETIME()
            if not get_times(
                    handle, ctypes.byref(creation), ctypes.byref(exit_time),
                    ctypes.byref(kernel_time), ctypes.byref(user_time)):
                return None
            value = ((int(creation.dwHighDateTime) << 32)
                     | int(creation.dwLowDateTime))
            return f"windows:{value}"
        finally:
            close_handle(handle)

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
        from ctypes import wintypes

        kernel32 = ctypes.WinDLL("kernel32", use_last_error=True)
        open_process = kernel32.OpenProcess
        open_process.argtypes = [wintypes.DWORD, wintypes.BOOL, wintypes.DWORD]
        open_process.restype = wintypes.HANDLE
        get_exit_code = kernel32.GetExitCodeProcess
        get_exit_code.argtypes = [wintypes.HANDLE, ctypes.POINTER(wintypes.DWORD)]
        get_exit_code.restype = wintypes.BOOL
        close_handle = kernel32.CloseHandle
        close_handle.argtypes = [wintypes.HANDLE]
        close_handle.restype = wintypes.BOOL
        handle = open_process(0x1000, False, pid)
        if not handle:
            return False
        try:
            exit_code = wintypes.DWORD()
            if not get_exit_code(handle, ctypes.byref(exit_code)):
                return True
            return exit_code.value == 259  # STILL_ACTIVE
        finally:
            close_handle(handle)
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
    if probe_process_identity(pid, birth) != "match":
        return False
    if os.name == "nt":
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
            if probe_process_identity(pid, birth) != "match":
                return completed.returncode == 0
            time.sleep(0.05)
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


def _operation_timeout(execution: job_runtime.JobExecution) -> float:
    if execution.timeout_seconds is not None:
        return execution.timeout_seconds
    return float(rag.DEFAULT_OPERATION_TIMEOUTS.get(
        execution.command, _GENERIC_BACKGROUND_TIMEOUT))


def _report_status(path: Path, *, operation: str,
                   run_id: str) -> str | None:
    payload = _read_private_json(path, missing_ok=True)
    if payload is None:
        return None
    if (type(payload.get("schema_version")) is not int
            or payload.get("schema_version") !=
            run_telemetry.REPORT_SCHEMA_VERSION
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
    event_status = None
    try:
        recovered = run_telemetry.RunTelemetry.recover(
            operation, run_id=run_id, events_path=paths.events,
            report_path=paths.report)
        if recovered.finished:
            event_status = recovered.report_payload()["status"]
    except (OSError, UnicodeError, ValueError):
        event_status = None
    report_status = _report_status(
        paths.report, operation=operation, run_id=run_id)
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
        return "interrupted"
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
    if status == "interrupted":
        return "cleanup_unconfirmed"
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
    script_path = Path(rag.__file__) if script_path is None else Path(script_path)
    supervisor = rag._run_cli_with_deadline if supervisor is None else supervisor
    with store.lease(job_id, timeout=0) as lease:
        execution = store.load_execution(job_id, lease=lease)
        if execution.status != "queued":
            raise job_runtime.JobStateError(
                "only a queued job can start a manager attempt")
        paths = _attempt_paths(
            store, job_id, execution.attempt_number, create=True)
        run_id = f"{job_id}.a{execution.attempt_number}"
        now = time.time()
        manager_pid = os.getpid()
        manager_birth = process_birth_identity(manager_pid)
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

        # Cancellation requested before launch never creates a worker.
        if store.is_cancel_requested(job_id, execution.attempt_token):
            current = store.transition_job(
                job_id, "cancel_requested",
                attempt_token=execution.attempt_token,
                expected_revision=current.revision, lease=lease)
            _write_ready(
                paths, execution=execution, nonce=ready_nonce,
                manager_pid=manager_pid, manager_birth=manager_birth)
            current = store.transition_job(
                job_id, "cancelled", attempt_token=execution.attempt_token,
                expected_revision=current.revision, lease=lease)
            runtime = replace(
                runtime, phase="terminal", job_status=current.status,
                cleanup_confirmed=True, exit_code=130,
                heartbeat_at=time.time(), updated_at=time.time())
            _write_runtime(paths.runtime, runtime)
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
        active_log_handle = None

        def child_started(process) -> None:
            nonlocal worker, worker_birth, runtime, current
            worker = process
            worker_birth = process_birth_identity(int(process.pid))
            runtime = replace(
                runtime, worker_pid=int(process.pid),
                worker_birth=worker_birth, heartbeat_at=time.time(),
                updated_at=time.time())
            _write_runtime(paths.runtime, runtime)
            current = store.transition_job(
                job_id, "running", attempt_token=execution.attempt_token,
                expected_revision=current.revision, lease=lease)
            runtime = replace(
                runtime, phase="running", job_status=current.status,
                updated_at=time.time())
            _write_runtime(paths.runtime, runtime)
            _write_ready(
                paths, execution=execution, nonce=ready_nonce,
                manager_pid=manager_pid, manager_birth=manager_birth)

        def cancellation_requested() -> bool:
            nonlocal cancel_observed, current, runtime
            requested = store.is_cancel_requested(
                job_id, execution.attempt_token)
            if requested and not cancel_observed:
                cancel_observed = True
                current = store.transition_job(
                    job_id, "cancel_requested",
                    attempt_token=execution.attempt_token,
                    expected_revision=current.revision, lease=lease)
                runtime = replace(
                    runtime, job_status=current.status,
                    heartbeat_at=time.time(), updated_at=time.time())
                _write_runtime(paths.runtime, runtime)
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
            _READY_NONCE_ENV: None,
        }
        try:
            with storage_policy.open_private_append(paths.log) as log_handle:
                active_log_handle = log_handle
                exit_code = int(supervisor(
                    script_path, worker_argv,
                    operation=execution.command,
                    timeout=_operation_timeout(execution),
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
        except BaseException:
            manager_error = True
        finally:
            active_log_handle = None
        try:
            _cap_closed_worker_log(paths.log)
        except BaseException:
            manager_error = True

        worker_pid = int(worker.pid) if worker is not None else None
        cleanup_confirmed = _confirm_worker_tree_gone(
            worker_pid, worker_birth)
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
        finished = time.time()
        runtime = replace(
            runtime, phase="terminal", job_status=current.status,
            heartbeat_at=finished, cleanup_confirmed=cleanup_confirmed,
            exit_code=exit_code, updated_at=finished)
        _write_runtime(paths.runtime, runtime)
        return ManagerResult(
            job_id=job_id,
            status=current.status,
            attempt_number=execution.attempt_number,
            exit_code=exit_code,
            cleanup_confirmed=cleanup_confirmed,
            reason=_result_reason(
                current.status, exit_code, cancel_observed),
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
    script_path = Path(rag.__file__) if script_path is None else Path(script_path)
    paths = _attempt_paths(
        store, job_id, execution.attempt_number, create=False)
    nonce = uuid4().hex
    environment = os.environ.copy()
    for name in (JOB_ROOT_ENV, JOB_ID_ENV, JOB_ATTEMPT_TOKEN_ENV):
        environment.pop(name, None)
    environment[_READY_NONCE_ENV] = nonce
    command = [
        sys.executable, "-u", str(Path(__file__).resolve()), "_manage",
        "--root", str(store.root), "--job-id", job_id,
        "--script", str(script_path.resolve()),
    ]
    options = {
        "stdin": subprocess.DEVNULL,
        "stdout": subprocess.DEVNULL,
        "stderr": subprocess.DEVNULL,
        "env": environment,
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
    process = subprocess.Popen(command, **options)
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
            current = store.get_job(job_id)
            return LaunchResult(
                job_id=job_id, status=current.status,
                attempt_number=current.attempt_number, ready=False)
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
    current = reconcile_job(store, job_id)
    return LaunchResult(
        job_id=job_id, status=current.status,
        attempt_number=current.attempt_number, ready=False)


def reconcile_job(store: job_runtime.JobStore,
                  job_id: str) -> job_runtime.JobSummary:
    """Conservatively reconcile a managerless nonterminal attempt.

    Live workers are terminated only when both PID and birth identity match.
    Mismatches and unverifiable identities are never signalled. No state is
    automatically resumed.
    """
    initial = store.get_job(job_id)
    if initial.terminal:
        return initial
    try:
        lease = store.lease(job_id, timeout=0).acquire()
    except job_runtime.JobBusyError:
        return initial
    try:
        execution = store.load_execution(job_id, lease=lease)
        paths = _attempt_paths(
            store, job_id, execution.attempt_number, create=False)
        runtime_payload = _read_private_json(
            paths.runtime, missing_ok=True)
        if runtime_payload is None:
            if execution.status == "queued":
                return store.get_job(job_id)
            target = "interrupted"
            return store.transition_job(
                job_id, target, attempt_token=execution.attempt_token,
                expected_revision=execution.revision, lease=lease)
        runtime = _load_runtime(paths.runtime, execution=execution)
        if execution.status == "queued":
            target = "failed"
            cleanup_confirmed = runtime.worker_pid is None
        else:
            cleanup_confirmed = False
            if runtime.worker_pid is None:
                cleanup_confirmed = True
            else:
                probe = probe_process_identity(
                    runtime.worker_pid, runtime.worker_birth)
                if probe == "match":
                    cleanup_confirmed = _terminate_recovered_worker(
                        runtime.worker_pid, runtime.worker_birth)
                elif probe == "gone":
                    cleanup_confirmed = _confirm_worker_tree_gone(
                        runtime.worker_pid, runtime.worker_birth)
                # mismatch/unverifiable deliberately refuse signalling.
            try:
                observed = _telemetry_status(
                    paths, operation=execution.command,
                    run_id=runtime.run_id)
            except JobManagerCorruptError:
                observed = None
            if cleanup_confirmed and observed in {
                    "succeeded", "partial", "failed"}:
                target = observed
            elif (cleanup_confirmed and observed == "cancelled"
                  and execution.status == "cancel_requested"):
                target = "cancelled"
            else:
                target = "interrupted"
        transition_revision = execution.revision
        if (execution.status == "starting"
                and target in {"succeeded", "partial"}):
            # A worker can commit telemetry after its PID was recorded but
            # before the manager durably published ``running``. Preserve the
            # legal lifecycle while honoring that committed terminal result.
            running = store.transition_job(
                job_id, "running", attempt_token=execution.attempt_token,
                expected_revision=transition_revision, lease=lease)
            transition_revision = running.revision
        current = store.transition_job(
            job_id, target, attempt_token=execution.attempt_token,
            expected_revision=transition_revision, lease=lease)
        now = time.time()
        reconciled = replace(
            runtime, phase="reconciled", job_status=current.status,
            manager_pid=os.getpid(),
            manager_birth=process_birth_identity(os.getpid()),
            heartbeat_at=now, cleanup_confirmed=cleanup_confirmed,
            updated_at=now)
        _write_runtime(paths.runtime, reconciled)
        return current
    finally:
        lease.release()


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
    for name in (JOB_ROOT_ENV, JOB_ID_ENV, JOB_ATTEMPT_TOKEN_ENV):
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
    "JobManagerCorruptError",
    "JobManagerError",
    "JobManagerValidationError",
    "LaunchResult",
    "MANAGER_SCHEMA_VERSION",
    "ManagerResult",
    "RuntimeMetadata",
    "launch_detached",
    "main",
    "probe_process_identity",
    "process_birth_identity",
    "reconcile_job",
    "run_job",
]
