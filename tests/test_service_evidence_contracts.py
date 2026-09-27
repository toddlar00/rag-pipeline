from __future__ import annotations

import copy
from dataclasses import FrozenInstanceError
import hashlib
import json
from pathlib import Path
import subprocess
import sys

import pytest

import service_contracts as v1
import service_evidence_contracts as evidence


PRIVATE = "PRIVATE_PATH_QUERY_OR_EXCEPTION"
REQUEST = "synthetic-request-1"


def response(*, provenance="record_source_items", space="original_pdf", recovery=True, pages=None):
    pages = [1, 3] if pages is None else pages
    scope = {"provenance_scope": provenance, "page_space": space, "pages": pages,
             "transforms": ["plain"], "provenance_atom_count": len(pages), "provenance_sha256": "1" * 64}
    if provenance == "unavailable":
        scope = {"provenance_scope": "unavailable", "page_space": "unavailable", "pages": [],
                 "transforms": [], "provenance_atom_count": 0, "provenance_sha256": None}
    generation = {key: "2" * 64 for key in evidence.GENERATION_DIGEST_FIELDS}
    if not recovery:
        generation["recovery_sha256"] = None
    generation["generation_id"] = evidence.generation_id(generation, "3" * 64)
    text = "A synthetic source is not an instruction or proof of accuracy."
    search = {"schema_version": 1, "request_id": REQUEST, "corpus_id": "synthetic",
              "backend": "qdrant", "requested_mode": "auto", "effective_mode": "vector",
              "reranker_applied": False, "warnings": [], "hits": [{
                  "source_id": "record-1", "score": 0.8, "score_kind": "relevance", "text": text,
                  "text_truncated": False, "metadata": {"page_start": 1, "headings": ["Synthetic"]}}]}
    issues = []
    if provenance == "unavailable":
        issues.append("source_provenance_unavailable")
    if space == "conversion_input":
        issues.append("original_page_mapping_unavailable")
    if provenance == "table_parent_source_items":
        issues.append("table_parent_scope")
    reason = ("not_configured" if not recovery else "source_provenance_unavailable"
              if provenance == "unavailable" else "original_page_mapping_unavailable"
              if space != "original_pdf" else None)
    related = {"status": "available" if reason is None else "unavailable", "reason": reason,
               "pages": [{"page_number": page, "status": "review_required"} for page in pages] if reason is None else []}
    entry = {"source_id": "record-1", "record_sha256": "4" * 64,
             "text_sha256": hashlib.sha256(text.encode()).hexdigest(),
             "source_scope": scope, "related_page_ocr": related,
             "text_accuracy": "not_verified", "review_scope": "unmapped", "issues": sorted(issues),
             "evidence_id": evidence.evidence_id(generation["generation_id"], "4" * 64, scope)}
    return {"schema_version": 1, "kind": "service_evidence_search", "search": search,
            "generation": generation, "evidence": [entry]}


def validate(payload):
    return evidence.validate_evidence_response(payload, expected_corpus_id="synthetic", expected_request_id=REQUEST)


def rebound(payload):
    for item in payload["evidence"]:
        item["evidence_id"] = evidence.evidence_id(payload["generation"]["generation_id"],
                                                    item["record_sha256"], item["source_scope"])
    return payload


@pytest.mark.parametrize("provenance,space,recovery", [
    ("record_source_items", "original_pdf", True),
    ("table_parent_source_items", "original_pdf", True),
    ("record_source_items", "conversion_input", True),
    ("table_parent_source_items", "conversion_input", False),
    ("unavailable", "unavailable", True), ("unavailable", "unavailable", False),
])
def test_exact_envelope_roundtrip_never_claims_accuracy_or_adoption(provenance, space, recovery):
    payload = response(provenance=provenance, space=space, recovery=recovery)
    result = validate(payload)
    assert result == payload and result is not payload
    assert result["search"] == v1.validate_public_search_response(
        payload["search"], expected_corpus_id="synthetic", expected_request_id=REQUEST)
    assert result["evidence"][0]["text_accuracy"] == "not_verified"
    assert result["evidence"][0]["review_scope"] == "unmapped"
    result["search"]["hits"][0]["metadata"]["headings"].append("modified")
    result["evidence"][0]["source_scope"]["pages"].append(5)
    assert "modified" not in payload["search"]["hits"][0]["metadata"]["headings"]
    assert 5 not in payload["evidence"][0]["source_scope"]["pages"]


def test_empty_search_is_empty_not_fabricated_evidence():
    payload = response()
    payload["search"]["hits"] = []
    payload["evidence"] = []
    assert validate(payload) == payload


