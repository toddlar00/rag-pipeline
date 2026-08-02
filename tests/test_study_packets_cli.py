"""Runtime, CLI, and atomic-publication tests for study packets."""

from contextlib import contextmanager, nullcontext
import json
import os
from pathlib import Path
from types import SimpleNamespace

import pytest

import document_profiles
import rag
import study_packets


_SOURCE_SHA256 = "a" * 64
_FINGERPRINT = (1, 2, 3, 4, 5)


def _atomic_temp_name(target: str) -> str:
    nonce = "a" * (8 if os.name == "nt" else 24)
    return f".{target}.{nonce}.tmp"


def _index_binding() -> dict:
    return {
        "backend": "chroma",
        "collection": "ethics",
        "source_sha256": _SOURCE_SHA256,
        "embedding_model": "model-a",
        "snapshot_fingerprint_sha256": (
            rag._study_packet_snapshot_fingerprint_sha256(_FINGERPRINT)),
    }


def _profile_receipt() -> dict:
    return document_profiles.profile_provenance(
        document_profiles.get_profile("us-law-casebook-v1"))


def _record(stable_id: str, text: str = "Rule 1.7 source text",
            **metadata) -> dict:
    metadata.setdefault("content_type", "statutory_excerpt")
    metadata.setdefault("heading_path_ids", ["#/texts/42"])
    metadata.setdefault("source_file", f"{stable_id}.pdf")
    metadata.setdefault("page_range", "12")
    metadata.setdefault("section_path", "Chapter 3 > Rules")
    record = {"text": text, "metadata": metadata}
    metadata["stable_id"] = rag._chunk_id(record)
    return record


def _write_syllabus(tmp_path: Path, *, entries: list[dict] | None = None,
                    course: str = "Legal Ethics") -> Path:
    payload = {
        "schema": "study-syllabus-v1",
        "course": course,
        "structure_profile": "us-law-casebook-v1",
        "entries": entries or [{
            "id": "week-03",
            "title": "Conflicts",
            "chapters": ["#/texts/42"],
        }],
    }
    path = tmp_path / "syllabus.json"
    path.write_text(json.dumps(payload), encoding="utf-8")
    return path


def _patch_runtime(
        monkeypatch, tmp_path: Path, records: list[dict], *,
        search_response=None, call_llm=None) -> tuple[Path, Path]:
    chunks_path = tmp_path / "run" / "chunks.jsonl"
    chunks_path.parent.mkdir(parents=True, exist_ok=True)
    db_dir = tmp_path / "db"
    db_dir.mkdir()
    monkeypatch.setattr(
        rag,
        "_load_packet_snapshot_strict",
        lambda _path: (
            records, _SOURCE_SHA256, _FINGERPRINT, _profile_receipt()),
    )
    monkeypatch.setattr(
        rag,
        "_load_study_packet_index_binding",
        lambda _db, **_kwargs: _index_binding(),
    )
    monkeypatch.setattr(
        rag, "_vector_store_lock", lambda *_args, **_kwargs: nullcontext())
    if search_response is not None:
        monkeypatch.setattr(
            rag, "search_index", lambda *_args, **_kwargs: search_response)
    monkeypatch.setattr(
        rag, "_call_llm", call_llm or (lambda *_args, **_kwargs: None))
    monkeypatch.setattr(
        rag._markdown_validation,
        "validate_markdown_candidate",
        lambda _markdown, **_kwargs: {"publishable": True},
    )
    return chunks_path, db_dir


def _build(monkeypatch, tmp_path: Path, records: list[dict], *,
           syllabus: Path | None = None, search_response=None,
           call_llm=None) -> tuple[Path, Path, Path]:
    chunks_path, db_dir = _patch_runtime(
        monkeypatch, tmp_path, records,
        search_response=search_response, call_llm=call_llm)
    syllabus_path = syllabus or _write_syllabus(tmp_path)
    rag.build_study_packets(
        syllabus_path,
        chunks_path=chunks_path,
        db_dir=db_dir,
        collection_name="ethics",
        embedding_model="model-a",
    )
    return chunks_path.parent / "packets", chunks_path, db_dir


