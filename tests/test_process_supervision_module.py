"""Direct tests for the extracted process-supervision policy module."""

import dataclasses
import os
import subprocess
import sys
from pathlib import Path

import pytest

import process_supervision as ps


def test_process_supervision_is_a_standard_library_leaf_module():
    result = subprocess.run(
        [
            sys.executable,
            "-c",
            (
                "import sys; import process_supervision; "
                "forbidden = {'rag', 'run_telemetry', 'cli_policy', "
                "'requests', 'chromadb', 'qdrant_client'}; "
                "loaded = forbidden.intersection(sys.modules); "
                "assert not loaded, sorted(loaded)"
            ),
        ],
        stdin=subprocess.DEVNULL,
        capture_output=True,
        check=False,
        text=True,
        timeout=30,
    )

    assert result.returncode == 0, result.stderr


def test_rag_facade_reexports_extracted_supervision_types(monkeypatch):
    import rag

    assert rag._WindowsKillJob is ps._WindowsKillJob
    assert rag._PosixSupervisedStartGate is ps._PosixSupervisedStartGate
    assert rag._WindowsSupervisedStartGate is ps._WindowsSupervisedStartGate
    assert rag._SupervisorSignal is ps._SupervisorSignal
    assert rag._SupervisorCleanupError is ps._SupervisorCleanupError
    assert not hasattr(rag, "_legacy_run_cli_with_deadline")
    assert not hasattr(rag, "_legacy_run_rag_entrypoint")

    expected = object()
    gate_name = (
        "_WindowsSupervisedStartGate"
        if os.name == "nt"
        else "_PosixSupervisedStartGate"
    )
    monkeypatch.setattr(rag, gate_name, lambda: expected)
    assert rag._new_supervised_start_gate() is expected


def test_rag_facade_late_binds_signal_and_cleanup_types(monkeypatch):
    import rag

    captured = {}

    class FacadeSignal(BaseException):
        pass

    class FacadeCleanupError(RuntimeError):
        pass

    monkeypatch.setattr(rag, "_SupervisorSignal", FacadeSignal)
    monkeypatch.setattr(rag, "_SupervisorCleanupError", FacadeCleanupError)
    monkeypatch.setattr(
        ps,
        "_run_cli_with_deadline",
        lambda *_args, **kwargs: captured.update(kwargs) or 0,
    )

    assert rag._run_cli_with_deadline(
        Path("worker.py"), [], operation="test", timeout=1
    ) == 0
    assert captured["supervisor_signal_type"] is FacadeSignal
    assert captured["cleanup_error_type"] is FacadeCleanupError


class FakeClock:
    def __init__(self, start: float = 100.0):
        self.now = start
        self.sleeps: list[float] = []

    def monotonic(self) -> float:
        return self.now

    def sleep(self, seconds: float) -> None:
        self.sleeps.append(seconds)
        self.now += seconds


def _config(**overrides) -> ps.SupervisionConfig:
    values = {
        "supervised_child_env": "TEST_SUPERVISED_CHILD",
        "run_id_env": "TEST_RUN_ID",
        "terminate_grace": 5.0,
        "poll_interval": 0.2,
        "start_gate_timeout": 60.0,
    }
    values.update(overrides)
    return ps.SupervisionConfig(**values)


def test_supervision_config_is_frozen():
    config = _config()
    with pytest.raises(dataclasses.FrozenInstanceError):
        config.terminate_grace = 1.0


def test_terminate_kill_job_branch_confirms_with_injected_grace():
    calls = {}

    class FakeJob:
        def terminate_and_confirm(self, *, timeout):
            calls["timeout"] = timeout
            return True

    class FakeProcess:
        def wait(self, timeout):
            calls["wait"] = timeout

        def poll(self):
            return 0

    assert ps._terminate_supervised_process(
        FakeProcess(), kill_job=FakeJob(), terminate_grace=1.25
    ) is True
    assert calls == {"timeout": 1.25, "wait": 1.25}


def test_terminate_kill_job_branch_fails_when_worker_survives():
    class FakeJob:
        def terminate_and_confirm(self, *, timeout):
            return True

    class FakeProcess:
        def wait(self, timeout):
            raise subprocess.TimeoutExpired("worker", timeout)

        def poll(self):
            return None

        def kill(self):
            pass

    assert ps._terminate_supervised_process(
        FakeProcess(), kill_job=FakeJob(), terminate_grace=0.01
    ) is False


def test_terminate_kill_job_branch_fails_closed_when_confirm_raises():
    closed = {}

    class FakeJob:
        def terminate_and_confirm(self, *, timeout):
            raise OSError("job query failed")

        def close(self):
            closed["closed"] = True
            return True

    class FakeProcess:
        def wait(self, timeout):
            return 0

        def poll(self):
            return 0

    assert ps._terminate_supervised_process(
        FakeProcess(), kill_job=FakeJob(), terminate_grace=0.01
    ) is False
    assert closed == {"closed": True}


