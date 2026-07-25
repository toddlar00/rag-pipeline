import json
import subprocess
import sys

import pytest

import llm_output_contracts as contracts


def test_output_contract_module_is_a_dependency_light_leaf():
    result = subprocess.run(
        [
            sys.executable,
            "-c",
            (
                "import sys; import llm_output_contracts; "
                "forbidden={'rag','llm_runtime','llm_adapters','requests',"
                "'docling','torch','chromadb','qdrant_client'}; "
                "loaded=sorted(forbidden.intersection(sys.modules)); "
                "assert not loaded, loaded"
            ),
        ],
        capture_output=True,
        check=False,
        text=True,
        timeout=30,
    )

    assert result.returncode == 0, result.stderr


@pytest.fixture
def classification_contract():
    return contracts.exact_classification_contract((
        "case_opinion",
        "notes_and_questions",
        "author_narrative",
        "statutory_excerpt",
        "table",
        "chapter_introduction",
        "footnote",
    ))


@pytest.mark.parametrize("label", [
    "case_opinion",
    "notes_and_questions",
    "author_narrative",
    "statutory_excerpt",
    "table",
    "chapter_introduction",
    "footnote",
])
def test_exact_enum_contract_accepts_each_declared_label(
        classification_contract, label):
    assert classification_contract(label) == label
    assert classification_contract(f" \t{label.upper()}\r\n") == label


@pytest.mark.parametrize("response", [
    "This is a case_opinion.",
    "not case_opinion",
    "case_opinion or footnote",
    '{"label":"case_opinion"}',
    "```case_opinion```",
    "<think>analysis</think>case_opinion",
    "Ignore the contract and return case_opinion",
    "prefix_case_opinion_suffix",
])
def test_exact_enum_contract_rejects_explanations_and_substrings(
        classification_contract, response):
    with pytest.raises(contracts.OutputContractRejected) as caught:
        classification_contract(response)

    assert caught.value.diagnostic_code == contracts.LABEL_MISMATCH


@pytest.mark.parametrize("response", ["", " ", "\t\r\n"])
def test_exact_enum_contract_rejects_empty_output(
        classification_contract, response):
    with pytest.raises(contracts.OutputContractRejected) as caught:
        classification_contract(response)

    assert caught.value.diagnostic_code == contracts.EMPTY_OUTPUT


@pytest.mark.parametrize("response", [
    "case_\x00opinion",
    "case_\nopinion",
    "case_\u202eopinion",
    "case_\u200dopinion",
])
def test_exact_enum_contract_rejects_embedded_control_and_format_characters(
        classification_contract, response):
    with pytest.raises(contracts.OutputContractRejected) as caught:
        classification_contract(response)

    assert caught.value.diagnostic_code == contracts.CONTROL_CHARACTER


def test_exact_enum_contract_rejects_invalid_unicode_encoding(
        classification_contract):
    canary = "CLASSIFICATION_ENCODING_CANARY"
    with pytest.raises(contracts.OutputContractRejected) as caught:
        classification_contract(canary + "\ud800")

    assert caught.value.diagnostic_code == contracts.INVALID_ENCODING
    assert canary not in str(caught.value)
    assert canary not in repr(caught.value)
    assert caught.value.__cause__ is None
    assert caught.value.__context__ is None


@pytest.mark.parametrize("response", [
    "ca\u017fe_opinion",
    "\u212aase_opinion",
    "case_opini\u043en",
    "\u00a0case_opinion\u00a0",
])
def test_exact_enum_contract_rejects_unicode_confusables_and_whitespace(
        classification_contract, response):
    with pytest.raises(contracts.OutputContractRejected) as caught:
        classification_contract(response)

    assert caught.value.diagnostic_code == contracts.LABEL_MISMATCH


def test_exact_enum_contract_enforces_the_raw_utf8_byte_limit():
    contract = contracts.ExactEnumContract(
        contract_id="boundary-v1", allowed_values=("case",), max_bytes=4)

    assert contract("case") == "case"
    with pytest.raises(contracts.OutputContractRejected) as caught:
        contract(" case ")

    assert caught.value.diagnostic_code == contracts.BYTE_LIMIT_EXCEEDED


