"""Strict, redacted outcome reports for durable background-job attempts.

The manager is the sole writer. Reports intentionally exclude arguments,
paths, process identities, attempt tokens, logs, and exception messages while
preserving enough lifecycle timing to audit cancellation and recovery.
"""

from __future__ import annotations

import math
import re
from dataclasses import dataclass, fields, replace
from pathlib import Path
from typing import Any

import storage_policy


SCHEMA_VERSION = 1
KIND = "job_attempt_outcome"
MAX_REPORT_BYTES = 64 * 1024
MAX_TIMESTAMP = 253_402_300_799.0

_JOB_ID = re.compile(r"^[0-9a-f]{32}$")
_IDENTIFIER = re.compile(r"^[A-Za-z0-9][A-Za-z0-9_.-]{0,127}$")
_STATUSES = frozenset({
    "queued", "starting", "running", "cancel_requested",
    "succeeded", "partial", "failed", "cancelled", "interrupted",
    "orphaned",
})
_TERMINAL_STATUSES = frozenset({
    "succeeded", "partial", "failed", "cancelled", "interrupted",
    "orphaned",
})
_TRIGGERS = frozenset({
    "normal", "cancel", "timeout", "recovery", "manager_error",
})
_FINALIZERS = frozenset({"manager", "recovery"})
_RECOVERY_ACTIONS = frozenset({
    "none", "pending", "unstarted_terminalized", "confirmed_gone",
    "terminated_exact_worker", "refused_identity", "cleanup_unconfirmed",
    "not_required",
})
_RECOVERY_RECEIPT_ACTIONS = frozenset({
    "unstarted_terminalized", "confirmed_gone", "terminated_exact_worker",
})
_WORKER_TELEMETRY_STATUSES = frozenset({
    "succeeded", "partial", "failed", "cancelled",
})
_TERMINAL_REASONS = frozenset({
    "completed", "cancelled", "cleanup_unconfirmed", "outcome_unconfirmed",
    "timeout", "cancellation_failed", "worker_failed",
    "manager_start_missing", "execution_validation_failed",
})
_TIMESTAMP_FIELDS = (
    "submitted_at",
    "manager_started_at",
    "worker_started_at",
    "cancel_requested_at",
    "cancel_observed_at",
    "recovery_started_at",
    "cleanup_confirmed_at",
    "finished_at",
)
_DURATION_FIELDS = (
    "total",
    "dispatch",
    "startup",
    "worker",
    "cancel_observation",
    "cancel_completion",
    "recovery",
)
_OUTCOME_FIELDS = (
    "trigger",
    "finalized_by",
    "worker_started",
    "cancel_requested",
    "cancel_observed",
    "cleanup_confirmed",
    "reconciled",
    "recovery_action",
    "runtime_missing",
    "report_repaired",
    "timing_reconstructed",
    "worker_telemetry_status",
    "terminal_reason",
    "exit_code",
    "manager_error",
)


def _valid_timestamp(value: Any, *, optional: bool) -> float | None:
    if value is None and optional:
        return None
    if (isinstance(value, bool) or not isinstance(value, (int, float))
            or not math.isfinite(float(value)) or float(value) < 0
            or float(value) > MAX_TIMESTAMP):
        raise ValueError("attempt report timestamp is invalid")
    return float(value)


def _duration_ms(later: float | None,
                 earlier: float | None) -> float | None:
    if later is None or earlier is None:
        return None
    duration = round((later - earlier) * 1000, 3)
    if not math.isfinite(duration) or duration < 0:
        raise ValueError("attempt report duration is invalid")
    return duration


