"""Generated-only exact-record and source-generation evidence controls."""

from contextlib import contextmanager
import copy
from dataclasses import FrozenInstanceError, replace
import hashlib
import json
import os
from pathlib import Path
import subprocess
import sys
from types import SimpleNamespace

import pytest

import index_state
import release_security
import service_contracts
import service_evidence_contracts as contracts
import service_evidence_search as core
from service_evidence_fixtures import build_bundle, physical_binding


POLICY = release_security.ReleaseSecurityPolicy()


def _response(bundle, records=None, *, mode="vector"):
    records = bundle.records if records is None else records
    binding = physical_binding()
    hits = []
    for record in records:
        source_id = binding.chunk_id(record)
        metadata = {**copy.deepcopy(record["metadata"]), "stable_id": source_id}
        metadata.pop("text", None)
        hits.append(SimpleNamespace(source_id=source_id, text=record["text"], metadata=metadata, score=0.75))
    return SimpleNamespace(hits=hits, backend="qdrant", requested_mode=mode,
                           effective_mode="vector", reranker_applied=False, warnings=[])


def _run(bundle, *, response=None, search=None, evidence_config=None, binding=None, request=None):
    response = _response(bundle) if response is None else response
    search = search or (lambda *_args, **_kwargs: response)
    binding = binding or physical_binding(search_index=search)
    return core.execute_evidence_search(
        bundle.config, evidence_config or bundle.evidence_config,
        request or service_contracts.SearchRequest("synthetic-query-secret", limit=50, mode="vector"),
        "req-evidence-unit", binding=binding, security_policy=POLICY)


def _json(path):
    return json.loads(path.read_bytes())


def _write_json(path, value):
    path.write_text(json.dumps(value, ensure_ascii=False, sort_keys=True) + "\n", encoding="utf-8")


def _expect_failure(bundle, **kwargs):
    with pytest.raises(service_contracts.ServiceContractError) as caught:
        _run(bundle, **kwargs)
    assert caught.value.code == "service_unavailable"
    assert "synthetic" not in str(caught.value)
    assert str(bundle.root) not in str(caught.value)


def _legacy_bundle(root, *, records=None):
    """Minimal current index manifest over explicitly unlineaged synthetic rows."""
    root.mkdir(exist_ok=True)
    db_path = root / "qdrant"
    db_path.mkdir()
    chunks_path = root / "legacy.jsonl"
    records = records or [{"text": "Unlineaged synthetic text.", "metadata": {
        "chapter_num": 1, "content_type": "author_narrative", "source_file": "legacy.json"}}]
    chunks_path.write_bytes(b"\n".join(core._canonical(record) for record in records) + b"\n")
    config = service_contracts.CorpusConfig("legacy", db_path, chunks_path, "legacy", "synthetic-model")
    binding = physical_binding()
    manifest = {"schema_version": binding.manifest_schema_version, "backend": "qdrant",
        "collection": "legacy", "embedding_model": "synthetic-model", "embedding_dimension": 4,
        "embedding_input_policy_version": binding.embedding_input_policy_version,
        "model_artifact_lock_sha256": binding.model_artifact_lock_sha256,
        "chunk_hashes": {binding.chunk_id(record): binding.chunk_hash(record) for record in records},
        "source_sha256": hashlib.sha256(chunks_path.read_bytes()).hexdigest(),
        "source_record_count": len(records), "table_child_count": 0,
        "quality_report_schema_version": None, "quality_report_sha256": None}
    manifest_path = index_state._index_manifest_path(db_path, backend="qdrant", collection_name="legacy")
    _write_json(manifest_path, manifest)
    return SimpleNamespace(root=root, config=config, db_path=db_path, chunks_path=chunks_path,
        manifest_path=manifest_path, records=records, evidence_config=contracts.EvidenceCorpusConfig("legacy"))


def test_core_import_does_not_load_physical_runtime():
    result = subprocess.run([sys.executable, "-c", (
        "import sys; import service_evidence_search; "
        "assert not {'rag','qdrant_client','pymupdf','cv2','numpy','torch','rapidocr','onnxruntime'} & set(sys.modules)"
    )], capture_output=True, text=True, timeout=30)
    assert result.returncode == 0, result.stderr


