from __future__ import annotations

import json
import os
import stat
import subprocess
import threading
import time
from pathlib import Path

import pytest

import job_manager
import job_runtime
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


def _assert_private(path: Path, *, directory: bool) -> None:
    if os.name == "nt":
        assert storage_policy.windows_path_is_private(
            path, directory=directory)
    else:
        expected = (storage_policy.PRIVATE_DIRECTORY_MODE if directory
                    else storage_policy.PRIVATE_FILE_MODE)
        assert stat.S_IMODE(path.stat().st_mode) == expected


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
        "ready_nonce": None,
    }

    paths = job_manager._attempt_paths(
        store, submitted.job_id, 1, create=False)
    for directory in (paths.directory.parent, paths.directory):
        _assert_private(directory, directory=True)
    for artifact in (paths.runtime, paths.log, paths.events, paths.report):
        assert artifact.is_file()
        _assert_private(artifact, directory=False)
    assert "private worker output" in paths.log.read_text(encoding="utf-8")
    runtime = job_manager._load_runtime(paths.runtime, execution=execution)
    assert runtime.phase == "terminal"
    assert runtime.job_status == "succeeded"
    assert runtime.manager_pid == os.getpid()
    assert runtime.worker_pid is not None
    public = json.dumps(result.as_dict())
    assert str(root) not in public
    assert execution.attempt_token not in public
    assert "worker_pid" not in public


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
    time.sleep(0.2)
    stopped = heartbeat.read_text(encoding="utf-8")
    time.sleep(0.25)
    assert heartbeat.read_text(encoding="utf-8") == stopped


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


def test_unconfirmed_cancel_never_classifies_as_cancelled():
    assert job_manager._classify_terminal(
        exit_code=130,
        cancel_observed=True,
        cleanup_confirmed=False,
        telemetry_status="cancelled",
        manager_error=False,
    ) == "interrupted"
    assert job_manager._classify_terminal(
        exit_code=130,
        cancel_observed=True,
        cleanup_confirmed=True,
        telemetry_status=None,
        manager_error=False,
    ) == "interrupted"


@pytest.mark.parametrize("command", ["full", "batch"])
def test_resumed_pipeline_attempt_injects_resume_before_terminator(
        tmp_path, command):
    execution = job_runtime.JobExecution(
        job_id="a" * 32,
        command=command,
        argv=("book.pdf", "--", "--resume"),
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
    assert reconciled.status == "interrupted"
    assert reconciled.attempt_number == 1


def test_reconcile_does_not_auto_resume_an_unstarted_queued_job(tmp_path):
    store = job_runtime.JobStore(tmp_path / "jobs")
    submitted = store.submit_job("full", ["--pdf", "book.pdf"])

    reconciled = job_manager.reconcile_job(store, submitted.job_id)

    assert reconciled == submitted
    assert reconciled.attempt_number == 1


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
