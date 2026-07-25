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
    with pytest.raises(contracts.OutputContractRejected) as caught:
        classification_contract("case_\ud800opinion")

    assert caught.value.diagnostic_code == contracts.INVALID_ENCODING


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
