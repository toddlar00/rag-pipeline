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


def test_normalize_text_repairs_only_high_confidence_pdf_artifacts():
    disclaimer = (
        "This and other authors' explanations draw from the comments to the "
        "Model Rules and from other sources. They are not comprehensive but "
        "highlight some important interpretive points."
    )
    source = (
        "A client- lawyer relationship. Standard - punctuation.\n"
        "https:// perma . cc / ABCD - EFGH\n"
        "https://example.org/some / split / path "
        "(last visited Jan. 1, 2020)\n"
        "perma.cc/ WXYZ - 1234\n"
        "We do.The next sentence says [W] e're ready.\n"
        f"{disclaimer}\n{disclaimer}"
    )

    normalized = chunking_core._normalize_text(source)

    assert "client-lawyer" in normalized
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
