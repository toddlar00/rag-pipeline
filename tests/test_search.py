import json
import hashlib
import sys
from concurrent.futures import ThreadPoolExecutor
from pathlib import Path
from types import ModuleType, SimpleNamespace

import pytest

import eval as retrieval_eval
import rag


@pytest.fixture
def search_fakes(monkeypatch, tmp_path):
    state = {
        "chroma_calls": [],
        "qdrant_calls": [],
        "embedding_calls": [],
        "bm25_calls": [],
        "rerank_calls": [],
        "qdrant_hybrid_error": None,
        "chroma_constructor_error": None,
        "qdrant_constructor_error": None,
        "chroma_query_error": None,
        "qdrant_query_error": None,
        "chroma_close_error": None,
        "qdrant_close_error": None,
        "chroma_close_calls": 0,
        "qdrant_close_calls": 0,
    }
    db_dir = tmp_path / "db"
    db_dir.mkdir()
    chunks_path = tmp_path / "chunks.jsonl"
    chunks_path.write_text(
        json.dumps({"text": "fixture chunk", "metadata": {}}) + "\n",
        encoding="utf-8",
    )
    source_sha256 = hashlib.sha256(chunks_path.read_bytes()).hexdigest()
    for backend in ("chroma", "qdrant"):
        rag._save_index_manifest(
            db_dir, backend=backend, collection_name=rag.DEFAULT_COLLECTION,
            embedding_model="fake-embedding", embedding_dimension=2,
            chunk_hashes={}, source_sha256=source_sha256,
            source_record_count=1,
        )

    def fake_embed(texts, model_name, *, input_type="document"):
        state["embedding_calls"].append((list(texts), model_name, input_type))
        return [[0.25, 0.75] for _ in texts]

    monkeypatch.setattr(rag, "_embed_texts", fake_embed)

    class FakeCollection:
        def query(self, **kwargs):
            state["chroma_calls"].append(kwargs)
            if state["chroma_query_error"] is not None:
                raise state["chroma_query_error"]
            count = kwargs["n_results"]
            return {
                "documents": [[f"dense-{index}" for index in range(count)]],
                "metadatas": [[
                    {
                        "chunk_index": index,
                        "content_type": "case_opinion",
                        "chapter_num": 2,
                    }
                    for index in range(count)
                ]],
                "distances": [[index / 100 for index in range(count)]],
            }

    class FakeChromaClient:
        def __init__(self, path):
            if state["chroma_constructor_error"] is not None:
                raise state["chroma_constructor_error"]
            self.path = path

        def get_collection(self, name):
            state["chroma_collection"] = name
            return FakeCollection()

        def close(self):
            state["chroma_close_calls"] += 1
            if state["chroma_close_error"] is not None:
                raise state["chroma_close_error"]

    chromadb = ModuleType("chromadb")
    chromadb.PersistentClient = FakeChromaClient
    monkeypatch.setitem(sys.modules, "chromadb", chromadb)

    def fake_bm25(query, path, n_results, **kwargs):
        state["bm25_calls"].append((query, path, n_results, kwargs))
        return (
            [f"keyword-{index}" for index in range(n_results)],
            [
                {
                    "chunk_index": index,
                    "content_type": "case_opinion",
                    "chapter_num": 2,
                }
                for index in range(n_results)
            ],
            [float(n_results - index) for index in range(n_results)],
        )

    monkeypatch.setattr(rag, "_bm25_search", fake_bm25)

    def model_value(**kwargs):
        return SimpleNamespace(**kwargs)

    models = SimpleNamespace(
        FieldCondition=model_value,
        MatchValue=model_value,
        Filter=model_value,
        SparseVector=model_value,
        Prefetch=model_value,
        FusionQuery=model_value,
        Fusion=SimpleNamespace(RRF="rrf"),
    )

    class FakeQdrantClient:
        def __init__(self, path):
            if state["qdrant_constructor_error"] is not None:
                raise state["qdrant_constructor_error"]
            self.path = path

        def collection_exists(self, name):
            state["qdrant_collection"] = name
            return True

        def query_points(self, **kwargs):
            state["qdrant_calls"].append(kwargs)
            if state["qdrant_query_error"] is not None:
                raise state["qdrant_query_error"]
            if "prefetch" in kwargs and state["qdrant_hybrid_error"]:
                raise RuntimeError(state["qdrant_hybrid_error"])
            count = kwargs["limit"]
            points = [
                SimpleNamespace(
                    payload={
                        "text": f"qdrant-{index}",
                        "chunk_index": index,
                        "content_type": "case_opinion",
                        "chapter_num": 2,
                    },
                    score=1.0 - index / 100,
                )
                for index in range(count)
            ]
            return SimpleNamespace(points=points)

        def close(self):
            state["qdrant_close_calls"] += 1
            state["qdrant_closed"] = True
            if state["qdrant_close_error"] is not None:
                raise state["qdrant_close_error"]

    qdrant_client = ModuleType("qdrant_client")
    qdrant_client.QdrantClient = FakeQdrantClient
    qdrant_client.models = models
    monkeypatch.setitem(sys.modules, "qdrant_client", qdrant_client)

    def fake_rerank(query, documents, metadatas, distances, top_k, **kwargs):
        state["rerank_calls"].append({
            "query": query,
            "candidate_count": len(documents),
            "distances": distances,
            "top_k": top_k,
            "kwargs": kwargs,
        })
        order = list(reversed(range(len(documents))))[:top_k]
        return (
            [documents[index] for index in order],
            [metadatas[index] for index in order],
            [0.99 - rank / 100 for rank, _ in enumerate(order)],
        )

    monkeypatch.setattr(rag, "_rerank", fake_rerank)
    return SimpleNamespace(
        state=state, db_dir=db_dir, chunks_path=chunks_path,
    )


