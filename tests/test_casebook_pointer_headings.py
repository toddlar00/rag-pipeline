"""Casebook-supplement pointer headings and the supplement profile.

A standalone casebook update prints each lettered update section with a
pointer to its place in the main casebook ("CHAPTER 2.B: ...") directly
below the section title.  Every fixture is synthetic; no private textbook
text is used.
"""

from dataclasses import replace

import pytest

import document_profiles
import heading_lineage
import rag


EXCERPT = document_profiles.EXCERPT_STRUCTURE_PROFILE
SUPPLEMENT = "us-law-casebook-supplement-v1"
T = "#/texts/"
_ISSUE_FIELDS = (
    "missing_refs", "unattached_refs", "missing_direct_refs",
    "duplicate_direct_refs", "invalid_binding_records",
    "scope_conflict_records", "display_binding_records",
)


def _item(ref, label, text, *, page=1, top=700, left=70, right=430,
          layer="body"):
    return {
        "self_ref": ref,
        "label": label,
        "content_layer": layer,
        "text": text,
        "children": [],
        "prov": [{
            "page_no": page,
            "bbox": {
                "l": left, "r": right, "t": top, "b": top - 15,
                "coord_origin": "BOTTOMLEFT",
            },
        }],
    }


def _document(items):
    ordered = sorted(items, key=lambda item: (
        item["prov"][0]["page_no"], -item["prov"][0]["bbox"]["t"],
        item["prov"][0]["bbox"]["l"]))
    return {
        "texts": list(items),
        "tables": [],
        "pictures": [],
        "groups": [],
        "body": {"children": [{"cref": item["self_ref"]} for item in ordered]},
        "pages": {
            str(page): {"size": {"width": 500, "height": 800}}
            for page in {item["prov"][0]["page_no"] for item in items}
        },
    }


def _record(ref):
    return {
        "text": "Synthetic body passage",
        "metadata": {
            "section_path": "",
            "headings": [],
            "content_source": "body",
            "source_items": [{"ref": ref, "label": "text", "spans": []}],
        },
    }


def _run(document, body_refs, ranges=()):
    """Assign source-heading paths, then attach and independently audit."""
    records = [_record(ref) for ref in body_refs]
    rag._assign_source_heading_paths(
        document, records, structural_ranges=set(ranges),
        excluded_heading_refs=set())
    heading_lineage.attach_heading_bindings(
        document, records, structural_ranges=ranges)
    audit = heading_lineage.audit_heading_bindings(
        document, records, structural_ranges=ranges)
    paths = [record["metadata"]["section_path"] for record in records]
    issues = {field: audit[field] for field in _ISSUE_FIELDS if audit[field]}
    return paths, issues, audit


def _composites(expected):
    return {
        refs[-1]: tuple(refs[:-1])
        for refs, _level, _start, _end in expected["heading_groups"]
        if len(refs) > 1
    }


def _section_then(pointer, *, head="B. SAMPLE UPDATE TOPIC"):
    return _document([
        _item(T + "0", "section_header", head, top=700),
        _item(T + "1", "section_header", pointer, top=680, left=90),
        _item(T + "2", "text", "Update body.", top=640),
    ])


def test_casebook_pointer_after_lettered_section_joins_its_node():
    document = _document([
        _item(T + "0", "text", "Earlier body.", top=760),
        _item(T + "1", "section_header", "B. SAMPLE UPDATE TOPIC", top=700),
        _item(T + "2", "section_header", "CHAPTER 2.A.1.E.: SAMPLE POINTER",
              top=680, left=90),
        _item(T + "3", "text", "Update body.", top=640),
    ])

    paths, issues, audit = _run(document, [T + "0", T + "3"])

    assert paths == [
        "", "B. SAMPLE UPDATE TOPIC → CHAPTER 2.A.1.E.: SAMPLE POINTER"]
    assert issues == {}
    assert audit["source_scope_paths"][1] == [T + "1", T + "2"]
    assert _composites(audit) == {T + "2": (T + "1",)}


