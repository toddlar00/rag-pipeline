import json
import os
import stat

import pytest

import attempt_reporting
import storage_policy


JOB_ID = "a" * 32
RUN_ID = f"{JOB_ID}.a1"


def _running_report():
    report = attempt_reporting.new_report(
        job_id=JOB_ID,
        attempt_number=1,
        run_id=RUN_ID,
        operation="full",
        status="starting",
        submitted_at=10.0,
        observed_at=12.0,
        manager_started_at=11.0,
    )
    return attempt_reporting.advance(
        report,
        observed_at=14.0,
        status="running",
        worker_started=True,
        worker_started_at=13.0,
    )


def _terminal_report():
    report = _running_report()
    report = attempt_reporting.advance(
        report,
        observed_at=16.0,
        status="cancel_requested",
        trigger="cancel",
        cancel_requested=True,
        cancel_requested_at=15.0,
        cancel_observed=True,
        cancel_observed_at=16.0,
    )
    return attempt_reporting.advance(
        report,
        observed_at=18.0,
        status="cancelled",
        cleanup_confirmed=True,
        cleanup_confirmed_at=17.0,
        finished_at=18.0,
        worker_telemetry_status="cancelled",
        terminal_reason="cancelled",
        exit_code=130,
    )


def test_attempt_report_records_exact_derived_lifecycle_durations():
    report = _terminal_report()

    assert report.report_revision == 4
    assert report.durations_ms() == {
        "total": 8000.0,
        "dispatch": 1000.0,
        "startup": 2000.0,
        "worker": 5000.0,
        "cancel_observation": 1000.0,
        "cancel_completion": 3000.0,
        "recovery": None,
    }
    assert attempt_reporting.parse_report(
        report.as_payload(),
        expected_job_id=JOB_ID,
        expected_attempt_number=1,
        expected_run_id=RUN_ID,
        expected_operation="full",
    ) == report


def test_parse_accepts_reordered_objects_but_rejects_derived_tampering():
    payload = _terminal_report().as_payload()
    payload["timestamps"] = dict(reversed(payload["timestamps"].items()))
    payload["durations_ms"] = dict(
        reversed(payload["durations_ms"].items()))
    payload["outcome"] = dict(reversed(payload["outcome"].items()))

    parsed = attempt_reporting.parse_report(
        payload,
        expected_job_id=JOB_ID,
        expected_attempt_number=1,
        expected_run_id=RUN_ID,
        expected_operation="full",
    )
    assert parsed.status == "cancelled"

    payload["durations_ms"]["total"] = 1.0
    with pytest.raises(ValueError, match="derived durations"):
        attempt_reporting.parse_report(
            payload,
            expected_job_id=JOB_ID,
            expected_attempt_number=1,
            expected_run_id=RUN_ID,
            expected_operation="full",
        )


@pytest.mark.parametrize(("path", "value", "message"), [
    (("status",), "succeeded", "terminal outcome"),
    (("outcome", "cleanup_confirmed"), False, "confirmed cleanup"),
    (("outcome", "terminal_reason"), "worker_failed", "terminal outcome"),
    (("timestamps", "finished_at"), 12.0, "out of order"),
    (("outcome", "recovery_action"), "confirmed_gone", "recovery action"),
])
def test_parse_rejects_contradictory_lifecycle_evidence(path, value, message):
    payload = _terminal_report().as_payload()
    target = payload
    for key in path[:-1]:
        target = target[key]
    target[path[-1]] = value

    with pytest.raises(ValueError, match=message):
        attempt_reporting.parse_report(
            payload,
            expected_job_id=JOB_ID,
            expected_attempt_number=1,
            expected_run_id=RUN_ID,
            expected_operation="full",
        )


def test_parse_rejects_unknown_fields_and_wrong_expected_identity():
    payload = _terminal_report().as_payload()
    payload["private_path"] = "C:/secret.pdf"
    with pytest.raises(ValueError, match="missing or unknown"):
        attempt_reporting.parse_report(
            payload,
            expected_job_id=JOB_ID,
            expected_attempt_number=1,
            expected_run_id=RUN_ID,
            expected_operation="full",
        )

    payload = _terminal_report().as_payload()
    with pytest.raises(ValueError, match="bind"):
        attempt_reporting.parse_report(
            payload,
            expected_job_id="b" * 32,
            expected_attempt_number=1,
            expected_run_id=RUN_ID,
            expected_operation="full",
        )


