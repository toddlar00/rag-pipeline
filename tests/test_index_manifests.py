import hashlib
import json
import queue
import sys
from concurrent.futures import Future
from pathlib import Path
from types import ModuleType, SimpleNamespace

import pytest

import rag


def _write_chunks(path: Path) -> None:
    record = {
        "text": "The court applies the governing rule.",
        "metadata": {"chunk_index": 0, "context": "Legal doctrine"},
    }
    path.write_text(json.dumps(record) + "\n", encoding="utf-8")


def _fake_embeddings(calls, dimensions):
    def embed(texts, model_name, *, input_type="document"):
        calls.append((list(texts), model_name, input_type))
        dimension = dimensions[model_name]
        return [[float(index) for index in range(dimension)] for _ in texts]

    return embed


def test_manifest_is_scoped_versioned_and_atomic(tmp_path):
    hashes = {"chunk_1": "abc"}

    chroma_path = rag._save_index_manifest(
        tmp_path, backend="chroma", collection_name="../../Civil Procedure",
        embedding_model="model-a", embedding_dimension=3,
        chunk_hashes=hashes)
    qdrant_path = rag._save_index_manifest(
        tmp_path, backend="qdrant", collection_name="../../Civil Procedure",
        embedding_model="model-a", embedding_dimension=3,
        chunk_hashes=hashes)

    assert chroma_path.parent == tmp_path
    assert qdrant_path.parent == tmp_path
    assert chroma_path != qdrant_path
    assert ".." not in chroma_path.name
    assert json.loads(chroma_path.read_text(encoding="utf-8")) == {
        "schema_version": rag.INDEX_MANIFEST_SCHEMA_VERSION,
        "backend": "chroma",
        "collection": "../../Civil Procedure",
        "embedding_model": "model-a",
        "embedding_dimension": 3,
        "chunk_hashes": hashes,
        "source_sha256": None,
        "source_record_count": None,
    }
    assert not list(tmp_path.glob("*.tmp"))

    with pytest.raises(ValueError, match="backend"):
        rag._index_manifest_path(
            tmp_path, backend="../outside", collection_name="book")


def test_atomic_json_failure_preserves_previous_file(monkeypatch, tmp_path):
    target = tmp_path / "manifest.json"
    target.write_text('{"old":true}', encoding="utf-8")

    def fail_replace(source, destination):
        raise OSError("simulated replace failure")

    monkeypatch.setattr(rag.os, "replace", fail_replace)

    with pytest.raises(OSError, match="replace failure"):
        rag._atomic_write_json(target, {"new": True})

    assert json.loads(target.read_text(encoding="utf-8")) == {"old": True}
    assert not list(tmp_path.glob("*.tmp"))


def test_index_update_marker_is_scoped_and_blocks_manifest_use(tmp_path):
    manifest_path = rag._save_index_manifest(
        tmp_path, backend="qdrant", collection_name="book",
        embedding_model="model-a", embedding_dimension=2,
        chunk_hashes={"chunk_a": "hash-a"})
    rag._save_index_manifest(
        tmp_path, backend="qdrant", collection_name="sibling",
        embedding_model="model-a", embedding_dimension=2,
        chunk_hashes={"chunk_b": "hash-b"})

    marker_path = rag._begin_qdrant_index_update(
        tmp_path, collection_name="book",
        source_sha256="target-source", source_record_count=1)
    marker = json.loads(marker_path.read_text(encoding="utf-8"))

    assert marker_path.parent == tmp_path
    assert marker_path != manifest_path
    assert marker["backend"] == "qdrant"
    assert marker["collection"] == "book"
    assert marker["target_source_sha256"] == "target-source"
    assert rag._qdrant_update_marker_path(
        tmp_path, collection_name="sibling") != marker_path

    # Presence is authoritative even if a crash truncated the diagnostics.
    marker_path.write_text("", encoding="utf-8")
    hashes, rebuild, reason = rag._resolve_incremental_index_state(
        tmp_path, backend="qdrant", collection_name="book",
        embedding_model="model-a", embedding_dimension=2,
        collection_exists=True, full_reindex=False)
    assert hashes == {}
    assert rebuild is True
    assert "did not complete" in reason
    with pytest.raises(ValueError, match="Index update is incomplete"):
        rag._query_manifest_dimension(
            tmp_path, backend="qdrant", collection_name="book",
            embedding_model="model-a")

    sibling_hashes, sibling_rebuild, _ = (
        rag._resolve_incremental_index_state(
            tmp_path, backend="qdrant", collection_name="sibling",
            embedding_model="model-a", embedding_dimension=2,
            collection_exists=True, full_reindex=False))
    assert sibling_hashes == {"chunk_b": "hash-b"}
    assert sibling_rebuild is False

    rag._finish_index_update(marker_path)
    assert not marker_path.exists()
    assert rag._query_manifest_dimension(
        tmp_path, backend="qdrant", collection_name="book",
        embedding_model="model-a") == 2