@dataclass(frozen=True, slots=True)
class AttemptReport:
    """One bounded manager-owned lifecycle snapshot."""

    job_id: str
    attempt_number: int
    run_id: str
    operation: str
    report_revision: int
    status: str
    submitted_at: float
    updated_at: float
    manager_started_at: float | None = None
    worker_started_at: float | None = None
    cancel_requested_at: float | None = None
    cancel_observed_at: float | None = None
    recovery_started_at: float | None = None
    cleanup_confirmed_at: float | None = None
    finished_at: float | None = None
    trigger: str = "normal"
    finalized_by: str = "manager"
    worker_started: bool = False
    cancel_requested: bool = False
    cancel_observed: bool = False
    cleanup_confirmed: bool | None = None
    reconciled: bool = False
    recovery_action: str = "none"
    runtime_missing: bool = False
    report_repaired: bool = False
    timing_reconstructed: bool = False
    worker_telemetry_status: str | None = None
    terminal_reason: str | None = None
    exit_code: int | None = None
    manager_error: bool = False

    def __post_init__(self) -> None:
        if not isinstance(self.job_id, str) or not _JOB_ID.fullmatch(
                self.job_id):
            raise ValueError("attempt report job ID is invalid")
        if (type(self.attempt_number) is not int
                or self.attempt_number < 1):
            raise ValueError("attempt report attempt number is invalid")
        for value, label in (
                (self.run_id, "run ID"), (self.operation, "operation")):
            if not isinstance(value, str) or not _IDENTIFIER.fullmatch(value):
                raise ValueError(f"attempt report {label} is invalid")
        if type(self.report_revision) is not int or self.report_revision < 1:
            raise ValueError("attempt report revision is invalid")
        if not isinstance(self.status, str) or self.status not in _STATUSES:
            raise ValueError("attempt report status is invalid")
        if not isinstance(self.trigger, str) or self.trigger not in _TRIGGERS:
            raise ValueError("attempt report trigger is invalid")
        if (not isinstance(self.finalized_by, str)
                or self.finalized_by not in _FINALIZERS):
            raise ValueError("attempt report finalizer is invalid")
        if (not isinstance(self.recovery_action, str)
                or self.recovery_action not in _RECOVERY_ACTIONS):
            raise ValueError("attempt report recovery action is invalid")
        if (self.worker_telemetry_status is not None
                and (not isinstance(self.worker_telemetry_status, str)
                     or self.worker_telemetry_status
                     not in _WORKER_TELEMETRY_STATUSES)):
            raise ValueError("attempt report worker telemetry status is invalid")
        if (self.terminal_reason is not None
                and (not isinstance(self.terminal_reason, str)
                     or self.terminal_reason not in _TERMINAL_REASONS)):
            raise ValueError("attempt report terminal reason is invalid")
        if (self.exit_code is not None
                and (type(self.exit_code) is not int)):
            raise ValueError("attempt report exit code is invalid")
        for name in (
                "worker_started", "cancel_requested", "cancel_observed",
                "reconciled", "runtime_missing", "report_repaired",
                "timing_reconstructed", "manager_error"):
            if type(getattr(self, name)) is not bool:
                raise ValueError("attempt report outcome flag is invalid")
        if (self.cleanup_confirmed is not None
                and type(self.cleanup_confirmed) is not bool):
            raise ValueError("attempt report cleanup outcome is invalid")

        timestamps = {
            name: _valid_timestamp(
                getattr(self, name), optional=name != "submitted_at")
            for name in _TIMESTAMP_FIELDS
        }
        updated_at = _valid_timestamp(self.updated_at, optional=False)
        assert updated_at is not None
        submitted_at = timestamps["submitted_at"]
        assert submitted_at is not None
        if any(
                value is not None and value < submitted_at
                for value in timestamps.values()):
            raise ValueError("attempt report timestamp predates submission")
        if any(
                value is not None and value > updated_at
                for value in timestamps.values()):
            raise ValueError("attempt report update time predates an event")
        ordered_pairs = (
            ("manager_started_at", "worker_started_at"),
            ("manager_started_at", "finished_at"),
            ("worker_started_at", "finished_at"),
            ("cancel_requested_at", "cancel_observed_at"),
            ("cancel_requested_at", "finished_at"),
            ("cancel_observed_at", "finished_at"),
            ("recovery_started_at", "finished_at"),
            ("cleanup_confirmed_at", "finished_at"),
            ("worker_started_at", "recovery_started_at"),
            ("manager_started_at", "recovery_started_at"),
            ("cancel_observed_at", "recovery_started_at"),
            ("worker_started_at", "cleanup_confirmed_at"),
            ("cancel_observed_at", "cleanup_confirmed_at"),
            ("recovery_started_at", "cleanup_confirmed_at"),
        )
        for earlier_name, later_name in ordered_pairs:
            earlier = timestamps[earlier_name]
            later = timestamps[later_name]
            if earlier is not None and later is not None and later < earlier:
                raise ValueError("attempt report timestamps are out of order")

        terminal = self.status in _TERMINAL_STATUSES
        if terminal != (self.finished_at is not None):
            raise ValueError("attempt report terminal timestamp is invalid")
        if terminal != (self.terminal_reason is not None):
            raise ValueError("attempt report terminal reason is invalid")
        if terminal != (self.cleanup_confirmed is not None):
            raise ValueError("attempt report cleanup outcome is invalid")
        if self.cleanup_confirmed is True:
            if self.cleanup_confirmed_at is None:
                raise ValueError("confirmed cleanup requires a timestamp")
        elif self.cleanup_confirmed_at is not None:
            raise ValueError("cleanup timestamp requires confirmed cleanup")
        if self.worker_started != (self.worker_started_at is not None):
            raise ValueError("worker start timestamp is inconsistent")
        if self.cancel_requested != (self.cancel_requested_at is not None):
            raise ValueError("cancel request timestamp is inconsistent")
        if self.cancel_observed != (self.cancel_observed_at is not None):
            raise ValueError("cancel observation timestamp is inconsistent")
        if self.cancel_observed and not self.cancel_requested:
            raise ValueError("cancel observation requires a request")
        if not terminal and (self.exit_code is not None or self.manager_error):
            raise ValueError("active attempt report has a terminal outcome")
        if self.status == "starting" and self.manager_started_at is None:
            raise ValueError("starting attempt lacks a manager timestamp")
        if self.status == "running" and not self.worker_started:
            raise ValueError("running attempt lacks a worker timestamp")
        if self.status == "cancel_requested" and not self.cancel_observed:
            raise ValueError("cancel-requested attempt lacks an observation")
        if (not terminal and self.status != "cancel_requested"
                and self.trigger not in {"normal", "recovery"}):
            raise ValueError("active attempt trigger is inconsistent")
        if (not terminal and self.status == "cancel_requested"
                and self.trigger not in {"cancel", "recovery"}):
            raise ValueError("cancel-requested trigger is inconsistent")
        if terminal:
            expected_reasons = {
                "succeeded": {"completed"},
                "partial": {"completed"},
                "failed": {
                    "timeout", "cancellation_failed", "worker_failed",
                    "manager_start_missing", "execution_validation_failed",
                },
                "cancelled": {"cancelled"},
                "interrupted": {"outcome_unconfirmed"},
                "orphaned": {"cleanup_unconfirmed"},
            }
            if self.terminal_reason not in expected_reasons[self.status]:
                raise ValueError("attempt report terminal outcome is inconsistent")
            if self.cleanup_confirmed != (self.status != "orphaned"):
                raise ValueError("attempt report cleanup outcome is inconsistent")
            if (self.status == "cancelled"
                    or self.terminal_reason == "cancellation_failed"):
                if not self.cancel_observed:
                    raise ValueError(
                        "cancellation outcome requires observed cancellation")
                if self.trigger not in {"cancel", "recovery"}:
                    raise ValueError(
                        "cancellation outcome has an inconsistent trigger")
            if self.trigger == "cancel" and not self.cancel_observed:
                raise ValueError("cancel trigger requires observed cancellation")
            if self.trigger == "timeout" and not (
                    self.status == "failed"
                    and self.terminal_reason == "timeout"
                    and self.exit_code == 124):
                raise ValueError("timeout trigger is inconsistent")
            if self.terminal_reason == "timeout" and self.trigger != "timeout":
                raise ValueError("timeout outcome is inconsistent")
            if self.trigger == "manager_error" and not self.manager_error:
                raise ValueError("manager-error trigger lacks an error outcome")
            if (self.manager_error
                    and self.trigger not in {"manager_error", "recovery"}):
                raise ValueError("manager error outcome is inconsistent")
            if (self.status in {"succeeded", "partial"}
                    and self.worker_telemetry_status is not None
                    and self.worker_telemetry_status != self.status):
                raise ValueError("worker telemetry outcome is inconsistent")
        if self.recovery_action == "none":
            if (self.recovery_started_at is not None or self.reconciled
                    or self.runtime_missing or self.report_repaired):
                raise ValueError("non-recovery attempt has recovery state")
            if self.timing_reconstructed:
                raise ValueError("non-recovery attempt has reconstructed timing")
            if self.trigger == "recovery":
                raise ValueError("recovery trigger requires a recovery action")
            if self.finalized_by != "manager":
                raise ValueError("non-recovery attempt has a recovery finalizer")
        elif self.recovery_started_at is None:
            raise ValueError("recovery action requires a start timestamp")
        elif self.finalized_by != "recovery":
            raise ValueError("recovery action requires a recovery finalizer")
        elif self.recovery_action == "pending":
            if self.reconciled or terminal:
                raise ValueError("pending recovery cannot be reconciled")
        elif (not terminal
              and self.recovery_action not in _RECOVERY_RECEIPT_ACTIONS):
            raise ValueError(
                "active recovery receipt must prove successful cleanup")
        elif terminal != self.reconciled:
            raise ValueError(
                "recorded recovery must be active or terminal and reconciled")

    def durations_ms(self) -> dict[str, float | None]:
        return {
            "total": _duration_ms(self.finished_at, self.submitted_at),
            "dispatch": _duration_ms(
                self.manager_started_at, self.submitted_at),
            "startup": _duration_ms(
                self.worker_started_at, self.manager_started_at),
            "worker": _duration_ms(self.finished_at, self.worker_started_at),
            "cancel_observation": _duration_ms(
                self.cancel_observed_at, self.cancel_requested_at),
            "cancel_completion": _duration_ms(
                self.finished_at, self.cancel_requested_at),
            "recovery": _duration_ms(
                self.finished_at, self.recovery_started_at),
        }

    def as_payload(self) -> dict:
        timestamps = {
            name: getattr(self, name) for name in _TIMESTAMP_FIELDS
        }
        outcome = {name: getattr(self, name) for name in _OUTCOME_FIELDS}
        return {
            "schema_version": SCHEMA_VERSION,
            "kind": KIND,
            "report_revision": self.report_revision,
            "job_id": self.job_id,
            "attempt_number": self.attempt_number,
            "run_id": self.run_id,
            "operation": self.operation,
            "status": self.status,
            "updated_at": self.updated_at,
            "timestamps": timestamps,
            "durations_ms": self.durations_ms(),
            "outcome": outcome,
        }


