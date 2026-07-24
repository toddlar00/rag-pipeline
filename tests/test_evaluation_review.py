import hashlib
import json
from pathlib import Path

import pytest

import eval as retrieval_eval
import evaluation_review
from retrieval_core import _chunk_id
import table_retrieval_core


def _write_jsonl(path: Path, records: list[dict]) -> bytes:
    raw = b"".join(
        json.dumps(record, ensure_ascii=False, separators=(",", ":")).encode()
        + b"\n"
        for record in records
    )
    path.write_bytes(raw)
    return raw


def _draft_fixture(tmp_path: Path):
    chunks = [
        {
            "text": "The private answer appears in this exact passage.",
            "metadata": {
                "source_file": "Private_Book",
                "source_id": "Private_Book",
                "chapter_num": 2,
                "page_start": 17,
                "page_end": 17,
                "content_type": "author_narrative",
                "headings": ["Owner review"],
                "source_items": [{"ref": "#/texts/17"}],
            },
        },
        {
            "text": "A deliberately irrelevant distractor.",
            "metadata": {
                "source_file": "Private_Book",
                "source_id": "Private_Book",
                "chapter_num": 3,
                "page_start": 22,
                "page_end": 22,
                "content_type": "author_narrative",
            },
        },
    ]
    chunks_path = tmp_path / "chunks.jsonl"
    chunk_raw = _write_jsonl(chunks_path, chunks)
    corpus = {
        "chunks_path": "private/chunks.jsonl",
        "sha256": hashlib.sha256(chunk_raw).hexdigest(),
        "record_count": len(chunks),
        "source_file": "Private_Book",
        "id_scheme": "retrieval_core._chunk_id",
    }
    queries = [
        {
            "query_id": "private-q1",
            "query": "Where does the private answer appear?",
            "tags": ["author_explanation", "cross_page"],
            "judgments": [
                {"chunk_id": _chunk_id(chunks[0]), "relevance": 3},
                {"chunk_id": _chunk_id(chunks[1]), "relevance": 0},
            ],
            "review_status": "draft_requires_corpus_owner",
            "corpus": corpus,
        },
        {
            "query_id": "private-q2",
            "query": "Which invented escrow percentage applies?",
            "tags": ["abstention"],
            "expected_abstain": True,
            "review_status": "draft_requires_corpus_owner",
            "corpus": corpus,
        },
    ]
    queries_path = tmp_path / "queries.jsonl"
    _write_jsonl(queries_path, queries)
    return queries_path, chunks_path, queries, chunks


def _approve_packet(path: Path) -> dict:
    packet = json.loads(path.read_text(encoding="utf-8"))
    for item in packet["review_items"]:
        item["query_decision"] = "approve"
        for judgment in item["judgment_reviews"]:
            judgment["decision"] = "approve"
    path.write_text(
        json.dumps(packet, indent=2, ensure_ascii=False) + "\n",
        encoding="utf-8",
    )
    return packet


def _write_full_compare_report(
        path: Path, queries_path: Path, chunks_path: Path,
        queries: list[dict], chunks: list[dict]) -> None:
    query_sha256 = hashlib.sha256(queries_path.read_bytes()).hexdigest()
    chunks_sha256 = hashlib.sha256(chunks_path.read_bytes()).hexdigest()
    configurations = []
    for label, hybrid, reranker in (
            ("Vector only", False, False),
            ("Vector + reranker", False, True),
            ("Hybrid", True, False),
            ("Hybrid + reranker", True, True)):
        configurations.append({
            "label": label,
            "configuration": {
                "queries_sha256": query_sha256,
                "report_detail": "full",
                "hybrid": hybrid,
                "use_reranker": reranker,
                "index_snapshot": {
                    "source_sha256": chunks_sha256,
                    "record_count": len(chunks),
                },
            },
            "query_details": [{
                "query_id": query["query_id"],
                "query": query["query"],
                "results": [{
                    "rank": rank,
                    "chunk_id": _chunk_id(chunk),
                    "relevance": 0.0,
                } for rank, chunk in enumerate(chunks, 1)],
            } for query in queries],
        })
    path.write_text(json.dumps({
        "schema_version": retrieval_eval.REPORT_SCHEMA_VERSION,
        "mode": "compare",
        "configurations": configurations,
    }), encoding="utf-8")