class FakeGate:
    kind = "fake-gate"
    child_value = "7"

    def __init__(self):
        self.released = False
        self.closed = False

    def popen_options(self):
        return {}

    def release(self):
        self.released = True

    def close(self):
        self.closed = True


class Recorder:
    def __init__(self):
        self.finalized = []
        self.started = []
        self.warnings = []

    def telemetry_start(self, operation, *, run_id, run_events, run_report):
        self.started.append((operation, run_id))

    def telemetry_finalize(
        self, operation, *, run_id, run_events, run_report, status, exc
    ):
        self.finalized.append((operation, status, type(exc).__name__))

    def warn(self, message):
        self.warnings.append(message)


def _core_kwargs(recorder, gate, clock, **overrides):
    values = {
        "operation": "index",
        "timeout": 5.0,
        "config": _config(),
        "telemetry_start_fn": recorder.telemetry_start,
        "telemetry_finalize_fn": recorder.telemetry_finalize,
        "warn_fn": recorder.warn,
        "kill_job_factory": lambda: None,
        "start_gate_factory": lambda: gate,
        "monotonic": clock.monotonic,
    }
    values.update(overrides)
    return values


def test_deadline_expiry_terminates_finalizes_and_returns_124(monkeypatch):
    clock = FakeClock()
    gate = FakeGate()
    recorder = Recorder()
    terminated = []

    class FakeProcess:
        pid = 4242

        def wait(self, timeout):
            clock.now += timeout
            raise subprocess.TimeoutExpired("worker", timeout)

        def poll(self):
            return None

    monkeypatch.setattr(
        ps.subprocess, "Popen", lambda *_args, **_kwargs: FakeProcess()
    )

    code = ps._run_cli_with_deadline(
        Path("worker.py"),
        ["index"],
        terminate_fn=lambda process, *, kill_job=None: (
            terminated.append(kill_job) or True
        ),
        **_core_kwargs(recorder, gate, clock),
    )

    assert code == 124
    assert gate.released and gate.closed
    assert terminated
    assert recorder.started == [("index", None)]
    assert recorder.finalized == [("index", "failed", "TimeoutError")]
    assert "exceeded its 5s deadline" in recorder.warnings[0]


def test_unconfirmed_cleanup_raises_and_skips_finalize(monkeypatch):
    clock = FakeClock()
    gate = FakeGate()
    recorder = Recorder()

    class FakeProcess:
        pid = 4243

        def wait(self, timeout):
            clock.now += timeout
            raise subprocess.TimeoutExpired("worker", timeout)

        def poll(self):
            return None

    monkeypatch.setattr(
        ps.subprocess, "Popen", lambda *_args, **_kwargs: FakeProcess()
    )

    with pytest.raises(ps._SupervisorCleanupError):
        ps._run_cli_with_deadline(
            Path("worker.py"),
            ["index"],
            terminate_fn=lambda process, *, kill_job=None: False,
            **_core_kwargs(recorder, gate, clock),
        )

    assert recorder.finalized == []
    assert "could not be confirmed" in recorder.warnings[0]


def test_external_cancellation_returns_130_and_finalizes_cancelled(
    monkeypatch,
):
    clock = FakeClock()
    gate = FakeGate()
    recorder = Recorder()

    class FakeProcess:
        pid = 4244

        def poll(self):
            return None

    monkeypatch.setattr(
        ps.subprocess, "Popen", lambda *_args, **_kwargs: FakeProcess()
    )

    code = ps._run_cli_with_deadline(
        Path("worker.py"),
        ["index"],
        cancel_requested=lambda: True,
        terminate_fn=lambda process, *, kill_job=None: True,
        **_core_kwargs(recorder, gate, clock),
    )

    assert code == 130
    assert recorder.finalized == [
        ("index", "cancelled", "KeyboardInterrupt")
    ]


def test_child_registration_failure_terminates_and_finalizes(monkeypatch):
    clock = FakeClock()
    gate = FakeGate()
    recorder = Recorder()
    terminated = []

    class FakeProcess:
        pid = 4245

        def poll(self):
            return None

    monkeypatch.setattr(
        ps.subprocess, "Popen", lambda *_args, **_kwargs: FakeProcess()
    )

    def failing_registration(_process):
        raise RuntimeError("registration failed")

    with pytest.raises(RuntimeError, match="registration failed"):
        ps._run_cli_with_deadline(
            Path("worker.py"),
            ["index"],
            on_child_started=failing_registration,
            terminate_fn=lambda process, *, kill_job=None: (
                terminated.append(process.pid) or True
            ),
            **_core_kwargs(recorder, gate, clock),
        )

    assert terminated == [4245]
    assert not gate.released
    assert gate.closed
    assert recorder.finalized == [("index", "failed", "RuntimeError")]


