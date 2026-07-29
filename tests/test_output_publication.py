"""Atomic publication and fail-closed resume validation regressions."""

import hashlib
import json
import os
import re
import textwrap
from pathlib import Path

import pytest

import rag
import release_security


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
    text = "A source-backed discussion of professional responsibility."
    item = {
        "self_ref": "#/texts/0",
        "label": "text",
        "text": text,
        "prov": [{
            "page_no": 1,
            "charspan": [0, len(text)],
            "bbox": {
                "l": 10, "t": 10, "r": 200, "b": 20,
                "coord_origin": "TOPLEFT",
            },
        }],
    }
    descriptor, _ = rag._source_fidelity_core.source_descriptor(item)
    tokens = rag._source_fidelity_core.lexical_tokens(text)
    record = {
        "text": text,
        "metadata": {
            "chunk_index": 0,
            "source_file": "book",
            "source_lineage_schema_version": (
                rag._quality_core.SOURCE_LINEAGE_SCHEMA_VERSION),
            "source_items": [{
                "ref": "#/texts/0",
                "label": "text",
                "parent_refs": [],
                "spans": descriptor["spans"],
                "scope": {"provenance_indexes": [0]},
                "source_text_sha256": descriptor["source_text_sha256"],
                "source_lexical_sha256": descriptor[
                    "source_lexical_sha256"],
                "source_lexical_count": descriptor[
                    "source_lexical_count"],
                "transform": "plain",
                "oracle_text_sha256": (
                    rag._source_fidelity_core.text_sha256(text)),
                "oracle_lexical_sha256": (
                    rag._source_fidelity_core.lexical_sha256(tokens)),
                "oracle_lexical_count": len(tokens),
                "recovery_sha256": None,
            }],
            "page_start": 1,
            "page_end": 1,
            "page_range": "pp.1-1",
            "chapter_num": 1,
            "chapter_title": "One",
            "section_path": "",
            rag._heading_lineage.HEADING_SCHEMA_FIELD: (
                rag._heading_lineage.HEADING_LINEAGE_SCHEMA_VERSION),
            rag._heading_lineage.HEADING_PATH_FIELD: [],
            rag._heading_lineage.DIRECT_HEADING_FIELD: [],
            rag._heading_lineage.HEADING_COMPONENTS_FIELD: [],
            "content_type": "author_narrative",
            "content_source": "body",
            "token_count": 8,
            "embedding_token_count": 10,
            "case_names": [],
            "primary_case": None,
        },
    }
    rag._source_fidelity_core.attach_record_attestations([record])
    rag._retrieval_core._attach_retrieval_linkage([record])
    return record


