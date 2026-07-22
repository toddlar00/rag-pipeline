import json
import os
import stat
import threading

import pytest

from run_telemetry import (
    RunTelemetry,
    failure_diagnostic,
    finalize_interrupted_run,
)


def _events(path):
    return [json.loads(line) for line in path.read_text(
        encoding="utf-8").splitlines()]


def test_run_and_stage_reports_share_one_safe_identifier(tmp_path):
    events_path = tmp_path / "private" / "events.jsonl"
    report_path = tmp_path / "private" / "report.json"
    telemetry = RunTelemetry(
        "full", run_id="run-123", events_path=events_path,
        report_path=report_path)

    telemetry.start()
    telemetry.stage_started("convert")
    telemetry.stage_finished(
        "convert", metrics={"pages": 12, "resumed": False})
    payload = telemetry.finish()

    events = _events(events_path)
    report = json.loads(report_path.read_text(encoding="utf-8"))
    assert payload == report
    assert report["run_id"] == "run-123"
    assert report["status"] == "succeeded"
    assert report["stages"]["convert"]["completed"] == 1
    assert report["stages"]["convert"]["duration_ms"] >= 0
    assert [event["sequence"] for event in events] == [1, 2, 3, 4]
    assert all(event["run_id"] == "run-123" for event in events)
    assert events[2]["metrics"]["pages"] == 12


def test_failure_diagnostics_hash_messages_and_offer_recovery(tmp_path):
    events_path = tmp_path / "events.jsonl"
    report_path = tmp_path / "report.json"
    telemetry = RunTelemetry(
        "index", events_path=events_path, report_path=report_path)
    telemetry.start()
    telemetry.stage_started("index.chroma")
    failure = TimeoutError("PRIVATE_PATH_AND_SOURCE_TEXT")
    telemetry.stage_failed("index.chroma", failure)
    telemetry.finish("failed", exc=failure)

    combined = events_path.read_text(encoding="utf-8") + report_path.read_text(
        encoding="utf-8")
    report = json.loads(report_path.read_text(encoding="utf-8"))
    assert "PRIVATE_PATH_AND_SOURCE_TEXT" not in combined
    assert report["failure"]["category"] == "timeout"
    assert report["failure"]["action"] == (
        "retry_or_raise_operation_timeout")
    assert len(report["failure"]["message_digest"]) == 64
    assert report["stages"]["index.chroma"]["failed"] == 1


def test_context_manager_classifies_cancellation(tmp_path):
    telemetry = RunTelemetry("full", report_path=tmp_path / "report.json")

    with pytest.raises(SystemExit) as raised:
        with telemetry:
            raise SystemExit(130)

    assert raised.value.code == 130
    assert telemetry.report_payload()["status"] == "cancelled"
    assert telemetry.report_payload()["failure"]["category"] == "cancelled"


def test_finish_is_idempotent_and_freezes_elapsed_time(tmp_path):
    monotonic_values = iter([10.0, 12.0, 13.0, 99.0])
    telemetry = RunTelemetry(
        "brief", report_path=tmp_path / "report.json",
        monotonic_clock=lambda: next(monotonic_values))
    telemetry.start()
    first = telemetry.finish()
    second = telemetry.finish()

    assert first == second
    assert second["elapsed_ms"] == 3000.0


def test_concurrent_finish_publishes_one_terminal_event(tmp_path):
    events_path = tmp_path / "events.jsonl"
    telemetry = RunTelemetry("query", events_path=events_path)
    telemetry.start()
    barrier = threading.Barrier(3)
    errors = []

    def finish_run():
        try:
            barrier.wait()
            telemetry.finish()
        except BaseException as exc:  # pragma: no cover - assertion payload
            errors.append(exc)

    threads = [threading.Thread(target=finish_run) for _ in range(2)]
    for thread in threads:
        thread.start()
    barrier.wait()
    for thread in threads:
        thread.join(timeout=5)

    assert errors == []
    assert not any(thread.is_alive() for thread in threads)
    assert [event["status"] for event in _events(events_path)] == [
        "started", "succeeded"]


def test_bare_system_exit_is_a_successful_completion(tmp_path):
    telemetry = RunTelemetry("info", report_path=tmp_path / "report.json")
    telemetry.start()

    report = telemetry.finish_from_exception(SystemExit())

    assert report["status"] == "succeeded"
    assert "failure" not in report


@pytest.mark.parametrize(
    "value", ["", "contains space", "../escape", "foo/bar", "x" * 129])
def test_identifiers_reject_ambiguous_or_path_like_values(value):
    with pytest.raises(ValueError, match="safe identifier"):
        RunTelemetry(value)


def test_metrics_reject_text_and_nonfinite_values():
    telemetry = RunTelemetry("full")
    telemetry.start()
    with pytest.raises(TypeError, match="finite numbers"):
        telemetry.stage_started("convert", metrics={"path": "private.pdf"})
    with pytest.raises(TypeError, match="finite numbers"):
        telemetry.stage_started("convert", metrics={"latency": float("nan")})


def test_run_outputs_must_be_distinct_files(tmp_path):
    output = tmp_path / "telemetry.json"
    with pytest.raises(ValueError, match="distinct files"):
        RunTelemetry(
            "full", events_path=output, report_path=output)