def test_packets_invalid_syllabus_exits_2_without_staging(tmp_path, capsys):
    syllabus = tmp_path / "syllabus.json"
    syllabus.write_text("not json", encoding="utf-8")
    with pytest.raises(SystemExit) as excinfo:
        rag.build_study_packets(
            syllabus,
            chunks_path=tmp_path / "run" / "chunks.jsonl",
            db_dir=tmp_path / "db",
        )
    assert excinfo.value.code == 2
    assert "strict JSON" in capsys.readouterr().out
    assert not (tmp_path / "run" / "packets").exists()
    assert not list(tmp_path.glob(".*study-packet-stage-*"))


def test_packet_snapshot_reads_completion_and_quality_under_one_lease(
        monkeypatch, tmp_path):
    chunks = tmp_path / "chunks.jsonl"
    records = [_record("chunk_s")]
    state = {"active": False, "completion": False, "quality": False}

    @contextmanager
    def lease(_path):
        assert not state["active"]
        state["active"] = True
        try:
            yield
        finally:
            state["active"] = False

    binding = rag._IndexChunkCompletionBinding(
        inputs={"source": "bound"},
        parameters_sha256="b" * 64,
        structure_profile=_profile_receipt(),
    )
    monkeypatch.setattr(rag, "_chunk_output_lease", lease)
    monkeypatch.setattr(
        rag, "_read_index_artifact_snapshot",
        lambda _path: (b"record", _SOURCE_SHA256, _FINGERPRINT),
    )
    monkeypatch.setattr(
        rag, "_parse_index_records_strict", lambda _raw, _path: records)

    def completion(*_args, **_kwargs):
        assert state["active"]
        state["completion"] = True
        return binding

    def quality(*_args, **kwargs):
        assert state["active"]
        assert kwargs["input_bindings"] == binding.inputs
        assert kwargs["parameters_sha256"] == binding.parameters_sha256
        state["quality"] = True
        return None, None, None

    monkeypatch.setattr(
        rag, "_load_index_chunk_completion_binding", completion)
    monkeypatch.setattr(rag, "_validated_quality_report_binding", quality)

    result = rag._load_packet_snapshot_strict(chunks)
    assert result == (
        records, _SOURCE_SHA256, _FINGERPRINT, _profile_receipt())
    assert state == {"active": False, "completion": True, "quality": True}


def test_packet_snapshot_missing_completion_fails_inside_lease(
        monkeypatch, tmp_path):
    active = False

    @contextmanager
    def lease(_path):
        nonlocal active
        active = True
        try:
            yield
        finally:
            active = False

    monkeypatch.setattr(rag, "_chunk_output_lease", lease)
    monkeypatch.setattr(
        rag, "_read_index_artifact_snapshot",
        lambda _path: (b"record", _SOURCE_SHA256, _FINGERPRINT),
    )
    monkeypatch.setattr(
        rag, "_parse_index_records_strict",
        lambda _raw, _path: [_record("chunk_s")],
    )

    def missing(*_args, **_kwargs):
        assert active
        raise ValueError("adjacent chunk completion is required")

    monkeypatch.setattr(
        rag, "_load_index_chunk_completion_binding", missing)
    with pytest.raises(ValueError, match="completion"):
        rag._load_packet_snapshot_strict(tmp_path / "chunks.jsonl")
    assert not active


def test_canonical_snapshot_maps_table_child_to_trusted_parent():
    source = _record(
        "table_parent",
        "| Rule | Meaning |\n|---|---|\n| A | 1 |\n| B | 2 |\n"
        "| C | 3 |\n| D | 4 |",
        content_type="table", content_source="table")
    expanded = rag._table_retrieval_core.expand_table_records(
        [source], stable_id_fn=lambda record: record["metadata"]["stable_id"],
        token_count_fn=lambda text: len(text.split()))
    parent, *children = expanded
    for child in children:
        child["metadata"]["stable_id"] = rag._chunk_id(child)
    child_id = children[0]["metadata"]["stable_id"]
    snapshot = rag._study_packet_canonical_snapshot(
        [parent, *children], source_sha256=_SOURCE_SHA256,
        fingerprint=_FINGERPRINT, structure_profile=_profile_receipt())
    assert snapshot.records == (parent,)
    assert snapshot.records_by_retrieval_id[child_id] is parent


