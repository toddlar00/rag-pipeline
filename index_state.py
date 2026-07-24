"""Backend-neutral index manifest and compatibility policy.

This module deliberately depends only on the Python standard library.  The
``rag`` compatibility facade supplies mutable collaborators such as atomic
publication, logging, hashing, and its current manifest schema version.
"""

from __future__ import annotations

import hashlib
import json
import re
from collections.abc import Callable
from pathlib import Path


PathFn = Callable[..., Path]
AtomicJsonWriterFn = Callable[[Path, object], None]
MarkerOwnershipFn = Callable[..., bool]
ManifestLoaderFn = Callable[..., dict | None]
ManifestMismatchFn = Callable[..., str | None]
WarningFn = Callable[..., None]
ArtifactHashFn = Callable[[Path], str]
BeginUpdateFn = Callable[..., Path]

_INDEX_UPDATE_MARKER_SCHEMA_VERSION = 1


def _index_manifest_path(db_dir: Path, *, backend: str,
                         collection_name: str) -> Path:
    """Return a collision-resistant path scoped to backend and collection."""
    if backend not in {"chroma", "qdrant"}:
        raise ValueError("backend must be 'chroma' or 'qdrant'")
    safe_name = re.sub(r"[^A-Za-z0-9_.-]+", "_", collection_name)
    safe_name = safe_name.strip("._")[:48] or "collection"
    digest = hashlib.sha256(collection_name.encode("utf-8")).hexdigest()[:12]
    return db_dir / f".rag-index-{backend}-{safe_name}-{digest}.json"


def _index_update_marker_path(
        db_dir: Path, *, backend: str, collection_name: str,
        manifest_path_fn: PathFn = _index_manifest_path) -> Path:
    """Return the collection-scoped marker for an unfinished index update."""
    manifest_path = manifest_path_fn(
        db_dir, backend=backend, collection_name=collection_name)
    return manifest_path.with_name(f"{manifest_path.stem}.updating.json")


def _qdrant_update_marker_path(
        qdrant_dir: Path, *, collection_name: str,
        marker_path_fn: PathFn = _index_update_marker_path) -> Path:
    """Return the collection-scoped marker for an unfinished Qdrant update."""
    return marker_path_fn(
        qdrant_dir, backend="qdrant", collection_name=collection_name)


def _chroma_update_marker_path(
        chroma_dir: Path, *, collection_name: str,
        marker_path_fn: PathFn = _index_update_marker_path) -> Path:
    """Return the collection-scoped marker for an unfinished Chroma update."""
    return marker_path_fn(
        chroma_dir, backend="chroma", collection_name=collection_name)


def _begin_index_update(
        db_dir: Path, *, backend: str, collection_name: str,
        source_sha256: str, source_record_count: int,
        manifest_schema_version: int,
        marker_path_fn: PathFn,
        atomic_write_json_fn: AtomicJsonWriterFn,
        owner_token: str | None = None,
        replace_existing: bool = False) -> Path:
    """Durably mark one backend collection dirty before physical mutation."""
    path = marker_path_fn(
        db_dir, backend=backend, collection_name=collection_name)
    if path.exists() and not replace_existing:
        return path
    payload = {
        "marker_schema_version": _INDEX_UPDATE_MARKER_SCHEMA_VERSION,
        "manifest_schema_version": manifest_schema_version,
        "backend": backend,
        "collection": collection_name,
        "target_source_sha256": source_sha256,
        "target_source_record_count": source_record_count,
    }
    if owner_token is not None:
        payload["owner_token"] = owner_token
    atomic_write_json_fn(path, payload)
    return path


