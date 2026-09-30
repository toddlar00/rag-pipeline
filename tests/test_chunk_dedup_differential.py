"""Differential controls for trigram near-duplicate removal.

``_oracle_deduplicate_chunks`` is a verbatim frozen copy of
``chunking_core._deduplicate_chunks`` at main 3aede7d.  The production
function may compute the Jaccard similarity differently, but it must keep the
same records, emit the same audit stream and policy-callback sequence, report
the same removal count, and raise the same exceptions.  Every corpus here is
synthetic.
"""

from __future__ import annotations

from collections.abc import Callable
from concurrent.futures import ThreadPoolExecutor
import math
import random
from types import SimpleNamespace

import pytest

import chunking_core
from chunking_core import (
    DEDUP_THRESHOLD,
    FingerprintFn,
    RemovedCallback,
    TrigramFn,
    _make_trigrams,
    _text_fingerprint,
)
import rag


def _oracle_deduplicate_chunks(
        chunks: list[dict], threshold: float = DEDUP_THRESHOLD, *,
        text_fingerprint_fn: FingerprintFn | None = None,
        make_trigrams_fn: TrigramFn | None = None,
        removed_callback: RemovedCallback | None = None,
        can_deduplicate_fn: Callable[[dict, dict], bool] | None = None,
        audit_hook: Callable[[str, int, object], object] | None = None,
        ) -> list[dict]:
    """Remove near-duplicate chunks based on trigram Jaccard similarity.

    The optional internal observer returns the exact consumed text for input
    events. Other events carry occurrence ordinals or the final list identity;
    they never expose mutable records.
    """
    if not chunks:
        if audit_hook is not None:
            audit_hook("result", 0, id(chunks))
        return chunks

    fingerprint_fn = (
        _text_fingerprint
        if text_fingerprint_fn is None else text_fingerprint_fn
    )
    trigrams_fn = _make_trigrams if make_trigrams_fn is None else make_trigrams_fn
    kept: list[dict] = []
    seen: list[tuple[int, frozenset[str], dict, int]] = []
    for index, chunk in enumerate(chunks):
        if audit_hook is None:
            fingerprint = fingerprint_fn(chunk["text"])
        else:
            fingerprint = fingerprint_fn(audit_hook("input", index, chunk["text"]))
        fingerprint_length = len(fingerprint)
        trigrams_a = trigrams_fn(fingerprint)
        if not trigrams_a:
            kept.append(chunk)
            if audit_hook is not None:
                audit_hook("keep", index, None)
            continue

        is_duplicate = False
        for seen_length, trigrams_b, seen_chunk, seen_index in seen:
            if (abs(fingerprint_length - seen_length)
                    / max(fingerprint_length, seen_length) > 0.2):
                continue
            if not trigrams_b:
                continue
            jaccard = len(trigrams_a & trigrams_b) / len(trigrams_a | trigrams_b)
            if (jaccard >= threshold
                    and (can_deduplicate_fn is None
                         or can_deduplicate_fn(seen_chunk, chunk))):
                is_duplicate = True
                if audit_hook is not None:
                    audit_hook("remove", index, seen_index)
                break

        if not is_duplicate:
            kept.append(chunk)
            seen.append((fingerprint_length, trigrams_a, chunk, index))
            if audit_hook is not None:
                audit_hook("keep", index, None)

    removed = len(chunks) - len(kept)
    if removed > 0 and removed_callback is not None:
        removed_callback(removed)
    if audit_hook is not None:
        audit_hook("result", len(kept), id(kept))
    return kept


def _identity(text):
    return text


