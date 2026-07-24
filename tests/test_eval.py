import hashlib
import json
from pathlib import Path
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


def test_corpus_pin_coverage_requires_every_query_and_both_fields():
    complete = {
        **_judged_query(),
        "corpus": {"sha256": "a" * 64, "record_count": 2},
    }
    with pytest.raises(ValueError, match="every query.*SHA-256.*record count"):
        retrieval_eval._validate_corpus_pin_coverage(
            [complete, _judged_query()])
    with pytest.raises(ValueError, match="every query.*SHA-256.*record count"):
        retrieval_eval._validate_corpus_pin_coverage([{
            **_judged_query(), "corpus": {"record_count": 2},
        }])

    retrieval_eval._validate_corpus_pin_coverage([_judged_query()])


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
    assert snapshot["model_artifact_lock_sha256"] == (
        rag._model_artifact_lock_sha256())


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
            "query": "unbounded grade",
            "judgments": [{"chunk_id": "chunk-a", "relevance": 101}],
        }, "0 to 100"),
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
    monkeypatch.setattr(
        retrieval_eval, "load_queries", lambda path: [_judged_query()])
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
    assert payload["configuration"]["model_artifact_lock_sha256"] == (
        rag._model_artifact_lock_sha256())
    assert payload["metrics"]["ndcg@5"] == 0.3
    assert payload["query_details"][0]["query"] == "minimum contacts"
    assert "Evaluation threshold failed" in caplog.text


def test_cli_can_gate_against_a_baseline_report(monkeypatch, tmp_path):
    baseline = tmp_path / "baseline.json"
    baseline.write_text(json.dumps({
        "configuration": {
            "model_artifact_lock_sha256": rag._model_artifact_lock_sha256(),
        },
        "metrics": {"mrr": 0.8},
    }), encoding="utf-8")
    corpus_sha256 = "a" * 64
    monkeypatch.setattr(
        retrieval_eval, "load_queries",
        lambda path: [{
            **_judged_query(),
            "corpus": {"sha256": corpus_sha256, "record_count": 1},
        }])
    monkeypatch.setattr(
        retrieval_eval, "_validate_declared_index",
        lambda *_args, **_kwargs: {
            "source_sha256": corpus_sha256,
            "record_count": 1,
            "_identity_lookup": {},
        })
    monkeypatch.setattr(
        retrieval_eval, "_report_config",
        lambda *_args, **_kwargs: {
            "model_artifact_lock_sha256": rag._model_artifact_lock_sha256(),
        })
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


def test_baseline_rejects_a_different_model_artifact_lock(tmp_path):
    baseline = tmp_path / "baseline.json"
    baseline.write_text(json.dumps({
        "configuration": {"model_artifact_lock_sha256": "a" * 64},
        "metrics": {"mrr": 0.8},
    }), encoding="utf-8")

    with pytest.raises(ValueError, match="model_artifact_lock_sha256"):
        retrieval_eval._load_baseline_metrics(
            baseline,
            expected_configuration={
                "model_artifact_lock_sha256": "b" * 64},
        )

    missing = tmp_path / "legacy-baseline.json"
    missing.write_text(json.dumps({
        "configuration": {}, "metrics": {"mrr": 0.8},
    }), encoding="utf-8")
    with pytest.raises(ValueError, match="lacks model_artifact_lock_sha256"):
        retrieval_eval._load_baseline_metrics(
            missing,
            expected_configuration={
                "model_artifact_lock_sha256": "b" * 64},
        )


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


def test_json_report_writer_preserves_previous_report_on_replace_failure(
        monkeypatch, tmp_path):
    path = tmp_path / "report.json"
    path.write_text('{"old": true}\n', encoding="utf-8")

    monkeypatch.setattr(
        rag.os, "replace",
        lambda *_args: (_ for _ in ()).throw(OSError("replace failed")),
    )

    with pytest.raises(OSError, match="replace failed"):
        retrieval_eval._write_report(path, {"new": True})

    assert json.loads(path.read_text(encoding="utf-8")) == {"old": True}
    assert list(tmp_path.glob(".report.json.*.tmp")) == []


def test_query_schema_accepts_abstention_filters_and_slices():
    retrieval_eval._validate_query({
        "query": "unsupported proposition",
        "expected_abstain": True,
        "filters": {"content_type": "doctrine", "chapter_num": 2},
        "tags": ["abstention", "filter"],
        "subject": "Property",
        "book": "Property Mini Corpus",
        "review_status": "approved",
    })


