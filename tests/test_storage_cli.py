import json

import pytest

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
