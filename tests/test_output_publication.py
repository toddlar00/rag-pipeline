"""Atomic publication and fail-closed resume validation regressions."""

import hashlib
import json
from pathlib import Path
import textwrap

import pytest

import rag


def _record(index: int, chapter: int, title: str, text: str) -> dict:
    return {
        "text": text,
        "metadata": {
            "chunk_index": index,
            "content_type": "author_narrative",
            "chapter_num": chapter,
            "chapter_title": title,
            "section_path": f"Chapter {chapter} > Section",
        },
    }


def _write_chunks(path: Path, records: list[dict]) -> None:
    rag._atomic_write_jsonl(path, records)


def _lineaged_record() -> dict:
    return {
        "text": "A source-backed discussion of professional responsibility.",
        "metadata": {
            "chunk_index": 0,
            "source_file": "book",
            "source_lineage_schema_version": 1,
            "source_items": [{
                "ref": "#/texts/0",
                "label": "text",
                "parent_refs": [],
                "spans": [{"page": 1}],
            }],
            "page_start": 1,
            "page_end": 1,
            "page_range": "pp.1-1",
            "chapter_num": 1,
            "chapter_title": "One",
            "section_path": "Chapter 1",
            "content_type": "author_narrative",
            "content_source": "body",
            "token_count": 8,
            "embedding_token_count": 10,
            "case_names": [],
            "primary_case": None,
        },
    }


def _write_quality_source(path: Path) -> None:
    rag._atomic_write_json(path, {
        "pages": {"1": {}},
        "texts": [{
            "self_ref": "#/texts/0",
            "label": "text",
            "content_layer": "body",
            "text": "A source-backed discussion of professional responsibility.",
            "prov": [{"page_no": 1}],
        }],
    })


def _chunk_inputs(document: Path) -> dict:
    raw = document.read_bytes()
    return {
        "docling_json": {
            "name": document.name,
            "size": len(raw),
            "sha256": hashlib.sha256(raw).hexdigest(),
        },
        "conversion_manifest": None,
        "table_recovery": None,
    }


def _write_chunk_completion(
        document: Path, chunks: Path, parameters: dict) -> None:
    rag._write_artifact_completion(
        rag._artifact_completion_path(chunks, stage="chunking"),
        stage="chunking",
        source_sha256=rag._cached_artifact_sha256(document),
        source_record_count=None,
        parameters=parameters,
        outputs={"chunks_jsonl": chunks},
        schema_version=rag.CHUNK_COMPLETION_SCHEMA_VERSION,
        extra_fields={"inputs": _chunk_inputs(document)},
    )


def test_atomic_text_failure_preserves_previous_bytes(monkeypatch, tmp_path):
    target = tmp_path / "book.md"
    original = b"previous complete output\n"
    target.write_bytes(original)
    monkeypatch.setattr(
        rag.os, "replace",
        lambda *_args: (_ for _ in ()).throw(OSError("replace failed")),
    )

    with pytest.raises(OSError, match="replace failed"):
        rag._atomic_write_text(target, "partial replacement")

    assert target.read_bytes() == original
    assert list(tmp_path.glob(".book.md.*.tmp")) == []