@pytest.mark.parametrize("backend", ["chroma", "qdrant"])
@pytest.mark.parametrize("hybrid", [False, True, None])
@pytest.mark.parametrize("use_reranker", [False, True, None])
def test_search_matrix_is_structured_and_overfetches(
        search_fakes, backend, hybrid, use_reranker):
    result = rag.search_index(
        "minimum contacts",
        search_fakes.db_dir,
        db_backend=backend,
        n_results=3,
        content_type="case_opinion",
        chapter_num=2,
        embedding_model="fake-embedding",
        hybrid=hybrid,
        use_reranker=use_reranker,
        chunks_path=search_fakes.chunks_path,
    )

    resolved_hybrid = hybrid is not False
    reranker_enabled = (
        not resolved_hybrid if use_reranker is None else use_reranker)
    expected_fetch = 3 * rag.RERANK_OVERFETCH if (
        resolved_hybrid or use_reranker is not False) else 3
    assert isinstance(result, rag.SearchResponse)
    assert all(isinstance(hit, rag.SearchHit) for hit in result.hits)
    assert len(result.hits) == 3
    assert result.backend == backend
    assert result.requested_mode == (
        "auto" if hybrid is None else "hybrid" if hybrid else "vector")
    assert result.effective_mode == (
        "hybrid" if resolved_hybrid else "vector")
    assert result.reranker_applied is reranker_enabled
    assert result.candidate_depth == expected_fetch
    assert result.reranker_model == (
        rag.DEFAULT_RERANKER_MODEL if reranker_enabled else None)
    assert result.warnings == []
    assert search_fakes.state["embedding_calls"] == [
        (["minimum contacts"], "fake-embedding", "query")
    ]

    if backend == "chroma":
        call = search_fakes.state["chroma_calls"][0]
        assert call["n_results"] == expected_fetch
        assert call["query_embeddings"] == [[0.25, 0.75]]
        assert call["where"] == {
            "$and": [
                {"content_type": {"$eq": "case_opinion"}},
                {"chapter_num": {"$eq": 2}},
            ]
        }
        assert bool(search_fakes.state["bm25_calls"]) is resolved_hybrid
        assert search_fakes.state["chroma_close_calls"] == 1
        assert search_fakes.state["qdrant_close_calls"] == 0
    else:
        call = search_fakes.state["qdrant_calls"][0]
        assert call["limit"] == expected_fetch
        assert ("prefetch" in call) is resolved_hybrid
        query_filter = (call["prefetch"][0].filter if resolved_hybrid
                        else call["query_filter"])
        assert [condition.key for condition in query_filter.must] == [
            "content_type", "chapter_num"
        ]
        assert search_fakes.state["qdrant_closed"] is True
        assert search_fakes.state["qdrant_close_calls"] == 1
        assert search_fakes.state["chroma_close_calls"] == 0

    if reranker_enabled:
        assert search_fakes.state["rerank_calls"][0][
            "candidate_count"] == expected_fetch
        assert search_fakes.state["rerank_calls"][0]["top_k"] == 3
    else:
        assert search_fakes.state["rerank_calls"] == []


@pytest.mark.parametrize("backend", ["chroma", "qdrant"])
@pytest.mark.parametrize("use_reranker", [False, True])
def test_table_children_force_overfetch_and_suppress_only_retrieved_parent(
        search_fakes, monkeypatch, backend, use_reranker):
    rag._save_index_manifest(
        search_fakes.db_dir, backend=backend,
        collection_name=rag.DEFAULT_COLLECTION,
        embedding_model="fake-embedding", embedding_dimension=2,
        chunk_hashes={}, source_record_count=4,
        table_child_count=2,
        quality_report_schema_version=(
            rag._quality_core.QUALITY_REPORT_SCHEMA_VERSION),
        quality_report_sha256="a" * 64,
    )
    parent = {
        "retrieval_role": "table_parent",
        "table_parent_stable_id": "table-parent",
        "stable_id": "table-parent",
    }
    child_one = {
        "retrieval_role": "table_child",
        "table_parent_stable_id": "table-parent",
        "table_child_index": 0,
        "stable_id": "table-row-0",
    }
    child_two = {
        "retrieval_role": "table_child",
        "table_parent_stable_id": "table-parent",
        "table_child_index": 1,
        "stable_id": "table-row-1",
    }
    observed = []

    def candidates(_query, _db, *, n_results, **_kwargs):
        observed.append(n_results)
        return (
            ["whole table", "first row", "other", "second row"],
            [parent, child_one, {"stable_id": "other"}, child_two],
            [0.99, 0.95, 0.9, 0.85],
            "vector",
        )

    monkeypatch.setattr(
        rag,
        "_search_chroma_candidates" if backend == "chroma"
        else "_search_qdrant_candidates",
        candidates,
    )

    response = rag.search_index(
        "fee safeguards", search_fakes.db_dir, db_backend=backend,
        n_results=3, embedding_model="fake-embedding", hybrid=False,
        use_reranker=use_reranker, overfetch=4,
        chunks_path=search_fakes.chunks_path,
    )

    assert observed == [12]
    assert response.candidate_depth == 12
    assert {hit.text for hit in response.hits} == {
        "first row", "other", "second row"}
    assert {hit.source_id for hit in response.hits} == {
        "table-row-0", "other", "table-row-1"}
    assert "whole table" not in [hit.text for hit in response.hits]
    if use_reranker:
        assert search_fakes.state["rerank_calls"][0][
            "candidate_count"] == 3
    else:
        assert search_fakes.state["rerank_calls"] == []
    assert response.hits[0].metadata["table_parent_stable_id"] == (
        "table-parent")
    grounded = rag._grounded_sources(response, "fee safeguards")
    assert grounded[0].source_id == response.hits[0].source_id
    assert grounded[0].metadata["table_parent_stable_id"] == "table-parent"