def test_rebind_preserves_draft_and_requires_every_judged_id(tmp_path):
    queries, chunks, _source_queries, source_chunks = _draft_fixture(tmp_path)
    stale_rows = [
        json.loads(line) for line in queries.read_text(encoding="utf-8").splitlines()
    ]
    for row in stale_rows:
        row["corpus"]["sha256"] = "0" * 64
        row["corpus"]["record_count"] = 999
    _write_jsonl(queries, stale_rows)
    rebound = tmp_path / "rebound.jsonl"

    summary = evaluation_review.rebind_draft_queries(
        queries, chunks, rebound,
        declared_chunks_path="output/Private_Book/chunks.jsonl")

    rebound_rows = [
        json.loads(line) for line in rebound.read_text(encoding="utf-8").splitlines()
    ]
    assert summary["status"] == "draft_requires_corpus_owner"
    assert summary["surviving_judgment_count"] == 2
    assert summary["surviving_unique_chunk_ids"] == 2
    assert {row["review_status"] for row in rebound_rows} == {
        "draft_requires_corpus_owner"}
    assert {row["corpus"]["sha256"] for row in rebound_rows} == {
        hashlib.sha256(chunks.read_bytes()).hexdigest()}
    assert {row["corpus"]["record_count"] for row in rebound_rows} == {2}
    assert {row["corpus"]["chunks_path"] for row in rebound_rows} == {
        "output/Private_Book/chunks.jsonl"}

    missing_rows = json.loads(json.dumps(stale_rows))
    missing_rows[0]["judgments"][0]["chunk_id"] = _chunk_id({
        "text": "missing",
        "metadata": source_chunks[0]["metadata"],
    })
    _write_jsonl(queries, missing_rows)
    with pytest.raises(ValueError, match="Judged IDs are absent"):
        evaluation_review.rebind_draft_queries(
            queries, chunks, tmp_path / "must-not-exist.jsonl")


def test_prepare_packet_binds_exact_private_evidence(tmp_path):
    queries, chunks, source_queries, source_chunks = _draft_fixture(tmp_path)
    packet_path = tmp_path / "review" / "packet.json"

    summary = evaluation_review.prepare_review_packet(
        queries, chunks, packet_path)

    packet = json.loads(packet_path.read_text(encoding="utf-8"))
    assert summary["status"] == "draft_requires_corpus_owner"
    assert summary["query_count"] == 2
    assert summary["judgment_count"] == 2
    assert packet["source"]["queries_sha256"] == hashlib.sha256(
        queries.read_bytes()).hexdigest()
    assert packet["source"]["corpus"]["sha256"] == hashlib.sha256(
        chunks.read_bytes()).hexdigest()
    assert packet["review_items"][0]["query_snapshot"] == source_queries[0]
    evidence = packet["review_items"][0]["judgment_reviews"][0]["evidence"][0]
    assert evidence["text"] == source_chunks[0]["text"]
    assert evidence["metadata"]["source_refs"] == ["#/texts/17"]
    assert packet["review_items"][1]["judgment_reviews"] == []
    assert packet["template_sha256"] == summary["template_sha256"]


def test_review_packet_groups_parent_and_accepted_child_evidence(tmp_path):
    parent = {
        "text": """Rule table
| Rule | Result |
| --- | --- |
| Alpha | One |
| Beta | Two |
| Gamma | Three |
| Delta | Four |
""",
        "metadata": {
            "source_file": "Private_Book",
            "content_type": "table",
            "content_source": "table",
            "page_start": 1,
            "page_end": 1,
            "page_range": "p.1",
            "table_rows": 4,
            "table_cols": 2,
            "token_count": 20,
        },
    }
    records = table_retrieval_core.expand_table_records(
        [parent], stable_id_fn=_chunk_id,
        token_count_fn=lambda value: len(value.split()))
    chunks_path = tmp_path / "table-chunks.jsonl"
    chunks_raw = _write_jsonl(chunks_path, records)
    parent_id = _chunk_id(records[0])
    accepted_child_id = _chunk_id(records[1])
    query = {
        "query_id": "table-q1",
        "query": "Which row supplies the answer?",
        "review_status": "draft_requires_corpus_owner",
        "judgments": [{
            "chunk_id": parent_id,
            "relevance": 3,
            "table_family": {
                "schema_version": 1,
                "accepted_child_chunk_ids": [accepted_child_id],
            },
        }],
        "corpus": {
            "sha256": hashlib.sha256(chunks_raw).hexdigest(),
            "record_count": len(records),
            "id_scheme": "retrieval_core._chunk_id",
        },
    }
    queries_path = tmp_path / "table-queries.jsonl"
    _write_jsonl(queries_path, [query])
    packet_path = tmp_path / "table-review.json"
    diagnostic_path = tmp_path / "table-diagnostic.json"
    _write_full_compare_report(
        diagnostic_path, queries_path, chunks_path, [query], records)

    evaluation_review.prepare_review_packet(
        queries_path, chunks_path, packet_path,
        diagnostic_report_path=diagnostic_path, candidate_depth=5)

    packet = json.loads(packet_path.read_text(encoding="utf-8"))
    review = packet["review_items"][0]["judgment_reviews"][0]
    assert [item["chunk_id"] for item in review["evidence"]] == [
        parent_id, accepted_child_id]
    assert [item["metadata"]["retrieval_role"]
            for item in review["evidence"]] == [
                "table_parent", "table_child"]
    candidate_ids = {
        item["chunk_id"]
        for item in packet["review_items"][0]["retrieval_candidates"]
    }
    assert parent_id not in candidate_ids
    assert accepted_child_id not in candidate_ids
    assert candidate_ids == {_chunk_id(record) for record in records[2:]}


