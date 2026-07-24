"""Deterministic no-model retrieval used by the checked-in evaluation suites.

This adapter is intentionally lower fidelity than dense production retrieval.
It exists so CI can exercise corpus integrity, filters, ranking metrics, and
adversarial fixtures without network access or multi-gigabyte model downloads.
"""

from __future__ import annotations

import hashlib
import json
import math
from collections import Counter
from dataclasses import dataclass
from pathlib import Path

from retrieval_core import (
    _chunk_id,
    _legal_search_tokens,
    _lexical_document_text,
)
import table_retrieval_core


_STOP_WORDS = frozenset({
    "a", "an", "and", "are", "as", "at", "be", "because", "been", "but",
    "by", "can", "could", "did", "do", "does", "for", "from", "had", "has",
    "have", "how", "if", "in", "into", "is", "it", "its", "may", "must",
    "not", "of", "on", "or", "that", "the", "their", "this", "to", "under",
    "was", "were", "what", "when", "where", "whether", "which", "who",
    "why", "will", "with", "would",
})
OFFLINE_RETRIEVER_VERSION = 2
MIN_CONTENT_TERM_MATCHES = 2
_STOP_WORDS_SHA256 = hashlib.sha256(
    "\n".join(sorted(_STOP_WORDS)).encode("utf-8")).hexdigest()


def _content_tokens(text: str) -> list[str]:
    return [token for token in _legal_search_tokens(text)
            if token not in _STOP_WORDS]


@dataclass(frozen=True)
class OfflineIndexSnapshot:
    source_sha256: str
    source_record_count: int
    table_child_count: int
    stable_ids: tuple[str, ...]

    def as_report_dict(self) -> dict:
        return {
            "source_sha256": self.source_sha256,
            "source_record_count": self.source_record_count,
            "record_count": self.source_record_count,
            "table_child_count": self.table_child_count,
            "id_scheme": "retrieval_core._chunk_id",
            "retriever_implementation": (
                f"offline_retrieval.OfflineBM25Index/v{OFFLINE_RETRIEVER_VERSION}"),
            "scoring": "BM25 Okapi with non-negative Robertson IDF",
            "minimum_content_term_matches": MIN_CONTENT_TERM_MATCHES,
            "stop_words_sha256": _STOP_WORDS_SHA256,
        }


class OfflineBM25Index:
    """Small deterministic BM25 index over a pinned chunks JSONL artifact."""

    def __init__(self, records: list[dict], source_sha256: str):
        if not records:
            raise ValueError("offline evaluation corpus must not be empty")
        self.records = tuple(records)
        self.stable_ids = tuple(_chunk_id(record) for record in records)
        if len(set(self.stable_ids)) != len(self.stable_ids):
            raise ValueError("offline evaluation corpus has duplicate stable IDs")
        self.snapshot = OfflineIndexSnapshot(
            source_sha256=source_sha256,
            source_record_count=len(records),
            table_child_count=table_retrieval_core.table_child_count(records),
            stable_ids=self.stable_ids,
        )
        self._tokens = tuple(
            _content_tokens(_lexical_document_text(
                record["text"], record["metadata"]))
            for record in records
        )
        self._frequencies = tuple(Counter(tokens) for tokens in self._tokens)
        self._document_lengths = tuple(len(tokens) for tokens in self._tokens)
        self._average_length = (
            sum(self._document_lengths) / len(self._document_lengths)) or 1.0
        document_frequency = Counter()
        for frequencies in self._frequencies:
            document_frequency.update(frequencies.keys())
        corpus_size = len(records)
        self._idf = {
            token: math.log(
                1 + (corpus_size - frequency + 0.5) / (frequency + 0.5))
            for token, frequency in document_frequency.items()
        }

    @classmethod
    def from_jsonl(cls, path: Path) -> "OfflineBM25Index":
        raw = Path(path).read_bytes()
        source_sha256 = hashlib.sha256(raw).hexdigest()
        records = []
        for line_number, line in enumerate(
                raw.decode("utf-8-sig").splitlines(), 1):
            if not line.strip():
                continue
            try:
                record = json.loads(line)
            except json.JSONDecodeError as exc:
                raise ValueError(
                    f"Invalid JSON at {path}:{line_number}: {exc.msg}") from exc
            if (not isinstance(record, dict)
                    or not isinstance(record.get("text"), str)
                    or not record["text"].strip()
                    or not isinstance(record.get("metadata"), dict)):
                raise ValueError(
                    f"{path}:{line_number} must contain non-empty text and "
                    "a metadata object")
            records.append(record)
        return cls(records, source_sha256)

    def search(self, query: str, *, n_results: int = 10,
               content_type: str | None = None,
               chapter_num: int | None = None) -> list[dict]:
        if not isinstance(query, str) or not query.strip():
            raise ValueError("offline query must not be blank")
        if (isinstance(n_results, bool) or not isinstance(n_results, int)
                or n_results < 1):
            raise ValueError("n_results must be a positive integer")
        query_tokens = _content_tokens(query)
        required_matches = (
            1 if len(set(query_tokens)) <= MIN_CONTENT_TERM_MATCHES
            else MIN_CONTENT_TERM_MATCHES)
        ranked = []
        for index, (record, stable_id) in enumerate(
                zip(self.records, self.stable_ids)):
            metadata = record["metadata"]
            if (content_type is not None
                    and metadata.get("content_type") != content_type):
                continue
            if (chapter_num is not None
                    and metadata.get("chapter_num") != chapter_num):
                continue
            if len(set(query_tokens) & self._frequencies[index].keys()) < (
                    required_matches):
                continue
            score = self._score(index, query_tokens)
            if score <= 0:
                continue
            source_id = metadata.get("source_id") or metadata.get("source_file")
            ranked.append((
                -score,
                stable_id,
                {
                    "text": record["text"],
                    "metadata": dict(metadata),
                    "score": round(score, 12),
                    "chunk_id": stable_id,
                    "source_id": str(source_id) if source_id else None,
                },
            ))
        ranked.sort(key=lambda item: (item[0], item[1]))
        ordered = [item[2] for item in ranked]
        keep_indexes = table_retrieval_core.table_candidate_keep_indexes(
            [result["metadata"] for result in ordered])
        return [ordered[index] for index in keep_indexes[:n_results]]

    def _score(self, index: int, query_tokens: list[str], *,
               k1: float = 1.5, b: float = 0.75) -> float:
        frequencies = self._frequencies[index]
        length = self._document_lengths[index]
        score = 0.0
        for token in sorted(set(query_tokens)):
            frequency = frequencies.get(token, 0)
            if not frequency:
                continue
            denominator = frequency + k1 * (
                1 - b + b * length / self._average_length)
            score += self._idf[token] * frequency * (k1 + 1) / denominator
        return score
