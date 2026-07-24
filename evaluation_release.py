"""Strict, portable release policy for reviewed retrieval evaluations."""

from __future__ import annotations

import argparse
import json
import math
import re
import sys
from pathlib import Path

import evaluation_contract
import evaluation_review
import model_artifacts
import retrieval_core


RELEASE_POLICY_SCHEMA_VERSION = 2
MAX_RELEASE_POLICY_BYTES = 256 * 1024
RELEASE_MODES = (
    "vector",
    "vector_reranked",
    "hybrid",
    "hybrid_reranked",
)
REQUIRED_MINIMUMS = frozenset({
    "success@3",
    "recall@10",
    "ndcg@10",
    "map",
    "abstention_accuracy",
    "filter_compliance",
})
REQUIRED_MAXIMUMS = frozenset({"false_answer_rate"})

_POLICY_ID_RE = re.compile(r"[a-z0-9][a-z0-9._-]{2,63}")
_HEX64_RE = re.compile(r"[0-9a-f]{64}")
_CONFLICTING_EVAL_OPTIONS = frozenset({
    "--retriever",
    "--compare",
    "--hybrid",
    "--vector-only",
    "--rerank",
    "--no-rerank",
    "--embedding-model",
    "--db-backend",
    "--reranker-model",
    "--overfetch",
    "--rrf-k",
    "--dense-weight",
    "--sparse-weight",
    "--context-window",
    "--context-max-characters",
    "--context-segment-characters",
    "--k",
    "--depth",
    "--report-detail",
    "--fail-under",
    "--fail-over",
    "--baseline-report",
    "--max-regression",
})


def _exact_fields(value: object, expected: set[str], *, label: str) -> dict:
    if not isinstance(value, dict) or set(value) != expected:
        raise ValueError(
            f"{label} fields do not match release-policy schema "
            f"v{RELEASE_POLICY_SCHEMA_VERSION}")
    return value


def _digest(value: object, *, label: str) -> str:
    if not isinstance(value, str) or _HEX64_RE.fullmatch(value) is None:
        raise ValueError(f"{label} must be a lowercase SHA-256 digest")
    return value


def _positive_int(value: object, *, label: str, maximum: int | None = None) -> int:
    if (isinstance(value, bool) or not isinstance(value, int) or value < 1
            or maximum is not None and value > maximum):
        suffix = f" no greater than {maximum}" if maximum is not None else ""
        raise ValueError(f"{label} must be a positive integer{suffix}")
    return value


def _finite_number(
        value: object, *, label: str, minimum: float,
        maximum: float) -> float:
    if (isinstance(value, bool) or not isinstance(value, (int, float))
            or not math.isfinite(float(value))
            or not minimum <= float(value) <= maximum):
        raise ValueError(
            f"{label} must be a finite number from {minimum} through {maximum}")
    return float(value)


def _thresholds(value: object, *, label: str, expected: frozenset[str]) -> dict:
    value = _exact_fields(value, set(expected), label=label)
    return {
        metric: _finite_number(
            threshold, label=f"{label} {metric}", minimum=0.0, maximum=1.0)
        for metric, threshold in sorted(value.items())
    }


