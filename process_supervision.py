"""Deadline supervision for CLI worker processes.

This standard-library-only module owns process containment, startup gating,
deadline/cancellation handling, and the generic supervised entrypoint flow.
``rag.py`` remains the compatibility facade and supplies telemetry, CLI policy,
configuration, and mutable collaborators at call time.
"""

from __future__ import annotations

import os
import signal
import subprocess
import sys
import threading as _threading
import time
from collections.abc import Callable
from dataclasses import dataclass
from pathlib import Path
from typing import Any


_DEFAULT_TERMINATE_GRACE = 5.0


@dataclass(frozen=True)
class SupervisionConfig:
    """Environment names and timing constants owned by a caller facade."""

    supervised_child_env: str
    run_id_env: str
    terminate_grace: float
    poll_interval: float
    start_gate_timeout: float


class _WindowsKillJob:
    """Windows Job Object that kills every assigned process when closed."""

    _KILL_ON_JOB_CLOSE = 0x00002000
    _EXTENDED_LIMIT_INFORMATION = 9

    def __init__(self):
        import ctypes
        from ctypes import wintypes

        class _IoCounters(ctypes.Structure):
            _fields_ = [
                ("ReadOperationCount", ctypes.c_ulonglong),
                ("WriteOperationCount", ctypes.c_ulonglong),
                ("OtherOperationCount", ctypes.c_ulonglong),
                ("ReadTransferCount", ctypes.c_ulonglong),
                ("WriteTransferCount", ctypes.c_ulonglong),
                ("OtherTransferCount", ctypes.c_ulonglong),
            ]

        class _BasicLimitInformation(ctypes.Structure):
            _fields_ = [
                ("PerProcessUserTimeLimit", wintypes.LARGE_INTEGER),
                ("PerJobUserTimeLimit", wintypes.LARGE_INTEGER),
                ("LimitFlags", wintypes.DWORD),
                ("MinimumWorkingSetSize", ctypes.c_size_t),
                ("MaximumWorkingSetSize", ctypes.c_size_t),
                ("ActiveProcessLimit", wintypes.DWORD),
                ("Affinity", ctypes.c_size_t),
                ("PriorityClass", wintypes.DWORD),
                ("SchedulingClass", wintypes.DWORD),
            ]

        class _ExtendedLimitInformation(ctypes.Structure):
            _fields_ = [
                ("BasicLimitInformation", _BasicLimitInformation),
                ("IoInfo", _IoCounters),
                ("ProcessMemoryLimit", ctypes.c_size_t),
                ("JobMemoryLimit", ctypes.c_size_t),
                ("PeakProcessMemoryUsed", ctypes.c_size_t),
                ("PeakJobMemoryUsed", ctypes.c_size_t),
            ]

        class _BasicAccountingInformation(ctypes.Structure):
            _fields_ = [
                ("TotalUserTime", wintypes.LARGE_INTEGER),
                ("TotalKernelTime", wintypes.LARGE_INTEGER),
                ("ThisPeriodTotalUserTime", wintypes.LARGE_INTEGER),
                ("ThisPeriodTotalKernelTime", wintypes.LARGE_INTEGER),
                ("TotalPageFaultCount", wintypes.DWORD),
                ("TotalProcesses", wintypes.DWORD),
                ("ActiveProcesses", wintypes.DWORD),
                ("TotalTerminatedProcesses", wintypes.DWORD),
            ]

        kernel32 = ctypes.WinDLL("kernel32", use_last_error=True)
        create_job = kernel32.CreateJobObjectW
        create_job.argtypes = [wintypes.LPVOID, wintypes.LPCWSTR]
        create_job.restype = wintypes.HANDLE
        set_information = kernel32.SetInformationJobObject
        set_information.argtypes = [
            wintypes.HANDLE,
            ctypes.c_int,
            wintypes.LPVOID,
            wintypes.DWORD,
        ]
        set_information.restype = wintypes.BOOL
        assign_process = kernel32.AssignProcessToJobObject
        assign_process.argtypes = [wintypes.HANDLE, wintypes.HANDLE]
        assign_process.restype = wintypes.BOOL
        terminate_job = kernel32.TerminateJobObject
        terminate_job.argtypes = [wintypes.HANDLE, wintypes.UINT]
        terminate_job.restype = wintypes.BOOL
        query_information = kernel32.QueryInformationJobObject
        query_information.argtypes = [
            wintypes.HANDLE,
            ctypes.c_int,
            wintypes.LPVOID,
            wintypes.DWORD,
            ctypes.POINTER(wintypes.DWORD),
        ]
        query_information.restype = wintypes.BOOL
        close_handle = kernel32.CloseHandle
        close_handle.argtypes = [wintypes.HANDLE]
        close_handle.restype = wintypes.BOOL
        set_handle_information = kernel32.SetHandleInformation
        set_handle_information.argtypes = [
            wintypes.HANDLE,
            wintypes.DWORD,
            wintypes.DWORD,
        ]
        set_handle_information.restype = wintypes.BOOL

        handle = create_job(None, None)
        if not handle:
            raise ctypes.WinError(ctypes.get_last_error())
        if not set_handle_information(handle, 0x00000001, 0):
            error = ctypes.WinError(ctypes.get_last_error())
            close_handle(handle)
            raise error
        limits = _ExtendedLimitInformation()
        limits.BasicLimitInformation.LimitFlags = self._KILL_ON_JOB_CLOSE
        if not set_information(
            handle,
            self._EXTENDED_LIMIT_INFORMATION,
            ctypes.byref(limits),
            ctypes.sizeof(limits),
        ):
            error = ctypes.WinError(ctypes.get_last_error())
            close_handle(handle)
            raise error
        self._ctypes = ctypes
        self._wintypes = wintypes
        self._assign_process = assign_process
        self._terminate_job = terminate_job
        self._query_information = query_information
        self._close_handle = close_handle
        self._basic_accounting_type = _BasicAccountingInformation
        self._handle = handle

    def assign(self, process) -> None:
        if not self._assign_process(
            self._handle, self._wintypes.HANDLE(int(process._handle))
        ):
            raise self._ctypes.WinError(self._ctypes.get_last_error())

    def close(self) -> bool:
        if self._handle:
            closed = bool(self._close_handle(self._handle))
            self._handle = None
            return closed
        return True

    def terminate(self, exit_code: int = 124) -> bool:
        if not self._handle:
            return True
        return bool(self._terminate_job(self._handle, exit_code))

    def active_processes(self) -> int:
        if not self._handle:
            return 0
        information = self._basic_accounting_type()
        returned = self._wintypes.DWORD()
        if not self._query_information(
            self._handle,
            1,
            self._ctypes.byref(information),
            self._ctypes.sizeof(information),
            self._ctypes.byref(returned),
        ):
            raise self._ctypes.WinError(self._ctypes.get_last_error())
        return int(information.ActiveProcesses)

    def terminate_and_confirm(
        self,
        *,
        exit_code: int = 124,
        timeout: float = _DEFAULT_TERMINATE_GRACE,
    ) -> bool:
        """Terminate every assigned process and confirm the Job is empty."""
        confirmed = False
        try:
            if not self.terminate(exit_code):
                return False
            deadline = time.monotonic() + timeout
            while True:
                if self.active_processes() == 0:
                    confirmed = True
                    break
                if time.monotonic() >= deadline:
                    break
                time.sleep(0.025)
        except OSError:
            confirmed = False
        finally:
            closed = self.close()
        return confirmed and closed


