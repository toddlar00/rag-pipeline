"""Ownership-checked, dry-run-first retention for sensitive local artifacts."""

from __future__ import annotations

import hashlib
import json
import math
import os
import re
import stat
import time
from dataclasses import dataclass
from pathlib import Path, PurePosixPath
from typing import Any
from uuid import uuid4

from storage_policy import (
    atomic_write_private_json,
    assert_no_link_components,
    ensure_private_directory,
    path_is_link_like,
)


RUN_MANIFEST_NAME = ".rag-run.json"
UI_EXPORT_MARKER_NAME = ".rag-owned.json"
QUARANTINE_DIRECTORY_NAME = ".rag-quarantine"
QUARANTINE_RECEIPT_NAME = ".rag-quarantine.json"
RUN_MANIFEST_SCHEMA_VERSION = 1
QUARANTINE_SCHEMA_VERSION = 1
_MAX_MARKER_BYTES = 256 * 1024
_CACHE_RECORD_MAX_BYTES = 32 * 1024 * 1024
DEFAULT_LLM_CACHE_MAX_BYTES = 5 * 1024 * 1024 * 1024
_TOKEN = re.compile(r"^[0-9a-f]{32}$")
_CACHE_KEY = re.compile(r"^[0-9a-f]{64}$")
_CACHE_SHARD = re.compile(r"^[0-9a-f]{2}$")
_RUN_STATES = frozenset({"active", "complete", "failed", "cancelled"})
_UI_STATES = frozenset({"creating", "complete", "failed"})


class RetentionError(RuntimeError):
    """Raised when ownership or safe-deletion checks fail closed."""


@dataclass(frozen=True)
class RetentionCandidate:
    category: str
    path: Path
    relative_path: str
    size_bytes: int
    file_count: int
    directory_count: int
    age_days: float
    fingerprint: str
    ownership_token: str | None = None

    def as_dict(self) -> dict[str, Any]:
        return {
            "category": self.category,
            "path": str(self.path),
            "relative_path": self.relative_path,
            "size_bytes": self.size_bytes,
            "file_count": self.file_count,
            "directory_count": self.directory_count,
            "age_days": round(self.age_days, 3),
            "fingerprint": self.fingerprint,
        }


@dataclass(frozen=True)
class RetentionPlan:
    action: str
    root: Path
    candidates: tuple[RetentionCandidate, ...]
    created_at: float
    context: dict[str, Any]

    @property
    def total_bytes(self) -> int:
        return sum(candidate.size_bytes for candidate in self.candidates)

    def as_dict(self) -> dict[str, Any]:
        return {
            "schema_version": 1,
            "action": self.action,
            "mode": "dry_run",
            "apply_required": True,
            "root": str(self.root),
            "candidate_count": len(self.candidates),
            "total_bytes": self.total_bytes,
            "candidates": [
                candidate.as_dict() for candidate in self.candidates],
        }


def _absolute(path: Path) -> Path:
    path = Path(path)
    return path if path.is_absolute() else Path(os.path.abspath(path))


def _safe_leaf(value: str, *, label: str) -> str:
    if (not isinstance(value, str) or not value or value in {".", ".."}
            or Path(value).name != value or "\0" in value
            or value.casefold().startswith(".rag-")):
        raise RetentionError(f"invalid {label}")
    return value


def _safe_relative(value: str, *, label: str) -> PurePosixPath:
    if not isinstance(value, str) or not value or "\\" in value:
        raise RetentionError(f"invalid {label}")
    relative = PurePosixPath(value)
    if (relative.is_absolute() or any(
            part in {"", ".", ".."} for part in relative.parts)):
        raise RetentionError(f"invalid {label}")
    return relative


def _finite_timestamp(value: object, *, label: str) -> float:
    if (isinstance(value, bool) or not isinstance(value, (int, float))
            or not math.isfinite(float(value)) or float(value) < 0):
        raise RetentionError(f"invalid {label}")
    return float(value)


def _normalize_age_days(value: float) -> float:
    try:
        normalized = float(value)
    except (TypeError, ValueError) as exc:
        raise ValueError("older-than days must be numeric") from exc
    if not math.isfinite(normalized) or normalized < 0:
        raise ValueError("older-than days must be finite and non-negative")
    return normalized


