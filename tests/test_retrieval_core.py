import hashlib
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


def _source_bound_record(
        *, source_file="sample_run_4", source_name="sample-source.pdf",
        source_size=12_345, source_sha256="a" * 64):
    return {
        "text": "A calibrated device records each verified measurement.",
        "metadata": {
            "source_file": source_file,
            "page_start": 3,
            "page_end": 3,
            "page_range": "3",
            "source_items": [{"ref": "#/texts/7"}],
            retrieval_core.STABLE_ID_SOURCE_FIELD: {
                "name": source_name,
                "size": source_size,
                "sha256": source_sha256,
            },
        },
    }


def test_source_bound_chunk_id_ignores_run_display_name():
    first = _source_bound_record(source_file="sample_run_3")
    second = _source_bound_record(source_file="sample_run_4")

    assert retrieval_core._chunk_id(first) == retrieval_core._chunk_id(second)
    assert retrieval_core._legacy_chunk_id(first) != (
        retrieval_core._legacy_chunk_id(second))


def test_chunk_id_scheme_reports_source_bound_v2_and_rejects_mixing():
    source_bound = _source_bound_record()
    legacy = _linked_record("legacy", 0)

    assert retrieval_core._chunk_id_scheme([source_bound]) == (
        retrieval_core.SOURCE_BOUND_CHUNK_ID_SCHEME)
    assert retrieval_core._chunk_id_scheme([legacy]) == (
        retrieval_core.LEGACY_CHUNK_ID_SCHEME)
    with pytest.raises(ValueError, match="mixes legacy and source-bound"):
        retrieval_core._chunk_id_scheme([source_bound, legacy])


@pytest.mark.parametrize(
    "changed_source",
    [
        {"source_name": "sample-source-revised.pdf"},
        {"source_size": 12_346},
        {"source_sha256": "b" * 64},
    ],
)
def test_source_bound_chunk_id_changes_with_original_source_identity(
        changed_source):
    original = _source_bound_record()
    changed = _source_bound_record(**changed_source)

    assert retrieval_core._chunk_id(original) != retrieval_core._chunk_id(
        changed)


def test_chunk_id_preserves_explicit_legacy_contract_without_source_binding():
    first = _linked_record("legacy text", 0, source="sample_run_3")
    second = _linked_record("legacy text", 0, source="sample_run_4")

    assert retrieval_core._chunk_id(first) == (
        retrieval_core._legacy_chunk_id(first))
    assert retrieval_core._chunk_id(second) == (
        retrieval_core._legacy_chunk_id(second))
    assert retrieval_core._chunk_id(first) != retrieval_core._chunk_id(second)


@pytest.mark.parametrize(
    "source",
    [
        {"name": "sample-source.pdf", "size": 12_345},
        {"name": "folder/sample-source.pdf", "size": 12_345,
         "sha256": "a" * 64},
        {"name": "sample-source.pdf", "size": 0, "sha256": "a" * 64},
        {"name": "sample-source.pdf", "size": True, "sha256": "a" * 64},
        {"name": "sample-source.pdf", "size": 12_345,
         "sha256": "A" * 64},
    ],
)
def test_source_bound_chunk_id_rejects_malformed_source_binding(source):
    record = _source_bound_record()
    record["metadata"][retrieval_core.STABLE_ID_SOURCE_FIELD] = source

    with pytest.raises(ValueError, match="stable_id_source"):
        retrieval_core._chunk_id(record)


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
    assert response.context_characters >= sum(
        len(segment.text) for hit in response.hits
        for segment in hit.context_segments)
    assert response.context_characters <= (
        retrieval_core.DEFAULT_CONTEXT_MAX_CHARACTERS)


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
        content_type="author_narrative", max_characters=1000,
        segment_characters=5)

    assert len(response.hits[0].context_segments) == 1
    segment = response.hits[0].context_segments[0]
    assert segment.relation == "next"
    assert segment.text == "incl\u2026"
    assert 5 < response.context_characters <= 1000


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


