import hashlib
import json
from contextlib import contextmanager
from copy import deepcopy
from pathlib import Path
from types import SimpleNamespace

import pytest

import publication_core
import rag
from operation_contracts import IndexOutcome


def _sha256(path: Path) -> str:
    return hashlib.sha256(path.read_bytes()).hexdigest()


def _artifact_binding(run_root: Path, path: Path) -> dict:
    return {
        "path": path.relative_to(run_root).as_posix(),
        "size": path.stat().st_size,
        "sha256": _sha256(path),
    }


@pytest.fixture
def committed_publication(monkeypatch, tmp_path):
    monkeypatch.setattr(rag, "OUTPUT_DIR", tmp_path / "output")
    monkeypatch.setattr(
        rag._markdown_validation,
        "validation_receipt_is_complete",
        lambda *_args, **_kwargs: True,
    )
    paths = rag._output_paths_for_name("Book")
    run_root = paths["doc"].parent
    run_root.mkdir(parents=True)

    source_pdf = tmp_path / "Book.pdf"
    source_pdf.write_bytes(b"%PDF-1.7\nsource\n")
    artifact = run_root / "bound-artifact.bin"
    artifact.write_bytes(b"original artifact bytes")
    binding = _artifact_binding(run_root, artifact)
    oracle_artifact = rag._source_oracle_registry_path(paths["chunks"])
    oracle_artifact.write_bytes(b'{"trusted":"oracle registry"}\n')
    oracle_binding = _artifact_binding(run_root, oracle_artifact)

    db_dir = paths["chroma"]
    db_dir.mkdir()
    manifest = rag._index_manifest_path(
        db_dir, backend="chroma", collection_name="book")
    manifest.write_bytes(b'{"committed":true}\n')

    common = {
        "policy_version": rag._PIPELINE_PUBLICATION_POLICY_VERSION,
        "artifacts": [binding, oracle_binding],
    }
    fidelity_sha256 = "9" * 64
    receipt = publication_core.build_publication_receipt({
        "source_completeness": {
            **common,
            "verification": "quality-bound-source-token-fidelity-v5",
            "input_pdf": rag._publication_input_binding(source_pdf),
            "record_count": 1,
            "eligible_source_items": 1,
            "represented_source_items": 1,
            "coverage_ppm": 1_000_000,
            "eligible_source_tables": 0,
            "represented_source_tables": 0,
            "source_tokens": 3,
            "covered_source_tokens": 3,
            "output_tokens": 3,
            "covered_output_tokens": 3,
            "fidelity_evidence_sha256": fidelity_sha256,
            "source_descriptor_root_sha256": "8" * 64,
            "output_attestation_root_sha256": "7" * 64,
            "source_oracle_root_sha256": "4" * 64,
            "source_oracle_registry_sha256": "3" * 64,
            "conversion_parameters_sha256": "a" * 64,
            "checks": list(rag._SOURCE_PUBLICATION_CHECKS),
        },
        "physical_order": {
            **common,
            "verification": "geometry-reading-order-v6",
            "record_count": 1,
            "unexpected_page_regressions": 0,
            "allowed_page_regressions": 0,
            "page_metadata_issues": 0,
            "chunk_index_issues": 0,
            "geometry_constraints": 0,
            "geometry_violations": 0,
            "order_exemptions": 0,
            "fidelity_evidence_sha256": fidelity_sha256,
            "source_oracle_root_sha256": "4" * 64,
            "geometry_root_sha256": "6" * 64,
            "checks": list(rag._PHYSICAL_PUBLICATION_CHECKS),
        },
        "semantic_hierarchy": {
            **common,
            "verification": "occurrence-bound-heading-lineage-v3",
            "chunk_completion_schema_version": (
                rag.CHUNK_COMPLETION_SCHEMA_VERSION),
            "structure_profile_parameters_sha256": "b" * 64,
            "chunk_parameters_sha256": "c" * 64,
            "record_count": 1,
            "heading_lineage_schema_version": (
                rag._heading_lineage.HEADING_LINEAGE_SCHEMA_VERSION),
            "source_heading_items": 0,
            "attachable_source_heading_items": 0,
            "attached_source_heading_items": 0,
            "directly_owned_source_heading_items": 0,
            "exception_source_heading_items": 0,
            "artifact_source_heading_items": 0,
            "unattached_source_heading_items": 0,
            "invalid_heading_binding_records": 0,
            "heading_lineage_evidence_sha256": "5" * 64,
            "raptor_requested": False,
            "raptor_parameters_sha256": None,
            "checks": list(rag._SEMANTIC_PUBLICATION_CHECKS),
        },
        "markdown_validity": {
            **common,
            "verification": "validated-markdown-completions-v1",
            "validation_policy": "strict",
            "unified_validation": {"candidate_count": 1},
            "split_validation": None,
            "split_requested": False,
            "unified_parameters_sha256": "d" * 64,
            "split_parameters_sha256": None,
            "unified_output_count": 1,
            "split_output_count": 0,
        },
        "vector_store_parity": {
            **common,
            "verification": "exact-physical-identity-and-position-v1",
            "backend": "chroma",
            "collection": "book",
            "embedding_model": "test-model",
            "embedding_dimension": 8,
            "record_count": 1,
            "physical_count": 1,
            "chunks_sha256": "e" * 64,
            "quality_report_schema_version": (
                rag._quality_core.QUALITY_REPORT_SCHEMA_VERSION),
            "quality_report_sha256": "f" * 64,
            "index_manifest_schema_version": rag.INDEX_MANIFEST_SCHEMA_VERSION,
            "db_path": db_dir.relative_to(run_root).as_posix(),
            "index_manifest_sha256": _sha256(manifest),
        },
    })

    def validate_generation(current_receipt, *_args, **_kwargs):
        vector = next(
            gate["evidence"] for gate in current_receipt["gates"]
            if gate["name"] == "vector_store_parity")
        marker = rag._index_update_marker_path(
            db_dir, backend="chroma", collection_name="book")
        if marker.exists() or _sha256(manifest) != vector[
                "index_manifest_sha256"]:
            raise ValueError("test vector generation is stale")

    monkeypatch.setattr(
        rag, "_validate_pipeline_publication_generation",
        validate_generation)
    paths["publication_receipt"].write_bytes(
        publication_core.serialize_publication_receipt(receipt))
    return SimpleNamespace(
        paths=paths,
        source_pdf=source_pdf,
        artifact=artifact,
        oracle_artifact=oracle_artifact,
        db_dir=db_dir,
        manifest=manifest,
        receipt=receipt,
    )


