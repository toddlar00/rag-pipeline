"""Content-silent OCR deadline binding; no PDF or OCR runtime is involved."""

from __future__ import annotations

import dataclasses
import math
import subprocess
import sys
import textwrap
import time
from pathlib import Path

import pytest

import ocr_recovery_supervision as supervision
import process_supervision


@pytest.mark.parametrize("value", [1, 1.0, 1.25, 60, 86400, 86400.0])
def test_timeout_accepts_only_bounded_finite_numbers(value):
    result = supervision.validate_timeout(value)
    assert type(result) is float
    assert result == value


@pytest.mark.parametrize("value", [
    True, False, None, "1", "private input", [], {}, object(),
    0, -1, 0.999, 86401, float("inf"), float("-inf"), float("nan"),
    math.nextafter(86400.0, math.inf), 10 ** 1000,
])
def test_timeout_rejects_invalid_types_ranges_and_overflow_without_echo(value):
    with pytest.raises(ValueError) as captured:
        supervision.validate_timeout(value)
    assert str(captured.value) == supervision._TIMEOUT_ERROR
    assert "private input" not in str(captured.value)


def test_invalid_timeout_precedes_argument_validation_and_any_supervisor(monkeypatch):
    monkeypatch.setattr(
        process_supervision, "_run_cli_with_deadline",
        lambda *_args, **_kwargs: pytest.fail("supervisor must not run"))
    with pytest.raises(ValueError, match="OCR timeout"):
        supervision.run_retry_with_deadline(None, timeout=True)


@pytest.mark.parametrize("arguments", [
    None, (), "--pdf", [None], [1], [True], [Path("source.pdf")], ["secret\0value"],
])
def test_invalid_worker_argument_shapes_are_rejected_before_launch(monkeypatch, arguments):
    monkeypatch.setattr(
        process_supervision, "_run_cli_with_deadline",
        lambda *_args, **_kwargs: pytest.fail("supervisor must not run"))
    with pytest.raises(ValueError) as captured:
        supervision.run_retry_with_deadline(arguments, timeout=1)
    assert "secret" not in str(captured.value)


@pytest.mark.parametrize("option", [
    "--timeout-seconds", "--timeout-seconds=2", "--timeout", "--timeout=2",
    "--timeout-second", "--t", "--t=2", "--time",
])
def test_forwarded_timeout_options_cannot_recursively_supervise(monkeypatch, option):
    monkeypatch.setattr(
        process_supervision, "_run_cli_with_deadline",
        lambda *_args, **_kwargs: pytest.fail("supervisor must not run"))
    with pytest.raises(ValueError, match="must not include a timeout"):
        supervision.run_retry_with_deadline(["--pdf", "source.pdf", option], timeout=1)


def test_supervisor_receives_only_the_fixed_contained_silent_ocr_binding(monkeypatch):
    captured = {}
    arguments = ["--pdf", "source.pdf", "--output", "review.json", "--pages", "2"]

    def fake_supervisor(script, child_arguments, **kwargs):
        captured.update(script=script, arguments=child_arguments, **kwargs)
        return 3

    monkeypatch.setattr(process_supervision, "_run_cli_with_deadline", fake_supervisor)
    assert supervision.run_retry_with_deadline(arguments, timeout=2) == 3
    assert captured == {
        "script": Path(supervision.__file__).with_name("tools") / "retry_ocr.py",
        "arguments": arguments,
        "operation": "ocr-retry",
        "timeout": 2.0,
        "config": supervision._CONFIG,
        "warn_fn": supervision._warn_deadline,
        "cleanup_error_type": supervision.SupervisorCleanupError,
        "stdout_target": subprocess.DEVNULL,
        "stderr_target": subprocess.DEVNULL,
    }
    assert captured["arguments"] is not arguments
    assert dataclasses.asdict(captured["config"]) == {
        "supervised_child_env": "RAG_PIPELINE_SUPERVISED_CHILD",
        "run_id_env": "RAG_PIPELINE_RUN_ID",
        "terminate_grace": 5.0, "poll_interval": 0.2, "start_gate_timeout": 60.0,
    }
    with pytest.raises(dataclasses.FrozenInstanceError):
        captured["config"].terminate_grace = 1.0


@pytest.mark.parametrize("existing_value", ["1", "0", "private input"])
def test_inherited_supervision_environment_never_bypasses_timeout(monkeypatch, existing_value):
    monkeypatch.setenv("RAG_PIPELINE_SUPERVISED_CHILD", existing_value)
    calls = []
    monkeypatch.setattr(
        process_supervision, "_run_cli_with_deadline",
        lambda *args, **kwargs: calls.append((args, kwargs)) or 0)
    assert supervision.run_retry_with_deadline([], timeout=1) == 0
    assert len(calls) == 1


@pytest.mark.parametrize("exit_code", [0, 1, 2, 3, 124, 125, 130, 143, -9])
def test_child_exit_status_is_not_reinterpreted_as_success(monkeypatch, capsys, exit_code):
    monkeypatch.setattr(
        process_supervision, "_run_cli_with_deadline",
        lambda *_args, **_kwargs: exit_code)
    assert supervision.run_retry_with_deadline([], timeout=1) == exit_code
    output = capsys.readouterr()
    assert output.out == ""
    if exit_code == 130:
        assert output.err == supervision._CANCELLATION_WARNING + "\n"
        assert "may remain" in output.err
    else:
        assert output.err == ""


def test_generic_warning_content_is_never_exposed(monkeypatch, capsys):
    def fake_supervisor(*_args, **kwargs):
        kwargs["warn_fn"]("private source text; index secret path; vector-store lease")
        return 124

    monkeypatch.setattr(process_supervision, "_run_cli_with_deadline", fake_supervisor)
    assert supervision.run_retry_with_deadline([], timeout=1) == 124
    output = capsys.readouterr()
    assert output.out == ""
    assert output.err == supervision._DEADLINE_WARNING + "\n"
    assert "may remain" in output.err
    assert "index" not in output.err
    assert "private source text" not in output.err