@pytest.mark.parametrize("backend", ["chroma", "qdrant"])
def test_search_propagates_client_close_failure(search_fakes, backend):
    close_error = RuntimeError(f"{backend} client close failure")
    search_fakes.state[f"{backend}_close_error"] = close_error

    with pytest.raises(RuntimeError) as raised:
        rag.search_index(
            "minimum contacts", search_fakes.db_dir,
            db_backend=backend, n_results=2,
            embedding_model="fake-embedding", hybrid=False,
            use_reranker=False, chunks_path=search_fakes.chunks_path)

    assert raised.value is close_error
    assert search_fakes.state[f"{backend}_close_calls"] == 1


@pytest.mark.parametrize("backend", ["chroma", "qdrant"])
def test_search_preserves_client_constructor_failure(search_fakes, backend):
    constructor_error = RuntimeError(f"{backend} constructor failure")
    search_fakes.state[f"{backend}_constructor_error"] = constructor_error

    with pytest.raises(RuntimeError) as raised:
        rag.search_index(
            "minimum contacts", search_fakes.db_dir,
            db_backend=backend, n_results=2,
            embedding_model="fake-embedding", hybrid=False,
            use_reranker=False, chunks_path=search_fakes.chunks_path)

    assert raised.value is constructor_error
    assert search_fakes.state[f"{backend}_close_calls"] == 0


@pytest.mark.parametrize("backend", ["chroma", "qdrant"])
def test_search_preserves_query_error_over_client_close_failure(
        search_fakes, backend):
    query_error = RuntimeError(f"primary {backend} query failure")
    close_error = RuntimeError(f"secondary {backend} close failure")
    search_fakes.state[f"{backend}_query_error"] = query_error
    search_fakes.state[f"{backend}_close_error"] = close_error

    with pytest.raises(RuntimeError) as raised:
        rag.search_index(
            "minimum contacts", search_fakes.db_dir,
            db_backend=backend, n_results=2,
            embedding_model="fake-embedding", hybrid=False,
            use_reranker=False, chunks_path=search_fakes.chunks_path)

    assert raised.value is query_error
    assert search_fakes.state[f"{backend}_close_calls"] == 1


def test_chroma_hybrid_refuses_chunks_from_another_index_generation(
        search_fakes):
    original_sha256 = hashlib.sha256(
        search_fakes.chunks_path.read_bytes()).hexdigest()
    rag._save_index_manifest(
        search_fakes.db_dir, backend="chroma", collection_name="book",
        embedding_model="fake-embedding", embedding_dimension=2,
        chunk_hashes={}, source_sha256=original_sha256,
        source_record_count=1)
    rag._atomic_write_jsonl(
        search_fakes.chunks_path,
        [{"text": "new corpus", "metadata": {}}])

    with pytest.raises(ValueError, match="does not match the indexed corpus"):
        rag.search_index(
            "minimum contacts", search_fakes.db_dir,
            db_backend="chroma", collection_name="book", n_results=2,
            embedding_model="fake-embedding", hybrid=True,
            use_reranker=False, chunks_path=search_fakes.chunks_path)

    assert search_fakes.state["embedding_calls"] == []
    assert search_fakes.state["chroma_close_calls"] == 0


def test_chroma_hybrid_rechecks_chunks_after_lexical_read(search_fakes,
                                                           monkeypatch):
    original_sha256 = hashlib.sha256(
        search_fakes.chunks_path.read_bytes()).hexdigest()
    rag._save_index_manifest(
        search_fakes.db_dir, backend="chroma", collection_name="book",
        embedding_model="fake-embedding", embedding_dimension=2,
        chunk_hashes={}, source_sha256=original_sha256,
        source_record_count=1)

    def replace_during_bm25(*_args, **_kwargs):
        rag._atomic_write_jsonl(
            search_fakes.chunks_path,
            [{"text": "replacement corpus", "metadata": {}}])
        return ["lexical"], [{"chunk_index": 0}], [1.0]

    monkeypatch.setattr(rag, "_bm25_search", replace_during_bm25)

    with pytest.raises(ValueError, match="does not match the indexed corpus"):
        rag.search_index(
            "minimum contacts", search_fakes.db_dir,
            db_backend="chroma", collection_name="book", n_results=2,
            embedding_model="fake-embedding", hybrid=True,
            use_reranker=False, chunks_path=search_fakes.chunks_path)

    assert search_fakes.state["chroma_close_calls"] == 1


def test_chroma_missing_chunks_reports_hybrid_fallback(search_fakes, tmp_path):
    missing_chunks = tmp_path / "missing.jsonl"

    result = rag.search_index(
        "minimum contacts", search_fakes.db_dir,
        db_backend="chroma", n_results=2, hybrid=True,
        embedding_model="fake-embedding", use_reranker=False,
        chunks_path=missing_chunks,
    )

    assert result.requested_mode == "hybrid"
    assert result.effective_mode == "vector"
    assert result.reranker_applied is False
    assert len(result.warnings) == 1
    assert "chunks file" in result.warnings[0]
    assert search_fakes.state["chroma_calls"][0]["n_results"] == 8


