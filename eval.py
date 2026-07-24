#!/usr/bin/env python3
"""
Evaluation harness for RAG retrieval quality.

Legacy query files may continue to use ``expected_keywords``.  For proper IR
metrics, queries may instead provide a finite, graded judgment set::

    {
      "query": "minimum contacts",
      "judgments": [
        {"chunk_id": "chunk_abc123", "relevance": 3},
        {"chunk_id": "chunk_def456", "relevance": 1}
      ],
      "expected_type": "case_opinion"
    }

Each judgment identifies either one stable ``chunk_id`` or ``source_id``; use
one identifier type consistently within a query.
Search results are matched against the same metadata fields; ``stable_id`` and
``source_file`` are accepted as current-backend compatibility aliases.
"""

import argparse
import hashlib
import json
import logging
import math
import os
import re
import sys
from pathlib import Path

import evaluation_metrics
import model_artifacts
import retrieval_core

sys.path.insert(0, str(Path(__file__).parent))

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s [%(levelname)s] %(message)s",
    datefmt="%H:%M:%S",
)
log = logging.getLogger(__name__)

DEFAULT_QUERIES = Path("eval_queries.jsonl")
DEFAULT_RETRIEVAL_DEPTH = 100
DEFAULT_RERANKER_MODEL = "BAAI/bge-reranker-v2-m3"
DEFAULT_OVERFETCH = 4
DEFAULT_RRF_K = 10
DEFAULT_DENSE_WEIGHT = 0.5
DEFAULT_SPARSE_WEIGHT = 1.0
DEFAULT_DB_LOCK_TIMEOUT = 30.0
DEFAULT_OPERATION_TIMEOUT = 14400.0
REPORT_SCHEMA_VERSION = 4
_JUDGMENT_ID_FIELDS = ("chunk_id", "source_id")
_ALLOWED_FILTER_FIELDS = ("content_type", "chapter_num")
_REVIEW_STATUSES = frozenset({"approved", "draft_requires_corpus_owner"})
_chunk_identity_cache: dict[str, tuple[tuple, str, dict]] = {}
_query_snapshot_sha256: dict[str, str] = {}


def load_queries(path: Path) -> list[dict]:
    """Load and validate evaluation queries from JSONL."""
    raw = Path(path).read_bytes()
    contents = raw.decode("utf-8-sig")
    queries = []
    for line_number, line in enumerate(contents.splitlines(), 1):
        if not line.strip():
            continue
        try:
            query = json.loads(line)
        except json.JSONDecodeError as exc:
            raise ValueError(
                f"Invalid JSON at {path}:{line_number}: {exc.msg}") from exc
        _validate_query(query, label=f"{path}:{line_number}")
        queries.append(query)
    if not queries:
        raise ValueError(f"No evaluation queries found in {path}")
    _query_snapshot_sha256[str(Path(path).resolve())] = hashlib.sha256(
        raw).hexdigest()
    return queries


def _validate_declared_corpus(queries: list[dict], chunks_path: Path) -> None:
    """Verify optional judged-set corpus fingerprints before scoring IDs."""
    if not any(isinstance(query.get("corpus"), dict) for query in queries):
        return
    if not chunks_path.is_file():
        raise FileNotFoundError(f"Chunks artifact not found: {chunks_path}")
    import rag
    raw, actual_hash, _ = rag._read_index_artifact_snapshot(chunks_path)
    try:
        contents = raw.decode("utf-8-sig")
    except UnicodeDecodeError:
        contents = raw.decode("latin-1")
    actual_count = sum(1 for line in contents.splitlines() if line.strip())
    _validate_declared_corpus_snapshot(
        queries, actual_hash=actual_hash, actual_count=actual_count)


def _validate_declared_corpus_snapshot(
        queries: list[dict], *, actual_hash: str, actual_count: int) -> None:
    """Validate declarations against one already-consumed corpus snapshot."""
    declarations = [
        query["corpus"] for query in queries
        if isinstance(query.get("corpus"), dict)
    ]
    if not declarations:
        return
    expected_hashes = {
        item["sha256"].lower()
        for item in declarations if item.get("sha256")
    }
    expected_counts = {
        item.get("record_count") for item in declarations
        if item.get("record_count") is not None
    }
    if len(expected_hashes) > 1 or len(expected_counts) > 1:
        raise ValueError("Evaluation queries declare inconsistent corpus snapshots")
    if expected_hashes:
        expected_hash = next(iter(expected_hashes))
        if actual_hash != expected_hash:
            raise ValueError(
                "Judged queries target a different chunks snapshot: "
                f"expected SHA-256 {expected_hash}, got {actual_hash}")
    if expected_counts:
        expected_count = next(iter(expected_counts))
        if actual_count != expected_count:
            raise ValueError(
                "Judged queries target a different record count: "
                f"expected {expected_count}, got {actual_count}")


def _validate_corpus_pin_coverage(queries: list[dict], *,
                                  required: bool = False) -> None:
    """Require one exact SHA/count declaration for every query in a pinned set."""
    declarations = [query.get("corpus") for query in queries]
    if not required and not any(isinstance(item, dict)
                                for item in declarations):
        return
    incomplete = [
        index for index, item in enumerate(declarations, 1)
        if not isinstance(item, dict)
        or "sha256" not in item
        or "record_count" not in item
    ]
    if incomplete:
        examples = ", ".join(str(index) for index in incomplete[:3])
        raise ValueError(
            "Corpus-pinned evaluation requires every query to declare the "
            "exact corpus SHA-256 and record count; incomplete query numbers: "
            + examples)


def _validate_release_gate_review_status(queries: list[dict]) -> None:
    """Prevent explicitly draft judgments from becoming release gates."""
    draft_numbers = [
        index for index, query in enumerate(queries, 1)
        if query.get("review_status") == "draft_requires_corpus_owner"
    ]
    if draft_numbers:
        examples = ", ".join(str(index) for index in draft_numbers[:3])
        raise ValueError(
            "Release thresholds cannot use judgments marked "
            "'draft_requires_corpus_owner'; review and mark them 'approved' "
            f"first (query numbers: {examples})"
        )


def _validate_judged_ids(queries: list[dict], records: list[dict],
                         chunk_id_fn) -> None:
    known_chunks = {chunk_id_fn(record) for record in records}
    known_sources = {
        str(value).strip()
        for record in records
        for value in (
            record.get("metadata", {}).get("source_id"),
            record.get("metadata", {}).get("source_file"),
        )
        if value not in (None, "")
    }
    judged_chunks = {
        judgment["chunk_id"].strip()
        for query in queries for judgment in query.get("judgments", [])
        if "chunk_id" in judgment
    }
    judged_sources = {
        judgment["source_id"].strip()
        for query in queries for judgment in query.get("judgments", [])
        if "source_id" in judgment
    }
    unknown = ((judged_chunks - known_chunks)
               | (judged_sources - known_sources))
    if unknown:
        examples = ", ".join(sorted(unknown)[:3])
        raise ValueError(
            "Judged IDs are absent from the declared chunks artifact: "
            + examples)


def _validate_declared_index_impl(
        queries: list[dict], chunks_path: Path, db_path: Path, *,
        db_backend: str, collection: str, embedding_model: str,
        rag_module=None, include_runtime_context: bool = False,
) -> dict | None:
    """Require an exact manifested index for corpus-pinned evaluations."""
    if not any(isinstance(query.get("corpus"), dict) for query in queries):
        return None
    if rag_module is None:
        import rag as rag_module

    rag_module._query_manifest_dimension(
        db_path, backend=db_backend, collection_name=collection,
        embedding_model=embedding_model)
    records, source_sha256, _ = (
        rag_module._load_index_snapshot_strict(chunks_path))
    expected_hashes = {
        rag_module._chunk_id(record): rag_module._chunk_hash(record)
        for record in records
    }
    if len(expected_hashes) != len(records):
        raise ValueError(
            "Chunks artifact contains duplicate stable IDs; indexing and "
            "evaluation are unsafe")

    manifest_path = rag_module._index_manifest_path(
        db_path, backend=db_backend, collection_name=collection)
    manifest = rag_module._load_index_manifest(
        db_path, backend=db_backend, collection_name=collection)
    if manifest is None:
        raise ValueError(
            "Corpus-pinned evaluation requires a compatible index manifest at "
            f"{manifest_path}. Re-run indexing for this collection.")
    dimension = manifest.get("embedding_dimension")
    if (isinstance(dimension, bool) or not isinstance(dimension, int)
            or dimension < 1):
        raise ValueError(
            f"Index manifest has an invalid embedding dimension: {manifest_path}")
    mismatch = rag_module._index_manifest_mismatch(
        manifest, backend=db_backend, collection_name=collection,
        embedding_model=embedding_model, embedding_dimension=dimension)
    if mismatch:
        raise ValueError(
            f"Corpus-pinned evaluation index is incompatible: {mismatch}. "
            "Re-run indexing for this collection.")

    indexed_hashes = manifest["chunk_hashes"]
    missing = set(expected_hashes) - set(indexed_hashes)
    extra = set(indexed_hashes) - set(expected_hashes)
    if missing or extra:
        raise ValueError(
            "Corpus-pinned evaluation index does not match the chunks artifact: "
            f"chunks={len(expected_hashes)}, manifest={len(indexed_hashes)}, "
            f"missing={len(missing)}, extra={len(extra)}. "
            "Re-run indexing for this collection.")
    if (manifest.get("source_sha256") != source_sha256
            or manifest.get("source_record_count") != len(records)):
        raise ValueError(
            "Index manifest was built from a different chunks artifact: "
            f"expected SHA-256 {source_sha256} and {len(records)} records, "
            f"got {manifest.get('source_sha256')} and "
            f"{manifest.get('source_record_count')}. Re-run indexing for this "
            "collection.")

    _validate_judged_ids(queries, records, rag_module._chunk_id)

    physical_count = rag_module._index_collection_count(
        db_path, collection, db_backend=db_backend)
    if physical_count != len(expected_hashes):
        raise ValueError(
            "Physical index count does not match the declared corpus: "
            f"expected {len(expected_hashes)}, got {physical_count}. "
            "Re-run indexing for this collection.")
    snapshot = {
        "manifest_path": str(manifest_path.resolve()),
        "schema_version": manifest["schema_version"],
        "embedding_dimension": dimension,
        "record_count": physical_count,
        "source_sha256": source_sha256,
        "model_artifact_lock_sha256": manifest[
            "model_artifact_lock_sha256"],
    }
    if include_runtime_context:
        snapshot["_identity_lookup"] = _chunk_identity_lookup_from_records(
            records, rag_module)
    return snapshot


