"""Static privacy errors, lazy help, and real generated-file CLI workflows."""

from __future__ import annotations

import argparse
import builtins
import json
from pathlib import Path
import subprocess
import sys

import pytest

from ocr_recovery import ReportCleanupError
from tools import suggest_ocr_columns as cli
from test_ocr_column_suggestions_io import PRIVATE, make_files


ROOT = Path(__file__).resolve().parents[1]
ARGS = ["--pdf=PRIVATE_SOURCE.pdf", "--recovery=PRIVATE_RECOVERY.json", "--output=PRIVATE_OUTPUT.json", "--pages", "1"]


def report():
    return {"requires_attention": True, "summary": {
        "source_pages": 8, "requested_pages": 3, "suggested_pages": 1,
        "abstained_pages": 1, "unavailable_pages": 1, "empty_candidate_pages": 0}}


def assert_private_absent(text):
    for value in (PRIVATE, "PRIVATE_SOURCE", "PRIVATE_RECOVERY", "PRIVATE_OUTPUT", "Traceback"):
        assert value not in text


def test_exact_forwarding_and_success_always_manual_review_exit(monkeypatch, capsys):
    calls = []

    def run(*args, **kwargs):
        calls.append((args, kwargs))
        return report()

    monkeypatch.setattr(cli, "suggest_column_files", run)
    assert cli.main(ARGS[:-1] + ["6", "1", "7"]) == 3
    assert calls == [((Path("PRIVATE_SOURCE.pdf"), Path("PRIVATE_RECOVERY.json"), Path("PRIVATE_OUTPUT.json")),
                      {"requested_pages": [6, 1, 7]})]
    output = capsys.readouterr()
    assert output.err == ""
    assert "3 requested of 8 source pages" in output.out
    assert "1 unconfirmed suggestions" in output.out
    assert "cannot distinguish prose from tables" in output.out
    assert "No OCR rerun, approval, or canonical text changes" in output.out
    assert_private_absent(output.out)


@pytest.mark.parametrize("arguments", [[], ["--PRIVATE_SOURCE=secret"], ARGS + ["--unknown=PRIVATE_SOURCE"],
    ["--pd=PRIVATE_SOURCE", *ARGS[1:]], ARGS[:-2], ARGS[:-1], ARGS[:-1] + ["PRIVATE_SOURCE"],
    ARGS[:-1] + ["1-3"], ARGS[:-1] + ["all"], ARGS[:-1] + ["0"], ARGS[:-1] + ["-1"],
    ARGS[:-1] + ["1.0"], ARGS[:-1] + ["+1"], ARGS[:-1] + ["１"], ARGS[:-1] + ["5001"],
    ARGS[:-1] + ["1", "1"], ARGS[:-1] + [str(n) for n in range(1, 22)], ARGS[:-1] + ["1" * 1000]])
def test_invalid_arguments_never_echo_values(arguments, capsys):
    with pytest.raises(SystemExit) as caught:
        cli.main(arguments)
    assert caught.value.code == 2
    output = capsys.readouterr()
    assert output.out == ""
    assert "arguments are invalid" in output.err
    assert_private_absent(output.err)


@pytest.mark.parametrize("exception,expected,message", [
    (ValueError, 2, "could not complete"), (RuntimeError, 2, "could not complete"),
    (ImportError, 2, "could not complete"), (PermissionError, 2, "could not complete"),
    (TimeoutError, 2, "could not complete"), (KeyError, 2, "could not complete"),
    (FileExistsError, 2, "choose a new --output"),
    (ReportCleanupError, 2, "was created"), (KeyboardInterrupt, 130, "cancelled"),
])
def test_errors_and_cancel_are_static(monkeypatch, capsys, exception, expected, message):
    def fail(*_args, **_kwargs):
        raise exception(PRIVATE)

    monkeypatch.setattr(cli, "suggest_column_files", fail)
    assert cli.main(ARGS) == expected
    output = capsys.readouterr()
    assert output.out == ""
    assert message in output.err
    assert_private_absent(output.err)


@pytest.mark.parametrize("phase", ["parse", "import", "summary", "print"])
def test_cancellation_including_post_publication_print_is_honest(monkeypatch, capsys, phase):
    def cancel(*_args, **_kwargs):
        raise KeyboardInterrupt(PRIVATE)

    monkeypatch.setattr(cli, "suggest_column_files", lambda *_args, **_kwargs: report())
    if phase == "parse":
        monkeypatch.setattr(argparse.ArgumentParser, "parse_args", cancel)
    elif phase == "summary":
        monkeypatch.setattr(cli, "_print_report", cancel)
    elif phase == "print":
        actual = builtins.print

        def printing(*args, **kwargs):
            if kwargs.get("file") is not sys.stderr:
                cancel()
            actual(*args, **kwargs)

        monkeypatch.setattr(builtins, "print", printing)
    else:
        actual = builtins.__import__

        def importing(name, *args, **kwargs):
            if name == "ocr_recovery":
                cancel()
            return actual(name, *args, **kwargs)

        monkeypatch.setattr(builtins, "__import__", importing)
    assert cli.main(ARGS) == 130
    output = capsys.readouterr()
    assert "may already exist" in output.err
    assert_private_absent(output.out + output.err)