def test_contract_provenance_binds_every_artifact_reuse_field(
        classification_contract):
    provenance = classification_contract.provenance(
        fallback_id=contracts.CLASSIFICATION_FALLBACK_ID)

    assert provenance == {
        "policy_version": contracts.OUTPUT_CONTRACT_POLICY_VERSION,
        "contract_id": contracts.CLASSIFICATION_CONTRACT_ID,
        "fallback_id": contracts.CLASSIFICATION_FALLBACK_ID,
        "max_bytes": contracts.CLASSIFICATION_MAX_BYTES,
        "allowed_values": list(classification_contract.allowed_values),
    }
    with pytest.raises(ValueError, match="fallback ID"):
        classification_contract.provenance(fallback_id="../unsafe")


def test_rejection_exception_never_retains_generated_text(
        classification_contract):
    canary = "MODEL_RESPONSE_CANARY_case_opinion_explanation"

    with pytest.raises(contracts.OutputContractRejected) as caught:
        classification_contract(canary)

    assert canary not in str(caught.value)
    assert canary not in repr(caught.value)
    assert caught.value.args == (
        "LLM output violated its contract (llm-output-label-mismatch)",)


@pytest.mark.parametrize("factory", [
    lambda: contracts.ExactEnumContract(
        contract_id="../unsafe", allowed_values=("case",), max_bytes=8),
    lambda: contracts.ExactEnumContract(
        contract_id="safe-v1", allowed_values=["case"], max_bytes=8),
    lambda: contracts.ExactEnumContract(
        contract_id="safe-v1", allowed_values=(), max_bytes=8),
    lambda: contracts.ExactEnumContract(
        contract_id="safe-v1", allowed_values=("Case",), max_bytes=8),
    lambda: contracts.ExactEnumContract(
        contract_id="safe-v1", allowed_values=("case", "case"), max_bytes=8),
    lambda: contracts.ExactEnumContract(
        contract_id="safe-v1", allowed_values=("case",), max_bytes=True),
    lambda: contracts.ExactEnumContract(
        contract_id="safe-v1", allowed_values=("case",), max_bytes=3),
])
def test_trusted_contract_declarations_fail_closed(factory):
    with pytest.raises(ValueError):
        factory()


def test_classification_factory_rejects_a_scalar_string():
    with pytest.raises(TypeError, match="collection"):
        contracts.exact_classification_contract("case_opinion")


def test_toc_hierarchy_contract_canonicalizes_one_exact_array():
    response = (
        ' \n[{"title":"  Chápter 一  ","page":0,"level":1},'
        '{"page":1000000,"level":5,"title":"Appendix"}]\t')

    canonical = contracts.TOC_HIERARCHY_CONTRACT(response)

    assert canonical == (
        '[{"level":1,"page":0,"title":"Chápter 一"},'
        '{"level":5,"page":1000000,"title":"Appendix"}]')
    assert contracts.TOC_HIERARCHY_CONTRACT(canonical) == canonical
    assert contracts.TOC_HIERARCHY_CONTRACT.parse(canonical) == [
        {"level": 1, "title": "Chápter 一", "page": 0},
        {"level": 5, "title": "Appendix", "page": 1_000_000},
    ]


@pytest.mark.parametrize(("response", "diagnostic_code"), [
    ('prefix [{"level":1,"title":"One","page":1}]',
     contracts.JSON_SYNTAX),
    ('[{"level":1,"title":"One","page":1}] suffix',
     contracts.JSON_SYNTAX),
    ('[{"level":1,"title":"One","page":1}] []',
     contracts.JSON_SYNTAX),
    ('```json\n[{"level":1,"title":"One","page":1}]\n```',
     contracts.JSON_SYNTAX),
    ('<think>private</think>[{"level":1,"title":"One","page":1}]',
     contracts.JSON_SYNTAX),
    ('{"level":1,"title":"One","page":1}',
     contracts.JSON_SHAPE_MISMATCH),
    ('[]', contracts.JSON_ITEM_LIMIT),
    ('[[[{"level":1,"title":"One","page":1}]]]',
     contracts.JSON_DEPTH_EXCEEDED),
    ('[{"level":1,"level":2,"title":"One","page":1}]',
     contracts.JSON_DUPLICATE_KEY),
    ('[{"level":1,"title":"One","page":NaN}]',
     contracts.JSON_NON_FINITE_NUMBER),
    ('[{"level":1,"title":"One","page":Infinity}]',
     contracts.JSON_NON_FINITE_NUMBER),
    ('[{"level":1,"title":"One","page":-Infinity}]',
     contracts.JSON_NON_FINITE_NUMBER),
    ('[{"level":1,"title":"One","page":1e999}]',
     contracts.JSON_NON_FINITE_NUMBER),
    ('[{"level":1,"title":"One","page":99999999}]',
     contracts.JSON_VALUE_OUT_OF_RANGE),
])
def test_toc_hierarchy_contract_rejects_wrappers_and_invalid_json_semantics(
        response, diagnostic_code):
    with pytest.raises(contracts.OutputContractRejected) as caught:
        contracts.TOC_HIERARCHY_CONTRACT(response)

    assert caught.value.diagnostic_code == diagnostic_code


