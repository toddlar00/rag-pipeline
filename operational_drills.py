"""Disposable, redacted operational recovery drills.

The drills exercise two failure modes that ordinary unit tests cannot prove:
a real hard-killed telemetry writer and transient Windows/synced-folder atomic
replacement interference. Reports contain only bounded identifiers, booleans,
counts, timestamps, and durations; paths, process identities, exceptions, and
corpus/model content are deliberately excluded.
"""

from __future__ import annotations

import json
import math
import os
import re
import subprocess
import sys
import time
from dataclasses import dataclass
from pathlib import Path
from uuid import uuid4

import process_supervision
import run_telemetry
import storage_policy


SCHEMA_VERSION = 1
KIND = "operational_recovery_drill"
REPORT_NAME = "operational-drill.report.json"
MAX_REPORT_BYTES = 64 * 1024
MAX_TIMESTAMP = 253_402_300_799.0
_RUN_ID = re.compile(r"^[0-9a-f]{32}$")
_REPORT_FIELDS = frozenset({
    "schema_version", "kind", "run_id", "status", "started_at",
    "finished_at", "duration_ms", "hard_kill", "synced_publication",
})
_HARD_KILL_FIELDS = frozenset({
    "passed", "worker_ready", "cleanup_confirmed", "child_exit_nonzero",
    "terminal_status", "source_event_count", "active_stages_closed",
    "kill_confirmation_ms", "telemetry_recovery_ms",
})
_SYNCED_FIELDS = frozenset({
    "passed", "transient_failures_requested", "replace_attempts",
    "writer_calls", "target_verified", "staging_clean", "publication_ms",
})
_PRIVATE_CANARY = "OPERATIONAL_DRILL_PRIVATE_CANARY"
_CHILD_ENVIRONMENT_ALLOWLIST = frozenset({
    "COMSPEC", "DYLD_LIBRARY_PATH", "LANG", "LC_ALL", "LD_LIBRARY_PATH",
    "PATH", "PATHEXT", "PYTHONIOENCODING", "PYTHONUTF8", "SYSTEMROOT",
    "TEMP", "TMP", "TMPDIR", "WINDIR",
})
def _exact_fields(payload: object, expected: frozenset[str], *,
                  label: str) -> dict:
    if not isinstance(payload, dict) or set(payload) != expected:
        raise ValueError(f"operational drill {label} fields are invalid")
    return payload


def _nonnegative_int(value: object, *, label: str) -> int:
    if type(value) is not int or value < 0:
        raise ValueError(f"operational drill {label} is invalid")
    return value


def _duration(value: object, *, label: str,
              optional: bool = False) -> float | None:
    if value is None and optional:
        return None
    if (isinstance(value, bool) or not isinstance(value, (int, float))
            or not math.isfinite(float(value)) or float(value) < 0):
        raise ValueError(f"operational drill {label} is invalid")
    return round(float(value), 3)


def _timestamp(value: object, *, label: str) -> float:
    observed = _duration(value, label=label)
    assert observed is not None
    if observed > MAX_TIMESTAMP:
        raise ValueError(f"operational drill {label} is invalid")
    return observed


def _bool(value: object, *, label: str) -> bool:
    if type(value) is not bool:
        raise ValueError(f"operational drill {label} is invalid")
    return value


def _monotonic(clock) -> float:
    value = clock()
    if (isinstance(value, bool) or not isinstance(value, (int, float))
            or not math.isfinite(float(value))):
        raise ValueError("operational drill monotonic clock is invalid")
    return float(value)


