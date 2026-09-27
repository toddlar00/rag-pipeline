"""Content-silent stage CLI routing, deadlines, cancellation and strict readback."""

from pathlib import Path
import subprocess
import sys

import pytest

import ocr_stage_io
import process_supervision
from tools import diagnose_ocr_stages as cli


PRIVATE = "PRIVATE-source-and-reference-text"


@pytest.fixture
def seams(monkeypatch):
    seen = {"requests": [], "workers": [], "supervised": [], "readback": []}
    request = object()
    bundle = {"diagnostics": {"coverage": {"selected_pages": [1, 6, 7], "reference_lines": 48,
              "available_gold_crops": 47, "selected_gold_crops": 48, "paired_full_and_gold_crops": 40}}}

    def prepare(*args, **kwargs):
        seen["requests"].append((args, kwargs))
        return request

    def supervise(*args, **kwargs):
        seen["supervised"].append((args, kwargs))
        return 3

    def read(*args, **kwargs):
        seen["readback"].append((args, kwargs))
        return bundle

    monkeypatch.setattr(ocr_stage_io, "stage_request", prepare)
    monkeypatch.setattr(ocr_stage_io, "run_stage_bundle", lambda *args, **kwargs: seen["workers"].append((args, kwargs)))
    monkeypatch.setattr(ocr_stage_io, "read_stage_completion", read)
    monkeypatch.setattr(process_supervision, "_run_cli_with_deadline", supervise)
    monkeypatch.delenv("RAG_OCR_STAGE_CHILD", raising=False)
    return seen


def args():
    return [f"--pdf=-{PRIVATE}.pdf", f"--references=-{PRIVATE}.json", f"--output-dir=-{PRIVATE}-output",
            f"--installation=-{PRIVATE}-installation.json"]


def test_success_contains_worker_then_strict_readback_and_only_safe_counts(seams, capsys):
    assert cli.main(args() + ["--timeout-seconds", "17"]) == 3
    assert len(seams["requests"]) == len(seams["supervised"]) == len(seams["readback"]) == 1
    assert not seams["workers"]
    positional, kwargs = seams["supervised"][0]
    assert positional[0] == Path(cli.__file__)
    assert positional[1] == args() + ["--worker"]
    assert kwargs["timeout"] == 17.0
    assert kwargs["stdout_target"] == kwargs["stderr_target"] == subprocess.DEVNULL
    assert kwargs["environment_overrides"]["HF_HUB_OFFLINE"] == "1"
    assert kwargs["environment_overrides"]["PYTHONNOUSERSITE"] == "1"
    assert kwargs["config"].supervised_child_env == "RAG_OCR_STAGE_CHILD"
    output = capsys.readouterr()
    assert PRIVATE not in output.out + output.err
    assert "47/48" in output.out and "48 declared gold lines" in output.out
    assert "not an additive causal explanation" in output.out


@pytest.mark.parametrize("code", [124, 130, 2, 0, -9, 99])
def test_aborted_or_failed_worker_is_never_read_as_success(seams, monkeypatch, capsys, code):
    monkeypatch.setattr(process_supervision, "_run_cli_with_deadline", lambda *_a, **_k: code)
    assert cli.main(args()) == (code if code in (124, 130) else 2)
    assert not seams["readback"]
    output = capsys.readouterr()
    assert "remain" in output.err and PRIVATE not in output.out + output.err
    assert "Gold-crop recognition:" not in output.out


@pytest.mark.parametrize("boundary", ["parse", "request", "supervise", "readback", "summary"])
@pytest.mark.parametrize("error,code", [(ValueError(PRIVATE), 2), (KeyboardInterrupt(PRIVATE), 130)])
def test_exceptions_and_late_cancellation_remain_content_silent(seams, monkeypatch, capsys, boundary, error, code):
    def fail(*_args, **_kwargs):
        raise error
    targets = {"parse": (cli._Parser, "parse_args"), "request": (ocr_stage_io, "stage_request"),
               "supervise": (process_supervision, "_run_cli_with_deadline"),
               "readback": (ocr_stage_io, "read_stage_completion"), "summary": (cli, "_print_summary")}
    module, name = targets[boundary]
    monkeypatch.setattr(module, name, fail)
    assert cli.main(args()) == code
    output = capsys.readouterr()
    assert PRIVATE not in output.out + output.err and "remain" in output.err
    assert "Gold-crop recognition:" not in output.out


@pytest.mark.parametrize("extra", [["--timeout-seconds", "0"], ["--timeout-seconds", "3601"],
                                   ["--timeout-seconds", "NaN"], ["--timeout-seconds", PRIVATE],
                                   [f"--unknown={PRIVATE}"], [f"--ref={PRIVATE}"], ["--dpi", "300"],
                                   ["--timeout-seconds", "1.5"]])
def test_invalid_options_do_not_echo_values_or_start_work(seams, capsys, extra):
    assert cli.main(args() + extra) == 2
    assert not seams["requests"] and not seams["supervised"] and not seams["workers"]
    output = capsys.readouterr()
    assert PRIVATE not in output.out + output.err


def test_missing_arguments_are_static_errors(seams, capsys):
    assert cli.main([f"--pdf={PRIVATE}"]) == 2
    assert not seams["requests"]
    assert PRIVATE not in capsys.readouterr().err


def test_inherited_worker_environment_does_not_bypass_parent_supervision(seams, monkeypatch):
    monkeypatch.setenv("RAG_OCR_STAGE_CHILD", "1")
    assert cli.main(args()) == 3
    assert len(seams["supervised"]) == 1 and not seams["workers"]


def test_direct_worker_without_containment_marker_is_rejected(seams):
    assert cli.main(args() + ["--worker"]) == 2
    assert not seams["workers"] and not seams["supervised"]


def test_worker_branch_calls_only_fixed_operation_and_never_recurses(seams, monkeypatch, capsys):
    monkeypatch.setenv("RAG_OCR_STAGE_CHILD", "1")
    assert cli.main(args() + ["--worker"]) == 3
    assert len(seams["workers"]) == 1 and not seams["supervised"] and not seams["readback"]
    assert capsys.readouterr().out == ""


def test_real_help_does_not_import_optional_ocr_or_emit_hidden_worker_flag():
    result = subprocess.run([sys.executable, str(Path(cli.__file__)), "--help"], capture_output=True, text=True, timeout=30)
    assert result.returncode == 0 and not result.stderr
    text = " ".join(result.stdout.split())
    assert "--references" in text and "--installation" in text and "1 through 3600" in text
    assert "--worker" not in text and "docs/ocr-stage-diagnostics.md" in text


def test_help_uses_static_program_name_with_hostile_launcher(monkeypatch, capsys):
    monkeypatch.setattr(sys, "argv", [PRIVATE])
    with pytest.raises(SystemExit) as stopped:
        cli.main(["--help"])
    assert stopped.value.code == 0
    output = capsys.readouterr()
    assert PRIVATE not in output.out + output.err and "diagnose_ocr_stages.py" in output.out


def test_real_bad_input_is_static_and_creates_no_output(tmp_path):
    output = tmp_path / PRIVATE
    result = subprocess.run([sys.executable, str(Path(cli.__file__)), f"--pdf={tmp_path / PRIVATE}",
                             f"--references={tmp_path / (PRIVATE + '.json')}", f"--output-dir={output}",
                             f"--installation={tmp_path / 'installation.json'}"],
                            capture_output=True, text=True, timeout=30)
    assert result.returncode == 2 and PRIVATE not in result.stdout + result.stderr
    assert not output.exists()