def new_report(*, job_id: str, attempt_number: int, run_id: str,
               operation: str, status: str, submitted_at: float,
               observed_at: float,
               manager_started_at: float | None = None) -> AttemptReport:
    """Create the first exact snapshot for one attempt."""
    submitted = _valid_timestamp(submitted_at, optional=False)
    observed = _valid_timestamp(observed_at, optional=False)
    assert submitted is not None and observed is not None
    observed = max(submitted, observed)
    manager_started_value = _valid_timestamp(
        manager_started_at, optional=True)
    manager_started = (
        max(submitted, manager_started_value)
        if manager_started_value is not None else None)
    updated = max(
        observed,
        manager_started if manager_started is not None else submitted,
    )
    return AttemptReport(
        job_id=job_id,
        attempt_number=attempt_number,
        run_id=run_id,
        operation=operation,
        report_revision=1,
        status=status,
        submitted_at=submitted,
        updated_at=updated,
        manager_started_at=manager_started,
    )


def advance(report: AttemptReport, *, observed_at: float,
            **changes) -> AttemptReport:
    """Return the next monotonic atomic snapshot."""
    if type(report) is not AttemptReport:
        raise TypeError("attempt report must be an AttemptReport")
    allowed = {item.name for item in fields(AttemptReport)} - {
        "job_id", "attempt_number", "run_id", "operation",
        "report_revision", "submitted_at", "updated_at",
    }
    unknown = set(changes) - allowed
    if unknown:
        raise ValueError("attempt report update contains unsupported fields")
    if report.status in _TERMINAL_STATUSES:
        raise ValueError("terminal attempt reports are immutable")
    next_status = changes.get("status", report.status)
    legal_statuses = {
        "queued": {
            "queued", "starting", "cancel_requested", "failed", "orphaned",
        },
        "starting": {
            "starting", "running", "cancel_requested", "failed",
            "interrupted", "orphaned",
        },
        "running": {
            "running", "cancel_requested", "succeeded", "partial", "failed",
            "interrupted", "orphaned",
        },
        "cancel_requested": {
            "cancel_requested", "cancelled", "succeeded", "partial", "failed",
            "interrupted", "orphaned",
        },
    }
    if (not isinstance(next_status, str)
            or next_status not in legal_statuses[report.status]):
        raise ValueError("attempt report status transition is invalid")
    write_once = {
        "manager_started_at", "worker_started_at", "cancel_requested_at",
        "cancel_observed_at", "recovery_started_at", "cleanup_confirmed_at",
        "finished_at", "cleanup_confirmed", "worker_telemetry_status",
        "terminal_reason", "exit_code",
    }
    for name in write_once:
        old = getattr(report, name)
        if old is not None and name in changes and changes[name] != old:
            raise ValueError("attempt report milestone is immutable")
    monotonic_flags = {
        "worker_started", "cancel_requested", "cancel_observed", "reconciled",
        "runtime_missing", "report_repaired", "manager_error",
        "timing_reconstructed",
    }
    if any(
            getattr(report, name) is True and changes.get(name) is not True
            for name in monotonic_flags if name in changes):
        raise ValueError("attempt report outcome cannot be cleared")
    next_trigger = changes.get("trigger", report.trigger)
    if next_trigger != report.trigger:
        discovered_terminal_cause = (
            report.trigger == "recovery"
            and next_status in _TERMINAL_STATUSES
            and next_trigger in {"cancel", "timeout", "manager_error"}
        )
        if (report.trigger != "normal" and next_trigger != "recovery"
                and not discovered_terminal_cause):
            raise ValueError("attempt report trigger cannot be rewritten")
    next_finalizer = changes.get("finalized_by", report.finalized_by)
    if (next_finalizer != report.finalized_by
            and not (report.finalized_by == "manager"
                     and next_finalizer == "recovery")):
        raise ValueError("attempt report finalizer cannot be rewritten")
    next_recovery = changes.get("recovery_action", report.recovery_action)
    allowed_recovery = {
        "none": {"none", "pending"},
        "pending": _RECOVERY_ACTIONS - {"none"},
    }
    if report.recovery_action not in {"none", "pending"}:
        allowed_recovery[report.recovery_action] = {report.recovery_action}
    if (report.recovery_action not in allowed_recovery
            or next_recovery not in allowed_recovery[report.recovery_action]):
        raise ValueError("attempt report recovery action cannot be rewritten")
    observed = _valid_timestamp(observed_at, optional=False)
    assert observed is not None
    effective_observed = max(report.updated_at, observed)
    candidate_times = [report.updated_at, effective_observed]
    for name, value in changes.items():
        if name not in _TIMESTAMP_FIELDS:
            continue
        candidate = _valid_timestamp(value, optional=True)
        if candidate is not None:
            if candidate > effective_observed:
                raise ValueError("attempt report event follows observation time")
            candidate_times.append(candidate)
    return replace(
        report,
        report_revision=report.report_revision + 1,
        updated_at=max(candidate_times),
        **changes,
    )


