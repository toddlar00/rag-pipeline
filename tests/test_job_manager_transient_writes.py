"""The manager's non-advisory writes tolerate transient Windows replaces.

Besides the advisory heartbeat, the durable-job manager publishes the
attempt's runtime metadata, outcome report and ready marker at every state
change. On Windows a scanner, indexer or sync client can briefly hold the
destination, and the atomic replace then fails with a sharing or access error
for longer than the storage helper's short retry window. A child-started write
used to fail a healthy job, and a terminal write escaped ``run_job`` after the
store had committed the terminal status. Those writes are now retried as fresh
atomic writes within bounded budgets: one shared by every write before the
ready handshake, a short one for cancellation evidence, and one per write
afterwards. Non-transient failures still propagate at once.
"""

import inspect
import json
import math
import threading
import time

import pytest

import job_coordination as job_manager
import job_runtime
import storage_policy
from test_job_heartbeat_transient import _replace_error
from test_job_manager import _attempt_outcome, _wait_for, _write_worker


_NONCE = "0" * 32


def _in_frame(name):
    return any(frame.function == name for frame in inspect.stack(0))


class _VirtualRetryClock:
    """job_coordination's time module, with virtual write-retry backoff.

    A sleep taken inside the manager's retry helper advances this clock
    instead of waiting, so every budget is exhausted deterministically and
    fast and the recorded sleeps are the real schedule. Every other read and
    sleep stays real.
    """

    def __init__(self):
        self.offset = 0.0
        self.sleeps = []

    def monotonic(self):
        return time.monotonic() + self.offset

    def sleep(self, seconds):
        if _in_frame("_write_tolerating_transient_replace"):
            self.sleeps.append(seconds)
            self.offset += seconds
            return
        time.sleep(seconds)

    def __getattr__(self, name):
        return getattr(time, name)


class _FlakyWrites:
    """Fail the manager's write seams by rule and record the writes made."""

    def __init__(self, monkeypatch):
        self.rules = []
        self.raised = []
        self.written = []
        real_runtime = job_manager._write_runtime
        real_report = job_manager._write_attempt_report
        real_ready = job_manager._write_ready
        real_cap = job_manager._cap_closed_worker_log

        def write_runtime(path, runtime):
            self._inject("runtime", runtime)
            real_runtime(path, runtime)
            self.written.append((
                "runtime", runtime.phase, runtime.job_status,
                runtime.worker_pid is not None))

        def write_report(path, report):
            self._inject("report", report)
            real_report(path, report)
            self.written.append(("report", report.status))

        def write_ready(paths, **options):
            self._inject("ready", options)
            real_ready(paths, **options)
            self.written.append(("ready",))

        def cap_log(path):
            self._inject("log", path)
            real_cap(path)

        monkeypatch.setattr(job_manager, "_write_runtime", write_runtime)
        monkeypatch.setattr(job_manager, "_write_attempt_report", write_report)
        monkeypatch.setattr(job_manager, "_write_ready", write_ready)
        monkeypatch.setattr(job_manager, "_cap_closed_worker_log", cap_log)

    def fail(self, kind, count, predicate=lambda _value: True,
             error=lambda: _replace_error(5)):
        self.rules.append([kind, predicate, count, error])

    def failures(self, kind):
        return [error for failed, error in self.raised if failed == kind]

    def _inject(self, kind, value):
        for rule in self.rules:
            rule_kind, predicate, count, error = rule
            if rule_kind == kind and count > 0 and predicate(value):
                rule[2] = count - 1
                failure = error()
                self.raised.append((kind, failure))
                raise failure


class _FakeWorker:
    pid = 424247


def _started_supervisor(*_args, **kwargs):
    kwargs["on_child_started"](_FakeWorker())
    return 0


def _forbidden_supervisor(*_args, **_kwargs):
    raise AssertionError("the manager started a worker")


@pytest.fixture
def clock(monkeypatch):
    virtual = _VirtualRetryClock()
    monkeypatch.setattr(job_manager, "time", virtual)
    return virtual


@pytest.fixture
def writes(monkeypatch):
    return _FlakyWrites(monkeypatch)


