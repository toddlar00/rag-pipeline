from pathlib import Path
from types import SimpleNamespace
import json

import pytest

import job_manager
import job_runtime
import rag


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
    assert execution.argv == ("--pdf", str(private_pdf))
    assert execution.timeout_seconds == 90
    assert observed["kwargs"] == {"ready_timeout": 2}
    assert metrics == {"jobs": 1, "terminal": 0, "applied": True}


def test_jobs_list_reconciles_each_job_without_exposing_arguments(
        monkeypatch, tmp_path, capsys):
    store = job_runtime.JobStore(tmp_path / "jobs")
    private_pdf = tmp_path / "Private.pdf"
    submitted = store.submit_job("full", ["--pdf", str(private_pdf)])
    observed = []
    monkeypatch.setattr(
        job_manager, "reconcile_job",
        lambda _store, job_id: observed.append(job_id) or _store.get_job(job_id),
    )

    rag._run_jobs_command(_args(store.root, "list", job_json=True))

    output = capsys.readouterr().out
    assert observed == [submitted.job_id]
    assert submitted.job_id in output
    assert str(private_pdf) not in output
    assert "--pdf" not in output


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
