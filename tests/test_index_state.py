import json
import subprocess
import sys

import pytest

import index_state
import rag


def test_index_state_is_a_lightweight_leaf_module():
    result = subprocess.run(
        [
            sys.executable,
            "-c",
            (
                "import sys; import index_state; "
                "forbidden = {'rag', 'artifact_io', 'chunking_core', "
                "'retrieval_core', 'llm_runtime', 'requests', 'docling', "
                "'chromadb', 'qdrant_client'}; "
                "loaded = forbidden.intersection(sys.modules); "
                "assert not loaded, sorted(loaded)"
            ),
        ],
        capture_output=True,
        check=False,
        text=True,
        timeout=30,
    )

    assert result.returncode == 0, result.stderr


def test_rag_reexports_standalone_index_state_helpers():
    assert rag._index_manifest_path is index_state._index_manifest_path
    assert rag._validate_query_vector_dimension is (
        index_state._validate_query_vector_dimension)


def test_marker_path_uses_current_rag_manifest_path(monkeypatch, tmp_path):
    observed = {}
    sentinel = tmp_path / "patched-manifest.json"

    def fake_manifest_path(db_dir, *, backend, collection_name):
        observed["args"] = (db_dir, backend, collection_name)
        return sentinel

    monkeypatch.setattr(rag, "_index_manifest_path", fake_manifest_path)

    result = rag._index_update_marker_path(
        tmp_path, backend="qdrant", collection_name="cases")

    assert observed["args"] == (tmp_path, "qdrant", "cases")
    assert result == tmp_path / "patched-manifest.updating.json"


def test_begin_update_uses_current_rag_policy_and_writer(monkeypatch, tmp_path):
    marker = tmp_path / "patched-marker.json"
    observed = {}

    def fake_marker_path(db_dir, *, backend, collection_name):
        observed["marker_args"] = (db_dir, backend, collection_name)
        return marker

    def fake_writer(path, payload):
        observed["write"] = (path, payload)

    monkeypatch.setattr(rag, "INDEX_MANIFEST_SCHEMA_VERSION", 73)
    monkeypatch.setattr(rag, "_index_update_marker_path", fake_marker_path)
    monkeypatch.setattr(rag, "_atomic_write_json", fake_writer)

    result = rag._begin_index_update(
        tmp_path, backend="chroma", collection_name="cases",
        source_sha256="source", source_record_count=4,
        owner_token="owner")

    assert result == marker
    assert observed["marker_args"] == (tmp_path, "chroma", "cases")
    path, payload = observed["write"]
    assert path == marker
    assert payload == {
        "marker_schema_version": 1,
        "manifest_schema_version": 73,
        "backend": "chroma",
        "collection": "cases",
        "target_source_sha256": "source",
        "target_source_record_count": 4,
        "owner_token": "owner",
    }


@pytest.mark.parametrize(
    ("facade_name", "directory_name", "expected_backend"),
    [
        ("_begin_qdrant_index_update", "qdrant", "qdrant"),
        ("_begin_chroma_index_update", "chroma", "chroma"),
    ],
)
def test_backend_begin_uses_current_generic_begin(
        monkeypatch, tmp_path, facade_name, directory_name, expected_backend):
    observed = {}
    sentinel = tmp_path / "sentinel.json"

    def fake_begin(db_dir, **kwargs):
        observed["args"] = (db_dir, kwargs)
        return sentinel

    monkeypatch.setattr(rag, "_begin_index_update", fake_begin)

    result = getattr(rag, facade_name)(
        tmp_path / directory_name, collection_name="cases",
        source_sha256="source", source_record_count=2,
        owner_token="owner", replace_existing=True)

    assert result == sentinel
    db_dir, kwargs = observed["args"]
    assert db_dir == tmp_path / directory_name
    assert kwargs == {
        "backend": expected_backend,
        "collection_name": "cases",
        "source_sha256": "source",
        "source_record_count": 2,
        "owner_token": "owner",
        "replace_existing": True,
    }