@pytest.mark.parametrize("expected", [False, True, {}, "private", v1.SearchRequest(PRIVATE, limit=True),
    v1.SearchRequest(PRIVATE, limit=0), v1.SearchRequest(PRIVATE, limit=51),
    v1.SearchRequest(PRIVATE, mode="raw"), v1.SearchRequest([], mode="auto"),
    v1.SearchRequest(PRIVATE, filters={}),
    v1.SearchRequest(PRIVATE, filters=v1.SearchFilters(chapter_num=True))])
def test_optional_expected_request_must_be_valid_and_type_strict(expected):
    with pytest.raises(v1.ServiceContractError) as caught:
        evidence.validate_evidence_response(response(), expected_corpus_id="synthetic",
                                            expected_request_id=REQUEST, expected_request=expected)
    assert PRIVATE not in str(caught.value)


def test_optional_request_checks_mode_and_limit_without_exposing_query():
    payload = response()
    actual = v1.parse_search_request({"query": PRIVATE, "mode": "auto", "limit": 1})
    assert evidence.validate_evidence_response(payload, expected_corpus_id="synthetic",
        expected_request_id=REQUEST, expected_request=actual) == payload
    with pytest.raises(v1.ServiceContractError):
        evidence.validate_evidence_response(payload, expected_corpus_id="synthetic", expected_request_id=REQUEST,
            expected_request=v1.parse_search_request({"query": PRIVATE, "mode": "vector"}))
    second_hit = copy.deepcopy(payload["search"]["hits"][0])
    second_entry = copy.deepcopy(payload["evidence"][0])
    second_hit["source_id"] = second_entry["source_id"] = "record-2"
    payload["search"]["hits"].append(second_hit)
    payload["evidence"].append(second_entry)
    assert validate(payload) == payload
    with pytest.raises(v1.ServiceContractError):
        evidence.validate_evidence_response(payload, expected_corpus_id="synthetic",
                                            expected_request_id=REQUEST, expected_request=actual)


@pytest.mark.parametrize("field,value", [("content_type", "table"), ("content_type", None),
    ("content_type", []), ("chapter_num", None), ("chapter_num", True), ("chapter_num", 1.0),
    ("chapter_num", "1"), ("chapter_num", 2)])
def test_optional_request_filters_match_public_metadata_without_type_coercion(field, value):
    payload = response()
    metadata = payload["search"]["hits"][0]["metadata"]
    metadata.update(content_type="author_narrative", chapter_num=1)
    expected = v1.parse_search_request({"query": PRIVATE, "filters": {
        "content_type": "author_narrative", "chapter_num": 1}})
    assert evidence.validate_evidence_response(payload, expected_corpus_id="synthetic",
        expected_request_id=REQUEST, expected_request=expected) == payload
    metadata[field] = value
    with pytest.raises(v1.ServiceContractError):
        evidence.validate_evidence_response(payload, expected_corpus_id="synthetic",
                                            expected_request_id=REQUEST, expected_request=expected)


def test_requested_filter_cannot_be_silently_omitted_and_empty_results_are_valid():
    payload = response()
    expected = v1.parse_search_request({"query": PRIVATE, "filters": {"chapter_num": 1}})
    with pytest.raises(v1.ServiceContractError):
        evidence.validate_evidence_response(payload, expected_corpus_id="synthetic",
                                            expected_request_id=REQUEST, expected_request=expected)
    payload["search"]["hits"] = []
    payload["evidence"] = []
    assert evidence.validate_evidence_response(payload, expected_corpus_id="synthetic",
        expected_request_id=REQUEST, expected_request=expected) == payload


@pytest.mark.parametrize("status", sorted(evidence.RELATED_PAGE_STATUSES))
def test_all_related_statuses_preserve_unverified_and_unmapped(status):
    payload = response(pages=[1])
    payload["evidence"][0]["related_page_ocr"]["pages"][0]["status"] = status
    result = validate(payload)["evidence"][0]
    assert result["text_accuracy"] == "not_verified" and result["review_scope"] == "unmapped"


def test_quality_backed_conversion_scope_does_not_require_configured_docling():
    payload = response(space="conversion_input", recovery=False)
    payload["generation"]["docling_sha256"] = None
    payload["generation"]["conversion_manifest_sha256"] = None
    assert validate(payload) == payload


def test_legacy_unavailable_source_scope_can_have_only_manifest_and_chunks():
    payload = response(provenance="unavailable", space="unavailable", recovery=False)
    for key in evidence.GENERATION_DIGEST_FIELDS - {"index_manifest_sha256", "chunks_sha256"}:
        payload["generation"][key] = None
    assert validate(payload) == payload