def _normalize_size_limit(value: int | None) -> int | None:
    if value is None:
        return None
    if isinstance(value, bool) or not isinstance(value, int) or value < 0:
        raise ValueError("cache size limit must be a non-negative integer")
    return value


def _read_json_object(path: Path, *, max_bytes: int = _MAX_MARKER_BYTES) -> dict:
    path = Path(path)
    assert_no_link_components(path)
    flags = os.O_RDONLY | getattr(os, "O_NOFOLLOW", 0)
    flags |= getattr(os, "O_BINARY", 0)
    try:
        descriptor = os.open(path, flags)
    except OSError as exc:
        raise RetentionError(f"invalid owned marker: {path}") from exc
    try:
        before = os.fstat(descriptor)
        if (not stat.S_ISREG(before.st_mode) or before.st_nlink != 1
                or before.st_size <= 0 or before.st_size > max_bytes):
            raise RetentionError(f"invalid owned marker: {path}")
        chunks = []
        remaining = int(before.st_size)
        while remaining:
            chunk = os.read(descriptor, min(remaining, 1024 * 1024))
            if not chunk:
                break
            chunks.append(chunk)
            remaining -= len(chunk)
        after = os.fstat(descriptor)
    except OSError as exc:
        raise RetentionError(f"invalid owned marker: {path}") from exc
    finally:
        os.close(descriptor)
    def identity(result) -> tuple[int, int, int, int, int, int]:
        return (
            int(result.st_dev), int(result.st_ino), int(result.st_size),
            int(result.st_mtime_ns), int(result.st_ctime_ns),
            int(result.st_nlink))
    if remaining or identity(before) != identity(after):
        raise RetentionError(f"owned marker changed while reading: {path}")
    try:
        payload = json.loads(b"".join(chunks).decode("utf-8"))
    except (UnicodeError, json.JSONDecodeError) as exc:
        raise RetentionError(f"invalid owned marker: {path}") from exc
    if not isinstance(payload, dict):
        raise RetentionError(f"invalid owned marker: {path}")
    return payload


def _validate_run_manifest(payload: dict, *, run_name: str) -> dict:
    if (payload.get("schema_version") != RUN_MANIFEST_SCHEMA_VERSION
            or payload.get("kind") != "pipeline_run"
            or payload.get("run_name") != run_name
            or payload.get("state") not in _RUN_STATES):
        raise RetentionError("pipeline run manifest failed validation")
    token = payload.get("ownership_token")
    if not isinstance(token, str) or not _TOKEN.fullmatch(token):
        raise RetentionError("pipeline run ownership token is invalid")
    _safe_leaf(payload.get("job_scope"), label="pipeline job scope")
    _finite_timestamp(payload.get("created_at"), label="created_at")
    _finite_timestamp(payload.get("updated_at"), label="updated_at")
    siblings = payload.get("owned_siblings")
    if (not isinstance(siblings, list) or len(siblings) > 16
            or any(not isinstance(sibling, str) for sibling in siblings)
            or len(set(siblings)) != len(siblings)):
        raise RetentionError("pipeline owned siblings are invalid")
    for sibling in siblings:
        _safe_leaf(sibling, label="owned sibling")
        if sibling == run_name:
            raise RetentionError("run directory cannot be an owned sibling")
    vector_stores = payload.get("vector_stores")
    if not isinstance(vector_stores, list) or len(vector_stores) > 8:
        raise RetentionError("pipeline vector-store ownership is invalid")
    observed = set()
    for record in vector_stores:
        if not isinstance(record, dict):
            raise RetentionError("pipeline vector-store ownership is invalid")
        backend = record.get("backend")
        relative = _safe_relative(
            record.get("path"), label="vector-store path")
        collection = record.get("collection")
        if (backend not in {"chroma", "qdrant"}
                or relative.parts[0] != run_name
                or not isinstance(collection, str) or not collection):
            raise RetentionError("pipeline vector-store ownership is invalid")
        identity = (backend, str(relative), collection)
        if identity in observed:
            raise RetentionError("duplicate vector-store ownership")
        observed.add(identity)
    return payload