@pytest.mark.parametrize(
    "stored_id",
    ["chunk_s", "chunk_0000000000000000", "chunk_AAAAAAAAAAAAAAAA"],
)
def test_canonical_snapshot_rejects_malformed_or_detached_stable_ids(
        stored_id):
    record = _record("chunk_s")
    record["metadata"]["stable_id"] = stored_id
    with pytest.raises(ValueError, match="stable ID"):
        rag._study_packet_canonical_snapshot(
            [record], source_sha256=_SOURCE_SHA256,
            fingerprint=_FINGERPRINT,
            structure_profile=_profile_receipt(),
        )


@pytest.mark.parametrize(
    ("field", "value"),
    [
        ("backend", "qdrant"),
        ("collection", "other"),
        ("embedding_model", "model-b"),
        ("source_sha256", "b" * 64),
    ],
)
def test_packet_manifest_mismatch_is_rejected(
        monkeypatch, tmp_path, field, value):
    db_dir = tmp_path / "db"
    db_dir.mkdir()
    record = _record("chunk_s")
    snapshot = rag._study_packet_canonical_snapshot(
        [record], source_sha256=_SOURCE_SHA256,
        fingerprint=_FINGERPRINT, structure_profile=_profile_receipt())
    manifest = {
        "schema_version": rag.INDEX_MANIFEST_SCHEMA_VERSION,
        "backend": "chroma",
        "collection": "ethics",
        "embedding_model": "model-a",
        "source_sha256": _SOURCE_SHA256,
        "source_record_count": 1,
    }
    manifest[field] = value
    observed = {}

    def load(_db, **kwargs):
        observed["warning_fn"] = kwargs["warning_fn"]
        return manifest

    monkeypatch.setattr(rag._index_state, "_load_index_manifest", load)
    with pytest.raises(ValueError, match=field):
        rag._load_study_packet_index_binding(
            db_dir, db_backend="chroma", collection_name="ethics",
            embedding_model="model-a", snapshot=snapshot)
    observed["warning_fn"]("message %s", "argument")


@pytest.mark.parametrize("missing", ["schema_version", "source_record_count"])
def test_packet_manifest_requires_current_schema_and_record_count(
        monkeypatch, tmp_path, missing):
    db_dir = tmp_path / "db"
    db_dir.mkdir()
    record = _record("chunk_s")
    snapshot = rag._study_packet_canonical_snapshot(
        [record], source_sha256=_SOURCE_SHA256,
        fingerprint=_FINGERPRINT, structure_profile=_profile_receipt())
    manifest = {
        "schema_version": rag.INDEX_MANIFEST_SCHEMA_VERSION,
        "backend": "chroma",
        "collection": "ethics",
        "embedding_model": "model-a",
        "source_sha256": _SOURCE_SHA256,
        "source_record_count": 1,
    }
    del manifest[missing]
    monkeypatch.setattr(
        rag._index_state, "_load_index_manifest",
        lambda *_args, **_kwargs: manifest)
    expected = "schema" if missing == "schema_version" else missing
    with pytest.raises(ValueError, match=expected):
        rag._load_study_packet_index_binding(
            db_dir, db_backend="chroma", collection_name="ethics",
            embedding_model="model-a", snapshot=snapshot)


