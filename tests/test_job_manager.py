from __future__ import annotations

import json
import os
import stat
import subprocess
import threading
import time
from dataclasses import replace
from pathlib import Path

import pytest

import attempt_reporting
import job_coordination as job_manager
import job_runtime
import runtime_supervision
import storage_policy


def _write_worker(path: Path, source: str) -> Path:
    path.write_text(source, encoding="utf-8")
    return path


def _wait_for(predicate, *, timeout: float = 10.0) -> bool:
    deadline = time.monotonic() + timeout
    while time.monotonic() < deadline:
        if predicate():
            return True
        time.sleep(0.05)
    return False


def _advance_to_running(store: job_runtime.JobStore, job_id: str):
    execution = store.load_execution(job_id)
    starting = store.transition_job(
        job_id, "starting", attempt_token=execution.attempt_token,
        expected_revision=execution.revision)
    running = store.transition_job(
        job_id, "running", attempt_token=execution.attempt_token,
        expected_revision=starting.revision)
    return execution, running


def _write_recovery_runtime(
        store, submitted, execution, running, *, worker_pid, worker_birth):
    paths = job_manager._attempt_paths(
        store, submitted.job_id, execution.attempt_number, create=True)
    now = time.time()
    runtime = job_manager.RuntimeMetadata(
        job_id=submitted.job_id,
        attempt_number=execution.attempt_number,
        attempt_token_sha256=job_manager._token_digest(
            execution.attempt_token),
        run_id=f"{submitted.job_id}.a1",
        phase="running",
        job_status=running.status,
        manager_pid=os.getpid(),
        manager_birth=job_manager.process_birth_identity(os.getpid()),
        worker_pid=worker_pid,
        worker_birth=worker_birth,
        heartbeat_at=now,
        cleanup_confirmed=None,
        exit_code=None,
        updated_at=now,
    )
    job_manager._write_runtime(paths.runtime, runtime)
    return paths, runtime


def _assert_private(path: Path, *, directory: bool) -> None:
    if os.name == "nt":
        assert storage_policy.windows_path_is_private(
            path, directory=directory)
    else:
        expected = (storage_policy.PRIVATE_DIRECTORY_MODE if directory
                    else storage_policy.PRIVATE_FILE_MODE)
        assert stat.S_IMODE(path.stat().st_mode) == expected


def _attempt_outcome(store, job_id):
    execution = store.load_execution(job_id)
    paths = job_manager._attempt_paths(
        store, job_id, execution.attempt_number, create=False)
    report = job_manager._load_attempt_report(
        paths, execution=execution)
    assert report is not None
    return paths, report


def _runtime_binding(
        script_path: Path, supervisor, *, timeouts=None,
        cleanup_error_type=runtime_supervision.SupervisorCleanupError):
    return runtime_supervision.PipelineRuntimeBinding(
        script_path=script_path,
        supervisor=supervisor,
        cleanup_error_type=cleanup_error_type,
        operation_timeouts=(
            runtime_supervision.DEFAULT_OPERATION_TIMEOUTS
            if timeouts is None else timeouts),
    )


def test_operation_timeout_uses_persisted_override_policy_and_fallback(
        tmp_path):
    store = job_runtime.JobStore(tmp_path / "jobs")
    policy_job = store.submit_job("full", [])
    fallback_job = store.submit_job("export", [])
    explicit_job = store.submit_job("index", [], timeout_seconds=17)
    binding = _runtime_binding(
        tmp_path / "pipeline.py",
        lambda *_args, **_kwargs: 0,
        timeouts={"full": 91},
    )

    assert job_manager._operation_timeout(
        store.load_execution(policy_job.job_id), binding) == 91
    assert job_manager._operation_timeout(
        store.load_execution(fallback_job.job_id), binding) == (
            job_manager._GENERIC_BACKGROUND_TIMEOUT)
    assert job_manager._operation_timeout(
        store.load_execution(explicit_job.job_id), binding) == 17


def test_run_job_resolves_atomic_runtime_binding_at_call_time(
        monkeypatch, tmp_path):
    captured = {}
    script = tmp_path / "bound-rag.py"

    def supervisor(script_path, argv, **kwargs):
        captured.update(
            script_path=script_path,
            argv=argv,
            kwargs=kwargs,
        )
        return 1

    binding = _runtime_binding(
        script, supervisor, timeouts={"index": 37})
    monkeypatch.setattr(
        job_manager, "_default_runtime_binding", lambda: binding)
    store = job_runtime.JobStore(tmp_path / "jobs")
    submitted = store.submit_job("index", ["--full-reindex"])
    execution = store.load_execution(submitted.job_id)

    result = job_manager.run_job(store, submitted.job_id)

    assert result.status == "failed"
    assert captured["script_path"] == script
    assert captured["argv"][0:2] == ["index", "--full-reindex"]
    options = captured["kwargs"]
    assert options["operation"] == "index"
    assert options["timeout"] == 37
    assert options["working_directory"] == execution.working_directory
    assert options["environment_overrides"] == {
        job_manager.JOB_ROOT_ENV: str(store.root),
        job_manager.JOB_ID_ENV: submitted.job_id,
        job_manager.JOB_ATTEMPT_TOKEN_ENV: execution.attempt_token,
        job_manager.OUTPUT_ROOT_ENV: str(execution.output_root),
        job_manager._READY_NONCE_ENV: None,
    }
    assert options["run_id"] == f"{submitted.job_id}.a1"
    assert options["run_events"].name == "run.events.jsonl"
    assert options["run_report"].name == "run.report.json"
    assert callable(options["cancel_requested"])
    assert callable(options["on_child_started"])
    assert callable(options["heartbeat"])
    assert options["stdout_target"] is options["stderr_target"]


def test_detached_launch_resolves_bound_pipeline_script_at_call_time(
        monkeypatch, tmp_path):
    captured = {}
    script = tmp_path / "bound-rag.py"
    binding = _runtime_binding(
        script, lambda *_args, **_kwargs: 0)
    monkeypatch.setattr(
        job_manager, "_default_runtime_binding", lambda: binding)

    class PendingManager:
        pid = os.getpid()

        @staticmethod
        def poll():
            return None

    def popen(command, **options):
        captured.update(command=command, options=options)
        return PendingManager()

    monkeypatch.setattr(job_manager.subprocess, "Popen", popen)
    monkeypatch.setattr(
        job_manager, "_ready_matches", lambda *_args, **_kwargs: True)
    store = job_runtime.JobStore(tmp_path / "jobs")
    submitted = store.submit_job("export", [])

    launched = job_manager.launch_detached(
        store, submitted.job_id, ready_timeout=1)

    assert launched.ready
    script_index = captured["command"].index("--script") + 1
    assert captured["command"][script_index] == str(script.resolve())
    assert captured["options"]["cwd"] == str(
        store.load_execution(submitted.job_id).working_directory)


