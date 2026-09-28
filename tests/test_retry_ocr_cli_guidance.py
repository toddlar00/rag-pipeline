"""Content-free retry argument errors and actionable aggregate failure guidance."""

import copy
from pathlib import Path
import subprocess
import sys

import pytest

from tools import retry_ocr as cli


PROJECT_ROOT = Path(__file__).resolve().parents[1]
PRIVATE = "PRIVATE_SOURCE_PATH_OR_EXCEPTION"
ARGS = ["--pdf", f"{PRIVATE}.pdf", "--output", f"{PRIVATE}.json"]


def _report(pages=None, *, failed=0, deferred=0, empty=0):
    report = {
        "summary": {
            "selected": max(1, failed + empty), "deferred": deferred,
            "review_required": 0 if failed or empty else 1,
            "empty": empty, "failed": failed,
        },
        "private": PRIVATE,
    }
    if pages is not None:
        report["pages"] = pages
    return report


def _failed(code):
    return {
        "page_number": 1, "status": "retry_failed", "error_code": code,
        "original_text": PRIVATE, "error": PRIVATE, "source_path": PRIVATE,
    }


@pytest.mark.parametrize("arguments", [
    [], ["--pdf", f"{PRIVATE}.pdf"],
    ARGS + ["--pages", PRIVATE],
    ARGS + ["--pages"],
    ARGS + ["--max-pages", PRIVATE],
    ARGS + ["--max-pages", "0"],
    ARGS + ["--max-pages", "21"],
    ARGS + ["--dpi", PRIVATE],
    ARGS + ["--dpi", "200"],
    ARGS + ["--preprocess", PRIVATE],
    ARGS + [f"--{PRIVATE}"],
    ARGS + [PRIVATE],
])
def test_argument_failures_never_echo_values_or_run_recovery(monkeypatch, capsys, arguments):
    def unexpected(*args, **kwargs):
        pytest.fail("invalid arguments ran recovery")

    monkeypatch.setattr(cli, "recover_pdf", unexpected)
    monkeypatch.setattr(sys, "argv", [f"{PRIVATE}/private_program.py"])
    with pytest.raises(SystemExit) as error:
        cli.main(arguments)
    assert error.value.code == 2
    captured = capsys.readouterr()
    assert captured.out == ""
    assert "usage: retry_ocr.py" in captured.err
    assert "OCR retry arguments are invalid" in captured.err
    assert "--help" in captured.err
    assert "docs/ocr-accuracy.md" in captured.err
    assert PRIVATE not in captured.err


def test_policy_validation_exception_details_are_not_exposed(monkeypatch, capsys):
    def fail(**kwargs):
        raise ValueError(PRIVATE)

    monkeypatch.setattr(cli, "RetryPolicy", fail)
    with pytest.raises(SystemExit) as error:
        cli.main(ARGS)
    assert error.value.code == 2
    captured = capsys.readouterr()
    assert PRIVATE not in captured.out + captured.err
    assert "supported ranges" in captured.err


@pytest.mark.parametrize(("code", "category", "action"), [
    ("retry_limit_or_validation", "validation or raster limit failures", "try 300 DPI"),
    ("retry_runtime_unavailable", "failures from unavailable OCR dependencies", "required OCR dependencies"),
    ("retry_runtime_failed", "OCR runtime failures", "verified local models and runtime setup"),
])
def test_known_failure_categories_offer_fixed_private_actions(monkeypatch, capsys, code, category, action):
    report = _report([_failed(code), _failed(code)], failed=2)
    original = copy.deepcopy(report)
    monkeypatch.setattr(cli, "recover_pdf", lambda *args, **kwargs: report)

    assert cli.main(ARGS) == 3

    captured = capsys.readouterr()
    assert f"Failure guidance: 2 {category}." in captured.out
    assert action in captured.out
    assert "new output" in captured.out
    assert "docs/ocr-accuracy.md" in captured.out
    assert PRIVATE not in captured.out + captured.err
    assert captured.err == ""
    assert report == original


