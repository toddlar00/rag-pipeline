"""Immutable, attested document-structure policies.

The pipeline keeps structure policy separate from content classification.  A
profile describes only how a publisher labels front/back matter, divisions,
and table-of-contents hierarchy.  Callers resolve one profile and pass it
explicitly; there is deliberately no mutable process-wide active profile.
"""

from __future__ import annotations

import hashlib
import json
import re
from dataclasses import dataclass
from types import MappingProxyType
from typing import Mapping


PROFILE_SCHEMA_VERSION = 1
DEFAULT_STRUCTURE_PROFILE = "us-law-casebook-v1"
_PROFILE_NAME_RE = re.compile(r"^[a-z0-9]+(?:-[a-z0-9]+)*$")
_DIGEST_RE = re.compile(r"^[0-9a-f]{64}$")
_DIVISION_CONTEXTS = frozenset({
    "section_boundary",
    "running_header",
    "toc_entry",
    "canonical_title",
    "chunk_heading",
    "cross_reference",
})
_NUMBER_STYLES = frozenset({"arabic", "roman", "word"})


@dataclass(frozen=True, slots=True)
class DivisionRule:
    """One context-qualified division-heading pattern.

    Patterns must contain a named ``number`` group.  Named ``kind`` and
    ``title`` groups are optional; their declared fallbacks keep matching
    deterministic when a publisher omits one of them.
    """

    contexts: frozenset[str]
    pattern: str
    default_kind: str
    title_group: str | None = "title"


@dataclass(frozen=True, slots=True)
class SectionRule:
    """A named front/back-matter section and its publication policy."""

    key: str
    patterns: tuple[str, ...]
    exclude_from_corpus: bool
    toc_seed: bool = False
    minimum_page_fraction: float | None = None


@dataclass(frozen=True, slots=True)
class HierarchyRule:
    """Map a TOC title pattern to a normalized hierarchy level."""

    level: int
    pattern: str


@dataclass(frozen=True, slots=True)
class StructureProfile:
    """Complete immutable policy for one reviewed document family."""

    schema_version: int
    name: str
    revision: int
    document_description: str
    division_rules: tuple[DivisionRule, ...]
    section_rules: tuple[SectionRule, ...]
    toc_skip_patterns: tuple[str, ...]
    toc_entry_hint_patterns: tuple[str, ...]
    hierarchy_rules: tuple[HierarchyRule, ...]
    canonical_title_template: str
    allowed_number_styles: tuple[str, ...]
    maximum_division_ordinal: int
    unknown_layout_policy: str = "error"


@dataclass(frozen=True, slots=True)
class DivisionMatch:
    """A division match retaining normalized and publisher display forms."""

    ordinal: int
    raw_number: str
    kind: str
    title: str
    matched_text: str


_WORD_NUMBERS = {
    "one": 1,
    "two": 2,
    "three": 3,
    "four": 4,
    "five": 5,
    "six": 6,
    "seven": 7,
    "eight": 8,
    "nine": 9,
    "ten": 10,
    "eleven": 11,
    "twelve": 12,
    "thirteen": 13,
    "fourteen": 14,
    "fifteen": 15,
    "sixteen": 16,
    "seventeen": 17,
    "eighteen": 18,
    "nineteen": 19,
    "twenty": 20,
    "twenty-one": 21,
    "twenty-two": 22,
    "twenty-three": 23,
    "twenty-four": 24,
    "twenty-five": 25,
    "twenty-six": 26,
    "twenty-seven": 27,
    "twenty-eight": 28,
    "twenty-nine": 29,
    "thirty": 30,
    "forty": 40,
    "fifty": 50,
}
_WORD_NUMBER_PATTERN = "(?:" + "|".join(sorted(
    (re.escape(value).replace(r"\-", "[- ]") for value in _WORD_NUMBERS),
    key=len,
    reverse=True,
)) + ")"
_ROMAN_DIGITS = (
    (1000, "M"), (900, "CM"), (500, "D"), (400, "CD"),
    (100, "C"), (90, "XC"), (50, "L"), (40, "XL"),
    (10, "X"), (9, "IX"), (5, "V"), (4, "IV"), (1, "I"),
)


def _roman_numeral(value: int) -> str:
    output = []
    remaining = value
    for amount, symbol in _ROMAN_DIGITS:
        while remaining >= amount:
            output.append(symbol)
            remaining -= amount
    return "".join(output)


