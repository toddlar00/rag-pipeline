#!/usr/bin/env python3
"""Prepare and attest corpus-owner review of private evaluation judgments.

Review packets contain private query and corpus evidence and therefore use the
repository's owner-only, link-aware storage policy.  Final receipts contain no
query text, corpus text, stable IDs, reviewer label, or local paths.  A receipt
is a provenance/attestation binding, not a cryptographic identity signature.
"""

from __future__ import annotations

import argparse
import copy
import hashlib
import json
import re
import sys
import unicodedata
from datetime import datetime, timezone
from pathlib import Path

import artifact_io
import evaluation_contract
from evaluation_inputs import (
    _corpus_contract as _corpus_contract,
    _hex_digest as _hex_digest,
    _read_snapshot as _read_snapshot,
    _strict_json_bytes as _strict_json_bytes,
)
import retrieval_core
import storage_policy
import table_retrieval_core


REVIEW_PACKET_SCHEMA_VERSION = 2
REVIEW_RECEIPT_SCHEMA_VERSION = 1
MAX_QUERIES_BYTES = 8 * 1024 * 1024
MAX_CHUNKS_BYTES = 512 * 1024 * 1024
MAX_PACKET_BYTES = 16 * 1024 * 1024
MAX_RECEIPT_BYTES = 64 * 1024
MAX_DIAGNOSTIC_REPORT_BYTES = 64 * 1024 * 1024
MAX_SOURCE_EVIDENCE_PER_JUDGMENT = 64
MAX_TABLE_FAMILY_EVIDENCE_PER_JUDGMENT = (
    table_retrieval_core.MAX_TABLE_CHILDREN_PER_PARENT + 1
)
MAX_REVIEW_CANDIDATE_DEPTH = 20
OWNER_ATTESTATION = (
    "I reviewed every query and judgment against the pinned corpus"
)

_UTC_TIMESTAMP_RE = re.compile(
    r"(?:20[0-9]{2})-(?:0[1-9]|1[0-2])-"
    r"(?:0[1-9]|[12][0-9]|3[01])T"
    r"(?:[01][0-9]|2[0-3]):[0-5][0-9]:[0-5][0-9]Z"
)
_DECISIONS = frozenset({"pending", "approve", "reject"})
_EVIDENCE_METADATA_FIELDS = (
    "source_file",
    "chapter_num",
    "chapter_title",
    "title",
    "headings",
    "section_path",
    "page_start",
    "page_end",
    "page_range",
    "content_type",
    "content_source",
    "retrieval_role",
    "table_retrieval_schema_version",
    "table_parent_stable_id",
    "table_child_index",
    "table_child_count",
)
_PACKET_INSTRUCTIONS = {
    "allowed_decisions": ["approve", "reject"],
    "attestation_required_for_finalization": OWNER_ATTESTATION,
    "edit_only": ["query_decision", "judgment_reviews[].decision"],
    "negative_case_note": (
        "Approving an abstention query attests that the proposition was "
        "independently checked against the pinned corpus."
    ),
    "retrieval_candidate_note": (
        "When retrieval candidates are present, query approval also attests "
        "that they were checked for missing or incorrectly graded evidence."
    ),
    "table_family_note": (
        "A table_family child is a query-specific alternate satisfier with the "
        "parent judgment's grade. Approve it only if that exact row answers the "
        "query; unlisted sibling rows receive no credit."
    ),
}


def _canonical_bytes(payload: object) -> bytes:
    return json.dumps(
        payload,
        ensure_ascii=False,
        sort_keys=True,
        separators=(",", ":"),
        allow_nan=False,
    ).encode("utf-8")


def _canonical_sha256(payload: object) -> str:
    return hashlib.sha256(_canonical_bytes(payload)).hexdigest()


def _pretty_json_size(payload: object) -> int:
    """Return the exact UTF-8 size used by private indented JSON writes."""
    serialized = json.dumps(
        payload,
        ensure_ascii=False,
        sort_keys=True,
        indent=2,
        allow_nan=False,
    ) + "\n"
    return len(serialized.encode("utf-8"))


def _parse_queries(raw: bytes, path: Path) -> list[dict]:
    import eval as retrieval_eval

    try:
        text = raw.decode("utf-8-sig")
    except UnicodeError as exc:
        raise ValueError(f"evaluation queries are not UTF-8: {path}") from exc
    queries = []
    for line_number, line in enumerate(text.splitlines(), 1):
        if not line.strip():
            continue
        query = _strict_json_bytes(
            line.encode("utf-8"),
            label=f"evaluation query {line_number}",
            max_bytes=MAX_QUERIES_BYTES,
        )
        retrieval_eval._validate_query(
            query, label=f"{path}:{line_number}")
        queries.append(query)
    if not queries:
        raise ValueError(f"no evaluation queries found in {path}")
    return queries


def _query_ids(queries: list[dict]) -> list[str]:
    query_ids = []
    for index, query in enumerate(queries, 1):
        query_id = query.get("query_id")
        if not isinstance(query_id, str) or not query_id.strip():
            raise ValueError(f"evaluation query {index} needs a non-empty query_id")
        query_ids.append(query_id.strip())
    if len(set(query_ids)) != len(query_ids):
        raise ValueError("evaluation query IDs must be unique")
    return query_ids