def load_pipeline_run_manifest(
        output_root: Path, run_name: str) -> tuple[Path, dict]:
    output_root = _absolute(output_root)
    run_name = _safe_leaf(run_name, label="run name")
    run_root = output_root / run_name
    assert_no_link_components(run_root)
    if not run_root.is_dir():
        raise RetentionError(f"pipeline run directory not found: {run_name}")
    manifest_path = run_root / RUN_MANIFEST_NAME
    payload = _read_json_object(manifest_path)
    return manifest_path, _validate_run_manifest(payload, run_name=run_name)


def ensure_pipeline_run_manifest(
        output_root: Path, run_root: Path, *, job_scope: str,
        owned_siblings: list[Path],
        vector_stores: list[dict[str, str]]) -> Path:
    output_root = ensure_private_directory(output_root)
    run_root = _absolute(run_root)
    if run_root.parent != output_root:
        raise RetentionError("pipeline run must be an immediate output child")
    run_name = _safe_leaf(run_root.name, label="run name")
    _safe_leaf(job_scope, label="pipeline job scope")
    if path_is_link_like(run_root):
        raise RetentionError("pipeline run cannot be a link or junction")
    manifest_path = run_root / RUN_MANIFEST_NAME
    sibling_names = []
    for sibling in owned_siblings:
        sibling = _absolute(sibling)
        if sibling.parent != output_root:
            raise RetentionError("owned sibling must be an output-root child")
        sibling_names.append(_safe_leaf(
            sibling.name, label="owned sibling"))
    if not manifest_path.exists():
        for sibling_name in sibling_names:
            sibling = output_root / sibling_name
            if sibling.exists() or path_is_link_like(sibling):
                raise RetentionError(
                    "refusing to claim a pre-existing sibling without an "
                    "ownership manifest")
    ensure_private_directory(run_root)
    normalized_stores = []
    for record in vector_stores:
        backend = record.get("backend")
        collection = record.get("collection")
        store_path = _absolute(Path(record.get("path", "")))
        try:
            relative = store_path.relative_to(output_root).as_posix()
        except ValueError as exc:
            raise RetentionError(
                "vector store must be within the output root") from exc
        relative_path = _safe_relative(
            relative, label="vector-store path")
        if (backend not in {"chroma", "qdrant"}
                or relative_path.parts[0] != run_name
                or not isinstance(collection, str) or not collection):
            raise RetentionError("pipeline vector-store ownership is invalid")
        normalized_stores.append({
            "backend": backend,
            "collection": collection,
            "path": relative,
        })
    now = time.time()
    if manifest_path.exists():
        payload = _validate_run_manifest(
            _read_json_object(manifest_path), run_name=run_name)
        expected = {
            "job_scope": job_scope,
            "owned_siblings": sorted(sibling_names),
            "vector_stores": sorted(
                normalized_stores,
                key=lambda item: (item["backend"], item["path"])),
        }
        actual = {
            "job_scope": payload["job_scope"],
            "owned_siblings": sorted(payload["owned_siblings"]),
            "vector_stores": sorted(
                payload["vector_stores"],
                key=lambda item: (item["backend"], item["path"])),
        }
        if actual != expected:
            raise RetentionError(
                "existing pipeline ownership manifest conflicts with this run")
        payload = dict(payload)
        payload.update({"state": "active", "updated_at": now})
    else:
        payload = {
            "schema_version": RUN_MANIFEST_SCHEMA_VERSION,
            "kind": "pipeline_run",
            "ownership_token": uuid4().hex,
            "run_name": run_name,
            "job_scope": job_scope,
            "created_at": now,
            "updated_at": now,
            "state": "active",
            "owned_siblings": sorted(sibling_names),
            "vector_stores": sorted(
                normalized_stores,
                key=lambda item: (item["backend"], item["path"])),
        }
    _validate_run_manifest(payload, run_name=run_name)
    atomic_write_private_json(manifest_path, payload, indent=2)
    return manifest_path


