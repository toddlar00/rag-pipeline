"""Content-free hard-scan CLI diagnostics and supervised result binding."""

import builtins
from pathlib import Path
import subprocess
import sys

import pytest

from ocr_recovery import ReportCleanupError
from tools import retry_ocr_hardscan as cli


PRIVATE = "PRIVATE_SOURCE_OR_EXCEPTION"
ARGS = ["--pdf", PRIVATE+".pdf", "--recovery", PRIVATE+"-recovery.json",
        "--plan", PRIVATE+"-plan.json", "--output", PRIVATE+"-output.json"]


def report():
    return {"summary": {"requested": 2, "candidates": 1, "empty": 0, "failed": 0, "abstained": 1},
            "private": PRIVATE}


def test_direct_cli_preserves_manual_review_and_only_prints_fixed_counts(monkeypatch, capsys):
    calls = []
    monkeypatch.setattr(cli, "recover_hardscan", lambda *args, **kwargs: calls.append((args, kwargs)) or report())
    assert cli.main(ARGS+["--dpi", "400"]) == 3
    assert calls == [((Path(ARGS[1]), Path(ARGS[3]), Path(ARGS[5]), Path(ARGS[7])), {"dpi": 400})]
    captured = capsys.readouterr()
    assert "2 requested, 1 candidates, 0 empty, 0 failed, 1 abstained" in captured.out
    assert "Manual review" in captured.out and "not general page dewarping" in captured.out
    assert PRIVATE not in captured.out+captured.err


@pytest.mark.parametrize("args", [[], ARGS+[PRIVATE], ARGS+["--dpi", "600"], ARGS+["--dpi", PRIVATE],
                                 ARGS+["--unknown", PRIVATE], ARGS+["--timeout-seconds", PRIVATE]])
def test_private_parser_arguments_are_never_echoed(capsys, args):
    with pytest.raises(SystemExit) as error:
        cli.main(args)
    assert error.value.code == 2
    captured = capsys.readouterr()
    assert PRIVATE not in captured.out+captured.err
    assert "usage: retry_ocr_hardscan.py" in captured.err


@pytest.mark.parametrize("error,status", [(ValueError, 2), (OSError, 2), (RuntimeError, 2), (ImportError, 2),
                                         (FileExistsError, 2), (ReportCleanupError, 2), (KeyboardInterrupt, 130)])
def test_runtime_failures_and_cleanup_are_sanitized(monkeypatch, capsys, error, status):
    def fail(*_args, **_kwargs):
        raise error(PRIVATE)

    monkeypatch.setattr(cli, "recover_hardscan", fail)
    assert cli.main(ARGS) == status
    captured = capsys.readouterr()
    assert PRIVATE not in captured.out+captured.err and "--output" in captured.err


@pytest.mark.parametrize("boundary", ["parser", "import"])
def test_early_cancellation_is_guarded(monkeypatch, capsys, boundary):
    actual = builtins.__import__

    def cancel(*_args, **_kwargs):
        raise KeyboardInterrupt(PRIVATE)

    def importing(name, *args, **kwargs):
        if name == "ocr_recovery":
            cancel()
        return actual(name, *args, **kwargs)

    if boundary == "parser":
        monkeypatch.setattr(cli._ArgumentParser, "parse_args", cancel)
    else:
        monkeypatch.setattr(builtins, "__import__", importing)
    assert cli.main(ARGS) == 130
    captured = capsys.readouterr()
    assert "cancelled" in captured.err and PRIVATE not in captured.out+captured.err


@pytest.mark.parametrize("timeout", ["nan", "inf", "0", ".5", "86401"])
def test_invalid_deadlines_cannot_launch_workers(monkeypatch, timeout):
    monkeypatch.setattr(cli, "run_with_deadline", lambda *_args, **_kwargs: pytest.fail("worker launched"))
    assert cli.main(ARGS+["--timeout-seconds", timeout]) == 2


@pytest.mark.parametrize("code,expected", [(3, 3), (124, 124), (130, 130), (0, 2), (1, 2), (2, 2), (125, 2)])
def test_supervision_is_canonical_and_report_verified_before_summary(monkeypatch, capsys, code, expected):
    calls, checks = [], []
    monkeypatch.setattr(cli, "run_with_deadline", lambda *args, **kwargs: calls.append((args, kwargs)) or code)
    monkeypatch.setattr(cli, "load_report", lambda _path: report() if code == 3 else pytest.fail("report loaded"))
    monkeypatch.setattr(cli, "verify_request", lambda value, **kwargs: checks.append(kwargs) or value)
    assert cli.main(ARGS+["--timeout-seconds", "2"]) == expected
    assert calls == [((["--pdf="+ARGS[1], "--recovery="+ARGS[3], "--plan="+ARGS[5], "--output="+ARGS[7],
                       "--dpi", "300"],), {"timeout": 2.})]
    assert checks == ([{"pdf_path": Path(ARGS[1]), "recovery_path": Path(ARGS[3]),
                        "plan_path": Path(ARGS[5]), "dpi": 300}] if code == 3 else [])
    captured = capsys.readouterr()
    assert PRIVATE not in captured.out+captured.err


def test_stale_report_verification_failure_never_prints_success(monkeypatch, capsys):
    monkeypatch.setattr(cli, "run_with_deadline", lambda *_args, **_kwargs: 3)
    monkeypatch.setattr(cli, "load_report", lambda _path: report())

    def fail(*_args, **_kwargs):
        raise ValueError(PRIVATE)

    monkeypatch.setattr(cli, "verify_request", fail)
    assert cli.main(ARGS+["--timeout-seconds", "2"]) == 2
    captured = capsys.readouterr()
    assert "requested," not in captured.out and PRIVATE not in captured.out+captured.err


def test_supervisor_suppresses_native_output_and_cannot_inherit_bypass(monkeypatch, capsys):
    import process_supervision

    calls = []

    def supervise(*args, **kwargs):
        calls.append((args, kwargs))
        kwargs["warn_fn"](PRIVATE)
        return 124

    monkeypatch.setenv("RAG_PIPELINE_SUPERVISED_CHILD", "1")
    monkeypatch.setattr(process_supervision, "_run_cli_with_deadline", supervise)
    assert cli.run_with_deadline([], timeout=1) == 124
    args, options = calls[0]
    assert args == (Path(cli.__file__), []) and options["operation"] == "ocr-hardscan-retry"
    assert options["stdout_target"] == options["stderr_target"] == subprocess.DEVNULL
    assert PRIVATE not in capsys.readouterr().err
    with pytest.raises(ValueError):
        cli.run_with_deadline(["--timeout-seconds=1"], timeout=1)


def test_help_is_available_without_pdf_cv_or_ocr_imports():
    code = "\n".join(["import sys", "from tools import retry_ocr_hardscan",
                       "try: retry_ocr_hardscan.main(['--help'])", "except SystemExit as error: assert error.code==0",
                       "assert not {'pymupdf','rapidocr','numpy','cv2','rag','ocr_hardscan_io'}.intersection(sys.modules)"])
    completed = subprocess.run([sys.executable, "-c", code], capture_output=True, timeout=30, check=False)
    assert completed.returncode == 0, completed.stderr