def _query_id_root(queries: list[dict]) -> str:
    return _canonical_sha256(_query_ids(queries))


def _coverage(queries: list[dict]) -> dict:
    return {
        "query_count": len(queries),
        "judgment_count": sum(
            len(query.get("judgments", [])) for query in queries),
        "expected_abstention_count": sum(
            query.get("expected_abstain") is True for query in queries),
        "tags": sorted({
            tag for query in queries for tag in query.get("tags", [])
        }),
    }


def _load_inputs(
        queries_path: Path, chunks_path: Path, *,
        required_status: str | None,
        validate_corpus_pin: bool = True,
        ) -> tuple[list[dict], str, list[dict], str]:
    import eval as retrieval_eval

    query_raw, query_sha256 = _read_snapshot(
        queries_path, label="evaluation queries", max_bytes=MAX_QUERIES_BYTES)
    chunk_raw, chunk_sha256 = _read_snapshot(
        chunks_path, label="chunks corpus", max_bytes=MAX_CHUNKS_BYTES)
    queries = _parse_queries(query_raw, Path(queries_path))
    records = artifact_io._parse_index_records_strict(
        chunk_raw, Path(chunks_path), chunk_id_fn=retrieval_core._chunk_id)
    _query_ids(queries)
    retrieval_eval._validate_corpus_pin_coverage(queries, required=True)
    if validate_corpus_pin:
        retrieval_eval._validate_declared_corpus_snapshot(
            queries, actual_hash=chunk_sha256, actual_count=len(records))
    retrieval_eval._validate_judged_ids(
        queries, records, retrieval_core._chunk_id,
        corpus_sha256=chunk_sha256)
    retrieval_eval._validate_grounding_evidence_ids(
        queries, records, retrieval_core._chunk_id)
    if required_status is not None:
        wrong = [
            index for index, query in enumerate(queries, 1)
            if query.get("review_status") != required_status
        ]
        if wrong:
            examples = ", ".join(str(value) for value in wrong[:3])
            raise ValueError(
                f"query review_status must be {required_status!r}; "
                f"mismatched query numbers: {examples}")
    return queries, query_sha256, records, chunk_sha256


def rebind_draft_queries(
        queries_path: Path, chunks_path: Path, output_path: Path, *,
        declared_chunks_path: str | None = None,
        force: bool = False) -> dict:
    """Re-pin a draft after proving every judged ID survives regeneration."""
    if _paths_alias(queries_path, chunks_path, output_path):
        raise ValueError("queries, chunks, and rebound output paths must be distinct")
    if declared_chunks_path is not None and (
            not isinstance(declared_chunks_path, str)
            or not declared_chunks_path.strip()):
        raise ValueError("declared chunks path must be a non-empty string")
    _require_output(output_path, force=force)
    queries, source_queries_sha256, records, chunks_sha256 = _load_inputs(
        queries_path, chunks_path,
        required_status="draft_requires_corpus_owner",
        validate_corpus_pin=False,
    )
    previous_corpus = _corpus_contract(queries)
    rebound = copy.deepcopy(queries)
    for query in rebound:
        query["corpus"]["sha256"] = chunks_sha256
        query["corpus"]["record_count"] = len(records)
        if declared_chunks_path is not None:
            query["corpus"]["chunks_path"] = declared_chunks_path.strip()
    rebound_raw = b"".join(
        _canonical_bytes(query) + b"\n" for query in rebound)
    storage_policy.atomic_write_private(
        output_path,
        lambda handle: handle.write(rebound_raw),
        text=False,
    )
    return {
        "schema_version": REVIEW_PACKET_SCHEMA_VERSION,
        "status": "draft_requires_corpus_owner",
        "source_queries_sha256": source_queries_sha256,
        "queries_sha256": hashlib.sha256(rebound_raw).hexdigest(),
        "previous_corpus_sha256": previous_corpus["sha256"],
        "corpus_sha256": chunks_sha256,
        "corpus_record_count": len(records),
        "surviving_judgment_count": sum(
            len(query.get("judgments", [])) for query in rebound),
        "surviving_unique_chunk_ids": len({
            chunk_id
            for query in rebound for judgment in query.get("judgments", [])
            for chunk_id in _judgment_chunk_ids(judgment)
        }),
        "query_count": len(rebound),
    }


def _evidence_metadata(metadata: dict) -> dict:
    result = {
        field: copy.deepcopy(metadata[field])
        for field in _EVIDENCE_METADATA_FIELDS if field in metadata
    }
    refs = sorted({
        item.get("ref")
        for item in metadata.get("source_items", [])
        if (isinstance(item, dict)
            and isinstance(item.get("ref"), str)
            and item["ref"])
    })
    if refs:
        result["source_refs"] = refs
    return result


