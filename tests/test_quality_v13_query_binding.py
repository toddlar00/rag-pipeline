"""Index manifest 10 binds quality 13; manifest 9 keeps its query behavior.

Quality schema 13 changed only source eligibility (the typed
``decorative_glyph`` exclusion).  Manifest 10 has the same payload as
manifest 9, so a manifest-9 index bound to a quality-12 report keeps every
query check it had when it was current, while indexing, evaluation and
publication treat it as stale.
"""

from pathlib import Path

import pytest

import rag


def _write_manifest(db_dir: Path, *, schema_version: int,
                    backend: str = "chroma",
                    quality_schema: int | None = 12,
                    table_child_count: int = 0,
                    source_record_count: int = 3,
                    embedding_input_policy_version: int | None = None,
                    ) -> dict:
    payload = {
        "schema_version": schema_version,
        "backend": backend,
        "collection": "cases",
        "embedding_model": "model",
        "embedding_dimension": 5,
        "embedding_input_policy_version": (
            rag.EMBEDDING_INPUT_POLICY_VERSION
            if embedding_input_policy_version is None
            else embedding_input_policy_version),
        "model_artifact_lock_sha256": rag._model_artifact_lock_sha256(),
        "chunk_hashes": {},
        "source_sha256": "b" * 64,
        "source_record_count": source_record_count,
        "table_child_count": table_child_count,
        "quality_report_schema_version": quality_schema,
        "quality_report_sha256": (
            None if quality_schema is None else "a" * 64),
    }
    rag._atomic_write_json(
        rag._index_manifest_path(
            db_dir, backend=backend, collection_name="cases"),
        payload)
    return payload


def _query_dimension(db_dir: Path, *, backend: str = "chroma",
                     allow_legacy: bool = True) -> int | None:
    return rag._query_manifest_dimension_impl(
        db_dir, backend=backend, collection_name="cases",
        embedding_model="model", allow_legacy=allow_legacy)


def test_schema_versions_and_query_bindings_mark_quality_v13():
    assert rag.INDEX_MANIFEST_SCHEMA_VERSION == 10
    assert rag._quality_core.QUALITY_REPORT_SCHEMA_VERSION == 13
    assert rag._LEGACY_QUERY_SCHEMA_BINDINGS == ((6, 2), (7, 3), (9, 12))
    assert rag._CONTEXT_QUERY_SCHEMA_BINDINGS == ((7, 3), (9, 12))


@pytest.mark.parametrize("backend", ["chroma", "qdrant"])
@pytest.mark.parametrize("allow_legacy", [True, False])
@pytest.mark.parametrize("quality_schema", [12, None])
def test_manifest_v9_index_still_answers_queries(
        tmp_path, backend, allow_legacy, quality_schema):
    _write_manifest(tmp_path, schema_version=9, backend=backend,
                    quality_schema=quality_schema)

    assert _query_dimension(
        tmp_path, backend=backend, allow_legacy=allow_legacy) == 5


@pytest.mark.parametrize("allow_legacy", [True, False])
@pytest.mark.parametrize("overrides, message", [
    ({"embedding_input_policy_version": 2}, "embedding_input_policy_version"),
    ({"table_child_count": 4}, "table child count"),
    ({"table_child_count": 1, "quality_schema": None},
     "table children without a quality report"),
    ({"quality_schema": 13}, "quality binding"),
])
def test_manifest_v9_keeps_every_check_it_had_as_current(
        tmp_path, overrides, message, allow_legacy):
    """Ordinary and context-window queries keep the same refusals."""
    _write_manifest(tmp_path, schema_version=9, **overrides)

    with pytest.raises(ValueError, match=message):
        _query_dimension(tmp_path, allow_legacy=allow_legacy)


@pytest.mark.parametrize("allow_legacy", [True, False])
def test_manifest_v9_table_children_still_widen_and_collapse_search(
        tmp_path, allow_legacy):
    _write_manifest(tmp_path, schema_version=9, table_child_count=2)

    assert _query_dimension(tmp_path, allow_legacy=allow_legacy) == 5
    assert rag._indexed_table_child_count(
        tmp_path, backend="chroma", collection_name="cases") == 2


def test_manifests_without_current_payload_have_no_table_children(tmp_path):
    _write_manifest(tmp_path, schema_version=8, table_child_count=2)

    assert rag._indexed_table_child_count(
        tmp_path, backend="chroma", collection_name="cases") == 0


def test_manifest_v10_requires_the_quality_v13_binding(tmp_path):
    _write_manifest(tmp_path, schema_version=10, quality_schema=12)

    with pytest.raises(ValueError, match="quality binding"):
        _query_dimension(tmp_path)

    _write_manifest(tmp_path, schema_version=10, quality_schema=13)
    assert _query_dimension(tmp_path, allow_legacy=False) == 5


def test_new_manifests_are_v10_and_bind_only_quality_v13(tmp_path):
    manifest_path = rag._save_index_manifest(
        tmp_path, backend="chroma", collection_name="cases",
        embedding_model="model", embedding_dimension=5,
        chunk_hashes={}, source_sha256="b" * 64, source_record_count=1,
        quality_report_schema_version=13, quality_report_sha256="a" * 64)

    assert rag._load_index_manifest(
        tmp_path, backend="chroma", collection_name="cases")[
            "schema_version"] == 10
    assert manifest_path.is_file()
    with pytest.raises(ValueError, match="quality binding"):
        rag._save_index_manifest(
            tmp_path, backend="chroma", collection_name="cases",
            embedding_model="model", embedding_dimension=5,
            chunk_hashes={}, source_sha256="b" * 64,
            source_record_count=1, quality_report_schema_version=12,
            quality_report_sha256="a" * 64)


def test_manifest_v9_index_is_rebuilt_rather_than_reused(tmp_path):
    manifest = _write_manifest(tmp_path, schema_version=9)

    assert rag._index_manifest_mismatch(
        manifest, backend="chroma", collection_name="cases",
        embedding_model="model", embedding_dimension=5) == (
            "schema_version changed (9 -> 10)")
    hashes, rebuild, reason = rag._resolve_incremental_index_state(
        tmp_path, backend="chroma", collection_name="cases",
        embedding_model="model", embedding_dimension=5,
        collection_exists=True, full_reindex=False)
    assert (hashes, rebuild, reason) == (
        {}, True, "schema_version changed (9 -> 10)")


@pytest.mark.parametrize("schema_version", [8, 11])
def test_unbound_manifest_versions_are_still_refused(
        tmp_path, schema_version):
    _write_manifest(tmp_path, schema_version=schema_version)

    with pytest.raises(ValueError, match="manifest schema_version"):
        _query_dimension(tmp_path)