def test_start_gate_factory_failure_finalizes_before_launch(monkeypatch):
    clock = FakeClock()
    recorder = Recorder()
    launched = []
    monkeypatch.setattr(
        ps.subprocess,
        "Popen",
        lambda *_args, **_kwargs: launched.append(True),
    )

    def failing_gate():
        raise OSError("no gate")

    with pytest.raises(OSError, match="no gate"):
        ps._run_cli_with_deadline(
            Path("worker.py"),
            ["index"],
            **_core_kwargs(
                recorder,
                FakeGate(),
                clock,
                start_gate_factory=failing_gate,
            ),
        )

    assert launched == []
    assert recorder.finalized == [("index", "failed", "OSError")]


def test_normalize_timeout_fn_rejects_before_launch(monkeypatch):
    clock = FakeClock()
    recorder = Recorder()
    launched = []
    monkeypatch.setattr(
        ps.subprocess,
        "Popen",
        lambda *_args, **_kwargs: launched.append(True),
    )

    def rejecting_normalize(value):
        raise ValueError(f"bad timeout {value}")

    with pytest.raises(ValueError, match="bad timeout 5.0"):
        ps._run_cli_with_deadline(
            Path("worker.py"),
            ["index"],
            **_core_kwargs(
                recorder,
                FakeGate(),
                clock,
                normalize_timeout_fn=rejecting_normalize,
            ),
        )

    assert launched == []


def _entrypoint_kwargs(calls, **overrides):
    values = {
        "config": _config(),
        "script_path": Path("rag.py"),
        "command_resolver": lambda args: args[0] if args else None,
        "operation_timeouts": {"index": 300.0, "query": 60.0},
        "telemetry_options_fn": lambda args, command: {
            "run_id": None,
            "events_path": None,
            "report_path": None,
        },
        "timeout_fn": lambda args, command: 42.0,
        "run_id_factory": lambda: "factory-run",
        "menu_fn": lambda: calls.append(("menu",)),
        "main_fn": lambda args: calls.append(("main", args)),
        "supervisor_fn": lambda script, argv, **kwargs: (
            calls.append(("supervise", script, argv, kwargs)) or 9
        ),
    }
    values.update(overrides)
    return values


def test_entrypoint_supervises_vector_commands(monkeypatch):
    calls = []
    monkeypatch.delenv("TEST_SUPERVISED_CHILD", raising=False)
    monkeypatch.delenv("TEST_RUN_ID", raising=False)

    assert ps._run_supervised_entrypoint(
        ["query", "terms"], **_entrypoint_kwargs(calls)
    ) == 9

    kind, script, argv, kwargs = calls[0]
    assert (kind, argv) == ("supervise", ["query", "terms"])
    assert script == Path("rag.py")
    assert kwargs == {
        "operation": "query",
        "timeout": 42.0,
        "run_id": "factory-run",
        "run_events": None,
        "run_report": None,
    }


def test_entrypoint_runs_menu_and_direct_commands(monkeypatch):
    calls = []
    monkeypatch.delenv("TEST_SUPERVISED_CHILD", raising=False)

    assert ps._run_supervised_entrypoint(
        [], **_entrypoint_kwargs(calls)
    ) == 0
    assert ps._run_supervised_entrypoint(
        ["export"], **_entrypoint_kwargs(calls)
    ) == 0
    assert calls == [("menu",), ("main", ["export"])]


def test_entrypoint_child_does_not_recursively_supervise(monkeypatch):
    calls = []
    monkeypatch.setenv("TEST_SUPERVISED_CHILD", "1")

    assert ps._run_supervised_entrypoint(
        ["index"], **_entrypoint_kwargs(calls)
    ) == 0
    assert calls == [("main", ["index"])]


def test_entrypoint_restores_environment_overrides(monkeypatch):
    calls = []
    monkeypatch.setenv("TEST_SUPERVISED_CHILD", "1")
    monkeypatch.setenv("KEEP_ME", "original")
    monkeypatch.delenv("ADD_ME", raising=False)

    def checking_main(args):
        calls.append(
            (
                os.environ.get("KEEP_ME"),
                os.environ.get("ADD_ME"),
                os.environ.get("DROP_ME"),
            )
        )

    monkeypatch.setenv("DROP_ME", "present")
    assert ps._run_supervised_entrypoint(
        ["index"],
        environment_overrides={
            "KEEP_ME": "overridden",
            "ADD_ME": "added",
            "DROP_ME": None,
        },
        **_entrypoint_kwargs(calls, main_fn=checking_main),
    ) == 0

    assert calls == [("overridden", "added", None)]
    assert os.environ["KEEP_ME"] == "original"
    assert "ADD_ME" not in os.environ
    assert os.environ["DROP_ME"] == "present"