def _evidence_record(record: dict) -> dict:
    return {
        "chunk_id": retrieval_core._chunk_id(record),
        "text": record["text"],
        "text_sha256": hashlib.sha256(
            record["text"].encode("utf-8")).hexdigest(),
        "metadata": _evidence_metadata(record["metadata"]),
    }


def _judgment_chunk_ids(judgment: dict) -> tuple[str, ...]:
    """Return every explicitly reviewed chunk alias for one judgment."""
    if "chunk_id" not in judgment:
        return ()
    family = judgment.get("table_family") or {}
    return (
        judgment["chunk_id"].strip(),
        *family.get("accepted_child_chunk_ids", []),
    )


def _diagnostic_candidates(
        report_path: Path, *, queries: list[dict], queries_sha256: str,
        records: list[dict], chunks_sha256: str,
        candidate_depth: int) -> tuple[dict[str, list[dict]], dict]:
    if (isinstance(candidate_depth, bool)
            or not isinstance(candidate_depth, int)
            or not 1 <= candidate_depth <= MAX_REVIEW_CANDIDATE_DEPTH):
        raise ValueError(
            f"candidate depth must be from 1 to {MAX_REVIEW_CANDIDATE_DEPTH}")
    raw, report_sha256 = _read_snapshot(
        report_path, label="diagnostic report",
        max_bytes=MAX_DIAGNOSTIC_REPORT_BYTES)
    report = _strict_json_bytes(
        raw, label="diagnostic report",
        max_bytes=MAX_DIAGNOSTIC_REPORT_BYTES)
    if (report.get("schema_version") != evaluation_contract.REPORT_SCHEMA_VERSION
            or report.get("mode") != "compare"):
        raise ValueError(
            "review candidates require a current-schema compare diagnostic "
            "report")
    configurations = report.get("configurations")
    if not isinstance(configurations, list) or len(configurations) != 4:
        raise ValueError("diagnostic report must contain all four retrieval modes")

    records_by_id = {
        retrieval_core._chunk_id(record): record for record in records
    }
    query_by_id = {query_id: query for query_id, query in zip(
        _query_ids(queries), queries)}
    candidates: dict[str, dict[str, dict]] = {
        query_id: {} for query_id in query_by_id
    }
    observed_modes = set()
    for configuration_index, item in enumerate(configurations, 1):
        if not isinstance(item, dict):
            raise ValueError(
                f"diagnostic configuration {configuration_index} must be an object")
        configuration = item.get("configuration")
        if not isinstance(configuration, dict):
            raise ValueError("diagnostic configuration provenance is missing")
        hybrid = configuration.get("hybrid")
        reranker = configuration.get("use_reranker")
        if not isinstance(hybrid, bool) or not isinstance(reranker, bool):
            raise ValueError("diagnostic retrieval modes must be explicit booleans")
        mode = (
            "hybrid_reranked" if hybrid and reranker
            else "hybrid" if hybrid
            else "vector_reranked" if reranker
            else "vector"
        )
        if mode in observed_modes:
            raise ValueError("diagnostic report contains a duplicate retrieval mode")
        observed_modes.add(mode)
        snapshot = configuration.get("index_snapshot")
        if (configuration.get("queries_sha256") != queries_sha256
                or configuration.get("report_detail") != "full"
                or not isinstance(snapshot, dict)
                or snapshot.get("source_sha256") != chunks_sha256
                or snapshot.get("record_count") != len(records)):
            raise ValueError(
                "diagnostic report does not bind the exact queries and corpus")

        details = item.get("query_details")
        if not isinstance(details, list) or len(details) != len(queries):
            raise ValueError(
                "diagnostic report query details are incomplete")
        seen_query_ids = set()
        for detail in details:
            if not isinstance(detail, dict):
                raise ValueError("diagnostic query detail must be an object")
            query_id = detail.get("query_id")
            if (query_id not in query_by_id or query_id in seen_query_ids
                    or detail.get("query") != query_by_id[query_id]["query"]):
                raise ValueError(
                    "diagnostic query identity does not match the query set")
            seen_query_ids.add(query_id)
            judged_ids = {
                chunk_id
                for judgment in query_by_id[query_id].get("judgments", [])
                for chunk_id in _judgment_chunk_ids(judgment)
            }
            results = detail.get("results")
            if not isinstance(results, list):
                raise ValueError("diagnostic query results must be a list")
            for result in results[:candidate_depth]:
                if not isinstance(result, dict):
                    raise ValueError("diagnostic result must be an object")
                chunk_id = result.get("chunk_id")
                if not isinstance(chunk_id, str) or not chunk_id:
                    raise ValueError(
                        "full diagnostic results must expose stable chunk IDs")
                if chunk_id not in records_by_id:
                    raise ValueError(
                        "diagnostic result is absent from the pinned corpus")
                if chunk_id in judged_ids:
                    continue
                rank = result.get("rank")
                if (isinstance(rank, bool) or not isinstance(rank, int)
                        or rank < 1 or rank > candidate_depth):
                    raise ValueError("diagnostic result rank is invalid")
                entry = candidates[query_id].setdefault(chunk_id, {
                    "chunk_id": chunk_id,
                    "appearances": [],
                    "evidence": _evidence_record(records_by_id[chunk_id]),
                })
                entry["appearances"].append({
                    "mode": mode,
                    "rank": rank,
                    "reported_relevance": result.get("relevance"),
                })
        if seen_query_ids != set(query_by_id):
            raise ValueError("diagnostic report omits a query")
    if observed_modes != {
            "vector", "vector_reranked", "hybrid", "hybrid_reranked"}:
        raise ValueError("diagnostic report retrieval mode coverage is incomplete")

    ordered = {}
    for query_id, entries in candidates.items():
        ordered[query_id] = sorted(
            entries.values(),
            key=lambda value: (
                min(item["rank"] for item in value["appearances"]),
                value["chunk_id"],
            ),
        )
    return ordered, {
        "schema_version": evaluation_contract.REPORT_SCHEMA_VERSION,
        "mode": "compare",
        "sha256": report_sha256,
        "candidate_depth": candidate_depth,
    }


