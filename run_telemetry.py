"""Prompt- and artifact-free run telemetry for pipeline operations.

The module is standard-library-only so CLI, UI, and future service adapters can
share one stable correlation and diagnostics contract without importing model
or vector-store dependencies.
"""

from __future__ import annotations

import hashlib
import hmac
import json
import math
import os
import re
import secrets
import threading
import time
from pathlib import Path
from uuid import uuid4

from storage_policy import (
    atomic_write_private_json,
    atomic_write_private_jsonl,
)


EVENT_SCHEMA_VERSION = 1
REPORT_SCHEMA_VERSION = 1
_IDENTIFIER = re.compile(r"^[A-Za-z0-9][A-Za-z0-9_.-]{0,127}$")
_TERMINAL_STATUSES = frozenset({
    "succeeded", "partial", "failed", "cancelled",
})
_STAGE_TERMINAL_STATUSES = frozenset({
    "completed", "skipped", "failed", "cancelled",
})
_EVENT_STATUSES = frozenset({
    "started", "completed", "skipped", "failed", "cancelled", "succeeded",
    "partial",
})
_MAX_RECOVERY_BYTES = 8 * 1024 * 1024
_MAX_RECOVERY_EVENTS = 10_000
_DIAGNOSTIC_DIGEST_KEY = secrets.token_bytes(32)


def new_run_id() -> str:
    """Return a non-semantic identifier safe to place in logs and filenames."""
    return uuid4().hex


def validate_distinct_output_paths(paths: dict[str, Path | None]) -> None:
    """Reject lexical, symlink, case, or hard-link aliases between outputs."""
    observed: list[tuple[str, Path, str]] = []
    for label, value in paths.items():
        if value is None:
            continue
        path = Path(value)
        try:
            normalized = os.path.normcase(str(path.resolve(strict=False)))
        except OSError:
            normalized = os.path.normcase(os.path.abspath(path))
        for prior_label, prior_path, prior_normalized in observed:
            aliases = normalized == prior_normalized
            if not aliases and path.exists() and prior_path.exists():
                try:
                    aliases = os.path.samefile(path, prior_path)
                except OSError:
                    aliases = False
            if aliases:
                raise ValueError(
                    f"{label} and {prior_label} must use distinct files")
        observed.append((label, path, normalized))


def _validate_identifier(value: object, *, label: str) -> str:
    if not isinstance(value, str) or not _IDENTIFIER.fullmatch(value):
        raise ValueError(
            f"{label} must be 1-128 safe identifier characters")
    return value


def _atomic_write_private_json(path: Path, payload: object) -> None:
    """Atomically publish pretty JSON under the shared private policy."""
    atomic_write_private_json(path, payload, indent=2)


def _atomic_write_private_jsonl(path: Path, records: list[dict]) -> None:
    """Rewrite the small stage-event stream without exposing partial lines."""
    atomic_write_private_jsonl(path, records, compact=True)


def _safe_metrics(metrics: dict | None) -> dict:
    if metrics is None:
        return {}
    if not isinstance(metrics, dict):
        raise TypeError("run telemetry metrics must be a dictionary")
    normalized = {}
    for key, value in metrics.items():
        metric = _validate_identifier(key, label="metric name")
        if isinstance(value, bool) or value is None:
            normalized[metric] = value
        elif isinstance(value, int):
            normalized[metric] = value
        elif isinstance(value, float) and math.isfinite(value):
            normalized[metric] = round(value, 3)
        else:
            raise TypeError(
                "run telemetry values must be finite numbers, booleans, or null")
    return normalized