def _validate_declared_index(
        queries: list[dict], chunks_path: Path, db_path: Path, *,
        db_backend: str, collection: str, embedding_model: str,
        rag_module=None, lock_timeout: float = DEFAULT_DB_LOCK_TIMEOUT,
        include_runtime_context: bool = False,
) -> dict | None:
    """Validate one coherent manifested/physical index generation."""
    if not any(isinstance(query.get("corpus"), dict) for query in queries):
        return None
    if rag_module is None:
        import rag as rag_module
    with rag_module._vector_store_lock(
            db_path, backend=db_backend, collection_name=collection,
            operation="evaluation integrity validation",
            timeout=lock_timeout):
        return _validate_declared_index_impl(
            queries, chunks_path, db_path,
            db_backend=db_backend, collection=collection,
            embedding_model=embedding_model, rag_module=rag_module,
            include_runtime_context=include_runtime_context)


def _normalize_judgments(query: dict) -> list[dict]:
    """Return normalized explicit judgments without mutating the query."""
    normalized = []
    for judgment in query.get("judgments", []):
        id_field = next(
            field for field in _JUDGMENT_ID_FIELDS if field in judgment)
        normalized.append({
            "id_type": id_field,
            "id": judgment[id_field].strip(),
            "relevance": float(judgment["relevance"]),
        })
    return normalized


def _validate_query(query: dict, *, label: str = "query") -> None:
    """Validate relevance, abstention, filter, and slice ground truth."""
    if not isinstance(query, dict):
        raise ValueError(f"{label} must be a JSON object")
    if not isinstance(query.get("query"), str) or not query["query"].strip():
        raise ValueError(f"{label} must contain a non-empty 'query' string")

    expected_abstain = query.get("expected_abstain", False)
    if not isinstance(expected_abstain, bool):
        raise ValueError(f"{label} 'expected_abstain' must be a boolean")

    keywords = query.get("expected_keywords")
    if keywords is not None and (
            not isinstance(keywords, list) or not keywords
            or not all(isinstance(item, str) and item.strip()
                       for item in keywords)):
        raise ValueError(
            f"{label} must contain non-empty string 'expected_keywords'")

    judgments = query.get("judgments")
    if judgments is not None:
        if not isinstance(judgments, list) or not judgments:
            raise ValueError(f"{label} 'judgments' must be a non-empty list")
        seen_ids = set()
        judgment_id_types = set()
        has_positive = False
        for index, judgment in enumerate(judgments, 1):
            judgment_label = f"{label} judgment #{index}"
            if not isinstance(judgment, dict):
                raise ValueError(f"{judgment_label} must be a JSON object")
            present_ids = [
                field for field in _JUDGMENT_ID_FIELDS
                if isinstance(judgment.get(field), str)
                and judgment[field].strip()
            ]
            if len(present_ids) != 1:
                raise ValueError(
                    f"{judgment_label} must contain exactly one non-empty "
                    "'chunk_id' or 'source_id'")
            relevance = judgment.get("relevance")
            if (isinstance(relevance, bool)
                    or not isinstance(relevance, (int, float))
                    or not math.isfinite(float(relevance))
                    or not 0 <= relevance <= 100):
                raise ValueError(
                    f"{judgment_label} 'relevance' must be a finite number "
                    "from 0 to 100")
            has_positive = has_positive or relevance > 0
            judgment_id = (present_ids[0], judgment[present_ids[0]].strip())
            judgment_id_types.add(present_ids[0])
            if judgment_id in seen_ids:
                raise ValueError(f"{judgment_label} duplicates {judgment_id!r}")
            seen_ids.add(judgment_id)
        if not has_positive:
            raise ValueError(f"{label} must have at least one relevant judgment")
        if len(judgment_id_types) > 1:
            raise ValueError(
                f"{label} judgments must use one ID type consistently; do not "
                "mix 'chunk_id' and 'source_id' in the same query")

    if keywords is not None and judgments is not None:
        raise ValueError(
            f"{label} must not mix 'expected_keywords' and graded 'judgments'")
    if expected_abstain and (keywords is not None or judgments is not None):
        raise ValueError(
            f"{label} expected-abstention cases cannot contain relevance ground "
            "truth")
    if not expected_abstain and keywords is None and judgments is None:
        raise ValueError(
            f"{label} must contain 'expected_keywords', graded 'judgments', or "
            "set 'expected_abstain' to true")

    expected_type = query.get("expected_type", "")
    if not isinstance(expected_type, str):
        raise ValueError(f"{label} 'expected_type' must be a string")
    if expected_abstain and expected_type:
        raise ValueError(
            f"{label} expected-abstention cases cannot require an expected type")

    filters = query.get("filters")
    if filters is not None:
        if not isinstance(filters, dict) or not filters:
            raise ValueError(f"{label} 'filters' must be a non-empty object")
        unknown = set(filters) - set(_ALLOWED_FILTER_FIELDS)
        if unknown:
            raise ValueError(
                f"{label} has unsupported filters: {', '.join(sorted(unknown))}")
        content_type = filters.get("content_type")
        if ("content_type" in filters
                and (not isinstance(content_type, str)
                     or not content_type.strip())):
            raise ValueError(
                f"{label} filter 'content_type' must be a non-empty string")
        chapter_num = filters.get("chapter_num")
        if ("chapter_num" in filters
                and (isinstance(chapter_num, bool)
                     or not isinstance(chapter_num, int)
                     or chapter_num < 0)):
            raise ValueError(
                f"{label} filter 'chapter_num' must be an integer >= 0")

    tags = query.get("tags")
    if tags is not None:
        if (not isinstance(tags, list) or not tags
                or not all(isinstance(tag, str) and tag.strip()
                           for tag in tags)):
            raise ValueError(
                f"{label} 'tags' must be a non-empty list of strings")
        normalized_tags = [tag.strip() for tag in tags]
        if len(set(normalized_tags)) != len(normalized_tags):
            raise ValueError(f"{label} 'tags' must not contain duplicates")
        tag_slugs = [_slice_slug(tag) for tag in normalized_tags]
        if (not all(tag_slugs)
                or len(set(tag_slugs)) != len(tag_slugs)):
            raise ValueError(
                f"{label} 'tags' must have unique ASCII metric names")

    for field in ("subject", "book", "difficulty"):
        value = query.get(field)
        if value is not None and (
                not isinstance(value, str) or not value.strip()):
            raise ValueError(f"{label} '{field}' must be a non-empty string")
        if value is not None and not _slice_slug(value):
            raise ValueError(
                f"{label} '{field}' must contain an ASCII letter or number")

    review_status = query.get("review_status")
    if review_status is not None and (
            not isinstance(review_status, str)
            or review_status not in _REVIEW_STATUSES):
        allowed = ", ".join(sorted(_REVIEW_STATUSES))
        raise ValueError(
            f"{label} 'review_status' must be one of: {allowed}")

    corpus = query.get("corpus")
    if corpus is not None:
        if not isinstance(corpus, dict):
            raise ValueError(f"{label} 'corpus' must be an object")
        digest = corpus.get("sha256")
        if digest is not None and (
                not isinstance(digest, str) or len(digest) != 64
                or any(char not in "0123456789abcdefABCDEF" for char in digest)):
            raise ValueError(
                f"{label} corpus 'sha256' must be a 64-character hex digest")
        count = corpus.get("record_count")
        if count is not None and (
                isinstance(count, bool) or not isinstance(count, int)
                or count < 1):
            raise ValueError(
                f"{label} corpus 'record_count' must be a positive integer")
        if digest is None and count is None:
            raise ValueError(
                f"{label} corpus must declare 'sha256' or 'record_count'")

    grounding_case = query.get("grounding_case")
    if grounding_case is not None:
        _validate_grounding_case(grounding_case, label=label)


