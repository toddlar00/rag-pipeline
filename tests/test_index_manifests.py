import concurrent.futures
import hashlib
import json
import queue
import sys
import threading
from concurrent.futures import Future
from pathlib import Path
from types import ModuleType, SimpleNamespace
from uuid import UUID

import pytest

import rag


class _FakeGrpcPointId:
    __hash__ = None
    DESCRIPTOR = SimpleNamespace(full_name="qdrant.PointId")

    def __init__(self, *, num=None, uuid=None):
        self.num = num
        self.uuid = uuid

    def WhichOneof(self, name):
        assert name == "point_id_options"
        if self.num is not None:
            return "num"
        if self.uuid is not None:
            return "uuid"
        return None


def _completed_update():
    return SimpleNamespace(status="completed")


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


def _track_queue_worker_resources(
        monkeypatch, *, constructor_error=None, submit_error=None,
        shutdown_error=None, close_error=None):
    state = SimpleNamespace(
        executor_constructions=0, executors=[], bars=[], events=[])

    class RecordingProgress:
        def __init__(self, *args, **kwargs):
            self.args = args
            self.kwargs = kwargs
            self.updates = 0
            self.close_calls = 0
            state.bars.append(self)

        def update(self, amount=1):
            self.updates += amount

        def close(self):
            self.close_calls += 1
            state.events.append("progress_close")
            if close_error is not None:
                raise close_error

    class RecordingExecutor:
        def __init__(self, max_workers):
            assert max_workers == 1
            state.executor_constructions += 1
            if constructor_error is not None:
                raise constructor_error
            self.future = None
            self.thread = None
            self.shutdown_calls = []
            state.executors.append(self)

        def submit(self, function, *args, **kwargs):
            assert self.future is None
            if submit_error is not None:
                raise submit_error
            self.future = Future()

            def run():
                if not self.future.set_running_or_notify_cancel():
                    return
                try:
                    result = function(*args, **kwargs)
                except BaseException as exc:
                    self.future.set_exception(exc)
                else:
                    self.future.set_result(result)
                finally:
                    state.events.append("worker_exit")

            self.thread = threading.Thread(target=run, daemon=True)
            self.thread.start()
            return self.future

        def shutdown(self, wait=True, *, cancel_futures=False):
            self.shutdown_calls.append((wait, cancel_futures))
            if wait and self.thread is not None:
                self.thread.join(timeout=1)
                if self.thread.is_alive():
                    raise AssertionError("queue worker did not stop")
            state.events.append("executor_shutdown")
            if shutdown_error is not None:
                raise shutdown_error

    tqdm_module = ModuleType("tqdm")
    tqdm_module.tqdm = RecordingProgress
    monkeypatch.setitem(sys.modules, "tqdm", tqdm_module)
    monkeypatch.setattr(
        concurrent.futures, "ThreadPoolExecutor", RecordingExecutor)
    return state


def _track_parallel_resources(
        monkeypatch, *, constructor_error=None, shutdown_error=None,
        close_error=None):
    state = SimpleNamespace(
        executor_constructions=0, executors=[], futures=[], bars=[], events=[])

    class RecordingProgress:
        def __init__(self, *args, **kwargs):
            self.args = args
            self.kwargs = kwargs
            self.updates = 0
            self.close_calls = 0
            state.bars.append(self)

        def update(self, amount=1):
            self.updates += amount

        def close(self):
            self.close_calls += 1
            state.events.append("progress_close")
            if close_error is not None:
                raise close_error

    class RecordingExecutor:
        def __init__(self, max_workers):
            assert max_workers == 4
            state.executor_constructions += 1
            if constructor_error is not None:
                raise constructor_error
            self.shutdown_calls = []
            state.executors.append(self)

        def submit(self, function, *args, **kwargs):
            future = Future()
            state.futures.append(future)
            try:
                result = function(*args, **kwargs)
            except BaseException as exc:
                future.set_exception(exc)
            else:
                future.set_result(result)
            return future

        def shutdown(self, wait=True, *, cancel_futures=False):
            self.shutdown_calls.append((wait, cancel_futures))
            state.events.append("executor_shutdown")
            if shutdown_error is not None:
                raise shutdown_error

    tqdm_module = ModuleType("tqdm")
    tqdm_module.tqdm = RecordingProgress
    monkeypatch.setitem(sys.modules, "tqdm", tqdm_module)
    monkeypatch.setattr(
        concurrent.futures, "ThreadPoolExecutor", RecordingExecutor)
    return state


def _install_counting_vector_client(
        monkeypatch, backend, *, constructor_error=None,
        count_error=None, close_error=None):
    state = SimpleNamespace(close_calls=0)

    class FakeCollection:
        def count(self):
            if count_error is not None:
                raise count_error
            return 3

    class FakeChromaClient:
        def __init__(self, path):
            if constructor_error is not None:
                raise constructor_error
            self.path = path

        def get_collection(self, name):
            assert name == "book"
            return FakeCollection()

        def close(self):
            state.close_calls += 1
            if close_error is not None:
                raise close_error

    class FakeQdrantClient:
        def __init__(self, path):
            if constructor_error is not None:
                raise constructor_error
            self.path = path

        def collection_exists(self, name):
            assert name == "book"
            return True

        def count(self, *, collection_name, exact):
            assert collection_name == "book"
            assert exact is True
            if count_error is not None:
                raise count_error
            return SimpleNamespace(count=3)

        def close(self):
            state.close_calls += 1
            if close_error is not None:
                raise close_error

    if backend == "chroma":
        chromadb = ModuleType("chromadb")
        chromadb.PersistentClient = FakeChromaClient
        monkeypatch.setitem(sys.modules, "chromadb", chromadb)
    else:
        qdrant_client = ModuleType("qdrant_client")
        qdrant_client.QdrantClient = FakeQdrantClient
        monkeypatch.setitem(sys.modules, "qdrant_client", qdrant_client)
    return state


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
        "model_artifact_lock_sha256": rag._model_artifact_lock_sha256(),
        "chunk_hashes": hashes,
        "source_sha256": None,
        "source_record_count": None,
    }
    assert not list(tmp_path.glob("*.tmp"))

    with pytest.raises(ValueError, match="backend"):
        rag._index_manifest_path(
            tmp_path, backend="../outside", collection_name="book")


def test_model_lock_change_invalidates_index_reuse_and_queries(
        monkeypatch, tmp_path):
    monkeypatch.setattr(
        rag, "_model_artifact_lock_sha256", lambda: "a" * 64)
    rag._save_index_manifest(
        tmp_path, backend="chroma", collection_name="book",
        embedding_model="model-a", embedding_dimension=3,
        chunk_hashes={"chunk": "hash"})

    monkeypatch.setattr(
        rag, "_model_artifact_lock_sha256", lambda: "b" * 64)
    hashes, rebuild, reason = rag._resolve_incremental_index_state(
        tmp_path, backend="chroma", collection_name="book",
        embedding_model="model-a", embedding_dimension=3,
        collection_exists=True, full_reindex=False)

    assert hashes == {}
    assert rebuild is True
    assert "model_artifact_lock_sha256 changed" in reason
    with pytest.raises(ValueError, match="model_artifact_lock_sha256"):
        rag._query_manifest_dimension_impl(
            tmp_path, backend="chroma", collection_name="book",
            embedding_model="model-a")


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


def test_atomic_jsonl_failure_preserves_previous_file(monkeypatch, tmp_path):
    target = tmp_path / "chunks.jsonl"
    original = b'{"text":"old"}\n'
    target.write_bytes(original)

    def fail_replace(source, destination):
        raise OSError("simulated JSONL replace failure")

    monkeypatch.setattr(rag.os, "replace", fail_replace)

    with pytest.raises(OSError, match="replace failure"):
        rag._atomic_write_jsonl(target, [{"text": "new"}])

    assert target.read_bytes() == original
    assert not list(tmp_path.glob("*.tmp"))


@pytest.mark.parametrize("format_name", ["json", "jsonl"])
def test_atomic_writer_serialization_failure_removes_temporary_file(
        tmp_path, format_name):
    target = tmp_path / f"artifact.{format_name}"
    original = b"previous contents"
    target.write_bytes(original)

    with pytest.raises(TypeError):
        if format_name == "json":
            rag._atomic_write_json(target, {"invalid": object()})
        else:
            rag._atomic_write_jsonl(target, [{"invalid": object()}])

    assert target.read_bytes() == original
    assert not list(tmp_path.glob("*.tmp"))


