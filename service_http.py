"""Authenticated loopback HTTP implementation for the RAG service."""

from __future__ import annotations

import asyncio
import hmac
import ipaddress
import re
import threading
from contextlib import asynccontextmanager
from dataclasses import dataclass, field
from typing import Any, Callable, Protocol
from uuid import uuid4

from fastapi import Depends, FastAPI, Request
from fastapi.exceptions import RequestValidationError
from fastapi.responses import JSONResponse
from starlette.exceptions import HTTPException as StarletteHTTPException

import service_contracts


DEFAULT_HOST = "127.0.0.1"
DEFAULT_RECONCILE_INTERVAL_SECONDS = 2.0
_LOOPBACK_HOSTS = frozenset({"127.0.0.1", "::1"})
_ROLE_READER = "reader"
_ROLE_ADMIN = "admin"


@dataclass(frozen=True, slots=True)
class ServiceCredentials:
    reader_token: str = field(repr=False)
    admin_token: str = field(repr=False)

    def __post_init__(self) -> None:
        service_contracts.validate_bearer_token(self.reader_token)
        service_contracts.validate_bearer_token(self.admin_token)
        if hmac.compare_digest(self.reader_token, self.admin_token):
            raise service_contracts.ServiceContractError()

    def role_for(self, token: str) -> str | None:
        if hmac.compare_digest(token, self.admin_token):
            return _ROLE_ADMIN
        if hmac.compare_digest(token, self.reader_token):
            return _ROLE_READER
        return None


# Preserve the supported facade path for representation and pickle continuity.
ServiceCredentials.__module__ = "service_api"


class ServiceRuntimePort(Protocol):
    """Structural runtime operations consumed by the HTTP adapter."""

    def start(self) -> None: ...
    def close(self) -> None: ...
    def reconcile_jobs(self) -> list[dict[str, Any]]: ...
    def mark_unhealthy(self) -> None: ...
    def readiness(self) -> bool: ...
    def public_corpora(self) -> list[dict[str, Any]]: ...
    def search(self, corpus_id: str, request: object,
               request_id: str) -> dict[str, Any]: ...
    def reindex(self, corpus_id: str, request: object, *,
                idempotency_key: str) -> object: ...
    def list_jobs(self, *, status: str | None, limit: int,
                  cursor: object | None) -> dict[str, Any]: ...
    def get_job(self, job_id: str) -> dict[str, Any]: ...
    def cancel_job(self, job_id: str, *, attempt_number: int,
                   revision: int) -> object: ...
    def resume_job(self, job_id: str, *, attempt_number: int,
                   revision: int) -> object: ...
    def deletion_plan(self, job_id: str) -> dict[str, Any]: ...
    def delete_job(self, job_id: str, *, attempt_number: int,
                   revision: int) -> dict[str, Any]: ...


@dataclass(frozen=True, slots=True)
class ServiceHttpBinding:
    """Atomic runtime policy consumed by one HTTP application."""

    runtime_error_type: type[Exception]
    default_job_page_limit: int
    max_job_page_limit: int

    def __post_init__(self) -> None:
        if (not isinstance(self.runtime_error_type, type)
                or not issubclass(self.runtime_error_type, Exception)):
            raise TypeError("runtime_error_type must be an Exception type")
        for name in ("default_job_page_limit", "max_job_page_limit"):
            value = getattr(self, name)
            if isinstance(value, bool) or not isinstance(value, int) or value < 1:
                raise TypeError(f"{name} must be a positive integer")
        if self.default_job_page_limit > self.max_job_page_limit:
            raise ValueError(
                "default_job_page_limit cannot exceed max_job_page_limit")


def _single_header(request: Request, name: str) -> str | None:
    values = [
        value.decode("latin-1")
        for key, value in request.scope.get("headers", [])
        if key.lower() == name.lower().encode("ascii")
    ]
    if len(values) > 1:
        raise service_contracts.ServiceContractError()
    return values[0] if values else None


def _bounded_decimal(
        value: object, *, minimum: int, maximum: int,
        code: str = "invalid_request") -> int:
    if (not isinstance(value, str) or len(value) > len(str(maximum))
            or not re.fullmatch(r"0|[1-9][0-9]*", value)):
        raise service_contracts.ServiceContractError(code)
    parsed = int(value)
    if not minimum <= parsed <= maximum:
        raise service_contracts.ServiceContractError(code)
    return parsed