def test_release_gates_reject_explicitly_draft_judgments():
    retrieval_eval._validate_release_gate_review_status([{
        "query": "reviewed query",
        "expected_keywords": ["answer"],
        "review_status": "approved",
    }])
    retrieval_eval._validate_release_gate_review_status([{
        "query": "legacy reviewed query",
        "expected_keywords": ["answer"],
    }])

    with pytest.raises(ValueError, match="Release thresholds cannot use"):
        retrieval_eval._validate_release_gate_review_status([{
            "query": "draft query",
            "expected_keywords": ["answer"],
            "review_status": "draft_requires_corpus_owner",
        }])


def test_cli_refuses_thresholds_for_explicitly_draft_queries(
        monkeypatch, tmp_path, caplog):
    monkeypatch.setattr(
        retrieval_eval,
        "load_queries",
        lambda _path: [{
            "query": "draft query",
            "expected_keywords": ["answer"],
            "review_status": "draft_requires_corpus_owner",
        }],
    )

    exit_code = retrieval_eval.main([
        "--queries", str(tmp_path / "queries.jsonl"),
        "--chunks", str(tmp_path / "chunks.jsonl"),
        "--db", str(tmp_path / "db"),
        "--collection", "book",
        "--fail-under", "mrr=0.5",
    ])

    assert exit_code == 1
    assert "Release thresholds cannot use" in caplog.text


@pytest.mark.parametrize(("query", "message"), [
    ({
        "query": "conflicting",
        "expected_abstain": True,
        "judgments": [{"chunk_id": "chunk-a", "relevance": 1}],
    }, "cannot contain relevance"),
    ({
        "query": "bad filter",
        "expected_keywords": ["answer"],
        "filters": {"unknown": "value"},
    }, "unsupported filters"),
    ({
        "query": "bad corpus",
        "expected_keywords": ["answer"],
        "corpus": {"sha256": "short"},
    }, "64-character"),
    ({
        "query": "mixed truth",
        "expected_keywords": ["answer"],
        "judgments": [{"chunk_id": "chunk-a", "relevance": 1}],
    }, "must not mix"),
    ({
        "query": "abstention type",
        "expected_abstain": True,
        "expected_type": "doctrine",
    }, "cannot require an expected type"),
    ({
        "query": "colliding tags",
        "expected_keywords": ["answer"],
        "tags": ["A!", "a?"],
    }, "unique ASCII"),
    ({
        "query": "unknown review state",
        "expected_keywords": ["answer"],
        "review_status": "looks_good_to_me",
    }, "review_status"),
])
def test_query_schema_rejects_ambiguous_adversarial_cases(query, message):
    with pytest.raises(ValueError, match=message):
        retrieval_eval._validate_query(query)


def test_source_id_judgments_validate_against_source_metadata():
    records = [{
        "text": "source text",
        "metadata": {"source_file": "book-source"},
    }]
    retrieval_eval._validate_judged_ids([{
        "query": "source",
        "judgments": [{"source_id": "book-source", "relevance": 2}],
    }], records, rag._chunk_id)


def test_evaluation_scores_filters_abstention_slices_and_redacts_by_default(
        monkeypatch, tmp_path):
    observed = []

    def fake_search(query, _db, **options):
        observed.append((query, options))
        if query == "unsupported":
            return []
        return [{
            "text": "Sensitive source passage.",
            "chunk_id": "chunk-primary",
            "metadata": {
                "content_type": "case_opinion", "chapter_num": 2,
            },
            "score": 0.9,
        }]

    monkeypatch.setattr(retrieval_eval, "run_search", fake_search)
    queries = [{
        "query_id": "filtered",
        "query": "sensitive query",
        "judgments": [{"chunk_id": "chunk-primary", "relevance": 3}],
        "expected_type": "case_opinion",
        "filters": {"content_type": "case_opinion", "chapter_num": 2},
        "tags": ["filter", "citation"],
        "subject": "Procedure",
    }, {
        "query_id": "abstain",
        "query": "unsupported",
        "expected_abstain": True,
        "tags": ["abstention"],
        "subject": "Procedure",
    }]

    report = retrieval_eval.evaluate(
        queries, tmp_path, k_values=[1], include_details=True,
        report_detail="summary", collect_measurements=True,
        embedding_cost_per_million_tokens=2.0)

    assert report["success@1"] == 1.0
    assert report["abstention_accuracy"] == 1.0
    assert report["false_answer_rate"] == 0.0
    assert report["filter_compliance"] == 1.0
    assert report["slice/tag/filter/filter_compliance"] == 1.0
    assert report["slice/tag/abstention/abstention_accuracy"] == 1.0
    subject_hash = hashlib.sha256(b"Procedure").hexdigest()[:16]
    assert report[f"slice/subject/{subject_hash}/total_queries"] == 2
    assert report[f"slice/subject/{subject_hash}/success@1/num_queries"] == 1
    assert report[
        f"slice/subject/{subject_hash}/abstention_accuracy/num_queries"] == 1
    assert report["measurements"]["query_latency_ms"]["count"] == 2
    assert report["costs"]["embedding"]["requests"] == 2
    assert report["costs"]["embedding"]["projected_usd"] is not None
    assert observed[0][1]["content_type"] == "case_opinion"
    assert observed[0][1]["chapter_num"] == 2
    detail = report["query_details"][0]
    assert "query" not in detail
    assert "text_preview" not in detail["results"][0]
    assert "chunk_id" not in detail["results"][0]
    assert "source_id" not in detail["results"][0]
    assert "query_id" not in detail
    assert len(detail["query_id_sha256"]) == 64
    assert len(detail["query_sha256"]) == 64
    assert len(detail["results"][0]["text_sha256"]) == 64