def test_conversion_completion_requires_both_untampered_outputs(
        monkeypatch, tmp_path):
    source = tmp_path / "book.pdf"
    document = tmp_path / "book.json"
    markdown = tmp_path / "book_docling.md"
    source.write_bytes(b"pdf generation")
    rag._atomic_write_text(document, '{"name": "book"}')
    rag._atomic_write_text(markdown, "# Complete markdown")
    parameters = rag._conversion_parameters(
        batch_size_override=None, backend="auto", auto_preprocess=True,
        ocr=None, watermark=None)
    rag._write_artifact_completion(
        rag._artifact_completion_path(document, stage="conversion"),
        stage="conversion", source_sha256=rag._cached_artifact_sha256(source),
        source_name=source.name,
        source_record_count=None, parameters=parameters,
        outputs={"docling_json": document, "docling_markdown": markdown},
        schema_version=rag.CONVERSION_COMPLETION_SCHEMA_VERSION,
        extra_fields={
            "source": {
                "name": source.name,
                "size": source.stat().st_size,
                "sha256": rag._cached_artifact_sha256(source),
                "capture_policy": "stream-copy-v1",
            },
            "effective_input": {
                "kind": "original",
                "name": source.name,
                "size": source.stat().st_size,
                "sha256": rag._cached_artifact_sha256(source),
            },
        })

    assert rag._converted_outputs_complete(
        source, document, markdown, parameters=parameters)

    monkeypatch.setattr(
        rag, "_model_artifact_lock_sha256", lambda: "e" * 64)
    changed_lock_parameters = rag._conversion_parameters(
        batch_size_override=None, backend="auto", auto_preprocess=True,
        ocr=None, watermark=None)
    assert not rag._converted_outputs_complete(
        source, document, markdown, parameters=changed_lock_parameters)

    rag._atomic_write_text(markdown, "tampered")
    assert not rag._converted_outputs_complete(
        source, document, markdown, parameters=parameters)


def test_chunk_completion_binds_source_options_model_lock_and_output(
        monkeypatch, tmp_path):
    document = tmp_path / "book.json"
    chunks = tmp_path / "chunks.jsonl"
    document.write_text('{"name":"book"}', encoding="utf-8")
    _write_chunks(chunks, [_record(0, 1, "One", "complete chunk")])
    monkeypatch.setattr(
        rag, "_llm_runtime",
        rag.LLMRuntime(rag.LLMRuntimeConfig(
            cache_mode="off", cache_dir=tmp_path / "llm-cache",
            max_transport_attempts=3)),
    )

    def parameters(**overrides):
        values = {
            "embedding_model": "model-a", "max_tokens": 512,
            "min_words": 10, "dedup_threshold": 0.9,
            "watermark": None, "llm_classify": True,
            "zeroshot_classify": True, "contextualize": True,
            "ollama_url": "http://localhost:11434",
            "ollama_model": "local-model", "gemini_key": "secret-a",
            "cloud_url": "https://example.test/v1",
            "cloud_model": "cloud-model", "cloud_key": "secret-b",
            "llm_workers": 2, "thinking": False,
            "reconstruct_headings": True, "quality_score": True,
            "llm_scaffold": True,
        }
        values.update(overrides)
        return rag._chunk_parameters(**values)

    initial = parameters()
    assert initial["max_llm_transport_attempts"] == 3
    _write_chunk_completion(document, chunks, initial)

    assert rag._chunks_complete(document, chunks, parameters=initial)
    assert "secret-a" not in str(initial)
    assert "secret-b" not in str(initial)
    assert not rag._chunks_complete(
        document, chunks, parameters=parameters(max_tokens=256))

    rag._llm_runtime.configure(rag.LLMRuntimeConfig(
        cache_mode="off", cache_dir=tmp_path / "llm-cache",
        max_transport_attempts=4))
    assert not rag._chunks_complete(
        document, chunks, parameters=parameters())
    rag._llm_runtime.configure(rag.LLMRuntimeConfig(
        cache_mode="off", cache_dir=tmp_path / "llm-cache",
        max_transport_attempts=3))

    monkeypatch.setattr(
        rag, "_model_artifact_lock_sha256", lambda: "f" * 64)
    assert not rag._chunks_complete(
        document, chunks, parameters=parameters())

    document.write_text('{"name":"changed"}', encoding="utf-8")
    assert not rag._chunks_complete(document, chunks, parameters=initial)