def _validate_grounding_case(case: dict, *, label: str) -> None:
    if not isinstance(case, dict):
        raise ValueError(f"{label} 'grounding_case' must be an object")
    if not isinstance(case.get("answer"), str) or not case["answer"].strip():
        raise ValueError(
            f"{label} grounding case must contain a non-empty 'answer'")
    if not isinstance(case.get("expected_abstained"), bool):
        raise ValueError(
            f"{label} grounding case 'expected_abstained' must be a boolean")
    case_type = case.get("case_type")
    if case_type is not None and (
            not isinstance(case_type, str) or not _slice_slug(case_type)):
        raise ValueError(
            f"{label} grounding case 'case_type' must be a non-empty label")
    citations = case.get("expected_citations")
    if citations is not None and (
            not isinstance(citations, list)
            or not all(isinstance(value, str)
                       and re.fullmatch(r"S[1-9]\d*", value)
                       for value in citations)
            or len(set(citations)) != len(citations)):
        raise ValueError(
            f"{label} grounding case citations must use S-number strings")
    for field in ("warning_contains", "excerpt_contains"):
        values = case.get(field)
        if values is not None and (
                not isinstance(values, list)
                or not all(isinstance(value, str) and value
                           for value in values)):
            raise ValueError(
                f"{label} grounding case '{field}' must be a list of strings")


def keyword_hit(result_text: str, keywords: list[str]) -> bool:
    """Check if a result contains any expected keyword."""
    text_lower = result_text.lower()
    return any(keyword.lower() in text_lower for keyword in keywords)


def _chunk_identity_lookup(chunks_path: Path, rag_module) -> dict:
    """Map stored result metadata back to the stable IDs used for indexing.

    Qdrant returns ``stable_id`` in its payload. Chroma stores the same ID as
    the collection record ID but its public search response currently omits
    IDs, so evaluation recovers it from the source chunks artifact.
    """
    cache_key = str(chunks_path.resolve())
    raw, source_sha256, fingerprint = (
        rag_module._read_index_artifact_snapshot(chunks_path))
    cached = _chunk_identity_cache.get(cache_key)
    if cached and cached[:2] == (fingerprint, source_sha256):
        return cached[2]

    try:
        contents = raw.decode("utf-8-sig")
    except UnicodeDecodeError:
        contents = raw.decode("latin-1")
    records = [
        json.loads(line) for line in contents.splitlines() if line.strip()
    ]
    lookup = _chunk_identity_lookup_from_records(records, rag_module)

    _chunk_identity_cache[cache_key] = (
        fingerprint, source_sha256, lookup)
    return lookup


def _chunk_identity_lookup_from_records(records: list[dict], rag_module) -> dict:
    """Build stable-ID recovery maps from one already-validated snapshot."""
    lookup = {"source_index_text": {}, "index_text": {}, "text": {}}

    def add_unique(mapping: dict, key, value) -> None:
        if key is None:
            return
        if key in mapping and mapping[key] != value:
            mapping[key] = None
        else:
            mapping[key] = value

    for record in records:
        if (not isinstance(record, dict)
                or not isinstance(record.get("text"), str)
                or not isinstance(record.get("metadata"), dict)):
            continue
        metadata = record["metadata"]
        chunk_id = rag_module._chunk_id(record)
        chunk_index = metadata.get("chunk_index")
        source_file = metadata.get("source_file")
        add_unique(
            lookup["source_index_text"],
            (source_file, chunk_index, record["text"])
            if source_file is not None and chunk_index is not None else None,
            chunk_id,
        )
        add_unique(
            lookup["index_text"],
            (chunk_index, record["text"])
            if chunk_index is not None else None,
            chunk_id,
        )
        add_unique(lookup["text"], record["text"], chunk_id)
    return lookup


def _recover_chunk_id(result: dict, lookup: dict) -> str | None:
    metadata = result.get("metadata") or {}
    source_file = metadata.get("source_file")
    chunk_index = metadata.get("chunk_index")
    text = result.get("text", "")
    candidates = [
        lookup["source_index_text"].get((source_file, chunk_index, text)),
        lookup["index_text"].get((chunk_index, text)),
        lookup["text"].get(text),
    ]
    return next((value for value in candidates if value), None)


def run_search(query: str, db_path: Path, *,
               collection: str = "civpro",
               embedding_model: str = "nomic-ai/nomic-embed-text-v2-moe",
               n_results: int = 10,
               content_type: str | None = None,
               chapter_num: int | None = None,
               use_reranker: bool | None = None,
               hybrid: bool | None = None,
               chunks_path: Path | None = None,
               chunk_identity_lookup: dict | None = None,
               db_backend: str = "chroma",
               reranker_model: str = DEFAULT_RERANKER_MODEL,
               overfetch: int = DEFAULT_OVERFETCH,
               rrf_k: int = DEFAULT_RRF_K,
               dense_weight: float = DEFAULT_DENSE_WEIGHT,
               sparse_weight: float = DEFAULT_SPARSE_WEIGHT,
               context_window: int = 0,
               context_max_characters: int = (
                   retrieval_core.DEFAULT_CONTEXT_MAX_CHARACTERS),
               context_segment_characters: int = (
                   retrieval_core.DEFAULT_CONTEXT_SEGMENT_CHARACTERS),
               lock_timeout: float = DEFAULT_DB_LOCK_TIMEOUT) -> list[dict]:
    """Run a search and return results as plain dictionaries."""
    import rag  # lazy import to avoid loading models at import time

    search_options = {
        "db_backend": db_backend,
        "n_results": n_results,
        "collection_name": collection,
        "embedding_model": embedding_model,
        "content_type": content_type,
        "chapter_num": chapter_num,
        "use_reranker": use_reranker,
        "hybrid": hybrid,
        "reranker_model": reranker_model,
        "overfetch": overfetch,
        "rrf_k": rrf_k,
        "dense_weight": dense_weight,
        "sparse_weight": sparse_weight,
        "context_window": context_window,
        "context_max_characters": context_max_characters,
        "context_segment_characters": context_segment_characters,
        "lock_timeout": lock_timeout,
    }
    if chunks_path is not None:
        search_options["chunks_path"] = chunks_path
    response = rag.search_index(query, db_path, **search_options)

    results = [
        {"text": hit.text, "metadata": hit.metadata, "score": hit.score}
        for hit in response.hits
    ]
    if getattr(response, "context_window", 0):
        for result, hit in zip(results, response.hits):
            result["equivalent_sources"] = [
                {
                    "source_id": alias.source_id,
                    "metadata": alias.metadata,
                }
                for alias in hit.source_aliases
            ]
            result["context"] = [
                {
                    "text": segment.text,
                    "metadata": segment.metadata,
                    "source_id": segment.source_id,
                    "relation": segment.relation,
                    "distance": segment.distance,
                    "equivalent_sources": [
                        {
                            "source_id": alias.source_id,
                            "metadata": alias.metadata,
                        }
                        for alias in segment.source_aliases
                    ],
                }
                for segment in hit.context_segments
            ]
    if ((chunk_identity_lookup is not None
         or (chunks_path is not None and chunks_path.is_file()))
            and any("chunk_id" not in _result_identifiers(result)
                    for result in results)):
        try:
            lookup = (chunk_identity_lookup
                      if chunk_identity_lookup is not None
                      else _chunk_identity_lookup(chunks_path, rag))
        except (OSError, UnicodeError, json.JSONDecodeError, AttributeError) as exc:
            log.warning("Could not recover stable chunk IDs from %s: %s",
                        chunks_path, exc)
        else:
            for result in results:
                if "chunk_id" not in _result_identifiers(result):
                    chunk_id = _recover_chunk_id(result, lookup)
                    if chunk_id:
                        result["chunk_id"] = chunk_id
    return results


def _result_identifiers(result: dict) -> dict[str, str]:
    """Extract stable judged identifiers from a retrieval result."""
    metadata = result.get("metadata") or {}
    identifiers = {}
    chunk_id = (result.get("chunk_id") or metadata.get("chunk_id")
                or metadata.get("stable_id"))
    source_id = (result.get("source_id") or metadata.get("source_id")
                 or metadata.get("source_file"))
    if isinstance(chunk_id, str) and chunk_id.strip():
        identifiers["chunk_id"] = chunk_id.strip()
    if isinstance(source_id, str) and source_id.strip():
        identifiers["source_id"] = source_id.strip()
    return identifiers


def _grade_results(results: list[dict], judgments: list[dict]) -> list[dict]:
    """Grade ranked results, awarding each gold judgment at most once."""
    gold = {
        (judgment["id_type"], judgment["id"]): judgment["relevance"]
        for judgment in judgments if judgment["relevance"] > 0
    }
    seen = set()
    graded = []
    for result in results:
        identifiers = _result_identifiers(result)
        matched = [
            (id_type, value) for id_type, value in identifiers.items()
            if (id_type, value) in gold and (id_type, value) not in seen
        ]
        seen.update(matched)
        relevance = max((gold[key] for key in matched), default=0.0)
        graded.append({
            "relevance": relevance,
            "matched_judgments": matched,
            "identifiers": identifiers,
        })
    return graded