def validate_release_policy(policy: object) -> dict:
    """Validate and normalize one exact current release policy."""
    policy = _exact_fields(policy, {
        "schema_version", "kind", "status", "policy_id", "queries",
        "corpus", "review", "model_artifacts", "scoring", "configuration",
        "modes",
    }, label="release policy")
    schema_version = policy.get("schema_version")
    if (isinstance(schema_version, bool)
            or not isinstance(schema_version, int)
            or schema_version != RELEASE_POLICY_SCHEMA_VERSION
            or policy.get("kind") != "retrieval_release_policy"
            or policy.get("status") != "approved"):
        raise ValueError(
            "release policy header must be approved schema "
            f"v{RELEASE_POLICY_SCHEMA_VERSION}")
    policy_id = policy.get("policy_id")
    if not isinstance(policy_id, str) or _POLICY_ID_RE.fullmatch(policy_id) is None:
        raise ValueError("release policy ID must be a portable lowercase slug")

    queries = _exact_fields(
        policy.get("queries"), {"sha256", "record_count"},
        label="release policy queries")
    _digest(queries.get("sha256"), label="release policy queries SHA-256")
    _positive_int(
        queries.get("record_count"), label="release policy query record count",
        maximum=100_000)
    corpus = _exact_fields(
        policy.get("corpus"), {"sha256", "record_count", "id_scheme"},
        label="release policy corpus")
    _digest(corpus.get("sha256"), label="release policy corpus SHA-256")
    _positive_int(
        corpus.get("record_count"), label="release policy corpus record count",
        maximum=10_000_000)
    if (not isinstance(corpus.get("id_scheme"), str)
            or not corpus["id_scheme"].strip()
            or len(corpus["id_scheme"]) > 200):
        raise ValueError("release policy corpus ID scheme is invalid")

    review = _exact_fields(
        policy.get("review"), {"review_batch_id", "receipt_sha256"},
        label="release policy review")
    _digest(review.get("review_batch_id"), label="release policy review batch")
    _digest(review.get("receipt_sha256"), label="release policy receipt SHA-256")
    model_policy = _exact_fields(
        policy.get("model_artifacts"), {"lock_sha256"},
        label="release policy model artifacts")
    _digest(
        model_policy.get("lock_sha256"),
        label="release policy model-artifact lock SHA-256")

    scoring = _exact_fields(policy.get("scoring"), {
        "report_schema_version", "grounding_scorer_version",
        "judgment_scorer_version", "table_family_judgment_schema_version",
        "table_retrieval_policy",
    }, label="release policy scoring")
    integer_fields = (
        "report_schema_version", "grounding_scorer_version",
        "judgment_scorer_version", "table_family_judgment_schema_version",
    )
    if any(type(scoring.get(field)) is not int for field in integer_fields):
        raise ValueError("release policy scoring versions must be integers")
    table_policy = _exact_fields(scoring.get("table_retrieval_policy"), {
        "schema_version", "minimum_source_rows", "max_children_per_parent",
        "max_children_per_corpus", "collapse_version",
    }, label="release policy table retrieval policy")
    if any(type(value) is not int for value in table_policy.values()):
        raise ValueError("release policy table retrieval values must be integers")
    expected_scoring = evaluation_contract.scoring_contract()
    if scoring != expected_scoring:
        raise ValueError(
            "release policy scoring contract is incompatible with this "
            "evaluator")

    configuration = _exact_fields(policy.get("configuration"), {
        "embedding_model", "db_backend", "reranker_model", "k_values",
        "retrieval_depth", "overfetch", "rrf_k", "dense_weight",
        "sparse_weight", "context_window", "context_max_characters",
        "context_segment_characters",
    }, label="release policy configuration")
    for field in ("embedding_model", "reranker_model"):
        value = configuration.get(field)
        if not isinstance(value, str) or not value.strip() or len(value) > 300:
            raise ValueError(f"release policy {field} is invalid")
    if configuration.get("db_backend") not in {"chroma", "qdrant"}:
        raise ValueError("release policy database backend is invalid")
    k_values = configuration.get("k_values")
    if (not isinstance(k_values, list)
            or any(isinstance(value, bool) or not isinstance(value, int)
                   for value in k_values)
            or k_values != [1, 3, 5, 10]):
        raise ValueError("release policy k_values must be exactly [1, 3, 5, 10]")
    depth = _positive_int(
        configuration.get("retrieval_depth"),
        label="release policy retrieval depth", maximum=1000)
    if depth < 10:
        raise ValueError("release policy retrieval depth must be at least 10")
    overfetch = _positive_int(
        configuration.get("overfetch"),
        label="release policy overfetch", maximum=20)
    rrf_k = _positive_int(
        configuration.get("rrf_k"), label="release policy rrf_k",
        maximum=10_000)
    dense_weight = _finite_number(
        configuration.get("dense_weight"), label="release policy dense weight",
        minimum=0.0, maximum=100.0)
    sparse_weight = _finite_number(
        configuration.get("sparse_weight"), label="release policy sparse weight",
        minimum=0.0, maximum=100.0)
    if dense_weight + sparse_weight == 0:
        raise ValueError("release policy fusion weights cannot both be zero")
    context_window = configuration.get("context_window")
    if (isinstance(context_window, bool) or not isinstance(context_window, int)
            or not 0 <= context_window <= retrieval_core.MAX_CONTEXT_WINDOW):
        raise ValueError("release policy context window is invalid")
    context_max = _positive_int(
        configuration.get("context_max_characters"),
        label="release policy context character budget",
        maximum=retrieval_core.MAX_CONTEXT_CHARACTERS)
    context_segment = _positive_int(
        configuration.get("context_segment_characters"),
        label="release policy context segment budget",
        maximum=retrieval_core.MAX_CONTEXT_SEGMENT_CHARACTERS)

    modes = _exact_fields(
        policy.get("modes"), set(RELEASE_MODES),
        label="release policy modes")
    normalized_modes = {}
    for mode in RELEASE_MODES:
        expected_hybrid = mode.startswith("hybrid")
        expected_reranker = mode.endswith("reranked")
        value = _exact_fields(
            modes[mode], {"hybrid", "reranker", "minimums", "maximums"},
            label=f"release policy mode {mode}")
        if (value.get("hybrid") is not expected_hybrid
                or value.get("reranker") is not expected_reranker):
            raise ValueError(f"release policy mode {mode} flags are inconsistent")
        normalized_modes[mode] = {
            "hybrid": expected_hybrid,
            "reranker": expected_reranker,
            "minimums": _thresholds(
                value.get("minimums"), label=f"mode {mode} minimums",
                expected=REQUIRED_MINIMUMS),
            "maximums": _thresholds(
                value.get("maximums"), label=f"mode {mode} maximums",
                expected=REQUIRED_MAXIMUMS),
        }

    normalized = json.loads(json.dumps(policy))
    normalized["configuration"].update({
        "retrieval_depth": depth,
        "overfetch": overfetch,
        "rrf_k": rrf_k,
        "dense_weight": dense_weight,
        "sparse_weight": sparse_weight,
        "context_window": context_window,
        "context_max_characters": context_max,
        "context_segment_characters": context_segment,
    })
    normalized["modes"] = normalized_modes
    return normalized


