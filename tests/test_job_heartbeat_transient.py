"""A transient failure to refresh the advisory heartbeat must not kill a job.

The job manager rewrites the attempt's ``runtime.json`` about once a second.
On Windows a scanner or indexer can briefly hold that file, and the atomic
replace then fails with a sharing or access error for longer than the storage
helper's short retry window. No reader judges liveness from ``heartbeat_at``,
yet a single failed heartbeat used to abort supervision: the manager killed a
healthy worker and reported the job as ``permission_denied``. Transient replace
errors are now tolerated for a bounded time; persistent or non-transient
failures still fail the job.
"""

import inspect

import pytest

import job_coordination as job_manager
import job_runtime
import storage_policy
from test_job_manager import _write_worker

SLEEPING_WORKER = """
import time
time.sleep(1.5)
print("worker finished")
"""


def _windows_error(winerror, error_type=PermissionError):
    error = error_type(13, "Access is denied")
    error.winerror = winerror
    return error


def _from_heartbeat():
    return any(frame.function == "heartbeat" for frame in inspect.stack())


def _run(tmp_path, monkeypatch, failure, *, fail_count=None):
    real_write = job_manager._write_runtime
    raised = []

    def flaky_write(path, runtime):
        if _from_heartbeat() and (fail_count is None or len(raised) < fail_count):
            raised.append(failure)
            raise failure
        return real_write(path, runtime)

    monkeypatch.setattr(job_manager, "_HEARTBEAT_INTERVAL", 0.0)
    monkeypatch.setattr(job_manager, "_write_runtime", flaky_write)
    worker = _write_worker(tmp_path / "sleeping_worker.py", SLEEPING_WORKER)
    store = job_runtime.JobStore(tmp_path / "jobs")
    submitted = store.submit_job("full", [], timeout_seconds=20)
    result = job_manager.run_job(store, submitted.job_id, script_path=worker)
    return result, raised


@pytest.mark.parametrize("winerror", [5, 32, 33])
def test_transient_heartbeat_replace_failures_do_not_fail_a_healthy_job(
        tmp_path, monkeypatch, winerror):
    result, raised = _run(tmp_path, monkeypatch, _windows_error(winerror),
                          fail_count=3)

    assert len(raised) == 3
    assert result.status == "succeeded"
    assert result.exit_code == 0


def test_heartbeat_failures_beyond_the_tolerance_still_fail_the_job(
        tmp_path, monkeypatch):
    monkeypatch.setattr(job_manager, "_HEARTBEAT_TRANSIENT_FAILURE_BUDGET", 0.0)

    result, raised = _run(tmp_path, monkeypatch, _windows_error(5))

    assert raised
    assert result.status == "failed"


def test_non_transient_heartbeat_failures_still_fail_the_job(
        tmp_path, monkeypatch):
    result, raised = _run(tmp_path, monkeypatch,
                          storage_policy.StoragePolicyError("identity changed"))

    assert len(raised) == 1
    assert result.status == "failed"


@pytest.mark.parametrize(("error", "transient"), [
    (_windows_error(5), True),
    (_windows_error(32, OSError), True),
    (_windows_error(33, OSError), True),
    (_windows_error(2, OSError), False),
    (PermissionError(13, "denied"), False),
    (storage_policy.StoragePolicyError("changed"), False),
])
def test_transient_replace_error_classification(error, transient):
    assert storage_policy.is_transient_replace_error(error) is transient
