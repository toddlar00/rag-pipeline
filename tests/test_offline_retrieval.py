import json

import pytest

from offline_retrieval import OfflineBM25Index
from retrieval_core import _chunk_id


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
