import json
import os
import stat

import pytest

import operational_drills
import storage_policy
from tools import run_operational_drills as drill_cli


def _successful_report():
    return operational_drills.OperationalDrillReport(
        run_id="a" * 32,
        status="succeeded",
        started_at=10.0,
        finished_at=11.0,
        duration_ms=1000.0,
        hard_kill=operational_drills.HardKillResult(
            passed=True,
            worker_ready=True,
            cleanup_confirmed=True,
            child_exit_nonzero=True,
            terminal_status="failed",
            source_event_count=2,
            active_stages_closed=1,
            kill_confirmation_ms=2.0,
            telemetry_recovery_ms=3.0,
        ),
        synced_publication=operational_drills.SyncedPublicationResult(
            passed=True,
            transient_failures_requested=2,
            replace_attempts=3,
            writer_calls=1,
            target_verified=True,
            staging_clean=True,
            publication_ms=150.0,
        ),
    )


def _assert_private(path, *, directory):
    if os.name == "nt":
        assert storage_policy.windows_path_is_private(
            path, directory=directory)
    else:
        expected = 0o700 if directory else 0o600
        assert stat.S_IMODE(path.stat().st_mode) == expected


def test_real_operational_drills_publish_strict_private_evidence(tmp_path):
    output_dir = tmp_path / "drill"

    report = operational_drills.run_operational_drills(output_dir)

    assert report.status == "succeeded"
    assert report.hard_kill.passed is True
    assert report.hard_kill.cleanup_confirmed is True
    assert report.hard_kill.source_event_count == 2
    assert report.hard_kill.active_stages_closed == 1
    assert report.synced_publication.passed is True
    assert report.synced_publication.replace_attempts == 3
    assert report.synced_publication.writer_calls == 1
    report_path = output_dir / operational_drills.REPORT_NAME
    assert operational_drills.load_report(report_path) == report
    assert not list(output_dir.glob(".*.tmp"))
    combined = "".join(
        path.read_text(encoding="utf-8")
        for path in output_dir.iterdir() if path.is_file())
    assert "OPERATIONAL_DRILL_PRIVATE_CANARY" not in combined
    assert "process" not in json.dumps(report.to_payload()).casefold()
    assert "path" not in json.dumps(report.to_payload()).casefold()
    _assert_private(output_dir, directory=True)
    for path in output_dir.iterdir():
        _assert_private(path, directory=False)

    with pytest.raises(ValueError, match="must be empty"):
        operational_drills.run_operational_drills(output_dir)


def test_internal_drill_exception_is_redacted_and_other_drill_continues(
        monkeypatch, tmp_path):
    canary = "C:/private/ethics.pdf API_KEY=super-secret"

    def fail_hard_kill(*args, **kwargs):
        raise RuntimeError(canary)

    monkeypatch.setattr(
        operational_drills, "_run_hard_kill_drill", fail_hard_kill)
    output_dir = tmp_path / "failed-drill"

    report = operational_drills.run_operational_drills(output_dir)

    assert report.status == "failed"
    assert report.hard_kill == operational_drills._empty_hard_kill_result()
    assert report.synced_publication.passed is True
    combined = "".join(
        path.read_text(encoding="utf-8")
        for path in output_dir.iterdir() if path.is_file())
    assert canary not in combined
    assert "super-secret" not in combined
    assert operational_drills.load_report(
        output_dir / operational_drills.REPORT_NAME) == report


def test_output_directory_rejects_symlink_aliases(tmp_path):
    real = tmp_path / "real"
    real.mkdir()
    alias = tmp_path / "alias"
    try:
        alias.symlink_to(real, target_is_directory=True)
    except (OSError, NotImplementedError):
        pytest.skip("directory symlinks require unavailable privileges")

    with pytest.raises(storage_policy.StoragePolicyError, match="links"):
        operational_drills.run_operational_drills(alias)

    assert list(real.iterdir()) == []