def _submit(tmp_path, command="full", arguments=()):
    store = job_runtime.JobStore(tmp_path / "jobs")
    return store, store.submit_job(
        command, list(arguments), timeout_seconds=20)


def _fake_run(tmp_path, monkeypatch, supervisor=_started_supervisor):
    monkeypatch.setattr(
        job_manager, "_confirm_worker_tree_gone", lambda _pid, _birth: True)
    store, submitted = _submit(tmp_path, "export")
    return store, submitted, lambda: job_manager.run_job(
        store, submitted.job_id, supervisor=supervisor, ready_nonce=_NONCE)


def _attempt_json(store, job_id, name):
    return json.loads(
        (store.root / job_id / "attempts" / "000001" / name)
        .read_text(encoding="utf-8"))


# Characterization: the manager's write order on the success and pre-launch
# cancellation paths, which the retries must leave unchanged.

def test_successful_attempt_write_order(tmp_path, monkeypatch, clock, writes):
    store, submitted, run = _fake_run(tmp_path, monkeypatch)

    result = run()

    assert result.status == "succeeded"
    assert writes.raised == [] and clock.sleeps == []
    assert writes.written == [
        ("report", "queued"),
        ("runtime", "starting", "queued", False),
        ("runtime", "starting", "starting", False),
        ("report", "starting"),
        ("runtime", "starting", "starting", True),
        ("runtime", "running", "running", True),
        ("report", "running"),
        ("ready",),
        ("runtime", "terminal", "succeeded", True),
        ("report", "succeeded"),
    ]


def test_prelaunch_cancellation_write_order(
        tmp_path, monkeypatch, clock, writes):
    store, submitted, run = _fake_run(
        tmp_path, monkeypatch, supervisor=_forbidden_supervisor)
    store.request_cancel(submitted.job_id)

    result = run()

    assert result.status == "cancelled"
    assert writes.raised == [] and clock.sleeps == []
    assert writes.written == [
        ("report", "queued"),
        ("runtime", "starting", "queued", False),
        ("runtime", "starting", "starting", False),
        ("report", "starting"),
        ("report", "cancel_requested"),
        ("ready",),
        ("runtime", "terminal", "cancelled", False),
        ("report", "cancelled"),
    ]


# Transient failures before the ready handshake.

@pytest.mark.parametrize(("kind", "winerror"), [
    ("runtime", 5), ("report", 32), ("ready", 33),
])
def test_child_started_writes_survive_transient_replace_failures(
        tmp_path, monkeypatch, clock, writes, kind, winerror):
    writes.fail(kind, 2, lambda _value: _in_frame("child_started"),
                lambda: _replace_error(winerror))
    worker = _write_worker(
        tmp_path / "worker.py", "import time\ntime.sleep(0.2)\n")
    store, submitted = _submit(tmp_path)

    result = job_manager.run_job(
        store, submitted.job_id, script_path=worker, ready_nonce=_NONCE)

    assert len(writes.failures(kind)) == 2
    assert result.status == "succeeded"
    assert result.exit_code == 0
    assert _attempt_json(store, submitted.job_id, "ready.json")["kind"] == (
        "job_manager_ready")
    runtime = _attempt_json(store, submitted.job_id, "runtime.json")
    assert runtime["phase"] == "terminal"
    assert runtime["worker_pid"] is not None


def test_writes_before_the_ready_handshake_share_one_budget(
        tmp_path, monkeypatch, clock, writes):
    # The first report write fails five times (1.9 s of backoff); the next
    # write then fails persistently and may only use what the shared budget
    # has left, so the handshake cannot be delayed past it.
    writes.fail("report", 5, lambda report: report.status == "queued")
    writes.fail("runtime", math.inf,
                lambda runtime: runtime.job_status == "queued")
    store, submitted, run = _fake_run(
        tmp_path, monkeypatch, supervisor=_forbidden_supervisor)

    with pytest.raises(PermissionError):
        run()

    budget = job_manager._PRE_READY_TRANSIENT_WRITE_BUDGET
    delays = list(job_manager._MANAGER_TRANSIENT_WRITE_DELAYS)
    assert len(writes.failures("report")) == 5
    assert len(writes.failures("runtime")) > 1
    assert clock.sleeps[:5] == delays
    assert sum(clock.sleeps) <= budget
    assert sum(clock.sleeps) > sum(delays)
    assert store.get_job(submitted.job_id).status == "queued"


