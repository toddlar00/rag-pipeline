"""Private, synthetic-only command contracts for region OCR retries."""

import builtins
from pathlib import Path
import subprocess
import sys

import pytest

from ocr_recovery import ReportCleanupError
from tools import retry_ocr_regions as cli


PRIVATE = "PRIVATE_PATH_OR_EXCEPTION"
ARGS = ["--pdf", PRIVATE + ".pdf", "--recovery", PRIVATE + "-recovery.json",
        "--plan", PRIVATE + "-plan.json", "--output", PRIVATE + "-output.json"]


def report():
    return {"summary": {"requested": 2, "candidates": 1, "empty": 0, "failed": 1}, "private": PRIVATE}


def test_direct_cli_forwards_arguments_and_always_requires_manual_review(monkeypatch, capsys):
    seen = []
    monkeypatch.setattr(cli, "recover_regions", lambda *args, **kwargs: seen.append((args, kwargs)) or report())
    assert cli.main(ARGS + ["--dpi", "400"]) == 3
    assert seen == [((Path(ARGS[1]), Path(ARGS[3]), Path(ARGS[5]), Path(ARGS[7])), {"dpi": 400})]
    captured = capsys.readouterr()
    assert "2 requested, 1 candidates, 0 empty, 1 failed" in captured.out
    assert "manual review" in captured.out and "canonical extraction unchanged" in captured.out
    assert PRIVATE not in captured.out + captured.err


@pytest.mark.parametrize("args", [[], ARGS + [PRIVATE], ARGS + ["--dpi", "600"],
                                 ARGS + ["--dpi", PRIVATE], ARGS + ["--unknown", PRIVATE],
                                 ARGS + ["--timeout-seconds", PRIVATE]])
def test_parser_never_echoes_private_arguments(monkeypatch, capsys, args):
    monkeypatch.setattr(cli, "recover_regions", lambda *_args, **_kwargs: pytest.fail("recovery ran"))
    monkeypatch.setattr(sys, "argv", [PRIVATE + "/program.py"])
    with pytest.raises(SystemExit) as caught:
        cli.main(args)
    assert caught.value.code == 2
    captured = capsys.readouterr()
    assert PRIVATE not in captured.out + captured.err
    assert "usage: retry_ocr_regions.py" in captured.err


def test_parser_cancellation_is_sanitized(monkeypatch, capsys):
    def cancel(*_args, **_kwargs):
        raise KeyboardInterrupt(PRIVATE)

    monkeypatch.setattr(cli._ArgumentParser, "parse_args", cancel)
    assert cli.main(ARGS) == 130
    captured = capsys.readouterr()
    assert "cancelled" in captured.err and PRIVATE not in captured.out + captured.err


@pytest.mark.parametrize("error,expected", [(ValueError, 2), (OSError, 2), (RuntimeError, 2),
                                           (ImportError, 2), (FileExistsError, 2),
                                           (ReportCleanupError, 2), (KeyboardInterrupt, 130)])
def test_terminal_errors_are_sanitized(monkeypatch, capsys, error, expected):
    def fail(*_args, **_kwargs):
        raise error(PRIVATE)

    monkeypatch.setattr(cli, "recover_regions", fail)
    assert cli.main(ARGS) == expected
    captured = capsys.readouterr()
    assert PRIVATE not in captured.out + captured.err
    assert "--output" in captured.err


@pytest.mark.parametrize("error,expected", [(ImportError, 2), (KeyboardInterrupt, 130)])
def test_dependency_import_boundary_is_guarded(monkeypatch, capsys, error, expected):
    actual = builtins.__import__

    def importing(name, *args, **kwargs):
        if name == "ocr_recovery":
            raise error(PRIVATE)
        return actual(name, *args, **kwargs)

    monkeypatch.setattr(builtins, "__import__", importing)
    assert cli.main(ARGS) == expected
    captured = capsys.readouterr()
    assert PRIVATE not in captured.out + captured.err


