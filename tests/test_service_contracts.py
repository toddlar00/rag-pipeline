import json
from dataclasses import dataclass

import pytest

import service_contracts as contracts


def test_strict_json_rejects_duplicates_constants_non_objects_and_size():
    assert contracts.decode_json_object(b'{"query":"rule 12"}') == {
        "query": "rule 12"}
    for raw in (
            b'{"query":"a","query":"b"}',
            b'{"query":NaN}',
            b'{"query":Infinity}',
            b'[]',
            b'\xff',
            ('{"query":' + '9' * 5000 + '}').encode(),
    ):
        with pytest.raises(contracts.ServiceContractError) as raised:
            contracts.decode_json_object(raw)
        assert raised.value.code == "invalid_json"
    with pytest.raises(contracts.ServiceContractError) as raised:
        contracts.decode_json_object(b'{"a":1}', max_bytes=2)
    assert raised.value.code == "payload_too_large"


def test_search_request_is_exact_bounded_and_typed():
    parsed = contracts.parse_search_request({
        "query": "minimum contacts",
        "limit": 7,
        "mode": "hybrid",
        "filters": {"content_type": "case_opinion", "chapter_num": 4},
    })
    assert parsed.query == "minimum contacts"
    assert parsed.limit == 7
    assert parsed.mode == "hybrid"
    assert parsed.filters.as_dict() == {
        "content_type": "case_opinion", "chapter_num": 4}

    invalid = [
        {},
        {"query": "   "},
        {"query": "ok", "unknown": True},
        {"query": "ok", "limit": True},
        {"query": "ok", "limit": 0},
        {"query": "ok", "limit": contracts.MAX_SEARCH_LIMIT + 1},
        {"query": "ok", "mode": "magic"},
        {"query": "ok", "filters": {"chapter_num": True}},
        {"query": "ok", "filters": {"content_type": "secret"}},
        {"query": "ok", "filters": {"source_file": "private.pdf"}},
        {"query": "x" * (contracts.MAX_QUERY_CHARACTERS + 1)},
        {"query": "\ud800"},
    ]
    for payload in invalid:
        with pytest.raises(contracts.ServiceContractError):
            contracts.parse_search_request(payload)


def test_search_request_repr_never_contains_raw_query():
    marker = "private-query-marker-1234"
    request = contracts.SearchRequest(marker)

    assert marker not in repr(request)


def test_reindex_request_rejects_unknown_and_non_boolean_fields():
    assert not contracts.parse_reindex_request({}).full_reindex
    assert contracts.parse_reindex_request({
        "full_reindex": True}).full_reindex
    for payload in ({"full_reindex": 1}, {"path": "private"}):
        with pytest.raises(contracts.ServiceContractError):
            contracts.parse_reindex_request(payload)


@pytest.mark.parametrize("value", ["Corpus", "-book", "book space", "", 3])
def test_corpus_ids_are_stable_lowercase_identifiers(value):
    with pytest.raises(contracts.ServiceContractError):
        contracts.validate_corpus_id(value)
    assert contracts.validate_corpus_id("con-law_2026") == "con-law_2026"


def test_header_credentials_are_bounded_ascii_tokens():
    assert contracts.validate_idempotency_key("request-1234") == "request-1234"
    assert contracts.validate_bearer_token("a" * 32) == "a" * 32
    for value in ("short", "contains space", "é" * 32, "x" * 129):
        with pytest.raises(contracts.ServiceContractError):
            contracts.validate_idempotency_key(value)
    for value in ("short", "x y" * 20, "é" * 32):
        with pytest.raises(contracts.ServiceContractError):
            contracts.validate_bearer_token(value)


def test_job_etag_round_trip_and_stale_shapes_fail_closed():
    value = contracts.job_etag(3, 14)
    assert value == '"rag-job-a3-r14"'
    assert contracts.parse_job_etag(value) == (3, 14)
    for invalid in ('W/"rag-job-a3-r14"', '"rag-job-r14"', "*", None):
        with pytest.raises(contracts.ServiceContractError) as raised:
            contracts.parse_job_etag(invalid)
        assert raised.value.code == "precondition_failed"


