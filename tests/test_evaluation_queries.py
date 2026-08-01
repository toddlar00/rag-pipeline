from __future__ import annotations

import copy
import hashlib
import json
import math
import subprocess
import sys
from pathlib import Path

import pytest

import eval as retrieval_eval
import evaluation_queries
import evaluation_review


PROJECT_ROOT = Path(__file__).resolve().parents[1]
EXTRACTED_FUNCTIONS = (
    "_finite_float",
    "_validate_declared_corpus_snapshot",
    "_validate_corpus_pin_coverage",
    "_judgment_satisfying_chunk_ids",
    "_has_table_family_judgments",
    "_validate_attested_table_family_judgments",
    "_validate_judged_ids",
    "_validate_grounding_evidence_ids",
    "_validate_query",
    "_validate_grounding_case",
    "_answer_units",
    "_slice_slug",
    "_model_visible_string_contains",
)


def test_evaluation_query_domain_isolated_from_applications_and_runtime():
    result = subprocess.run(
        [
            sys.executable,
            "-B",
            "-c",
            (
                "import sys; import evaluation_queries; "
                "forbidden={'eval','evaluation_review','evaluation_release',"
                "'rag','job_manager','service_runtime','model_artifacts',"
                "'offline_retrieval','requests','numpy','torch'}; "
                "loaded=sorted(forbidden.intersection(sys.modules)); "
                "assert not loaded, loaded"
            ),
        ],
        cwd=PROJECT_ROOT,
        check=False,
        capture_output=True,
        text=True,
        timeout=30,
    )
    assert result.returncode == 0, result.stderr


def test_eval_preserves_object_identical_query_domain_aliases():
    for name in EXTRACTED_FUNCTIONS:
        assert getattr(retrieval_eval, name) is getattr(evaluation_queries, name)
    assert retrieval_eval.GROUNDING_CASE_SCHEMA_VERSION == (
        evaluation_queries.GROUNDING_CASE_SCHEMA_VERSION)
    assert retrieval_eval.TABLE_FAMILY_JUDGMENT_SCHEMA_VERSION == (
        evaluation_queries.TABLE_FAMILY_JUDGMENT_SCHEMA_VERSION)
    assert retrieval_eval._JUDGMENT_ID_FIELDS is (
        evaluation_queries._JUDGMENT_ID_FIELDS)
    assert retrieval_eval._ALLOWED_FILTER_FIELDS is (
        evaluation_queries._ALLOWED_FILTER_FIELDS)
    assert retrieval_eval._REVIEW_STATUSES is evaluation_queries._REVIEW_STATUSES


def test_review_parsing_does_not_import_the_evaluator():
    source = (
        "import sys; from pathlib import Path; import evaluation_review as r; "
        "assert 'eval' not in sys.modules; "
        "queries=r._parse_queries("
        "b'{\"query\":\"q\",\"expected_keywords\":[\"a\"]}\\n', "
        "Path('queries.jsonl')); "
        "assert queries[0]['query']=='q'; assert 'eval' not in sys.modules"
    )
    result = subprocess.run(
        [sys.executable, "-B", "-c", source],
        cwd=PROJECT_ROOT,
        check=False,
        capture_output=True,
        text=True,
        timeout=30,
    )
    assert result.returncode == 0, result.stderr


def test_review_input_validation_does_not_import_the_evaluator():
    suite = PROJECT_ROOT / "evaluation" / "suites" / "property"
    source = (
        "import sys; from pathlib import Path; import evaluation_review as r; "
        "assert 'eval' not in sys.modules; "
        f"r._load_inputs(Path({str(suite / 'queries.jsonl')!r}), "
        f"Path({str(suite / 'chunks.jsonl')!r}), required_status=None); "
        "assert 'eval' not in sys.modules"
    )
    result = subprocess.run(
        [sys.executable, "-B", "-c", source],
        cwd=PROJECT_ROOT,
        check=False,
        capture_output=True,
        text=True,
        timeout=30,
    )
    assert result.returncode == 0, result.stderr