@pytest.mark.parametrize("backend", ["qdrant", "chroma"])
def test_index_update_marker_is_scoped_and_blocks_manifest_use(
        tmp_path, backend):
    manifest_path = rag._save_index_manifest(
        tmp_path, backend=backend, collection_name="book",
        embedding_model="model-a", embedding_dimension=2,
        chunk_hashes={"chunk_a": "hash-a"})
    rag._save_index_manifest(
        tmp_path, backend=backend, collection_name="sibling",
        embedding_model="model-a", embedding_dimension=2,
        chunk_hashes={"chunk_b": "hash-b"})

    owner_token = "current-update-token"
    marker_path = rag._begin_index_update(
        tmp_path, backend=backend, collection_name="book",
        source_sha256="target-source", source_record_count=1,
        owner_token=owner_token)
    marker = json.loads(marker_path.read_text(encoding="utf-8"))

    assert marker_path.parent == tmp_path
    assert marker_path != manifest_path
    assert marker["backend"] == backend
    assert marker["collection"] == "book"
    assert marker["target_source_sha256"] == "target-source"
    assert marker["owner_token"] == owner_token
    assert rag._index_update_marker_path(
        tmp_path, backend=backend,
        collection_name="sibling") != marker_path
    other_backend = "chroma" if backend == "qdrant" else "qdrant"
    assert rag._index_update_marker_path(
        tmp_path, backend=other_backend,
        collection_name="book") != marker_path

    active_hashes, active_rebuild, active_reason = (
        rag._resolve_incremental_index_state(
            tmp_path, backend=backend, collection_name="book",
            embedding_model="model-a", embedding_dimension=2,
            collection_exists=True, full_reindex=False,
            active_update_token=owner_token))
    assert active_hashes == {"chunk_a": "hash-a"}
    assert active_rebuild is False
    assert active_reason == "manifest compatible"

    # Presence is authoritative even if a crash truncated the diagnostics;
    # the former owner token cannot bypass a marker that no longer proves it.
    marker_path.write_text("", encoding="utf-8")
    hashes, rebuild, reason = rag._resolve_incremental_index_state(
        tmp_path, backend=backend, collection_name="book",
        embedding_model="model-a", embedding_dimension=2,
        collection_exists=True, full_reindex=False)
    assert hashes == {}
    assert rebuild is True
    assert "did not complete" in reason

    lost_hashes, lost_rebuild, lost_reason = (
        rag._resolve_incremental_index_state(
            tmp_path, backend=backend, collection_name="book",
            embedding_model="model-a", embedding_dimension=2,
            collection_exists=True, full_reindex=False,
            active_update_token=owner_token))
    assert lost_hashes == {}
    assert lost_rebuild is True
    assert "did not complete" in lost_reason

    with pytest.raises(ValueError, match="Index update is incomplete"):
        rag._query_manifest_dimension(
            tmp_path, backend=backend, collection_name="book",
            embedding_model="model-a")

    sibling_hashes, sibling_rebuild, _ = (
        rag._resolve_incremental_index_state(
            tmp_path, backend=backend, collection_name="sibling",
            embedding_model="model-a", embedding_dimension=2,
            collection_exists=True, full_reindex=False))
    assert sibling_hashes == {"chunk_b": "hash-b"}
    assert sibling_rebuild is False

    with pytest.raises(RuntimeError, match="ownership changed"):
        rag._finish_index_update(marker_path, owner_token=owner_token)
    rag._begin_index_update(
        tmp_path, backend=backend, collection_name="book",
        source_sha256="target-source", source_record_count=1,
        owner_token=owner_token, replace_existing=True)
    rag._finish_index_update(marker_path, owner_token=owner_token)
    assert not marker_path.exists()
    assert rag._query_manifest_dimension(
        tmp_path, backend=backend, collection_name="book",
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


@pytest.mark.parametrize("backend", ["chroma", "qdrant"])
def test_index_collection_count_closes_client_on_success(
        monkeypatch, tmp_path, backend):
    state = _install_counting_vector_client(monkeypatch, backend)
    db_path = tmp_path / backend
    db_path.mkdir()

    assert rag._index_collection_count(
        db_path, "book", db_backend=backend) == 3
    assert state.close_calls == 1


@pytest.mark.parametrize("backend", ["chroma", "qdrant"])
def test_index_collection_count_preserves_constructor_failure(
        monkeypatch, tmp_path, backend):
    constructor_error = RuntimeError(f"{backend} constructor failure")
    state = _install_counting_vector_client(
        monkeypatch, backend, constructor_error=constructor_error)
    db_path = tmp_path / backend
    db_path.mkdir()

    with pytest.raises(RuntimeError) as raised:
        rag._index_collection_count(
            db_path, "book", db_backend=backend)

    assert raised.value is constructor_error
    assert state.close_calls == 0


@pytest.mark.parametrize("backend", ["chroma", "qdrant"])
def test_index_collection_count_preserves_primary_over_close_failure(
        monkeypatch, tmp_path, backend):
    count_error = RuntimeError(f"primary {backend} count failure")
    close_error = RuntimeError(f"secondary {backend} close failure")
    state = _install_counting_vector_client(
        monkeypatch, backend, count_error=count_error,
        close_error=close_error)
    db_path = tmp_path / backend
    db_path.mkdir()

    with pytest.raises(RuntimeError) as raised:
        rag._index_collection_count(
            db_path, "book", db_backend=backend)

    assert raised.value is count_error
    assert state.close_calls == 1


@pytest.mark.parametrize("backend", ["chroma", "qdrant"])
def test_index_collection_count_propagates_close_only_failure(
        monkeypatch, tmp_path, backend):
    close_error = RuntimeError(f"{backend} close failure")
    state = _install_counting_vector_client(
        monkeypatch, backend, close_error=close_error)
    db_path = tmp_path / backend
    db_path.mkdir()

    with pytest.raises(RuntimeError) as raised:
        rag._index_collection_count(
            db_path, "book", db_backend=backend)

    assert raised.value is close_error
    assert state.close_calls == 1


def test_bounded_upsert_queue_surfaces_worker_failure_without_blocking():
    work_queue = queue.Queue(maxsize=1)
    work_queue.put("already full")
    worker = Future()
    worker.set_exception(RuntimeError("upsert failed"))

    with pytest.raises(RuntimeError, match="upsert failed"):
        rag._put_unless_worker_failed(work_queue, "next batch", worker)


def test_bounded_upsert_queue_rejects_an_exited_worker():
    work_queue = queue.Queue(maxsize=1)
    worker = Future()
    worker.set_result(None)

    with pytest.raises(RuntimeError, match="worker exited"):
        rag._put_unless_worker_failed(work_queue, "next batch", worker)


def test_queue_worker_cleanup_rejects_successful_premature_exit():
    events = []
    worker = Future()
    worker.set_result(None)
    executor = SimpleNamespace(
        shutdown=lambda *, wait: events.append(("shutdown", wait)))
    progress = SimpleNamespace(close=lambda: events.append(("close", None)))

    with pytest.raises(RuntimeError, match="exited before it was asked"):
        rag._finish_queue_worker(
            queue.Queue(), worker, executor, progress,
            worker_name="test worker", primary_error=None)

    assert events == [("shutdown", True), ("close", None)]


def test_queue_worker_cleanup_preserves_primary_traceback():
    worker = Future()
    executor = SimpleNamespace(shutdown=lambda *, wait: None)
    progress = SimpleNamespace(close=lambda: None)

    try:
        raise RuntimeError("worker failed during producer put")
    except RuntimeError as primary_error:
        worker.set_exception(primary_error)
        original_traceback = primary_error.__traceback__

        rag._finish_queue_worker(
            queue.Queue(), worker, executor, progress,
            worker_name="test worker", primary_error=primary_error)

        assert primary_error.__traceback__ is original_traceback


def test_vector_client_cleanup_requires_close_and_closes_once():
    events = []
    client = SimpleNamespace(close=lambda: events.append("close"))

    with pytest.raises(RuntimeError, match="does not expose required close"):
        rag._finish_vector_client(
            object(), client_name="test", primary_error=None)
    primary_error = ValueError("primary failure")
    rag._finish_vector_client(
        object(), client_name="test", primary_error=primary_error)
    rag._finish_vector_client(
        client, client_name="test", primary_error=None)

    assert events == ["close"]


def test_vector_client_cleanup_preserves_primary_error_and_traceback():
    close_error = RuntimeError("secondary client close failure")

    class FailingClient:
        def __init__(self):
            self.close_calls = 0

        def close(self):
            self.close_calls += 1
            raise close_error

    client = FailingClient()
    try:
        raise ValueError("primary vector operation failure")
    except ValueError as primary_error:
        original_traceback = primary_error.__traceback__
        rag._finish_vector_client(
            client, client_name="test", primary_error=primary_error)
        assert primary_error.__traceback__ is original_traceback

    assert client.close_calls == 1


def test_vector_client_cleanup_preserves_primary_when_logging_fails(
        monkeypatch):
    primary_error = ValueError("primary vector operation failure")
    close_error = RuntimeError("secondary client close failure")

    def fail_logging(*_args, **_kwargs):
        raise OSError("logging handler failure")

    monkeypatch.setattr(rag.log, "error", fail_logging)
    client = SimpleNamespace(
        close=lambda: (_ for _ in ()).throw(close_error))

    try:
        raise primary_error
    except ValueError as active_error:
        original_traceback = active_error.__traceback__
        rag._finish_vector_client(
            client, client_name="test", primary_error=active_error)
        assert active_error is primary_error
        assert active_error.__traceback__ is original_traceback


def test_vector_client_cleanup_propagates_close_error_despite_outer_handler():
    close_error = RuntimeError("client close failure")
    client = SimpleNamespace(close=lambda: (_ for _ in ()).throw(close_error))

    try:
        raise ValueError("unrelated outer failure")
    except ValueError:
        with pytest.raises(RuntimeError) as raised:
            rag._finish_vector_client(
                client, client_name="test", primary_error=None)

    assert raised.value is close_error


def test_vector_client_cleanup_handles_raising_close_descriptor():
    descriptor_error = RuntimeError("close descriptor failure")

    class FailingDescriptorClient:
        @property
        def close(self):
            raise descriptor_error

    with pytest.raises(RuntimeError) as raised:
        rag._finish_vector_client(
            FailingDescriptorClient(), client_name="test",
            primary_error=None)
    assert raised.value is descriptor_error

    primary_error = ValueError("primary failure")
    rag._finish_vector_client(
        FailingDescriptorClient(), client_name="test",
        primary_error=primary_error)


def test_vector_client_owner_closes_all_clients_and_preserves_primary():
    events = []
    first_error = RuntimeError("first close failure")

    def close_first():
        events.append("first")
        raise first_error

    owner = rag._VectorClientOwner("test")
    owner.own(SimpleNamespace(close=close_first))
    owner.own(SimpleNamespace(close=lambda: events.append("second")))

    with pytest.raises(RuntimeError) as raised:
        owner.finish(primary_error=None)
    assert raised.value is first_error
    assert events == ["second", "first"]

    owner = rag._VectorClientOwner("test")
    owner.own(SimpleNamespace(close=close_first))
    primary_error = ValueError("primary failure")
    owner.finish(primary_error=primary_error)
    assert events == ["second", "first", "first"]


def test_vector_client_owner_preserves_first_cleanup_when_logging_fails(
        monkeypatch):
    first_cleanup_error = RuntimeError("first cleanup failure")
    additional_cleanup_error = RuntimeError("additional cleanup failure")

    def fail_logging(*_args, **_kwargs):
        raise OSError("logging handler failure")

    monkeypatch.setattr(rag.log, "error", fail_logging)
    owner = rag._VectorClientOwner("test")
    owner.own(SimpleNamespace(
        close=lambda: (_ for _ in ()).throw(additional_cleanup_error)))
    owner.own(SimpleNamespace(
        close=lambda: (_ for _ in ()).throw(first_cleanup_error)))

    with pytest.raises(RuntimeError) as raised:
        owner.finish(primary_error=None)

    assert raised.value is first_cleanup_error


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


def test_chroma_identity_verifier_accepts_short_paginated_records():
    calls = []
    rows = [
        ("chunk_b", {"stable_id": "chunk_b"}),
        ("chunk_a", {"stable_id": "chunk_a"}),
    ]

    class FakeCollection:
        def count(self):
            calls.append(("count",))
            return len(rows)

        def get(self, *, limit, offset, include):
            calls.append(("get", limit, offset, include))
            page = rows[offset:offset + 1]
            return {
                "ids": [item_id for item_id, _ in page],
                "metadatas": [metadata for _, metadata in page],
            }

    actual = rag._require_chroma_stable_ids(
        FakeCollection(), "book", {"chunk_a", "chunk_b"}, page_size=3)

    assert actual == {"chunk_a", "chunk_b"}
    assert calls == [
        ("count",),
        ("get", 3, 0, ["metadatas"]),
        ("get", 3, 1, ["metadatas"]),
        ("get", 3, 2, ["metadatas"]),
        ("count",),
    ]


def test_chroma_empty_scan_confirms_terminal_page_and_count():
    calls = []

    class FakeCollection:
        def count(self):
            calls.append(("count",))
            return 0

        def get(self, *, limit, offset, include):
            calls.append(("get", limit, offset, include))
            return {"ids": [], "metadatas": []}

    assert rag._chroma_stable_id_rows(
        FakeCollection(), "book", page_size=7) == []
    assert calls == [
        ("count",),
        ("get", 7, 0, ["metadatas"]),
        ("count",),
    ]


@pytest.mark.parametrize(
    ("rows", "expected", "message"),
    [
        ([
            ("chunk_a", {"stable_id": "chunk_a"}),
            ("chunk_c", {"stable_id": "chunk_c"}),
        ], {"chunk_a", "chunk_b"}, "not found.*unexpected"),
        ([
            ("chunk_a", {"stable_id": "chunk_a"}),
            ("chunk_alias", {"stable_id": "chunk_a"}),
        ], {"chunk_a", "chunk_alias"}, "duplicate stable-ID.*mismatch"),
        ([
            ("chunk_a", None),
        ], {"chunk_a"}, "without a stable ID"),
        ([
            ("chunk_a", {"stable_id": "chunk_b"}),
        ], {"chunk_a"}, "document ID/stable ID mismatch"),
    ],
    ids=["equal-count-swap", "duplicate-metadata", "untracked", "mismatch"],
)
def test_chroma_identity_verifier_rejects_physical_drift(
        rows, expected, message):
    class FakeCollection:
        def count(self):
            return len(rows)

        def get(self, *, limit, offset, include):
            page = rows[offset:offset + limit]
            return {
                "ids": [item_id for item_id, _ in page],
                "metadatas": [metadata for _, metadata in page],
            }

    with pytest.raises(RuntimeError, match=message):
        rag._require_chroma_stable_ids(
            FakeCollection(), "book", expected, page_size=2)


@pytest.mark.parametrize("count", [None, True, -1, 1.5, "1"])
def test_chroma_scan_rejects_invalid_physical_count(count):
    class FakeCollection:
        def count(self):
            return count

        def get(self, **_kwargs):
            pytest.fail("an invalid count must fail before Chroma get")

    with pytest.raises(RuntimeError, match="invalid logical record count"):
        rag._chroma_stable_id_rows(FakeCollection(), "book")


def test_chroma_mutation_batch_size_respects_client_cap_and_fallback():
    class CappedClient:
        def get_max_batch_size(self):
            return 37

    class LegacyCappedClient:
        max_batch_size = 29

    assert rag._chroma_mutation_batch_size(CappedClient()) == 37
    assert rag._chroma_mutation_batch_size(LegacyCappedClient()) == 29
    assert rag._chroma_mutation_batch_size(
        CappedClient(), upper_bound=20) == 20
    assert rag._chroma_mutation_batch_size(object()) == 1000


@pytest.mark.parametrize("batch_size", [None, True, 0, -1, 1.5, "1"])
def test_chroma_mutation_batch_size_rejects_invalid_client_cap(batch_size):
    class InvalidClient:
        def get_max_batch_size(self):
            return batch_size

    with pytest.raises(RuntimeError, match="invalid maximum batch size"):
        rag._chroma_mutation_batch_size(InvalidClient())

    class InvalidLegacyClient:
        max_batch_size = batch_size

    with pytest.raises(RuntimeError, match="invalid maximum batch size"):
        rag._chroma_mutation_batch_size(InvalidLegacyClient())


@pytest.mark.parametrize("page_size", [None, True, 0, -1, 1.5, "1"])
def test_chroma_scan_rejects_invalid_page_size(page_size):
    class FakeCollection:
        def count(self):
            pytest.fail("an invalid page size must fail before counting")

    with pytest.raises(ValueError, match="positive integer"):
        rag._chroma_stable_id_rows(
            FakeCollection(), "book", page_size=page_size)


@pytest.mark.parametrize(
    ("count_values", "responses", "page_size", "message"),
    [
        ([1, 1], [
            {"ids": [], "metadatas": []},
        ], 1, "incomplete exact-count scan"),
        ([2], [
            {
                "ids": ["chunk_a", "chunk_b"],
                "metadatas": [
                    {"stable_id": "chunk_a"},
                    {"stable_id": "chunk_b"},
                ],
            },
        ], 1, "more records than its page limit"),
        ([1], [
            {"ids": ["chunk_a"], "metadatas": []},
        ], 1, "misaligned IDs and metadata"),
        ([1, 2], [
            {"ids": ["chunk_a"],
             "metadatas": [{"stable_id": "chunk_a"}]},
            {"ids": [], "metadatas": []},
        ], 1, "changed or returned an incomplete"),
        ([2], [
            {"ids": ["chunk_a"],
             "metadatas": [{"stable_id": "chunk_a"}]},
            {"ids": ["chunk_a"],
             "metadatas": [{"stable_id": "chunk_a"}]},
        ], 1, "repeated a document ID"),
        ([1], [
            {"ids": [None], "metadatas": [None]},
        ], 1, "invalid document ID"),
        ([1], [
            {"ids": ["chunk_a"],
             "metadatas": [{"stable_id": "chunk_a"}]},
            {"ids": ["chunk_b"],
             "metadatas": [{"stable_id": "chunk_b"}]},
        ], 1, "exceeded its logical record count"),
    ],
    ids=[
        "early-empty", "oversized", "misaligned", "count-drift",
        "repeated-id", "invalid-id", "overflow",
    ],
)
def test_chroma_scan_rejects_count_and_pagination_inconsistency(
        count_values, responses, page_size, message):
    counts = list(count_values)
    pages = list(responses)

    class FakeCollection:
        def count(self):
            return counts.pop(0)

        def get(self, *, limit, offset, include):
            return pages.pop(0)

    with pytest.raises(RuntimeError, match=message):
        rag._chroma_stable_id_rows(
            FakeCollection(), "book", page_size=page_size)


@pytest.mark.parametrize(
    "result",
    [None, [], {"ids": []}, {"metadatas": []},
     {"ids": (), "metadatas": []}],
)
def test_chroma_scan_rejects_malformed_get_result(result):
    class FakeCollection:
        def count(self):
            return 0

        def get(self, **_kwargs):
            return result

    with pytest.raises(RuntimeError, match="invalid result|omitted"):
        rag._chroma_stable_id_rows(FakeCollection(), "book")


def test_qdrant_removed_id_lookup_scrolls_every_page():
    calls = []
    count_calls = []
    pages = {
        None: ([SimpleNamespace(
            id=1, payload={"stable_id": "keep"})], "page-2"),
        "page-2": ([SimpleNamespace(
            id=2, payload={"stable_id": "remove"})], None),
    }

    class FakeClient:
        def count(self, *, collection_name, exact):
            count_calls.append((collection_name, exact))
            return SimpleNamespace(count=2)

        def scroll(self, collection_name, *, limit, offset,
                   with_payload, with_vectors):
            calls.append((collection_name, limit, offset,
                          with_payload, with_vectors))
            return pages[offset]

    point_ids = rag._qdrant_point_ids_for_stable_ids(
        FakeClient(), "book", {"remove"}, page_size=1)

    assert point_ids == [2]
    assert [call[2] for call in calls] == [None, "page-2"]
    assert count_calls == [("book", True), ("book", True)]


def test_qdrant_removed_id_lookup_empty_target_does_not_scroll():
    class FakeClient:
        def count(self, **_kwargs):
            pytest.fail("an empty removal set must not count Qdrant")

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
        def count(self, *, collection_name, exact):
            assert collection_name == "book"
            assert exact is True
            return SimpleNamespace(count=5)

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
            None: ([SimpleNamespace(id=1, payload={})], "loop"),
            "loop": ([SimpleNamespace(id=2, payload={})], "loop"),
        },
        {
            None: ([SimpleNamespace(id=1, payload={})], "offset-a"),
            "offset-a": ([SimpleNamespace(id=2, payload={})], "offset-b"),
            "offset-b": ([SimpleNamespace(id=3, payload={})], "offset-a"),
        },
    ],
    ids=["immediate", "multi-offset"],
)
def test_qdrant_removed_id_lookup_rejects_continuation_cycles(pages):
    calls = []

    class FakeClient:
        def count(self, *, collection_name, exact):
            assert exact is True
            return SimpleNamespace(count=len(pages) + 1)

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
        def count(self, *, collection_name, exact):
            assert exact is True
            return SimpleNamespace(count=2)

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
        def count(self, *, collection_name, exact):
            assert exact is True
            return SimpleNamespace(count=2)

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
        def count(self, *, collection_name, exact):
            assert exact is True
            return SimpleNamespace(count=len(points))

        def scroll(self, collection_name, *, limit, offset,
                   with_payload, with_vectors):
            return points, None

    with pytest.raises(RuntimeError, match=message):
        rag._require_qdrant_stable_ids(
            FakeClient(), "book", expected)