def test_publication_commit_rejects_receipt_and_artifact_tampering(
        committed_publication):
    state = committed_publication
    assert rag._pipeline_publication_complete(
        state.paths, pdf_path=state.source_pdf,
        expected_receipt=state.receipt)

    tampered_receipt = json.loads(
        state.paths["publication_receipt"].read_text(encoding="utf-8"))
    tampered_receipt["gates"][0]["evidence"]["policy_version"] = 1
    state.paths["publication_receipt"].write_text(
        json.dumps(tampered_receipt), encoding="utf-8")
    assert not rag._pipeline_publication_complete(
        state.paths, pdf_path=state.source_pdf)

    state.paths["publication_receipt"].write_bytes(
        publication_core.serialize_publication_receipt(state.receipt))
    state.artifact.write_bytes(b"tampered artifact bytes")
    assert not rag._pipeline_publication_complete(
        state.paths, pdf_path=state.source_pdf)


def test_publication_commit_rejects_dirty_or_changed_vector_state(
        committed_publication):
    state = committed_publication
    assert rag._pipeline_publication_complete(
        state.paths, pdf_path=state.source_pdf)

    marker = rag._index_update_marker_path(
        state.db_dir, backend="chroma", collection_name="book")
    marker.write_text('{"state":"updating"}\n', encoding="utf-8")
    assert not rag._pipeline_publication_complete(
        state.paths, pdf_path=state.source_pdf)

    marker.unlink()
    state.manifest.write_bytes(b'{"committed":false,"tampered":true}\n')
    assert not rag._pipeline_publication_complete(
        state.paths, pdf_path=state.source_pdf)


