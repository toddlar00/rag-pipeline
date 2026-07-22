"""Wait for supervisor containment before executing one Python script.

This bootstrap is intentionally standard-library-only.  The parent supervisor
passes a one-shot operating-system gate; target code is not imported or
executed until that gate is released after process-tree containment and durable
worker registration.  An absent parent therefore produces a harmless startup
failure instead of an untracked operation.
"""

from __future__ import annotations

import math
import os
from pathlib import Path
import runpy
import select
import signal
import sys


STARTUP_FAILURE_EXIT = 125
_MAX_GATE_TIMEOUT_SECONDS = 10 * 60.0


def _parse_positive_int(value: str) -> int | None:
    try:
        parsed = int(value, 10)
    except (TypeError, ValueError):
        return None
    return parsed if parsed >= 0 else None


def _parse_timeout(value: str) -> float | None:
    try:
        parsed = float(value)
    except (TypeError, ValueError):
        return None
    if (not math.isfinite(parsed) or parsed <= 0
            or parsed > _MAX_GATE_TIMEOUT_SECONDS):
        return None
    return parsed


def _wait_for_posix_pipe(descriptor: int, timeout: float) -> int | None:
    if os.name == "nt":
        return None
    keep_open = False
    try:
        ready, _, _ = select.select([descriptor], [], [], timeout)
        if not ready:
            return None
        if os.read(descriptor, 1) != b"\x01":
            return None
        keep_open = True
        return descriptor
    except (OSError, ValueError):
        return None
    finally:
        if not keep_open:
            try:
                os.close(descriptor)
            except OSError:
                pass


def _wait_for_windows_event(handle_value: int, timeout: float) -> bool:
    if os.name != "nt" or handle_value <= 0:
        return False
    import ctypes
    from ctypes import wintypes

    kernel32 = ctypes.WinDLL("kernel32", use_last_error=True)
    wait_for_single_object = kernel32.WaitForSingleObject
    wait_for_single_object.argtypes = [wintypes.HANDLE, wintypes.DWORD]
    wait_for_single_object.restype = wintypes.DWORD
    close_handle = kernel32.CloseHandle
    close_handle.argtypes = [wintypes.HANDLE]
    close_handle.restype = wintypes.BOOL
    handle = wintypes.HANDLE(handle_value)
    milliseconds = min(max(1, math.ceil(timeout * 1000)), 0xFFFFFFFE)
    try:
        return wait_for_single_object(handle, milliseconds) == 0
    finally:
        close_handle(handle)


def _wait_for_release(
        kind: str, value: str, timeout: float) -> tuple[bool, int | None]:
    parsed = _parse_positive_int(value)
    if parsed is None:
        return False, None
    if kind == "posix-pipe":
        descriptor = _wait_for_posix_pipe(parsed, timeout)
        return descriptor is not None, descriptor
    if kind == "windows-event":
        return _wait_for_windows_event(parsed, timeout), None
    return False, None


def _start_posix_parent_watchdog(descriptor: int) -> bool:
    """Kill the contained process group when the supervisor pipe reaches EOF."""
    if os.name == "nt":
        return False
    # ``SIG_IGN`` survives exec. Normalize only the watcher disposition, then
    # restore the target bootstrap immediately after fork so user-code signal
    # semantics remain unchanged.
    previous_sigterm = signal.getsignal(signal.SIGTERM)
    signal.signal(signal.SIGTERM, signal.SIG_DFL)
    try:
        watchdog_pid = os.fork()
    except OSError:
        signal.signal(signal.SIGTERM, previous_sigterm)
        try:
            os.close(descriptor)
        except OSError:
            pass
        return False
    if watchdog_pid:
        signal.signal(signal.SIGTERM, previous_sigterm)
        try:
            os.close(descriptor)
        except OSError:
            try:
                os.kill(watchdog_pid, signal.SIGKILL)
                os.waitpid(watchdog_pid, 0)
            except OSError:
                pass
            return False
        return True

    # This child performs only async-signal-safe operating-system calls after
    # fork. It stays in the target's process group and never imports user code.
    try:
        while os.read(descriptor, 1):
            pass
    except BaseException:
        pass
    try:
        os.killpg(os.getpgrp(), signal.SIGKILL)
    except BaseException:
        pass
    os._exit(STARTUP_FAILURE_EXIT)


def _run_target(script_value: str, arguments: list[str]) -> int:
    """Execute a script with the observable argv/path setup of ``python FILE``."""
    script = Path(script_value).resolve()
    script_text = str(script)
    sys.argv[:] = [script_text, *arguments]
    if sys.path:
        sys.path[0] = str(script.parent)
    else:
        sys.path.append(str(script.parent))
    if hasattr(sys, "orig_argv"):
        sys.orig_argv = [sys.executable, "-u", script_text, *arguments]
    runpy.run_path(script_text, run_name="__main__")
    return 0


def main(argv: list[str] | None = None) -> int:
    arguments = list(sys.argv[1:] if argv is None else argv)
    if len(arguments) < 4:
        return STARTUP_FAILURE_EXIT
    kind, value, raw_timeout, script, *target_arguments = arguments
    timeout = _parse_timeout(raw_timeout)
    if timeout is None:
        return STARTUP_FAILURE_EXIT
    released, liveness_descriptor = _wait_for_release(kind, value, timeout)
    if not released:
        return STARTUP_FAILURE_EXIT
    if (liveness_descriptor is not None
            and not _start_posix_parent_watchdog(liveness_descriptor)):
        return STARTUP_FAILURE_EXIT
    return _run_target(script, target_arguments)


if __name__ == "__main__":
    raise SystemExit(main())