def test_finish_update_uses_current_ownership_check(monkeypatch, tmp_path):
    marker = tmp_path / "marker.json"
    marker.write_text("{}", encoding="utf-8")
    observed = {}

    def fake_owned(path, owner_token, **kwargs):
        observed["args"] = (path, owner_token, kwargs)
        return True

    monkeypatch.setattr(rag, "_index_update_marker_owned_by", fake_owned)

    rag._finish_index_update(
        marker, owner_token="owner", backend="qdrant",
        collection_name="cases")

    assert observed["args"] == (
        marker, "owner",
        {"backend": "qdrant", "collection_name": "cases"},
    )
    assert not marker.exists()


def test_load_manifest_uses_current_path_and_warning(monkeypatch, tmp_path):
    manifest_path = tmp_path / "patched.json"
    manifest_path.write_text("not-json", encoding="utf-8")
    warnings = []

    monkeypatch.setattr(
        rag, "_index_manifest_path", lambda *args, **kwargs: manifest_path)
    monkeypatch.setattr(rag.log, "warning", lambda *args: warnings.append(args))

    assert rag._load_index_manifest(
        tmp_path, backend="chroma", collection_name="cases") is None
    assert warnings
    assert warnings[0][1] == manifest_path


def test_load_manifest_rebuilds_safely_on_undecodable_bytes(
        monkeypatch, tmp_path):
    """A torn or partially synced manifest must trigger the safe rebuild."""
    manifest_path = tmp_path / "torn.json"
    manifest_path.write_bytes(b'{"schema_version": 8, "name": "\xff\xfe"}')
    warnings = []

    monkeypatch.setattr(
        rag, "_index_manifest_path", lambda *args, **kwargs: manifest_path)
    monkeypatch.setattr(rag.log, "warning", lambda *args: warnings.append(args))

    assert rag._load_index_manifest(
        tmp_path, backend="chroma", collection_name="cases") is None
    assert warnings
    assert warnings[0][1] == manifest_path


def test_resolver_uses_current_rag_collaborators(monkeypatch, tmp_path):
    marker = tmp_path / "marker.json"
    marker.write_text("{}", encoding="utf-8")
    calls = []
    manifest = {"chunk_hashes": {"stable": "hash"}}

    def fake_marker_path(*args, **kwargs):
        calls.append(("path", args, kwargs))
        return marker

    def fake_owned(*args, **kwargs):
        calls.append(("owned", args, kwargs))
        return True

    def fake_load(*args, **kwargs):
        calls.append(("load", args, kwargs))
        return manifest

    def fake_mismatch(*args, **kwargs):
        calls.append(("mismatch", args, kwargs))
        return None

    monkeypatch.setattr(rag, "_index_update_marker_path", fake_marker_path)
    monkeypatch.setattr(rag, "_index_update_marker_owned_by", fake_owned)
    monkeypatch.setattr(rag, "_load_index_manifest", fake_load)
    monkeypatch.setattr(rag, "_index_manifest_mismatch", fake_mismatch)

    result = rag._resolve_incremental_index_state(
        tmp_path, backend="qdrant", collection_name="cases",
        embedding_model="model", embedding_dimension=3,
        collection_exists=True, full_reindex=False,
        active_update_token="owner")

    assert result == ({"stable": "hash"}, False, "manifest compatible")
    assert [call[0] for call in calls] == [
        "path", "owned", "load", "mismatch"]


