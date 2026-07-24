import copy
import json
from types import SimpleNamespace

import pytest

import eval as retrieval_eval
import evaluation_release
import model_artifacts


def _policy(*, queries_sha256="a" * 64, corpus_sha256="b" * 64):
    minimums = {
        "success@3": 0.85,
        "recall@10": 0.80,
        "ndcg@10": 0.75,
        "map": 0.70,
        "abstention_accuracy": 1.0,
        "filter_compliance": 1.0,
    }
    modes = {}
    for mode in evaluation_release.RELEASE_MODES:
        modes[mode] = {
            "hybrid": mode.startswith("hybrid"),
            "reranker": mode.endswith("reranked"),
            "minimums": dict(minimums),
            "maximums": {"false_answer_rate": 0.0},
        }
    return {
        "schema_version": evaluation_release.RELEASE_POLICY_SCHEMA_VERSION,
        "kind": "retrieval_release_policy",
        "status": "approved",
        "policy_id": "ethics-v1",
        "queries": {"sha256": queries_sha256, "record_count": 2},
        "corpus": {
            "sha256": corpus_sha256,
            "record_count": 10,
            "id_scheme": "retrieval_core._chunk_id",
        },
        "review": {
            "review_batch_id": "c" * 64,
            "receipt_sha256": "d" * 64,
        },
        "model_artifacts": {
            "lock_sha256": model_artifacts.model_artifact_lock_sha256(),
        },
        "scoring": {
            "report_schema_version": retrieval_eval.REPORT_SCHEMA_VERSION,
            "grounding_scorer_version": (
                retrieval_eval.GROUNDING_SCORER_VERSION),
            "judgment_scorer_version": (
                retrieval_eval.JUDGMENT_SCORER_VERSION),
            "table_family_judgment_schema_version": (
                retrieval_eval.TABLE_FAMILY_JUDGMENT_SCHEMA_VERSION),
            "table_retrieval_policy": (
                retrieval_eval._table_retrieval_policy_contract()),
        },
        "configuration": {
            "embedding_model": "nomic-ai/nomic-embed-text-v2-moe",
            "db_backend": "chroma",
            "reranker_model": "BAAI/bge-reranker-v2-m3",
            "k_values": [1, 3, 5, 10],
            "retrieval_depth": 20,
            "overfetch": 4,
            "rrf_k": 10,
            "dense_weight": 0.5,
            "sparse_weight": 1.0,
            "context_window": 0,
            "context_max_characters": 8000,
            "context_segment_characters": 1600,
        },
        "modes": modes,
    }


def _write_policy(tmp_path, payload=None):
    path = tmp_path / "policy.json"
    path.write_text(json.dumps(payload or _policy()), encoding="utf-8")
    return path


def test_release_policy_requires_exact_four_mode_threshold_contract(tmp_path):
    policy_path = _write_policy(tmp_path)

    policy, binding = evaluation_release.load_release_policy(policy_path)

    assert binding["policy_id"] == "ethics-v1"
    assert binding["schema_version"] == (
        evaluation_release.RELEASE_POLICY_SCHEMA_VERSION)
    assert set(policy["modes"]) == set(evaluation_release.RELEASE_MODES)
    assert all(
        set(mode["minimums"]) == evaluation_release.REQUIRED_MINIMUMS
        for mode in policy["modes"].values())
    assert all(
        set(mode["maximums"]) == evaluation_release.REQUIRED_MAXIMUMS
        for mode in policy["modes"].values())


@pytest.mark.parametrize(("mutate", "message"), [
    (lambda value: value.update(status="draft_requires_corpus_owner"),
     "header"),
    (lambda value: value.update(schema_version=True),
     "header"),
    (lambda value: value["modes"].pop("hybrid_reranked"),
     "fields"),
    (lambda value: value["modes"]["vector"].update(hybrid=True),
     "flags"),
    (lambda value: value["modes"]["vector"]["minimums"].pop("map"),
     "fields"),
    (lambda value: value["configuration"].update(k_values=[5, 10]),
     "k_values"),
    (lambda value: value["configuration"].update(k_values=[True, 3, 5, 10]),
     "k_values"),
    (lambda value: value["configuration"].update(k_values=[1.0, 3, 5, 10]),
     "k_values"),
    (lambda value: value["configuration"].update(dense_weight=float("inf")),
     "finite number"),
    (lambda value: value["scoring"].update(judgment_scorer_version=999),
     "incompatible"),
    (lambda value: value["scoring"].pop("table_retrieval_policy"),
     "fields"),
])
def test_release_policy_rejects_incomplete_or_ambiguous_contracts(
        mutate, message):
    policy = _policy()
    mutate(policy)

    with pytest.raises(ValueError, match=message):
        evaluation_release.validate_release_policy(policy)


