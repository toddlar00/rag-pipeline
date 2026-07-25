import subprocess
import sys

import pytest

import embedding_policy


@pytest.mark.parametrize(
    "prefix",
    embedding_policy.SUPPORTED_API_EMBEDDING_MODEL_PREFIXES,
)
def test_supported_api_embedding_families_are_classified_as_remote(prefix):
    assert embedding_policy.is_api_embedding_model(prefix)
    assert embedding_policy.is_api_embedding_model(f"{prefix}model")


@pytest.mark.parametrize(
    "prefix",
    embedding_policy.UNSUPPORTED_API_EMBEDDING_MODEL_PREFIXES,
)
def test_known_but_unsupported_api_families_are_still_remote(prefix):
    assert embedding_policy.is_api_embedding_model(prefix)
    assert embedding_policy.is_api_embedding_model(f"{prefix}model")


@pytest.mark.parametrize(
    "model_name",
    [
        "dunzhang/stella_en_400M_v5",
        "nomic-ai/nomic-embed-text-v2-moe",
        "sentence-transformers/all-MiniLM-L6-v2",
        "local/embed-model",
        "",
    ],
)
def test_local_embedding_names_do_not_match_api_families(model_name):
    assert not embedding_policy.is_api_embedding_model(model_name)


@pytest.mark.parametrize(
    "model_name",
    [
        "voyage",
        "text-embedding",
        "embed",
        "cohere",
        "embo",
        "minimax-em",
        "Voyage-law-2",
        " voyage-law-2",
        "local-voyage-law-2",
        "local/text-embedding-3-large",
    ],
)
def test_api_prefix_matching_has_exact_case_sensitive_boundaries(model_name):
    assert not embedding_policy.is_api_embedding_model(model_name)


def test_api_prefix_tuple_is_exact_ordered_composition_and_immutable():
    expected = (
        *embedding_policy.SUPPORTED_API_EMBEDDING_MODEL_PREFIXES,
        *embedding_policy.UNSUPPORTED_API_EMBEDDING_MODEL_PREFIXES,
    )

    assert embedding_policy.API_EMBEDDING_MODEL_PREFIXES == expected
    assert isinstance(embedding_policy.API_EMBEDDING_MODEL_PREFIXES, tuple)
    with pytest.raises(TypeError):
        embedding_policy.API_EMBEDDING_MODEL_PREFIXES[0] = "changed-"


@pytest.mark.parametrize("model_name", [None, 1, b"voyage-law-2", object()])
def test_embedding_classifier_rejects_non_text_values(model_name):
    with pytest.raises(TypeError, match="embedding model name must be text"):
        embedding_policy.is_api_embedding_model(model_name)


def test_embedding_policy_is_a_standard_library_only_leaf_module():
    result = subprocess.run(
        [
            sys.executable,
            "-I",
            "-c",
            (
                "import sys; sys.path.insert(0, '.'); import embedding_policy; "
                "forbidden = {'rag', 'service_runtime', 'requests', 'torch', "
                "'transformers', 'qdrant_client', 'chromadb'}; "
                "loaded = forbidden.intersection(sys.modules); "
                "assert not loaded, sorted(loaded)"
            ),
        ],
        capture_output=True,
        check=False,
        text=True,
        timeout=30,
    )

    assert result.returncode == 0, result.stderr


def test_rag_legacy_prefix_names_alias_the_shared_policy_objects():
    import rag

    assert rag._SUPPORTED_API_EMBEDDING_MODEL_PREFIXES is (
        embedding_policy.SUPPORTED_API_EMBEDDING_MODEL_PREFIXES)
    assert rag._UNSUPPORTED_API_EMBEDDING_MODEL_PREFIXES is (
        embedding_policy.UNSUPPORTED_API_EMBEDDING_MODEL_PREFIXES)
    assert rag._API_EMBEDDING_MODEL_PREFIXES is (
        embedding_policy.API_EMBEDDING_MODEL_PREFIXES)
