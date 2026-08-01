"""Bounded, body-safe ingestion for provider HTTP responses.

Provider endpoints are outside the process trust boundary.  This module keeps
their response bytes out of diagnostics, rejects ambiguous framing and MIME
types, and enforces decoded-byte and nesting ceilings before JSON materializes.
It deliberately has no provider SDK or application imports.
"""

from __future__ import annotations

import json
import math
import time
from collections.abc import Mapping
from collections.abc import Callable
from typing import Protocol


LLM_RESPONSE_MAX_BYTES = 16 * 1024 * 1024
RERANK_RESPONSE_MAX_BYTES = 8 * 1024 * 1024
EMBEDDING_RESPONSE_MAX_BYTES = 32 * 1024 * 1024
JSON_RESPONSE_MAX_DEPTH = 64
_STREAM_CHUNK_BYTES = 64 * 1024


class ProviderResponseRejected(ValueError):
    """A provider response violated the bounded JSON transport contract."""


class ProviderResponseDeadlineExceeded(TimeoutError):
    """A provider response exceeded its overall streaming deadline."""


class ProviderResponseReadError(ConnectionError):
    """A provider response stream or its cleanup failed safely."""


class ProviderResponseReadTimeout(TimeoutError):
    """A provider response stream hit its transport read timeout."""


class HttpResponseProtocol(Protocol):
    """Response surface used by the bounded reader."""

    status_code: int
    headers: Mapping[str, object]

    def iter_content(self, *, chunk_size: int): ...

    def close(self) -> None: ...


class OwnedHttpResponse:
    """Keep a Requests session alive until its streaming response is closed."""

    __slots__ = ("_closed", "_owner", "_response")

    def __init__(self, response: object, owner: object):
        self._response = response
        self._owner = owner
        self._closed = False

    @property
    def response(self) -> object:
        """Expose the wrapped response for narrow transport-level tests."""
        return self._response

    def __getattr__(self, name: str):
        return getattr(self._response, name)

    def close(self) -> None:
        if self._closed:
            return
        self._closed = True
        failed = False
        try:
            close = getattr(self._response, "close", None)
            if callable(close):
                try:
                    close()
                except Exception:
                    failed = True
        finally:
            close_owner = getattr(self._owner, "close", None)
            if callable(close_owner):
                try:
                    close_owner()
                except Exception:
                    failed = True
        if failed:
            raise ProviderResponseReadError(
                "provider response cleanup failed") from None


def close_http_response(response: object) -> None:
    """Close a response when supported; repeated calls must remain harmless."""
    close = getattr(response, "close", None)
    if callable(close):
        try:
            close()
        except ProviderResponseReadError:
            raise
        except Exception:
            raise ProviderResponseReadError(
                "provider response cleanup failed") from None


def _header_value(headers: object, name: str) -> object | None:
    if not isinstance(headers, Mapping):
        raise ProviderResponseRejected("provider response headers are invalid")
    value = headers.get(name)
    if value is not None:
        return value
    expected = name.casefold()
    for key, candidate in headers.items():
        if isinstance(key, str) and key.casefold() == expected:
            return candidate
    return None


def _content_length(headers: object, *, max_bytes: int) -> int | None:
    value = _header_value(headers, "Content-Length")
    transfer_encoding = _header_value(headers, "Transfer-Encoding")
    if value is not None and transfer_encoding is not None:
        raise ProviderResponseRejected(
            "provider response has ambiguous HTTP framing")
    if value is None:
        return None
    if not isinstance(value, str):
        raise ProviderResponseRejected(
            "provider response Content-Length is invalid")
    normalized = value.strip()
    if not normalized or not normalized.isascii() or not normalized.isdecimal():
        raise ProviderResponseRejected(
            "provider response Content-Length is invalid")
    declared = int(normalized)
    if declared > max_bytes:
        raise ProviderResponseRejected("provider response exceeds byte limit")
    return declared


def _require_json_content_type(headers: object) -> None:
    value = _header_value(headers, "Content-Type")
    if not isinstance(value, str):
        raise ProviderResponseRejected(
            "provider response Content-Type is missing or invalid")
    pieces = [piece.strip() for piece in value.split(";")]
    media_type = pieces[0].casefold()
    if not (
        media_type == "application/json"
        or (media_type.startswith("application/")
            and media_type.endswith("+json"))
    ):
        raise ProviderResponseRejected(
            "provider response Content-Type is not JSON")
    for parameter in pieces[1:]:
        if not parameter:
            continue
        name, separator, parameter_value = parameter.partition("=")
        if name.strip().casefold() != "charset":
            continue
        if not separator or parameter_value.strip().strip('"').casefold() not in {
            "utf-8", "utf8",
        }:
            raise ProviderResponseRejected(
                "provider response JSON charset is unsupported")


