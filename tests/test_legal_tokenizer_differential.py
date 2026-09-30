"""Differential oracle for the legal lexical analyzer in ``retrieval_core``.

``_normalize_legal_search_text`` and ``_legal_search_tokens`` produce the
terms stored in every BM25 corpus and Qdrant sparse vector and used by the
offline evaluator. The oracle below is a frozen copy of the analyzer at
3aede7d, before any pass was guarded or precompiled. Performance work on the
analyzer must return exactly the oracle's normalized text and token list for
every input.
"""

import functools
import json
import random
import re
import unicodedata
from pathlib import Path

import pytest

import offline_retrieval
import rag
import retrieval_core


PROJECT_ROOT = Path(__file__).resolve().parents[1]
EVALUATION_SUITES = PROJECT_ROOT / "evaluation" / "suites"

# Inline pattern sources of the analyzer at 3aede7d.
ORACLE_HYPHEN_WRAP = r"(?<=[a-z])-[ \t]*\r?\n[ \t]*(?=[a-z])"
ORACLE_TITLE_USC = r"\btitle\s+(\d+)\s+of\s+the\s+united\s+states\s+code\b"
ORACLE_THOUSANDS = r"(?<=\d),(?=\d{3}(?:\D|$))"
ORACLE_SUBSECTION_PART = r"[a-z0-9]+"
ORACLE_USD = r"\$\s*(\d+(?:\.\d+)?)"


def oracle_normalize(text):
    """Frozen copy of the unguarded ``_normalize_legal_search_text``."""
    normalized = unicodedata.normalize("NFKC", str(text)).casefold()
    normalized = normalized.replace("\u00ad", "")
    normalized = re.sub(ORACLE_HYPHEN_WRAP, "", normalized)
    normalized = normalized.replace("\u00a7\u00a7", " sections ").replace(
        "\u00a7", " section ")
    normalized = re.sub(ORACLE_TITLE_USC, r"\1 usc", normalized)
    for pattern, replacement in retrieval_core._LEGAL_SEARCH_ALIASES:
        normalized = pattern.sub(replacement, normalized)
    normalized = re.sub(ORACLE_THOUSANDS, "", normalized)
    return normalized


def oracle_tokens(text):
    """Frozen copy of the unguarded ``_legal_search_tokens``."""
    normalized = oracle_normalize(text)
    canonical = []
    for match in retrieval_core._LEGAL_SUBSECTION_RE.finditer(normalized):
        subsections = re.findall(ORACLE_SUBSECTION_PART, match.group(2))
        canonical.append(
            match.group(1) + "".join(f"({part})" for part in subsections))
    canonical.extend(
        f"{volume}_{reporter}_{page}"
        for volume, reporter, page
        in retrieval_core._LEGAL_CITATION_RE.findall(normalized)
    )
    canonical.extend(
        f"usd_{amount.replace('.', '_')}"
        for amount in re.findall(ORACLE_USD, normalized)
    )
    return retrieval_core._LEGAL_WORD_RE.findall(normalized) + canonical