def test_vector_publication_rechecks_manifest_after_acquiring_lock(
        monkeypatch, tmp_path):
    run_root = tmp_path / "Book"
    db_dir = run_root / "Book_chroma"
    db_dir.mkdir(parents=True)
    manifest = rag._index_manifest_path(
        db_dir, backend="chroma", collection_name="book")
    manifest.write_bytes(b'{"generation":"receipt-bound"}\n')
    old_sha256 = _sha256(manifest)
    relative_manifest = manifest.relative_to(run_root).as_posix()
    events = []

    @contextmanager
    def delayed_lock(*_args, **_kwargs):
        events.append("lock_enter")
        manifest.write_bytes(b'{"generation":"replacement"}\n')
        yield

    def physical_scan(*_args, **_kwargs):
        events.append("physical_scan")
        return 0

    monkeypatch.setattr(rag, "_vector_store_lock", delayed_lock)
    monkeypatch.setattr(
        rag, "_verify_publication_vector_store", physical_scan)
    paths = {
        "chroma": db_dir,
        "qdrant": run_root / "Book_qdrant",
    }
    vector = {
        "backend": "chroma",
        "collection": "book",
        "embedding_model": "test-model",
        "embedding_dimension": 8,
        "physical_count": 0,
        "chunks_sha256": "a" * 64,
        "quality_report_schema_version": (
            rag._quality_core.QUALITY_REPORT_SCHEMA_VERSION),
        "quality_report_sha256": "b" * 64,
        "index_manifest_sha256": old_sha256,
        "db_path": db_dir.relative_to(run_root).as_posix(),
    }
    artifacts = {
        relative_manifest: {
            "path": relative_manifest,
            "size": manifest.stat().st_size,
            "sha256": old_sha256,
        },
    }

    with pytest.raises(ValueError, match="vector publication manifest is stale"):
        rag._validate_pipeline_vector_publication_generation(
            run_root=run_root, paths=paths, gate_artifacts=artifacts,
            vector=vector, records=[], chunks_sha256="a" * 64,
            quality_schema=rag._quality_core.QUALITY_REPORT_SCHEMA_VERSION,
            quality_sha256="b" * 64, vector_store_locked=False)

    assert events == ["lock_enter"]


def test_publication_commit_rejects_source_oracle_registry_tampering(
        committed_publication):
    state = committed_publication
    assert rag._pipeline_publication_complete(
        state.paths, pdf_path=state.source_pdf)

    state.oracle_artifact.write_bytes(b'{"tampered":"oracle registry"}\n')

    assert not rag._pipeline_publication_complete(
        state.paths, pdf_path=state.source_pdf)

def test_publication_requires_null_raptor_binding_when_unrequested(
        committed_publication):
    state = committed_publication
    evidence = {
        gate["name"]: deepcopy(gate["evidence"])
        for gate in state.receipt["gates"]
    }
    evidence["semantic_hierarchy"]["raptor_parameters_sha256"] = "detached"
    malformed = publication_core.build_publication_receipt(evidence)
    state.paths["publication_receipt"].write_bytes(
        publication_core.serialize_publication_receipt(malformed))

    assert not rag._pipeline_publication_complete(
        state.paths, pdf_path=state.source_pdf)


@pytest.mark.parametrize(
    "mutation", [
        "policy", "nonstring_path", "heading_count",
        "oracle_root_mismatch", "oracle_registry_digest",
        "artifact_count", "stale_heading_schema",
        "stale_heading_verification",
    ])
def test_publication_commit_rejects_unsupported_or_malformed_evidence(
        committed_publication, mutation):
    state = committed_publication
    evidence = {
        gate["name"]: deepcopy(gate["evidence"])
        for gate in state.receipt["gates"]
    }
    if mutation == "policy":
        evidence["source_completeness"]["policy_version"] = (
            rag._PIPELINE_PUBLICATION_POLICY_VERSION - 1)
    elif mutation == "nonstring_path":
        evidence["source_completeness"]["artifacts"][0]["path"] = [
            "bound-artifact.bin"]
    elif mutation == "heading_count":
        evidence["semantic_hierarchy"][
            "attachable_source_heading_items"] = 1
    elif mutation == "oracle_root_mismatch":
        evidence["physical_order"]["source_oracle_root_sha256"] = "2" * 64
    elif mutation == "oracle_registry_digest":
        evidence["source_completeness"][
            "source_oracle_registry_sha256"] = "detached"
    elif mutation == "artifact_count":
        evidence["semantic_hierarchy"][
            "artifact_source_heading_items"] = 1
    elif mutation == "stale_heading_schema":
        evidence["semantic_hierarchy"][
            "heading_lineage_schema_version"] = (
                rag._heading_lineage.HEADING_LINEAGE_SCHEMA_VERSION - 1)
    else:
        evidence["semantic_hierarchy"][
            "verification"] = "occurrence-bound-heading-lineage-v2"
    malformed = publication_core.build_publication_receipt(evidence)
    state.paths["publication_receipt"].write_bytes(
        publication_core.serialize_publication_receipt(malformed))

    assert not rag._pipeline_publication_complete(
        state.paths, pdf_path=state.source_pdf)


