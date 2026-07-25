import json
from pathlib import Path
from types import SimpleNamespace

import pytest

import rag


_PROFILE_FIXTURES = Path(__file__).parent / "fixtures" / "structure_profiles"


def _item(label, text, page):
    return {"label": label, "text": text, "prov": [{"page_no": page}]}


def _cell(text, row, col, *, top=0, bottom=10, col_end=None):
    return {
        "text": text,
        "start_row_offset_idx": row,
        "end_row_offset_idx": row + 1,
        "start_col_offset_idx": col,
        "end_col_offset_idx": col + 1 if col_end is None else col_end,
        "bbox": {"l": col * 100, "r": (col + 1) * 100,
                 "t": top, "b": bottom, "coord_origin": "TOPLEFT"},
    }


def _table(page, cells):
    return {"prov": [{"page_no": page}], "data": {"table_cells": cells}}


def test_identify_book_sections_uses_explicit_contiguous_spans():
    texts = [
        _item("section_header", "Summary of Contents", 9),
        _item("page_header", "Summary of Contents", 10),
        _item("section_header", "Contents", 13),
        _item("page_header", "Contents", 14),
        _item("page_header", "Contents", 15),
        _item("section_header", "Table of Problems", 17),
        _item("page_header", "Table of Problems", 18),
        _item("section_header", "Acknowledgments", 20),
        _item("page_header", "xxi Acknowledgments", 21),
        _item("page_header", "Chapter 1 - Beginning", 30),
        _item("section_header", "About the Authors", 90),
        _item("page_header", "About the Authors", 91),
        _item("section_header", "Table of Cases", 92),
        _item("section_header", "Table of Rules, Statutes, and Standards", 94),
        _item("section_header", "Index", 97),
        _item("page_header", "Index", 98),
    ]
    body_table = _table(50, [
        _cell("Chapter 8", row, 0) if row == 0
        else _cell(f"Entry {row}", row, 0)
        for row in range(4)
    ] + [_cell(str(100 + row), row, 1) for row in range(4)])
    doc = {"pages": {str(i): {} for i in range(1, 101)},
           "texts": texts, "tables": [body_table]}

    sections = rag._identify_book_sections(doc)

    assert sections["summary"] == {"start": 9, "end": 10}
    assert sections["contents"] == {"start": 13, "end": 15}
    assert sections["toc"] == {"start": 9, "end": 15}
    assert sections["problems"] == {"start": 17, "end": 18}
    assert sections["acknowledgments"] == {"start": 20, "end": 21}
    assert sections["about_authors"] == {"start": 90, "end": 91}
    assert sections["index"] == {"start": 97, "end": 98}


def test_toc_parser_splits_declared_row_by_visual_geometry():
    cells = [
        _cell("I. Prior section", 0, 0, top=10, bottom=20),
        _cell("Chapter 12: Duties to Third Persons", 0, 1,
              top=40, bottom=51),
        _cell("673", 0, 2, top=42, bottom=52),
    ]

    entries = rag._parse_toc_tables(
        {"tables": [_table(5, cells)]}, 5, 5)

    assert entries == [{
        "level": 1,
        "title": "Chapter 12: Duties to Third Persons",
        "page": 673,
    }]


def test_toc_parser_recovers_merged_lines_and_removes_leaders():
    cells = [
        _cell("Problem 2-2: Evidence . . . . by others in an office", 0, 0),
        _cell(". . 109 . . 110", 0, 1),
    ]

    entries = rag._parse_toc_tables(
        {"tables": [_table(5, cells)]}, 5, 5)

    assert entries == [
        {"level": 3, "title": "Problem 2-2: Evidence", "page": 109},
        {"level": 3, "title": "by others in an office", "page": 110},
    ]


def test_toc_parser_rejects_problem_row_as_chapter_boundary():
    cells = [
        _cell("Chapter 4: Confidences — 4-1 Dinner with Anna", 0, 0),
        _cell("220", 0, 1),
    ]

    assert rag._parse_toc_tables(
        {"tables": [_table(5, cells)]}, 5, 5) == []


def test_canonical_titles_are_earliest_valid_not_last_write_wins():
    scaffold = [
        {"level": 1, "title": "Chapter 1: Admission", "page": 10,
         "chapter_num": 1},
        {"level": 1, "title": "Chapter 1: Admission — 1-1 Problem", "page": 20,
         "chapter_num": 1},
    ]

    assert rag._canonical_chapter_titles(scaffold, {}) == {
        1: "Chapter 1: Admission",
    }


