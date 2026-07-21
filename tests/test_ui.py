from pathlib import Path
from types import SimpleNamespace

import ui


def test_search_uses_backend_bound_to_config(monkeypatch, tmp_path):
    observed = {}
    monkeypatch.setitem(ui._config, "db_path", tmp_path)
    monkeypatch.setitem(ui._config, "chunks_path", tmp_path / "chunks.jsonl")
    monkeypatch.setitem(ui._config, "db_backend", "chroma")
    monkeypatch.setitem(ui._config, "collection", "book")

    def fake_search(*args, **kwargs):
        observed.update(kwargs)
        return SimpleNamespace(
            hits=[], effective_mode="vector",
            reranker_applied=False, warnings=[])

    monkeypatch.setattr(ui.rag, "search_index", fake_search)

    ui.do_search("query", "All", "All", 5, False, False, "qdrant")

    assert observed["db_backend"] == "chroma"


def test_search_ui_maps_auto_modes_to_tristate(monkeypatch, tmp_path):
    observed = {}
    monkeypatch.setitem(ui._config, "db_path", tmp_path)
    monkeypatch.setitem(ui._config, "chunks_path", tmp_path / "chunks.jsonl")
    monkeypatch.setitem(ui._config, "db_backend", "chroma")
    monkeypatch.setitem(ui._config, "collection", "book")

    def fake_search(*args, **kwargs):
        observed.update(kwargs)
        return SimpleNamespace(
            hits=[], effective_mode="hybrid",
            reranker_applied=False, warnings=[])

    monkeypatch.setattr(ui.rag, "search_index", fake_search)

    ui.do_search(
        "query", "All", "All", 5,
        "Auto (recommended)", "Auto (recommended)")

    assert observed["hybrid"] is None
    assert observed["use_reranker"] is None


def test_split_export_uses_unique_directory_and_returns_archive(
        monkeypatch, tmp_path):
    chunks = tmp_path / "Book_chunks.jsonl"
    chunks.write_text("{}\n", encoding="utf-8")
    monkeypatch.setitem(ui._config, "chunks_path", chunks)
    monkeypatch.setattr(ui, "uuid4", lambda: SimpleNamespace(hex="unique-run"))

    def fake_export(chunks_path, out_path, **kwargs):
        assert kwargs["split_chapters"] is True
        chapters_dir = kwargs["chapters_dir"]
        chapters_dir.mkdir(parents=True)
        (chapters_dir / "ch01.md").write_text("# One", encoding="utf-8")

    monkeypatch.setattr(ui.rag, "export_markdown", fake_export)

    summary, archive = ui.do_export([], True, "", True)

    expected_root = tmp_path / "ui_exports" / "unique-run"
    assert "ch01.md" in summary
    assert Path(archive).is_file()
    assert Path(archive).parent == expected_root