@pytest.mark.parametrize("pointer", [
    "CHAPTER 2.B: SAMPLE POINTER",
    "CHAPTER 2.A.1.E.: SAMPLE POINTER",
    "CHAPTER 5, SECTION C: SAMPLE POINTER",
    "CHAPTER 6 IN GENERAL",
    "CHAPTER 7 GENERALLY",
])
def test_uppercase_pointer_forms_join_their_section(pointer):
    paths, issues, _ = _run(
        _section_then(pointer, head="G. SAMPLE UPDATE TOPIC"), [T + "2"])

    assert paths == [f"G. SAMPLE UPDATE TOPIC → {pointer}"]
    assert issues == {}


@pytest.mark.parametrize("division", [
    "CHAPTER 3",
    "CHAPTER 3: OFFER",
    "Chapter 3 Offer and Acceptance",
    "PART 2.A: SAMPLE",
    # Pointers are matched case-sensitively: every in-document pointer is
    # uppercase, so title-case text stays an authoritative division.
    "Chapter 7 Generally",
    "Chapter 2.A.1.e.: Sample",
    "CHAPTER 2.A.1.e.: SAMPLE",
    # A pointer addresses a lettered section of a chapter; a decimal
    # chapter number and text that merely starts like a pointer do not.
    "CHAPTER 1.1: INTRODUCTION",
    "CHAPTER 6 GENERALLY APPLICABLE RULES",
    "CHAPTER 2.B:SAMPLE",
])
def test_real_divisions_after_bodiless_lettered_heading_still_fail(division):
    document = _section_then(division)

    paths, issues, _ = _run(document, [T + "2"])

    assert paths == [division]
    assert issues["missing_refs"] == [T + "0"]
    assert heading_lineage._casebook_pointer_runs(document) == {}


@pytest.mark.parametrize("head", [
    "Sample Topic", "II. SAMPLE TOPIC", "1. SAMPLE TOPIC",
    "b. sample topic", "B.",
])
def test_pointer_needs_a_lettered_section_title(head):
    document = _section_then("CHAPTER 2.B: SAMPLE POINTER", head=head)

    _, issues, _ = _run(document, [T + "2"])

    assert issues["missing_refs"] == [T + "0"]
    assert heading_lineage._casebook_pointer_runs(document) == {}


def test_body_text_between_keeps_the_pointer_authoritative():
    document = _document([
        _item(T + "0", "section_header", "B. SAMPLE TOPIC", top=700),
        _item(T + "1", "text", "Section body.", top=680),
        _item(T + "2", "section_header", "CHAPTER 2.B: SAMPLE", top=640),
        _item(T + "3", "text", "Pointer body.", top=600),
    ])

    paths, issues, _ = _run(document, [T + "1", T + "3"])

    assert paths == ["B. SAMPLE TOPIC", "CHAPTER 2.B: SAMPLE"]
    assert issues == {}
    assert heading_lineage._casebook_pointer_runs(document) == {}


def test_non_body_item_between_breaks_adjacency():
    document = _document([
        _item(T + "0", "section_header", "B. SAMPLE TOPIC", top=700),
        _item(T + "1", "formula", "", top=680),
        _item(T + "2", "section_header", "CHAPTER 2.B: SAMPLE", top=640),
        _item(T + "3", "text", "Pointer body.", top=600),
    ])

    _, issues, _ = _run(document, [T + "3"])

    assert issues["missing_refs"] == [T + "0"]
    assert heading_lineage._casebook_pointer_runs(document) == {}


def _page_turn(footer):
    return _document([
        _item(T + "0", "text", "Earlier body.", page=1, top=300),
        _item(T + "1", "section_header", "C. SAMPLE TOPIC", page=1, top=100),
        _item(T + "2", "page_footer", footer, page=1, top=40,
              layer="furniture"),
        _item(T + "3", "section_header", "CHAPTER 2.A.1.F.: SAMPLE",
              page=2, top=760, left=90),
        _item(T + "4", "text", "Update body.", page=2, top=700),
    ])


def test_page_label_between_section_and_pointer_is_adjacent():
    paths, issues, _ = _run(_page_turn("7"), [T + "0", T + "4"])

    assert paths == ["", "C. SAMPLE TOPIC → CHAPTER 2.A.1.F.: SAMPLE"]
    assert issues == {}