def test_review_packet_accepts_maximum_table_family_evidence(tmp_path):
    row_count = table_retrieval_core.MAX_TABLE_CHILDREN_PER_PARENT
    rows = "\n".join(
        f"| Rule {index:03d} | Result {index:03d} |"
        for index in range(row_count)
    )
    parent = {
        "text": (
            "Rule table\n"
            "| Rule | Result |\n"
            "| --- | --- |\n"
            f"{rows}\n"
        ),
        "metadata": {
            "source_file": "Private_Book",
            "content_type": "table",
            "content_source": "table",
            "page_start": 1,
            "page_end": 2,
            "page_range": "pp.1-2",
            "table_rows": row_count,
            "table_cols": 2,
            "token_count": 1000,
        },
    }
    records = table_retrieval_core.expand_table_records(
        [parent], stable_id_fn=_chunk_id,
        token_count_fn=lambda value: len(value.split()))
    chunks_path = tmp_path / "max-table-chunks.jsonl"
    chunks_raw = _write_jsonl(chunks_path, records)
    parent_id = _chunk_id(records[0])
    accepted_child_ids = sorted(_chunk_id(record) for record in records[1:])
    query = {
        "query_id": "table-max-q1",
        "query": "Which rows are accepted answers?",
        "review_status": "draft_requires_corpus_owner",
        "judgments": [{
            "chunk_id": parent_id,
            "relevance": 3,
            "table_family": {
                "schema_version": 1,
                "accepted_child_chunk_ids": accepted_child_ids,
            },
        }],
        "corpus": {
            "sha256": hashlib.sha256(chunks_raw).hexdigest(),
            "record_count": len(records),
            "id_scheme": "retrieval_core._chunk_id",
        },
    }
    queries_path = tmp_path / "max-table-queries.jsonl"
    _write_jsonl(queries_path, [query])
    packet_path = tmp_path / "max-table-review.json"

    evaluation_review.prepare_review_packet(
        queries_path, chunks_path, packet_path)

    packet = json.loads(packet_path.read_text(encoding="utf-8"))
    evidence = packet["review_items"][0]["judgment_reviews"][0]["evidence"]
    assert len(evidence) == row_count + 1
    assert evidence[0]["chunk_id"] == parent_id
    assert {item["chunk_id"] for item in evidence[1:]} == set(
        accepted_child_ids)


def test_prepare_packet_deduplicates_source_judgment_evidence(tmp_path):
    queries, chunks, source_queries, source_chunks = _draft_fixture(tmp_path)
    source_queries[0]["judgments"] = [{
        "source_id": "Private_Book",
        "relevance": 3,
    }]
    _write_jsonl(queries, source_queries)
    packet_path = tmp_path / "packet.json"

    evaluation_review.prepare_review_packet(queries, chunks, packet_path)

    packet = json.loads(packet_path.read_text(encoding="utf-8"))
    evidence = packet["review_items"][0]["judgment_reviews"][0]["evidence"]
    assert [item["chunk_id"] for item in evidence] == [
        _chunk_id(chunk) for chunk in source_chunks
    ]


