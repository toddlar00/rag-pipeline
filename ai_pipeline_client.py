"""Reader-only, bounded loopback client for the existing local service v1.

This is an agent-callable client, not an MCP server, a shell bridge, or a new
authorization boundary. Search hits are untrusted source data and can be private.
No provider, OCR, vector store, service host, or credential file is imported.
"""

from __future__ import annotations

from dataclasses import dataclass, field
import hashlib
import http.client
import json
import math
import os
import re
import socket
import threading
import time
from uuid import uuid4

import service_contracts as contracts


TOKEN_ENVIRONMENT_VARIABLE = "RAG_PIPELINE_READER_TOKEN"
MAX_RESPONSE_BYTES = 2 * 1024 * 1024
MAX_CORPORA = 64
OPENAPI_SHA256 = "169b09c0a820c72483ff74da3e8a48175371b1c6d5ee9dbb4371f5d1953b02ce"
_MESSAGES = {
    "invalid_configuration": "Use literal loopback, a valid port, and a finite timeout in 0.1..600 seconds.",
    "invalid_request": "Use a documented reader operation and bounded service-v1 search JSON.",
    "credentials_missing": "Set RAG_PIPELINE_READER_TOKEN in this process from the service reader credential; never paste it into chat.",
    "credentials_invalid": "RAG_PIPELINE_READER_TOKEN has an invalid format; use the service reader credential.",
    "connection_failed": "The local service could not be reached; check its listener and health.",
    "deadline_exceeded": "The local service request exceeded its deadline.",
    "invalid_response": "The local service returned an unsupported or invalid bounded response.",
    "redirect_refused": "The local service attempted a redirect; redirects are not permitted.",
    "unauthorized": "The service rejected authentication; check the reader credential without displaying it.",
    "forbidden": "The credential does not permit this reader operation.",
    "not_found": "The requested service resource was not found; check corpus discovery.",
    "concurrency_limited": "The local service is busy; retry later within your operation budget.",
    "service_unavailable": "The local service is unavailable; check readiness before retrying.",
    "remote_error": "The local service rejected the request; inspect its documented contract.",
    "cancelled": "The reader request was cancelled.",
}
_RETRYABLE = {"connection_failed", "deadline_exceeded", "concurrency_limited", "service_unavailable"}


class PipelineClientError(RuntimeError):
    """Fixed diagnostics; never retain server bodies, queries, or credentials."""

    def __init__(self, code: str):
        self.code = code if isinstance(code, str) and code in _MESSAGES else "invalid_response"
        super().__init__(_MESSAGES[self.code])

    @property
    def exit_code(self) -> int:
        return 130 if self.code == "cancelled" else 3 if self.code in _RETRYABLE else 2

    def as_dict(self) -> dict:
        return {"schema_version": 1, "ok": False, "error": {
            "code": self.code, "message": _MESSAGES[self.code],
            "retryable": self.code in _RETRYABLE,
        }}


def parse_search_json(raw: bytes) -> dict:
    """Validate the complete stdin contract before opening a socket."""
    try:
        payload = contracts.decode_json_object(raw)
        request = contracts.parse_search_request(payload)
    except (contracts.ServiceContractError, TypeError, ValueError):
        raise PipelineClientError("invalid_request") from None
    return {"query": request.query, "limit": request.limit, "mode": request.mode,
            "filters": request.filters.as_dict()}


def _one_header(headers: list[tuple[str, str]], name: str) -> str | None:
    values = [value for key, value in headers if key.lower() == name]
    if len(values) > 1:
        raise PipelineClientError("invalid_response")
    return values[0] if values else None


def _remaining(deadline: float) -> float:
    remaining = deadline - time.monotonic()
    if remaining <= 0:
        raise PipelineClientError("deadline_exceeded")
    return remaining


def _close_quietly(resource) -> None:
    if resource is not None:
        try:
            resource.close()
        except Exception:
            pass