def parse_division_ordinal(
        raw: str, *, allowed_styles: tuple[str, ...] = (
            "arabic", "roman", "word"), maximum: int = 50,
) -> int | None:
    """Normalize a reviewed Arabic, Roman, or English division ordinal."""
    if not isinstance(raw, str) or not raw.strip():
        return None
    token = re.sub(r"\s+", " ", raw.strip()).casefold()
    styles = frozenset(allowed_styles)
    value: int | None = None
    if "arabic" in styles and token.isascii() and token.isdigit():
        value = int(token)
    elif "word" in styles:
        normalized_word = token.replace(" ", "-")
        value = _WORD_NUMBERS.get(normalized_word)
        if value is None and "-" in normalized_word:
            tens, _, ones = normalized_word.partition("-")
            tens_value = _WORD_NUMBERS.get(tens)
            ones_value = _WORD_NUMBERS.get(ones)
            if tens_value in {20, 30, 40} and ones_value in range(1, 10):
                value = tens_value + ones_value
    if value is None and "roman" in styles:
        roman = token.upper()
        if re.fullmatch(r"[IVXLCDM]+", roman):
            cursor = 0
            value = 0
            for amount, symbol in _ROMAN_DIGITS:
                while roman[cursor:cursor + len(symbol)] == symbol:
                    value += amount
                    cursor += len(symbol)
            if cursor != len(roman) or _roman_numeral(value) != roman:
                value = None
    if value is None or value < 1 or value > maximum:
        return None
    return value


def _profile_payload(profile: StructureProfile) -> dict:
    return {
        "schema_version": profile.schema_version,
        "name": profile.name,
        "revision": profile.revision,
        "document_description": profile.document_description,
        "division_rules": [
            {
                "contexts": sorted(rule.contexts),
                "pattern": rule.pattern,
                "default_kind": rule.default_kind,
                "title_group": rule.title_group,
            }
            for rule in profile.division_rules
        ],
        "section_rules": [
            {
                "key": rule.key,
                "patterns": list(rule.patterns),
                "exclude_from_corpus": rule.exclude_from_corpus,
                "toc_seed": rule.toc_seed,
                "minimum_page_fraction": rule.minimum_page_fraction,
            }
            for rule in profile.section_rules
        ],
        "toc_skip_patterns": list(profile.toc_skip_patterns),
        "toc_entry_hint_patterns": list(profile.toc_entry_hint_patterns),
        "hierarchy_rules": [
            {"level": rule.level, "pattern": rule.pattern}
            for rule in profile.hierarchy_rules
        ],
        "canonical_title_template": profile.canonical_title_template,
        "allowed_number_styles": list(profile.allowed_number_styles),
        "maximum_division_ordinal": profile.maximum_division_ordinal,
        "unknown_layout_policy": profile.unknown_layout_policy,
    }


def profile_sha256(profile: StructureProfile) -> str:
    """Return a canonical digest over every behavior-defining field."""
    payload = json.dumps(
        _profile_payload(profile), ensure_ascii=True, sort_keys=True,
        separators=(",", ":"), allow_nan=False,
    ).encode("utf-8")
    return hashlib.sha256(payload).hexdigest()


def profile_provenance(profile: StructureProfile) -> dict:
    """Return the strict receipt embedded in chunk completion evidence."""
    return {
        "schema_version": profile.schema_version,
        "name": profile.name,
        "revision": profile.revision,
        "sha256": profile_sha256(profile),
    }