def _index_update_marker_owned_by(
        path: Path, owner_token: str | None, *,
        manifest_schema_version: int,
        backend: str | None = None,
        collection_name: str | None = None) -> bool:
    """Return whether a marker carries this run's per-update ownership token."""
    if owner_token is None:
        return False
    try:
        payload = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, UnicodeError, json.JSONDecodeError):
        return False
    if not isinstance(payload, dict):
        return False
    if (payload.get("marker_schema_version") != (
            _INDEX_UPDATE_MARKER_SCHEMA_VERSION)
            or payload.get("manifest_schema_version") != (
                manifest_schema_version)
            or payload.get("owner_token") != owner_token
            or not isinstance(payload.get("target_source_sha256"), str)
            or isinstance(payload.get("target_source_record_count"), bool)
            or not isinstance(payload.get("target_source_record_count"), int)
            or payload["target_source_record_count"] < 0):
        return False
    if backend is not None and payload.get("backend") != backend:
        return False
    if (collection_name is not None
            and payload.get("collection") != collection_name):
        return False
    return True


def _begin_qdrant_index_update(
        qdrant_dir: Path, *, collection_name: str,
        source_sha256: str, source_record_count: int, owner_token: str,
        begin_index_update_fn: BeginUpdateFn,
        replace_existing: bool = False) -> Path:
    """Durably mark Qdrant dirty before its first physical mutation."""
    return begin_index_update_fn(
        qdrant_dir, backend="qdrant", collection_name=collection_name,
        source_sha256=source_sha256,
        source_record_count=source_record_count, owner_token=owner_token,
        replace_existing=replace_existing)


def _begin_chroma_index_update(
        chroma_dir: Path, *, collection_name: str,
        source_sha256: str, source_record_count: int, owner_token: str,
        begin_index_update_fn: BeginUpdateFn,
        replace_existing: bool = False) -> Path:
    """Durably mark Chroma dirty before its first physical mutation."""
    return begin_index_update_fn(
        chroma_dir, backend="chroma", collection_name=collection_name,
        source_sha256=source_sha256,
        source_record_count=source_record_count, owner_token=owner_token,
        replace_existing=replace_existing)


def _finish_index_update(
        marker_path: Path, *, owner_token: str,
        marker_owned_by_fn: MarkerOwnershipFn,
        backend: str | None = None,
        collection_name: str | None = None) -> None:
    """Mark an update clean after its verified manifest has been committed."""
    if not marker_owned_by_fn(
            marker_path, owner_token, backend=backend,
            collection_name=collection_name):
        raise RuntimeError(
            f"Index update marker ownership changed before commit: "
            f"{marker_path}. The collection remains dirty and must be rebuilt."
        )
    marker_path.unlink()


def _load_index_manifest(
        db_dir: Path, *, backend: str, collection_name: str,
        manifest_path_fn: PathFn,
        warning_fn: WarningFn) -> dict | None:
    """Load a collection-scoped manifest, returning ``None`` if unusable."""
    path = manifest_path_fn(
        db_dir, backend=backend, collection_name=collection_name)
    if not path.is_file():
        return None
    try:
        payload = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError) as exc:
        warning_fn(
            "Index manifest is unreadable (%s); collection will be "
            "rebuilt safely: %s", path, exc)
        return None
    if not isinstance(payload, dict):
        warning_fn(
            "Index manifest is not a JSON object (%s); collection "
            "will be rebuilt safely", path)
        return None
    return payload