@pytest.mark.parametrize(
    ("payload", "message"),
    [
        ('{"text":"valid","metadata":{}}\n{broken\n', "Invalid JSON"),
        ('{"metadata":{}}\n', "'text' must be a non-empty string"),
        ('{"text":"valid","metadata":[]}\n', "'metadata' must be a JSON object"),
        ('{"text":"valid","metadata":{"score":NaN}}\n', "finite numbers"),
        ("\n", "contains no records"),
    ],
)
def test_index_record_loader_fails_closed(payload, message, tmp_path):
    chunks = tmp_path / "chunks.jsonl"
    chunks.write_text(payload, encoding="utf-8")

    with pytest.raises(ValueError, match=message):
        rag._load_index_records_strict(chunks)


def test_index_record_loader_rejects_duplicate_stable_ids(tmp_path):
    chunks = tmp_path / "chunks.jsonl"
    records = [
        {"text": "same source", "metadata": {"chunk_index": index}}
        for index in range(2)
    ]
    chunks.write_text(
        "".join(json.dumps(record) + "\n" for record in records),
        encoding="utf-8")

    with pytest.raises(ValueError, match="duplicate stable chunk IDs"):
        rag._load_index_records_strict(chunks)


def test_invalid_chunks_fail_before_existing_collection_is_opened(
        monkeypatch, tmp_path):
    chunks = tmp_path / "chunks.jsonl"
    chunks.write_text('{"metadata":{}}\n', encoding="utf-8")
    chromadb = ModuleType("chromadb")

    def fail_client(*args, **kwargs):
        pytest.fail("invalid input must fail before opening the index")

    chromadb.PersistentClient = fail_client
    monkeypatch.setitem(sys.modules, "chromadb", chromadb)

    with pytest.raises(ValueError, match="'text'"):
        rag.index_chunks(chunks, tmp_path / "db", embedding_model="model-a")


def test_bounded_upsert_queue_surfaces_worker_failure_without_blocking():
    work_queue = queue.Queue(maxsize=1)
    work_queue.put("already full")
    worker = Future()
    worker.set_exception(RuntimeError("upsert failed"))

    with pytest.raises(RuntimeError, match="upsert failed"):
        rag._put_unless_worker_failed(work_queue, "next batch", worker)


def test_manifest_validation_rebuilds_on_schema_model_or_dimension_change(
        tmp_path):
    rag._save_index_manifest(
        tmp_path, backend="chroma", collection_name="book",
        embedding_model="model-a", embedding_dimension=2,
        chunk_hashes={"chunk_a": "a"})

    hashes, rebuild, reason = rag._resolve_incremental_index_state(
        tmp_path, backend="chroma", collection_name="book",
        embedding_model="model-a", embedding_dimension=2,
        collection_exists=True, full_reindex=False)
    assert hashes == {"chunk_a": "a"}
    assert rebuild is False
    assert reason == "manifest compatible"

    for model, dimension in [("model-b", 2), ("model-a", 4)]:
        hashes, rebuild, reason = rag._resolve_incremental_index_state(
            tmp_path, backend="chroma", collection_name="book",
            embedding_model=model, embedding_dimension=dimension,
            collection_exists=True, full_reindex=False)
        assert hashes == {}
        assert rebuild is True
        assert "changed" in reason

    manifest_path = rag._index_manifest_path(
        tmp_path, backend="chroma", collection_name="book")
    payload = json.loads(manifest_path.read_text(encoding="utf-8"))
    payload["schema_version"] += 1
    manifest_path.write_text(json.dumps(payload), encoding="utf-8")

    hashes, rebuild, reason = rag._resolve_incremental_index_state(
        tmp_path, backend="chroma", collection_name="book",
        embedding_model="model-a", embedding_dimension=2,
        collection_exists=True, full_reindex=False)
    assert hashes == {}
    assert rebuild is True
    assert reason.startswith("schema_version changed")


def test_legacy_sidecar_is_never_used_for_an_incremental_skip(tmp_path):
    legacy = tmp_path / "chunk_hashes.json"
    legacy.write_text('{"chunk_old":"old"}', encoding="utf-8")

    hashes, rebuild, reason = rag._resolve_incremental_index_state(
        tmp_path, backend="qdrant", collection_name="book",
        embedding_model="model-a", embedding_dimension=2,
        collection_exists=True, full_reindex=False)

    assert hashes == {}
    assert rebuild is True
    assert "legacy" in reason
    assert legacy.is_file()


def test_qdrant_removed_id_lookup_scrolls_every_page():
    calls = []
    pages = {
        None: ([SimpleNamespace(
            id=1, payload={"stable_id": "keep"})], "page-2"),
        "page-2": ([SimpleNamespace(
            id=2, payload={"stable_id": "remove"})], None),
    }

    class FakeClient:
        def scroll(self, collection_name, *, limit, offset,
                   with_payload, with_vectors):
            calls.append((collection_name, limit, offset,
                          with_payload, with_vectors))
            return pages[offset]

    point_ids = rag._qdrant_point_ids_for_stable_ids(
        FakeClient(), "book", {"remove"}, page_size=1)

    assert point_ids == [2]
    assert [call[2] for call in calls] == [None, "page-2"]