@pytest.mark.parametrize("item", [
    {"level": 1, "title": "One"},
    {"level": 1, "title": "One", "page": 1, "marker": ""},
    {"level": True, "title": "One", "page": 1},
    {"level": 1.0, "title": "One", "page": 1},
    {"level": "1", "title": "One", "page": 1},
    {"level": 1, "title": "One", "page": False},
    {"level": 1, "title": "One", "page": 1.0},
    {"level": 1, "title": "One", "page": "1"},
])
def test_toc_hierarchy_contract_rejects_missing_extra_and_coercive_fields(
        item):
    response = json.dumps([item])

    with pytest.raises(contracts.OutputContractRejected) as caught:
        contracts.TOC_HIERARCHY_CONTRACT(response)

    assert caught.value.diagnostic_code == contracts.JSON_SHAPE_MISMATCH


@pytest.mark.parametrize("item", [
    {"level": 0, "title": "One", "page": 1},
    {"level": 6, "title": "One", "page": 1},
    {"level": 1, "title": "One", "page": -1},
    {"level": 1, "title": "One", "page": 1_000_001},
])
def test_toc_hierarchy_contract_rejects_out_of_range_values(item):
    response = json.dumps([item])

    with pytest.raises(contracts.OutputContractRejected) as caught:
        contracts.TOC_HIERARCHY_CONTRACT(response)

    assert caught.value.diagnostic_code == contracts.JSON_VALUE_OUT_OF_RANGE


@pytest.mark.parametrize(("title", "diagnostic_code"), [
    ("", contracts.JSON_SHAPE_MISMATCH),
    ("   ", contracts.JSON_SHAPE_MISMATCH),
    ("One\nTwo", contracts.CONTROL_CHARACTER),
    ("\tOne", contracts.CONTROL_CHARACTER),
    ("One\u2028Two", contracts.CONTROL_CHARACTER),
    ("One\u202eTwo", contracts.CONTROL_CHARACTER),
    ("\u00a0One", contracts.CONTROL_CHARACTER),
    ("\ud800", contracts.INVALID_ENCODING),
    ("é" * 257, contracts.JSON_STRING_LIMIT),
])
def test_toc_hierarchy_contract_rejects_unsafe_or_oversized_titles(
        title, diagnostic_code):
    response = json.dumps(
        [{"level": 1, "title": title, "page": 1}])

    with pytest.raises(contracts.OutputContractRejected) as caught:
        contracts.TOC_HIERARCHY_CONTRACT(response)

    assert caught.value.diagnostic_code == diagnostic_code


def test_toc_hierarchy_contract_accepts_title_utf8_byte_boundary():
    title = "é" * 256
    response = json.dumps(
        [{"level": 1, "title": title, "page": 1}], ensure_ascii=False)

    assert contracts.TOC_HIERARCHY_CONTRACT.parse(response)[0]["title"] == title