def _index_manifest_mismatch(
        manifest: dict, *, backend: str, collection_name: str,
        embedding_model: str, embedding_dimension: int,
        model_artifact_lock_sha256: str,
        manifest_schema_version: int,
        quality_report_schema_version: int) -> str | None:
    """Return why *manifest* is incompatible, or ``None`` when safe to use."""
    expected = {
        "schema_version": manifest_schema_version,
        "backend": backend,
        "collection": collection_name,
        "embedding_model": embedding_model,
        "embedding_dimension": embedding_dimension,
        "model_artifact_lock_sha256": model_artifact_lock_sha256,
    }
    for key, value in expected.items():
        if manifest.get(key) != value:
            return f"{key} changed ({manifest.get(key)!r} -> {value!r})"

    quality_mismatch = _quality_report_binding_mismatch(
        manifest, quality_report_schema_version=quality_report_schema_version)
    if quality_mismatch:
        return quality_mismatch

    table_child_count = manifest.get("table_child_count")
    source_record_count = manifest.get("source_record_count")
    if (isinstance(table_child_count, bool)
            or not isinstance(table_child_count, int)
            or table_child_count < 0
            or (table_child_count > 0
                and (isinstance(source_record_count, bool)
                     or not isinstance(source_record_count, int)))
            or (isinstance(source_record_count, int)
                and not isinstance(source_record_count, bool)
                and table_child_count > source_record_count)):
        return "table_child_count is missing or invalid"
    if (table_child_count > 0
            and manifest.get("quality_report_schema_version") is None):
        return "table children require a quality report binding"

    hashes = manifest.get("chunk_hashes")
    if (not isinstance(hashes, dict)
            or not all(isinstance(key, str) and isinstance(value, str)
                       for key, value in hashes.items())):
        return "chunk_hashes is missing or invalid"
    return None


def _quality_report_binding_mismatch(
        manifest: dict, *, quality_report_schema_version: int,
) -> str | None:
    """Validate the all-null or exact schema/SHA quality binding pair."""
    schema_key = "quality_report_schema_version"
    sha_key = "quality_report_sha256"
    if schema_key not in manifest or sha_key not in manifest:
        return "quality report binding is missing"
    schema = manifest[schema_key]
    report_sha256 = manifest[sha_key]
    if schema is None and report_sha256 is None:
        return None
    if (isinstance(schema, bool) or not isinstance(schema, int)
            or schema != quality_report_schema_version):
        return "quality report schema is missing, invalid, or unsupported"
    if (not isinstance(report_sha256, str)
            or re.fullmatch(r"[0-9a-f]{64}", report_sha256) is None):
        return "quality report SHA-256 is missing or invalid"
    return None


def _resolve_incremental_index_state(
        db_dir: Path, *, backend: str, collection_name: str,
        embedding_model: str, embedding_dimension: int,
        collection_exists: bool, full_reindex: bool,
        marker_path_fn: PathFn,
        marker_owned_by_fn: MarkerOwnershipFn,
        load_manifest_fn: ManifestLoaderFn,
        manifest_mismatch_fn: ManifestMismatchFn,
        active_update_token: str | None = None,
) -> tuple[dict[str, str], bool, str]:
    """Resolve hashes and whether the named collection must be rebuilt.

    A legacy database-wide sidecar is deliberately never trusted for a skip:
    it cannot prove which collection or embedding model produced the vectors.
    Rebuilding only the requested collection safely migrates it to the new
    manifest without touching sibling collections or deleting the legacy file.
    """
    marker_path = marker_path_fn(
        db_dir, backend=backend, collection_name=collection_name)
    if (marker_path.exists()
            and not marker_owned_by_fn(
                marker_path, active_update_token, backend=backend,
                collection_name=collection_name)):
        return {}, collection_exists, "previous index update did not complete"
    if full_reindex:
        return {}, collection_exists, "full reindex requested"
    if not collection_exists:
        return {}, False, "collection does not exist"

    manifest = load_manifest_fn(
        db_dir, backend=backend, collection_name=collection_name)
    if manifest is None:
        legacy_path = db_dir / "chunk_hashes.json"
        reason = ("legacy chunk_hashes.json lacks collection/model metadata"
                  if legacy_path.is_file() else
                  "collection has no compatible index manifest")
        return {}, True, reason

    mismatch = manifest_mismatch_fn(
        manifest, backend=backend, collection_name=collection_name,
        embedding_model=embedding_model,
        embedding_dimension=embedding_dimension)
    if mismatch:
        return {}, True, mismatch
    return dict(manifest["chunk_hashes"]), False, "manifest compatible"