def test_scaffold_lookup_prefers_new_chapter_on_shared_page_and_is_bounded():
    scaffold = [
        {"page": 9, "chapter_num": 1, "path": "Chapter 1 > Last section"},
        {"page": 10, "chapter_num": 1, "path": "Chapter 1 > Carryover"},
        {"page": 10, "chapter_num": 2, "path": "Chapter 2"},
    ]

    lookup = rag._build_scaffold_lookup(scaffold, max_page=12)

    assert lookup[10]["chapter_num"] == 2
    assert lookup[12]["chapter_num"] == 2
    assert 13 not in lookup


def test_default_profile_fixture_preserves_casebook_structure():
    doc = json.loads((_PROFILE_FIXTURES / "publisher_alpha_casebook.json")
                     .read_text(encoding="utf-8"))

    sections = rag._identify_book_sections(doc)
    entries = rag._parse_toc_tables(doc, 2, 2)
    chapter_map = rag._build_chapter_map_from_document(doc)

    assert sections["toc"] == {"start": 2, "end": 2}
    assert sections["index"] == {"start": 19, "end": 19}
    assert entries == [
        {"level": 1, "title": "Chapter 1: Foundations", "page": 5},
        {"level": 2, "title": "A. First Principles", "page": 6},
    ]
    assert chapter_map[1]["title"] == "Foundations"
    assert chapter_map[1]["min_page"] == 5
    assert chapter_map[1]["max_page"] == 6


def test_roman_parts_profile_fixture_normalizes_ordinals_end_to_end():
    profile = "roman-parts-book-v1"
    doc = json.loads((_PROFILE_FIXTURES / "publisher_beta_roman_parts.json")
                     .read_text(encoding="utf-8"))

    sections = rag._identify_book_sections(
        doc, structure_profile=profile)
    entries = rag._parse_toc_tables(
        doc, 3, 3, structure_profile=profile)
    chapter_map = rag._build_chapter_map_from_document(
        doc, structure_profile=profile)
    scaffold = rag._build_scaffold(
        doc, sections, structure_profile=profile)
    titles = rag._canonical_chapter_titles(
        scaffold, chapter_map, structure_profile=profile)

    assert sections["toc"] == {"start": 3, "end": 3}
    assert sections["bibliography"] == {"start": 35, "end": 35}
    assert sections["index"] == {"start": 39, "end": 39}
    assert entries == [
        {"level": 1,
         "title": "Part IV — Institutions and Practice", "page": 10},
        {"level": 2, "title": "I. Institutions", "page": 11},
    ]
    assert chapter_map[4] == {
        "title": "Institutions And Practice",
        "division_kind": "Part",
        "division_number": "IV",
        "min_page": 10,
        "max_page": 11,
    }
    assert scaffold[0]["chapter_num"] == 4
    assert scaffold[0]["division_number"] == "IV"
    assert titles == {4: "Part IV — Institutions and Practice"}


def test_wrong_profile_fails_closed_instead_of_guessing_layout():
    doc = json.loads((_PROFILE_FIXTURES / "publisher_beta_roman_parts.json")
                     .read_text(encoding="utf-8"))
    sections = rag._identify_book_sections(doc)

    with pytest.raises(ValueError, match="does not match structure profile"):
        rag._build_scaffold(doc, sections)


def test_recognized_contents_with_no_primary_divisions_fails_closed():
    doc = {
        "texts": [
            _item("section_header", "Contents", 1),
            _item("text", "Publisher note without numbered divisions", 1),
        ],
        "tables": [],
    }

    with pytest.raises(ValueError, match="does not match structure profile"):
        rag._build_scaffold(
            doc, {"contents": {"start": 1, "end": 1}})


def test_llm_scaffold_cannot_invent_profile_source_evidence(
        monkeypatch):
    doc = json.loads((_PROFILE_FIXTURES / "publisher_beta_roman_parts.json")
                     .read_text(encoding="utf-8"))
    sections = rag._identify_book_sections(doc)

    class ImmediateTeam:
        def __init__(self, *_args, **_kwargs):
            pass

        def run(self, *, expert_fn, **_kwargs):
            return {"result": expert_fn(), "flagged": []}

    monkeypatch.setattr(rag, "_AgentTeam", ImmediateTeam)
    monkeypatch.setattr(rag, "_analyze_toc_layout", lambda *_a, **_k: {})
    monkeypatch.setattr(
        rag, "_llm_parse_scaffold",
        lambda *_a, **_k: [{
            "level": 1,
            "title": "Chapter 4: Hallucinated legal layout",
            "page": 10,
        }],
    )

    with pytest.raises(ValueError, match="does not match structure profile"):
        rag._build_scaffold(
            doc, sections, use_llm=True,
            ollama_url="http://127.0.0.1:11434")