def test_run_job_success_persists_private_attempt_and_exact_worker_env(tmp_path):
    root = tmp_path / "jobs"
    environment_marker = tmp_path / "worker-environment.json"
    worker = _write_worker(
        tmp_path / "success_worker.py",
        """
import json
import os
import sys
from pathlib import Path

Path(sys.argv[2]).write_text(json.dumps({
    "root": os.environ.get("RAG_PIPELINE_JOB_ROOT"),
    "job_id": os.environ.get("RAG_PIPELINE_JOB_ID"),
    "attempt_token": os.environ.get("RAG_PIPELINE_JOB_ATTEMPT_TOKEN"),
    "output_root": os.environ.get("RAG_PIPELINE_OUTPUT_ROOT"),
    "ready_nonce": os.environ.get("RAG_PIPELINE_MANAGER_READY_NONCE"),
}), encoding="utf-8")
print("private worker output")
""",
    )
    store = job_runtime.JobStore(root)
    submitted = store.submit_job(
        "full", [str(environment_marker)], timeout_seconds=5)
    execution = store.load_execution(submitted.job_id)

    result = job_manager.run_job(
        store, submitted.job_id, script_path=worker)

    assert result.status == "succeeded"
    assert result.exit_code == 0
    assert result.cleanup_confirmed
    assert store.get_job(submitted.job_id).status == "succeeded"
    observed_environment = json.loads(
        environment_marker.read_text(encoding="utf-8"))
    assert observed_environment == {
        "root": str(store.root),
        "job_id": submitted.job_id,
        "attempt_token": execution.attempt_token,
        "output_root": str(execution.output_root),
        "ready_nonce": None,
    }

    paths = job_manager._attempt_paths(
        store, submitted.job_id, 1, create=False)
    for directory in (paths.directory.parent, paths.directory):
        _assert_private(directory, directory=True)
    for artifact in (
            paths.runtime, paths.log, paths.events, paths.report,
            paths.attempt_report):
        assert artifact.is_file()
        _assert_private(artifact, directory=False)
    assert "private worker output" in paths.log.read_text(encoding="utf-8")
    runtime = job_manager._load_runtime(paths.runtime, execution=execution)
    assert runtime.phase == "terminal"
    assert runtime.job_status == "succeeded"
    assert runtime.manager_pid == os.getpid()
    assert runtime.worker_pid is not None
    outcome = job_manager._load_attempt_report(
        paths, execution=execution)
    assert outcome is not None
    assert outcome.status == "succeeded"
    assert outcome.trigger == "normal"
    assert outcome.finalized_by == "manager"
    assert outcome.worker_started
    assert outcome.cleanup_confirmed
    assert outcome.terminal_reason == "completed"
    assert outcome.recovery_action == "none"
    private_outcome = paths.attempt_report.read_text(encoding="utf-8")
    assert str(environment_marker) not in private_outcome
    assert str(root) not in private_outcome
    assert execution.attempt_token not in private_outcome
    assert str(runtime.worker_pid) not in private_outcome
    public = json.dumps(result.as_dict())
    assert str(root) not in public
    assert execution.attempt_token not in public
    assert "worker_pid" not in public


def test_direct_run_job_executes_from_persisted_working_directory(
        monkeypatch, tmp_path):
    submitted_directory = tmp_path / "submitted"
    submitted_directory.mkdir()
    other_directory = tmp_path / "caller"
    other_directory.mkdir()
    output_root = submitted_directory / "output"
    worker = _write_worker(
        tmp_path / "cwd_worker.py",
        "import sys\n"
        "from pathlib import Path\n"
        "Path(sys.argv[2]).write_text(str(Path.cwd()), encoding='utf-8')\n",
    )
    store = job_runtime.JobStore(tmp_path / "jobs")
    submitted = store.submit_job(
        "export", ["worker-cwd.txt"],
        working_directory=submitted_directory, output_root=output_root)
    monkeypatch.chdir(other_directory)

    result = job_manager.run_job(
        store, submitted.job_id, script_path=worker)

    marker = submitted_directory / "worker-cwd.txt"
    assert result.status == "succeeded"
    assert marker.read_text(encoding="utf-8") == str(
        submitted_directory.resolve())
    assert not (other_directory / "worker-cwd.txt").exists()


def test_direct_run_job_terminalizes_invalid_directory_binding(tmp_path):
    working = tmp_path / "submitted"
    working.mkdir()
    output_root = working / "output"
    store = job_runtime.JobStore(tmp_path / "jobs")
    submitted = store.submit_job(
        "export", [], working_directory=working, output_root=output_root)
    held_directory = (
        os.open(output_root, os.O_RDONLY | getattr(os, "O_DIRECTORY", 0))
        if os.name != "nt" else None)
    try:
        output_root.rmdir()
        storage_policy.ensure_private_directory(output_root)

        with pytest.raises(
                job_runtime.JobStateError,
                match="output root identity changed"):
            job_manager.run_job(
                store, submitted.job_id, script_path=tmp_path / "unused.py")
    finally:
        if held_directory is not None:
            os.close(held_directory)

    assert store.get_job(submitted.job_id).status == "failed"
    paths, outcome = _attempt_outcome(store, submitted.job_id)
    assert paths.directory.is_dir()
    assert not paths.runtime.exists()
    assert outcome.status == "failed"
    assert outcome.trigger == "manager_error"
    assert outcome.terminal_reason == "execution_validation_failed"
    assert outcome.cleanup_confirmed
    assert outcome.manager_error


def test_direct_run_job_terminalizes_private_probe_failure(
        monkeypatch, tmp_path):
    working = tmp_path / "submitted"
    working.mkdir()
    store = job_runtime.JobStore(tmp_path / "jobs")
    output_root = working / "output"
    submitted = store.submit_job(
        "export", [], working_directory=working,
        output_root=output_root)
    real_private_probe = job_runtime._private_mode_ok

    def fail_private_probe(path, *, directory):
        if Path(path) == output_root.resolve():
            assert directory
            raise OSError("injected private-mode probe failure")
        return real_private_probe(path, directory=directory)

    monkeypatch.setattr(
        job_runtime, "_private_mode_ok", fail_private_probe)

    with pytest.raises(job_runtime.JobStateError, match="is unavailable"):
        job_manager.run_job(
            store, submitted.job_id, script_path=tmp_path / "unused.py")

    assert store.get_job(submitted.job_id).status == "failed"
    paths, outcome = _attempt_outcome(store, submitted.job_id)
    assert paths.directory.is_dir()
    assert not paths.runtime.exists()
    assert outcome.status == "failed"
    assert outcome.terminal_reason == "execution_validation_failed"
    assert outcome.manager_error


def test_run_job_uses_terminal_partial_telemetry(tmp_path):
    worker = _write_worker(
        tmp_path / "partial_worker.py",
        """
import json
import os
import sys
from pathlib import Path

def value(flag):
    index = sys.argv.index(flag)
    return sys.argv[index + 1]

report = Path(value("--run-report"))
report.write_text(json.dumps({
    "schema_version": 1,
    "run_id": value("--run-id"),
    "operation": sys.argv[1],
    "status": "partial",
}), encoding="utf-8")
if os.name != "nt":
    os.chmod(report, 0o600)
""",
    )
    store = job_runtime.JobStore(tmp_path / "jobs")
    submitted = store.submit_job("batch", [], timeout_seconds=5)

    result = job_manager.run_job(
        store, submitted.job_id, script_path=worker)

    assert result.status == "partial"
    assert result.exit_code == 0
    assert result.reason == "completed"
    assert store.get_job(submitted.job_id).status == "partial"