def _dcg(grades: list[float]) -> float:
    return sum(
        (2 ** grade - 1) / math.log2(rank + 2)
        for rank, grade in enumerate(grades)
    )


def _judged_metrics(graded: list[dict], judgments: list[dict],
                    k_values: list[int]) -> dict[str, float]:
    """Calculate finite-qrels Recall, nDCG, and average precision."""
    positive = [j for j in judgments if j["relevance"] > 0]
    total_relevant = len(positive)
    metrics = {}
    for k in k_values:
        top_k = graded[:k]
        matched = {
            key for item in top_k for key in item["matched_judgments"]
        }
        metrics[f"recall@{k}"] = len(matched) / total_relevant
        actual = [item["relevance"] for item in top_k]
        ideal = sorted(
            (item["relevance"] for item in positive), reverse=True)[:k]
        ideal_dcg = _dcg(ideal)
        metrics[f"ndcg@{k}"] = _dcg(actual) / ideal_dcg if ideal_dcg else 0.0

    relevant_seen = 0
    precision_sum = 0.0
    for rank, item in enumerate(graded, 1):
        if item["relevance"] > 0:
            relevant_seen += 1
            precision_sum += relevant_seen / rank
    metrics["average_precision"] = precision_sum / total_relevant
    return metrics


def _result_detail(result: dict, rank: int, relevance: float,
                   matched_judgments: list[tuple[str, str]],
                   keyword_matches: list[str] | None = None, *,
                   include_text: bool = False) -> dict:
    metadata = result.get("metadata") or {}
    identifiers = _result_identifiers(result)
    detail = {
        "rank": rank,
        "score": result.get("score"),
        "relevance": round(float(relevance), 6),
        "content_type": metadata.get("content_type", ""),
        "text_sha256": hashlib.sha256(
            str(result.get("text", "")).encode("utf-8")).hexdigest(),
    }
    if include_text:
        detail["chunk_id"] = identifiers.get("chunk_id")
        detail["source_id"] = identifiers.get("source_id")
        detail["matched_judgments"] = [
            {"id_type": id_type, "id": value}
            for id_type, value in matched_judgments
        ]
        detail["text_preview"] = str(result.get("text", ""))[:200]
    else:
        detail["chunk_id_sha256"] = _optional_text_sha256(
            identifiers.get("chunk_id"))
        detail["source_id_sha256"] = _optional_text_sha256(
            identifiers.get("source_id"))
        detail["matched_judgments"] = [
            {
                "id_type": id_type,
                "id_sha256": hashlib.sha256(value.encode("utf-8")).hexdigest(),
            }
            for id_type, value in matched_judgments
        ]
    if keyword_matches is not None:
        if include_text:
            detail["keyword_matches"] = keyword_matches
        else:
            detail["keyword_match_count"] = len(keyword_matches)
            detail["keyword_match_sha256"] = [
                hashlib.sha256(value.encode("utf-8")).hexdigest()
                for value in keyword_matches
            ]
    return detail


def _optional_text_sha256(value: str | None) -> str | None:
    return (hashlib.sha256(value.encode("utf-8")).hexdigest()
            if value is not None else None)


def _filters_match(result: dict, filters: dict) -> bool:
    metadata = result.get("metadata") or {}
    return all(metadata.get(key) == value for key, value in filters.items())


def _slice_names(query: dict, *, redact_names: bool = False) -> list[str]:
    values = []
    for tag in query.get("tags", []):
        values.append(f"tag/{_slice_slug(tag)}")
    for field in ("subject", "book", "difficulty"):
        if query.get(field):
            label = _slice_slug(query[field])
            if redact_names and field in {"subject", "book"}:
                label = hashlib.sha256(
                    query[field].encode("utf-8")).hexdigest()[:16]
            values.append(f"{field}/{label}")
    case_type = (query.get("grounding_case") or {}).get("case_type")
    if case_type:
        values.append(f"grounding_case/{_slice_slug(case_type)}")
    return values


def _slice_slug(value: str) -> str:
    return re.sub(r"[^a-z0-9_-]+", "_", value.casefold()).strip("_")


def _evaluate_grounding_case(
        results: list[dict], query_text: str, case: dict) -> tuple[float, dict]:
    from retrieval_core import (
        ContextSegment,
        ContextSourceAlias,
        SearchHit,
        SearchResponse,
        _grounded_sources,
        _validate_grounded_answer,
    )

    hits = []
    for result in results:
        identifiers = _result_identifiers(result)
        hit = SearchHit(
            text=str(result.get("text", "")),
            metadata=dict(result.get("metadata") or {}),
            score=float(result.get("score") or 0.0),
            source_id=identifiers.get("chunk_id")
            or identifiers.get("source_id") or "",
        )
        for segment in result.get("context") or []:
            hit.context_segments.append(ContextSegment(
                text=str(segment.get("text") or ""),
                metadata=dict(segment.get("metadata") or {}),
                source_id=str(segment.get("source_id") or ""),
                relation=str(segment.get("relation") or ""),
                distance=int(segment.get("distance") or 0),
                source_aliases=tuple(
                    ContextSourceAlias(
                        source_id=str(alias.get("source_id") or ""),
                        metadata=dict(alias.get("metadata") or {}),
                    )
                    for alias in segment.get("equivalent_sources") or []
                ),
            ))
        hit.source_aliases = [
            ContextSourceAlias(
                source_id=str(alias.get("source_id") or ""),
                metadata=dict(alias.get("metadata") or {}),
            )
            for alias in result.get("equivalent_sources") or []
        ]
        hits.append(hit)
    response = SearchResponse(
        hits=hits, backend="evaluation", requested_mode="evaluation",
        effective_mode="evaluation", reranker_applied=False)
    sources = _grounded_sources(response, query_text)
    answer = _validate_grounded_answer(case["answer"], sources)
    checks = {
        "abstained": answer.abstained == case["expected_abstained"],
    }
    if "expected_citations" in case:
        checks["citations"] = answer.citations == case["expected_citations"]
    if case.get("warning_contains"):
        checks["warnings"] = all(
            any(fragment in warning for warning in answer.warnings)
            for fragment in case["warning_contains"])
    if case.get("excerpt_contains"):
        checks["excerpts"] = all(
            any(fragment in source.excerpt for source in sources)
            for fragment in case["excerpt_contains"])
    return (1.0 if all(checks.values()) else 0.0), {
        "case_type": case.get("case_type"),
        "passed": all(checks.values()),
        "checks": checks,
        "actual_abstained": answer.abstained,
        "actual_citations": answer.citations,
        "warnings": answer.warnings,
        "source_count": len(sources),
    }