def _validate_profile(profile: StructureProfile) -> None:
    if (not isinstance(profile.schema_version, int)
            or isinstance(profile.schema_version, bool)
            or profile.schema_version != PROFILE_SCHEMA_VERSION):
        raise ValueError("unsupported structure-profile schema version")
    if not _PROFILE_NAME_RE.fullmatch(profile.name):
        raise ValueError("invalid structure-profile name")
    if (not isinstance(profile.revision, int)
            or isinstance(profile.revision, bool) or profile.revision < 1):
        raise ValueError("structure-profile revision must be positive")
    if not profile.document_description.strip():
        raise ValueError("structure-profile description must not be empty")
    if profile.unknown_layout_policy != "error":
        raise ValueError("unsupported unknown-layout policy")
    styles = tuple(profile.allowed_number_styles)
    if (not styles or len(set(styles)) != len(styles)
            or not set(styles).issubset(_NUMBER_STYLES)):
        raise ValueError("invalid division-number styles")
    if (not isinstance(profile.maximum_division_ordinal, int)
            or isinstance(profile.maximum_division_ordinal, bool)
            or not 1 <= profile.maximum_division_ordinal <= 3999):
        raise ValueError("invalid maximum division ordinal")
    if not profile.division_rules:
        raise ValueError("structure profile requires division rules")
    for rule in profile.division_rules:
        if (not rule.contexts
                or not rule.contexts.issubset(_DIVISION_CONTEXTS)):
            raise ValueError("invalid division-rule contexts")
        compiled = re.compile(rule.pattern, re.IGNORECASE)
        if "number" not in compiled.groupindex:
            raise ValueError("division rule requires a named number group")
        if (rule.title_group is not None
                and rule.title_group not in compiled.groupindex):
            raise ValueError("division rule declares a missing title group")
        if not rule.default_kind.strip():
            raise ValueError("division rule requires a default kind")
    section_keys = [rule.key for rule in profile.section_rules]
    if len(section_keys) != len(set(section_keys)):
        raise ValueError("duplicate structure-profile section key")
    if not any(rule.toc_seed for rule in profile.section_rules):
        raise ValueError("structure profile requires a TOC seed rule")
    for rule in profile.section_rules:
        if not re.fullmatch(r"[a-z][a-z0-9_]*", rule.key):
            raise ValueError("invalid structure-profile section key")
        if not rule.patterns:
            raise ValueError("section rule requires at least one pattern")
        for pattern in rule.patterns:
            re.compile(pattern, re.IGNORECASE)
        fraction = rule.minimum_page_fraction
        if fraction is not None and not 0.0 <= fraction <= 1.0:
            raise ValueError("section page fraction must be between zero and one")
    for pattern in (
            profile.toc_skip_patterns + profile.toc_entry_hint_patterns):
        re.compile(pattern, re.IGNORECASE)
    for rule in profile.hierarchy_rules:
        if not 2 <= rule.level <= 5:
            raise ValueError("hierarchy level must be between two and five")
        re.compile(rule.pattern, re.IGNORECASE)
    fields = set(re.findall(
        r"\{([A-Za-z_][A-Za-z0-9_]*)\}",
        profile.canonical_title_template))
    if not {"kind", "number", "title"}.issubset(fields):
        raise ValueError("canonical title template is missing required fields")


def _division_rule(
        contexts: tuple[str, ...], pattern: str, default_kind: str,
        title_group: str | None = "title",
) -> DivisionRule:
    return DivisionRule(
        contexts=frozenset(contexts), pattern=pattern,
        default_kind=default_kind, title_group=title_group)