@pytest.mark.parametrize("count", [None, True, -1, 1.5, "1"])
def test_qdrant_scan_rejects_invalid_exact_count(count):
    class FakeClient:
        def count(self, *, collection_name, exact):
            assert exact is True
            if count is None:
                return SimpleNamespace()
            return SimpleNamespace(count=count)

        def scroll(self, *_args, **_kwargs):
            pytest.fail("an invalid exact count must fail before scrolling")

    with pytest.raises(RuntimeError, match="invalid exact point count"):
        rag._qdrant_stable_id_rows(FakeClient(), "book")


@pytest.mark.parametrize(
    "status",
    ["completed", SimpleNamespace(value="completed")],
)
def test_qdrant_update_requires_completed_status(status):
    rag._require_qdrant_update_completed(
        SimpleNamespace(status=status), "test mutation")


@pytest.mark.parametrize(
    "result",
    [
        None,
        SimpleNamespace(),
        SimpleNamespace(status=None),
        SimpleNamespace(status="acknowledged"),
        SimpleNamespace(status="wait_timeout"),
        SimpleNamespace(status="clock_rejected"),
        SimpleNamespace(status="COMPLETED"),
        SimpleNamespace(status=2),
        SimpleNamespace(status=True),
        SimpleNamespace(status=SimpleNamespace(value="acknowledged")),
    ],
)
def test_qdrant_update_rejects_noncompleted_or_missing_status(result):
    with pytest.raises(RuntimeError, match="did not report completed status"):
        rag._require_qdrant_update_completed(result, "test mutation")


def test_qdrant_update_validates_public_status_enum():
    models = pytest.importorskip("qdrant_client.http.models")

    rag._require_qdrant_update_completed(
        models.UpdateResult(
            operation_id=1, status=models.UpdateStatus.COMPLETED),
        "test mutation")
    for status in (
            models.UpdateStatus.ACKNOWLEDGED,
            models.UpdateStatus.WAIT_TIMEOUT):
        with pytest.raises(
                RuntimeError, match="did not report completed status"):
            rag._require_qdrant_update_completed(
                models.UpdateResult(operation_id=1, status=status),
                "test mutation")


def test_qdrant_empty_scan_confirms_count_before_and_after():
    calls = []

    class FakeClient:
        def count(self, *, collection_name, exact):
            calls.append(("count", collection_name, exact))
            return SimpleNamespace(count=0)

        def scroll(self, collection_name, *, limit, offset,
                   with_payload, with_vectors):
            calls.append(("scroll", offset))
            return [], None

    assert rag._qdrant_stable_id_rows(
        FakeClient(), "book", page_size=1) == []
    assert calls == [
        ("count", "book", True),
        ("scroll", None),
        ("count", "book", True),
    ]


@pytest.mark.parametrize(
    ("count_values", "pages", "page_size", "message"),
    [
        ([2, 2], {
            None: ([SimpleNamespace(id=1, payload={})], None),
        }, 2, "incomplete exact-count scroll"),
        ([1], {
            None: ([
                SimpleNamespace(id=1, payload={}),
                SimpleNamespace(id=2, payload={}),
            ], None),
        }, 2, "exceeded its exact point count"),
        ([1], {
            None: ([], "next"),
        }, 1, "empty nonterminal page"),
        ([2], {
            None: ([
                SimpleNamespace(id=1, payload={}),
                SimpleNamespace(id=2, payload={}),
            ], None),
        }, 1, "more points than its page limit"),
        ([1, 2], {
            None: ([SimpleNamespace(id=1, payload={})], None),
        }, 1, "changed or returned an incomplete"),
    ],
    ids=["short", "overflow", "empty-nonterminal", "oversized", "drift"],
)
def test_qdrant_scan_rejects_count_and_pagination_inconsistency(
        count_values, pages, page_size, message):
    counts = list(count_values)

    class FakeClient:
        def count(self, *, collection_name, exact):
            assert exact is True
            return SimpleNamespace(count=counts.pop(0))

        def scroll(self, collection_name, *, limit, offset,
                   with_payload, with_vectors):
            return pages[offset]

    with pytest.raises(RuntimeError, match=message):
        rag._qdrant_stable_id_rows(
            FakeClient(), "book", page_size=page_size)


@pytest.mark.parametrize(
    "pages",
    [
        {
            None: ([
                SimpleNamespace(id=1, payload={}),
                SimpleNamespace(id=1, payload={}),
            ], None),
        },
        {
            None: ([SimpleNamespace(id=1, payload={})], "next"),
            "next": ([SimpleNamespace(id=1, payload={})], None),
        },
    ],
    ids=["same-page", "across-pages"],
)
def test_qdrant_scan_rejects_repeated_physical_point_ids(pages):
    class FakeClient:
        def count(self, *, collection_name, exact):
            assert exact is True
            return SimpleNamespace(count=2)

        def scroll(self, collection_name, *, limit, offset,
                   with_payload, with_vectors):
            return pages[offset]

    with pytest.raises(RuntimeError, match="repeated a physical point ID"):
        rag._qdrant_stable_id_rows(FakeClient(), "book", page_size=2)


def test_qdrant_scan_normalizes_unhashable_grpc_ids_and_offsets():
    grpc_offset = _FakeGrpcPointId(num=99)
    repeated_offset = _FakeGrpcPointId(num=99)
    calls = 0

    class FakeClient:
        def count(self, *, collection_name, exact):
            assert exact is True
            return SimpleNamespace(count=3)

        def scroll(self, collection_name, *, limit, offset,
                   with_payload, with_vectors):
            nonlocal calls
            calls += 1
            if calls == 1:
                return [SimpleNamespace(id=1, payload={})], grpc_offset
            return [SimpleNamespace(id=2, payload={})], repeated_offset

    with pytest.raises(RuntimeError, match="continuation offset"):
        rag._qdrant_stable_id_rows(FakeClient(), "book", page_size=1)
    assert calls == 2

    raw_uuid = "12345678-1234-5678-1234-567812345678"
    assert rag._qdrant_id_key(7) != rag._qdrant_id_key(
        _FakeGrpcPointId(num=7))
    assert rag._qdrant_id_key(UUID(raw_uuid)) != rag._qdrant_id_key(
        _FakeGrpcPointId(uuid=raw_uuid))
    assert rag._qdrant_id_key(raw_uuid.upper()) != rag._qdrant_id_key(
        raw_uuid.replace("-", ""))


def test_qdrant_scan_rejects_logically_repeated_grpc_point_id():
    class FakeClient:
        def count(self, *, collection_name, exact):
            assert exact is True
            return SimpleNamespace(count=2)

        def scroll(self, collection_name, *, limit, offset,
                   with_payload, with_vectors):
            if offset is None:
                return [SimpleNamespace(
                    id=_FakeGrpcPointId(num=7), payload={})], "next"
            return [SimpleNamespace(
                id=_FakeGrpcPointId(num=7), payload={})], None

    with pytest.raises(RuntimeError, match="repeated a physical point ID"):
        rag._qdrant_stable_id_rows(FakeClient(), "book", page_size=1)