def repair_terminal_cancel_request(
        report: AttemptReport, *, cancel_requested_at: float,
        observed_at: float) -> AttemptReport:
    """Merge a late durable cancel marker into an otherwise final snapshot.

    A cancel requester can validate a nonterminal state immediately before the
    manager commits its terminal transition, then publish the marker after that
    transition. Normal terminal reports remain immutable through ``advance``;
    this narrow repair records only the newly authoritative marker and marks
    the timing as reconstructed while preserving every prior outcome fact.
    """
    if type(report) is not AttemptReport:
        raise TypeError("attempt report must be an AttemptReport")
    if report.status not in _TERMINAL_STATUSES:
        raise ValueError("late cancel repair requires a terminal report")
    requested = _valid_timestamp(cancel_requested_at, optional=False)
    observed = _valid_timestamp(observed_at, optional=False)
    assert requested is not None and observed is not None
    if requested < report.submitted_at:
        raise ValueError("attempt report cancel request predates submission")
    if report.cancel_requested_at is not None:
        if report.cancel_requested_at != requested:
            raise ValueError("attempt report cancel request is immutable")
        return report

    assert report.finished_at is not None
    finished_at = max(report.finished_at, requested)
    recovery_started_at = report.recovery_started_at
    recovery_action = report.recovery_action
    if recovery_action == "none":
        recovery_started_at = (
            report.cleanup_confirmed_at or report.finished_at)
        recovery_action = "not_required"
    return replace(
        report,
        report_revision=report.report_revision + 1,
        updated_at=max(report.updated_at, observed, finished_at),
        cancel_requested=True,
        cancel_requested_at=requested,
        recovery_started_at=recovery_started_at,
        finished_at=finished_at,
        finalized_by="recovery",
        reconciled=True,
        recovery_action=recovery_action,
        report_repaired=True,
        timing_reconstructed=True,
    )