def _build_packet(
        queries: list[dict], *, queries_sha256: str,
        records: list[dict], chunks_sha256: str,
        retrieval_candidates: dict[str, list[dict]] | None = None,
        diagnostic_binding: dict | None = None) -> dict:
    corpus = _corpus_contract(queries)
    if corpus["sha256"] != chunks_sha256 or corpus["record_count"] != len(records):
        raise ValueError("review packet corpus does not match query declarations")

    by_chunk_id = {
        retrieval_core._chunk_id(record): [record] for record in records
    }
    by_source_id: dict[str, dict[str, dict]] = {}
    for record in records:
        metadata = record["metadata"]
        chunk_id = retrieval_core._chunk_id(record)
        for value in (metadata.get("source_id"), metadata.get("source_file")):
            if isinstance(value, str) and value.strip():
                by_source_id.setdefault(value.strip(), {})[chunk_id] = record

    review_items = []
    for ordinal, query in enumerate(queries, 1):
        judgment_reviews = []
        for judgment in query.get("judgments", []):
            id_type = "chunk_id" if "chunk_id" in judgment else "source_id"
            identifier = judgment[id_type].strip()
            evidence = (
                [
                    record
                    for chunk_id in _judgment_chunk_ids(judgment)
                    for record in by_chunk_id.get(chunk_id, [])
                ] if id_type == "chunk_id"
                else list(by_source_id.get(identifier, {}).values()))
            if not evidence:
                raise ValueError(
                    f"query {ordinal} judgment has no corpus evidence")
            if (id_type == "source_id"
                    and len(evidence) > MAX_SOURCE_EVIDENCE_PER_JUDGMENT):
                raise ValueError(
                    f"query {ordinal} source judgment expands to more than "
                    f"{MAX_SOURCE_EVIDENCE_PER_JUDGMENT} chunks; use chunk IDs")
            if (id_type == "chunk_id"
                    and len(evidence)
                    > MAX_TABLE_FAMILY_EVIDENCE_PER_JUDGMENT):
                raise ValueError(
                    f"query {ordinal} table-family judgment expands to more "
                    f"than {MAX_TABLE_FAMILY_EVIDENCE_PER_JUDGMENT} chunks")
            judgment_reviews.append({
                "id_type": id_type,
                "id": identifier,
                "relevance": judgment["relevance"],
                "decision": "pending",
                "evidence": [_evidence_record(record) for record in evidence],
            })
        review_items.append({
            "ordinal": ordinal,
            "query_snapshot": copy.deepcopy(query),
            "query_decision": "pending",
            "judgment_reviews": judgment_reviews,
            "retrieval_candidates": copy.deepcopy(
                (retrieval_candidates or {}).get(query["query_id"], [])),
        })

    body = {
        "schema_version": REVIEW_PACKET_SCHEMA_VERSION,
        "kind": "corpus_owner_review_packet",
        "status": "draft_requires_corpus_owner",
        "instructions": copy.deepcopy(_PACKET_INSTRUCTIONS),
        "source": {
            "queries_sha256": queries_sha256,
            "query_id_root_sha256": _query_id_root(queries),
            "corpus": corpus,
            "diagnostic_report": copy.deepcopy(diagnostic_binding),
        },
        "coverage": _coverage(queries),
        "review_items": review_items,
    }
    packet = dict(body)
    packet["template_sha256"] = _canonical_sha256(body)
    return packet


def _paths_alias(*paths: Path) -> bool:
    normalized = [str(Path(path).resolve(strict=False)).casefold() for path in paths]
    return len(set(normalized)) != len(normalized)


def _require_output(path: Path, *, force: bool) -> None:
    path = Path(path)
    if storage_policy.path_is_link_like(path):
        raise ValueError("review outputs cannot replace links or junctions")
    if path.exists() and not force:
        raise FileExistsError(f"output already exists (use --force): {path}")