@dataclass(frozen=True, slots=True)
class PipelineReader:
    host: str = "127.0.0.1"
    port: int = 8765
    timeout: float = 30.0
    token: str | None = field(default=None, repr=False)

    def __post_init__(self) -> None:
        if (not isinstance(self.host, str) or self.host not in {"127.0.0.1", "::1"}
                or type(self.port) is not int or not 1 <= self.port <= 65535
                or type(self.timeout) not in {int, float}
                or not 0.1 <= self.timeout <= 600 or not math.isfinite(self.timeout)):
            raise PipelineClientError("invalid_configuration")
        if self.token is not None:
            try:
                contracts.validate_bearer_token(self.token)
            except contracts.ServiceContractError:
                raise PipelineClientError("credentials_invalid") from None

    @classmethod
    def from_environment(cls, *, host: str = "127.0.0.1", port: int = 8765,
                         timeout: float = 30.0, authenticated: bool = True) -> PipelineReader:
        token = os.environ.get(TOKEN_ENVIRONMENT_VARIABLE) if authenticated else None
        if authenticated and not token:
            raise PipelineClientError("credentials_missing")
        return cls(host=host, port=port, timeout=timeout, token=token)

    def health(self, probe: str = "ready") -> dict:
        if not isinstance(probe, str) or probe not in {"live", "ready"}:
            raise PipelineClientError("invalid_request")
        result = self._request("health", probe=probe)
        allowed = {"live"} if probe == "live" else {"ready", "not_ready"}
        if (set(result) != {"status"} or not isinstance(result["status"], str)
                or result["status"] not in allowed):
            raise PipelineClientError("invalid_response")
        return result

    def schema(self) -> dict:
        """Return the pinned service OpenAPI, not permission to use its admin routes."""
        result = self._request("schema")
        try:
            normalized = (json.dumps(result, ensure_ascii=False, sort_keys=True,
                                     separators=(",", ":")) + "\n").encode("utf-8")
        except (ValueError, UnicodeError, RecursionError):
            raise PipelineClientError("invalid_response") from None
        if hashlib.sha256(normalized).hexdigest() != OPENAPI_SHA256:
            raise PipelineClientError("invalid_response")
        return result

    def corpora(self) -> dict:
        result = self._request("corpora")
        if (set(result) != {"schema_version", "items"}
                or type(result["schema_version"]) is not int or result["schema_version"] != 1
                or not isinstance(result["items"], list) or len(result["items"]) > MAX_CORPORA):
            raise PipelineClientError("invalid_response")
        seen = set()
        for item in result["items"]:
            if (not isinstance(item, dict) or set(item) != {
                    "schema_version", "corpus_id", "backend", "capabilities"}
                    or type(item["schema_version"]) is not int or item["schema_version"] != 1
                    or item["backend"] != "qdrant"
                    or item["capabilities"] != {
                        "search": True, "reindex": True, "rerank": False, "llm_answer": False}
                    or any(type(value) is not bool for value in item["capabilities"].values())):
                raise PipelineClientError("invalid_response")
            try:
                corpus_id = contracts.validate_corpus_id(item["corpus_id"])
            except contracts.ServiceContractError:
                raise PipelineClientError("invalid_response") from None
            if corpus_id in seen:
                raise PipelineClientError("invalid_response")
            seen.add(corpus_id)
        return result

    def search(self, corpus_id: str, payload: dict) -> dict:
        return self._search("search", corpus_id, payload)

    def search_evidence(self, corpus_id: str, payload: dict) -> dict:
        """Read source-scope diagnostics; never assume OCR accuracy or adoption."""
        return self._search("search_evidence", corpus_id, payload)

    def _search(self, operation: str, corpus_id: str, payload: dict) -> dict:
        try:
            contracts.validate_corpus_id(corpus_id)
            request = contracts.parse_search_request(payload)
            normalized = {"query": request.query, "limit": request.limit,
                          "mode": request.mode, "filters": request.filters.as_dict()}
            raw = json.dumps(normalized, ensure_ascii=False, allow_nan=False).encode("utf-8")
        except (contracts.ServiceContractError, TypeError, ValueError, UnicodeError, RecursionError):
            raise PipelineClientError("invalid_request") from None
        checked = parse_search_json(raw)
        return self._request(operation, corpus_id=corpus_id, payload=checked)

    def _request(self, operation: str, *, probe: str = "ready",
                 corpus_id: str | None = None, payload: dict | None = None) -> dict:
        # No caller-provided URL, method, header, raw path, or admin operation.
        routes = {"health": ("GET", f"/health/{probe}"),
                  "schema": ("GET", "/v1/openapi.json"), "corpora": ("GET", "/v1/corpora")}
        if operation in {"search", "search_evidence"}:
            try:
                contracts.validate_corpus_id(corpus_id)
            except contracts.ServiceContractError:
                raise PipelineClientError("invalid_request") from None
            prefix = "/evidence/v1" if operation == "search_evidence" else "/v1"
            method, path = "POST", f"{prefix}/corpora/{corpus_id}/search"
        elif operation in routes and (operation != "health" or probe in {"live", "ready"}):
            method, path = routes[operation]
        else:
            raise PipelineClientError("invalid_request")
        if operation != "health" and self.token is None:
            raise PipelineClientError("credentials_missing")
        request_id = uuid4().hex
        headers = {"Accept": "application/json", "Accept-Encoding": "identity",
                   "Connection": "close", "X-Request-ID": request_id}
        if operation != "health":
            headers["Authorization"] = f"Bearer {self.token}"
        body = None
        if payload is not None:
            body = json.dumps(payload, ensure_ascii=False, allow_nan=False).encode("utf-8")
            headers["Content-Type"] = "application/json"
        deadline = time.monotonic() + self.timeout
        connection = response = timer = None
        expired = threading.Event()
        try:
            # http.client has no proxy, netrc, cookie, redirect, or SDK retry layer.
            connection = http.client.HTTPConnection(self.host, self.port, timeout=self.timeout)
            connection.set_debuglevel(0)
            connection.connect()
            network_socket = connection.sock

            def expire() -> None:
                expired.set()
                try:
                    network_socket.shutdown(socket.SHUT_RDWR)
                except OSError:
                    pass

            # Also bound trickling headers: per-read socket timeouts alone do not.
            timer = threading.Timer(_remaining(deadline), expire)
            timer.daemon = True
            timer.start()
            network_socket.settimeout(_remaining(deadline))
            connection.request(method, path, body=body, headers=headers)
            network_socket.settimeout(_remaining(deadline))
            response = connection.getresponse()
            response_headers = response.getheaders()
            if 300 <= response.status <= 399:
                raise PipelineClientError("redirect_refused")
            if _one_header(response_headers, "x-request-id") != request_id:
                raise PipelineClientError("invalid_response")
            content_type = _one_header(response_headers, "content-type")
            allowed_mime = {"application/json"} if response.status == 200 else {
                "application/json", "application/problem+json"}
            if (content_type is None or content_type.split(";")[0].strip().lower() not in allowed_mime
                    or ";" in content_type and content_type.split(";", 1)[1].strip().lower()
                    not in {"charset=utf-8", 'charset="utf-8"'}):
                raise PipelineClientError("invalid_response")
            if _one_header(response_headers, "content-encoding") not in {None, "identity"}:
                raise PipelineClientError("invalid_response")
            length = _one_header(response_headers, "content-length")
            transfer = _one_header(response_headers, "transfer-encoding")
            if (transfer not in {None, "chunked"} or transfer is not None and length is not None
                    or length is not None and (re.fullmatch(r"0|[1-9][0-9]*", length) is None
                                               or len(length) > 8 or int(length) > MAX_RESPONSE_BYTES)):
                raise PipelineClientError("invalid_response")
            raw = bytearray()
            while length is None or len(raw) < int(length):
                network_socket.settimeout(_remaining(deadline))
                chunk = response.read1(min(65536, MAX_RESPONSE_BYTES + 1 - len(raw)))
                if not chunk:
                    break
                raw.extend(chunk)
                if len(raw) > MAX_RESPONSE_BYTES:
                    raise PipelineClientError("invalid_response")
            _remaining(deadline)
            if expired.is_set():
                raise PipelineClientError("deadline_exceeded")
            if length is not None and len(raw) != int(length):
                raise PipelineClientError("invalid_response")
            result = contracts.decode_json_object(bytes(raw), max_bytes=MAX_RESPONSE_BYTES)
            if response.status != 200:
                if operation == "health" and probe == "ready" and response.status == 503 and result == {"status": "not_ready"}:
                    return result
                code = result.get("code")
                if (not isinstance(code, str) or code not in contracts.PROBLEM_DEFINITIONS
                        or result != contracts.ServiceProblem(code, request_id).as_dict()
                        or response.status != contracts.PROBLEM_DEFINITIONS[code][0]):
                    raise PipelineClientError("invalid_response")
                raise PipelineClientError(code if code in _MESSAGES else "remote_error")
            if operation in {"search", "search_evidence"}:
                if operation == "search_evidence":
                    from service_evidence_contracts import validate_evidence_response

                    result = validate_evidence_response(
                        result, expected_corpus_id=corpus_id, expected_request_id=request_id,
                        expected_request=contracts.parse_search_request(payload))
                    search = result["search"]
                else:
                    result = contracts.validate_public_search_response(
                        result, expected_corpus_id=corpus_id, expected_request_id=request_id)
                    search = result
                if search["requested_mode"] != payload["mode"] or len(search["hits"]) > payload["limit"]:
                    raise PipelineClientError("invalid_response")
            return result
        except PipelineClientError:
            raise
        except (TimeoutError, socket.timeout):
            raise PipelineClientError("deadline_exceeded") from None
        except (contracts.ServiceContractError, ImportError, TypeError, ValueError, RecursionError):
            raise PipelineClientError("invalid_response") from None
        except (OSError, http.client.HTTPException):
            raise PipelineClientError("deadline_exceeded" if expired.is_set() else "connection_failed") from None
        finally:
            if timer is not None:
                timer.cancel()
            _close_quietly(response)
            _close_quietly(connection)
