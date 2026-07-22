"""Stable, dependency-free contracts for the local service API.

The HTTP adapter and application runtime deliberately share this leaf module.
It accepts only bounded, exactly-shaped values and exposes only redacted public
records.  Filesystem paths, provider identities, raw warnings, exceptions, job
arguments, and private attempt data are never response fields.
"""

from __future__ import annotations

import base64
import hashlib
import json
import math
import re
import threading
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, Mapping


SERVICE_API_VERSION = "v1"
SERVICE_SCHEMA_VERSION = 1
MAX_REQUEST_BYTES = 64 * 1024
MAX_QUERY_CHARACTERS = 4096
MAX_QUERY_BYTES = 16 * 1024
MAX_SEARCH_LIMIT = 50
MAX_HIT_TEXT_CHARACTERS = 32 * 1024
MAX_PUBLIC_SEARCH_RESPONSE_BYTES = 1_900_000
MAX_CURSOR_BYTES = 256
MAX_IDEMPOTENCY_KEY_BYTES = 128
MAX_REQUEST_ID_BYTES = 128
MAX_COUNTER = (1 << 63) - 1
MAX_PLATFORM_TIMEOUT_SECONDS = float(threading.TIMEOUT_MAX)
MAX_REINDEX_TIMEOUT_SECONDS = float(31 * 24 * 60 * 60)
MAX_READY_TIMEOUT_SECONDS = 10 * 60.0

SEARCH_MODES = frozenset({"auto", "vector", "hybrid"})
SERVICE_BACKENDS = frozenset({"qdrant"})
JOB_STATUSES = frozenset({
    "queued",
    "starting",
    "running",
    "cancel_requested",
    "succeeded",
    "partial",
    "failed",
    "cancelled",
    "interrupted",
    "orphaned",
    "deleting",
})
TERMINAL_JOB_STATUSES = frozenset({
    "succeeded", "partial", "failed", "cancelled", "interrupted",
    "orphaned", "deleting",
})
CONTENT_TYPES = frozenset({
    "case_opinion",
    "notes_and_questions",
    "author_narrative",
    "statutory_excerpt",
    "chapter_introduction",
    "table",
    "footnote",
    "structural",
    "empty",
})

_CORPUS_ID = re.compile(r"^[a-z][a-z0-9_-]{0,63}$")
_COLLECTION_NAME = re.compile(r"^[A-Za-z0-9][A-Za-z0-9_.-]{0,127}$")
_JOB_ID = re.compile(r"^[0-9a-f]{32}$")
_REQUEST_ID = re.compile(r"^[A-Za-z0-9][A-Za-z0-9._-]{0,127}$")
_OPAQUE_SOURCE_ID = re.compile(r"^[A-Za-z0-9][A-Za-z0-9._:-]{0,127}$")
_HEADER_TOKEN = re.compile(r"^[A-Za-z0-9][A-Za-z0-9._~:+/=-]*$")
_ETAG = re.compile(r'^"rag-job-a([1-9][0-9]*)-r(0|[1-9][0-9]*)"$')

PUBLIC_METADATA_FIELDS = frozenset({
    "content_type",
    "chapter_num",
    "chapter_title",
    "section_path",
    "primary_case",
    "case_names",
    "page_start",
    "page_end",
    "page_range",
    "headings",
})
_PUBLIC_WARNING_PATTERNS = (
    ("manifested chunks sha-256", "hybrid_manifest_unavailable"),
    ("chunks file", "hybrid_chunks_unavailable"),
    ("bm25 search failed", "hybrid_lexical_failed"),
    ("bm25 search returned no candidates", "hybrid_lexical_empty"),
    ("hybrid search failed", "hybrid_failed"),
    ("rerank", "reranker_unavailable"),
)
PUBLIC_WARNING_CODES = frozenset(
    {code for _fragment, code in _PUBLIC_WARNING_PATTERNS}
    | {"retrieval_degraded"}
)


class ServiceContractError(ValueError):
    """A safe, stable contract failure suitable for HTTP mapping."""

    def __init__(self, code: str = "invalid_request"):
        if code not in PROBLEM_DEFINITIONS:
            code = "invalid_request"
        self.code = code
        super().__init__(PROBLEM_DEFINITIONS[code][1])