def test_real_policy_generation_returns_exact_scope_and_all_related_states(tmp_path):
    bundle = build_bundle(tmp_path)
    before_records = copy.deepcopy(bundle.records)
    result = _run(bundle)
    assert result["kind"] == "service_evidence_search"
    assert len(result["evidence"]) == 5
    assert [entry["related_page_ocr"]["pages"][0]["status"] for entry in result["evidence"]] == [
        "review_required", "empty_candidate", "retry_failed", "deferred", "not_selected"]
    for page, entry in enumerate(result["evidence"], 1):
        assert entry["source_scope"]["pages"] == [page]
        assert entry["source_scope"]["page_space"] == "original_pdf"
        assert entry["source_scope"]["provenance_scope"] == "record_source_items"
        assert entry["source_scope"]["transforms"] == ["plain"]
        assert entry["source_scope"]["provenance_atom_count"] == 1
        assert entry["text_accuracy"] == "not_verified" and entry["review_scope"] == "unmapped"
        assert entry["record_sha256"] == core._digest(bundle.records[page - 1])
    encoded = json.dumps(result)
    assert bundle.source["sha256"] not in encoded
    assert bundle.source["name"] not in encoded
    assert str(bundle.root) not in encoded
    assert "synthetic-query-secret" not in encoded
    assert "The synthetic defendant is not liable for 25 units." not in encoded
    assert bundle.records == before_records
    assert result == _run(bundle)


def test_exact_policy_pass_and_corrected_candidate_never_mean_adoption(tmp_path):
    bundle = build_bundle(tmp_path)
    assert bundle.quality["status"] == "pass"
    assert "is liable" in bundle.records[0]["text"]
    assert "not liable" in bundle.report["pages"][0]["candidate"]["text"]
    first = _run(bundle)["evidence"][0]
    assert first["text_accuracy"] == "not_verified"
    assert first["review_scope"] == "unmapped"
    assert first["related_page_ocr"] == {"status": "available", "reason": None,
                                       "pages": [{"page_number": 1, "status": "review_required"}]}


@pytest.mark.parametrize("mode,hybrid", [("auto", None), ("vector", False), ("hybrid", True)])
def test_search_preserves_exact_policy_and_arguments_inside_ordered_leases(tmp_path, mode, hybrid):
    bundle = build_bundle(tmp_path)
    events = []

    def lease(name):
        @contextmanager
        def scoped(*args, **kwargs):
            events.append(("enter", name, args, kwargs))
            try:
                yield
            finally:
                events.append(("exit", name))
        return scoped

    def search(*args, **kwargs):
        events.append(("search", args, kwargs))
        return _response(bundle, mode=mode)

    binding = replace(physical_binding(search_index=search), vector_store_lock=lease("db"),
                      chunk_output_lease=lease("chunks"))
    request = service_contracts.SearchRequest("needle", limit=9, mode=mode,
        filters=service_contracts.SearchFilters(content_type="author_narrative", chapter_num=1))
    result = _run(bundle, binding=binding, request=request)
    assert [event[:2] for event in events if event[0] != "search"] == [
        ("enter", "db"), ("enter", "chunks"), ("exit", "chunks"), ("exit", "db")]
    assert events[2][0] == "search"
    assert events[2][1] == ("needle", bundle.db_path)
    assert events[2][2] == {"db_backend": "qdrant", "n_results": 9,
        "content_type": "author_narrative", "chapter_num": 1, "collection_name": bundle.config.collection_name,
        "embedding_model": bundle.config.embedding_model, "use_reranker": False, "hybrid": hybrid,
        "chunks_path": bundle.chunks_path, "lock_timeout": bundle.config.db_lock_timeout_seconds,
        "security_policy": POLICY}
    assert events[2][2]["security_policy"] is POLICY
    assert result["search"]["requested_mode"] == mode


@pytest.mark.parametrize("field,value", [
    ("chapter_title", "forged title"), ("case_names", ["fabricated case"]),
    ("primary_case", "fabricated case"), ("chapter_num", True), ("chapter_num", 1.0),
    ("stable_id", "chunk_ffffffffffffffff"), ("source_file", "private-forged-path"),
    ("extra", {"private": "untrusted"}),
])
def test_full_metadata_forgery_rejected(tmp_path, field, value):
    bundle = build_bundle(tmp_path)
    response = _response(bundle)
    response.hits[0].metadata[field] = value
    _expect_failure(bundle, response=response)


@pytest.mark.parametrize("mutation", ["scope", "bbox", "nested_boolean", "nested_float"])
def test_nested_source_metadata_forgery_rejected(tmp_path, mutation):
    bundle = build_bundle(tmp_path)
    response = _response(bundle)
    item = response.hits[0].metadata["source_items"][0]
    if mutation == "scope":
        item["scope"]["provenance_indexes"] = [1]
    elif mutation == "bbox":
        item["spans"][0]["bbox"][0] += 1
    else:
        item["spans"][0]["page"] = True if mutation == "nested_boolean" else 1.0
    _expect_failure(bundle, response=response)