def _write_quality_source(path: Path) -> None:
    text = "A source-backed discussion of professional responsibility."
    rag._atomic_write_json(path, {
        "pages": {"1": {}},
        "texts": [{
            "self_ref": "#/texts/0",
            "label": "text",
            "content_layer": "body",
            "text": text,
            "prov": [{
                "page_no": 1,
                "charspan": [0, len(text)],
                "bbox": {
                    "l": 10, "t": 10, "r": 200, "b": 20,
                    "coord_origin": "TOPLEFT",
                },
            }],
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
    receipt = parameters.get("structure_profile")
    if receipt is None:
        profile = rag._document_profiles.get_profile(
            rag.DEFAULT_STRUCTURE_PROFILE)
        receipt = rag._document_profiles.profile_provenance(profile)
        parameters["structure_profile"] = receipt
    else:
        rag._document_profiles.profile_from_provenance(receipt)
    upstream_inputs = _chunk_inputs(document)
    oracle_path = rag._source_oracle_registry_path(chunks)
    rag._atomic_write_json(
        oracle_path,
        rag._source_fidelity_core.build_source_oracle_registry(
            oracles={}, input_bindings=upstream_inputs),
    )
    inputs = {
        **upstream_inputs,
        "source_fidelity_oracles": rag._source_oracle_registry_binding(
            oracle_path),
    }
    rag._write_artifact_completion(
        rag._artifact_completion_path(chunks, stage="chunking"),
        stage="chunking",
        source_sha256=rag._cached_artifact_sha256(document),
        source_record_count=None,
        parameters=parameters,
        outputs={
            "chunks_jsonl": chunks,
            "source_fidelity_oracles": oracle_path,
        },
        schema_version=rag.CHUNK_COMPLETION_SCHEMA_VERSION,
        extra_fields={
            "inputs": inputs,
            "structure_profile": receipt,
            "structure_profile_parameters_sha256": (
                rag._structure_profile_parameters_binding(
                    rag._artifact_parameters_sha256(parameters), receipt)),
        },
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


def test_plaintext_export_offsets_index_the_published_bytes(tmp_path):
    """char_start/char_end are a citation contract against the .txt file."""
    chunks = [
        _record(0, 1, "Duties", "First passage.\nSecond line of the first."),
        _record(1, 1, "Duties", "Another passage.\nWith its own line."),
        _record(2, 2, "Fees", "Third passage."),
    ]
    export_path = tmp_path / "book"

    rag._export_plaintext(chunks, export_path)

    published = (tmp_path / "book.txt").read_bytes().decode("utf-8")
    records = json.loads(
        (tmp_path / "book.metadata.json").read_text(encoding="utf-8"))

    assert b"\r\n" not in (tmp_path / "book.txt").read_bytes()
    assert len(records) == len(chunks)
    for record, chunk in zip(records, chunks):
        block = published[record["char_start"]:record["char_end"]]
        assert block.endswith(chunk["text"])
        assert block.startswith("[CHUNK ")
    assert records[-1]["char_end"] == len(published)


def test_atomic_exact_utf8_failure_preserves_previous_bytes(
        monkeypatch, tmp_path):
    target = tmp_path / "export.txt"
    original = b"previous complete output\n"
    target.write_bytes(original)
    monkeypatch.setattr(
        rag.os, "replace",
        lambda *_args: (_ for _ in ()).throw(OSError("replace failed")),
    )

    with pytest.raises(OSError, match="replace failed"):
        rag._atomic_write_exact_utf8(target, "partial\nreplacement")

    assert target.read_bytes() == original
    assert list(tmp_path.glob(".export.txt.*.tmp")) == []


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


@pytest.mark.skipif(os.name != "nt", reason="Windows path case semantics")
def test_conversion_completion_accepts_source_path_case_only_change(tmp_path):
    source = tmp_path / "book.pdf"
    document = tmp_path / "book.json"
    markdown = tmp_path / "book_docling.md"
    source.write_bytes(b"pdf generation")
    rag._atomic_write_text(document, '{"name": "book"}')
    rag._atomic_write_text(markdown, "# Complete markdown")
    parameters = rag._conversion_parameters(
        batch_size_override=None, backend="auto", auto_preprocess=True,
        ocr=None, watermark=None)
    digest = rag._cached_artifact_sha256(source)
    rag._write_artifact_completion(
        rag._artifact_completion_path(document, stage="conversion"),
        stage="conversion", source_sha256=digest,
        source_name=source.name, source_record_count=None,
        parameters=parameters,
        outputs={"docling_json": document, "docling_markdown": markdown},
        schema_version=rag.CONVERSION_COMPLETION_SCHEMA_VERSION,
        extra_fields={
            "source": {
                "name": source.name, "size": source.stat().st_size,
                "sha256": digest, "capture_policy": "stream-copy-v1",
            },
            "effective_input": {
                "kind": "original", "name": source.name,
                "size": source.stat().st_size, "sha256": digest,
            },
        })

    assert rag._converted_outputs_complete(
        source.with_name("BOOK.pdf"), document, markdown,
        parameters=parameters)


def test_conversion_parameters_invalidate_legacy_explicit_ocr_receipts():
    forced = rag._conversion_parameters(
        batch_size_override=None, backend="auto", auto_preprocess=True,
        ocr=True, watermark=None)
    automatic = rag._conversion_parameters(
        batch_size_override=None, backend="auto", auto_preprocess=True,
        ocr=None, watermark=None)
    disabled = rag._conversion_parameters(
        batch_size_override=None, backend="auto", auto_preprocess=True,
        ocr=False, watermark=None)

    assert forced["force_full_page_ocr"] is True
    assert automatic["force_full_page_ocr"] is False
    assert disabled["force_full_page_ocr"] is False

    legacy_forced = dict(forced)
    legacy_forced.pop("force_full_page_ocr")
    assert rag._artifact_parameters_sha256(forced) != (
        rag._artifact_parameters_sha256(legacy_forced))


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
            "ollama_url": "http://127.0.0.1:11434",
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
    assert "127.0.0.1" not in str(initial)
    assert "example.test" not in str(initial)
    assert initial["cloud_url"]["policy_version"] == 1
    assert initial["cloud_url"]["endpoint_id"].startswith(
        "v1:custom:sha256:")
    completion_text = rag._artifact_completion_path(
        chunks, stage="chunking").read_text(encoding="utf-8")
    assert "secret" not in completion_text

    version_one = parameters(cloud_url="https://example.test/v1")
    version_two = parameters(cloud_url="https://example.test/v2")
    assert version_one["cloud_url"] != version_two["cloud_url"]
    for unsafe_url in (
        "https://user:secret@example.test/v1",
        "https://example.test/v1?token=secret",
        "cloud-user:secret@example.test/v1",
    ):
        with pytest.raises((TypeError, ValueError)):
            parameters(cloud_url=unsafe_url)
    assert not rag._chunks_complete(
        document, chunks, parameters=parameters(max_tokens=256))
    assert not rag._chunks_complete(
        document, chunks,
        parameters=parameters(structure_profile="roman-parts-book-v1"))

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

    manifest_path = rag._artifact_completion_path(
        chunks, stage="chunking")
    payload = json.loads(manifest_path.read_text(encoding="utf-8"))
    payload.pop("structure_profile")
    rag._atomic_write_json(manifest_path, payload)
    assert not rag._chunks_complete(document, chunks, parameters=initial)

    _write_chunk_completion(document, chunks, initial)
    payload = json.loads(manifest_path.read_text(encoding="utf-8"))
    payload["structure_profile"]["sha256"] = "f" * 64
    rag._atomic_write_json(manifest_path, payload)
    assert not rag._chunks_complete(document, chunks, parameters=initial)

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

    forged = json.loads(json.dumps(report))
    forged["source_analysis"]["pictures"] = {
        "total": 1,
        "with_linked_text": 0,
        "covered_by_linked_text": 0,
        "covered_by_figure_text": 0,
        "missing_linked_text": 0,
        "large_unlinked": 1,
        "other_unlinked": 0,
        "substantive_risk": 1,
        "substantive_risk_refs": ["#/pictures/999"],
    }
    picture_check = next(
        check for check in forged["checks"]
        if check["name"] == "uncovered_substantive_pictures")
    picture_check.update({"status": "warn", "observed": 1})
    rag._atomic_write_json(report_path, forged)
    assert not rag._quality_report_complete(
        document, chunks, parameters=parameters)
    report = rag._publish_corpus_quality_report(
        document, chunks, parameters=parameters, structural_ranges=set())

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


def test_source_oracle_sidecar_tamper_fails_closed_until_regenerated(
        tmp_path):
    document = tmp_path / "book.json"
    chunks = tmp_path / "book_chunks.jsonl"
    parameters = {"embedding_model": "model-a", "chunking_policy_version": 19}
    _write_quality_source(document)
    _write_chunks(chunks, [_lineaged_record()])
    _write_chunk_completion(document, chunks, parameters)
    rag._publish_corpus_quality_report(
        document, chunks, parameters=parameters, structural_ranges=set())
    oracle_path = rag._source_oracle_registry_path(chunks)

    payload = json.loads(oracle_path.read_text(encoding="utf-8"))
    payload["oracle_root_sha256"] = "0" * 64
    rag._atomic_write_json(oracle_path, payload)

    assert not rag._quality_report_complete(
        document, chunks, parameters=parameters)
    with pytest.raises(ValueError, match="source oracle registry"):
        rag._load_index_records_strict(chunks)

    _write_chunk_completion(document, chunks, parameters)
    repaired = rag._publish_corpus_quality_report(
        document, chunks, parameters=parameters, structural_ranges=set())

    assert repaired["status"] == "pass"
    assert rag._quality_report_complete(
        document, chunks, parameters=parameters)
    assert len(rag._load_index_records_strict(chunks)) == 1


def test_quality_repair_reconstructs_ranges_with_attested_profile(
        monkeypatch, tmp_path):
    document = tmp_path / "book.json"
    chunks = tmp_path / "book_chunks.jsonl"
    profile = rag._document_profiles.get_profile("roman-parts-book-v1")
    parameters = {
        "embedding_model": "model-a",
        "chunking_policy_version": 20,
        "structure_profile": rag._document_profiles.profile_provenance(
            profile),
    }
    _write_quality_source(document)
    _write_chunks(chunks, [_lineaged_record()])
    _write_chunk_completion(document, chunks, parameters)
    observed = {}
    real_identify = rag._identify_book_sections

    def capture(document_mapping, **kwargs):
        observed["profile"] = kwargs["structure_profile"]
        return real_identify(document_mapping, **kwargs)

    monkeypatch.setattr(rag, "_identify_book_sections", capture)

    report = rag._publish_corpus_quality_report(
        document, chunks, parameters=parameters)

    assert report["status"] == "pass"
    assert observed["profile"] is profile


def test_quality_repair_rejects_profile_override_against_receipt(tmp_path):
    document = tmp_path / "book.json"
    chunks = tmp_path / "book_chunks.jsonl"
    parameters = {"embedding_model": "model-a", "chunking_policy_version": 20}
    _write_quality_source(document)
    _write_chunks(chunks, [_lineaged_record()])
    _write_chunk_completion(document, chunks, parameters)

    with pytest.raises(ValueError, match="does not match the attested"):
        rag._publish_corpus_quality_report(
            document,
            chunks,
            parameters=parameters,
            structural_ranges=set(),
            structure_profile="roman-parts-book-v1",
        )


def test_index_reader_binds_top_level_profile_to_parameter_receipt(tmp_path):
    document = tmp_path / "book.json"
    chunks = tmp_path / "book_chunks.jsonl"
    parameters = {"embedding_model": "model-a", "chunking_policy_version": 20}
    _write_quality_source(document)
    _write_chunks(chunks, [_lineaged_record()])
    _write_chunk_completion(document, chunks, parameters)
    rag._publish_corpus_quality_report(
        document, chunks, parameters=parameters, structural_ranges=set())

    manifest_path = rag._artifact_completion_path(chunks, stage="chunking")
    payload = json.loads(manifest_path.read_text(encoding="utf-8"))
    payload["structure_profile"] = rag._document_profiles.profile_provenance(
        rag._document_profiles.get_profile("roman-parts-book-v1"))
    rag._atomic_write_json(manifest_path, payload)

    with pytest.raises(ValueError, match="detached from parameters"):
        rag._load_index_records_strict(chunks)


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
    with pytest.raises(
            ValueError,
            match="does not match chunks artifact|does not bind this chunks"):
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

    assert parameters["endnote_serialization_version"] == 10
    assert parameters["footnote_dialect"] == "pandoc"
    assert parameters["currency_escape_version"] == 3
    assert parameters["source_literal_escape_version"] == 5
    assert parameters["table_cell_padding_version"] == 2
    assert parameters["emphasis_marker"] == "_"
    assert parameters["thematic_break_normalization_version"] == 2
    assert parameters["implicit_reference_escape_version"] == 2
    assert parameters["ordered_list_literal_escape_version"] == 1
    assert parameters["split_matter_partition_version"] == 1
    assert parameters["heading_hierarchy_version"] == 2
    assert parameters["case_heading_version"] == 2
    assert parameters["markdown_validation_policy_version"] == 5
    assert parameters["markdown_validation"] == "auto"
    assert rag._unified_export_complete(
        chunks, output, parameters=parameters)
    manifest_path = rag._artifact_completion_path(
        output, stage="unified_export")
    manifest = json.loads(manifest_path.read_text(encoding="utf-8"))
    receipt = manifest["markdown_validation"]
    assert receipt["publishable"] is True
    assert receipt["candidate_count"] == 1
    assert receipt["internal"]["status"] == "pass"
    legacy_parameters = {
        **parameters,
        "endnote_serialization_version": 5,
    }
    assert not rag._unified_export_complete(
        chunks, output, parameters=legacy_parameters)
    strict_parameters = {
        **parameters,
        "markdown_validation": "strict",
    }
    assert not rag._unified_export_complete(
        chunks, output, parameters=strict_parameters)
    _write_chunks(chunks, [_record(0, 1, "One", "second generation")])
    assert not rag._unified_export_complete(
        chunks, output, parameters=parameters)


def test_unified_export_resume_rejects_missing_validation_receipt(tmp_path):
    chunks = tmp_path / "chunks.jsonl"
    output = tmp_path / "book.md"
    parameters = rag._markdown_export_parameters(
        include_types=None, exclude_types=None, chapters=None,
        split_chapters=False)
    _write_chunks(chunks, [_record(0, 1, "One", "source text")])
    rag.export_markdown(chunks, output, validation_policy="internal")
    internal_parameters = {
        **parameters,
        "markdown_validation": "internal",
    }
    manifest_path = rag._artifact_completion_path(
        output, stage="unified_export")
    manifest = json.loads(manifest_path.read_text(encoding="utf-8"))
    del manifest["markdown_validation"]
    rag._atomic_write_json(manifest_path, manifest)

    assert not rag._unified_export_complete(
        chunks, output, parameters=internal_parameters)


def test_split_validation_failure_publishes_no_candidate(monkeypatch, tmp_path):
    chunks = tmp_path / "chunks.jsonl"
    output = tmp_path / "book.md"
    chapters = tmp_path / "Chapters"
    _write_chunks(chunks, [
        _record(0, 1, "One", "chapter one"),
        _record(1, 2, "Two", "chapter two"),
    ])

    class FailingSession:
        calls = 0

        def __init__(self, _policy):
            pass

        def validate(self, *_args, **_kwargs):
            self.calls += 1
            if self.calls == 2:
                raise rag._markdown_validation.MarkdownValidationError(
                    "injected second chapter failure")
            return {"first_candidate_only": True}

    monkeypatch.setattr(
        rag._markdown_validation, "MarkdownValidationSession", FailingSession)

    with pytest.raises(
            rag._markdown_validation.MarkdownValidationError,
            match="second chapter failure"):
        rag.export_markdown(
            chunks, output, split_chapters=True,
            chapters_dir=chapters)

    assert not chapters.exists()
    assert not rag._artifact_completion_path(
        chapters, stage="split_export").exists()


def test_unified_and_split_exports_use_per_file_numeric_pandoc_footnotes(
        tmp_path):
    chunks = tmp_path / "chunks.jsonl"
    output = tmp_path / "book.md"
    chapters = tmp_path / "Chapters"
    body = _record(
        0, 1, "One", "A $100 source-backed proposition.")
    body["metadata"].update({
        "content_source": "body",
        "page_start": 10,
        "page_end": 10,
        "stable_id": "chunk_aaaaaaaaaaaaaaaa",
    })
    footnote = _record(
        1, 1, "One", "1. The $50 supporting authority.")
    footnote["metadata"].update({
        "content_type": "footnote",
        "content_source": "footnote",
        "page_start": 10,
        "page_end": 10,
        "stable_id": "chunk_1111111111111111",
        "footnote_parent_stable_id": "chunk_aaaaaaaaaaaaaaaa",
    })
    body["metadata"]["footnote_stable_ids"] = [
        "chunk_1111111111111111"]
    body["metadata"]["cross_references"] = ["Ch.2"]
    second_chapter = _record(
        2, 2, "Two", "A $75 second-chapter proposition.")
    second_chapter["metadata"].update({
        "content_source": "body",
        "page_start": 20,
        "page_end": 20,
        "stable_id": "chunk_bbbbbbbbbbbbbbbb",
        "footnote_stable_ids": ["chunk_2222222222222222"],
    })
    second_footnote = _record(
        3, 2, "Two", "1. The $25 second supporting authority.")
    second_footnote["metadata"].update({
        "content_type": "footnote",
        "content_source": "footnote",
        "page_start": 20,
        "page_end": 20,
        "stable_id": "chunk_2222222222222222",
        "footnote_parent_stable_id": "chunk_bbbbbbbbbbbbbbbb",
    })
    _write_chunks(chunks, [body, footnote, second_chapter, second_footnote])

    rag.export_markdown(chunks, output)
    rag.export_markdown(
        chunks, output, split_chapters=True, chapters_dir=chapters)

    unified = output.read_text(encoding="utf-8")
    split_one = (chapters / "ch01_One.md").read_text(encoding="utf-8")
    split_two = (chapters / "ch02_Two.md").read_text(encoding="utf-8")
    first_definition = (
        r"[^1]: _Related page context; exact source marker unresolved._ "
        r"The \$50 supporting authority.")
    second_unified_definition = (
        r"[^2]: _Related page context; exact source marker unresolved._ "
        r"The \$25 second supporting authority.")
    second_split_definition = (
        r"[^1]: _Related page context; exact source marker unresolved._ "
        r"The \$25 second supporting authority.")
    assert r"A \$100 source-backed proposition." in unified
    assert r"A \$100 source-backed proposition." in split_one
    assert r"A \$75 second-chapter proposition." in unified
    assert r"A \$75 second-chapter proposition." in split_two
    assert "Related page-context note[^1]" in unified
    assert "Related page-context note[^1]" in split_one
    assert "Related page-context note[^2]" in unified
    assert "Related page-context note[^1]" in split_two
    assert first_definition in unified and first_definition in split_one
    assert second_unified_definition in unified
    assert second_split_definition in split_two
    assert unified.count("[^1]") == 2
    assert unified.count("[^2]") == 2
    assert split_one.count("[^1]") == 2
    assert "[^2]" not in split_one
    assert split_two.count("[^1]") == 2
    assert "[^2]" not in split_two
    assert unified.count("## Endnotes") == 1
    assert split_one.count("## Endnotes") == 1
    assert split_two.count("## Endnotes") == 1
    assert split_one.index("**Cross-References:**") < split_one.index(
        "## Endnotes")
    assert split_one.rstrip().endswith(first_definition)
    assert split_two.rstrip().endswith(second_split_definition)
    assert r"A \$100 source-backed proposition" in unified
    assert r"The \$50 supporting authority" in unified
    assert r"A \$100 source-backed proposition" in split_one
    assert r"The \$50 supporting authority" in split_one
    assert r"A \$75 second-chapter proposition" in split_two
    assert r"The \$25 second supporting authority" in split_two
    unescaped_currency = re.compile(r"(?<!\\)\$(?=[^\S\r\n]*\d)")
    assert unescaped_currency.search(unified) is None
    assert unescaped_currency.search(split_one) is None
    assert unescaped_currency.search(split_two) is None
    assert "<details><summary>Footnote" not in unified
    assert "<details><summary>Footnote" not in split_one
    assert "<details><summary>Footnote" not in split_two
    assert '<a name="rag-fn' not in unified
    assert '<a name="rag-fn' not in split_one
    assert '<a name="rag-fn' not in split_two
    assert "](#rag-fn" not in unified
    assert "](#rag-fn" not in split_one
    assert "](#rag-fn" not in split_two
    assert "Back to reference" not in unified
    assert "Back to reference" not in split_one
    assert "Back to reference" not in split_two
    for stable_suffix in (
            "1111111111111111", "2222222222222222",
            "aaaaaaaaaaaaaaaa", "bbbbbbbbbbbbbbbb"):
        assert stable_suffix not in unified
        assert stable_suffix not in split_one
        assert stable_suffix not in split_two


def test_split_export_separates_retained_front_and_back_matter(tmp_path):
    chunks = tmp_path / "chunks.jsonl"
    output = tmp_path / "book.md"
    chapters = tmp_path / "Chapters"
    front = _record(0, 1, "One", "retained preface")
    front["metadata"].update({"chapter_num": None, "page_start": 5})
    chapter = _record(1, 1, "One", "chapter body")
    chapter["metadata"]["page_start"] = 10
    back = _record(2, 1, "One", "retained self-assessment")
    back["metadata"].update({"chapter_num": None, "page_start": 20})
    _write_chunks(chunks, [front, chapter, back])

    rag.export_markdown(
        chunks, output, split_chapters=True, chapters_dir=chapters,
        validation_policy="internal")

    assert {path.name for path in chapters.glob("*.md")} == {
        "front_matter.md", "ch01_One.md", "back_matter.md",
    }
    assert "retained preface" in (
        chapters / "front_matter.md").read_text(encoding="utf-8")
    assert "chapter body" in (
        chapters / "ch01_One.md").read_text(encoding="utf-8")
    assert "retained self-assessment" in (
        chapters / "back_matter.md").read_text(encoding="utf-8")
    assert rag._owned_chapter_filename("back_matter.md")


def test_unassigned_matter_path_renders_standalone_heading_hierarchy():
    record = _record(0, 1, "One", "assessment body")
    record["metadata"].update({
        "chapter_num": None,
        "chapter_title": None,
        "section_path": (
            "Self-Assessment Questions > Chapter 1: Introduction > Question 1"
        ),
    })

    markdown = rag._assemble_markdown([record])

    assert markdown.startswith("# Self-Assessment Questions")
    assert "\n## Chapter 1: Introduction\n" in markdown
    assert "\n### Question 1\n" in markdown


def test_canonical_markdown_emits_visible_physical_page_markers_once():
    first = _record(0, 1, "One", "first page body")
    first["metadata"].update({
        "page_start": 99,
        "page_end": 99,
        "pdf_page_start": 10,
        "pdf_page_end": 10,
    })
    same_page = _record(1, 1, "One", "same page body")
    same_page["metadata"].update({
        "pdf_page_start": 10,
        "pdf_page_end": 10,
    })
    range_record = _record(2, 1, "One", "page range body")
    range_record["metadata"].update({
        "pdf_page_start": 11,
        "pdf_page_end": 12,
    })

    markdown = rag._assemble_markdown([first, same_page, range_record])

    assert markdown.count("[PDF page 10]") == 1
    assert markdown.count("[PDF pages 11–12]") == 1
    assert "[PDF page 99]" not in markdown
    assert markdown.index("# Chapter 1 - One") < markdown.index(
        "[PDF page 10]") < markdown.index("## Section")
    assert markdown.index("[PDF page 10]") < markdown.index("first page body")
    assert markdown.index("same page body") < markdown.index(
        "[PDF pages 11–12]")
    parameters = rag._markdown_export_parameters(
        include_types=None,
        exclude_types=None,
        chapters=None,
        split_chapters=True,
        validation_policy="internal",
    )
    assert parameters["page_marker_version"] == 1


def test_case_caption_heading_is_not_duplicated_in_markdown_record_text():
    caption = "Sample Transit Co. v. Example Harbor Co."
    source_text = (
        f"{caption}\n"
        "321 F.4th 456 (Example Cir. 2025).\n"
        "The operator sought recovery for damaged test equipment."
    )
    record = _record(0, 2, "Sample Allocation", source_text)
    record["metadata"].update({
        "content_type": "case_opinion",
        "section_path": f"Chapter 2 > Allocation Method > {caption}",
        "headings": ["Chapter 2", "Allocation Method", caption],
        "primary_case": caption,
        "page_range": "p. 321",
    })

    markdown = rag._assemble_markdown([record])

    visible_case_headings = re.findall(
        rf"(?m)^#{{1,6}} {re.escape(caption)}$", markdown)
    assert visible_case_headings == [f"### {caption}"]
    assert f"##### {caption}" not in markdown
    assert "321 F.4th 456 (Example Cir. 2025)." in markdown
    assert "The operator sought recovery" in markdown
    assert record["text"] == source_text


def test_split_export_rejects_unassigned_chunks_inside_chapter_span(tmp_path):
    chunks = tmp_path / "chunks.jsonl"
    output = tmp_path / "book.md"
    chapters = tmp_path / "Chapters"
    first = _record(0, 1, "One", "first chapter")
    first["metadata"]["page_start"] = 10
    unassigned = _record(1, 1, "One", "unassigned body")
    unassigned["metadata"].update({"chapter_num": None, "page_start": 15})
    last = _record(2, 2, "Two", "second chapter")
    last["metadata"]["page_start"] = 20
    _write_chunks(chunks, [first, unassigned, last])

    with pytest.raises(ValueError, match="inside the assigned chapter span"):
        rag.export_markdown(
            chunks, output, split_chapters=True, chapters_dir=chapters,
            validation_policy="internal")

    assert not chapters.exists()


def test_split_export_uses_full_snapshot_to_partition_filtered_back_matter(
        tmp_path):
    chunks = tmp_path / "chunks.jsonl"
    output = tmp_path / "book.md"
    chapters = tmp_path / "Chapters"
    chapter = _record(0, 1, "One", "chapter body")
    chapter["metadata"]["page_start"] = 10
    back = _record(1, 1, "One", "retained self-assessment")
    back["metadata"].update({
        "chapter_num": None,
        "page_start": 20,
        "content_type": "notes_and_questions",
    })
    _write_chunks(chunks, [chapter, back])

    rag.export_markdown(
        chunks, output, split_chapters=True, chapters_dir=chapters,
        include_types=["notes_and_questions"],
        validation_policy="internal")

    assert {path.name for path in chapters.glob("*.md")} == {
        "back_matter.md",
    }
    assert "retained self-assessment" in (
        chapters / "back_matter.md").read_text(encoding="utf-8")


def test_split_export_rejects_unplaced_unassigned_matter(tmp_path):
    chunks = tmp_path / "chunks.jsonl"
    output = tmp_path / "book.md"
    chapters = tmp_path / "Chapters"
    chapter = _record(0, 1, "One", "chapter body")
    chapter["metadata"]["page_start"] = 10
    unplaced = _record(1, 1, "One", "matter without a source page")
    unplaced["metadata"]["chapter_num"] = None
    _write_chunks(chunks, [chapter, unplaced])

    with pytest.raises(ValueError, match="without source-page metadata"):
        rag.export_markdown(
            chunks, output, split_chapters=True, chapters_dir=chapters,
            validation_policy="internal")

    assert not chapters.exists()


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
        cloud_key="never-persist-this", ollama_url="http://127.0.0.1",
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


def test_raptor_parameters_bind_ambient_gemini_without_persisting_key(
        monkeypatch):
    kwargs = {
        "embedding_model": "model",
        "cloud_url": "",
        "cloud_model": "",
        "cloud_key": "",
        "ollama_url": "http://127.0.0.1:11434",
        "ollama_model": "local",
        "gemini_key": "",
        "thinking": False,
        "security_policy": release_security.ReleaseSecurityPolicy(
            network_policy="allow-cloud"),
    }
    monkeypatch.delenv("GEMINI_API_KEY", raising=False)
    without_key = rag._raptor_parameters(**kwargs)
    monkeypatch.setenv("GEMINI_API_KEY", "GEMINI_SECRET_CANARY")
    with_key = rag._raptor_parameters(**kwargs)

    assert without_key["gemini_configured"] is False
    assert with_key["gemini_configured"] is True
    assert rag._artifact_parameters_sha256(without_key) != (
        rag._artifact_parameters_sha256(with_key))
    assert "GEMINI_SECRET_CANARY" not in json.dumps(with_key)


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