PROBLEM_DEFINITIONS: dict[str, tuple[int, str, bool]] = {
    "invalid_request": (400, "The request is invalid.", False),
    "invalid_json": (400, "The JSON body is invalid.", False),
    "unauthorized": (401, "Authentication is required.", False),
    "forbidden": (403, "This credential lacks permission.", False),
    "not_found": (404, "The requested resource was not found.", False),
    "method_not_allowed": (405, "The request method is not allowed.", False),
    "conflict": (409, "The request conflicts with current state.", False),
    "precondition_failed": (412, "The resource precondition is stale.", False),
    "payload_too_large": (413, "The request body is too large.", False),
    "unsupported_media_type": (415, "JSON content is required.", False),
    "precondition_required": (428, "A resource precondition is required.", False),
    "concurrency_limited": (429, "Service concurrency is exhausted.", True),
    "service_unavailable": (503, "The service is temporarily unavailable.", True),
    "deadline_exceeded": (504, "The operation deadline was exceeded.", True),
    "internal_error": (500, "The service could not complete the request.", False),
}


@dataclass(frozen=True, slots=True)
class ServiceProblem:
    """Fixed ``application/problem+json`` response without raw details."""

    code: str
    request_id: str

    def __post_init__(self) -> None:
        if self.code not in PROBLEM_DEFINITIONS:
            raise ValueError("unknown service problem code")
        validate_request_id(self.request_id)

    @property
    def status(self) -> int:
        return PROBLEM_DEFINITIONS[self.code][0]

    def as_dict(self) -> dict[str, Any]:
        status, title, retryable = PROBLEM_DEFINITIONS[self.code]
        return {
            "type": f"urn:rag-pipeline:problem:{self.code}",
            "title": title,
            "status": status,
            "code": self.code,
            "request_id": self.request_id,
            "retryable": retryable,
        }


@dataclass(frozen=True, slots=True)
class SearchFilters:
    content_type: str | None = None
    chapter_num: int | None = None

    def as_dict(self) -> dict[str, str | int]:
        result: dict[str, str | int] = {}
        if self.content_type is not None:
            result["content_type"] = self.content_type
        if self.chapter_num is not None:
            result["chapter_num"] = self.chapter_num
        return result


@dataclass(frozen=True, slots=True)
class SearchRequest:
    query: str = field(repr=False)
    limit: int = 5
    mode: str = "auto"
    filters: SearchFilters = field(default_factory=SearchFilters)


@dataclass(frozen=True, slots=True)
class ReindexRequest:
    full_reindex: bool = False


@dataclass(frozen=True, slots=True)
class CorpusConfig:
    """Private immutable server-side binding for one public corpus ID."""

    corpus_id: str
    db_path: Path = field(repr=False)
    chunks_path: Path = field(repr=False)
    collection_name: str = field(repr=False)
    embedding_model: str = field(repr=False)
    backend: str = "qdrant"
    search_timeout_seconds: float = 300.0
    db_lock_timeout_seconds: float = 30.0
    reindex_timeout_seconds: float = 7200.0

    def __post_init__(self) -> None:
        validate_corpus_id(self.corpus_id)
        if self.backend not in SERVICE_BACKENDS:
            raise ServiceContractError()
        if (not isinstance(self.collection_name, str)
                or not _COLLECTION_NAME.fullmatch(self.collection_name)):
            raise ServiceContractError()
        if (not isinstance(self.embedding_model, str)
                or not self.embedding_model.strip()
                or _utf8_length(self.embedding_model) > 512):
            raise ServiceContractError()
        for name in ("db_path", "chunks_path"):
            value = getattr(self, name)
            if not isinstance(value, Path) or not value.is_absolute():
                raise ServiceContractError()
        for name, maximum in (
                ("search_timeout_seconds", MAX_PLATFORM_TIMEOUT_SECONDS),
                ("db_lock_timeout_seconds", MAX_PLATFORM_TIMEOUT_SECONDS),
                ("reindex_timeout_seconds", MAX_REINDEX_TIMEOUT_SECONDS)):
            if positive_finite(getattr(self, name)) > maximum:
                raise ServiceContractError()

    def public_dict(self) -> dict[str, Any]:
        return {
            "schema_version": SERVICE_SCHEMA_VERSION,
            "corpus_id": self.corpus_id,
            "backend": self.backend,
            "capabilities": {
                "search": True,
                "reindex": True,
                "rerank": False,
                "llm_answer": False,
            },
        }