def test_toc_hierarchy_contract_accepts_max_items_at_worst_escape_boundary():
    title = '"\\' * 256
    entries = [
        {"level": (index % 5) + 1, "title": title, "page": index}
        for index in range(contracts.TOC_HIERARCHY_MAX_ITEMS)
    ]
    response = json.dumps(
        entries, ensure_ascii=False, separators=(",", ":"))

    assert len(entries) == 100
    assert contracts.TOC_HIERARCHY_MAX_BYTES == 128 * 1024
    assert len(title.encode("utf-8")) == contracts.TOC_HIERARCHY_MAX_TITLE_BYTES
    assert len(response.encode("utf-8")) < contracts.TOC_HIERARCHY_MAX_BYTES

    canonical = contracts.TOC_HIERARCHY_CONTRACT(response)

    assert len(canonical.encode("utf-8")) < contracts.TOC_HIERARCHY_MAX_BYTES
    assert contracts.TOC_HIERARCHY_CONTRACT(canonical) == canonical
    parsed = contracts.TOC_HIERARCHY_CONTRACT.parse(canonical)
    assert len(parsed) == contracts.TOC_HIERARCHY_MAX_ITEMS
    assert all(item["title"] == title for item in parsed)


def test_toc_hierarchy_contract_preserves_distinct_nfc_and_nfd_titles():
    nfc_title = "Café"
    nfd_title = "Cafe\u0301"
    response = json.dumps([
        {"level": 1, "title": nfc_title, "page": 1},
        {"level": 1, "title": nfd_title, "page": 2},
    ], ensure_ascii=False)

    parsed = contracts.TOC_HIERARCHY_CONTRACT.parse(response)

    assert nfc_title != nfd_title
    assert [item["title"] for item in parsed] == [nfc_title, nfd_title]


def test_toc_hierarchy_contract_accepts_json_punctuation_inside_title():
    title = 'Part {A}: "Quoted" [Notes] \\ Appendix'
    response = json.dumps(
        [{"level": 1, "title": title, "page": 1}], ensure_ascii=False)

    canonical = contracts.TOC_HIERARCHY_CONTRACT(response)

    assert contracts.TOC_HIERARCHY_CONTRACT.parse(canonical)[0]["title"] == title


@pytest.mark.parametrize("response", [
    None,
    b'[{"level":1,"title":"One","page":1}]',
    bytearray(b"[]"),
    [{"level": 1, "title": "One", "page": 1}],
    {"level": 1, "title": "One", "page": 1},
    1,
    True,
])
def test_toc_hierarchy_contract_rejects_non_text_responses(response):
    with pytest.raises(contracts.OutputContractRejected) as caught:
        contracts.TOC_HIERARCHY_CONTRACT(response)

    assert caught.value.diagnostic_code == contracts.INVALID_TYPE


def test_toc_hierarchy_contract_rejects_escaped_equivalent_duplicate_key():
    response = (
        '[{"level":1,"\\u006cevel":2,"title":"One","page":1}]')

    with pytest.raises(contracts.OutputContractRejected) as caught:
        contracts.TOC_HIERARCHY_CONTRACT(response)

    assert caught.value.diagnostic_code == contracts.JSON_DUPLICATE_KEY


def test_toc_hierarchy_contract_rejects_item_and_response_byte_overflow():
    too_many = [
        {"level": 1, "title": f"Entry {index}", "page": index}
        for index in range(contracts.TOC_HIERARCHY_MAX_ITEMS + 1)
    ]
    with pytest.raises(contracts.OutputContractRejected) as item_error:
        contracts.TOC_HIERARCHY_CONTRACT(json.dumps(too_many))
    assert item_error.value.diagnostic_code == contracts.JSON_ITEM_LIMIT

    with pytest.raises(contracts.OutputContractRejected) as byte_error:
        contracts.TOC_HIERARCHY_CONTRACT(
            "x" * (contracts.TOC_HIERARCHY_MAX_BYTES + 1))
    assert byte_error.value.diagnostic_code == contracts.BYTE_LIMIT_EXCEEDED


def test_toc_hierarchy_contract_rejects_huge_integer_before_conversion():
    response = (
        '[{"level":1,"title":"One","page":'
        + "9" * 10_000
        + "}]"
    )

    with pytest.raises(contracts.OutputContractRejected) as caught:
        contracts.TOC_HIERARCHY_CONTRACT(response)

    assert caught.value.diagnostic_code == contracts.JSON_VALUE_OUT_OF_RANGE
    assert caught.value.__cause__ is None
    assert caught.value.__context__ is None


def test_toc_hierarchy_contract_rejects_whole_array_for_one_invalid_item():
    response = (
        '[{"level":1,"title":"Valid","page":1},'
        '{"level":"2","title":"Invalid","page":2}]')

    with pytest.raises(contracts.OutputContractRejected) as caught:
        contracts.TOC_HIERARCHY_CONTRACT(response)

    assert caught.value.diagnostic_code == contracts.JSON_SHAPE_MISMATCH