def parse_report(payload: Any, *, expected_job_id: str,
                 expected_attempt_number: int,
                 expected_run_id: str,
                 expected_operation: str) -> AttemptReport:
    """Validate an exact report payload and its expected attempt identity."""
    if not isinstance(payload, dict) or set(payload) != {
            "schema_version", "kind", "report_revision", "job_id",
            "attempt_number", "run_id", "operation", "status", "updated_at",
            "timestamps", "durations_ms", "outcome"}:
        raise ValueError("attempt report has missing or unknown fields")
    if (type(payload["schema_version"]) is not int
            or payload["schema_version"] != SCHEMA_VERSION
            or payload["kind"] != KIND):
        raise ValueError("attempt report schema is unsupported")
    timestamps = payload["timestamps"]
    durations = payload["durations_ms"]
    outcome = payload["outcome"]
    if (not isinstance(timestamps, dict)
            or set(timestamps) != set(_TIMESTAMP_FIELDS)):
        raise ValueError("attempt report timestamps are incompatible")
    if (not isinstance(durations, dict)
            or set(durations) != set(_DURATION_FIELDS)):
        raise ValueError("attempt report durations are incompatible")
    if (not isinstance(outcome, dict)
            or set(outcome) != set(_OUTCOME_FIELDS)):
        raise ValueError("attempt report outcome is incompatible")
    report = AttemptReport(
        job_id=payload["job_id"],
        attempt_number=payload["attempt_number"],
        run_id=payload["run_id"],
        operation=payload["operation"],
        report_revision=payload["report_revision"],
        status=payload["status"],
        submitted_at=timestamps["submitted_at"],
        updated_at=payload["updated_at"],
        manager_started_at=timestamps["manager_started_at"],
        worker_started_at=timestamps["worker_started_at"],
        cancel_requested_at=timestamps["cancel_requested_at"],
        cancel_observed_at=timestamps["cancel_observed_at"],
        recovery_started_at=timestamps["recovery_started_at"],
        cleanup_confirmed_at=timestamps["cleanup_confirmed_at"],
        finished_at=timestamps["finished_at"],
        **outcome,
    )
    if (report.job_id != expected_job_id
            or report.attempt_number != expected_attempt_number
            or report.run_id != expected_run_id
            or report.operation != expected_operation):
        raise ValueError("attempt report does not bind the expected attempt")
    if report.as_payload() != payload:
        raise ValueError("attempt report derived durations are incompatible")
    return report


def publish(path: Path, report: AttemptReport) -> None:
    """Atomically publish a private report snapshot."""
    if type(report) is not AttemptReport:
        raise TypeError("attempt report must be an exact AttemptReport")
    storage_policy.atomic_write_private_json(path, report.as_payload())


__all__ = [
    "AttemptReport",
    "KIND",
    "MAX_REPORT_BYTES",
    "MAX_TIMESTAMP",
    "SCHEMA_VERSION",
    "advance",
    "new_report",
    "parse_report",
    "publish",
    "repair_terminal_cancel_request",
]