def _host_name(value: str) -> str:
    if not isinstance(value, str) or not value:
        raise service_contracts.ServiceContractError()
    if value.startswith("["):
        end = value.find("]")
        if end < 0:
            raise service_contracts.ServiceContractError()
        host = value[1:end]
        suffix = value[end + 1:]
        if suffix and (not suffix.startswith(":")
                       or not suffix[1:].isdigit()):
            raise service_contracts.ServiceContractError()
        return host
    if value.count(":") == 1:
        host, port = value.rsplit(":", 1)
        if not port.isdigit():
            raise service_contracts.ServiceContractError()
        return host
    if ":" in value:
        raise service_contracts.ServiceContractError()
    return value


def _problem_response(
        code: str, request_id: str, *,
        headers: dict[str, str] | None = None) -> JSONResponse:
    problem = service_contracts.ServiceProblem(code, request_id)
    response_headers = dict(headers or {})
    if code == "unauthorized":
        response_headers["WWW-Authenticate"] = "Bearer"
    if code in {"concurrency_limited", "service_unavailable"}:
        response_headers["Retry-After"] = "1"
    return JSONResponse(
        problem.as_dict(),
        status_code=problem.status,
        media_type="application/problem+json",
        headers=response_headers,
    )


def _secure_response(response):
    response.headers["Cache-Control"] = "no-store"
    response.headers["X-Content-Type-Options"] = "nosniff"
    response.headers["Referrer-Policy"] = "no-referrer"
    response.headers["X-Frame-Options"] = "DENY"
    response.headers["Content-Security-Policy"] = "default-src 'none'"
    return response


def _request_id(request: Request) -> str:
    value = _single_header(request, "x-request-id")
    if value is None:
        return uuid4().hex
    return service_contracts.validate_request_id(value)


def _authorization_token(request: Request) -> str:
    value = _single_header(request, "authorization")
    if value is None or not value.startswith("Bearer "):
        raise service_contracts.ServiceContractError("unauthorized")
    token = value[len("Bearer "):]
    try:
        return service_contracts.validate_bearer_token(token)
    except service_contracts.ServiceContractError as exc:
        raise service_contracts.ServiceContractError("unauthorized") from exc


def _require_role(role: str) -> Callable:
    async def dependency(request: Request) -> str:
        token = _authorization_token(request)
        credentials: ServiceCredentials = request.app.state.credentials
        observed = credentials.role_for(token)
        if observed is None:
            raise service_contracts.ServiceContractError("unauthorized")
        if role == _ROLE_ADMIN and observed != _ROLE_ADMIN:
            raise service_contracts.ServiceContractError("forbidden")
        return observed

    return dependency


require_reader = _require_role(_ROLE_READER)
require_admin = _require_role(_ROLE_ADMIN)


async def _bounded_json_body(request: Request) -> dict[str, Any]:
    content_type = _single_header(request, "content-type")
    if content_type is None:
        raise service_contracts.ServiceContractError(
            "unsupported_media_type")
    media_type, _, parameters = content_type.partition(";")
    if media_type.strip().casefold() != "application/json":
        raise service_contracts.ServiceContractError(
            "unsupported_media_type")
    if parameters and parameters.strip().casefold() not in {
            "charset=utf-8", "charset=\"utf-8\""}:
        raise service_contracts.ServiceContractError(
            "unsupported_media_type")
    content_encoding = _single_header(request, "content-encoding")
    if content_encoding not in {None, "identity"}:
        raise service_contracts.ServiceContractError(
            "unsupported_media_type")
    content_length = _single_header(request, "content-length")
    if content_length is not None:
        _bounded_decimal(
            content_length, minimum=0,
            maximum=service_contracts.MAX_REQUEST_BYTES,
            code="payload_too_large")
    body = bytearray()
    async for chunk in request.stream():
        if len(chunk) > service_contracts.MAX_REQUEST_BYTES - len(body):
            raise service_contracts.ServiceContractError(
                "payload_too_large")
        body.extend(chunk)
    return service_contracts.decode_json_object(bytes(body))


async def _require_empty_body(request: Request) -> None:
    content_length = _single_header(request, "content-length")
    if content_length not in {None, "0"}:
        raise service_contracts.ServiceContractError()
    async for chunk in request.stream():
        if chunk:
            raise service_contracts.ServiceContractError()


def _idempotency_key(request: Request) -> str:
    value = _single_header(request, "idempotency-key")
    if value is None:
        raise service_contracts.ServiceContractError(
            "precondition_required")
    return service_contracts.validate_idempotency_key(value)


def _job_precondition(request: Request) -> tuple[int, int]:
    value = _single_header(request, "if-match")
    if value is None:
        raise service_contracts.ServiceContractError(
            "precondition_required")
    return service_contracts.parse_job_etag(value)