# Line-wrap hyphen joins and soft-hyphen removal run before the other passes,
# so they can create a literal that the raw text does not contain.
_SPLIT_LITERALS = (
    "federal", "rules", "civ", "ct", "supp", "3d", "2d", "title", "united",
    "usc", "code",
)
_FUZZ_ATOMS = (
    # Citation and abbreviation forms.
    "U.S.C.", "U. S. C.", "u.s.c", "USC", "U.S.", "us", "usa", "bus",
    "(u.s.)", "us,", "S. Ct.", "S.Ct.", "s ct", "sct", "Ct", "ct", "F. Supp.",
    "F. Supp. 2d", "F.Supp.3d", "SUPP", "supp", "F.3d", "F.2d", "f3d", "2d.",
    "3d)", "3d", "2d", "Fed. R. Civ. P.", "fed r civ p", "fed", "civ", "CIV",
    "federal", "Federal Rules of Civil Procedure",
    "federal rule of civil procedure", "rule", "rules",
    "Title 28 of the United States Code", "title", "TITLE", "united",
    "states", "code", "of", "the", "\u00a7", "\u00a7\u00a7", "12(b)(6)",
    "12 ( b ) ( 6 )", "(a)", "(", ")", "326", "310", "1,000", "75,000",
    "1,000,000", ",", "$", "$ 1.5", "$75,000", "9", "u", "U", "s", "S", "c",
    "C", "t", "f", "F", ".", ". ", "x", "'", "o'brien", "a-\nb",
    # Whitespace, line breaks, and hyphens.
    " ", "  ", "\n", "\r\n", "\r", "\t", "\x0b", "\x0c", "\u2028",
    "\u00a0", "\u3000", "-", "-\n", "- \n ", "-\t\r\n\t", "\u00ad",
    "\u200b", "\u200d", "\u2010", "\u2011", "\ufe63", "\uff0d",
    # Compatibility forms that NFKC rewrites.
    "\uff08", "\uff09", "\uff04", "\uff0c", "\uff15", "\uff13\uff44",
    "\uff46", "\uff35\uff0e\uff33\uff0e", "\ufb01", "\ufb00", "\ufb05",
    "\ufb06", "\u017f", "\u2162", "\u33a0", "\u2121", "\u00bd", "\u00b2",
    # Characters whose case folding changes length or script.
    "\u00df", "\u1e9e", "\u0130", "\u0131", "\u212a", "\u212b", "\u01c5",
    "\u0345", "\u0390",
    # Unicode word characters directly before u and s.
    "\u00e9", "\u00fc", "\u017e", "\u0663", "_", "\u0301", "\u00e9u.s.",
    "\u0663s. ct.", "_u.s.c.", "\u00b2us",
)


# Whole matches of the guarded passes, split below at every position.
_MATCH_TEMPLATES = (
    "Federal Rules of Civil Procedure", "Fed. R. Civ. P.", "U.S.C.", "U.S.",
    "S. Ct.", "F. Supp. 3d", "F. Supp. 2d", "F. Supp.", "F.3d", "F.2d",
    "Title 28 of the United States Code", "\u00a7 12", "12(b)(6)",
    "$75,000", "326 U.S. 310",
)


def _split_literal_atoms():
    atoms = []
    for literal in _SPLIT_LITERALS:
        for index in range(1, len(literal)):
            head, tail = literal[:index], literal[index:]
            atoms.extend(
                head + joiner + tail
                for joiner in ("-\n", "- \r\n\t", "\u00ad"))
            atoms.append(head.upper() + "-\n" + tail.upper())
    return tuple(atoms)


def _split_template_texts(rng, atoms):
    """Every template split once by a soft hyphen or line-wrap hyphen."""
    texts = []
    for template in _MATCH_TEMPLATES:
        for index in range(1, len(template)):
            head, tail = template[:index], template[index:]
            joiners = ["\u00ad"]
            if head[-1].isalpha() and tail[0].isalpha():
                joiners.extend(("-\n", "- \r\n\t"))
            for joiner in joiners:
                prefix = "".join(
                    rng.choice(atoms) for _ in range(rng.randint(0, 3)))
                suffix = "".join(
                    rng.choice(atoms) for _ in range(rng.randint(0, 3)))
                texts.append(f"{prefix} {head}{joiner}{tail} {suffix}")
    return texts


def _respaced_template_texts(rng):
    """Templates with every space replaced by other whitespace, re-cased."""
    whitespace = (
        " ", "  ", "", "\n", "\t", "\r\n", "\x0b", "\x0c", "\u00a0", "\u3000",
        "\u2028")
    texts = []
    for template in _MATCH_TEMPLATES:
        for _ in range(20):
            text = "".join(
                rng.choice(whitespace) if char == " " else char
                for char in template)
            texts.append(rng.choice((str.lower, str.upper, str.title))(text))
    return texts