def mark_pipeline_run_state(manifest_path: Path, state: str) -> None:
    if state not in _RUN_STATES - {"active"}:
        raise ValueError("terminal pipeline run state is invalid")
    manifest_path = Path(manifest_path)
    payload = _validate_run_manifest(
        _read_json_object(manifest_path), run_name=manifest_path.parent.name)
    payload = dict(payload)
    payload.update({"state": state, "updated_at": time.time()})
    atomic_write_private_json(manifest_path, payload, indent=2)


def _snapshot_owned_path(path: Path) -> tuple[str, int, int, int]:
    path = Path(path)
    digest = hashlib.sha256()
    size_bytes = 0
    file_count = 0
    directory_count = 0

    def visit(candidate: Path, relative: str) -> None:
        nonlocal size_bytes, file_count, directory_count
        if path_is_link_like(candidate):
            raise RetentionError(
                f"owned path contains a link or junction: {candidate}")
        result = os.lstat(candidate)
        kind = "d" if stat.S_ISDIR(result.st_mode) else "f"
        digest.update(json.dumps([
            relative, kind, int(result.st_dev), int(result.st_ino),
            int(result.st_size), int(result.st_mtime_ns),
            int(result.st_nlink),
        ], ensure_ascii=False, separators=(",", ":")).encode("utf-8"))
        if stat.S_ISDIR(result.st_mode):
            directory_count += 1
            with os.scandir(candidate) as entries:
                children = sorted(entries, key=lambda entry: entry.name)
            for entry in children:
                child_relative = (
                    f"{relative}/{entry.name}" if relative else entry.name)
                visit(Path(entry.path), child_relative)
            return
        if not stat.S_ISREG(result.st_mode) or result.st_nlink != 1:
            raise RetentionError(
                f"owned path contains a special or hard-linked file: "
                f"{candidate}")
        file_count += 1
        size_bytes += int(result.st_size)

    visit(path, "")
    return digest.hexdigest(), size_bytes, file_count, directory_count


def _candidate(
        *, category: str, path: Path, root: Path, timestamp: float,
        now: float, ownership_token: str | None = None) -> RetentionCandidate:
    fingerprint, size_bytes, file_count, directory_count = (
        _snapshot_owned_path(path))
    return RetentionCandidate(
        category=category,
        path=_absolute(path),
        relative_path=_absolute(path).relative_to(_absolute(root)).as_posix(),
        size_bytes=size_bytes,
        file_count=file_count,
        directory_count=directory_count,
        age_days=max(0.0, (now - timestamp) / 86400.0),
        fingerprint=fingerprint,
        ownership_token=ownership_token,
    )


def plan_pipeline_run_deletion(
        output_root: Path, run_name: str, *, now: float | None = None,
        ) -> RetentionPlan:
    output_root = _absolute(output_root)
    manifest_path, manifest = load_pipeline_run_manifest(
        output_root, run_name)
    current_time = time.time() if now is None else float(now)
    updated_at = _finite_timestamp(
        manifest["updated_at"], label="updated_at")
    run_root = manifest_path.parent
    candidates = [_candidate(
        category="pipeline_run", path=run_root, root=output_root,
        timestamp=updated_at, now=current_time,
        ownership_token=manifest["ownership_token"])]
    for sibling_name in manifest["owned_siblings"]:
        sibling = output_root / sibling_name
        if sibling.exists() or path_is_link_like(sibling):
            candidates.append(_candidate(
                category="pipeline_sibling", path=sibling,
                root=output_root, timestamp=updated_at, now=current_time,
                ownership_token=manifest["ownership_token"]))
    return RetentionPlan(
        action="delete_pipeline_run", root=output_root,
        candidates=tuple(candidates), created_at=current_time,
        context={
            "run_name": run_name,
            "job_scope": manifest["job_scope"],
            "ownership_token": manifest["ownership_token"],
            "vector_stores": manifest["vector_stores"],
        },
    )


