from dataclasses import replace
from pathlib import Path
from types import SimpleNamespace
import json
import threading
import time

import pytest

import job_application
import job_manager
import job_runtime
import rag
import retention


def _args(root: Path, action: str, **overrides):
    values = {
        "job_root": root,
        "job_action": action,
        "job_json": False,
    }
    values.update(overrides)
    return SimpleNamespace(**values)


def _install_job_binding(monkeypatch, **overrides):
    binding = replace(
        job_application.default_job_application_binding(), **overrides)
    monkeypatch.setattr(
        rag, "_default_job_application_binding", lambda: binding)
    return binding


def test_background_submit_tokens_require_fresh_allowed_command():
    assert rag._background_submit_tokens([
        "--", "index", "--chunks", "Book.jsonl",
    ]) == ("index", ["--chunks", "Book.jsonl"])

    with pytest.raises(job_runtime.JobValidationError, match="requires"):
        rag._background_submit_tokens([])
    with pytest.raises(job_runtime.JobValidationError, match="cannot infer"):
        rag._background_submit_tokens([
            "full", "--pdf", "Book.pdf", "--resume",
        ])


def test_background_policy_is_canonical_opaque_and_stops_at_terminator():
    result = rag._canonical_background_security_argv(
        "index",
        [
            "--chunks", "Book.jsonl",
            "--security-profile=development",
            "--network-policy", "allow-cloud",
            "--llm-cache-namespace", "private-tenant",
            "--trust-environment-network",
            "--", "--security-profile", "literal-positional",
        ],
    )

    assert "private-tenant" not in result
    assert result[-3:] == [
        "--", "--security-profile", "literal-positional"]
    assert result[result.index("--security-profile") + 1] == "development"
    namespace_index = result.index("--release-cache-namespace-id")
    assert result[namespace_index + 1].startswith("v1:sha256:")
    assert "--release-security-policy-version" in result


def test_jobs_submit_persists_private_spec_and_prints_redacted_json(
        monkeypatch, tmp_path, capsys):
    private_pdf = tmp_path / "Private Casebook.pdf"
    observed = {}

    def fake_launch(store, job_id, **kwargs):
        observed.update(store=store, job_id=job_id, kwargs=kwargs)
        return SimpleNamespace(as_dict=lambda: {
            "job_id": job_id, "status": "starting", "ready": True,
        })

    _install_job_binding(monkeypatch, launch_detached=fake_launch)
    metrics = rag._run_jobs_command(_args(
        tmp_path / "jobs", "submit", job_json=True,
        job_command=["--", "full", "--pdf", str(private_pdf)],
        timeout=90, ready_timeout=2,
    ))

    output = capsys.readouterr().out
    payload = json.loads(output)
    assert payload["job"]["command"] == "full"
    assert payload["launch"]["ready"] is True
    assert str(private_pdf) not in output
    assert "--pdf" not in output
    execution = observed["store"].load_execution(observed["job_id"])
    assert execution.argv == (
        "--pdf", str(private_pdf),
        "--release-security-policy-version", "1",
        "--security-profile", "release",
        "--network-policy", "local-only",
        "--model-download-policy", "cache-only",
    )
    assert execution.timeout_seconds == 90
    assert observed["kwargs"] == {"ready_timeout": 2}
    assert metrics == {"jobs": 1, "terminal": 0, "applied": True}


def test_jobs_list_reconciles_each_job_without_exposing_arguments(
        monkeypatch, tmp_path, capsys):
    store = job_runtime.JobStore(tmp_path / "jobs")
    private_pdf = tmp_path / "Private.pdf"
    submitted = store.submit_job("full", ["--pdf", str(private_pdf)])
    observed = []

    def fake_reconcile_all(selected_store):
        summaries = selected_store.list_jobs()
        observed.extend(summary.job_id for summary in summaries)
        return summaries

    _install_job_binding(
        monkeypatch, reconcile_all_jobs=fake_reconcile_all)

    rag._run_jobs_command(_args(store.root, "list", job_json=True))

    output = capsys.readouterr().out
    assert observed == [submitted.job_id]
    assert submitted.job_id in output
    assert str(private_pdf) not in output
    assert "--pdf" not in output