def test_prepare_packet_adds_exact_unjudged_retrieval_candidates(tmp_path):
    queries_path, chunks_path, queries, chunks = _draft_fixture(tmp_path)
    diagnostic = tmp_path / "compare.full.json"
    packet_path = tmp_path / "packet.json"
    _write_full_compare_report(
        diagnostic, queries_path, chunks_path, queries, chunks)

    summary = evaluation_review.prepare_review_packet(
        queries_path, chunks_path, packet_path,
        diagnostic_report_path=diagnostic,
        candidate_depth=2,
    )

    packet = json.loads(packet_path.read_text(encoding="utf-8"))
    assert summary["retrieval_candidate_count"] == 2
    assert packet["source"]["diagnostic_report"] == {
        "schema_version": retrieval_eval.REPORT_SCHEMA_VERSION,
        "mode": "compare",
        "sha256": hashlib.sha256(diagnostic.read_bytes()).hexdigest(),
        "candidate_depth": 2,
    }
    assert packet["review_items"][0]["retrieval_candidates"] == []
    negative_candidates = packet["review_items"][1]["retrieval_candidates"]
    assert {item["chunk_id"] for item in negative_candidates} == {
        _chunk_id(chunk) for chunk in chunks}
    assert all(len(item["appearances"]) == 4 for item in negative_candidates)
    assert all(item["evidence"]["text"] for item in negative_candidates)

    report = json.loads(diagnostic.read_text(encoding="utf-8"))
    report["configurations"][0]["configuration"]["index_snapshot"][
        "source_sha256"] = "f" * 64
    diagnostic.write_text(json.dumps(report), encoding="utf-8")
    with pytest.raises(ValueError, match="exact queries and corpus"):
        evaluation_review.prepare_review_packet(
            queries_path, chunks_path, tmp_path / "bad-packet.json",
            diagnostic_report_path=diagnostic,
            candidate_depth=2,
        )


def test_finalize_requires_every_explicit_decision(tmp_path):
    queries, chunks, _source_queries, _source_chunks = _draft_fixture(tmp_path)
    packet = tmp_path / "packet.json"
    approved = tmp_path / "approved.jsonl"
    receipt = tmp_path / "receipt.json"
    evaluation_review.prepare_review_packet(queries, chunks, packet)

    with pytest.raises(ValueError, match="not fully approved"):
        evaluation_review.finalize_review_packet(
            packet, queries, chunks,
            approved_queries_path=approved,
            receipt_path=receipt,
            reviewer_id="Corpus Owner",
            reviewed_at="2026-07-24T18:30:00Z",
            attestation=evaluation_review.OWNER_ATTESTATION,
        )

    assert not approved.exists()
    assert not receipt.exists()


def test_finalize_emits_approved_queries_and_content_free_receipt(tmp_path):
    queries, chunks, _source_queries, source_chunks = _draft_fixture(tmp_path)
    packet = tmp_path / "packet.json"
    approved = tmp_path / "approved.jsonl"
    receipt = tmp_path / "receipt.json"
    evaluation_review.prepare_review_packet(queries, chunks, packet)
    _approve_packet(packet)

    summary = evaluation_review.finalize_review_packet(
        packet, queries, chunks,
        approved_queries_path=approved,
        receipt_path=receipt,
        reviewer_id="Corpus Owner",
        reviewed_at="2026-07-24T18:30:00Z",
        attestation=evaluation_review.OWNER_ATTESTATION,
    )
    approved_queries = [
        json.loads(line) for line in approved.read_text(encoding="utf-8").splitlines()
    ]
    receipt_payload = json.loads(receipt.read_text(encoding="utf-8"))

    assert summary["status"] == "approved"
    assert {query["review_status"] for query in approved_queries} == {"approved"}
    assert {
        query["approval"]["review_batch_id"] for query in approved_queries
    } == {summary["review_batch_id"]}
    assert receipt_payload["approved_queries"]["sha256"] == hashlib.sha256(
        approved.read_bytes()).hexdigest()
    validated = evaluation_review.validate_review_receipt_files(
        approved, chunks, receipt)
    assert validated["review_batch_id"] == summary["review_batch_id"]

    portable_receipt = receipt.read_text(encoding="utf-8")
    assert "Corpus Owner" not in portable_receipt
    assert source_chunks[0]["text"] not in portable_receipt
    assert _chunk_id(source_chunks[0]) not in portable_receipt
    assert "private/chunks.jsonl" not in portable_receipt


def test_finalize_rejects_evidence_tampering_even_when_decisions_approve(tmp_path):
    queries, chunks, _source_queries, _source_chunks = _draft_fixture(tmp_path)
    packet_path = tmp_path / "packet.json"
    evaluation_review.prepare_review_packet(queries, chunks, packet_path)
    packet = _approve_packet(packet_path)
    packet["review_items"][0]["judgment_reviews"][0]["evidence"][0][
        "text"] = "substituted evidence"
    packet_path.write_text(json.dumps(packet), encoding="utf-8")

    with pytest.raises(ValueError, match="provenance or evidence changed"):
        evaluation_review.finalize_review_packet(
            packet_path, queries, chunks,
            approved_queries_path=tmp_path / "approved.jsonl",
            receipt_path=tmp_path / "receipt.json",
            reviewer_id="Corpus Owner",
            reviewed_at="2026-07-24T18:30:00Z",
            attestation=evaluation_review.OWNER_ATTESTATION,
        )

    evaluation_review.prepare_review_packet(
        queries, chunks, packet_path, force=True)
    packet = _approve_packet(packet_path)
    packet["review_items"][0]["judgment_reviews"][0]["relevance"] = 3.0
    packet_path.write_text(json.dumps(packet), encoding="utf-8")
    with pytest.raises(ValueError, match="provenance or evidence changed"):
        evaluation_review.finalize_review_packet(
            packet_path, queries, chunks,
            approved_queries_path=tmp_path / "numeric-approved.jsonl",
            receipt_path=tmp_path / "numeric-receipt.json",
            reviewer_id="Corpus Owner",
            reviewed_at="2026-07-24T18:30:00Z",
            attestation=evaluation_review.OWNER_ATTESTATION,
        )