def test_text_bearing_footer_between_section_and_pointer_breaks_the_run():
    # A substantive footer is published as its own record under the section
    # title, so the section is not bodiless and today's result is kept.
    document = _page_turn(
        "1 See Sample Agency v. Sample Board, 1 U.S. 2 (1900).")

    paths, issues, _ = _run(document, [T + "0", T + "2", T + "4"])

    assert paths == ["", "C. SAMPLE TOPIC", "CHAPTER 2.A.1.F.: SAMPLE"]
    assert issues == {}
    assert heading_lineage._casebook_pointer_runs(document) == {}


def _split_title(fragment, *, fragment_top=700, fragment_left=202):
    return _document([
        _item(T + "0", "section_header", "H. SAMPLE V. AGENCY",
              top=700, right=200),
        _item(T + "1", "section_header", fragment, top=fragment_top,
              left=fragment_left, right=390),
        _item(T + "2", "section_header", "CHAPTER 6, SECTION F: SAMPLE",
              top=680 if fragment_top == 700 else 660, left=90),
        _item(T + "3", "text", "Update body.", top=620),
    ])


def test_same_line_title_fragment_folds_into_its_section():
    document = _split_title("& SECOND PHRASE")

    paths, issues, audit = _run(document, [T + "3"])

    assert paths == [
        "H. SAMPLE V. AGENCY → & SECOND PHRASE → "
        "CHAPTER 6, SECTION F: SAMPLE"]
    assert issues == {}
    assert _composites(audit) == {T + "2": (T + "0", T + "1")}


@pytest.mark.parametrize("fragment, top, left", [
    ("& SECOND PHRASE", 680, 70),  # the next printed line, not the title
    ("1. SECOND PHRASE", 700, 202),  # a marker is never a title fragment
])
def test_unproven_title_fragments_keep_todays_failure(fragment, top, left):
    document = _split_title(fragment, fragment_top=top, fragment_left=left)

    _, issues, _ = _run(document, [T + "3"])

    assert issues["missing_refs"] == [T + "0", T + "1"]
    assert heading_lineage._casebook_pointer_runs(document) == {}


def test_pointer_sections_are_peers():
    # "I." is a Roman numeral elsewhere; after a pointer-proven "H." it is
    # the next section letter and must not nest below "H.".
    document = _document([
        _item(T + "0", "section_header", "H. FIRST UPDATE", top=760),
        _item(T + "1", "section_header", "CHAPTER 6, SECTION F: SAMPLE",
              top=740, left=90),
        _item(T + "2", "text", "First body.", top=700),
        _item(T + "3", "section_header", "I. SECOND UPDATE", top=600),
        _item(T + "4", "section_header", "CHAPTER 7 GENERALLY",
              top=580, left=90),
        _item(T + "5", "text", "Second body.", top=540),
    ])

    paths, issues, audit = _run(document, [T + "2", T + "5"])

    assert paths == [
        "H. FIRST UPDATE → CHAPTER 6, SECTION F: SAMPLE",
        "I. SECOND UPDATE → CHAPTER 7 GENERALLY",
    ]
    assert issues == {}
    assert heading_lineage._casebook_pointer_runs(document) == {
        T + "1": (T + "0",), T + "4": (T + "3",)}
    assert _composites(audit) == heading_lineage._casebook_pointer_runs(
        document)


@pytest.mark.parametrize("pointer", [
    "Chapter 2 In General", "CHAPTER 2 IN GENERAL"])
def test_lettered_heading_inside_a_chapter_is_never_joined(pointer):
    # A bodiless closing "A." under CHAPTER 1 followed by the next chapter
    # is a genuine lineage failure.  Joining would silently nest Chapter 2
    # under Chapter 1, so the section must be the bottom of the stack.
    document = _document([
        _item(T + "0", "section_header", "CHAPTER 1 FOUNDATIONS", top=780),
        _item(T + "1", "text", "Opening body.", top=760),
        _item(T + "2", "section_header", "A. Closing Remarks", top=700),
        _item(T + "3", "section_header", pointer, page=2, top=780),
        _item(T + "4", "text", "Chapter two body.", page=2, top=740),
        _item(T + "5", "section_header", "Chapter 3 Remedies", page=3,
              top=780),
        _item(T + "6", "text", "Chapter three body.", page=3, top=740),
    ])

    paths, issues, _ = _run(document, [T + "1", T + "4", T + "6"])

    assert paths == ["CHAPTER 1 FOUNDATIONS", pointer, "Chapter 3 Remedies"]
    assert issues["missing_refs"] == [T + "2"]
    assert heading_lineage._casebook_pointer_runs(document) == {}