def test_full_report_detail_is_an_explicit_text_opt_in(monkeypatch, tmp_path):
    monkeypatch.setattr(
        retrieval_eval, "run_search",
        lambda *_args, **_kwargs: [{
            "text": "Visible source text", "metadata": {}, "score": 1.0,
        }])

    report = retrieval_eval.evaluate(
        [{"query": "visible query", "expected_keywords": ["Visible"]}],
        tmp_path, k_values=[1], include_details=True, report_detail="full")

    assert report["query_details"][0]["query"] == "visible query"
    assert report["query_details"][0]["results"][0][
        "text_preview"] == "Visible source text"


def test_summary_detail_hashes_legacy_keywords(monkeypatch, tmp_path):
    monkeypatch.setattr(
        retrieval_eval, "run_search",
        lambda *_args, **_kwargs: [{
            "text": "private expected phrase", "metadata": {}, "score": 1.0,
        }])

    report = retrieval_eval.evaluate(
        [{"query": "private query", "expected_keywords": [
            "private expected phrase"]}],
        tmp_path, k_values=[1], include_details=True,
        report_detail="summary")

    result = report["query_details"][0]["results"][0]
    assert "keyword_matches" not in result
    assert result["keyword_match_count"] == 1
    assert len(result["keyword_match_sha256"][0]) == 64


def test_grounding_cases_are_aggregated_without_running_an_llm(
        monkeypatch, tmp_path):
    monkeypatch.setattr(
        retrieval_eval, "run_search",
        lambda *_args, **_kwargs: [{
            "text": "The source supports minimum contacts.",
            "chunk_id": "chunk-primary", "metadata": {}, "score": 1.0,
        }])
    query = {
        "query": "What test applies?",
        "judgments": [{"chunk_id": "chunk-primary", "relevance": 3}],
        "tags": ["citation"],
        "grounding_case": {
            "answer": "Minimum contacts is the test [S1].",
            "expected_abstained": False,
            "expected_citations": ["S1"],
        },
    }

    report = retrieval_eval.evaluate(
        [query], tmp_path, k_values=[1], include_details=True)

    assert report["grounding_accuracy"] == 1.0
    assert report["slice/tag/citation/grounding_accuracy"] == 1.0
    assert report["query_details"][0]["grounding"]["passed"] is True


def test_summary_grounding_detail_hashes_quote_bearing_warnings(
        monkeypatch, tmp_path):
    monkeypatch.setattr(
        retrieval_eval, "run_search",
        lambda *_args, **_kwargs: [{
            "text": "Actual evidence only.",
            "chunk_id": "chunk-primary", "metadata": {}, "score": 1.0,
        }])
    query = {
        "query": "What does it say?",
        "judgments": [{"chunk_id": "chunk-primary", "relevance": 3}],
        "grounding_case": {
            "case_type": "unsupported_quote",
            "answer": 'It says "private invented quotation" [S1].',
            "expected_abstained": True,
            "warning_contains": ["Unsupported direct quotation"],
        },
    }

    report = retrieval_eval.evaluate(
        [query], tmp_path, k_values=[1], include_details=True,
        report_detail="summary")

    grounding = report["query_details"][0]["grounding"]
    assert "warnings" not in grounding
    assert grounding["warning_count"] == 1
    assert len(grounding["warning_sha256"][0]) == 64