_LEGAL_PROFILE = StructureProfile(
    schema_version=PROFILE_SCHEMA_VERSION,
    name=DEFAULT_STRUCTURE_PROFILE,
    revision=1,
    document_description=(
        "United States law-school casebooks with Arabic chapters and "
        "publisher front/back matter"),
    division_rules=(
        _division_rule(
            ("section_boundary", "running_header"),
            r"^Chapter\s+(?P<number>\d{1,2})\s*"
            r"[:\-\xb7\u00b7\u2022\u2013\u2014]?\s+(?P<title>.+)$",
            "Chapter"),
        _division_rule(
            ("section_boundary", "running_header"),
            r"^(?P<number>\d{1,2})\s*"
            r"[\xb7\u00b7\u2022\-\u2013\u2014]\s+(?P<title>.+)$",
            "Chapter"),
        _division_rule(
            ("section_boundary", "running_header"),
            rf"^Chapter\s+(?P<number>{_WORD_NUMBER_PATTERN})\s*"
            r"[:\-\xb7\u00b7\u2022\u2013\u2014]?\s+(?P<title>.+)$",
            "Chapter"),
        _division_rule(
            ("section_boundary", "running_header"),
            r"^(?P<kind>Part|Unit)\s+(?P<number>[A-Za-z0-9-]+)\s*"
            r"[:\-\xb7\u00b7\u2022\u2013\u2014]?\s+(?P<title>.+)$",
            "Part"),
        _division_rule(
            ("toc_entry", "canonical_title"),
            r"^(?P<kind>Chapter|Part|Unit)\s+(?P<number>\d{1,2})\b"
            r"(?P<title>.*)$",
            "Chapter"),
        _division_rule(
            ("chunk_heading",),
            r"^(?:(?P<kind>Chapter)\s+)?(?P<number>\d{1,2})\s*"
            r"[\xb7\u00b7\u2022\-\u2013\u2014]\s*(?P<title>.+)$",
            "Chapter"),
        _division_rule(
            ("chunk_heading",),
            r"^(?:(?P<kind>CHAPTER)\s+)?(?P<number>\d{1,2})\s{2,}"
            r"(?P<title>[A-Z][A-Z\s,]{4,})$",
            "Chapter"),
        _division_rule(
            ("cross_reference",),
            r"\b(?P<kind>Chapter)\s+(?P<number>\d+)"
            r"(?:,\s*Section\s*(?P<title>[A-Z](?:\.\d+)*))?",
            "Chapter"),
    ),
    section_rules=(
        SectionRule("contents", (
            r"contents", r"brief contents", r"short contents"), True,
            toc_seed=True),
        SectionRule("summary", (r"summary of contents",), True,
                    toc_seed=True),
        SectionRule("toc", (r"table of contents", r"detailed contents"),
                    True, toc_seed=True),
        SectionRule("problems", (r"table of problems",), True),
        SectionRule("acknowledgments", (r"acknowledgments?",), True),
        SectionRule("about_authors", (r"about the authors?",), True),
        SectionRule("table_of_cases", (r"table of cases",), True),
        SectionRule("table_of_rules", (
            r"table of (?:rules|authorities|restatements|statutes|"
            r"bar opinions|standards)(?:,.*)?",), True),
        SectionRule("index", (r"index", r"subject index"), True,
                    minimum_page_fraction=0.5),
        SectionRule("editorial", (
            r"editorial advisors?", r"(?:series |editorial )?advisory board"),
            True),
        SectionRule("publisher", (r"about .* publishing",), True),
        SectionRule("credits", (
            r"textual material", r"images",
            r"design \(chapter opener graphic\)"), True),
    ),
    toc_skip_patterns=(
        r"^(table of )?contents$", r"^summary of contents$",
        r"^detailed contents$", r"^brief contents$", r"^page$",
    ),
    toc_entry_hint_patterns=(
        r"(?:chapter|part|preface|appendix|index|acknowledgment|"
        r"table of cases)\b",
    ),
    hierarchy_rules=(
        HierarchyRule(2, r"^[A-Z]\.\s"),
        HierarchyRule(3, r"^\d+\.\s"),
        HierarchyRule(4, r"^[a-z]\.\s"),
        HierarchyRule(3, r"^[ivxlc]+\.\s"),
        HierarchyRule(5, r"(?:\bv\.\s|\sv\s)"),
    ),
    canonical_title_template="{kind} {number}: {title}",
    allowed_number_styles=("arabic", "roman", "word"),
    maximum_division_ordinal=50,
)


_ROMAN_PART_PROFILE = StructureProfile(
    schema_version=PROFILE_SCHEMA_VERSION,
    name="roman-parts-book-v1",
    revision=1,
    document_description=(
        "General scholarly books whose primary divisions are Roman-numbered "
        "Parts and whose back matter includes bibliography or glossary"),
    division_rules=(
        _division_rule(
            ("section_boundary", "running_header", "toc_entry",
             "canonical_title", "chunk_heading"),
            r"^(?P<kind>Part)\s+(?P<number>[IVXLCDM]+)\b"
            r"(?:\s*[:\-\u2013\u2014]\s*(?P<title>.+))?$",
            "Part"),
        _division_rule(
            ("cross_reference",),
            r"\b(?P<kind>Part)\s+(?P<number>[IVXLCDM]+)\b",
            "Part", title_group=None),
    ),
    section_rules=(
        SectionRule("contents", (r"contents",), True, toc_seed=True),
        SectionRule("toc", (r"table of contents",), True, toc_seed=True),
        SectionRule("preface", (r"preface", r"foreword"), True),
        SectionRule("acknowledgments", (r"acknowledgments?",), True),
        SectionRule("about_authors", (
            r"about the (?:authors?|contributors?)",), True),
        SectionRule("bibliography", (
            r"bibliography", r"references", r"works cited"), True,
                    minimum_page_fraction=0.5),
        SectionRule("glossary", (r"glossary",), True,
                    minimum_page_fraction=0.5),
        SectionRule("index", (r"index", r"subject index"), True,
                    minimum_page_fraction=0.5),
    ),
    toc_skip_patterns=(
        r"^(table of )?contents$", r"^page$",
    ),
    toc_entry_hint_patterns=(
        r"(?:part|preface|foreword|appendix|bibliography|glossary|index)\b",
    ),
    hierarchy_rules=(
        HierarchyRule(2, r"^[IVXLCDM]+\.\s"),
        HierarchyRule(3, r"^[A-Z]\.\s"),
        HierarchyRule(4, r"^\d+\.\s"),
        HierarchyRule(5, r"^[a-z]\.\s"),
    ),
    canonical_title_template="{kind} {number}: {title}",
    allowed_number_styles=("roman",),
    maximum_division_ordinal=50,
)


