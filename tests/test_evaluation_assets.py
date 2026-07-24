import hashlib
import json
from pathlib import Path

import pytest

import eval as retrieval_eval
from retrieval_core import _chunk_id


REPOSITORY_ROOT = Path(__file__).resolve().parents[1]
SUITES_ROOT = REPOSITORY_ROOT / "evaluation" / "suites"
SUITES = ("property", "constitutional_law")
ETHICS_DRAFT_QUERIES = REPOSITORY_ROOT / "eval_queries_ethics_draft.jsonl"


def _load_jsonl(path: Path) -> list[dict]:
    return [
        json.loads(line)
        for line in path.read_text(encoding="utf-8").splitlines()
        if line.strip()
    ]


def _sha256(path: Path) -> str:
    return hashlib.sha256(path.read_bytes()).hexdigest()


@pytest.mark.parametrize("suite_name", SUITES)
def test_suite_manifest_pins_license_safe_assets(suite_name):
    suite_dir = SUITES_ROOT / suite_name
    manifest = json.loads(
        (suite_dir / "manifest.json").read_text(encoding="utf-8")
    )

    assert manifest["schema_version"] == 1
    assert manifest["license"] == "CC0-1.0"
    assert "written specifically for this evaluation suite" in manifest["source_note"]
    assert "no source text is copied" in manifest["source_note"]

    for asset_name in ("chunks", "queries"):
        declared = manifest[asset_name]
        asset_path = suite_dir / declared["path"]
        rows = _load_jsonl(asset_path)
        assert _sha256(asset_path) == declared["sha256"]
        assert len(rows) == declared["record_count"]

    assert manifest["chunks"]["record_count"] >= 8
    assert manifest["queries"]["record_count"] >= 6
    assert manifest["chunks"]["id_scheme"] == "retrieval_core._chunk_id"


@pytest.mark.parametrize("suite_name", SUITES)
def test_judgments_match_stable_chunk_ids_and_declared_corpus(suite_name):
    suite_dir = SUITES_ROOT / suite_name
    manifest = json.loads(
        (suite_dir / "manifest.json").read_text(encoding="utf-8")
    )
    chunks = _load_jsonl(suite_dir / manifest["chunks"]["path"])
    queries = _load_jsonl(suite_dir / manifest["queries"]["path"])
    chunks_by_id = {_chunk_id(chunk): chunk for chunk in chunks}

    assert len(chunks_by_id) == len(chunks)
    assert all(chunk["metadata"]["subject"] == manifest["subject"] for chunk in chunks)
    assert all(chunk["metadata"]["book"] for chunk in chunks)
    assert any(
        "distractor" in chunk["metadata"]["content_type"]
        or "distractor" in chunk["metadata"]["title"].lower()
        for chunk in chunks
    )

    query_ids = [query["query_id"] for query in queries]
    assert len(query_ids) == len(set(query_ids))
    for query in queries:
        assert query["subject"] == manifest["subject"]
        assert query["book"] == manifest["book"]
        assert query["tags"] and all(isinstance(tag, str) for tag in query["tags"])
        assert query["corpus"]["sha256"] == manifest["chunks"]["sha256"]
        assert query["corpus"]["record_count"] == manifest["chunks"]["record_count"]
        expected_path = (
            f"evaluation/suites/{suite_name}/"
            f"{manifest['chunks']['path']}"
        )
        assert query["corpus"]["chunks_path"] == expected_path
        assert query["corpus"]["id_scheme"] == "retrieval_core._chunk_id"

        if query.get("expected_abstain"):
            assert "judgments" not in query
            continue

        judgments = query["judgments"]
        assert judgments
        assert all(judgment["chunk_id"] in chunks_by_id for judgment in judgments)
        assert all(judgment["relevance"] in (1, 2, 3) for judgment in judgments)

        filters = query.get("filters")
        if filters:
            assert set(filters) <= {"content_type", "chapter_num"}
            assert isinstance(filters.get("content_type"), str)
            assert isinstance(filters.get("chapter_num"), int)
            assert any(
                all(chunks_by_id[judgment["chunk_id"]]["metadata"].get(key) == value
                    for key, value in filters.items())
                for judgment in judgments
            )