def test_job_cursor_is_canonical_exact_and_tamper_checked():
    job_id = "a" * 32
    cursor = contracts.encode_job_cursor(1234.5, job_id)
    assert contracts.decode_job_cursor(cursor) == (1234.5, job_id)
    with pytest.raises(contracts.ServiceContractError):
        contracts.decode_job_cursor(cursor + "=")
    malformed = contracts.encode_job_cursor(5, job_id)
    decoded = json.loads(__import__("base64").urlsafe_b64decode(
        malformed + "=" * (-len(malformed) % 4)))
    decoded["job_id"] = "not-a-job"
    tampered = __import__("base64").urlsafe_b64encode(
        json.dumps(decoded, separators=(",", ":"), sort_keys=True).encode()
    ).rstrip(b"=").decode()
    with pytest.raises(contracts.ServiceContractError):
        contracts.decode_job_cursor(tampered)


def test_idempotency_key_derives_stable_operation_scoped_job_id():
    first = contracts.job_id_for_idempotency("property", "request-1234")
    assert first == contracts.job_id_for_idempotency(
        "property", "request-1234")
    assert first != contracts.job_id_for_idempotency(
        "property", "request-5678")
    assert len(first) == 32
    int(first, 16)


def test_corpus_public_contract_never_exposes_private_configuration(tmp_path):
    config = contracts.CorpusConfig(
        corpus_id="property",
        db_path=(tmp_path / "private-qdrant").resolve(),
        chunks_path=(tmp_path / "private-chunks.jsonl").resolve(),
        collection_name="private_collection",
        embedding_model="private/model",
    )
    rendered = json.dumps(config.public_dict())
    assert config.backend == "qdrant"
    assert str(tmp_path) not in rendered
    assert "private_collection" not in rendered
    assert "private/model" not in rendered
    assert config.public_dict()["capabilities"]["rerank"] is False
    assert str(tmp_path) not in repr(config)


@pytest.mark.parametrize(("field_name", "maximum"), [
    ("search_timeout_seconds", contracts.MAX_PLATFORM_TIMEOUT_SECONDS),
    ("db_lock_timeout_seconds", contracts.MAX_PLATFORM_TIMEOUT_SECONDS),
    ("reindex_timeout_seconds", contracts.MAX_REINDEX_TIMEOUT_SECONDS),
])
def test_corpus_config_rejects_timeouts_downstream_cannot_accept(
        tmp_path, field_name, maximum):
    options = {field_name: maximum + 1.0}

    with pytest.raises(contracts.ServiceContractError):
        contracts.CorpusConfig(
            corpus_id="property",
            db_path=(tmp_path / "qdrant").resolve(),
            chunks_path=(tmp_path / "chunks.jsonl").resolve(),
            collection_name="property",
            embedding_model="test-model",
            **options,
        )


@dataclass
class _Summary:
    job_id: str = "b" * 32
    command: str = "index"
    status: str = "running"
    attempt_number: int = 2
    revision: int = 3
    created_at: float = 10.0
    updated_at: float = 11.0
    argv: tuple[str, ...] = ("--chunks", "C:/private/book.jsonl")
    attempt_token: str = "secret"


def test_public_job_serializes_only_redacted_summary_fields():
    rendered = contracts.public_job(_Summary())
    encoded = json.dumps(rendered)
    assert rendered["etag"] == '"rag-job-a2-r3"'
    assert rendered["terminal"] is False
    assert "private" not in encoded
    assert "secret" not in encoded
    assert "argv" not in encoded


@dataclass
class _Hit:
    text: str
    metadata: dict
    score: float
    source_id: str


@dataclass
class _Response:
    hits: list
    backend: str = "qdrant"
    requested_mode: str = "hybrid"
    effective_mode: str = "vector"
    reranker_applied: bool = False
    warnings: list = None


def test_public_search_response_allowlists_metadata_and_warning_codes():
    response = _Response(
        hits=[_Hit(
            text="holding",
            metadata={
                "content_type": "case_opinion",
                "chapter_num": 4,
                "primary_case": "International Shoe",
                "source_file": "C:/private/book.pdf",
                "api_key": "secret",
            },
            score=0.75,
            source_id="chunk_abcd",
        )],
        warnings=["Hybrid BM25 search failed (private path); used vector search."],
    )
    rendered = contracts.public_search_response(
        response, corpus_id="property", request_id="req-1")
    encoded = json.dumps(rendered)
    assert rendered["warnings"] == ["hybrid_lexical_failed"]
    assert rendered["hits"][0]["score_kind"] == "relevance"
    assert rendered["hits"][0]["metadata"] == {
        "chapter_num": 4,
        "content_type": "case_opinion",
        "primary_case": "International Shoe",
    }
    assert "private" not in encoded
    assert "secret" not in encoded
    assert "source_file" not in encoded