def test_jobs_command_snapshots_one_complete_binding_generation(
        monkeypatch, tmp_path, capsys):
    store = job_runtime.JobStore(tmp_path / "jobs")
    submitted = store.submit_job("export", ["--chunks", "Private.jsonl"])
    events = []

    def unexpected(*_args, **_kwargs):
        pytest.fail("a later job-application generation was mixed in")

    second = replace(
        job_application.default_job_application_binding(),
        reconcile_all_jobs=unexpected,
    )

    def first_reconcile_all(selected_store):
        events.append(("first", selected_store.root))
        monkeypatch.setattr(
            rag, "_default_job_application_binding", lambda: second)
        return selected_store.list_jobs()

    first = replace(
        job_application.default_job_application_binding(),
        reconcile_all_jobs=first_reconcile_all,
    )
    resolutions = []
    monkeypatch.setattr(
        rag, "_default_job_application_binding",
        lambda: resolutions.append(first) or first,
    )

    rag._run_jobs_command(_args(store.root, "list", job_json=True))

    assert resolutions == [first]
    assert events == [("first", store.root)]
    assert submitted.job_id in capsys.readouterr().out


def test_jobs_status_reconciles_exact_job_and_prints_only_redacted_summary(
        monkeypatch, tmp_path, capsys):
    store = job_runtime.JobStore(tmp_path / "jobs")
    private_chunks = tmp_path / "Private chunks.jsonl"
    submitted = store.submit_job(
        "export", ["--chunks", str(private_chunks)])
    observed = []

    def fake_reconcile(selected_store, job_id):
        observed.append((selected_store, job_id))
        return selected_store.get_job(job_id)

    _install_job_binding(monkeypatch, reconcile_job=fake_reconcile)

    metrics = rag._run_jobs_command(_args(
        store.root, "status", job_json=True,
        job_id=submitted.job_id,
    ))

    output = capsys.readouterr().out
    assert json.loads(output) == submitted.as_dict()
    assert observed == [(observed[0][0], submitted.job_id)]
    assert observed[0][0].root == store.root
    assert str(private_chunks) not in output
    assert "--chunks" not in output
    assert metrics == {"jobs": 1, "terminal": 0, "applied": False}


def test_jobs_list_holds_root_lease_across_reconciliation(
        monkeypatch, tmp_path, capsys):
    store = job_runtime.JobStore(tmp_path / "jobs")
    submitted = store.submit_job("export", [])
    execution = store.load_execution(submitted.job_id)
    store.transition_job(
        submitted.job_id, "failed",
        attempt_token=execution.attempt_token,
        expected_revision=execution.revision)
    store.prepare_delete(submitted.job_id)
    plan = retention.plan_background_job_deletion(
        store.root, submitted.job_id)
    deletion_done = threading.Event()
    outcomes = {}
    deletion_thread = None

    def delete_job():
        try:
            outcomes["deleted"] = retention.apply_retention_plan(plan)
        except BaseException as exc:
            outcomes["delete_error"] = exc
        finally:
            deletion_done.set()

    real_list_jobs = job_runtime.JobStore.list_jobs
    scheduled = False

    def list_while_deletion_waits(selected_store, *, lease=None):
        nonlocal deletion_thread, scheduled
        summaries = real_list_jobs(selected_store, lease=lease)
        if not scheduled:
            scheduled = True
            deletion_thread = threading.Thread(target=delete_job)
            deletion_thread.start()
            assert not deletion_done.wait(0.2)
        return summaries

    monkeypatch.setattr(
        job_runtime.JobStore, "list_jobs", list_while_deletion_waits)

    metrics = rag._run_jobs_command(
        _args(store.root, "list", job_json=True))
    assert deletion_thread is not None
    deletion_thread.join(timeout=5)

    output = capsys.readouterr().out
    assert not deletion_thread.is_alive()
    assert "delete_error" not in outcomes
    assert outcomes["deleted"]["deleted_count"] == 1
    assert submitted.job_id in output
    assert metrics == {"jobs": 1, "terminal": 1, "applied": False}


