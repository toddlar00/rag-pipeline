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
    assert messages == ["Deduplication: removed 1 near-duplicate chunks"]


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
