from concurrent.futures import ThreadPoolExecutor
from dataclasses import FrozenInstanceError, replace

import pytest

import document_profiles


def test_profile_registry_is_immutable_and_has_reviewed_default():
    assert document_profiles.profile_names() == (
        "us-law-casebook-v1", "roman-parts-book-v1")
    default = document_profiles.get_profile(
        document_profiles.DEFAULT_STRUCTURE_PROFILE)
    assert default is document_profiles.STRUCTURE_PROFILES[default.name]

    with pytest.raises(TypeError):
        document_profiles.STRUCTURE_PROFILES["new"] = default
    with pytest.raises(FrozenInstanceError):
        default.revision = 2


def test_unknown_profile_fails_closed_with_registered_choices():
    with pytest.raises(ValueError, match="unknown structure profile") as exc:
        document_profiles.get_profile("publisher-auto")
    assert "us-law-casebook-v1" in str(exc.value)
    assert "roman-parts-book-v1" in str(exc.value)


def test_profile_provenance_is_strict_stable_and_content_addressed():
    profile = document_profiles.get_profile("us-law-casebook-v1")
    first = document_profiles.profile_provenance(profile)
    second = document_profiles.profile_provenance(profile)

    assert first == second
    assert first == {
        "schema_version": 1,
        "name": "us-law-casebook-v1",
        "revision": 6,
        "sha256": document_profiles.profile_sha256(profile),
    }
    assert document_profiles.profile_from_provenance(first) is profile

    changed = replace(profile, document_description="Changed policy content")
    assert document_profiles.profile_sha256(changed) != first["sha256"]
    with pytest.raises(ValueError, match="does not match policy"):
        document_profiles.profile_from_provenance({
            **first, "sha256": document_profiles.profile_sha256(changed)})


@pytest.mark.parametrize("value, expected", [
    ("4", 4),
    ("IV", 4),
    ("four", 4),
    ("twenty nine", 29),
    ("XL", 40),
    ("IIII", None),
    ("IC", None),
    ("0", None),
    ("51", None),
])
def test_division_ordinals_are_normalized_strictly(value, expected):
    assert document_profiles.parse_division_ordinal(value) == expected


def test_context_qualified_match_preserves_roman_display_designator():
    profile = document_profiles.get_profile("roman-parts-book-v1")
    match = document_profiles.match_division(
        "Part IV — Institutions and Practice", profile, "running_header")

    assert match == document_profiles.DivisionMatch(
        ordinal=4,
        raw_number="IV",
        kind="Part",
        title="Institutions and Practice",
        matched_text="Part IV — Institutions and Practice",
    )
    assert document_profiles.canonical_division_title(match, profile) == (
        "Part IV: Institutions and Practice")
    assert document_profiles.match_division(
        "Chapter 4 — Institutions", profile, "running_header") is None


def test_legal_profile_matches_split_running_header_after_aggregation():
    profile = document_profiles.get_profile("us-law-casebook-v1")

    match = document_profiles.match_division(
        "198 SAMPLE SYSTEM OPERATIONS CH. 4", profile, "running_header")
    opener = document_profiles.match_division(
        "CHAPTER 4", profile, "section_boundary")

    assert match == document_profiles.DivisionMatch(
        ordinal=4,
        raw_number="4",
        kind="Chapter",
        title="SAMPLE SYSTEM OPERATIONS",
        matched_text="198 SAMPLE SYSTEM OPERATIONS CH. 4",
    )
    assert opener is not None
    assert opener.ordinal == 4
    assert opener.title == ""


def test_spaced_word_division_preserves_the_complete_ordinal():
    profile = document_profiles.get_profile("us-law-casebook-v1")

    match = document_profiles.match_division(
        "Chapter Twenty One: Duties", profile, "section_boundary")

    assert match is not None
    assert (match.ordinal, match.raw_number, match.title) == (
        21, "Twenty One", "Duties")


def test_profile_native_fallback_and_subnumber_detection_keep_roman_display():
    profile = document_profiles.get_profile("roman-parts-book-v1")
    match = document_profiles.match_division(
        "Part IV — Institutions — IV-1 Exercise", profile, "toc_entry")

    assert document_profiles.fallback_division_title(profile, 4) == "Part IV"
    assert match is not None
    assert document_profiles.contains_division_subnumber(
        "Part IV — Institutions — IV-1 Exercise", match)


def test_default_profile_keeps_existing_casebook_heading_behavior():
    profile = document_profiles.get_profile("us-law-casebook-v1")
    match = document_profiles.match_division(
        "3 · PERSONAL JURISDICTION", profile, "chunk_heading")

    assert match is not None
    assert (match.ordinal, match.kind, match.title) == (
        3, "Chapter", "PERSONAL JURISDICTION")
    assert document_profiles.hierarchy_level(
        "A. The Study of Procedure", profile) == 2
    assert document_profiles.hierarchy_level(
        "International Shoe Co. v. Washington", profile) == 5


def test_profiles_do_not_cross_talk_between_concurrent_callers():
    legal = document_profiles.get_profile("us-law-casebook-v1")
    roman = document_profiles.get_profile("roman-parts-book-v1")

    def classify(profile, heading):
        match = document_profiles.match_division(
            heading, profile, "running_header")
        return None if match is None else (match.kind, match.ordinal)

    jobs = [
        (legal, "Chapter 7 — Remedies"),
        (roman, "Part VII — Remedies"),
    ] * 50
    with ThreadPoolExecutor(max_workers=8) as pool:
        results = list(pool.map(lambda pair: classify(*pair), jobs))

    assert results[::2] == [("Chapter", 7)] * 50
    assert results[1::2] == [("Part", 7)] * 50


def test_profile_registration_rejects_missing_named_number_group():
    profile = document_profiles.get_profile("us-law-casebook-v1")
    invalid_rule = replace(
        profile.division_rules[0], pattern=r"^Chapter (\d+)$")
    invalid = replace(
        profile, name="invalid-profile-v1",
        division_rules=(invalid_rule,) + profile.division_rules[1:])

    with pytest.raises(ValueError, match="named number group"):
        document_profiles._validate_profile(invalid)