@dataclass(frozen=True, slots=True)
class HardKillResult:
    passed: bool
    worker_ready: bool
    cleanup_confirmed: bool
    child_exit_nonzero: bool
    terminal_status: str | None
    source_event_count: int
    active_stages_closed: int
    kill_confirmation_ms: float | None
    telemetry_recovery_ms: float | None

    def __post_init__(self) -> None:
        for name in (
                "passed", "worker_ready", "cleanup_confirmed",
                "child_exit_nonzero"):
            _bool(getattr(self, name), label=f"hard-kill {name}")
        if self.terminal_status not in {None, "failed", "cancelled"}:
            raise ValueError("operational drill terminal status is invalid")
        _nonnegative_int(
            self.source_event_count, label="hard-kill source event count")
        _nonnegative_int(
            self.active_stages_closed,
            label="hard-kill active stage count")
        _duration(
            self.kill_confirmation_ms, label="hard-kill confirmation",
            optional=True)
        _duration(
            self.telemetry_recovery_ms, label="telemetry recovery",
            optional=True)
        if self.passed and not (
                self.worker_ready and self.cleanup_confirmed
                and self.child_exit_nonzero
                and self.terminal_status == "failed"
                and self.source_event_count == 2
                and self.active_stages_closed == 1
                and self.kill_confirmation_ms is not None
                and self.telemetry_recovery_ms is not None):
            raise ValueError("operational hard-kill success is inconsistent")

    def to_payload(self) -> dict:
        return {
            "passed": self.passed,
            "worker_ready": self.worker_ready,
            "cleanup_confirmed": self.cleanup_confirmed,
            "child_exit_nonzero": self.child_exit_nonzero,
            "terminal_status": self.terminal_status,
            "source_event_count": self.source_event_count,
            "active_stages_closed": self.active_stages_closed,
            "kill_confirmation_ms": self.kill_confirmation_ms,
            "telemetry_recovery_ms": self.telemetry_recovery_ms,
        }


@dataclass(frozen=True, slots=True)
class SyncedPublicationResult:
    passed: bool
    transient_failures_requested: int
    replace_attempts: int
    writer_calls: int
    target_verified: bool
    staging_clean: bool
    publication_ms: float | None

    def __post_init__(self) -> None:
        for name in ("passed", "target_verified", "staging_clean"):
            _bool(getattr(self, name), label=f"synced-publication {name}")
        for name in (
                "transient_failures_requested", "replace_attempts",
                "writer_calls"):
            _nonnegative_int(
                getattr(self, name), label=f"synced-publication {name}")
        _duration(
            self.publication_ms, label="synced publication", optional=True)
        if self.passed and not (
                self.target_verified and self.staging_clean
                and self.writer_calls == 1
                and self.replace_attempts
                == self.transient_failures_requested + 1
                and self.publication_ms is not None):
            raise ValueError(
                "operational synced-publication success is inconsistent")

    def to_payload(self) -> dict:
        return {
            "passed": self.passed,
            "transient_failures_requested": (
                self.transient_failures_requested),
            "replace_attempts": self.replace_attempts,
            "writer_calls": self.writer_calls,
            "target_verified": self.target_verified,
            "staging_clean": self.staging_clean,
            "publication_ms": self.publication_ms,
        }


@dataclass(frozen=True, slots=True)
class OperationalDrillReport:
    run_id: str
    status: str
    started_at: float
    finished_at: float
    duration_ms: float
    hard_kill: HardKillResult
    synced_publication: SyncedPublicationResult

    def __post_init__(self) -> None:
        if not isinstance(self.run_id, str) or not _RUN_ID.fullmatch(
                self.run_id):
            raise ValueError("operational drill run ID is invalid")
        if self.status not in {"succeeded", "failed"}:
            raise ValueError("operational drill status is invalid")
        started = _timestamp(self.started_at, label="start timestamp")
        finished = _timestamp(self.finished_at, label="finish timestamp")
        duration = _duration(self.duration_ms, label="duration")
        if finished < started or duration is None:
            raise ValueError("operational drill timestamps are inconsistent")
        if type(self.hard_kill) is not HardKillResult:
            raise TypeError("hard_kill must be HardKillResult")
        if type(self.synced_publication) is not SyncedPublicationResult:
            raise TypeError(
                "synced_publication must be SyncedPublicationResult")
        expected = (
            "succeeded" if (
                self.hard_kill.passed and self.synced_publication.passed)
            else "failed")
        if self.status != expected:
            raise ValueError("operational drill status is inconsistent")

    def to_payload(self) -> dict:
        return {
            "schema_version": SCHEMA_VERSION,
            "kind": KIND,
            "run_id": self.run_id,
            "status": self.status,
            "started_at": self.started_at,
            "finished_at": self.finished_at,
            "duration_ms": self.duration_ms,
            "hard_kill": self.hard_kill.to_payload(),
            "synced_publication": self.synced_publication.to_payload(),
        }