def test_main_parses_jobs_submit_remainder_without_normal_supervision(
        monkeypatch, tmp_path):
    observed = {}
    monkeypatch.setattr(
        rag, "_run_jobs_command",
        lambda args, *, job_binding: observed.update(
            action=args.job_action,
            root=args.job_root,
            command=args.job_command,
        ) or {"jobs": 1, "terminal": 0, "applied": True},
    )

    rag.main([
        "jobs", "submit", "--job-root", str(tmp_path / "jobs"),
        "--", "full", "--pdf", "Book.pdf",
    ])

    assert observed == {
        "action": "submit",
        "root": tmp_path / "jobs",
        "command": ["--", "full", "--pdf", "Book.pdf"],
    }


def test_main_uses_same_binding_generation_for_manager_error_mapping(
        monkeypatch, tmp_path):
    class FirstManagerError(job_manager.JobManagerError):
        pass

    class SecondManagerError(job_manager.JobManagerError):
        pass

    first = replace(
        job_application.default_job_application_binding(),
        manager_error_type=FirstManagerError,
    )
    second = replace(first, manager_error_type=SecondManagerError)
    resolutions = []
    observed = []
    monkeypatch.setattr(
        rag, "_default_job_application_binding",
        lambda: resolutions.append(first) or first,
    )

    def fail_command(_args, *, job_binding):
        observed.append(job_binding)
        monkeypatch.setattr(
            rag, "_default_job_application_binding", lambda: second)
        raise FirstManagerError("safe manager failure")

    monkeypatch.setattr(rag, "_run_jobs_command", fail_command)

    with pytest.raises(SystemExit) as raised:
        rag.main([
            "jobs", "list", "--job-root", str(tmp_path / "jobs"),
        ])

    assert raised.value.code == 1
    assert resolutions == [first]
    assert observed == [first]


def test_jobs_submit_runs_real_detached_export_to_terminal_success(
        tmp_path, capsys):
    chunks = tmp_path / "Book_chunks.jsonl"
    output = tmp_path / "Book.md"
    chunks.write_text(json.dumps({
        "text": "A private legal rule.",
        "metadata": {
            "chunk_index": 0,
            "content_type": "author_narrative",
            "chapter_num": 1,
            "chapter_title": "One",
            "section_path": "Chapter 1 > Rule",
        },
    }) + "\n", encoding="utf-8")
    job_root = tmp_path / "jobs"

    rag._run_jobs_command(_args(
        job_root, "submit", job_json=True,
        job_command=[
            "--", "export", "--chunks", str(chunks),
            "--out", str(output),
        ],
        timeout=30, ready_timeout=10,
    ))
    submitted = json.loads(capsys.readouterr().out)
    job_id = submitted["job"]["job_id"]
    store = job_runtime.JobStore(job_root)
    deadline = time.monotonic() + 15
    summary = store.get_job(job_id)
    while not summary.terminal and time.monotonic() < deadline:
        time.sleep(0.05)
        summary = store.get_job(job_id)

    assert summary.status == "succeeded"
    assert output.is_file()
    assert "A private legal rule." in output.read_text(encoding="utf-8")


