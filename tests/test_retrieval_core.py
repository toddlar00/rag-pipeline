import subprocess
import sys

import pytest

import rag
import retrieval_core


def test_rag_reexports_retrieval_domain_models():
    assert rag.SearchHit is retrieval_core.SearchHit
    assert rag.ContextSourceAlias is retrieval_core.ContextSourceAlias
    assert rag.ContextSegment is retrieval_core.ContextSegment
    assert rag.GroundedSource is retrieval_core.GroundedSource
    assert rag.GroundedAnswer is retrieval_core.GroundedAnswer
    assert rag.SearchResponse is retrieval_core.SearchResponse


def test_retrieval_core_is_a_lightweight_leaf_module():
    result = subprocess.run(
        [
            sys.executable,
            "-c",
            (
                "import sys; import retrieval_core; "
                "forbidden = {'rag', 'llm_runtime', 'requests', 'chromadb', "
                "'qdrant_client'}; "
                "loaded = forbidden.intersection(sys.modules); "
                "assert not loaded, sorted(loaded)"
            ),
        ],
        capture_output=True,
        check=False,
        text=True,
        timeout=30,
    )

    assert result.returncode == 0, result.stderr


def test_search_hit_source_id_uses_current_rag_chunk_id(monkeypatch):
    observed = {}

    def fake_chunk_id(record):
        observed["record"] = record
        return "compatibility-id"

    monkeypatch.setattr(rag, "_chunk_id", fake_chunk_id)
    hit = rag.SearchHit(
        text="A retrieved passage.",
        metadata={"source_file": "book.pdf", "page_start": 4},
        score=0.9,
    )

    assert rag._search_hit_source_id(hit) == "compatibility-id"
    assert observed["record"] == {
        "text": hit.text,
        "metadata": hit.metadata,
    }


def test_grounded_sources_uses_current_rag_source_id_resolver(monkeypatch):
    hit = rag.SearchHit(
        text="A retrieved passage.",
        metadata={"forced_id": "patched-source"},
        score=0.9,
    )
    response = rag.SearchResponse(
        hits=[hit],
        backend="chroma",
        requested_mode="vector",
        effective_mode="vector",
        reranker_applied=False,
    )
    monkeypatch.setattr(
        rag, "_search_hit_source_id",
        lambda current_hit: current_hit.metadata["forced_id"],
    )

    sources = rag._grounded_sources(response)

    assert [source.source_id for source in sources] == ["patched-source"]
    assert hit.source_id == "patched-source"


def _linked_record(text, index, *, source="book.json", chapter=1,
                   content_type="author_narrative"):
    return {
        "text": text,
        "metadata": {
            "chunk_index": index,
            "source_file": source,
            "chapter_num": chapter,
            "content_type": content_type,
            "page_start": index + 1,
            "page_end": index + 1,
            "page_range": str(index + 1),
        },
    }


def test_retrieval_linkage_preserves_ids_and_tracks_final_order():
    records = [
        _linked_record("chapter one a", 0),
        _linked_record("chapter one b", 1),
        _linked_record("chapter two", 2, chapter=2),
        _linked_record("unknown chapter", 3, chapter=None),
        _linked_record("different source", 4, source="other.json"),
    ]
    ids_before = [retrieval_core._chunk_id(record) for record in records]
    hashes_before = [retrieval_core._chunk_hash(record) for record in records]

    attached_ids = retrieval_core._attach_retrieval_linkage(records)

    assert attached_ids == ids_before
    assert [retrieval_core._chunk_id(record) for record in records] == ids_before
    assert [retrieval_core._chunk_hash(record) for record in records] != (
        hashes_before)
    assert records[0]["metadata"]["previous_stable_id"] == ""
    assert records[0]["metadata"]["next_stable_id"] == ids_before[1]
    assert records[1]["metadata"]["previous_stable_id"] == ids_before[0]
    assert records[1]["metadata"]["next_stable_id"] == ""
    assert all(
        record["metadata"]["previous_stable_id"] == ""
        and record["metadata"]["next_stable_id"] == ""
        for record in records[2:]
    )
    assert retrieval_core._retrieval_linkage_issues(records) == {}


@pytest.mark.parametrize(
    ("field_name", "tampered_value", "issue_name", "record_index"),
    [
        ("retrieval_linkage_schema_version", 0, "schema_version", 0),
        ("stable_id", "chunk_forged", "stable_id", 0),
        ("context_parent_id", "context_forged", "context_parent_id", 0),
        ("previous_stable_id", "chunk_forged", "previous_stable_id", 0),
        ("next_stable_id", "chunk_forged", "next_stable_id", 0),
    ],
)
def test_retrieval_linkage_detects_every_tampered_field(
        field_name, tampered_value, issue_name, record_index):
    records = [
        _linked_record("first", 0),
        _linked_record("second", 1),
    ]
    retrieval_core._attach_retrieval_linkage(records)
    records[record_index]["metadata"][field_name] = tampered_value

    assert retrieval_core._retrieval_linkage_issues(records) == {
        issue_name: [record_index],
    }


