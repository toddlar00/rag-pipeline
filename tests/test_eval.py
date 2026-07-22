import hashlib
import json
from types import SimpleNamespace

import pytest

import eval as retrieval_eval
import rag


def _judged_query():
    return {
        "query_id": "personal-jurisdiction",
        "query": "minimum contacts",
        "judgments": [
            {"chunk_id": "chunk-primary", "relevance": 3},
            {"chunk_id": "chunk-secondary", "relevance": 2},
            {"chunk_id": "chunk-missing", "relevance": 1},
        ],
        "expected_type": "case_opinion",
    }


def _ranked_results():
    return [
        {
            "text": "International Shoe established minimum contacts.",
            "metadata": {
                "stable_id": "chunk-primary",
                "source_file": "source-primary",
                "content_type": "case_opinion",
            },
            "score": 0.99,
        },
        {
            "text": "An unrelated procedural passage.",
            "metadata": {
                "stable_id": "chunk-irrelevant",
                "source_file": "source-other",
                "content_type": "author_narrative",
            },
            "score": 0.75,
        },
        {
            "text": "A second relevant source passage.",
            "metadata": {
                "stable_id": "chunk-secondary",
                "source_file": "source-secondary",
                "content_type": "case_opinion",
            },
            "score": 0.7,
        },
        {
            "text": "A duplicate hit for the primary judgment.",
            "metadata": {
                "stable_id": "chunk-primary",
                "source_file": "source-primary",
                "content_type": "case_opinion",
            },
            "score": 0.6,
        },
    ]


def test_load_queries_accepts_legacy_and_graded_schemas(tmp_path):
    path = tmp_path / "queries.jsonl"
    path.write_text(
        "\n".join([
            json.dumps({
                "query": "legacy query",
                "expected_keywords": ["legacy answer"],
            }),
            json.dumps(_judged_query()),
        ]) + "\n",
        encoding="utf-8",
    )

    queries = retrieval_eval.load_queries(path)

    assert len(queries) == 2
    assert queries[0]["expected_keywords"] == ["legacy answer"]
    assert queries[1]["judgments"][0]["relevance"] == 3


def test_declared_corpus_fingerprint_rejects_stale_judgments(tmp_path):
    chunks = tmp_path / "chunks.jsonl"
    chunks.write_text('{"text":"current"}\n', encoding="utf-8")
    queries = [{
        **_judged_query(),
        "corpus": {"sha256": "0" * 64, "record_count": 1},
    }]

    with pytest.raises(ValueError, match="different chunks snapshot"):
        retrieval_eval._validate_declared_corpus(queries, chunks)


def _write_manifested_eval_corpus(tmp_path):
    record = {
        "text": "International Shoe established minimum contacts.",
        "metadata": {"source_file": "book", "page_start": 10},
    }
    chunks = tmp_path / "chunks.jsonl"
    chunks.write_text(json.dumps(record) + "\n", encoding="utf-8")
    db = tmp_path / "db"
    db.mkdir()
    chunk_id = rag._chunk_id(record)
    rag._save_index_manifest(
        db, backend="chroma", collection_name="book",
        embedding_model="model-a", embedding_dimension=3,
        chunk_hashes={chunk_id: rag._chunk_hash(record)},
        source_sha256=hashlib.sha256(chunks.read_bytes()).hexdigest(),
        source_record_count=1)
    queries = [{
        "query": "minimum contacts",
        "judgments": [{"chunk_id": chunk_id, "relevance": 3}],
        "corpus": {"record_count": 1},
    }]
    return chunks, db, queries


def test_declared_index_validation_skips_unpinned_queries(tmp_path):
    assert retrieval_eval._validate_declared_index(
        [_judged_query()], tmp_path / "missing.jsonl", tmp_path / "missing-db",
        db_backend="chroma", collection="book", embedding_model="model-a",
    ) is None


def test_declared_index_requires_exact_manifest_and_physical_count(
        monkeypatch, tmp_path):
    chunks, db, queries = _write_manifested_eval_corpus(tmp_path)
    monkeypatch.setattr(rag, "_index_collection_count", lambda *args, **kwargs: 1)

    snapshot = retrieval_eval._validate_declared_index(
        queries, chunks, db, db_backend="chroma", collection="book",
        embedding_model="model-a", rag_module=rag)

    assert snapshot["record_count"] == 1
    assert snapshot["schema_version"] == rag.INDEX_MANIFEST_SCHEMA_VERSION
    assert snapshot["embedding_dimension"] == 3