def prepare_review_packet(
        queries_path: Path, chunks_path: Path, output_path: Path, *,
        diagnostic_report_path: Path | None = None,
        candidate_depth: int = 10,
        force: bool = False) -> dict:
    """Write one private, editable review packet bound to exact input bytes."""
    paths = [queries_path, chunks_path, output_path]
    if diagnostic_report_path is not None:
        paths.append(diagnostic_report_path)
    if _paths_alias(*paths):
        raise ValueError("review packet inputs and output paths must be distinct")
    _require_output(output_path, force=force)
    queries, queries_sha256, records, chunks_sha256 = _load_inputs(
        queries_path, chunks_path,
        required_status="draft_requires_corpus_owner")
    retrieval_candidates = None
    diagnostic_binding = None
    if diagnostic_report_path is not None:
        retrieval_candidates, diagnostic_binding = _diagnostic_candidates(
            diagnostic_report_path,
            queries=queries,
            queries_sha256=queries_sha256,
            records=records,
            chunks_sha256=chunks_sha256,
            candidate_depth=candidate_depth,
        )
    packet = _build_packet(
        queries, queries_sha256=queries_sha256,
        records=records, chunks_sha256=chunks_sha256,
        retrieval_candidates=retrieval_candidates,
        diagnostic_binding=diagnostic_binding)
    packet_size = _pretty_json_size(packet)
    if packet_size > MAX_PACKET_BYTES:
        raise ValueError(
            "review packet would exceed the publication limit: "
            f"{packet_size} > {MAX_PACKET_BYTES} bytes")
    storage_policy.atomic_write_private_json(output_path, packet, indent=2)
    return {
        "schema_version": REVIEW_PACKET_SCHEMA_VERSION,
        "status": packet["status"],
        "template_sha256": packet["template_sha256"],
        "retrieval_candidate_count": sum(
            len(item["retrieval_candidates"])
            for item in packet["review_items"]),
        **packet["coverage"],
    }


def _load_packet(path: Path) -> tuple[dict, str]:
    raw, digest = _read_snapshot(
        path, label="review packet", max_bytes=MAX_PACKET_BYTES)
    return _strict_json_bytes(
        raw, label="review packet", max_bytes=MAX_PACKET_BYTES), digest


def _normalize_packet_decisions(packet: dict) -> tuple[dict, list[str]]:
    normalized = copy.deepcopy(packet)
    failures = []
    items = normalized.get("review_items")
    if not isinstance(items, list):
        raise ValueError("review packet review_items must be a list")
    for ordinal, item in enumerate(items, 1):
        if not isinstance(item, dict):
            raise ValueError(f"review item {ordinal} must be an object")
        decision = item.get("query_decision")
        if decision not in _DECISIONS:
            raise ValueError(f"review item {ordinal} has an invalid query decision")
        if decision != "approve":
            failures.append(f"query {ordinal}: {decision}")
        item["query_decision"] = "pending"
        judgments = item.get("judgment_reviews")
        if not isinstance(judgments, list):
            raise ValueError(
                f"review item {ordinal} judgment_reviews must be a list")
        for judgment_index, judgment in enumerate(judgments, 1):
            if not isinstance(judgment, dict):
                raise ValueError(
                    f"review item {ordinal} judgment {judgment_index} "
                    "must be an object")
            judgment_decision = judgment.get("decision")
            if judgment_decision not in _DECISIONS:
                raise ValueError(
                    f"review item {ordinal} judgment {judgment_index} "
                    "has an invalid decision")
            if judgment_decision != "approve":
                failures.append(
                    f"query {ordinal} judgment {judgment_index}: "
                    f"{judgment_decision}")
            judgment["decision"] = "pending"
    return normalized, failures


def _reviewed_at(value: str) -> str:
    if not isinstance(value, str) or _UTC_TIMESTAMP_RE.fullmatch(value) is None:
        raise ValueError("reviewed-at must use YYYY-MM-DDTHH:MM:SSZ")
    try:
        parsed = datetime.strptime(value, "%Y-%m-%dT%H:%M:%SZ").replace(
            tzinfo=timezone.utc)
    except ValueError as exc:
        raise ValueError("reviewed-at is not a valid UTC timestamp") from exc
    if parsed.year < 2020:
        raise ValueError("reviewed-at is outside the supported date range")
    return value


def _reviewer_id_sha256(value: str) -> str:
    if not isinstance(value, str):
        raise ValueError("reviewer-id must be text")
    normalized = unicodedata.normalize("NFKC", value).strip().casefold()
    if not 3 <= len(normalized) <= 200 or any(ord(char) < 32 for char in normalized):
        raise ValueError("reviewer-id must contain 3-200 printable characters")
    return hashlib.sha256(normalized.encode("utf-8")).hexdigest()


def _approval_batch_id(
        *, packet_sha256: str, template_sha256: str,
        source_queries_sha256: str,
        corpus: dict, reviewer_id_sha256: str, reviewed_at: str) -> str:
    return _canonical_sha256({
        "schema_version": REVIEW_RECEIPT_SCHEMA_VERSION,
        "packet_sha256": packet_sha256,
        "template_sha256": template_sha256,
        "source_queries_sha256": source_queries_sha256,
        "corpus": corpus,
        "reviewer_id_sha256": reviewer_id_sha256,
        "reviewed_at": reviewed_at,
    })


