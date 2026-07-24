import json

import pytest

from offline_retrieval import OfflineBM25Index
from retrieval_core import _chunk_id
import table_retrieval_core


def _records():
    return [
        {
            "text": "Adverse possession requires hostile continuous possession.",
            "metadata": {
                "source_file": "property", "content_type": "doctrine",
                "chapter_num": 2,
            },
        },
        {
            "text": "Marbury v. Madison, 5 U.S. 137, concerns judicial review.",
            "metadata": {
                "source_file": "constitutional", "content_type": "case_summary",
                "chapter_num": 1,
            },
        },
        {
            "text": "A garden catalog describes trees and watering schedules.",
            "metadata": {
                "source_file": "distractor", "content_type": "distractor",
                "chapter_num": 9,
            },
        },
    ]


def _write_chunks(path, records=None):
    records = _records() if records is None else records
    path.write_text(
        "".join(json.dumps(record) + "\n" for record in records),
        encoding="utf-8",
    )


def test_offline_bm25_is_deterministic_and_returns_stable_ids(tmp_path):
    chunks = tmp_path / "chunks.jsonl"
    records = _records()
    _write_chunks(chunks, records)
    index = OfflineBM25Index.from_jsonl(chunks)

    first = index.search("hostile continuous adverse possession", n_results=3)
    second = index.search("hostile continuous adverse possession", n_results=3)

    assert first == second
    assert first[0]["chunk_id"] == _chunk_id(records[0])
    assert first[0]["source_id"] == "property"
    assert index.snapshot.source_record_count == 3
    assert index.snapshot.table_child_count == 0
    assert len(index.snapshot.source_sha256) == 64


def test_offline_bm25_applies_both_metadata_filters(tmp_path):
    chunks = tmp_path / "chunks.jsonl"
    _write_chunks(chunks)
    index = OfflineBM25Index.from_jsonl(chunks)

    found = index.search(
        "hostile continuous possession", content_type="doctrine",
        chapter_num=2)
    excluded = index.search(
        "hostile continuous possession", content_type="case_summary",
        chapter_num=1)

    assert len(found) == 1
    assert excluded == []


def test_offline_bm25_abstains_without_two_content_term_matches(tmp_path):
    chunks = tmp_path / "chunks.jsonl"
    _write_chunks(chunks)
    index = OfflineBM25Index.from_jsonl(chunks)

    assert index.search(
        "Which treaty fixes lithium royalties for a mineral estate?") == []


def test_offline_corpus_rejects_duplicate_stable_ids(tmp_path):
    chunks = tmp_path / "chunks.jsonl"
    record = _records()[0]
    _write_chunks(chunks, [record, record])

    with pytest.raises(ValueError, match="duplicate stable IDs"):
        OfflineBM25Index.from_jsonl(chunks)


def test_header_propagated_table_rows_improve_header_dependent_relevance(
        tmp_path):
    parent = {
        "text": """Fee restrictions
| Arrangement | Required safeguard |
| --- | --- |
| Contingent fee | Written agreement |
| Fee division | Client consent |
| Business transaction | Independent advice |
| Aggregate settlement | Informed consent |
""",
        "metadata": {
            "source_file": "ethics",
            "content_type": "table",
            "content_source": "table",
            "chapter_num": 9,
            "page_start": 100,
            "page_end": 100,
            "page_range": "p.100",
            "table_rows": 4,
            "table_cols": 2,
            "token_count": 30,
        },
    }
    records = table_retrieval_core.expand_table_records(
        [parent], stable_id_fn=_chunk_id,
        token_count_fn=lambda value: len(value.split()),
    )
    chunks = tmp_path / "table_chunks.jsonl"
    _write_chunks(chunks, records)
    index = OfflineBM25Index.from_jsonl(chunks)

    results = index.search(
        "Which contingent fee requires a written agreement?",
        content_type="table", chapter_num=9, n_results=5)

    assert results[0]["metadata"]["retrieval_role"] == "table_child"
    assert results[0]["metadata"]["table_child_index"] == 0
    assert results[0]["chunk_id"] != (
        results[0]["metadata"]["table_parent_stable_id"])
    assert "Required safeguard" in results[0]["text"]
    assert _chunk_id(records[0]) not in {
        result["chunk_id"] for result in results}
    assert index.snapshot.as_report_dict()["table_child_count"] == 4


@pytest.mark.parametrize("payload", [
    {"text": "", "metadata": {}},
    {"text": "valid but metadata missing"},
    ["not", "an", "object"],
])
def test_offline_corpus_rejects_invalid_records(tmp_path, payload):
    chunks = tmp_path / "chunks.jsonl"
    chunks.write_text(json.dumps(payload) + "\n", encoding="utf-8")

    with pytest.raises(ValueError, match="non-empty text"):
        OfflineBM25Index.from_jsonl(chunks)
