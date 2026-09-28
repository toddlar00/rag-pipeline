"""Opt-in, content-silent process supervision for the OCR review command.

The shared supervisor owns containment and termination. This narrow binding
does not parse PDFs, load OCR models, publish reports, or remove artifacts.
"""

from __future__ import annotations

import math
from pathlib import Path
import subprocess
import sys

import process_supervision


_RETRY_SCRIPT_PATH = Path(__file__).with_name("tools") / "retry_ocr.py"
_CONFIG = process_supervision.SupervisionConfig(
    supervised_child_env="RAG_PIPELINE_SUPERVISED_CHILD",
    run_id_env="RAG_PIPELINE_RUN_ID",
    terminate_grace=5.0,
    poll_interval=0.2,
    start_gate_timeout=60.0,
)
SupervisorCleanupError = process_supervision._SupervisorCleanupError
_TIMEOUT_ERROR = "OCR timeout must be a finite number from 1 to 86400 seconds"
_DEADLINE_WARNING = (
    "OCR retry worker exceeded its deadline. Inspect --output before retrying; "
    "a completed report or private temporary files may remain. "
    "No canonical extraction was changed."
)
_CANCELLATION_WARNING = (
    "OCR retry worker was cancelled. Inspect --output before retrying; "
    "a completed report or private temporary files may remain. "
    "No canonical extraction was changed."
)


def validate_timeout(value: object) -> float:
    """Validate a numeric worker deadline without echoing untrusted input."""
    if isinstance(value, bool) or not isinstance(value, (int, float)):
        raise ValueError(_TIMEOUT_ERROR)
    try:
        timeout = float(value)
    except (OverflowError, TypeError, ValueError):
        raise ValueError(_TIMEOUT_ERROR) from None
    if not math.isfinite(timeout) or not 1.0 <= timeout <= 86400.0:
        raise ValueError(_TIMEOUT_ERROR)
    return timeout


def _worker_arguments(argv: list[str]) -> list[str]:
    if not isinstance(argv, list):
        raise ValueError("OCR worker arguments must be a list of strings")
    result = list(argv)
    for argument in result:
        if not isinstance(argument, str) or "\0" in argument:
            raise ValueError("OCR worker arguments must be strings without NUL")
        option = argument.partition("=")[0]
        # Reject argparse's long-option abbreviations as well as the exact
        # flag. The caller canonicalizes options and removes the deadline;
        # inherited child environment flags must never bypass supervision.
        if (option.startswith("--") and len(option) > 2
                and "--timeout-seconds".startswith(option)):
            raise ValueError("OCR worker arguments must not include a timeout option")
    return result


def _warn_deadline(_untrusted_message: str) -> None:
    """Replace generic index-oriented diagnostics with fixed OCR guidance."""
    print(_DEADLINE_WARNING, file=sys.stderr)


def run_retry_with_deadline(argv: list[str], *, timeout: float) -> int:
    """Run only the OCR retry script under a validated worker deadline.

    Native output is discarded. The caller validates a completed report and
    prints its own summary. Exit 124/130 never implies that no report exists;
    termination may follow publication and bypass private scratch cleanup.
    """
    deadline = validate_timeout(timeout)
    arguments = _worker_arguments(argv)
    result = process_supervision._run_cli_with_deadline(
        _RETRY_SCRIPT_PATH,
        arguments,
        operation="ocr-retry",
        timeout=deadline,
        config=_CONFIG,
        warn_fn=_warn_deadline,
        cleanup_error_type=SupervisorCleanupError,
        stdout_target=subprocess.DEVNULL,
        stderr_target=subprocess.DEVNULL,
    )
    if result == 130:
        print(_CANCELLATION_WARNING, file=sys.stderr)
    return result
