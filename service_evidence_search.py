"""Exact manifested-row evidence search through an injected physical boundary.

This module never opens a PDF or loads an OCR/vector/model runtime. Captured
lineage is provenance, not OCR correctness; recovery is related page evidence,
never an assertion that its candidate was adopted into a retrieved record.
"""

from __future__ import annotations

from collections.abc import Callable
from dataclasses import dataclass, fields
import hashlib
import json
import os
from pathlib import Path
import stat

import artifact_io
import document_profiles
from evaluation_inputs import _hex_digest, _read_snapshot, _strict_json_bytes
import index_state
from ocr_recovery_comparison import MAX_RECOVERY_BYTES, validate_recovery_report
import quality_core
import release_security
import service_contracts
import service_evidence_contracts as contracts
import source_fidelity_core
import storage_policy
import table_retrieval_core


MAX_CHUNKS_BYTES = 64 * 1024 * 1024
MAX_RECORD_BYTES = 4 * 1024 * 1024
MAX_RECORDS = 100_000
MAX_INDEX_MANIFEST_BYTES = 8 * 1024 * 1024
MAX_COMPLETION_BYTES = 1024 * 1024
MAX_DOCLING_BYTES = 64 * 1024 * 1024
MAX_SCOPE_ATOMS = 4096


@dataclass(frozen=True, slots=True)
class EvidenceSearchBinding:
    """One complete operation-time snapshot of physical facade capabilities."""

    search_index: Callable
    vector_store_lock: Callable
    chunk_output_lease: Callable
    chunk_id: Callable
    chunk_hash: Callable
    validated_quality_report_binding: Callable
    load_conversion_source_binding: Callable
    identify_book_sections: Callable
    book_structural_ranges: Callable
    manifest_schema_version: int
    embedding_input_policy_version: int
    model_artifact_lock_sha256: str

    def __post_init__(self) -> None:
        for field in fields(self):
            value = getattr(self, field.name)
            if field.name in {"manifest_schema_version", "embedding_input_policy_version"}:
                if type(value) is not int or value < 1:
                    raise TypeError("invalid evidence binding policy version")
            elif field.name == "model_artifact_lock_sha256":
                _hex_digest(value, label="evidence model lock")
            elif not callable(value):
                raise TypeError("invalid evidence binding capability")


def _canonical(value: object) -> bytes:
    return json.dumps(value, sort_keys=True, ensure_ascii=False, allow_nan=False,
                      separators=(",", ":")).encode("utf-8")


def _digest(value: object) -> str:
    return hashlib.sha256(_canonical(value)).hexdigest()


def _file_identity(value) -> tuple:
    return (int(value.st_dev), int(value.st_ino), int(value.st_size), int(value.st_mtime_ns))


def _capture(path: Path, limit: int, snapshots: dict, role: str) -> tuple[bytes, str]:
    """Pin one bounded, single-linked input generation without modifying it."""
    path = Path(path).absolute()
    storage_policy.assert_no_link_components(path)
    before = path.stat(follow_symlinks=False)
    parent = path.parent.stat(follow_symlinks=False)
    if not stat.S_ISREG(before.st_mode) or before.st_nlink != 1:
        raise ValueError("evidence inputs require single-linked regular files")
    for old_path, _, _, _ in snapshots.values():
        if path == old_path or os.path.samefile(path, old_path):
            raise ValueError("evidence input roles must be distinct")
    raw, digest = _read_snapshot(path, label="evidence artifact", max_bytes=limit)
    storage_policy.assert_no_link_components(path)
    after = path.stat(follow_symlinks=False)
    parent_after = path.parent.stat(follow_symlinks=False)
    if (_file_identity(before) != _file_identity(after) or after.st_nlink != 1
            or not stat.S_ISREG(after.st_mode) or len(raw) != after.st_size
            or (parent.st_dev, parent.st_ino) != (parent_after.st_dev, parent_after.st_ino)):
        raise RuntimeError("evidence artifact changed during capture")
    snapshots[role] = (path, digest, len(raw), limit)
    return raw, digest


def _capture_json(path: Path, limit: int, snapshots: dict, role: str) -> tuple[dict, str]:
    raw, digest = _capture(path, limit, snapshots, role)
    return _strict_json_bytes(raw, label="evidence artifact", max_bytes=limit), digest


def _recheck(snapshots: dict) -> None:
    current = {}
    for role, (path, digest, size, limit) in snapshots.items():
        raw, observed = _capture(path, limit, current, role)
        if digest != observed or size != len(raw):
            raise RuntimeError("evidence artifact generation changed")