def test_quality_report_is_repairable_and_required_for_lineaged_chunks(
        tmp_path):
    document = tmp_path / "book.json"
    chunks = tmp_path / "book_chunks.jsonl"
    parameters = {"embedding_model": "model-a", "chunking_policy_version": 19}
    _write_quality_source(document)
    _write_chunks(chunks, [_lineaged_record()])
    _write_chunk_completion(document, chunks, parameters)

    report = rag._publish_corpus_quality_report(
        document, chunks, parameters=parameters, structural_ranges=set())
    report_path = rag._quality_core.quality_report_path(chunks)

    assert report["status"] == "pass"
    assert rag._quality_report_complete(
        document, chunks, parameters=parameters)
    assert len(rag._load_index_records_strict(chunks)) == 1

    report_path.unlink()
    assert not rag._quality_report_complete(
        document, chunks, parameters=parameters)
    with pytest.raises(OSError):
        rag._load_index_records_strict(chunks)

    repaired = rag._publish_corpus_quality_report(
        document, chunks, parameters=parameters, structural_ranges=set())
    assert repaired == report
    assert rag._quality_report_complete(
        document, chunks, parameters=parameters)


def test_lineaged_chunks_refuse_stale_or_failed_quality_report(tmp_path):
    document = tmp_path / "book.json"
    chunks = tmp_path / "book_chunks.jsonl"
    parameters = {"embedding_model": "model-a", "chunking_policy_version": 19}
    _write_quality_source(document)
    _write_chunks(chunks, [_lineaged_record()])
    _write_chunk_completion(document, chunks, parameters)
    rag._publish_corpus_quality_report(
        document, chunks, parameters=parameters, structural_ranges=set())
    report_path = rag._quality_core.quality_report_path(chunks)

    payload = json.loads(report_path.read_text(encoding="utf-8"))
    payload["status"] = "fail"
    rag._atomic_write_json(report_path, payload)
    with pytest.raises(ValueError, match="did not pass"):
        rag._load_index_records_strict(chunks)

    rag._publish_corpus_quality_report(
        document, chunks, parameters=parameters, structural_ranges=set())
    changed = _lineaged_record()
    changed["text"] += " Changed after attestation."
    _write_chunks(chunks, [changed])
    with pytest.raises(ValueError, match="does not match chunks artifact"):
        rag._load_index_records_strict(chunks)


@pytest.mark.parametrize("consumer_name", [
    "extract_questions",
    "generate_exam_questions",
    "build_citation_graph",
    "generate_briefs",
])
def test_downstream_consumers_require_quality_report(
        consumer_name, tmp_path):
    chunks = tmp_path / "book_chunks.jsonl"
    output = tmp_path / f"{consumer_name}.json"
    _write_chunks(chunks, [_lineaged_record()])

    with pytest.raises(OSError):
        getattr(rag, consumer_name)(chunks, output)

    assert not output.exists()


def test_unified_export_manifest_binds_output_to_exact_chunks(tmp_path):
    chunks = tmp_path / "chunks.jsonl"
    output = tmp_path / "book.md"
    parameters = rag._markdown_export_parameters(
        include_types=None, exclude_types=None, chapters=None,
        split_chapters=False)
    _write_chunks(chunks, [_record(0, 1, "One", "first generation")])

    rag.export_markdown(chunks, output)

    assert rag._unified_export_complete(
        chunks, output, parameters=parameters)
    _write_chunks(chunks, [_record(0, 1, "One", "second generation")])
    assert not rag._unified_export_complete(
        chunks, output, parameters=parameters)