def test_qdrant_removed_id_lookup_empty_target_does_not_scroll():
    class FakeClient:
        def scroll(self, *_args, **_kwargs):
            pytest.fail("an empty removal set must not scan Qdrant")

    assert rag._qdrant_point_ids_for_stable_ids(
        FakeClient(), "book", set()) == []


def test_qdrant_removed_id_lookup_collects_all_matches_across_pages():
    calls = []
    pages = {
        None: ([
            SimpleNamespace(id=10, payload={"stable_id": "remove-a"}),
            SimpleNamespace(id=11, payload={"stable_id": "keep"}),
        ], "page-2"),
        "page-2": ([
            SimpleNamespace(id=20, payload={"stable_id": "remove-b"}),
            SimpleNamespace(id=21, payload=None),
        ], "page-3"),
        "page-3": ([
            SimpleNamespace(id=30, payload={"stable_id": "remove-a"}),
        ], None),
    }

    class FakeClient:
        def scroll(self, collection_name, *, limit, offset,
                   with_payload, with_vectors):
            calls.append(offset)
            return pages[offset]

    point_ids = rag._qdrant_point_ids_for_stable_ids(
        FakeClient(), "book", {"remove-a", "remove-b"}, page_size=2)

    assert point_ids == [10, 20, 30]
    assert calls == [None, "page-2", "page-3"]


@pytest.mark.parametrize(
    "pages",
    [
        {
            None: ([], "loop"),
            "loop": ([], "loop"),
        },
        {
            None: ([], "offset-a"),
            "offset-a": ([], "offset-b"),
            "offset-b": ([], "offset-a"),
        },
    ],
    ids=["immediate", "multi-offset"],
)
def test_qdrant_removed_id_lookup_rejects_continuation_cycles(pages):
    calls = []

    class FakeClient:
        def scroll(self, collection_name, *, limit, offset,
                   with_payload, with_vectors):
            calls.append(offset)
            if len(calls) > len(pages):
                pytest.fail("continuation cycle was not detected")
            return pages[offset]

    with pytest.raises(RuntimeError, match="continuation offset"):
        rag._qdrant_point_ids_for_stable_ids(
            FakeClient(), "book", {"remove"}, page_size=1)


def test_qdrant_removed_id_lookup_requires_exact_stable_id_set():
    class FakeClient:
        def scroll(self, collection_name, *, limit, offset,
                   with_payload, with_vectors):
            return ([
                SimpleNamespace(id=1, payload={"stable_id": "remove-a"}),
                SimpleNamespace(id=2, payload={"stable_id": "remove-a"}),
            ], None)

    with pytest.raises(RuntimeError, match="stable ID.*not found"):
        rag._qdrant_point_ids_for_stable_ids(
            FakeClient(), "book", {"remove-a", "remove-b"})


def test_qdrant_identity_verifier_accepts_exact_reordered_points():
    class FakeClient:
        def scroll(self, collection_name, *, limit, offset,
                   with_payload, with_vectors):
            return ([
                SimpleNamespace(id=2, payload={"stable_id": "chunk_b"}),
                SimpleNamespace(id=1, payload={"stable_id": "chunk_a"}),
            ], None)

    point_ids = rag._require_qdrant_stable_ids(
        FakeClient(), "book", {"chunk_a", "chunk_b"})

    assert point_ids == {"chunk_b": [2], "chunk_a": [1]}


@pytest.mark.parametrize(
    ("points", "expected", "message"),
    [
        ([
            SimpleNamespace(id=1, payload={"stable_id": "chunk_a"}),
            SimpleNamespace(id=2, payload={"stable_id": "chunk_c"}),
        ], {"chunk_a", "chunk_b"}, "not found.*unexpected"),
        ([
            SimpleNamespace(id=1, payload={"stable_id": "chunk_a"}),
            SimpleNamespace(id=2, payload={"stable_id": "chunk_a"}),
        ], {"chunk_a"}, "duplicate stable-ID"),
        ([SimpleNamespace(id=1, payload=None)], set(),
         "without a stable ID"),
    ],
    ids=["equal-count-swap", "duplicate", "missing-payload"],
)
def test_qdrant_identity_verifier_rejects_physical_drift(
        points, expected, message):
    class FakeClient:
        def scroll(self, collection_name, *, limit, offset,
                   with_payload, with_vectors):
            return points, None

    with pytest.raises(RuntimeError, match=message):
        rag._require_qdrant_stable_ids(
            FakeClient(), "book", expected)


def test_qdrant_payload_canonical_fields_override_legacy_metadata():
    record = {
        "text": "canonical text",
        "metadata": {"text": "forged", "stable_id": "chunk_old"},
    }

    payload = rag._qdrant_payload(record, "chunk_current")

    assert payload["text"] == "canonical text"
    assert payload["stable_id"] == "chunk_current"