def test_packet_manifest_rejects_boolean_source_record_count(
        monkeypatch, tmp_path):
    db_dir = tmp_path / "db"
    db_dir.mkdir()
    record = _record("chunk_s")
    snapshot = rag._study_packet_canonical_snapshot(
        [record], source_sha256=_SOURCE_SHA256,
        fingerprint=_FINGERPRINT, structure_profile=_profile_receipt())
    manifest = {
        "schema_version": rag.INDEX_MANIFEST_SCHEMA_VERSION,
        "backend": "chroma",
        "collection": "ethics",
        "embedding_model": "model-a",
        "source_sha256": _SOURCE_SHA256,
        "source_record_count": True,
    }
    monkeypatch.setattr(
        rag._index_state, "_load_index_manifest",
        lambda *_args, **_kwargs: manifest)

    with pytest.raises(ValueError, match="source_record_count"):
        rag._load_study_packet_index_binding(
            db_dir, db_backend="chroma", collection_name="ethics",
            embedding_model="model-a", snapshot=snapshot)


@pytest.mark.parametrize(
    "fingerprint",
    [(), (1, 2, 3, 4), (1, 2, 3, 4, True), (1, 2, 3, 4, -1)],
)
def test_snapshot_fingerprint_binding_is_strict(fingerprint):
    with pytest.raises(ValueError, match="fingerprint"):
        rag._study_packet_snapshot_fingerprint_sha256(fingerprint)


def test_query_hits_are_ids_only_and_warnings_reach_packet(
        monkeypatch, tmp_path):
    trusted = _record("chunk_s", "TRUSTED RULE BYTES")
    trusted_id = trusted["metadata"]["stable_id"]
    response = SimpleNamespace(
        hits=[
            SimpleNamespace(
                source_id=trusted_id,
                text="POISONED VECTOR TEXT",
                metadata={
                    "stable_id": "chunk_0000000000000000",
                    "source_file": "evil.md",
                },
            ),
            SimpleNamespace(
                source_id="",
                text="MISSING-ID VECTOR TEXT",
                metadata={
                    "stable_id": trusted_id,
                    "source_file": "also-evil.md",
                },
            ),
        ],
        warnings=["hybrid corpus unavailable"],
        requested_mode="auto",
        effective_mode="vector",
    )
    syllabus = _write_syllabus(tmp_path, entries=[{
        "id": "week-03", "title": "Conflicts",
        "queries": ["current-client conflict"],
    }])
    output_dir, _, _ = _build(
        monkeypatch, tmp_path, [trusted], syllabus=syllabus,
        search_response=response)
    packet = (output_dir / "week-03.md").read_text(encoding="utf-8")
    assert "TRUSTED RULE BYTES" in packet
    assert "POISONED VECTOR TEXT" not in packet
    assert "MISSING-ID VECTOR TEXT" not in packet
    assert "evil.md" not in packet
    assert "also-evil.md" not in packet
    assert "hybrid corpus unavailable" in packet
    assert "retrieval mode degraded" in packet
    assert "no stable ID" in packet


def test_table_child_hit_is_mapped_to_attested_parent_end_to_end(
        monkeypatch, tmp_path):
    source = _record(
        "table_parent",
        "| Rule | Meaning |\n|---|---|\n| A | one |\n| B | two |\n"
        "| C | three |\n| D | four |",
        content_type="table",
        content_source="table",
    )
    parent, *children = rag._table_retrieval_core.expand_table_records(
        [source],
        stable_id_fn=lambda record: record["metadata"]["stable_id"],
        token_count_fn=lambda text: len(text.split()),
    )
    for child in children:
        child["metadata"]["stable_id"] = rag._chunk_id(child)
    child_id = children[0]["metadata"]["stable_id"]
    response = SimpleNamespace(
        hits=[SimpleNamespace(
            source_id=child_id,
            text="POISONED CHILD TEXT",
            metadata={"stable_id": "chunk_0000000000000000"},
        )],
        warnings=[],
        requested_mode="vector",
        effective_mode="vector",
    )
    syllabus = _write_syllabus(tmp_path, entries=[{
        "id": "week-03", "title": "Table rules", "queries": ["Rule A"],
    }])
    chunks_path, db_dir = _patch_runtime(
        monkeypatch, tmp_path, [parent, *children],
        search_response=response,
    )
    validation_calls = []

    def validate(markdown, **kwargs):
        validation_calls.append((markdown, kwargs))
        return {"publishable": True}

    monkeypatch.setattr(
        rag._markdown_validation, "validate_markdown_candidate", validate)
    rag.build_study_packets(
        syllabus,
        chunks_path=chunks_path,
        db_dir=db_dir,
        collection_name="ethics",
        embedding_model="model-a",
    )
    output_dir = chunks_path.parent / "packets"
    packet = (output_dir / "week-03.md").read_text(encoding="utf-8")
    assert "| D | four |" in packet
    assert "<!-- TABLE -->" in packet
    assert "POISONED CHILD TEXT" not in packet
    assert "mapped retrieval\\-only table row" in packet
    packet_validation = next(
        kwargs for _markdown, kwargs in validation_calls
        if kwargs["source_name"] == "packets/week-03.md")
    index_validation = next(
        kwargs for _markdown, kwargs in validation_calls
        if kwargs["source_name"] == "packets/index.md")
    assert packet_validation["expected_table_count"] == 1
    assert packet_validation["require_table_markers"] is True
    assert index_validation["require_table_markers"] is False