def _save_index_manifest(
        db_dir: Path, *, backend: str, collection_name: str,
        embedding_model: str, embedding_dimension: int,
        model_artifact_lock_sha256: str,
        chunk_hashes: dict[str, str], manifest_schema_version: int,
        quality_report_policy_schema_version: int,
        manifest_path_fn: PathFn,
        atomic_write_json_fn: AtomicJsonWriterFn,
        source_sha256: str | None = None,
        source_record_count: int | None = None,
        quality_report_schema_version: int | None = None,
        quality_report_sha256: str | None = None,
        table_child_count: int = 0) -> Path:
    """Atomically persist versioned incremental state for one collection."""
    path = manifest_path_fn(
        db_dir, backend=backend, collection_name=collection_name)
    quality_binding = {
        "quality_report_schema_version": quality_report_schema_version,
        "quality_report_sha256": quality_report_sha256,
    }
    quality_mismatch = _quality_report_binding_mismatch(
        quality_binding,
        quality_report_schema_version=quality_report_policy_schema_version)
    if quality_mismatch:
        raise ValueError(f"Invalid index quality binding: {quality_mismatch}")
    if (isinstance(table_child_count, bool)
            or not isinstance(table_child_count, int)
            or table_child_count < 0
            or (table_child_count > 0 and source_record_count is None)
            or (source_record_count is not None
                and table_child_count > source_record_count)):
        raise ValueError("Invalid index table_child_count")
    if table_child_count > 0 and quality_report_schema_version is None:
        raise ValueError(
            "Index table children require a quality report binding")
    payload = {
        "schema_version": manifest_schema_version,
        "backend": backend,
        "collection": collection_name,
        "embedding_model": embedding_model,
        "embedding_dimension": embedding_dimension,
        "model_artifact_lock_sha256": model_artifact_lock_sha256,
        "chunk_hashes": chunk_hashes,
        "source_sha256": source_sha256,
        "source_record_count": source_record_count,
        "table_child_count": table_child_count,
        **quality_binding,
    }
    atomic_write_json_fn(path, payload)
    return path


def _query_manifest_dimension_impl(
        db_dir: Path, *, backend: str, collection_name: str,
        embedding_model: str, model_artifact_lock_sha256: str,
        manifest_schema_version: int,
        quality_report_schema_version: int,
        marker_path_fn: PathFn,
        manifest_path_fn: PathFn,
        load_manifest_fn: ManifestLoaderFn,
        compatible_schema_bindings: tuple[tuple[int, int], ...] = (),
) -> int | None:
    """Validate query/index compatibility and return the indexed dimension.

    Legacy collections without a manifest remain queryable. Once a manifest
    exists, however, querying with a different model or stale schema is refused
    rather than silently comparing vectors from incompatible embedding spaces.
    """
    marker_path = marker_path_fn(
        db_dir, backend=backend, collection_name=collection_name)
    if marker_path.exists():
        raise ValueError(
            f"Index update is incomplete for {backend.title()} collection "
            f"'{collection_name}': {marker_path}. Re-run indexing to "
            "rebuild the collection before querying."
        )
    manifest_path = manifest_path_fn(
        db_dir, backend=backend, collection_name=collection_name)
    manifest = load_manifest_fn(
        db_dir, backend=backend, collection_name=collection_name)
    if manifest is None:
        if manifest_path.exists():
            raise ValueError(
                f"Index manifest is unreadable: {manifest_path}. "
                "Re-run indexing for this collection."
            )
        return None

    manifest_version = manifest.get("schema_version")
    quality_policy_by_manifest = {
        manifest_schema_version: quality_report_schema_version,
        **dict(compatible_schema_bindings),
    }
    if manifest_version not in quality_policy_by_manifest:
        raise ValueError(
            "Query/index mismatch: manifest schema_version is "
            f"{manifest_version!r}, expected one of "
            f"{sorted(quality_policy_by_manifest)!r}. Re-run indexing or "
            "query with the indexed embedding model."
        )
    expected = {
        "backend": backend,
        "collection": collection_name,
        "embedding_model": embedding_model,
        "model_artifact_lock_sha256": model_artifact_lock_sha256,
    }
    for key, value in expected.items():
        if manifest.get(key) != value:
            raise ValueError(
                f"Query/index mismatch: manifest {key} is "
                f"{manifest.get(key)!r}, expected {value!r}. Re-run indexing "
                "or query with the indexed embedding model."
            )
    quality_mismatch = _quality_report_binding_mismatch(
        manifest,
        quality_report_schema_version=(
            quality_policy_by_manifest[manifest_version]))
    if quality_mismatch:
        raise ValueError(
            f"Index manifest has an invalid quality binding "
            f"({quality_mismatch}): {manifest_path}. Re-run indexing for "
            "this collection."
        )
    if manifest_version == manifest_schema_version:
        table_child_count = manifest.get("table_child_count")
        source_record_count = manifest.get("source_record_count")
        if (isinstance(table_child_count, bool)
                or not isinstance(table_child_count, int)
                or table_child_count < 0
                or (table_child_count > 0
                    and (isinstance(source_record_count, bool)
                         or not isinstance(source_record_count, int)))
                or (isinstance(source_record_count, int)
                    and not isinstance(source_record_count, bool)
                    and table_child_count > source_record_count)):
            raise ValueError(
                f"Index manifest has an invalid table child count: "
                f"{manifest_path}. Re-run indexing for this collection."
            )
        if (table_child_count > 0
                and manifest.get("quality_report_schema_version") is None):
            raise ValueError(
                f"Index manifest has table children without a quality report "
                f"binding: {manifest_path}. Re-run indexing for this "
                "collection."
            )
    dimension = manifest.get("embedding_dimension")
    if (isinstance(dimension, bool) or not isinstance(dimension, int)
            or dimension < 1):
        raise ValueError(
            f"Index manifest has an invalid embedding dimension: "
            f"{manifest_path}. Re-run indexing for this collection."
        )
    return dimension