def positive_finite(value: object) -> float:
    if (isinstance(value, bool) or not isinstance(value, (int, float))
            or not math.isfinite(float(value)) or float(value) <= 0):
        raise ServiceContractError()
    return float(value)


def _utf8_length(value: str, *, code: str = "invalid_request") -> int:
    try:
        return len(value.encode("utf-8"))
    except UnicodeEncodeError as exc:
        raise ServiceContractError(code) from exc


def _exact_fields(
        payload: Mapping[str, Any], *, allowed: frozenset[str],
        required: frozenset[str] = frozenset()) -> None:
    if not isinstance(payload, dict):
        raise ServiceContractError()
    fields = frozenset(payload)
    if not required.issubset(fields) or not fields.issubset(allowed):
        raise ServiceContractError()


def _unique_object(pairs: list[tuple[str, Any]]) -> dict[str, Any]:
    result: dict[str, Any] = {}
    for key, value in pairs:
        if key in result:
            raise ServiceContractError("invalid_json")
        result[key] = value
    return result


def _reject_json_constant(_value: str) -> None:
    raise ServiceContractError("invalid_json")


def _parse_json_integer(value: str) -> int:
    if len(value.removeprefix("-")) > len(str(MAX_COUNTER)):
        raise ServiceContractError("invalid_json")
    parsed = int(value)
    if abs(parsed) > MAX_COUNTER:
        raise ServiceContractError("invalid_json")
    return parsed


def _parse_json_float(value: str) -> float:
    parsed = float(value)
    if not math.isfinite(parsed):
        raise ServiceContractError("invalid_json")
    return parsed


def decode_json_object(
        raw: bytes, *, max_bytes: int = MAX_REQUEST_BYTES) -> dict[str, Any]:
    """Decode one strict UTF-8 JSON object with duplicate/NaN rejection."""
    if not isinstance(raw, bytes):
        raise TypeError("raw JSON must be bytes")
    if (isinstance(max_bytes, bool) or not isinstance(max_bytes, int)
            or max_bytes < 1):
        raise ValueError("max_bytes must be a positive integer")
    if len(raw) > max_bytes:
        raise ServiceContractError("payload_too_large")
    try:
        value = json.loads(
            raw.decode("utf-8"),
            object_pairs_hook=_unique_object,
            parse_constant=_reject_json_constant,
            parse_int=_parse_json_integer,
            parse_float=_parse_json_float,
        )
    except ServiceContractError:
        raise
    except (UnicodeDecodeError, ValueError, RecursionError) as exc:
        raise ServiceContractError("invalid_json") from exc
    if not isinstance(value, dict):
        raise ServiceContractError("invalid_json")
    return value


def validate_corpus_id(value: object) -> str:
    if not isinstance(value, str) or not _CORPUS_ID.fullmatch(value):
        raise ServiceContractError()
    return value


def validate_job_id(value: object) -> str:
    if not isinstance(value, str) or not _JOB_ID.fullmatch(value):
        raise ServiceContractError()
    return value


def validate_request_id(value: object) -> str:
    if not isinstance(value, str) or not _REQUEST_ID.fullmatch(value):
        raise ServiceContractError()
    return value


def validate_idempotency_key(value: object) -> str:
    if (not isinstance(value, str) or not _HEADER_TOKEN.fullmatch(value)
            or len(value.encode("ascii", errors="ignore")) < 8
            or len(value.encode("ascii", errors="ignore")) >
            MAX_IDEMPOTENCY_KEY_BYTES):
        raise ServiceContractError()
    try:
        value.encode("ascii")
    except UnicodeEncodeError as exc:
        raise ServiceContractError() from exc
    return value


def validate_bearer_token(value: object) -> str:
    if (not isinstance(value, str) or not _HEADER_TOKEN.fullmatch(value)
            or not 32 <= len(value) <= 512):
        raise ServiceContractError()
    try:
        value.encode("ascii")
    except UnicodeEncodeError as exc:
        raise ServiceContractError() from exc
    return value