def _approved_query_bytes(queries: list[dict], batch_id: str) -> tuple[list[dict], bytes]:
    approved = copy.deepcopy(queries)
    for query in approved:
        query["review_status"] = "approved"
        query["approval"] = {
            "schema_version": REVIEW_RECEIPT_SCHEMA_VERSION,
            "review_batch_id": batch_id,
        }
    raw = b"".join(_canonical_bytes(query) + b"\n" for query in approved)
    return approved, raw


def _receipt_payload(
        approved: list[dict], approved_raw: bytes, *, packet_sha256: str,
        template_sha256: str, source_queries_sha256: str,
        reviewer_id_sha256: str, reviewed_at: str,
        review_batch_id: str) -> dict:
    corpus = _corpus_contract(approved)
    coverage = _coverage(approved)
    return {
        "schema_version": REVIEW_RECEIPT_SCHEMA_VERSION,
        "kind": "corpus_owner_review_receipt",
        "status": "approved",
        "review_batch_id": review_batch_id,
        "reviewed_at": reviewed_at,
        "reviewer_id_sha256": reviewer_id_sha256,
        "packet": {
            "sha256": packet_sha256,
            "template_sha256": template_sha256,
        },
        "approved_queries": {
            "sha256": hashlib.sha256(approved_raw).hexdigest(),
            "source_sha256": source_queries_sha256,
            "record_count": len(approved),
            "query_id_root_sha256": _query_id_root(approved),
            "judgment_count": coverage["judgment_count"],
        },
        "corpus": corpus,
        "coverage": {
            "expected_abstention_count": coverage[
                "expected_abstention_count"],
            "tags": coverage["tags"],
        },
        "decision_counts": {
            "queries_approved": len(approved),
            "judgments_approved": coverage["judgment_count"],
        },
    }


def finalize_review_packet(
        packet_path: Path, queries_path: Path, chunks_path: Path, *,
        approved_queries_path: Path, receipt_path: Path,
        reviewer_id: str, reviewed_at: str, attestation: str,
        diagnostic_report_path: Path | None = None,
        candidate_depth: int = 10,
        force: bool = False) -> dict:
    """Promote an entirely approved packet and emit a content-free receipt."""
    paths = [
        packet_path, queries_path, chunks_path,
        approved_queries_path, receipt_path]
    if diagnostic_report_path is not None:
        paths.append(diagnostic_report_path)
    if _paths_alias(*paths):
        raise ValueError("review inputs and outputs must use distinct paths")
    if attestation != OWNER_ATTESTATION:
        raise ValueError("the exact corpus-owner attestation is required")
    reviewed_at = _reviewed_at(reviewed_at)
    reviewer_digest = _reviewer_id_sha256(reviewer_id)
    _require_output(approved_queries_path, force=force)
    _require_output(receipt_path, force=force)

    queries, queries_sha256, records, chunks_sha256 = _load_inputs(
        queries_path, chunks_path,
        required_status="draft_requires_corpus_owner")
    retrieval_candidates = None
    diagnostic_binding = None
    if diagnostic_report_path is not None:
        retrieval_candidates, diagnostic_binding = _diagnostic_candidates(
            diagnostic_report_path,
            queries=queries,
            queries_sha256=queries_sha256,
            records=records,
            chunks_sha256=chunks_sha256,
            candidate_depth=candidate_depth,
        )
    expected_packet = _build_packet(
        queries, queries_sha256=queries_sha256,
        records=records, chunks_sha256=chunks_sha256,
        retrieval_candidates=retrieval_candidates,
        diagnostic_binding=diagnostic_binding)
    packet, packet_sha256 = _load_packet(packet_path)
    normalized, decision_failures = _normalize_packet_decisions(packet)
    if _canonical_bytes(normalized) != _canonical_bytes(expected_packet):
        raise ValueError(
            "review packet provenance or evidence changed; prepare it again")
    if decision_failures:
        examples = "; ".join(decision_failures[:3])
        raise ValueError(
            "review packet is not fully approved; unresolved decisions: "
            + examples)

    corpus = _corpus_contract(queries)
    batch_id = _approval_batch_id(
        packet_sha256=packet_sha256,
        template_sha256=expected_packet["template_sha256"],
        source_queries_sha256=queries_sha256,
        corpus=corpus,
        reviewer_id_sha256=reviewer_digest,
        reviewed_at=reviewed_at,
    )
    approved, approved_raw = _approved_query_bytes(queries, batch_id)
    receipt = _receipt_payload(
        approved, approved_raw,
        packet_sha256=packet_sha256,
        template_sha256=expected_packet["template_sha256"],
        source_queries_sha256=queries_sha256,
        reviewer_id_sha256=reviewer_digest,
        reviewed_at=reviewed_at,
        review_batch_id=batch_id,
    )

    storage_policy.atomic_write_private(
        approved_queries_path,
        lambda handle: handle.write(approved_raw),
        text=False,
    )
    storage_policy.atomic_write_private_json(receipt_path, receipt, indent=2)
    return {
        "schema_version": REVIEW_RECEIPT_SCHEMA_VERSION,
        "status": "approved",
        "review_batch_id": batch_id,
        "approved_queries_sha256": receipt["approved_queries"]["sha256"],
        **_coverage(approved),
    }