def test_cleanup_failure_stays_distinct_and_does_not_print_exception(monkeypatch, capsys):
    assert supervision.SupervisorCleanupError is process_supervision._SupervisorCleanupError
    error = supervision.SupervisorCleanupError("private source content")

    def fail(*_args, **_kwargs):
        raise error

    monkeypatch.setattr(process_supervision, "_run_cli_with_deadline", fail)
    with pytest.raises(supervision.SupervisorCleanupError) as captured:
        supervision.run_retry_with_deadline([], timeout=1)
    assert captured.value is error
    assert capsys.readouterr() == ("", "")


def test_import_does_not_load_pdf_ocr_array_or_facade_dependencies():
    result = subprocess.run(
        [sys.executable, "-c", (
            "import sys; import ocr_recovery_supervision; "
            "forbidden = {'rag', 'runtime_supervision', 'ocr_recovery_runtime', "
            "'pymupdf', 'rapidocr', 'onnxruntime', 'cv2', 'numpy'}; "
            "assert not forbidden.intersection(sys.modules)"
        )],
        stdin=subprocess.DEVNULL, capture_output=True, text=True,
        timeout=30, check=False,
    )
    assert result.returncode == 0, result.stderr


@pytest.mark.parametrize("exit_code", [0, 3])
def test_real_worker_output_is_suppressed_even_on_normal_exit(
        monkeypatch, tmp_path, capfd, exit_code):
    script = tmp_path / "synthetic_worker.py"
    script.write_text(
        "import sys\n"
        "print('SYNTHETIC-PRIVATE-STDOUT', flush=True)\n"
        "print('SYNTHETIC-PRIVATE-STDERR', file=sys.stderr, flush=True)\n"
        f"raise SystemExit({exit_code})\n", encoding="utf-8")
    monkeypatch.setattr(supervision, "_RETRY_SCRIPT_PATH", script)
    assert supervision.run_retry_with_deadline([], timeout=10) == exit_code
    assert capfd.readouterr() == ("", "")


def test_real_stalled_worker_is_reaped_without_claiming_output_absence(
        monkeypatch, tmp_path, capfd):
    script = tmp_path / "synthetic_stall.py"
    committed = tmp_path / "synthetic_commit.json"
    script.write_text(
        "from pathlib import Path\nimport sys\nimport time\n"
        "Path(sys.argv[1]).write_text('{\"synthetic\":true}', encoding='utf-8')\n"
        "print('SYNTHETIC-PRIVATE-STDOUT', flush=True)\n"
        "print('SYNTHETIC-PRIVATE-STDERR', file=sys.stderr, flush=True)\n"
        "time.sleep(60)\n", encoding="utf-8")
    monkeypatch.setattr(supervision, "_RETRY_SCRIPT_PATH", script)
    observed = {}
    real_supervisor = process_supervision._run_cli_with_deadline

    def with_observation(*args, **kwargs):
        return real_supervisor(
            *args, **kwargs,
            on_child_started=lambda process: observed.update(process=process))

    monkeypatch.setattr(process_supervision, "_run_cli_with_deadline", with_observation)
    assert supervision.run_retry_with_deadline([str(committed)], timeout=2) == 124
    assert observed["process"].poll() is not None
    assert committed.read_text(encoding="utf-8") == '{"synthetic":true}'
    output = capfd.readouterr()
    assert output.out == ""
    assert output.err == supervision._DEADLINE_WARNING + "\n"


@pytest.mark.parametrize("cancel", [False, True])
def test_real_worker_descendants_stop_before_timeout_or_cancellation_returns(
        monkeypatch, tmp_path, capfd, cancel):
    script = tmp_path / "synthetic_tree.py"
    child_script = tmp_path / "synthetic_descendant.py"
    heartbeat = tmp_path / "synthetic_heartbeat.txt"
    child_script.write_text(textwrap.dedent("""\
        from pathlib import Path
        import sys
        import time

        target = Path(sys.argv[1])
        print('SYNTHETIC-PRIVATE-CHILD-STDOUT', flush=True)
        print('SYNTHETIC-PRIVATE-CHILD-STDERR', file=sys.stderr, flush=True)
        for value in range(1200):
            target.write_text(str(value), encoding='ascii')
            time.sleep(0.05)
        """), encoding="utf-8")
    script.write_text(textwrap.dedent("""\
        import subprocess
        import sys
        import time

        subprocess.Popen([sys.executable, '-u', sys.argv[1], sys.argv[2]])
        time.sleep(60)
        """), encoding="utf-8")
    monkeypatch.setattr(supervision, "_RETRY_SCRIPT_PATH", script)
    observed = {}
    real_supervisor = process_supervision._run_cli_with_deadline

    def with_observation(*args, **kwargs):
        return real_supervisor(
            *args, **kwargs,
            on_child_started=lambda process: observed.update(process=process),
            cancel_requested=heartbeat.exists if cancel else None)

    monkeypatch.setattr(process_supervision, "_run_cli_with_deadline", with_observation)
    assert supervision.run_retry_with_deadline(
        [str(child_script), str(heartbeat)], timeout=3) == (130 if cancel else 124)
    assert observed["process"].poll() is not None
    assert heartbeat.exists()
    before = heartbeat.read_bytes()
    time.sleep(0.2)
    assert heartbeat.read_bytes() == before
    output = capfd.readouterr()
    assert output.out == ""
    expected = supervision._CANCELLATION_WARNING if cancel else supervision._DEADLINE_WARNING
    assert output.err == expected + "\n"