def _evaluate_impl(queries: list[dict], db_path: Path, *,
                   k_values: list[int] | None = None,
                   include_details: bool = False,
                   report_detail: str = "full",
                   search_fn=None,
                   collect_measurements: bool = False,
                   embedding_requests: bool = True,
                   embedding_cost_per_million_tokens: float | None = None,
                   llm_usage: dict | None = None,
                   llm_input_cost_per_million_tokens: float | None = None,
                   llm_output_cost_per_million_tokens: float | None = None,
                   measurement_collector=None,
                   **search_kwargs) -> dict:
    """Evaluate relevance, abstention, filter, and grounding behavior."""
    k_values = [5, 10] if k_values is None else sorted(set(k_values))
    if any(not isinstance(k, int) or isinstance(k, bool) or k < 1
           for k in k_values):
        raise ValueError("k_values must contain positive integers")
    if report_detail not in {"summary", "full"}:
        raise ValueError("report_detail must be 'summary' or 'full'")
    for query_index, query in enumerate(queries, 1):
        _validate_query(query, label=f"query #{query_index}")
    required_results = max(k_values, default=0)
    if required_results:
        configured_results = int(search_kwargs.get(
            "n_results", DEFAULT_RETRIEVAL_DEPTH))
        search_kwargs["n_results"] = max(required_results, configured_results)

    metric_values: dict[str, list[float]] = {
        **{f"success@{k}": [] for k in k_values},
        "mrr": [],
        "type_accuracy": [],
    }
    judged_metric_values: dict[str, list[float]] = {
        **{f"recall@{k}": [] for k in k_values},
        **{f"ndcg@{k}": [] for k in k_values},
        "map": [],
    }
    auxiliary_metric_values: dict[str, list[float]] = {
        "abstention_accuracy": [],
        "false_answer_rate": [],
        "filter_compliance": [],
        "grounding_accuracy": [],
    }
    slice_values: dict[str, dict[str, list[float]]] = {}
    slice_counts: dict[str, int] = {}
    query_details = []
    judged_query_count = 0
    abstention_query_count = 0
    search_callable = search_fn or run_search
    collector = None
    if collect_measurements:
        collector = measurement_collector or evaluation_metrics.MeasurementCollector()
        collector.start()

    measurements = None
    try:
        for query_index, query in enumerate(queries, 1):
            query_text = query["query"]
            keywords = query.get("expected_keywords", [])
            expected_type = query.get("expected_type", "")
            judgments = _normalize_judgments(query)
            expected_abstain = query.get("expected_abstain", False)
            filters = dict(query.get("filters") or {})
            query_search_kwargs = {**search_kwargs, **filters}
            started_at = collector.begin_query() if collector else None
            results = search_callable(
                query_text, db_path, **query_search_kwargs)
            query_latency_ms = (
                collector.end_query(started_at) if collector else None)
            detail_metrics = {}
            grounding_detail = None

            if expected_abstain:
                abstention_query_count += 1
                abstained = not results
                abstention_accuracy = 1.0 if abstained else 0.0
                false_answer_rate = 0.0 if abstained else 1.0
                auxiliary_metric_values["abstention_accuracy"].append(
                    abstention_accuracy)
                auxiliary_metric_values["false_answer_rate"].append(
                    false_answer_rate)
                detail_metrics.update({
                    "abstention_accuracy": abstention_accuracy,
                    "false_answer_rate": false_answer_rate,
                })
                relevance = []
                result_details = [
                    _result_detail(
                        result, rank, 0.0, [],
                        include_text=report_detail == "full")
                    for rank, result in enumerate(results, 1)
                ]
                judgment_mode = "expected_abstention"
            elif judgments:
                judged_query_count += 1
                graded = _grade_results(results, judgments)
                relevance = [item["relevance"] for item in graded]
                judged = _judged_metrics(graded, judgments, k_values)
                for key, value in judged.items():
                    aggregate_key = "map" if key == "average_precision" else key
                    judged_metric_values[aggregate_key].append(value)
                result_details = [
                    _result_detail(
                        result, rank, grade["relevance"],
                        grade["matched_judgments"],
                        include_text=report_detail == "full",
                    )
                    for rank, (result, grade) in enumerate(
                        zip(results, graded), 1)
                ]
                detail_metrics.update(judged)
                judgment_mode = "graded_ids"
            else:
                relevance = []
                result_details = []
                for rank, result in enumerate(results, 1):
                    text = str(result.get("text", ""))
                    matches = [keyword for keyword in keywords
                               if keyword.lower() in text.lower()]
                    grade = 1.0 if matches else 0.0
                    relevance.append(grade)
                    result_details.append(_result_detail(
                        result, rank, grade, [], keyword_matches=matches,
                        include_text=report_detail == "full"))
                judgment_mode = "legacy_keywords"

            if not expected_abstain:
                for k in k_values:
                    hit = any(grade > 0 for grade in relevance[:k])
                    value = 1.0 if hit else 0.0
                    metric_values[f"success@{k}"].append(value)
                    detail_metrics[f"success@{k}"] = value

                reciprocal_rank = 0.0
                for rank, grade in enumerate(relevance, 1):
                    if grade > 0:
                        reciprocal_rank = 1.0 / rank
                        break
                metric_values["mrr"].append(reciprocal_rank)
                detail_metrics["mrr"] = reciprocal_rank

                if expected_type:
                    top_type = (
                        results[0].get("metadata", {}).get("content_type", "")
                        if results else "")
                    type_accuracy = 1.0 if top_type == expected_type else 0.0
                    metric_values["type_accuracy"].append(type_accuracy)
                    detail_metrics["type_accuracy"] = type_accuracy

            if filters:
                compliance = 1.0 if all(
                    _filters_match(result, filters) for result in results) else 0.0
                auxiliary_metric_values["filter_compliance"].append(compliance)
                detail_metrics["filter_compliance"] = compliance

            if query.get("grounding_case"):
                grounding_accuracy, grounding_detail = _evaluate_grounding_case(
                    results, query_text, query["grounding_case"])
                if report_detail == "summary":
                    warnings = grounding_detail.pop("warnings", [])
                    grounding_detail["warning_count"] = len(warnings)
                    grounding_detail["warning_sha256"] = [
                        hashlib.sha256(value.encode("utf-8")).hexdigest()
                        for value in warnings
                    ]
                auxiliary_metric_values["grounding_accuracy"].append(
                    grounding_accuracy)
                detail_metrics["grounding_accuracy"] = grounding_accuracy

            for slice_name in _slice_names(
                    query, redact_names=report_detail == "summary"):
                target = slice_values.setdefault(slice_name, {})
                slice_counts[slice_name] = slice_counts.get(slice_name, 0) + 1
                for key, value in detail_metrics.items():
                    slice_metric = (
                        "map" if key == "average_precision" else key)
                    target.setdefault(slice_metric, []).append(float(value))

            if include_details:
                query_id = query.get("query_id", query_index)
                detail = {
                    "query_index": query_index,
                    "query_sha256": hashlib.sha256(
                        query_text.encode("utf-8")).hexdigest(),
                    "tags": list(query.get("tags", [])),
                    "filters": filters,
                    "judgment_mode": judgment_mode,
                    "metrics": {
                        key: round(value, 6)
                        for key, value in detail_metrics.items()
                    },
                    "results": result_details,
                }
                if report_detail == "full":
                    detail["query_id"] = query_id
                    detail["query"] = query_text
                    detail["subject"] = query.get("subject")
                    detail["book"] = query.get("book")
                else:
                    detail["query_id_sha256"] = hashlib.sha256(
                        str(query_id).encode("utf-8")).hexdigest()
                if query_latency_ms is not None:
                    detail["latency_ms"] = round(query_latency_ms, 3)
                if grounding_detail is not None:
                    detail["grounding"] = grounding_detail
                query_details.append(detail)
    finally:
        if collector is not None:
            measurements = collector.finish()

    summary = {}
    for key, values in metric_values.items():
        if values:
            summary[key] = round(sum(values) / len(values), 3)
    if metric_values["type_accuracy"]:
        summary["num_type_queries"] = len(metric_values["type_accuracy"])
    if judged_query_count:
        for key, values in judged_metric_values.items():
            if values:
                summary[key] = round(sum(values) / len(values), 3)
        summary["num_judged_queries"] = judged_query_count
    for key, values in auxiliary_metric_values.items():
        if values:
            summary[key] = round(sum(values) / len(values), 3)
    if auxiliary_metric_values["filter_compliance"]:
        summary["num_filter_queries"] = len(
            auxiliary_metric_values["filter_compliance"])
    if auxiliary_metric_values["grounding_accuracy"]:
        summary["num_grounding_queries"] = len(
            auxiliary_metric_values["grounding_accuracy"])
    if abstention_query_count:
        summary["num_abstention_queries"] = abstention_query_count
    for slice_name, metrics in sorted(slice_values.items()):
        for metric, values in sorted(metrics.items()):
            summary[f"slice/{slice_name}/{metric}"] = round(
                sum(values) / len(values), 3)
            summary[f"slice/{slice_name}/{metric}/num_queries"] = len(values)
        summary[f"slice/{slice_name}/total_queries"] = slice_counts[slice_name]
    summary["num_queries"] = len(queries)
    if include_details:
        summary["query_details"] = query_details
    if collect_measurements:
        summary["measurements"] = measurements
        summary["costs"] = evaluation_metrics.project_costs(
            [query["query"] for query in queries],
            embedding_rate_per_million=embedding_cost_per_million_tokens,
            embedding_requests=embedding_requests,
            llm_usage=llm_usage,
            llm_input_rate_per_million=llm_input_cost_per_million_tokens,
            llm_output_rate_per_million=llm_output_cost_per_million_tokens,
        )
    return summary


def evaluate(queries: list[dict], db_path: Path, *,
             k_values: list[int] | None = None,
             include_details: bool = False,
             report_detail: str = "full",
             collect_measurements: bool = False,
             embedding_cost_per_million_tokens: float | None = None,
             llm_usage: dict | None = None,
             llm_input_cost_per_million_tokens: float | None = None,
             llm_output_cost_per_million_tokens: float | None = None,
             measurement_collector=None,
             **search_kwargs) -> dict:
    """Evaluate every query against one locked physical index generation."""
    import rag

    backend = search_kwargs.get("db_backend", "chroma")
    collection = search_kwargs.get("collection", "civpro")
    lock_timeout = search_kwargs.get(
        "lock_timeout", DEFAULT_DB_LOCK_TIMEOUT)
    with rag._vector_store_lock(
            db_path, backend=backend, collection_name=collection,
            operation="retrieval evaluation", timeout=lock_timeout):
        return _evaluate_impl(
            queries, db_path, k_values=k_values,
            include_details=include_details, report_detail=report_detail,
            collect_measurements=collect_measurements,
            embedding_cost_per_million_tokens=(
                embedding_cost_per_million_tokens),
            llm_usage=llm_usage,
            llm_input_cost_per_million_tokens=(
                llm_input_cost_per_million_tokens),
            llm_output_cost_per_million_tokens=(
                llm_output_cost_per_million_tokens),
            measurement_collector=measurement_collector,
            **search_kwargs)