def test_qdrant_numeric_point_id_collision_fails_before_open(
        monkeypatch, tmp_path):
    chunks = tmp_path / "chunks.jsonl"
    chunks.write_text(
        "".join(json.dumps({
            "text": text,
            "metadata": {"chunk_index": index},
        }) + "\n" for index, text in enumerate(["first", "second"])),
        encoding="utf-8")
    stable_ids = {
        "first": "chunk_0000000000000001",
        "second": "chunk_8000000000000001",
    }

    class FailClient:
        def __init__(self, *_args, **_kwargs):
            pytest.fail("point-ID collisions must fail before Qdrant opens")

    qdrant_client = ModuleType("qdrant_client")
    qdrant_client.QdrantClient = FailClient
    qdrant_client.models = SimpleNamespace()
    monkeypatch.setitem(sys.modules, "qdrant_client", qdrant_client)
    monkeypatch.setattr(
        rag, "_chunk_id", lambda record: stable_ids[record["text"]])
    monkeypatch.setattr(
        rag, "_count_embedding_text_tokens",
        lambda texts, _model: ([1 for _text in texts], True))

    with pytest.raises(ValueError, match="colliding numeric Qdrant point IDs"):
        rag.index_chunks_qdrant(
            chunks, tmp_path / "qdrant", embedding_model="model-a")


def test_qdrant_marker_write_failure_prevents_collection_mutation(
        monkeypatch, tmp_path):
    chunks_path = tmp_path / "chunks.jsonl"
    db_path = tmp_path / "qdrant"
    _write_chunks(chunks_path)
    mutations = []

    class FakeQdrantClient:
        def __init__(self, path):
            self.path = path

        def collection_exists(self, collection_name):
            return False

        def create_collection(self, **kwargs):
            mutations.append("create")

        def delete_collection(self, collection_name):
            mutations.append("delete")

        def upsert(self, **kwargs):
            mutations.append("upsert")

    qdrant_client = ModuleType("qdrant_client")
    qdrant_client.QdrantClient = FakeQdrantClient
    qdrant_client.models = SimpleNamespace()
    monkeypatch.setitem(sys.modules, "qdrant_client", qdrant_client)
    monkeypatch.setattr(
        rag, "_count_embedding_text_tokens",
        lambda texts, _model: ([7 for _text in texts], True))
    monkeypatch.setattr(
        rag, "_embed_texts",
        lambda texts, _model, **_kwargs: [
            [0.0, 1.0] for _text in texts])

    def fail_atomic_write(*_args, **_kwargs):
        raise OSError("injected marker write failure")

    monkeypatch.setattr(rag, "_atomic_write_json", fail_atomic_write)

    with pytest.raises(OSError, match="marker write failure"):
        rag.index_chunks_qdrant(
            chunks_path, db_path,
            collection_name="book", embedding_model="model-a")

    assert mutations == []
    assert not rag._qdrant_update_marker_path(
        db_path, collection_name="book").exists()


def _prepare_qdrant_removed_only_incremental(
        monkeypatch, tmp_path, *, include_removed_point):
    chunks_path = tmp_path / "chunks.jsonl"
    db_path = tmp_path / "qdrant"
    keep_record = {
        "text": "The retained rule remains indexed.",
        "metadata": {
            "chunk_index": 0,
            "context": "Retained doctrine",
            "embedding_token_count": 7,
        },
    }
    removed_record = {
        "text": "This obsolete rule must be removed.",
        "metadata": {
            "chunk_index": 1,
            "context": "Obsolete doctrine",
            "embedding_token_count": 7,
        },
    }
    chunks_path.write_text(
        json.dumps(keep_record) + "\n", encoding="utf-8")

    keep_stable_id = rag._chunk_id(keep_record)
    removed_stable_id = rag._chunk_id(removed_record)
    old_hashes = {
        keep_stable_id: rag._chunk_hash(keep_record),
        removed_stable_id: rag._chunk_hash(removed_record),
    }
    manifest_path = rag._save_index_manifest(
        db_path, backend="qdrant", collection_name="book",
        embedding_model="model-a", embedding_dimension=2,
        chunk_hashes=old_hashes, source_sha256="old-source",
        source_record_count=2)
    marker_path = rag._qdrant_update_marker_path(
        db_path, collection_name="book")

    keep_point = SimpleNamespace(
        id=101, payload={"stable_id": keep_stable_id})
    removed_point = SimpleNamespace(
        id=202, payload={"stable_id": removed_stable_id})
    state = SimpleNamespace(
        points={101: keep_point}, scroll_offsets=[], deletes=[])
    if include_removed_point:
        state.points[202] = removed_point

    class Model:
        def __init__(self, **kwargs):
            self.__dict__.update(kwargs)

    models = SimpleNamespace(PointIdsList=Model)

    class FakeQdrantClient:
        def __init__(self, path):
            self.path = path

        def collection_exists(self, collection_name):
            return collection_name == "book"

        def delete_collection(self, collection_name):
            pytest.fail("a compatible removal-only run must not rebuild")

        def create_collection(self, **kwargs):
            pytest.fail("the existing compatible collection must be reused")

        def scroll(self, collection_name, *, limit, offset,
                   with_payload, with_vectors):
            state.scroll_offsets.append(offset)
            if offset is None:
                return [state.points[101]], "later-page"
            assert offset == "later-page"
            point = state.points.get(202)
            return ([point] if point is not None else []), None

        def delete(self, collection_name, *, points_selector, wait):
            assert wait is True
            assert marker_path.is_file()
            state.deletes.append(list(points_selector.points))
            for point_id in points_selector.points:
                state.points.pop(point_id, None)

        def upsert(self, **kwargs):
            pytest.fail("a removal-only run must not upsert unchanged chunks")

        def get_collection(self, collection_name):
            return SimpleNamespace(points_count=len(state.points))

    qdrant_client = ModuleType("qdrant_client")
    qdrant_client.QdrantClient = FakeQdrantClient
    qdrant_client.models = models
    monkeypatch.setitem(sys.modules, "qdrant_client", qdrant_client)
    monkeypatch.setattr(
        rag, "_count_embedding_text_tokens",
        lambda texts, _model: ([7 for _text in texts], True))
    monkeypatch.setattr(
        rag, "_embed_texts",
        lambda texts, _model, **_kwargs: [
            [0.0, 1.0] for _text in texts])

    return SimpleNamespace(
        chunks_path=chunks_path,
        db_path=db_path,
        manifest_path=manifest_path,
        marker_path=marker_path,
        keep_stable_id=keep_stable_id,
        removed_stable_id=removed_stable_id,
        state=state,
    )