def _job_response(
        payload: dict[str, Any], *, status_code: int = 200,
        location: str | None = None) -> JSONResponse:
    job = payload["job"] if "job" in payload else payload
    headers = {"ETag": job["etag"]}
    if location is not None:
        headers["Location"] = location
    return JSONResponse(payload, status_code=status_code, headers=headers)


def _query_values(request: Request) -> dict[str, str]:
    values: dict[str, str] = {}
    for key, value in request.query_params.multi_items():
        if key in values or key not in {"status", "limit", "cursor"}:
            raise service_contracts.ServiceContractError()
        values[key] = value
    return values


def service_openapi_document() -> dict[str, Any]:
    """Return the complete, static v1 client contract served to readers."""
    def schema_ref(name):
        return {"$ref": f"#/components/schemas/{name}"}

    def parameter_ref(name):
        return {"$ref": f"#/components/parameters/{name}"}

    def response_ref(status):
        return {"$ref": f"#/components/responses/Problem{status}"}

    common_headers = {
        "X-Request-ID": {"$ref": "#/components/headers/RequestID"},
    }

    def success(description, schema, *, headers=()):
        response = {
            "description": description,
            "headers": {
                **common_headers,
                **{
                    name: {"$ref": f"#/components/headers/{name}"}
                    for name in headers
                },
            },
            "content": {"application/json": {"schema": schema}},
        }
        return response

    def operation(
            operation_id, summary, *, role=None, parameters=(),
            request_schema=None, successes=None, errors=()):
        result = {
            "operationId": operation_id,
            "summary": summary,
            "parameters": [
                parameter_ref("RequestID"),
                *(parameter_ref(name) for name in parameters),
            ],
            "responses": {
                str(status): value
                for status, value in (successes or {}).items()
            },
        }
        if role is not None:
            result["security"] = (
                [{"readerBearer": []}, {"adminBearer": []}]
                if role == "reader" else [{"adminBearer": []}]
            )
        if request_schema is not None:
            result["requestBody"] = {
                "required": True,
                "content": {
                    "application/json": {"schema": request_schema},
                },
            }
        # Host/request-ID validation, the application-level concurrency gate,
        # and the sanitized fallback handler apply to every route.
        for status in sorted({400, 429, 500, *errors}):
            result["responses"].setdefault(str(status), response_ref(status))
        return result

    status_values = sorted(service_contracts.JOB_STATUSES)
    warning_values = sorted(service_contracts.PUBLIC_WARNING_CODES)
    metadata_value = {
        "oneOf": [
            {"type": "null"},
            {"type": "boolean"},
            {"type": "integer"},
            {"type": "number"},
            {"type": "string", "maxLength": 512},
            {
                "type": "array",
                "maxItems": 16,
                "items": {
                    "oneOf": [
                        {"type": "null"},
                        {"type": "boolean"},
                        {"type": "integer"},
                        {"type": "number"},
                        {"type": "string", "maxLength": 512},
                    ],
                },
            },
        ],
    }
    schemas = {
        "Problem": {
            "type": "object",
            "additionalProperties": False,
            "required": [
                "type", "title", "status", "code", "request_id",
                "retryable",
            ],
            "properties": {
                "type": {"type": "string", "format": "uri"},
                "title": {"type": "string"},
                "status": {"type": "integer", "minimum": 400,
                           "maximum": 599},
                "code": {
                    "type": "string",
                    "enum": sorted(service_contracts.PROBLEM_DEFINITIONS),
                },
                "request_id": {"$ref": "#/components/schemas/RequestID"},
                "retryable": {"type": "boolean"},
            },
        },
        "RequestID": {
            "type": "string", "minLength": 1, "maxLength": 128,
            "pattern": r"^[A-Za-z0-9][A-Za-z0-9._-]{0,127}$",
        },
        "Health": {
            "type": "object", "additionalProperties": False,
            "required": ["status"],
            "properties": {
                "status": {
                    "type": "string",
                    "enum": ["live", "ready", "not_ready"],
                },
            },
        },
        "CorpusCapabilities": {
            "type": "object", "additionalProperties": False,
            "required": ["search", "reindex", "rerank", "llm_answer"],
            "properties": {
                "search": {"const": True},
                "reindex": {"const": True},
                "rerank": {"const": False},
                "llm_answer": {"const": False},
            },
        },
        "Corpus": {
            "type": "object", "additionalProperties": False,
            "required": [
                "schema_version", "corpus_id", "backend", "capabilities",
            ],
            "properties": {
                "schema_version": {"const": 1},
                "corpus_id": {
                    "type": "string", "maxLength": 64,
                    "pattern": r"^[a-z][a-z0-9_-]{0,63}$",
                },
                "backend": {"const": "qdrant"},
                "capabilities": schema_ref("CorpusCapabilities"),
            },
        },
        "CorpusList": {
            "type": "object", "additionalProperties": False,
            "required": ["schema_version", "items"],
            "properties": {
                "schema_version": {"const": 1},
                "items": {"type": "array", "maxItems": 64,
                          "items": schema_ref("Corpus")},
            },
        },
        "SearchFilters": {
            "type": "object", "additionalProperties": False,
            "properties": {
                "content_type": {
                    "type": "string",
                    "enum": sorted(service_contracts.CONTENT_TYPES),
                },
                "chapter_num": {"type": "integer", "minimum": 0,
                                "maximum": 1_000_000},
            },
        },
        "SearchRequest": {
            "type": "object", "additionalProperties": False,
            "required": ["query"],
            "properties": {
                "query": {
                    "type": "string", "minLength": 1,
                    "maxLength": service_contracts.MAX_QUERY_CHARACTERS,
                },
                "limit": {
                    "type": "integer", "minimum": 1,
                    "maximum": service_contracts.MAX_SEARCH_LIMIT,
                    "default": 5,
                },
                "mode": {
                    "type": "string",
                    "enum": sorted(service_contracts.SEARCH_MODES),
                    "default": "auto",
                },
                "filters": schema_ref("SearchFilters"),
            },
        },
        "PublicMetadata": {
            "type": "object", "additionalProperties": False,
            "properties": {
                name: metadata_value
                for name in sorted(service_contracts.PUBLIC_METADATA_FIELDS)
            },
        },
        "SearchHit": {
            "type": "object", "additionalProperties": False,
            "required": [
                "source_id", "score", "score_kind", "text",
                "text_truncated", "metadata",
            ],
            "properties": {
                "source_id": {
                    "type": "string", "maxLength": 128,
                    "pattern": r"^[A-Za-z0-9][A-Za-z0-9._:-]{0,127}$",
                },
                "score": {"type": "number"},
                "score_kind": {"const": "relevance"},
                "text": {"type": "string", "maxLength":
                         service_contracts.MAX_HIT_TEXT_CHARACTERS},
                "text_truncated": {"type": "boolean"},
                "metadata": schema_ref("PublicMetadata"),
            },
        },
        "SearchResponse": {
            "type": "object", "additionalProperties": False,
            "required": [
                "schema_version", "request_id", "corpus_id", "backend",
                "requested_mode", "effective_mode", "reranker_applied",
                "warnings", "hits",
            ],
            "properties": {
                "schema_version": {"const": 1},
                "request_id": schema_ref("RequestID"),
                "corpus_id": {
                    "type": "string", "maxLength": 64,
                    "pattern": r"^[a-z][a-z0-9_-]{0,63}$",
                },
                "backend": {"const": "qdrant"},
                "requested_mode": {"type": "string", "enum":
                                   sorted(service_contracts.SEARCH_MODES)},
                "effective_mode": {"type": "string",
                                   "enum": ["hybrid", "vector"]},
                "reranker_applied": {"const": False},
                "warnings": {"type": "array", "maxItems": 32,
                             "uniqueItems": True,
                             "items": {"type": "string",
                                       "enum": warning_values}},
                "hits": {"type": "array", "maxItems":
                         service_contracts.MAX_SEARCH_LIMIT,
                         "items": schema_ref("SearchHit")},
            },
        },
        "ReindexRequest": {
            "type": "object", "additionalProperties": False,
            "properties": {"full_reindex": {"type": "boolean",
                                              "default": False}},
        },
        "Job": {
            "type": "object", "additionalProperties": False,
            "required": [
                "schema_version", "job_id", "command", "status",
                "attempt_number", "revision", "created_at", "updated_at",
                "terminal", "etag",
            ],
            "properties": {
                "schema_version": {"const": 1},
                "job_id": {"type": "string", "pattern": r"^[0-9a-f]{32}$"},
                "command": {"type": "string", "maxLength": 64},
                "status": {"type": "string", "enum": status_values},
                "attempt_number": {"type": "integer", "minimum": 1},
                "revision": {"type": "integer", "minimum": 0},
                "created_at": {"type": "number", "minimum": 0},
                "updated_at": {"type": "number", "minimum": 0},
                "terminal": {"type": "boolean"},
                "etag": {"type": "string", "pattern":
                         r'^"rag-job-a[1-9][0-9]*-r(?:0|[1-9][0-9]*)"$'},
            },
        },
        "ServiceJobResult": {
            "type": "object", "additionalProperties": False,
            "required": ["schema_version", "job", "idempotent_replay"],
            "properties": {
                "schema_version": {"const": 1},
                "job": schema_ref("Job"),
                "idempotent_replay": {"type": "boolean"},
            },
        },
        "JobList": {
            "type": "object", "additionalProperties": False,
            "required": ["schema_version", "items", "next_cursor"],
            "properties": {
                "schema_version": {"const": 1},
                "items": {"type": "array", "maxItems": 100,
                          "items": schema_ref("Job")},
                "next_cursor": {"oneOf": [
                    {"type": "null"},
                    {"type": "string", "maxLength": 256},
                ]},
            },
        },
        "DeletionPlan": {
            "type": "object", "additionalProperties": False,
            "required": [
                "schema_version", "job", "candidate_count", "total_bytes",
                "apply_required",
            ],
            "properties": {
                "schema_version": {"const": 1},
                "job": schema_ref("Job"),
                "candidate_count": {"type": "integer", "minimum": 0},
                "total_bytes": {"type": "integer", "minimum": 0},
                "apply_required": {"const": True},
            },
        },
        "DeletionOutcome": {
            "type": "object", "additionalProperties": False,
            "required": [
                "schema_version", "job_id", "deleted", "deleted_count",
                "deleted_bytes",
            ],
            "properties": {
                "schema_version": {"const": 1},
                "job_id": {"type": "string", "pattern": r"^[0-9a-f]{32}$"},
                "deleted": {"const": True},
                "deleted_count": {"type": "integer", "minimum": 0},
                "deleted_bytes": {"type": "integer", "minimum": 0},
            },
        },
    }
    parameters = {
        "RequestID": {
            "name": "X-Request-ID", "in": "header", "required": False,
            "schema": schema_ref("RequestID"),
        },
        "CorpusID": {
            "name": "corpus_id", "in": "path", "required": True,
            "schema": schemas["Corpus"]["properties"]["corpus_id"],
        },
        "JobID": {
            "name": "job_id", "in": "path", "required": True,
            "schema": schemas["Job"]["properties"]["job_id"],
        },
        "IdempotencyKey": {
            "name": "Idempotency-Key", "in": "header", "required": True,
            "schema": {"type": "string", "minLength": 8,
                       "maxLength": 128,
                       "pattern": r"^[A-Za-z0-9][A-Za-z0-9._~:+/=-]*$"},
        },
        "IfMatch": {
            "name": "If-Match", "in": "header", "required": True,
            "schema": schemas["Job"]["properties"]["etag"],
        },
        "ConfirmJobID": {
            "name": "X-Confirm-Job-ID", "in": "header", "required": True,
            "schema": schemas["Job"]["properties"]["job_id"],
        },
        "JobStatus": {
            "name": "status", "in": "query", "required": False,
            "schema": {"type": "string", "enum": status_values},
        },
        "JobLimit": {
            "name": "limit", "in": "query", "required": False,
            "schema": {"type": "integer", "minimum": 1, "maximum": 100,
                       "default": 50},
        },
        "JobCursor": {
            "name": "cursor", "in": "query", "required": False,
            "schema": {"type": "string", "maxLength": 256},
        },
    }
    headers = {
        "RequestID": {"description": "Validated or generated request ID.",
                      "schema": schema_ref("RequestID")},
        "ETag": {"description": "Exact job attempt/revision validator.",
                 "schema": schemas["Job"]["properties"]["etag"]},
        "Location": {"description": "Relative job resource URI.",
                     "schema": {"type": "string"}},
    }
    problem_responses = {}
    for status in sorted({
            definition[0]
            for definition in service_contracts.PROBLEM_DEFINITIONS.values()}):
        status_headers = dict(common_headers)
        if status == 401:
            status_headers["WWW-Authenticate"] = {
                "schema": {"type": "string", "const": "Bearer"}}
        if status in {429, 503}:
            status_headers["Retry-After"] = {
                "schema": {"type": "string", "const": "1"}}
        problem_responses[f"Problem{status}"] = {
            "description": f"Stable {status} problem response.",
            "headers": status_headers,
            "content": {
                "application/problem+json": {"schema": schema_ref("Problem")},
            },
        }

    paths = {
        "/health/live": {"get": operation(
            "getLiveness", "Liveness",
            successes={200: success("Service process is live.",
                                    schema_ref("Health"))},
        )},
        "/health/ready": {"get": operation(
            "getReadiness", "Readiness",
            successes={
                200: success("Service is ready.", schema_ref("Health")),
                503: success("Service is not ready.", schema_ref("Health")),
            },
        )},
        "/v1/openapi.json": {"get": operation(
            "getV1Contract", "Versioned API contract", role="reader",
            successes={200: success(
                "Static OpenAPI 3.1 contract.", {"type": "object"})},
            errors=(400, 401),
        )},
        "/v1/corpora": {"get": operation(
            "listCorpora", "List configured corpus capabilities",
            role="reader",
            successes={200: success(
                "Configured corpora.", schema_ref("CorpusList"))},
            errors=(400, 401, 503),
        )},
        "/v1/corpora/{corpus_id}/search": {"post": operation(
            "searchCorpus", "Search one statically configured Qdrant corpus",
            role="reader", parameters=("CorpusID",),
            request_schema=schema_ref("SearchRequest"),
            successes={200: success(
                "Bounded redacted retrieval result.",
                schema_ref("SearchResponse"))},
            errors=(400, 401, 404, 413, 415, 429, 503, 504),
        )},
        "/v1/corpora/{corpus_id}/reindex": {"post": operation(
            "reindexCorpus", "Submit an idempotent structured reindex job",
            role="admin", parameters=("CorpusID", "IdempotencyKey"),
            request_schema=schema_ref("ReindexRequest"),
            successes={
                200: success("Idempotent replay.",
                             schema_ref("ServiceJobResult"),
                             headers=("ETag", "Location")),
                202: success("New job accepted.",
                             schema_ref("ServiceJobResult"),
                             headers=("ETag", "Location")),
            },
            errors=(400, 401, 403, 404, 409, 413, 415, 428, 503),
        )},
        "/v1/jobs": {"get": operation(
            "listJobs", "List redacted durable job summaries", role="admin",
            parameters=("JobStatus", "JobLimit", "JobCursor"),
            successes={200: success("Job page.", schema_ref("JobList"))},
            errors=(400, 401, 403, 503),
        )},
        "/v1/jobs/{job_id}": {
            "get": operation(
                "getJob", "Read one redacted job summary", role="admin",
                parameters=("JobID",),
                successes={200: success("Job summary.", schema_ref("Job"),
                                        headers=("ETag",))},
                errors=(400, 401, 403, 404, 503),
            ),
            "delete": operation(
                "deleteJob", "Delete one exact safely terminal job",
                role="admin",
                parameters=("JobID", "IfMatch", "ConfirmJobID"),
                successes={200: success(
                    "Deletion outcome.", schema_ref("DeletionOutcome"))},
                errors=(400, 401, 403, 404, 409, 412, 428, 503),
            ),
        },
        "/v1/jobs/{job_id}/cancel": {"post": operation(
            "cancelJob", "Request exact-attempt cancellation", role="admin",
            parameters=("JobID", "IfMatch"),
            successes={202: success(
                "Cancellation accepted.", schema_ref("ServiceJobResult"),
                headers=("ETag",))},
            errors=(400, 401, 403, 404, 409, 412, 428, 503),
        )},
        "/v1/jobs/{job_id}/resume": {"post": operation(
            "resumeJob", "Resume one exact recoverable attempt", role="admin",
            parameters=("JobID", "IfMatch"),
            successes={202: success(
                "Resume accepted.", schema_ref("ServiceJobResult"),
                headers=("ETag",))},
            errors=(400, 401, 403, 404, 409, 412, 428, 503),
        )},
        "/v1/jobs/{job_id}/deletion-plan": {"post": operation(
            "planJobDeletion", "Return a redacted deletion dry run",
            role="admin", parameters=("JobID",),
            successes={200: success(
                "Deletion dry run.", schema_ref("DeletionPlan"),
                headers=("ETag",))},
            errors=(400, 401, 403, 404, 409, 503),
        )},
    }
    bearer = {"type": "http", "scheme": "bearer", "bearerFormat": "opaque"}
    return {
        "openapi": "3.1.0",
        "info": {
            "title": "RAG Pipeline Local Service",
            "version": "1.0.0",
            "description": (
                "Authenticated single-principal API bound only to loopback. "
                "Filesystem paths, job arguments, logs, and credentials are "
                "never response fields."),
        },
        # Resolve against the current loopback origin so the static contract
        # remains accurate for custom ports and the supported IPv6 binding.
        "servers": [{"url": "/"}],
        "components": {
            "securitySchemes": {
                "readerBearer": dict(bearer),
                "adminBearer": dict(bearer),
            },
            "headers": headers,
            "parameters": parameters,
            "responses": problem_responses,
            "schemas": schemas,
        },
        "paths": paths,
    }