def test_rejected_llm_hierarchy_uses_deterministic_scaffold(monkeypatch):
    profile = "roman-parts-book-v1"
    doc = json.loads((_PROFILE_FIXTURES / "publisher_beta_roman_parts.json")
                     .read_text(encoding="utf-8"))
    sections = rag._identify_book_sections(doc, structure_profile=profile)

    class ImmediateTeam:
        def __init__(self, *_args, **_kwargs):
            pass

        def run(self, *, expert_fn, **_kwargs):
            return {"result": expert_fn(), "flagged": []}

    monkeypatch.setattr(rag, "_AgentTeam", ImmediateTeam)
    monkeypatch.setattr(rag, "_calculate_page_delta", lambda _doc: 0)
    monkeypatch.setattr(rag, "_analyze_toc_layout", lambda *_a, **_k: {})
    monkeypatch.setattr(rag, "_llm_parse_scaffold", lambda *_a, **_k: [])

    scaffold = rag._build_scaffold(
        doc, sections, structure_profile=profile, use_llm=True,
        ollama_url="http://127.0.0.1:11434")

    assert [(entry["level"], entry["title"], entry["page"])
            for entry in scaffold] == [
        (1, "Part IV — Institutions and Practice", 10),
        (2, "I. Institutions", 11),
    ]


def test_valid_layout_analysis_is_only_forwarded_as_hierarchy_hints(
        monkeypatch):
    profile = "roman-parts-book-v1"
    doc = json.loads((_PROFILE_FIXTURES / "publisher_beta_roman_parts.json")
                     .read_text(encoding="utf-8"))
    sections = rag._identify_book_sections(doc, structure_profile=profile)
    layout = {
        "page_number_format": "trailing Arabic number",
        "division_pattern": "Part followed by Roman numeral",
        "division_examples": ["Part IV"],
        "section_markers": ["I."],
        "subsection_markers": [],
        "named_item_format": "",
        "hierarchy_order": ["Part", "Section"],
        "hierarchy_levels": {
            "1": "Part", "2": "Section", "3": "", "4": "", "5": "",
        },
    }
    observed = {}

    class ImmediateTeam:
        def __init__(self, *_args, **_kwargs):
            pass

        def run(self, *, expert_fn, **_kwargs):
            return {"result": expert_fn(), "flagged": []}

    def parse_scaffold(*_args, **kwargs):
        observed["layout_schema"] = kwargs["layout_schema"]
        return []

    monkeypatch.setattr(rag, "_AgentTeam", ImmediateTeam)
    monkeypatch.setattr(rag, "_calculate_page_delta", lambda _doc: 0)
    monkeypatch.setattr(
        rag, "_analyze_toc_layout", lambda *_args, **_kwargs: layout)
    monkeypatch.setattr(rag, "_llm_parse_scaffold", parse_scaffold)

    scaffold = rag._build_scaffold(
        doc, sections, structure_profile=profile, use_llm=True,
        ollama_url="http://127.0.0.1:11434")

    assert observed["layout_schema"] is layout
    assert [(entry["level"], entry["title"]) for entry in scaffold] == [
        (1, "Part IV — Institutions and Practice"),
        (2, "I. Institutions"),
    ]


def test_llm_scaffold_keeps_every_deterministic_primary_title(monkeypatch):
    doc = {
        "texts": [],
        "tables": [_table(3, [
            _cell("Chapter 1: Real Source Title", 0, 0),
            _cell("5", 0, 1),
            _cell("Chapter 2: Second Source Title", 1, 0),
            _cell("10", 1, 1),
        ])],
    }
    generated = [{
        "level": 1,
        "title": "Chapter 1: Hallucinated Replacement",
        "page": 5,
    }, {
        "level": 1,
        "title": "Chapter 2: Another Hallucination",
        "page": 10,
    }]

    class ImmediateTeam:
        def __init__(self, *_args, **_kwargs):
            pass

        def run(self, *, expert_fn, **_kwargs):
            return {"result": expert_fn(), "flagged": []}

    monkeypatch.setattr(rag, "_AgentTeam", ImmediateTeam)
    monkeypatch.setattr(rag, "_calculate_page_delta", lambda _doc: 0)
    monkeypatch.setattr(rag, "_analyze_toc_layout", lambda *_a, **_k: {})
    monkeypatch.setattr(
        rag, "_llm_parse_scaffold",
        lambda *_a, **_k: [dict(entry) for entry in generated],
    )

    scaffold = rag._build_scaffold(
        doc, {"toc": {"start": 3, "end": 3}}, use_llm=True,
        ollama_url="http://127.0.0.1:11434")

    assert [entry["title"] for entry in scaffold] == [
        "Chapter 1: Real Source Title",
        "Chapter 2: Second Source Title",
    ]

    generated.pop()
    with pytest.raises(ValueError, match="does not match structure profile"):
        rag._build_scaffold(
            doc, {"toc": {"start": 3, "end": 3}}, use_llm=True,
            ollama_url="http://127.0.0.1:11434")