def test_baseline_index_snapshot_comparison_ignores_manifest_path(tmp_path):
    baseline = tmp_path / "baseline.json"
    baseline.write_text(json.dumps({
        "configuration": {
            "model_artifact_lock_sha256": "a" * 64,
            "index_snapshot": {
                "manifest_path": "/machine-a/private/index.json",
                "source_sha256": "b" * 64,
                "record_count": 10,
            },
        },
        "metrics": {"mrr": 1.0},
    }), encoding="utf-8")

    metrics = retrieval_eval._load_baseline_metrics(
        baseline,
        expected_configuration={
            "model_artifact_lock_sha256": "a" * 64,
            "index_snapshot": {
                "manifest_path": "C:/machine-b/index.json",
                "source_sha256": "b" * 64,
                "record_count": 10,
            },
        },
    )

    assert metrics == {"mrr": 1.0}


def test_strict_baseline_requires_schema_mode_and_complete_provenance(tmp_path):
    baseline = tmp_path / "incomplete.json"
    baseline.write_text(json.dumps({
        "schema_version": 4,
        "mode": "single",
        "configuration": {"retriever": "bm25"},
        "metrics": {"mrr": 1.0},
    }), encoding="utf-8")

    with pytest.raises(ValueError, match="required provenance"):
        retrieval_eval._load_baseline_metrics(
            baseline,
            expected_configuration={
                "retriever": "bm25",
                "k_values": [1],
                "retrieval_depth": 5,
                "queries_sha256": "a" * 64,
                "index_snapshot": {"source_sha256": "b" * 64},
                "use_reranker": False,
                "hybrid": False,
            },
        )


def test_strict_baseline_rejects_an_old_report_schema(tmp_path):
    configuration = {
        "retriever": "bm25",
        "k_values": [1],
        "retrieval_depth": 5,
        "queries_sha256": "a" * 64,
        "index_snapshot": {"source_sha256": "b" * 64},
        "use_reranker": False,
        "hybrid": False,
    }
    baseline = tmp_path / "old-schema.json"
    baseline.write_text(json.dumps({
        "schema_version": 3,
        "mode": "single",
        "configuration": configuration,
        "metrics": {"mrr": 1.0},
    }), encoding="utf-8")

    with pytest.raises(ValueError, match="schema version"):
        retrieval_eval._load_baseline_metrics(
            baseline, expected_configuration=configuration)


def test_strict_index_baseline_preserves_adaptive_null_modes(tmp_path):
    configuration = {
        "retriever": "index",
        "collection_sha256": "a" * 64,
        "embedding_model": "model-a",
        "db_backend": "chroma",
        "k_values": [1, 5],
        "retrieval_depth": 10,
        "queries_sha256": "b" * 64,
        "index_snapshot": {
            "source_sha256": "c" * 64,
            "record_count": 10,
            "schema_version": 5,
        },
        "use_reranker": None,
        "hybrid": None,
        "reranker_model": "reranker-a",
        "overfetch": 4,
        "rrf_k": 10,
        "dense_weight": 0.5,
        "sparse_weight": 1.0,
        "model_artifact_lock_sha256": "d" * 64,
    }
    baseline = tmp_path / "adaptive.json"
    baseline.write_text(json.dumps({
        "schema_version": retrieval_eval.REPORT_SCHEMA_VERSION,
        "mode": "single",
        "configuration": configuration,
        "metrics": {"mrr": 1.0},
    }), encoding="utf-8")

    assert retrieval_eval._load_baseline_metrics(
        baseline, expected_configuration=configuration) == {"mrr": 1.0}


def test_collection_provenance_is_portable_across_report_detail_modes():
    raw = retrieval_eval._portable_configuration({"collection": "private book"})
    redacted = retrieval_eval._portable_configuration({
        "collection_sha256": hashlib.sha256(b"private book").hexdigest(),
    })
    assert raw == redacted


def test_threshold_helper_supports_upper_bounds():
    failures = retrieval_eval._threshold_failures(
        {"false_answer_rate": 0.2}, {},
        maximums={"false_answer_rate": 0.0})
    assert failures == ["false_answer_rate=0.2 is above 0.0"]