def load_release_policy(path: Path) -> tuple[dict, dict]:
    raw, policy_sha256 = evaluation_review._read_snapshot(
        path, label="release policy", max_bytes=MAX_RELEASE_POLICY_BYTES)
    payload = evaluation_review._strict_json_bytes(
        raw, label="release policy", max_bytes=MAX_RELEASE_POLICY_BYTES)
    policy = validate_release_policy(payload)
    return policy, {
        "schema_version": RELEASE_POLICY_SCHEMA_VERSION,
        "policy_id": policy["policy_id"],
        "policy_sha256": policy_sha256,
    }


def _option_name(argument: str) -> str:
    return argument.split("=", 1)[0]


def conflicting_eval_options(argv: list[str]) -> list[str]:
    return sorted({
        _option_name(argument)
        for argument in argv
        if argument.startswith("--")
        and _option_name(argument) in _CONFLICTING_EVAL_OPTIONS
    })


def apply_release_policy(args, *, argv: list[str]) -> dict:
    """Load a policy and make it the sole owner of retrieval/gate settings."""
    policy_path = getattr(args, "release_policy", None)
    mode_name = getattr(args, "policy_mode", None)
    if (policy_path is None) != (mode_name is None):
        raise ValueError("--release-policy and --policy-mode are required together")
    if policy_path is None:
        return {}
    conflicts = conflicting_eval_options(argv)
    if conflicts:
        raise ValueError(
            "release policy owns retrieval and threshold options; remove: "
            + ", ".join(conflicts))
    if getattr(args, "json_report", None) is None:
        raise ValueError("release-policy evaluation requires --json-report")
    if getattr(args, "review_receipt", None) is None:
        raise ValueError("release-policy evaluation requires --review-receipt")

    policy, binding = load_release_policy(policy_path)
    if mode_name not in policy["modes"]:
        raise ValueError(f"release policy does not define mode {mode_name!r}")
    configuration = policy["configuration"]
    mode = policy["modes"][mode_name]
    args.retriever = "index"
    args.compare = False
    args.embedding_model = configuration["embedding_model"]
    args.db_backend = configuration["db_backend"]
    args.reranker_model = configuration["reranker_model"]
    args.k = list(configuration["k_values"])
    args.depth = configuration["retrieval_depth"]
    args.overfetch = configuration["overfetch"]
    args.rrf_k = configuration["rrf_k"]
    args.dense_weight = configuration["dense_weight"]
    args.sparse_weight = configuration["sparse_weight"]
    args.context_window = configuration["context_window"]
    args.context_max_characters = configuration["context_max_characters"]
    args.context_segment_characters = configuration[
        "context_segment_characters"]
    args.hybrid = mode["hybrid"]
    args.reranker = mode["reranker"]
    args.report_detail = "summary"
    args.fail_under = [
        f"{metric}={value!r}"
        for metric, value in mode["minimums"].items()
    ]
    args.fail_over = [
        f"{metric}={value!r}"
        for metric, value in mode["maximums"].items()
    ]
    args.baseline_report = None
    args.max_regression = []
    args.release_policy_payload = policy
    args.release_policy_binding = {**binding, "mode": mode_name}
    return policy