def test_chroma_unmanifested_chunks_do_not_enter_hybrid_mode(search_fakes):
    rag._index_manifest_path(
        search_fakes.db_dir, backend="chroma",
        collection_name=rag.DEFAULT_COLLECTION).unlink()

    result = rag.search_index(
        "minimum contacts", search_fakes.db_dir,
        db_backend="chroma", n_results=2,
        embedding_model="fake-embedding", hybrid=True,
        use_reranker=False, chunks_path=search_fakes.chunks_path,
    )

    assert result.effective_mode == "vector"
    assert search_fakes.state["bm25_calls"] == []
    assert "manifested chunks SHA-256" in result.warnings[0]


def test_chroma_auto_mode_uses_vector_cleanly_without_chunks(
        search_fakes, tmp_path):
    result = rag.search_index(
        "minimum contacts", search_fakes.db_dir,
        db_backend="chroma", n_results=2, hybrid=None,
        embedding_model="fake-embedding", use_reranker=False,
        chunks_path=tmp_path / "missing.jsonl",
    )

    assert result.requested_mode == "auto"
    assert result.effective_mode == "vector"
    assert result.warnings == []
    assert result.candidate_depth == 2
    assert search_fakes.state["bm25_calls"] == []


def test_chroma_bm25_failure_reports_hybrid_fallback(
        search_fakes, monkeypatch):
    def fail_bm25(*args, **kwargs):
        raise RuntimeError("offline index is corrupt")

    monkeypatch.setattr(rag, "_bm25_search", fail_bm25)

    result = rag.search_index(
        "minimum contacts", search_fakes.db_dir,
        db_backend="chroma", n_results=2, hybrid=True,
        embedding_model="fake-embedding", use_reranker=False,
        chunks_path=search_fakes.chunks_path,
    )

    assert result.effective_mode == "vector"
    assert "BM25 search failed" in result.warnings[0]


def test_qdrant_hybrid_failure_retries_vector_search(search_fakes):
    search_fakes.state["qdrant_hybrid_error"] = "sparse vector unavailable"

    result = rag.search_index(
        "minimum contacts", search_fakes.db_dir,
        db_backend="qdrant", n_results=2, hybrid=True,
        embedding_model="fake-embedding", use_reranker=False,
    )

    assert result.requested_mode == "hybrid"
    assert result.effective_mode == "vector"
    assert "hybrid search failed" in result.warnings[0]
    assert len(search_fakes.state["qdrant_calls"]) == 2
    assert "prefetch" in search_fakes.state["qdrant_calls"][0]
    assert "prefetch" not in search_fakes.state["qdrant_calls"][1]
    assert search_fakes.state["qdrant_calls"][1]["limit"] == 8


@pytest.mark.parametrize("backend", ["chroma", "qdrant"])
def test_reranker_failure_keeps_backend_ranking_and_warns(
        search_fakes, monkeypatch, backend):
    def fail_reranker(*args, **kwargs):
        raise RuntimeError("model unavailable")

    monkeypatch.setattr(rag, "_rerank", fail_reranker)

    result = rag.search_index(
        "minimum contacts", search_fakes.db_dir,
        db_backend=backend, n_results=2, use_reranker=True,
        embedding_model="fake-embedding",
        chunks_path=search_fakes.chunks_path,
    )

    assert result.reranker_applied is False
    assert len(result.hits) == 2
    assert "Reranker failed" in result.warnings[0]


def test_search_releases_vector_lease_before_reranking(
        search_fakes, monkeypatch):
    acquired = []

    def assert_lease_released(query, documents, metadatas, distances, top_k,
                              **_kwargs):
        def acquire_from_other_thread():
            with rag._vector_store_lock(
                    search_fakes.db_dir, backend="chroma",
                    collection_name=rag.DEFAULT_COLLECTION,
                    operation="reranker boundary test", timeout=0):
                return True

        with ThreadPoolExecutor(max_workers=1) as pool:
            acquired.append(pool.submit(acquire_from_other_thread).result())
        return documents[:top_k], metadatas[:top_k], distances[:top_k]

    monkeypatch.setattr(rag, "_rerank", assert_lease_released)
    response = rag.search_index(
        "minimum contacts", search_fakes.db_dir, db_backend="chroma",
        n_results=2, embedding_model="fake-embedding", hybrid=False,
        use_reranker=True, chunks_path=search_fakes.chunks_path,
    )

    assert acquired == [True]
    assert response.reranker_applied is True


@pytest.mark.parametrize("backend", ["chroma", "qdrant"])
def test_context_search_uses_exact_manifested_chunks_without_changing_rank(
        search_fakes, monkeypatch, backend):
    prefix = "dense" if backend == "chroma" else "qdrant"
    records = [
        {
            "text": f"{prefix}-{index}",
            "metadata": {
                "chunk_index": index,
                "source_file": "book.json",
                "chapter_num": 2,
                "content_type": "case_opinion",
                "page_start": index + 1,
                "page_end": index + 1,
                "page_range": str(index + 1),
            },
        }
        for index in range(3)
    ]
    rag._retrieval_core._attach_retrieval_linkage(records)
    source_sha256 = hashlib.sha256(
        search_fakes.chunks_path.read_bytes()).hexdigest()
    monkeypatch.setattr(
        rag, "_load_index_snapshot_with_quality",
        lambda _path: (records, source_sha256, (1, 2, 3, 4, 5), (3, "f" * 64)),
    )
    monkeypatch.setattr(
        rag, "_require_hybrid_chunks_snapshot",
        lambda *_args, **_kwargs: source_sha256,
    )
    def source_id_for_hit(hit):
        metadata = records[hit.metadata["chunk_index"]]["metadata"]
        hit.metadata.update({
            key: metadata.get(key)
            for key in rag._retrieval_core._CONTEXT_HIT_IDENTITY_FIELDS
        })
        hit.metadata["stable_id"] = metadata["stable_id"]
        return metadata["stable_id"]

    monkeypatch.setattr(rag, "_search_hit_source_id", source_id_for_hit)

    response = rag.search_index(
        "minimum contacts", search_fakes.db_dir,
        db_backend=backend, n_results=1,
        embedding_model="fake-embedding", hybrid=False,
        use_reranker=False, chunks_path=search_fakes.chunks_path,
        context_window=1,
    )

    assert [hit.text for hit in response.hits] == [f"{prefix}-0"]
    assert response.context_window == 1
    assert [
        segment.source_id for segment in response.hits[0].context_segments
    ] == [records[1]["metadata"]["stable_id"]]