def _observe(dedup, chunks, threshold, trigram_values=None, *,
             fingerprint_fn=_identity, policy=None, audited=True):
    """Run one implementation and return everything a caller can observe."""
    events = []
    result_ids = []
    values = None if trigram_values is None else iter(trigram_values)

    def fingerprint(text):
        value = fingerprint_fn(text)
        events.append(("fingerprint", text, value))
        return value

    def trigrams(value):
        events.append(("trigrams", value))
        return _make_trigrams(value) if values is None else next(values)

    def audit(kind, index, value):
        if kind == "result":
            result_ids.append(value)
            events.append(("audit", kind, index))
        else:
            events.append(("audit", kind, index, value))
        return value

    def can_deduplicate(kept, candidate):
        verdict = policy(kept, candidate)
        events.append(("policy", id(kept), id(candidate), verdict))
        return verdict

    try:
        result = dedup(
            chunks, threshold,
            text_fingerprint_fn=fingerprint,
            make_trigrams_fn=trigrams,
            removed_callback=lambda count: events.append(("removed", count)),
            can_deduplicate_fn=None if policy is None else can_deduplicate,
            audit_hook=audit if audited else None,
        )
    except Exception as exc:  # the raised type and message are compared too
        return ("raised", type(exc), str(exc), events)
    return ("returned", [id(chunk) for chunk in result], result is chunks,
            result_ids == ([id(result)] if audited else []), events)


def _assert_same(chunks, threshold, trigram_values=None, **kwargs):
    expected = _observe(_oracle_deduplicate_chunks, chunks, threshold,
                        trigram_values, **kwargs)
    actual = _observe(chunking_core._deduplicate_chunks, chunks, threshold,
                      trigram_values, **kwargs)
    assert actual == expected
    return expected


class _TrigramSet(frozenset):
    """A frozenset subclass must keep the original set-algebra dispatch."""


class _Gram(str):
    """A str subclass member must keep the original set-algebra dispatch."""


_THRESHOLDS = (
    0.95, math.nextafter(0.95, 1.0), math.nextafter(0.95, 0.0), 0.8, 0.5,
    1.0, 1, 0, 0.0, -1.0, 2.0, math.inf, math.nan,
)


def _random_policy(rng):
    mode = rng.randrange(4)
    if mode == 0:
        return None
    if mode == 1:
        return lambda kept, candidate: True
    if mode == 2:
        return lambda kept, candidate: False
    salt = rng.randrange(1 << 30)
    return lambda kept, candidate: random.Random(
        salt + 7919 * kept["metadata"]["ordinal"]
        + candidate["metadata"]["ordinal"]).random() < 0.5


def _random_case(rng):
    universe = [f"g{position:03d}"
                for position in range(rng.choice((4, 12, 40, 160)))]
    bases = [
        frozenset(rng.sample(universe, rng.randint(0, min(len(universe), 40))))
        for _ in range(rng.randint(1, 6))
    ]
    chunks, values = [], []
    for ordinal in range(rng.randint(0, 40)):
        grams = set(rng.choice(bases))
        for _ in range(rng.choice((0, 0, 1, 1, 2, 3))):
            if grams and rng.random() < 0.5:
                grams.discard(rng.choice(sorted(grams)))
            else:
                grams.add(rng.choice(universe))
        roll = rng.random()
        if roll < 0.03:
            value = set(grams)
        elif roll < 0.06:
            value = _TrigramSet(grams)
        else:
            value = frozenset(grams)
        length = (rng.randint(0, 4) if rng.random() < 0.03
                  else rng.choice((3, 50, 52, 55, 60, 61, 70, 100)))
        chunks.append({"text": "x" * length, "metadata": {"ordinal": ordinal}})
        values.append(value)
    if chunks and rng.random() < 0.2:
        position = rng.randrange(len(chunks))
        chunks.append(chunks[position])
        values.append(values[position])
    return chunks, values


@pytest.mark.parametrize("seed", range(24))
def test_seeded_synthetic_corpora_match_the_oracle(seed):
    rng = random.Random(seed)
    outcomes = set()
    for _ in range(100):
        chunks, values = _random_case(rng)
        threshold = rng.choice(_THRESHOLDS)
        policy = _random_policy(rng)
        for audited in (True, False):
            outcome = _assert_same(chunks, threshold, values,
                                   policy=policy, audited=audited)
        outcomes.add(outcome[0])
    assert "returned" in outcomes


_GRAMS = [f"b{position:02d}" for position in range(24)]


def _grams(*positions):
    return frozenset(_GRAMS[position] for position in positions)


_BASE = _grams(*range(20))