def test_handler_forwards_complete_llm_contract_and_degrades_budget_failure(
        monkeypatch, tmp_path):
    observed = []

    def call_llm(prompt, **kwargs):
        observed.append((prompt, kwargs))
        raise rag.LLMBudgetExceeded("budget exhausted")

    chunks_path, db_dir = _patch_runtime(
        monkeypatch, tmp_path, [_record("chunk_s")], call_llm=call_llm)
    policy = object()
    rag.build_study_packets(
        _write_syllabus(tmp_path),
        chunks_path=chunks_path,
        db_dir=db_dir,
        collection_name="ethics",
        embedding_model="model-a",
        cloud_url="https://cloud.example/v1",
        cloud_model="cloud-model",
        cloud_key="cloud-key",
        ollama_url="http://localhost:11434",
        ollama_model="local-model",
        gemini_key="gemini-key",
        llm_workers=7,
        thinking=True,
        security_policy=policy,
    )
    assert len(observed) == 2
    for _prompt, kwargs in observed:
        assert kwargs["cloud_url"] == "https://cloud.example/v1"
        assert kwargs["cloud_model"] == "cloud-model"
        assert kwargs["cloud_key"] == "cloud-key"
        assert kwargs["ollama_url"] == "http://localhost:11434"
        assert kwargs["ollama_model"] == "local-model"
        assert kwargs["gemini_key"] == "gemini-key"
        assert kwargs["llm_workers"] == 7
        assert kwargs["thinking"] is True
        assert kwargs["security_policy"] is policy
        assert kwargs["max_tokens"] == 4096
        assert kwargs["timeout"] == 60
        assert kwargs["operation"] == "study_packet_outline"
        assert kwargs["output_contract_id"] == (
            study_packets.OUTLINE_CONTRACT_ID)
        assert kwargs["output_fallback_id"] == (
            study_packets.OUTLINE_FALLBACK_ID)
        assert kwargs["output_validator"].contract_id == (
            study_packets.OUTLINE_CONTRACT_ID)
    packet = chunks_path.parent / "packets" / "week-03.md"
    assert "Outline omitted" in packet.read_text(encoding="utf-8")


def test_strict_validator_unavailability_is_run_fatal_and_cleans_stage(
        monkeypatch, tmp_path):
    chunks_path, db_dir = _patch_runtime(
        monkeypatch, tmp_path, [_record("chunk_s")])

    def unavailable(*_args, **_kwargs):
        raise rag._markdown_validation.MarkdownValidationError(
            "strict Markdown validation requires pinned Pandoc")

    monkeypatch.setattr(
        rag._markdown_validation, "validate_markdown_candidate", unavailable)
    with pytest.raises(SystemExit) as excinfo:
        rag.build_study_packets(
            _write_syllabus(tmp_path), chunks_path=chunks_path, db_dir=db_dir,
            collection_name="ethics", embedding_model="model-a")
    assert excinfo.value.code == 1
    assert not (chunks_path.parent / "packets").exists()
    assert not list(chunks_path.parent.glob(
        ".packets.study-packet-stage-*"))