class _PosixSupervisedStartGate:
    """Startup gate whose open writer also proves supervisor liveness."""

    kind = "posix-pipe"

    def __init__(self):
        self._read_descriptor, self._write_descriptor = os.pipe()

    @property
    def child_value(self) -> str:
        return str(self._read_descriptor)

    def popen_options(self) -> dict:
        return {
            "close_fds": True,
            "pass_fds": (self._read_descriptor,),
        }

    def release(self) -> None:
        if self._write_descriptor < 0:
            raise RuntimeError("supervised worker start gate is closed")
        if os.write(self._write_descriptor, b"\x01") != 1:
            raise OSError("supervised worker start gate release was incomplete")
        descriptor = self._read_descriptor
        self._read_descriptor = -1
        if descriptor >= 0:
            os.close(descriptor)

    def close(self) -> None:
        for attribute in ("_write_descriptor", "_read_descriptor"):
            descriptor = getattr(self, attribute)
            if descriptor < 0:
                continue
            setattr(self, attribute, -1)
            try:
                os.close(descriptor)
            except OSError:
                pass


class _WindowsSupervisedStartGate:
    """One-shot inherited event used to release a contained Windows worker."""

    kind = "windows-event"

    def __init__(self):
        import ctypes
        from ctypes import wintypes

        kernel32 = ctypes.WinDLL("kernel32", use_last_error=True)
        create_event = kernel32.CreateEventW
        create_event.argtypes = [
            wintypes.LPVOID,
            wintypes.BOOL,
            wintypes.BOOL,
            wintypes.LPCWSTR,
        ]
        create_event.restype = wintypes.HANDLE
        set_event = kernel32.SetEvent
        set_event.argtypes = [wintypes.HANDLE]
        set_event.restype = wintypes.BOOL
        set_handle_information = kernel32.SetHandleInformation
        set_handle_information.argtypes = [
            wintypes.HANDLE,
            wintypes.DWORD,
            wintypes.DWORD,
        ]
        set_handle_information.restype = wintypes.BOOL
        close_handle = kernel32.CloseHandle
        close_handle.argtypes = [wintypes.HANDLE]
        close_handle.restype = wintypes.BOOL
        handle = create_event(None, True, False, None)
        if not handle:
            raise ctypes.WinError(ctypes.get_last_error())
        if not set_handle_information(handle, 0x00000001, 0x00000001):
            error = ctypes.WinError(ctypes.get_last_error())
            close_handle(handle)
            raise error
        self._ctypes = ctypes
        self._set_event = set_event
        self._close_handle = close_handle
        self._handle = handle

    @property
    def child_value(self) -> str:
        return str(int(self._handle))

    def popen_options(self) -> dict:
        startup = subprocess.STARTUPINFO()
        startup.lpAttributeList = {"handle_list": [int(self._handle)]}
        return {"close_fds": True, "startupinfo": startup}

    def release(self) -> None:
        if not self._handle:
            raise RuntimeError("supervised worker start gate is closed")
        if not self._set_event(self._handle):
            raise self._ctypes.WinError(self._ctypes.get_last_error())
        self.close()

    def close(self) -> None:
        if self._handle:
            self._close_handle(self._handle)
            self._handle = None