def test_attempt_bound_cancel_kills_worker_descendant_and_reports_cancelled(
        tmp_path):
    heartbeat = tmp_path / "descendant-heartbeat.txt"
    started = tmp_path / "worker-started.txt"
    worker = _write_worker(
        tmp_path / "descendant_worker.py",
        """
import subprocess
import sys
import time
from pathlib import Path

child_source = '''
import sys
import time
from pathlib import Path
path = Path(sys.argv[1])
for number in range(10000):
    path.write_text(str(number), encoding="utf-8")
    time.sleep(0.02)
'''
child = subprocess.Popen([sys.executable, "-c", child_source, sys.argv[2]])
Path(sys.argv[3]).write_text(str(child.pid), encoding="utf-8")
time.sleep(60)
""",
    )
    store = job_runtime.JobStore(tmp_path / "jobs")
    submitted = store.submit_job(
        "full", [str(heartbeat), str(started)], timeout_seconds=20)
    result_box = {}

    def run_manager():
        result_box["result"] = job_manager.run_job(
            store, submitted.job_id, script_path=worker)

    manager = threading.Thread(target=run_manager)
    manager.start()
    assert _wait_for(lambda: started.is_file() and heartbeat.is_file())
    store.request_cancel(submitted.job_id)
    manager.join(timeout=20)

    assert not manager.is_alive()
    result = result_box["result"]
    assert result.status == "cancelled"
    assert result.exit_code == 130
    assert result.cleanup_confirmed
    assert store.get_job(submitted.job_id).status == "cancelled"
    _paths, outcome = _attempt_outcome(store, submitted.job_id)
    assert outcome.status == "cancelled"
    assert outcome.trigger == "cancel"
    assert outcome.cancel_requested
    assert outcome.cancel_observed
    assert outcome.durations_ms()["cancel_observation"] is not None
    assert outcome.durations_ms()["cancel_completion"] is not None
    time.sleep(0.2)
    stopped = heartbeat.read_text(encoding="utf-8")
    time.sleep(0.25)
    assert heartbeat.read_text(encoding="utf-8") == stopped


def test_prelaunch_cancellation_reports_no_worker_and_confirmed_cleanup(
        tmp_path):
    store = job_runtime.JobStore(tmp_path / "jobs")
    submitted = store.submit_job("export", [])
    store.request_cancel(submitted.job_id)

    def forbidden_supervisor(*_args, **_kwargs):
        raise AssertionError("prelaunch cancellation started a worker")

    result = job_manager.run_job(
        store, submitted.job_id, supervisor=forbidden_supervisor)
    _paths, outcome = _attempt_outcome(store, submitted.job_id)

    assert result.status == "cancelled"
    assert outcome.status == "cancelled"
    assert outcome.finalized_by == "manager"
    assert outcome.trigger == "cancel"
    assert not outcome.worker_started
    assert outcome.cleanup_confirmed
    assert outcome.exit_code == 130


def test_timeout_is_failed_only_after_worker_cleanup(tmp_path):
    started = tmp_path / "started.txt"
    worker = _write_worker(
        tmp_path / "timeout_worker.py",
        """
import sys
import time
from pathlib import Path
Path(sys.argv[2]).write_text("started", encoding="utf-8")
time.sleep(60)
""",
    )
    store = job_runtime.JobStore(tmp_path / "jobs")
    submitted = store.submit_job(
        "full", [str(started)], timeout_seconds=0.3)

    result = job_manager.run_job(
        store, submitted.job_id, script_path=worker)

    assert started.is_file()
    assert result.status == "failed"
    assert result.exit_code == 124
    assert result.cleanup_confirmed
    assert result.reason == "timeout"
    _paths, outcome = _attempt_outcome(store, submitted.job_id)
    assert outcome.status == "failed"
    assert outcome.trigger == "timeout"
    assert outcome.terminal_reason == "timeout"
    assert outcome.exit_code == 124


def test_worker_log_is_bounded_without_pipe_deadlock(tmp_path):
    worker = _write_worker(
        tmp_path / "large_output_worker.py",
        "import sys\n"
        f"sys.stdout.write('x' * {job_manager._MAX_WORKER_LOG_BYTES + 4096})\n"
        "sys.stdout.flush()\n",
    )
    store = job_runtime.JobStore(tmp_path / "jobs")
    submitted = store.submit_job("export", [], timeout_seconds=10)

    result = job_manager.run_job(
        store, submitted.job_id, script_path=worker)

    paths = job_manager._attempt_paths(
        store, submitted.job_id, 1, create=False)
    assert result.status == "succeeded"
    assert paths.log.stat().st_size <= job_manager._MAX_WORKER_LOG_BYTES
    with paths.log.open("rb") as handle:
        assert handle.read(64).startswith(b"[earlier worker output truncated")


def test_supervisor_launch_failure_is_a_redacted_failed_attempt(tmp_path):
    store = job_runtime.JobStore(tmp_path / "jobs")
    submitted = store.submit_job("index", ["--chunks", "private.json"])

    def fail_supervisor(*args, **kwargs):
        raise OSError("private injected launch detail")

    result = job_manager.run_job(
        store, submitted.job_id,
        script_path=tmp_path / "unused.py",
        supervisor=fail_supervisor)

    assert result.status == "failed"
    assert result.exit_code is None
    assert result.cleanup_confirmed
    assert "private" not in json.dumps(result.as_dict())
    assert store.get_job(submitted.job_id).status == "failed"
    _paths, outcome = _attempt_outcome(store, submitted.job_id)
    assert outcome.status == "failed"
    assert outcome.trigger == "manager_error"
    assert outcome.manager_error
    assert outcome.terminal_reason == "worker_failed"


def test_unconfirmed_cancel_never_classifies_as_cancelled():
    assert job_manager._classify_terminal(
        exit_code=130,
        cancel_observed=True,
        cleanup_confirmed=False,
        telemetry_status="cancelled",
        manager_error=False,
    ) == "orphaned"
    assert job_manager._classify_terminal(
        exit_code=130,
        cancel_observed=True,
        cleanup_confirmed=True,
        telemetry_status=None,
        manager_error=False,
    ) == "interrupted"


def test_supervisor_cleanup_failure_is_persisted_as_nonresumable_orphan(
        monkeypatch, tmp_path):
    store = job_runtime.JobStore(tmp_path / "jobs")
    submitted = store.submit_job("index", [])

    class FakeProcess:
        pid = 987654321

    def fail_cleanup(*_args, **kwargs):
        kwargs["on_child_started"](FakeProcess())
        raise runtime_supervision.SupervisorCleanupError(
            "supervised worker tree cleanup could not be confirmed")

    monkeypatch.setattr(
        job_manager, "_confirm_worker_tree_gone",
        lambda _pid, _birth: True)

    result = job_manager.run_job(
        store, submitted.job_id, script_path=tmp_path / "unused.py",
        supervisor=fail_cleanup)

    assert result.status == "orphaned"
    assert not result.cleanup_confirmed
    assert store.get_job(submitted.job_id).status == "orphaned"
    with pytest.raises(job_runtime.JobStateError, match="not resumable"):
        store.prepare_resume(submitted.job_id)


@pytest.mark.parametrize("command", ["full", "batch"])
def test_resumed_pipeline_attempt_injects_resume_before_terminator(
        tmp_path, command):
    output_root = storage_policy.ensure_private_directory(tmp_path / "output")
    cwd_stat = tmp_path.stat()
    output_stat = output_root.stat()
    execution = job_runtime.JobExecution(
        job_id="a" * 32,
        command=command,
        argv=("book.pdf", "--", "--resume"),
        working_directory=tmp_path,
        working_directory_device=int(cwd_stat.st_dev),
        working_directory_inode=int(cwd_stat.st_ino),
        output_root=output_root,
        output_root_device=int(output_stat.st_dev),
        output_root_inode=int(output_stat.st_ino),
        timeout_seconds=5,
        status="queued",
        attempt_number=2,
        attempt_token="b" * 32,
        revision=4,
    )
    directory = tmp_path / "attempt"
    paths = job_manager._AttemptPaths(
        directory=directory,
        runtime=directory / "runtime.json",
        ready=directory / "ready.json",
        log=directory / "worker.log",
        events=directory / "events.jsonl",
        report=directory / "report.json",
        attempt_report=directory / "attempt.report.json",
    )

    argv = job_manager._inject_telemetry_arguments(
        execution, paths, "run-id")
    terminator = argv.index("--")

    assert argv[0] == command
    assert argv[:terminator].count("--resume") == 1
    assert argv[terminator + 1:] == ["--resume"]
    assert argv.index("--run-report") < terminator