@pytest.mark.parametrize(("second", "threshold", "removed"), [
    # 19/20 is exactly the default threshold and the exact size ratio.
    (_grams(*range(19)), 0.95, True),
    (_grams(*range(19)), 19 / 20, True),
    (_grams(*range(19)), math.nextafter(0.95, 1.0), False),
    (_grams(*range(21)), 0.95, True),
    (_grams(*range(19), 20), 0.95, False),
    (_grams(*range(19), 20), 0.9, True),
    (_grams(*range(18), 20), 0.95, False),
    (_grams(*range(18), 20), 0.85, True),
    (_BASE, 1.0, True),
    (_BASE, 1, True),
    (_grams(*range(21)), 1.0, False),
    (_grams(*range(21)), 1, False),
    (_grams(20, 21, 22), 0, True),
    (_grams(20, 21, 22), 0.0, True),
    (_grams(20, 21, 22), -1.0, True),
    (_BASE, math.nan, False),
    (_BASE, 2.0, False),
    (_BASE, math.inf, False),
])
def test_exact_fraction_boundaries_match_golden_values(second, threshold, removed):
    chunks = [{"text": "x" * 60}, {"text": "x" * 60}]
    for audited in (True, False):
        outcome = _assert_same(chunks, threshold, [_BASE, second],
                               audited=audited)
        assert outcome[0] == "returned"
        assert outcome[1] == [id(chunk) for chunk in chunks[:1 if removed else 2]]


@pytest.mark.parametrize("make", [
    set, list, tuple, _TrigramSet,
    lambda grams: {gram: None for gram in grams}.keys(),
    lambda grams: frozenset(_Gram(gram) for gram in grams),
    lambda grams: frozenset(len(gram) + ordinal for ordinal, gram in enumerate(grams)),
    lambda grams: frozenset([1, 1.0, *grams]),
])
@pytest.mark.parametrize("threshold", [0.95, 0.5, "0.9"])
@pytest.mark.parametrize("mixed", [False, True])
def test_out_of_contract_trigram_values_keep_original_behavior(make, threshold, mixed):
    base = ["abc", "bcd", "cde", "def"]
    values = [make(base), make(base[:3]), make(base[1:]), make(base)]
    if mixed:
        values[1::2] = [frozenset(base), frozenset(base[:3])]
    chunks = [{"text": "x" * 40} for _ in values]
    for audited in (True, False):
        _assert_same(chunks, threshold, values, audited=audited)


@pytest.mark.parametrize("lengths", [(0, 0), (0, 5), (5, 0), (0, 0, 0)])
def test_zero_length_fingerprints_keep_the_original_error_order(lengths):
    chunks = [{"text": "x" * length} for length in lengths]
    values = [frozenset({"abc", "bcd"}) for _ in lengths]
    for audited in (True, False):
        _assert_same(chunks, 0.95, values, audited=audited)
    if lengths == (0, 0):
        outcome = _assert_same(chunks, 0.95, values)
        assert outcome[:2] == ("raised", ZeroDivisionError)


def test_non_builtin_trigrams_and_threshold_keep_dunder_dispatch():
    def run(dedup):
        calls = []

        class Gram(str):
            def __hash__(self):
                calls.append(("hash", str.__str__(self)))
                return str.__hash__(self)

            def __eq__(self, other):
                calls.append(("eq", str.__str__(self)))
                return str.__eq__(self, other)

        class Grams(frozenset):
            def __bool__(self):
                calls.append(("bool", frozenset.__len__(self)))
                return frozenset.__len__(self) != 0

            def __len__(self):
                calls.append(("len", frozenset.__len__(self)))
                return frozenset.__len__(self)

            def __iter__(self):
                calls.append("iter")
                return frozenset.__iter__(self)

            def __and__(self, other):
                calls.append("and")
                return Grams(frozenset.__and__(self, other))

            def __rand__(self, other):
                calls.append("rand")
                return Grams(frozenset.__and__(self, other))

            def __or__(self, other):
                calls.append("or")
                return Grams(frozenset.__or__(self, other))

            def __ror__(self, other):
                calls.append("ror")
                return Grams(frozenset.__or__(self, other))

        class Threshold(float):
            def __lt__(self, other):
                calls.append("lt")
                return float.__lt__(self, other)

            def __le__(self, other):
                calls.append("le")
                return float.__le__(self, other)

            def __gt__(self, other):
                calls.append("gt")
                return float.__gt__(self, other)

            def __ge__(self, other):
                calls.append("ge")
                return float.__ge__(self, other)

        plain = [Gram(text) for text in ("aa", "bb", "cc", "dd")]
        values = iter([
            Grams(plain[:3]), frozenset(plain), Grams(plain[:3]),
            frozenset({"aa", "bb"}), Grams({"x"}), frozenset({"aa", "bb", "cc"}),
            frozenset({"aa", "bb", "cc"}), frozenset(plain[:3]),
        ])
        calls.clear()
        chunks = [{"text": "xxxxx"} for _ in range(8)]
        result = dedup(chunks, Threshold(0.7), text_fingerprint_fn=_identity,
                       make_trigrams_fn=lambda value: next(values))
        positions = {id(chunk): index for index, chunk in enumerate(chunks)}
        return [positions[id(chunk)] for chunk in result], calls

    expected = run(_oracle_deduplicate_chunks)
    assert run(chunking_core._deduplicate_chunks) == expected
    assert expected[1]