def test_mixed_failures_are_counted_in_fixed_order_without_reading_other_page_fields(monkeypatch, capsys):
    report = _report([
        _failed("retry_runtime_failed"),
        _failed("retry_runtime_unavailable"),
        _failed("retry_limit_or_validation"),
        _failed("retry_runtime_failed"),
        {"status": "review_required", "error_code": "retry_runtime_failed", "text": PRIVATE},
    ], failed=4)
    monkeypatch.setattr(cli, "recover_pdf", lambda *args, **kwargs: report)
    assert cli.main(ARGS) == 3
    captured = capsys.readouterr()
    lines = [line for line in captured.out.splitlines() if line.startswith("Failure guidance:")]
    assert len(lines) == 3
    assert lines[0].startswith("Failure guidance: 1 validation or raster limit failure.")
    assert lines[1].startswith("Failure guidance: 1 failure from unavailable OCR dependencies.")
    assert lines[2].startswith("Failure guidance: 2 OCR runtime failures.")
    assert PRIVATE not in captured.out + captured.err


@pytest.mark.parametrize("pages", [
    [], [_failed(PRIVATE)], [_failed([PRIVATE])], [PRIVATE, None, {}],
    [{"status": "review_required", "error_code": "retry_runtime_failed", "text": PRIVATE}],
])
def test_unknown_or_nonfailed_page_entries_do_not_leak_or_invent_guidance(monkeypatch, capsys, pages):
    monkeypatch.setattr(cli, "recover_pdf", lambda *args, **kwargs: _report(pages, failed=1))
    assert cli.main(ARGS) == 3
    captured = capsys.readouterr()
    assert "Failure guidance:" not in captured.out
    assert PRIVATE not in captured.out + captured.err


@pytest.mark.parametrize("pages", [None, PRIVATE, {"private": PRIVATE}])
def test_summary_only_and_unavailable_page_lists_remain_supported(monkeypatch, capsys, pages):
    report = _report(failed=1)
    if pages is not None:
        report["pages"] = pages
    monkeypatch.setattr(cli, "recover_pdf", lambda *args, **kwargs: report)
    assert cli.main(ARGS) == 3
    captured = capsys.readouterr()
    assert "1 failed" in captured.out
    assert "Failure guidance:" not in captured.out
    assert PRIVATE not in captured.out + captured.err


@pytest.mark.parametrize(("values", "expected"), [
    ({}, 0), ({"deferred": 1}, 3), ({"empty": 1}, 3), ({"failed": 1}, 3),
])
def test_guidance_keeps_summary_exit_contract(monkeypatch, capsys, values, expected):
    monkeypatch.setattr(cli, "recover_pdf", lambda *args, **kwargs: _report(**values))
    assert cli.main(ARGS) == expected
    captured = capsys.readouterr()
    assert "Original extraction unchanged" in captured.out
    assert "accuracy" in captured.out
    assert "Failure guidance:" not in captured.out


def test_valid_arguments_keep_existing_recovery_call_contract(monkeypatch):
    calls = []

    def recover(*args, **kwargs):
        calls.append((args, kwargs))
        return _report()

    monkeypatch.setattr(cli, "recover_pdf", recover)
    assert cli.main(ARGS + ["--pages", "7", "3", "--dpi", "400", "--max-pages", "2",
                            "--preprocess", "contrast", "--evidence", "evidence.json"]) == 0
    args, kwargs = calls[0]
    assert args == (Path(ARGS[1]), Path(ARGS[3]))
    assert kwargs["requested_pages"] == (7, 3)
    assert kwargs["evidence_path"] == Path("evidence.json")
    assert kwargs["policy"].dpi == 400
    assert kwargs["policy"].max_pages == 2
    assert kwargs["policy"].preprocessing == "contrast"


def test_real_help_describes_supported_ranges_and_review_workflow():
    result = subprocess.run(
        [sys.executable, str(PROJECT_ROOT / "tools" / "retry_ocr.py"), "--help"],
        capture_output=True, text=True, timeout=30, cwd=PROJECT_ROOT, check=False)
    assert result.returncode == 0, result.stderr
    for value in ("1..5000", "1..20", "300", "400", "--preprocess", "--evidence", "docs/ocr-accuracy.md"):
        assert value in result.stdout
    assert "Accuracy still requires source review" in " ".join(result.stdout.split())


def test_real_invalid_argument_failure_is_private():
    result = subprocess.run(
        [sys.executable, str(PROJECT_ROOT / "tools" / "retry_ocr.py"), *ARGS, "--pages", PRIVATE],
        capture_output=True, text=True, timeout=30, cwd=PROJECT_ROOT, check=False)
    assert result.returncode == 2
    assert result.stdout == ""
    assert "OCR retry arguments are invalid" in result.stderr
    assert PRIVATE not in result.stderr
    assert "Traceback" not in result.stderr
