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
_SPACED_HYPHEN_RE = re.compile(r"(?<=[A-Za-z0-9])-\s+(?=[A-Za-z0-9])")
_MISSING_SENTENCE_SPACE_RE = re.compile(r"(?<=[a-z])\.(?=[A-Z])")
_BRACKETED_CONTRACTION_RE = re.compile(
    r"(\[[A-Za-z]\])\s+(?=(?:[a-z]'(?:re|ve|ll|d|s|t)|"
    r"'(?:m|re|ve|ll|d))\b)",
    re.IGNORECASE,
)
_PERMA_URL_RE = re.compile(
    r"\b(https?://)\s*perma\s*\.\s*cc\s*/\s*"
    r"([A-Za-z0-9]+)\s*-\s*([A-Za-z0-9]+)",
    re.IGNORECASE,
)
_BARE_PERMA_URL_RE = re.compile(
    r"\bperma\s*\.\s*cc\s*/\s*([A-Za-z0-9]+)\s*-\s*([A-Za-z0-9]+)",
    re.IGNORECASE,
)
_SPACED_GENERIC_URL_RE = re.compile(
    r"\b(https?://)\s+([^\n]*?)(?="
    r"\s+\((?:last\s+(?:visited|accessed|updated)|accessed|visited|updated)\b"
    r"|\s+\.(?=\s|$)|$)",
    re.IGNORECASE | re.MULTILINE,
)
_VISITED_URL_RE = re.compile(
    r"\b(https?://[^\s)\n]+(?:\s*[./_-]\s*[A-Za-z0-9%?=&+#~:-]+)+)"
    r"(?=\s+\((?:last\s+visited|last\s+accessed|accessed|visited)\b)",
    re.IGNORECASE,
)
_EDITORIAL_BOILERPLATE_RE = re.compile(
    r"(?:\*{1,2}\s*)?This and other authors['’] explanations draw from "
    r"the comments to the Model Rules and from other sources\.\s*"
    r"They are not comprehensive but highlight some important "
    r"(?:interpretive points|aspects of the rule)\.?(?:\s*\*{1,2})?",
    re.IGNORECASE,
)
_DOT_LEADER_SUFFIX_RE = re.compile(
    r"(?:\s*[.\u2026\u00b7]){3,}\s*(?:\d{1,4})?\s*$")
_LEAKED_PAGE_SUFFIX_RE = re.compile(
    r"\s+p(?:age)?\.?\s*\d{1,4}\s*$", re.IGNORECASE)
_DUPLICATE_CHAPTER_SUFFIX_RE = re.compile(
    r"\s+(?=Chapter\s+\d{1,2}\s*(?:[·\-\xb7\u2022–—:]|$)).*$",
    re.IGNORECASE,
)
_TABULAR_HEADING_RE = re.compile(
    r"(?:\bN/?A\b.*\d|\d(?:\.\d+)?\s+\d(?:\.\d+)?\s+\bN/?A\b)",
    re.IGNORECASE,
)