def test_save_manifest_uses_current_rag_path_writer_and_schema(
        monkeypatch, tmp_path):
    manifest_path = tmp_path / "manifest.json"
    observed = {}

    monkeypatch.setattr(rag, "INDEX_MANIFEST_SCHEMA_VERSION", 81)
    monkeypatch.setattr(
        rag, "_index_manifest_path", lambda *args, **kwargs: manifest_path)
    monkeypatch.setattr(
        rag, "_atomic_write_json",
        lambda path, payload: observed.update(path=path, payload=payload))

    result = rag._save_index_manifest(
        tmp_path, backend="qdrant", collection_name="cases",
        embedding_model="model", embedding_dimension=3,
        chunk_hashes={"stable": "hash"}, source_sha256="source",
        source_record_count=1)

    assert result == manifest_path
    assert observed["path"] == manifest_path
    assert observed["payload"]["schema_version"] == 81
    assert observed["payload"]["embedding_input_policy_version"] == (
        rag.EMBEDDING_INPUT_POLICY_VERSION)
    assert observed["payload"]["chunk_hashes"] == {"stable": "hash"}


def test_query_manifest_impl_uses_current_rag_collaborators(
        monkeypatch, tmp_path):
    marker = tmp_path / "missing-marker.json"
    manifest_path = tmp_path / "manifest.json"
    manifest = {
        "schema_version": 91,
        "backend": "chroma",
        "collection": "cases",
        "embedding_model": "model",
        "embedding_dimension": 5,
        "embedding_input_policy_version": (
            rag.EMBEDDING_INPUT_POLICY_VERSION),
        "model_artifact_lock_sha256": rag._model_artifact_lock_sha256(),
        "quality_report_schema_version": None,
        "quality_report_sha256": None,
        "table_child_count": 0,
    }

    monkeypatch.setattr(rag, "INDEX_MANIFEST_SCHEMA_VERSION", 91)
    monkeypatch.setattr(
        rag, "_index_update_marker_path", lambda *args, **kwargs: marker)
    monkeypatch.setattr(
        rag, "_index_manifest_path", lambda *args, **kwargs: manifest_path)
    monkeypatch.setattr(
        rag, "_load_index_manifest", lambda *args, **kwargs: manifest)

    assert rag._query_manifest_dimension_impl(
        tmp_path, backend="chroma", collection_name="cases",
        embedding_model="model") == 5


def test_hybrid_snapshot_uses_current_manifest_and_hash_hooks(
        monkeypatch, tmp_path):
    chunks_path = tmp_path / "chunks.jsonl"
    calls = []

    def fake_load(*args, **kwargs):
        calls.append(("load", args, kwargs))
        return {"source_sha256": "expected"}

    def fake_hash(path):
        calls.append(("hash", path))
        return "expected"

    monkeypatch.setattr(rag, "_load_index_manifest", fake_load)
    monkeypatch.setattr(rag, "_cached_artifact_sha256", fake_hash)

    assert rag._require_hybrid_chunks_snapshot(
        chunks_path, tmp_path, backend="qdrant",
        collection_name="cases") == "expected"
    assert calls[-1] == ("hash", chunks_path)


def test_manifest_persists_and_validates_quality_report_binding(tmp_path):
    report_sha256 = "a" * 64
    manifest_path = rag._save_index_manifest(
        tmp_path, backend="chroma", collection_name="cases",
        embedding_model="model", embedding_dimension=5,
        chunk_hashes={"stable": "hash"}, source_sha256="b" * 64,
        source_record_count=1,
        quality_report_schema_version=(
            rag._quality_core.QUALITY_REPORT_SCHEMA_VERSION),
        quality_report_sha256=report_sha256)
    manifest = json.loads(manifest_path.read_text(encoding="utf-8"))

    assert manifest["quality_report_schema_version"] == (
        rag._quality_core.QUALITY_REPORT_SCHEMA_VERSION)
    assert manifest["quality_report_sha256"] == report_sha256
    assert manifest["embedding_input_policy_version"] == (
        rag.EMBEDDING_INPUT_POLICY_VERSION)
    assert rag._index_manifest_mismatch(
        manifest, backend="chroma", collection_name="cases",
        embedding_model="model", embedding_dimension=5) is None
    assert rag._query_manifest_dimension_impl(
        tmp_path, backend="chroma", collection_name="cases",
        embedding_model="model") == 5