def parse_report(payload: object) -> OperationalDrillReport:
    """Parse the exact bounded report schema without compatibility guessing."""
    payload = _exact_fields(payload, _REPORT_FIELDS, label="report")
    if type(payload["schema_version"]) is not int or payload[
            "schema_version"] != SCHEMA_VERSION:
        raise ValueError("operational drill schema version is invalid")
    if payload["kind"] != KIND:
        raise ValueError("operational drill kind is invalid")
    hard = _exact_fields(
        payload["hard_kill"], _HARD_KILL_FIELDS, label="hard-kill")
    synced = _exact_fields(
        payload["synced_publication"], _SYNCED_FIELDS,
        label="synced-publication")
    return OperationalDrillReport(
        run_id=payload["run_id"], status=payload["status"],
        started_at=payload["started_at"],
        finished_at=payload["finished_at"],
        duration_ms=payload["duration_ms"],
        hard_kill=HardKillResult(**hard),
        synced_publication=SyncedPublicationResult(**synced),
    )


def _reject_constant(value: str):
    raise ValueError(f"non-standard JSON constant {value!r}")


def _unique_object(pairs: list[tuple[str, object]]) -> dict:
    result = {}
    for key, value in pairs:
        if key in result:
            raise ValueError("operational drill JSON contains duplicate fields")
        result[key] = value
    return result


def load_report(path: Path) -> OperationalDrillReport:
    path = Path(path)
    if path.stat().st_size > MAX_REPORT_BYTES:
        raise ValueError("operational drill report exceeds its size limit")
    payload = json.loads(
        path.read_text(encoding="utf-8"),
        object_pairs_hook=_unique_object,
        parse_constant=_reject_constant)
    return parse_report(payload)


def publish(path: Path, report: OperationalDrillReport) -> None:
    if type(report) is not OperationalDrillReport:
        raise TypeError("operational drill report must use the exact type")
    payload = report.to_payload()
    parse_report(payload)
    encoded = json.dumps(
        payload, ensure_ascii=False, sort_keys=True, allow_nan=False,
        separators=(",", ":"))
    if len(encoded.encode("utf-8")) > MAX_REPORT_BYTES:
        raise ValueError("operational drill report exceeds its size limit")
    storage_policy.atomic_write_private_json(path, payload, indent=2)


def _empty_hard_kill_result() -> HardKillResult:
    return HardKillResult(
        passed=False, worker_ready=False, cleanup_confirmed=False,
        child_exit_nonzero=False, terminal_status=None, source_event_count=0,
        active_stages_closed=0, kill_confirmation_ms=None,
        telemetry_recovery_ms=None)


def _empty_synced_result(*, transient_failures: int = 2
                         ) -> SyncedPublicationResult:
    return SyncedPublicationResult(
        passed=False, transient_failures_requested=transient_failures,
        replace_attempts=0, writer_calls=0, target_verified=False,
        staging_clean=False, publication_ms=None)


def _wait_for_ready(process: subprocess.Popen, ready_path: Path, *,
                    timeout: float, monotonic_clock=time.monotonic) -> bool:
    deadline = _monotonic(monotonic_clock) + timeout
    while _monotonic(monotonic_clock) < deadline:
        if ready_path.is_file():
            return True
        if process.poll() is not None:
            return False
        time.sleep(0.02)
    return ready_path.is_file()


def _child_environment() -> dict[str, str]:
    """Return only interpreter/OS plumbing, never ambient provider secrets."""
    return {
        key: value for key, value in os.environ.items()
        if key.upper() in _CHILD_ENVIRONMENT_ALLOWLIST
    }