def test_lettered_heading_nested_below_a_bodiless_heading_is_not_joined():
    # The heading run opens at the bodiless heading, so its first member is
    # not a lettered section title and no pointer run is proven.
    document = _document([
        _item(T + "0", "section_header", "NOTES AND QUESTIONS", top=760),
        _item(T + "1", "text", "Notes body.", top=740),
        _item(T + "2", "section_header", "SAMPLE HEADING", top=700),
        _item(T + "3", "section_header", "B. SAMPLE UPDATE", top=680),
        _item(T + "4", "section_header", "CHAPTER 2.B: SAMPLE", top=660,
              left=90),
        _item(T + "5", "text", "Update body.", top=620),
    ])

    _, issues, _ = _run(document, [T + "1", T + "5"])

    assert issues["missing_refs"] == [T + "2", T + "3"]
    assert heading_lineage._casebook_pointer_runs(document) == {}


def test_pointer_run_inside_structural_range_is_ignored():
    document = _section_then("CHAPTER 2.B: SAMPLE POINTER")

    expected = heading_lineage.expected_heading_bindings(
        document, [_record(T + "2")], structural_ranges=((1, 1),))

    assert expected["attachable_refs"] == []
    assert heading_lineage._casebook_pointer_runs(
        document, structural_ranges=((1, 1),)) == {}
    assert heading_lineage._casebook_pointer_runs(document) == {
        T + "1": (T + "0",)}


def test_excluded_section_heading_never_opens_a_pointer_run():
    document = _section_then("CHAPTER 2.B: SAMPLE POINTER")

    assert heading_lineage._casebook_pointer_runs(
        document, excluded_heading_refs={T + "0"}) == {}


@pytest.mark.parametrize("document", [
    _section_then("CHAPTER 2.B: SAMPLE POINTER"),
    _split_title("& SECOND PHRASE"),
    _split_title("& SECOND PHRASE", fragment_top=680, fragment_left=70),
    _section_then("CHAPTER 3: OFFER"),
])
def test_private_pointer_proof_matches_the_lineage_loop(document):
    body = [T + "3"] if len(document["texts"]) == 4 else [T + "2"]
    fresh = heading_lineage.expected_heading_bindings(
        document, [_record(ref) for ref in body])
    _, _, audit = _run(document, body)

    proof = heading_lineage._casebook_pointer_runs(document)
    assert _composites(fresh) == proof
    assert _composites(audit) == proof


def test_supplement_profile_is_explicit_and_additive():
    excerpt = document_profiles.get_profile(EXCERPT)
    supplement = document_profiles.get_profile(SUPPLEMENT)

    assert document_profiles.SUPPLEMENT_STRUCTURE_PROFILE == SUPPLEMENT
    assert document_profiles.profile_names() == (
        document_profiles.DEFAULT_STRUCTURE_PROFILE, "roman-parts-book-v1",
        EXCERPT, SUPPLEMENT)
    assert supplement.revision == 1
    assert document_profiles.profile_sha256(supplement) == (
        "a4fe15a86699c43fb1e27a16c15c3e63"
        "a9bfca033e7309146ff720a6795fe709")
    assert not document_profiles.requires_toc(supplement)
    assert document_profiles.casebook_family(supplement)
    assert supplement.section_rules == ()
    assert supplement.document_description != excerpt.document_description
    # Only the name and description differ from the excerpt policy.
    assert replace(
        supplement, name=EXCERPT,
        document_description=excerpt.document_description) == excerpt
    assert document_profiles.profile_from_provenance(
        document_profiles.profile_provenance(supplement)) is supplement