def test_qdrant_removed_only_incremental_deletes_later_page_and_saves_manifest(
        monkeypatch, tmp_path):
    fixture = _prepare_qdrant_removed_only_incremental(
        monkeypatch, tmp_path, include_removed_point=True)

    rag.index_chunks_qdrant(
        fixture.chunks_path, fixture.db_path,
        collection_name="book", embedding_model="model-a")

    assert fixture.state.scroll_offsets == [
        None, "later-page", None, "later-page"]
    assert fixture.state.deletes == [[202]]
    assert set(fixture.state.points) == {101}
    assert not fixture.marker_path.exists()
    manifest = json.loads(
        fixture.manifest_path.read_text(encoding="utf-8"))
    assert set(manifest["chunk_hashes"]) == {fixture.keep_stable_id}
    assert fixture.removed_stable_id not in manifest["chunk_hashes"]
    assert manifest["source_record_count"] == 1
    assert manifest["source_sha256"] == hashlib.sha256(
        fixture.chunks_path.read_bytes()).hexdigest()


def test_qdrant_missing_manifest_removal_fails_without_saving_manifest(
        monkeypatch, tmp_path):
    fixture = _prepare_qdrant_removed_only_incremental(
        monkeypatch, tmp_path, include_removed_point=False)
    original_manifest = fixture.manifest_path.read_bytes()

    with pytest.raises(RuntimeError, match="stable ID.*not found"):
        rag.index_chunks_qdrant(
            fixture.chunks_path, fixture.db_path,
            collection_name="book", embedding_model="model-a")

    assert fixture.state.scroll_offsets == [None, "later-page"]
    assert fixture.state.deletes == []
    assert not fixture.marker_path.exists()
    assert fixture.manifest_path.read_bytes() == original_manifest
    manifest = json.loads(original_manifest)
    assert set(manifest["chunk_hashes"]) == {
        fixture.keep_stable_id, fixture.removed_stable_id}