def _new_supervised_start_gate():
    return (
        _WindowsSupervisedStartGate()
        if os.name == "nt"
        else _PosixSupervisedStartGate()
    )


class _SupervisorSignal(BaseException):
    """Internal control flow used to clean up before honoring a signal."""

    def __init__(self, signum: int):
        self.signum = signum
        super().__init__(signum)


class _SupervisorCleanupError(RuntimeError):
    """Raised only after owned process-tree cleanup remains unconfirmed."""


def _terminate_supervised_process(
    process,
    *,
    kill_job=None,
    terminate_grace: float = _DEFAULT_TERMINATE_GRACE,
    monotonic: Callable[[], float] = time.monotonic,
    sleep_fn: Callable[[float], None] = time.sleep,
) -> bool:
    """Terminate a supervised tree and confirm the direct worker was reaped."""
    if kill_job is not None:
        try:
            confirm = getattr(kill_job, "terminate_and_confirm", None)
            if confirm is not None:
                tree_gone = bool(confirm(timeout=terminate_grace))
            else:
                tree_gone = bool(kill_job.terminate())
                tree_gone = bool(kill_job.close()) and tree_gone
        except BaseException:
            tree_gone = False
            try:
                kill_job.close()
            except BaseException:
                pass
        try:
            process.wait(timeout=terminate_grace)
        except subprocess.TimeoutExpired:
            if process.poll() is None:
                try:
                    process.kill()
                except OSError:
                    pass
            try:
                process.wait(timeout=terminate_grace)
            except subprocess.TimeoutExpired:
                return False
        return process.poll() is not None and tree_gone

    if os.name == "nt":
        taskkill_options = {
            "stdout": subprocess.DEVNULL,
            "stderr": subprocess.DEVNULL,
            "check": False,
            "timeout": terminate_grace,
        }
        create_no_window = getattr(subprocess, "CREATE_NO_WINDOW", 0)
        if create_no_window:
            taskkill_options["creationflags"] = create_no_window
        taskkill_succeeded = False
        try:
            completed = subprocess.run(
                ["taskkill", "/PID", str(process.pid), "/T", "/F"],
                **taskkill_options,
            )
            taskkill_succeeded = completed.returncode == 0
            if completed.returncode and process.poll() is None:
                process.kill()
        except (OSError, subprocess.SubprocessError):
            if process.poll() is None:
                try:
                    process.kill()
                except OSError:
                    pass
        try:
            process.wait(timeout=terminate_grace)
        except subprocess.TimeoutExpired:
            if process.poll() is None:
                try:
                    process.kill()
                except OSError:
                    pass
            try:
                process.wait(timeout=terminate_grace)
            except subprocess.TimeoutExpired:
                pass
        return process.poll() is not None and taskkill_succeeded

    def send_signal(sig) -> None:
        try:
            os.killpg(process.pid, sig)
        except ProcessLookupError:
            pass

    try:
        send_signal(signal.SIGTERM)
    except OSError:
        if process.poll() is None:
            process.kill()

    deadline = monotonic() + terminate_grace
    while monotonic() < deadline:
        process.poll()
        try:
            os.killpg(process.pid, 0)
        except ProcessLookupError:
            break
        except PermissionError:
            pass
        sleep_fn(0.05)
    try:
        send_signal(signal.SIGKILL)
    except OSError:
        pass
    worker_reaped = process.poll() is not None
    if not worker_reaped:
        try:
            process.wait(timeout=terminate_grace)
        except subprocess.TimeoutExpired:
            return False
        worker_reaped = True
    group_gone = False
    deadline = monotonic() + terminate_grace
    while monotonic() < deadline:
        try:
            os.killpg(process.pid, 0)
        except ProcessLookupError:
            group_gone = True
            break
        except PermissionError:
            pass
        sleep_fn(0.05)
    return worker_reaped and group_gone