@pytest.mark.parametrize("invalid", [True, float("nan"), -1.0])
def test_timestamp_apis_reject_nonfinite_boolean_and_negative_values(invalid):
    with pytest.raises(ValueError, match="timestamp"):
        attempt_reporting.new_report(
            job_id=JOB_ID,
            attempt_number=1,
            run_id=RUN_ID,
            operation="full",
            status="starting",
            submitted_at=10.0,
            observed_at=12.0,
            manager_started_at=invalid,
        )
    with pytest.raises(ValueError, match="timestamp"):
        attempt_reporting.advance(
            _running_report(),
            observed_at=14.0,
            cancel_requested_at=invalid,
        )


def test_published_report_is_private_redacted_and_atomically_replaceable(
        tmp_path):
    path = storage_policy.ensure_private_directory(tmp_path / "attempt") / (
        "attempt.report.json")
    report = _terminal_report()

    attempt_reporting.publish(path, report)
    raw = path.read_text(encoding="utf-8")
    payload = json.loads(raw)

    assert payload == report.as_payload()
    forbidden = {
        "argv", "path", "pid", "birth", "token", "log", "exception",
        "C:/secret.pdf", "super-secret",
    }
    rendered = raw.lower()
    assert all(value.lower() not in rendered for value in forbidden)
    if os.name == "nt":
        assert storage_policy.windows_path_is_private(path, directory=False)
    else:
        assert stat.S_IMODE(path.stat().st_mode) == 0o600

    attempt_reporting.publish(path, report)
    assert json.loads(path.read_text(encoding="utf-8"))[
        "report_revision"] == report.report_revision
    assert not list(path.parent.glob(f".{path.name}.*.tmp"))


@pytest.mark.parametrize("changes", [
    {"status": "running", "finished_at": None, "terminal_reason": None,
     "cleanup_confirmed": None, "cleanup_confirmed_at": None,
     "exit_code": None},
    {"status": "failed", "terminal_reason": "worker_failed"},
])
def test_terminal_report_cannot_be_rewritten_or_reopened(changes):
    with pytest.raises(ValueError, match="terminal.*immutable"):
        attempt_reporting.advance(
            _terminal_report(), observed_at=20.0, **changes)


@pytest.mark.parametrize("changes", [
    {"status": "queued"},
    {"worker_started": False},
    {"worker_started_at": None},
])
def test_active_report_cannot_roll_back_state_or_milestones(changes):
    with pytest.raises(ValueError, match="transition|cleared|immutable"):
        attempt_reporting.advance(
            _running_report(), observed_at=15.0, **changes)


def test_extreme_finite_timestamps_are_rejected_before_publication(tmp_path):
    with pytest.raises(ValueError, match="timestamp"):
        attempt_reporting.new_report(
            job_id=JOB_ID,
            attempt_number=1,
            run_id=RUN_ID,
            operation="full",
            status="starting",
            submitted_at=1e308,
            observed_at=1e308,
            manager_started_at=1e308,
        )
    assert not (tmp_path / "attempt.report.json").exists()


def test_publish_rejects_arbitrary_payload_objects(tmp_path):
    class ForgedReport:
        @staticmethod
        def as_payload():
            return {"private_path": "C:/secret.pdf", "token": "secret"}

    path = tmp_path / "attempt.report.json"
    with pytest.raises(TypeError, match="exact AttemptReport"):
        attempt_reporting.publish(path, ForgedReport())
    assert not path.exists()


@pytest.mark.parametrize(("mutations", "message"), [
    ({
        "outcome.worker_started": False,
        "timestamps.worker_started_at": None,
    }, "running attempt lacks"),
    ({
        "status": "cancel_requested",
        "outcome.cancel_requested": False,
        "outcome.cancel_observed": False,
        "timestamps.cancel_requested_at": None,
        "timestamps.cancel_observed_at": None,
    }, "cancel-requested attempt lacks"),
    ({"outcome.trigger": "timeout"}, "active attempt trigger"),
])
def test_active_report_state_requires_matching_milestone_evidence(
        mutations, message):
    payload = _running_report().as_payload()
    for dotted, value in mutations.items():
        target = payload
        parts = dotted.split(".")
        for part in parts[:-1]:
            target = target[part]
        target[parts[-1]] = value

    with pytest.raises(ValueError, match=message):
        attempt_reporting.parse_report(
            payload,
            expected_job_id=JOB_ID,
            expected_attempt_number=1,
            expected_run_id=RUN_ID,
            expected_operation="full",
        )