def test_reconcile_refuses_worker_pid_birth_mismatch(tmp_path, monkeypatch):
    store = job_runtime.JobStore(tmp_path / "jobs")
    submitted = store.submit_job("full", ["--pdf", "book.pdf"])
    execution, running = _advance_to_running(store, submitted.job_id)
    paths = job_manager._attempt_paths(
        store, submitted.job_id, execution.attempt_number, create=True)
    now = time.time()
    runtime = job_manager.RuntimeMetadata(
        job_id=submitted.job_id,
        attempt_number=execution.attempt_number,
        attempt_token_sha256=job_manager._token_digest(
            execution.attempt_token),
        run_id=f"{submitted.job_id}.a1",
        phase="running",
        job_status=running.status,
        manager_pid=os.getpid(),
        manager_birth=job_manager.process_birth_identity(os.getpid()),
        worker_pid=os.getpid(),
        worker_birth="deliberately-wrong-birth-identity",
        heartbeat_at=now,
        cleanup_confirmed=None,
        exit_code=None,
        updated_at=now,
    )
    job_manager._write_runtime(paths.runtime, runtime)

    called = False

    def forbidden_termination(pid, birth):
        nonlocal called
        called = True
        raise AssertionError("PID mismatch must not be signalled")

    monkeypatch.setattr(
        job_manager, "_terminate_recovered_worker", forbidden_termination)
    reconciled = job_manager.reconcile_job(store, submitted.job_id)

    assert not called
    assert reconciled.status == "orphaned"
    assert reconciled.attempt_number == 1
    _paths, outcome = _attempt_outcome(store, submitted.job_id)
    assert outcome.status == "orphaned"
    assert outcome.finalized_by == "recovery"
    assert outcome.recovery_action == "refused_identity"
    assert outcome.reconciled
    assert not outcome.cleanup_confirmed
    with pytest.raises(job_runtime.JobStateError, match="not resumable"):
        store.prepare_resume(submitted.job_id)


def test_corrupt_attempt_report_cannot_block_exact_worker_recovery(
        tmp_path, monkeypatch):
    store = job_runtime.JobStore(tmp_path / "jobs")
    submitted = store.submit_job("full", ["--pdf", "book.pdf"])
    execution, running = _advance_to_running(store, submitted.job_id)
    paths = job_manager._attempt_paths(
        store, submitted.job_id, execution.attempt_number, create=True)
    now = time.time()
    runtime = job_manager.RuntimeMetadata(
        job_id=submitted.job_id,
        attempt_number=execution.attempt_number,
        attempt_token_sha256=job_manager._token_digest(
            execution.attempt_token),
        run_id=f"{submitted.job_id}.a1",
        phase="running",
        job_status=running.status,
        manager_pid=os.getpid(),
        manager_birth=job_manager.process_birth_identity(os.getpid()),
        worker_pid=424242,
        worker_birth="test-worker-birth",
        heartbeat_at=now,
        cleanup_confirmed=None,
        exit_code=None,
        updated_at=now,
    )
    job_manager._write_runtime(paths.runtime, runtime)
    storage_policy.atomic_write_private_json(
        paths.attempt_report,
        {"schema_version": 1, "private_path": "C:/secret.pdf"},
    )
    terminated = []
    monkeypatch.setattr(
        job_manager, "probe_process_identity",
        lambda pid, birth: "match")
    monkeypatch.setattr(
        job_manager, "_terminate_recovered_worker",
        lambda pid, birth: terminated.append((pid, birth)) or True)
    real_write = job_manager._write_attempt_report
    pending_failures = []

    def fail_first_pending_publication(path, report):
        if report.recovery_action == "pending" and not pending_failures:
            pending_failures.append(True)
            raise OSError("injected pending publication failure")
        return real_write(path, report)

    monkeypatch.setattr(
        job_manager, "_write_attempt_report", fail_first_pending_publication)

    reconciled = job_manager.reconcile_job(store, submitted.job_id)

    assert pending_failures == [True]
    assert terminated == [(424242, "test-worker-birth")]
    assert reconciled.status == "interrupted"
    _paths, outcome = _attempt_outcome(store, submitted.job_id)
    assert outcome.status == "interrupted"
    assert outcome.recovery_action == "terminated_exact_worker"
    assert outcome.report_repaired
    assert outcome.timing_reconstructed
    assert not outcome.runtime_missing


def test_corrupt_cancel_marker_cannot_block_exact_worker_recovery(
        tmp_path, monkeypatch):
    store = job_runtime.JobStore(tmp_path / "jobs")
    submitted = store.submit_job("full", ["--pdf", "book.pdf"])
    execution, running = _advance_to_running(store, submitted.job_id)
    _paths, _runtime = _write_recovery_runtime(
        store, submitted, execution, running,
        worker_pid=424249, worker_birth="corrupt-cancel-worker")
    marker_path = (
        store.root / submitted.job_id
        / job_runtime._CANCEL_NAME_TEMPLATE.format(
            attempt_number=execution.attempt_number)
    )
    storage_policy.atomic_write_private(
        marker_path,
        lambda handle: handle.write(b"{not-json"),
        text=False,
    )
    terminated = []
    monkeypatch.setattr(
        job_manager, "probe_process_identity",
        lambda _pid, _birth: "match")
    monkeypatch.setattr(
        job_manager, "_terminate_recovered_worker",
        lambda pid, birth: terminated.append((pid, birth)) or True)

    reconciled = job_manager.reconcile_job(store, submitted.job_id)
    _paths, outcome = _attempt_outcome(store, submitted.job_id)

    assert terminated == [(424249, "corrupt-cancel-worker")]
    assert reconciled.status == "interrupted"
    assert outcome.status == "interrupted"
    assert not outcome.cancel_requested
    assert outcome.recovery_action == "terminated_exact_worker"
    assert outcome.report_repaired
    assert outcome.timing_reconstructed
    assert job_manager.reconcile_job(store, submitted.job_id) == reconciled


def test_attempt_report_reader_enforces_identity_and_size_bounds(tmp_path):
    store = job_runtime.JobStore(tmp_path / "jobs")
    submitted = store.submit_job("export", [])
    execution = store.load_execution(submitted.job_id)
    paths = job_manager._attempt_paths(
        store, submitted.job_id, execution.attempt_number, create=True)
    wrong_job_id = "b" * 32
    forged = attempt_reporting.new_report(
        job_id=wrong_job_id,
        attempt_number=execution.attempt_number,
        run_id=f"{wrong_job_id}.a1",
        operation=execution.command,
        status="queued",
        submitted_at=submitted.updated_at,
        observed_at=submitted.updated_at,
    )
    attempt_reporting.publish(paths.attempt_report, forged)

    with pytest.raises(job_manager.JobManagerCorruptError, match="invalid"):
        job_manager._load_attempt_report(paths, execution=execution)

    storage_policy.atomic_write_private(
        paths.attempt_report,
        lambda handle: handle.write(
            b"x" * (attempt_reporting.MAX_REPORT_BYTES + 1)),
        text=False,
    )
    with pytest.raises(
            job_manager.JobManagerCorruptError, match="bounded regular"):
        job_manager._load_attempt_report(paths, execution=execution)


