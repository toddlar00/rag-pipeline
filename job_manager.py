"""Legacy durable-job import and executable facade.

The coordination engine lives in :mod:`job_coordination` so application
services can depend on it without importing this command shell.  Public names
remain object-identical aliases and direct ``python job_manager.py`` execution
continues to dispatch the manager child and reconciliation CLI.
"""

from __future__ import annotations

from collections.abc import Sequence

import job_coordination as _coordination
from job_coordination_contracts import (
    JobManagerCorruptError,
    JobManagerError,
    JobManagerLaunchError,
    JobManagerValidationError,
    LaunchResult,
    MANAGER_SCHEMA_VERSION,
    ManagerResult,
)


JOB_ATTEMPT_TOKEN_ENV = _coordination.JOB_ATTEMPT_TOKEN_ENV
JOB_ID_ENV = _coordination.JOB_ID_ENV
JOB_ROOT_ENV = _coordination.JOB_ROOT_ENV
OUTPUT_ROOT_ENV = _coordination.OUTPUT_ROOT_ENV
RuntimeMetadata = _coordination.RuntimeMetadata
launch_detached = _coordination.launch_detached
probe_process_identity = _coordination.probe_process_identity
process_birth_identity = _coordination.process_birth_identity
reconcile_all_jobs = _coordination.reconcile_all_jobs
reconcile_job = _coordination.reconcile_job
run_job = _coordination.run_job

# Preserve the one private seam used by the legacy UI fault drill.  Engine tests
# target job_coordination directly so private implementation ownership is clear.
subprocess = _coordination.subprocess


def main(argv: Sequence[str] | None = None) -> int:
    """Delegate the stable executable surface to the coordination engine."""
    return _coordination.main(argv)


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
    "MANAGER_SCHEMA_VERSION",
    "ManagerResult",
    "RuntimeMetadata",
    "launch_detached",
    "main",
    "probe_process_identity",
    "process_birth_identity",
    "reconcile_all_jobs",
    "reconcile_job",
    "run_job",
]