@pytest.mark.parametrize("mutation", ["text", "unknown_id", "duplicate", "too_many"])
def test_hit_association_must_be_exact_and_unique(tmp_path, mutation):
    bundle = build_bundle(tmp_path)
    response = _response(bundle)
    request = None
    if mutation == "text":
        response.hits[0].text += " changed"
    elif mutation == "unknown_id":
        response.hits[0].source_id = "chunk_ffffffffffffffff"
    elif mutation == "duplicate":
        response.hits[1] = copy.deepcopy(response.hits[0])
    else:
        request = service_contracts.SearchRequest("needle", limit=1, mode="vector")
    _expect_failure(bundle, response=response, request=request)


def test_identical_public_prefix_does_not_hide_full_text_mismatch(tmp_path):
    # One long lexical token exercises the public character boundary without
    # falsely publishing a fixture that exceeds the real embedding token gate.
    prefix = "syntheticprefix" * 2500 + " "
    bundle = build_bundle(tmp_path, texts=(prefix + "FIRST", prefix + "SECOND"), with_recovery=False)
    result = _run(bundle)
    assert result["search"]["hits"][0]["text_truncated"] is True
    assert result["search"]["hits"][0]["text"] == result["search"]["hits"][1]["text"]
    assert result["evidence"][0]["text_sha256"] != result["evidence"][1]["text_sha256"]
    response = _response(bundle)
    response.hits[0].text = bundle.records[1]["text"]
    _expect_failure(bundle, response=response)


@pytest.mark.parametrize("field,value", [
    ("schema_version", True), ("source_record_count", 5.0), ("embedding_dimension", True),
    ("embedding_input_policy_version", 1.0), ("table_child_count", False),
    ("source_sha256", "0" * 64), ("quality_report_sha256", "0" * 64),
    ("model_artifact_lock_sha256", "0" * 64), ("embedding_model", "different-model"),
    ("collection", "different-collection"), ("extra", "unrecognized"),
])
def test_manifest_conflicts_fail_before_search(tmp_path, field, value):
    bundle = build_bundle(tmp_path)
    manifest = _json(bundle.manifest_path)
    manifest[field] = value
    _write_json(bundle.manifest_path, manifest)
    _expect_failure(bundle, search=lambda *_a, **_k: pytest.fail("inconsistent inputs reached retrieval"))


@pytest.mark.parametrize("role", ["manifest_path", "chunks_path", "quality_path", "completion_path", "oracle_path",
                                  "docling_path", "conversion_path", "recovery_path"])
def test_artifact_changed_during_search_rejects_every_generation(tmp_path, role):
    bundle = build_bundle(tmp_path)

    def search(*_args, **_kwargs):
        path = getattr(bundle, role)
        path.write_bytes(path.read_bytes() + b" ")
        return _response(bundle)

    _expect_failure(bundle, search=search)


@pytest.mark.parametrize("when", ["before", "during"])
def test_incomplete_index_marker_fails_closed(tmp_path, when):
    bundle = build_bundle(tmp_path)
    marker = index_state._index_update_marker_path(bundle.db_path, backend="qdrant", collection_name=bundle.config.collection_name)
    if when == "before":
        marker.write_text("{}", encoding="utf-8")

    def search(*_args, **_kwargs):
        if when == "before":
            pytest.fail("dirty index reached retrieval")
        marker.write_text("{}", encoding="utf-8")
        return _response(bundle)

    _expect_failure(bundle, search=search)


def test_quality_only_scope_never_assumes_original_pdf_or_recovery(tmp_path):
    bundle = build_bundle(tmp_path)
    result = _run(bundle, evidence_config=contracts.EvidenceCorpusConfig(bundle.config.corpus_id))
    assert result["generation"]["docling_sha256"] is None
    assert result["generation"]["recovery_sha256"] is None
    for entry in result["evidence"]:
        assert entry["source_scope"]["page_space"] == "conversion_input"
        assert entry["issues"] == ["original_page_mapping_unavailable"]
        assert entry["related_page_ocr"] == {"status": "unavailable", "reason": "not_configured", "pages": []}


