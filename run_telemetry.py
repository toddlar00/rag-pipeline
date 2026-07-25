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
    jsonl_lines,
)


EVENT_SCHEMA_VERSION = 1
REPORT_SCHEMA_VERSION = 2
SUPPORTED_REPORT_SCHEMA_VERSIONS = frozenset({1, REPORT_SCHEMA_VERSION})
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
_RECOVERY_EVENT_METRICS = (
    "recovery_source_event_count",
    "recovery_active_stages_closed",
    "recovery_stage_closure_ms",
)


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
    json.dumps(payload, allow_nan=False)
    atomic_write_private_json(path, payload, indent=2)


def _atomic_write_private_jsonl(path: Path, records: list[dict]) -> None:
    """Rewrite the small stage-event stream without exposing partial lines."""
    for record in records:
        json.dumps(record, allow_nan=False, separators=(",", ":"))
    atomic_write_private_jsonl(path, records, compact=True)


def _safe_metrics(metrics: dict | None) -> dict:
    if metrics is None:
        return {}
    if not isinstance(metrics, dict):
        raise TypeError("run telemetry metrics must be a dictionary")
    normalized = {}
    for key, value in metrics.items():
        metric = _validate_identifier(key, label="metric name")
        if metric == "duration_ms":
            if (isinstance(value, bool)
                    or not isinstance(value, (int, float))
                    or not math.isfinite(float(value)) or value < 0):
                raise TypeError(
                    "run telemetry duration must be finite and non-negative")
            normalized[metric] = round(value, 3)
            continue
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


def _finite_clock_value(value: object, *, label: str) -> float:
    if (isinstance(value, bool)
            or not isinstance(value, (int, float))
            or not math.isfinite(float(value))):
        raise ValueError(f"{label} must be finite")
    return float(value)


def _empty_stage_summary() -> dict:
    return {
        "started": 0, "completed": 0, "skipped": 0,
        "failed": 0, "cancelled": 0, "duration_ms": 0.0,
    }


def _copy_stage_summary(summary: dict) -> dict:
    copied = {
        key: value for key, value in summary.items() if key != "metrics"
    }
    if "metrics" in summary:
        copied["metrics"] = {
            metric: dict(observed)
            for metric, observed in summary["metrics"].items()
        }
    return copied


def _checked_metric_sum(left: int | float, right: int | float, *,
                        metric: str) -> int | float:
    try:
        total = left + right
    except OverflowError as exc:
        raise ValueError(
            f"stage metric {metric!r} aggregate is not finite") from exc
    if isinstance(total, float) and not math.isfinite(total):
        raise ValueError(
            f"stage metric {metric!r} aggregate is not finite")
    return total


def _apply_stage_event(summary: dict, event: dict) -> None:
    """Validate and apply one non-run event to a private aggregate copy."""
    status = event["status"]
    if status in summary:
        summary[status] += 1
    duration = event["metrics"].get("duration_ms")
    if isinstance(duration, (int, float)) and not isinstance(duration, bool):
        summary["duration_ms"] = _checked_metric_sum(
            summary["duration_ms"], duration, metric="duration_ms")
    if status not in _STAGE_TERMINAL_STATUSES:
        return
    for metric, value in event["metrics"].items():
        if metric == "duration_ms" or value is None:
            continue
        metrics = summary.setdefault("metrics", {})
        observed = metrics.get(metric)
        if isinstance(value, bool):
            if observed is None:
                observed = {
                    "kind": "boolean", "samples": 0,
                    "true_count": 0, "false_count": 0,
                    "latest": value,
                }
                metrics[metric] = observed
            if observed["kind"] != "boolean":
                raise ValueError(f"stage metric {metric!r} changed type")
            observed["samples"] += 1
            observed["true_count" if value else "false_count"] += 1
            observed["latest"] = value
            continue
        if observed is None:
            observed = {
                "kind": "number", "samples": 0,
                "total": 0, "minimum": value,
                "maximum": value, "latest": value,
            }
            metrics[metric] = observed
        if observed["kind"] != "number":
            raise ValueError(f"stage metric {metric!r} changed type")
        observed["samples"] += 1
        observed["total"] = _checked_metric_sum(
            observed["total"], value, metric=metric)
        observed["minimum"] = min(observed["minimum"], value)
        observed["maximum"] = max(observed["maximum"], value)
        observed["latest"] = value