def test_legacy_loader_preserves_bytes_types_extensions_and_digest(tmp_path):
    path = tmp_path / "queries.jsonl"
    raw = (
        b"\xef\xbb\xbf\n"
        b'{"query":"first","query":"  final  ",'
        b'"expected_keywords":["  answer  "],"extension":NaN}\n\n'
        b'{"query":"graded","judgments":'
        b'[{"chunk_id":"  chunk-1  ","relevance":3.0}],'
        b'"extension":{"integer":3}}\n'
    )
    path.write_bytes(raw)

    queries = retrieval_eval.load_queries(path)

    assert queries[0]["query"] == "  final  "
    assert queries[0]["expected_keywords"] == ["  answer  "]
    assert math.isnan(queries[0]["extension"])
    assert queries[1]["judgments"][0] == {
        "chunk_id": "  chunk-1  ", "relevance": 3.0}
    assert type(queries[1]["judgments"][0]["relevance"]) is float
    assert type(queries[1]["extension"]["integer"]) is int
    assert retrieval_eval._query_snapshot_sha256[
        str(path.resolve())] == hashlib.sha256(raw).hexdigest()


def test_legacy_loader_preserves_exact_failure_surfaces(tmp_path):
    malformed = tmp_path / "malformed.jsonl"
    malformed.write_bytes(b"\n\n{\"query\":\n")
    with pytest.raises(ValueError) as malformed_error:
        retrieval_eval.load_queries(malformed)
    assert str(malformed_error.value) == (
        f"Invalid JSON at {malformed}:3: Expecting value")

    empty = tmp_path / "empty.jsonl"
    empty.write_text("\n\n", encoding="utf-8")
    with pytest.raises(ValueError) as empty_error:
        retrieval_eval.load_queries(empty)
    assert str(empty_error.value) == f"No evaluation queries found in {empty}"

    non_object = tmp_path / "non-object.jsonl"
    non_object.write_text("[]\n", encoding="utf-8")
    with pytest.raises(ValueError) as object_error:
        retrieval_eval.load_queries(non_object)
    assert str(object_error.value) == f"{non_object}:1 must be a JSON object"

    invalid_utf8 = tmp_path / "invalid-utf8.jsonl"
    invalid_utf8.write_bytes(b"\xff")
    with pytest.raises(UnicodeDecodeError):
        retrieval_eval.load_queries(invalid_utf8)


def test_legacy_loader_publishes_digest_only_after_complete_validation(tmp_path):
    path = tmp_path / "queries.jsonl"
    path.write_text(
        '{"query":"valid","expected_keywords":["answer"]}\n{',
        encoding="utf-8",
    )
    cache_key = str(path.resolve())
    retrieval_eval._query_snapshot_sha256[cache_key] = "prior"

    with pytest.raises(ValueError, match="Invalid JSON"):
        retrieval_eval.load_queries(path)

    assert retrieval_eval._query_snapshot_sha256[cache_key] == "prior"


def test_legacy_loader_retains_eval_local_validator_injection(monkeypatch, tmp_path):
    path = tmp_path / "queries.jsonl"
    path.write_text('{"extension":true}\n', encoding="utf-8")
    observed = []

    def validate(query, *, label):
        observed.append((query, label))

    monkeypatch.setattr(retrieval_eval, "_validate_query", validate)

    assert retrieval_eval.load_queries(path) == [{"extension": True}]
    assert observed == [({"extension": True}, f"{path}:1")]


def _maximal_query() -> dict:
    return {
        "query": "  Supported question?  ",
        "judgments": [{
            "chunk_id": "  parent  ",
            "relevance": 3,
            "table_family": {
                "schema_version": 1,
                "accepted_child_chunk_ids": ["child"],
            },
        }],
        "expected_type": "  table  ",
        "filters": {"content_type": "  table  ", "chapter_num": 0},
        "tags": ["table_family", "prompt_injection"],
        "subject": "  Subject  ",
        "book": "  Book  ",
        "difficulty": "  hard  ",
        "review_status": "approved",
        "approval": {"schema_version": 1, "review_batch_id": "a" * 64},
        "corpus": {
            "sha256": "A" * 64,
            "record_count": 2,
            "id_scheme": "  stable-v1  ",
            "extension": True,
        },
        "grounding_case": {
            "schema_version": 2,
            "case_type": "  fixture  ",
            "answer": "Supported claim [S1]",
            "expected_abstained": False,
            "expected_citations": ["S1"],
            "warning_contains": [],
            "excerpt_contains": [],
            "claim_judgments": [{
                "claim_id": "claim-1",
                "answer_unit": "Supported claim [S1]",
                "entailed_by": [{
                    "source_id": "source-1",
                    "excerpt_contains": "anchor",
                }],
            }],
            "prompt_injection": {
                "source_id": "source-1", "marker": "ignore this"},
        },
        "extension": {"integer": 3, "float": 3.0},
    }


