"""Static CLI failures, contained-worker wiring and strict no-OCR readback."""

import subprocess
from pathlib import Path
import sys

import pytest

import ocr_detection_disposition_io as workflow
import process_supervision
from tools import diagnose_ocr_dispositions as cli
from test_ocr_detection_disposition_io import FailedReader, case as case


def arguments(case):
    return ["--pdf", str(case.source), "--output-dir", str(case.output)]


def test_contained_parent_binds_exact_request_and_verifies_real_no_call_bundle(case, monkeypatch, capsys):
    observed = []

    def supervise(path, args, **kwargs):
        observed.append((path, args, kwargs))
        assert path == Path(cli.__file__)
        assert kwargs["stdout_target"] == kwargs["stderr_target"] == subprocess.DEVNULL
        assert kwargs["environment_overrides"]["HF_HUB_OFFLINE"] == "1"
        assert kwargs["environment_overrides"]["TRANSFORMERS_OFFLINE"] == "1"
        assert kwargs["environment_overrides"]["PYTHONNOUSERSITE"] == "1"
        assert args[-1] == "--worker"
        assert sum(arg.startswith("--expected-request-sha256=") for arg in args) == 1
        monkeypatch.setenv("RAG_OCR_DISPOSITION_CHILD", "1")
        return cli.main(args)

    monkeypatch.setattr(process_supervision, "_run_cli_with_deadline", supervise)
    assert cli.main(arguments(case) + ["--page", "3", "--max-pages", "1"]) == 3
    assert len(observed) == 1 and FailedReader.attempts == [3]
    output = capsys.readouterr()
    assert "bundle verified" in output.out and "0 actual OCR calls" in output.out
    assert "3 not run" in output.out and "accuracy" in output.out
    assert "PRIVATE" not in output.out + output.err


def test_verify_only_never_starts_an_ocr_worker(case, monkeypatch, capsys):
    workflow.run_disposition_bundle(case.source, case.output)
    initial_attempts = list(FailedReader.attempts)
    monkeypatch.setattr(process_supervision, "_run_cli_with_deadline", lambda *_a, **_kw: pytest.fail("no worker during readback"))
    assert cli.main(arguments(case) + ["--verify-only"]) == 3
    assert FailedReader.attempts == initial_attempts
    assert "bundle verified" in capsys.readouterr().out


@pytest.mark.parametrize("extra", [
    ["--timeout-seconds", "0"], ["--timeout-seconds", "3601"], ["--page", "0"],
    ["--page", "1", "--page", "1"], ["--max-pages", "0"], ["--dpi", "700"],
    ["--operation", "PRIVATE"], ["--pdf-unknown", "PRIVATE"], ["--pre", "contrast"],
    ["--checkpoint"], ["--resume"], ["--worker"], ["--worker", "--verify-only"],
    ["--expected-request-sha256", "a" * 64],
])
def test_invalid_arguments_or_uncontained_worker_are_static_and_do_not_write(case, extra, capsys):
    assert cli.main(arguments(case) + extra) == 2
    assert not case.output.exists() and FailedReader.attempts == []
    output = capsys.readouterr()
    assert "PRIVATE" not in output.out + output.err
    assert "may remain" in output.err and "bundle verified" not in output.out


@pytest.mark.parametrize("code", [0, 2, 124, 130])
def test_noncompletion_keeps_timeout_cancel_codes_without_success_claim(case, monkeypatch, capsys, code):
    monkeypatch.setattr(process_supervision, "_run_cli_with_deadline", lambda *_a, **_kw: code)
    assert cli.main(arguments(case)) == (code if code in (124, 130) else 2)
    output = capsys.readouterr()
    assert "may remain" in output.err and "bundle verified" not in output.out


def test_parent_rejects_late_source_change_after_worker_success(case, monkeypatch, capsys):
    def supervise(_path, _args, **_kwargs):
        workflow.run_disposition_bundle(case.source, case.output)
        case.source.write_bytes(b"changed source after worker")
        return 3

    monkeypatch.setattr(process_supervision, "_run_cli_with_deadline", supervise)
    assert cli.main(arguments(case)) == 2
    output = capsys.readouterr()
    assert "may remain" in output.err and "bundle verified" not in output.out
    assert (case.output / "manifest.json").exists()


def test_forged_success_without_manifest_is_not_accepted(case, monkeypatch, capsys):
    monkeypatch.setattr(process_supervision, "_run_cli_with_deadline", lambda *_a, **_kw: 3)
    assert cli.main(arguments(case)) == 2
    assert "bundle verified" not in capsys.readouterr().out


def test_parent_cancellation_propagates_static_warning(case, monkeypatch, capsys):
    def cancel(*_args, **_kwargs):
        raise KeyboardInterrupt()

    monkeypatch.setattr(process_supervision, "_run_cli_with_deadline", cancel)
    assert cli.main(arguments(case)) == 130
    assert "may remain" in capsys.readouterr().err


def test_help_works_in_fresh_process_without_loading_runtime():
    result = subprocess.run([sys.executable, str(Path(cli.__file__)), "--help"], capture_output=True, text=True, timeout=15)
    assert result.returncode == 0
    assert "--verify-only" in result.stdout and "--operation" in result.stdout
    assert "--expected-request-sha256" not in result.stdout and "--worker" not in result.stdout
    assert result.stderr == ""