def test_future_report_timestamp_cannot_block_worker_recovery(
        tmp_path, monkeypatch):
    store = job_runtime.JobStore(tmp_path / "jobs")
    submitted = store.submit_job("full", ["--pdf", "book.pdf"])
    execution, running = _advance_to_running(store, submitted.job_id)
    paths = job_manager._attempt_paths(
        store, submitted.job_id, execution.attempt_number, create=True)
    now = time.time()
    runtime = job_manager.RuntimeMetadata(
        job_id=submitted.job_id,
        attempt_number=execution.attempt_number,
        attempt_token_sha256=job_manager._token_digest(
            execution.attempt_token),
        run_id=f"{submitted.job_id}.a1",
        phase="running",
        job_status=running.status,
        manager_pid=os.getpid(),
        manager_birth=job_manager.process_birth_identity(os.getpid()),
        worker_pid=424243,
        worker_birth="future-clock-worker",
        heartbeat_at=now,
        cleanup_confirmed=None,
        exit_code=None,
        updated_at=now,
    )
    job_manager._write_runtime(paths.runtime, runtime)
    future = now + 3600.0
    report = attempt_reporting.new_report(
        job_id=submitted.job_id,
        attempt_number=execution.attempt_number,
        run_id=f"{submitted.job_id}.a1",
        operation=execution.command,
        status="queued",
        submitted_at=submitted.updated_at,
        observed_at=future,
        manager_started_at=future,
    )
    report = attempt_reporting.advance(
        report, observed_at=future, status="starting")
    report = attempt_reporting.advance(
        report,
        observed_at=future,
        status="running",
        worker_started=True,
        worker_started_at=future,
    )
    attempt_reporting.publish(paths.attempt_report, report)
    terminated = []
    monkeypatch.setattr(
        job_manager, "probe_process_identity",
        lambda _pid, _birth: "match")
    monkeypatch.setattr(
        job_manager, "_terminate_recovered_worker",
        lambda pid, birth: terminated.append((pid, birth)) or True)

    reconciled = job_manager.reconcile_job(store, submitted.job_id)
    _paths, outcome = _attempt_outcome(store, submitted.job_id)

    assert terminated == [(424243, "future-clock-worker")]
    assert reconciled.status == "interrupted"
    assert outcome.status == "interrupted"
    assert outcome.recovery_started_at == future
    assert outcome.recovery_action == "terminated_exact_worker"


def test_out_of_range_runtime_clock_terminalizes_without_trusting_identity(
        tmp_path, monkeypatch):
    store = job_runtime.JobStore(tmp_path / "jobs")
    submitted = store.submit_job("full", ["--pdf", "book.pdf"])
    execution, running = _advance_to_running(store, submitted.job_id)
    paths, runtime = _write_recovery_runtime(
        store, submitted, execution, running,
        worker_pid=424248, worker_birth="invalid-clock-worker")
    invalid_timestamp = attempt_reporting.MAX_TIMESTAMP + 1.0
    job_manager._write_runtime(
        paths.runtime,
        replace(
            runtime,
            heartbeat_at=invalid_timestamp,
            updated_at=invalid_timestamp,
        ),
    )

    def forbidden_probe(_pid, _birth):
        raise AssertionError("a corrupt runtime identity must not be trusted")

    monkeypatch.setattr(
        job_manager, "probe_process_identity", forbidden_probe)

    reconciled = job_manager.reconcile_job(store, submitted.job_id)
    _paths, outcome = _attempt_outcome(store, submitted.job_id)

    assert reconciled.status == "orphaned"
    assert outcome.status == "orphaned"
    assert outcome.runtime_missing
    assert outcome.report_repaired
    assert outcome.timing_reconstructed
    assert outcome.recovery_action == "cleanup_unconfirmed"


def test_exact_recovery_action_survives_failed_terminal_report_write(
        tmp_path, monkeypatch):
    store = job_runtime.JobStore(tmp_path / "jobs")
    submitted = store.submit_job("full", ["--pdf", "book.pdf"])
    execution, running = _advance_to_running(store, submitted.job_id)
    paths, _runtime = _write_recovery_runtime(
        store, submitted, execution, running,
        worker_pid=424244, worker_birth="receipt-worker")
    monkeypatch.setattr(
        job_manager, "probe_process_identity",
        lambda _pid, _birth: "match")
    monkeypatch.setattr(
        job_manager, "_terminate_recovered_worker",
        lambda _pid, _birth: True)
    real_write = job_manager._write_attempt_report

    def fail_recovery_terminal(path, report):
        if (report.status in job_runtime.TERMINAL_JOB_STATUSES
                and report.finalized_by == "recovery"):
            raise OSError("injected recovery report failure")
        return real_write(path, report)

    monkeypatch.setattr(
        job_manager, "_write_attempt_report", fail_recovery_terminal)
    with pytest.raises(OSError, match="recovery report failure"):
        job_manager.reconcile_job(store, submitted.job_id)

    assert store.get_job(submitted.job_id).status == "interrupted"
    receipt = job_manager._load_attempt_report(paths, execution=execution)
    assert receipt is not None
    assert receipt.status == "running"
    assert receipt.recovery_action == "terminated_exact_worker"
    assert not receipt.reconciled

    monkeypatch.setattr(job_manager, "_write_attempt_report", real_write)
    reconciled = job_manager.reconcile_job(store, submitted.job_id)
    _paths, outcome = _attempt_outcome(store, submitted.job_id)

    assert reconciled.status == "interrupted"
    assert outcome.status == "interrupted"
    assert outcome.recovery_action == "terminated_exact_worker"
    assert outcome.report_repaired


def test_failed_recovery_action_is_not_frozen_before_terminal_state(
        tmp_path, monkeypatch):
    store = job_runtime.JobStore(tmp_path / "jobs")
    submitted = store.submit_job("full", ["--pdf", "book.pdf"])
    execution, running = _advance_to_running(store, submitted.job_id)
    paths, _runtime = _write_recovery_runtime(
        store, submitted, execution, running,
        worker_pid=424245, worker_birth="retry-worker")
    probes = iter(("mismatch", "gone"))
    monkeypatch.setattr(
        job_manager, "probe_process_identity",
        lambda _pid, _birth: next(probes))
    monkeypatch.setattr(
        job_manager, "_confirm_worker_tree_gone",
        lambda _pid, _birth: True)
    real_transition = store.transition_job
    failed_once = []

    def fail_first_terminal_transition(job_id, target_status, **kwargs):
        if target_status == "orphaned" and not failed_once:
            failed_once.append(True)
            raise OSError("injected state transition failure")
        return real_transition(job_id, target_status, **kwargs)

    monkeypatch.setattr(store, "transition_job", fail_first_terminal_transition)
    with pytest.raises(OSError, match="state transition failure"):
        job_manager.reconcile_job(store, submitted.job_id)

    pending = job_manager._load_attempt_report(paths, execution=execution)
    assert pending is not None
    assert pending.status == "running"
    assert pending.recovery_action == "pending"

    reconciled = job_manager.reconcile_job(store, submitted.job_id)
    _paths, outcome = _attempt_outcome(store, submitted.job_id)
    assert reconciled.status == "interrupted"
    assert outcome.recovery_action == "confirmed_gone"
    assert outcome.cleanup_confirmed