def test_non_us_casebook_profile_is_rejected_before_staging(
        monkeypatch, tmp_path):
    chunks_path, db_dir = _patch_runtime(
        monkeypatch, tmp_path, [_record("chunk_s")])
    syllabus = _write_syllabus(tmp_path)
    payload = json.loads(syllabus.read_text(encoding="utf-8"))
    payload["structure_profile"] = "roman-parts-book-v1"
    syllabus.write_text(json.dumps(payload), encoding="utf-8")

    with pytest.raises(SystemExit) as excinfo:
        rag.build_study_packets(
            syllabus, chunks_path=chunks_path, db_dir=db_dir,
            collection_name="ethics", embedding_model="model-a")
    assert excinfo.value.code == 1
    assert not (chunks_path.parent / "packets").exists()
    assert not list(chunks_path.parent.glob(
        ".packets.study-packet-stage-*"))


def test_staged_packet_parse_failure_is_run_fatal_and_cleans_stage(
        monkeypatch, tmp_path):
    chunks_path, db_dir = _patch_runtime(
        monkeypatch, tmp_path, [_record("chunk_s")])

    def reject_staged_packet(*_args, **_kwargs):
        raise ValueError("injected staged packet failure")

    monkeypatch.setattr(
        rag, "_parse_study_packet_file", reject_staged_packet)
    with pytest.raises(SystemExit) as excinfo:
        rag.build_study_packets(
            _write_syllabus(tmp_path), chunks_path=chunks_path, db_dir=db_dir,
            collection_name="ethics", embedding_model="model-a")
    assert excinfo.value.code == 1
    assert not (chunks_path.parent / "packets").exists()
    assert not list(chunks_path.parent.glob(
        ".packets.study-packet-stage-*"))


def test_unexpected_llm_exception_propagates_and_cleans_stage(
        monkeypatch, tmp_path):
    def programming_error(*_args, **_kwargs):
        raise AssertionError("programmer invariant")

    chunks_path, db_dir = _patch_runtime(
        monkeypatch, tmp_path, [_record("chunk_s")],
        call_llm=programming_error)
    with pytest.raises(AssertionError, match="programmer invariant"):
        rag.build_study_packets(
            _write_syllabus(tmp_path), chunks_path=chunks_path, db_dir=db_dir,
            collection_name="ethics", embedding_model="model-a")
    assert not list(chunks_path.parent.glob(
        ".packets.study-packet-stage-*"))


def test_packets_cli_forwards_provider_security_telemetry_and_qdrant_default(
        monkeypatch, tmp_path):
    captured = {}

    def handler(syllabus_path, **kwargs):
        captured["syllabus"] = syllabus_path
        captured.update(kwargs)

    monkeypatch.setattr(rag, "build_study_packets", handler)
    syllabus = _write_syllabus(tmp_path)
    rag.main([
        "packets", "--syllabus", str(syllabus),
        "--db-backend", "qdrant",
        "--collection", "ethics",
        "--embedding-model", "model-a",
        "--ollama-model", "local-study-model",
        "--llm-workers", "3",
        "--run-id", "packet-run-1",
    ])
    assert captured["syllabus"] == syllabus
    assert captured["db_dir"] == rag.DEFAULT_QDRANT_DIR
    assert captured["db_backend"] == "qdrant"
    assert captured["collection_name"] == "ethics"
    assert captured["ollama_model"] == "local-study-model"
    assert captured["llm_workers"] == 3
    assert captured["security_policy"].network_policy == "local-only"