def test_jobs_delete_is_dry_run_first_and_removes_only_terminal_job(
        tmp_path, capsys):
    store = job_runtime.JobStore(tmp_path / "jobs")
    submitted = store.submit_job("export", ["--chunks", "Private.jsonl"])
    execution = store.load_execution(submitted.job_id)
    store.transition_job(
        submitted.job_id, "failed",
        attempt_token=execution.attempt_token,
        expected_revision=execution.revision)

    dry_metrics = rag._run_jobs_command(_args(
        store.root, "delete", job_json=True,
        job_id=submitted.job_id, apply=False))
    dry_run = json.loads(capsys.readouterr().out)

    assert dry_run["mode"] == "dry_run"
    assert dry_run["candidate_count"] == 1
    assert store.get_job(submitted.job_id).status == "failed"
    assert dry_metrics == {"jobs": 1, "terminal": 1, "applied": False}

    applied_metrics = rag._run_jobs_command(_args(
        store.root, "delete", job_json=True,
        job_id=submitted.job_id, apply=True))
    applied = json.loads(capsys.readouterr().out)

    assert applied["mode"] == "applied"
    assert applied["deleted_count"] == 1
    assert store.list_jobs() == []
    assert applied_metrics == {"jobs": 1, "terminal": 1, "applied": True}


def test_jobs_cancel_terminalizes_an_unstarted_queued_job(tmp_path, capsys):
    store = job_runtime.JobStore(tmp_path / "jobs")
    submitted = store.submit_job("export", ["--chunks", "Private.jsonl"])

    metrics = rag._run_jobs_command(_args(
        store.root, "cancel", job_json=True,
        job_id=submitted.job_id, wait=False, wait_timeout=1))
    payload = json.loads(capsys.readouterr().out)

    assert payload["status"] == "cancelled"
    assert store.get_job(submitted.job_id).status == "cancelled"
    assert metrics == {"jobs": 1, "terminal": 1, "applied": True}


def test_jobs_cancel_wait_requests_first_and_reconciles_until_terminal(
        monkeypatch, tmp_path, capsys):
    store = job_runtime.JobStore(tmp_path / "jobs")
    private_chunks = tmp_path / "Private chunks.jsonl"
    submitted = store.submit_job(
        "export", ["--chunks", str(private_chunks)])
    execution = store.load_execution(submitted.job_id)
    starting = store.transition_job(
        submitted.job_id, "starting",
        attempt_token=execution.attempt_token,
        expected_revision=execution.revision)
    running = store.transition_job(
        submitted.job_id, "running",
        attempt_token=execution.attempt_token,
        expected_revision=starting.revision)
    events = []
    reconcile_statuses = [
        ("cancel_requested", False),
        ("cancel_requested", False),
        ("cancelled", True),
    ]
    real_request_cancel = job_runtime.JobStore.request_cancel

    def request_cancel(selected_store, job_id, **kwargs):
        events.append(("request_cancel", selected_store, job_id, kwargs))
        return real_request_cancel(selected_store, job_id, **kwargs)

    def reconcile(selected_store, job_id):
        events.append(("reconcile", selected_store, job_id))
        status, expected_terminal = reconcile_statuses.pop(0)
        summary = job_runtime.JobSummary(
            job_id=job_id,
            command=running.command,
            status=status,
            attempt_number=running.attempt_number,
            revision=running.revision,
            created_at=running.created_at,
            updated_at=running.updated_at,
        )
        assert summary.terminal is expected_terminal
        return summary

    monkeypatch.setattr(
        job_runtime.JobStore, "request_cancel", request_cancel)
    _install_job_binding(monkeypatch, reconcile_job=reconcile)
    monkeypatch.setattr(
        rag.time, "sleep",
        lambda seconds: events.append(("sleep", seconds)),
    )

    metrics = rag._run_jobs_command(_args(
        store.root, "cancel", job_json=True,
        job_id=submitted.job_id, wait=True, wait_timeout=30,
    ))

    output = capsys.readouterr().out
    payload = json.loads(output)
    selected_store = events[0][1]
    assert events == [
        ("request_cancel", selected_store, submitted.job_id, {}),
        ("reconcile", selected_store, submitted.job_id),
        ("reconcile", selected_store, submitted.job_id),
        ("sleep", 0.1),
        ("reconcile", selected_store, submitted.job_id),
    ]
    assert reconcile_statuses == []
    assert selected_store.root == store.root
    assert payload["status"] == "cancelled"
    assert str(private_chunks) not in output
    assert "--chunks" not in output
    assert metrics == {"jobs": 1, "terminal": 1, "applied": True}