@pytest.mark.parametrize("manifest_digest", [None, "f" * 64])
def test_context_search_rejects_missing_or_mismatched_generation(
        search_fakes, monkeypatch, manifest_digest):
    monkeypatch.setattr(
        rag, "_load_index_snapshot_with_quality",
        lambda _path: ([], "a" * 64, (1, 2, 3, 4, 5), (3, "e" * 64)),
    )
    monkeypatch.setattr(
        rag, "_require_hybrid_chunks_snapshot",
        lambda *_args, **_kwargs: manifest_digest,
    )

    with pytest.raises(ValueError, match="does not match the indexed corpus"):
        rag.search_index(
            "minimum contacts", search_fakes.db_dir,
            db_backend="chroma", n_results=1,
            embedding_model="fake-embedding", hybrid=False,
            use_reranker=False, chunks_path=search_fakes.chunks_path,
            context_window=1,
        )
    assert search_fakes.state["embedding_calls"] == []


def test_context_search_rejects_path_replacement_after_snapshot_load(
        search_fakes, monkeypatch):
    original_sha256 = hashlib.sha256(
        search_fakes.chunks_path.read_bytes()).hexdigest()

    def load_then_replace(_path):
        search_fakes.chunks_path.write_text(
            json.dumps({"text": "replacement chunk", "metadata": {}}) + "\n",
            encoding="utf-8",
        )
        return [], original_sha256, (1, 2, 3, 4, 5), (3, None)

    monkeypatch.setattr(
        rag, "_load_index_snapshot_with_quality", load_then_replace)

    with pytest.raises(ValueError, match="does not match the indexed corpus"):
        rag.search_index(
            "minimum contacts", search_fakes.db_dir,
            db_backend="chroma", n_results=1,
            embedding_model="fake-embedding", hybrid=False,
            use_reranker=False, chunks_path=search_fakes.chunks_path,
            context_window=1,
        )
    assert search_fakes.state["embedding_calls"] == []


@pytest.mark.parametrize("wrapper, backend", [
    (rag.query_index, "chroma"),
    (rag.query_index_qdrant, "qdrant"),
])
def test_legacy_wrappers_preserve_json_result_shape(
        monkeypatch, capsys, wrapper, backend):
    response = rag.SearchResponse(
        hits=[rag.SearchHit("result text", {"chapter_num": 2}, 0.87654)],
        backend=backend,
        requested_mode="hybrid",
        effective_mode="vector",
        reranker_applied=False,
        warnings=["fallback"],
    )
    observed = {}

    def fake_search(*args, **kwargs):
        observed["kwargs"] = kwargs
        return response

    monkeypatch.setattr(rag, "search_index", fake_search)
    wrapper(
        "minimum contacts", Path("unused"), output_json=True,
        hybrid=True, use_reranker=True,
    )

    payload = json.loads(capsys.readouterr().out)
    assert payload == [{
        "score": 0.8765,
        "search_mode": "vector",
        "reranked": False,
        "text": "result text",
        "metadata": {"chapter_num": 2},
    }]
    assert observed["kwargs"]["db_backend"] == backend


def test_eval_delegates_hybrid_and_reranker_to_public_search(
        monkeypatch, tmp_path):
    observed = {}
    response = rag.SearchResponse(
        hits=[rag.SearchHit("result text", {"content_type": "case_opinion"}, 0.8)],
        backend="chroma",
        requested_mode="hybrid",
        effective_mode="hybrid",
        reranker_applied=True,
    )

    def fake_search(*args, **kwargs):
        observed["args"] = args
        observed["kwargs"] = kwargs
        return response

    monkeypatch.setattr(rag, "search_index", fake_search)
    results = retrieval_eval.run_search(
        "minimum contacts", tmp_path,
        n_results=7, hybrid=True, use_reranker=True,
        chunks_path=tmp_path / "chunks.jsonl",
    )

    assert results == [{
        "text": "result text",
        "metadata": {"content_type": "case_opinion"},
        "score": 0.8,
    }]
    assert observed["kwargs"]["n_results"] == 7
    assert observed["kwargs"]["hybrid"] is True
    assert observed["kwargs"]["use_reranker"] is True


def test_eval_preserves_context_for_grounding_without_changing_rank(
        monkeypatch, tmp_path):
    observed = {}
    hit = rag.SearchHit(
        "ranked text", {"stable_id": "chunk_primary"}, 0.8,
        source_id="chunk_primary")
    hit.context_segments = [rag.ContextSegment(
        text="neighbor text",
        metadata={"page_range": "2"},
        source_id="chunk_neighbor",
        relation="next",
        distance=1,
    )]
    response = rag.SearchResponse(
        hits=[hit], backend="chroma", requested_mode="vector",
        effective_mode="vector", reranker_applied=False,
        context_window=1)

    def fake_search(*args, **kwargs):
        observed.update(kwargs)
        return response

    monkeypatch.setattr(rag, "search_index", fake_search)
    results = retrieval_eval.run_search(
        "query", tmp_path, chunks_path=tmp_path / "chunks.jsonl",
        context_window=1)

    assert len(results) == 1
    assert results[0]["text"] == "ranked text"
    assert [item["source_id"] for item in results[0]["context"]] == [
        "chunk_neighbor"]
    assert observed["context_window"] == 1