@functools.cache
def fuzz_texts():
    """Seeded adversarial strings shared by every property in this module."""
    rng = random.Random(20260930)
    atoms = _FUZZ_ATOMS + _split_literal_atoms()
    texts = _split_template_texts(rng, atoms)
    texts.extend(_respaced_template_texts(rng))
    texts.extend(
        "".join(rng.choice(atoms) for _ in range(rng.randint(1, 40)))
        for _ in range(16_000)
    )
    texts.extend(
        "".join(rng.choice(atoms) for _ in range(rng.randint(40, 200)))
        for _ in range(1_000)
    )
    # Arbitrary code points, including astral ones, exercise Unicode word
    # boundaries around the literals the passes look for.
    pool = [chr(code) for code in range(0x20, 0x250)]
    pool.extend(chr(rng.randrange(0x250, 0x30000)) for _ in range(2_000))
    pool = [char for char in pool if not 0xD800 <= ord(char) <= 0xDFFF]
    hot = "usctfd23.-\n ,($\u00a7"
    texts.extend(
        "".join(
            rng.choice(hot) if rng.random() < 0.6 else rng.choice(pool)
            for _ in range(rng.randint(1, 30)))
        for _ in range(4_000)
    )
    return tuple(texts)


@functools.cache
def fuzz_pattern_inputs():
    """Raw and NFKC-casefolded fuzz strings for per-pattern properties."""
    folded = (
        unicodedata.normalize("NFKC", text).casefold()
        for text in fuzz_texts())
    return fuzz_texts() + tuple(folded)


def suite_texts():
    """Chunk, lexical-document, and query text of the CC0 evaluation suites."""
    texts = []
    for suite in sorted(path for path in EVALUATION_SUITES.iterdir()
                        if path.is_dir()):
        for line in (suite / "chunks.jsonl").read_text(
                encoding="utf-8").splitlines():
            if line.strip():
                record = json.loads(line)
                texts.append(record["text"])
                texts.append(retrieval_core._lexical_document_text(
                    record["text"], record.get("metadata")))
        for line in (suite / "queries.jsonl").read_text(
                encoding="utf-8").splitlines():
            if line.strip():
                texts.append(json.loads(line)["query"])
    return texts


def _analyzer_mismatches(texts):
    return [
        ascii(text) for text in texts
        if retrieval_core._normalize_legal_search_text(text)
        != oracle_normalize(text)
        or retrieval_core._legal_search_tokens(text) != oracle_tokens(text)
    ]


@pytest.mark.parametrize(
    ("text", "normalized", "tokens"),
    [
        (
            "28 U.S.C. \u00a7 1332(a)(1)",
            "28  usc   section  1332(a)(1)",
            ["28", "usc", "section", "1332", "a", "1", "1332(a)(1)"],
        ),
        (
            "Fed. R. Civ. P. 12(b)(6)",
            " rule  12(b)(6)",
            ["rule", "12", "b", "6", "12(b)(6)"],
        ),
        (
            "Federal Rules of Civil Procedure 56",
            " rule  56",
            ["rule", "56"],
        ),
        ("Title 28 of the United States Code", "28  usc ", ["28", "usc"]),
        (
            "326 U.S. 310; 123 S. Ct. 456; 45 F.3d 67",
            "326  us  310; 123  sct  456; 45  f3d  67",
            [
                "326", "us", "310", "123", "sct", "456", "45", "f", "3", "d",
                "67", "326_us_310", "123_sct_456", "45_f3d_67",
            ],
        ),
        (
            "$75,000 and $ 1,250.50",
            "$75000 and $ 1250.50",
            ["75000", "and", "1250.50", "usd_75000", "usd_1250_50"],
        ),
        # Line-wrap and soft hyphens create the literal a later pass needs.
        ("Fed-\neral Rules of Civil Procedure", " rule ", ["rule"]),
        (
            "Fed. R. Ci-\nv. P. 12(b)(6)",
            " rule  12(b)(6)",
            ["rule", "12", "b", "6", "12(b)(6)"],
        ),
        ("540 S. C-\nt. 1", "540  sct  1", ["540", "sct", "1", "540_sct_1"]),
        (
            "512 F. Supp. 2d 9 and 88 F. Su\u00adpp. 3d 10",
            "512  fsupp2d  9 and 88  fsupp3d  10",
            [
                "512", "fsupp", "2", "d", "9", "and", "88", "fsupp", "3", "d",
                "10", "512_fsupp2d_9", "88_fsupp3d_10",
            ],
        ),
        ("Tit-\nle 28 of the Uni-\nted States Code", "28  usc ", ["28", "usc"]),
        # Compatibility forms fold to the ASCII literals.
        (
            "\uff04\uff17\uff15\uff0c\uff10\uff10\uff10",
            "$75000",
            ["75000", "usd_75000"],
        ),
        # A Unicode word character before u or s is not a word boundary.
        (
            "bus US \u00e9us _us \u0663us",
            "bus  us  \u00e9us _us \u0663us",
            ["bus", "us", "us", "us", "\u0663", "us"],
        ),
    ],
)
def test_analyzer_matches_characterized_legal_forms(text, normalized, tokens):
    assert retrieval_core._normalize_legal_search_text(text) == normalized
    assert retrieval_core._legal_search_tokens(text) == tokens
    assert oracle_normalize(text) == normalized
    assert oracle_tokens(text) == tokens