@pytest.mark.parametrize(
    ("value", "message"),
    [
        (True, "unsupported point ID type"),
        (object(), "unsupported point ID type"),
        (_FakeGrpcPointId(), "unset or unsupported protobuf point ID"),
    ],
)
def test_qdrant_id_key_rejects_unsupported_identifiers(value, message):
    with pytest.raises(RuntimeError, match=message):
        rag._qdrant_id_key(value)


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
    close_calls = []

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

        def close(self):
            close_calls.append(True)

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
    assert close_calls == [True]
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
        points={101: keep_point}, scroll_offsets=[], deletes=[],
        client_close_calls=0)
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

        def count(self, *, collection_name, exact):
            assert exact is True
            return SimpleNamespace(count=len(state.points))

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
            return _completed_update()

        def upsert(self, **kwargs):
            pytest.fail("a removal-only run must not upsert unchanged chunks")

        def get_collection(self, collection_name):
            return SimpleNamespace(points_count=len(state.points))

        def close(self):
            state.client_close_calls += 1

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

    outcome = rag.index_chunks_qdrant(
        fixture.chunks_path, fixture.db_path,
        collection_name="book", embedding_model="model-a")

    assert fixture.state.scroll_offsets == [
        None, "later-page", None, "later-page"]
    assert fixture.state.deletes == [[202]]
    assert fixture.state.client_close_calls == 1
    assert set(fixture.state.points) == {101}
    assert not fixture.marker_path.exists()
    manifest = json.loads(
        fixture.manifest_path.read_text(encoding="utf-8"))
    assert set(manifest["chunk_hashes"]) == {fixture.keep_stable_id}
    assert fixture.removed_stable_id not in manifest["chunk_hashes"]
    assert manifest["source_record_count"] == 1
    assert manifest["source_sha256"] == hashlib.sha256(
        fixture.chunks_path.read_bytes()).hexdigest()
    assert outcome.disposition == "updated"
    assert outcome.changed_records == 0
    assert outcome.unchanged_records == 1
    assert outcome.removed_records == 1
    assert outcome.upserted_records == 0
    assert outcome.batch_count == 0


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
    assert fixture.state.client_close_calls == 1
    assert not fixture.marker_path.exists()
    assert fixture.manifest_path.read_bytes() == original_manifest
    manifest = json.loads(original_manifest)
    assert set(manifest["chunk_hashes"]) == {
        fixture.keep_stable_id, fixture.removed_stable_id}


def _prepare_qdrant_changed_existing_incremental(
        monkeypatch, tmp_path, *, delete_mutates=True, upsert_mutates=True,
        count_values=None, delete_status="completed",
        upsert_status="completed", client_close_error=None):
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
        delete_mutates=delete_mutates, upsert_mutates=upsert_mutates,
        client_close_calls=0, lifecycle_events=[],
        count_values=(list(count_values)
                      if count_values is not None else None),
        delete_status=delete_status, upsert_status=upsert_status)

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

        def count(self, *, collection_name, exact):
            assert exact is True
            if state.count_values is not None:
                return SimpleNamespace(count=state.count_values.pop(0))
            return SimpleNamespace(count=len(state.points))

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
            return SimpleNamespace(status=state.delete_status)

        def upsert(self, *, collection_name, points, wait):
            assert wait is True
            assert marker_path.is_file()
            state.upserts.append(list(points))
            if state.upsert_mutates:
                for point in points:
                    state.points[point.id] = point
            return SimpleNamespace(status=state.upsert_status)

        def close(self):
            state.client_close_calls += 1
            state.lifecycle_events.append("client_close")
            if client_close_error is not None:
                raise client_close_error

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
        old_record=old_record,
        new_record=new_record,
        state=state,
    )


def test_qdrant_producer_failure_stops_worker_and_preserves_recovery_state(
        monkeypatch, tmp_path):
    resources = _track_queue_worker_resources(
        monkeypatch,
        shutdown_error=RuntimeError("secondary executor shutdown failure"),
        close_error=RuntimeError("secondary progress close failure"),
    )
    fixture = _prepare_qdrant_changed_existing_incremental(
        monkeypatch, tmp_path,
        client_close_error=RuntimeError(
            "secondary Qdrant client close failure"))
    original_manifest = fixture.manifest_path.read_bytes()

    def fail_index_embedding(texts, _model, **_kwargs):
        if texts == ["RAG index embedding-dimension probe"]:
            return [[0.0, 1.0]]
        raise RuntimeError("injected producer embedding failure")

    monkeypatch.setattr(rag, "_embed_texts", fail_index_embedding)

    with pytest.raises(
            RuntimeError, match="injected producer embedding failure"):
        rag.index_chunks_qdrant(
            fixture.chunks_path, fixture.db_path,
            collection_name="book", embedding_model="model-a")

    assert fixture.state.deletes == [[fixture.point_id]]
    assert fixture.state.points == {}
    assert fixture.state.upserts == []
    assert fixture.state.scrolls == 2
    assert fixture.marker_path.is_file()
    assert fixture.manifest_path.read_bytes() == original_manifest

    assert len(resources.executors) == 1
    executor = resources.executors[0]
    assert executor.shutdown_calls == [(True, False)]
    assert executor.future.done()
    assert not executor.thread.is_alive()
    assert len(resources.bars) == 1
    assert resources.bars[0].updates == 0
    assert resources.bars[0].close_calls == 1
    assert resources.events == [
        "worker_exit", "executor_shutdown", "progress_close"]
    assert fixture.state.client_close_calls == 1


@pytest.mark.parametrize("failure_stage", ["constructor", "submit"])
def test_qdrant_worker_setup_failure_closes_created_resources(
        monkeypatch, tmp_path, failure_stage):
    setup_error = RuntimeError(f"injected executor {failure_stage} failure")
    tracker_options = {
        f"{failure_stage}_error": setup_error,
    }
    resources = _track_queue_worker_resources(
        monkeypatch, **tracker_options)
    fixture = _prepare_qdrant_changed_existing_incremental(
        monkeypatch, tmp_path)
    original_manifest = fixture.manifest_path.read_bytes()

    with pytest.raises(RuntimeError) as raised:
        rag.index_chunks_qdrant(
            fixture.chunks_path, fixture.db_path,
            collection_name="book", embedding_model="model-a")

    assert raised.value is setup_error
    assert resources.executor_constructions == 1
    assert fixture.state.points == {}
    assert fixture.state.upserts == []
    assert fixture.state.scrolls == 2
    assert fixture.marker_path.is_file()
    assert fixture.manifest_path.read_bytes() == original_manifest
    assert resources.bars[0].close_calls == 1

    if failure_stage == "constructor":
        assert resources.executors == []
        assert resources.events == ["progress_close"]
    else:
        assert len(resources.executors) == 1
        assert resources.executors[0].future is None
        assert resources.executors[0].shutdown_calls == [(True, False)]
        assert resources.events == ["executor_shutdown", "progress_close"]


@pytest.mark.parametrize("failure_resource", ["shutdown", "close"])
def test_qdrant_cleanup_failure_prevents_manifest_commit(
        monkeypatch, tmp_path, failure_resource):
    cleanup_error = RuntimeError(
        f"injected {failure_resource} cleanup failure")
    tracker_options = {
        f"{failure_resource}_error": cleanup_error,
    }
    resources = _track_queue_worker_resources(
        monkeypatch, **tracker_options)
    fixture = _prepare_qdrant_changed_existing_incremental(
        monkeypatch, tmp_path)
    original_manifest = fixture.manifest_path.read_bytes()

    with pytest.raises(RuntimeError) as raised:
        rag.index_chunks_qdrant(
            fixture.chunks_path, fixture.db_path,
            collection_name="book", embedding_model="model-a")

    assert raised.value is cleanup_error
    assert len(fixture.state.upserts) == 1
    assert fixture.state.points[fixture.point_id].payload["context"] == (
        "Corrected classification")
    assert fixture.state.scrolls == 2
    assert fixture.marker_path.is_file()
    assert fixture.manifest_path.read_bytes() == original_manifest
    assert resources.executors[0].shutdown_calls == [(True, False)]
    assert resources.bars[0].close_calls == 1
    assert resources.events == [
        "worker_exit", "executor_shutdown", "progress_close"]


def test_qdrant_worker_failure_closes_resources_and_preserves_manifest(
        monkeypatch, tmp_path):
    resources = _track_queue_worker_resources(monkeypatch)
    fixture = _prepare_qdrant_changed_existing_incremental(
        monkeypatch, tmp_path)
    original_manifest = fixture.manifest_path.read_bytes()
    client_type = sys.modules["qdrant_client"].QdrantClient

    def ambiguous_upsert(self, *, collection_name, points, wait):
        assert wait is True
        assert fixture.marker_path.is_file()
        fixture.state.upserts.append(list(points))
        for point in points:
            fixture.state.points[point.id] = point
        raise RuntimeError("injected Qdrant upsert failure")

    monkeypatch.setattr(client_type, "upsert", ambiguous_upsert)

    # An unrelated outer handler must not make cleanup mistake its exception
    # for an active producer failure and suppress the worker error.
    try:
        raise ValueError("unrelated outer error")
    except ValueError:
        with pytest.raises(
                RuntimeError, match="injected Qdrant upsert failure"):
            rag.index_chunks_qdrant(
                fixture.chunks_path, fixture.db_path,
                collection_name="book", embedding_model="model-a")

    assert len(fixture.state.upserts) == 1
    assert fixture.state.points[fixture.point_id].payload["context"] == (
        "Corrected classification")
    assert fixture.state.scrolls == 2
    assert fixture.marker_path.is_file()
    assert fixture.manifest_path.read_bytes() == original_manifest

    executor = resources.executors[0]
    assert executor.shutdown_calls == [(True, False)]
    assert executor.future.done()
    assert not executor.thread.is_alive()
    assert resources.bars[0].updates == 0
    assert resources.bars[0].close_calls == 1
    assert resources.events == [
        "worker_exit", "executor_shutdown", "progress_close"]


def test_qdrant_cleanup_does_not_mask_active_producer_failure(
        monkeypatch, tmp_path):
    resources = _track_queue_worker_resources(monkeypatch)
    fixture = _prepare_qdrant_changed_existing_incremental(
        monkeypatch, tmp_path)
    original_manifest = fixture.manifest_path.read_bytes()
    client_type = sys.modules["qdrant_client"].QdrantClient

    def fail_upsert(self, *, collection_name, points, wait):
        assert wait is True
        fixture.state.upserts.append(list(points))
        raise RuntimeError("secondary worker failure")

    monkeypatch.setattr(client_type, "upsert", fail_upsert)
    monkeypatch.setattr(
        rag, "_batch_index_records",
        lambda records, _model, **_kwargs: [list(records), list(records)],
    )
    index_embedding_calls = 0

    def fail_second_index_embedding(texts, _model, **_kwargs):
        nonlocal index_embedding_calls
        if texts == ["RAG index embedding-dimension probe"]:
            return [[0.0, 1.0]]
        index_embedding_calls += 1
        if index_embedding_calls == 1:
            return [[0.0, 1.0] for _text in texts]
        raise RuntimeError("primary producer failure")

    monkeypatch.setattr(rag, "_embed_texts", fail_second_index_embedding)

    with pytest.raises(RuntimeError, match="primary producer failure"):
        rag.index_chunks_qdrant(
            fixture.chunks_path, fixture.db_path,
            collection_name="book", embedding_model="model-a")

    assert len(fixture.state.upserts) == 1
    assert fixture.marker_path.is_file()
    assert fixture.manifest_path.read_bytes() == original_manifest
    assert resources.executors[0].shutdown_calls == [(True, False)]
    assert not resources.executors[0].thread.is_alive()
    assert resources.bars[0].close_calls == 1


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


def test_qdrant_noncompleted_delete_status_stops_before_reconciliation(
        monkeypatch, tmp_path):
    fixture = _prepare_qdrant_changed_existing_incremental(
        monkeypatch, tmp_path, delete_status="acknowledged")
    original_manifest = fixture.manifest_path.read_bytes()

    with pytest.raises(RuntimeError, match="point deletion.*completed status"):
        rag.index_chunks_qdrant(
            fixture.chunks_path, fixture.db_path,
            collection_name="book", embedding_model="model-a")

    assert fixture.state.points == {}
    assert fixture.state.scrolls == 1
    assert fixture.state.upserts == []
    assert fixture.marker_path.is_file()
    assert fixture.manifest_path.read_bytes() == original_manifest


def test_qdrant_noncompleted_upsert_status_stops_before_final_reconciliation(
        monkeypatch, tmp_path):
    fixture = _prepare_qdrant_changed_existing_incremental(
        monkeypatch, tmp_path, upsert_status="wait_timeout")
    original_manifest = fixture.manifest_path.read_bytes()

    with pytest.raises(RuntimeError, match="point upsert.*completed status"):
        rag.index_chunks_qdrant(
            fixture.chunks_path, fixture.db_path,
            collection_name="book", embedding_model="model-a")

    assert fixture.state.points[fixture.point_id].payload["context"] == (
        "Corrected classification")
    assert fixture.state.scrolls == 2
    assert len(fixture.state.upserts) == 1
    assert fixture.marker_path.is_file()
    assert fixture.manifest_path.read_bytes() == original_manifest