def test_writes_keep_the_pre_ready_budget_until_ready_is_published(
        tmp_path, monkeypatch, clock, writes):
    # A worker start that fails before the ready marker leaves the launcher
    # still waiting, so the terminal writes that follow must not start fresh
    # per-write budgets of their own.
    writes.fail("ready", math.inf)
    writes.fail("runtime", math.inf,
                lambda runtime: runtime.phase == "terminal")
    store, submitted, run = _fake_run(tmp_path, monkeypatch)

    with pytest.raises(PermissionError):
        run()

    assert len(writes.failures("ready")) > 1
    assert len(writes.failures("runtime")) >= 1
    assert sum(clock.sleeps) <= job_manager._PRE_READY_TRANSIENT_WRITE_BUDGET
    assert store.get_job(submitted.job_id).status == "failed"


# Transient failures after a committed terminal transition.

def test_terminal_runtime_write_survives_transient_replace_failures(
        tmp_path, monkeypatch, clock, writes):
    writes.fail("runtime", 3, lambda runtime: runtime.phase == "terminal")
    store, submitted, run = _fake_run(tmp_path, monkeypatch)

    result = run()

    assert len(writes.failures("runtime")) == 3
    assert result.status == "succeeded"
    assert _attempt_json(
        store, submitted.job_id, "runtime.json")["phase"] == "terminal"
    _paths, outcome = _attempt_outcome(store, submitted.job_id)
    assert outcome.status == "succeeded"
    assert outcome.finalized_by == "manager"


def test_terminal_report_write_survives_transient_replace_failures(
        tmp_path, monkeypatch, clock, writes):
    writes.fail(
        "report", 2,
        lambda report: report.status in job_runtime.TERMINAL_JOB_STATUSES,
        lambda: _replace_error(32, OSError))
    store, submitted, run = _fake_run(tmp_path, monkeypatch)

    result = run()

    assert len(writes.failures("report")) == 2
    assert result.status == "succeeded"
    _paths, outcome = _attempt_outcome(store, submitted.job_id)
    assert outcome.status == "succeeded"
    assert outcome.finalized_by == "manager"


def test_persistent_terminal_failure_escapes_after_its_budget(
        tmp_path, monkeypatch, clock, writes):
    writes.fail("runtime", math.inf,
                lambda runtime: runtime.phase == "terminal")
    store, submitted, run = _fake_run(tmp_path, monkeypatch)

    with pytest.raises(PermissionError):
        run()

    budget = job_manager._MANAGER_TRANSIENT_WRITE_BUDGET
    delays = job_manager._MANAGER_TRANSIENT_WRITE_DELAYS
    assert len(writes.failures("runtime")) == len(clock.sleeps) + 1
    assert clock.sleeps[:len(delays)] == list(delays)
    assert max(clock.sleeps) == delays[-1]  # the last delay repeats
    assert budget - delays[-1] < sum(clock.sleeps) <= budget
    # The committed terminal status stands; reconciliation repairs the rest.
    assert store.get_job(submitted.job_id).status == "succeeded"


@pytest.mark.parametrize("error", [
    lambda: PermissionError(13, "denied"),
    lambda: storage_policy.StoragePolicyError("identity changed"),
    lambda: _replace_error(2, OSError),
], ids=["one-path-access", "storage-policy", "not-a-sharing-error"])
def test_non_transient_terminal_failure_is_not_retried(
        tmp_path, monkeypatch, clock, writes, error):
    writes.fail("runtime", math.inf,
                lambda runtime: runtime.phase == "terminal", error)
    store, submitted, run = _fake_run(tmp_path, monkeypatch)

    with pytest.raises(OSError):
        run()

    assert len(writes.failures("runtime")) == 1
    assert clock.sleeps == []
    assert store.get_job(submitted.job_id).status == "succeeded"