def create_app(
        runtime: ServiceRuntimePort,
        credentials: ServiceCredentials, *,
        http_binding: ServiceHttpBinding,
        host: str = DEFAULT_HOST,
        reconcile_interval_seconds: float =
        DEFAULT_RECONCILE_INTERVAL_SECONDS,
        max_http_concurrency: int = 64,
        enforce_peer_loopback: bool = True) -> FastAPI:
    """Create one non-global ASGI application around an injected runtime."""
    if not isinstance(http_binding, ServiceHttpBinding):
        raise TypeError("http_binding must be a ServiceHttpBinding")
    if host not in _LOOPBACK_HOSTS:
        raise service_contracts.ServiceContractError()
    interval = service_contracts.positive_finite(
        reconcile_interval_seconds)
    if interval < 0.1 or interval > 3600:
        raise service_contracts.ServiceContractError()
    if (isinstance(max_http_concurrency, bool)
            or not isinstance(max_http_concurrency, int)
            or not 1 <= max_http_concurrency <= 256
            or not isinstance(enforce_peer_loopback, bool)):
        raise service_contracts.ServiceContractError()
    http_slots = threading.BoundedSemaphore(max_http_concurrency)

    @asynccontextmanager
    async def lifespan(app: FastAPI):
        try:
            await asyncio.to_thread(runtime.start)
        except Exception:
            raise RuntimeError("local service startup failed") from None
        stop = asyncio.Event()

        async def reconcile_loop():
            while True:
                try:
                    await asyncio.wait_for(stop.wait(), timeout=interval)
                    return
                except asyncio.TimeoutError:
                    try:
                        await asyncio.to_thread(runtime.reconcile_jobs)
                    except http_binding.runtime_error_type as exc:
                        if exc.fatal:
                            runtime.mark_unhealthy()
                    except Exception:
                        runtime.mark_unhealthy()

        task = asyncio.create_task(reconcile_loop())
        try:
            yield
        finally:
            stop.set()
            try:
                await task
            finally:
                try:
                    await asyncio.to_thread(runtime.close)
                except Exception:
                    raise RuntimeError(
                        "local service shutdown failed") from None

    app = FastAPI(
        title="RAG Pipeline Local Service",
        version="1.0.0",
        docs_url=None,
        redoc_url=None,
        openapi_url=None,
        lifespan=lifespan,
    )
    app.state.runtime = runtime
    app.state.credentials = credentials
    app.state.configured_host = host

    @app.middleware("http")
    async def security_boundary(request: Request, call_next):
        generated_request_id = uuid4().hex
        acquired = False
        try:
            request_id = _request_id(request)
            host_header = _single_header(request, "host")
            if host_header is None or _host_name(host_header) != host:
                raise service_contracts.ServiceContractError()
            if enforce_peer_loopback:
                client = request.client
                try:
                    peer_is_loopback = (
                        client is not None
                        and ipaddress.ip_address(client.host).is_loopback)
                except (TypeError, ValueError):
                    peer_is_loopback = False
                if not peer_is_loopback:
                    raise service_contracts.ServiceContractError()
            request.state.request_id = request_id
            if not http_slots.acquire(blocking=False):
                response = _problem_response(
                    "concurrency_limited", request_id)
            else:
                acquired = True
                response = await call_next(request)
        except service_contracts.ServiceContractError as exc:
            request_id = getattr(
                request.state, "request_id", generated_request_id)
            response = _problem_response(exc.code, request_id)
        except Exception:
            request_id = getattr(
                request.state, "request_id", generated_request_id)
            runtime.mark_unhealthy()
            response = _problem_response("internal_error", request_id)
        finally:
            if acquired:
                http_slots.release()
        response.headers["X-Request-ID"] = request_id
        return _secure_response(response)

    @app.exception_handler(service_contracts.ServiceContractError)
    async def contract_error(request: Request, exc):
        return _problem_response(exc.code, request.state.request_id)

    @app.exception_handler(http_binding.runtime_error_type)
    async def runtime_error(request: Request, exc):
        headers = None
        if exc.job_id is not None:
            headers = {"Location": f"/v1/jobs/{exc.job_id}"}
        return _problem_response(
            exc.code, request.state.request_id, headers=headers)

    @app.exception_handler(StarletteHTTPException)
    async def http_error(request: Request, exc):
        code = (
            "not_found" if exc.status_code == 404
            else "method_not_allowed" if exc.status_code == 405
            else "invalid_request")
        return _problem_response(code, request.state.request_id)

    @app.exception_handler(RequestValidationError)
    async def validation_error(request: Request, _exc):
        return _problem_response("invalid_request", request.state.request_id)

    @app.exception_handler(Exception)
    async def unexpected_error(request: Request, _exc):
        runtime.mark_unhealthy()
        return _problem_response("internal_error", request.state.request_id)

    @app.get("/health/live")
    async def live():
        return {"status": "live"}

    @app.get("/health/ready")
    async def ready():
        if not runtime.readiness():
            return JSONResponse({"status": "not_ready"}, status_code=503)
        return {"status": "ready"}

    @app.get("/v1/openapi.json", dependencies=[Depends(require_reader)])
    async def openapi_v1():
        return service_openapi_document()

    @app.get("/v1/corpora", dependencies=[Depends(require_reader)])
    async def corpora():
        items = await asyncio.to_thread(runtime.public_corpora)
        return {
            "schema_version": service_contracts.SERVICE_SCHEMA_VERSION,
            "items": items,
        }

    @app.post(
        "/v1/corpora/{corpus_id}/search",
        dependencies=[Depends(require_reader)])
    async def search(corpus_id: str, request: Request):
        body = await _bounded_json_body(request)
        search_request = service_contracts.parse_search_request(body)
        return await asyncio.to_thread(
            runtime.search, corpus_id, search_request,
            request.state.request_id)

    @app.post(
        "/v1/corpora/{corpus_id}/reindex",
        dependencies=[Depends(require_admin)])
    async def reindex(corpus_id: str, request: Request):
        key = _idempotency_key(request)
        body = await _bounded_json_body(request)
        reindex_request = service_contracts.parse_reindex_request(body)
        result = await asyncio.to_thread(
            runtime.reindex, corpus_id, reindex_request,
            idempotency_key=key)
        payload = result.as_dict()
        location = f"/v1/jobs/{result.job['job_id']}"
        return _job_response(
            payload,
            status_code=200 if result.idempotent_replay else 202,
            location=location)

    @app.get("/v1/jobs", dependencies=[Depends(require_admin)])
    async def list_jobs(request: Request):
        query = _query_values(request)
        status = query.get("status")
        limit_value = query.get("limit")
        if limit_value is None:
            limit = http_binding.default_job_page_limit
        else:
            limit = _bounded_decimal(
                limit_value, minimum=1,
                maximum=http_binding.max_job_page_limit)
        cursor = (
            service_contracts.decode_job_cursor(query["cursor"])
            if "cursor" in query else None)
        return await asyncio.to_thread(
            runtime.list_jobs, status=status, limit=limit, cursor=cursor)

    @app.get("/v1/jobs/{job_id}", dependencies=[Depends(require_admin)])
    async def get_job(job_id: str):
        payload = await asyncio.to_thread(runtime.get_job, job_id)
        return _job_response(payload)

    @app.post(
        "/v1/jobs/{job_id}/cancel",
        dependencies=[Depends(require_admin)])
    async def cancel_job(job_id: str, request: Request):
        await _require_empty_body(request)
        attempt_number, revision = _job_precondition(request)
        result = await asyncio.to_thread(
            runtime.cancel_job, job_id,
            attempt_number=attempt_number, revision=revision)
        return _job_response(result.as_dict(), status_code=202)

    @app.post(
        "/v1/jobs/{job_id}/resume",
        dependencies=[Depends(require_admin)])
    async def resume_job(job_id: str, request: Request):
        await _require_empty_body(request)
        attempt_number, revision = _job_precondition(request)
        result = await asyncio.to_thread(
            runtime.resume_job, job_id,
            attempt_number=attempt_number, revision=revision)
        return _job_response(result.as_dict(), status_code=202)

    @app.post(
        "/v1/jobs/{job_id}/deletion-plan",
        dependencies=[Depends(require_admin)])
    async def deletion_plan(job_id: str, request: Request):
        await _require_empty_body(request)
        payload = await asyncio.to_thread(runtime.deletion_plan, job_id)
        return _job_response(payload)

    @app.delete(
        "/v1/jobs/{job_id}", dependencies=[Depends(require_admin)])
    async def delete_job(job_id: str, request: Request):
        await _require_empty_body(request)
        service_contracts.validate_job_id(job_id)
        attempt_number, revision = _job_precondition(request)
        confirmation = _single_header(request, "x-confirm-job-id")
        if confirmation is None:
            raise service_contracts.ServiceContractError(
                "precondition_required")
        try:
            confirmation = service_contracts.validate_job_id(confirmation)
        except service_contracts.ServiceContractError as exc:
            raise service_contracts.ServiceContractError(
                "precondition_failed") from exc
        if not hmac.compare_digest(confirmation, job_id):
            raise service_contracts.ServiceContractError(
                "precondition_failed")
        return await asyncio.to_thread(
            runtime.delete_job, job_id,
            attempt_number=attempt_number, revision=revision)

    return app


__all__ = [
    "DEFAULT_HOST",
    "DEFAULT_RECONCILE_INTERVAL_SECONDS",
    "ServiceCredentials",
    "ServiceHttpBinding",
    "ServiceRuntimePort",
    "create_app",
    "require_admin",
    "require_reader",
    "service_openapi_document",
]