def evaluate_offline_bm25(queries: list[dict], index, *,
                          k_values: list[int] | None = None,
                          include_details: bool = False,
                          report_detail: str = "summary",
                          collect_measurements: bool = False,
                          **options) -> dict:
    """Evaluate a pinned corpus with the deterministic, model-free adapter."""
    def offline_search(query: str, _db_path: Path, **search_options):
        return index.search(
            query,
            n_results=search_options.get("n_results", 10),
            content_type=search_options.get("content_type"),
            chapter_num=search_options.get("chapter_num"),
        )

    return _evaluate_impl(
        queries, Path("."), k_values=k_values, include_details=include_details,
        report_detail=report_detail, search_fn=offline_search,
        collect_measurements=collect_measurements, embedding_requests=False,
        **options)


def _parse_metric_limits(values: list[str], *, option: str) -> dict[str, float]:
    limits = {}
    for value in values:
        if "=" not in value:
            raise ValueError(f"{option} values must use METRIC=VALUE")
        metric, raw_limit = value.rsplit("=", 1)
        metric = metric.strip()
        try:
            limit = float(raw_limit)
        except ValueError as exc:
            raise ValueError(f"Invalid numeric limit in {value!r}") from exc
        if not metric or not math.isfinite(limit) or limit < 0:
            raise ValueError(f"Invalid {option} value: {value!r}")
        limits[metric] = limit
    return limits


def _load_baseline_metrics(path: Path, *,
                           expected_configuration: dict | None = None,
                           ) -> dict[str, float]:
    payload = json.loads(path.read_text(encoding="utf-8"))
    metrics = payload.get("metrics", payload) if isinstance(payload, dict) else {}
    if not isinstance(metrics, dict):
        raise ValueError(f"Baseline report has no metrics object: {path}")
    baseline_configuration = (
        payload.get("configuration") if isinstance(payload, dict) else None
    )
    if expected_configuration is not None:
        if not isinstance(baseline_configuration, dict):
            raise ValueError(
                "Baseline report lacks configuration provenance")
        baseline_configuration = _portable_configuration(
            baseline_configuration)
        expected_configuration = _portable_configuration(
            expected_configuration)
        retriever = expected_configuration.get("retriever")
        strict = retriever in {"index", "bm25"}
        if strict:
            if payload.get("schema_version") != REPORT_SCHEMA_VERSION:
                raise ValueError(
                    "Baseline report schema version is incompatible")
            if payload.get("mode") != "single":
                raise ValueError("Baseline report mode must be 'single'")
            required_keys = {
                "retriever", "k_values", "retrieval_depth", "queries_sha256",
                "index_snapshot", "use_reranker", "hybrid",
            }
            if retriever == "index":
                required_keys.update({
                    "collection_sha256", "embedding_model", "db_backend",
                    "reranker_model", "overfetch", "rrf_k", "dense_weight",
                    "sparse_weight", "model_artifact_lock_sha256",
                })
            missing = sorted(required_keys - baseline_configuration.keys())
            if missing:
                raise ValueError(
                    "Baseline configuration lacks required provenance: "
                    + ", ".join(missing))
            nullable_keys = {"use_reranker", "hybrid"}
            incomplete_current = sorted(
                key for key in required_keys
                if key not in expected_configuration
                or (key not in nullable_keys
                    and expected_configuration[key] is None))
            if incomplete_current:
                raise ValueError(
                    "Current evaluation lacks required provenance: "
                    + ", ".join(incomplete_current))
        else:
            required_keys = set()
            expected_lock = expected_configuration.get(
                "model_artifact_lock_sha256")
            baseline_lock = baseline_configuration.get(
                "model_artifact_lock_sha256")
            if expected_lock is not None and baseline_lock is None:
                raise ValueError(
                    "Baseline configuration lacks model_artifact_lock_sha256")
        comparable_keys = {
            "retriever", "collection", "embedding_model", "db_backend",
            "k_values", "retrieval_depth", "queries_sha256", "use_reranker",
            "hybrid", "reranker_model", "overfetch", "rrf_k",
            "dense_weight", "sparse_weight", "index_snapshot",
            "model_artifact_lock_sha256", "collection_sha256",
        }
        mismatches = []
        for key in comparable_keys:
            if key not in baseline_configuration and key not in required_keys:
                continue
            baseline_value = baseline_configuration.get(key)
            expected_value = expected_configuration.get(key)
            if key == "index_snapshot":
                baseline_value = _portable_index_snapshot(baseline_value)
                expected_value = _portable_index_snapshot(expected_value)
            if baseline_value != expected_value:
                mismatches.append(key)
        if mismatches:
            raise ValueError(
                "Baseline configuration differs for: "
                + ", ".join(mismatches))
    numeric_metrics = {}
    for key, value in metrics.items():
        if isinstance(value, (int, float)) and not isinstance(value, bool):
            if not math.isfinite(float(value)):
                raise ValueError(
                    f"Baseline metric {key!r} must be finite: {path}")
            numeric_metrics[key] = float(value)
    return numeric_metrics


def _portable_index_snapshot(snapshot):
    """Remove host-specific paths before comparing baseline provenance."""
    if not isinstance(snapshot, dict):
        return snapshot
    return {
        key: value for key, value in snapshot.items()
        if key not in {"manifest_path", "path"}
    }


def _portable_configuration(configuration: dict) -> dict:
    normalized = dict(configuration)
    collection = normalized.pop("collection", None)
    if collection is not None and "collection_sha256" not in normalized:
        normalized["collection_sha256"] = hashlib.sha256(
            str(collection).encode("utf-8")).hexdigest()
    if "index_snapshot" in normalized:
        normalized["index_snapshot"] = _portable_index_snapshot(
            normalized["index_snapshot"])
    return normalized


def _threshold_failures(metrics: dict, minimums: dict[str, float], *,
                        maximums: dict[str, float] | None = None,
                        baseline: dict[str, float] | None = None,
                        regressions: dict[str, float] | None = None) -> list[str]:
    failures = []
    for metric, minimum in minimums.items():
        actual = metrics.get(metric)
        if not _finite_metric(actual) or actual < minimum:
            failures.append(f"{metric}={actual!r} is below {minimum}")
    for metric, maximum in (maximums or {}).items():
        actual = metrics.get(metric)
        if not _finite_metric(actual) or actual > maximum:
            failures.append(f"{metric}={actual!r} is above {maximum}")
    for metric, tolerance in (regressions or {}).items():
        actual = metrics.get(metric)
        prior = (baseline or {}).get(metric)
        if not _finite_metric(actual):
            failures.append(f"{metric} is absent from the current report")
        elif not _finite_metric(prior):
            failures.append(f"{metric} is absent from the baseline report")
        elif prior - actual > tolerance:
            failures.append(
                f"{metric} regressed by {prior - actual:.3f} "
                f"(allowed {tolerance:.3f})")
    return failures


def _finite_metric(value) -> bool:
    return (isinstance(value, (int, float)) and not isinstance(value, bool)
            and math.isfinite(float(value)))


def _write_report(path: Path, payload: dict) -> None:
    import rag

    rag._atomic_write_text(
        path, json.dumps(payload, indent=2, ensure_ascii=False) + "\n")


