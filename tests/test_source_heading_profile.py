"""Explicit TOC-less casebook-excerpt profile built from source headings.

Every fixture below is synthetic; no private textbook text is used.
"""

from dataclasses import replace

import pytest

import document_profiles
import heading_lineage
import rag


EXCERPT = "us-law-casebook-excerpt-v1"
LEGAL = document_profiles.DEFAULT_STRUCTURE_PROFILE


def _item(ref, label, text, *, page=1, top=700, layer="body"):
    return {
        "self_ref": ref,
        "label": label,
        "content_layer": layer,
        "text": text,
        "children": [],
        "prov": [{
            "page_no": page,
            "bbox": {
                "l": 70, "r": 430, "t": top, "b": top - 15,
                "coord_origin": "BOTTOMLEFT",
            },
        }],
    }


def _document(items):
    return {
        "texts": items,
        "tables": [],
        "pictures": [],
        "groups": [],
        "body": {"children": [{"cref": item["self_ref"]} for item in items]},
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


def test_existing_profile_digests_are_unchanged():
    assert {
        name: document_profiles.profile_sha256(
            document_profiles.get_profile(name))
        for name in (LEGAL, "roman-parts-book-v1")
    } == {
        LEGAL: (
            "5fdc5c2539b20ae75173e9673a2d8ee5"
            "45772f5895be0f5f899853f56a07c0fd"),
        "roman-parts-book-v1": (
            "655388134ba62e37c2d392959be998cb"
            "a98df39e8b57ea601da3fe79f8b14378"),
    }


def test_excerpt_profile_is_explicit_and_shares_casebook_divisions():
    legal = document_profiles.get_profile(LEGAL)
    excerpt = document_profiles.get_profile(EXCERPT)

    assert document_profiles.profile_names()[-1] == EXCERPT
    assert excerpt.scaffold_source == "source_headings"
    assert legal.scaffold_source == "toc"
    assert document_profiles.requires_toc(legal)
    assert not document_profiles.requires_toc(excerpt)
    assert excerpt.section_rules == ()
    assert document_profiles.toc_seed_keys(excerpt) == ()
    assert excerpt.division_rules == legal.division_rules
    assert document_profiles.profile_from_provenance(
        document_profiles.profile_provenance(excerpt)) is excerpt


def test_scaffold_source_is_part_of_the_profile_digest():
    legal = document_profiles.get_profile(LEGAL)
    flipped = replace(legal, scaffold_source="source_headings")

    assert (document_profiles.profile_sha256(flipped)
            != document_profiles.profile_sha256(legal))


@pytest.mark.parametrize("changes, message", [
    ({"scaffold_source": "bookmarks"}, "unsupported scaffold source"),
    ({"section_rules": (document_profiles.SectionRule(
        "contents", ("contents",), True, toc_seed=True),)},
     "must not declare section rules"),
    ({"section_rules": (document_profiles.SectionRule(
        "credits", ("credits",), True),)},
     "must not declare section rules"),
])
def test_source_heading_profile_validation_fails_closed(changes, message):
    excerpt = document_profiles.get_profile(EXCERPT)
    invalid = replace(excerpt, name="invalid-excerpt-v1", **changes)

    with pytest.raises(ValueError, match=message):
        document_profiles._validate_profile(invalid)


def test_toc_profile_still_requires_a_toc_seed_rule():
    legal = document_profiles.get_profile(LEGAL)
    unseeded = replace(legal, name="unseeded-v1", section_rules=tuple(
        rule for rule in legal.section_rules if not rule.toc_seed))

    with pytest.raises(ValueError, match="requires a TOC seed rule"):
        document_profiles._validate_profile(unseeded)


def test_casebook_family_includes_only_casebook_profiles():
    assert document_profiles.casebook_family(
        document_profiles.get_profile(LEGAL))
    assert document_profiles.casebook_family(
        document_profiles.get_profile(EXCERPT))
    assert not document_profiles.casebook_family(
        document_profiles.get_profile("roman-parts-book-v1"))


def _later_chapter_document():
    # Page 1 is mid-chapter body text; the next chapter opens on page 3.
    return _document([
        _item("#/texts/0", "text", "Continuing discussion.", page=1),
        _item("#/texts/1", "section_header", "Contents", page=2),
        _item("#/texts/2", "text", "More discussion.", page=2, top=600),
        _item("#/texts/3", "section_header", "CHAPTER 4", page=3),
        _item("#/texts/4", "text", "Chapter body.", page=3, top=600),
    ])


def test_toc_profile_keeps_pre_chapter_front_matter_exclusion():
    sections = rag._identify_book_sections(
        _later_chapter_document(), structure_profile=LEGAL, emit_log=False)

    assert sections["front_matter"] == {"start": 1, "end": 2}
    assert (1, 2) in rag._book_structural_ranges(
        sections, structure_profile=LEGAL)


def test_source_heading_profile_excludes_no_excerpt_pages():
    sections = rag._identify_book_sections(
        _later_chapter_document(), structure_profile=EXCERPT, emit_log=False)

    assert sections == {"front_matter": None}
    assert rag._book_structural_ranges(
        sections, structure_profile=EXCERPT) == set()


def test_toc_gate_exits_only_for_toc_profiles():
    legal = document_profiles.get_profile(LEGAL)
    excerpt = document_profiles.get_profile(EXCERPT)

    with pytest.raises(SystemExit) as exc:
        rag._require_structure_scaffold_evidence(
            {"front_matter": None}, legal, "synthetic.json",
            llm_scaffold=False)
    assert exc.value.code == 1
    assert rag._require_structure_scaffold_evidence(
        {"contents": {"start": 2, "end": 2}}, legal, "synthetic.json",
        llm_scaffold=False) is None
    assert rag._require_structure_scaffold_evidence(
        {"front_matter": None}, excerpt, "synthetic.json",
        llm_scaffold=False) is None


def test_llm_scaffold_is_rejected_for_source_heading_profiles():
    excerpt = document_profiles.get_profile(EXCERPT)

    with pytest.raises(ValueError, match="--llm-scaffold"):
        rag._require_structure_scaffold_evidence(
            {"front_matter": None}, excerpt, "synthetic.json",
            llm_scaffold=True)


@pytest.mark.parametrize("text, expected", [
    # Lineage treats these two marker families as attested; so must the
    # override pass, or the later no-override audit is not a fixed point.
    ("CHAPTER 4", (1, True)),
    ("Part Two", (1, True)),
    ("§ 2.01 Scope", (2, True)),
    ("A. FIRST TOPIC", (3, False)),
    ("B.", (3, False)),
    ("C. THIRD TOPIC", (3, False)),
    ("I. Background", (4, False)),
    ("II. Discussion", (4, False)),
    ("III. Analysis", (4, False)),
    ("V. Remedy", (4, False)),
    ("12. Review Standards", (4, False)),
    ("2.", (4, False)),
    ("a. narrower point", (5, False)),
    ("i. first factor", (5, False)),
    ("ii. second factor", (5, False)),
    ("NOTES AND QUESTIONS", (6, False)),
    ("§ 553. Rule making", (6, False)),
    ("Sample Agency v. Sample Board", (6, False)),
])
def test_source_heading_stack_levels(text, expected):
    assert rag._source_heading_stack_level(text) == expected


def test_authoritative_division_run_matches_the_audit_scope():
    document = _document([
        _item("#/texts/0", "section_header", "PART TWO", top=760),
        _item("#/texts/1", "section_header", "CONTRACT FORMATION", top=740),
        _item("#/texts/2", "section_header", "CHAPTER 3", top=700),
        _item("#/texts/3", "section_header", "OFFER", top=680),
        _item("#/texts/4", "text", "Chapter body.", top=640),
    ])
    records = [_record("#/texts/4")]

    rag._assign_source_heading_paths(
        document, records, structural_ranges=set(),
        excluded_heading_refs=set())
    heading_lineage.attach_heading_bindings(document, records)
    audit = heading_lineage.audit_heading_bindings(document, records)

    assert records[0]["metadata"]["section_path"] == (
        "CHAPTER 3 → OFFER")
    assert audit["display_binding_records"] == []
    assert audit["scope_conflict_records"] == []
    # A bodiless Part is a genuine lineage failure in both passes.
    assert audit["missing_refs"] == ["#/texts/0", "#/texts/1"]


def test_single_letter_roman_parts_are_siblings_of_later_parts():
    document = _document([
        _item("#/texts/0", "section_header", "I. Background", top=700),
        _item("#/texts/1", "text", "Body", top=650),
        _item("#/texts/2", "section_header", "II. Discussion", top=600),
        _item("#/texts/3", "text", "Body", top=550),
    ])
    records = [_record("#/texts/1"), _record("#/texts/3")]

    rag._assign_source_heading_paths(
        document, records, structural_ranges=set(),
        excluded_heading_refs=set())

    assert [record["metadata"]["section_path"] for record in records] == [
        "I. Background", "II. Discussion"]


def test_lineage_level_overrides_replace_conflicting_record_hints():
    document = _document([
        _item("#/texts/0", "section_header", "Overview", top=700),
        _item("#/texts/1", "text", "Body", top=650),
        _item("#/texts/2", "section_header", "Detail", top=600),
        _item("#/texts/3", "text", "Body", top=550),
    ])
    records = [_record("#/texts/1"), _record("#/texts/3")]
    records[0]["metadata"]["section_path"] = "Overview"
    records[1]["metadata"]["section_path"] = "Overview > Detail"

    hinted = heading_lineage.expected_heading_bindings(document, records)
    peers = heading_lineage.expected_heading_bindings(
        document, records, level_overrides={
            "#/texts/0": (3, True), "#/texts/2": (3, True)})

    # Without overrides the record paths nest Detail below Overview.
    assert hinted["source_scope_paths"] == [
        ["#/texts/0"], ["#/texts/0", "#/texts/2"]]
    assert peers["source_scope_paths"] == [["#/texts/0"], ["#/texts/2"]]


def test_lineage_level_override_preempts_authoritative_division_rule():
    document = _document([
        _item("#/texts/0", "section_header", "Overview", top=700),
        _item("#/texts/1", "text", "Body", top=650),
        _item("#/texts/2", "section_header", "Chapter 2", top=600),
        _item("#/texts/3", "text", "Body", top=550),
    ])
    records = [_record("#/texts/1"), _record("#/texts/3")]

    default = heading_lineage.expected_heading_bindings(document, records)
    nested = heading_lineage.expected_heading_bindings(
        document, records, level_overrides={
            "#/texts/0": (1, True), "#/texts/2": (2, True)})

    assert default["source_scope_paths"][1] == ["#/texts/2"]
    assert nested["source_scope_paths"][1] == ["#/texts/0", "#/texts/2"]


def _running_header_document():
    items = []
    for page in (1, 2):
        items.append(_item(
            f"#/texts/{len(items)}", "page_header",
            f"{197 + page} SAMPLE SYSTEM OPERATIONS CH. 4",
            page=page, top=780, layer="furniture"))
        items.append(_item(
            f"#/texts/{len(items)}", "text", "Synthetic body.",
            page=page, top=600))
    return _document(items)


def test_toc_profile_keeps_running_header_chapter_map():
    chapter_map = rag._build_chapter_map_from_document(
        _running_header_document(), structure_profile=LEGAL)

    assert list(chapter_map) == [4]


def test_source_heading_profile_derives_no_running_header_chapters():
    assert rag._build_chapter_map_from_document(
        _running_header_document(), structure_profile=EXCERPT) == {}


def test_llm_scaffold_is_rejected_when_arguments_are_parsed(capsys):
    with pytest.raises(SystemExit) as exc:
        rag.main([
            "chunk", "--doc", "missing-synthetic.json",
            "--structure-profile", EXCERPT, "--llm-scaffold"])

    assert exc.value.code == 2
    assert "--llm-scaffold" in capsys.readouterr().err


@pytest.mark.parametrize("command", [
    ["full", "--pdf", "missing-synthetic.pdf"],
    ["batch", "missing-synthetic.pdf"],
])
def test_split_chapters_is_rejected_when_arguments_are_parsed(
        capsys, command, monkeypatch, tmp_path):
    # Excerpts carry no chapter page anchors, so the split export would fail
    # only after conversion, chunking and indexing had finished. A regression
    # must not allocate run directories in the real output root.
    monkeypatch.chdir(tmp_path)
    monkeypatch.setattr(rag, "OUTPUT_DIR", tmp_path / "output")
    with pytest.raises(SystemExit) as exc:
        rag.main([
            *command, "--structure-profile", EXCERPT, "--split-chapters"])

    assert exc.value.code == 2
    assert "--split-chapters" in capsys.readouterr().err


def _mid_chapter_excerpt():
    headings = {
        1: "CHAPTER 4", 2: "SAMPLE PROCEDURES", 3: "A. FIRST TOPIC",
        5: "1. FIRST SUBTOPIC", 7: "Sample Agency v. Sample Board",
        8: "101 U.S. 1 (1900).", 10: "III. Analysis",
        11: "A. Governing Principles", 13: "NOTES AND QUESTIONS",
        15: "2. SECOND SUBTOPIC", 17: "B. SECOND TOPIC",
    }
    items = []
    for index in range(19):
        page = 1 + index // 7
        top = 760 - (index % 7) * 90
        text = headings.get(index, f"Synthetic body {index}.")
        label = "section_header" if index in headings else "text"
        items.append(_item(f"#/texts/{index}", label, text, page=page, top=top))
    body = [index for index in range(19) if index not in headings]
    return _document(items), body


def test_source_heading_paths_follow_excerpt_occurrences():
    document, body = _mid_chapter_excerpt()
    records = [_record(f"#/texts/{index}") for index in body]

    rag._assign_source_heading_paths(
        document, records, structural_ranges=set(),
        excluded_heading_refs=set())

    chapter = "CHAPTER 4 → SAMPLE PROCEDURES"
    assert {
        index: record["metadata"]["section_path"]
        for index, record in zip(body, records)
    } == {
        0: "",
        4: f"{chapter} → A. FIRST TOPIC",
        6: f"{chapter} → A. FIRST TOPIC → 1. FIRST SUBTOPIC",
        9: (f"{chapter} → A. FIRST TOPIC → 1. FIRST SUBTOPIC "
            "→ Sample Agency v. Sample Board → 101 U.S. 1 (1900)."),
        12: (f"{chapter} → A. FIRST TOPIC → III. Analysis "
             "→ A. Governing Principles"),
        14: (f"{chapter} → A. FIRST TOPIC → III. Analysis "
             "→ A. Governing Principles → NOTES AND QUESTIONS"),
        16: f"{chapter} → A. FIRST TOPIC → 2. SECOND SUBTOPIC",
        18: f"{chapter} → B. SECOND TOPIC",
    }
    assert records[1]["metadata"]["headings"] == [
        "CHAPTER 4", "SAMPLE PROCEDURES", "A. FIRST TOPIC"]


def test_source_heading_paths_pass_independent_lineage_audit():
    document, body = _mid_chapter_excerpt()
    records = [_record(f"#/texts/{index}") for index in body]
    rag._assign_source_heading_paths(
        document, records, structural_ranges=set(),
        excluded_heading_refs=set())

    heading_lineage.attach_heading_bindings(document, records)
    audit = heading_lineage.audit_heading_bindings(document, records)

    assert {
        field: audit[field] for field in (
            "missing_refs", "scope_conflict_records",
            "display_binding_records", "unattached_refs",
            "missing_direct_refs", "duplicate_direct_refs",
            "invalid_binding_records")
    } == {
        "missing_refs": [], "scope_conflict_records": [],
        "display_binding_records": [], "unattached_refs": [],
        "missing_direct_refs": [], "duplicate_direct_refs": [],
        "invalid_binding_records": [],
    }
