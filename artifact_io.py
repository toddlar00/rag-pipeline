"""Strict artifact reads and crash-safe publication primitives.

The module is intentionally limited to Python's standard library.  Callers
inject domain identity, cached hashing, logging, and manifest-version policy so
this leaf does not depend on pipeline orchestration or backend state.
"""

from __future__ import annotations

import hashlib
import json
import math
import os
from collections.abc import Callable
from pathlib import Path
from typing import Any

from storage_policy import (
    atomic_write_private_json,
    atomic_write_private_jsonl,
    atomic_write_private_text,
)


ArtifactFingerprint = tuple[int, int, int, int, int]
ChunkIdFn = Callable[[dict], str]
CleanupErrorFn = Callable[..., None]
ReplaceFn = Callable[[Any, Any], Any]
ArtifactHashFn = Callable[[Path], str]
AtomicJsonWriterFn = Callable[[Path, object], None]


def _artifact_stat_fingerprint(stat_result) -> ArtifactFingerprint:
    """Return a strong identity for one opened artifact generation."""
    return (
        int(stat_result.st_dev), int(stat_result.st_ino),
        int(stat_result.st_size), int(stat_result.st_mtime_ns),
        int(stat_result.st_ctime_ns),
    )


def _read_index_artifact_snapshot(
        path: Path) -> tuple[bytes, str, ArtifactFingerprint]:
    """Read, identify, and hash the exact bytes from one file handle.

    Opening once prevents an atomic path replacement from mixing the hash of
    one chunks generation with records parsed from another. ``fstat`` guards
    against an in-place write racing the read.
    """
    path = Path(path)
    with path.open("rb") as handle:
        before = _artifact_stat_fingerprint(os.fstat(handle.fileno()))
        raw = handle.read()
        after = _artifact_stat_fingerprint(os.fstat(handle.fileno()))
    if after != before:
        raise RuntimeError(
            f"Artifact changed while it was being read: {path}")
    return raw, hashlib.sha256(raw).hexdigest(), after


def _parse_index_records_strict(
        raw: bytes, path: Path, *, chunk_id_fn: ChunkIdFn) -> list[dict]:
    """Parse and validate one already-captured chunks byte snapshot."""
    last_unicode_error = None
    for encoding in ("utf-8-sig", "latin-1"):
        try:
            contents = raw.decode(encoding)
        except UnicodeDecodeError as exc:
            last_unicode_error = exc
            continue

        records = []
        for line_number, line in enumerate(contents.splitlines(), 1):
            if not line.strip():
                continue
            try:
                record = json.loads(line)
            except json.JSONDecodeError as exc:
                raise ValueError(
                    f"Invalid JSON in chunks file {path}:{line_number}: "
                    f"{exc.msg}"
                ) from exc
            if not isinstance(record, dict):
                raise ValueError(
                    f"Invalid chunk at {path}:{line_number}: "
                    "record must be a JSON object"
                )
            if (not isinstance(record.get("text"), str)
                    or not record["text"].strip()):
                raise ValueError(
                    f"Invalid chunk at {path}:{line_number}: "
                    "'text' must be a non-empty string"
                )
            if not isinstance(record.get("metadata"), dict):
                raise ValueError(
                    f"Invalid chunk at {path}:{line_number}: "
                    "'metadata' must be a JSON object"
                )
            pending = [("metadata", record["metadata"])]
            while pending:
                field_path, value = pending.pop()
                if isinstance(value, float) and not math.isfinite(value):
                    raise ValueError(
                        f"Invalid chunk at {path}:{line_number}: "
                        f"'{field_path}' must contain finite numbers"
                    )
                if isinstance(value, dict):
                    pending.extend(
                        (f"{field_path}.{key}", child)
                        for key, child in value.items()
                    )
                elif isinstance(value, list):
                    pending.extend(
                        (f"{field_path}[{index}]", child)
                        for index, child in enumerate(value)
                    )
            records.append(record)

        if not records:
            raise ValueError(f"Chunks file contains no records: {path}")
        stable_ids = [chunk_id_fn(record) for record in records]
        if len(set(stable_ids)) != len(stable_ids):
            raise ValueError(
                f"Chunks file contains duplicate stable chunk IDs: {path}")
        return records

    raise ValueError(
        f"Could not decode chunks file: {path}") from last_unicode_error


