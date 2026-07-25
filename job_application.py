"""Inward durable-job capabilities shared by application adapters.

The command-line facade and UI consume one immutable generation without
importing one another or treating ``job_manager`` as a service locator.
Durable execution and state ownership remain in their inward modules; the
service retains its stricter service-only coordination and store policies.
"""

from __future__ import annotations

from collections.abc import Callable
from dataclasses import dataclass

import job_coordination
import job_coordination_contracts
import job_runtime


JobStoreFactory = Callable[..., job_runtime.JobStore]
JobOperation = Callable[..., object]


@dataclass(frozen=True, slots=True)
class JobApplicationBinding:
    """Atomic durable-job capabilities used by application surfaces."""

    job_store_factory: JobStoreFactory
    launch_detached: JobOperation
    reconcile_job: JobOperation
    reconcile_all_jobs: JobOperation
    manager_error_type: type[Exception]

    def __post_init__(self) -> None:
        for name in (
                "job_store_factory", "launch_detached", "reconcile_job",
                "reconcile_all_jobs"):
            if not callable(getattr(self, name)):
                raise TypeError(f"{name} must be callable")
        if (not isinstance(self.manager_error_type, type)
                or not issubclass(
                    self.manager_error_type,
                    job_coordination_contracts.JobManagerError)):
            raise TypeError(
                "manager_error_type must be a JobManagerError type")


_DEFAULT_JOB_APPLICATION_BINDING = JobApplicationBinding(
    job_store_factory=job_runtime.JobStore,
    launch_detached=job_coordination.launch_detached,
    reconcile_job=job_coordination.reconcile_job,
    reconcile_all_jobs=job_coordination.reconcile_all_jobs,
    manager_error_type=job_coordination_contracts.JobManagerError,
)


def default_job_application_binding() -> JobApplicationBinding:
    """Return the immutable production job-application generation."""
    return _DEFAULT_JOB_APPLICATION_BINDING


__all__ = [
    "JobApplicationBinding",
    "JobOperation",
    "JobStoreFactory",
    "default_job_application_binding",
]