def test_recovery_pending_and_completed_states_cannot_cross_terminal_boundary():
    active = _running_report().as_payload()
    active["outcome"].update({
        "trigger": "recovery",
        "finalized_by": "recovery",
        "reconciled": True,
        "recovery_action": "confirmed_gone",
    })
    active["timestamps"]["recovery_started_at"] = 14.0
    active["updated_at"] = 14.0
    with pytest.raises(ValueError, match="terminal and reconciled"):
        attempt_reporting.parse_report(
            active,
            expected_job_id=JOB_ID,
            expected_attempt_number=1,
            expected_run_id=RUN_ID,
            expected_operation="full",
        )

    terminal = _terminal_report().as_payload()
    terminal["outcome"].update({
        "finalized_by": "recovery",
        "recovery_action": "pending",
    })
    terminal["timestamps"]["recovery_started_at"] = 16.0
    terminal["durations_ms"]["recovery"] = 2000.0
    with pytest.raises(ValueError, match="pending recovery"):
        attempt_reporting.parse_report(
            terminal,
            expected_job_id=JOB_ID,
            expected_attempt_number=1,
            expected_run_id=RUN_ID,
            expected_operation="full",
        )


def test_only_successful_cleanup_can_be_persisted_as_an_active_receipt():
    pending = attempt_reporting.advance(
        _running_report(),
        observed_at=15.0,
        trigger="recovery",
        finalized_by="recovery",
        recovery_started_at=15.0,
        recovery_action="pending",
    )
    receipt = attempt_reporting.advance(
        pending, observed_at=16.0, recovery_action="confirmed_gone")

    assert receipt.status == "running"
    assert receipt.recovery_action == "confirmed_gone"
    assert not receipt.reconciled
    with pytest.raises(ValueError, match="successful cleanup"):
        attempt_reporting.advance(
            pending, observed_at=16.0, recovery_action="refused_identity")


def test_orphaned_report_can_preserve_successful_worker_telemetry():
    pending = attempt_reporting.advance(
        _running_report(),
        observed_at=15.0,
        trigger="recovery",
        finalized_by="recovery",
        recovery_started_at=15.0,
        recovery_action="pending",
    )
    orphaned = attempt_reporting.advance(
        pending,
        observed_at=18.0,
        status="orphaned",
        cleanup_confirmed=False,
        finished_at=18.0,
        reconciled=True,
        recovery_action="cleanup_unconfirmed",
        worker_telemetry_status="succeeded",
        terminal_reason="cleanup_unconfirmed",
    )

    assert attempt_reporting.parse_report(
        orphaned.as_payload(),
        expected_job_id=JOB_ID,
        expected_attempt_number=1,
        expected_run_id=RUN_ID,
        expected_operation="full",
    ) == orphaned


def test_timeout_outcome_requires_exact_trigger_reason_and_exit_code():
    payload = _terminal_report().as_payload()
    payload["status"] = "failed"
    payload["outcome"]["terminal_reason"] = "timeout"
    payload["outcome"]["exit_code"] = 124

    with pytest.raises(ValueError, match="timeout outcome"):
        attempt_reporting.parse_report(
            payload,
            expected_job_id=JOB_ID,
            expected_attempt_number=1,
            expected_run_id=RUN_ID,
            expected_operation="full",
        )


def test_cancelled_outcome_requires_cancel_or_recovery_trigger():
    payload = _terminal_report().as_payload()
    payload["outcome"]["trigger"] = "normal"

    with pytest.raises(ValueError, match="inconsistent trigger"):
        attempt_reporting.parse_report(
            payload,
            expected_job_id=JOB_ID,
            expected_attempt_number=1,
            expected_run_id=RUN_ID,
            expected_operation="full",
        )