def test_analyzer_matches_frozen_oracle_on_seeded_adversarial_text():
    assert _analyzer_mismatches(fuzz_texts())[:3] == []


def test_analyzer_matches_frozen_oracle_on_cc0_evaluation_suites():
    texts = suite_texts()

    assert len(texts) > 50
    assert _analyzer_mismatches(texts) == []


def _oracle_passes():
    """The oracle normalizer's passes after NFKC and casefolding, in order."""
    passes = [
        ("soft_hyphen", lambda text: text.replace("\u00ad", "")),
        ("hyphen_wrap", lambda text: re.sub(ORACLE_HYPHEN_WRAP, "", text)),
        ("section", lambda text: text.replace(
            "\u00a7\u00a7", " sections ").replace("\u00a7", " section ")),
        ("title_usc",
         lambda text: re.sub(ORACLE_TITLE_USC, r"\1 usc", text)),
    ]
    passes.extend(
        (f"alias_{index}", functools.partial(pattern.sub, replacement))
        for index, (pattern, replacement)
        in enumerate(retrieval_core._LEGAL_SEARCH_ALIASES)
    )
    passes.append(
        ("thousands", lambda text: re.sub(ORACLE_THOUSANDS, "", text)))
    return passes


def _required_literal_cases():
    """Each guarded pass's canonical and executed pattern with its guard.

    The inline guards mirror the literals tested in the analyzer's body.
    """
    pairs = []
    for index, ((canonical, _), (guard, executed, _)) in enumerate(zip(
            retrieval_core._LEGAL_SEARCH_ALIASES,
            retrieval_core._LEGAL_ALIAS_PLAN, strict=True)):
        if guard is not None:
            pairs.append((f"alias_{index}", canonical, executed, guard))
    pairs.extend([
        ("hyphen_wrap", re.compile(ORACLE_HYPHEN_WRAP),
         retrieval_core._LEGAL_HYPHEN_WRAP_RE, "-"),
        ("title_usc", re.compile(ORACLE_TITLE_USC),
         retrieval_core._LEGAL_TITLE_USC_RE, "united"),
        ("thousands", re.compile(ORACLE_THOUSANDS),
         retrieval_core._LEGAL_THOUSANDS_RE, ","),
        ("subsection", retrieval_core._LEGAL_SUBSECTION_RE,
         retrieval_core._LEGAL_SUBSECTION_RE, "("),
        ("usd", re.compile(ORACLE_USD), retrieval_core._LEGAL_USD_RE, "$"),
    ])
    cases = []
    for name, canonical, executed, guard in pairs:
        cases.append((name, "canonical", canonical.pattern, guard))
        if executed.pattern != canonical.pattern:
            cases.append((name, "executed", executed.pattern, guard))
    return cases