def _build_registry() -> Mapping[str, StructureProfile]:
    profiles = (_LEGAL_PROFILE, _ROMAN_PART_PROFILE)
    registry: dict[str, StructureProfile] = {}
    for profile in profiles:
        _validate_profile(profile)
        if profile.name in registry:
            raise ValueError(f"duplicate structure profile: {profile.name}")
        registry[profile.name] = profile
    return MappingProxyType(registry)


STRUCTURE_PROFILES = _build_registry()


def profile_names() -> tuple[str, ...]:
    """Return registered profile names in deterministic display order."""
    return tuple(STRUCTURE_PROFILES)


def get_profile(value: str | StructureProfile) -> StructureProfile:
    """Resolve a registered profile without changing process-global state."""
    if isinstance(value, StructureProfile):
        registered = STRUCTURE_PROFILES.get(value.name)
        if registered is None or profile_sha256(registered) != profile_sha256(value):
            raise ValueError(f"unregistered structure profile: {value.name}")
        return registered
    if not isinstance(value, str):
        raise TypeError("structure profile must be a registered name")
    try:
        return STRUCTURE_PROFILES[value]
    except KeyError as exc:
        choices = ", ".join(profile_names())
        raise ValueError(
            f"unknown structure profile {value!r}; choose one of: {choices}"
        ) from exc


def fallback_division_title(
        profile: str | StructureProfile, ordinal: int,
) -> str:
    """Format a profile-native division reference when no title was found."""
    resolved = get_profile(profile)
    if (not isinstance(ordinal, int) or isinstance(ordinal, bool)
            or not 1 <= ordinal <= resolved.maximum_division_ordinal):
        raise ValueError("division ordinal is outside the profile range")
    if "arabic" in resolved.allowed_number_styles:
        number = str(ordinal)
    elif "roman" in resolved.allowed_number_styles:
        number = _roman_numeral(ordinal)
    else:
        candidates = [
            word for word, value in _WORD_NUMBERS.items() if value == ordinal
        ]
        if not candidates:
            raise ValueError("profile cannot display this division ordinal")
        number = min(candidates, key=len).replace("-", " ").title()
    rule = next(
        (candidate for candidate in resolved.division_rules
         if "canonical_title" in candidate.contexts),
        resolved.division_rules[0],
    )
    return f"{rule.default_kind.title()} {number}"


def contains_division_subnumber(
        text: str, division: DivisionMatch,
) -> bool:
    """Detect publisher or normalized ``N-1`` subdivision artifacts."""
    if not isinstance(text, str):
        return False
    variants = {str(division.ordinal), division.raw_number.strip()}
    for value in variants:
        if not value:
            continue
        pattern = re.escape(value).replace(r"\ ", r"\s+")
        if re.search(
                rf"(?<![A-Za-z0-9]){pattern}-\d+\b", text,
                re.IGNORECASE):
            return True
    return False


def profile_from_provenance(receipt: object) -> StructureProfile:
    """Validate a strict receipt and return its currently registered policy."""
    if (not isinstance(receipt, dict)
            or set(receipt) != {"schema_version", "name", "revision", "sha256"}
            or not isinstance(receipt.get("schema_version"), int)
            or isinstance(receipt.get("schema_version"), bool)
            or not isinstance(receipt.get("name"), str)
            or not isinstance(receipt.get("revision"), int)
            or isinstance(receipt.get("revision"), bool)
            or not isinstance(receipt.get("sha256"), str)
            or _DIGEST_RE.fullmatch(receipt["sha256"]) is None):
        raise ValueError("invalid structure-profile provenance")
    profile = get_profile(receipt["name"])
    if receipt != profile_provenance(profile):
        raise ValueError("structure-profile provenance does not match policy")
    return profile