def test_table_children_and_duplicate_rows_retain_parent_scope_only(tmp_path):
    bundle = build_bundle(tmp_path, with_table=True)
    result = _run(bundle)
    children = [(record, entry) for record, entry in zip(bundle.records, result["evidence"])
                if record["metadata"].get("retrieval_role") == "table_child"]
    assert len(children) == 4
    assert children[-1][0]["text"] == children[-2][0]["text"]
    assert children[-1][1]["source_id"] != children[-2][1]["source_id"]
    for record, entry in children:
        assert entry["source_scope"]["provenance_scope"] == "table_parent_source_items"
        assert entry["source_scope"]["pages"] == [5]
        assert entry["issues"] == ["table_parent_scope"]
        assert entry["text_sha256"] == hashlib.sha256(record["text"].encode()).hexdigest()
        assert "bbox" not in json.dumps(entry)


def test_multipage_fragments_use_scoped_atoms_not_all_spans_or_page_range(tmp_path):
    bundle = build_bundle(tmp_path, with_multipage=True)
    result = _run(bundle)
    fragments = [(record, entry) for record, entry in zip(bundle.records, result["evidence"])
                 if len(record["metadata"]["source_items"][0]["spans"]) == 2]
    assert len(fragments) == 2
    for (record, entry), expected_page in zip(fragments, [2, 5]):
        scope = entry["source_scope"]
        assert scope["pages"] == [expected_page]
        assert scope["provenance_atom_count"] == 1
    assert fragments[0][1]["source_scope"]["provenance_sha256"] != fragments[1][1]["source_scope"]["provenance_sha256"]


@pytest.mark.parametrize("failure", [KeyboardInterrupt, SystemExit])
def test_baseexception_cancellation_propagates_and_releases_both_leases(tmp_path, failure):
    bundle = build_bundle(tmp_path)
    events = []

    @contextmanager
    def lease(*_args, **_kwargs):
        events.append("enter")
        try:
            yield
        finally:
            events.append("exit")

    def search(*_args, **_kwargs):
        raise failure("private cancellation")

    binding = replace(physical_binding(search_index=search), vector_store_lock=lease, chunk_output_lease=lease)
    with pytest.raises(failure):
        _run(bundle, binding=binding)
    assert events == ["enter", "enter", "exit", "exit"]


def test_binding_is_frozen_and_validates_all_capabilities():
    binding = physical_binding()
    with pytest.raises(FrozenInstanceError):
        binding.chunk_id = lambda _: "invalid"
    for name, value in [("search_index", None), ("manifest_schema_version", True),
                        ("embedding_input_policy_version", 0), ("model_artifact_lock_sha256", "not-a-digest")]:
        with pytest.raises((TypeError, ValueError)):
            replace(binding, **{name: value})


def test_legacy_rows_prove_exact_correspondence_without_fabricating_source_evidence(tmp_path):
    bundle = _legacy_bundle(tmp_path)
    result = _run(bundle)
    entry = result["evidence"][0]
    assert entry["source_scope"] == {"provenance_scope": "unavailable", "page_space": "unavailable",
        "pages": [], "transforms": [], "provenance_atom_count": 0, "provenance_sha256": None}
    assert entry["related_page_ocr"] == {"status": "unavailable", "reason": "not_configured", "pages": []}
    assert entry["issues"] == ["source_provenance_unavailable"]
    assert entry["record_sha256"] == core._digest(bundle.records[0])
    assert all(value is None for field, value in result["generation"].items()
               if field not in {"index_manifest_sha256", "chunks_sha256", "generation_id"})


def _legacy_proof_paths(bundle):
    return {
        "quality": core.quality_core.quality_report_path(bundle.chunks_path),
        "completion": core.artifact_io._artifact_completion_path(bundle.chunks_path, stage="chunking"),
        "oracle": core.source_fidelity_core.source_oracle_registry_path(bundle.chunks_path),
    }


def _add_legacy_sidecar(path, kind):
    if kind == "directory":
        path.mkdir()
    elif kind in {"dangling_link", "file_link"}:
        target = path.with_name(path.name + ".link-target")
        if kind == "file_link":
            target.write_bytes(b"private synthetic sidecar target")
        try:
            path.symlink_to(target)
        except OSError:
            pytest.skip("symlink creation unavailable")
    else:
        path.write_bytes(b"private synthetic malformed proof" if kind == "malformed" else
                         b'{"schema_version":7,"source_sha256":"wrong-generation"}')


def _stable_identity(status):
    # Access time is not identity: reading a symlink's target can advance
    # its atime, and a one-second tick between the two reads flaked.
    return (status.st_mode, status.st_ino, status.st_dev, status.st_nlink,
            status.st_uid, status.st_gid, status.st_size,
            status.st_mtime_ns, status.st_ctime_ns)