def _validate_background_job(
        path: Path, *, expected_job_id: str,
        expected_token: str, required_status: str | None = None) -> None:
    spec = _read_json_object(path / "spec.json")
    state = _read_json_object(path / "state.json")
    if (not _TOKEN.fullmatch(expected_job_id)
            or not _CACHE_KEY.fullmatch(expected_token)
            or spec.get("kind") != "job_spec"
            or state.get("kind") != "job_state"
            or spec.get("job_id") != expected_job_id
            or state.get("job_id") != expected_job_id
            or state.get("spec_sha256") != expected_token):
        raise RetentionError("background job ownership failed validation")
    if required_status is not None and state.get("status") != required_status:
        raise RetentionError(
            f"background job must be {required_status!r} before deletion")


def plan_background_job_deletion(
        job_root: Path, job_id: str, *,
        now: float | None = None) -> RetentionPlan:
    """Plan deletion of one schema-validated, safely terminal job."""
    import job_runtime

    store = job_runtime.JobStore(job_root)
    try:
        job_path, ownership_token, updated_at = store.retention_record(job_id)
    except job_runtime.JobRuntimeError as exc:
        raise RetentionError(str(exc)) from exc
    _validate_background_job(
        job_path, expected_job_id=job_id,
        expected_token=ownership_token)
    current_time = time.time() if now is None else float(now)
    candidate = _candidate(
        category="background_job", path=job_path, root=store.root,
        timestamp=updated_at, now=current_time,
        ownership_token=ownership_token)
    return RetentionPlan(
        action="delete_background_job", root=store.root,
        candidates=(candidate,), created_at=current_time,
        context={"job_id": job_id, "ownership_token": ownership_token},
    )


def _validate_cache_payload(path: Path, key: str) -> str:
    payload = _read_json_object(path, max_bytes=_CACHE_RECORD_MAX_BYTES)
    if (payload.get("schema_version") not in {1, 2}
            or payload.get("cache_key") != key
            or not isinstance(payload.get("result"), dict)):
        raise RetentionError("LLM cache record failed ownership validation")
    return key


def _validate_cache_record(path: Path) -> str:
    match = re.fullmatch(r"([0-9a-f]{64})\.json", path.name)
    if not match or not _CACHE_SHARD.fullmatch(path.parent.name):
        raise RetentionError("LLM cache path is not pipeline-owned")
    key = match.group(1)
    if key[:2] != path.parent.name:
        raise RetentionError("LLM cache shard does not match its key")
    return _validate_cache_payload(path, key)


def plan_llm_cache_prune(
        cache_root: Path, *, older_than_days: float,
        max_total_bytes: int | None = None,
        now: float | None = None) -> RetentionPlan:
    cache_root = _absolute(cache_root)
    age = _normalize_age_days(older_than_days)
    size_limit = _normalize_size_limit(max_total_bytes)
    current_time = time.time() if now is None else float(now)
    cutoff = current_time - age * 86400.0
    owned_records: list[tuple[Path, os.stat_result, str]] = []
    if cache_root.exists():
        assert_no_link_components(cache_root)
        if not cache_root.is_dir():
            raise RetentionError("LLM cache root is not a directory")
        for shard in sorted(cache_root.iterdir(), key=lambda path: path.name):
            if not _CACHE_SHARD.fullmatch(shard.name):
                continue
            if path_is_link_like(shard) or not shard.is_dir():
                raise RetentionError("LLM cache shard is not a safe directory")
            for record in sorted(shard.iterdir(), key=lambda path: path.name):
                if not re.fullmatch(r"[0-9a-f]{64}\.json", record.name):
                    continue
                result = os.lstat(record)
                key = _validate_cache_record(record)
                owned_records.append((record, result, key))
    selected = {
        path for path, result, _ in owned_records
        if result.st_mtime <= cutoff}
    remaining_bytes = sum(
        int(result.st_size) for path, result, _ in owned_records
        if path not in selected)
    if size_limit is not None and remaining_bytes > size_limit:
        for path, result, _ in sorted(
                (record for record in owned_records
                 if record[0] not in selected),
                key=lambda record: (record[1].st_mtime, str(record[0]))):
            selected.add(path)
            remaining_bytes -= int(result.st_size)
            if remaining_bytes <= size_limit:
                break
    candidates = []
    for record, result, key in owned_records:
        if record in selected:
            candidates.append(_candidate(
                category="llm_cache", path=record, root=cache_root,
                timestamp=result.st_mtime, now=current_time,
                ownership_token=key))
    return RetentionPlan(
        action="prune_llm_cache", root=cache_root,
        candidates=tuple(candidates), created_at=current_time,
        context={
            "older_than_days": age,
            "max_total_bytes": size_limit,
            "owned_bytes_after_plan": remaining_bytes,
        },
    )