def match_division(
        text: str, profile: StructureProfile, context: str,
) -> DivisionMatch | None:
    """Return the first valid division match for one explicit context."""
    if context not in _DIVISION_CONTEXTS:
        raise ValueError(f"unknown division context: {context}")
    if not isinstance(text, str):
        return None
    for rule in profile.division_rules:
        if context not in rule.contexts:
            continue
        match = re.search(rule.pattern, text, re.IGNORECASE)
        if match is None:
            continue
        raw_number = match.group("number").strip()
        ordinal = parse_division_ordinal(
            raw_number, allowed_styles=profile.allowed_number_styles,
            maximum=profile.maximum_division_ordinal)
        if ordinal is None:
            continue
        kind = (
            match.groupdict().get("kind") or rule.default_kind
        ).strip().title()
        title = ""
        if rule.title_group is not None:
            title = (match.group(rule.title_group) or "").strip(" \t:.-\u2013\u2014")
        return DivisionMatch(
            ordinal=ordinal, raw_number=raw_number, kind=kind,
            title=title, matched_text=match.group(0))
    return None


def find_divisions(
        text: str, profile: StructureProfile, context: str,
) -> tuple[DivisionMatch, ...]:
    """Return all non-overlapping valid divisions for a search context."""
    if context not in _DIVISION_CONTEXTS:
        raise ValueError(f"unknown division context: {context}")
    output = []
    occupied: list[tuple[int, int]] = []
    for rule in profile.division_rules:
        if context not in rule.contexts:
            continue
        for match in re.finditer(rule.pattern, text, re.IGNORECASE):
            if any(match.start() < end and match.end() > start
                   for start, end in occupied):
                continue
            raw_number = match.group("number").strip()
            ordinal = parse_division_ordinal(
                raw_number, allowed_styles=profile.allowed_number_styles,
                maximum=profile.maximum_division_ordinal)
            if ordinal is None:
                continue
            title = ""
            if rule.title_group is not None:
                title = (match.group(rule.title_group) or "").strip(
                    " \t:.-\u2013\u2014")
            output.append(DivisionMatch(
                ordinal=ordinal,
                raw_number=raw_number,
                kind=(match.groupdict().get("kind")
                      or rule.default_kind).strip().title(),
                title=title,
                matched_text=match.group(0),
            ))
            occupied.append((match.start(), match.end()))
    return tuple(output)


def section_rule_for_heading(
        heading: str, profile: StructureProfile,
) -> SectionRule | None:
    """Match one normalized heading against the selected profile."""
    for rule in profile.section_rules:
        if any(re.fullmatch(pattern, heading, re.IGNORECASE)
               for pattern in rule.patterns):
            return rule
    return None


def toc_seed_keys(profile: StructureProfile) -> tuple[str, ...]:
    return tuple(rule.key for rule in profile.section_rules if rule.toc_seed)


def structural_section_keys(profile: StructureProfile) -> tuple[str, ...]:
    return tuple(
        rule.key for rule in profile.section_rules if rule.exclude_from_corpus)


def structural_heading_patterns(profile: StructureProfile) -> tuple[re.Pattern, ...]:
    """Compile exact structural-heading patterns for classifier injection."""
    return tuple(
        re.compile(rf"^(?:{pattern})$", re.IGNORECASE | re.MULTILINE)
        for rule in profile.section_rules if rule.exclude_from_corpus
        for pattern in rule.patterns
    )


def hierarchy_level(title: str, profile: StructureProfile) -> int:
    """Infer one TOC hierarchy level under the selected profile."""
    if match_division(title, profile, "toc_entry") is not None:
        return 1
    for rule in profile.hierarchy_rules:
        if re.search(rule.pattern, title, re.IGNORECASE):
            return rule.level
    return 3


def canonical_division_title(
        match: DivisionMatch, profile: StructureProfile, *,
        fallback_title: str = "",
) -> str:
    """Format a canonical title without discarding publisher numbering."""
    title = (match.title or fallback_title).strip()
    if not title:
        return f"{match.kind} {match.raw_number}"
    return profile.canonical_title_template.format(
        kind=match.kind, number=match.raw_number, title=title).strip()