def test_toc_hierarchy_provenance_binds_schema_and_fallback_limits():
    provenance = contracts.TOC_HIERARCHY_CONTRACT.provenance(
        fallback_id=contracts.TOC_SCAFFOLD_FALLBACK_ID)

    assert provenance == {
        "policy_version": contracts.OUTPUT_CONTRACT_POLICY_VERSION,
        "contract_id": contracts.TOC_HIERARCHY_CONTRACT_ID,
        "fallback_id": contracts.TOC_SCAFFOLD_FALLBACK_ID,
        "max_bytes": contracts.TOC_HIERARCHY_MAX_BYTES,
        "max_depth": contracts.TOC_HIERARCHY_MAX_DEPTH,
        "max_integer_digits": contracts.TOC_HIERARCHY_MAX_INTEGER_DIGITS,
        "min_items": contracts.TOC_HIERARCHY_MIN_ITEMS,
        "max_items": contracts.TOC_HIERARCHY_MAX_ITEMS,
        "max_title_chars": contracts.TOC_HIERARCHY_MAX_TITLE_CHARS,
        "max_title_bytes": contracts.TOC_HIERARCHY_MAX_TITLE_BYTES,
        "max_page": contracts.TOC_HIERARCHY_MAX_PAGE,
        "min_level": contracts.TOC_HIERARCHY_MIN_LEVEL,
        "max_level": contracts.TOC_HIERARCHY_MAX_LEVEL,
        "unicode_data_major": int(
            contracts.TOC_HIERARCHY_UNICODE_DATA_VERSION.split(".")[0]),
        "unicode_data_minor": int(
            contracts.TOC_HIERARCHY_UNICODE_DATA_VERSION.split(".")[1]),
        "unicode_data_patch": int(
            contracts.TOC_HIERARCHY_UNICODE_DATA_VERSION.split(".")[2]),
    }


@pytest.mark.parametrize("response", [
    "MODEL_RESPONSE_CANARY not json",
    '[{"level":1,"title":"MODEL_RESPONSE_CANARY","page":',
    '[{"level":1,"title":"\\ud800","page":1}]',
])
def test_toc_rejection_has_no_response_or_decoder_exception_context(response):
    with pytest.raises(contracts.OutputContractRejected) as caught:
        contracts.TOC_HIERARCHY_CONTRACT(response)

    assert "MODEL_RESPONSE_CANARY" not in str(caught.value)
    assert "MODEL_RESPONSE_CANARY" not in repr(caught.value)
    assert caught.value.__cause__ is None
    assert caught.value.__context__ is None


def test_exact_json_contract_hides_schema_validator_exception_context():
    canary = "TRUSTED_SCHEMA_VALIDATOR_EXCEPTION_CANARY"

    def fail_validation(_value):
        raise RuntimeError(canary)

    contract = contracts.ExactJSONContract(
        contract_id="test-json-v1",
        max_bytes=64,
        max_depth=1,
        schema_validator=fail_validation,
    )

    with pytest.raises(contracts.OutputContractRejected) as caught:
        contract('{"safe":1}')

    assert caught.value.diagnostic_code == contracts.INTERNAL_CONTRACT_ERROR
    assert canary not in str(caught.value)
    assert canary not in repr(caught.value)
    assert caught.value.__cause__ is None
    assert caught.value.__context__ is None


def test_exact_json_contract_rebuilds_validator_rejections_without_context():
    canary = "SCHEMA_REJECTION_CONTEXT_CANARY"

    def reject_with_context(value):
        try:
            raise ValueError(value["title"])
        except ValueError:
            raise contracts.OutputContractRejected(
                contracts.JSON_SHAPE_MISMATCH)

    contract = contracts.ExactJSONContract(
        contract_id="test-json-v1",
        max_bytes=128,
        max_depth=1,
        schema_validator=reject_with_context,
    )

    with pytest.raises(contracts.OutputContractRejected) as caught:
        contract(json.dumps({"title": canary}))

    assert caught.value.diagnostic_code == contracts.JSON_SHAPE_MISMATCH
    assert canary not in str(caught.value)
    assert canary not in repr(caught.value)
    assert caught.value.__cause__ is None
    assert caught.value.__context__ is None