def test_prelaunch_cancellation_terminal_writes_survive_transient_failures(
        tmp_path, monkeypatch, clock, writes):
    writes.fail("runtime", 2, lambda runtime: runtime.phase == "terminal")
    writes.fail("report", 2, lambda report: report.status == "cancelled")
    store, submitted, run = _fake_run(
        tmp_path, monkeypatch, supervisor=_forbidden_supervisor)
    store.request_cancel(submitted.job_id)

    result = run()

    assert len(writes.failures("runtime")) == 2
    assert len(writes.failures("report")) == 2
    assert result.status == "cancelled"
    _paths, outcome = _attempt_outcome(store, submitted.job_id)
    assert outcome.status == "cancelled"
    assert outcome.finalized_by == "manager"


def test_closed_worker_log_cap_survives_transient_replace_failures(
        tmp_path, monkeypatch, clock, writes):
    writes.fail("log", 2, error=lambda: _replace_error(33, OSError))
    store, submitted, run = _fake_run(tmp_path, monkeypatch)

    result = run()

    assert len(writes.failures("log")) == 2
    assert result.status == "succeeded"
    _paths, outcome = _attempt_outcome(store, submitted.job_id)
    assert not outcome.manager_error


# Transient failures of the cancellation evidence while a worker runs.

def _cancel_running_worker(tmp_path):
    started = tmp_path / "worker-started.txt"
    worker = _write_worker(
        tmp_path / "cancel_worker.py",
        """
import sys
import time
from pathlib import Path
Path(sys.argv[2]).write_text("started", encoding="utf-8")
time.sleep(30)
""",
    )
    store, submitted = _submit(tmp_path, arguments=[str(started)])
    result_box = {}

    def run_manager():
        try:
            result_box["result"] = job_manager.run_job(
                store, submitted.job_id, script_path=worker)
        except BaseException as exc:  # surfaced by the assertions below
            result_box["error"] = exc

    manager = threading.Thread(target=run_manager)
    manager.start()
    assert _wait_for(started.is_file)
    store.request_cancel(submitted.job_id)
    manager.join(timeout=30)
    assert not manager.is_alive()
    return store, submitted, result_box


def _cancel_evidence(_value):
    return _in_frame("cancellation_requested")


def test_cancellation_writes_survive_transient_replace_failures(
        tmp_path, clock, writes):
    writes.fail("runtime", 2, _cancel_evidence)
    writes.fail("report", 2, _cancel_evidence)

    store, submitted, outcome = _cancel_running_worker(tmp_path)

    assert "error" not in outcome, outcome.get("error")
    result = outcome["result"]
    assert len(writes.failures("runtime")) == 2
    assert len(writes.failures("report")) == 2
    assert result.status == "cancelled"
    assert result.cleanup_confirmed
    _paths, outcome = _attempt_outcome(store, submitted.job_id)
    assert outcome.status == "cancelled"
    assert outcome.cancel_observed


def test_cancellation_writes_share_one_short_budget(tmp_path, clock, writes):
    # The runtime write fails five times (1.9 s of backoff); the report write
    # then fails persistently and gets only what is left of the short budget,
    # so a cancelled worker is not left running for long.
    writes.fail("runtime", 5, _cancel_evidence)
    writes.fail("report", math.inf, _cancel_evidence)

    store, submitted, _outcome = _cancel_running_worker(tmp_path)

    budget = job_manager._CANCELLATION_TRANSIENT_WRITE_BUDGET
    assert len(writes.failures("runtime")) == 5
    assert len(writes.failures("report")) > 1
    assert sum(clock.sleeps) <= budget
    # The exhausted failure still aborts supervision, as a first failure did
    # before these retries: the worker is stopped and the attempt is terminal.
    # run_job then raises from the terminal report, which rejects a manager
    # error under a cancel trigger; that predates the retries and is not
    # asserted here.
    assert store.get_job(submitted.job_id).status == "interrupted"
    runtime = _attempt_json(store, submitted.job_id, "runtime.json")
    assert runtime["phase"] == "terminal"
    assert runtime["cleanup_confirmed"] is True