def test_query_validation_is_observational_and_preserves_exact_types():
    query = _maximal_query()
    before = copy.deepcopy(query)
    canonical_before = json.dumps(
        query, sort_keys=True, separators=(",", ":"))

    evaluation_queries._validate_query(query, label="fixture")

    assert query == before
    assert json.dumps(query, sort_keys=True, separators=(",", ":")) == (
        canonical_before)
    assert type(query["judgments"][0]["relevance"]) is int
    assert type(query["extension"]["float"]) is float


def test_declared_corpus_snapshot_preserves_partial_contract_and_error_order():
    queries = [
        {"corpus": {"sha256": "A" * 64}},
        {"corpus": {"record_count": 2}},
        {},
    ]
    before = copy.deepcopy(queries)

    evaluation_queries._validate_declared_corpus_snapshot(
        queries, actual_hash="a" * 64, actual_count=2)
    assert queries == before

    with pytest.raises(ValueError) as mismatch:
        evaluation_queries._validate_declared_corpus_snapshot(
            queries, actual_hash="b" * 64, actual_count=3)
    assert str(mismatch.value) == (
        "Judged queries target a different chunks snapshot: "
        f"expected SHA-256 {'a' * 64}, got {'b' * 64}")

    with pytest.raises(ValueError) as count_mismatch:
        evaluation_queries._validate_declared_corpus_snapshot(
            [{"corpus": {"record_count": 2}}],
            actual_hash="a" * 64, actual_count=3)
    assert str(count_mismatch.value) == (
        "Judged queries target a different record count: expected 2, got 3")

    inconsistent = [
        {"corpus": {"sha256": "a" * 64}},
        {"corpus": {"sha256": "b" * 64}},
    ]
    with pytest.raises(ValueError, match="inconsistent corpus snapshots"):
        evaluation_queries._validate_declared_corpus_snapshot(
            inconsistent, actual_hash="a" * 64, actual_count=1)


def test_corpus_pin_coverage_preserves_optional_required_and_index_reporting():
    evaluation_queries._validate_corpus_pin_coverage([{}])
    evaluation_queries._validate_corpus_pin_coverage([], required=True)
    evaluation_queries._validate_corpus_pin_coverage([{
        "corpus": {
            "sha256": "A" * 64,
            "record_count": 1,
            "extension": True,
        },
    }], required=True)

    queries = [{"corpus": {"sha256": "a" * 64, "record_count": 1}}]
    queries.extend({} for _ in range(4))
    with pytest.raises(ValueError) as incomplete:
        evaluation_queries._validate_corpus_pin_coverage(
            queries, required=True)
    assert str(incomplete.value).endswith("query numbers: 2, 3, 4")


def test_legacy_and_review_parsers_retain_distinct_duplicate_key_policies(
        tmp_path):
    path = tmp_path / "queries.jsonl"
    raw = (
        b'{"query":"first","query":"last",'
        b'"expected_keywords":["answer"]}\n'
    )
    path.write_bytes(raw)

    assert retrieval_eval.load_queries(path)[0]["query"] == "last"
    with pytest.raises(ValueError, match="duplicate field 'query'"):
        evaluation_review._parse_queries(raw, path)


def test_duplicate_query_ids_remain_a_review_staging_check(tmp_path):
    path = tmp_path / "queries.jsonl"
    raw = b"\n".join([
        b'{"query_id":"same","query":"one",'
        b'"expected_keywords":["answer"]}',
        b'{"query_id":"same","query":"two",'
        b'"expected_keywords":["answer"]}',
    ]) + b"\n"
    path.write_bytes(raw)

    assert len(retrieval_eval.load_queries(path)) == 2
    parsed = evaluation_review._parse_queries(raw, path)
    assert len(parsed) == 2
    with pytest.raises(ValueError, match="evaluation query IDs must be unique"):
        evaluation_review._query_ids(parsed)