def test_roman_subnumber_row_is_not_a_primary_division():
    cells = [
        _cell("Part IV — Institutions — IV-1 Exercise", 0, 0),
        _cell("220", 0, 1),
    ]

    assert rag._parse_toc_tables(
        {"tables": [_table(5, cells)]}, 5, 5,
        structure_profile="roman-parts-book-v1") == []


def test_roman_canonical_title_skips_subnumber_artifact():
    scaffold = [
        {"level": 1, "title": "Part IV — Institutions — IV-1 Exercise",
         "page": 10, "chapter_num": 4},
        {"level": 1, "title": "Part IV — Institutions",
         "page": 11, "chapter_num": 4},
    ]

    assert rag._canonical_chapter_titles(
        scaffold, {}, structure_profile="roman-parts-book-v1") == {
            4: "Part IV — Institutions",
        }

    assert rag._canonical_chapter_titles(
        [], {
            4: {
                "title": "Institutions — IV-1 Exercise",
                "division_kind": "Part",
                "division_number": "IV",
            },
        }, structure_profile="roman-parts-book-v1") == {}


def test_publication_gate_rejects_roman_subnumber_chapter_title():
    title = "Part IV — Institutions — IV-1 Exercise"
    records = [{
        "text": "Substantive prose for the selected division.",
        "metadata": {
            "headings": [title],
            "section_path": title,
            "content_type": "author_narrative",
            "content_source": "body",
            "page_start": 10,
            "page_end": 10,
            "case_names": [],
            "chapter_num": 4,
            "chapter_title": title,
        },
    }]

    with pytest.raises(RuntimeError, match="chapter title contains TOC artifacts"):
        rag._validate_chunk_structure_for_publication(
            records,
            scaffold=[{"level": 1, "chapter_num": 4, "page": 10}],
            book_sections={},
            chapter_map={4: {"min_page": 10, "max_page": 10}},
            chapter_titles={4: title},
            structure_profile="roman-parts-book-v1",
        )


def test_heading_repair_uses_profile_native_roman_fallback(monkeypatch):
    observed = {}

    def fake_call(prompt, **_kwargs):
        observed["prompt"] = prompt
        return "Part IV > A. Institutions"

    monkeypatch.setattr(rag, "_call_llm", fake_call)

    result = rag._reconstruct_heading(
        "Substantive text", "B", 4, "",
        structure_profile="roman-parts-book-v1")

    assert result == "Part IV > A. Institutions"
    assert 'division "Part IV"' in observed["prompt"]

    monkeypatch.setattr(
        rag, "_call_llm", lambda *_args, **_kwargs: (
            "Unrelated > Section 4 > Details"))
    assert rag._reconstruct_heading(
        "Substantive text", "B", 4, "",
        structure_profile="roman-parts-book-v1") is None

    for wrong_path in (
            "Wrong > Part IV > Details",
            "Not Part IV at all > Details"):
        monkeypatch.setattr(
            rag, "_call_llm",
            lambda *_args, _value=wrong_path, **_kwargs: _value)
        assert rag._reconstruct_heading(
            "Substantive text", "B", 4, "",
            structure_profile="roman-parts-book-v1") is None


def _record(text, heading, page, *, content_type="author_narrative"):
    return {
        "text": text,
        "metadata": {
            "headings": [heading],
            "section_path": heading,
            "content_type": content_type,
            "content_source": "body",
            "page_start": page,
            "page_end": page,
            "page_range": f"pp.{page}-{page}",
            "case_names": [],
            "primary_case": None,
            "cross_references": [],
            "chapter_num": None,
            "chapter_title": None,
            "token_count": len(text.split()),
        },
    }


def test_coalesce_reconstructs_alternating_rule_and_explanation_lanes():
    records = [
        _record("(a) A lawyer shall not act unless", "Rule Language", 1),
        _record("The exception is deliberately narrow.", "Authors' Explanation", 1),
        _record("the client gives informed consent", "Rule Language", 1),
        _record("Consent must be documented.", "Authors' Explanation", 2),
        _record("in writing.", "Rule Language", 2),
    ]

    repaired = rag._coalesce_chunk_boundaries(
        records, lambda text: len(text.split()), 100)

    assert len(repaired) == 2
    assert "unless\n\nthe client" in repaired[0]["text"]
    assert repaired[0]["text"].endswith("in writing.")
    assert "narrow.\n\nConsent" in repaired[1]["text"]


