"""Typed quality exclusion for text items the chunker erases as squares."""

import copy
import itertools

import pytest

import chunking_core
import quality_core
from test_quality_core import (
    _build,
    _record,
    _source_item,
    _validation_kwargs,
)

SQUARE = "\u25a0"
WHITE_SQUARE = "\u25a1"
NO_BREAK_SPACE = "\u00a0"
EM_SPACE = "\u2003"
IDEOGRAPHIC_SPACE = "\u3000"
ZERO_WIDTH_SPACE = "\u200b"
REPLACEMENT_CHARACTER = "\ufffd"


def _erased_by_normalization(text: str) -> bool:
    return SQUARE in text and chunking_core._normalize_text(text) == ""


def _single_item_inventory(text: str) -> tuple[dict, dict]:
    _, eligible, exclusions, issues = quality_core.source_inventory(
        {"texts": [_source_item("#/texts/0", 1, text=text)]},
        structural_ranges=[])
    assert issues == {}
    return eligible, exclusions


def _typed_like_normalization(text: str) -> bool:
    """The inventory excludes exactly the text the chunker erases."""
    erased = _erased_by_normalization(text)
    eligible, exclusions = _single_item_inventory(text)
    return ((exclusions == {"decorative_glyph": 1}) is erased
            and ("#/texts/0" in eligible) is (
                not erased and bool(text.strip())))


def _check(report: dict, name: str) -> dict:
    return next(check for check in report["checks"] if check["name"] == name)


def _native_repair_record(index: int, ref: str, page: int, *,
                          text: str) -> dict:
    record = _record(index, ref, page, text=text)
    record["metadata"]["source_items"][0]["transform"] = "native_repair"
    return record


def test_quality_report_schema_version_marks_decorative_glyph_policy():
    assert quality_core.QUALITY_REPORT_SCHEMA_VERSION == 13


@pytest.mark.parametrize("text, erased", [
    (SQUARE, True),
    (f"{SQUARE}   {SQUARE}   {SQUARE}", True),
    (f"{SQUARE}\t{SQUARE}", True),
    (f"{NO_BREAK_SPACE}{SQUARE}{NO_BREAK_SPACE}", True),
    (f"{SQUARE}\n\n{SQUARE}\n", True),
    (f"{SQUARE}\n{EM_SPACE}\n", True),
    (f" {SQUARE} ", True),
    (f"{SQUARE} a", False),
    (f"{SQUARE} JUSTICE ROE, concurring.", False),
    (WHITE_SQUARE, False),
    (f"{SQUARE}{WHITE_SQUARE}", False),
    (f"- {SQUARE}", False),
    (f"{EM_SPACE}{SQUARE}", False),
    (f"{SQUARE}{EM_SPACE}", False),
    (f"{IDEOGRAPHIC_SPACE}{SQUARE}", False),
    (f"{SQUARE}{EM_SPACE}{SQUARE}", False),
    (f"{SQUARE}\r\n", False),
    (f"{SQUARE}\r", False),
    ("", False),
    ("   ", False),
])
def test_decorative_square_predicate_cases(text, erased):
    assert chunking_core.normalization_erases_decorative_squares(
        text) is erased
    assert _erased_by_normalization(text) is erased
    assert _typed_like_normalization(text)


@pytest.mark.parametrize("text", [
    f"{SQUARE}{ZERO_WIDTH_SPACE}",
    f"{SQUARE}{REPLACEMENT_CHARACTER}",
    f"{SQUARE}\nA",
    f"{SQUARE}\n7",
])
def test_squares_erased_only_with_other_rules_stay_eligible(text):
    """Other normalization rules also erase these; they stay fail-closed."""
    assert _erased_by_normalization(text)
    assert not chunking_core.normalization_erases_decorative_squares(text)

    _, eligible, exclusions, issues = quality_core.source_inventory(
        {"texts": [_source_item("#/texts/0", 1, text=text)]},
        structural_ranges=[])

    assert set(eligible) == {"#/texts/0"}
    assert exclusions == {}
    assert issues == {}


def test_decorative_square_predicate_mirrors_normalization_exhaustively():
    alphabet = (SQUARE, WHITE_SQUARE, " ", "\t", NO_BREAK_SPACE, "\n",
                EM_SPACE, "\r", "x", "-")
    mismatches = [
        text
        for length in range(1, 5)
        for text in map("".join, itertools.product(alphabet, repeat=length))
        if chunking_core.normalization_erases_decorative_squares(text)
        != _erased_by_normalization(text)
    ]
    assert mismatches == []


def test_decorative_glyph_inventory_mirrors_normalization_exhaustively():
    """The inventory tests the unstripped text, as the chunker sees it.

    Python's str.strip() also removes em spaces and carriage returns, which
    the square rule keeps, so a stripped test would drop emitted squares.
    """
    alphabet = (SQUARE, WHITE_SQUARE, " ", "\t", NO_BREAK_SPACE, "\n",
                EM_SPACE, "\r", "x", "-")
    mismatches = [
        text
        for length in range(1, 5)
        for text in map("".join, itertools.product(alphabet, repeat=length))
        if not _typed_like_normalization(text)
    ]
    assert mismatches == []