@pytest.mark.parametrize("timeout", ["nan", "inf", "0", "0.9", "86401"])
def test_invalid_deadline_does_not_launch_or_load_pdf(monkeypatch, timeout):
    monkeypatch.setattr(cli, "run_with_deadline", lambda *_args, **_kwargs: pytest.fail("worker ran"))
    monkeypatch.setattr(cli, "recover_regions", lambda *_args, **_kwargs: pytest.fail("recovery ran"))
    assert cli.main(ARGS + ["--timeout-seconds", timeout]) == 2


@pytest.mark.parametrize("code,expected", [(3, 3), (124, 124), (130, 130), (0, 2), (1, 2), (2, 2), (125, 2)])
def test_supervised_mode_strips_deadline_and_preserves_terminal_status(monkeypatch, capsys, code, expected):
    calls = []
    monkeypatch.setattr(cli, "run_with_deadline", lambda *args, **kwargs: calls.append((args, kwargs)) or code)
    monkeypatch.setattr(cli, "recover_regions", lambda *_args, **_kwargs: pytest.fail("direct recovery ran"))
    monkeypatch.setattr(cli, "load_region_review", lambda _path: report() if code == 3 else pytest.fail("report loaded"))
    checked = []
    monkeypatch.setattr(cli, "verify_region_review_request",
                        lambda value, **kwargs: checked.append((value, kwargs)) or value)
    assert cli.main(ARGS + ["--timeout-seconds", "2"]) == expected
    assert calls == [((["--pdf=" + ARGS[1], "--recovery=" + ARGS[3], "--plan=" + ARGS[5],
                       "--output=" + ARGS[7], "--dpi", "300"],), {"timeout": 2.0})]
    captured = capsys.readouterr()
    assert PRIVATE not in captured.out + captured.err
    assert checked == ([(report(), {"pdf_path": Path(ARGS[1]), "recovery_path": Path(ARGS[3]),
                                    "plan_path": Path(ARGS[5]), "dpi": 300})] if code == 3 else [])


@pytest.mark.parametrize("error,expected", [(ValueError, 2), (RuntimeError, 2), (KeyboardInterrupt, 130)])
def test_supervised_report_binding_failure_precedes_summary(monkeypatch, capsys, error, expected):
    monkeypatch.setattr(cli, "run_with_deadline", lambda *_args, **_kwargs: 3)
    monkeypatch.setattr(cli, "load_region_review", lambda _path: report())

    def fail(*_args, **_kwargs):
        raise error(PRIVATE)

    monkeypatch.setattr(cli, "verify_region_review_request", fail)
    assert cli.main(ARGS + ["--timeout-seconds", "2"]) == expected
    captured = capsys.readouterr()
    assert "requested," not in captured.out and PRIVATE not in captured.out + captured.err


def test_worker_supervision_is_silent_and_ignores_unrelated_inherited_flags(monkeypatch, capsys):
    import process_supervision

    calls = []

    def supervise(*args, **kwargs):
        calls.append((args, kwargs))
        kwargs["warn_fn"](PRIVATE + " vector-store")
        return 124

    monkeypatch.setenv("RAG_PIPELINE_SUPERVISED_CHILD", "1")
    monkeypatch.setattr(process_supervision, "_run_cli_with_deadline", supervise)
    assert cli.run_with_deadline([], timeout=1) == 124
    args, kwargs = calls[0]
    assert args == (Path(cli.__file__), [])
    assert kwargs["stdout_target"] == kwargs["stderr_target"] == subprocess.DEVNULL
    assert kwargs["operation"] == "ocr-region-retry"
    captured = capsys.readouterr()
    assert PRIVATE not in captured.out + captured.err
    assert "may remain" in captured.err


def test_help_is_dependency_light_and_does_not_import_pdf_or_ocr():
    program = "\n".join([
        "import sys", "from tools import retry_ocr_regions",
        "try: retry_ocr_regions.main(['--help'])", "except SystemExit as error: assert error.code==0",
        "assert not {'rag','ocr_regions','ocr_region_runtime','pymupdf','rapidocr','numpy','cv2'}.intersection(sys.modules)",
    ])
    completed = subprocess.run([sys.executable, "-c", program], capture_output=True, text=True,
                               check=False, timeout=30)
    assert completed.returncode == 0, completed.stderr
    assert "--timeout-seconds" in completed.stdout and "--plan" in completed.stdout