def test_coalesce_repairs_same_heading_sentence_split():
    records = [
        _record('The court explained that', "Quoted Opinion", 4),
        _record('the right attached at arraignment.', "Quoted Opinion", 5),
    ]

    repaired = rag._coalesce_chunk_boundaries(
        records, lambda text: len(text.split()), 100)

    assert len(repaired) == 1
    assert "that the right" in repaired[0]["text"]


def test_coalesce_orders_text_marked_rule_lane_before_explanation_lane():
    records = [
        _record(
            "Rule language**\n(a) A lawyer represents the organization.",
            "Rule 1.13 Organization as Client", 5),
        _record(
            "Authors' explanation***\nThe organization is the client.",
            "Rule 1.13 Organization as Client", 5),
        _record(
            "Rule language**\n(b) A lawyer shall proceed as necessary.",
            "Rule 1.13 Organization as Client", 6),
        _record(
            "Authors' explanation***\nParagraph (b) requires action.",
            "Rule 1.13 Organization as Client", 6),
    ]

    repaired = rag._coalesce_chunk_boundaries(
        records, lambda text: len(text.split()), 100)

    assert len(repaired) == 2
    assert repaired[0]["text"].startswith("Rule language")
    assert "(a)" in repaired[0]["text"] and "(b)" in repaired[0]["text"]
    assert repaired[1]["text"].startswith("Authors' explanation")


def test_coalesce_does_not_reorder_two_lane_source_tables():
    table = _record(
        "| Rule language | Authors' explanation |\n|---|---|\n| A | B |",
        "Authors' explanation", 5)
    table["metadata"].update({
        "content_type": "table", "content_source": "table"})
    explanation = _record(
        "The following prose resumes the discussion.",
        "Authors' explanation", 5)

    repaired = rag._coalesce_chunk_boundaries(
        [table, explanation], lambda text: len(text.split()), 100)

    assert repaired == [table, explanation]


def test_coalesce_moves_boundary_footnotes_after_continued_sentence():
    records = [
        _record(
            "Earlier sentence. Institutions have\n32. First citation.",
            "Discussion", 4),
        _record(
            "33. Second citation.\nincreasingly resisted inquiry.",
            "Discussion", 5),
    ]

    repaired = rag._coalesce_chunk_boundaries(
        records, lambda text: len(text.split()), 100)

    assert len(repaired) == 1
    assert "Institutions have increasingly resisted inquiry." in repaired[0]["text"]
    assert repaired[0]["text"].endswith(
        "32. First citation.\n33. Second citation.")


def test_publication_gate_rejects_structural_page_leak():
    title = "Chapter 1: Admission"
    record = _record("Back matter", title, 30)
    record["metadata"].update({
        "chapter_num": 1,
        "chapter_title": title,
        "section_path": title,
    })

    with pytest.raises(RuntimeError, match="retains structural"):
        rag._validate_chunk_structure_for_publication(
            [record],
            scaffold=[{"level": 1, "title": title, "page": 10,
                       "chapter_num": 1}],
            book_sections={"about_authors": {"start": 30, "end": 31}},
            chapter_map={1: {"min_page": 10, "max_page": 29,
                             "title": "Admission"}},
            chapter_titles={1: title},
        )


def test_publication_gate_rejects_text_hygiene_regressions():
    title = "Chapter 1: Admission"
    record = _record("See https:// www .example .com.", title, 10)
    record["metadata"].update({
        "chapter_num": 1,
        "chapter_title": title,
        "section_path": title,
    })

    with pytest.raises(RuntimeError, match="split URL"):
        rag._validate_chunk_structure_for_publication(
            [record],
            scaffold=[{"level": 1, "title": title, "page": 10,
                       "chapter_num": 1}],
            book_sections={},
            chapter_map={1: {"min_page": 10, "max_page": 19,
                             "title": "Admission"}},
            chapter_titles={1: title},
        )


def test_publication_gate_preserves_two_lane_rule_explanation_tables():
    title = "Chapter 1: Admission"
    record = _record(
        "| Rule language | Authors' explanation |\n"
        "|---|---|\n"
        "| A lawyer shall act. | This provision requires action. |",
        "Authors' explanation**",
        10,
    )
    record["metadata"].update({
        "chapter_num": 1,
        "chapter_title": title,
        "section_path": title,
        "content_type": "table",
        "content_source": "table",
    })

    rag._validate_chunk_structure_for_publication(
        [record],
        scaffold=[{"level": 1, "title": title, "page": 10,
                   "chapter_num": 1}],
        book_sections={},
        chapter_map={1: {"min_page": 10, "max_page": 19,
                         "title": "Admission"}},
        chapter_titles={1: title},
    )