@pytest.mark.parametrize("role", ["quality", "completion", "oracle"])
@pytest.mark.parametrize("kind", ["malformed", "wrong_generation", "directory", "dangling_link", "file_link"])
def test_legacy_present_proof_fails_before_search_without_opening_or_removing_it(
        tmp_path, monkeypatch, role, kind):
    bundle = _legacy_bundle(tmp_path)
    path = _legacy_proof_paths(bundle)[role]
    _add_legacy_sidecar(path, kind)
    before_identity = _stable_identity(path.lstat())
    before_inputs = {item: item.read_bytes() for item in (bundle.chunks_path, bundle.manifest_path)}
    real_capture = core._capture

    def capture(captured_path, *args, **kwargs):
        assert Path(captured_path) != path, "legacy admission must not parse orphaned proof"
        return real_capture(captured_path, *args, **kwargs)

    monkeypatch.setattr(core, "_capture", capture)
    _expect_failure(bundle, search=lambda *_a, **_k: pytest.fail("orphaned proof reached retrieval"))
    assert _stable_identity(path.lstat()) == before_identity
    assert {item: item.read_bytes() for item in before_inputs} == before_inputs


def test_legacy_multiple_orphan_proofs_do_not_form_an_unbound_quality_generation(tmp_path):
    bundle = _legacy_bundle(tmp_path)
    paths = _legacy_proof_paths(bundle)
    for path in paths.values():
        _add_legacy_sidecar(path, "wrong_generation")
    before = {path: path.read_bytes() for path in paths.values()}
    _expect_failure(bundle, search=lambda *_a, **_k: pytest.fail("unbound proof reached retrieval"))
    assert {path: path.read_bytes() for path in before} == before


@pytest.mark.parametrize("role", ["quality", "completion", "oracle"])
@pytest.mark.parametrize("phase", ["search", "after_artifact_recheck"])
@pytest.mark.parametrize("kind", ["malformed", "directory", "dangling_link"])
def test_legacy_proof_appearing_during_search_or_after_artifact_recheck_suppresses_success(
        tmp_path, monkeypatch, role, phase, kind):
    bundle = _legacy_bundle(tmp_path)
    path = _legacy_proof_paths(bundle)[role]
    before_inputs = {item: item.read_bytes() for item in (bundle.chunks_path, bundle.manifest_path)}
    calls = []
    real_recheck = core._recheck

    def search(*_args, **_kwargs):
        calls.append("search")
        if phase == "search":
            _add_legacy_sidecar(path, kind)
        return _response(bundle)

    def recheck(snapshots):
        real_recheck(snapshots)
        calls.append("recheck")
        if phase == "after_artifact_recheck":
            _add_legacy_sidecar(path, kind)

    monkeypatch.setattr(core, "_recheck", recheck)
    _expect_failure(bundle, search=search)
    assert calls == ["search", "recheck"]
    assert path.lstat()
    assert {item: item.read_bytes() for item in before_inputs} == before_inputs


@pytest.mark.parametrize("role", ["quality", "completion", "oracle"])
def test_legacy_proof_absence_check_does_not_treat_stat_failure_as_missing(tmp_path, monkeypatch, role):
    bundle = _legacy_bundle(tmp_path)
    path = _legacy_proof_paths(bundle)[role]
    real_lstat = Path.lstat

    def lstat(value, *args, **kwargs):
        if value == path:
            raise PermissionError("private synthetic filesystem failure")
        return real_lstat(value, *args, **kwargs)

    monkeypatch.setattr(Path, "lstat", lstat)
    _expect_failure(bundle, search=lambda *_a, **_k: pytest.fail("unproved absence reached retrieval"))


def test_reserved_metadata_collisions_use_actual_qdrant_payload_semantics(tmp_path):
    record = {"text": "The real synthetic row text.", "metadata": {
        "text": "Not the indexed text.", "stable_id": "not-the-computed-id", "chapter_num": 1}}
    bundle = _legacy_bundle(tmp_path, records=[record])
    result = _run(bundle)
    assert result["search"]["hits"][0]["text"] == record["text"]
    assert result["evidence"][0]["record_sha256"] == core._digest(record)
    response = _response(bundle)
    response.hits[0].metadata["text"] = record["metadata"]["text"]
    _expect_failure(bundle, response=response)


def test_preprocessed_conversion_never_joins_original_page_numbers(tmp_path):
    bundle = build_bundle(tmp_path, preprocessed=True)
    result = _run(bundle)
    assert result["generation"]["recovery_sha256"] is not None
    for entry in result["evidence"]:
        assert entry["source_scope"]["page_space"] == "conversion_input"
        assert entry["issues"] == ["original_page_mapping_unavailable"]
        assert entry["related_page_ocr"] == {"status": "unavailable",
            "reason": "original_page_mapping_unavailable", "pages": []}