def test_receipt_validation_rejects_query_or_receipt_tampering(tmp_path):
    queries, chunks, _source_queries, _source_chunks = _draft_fixture(tmp_path)
    packet = tmp_path / "packet.json"
    approved = tmp_path / "approved.jsonl"
    receipt = tmp_path / "receipt.json"
    evaluation_review.prepare_review_packet(queries, chunks, packet)
    _approve_packet(packet)
    evaluation_review.finalize_review_packet(
        packet, queries, chunks,
        approved_queries_path=approved,
        receipt_path=receipt,
        reviewer_id="Corpus Owner",
        reviewed_at="2026-07-24T18:30:00Z",
        attestation=evaluation_review.OWNER_ATTESTATION,
    )
    receipt_raw = receipt.read_text(encoding="utf-8")

    approved_raw = approved.read_text(encoding="utf-8")
    tampered_queries = approved_raw.replace(
        "Where does", "Precisely where does")
    approved.write_text(tampered_queries, encoding="utf-8")
    with pytest.raises(ValueError, match="does not bind the approved query set"):
        evaluation_review.validate_review_receipt_files(approved, chunks, receipt)

    approved.write_bytes(approved_raw.encode("utf-8"))
    receipt_payload = json.loads(receipt_raw)
    receipt_payload["packet"]["sha256"] = "f" * 64
    receipt.write_text(json.dumps(receipt_payload), encoding="utf-8")
    with pytest.raises(ValueError, match="approval batch digest is invalid"):
        evaluation_review.validate_review_receipt_files(approved, chunks, receipt)

    receipt_payload = json.loads(receipt_raw)
    receipt_payload["coverage"]["expected_abstention_count"] = True
    receipt.write_text(json.dumps(receipt_payload), encoding="utf-8")
    with pytest.raises(ValueError, match="coverage does not match"):
        evaluation_review.validate_review_receipt_files(approved, chunks, receipt)


def test_strict_packet_parser_rejects_duplicates_and_nonfinite_numbers():
    with pytest.raises(ValueError, match="duplicate field"):
        evaluation_review._strict_json_bytes(
            b'{"schema_version":1,"schema_version":1}',
            label="review packet", max_bytes=1024)
    with pytest.raises(ValueError, match="non-finite"):
        evaluation_review._strict_json_bytes(
            b'{"value":NaN}', label="review packet", max_bytes=1024)


def test_query_schema_binds_approval_to_approved_status():
    query = {
        "query": "approved evidence",
        "expected_keywords": ["evidence"],
        "review_status": "draft_requires_corpus_owner",
        "approval": {
            "schema_version": 1,
            "review_batch_id": "a" * 64,
        },
    }

    with pytest.raises(ValueError, match="requires review_status"):
        retrieval_eval._validate_query(query)

    query["review_status"] = "approved"
    retrieval_eval._validate_query(query)

    query["approval"]["schema_version"] = True
    with pytest.raises(ValueError, match="schema-v1 review batch binding"):
        retrieval_eval._validate_query(query)


def test_release_gate_requires_receipt_for_receipt_bound_queries(
        tmp_path, caplog):
    queries = tmp_path / "approved.jsonl"
    _write_jsonl(queries, [{
        "query_id": "approved-q1",
        "query": "approved evidence",
        "expected_keywords": ["evidence"],
        "review_status": "approved",
        "approval": {
            "schema_version": 1,
            "review_batch_id": "a" * 64,
        },
    }])

    exit_code = retrieval_eval.main([
        "--queries", str(queries),
        "--chunks", str(tmp_path / "missing.jsonl"),
        "--db", str(tmp_path / "db"),
        "--collection", "book",
        "--fail-under", "mrr=0.5",
    ])

    assert exit_code == 1
    assert "Receipt-bound release thresholds require" in caplog.text
