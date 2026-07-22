"""Pure text preparation and classification for structure-aware chunking.

This module intentionally uses only Python's standard library.  Pipeline
orchestration injects replaceable helper and logging callbacks through the
``rag.py`` compatibility facade.
"""

from __future__ import annotations

import re
from collections.abc import Callable


MIN_CHUNK_WORDS = 20
DEDUP_THRESHOLD = 0.95

_WHITESPACE_RE = re.compile(r"[^\S\n]+")
_NEWLINES_RE = re.compile(r"\n{3,}")
_FP_RE = re.compile(r"[^a-z0-9]")
_HEADER_FOOTER_RE = re.compile(r"^\s*\d{1,4}\s*$", re.MULTILINE)

_STRUCTURAL_PATTERNS = [
    re.compile(r"^(Table of )?Contents$", re.IGNORECASE | re.MULTILINE),
    re.compile(r"^Index$", re.IGNORECASE | re.MULTILINE),
    re.compile(r"^Preface$", re.IGNORECASE | re.MULTILINE),
    re.compile(r"^Acknowledgments?$", re.IGNORECASE | re.MULTILINE),
    re.compile(r"^About the Authors?$", re.IGNORECASE | re.MULTILINE),
    re.compile(
        r"^(Series |Editorial )?Advisory Board$",
        re.IGNORECASE | re.MULTILINE,
    ),
]
_TOC_LINE_RE = re.compile(r"^.{5,80}\s+\d{1,4}\s*$", re.MULTILINE)
_INDEX_LINE_RE = re.compile(
    r"^[A-Z].{2,60},\s*\d{1,4}(?:[-,]\s*\d{1,4})*\s*$", re.MULTILINE)

NOTES_Q_RE = re.compile(r"^(?:Notes and Questions|Questions)\b", re.MULTILINE)
CHAPTER_RE = re.compile(
    r"^(?:Chapter\s+)?(\d{1,2})\s*[·\-\xb7\u00b7\u2022–—]\s*(.+)$",
    re.MULTILINE,
)
CASE_EXTRACT_RE = re.compile(
    r"((?:(?:In re|Ex parte)\s+)?[A-Z][A-Za-z\'\-\.]+"
    r"(?:\s+[A-Z][A-Za-z\'\-\.]+)*\s+v\.\s+"
    r"[A-Z][A-Za-z\'\-\.]+(?:\s+[A-Za-z\'\-\.,]+)*)"
)
_FOOTNOTE_NUM_RE = re.compile(
    r"^\s*(?:\d{1,3}[.\)]\s|[\u00b9\u00b2\u00b3\u2070-\u2079]+\s|"
    r"\[\d{1,3}\]\s)",
    re.MULTILINE,
)
_FOOTNOTE_CITE_MARKERS = [
    "Id.", "id.", "supra", "infra", "See ", "see ", "Cf.", "cf.",
    "e.g.,", "Compare ",
]


TextTransformFn = Callable[[str], str]
StructuralContentFn = Callable[[str, list[str] | None], bool]
FingerprintFn = Callable[[str], str]
TrigramFn = Callable[[str], frozenset[str]]
RemovedCallback = Callable[[int], None]


def _normalize_text(
        text: str, *,
        strip_headers_footers_fn: TextTransformFn | None = None,
        dedup_nearby_lines_fn: TextTransformFn | None = None) -> str:
    """Fix common encoding artifacts from PDF extraction.

    Handles: non-breaking spaces (\xa0), smart quotes, en/em dashes,
    ligatures (fi, fl, ff, ffi, ffl), and stray control characters.
    """
    if not text:
        return ""
    replacements = {
        "\xa0": " ",
        "\u2018": "'",
        "\u2019": "'",
        "\u201c": '"',
        "\u201d": '"',
        "\u2013": "-",
        "\u2014": " - ",
        "\ufb01": "fi",
        "\ufb02": "fl",
        "\ufb00": "ff",
        "\ufb03": "ffi",
        "\ufb04": "ffl",
        "\ufffd": "",
    }
    for old, new in replacements.items():
        text = text.replace(old, new)
    if any(0xE000 <= ord(char) <= 0xF8FF for char in text):
        pua_map = {chr(0xF643 + index): str(index) for index in range(10)}
        text = "".join(pua_map.get(char, char) for char in text)
    text = _WHITESPACE_RE.sub(" ", text)
    text = _NEWLINES_RE.sub("\n\n", text)
    strip_fn = (
        _strip_headers_footers
        if strip_headers_footers_fn is None else strip_headers_footers_fn
    )
    dedup_fn = (
        _dedup_nearby_lines
        if dedup_nearby_lines_fn is None else dedup_nearby_lines_fn
    )
    text = strip_fn(text)
    text = dedup_fn(text)
    return text.strip()