def test_split_export_partial_retry_removes_obsolete_owned_files(
        monkeypatch, tmp_path):
    chunks = tmp_path / "chunks.jsonl"
    output = tmp_path / "book.md"
    chapters = tmp_path / "Chapters"
    parameters = rag._markdown_export_parameters(
        include_types=None, exclude_types=None, chapters=None,
        split_chapters=True)
    _write_chunks(chunks, [
        _record(0, 1, "One", "chapter one"),
        _record(1, 2, "Two", "chapter two"),
    ])
    rag.export_markdown(
        chunks, output, split_chapters=True, chapters_dir=chapters)
    assert rag._split_export_complete(
        chunks, chapters, parameters=parameters)

    _write_chunks(chunks, [
        _record(0, 1, "One", "updated chapter one"),
        _record(1, 3, "Three", "chapter three"),
    ])
    original_writer = rag._atomic_write_text
    calls = 0

    def fail_after_first(path, content):
        nonlocal calls
        calls += 1
        if calls == 2:
            raise OSError("injected chapter failure")
        original_writer(path, content)

    monkeypatch.setattr(rag, "_atomic_write_text", fail_after_first)
    with pytest.raises(OSError, match="chapter failure"):
        rag.export_markdown(
            chunks, output, split_chapters=True, chapters_dir=chapters)
    assert not rag._split_export_complete(
        chunks, chapters, parameters=parameters)

    monkeypatch.setattr(rag, "_atomic_write_text", original_writer)
    rag.export_markdown(
        chunks, output, split_chapters=True, chapters_dir=chapters)

    assert rag._split_export_complete(
        chunks, chapters, parameters=parameters)
    assert {path.name for path in chapters.glob("*.md")} == {
        "ch01_One.md", "ch03_Three.md",
    }


def test_raptor_validator_accepts_source_bound_degraded_tree(tmp_path):
    chunks = tmp_path / "chunks.jsonl"
    output = tmp_path / "book_raptor.json"
    _write_chunks(chunks, [_record(0, 1, "One", "source text")])
    _, source_sha256, _ = rag._load_index_snapshot_strict(chunks)
    parameters = rag._raptor_parameters(
        embedding_model="model", cloud_url="", cloud_model="",
        cloud_key="never-persist-this", ollama_url="http://localhost",
        ollama_model="local",
        gemini_key="", thinking=False)
    tree = {
        "schema_version": rag.ARTIFACT_COMPLETION_SCHEMA_VERSION,
        "source_sha256": source_sha256,
        "source_record_count": 1,
        "parameters_sha256": rag._artifact_parameters_sha256(parameters),
        "levels": 2,
        "nodes": [
            {"node_id": "L0_0", "level": 0, "children": [],
             "text": "source", "metadata": {}},
            {"node_id": "L1_0", "level": 1, "children": ["L0_0"],
             "text": "summary", "metadata": {}},
        ],
        "stats": {"level_0": 1, "level_1": 1, "level_2": 0,
                  "total": 2},
    }
    rag._atomic_write_json(output, tree)

    assert rag._raptor_output_complete(
        chunks, output, parameters=parameters)
    assert "never-persist-this" not in output.read_text(encoding="utf-8")

    tree["source_sha256"] = hashlib.sha256(b"other").hexdigest()
    rag._atomic_write_json(output, tree)
    assert not rag._raptor_output_complete(
        chunks, output, parameters=parameters)


def test_killed_export_before_completion_commit_is_not_resumable(tmp_path):
    project_root = Path(__file__).resolve().parents[1]
    chunks = tmp_path / "chunks.jsonl"
    output = tmp_path / "book.md"
    published = tmp_path / "published.ready"
    worker = tmp_path / "partial_export_worker.py"
    _write_chunks(chunks, [_record(0, 1, "One", "source text")])
    worker.write_text(
        textwrap.dedent(
            """
            from pathlib import Path
            import sys
            import time

            sys.path.insert(0, sys.argv[1])
            import rag

            rag._atomic_write_text(Path(sys.argv[2]), "published but uncommitted")
            rag._atomic_write_text(Path(sys.argv[3]), "ready")
            time.sleep(60)
            """
        ),
        encoding="utf-8",
    )

    exit_code = rag._run_cli_with_deadline(
        worker, [str(project_root), str(output), str(published)],
        operation="export publication", timeout=30,
        cancel_requested=published.is_file)

    parameters = rag._markdown_export_parameters(
        include_types=None, exclude_types=None, chapters=None,
        split_chapters=False)
    assert exit_code == 130
    assert output.read_text(encoding="utf-8") == "published but uncommitted"
    assert not rag._unified_export_complete(
        chunks, output, parameters=parameters)