def test_jobs_resume_reconciles_revision_before_launch_with_exact_timeout(
        monkeypatch, tmp_path, capsys):
    store = job_runtime.JobStore(tmp_path / "jobs")
    private_chunks = tmp_path / "Private chunks.jsonl"
    submitted = store.submit_job(
        "export", ["--chunks", str(private_chunks)])
    execution = store.load_execution(submitted.job_id)
    starting = store.transition_job(
        submitted.job_id, "starting",
        attempt_token=execution.attempt_token,
        expected_revision=execution.revision)
    running = store.transition_job(
        submitted.job_id, "running",
        attempt_token=execution.attempt_token,
        expected_revision=starting.revision)
    failed = store.transition_job(
        submitted.job_id, "failed",
        attempt_token=execution.attempt_token,
        expected_revision=running.revision)
    events = []
    real_prepare_resume = job_runtime.JobStore.prepare_resume

    def reconcile(selected_store, job_id):
        events.append(("reconcile", selected_store, job_id))
        return selected_store.get_job(job_id)

    def prepare_resume(selected_store, job_id, **kwargs):
        events.append(("prepare_resume", selected_store, job_id, kwargs))
        return real_prepare_resume(selected_store, job_id, **kwargs)

    def launch(selected_store, job_id, *, ready_timeout):
        events.append((
            "launch", selected_store, job_id,
            {"ready_timeout": ready_timeout},
        ))
        resumed_execution = selected_store.load_execution(job_id)
        summary = selected_store.transition_job(
            job_id, "starting",
            attempt_token=resumed_execution.attempt_token,
            expected_revision=resumed_execution.revision)
        return job_manager.LaunchResult(
            job_id=job_id,
            status=summary.status,
            attempt_number=summary.attempt_number,
            ready=True,
        )

    monkeypatch.setattr(
        job_runtime.JobStore, "prepare_resume", prepare_resume)
    _install_job_binding(
        monkeypatch, reconcile_job=reconcile, launch_detached=launch)

    metrics = rag._run_jobs_command(_args(
        store.root, "resume", job_json=True,
        job_id=submitted.job_id, ready_timeout=4.25,
    ))

    output = capsys.readouterr().out
    payload = json.loads(output)
    selected_store = events[0][1]
    assert events == [
        ("reconcile", selected_store, submitted.job_id),
        (
            "prepare_resume", selected_store, submitted.job_id,
            {"expected_revision": failed.revision},
        ),
        (
            "launch", selected_store, submitted.job_id,
            {"ready_timeout": 4.25},
        ),
    ]
    assert selected_store.root == store.root
    assert payload["resumed_from_revision"] == failed.revision
    assert payload["job"]["status"] == "starting"
    assert payload["job"]["attempt_number"] == 2
    assert payload["launch"]["ready"] is True
    assert str(private_chunks) not in output
    assert "--chunks" not in output
    assert metrics == {"jobs": 1, "terminal": 0, "applied": True}