@pytest.mark.parametrize("path,value", [
    (("schema_version",), True), (("schema_version",), 2), (("kind",), PRIVATE),
    (("generation", "index_manifest_sha256"), None), (("generation", "chunks_sha256"), "A" * 64),
    (("generation", "generation_id"), True), (("generation", "recovery_sha256"), PRIVATE),
    (("search", "request_id"), "other"), (("search", "corpus_id"), "other"),
    (("search", "hits", 0, "score"), 10 ** 1000),
    (("search", "hits", 0, "text_truncated"), 1),
    (("evidence", 0, "source_id"), "other"), (("evidence", 0, "record_sha256"), PRIVATE),
    (("evidence", 0, "text_sha256"), "f" * 64), (("evidence", 0, "evidence_id"), "f" * 64),
    (("evidence", 0, "text_accuracy"), "verified"), (("evidence", 0, "review_scope"), "adopted"),
    (("evidence", 0, "issues"), [PRIVATE]), (("evidence", 0, "issues"), ["table_parent_scope"]),
    (("evidence", 0, "related_page_ocr", "status"), "adopted"),
    (("evidence", 0, "related_page_ocr", "reason"), PRIVATE),
    (("evidence", 0, "related_page_ocr", "pages", 0, "page_number"), True),
    (("evidence", 0, "related_page_ocr", "pages", 0, "status"), "accepted"),
])
def test_malformed_scalar_and_correspondence_values_are_private(path, value):
    payload = response()
    target = payload
    for key in path[:-1]:
        target = target[key]
    target[path[-1]] = value
    with pytest.raises(v1.ServiceContractError) as caught:
        validate(payload)
    assert caught.value.code == "service_unavailable" and PRIVATE not in str(caught.value)


@pytest.mark.parametrize("path", [(), ("generation",), ("search",), ("evidence", 0),
                                  ("evidence", 0, "source_scope"), ("evidence", 0, "related_page_ocr"),
                                  ("evidence", 0, "related_page_ocr", "pages", 0)])
def test_all_new_shapes_reject_extra_private_fields_without_traversing_cycles(path):
    payload = response()
    target = payload
    for key in path:
        target = target[key]
    target[PRIVATE] = payload
    with pytest.raises(v1.ServiceContractError):
        validate(payload)


@pytest.mark.parametrize("field,value", [
    ("pages", [True]), ("pages", [0]), ("pages", [5001]), ("pages", [2, 1]), ("pages", [1, 1]),
    ("pages", []), ("pages", [1] * 5001), ("pages", {}),
    ("transforms", []), ("transforms", ["plain", "plain"]), ("transforms", ["ocr_verified"]),
    ("transforms", [[PRIVATE]]), ("provenance_atom_count", True), ("provenance_atom_count", 0),
    ("provenance_atom_count", 100001), ("provenance_atom_count", 1),
    ("provenance_sha256", None), ("page_space", "scan"), ("provenance_scope", "table_row_geometry"),
])
def test_scope_is_sorted_bounded_and_honest(field, value):
    payload = response()
    payload["evidence"][0]["source_scope"][field] = value
    with pytest.raises(v1.ServiceContractError):
        validate(payload)


@pytest.mark.parametrize("name", ["quality_sha256", "chunk_completion_sha256", "source_oracles_sha256",
                                  "conversion_manifest_sha256", "docling_sha256"])
def test_required_source_binding_digests_cannot_be_removed(name):
    payload = response()
    payload["generation"][name] = None
    with pytest.raises(v1.ServiceContractError):
        validate(payload)


@pytest.mark.parametrize("present", [
    ["quality_sha256"], ["chunk_completion_sha256"], ["source_oracles_sha256"],
    ["quality_sha256", "chunk_completion_sha256"], ["docling_sha256"], ["conversion_manifest_sha256"],
    ["docling_sha256", "conversion_manifest_sha256"], ["recovery_sha256"],
])
def test_unavailable_scope_still_cannot_hide_impossible_generation_bindings(present):
    payload = response(provenance="unavailable", space="unavailable", recovery=False)
    for key in evidence.GENERATION_DIGEST_FIELDS - {"index_manifest_sha256", "chunks_sha256"}:
        payload["generation"][key] = "7" * 64 if key in present else None
    with pytest.raises(v1.ServiceContractError):
        validate(payload)


@pytest.mark.parametrize("mutation", ["missing", "extra", "duplicate", "reordered", "scope_mutated",
                                     "page_missing", "page_extra", "page_reordered", "unavailable_pages"])