@pytest.mark.parametrize(("manifest_age", "policy_state", "mismatch_key"), [
    ("old", "current", "schema_version"),
    ("current", "missing", "embedding_input_policy_version"),
    ("current", "different", "embedding_input_policy_version"),
])
def test_manifest_reuse_rejects_old_or_stale_embedding_input_policy(
        tmp_path, manifest_age, policy_state, mismatch_key):
    manifest_path = rag._save_index_manifest(
        tmp_path, backend="chroma", collection_name="cases",
        embedding_model="model", embedding_dimension=5,
        chunk_hashes={"stable": "hash"}, source_sha256="b" * 64,
        source_record_count=1)
    manifest = json.loads(manifest_path.read_text(encoding="utf-8"))
    if manifest_age == "old":
        manifest["schema_version"] = rag.INDEX_MANIFEST_SCHEMA_VERSION - 1
    if policy_state == "missing":
        manifest.pop("embedding_input_policy_version")
    elif policy_state == "different":
        manifest["embedding_input_policy_version"] = (
            rag.EMBEDDING_INPUT_POLICY_VERSION + 1)
    rag._atomic_write_json(manifest_path, manifest)

    hashes, rebuild, reason = rag._resolve_incremental_index_state(
        tmp_path, backend="chroma", collection_name="cases",
        embedding_model="model", embedding_dimension=5,
        collection_exists=True, full_reindex=False)

    assert hashes == {}
    assert rebuild is True
    assert mismatch_key in reason
    with pytest.raises(ValueError, match=mismatch_key):
        rag._query_manifest_dimension_impl(
            tmp_path, backend="chroma", collection_name="cases",
            embedding_model="model", allow_legacy=False)


def test_schema_v6_queries_remain_compatible_only_without_context(tmp_path):
    manifest_path = rag._index_manifest_path(
        tmp_path, backend="chroma", collection_name="cases")
    rag._atomic_write_json(manifest_path, {
        "schema_version": 6,
        "backend": "chroma",
        "collection": "cases",
        "embedding_model": "model",
        "embedding_dimension": 5,
        "model_artifact_lock_sha256": rag._model_artifact_lock_sha256(),
        "chunk_hashes": {},
        "source_sha256": "b" * 64,
        "source_record_count": 1,
        "quality_report_schema_version": 2,
        "quality_report_sha256": "a" * 64,
    })

    assert rag._query_manifest_dimension_impl(
        tmp_path, backend="chroma", collection_name="cases",
        embedding_model="model", allow_legacy=True) == 5
    with pytest.raises(ValueError, match="schema_version"):
        rag._query_manifest_dimension_impl(
            tmp_path, backend="chroma", collection_name="cases",
            embedding_model="model", allow_legacy=False)


def test_schema_v7_context_indexes_remain_query_compatible(tmp_path):
    manifest_path = rag._index_manifest_path(
        tmp_path, backend="chroma", collection_name="cases")
    rag._atomic_write_json(manifest_path, {
        "schema_version": 7,
        "backend": "chroma",
        "collection": "cases",
        "embedding_model": "model",
        "embedding_dimension": 5,
        "model_artifact_lock_sha256": rag._model_artifact_lock_sha256(),
        "chunk_hashes": {},
        "source_sha256": "b" * 64,
        "source_record_count": 1,
        "quality_report_schema_version": 3,
        "quality_report_sha256": "a" * 64,
    })

    assert rag._query_manifest_dimension_impl(
        tmp_path, backend="chroma", collection_name="cases",
        embedding_model="model", allow_legacy=False) == 5


@pytest.mark.parametrize(("schema", "report_sha256"), [
    (None, "a" * 64),
    (1, None),
    (True, "a" * 64),
    (10, "a" * 64),
    (1, "short"),
    (1, "A" * 64),
])
def test_manifest_rejects_invalid_quality_report_binding(
        tmp_path, schema, report_sha256):
    with pytest.raises(ValueError, match="quality binding"):
        rag._save_index_manifest(
            tmp_path, backend="qdrant", collection_name="cases",
            embedding_model="model", embedding_dimension=3,
            chunk_hashes={}, quality_report_schema_version=schema,
            quality_report_sha256=report_sha256)


