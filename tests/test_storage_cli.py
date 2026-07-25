import json
import threading
from pathlib import Path

import pytest

import artifact_io
import rag
import retention
import storage_policy


def _owned_run(output_root, name="book"):
    run_root = output_root / name
    db_root = run_root / f"{name}_chroma"
    retention.ensure_pipeline_run_manifest(
        output_root,
        run_root,
        job_scope=name,
        owned_siblings=[],
        vector_stores=[{
            "backend": "chroma",
            "collection": "book",
            "path": db_root,
        }],
    )
    storage_policy.ensure_private_directory(db_root)
    storage_policy.atomic_write_private_text(db_root / "vectors.bin", "data")
    storage_policy.atomic_write_private_text(
        run_root / "private.txt", "private")
    return run_root


def test_storage_cli_is_dry_run_by_default(tmp_path, capsys):
    output_root = tmp_path / "output"
    run_root = _owned_run(output_root)

    rag.main([
        "--quiet", "storage", "--delete-run", "book",
        "--output-root", str(output_root), "--json",
    ])

    payload = json.loads(capsys.readouterr().out)
    assert payload["mode"] == "dry_run"
    assert payload["apply_required"] is True
    assert payload["candidate_count"] == 1
    assert run_root.is_dir()
    assert not (output_root / retention.QUARANTINE_DIRECTORY_NAME).exists()


def test_storage_cli_apply_deletes_owned_run_under_leases(tmp_path, capsys):
    output_root = tmp_path / "output"
    run_root = _owned_run(output_root)

    rag.main([
        "--quiet", "storage", "--delete-run", "book",
        "--output-root", str(output_root), "--apply", "--json",
    ])

    payload = json.loads(capsys.readouterr().out)
    assert payload["mode"] == "applied"
    assert payload["deleted_count"] == 1
    assert not run_root.exists()
    assert (output_root / ".rag-locks").is_dir()


def test_pipeline_job_lease_identity_survives_a_dotted_run_scope(tmp_path):
    """Both sides must derive the same lease from a scope containing a dot."""
    output_root = tmp_path / "output"

    pipeline = rag._pipeline_job_lock(
        Path("Contracts vol.2.pdf"), output_root=output_root)
    retention_side = rag._pipeline_job_lock(
        scope_name="Contracts vol.2", output_root=output_root)

    assert pipeline.key == retention_side.key
    assert pipeline.lock_path == retention_side.lock_path

    with pytest.raises(ValueError, match="exactly one of"):
        rag._pipeline_job_lock(Path("a.pdf"), scope_name="a")
    with pytest.raises(ValueError, match="exactly one of"):
        rag._pipeline_job_lock()


def test_storage_cli_delete_waits_for_a_running_dotted_pipeline(tmp_path):
    """Deleting a run must contend with the pipeline that owns its scope."""
    output_root = tmp_path / "output"
    run_root = _owned_run(output_root, name="Contracts vol.2")
    entered = threading.Event()
    release = threading.Event()

    def hold_pipeline_lease():
        with rag._pipeline_job_lock(
                Path("Contracts vol.2.pdf"),
                output_root=output_root, timeout=5):
            entered.set()
            release.wait(20)

    holder = threading.Thread(target=hold_pipeline_lease)
    holder.start()
    try:
        assert entered.wait(10)
        with pytest.raises(SystemExit) as raised:
            rag.main([
                "--quiet", "storage", "--delete-run", "Contracts vol.2",
                "--output-root", str(output_root),
                "--db-lock-timeout", "0.05", "--apply",
            ])
        assert raised.value.code == 1
        assert run_root.is_dir()
        assert (run_root / "private.txt").is_file()
    finally:
        release.set()
        holder.join(timeout=20)


def test_storage_cli_refuses_unowned_run_without_traceback(tmp_path):
    output_root = tmp_path / "output"
    (output_root / "unowned").mkdir(parents=True)

    with pytest.raises(SystemExit) as raised:
        rag.main([
            "--quiet", "storage", "--delete-run", "unowned",
            "--output-root", str(output_root), "--apply",
        ])

    assert raised.value.code == 1
    assert (output_root / "unowned").is_dir()


def test_storage_cli_plans_then_prunes_dead_snapshot_scratch(
        tmp_path, capsys):
    scratch_root = artifact_io._snapshot_scratch_root(tmp_path)
    owned = storage_policy.ensure_private_directory(
        scratch_root / "run-owned")
    storage_policy.atomic_write_private_json(
        owned / artifact_io._SNAPSHOT_OWNER_MARKER,
        {
            "schema_version": artifact_io._SNAPSHOT_OWNER_SCHEMA_VERSION,
            "kind": "rag_snapshot_scratch",
            "pid": 2_147_483_647,
            "process_birth": None,
            "created_ns": 1,
            "nonce": owned.name,
        },
    )
    storage_policy.atomic_write_private_text(
        owned / "sensitive.pdf", "private")
    common = [
        "--quiet", "storage", "--prune-snapshot-scratch",
        "--snapshot-scratch-root", str(tmp_path),
        "--older-than-days", "0", "--json",
    ]

    rag.main(common)

    plan = json.loads(capsys.readouterr().out)
    assert plan["mode"] == "dry_run"
    assert plan["apply_required"] is True
    assert plan["candidate_count"] == 1
    assert plan["candidates"][0]["relative_path"] == owned.name
    assert owned.is_dir()

    rag.main([*common, "--apply"])

    result = json.loads(capsys.readouterr().out)
    assert result["mode"] == "applied"
    assert result["deleted_count"] == 1
    assert not owned.exists()


def test_storage_cli_snapshot_dry_run_does_not_create_root(tmp_path, capsys):
    owned_root = artifact_io._snapshot_scratch_root_path(tmp_path)

    rag.main([
        "--quiet", "storage", "--prune-snapshot-scratch",
        "--snapshot-scratch-root", str(tmp_path),
        "--older-than-days", "0", "--json",
    ])

    payload = json.loads(capsys.readouterr().out)
    assert payload["mode"] == "dry_run"
    assert payload["root"] == str(owned_root)
    assert payload["candidate_count"] == 0
    assert not owned_root.exists()