def test_no_dropped_reordered_or_fabricated_hit_and_page_associations(mutation):
    payload = response()
    if mutation == "missing":
        payload["evidence"] = []
    elif mutation in {"extra", "duplicate", "reordered"}:
        second = copy.deepcopy(payload["evidence"][0])
        payload["evidence"].append(second)
        if mutation != "extra":
            second_hit = copy.deepcopy(payload["search"]["hits"][0])
            payload["search"]["hits"].append(second_hit)
            if mutation == "reordered":
                second["source_id"] = second_hit["source_id"] = "record-2"
                payload["evidence"].reverse()
    elif mutation == "scope_mutated":
        payload["evidence"][0]["source_scope"]["pages"] = [1, 2]
    elif mutation == "unavailable_pages":
        payload = response(space="conversion_input")
        payload["evidence"][0]["related_page_ocr"]["pages"] = [{"page_number": 1, "status": "review_required"}]
    else:
        pages = payload["evidence"][0]["related_page_ocr"]["pages"]
        if mutation == "page_missing":
            pages.pop()
        elif mutation == "page_extra":
            pages.append({"page_number": 4, "status": "not_selected"})
        else:
            pages.reverse()
    with pytest.raises(v1.ServiceContractError):
        validate(payload)


def test_truncated_text_retains_full_text_digest_and_distinct_record_identity():
    payload = response()
    original_digest = payload["evidence"][0]["text_sha256"]
    payload["search"]["hits"][0]["text"] = "A synthetic"
    payload["search"]["hits"][0]["text_truncated"] = True
    second_hit = copy.deepcopy(payload["search"]["hits"][0])
    second_hit["source_id"] = "record-2"
    second = copy.deepcopy(payload["evidence"][0])
    second.update(source_id="record-2", record_sha256="5" * 64, text_sha256="6" * 64)
    payload["search"]["hits"].append(second_hit)
    payload["evidence"].append(second)
    result = validate(rebound(payload))
    assert result["search"]["hits"][0]["text"] == result["search"]["hits"][1]["text"]
    assert result["evidence"][0]["text_sha256"] == original_digest
    assert result["evidence"][0]["evidence_id"] != result["evidence"][1]["evidence_id"]


def test_public_budgets_are_fixed_and_companion_never_silently_truncates():
    assert evidence.MAX_PUBLIC_EVIDENCE_RESPONSE_BYTES == 2_000_000
    assert evidence.MAX_EVIDENCE_BYTES == 96_000
    payload = response(pages=list(range(1, 5001)))
    with pytest.raises(v1.ServiceContractError):
        validate(payload)
    assert len(payload["evidence"][0]["source_scope"]["pages"]) == 5000


def test_total_budget_is_checked_on_canonical_utf8(monkeypatch):
    payload = response()
    size = len(json.dumps(payload, ensure_ascii=False, sort_keys=True, separators=(",", ":")).encode())
    monkeypatch.setattr(evidence, "MAX_PUBLIC_EVIDENCE_RESPONSE_BYTES", size)
    assert validate(payload) == payload
    monkeypatch.setattr(evidence, "MAX_PUBLIC_EVIDENCE_RESPONSE_BYTES", size - 1)
    with pytest.raises(v1.ServiceContractError):
        validate(payload)


@pytest.mark.parametrize("value", [["x"] * 17, "x" * 513, [["nested"]], {"private": PRIVATE}, ("tuple",)])
def test_metadata_preflight_rejects_oversize_or_non_json_before_v1_copy(value, monkeypatch):
    payload = response()
    payload["search"]["hits"][0]["metadata"]["headings"] = value

    def forbidden(*args, **kwargs):
        raise AssertionError("must reject before v1 public-metadata copying")

    monkeypatch.setattr(v1, "validate_public_search_response", forbidden)
    with pytest.raises(v1.ServiceContractError):
        validate(payload)


def test_deep_metadata_and_evidence_cycles_are_rejected_without_serializing():
    payload = response()
    cyclic = []
    cyclic.append(cyclic)
    payload["search"]["hits"][0]["metadata"]["headings"] = cyclic
    with pytest.raises(v1.ServiceContractError):
        validate(payload)
    payload = response()
    payload["evidence"] = [payload]
    with pytest.raises(v1.ServiceContractError):
        validate(payload)