def test_publication_gate_still_rejects_non_table_author_lane_mismatch():
    title = "Chapter 1: Admission"
    record = _record(
        "This prose explains the rule.", "Authors' explanation", 10)
    record["metadata"].update({
        "chapter_num": 1,
        "chapter_title": title,
        "section_path": title,
        "content_type": "statutory_excerpt",
        "content_source": "body",
    })

    with pytest.raises(RuntimeError, match="misclassifies author explanation"):
        rag._validate_chunk_structure_for_publication(
            [record],
            scaffold=[{"level": 1, "title": title, "page": 10,
                       "chapter_num": 1}],
            book_sections={},
            chapter_map={1: {"min_page": 10, "max_page": 19,
                             "title": "Admission"}},
            chapter_titles={1: title},
        )


def test_restored_markdown_table_is_row_packed_with_repeated_headers():
    markdown = (
        "| Issue | Synopsis |\n"
        "|---|---|\n"
        "| One | Alpha beta gamma delta |\n"
        "| Two | Epsilon zeta eta theta |\n"
        "| Three | Iota kappa lambda mu |"
    )

    parts = rag._split_markdown_table_by_rows(
        markdown, lambda text: len(text.split()), max_tokens=14)

    assert len(parts) == 3
    assert all(part.startswith("| Issue | Synopsis |\n|---|---|\n")
               for part in parts)
    assert all(len(part.split()) <= 14 for part in parts)
    assert [line for part in parts for line in part.splitlines()[2:]] == [
        "| One | Alpha beta gamma delta |",
        "| Two | Epsilon zeta eta theta |",
        "| Three | Iota kappa lambda mu |",
    ]


def test_restored_captioned_table_repeats_preamble_when_row_packed():
    markdown = (
        "Rule 1.18 Duties to Prospective Client\n\n"
        "| Rule language | Authors' explanation |\n"
        "|---|---|\n"
        "| One | Alpha beta gamma delta |\n"
        "| Two | Epsilon zeta eta theta |"
    )

    parts = rag._split_markdown_table_by_rows(
        markdown, lambda text: len(text.split()), max_tokens=18)

    assert len(parts) == 2
    assert all(part.startswith(
        "Rule 1.18 Duties to Prospective Client\n"
        "| Rule language | Authors' explanation |\n|---|---|\n")
        for part in parts)
    assert [part.splitlines()[-1] for part in parts] == [
        "| One | Alpha beta gamma delta |",
        "| Two | Epsilon zeta eta theta |",
    ]


def test_source_table_recovery_ignores_token_fusion_without_text_loss():
    assert not rag._source_table_needs_recovery(
        "Counsel worked in New York and New York again.",
        "Counsel worked in NewYork and NewYork again.",
    )


def test_source_table_recovery_catches_short_low_ratio_truncation():
    shared = " ".join(f"word{index}" for index in range(100))
    assert rag._source_table_needs_recovery(
        f"{shared} The contingent fee is calculated.",
        f"{shared} The contingent fee is",
    )


def test_source_table_recovery_preserves_referenced_caption():
    caption = SimpleNamespace(
        self_ref="#/texts/1", text="Rule 3.3 Candor Toward the Tribunal")
    table = SimpleNamespace(
        captions=[SimpleNamespace(cref="#/texts/1")])

    restored = rag._prepend_source_table_captions(
        "| Rule | Explanation |\n|---|---|\n| Text | Meaning |",
        table, {caption.self_ref: caption})

    assert restored.startswith(
        "Rule 3.3 Candor Toward the Tribunal\n\n| Rule | Explanation |")


def test_source_lineage_expands_table_caption_and_geometry():
    origin = SimpleNamespace(value="BOTTOMLEFT")
    provenance = SimpleNamespace(
        page_no=7,
        bbox=SimpleNamespace(
            l=1.23456, t=8.76543, r=9.0, b=2.0,
            coord_origin=origin),
    )
    caption = SimpleNamespace(
        self_ref="#/texts/2", label="caption", prov=[provenance])
    table = SimpleNamespace(
        self_ref="#/tables/0", label="table", prov=[provenance],
        captions=[SimpleNamespace(cref=caption.self_ref)],
        footnotes=[], children=[])
    document = SimpleNamespace(
        texts=[caption], pictures=[], tables=[table],
        key_value_items=[], form_items=[])
    item_by_ref, parents, captions = rag._docling_lineage_catalog(document)

    lineage = rag._source_lineage_for_items(
        [table], item_by_ref=item_by_ref,
        parent_refs_by_child=parents,
        caption_refs_by_parent=captions)

    assert [item["ref"] for item in lineage] == [
        "#/tables/0", "#/texts/2"]
    assert lineage[1]["parent_refs"] == ["#/tables/0"]
    assert lineage[0]["spans"] == [{
        "page": 7,
        "bbox": [1.235, 8.765, 9.0, 2.0],
        "origin": "BOTTOMLEFT",
    }]


