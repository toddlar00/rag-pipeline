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
import time

import pytest

import job_coordination as job_manager
import job_runtime
import storage_policy
from test_job_manager import _write_worker


def _worker(seconds):
    return f"""
import time
time.sleep({seconds})
print("worker finished")
"""


def _replace_error(winerror, error_type=PermissionError):
    # os.replace names both paths; the classifier requires filename2.
    error = error_type(13, "Access is denied", "runtime.json.tmp", None, "runtime.json")
    error.winerror = winerror
    return error


def _from_heartbeat():
    return any(frame.function == "heartbeat" for frame in inspect.stack(0))


class _HeartbeatClock:
    """job_coordination's time module, with a stepped clock for the heartbeat.

    Only the heartbeat's monotonic reads advance by a fixed step, so the
    failure budget counts heartbeats rather than wall time and cannot be
    exceeded early on a slow runner. job_coordination's other reads (the
    worker-tree confirmation) stay real; process_supervision's deadline
    clock is its own and is not patched.
    """

    def __init__(self, step):
        self._step, self._now = step, 0.0

    def monotonic(self):
        if _from_heartbeat():
            self._now += self._step
            return self._now
        return time.monotonic()

    def __getattr__(self, name):
        return getattr(time, name)


def _run(tmp_path, monkeypatch, failure, *, should_fail, seconds=1.5):
    real_write = job_manager._write_runtime
    raised = []
    heartbeats = []

    def flaky_write(path, runtime):
        if _from_heartbeat():
            heartbeats.append(None)
            if should_fail(len(heartbeats)):
                raised.append(failure)
                raise failure
        return real_write(path, runtime)

    monkeypatch.setattr(job_manager, "_HEARTBEAT_INTERVAL", 0.0)
    monkeypatch.setattr(job_manager, "_write_runtime", flaky_write)
    worker = _write_worker(tmp_path / "sleeping_worker.py", _worker(seconds))
    store = job_runtime.JobStore(tmp_path / "jobs")
    submitted = store.submit_job("full", [], timeout_seconds=20)
    result = job_manager.run_job(store, submitted.job_id, script_path=worker)
    return result, raised


@pytest.mark.parametrize("winerror", [5, 32, 33])
def test_transient_heartbeat_replace_failures_do_not_fail_a_healthy_job(
        tmp_path, monkeypatch, winerror):
    result, raised = _run(tmp_path, monkeypatch, _replace_error(winerror),
                          should_fail=lambda count: count <= 3)

    assert len(raised) == 3
    assert result.status == "succeeded"
    assert result.exit_code == 0


def test_persistent_heartbeat_failures_fail_the_job_after_the_budget(
        tmp_path, monkeypatch):
    # Each failed heartbeat advances the clock 0.2 s: the fourth reaches 0.6 s
    # past the first failure, beyond the 0.5 s budget.
    monkeypatch.setattr(job_manager, "_HEARTBEAT_TRANSIENT_FAILURE_BUDGET", 0.5)
    monkeypatch.setattr(job_manager, "time", _HeartbeatClock(0.2))

    result, raised = _run(tmp_path, monkeypatch, _replace_error(5),
                          should_fail=lambda count: True, seconds=5)

    assert len(raised) == 4  # three tolerated within the budget, then propagated
    assert result.status == "failed"


def test_a_successful_heartbeat_resets_the_failure_budget(
        tmp_path, monkeypatch):
    # Three failures span 0.4 s of the 0.5 s budget; without the reset after
    # every fourth (successful) heartbeat, the fifth would exceed it.
    monkeypatch.setattr(job_manager, "_HEARTBEAT_TRANSIENT_FAILURE_BUDGET", 0.5)
    monkeypatch.setattr(job_manager, "time", _HeartbeatClock(0.2))

    # A 5 s worker leaves room for the five or more heartbeats the reset needs,
    # even when a slow runner spaces them out.
    result, raised = _run(tmp_path, monkeypatch, _replace_error(32),
                          should_fail=lambda count: count % 4 != 0, seconds=5)

    assert len(raised) > 3
    assert result.status == "succeeded"


def test_non_transient_heartbeat_failures_still_fail_the_job(
        tmp_path, monkeypatch):
    result, raised = _run(tmp_path, monkeypatch,
                          storage_policy.StoragePolicyError("identity changed"),
                          should_fail=lambda count: True)

    assert len(raised) == 1
    assert result.status == "failed"


@pytest.mark.parametrize(("error", "transient"), [
    (_replace_error(5), True),
    (_replace_error(32, OSError), True),
    (_replace_error(33, OSError), True),
    (_replace_error(2, OSError), False),
    (PermissionError(13, "denied"), False),
    (storage_policy.StoragePolicyError("changed"), False),
])
def test_transient_replace_error_classification(error, transient):
    assert storage_policy.is_transient_replace_error(error) is transient


def test_a_permission_inspection_error_is_not_a_transient_replace():
    # An access error while reading a file's DACL names one path, not two.
    error = PermissionError(13, "Access is denied", "runtime.json")
    error.winerror = 5

    assert storage_policy.is_transient_replace_error(error) is False
