"""Content-free CLI behavior and real saved-artifact subprocess integration."""

from __future__ import annotations

import argparse
import builtins
import json
from pathlib import Path
import subprocess
import sys

import pytest

from ocr_recovery import ReportCleanupError
from tools import inspect_ocr_omissions as cli
from test_ocr_docling import PRIVATE
from test_ocr_omission import inspect
from test_ocr_omission_io import make_files


ROOT = Path(__file__).resolve().parents[1]
ARGS = ["--pdf=PRIVATE_SOURCE.pdf", "--recovery=PRIVATE_RECOVERY.json",
        "--proposals=PRIVATE_PROPOSALS.json", "--output=PRIVATE_OUTPUT.json"]


@pytest.fixture
def files(tmp_path):
    return make_files(tmp_path)


def assert_private_absent(text):
    for value in (PRIVATE, "PRIVATE_SOURCE", "PRIVATE_RECOVERY", "PRIVATE_PROPOSALS", "PRIVATE_OUTPUT", "Traceback"):
        assert value not in text


@pytest.mark.parametrize("attention,expected", [(False, 0), (True, 3)])
def test_forwarding_and_content_free_summary(monkeypatch, capsys, attention, expected):
    report = inspect()
    report["requires_attention"] = attention
    calls = []

    def run(*args):
        calls.append(args)
        return report

    monkeypatch.setattr(cli, "inspect_omission_files", run)
    assert cli.main(ARGS) == expected
    assert calls == [(Path("PRIVATE_SOURCE.pdf"), Path("PRIVATE_RECOVERY.json"),
                      Path("PRIVATE_PROPOSALS.json"), Path("PRIVATE_OUTPUT.json"))]
    output = capsys.readouterr().out
    assert "1 evaluated of 1 source pages" in output
    assert "4 text/heading/table targets" in output
    assert "not independent scan ink or complete source text" in output
    assert_private_absent(output)


@pytest.mark.parametrize("arguments", [[], ["--PRIVATE_SOURCE=secret"], ARGS + ["PRIVATE_SOURCE"],
                                       ARGS + ["--unknown=PRIVATE_SOURCE"],
                                       ["--pdf", "--recovery=x", "--proposals=x", "--output=x"],
                                       ["--pd=PRIVATE_SOURCE", *ARGS[1:]]])
def test_bad_arguments_have_static_errors_without_arbitrary_values(arguments, capsys):
    with pytest.raises(SystemExit) as caught:
        cli.main(arguments)
    assert caught.value.code == 2
    output = capsys.readouterr().err
    assert "arguments are invalid" in output
    assert "docs/ocr-omissions.md" in output
    assert_private_absent(output)


@pytest.mark.parametrize("exception,code,message", [
    (ValueError, 2, "could not complete"), (RuntimeError, 2, "could not complete"),
    (ImportError, 2, "could not complete"), (KeyError, 2, "could not complete"),
    (FileExistsError, 2, "choose a new --output"),
    (ReportCleanupError, 2, "was created"), (KeyboardInterrupt, 130, "cancelled"),
])
def test_failure_and_cancellation_messages_do_not_expose_inputs(monkeypatch, capsys, exception, code, message):
    def fail(*_args):
        raise exception(PRIVATE)

    monkeypatch.setattr(cli, "inspect_omission_files", fail)
    assert cli.main(ARGS) == code
    output = capsys.readouterr()
    assert message in output.err
    assert output.out == ""
    assert_private_absent(output.err)


@pytest.mark.parametrize("phase", ["parse", "import", "summary"])
def test_cancellation_at_all_outer_boundaries_is_sanitized(monkeypatch, capsys, phase):
    def cancel(*_args, **_kwargs):
        raise KeyboardInterrupt(PRIVATE)

    monkeypatch.setattr(cli, "inspect_omission_files", lambda *_args: inspect())
    if phase == "parse":
        monkeypatch.setattr(argparse.ArgumentParser, "parse_args", cancel)
    elif phase == "summary":
        monkeypatch.setattr(cli, "_print_report", cancel)
    else:
        actual = builtins.__import__

        def importing(name, *args, **kwargs):
            if name == "ocr_recovery":
                cancel()
            return actual(name, *args, **kwargs)

        monkeypatch.setattr(builtins, "__import__", importing)
    assert cli.main(ARGS) == 130
    output = capsys.readouterr().err
    assert "may already exist" in output
    assert_private_absent(output)


@pytest.mark.parametrize("bad", [PRIVATE, True, -1, 10001])
def test_unexpected_summary_values_are_not_printed(monkeypatch, capsys, bad):
    report = inspect()
    report["summary"]["evaluated_pages"] = bad
    monkeypatch.setattr(cli, "inspect_omission_files", lambda *_args: report)
    assert cli.main(ARGS) == 2
    output = capsys.readouterr()
    assert output.out == ""
    assert_private_absent(output.err)


def test_real_help_is_dependency_light_and_documents_limits():
    script = ("import sys; from tools.inspect_ocr_omissions import main; "
              "assert 'ocr_omission' not in sys.modules; "
              "assert 'ocr_omission_io' not in sys.modules; main(['--help'])")
    result = subprocess.run([sys.executable, "-c", script], cwd=ROOT, text=True, capture_output=True,
                            check=False, timeout=20)
    assert result.returncode == 0
    output = " ".join(result.stdout.split())
    for flag in ("--pdf", "--recovery", "--proposals", "--output"):
        assert flag in output
    assert "256 MiB" in output
    assert "not completeness certification" in output
    assert "docs/ocr-omissions.md" in output


def test_actual_cli_saved_json_smoke_and_create_only_retry(files):
    arguments = [f"--{flag}={files[key]}" for flag, key in
                 (("pdf", "source"), ("recovery", "recovery"), ("proposals", "proposals"), ("output", "output"))]
    command = [sys.executable, "tools/inspect_ocr_omissions.py", *arguments]
    before = {key: path.read_bytes() for key, path in files.items() if key != "output"}
    result = subprocess.run(command, cwd=ROOT, text=True, capture_output=True, check=False, timeout=30)
    assert result.returncode == 3, result.stderr
    assert "1 without line overlap" in result.stdout
    assert_private_absent(result.stdout + result.stderr)
    assert str(files["source"]) not in result.stdout + result.stderr
    saved = files["output"].read_bytes()
    assert json.loads(saved)["summary"]["attention_regions"] == 1
    repeated = subprocess.run(command, cwd=ROOT, text=True, capture_output=True, check=False, timeout=30)
    assert repeated.returncode == 2
    assert "choose a new --output" in repeated.stderr
    assert files["output"].read_bytes() == saved
    assert all(path.read_bytes() == before[key] for key, path in files.items() if key != "output")