def _atomic_write_text(
        path: Path, content: str, *, replace_fn: ReplaceFn | None = None,
        cleanup_error_fn: CleanupErrorFn | None = None) -> None:
    """Durably replace a text artifact without exposing partial contents."""
    atomic_write_private_text(
        path, content, replace_fn=replace_fn,
        cleanup_error_fn=cleanup_error_fn)


def _artifact_parameters_sha256(parameters: dict) -> str:
    """Return a stable credential-free configuration fingerprint."""
    serialized = json.dumps(
        parameters, ensure_ascii=False, sort_keys=True,
        separators=(",", ":"), default=str)
    return hashlib.sha256(serialized.encode("utf-8")).hexdigest()


def _artifact_completion_path(target: Path, *, stage: str) -> Path:
    if stage == "split_export":
        return target / ".rag-complete.json"
    return target.with_name(f".{target.name}.{stage}.complete.json")


def _write_artifact_completion(
        manifest_path: Path, *, stage: str, source_sha256: str,
        source_record_count: int | None, parameters: dict,
        outputs: dict[str, Path], schema_version: int,
        artifact_sha256_fn: ArtifactHashFn,
        atomic_write_json_fn: AtomicJsonWriterFn) -> None:
    output_records = []
    for role, path in sorted(outputs.items()):
        stat_result = path.stat()
        if not path.is_file() or stat_result.st_size <= 0:
            raise RuntimeError(
                f"Cannot commit incomplete {stage} output: {path}")
        output_records.append({
            "role": role,
            "name": path.name,
            "size": stat_result.st_size,
            "sha256": artifact_sha256_fn(path),
        })
    atomic_write_json_fn(manifest_path, {
        "schema_version": schema_version,
        "stage": stage,
        "source_sha256": source_sha256,
        "source_record_count": source_record_count,
        "parameters_sha256": _artifact_parameters_sha256(parameters),
        "outputs": output_records,
    })


def _fixed_artifacts_complete(
        manifest_path: Path, *, stage: str, source_sha256: str,
        source_record_count: int | None, parameters: dict,
        outputs: dict[str, Path], schema_version: int,
        artifact_sha256_fn: ArtifactHashFn) -> bool:
    try:
        payload = json.loads(manifest_path.read_text(encoding="utf-8"))
        if not isinstance(payload, dict):
            return False
        expected_header = {
            "schema_version": schema_version,
            "stage": stage,
            "source_sha256": source_sha256,
            "source_record_count": source_record_count,
            "parameters_sha256": _artifact_parameters_sha256(parameters),
        }
        if any(payload.get(key) != value
               for key, value in expected_header.items()):
            return False
        records = payload.get("outputs")
        if not isinstance(records, list) or len(records) != len(outputs):
            return False
        by_role = {
            record.get("role"): record for record in records
            if isinstance(record, dict) and isinstance(record.get("role"), str)
        }
        if set(by_role) != set(outputs):
            return False
        for role, path in outputs.items():
            record = by_role[role]
            if record.get("name") != path.name or not path.is_file():
                return False
            stat_result = path.stat()
            if (stat_result.st_size <= 0
                    or record.get("size") != stat_result.st_size
                    or record.get("sha256") != artifact_sha256_fn(path)):
                return False
        return True
    except (OSError, UnicodeError, json.JSONDecodeError, RuntimeError):
        return False


def _atomic_write_json(
        path: Path, payload: object, *, replace_fn: ReplaceFn | None = None,
        cleanup_error_fn: CleanupErrorFn | None = None) -> None:
    """Write JSON by replacing a fully flushed temporary file atomically."""
    atomic_write_private_json(
        path, payload, replace_fn=replace_fn,
        cleanup_error_fn=cleanup_error_fn)


def _atomic_write_jsonl(
        path: Path, records: list[dict], *,
        replace_fn: ReplaceFn | None = None,
        cleanup_error_fn: CleanupErrorFn | None = None) -> None:
    """Durably replace a JSONL artifact without exposing partial contents."""
    atomic_write_private_jsonl(
        path, records, replace_fn=replace_fn,
        cleanup_error_fn=cleanup_error_fn)
