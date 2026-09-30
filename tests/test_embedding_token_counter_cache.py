"""Exact embedding token counting and prefix-bounding characterization.

The real-tokenizer tests compare the production counter with a frozen copy
of the per-text counter from main 3aede7d.  They use synthetic legal-style
text only and skip unless the reviewed Nomic token-counter bundle is already
present and byte-verified in the local model-artifact cache.
"""

import copy
import sys
from types import ModuleType

import pytest

import model_artifacts
import rag


NOMIC = rag.DEFAULT_EMBEDDING_MODEL_GENERAL
_WORDS = (
    "plaintiff", "defendant", "negligence", "§ 402A", "¶ 12",
    "Restatement (Second) of Torts", "“duty”", "‘breach’",
    "café", "Müller", "é", "中文", "\U0001F600",
    "​", "\r\n", "\n\n", "<s>", "</s>", "<mask>", "Id.", "U.S.C.",
    "F.3d", "—", " ", "ﬁ", "\t", "held", "court",
)


def _synthetic_text(seed: int, words: int) -> str:
    """Build deterministic synthetic legal-style text without randomness."""
    return " ".join(
        _WORDS[(seed * 7 + index * 13 + index // 5) % len(_WORDS)]
        for index in range(words)
    )


def _synthetic_corpus() -> list[str]:
    sizes = (0, 1, 5, 40, 180, 230, 330, 900)
    texts = [_synthetic_text(seed, sizes[seed % len(sizes)])
             for seed in range(600)]
    return texts + [
        "", " ", "\n\n", "search_document: already prefixed", "<s>",
        "​",
    ]


def _require_verified_token_counter() -> None:
    pytest.importorskip("transformers")
    try:
        spec = model_artifacts.runtime_bundle_spec(NOMIC, "token_counter")
        present = model_artifacts.verify_cached_runtime_bundle(spec)
    except model_artifacts.ModelArtifactError as exc:
        pytest.skip(f"reviewed token-counter bundle is unavailable: {exc}")
    if not present:
        pytest.skip("reviewed token-counter bundle is not cached locally")


def _frozen_per_text_counter(texts, embedding_model):
    """The main 3aede7d success path: load per call, encode text by text."""
    texts = rag._prepare_embedding_inputs(texts, embedding_model, "document")
    from transformers import AutoTokenizer

    model_source, verified = rag._model_loader_source(
        embedding_model, "token_counter")
    assert verified is True
    tokenizer = AutoTokenizer.from_pretrained(
        model_source, trust_remote_code=False, local_files_only=True)
    return [
        len(tokenizer.encode(
            text, add_special_tokens=True, truncation=False))
        for text in texts
    ], True


def _frozen_loaded_counter():
    """The same per-text semantics with one load, for many bounding steps."""
    from transformers import AutoTokenizer

    model_source, verified = rag._model_loader_source(NOMIC, "token_counter")
    assert verified is True
    tokenizer = AutoTokenizer.from_pretrained(
        model_source, trust_remote_code=False, local_files_only=True)

    def count(texts, embedding_model):
        assert embedding_model == NOMIC
        texts = rag._prepare_embedding_inputs(
            texts, embedding_model, "document")
        return [
            len(tokenizer.encode(
                text, add_special_tokens=True, truncation=False))
            for text in texts
        ], True

    return count


def _install_transformers(monkeypatch, tokenizer_class) -> None:
    module = ModuleType("transformers")
    module.AutoTokenizer = tokenizer_class
    monkeypatch.setitem(sys.modules, "transformers", module)


def test_real_exact_counts_match_the_frozen_per_text_counter():
    _require_verified_token_counter()
    texts = _synthetic_corpus()

    expected = _frozen_per_text_counter(texts, NOMIC)

    assert rag._count_embedding_text_tokens(texts, NOMIC) == expected
    assert rag._count_embedding_text_tokens(texts, NOMIC) == expected
    assert rag._count_embedding_text_tokens(texts[:1], NOMIC) == (
        expected[0][:1], True)
    assert rag._count_embedding_text_tokens([], NOMIC) == ([], True)
    # Counting never truncates: long synthetic inputs exceed the model limit.
    assert max(expected[0]) > rag.EMBEDDING_MAX_TOKENS[NOMIC]


def _bounding_records() -> list[dict]:
    return [
        # The body fits, but the retrieval prefix pushes it over 512.
        {"text": _synthetic_text(11, 150),
         "metadata": {"context": _synthetic_text(12, 120)}},
        # The body alone exceeds 512, so the prefix is removed entirely.
        {"text": _synthetic_text(13, 260),
         "metadata": {"context": _synthetic_text(14, 30)}},
        # Body and prefix fit together and stay untouched.
        {"text": _synthetic_text(15, 40),
         "metadata": {"context": _synthetic_text(16, 10)}},
    ]


def test_real_prefix_bounding_matches_the_frozen_per_text_counter(
        monkeypatch):
    _require_verified_token_counter()
    expected_records = _bounding_records()
    actual_records = copy.deepcopy(expected_records)
    frozen = _frozen_loaded_counter()

    with monkeypatch.context() as patch:
        patch.setattr(rag, "_count_embedding_text_tokens", frozen)
        expected_changed = rag._bound_embedding_prefixes_to_model_limit(
            expected_records, NOMIC)
        expected_counts, _ = frozen(
            [rag._embedding_text(record) for record in expected_records],
            NOMIC)

    actual_changed = rag._bound_embedding_prefixes_to_model_limit(
        actual_records, NOMIC)
    actual_counts, exact = rag._count_embedding_text_tokens(
        [rag._embedding_text(record) for record in actual_records], NOMIC)

    assert exact is True
    assert actual_changed == expected_changed == 1
    assert actual_records == expected_records
    assert actual_counts == expected_counts
    limit = rag.EMBEDDING_MAX_TOKENS[NOMIC]
    bounded, oversized, untouched = (
        record["metadata"] for record in actual_records)
    assert bounded["embedding_prefix_truncated"] is True
    assert 0 < bounded["embedding_prefix_char_limit"] < len(
        _bounding_records()[0]["metadata"]["context"])
    assert actual_counts[0] <= limit
    assert oversized["embedding_prefix_char_limit"] == 0
    assert "embedding_prefix_truncated" not in oversized
    assert actual_counts[1] > limit
    assert "embedding_prefix_char_limit" not in untouched
    assert actual_counts[2] <= limit


def test_failed_tokenizer_load_uses_the_conservative_estimate(monkeypatch):
    monkeypatch.setattr(
        rag, "_model_loader_source",
        lambda *_args: ("verified/tokenizer", True))

    class BrokenTokenizer:
        @classmethod
        def from_pretrained(cls, *_args, **_kwargs):
            raise OSError("synthetic tokenizer load failure")

    _install_transformers(monkeypatch, BrokenTokenizer)
    texts = ["§ 402A “duty”", ""]

    counts, exact = rag._count_embedding_text_tokens(texts, NOMIC)

    assert exact is False
    assert counts == [
        rag._conservative_token_estimate(text)
        for text in rag._prepare_embedding_inputs(texts, NOMIC, "document")
    ]
    assert rag._count_embedding_text_tokens([], NOMIC) == ([], False)


def test_empty_input_is_exact_after_a_successful_load(monkeypatch):
    loads = []
    monkeypatch.setattr(
        rag, "_model_loader_source",
        lambda *_args: ("verified/tokenizer", True))

    class FakeTokenizer:
        @classmethod
        def from_pretrained(cls, model_source, **kwargs):
            loads.append((model_source, kwargs))
            return cls()

        def encode(self, _text, **_kwargs):
            raise AssertionError("there is no text to encode")

    _install_transformers(monkeypatch, FakeTokenizer)

    assert rag._count_embedding_text_tokens([], NOMIC) == ([], True)
    assert loads == [(
        "verified/tokenizer",
        {"trust_remote_code": False, "local_files_only": True},
    )]


def test_unverified_development_source_loads_on_every_call(monkeypatch):
    loads = []
    monkeypatch.setattr(
        rag, "_model_loader_source", lambda *_args: ("custom/model", False))

    class FakeTokenizer:
        @classmethod
        def from_pretrained(cls, model_source, **kwargs):
            loads.append((model_source, kwargs))
            return cls()

        def encode(self, _text, **_kwargs):
            return [1, 2, 3]

    _install_transformers(monkeypatch, FakeTokenizer)

    for _ in range(2):
        assert rag._count_embedding_text_tokens(
            ["ordinary"], "custom/model") == ([3], True)
    assert loads == [("custom/model", {"trust_remote_code": True})] * 2


def test_every_call_reverifies_the_token_counter_bundle(monkeypatch):
    outcomes = [
        ("verified/tokenizer", True),
        model_artifacts.ModelArtifactError("synthetic verification failure"),
    ]

    def loader_source(*args):
        assert args == (NOMIC, "token_counter")
        outcome = outcomes.pop(0)
        if isinstance(outcome, Exception):
            raise outcome
        return outcome

    monkeypatch.setattr(rag, "_model_loader_source", loader_source)

    class FakeTokenizer:
        @classmethod
        def from_pretrained(cls, *_args, **_kwargs):
            return cls()

        def encode(self, _text, **_kwargs):
            return [1, 2]

    _install_transformers(monkeypatch, FakeTokenizer)

    assert rag._count_embedding_text_tokens(["text"], NOMIC) == ([2], True)
    counts, exact = rag._count_embedding_text_tokens(["text"], NOMIC)

    assert exact is False
    assert counts == [rag._conservative_token_estimate(
        "search_document: text")]
    assert outcomes == []


def test_api_models_never_resolve_or_load_a_local_tokenizer(monkeypatch):
    def forbidden(*_args, **_kwargs):
        pytest.fail("API token counting resolved a local tokenizer")

    monkeypatch.setattr(rag, "_model_loader_source", forbidden)

    class ForbiddenTokenizer:
        from_pretrained = staticmethod(forbidden)

    _install_transformers(monkeypatch, ForbiddenTokenizer)

    counts, exact = rag._count_embedding_text_tokens(
        ["private legal text"], "voyage-law-2")

    assert exact is False
    assert counts == [rag._conservative_token_estimate("private legal text")]