def test_source_preparation_recovers_omitted_caption_identity_from_exact_text():
    origin = SimpleNamespace(value="BOTTOMLEFT")
    def provenance(top):
        return SimpleNamespace(
            page_no=7,
            bbox=SimpleNamespace(
                l=1.0, t=top, r=10.0, b=top - 1,
                coord_origin=origin))

    body = SimpleNamespace(
        self_ref="#/texts/1", label="text", content_layer="body",
        text="Theophylline is discussed in this paragraph.",
        prov=[provenance(20)])
    caption = SimpleNamespace(
        self_ref="#/texts/2", label="caption", content_layer="body",
        text="Theophylline", prov=[provenance(10)])
    document = SimpleNamespace(
        texts=[body, caption], pictures=[], tables=[],
        key_value_items=[], form_items=[])
    raw = SimpleNamespace(
        text=body.text,
        meta=SimpleNamespace(headings=["Problem"], doc_items=[body]))

    prepared = rag._prepare_source_preserving_chunks(
        [raw], document, lambda text: len(text.split()), 100,
        structural_ranges=set())

    assert len(prepared) == 1
    assert prepared[0][0] == body.text
    assert prepared[0][2] == [body, caption]
    assert prepared[0][3] is True


def test_mixed_table_chunk_preserves_prose_and_emits_table_once():
    before = SimpleNamespace(
        self_ref="#/texts/1", label="text", text="Before the table.")
    caption = SimpleNamespace(
        self_ref="#/texts/2", label="caption", text="Table caption")
    after = SimpleNamespace(
        self_ref="#/texts/3", label="text", text="After the table.")
    footnote = SimpleNamespace(
        self_ref="#/texts/4", label="footnote", text="1. Table citation.")

    class Table:
        self_ref = "#/tables/0"
        label = "table"
        captions = [SimpleNamespace(cref="#/texts/2")]
        footnotes = [SimpleNamespace(cref="#/texts/4")]

        @staticmethod
        def export_to_markdown(*, doc):
            assert doc is document
            return (
                "Table caption\n\n"
                "| Issue | Result |\n|---|---|\n| One | Preserved |"
            )

    table = Table()
    document = SimpleNamespace(
        texts=[before, caption, after, footnote], pictures=[], tables=[table],
        key_value_items=[], form_items=[])
    meta = SimpleNamespace(
        headings=["Example"], doc_items=[before, caption, table, after])
    first = SimpleNamespace(meta=meta, text="flattened table serialization")
    duplicate = SimpleNamespace(
        meta=SimpleNamespace(headings=["Example"], doc_items=[table]),
        text="duplicate flattened serialization")
    duplicate_footnote = SimpleNamespace(
        meta=SimpleNamespace(headings=["Example"], doc_items=[footnote]),
        text="1. Table citation.")

    prepared = rag._prepare_source_preserving_chunks(
        [first, duplicate, duplicate_footnote], document,
        lambda text: len(text.split()), 100)

    assert [part[0] for part in prepared] == [
        "Before the table.",
        "Table caption\n\n| Issue | Result |\n|---|---|\n| One | Preserved |",
        "1. Table citation.",
        "After the table.",
    ]
    assert all(part[3] is True for part in prepared)
    assert prepared[0][2] == [before]
    assert prepared[1][2] == [table]
    assert prepared[2][2] == [footnote]
    assert prepared[3][2] == [after]


def test_picture_nested_footnote_is_emitted_after_parent_chunk():
    footnote = SimpleNamespace(
        self_ref="#/texts/1", label="footnote", text="7. Figure source.")
    picture = SimpleNamespace(
        self_ref="#/pictures/0", label="picture",
        footnotes=[SimpleNamespace(cref="#/texts/1")])
    document = SimpleNamespace(
        texts=[footnote], pictures=[picture], tables=[],
        key_value_items=[], form_items=[])
    raw = SimpleNamespace(
        text="Figure caption and surrounding discussion.",
        meta=SimpleNamespace(headings=["Example"], doc_items=[picture]))

    prepared = rag._prepare_source_preserving_chunks(
        [raw], document, lambda text: len(text.split()), 100)

    assert [part[0] for part in prepared] == [
        "Figure caption and surrounding discussion.",
        "7. Figure source.",
    ]
    assert prepared[0][3] is False
    assert prepared[1][2] == [footnote]
    assert prepared[1][3] is True