def test_qdrant_postwrite_count_drift_keeps_recovery_marker_and_old_manifest(
        monkeypatch, tmp_path):
    fixture = _prepare_qdrant_changed_existing_incremental(
        monkeypatch, tmp_path,
        count_values=[1, 1, 0, 0, 1, 2])
    original_manifest = fixture.manifest_path.read_bytes()

    with pytest.raises(RuntimeError, match="changed or returned an incomplete"):
        rag.index_chunks_qdrant(
            fixture.chunks_path, fixture.db_path,
            collection_name="book", embedding_model="model-a")

    assert fixture.state.count_values == []
    assert fixture.state.points[fixture.point_id].payload["context"] == (
        "Corrected classification")
    assert fixture.marker_path.is_file()
    assert fixture.manifest_path.read_bytes() == original_manifest


def test_qdrant_changed_existing_is_deleted_then_replaced_before_manifest_save(
        monkeypatch, tmp_path):
    fixture = _prepare_qdrant_changed_existing_incremental(
        monkeypatch, tmp_path)
    save_manifest = rag._save_index_manifest

    def save_after_client_close(*args, **kwargs):
        assert fixture.state.lifecycle_events == ["client_close"]
        return save_manifest(*args, **kwargs)

    monkeypatch.setattr(rag, "_save_index_manifest", save_after_client_close)

    outcome = rag.index_chunks_qdrant(
        fixture.chunks_path, fixture.db_path,
        collection_name="book", embedding_model="model-a")

    assert fixture.state.deletes == [[fixture.point_id]]
    assert len(fixture.state.upserts) == 1
    assert fixture.state.scrolls == 3
    assert fixture.state.client_close_calls == 1
    assert not fixture.marker_path.exists()
    assert fixture.state.points[fixture.point_id].payload["context"] == (
        "Corrected classification")
    manifest = json.loads(
        fixture.manifest_path.read_text(encoding="utf-8"))
    assert manifest["chunk_hashes"] == {
        fixture.stable_id: fixture.new_hash}
    assert manifest["source_sha256"] == hashlib.sha256(
        fixture.chunks_path.read_bytes()).hexdigest()
    assert outcome.disposition == "updated"
    assert outcome.changed_records == 1
    assert outcome.unchanged_records == 0
    assert outcome.removed_records == 0
    assert outcome.upserted_records == 1
    assert outcome.batch_count == 1


def test_qdrant_client_close_failure_prevents_manifest_commit(
        monkeypatch, tmp_path):
    close_error = RuntimeError("injected Qdrant client close failure")
    fixture = _prepare_qdrant_changed_existing_incremental(
        monkeypatch, tmp_path, client_close_error=close_error)
    original_manifest = fixture.manifest_path.read_bytes()

    with pytest.raises(RuntimeError) as raised:
        rag.index_chunks_qdrant(
            fixture.chunks_path, fixture.db_path,
            collection_name="book", embedding_model="model-a")

    assert raised.value is close_error
    assert fixture.state.client_close_calls == 1
    assert fixture.state.points[fixture.point_id].payload["context"] == (
        "Corrected classification")
    assert fixture.marker_path.is_file()
    assert fixture.manifest_path.read_bytes() == original_manifest


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

    def fail_marker_cleanup(marker_path, **marker_kwargs):
        assert marker_kwargs["owner_token"]
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


def _prepare_chroma_changed_existing_incremental(
        monkeypatch, tmp_path, *, embedding_model="model-a",
        delete_mutates=True, upsert_mutates=True, count_values=None,
        client_close_error=None):
    chunks_path = tmp_path / "chunks.jsonl"
    db_path = tmp_path / "chroma"
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
    manifest_path = rag._save_index_manifest(
        db_path, backend="chroma", collection_name="book",
        embedding_model=embedding_model, embedding_dimension=2,
        chunk_hashes={stable_id: old_hash}, source_sha256="old-source",
        source_record_count=1)
    marker_path = rag._chroma_update_marker_path(
        db_path, collection_name="book")
    state = SimpleNamespace(
        upserts=[], deletes=[], collection_deletes=[], collection_creates=[],
        collections={}, gets=[], delete_mutates=delete_mutates,
        upsert_mutates=upsert_mutates,
        client_close_calls=0, lifecycle_events=[],
        count_values=(list(count_values)
                      if count_values is not None else None))

    class FakeCollection:
        def __init__(self, name, rows=None):
            self.name = name
            self.rows = dict(rows or {})

        def upsert(self, *, ids, embeddings, documents, metadatas):
            assert marker_path.is_file()
            state.upserts.append(list(ids))
            if state.upsert_mutates:
                for index, item_id in enumerate(ids):
                    self.rows[item_id] = {
                        "embedding": embeddings[index],
                        "document": documents[index],
                        "metadata": metadatas[index],
                    }

        def delete(self, *, ids):
            assert marker_path.is_file()
            state.deletes.append(list(ids))
            if state.delete_mutates:
                for item_id in ids:
                    self.rows.pop(item_id, None)

        def count(self):
            if state.count_values is not None:
                return state.count_values.pop(0)
            return len(self.rows)

        def get(self, *, limit, offset, include):
            assert include == ["metadatas"]
            state.gets.append((self.name, limit, offset))
            ids = list(self.rows)[offset:offset + limit]
            return {
                "ids": ids,
                "metadatas": [
                    self.rows[item_id].get("metadata")
                    if isinstance(self.rows[item_id], dict) else None
                    for item_id in ids
                ],
            }

    collection = FakeCollection(
        "book",
        {
            stable_id: {
                    "embedding": [1.0, 0.0],
                    "document": old_record["text"],
                    "metadata": {
                        **old_record["metadata"],
                        "stable_id": stable_id,
                    },
            },
        },
    )
    sibling = FakeCollection("sibling", {"keep": "untouched"})
    state.collections.update(book=collection, sibling=sibling)

    class FakeChromaClient:
        def __init__(self, path):
            self.path = path

        def get_collection(self, name):
            if name not in state.collections:
                raise LookupError(name)
            return state.collections[name]

        def get_or_create_collection(self, *, name, metadata):
            assert name == "book"
            if name not in state.collections:
                assert marker_path.is_file()
                state.collection_creates.append(name)
                state.collections[name] = collection
            return state.collections[name]

        def delete_collection(self, name):
            assert marker_path.is_file()
            state.collection_deletes.append(name)
            state.collections.pop(name).rows.clear()

        def close(self):
            state.client_close_calls += 1
            state.lifecycle_events.append("client_close")
            if client_close_error is not None:
                raise client_close_error

    chromadb = ModuleType("chromadb")
    chromadb.PersistentClient = FakeChromaClient
    monkeypatch.setitem(sys.modules, "chromadb", chromadb)
    monkeypatch.setattr(
        rag, "_count_embedding_text_tokens",
        lambda texts, _model: ([7 for _text in texts], True))

    def successful_embed(texts, _model, **_kwargs):
        return [[0.0, 1.0] for _text in texts]

    monkeypatch.setattr(rag, "_embed_texts", successful_embed)

    return SimpleNamespace(
        chunks_path=chunks_path,
        db_path=db_path,
        embedding_model=embedding_model,
        manifest_path=manifest_path,
        marker_path=marker_path,
        stable_id=stable_id,
        old_hash=old_hash,
        new_hash=new_hash,
        old_record=old_record,
        new_record=new_record,
        collection=collection,
        sibling=sibling,
        successful_embed=successful_embed,
        state=state,
    )


@pytest.mark.parametrize("backend", ["chroma", "qdrant"])
def test_index_manifest_and_vectors_use_one_chunks_byte_snapshot(
        monkeypatch, tmp_path, backend):
    if backend == "chroma":
        fixture = _prepare_chroma_changed_existing_incremental(
            monkeypatch, tmp_path)
    else:
        fixture = _prepare_qdrant_changed_existing_incremental(
            monkeypatch, tmp_path)

    generation_a = fixture.chunks_path.read_bytes()
    generation_a_sha256 = hashlib.sha256(generation_a).hexdigest()
    replacement = {
        **fixture.new_record,
        "metadata": {
            **fixture.new_record["metadata"],
            "context": "Racing replacement generation",
        },
    }
    parse_snapshot = rag._parse_index_records_strict
    replaced = False

    def replace_path_then_parse(raw, path):
        nonlocal replaced
        if not replaced:
            replaced = True
            rag._atomic_write_jsonl(path, [replacement])
        return parse_snapshot(raw, path)

    monkeypatch.setattr(
        rag, "_parse_index_records_strict", replace_path_then_parse)
    if backend == "chroma":
        rag.index_chunks(
            fixture.chunks_path, fixture.db_path,
            collection_name="book", embedding_model="model-a")
        persisted_context = fixture.collection.rows[
            fixture.stable_id]["metadata"]["context"]
    else:
        rag.index_chunks_qdrant(
            fixture.chunks_path, fixture.db_path,
            collection_name="book", embedding_model="model-a")
        persisted_context = fixture.state.points[
            fixture.point_id].payload["context"]

    manifest = json.loads(
        fixture.manifest_path.read_text(encoding="utf-8"))
    assert replaced is True
    assert manifest["source_sha256"] == generation_a_sha256
    assert manifest["chunk_hashes"] == {
        fixture.stable_id: fixture.new_hash}
    assert persisted_context == "Corrected classification"
    assert json.loads(fixture.chunks_path.read_text(encoding="utf-8"))[
        "metadata"]["context"] == "Racing replacement generation"


@pytest.mark.parametrize("backend", ["chroma", "qdrant"])
def test_indexer_cannot_clean_marker_replaced_after_manifest_commit(
        monkeypatch, tmp_path, backend):
    if backend == "chroma":
        fixture = _prepare_chroma_changed_existing_incremental(
            monkeypatch, tmp_path)
        run_index = rag.index_chunks
    else:
        fixture = _prepare_qdrant_changed_existing_incremental(
            monkeypatch, tmp_path)
        run_index = rag.index_chunks_qdrant
    save_manifest = rag._save_index_manifest
    replacement_token = "replacement-update-token"

    def replace_marker_after_save(*args, **kwargs):
        result = save_manifest(*args, **kwargs)
        rag._begin_index_update(
            fixture.db_path, backend=backend, collection_name="book",
            source_sha256="replacement-generation", source_record_count=1,
            owner_token=replacement_token, replace_existing=True)
        return result

    monkeypatch.setattr(rag, "_save_index_manifest", replace_marker_after_save)
    with pytest.raises(RuntimeError, match="ownership changed"):
        run_index(
            fixture.chunks_path, fixture.db_path,
            collection_name="book", embedding_model="model-a")

    marker = json.loads(fixture.marker_path.read_text(encoding="utf-8"))
    assert marker["owner_token"] == replacement_token
    assert marker["target_source_sha256"] == "replacement-generation"


def test_chroma_marker_write_failure_prevents_first_mutation(
        monkeypatch, tmp_path):
    fixture = _prepare_chroma_changed_existing_incremental(
        monkeypatch, tmp_path)
    original_manifest = fixture.manifest_path.read_bytes()

    def fail_marker_write(*_args, **_kwargs):
        raise OSError("injected Chroma marker write failure")

    monkeypatch.setattr(rag, "_atomic_write_json", fail_marker_write)
    with pytest.raises(OSError, match="Chroma marker write failure"):
        rag.index_chunks(
            fixture.chunks_path, fixture.db_path,
            collection_name="book", embedding_model="model-a")

    assert fixture.state.upserts == []
    assert fixture.state.deletes == []
    assert fixture.state.collection_deletes == []
    assert fixture.state.collection_creates == []
    assert fixture.collection.rows[fixture.stable_id]["metadata"][
        "context"] == "Old classification"
    assert fixture.manifest_path.read_bytes() == original_manifest
    assert not fixture.marker_path.exists()


def test_chroma_preflight_drift_fails_before_marker_or_mutation(
        monkeypatch, tmp_path):
    fixture = _prepare_chroma_changed_existing_incremental(
        monkeypatch, tmp_path)
    original_manifest = fixture.manifest_path.read_bytes()
    unexpected_id = "chunk_unexpected"
    fixture.collection.rows[unexpected_id] = {
        "embedding": [1.0, 0.0],
        "document": "Untracked physical record",
        "metadata": {"stable_id": unexpected_id},
    }

    with pytest.raises(RuntimeError, match="unexpected stable ID"):
        rag.index_chunks(
            fixture.chunks_path, fixture.db_path,
            collection_name="book", embedding_model="model-a")

    assert fixture.state.deletes == []
    assert fixture.state.upserts == []
    assert not fixture.marker_path.exists()
    assert fixture.manifest_path.read_bytes() == original_manifest


