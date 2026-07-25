"""Concrete supervision policy shared by runtime composition consumers.

The process loop remains in :mod:`process_supervision`.  This module binds it
to the pipeline's stable entrypoint, deadlines, environment names, and
prompt-free telemetry without importing the large :mod:`rag` compatibility
facade.  Consumers resolve the frozen binding at call time so tests and future
application roots can replace the complete capability set atomically.
"""

from __future__ import annotations

import math
import sys
import threading
from collections.abc import Callable, Mapping
from dataclasses import dataclass
from pathlib import Path
from types import MappingProxyType
from typing import Any

import cli_policy
import process_supervision
import run_telemetry


DEFAULT_OPERATION_TIMEOUTS: Mapping[str, float] = MappingProxyType({
    "index": 7200.0,
    "query": 300.0,
    "info": 120.0,
    "full": 14400.0,
    "batch": 43200.0,
    "evaluation": 14400.0,
    "storage": 600.0,
})
PIPELINE_SCRIPT_PATH = Path(__file__).with_name("rag.py")
SUPERVISED_CHILD_ENV = "RAG_PIPELINE_SUPERVISED_CHILD"
RUN_ID_ENV = "RAG_PIPELINE_RUN_ID"
SUPERVISED_TERMINATE_GRACE = 5.0
SUPERVISED_POLL_INTERVAL = 0.2
SUPERVISED_START_GATE_TIMEOUT = 60.0

SupervisorCleanupError = process_supervision._SupervisorCleanupError
SupervisorFn = Callable[..., int]
_WindowsKillJob = process_supervision._WindowsKillJob
_new_supervised_start_gate = process_supervision._new_supervised_start_gate
_SupervisorSignal = process_supervision._SupervisorSignal


@dataclass(frozen=True, slots=True)
class PipelineRuntimeBinding:
    """Atomic capabilities required to supervise one pipeline job."""

    script_path: Path
    supervisor: SupervisorFn
    cleanup_error_type: type[RuntimeError]
    operation_timeouts: Mapping[str, float]

    def __post_init__(self) -> None:
        script_path = Path(self.script_path)
        if not callable(self.supervisor):
            raise TypeError("supervisor must be callable")
        if (not isinstance(self.cleanup_error_type, type)
                or not issubclass(self.cleanup_error_type, RuntimeError)):
            raise TypeError(
                "cleanup_error_type must be a RuntimeError type")
        if not isinstance(self.operation_timeouts, Mapping):
            raise TypeError("operation_timeouts must be a mapping")
        timeouts: dict[str, float] = {}
        for operation, timeout in self.operation_timeouts.items():
            if not isinstance(operation, str) or not operation:
                raise ValueError("operation timeout names must be non-empty")
            try:
                timeout_value = float(timeout)
            except (OverflowError, TypeError, ValueError) as exc:
                raise ValueError(
                    "operation timeouts must be finite positive numbers"
                ) from exc
            if (isinstance(timeout, bool)
                    or not isinstance(timeout, (int, float))
                    or not math.isfinite(timeout_value)
                    or timeout_value <= 0):
                raise ValueError(
                    "operation timeouts must be finite positive numbers")
            timeouts[operation] = timeout_value
        object.__setattr__(self, "script_path", script_path)
        object.__setattr__(
            self, "operation_timeouts", MappingProxyType(timeouts))


def _normalize_operation_timeout(timeout: object) -> float:
    return cli_policy._normalize_operation_timeout(
        timeout, timeout_max=threading.TIMEOUT_MAX)


def _supervision_config() -> process_supervision.SupervisionConfig:
    return process_supervision.SupervisionConfig(
        supervised_child_env=SUPERVISED_CHILD_ENV,
        run_id_env=RUN_ID_ENV,
        terminate_grace=SUPERVISED_TERMINATE_GRACE,
        poll_interval=SUPERVISED_POLL_INTERVAL,
        start_gate_timeout=SUPERVISED_START_GATE_TIMEOUT,
    )