def test_source_inventory_types_erased_square_text():
    document = {"texts": [
        _source_item("#/texts/0", 1, text=SQUARE),
        _source_item("#/texts/1", 1, text=f"{SQUARE}   {SQUARE}"),
        _source_item("#/texts/2", 1,
                     text=f"{SQUARE} JUSTICE ROE, concurring."),
        _source_item("#/texts/3", 1, text=WHITE_SQUARE),
        _source_item("#/texts/4", 1, label="list_item", text=SQUARE),
        _source_item("#/texts/5", 1, label="section_header", text=SQUARE),
        _source_item("#/texts/6", 1, label="caption", text=SQUARE),
        _source_item("#/texts/7", 1, text=f"{SQUARE}{ZERO_WIDTH_SPACE}"),
        _source_item("#/texts/8", 9, text=SQUARE),
        {**_source_item("#/texts/9", 1, text=SQUARE),
         "content_layer": "furniture"},
        _source_item("#/texts/10", 1, text=f"{EM_SPACE}{SQUARE}"),
        _source_item("#/texts/11", 1, text=f"{SQUARE}\r\n"),
    ]}

    _, eligible, exclusions, issues = quality_core.source_inventory(
        document, structural_ranges=[(9, 9)])

    assert set(eligible) == {
        "#/texts/2", "#/texts/3", "#/texts/4", "#/texts/6", "#/texts/7",
        "#/texts/10", "#/texts/11"}
    assert exclusions == {
        "decorative_glyph": 2,
        "furniture": 1,
        "label:section_header": 1,
        "structural_range": 1,
    }
    assert issues == {}


def test_source_inventory_reads_orig_when_text_is_empty():
    item = _source_item("#/texts/0", 1, text="")
    item["orig"] = SQUARE

    _, eligible, exclusions, _ = quality_core.source_inventory(
        {"texts": [item]}, structural_ranges=[])

    assert eligible == {}
    assert exclusions == {"decorative_glyph": 1}


def test_source_oracle_bound_square_text_stays_eligible():
    """A registry-bound repair replaces the text, so nothing is erased."""
    document = {"texts": [_source_item("#/texts/0", 1, text=SQUARE)]}

    _, eligible, exclusions, _ = quality_core.source_inventory(
        document, structural_ranges=[], source_oracle_refs={"#/texts/0"})

    assert set(eligible) == {"#/texts/0"}
    assert exclusions == {}


def test_quality_report_passes_when_erased_square_has_no_lineage():
    document = {"texts": [
        _source_item("#/texts/0", 1, text="Alpha beta gamma."),
        _source_item("#/texts/1", 1, text=SQUARE),
    ]}
    records = [_record(0, "#/texts/0", 1, text="Alpha beta gamma.")]

    report = _build(records, document)

    assert report["status"] == "pass"
    lineage = report["source_lineage"]
    assert lineage["eligible_items"] == lineage["represented_items"] == 1
    assert lineage["missing_refs"] == []
    assert lineage["excluded_items_by_reason"] == {"decorative_glyph": 1}
    assert _check(report, "eligible_source_items_represented")[
        "status"] == "pass"
    quality_core.validate_quality_report(
        copy.deepcopy(report), **_validation_kwargs(), records=records,
        document=document)


def test_registry_bound_square_text_stays_eligible_in_build_and_validation():
    """Both source_inventory call sites receive the trusted oracle refs.

    Building without them excludes the item, so the counts below change;
    validating without them recomputes a different eligible set, so the
    source-backed fidelity recomputation rejects the report.
    """
    document = {"texts": [
        _source_item("#/texts/0", 1, text="Alpha beta gamma."),
        _source_item("#/texts/1", 1, text=SQUARE),
    ]}
    records = [
        _record(0, "#/texts/0", 1, text="Alpha beta gamma."),
        _native_repair_record(1, "#/texts/1", 1, text="Delta epsilon."),
    ]

    report = _build(records, document)
    kwargs = _validation_kwargs(record_count=2)

    assert [entry["ref"] for entry in kwargs["source_oracle_registry"][
        "oracles"]] == ["#/texts/1"]
    assert report["status"] == "pass"
    lineage = report["source_lineage"]
    assert lineage["eligible_items"] == lineage["represented_items"] == 2
    assert lineage["missing_refs"] == []
    assert lineage["excluded_items_by_reason"] == {}
    quality_core.validate_quality_report(
        copy.deepcopy(report), **kwargs, records=records, document=document)


@pytest.mark.parametrize("label, text", [
    ("text", WHITE_SQUARE),
    ("text", f"{SQUARE} JUSTICE ROE, concurring."),
    ("list_item", SQUARE),
])
def test_quality_report_still_requires_emitted_square_items(label, text):
    document = {"texts": [
        _source_item("#/texts/0", 1, text="Alpha beta gamma."),
        _source_item("#/texts/1", 1, label=label, text=text),
    ]}
    records = [_record(0, "#/texts/0", 1, text="Alpha beta gamma.")]

    report = _build(records, document)

    assert report["status"] == "fail"
    assert report["source_lineage"]["missing_refs"] == ["#/texts/1"]
    assert "decorative_glyph" not in report["source_lineage"][
        "excluded_items_by_reason"]


def test_validation_rejects_previous_quality_schema():
    document = {"texts": [
        _source_item("#/texts/0", 1, text="Alpha beta gamma.")]}
    records = [_record(0, "#/texts/0", 1, text="Alpha beta gamma.")]
    report = _build(records, document)
    report["schema_version"] = quality_core.QUALITY_REPORT_SCHEMA_VERSION - 1

    with pytest.raises(ValueError, match="unsupported corpus quality"):
        quality_core.validate_quality_report(
            report, **_validation_kwargs(), records=records,
            document=document)


def test_no_decorative_reason_key_without_erased_squares():
    document = {"texts": [
        _source_item("#/texts/0", 1, text="Alpha beta gamma."),
        _source_item("#/texts/1", 1, text=WHITE_SQUARE),
    ]}
    records = [
        _record(0, "#/texts/0", 1, text="Alpha beta gamma."),
        _record(1, "#/texts/1", 1, text=WHITE_SQUARE),
    ]

    report = _build(records, document)

    assert report["status"] == "pass"
    assert report["source_lineage"]["excluded_items_by_reason"] == {}
