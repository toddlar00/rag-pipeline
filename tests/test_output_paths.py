from pathlib import Path

import rag


def _use_output_dir(monkeypatch, tmp_path: Path) -> Path:
    output_dir = tmp_path / "output"
    output_dir.mkdir()
    monkeypatch.setattr(rag, "OUTPUT_DIR", output_dir)
    return output_dir


def test_new_run_uses_book_scoped_paths(monkeypatch, tmp_path):
    output_dir = _use_output_dir(monkeypatch, tmp_path)

    paths = rag._derive_output_paths(Path("Civil Procedure.pdf"))

    book_dir = output_dir / "Civil Procedure"
    assert paths == {
        "doc": book_dir / "Civil Procedure.json",
        "converted_markdown": book_dir / "Civil Procedure_docling.md",
        "chunks": book_dir / "Civil Procedure_chunks.jsonl",
        "quality_report": book_dir / "Civil Procedure_chunks.quality.json",
        "export": book_dir / "Civil Procedure.md",
        "chapters_dir": book_dir / "Chapters",
        "chroma": book_dir / "Civil Procedure_chroma",
        "qdrant": book_dir / "Civil Procedure_qdrant",
        "preprocessed": output_dir / "Civil Procedure_preprocessed.pdf",
        "collection": "civil_procedure",
    }


def test_new_run_increments_after_book_directory(monkeypatch, tmp_path):
    output_dir = _use_output_dir(monkeypatch, tmp_path)
    (output_dir / "Property I").mkdir()

    paths = rag._derive_output_paths(Path("Property I.pdf"))

    assert paths["doc"] == output_dir / "Property I_2" / "Property I_2.json"
    assert paths["collection"] == "property_i_2"


def test_new_run_continues_after_highest_number_despite_gap(
        monkeypatch, tmp_path):
    output_dir = _use_output_dir(monkeypatch, tmp_path)
    (output_dir / "Contracts").mkdir()
    (output_dir / "Contracts_3").mkdir()

    paths = rag._derive_output_paths(Path("Contracts.pdf"))

    assert paths["doc"] == output_dir / "Contracts_4" / "Contracts_4.json"


def test_resume_uses_highest_existing_run_despite_gap(monkeypatch, tmp_path):
    output_dir = _use_output_dir(monkeypatch, tmp_path)
    (output_dir / "Torts").mkdir()
    (output_dir / "Torts_7").mkdir()

    paths = rag._derive_output_paths_existing(Path("Torts.pdf"))

    assert paths["doc"] == output_dir / "Torts_7" / "Torts_7.json"


def test_legacy_flat_json_reserves_run_name(monkeypatch, tmp_path):
    output_dir = _use_output_dir(monkeypatch, tmp_path)
    (output_dir / "Constitutional Law.json").write_text("{}", encoding="utf-8")

    paths = rag._derive_output_paths(Path("Constitutional Law.pdf"))

    assert paths["doc"] == (
        output_dir / "Constitutional Law_2" / "Constitutional Law_2.json"
    )


def test_unrelated_suffixes_do_not_affect_run_number(monkeypatch, tmp_path):
    output_dir = _use_output_dir(monkeypatch, tmp_path)
    (output_dir / "Cases_draft").mkdir()
    (output_dir / "Cases_1").mkdir()

    paths = rag._derive_output_paths(Path("Cases.pdf"))

    assert paths["doc"] == output_dir / "Cases" / "Cases.json"
