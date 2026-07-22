from pathlib import Path

import rag


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
    db_path = tmp_path / "chroma"
    db_path.mkdir()
    close_error = RuntimeError("injected client close failure")
    monkeypatch.setattr(
        rag, "_index_collection_count",
        lambda *_args, **_kwargs: (_ for _ in ()).throw(close_error),
    )

    rag.show_info(db_path, collection_name="book")

    output = capsys.readouterr().out
    assert "Collection status unavailable: injected client close failure" in output
    assert "Collection 'book' not found" not in output
