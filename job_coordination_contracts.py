"""Dependency-light contracts for durable job coordination.

The durable coordination engine and its legacy executable facade share these
object-identical result, error, and capability contracts.  Keeping the binding
here lets application services snapshot launch, reconciliation, and integrity
classification as one immutable generation.
"""

from __future__ import annotations

from collections.abc import Callable
from dataclasses import dataclass


MANAGER_SCHEMA_VERSION = 1
LaunchFn = Callable[..., object]
ReconcileFn = Callable[..., object]


class JobManagerError(Exception):
    """Base class for detached manager failures."""


class JobManagerValidationError(JobManagerError, ValueError):
    """Raised before unsafe manager input is used."""


class JobManagerCorruptError(JobManagerError):
    """Raised when private attempt metadata cannot be trusted."""


class JobManagerLaunchError(JobManagerError):
    """Raised when a detached manager cannot prove its ready handshake."""


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
class ServiceJobCoordinationBinding:
    """Atomic durable-job capabilities consumed by the service runtime."""

    launch_detached: LaunchFn
    reconcile_job: ReconcileFn
    corrupt_error_type: type[Exception]

    def __post_init__(self) -> None:
        if not callable(self.launch_detached):
            raise TypeError("launch_detached must be callable")
        if not callable(self.reconcile_job):
            raise TypeError("reconcile_job must be callable")
        if (not isinstance(self.corrupt_error_type, type)
                or not issubclass(
                    self.corrupt_error_type, JobManagerCorruptError)):
            raise TypeError(
                "corrupt_error_type must be a JobManagerCorruptError type")


# These contracts historically lived in job_manager.py.  Preserve their public
# import/pickle identity while the executable module becomes a thin facade.
for _legacy_type in (
        JobManagerError, JobManagerValidationError,
        JobManagerCorruptError, JobManagerLaunchError,
        ManagerResult, LaunchResult):
    _legacy_type.__module__ = "job_manager"


__all__ = [
    "JobManagerCorruptError",
    "JobManagerError",
    "JobManagerLaunchError",
    "JobManagerValidationError",
    "LaunchFn",
    "LaunchResult",
    "MANAGER_SCHEMA_VERSION",
    "ManagerResult",
    "ReconcileFn",
    "ServiceJobCoordinationBinding",
]