def test_domain_separated_ids_include_private_source_without_returning_it():
    digests = {key: "1" * 64 for key in evidence.GENERATION_DIGEST_FIELDS}
    first = evidence.generation_id(digests, "2" * 64)
    assert first == evidence.generation_id(dict(reversed(list(digests.items()))), "2" * 64)
    assert first != evidence.generation_id(digests, "3" * 64)
    assert first != evidence.generation_id(digests)
    scope = response()["evidence"][0]["source_scope"]
    assert evidence.evidence_id(first, "4" * 64, scope) != first
    changed = copy.deepcopy(scope)
    changed["pages"] = [1, 2]
    assert evidence.evidence_id(first, "4" * 64, scope) != evidence.evidence_id(first, "4" * 64, changed)


@pytest.fixture
def corpora(tmp_path):
    return {"synthetic": v1.CorpusConfig("synthetic", tmp_path / "db", tmp_path / "chunks.jsonl",
                                        "synthetic", "synthetic-model")}


def config_entry(**changes):
    return {"corpus_id": "synthetic", "docling_path": None, "recovery_path": None, **changes}


def registry(entry=None):
    return {"schema_version": 1, "kind": "service_evidence_config", "corpora": [entry or config_entry()]}


def test_operator_paths_are_private_frozen_and_lexical_without_io(tmp_path, corpora, monkeypatch):
    def forbidden(*args, **kwargs):
        raise AssertionError("contract must not perform filesystem IO")

    monkeypatch.setattr(Path, "resolve", forbidden)
    monkeypatch.setattr(Path, "stat", forbidden)
    monkeypatch.setattr(Path, "open", forbidden)
    payload = registry(config_entry(docling_path=f"nested/../{PRIVATE}.json", recovery_path="retry.json"))
    parsed = evidence.parse_evidence_registry(payload, base=tmp_path, corpora=corpora)["synthetic"]
    assert parsed.docling_path == tmp_path / f"{PRIVATE}.json"
    assert parsed.recovery_path == tmp_path / "retry.json"
    assert PRIVATE not in repr(parsed)
    assert evidence.parse_evidence_corpus(evidence.config_payload(parsed), base=tmp_path) == parsed
    with pytest.raises(FrozenInstanceError):
        parsed.corpus_id = "changed"


@pytest.mark.parametrize("change", [
    {"corpus_id": "unknown"}, {"corpus_id": PRIVATE}, {"corpus_id": True},
    {"docling_path": ""}, {"docling_path": "   "}, {"docling_path": "a\x00b"},
    {"docling_path": "a\nb"}, {"docling_path": "\ud800"}, {"docling_path": True},
    {"docling_path": []}, {"docling_path": "a" * 4097}, {"recovery_path": "retry.json"},
    {"url": PRIVATE},
])
def test_operator_config_rejects_bad_paths_extra_fields_and_unknown_corpora(change, tmp_path, corpora):
    with pytest.raises(v1.ServiceContractError) as caught:
        evidence.parse_evidence_registry(registry(config_entry(**change)), base=tmp_path, corpora=corpora)
    assert PRIVATE not in str(caught.value)


@pytest.mark.parametrize("mutation", ["bool", "kind", "extra", "duplicate", "oversized", "missing", "cycle"])
def test_registry_is_strict_bounded_and_closed(mutation, tmp_path, corpora):
    payload = registry()
    if mutation == "bool":
        payload["schema_version"] = True
    elif mutation == "kind":
        payload["kind"] = "v1"
    elif mutation == "extra":
        payload[PRIVATE] = True
    elif mutation == "duplicate":
        payload["corpora"] *= 2
    elif mutation == "oversized":
        payload["corpora"] *= 65
    elif mutation == "missing":
        payload["corpora"][0].pop("docling_path")
    else:
        payload["corpora"][0]["docling_path"] = payload
    with pytest.raises(v1.ServiceContractError):
        evidence.parse_evidence_registry(payload, base=tmp_path, corpora=corpora)


def test_empty_registry_is_valid_and_relative_base_is_rejected(tmp_path, corpora):
    payload = registry()
    payload["corpora"] = []
    assert evidence.parse_evidence_registry(payload, base=tmp_path, corpora=corpora) == {}
    with pytest.raises(v1.ServiceContractError):
        evidence.parse_evidence_registry(payload, base=Path("relative"), corpora=corpora)
    with pytest.raises(v1.ServiceContractError):
        evidence.EvidenceCorpusConfig("synthetic", Path("relative"))


def test_import_is_dependency_light_and_has_no_service_runtime_or_model_side_effects():
    command = "import sys; import service_evidence_contracts; assert not any(x in sys.modules for x in ('rag','fastapi','rapidocr','numpy','service_runtime','service_evidence_search'))"
    result = subprocess.run([sys.executable, "-B", "-c", command], cwd=Path(__file__).resolve().parents[1],
                            capture_output=True, text=True, timeout=10)
    assert result.returncode == 0, result.stderr