def test_unseen_picture_parent_places_nested_footnote_on_source_page():
    provenance = SimpleNamespace(page_no=8)
    body = SimpleNamespace(
        self_ref="#/texts/1", label="text", text="Page discussion.",
        prov=[provenance])
    footnote = SimpleNamespace(
        self_ref="#/texts/2", label="footnote", text="4. Figure source.",
        prov=[provenance])
    picture = SimpleNamespace(
        self_ref="#/pictures/0", label="picture",
        footnotes=[SimpleNamespace(cref="#/texts/2")])
    document = SimpleNamespace(
        texts=[body, footnote], pictures=[picture], tables=[],
        key_value_items=[], form_items=[])
    raw = SimpleNamespace(
        text="Page discussion.",
        meta=SimpleNamespace(headings=["Section"], doc_items=[body]))

    prepared = rag._prepare_source_preserving_chunks(
        [raw], document, lambda text: len(text.split()), 100)

    assert [part[0] for part in prepared] == [
        "Page discussion.", "4. Figure source."]
    assert prepared[1][1] == ["Section"]
    assert prepared[1][2] == [footnote]
    assert prepared[1][3] is True


def test_source_geometry_separates_answers_merged_across_headings():
    def provenance(top):
        return [SimpleNamespace(
            page_no=8,
            bbox=SimpleNamespace(
                t=top,
                coord_origin=SimpleNamespace(value="BOTTOMLEFT"),
            ),
        )]

    first_heading = SimpleNamespace(
        self_ref="#/texts/0", label="section_header",
        text="Are you sure?", prov=provenance(500))
    second_heading = SimpleNamespace(
        self_ref="#/texts/1", label="section_header",
        text="Did they bill for secretarial time?", prov=provenance(400))
    first_answer = SimpleNamespace(
        self_ref="#/texts/2", label="text", text="Absolutely.",
        prov=provenance(480))
    second_answer = SimpleNamespace(
        self_ref="#/texts/3", label="text", text="No.",
        prov=provenance(380))
    document = SimpleNamespace(
        texts=[first_heading, second_heading, first_answer, second_answer],
        pictures=[], tables=[], key_value_items=[], form_items=[])
    raw = SimpleNamespace(
        text="Absolutely.\nNo.",
        meta=SimpleNamespace(
            headings=["Did they bill for secretarial time?"],
            doc_items=[first_answer, second_answer]))

    prepared = rag._prepare_source_preserving_chunks(
        [raw], document, lambda text: len(text.split()), 100)

    assert [part[0] for part in prepared] == ["Absolutely.", "No."]
    assert [part[1] for part in prepared] == [
        ["Are you sure?"], ["Did they bill for secretarial time?"]]


def test_source_preparation_separates_structural_boundary_items():
    def provenance(page, top):
        return [SimpleNamespace(
            page_no=page,
            bbox=SimpleNamespace(
                t=top,
                coord_origin=SimpleNamespace(value="BOTTOMLEFT"),
            ),
        )]

    preface_heading = SimpleNamespace(
        self_ref="#/texts/0", label="section_header",
        text="Preface to the Sixth Edition", prov=provenance(29, 500))
    structural_item = SimpleNamespace(
        self_ref="#/texts/1", label="text",
        text="Final table-of-problems entry.", prov=provenance(27, 100))
    substantive_item = SimpleNamespace(
        self_ref="#/texts/2", label="text",
        text="This book is an introduction to the law governing lawyers.",
        prov=provenance(29, 480))
    document = SimpleNamespace(
        texts=[preface_heading, structural_item, substantive_item],
        pictures=[], tables=[], key_value_items=[], form_items=[])
    raw = SimpleNamespace(
        text=("Final table-of-problems entry.\n"
              "This book is an introduction to the law governing lawyers."),
        meta=SimpleNamespace(
            headings=["Table of Problems"],
            doc_items=[structural_item, substantive_item]))

    prepared = rag._prepare_source_preserving_chunks(
        [raw], document, lambda text: len(text.split()), 100,
        structural_ranges={(25, 27)})

    assert [part[0] for part in prepared] == [
        "Final table-of-problems entry.",
        "This book is an introduction to the law governing lawyers.",
    ]
    assert prepared[0][1] == ["Table of Problems"]
    assert prepared[1][1] == ["Preface to the Sixth Edition"]
    assert all(part[3] is True for part in prepared)