def test_bound_structure_profile_reconstructs_nonempty_exclusions(tmp_path):
    bundle = build_bundle(tmp_path, excluded_frontmatter=True, with_table=True, with_multipage=True)
    assert bundle.structural_ranges == {(1, 1)}
    original = physical_binding()
    observed = []

    def validate(*args, **kwargs):
        observed.append(kwargs)
        return original.validated_quality_report_binding(*args, **kwargs)

    result = _run(bundle, binding=replace(physical_binding(search_index=lambda *_a, **_k: _response(bundle)),
                                         validated_quality_report_binding=validate))
    assert observed[0]["structural_ranges"] == {(1, 1)}
    assert observed[0]["embedding_model"] == bundle.config.embedding_model
    assert all(1 not in entry["source_scope"]["pages"] for entry in result["evidence"])
    assert len(result["evidence"]) == len(bundle.records)


@pytest.mark.parametrize("mutation", ["wrong_source", "bool_page_count", "unknown_status", "forged_summary"])
def test_configured_recovery_conflicts_fail_before_retrieval(tmp_path, mutation):
    bundle = build_bundle(tmp_path)
    report = _json(bundle.recovery_path)
    if mutation == "wrong_source":
        report["source_sha256"] = "0" * 64
    elif mutation == "bool_page_count":
        report["page_count"] = True
    elif mutation == "unknown_status":
        report["pages"][0]["status"] = "approved"
    else:
        report["summary"]["review_required"] += 1
    _write_json(bundle.recovery_path, report)
    _expect_failure(bundle, search=lambda *_a, **_k: pytest.fail("invalid recovery reached retrieval"))


@pytest.mark.parametrize("replacement", ["docling", "recovery"])
def test_explicit_source_paths_cannot_alias_any_other_input_role(tmp_path, replacement):
    bundle = build_bundle(tmp_path)
    config = contracts.EvidenceCorpusConfig(bundle.config.corpus_id,
        docling_path=bundle.chunks_path if replacement == "docling" else bundle.docling_path,
        recovery_path=bundle.docling_path if replacement == "recovery" else None)
    _expect_failure(bundle, evidence_config=config)


@pytest.mark.parametrize("role", ["chunks_path", "manifest_path", "quality_path", "recovery_path"])
def test_hardlinked_input_is_not_captured(tmp_path, role):
    bundle = build_bundle(tmp_path)
    path = getattr(bundle, role)
    try:
        os.link(path, tmp_path / "additional-hardlink")
    except OSError:
        pytest.skip("hard links unavailable")
    _expect_failure(bundle)


def test_symlinked_input_is_not_followed(tmp_path):
    bundle = build_bundle(tmp_path)
    link = tmp_path / "linked-recovery.json"
    try:
        link.symlink_to(bundle.recovery_path)
    except OSError:
        pytest.skip("symlink creation unavailable")
    config = contracts.EvidenceCorpusConfig(bundle.config.corpus_id, bundle.docling_path, link)
    _expect_failure(bundle, evidence_config=config)


@pytest.mark.parametrize("replacement", ["same_size", "different_size"])
def test_capture_rejects_file_replacement_during_snapshot(tmp_path, monkeypatch, replacement):
    bundle = build_bundle(tmp_path)
    real = core._read_snapshot

    def snapshot(path, **kwargs):
        result = real(path, **kwargs)
        if Path(path) == bundle.chunks_path:
            temporary = tmp_path / "replacement.jsonl"
            temporary.write_bytes(result[0] if replacement == "same_size" else result[0] + b" ")
            os.replace(temporary, path)
        return result

    monkeypatch.setattr(core, "_read_snapshot", snapshot)
    _expect_failure(bundle)


@pytest.mark.parametrize("constant", ["MAX_CHUNKS_BYTES", "MAX_RECORD_BYTES", "MAX_RECORDS",
    "MAX_INDEX_MANIFEST_BYTES", "MAX_COMPLETION_BYTES", "MAX_DOCLING_BYTES", "MAX_SCOPE_ATOMS"])
def test_every_producer_byte_count_and_scope_budget_fails_closed(tmp_path, monkeypatch, constant):
    bundle = build_bundle(tmp_path)
    monkeypatch.setattr(core, constant, 0 if constant == "MAX_SCOPE_ATOMS" else 1)
    _expect_failure(bundle)