def test_declared_index_rejects_incomplete_update(monkeypatch, tmp_path):
    chunks, db, queries = _write_manifested_eval_corpus(tmp_path)
    rag._begin_index_update(
        db, backend="chroma", collection_name="book",
        source_sha256="target-source", source_record_count=1)
    monkeypatch.setattr(
        rag, "_index_collection_count",
        lambda *args, **kwargs: pytest.fail(
            "dirty evaluation must fail before inspecting physical counts"),
    )

    with pytest.raises(ValueError, match="Index update is incomplete"):
        retrieval_eval._validate_declared_index(
            queries, chunks, db, db_backend="chroma", collection="book",
            embedding_model="model-a", rag_module=rag)


def test_declared_index_rejects_missing_manifest(tmp_path):
    chunks, db, queries = _write_manifested_eval_corpus(tmp_path)
    rag._index_manifest_path(
        db, backend="chroma", collection_name="book").unlink()

    with pytest.raises(ValueError, match="requires a compatible index manifest"):
        retrieval_eval._validate_declared_index(
            queries, chunks, db, db_backend="chroma", collection="book",
            embedding_model="model-a", rag_module=rag)


def test_declared_index_rejects_source_fingerprint_mismatch(monkeypatch, tmp_path):
    chunks, db, queries = _write_manifested_eval_corpus(tmp_path)
    manifest_path = rag._index_manifest_path(
        db, backend="chroma", collection_name="book")
    manifest = json.loads(manifest_path.read_text(encoding="utf-8"))
    manifest["source_sha256"] = "0" * 64
    manifest_path.write_text(json.dumps(manifest), encoding="utf-8")
    monkeypatch.setattr(rag, "_index_collection_count", lambda *args, **kwargs: 1)

    with pytest.raises(ValueError, match="different chunks artifact"):
        retrieval_eval._validate_declared_index(
            queries, chunks, db, db_backend="chroma", collection="book",
            embedding_model="model-a", rag_module=rag)


def test_declared_index_rejects_physical_count_mismatch(monkeypatch, tmp_path):
    chunks, db, queries = _write_manifested_eval_corpus(tmp_path)
    monkeypatch.setattr(rag, "_index_collection_count", lambda *args, **kwargs: 0)

    with pytest.raises(ValueError, match="expected 1, got 0"):
        retrieval_eval._validate_declared_index(
            queries, chunks, db, db_backend="chroma", collection="book",
            embedding_model="model-a", rag_module=rag)


@pytest.mark.parametrize(
    ("query", "message"),
    [
        ({"query": "missing gold"}, "expected_keywords.*judgments"),
        ({
            "query": "two IDs",
            "judgments": [{
                "chunk_id": "chunk-a", "source_id": "source-a",
                "relevance": 1,
            }],
        }, "exactly one"),
        ({
            "query": "bad grade",
            "judgments": [{"chunk_id": "chunk-a", "relevance": -1}],
        }, "finite number"),
        ({
            "query": "no positive gold",
            "judgments": [{"chunk_id": "chunk-a", "relevance": 0}],
        }, "at least one relevant"),
        ({
            "query": "duplicate gold",
            "judgments": [
                {"chunk_id": "chunk-a", "relevance": 1},
                {"chunk_id": "chunk-a", "relevance": 2},
            ],
        }, "duplicates"),
        ({
            "query": "mixed IDs",
            "judgments": [
                {"chunk_id": "chunk-a", "relevance": 2},
                {"source_id": "source-a", "relevance": 1},
            ],
        }, "one ID type"),
    ],
)
def test_query_validation_rejects_invalid_judgments(query, message):
    with pytest.raises(ValueError, match=message):
        retrieval_eval._validate_query(query)