def test_jobs_submit_launch_baseexception_rolls_back_and_reraises_original(
        monkeypatch, tmp_path, capsys):
    class InjectedLaunchError(BaseException):
        pass

    private_pdf = tmp_path / "Private Casebook.pdf"
    primary = InjectedLaunchError("injected primary launch failure")
    observed = {}

    def launch(selected_store, job_id, *, ready_timeout):
        observed.update(
            store=selected_store,
            job_id=job_id,
            ready_timeout=ready_timeout,
        )
        raise primary

    _install_job_binding(monkeypatch, launch_detached=launch)

    with pytest.raises(InjectedLaunchError) as raised:
        rag._run_jobs_command(_args(
            tmp_path / "jobs", "submit", job_json=True,
            job_command=["--", "full", "--pdf", str(private_pdf)],
            timeout=90, ready_timeout=2.5,
        ))

    assert raised.value is primary
    assert observed["ready_timeout"] == 2.5
    assert observed["store"].get_job(observed["job_id"]).status == "failed"
    output = capsys.readouterr().out
    assert output == ""
    assert str(private_pdf) not in output


def test_jobs_resume_launch_baseexception_fails_new_attempt_and_reraises(
        monkeypatch, tmp_path, capsys):
    class InjectedLaunchError(BaseException):
        pass

    store = job_runtime.JobStore(tmp_path / "jobs")
    private_chunks = tmp_path / "Private chunks.jsonl"
    submitted = store.submit_job(
        "export", ["--chunks", str(private_chunks)])
    execution = store.load_execution(submitted.job_id)
    starting = store.transition_job(
        submitted.job_id, "starting",
        attempt_token=execution.attempt_token,
        expected_revision=execution.revision)
    running = store.transition_job(
        submitted.job_id, "running",
        attempt_token=execution.attempt_token,
        expected_revision=starting.revision)
    store.transition_job(
        submitted.job_id, "failed",
        attempt_token=execution.attempt_token,
        expected_revision=running.revision)
    primary = InjectedLaunchError("injected resume launch failure")

    _install_job_binding(
        monkeypatch,
        reconcile_job=(
            lambda selected_store, job_id: selected_store.get_job(job_id)),
        launch_detached=(
            lambda *_args, **_kwargs: (_ for _ in ()).throw(primary)),
    )

    with pytest.raises(InjectedLaunchError) as raised:
        rag._run_jobs_command(_args(
            store.root, "resume", job_json=True,
            job_id=submitted.job_id, ready_timeout=3,
        ))

    assert raised.value is primary
    current = store.get_job(submitted.job_id)
    assert current.status == "failed"
    assert current.attempt_number == 2
    output = capsys.readouterr().out
    assert output == ""
    assert str(private_chunks) not in output


def test_jobs_launch_rollback_failure_never_replaces_primary_baseexception(
        monkeypatch, tmp_path, capsys):
    class InjectedLaunchError(BaseException):
        pass

    class InjectedRollbackError(BaseException):
        pass

    primary = InjectedLaunchError("injected primary launch failure")
    rollback = InjectedRollbackError("injected rollback failure")
    cleanup = {}

    _install_job_binding(
        monkeypatch,
        launch_detached=(
            lambda *_args, **_kwargs: (_ for _ in ()).throw(primary)),
    )
    monkeypatch.setattr(
        job_runtime.JobStore, "transition_job",
        lambda *_args, **_kwargs: (_ for _ in ()).throw(rollback),
    )
    monkeypatch.setattr(
        rag, "_log_cleanup_error",
        lambda message, *args, error: cleanup.update(
            message=message, args=args, error=error),
    )

    with pytest.raises(InjectedLaunchError) as raised:
        rag._run_jobs_command(_args(
            tmp_path / "jobs", "submit", job_json=True,
            job_command=["--", "export", "--chunks", "Private.jsonl"],
            timeout=90, ready_timeout=2,
        ))

    assert raised.value is primary
    assert cleanup["error"] is rollback
    assert cleanup["args"] == ()
    assert cleanup["message"] == (
        "Background launch-state update failed while preserving the launch "
        "error")
    jobs = job_runtime.JobStore(tmp_path / "jobs").list_jobs()
    assert len(jobs) == 1
    assert jobs[0].status == "queued"
    assert capsys.readouterr().out == ""