def _require_legacy_proof_absent(chunks_path: Path) -> None:
    """Only proof-free legacy inputs may expose unavailable source evidence.

    This companion-only admission rule does not interpret orphaned proof or
    change ordinary legacy search. Any canonical sidecar requires bound quality.
    """
    for path in (
            quality_core.quality_report_path(chunks_path),
            artifact_io._artifact_completion_path(chunks_path, stage="chunking"),
            source_fidelity_core.source_oracle_registry_path(chunks_path)):
        storage_policy.assert_no_link_components(path)
        try:
            path.lstat()
        except FileNotFoundError:
            continue
        raise ValueError("source-backed evidence requires bound quality")


def _records(raw: bytes, binding: EvidenceSearchBinding) -> list[dict]:
    records = []
    for line in raw.split(b"\n"):
        if not line.strip():
            continue
        if len(records) == MAX_RECORDS:
            raise ValueError("evidence record budget exceeded")
        record = _strict_json_bytes(line, label="evidence record", max_bytes=MAX_RECORD_BYTES)
        if (not isinstance(record.get("text"), str) or not record["text"].strip()
                or not isinstance(record.get("metadata"), dict)):
            raise ValueError("invalid evidence record")
        _canonical(record)
        records.append(record)
    if not records:
        raise ValueError("evidence corpus is empty")
    ids = [binding.chunk_id(record) for record in records]
    if len(set(ids)) != len(ids):
        raise ValueError("evidence corpus has duplicate record identifiers")
    return records


def _manifest(payload: dict, records: list[dict], chunks_digest: str,
              config: service_contracts.CorpusConfig, binding: EvidenceSearchBinding) -> None:
    if set(payload) != {"schema_version", "backend", "collection", "embedding_model",
                        "embedding_dimension", "embedding_input_policy_version",
                        "model_artifact_lock_sha256", "chunk_hashes", "source_sha256",
                        "source_record_count", "table_child_count", "quality_report_schema_version",
                        "quality_report_sha256"}:
        raise ValueError("invalid evidence index manifest fields")
    for key in ("schema_version", "embedding_dimension", "embedding_input_policy_version",
                "source_record_count", "table_child_count"):
        if type(payload[key]) is not int or payload[key] < (0 if key == "table_child_count" else 1):
            raise ValueError("invalid evidence index manifest integer")
    mismatch = index_state._index_manifest_mismatch(
        payload, backend=config.backend, collection_name=config.collection_name,
        embedding_model=config.embedding_model, embedding_dimension=payload["embedding_dimension"],
        embedding_input_policy_version=binding.embedding_input_policy_version,
        model_artifact_lock_sha256=binding.model_artifact_lock_sha256,
        manifest_schema_version=binding.manifest_schema_version,
        quality_report_schema_version=quality_core.QUALITY_REPORT_SCHEMA_VERSION)
    expected_hashes = {binding.chunk_id(record): binding.chunk_hash(record) for record in records}
    if (mismatch is not None or payload["source_sha256"] != chunks_digest
            or payload["source_record_count"] != len(records)
            or payload["table_child_count"] != table_retrieval_core.table_child_count(records)
            or _canonical(payload["chunk_hashes"]) != _canonical(expected_hashes)):
        raise ValueError("evidence index manifest does not match captured corpus")


