import json
import os
import subprocess
import sys
from pathlib import Path

import pytest

import inspect_chunks
import preprocess_pdf


def _chunk(content_type, text="short text", case_names=None):
    return {
        "text": text,
        "metadata": {
            "content_type": content_type,
            "case_names": case_names or [],
        },
    }


def _write_chunks(path, chunks):
    path.write_text(
        "\n".join(json.dumps(chunk, ensure_ascii=False) for chunk in chunks),
        encoding="utf-8",
    )


def test_load_chunks_accepts_utf8_and_ignores_blank_lines(tmp_path):
    chunks_file = tmp_path / "chunks.jsonl"
    chunks_file.write_text(
        "\n"
        + json.dumps(
            _chunk("case_opinion", "Résumé — 東京"),
            ensure_ascii=False,
        )
        + "\n\n",
        encoding="utf-8",
    )

    chunks = inspect_chunks.load_chunks(chunks_file)

    assert chunks[0]["text"] == "Résumé — 東京"


@pytest.mark.parametrize("contents", ["", "\n \t\n"])
def test_load_chunks_rejects_empty_or_blank_files(tmp_path, contents):
    chunks_file = tmp_path / "chunks.jsonl"
    chunks_file.write_text(contents, encoding="utf-8")

    with pytest.raises(inspect_chunks.ChunkFileError, match="no chunk records"):
        inspect_chunks.load_chunks(chunks_file)


def test_load_chunks_reports_malformed_json_line(tmp_path):
    chunks_file = tmp_path / "chunks.jsonl"
    chunks_file.write_text(
        json.dumps(_chunk("author_narrative")) + "\n{not json}\n",
        encoding="utf-8",
    )

    with pytest.raises(
        inspect_chunks.ChunkFileError,
        match=r"invalid JSON on line 2, column 2",
    ):
        inspect_chunks.load_chunks(chunks_file)


def test_load_chunks_reports_invalid_record_shape(tmp_path):
    chunks_file = tmp_path / "chunks.jsonl"
    chunks_file.write_text("[]\n", encoding="utf-8")

    with pytest.raises(
        inspect_chunks.ChunkFileError,
        match=r"line 1: expected a JSON object",
    ):
        inspect_chunks.load_chunks(chunks_file)


def test_filter_preserves_original_indices_in_case_listing(tmp_path, capsys):
    chunks_file = tmp_path / "chunks.jsonl"
    _write_chunks(
        chunks_file,
        [
            _chunk("author_narrative"),
            _chunk("case_opinion", case_names=["Marbury v. Madison"]),
        ],
    )

    result = inspect_chunks.main(
        [str(chunks_file), "--type", "case_opinion", "--cases"]
    )

    assert result == 0
    assert "Chunks: 1" in capsys.readouterr().out


def test_show_uses_original_index_after_filtering(tmp_path, capsys):
    chunks_file = tmp_path / "chunks.jsonl"
    _write_chunks(
        chunks_file,
        [
            _chunk("author_narrative", "first"),
            _chunk("case_opinion", "second"),
        ],
    )

    result = inspect_chunks.main(
        [str(chunks_file), "--type", "case_opinion", "--show", "1"]
    )

    assert result == 0
    output = capsys.readouterr().out
    assert "CHUNK #1" in output
    assert "second" in output


def test_cli_reconfigures_ascii_streams_to_utf8(tmp_path):
    chunks_file = tmp_path / "chunks.jsonl"
    _write_chunks(
        chunks_file,
        [_chunk("case_opinion", case_names=["東京 v. Osaka"])],
    )
    environment = os.environ.copy()
    environment["PYTHONIOENCODING"] = "ascii"

    completed = subprocess.run(
        [
            sys.executable,
            str(Path(inspect_chunks.__file__)),
            str(chunks_file),
            "--cases",
        ],
        stdout=subprocess.PIPE,
        stderr=subprocess.PIPE,
        env=environment,
        check=False,
    )

    assert completed.returncode == 0, completed.stderr.decode("utf-8")
    assert "東京 v. Osaka" in completed.stdout.decode("utf-8")


def test_preprocess_analyze_uses_cli_minimum_dimension(
    tmp_path, monkeypatch, capsys
):
    source = tmp_path / "source.pdf"
    source.write_bytes(b"placeholder")
    observed = {}

    def fake_analyze(input_path, min_dimension):
        observed["call"] = (input_path, min_dimension)
        return {
            "total_pages": 2,
            "pages_with_large_images": 0,
            "unique_image_dims": set(),
            "total_image_xrefs": set(),
            "text_ok_pages": 2,
        }

    monkeypatch.setattr(preprocess_pdf, "analyze_pdf", fake_analyze)

    result = preprocess_pdf.main(
        [str(source), "--analyze", "--min-dim", "777"]
    )

    assert result == 0
    assert observed["call"] == (source, 777)
    assert "Unique image dimensions:  none" in capsys.readouterr().out


def test_preprocess_rejects_same_input_and_output(tmp_path, monkeypatch):
    source = tmp_path / "source.pdf"
    source.write_bytes(b"placeholder")
    monkeypatch.setattr(
        preprocess_pdf,
        "strip_background_images",
        lambda *args: pytest.fail("strip should not run"),
    )

    with pytest.raises(SystemExit) as error:
        preprocess_pdf.main([str(source), "--output", str(source)])

    assert error.value.code == 2


def test_preprocess_prints_existing_pipeline_command(tmp_path, monkeypatch, capsys):
    source = tmp_path / "Source Book.pdf"
    output = tmp_path / "Clean Book.pdf"
    source.write_bytes(b"placeholder")
    monkeypatch.setattr(
        preprocess_pdf,
        "strip_background_images",
        lambda *args: {
            "elapsed_sec": 1.0,
            "images_removed": 2,
            "original_mb": 3.0,
            "stripped_mb": 1.0,
            "failed_pages": [],
            "text_verify_passed": 2,
            "text_verify_total": 2,
        },
    )

    result = preprocess_pdf.main([str(source), "--output", str(output)])

    assert result == 0
    command_output = capsys.readouterr().out
    assert "python rag.py convert --pdf" in command_output
    assert str(output) in command_output
    assert "civpro_pipeline.py" not in command_output


def test_preprocess_rejects_nonpositive_minimum_dimension(tmp_path):
    source = tmp_path / "source.pdf"
    source.write_bytes(b"placeholder")

    with pytest.raises(SystemExit) as error:
        preprocess_pdf.main([str(source), "--min-dim", "0"])

    assert error.value.code == 2