def test_context_assembly_preserves_rank_and_deduplicates_overlap():
    records = [
        _linked_record(f"passage {index}", index)
        for index in range(5)
    ]
    retrieval_core._attach_retrieval_linkage(records)
    hits = [
        retrieval_core.SearchHit(
            records[index]["text"], dict(records[index]["metadata"]),
            1.0 - index / 10,
            source_id=records[index]["metadata"]["stable_id"],
        )
        for index in (1, 3)
    ]
    response = retrieval_core.SearchResponse(
        hits=hits,
        backend="chroma",
        requested_mode="vector",
        effective_mode="vector",
        reranker_applied=False,
    )

    retrieval_core._assemble_retrieval_context(
        response, records, context_window=2)

    assert [hit.text for hit in response.hits] == ["passage 1", "passage 3"]
    assert [
        segment.source_id for hit in response.hits
        for segment in hit.context_segments
    ] == [
        records[0]["metadata"]["stable_id"],
        records[2]["metadata"]["stable_id"],
        records[4]["metadata"]["stable_id"],
    ]
    assert response.hits[0].context_segments[0].relation == "previous"
    assert response.context_window == 2
    assert response.context_characters == sum(
        len(segment.text) for hit in response.hits
        for segment in hit.context_segments)


def test_context_assembly_enforces_filters_and_global_budget():
    records = [
        _linked_record("excluded previous", 0, content_type="footnote"),
        _linked_record("primary", 1),
        _linked_record("included next", 2),
    ]
    retrieval_core._attach_retrieval_linkage(records)
    primary = records[1]
    response = retrieval_core.SearchResponse(
        hits=[retrieval_core.SearchHit(
            primary["text"], dict(primary["metadata"]), 0.9,
            source_id=primary["metadata"]["stable_id"])],
        backend="qdrant",
        requested_mode="hybrid",
        effective_mode="hybrid",
        reranker_applied=False,
    )

    retrieval_core._assemble_retrieval_context(
        response, records, context_window=1,
        content_type="author_narrative", max_characters=5,
        segment_characters=5)

    assert len(response.hits[0].context_segments) == 1
    segment = response.hits[0].context_segments[0]
    assert segment.relation == "next"
    assert segment.text == "incl\u2026"
    assert response.context_characters == 5


@pytest.mark.parametrize("tamper", ["text", "chapter"])
def test_context_assembly_rejects_mismatched_vector_payload(tamper):
    records = [
        _linked_record("primary", 0),
        _linked_record("neighbor", 1),
    ]
    retrieval_core._attach_retrieval_linkage(records)
    metadata = dict(records[0]["metadata"])
    text = records[0]["text"]
    if tamper == "text":
        text = "stale vector payload"
    else:
        metadata["chapter_num"] = 999
    response = retrieval_core.SearchResponse(
        hits=[retrieval_core.SearchHit(
            text, metadata, 0.9,
            source_id=records[0]["metadata"]["stable_id"])],
        backend="chroma",
        requested_mode="vector",
        effective_mode="vector",
        reranker_applied=False,
    )

    with pytest.raises(ValueError, match="payload does not match"):
        retrieval_core._assemble_retrieval_context(
            response, records, context_window=1)


def test_context_assembly_renders_identical_text_once_with_all_provenance():
    records = [
        _linked_record("identical neighboring evidence", 0),
        _linked_record("primary", 1),
        _linked_record("identical neighboring evidence", 2),
    ]
    retrieval_core._attach_retrieval_linkage(records)
    primary = records[1]
    response = retrieval_core.SearchResponse(
        hits=[retrieval_core.SearchHit(
            primary["text"], dict(primary["metadata"]), 0.9,
            source_id=primary["metadata"]["stable_id"])],
        backend="chroma",
        requested_mode="vector",
        effective_mode="vector",
        reranker_applied=False,
    )

    retrieval_core._assemble_retrieval_context(
        response, records, context_window=1)

    assert len(response.hits[0].context_segments) == 1
    segment = response.hits[0].context_segments[0]
    assert segment.source_id == records[0]["metadata"]["stable_id"]
    assert [alias.source_id for alias in segment.source_aliases] == [
        records[2]["metadata"]["stable_id"],
    ]
    sources = retrieval_core._grounded_sources(response)
    assert [source.text for source in sources].count(
        "identical neighboring evidence") == 1
    assert sources[1].metadata["equivalent_sources"][0]["source_id"] == (
        records[2]["metadata"]["stable_id"])