def test_public_search_response_rejects_nonfinite_scores_and_bounds_text():
    response = _Response(
        hits=[_Hit("x", {}, float("nan"), "chunk_abcd")], warnings=[])
    with pytest.raises(contracts.ServiceContractError) as raised:
        contracts.public_search_response(
            response, corpus_id="property", request_id="req-1")
    assert raised.value.code == "service_unavailable"

    response.hits[0].score = 1.0
    response.hits[0].text = "x" * (
        contracts.MAX_HIT_TEXT_CHARACTERS + 10)
    rendered = contracts.public_search_response(
        response, corpus_id="property", request_id="req-1")
    assert rendered["hits"][0]["text_truncated"] is True
    assert len(rendered["hits"][0]["text"]) == (
        contracts.MAX_HIT_TEXT_CHARACTERS)


def test_worker_public_search_response_is_strictly_revalidated():
    response = _Response(
        hits=[_Hit("holding", {"chapter_num": 4}, 0.75, "chunk_abcd")],
        warnings=[],
    )
    rendered = contracts.public_search_response(
        response, corpus_id="property", request_id="req-1")

    assert contracts.validate_public_search_response(
        rendered,
        expected_corpus_id="property",
        expected_request_id="req-1",
    ) == rendered

    invalid_variants = []
    for mutate in (
        lambda value: value.update({"private_path": "C:/secret"}),
        lambda value: value["hits"][0].update({"private_path": "C:/secret"}),
        lambda value: value["hits"][0]["metadata"].update(
            {"source_file": "C:/secret"}),
        lambda value: value.update({"warnings": ["C:/secret"]}),
        lambda value: value.update({"schema_version": True}),
    ):
        candidate = json.loads(json.dumps(rendered))
        mutate(candidate)
        invalid_variants.append(candidate)

    for invalid in invalid_variants:
        with pytest.raises(contracts.ServiceContractError) as raised:
            contracts.validate_public_search_response(
                invalid,
                expected_corpus_id="property",
                expected_request_id="req-1",
            )
        assert raised.value.code == "service_unavailable"


def test_public_search_response_enforces_composed_multibyte_budget():
    response = _Response(
        hits=[
            _Hit(
                "😀" * contracts.MAX_HIT_TEXT_CHARACTERS,
                {"headings": ["h" * 512] * 16},
                1.0 - index / 100,
                f"chunk_{index}",
            )
            for index in range(contracts.MAX_SEARCH_LIMIT)
        ],
        warnings=[],
    )

    rendered = contracts.public_search_response(
        response, corpus_id="property", request_id="req-1")
    encoded = json.dumps(
        rendered, ensure_ascii=False, sort_keys=True,
        separators=(",", ":")).encode("utf-8")

    assert len(encoded) <= contracts.MAX_PUBLIC_SEARCH_RESPONSE_BYTES
    assert rendered["hits"][0]["text"]
    assert rendered["hits"][-1]["text_truncated"] is True
    assert contracts.validate_public_search_response(
        rendered,
        expected_corpus_id="property",
        expected_request_id="req-1",
    ) == rendered


def test_public_metadata_rejects_nested_arrays_outside_openapi_contract():
    assert contracts.public_metadata({
        "headings": ["Rule 12", 3, None, True],
    }) == {"headings": ["Rule 12", 3, None, True]}

    with pytest.raises(contracts.ServiceContractError) as raised:
        contracts.public_metadata({"headings": [["nested"]]})

    assert raised.value.code == "service_unavailable"

    response = _Response(
        hits=[_Hit("holding", {"headings": ["flat"]}, 0.75, "chunk_abcd")],
        warnings=[],
    )
    rendered = contracts.public_search_response(
        response, corpus_id="property", request_id="req-1")
    rendered["hits"][0]["metadata"]["headings"] = [["nested"]]
    with pytest.raises(contracts.ServiceContractError) as raised:
        contracts.validate_public_search_response(
            rendered,
            expected_corpus_id="property",
            expected_request_id="req-1",
        )
    assert raised.value.code == "service_unavailable"


def test_problem_contract_has_no_detail_or_exception_text():
    problem = contracts.ServiceProblem(
        "service_unavailable", "request-7").as_dict()
    assert problem["status"] == 503
    assert problem["retryable"] is True
    assert "detail" not in problem
    assert set(problem) == {
        "type", "title", "status", "code", "request_id", "retryable"}