def _source_inputs(config, evidence_config, records, chunks_digest, snapshots, binding):
    quality_path = quality_core.quality_report_path(config.chunks_path)
    quality, quality_digest = _capture_json(
        quality_path, quality_core.MAX_QUALITY_REPORT_BYTES, snapshots, "quality")
    completion_path = artifact_io._artifact_completion_path(config.chunks_path, stage="chunking")
    completion, _ = _capture_json(completion_path, MAX_COMPLETION_BYTES, snapshots, "chunk_completion")
    oracle_path = source_fidelity_core.source_oracle_registry_path(config.chunks_path)
    oracles, _ = _capture_json(oracle_path, source_fidelity_core.MAX_SOURCE_ORACLE_REGISTRY_BYTES,
                              snapshots, "source_oracles")
    if any(type(payload.get("schema_version")) is not int for payload in (quality, completion, oracles)):
        raise ValueError("evidence artifact schema versions must be integers")
    if type(oracles.get("source_fidelity_schema_version")) is not int:
        raise ValueError("evidence source-fidelity version must be an integer")
    inputs = completion.get("inputs")
    if not quality_core._valid_input_bindings(inputs):
        raise ValueError("invalid evidence chunk input bindings")
    for key in ("source_fidelity_oracles", "conversion_manifest"):
        if inputs[key] is not None and type(inputs[key]["schema_version"]) is not int:
            raise ValueError("evidence input schema versions must be integers")
    upstream = {key: inputs[key] for key in ("docling_json", "conversion_manifest", "table_recovery")}
    if (_canonical(quality.get("inputs")) != _canonical(inputs)
            or _canonical(oracles.get("inputs")) != _canonical(upstream)
            or inputs["table_recovery"] is not None and _canonical(
                inputs["table_recovery"]["conversion_manifest"]) != _canonical(inputs["conversion_manifest"])):
        raise ValueError("evidence input bindings disagree")
    expected_oracle = inputs["source_fidelity_oracles"]
    if (expected_oracle["name"] != oracle_path.name
            or expected_oracle["sha256"] != snapshots["source_oracles"][1]
            or expected_oracle["size"] != snapshots["source_oracles"][2]):
        raise ValueError("evidence oracle binding mismatch")
    source_fidelity_core.validate_source_oracle_registry(oracles, expected_input_bindings=upstream)
    document = conversion = recovery = None
    structural_ranges = ()
    if evidence_config.docling_path is not None:
        document, document_digest = _capture_json(evidence_config.docling_path, MAX_DOCLING_BYTES,
                                                 snapshots, "docling")
        expected_document = {"name": evidence_config.docling_path.name,
                             "size": snapshots["docling"][2], "sha256": document_digest}
        if _canonical(inputs["docling_json"]) != _canonical(expected_document):
            raise ValueError("configured Docling generation is not bound to chunks")
        conversion_path = artifact_io._artifact_completion_path(evidence_config.docling_path, stage="conversion")
        conversion_payload, conversion_digest = _capture_json(
            conversion_path, MAX_COMPLETION_BYTES, snapshots, "conversion_manifest")
        if type(conversion_payload.get("schema_version")) is not int:
            raise ValueError("evidence conversion version must be an integer")
        if _canonical(inputs["conversion_manifest"]) != _canonical({
                "name": conversion_path.name, "sha256": conversion_digest, "schema_version": 3}):
            raise ValueError("configured conversion generation is not bound to chunks")
        conversion = binding.load_conversion_source_binding(
            evidence_config.docling_path, document_sha256=document_digest,
            document_size=snapshots["docling"][2])
        if (conversion is None or conversion.schema_version != 3 or conversion.capture_verified is not True
                or Path(conversion.manifest_path).absolute() != conversion_path.absolute()
                or conversion.manifest_sha256 != conversion_digest):
            raise ValueError("a matching capture-verified conversion is required")
        for record in records:
            captured_source = record["metadata"].get("stable_id_source")
            if captured_source is not None and _canonical(captured_source) != _canonical({
                    "name": conversion.source_name, "size": conversion.source_size,
                    "sha256": conversion.source_sha256}):
                raise ValueError("record original-source binding mismatch")
        profile = document_profiles.profile_from_provenance(completion.get("structure_profile"))
        structural_ranges = binding.book_structural_ranges(
            binding.identify_book_sections(document, structure_profile=profile, emit_log=False),
            structure_profile=profile)
        if evidence_config.recovery_path is not None:
            recovery, _ = _capture_json(evidence_config.recovery_path, MAX_RECOVERY_BYTES,
                                       snapshots, "recovery")
            recovery = validate_recovery_report(recovery)
            if recovery["source_sha256"] != conversion.source_sha256:
                raise ValueError("OCR recovery belongs to a different original source")
    result = binding.validated_quality_report_binding(
        config.chunks_path, records, chunks_digest, snapshots["chunks"][2],
        document=document, structural_ranges=structural_ranges, allow_legacy_quality=False,
        embedding_model=config.embedding_model)
    if (not isinstance(result, tuple) or len(result) != 3
            or type(result[0]) is not int or result[0] != quality_core.QUALITY_REPORT_SCHEMA_VERSION
            or result[1] != quality_digest or _canonical(result[2]) != _canonical(quality)):
        raise ValueError("quality validation returned a different captured generation")
    return conversion, recovery