def test_evaluate_uses_success_name_fetches_max_k_and_counts_empty_type_miss(
        monkeypatch, tmp_path):
    observed = []

    def fake_run_search(query, db_path, **kwargs):
        observed.append(kwargs)
        return []

    monkeypatch.setattr(retrieval_eval, "run_search", fake_run_search)
    metrics = retrieval_eval.evaluate(
        [{
            "query": "minimum contacts",
            "expected_keywords": ["International Shoe"],
            "expected_type": "case_opinion",
        }],
        tmp_path,
        k_values=[2, 7],
        n_results=3,
    )

    assert metrics == {
        "success@2": 0.0,
        "success@7": 0.0,
        "mrr": 0.0,
        "type_accuracy": 0.0,
        "num_type_queries": 1,
        "num_queries": 1,
    }
    assert observed[0]["n_results"] == 7


def test_chroma_batch_embeds_context_but_stores_raw_text():
    record = {
        "text": "Raw holding text.",
        "metadata": {
            "context": "Chapter 2: Personal Jurisdiction",
            "case_names": ["International Shoe"],
            "cross_references": [],
            "headings": ["Minimum Contacts"],
            "primary_case": None,
            "chapter_title": None,
            "chapter_num": None,
        },
    }

    ids, embedding_inputs, documents, metadatas = (
        rag._prepare_chroma_batch([record]))

    assert ids == [rag._chunk_id(record)]
    assert embedding_inputs == [
        "Chapter 2: Personal Jurisdiction\n\nRaw holding text."
    ]
    assert documents == ["Raw holding text."]
    assert metadatas[0]["context"] == "Chapter 2: Personal Jurisdiction"
    assert metadatas[0]["case_names"] == "International Shoe"
    assert metadatas[0]["chapter_num"] == -1


def test_chunk_id_distinguishes_same_page_source_occurrences():
    def record(ref):
        return {
            "text": "The same rule appears twice on this page.",
            "metadata": {
                "source_file": "book",
                "page_start": 10,
                "page_end": 10,
                "page_range": "pp.10-10",
                "source_items": [{"ref": ref}],
            },
        }

    first = record("#/texts/1")
    second = record("#/texts/2")

    assert rag._chunk_id(first) != rag._chunk_id(second)
    del first["metadata"]["source_items"]
    del second["metadata"]["source_items"]
    assert rag._chunk_id(first) == rag._chunk_id(second)


def test_chroma_batch_embeds_heading_for_short_source_answer():
    record = {
        "text": "No.",
        "metadata": {
            "context": "",
            "case_names": [],
            "cross_references": [],
            "headings": ["Did they bill for secretarial time?"],
            "primary_case": None,
            "chapter_title": "Chapter 9",
            "chapter_num": 9,
        },
    }

    _, embedding_inputs, documents, _ = rag._prepare_chroma_batch([record])

    assert embedding_inputs == [
        "Did they bill for secretarial time?\n\nNo."]
    assert documents == ["No."]


def test_chroma_result_normalizer_strips_legacy_context_prefix():
    docs, metas, scores = rag._unpack_chroma_results({
        "documents": [["Context line\n\nRaw text"]],
        "metadatas": [[{"context": "Context line"}]],
        "distances": [[0.25]],
    })

    assert docs == ["Raw text"]
    assert metas == [{"context": "Context line"}]
    assert scores == [0.75]


def test_search_rejects_invalid_requests(tmp_path):
    with pytest.raises(ValueError, match="db_backend"):
        rag.search_index("query", tmp_path, db_backend="unknown")
    with pytest.raises(ValueError, match="blank"):
        rag.search_index("  ", tmp_path)
    with pytest.raises(ValueError, match="at least 1"):
        rag.search_index("query", tmp_path, n_results=0)
    with pytest.raises(FileNotFoundError):
        rag.search_index("query", tmp_path / "missing")


@pytest.mark.parametrize(
    ("flags", "expected_hybrid", "expected_reranker"),
    [
        ([], None, None),
        (["--hybrid"], True, None),
        (["--vector-only"], False, None),
        (["--rerank"], None, True),
        (["--no-rerank"], None, False),
    ],
)
def test_query_cli_preserves_auto_and_explicit_modes(
        monkeypatch, flags, expected_hybrid, expected_reranker):
    observed = {}
    monkeypatch.setattr(rag, "_validate_api_key", lambda model: None)
    monkeypatch.setattr(
        rag, "_query_index_for_backend",
        lambda *args, **kwargs: observed.update(kwargs),
    )
    monkeypatch.setattr(
        sys, "argv", ["rag.py", "query", "minimum contacts", *flags])

    rag.main()

    assert observed["hybrid"] is expected_hybrid
    assert observed["use_reranker"] is expected_reranker
    assert observed["rrf_k"] == rag.DEFAULT_RRF_K
    assert observed["dense_weight"] == rag.DEFAULT_DENSE_RRF_WEIGHT
    assert observed["sparse_weight"] == rag.DEFAULT_SPARSE_RRF_WEIGHT


