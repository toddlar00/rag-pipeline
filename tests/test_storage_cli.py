import json

import pytest

import artifact_io
import rag
import retention
import storage_policy


def _owned_run(output_root):
    run_root = output_root / "book"
    db_root = run_root / "book_chroma"
    retention.ensure_pipeline_run_manifest(
        output_root,
        run_root,
        job_scope="book",
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
