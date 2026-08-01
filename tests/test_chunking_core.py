import subprocess
import sys

import pytest

import chunking_core
import rag


def test_chunking_core_is_a_lightweight_leaf_module():
    result = subprocess.run(
        [
            sys.executable,
            "-c",
            (
                "import sys; import chunking_core; "
                "forbidden = {'rag', 'artifact_io', 'retrieval_core', "
                "'llm_runtime', 'requests', 'docling', 'chromadb', "
                "'qdrant_client'}; "
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


def test_rag_reexports_chunking_constants_and_standalone_helpers():
    assert rag.MIN_CHUNK_WORDS == chunking_core.MIN_CHUNK_WORDS
    assert rag.DEDUP_THRESHOLD == chunking_core.DEDUP_THRESHOLD
    assert rag._is_structural_content is chunking_core._is_structural_content
    assert rag._text_fingerprint is chunking_core._text_fingerprint
    assert rag._make_trigrams is chunking_core._make_trigrams
    assert rag.extract_case_names is chunking_core.extract_case_names
    assert rag.build_section_path is chunking_core.build_section_path
    assert rag.estimate_page_range is chunking_core.estimate_page_range
    assert rag.CHAPTER_RE is chunking_core.CHAPTER_RE


def test_normalize_text_uses_current_rag_cleanup_helpers(monkeypatch):
    observed = []

    def fake_strip(text):
        observed.append(("strip", text))
        return text + "|stripped"

    def fake_dedup(text):
        observed.append(("dedup", text))
        return text + "|deduplicated"

    monkeypatch.setattr(rag, "_strip_headers_footers", fake_strip)
    monkeypatch.setattr(rag, "_dedup_nearby_lines", fake_dedup)

    normalized = rag._normalize_text("source")

    assert normalized == "source|stripped|deduplicated"
    assert observed == [
        ("strip", "source"),
        ("dedup", "source|stripped"),
    ]


def test_classifier_uses_current_rag_structural_helper(monkeypatch):
    observed = {}

    def force_structural(text, headings):
        observed["args"] = (text, headings)
        return True

    monkeypatch.setattr(rag, "_is_structural_content", force_structural)

    assert rag.classify_content_type("ordinary prose", ["A heading"]) == (
        "structural")
    assert observed["args"] == ("ordinary prose", ["A heading"])


def test_nearby_dedup_never_drops_markdown_table_structure():
    """Repeated table rows and adjacent table headers are real content."""
    repeated_row = "\n".join([
        "| Year | Men | Women |",
        "| --- | --- | --- |",
        "| 2019 | 50 | 50 |",
        "| 2020 | 51 | 49 |",
        "| 2019 | 50 | 50 |",
        "| 2021 | 52 | 48 |",
    ])
    assert chunking_core._dedup_nearby_lines(repeated_row) == repeated_row

    adjacent_tables = "\n".join([
        "| A | B |", "| --- | --- |", "| 1 | 2 |",
        "",
        "| A | B |", "| --- | --- |", "| 3 | 4 |",
    ])
    assert chunking_core._dedup_nearby_lines(
        adjacent_tables) == adjacent_tables
    assert rag._normalize_text(repeated_row) == repeated_row


def test_nearby_dedup_still_removes_repeated_prose_furniture():
    text = "\n".join([
        "CHAPTER 4 — PROFESSIONAL RESPONSIBILITY",
        "Substantive first paragraph.",
        "CHAPTER 4 — PROFESSIONAL RESPONSIBILITY",
        "Substantive second paragraph.",
    ])

    assert chunking_core._dedup_nearby_lines(text) == "\n".join([
        "CHAPTER 4 — PROFESSIONAL RESPONSIBILITY",
        "Substantive first paragraph.",
        "Substantive second paragraph.",
    ])


def test_dedup_uses_current_rag_helpers_and_logger(monkeypatch):
    fingerprint_calls = []
    trigram_calls = []
    messages = []

    def fake_fingerprint(text):
        fingerprint_calls.append(text)
        return "same-fingerprint"

    def fake_trigrams(fingerprint):
        trigram_calls.append(fingerprint)
        return frozenset({"same"})

    monkeypatch.setattr(rag, "_text_fingerprint", fake_fingerprint)
    monkeypatch.setattr(rag, "_make_trigrams", fake_trigrams)
    monkeypatch.setattr(rag.log, "info", messages.append)
    chunks = [
        {"text": "first", "metadata": {"chunk_index": 0}},
        {"text": "second", "metadata": {"chunk_index": 1}},
    ]

    assert rag._deduplicate_chunks(chunks) == chunks[:1]
    assert fingerprint_calls == ["first", "second"]
    assert trigram_calls == ["same-fingerprint", "same-fingerprint"]
    assert messages == [
        "Deduplication: removed 1 source-overlapping near-duplicate chunks"]


def test_dedup_preserves_repeated_text_at_distinct_source_identities():
    def record(index, ref):
        return {
            "text": "The same substantive rule appears here.",
            "metadata": {
                "chunk_index": index,
                "source_items": [{"ref": ref}],
            },
        }

    distinct = [record(0, "#/texts/1"), record(1, "#/texts/2")]
    overlapping = [record(0, "#/texts/1"), record(1, "#/texts/1")]

    assert rag._deduplicate_chunks(distinct) == distinct
    assert rag._deduplicate_chunks(overlapping) == overlapping[:1]


def test_dedup_preserves_similar_distinct_holdings_from_same_source():
    common = (
        "The court considered the complete record and the parties' arguments "
        "before announcing its final disposition. "
    )
    chunks = [
        {
            "text": common + "The judgment is affirmed.",
            "metadata": {"source_items": [{"ref": "#/texts/1"}]},
        },
        {
            "text": common + "The judgment is reversed.",
            "metadata": {"source_items": [{"ref": "#/texts/1"}]},
        },
    ]

    assert rag._deduplicate_chunks(chunks, threshold=0.8) == chunks


@pytest.mark.parametrize(
    ("text", "headings", "expected"),
    [
        ("   ", None, "empty"),
        ("ordinary words", ["Table of Contents"], "structural"),
        (
            "Questions about the doctrine appear below.",
            ["Notes and Questions"],
            "notes_and_questions",
        ),
        (
            "Questions about the doctrine appear below.",
            ["Notes & Questions"],
            "notes_and_questions",
        ),
        (
            "This chapter introduces the governing doctrine.",
            ["Chapter 2 · Introduction"],
            "chapter_introduction",
        ),
        (
            "Justice Smith delivered the opinion for a unanimous court.",
            None,
            "case_opinion",
        ),
        (
            "Under 28 U.S.C. § 1332, the amount in controversy is required.",
            None,
            "statutory_excerpt",
        ),
        ("1. See Id. supra and infra for comparison.", None, "footnote"),
        (
            "name|rule|result\nfirst|alpha|yes\nsecond|beta|no",
            None,
            "table",
        ),
        (
            "The textbook author explains the doctrine in ordinary prose.",
            None,
            "author_narrative",
        ),
    ],
)
def test_content_classification_categories(text, headings, expected):
    assert chunking_core.classify_content_type(text, headings) == expected


@pytest.mark.parametrize(
    ("headings", "expected"),
    [
        (
            ["Chapter 1 · Sample Systems", "§1.01 Introduction"],
            "chapter_introduction",
        ),
        (
            [
                "§1.01 Introduction",
                "Chapter 1 · Sample Systems",
                "§1.01 Introduction",
            ],
            "chapter_introduction",
        ),
        (
            [
                "Chapter 1 · Sample Systems",
                "§1.02 Routine Measurements",
            ],
            "author_narrative",
        ),
        (
            [
                "Chapter 6 · Results",
                "§6.05 Fairness in Sample Results",
                "A. Introduction",
            ],
            "author_narrative",
        ),
        (
            [
                "Chapter 8 · Recorded Events",
                "Sample v. Example County",
                "Introduction",
            ],
            "author_narrative",
        ),
        (
            ["Chapter 2 · Sample Operations", "§1.01 Introduction"],
            "author_narrative",
        ),
    ],
)
def test_chapter_introduction_classification_is_leaf_scoped(
        headings, expected):
    text = "The authors provide substantive explanatory prose for readers."
    assert chunking_core.classify_content_type(text, headings) == expected


def test_inferred_chapter_introduction_requires_deterministic_scope():
    record = {
        "text": "The report recounts the events preceding the final review.",
        "metadata": {
            "headings": ["Sample v. Example District", "Introduction"],
            "section_path": (
                "Chapter 8 · Recorded Events > §8.05 Review Sequence"
                " > Sample v. Example District > Introduction"
            ),
        },
    }

    assert not rag._inferred_content_type_allowed(
        record, "chapter_introduction",
        structure_profile="us-law-casebook-v1",
    )
    assert rag._inferred_content_type_allowed(
        record, "case_opinion",
        structure_profile="us-law-casebook-v1",
    )

    record["metadata"].update({
        "headings": ["§8.01 Introduction"],
        "section_path": (
            "Chapter 8 · Recorded Events > §8.01 Introduction"
        ),
    })
    assert rag._inferred_content_type_allowed(
        record, "chapter_introduction",
        structure_profile="us-law-casebook-v1",
    )


def test_nested_notes_heading_prevents_case_opinion_false_positive():
    text = (
        "Reviewer Rowan affirmed the result discussed in these study questions."
    )
    headings = [
        "Chapter 6 · Sample Results",
        "Notes & Questions",
        "Example v. Sample City",
        "I. Introduction",
    ]

    assert chunking_core.classify_content_type(
        text, headings) == "author_narrative"


@pytest.mark.parametrize(
    ("text", "headings"),
    [
        (
            "Consistency and fairness matter. Reviewer Rowan explained why "
            "each result must receive a complete review.",
            ["Chapter 1 · Introduction to Sample Systems", "E. Fairness"],
        ),
        (
            "A reviewer may write a concurring opinion, while another "
            "reviewer may write a dissenting opinion.",
            ["Chapter 1 · Introduction to Sample Systems", "E. Review"],
        ),
    ],
)
def test_case_vocabulary_in_author_explanation_is_not_case_opinion(
        text, headings):
    assert chunking_core.classify_content_type(
        text, headings) == "author_narrative"


def test_normalize_text_repairs_only_high_confidence_pdf_artifacts():
    disclaimer = (
        "This and other authors' explanations draw from the comments to the "
        "Model Rules and from other sources. They are not comprehensive but "
        "highlight some important interpretive points."
    )
    source = (
        "A data- steward relationship. Standard - punctuation.\n"
        "https:// perma . cc / ABCD - EFGH\n"
        "https://example.org/some / split / path "
        "(last visited Jan. 1, 2020)\n"
        "perma.cc/ WXYZ - 1234\n"
        "We do.The next sentence says [W] e're ready.\n"
        f"{disclaimer}\n{disclaimer}"
    )

    normalized = chunking_core._normalize_text(source)

    assert "data-steward" in normalized
    assert "Standard - punctuation" in normalized
    assert "https://perma.cc/ABCD-EFGH" in normalized
    assert "https://example.org/some/split/path (last visited" in normalized
    assert "perma.cc/WXYZ-1234" in normalized
    assert "We do. The next sentence" in normalized
    assert "[W]e're ready" in normalized
    assert "This and other authors" not in normalized


def test_normalize_text_does_not_insert_sentence_space_inside_url_token():
    url = "https://example.comOpenAI"

    assert chunking_core._normalize_text(url) == url
    assert chunking_core._normalize_text("www . Example . org") == (
        "www.Example.org")


def test_visited_url_repair_does_not_consume_prose_between_two_urls():
    source = (
        "https:// perma . cc / ABCD - EFGH (archived source). "
        "This explanatory prose must retain its spaces. "
        "http:// www .example .com / split -path "
        "(last visited Jan. 1, 2025)."
    )

    normalized = chunking_core._normalize_text(source)

    assert "https://perma.cc/ABCD-EFGH" in normalized
    assert "This explanatory prose must retain its spaces" in normalized
    assert "http://www.example.com/split-path" in normalized


@pytest.mark.parametrize(
    ("source", "expected"),
    [
        (
            "https://www .example.com/2018/11/16/archive/story.html",
            "https://www.example.com/2018/11/16/archive/story.html",
        ),
        (
            "https:// docs.example.org/resources/research-notes -archive",
            "https://docs.example.org/resources/research-notes-archive",
        ),
        (
            "https://example.com/reports /incident-summary",
            "https://example.com/reports/incident-summary",
        ),
        (
            "https://news .example.com/US/story ?id=12345",
            "https://news.example.com/US/story?id=12345",
        ),
        (
            "www.example.com/2018 /11/13/archive/events.html",
            "www.example.com/2018/11/13/archive/events.html",
        ),
        (
            "https://\u200bwww .example.org/athlete/sample-profile",
            "https://www.example.org/athlete/sample-profile",
        ),
        (
            "https:// www . example . com / 2018 / 11 / story . html",
            "https://www.example.com/2018/11/story.html",
        ),
        (
            "http:// www . example . org / history / timeline . htm",
            "http://www.example.org/history/timeline.htm",
        ),
    ],
)
def test_normalize_text_repairs_only_url_bound_spacing(source, expected):
    assert chunking_core._normalize_text(source) == expected


@pytest.mark.parametrize(
    ("source", "expected"),
    [
        (
            "See https:// archive . example . com / sample - assets / "
            "uploads / 2025 / 01 / DEVICE - REVIEW - NOTE .pdf.",
            "See https://archive.example.com/sample-assets/uploads/2025/01/"
            "DEVICE-REVIEW-NOTE.pdf.",
        ),
        (
            "See https://www . example . com / 2025 / 01 / 02 / reports / "
            "sample - archive - device - results .html.",
            "See https://www.example.com/2025/01/02/reports/"
            "sample-archive-device-results.html.",
        ),
        (
            "See ht tps:// www . example . org / sample -maps "
            "(visited Jan. 3, 2025).",
            "See https://www.example.org/sample-maps "
            "(visited Jan. 3, 2025).",
        ),
        (
            "See http://www . example . com / 2025 / 01 / 04 / reports / "
            "sample . html ? pagewanted =all. How should the archive be read?",
            "See http://www.example.com/2025/01/04/reports/sample.html"
            "?pagewanted=all. How should the archive be read?",
        ),
        (
            "See https://www . example . org / sites / authors / 2025 / "
            "synthetic - analysis - results /#sample123.",
            "See https://www.example.org/sites/authors/2025/"
            "synthetic-analysis-results/#sample123.",
        ),
        (
            "See https://beta . example . com / reports / sample - "
            "manufacturer / 2025 / sample1234 - test _ story .html.",
            "See https://beta.example.com/reports/sample-manufacturer/"
            "2025/sample1234-test_story.html.",
        ),
    ],
)
def test_transactional_url_repair_handles_source_pdf_spacing(source, expected):
    assert chunking_core._normalize_text(source) == expected


def test_generic_url_parser_still_fails_closed_on_mixed_native_spacing():
    source = (
        "See https:// beta . example . com / reports / "
        "sample-device-results-require-additional-independent-review / "
        "2025 / 01 / 05 / aaaaaaaa - bbbb - cccc - dddd - "
        "eeeeeeeeeeee _ story .html."
    )

    assert chunking_core._repair_spaced_url_candidates(source) == source
    assert chunking_core.has_malformed_url_spacing(source)


@pytest.mark.parametrize(
    "source",
    [
        "See https://example.com . Next sentence remains separate.",
        "See www.example.com . Next sentence remains separate.",
        "See https://example.com / commentary follows.",
        "See https://example.com/path - commentary follows.",
        "See https://example.com/path\n- commentary on the next line.",
    ],
)
def test_transactional_url_repair_rejects_prose_boundaries(source):
    assert chunking_core._normalize_text(source) == source


def test_transactional_url_repair_stops_after_closed_spaced_host():
    source = "See https://example . com . Next sentence remains separate."
    assert chunking_core._normalize_text(source) == (
        "See https://example.com . Next sentence remains separate.")


@pytest.mark.parametrize(
    "source",
    [
        "See ht tps:// www . example . org / source . html.",
        "See https://beta . example . com / story . html.",
        "See www . example . com for the source.",
    ],
)
def test_malformed_url_gate_detects_transactional_repair_candidates(source):
    assert chunking_core.has_malformed_url_spacing(source)
    assert not chunking_core.has_malformed_url_spacing(
        chunking_core._normalize_text(source))


@pytest.mark.parametrize(
    "source",
    [
        "See https://example.com . Next sentence remains separate.",
        "See https://example.com / commentary follows.",
        "See https://example.com/path - commentary follows.",
    ],
)
def test_malformed_url_gate_does_not_flag_prose_boundaries(source):
    assert not chunking_core.has_malformed_url_spacing(source)


def test_spaced_url_scheme_repair_does_not_consume_following_prose():
    source = (
        "Watch https:// media.example.com/watch?v=abc123. "
        "The next sentence must retain its spaces."
    )

    assert chunking_core._normalize_text(source) == (
        "Watch https://media.example.com/watch?v=abc123. "
        "The next sentence must retain its spaces."
    )


@pytest.mark.parametrize(
    "source",
    [
        "See https://example.com for the source.",
        "See https://example.com/path - commentary follows.",
        "See www.example.com/path and compare the printed source.",
    ],
)
def test_url_repair_preserves_ordinary_prose_boundaries(source):
    assert chunking_core._normalize_text(source) == source


@pytest.mark.parametrize(
    ("source", "expected"),
    [
        (
            "ht tp:// www . example . com / source . html",
            "http://www.example.com/source.html",
        ),
        (
            "https://library . example . org / source . html",
            "https://library.example.org/source.html",
        ),
        (
            "https://data . news . example . com / source . html",
            "https://data.news.example.com/source.html",
        ),
        (
            "https://museum . example . test / source . html",
            "https://museum.example.test/source.html",
        ),
        (
            "https://Example . Com / source . html",
            "https://Example.Com/source.html",
        ),
        (
            "https://sample . example . invalid / source . html",
            "https://sample.example.invalid/source.html",
        ),
        (
            "https://example . com / search ? q = alpha - commentary follows",
            "https://example.com/search?q=alpha - commentary follows",
        ),
        (
            "https://example . com / search ?q=alpha & compare authorities",
            "https://example.com/search?q=alpha & compare authorities",
        ),
    ],
)
def test_transactional_url_repair_adversarial_valid_endpoints(source, expected):
    assert chunking_core._normalize_text(source) == expected
    assert chunking_core.has_malformed_url_spacing(source)
    assert not chunking_core.has_malformed_url_spacing(expected)


@pytest.mark.parametrize(
    "source",
    [
        "https://example.com . in this discussion",
        "https://example.com . to compare authorities",
    ],
)
def test_transactional_url_repair_does_not_treat_prose_as_country_tld(source):
    assert chunking_core._normalize_text(source) == source
    assert not chunking_core.has_malformed_url_spacing(source)


@pytest.mark.parametrize(
    "source",
    [
        "https://example . com : 8080 / source . html",
        "https://example . com / unresolved tail",
    ],
)
def test_transactional_url_repair_fails_closed_on_unproven_endpoints(source):
    assert chunking_core._normalize_text(source) == source
    assert chunking_core.has_malformed_url_spacing(source)


@pytest.mark.parametrize(
    ("source", "expected"),
    [
        (
            "https://foo . com . example . invalid / source . html",
            "https://foo.com.example.invalid/source.html",
        ),
        (
            "https://foo . law . example . invalid / source . html",
            "https://foo.law.example.invalid/source.html",
        ),
        (
            "https://sample . example.INVALID",
            "https://sample.example.INVALID",
        ),
        (
            "https://sample . example . invalid",
            "https://sample.example.invalid",
        ),
        (
            "https://192.0.2.1 / source . html",
            "https://192.0.2.1/source.html",
        ),
        (
            "https://xn--sample-9db . example / source . html",
            "https://xn--sample-9db.example/source.html",
        ),
        (
            "https://catalog.example.com /sites/sample7e/.",
            "https://catalog.example.com/sites/sample7e/.",
        ),
        (
            "https://catalog.example.com/sites/ sample7e/ .",
            "https://catalog.example.com/sites/sample7e/.",
        ),
        (
            "https://catalog.example.com/sites /sample7e/ .",
            "https://catalog.example.com/sites/sample7e/.",
        ),
        (
            "https://www \u00b7example.org/story.html",
            "https://www.example.org/story.html",
        ),
        (
            "www.example. com/games.' The rules apply.",
            "www.example.com/games.' The rules apply.",
        ),
    ],
)
def test_transactional_url_repair_closes_whole_proven_candidate(source, expected):
    normalized = chunking_core._normalize_text(source)
    assert normalized == expected
    assert chunking_core.has_malformed_url_spacing(source)
    assert not chunking_core.has_malformed_url_spacing(normalized)


@pytest.mark.parametrize(
    "source",
    [
        "https://example . com @ attacker . example/login",
        "https://example . com@attacker.example/login",
        "https://example . com..attacker/login",
        "https://example . com_attacker/login",
        "https://example . com\\attacker/login",
        "https://example . com;@attacker.example/login",
        "https://example . com!@attacker.example/login",
        "https://example . com$@attacker.example/login",
        "https://example . com&@attacker.example/login",
        "https://example . com'@attacker.example/login",
        "https://example . com(@attacker.example/login",
        "https://example . com)@attacker.example/login",
        "https://example . com*@attacker.example/login",
        "https://example . com+@attacker.example/login",
        "https://example . com,@attacker.example/login",
        "https://example . com=@attacker.example/login",
        "https://example . com;attacker.example/login",
        "https://example . com!attacker.example/login",
        "https://example . com$attacker.example/login",
        "https://example . com&attacker.example/login",
        "https://example . com'attacker.example/login",
        "https://example . com(attacker.example/login",
        "https://example . com)attacker.example/login",
        "https://example . com*attacker.example/login",
        "https://example . com+attacker.example/login",
        "https://example . com,attacker.example/login",
        "https://example . com=attacker.example/login",
        "https://example . com/search ? email = user @ example . com",
        "https://example . com/search ? q = % ZZ",
        "https://example.com : 8080 / source . html",
        "https://example.com: 8080 / source . html",
        "https://example.com:8080 / source . html",
        "https://[2001:db8::1] / source . html",
        "https://[2001 : db8 :: 1] / source . html",
        "https://[2001 : db8 :: 1]/source.html",
        "https://[2001 : db8 :: 1]",
        "https://[2001 : db8 :: 1]?q=x",
        "https://user:pass@example.com / source . html",
        "https://user : pass @ example . com / source . html",
        "https://user : pass @ example . com/source.html",
        "https://user : pass @ example . com",
        "https://user : pass @ example . com?q=x",
        "https://b\u00fccher . example / source . html",
    ],
)
def test_transactional_url_repair_rejects_authority_or_escape_ambiguity(source):
    assert chunking_core._normalize_text(source) == source
    assert chunking_core.has_malformed_url_spacing(source)


@pytest.mark.parametrize(
    ("source", "expected"),
    [
        (
            "https://example . com/search ? q = alpha - commentary = follows.",
            "https://example.com/search?q=alpha - commentary = follows.",
        ),
        (
            "https://example . com/search ? q = alpha & compare = authorities.",
            "https://example.com/search?q=alpha & compare = authorities.",
        ),
        (
            "https://example . com/search ? q = alpha - commentary # note.",
            "https://example.com/search?q=alpha - commentary # note.",
        ),
        (
            "https://example . com/search ? q = alpha = commentary = follows.",
            "https://example.com/search?q=alpha = commentary = follows.",
        ),
        (
            "https://example . com/search ? q = alpha + commentary = follows.",
            "https://example.com/search?q=alpha + commentary = follows.",
        ),
        (
            "https://example . com/search ? q = alpha / commentary = follows.",
            "https://example.com/search?q=alpha / commentary = follows.",
        ),
        (
            "https://example . com/search ? q = alpha & page = 2.",
            "https://example.com/search?q=alpha&page=2.",
        ),
    ],
)
def test_transactional_url_repair_cannot_promote_past_query_prose(source, expected):
    normalized = chunking_core._normalize_text(source)
    assert normalized == expected
    assert "commentary=" not in normalized
    assert "compare=" not in normalized
    assert "commentary#" not in normalized


def test_transactional_url_repair_does_not_merge_adjacent_domains():
    source = "https://example . com. www.example.org/path.html"
    normalized = chunking_core._normalize_text(source)
    assert normalized == "https://example.com. www.example.org/path.html"
    assert "example.com.www" not in normalized
    assert not chunking_core.has_malformed_url_spacing(normalized)


@pytest.mark.parametrize(
    ("source", "expected"),
    [
        (
            "https://example . com . www.example.org/path.html",
            "https://example.com . www.example.org/path.html",
        ),
        (
            "https://example . com . Federal . sample reviews",
            "https://example.com . Federal . sample reviews",
        ),
    ],
)
def test_transactional_url_repair_stops_at_spaced_sentence_domain(source, expected):
    normalized = chunking_core._normalize_text(source)
    assert normalized == expected
    assert "example.com.www" not in normalized
    assert "example.com.Federal" not in normalized


@pytest.mark.parametrize(
    ("source", "expected"),
    [
        (
            "https:// shop . example . com / Sample - Device / dp / ITEM123 . "
            "It is much longer.",
            "https://shop.example.com/Sample-Device/dp/ITEM123. "
            "It is much longer.",
        ),
        (
            "https:// www . example . org / blog / sample - metric - gap / "
            "(2023).",
            "https://www.example.org/blog/sample-metric-gap/ (2023).",
        ),
        (
            "https:// research . example . org / short - reads / "
            "sample - issues / (noting the survey).",
            "https://research.example.org/short-reads/sample-issues/ "
            "(noting the survey).",
        ),
        (
            "https:// policy . example . org / technology / sample - rule - "
            "index; see, e.g., the collected entries.",
            "https://policy.example.org/technology/sample-rule-index; "
            "see, e.g., the collected entries.",
        ),
        (
            "https:// registry . example . net / en / news - archive / details / "
            "? varevent = 42 .",
            "https://registry.example.net/en/news-archive/details/"
            "?varevent=42 .",
        ),
        (
            "(https:// safety . example . org); next source.",
            "(https://safety.example.org); next source.",
        ),
        (
            "https:// media . example . com / watch ? v = sample123&t =26s. "
            "The video follows.",
            "https://media.example.com/watch?v=sample123&t=26s. "
            "The video follows.",
        ),
    ],
)
def test_transactional_url_repair_handles_representative_boundaries(
        source, expected):
    normalized = chunking_core._normalize_text(source)
    assert normalized == expected
    assert not chunking_core.has_malformed_url_spacing(normalized)


@pytest.mark.parametrize(
    "source",
    [
        "htt ps:// www . example . com / source . html",
        "h ttps:// www . example . com / source . html",
        "https :// www . example . com / source . html",
        "https:/ / www . example . com / source . html",
        "h t tp:// www . example . com / source . html",
        "h t t ps:// www . example . com / source . html",
    ],
)
def test_transactional_url_repair_fails_closed_on_malformed_scheme(source):
    assert chunking_core._normalize_text(source) == source
    assert chunking_core.has_malformed_url_spacing(source)


def test_literal_http_syntax_is_not_a_malformed_url_candidate():
    source = "The literal token https:// is discussed here."
    assert chunking_core._normalize_text(source) == source
    assert not chunking_core.has_malformed_url_spacing(source)


@pytest.mark.parametrize(
    "source",
    [
        "[click](java\u200bscript:alert(1))",
        "data\u200b:text/html,<h1>x</h1>",
        "[click](vb\u2060script:alert(1))",
        "[click](fi\ufeffle:///tmp/source)",
        "[click](java\u200bscript&#58;alert(1))",
        "[click](java\u200bscript&#x3a;alert(1))",
        "[click](java\u200bscript&colon;alert(1))",
        "[click](java\u200bscript\\:alert(1))",
        "<a href=\"java\u200bscript&#58;alert(1)\">click</a>",
    ],
)
def test_zero_width_cleanup_does_not_activate_unsafe_scheme(source):
    assert chunking_core._normalize_text(source) == source


def test_zero_width_cleanup_is_safe_per_obfuscated_scheme_occurrence():
    source = (
        "javascript:void(0) and [click](java\u200bscript:alert(1)) "
        "plus https://\u200bwww . example . org / source . html"
    )
    normalized = chunking_core._normalize_text(source)
    assert "[click](java\u200bscript:alert(1))" in normalized
    assert "https://www.example.org/source.html" in normalized
    assert normalized.count("javascript:") == 1


def test_zero_width_cleanup_still_repairs_safe_extraction_artifacts():
    assert chunking_core._normalize_text(
        "https://\u200bwww . example . org / source . html",
    ) == "https://www.example.org/source.html"


def test_normalize_text_repairs_known_fused_legal_extraction_terms():
    source = (
        "The clientlawyer relationship used lawyerclient communications in "
        "a SarbanesOxley matter before a threejudge panel in NewYork."
    )

    normalized = chunking_core._normalize_text(source)

    assert "client-lawyer relationship" in normalized
    assert "lawyer-client communications" in normalized
    assert "Sarbanes-Oxley" in normalized
    assert "three-judge panel" in normalized
    assert "New York" in normalized


def test_normalize_text_removes_decorative_section_marker_lines():
    source = (
        "Continuation from the preceding page.\n"
        "C\n"
        "The new major section begins with substantive prose."
    )

    normalized = chunking_core._normalize_text(source)

    assert "\nC\n" not in normalized
    assert "Continuation from the preceding page." in normalized
    assert "The new major section begins" in normalized


def test_normalize_text_removes_decorative_square_lines():
    source = "CHAPTER 1\n\u25a0   \u25a0   \u25a0\nINTRODUCTION"

    normalized = chunking_core._normalize_text(source)

    assert "\u25a0" not in normalized
    assert normalized == "CHAPTER 1\nINTRODUCTION"


@pytest.mark.parametrize("marker", ["A", "C\n", "  J  "])
def test_normalize_text_removes_marker_only_chunks(marker):
    assert chunking_core._normalize_text(marker) == ""


def test_clean_heading_repairs_high_confidence_fused_typography():
    heading = (
        "A CommonLawApproachtoSample Terms underUCC2-207"
    )

    assert chunking_core.clean_heading_text(heading) == (
        "A Common Law Approach to Sample Terms under UCC 2-207"
    )


@pytest.mark.parametrize(
    "heading",
    [
        "Summary of Contents",
        "Detailed Contents",
        "Table of Problems",
        "Table of Cases",
        "Table of Rules, Statutes, and Standards",
        "About the Authors",
        "Editorial Advisors",
    ],
)
def test_structural_detection_recognizes_book_front_and_back_matter(heading):
    text = "Long enough supporting text that should not trigger the short-title rule."

    assert chunking_core._is_structural_content(text, ["Book", heading])


@pytest.mark.parametrize(
    "text",
    [
        "Copyright © 2025 Example Press. All rights reserved worldwide.",
        "No part of this publication may be reproduced or transmitted by any means.",
    ],
)
def test_structural_detection_recognizes_copyright_boilerplate(text):
    assert chunking_core._is_structural_content(text, None)


def test_classifier_prefers_table_shape_over_rule_citations():
    text = (
        "rule|authority|result\n"
        "Rule 1.6|28 U.S.C. § 1|yes\n"
        "Rule 3.3|Model Code § 2|no"
    )

    assert chunking_core.classify_content_type(text, None) == "table"


def test_classifier_recognizes_opening_rule_language_marker():
    text = (
        "Rule language**\n"
        "(a) A lawyer employed by an organization represents the organization."
    )

    assert chunking_core.classify_content_type(
        text, ["Rule 1.13 Organization as Client"]
    ) == "statutory_excerpt"


def test_classifier_uses_explicit_rule_and_explanation_lanes():
    quoted_rule = "(a) A lawyer shall comply with Rule 1.6 and § 2."

    assert chunking_core.classify_content_type(
        quoted_rule, ["Rule Language"]) == "statutory_excerpt"
    assert chunking_core.classify_content_type(
        quoted_rule, ["Authors' Explanation"]) == "author_narrative"


def test_classifier_does_not_treat_question_about_model_rule_as_rule_text():
    text = (
        "Many states use the Model Rules as a basis for their own rules, but "
        "their screening provisions vary substantially."
    )

    assert chunking_core.classify_content_type(
        text,
        ["Are state rules the same as the new Model Rule 1.10?"],
    ) == "author_narrative"


def test_classifier_does_not_treat_embedded_footnotes_as_a_footnote_chunk():
    text = (
        "The authors explain the governing doctrine in ordinary prose.\n"
        "1. See Id. supra and infra.\n"
        "2. See also Id. and compare the authorities supra."
    )

    assert chunking_core.classify_content_type(text, None) == "author_narrative"


def test_classifier_does_not_treat_incidental_citations_as_statutory_text():
    text = (
        "The authors compare Rule 1.6 with 28 U.S.C. § 1332 and explain why "
        "the authorities point in different directions."
    )

    assert chunking_core.classify_content_type(text, None) == "author_narrative"


def test_extract_case_names_stops_at_narrative_and_handles_in_re_names():
    text = (
        "See Kirby v. Illinois, the Supreme Court explained the doctrine. "
        "Brown v. Board of Education was decided before In re Gault."
    )

    assert chunking_core.extract_case_names(text) == [
        "Kirby v. Illinois",
        "Brown v. Board of Education",
        "In re Gault",
    ]


def test_extract_case_names_normalizes_wrapped_case_captions():
    text = (
        "In Sample Supply Co. v.\n"
        "Example Market Group, 321 Ex. 456, the panel ruled."
    )

    assert chunking_core.extract_case_names(text) == [
        "Sample Supply Co. v. Example Market Group",
    ]


def test_extract_case_names_bounds_long_capitalized_parties():
    text = (
        "Alpha Bravo Charlie Delta Echo Foxtrot Golf Hotel India Juliet Kilo "
        "Lima v. November Oscar Papa Quebec Romeo Sierra Tango Uniform Victor "
        "Whiskey Xray Yankee Zulu."
    )

    names = chunking_core.extract_case_names(text)

    assert names
    assert all(len(name) <= 160 for name in names)
    assert all(len(name.split()) <= 19 for name in names)


def test_extract_case_names_trims_article_titles_before_case_name():
    text = (
        "A Study of How Turner v. Rogers Affected Child Support Proceedings. "
        "Fifty Years of Defiance and Resistance After Gideon v. Wainwright."
    )

    assert chunking_core.extract_case_names(text) == [
        "Turner v. Rogers",
        "Gideon v. Wainwright",
    ]


def test_clean_heading_text_removes_scaffold_artifacts_conservatively():
    assert chunking_core.clean_heading_text(
        "A. Lawyers' duties ........ 421") == "A. Lawyers' duties"
    assert chunking_core.clean_heading_text(
        "A. Lawyers' duties p. 421") == "A. Lawyers' duties"
    assert chunking_core.clean_heading_text(
        "A. Lawyers' duties Chapter 12 — Advocacy"
    ) == "A. Lawyers' duties"
    assert chunking_core.clean_heading_text(
        "Chapter 12 — Advocacy") == "Chapter 12 — Advocacy"
    assert chunking_core.clean_heading_text("Rule 1.6") == "Rule 1.6"
    assert chunking_core.clean_heading_text(
        "The problem- based approach") == "The problem-based approach"


def test_build_section_path_cleans_headings_and_skips_table_rows():
    path = chunking_core.build_section_path([
        "Chapter 1 — Introduction",
        "N/A 0.25 1.00",
        "A. Duties ........ 42",
    ])

    assert path == "Chapter 1 — Introduction → A. Duties"


@pytest.mark.parametrize(
    "text",
    [
        "Id. at 41.",
        "Ibid. at 83",
        "13 Tria1 at 22-23",
        (
            "2) a defendant should receive the ordinary protection that "
            "the governing rule provides in every comparable case."
        ),
    ],
)
def test_probable_misclassified_heading_accepts_text_proof(text):
    assert chunking_core.is_probable_misclassified_section_header(text)


def test_probable_misclassified_heading_requires_geometry_for_packaging_copy():
    text = (
        "PORTABLE SAMPLE MODULE REMAINS READY FOR CONTROLLED LABORATORY USE "
        "WITH SEALED COMPONENTS CLEAR STATUS LIGHTS REUSABLE PACKAGING SIMPLE "
        "STARTUP INSTRUCTIONS AND CONSISTENT PERFORMANCE ACROSS ROUTINE "
        "DEMONSTRATIONS"
    )

    assert not chunking_core.is_probable_misclassified_section_header(text)
    assert not chunking_core.is_probable_misclassified_section_header(
        text, bbox_height=89.9)
    assert chunking_core.is_probable_misclassified_section_header(
        text, bbox_height=103.4)


@pytest.mark.parametrize(
    ("text", "bbox_height"),
    [
        (
            "THE RESPONSE MUST FOLLOW EACH CLEAR INSTRUCTION IN THE SAMPLE "
            "REQUEST AND USE THE SPECIFIED FORMAT FOR DELIVERY",
            62.4,
        ),
        (
            "THE SAMPLE SYSTEM'S APPROACH TO CONFLICTING CONFIGURATION VALUES "
            "IN RELATED REQUESTS: RESOLVING DUPLICATE SETTINGS",
            62.4,
        ),
        (
            "CONFIGURATION DETAILS REVEALED AFTER A DEVICE IS STARTED: "
            "PACKAGED SETTINGS, ROLLING UPDATES, AND UNILATERAL SAMPLE "
            "OVERRIDES",
            78.4,
        ),
        (
            "CHAPTER 6. CONFLICTING CONFIGURATION VALUES, OVERRIDE ORDER, AND "
            "LATE DISCOVERY OF SHARED SETTINGS",
            146.4,
        ),
        (
            "I. DOES THE SAMPLE RECOVERY PROCESS HANDLE AN UNPLANNED DEVICE "
            "RESTART WITHOUT LOSING VERIFIED STATE?",
            134.3,
        ),
        (
            "SIMULATED EVIDENCE AND SYSTEM DESIGN: ARE OPERATORS AND "
            "MAINTAINERS FOLLOWING A CONSISTENT REVIEW PROCESS ACROSS "
            "ENVIRONMENTS?",
            34.7,
        ),
        (
            "§ 6.01 A VERY LONG WRAPPED SECTION HEADING THAT REMAINS A "
            "STRUCTURAL DIVISION EVEN WHEN ITS DISPLAY BOX IS TALL AND ITS "
            "WORDS ARE ALL CAPITALIZED ACROSS SEVERAL AUTHORED LINES",
            100.0,
        ),
    ],
)
def test_probable_misclassified_heading_preserves_representative_headings(
        text, bbox_height):
    assert not chunking_core.is_probable_misclassified_section_header(
        text, bbox_height=bbox_height)