def test_release_policy_parser_rejects_duplicate_keys(tmp_path):
    path = tmp_path / "duplicate.json"
    path.write_text(
        '{"schema_version":1,"schema_version":1}', encoding="utf-8")

    with pytest.raises(ValueError, match="duplicate field"):
        evaluation_release.load_release_policy(path)


def test_apply_release_policy_owns_mode_configuration_and_gates(tmp_path):
    policy_path = _write_policy(tmp_path)
    args = SimpleNamespace(
        release_policy=policy_path,
        policy_mode="hybrid_reranked",
        json_report=tmp_path / "report.json",
        review_receipt=tmp_path / "receipt.json",
    )

    policy = evaluation_release.apply_release_policy(args, argv=[])

    assert policy["policy_id"] == "ethics-v1"
    assert args.retriever == "index"
    assert args.hybrid is True
    assert args.reranker is True
    assert args.k == [1, 3, 5, 10]
    assert args.depth == 20
    assert args.report_detail == "summary"
    assert set(args.fail_under) == {
        "success@3=0.85", "recall@10=0.8", "ndcg@10=0.75", "map=0.7",
        "abstention_accuracy=1.0", "filter_compliance=1.0",
    }
    assert args.fail_over == ["false_answer_rate=0.0"]
    assert args.release_policy_binding["mode"] == "hybrid_reranked"


def test_apply_release_policy_preserves_threshold_precision(tmp_path):
    payload = _policy()
    payload["modes"]["vector"]["minimums"]["map"] = 0.9999994
    args = SimpleNamespace(
        release_policy=_write_policy(tmp_path, payload),
        policy_mode="vector",
        json_report=tmp_path / "report.json",
        review_receipt=tmp_path / "receipt.json",
    )

    evaluation_release.apply_release_policy(args, argv=[])

    assert "map=0.9999994" in args.fail_under


def test_apply_release_policy_rejects_ambiguous_cli_overrides(tmp_path):
    args = SimpleNamespace(
        release_policy=_write_policy(tmp_path),
        policy_mode="vector",
        json_report=tmp_path / "report.json",
        review_receipt=tmp_path / "receipt.json",
    )

    with pytest.raises(ValueError, match="remove: --hybrid, --k"):
        evaluation_release.apply_release_policy(
            args, argv=["--hybrid", "--k", "1", "3"])


def test_runtime_binding_requires_queries_corpus_receipt_and_model_lock():
    policy = evaluation_release.validate_release_policy(_policy())
    queries = [{
        "query_id": query_id,
        "query": "query",
        "expected_abstain": True,
        "review_status": "approved",
        "corpus": {
            "sha256": "b" * 64,
            "record_count": 10,
            "id_scheme": "retrieval_core._chunk_id",
        },
    } for query_id in ("q1", "q2")]
    receipt = {
        "review_batch_id": "c" * 64,
        "receipt_sha256": "d" * 64,
    }

    binding = evaluation_release.validate_release_policy_runtime(
        policy, queries=queries, queries_sha256="a" * 64,
        review_receipt_binding=receipt)

    assert binding["policy_id"] == "ethics-v1"
    stale = copy.deepcopy(policy)
    stale["corpus"]["record_count"] = 9
    with pytest.raises(ValueError, match="query corpus"):
        evaluation_release.validate_release_policy_runtime(
            stale, queries=queries, queries_sha256="a" * 64,
            review_receipt_binding=receipt)


def test_eval_cli_rejects_policy_plus_manual_retrieval_flags(tmp_path):
    policy_path = _write_policy(tmp_path)

    with pytest.raises(SystemExit) as error:
        retrieval_eval.main([
            "--release-policy", str(policy_path),
            "--policy-mode", "vector",
            "--review-receipt", str(tmp_path / "receipt.json"),
            "--queries", str(tmp_path / "queries.jsonl"),
            "--chunks", str(tmp_path / "chunks.jsonl"),
            "--db", str(tmp_path / "db"),
            "--collection", "book",
            "--json-report", str(tmp_path / "report.json"),
            "--hybrid",
        ])

    assert error.value.code == 2