@pytest.mark.parametrize("mutation", ["duplicate_key", "duplicate_row", "nonfinite", "deep_json", "invalid_utf8"])
def test_strict_chunk_parser_rejects_before_retrieval(tmp_path, mutation):
    bundle = _legacy_bundle(tmp_path)
    raw = bundle.chunks_path.read_bytes()
    if mutation == "duplicate_key":
        raw = b'{"text":"one","text":"two","metadata":{}}\n'
    elif mutation == "duplicate_row":
        raw *= 2
    elif mutation == "nonfinite":
        raw = b'{"text":"one","metadata":{"value":NaN}}\n'
    elif mutation == "deep_json":
        raw = b'{"text":"one","metadata":{"value":' + b"[" * 65 + b"0" + b"]" * 65 + b"}}\n"
    else:
        raw = b'\xff'
    bundle.chunks_path.write_bytes(raw)
    _expect_failure(bundle, search=lambda *_a, **_k: pytest.fail("invalid JSON reached retrieval"))


@pytest.mark.parametrize("search_request", [
    service_contracts.SearchRequest("", mode="vector"),
    service_contracts.SearchRequest("query", limit=True, mode="vector"),
    service_contracts.SearchRequest("query", limit=51, mode="vector"),
    service_contracts.SearchRequest("query", mode="invalid"),
    service_contracts.SearchRequest("query", filters=service_contracts.SearchFilters(chapter_num=True)),
    service_contracts.SearchRequest("query", filters={}),
])
def test_direct_api_validates_dataclass_contents_before_any_physical_call(tmp_path, search_request):
    bundle = _legacy_bundle(tmp_path)
    _expect_failure(bundle, request=search_request, search=lambda *_a, **_k: pytest.fail("invalid request reached retrieval"))


def test_response_must_echo_actual_requested_mode(tmp_path):
    bundle = _legacy_bundle(tmp_path)
    _expect_failure(bundle, response=_response(bundle, mode="auto"))


def test_untrusted_numeric_overflow_is_redacted(tmp_path):
    bundle = _legacy_bundle(tmp_path)
    response = _response(bundle)
    response.hits[0].score = 10 ** 1000
    _expect_failure(bundle, response=response)


def test_empty_search_still_binds_full_validated_generation(tmp_path):
    bundle = build_bundle(tmp_path)
    full = _run(bundle)
    empty = _run(bundle, response=_response(bundle, []))
    assert empty["search"]["hits"] == [] and empty["evidence"] == []
    assert empty["generation"] == full["generation"]


def test_retrieval_objects_are_detached_before_physical_rechecks(tmp_path, monkeypatch):
    bundle = _legacy_bundle(tmp_path)
    response = _response(bundle)
    real = core._recheck

    def recheck(snapshots):
        real(snapshots)
        response.hits[0].text = "forged after matching"
        response.hits[0].metadata["chapter_num"] = 99
        response.hits.clear()

    monkeypatch.setattr(core, "_recheck", recheck)
    result = _run(bundle, response=response)
    assert len(result["evidence"]) == 1
    assert result["search"]["hits"][0]["text"] == bundle.records[0]["text"]
    assert result["search"]["hits"][0]["metadata"]["chapter_num"] == 1


def test_annotation_byte_budget_is_exact_and_never_silently_drops_evidence(tmp_path, monkeypatch):
    bundle = build_bundle(tmp_path)
    result = _run(bundle)
    companion = {key: value for key, value in result.items() if key != "search"}
    size = len(core._canonical(companion))
    monkeypatch.setattr(contracts, "MAX_EVIDENCE_BYTES", size)
    assert _run(bundle) == result
    monkeypatch.setattr(contracts, "MAX_EVIDENCE_BYTES", size - 1)
    _expect_failure(bundle)


def test_response_byte_truncation_preserves_full_unicode_hashes(tmp_path):
    texts = tuple("漢" * 32760 + f" distinct{i}" for i in range(25))
    records = [{"text": text, "metadata": {"chunk_index": i, "chapter_num": 1}}
               for i, text in enumerate(texts)]
    bundle = _legacy_bundle(tmp_path, records=records)
    result = _run(bundle)
    assert len(core._canonical(result)) <= contracts.MAX_PUBLIC_EVIDENCE_RESPONSE_BYTES
    assert any(hit["text_truncated"] for hit in result["search"]["hits"])
    for record, entry in zip(records, result["evidence"]):
        assert entry["text_sha256"] == hashlib.sha256(record["text"].encode("utf-8")).hexdigest()


@pytest.mark.parametrize("filters", [service_contracts.SearchFilters(chapter_num=2),
    service_contracts.SearchFilters(content_type="statutory_excerpt")])