def _validate_ui_marker(
        export_root: Path, *, expected_token: str | None = None) -> dict:
    marker = _read_json_object(export_root / UI_EXPORT_MARKER_NAME)
    token = marker.get("ownership_token")
    expected = export_root.name if expected_token is None else expected_token
    if (marker.get("schema_version") != 1
            or marker.get("kind") != "ui_export"
            or not isinstance(token, str) or not _TOKEN.fullmatch(token)
            or token != expected
            or marker.get("state") not in _UI_STATES
            or not isinstance(marker.get("artifacts"), list)):
        raise RetentionError("UI export marker failed ownership validation")
    _finite_timestamp(marker.get("created_at"), label="created_at")
    _finite_timestamp(marker.get("updated_at"), label="updated_at")
    return marker


def _ui_export_parents(output_root: Path) -> list[Path]:
    parents = []
    direct = output_root / "ui_exports"
    if not path_is_link_like(direct) and direct.is_dir():
        parents.append(direct)
    if not output_root.is_dir():
        return parents
    for child in sorted(output_root.iterdir(), key=lambda path: path.name):
        if child.name.startswith(".") or path_is_link_like(child):
            continue
        if child.is_dir():
            candidate = child / "ui_exports"
            if candidate.is_dir() and not path_is_link_like(candidate):
                parents.append(candidate)
    return parents


def plan_ui_export_prune(
        output_root: Path, *, older_than_days: float,
        now: float | None = None) -> RetentionPlan:
    output_root = _absolute(output_root)
    age = _normalize_age_days(older_than_days)
    current_time = time.time() if now is None else float(now)
    cutoff = current_time - age * 86400.0
    candidates = []
    if output_root.exists():
        assert_no_link_components(output_root)
        for export_parent in _ui_export_parents(output_root):
            for export_root in sorted(
                    export_parent.iterdir(), key=lambda path: path.name):
                if (not _TOKEN.fullmatch(export_root.name)
                        or path_is_link_like(export_root)
                        or not export_root.is_dir()):
                    continue
                marker = _validate_ui_marker(export_root)
                if marker["state"] != "complete":
                    continue
                updated_at = _finite_timestamp(
                    marker["updated_at"], label="updated_at")
                if updated_at > cutoff:
                    continue
                candidates.append(_candidate(
                    category="ui_export", path=export_root,
                    root=output_root, timestamp=updated_at,
                    now=current_time,
                    ownership_token=marker["ownership_token"]))
    return RetentionPlan(
        action="prune_ui_exports", root=output_root,
        candidates=tuple(candidates), created_at=current_time,
        context={"older_than_days": age},
    )


def _validate_candidate(
        candidate: RetentionCandidate, *, path: Path | None = None,
        quarantined: bool = False) -> None:
    candidate_path = candidate.path if path is None else Path(path)
    if not candidate_path.exists() and not path_is_link_like(candidate_path):
        raise RetentionError(
            f"retention candidate disappeared: {candidate_path}")
    fingerprint, _, _, _ = _snapshot_owned_path(candidate_path)
    if fingerprint != candidate.fingerprint:
        raise RetentionError(
            f"retention candidate changed after planning: {candidate_path}")
    if candidate.category == "llm_cache":
        key = (
            _validate_cache_payload(candidate_path, candidate.ownership_token)
            if quarantined
            else _validate_cache_record(candidate_path)
        )
        if key != candidate.ownership_token:
            raise RetentionError("LLM cache ownership changed after planning")
    elif candidate.category == "ui_export":
        marker = _validate_ui_marker(
            candidate_path,
            expected_token=(candidate.ownership_token if quarantined else None),
        )
        if marker["ownership_token"] != candidate.ownership_token:
            raise RetentionError("UI export ownership changed after planning")
    elif candidate.category == "pipeline_run":
        payload = _validate_run_manifest(
            _read_json_object(candidate_path / RUN_MANIFEST_NAME),
            run_name=candidate.path.name)
        if payload["ownership_token"] != candidate.ownership_token:
            raise RetentionError("pipeline run ownership changed after planning")
    elif candidate.category == "background_job":
        _validate_background_job(
            candidate_path, expected_job_id=candidate.path.name,
            expected_token=str(candidate.ownership_token),
            required_status="deleting")