def test_corrupt_final_cancel_marker_still_publishes_the_terminal_report(
        monkeypatch, tmp_path):
    """The terminal transition has committed; evidence damage cannot undo it."""
    script = tmp_path / "bound-rag.py"
    binding = _runtime_binding(
        script, lambda script_path, argv, **kwargs: 1)
    monkeypatch.setattr(
        job_manager, "_default_runtime_binding", lambda: binding)
    store = job_runtime.JobStore(tmp_path / "jobs")
    submitted = store.submit_job("index", ["--full-reindex"])

    def corrupt(_job_id, _attempt_token):
        raise job_runtime.JobCorruptError("cancel marker is unreadable")

    monkeypatch.setattr(store, "cancel_requested_at", corrupt)

    result = job_manager.run_job(store, submitted.job_id)

    _paths, outcome = _attempt_outcome(store, submitted.job_id)
    assert result.status == "failed"
    assert outcome.status == "failed"
    assert outcome.finished_at is not None
    assert outcome.terminal_reason == result.reason
    # A non-recovery report cannot carry recovery/repair state; the damaged
    # marker is left for the next reconciliation to classify.
    assert not outcome.report_repaired
    assert store.get_job(submitted.job_id).status == "failed"


def test_cancel_marker_arriving_during_recovery_is_reported_unobserved(
        tmp_path, monkeypatch):
    store = job_runtime.JobStore(tmp_path / "jobs")
    submitted = store.submit_job("full", ["--pdf", "book.pdf"])
    execution, running = _advance_to_running(store, submitted.job_id)
    _paths, _runtime = _write_recovery_runtime(
        store, submitted, execution, running,
        worker_pid=424246, worker_birth="cancel-race-worker")
    monkeypatch.setattr(
        job_manager, "probe_process_identity",
        lambda _pid, _birth: "match")

    def cancel_while_terminating(_pid, _birth):
        store.request_cancel(submitted.job_id)
        return True

    monkeypatch.setattr(
        job_manager, "_terminate_recovered_worker",
        cancel_while_terminating)

    reconciled = job_manager.reconcile_job(store, submitted.job_id)
    _paths, outcome = _attempt_outcome(store, submitted.job_id)

    assert reconciled.status == "interrupted"
    assert outcome.cancel_requested
    assert not outcome.cancel_observed
    assert outcome.cancel_requested_at == store.cancel_requested_at(
        submitted.job_id, execution.attempt_token)
    assert outcome.durations_ms()["cancel_observation"] is None
    assert outcome.durations_ms()["cancel_completion"] is not None
    assert outcome.trigger == "recovery"


def test_cancel_publication_is_serialized_before_terminal_state_and_resume(
        tmp_path, monkeypatch):
    store = job_runtime.JobStore(tmp_path / "jobs")
    submitted = store.submit_job("full", ["--pdf", "book.pdf"])
    execution, running = _advance_to_running(store, submitted.job_id)
    _paths, _runtime = _write_recovery_runtime(
        store, submitted, execution, running,
        worker_pid=424250, worker_birth="resume-race-worker")
    cleanup_entered = threading.Event()
    allow_cleanup_return = threading.Event()
    cancel_write_entered = threading.Event()
    allow_cancel_write = threading.Event()
    real_atomic_write = storage_policy.atomic_write_private_json

    def blocking_atomic_write(path, payload):
        if payload.get("kind") == "job_cancel_request":
            cancel_write_entered.set()
            assert allow_cancel_write.wait(5)
        return real_atomic_write(path, payload)

    def terminate_after_cancel_starts(_pid, _birth):
        cleanup_entered.set()
        assert allow_cleanup_return.wait(5)
        return True

    monkeypatch.setattr(
        storage_policy, "atomic_write_private_json", blocking_atomic_write)
    monkeypatch.setattr(
        job_manager, "probe_process_identity",
        lambda _pid, _birth: "match")
    monkeypatch.setattr(
        job_manager, "_terminate_recovered_worker",
        terminate_after_cancel_starts)
    recovery_results = []
    recovery_errors = []
    cancel_results = []
    cancel_errors = []

    def recover():
        try:
            recovery_results.append(
                job_manager.reconcile_job(store, submitted.job_id))
        except BaseException as exc:  # thread boundary captures exact failure
            recovery_errors.append(exc)

    def request_cancel():
        try:
            cancel_results.append(store.request_cancel(submitted.job_id))
        except BaseException as exc:  # thread boundary captures exact failure
            cancel_errors.append(exc)

    recovery_thread = threading.Thread(target=recover)
    recovery_thread.start()
    assert cleanup_entered.wait(5)
    cancel_thread = threading.Thread(target=request_cancel)
    cancel_thread.start()
    assert cancel_write_entered.wait(5)
    allow_cleanup_return.set()
    time.sleep(0.05)
    assert recovery_thread.is_alive()
    allow_cancel_write.set()
    cancel_thread.join(timeout=5)
    recovery_thread.join(timeout=5)

    assert not cancel_thread.is_alive()
    assert not recovery_thread.is_alive()
    assert not cancel_errors
    assert not recovery_errors
    assert cancel_results[0].attempt_number == 1
    assert recovery_results[0].status == "interrupted"
    _paths, outcome = _attempt_outcome(store, submitted.job_id)
    assert outcome.cancel_requested
    assert not outcome.cancel_observed
    assert outcome.recovery_action == "terminated_exact_worker"

    resumed = store.prepare_resume(submitted.job_id)
    assert resumed.status == "queued"
    assert resumed.attempt_number == 2


def test_terminal_report_publication_failure_is_repaired_idempotently(
        tmp_path, monkeypatch):
    store = job_runtime.JobStore(tmp_path / "jobs")
    submitted = store.submit_job("export", [])
    real_write = job_manager._write_attempt_report

    def fail_terminal(path, report):
        if report.status in job_runtime.TERMINAL_JOB_STATUSES:
            raise OSError("injected terminal report publication failure")
        return real_write(path, report)

    monkeypatch.setattr(job_manager, "_write_attempt_report", fail_terminal)
    with pytest.raises(OSError, match="publication failure"):
        job_manager.run_job(
            store, submitted.job_id,
            supervisor=lambda *_args, **_kwargs: 1)

    assert store.get_job(submitted.job_id).status == "failed"
    monkeypatch.setattr(job_manager, "_write_attempt_report", real_write)
    reconciled = job_manager.reconcile_job(store, submitted.job_id)
    paths, outcome = _attempt_outcome(store, submitted.job_id)
    repaired_bytes = paths.attempt_report.read_bytes()

    assert reconciled.status == "failed"
    assert outcome.status == "failed"
    assert outcome.finalized_by == "recovery"
    assert outcome.recovery_action == "not_required"
    assert outcome.report_repaired
    assert not outcome.timing_reconstructed
    assert job_manager.reconcile_job(store, submitted.job_id) == reconciled
    assert paths.attempt_report.read_bytes() == repaired_bytes


def test_terminal_report_repair_merges_a_late_cancel_marker_idempotently(
        tmp_path, monkeypatch):
    store = job_runtime.JobStore(tmp_path / "jobs")
    submitted = store.submit_job("export", [])

    class FakeProcess:
        pid = 424247

    def successful_supervisor(*_args, **kwargs):
        kwargs["on_child_started"](FakeProcess())
        return 0

    monkeypatch.setattr(
        job_manager, "_confirm_worker_tree_gone",
        lambda _pid, _birth: True)
    result = job_manager.run_job(
        store, submitted.job_id,
        supervisor=successful_supervisor)
    execution = store.load_execution(submitted.job_id)
    paths, original = _attempt_outcome(store, submitted.job_id)
    requested_at = max(time.time(), store.get_job(submitted.job_id).updated_at)
    marker = job_runtime._CancelMarker(
        job_id=submitted.job_id,
        attempt_number=execution.attempt_number,
        attempt_token=execution.attempt_token,
        requested_at=requested_at,
    )
    marker_path = (
        store.root / submitted.job_id
        / job_runtime._CANCEL_NAME_TEMPLATE.format(
            attempt_number=execution.attempt_number)
    )
    storage_policy.atomic_write_private_json(
        marker_path, job_runtime._cancel_payload(marker))

    reconciled = job_manager.reconcile_job(store, submitted.job_id)
    _paths, repaired = _attempt_outcome(store, submitted.job_id)
    repaired_bytes = paths.attempt_report.read_bytes()

    assert result.status == "succeeded"
    assert reconciled.status == "succeeded"
    assert not original.cancel_requested
    assert repaired.cancel_requested
    assert not repaired.cancel_observed
    assert repaired.cancel_requested_at == requested_at
    assert repaired.finalized_by == "recovery"
    assert repaired.recovery_action == "not_required"
    assert repaired.report_repaired
    assert repaired.timing_reconstructed
    assert repaired.durations_ms()["cancel_completion"] == 0.0
    assert job_manager.reconcile_job(store, submitted.job_id) == reconciled
    assert paths.attempt_report.read_bytes() == repaired_bytes