def test_judged_metrics_and_details_use_stable_ids_once(monkeypatch, tmp_path):
    monkeypatch.setattr(
        retrieval_eval, "run_search",
        lambda *args, **kwargs: _ranked_results(),
    )

    report = retrieval_eval.evaluate(
        [_judged_query()],
        tmp_path,
        k_values=[1, 3],
        include_details=True,
    )

    assert report["success@1"] == 1.0
    assert report["success@3"] == 1.0
    assert report["mrr"] == 1.0
    assert report["type_accuracy"] == 1.0
    assert report["recall@1"] == 0.333
    assert report["recall@3"] == 0.667
    assert report["ndcg@1"] == 1.0
    assert report["ndcg@3"] == 0.905
    assert report["map"] == 0.556
    assert report["num_judged_queries"] == 1

    detail = report["query_details"][0]
    assert detail["query_id"] == "personal-jurisdiction"
    assert detail["judgment_mode"] == "graded_ids"
    assert detail["results"][0]["chunk_id"] == "chunk-primary"
    assert detail["results"][0]["relevance"] == 3.0
    assert detail["results"][2]["chunk_id"] == "chunk-secondary"
    assert detail["results"][2]["relevance"] == 2.0
    assert detail["results"][3]["relevance"] == 0.0
    assert detail["results"][3]["matched_judgments"] == []


def test_mixed_schema_aggregates_finite_set_metrics_only_over_judged_queries(
        monkeypatch, tmp_path):
    def fake_search(query, *args, **kwargs):
        if query == "legacy":
            return [{
                "text": "the expected legacy phrase",
                "metadata": {"content_type": "author_narrative"},
                "score": 1.0,
            }]
        return _ranked_results()

    monkeypatch.setattr(retrieval_eval, "run_search", fake_search)
    queries = [
        {
            "query": "legacy",
            "expected_keywords": ["expected legacy phrase"],
            "expected_type": "author_narrative",
        },
        _judged_query(),
    ]

    report = retrieval_eval.evaluate(queries, tmp_path, k_values=[1])

    assert report["success@1"] == 1.0
    assert report["mrr"] == 1.0
    assert report["type_accuracy"] == 1.0
    assert report["recall@1"] == 0.333
    assert report["num_judged_queries"] == 1
    assert report["num_queries"] == 2
    assert "query_details" not in report


def test_legacy_details_record_keyword_matches(monkeypatch, tmp_path):
    monkeypatch.setattr(
        retrieval_eval,
        "run_search",
        lambda *args, **kwargs: [{
            "text": "International Shoe and minimum contacts",
            "metadata": {},
            "score": 0.8,
        }],
    )

    report = retrieval_eval.evaluate(
        [{
            "query": "legacy",
            "expected_keywords": ["International Shoe", "unmatched"],
        }],
        tmp_path,
        k_values=[1],
        include_details=True,
    )

    detail = report["query_details"][0]
    assert detail["judgment_mode"] == "legacy_keywords"
    assert detail["results"][0]["keyword_matches"] == ["International Shoe"]
    assert "recall@1" not in report
    assert "map" not in report


def test_evaluate_fetches_largest_requested_cutoff(monkeypatch, tmp_path):
    observed = {}

    def fake_search(*args, **kwargs):
        observed.update(kwargs)
        return []

    monkeypatch.setattr(retrieval_eval, "run_search", fake_search)
    retrieval_eval.evaluate(
        [{"query": "legacy", "expected_keywords": ["answer"]}],
        tmp_path,
        k_values=[2, 8],
        n_results=3,
    )

    assert observed["n_results"] == 8


def test_evaluate_holds_one_reentrant_lease_across_all_queries(
        monkeypatch, tmp_path):
    lock_attempts = 0
    unlocks = 0
    try_lock = rag._try_vector_file_lock
    unlock = rag._unlock_vector_file

    def track_lock(handle):
        nonlocal lock_attempts
        lock_attempts += 1
        return try_lock(handle)

    def track_unlock(handle):
        nonlocal unlocks
        unlocks += 1
        return unlock(handle)

    def fake_search(_query, db_path, **_kwargs):
        with rag._vector_store_lock(
                db_path, backend="qdrant", collection_name="other",
                operation="nested evaluation query", timeout=0):
            return []

    monkeypatch.setattr(rag, "_try_vector_file_lock", track_lock)
    monkeypatch.setattr(rag, "_unlock_vector_file", track_unlock)
    monkeypatch.setattr(retrieval_eval, "run_search", fake_search)

    retrieval_eval.evaluate(
        [
            {"query": "first", "expected_keywords": ["answer"]},
            {"query": "second", "expected_keywords": ["answer"]},
        ],
        tmp_path,
        db_backend="chroma",
        collection="book",
        lock_timeout=1,
    )

    assert lock_attempts == 1
    assert unlocks == 1