def test_packet_markers_hex_encode_hostile_comment_terminators(
        monkeypatch, tmp_path):
    syllabus = _write_syllabus(
        tmp_path, course="Legal --> Ethics -- comment",
        entries=[{
            "id": "week--03", "title": "Conflict --> doctrine",
            "chapters": ["#/texts/42"],
        }])
    output_dir, _, _ = _build(
        monkeypatch, tmp_path, [_record("chunk_s")], syllabus=syllabus)
    for path in (output_dir / "week--03.md", output_dir / "index.md"):
        marker = path.read_text(encoding="utf-8").splitlines()[-1]
        payload = marker.split(":", 1)[1].split(" ", 1)[0]
        assert payload
        assert set(payload) <= set("0123456789abcdef")
        assert "Conflict -->" not in marker
    rag._load_study_packet_generation(output_dir)


def test_rerun_removes_only_index_owned_stale_packets(monkeypatch, tmp_path):
    records = [
        _record("chunk_a", heading_path_ids=["#/texts/42"]),
        _record("chunk_b", heading_path_ids=["#/texts/43"]),
    ]
    first = _write_syllabus(tmp_path, entries=[
        {"id": "a", "title": "A", "chapters": ["#/texts/42"]},
        {"id": "b", "title": "B", "chapters": ["#/texts/43"]},
    ])
    output_dir, chunks_path, db_dir = _build(
        monkeypatch, tmp_path, records, syllabus=first)
    assert (output_dir / "b.md").is_file()

    second = _write_syllabus(tmp_path, entries=[
        {"id": "a", "title": "A", "chapters": ["#/texts/42"]},
    ])
    rag.build_study_packets(
        second, chunks_path=chunks_path, db_dir=db_dir,
        collection_name="ethics", embedding_model="model-a")
    assert (output_dir / "a.md").is_file()
    assert not (output_dir / "b.md").exists()
    generation = rag._load_study_packet_generation(output_dir)
    assert set(generation.records) == {"a.md"}


def test_recovery_removes_an_empty_stage_only_orphan(tmp_path):
    output_dir = tmp_path / "packets"
    transaction_id = "1" * 32
    stage_dir = rag._study_packet_stage_path(output_dir, transaction_id)
    stage_dir.mkdir()

    rag._recover_study_packet_transactions(output_dir)

    assert not stage_dir.exists()


def test_recovery_removes_an_index_atomic_temp_only_orphan(tmp_path):
    output_dir = tmp_path / "packets"
    transaction_id = "4" * 32
    stage_dir = rag._study_packet_stage_path(output_dir, transaction_id)
    stage_dir.mkdir()
    (stage_dir / _atomic_temp_name("index.md")).write_text(
        "partial private index", encoding="utf-8")

    rag._recover_study_packet_transactions(output_dir)

    assert not stage_dir.exists()


def test_recovery_removes_an_index_bound_partial_stage(
        monkeypatch, tmp_path):
    records = [
        _record("chunk_a", heading_path_ids=["#/texts/42"]),
        _record("chunk_b", heading_path_ids=["#/texts/43"]),
    ]
    syllabus = _write_syllabus(tmp_path, entries=[
        {"id": "a", "title": "A", "chapters": ["#/texts/42"]},
        {"id": "b", "title": "B", "chapters": ["#/texts/43"]},
    ])
    output_dir, _, _ = _build(
        monkeypatch, tmp_path, records, syllabus=syllabus)
    before = {
        entry.name: entry.read_bytes() for entry in output_dir.iterdir()
    }
    transaction_id = "2" * 32
    stage_dir = rag._study_packet_stage_path(output_dir, transaction_id)
    stage_dir.mkdir()
    (stage_dir / "index.md").write_bytes(before["index.md"])
    (stage_dir / "a.md").write_bytes(before["a.md"])

    rag._recover_study_packet_transactions(output_dir)

    assert not stage_dir.exists()
    assert {
        entry.name: entry.read_bytes() for entry in output_dir.iterdir()
    } == before