def test_reconcile_does_not_auto_resume_an_unstarted_queued_job(tmp_path):
    store = job_runtime.JobStore(tmp_path / "jobs")
    submitted = store.submit_job("full", ["--pdf", "book.pdf"])

    reconciled = job_manager.reconcile_job(store, submitted.job_id)

    assert reconciled == submitted
    assert reconciled.attempt_number == 1


def test_untouched_queued_reconciliation_never_reserves_manager_lease(
        monkeypatch, tmp_path):
    store = job_runtime.JobStore(tmp_path / "jobs")
    submitted = store.submit_job("export", [])

    def forbidden_lease(*_args, **_kwargs):
        raise AssertionError("untouched queued reconciliation stole startup lease")

    monkeypatch.setattr(store, "lease", forbidden_lease)

    assert job_manager.reconcile_job(store, submitted.job_id) == submitted
    assert job_manager.reconcile_all_jobs(store) == [submitted]


def test_reconcile_rechecks_terminal_state_after_acquiring_lease(
        monkeypatch, tmp_path):
    store = job_runtime.JobStore(tmp_path / "jobs")
    submitted = store.submit_job("export", [])
    execution = store.load_execution(submitted.job_id)
    store.request_cancel(submitted.job_id)
    requested = store.transition_job(
        submitted.job_id, "cancel_requested",
        attempt_token=execution.attempt_token,
        expected_revision=execution.revision)
    cancelled = store.transition_job(
        submitted.job_id, "cancelled",
        attempt_token=execution.attempt_token,
        expected_revision=requested.revision)
    real_get_job = store.get_job
    calls = 0

    def stale_then_authoritative(job_id):
        nonlocal calls
        calls += 1
        if calls == 1:
            return replace(cancelled, status="starting")
        return real_get_job(job_id)

    monkeypatch.setattr(store, "get_job", stale_then_authoritative)

    reconciled = job_manager.reconcile_job(store, submitted.job_id)

    assert reconciled == cancelled
    assert calls == 2


def test_reconcile_all_releases_reserved_leases_if_reservation_fails(
        monkeypatch, tmp_path):
    store = job_runtime.JobStore(tmp_path / "jobs")
    first = store.submit_job("export", ["first"])
    second = store.submit_job("export", ["second"])
    store.request_cancel(first.job_id)
    store.request_cancel(second.job_id)
    real_lease = store.lease
    issued = []
    calls = 0

    def fail_second_reservation(job_id, *, timeout=0):
        nonlocal calls
        calls += 1
        if calls == 2:
            raise OSError("injected second reservation failure")
        lease = real_lease(job_id, timeout=timeout)
        issued.append(lease)
        return lease

    monkeypatch.setattr(store, "lease", fail_second_reservation)

    with pytest.raises(OSError, match="second reservation failure"):
        job_manager.reconcile_all_jobs(store)

    assert len(issued) == 1
    assert not issued[0].active
    with real_lease(issued[0].job_id, timeout=0):
        pass


def test_reconcile_releases_job_lease_if_root_lease_exit_fails(
        monkeypatch, tmp_path):
    store = job_runtime.JobStore(tmp_path / "jobs")
    submitted = store.submit_job("export", [])
    real_store_lease = store.store_lease

    class FailingRootExit:
        def __enter__(self):
            self.lease = real_store_lease().acquire()
            return self.lease

        def __exit__(self, _exc_type, _exc, _traceback):
            self.lease.release()
            raise OSError("injected root lease exit failure")

    monkeypatch.setattr(
        store, "store_lease", lambda **_kwargs: FailingRootExit())

    with pytest.raises(OSError, match="root lease exit failure"):
        job_manager.reconcile_job(
            store, submitted.job_id, fail_queued=True)

    with store.lease(submitted.job_id, timeout=0):
        pass


def test_reconcile_queued_runtime_with_worker_pid_fails_closed(tmp_path):
    store = job_runtime.JobStore(tmp_path / "jobs")
    submitted = store.submit_job("full", ["--pdf", "book.pdf"])
    execution = store.load_execution(submitted.job_id)
    paths = job_manager._attempt_paths(
        store, submitted.job_id, execution.attempt_number, create=True)
    now = time.time()
    runtime = job_manager.RuntimeMetadata(
        job_id=submitted.job_id,
        attempt_number=execution.attempt_number,
        attempt_token_sha256=job_manager._token_digest(
            execution.attempt_token),
        run_id=f"{submitted.job_id}.a1",
        phase="starting",
        job_status="queued",
        manager_pid=os.getpid(),
        manager_birth=job_manager.process_birth_identity(os.getpid()),
        worker_pid=os.getpid(),
        worker_birth="deliberately-untrusted-birth-identity",
        heartbeat_at=now,
        cleanup_confirmed=None,
        exit_code=None,
        updated_at=now,
    )
    job_manager._write_runtime(paths.runtime, runtime)

    reconciled = job_manager.reconcile_job(store, submitted.job_id)

    assert reconciled.status == "orphaned"
    with pytest.raises(job_runtime.JobStateError, match="not resumable"):
        store.prepare_resume(submitted.job_id)


def test_detached_launcher_waits_for_exact_ready_handshake(tmp_path):
    marker = tmp_path / "detached-started.txt"
    worker = _write_worker(
        tmp_path / "detached_worker.py",
        """
import sys
import time
from pathlib import Path
Path(sys.argv[2]).write_text("ready", encoding="utf-8")
time.sleep(0.4)
""",
    )
    store = job_runtime.JobStore(tmp_path / "jobs")
    submitted = store.submit_job(
        "full", [str(marker)], timeout_seconds=5)

    launched = job_manager.launch_detached(
        store, submitted.job_id, script_path=worker, ready_timeout=10)

    assert launched.ready
    assert launched.job_id == submitted.job_id
    assert launched.status in {"running", "succeeded"}
    assert _wait_for(
        lambda: store.get_job(submitted.job_id).status == "succeeded",
        timeout=10)
    assert marker.is_file()
    assert "root" not in launched.as_dict()
    assert "manager_pid" not in launched.as_dict()


def test_process_birth_probe_matches_current_and_rejects_wrong_birth():
    birth = job_manager.process_birth_identity(os.getpid())
    if birth is None:
        pytest.skip("platform does not expose process birth identity")
    assert job_manager.probe_process_identity(os.getpid(), birth) == "match"
    assert job_manager.probe_process_identity(
        os.getpid(), birth + "-wrong") == "mismatch"


