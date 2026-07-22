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


def _wait_for_posix_pipe(descriptor: int, timeout: float) -> bool:
    if os.name == "nt":
        return False
    try:
        ready, _, _ = select.select([descriptor], [], [], timeout)
        if not ready:
            return False
        return os.read(descriptor, 1) == b"\x01"
    except (OSError, ValueError):
        return False
    finally:
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


def _wait_for_release(kind: str, value: str, timeout: float) -> bool:
    parsed = _parse_positive_int(value)
    if parsed is None:
        return False
    if kind == "posix-pipe":
        return _wait_for_posix_pipe(parsed, timeout)
    if kind == "windows-event":
        return _wait_for_windows_event(parsed, timeout)
    return False


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
    if timeout is None or not _wait_for_release(kind, value, timeout):
        return STARTUP_FAILURE_EXIT
    return _run_target(script, target_arguments)


if __name__ == "__main__":
    raise SystemExit(main())