def _run_hard_kill_drill(
        output_dir: Path, run_id: str, *, ready_timeout: float = 5.0,
        kill_timeout: float = 5.0,
        monotonic_clock=time.monotonic) -> HardKillResult:
    events_path = output_dir / "hard-kill.events.jsonl"
    report_path = output_dir / "hard-kill.report.json"
    ready_path = output_dir / ".hard-kill.ready"
    environment = _child_environment()
    environment.update({
        "RAG_DRILL_EVENTS": str(events_path),
        "RAG_DRILL_REPORT": str(report_path),
        "RAG_DRILL_READY": str(ready_path),
        "RAG_DRILL_RUN_ID": run_id,
    })
    project_root = Path(__file__).resolve().parent
    supervisor = Path(process_supervision.__file__).with_name(
        "supervised_worker.py").resolve()
    worker = project_root / "tools" / "operational_drill_worker.py"
    start_gate = None
    kill_job = None
    process = None
    worker_ready = False
    cleanup_confirmed = False
    cleanup_attempted = False
    kill_confirmation_ms = None
    telemetry_recovery_ms = None
    recovered = None
    try:
        start_gate = process_supervision._new_supervised_start_gate()
        kill_job = (
            process_supervision._WindowsKillJob()
            if os.name == "nt" else None)
        popen_options = {
            "cwd": project_root,
            "env": environment,
            "stdin": subprocess.DEVNULL,
            "stdout": subprocess.DEVNULL,
            "stderr": subprocess.DEVNULL,
        }
        if os.name == "nt":
            popen_options["creationflags"] = (
                getattr(subprocess, "CREATE_NEW_PROCESS_GROUP", 0)
                | getattr(subprocess, "CREATE_NO_WINDOW", 0))
        else:
            popen_options["start_new_session"] = True
        popen_options.update(start_gate.popen_options())
        process = subprocess.Popen(
            [
                sys.executable, "-u", str(supervisor), start_gate.kind,
                start_gate.child_value, str(ready_timeout), str(worker),
            ],
            **popen_options,
        )
        if kill_job is not None:
            kill_job.assign(process)
        start_gate.release()
        worker_ready = _wait_for_ready(
            process, ready_path, timeout=ready_timeout,
            monotonic_clock=monotonic_clock)
        if not worker_ready:
            raise RuntimeError("operational drill worker did not become ready")
        kill_started = _monotonic(monotonic_clock)
        cleanup_attempted = True
        cleanup_confirmed = process_supervision._terminate_supervised_process(
            process, kill_job=kill_job, terminate_grace=kill_timeout)
        kill_confirmation_ms = round(max(
            0.0, (_monotonic(monotonic_clock) - kill_started) * 1000), 3)
        if not cleanup_confirmed:
            raise RuntimeError(
                "operational drill process-tree cleanup was unconfirmed")
        recovery_started = _monotonic(monotonic_clock)
        recovered = run_telemetry.finalize_interrupted_run(
            "operational_drill", run_id=run_id, status="failed",
            exc=TimeoutError(_PRIVATE_CANARY), events_path=events_path,
            report_path=report_path)
        telemetry_recovery_ms = round(max(
            0.0,
            (_monotonic(monotonic_clock) - recovery_started) * 1000), 3)
    finally:
        if (process is not None and not cleanup_confirmed
                and not cleanup_attempted):
            cleanup_attempted = True
            try:
                cleanup_confirmed = (
                    process_supervision._terminate_supervised_process(
                        process, kill_job=kill_job,
                        terminate_grace=kill_timeout))
            except BaseException:
                cleanup_confirmed = False
        if start_gate is not None:
            start_gate.close()
        if kill_job is not None:
            try:
                cleanup_confirmed = (
                    bool(kill_job.close()) and cleanup_confirmed)
            except BaseException:
                cleanup_confirmed = False
        try:
            ready_path.unlink(missing_ok=True)
        except OSError:
            pass
        if process is not None and not cleanup_confirmed:
            raise RuntimeError(
                "operational drill process-tree cleanup was unconfirmed")
    assert process is not None
    child_exit_nonzero = process.returncode is not None and process.returncode != 0
    recovery = recovered.get("recovery", {}) if recovered is not None else {}
    source_count = recovery.get("source_event_count", 0)
    active_count = recovery.get("active_stages_closed", 0)
    terminal_status = recovered.get("status") if recovered is not None else None
    combined = (
        events_path.read_text(encoding="utf-8")
        + report_path.read_text(encoding="utf-8"))
    passed = bool(
        worker_ready and cleanup_confirmed and child_exit_nonzero
        and terminal_status == "failed"
        and source_count == 2 and active_count == 1
        and recovered.get("event_count") == 4
        and recovered.get("stages", {}).get(
            "hard_kill_work", {}).get("failed") == 1
        and _PRIVATE_CANARY not in combined)
    return HardKillResult(
        passed=passed, worker_ready=worker_ready,
        cleanup_confirmed=cleanup_confirmed,
        child_exit_nonzero=child_exit_nonzero,
        terminal_status=terminal_status,
        source_event_count=source_count,
        active_stages_closed=active_count,
        kill_confirmation_ms=kill_confirmation_ms,
        telemetry_recovery_ms=telemetry_recovery_ms)