def _require_bounded_json_depth(text: str, *, max_depth: int) -> None:
    depth = 0
    in_string = False
    escaped = False
    for character in text:
        if in_string:
            if escaped:
                escaped = False
            elif character == "\\":
                escaped = True
            elif character == '"':
                in_string = False
            continue
        if character == '"':
            in_string = True
        elif character in "[{":
            depth += 1
            if depth > max_depth:
                raise ProviderResponseRejected(
                    "provider response JSON nesting exceeds limit")
        elif character in "]}":
            depth -= 1


def _reject_nonstandard_number(_value: str):
    raise ProviderResponseRejected(
        "provider response JSON contains a non-standard number")


def _unique_object(pairs: list[tuple[str, object]]) -> dict[str, object]:
    result: dict[str, object] = {}
    for key, value in pairs:
        if key in result:
            raise ProviderResponseRejected(
                "provider response JSON contains a duplicate field")
        result[key] = value
    return result


def read_bounded_json_response(
        response: HttpResponseProtocol, *, max_bytes: int,
        max_depth: int = JSON_RESPONSE_MAX_DEPTH,
        deadline_seconds: float = 60.0,
        monotonic_fn: Callable[[], float] = time.monotonic) -> object:
    """Stream, bound, decode, and parse one successful JSON response.

    The response is always closed.  ``iter_content`` yields decoded bytes in
    Requests, so the byte ceiling also applies after gzip/deflate expansion.
    No response byte or text is included in any raised diagnostic.
    """
    if isinstance(max_bytes, bool) or not isinstance(max_bytes, int) \
            or max_bytes < 1:
        raise ValueError("max_bytes must be a positive integer")
    if isinstance(max_depth, bool) or not isinstance(max_depth, int) \
            or max_depth < 1:
        raise ValueError("max_depth must be a positive integer")
    if isinstance(deadline_seconds, bool) or not isinstance(
            deadline_seconds, (int, float)) or not math.isfinite(
                deadline_seconds) or deadline_seconds <= 0:
        raise ValueError("deadline_seconds must be positive")

    try:
        started = monotonic_fn()
        headers = getattr(response, "headers", None)
        _require_json_content_type(headers)
        declared_length = _content_length(headers, max_bytes=max_bytes)
        content_encoding = _header_value(headers, "Content-Encoding")
        if content_encoding is not None and not isinstance(
                content_encoding, str):
            raise ProviderResponseRejected(
                "provider response Content-Encoding is invalid")

        iterator = getattr(response, "iter_content", None)
        if not callable(iterator):
            raise ProviderResponseRejected(
                "provider response does not support bounded streaming")
        normalized_encoding = (
            content_encoding.strip().casefold() if content_encoding else "")
        is_encoded = normalized_encoding not in {"", "identity"}
        body = bytearray()
        try:
            for chunk in iterator(chunk_size=_STREAM_CHUNK_BYTES):
                if monotonic_fn() - started > deadline_seconds:
                    raise ProviderResponseDeadlineExceeded(
                        "provider response exceeded streaming deadline")
                if not chunk:
                    continue
                if not isinstance(chunk, (bytes, bytearray, memoryview)):
                    raise ProviderResponseRejected(
                        "provider response stream yielded invalid bytes")
                if len(body) + len(chunk) > max_bytes:
                    raise ProviderResponseRejected(
                        "provider response exceeds byte limit")
                if (declared_length is not None and not is_encoded
                        and len(body) + len(chunk) > declared_length):
                    raise ProviderResponseRejected(
                        "provider response length does not match Content-Length")
                body.extend(chunk)
        except (ProviderResponseRejected, ProviderResponseDeadlineExceeded):
            raise
        except Exception as exc:
            if "timeout" in type(exc).__name__.casefold():
                raise ProviderResponseReadTimeout(
                    "provider response stream timed out") from None
            raise ProviderResponseReadError(
                "provider response stream failed") from None

        if (declared_length is not None and not is_encoded
                and len(body) != declared_length):
            raise ProviderResponseRejected(
                "provider response length does not match Content-Length")
        try:
            text = bytes(body).decode("utf-8-sig")
        except UnicodeDecodeError:
            raise ProviderResponseRejected(
                "provider response JSON is not UTF-8") from None
        _require_bounded_json_depth(text, max_depth=max_depth)
        try:
            return json.loads(
                text,
                object_pairs_hook=_unique_object,
                parse_constant=_reject_nonstandard_number,
            )
        except ProviderResponseRejected:
            raise
        except (json.JSONDecodeError, RecursionError, UnicodeError):
            raise ProviderResponseRejected(
                "provider response is not valid JSON") from None
    finally:
        close_http_response(response)