def parse_search_request(payload: Mapping[str, Any]) -> SearchRequest:
    _exact_fields(
        payload, allowed=frozenset({"query", "limit", "mode", "filters"}),
        required=frozenset({"query"}))
    query = payload["query"]
    if (not isinstance(query, str) or not query.strip() or "\x00" in query
            or len(query) > MAX_QUERY_CHARACTERS
            or _utf8_length(query) > MAX_QUERY_BYTES):
        raise ServiceContractError()
    limit = payload.get("limit", 5)
    if (isinstance(limit, bool) or not isinstance(limit, int)
            or not 1 <= limit <= MAX_SEARCH_LIMIT):
        raise ServiceContractError()
    mode = payload.get("mode", "auto")
    if not isinstance(mode, str) or mode not in SEARCH_MODES:
        raise ServiceContractError()
    filters_payload = payload.get("filters", {})
    _exact_fields(
        filters_payload,
        allowed=frozenset({"content_type", "chapter_num"}))
    content_type = filters_payload.get("content_type")
    if content_type is not None and content_type not in CONTENT_TYPES:
        raise ServiceContractError()
    chapter_num = filters_payload.get("chapter_num")
    if (chapter_num is not None
            and (isinstance(chapter_num, bool)
                 or not isinstance(chapter_num, int)
                 or not 0 <= chapter_num <= 1_000_000)):
        raise ServiceContractError()
    return SearchRequest(
        query=query,
        limit=limit,
        mode=mode,
        filters=SearchFilters(
            content_type=content_type,
            chapter_num=chapter_num,
        ),
    )


def parse_reindex_request(payload: Mapping[str, Any]) -> ReindexRequest:
    _exact_fields(payload, allowed=frozenset({"full_reindex"}))
    full_reindex = payload.get("full_reindex", False)
    if not isinstance(full_reindex, bool):
        raise ServiceContractError()
    return ReindexRequest(full_reindex=full_reindex)


def job_etag(attempt_number: object, revision: object) -> str:
    if (isinstance(attempt_number, bool)
            or not isinstance(attempt_number, int)
            or not 1 <= attempt_number <= MAX_COUNTER
            or isinstance(revision, bool) or not isinstance(revision, int)
            or not 0 <= revision <= MAX_COUNTER):
        raise ServiceContractError()
    return f'"rag-job-a{attempt_number}-r{revision}"'


def parse_job_etag(value: object) -> tuple[int, int]:
    if not isinstance(value, str) or len(value) > 64:
        raise ServiceContractError("precondition_failed")
    matched = _ETAG.fullmatch(value)
    if matched is None:
        raise ServiceContractError("precondition_failed")
    attempt_number, revision = (int(part) for part in matched.groups())
    if attempt_number > MAX_COUNTER or revision > MAX_COUNTER:
        raise ServiceContractError("precondition_failed")
    return attempt_number, revision


def encode_job_cursor(created_at: object, job_id: object) -> str:
    created = finite_timestamp(created_at)
    identifier = validate_job_id(job_id)
    payload = json.dumps(
        {
            "schema_version": SERVICE_SCHEMA_VERSION,
            "created_at": created.hex(),
            "job_id": identifier,
        },
        sort_keys=True,
        separators=(",", ":"),
    ).encode("ascii")
    return base64.urlsafe_b64encode(payload).rstrip(b"=").decode("ascii")


