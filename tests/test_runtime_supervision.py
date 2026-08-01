from __future__ import annotations

from dataclasses import FrozenInstanceError
import json
from pathlib import Path

import pytest

import process_supervision
import runtime_supervision


def test_default_binding_is_frozen_immutable_and_uses_exact_runtime_types():
    binding = runtime_supervision.default_runtime_binding()

    assert binding is runtime_supervision.default_runtime_binding()
    assert binding.script_path == Path(
        runtime_supervision.__file__).with_name("rag.py")
    assert binding.supervisor is runtime_supervision.run_cli_with_deadline
    assert binding.cleanup_error_type is (
        process_supervision._SupervisorCleanupError)
    assert dict(binding.operation_timeouts) == {
        "index": 7200.0,
        "query": 300.0,
        "info": 120.0,
        "full": 14400.0,
        "batch": 43200.0,
        "evaluation": 14400.0,
        "storage": 600.0,
    }
    with pytest.raises(FrozenInstanceError):
        binding.script_path = Path("replacement.py")
    with pytest.raises(TypeError):
        binding.operation_timeouts["index"] = 1.0


def test_binding_copies_timeout_policy_before_publication():
    source = {"index": 7}
    binding = runtime_supervision.PipelineRuntimeBinding(
        script_path=Path("pipeline.py"),
        supervisor=lambda *_args, **_kwargs: 0,
        cleanup_error_type=RuntimeError,
        operation_timeouts=source,
    )

    source["index"] = 11

    assert dict(binding.operation_timeouts) == {"index": 7.0}


@pytest.mark.parametrize(
    ("field", "value", "error"),
    [
        ("supervisor", None, TypeError),
        ("cleanup_error_type", RuntimeError("instance"), TypeError),
        ("cleanup_error_type", KeyboardInterrupt, TypeError),
        ("operation_timeouts", None, TypeError),
        ("operation_timeouts", {"index": True}, ValueError),
        ("operation_timeouts", {"index": float("inf")}, ValueError),
        ("operation_timeouts", {"index": 10 ** 10_000}, ValueError),
        ("operation_timeouts", {"": 1.0}, ValueError),
    ],
)
def test_binding_rejects_invalid_capabilities(field, value, error):
    values = {
        "script_path": Path("pipeline.py"),
        "supervisor": lambda *_args, **_kwargs: 0,
        "cleanup_error_type": RuntimeError,
        "operation_timeouts": {"index": 1.0},
    }
    values[field] = value

    with pytest.raises(error):
        runtime_supervision.PipelineRuntimeBinding(**values)


def test_concrete_supervisor_binds_exact_config_telemetry_and_cleanup_type(
        monkeypatch, tmp_path):
    captured = {}

    def core(script_path, argv, **kwargs):
        captured.update(script_path=script_path, argv=argv, kwargs=kwargs)
        return 23

    monkeypatch.setattr(
        runtime_supervision.process_supervision,
        "_run_cli_with_deadline",
        core,
    )
    events = tmp_path / "events.jsonl"
    report = tmp_path / "report.json"
    stdout = object()
    stderr = object()

    assert runtime_supervision.run_cli_with_deadline(
        tmp_path / "worker.py",
        ["index"],
        operation="index",
        timeout=9,
        working_directory=tmp_path,
        environment_overrides={"EXACT": "value"},
        run_id="run-1",
        run_events=events,
        run_report=report,
        cancel_requested=lambda: False,
        on_child_started=lambda _process: None,
        heartbeat=lambda _process: None,
        stdout_target=stdout,
        stderr_target=stderr,
    ) == 23

    options = captured["kwargs"]
    assert captured["script_path"] == tmp_path / "worker.py"
    assert captured["argv"] == ["index"]
    assert options["operation"] == "index"
    assert options["timeout"] == 9
    assert options["config"] == process_supervision.SupervisionConfig(
        supervised_child_env="RAG_PIPELINE_SUPERVISED_CHILD",
        run_id_env="RAG_PIPELINE_RUN_ID",
        terminate_grace=5.0,
        poll_interval=0.2,
        start_gate_timeout=60.0,
    )
    assert options["cleanup_error_type"] is (
        process_supervision._SupervisorCleanupError)
    assert callable(options["normalize_timeout_fn"])
    assert callable(options["telemetry_start_fn"])
    assert callable(options["telemetry_finalize_fn"])
    assert options["kill_job_factory"] is (
        process_supervision._WindowsKillJob)
    assert options["start_gate_factory"] is (
        process_supervision._new_supervised_start_gate)
    assert options["terminate_fn"] is (
        runtime_supervision._terminate_supervised_process)
    assert options["supervisor_signal_type"] is (
        process_supervision._SupervisorSignal)
    assert options["working_directory"] == tmp_path
    assert options["environment_overrides"] == {"EXACT": "value"}
    assert options["run_id"] == "run-1"
    assert options["run_events"] == events
    assert options["run_report"] == report
    assert callable(options["cancel_requested"])
    assert callable(options["on_child_started"])
    assert callable(options["heartbeat"])
    assert options["stdout_target"] is stdout
    assert options["stderr_target"] is stderr


def test_concrete_timeout_and_telemetry_callbacks_preserve_runtime_contract(
        tmp_path):
    events = tmp_path / "events.jsonl"
    report = tmp_path / "report.json"

    assert runtime_supervision._normalize_operation_timeout("2.5") == 2.5
    for invalid in (True, 0, float("inf")):
        with pytest.raises(ValueError, match="finite positive number"):
            runtime_supervision._normalize_operation_timeout(invalid)

    runtime_supervision._start_telemetry(
        "index",
        run_id="runtime-binding-run",
        run_events=events,
        run_report=report,
    )
    running = json.loads(report.read_text(encoding="utf-8"))
    assert running["operation"] == "index"
    assert running["run_id"] == "runtime-binding-run"
    assert running["status"] == "running"

    runtime_supervision._finalize_telemetry(
        "index",
        run_id="runtime-binding-run",
        run_events=events,
        run_report=report,
        status="failed",
        exc=TimeoutError("private injected detail"),
    )
    terminal_text = report.read_text(encoding="utf-8")
    terminal = json.loads(terminal_text)
    assert terminal["status"] == "failed"
    assert terminal["failure"]["category"] == "timeout"
    assert "private injected detail" not in terminal_text