def test_seeded_fuzz_exercises_every_oracle_pass():
    passes = _oracle_passes()
    required = {}
    for name, _, _, literal in _required_literal_cases():
        required.setdefault(name, []).append(literal)
    changed = dict.fromkeys((name for name, _ in passes), 0)
    # Changes made although a required literal was absent from the folded
    # input, so an earlier pass must have created it.
    created = dict.fromkeys(
        (name for name, literals in required.items()
         if any(literal.isalpha() for literal in literals)), 0)
    diverged = 0
    for original in fuzz_texts():
        folded = unicodedata.normalize("NFKC", original).casefold()
        text = folded
        for name, apply in passes:
            updated = apply(text)
            if updated != text:
                changed[name] += 1
                if name in created and any(
                        literal not in folded for literal in required[name]):
                    created[name] += 1
            text = updated
        diverged += text != oracle_normalize(original)

    assert diverged == 0
    assert {name: count for name, count in changed.items() if count < 20} == {}
    assert {name: count for name, count in created.items() if count < 5} == {}


@pytest.mark.parametrize(
    ("name", "source", "literal"),
    [
        pytest.param(
            name, source, literal, id=f"{name}-{role}-{ascii(literal)}")
        for name, role, source, literal in _required_literal_cases()
    ],
)
def test_every_match_of_a_guarded_pass_contains_its_guard_literal(
        name, source, literal):
    pattern = re.compile(source)
    matches = [
        match.group(0) for text in fuzz_pattern_inputs()
        for match in pattern.finditer(text)
    ]

    assert len(matches) >= 20, name
    assert [ascii(match) for match in matches if literal not in match] == []


def _word_literal_first(source):
    """Rewrite a leading ``\\bX`` as ``X(?<!\\wX)`` for one word character X.

    ``\\b`` before a word character holds exactly when no word character
    precedes it, and a lookbehind cannot match before the start of the
    string, so the negative lookbehind accepts that position too.
    """
    match = re.fullmatch(r"\\b(\w)(?![*+?{])(.*)", source, re.DOTALL)
    assert match is not None, source
    char, rest = match.groups()
    return char + r"(?<!\w" + char + ")" + rest


def _lookbehind_literal_first(source):
    """Rewrite a leading ``(?<=B)X`` as ``X(?<=BX)`` for one literal X."""
    match = re.fullmatch(
        r"\(\?<=([^()]+)\)([^\\.^$*+?{}\[\]|()])(?![*+?{])(.*)",
        source, re.DOTALL)
    assert match is not None, source
    behind, char, rest = match.groups()
    return char + "(?<=" + behind + char + ")" + rest


def test_literal_first_transforms_rewrite_only_the_leading_assertion():
    assert _word_literal_first(r"\bs\.?\s*ct\.?(?=\W|$)") == (
        r"s(?<!\ws)\.?\s*ct\.?(?=\W|$)")
    assert _lookbehind_literal_first(ORACLE_THOUSANDS) == (
        r",(?<=\d,)(?=\d{3}(?:\D|$))")
    for source in (r"\bu?s", r"\b\d+", r"(?<=a)b*", r"(?<=a)\.x"):
        with pytest.raises(AssertionError):
            (_word_literal_first if source.startswith("\\b")
             else _lookbehind_literal_first)(source)


def test_alias_plan_follows_the_canonical_aliases_in_order():
    aliases = retrieval_core._LEGAL_SEARCH_ALIASES
    guards = retrieval_core._LEGAL_ALIAS_GUARDS
    plan = retrieval_core._LEGAL_ALIAS_PLAN

    assert len(plan) == len(aliases) == len(guards)
    rewritten = []
    for index, ((guard, pattern, replacement), (canonical, expected),
                declared) in enumerate(zip(plan, aliases, guards, strict=True)):
        assert guard == declared
        assert replacement is expected
        if pattern is not canonical:
            # A literal-first entry must be the mechanical rewrite of the
            # canonical source, so editing a canonical alias fails here.
            assert pattern.pattern == _word_literal_first(canonical.pattern)
            assert pattern.flags == canonical.flags
            rewritten.append(index)

    assert rewritten == [2, 3, 4]