def _prepare_qdrant_changed_existing_incremental(
        monkeypatch, tmp_path, *, delete_mutates=True, upsert_mutates=True):
    chunks_path = tmp_path / "chunks.jsonl"
    db_path = tmp_path / "qdrant"
    old_record = {
        "text": "The source text keeps its durable identity.",
        "metadata": {
            "chunk_index": 0,
            "context": "Old classification",
            "embedding_token_count": 7,
        },
    }
    new_record = {
        **old_record,
        "metadata": {
            **old_record["metadata"],
            "context": "Corrected classification",
        },
    }
    chunks_path.write_text(
        json.dumps(new_record) + "\n", encoding="utf-8")

    stable_id = rag._chunk_id(old_record)
    assert rag._chunk_id(new_record) == stable_id
    old_hash = rag._chunk_hash(old_record)
    new_hash = rag._chunk_hash(new_record)
    assert new_hash != old_hash
    manifest_path = rag._save_index_manifest(
        db_path, backend="qdrant", collection_name="book",
        embedding_model="model-a", embedding_dimension=2,
        chunk_hashes={stable_id: old_hash}, source_sha256="old-source",
        source_record_count=1)
    marker_path = rag._qdrant_update_marker_path(
        db_path, collection_name="book")

    point_id = rag._qdrant_point_id(stable_id)
    old_point = SimpleNamespace(
        id=point_id,
        payload={"stable_id": stable_id, "context": "Old classification"},
    )
    state = SimpleNamespace(
        points={point_id: old_point}, deletes=[], upserts=[], scrolls=0,
        collection_exists=True, collection_rebuilds=0,
        delete_mutates=delete_mutates, upsert_mutates=upsert_mutates)

    class Model:
        def __init__(self, **kwargs):
            self.__dict__.update(kwargs)

    models = SimpleNamespace(
        VectorParams=Model,
        SparseVectorParams=Model,
        SparseVector=Model,
        PointStruct=Model,
        PointIdsList=Model,
        Distance=SimpleNamespace(COSINE="cosine"),
        Modifier=SimpleNamespace(IDF="idf"),
    )

    class FakeQdrantClient:
        def __init__(self, path):
            self.path = path

        def collection_exists(self, collection_name):
            return collection_name == "book" and state.collection_exists

        def delete_collection(self, collection_name):
            assert marker_path.is_file()
            state.collection_rebuilds += 1
            state.points.clear()
            state.collection_exists = False

        def create_collection(self, **kwargs):
            assert marker_path.is_file()
            state.collection_exists = True

        def scroll(self, collection_name, *, limit, offset,
                   with_payload, with_vectors):
            state.scrolls += 1
            return list(state.points.values()), None

        def delete(self, collection_name, *, points_selector, wait):
            assert wait is True
            assert marker_path.is_file()
            state.deletes.append(list(points_selector.points))
            if state.delete_mutates:
                for existing_id in points_selector.points:
                    state.points.pop(existing_id, None)

        def upsert(self, *, collection_name, points, wait):
            assert wait is True
            assert marker_path.is_file()
            state.upserts.append(list(points))
            if state.upsert_mutates:
                for point in points:
                    state.points[point.id] = point

    qdrant_client = ModuleType("qdrant_client")
    qdrant_client.QdrantClient = FakeQdrantClient
    qdrant_client.models = models
    monkeypatch.setitem(sys.modules, "qdrant_client", qdrant_client)
    monkeypatch.setattr(
        rag, "_count_embedding_text_tokens",
        lambda texts, _model: ([7 for _text in texts], True))
    monkeypatch.setattr(
        rag, "_embed_texts",
        lambda texts, _model, **_kwargs: [
            [0.0, 1.0] for _text in texts])

    return SimpleNamespace(
        chunks_path=chunks_path,
        db_path=db_path,
        manifest_path=manifest_path,
        marker_path=marker_path,
        stable_id=stable_id,
        point_id=point_id,
        old_hash=old_hash,
        new_hash=new_hash,
        state=state,
    )


def test_qdrant_changed_existing_delete_noop_preserves_old_manifest(
        monkeypatch, tmp_path):
    fixture = _prepare_qdrant_changed_existing_incremental(
        monkeypatch, tmp_path, delete_mutates=False)
    original_manifest = fixture.manifest_path.read_bytes()

    with pytest.raises(RuntimeError, match="unexpected stable ID"):
        rag.index_chunks_qdrant(
            fixture.chunks_path, fixture.db_path,
            collection_name="book", embedding_model="model-a")

    assert fixture.state.deletes == [[fixture.point_id]]
    assert fixture.state.upserts == []
    assert fixture.state.scrolls == 2
    assert fixture.marker_path.is_file()
    assert fixture.manifest_path.read_bytes() == original_manifest


def test_qdrant_changed_existing_upsert_noop_preserves_old_manifest(
        monkeypatch, tmp_path):
    fixture = _prepare_qdrant_changed_existing_incremental(
        monkeypatch, tmp_path, upsert_mutates=False)
    original_manifest = fixture.manifest_path.read_bytes()

    with pytest.raises(RuntimeError, match="stable ID.*not found"):
        rag.index_chunks_qdrant(
            fixture.chunks_path, fixture.db_path,
            collection_name="book", embedding_model="model-a")

    assert fixture.state.deletes == [[fixture.point_id]]
    assert len(fixture.state.upserts) == 1
    assert fixture.state.points == {}
    assert fixture.state.scrolls == 3
    assert fixture.marker_path.is_file()
    assert fixture.manifest_path.read_bytes() == original_manifest


def test_qdrant_changed_existing_is_deleted_then_replaced_before_manifest_save(
        monkeypatch, tmp_path):
    fixture = _prepare_qdrant_changed_existing_incremental(
        monkeypatch, tmp_path)

    rag.index_chunks_qdrant(
        fixture.chunks_path, fixture.db_path,
        collection_name="book", embedding_model="model-a")

    assert fixture.state.deletes == [[fixture.point_id]]
    assert len(fixture.state.upserts) == 1
    assert fixture.state.scrolls == 3
    assert not fixture.marker_path.exists()
    assert fixture.state.points[fixture.point_id].payload["context"] == (
        "Corrected classification")
    manifest = json.loads(
        fixture.manifest_path.read_text(encoding="utf-8"))
    assert manifest["chunk_hashes"] == {
        fixture.stable_id: fixture.new_hash}
    assert manifest["source_sha256"] == hashlib.sha256(
        fixture.chunks_path.read_bytes()).hexdigest()