_STRUCTURAL_PATTERNS = [
    re.compile(r"^(Table of )?Contents$", re.IGNORECASE | re.MULTILINE),
    re.compile(
        r"^(?:Summary|Brief|Short|Detailed) (?:of )?Contents$",
        re.IGNORECASE | re.MULTILINE,
    ),
    re.compile(r"^Table of Problems$", re.IGNORECASE | re.MULTILINE),
    re.compile(r"^Table of Cases$", re.IGNORECASE | re.MULTILINE),
    re.compile(
        r"^Table of (?:Rules|Authorities|Restatements|Statutes|Bar "
        r"Opinions|Standards)(?:,.*)?$",
        re.IGNORECASE | re.MULTILINE,
    ),
    re.compile(r"^Index$", re.IGNORECASE | re.MULTILINE),
    re.compile(r"^Acknowledgments?$", re.IGNORECASE | re.MULTILINE),
    re.compile(r"^About the Authors?$", re.IGNORECASE | re.MULTILINE),
    re.compile(r"^About .*Publishing$", re.IGNORECASE | re.MULTILINE),
    re.compile(
        r"^Major products, programs, and initiatives include:?$",
        re.IGNORECASE | re.MULTILINE,
    ),
    re.compile(r"^Textual material$", re.IGNORECASE | re.MULTILINE),
    re.compile(r"^Images$", re.IGNORECASE | re.MULTILINE),
    re.compile(
        r"^Design \(chapter opener graphic\)$",
        re.IGNORECASE | re.MULTILINE,
    ),
    re.compile(
        r"^(Series |Editorial )?Advisory Board$",
        re.IGNORECASE | re.MULTILINE,
    ),
    re.compile(r"^Editorial Advisors?$", re.IGNORECASE | re.MULTILINE),
]
_STRUCTURAL_TEXT_PATTERNS = [
    re.compile(
        r"Copyright\s+(?:©|\(c\)).{0,250}\bAll rights reserved\b",
        re.IGNORECASE | re.DOTALL,
    ),
    re.compile(
        r"\bNo part of this publication may be reproduced or transmitted\b",
        re.IGNORECASE,
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
_CASE_CAP_WORD = r"(?:[A-Z][A-Za-z&'\u2019.\-]*|[A-Z]{2,})"
_CASE_CONNECTOR = r"(?:of|the|for|and|&|ex\s+rel\.)"
_CASE_PARTY = (
    rf"{_CASE_CAP_WORD}(?:\s+{_CASE_CAP_WORD}|"
    rf"\s+{_CASE_CONNECTOR}\s+{_CASE_CAP_WORD}){{0,8}}"
)
CASE_EXTRACT_RE = re.compile(
    rf"\b((?:In re|Ex parte)\s+{_CASE_PARTY}|"
    rf"{_CASE_PARTY}\s+v\.\s+{_CASE_PARTY})"
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

_RULE_HEADING_RE = re.compile(
    r"(?:rule language|statutory text|"
    r"text of (?:the )?(?:rule|statute)(?:\s+\d+(?:\.\d+)*)?|"
    r"model rule\s+\d+(?:\.\d+)*|code provision|regulatory text)\s*\**",
    re.IGNORECASE,
)
_EXPLANATION_HEADING_RE = re.compile(
    r"(?:authors?['’] explanation|commentary|analysis)\s*\**",
    re.IGNORECASE,
)
_STATUTE_OPENING_RE = re.compile(
    r"^\s*(?:Under\s+)?(?:\d+\s+U\.S\.C\.\s+§|"
    r"(?:Model\s+)?Rule\s+\d+(?:\.\d+)*\b|"
    r"Rule language\s*\**(?:\s|$)|"
    r"§+\s*\d+)",
    re.IGNORECASE,
)
_CASE_ARTICLE_PREFIX_RE = re.compile(
    r"^(?:(?:A|An|The)\s+)?(?:Study|Studies|Fifty\s+Years|Legacy|Lessons|"
    r"Impact|Effects?)\b.*\b(?:How|After|From)\s+(.+)$",
    re.IGNORECASE,
)
_CASE_ARTICLE_SUFFIX_RE = re.compile(
    r"\s+(?:Affected|Changed|Transformed|Revisited|Reshaped|Aftermath)\b.*$"
)
_FUSED_TERM_REPLACEMENTS = {
    "clientlawyer": "client-lawyer",
    "lawyerclient": "lawyer-client",
    "plaintiffdefendant": "plaintiff-defendant",
    "threejudge": "three-judge",
    "newyork": "New York",
    "sarbanesoxley": "Sarbanes-Oxley",
    "wendyweikal": "Wendy Weikal",
    "marzanolesnevich": "Marzano-Lesnevich",
    "timesleader": "Times Leader",
    "lawbloomington": "Law Bloomington",
}
_FUSED_TERM_RE = re.compile(
    r"\b(?:" + "|".join(map(re.escape, _FUSED_TERM_REPLACEMENTS)) + r")\b",
    re.IGNORECASE,
)


TextTransformFn = Callable[[str], str]
StructuralContentFn = Callable[[str, list[str] | None], bool]
ChapterHeadingFn = Callable[[str], bool]
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

    # These repairs target extraction artifacts with strong delimiters.  In
    # particular, do not remove ordinary spaces around hyphens: those may be
    # intentional punctuation rather than broken word wrapping.
    text = _EDITORIAL_BOILERPLATE_RE.sub("", text)
    text = _PERMA_URL_RE.sub(
        lambda match: (
            f"{match.group(1)}perma.cc/"
            f"{match.group(2)}-{match.group(3)}"
        ),
        text,
    )
    text = _BARE_PERMA_URL_RE.sub(
        lambda match: f"perma.cc/{match.group(1)}-{match.group(2)}",
        text,
    )
    text = _SPACED_GENERIC_URL_RE.sub(
        lambda match: match.group(1) + re.sub(r"\s+", "", match.group(2)),
        text,
    )
    text = _VISITED_URL_RE.sub(
        lambda match: re.sub(r"\s+", "", match.group(1)),
        text,
    )
    text = _SPACED_HYPHEN_RE.sub("-", text)
    text = _BRACKETED_CONTRACTION_RE.sub(r"\1", text)
    text = _FUSED_TERM_RE.sub(
        lambda match: _FUSED_TERM_REPLACEMENTS[match.group(0).lower()], text)

    def restore_sentence_space(match: re.Match[str]) -> str:
        """Add a missing sentence space, except inside URL-like tokens."""
        before = text[:match.start()]
        token_start = max(before.rfind(" "), before.rfind("\n")) + 1
        token = before[token_start:]
        if "://" in token or token.lower().startswith("www."):
            return "."
        return ". "

    text = _MISSING_SENTENCE_SPACE_RE.sub(restore_sentence_space, text)
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
    """Remove lines that duplicate another line within *window* lines above.

    The rule targets repeated page furniture in extracted prose.  Markdown
    table rows are exempt: a table may legitimately repeat a data row, and two
    adjacent tables repeat their header and separator, so applying the rule
    there deleted real cells and merged a following table into the previous
    one's body.
    """
    lines = text.split("\n")
    out: list[str] = []
    for line in lines:
        stripped = line.strip()
        if not stripped or stripped.startswith("|"):
            out.append(line)
            continue
        start = max(0, len(out) - window)
        duplicate = any(previous.strip() == stripped for previous in out[start:])
        if not duplicate:
            out.append(line)
    return "\n".join(out)


def _is_structural_content(
        text: str, headings: list[str] | None, *,
        structural_patterns: list[re.Pattern] | tuple[re.Pattern, ...] | None = None,
) -> bool:
    """Detect TOC, index, title pages, copyright — not substantive content."""
    patterns = (
        _STRUCTURAL_PATTERNS
        if structural_patterns is None else structural_patterns)
    candidates = [clean_heading_text(heading) for heading in (headings or [])]
    candidates.append(text)
    if any(
            pattern.search(candidate)
            for candidate in candidates if candidate
            for pattern in patterns):
        return True
    if any(pattern.search(text) for pattern in _STRUCTURAL_TEXT_PATTERNS):
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
        removed_callback: RemovedCallback | None = None,
        can_deduplicate_fn: Callable[[dict, dict], bool] | None = None,
        ) -> list[dict]:
    """Remove near-duplicate chunks based on trigram Jaccard similarity."""
    if not chunks:
        return chunks

    fingerprint_fn = (
        _text_fingerprint
        if text_fingerprint_fn is None else text_fingerprint_fn
    )
    trigrams_fn = _make_trigrams if make_trigrams_fn is None else make_trigrams_fn
    kept: list[dict] = []
    seen: list[tuple[int, frozenset[str], dict]] = []
    for chunk in chunks:
        fingerprint = fingerprint_fn(chunk["text"])
        fingerprint_length = len(fingerprint)
        trigrams_a = trigrams_fn(fingerprint)
        if not trigrams_a:
            kept.append(chunk)
            continue

        is_duplicate = False
        for seen_length, trigrams_b, seen_chunk in seen:
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
                break

        if not is_duplicate:
            kept.append(chunk)
            seen.append((fingerprint_length, trigrams_a, chunk))

    removed = len(chunks) - len(kept)
    if removed > 0 and removed_callback is not None:
        removed_callback(removed)
    return kept


def classify_content_type(
        text: str, headings: list[str] | None, *,
        structural_content_fn: StructuralContentFn | None = None,
        chapter_heading_fn: ChapterHeadingFn | None = None) -> str:
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
    heading_values = [
        clean_heading_text(heading) for heading in (headings or [])
    ]
    if NOTES_Q_RE.search(heading_text) or NOTES_Q_RE.search(text[:200]):
        return "notes_and_questions"
    is_chapter_heading = (
        bool(CHAPTER_RE.search(heading_text))
        if chapter_heading_fn is None
        else any(chapter_heading_fn(value) for value in heading_values)
    )
    if is_chapter_heading:
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

    lines = text.strip().split("\n")
    pipe_lines = sum(1 for line in lines if line.count("|") >= 2)
    if len(lines) > 2 and pipe_lines > len(lines) * 0.3:
        return "table"
    tab_lines = [line for line in lines if line.count("\t") >= 2]
    if len(tab_lines) >= 3:
        return "table"

    # An explanatory lane often quotes several rules.  Its explicit heading
    # is a more reliable signal than incidental citations in the prose.
    if any(_EXPLANATION_HEADING_RE.fullmatch(value)
           for value in heading_values if value):
        return "author_narrative"
    if any(_RULE_HEADING_RE.fullmatch(value)
           for value in heading_values if value):
        return "statutory_excerpt"
    if _STATUTE_OPENING_RE.search(text[:500]):
        return "statutory_excerpt"

    # A narrative chunk may contain several numbered footnotes.  Only label a
    # chunk as a footnote when the first non-whitespace content is itself a
    # footnote marker, then require citation-language corroboration.
    opens_with_footnote = bool(_FOOTNOTE_NUM_RE.match(text))
    if opens_with_footnote:
        numbered_footnotes = len(_FOOTNOTE_NUM_RE.findall(text))
        citation_markers = sum(
            1 for marker in _FOOTNOTE_CITE_MARKERS if marker in text)
        if numbered_footnotes >= 2 and citation_markers >= 3:
            return "footnote"
        if (citation_markers >= 4 and len(text.split()) < 200):
            return "footnote"
    return "author_narrative"


def extract_case_names(text: str) -> list[str]:
    """Extract case names mentioned in the chunk."""
    matches = CASE_EXTRACT_RE.findall(text)
    seen = set()
    result = []
    signal_prefixes = [
        "See ", "see ", "Cf. ", "cf. ", "Compare ", "In ",
        "But see ", "E.g., ", "After ", "How ",
    ]
    for match in matches:
        cleaned = match.strip().rstrip(".,;")
        for prefix in signal_prefixes:
            if (prefix == "In "
                    and cleaned.lower().startswith("in re ")):
                continue
            if cleaned.startswith(prefix):
                cleaned = cleaned[len(prefix):]
        if " v. " in cleaned:
            left, right = cleaned.split(" v. ", 1)
            article_prefix = _CASE_ARTICLE_PREFIX_RE.match(left)
            if article_prefix:
                right = _CASE_ARTICLE_SUFFIX_RE.sub("", right)
                cleaned = f"{article_prefix.group(1)} v. {right}"
        if "The Rest" in cleaned or "The Story" in cleaned:
            continue
        if cleaned not in seen and 5 < len(cleaned) <= 160:
            seen.add(cleaned)
            result.append(cleaned)
    return result[:10]


def clean_heading_text(text: str) -> str:
    """Remove extraction-only suffixes from a section heading.

    The cleanup is intentionally heading-specific: applying page-number or
    dot-leader rules to ordinary prose could damage citations and ellipses.
    """
    if not text:
        return ""
    cleaned = " ".join(
        text.replace("\xa0", " ").replace("\u2026", "...").split())
    cleaned = _SPACED_HYPHEN_RE.sub("-", cleaned)
    cleaned = re.sub(
        r"\bAconcluding\b", "A concluding", cleaned,
        flags=re.IGNORECASE,
    )
    is_chapter_heading = bool(re.match(
        r"^Chapter\s+\d{1,2}\b", cleaned, re.IGNORECASE))
    if not is_chapter_heading:
        cleaned = _DUPLICATE_CHAPTER_SUFFIX_RE.sub("", cleaned)
    cleaned = _DOT_LEADER_SUFFIX_RE.sub("", cleaned)
    cleaned = _LEAKED_PAGE_SUFFIX_RE.sub("", cleaned)
    return cleaned.rstrip(" .\u2026\u00b7").strip()


def build_section_path(headings: list[str] | None) -> str:
    if not headings:
        return ""
    cleaned = [clean_heading_text(heading) for heading in headings]
    return " → ".join(
        heading for heading in cleaned
        if heading and not _TABULAR_HEADING_RE.search(heading))


def estimate_page_range(chunk_index: int, total_chunks: int,
                        total_pages: int) -> str:
    """Rough page estimate based on chunk position (fallback only)."""
    if total_chunks <= 1 or total_pages <= 1:
        return "~p.1"
    bounded_index = min(max(chunk_index, 0), total_chunks - 1)
    position = bounded_index / (total_chunks - 1)
    page = 1 + round(position * (total_pages - 1))
    return f"~p.{page}"