def _require_hybrid_chunks_snapshot(
        chunks_path: Path, db_dir: Path, *, backend: str,
        collection_name: str,
        load_manifest_fn: ManifestLoaderFn,
        artifact_sha256_fn: ArtifactHashFn,
        quality_report_path_fn: Callable[[Path], Path]) -> str | None:
    """Validate and return the manifested lexical corpus digest, if proven."""
    manifest = load_manifest_fn(
        db_dir, backend=backend, collection_name=collection_name)
    if manifest is None:
        return None
    expected_sha256 = manifest.get("source_sha256")
    if not isinstance(expected_sha256, str) or not expected_sha256:
        return None
    actual_sha256 = artifact_sha256_fn(chunks_path)
    if actual_sha256 != expected_sha256:
        raise ValueError(
            f"Hybrid chunks artifact does not match the indexed corpus for "
            f"{backend.title()} collection '{collection_name}': "
            f"{chunks_path}. Re-run indexing or select the chunks file used "
            "to build this collection."
        )
    expected_report_sha256 = manifest.get("quality_report_sha256")
    if expected_report_sha256 is not None:
        report_path = quality_report_path_fn(chunks_path)
        try:
            actual_report_sha256 = artifact_sha256_fn(report_path)
        except OSError as exc:
            raise ValueError(
                f"Hybrid quality report is missing for {chunks_path}"
            ) from exc
        if actual_report_sha256 != expected_report_sha256:
            raise ValueError(
                f"Hybrid quality report does not match the indexed corpus "
                f"for {backend.title()} collection '{collection_name}': "
                f"{report_path}. Re-run indexing or restore the report used "
                "to build this collection."
            )
    return expected_sha256


def _validate_query_vector_dimension(
        vector: list[float], expected_dimension: int | None,
        embedding_model: str) -> None:
    """Refuse a query vector that cannot belong to the manifested index."""
    if expected_dimension is not None and len(vector) != expected_dimension:
        raise ValueError(
            f"Embedding model '{embedding_model}' returned a {len(vector)}-"
            f"dimension query vector, but the index manifest requires "
            f"{expected_dimension}. Re-run indexing before querying."
        )