@pytest.mark.parametrize("count", [True, -1, 3])
def test_manifest_rejects_invalid_table_child_count(tmp_path, count):
    with pytest.raises(ValueError, match="table_child_count"):
        rag._save_index_manifest(
            tmp_path, backend="qdrant", collection_name="cases",
            embedding_model="model", embedding_dimension=3,
            chunk_hashes={}, source_record_count=2,
            table_child_count=count)


def test_table_children_require_a_current_quality_binding(tmp_path):
    with pytest.raises(ValueError, match="quality report binding"):
        rag._save_index_manifest(
            tmp_path, backend="chroma", collection_name="cases",
            embedding_model="model", embedding_dimension=5,
            chunk_hashes={"child": "hash"}, source_record_count=1,
            table_child_count=1)

    manifest = {
        "schema_version": rag.INDEX_MANIFEST_SCHEMA_VERSION,
        "backend": "chroma",
        "collection": "cases",
        "embedding_model": "model",
        "embedding_dimension": 5,
        "embedding_input_policy_version": (
            rag.EMBEDDING_INPUT_POLICY_VERSION),
        "model_artifact_lock_sha256": rag._model_artifact_lock_sha256(),
        "chunk_hashes": {"child": "hash"},
        "source_sha256": "b" * 64,
        "source_record_count": 1,
        "table_child_count": 1,
        "quality_report_schema_version": None,
        "quality_report_sha256": None,
    }
    assert "quality report binding" in rag._index_manifest_mismatch(
        manifest, backend="chroma", collection_name="cases",
        embedding_model="model", embedding_dimension=5)
    rag._atomic_write_json(
        rag._index_manifest_path(
            tmp_path, backend="chroma", collection_name="cases"),
        manifest,
    )
    with pytest.raises(ValueError, match="quality report binding"):
        rag._query_manifest_dimension_impl(
            tmp_path, backend="chroma", collection_name="cases",
            embedding_model="model")


def test_hybrid_snapshot_binds_adjacent_quality_report(tmp_path):
    chunks_path = tmp_path / "book_chunks.jsonl"
    report_path = rag._quality_core.quality_report_path(chunks_path)
    chunks_path.write_bytes(b'{"text":"source"}\n')
    report_path.write_bytes(b'{"status":"pass"}')
    rag._save_index_manifest(
        tmp_path, backend="chroma", collection_name="cases",
        embedding_model="model", embedding_dimension=3,
        chunk_hashes={},
        source_sha256=rag._cached_artifact_sha256(chunks_path),
        source_record_count=1,
        quality_report_schema_version=(
            rag._quality_core.QUALITY_REPORT_SCHEMA_VERSION),
        quality_report_sha256=rag._cached_artifact_sha256(report_path))

    assert rag._require_hybrid_chunks_snapshot(
        chunks_path, tmp_path, backend="chroma",
        collection_name="cases") == rag._cached_artifact_sha256(chunks_path)

    report_path.write_bytes(b'{"status":"tampered"}')
    with pytest.raises(ValueError, match="quality report does not match"):
        rag._require_hybrid_chunks_snapshot(
            chunks_path, tmp_path, backend="chroma",
            collection_name="cases")


def test_marker_ownership_uses_current_manifest_schema(monkeypatch, tmp_path):
    marker = tmp_path / "marker.json"
    marker.write_text(json.dumps({
        "marker_schema_version": 1,
        "manifest_schema_version": 101,
        "backend": "qdrant",
        "collection": "cases",
        "target_source_sha256": "source",
        "target_source_record_count": 1,
        "owner_token": "owner",
    }), encoding="utf-8")
    monkeypatch.setattr(rag, "INDEX_MANIFEST_SCHEMA_VERSION", 101)

    assert rag._index_update_marker_owned_by(
        marker, "owner", backend="qdrant", collection_name="cases")