def test_recovery_removes_an_index_bound_packet_atomic_temp(
        monkeypatch, tmp_path):
    records = [_record("chunk_a", heading_path_ids=["#/texts/42"])]
    syllabus = _write_syllabus(tmp_path, entries=[
        {"id": "a", "title": "A", "chapters": ["#/texts/42"]},
    ])
    output_dir, _, _ = _build(
        monkeypatch, tmp_path, records, syllabus=syllabus)
    before = {
        entry.name: entry.read_bytes() for entry in output_dir.iterdir()
    }
    transaction_id = "5" * 32
    stage_dir = rag._study_packet_stage_path(output_dir, transaction_id)
    stage_dir.mkdir()
    (stage_dir / "index.md").write_bytes(before["index.md"])
    (stage_dir / _atomic_temp_name("a.md")).write_text(
        "partial private packet", encoding="utf-8")

    rag._recover_study_packet_transactions(output_dir)

    assert not stage_dir.exists()
    assert {
        entry.name: entry.read_bytes() for entry in output_dir.iterdir()
    } == before


def test_recovery_retains_an_unowned_partial_stage(tmp_path):
    output_dir = tmp_path / "packets"
    transaction_id = "3" * 32
    stage_dir = rag._study_packet_stage_path(output_dir, transaction_id)
    stage_dir.mkdir()
    foreign = stage_dir / "notes.txt"
    foreign.write_text("user-owned", encoding="utf-8")

    with pytest.raises(ValueError, match="candidate index"):
        rag._recover_study_packet_transactions(output_dir)

    assert stage_dir.is_dir()
    assert foreign.read_text(encoding="utf-8") == "user-owned"


def test_pre_mutation_promotion_failure_discards_current_stage(
        monkeypatch, tmp_path):
    records = [_record("chunk_s")]
    output_dir, chunks_path, db_dir = _build(monkeypatch, tmp_path, records)
    foreign = output_dir / "notes.txt"
    foreign.write_text("user-owned", encoding="utf-8")
    before = {
        entry.name: entry.read_bytes() for entry in output_dir.iterdir()
    }

    with pytest.raises(SystemExit) as excinfo:
        rag.build_study_packets(
            _write_syllabus(tmp_path, course="Changed Course"),
            chunks_path=chunks_path,
            db_dir=db_dir,
            collection_name="ethics",
            embedding_model="model-a",
        )

    assert excinfo.value.code == 1
    assert {
        entry.name: entry.read_bytes() for entry in output_dir.iterdir()
    } == before
    assert not list(output_dir.parent.glob(
        ".packets.study-packet-stage-*"))
    assert not list(output_dir.parent.glob(
        ".packets.study-packet-backup-*"))


def test_failed_final_index_commit_restores_prior_generation(
        monkeypatch, tmp_path):
    records = [_record("chunk_s")]
    output_dir, chunks_path, db_dir = _build(monkeypatch, tmp_path, records)
    before = rag._load_study_packet_generation(output_dir)
    before_bytes = {
        entry.name: entry.read_bytes() for entry in output_dir.iterdir()
    }
    syllabus = _write_syllabus(tmp_path, course="Changed Course")
    real_replace = rag.os.replace
    failed = False

    def fail_index_commit(source, destination):
        nonlocal failed
        destination = Path(destination)
        source = Path(source)
        if (not failed and destination == output_dir / "index.md"
                and ".study-packet-stage-" in source.parent.name):
            failed = True
            raise OSError("Dropbox sharing violation")
        return real_replace(source, destination)

    monkeypatch.setattr(rag.os, "replace", fail_index_commit)
    with pytest.raises(SystemExit) as excinfo:
        rag.build_study_packets(
            syllabus, chunks_path=chunks_path, db_dir=db_dir,
            collection_name="ethics", embedding_model="model-a")
    assert excinfo.value.code == 1
    assert failed
    after = rag._load_study_packet_generation(output_dir)
    assert after.generation_id == before.generation_id
    assert {
        entry.name: entry.read_bytes() for entry in output_dir.iterdir()
    } == before_bytes
    assert not list(output_dir.parent.glob(
        ".packets.study-packet-stage-*"))
    assert not list(output_dir.parent.glob(
        ".packets.study-packet-backup-*"))