def test_run_outputs_reject_existing_hardlink_aliases(tmp_path):
    events_path = tmp_path / "events.jsonl"
    report_path = tmp_path / "report.json"
    events_path.write_text("", encoding="utf-8")
    os.link(events_path, report_path)

    with pytest.raises(ValueError, match="distinct files"):
        RunTelemetry(
            "full", events_path=events_path, report_path=report_path)


@pytest.mark.skipif(os.name == "nt", reason="Windows chmod does not model ACLs")
def test_persisted_telemetry_requests_owner_only_permissions(tmp_path):
    private = tmp_path / "new" / "nested"
    events_path = private / "events.jsonl"
    report_path = private / "report.json"
    with RunTelemetry(
            "full", events_path=events_path, report_path=report_path):
        pass

    assert stat.S_IMODE(private.stat().st_mode) == 0o700
    assert stat.S_IMODE(events_path.stat().st_mode) == 0o600
    assert stat.S_IMODE(report_path.stat().st_mode) == 0o600


@pytest.mark.parametrize(
    ("exc", "category", "action"),
    [
        (FileNotFoundError("private"), "input_missing", "verify_the_input_path"),
        (PermissionError("private"), "permission_denied",
         "fix_permissions_and_resume"),
        (RuntimeError("private"), "internal_error", "inspect_logs_and_resume"),
    ],
)
def test_failure_category_contract(exc, category, action):
    diagnostic = failure_diagnostic(exc)
    assert diagnostic["category"] == category
    assert diagnostic["action"] == action
    assert "private" not in json.dumps(diagnostic)


def test_supervisor_can_finalize_a_killed_workers_event_stream(tmp_path):
    events_path = tmp_path / "events.jsonl"
    report_path = tmp_path / "report.json"
    worker = RunTelemetry(
        "index", run_id="shared-run", events_path=events_path,
        report_path=report_path)
    worker.start()
    worker.stage_started("index.chroma")

    report = finalize_interrupted_run(
        "index", run_id="shared-run", status="failed",
        exc=TimeoutError("PRIVATE_TIMEOUT_DETAIL"),
        events_path=events_path, report_path=report_path)

    assert report["status"] == "failed"
    assert report["failure"]["category"] == "timeout"
    assert report["event_count"] == 4
    assert report["stages"]["index.chroma"]["failed"] == 1
    assert _events(events_path)[-1]["status"] == "failed"
    assert "PRIVATE_TIMEOUT_DETAIL" not in events_path.read_text(
        encoding="utf-8")


def test_supervisor_does_not_replace_a_committed_success(tmp_path):
    events_path = tmp_path / "events.jsonl"
    report_path = tmp_path / "report.json"
    worker = RunTelemetry(
        "full", run_id="completed-run", events_path=events_path,
        report_path=report_path)
    worker.start()
    expected = worker.finish()

    observed = finalize_interrupted_run(
        "full", run_id="completed-run", status="cancelled",
        exc=KeyboardInterrupt(), events_path=events_path,
        report_path=report_path)

    assert observed == expected
    assert observed["status"] == "succeeded"
    assert len(_events(events_path)) == 2


def test_new_start_replaces_stale_terminal_report_before_recovery(tmp_path):
    events_path = tmp_path / "events.jsonl"
    report_path = tmp_path / "report.json"
    old = RunTelemetry(
        "full", run_id="reused-run", events_path=events_path,
        report_path=report_path)
    old.start()
    old.finish()

    current = RunTelemetry(
        "full", run_id="reused-run", events_path=events_path,
        report_path=report_path)
    current.start()
    assert json.loads(report_path.read_text(encoding="utf-8"))["status"] == (
        "running")

    observed = finalize_interrupted_run(
        "full", run_id="reused-run", status="failed",
        exc=TimeoutError("private"), events_path=events_path,
        report_path=report_path)

    assert observed["status"] == "failed"
    assert _events(events_path)[-1]["status"] == "failed"


def test_recovery_cancels_every_unmatched_stage(tmp_path):
    events_path = tmp_path / "events.jsonl"
    worker = RunTelemetry(
        "batch", run_id="cancelled-run", events_path=events_path)
    worker.start()
    worker.stage_started("batch")
    worker.stage_started("item_1.index")

    report = finalize_interrupted_run(
        "batch", run_id="cancelled-run", status="cancelled",
        exc=KeyboardInterrupt(), events_path=events_path)

    assert report["status"] == "cancelled"
    assert report["stages"]["batch"]["cancelled"] == 1
    assert report["stages"]["item_1.index"]["cancelled"] == 1
    assert [event["status"] for event in _events(events_path)[-3:]] == [
        "cancelled", "cancelled", "cancelled"]


def test_recovery_rejects_unrecognized_event_fields(tmp_path):
    events_path = tmp_path / "events.jsonl"
    worker = RunTelemetry("full", run_id="run-safe", events_path=events_path)
    worker.start()
    payload = _events(events_path)[0]
    payload["private_path"] = "canary"
    events_path.write_text(json.dumps(payload) + "\n", encoding="utf-8")

    with pytest.raises(ValueError, match="unsupported fields"):
        RunTelemetry.recover(
            "full", run_id="run-safe", events_path=events_path)