def _validate_receipt_shape(receipt: dict) -> None:
    expected_top = {
        "schema_version", "kind", "status", "review_batch_id",
        "reviewed_at", "reviewer_id_sha256", "packet",
        "approved_queries", "corpus", "coverage", "decision_counts",
    }
    if set(receipt) != expected_top:
        raise ValueError("review receipt fields do not match schema v1")
    schema_version = receipt.get("schema_version")
    if (isinstance(schema_version, bool)
            or not isinstance(schema_version, int)
            or schema_version != REVIEW_RECEIPT_SCHEMA_VERSION
            or receipt.get("kind") != "corpus_owner_review_receipt"
            or receipt.get("status") != "approved"):
        raise ValueError("review receipt header is invalid")
    for field in ("review_batch_id", "reviewer_id_sha256"):
        _hex_digest(receipt.get(field), label=f"review receipt {field}")
    _reviewed_at(receipt.get("reviewed_at"))
    if set(receipt.get("packet", {})) != {"sha256", "template_sha256"}:
        raise ValueError("review receipt packet binding is invalid")
    for field in ("sha256", "template_sha256"):
        _hex_digest(receipt["packet"].get(field), label=f"packet {field}")
    if set(receipt.get("approved_queries", {})) != {
            "sha256", "source_sha256", "record_count",
            "query_id_root_sha256", "judgment_count"}:
        raise ValueError("review receipt query binding is invalid")
    for field in ("sha256", "source_sha256", "query_id_root_sha256"):
        _hex_digest(
            receipt["approved_queries"].get(field),
            label=f"approved queries {field}")
    if set(receipt.get("corpus", {})) != {
            "sha256", "record_count", "id_scheme"}:
        raise ValueError("review receipt corpus binding is invalid")
    _hex_digest(receipt["corpus"].get("sha256"), label="receipt corpus SHA-256")
    if set(receipt.get("coverage", {})) != {
            "expected_abstention_count", "tags"}:
        raise ValueError("review receipt coverage is invalid")
    if set(receipt.get("decision_counts", {})) != {
            "queries_approved", "judgments_approved"}:
        raise ValueError("review receipt decision counts are invalid")


def validate_review_receipt(
        queries: list[dict], queries_sha256: str,
        receipt_path: Path) -> dict:
    """Validate a portable receipt against already-validated query bytes."""
    raw, receipt_sha256 = _read_snapshot(
        receipt_path, label="review receipt", max_bytes=MAX_RECEIPT_BYTES)
    receipt = _strict_json_bytes(
        raw, label="review receipt", max_bytes=MAX_RECEIPT_BYTES)
    _validate_receipt_shape(receipt)
    _hex_digest(queries_sha256, label="queries SHA-256")

    approvals = [query.get("approval") for query in queries]
    if any(not isinstance(value, dict) for value in approvals):
        raise ValueError(
            "receipt-bound queries must all contain approval provenance")
    expected_approval_fields = {"schema_version", "review_batch_id"}
    if any(set(value) != expected_approval_fields for value in approvals):
        raise ValueError("query approval fields do not match schema v1")
    batch_ids = {value.get("review_batch_id") for value in approvals}
    if (any(isinstance(value.get("schema_version"), bool)
            or not isinstance(value.get("schema_version"), int)
            or value.get("schema_version") != REVIEW_RECEIPT_SCHEMA_VERSION
            for value in approvals)
            or len(batch_ids) != 1
            or next(iter(batch_ids)) != receipt["review_batch_id"]):
        raise ValueError("query approval batch does not match the review receipt")
    if any(query.get("review_status") != "approved" for query in queries):
        raise ValueError("receipt-bound queries must be marked approved")

    coverage = _coverage(queries)
    approved_binding = receipt["approved_queries"]
    corpus = _corpus_contract(queries)
    expected_approved_binding = {
        "sha256": queries_sha256,
        "record_count": len(queries),
        "query_id_root_sha256": _query_id_root(queries),
        "judgment_count": coverage["judgment_count"],
    }
    if any(_canonical_bytes(approved_binding[field]) != _canonical_bytes(value)
           for field, value in expected_approved_binding.items()):
        raise ValueError("review receipt does not bind the approved query set")
    if _canonical_bytes(receipt["corpus"]) != _canonical_bytes(corpus):
        raise ValueError("review receipt does not bind the declared corpus")
    expected_coverage = {
            "expected_abstention_count": coverage[
                "expected_abstention_count"],
            "tags": coverage["tags"],
            }
    if (_canonical_bytes(receipt["coverage"])
            != _canonical_bytes(expected_coverage)):
        raise ValueError("review receipt coverage does not match the query set")
    expected_decision_counts = {
            "queries_approved": len(queries),
            "judgments_approved": coverage["judgment_count"],
            }
    if (_canonical_bytes(receipt["decision_counts"])
            != _canonical_bytes(expected_decision_counts)):
        raise ValueError("review receipt approval counts do not match")

    expected_batch = _approval_batch_id(
        packet_sha256=receipt["packet"]["sha256"],
        template_sha256=receipt["packet"]["template_sha256"],
        source_queries_sha256=approved_binding["source_sha256"],
        corpus=corpus,
        reviewer_id_sha256=receipt["reviewer_id_sha256"],
        reviewed_at=receipt["reviewed_at"],
    )
    if expected_batch != receipt["review_batch_id"]:
        raise ValueError("review receipt approval batch digest is invalid")
    return {
        "schema_version": REVIEW_RECEIPT_SCHEMA_VERSION,
        "review_batch_id": receipt["review_batch_id"],
        "receipt_sha256": receipt_sha256,
        "reviewed_at": receipt["reviewed_at"],
    }