def test_run_search_recovers_chroma_chunk_id_from_chunks_artifact(
        monkeypatch, tmp_path):
    record = {
        "text": "A stable source passage.",
        "metadata": {
            "chunk_index": 7,
            "source_file": "book",
            "content_type": "case_opinion",
        },
    }
    chunks = tmp_path / "chunks.jsonl"
    chunks.write_text(json.dumps(record) + "\n", encoding="utf-8")
    hit = SimpleNamespace(
        text=record["text"], metadata=record["metadata"], score=0.9)
    monkeypatch.setattr(
        rag,
        "search_index",
        lambda *args, **kwargs: SimpleNamespace(hits=[hit]),
    )
    retrieval_eval._chunk_identity_cache.clear()

    results = retrieval_eval.run_search(
        "query", tmp_path / "db", chunks_path=chunks)

    assert results[0]["chunk_id"] == rag._chunk_id(record)


def test_duplicate_text_does_not_override_metadata_identity(tmp_path):
    records = [
        {
            "text": "Repeated text",
            "metadata": {
                "chunk_index": index,
                "source_file": "book",
                "page_start": index,
            },
        }
        for index in (1, 2)
    ]
    chunks = tmp_path / "chunks.jsonl"
    chunks.write_text(
        "".join(json.dumps(record) + "\n" for record in records),
        encoding="utf-8",
    )
    retrieval_eval._chunk_identity_cache.clear()

    lookup = retrieval_eval._chunk_identity_lookup(chunks, rag)

    assert lookup["text"]["Repeated text"] is None
    assert retrieval_eval._recover_chunk_id({
        "text": "Repeated text",
        "metadata": {"chunk_index": 2, "source_file": "book"},
    }, lookup) == rag._chunk_id(records[1])


def test_chunk_id_recovery_failure_does_not_discard_search_results(
        monkeypatch, tmp_path, caplog):
    chunks = tmp_path / "chunks.jsonl"
    chunks.write_text("{malformed\n", encoding="utf-8")
    hit = SimpleNamespace(text="Still returned", metadata={}, score=0.5)
    monkeypatch.setattr(
        rag,
        "search_index",
        lambda *args, **kwargs: SimpleNamespace(hits=[hit]),
    )
    retrieval_eval._chunk_identity_cache.clear()

    results = retrieval_eval.run_search(
        "query", tmp_path / "db", chunks_path=chunks)

    assert results == [{"text": "Still returned", "metadata": {}, "score": 0.5}]
    assert "Could not recover stable chunk IDs" in caplog.text


def test_chunk_identity_cache_keys_exact_snapshot_sha(monkeypatch, tmp_path):
    chunks = tmp_path / "chunks.jsonl"
    first = {"text": "alpha identity", "metadata": {"chunk_index": 0}}
    second = {"text": "bravo identity", "metadata": {"chunk_index": 0}}
    monkeypatch.setattr(
        rag, "_artifact_stat_fingerprint", lambda _stat: (1, 2, 3, 4, 5))
    retrieval_eval._chunk_identity_cache.clear()

    rag._atomic_write_jsonl(chunks, [first])
    first_lookup = retrieval_eval._chunk_identity_lookup(chunks, rag)
    rag._atomic_write_jsonl(chunks, [second])
    second_lookup = retrieval_eval._chunk_identity_lookup(chunks, rag)

    assert first_lookup["text"][first["text"]] == rag._chunk_id(first)
    assert second_lookup["text"][second["text"]] == rag._chunk_id(second)
    assert first["text"] not in second_lookup["text"]


def test_threshold_helpers_cover_absolute_and_baseline_regressions():
    failures = retrieval_eval._threshold_failures(
        {"mrr": 0.7, "success@5": 0.9},
        {"success@5": 0.8, "mrr": 0.75},
        baseline={"mrr": 0.9},
        regressions={"mrr": 0.1, "ndcg@5": 0.05},
    )

    assert any("mrr=0.7 is below 0.75" in item for item in failures)
    assert any("mrr regressed by 0.200" in item for item in failures)
    assert any("ndcg@5 is absent from the current" in item for item in failures)