def _build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        description="Evaluate RAG pipeline retrieval quality")
    parser.add_argument("--queries", type=Path, default=DEFAULT_QUERIES)
    parser.add_argument(
        "--retriever", choices=["index", "bm25"], default="index",
        help=("Production vector index or deterministic offline BM25 "
              "(default: index)"))
    parser.add_argument("--chunks", type=Path, required=True,
                        help="Book-scoped chunks JSONL from a pipeline run")
    parser.add_argument("--db", type=Path,
                        help="Book-scoped ChromaDB or Qdrant directory")
    parser.add_argument("--collection", type=str)
    parser.add_argument("--embedding-model", type=str,
                        default="nomic-ai/nomic-embed-text-v2-moe")
    parser.add_argument("--db-backend", type=str, default="chroma",
                        choices=["chroma", "qdrant"])
    parser.add_argument(
        "--db-lock-timeout", type=float, default=DEFAULT_DB_LOCK_TIMEOUT,
        help="Seconds to wait for exclusive local vector-store access")
    parser.add_argument(
        "--operation-timeout", type=float,
        default=DEFAULT_OPERATION_TIMEOUT,
        help=("Maximum wall-clock seconds for the isolated evaluation worker "
              f"(default: {DEFAULT_OPERATION_TIMEOUT:g})"))
    parser.add_argument("--compare", action="store_true",
                        help="Compare vector, hybrid, and reranked configurations")
    retrieval_mode = parser.add_mutually_exclusive_group()
    retrieval_mode.add_argument(
        "--hybrid", dest="hybrid", action="store_const", const=True,
        default=None, help="Force hybrid retrieval in single-run mode")
    retrieval_mode.add_argument(
        "--vector-only", dest="hybrid", action="store_const", const=False,
        help="Use vector-only retrieval in single-run mode")
    reranker_mode = parser.add_mutually_exclusive_group()
    reranker_mode.add_argument(
        "--rerank", dest="reranker", action="store_const", const=True,
        default=None, help="Force reranking in single-run mode")
    reranker_mode.add_argument(
        "--no-rerank", dest="reranker", action="store_const", const=False,
        help="Disable reranking in single-run mode")
    parser.add_argument(
        "--reranker-model", default=DEFAULT_RERANKER_MODEL,
        help=f"Reranker model (default: {DEFAULT_RERANKER_MODEL})")
    parser.add_argument(
        "--overfetch", type=int, default=DEFAULT_OVERFETCH,
        help=f"Candidate-pool multiplier (default: {DEFAULT_OVERFETCH})")
    parser.add_argument(
        "--rrf-k", type=int, default=DEFAULT_RRF_K,
        help=f"Chroma RRF rank constant (default: {DEFAULT_RRF_K})")
    parser.add_argument(
        "--dense-weight", type=float, default=DEFAULT_DENSE_WEIGHT,
        help=f"Chroma dense-list RRF weight (default: {DEFAULT_DENSE_WEIGHT})")
    parser.add_argument(
        "--sparse-weight", type=float, default=DEFAULT_SPARSE_WEIGHT,
        help=f"Chroma lexical-list RRF weight (default: {DEFAULT_SPARSE_WEIGHT})")
    parser.add_argument(
        "--context-window", type=int, default=0,
        choices=range(retrieval_core.MAX_CONTEXT_WINDOW + 1), metavar="N",
        help="Attach N neighboring chunks for grounding ablations")
    parser.add_argument(
        "--context-max-characters", type=int,
        default=retrieval_core.DEFAULT_CONTEXT_MAX_CHARACTERS,
        help="Total supplementary context character budget")
    parser.add_argument(
        "--context-segment-characters", type=int,
        default=retrieval_core.DEFAULT_CONTEXT_SEGMENT_CHARACTERS,
        help="Maximum characters retained from each neighbor")
    parser.add_argument("--k", type=int, nargs="+", default=[5, 10],
                        help="Cutoffs for Success, Recall, and nDCG")
    parser.add_argument(
        "--depth", type=int, default=DEFAULT_RETRIEVAL_DEPTH,
        help="Fixed retrieval depth used for MRR/MAP (default: 100)")
    parser.add_argument("--json-report", type=Path,
                        help="Write a detailed machine-readable JSON report")
    parser.add_argument(
        "--report-detail", choices=["summary", "full"], default="summary",
        help=("Summary redacts query/source text; full includes previews "
              "(default: summary)"))
    parser.add_argument(
        "--embedding-cost-per-million-tokens", type=float,
        help="Caller-supplied USD rate used to project query embedding cost")
    parser.add_argument(
        "--llm-report", type=Path,
        help="Prompt-free LLMRuntime aggregate report to include in cost metrics")
    parser.add_argument(
        "--llm-input-cost-per-million-tokens", type=float,
        help="Caller-supplied USD input-token rate for --llm-report")
    parser.add_argument(
        "--llm-output-cost-per-million-tokens", type=float,
        help="Caller-supplied USD output-token rate for --llm-report")
    parser.add_argument(
        "--fail-under", action="append", default=[], metavar="METRIC=VALUE",
        help="Exit 2 when a summary metric is below this minimum (repeatable)")
    parser.add_argument(
        "--fail-over", action="append", default=[], metavar="METRIC=VALUE",
        help="Exit 2 when a summary metric exceeds this maximum (repeatable)")
    parser.add_argument("--baseline-report", type=Path,
                        help="Prior JSON report used for regression checks")
    parser.add_argument(
        "--max-regression", action="append", default=[],
        metavar="METRIC=DELTA",
        help="Exit 2 when a metric drops more than DELTA from baseline")
    return parser


def _report_config(args, **overrides) -> dict:
    query_path = args.queries.resolve()
    query_digest = getattr(
        args, "queries_sha256",
        _query_snapshot_sha256.get(str(query_path)))
    configuration = {
        "retriever": args.retriever,
        "collection": args.collection,
        "embedding_model": (
            args.embedding_model if args.retriever == "index" else None),
        "model_artifact_lock_sha256": (
            model_artifacts.model_artifact_lock_sha256()
            if args.retriever == "index" else None),
        "db_backend": args.db_backend if args.retriever == "index" else None,
        "db_lock_timeout": args.db_lock_timeout,
        "operation_timeout": args.operation_timeout,
        "k_values": sorted(set(args.k)),
        "retrieval_depth": args.depth,
        "queries_path": str(query_path),
        "queries_sha256": query_digest,
        "report_detail": args.report_detail,
        "cost_rates": {
            "source": "caller_supplied",
            "embedding_per_million_tokens": (
                args.embedding_cost_per_million_tokens),
            "llm_input_per_million_tokens": (
                args.llm_input_cost_per_million_tokens),
            "llm_output_per_million_tokens": (
                args.llm_output_cost_per_million_tokens),
        },
        "reranker_model": args.reranker_model,
        "overfetch": args.overfetch,
        "rrf_k": args.rrf_k,
        "dense_weight": args.dense_weight,
        "sparse_weight": args.sparse_weight,
        "context_window": args.context_window,
        "context_max_characters": args.context_max_characters,
        "context_segment_characters": args.context_segment_characters,
        **overrides,
    }
    if args.report_detail == "summary":
        configuration.pop("queries_path", None)
        collection = configuration.pop("collection", None)
        if collection is not None:
            configuration["collection_sha256"] = hashlib.sha256(
                collection.encode("utf-8")).hexdigest()
        if "index_snapshot" in configuration:
            configuration["index_snapshot"] = _portable_index_snapshot(
                configuration["index_snapshot"])
    return configuration


def _print_metrics(metrics: dict) -> None:
    print(f"\n{'=' * 50}")
    print(" Evaluation Results")
    print(f"{'=' * 50}")
    for key, value in metrics.items():
        if key != "query_details" and not key.startswith("slice/"):
            print(f"  {key:20s} {value}")
    print()