@pytest.mark.parametrize(
    ("compiled", "source", "transform"),
    [
        pytest.param(
            retrieval_core._LEGAL_HYPHEN_WRAP_RE, ORACLE_HYPHEN_WRAP,
            _lookbehind_literal_first, id="hyphen_wrap"),
        pytest.param(
            retrieval_core._LEGAL_TITLE_USC_RE, ORACLE_TITLE_USC, None,
            id="title_usc"),
        pytest.param(
            retrieval_core._LEGAL_THOUSANDS_RE, ORACLE_THOUSANDS,
            _lookbehind_literal_first, id="thousands"),
        pytest.param(
            retrieval_core._LEGAL_SUBSECTION_PART_RE, ORACLE_SUBSECTION_PART,
            None, id="subsection_part"),
        pytest.param(
            retrieval_core._LEGAL_USD_RE, ORACLE_USD, None, id="usd"),
    ],
)
def test_precompiled_inline_patterns_follow_their_oracle_source(
        compiled, source, transform):
    expected = source if transform is None else transform(source)

    assert compiled.pattern == expected
    assert compiled.flags == re.compile(source).flags


def _literal_first_pairs():
    """Canonical and literal-first forms of every rewritten pass."""
    pairs = [
        (f"alias_{index}", canonical, executed)
        for index, ((canonical, _), (_, executed, _)) in enumerate(zip(
            retrieval_core._LEGAL_SEARCH_ALIASES,
            retrieval_core._LEGAL_ALIAS_PLAN, strict=True))
        if executed is not canonical
    ]
    pairs.append((
        "hyphen_wrap", re.compile(ORACLE_HYPHEN_WRAP),
        retrieval_core._LEGAL_HYPHEN_WRAP_RE))
    pairs.append((
        "thousands", re.compile(ORACLE_THOUSANDS),
        retrieval_core._LEGAL_THOUSANDS_RE))
    return pairs


def _spans(pattern, text):
    return [match.span() for match in pattern.finditer(text)]


@pytest.mark.parametrize(
    ("name", "canonical", "executed"),
    [pytest.param(*pair, id=pair[0]) for pair in _literal_first_pairs()],
)
def test_literal_first_pattern_matches_the_canonical_spans(
        name, canonical, executed):
    texts = fuzz_pattern_inputs()
    differing = [
        ascii(text) for text in texts
        if _spans(canonical, text) != _spans(executed, text)
    ]

    assert sum(1 for text in texts if canonical.search(text)) >= 20, name
    assert differing[:3] == []


# One match of each rewritten pass, preceded below by every code point.
_LITERAL_FIRST_PROBES = {
    "alias_2": "u.s.c.",
    "alias_3": "u.s.",
    "alias_4": "s.ct.",
    "hyphen_wrap": "-\nb",
    "thousands": ",000",
}


def test_literal_first_pattern_agrees_after_every_preceding_code_point():
    pairs = _literal_first_pairs()
    code_points = [
        code for code in range(0x10000) if not 0xD800 <= code <= 0xDFFF]
    code_points.extend(range(0x10000, 0x110000, 97))

    assert sorted(name for name, _, _ in pairs) == sorted(
        _LITERAL_FIRST_PROBES)
    for name, canonical, executed in pairs:
        probe = _LITERAL_FIRST_PROBES[name]
        text = "".join(chr(code) + probe + " " for code in code_points)
        spans = _spans(canonical, text)

        assert len(spans) >= 10, name
        assert spans == _spans(executed, text), name
        assert _spans(canonical, probe) == _spans(executed, probe), name


def test_analyzer_names_stay_shared_by_rag_and_offline_retrieval():
    aliases = retrieval_core._LEGAL_SEARCH_ALIASES

    assert rag._LEGAL_SEARCH_ALIASES is aliases
    assert rag._LEGAL_WORD_RE is retrieval_core._LEGAL_WORD_RE
    assert rag._LEGAL_SUBSECTION_RE is retrieval_core._LEGAL_SUBSECTION_RE
    assert rag._LEGAL_CITATION_RE is retrieval_core._LEGAL_CITATION_RE
    assert (rag._normalize_legal_search_text
            is retrieval_core._normalize_legal_search_text)
    assert rag._legal_search_tokens is retrieval_core._legal_search_tokens
    assert (offline_retrieval._legal_search_tokens
            is retrieval_core._legal_search_tokens)
    assert isinstance(aliases, tuple)
    assert len(aliases) == 10
    assert all(
        isinstance(pattern, re.Pattern) and isinstance(replacement, str)
        for pattern, replacement in aliases)