def test_cli_writes_detailed_report_and_fails_threshold(
        monkeypatch, tmp_path, caplog):
    output = tmp_path / "reports" / "evaluation.json"
    monkeypatch.setattr(retrieval_eval, "load_queries", lambda path: [_judged_query()])
    monkeypatch.setattr(
        retrieval_eval,
        "evaluate",
        lambda *args, **kwargs: {
            "success@5": 0.5,
            "mrr": 0.4,
            "recall@5": 0.25,
            "ndcg@5": 0.3,
            "map": 0.2,
            "num_judged_queries": 1,
            "num_queries": 1,
            "query_details": [{"query": "minimum contacts", "results": []}],
        },
    )

    exit_code = retrieval_eval.main([
        "--queries", str(tmp_path / "queries.jsonl"),
        "--chunks", str(tmp_path / "chunks.jsonl"),
        "--db", str(tmp_path / "db"),
        "--collection", "book",
        "--k", "5",
        "--json-report", str(output),
        "--fail-under", "mrr=0.5",
    ])

    assert exit_code == 2
    payload = json.loads(output.read_text(encoding="utf-8"))
    assert payload["schema_version"] == retrieval_eval.REPORT_SCHEMA_VERSION
    assert payload["metrics"]["ndcg@5"] == 0.3
    assert payload["query_details"][0]["query"] == "minimum contacts"
    assert "Evaluation threshold failed" in caplog.text


def test_cli_can_gate_against_a_baseline_report(monkeypatch, tmp_path):
    baseline = tmp_path / "baseline.json"
    baseline.write_text(json.dumps({"metrics": {"mrr": 0.8}}), encoding="utf-8")
    monkeypatch.setattr(retrieval_eval, "load_queries", lambda path: [_judged_query()])
    monkeypatch.setattr(
        retrieval_eval,
        "evaluate",
        lambda *args, **kwargs: {
            "mrr": 0.65,
            "num_queries": 1,
            "query_details": [],
        },
    )

    exit_code = retrieval_eval.main([
        "--chunks", str(tmp_path / "chunks.jsonl"),
        "--db", str(tmp_path / "db"),
        "--collection", "book",
        "--baseline-report", str(baseline),
        "--max-regression", "mrr=0.1",
    ])

    assert exit_code == 2


def test_compare_table_uses_requested_cutoffs(monkeypatch, tmp_path, capsys):
    monkeypatch.setattr(
        retrieval_eval, "load_queries", lambda path: [_judged_query()])
    monkeypatch.setattr(
        retrieval_eval,
        "evaluate",
        lambda *args, **kwargs: {
            "success@1": 0.5,
            "success@3": 0.75,
            "mrr": 0.4,
            "type_accuracy": 1.0,
            "num_queries": 1,
            "query_details": [],
        },
    )

    exit_code = retrieval_eval.main([
        "--chunks", str(tmp_path / "chunks.jsonl"),
        "--db", str(tmp_path / "db"),
        "--collection", "book",
        "--compare",
        "--k", "1", "3",
    ])

    output = capsys.readouterr().out
    assert exit_code == 0
    assert "Success@1" in output
    assert "Success@3" in output
    assert "Success@5" not in output


def test_cli_rejects_malformed_thresholds(tmp_path):
    with pytest.raises(SystemExit) as error:
        retrieval_eval.main([
            "--chunks", str(tmp_path / "chunks.jsonl"),
            "--db", str(tmp_path / "db"),
            "--collection", "book",
            "--fail-under", "mrr",
        ])

    assert error.value.code == 2


def test_cli_rejects_overwriting_its_baseline(monkeypatch, tmp_path):
    report = tmp_path / "report.json"
    report.write_text(json.dumps({"metrics": {"mrr": 0.8}}), encoding="utf-8")
    monkeypatch.setattr(
        retrieval_eval, "load_queries", lambda path: [_judged_query()])

    with pytest.raises(SystemExit) as error:
        retrieval_eval.main([
            "--chunks", str(tmp_path / "chunks.jsonl"),
            "--db", str(tmp_path / "db"),
            "--collection", "book",
            "--json-report", str(report),
            "--baseline-report", str(report),
            "--max-regression", "mrr=0.1",
        ])

    assert error.value.code == 2


def test_run_search_propagates_unavailable_index(monkeypatch, tmp_path):
    monkeypatch.setattr(
        rag,
        "search_index",
        lambda *args, **kwargs: (_ for _ in ()).throw(
            FileNotFoundError("missing index")),
    )

    with pytest.raises(FileNotFoundError, match="missing index"):
        retrieval_eval.run_search("query", tmp_path)


def test_json_report_writer_creates_parent_and_trailing_newline(tmp_path):
    path = tmp_path / "nested" / "report.json"

    retrieval_eval._write_report(path, {"metrics": {"mrr": 1.0}})

    assert json.loads(path.read_text(encoding="utf-8"))["metrics"]["mrr"] == 1.0
    assert path.read_text(encoding="utf-8").endswith("\n")