@pytest.mark.parametrize(
    "flags",
    [
        ["--hybrid", "--vector-only"],
        ["--rerank", "--no-rerank"],
    ],
)
def test_query_cli_rejects_conflicting_retrieval_flags(monkeypatch, flags):
    monkeypatch.setattr(
        sys, "argv", ["rag.py", "query", "minimum contacts", *flags])

    with pytest.raises(SystemExit) as error:
        rag.main()

    assert error.value.code == 2


@pytest.mark.parametrize("backend", ["chroma", "qdrant"])
def test_search_rejects_embedding_model_that_differs_from_manifest(
        search_fakes, backend):
    rag._save_index_manifest(
        search_fakes.db_dir,
        backend=backend,
        collection_name=rag.DEFAULT_COLLECTION,
        embedding_model="indexed-model",
        embedding_dimension=2,
        chunk_hashes={"chunk_a": "hash_a"},
    )

    with pytest.raises(ValueError, match="embedding_model"):
        rag.search_index(
            "minimum contacts",
            search_fakes.db_dir,
            db_backend=backend,
            embedding_model="wrong-model",
            use_reranker=False,
        )

    assert search_fakes.state["embedding_calls"] == []


@pytest.mark.parametrize("backend", ["chroma", "qdrant"])
def test_search_rejects_query_vector_with_manifest_dimension_mismatch(
        search_fakes, backend):
    rag._save_index_manifest(
        search_fakes.db_dir,
        backend=backend,
        collection_name=rag.DEFAULT_COLLECTION,
        embedding_model="fake-embedding",
        embedding_dimension=3,
        chunk_hashes={"chunk_a": "hash_a"},
    )

    with pytest.raises(ValueError, match="3"):
        rag.search_index(
            "minimum contacts",
            search_fakes.db_dir,
            db_backend=backend,
            embedding_model="fake-embedding",
            use_reranker=False,
        )


def test_rrf_fuses_matching_text_without_chunk_ids():
    merged = rag._reciprocal_rank_fusion([
        [("same text", {}, 0.9)],
        [("same text", {}, 12.0)],
    ])

    assert len(merged) == 1
    assert merged[0][0] == "same text"
    assert merged[0][2] == pytest.approx(2 / (rag.DEFAULT_RRF_K + 1))


def test_bm25_and_dense_results_share_the_same_stable_identity(tmp_path):
    chunks = tmp_path / "chunks.jsonl"
    record = {
        "text": "minimum contacts govern forum jurisdiction",
        "metadata": {"chunk_index": 7, "source_file": "book"},
    }
    chunks.write_text(json.dumps(record) + "\n", encoding="utf-8")
    rag._bm25_cache.clear()

    documents, metadatas, scores = rag._bm25_search(
        "minimum contacts", chunks, 1)
    stable_id = rag._chunk_id(record)
    merged = rag._reciprocal_rank_fusion([
        [(record["text"], {**record["metadata"], "stable_id": stable_id}, 0.9)],
        list(zip(documents, metadatas, scores)),
    ])

    assert metadatas[0]["stable_id"] == stable_id
    assert len(merged) == 1
    assert merged[0][2] == pytest.approx(2 / (rag.DEFAULT_RRF_K + 1))


@pytest.mark.parametrize(
    ("left", "right", "required"),
    [
        (
            "28 U.S.C. § 1332(a)(1)",
            "28 USC section 1332(a)(1)",
            {"28", "usc", "section", "1332(a)(1)"},
        ),
        (
            "Fed. R. Civ. P. 12(b)(6)",
            "Rule 12(b)(6)",
            {"rule", "12(b)(6)"},
        ),
        ("juris-\ndiction", "jurisdiction", {"jurisdiction"}),
        ("Ｒｕｌｅ １２（ｂ）（６）", "Rule 12(b)(6)", {"rule", "12(b)(6)"}),
        ("juris\u00addiction", "jurisdiction", {"jurisdiction"}),
    ],
)
def test_legal_search_tokens_normalize_equivalent_forms(left, right, required):
    left_tokens = set(rag._legal_search_tokens(left))
    right_tokens = set(rag._legal_search_tokens(right))

    assert required <= left_tokens
    assert required <= right_tokens


def test_bm25_searches_bounded_legal_metadata_but_returns_raw_text(tmp_path):
    records = [
        {
            "text": "The decision explains the governing standard.",
            "metadata": {
                "primary_case": "International Shoe Co. v. Washington",
                "context": "Minimum contacts and personal jurisdiction.",
            },
        },
        {
            "text": "The decision explains a different standard.",
            "metadata": {"primary_case": "Erie Railroad Co. v. Tompkins"},
        },
        {
            "text": "The court addresses another issue.",
            "metadata": {"primary_case": "Pennoyer v. Neff"},
        },
    ]
    chunks = tmp_path / "chunks.jsonl"
    chunks.write_text(
        "".join(json.dumps(record) + "\n" for record in records),
        encoding="utf-8",
    )
    rag._bm25_cache.clear()

    documents, metadatas, _ = rag._bm25_search(
        "International Shoe minimum contacts", chunks, 3)

    assert documents[0] == records[0]["text"]
    assert metadatas[0]["primary_case"].startswith("International Shoe")
    assert all("International Shoe" not in document for document in documents)