def _delete_owned_path(path: Path) -> None:
    if path_is_link_like(path):
        raise RetentionError(
            f"refusing to delete through a link or junction: {path}")
    result = os.lstat(path)
    if stat.S_ISDIR(result.st_mode):
        with os.scandir(path) as entries:
            children = sorted(
                (Path(entry.path) for entry in entries),
                key=lambda child: (
                    child.name == QUARANTINE_RECEIPT_NAME, child.name),
            )
        for child in children:
            _delete_owned_path(child)
        os.rmdir(path)
        return
    if not stat.S_ISREG(result.st_mode) or result.st_nlink != 1:
        raise RetentionError(
            f"refusing to delete a special or hard-linked file: {path}")
    os.unlink(path)


def _quarantine_receipt(
        operation_id: str, plan: RetentionPlan, *, state: str) -> dict:
    return {
        "schema_version": QUARANTINE_SCHEMA_VERSION,
        "kind": "retention_quarantine",
        "operation_id": operation_id,
        "action": plan.action,
        "created_at": time.time(),
        "state": state,
        "entries": [candidate.relative_path for candidate in plan.candidates],
    }


def apply_retention_plan(plan: RetentionPlan) -> dict[str, Any]:
    """Apply a plan under any root-level coordination it requires."""
    if plan.action == "delete_background_job":
        import job_runtime

        try:
            store = job_runtime.JobStore(plan.root)
            with store.store_lease():
                return _apply_retention_plan_unlocked(plan)
        except job_runtime.JobRuntimeError as exc:
            raise RetentionError(
                "background job store could not be locked safely") from exc
    return _apply_retention_plan_unlocked(plan)


def _apply_retention_plan_unlocked(plan: RetentionPlan) -> dict[str, Any]:
    """Revalidate, quarantine, then erase every candidate in one plan."""
    if plan.action not in {
            "delete_pipeline_run", "delete_background_job",
            "prune_llm_cache", "prune_ui_exports"}:
        raise ValueError("retention plan action is not directly applicable")
    if not plan.candidates:
        return {
            "action": plan.action, "mode": "applied",
            "deleted_count": 0, "deleted_bytes": 0,
        }
    for candidate in plan.candidates:
        _validate_candidate(candidate)
    ensure_private_directory(plan.root)
    quarantine_root = ensure_private_directory(
        plan.root / QUARANTINE_DIRECTORY_NAME)
    operation_id = uuid4().hex
    operation_root = ensure_private_directory(
        quarantine_root / operation_id)
    receipt_path = operation_root / QUARANTINE_RECEIPT_NAME
    atomic_write_private_json(
        receipt_path,
        _quarantine_receipt(operation_id, plan, state="staging"),
        indent=2)
    moved: list[tuple[Path, Path]] = []
    try:
        for index, candidate in enumerate(plan.candidates):
            _validate_candidate(candidate)
            destination = operation_root / (
                f"{index:04d}-{candidate.path.name}")
            if destination.exists() or path_is_link_like(destination):
                raise RetentionError(
                    "quarantine destination unexpectedly exists")
            os.replace(candidate.path, destination)
            moved.append((candidate.path, destination))
            _validate_candidate(
                candidate, path=destination, quarantined=True)
        atomic_write_private_json(
            receipt_path,
            _quarantine_receipt(operation_id, plan, state="staged"),
            indent=2)
        for candidate, (_, destination) in zip(plan.candidates, moved):
            _validate_candidate(
                candidate, path=destination, quarantined=True)
    except BaseException as staging_error:
        rollback_errors = []
        for source, destination in reversed(moved):
            try:
                if source.exists() or path_is_link_like(source):
                    raise RetentionError(
                        f"rollback destination was recreated: {source}")
                os.replace(destination, source)
            except BaseException as rollback_error:
                rollback_errors.append(rollback_error)
        if rollback_errors:
            try:
                atomic_write_private_json(
                    receipt_path,
                    _quarantine_receipt(
                        operation_id, plan, state="rollback_failed"),
                    indent=2)
            except BaseException:
                pass
            raise RetentionError(
                f"retention staging failed and quarantine recovery is "
                f"required at {operation_root}") from staging_error
        try:
            _delete_owned_path(operation_root)
        except BaseException:
            pass
        raise
    try:
        _delete_owned_path(operation_root)
    except BaseException as exc:
        raise RetentionError(
            f"owned data was quarantined but secure erasure did not "
            f"finish; recovery data remains at {operation_root}") from exc
    for candidate in plan.candidates:
        parent = candidate.path.parent
        if (_CACHE_SHARD.fullmatch(parent.name)
                and parent.parent == plan.root):
            try:
                parent.rmdir()
            except OSError:
                pass
    return {
        "action": plan.action,
        "mode": "applied",
        "deleted_count": len(plan.candidates),
        "deleted_bytes": plan.total_bytes,
    }


