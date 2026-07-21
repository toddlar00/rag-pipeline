from pathlib import Path
from types import SimpleNamespace

import pytest

import rag


def _args(**overrides):
    values = {
        "collection": None,
        "db_backend": "chroma",
        "embedding_model": "test-embedding",
        "full_reindex": False,
        "batch_size": None,
        "backend": "auto",
        "force": False,
        "no_preprocess": False,
        "max_tokens": 512,
        "min_words": 10,
        "dedup_threshold": 0.9,
        "llm_classify": False,
        "zeroshot_classify": False,
        "contextualize": False,
        "reconstruct_headings": False,
        "quality_score": False,
        "cloud_url": "https://example.test/v1",
        "cloud_model": "test-model",
        "cloud_key": "test-key",
        "ollama_url": "",
        "ollama_model": "",
        "gemini_key": "",
        "llm_workers": 2,
        "thinking": False,
        "split_chapters": False,
        "raptor": False,
    }
    values.update(overrides)
    return SimpleNamespace(**values)


def _paths(monkeypatch, tmp_path: Path):
    monkeypatch.setattr(rag, "OUTPUT_DIR", tmp_path / "output")
    return rag._output_paths_for_name("Book")


def test_shared_runner_passes_distinct_per_run_conversion_artifacts(
        monkeypatch, tmp_path):
    paths = _paths(monkeypatch, tmp_path)
    calls = []

    monkeypatch.setattr(
        rag, "convert_pdf",
        lambda *args, **kwargs: calls.append(("convert", args, kwargs)))
    monkeypatch.setattr(
        rag, "chunk_document",
        lambda *args, **kwargs: calls.append(("chunk", args, kwargs)))
    monkeypatch.setattr(
        rag, "_index_chunks_for_backend",
        lambda *args, **kwargs: calls.append(("index", args, kwargs)))
    monkeypatch.setattr(
        rag, "export_markdown",
        lambda *args, **kwargs: calls.append(("export", args, kwargs)))

    result = rag._run_pipeline_stages(
        Path("Book.pdf"), paths, _args(), resume=False, watermark=None)

    convert = next(call for call in calls if call[0] == "convert")
    assert convert[2]["preprocessed_output"] == paths["preprocessed"]
    assert convert[2]["markdown_output"] == paths["converted_markdown"]
    assert paths["converted_markdown"] != paths["export"]
    assert [call[0] for call in calls] == ["convert", "chunk", "index", "export"]
    assert result["collection"] == "book"
    assert result["db_dir"] == paths["chroma"]


def test_shared_runner_honors_an_explicit_default_named_collection(
        monkeypatch, tmp_path):
    paths = _paths(monkeypatch, tmp_path)
    monkeypatch.setattr(rag, "convert_pdf", lambda *args, **kwargs: None)
    monkeypatch.setattr(rag, "chunk_document", lambda *args, **kwargs: None)
    monkeypatch.setattr(rag, "_index_chunks_for_backend", lambda *args, **kwargs: None)
    monkeypatch.setattr(rag, "export_markdown", lambda *args, **kwargs: None)

    result = rag._run_pipeline_stages(
        Path("Book.pdf"), paths,
        _args(collection=rag.DEFAULT_COLLECTION),
        resume=False, watermark=None,
    )

    assert result["collection"] == rag.DEFAULT_COLLECTION


def test_shared_runner_honors_split_chapters_flag(monkeypatch, tmp_path):
    paths = _paths(monkeypatch, tmp_path)
    exports = []
    monkeypatch.setattr(rag, "convert_pdf", lambda *args, **kwargs: None)
    monkeypatch.setattr(rag, "chunk_document", lambda *args, **kwargs: None)
    monkeypatch.setattr(rag, "_index_chunks_for_backend", lambda *args, **kwargs: None)
    monkeypatch.setattr(
        rag, "export_markdown",
        lambda *args, **kwargs: exports.append((args, kwargs)))

    rag._run_pipeline_stages(
        Path("Book.pdf"), paths, _args(split_chapters=True),
        resume=False, watermark=None)

    assert len(exports) == 2
    assert "split_chapters" not in exports[0][1]
    assert exports[1][1]["split_chapters"] is True
    assert exports[1][1]["chapters_dir"] == paths["chapters_dir"]


def test_resume_revalidates_index_and_rebuilds_missing_chapter_exports(
        monkeypatch, tmp_path):
    paths = _paths(monkeypatch, tmp_path)
    paths["doc"].parent.mkdir(parents=True, exist_ok=True)
    paths["doc"].write_text("{}", encoding="utf-8")
    paths["chunks"].write_text(
        '{"text":"complete","metadata":{}}\n', encoding="utf-8")
    paths["export"].write_text("complete", encoding="utf-8")
    paths["chroma"].mkdir()

    monkeypatch.setattr(
        rag, "convert_pdf",
        lambda *args, **kwargs: pytest.fail("convert should have resumed"))
    monkeypatch.setattr(
        rag, "chunk_document",
        lambda *args, **kwargs: pytest.fail("chunk should have resumed"))
    index_calls = []
    monkeypatch.setattr(
        rag, "_index_chunks_for_backend",
        lambda *args, **kwargs: index_calls.append((args, kwargs)))
    exports = []
    monkeypatch.setattr(
        rag, "export_markdown",
        lambda *args, **kwargs: exports.append((args, kwargs)))

    rag._run_pipeline_stages(
        Path("Book.pdf"), paths, _args(split_chapters=True),
        resume=True, watermark=None)

    assert len(exports) == 1
    assert exports[0][1]["split_chapters"] is True
    assert len(index_calls) == 1
    assert index_calls[0][1]["embedding_model"] == "test-embedding"