def decode_job_cursor(value: object) -> tuple[float, str]:
    if (not isinstance(value, str) or not value
            or len(value.encode("ascii", errors="ignore")) > MAX_CURSOR_BYTES
            or not re.fullmatch(r"[A-Za-z0-9_-]+", value)):
        raise ServiceContractError()
    try:
        value.encode("ascii")
        padding = "=" * (-len(value) % 4)
        raw = base64.b64decode(
            value + padding, altchars=b"-_", validate=True)
        decoded = decode_json_object(raw, max_bytes=MAX_CURSOR_BYTES)
    except (UnicodeEncodeError, ValueError, ServiceContractError) as exc:
        raise ServiceContractError() from exc
    if base64.urlsafe_b64encode(raw).rstrip(b"=").decode("ascii") != value:
        raise ServiceContractError()
    _exact_fields(
        decoded,
        allowed=frozenset({"schema_version", "created_at", "job_id"}),
        required=frozenset({"schema_version", "created_at", "job_id"}))
    if decoded["schema_version"] != SERVICE_SCHEMA_VERSION:
        raise ServiceContractError()
    encoded_time = decoded["created_at"]
    if not isinstance(encoded_time, str) or len(encoded_time) > 32:
        raise ServiceContractError()
    try:
        created_at = float.fromhex(encoded_time)
    except ValueError as exc:
        raise ServiceContractError() from exc
    return finite_timestamp(created_at), validate_job_id(decoded["job_id"])


def job_id_for_idempotency(
        corpus_id: object, idempotency_key: object, *,
        operation: str = "reindex") -> str:
    corpus = validate_corpus_id(corpus_id)
    key = validate_idempotency_key(idempotency_key)
    if operation != "reindex":
        raise ServiceContractError()
    material = (
        f"rag-service/{SERVICE_API_VERSION}/{operation}/{corpus}\0{key}"
    ).encode("ascii")
    return hashlib.sha256(material).hexdigest()[:32]


def finite_timestamp(value: object) -> float:
    if (isinstance(value, bool) or not isinstance(value, (int, float))
            or not math.isfinite(float(value)) or float(value) < 0):
        raise ServiceContractError()
    return float(value)


def public_job(summary: object) -> dict[str, Any]:
    """Serialize only the explicitly redacted ``JobSummary`` surface."""
    job_id = validate_job_id(getattr(summary, "job_id", None))
    command = getattr(summary, "command", None)
    status = getattr(summary, "status", None)
    attempt_number = getattr(summary, "attempt_number", None)
    revision = getattr(summary, "revision", None)
    if (not isinstance(command, str) or not command
            or len(command.encode("utf-8")) > 64
            or status not in JOB_STATUSES):
        raise ServiceContractError("service_unavailable")
    etag = job_etag(attempt_number, revision)
    return {
        "schema_version": SERVICE_SCHEMA_VERSION,
        "job_id": job_id,
        "command": command,
        "status": status,
        "attempt_number": attempt_number,
        "revision": revision,
        "created_at": finite_timestamp(getattr(summary, "created_at", None)),
        "updated_at": finite_timestamp(getattr(summary, "updated_at", None)),
        "terminal": status in TERMINAL_JOB_STATUSES,
        "etag": etag,
    }


def _public_metadata_value(value: object, *, depth: int = 0) -> Any:
    if depth > 2:
        raise ServiceContractError("service_unavailable")
    if value is None or isinstance(value, bool):
        return value
    if isinstance(value, int):
        if abs(value) > MAX_COUNTER:
            raise ServiceContractError("service_unavailable")
        return value
    if isinstance(value, float):
        if not math.isfinite(value):
            raise ServiceContractError("service_unavailable")
        return value
    if isinstance(value, str):
        return value[:512]
    if isinstance(value, (list, tuple)):
        if depth >= 1:
            raise ServiceContractError("service_unavailable")
        return [
            _public_metadata_value(item, depth=depth + 1)
            for item in list(value)[:16]
        ]
    raise ServiceContractError("service_unavailable")


def public_metadata(metadata: object) -> dict[str, Any]:
    if not isinstance(metadata, dict):
        raise ServiceContractError("service_unavailable")
    result = {
        key: _public_metadata_value(metadata[key])
        for key in sorted(PUBLIC_METADATA_FIELDS & metadata.keys())
    }
    try:
        encoded = json.dumps(
            result, ensure_ascii=False, allow_nan=False,
            sort_keys=True, separators=(",", ":")).encode("utf-8")
    except (TypeError, ValueError) as exc:
        raise ServiceContractError("service_unavailable") from exc
    if len(encoded) > 16 * 1024:
        raise ServiceContractError("service_unavailable")
    return result