def test_windows_access_denied_process_probe_is_unverifiable(monkeypatch):
    class AccessDeniedOpenProcess:
        argtypes = None
        restype = None

        @staticmethod
        def __call__(_access, _inherit, _pid):
            return 0

    class AccessDeniedKernel32:
        OpenProcess = AccessDeniedOpenProcess()

    monkeypatch.setattr(
        job_manager.ctypes, "WinDLL",
        lambda *_args, **_kwargs: AccessDeniedKernel32(), raising=False)
    monkeypatch.setattr(
        job_manager.ctypes, "get_last_error", lambda: 5, raising=False)

    assert job_manager._windows_process_identity_probe(12345) == (
        "unverifiable", None)


@pytest.mark.skipif(os.name != "nt", reason="Windows PID-reuse contract")
def test_windows_recovery_holds_verified_handle_through_taskkill(
        monkeypatch):
    birth = "windows:123456"

    class FakeReference:
        closed = False
        terminated = False

        def snapshot(self):
            if self.terminated:
                return "gone", None
            return "live", birth

        def __enter__(self):
            assert not self.closed
            return self

        def __exit__(self, _exc_type, _exc, _traceback):
            self.closed = True

    reference = FakeReference()

    class Completed:
        returncode = 0

    def taskkill(*_args, **_kwargs):
        assert not reference.closed
        reference.terminated = True
        return Completed()

    monkeypatch.setattr(
        job_manager, "_open_windows_process_reference",
        lambda _pid: ("open", reference))
    monkeypatch.setattr(job_manager.subprocess, "run", taskkill)

    assert job_manager._terminate_recovered_worker(12345, birth)
    assert reference.closed


def test_recovery_terminator_refuses_unverifiable_identity(
        monkeypatch):
    signals = []
    monkeypatch.setattr(
        job_manager, "probe_process_identity",
        lambda pid, birth: "unverifiable")
    if os.name == "nt":
        monkeypatch.setattr(
            subprocess, "run",
            lambda *args, **kwargs: signals.append(args))
    else:
        monkeypatch.setattr(
            os, "killpg", lambda *args: signals.append(args))

    assert not job_manager._terminate_recovered_worker(12345, None)
    assert signals == []


def test_queued_cancel_reconciles_without_starting_a_manager(tmp_path):
    store = job_runtime.JobStore(tmp_path / "jobs")
    submitted = store.submit_job("export", ["--chunks", "Book.jsonl"])

    store.request_cancel(submitted.job_id)
    reconciled = job_manager.reconcile_job(store, submitted.job_id)

    assert reconciled.status == "cancelled"
    assert reconciled.attempt_number == 1
    paths, outcome = _attempt_outcome(store, submitted.job_id)
    assert not paths.runtime.exists()
    assert outcome.status == "cancelled"
    assert outcome.finalized_by == "recovery"
    assert outcome.recovery_action == "unstarted_terminalized"
    assert outcome.runtime_missing
    assert outcome.report_repaired
    assert outcome.timing_reconstructed
    assert outcome.cancel_requested and outcome.cancel_observed


def test_detached_early_exit_is_a_terminal_launch_error(
        monkeypatch, tmp_path):
    store = job_runtime.JobStore(tmp_path / "jobs")
    submitted = store.submit_job("export", ["--chunks", "Book.jsonl"])

    class ExitedManager:
        pid = os.getpid()

        @staticmethod
        def poll():
            return 1

    observed = {}

    def launch(command, **options):
        observed.update(command=command, options=options)
        return ExitedManager()

    monkeypatch.setattr(job_manager.subprocess, "Popen", launch)

    with pytest.raises(
            job_manager.JobManagerLaunchError, match="before its ready"):
        job_manager.launch_detached(
            store, submitted.job_id, ready_timeout=0.1)
    assert store.get_job(submitted.job_id).status == "failed"
    assert Path(observed["command"][2]).resolve() == (
        job_manager.MANAGER_SCRIPT_PATH)


def test_detached_spawn_failure_is_a_terminal_launch_error(
        monkeypatch, tmp_path):
    store = job_runtime.JobStore(tmp_path / "jobs")
    submitted = store.submit_job("export", ["--chunks", "Book.jsonl"])
    monkeypatch.setattr(
        job_manager.subprocess, "Popen",
        lambda *args, **kwargs: (_ for _ in ()).throw(
            OSError("injected spawn failure")))

    with pytest.raises(
            job_manager.JobManagerLaunchError, match="could not be started"):
        job_manager.launch_detached(
            store, submitted.job_id, ready_timeout=0.1)

    assert store.get_job(submitted.job_id).status == "failed"


def test_large_valid_run_report_is_within_recovery_bound(tmp_path):
    report = tmp_path / "run.report.json"
    storage_policy.atomic_write_private_json(report, {
        "schema_version": 1,
        "operation": "batch",
        "run_id": "job.a1",
        "status": "succeeded",
        "batch_items": ["x" * 1024 for _ in range(100)],
    })

    assert job_manager._report_status(
        report, operation="batch", run_id="job.a1") == "succeeded"
    assert report.stat().st_size > job_manager._MAX_MANAGER_JSON_BYTES


@pytest.mark.parametrize("status", ["partial", "succeeded"])
def test_report_only_terminal_telemetry_is_not_rewritten(
        tmp_path, status):
    directory = tmp_path / "attempt"
    directory.mkdir()
    paths = job_manager._AttemptPaths(
        directory=directory,
        runtime=directory / "runtime.json",
        ready=directory / "ready.json",
        log=directory / "worker.log",
        events=directory / "run.events.jsonl",
        report=directory / "run.report.json",
        attempt_report=directory / "attempt.report.json",
    )
    storage_policy.atomic_write_private_json(paths.report, {
        "schema_version": 1,
        "operation": "batch",
        "run_id": "job.a1",
        "status": status,
        "batch_items": ["x" * 1024 for _ in range(100)],
    })
    original_report = paths.report.read_bytes()

    assert job_manager._telemetry_status(
        paths, operation="batch", run_id="job.a1") == status
    assert paths.report.read_bytes() == original_report
    assert not paths.events.exists()


def test_resumed_attempt_runs_from_bound_submission_directory(
        monkeypatch, tmp_path):
    working = tmp_path / "submission"
    working.mkdir()
    output = working / "output"
    marker = tmp_path / "bound-cwd.json"
    worker = _write_worker(
        tmp_path / "cwd_worker.py",
        "import json, os, sys\n"
        "from pathlib import Path\n"
        "Path(sys.argv[2]).write_text(json.dumps({"
        "'cwd': os.getcwd(), 'output': os.environ.get("
        "'RAG_PIPELINE_OUTPUT_ROOT')}), encoding='utf-8')\n",
    )
    store = job_runtime.JobStore(tmp_path / "jobs")
    submitted = store.submit_job(
        "export", [str(marker)], timeout_seconds=5,
        working_directory=working, output_root=output)
    first = store.load_execution(submitted.job_id)
    failed = store.transition_job(
        submitted.job_id, "failed", attempt_token=first.attempt_token,
        expected_revision=first.revision)
    store.prepare_resume(
        submitted.job_id, expected_revision=failed.revision)
    other_caller = tmp_path / "other-caller"
    other_caller.mkdir()
    monkeypatch.chdir(other_caller)

    launched = job_manager.launch_detached(
        store, submitted.job_id, script_path=worker, ready_timeout=10)

    assert launched.ready
    assert launched.attempt_number == 2
    assert _wait_for(
        lambda: store.get_job(submitted.job_id).status == "succeeded",
        timeout=10)
    observed = json.loads(marker.read_text(encoding="utf-8"))
    assert Path(observed["cwd"]) == working.resolve()
    assert Path(observed["output"]) == output.resolve()