def test_valid_hit_cannot_violate_requested_filters(tmp_path, filters):
    bundle = build_bundle(tmp_path)
    request = service_contracts.SearchRequest("needle", limit=5, mode="vector", filters=filters)
    _expect_failure(bundle, request=request)


def test_new_manifest_bytes_change_opaque_generation_even_for_identical_rows(tmp_path):
    bundle = build_bundle(tmp_path)
    before = _run(bundle)
    bundle.manifest_path.write_bytes(bundle.manifest_path.read_bytes() + b"\n")
    after = _run(bundle)
    assert before["search"] == after["search"]
    assert before["generation"]["chunks_sha256"] == after["generation"]["chunks_sha256"]
    assert before["generation"]["index_manifest_sha256"] != after["generation"]["index_manifest_sha256"]
    assert before["generation"]["generation_id"] != after["generation"]["generation_id"]
    for first, second in zip(before["evidence"], after["evidence"]):
        assert first["record_sha256"] == second["record_sha256"]
        assert first["evidence_id"] != second["evidence_id"]


@pytest.mark.parametrize("which", ["quality_digest", "quality_payload", "quality_schema_float", "conversion_digest",
                                  "conversion_path", "conversion_unverified"])
def test_physical_validation_cannot_return_another_generation(tmp_path, which):
    bundle = build_bundle(tmp_path)
    binding = physical_binding(search_index=lambda *_a, **_k: _response(bundle))
    if which.startswith("quality"):
        original = binding.validated_quality_report_binding

        def quality(*args, **kwargs):
            schema, digest, payload = original(*args, **kwargs)
            if which == "quality_digest":
                digest = "0" * 64
            elif which == "quality_schema_float":
                schema = float(schema)
            else:
                payload["unbound_extra"] = True
            return schema, digest, payload

        binding = replace(binding, validated_quality_report_binding=quality)
    else:
        original = binding.load_conversion_source_binding

        def conversion(*args, **kwargs):
            result = original(*args, **kwargs)
            if which == "conversion_digest":
                return replace(result, manifest_sha256="0" * 64)
            if which == "conversion_path":
                return replace(result, manifest_path=tmp_path / "other-conversion.json")
            return replace(result, capture_verified=False)

        binding = replace(binding, load_conversion_source_binding=conversion)
    _expect_failure(bundle, binding=binding)


def test_page_set_outside_original_recovery_universe_is_not_silently_narrowed(tmp_path):
    bundle = build_bundle(tmp_path, texts=tuple(f"Synthetic physical page {i}." for i in range(6)))
    report = _json(bundle.recovery_path)
    report["page_count"] = 5
    report["summary"]["inspected"] = 5
    # This report remains internally valid: its retry subset and deferred page
    # are all <= 5. Only the Docling-derived sixth-page join exposes the gap.
    core.validate_recovery_report(report)
    _write_json(bundle.recovery_path, report)
    _expect_failure(bundle)


def test_known_table_children_have_exactly_the_parent_provenance_digest(tmp_path):
    bundle = build_bundle(tmp_path, with_table=True)
    evidence = _run(bundle)["evidence"]
    parents = [(record, entry) for record, entry in zip(bundle.records, evidence)
               if record["metadata"].get("retrieval_role") == "table_parent"]
    assert len(parents) == 1
    parent = parents[0][1]["source_scope"]
    children = [entry for record, entry in zip(bundle.records, evidence)
                if record["metadata"].get("retrieval_role") == "table_child"]
    assert len(children) == 4
    for child in children:
        assert child["source_scope"]["provenance_sha256"] == parent["provenance_sha256"]
        assert child["source_scope"]["provenance_atom_count"] == parent["provenance_atom_count"]


@pytest.mark.parametrize("mutation", ["completion_schema", "oracle_binding_schema", "conversion_binding_schema"])
def test_completion_versions_are_type_strict_even_when_delegated_equality_is_not(tmp_path, mutation):
    bundle = build_bundle(tmp_path, with_recovery=False)
    completion = _json(bundle.completion_path)
    if mutation == "completion_schema":
        completion["schema_version"] = float(completion["schema_version"])
    else:
        key = "source_fidelity_oracles" if mutation == "oracle_binding_schema" else "conversion_manifest"
        completion["inputs"][key]["schema_version"] = float(completion["inputs"][key]["schema_version"])
    _write_json(bundle.completion_path, completion)
    # Without explicit Docling the prior delegated helpers compare nested
    # bindings using Python equality; 3.0 must not stand in for integer3.
    _expect_failure(bundle, evidence_config=contracts.EvidenceCorpusConfig(bundle.config.corpus_id))