def _main_with_args(args, parser: argparse.ArgumentParser) -> int:
    if args.compare and (args.fail_under or args.baseline_report
                         or args.max_regression or args.fail_over):
        parser.error("threshold checks are supported only without --compare")
    if args.retriever == "bm25" and args.compare:
        parser.error("--compare is supported only with --retriever index")
    if args.retriever == "index" and (args.db is None or not args.collection):
        parser.error("--db and --collection are required with --retriever index")
    if args.retriever == "bm25" and (
            args.hybrid is not None or args.reranker is not None
            or args.context_window):
        parser.error(
            "hybrid, reranker, and context flags do not apply to offline BM25")
    if args.max_regression and not args.baseline_report:
        parser.error("--max-regression requires --baseline-report")
    if args.baseline_report and not args.max_regression:
        parser.error("--baseline-report requires at least one --max-regression")
    if args.depth < max(args.k):
        parser.error("--depth must be at least the largest --k cutoff")
    if not 1 <= args.overfetch <= 20:
        parser.error("--overfetch must be from 1 to 20")
    if args.rrf_k < 1:
        parser.error("--rrf-k must be a positive integer")
    if not 0 <= args.context_window <= retrieval_core.MAX_CONTEXT_WINDOW:
        parser.error("--context-window is outside the supported range")
    if not 1 <= args.context_max_characters <= (
            retrieval_core.MAX_CONTEXT_CHARACTERS):
        parser.error("--context-max-characters is outside the supported range")
    if not 1 <= args.context_segment_characters <= (
            retrieval_core.MAX_CONTEXT_SEGMENT_CHARACTERS):
        parser.error(
            "--context-segment-characters is outside the supported range")
    if (not math.isfinite(args.dense_weight)
            or not math.isfinite(args.sparse_weight)
            or args.dense_weight < 0 or args.sparse_weight < 0
            or args.dense_weight + args.sparse_weight == 0):
        parser.error("fusion weights must be finite, non-negative, and not both zero")
    if (args.json_report and args.baseline_report
            and args.json_report.resolve() == args.baseline_report.resolve()):
        parser.error("--json-report and --baseline-report must be different files")
    supplied_llm_rates = (
        args.llm_input_cost_per_million_tokens is not None,
        args.llm_output_cost_per_million_tokens is not None,
    )
    if supplied_llm_rates[0] != supplied_llm_rates[1]:
        parser.error("both LLM input and output rates must be supplied together")
    if any(supplied_llm_rates) and args.llm_report is None:
        parser.error("LLM cost rates require --llm-report")
    try:
        evaluation_metrics.project_costs(
            [],
            embedding_rate_per_million=(
                args.embedding_cost_per_million_tokens),
            llm_input_rate_per_million=(
                args.llm_input_cost_per_million_tokens),
            llm_output_rate_per_million=(
                args.llm_output_cost_per_million_tokens),
        )
    except ValueError as exc:
        parser.error(str(exc))

    try:
        minimums = _parse_metric_limits(args.fail_under, option="--fail-under")
        maximums = _parse_metric_limits(args.fail_over, option="--fail-over")
        regressions = _parse_metric_limits(
            args.max_regression, option="--max-regression")
    except ValueError as exc:
        parser.error(str(exc))

    offline_index = None
    chunk_identity_lookup = None
    llm_usage = None
    try:
        query_cache_key = str(args.queries.resolve())
        _query_snapshot_sha256.pop(query_cache_key, None)
        queries = load_queries(args.queries)
        args.queries_sha256 = _query_snapshot_sha256.get(query_cache_key)
        if minimums or maximums or regressions:
            _validate_release_gate_review_status(queries)
        _validate_corpus_pin_coverage(
            queries,
            required=(args.retriever == "bm25"
                      or args.baseline_report is not None),
        )
        if args.retriever == "bm25":
            from offline_retrieval import OfflineBM25Index
            from retrieval_core import _chunk_id

            offline_index = OfflineBM25Index.from_jsonl(args.chunks)
            _validate_declared_corpus_snapshot(
                queries,
                actual_hash=offline_index.snapshot.source_sha256,
                actual_count=offline_index.snapshot.source_record_count)
            _validate_judged_ids(queries, list(offline_index.records), _chunk_id)
            index_snapshot = offline_index.snapshot.as_report_dict()
        else:
            index_snapshot = _validate_declared_index(
                queries, args.chunks, args.db,
                db_backend=args.db_backend, collection=args.collection,
                embedding_model=args.embedding_model,
                lock_timeout=args.db_lock_timeout,
                include_runtime_context=True)
            if index_snapshot is not None:
                chunk_identity_lookup = index_snapshot.pop("_identity_lookup")
                _validate_declared_corpus_snapshot(
                    queries,
                    actual_hash=index_snapshot["source_sha256"],
                    actual_count=index_snapshot["record_count"])
        if args.llm_report:
            llm_usage = evaluation_metrics.parse_llm_usage_report(
                args.llm_report)
    except (OSError, UnicodeError, json.JSONDecodeError, LookupError,
            ValueError) as exc:
        log.error("Evaluation integrity check failed: %s", exc)
        return 1
    log.info(f"Loaded {len(queries)} evaluation queries from {args.queries}")

    common = {
        "n_results": args.depth,
        "k_values": args.k,
        "include_details": True,
        "report_detail": args.report_detail,
        "collect_measurements": True,
        "embedding_cost_per_million_tokens": (
            args.embedding_cost_per_million_tokens),
        "llm_usage": llm_usage,
        "llm_input_cost_per_million_tokens": (
            args.llm_input_cost_per_million_tokens),
        "llm_output_cost_per_million_tokens": (
            args.llm_output_cost_per_million_tokens),
    }
    if args.retriever == "index":
        common.update({
            "collection": args.collection,
            "embedding_model": args.embedding_model,
            "chunks_path": args.chunks,
            "chunk_identity_lookup": chunk_identity_lookup,
            "db_backend": args.db_backend,
            "lock_timeout": args.db_lock_timeout,
            "reranker_model": args.reranker_model,
            "overfetch": args.overfetch,
            "rrf_k": args.rrf_k,
            "dense_weight": args.dense_weight,
            "sparse_weight": args.sparse_weight,
            "context_window": args.context_window,
            "context_max_characters": args.context_max_characters,
            "context_segment_characters": args.context_segment_characters,
        })
    storage_target = args.db if args.retriever == "index" else args.chunks
    storage = evaluation_metrics.measure_path(storage_target)
    storage["kind"] = (
        "vector_index" if args.retriever == "index"
        else "ephemeral_bm25_source_corpus")
    if args.report_detail == "summary":
        storage.pop("path", None)

    if args.compare:
        configs = [
            {"label": "Vector only", "use_reranker": False, "hybrid": False},
            {"label": "Vector + reranker", "use_reranker": True, "hybrid": False},
            {"label": "Hybrid (BM25+vector)", "use_reranker": False, "hybrid": True},
            {"label": "Hybrid + reranker", "use_reranker": True, "hybrid": True},
        ]
        reports = []
        had_errors = False
        success_metrics = [f"success@{k}" for k in sorted(set(args.k))]
        display_metrics = success_metrics + ["mrr", "type_accuracy"]
        display_labels = [
            *[metric.title() for metric in success_metrics],
            "MRR", "Type Acc",
        ]
        print(f"\n{'Config':<30s}" + "".join(
            f" {label:>12s}" for label in display_labels))
        print("-" * (30 + 13 * len(display_labels)))
        for config in configs:
            label = config["label"]
            run_kwargs = {key: value for key, value in config.items()
                          if key != "label"}
            try:
                result = evaluate(queries, args.db, **common, **run_kwargs)
                details = result.pop("query_details")
                measurements = result.pop("measurements", {})
                measurements["index_storage"] = storage
                costs = result.pop("costs", {})
                reports.append({
                    "label": label,
                    "configuration": _report_config(
                        args, index_snapshot=index_snapshot, **run_kwargs),
                    "metrics": result,
                    "measurements": measurements,
                    "costs": costs,
                    "query_details": details,
                })
                print(f"{label:<30s}" + "".join(
                    f" {result.get(metric, 0):>12.3f}"
                    for metric in display_metrics))
            except Exception as exc:
                had_errors = True
                error = {
                    "type": type(exc).__name__,
                    "message_sha256": hashlib.sha256(
                        str(exc).encode("utf-8")).hexdigest(),
                }
                if args.report_detail == "full":
                    error["message"] = str(exc)
                reports.append({"label": label, "error": error})
                display_error = (
                    str(exc)[:40] if args.report_detail == "full"
                    else type(exc).__name__)
                print(f"{label:<30s} {'ERROR':>12s} {display_error}")
        report = {
            "schema_version": REPORT_SCHEMA_VERSION,
            "mode": "compare",
            "measurement_scope": (
                "configurations ran sequentially in one warm process; latency "
                "and memory are not cross-configuration benchmarks, and any "
                "external LLM usage input is repeated unchanged"),
            "configurations": reports,
        }
        if args.json_report:
            _write_report(args.json_report, report)
        return 1 if had_errors else 0

    try:
        single_modes = {
            "use_reranker": args.reranker,
            "hybrid": args.hybrid,
        }
        if args.retriever == "bm25":
            result = evaluate_offline_bm25(
                queries, offline_index, **common)
            single_modes = {
                "use_reranker": False,
                "hybrid": False,
            }
        else:
            result = evaluate(queries, args.db, **common, **single_modes)
    except (FileNotFoundError, LookupError, ValueError) as exc:
        log.error("Evaluation retrieval failed: %s", exc)
        return 1
    details = result.pop("query_details")
    measurements = result.pop("measurements", {})
    measurements["index_storage"] = storage
    costs = result.pop("costs", {})
    _print_metrics(result)
    configuration = _report_config(
        args, index_snapshot=index_snapshot,
        llm_report_sha256=(
            llm_usage.get("report_sha256") if llm_usage else None),
        **single_modes)
    try:
        baseline = _load_baseline_metrics(
            args.baseline_report,
            expected_configuration=configuration,
        ) if args.baseline_report else None
    except (OSError, json.JSONDecodeError, ValueError) as exc:
        log.error("Could not use baseline report: %s", exc)
        return 1
    report = {
        "schema_version": REPORT_SCHEMA_VERSION,
        "mode": "single",
        "configuration": configuration,
        "metrics": result,
        "measurements": measurements,
        "costs": costs,
        "query_details": details,
    }
    if args.json_report:
        _write_report(args.json_report, report)

    failures = _threshold_failures(
        result, minimums, maximums=maximums,
        baseline=baseline, regressions=regressions)
    for failure in failures:
        log.error("Evaluation threshold failed: %s", failure)
    return 2 if failures else 0


def main(argv: list[str] | None = None) -> int:
    """Run one generation-consistent evaluation under a database lease."""
    parser = _build_parser()
    args = parser.parse_args(argv)
    import rag

    try:
        args.db_lock_timeout = rag._normalize_db_lock_timeout(
            args.db_lock_timeout)
        args.operation_timeout = rag._normalize_operation_timeout(
            args.operation_timeout)
    except ValueError as exc:
        parser.error(str(exc))

    if args.retriever == "bm25":
        return _main_with_args(args, parser)
    if args.db is None or not args.collection:
        return _main_with_args(args, parser)
    try:
        with rag._vector_store_lock(
                args.db, backend=args.db_backend,
                collection_name=args.collection,
                operation="evaluation run", timeout=args.db_lock_timeout):
            return _main_with_args(args, parser)
    except rag.VectorStoreBusyError as exc:
        log.error("Evaluation could not acquire the index: %s", exc)
        return 1


def _run_eval_entrypoint(argv: list[str] | None = None) -> int:
    """Supervise the evaluation process so a stuck client can be terminated."""
    import rag

    cli_args = list(sys.argv[1:] if argv is None else argv)
    if os.environ.get(rag._SUPERVISED_CHILD_ENV) == "1":
        return main(cli_args)
    return rag._run_cli_with_deadline(
        Path(__file__), cli_args, operation="evaluation",
        timeout=rag._cli_operation_timeout(cli_args, "evaluation"))


if __name__ == "__main__":
    sys.exit(_run_eval_entrypoint())