@pytest.mark.parametrize("value", [True, -1, 5001, 1.0, PRIVATE, None])
def test_summary_values_are_validated_before_any_print(monkeypatch, capsys, value):
    result = report()
    result["summary"]["suggested_pages"] = value
    monkeypatch.setattr(cli, "suggest_column_files", lambda *_args, **_kwargs: result)
    assert cli.main(ARGS) == 2
    output = capsys.readouterr()
    assert output.out == ""
    assert_private_absent(output.err)


def test_false_automatic_success_cannot_change_exit_to_zero(monkeypatch, capsys):
    result = report()
    result["requires_attention"] = False
    monkeypatch.setattr(cli, "suggest_column_files", lambda *_args, **_kwargs: result)
    assert cli.main(ARGS) == 2
    assert capsys.readouterr().out == ""


@pytest.mark.parametrize("field,value", [
    ("suggested_pages", 0), ("suggested_pages", 2), ("abstained_pages", 3),
    ("unavailable_pages", 0), ("empty_candidate_pages", 1), ("requested_pages", 2),
])
def test_inconsistent_but_individually_valid_counts_cannot_print_success(monkeypatch, capsys, field, value):
    result = report()
    result["summary"][field] = value
    monkeypatch.setattr(cli, "suggest_column_files", lambda *_args, **_kwargs: result)
    assert cli.main(ARGS) == 2
    output = capsys.readouterr()
    assert output.out == ""
    assert "could not complete" in output.err
    assert_private_absent(output.err)


def test_real_help_is_dependency_light_and_explicit():
    script = ("import sys; from tools.suggest_ocr_columns import main; "
              "assert not any(n in sys.modules for n in ('ocr_column_suggestions', 'ocr_column_suggestions_io', "
              "'fitz', 'cv2', 'numpy', 'rapidocr', 'torch', 'rag')); main(['--help'])")
    result = subprocess.run([sys.executable, "-B", "-c", script], cwd=ROOT, text=True,
                            capture_output=True, check=False, timeout=20)
    assert result.returncode == 0
    output = " ".join(result.stdout.split())
    for value in ("--pdf", "--recovery", "--output", "--pages", "256 MiB", "64 MiB", "1 MiB",
                  "1 to 20", "manual review always required", "No PDF parser or model"):
        assert value in output


@pytest.mark.parametrize("mode", ["none", "contrast"])
def test_real_cli_generated_input_smoke_and_no_overwrite(tmp_path, mode):
    files = make_files(tmp_path, mode=mode)
    before = {key: path.read_bytes() for key, path in files.items() if key != "output"}
    args = [f"--{flag}={files[key]}" for flag, key in (("pdf", "source"), ("recovery", "recovery"), ("output", "output"))]
    command = [sys.executable, "-B", "tools/suggest_ocr_columns.py", *args, "--pages", "1"]
    first = subprocess.run(command, cwd=ROOT, text=True, capture_output=True, check=False, timeout=30)
    assert first.returncode == 3, first.stderr
    assert first.stderr == ""
    assert_private_absent(first.stdout)
    assert str(tmp_path) not in first.stdout
    saved = files["output"].read_bytes()
    assert json.loads(saved)["summary"]["suggested_pages"] == (1 if mode == "none" else 0)
    second = subprocess.run(command, cwd=ROOT, text=True, capture_output=True, check=False, timeout=30)
    assert second.returncode == 2
    assert "choose a new --output" in second.stderr
    assert_private_absent(second.stdout + second.stderr)
    assert files["output"].read_bytes() == saved
    assert all(path.read_bytes() == before[key] for key, path in files.items() if key != "output")


def test_real_cli_mismatched_source_cannot_emit_success(tmp_path):
    files = make_files(tmp_path)
    files["source"].write_bytes(b"Different generated bytes")
    args = [f"--{flag}={files[key]}" for flag, key in (("pdf", "source"), ("recovery", "recovery"), ("output", "output"))]
    result = subprocess.run([sys.executable, "-B", "tools/suggest_ocr_columns.py", *args, "--pages", "1"],
                            cwd=ROOT, text=True, capture_output=True, check=False, timeout=30)
    assert result.returncode == 2
    assert result.stdout == ""
    assert_private_absent(result.stderr)
    assert not files["output"].exists()