def _synthetic_text_corpus(seed, count):
    rng = random.Random(seed)
    vocabulary = ["".join(rng.choices("etaoinshrdlucmfwyp", k=rng.randint(2, 9)))
                  for _ in range(400)]
    chunks = []
    for ordinal in range(count):
        roll = rng.random()
        ref = f"#/texts/{ordinal}"
        if chunks and roll < 0.4:
            source = rng.choice(chunks)
            if rng.random() < 0.5:
                ref = source["metadata"]["source_items"][0]["ref"]
            words = source["text"].split()
            if roll >= 0.15:
                for _ in range(rng.choice((1, 1, 2, 4))):
                    words[rng.randrange(len(words))] = rng.choice(vocabulary)
            text = " ".join(words)
        else:
            text = " ".join(rng.choices(vocabulary, k=rng.randint(30, 120)))
        chunks.append({"text": text, "metadata": {
            "ordinal": ordinal, "source_items": [{"ref": ref}]}})
    return chunks


def _same_text(kept, candidate):
    return kept["text"] == candidate["text"]


@pytest.mark.parametrize("seed", range(3))
@pytest.mark.parametrize("threshold", [0.95, 0.8])
@pytest.mark.parametrize("policy", [None, _same_text])
def test_default_helpers_on_synthetic_text_match_the_oracle(seed, threshold, policy):
    chunks = _synthetic_text_corpus(seed, 100)
    outcome = _assert_same(chunks, threshold, fingerprint_fn=_text_fingerprint,
                           policy=policy, audited=bool(seed % 2))
    assert outcome[0] == "returned" and len(outcome[1]) < len(chunks)


def test_rag_facade_policy_matches_the_oracle(monkeypatch):
    assert rag._CHUNK_DEDUP_CORE is chunking_core._deduplicate_chunks
    captured = {}

    def capture(chunks, threshold, **kwargs):
        captured.update(kwargs)
        return chunks

    with monkeypatch.context() as patch:
        patch.setattr(rag, "_chunking_core",
                      SimpleNamespace(_deduplicate_chunks=capture))
        rag._deduplicate_chunks([{"text": "synthetic"}])
    policy = captured["can_deduplicate_fn"]
    corpus = _synthetic_text_corpus(11, 100)
    outcome = _assert_same(corpus, rag.DEDUP_THRESHOLD,
                           fingerprint_fn=captured["text_fingerprint_fn"],
                           policy=policy)
    assert outcome[0] == "returned" and len(outcome[1]) < len(corpus)
    assert [id(chunk) for chunk in rag._deduplicate_chunks(corpus)] == outcome[1]


def test_concurrent_calls_match_the_oracle():
    corpora = [_synthetic_text_corpus(seed, 60) for seed in range(20, 26)]
    expected = [[id(chunk) for chunk in _oracle_deduplicate_chunks(corpus, 0.8)]
                for corpus in corpora]
    with ThreadPoolExecutor(max_workers=3) as pool:
        results = list(pool.map(
            lambda corpus: chunking_core._deduplicate_chunks(corpus, 0.8), corpora))
    assert [[id(chunk) for chunk in result] for result in results] == expected