def test_qdrant_manifest_commit_failure_stays_dirty_and_retry_rebuilds(
        monkeypatch, tmp_path):
    fixture = _prepare_qdrant_changed_existing_incremental(
        monkeypatch, tmp_path)
    original_manifest = fixture.manifest_path.read_bytes()
    save_manifest = rag._save_index_manifest

    def fail_manifest_commit(*args, **kwargs):
        assert fixture.marker_path.is_file()
        raise OSError("injected manifest commit failure")

    monkeypatch.setattr(rag, "_save_index_manifest", fail_manifest_commit)
    with pytest.raises(OSError, match="manifest commit failure"):
        rag.index_chunks_qdrant(
            fixture.chunks_path, fixture.db_path,
            collection_name="book", embedding_model="model-a")

    assert fixture.marker_path.is_file()
    assert fixture.manifest_path.read_bytes() == original_manifest
    assert fixture.state.points[fixture.point_id].payload["context"] == (
        "Corrected classification")
    with pytest.raises(ValueError, match="Index update is incomplete"):
        rag._query_manifest_dimension(
            fixture.db_path, backend="qdrant", collection_name="book",
            embedding_model="model-a")

    monkeypatch.setattr(rag, "_save_index_manifest", save_manifest)
    rag.index_chunks_qdrant(
        fixture.chunks_path, fixture.db_path,
        collection_name="book", embedding_model="model-a")

    assert fixture.state.collection_rebuilds == 1
    assert len(fixture.state.upserts) == 2
    assert not fixture.marker_path.exists()
    manifest = json.loads(
        fixture.manifest_path.read_text(encoding="utf-8"))
    assert manifest["chunk_hashes"] == {
        fixture.stable_id: fixture.new_hash}


def test_qdrant_marker_cleanup_runs_after_manifest_commit(
        monkeypatch, tmp_path):
    fixture = _prepare_qdrant_changed_existing_incremental(
        monkeypatch, tmp_path)

    def fail_marker_cleanup(marker_path):
        assert marker_path.is_file()
        manifest = json.loads(
            fixture.manifest_path.read_text(encoding="utf-8"))
        assert manifest["chunk_hashes"] == {
            fixture.stable_id: fixture.new_hash}
        raise OSError("injected marker cleanup failure")

    monkeypatch.setattr(rag, "_finish_index_update", fail_marker_cleanup)
    with pytest.raises(OSError, match="marker cleanup failure"):
        rag.index_chunks_qdrant(
            fixture.chunks_path, fixture.db_path,
            collection_name="book", embedding_model="model-a")

    assert fixture.marker_path.is_file()
    with pytest.raises(ValueError, match="Index update is incomplete"):
        rag._query_manifest_dimension(
            fixture.db_path, backend="qdrant", collection_name="book",
            embedding_model="model-a")


def test_chroma_migrates_legacy_skips_compatible_and_rebuilds_model_change(
        monkeypatch, tmp_path):
    chunks_path = tmp_path / "chunks.jsonl"
    db_path = tmp_path / "chroma"
    db_path.mkdir()
    _write_chunks(chunks_path)
    (db_path / "chunk_hashes.json").write_text(
        '{"chunk_stale":"stale"}', encoding="utf-8")

    state = SimpleNamespace(collections={}, deleted=[], upserts=[])

    class FakeCollection:
        def __init__(self, name):
            self.name = name
            self.rows = {}

        def upsert(self, *, ids, embeddings, documents, metadatas):
            state.upserts.append((self.name, embeddings))
            for index, item_id in enumerate(ids):
                self.rows[item_id] = (
                    embeddings[index], documents[index], metadatas[index])

        def delete(self, *, ids):
            for item_id in ids:
                self.rows.pop(item_id, None)

        def count(self):
            return len(self.rows)

    state.collections["book"] = FakeCollection("book")
    state.collections["sibling"] = FakeCollection("sibling")
    state.collections["sibling"].rows["keep"] = "untouched"

    class FakeChromaClient:
        def __init__(self, path):
            self.path = path

        def get_collection(self, name):
            if name not in state.collections:
                raise LookupError(name)
            return state.collections[name]

        def get_or_create_collection(self, *, name, metadata):
            state.collections.setdefault(name, FakeCollection(name))
            return state.collections[name]

        def delete_collection(self, name):
            state.deleted.append(name)
            del state.collections[name]

    chromadb = ModuleType("chromadb")
    chromadb.PersistentClient = FakeChromaClient
    monkeypatch.setitem(sys.modules, "chromadb", chromadb)

    embedding_calls = []
    monkeypatch.setattr(
        rag, "_embed_texts",
        _fake_embeddings(embedding_calls, {"model-a": 2, "model-b": 3}))

    rag.index_chunks(
        chunks_path, db_path, collection_name="book",
        embedding_model="model-a")

    assert state.deleted == ["book"]
    assert state.collections["sibling"].rows == {"keep": "untouched"}
    assert (db_path / "chunk_hashes.json").is_file()
    first_upsert_count = len(state.upserts)
    first_call_count = len(embedding_calls)

    rag.index_chunks(
        chunks_path, db_path, collection_name="book",
        embedding_model="model-a")

    assert state.deleted == ["book"]
    assert len(state.upserts) == first_upsert_count
    assert len(embedding_calls) == first_call_count + 1  # dimension probe only

    rag.index_chunks(
        chunks_path, db_path, collection_name="book",
        embedding_model="model-b")

    assert state.deleted == ["book", "book"]
    assert len(state.upserts) == first_upsert_count + 1
    assert state.collections["sibling"].rows == {"keep": "untouched"}
    manifest = rag._load_index_manifest(
        db_path, backend="chroma", collection_name="book")
    assert manifest["embedding_model"] == "model-b"
    assert manifest["embedding_dimension"] == 3
    assert manifest["source_sha256"] == hashlib.sha256(
        chunks_path.read_bytes()).hexdigest()
    assert manifest["source_record_count"] == 1