def _pipeline_args() -> SimpleNamespace:
    return SimpleNamespace(
        collection=None,
        db_backend="chroma",
        embedding_model="test-embedding",
        structure_profile=rag.DEFAULT_STRUCTURE_PROFILE,
        full_reindex=False,
        batch_size=None,
        backend="auto",
        force=False,
        no_preprocess=False,
        max_tokens=512,
        min_words=10,
        dedup_threshold=0.9,
        llm_classify=False,
        zeroshot_classify=False,
        contextualize=False,
        reconstruct_headings=False,
        quality_score=False,
        cloud_url="https://example.test/v1",
        cloud_model="test-model",
        cloud_key="test-key",
        ollama_url="",
        ollama_model="",
        gemini_key="",
        llm_workers=2,
        thinking=False,
        split_chapters=False,
        raptor=False,
        db_lock_timeout=rag.DEFAULT_DB_LOCK_TIMEOUT,
        markdown_validation="strict",
    )


def _stub_pipeline_until_publication(monkeypatch, tmp_path, events):
    monkeypatch.setattr(rag, "OUTPUT_DIR", tmp_path / "output")
    paths = rag._output_paths_for_name("Book")
    for validator in (
            "_converted_outputs_complete",
            "_chunks_complete",
            "_quality_report_complete",
            "_unified_export_complete",
            "_split_export_complete",
            "_raptor_output_complete"):
        monkeypatch.setattr(rag, validator, lambda *_args, **_kwargs: True)
    monkeypatch.setattr(
        rag, "convert_pdf",
        lambda *_args, **_kwargs: events.append("convert"))
    monkeypatch.setattr(
        rag, "chunk_document",
        lambda *_args, **_kwargs: events.append("chunk"))
    monkeypatch.setattr(
        rag, "export_markdown",
        lambda *_args, **_kwargs: events.append("export"))

    def fake_index(*_args, **_kwargs):
        events.append("index")
        return IndexOutcome(
            backend="chroma",
            disposition="unchanged",
            total_records=0,
            changed_records=0,
            unchanged_records=0,
            removed_records=0,
            upserted_records=0,
            batch_count=0,
            physical_count=0,
            committed=True,
        )

    monkeypatch.setattr(rag, "_index_chunks_for_backend", fake_index)
    return paths


def test_pipeline_invokes_publication_as_its_final_stage(
        monkeypatch, tmp_path):
    events = []
    paths = _stub_pipeline_until_publication(monkeypatch, tmp_path, events)
    observed = {}

    def fake_publish(pdf_path, published_paths, **kwargs):
        events.append("publication")
        observed.update({
            "pdf_path": pdf_path,
            "paths": published_paths,
            "kwargs": kwargs,
        })
        return {
            "root_sha256": "0" * 64,
            "gates": [{"name": name} for name in (
                "source_completeness",
                "physical_order",
                "semantic_hierarchy",
                "markdown_validity",
                "vector_store_parity",
            )],
        }

    monkeypatch.setattr(rag, "_publish_pipeline_publication", fake_publish)
    result = rag._run_pipeline_stages(
        Path("Book.pdf"), paths, _pipeline_args(),
        resume=False, watermark=None)

    assert events == ["convert", "chunk", "index", "export", "publication"]
    assert observed["pdf_path"] == Path("Book.pdf")
    assert observed["paths"] is paths
    assert observed["kwargs"]["index_outcome"] is result["index_outcome"]
    assert result["publication_receipt"] == paths["publication_receipt"]


def test_pipeline_attributes_final_commit_failure_to_publication(
        monkeypatch, tmp_path):
    events = []
    paths = _stub_pipeline_until_publication(monkeypatch, tmp_path, events)
    failure = RuntimeError("injected publication failure")

    def fail_publication(*_args, **_kwargs):
        events.append("publication")
        raise failure

    monkeypatch.setattr(rag, "_publish_pipeline_publication", fail_publication)
    with pytest.raises(rag._PipelineStageError) as raised:
        rag._run_pipeline_stages(
            Path("Book.pdf"), paths, _pipeline_args(),
            resume=False, watermark=None)

    assert events[-1] == "publication"
    assert raised.value.stage == "publication"
    assert raised.value.cause is failure