def _validate_quarantine_receipt(operation_root: Path) -> dict:
    receipt = _read_json_object(
        operation_root / QUARANTINE_RECEIPT_NAME)
    if (receipt.get("schema_version") != QUARANTINE_SCHEMA_VERSION
            or receipt.get("kind") != "retention_quarantine"
            or receipt.get("operation_id") != operation_root.name
            or not _TOKEN.fullmatch(operation_root.name)
            or receipt.get("state") not in {
                "staging", "staged", "rollback_failed"}
            or not isinstance(receipt.get("entries"), list)):
        raise RetentionError("quarantine receipt failed validation")
    _finite_timestamp(receipt.get("created_at"), label="created_at")
    return receipt


def plan_quarantine_purge(
        root: Path, *, older_than_days: float,
        now: float | None = None) -> RetentionPlan:
    root = _absolute(root)
    age = _normalize_age_days(older_than_days)
    current_time = time.time() if now is None else float(now)
    cutoff = current_time - age * 86400.0
    quarantine_root = root / QUARANTINE_DIRECTORY_NAME
    candidates = []
    if quarantine_root.exists():
        assert_no_link_components(quarantine_root)
        for operation_root in sorted(
                quarantine_root.iterdir(), key=lambda path: path.name):
            if (not _TOKEN.fullmatch(operation_root.name)
                    or path_is_link_like(operation_root)
                    or not operation_root.is_dir()):
                continue
            receipt = _validate_quarantine_receipt(operation_root)
            created_at = _finite_timestamp(
                receipt["created_at"], label="created_at")
            if created_at > cutoff:
                continue
            candidates.append(_candidate(
                category="quarantine", path=operation_root,
                root=root, timestamp=created_at, now=current_time,
                ownership_token=operation_root.name))
    return RetentionPlan(
        action="purge_quarantine", root=root,
        candidates=tuple(candidates), created_at=current_time,
        context={"older_than_days": age},
    )


def apply_quarantine_purge(plan: RetentionPlan) -> dict[str, Any]:
    if plan.action != "purge_quarantine":
        raise ValueError("not a quarantine purge plan")
    for candidate in plan.candidates:
        _validate_candidate(candidate)
        receipt = _validate_quarantine_receipt(candidate.path)
        if receipt["operation_id"] != candidate.ownership_token:
            raise RetentionError("quarantine ownership changed after planning")
    for candidate in plan.candidates:
        _validate_candidate(candidate)
        receipt = _validate_quarantine_receipt(candidate.path)
        if receipt["operation_id"] != candidate.ownership_token:
            raise RetentionError("quarantine ownership changed before deletion")
        _delete_owned_path(candidate.path)
    return {
        "action": plan.action,
        "mode": "applied",
        "deleted_count": len(plan.candidates),
        "deleted_bytes": plan.total_bytes,
    }