def test_combined_suites_cover_adversarial_categories_and_long_context():
    all_chunks = []
    all_queries = []
    for suite_name in SUITES:
        suite_dir = SUITES_ROOT / suite_name
        all_chunks.extend(_load_jsonl(suite_dir / "chunks.jsonl"))
        all_queries.extend(_load_jsonl(suite_dir / "queries.jsonl"))

    all_ids = [_chunk_id(chunk) for chunk in all_chunks]
    assert len(all_ids) == len(set(all_ids))

    covered_tags = {
        tag
        for query in all_queries
        for tag in query["tags"]
    }
    assert {"citation", "filter", "long_context", "abstention"} <= covered_tags

    chunks_by_id = {_chunk_id(chunk): chunk for chunk in all_chunks}
    long_context_queries = [
        query for query in all_queries if "long_context" in query["tags"]
    ]
    assert len(long_context_queries) >= len(SUITES)
    for query in long_context_queries:
        target_id = query["judgments"][0]["chunk_id"]
        target_text = chunks_by_id[target_id]["text"]
        anchor_position = target_text.index(query["answer_anchor"])
        assert len(target_text) > 10_000
        assert anchor_position > len(target_text) * 0.9

    abstention_queries = [
        query for query in all_queries if "abstention" in query["tags"]
    ]
    assert len(abstention_queries) >= len(SUITES)
    assert all(query.get("expected_abstain") is True for query in abstention_queries)
    assert all("judgments" not in query for query in abstention_queries)

    grounding_cases = [
        query["grounding_case"]
        for query in all_queries if query.get("grounding_case")
    ]
    assert {case["case_type"] for case in grounding_cases} >= {
        "unknown_citation", "uncited_answer", "unsupported_quote",
        "supported_claim", "prompt_injection_supported",
        "prompt_injection_abstention",
    }
    assert {case["expected_abstained"] for case in grounding_cases} == {
        False, True}
    assert all("grounding" in query["tags"] for query in all_queries
               if query.get("grounding_case"))

    v2_cases = [
        (query, query["grounding_case"])
        for query in all_queries
        if (query.get("grounding_case") or {}).get("schema_version") == 2
    ]
    assert v2_cases
    assert all(query["review_status"] == "approved"
               for query, _case in v2_cases)
    assert any(
        claim["entailed_by"]
        for _query, case in v2_cases
        for claim in case["claim_judgments"])
    assert any(
        not claim["entailed_by"]
        for _query, case in v2_cases
        for claim in case["claim_judgments"])

    prompt_suites = {
        query["subject"]
        for query in all_queries
        if (query.get("grounding_case") or {}).get("prompt_injection")
    }
    assert prompt_suites == {"Property", "Constitutional Law"}
    security_chunks = [
        chunk for chunk in all_chunks
        if chunk["metadata"]["content_type"] == "security_fixture"
    ]
    assert len(security_chunks) == len(SUITES)
    for chunk in security_chunks:
        assert "\nBEGIN_UNTRUSTED_SOURCE\n" in chunk["text"]
        assert "\nEND_UNTRUSTED_SOURCE" in chunk["text"]
        assert "SYSTEM:" in chunk["text"]
        assert "Question:" in chunk["text"]
        assert "[S99]" in chunk["text"]
        assert "INSUFFICIENT_EVIDENCE" in chunk["text"]
        assert "SYSTEM:" in chunk["metadata"]["title"]


def test_private_ethics_draft_is_pinned_and_cannot_pose_as_reviewed():
    queries = _load_jsonl(ETHICS_DRAFT_QUERIES)

    assert len(queries) == 14
    assert sum(
        len(query.get("judgments", [])) for query in queries
    ) == 24
    assert len({query["query_id"] for query in queries}) == len(queries)
    assert {
        query["review_status"] for query in queries
    } == {"draft_requires_corpus_owner"}

    declarations = {
        (query["corpus"]["sha256"], query["corpus"]["record_count"])
        for query in queries
    }
    assert declarations == {(
        "a56f145f09a6c97efac1ad622e478a735b9f23fb1d48d4807ae89edd4fd7a790",
        1715,
    )}
    assert all(
        query["corpus"]["chunks_path"]
        == "output/Ethics_3/Ethics_3_chunks.jsonl"
        for query in queries
    )

    tags = {tag for query in queries for tag in query["tags"]}
    assert {
        "abstention", "author_explanation", "case", "cross_page",
        "filter", "outline_distractor", "positive_outline", "rule_text",
        "table",
    } <= tags
    assert any(query.get("expected_abstain") is True for query in queries)
    assert any(
        any(judgment["relevance"] == 0
            for judgment in query.get("judgments", []))
        for query in queries
    )
    assert all(
        any(judgment["relevance"] > 0
            for judgment in query.get("judgments", []))
        for query in queries if not query.get("expected_abstain")
    )


@pytest.mark.parametrize(("suite_name", "baseline_name"), [
    ("property", "property-bm25.json"),
    ("constitutional_law", "constitutional-law-bm25.json"),
])
def test_portable_baselines_bind_exact_suite_assets(suite_name, baseline_name):
    suite_dir = SUITES_ROOT / suite_name
    manifest = json.loads(
        (suite_dir / "manifest.json").read_text(encoding="utf-8")
    )
    baseline = json.loads(
        (REPOSITORY_ROOT / "evaluation" / "baselines" / baseline_name)
        .read_text(encoding="utf-8")
    )
    configuration = baseline["configuration"]

    assert baseline["schema_version"] == retrieval_eval.REPORT_SCHEMA_VERSION
    assert configuration["retriever"] == "bm25"
    assert configuration["grounding_scorer_version"] == 2
    assert configuration["judgment_scorer_version"] == (
        retrieval_eval.JUDGMENT_SCORER_VERSION)
    assert configuration["table_family_judgment_schema_version"] == (
        retrieval_eval.TABLE_FAMILY_JUDGMENT_SCHEMA_VERSION)
    assert configuration["table_retrieval_policy"] == (
        retrieval_eval._table_retrieval_policy_contract())
    assert configuration["queries_sha256"] == manifest["queries"]["sha256"]
    assert configuration["index_snapshot"]["source_sha256"] == (
        manifest["chunks"]["sha256"])
    assert configuration["index_snapshot"]["record_count"] == (
        manifest["chunks"]["record_count"])
    assert configuration["index_snapshot"]["table_child_count"] == 0
    assert configuration["context_window"] == 0
    assert configuration["context_max_characters"] == 8000
    assert configuration["context_segment_characters"] == 1600
    assert "path" not in configuration["index_snapshot"]
    assert baseline["metrics"]["abstention_accuracy"] == 1.0
    assert baseline["metrics"]["filter_compliance"] == 1.0
    assert baseline["metrics"]["grounding_accuracy"] == 1.0
    assert baseline["metrics"][
        "claim_citation_entailment_accuracy"] == 1.0
    assert baseline["metrics"]["unsupported_claim_rate"] == 0.0
    assert baseline["metrics"]["answer_abstention_accuracy"] == 1.0
    assert baseline["metrics"][
        "prompt_injection_fixture_accuracy"] == 1.0
