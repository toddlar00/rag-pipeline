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


def test_qdrant_payload_canonical_fields_override_legacy_metadata():
    record = {
        "text": "canonical text",
        "metadata": {"text": "forged", "stable_id": "chunk_old"},
    }

    payload = rag._qdrant_payload(record, "chunk_current")

    assert payload["text"] == "canonical text"
    assert payload["stable_id"] == "chunk_current"


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
            state.collections[collection_name] = {}

        def delete_collection(self, collection_name):
            state.deleted.append(collection_name)
            del state.collections[collection_name]

        def upsert(self, *, collection_name, points):
            state.upserts.append((collection_name, points))
            for point in points:
                state.collections[collection_name][point.id] = point

        def scroll(self, collection_name, limit, offset=None, **kwargs):
            points = list(state.collections[collection_name].values())
            return points, None

        def delete(self, collection_name, *, points_selector):
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
    first_upsert_count = len(state.upserts)
    first_call_count = len(embedding_calls)

    rag.index_chunks_qdrant(
        chunks_path, db_path, collection_name="book",
        embedding_model="model-a")
    assert len(state.upserts) == first_upsert_count
    assert len(embedding_calls) == first_call_count + 1  # dimension probe only

    rag.index_chunks_qdrant(
        chunks_path, db_path, collection_name="book",
        embedding_model="model-b")

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