def public_warning_codes(warnings: object) -> list[str]:
    if not isinstance(warnings, list):
        raise ServiceContractError("service_unavailable")
    result: list[str] = []
    for warning in warnings[:32]:
        if not isinstance(warning, str):
            raise ServiceContractError("service_unavailable")
        normalized = warning.casefold()
        code = next((
            candidate for fragment, candidate in _PUBLIC_WARNING_PATTERNS
            if fragment in normalized
        ), "retrieval_degraded")
        if code not in result:
            result.append(code)
    return result


def _encoded_json_bytes(value: object) -> bytes:
    try:
        return json.dumps(
            value, ensure_ascii=False, allow_nan=False,
            sort_keys=True, separators=(",", ":")).encode("utf-8")
    except (TypeError, ValueError, UnicodeEncodeError) as exc:
        raise ServiceContractError("service_unavailable") from exc


def _fit_search_text_budget(
        payload: dict[str, Any], desired_texts: list[tuple[str, bool]],
        ) -> dict[str, Any]:
    """Allocate the exact global response budget in retrieval-rank order."""
    for hit in payload["hits"]:
        hit["text"] = ""
        hit["text_truncated"] = True
    current_size = len(_encoded_json_bytes(payload))
    if current_size > MAX_PUBLIC_SEARCH_RESPONSE_BYTES:
        raise ServiceContractError("service_unavailable")

    for hit, (desired, already_truncated) in zip(
            payload["hits"], desired_texts, strict=True):
        available = MAX_PUBLIC_SEARCH_RESPONSE_BYTES - current_size
        full_delta = len(_encoded_json_bytes(desired)) - 2
        complete_flag_delta = 0 if already_truncated else 1
        if full_delta + complete_flag_delta <= available:
            hit["text"] = desired
            hit["text_truncated"] = already_truncated
            current_size += full_delta + complete_flag_delta
            continue
        low = 0
        high = len(desired)
        while low < high:
            middle = (low + high + 1) // 2
            delta = len(_encoded_json_bytes(desired[:middle])) - 2
            if delta <= available:
                low = middle
            else:
                high = middle - 1
        prefix = desired[:low]
        hit["text"] = prefix
        current_size += len(_encoded_json_bytes(prefix)) - 2

    if len(_encoded_json_bytes(payload)) > MAX_PUBLIC_SEARCH_RESPONSE_BYTES:
        raise ServiceContractError("service_unavailable")
    return payload


def public_search_response(
        response: object, *, corpus_id: object, request_id: object) -> dict[str, Any]:
    """Serialize a retrieval response with bounded text and metadata."""
    corpus = validate_corpus_id(corpus_id)
    request = validate_request_id(request_id)
    requested_mode = getattr(response, "requested_mode", None)
    effective_mode = getattr(response, "effective_mode", None)
    backend = getattr(response, "backend", None)
    reranker_applied = getattr(response, "reranker_applied", None)
    if (requested_mode not in SEARCH_MODES
            or effective_mode not in {"vector", "hybrid"}
            or backend not in SERVICE_BACKENDS
            or reranker_applied is not False):
        raise ServiceContractError("service_unavailable")
    raw_hits = getattr(response, "hits", None)
    if not isinstance(raw_hits, list) or len(raw_hits) > MAX_SEARCH_LIMIT:
        raise ServiceContractError("service_unavailable")
    hits = []
    desired_texts: list[tuple[str, bool]] = []
    for hit in raw_hits:
        source_id = getattr(hit, "source_id", None)
        score = getattr(hit, "score", None)
        text = getattr(hit, "text", None)
        if (not isinstance(source_id, str)
                or not _OPAQUE_SOURCE_ID.fullmatch(source_id)
                or isinstance(score, bool)
                or not isinstance(score, (int, float))
                or not math.isfinite(float(score))
                or not isinstance(text, str)):
            raise ServiceContractError("service_unavailable")
        public_text = text[:MAX_HIT_TEXT_CHARACTERS]
        _utf8_length(public_text, code="service_unavailable")
        text_truncated = len(public_text) != len(text)
        desired_texts.append((public_text, text_truncated))
        hits.append({
            "source_id": source_id,
            "score": float(score),
            "score_kind": "relevance",
            "text": public_text,
            "text_truncated": text_truncated,
            "metadata": public_metadata(getattr(hit, "metadata", None)),
        })
    payload = {
        "schema_version": SERVICE_SCHEMA_VERSION,
        "request_id": request,
        "corpus_id": corpus,
        "backend": backend,
        "requested_mode": requested_mode,
        "effective_mode": effective_mode,
        "reranker_applied": False,
        "warnings": public_warning_codes(getattr(response, "warnings", None)),
        "hits": hits,
    }
    return _fit_search_text_budget(payload, desired_texts)