def test_context_metadata_is_bounded_and_charged_to_the_global_budget():
    records = [
        _linked_record("primary", 0),
        _linked_record("next evidence", 1),
    ]
    records[1]["metadata"]["section_path"] = "x" * 100_000
    retrieval_core._attach_retrieval_linkage(records)
    response = retrieval_core.SearchResponse(
        hits=[retrieval_core.SearchHit(
            records[0]["text"], dict(records[0]["metadata"]), 0.9,
            source_id=records[0]["metadata"]["stable_id"])],
        backend="chroma",
        requested_mode="vector",
        effective_mode="vector",
        reranker_applied=False,
    )

    retrieval_core._assemble_retrieval_context(
        response, records, context_window=1,
        max_characters=2_000, segment_characters=4)

    segment = response.hits[0].context_segments[0]
    assert segment.text == "nex\u2026"
    assert len(segment.metadata["section_path"]) <= (
        retrieval_core._METADATA_STRING_CHAR_LIMIT)
    assert 4 < response.context_characters <= 2_000
    prompt = retrieval_core._grounded_answer_prompt(
        "What follows?", retrieval_core._grounded_sources(response))
    assert "x" * 100_000 not in prompt
    assert len(prompt) < 10_000


# Sparse-vector indices are persisted in Qdrant collections, so these digests
# must stay byte-identical across any change to how the hash is computed.
# Synthetic and public-citation tokens only.
_STABLE_TOKEN_HASH_GOLDEN = {
    "": 3558706393,
    "a": 214005177,
    "the": 2411998317,
    "rule": 2551979643,
    "section": 1943352366,
    "negligence": 2281413742,
    "duty": 2714776504,
    "res": 2602594663,
    "ipsa": 3534904213,
    "loquitur": 3739756339,
    "usc": 2472078972,
    "us": 188454906,
    "sct": 82664998,
    "f3d": 833220109,
    "fsupp3d": 2313342258,
    "1332": 685902262,
    "1332(a)(1)": 2845635709,
    "12(b)(6)": 3657141309,
    "326_us_310": 4180724443,
    "usd_75000": 3514950000,
    "usd_10_50": 3273113821,
    "don't": 1500549786,
    "3.5": 2151704230,
    "§": 3181005861,
    "é": 1725812119,
    "ü": 3224637605,
    "中文": 2814034467,
    "\U0001f600": 704834243,
}
_SYNTHETIC_TOKEN_ALPHABET = (
    "abcdefghijklmnopqrstuvwxyz0123456789_()'."
    "§éü中文\U0001f600"
)


def _synthetic_tokens(count):
    # A fixed 64-bit LCG keeps the sample identical on every Python version.
    state = 0x2545F491
    tokens = []
    for index in range(count):
        chars = []
        for _ in range(index % 25):
            state = (
                state * 6364136223846793005 + 1442695040888963407
            ) % 2**64
            chars.append(
                _SYNTHETIC_TOKEN_ALPHABET[
                    (state >> 33) % len(_SYNTHETIC_TOKEN_ALPHABET)
                ]
            )
        tokens.append("".join(chars))
    return tokens


def test_stable_token_hash_matches_persisted_sparse_indices():
    assert rag._stable_token_hash is retrieval_core._stable_token_hash
    assert {
        token: retrieval_core._stable_token_hash(token)
        for token in _STABLE_TOKEN_HASH_GOLDEN
    } == _STABLE_TOKEN_HASH_GOLDEN


def test_stable_token_hash_is_unchanged_over_a_broad_synthetic_sample():
    digest = hashlib.sha256()
    for token in _synthetic_tokens(5000):
        digest.update(
            retrieval_core._stable_token_hash(token).to_bytes(4, "big"))

    assert digest.hexdigest() == (
        "cafef627737a9ed818a70fcb109208c627cace823cbae1715460b52caad2294c"
    )


def test_stable_token_hash_rejects_unencodable_tokens():
    with pytest.raises(UnicodeEncodeError):
        retrieval_core._stable_token_hash("\ud800")


def test_sparse_token_vector_counts_terms_under_the_stable_hash():
    assert rag._sparse_token_vector is retrieval_core._sparse_token_vector
    assert retrieval_core._sparse_token_vector(
        "negligence duty negligence"
    ) == (
        [
            _STABLE_TOKEN_HASH_GOLDEN["negligence"],
            _STABLE_TOKEN_HASH_GOLDEN["duty"],
        ],
        [2.0, 1.0],
    )