def test_runner_reports_the_failing_stage_including_system_exit(
        monkeypatch, tmp_path):
    paths = _paths(monkeypatch, tmp_path)
    monkeypatch.setattr(rag, "convert_pdf", lambda *args, **kwargs: None)
    monkeypatch.setattr(
        rag, "chunk_document",
        lambda *args, **kwargs: (_ for _ in ()).throw(SystemExit(2)))

    with pytest.raises(rag._PipelineStageError) as error:
        rag._run_pipeline_stages(
            Path("Book.pdf"), paths, _args(), resume=False, watermark=None)

    assert error.value.stage == "chunk"
    assert isinstance(error.value.cause, SystemExit)


def test_convert_derives_a_preprocessed_path_from_its_output(
        monkeypatch, tmp_path):
    pdf = tmp_path / "source.pdf"
    pdf.write_bytes(b"pdf")
    doc_output = tmp_path / "run" / "book.json"
    observed = {}

    monkeypatch.setattr(
        rag, "_analyze_pdf_images",
        lambda path: {
            "total_pages": 1,
            "pages_with_large_images": 1,
            "unique_dims": set(),
            "image_xrefs": set(),
        },
    )

    class StopAfterPreprocess(Exception):
        pass

    def fake_preprocess(input_path, output_path, **kwargs):
        observed["output"] = output_path
        raise StopAfterPreprocess

    monkeypatch.setattr(rag, "preprocess_pdf", fake_preprocess)

    with pytest.raises(StopAfterPreprocess):
        rag.convert_pdf(pdf, doc_output)

    assert observed["output"] == doc_output.with_name("book_preprocessed.pdf")


def test_resume_command_preserves_pipeline_options():
    args = _args(
        split_chapters=True,
        llm_scaffold=True,
        contextualize=True,
        thinking=True,
        cloud_url="https://api.deepseek.com",
        cloud_model="deepseek-v4-pro",
        cloud_key="deepseek-secret",
        gemini_key="gemini-secret",
        max_tokens=1024,
        min_words=5,
        backend="auto",
        max_llm_transport_attempts=23,
    )

    command = rag._build_resume_cmd(Path("My Book.pdf"), args)

    assert '--pdf "My Book.pdf" --resume' in command
    assert "--split-chapters" in command
    assert "--llm-scaffold" in command
    assert "--contextualize" in command
    assert "--thinking" in command
    assert "--cloud-url https://api.deepseek.com" in command
    assert "--cloud-model deepseek-v4-pro" in command
    assert "--max-tokens 1024" in command
    assert "--min-words 5" in command
    assert "--backend auto" in command
    assert "--max-llm-transport-attempts 23" in command
    assert "deepseek-secret" not in command
    assert "gemini-secret" not in command
    assert "--cloud-key" not in command
    assert "--api-key" not in command
    assert "--gemini-key" not in command


def test_shared_runner_forwards_thinking_and_provider_to_all_llm_stages(
        monkeypatch, tmp_path):
    paths = _paths(monkeypatch, tmp_path)
    calls = {}

    monkeypatch.setattr(rag, "convert_pdf", lambda *args, **kwargs: None)
    monkeypatch.setattr(
        rag,
        "chunk_document",
        lambda *args, **kwargs: calls.setdefault("chunk", kwargs),
    )
    monkeypatch.setattr(rag, "_index_chunks_for_backend", lambda *args, **kwargs: None)
    monkeypatch.setattr(
        rag,
        "export_markdown",
        lambda *args, **kwargs: calls.setdefault("export", kwargs),
    )
    monkeypatch.setattr(
        rag,
        "build_raptor_tree",
        lambda *args, **kwargs: calls.setdefault("raptor", kwargs),
    )
    args = _args(
        raptor=True,
        thinking=True,
        cloud_url="https://api.deepseek.com",
        cloud_model="deepseek-v4-pro",
        cloud_key="typed-key",
        llm_workers=4,
    )

    rag._run_pipeline_stages(
        Path("Book.pdf"), paths, args, resume=False, watermark=None)

    assert set(calls) == {"chunk", "export", "raptor"}
    for kwargs in calls.values():
        assert kwargs["thinking"] is True
        assert kwargs["cloud_url"] == "https://api.deepseek.com"
        assert kwargs["cloud_model"] == "deepseek-v4-pro"
        assert kwargs["cloud_key"] == "typed-key"
        assert kwargs["llm_workers"] == 4


def test_resume_validators_reject_partial_json_artifacts(tmp_path):
    doc = tmp_path / "book.json"
    chunks = tmp_path / "chunks.jsonl"
    doc.write_text('{"partial":', encoding="utf-8")
    chunks.write_text(
        '{"text":"valid","metadata":{}}\n{"text":', encoding="utf-8")

    assert rag._json_file_is_valid(doc) is False
    assert rag._chunk_record_count(chunks) is None


def test_split_export_rejects_non_markdown_formats(tmp_path):
    with pytest.raises(ValueError, match="only for markdown"):
        rag.export_markdown(
            tmp_path / "chunks.jsonl",
            tmp_path / "book.txt",
            split_chapters=True,
            format="plaintext",
        )