def validate_public_search_response(
        payload: object, *, expected_corpus_id: object,
        expected_request_id: object) -> dict[str, Any]:
    """Strictly revalidate a search worker's bounded public envelope."""
    corpus = validate_corpus_id(expected_corpus_id)
    request = validate_request_id(expected_request_id)
    response_fields = frozenset({
        "schema_version", "request_id", "corpus_id", "backend",
        "requested_mode", "effective_mode", "reranker_applied",
        "warnings", "hits",
    })
    if not isinstance(payload, dict) or frozenset(payload) != response_fields:
        raise ServiceContractError("service_unavailable")
    assert isinstance(payload, dict)
    if (type(payload["schema_version"]) is not int
            or payload["schema_version"] != SERVICE_SCHEMA_VERSION
            or payload["request_id"] != request
            or payload["corpus_id"] != corpus
            or payload["backend"] not in SERVICE_BACKENDS
            or payload["requested_mode"] not in SEARCH_MODES
            or payload["effective_mode"] not in {"vector", "hybrid"}
            or payload["reranker_applied"] is not False):
        raise ServiceContractError("service_unavailable")

    warnings = payload["warnings"]
    if (not isinstance(warnings, list) or len(warnings) > 32
            or any(not isinstance(code, str)
                   or code not in PUBLIC_WARNING_CODES for code in warnings)
            or len(set(warnings)) != len(warnings)):
        raise ServiceContractError("service_unavailable")

    raw_hits = payload["hits"]
    if not isinstance(raw_hits, list) or len(raw_hits) > MAX_SEARCH_LIMIT:
        raise ServiceContractError("service_unavailable")
    hits: list[dict[str, Any]] = []
    hit_fields = frozenset({
        "source_id", "score", "score_kind", "text", "text_truncated",
        "metadata",
    })
    for raw_hit in raw_hits:
        if (not isinstance(raw_hit, dict)
                or frozenset(raw_hit) != hit_fields):
            raise ServiceContractError("service_unavailable")
        assert isinstance(raw_hit, dict)
        source_id = raw_hit["source_id"]
        score = raw_hit["score"]
        text = raw_hit["text"]
        metadata = raw_hit["metadata"]
        if (not isinstance(source_id, str)
                or not _OPAQUE_SOURCE_ID.fullmatch(source_id)
                or isinstance(score, bool)
                or not isinstance(score, (int, float))
                or not math.isfinite(float(score))
                or raw_hit["score_kind"] != "relevance"
                or not isinstance(text, str)
                or len(text) > MAX_HIT_TEXT_CHARACTERS
                or not isinstance(raw_hit["text_truncated"], bool)
                or not isinstance(metadata, dict)
                or not frozenset(metadata).issubset(PUBLIC_METADATA_FIELDS)
                or public_metadata(metadata) != metadata):
            raise ServiceContractError("service_unavailable")
        try:
            text.encode("utf-8")
        except UnicodeEncodeError as exc:
            raise ServiceContractError("service_unavailable") from exc
        hits.append({
            "source_id": source_id,
            "score": float(score),
            "score_kind": "relevance",
            "text": text,
            "text_truncated": raw_hit["text_truncated"],
            "metadata": metadata,
        })
    result = {
        "schema_version": SERVICE_SCHEMA_VERSION,
        "request_id": request,
        "corpus_id": corpus,
        "backend": payload["backend"],
        "requested_mode": payload["requested_mode"],
        "effective_mode": payload["effective_mode"],
        "reranker_applied": False,
        "warnings": list(warnings),
        "hits": hits,
    }
    if len(_encoded_json_bytes(result)) > MAX_PUBLIC_SEARCH_RESPONSE_BYTES:
        raise ServiceContractError("service_unavailable")
    return result
