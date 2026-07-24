from pathlib import Path
from types import SimpleNamespace
import json
import threading
import time

import pytest

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

    monkeypatch.setattr(job_manager, "launch_detached", fake_launch)
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

    monkeypatch.setattr(
        job_manager, "reconcile_all_jobs", fake_reconcile_all)

    rag._run_jobs_command(_args(store.root, "list", job_json=True))

    output = capsys.readouterr().out
    assert observed == [submitted.job_id]
    assert submitted.job_id in output
    assert str(private_pdf) not in output
    assert "--pdf" not in output


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
        lambda args: observed.update(
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