def failure_diagnostic(exc: BaseException) -> dict:
    """Return an actionable, content-free diagnostic for an exception."""
    error_type = type(exc).__name__
    lowered = error_type.casefold()
    if isinstance(exc, (KeyboardInterrupt,)):
        category, action = "cancelled", "resume_when_ready"
    elif isinstance(exc, TimeoutError) or "timeout" in lowered:
        category, action = "timeout", "retry_or_raise_operation_timeout"
    elif "budget" in lowered:
        category, action = "budget_exhausted", "resume_with_a_larger_budget"
    elif "busy" in lowered or "lock" in lowered:
        category, action = "resource_busy", "wait_or_raise_lock_timeout"
    elif isinstance(exc, FileNotFoundError):
        category, action = "input_missing", "verify_the_input_path"
    elif isinstance(exc, PermissionError):
        category, action = "permission_denied", "fix_permissions_and_resume"
    elif isinstance(exc, SystemExit):
        code = exc.code if isinstance(exc.code, int) else 1
        if code == 130:
            category, action = "cancelled", "resume_when_ready"
        else:
            category, action = "process_exit", "inspect_logs_and_resume"
    else:
        category, action = "internal_error", "inspect_logs_and_resume"
    safe_error_type = (
        error_type if _IDENTIFIER.fullmatch(error_type) else "Exception")
    return {
        "category": category,
        "action": action,
        "error_type": safe_error_type,
        # A process-private HMAC remains useful for correlating repeated local
        # failures without making low-entropy filenames or messages vulnerable
        # to an offline dictionary attack from an exported report.
        "message_digest": hmac.new(
            _DIAGNOSTIC_DIGEST_KEY,
            str(exc).encode("utf-8", errors="replace"),
            hashlib.sha256,
        ).hexdigest(),
    }


def _load_recovery_events(path: Path, *, operation: str,
                          run_id: str) -> list[dict]:
    if not path.is_file():
        return []
    if path.stat().st_size > _MAX_RECOVERY_BYTES:
        raise ValueError("run event stream exceeds its recovery size limit")
    events = []
    for line_number, line in enumerate(
            path.read_text(encoding="utf-8").splitlines(), 1):
        if not line.strip():
            continue
        if len(events) >= _MAX_RECOVERY_EVENTS:
            raise ValueError("run event stream exceeds its recovery event limit")
        try:
            event = json.loads(line)
        except json.JSONDecodeError as exc:
            raise ValueError(
                f"invalid run event JSON on line {line_number}") from exc
        if not isinstance(event, dict):
            raise ValueError("run event must be a JSON object")
        allowed = {
            "schema_version", "sequence", "timestamp", "run_id",
            "parent_run_id", "operation", "stage", "status", "metrics",
            "diagnostic",
        }
        if set(event) - allowed:
            raise ValueError("run event contains unsupported fields")
        timestamp = event.get("timestamp")
        if (isinstance(timestamp, bool)
                or not isinstance(timestamp, (int, float))
                or not math.isfinite(float(timestamp))):
            raise ValueError("run event timestamp must be finite")
        parent_run_id = event.get("parent_run_id")
        if parent_run_id is not None:
            _validate_identifier(parent_run_id, label="parent_run_id")
        if (event.get("schema_version") != EVENT_SCHEMA_VERSION
                or event.get("sequence") != len(events) + 1
                or event.get("run_id") != run_id
                or event.get("operation") != operation
                or event.get("status") not in _EVENT_STATUSES):
            raise ValueError("run event identity or sequence is incompatible")
        _validate_identifier(event.get("stage"), label="stage")
        _safe_metrics(event.get("metrics"))
        diagnostic = event.get("diagnostic")
        if diagnostic is not None:
            if (not isinstance(diagnostic, dict)
                    or set(diagnostic) != {
                        "category", "action", "error_type", "message_digest"}
                    or not isinstance(diagnostic["message_digest"], str)
                    or not re.fullmatch(
                        r"[0-9a-f]{64}", diagnostic["message_digest"])):
                raise ValueError("run event diagnostic is incompatible")
            for key in ("category", "action", "error_type"):
                _validate_identifier(diagnostic[key], label=key)
        events.append(event)
    if events:
        if events[0]["stage"] != "run" or events[0]["status"] != "started":
            raise ValueError("run event stream must begin with run start")
        parent_ids = {event.get("parent_run_id") for event in events}
        if len(parent_ids) != 1:
            raise ValueError("run event parent identity is incompatible")
        terminal_indexes = [
            index for index, event in enumerate(events)
            if event["stage"] == "run"
            and event["status"] in _TERMINAL_STATUSES
        ]
        if (len(terminal_indexes) > 1
                or terminal_indexes and terminal_indexes[0] != len(events) - 1):
            raise ValueError("run terminal event must be unique and last")
        for index, event in enumerate(events):
            if event["stage"] != "run":
                continue
            valid_statuses = {"started"} if index == 0 else _TERMINAL_STATUSES
            if event["status"] not in valid_statuses:
                raise ValueError("run event status is incompatible")
    return events