def test_thresholds_fail_closed_for_nonfinite_or_boolean_metrics():
    failures = retrieval_eval._threshold_failures(
        {"nan": float("nan"), "boolean": True},
        {"nan": 0.0}, maximums={"boolean": 1.0})
    assert len(failures) == 2


def test_baseline_rejects_nonfinite_metrics(tmp_path):
    baseline = tmp_path / "nonfinite.json"
    baseline.write_text(
        '{"metrics":{"mrr":NaN}}', encoding="utf-8")

    with pytest.raises(ValueError, match="must be finite"):
        retrieval_eval._load_baseline_metrics(baseline)


def test_offline_cli_runs_without_database_and_writes_redacted_telemetry(
        tmp_path):
    root = Path(__file__).resolve().parents[1]
    suite = root / "evaluation" / "suites" / "property"
    report_path = tmp_path / "property-report.json"

    exit_code = retrieval_eval.main([
        "--retriever", "bm25",
        "--queries", str(suite / "queries.jsonl"),
        "--chunks", str(suite / "chunks.jsonl"),
        "--k", "1", "3",
        "--depth", "5",
        "--json-report", str(report_path),
        "--fail-under", "ndcg@3=0.9",
        "--fail-under", "abstention_accuracy=1",
        "--fail-over", "false_answer_rate=0",
    ])

    assert exit_code == 0
    payload = json.loads(report_path.read_text(encoding="utf-8"))
    assert payload["schema_version"] == 4
    assert payload["configuration"]["retriever"] == "bm25"
    assert payload["configuration"]["model_artifact_lock_sha256"] is None
    assert "queries_path" not in payload["configuration"]
    assert "path" not in payload["measurements"]["index_storage"]
    assert payload["measurements"]["index_storage"]["kind"] == (
        "ephemeral_bm25_source_corpus")
    assert payload["costs"]["embedding"]["status"] == "not_used"
    assert "query" not in payload["query_details"][0]
    assert "text_preview" not in payload["query_details"][0]["results"][0]


def test_report_query_digest_uses_the_scored_snapshot(
        monkeypatch, tmp_path):
    queries_path = tmp_path / "queries.jsonl"
    original = json.dumps({
        "query": "original query", "expected_keywords": ["answer"],
    }) + "\n"
    queries_path.write_text(original, encoding="utf-8")
    original_bytes = queries_path.read_bytes()
    report_path = tmp_path / "report.json"

    def replace_queries_during_evaluation(*_args, **_kwargs):
        queries_path.write_text(json.dumps({
            "query": "replacement query", "expected_keywords": ["other"],
        }) + "\n", encoding="utf-8")
        return {"mrr": 1.0, "num_queries": 1, "query_details": []}

    monkeypatch.setattr(
        retrieval_eval, "evaluate", replace_queries_during_evaluation)

    assert retrieval_eval.main([
        "--queries", str(queries_path),
        "--chunks", str(tmp_path / "chunks.jsonl"),
        "--db", str(tmp_path / "db"),
        "--collection", "book",
        "--json-report", str(report_path),
    ]) == 0

    payload = json.loads(report_path.read_text(encoding="utf-8"))
    assert payload["configuration"]["queries_sha256"] == hashlib.sha256(
        original_bytes).hexdigest()


def test_validated_identity_lookup_survives_chunks_path_replacement(
        monkeypatch, tmp_path):
    original = {
        "text": "Original stable source passage.",
        "metadata": {"chunk_index": 1, "source_file": "private-book"},
    }
    replacement = {
        "text": "Replacement passage.",
        "metadata": {"chunk_index": 1, "source_file": "other-book"},
    }
    chunks = tmp_path / "chunks.jsonl"
    chunks.write_text(json.dumps(original) + "\n", encoding="utf-8")
    lookup = retrieval_eval._chunk_identity_lookup_from_records([original], rag)
    chunks.write_text(json.dumps(replacement) + "\n", encoding="utf-8")
    hit = SimpleNamespace(
        text=original["text"], metadata=original["metadata"], score=0.9)
    monkeypatch.setattr(
        rag, "search_index", lambda *_args, **_kwargs: SimpleNamespace(hits=[hit]))
    monkeypatch.setattr(
        retrieval_eval, "_chunk_identity_lookup",
        lambda *_args, **_kwargs: pytest.fail("must not re-read chunks"))

    results = retrieval_eval.run_search(
        "query", tmp_path / "db", chunks_path=chunks,
        chunk_identity_lookup=lookup)

    assert results[0]["chunk_id"] == rag._chunk_id(original)