def test_chroma_compatible_empty_manifest_rejects_physical_records(
        monkeypatch, tmp_path):
    fixture = _prepare_chroma_changed_existing_incremental(
        monkeypatch, tmp_path)
    rag._save_index_manifest(
        fixture.db_path, backend="chroma", collection_name="book",
        embedding_model="model-a", embedding_dimension=2,
        chunk_hashes={}, source_sha256="empty-source",
        source_record_count=0)
    original_manifest = fixture.manifest_path.read_bytes()

    with pytest.raises(RuntimeError, match="unexpected stable ID"):
        rag.index_chunks(
            fixture.chunks_path, fixture.db_path,
            collection_name="book", embedding_model="model-a")

    assert fixture.state.deletes == []
    assert fixture.state.upserts == []
    assert not fixture.marker_path.exists()
    assert fixture.manifest_path.read_bytes() == original_manifest


def test_chroma_changed_existing_delete_noop_preserves_old_manifest(
        monkeypatch, tmp_path):
    fixture = _prepare_chroma_changed_existing_incremental(
        monkeypatch, tmp_path, delete_mutates=False)
    original_manifest = fixture.manifest_path.read_bytes()

    with pytest.raises(RuntimeError, match="unexpected stable ID"):
        rag.index_chunks(
            fixture.chunks_path, fixture.db_path,
            collection_name="book", embedding_model="model-a")

    assert fixture.state.deletes == [[fixture.stable_id]]
    assert fixture.state.upserts == []
    assert fixture.collection.rows[fixture.stable_id]["metadata"][
        "context"] == "Old classification"
    assert fixture.marker_path.is_file()
    assert fixture.manifest_path.read_bytes() == original_manifest


def test_chroma_partial_delete_preserves_old_manifest(monkeypatch, tmp_path):
    fixture = _prepare_chroma_changed_existing_incremental(
        monkeypatch, tmp_path)
    removed_record = {
        "text": "A second stale record must also be deleted.",
        "metadata": {
            "chunk_index": 1,
            "context": "Obsolete doctrine",
            "embedding_token_count": 7,
        },
    }
    removed_id = rag._chunk_id(removed_record)
    fixture.collection.rows[removed_id] = {
        "embedding": [1.0, 0.0],
        "document": removed_record["text"],
        "metadata": {
            **removed_record["metadata"],
            "stable_id": removed_id,
        },
    }
    rag._save_index_manifest(
        fixture.db_path, backend="chroma", collection_name="book",
        embedding_model="model-a", embedding_dimension=2,
        chunk_hashes={
            fixture.stable_id: fixture.old_hash,
            removed_id: rag._chunk_hash(removed_record),
        },
        source_sha256="old-source", source_record_count=2)
    original_manifest = fixture.manifest_path.read_bytes()
    delete = fixture.collection.delete
    requested_deletions = []

    def partial_delete(*, ids):
        requested_deletions.append(list(ids))
        delete(ids=ids[:1])

    monkeypatch.setattr(fixture.collection, "delete", partial_delete)
    with pytest.raises(RuntimeError, match="unexpected stable ID"):
        rag.index_chunks(
            fixture.chunks_path, fixture.db_path,
            collection_name="book", embedding_model="model-a")

    assert requested_deletions == [[removed_id, fixture.stable_id]]
    assert fixture.state.deletes == [[removed_id]]
    assert set(fixture.collection.rows) == {fixture.stable_id}
    assert fixture.state.upserts == []
    assert fixture.marker_path.is_file()
    assert fixture.manifest_path.read_bytes() == original_manifest


def test_chroma_changed_existing_upsert_noop_preserves_old_manifest(
        monkeypatch, tmp_path):
    fixture = _prepare_chroma_changed_existing_incremental(
        monkeypatch, tmp_path, upsert_mutates=False)
    original_manifest = fixture.manifest_path.read_bytes()

    with pytest.raises(RuntimeError, match="stable ID.*not found"):
        rag.index_chunks(
            fixture.chunks_path, fixture.db_path,
            collection_name="book", embedding_model="model-a")

    assert fixture.state.deletes == [[fixture.stable_id]]
    assert fixture.state.upserts == [[fixture.stable_id]]
    assert fixture.collection.rows == {}
    assert fixture.marker_path.is_file()
    assert fixture.manifest_path.read_bytes() == original_manifest


def test_chroma_new_record_upsert_noop_preserves_old_manifest(
        monkeypatch, tmp_path):
    fixture = _prepare_chroma_changed_existing_incremental(
        monkeypatch, tmp_path, upsert_mutates=False)
    original_manifest = fixture.manifest_path.read_bytes()
    added_record = {
        "text": "A newly added rule must become a physical record.",
        "metadata": {
            "chunk_index": 1,
            "context": "New doctrine",
            "embedding_token_count": 7,
        },
    }
    added_id = rag._chunk_id(added_record)
    fixture.chunks_path.write_text(
        json.dumps(fixture.old_record) + "\n"
        + json.dumps(added_record) + "\n",
        encoding="utf-8",
    )

    with pytest.raises(RuntimeError, match="stable ID.*not found"):
        rag.index_chunks(
            fixture.chunks_path, fixture.db_path,
            collection_name="book", embedding_model="model-a")

    assert fixture.state.deletes == []
    assert fixture.state.upserts == [[added_id]]
    assert set(fixture.collection.rows) == {fixture.stable_id}
    assert fixture.marker_path.is_file()
    assert fixture.manifest_path.read_bytes() == original_manifest


def test_chroma_postwrite_count_drift_preserves_old_manifest(
        monkeypatch, tmp_path):
    fixture = _prepare_chroma_changed_existing_incremental(
        monkeypatch, tmp_path,
        count_values=[1, 1, 0, 0, 1, 2])
    original_manifest = fixture.manifest_path.read_bytes()

    with pytest.raises(RuntimeError, match="changed or returned an incomplete"):
        rag.index_chunks(
            fixture.chunks_path, fixture.db_path,
            collection_name="book", embedding_model="model-a")

    assert fixture.state.count_values == []
    assert fixture.state.deletes == [[fixture.stable_id]]
    assert fixture.state.upserts == [[fixture.stable_id]]
    assert fixture.collection.rows[fixture.stable_id]["metadata"][
        "context"] == "Corrected classification"
    assert fixture.marker_path.is_file()
    assert fixture.manifest_path.read_bytes() == original_manifest


def test_chroma_changed_existing_is_deleted_then_replaced_before_manifest_save(
        monkeypatch, tmp_path):
    fixture = _prepare_chroma_changed_existing_incremental(
        monkeypatch, tmp_path)
    require_stable_ids = rag._require_chroma_stable_ids
    save_manifest = rag._save_index_manifest
    finish_update = rag._finish_index_update
    events = []

    def reconcile(collection, collection_name, expected, **kwargs):
        result = require_stable_ids(
            collection, collection_name, expected, **kwargs)
        events.append(("reconcile", frozenset(expected)))
        return result

    def save_after_reconciliation(*args, **kwargs):
        assert events == [
            ("reconcile", frozenset({fixture.stable_id})),
            ("reconcile", frozenset()),
            ("reconcile", frozenset({fixture.stable_id})),
        ]
        assert fixture.state.lifecycle_events == ["client_close"]
        events.append(("manifest_save", None))
        return save_manifest(*args, **kwargs)

    def finish_after_manifest(marker_path, **marker_kwargs):
        assert events[-1] == ("manifest_save", None)
        events.append(("marker_cleanup", None))
        result = finish_update(marker_path, **marker_kwargs)

        def fail_postcommit_count():
            pytest.fail(
                "a committed Chroma update must not perform another count")

        monkeypatch.setattr(
            fixture.collection, "count", fail_postcommit_count)
        return result

    monkeypatch.setattr(rag, "_require_chroma_stable_ids", reconcile)
    monkeypatch.setattr(rag, "_save_index_manifest", save_after_reconciliation)
    monkeypatch.setattr(rag, "_finish_index_update", finish_after_manifest)

    outcome = rag.index_chunks(
        fixture.chunks_path, fixture.db_path,
        collection_name="book", embedding_model="model-a")

    assert fixture.state.deletes == [[fixture.stable_id]]
    assert fixture.state.upserts == [[fixture.stable_id]]
    assert fixture.collection.rows[fixture.stable_id]["metadata"][
        "context"] == "Corrected classification"
    assert not fixture.marker_path.exists()
    manifest = json.loads(
        fixture.manifest_path.read_text(encoding="utf-8"))
    assert manifest["chunk_hashes"] == {
        fixture.stable_id: fixture.new_hash}
    assert events[-2:] == [
        ("manifest_save", None), ("marker_cleanup", None)]
    assert fixture.state.client_close_calls == 1
    assert outcome.disposition == "updated"
    assert outcome.changed_records == 1
    assert outcome.unchanged_records == 0
    assert outcome.removed_records == 0
    assert outcome.upserted_records == 1
    assert outcome.batch_count == 1


def test_chroma_client_close_failure_prevents_manifest_commit(
        monkeypatch, tmp_path):
    close_error = RuntimeError("injected Chroma client close failure")
    fixture = _prepare_chroma_changed_existing_incremental(
        monkeypatch, tmp_path, client_close_error=close_error)
    original_manifest = fixture.manifest_path.read_bytes()

    with pytest.raises(RuntimeError) as raised:
        rag.index_chunks(
            fixture.chunks_path, fixture.db_path,
            collection_name="book", embedding_model="model-a")

    assert raised.value is close_error
    assert fixture.state.client_close_calls == 1
    assert fixture.collection.rows[fixture.stable_id]["metadata"][
        "context"] == "Corrected classification"
    assert fixture.marker_path.is_file()
    assert fixture.manifest_path.read_bytes() == original_manifest


def test_chroma_client_close_does_not_mask_index_failure(
        monkeypatch, tmp_path):
    close_error = RuntimeError("secondary Chroma client close failure")
    index_error = RuntimeError("primary Chroma embedding failure")
    fixture = _prepare_chroma_changed_existing_incremental(
        monkeypatch, tmp_path, client_close_error=close_error)
    original_manifest = fixture.manifest_path.read_bytes()

    def fail_index_embedding(texts, _model, **_kwargs):
        if texts == ["RAG index embedding-dimension probe"]:
            return [[0.0, 1.0]]
        raise index_error

    monkeypatch.setattr(rag, "_embed_texts", fail_index_embedding)
    with pytest.raises(RuntimeError) as raised:
        rag.index_chunks(
            fixture.chunks_path, fixture.db_path,
            collection_name="book", embedding_model="model-a")

    assert raised.value is index_error
    assert fixture.state.client_close_calls == 1
    assert fixture.state.upserts == []
    assert fixture.marker_path.is_file()
    assert fixture.manifest_path.read_bytes() == original_manifest


def test_chroma_deletions_respect_client_batch_limit(monkeypatch, tmp_path):
    fixture = _prepare_chroma_changed_existing_incremental(
        monkeypatch, tmp_path)
    removed_record = {
        "text": "This obsolete record must be deleted in its own batch.",
        "metadata": {
            "chunk_index": 1,
            "context": "Obsolete doctrine",
            "embedding_token_count": 7,
        },
    }
    removed_id = rag._chunk_id(removed_record)
    fixture.collection.rows[removed_id] = {
        "embedding": [1.0, 0.0],
        "document": removed_record["text"],
        "metadata": {
            **removed_record["metadata"],
            "stable_id": removed_id,
        },
    }
    rag._save_index_manifest(
        fixture.db_path, backend="chroma", collection_name="book",
        embedding_model="model-a", embedding_dimension=2,
        chunk_hashes={
            fixture.stable_id: fixture.old_hash,
            removed_id: rag._chunk_hash(removed_record),
        },
        source_sha256="old-source", source_record_count=2)
    client_type = sys.modules["chromadb"].PersistentClient
    monkeypatch.setattr(
        client_type, "get_max_batch_size", lambda _self: 1, raising=False)

    rag.index_chunks(
        fixture.chunks_path, fixture.db_path,
        collection_name="book", embedding_model="model-a")

    assert fixture.state.deletes == [[removed_id], [fixture.stable_id]]
    assert fixture.state.upserts == [[fixture.stable_id]]
    assert set(fixture.collection.rows) == {fixture.stable_id}
    assert not fixture.marker_path.exists()


