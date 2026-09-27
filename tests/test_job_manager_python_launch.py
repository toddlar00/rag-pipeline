"""Detached jobs share interpreter selection without relaxing ready identity."""

from __future__ import annotations

import os

import pytest

import job_coordination
import job_runtime
import process_supervision


def _submission(tmp_path):
    working = tmp_path / "submission with spaces"
    working.mkdir()
    store = job_runtime.JobStore(tmp_path / "jobs")
    submitted = store.submit_job(
        "export", ["--chunks", "synthetic.jsonl"],
        working_directory=working, output_root=working / "output")
    execution = store.load_execution(submitted.job_id)
    return store, execution


def _publish_ready(store, execution, environment, *, pid, birth, nonce=None):
    paths = job_coordination._attempt_paths(
        store, execution.job_id, execution.attempt_number, create=True)
    job_coordination._write_ready(
        paths, execution=execution,
        nonce=(environment[job_coordination._READY_NONCE_ENV]
               if nonce is None else nonce),
        manager_pid=pid, manager_birth=birth)


def test_detached_launch_uses_shared_command_and_detached_environment(
        monkeypatch, tmp_path):
    store, execution = _submission(tmp_path)
    script = tmp_path / "synthetic worker.py"
    inherited_names = (
        job_coordination.JOB_ROOT_ENV, job_coordination.JOB_ID_ENV,
        job_coordination.JOB_ATTEMPT_TOKEN_ENV,
        job_coordination.OUTPUT_ROOT_ENV)
    for name in inherited_names:
        monkeypatch.setenv(name, "stale-parent-job-value")
    monkeypatch.setenv(job_coordination._READY_NONCE_ENV, "stale-parent-nonce")
    monkeypatch.setenv("OCR_TEST_RETAINED_ENVIRONMENT", "retained")
    parent_environment = dict(os.environ)
    observed = {}

    def shared_launch(arguments, environment):
        observed["arguments"] = list(arguments)
        observed["supplied_environment"] = environment
        assert environment is not os.environ
        assert all(name not in environment for name in inherited_names)
        assert environment["OCR_TEST_RETAINED_ENVIRONMENT"] == "retained"
        nonce = environment[job_coordination._READY_NONCE_ENV]
        assert len(nonce) == 32 and nonce != "stale-parent-nonce"
        command = ["shared-interpreter-selection", *arguments]
        child_environment = {**environment, "OCR_TEST_CHILD_ONLY": "selected"}
        observed.update(command=command, child_environment=child_environment)
        return command, child_environment

    class Manager:
        pid = 43210

        @staticmethod
        def poll():
            pytest.fail("matching ready marker must be checked before polling")

    def launch(command, **options):
        assert command is observed["command"]
        assert options["env"] is observed["child_environment"]
        observed["options"] = options
        _publish_ready(
            store, execution, options["env"], pid=Manager.pid,
            birth="birth-exact")
        return Manager()

    monkeypatch.setattr(process_supervision, "python_worker_launch", shared_launch)
    monkeypatch.setattr(job_coordination.subprocess, "Popen", launch)
    monkeypatch.setattr(job_coordination, "process_birth_identity", lambda _pid: "birth-exact")

    result = job_coordination.launch_detached(
        store, execution.job_id, script_path=script)

    assert result.ready
    assert observed["arguments"] == [
        "-u", str(job_coordination.MANAGER_SCRIPT_PATH), "_manage",
        "--root", str(store.root), "--job-id", execution.job_id,
        "--script", str(script.resolve()),
    ]
    options = observed["options"]
    assert options["cwd"] == str(execution.working_directory)
    assert options["close_fds"] is True
    assert options["stdin"] == job_coordination.subprocess.DEVNULL
    assert options["stdout"] == job_coordination.subprocess.DEVNULL
    assert options["stderr"] == job_coordination.subprocess.DEVNULL
    assert "shell" not in options
    if os.name == "nt":
        assert options["creationflags"] == (
            job_coordination.subprocess.CREATE_NO_WINDOW
            | job_coordination.subprocess.DETACHED_PROCESS
            | job_coordination.subprocess.CREATE_NEW_PROCESS_GROUP)
        assert "start_new_session" not in options
    else:
        assert options["start_new_session"] is True
        assert "creationflags" not in options
    assert "OCR_TEST_CHILD_ONLY" not in observed["supplied_environment"]
    assert dict(os.environ) == parent_environment


@pytest.mark.parametrize("mismatch", ["pid", "birth", "nonce"])
def test_shared_launch_still_rejects_nonmatching_ready_identity(
        monkeypatch, tmp_path, mismatch):
    store, execution = _submission(tmp_path)

    class ExitedManager:
        pid = 43210

        @staticmethod
        def poll():
            return 0

    def launch(_command, **options):
        _publish_ready(
            store, execution, options["env"],
            pid=ExitedManager.pid + (mismatch == "pid"),
            birth="different-birth" if mismatch == "birth" else "birth-exact",
            nonce="different-nonce" if mismatch == "nonce" else None)
        return ExitedManager()

    monkeypatch.setattr(
        process_supervision, "python_worker_launch",
        lambda arguments, environment: (["shared-interpreter", *arguments], dict(environment)))
    monkeypatch.setattr(job_coordination.subprocess, "Popen", launch)
    monkeypatch.setattr(job_coordination, "process_birth_identity", lambda _pid: "birth-exact")

    with pytest.raises(job_coordination.JobManagerLaunchError, match="before its ready handshake"):
        job_coordination.launch_detached(store, execution.job_id)

    assert store.get_job(execution.job_id).status == "failed"


@pytest.mark.parametrize("error", [
    RuntimeError("unavailable private interpreter identity"),
    OSError("inaccessible private interpreter path"),
])
def test_interpreter_selection_failure_terminalizes_without_spawn(
        monkeypatch, tmp_path, error):
    store, execution = _submission(tmp_path)

    def unavailable(_arguments, _environment):
        raise error

    monkeypatch.setattr(process_supervision, "python_worker_launch", unavailable)
    monkeypatch.setattr(
        job_coordination.subprocess, "Popen",
        lambda *_args, **_kwargs: pytest.fail("invalid interpreter cannot spawn"))

    with pytest.raises(job_coordination.JobManagerLaunchError, match="could not be started") as caught:
        job_coordination.launch_detached(store, execution.job_id)

    assert str(error) not in str(caught.value)
    assert caught.value.__cause__ is error
    assert store.get_job(execution.job_id).status == "failed"