@pytest.mark.parametrize(
    "mutate",
    [
        lambda payload: payload.update({"private_path": "C:/private"}),
        lambda payload: payload.update({"schema_version": True}),
        lambda payload: payload["hard_kill"].update({"passed": 1}),
        lambda payload: payload["hard_kill"].update({
            "kill_confirmation_ms": float("inf")}),
        lambda payload: payload["hard_kill"].update({
            "source_event_count": 999}),
        lambda payload: payload["hard_kill"].update({
            "active_stages_closed": 999}),
        lambda payload: payload["synced_publication"].update({
            "replace_attempts": 2}),
        lambda payload: payload.update({"status": "failed"}),
    ],
)
def test_parser_rejects_unknown_ambiguous_or_inconsistent_values(mutate):
    payload = _successful_report().to_payload()
    mutate(payload)

    with pytest.raises((TypeError, ValueError)):
        operational_drills.parse_report(payload)


def test_loader_rejects_duplicate_nonstandard_and_oversize_json(tmp_path):
    duplicate = tmp_path / "duplicate.json"
    duplicate.write_text(
        '{"schema_version":1,"schema_version":1}', encoding="utf-8")
    with pytest.raises(ValueError, match="duplicate"):
        operational_drills.load_report(duplicate)

    nonstandard = tmp_path / "nonstandard.json"
    nonstandard.write_text('{"duration_ms":Infinity}', encoding="utf-8")
    with pytest.raises(ValueError, match="non-standard"):
        operational_drills.load_report(nonstandard)

    oversize = tmp_path / "oversize.json"
    oversize.write_bytes(b" " * (operational_drills.MAX_REPORT_BYTES + 1))
    with pytest.raises(ValueError, match="size limit"):
        operational_drills.load_report(oversize)


def test_publish_round_trips_and_rejects_subclasses(tmp_path):
    report = _successful_report()
    path = tmp_path / "report.json"

    operational_drills.publish(path, report)

    assert operational_drills.load_report(path) == report

    class ReportSubclass(operational_drills.OperationalDrillReport):
        pass

    subclass = ReportSubclass(
        run_id=report.run_id, status=report.status,
        started_at=report.started_at, finished_at=report.finished_at,
        duration_ms=report.duration_ms, hard_kill=report.hard_kill,
        synced_publication=report.synced_publication)
    with pytest.raises(TypeError, match="exact type"):
        operational_drills.publish(path, subclass)


def test_nested_result_subclasses_cannot_expand_the_report_schema(tmp_path):
    report = _successful_report()

    class MaliciousHardKill(operational_drills.HardKillResult):
        def to_payload(self):
            payload = super().to_payload()
            payload["private_path"] = "C:/secret/ethics.pdf"
            return payload

    malicious = MaliciousHardKill(
        **report.hard_kill.to_payload())
    with pytest.raises(TypeError, match="HardKillResult"):
        operational_drills.OperationalDrillReport(
            run_id=report.run_id, status=report.status,
            started_at=report.started_at, finished_at=report.finished_at,
            duration_ms=report.duration_ms, hard_kill=malicious,
            synced_publication=report.synced_publication)

    object.__setattr__(report, "hard_kill", malicious)
    with pytest.raises(ValueError, match="fields"):
        operational_drills.publish(tmp_path / "malicious.json", report)
    assert not (tmp_path / "malicious.json").exists()
    object.__setattr__(report, "hard_kill", _successful_report().hard_kill)

    class MaliciousSyncedPublication(
            operational_drills.SyncedPublicationResult):
        def to_payload(self):
            payload = super().to_payload()
            payload["private_path"] = "C:/secret/ethics.pdf"
            return payload

    synced = MaliciousSyncedPublication(
        **report.synced_publication.to_payload())
    with pytest.raises(TypeError, match="SyncedPublicationResult"):
        operational_drills.OperationalDrillReport(
            run_id=report.run_id, status=report.status,
            started_at=report.started_at, finished_at=report.finished_at,
            duration_ms=report.duration_ms, hard_kill=report.hard_kill,
            synced_publication=synced)

    object.__setattr__(report, "synced_publication", synced)
    with pytest.raises(ValueError, match="fields"):
        operational_drills.publish(tmp_path / "malicious-synced.json", report)
    assert not (tmp_path / "malicious-synced.json").exists()