def _strip_headers_footers(text: str) -> str:
    """Remove likely page headers and footers from chunk text.

    Only standalone page-number lines are removed. Without page-position
    provenance, short all-caps lines and Roman numerals are ambiguous: they are
    often legitimate legal headings (for example ``PERSONAL JURISDICTION`` or
    ``IV.``) and must be preserved.
    """
    if not text:
        return text
    return _HEADER_FOOTER_RE.sub("", text)


def _dedup_nearby_lines(text: str, window: int = 5) -> str:
    """Remove lines that duplicate another line within *window* lines above."""
    lines = text.split("\n")
    out: list[str] = []
    for line in lines:
        stripped = line.strip()
        if not stripped:
            out.append(line)
            continue
        start = max(0, len(out) - window)
        duplicate = any(previous.strip() == stripped for previous in out[start:])
        if not duplicate:
            out.append(line)
    return "\n".join(out)


def _is_structural_content(text: str, headings: list[str] | None) -> bool:
    """Detect TOC, index, title pages, copyright — not substantive content."""
    heading_text = " ".join(headings) if headings else ""
    if any(pattern.search(heading_text) for pattern in _STRUCTURAL_PATTERNS):
        return True

    lines = text.strip().split("\n")
    if len(lines) > 3:
        toc_matches = len(_TOC_LINE_RE.findall(text))
        if toc_matches > len(lines) * 0.4:
            return True
    if len(lines) > 5:
        index_matches = len(_INDEX_LINE_RE.findall(text))
        if index_matches > len(lines) * 0.4:
            return True
    word_count = len(text.split())
    return word_count < 15 and not any(char.islower() for char in text[:100])


def _text_fingerprint(text: str) -> str:
    """Produce a normalized fingerprint for near-duplicate detection."""
    return _FP_RE.sub("", text.lower())


def _make_trigrams(fingerprint: str) -> frozenset[str]:
    """Pre-compute character trigrams for a fingerprint."""
    if len(fingerprint) < 3:
        return frozenset()
    return frozenset(
        fingerprint[index:index + 3]
        for index in range(len(fingerprint) - 2)
    )


def _deduplicate_chunks(
        chunks: list[dict], threshold: float = DEDUP_THRESHOLD, *,
        text_fingerprint_fn: FingerprintFn | None = None,
        make_trigrams_fn: TrigramFn | None = None,
        removed_callback: RemovedCallback | None = None) -> list[dict]:
    """Remove near-duplicate chunks based on trigram Jaccard similarity."""
    if not chunks:
        return chunks

    fingerprint_fn = (
        _text_fingerprint
        if text_fingerprint_fn is None else text_fingerprint_fn
    )
    trigrams_fn = _make_trigrams if make_trigrams_fn is None else make_trigrams_fn
    kept: list[dict] = []
    seen: list[tuple[int, frozenset[str]]] = []
    for chunk in chunks:
        fingerprint = fingerprint_fn(chunk["text"])
        fingerprint_length = len(fingerprint)
        trigrams_a = trigrams_fn(fingerprint)
        if not trigrams_a:
            kept.append(chunk)
            continue

        is_duplicate = False
        for seen_length, trigrams_b in seen:
            if (abs(fingerprint_length - seen_length)
                    / max(fingerprint_length, seen_length) > 0.2):
                continue
            if not trigrams_b:
                continue
            jaccard = len(trigrams_a & trigrams_b) / len(trigrams_a | trigrams_b)
            if jaccard >= threshold:
                is_duplicate = True
                break

        if not is_duplicate:
            kept.append(chunk)
            seen.append((fingerprint_length, trigrams_a))

    removed = len(chunks) - len(kept)
    if removed > 0 and removed_callback is not None:
        removed_callback(removed)
    return kept


