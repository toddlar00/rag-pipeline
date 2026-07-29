"""Pure text preparation and classification for structure-aware chunking.

This module intentionally uses only Python's standard library.  Pipeline
orchestration injects replaceable helper and logging callbacks through the
``rag.py`` compatibility facade.
"""

from __future__ import annotations

import ipaddress
import re
from collections.abc import Callable
from typing import NamedTuple
from urllib.parse import urlsplit


MIN_CHUNK_WORDS = 20
DEDUP_THRESHOLD = 0.95
CHUNKING_POLICY_VERSION = 79
NORMALIZATION_POLICY_VERSION = 3

_WHITESPACE_RE = re.compile(r"[^\S\n]+")
_NEWLINES_RE = re.compile(r"\n{3,}")
_FP_RE = re.compile(r"[^a-z0-9]")
_HEADER_FOOTER_RE = re.compile(r"^\s*\d{1,4}\s*$", re.MULTILINE)
_ZERO_WIDTH_FORMATTING_RE = re.compile(
    r"[\u200b\u200c\u200d\u2060\ufeff]"
)
_SECTION_MARKER_LINE_RE = re.compile(
    r"^[ \t]*[A-J][ \t]*(?:\n|$)", re.MULTILINE)
_DECORATIVE_SQUARE_LINE_RE = re.compile(
    r"^[ \t]*(?:\u25a0[ \t]*)+(?:\n|$)", re.MULTILINE)
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
_SPACED_URL_START_RE = re.compile(
    r"(?<![A-Za-z0-9_])(?:https?://|ht[ \t]+tps?://|"
    r"www(?=[ \t]*[.\u00b7\u2022]))",
    re.IGNORECASE,
)
_URL_ATOM_RE = re.compile(r"[A-Za-z0-9%]+")
_URL_VISIT_MARKER_RE = re.compile(
    r"[ \t]+\((?:last[ \t]+)?(?:visited|accessed|updated)\b",
    re.IGNORECASE,
)
_URL_FILE_EXTENSIONS = frozenset({
    "asp", "aspx", "htm", "html", "pdf", "php",
})
_URL_GENERIC_TLDS = frozenset({
    "ai", "app", "au", "biz", "ca", "cc", "co", "com", "de", "dev",
    "edu", "eu", "fr", "gov", "ie", "info", "int", "io", "jp", "law",
    "me", "mil", "museum", "net", "news", "nz", "online", "org", "site",
    "test", "tv", "uk", "us", "za",
})
_URL_TAIL_DELIMITERS = frozenset("/?&#=.-_~+:%")
_URL_DOT_SEPARATORS = frozenset(".\u00b7\u2022")
_UNSAFE_NORMALIZED_SCHEME_RE = re.compile(
    r"(?:javascript|vbscript|data|file)"
    r"(?::|\\:|&#(?:0*58|x0*3a);|&colon;)",
    re.IGNORECASE,
)
_MALFORMED_SCHEME_RE = re.compile(
    r"(?<![A-Za-z0-9_])h[ \t]*t[ \t]*t[ \t]*p[ \t]*s?"
    r"[ \t]*:[ \t]*/[ \t]*/",
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
_MISCLASSIFIED_SECTION_CITATION_RE = re.compile(
    r"(?:"
    r"(?:Id\.|Ibid\.)\s+at\s+\d+(?:[-\u2013]\d+)?"
    r"|\d+\s+(?:Trial|Tria1)\s+at\s+\d+(?:[-\u2013]\d+)?"
    r")\.?$",
    re.IGNORECASE,
)
_MISCLASSIFIED_NUMBERED_SENTENCE_RE = re.compile(r"^\d+\)\s+[a-z]")
_RUNNING_DIVISION_BANNER_RE = re.compile(
    r"^(?:\d{1,4}\s+)?\d{1,2}\s*[\u00b7\u2022]\s+"
    r"[A-Z][A-Z0-9 &'\u2019,.:()\-/]{4,}$"
)


class UrlRepairTransaction(NamedTuple):
    """One source span atomically accepted by the strict URL parser."""

    start: int
    end: int
    raw: str
    canonical: str
    bare: bool
    allow_unlisted_tld: bool


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

NOTES_Q_RE = re.compile(
    r"^(?:Notes?\s+(?:and|&)\s+Questions?|Questions?)\b",
    re.IGNORECASE | re.MULTILINE,
)
PROBLEM_HEADING_RE = re.compile(
    r"^\s*(?:PROBLEM\s+\d+(?:\s*-\s*\d+)+|HYPOTHETICAL\b)",
    re.IGNORECASE,
)
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


def _skip_url_space(value: str, position: int) -> int:
    while position < len(value) and value[position] in " \t":
        position += 1
    return position


def _parse_url_label(
        value: str, position: int,
) -> tuple[str, str, int, int] | None:
    """Parse one DNS label, including extraction-spaced hard hyphens."""
    match = re.match(r"[A-Za-z0-9]+", value[position:])
    if match is None:
        return None
    raw_label = match.group(0)
    compact = raw_label
    position += len(raw_label)
    whitespace_joins = 0
    while True:
        boundary_start = position
        hyphen_position = _skip_url_space(value, position)
        if (hyphen_position >= len(value)
                or value[hyphen_position] != "-"):
            break
        hyphen_end = hyphen_position + 1
        while (hyphen_end < len(value)
               and value[hyphen_end] == "-"):
            hyphen_end += 1
        atom_position = _skip_url_space(value, hyphen_end)
        atom = re.match(r"[A-Za-z0-9]+", value[atom_position:])
        if atom is None:
            break
        if hyphen_position != boundary_start or atom_position > hyphen_end:
            whitespace_joins += 1
        raw_label += value[boundary_start:atom_position] + atom.group(0)
        compact += value[hyphen_position:hyphen_end] + atom.group(0)
        position = atom_position + len(atom.group(0))
    return raw_label, compact, position, whitespace_joins


def _recognized_url_tld(raw_label: str, compact_label: str) -> bool:
    del raw_label
    return compact_label.casefold() in _URL_GENERIC_TLDS


def _peek_url_domain_label(
        value: str, position: int,
) -> tuple[str, str, int, int, bool, bool] | None:
    boundary_start = position
    dot_position = _skip_url_space(value, position)
    if (dot_position >= len(value)
            or value[dot_position] not in _URL_DOT_SEPARATORS):
        return None
    label_position = _skip_url_space(value, dot_position + 1)
    parsed = _parse_url_label(value, label_position)
    if parsed is None:
        return None
    raw_label, compact_label, end, label_joins = parsed
    whitespace_before = dot_position != boundary_start
    whitespace_after = label_position > dot_position + 1
    joins = label_joins + int(
        whitespace_before or whitespace_after
        or value[dot_position] != ".")
    return (
        raw_label,
        compact_label,
        end,
        joins,
        whitespace_before,
        whitespace_after,
    )


def _valid_compact_url(
        value: str, *, bare: bool, allow_unlisted_tld: bool = False,
) -> bool:
    candidate = f"http://{value}" if bare else value
    if (re.search(r"%(?![0-9A-Fa-f]{2})", value)
            or "\\" in value):
        return False
    try:
        parsed = urlsplit(candidate)
        hostname = parsed.hostname or ""
        port = parsed.port
    except ValueError:
        return False
    if (parsed.scheme.casefold() not in {"http", "https"}
            or not hostname or port is not None
            or parsed.username is not None or parsed.password is not None
            or len(hostname.rstrip(".")) > 253
            or " " in value or "\t" in value or "\n" in value):
        return False
    labels = hostname.split(".")
    try:
        parsed_ip = ipaddress.ip_address(hostname.strip("[]"))
    except ValueError:
        parsed_ip = None
    if parsed_ip is not None:
        return allow_unlisted_tld
    if len(labels) < 2:
        return False
    if any(re.fullmatch(
            r"[A-Za-z0-9](?:[A-Za-z0-9-]{0,61}[A-Za-z0-9])?",
            label) is None for label in labels):
        return False
    if _recognized_url_tld(labels[-1], labels[-1]):
        return True
    return bool(
        allow_unlisted_tld
        and len(labels[-1]) >= 2
        and (any(character.isalpha() for character in labels[-1])
             or labels[-1].casefold().startswith("xn--"))
    )


def _url_tail_terminal(value: str, position: int) -> bool:
    line_end = value.find("\n", position)
    if line_end < 0:
        line_end = len(value)
    remainder = value[position:line_end]
    return re.fullmatch(r"[ \t]*[.,;:!?)]?[ \t]*", remainder) is not None


def _url_followed_by_prose_boundary(value: str, position: int) -> bool:
    """Recognize closing punctuation without absorbing the next sentence."""
    line_end = value.find("\n", position)
    if line_end < 0:
        line_end = len(value)
    remainder = value[position:line_end]
    return bool(re.match(
        r"[ \t]*(?:(?:[.!?][\"'\u2019\u201d)]*|[)](?:[.!?])?)"
        r"[ \t]+(?=[A-Z])|[;,][ \t]+(?=(?:see|cf\.|accord|but see)\b))",
        remainder,
        re.IGNORECASE,
    ))


def _proven_numeric_query_parameter(value: str, position: int) -> bool:
    """Recognize an extraction-spaced ``& key = 2`` terminal parameter."""
    line_end = value.find("\n", position)
    if line_end < 0:
        line_end = len(value)
    return bool(re.match(
        r"[ \t]+&[ \t]+[A-Za-z][A-Za-z0-9._~-]*[ \t]*="
        r"[ \t]*[0-9]+(?=[ \t]*(?:[.,;!?)]|$))",
        value[position:line_end],
    ))


def _parse_url_tail(value: str, position: int) -> int | None:
    """Return the last source-proven endpoint of one same-line URL tail."""
    tail_start = position
    current = position
    parsed_any = False
    delimiter_count = 0
    ambiguous_join = False
    attached_join = False
    numeric_atom = False
    in_query = False
    in_fragment = False
    query_or_fragment_endpoint = False
    expects_query_assignment = False
    strong_end: int | None = None

    while True:
        boundary_start = current
        delimiter_position = _skip_url_space(value, current)
        if (delimiter_position >= len(value)
                or value[delimiter_position] not in _URL_TAIL_DELIMITERS):
            break
        if not parsed_any and value[delimiter_position] not in "/?#":
            break

        # A final slash is a valid endpoint.  Keep adjacent sentence
        # punctuation outside the URL, including extraction-spaced ``/ .``.
        if value[delimiter_position] == "/":
            after_slash = _skip_url_space(value, delimiter_position + 1)
            if (parsed_any
                    and (after_slash >= len(value)
                         or value[after_slash] == "\n"
                         or value[after_slash] in ".,;!\"'\u2019\u201d)"
                         or (after_slash > delimiter_position + 1
                             and value[after_slash] == "("))):
                strong_end = delimiter_position + 1
                current = strong_end
                break

        delimiters = []
        cursor = delimiter_position
        whitespace_after = False
        while cursor < len(value) and value[cursor] in _URL_TAIL_DELIMITERS:
            delimiters.append(value[cursor])
            cursor += 1
            spaced = _skip_url_space(value, cursor)
            whitespace_after = whitespace_after or spaced != cursor
            cursor = spaced
            if (cursor >= len(value)
                    or value[cursor] not in _URL_TAIL_DELIMITERS):
                break
        atom = _URL_ATOM_RE.match(value, cursor)
        if atom is None:
            break
        delimiter_text = "".join(delimiters)
        whitespace_before = delimiter_position != boundary_start
        atom_text = atom.group(0)
        proven_spaced_query_parameter = bool(
            in_query
            and "&" in delimiter_text
            and _proven_numeric_query_parameter(value, boundary_start)
        )
        if ("." in delimiter_text
                and (whitespace_before or whitespace_after)
                and atom_text.casefold() not in _URL_FILE_EXTENSIONS):
            break
        if (whitespace_before and whitespace_after
                and any(character in "/-_" for character in delimiters)):
            ambiguous_join = True
        if (whitespace_before and whitespace_after
                and (strong_end is not None or attached_join)
                and any(character in "-&_" for character in delimiters)
                and not proven_spaced_query_parameter):
            break
        proven_query_assignment = bool(
            expects_query_assignment and "=" in delimiter_text)
        if (query_or_fragment_endpoint
                and (whitespace_before or whitespace_after)
                and not proven_query_assignment
                and not proven_spaced_query_parameter):
            break
        if not whitespace_before or not whitespace_after:
            attached_join = True
        delimiter_count += len(delimiters)
        numeric_atom = numeric_atom or atom_text.isdigit()
        in_query = in_query or "?" in delimiter_text
        starts_fragment = "#" in delimiter_text
        in_fragment = in_fragment or starts_fragment
        assigns_query_value = in_query and "=" in delimiter_text
        current = atom.end()
        parsed_any = True

        if ("." in delimiter_text
                and atom_text.casefold() in _URL_FILE_EXTENSIONS):
            strong_end = current
        if (assigns_query_value or starts_fragment
                or (in_fragment
                    and not whitespace_before and not whitespace_after)):
            strong_end = current
            query_or_fragment_endpoint = True
        if assigns_query_value:
            expects_query_assignment = False
        elif (in_query and not in_fragment
                and any(character in "?&" for character in delimiter_text)):
            expects_query_assignment = True

    if not parsed_any:
        return None
    if _URL_VISIT_MARKER_RE.match(value, current):
        strong_end = current
    if _url_followed_by_prose_boundary(value, current):
        strong_end = current
    if (strong_end is None and _url_tail_terminal(value, current)
            and (not ambiguous_join
                 or delimiter_count >= 3
                 or numeric_atom
                 or attached_join)):
        strong_end = current
    if strong_end is None or strong_end <= tail_start:
        return None
    return strong_end


def _dangerous_url_continuation(value: str, position: int) -> bool:
    """Reject suffixes that could change a repaired URL's authority."""
    if position >= len(value) or value[position] == "\n":
        return False
    immediate = value[position]
    following = _skip_url_space(value, position)
    next_character = value[following] if following < len(value) else ""
    if next_character in "@\\":
        return True
    if immediate in "!$&'()*+,;=":
        following_character = (
            value[position + 1] if position + 1 < len(value) else ""
        )
        sentence_boundary = bool(
            immediate in "!,';)"
            and (not following_character
                 or following_character.isspace()
                 or following_character in ".,;:!?)]\"'\u2019\u201d")
        )
        if not sentence_boundary:
            return True
    if re.match(
            r"[!$&'()*+,;=:%._A-Za-z0-9-]*[@\\]",
            value[position:]):
        return True
    if immediate in "@\\%_" or immediate.isalnum():
        return True
    if immediate == ":":
        return bool(re.match(r":[ \t]*\d", value[position:]))
    if immediate == "." and position + 1 < len(value):
        after_dot = value[position + 1]
        return bool(
            after_dot in ".@\\%_"
            or after_dot.isalnum()
        )
    return False


def _generic_authority_has_spaced_tail(value: str, start: int) -> bool:
    """Detect credible malformed tails for authorities outside the TLD list."""
    scheme = re.match(r"https?://", value[start:], re.IGNORECASE)
    if scheme is None:
        return False
    authority_start = start + scheme.end()
    authority_match = re.match(r"[^\s/?#]+", value[authority_start:])
    if authority_match is None:
        return False
    authority = authority_match.group(0)
    authority_end = authority_start + authority_match.end()

    port_prefix = authority[:-1] if authority.endswith(":") else authority
    port_remainder = value[authority_end:]
    if (authority.endswith(":")
            and re.match(r"[ \t]+\d{1,5}(?:[ \t]+/|\b)", port_remainder)
            and _valid_general_http_authority(port_prefix)):
        return True
    spaced_port = re.match(
        r"[ \t]*:[ \t]*\d{1,5}(?=[ \t]*(?:[/\n]|$|[.,;!?)]))",
        port_remainder,
    )
    if (spaced_port is not None
            and any(character in " \t" for character in spaced_port.group(0))
            and _valid_general_http_authority(authority)):
        return True
    if not _valid_general_http_authority(authority):
        return False
    tail_end = _parse_url_tail(value, authority_end)
    return bool(
        tail_end is not None
        and any(character in " \t" for character in value[authority_end:tail_end])
    )


def _valid_general_http_authority(authority: str) -> bool:
    """Validate a compact authority for fail-closed detection, not repair."""
    if not authority or "\\" in authority or re.search(r"\s", authority):
        return False
    try:
        parsed = urlsplit(f"https://{authority}")
        hostname = parsed.hostname or ""
        _ = parsed.port
    except ValueError:
        return False
    if (not hostname or len(hostname.rstrip(".")) > 253
            or parsed.path or parsed.query or parsed.fragment):
        return False
    try:
        ipaddress.ip_address(hostname.strip("[]"))
        return True
    except ValueError:
        pass
    labels = hostname.split(".")
    return bool(
        len(labels) >= 2
        and all(re.fullmatch(
            r"[A-Za-z0-9](?:[A-Za-z0-9-]{0,61}[A-Za-z0-9])?",
            label,
        ) for label in labels)
    )


def _bare_url_follows_malformed_scheme(value: str, position: int) -> bool:
    prefix_start = max(
        0,
        value.rfind("\n", 0, position),
        value.rfind("(", 0, position),
        value.rfind("[", 0, position),
        position - 32,
    )
    prefix = value[prefix_start:position]
    return any(
        not re.fullmatch(r"https?://", match.group(0), re.IGNORECASE)
        and not prefix[match.end():].strip(" \t")
        for match in _MALFORMED_SCHEME_RE.finditer(prefix)
    )


def _process_spaced_url_candidates(
        value: str, *,
        _transactions: list[UrlRepairTransaction] | None = None,
) -> tuple[str, bool]:
    """Return transactional repairs and whether malformed URL space exists."""
    pieces: list[str] = []
    output_cursor = 0
    search_position = 0
    malformed = False
    while True:
        start = _SPACED_URL_START_RE.search(value, search_position)
        if start is None:
            break
        raw_start = start.group(0)
        bare = raw_start.casefold().startswith("www")
        if (bare
                and _bare_url_follows_malformed_scheme(
                    value, start.start())):
            malformed = True
            search_position = start.end()
            continue
        ocr_scheme = bool(re.fullmatch(
            r"ht[ \t]+tps?://", raw_start, re.IGNORECASE))
        if bare:
            label_position = start.start()
            scheme = ""
            scheme_joins = 0
        else:
            label_position = _skip_url_space(value, start.end())
            compact_scheme = re.sub(r"[ \t]+", "", raw_start).casefold()
            scheme = "https://" if compact_scheme.startswith("https") else (
                "http://")
            scheme_joins = int(ocr_scheme or label_position != start.end())

        first = _parse_url_label(value, label_position)
        if first is None:
            search_position = start.end()
            continue
        raw_label, compact_label, host_end, host_joins = first
        labels = [(raw_label, compact_label)]
        terminal_candidates: list[tuple[int, int, int, bool]] = []
        while True:
            following = _peek_url_domain_label(value, host_end)
            if following is None:
                break
            (
                raw_label,
                compact_label,
                following_end,
                joins,
                whitespace_before,
                whitespace_after,
            ) = following
            # ``host. Next`` and ``host. www.other`` are sentence boundaries,
            # not evidence that the following prose/domain extends this host.
            if (terminal_candidates
                    and ((not whitespace_before and whitespace_after)
                         or compact_label.casefold() == "www")):
                break
            host_end = following_end
            labels.append((raw_label, compact_label))
            host_joins += joins
            if _recognized_url_tld(raw_label, compact_label):
                terminal_candidates.append((
                    len(labels), host_end, host_joins, False))

        full_tail_end = _parse_url_tail(value, host_end)
        if (len(labels) >= 2
                and not _recognized_url_tld(*labels[-1])
                and (full_tail_end is not None
                     or _url_tail_terminal(value, host_end))):
            terminal_candidates.append((
                len(labels), host_end, host_joins, True))

        if not terminal_candidates:
            malformed = malformed or bool(
                len(labels) >= 2
                and (ocr_scheme or scheme_joins or host_joins
                     or full_tail_end is not None))
            if (not bare
                    and _generic_authority_has_spaced_tail(
                        value, start.start())):
                malformed = True
            search_position = start.end()
            continue

        selected_index = len(terminal_candidates) - 1
        if selected_index > 0:
            previous = terminal_candidates[selected_index - 1]
            selected = terminal_candidates[selected_index]
            selected_tail = _parse_url_tail(value, selected[1])
            if (selected[0] == len(labels)
                    and selected[2] > previous[2]
                    and selected_tail is None
                    and not _url_tail_terminal(value, selected[1])):
                selected_index -= 1
        (
            terminal_label_count,
            terminal_end,
            terminal_joins,
            allow_unlisted_tld,
        ) = terminal_candidates[selected_index]

        tail_end = _parse_url_tail(value, terminal_end)
        if (terminal_label_count < len(labels)
                and full_tail_end is not None):
            malformed = True
            search_position = start.end()
            continue
        following_position = _skip_url_space(value, terminal_end)
        unresolved_tail = bool(
            following_position < len(value)
            and value[following_position] in "/?#:"
            and tail_end is None)
        if unresolved_tail and (scheme_joins or terminal_joins):
            malformed = True
            search_position = start.end()
            continue

        candidate_end = tail_end or terminal_end
        compact_tail = (
            re.sub(r"[ \t]+", "", value[terminal_end:tail_end])
            if tail_end is not None else ""
        )
        authority = scheme + ".".join(
            label for _, label in labels[:terminal_label_count])
        replacement = authority + compact_tail
        if tail_end is not None:
            punctuation_position = _skip_url_space(value, tail_end)
            if (punctuation_position > tail_end
                    and punctuation_position < len(value)
                    and value[punctuation_position]
                    in ".,;!?\"'\u2019\u201d)"
                    and (replacement.endswith("/")
                         or _url_followed_by_prose_boundary(
                             value, tail_end))):
                candidate_end = punctuation_position
        raw_candidate = value[start.start():candidate_end]
        changed = bool(
            scheme_joins or terminal_joins
            or (tail_end is not None and compact_tail
                != value[terminal_end:tail_end])
            or replacement != raw_candidate)
        if changed and _dangerous_url_continuation(value, candidate_end):
            malformed = True
            search_position = start.end()
            continue
        if (not changed
                or not _valid_compact_url(
                    replacement,
                    bare=bare,
                    allow_unlisted_tld=allow_unlisted_tld,
                )):
            malformed = malformed or bool(
                changed or ocr_scheme or scheme_joins or terminal_joins)
            search_position = start.end()
            continue
        if _transactions is not None:
            _transactions.append(UrlRepairTransaction(
                start=start.start(),
                end=candidate_end,
                raw=raw_candidate,
                canonical=replacement,
                bare=bare,
                allow_unlisted_tld=allow_unlisted_tld,
            ))
        pieces.extend((value[output_cursor:start.start()], replacement))
        output_cursor = candidate_end
        search_position = candidate_end

    if not pieces:
        return value, malformed
    pieces.append(value[output_cursor:])
    return "".join(pieces), malformed


def _repair_spaced_url_candidates(value: str) -> str:
    """Transactionally compact only bounded, structurally valid URLs."""
    return _process_spaced_url_candidates(value)[0]


def _accepted_spaced_url_transactions(
        value: str,
) -> tuple[UrlRepairTransaction, ...]:
    """Return only source spans accepted by the unchanged strict parser."""
    transactions: list[UrlRepairTransaction] = []
    _process_spaced_url_candidates(value, _transactions=transactions)
    return tuple(transactions)


_MALFORMED_URL_LITERAL_RE = re.compile(
    r"\bperma\.cc/[ \t]+(?=[A-Za-z0-9])",
    re.IGNORECASE,
)


def _has_malformed_http_scheme(value: str) -> bool:
    for match in _MALFORMED_SCHEME_RE.finditer(value):
        if re.fullmatch(
                r"https?://", match.group(0), re.IGNORECASE):
            continue
        host_start = _skip_url_space(value, match.end())
        line_end = value.find("\n", host_start)
        if line_end < 0:
            line_end = len(value)
        if re.match(
                r"(?:[A-Za-z0-9-]+[ \t]*[.\u00b7\u2022][ \t]*)+"
                r"[A-Za-z0-9-]+",
                value[host_start:line_end]):
            return True
    return False


def _has_spaced_complex_authority(value: str) -> bool:
    """Detect spaced IPv6 or userinfo authorities that are unsafe to repair."""
    for match in re.finditer(
            r"(?<![A-Za-z0-9_])https?://", value, re.IGNORECASE):
        line_end = value.find("\n", match.end())
        if line_end < 0:
            line_end = len(value)
        remainder = value[match.end():line_end]
        authority_boundary = re.search(r"[/?#]", remainder)
        authority = (
            remainder[:authority_boundary.start()]
            if authority_boundary is not None else remainder
        ).strip(" \t")
        if (authority.startswith("[") and "]" in authority
                and ":" in authority and re.search(r"[ \t]", authority)):
            return True
        if ("@" in authority and re.search(r"[ \t]", authority)
                and re.search(
                    r"@[ \t]*(?:[A-Za-z0-9-]+[ \t]*\.[ \t]*)+"
                    r"[A-Za-z0-9-]+$",
                    authority,
                )):
            return True
    return False


def _has_spaced_unicode_http_host(value: str) -> bool:
    """Fail closed for IDN spacing until Unicode-host repair is supported."""
    for match in re.finditer(
            r"(?<![A-Za-z0-9_])https?://", value, re.IGNORECASE):
        line_end = value.find("\n", match.end())
        if line_end < 0:
            line_end = len(value)
        remainder = value[match.end():line_end]
        path_boundary = remainder.find("/")
        authority = (
            remainder[:path_boundary]
            if path_boundary >= 0 else remainder
        )
        if (any(ord(character) > 127 and character.isalpha()
                for character in authority)
                and "." in authority
                and re.search(r"[ \t]", authority)):
            return True
    return False


def has_malformed_url_spacing(value: str) -> bool:
    """Return whether text retains a proven, repairable URL-space defect."""
    repaired, malformed = _process_spaced_url_candidates(value)
    unresolved_general_authority = any(
        _generic_authority_has_spaced_tail(value, match.start())
        for match in re.finditer(
            r"(?<![A-Za-z0-9_])https?://", value, re.IGNORECASE)
    )
    return bool(
        _MALFORMED_URL_LITERAL_RE.search(value)
        or _has_malformed_http_scheme(value)
        or _has_spaced_complex_authority(value)
        or _has_spaced_unicode_http_host(value)
        or unresolved_general_authority
        or malformed
        or repaired != value
    )


def _remove_safe_zero_width_formatting(value: str) -> str:
    """Remove extraction controls unless doing so activates an unsafe scheme."""
    result = value
    search_position = 0
    while True:
        marker = _ZERO_WIDTH_FORMATTING_RE.search(result, search_position)
        if marker is None:
            return result
        position = marker.start()
        candidate = result[:position] + result[marker.end():]
        activates_unsafe_scheme = any(
            match.start() <= position < match.end()
            for match in _UNSAFE_NORMALIZED_SCHEME_RE.finditer(candidate)
        )
        if activates_unsafe_scheme:
            search_position = marker.end()
        else:
            result = candidate
            search_position = position


def is_probable_misclassified_section_header(
        text: str, *, bbox_height: float | None = None) -> bool:
    """Identify source objects whose text proves they are body, not headings.

    The rules intentionally cover only high-confidence Docling failures seen
    in source-bound casebooks: standalone pinpoint citations, a long prose
    sentence introduced by ``N)``, and unusually tall, long all-capital
    packaging copy.  The last rule requires caller-supplied geometry so
    ordinary statute, case, numbered, and all-caps headings remain eligible.
    """
    cleaned = " ".join(str(text or "").split())
    if not cleaned:
        return False
    if _MISCLASSIFIED_SECTION_CITATION_RE.fullmatch(cleaned):
        return True
    if (_MISCLASSIFIED_NUMBERED_SENTENCE_RE.match(cleaned)
            and (len(cleaned.split()) >= 10 or cleaned.endswith("."))):
        return True
    letters = "".join(character for character in cleaned
                      if character.isalpha())
    outline_prefix = re.match(
        r"^(?:Chapter|Part|Unit)\b|^§|"
        r"^(?:[IVXLCDM]+|[A-Z]|\d+|[a-z])\.",
        cleaned,
        re.IGNORECASE,
    )
    return bool(
        bbox_height is not None
        and bbox_height >= 90.0
        and len(cleaned.split()) >= 24
        and not outline_prefix
        and letters
        and letters.upper() == letters)


def is_probable_running_division_banner(text: str) -> bool:
    """Recognize a compact all-caps casebook running-division banner."""
    cleaned = " ".join(str(text or "").split())
    return bool(_RUNNING_DIVISION_BANNER_RE.fullmatch(cleaned))


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
    # PDF text layers sometimes insert invisible format controls in the middle
    # of otherwise contiguous words and URLs. These characters carry no text
    # semantics here and prevent the URL-bound repair rules below from matching.
    text = _remove_safe_zero_width_formatting(text)
    if any(0xE000 <= ord(char) <= 0xF8FF for char in text):
        pua_map = {chr(0xF643 + index): str(index) for index in range(10)}
        text = "".join(pua_map.get(char, char) for char in text)

    # These repairs target extraction artifacts with strong delimiters.  In
    # particular, do not remove ordinary spaces around hyphens: those may be
    # intentional punctuation rather than broken word wrapping.
    text = _EDITORIAL_BOILERPLATE_RE.sub("", text)
    # Casebook publishers often render the letter for a major section as a
    # separate decorative glyph. Docling can merge that glyph into prose even
    # though the adjacent section heading already carries the semantics.
    text = _SECTION_MARKER_LINE_RE.sub("", text)
    text = _DECORATIVE_SQUARE_LINE_RE.sub("", text)
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
    # Commit a spaced-URL repair only after one same-line candidate has a
    # closed host and a bounded path/query/fragment endpoint.  This prevents
    # partial cleanup from hiding malformed URLs and prevents a sentence dot
    # or prose dash after a valid host from being swallowed into the URL.
    text = _repair_spaced_url_candidates(text)
    text = _SPACED_HYPHEN_RE.sub("-", text)
    text = _BRACKETED_CONTRACTION_RE.sub(r"\1", text)
    text = _FUSED_TERM_RE.sub(
        lambda match: _FUSED_TERM_REPLACEMENTS[match.group(0).lower()], text)

    def restore_sentence_space(match: re.Match[str]) -> str:
        """Add a missing sentence space, except inside URL-like tokens."""
        before = text[:match.start()]
        token_start = max(before.rfind(" "), before.rfind("\n")) + 1
        token = before[token_start:]
        if ("://" in token
                or token.casefold() == "www"
                or token.casefold().startswith("www.")):
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
    if any(PROBLEM_HEADING_RE.search(value) for value in heading_values):
        return "problem_hypothetical"

    def is_chapter_heading(value: str) -> bool:
        return (
            bool(CHAPTER_RE.search(value))
            if chapter_heading_fn is None
            else chapter_heading_fn(value)
        )

    chapter_headings = [
        value for value in heading_values if is_chapter_heading(value)
    ]
    if chapter_headings and heading_values:
        # Introduction is a scope-sensitive type.  A chapter title such as
        # ``Chapter 1 - Introduction to Sample Systems`` remains an ancestor
        # of every
        # section in that chapter, so searching the entire heading stack would
        # incorrectly turn all descendants into introductions.  Only the local
        # leaf can establish the type: either the introductory chapter heading
        # itself or its conventional direct ``section .01`` introduction.
        local_heading = heading_values[-1]
        if (is_chapter_heading(local_heading)
                and re.search(
                    r"\bIntroduction\b", local_heading, re.IGNORECASE)):
            return "chapter_introduction"

        direct_intro = re.match(
            r"^\s*(?:\u00a7\s*)?(\d{1,2})\s*\.\s*0?1\s+"
            r"Introduction\b",
            local_heading,
            re.IGNORECASE,
        )
        chapter_number = re.match(
            r"^\s*(?:Chapter\s+)?(\d{1,2})\b",
            chapter_headings[-1],
            re.IGNORECASE,
        )
        if (direct_intro and chapter_number
                and direct_intro.group(1) == chapter_number.group(1)):
            return "chapter_introduction"

    case_markers = [
        "Justice ", "Judge ", "JUSTICE ", "JUDGE ",
        "delivered the opinion", "Opinion of the Court",
        "concurring", "dissenting", "affirmed", "reversed",
        "certiorari", "Argued ", "Decided ",
    ]
    strong_case_markers = [
        "delivered the opinion", "Opinion of the Court",
        "certiorari", "Argued ", "Decided ",
    ]
    has_case_heading = any(
        CASE_EXTRACT_RE.search(value) for value in heading_values
    )
    has_notes_heading = any(
        NOTES_Q_RE.search(value) for value in heading_values
    )
    case_marker_count = sum(
        1 for marker in case_markers if marker in text)
    if (not has_notes_heading
            and case_marker_count >= 2
            and (has_case_heading
                 or any(marker in text for marker in strong_case_markers))):
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
        # Case captions frequently wrap at column or page-layout boundaries.
        # Normalize all internal whitespace before applying signal stripping
        # and before persisting entities consumed by the publication gate.
        cleaned = " ".join(match.split()).rstrip(".,;")
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
    cleaned = re.sub(r"\s*&\s*", " & ", cleaned)
    cleaned = re.sub(r"(?<=[a-z])(?=UCC)", " ", cleaned)
    cleaned = re.sub(r"\bUCC(?=\d)", "UCC ", cleaned)
    cleaned = re.sub(r"CommonLaw", "Common Law ", cleaned)
    cleaned = re.sub(r"Approachto", "Approach to ", cleaned)
    cleaned = " ".join(cleaned.split())
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