def _scope(record: dict, *, quality_available: bool, conversion) -> dict:
    unavailable = {"provenance_scope": "unavailable", "page_space": "unavailable", "pages": [],
                   "transforms": [], "provenance_atom_count": 0, "provenance_sha256": None}
    if not quality_available:
        return unavailable
    metadata = record["metadata"]
    items = metadata.get("source_items")
    if not isinstance(items, list) or not items:
        raise ValueError("quality-bound record lacks source provenance")
    atoms = []
    pages = set()
    transforms = set()
    for item in items:
        indexes = source_fidelity_core.lineage_scope_indexes(item)
        item_pages = source_fidelity_core.lineage_scope_pages(item)
        if indexes is None or item_pages is None:
            raise ValueError("record source scope is invalid")
        indexes = set(indexes)
        pages.update(item_pages)
        transforms.add(item["transform"])
        for span in item["spans"]:
            if span["provenance_index"] in indexes:
                atoms.append({"ref": item["ref"], "transform": item["transform"], "span": span})
        if len(atoms) > MAX_SCOPE_ATOMS or any(page > 5000 for page in pages):
            raise ValueError("record source scope exceeds evidence bounds")
    return {
        "provenance_scope": ("table_parent_source_items" if metadata.get("retrieval_role") == "table_child"
                             else "record_source_items"),
        "page_space": ("original_pdf" if conversion is not None and conversion.effective_input_kind == "original"
                       else "conversion_input"),
        "pages": sorted(pages), "transforms": sorted(transforms),
        "provenance_atom_count": len(atoms), "provenance_sha256": _digest(atoms),
    }


def _related(scope: dict, recovery: dict | None) -> dict:
    if recovery is None:
        reason = "not_configured"
    elif scope["provenance_scope"] == "unavailable":
        reason = "source_provenance_unavailable"
    elif scope["page_space"] != "original_pdf":
        reason = "original_page_mapping_unavailable"
    else:
        if any(page > recovery["page_count"] for page in scope["pages"]):
            raise ValueError("source provenance exceeds the recovery page universe")
        selected = {page["page_number"]: page["status"] for page in recovery["pages"]}
        deferred = {page["page_number"] for page in recovery["deferred_pages"]}
        return {"status": "available", "reason": None, "pages": [
            {"page_number": page, "status": selected.get(page, "deferred" if page in deferred else "not_selected")}
            for page in scope["pages"]]}
    return {"status": "unavailable", "reason": reason, "pages": []}


def execute_evidence_search(config, evidence_config, request, request_id, *, binding, security_policy):
    """Search exact pinned records; return no private paths or OCR candidate text.

    Ordinary search is untouched. Input or consistency failures are deliberately
    redacted here; cancellation and other BaseException subclasses propagate to
    the existing contained-worker cleanup boundary.
    """
    try:
        if (not isinstance(config, service_contracts.CorpusConfig)
                or not isinstance(evidence_config, contracts.EvidenceCorpusConfig)
                or evidence_config.corpus_id != config.corpus_id
                or not isinstance(request, service_contracts.SearchRequest)
                or not isinstance(binding, EvidenceSearchBinding)
                or not isinstance(security_policy, release_security.ReleaseSecurityPolicy)):
            raise ValueError("invalid evidence search capability")
        service_contracts.validate_request_id(request_id)
        if not isinstance(request.filters, service_contracts.SearchFilters):
            raise ValueError("invalid evidence search filters")
        request = service_contracts.parse_search_request({
            "query": request.query, "limit": request.limit, "mode": request.mode,
            "filters": request.filters.as_dict()})
        return _execute(config, evidence_config, request, request_id, binding, security_policy)
    except (OSError, ValueError, TypeError, KeyError, AttributeError, RuntimeError, OverflowError):
        raise service_contracts.ServiceContractError("service_unavailable") from None