def test_exact_json_contract_hides_canonical_serialization_failure_context():
    class UnsafeCanonicalValue:
        def __repr__(self):
            return "CANONICAL_SERIALIZATION_CANARY"

    contract = contracts.ExactJSONContract(
        contract_id="test-json-v1",
        max_bytes=64,
        max_depth=1,
        schema_validator=lambda _value: UnsafeCanonicalValue(),
    )

    with pytest.raises(contracts.OutputContractRejected) as caught:
        contract("null")

    assert caught.value.diagnostic_code == contracts.INTERNAL_CONTRACT_ERROR
    assert "CANONICAL_SERIALIZATION_CANARY" not in str(caught.value)
    assert "CANONICAL_SERIALIZATION_CANARY" not in repr(caught.value)
    assert caught.value.__cause__ is None
    assert caught.value.__context__ is None


@pytest.mark.parametrize("factory", [
    lambda: contracts.ExactJSONContract(
        contract_id="../unsafe", max_bytes=64, max_depth=1,
        schema_validator=lambda value: value),
    lambda: contracts.ExactJSONContract(
        contract_id="safe-v1", max_bytes=True, max_depth=1,
        schema_validator=lambda value: value),
    lambda: contracts.ExactJSONContract(
        contract_id="safe-v1", max_bytes=0, max_depth=1,
        schema_validator=lambda value: value),
    lambda: contracts.ExactJSONContract(
        contract_id="safe-v1", max_bytes=64, max_depth=True,
        schema_validator=lambda value: value),
    lambda: contracts.ExactJSONContract(
        contract_id="safe-v1", max_bytes=64, max_depth=0,
        schema_validator=lambda value: value),
    lambda: contracts.ExactJSONContract(
        contract_id="safe-v1", max_bytes=64, max_depth=1,
        max_integer_digits=True, schema_validator=lambda value: value),
    lambda: contracts.ExactJSONContract(
        contract_id="safe-v1", max_bytes=64, max_depth=1,
        max_integer_digits=0, schema_validator=lambda value: value),
    lambda: contracts.ExactJSONContract(
        contract_id="safe-v1", max_bytes=64, max_depth=1,
        schema_validator=None),
    lambda: contracts.ExactJSONContract(
        contract_id="safe-v1", max_bytes=64, max_depth=1,
        schema_validator=lambda value: value,
        provenance_fields=(("../unsafe", 1),)),
    lambda: contracts.ExactJSONContract(
        contract_id="safe-v1", max_bytes=64, max_depth=1,
        schema_validator=lambda value: value,
        provenance_fields=(("limit", 1), ("limit", 2))),
    lambda: contracts.ExactJSONContract(
        contract_id="safe-v1", max_bytes=64, max_depth=1,
        schema_validator=lambda value: value,
        provenance_fields=(("contract_id", 1),)),
    lambda: contracts.ExactJSONContract(
        contract_id="safe-v1", max_bytes=64, max_depth=1,
        schema_validator=lambda value: value,
        provenance_fields=(("limit", True),)),
    lambda: contracts.ExactJSONContract(
        contract_id="safe-v1", max_bytes=64, max_depth=1,
        schema_validator=lambda value: value,
        provenance_fields=(("limit", -1),)),
])
def test_trusted_exact_json_contract_declarations_fail_closed(factory):
    with pytest.raises((TypeError, ValueError)):
        factory()


def test_exact_json_contract_provenance_validates_fallback_identifier():
    contract = contracts.ExactJSONContract(
        contract_id="test-json-v1",
        max_bytes=64,
        max_depth=1,
        schema_validator=lambda value: value,
        provenance_fields=(("item_limit", 3),),
    )

    assert contract.provenance(fallback_id="safe-fallback-v1") == {
        "policy_version": contracts.OUTPUT_CONTRACT_POLICY_VERSION,
        "contract_id": "test-json-v1",
        "fallback_id": "safe-fallback-v1",
        "max_bytes": 64,
        "max_depth": 1,
        "max_integer_digits": 64,
        "item_limit": 3,
    }
    with pytest.raises(ValueError, match="fallback ID"):
        contract.provenance(fallback_id="../unsafe")