def _run_cli_with_deadline(
    script_path: Path,
    argv: list[str],
    *,
    operation: str,
    timeout: float,
    config: SupervisionConfig,
    normalize_timeout_fn: Callable[[float], float] | None = None,
    telemetry_start_fn: Callable[..., None] | None = None,
    telemetry_finalize_fn: Callable[..., None] | None = None,
    warn_fn: Callable[[str], None] | None = None,
    kill_job_factory: Callable[[], Any] | None = None,
    start_gate_factory: Callable[[], Any] | None = None,
    terminate_fn: Callable[..., bool] | None = None,
    supervisor_signal_type: type[BaseException] = _SupervisorSignal,
    cleanup_error_type: type[RuntimeError] = _SupervisorCleanupError,
    monotonic: Callable[[], float] = time.monotonic,
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
    """Run one CLI operation in a killable process with a wall-clock deadline."""
    if normalize_timeout_fn is not None:
        timeout = normalize_timeout_fn(timeout)
    if kill_job_factory is None:
        kill_job_factory = _WindowsKillJob
    if start_gate_factory is None:
        start_gate_factory = _new_supervised_start_gate
    if terminate_fn is None:

        def terminate_fn(process, *, kill_job=None):
            return _terminate_supervised_process(
                process,
                kill_job=kill_job,
                terminate_grace=config.terminate_grace,
            )

    if warn_fn is None:

        def warn_fn(message):
            print(message, file=sys.stderr)

    def finalize(status: str, exc: BaseException) -> None:
        if telemetry_finalize_fn is not None:
            telemetry_finalize_fn(
                operation,
                run_id=run_id,
                run_events=run_events,
                run_report=run_report,
                status=status,
                exc=exc,
            )

    environment = os.environ.copy()
    environment[config.supervised_child_env] = "1"
    for name, value in (environment_overrides or {}).items():
        if value is None:
            environment.pop(name, None)
        else:
            environment[name] = value
    if run_id is not None:
        environment[config.run_id_env] = run_id
    if telemetry_start_fn is not None:
        telemetry_start_fn(
            operation,
            run_id=run_id,
            run_events=run_events,
            run_report=run_report,
        )
    target_script = str(Path(script_path).resolve())
    bootstrap_script = str(
        Path(__file__).with_name("supervised_worker.py").resolve()
    )
    process_options = {"env": environment}
    if working_directory is not None:
        process_options["cwd"] = os.fspath(working_directory)
    if stdout_target is not None:
        process_options["stdout"] = stdout_target
    if stderr_target is not None:
        process_options["stderr"] = stderr_target
    kill_job = None
    start_gate = None
    try:
        if os.name == "nt":
            process_options["creationflags"] = getattr(
                subprocess, "CREATE_NEW_PROCESS_GROUP", 0
            )
            kill_job = kill_job_factory()
        else:
            process_options["start_new_session"] = True
        start_gate = start_gate_factory()
        process_options.update(start_gate.popen_options())
        command = [
            sys.executable,
            "-u",
            bootstrap_script,
            start_gate.kind,
            start_gate.child_value,
            str(config.start_gate_timeout),
            target_script,
            *argv,
        ]
        process = subprocess.Popen(command, **process_options)
    except BaseException as exc:
        if start_gate is not None:
            start_gate.close()
        if kill_job is not None:
            kill_job.close()
        finalize(
            "cancelled" if isinstance(exc, KeyboardInterrupt) else "failed",
            exc,
        )
        raise
    try:
        if kill_job is not None:
            kill_job.assign(process)
        if on_child_started is not None:
            on_child_started(process)
        start_gate.release()
    except BaseException as exc:
        start_gate.close()
        cleanup_complete = terminate_fn(process, kill_job=kill_job)
        if cleanup_complete:
            finalize("failed", exc)
        else:
            raise cleanup_error_type(
                "supervised worker tree cleanup could not be confirmed"
            ) from exc
        raise

    previous_handlers = {}
    try:
        if (
            os.name != "nt"
            and _threading.current_thread() is _threading.main_thread()
        ):

            def raise_supervisor_signal(received, _frame):
                raise supervisor_signal_type(received)

            for signal_name in ("SIGTERM", "SIGHUP"):
                signum = getattr(signal, signal_name, None)
                if signum is None:
                    continue
                previous_handlers[signum] = signal.getsignal(signum)
                signal.signal(signum, raise_supervisor_signal)
    except BaseException as exc:
        start_gate.close()
        cleanup_complete = terminate_fn(process, kill_job=kill_job)
        for signum, previous in previous_handlers.items():
            signal.signal(signum, previous)
        if not cleanup_complete:
            raise cleanup_error_type(
                "supervised worker tree cleanup could not be confirmed"
            ) from exc
        raise
    try:
        deadline = monotonic() + timeout
        poll_callbacks = cancel_requested is not None or heartbeat is not None
        while True:
            if cancel_requested is not None and cancel_requested():
                observed_exit = process.poll()
                if observed_exit is not None:
                    exit_code = int(observed_exit)
                    break
                cleanup_complete = terminate_fn(process, kill_job=kill_job)
                if cleanup_complete:
                    finalize("cancelled", KeyboardInterrupt())
                else:
                    raise cleanup_error_type(
                        "supervised worker tree cleanup could not be confirmed"
                    )
                return 130
            remaining = deadline - monotonic()
            if remaining <= 0:
                raise subprocess.TimeoutExpired(command, timeout)
            wait_timeout = (
                min(remaining, config.poll_interval)
                if poll_callbacks
                else remaining
            )
            try:
                exit_code = int(process.wait(timeout=wait_timeout))
                break
            except subprocess.TimeoutExpired:
                if heartbeat is not None:
                    heartbeat(process)
                if not poll_callbacks or monotonic() >= deadline:
                    raise
        cleanup_complete = terminate_fn(process, kill_job=kill_job)
        if not cleanup_complete:
            raise cleanup_error_type(
                "supervised worker tree cleanup could not be confirmed"
            )
        if exit_code:
            finalize(
                "cancelled" if exit_code == 130 else "failed",
                SystemExit(exit_code),
            )
        return exit_code
    except subprocess.TimeoutExpired:
        cleanup_complete = terminate_fn(process, kill_job=kill_job)
        if cleanup_complete:
            finalize(
                "failed",
                TimeoutError("supervised operation deadline exceeded"),
            )
        cleanup_status = (
            "Any operating-system vector-store lease was released; retry "
            "the command to recover an interrupted index."
            if cleanup_complete
            else "Worker cleanup could not be confirmed; verify that no child "
            "process remains before retrying the index."
        )
        warn_fn(
            f"Operation '{operation}' exceeded its {timeout:g}s deadline and "
            f"was terminated. {cleanup_status}"
        )
        if not cleanup_complete:
            raise cleanup_error_type(
                "supervised worker tree cleanup could not be confirmed"
            )
        return 124
    except supervisor_signal_type as exc:
        cleanup_complete = terminate_fn(process, kill_job=kill_job)
        if cleanup_complete:
            finalize("cancelled", KeyboardInterrupt())
        else:
            raise cleanup_error_type(
                "supervised worker tree cleanup could not be confirmed"
            ) from exc
        return 128 + exc.signum
    except KeyboardInterrupt as exc:
        cleanup_complete = terminate_fn(process, kill_job=kill_job)
        if cleanup_complete:
            finalize("cancelled", exc)
        else:
            raise cleanup_error_type(
                "supervised worker tree cleanup could not be confirmed"
            ) from exc
        return 130
    except cleanup_error_type:
        raise
    except BaseException as exc:
        cleanup_complete = terminate_fn(process, kill_job=kill_job)
        if cleanup_complete:
            finalize("failed", exc)
        else:
            raise cleanup_error_type(
                "supervised worker tree cleanup could not be confirmed"
            ) from exc
        raise
    finally:
        for signum, previous in previous_handlers.items():
            signal.signal(signum, previous)
        start_gate.close()
        if kill_job is not None:
            kill_job.close()


def _run_supervised_entrypoint(
    argv: list[str] | None = None,
    *,
    environment_overrides: dict[str, str | None] | None = None,
    config: SupervisionConfig,
    script_path: Path,
    command_resolver: Callable[[list[str]], str | None],
    operation_timeouts: dict,
    telemetry_options_fn: Callable[[list[str], str], dict],
    timeout_fn: Callable[[list[str], str], float],
    run_id_factory: Callable[[], str],
    menu_fn: Callable[[], None],
    main_fn: Callable[[list[str]], Any],
    supervisor_fn: Callable[..., int],
) -> int:
    """Run direct commands and supervise operations that own vector clients."""
    cli_args = list(sys.argv[1:] if argv is None else argv)
    command = command_resolver(cli_args)
    if not cli_args or command == "menu":
        menu_fn()
        return 0
    if (
        command in operation_timeouts
        and os.environ.get(config.supervised_child_env) != "1"
    ):
        telemetry_options = telemetry_options_fn(cli_args, command)
        explicit_run_id = telemetry_options["run_id"]
        run_id = (
            explicit_run_id
            if explicit_run_id is not None
            else os.environ.get(config.run_id_env) or run_id_factory()
        )
        supervisor_options = {
            "operation": command,
            "timeout": timeout_fn(cli_args, command),
            "run_id": run_id,
            "run_events": (
                Path(telemetry_options["events_path"])
                if telemetry_options["events_path"] is not None
                else None
            ),
            "run_report": (
                Path(telemetry_options["report_path"])
                if telemetry_options["report_path"] is not None
                else None
            ),
        }
        if environment_overrides:
            supervisor_options["environment_overrides"] = environment_overrides
        return supervisor_fn(Path(script_path), cli_args, **supervisor_options)

    previous_environment = {}
    missing_environment = set()
    for name, value in (environment_overrides or {}).items():
        if name in os.environ:
            previous_environment[name] = os.environ[name]
        else:
            missing_environment.add(name)
        if value is None:
            os.environ.pop(name, None)
        else:
            os.environ[name] = value
    try:
        main_fn(cli_args)
    finally:
        for name in missing_environment:
            os.environ.pop(name, None)
        os.environ.update(previous_environment)
    return 0