def test_chroma_parallel_embedding_failure_preserves_primary_and_closes_progress(
        monkeypatch, tmp_path):
    embedding_error = RuntimeError("primary parallel embedding failure")
    shutdown_error = RuntimeError("secondary parallel executor shutdown failure")
    close_error = RuntimeError("secondary parallel progress close failure")
    resources = _track_parallel_resources(
        monkeypatch, shutdown_error=shutdown_error,
        close_error=close_error)
    fixture = _prepare_chroma_changed_existing_incremental(
        monkeypatch, tmp_path, embedding_model="text-embedding-test")
    original_manifest = fixture.manifest_path.read_bytes()

    def fail_index_embedding(texts, _model, **_kwargs):
        if texts == ["RAG index embedding-dimension probe"]:
            return [[0.0, 1.0]]
        raise embedding_error

    monkeypatch.setattr(rag, "_embed_texts", fail_index_embedding)
    with pytest.raises(RuntimeError) as raised:
        rag.index_chunks(
            fixture.chunks_path, fixture.db_path,
            collection_name="book",
            embedding_model=fixture.embedding_model)

    assert raised.value is embedding_error
    assert fixture.state.upserts == []
    assert fixture.marker_path.is_file()
    assert fixture.manifest_path.read_bytes() == original_manifest
    assert resources.futures[0].exception() is embedding_error
    assert resources.executors[0].shutdown_calls == [(True, False)]
    assert resources.events == ["executor_shutdown", "progress_close"]
    assert len(resources.bars) == 1
    assert resources.bars[0].updates == 0
    assert resources.bars[0].close_calls == 1


def test_chroma_parallel_upsert_failure_closes_progress(
        monkeypatch, tmp_path):
    resources = _track_parallel_resources(monkeypatch)
    fixture = _prepare_chroma_changed_existing_incremental(
        monkeypatch, tmp_path, embedding_model="text-embedding-test")
    original_manifest = fixture.manifest_path.read_bytes()
    successful_upsert = fixture.collection.upsert
    upsert_error = RuntimeError("injected parallel Chroma upsert failure")

    def ambiguous_upsert(*, ids, embeddings, documents, metadatas):
        successful_upsert(
            ids=ids, embeddings=embeddings,
            documents=documents, metadatas=metadatas)
        raise upsert_error

    monkeypatch.setattr(fixture.collection, "upsert", ambiguous_upsert)
    with pytest.raises(RuntimeError) as raised:
        rag.index_chunks(
            fixture.chunks_path, fixture.db_path,
            collection_name="book",
            embedding_model=fixture.embedding_model)

    assert raised.value is upsert_error
    assert fixture.state.upserts == [[fixture.stable_id]]
    assert fixture.marker_path.is_file()
    assert fixture.manifest_path.read_bytes() == original_manifest
    assert resources.executors[0].shutdown_calls == [(True, False)]
    assert resources.events == ["executor_shutdown", "progress_close"]
    assert resources.bars[0].updates == 0
    assert resources.bars[0].close_calls == 1


def test_chroma_parallel_close_failure_prevents_manifest_commit(
        monkeypatch, tmp_path):
    close_error = RuntimeError("injected parallel progress close failure")
    resources = _track_parallel_resources(
        monkeypatch, close_error=close_error)
    fixture = _prepare_chroma_changed_existing_incremental(
        monkeypatch, tmp_path, embedding_model="text-embedding-test")
    original_manifest = fixture.manifest_path.read_bytes()

    try:
        raise ValueError("unrelated outer error")
    except ValueError:
        with pytest.raises(RuntimeError) as raised:
            rag.index_chunks(
                fixture.chunks_path, fixture.db_path,
                collection_name="book",
                embedding_model=fixture.embedding_model)

    assert raised.value is close_error
    assert fixture.state.upserts == [[fixture.stable_id]]
    assert fixture.marker_path.is_file()
    assert fixture.manifest_path.read_bytes() == original_manifest
    assert resources.executors[0].shutdown_calls == [(True, False)]
    assert resources.events == ["executor_shutdown", "progress_close"]
    assert resources.bars[0].updates == 1
    assert resources.bars[0].close_calls == 1


def test_chroma_parallel_executor_constructor_failure_closes_progress(
        monkeypatch, tmp_path):
    setup_error = RuntimeError("injected parallel executor setup failure")
    close_error = RuntimeError("secondary parallel progress close failure")
    resources = _track_parallel_resources(
        monkeypatch, constructor_error=setup_error, close_error=close_error)
    fixture = _prepare_chroma_changed_existing_incremental(
        monkeypatch, tmp_path, embedding_model="text-embedding-test")
    original_manifest = fixture.manifest_path.read_bytes()

    with pytest.raises(RuntimeError) as raised:
        rag.index_chunks(
            fixture.chunks_path, fixture.db_path,
            collection_name="book",
            embedding_model=fixture.embedding_model)

    assert raised.value is setup_error
    assert fixture.state.upserts == []
    assert fixture.marker_path.is_file()
    assert fixture.manifest_path.read_bytes() == original_manifest
    assert resources.executor_constructions == 1
    assert resources.executors == []
    assert resources.events == ["progress_close"]
    assert resources.bars[0].updates == 0
    assert resources.bars[0].close_calls == 1


def test_chroma_parallel_shutdown_failure_precedes_close_failure(
        monkeypatch, tmp_path):
    shutdown_error = RuntimeError("injected parallel executor shutdown failure")
    close_error = RuntimeError("secondary parallel progress close failure")
    resources = _track_parallel_resources(
        monkeypatch, shutdown_error=shutdown_error,
        close_error=close_error)
    fixture = _prepare_chroma_changed_existing_incremental(
        monkeypatch, tmp_path, embedding_model="text-embedding-test")
    original_manifest = fixture.manifest_path.read_bytes()

    with pytest.raises(RuntimeError) as raised:
        rag.index_chunks(
            fixture.chunks_path, fixture.db_path,
            collection_name="book",
            embedding_model=fixture.embedding_model)

    assert raised.value is shutdown_error
    assert fixture.state.upserts == [[fixture.stable_id]]
    assert fixture.marker_path.is_file()
    assert fixture.manifest_path.read_bytes() == original_manifest
    assert resources.executors[0].shutdown_calls == [(True, False)]
    assert resources.events == ["executor_shutdown", "progress_close"]
    assert resources.bars[0].updates == 1
    assert resources.bars[0].close_calls == 1


def test_chroma_parallel_cleanup_precedes_manifest_and_marker_commit(
        monkeypatch, tmp_path):
    resources = _track_parallel_resources(monkeypatch)
    fixture = _prepare_chroma_changed_existing_incremental(
        monkeypatch, tmp_path, embedding_model="text-embedding-test")
    original_save_manifest = rag._save_index_manifest
    original_finish_update = rag._finish_index_update

    def save_manifest(*args, **kwargs):
        assert resources.events == ["executor_shutdown", "progress_close"]
        assert fixture.marker_path.is_file()
        assert resources.bars[0].updates == 1
        assert resources.bars[0].close_calls == 1
        resources.events.append("manifest_save")
        return original_save_manifest(*args, **kwargs)

    def finish_update(marker_path, **marker_kwargs):
        assert marker_path == fixture.marker_path
        assert resources.events == [
            "executor_shutdown", "progress_close", "manifest_save"]
        manifest = json.loads(
            fixture.manifest_path.read_text(encoding="utf-8"))
        assert manifest["chunk_hashes"] == {
            fixture.stable_id: fixture.new_hash}
        resources.events.append("marker_cleanup")
        return original_finish_update(marker_path, **marker_kwargs)

    monkeypatch.setattr(rag, "_save_index_manifest", save_manifest)
    monkeypatch.setattr(rag, "_finish_index_update", finish_update)
    rag.index_chunks(
        fixture.chunks_path, fixture.db_path,
        collection_name="book", embedding_model=fixture.embedding_model)

    assert resources.executors[0].shutdown_calls == [(True, False)]
    assert resources.events == [
        "executor_shutdown", "progress_close", "manifest_save",
        "marker_cleanup"]
    assert not fixture.marker_path.exists()


def test_chroma_sequential_producer_failure_stops_worker_and_retries(
        monkeypatch, tmp_path):
    resources = _track_queue_worker_resources(monkeypatch)
    fixture = _prepare_chroma_changed_existing_incremental(
        monkeypatch, tmp_path)
    original_manifest = fixture.manifest_path.read_bytes()

    def fail_index_embedding(texts, _model, **_kwargs):
        if texts == ["RAG index embedding-dimension probe"]:
            return [[0.0, 1.0]]
        raise RuntimeError("injected Chroma producer failure")

    monkeypatch.setattr(rag, "_embed_texts", fail_index_embedding)
    with pytest.raises(RuntimeError, match="injected Chroma producer failure"):
        rag.index_chunks(
            fixture.chunks_path, fixture.db_path,
            collection_name="book", embedding_model="model-a")

    assert fixture.state.upserts == []
    assert fixture.state.deletes == [[fixture.stable_id]]
    assert fixture.collection.rows == {}
    assert fixture.marker_path.is_file()
    assert fixture.manifest_path.read_bytes() == original_manifest
    assert resources.executors[0].shutdown_calls == [(True, False)]
    assert not resources.executors[0].thread.is_alive()
    assert resources.bars[0].updates == 0
    assert resources.bars[0].close_calls == 1
    with pytest.raises(ValueError, match="Index update is incomplete"):
        rag._query_manifest_dimension(
            fixture.db_path, backend="chroma", collection_name="book",
            embedding_model="model-a")

    monkeypatch.setattr(rag, "_embed_texts", fixture.successful_embed)
    rag.index_chunks(
        fixture.chunks_path, fixture.db_path,
        collection_name="book", embedding_model="model-a")

    assert fixture.state.upserts == [[fixture.stable_id]]
    assert fixture.state.collection_deletes == ["book"]
    assert fixture.state.collection_creates == ["book"]
    assert fixture.sibling.rows == {"keep": "untouched"}
    assert not fixture.marker_path.exists()
    assert fixture.collection.rows[fixture.stable_id]["metadata"][
        "context"] == "Corrected classification"
    manifest = json.loads(
        fixture.manifest_path.read_text(encoding="utf-8"))
    assert manifest["chunk_hashes"] == {
        fixture.stable_id: fixture.new_hash}


def test_chroma_sequential_worker_failure_closes_resources_and_retries(
        monkeypatch, tmp_path):
    resources = _track_queue_worker_resources(monkeypatch)
    fixture = _prepare_chroma_changed_existing_incremental(
        monkeypatch, tmp_path)
    original_manifest = fixture.manifest_path.read_bytes()
    successful_upsert = fixture.collection.upsert

    def ambiguous_upsert(*, ids, embeddings, documents, metadatas):
        successful_upsert(
            ids=ids, embeddings=embeddings,
            documents=documents, metadatas=metadatas)
        raise RuntimeError("injected Chroma upsert failure")

    monkeypatch.setattr(fixture.collection, "upsert", ambiguous_upsert)
    with pytest.raises(RuntimeError, match="injected Chroma upsert failure"):
        rag.index_chunks(
            fixture.chunks_path, fixture.db_path,
            collection_name="book", embedding_model="model-a")

    assert fixture.state.upserts == [[fixture.stable_id]]
    assert fixture.collection.rows[fixture.stable_id]["metadata"][
        "context"] == "Corrected classification"
    assert fixture.marker_path.is_file()
    assert fixture.manifest_path.read_bytes() == original_manifest
    assert resources.executors[0].shutdown_calls == [(True, False)]
    assert not resources.executors[0].thread.is_alive()
    assert resources.bars[0].updates == 0
    assert resources.bars[0].close_calls == 1
    with pytest.raises(ValueError, match="Index update is incomplete"):
        rag._query_manifest_dimension(
            fixture.db_path, backend="chroma", collection_name="book",
            embedding_model="model-a")

    monkeypatch.setattr(fixture.collection, "upsert", successful_upsert)
    rag.index_chunks(
        fixture.chunks_path, fixture.db_path,
        collection_name="book", embedding_model="model-a")

    assert fixture.state.upserts == [
        [fixture.stable_id], [fixture.stable_id]]
    assert fixture.state.collection_deletes == ["book"]
    assert fixture.state.collection_creates == ["book"]
    assert fixture.sibling.rows == {"keep": "untouched"}
    assert not fixture.marker_path.exists()
    manifest = json.loads(
        fixture.manifest_path.read_text(encoding="utf-8"))
    assert manifest["chunk_hashes"] == {
        fixture.stable_id: fixture.new_hash}


def test_chroma_manifest_commit_failure_keeps_recovery_state(
        monkeypatch, tmp_path):
    fixture = _prepare_chroma_changed_existing_incremental(
        monkeypatch, tmp_path)
    original_manifest = fixture.manifest_path.read_bytes()

    def fail_manifest_commit(*_args, **_kwargs):
        assert fixture.marker_path.is_file()
        raise OSError("injected Chroma manifest commit failure")

    monkeypatch.setattr(rag, "_save_index_manifest", fail_manifest_commit)
    monkeypatch.setattr(
        rag, "_finish_index_update",
        lambda _path, **_kwargs: pytest.fail(
            "marker cleanup must not run after a manifest failure"),
    )

    with pytest.raises(OSError, match="Chroma manifest commit failure"):
        rag.index_chunks(
            fixture.chunks_path, fixture.db_path,
            collection_name="book", embedding_model="model-a")

    assert fixture.state.upserts == [[fixture.stable_id]]
    assert fixture.collection.rows[fixture.stable_id]["metadata"][
        "context"] == "Corrected classification"
    assert fixture.marker_path.is_file()
    assert fixture.manifest_path.read_bytes() == original_manifest
    with pytest.raises(ValueError, match="Index update is incomplete"):
        rag._query_manifest_dimension(
            fixture.db_path, backend="chroma", collection_name="book",
            embedding_model="model-a")