def test_bm25_parses_and_caches_the_captured_byte_snapshot(
        monkeypatch, tmp_path):
    chunks = tmp_path / "chunks.jsonl"
    generation_a = {"text": "alpha doctrine", "metadata": {}}
    generation_b = {"text": "bravo doctrine", "metadata": {}}
    rag._atomic_write_jsonl(chunks, [generation_a])
    expected_a = hashlib.sha256(chunks.read_bytes()).hexdigest()
    parse_snapshot = rag._parse_index_records_strict
    replaced = False

    def replace_then_parse(raw, path):
        nonlocal replaced
        if not replaced:
            replaced = True
            rag._atomic_write_jsonl(path, [generation_b])
        return parse_snapshot(raw, path)

    rag._bm25_cache.clear()
    monkeypatch.setattr(rag, "_parse_index_records_strict", replace_then_parse)
    documents, _, _ = rag._bm25_search(
        "alpha", chunks, 1, expected_source_sha256=expected_a)
    expected_b = hashlib.sha256(chunks.read_bytes()).hexdigest()
    documents_b, _, _ = rag._bm25_search(
        "bravo", chunks, 1, expected_source_sha256=expected_b)

    assert documents == [generation_a["text"]]
    assert documents_b == [generation_b["text"]]


def test_bm25_cache_sha_prevents_aba_stat_aliasing(monkeypatch, tmp_path):
    chunks = tmp_path / "chunks.jsonl"
    generation_a = {"text": "alpha doctrine", "metadata": {}}
    generation_b = {"text": "bravo doctrine", "metadata": {}}
    monkeypatch.setattr(
        rag, "_artifact_stat_fingerprint", lambda _stat: (1, 2, 3, 4, 5))
    rag._bm25_cache.clear()

    for record, query in (
            (generation_a, "alpha"),
            (generation_b, "bravo"),
            (generation_a, "alpha")):
        rag._atomic_write_jsonl(chunks, [record])
        expected = hashlib.sha256(chunks.read_bytes()).hexdigest()
        documents, _, _ = rag._bm25_search(
            query, chunks, 1, expected_source_sha256=expected)
        assert documents == [record["text"]]


def test_qdrant_sparse_query_uses_shared_legal_analyzer(search_fakes):
    query = "28 U.S.C. § 1332(a)(1)"

    rag.search_index(
        query, search_fakes.db_dir, db_backend="qdrant",
        embedding_model="fake-embedding", hybrid=True,
        use_reranker=False, chunks_path=search_fakes.chunks_path,
    )

    sparse = search_fakes.state["qdrant_calls"][0]["prefetch"][1].query
    expected_indices, expected_values = rag._sparse_token_vector(query)
    assert sparse.indices == expected_indices
    assert sparse.values == expected_values


def test_metadata_aware_reranker_scores_enriched_text_but_returns_raw(
        monkeypatch):
    captured = {}

    class FakeReranker:
        def compute_score(self, pairs, normalize):
            captured["pairs"] = pairs
            captured["normalize"] = normalize
            return [0.1, 0.9]

    monkeypatch.setattr(rag, "_get_reranker", lambda model: FakeReranker())
    documents = ["raw first", "raw second"]
    metadatas = [
        {"primary_case": "Case One", "section_path": "Jurisdiction"},
        {"context": "A controlling procedural rule"},
    ]

    ranked_docs, ranked_metas, scores = rag._rerank(
        "query", documents, metadatas, [0.2, 0.3], 2,
        reranker_model="model-a")

    assert "Case: Case One" in captured["pairs"][0][1]
    assert "Section: Jurisdiction" in captured["pairs"][0][1]
    assert captured["pairs"][0][1].endswith("raw first")
    assert ranked_docs == ["raw second", "raw first"]
    assert ranked_metas == [metadatas[1], metadatas[0]]
    assert scores == [0.9, 0.1]


def test_reranker_cache_is_keyed_by_model(monkeypatch):
    loaded = []
    monkeypatch.setenv("RAG_ALLOW_UNPINNED_MODELS", "1")

    class FakeFlagReranker:
        def __init__(self, model_name, **kwargs):
            self.model_name = model_name
            loaded.append((model_name, kwargs))

    module = ModuleType("FlagEmbedding")
    module.FlagReranker = FakeFlagReranker
    monkeypatch.setitem(sys.modules, "FlagEmbedding", module)
    monkeypatch.setattr(rag, "_reranker_instances", {})

    model_a = rag._get_reranker("model-a")
    model_b = rag._get_reranker("model-b")

    assert rag._get_reranker("model-a") is model_a
    assert model_b is not model_a
    assert [item[0] for item in loaded] == ["model-a", "model-b"]


def test_weighted_rrf_can_calibrate_dense_and_sparse_rankings():
    dense = [
        ("A", {"stable_id": "a"}, 1.0),
        ("B", {"stable_id": "b"}, 0.0),
    ]
    sparse = [
        ("B", {"stable_id": "b"}, 1.0),
        ("A", {"stable_id": "a"}, 0.0),
    ]

    merged = rag._reciprocal_rank_fusion(
        [dense, sparse], k=60, top_n=2, weights=[1, 3])

    assert [item[0] for item in merged] == ["B", "A"]
    assert merged[0][2] == pytest.approx(1 / 62 + 3 / 61)
    assert merged[1][2] == pytest.approx(1 / 61 + 3 / 62)


@pytest.mark.parametrize("weights", [[1], [-1, 1], [float("inf"), 1], [0, 0]])
def test_rrf_rejects_invalid_weights(weights):
    with pytest.raises(ValueError, match="weights"):
        rag._reciprocal_rank_fusion([[], []], weights=weights)


def test_eval_rejects_missing_ground_truth(tmp_path):
    query_file = tmp_path / "queries.jsonl"
    query_file.write_text(
        json.dumps({"query": "minimum contacts", "expected_keywords": []}) + "\n",
        encoding="utf-8",
    )

    with pytest.raises(ValueError, match="expected_keywords"):
        retrieval_eval.load_queries(query_file)