def validate_review_receipt_files(
        queries_path: Path, chunks_path: Path, receipt_path: Path) -> dict:
    """Validate approved queries, their live corpus, and one receipt."""
    queries, queries_sha256, _records, _chunks_sha256 = _load_inputs(
        queries_path, chunks_path, required_status="approved")
    return validate_review_receipt(queries, queries_sha256, receipt_path)


def _build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        description="Prepare or validate private corpus-owner review evidence")
    actions = parser.add_subparsers(dest="command", required=True)

    rebind = actions.add_parser(
        "rebind",
        help="Re-pin a draft after all judged IDs survive corpus regeneration")
    rebind.add_argument("--queries", type=Path, required=True)
    rebind.add_argument("--chunks", type=Path, required=True)
    rebind.add_argument("--out", type=Path, required=True)
    rebind.add_argument(
        "--declared-chunks-path",
        help="Portable chunks path written into every corpus declaration")
    rebind.add_argument("--force", action="store_true")

    prepare = actions.add_parser(
        "prepare", help="Write a private packet with pinned judgment evidence")
    prepare.add_argument("--queries", type=Path, required=True)
    prepare.add_argument("--chunks", type=Path, required=True)
    prepare.add_argument("--out", type=Path, required=True)
    prepare.add_argument(
        "--diagnostic-report", type=Path,
        help="Current-schema full compare report used to add unjudged candidates")
    prepare.add_argument(
        "--candidate-depth", type=int, default=10,
        help=("Candidates retained per retrieval mode when a diagnostic "
              "report is supplied (default: 10)"))
    prepare.add_argument("--force", action="store_true")

    finalize = actions.add_parser(
        "finalize", help="Promote an entirely approved packet and write a receipt")
    finalize.add_argument("--packet", type=Path, required=True)
    finalize.add_argument("--queries", type=Path, required=True)
    finalize.add_argument("--chunks", type=Path, required=True)
    finalize.add_argument("--approved-queries-out", type=Path, required=True)
    finalize.add_argument("--receipt-out", type=Path, required=True)
    finalize.add_argument("--reviewer-id", required=True)
    finalize.add_argument("--reviewed-at", required=True)
    finalize.add_argument("--attestation", required=True)
    finalize.add_argument(
        "--diagnostic-report", type=Path,
        help="Same current-schema full compare report used to prepare the packet")
    finalize.add_argument(
        "--candidate-depth", type=int, default=10,
        help=("Candidate depth used when the review packet was prepared "
              "(default: 10)"))
    finalize.add_argument("--force", action="store_true")

    validate = actions.add_parser(
        "validate", help="Validate approved queries, live corpus, and receipt")
    validate.add_argument("--queries", type=Path, required=True)
    validate.add_argument("--chunks", type=Path, required=True)
    validate.add_argument("--receipt", type=Path, required=True)
    return parser


def main(argv: list[str] | None = None) -> int:
    parser = _build_parser()
    args = parser.parse_args(argv)
    try:
        if args.command == "rebind":
            summary = rebind_draft_queries(
                args.queries, args.chunks, args.out,
                declared_chunks_path=args.declared_chunks_path,
                force=args.force,
            )
        elif args.command == "prepare":
            summary = prepare_review_packet(
                args.queries, args.chunks, args.out,
                diagnostic_report_path=args.diagnostic_report,
                candidate_depth=args.candidate_depth,
                force=args.force)
        elif args.command == "finalize":
            summary = finalize_review_packet(
                args.packet, args.queries, args.chunks,
                approved_queries_path=args.approved_queries_out,
                receipt_path=args.receipt_out,
                reviewer_id=args.reviewer_id,
                reviewed_at=args.reviewed_at,
                attestation=args.attestation,
                diagnostic_report_path=args.diagnostic_report,
                candidate_depth=args.candidate_depth,
                force=args.force,
            )
        else:
            summary = validate_review_receipt_files(
                args.queries, args.chunks, args.receipt)
    except (OSError, UnicodeError, ValueError) as exc:
        print(f"Review evidence error: {exc}", file=sys.stderr)
        return 1
    print(json.dumps(summary, sort_keys=True, separators=(",", ":")))
    return 0


if __name__ == "__main__":
    sys.exit(main())