@pytest.mark.parametrize("value", [True, 0, 0.09, 61, float("inf")])
def test_run_rejects_invalid_timeouts_before_creating_output(tmp_path, value):
    output_dir = tmp_path / "never-created"
    with pytest.raises(ValueError, match="timeout"):
        operational_drills.run_operational_drills(
            output_dir, ready_timeout=value)
    assert not output_dir.exists()


def test_cli_prints_only_the_redacted_report(monkeypatch, capsys, tmp_path):
    report = _successful_report()
    monkeypatch.setattr(
        operational_drills, "run_operational_drills",
        lambda *args, **kwargs: report)

    status = drill_cli.main([
        "--output-dir", str(tmp_path / "C_PRIVATE_CANARY")])

    captured = capsys.readouterr()
    assert status == 0
    assert json.loads(captured.out) == report.to_payload()
    assert "C_PRIVATE_CANARY" not in captured.out
    assert captured.err == ""


def test_cli_redacts_startup_errors(monkeypatch, capsys, tmp_path):
    def fail(*args, **kwargs):
        raise RuntimeError("C:/private/secret")

    monkeypatch.setattr(operational_drills, "run_operational_drills", fail)

    status = drill_cli.main([
        "--output-dir", str(tmp_path / "private-canary")])

    captured = capsys.readouterr()
    assert status == 2
    assert "private-canary" not in captured.err
    assert "secret" not in captured.err


def test_hard_kill_child_environment_excludes_provider_secrets(monkeypatch):
    monkeypatch.setenv("PATH", "safe-runtime-path")
    monkeypatch.setenv("GEMINI_API_KEY", "provider-secret")
    monkeypatch.setenv("DEEPSEEK_API_KEY", "provider-secret")

    environment = operational_drills._child_environment()

    assert environment["PATH"] == "safe-runtime-path"
    assert "GEMINI_API_KEY" not in environment
    assert "DEEPSEEK_API_KEY" not in environment
    assert "provider-secret" not in environment.values()


def test_failed_process_tree_confirmation_cannot_be_upgraded_on_retry(
        monkeypatch, tmp_path):
    class FakeGate:
        kind = "test-gate"
        child_value = "1"

        def popen_options(self):
            return {}

        def release(self):
            pass

        def close(self):
            pass

    class FakeJob:
        def assign(self, process):
            pass

        def close(self):
            return True

    class FakeProcess:
        returncode = 124

        def poll(self):
            return self.returncode

    calls = []

    monkeypatch.setattr(
        operational_drills.process_supervision,
        "_new_supervised_start_gate", FakeGate)
    monkeypatch.setattr(
        operational_drills.process_supervision, "_WindowsKillJob", FakeJob)
    monkeypatch.setattr(
        operational_drills.subprocess, "Popen",
        lambda *args, **kwargs: FakeProcess())
    monkeypatch.setattr(
        operational_drills, "_wait_for_ready",
        lambda *args, **kwargs: True)

    def fail_then_claim_success(*args, **kwargs):
        calls.append((args, kwargs))
        return len(calls) > 1

    monkeypatch.setattr(
        operational_drills.process_supervision,
        "_terminate_supervised_process", fail_then_claim_success)

    with pytest.raises(RuntimeError, match="cleanup was unconfirmed"):
        operational_drills._run_hard_kill_drill(
            tmp_path, "b" * 32, ready_timeout=1, kill_timeout=1)

    assert len(calls) == 1