def test_chroma_removal_only_commits_before_marker_cleanup(
        monkeypatch, tmp_path):
    fixture = _prepare_chroma_changed_existing_incremental(
        monkeypatch, tmp_path)
    removed_record = {
        "text": "This obsolete rule must be removed.",
        "metadata": {
            "chunk_index": 1,
            "context": "Obsolete doctrine",
            "embedding_token_count": 7,
        },
    }
    removed_id = rag._chunk_id(removed_record)
    fixture.chunks_path.write_text(
        json.dumps(fixture.old_record) + "\n", encoding="utf-8")
    fixture.collection.rows[removed_id] = {
        "embedding": [1.0, 0.0],
        "document": removed_record["text"],
        "metadata": {
            **removed_record["metadata"],
            "stable_id": removed_id,
        },
    }
    rag._save_index_manifest(
        fixture.db_path, backend="chroma", collection_name="book",
        embedding_model="model-a", embedding_dimension=2,
        chunk_hashes={
            fixture.stable_id: fixture.old_hash,
            removed_id: rag._chunk_hash(removed_record),
        },
        source_sha256="old-source", source_record_count=2)
    events = []
    save_manifest = rag._save_index_manifest
    finish_update = rag._finish_index_update

    def assert_guarded_save(*args, **kwargs):
        assert fixture.marker_path.is_file()
        events.append("manifest_save")
        return save_manifest(*args, **kwargs)

    def assert_committed_then_finish(marker_path, **marker_kwargs):
        assert marker_path == fixture.marker_path
        manifest = json.loads(
            fixture.manifest_path.read_text(encoding="utf-8"))
        assert manifest["chunk_hashes"] == {
            fixture.stable_id: fixture.old_hash}
        events.append("marker_cleanup")
        finish_update(marker_path, **marker_kwargs)

    monkeypatch.setattr(rag, "_save_index_manifest", assert_guarded_save)
    monkeypatch.setattr(rag, "_finish_index_update", assert_committed_then_finish)
    outcome = rag.index_chunks(
        fixture.chunks_path, fixture.db_path,
        collection_name="book", embedding_model="model-a")

    assert fixture.state.deletes == [[removed_id]]
    assert fixture.state.upserts == []
    assert set(fixture.collection.rows) == {fixture.stable_id}
    assert events == ["manifest_save", "marker_cleanup"]
    assert not fixture.marker_path.exists()
    assert outcome.disposition == "updated"
    assert outcome.changed_records == 0
    assert outcome.unchanged_records == 1
    assert outcome.removed_records == 1
    assert outcome.upserted_records == 0
    assert outcome.batch_count == 0


def test_chroma_unchanged_run_never_starts_update(monkeypatch, tmp_path):
    fixture = _prepare_chroma_changed_existing_incremental(
        monkeypatch, tmp_path)
    rag.index_chunks(
        fixture.chunks_path, fixture.db_path,
        collection_name="book", embedding_model="model-a")
    assert not fixture.marker_path.exists()
    fixture.state.upserts.clear()
    fixture.state.deletes.clear()
    fixture.state.collection_deletes.clear()
    fixture.state.collection_creates.clear()

    monkeypatch.setattr(
        rag, "_begin_chroma_index_update",
        lambda *_args, **_kwargs: pytest.fail(
            "an unchanged Chroma run must not create an update marker"),
    )
    outcome = rag.index_chunks(
        fixture.chunks_path, fixture.db_path,
        collection_name="book", embedding_model="model-a")

    assert fixture.state.upserts == []
    assert fixture.state.deletes == []
    assert fixture.state.collection_deletes == []
    assert fixture.state.collection_creates == []
    assert not fixture.marker_path.exists()
    assert outcome.disposition == "unchanged"
    assert outcome.changed_records == 0
    assert outcome.unchanged_records == outcome.total_records
    assert outcome.committed is True


def test_chroma_sequential_cleanup_precedes_manifest_commit(
        monkeypatch, tmp_path):
    resources = _track_queue_worker_resources(monkeypatch)
    fixture = _prepare_chroma_changed_existing_incremental(
        monkeypatch, tmp_path)
    save_manifest = rag._save_index_manifest
    finish_update = rag._finish_index_update

    def assert_clean_then_save(*args, **kwargs):
        assert len(resources.executors) == 1
        assert not resources.executors[0].thread.is_alive()
        assert resources.executors[0].shutdown_calls == [(True, False)]
        assert resources.bars[0].updates == 1
        assert resources.bars[0].close_calls == 1
        assert fixture.marker_path.is_file()
        resources.events.append("manifest_save")
        return save_manifest(*args, **kwargs)

    def assert_committed_then_finish(marker_path, **marker_kwargs):
        assert marker_path == fixture.marker_path
        manifest = json.loads(
            fixture.manifest_path.read_text(encoding="utf-8"))
        assert manifest["chunk_hashes"] == {
            fixture.stable_id: fixture.new_hash}
        resources.events.append("marker_cleanup")
        finish_update(marker_path, **marker_kwargs)

    monkeypatch.setattr(rag, "_save_index_manifest", assert_clean_then_save)
    monkeypatch.setattr(rag, "_finish_index_update", assert_committed_then_finish)
    rag.index_chunks(
        fixture.chunks_path, fixture.db_path,
        collection_name="book", embedding_model="model-a")

    assert resources.events == [
        "worker_exit", "executor_shutdown", "progress_close",
        "manifest_save", "marker_cleanup"]
    assert not fixture.marker_path.exists()
    manifest = json.loads(
        fixture.manifest_path.read_text(encoding="utf-8"))
    assert manifest["chunk_hashes"] == {
        fixture.stable_id: fixture.new_hash}


def test_chroma_migrates_legacy_skips_compatible_and_rebuilds_model_change(
        monkeypatch, tmp_path):
    chunks_path = tmp_path / "chunks.jsonl"
    db_path = tmp_path / "chroma"
    db_path.mkdir()
    _write_chunks(chunks_path)
    (db_path / "chunk_hashes.json").write_text(
        '{"chunk_stale":"stale"}', encoding="utf-8")

    state = SimpleNamespace(
        collections={}, deleted=[], upserts=[], client_close_calls=0)

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

        def get(self, *, limit, offset, include):
            assert include == ["metadatas"]
            ids = list(self.rows)[offset:offset + limit]
            return {
                "ids": ids,
                "metadatas": [self.rows[item_id][2] for item_id in ids],
            }

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

        def close(self):
            state.client_close_calls += 1

    chromadb = ModuleType("chromadb")
    chromadb.PersistentClient = FakeChromaClient
    monkeypatch.setitem(sys.modules, "chromadb", chromadb)

    embedding_calls = []
    monkeypatch.setattr(
        rag, "_embed_texts",
        _fake_embeddings(embedding_calls, {"model-a": 2, "model-b": 3}))

    first_outcome = rag.index_chunks(
        chunks_path, db_path, collection_name="book",
        embedding_model="model-a")

    assert state.deleted == ["book"]
    assert state.collections["sibling"].rows == {"keep": "untouched"}
    assert state.client_close_calls == 1
    assert (db_path / "chunk_hashes.json").is_file()
    first_upsert_count = len(state.upserts)
    first_call_count = len(embedding_calls)
    assert first_outcome.disposition == "rebuilt"
    assert first_outcome.changed_records == 1
    assert first_outcome.upserted_records == 1

    second_outcome = rag.index_chunks(
        chunks_path, db_path, collection_name="book",
        embedding_model="model-a")

    assert state.deleted == ["book"]
    assert len(state.upserts) == first_upsert_count
    assert len(embedding_calls) == first_call_count + 1  # dimension probe only
    assert second_outcome.disposition == "unchanged"

    third_outcome = rag.index_chunks(
        chunks_path, db_path, collection_name="book",
        embedding_model="model-b")

    assert state.deleted == ["book", "book"]
    assert len(state.upserts) == first_upsert_count + 1
    assert state.collections["sibling"].rows == {"keep": "untouched"}
    assert state.client_close_calls == 3
    manifest = rag._load_index_manifest(
        db_path, backend="chroma", collection_name="book")
    assert manifest["embedding_model"] == "model-b"
    assert manifest["embedding_dimension"] == 3
    assert manifest["source_sha256"] == hashlib.sha256(
        chunks_path.read_bytes()).hexdigest()
    assert manifest["source_record_count"] == 1
    assert third_outcome.disposition == "rebuilt"
    assert third_outcome.changed_records == 1
    assert third_outcome.upserted_records == 1


def test_qdrant_manifest_skip_and_model_change_preserve_sibling(
        monkeypatch, tmp_path):
    chunks_path = tmp_path / "chunks.jsonl"
    db_path = tmp_path / "qdrant"
    _write_chunks(chunks_path)

    state = SimpleNamespace(
        collections={"sibling": {"keep": "untouched"}},
        deleted=[], upserts=[], client_close_calls=0)
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

        def count(self, *, collection_name, exact):
            assert exact is True
            return SimpleNamespace(
                count=len(state.collections[collection_name]))

        def upsert(self, *, collection_name, points, wait):
            assert wait is True
            assert marker_path.is_file()
            state.upserts.append((collection_name, points))
            for point in points:
                state.collections[collection_name][point.id] = point
            return _completed_update()

        def scroll(self, collection_name, limit, offset=None, **kwargs):
            points = list(state.collections[collection_name].values())
            return points, None

        def delete(self, collection_name, *, points_selector, wait):
            assert wait is True
            assert marker_path.is_file()
            for point_id in points_selector.points:
                state.collections[collection_name].pop(point_id, None)
            return _completed_update()

        def get_collection(self, collection_name):
            return SimpleNamespace(
                points_count=len(state.collections[collection_name]))

        def close(self):
            state.client_close_calls += 1

    qdrant_client = ModuleType("qdrant_client")
    qdrant_client.QdrantClient = FakeQdrantClient
    qdrant_client.models = models
    monkeypatch.setitem(sys.modules, "qdrant_client", qdrant_client)

    embedding_calls = []
    monkeypatch.setattr(
        rag, "_embed_texts",
        _fake_embeddings(embedding_calls, {"model-a": 2, "model-b": 4}))

    first_outcome = rag.index_chunks_qdrant(
        chunks_path, db_path, collection_name="book",
        embedding_model="model-a")
    assert not marker_path.exists()
    first_upsert_count = len(state.upserts)
    first_call_count = len(embedding_calls)
    assert first_outcome.disposition == "created"
    assert first_outcome.changed_records == 1
    assert first_outcome.upserted_records == 1

    begin_update = rag._begin_qdrant_index_update
    monkeypatch.setattr(
        rag, "_begin_qdrant_index_update",
        lambda *_args, **_kwargs: pytest.fail(
            "an unchanged run must not create an update marker"))
    outcome = rag.index_chunks_qdrant(
        chunks_path, db_path, collection_name="book",
        embedding_model="model-a")
    monkeypatch.setattr(rag, "_begin_qdrant_index_update", begin_update)
    assert not marker_path.exists()
    assert len(state.upserts) == first_upsert_count
    assert len(embedding_calls) == first_call_count + 1  # dimension probe only
    assert outcome.disposition == "unchanged"
    assert outcome.changed_records == 0
    assert outcome.physical_count == outcome.total_records

    rebuilt_outcome = rag.index_chunks_qdrant(
        chunks_path, db_path, collection_name="book",
        embedding_model="model-b")
    assert not marker_path.exists()

    assert state.deleted == ["book"]
    assert state.collections["sibling"] == {"keep": "untouched"}
    assert state.client_close_calls == 3
    assert len(state.upserts) == first_upsert_count + 1
    manifest = rag._load_index_manifest(
        db_path, backend="qdrant", collection_name="book")
    assert manifest["embedding_model"] == "model-b"
    assert manifest["embedding_dimension"] == 4
    assert manifest["source_sha256"] == hashlib.sha256(
        chunks_path.read_bytes()).hexdigest()
    assert manifest["source_record_count"] == 1
    assert rebuilt_outcome.disposition == "rebuilt"
    assert rebuilt_outcome.changed_records == 1
    assert rebuilt_outcome.upserted_records == 1