def classify_content_type(
        text: str, headings: list[str] | None, *,
        structural_content_fn: StructuralContentFn | None = None) -> str:
    """Classify a chunk's content type based on text patterns and headings."""
    if not text.strip():
        return "empty"

    structural_fn = (
        _is_structural_content
        if structural_content_fn is None else structural_content_fn
    )
    if structural_fn(text, headings):
        return "structural"

    heading_text = " ".join(headings) if headings else ""
    if NOTES_Q_RE.search(heading_text) or NOTES_Q_RE.search(text[:200]):
        return "notes_and_questions"
    if CHAPTER_RE.search(heading_text):
        if any("Introduction" in heading for heading in (headings or [])):
            return "chapter_introduction"

    case_markers = [
        "Justice ", "Judge ", "JUSTICE ", "JUDGE ",
        "delivered the opinion", "Opinion of the Court",
        "concurring", "dissenting", "affirmed", "reversed",
        "certiorari", "Argued ", "Decided ",
    ]
    if sum(1 for marker in case_markers if marker in text) >= 2:
        return "case_opinion"

    statute_markers = ["U.S.C.", "§", "Fed. R. Civ. P.", "Rule "]
    if sum(1 for marker in statute_markers if marker in text[:500]) >= 2:
        return "statutory_excerpt"

    lines = text.strip().split("\n")
    numbered_footnotes = len(_FOOTNOTE_NUM_RE.findall(text))
    citation_markers = sum(
        1 for marker in _FOOTNOTE_CITE_MARKERS if marker in text)
    if numbered_footnotes >= 2 and citation_markers >= 3:
        return "footnote"
    if (numbered_footnotes >= 1 and citation_markers >= 4
            and len(text.split()) < 200):
        return "footnote"

    pipe_lines = sum(1 for line in lines if line.count("|") >= 2)
    if len(lines) > 2 and pipe_lines > len(lines) * 0.3:
        return "table"
    tab_lines = [line for line in lines if line.count("\t") >= 2]
    if len(tab_lines) >= 3:
        return "table"
    return "author_narrative"


def extract_case_names(text: str) -> list[str]:
    """Extract case names mentioned in the chunk."""
    matches = CASE_EXTRACT_RE.findall(text)
    seen = set()
    result = []
    signal_prefixes = [
        "See ", "see ", "Cf. ", "cf. ", "Compare ", "In ",
        "But see ", "E.g., ",
    ]
    for match in matches:
        cleaned = match.strip().rstrip(".,;")
        for prefix in signal_prefixes:
            if cleaned.startswith(prefix):
                cleaned = cleaned[len(prefix):]
        if "The Rest" in cleaned or "The Story" in cleaned:
            continue
        if cleaned not in seen and len(cleaned) > 5:
            seen.add(cleaned)
            result.append(cleaned)
    return result[:10]


def build_section_path(headings: list[str] | None) -> str:
    if not headings:
        return ""
    return " → ".join(heading.strip() for heading in headings if heading.strip())


def estimate_page_range(chunk_index: int, total_chunks: int,
                        total_pages: int) -> str:
    """Rough page estimate based on chunk position (fallback only)."""
    if total_chunks <= 1 or total_pages <= 1:
        return "~p.1"
    bounded_index = min(max(chunk_index, 0), total_chunks - 1)
    position = bounded_index / (total_chunks - 1)
    page = 1 + round(position * (total_pages - 1))
    return f"~p.{page}"