def _execute(config, evidence_config, request, request_id, binding, security_policy):
    snapshots = {}
    storage_policy.assert_no_link_components(config.db_path)
    storage_policy.assert_no_link_components(config.chunks_path)
    marker = index_state._index_update_marker_path(config.db_path, backend=config.backend,
                                                  collection_name=config.collection_name)
    manifest_path = index_state._index_manifest_path(config.db_path, backend=config.backend,
                                                    collection_name=config.collection_name)
    with binding.vector_store_lock(config.db_path, backend=config.backend, collection_name=config.collection_name,
                                   operation="evidence search", timeout=config.db_lock_timeout_seconds):
        with binding.chunk_output_lease(config.chunks_path, timeout=config.db_lock_timeout_seconds):
            storage_policy.assert_no_link_components(marker)
            if marker.exists():
                raise ValueError("index generation is incomplete")
            manifest, _ = _capture_json(manifest_path, MAX_INDEX_MANIFEST_BYTES, snapshots, "index_manifest")
            raw, chunks_digest = _capture(config.chunks_path, MAX_CHUNKS_BYTES, snapshots, "chunks")
            records = _records(raw, binding)
            _manifest(manifest, records, chunks_digest, config, binding)
            record_bytes = {binding.chunk_id(record): _canonical(record) for record in records}
            quality_available = manifest["quality_report_sha256"] is not None
            conversion = recovery = None
            if quality_available:
                conversion, recovery = _source_inputs(config, evidence_config, records, chunks_digest, snapshots, binding)
                if manifest["quality_report_sha256"] != snapshots["quality"][1]:
                    raise ValueError("index quality generation does not match")
            else:
                if (evidence_config.docling_path is not None or evidence_config.recovery_path is not None
                        or any("source_lineage_schema_version" in record["metadata"] for record in records)
                        or table_retrieval_core.has_table_retrieval_metadata(records)):
                    raise ValueError("source-backed evidence requires bound quality")
                _require_legacy_proof_absent(config.chunks_path)
            response = binding.search_index(
                request.query, config.db_path, db_backend="qdrant", n_results=request.limit,
                content_type=request.filters.content_type, chapter_num=request.filters.chapter_num,
                collection_name=config.collection_name, embedding_model=config.embedding_model,
                use_reranker=False, hybrid={"auto": None, "vector": False, "hybrid": True}[request.mode],
                chunks_path=config.chunks_path, lock_timeout=config.db_lock_timeout_seconds,
                security_policy=security_policy)
            by_id = {binding.chunk_id(record): record for record in records}
            hits = getattr(response, "hits", None)
            if (not isinstance(hits, list) or len(hits) > request.limit
                    or getattr(response, "requested_mode", None) != request.mode):
                raise ValueError("invalid evidence retrieval result count")
            evidence = []
            generation = {f"{role}_sha256": snapshots[role][1] if role in snapshots else None for role in (
                "index_manifest", "chunks", "quality", "chunk_completion", "source_oracles", "docling", "conversion_manifest", "recovery")}
            generation["generation_id"] = contracts.generation_id(
                generation, None if conversion is None else conversion.source_sha256)
            evidence_size = len(_canonical({"schema_version": 1, "kind": "service_evidence_search",
                                             "generation": generation, "evidence": []}))
            for hit in hits:
                record = by_id.get(hit.source_id)
                if record is None or _canonical(record) != record_bytes[hit.source_id]:
                    raise ValueError("retrieved source record does not exist or changed")
                expected_metadata = {**record["metadata"], "stable_id": hit.source_id}
                expected_metadata.pop("text", None)
                if hit.text != record["text"] or _canonical(hit.metadata) != _canonical(expected_metadata):
                    raise ValueError("retrieved full payload does not match manifested record")
                scope = _scope(record, quality_available=quality_available, conversion=conversion)
                record_digest = hashlib.sha256(record_bytes[hit.source_id]).hexdigest()
                issues = []
                if scope["provenance_scope"] == "unavailable":
                    issues.append("source_provenance_unavailable")
                if scope["page_space"] == "conversion_input":
                    issues.append("original_page_mapping_unavailable")
                if scope["provenance_scope"] == "table_parent_source_items":
                    issues.append("table_parent_scope")
                entry = {"source_id": hit.source_id, "record_sha256": record_digest,
                         "text_sha256": hashlib.sha256(record["text"].encode("utf-8")).hexdigest(),
                         "source_scope": scope, "related_page_ocr": _related(scope, recovery),
                         "text_accuracy": "not_verified", "review_scope": "unmapped", "issues": sorted(issues)}
                entry["evidence_id"] = contracts.evidence_id(generation["generation_id"], record_digest, scope)
                evidence_size += len(_canonical(entry)) + bool(evidence)
                if evidence_size > contracts.MAX_EVIDENCE_BYTES:
                    raise ValueError("evidence annotation byte budget exceeded")
                evidence.append(entry)
            # Detach the public result before physical snapshot callbacks run
            # again. No mutable retrieval object crosses that verification gap.
            public_search = service_contracts.public_search_response(
                response, corpus_id=config.corpus_id, request_id=request_id)
            _recheck(snapshots)
            storage_policy.assert_no_link_components(marker)
            if marker.exists():
                raise ValueError("index generation became incomplete")
            if not quality_available:
                _require_legacy_proof_absent(config.chunks_path)
            result = {"schema_version": 1, "kind": "service_evidence_search", "generation": generation,
                      "search": public_search,
                      "evidence": evidence}
            return contracts.validate_evidence_response(result, expected_corpus_id=config.corpus_id,
                                                        expected_request_id=request_id,
                                                        expected_request=request)
