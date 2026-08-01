import json
from contextlib import contextmanager
from pathlib import Path

import rag


def _complete_manifest(run_name: str) -> dict:
    return {
        "state": "complete",
        "run_name": run_name,
        "job_scope": run_name,
        "ownership_token": "a" * 32,
    }


def _logical_run_marker(output_dir: Path, run_name: str, *,
                        state: str = "complete") -> Path:
    run_root = output_dir / run_name
    run_root.mkdir(parents=True, exist_ok=True)
    marker = run_root / ".rag-run.json"
    marker.write_text(json.dumps({"state": state}), encoding="utf-8")
    return run_root


def test_show_info_preserves_legacy_chroma_dir_keyword(
        monkeypatch, tmp_path, capsys):
    output_dir = tmp_path / "output"
    output_dir.mkdir()
    monkeypatch.setattr(rag, "OUTPUT_DIR", output_dir)

    rag.show_info(chroma_dir=tmp_path / "missing-db")

    assert "Pipeline Output Status" in capsys.readouterr().out


def test_show_info_discovers_book_scoped_artifacts(monkeypatch, tmp_path, capsys):
    output_dir = tmp_path / "output"
    book_dir = output_dir / "Evidence"
    chapters_dir = book_dir / "Chapters"
    chapters_dir.mkdir(parents=True)
    (book_dir / "Evidence.json").write_text("{}", encoding="utf-8")
    (book_dir / "Evidence_chunks.jsonl").write_text(
        '{"text":"rule","metadata":{"content_type":"author_narrative"}}\n',
        encoding="utf-8",
    )
    (book_dir / "Evidence.md").write_text("# Evidence", encoding="utf-8")
    (chapters_dir / "ch01.md").write_text("# One", encoding="utf-8")
    monkeypatch.setattr(rag, "OUTPUT_DIR", output_dir)

    rag.show_info(tmp_path / "missing-db")

    output = capsys.readouterr().out
    assert str(Path("Evidence") / "Evidence.json") in output
    assert str(Path("Evidence") / "Evidence_chunks.jsonl") in output
    assert str(Path("Evidence") / "Chapters") in output


def test_show_info_does_not_mislabel_collection_status_failure(
        monkeypatch, tmp_path, capsys):
    output_dir = tmp_path / "output"
    output_dir.mkdir()
    db_path = tmp_path / "chroma"
    db_path.mkdir()
    monkeypatch.setattr(rag, "OUTPUT_DIR", output_dir)
    close_error = RuntimeError("injected client close failure")
    monkeypatch.setattr(
        rag, "_index_collection_count",
        lambda *_args, **_kwargs: (_ for _ in ()).throw(close_error),
    )

    rag.show_info(db_path, collection_name="book")

    output = capsys.readouterr().out
    assert "Collection status unavailable: injected client close failure" in output
    assert "Collection 'book' not found" not in output


def test_show_info_labels_only_a_complete_verified_run_ready(
        monkeypatch, tmp_path, capsys):
    output_dir = tmp_path / "output"
    run_root = _logical_run_marker(output_dir, "Evidence")
    (run_root / ".rag-publication.json").write_text(
        '{"receipt":true}', encoding="utf-8")
    monkeypatch.setattr(rag, "OUTPUT_DIR", output_dir)
    monkeypatch.setattr(
        rag._retention, "load_pipeline_run_manifest",
        lambda *_args, **_kwargs: (
            run_root / ".rag-run.json", _complete_manifest("Evidence")),
    )
    monkeypatch.setattr(
        rag, "_pipeline_publication_complete", lambda _paths: True)

    rag.show_info(tmp_path / "missing-db")

    assert "[READY] Evidence" in capsys.readouterr().out


def test_show_info_labels_stale_receipt_not_ready(
        monkeypatch, tmp_path, capsys):
    output_dir = tmp_path / "output"
    run_root = _logical_run_marker(output_dir, "Evidence")
    (run_root / ".rag-publication.json").write_text(
        '{"receipt":false}', encoding="utf-8")
    monkeypatch.setattr(rag, "OUTPUT_DIR", output_dir)
    monkeypatch.setattr(
        rag._retention, "load_pipeline_run_manifest",
        lambda *_args, **_kwargs: (
            run_root / ".rag-run.json", _complete_manifest("Evidence")),
    )
    monkeypatch.setattr(
        rag, "_pipeline_publication_complete", lambda _paths: False)

    rag.show_info(tmp_path / "missing-db")

    output = capsys.readouterr().out
    assert "[STALE] Evidence" in output
    assert "[READY] Evidence" not in output


def test_show_info_rechecks_complete_manifest_after_acquiring_run_lease(
        monkeypatch, tmp_path, capsys):
    output_dir = tmp_path / "output"
    run_root = _logical_run_marker(output_dir, "Evidence")
    (run_root / ".rag-publication.json").write_text(
        '{"receipt":true}', encoding="utf-8")
    monkeypatch.setattr(rag, "OUTPUT_DIR", output_dir)
    state = {"active": False, "loads": 0}
    lease_call = {}

    def load_manifest(*_args, **_kwargs):
        state["loads"] += 1
        payload = _complete_manifest("Evidence")
        if state["active"]:
            payload["state"] = "active"
        return run_root / ".rag-run.json", payload

    @contextmanager
    def delayed_lease(*_args, **kwargs):
        lease_call.update(kwargs)
        state["active"] = True
        yield

    monkeypatch.setattr(
        rag._retention, "load_pipeline_run_manifest", load_manifest)
    monkeypatch.setattr(rag, "_pipeline_job_lock", delayed_lease)
    monkeypatch.setattr(
        rag, "_pipeline_publication_complete",
        lambda _paths: (_ for _ in ()).throw(
            AssertionError("active runs must not verify publication")),
    )

    rag.show_info(tmp_path / "missing-db", lock_timeout=0.25)

    output = capsys.readouterr().out
    assert state["loads"] == 2
    assert lease_call == {
        "scope_name": "Evidence",
        "timeout": 0.25,
        "output_root": output_dir,
    }
    assert "[STALE] Evidence" in output
    assert "[READY] Evidence" not in output


def test_show_info_short_circuits_noncomplete_manifest(
        monkeypatch, tmp_path, capsys):
    output_dir = tmp_path / "output"
    run_root = _logical_run_marker(output_dir, "Evidence", state="failed")
    monkeypatch.setattr(rag, "OUTPUT_DIR", output_dir)
    monkeypatch.setattr(
        rag._retention, "load_pipeline_run_manifest",
        lambda *_args, **_kwargs: (
            run_root / ".rag-run.json", {"state": "failed"}),
    )
    monkeypatch.setattr(
        rag, "_pipeline_publication_complete",
        lambda _paths: (_ for _ in ()).throw(
            AssertionError("failed runs must not verify publication")),
    )

    rag.show_info(tmp_path / "missing-db")

    output = capsys.readouterr().out
    assert "[STALE] Evidence" in output
    assert "READY" not in output