def _run_synced_publication_drill(
        output_dir: Path, *, transient_failures: int = 2,
        monotonic_clock=time.monotonic) -> SyncedPublicationResult:
    target = output_dir / "synced-publication.txt"
    content = "operational-publication-drill-v1\n"
    counters = {"replace_attempts": 0, "writer_calls": 0}

    def replace(source, destination, *args, **kwargs):
        counters["replace_attempts"] += 1
        if counters["replace_attempts"] <= transient_failures:
            error = PermissionError("simulated sharing violation")
            error.winerror = 32
            raise error
        os.replace(source, destination, *args, **kwargs)

    def writer(handle) -> None:
        counters["writer_calls"] += 1
        handle.write(content)

    started = _monotonic(monotonic_clock)
    storage_policy.atomic_write_private(
        target, writer, text=True, replace_fn=replace)
    publication_ms = round(max(
        0.0, (_monotonic(monotonic_clock) - started) * 1000), 3)
    target_verified = target.read_text(encoding="utf-8") == content
    staging_clean = not any(
        candidate.name.startswith(f".{target.name}.")
        and candidate.name.endswith(".tmp")
        for candidate in output_dir.iterdir())
    passed = bool(
        counters["replace_attempts"] == transient_failures + 1
        and counters["writer_calls"] == 1
        and target_verified and staging_clean)
    return SyncedPublicationResult(
        passed=passed, transient_failures_requested=transient_failures,
        replace_attempts=counters["replace_attempts"],
        writer_calls=counters["writer_calls"],
        target_verified=target_verified, staging_clean=staging_clean,
        publication_ms=publication_ms)


def _validated_timeout(value: object, *, label: str) -> float:
    if (isinstance(value, bool) or not isinstance(value, (int, float))
            or not math.isfinite(float(value))
            or not 0.1 <= float(value) <= 60.0):
        raise ValueError(f"operational drill {label} must be 0.1-60 seconds")
    return float(value)


def run_operational_drills(
        output_dir: Path, *, ready_timeout: float = 5.0,
        kill_timeout: float = 5.0, wall_clock=time.time,
        monotonic_clock=time.monotonic) -> OperationalDrillReport:
    """Run both drills once in a new empty private evidence directory."""
    ready_timeout = _validated_timeout(ready_timeout, label="ready timeout")
    kill_timeout = _validated_timeout(kill_timeout, label="kill timeout")
    requested_output = Path(output_dir)
    storage_policy.assert_no_link_components(requested_output)
    output_dir = requested_output.resolve(strict=False)
    if output_dir == Path(output_dir.anchor) or output_dir == Path.home().resolve(
            strict=False):
        raise ValueError("operational drill output directory is too broad")
    existed = output_dir.exists()
    if existed:
        storage_policy.assert_no_link_components(output_dir)
        if not output_dir.is_dir():
            raise ValueError("operational drill output must be a directory")
        if any(output_dir.iterdir()):
            raise ValueError("operational drill output directory must be empty")
    output_dir = storage_policy.ensure_private_directory(output_dir)
    run_id = uuid4().hex
    started_at = _timestamp(wall_clock(), label="start timestamp")
    started_monotonic = _monotonic(monotonic_clock)
    hard_kill = _empty_hard_kill_result()
    synced = _empty_synced_result()
    try:
        hard_kill = _run_hard_kill_drill(
            output_dir, run_id, ready_timeout=ready_timeout,
            kill_timeout=kill_timeout, monotonic_clock=monotonic_clock)
    except Exception:
        pass
    try:
        synced = _run_synced_publication_drill(
            output_dir, monotonic_clock=monotonic_clock)
    except Exception:
        pass
    finished_at = max(
        started_at, _timestamp(wall_clock(), label="finish timestamp"))
    duration_ms = round(max(
        0.0, (_monotonic(monotonic_clock) - started_monotonic) * 1000), 3)
    status = "succeeded" if hard_kill.passed and synced.passed else "failed"
    report = OperationalDrillReport(
        run_id=run_id, status=status, started_at=started_at,
        finished_at=finished_at, duration_ms=duration_ms,
        hard_kill=hard_kill, synced_publication=synced)
    publish(output_dir / REPORT_NAME, report)
    return report


__all__ = [
    "HardKillResult",
    "KIND",
    "MAX_REPORT_BYTES",
    "OperationalDrillReport",
    "REPORT_NAME",
    "SCHEMA_VERSION",
    "SyncedPublicationResult",
    "load_report",
    "parse_report",
    "publish",
    "run_operational_drills",
]
