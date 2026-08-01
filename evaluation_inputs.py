"""Strict, link-aware input contracts shared by evaluation policy layers.

This dependency-light module intentionally contains no evaluator, review,
release, retrieval, model, or runtime orchestration imports.  Artifact reads
flow only through the standard-library storage leaves.
"""

from __future__ import annotations

import json
import math
import re
from pathlib import Path

import artifact_io
import storage_policy


_HEX64_RE = re.compile(r"[0-9a-f]{64}")
_MAX_JSON_NESTING_DEPTH = 64


def _hex_digest(value: object, *, label: str) -> str:
    if not isinstance(value, str) or _HEX64_RE.fullmatch(value) is None:
        raise ValueError(f"{label} must be a lowercase SHA-256 digest")
    return value


def _strict_json_bytes(raw: bytes, *, label: str, max_bytes: int) -> dict:
    if len(raw) > max_bytes:
        raise ValueError(f"{label} exceeds {max_bytes} bytes")

    try:
        text = raw.decode("utf-8-sig")
    except UnicodeError as exc:
        raise ValueError(f"{label} is not valid UTF-8 JSON") from exc

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
            if depth > _MAX_JSON_NESTING_DEPTH:
                raise ValueError(
                    f"{label} exceeds JSON nesting depth "
                    f"{_MAX_JSON_NESTING_DEPTH}")
        elif character in "]}":
            depth -= 1

    def reject_duplicate_keys(pairs):
        result = {}
        for key, value in pairs:
            if key in result:
                raise ValueError(f"{label} contains duplicate field {key!r}")
            result[key] = value
        return result

    def reject_constant(value):
        raise ValueError(f"{label} contains non-finite number {value}")

    def parse_finite_float(value):
        parsed = float(value)
        if not math.isfinite(parsed):
            reject_constant(value)
        return parsed

    def parse_runtime_int(value):
        try:
            parsed = int(value)
            finite = float(parsed)
        except (OverflowError, ValueError) as exc:
            raise ValueError(
                f"{label} contains an integer outside the finite runtime range"
            ) from exc
        if not math.isfinite(finite):
            raise ValueError(
                f"{label} contains an integer outside the finite runtime range")
        return parsed

    try:
        payload = json.loads(
            text,
            object_pairs_hook=reject_duplicate_keys,
            parse_constant=reject_constant,
            parse_float=parse_finite_float,
            parse_int=parse_runtime_int,
        )
    except (json.JSONDecodeError, RecursionError) as exc:
        raise ValueError(f"{label} is not valid UTF-8 JSON") from exc
    if not isinstance(payload, dict):
        raise ValueError(f"{label} must be one JSON object")
    return payload


def _read_snapshot(path: Path, *, label: str, max_bytes: int) -> tuple[bytes, str]:
    path = Path(path)
    storage_policy.assert_no_link_components(path)
    raw, digest, _ = artifact_io._read_index_artifact_snapshot(
        path, max_bytes=max_bytes)
    return raw, digest


def _corpus_contract(queries: list[dict]) -> dict:
    declarations = [query.get("corpus") for query in queries]
    if any(not isinstance(value, dict) for value in declarations):
        raise ValueError("owner review requires every query to pin one corpus")
    digest_values = []
    for declaration in declarations:
        raw_digest = declaration.get("sha256", "")
        if not isinstance(raw_digest, str):
            _hex_digest(raw_digest, label="corpus SHA-256")
        digest_values.append(raw_digest.lower())
    count_values = [value.get("record_count") for value in declarations]
    scheme_values = [value.get("id_scheme") for value in declarations]

    def all_same(values):
        return bool(values) and all(value == values[0] for value in values[1:])

    digests = set(digest_values)
    if (
        len(digests) != 1
        or not all_same(count_values)
        or not all_same(scheme_values)
    ):
        raise ValueError(
            "owner review queries must share one corpus digest, count, and ID scheme")
    digest = _hex_digest(next(iter(digests)), label="corpus SHA-256")
    count = count_values[0]
    scheme = scheme_values[0]
    if isinstance(count, bool) or not isinstance(count, int) or count < 1:
        raise ValueError("corpus record count must be a positive integer")
    if not isinstance(scheme, str) or not scheme.strip():
        raise ValueError("corpus ID scheme must be a non-empty string")
    return {
        "sha256": digest,
        "record_count": count,
        "id_scheme": scheme.strip(),
    }