def test_qdrant_manifest_skip_and_model_change_preserve_sibling(
        monkeypatch, tmp_path):
    chunks_path = tmp_path / "chunks.jsonl"
    db_path = tmp_path / "qdrant"
    _write_chunks(chunks_path)

    state = SimpleNamespace(
        collections={"sibling": {"keep": "untouched"}},
        deleted=[], upserts=[])
    marker_path = rag._qdrant_update_marker_path(
        db_path, collection_name="book")

    class Model:
        def __init__(self, **kwargs):
            self.__dict__.update(kwargs)

    models = SimpleNamespace(
        VectorParams=Model,
        SparseVectorParams=Model,
        SparseVector=Model,
        PointStruct=Model,
        PointIdsList=Model,
        Distance=SimpleNamespace(COSINE="cosine"),
        Modifier=SimpleNamespace(IDF="idf"),
    )

    class FakeQdrantClient:
        def __init__(self, path):
            self.path = path

        def collection_exists(self, name):
            return name in state.collections

        def create_collection(self, *, collection_name, **kwargs):
            assert marker_path.is_file()
            state.collections[collection_name] = {}

        def delete_collection(self, collection_name):
            assert marker_path.is_file()
            state.deleted.append(collection_name)
            del state.collections[collection_name]

        def upsert(self, *, collection_name, points, wait):
            assert wait is True
            assert marker_path.is_file()
            state.upserts.append((collection_name, points))
            for point in points:
                state.collections[collection_name][point.id] = point

        def scroll(self, collection_name, limit, offset=None, **kwargs):
            points = list(state.collections[collection_name].values())
            return points, None

        def delete(self, collection_name, *, points_selector, wait):
            assert wait is True
            assert marker_path.is_file()
            for point_id in points_selector.points:
                state.collections[collection_name].pop(point_id, None)

        def get_collection(self, collection_name):
            return SimpleNamespace(
                points_count=len(state.collections[collection_name]))

    qdrant_client = ModuleType("qdrant_client")
    qdrant_client.QdrantClient = FakeQdrantClient
    qdrant_client.models = models
    monkeypatch.setitem(sys.modules, "qdrant_client", qdrant_client)

    embedding_calls = []
    monkeypatch.setattr(
        rag, "_embed_texts",
        _fake_embeddings(embedding_calls, {"model-a": 2, "model-b": 4}))

    rag.index_chunks_qdrant(
        chunks_path, db_path, collection_name="book",
        embedding_model="model-a")
    assert not marker_path.exists()
    first_upsert_count = len(state.upserts)
    first_call_count = len(embedding_calls)

    begin_update = rag._begin_qdrant_index_update
    monkeypatch.setattr(
        rag, "_begin_qdrant_index_update",
        lambda *_args, **_kwargs: pytest.fail(
            "an unchanged run must not create an update marker"))
    rag.index_chunks_qdrant(
        chunks_path, db_path, collection_name="book",
        embedding_model="model-a")
    monkeypatch.setattr(rag, "_begin_qdrant_index_update", begin_update)
    assert not marker_path.exists()
    assert len(state.upserts) == first_upsert_count
    assert len(embedding_calls) == first_call_count + 1  # dimension probe only

    rag.index_chunks_qdrant(
        chunks_path, db_path, collection_name="book",
        embedding_model="model-b")
    assert not marker_path.exists()

    assert state.deleted == ["book"]
    assert state.collections["sibling"] == {"keep": "untouched"}
    assert len(state.upserts) == first_upsert_count + 1
    manifest = rag._load_index_manifest(
        db_path, backend="qdrant", collection_name="book")
    assert manifest["embedding_model"] == "model-b"
    assert manifest["embedding_dimension"] == 4
    assert manifest["source_sha256"] == hashlib.sha256(
        chunks_path.read_bytes()).hexdigest()
    assert manifest["source_record_count"] == 1