def _render_stage_summaries(summaries: dict[str, dict]) -> dict:
    rendered = {}
    for stage, source in sorted(summaries.items()):
        target = _copy_stage_summary(source)
        target["duration_ms"] = round(target["duration_ms"], 3)
        metrics = target.get("metrics")
        if metrics:
            for observed in metrics.values():
                if observed["kind"] != "number":
                    continue
                for field in ("total", "minimum", "maximum", "latest"):
                    if isinstance(observed[field], float):
                        observed[field] = round(observed[field], 3)
            target["metrics"] = dict(sorted(metrics.items()))
        rendered[stage] = target
    return rendered


def _terminal_recovery(event: dict) -> dict | None:
    metrics = event.get("metrics", {})
    present = [name in metrics for name in _RECOVERY_EVENT_METRICS]
    if not any(present):
        return None
    if not all(present):
        raise ValueError("terminal recovery metrics are incomplete")
    source_count = metrics[_RECOVERY_EVENT_METRICS[0]]
    active_count = metrics[_RECOVERY_EVENT_METRICS[1]]
    closure_ms = metrics[_RECOVERY_EVENT_METRICS[2]]
    for label, value in (
            ("source event count", source_count),
            ("active stage count", active_count)):
        if (isinstance(value, bool) or not isinstance(value, int)
                or value < 0):
            raise ValueError(f"terminal recovery {label} is invalid")
    preceding_events = event["sequence"] - 1
    expected_preceding = source_count + active_count
    if source_count == 0:
        expected_preceding += 1
    if expected_preceding != preceding_events:
        raise ValueError("terminal recovery event counts are invalid")
    if (isinstance(closure_ms, bool)
            or not isinstance(closure_ms, (int, float))
            or not math.isfinite(float(closure_ms))
            or closure_ms < 0):
        raise ValueError("terminal recovery stage closure is invalid")
    return {
        "source_event_count": source_count,
        "active_stages_closed": active_count,
        "stage_closure_ms": closure_ms,
    }


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
            jsonl_lines(path.read_text(encoding="utf-8")), 1):
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
        if (type(event.get("schema_version")) is not int
                or event.get("schema_version") != EVENT_SCHEMA_VERSION
                or type(event.get("sequence")) is not int
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
        self._recovered_source_event_count: int | None = None
        self._recovery: dict | None = None
        self._stage_summaries: dict[str, dict] = {}

    @property
    def enabled(self) -> bool:
        return self.events_path is not None or self.report_path is not None

    @property
    def finished(self) -> bool:
        return self._status in _TERMINAL_STATUSES

    @property
    def active_stage_count(self) -> int:
        with self._lock:
            return sum(len(starts) for starts in self._active_stages.values())

    def _publish_events(self) -> None:
        if self.events_path is not None:
            _atomic_write_private_jsonl(self.events_path, self._events)

    @classmethod
    def recover(cls, operation: str, *, run_id: str,
                events_path: Path | None = None,
                report_path: Path | None = None,
                wall_clock=time.time,
                monotonic_clock=time.monotonic) -> "RunTelemetry":
        """Recover a killed worker's valid event stream after it has exited."""
        telemetry = cls(
            operation, run_id=run_id, events_path=events_path,
            report_path=report_path, wall_clock=wall_clock,
            monotonic_clock=monotonic_clock)
        if events_path is None:
            telemetry._recovered_source_event_count = 0
            telemetry.start()
            return telemetry
        events = _load_recovery_events(
            Path(events_path), operation=telemetry.operation,
            run_id=telemetry.run_id)
        telemetry._recovered_source_event_count = len(events)
        if not events:
            telemetry.start()
            return telemetry
        telemetry._events = events
        telemetry.parent_run_id = events[0].get("parent_run_id")
        telemetry._started_at = float(events[0]["timestamp"])
        now_wall = _finite_clock_value(
            telemetry._wall_clock(), label="wall clock")
        now_monotonic = _finite_clock_value(
            telemetry._monotonic(), label="monotonic clock")
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
        for event in events:
            stage = event["stage"]
            if stage == "run":
                continue
            aggregate = _copy_stage_summary(
                telemetry._stage_summaries.get(
                    stage, _empty_stage_summary()))
            _apply_stage_event(aggregate, event)
            telemetry._stage_summaries[stage] = aggregate
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
                telemetry._finished_monotonic = _finite_clock_value(
                    telemetry._monotonic(), label="monotonic clock")
            telemetry._failure = terminal.get("diagnostic")
            telemetry._recovery = _terminal_recovery(terminal)
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
        if status not in _EVENT_STATUSES:
            raise ValueError("invalid run event status")
        with self._lock:
            if self._started_at is None:
                raise RuntimeError("run telemetry has not been started")
            if self.finished:
                raise RuntimeError("run telemetry is already finished")
            normalized_metrics = _safe_metrics(metrics)
            event = {
                "schema_version": EVENT_SCHEMA_VERSION,
                "sequence": len(self._events) + 1,
                "timestamp": _finite_clock_value(
                    self._wall_clock(), label="wall clock"),
                "run_id": self.run_id,
                "operation": self.operation,
                "stage": stage,
                "status": status,
                "metrics": normalized_metrics,
            }
            if self.parent_run_id is not None:
                event["parent_run_id"] = self.parent_run_id
            if diagnostic is not None:
                event["diagnostic"] = dict(diagnostic)
            aggregate = None
            if stage != "run":
                aggregate = _copy_stage_summary(
                    self._stage_summaries.get(
                        stage, _empty_stage_summary()))
                _apply_stage_event(aggregate, event)
            self._events.append(event)
            try:
                self._publish_events()
            except BaseException:
                self._events.pop()
                raise
            if aggregate is not None:
                self._stage_summaries[stage] = aggregate
            return dict(event)

    def start(self) -> str:
        with self._lock:
            if self._started_at is not None:
                return self.run_id
            self._started_at = _finite_clock_value(
                self._wall_clock(), label="wall clock")
            self._started_monotonic = _finite_clock_value(
                self._monotonic(), label="monotonic clock")
            try:
                start_event = self._append("run", "started")
            except BaseException:
                self._started_at = None
                self._started_monotonic = None
                raise
            self._started_at = float(start_event["timestamp"])
            if self.report_path is not None:
                self.persist_report()
            return self.run_id

    def stage_started(self, stage: str, *, metrics: dict | None = None) -> None:
        stage = _validate_identifier(stage, label="stage")
        with self._lock:
            started = _finite_clock_value(
                self._monotonic(), label="monotonic clock")
            self._append(stage, "started", metrics=metrics)
            self._active_stages.setdefault(stage, []).append(started)

    def stage_finished(self, stage: str, *, status: str = "completed",
                       metrics: dict | None = None) -> None:
        if status not in {"completed", "skipped"}:
            raise ValueError(f"invalid successful stage status: {status}")
        stage = _validate_identifier(stage, label="stage")
        combined = dict(metrics or {})
        with self._lock:
            active = self._active_stages.get(stage, [])
            started = active[-1] if active else None
            if started is not None and "duration_ms" not in combined:
                finished = _finite_clock_value(
                    self._monotonic(), label="monotonic clock")
                combined["duration_ms"] = max(
                    0.0, (finished - started) * 1000)
            self._append(stage, status, metrics=combined)
            if active:
                active.pop()
            if not active:
                self._active_stages.pop(stage, None)

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
            started = active[-1] if active else None
            if started is not None and "duration_ms" not in combined:
                finished = _finite_clock_value(
                    self._monotonic(), label="monotonic clock")
                combined["duration_ms"] = max(
                    0.0, (finished - started) * 1000)
            diagnostic = failure_diagnostic(exc)
            self._append(stage, status, metrics=combined,
                         diagnostic=diagnostic)
            if active:
                active.pop()
            if not active:
                self._active_stages.pop(stage, None)
            self._failure = diagnostic

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

    def record_recovery(self, *, active_stages_closed: int,
                        stage_closure_ms: float) -> None:
        """Attach one content-free interrupted-run recovery measurement."""
        if self._recovered_source_event_count is None:
            raise RuntimeError("telemetry was not created through recovery")
        if (isinstance(active_stages_closed, bool)
                or not isinstance(active_stages_closed, int)
                or active_stages_closed < 0):
            raise ValueError("active_stages_closed must be non-negative")
        normalized = _safe_metrics({"stage_closure_ms": stage_closure_ms})
        with self._lock:
            if self._recovery is not None:
                raise RuntimeError("run recovery was already recorded")
            self._recovery = {
                "source_event_count": self._recovered_source_event_count,
                "active_stages_closed": active_stages_closed,
                "stage_closure_ms": normalized["stage_closure_ms"],
            }

    def stage_observation(self, stage: str, *,
                          metrics: dict | None = None) -> None:
        """Record aggregate component metrics gathered outside a timed span."""
        self._append(stage, "completed", metrics=metrics)

    def _stage_summary(self) -> dict:
        return _render_stage_summaries(self._stage_summaries)

    def report_payload(self) -> dict:
        with self._lock:
            elapsed_ms = None
            if self._started_monotonic is not None:
                observed_monotonic = (
                    self._finished_monotonic
                    if self._finished_monotonic is not None
                    else _finite_clock_value(
                        self._monotonic(), label="monotonic clock"))
                elapsed_ms = round(
                    max(0.0, (
                        observed_monotonic - self._started_monotonic) * 1000),
                    3)
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
            if self._recovery is not None:
                payload["recovery"] = dict(self._recovery)
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
                return self.persist_report()
            diagnostic = failure_diagnostic(exc) if exc is not None else None
            if status == "succeeded":
                diagnostic = None
            elif self._failure is not None:
                diagnostic = dict(self._failure)
            finished_monotonic = _finite_clock_value(
                self._monotonic(), label="monotonic clock")
            elapsed_ms = None
            if self._started_monotonic is not None:
                elapsed_ms = max(
                    0.0,
                    (finished_monotonic - self._started_monotonic) * 1000)
            terminal_metrics = (
                {"duration_ms": elapsed_ms}
                if elapsed_ms is not None else {})
            if self._recovery is not None:
                terminal_metrics.update({
                    _RECOVERY_EVENT_METRICS[0]: self._recovery[
                        "source_event_count"],
                    _RECOVERY_EVENT_METRICS[1]: self._recovery[
                        "active_stages_closed"],
                    _RECOVERY_EVENT_METRICS[2]: self._recovery[
                        "stage_closure_ms"],
                })
            terminal_event = self._append(
                "run", status,
                metrics=terminal_metrics,
                diagnostic=diagnostic)
            self._status = status
            self._finished_at = float(terminal_event["timestamp"])
            self._finished_monotonic = finished_monotonic
            self._failure = None if status == "succeeded" else diagnostic
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
        report_path: Path | None = None,
        wall_clock=time.time,
        monotonic_clock=time.monotonic) -> dict:
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
                    and type(existing.get("schema_version")) is int
                    and existing["schema_version"]
                    in SUPPORTED_REPORT_SCHEMA_VERSIONS
                    and existing.get("run_id") == run_id
                    and existing.get("operation") == operation
                    and existing.get("status") in _TERMINAL_STATUSES):
                return existing
        except (OSError, UnicodeError, json.JSONDecodeError):
            pass
    recovery_started = _finite_clock_value(
        monotonic_clock(), label="monotonic clock")
    telemetry = RunTelemetry.recover(
        operation, run_id=run_id, events_path=events_path,
        report_path=report_path, wall_clock=wall_clock,
        monotonic_clock=monotonic_clock)
    if telemetry.finished:
        return telemetry.persist_report()
    active_stages_closed = telemetry.active_stage_count
    telemetry.terminate_active_stages(status, exc)
    telemetry.record_recovery(
        active_stages_closed=active_stages_closed,
        stage_closure_ms=max(
            0.0, (_finite_clock_value(
                monotonic_clock(), label="monotonic clock")
                - recovery_started) * 1000),
    )
    return telemetry.finish(status, exc=exc)