class RunTelemetry:
    """Thread-safe run and stage event recorder with a stable JSON contract."""

    def __init__(self, operation: str, *, run_id: str | None = None,
                 parent_run_id: str | None = None,
                 events_path: Path | None = None,
                 report_path: Path | None = None,
                 wall_clock=time.time, monotonic_clock=time.monotonic):
        self.operation = _validate_identifier(operation, label="operation")
        self.run_id = _validate_identifier(
            run_id or new_run_id(), label="run_id")
        self.parent_run_id = (
            _validate_identifier(parent_run_id, label="parent_run_id")
            if parent_run_id is not None else None)
        self.events_path = Path(events_path) if events_path is not None else None
        self.report_path = Path(report_path) if report_path is not None else None
        validate_distinct_output_paths({
            "run events": self.events_path,
            "run report": self.report_path,
        })
        self._wall_clock = wall_clock
        self._monotonic = monotonic_clock
        self._lock = threading.RLock()
        self._events: list[dict] = []
        self._active_stages: dict[str, list[float]] = {}
        self._started_at: float | None = None
        self._started_monotonic: float | None = None
        self._finished_at: float | None = None
        self._finished_monotonic: float | None = None
        self._status: str | None = None
        self._failure: dict | None = None

    @property
    def enabled(self) -> bool:
        return self.events_path is not None or self.report_path is not None

    @property
    def finished(self) -> bool:
        return self._status in _TERMINAL_STATUSES

    def _publish_events(self) -> None:
        if self.events_path is not None:
            _atomic_write_private_jsonl(self.events_path, self._events)

    @classmethod
    def recover(cls, operation: str, *, run_id: str,
                events_path: Path | None = None,
                report_path: Path | None = None) -> "RunTelemetry":
        """Recover a killed worker's valid event stream after it has exited."""
        telemetry = cls(
            operation, run_id=run_id, events_path=events_path,
            report_path=report_path)
        if events_path is None:
            telemetry.start()
            return telemetry
        events = _load_recovery_events(
            Path(events_path), operation=telemetry.operation,
            run_id=telemetry.run_id)
        if not events:
            telemetry.start()
            return telemetry
        telemetry._events = events
        telemetry.parent_run_id = events[0].get("parent_run_id")
        telemetry._started_at = float(events[0]["timestamp"])
        now_wall = telemetry._wall_clock()
        now_monotonic = telemetry._monotonic()
        elapsed = max(0.0, now_wall - telemetry._started_at)
        telemetry._started_monotonic = now_monotonic - elapsed
        active_stage_times: dict[str, list[float]] = {}
        for event in events:
            stage = event["stage"]
            if stage == "run":
                continue
            if event["status"] == "started":
                active_stage_times.setdefault(stage, []).append(
                    float(event["timestamp"]))
            elif event["status"] in _STAGE_TERMINAL_STATUSES:
                active = active_stage_times.get(stage, [])
                if active:
                    active.pop()
                if not active:
                    active_stage_times.pop(stage, None)
        telemetry._active_stages = {
            stage: [
                now_monotonic - max(0.0, now_wall - started_at)
                for started_at in starts
            ]
            for stage, starts in active_stage_times.items()
        }
        terminal = next((
            event for event in reversed(events)
            if event["stage"] == "run"
            and event["status"] in _TERMINAL_STATUSES
        ), None)
        if terminal is not None:
            telemetry._status = terminal["status"]
            telemetry._finished_at = float(terminal["timestamp"])
            duration_ms = terminal.get("metrics", {}).get("duration_ms")
            if (isinstance(duration_ms, (int, float))
                    and not isinstance(duration_ms, bool)
                    and telemetry._started_monotonic is not None):
                telemetry._finished_monotonic = (
                    telemetry._started_monotonic + float(duration_ms) / 1000)
            else:
                telemetry._finished_monotonic = telemetry._monotonic()
            telemetry._failure = terminal.get("diagnostic")
        else:
            prior_failure = next((
                event.get("diagnostic") for event in reversed(events)
                if event["status"] in {"failed", "cancelled"}
                and event.get("diagnostic") is not None
            ), None)
            telemetry._failure = prior_failure
        return telemetry

    def _append(self, stage: str, status: str, *, metrics: dict | None = None,
                diagnostic: dict | None = None) -> dict:
        stage = _validate_identifier(stage, label="stage")
        with self._lock:
            if self._started_at is None:
                raise RuntimeError("run telemetry has not been started")
            if self.finished:
                raise RuntimeError("run telemetry is already finished")
            event = {
                "schema_version": EVENT_SCHEMA_VERSION,
                "sequence": len(self._events) + 1,
                "timestamp": self._wall_clock(),
                "run_id": self.run_id,
                "operation": self.operation,
                "stage": stage,
                "status": status,
                "metrics": _safe_metrics(metrics),
            }
            if self.parent_run_id is not None:
                event["parent_run_id"] = self.parent_run_id
            if diagnostic is not None:
                event["diagnostic"] = dict(diagnostic)
            self._events.append(event)
            self._publish_events()
            return dict(event)

    def start(self) -> str:
        with self._lock:
            if self._started_at is not None:
                return self.run_id
            self._started_at = self._wall_clock()
            self._started_monotonic = self._monotonic()
            start_event = self._append("run", "started")
            self._started_at = float(start_event["timestamp"])
            if self.report_path is not None:
                self.persist_report()
            return self.run_id

    def stage_started(self, stage: str, *, metrics: dict | None = None) -> None:
        stage = _validate_identifier(stage, label="stage")
        with self._lock:
            self._active_stages.setdefault(stage, []).append(self._monotonic())
            self._append(stage, "started", metrics=metrics)

    def stage_finished(self, stage: str, *, status: str = "completed",
                       metrics: dict | None = None) -> None:
        if status not in {"completed", "skipped"}:
            raise ValueError(f"invalid successful stage status: {status}")
        stage = _validate_identifier(stage, label="stage")
        combined = dict(metrics or {})
        with self._lock:
            active = self._active_stages.get(stage, [])
            started = active.pop() if active else None
            if not active:
                self._active_stages.pop(stage, None)
            if started is not None and "duration_ms" not in combined:
                combined["duration_ms"] = (
                    self._monotonic() - started) * 1000
            self._append(stage, status, metrics=combined)

    def stage_failed(self, stage: str, exc: BaseException, *,
                     metrics: dict | None = None) -> None:
        self._stage_terminated(stage, "failed", exc, metrics=metrics)

    def stage_cancelled(self, stage: str, exc: BaseException, *,
                        metrics: dict | None = None) -> None:
        self._stage_terminated(stage, "cancelled", exc, metrics=metrics)

    def _stage_terminated(self, stage: str, status: str,
                          exc: BaseException, *,
                          metrics: dict | None = None) -> None:
        if status not in {"failed", "cancelled"}:
            raise ValueError(f"invalid failed stage status: {status}")
        stage = _validate_identifier(stage, label="stage")
        combined = dict(metrics or {})
        with self._lock:
            active = self._active_stages.get(stage, [])
            started = active.pop() if active else None
            if not active:
                self._active_stages.pop(stage, None)
            if started is not None and "duration_ms" not in combined:
                combined["duration_ms"] = (
                    self._monotonic() - started) * 1000
            diagnostic = failure_diagnostic(exc)
            self._failure = diagnostic
            self._append(stage, status, metrics=combined,
                         diagnostic=diagnostic)

    def terminate_active_stages(self, status: str,
                                exc: BaseException) -> None:
        """Close every unmatched stage before a recovered run terminates."""
        if status not in {"failed", "cancelled"}:
            raise ValueError("active stages must fail or be cancelled")
        with self._lock:
            active = [
                (stage, len(starts))
                for stage, starts in self._active_stages.items()
            ]
        for stage, count in active:
            for _ in range(count):
                self._stage_terminated(stage, status, exc)

    def stage_observation(self, stage: str, *,
                          metrics: dict | None = None) -> None:
        """Record aggregate component metrics gathered outside a timed span."""
        self._append(stage, "completed", metrics=metrics)

    def _stage_summary(self) -> dict:
        summary: dict[str, dict] = {}
        for event in self._events:
            stage = event["stage"]
            if stage == "run":
                continue
            target = summary.setdefault(stage, {
                "started": 0, "completed": 0, "skipped": 0,
                "failed": 0, "cancelled": 0, "duration_ms": 0.0,
            })
            status = event["status"]
            if status in target:
                target[status] += 1
            duration = event["metrics"].get("duration_ms")
            if isinstance(duration, (int, float)) and not isinstance(
                    duration, bool):
                target["duration_ms"] += float(duration)
        for target in summary.values():
            target["duration_ms"] = round(target["duration_ms"], 3)
        return dict(sorted(summary.items()))

    def report_payload(self) -> dict:
        with self._lock:
            elapsed_ms = None
            if self._started_monotonic is not None:
                elapsed_ms = round(
                    ((self._finished_monotonic
                      if self._finished_monotonic is not None
                      else self._monotonic())
                     - self._started_monotonic) * 1000, 3)
            payload = {
                "schema_version": REPORT_SCHEMA_VERSION,
                "run_id": self.run_id,
                "operation": self.operation,
                "status": self._status or "running",
                "started_at": self._started_at,
                "finished_at": self._finished_at,
                "elapsed_ms": elapsed_ms,
                "event_count": len(self._events),
                "stages": self._stage_summary(),
            }
            if self.parent_run_id is not None:
                payload["parent_run_id"] = self.parent_run_id
            if self._failure is not None:
                payload["failure"] = dict(self._failure)
            return payload

    def persist_report(self) -> dict:
        """Publish the current snapshot without changing its run state."""
        payload = self.report_payload()
        if self.report_path is not None:
            _atomic_write_private_json(self.report_path, payload)
        return payload

    def finish(self, status: str = "succeeded", *,
               exc: BaseException | None = None) -> dict:
        if status not in _TERMINAL_STATUSES:
            raise ValueError(f"invalid run status: {status}")
        with self._lock:
            if self.finished:
                return self.report_payload()
            diagnostic = failure_diagnostic(exc) if exc is not None else None
            if status == "succeeded":
                diagnostic = None
                self._failure = None
            elif self._failure is not None:
                diagnostic = dict(self._failure)
            elif diagnostic is not None:
                self._failure = diagnostic
            finished_monotonic = self._monotonic()
            elapsed_ms = None
            if self._started_monotonic is not None:
                elapsed_ms = (
                    finished_monotonic - self._started_monotonic) * 1000
            terminal_event = self._append(
                "run", status,
                metrics={
                    "duration_ms": elapsed_ms
                } if elapsed_ms is not None else {},
                diagnostic=diagnostic)
            self._status = status
            self._finished_at = float(terminal_event["timestamp"])
            self._finished_monotonic = finished_monotonic
            payload = self.report_payload()
            if self.report_path is not None:
                _atomic_write_private_json(self.report_path, payload)
            return payload

    def finish_from_exception(self, exc: BaseException | None) -> dict:
        if exc is None:
            return self.finish("succeeded")
        if isinstance(exc, KeyboardInterrupt):
            return self.finish("cancelled", exc=exc)
        if isinstance(exc, SystemExit):
            if exc.code is None or exc.code == 0:
                return self.finish("succeeded")
            code = exc.code if isinstance(exc.code, int) else 1
            if code == 130:
                return self.finish("cancelled", exc=exc)
        return self.finish("failed", exc=exc)

    def __enter__(self) -> "RunTelemetry":
        self.start()
        return self

    def __exit__(self, exc_type, exc, traceback) -> bool:
        self.finish_from_exception(exc)
        return False


def finalize_interrupted_run(
        operation: str, *, run_id: str, status: str, exc: BaseException,
        events_path: Path | None = None,
        report_path: Path | None = None) -> dict:
    """Synthesize a terminal record after a supervised worker is gone."""
    if status not in {"failed", "cancelled"}:
        raise ValueError("interrupted run status must be failed or cancelled")
    events_available = (
        events_path is not None and Path(events_path).is_file()
        and Path(events_path).stat().st_size > 0)
    if not events_available and report_path is not None and Path(
            report_path).is_file():
        try:
            existing = json.loads(Path(report_path).read_text(encoding="utf-8"))
            if (isinstance(existing, dict)
                    and existing.get("schema_version") == REPORT_SCHEMA_VERSION
                    and existing.get("run_id") == run_id
                    and existing.get("operation") == operation
                    and existing.get("status") in _TERMINAL_STATUSES):
                return existing
        except (OSError, UnicodeError, json.JSONDecodeError):
            pass
    telemetry = RunTelemetry.recover(
        operation, run_id=run_id, events_path=events_path,
        report_path=report_path)
    if telemetry.finished:
        return telemetry.persist_report()
    telemetry.terminate_active_stages(status, exc)
    return telemetry.finish(status, exc=exc)