def _terminate_supervised_process(process, *, kill_job=None) -> bool:
    return process_supervision._terminate_supervised_process(
        process,
        kill_job=kill_job,
        terminate_grace=SUPERVISED_TERMINATE_GRACE,
    )


def _telemetry_requested(
    run_id: str | None,
    run_events: Path | None,
    run_report: Path | None,
) -> bool:
    return run_id is not None and (
        run_events is not None or run_report is not None)


def _start_telemetry(
    operation: str,
    *,
    run_id: str | None,
    run_events: Path | None,
    run_report: Path | None,
) -> None:
    if _telemetry_requested(run_id, run_events, run_report):
        run_telemetry.RunTelemetry(
            operation,
            run_id=run_id,
            events_path=run_events,
            report_path=run_report,
        ).start()


def _finalize_telemetry(
    operation: str,
    *,
    run_id: str | None,
    run_events: Path | None,
    run_report: Path | None,
    status: str,
    exc: BaseException,
) -> None:
    if not _telemetry_requested(run_id, run_events, run_report):
        return
    try:
        run_telemetry.finalize_interrupted_run(
            operation,
            run_id=run_id,
            status=status,
            exc=exc,
            events_path=run_events,
            report_path=run_report,
        )
    except Exception as telemetry_exc:
        print(
            "Could not finalize supervised run telemetry "
            f"({type(telemetry_exc).__name__}).",
            file=sys.stderr,
        )


def run_cli_with_deadline(
    script_path: Path,
    argv: list[str],
    *,
    operation: str,
    timeout: float,
    working_directory: Path | None = None,
    environment_overrides: dict[str, str | None] | None = None,
    run_id: str | None = None,
    run_events: Path | None = None,
    run_report: Path | None = None,
    cancel_requested: Callable[[], bool] | None = None,
    on_child_started: Callable[[Any], None] | None = None,
    heartbeat: Callable[[Any], None] | None = None,
    stdout_target: Any = None,
    stderr_target: Any = None,
) -> int:
    """Run one pipeline command using the concrete production policy."""
    return process_supervision._run_cli_with_deadline(
        script_path,
        argv,
        operation=operation,
        timeout=timeout,
        config=_supervision_config(),
        normalize_timeout_fn=_normalize_operation_timeout,
        telemetry_start_fn=_start_telemetry,
        telemetry_finalize_fn=_finalize_telemetry,
        kill_job_factory=_WindowsKillJob,
        start_gate_factory=_new_supervised_start_gate,
        terminate_fn=_terminate_supervised_process,
        supervisor_signal_type=_SupervisorSignal,
        cleanup_error_type=SupervisorCleanupError,
        working_directory=working_directory,
        environment_overrides=environment_overrides,
        run_id=run_id,
        run_events=run_events,
        run_report=run_report,
        cancel_requested=cancel_requested,
        on_child_started=on_child_started,
        heartbeat=heartbeat,
        stdout_target=stdout_target,
        stderr_target=stderr_target,
    )


_DEFAULT_RUNTIME_BINDING = PipelineRuntimeBinding(
    script_path=PIPELINE_SCRIPT_PATH,
    supervisor=run_cli_with_deadline,
    cleanup_error_type=SupervisorCleanupError,
    operation_timeouts=DEFAULT_OPERATION_TIMEOUTS,
)


def default_runtime_binding() -> PipelineRuntimeBinding:
    """Return the immutable production binding for call-time resolution."""
    return _DEFAULT_RUNTIME_BINDING


__all__ = [
    "DEFAULT_OPERATION_TIMEOUTS",
    "PIPELINE_SCRIPT_PATH",
    "PipelineRuntimeBinding",
    "RUN_ID_ENV",
    "SUPERVISED_CHILD_ENV",
    "SUPERVISED_POLL_INTERVAL",
    "SUPERVISED_START_GATE_TIMEOUT",
    "SUPERVISED_TERMINATE_GRACE",
    "SupervisorCleanupError",
    "default_runtime_binding",
    "run_cli_with_deadline",
]