def validate_release_policy_runtime(
        policy: dict, *, queries: list[dict], queries_sha256: str,
        review_receipt_binding: dict) -> dict:
    """Bind one static policy to current approved queries and model policy."""
    if policy["queries"] != {
            "sha256": queries_sha256,
            "record_count": len(queries),
            }:
        raise ValueError("release policy does not bind the approved query set")
    corpus = evaluation_review._corpus_contract(queries)
    if policy["corpus"] != corpus:
        raise ValueError("release policy does not bind the query corpus")
    if policy["review"] != {
            "review_batch_id": review_receipt_binding["review_batch_id"],
            "receipt_sha256": review_receipt_binding["receipt_sha256"],
            }:
        raise ValueError("release policy does not bind the review receipt")
    current_lock = model_artifacts.model_artifact_lock_sha256()
    if policy["model_artifacts"] != {"lock_sha256": current_lock}:
        raise ValueError("release policy model-artifact lock is stale")
    return {
        "policy_id": policy["policy_id"],
        "queries_sha256": queries_sha256,
        "corpus_sha256": corpus["sha256"],
        "review_batch_id": review_receipt_binding["review_batch_id"],
        "model_artifact_lock_sha256": current_lock,
    }


def _build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        description="Validate a reviewed retrieval release policy")
    parser.add_argument("--policy", type=Path, required=True)
    return parser


def main(argv: list[str] | None = None) -> int:
    args = _build_parser().parse_args(argv)
    try:
        policy, binding = load_release_policy(args.policy)
    except (OSError, UnicodeError, ValueError) as exc:
        print(f"Release policy error: {exc}", file=sys.stderr)
        return 1
    summary = {
        **binding,
        "status": policy["status"],
        "modes": list(policy["modes"]),
    }
    print(json.dumps(summary, sort_keys=True, separators=(",", ":")))
    return 0


if __name__ == "__main__":
    sys.exit(main())
