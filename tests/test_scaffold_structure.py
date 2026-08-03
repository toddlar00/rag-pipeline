import json
import logging
from contextlib import contextmanager
from pathlib import Path
from types import SimpleNamespace

import pytest

import quality_core
import rag
import source_fidelity_core


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
    assert sections["index"] == {"start": 97, "end": 100}


def test_identify_book_sections_limits_toc_seeds_to_front_matter():
    chapter_pages = [57, 111, 253, 421, 507, 573, 633, 711, 825, 881]
    texts = [
        _item("section_header", "Summary of Contents", 25),
        _item("page_header", "Summary of Contents", 26),
        _item("section_header", "Contents", 29),
        _item("page_header", "Contents", 42),
    ]
    for number, page in enumerate(chapter_pages, start=1):
        texts.extend([
            _item("section_header", f"Chapter {number} Test Chapter", page),
            _item(
                "text" if number % 2 == 0 else "section_header",
                "Summary of Contents",
                page,
            ),
        ])
    doc = {
        "pages": {str(page): {} for page in range(1, 1011)},
        "texts": texts,
        "tables": [],
    }

    sections = rag._identify_book_sections(doc)
    structural_ranges = rag._book_structural_ranges(sections)

    assert sections["summary"] == {"start": 25, "end": 26}
    assert sections["contents"] == {"start": 29, "end": 42}
    assert sections["toc"] == {"start": 25, "end": 42}
    assert sections["front_matter"] == {"start": 1, "end": 56}
    assert structural_ranges == {
        (1, 24), (1, 56), (25, 26), (25, 42), (29, 42),
    }
    assert any(start <= 56 <= end for start, end in structural_ranges)
    assert not any(start <= 57 <= end for start, end in structural_ranges)
    assert all(
        not any(start <= page <= end for start, end in structural_ranges)
        for page in chapter_pages
    )


def test_identify_book_sections_recognizes_table_of_laws_backmatter():
    doc = {
        "pages": {str(page): {} for page in range(1, 101)},
        "texts": [
            _item(
                "section_header",
                "Table of Statutes, Directives, and Standards",
                90,
            ),
            _item(
                "page_header",
                "Table of Statutes, Directives, and Standards",
                91,
            ),
        ],
        "tables": [],
    }

    sections = rag._identify_book_sections(doc)

    assert sections["table_of_rules"] == {"start": 90, "end": 91}


def test_identify_book_sections_recognizes_secondary_sources_backmatter():
    doc = {
        "pages": {str(page): {} for page in range(1, 101)},
        "texts": [
            _item("section_header", "Table of Authorities", 90),
            _item("page_header", "Table of Authorities", 91),
        ],
        "tables": [],
    }

    sections = rag._identify_book_sections(doc)

    assert sections["table_of_rules"] == {"start": 90, "end": 91}


def test_identify_book_sections_retains_self_assessment_as_separate_matter():
    doc = {
        "pages": {str(page): {} for page in range(1, 101)},
        "texts": [
            _item(
                "section_header",
                "S E L F - A S S E S S M E N T   Q U E S T I O N S",
                80,
            ),
            _item("page_header", "Self-Assessment Questions", 81),
            _item("page_header", "Self-Assessment Questions", 82),
        ],
        "tables": [],
    }

    sections = rag._identify_book_sections(doc)
    profile = rag._document_profiles.get_profile(
        rag.DEFAULT_STRUCTURE_PROFILE)

    assert sections["self_assessment"] == {"start": 80, "end": 82}
    assert "self_assessment" not in (
        rag._document_profiles.structural_section_keys(profile))
    assert rag._book_matter_ranges(doc_sections := sections) >= {
        (80, 82),
    }
    assert (80, 82) not in rag._book_structural_ranges(doc_sections)


def test_identify_book_sections_recognizes_spaced_index_opener():
    doc = {
        "pages": {str(page): {} for page in range(1, 101)},
        "texts": [
            _item("page_header", "I N D E X", 90),
            _item("page_header", "Index", 91),
        ],
        "tables": [],
    }

    sections = rag._identify_book_sections(doc)

    assert sections["index"] == {"start": 90, "end": 100}


def test_identify_book_sections_recognizes_text_only_appendix_and_index():
    doc = {
        "pages": {str(page): {} for page in range(1, 101)},
        "texts": [
            _item("text", "APPENDIX", 80),
            _item("text", "APPENDIX", 82),
            _item("text", "INDEX", 95),
        ],
        "tables": [],
    }

    sections = rag._identify_book_sections(doc)
    profile = rag._document_profiles.get_profile(
        rag.DEFAULT_STRUCTURE_PROFILE)

    assert sections["appendix"] == {"start": 80, "end": 82}
    assert sections["index"] == {"start": 95, "end": 100}
    assert (80, 82) in rag._book_matter_ranges(sections)
    assert (80, 82) not in rag._book_structural_ranges(sections)
    assert "appendix" not in (
        rag._document_profiles.structural_section_keys(profile))


def test_chapter_map_reassembles_split_page_header_items():
    doc = {
        "texts": [
            _item("page_header", "CHAPTER 4", 20),
            _item("section_header", "THE SENSOR CALIBRATION PROCESS", 20),
            _item("page_header", "198", 21),
            _item("page_header", "THE SENSOR CALIBRATION PROCESS", 21),
            _item("page_header", "CH. 4", 21),
        ],
    }

    chapter_map = rag._build_chapter_map_from_document(doc)

    assert chapter_map == {
        4: {
            "title": "The Sensor Calibration Process",
            "division_kind": "Chapter",
            "division_number": "4",
            "min_page": 20,
            "max_page": 21,
        },
    }


def test_toc_parser_splits_declared_row_by_visual_geometry():
    cells = [
        _cell("I. Prior section", 0, 0, top=10, bottom=20),
        _cell("Chapter 12: Remote Sensor Maintenance", 0, 1,
              top=40, bottom=51),
        _cell("673", 0, 2, top=42, bottom=52),
    ]

    entries = rag._parse_toc_tables(
        {"tables": [_table(5, cells)]}, 5, 5)

    assert entries == [{
        "level": 1,
        "title": "Chapter 12: Remote Sensor Maintenance",
        "page": 673,
    }]


def test_toc_parser_recovers_merged_lines_and_removes_leaders():
    cells = [
        _cell("Exercise 2-2: Input samples . . . . from a backup channel", 0, 0),
        _cell(". . 109 . . 110", 0, 1),
    ]

    entries = rag._parse_toc_tables(
        {"tables": [_table(5, cells)]}, 5, 5)

    assert entries == [
        {"level": 3, "title": "Exercise 2-2: Input samples", "page": 109},
        {"level": 3, "title": "from a backup channel", "page": 110},
    ]


def test_toc_parser_does_not_join_short_titled_chapter_to_summary_row():
    cells = [
        _cell("Chapter 3 · System Setup", 0, 0),
        _cell("199", 0, 1),
        _cell("Summary of Contents", 1, 0),
        _cell("199", 1, 1),
    ]

    entries = rag._parse_toc_tables(
        {"tables": [_table(5, cells)]}, 5, 5)

    assert entries == [
        {"level": 1, "title": "Chapter 3 · System Setup", "page": 199},
        {"level": 3, "title": "Summary of Contents", "page": 199},
    ]


def test_toc_parser_still_joins_bare_chapter_label_to_next_row():
    cells = [
        _cell("CHAPTER 3", 0, 0),
        _cell("System Setup", 1, 0),
        _cell("199", 1, 1),
    ]

    assert rag._parse_toc_tables(
        {"tables": [_table(5, cells)]}, 5, 5) == [{
            "level": 1,
            "title": "CHAPTER 3 — System Setup",
        "page": 199,
    }]


def test_toc_parser_joins_wrapped_titled_chapter_with_space():
    cells = [
        _cell(
            "Chapter 9 · Monitoring Remote and Intermittent",
            0,
            0,
            col_end=2,
        ),
        _cell("Device Signals", 1, 0),
        _cell("825", 1, 1),
    ]

    entries = rag._parse_toc_tables(
        {"tables": [_table(5, cells)]}, 5, 5)

    assert entries == [{
        "level": 1,
        "title": (
            "Chapter 9 · Monitoring Remote and Intermittent "
            "Device Signals"
        ),
        "page": 825,
    }]
    assert "Intermittent — Device" not in entries[0]["title"]


def test_toc_parser_promotes_complete_numbered_summary_sequence():
    cells = []
    for row, (number, title, page) in enumerate((
            ("1.", "System Overview", "1"),
            ("2.", "Device Inputs", "33"),
            ("3.", "Operator Approval", "61"),
            ("4.", "Deployment Request", "105"),
    )):
        cells.extend((
            _cell(number, row, 0),
            _cell(title, row, 1),
            _cell(page, row, 2),
        ))

    entries = rag._parse_toc_tables(
        {"tables": [_table(9, cells)]}, 9, 9, summary_span=(9, 9))

    assert entries == [
        {"level": 1, "title": "Chapter 1: System Overview",
         "page": 1},
        {"level": 1, "title": "Chapter 2: Device Inputs", "page": 33},
        {"level": 1, "title": "Chapter 3: Operator Approval", "page": 61},
        {"level": 1, "title": "Chapter 4: Deployment Request", "page": 105},
    ]


def test_toc_parser_does_not_promote_incomplete_numbered_summary():
    cells = [
        _cell("1.", 0, 0), _cell("First section", 0, 1),
        _cell("10", 0, 2),
        _cell("3.", 1, 0), _cell("Third section", 1, 1),
        _cell("20", 1, 2),
        _cell("4.", 2, 0), _cell("Fourth section", 2, 1),
        _cell("30", 2, 2),
    ]

    entries = rag._parse_toc_tables(
        {"tables": [_table(9, cells)]}, 9, 9, summary_span=(9, 9))

    assert [entry["level"] for entry in entries] == [3, 3, 3]


def test_toc_parser_suppresses_only_duplicate_primary_division_keys():
    cells = [
        _cell("Chapter 1: Foundations", 0, 0, col_end=3),
        _cell("3", 0, 3),
        _cell("A. First Principles", 1, 0), _cell("4", 1, 1),
        _cell("Chapter 1: Foundations — Summary", 2, 0, col_end=3),
        _cell("4", 2, 3),
        _cell("B. Later Principles", 3, 0), _cell("5", 3, 1),
        _cell("Chapter 2: Liability", 4, 0, col_end=3),
        _cell("20", 4, 3),
        _cell("Part 1: Remedies", 5, 0, col_end=3),
        _cell("20", 5, 3),
    ]

    entries = rag._parse_toc_tables(
        {"tables": [_table(2, cells)]}, 2, 2)

    assert entries == [
        {"level": 1, "title": "Chapter 1: Foundations", "page": 3},
        {"level": 2, "title": "A. First Principles", "page": 4},
        {"level": 2, "title": "B. Later Principles", "page": 5},
        {"level": 1, "title": "Chapter 2: Liability", "page": 20},
        {"level": 1, "title": "Part 1: Remedies", "page": 20},
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


def test_scaffold_assignment_end_uses_backmatter_after_missing_final_header():
    scaffold = [
        {"level": 1, "chapter_num": 1, "page": 10},
        {"level": 1, "chapter_num": 2, "page": 20},
        {"level": 1, "chapter_num": 3, "page": 30},
    ]
    chapter_map = {
        1: {"min_page": 10, "max_page": 19},
        2: {"min_page": 20, "max_page": 29},
    }

    assert rag._scaffold_assignment_end(
        scaffold, chapter_map, {(50, 60)}) == 49


def test_scaffold_qc_rejects_chapter_assignment_after_matter_boundary():
    scaffold = [
        {"level": 1, "chapter_num": 1, "page": 10, "path": "Chapter 1"},
    ]
    records = [{
        "text": "retained assessment",
        "metadata": {
            "page_start": 20,
            "chapter_num": 1,
            "section_path": "Chapter 1",
        },
    }]

    result = rag._validate_against_scaffold(
        records, scaffold, {}, assignment_end=19)

    assert result["passed"] is False
    assert result["tail_assignments"] == [0]
    assert "retain chapter assignments" in result["flagged"][0]


def _scaffold_qc_heading_record(page, title, occurrence_id, *, path="source"):
    return {
        "text": f"Body under {title}",
        "metadata": {
            "page_start": page,
            "page_end": page,
            "chapter_num": 1,
            "section_path": path,
            "headings": [title],
            "heading_path_components": [{
                "display": title,
                "occurrence_ids": [occurrence_id],
                "binding": "source_heading",
            }],
        },
    }


def test_scaffold_qc_snapshot_binds_without_mutating_output_records():
    def source_item(ref, label, text, page, top):
        return {
            "self_ref": ref,
            "label": label,
            "content_layer": "body",
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

    heading = source_item(
        "#/texts/0", "section_header", "Introduction", 1, 700)
    body = source_item("#/texts/1", "text", "Body", 1, 650)
    document = {
        "texts": [heading, body],
        "tables": [],
        "pictures": [],
        "groups": [],
        "body": {"children": [
            {"cref": heading["self_ref"]}, {"cref": body["self_ref"]},
        ]},
        "pages": {
            "1": {"size": {"width": 500, "height": 800}},
            "2": {"size": {"width": 500, "height": 800}},
        },
    }
    records = [{
        "text": "Body",
        "metadata": {
            "page_start": 1,
            "page_end": 1,
            "chapter_num": 1,
            "section_path": "Introduction",
            "headings": ["Introduction"],
            "content_source": "body",
            "source_items": [{
                "ref": body["self_ref"], "label": "text", "spans": [],
            }],
        },
    }]
    scaffold = [
        {"level": 3, "chapter_num": 1, "page": 1,
         "title": "Introduction", "path": "Chapter 1 > A > Introduction"},
        {"level": 3, "chapter_num": 1, "page": 2,
         "title": "Introduction", "path": "Chapter 1 > B > Introduction"},
    ]

    result = rag._validate_bound_scaffold_snapshot(
        records, scaffold, {}, document=document, assignment_end=2,
        structural_ranges=set(), excluded_heading_refs=set())

    assert result["coverage"] == 0.5
    assert result["represented_scaffold_entries"] == 1
    assert result["heading_occurrence_representations"] == 1
    assert "heading_path_components" not in records[0]["metadata"]
    assert "heading_path_ids" not in records[0]["metadata"]


def test_scaffold_qc_flags_replace_failure_and_clear_on_pass(tmp_path):
    path = tmp_path / "book_qc_flags.json"
    path.write_text("stale", encoding="utf-8")

    written = rag._publish_scaffold_qc_flags(
        path, passed=False,
        programmatic_check={"passed": False, "coverage": 0.25},
        doc_path=Path("book.json"), team_audit=[])

    assert written is True
    assert json.loads(path.read_text(encoding="utf-8"))["coverage"] == 0.25
    cleared = rag._publish_scaffold_qc_flags(
        path, passed=True, programmatic_check={"passed": True},
        doc_path=Path("book.json"), team_audit=[])
    assert cleared is False
    assert not path.exists()


def test_scaffold_qc_represents_repeated_titles_by_distinct_occurrence():
    scaffold = [
        {"level": 3, "chapter_num": 1, "page": 10,
         "title": "Introduction", "path": "Chapter 1 > A > Introduction"},
        {"level": 3, "chapter_num": 1, "page": 20,
         "title": "Introduction", "path": "Chapter 1 > B > Introduction"},
    ]
    records = [
        _scaffold_qc_heading_record(10, "Introduction", "#/texts/10"),
        _scaffold_qc_heading_record(20, "Introduction", "#/texts/20"),
    ]

    result = rag._validate_against_scaffold(records, scaffold, {})

    assert result["coverage"] == 1.0
    assert result["represented_scaffold_entries"] == 2
    assert result["heading_occurrence_representations"] == 2
    assert result["unrepresented_scaffold_entries"] == []


def test_scaffold_qc_does_not_reuse_absent_repeated_title_occurrence():
    scaffold = [
        {"level": 3, "chapter_num": 1, "page": 10,
         "title": "Introduction", "path": "Chapter 1 > A > Introduction"},
        {"level": 3, "chapter_num": 1, "page": 20,
         "title": "Introduction", "path": "Chapter 1 > B > Introduction"},
        {"level": 3, "chapter_num": 1, "page": 30,
         "title": "Introduction", "path": "Chapter 1 > C > Introduction"},
    ]
    # The same source occurrence is carried into a later record. It remains
    # one heading occurrence and cannot stand in for the absent rows at 20/30.
    records = [
        _scaffold_qc_heading_record(10, "Introduction", "#/texts/10"),
        _scaffold_qc_heading_record(20, "Introduction", "#/texts/10"),
    ]

    result = rag._validate_against_scaffold(records, scaffold, {})

    assert result["coverage"] == pytest.approx(1 / 3)
    assert result["represented_scaffold_entries"] == 1
    assert [entry["page"] for entry in
            result["unrepresented_scaffold_entries"]] == [20, 30]
    assert result["passed"] is False
    assert result["flagged"] == [
        "Low scaffold coverage: 33% of scaffold occurrences represented"]


def test_scaffold_qc_preserves_unique_exact_path_evidence():
    path = "Chapter 1 > Exact Section"
    scaffold = [{
        "level": 3, "chapter_num": 1, "page": 10,
        "title": "Exact Section", "path": path,
    }]
    records = [{
        "text": "Body",
        "metadata": {
            "page_start": 10,
            "page_end": 10,
            "chapter_num": 1,
            "section_path": path,
        },
    }]

    result = rag._validate_against_scaffold(records, scaffold, {})

    assert result["coverage"] == 1.0
    assert result["exact_path_representations"] == 1
    assert result["passed"] is True


def test_scaffold_qc_excludes_entries_after_assignment_end():
    represented_path = "Chapter 1 > Represented Section"
    scaffold = [
        {"level": 3, "chapter_num": 1, "page": 10,
         "title": "Represented Section", "path": represented_path},
        {"level": 3, "chapter_num": 1, "page": 100,
         "title": "Table of Cases", "path": "Table of Cases"},
    ]
    records = [{
        "text": "Body",
        "metadata": {
            "page_start": 10,
            "page_end": 10,
            "chapter_num": 1,
            "section_path": represented_path,
        },
    }]

    result = rag._validate_against_scaffold(
        records, scaffold, {}, assignment_end=50)

    assert result["coverage"] == 1.0
    assert result["scaffold_entries"] == 1
    assert result["excluded_scaffold_entries"] == 1
    assert result["passed"] is True


def test_scaffold_qc_repeated_exact_paths_require_trusted_scope():
    first_path = "Chapter 1 > A > Introduction"
    second_path = "Chapter 1 > B > Introduction"
    scaffold = [
        {"level": 3, "chapter_num": 1, "page": 0,
         "title": "Introduction", "path": first_path},
        {"level": 3, "chapter_num": 1, "page": -1,
         "title": "Introduction", "path": second_path},
    ]
    records = [
        _scaffold_qc_heading_record(
            10, "Introduction", "#/texts/10", path=first_path),
        _scaffold_qc_heading_record(
            20, "Introduction", "#/texts/20", path=second_path),
    ]

    result = rag._validate_against_scaffold(records, scaffold, {})

    assert result["coverage"] == 0.0
    assert result["represented_scaffold_entries"] == 0
    assert result["exact_path_representations"] == 0
    assert len(result["unrepresented_scaffold_entries"]) == 2


def test_repair_bare_scaffold_marker_uses_adjacent_source_heading():
    scaffold = [{"level": 3, "title": "A", "page": 50}]
    document = {"texts": [
        _item("text", "A", 50),
        _item("section_header", "EXTERNAL SERVICE MODES", 50),
        _item(
            "section_header",
            "1. Registered and Guest Operators",
            50,
        ),
    ]}

    repaired = rag._repair_bare_scaffold_section_markers(scaffold, document)

    assert repaired == 1
    assert scaffold == [{
        "level": 2,
        "title": "A. EXTERNAL SERVICE MODES",
        "page": 50,
    }]


def test_retained_matter_paths_restore_division_parent_from_source_lineage():
    document = {"texts": [
        {**_item("section_header", "Self-Assessment Questions", 80),
         "self_ref": "#/texts/0"},
        {**_item("text", "Overview", 80), "self_ref": "#/texts/1"},
        {**_item("section_header", "CHAPTER 1. BASICS", 80),
         "self_ref": "#/texts/2"},
        {**_item("section_header", "Question 1", 80),
         "self_ref": "#/texts/3"},
        {**_item("text", "First prompt text", 80), "self_ref": "#/texts/4"},
        {**_item("section_header", "Question 1 Answer", 80),
         "self_ref": "#/texts/5"},
        {**_item("text", "First response text", 80), "self_ref": "#/texts/6"},
        {**_item("section_header", "CHAPTER 2. DEVICES", 81),
         "self_ref": "#/texts/7"},
        {**_item("section_header", "Question 1", 81),
         "self_ref": "#/texts/8"},
        {**_item("text", "Second prompt text", 81), "self_ref": "#/texts/9"},
    ]}

    def record(page, heading, ref):
        return {"text": "body", "metadata": {
            "chapter_num": None,
            "page_start": page,
            "headings": [heading],
            "section_path": heading,
            "source_items": [{"ref": ref}],
        }}

    records = [
        record(80, "Self-Assessment Questions", "#/texts/1"),
        record(80, "Question 1", "#/texts/4"),
        record(80, "Question 1 Answer", "#/texts/6"),
        record(81, "Question 1", "#/texts/9"),
    ]
    sections = {"self_assessment": {"start": 80, "end": 81}}

    repaired = rag._apply_retained_matter_paths(
        records, document, sections)

    assert repaired == 3
    assert [record["metadata"]["section_path"] for record in records] == [
        "Self-Assessment Questions",
        "Self-Assessment Questions > Chapter 1: BASICS > Question 1",
        ("Self-Assessment Questions > Chapter 1: BASICS > "
         "Question 1 Answer"),
        "Self-Assessment Questions > Chapter 2: DEVICES > Question 1",
    ]


def test_generic_appendix_retains_generic_root_title():
    document = {"texts": [
        {**_item("section_header", "APPENDIX", 80),
         "self_ref": "#/texts/0"},
        {**_item("section_header", "General Reference Material", 80),
         "self_ref": "#/texts/1"},
        {**_item("text", "Reference discussion.", 80),
         "self_ref": "#/texts/2"},
    ]}
    record = {
        "text": "Reference discussion.",
        "metadata": {
            "chapter_num": None,
            "page_start": 80,
            "headings": ["APPENDIX", "General Reference Material"],
            "section_path": "General Reference Material",
            "source_items": [{"ref": "#/texts/2"}],
        },
    }

    repaired = rag._apply_retained_matter_paths(
        [record], document, {"appendix": {"start": 80, "end": 80}})

    assert repaired == 1
    assert record["metadata"]["section_path"] == (
        "Appendix > General Reference Material")


def test_default_profile_fixture_preserves_casebook_structure():
    doc = json.loads((_PROFILE_FIXTURES / "publisher_alpha_casebook.json")
                     .read_text(encoding="utf-8"))

    sections = rag._identify_book_sections(doc)
    entries = rag._parse_toc_tables(doc, 2, 2)
    chapter_map = rag._build_chapter_map_from_document(doc)

    assert sections["toc"] == {"start": 2, "end": 2}
    assert sections["index"] == {"start": 19, "end": 20}
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
    assert sections["index"] == {"start": 39, "end": 40}
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


def test_coalesce_dehyphenates_lowercase_cross_page_word_fragment():
    records = [
        _record("The calibration of the device and the ques-", "Review", 301),
        _record("tion of timing remains disputed.", "Review", 302),
    ]

    repaired = rag._coalesce_chunk_boundaries(
        records, lambda text: len(text.split()), 100)

    assert len(repaired) == 1
    assert repaired[0]["text"] == (
        "The calibration of the device and the question of timing remains "
        "disputed.")


def test_coalesce_preserves_punctuation_hyphen_before_uppercase_dialogue():
    records = [
        _record("I'm sorry, Mr. Boomer-", "Boomer", 845),
        _record("We really can't shut the plant down.", "Boomer", 845),
    ]

    repaired = rag._coalesce_chunk_boundaries(
        records, lambda text: len(text.split()), 100)

    assert len(repaired) == 1
    assert repaired[0]["text"] == (
        "I'm sorry, Mr. Boomer- We really can't shut the plant down.")


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


def test_publication_gate_rejects_deterministic_front_matter_leak():
    title = "Chapter 1: Introduction"
    record = _record("Online teaching materials", "Online Materials", 56)

    with pytest.raises(RuntimeError, match="retains structural"):
        rag._validate_chunk_structure_for_publication(
            [record],
            scaffold=[{"level": 1, "title": title, "page": 57,
                       "chapter_num": 1}],
            book_sections={"front_matter": {"start": 1, "end": 56}},
            chapter_map={1: {"min_page": 57, "max_page": 80,
                             "title": "Introduction"}},
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


def test_publication_gate_distinguishes_dialogue_dash_from_split_word():
    dialogue = _record(
        "I'm sorry, Mr. Boomer-\nWe really can't shut the plant down.",
        "Boomer",
        845,
    )
    rag._validate_chunk_structure_for_publication(
        [dialogue], scaffold=[], book_sections={}, chapter_map={},
        chapter_titles={})

    split_word = _record(
        "The relationship raises the ques- tion of foreseeability.",
        "Duty",
        301,
    )
    with pytest.raises(RuntimeError, match="split-hyphen artifact"):
        rag._validate_chunk_structure_for_publication(
            [split_word], scaffold=[], book_sections={}, chapter_map={},
            chapter_titles={})


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
        markdown, lambda text: len(text.split()), max_tokens=24)

    assert len(parts) == 2
    assert all(part.startswith(
        "Rule 1.18 Duties to Prospective Client\n"
        "| Rule language | Authors' explanation |\n|---|---|\n")
        for part in parts)
    assert [part.splitlines()[-1] for part in parts] == [
        "| One | Alpha beta gamma delta |",
        "| Two | Epsilon zeta eta theta |",
    ]


def test_single_oversized_table_row_splits_by_cell_under_repeated_headers():
    left = "A lawyer must disclose all material facts before the hearing"
    right = (
        "The explanation describes the tribunal duty and its carefully "
        "limited exceptions in several sentences")
    markdown = (
        "Rule 3.3 Candor\n"
        "| Rule language | Authors' explanation |\n"
        "|---|---|\n"
        f"| {left} | {right} |")

    parts = rag._split_markdown_table_by_rows(
        markdown, lambda text: len(text.split()), max_tokens=16)

    assert len(parts) > 1
    assert all(len(part.split()) <= 16 for part in parts)
    observed = [[], []]
    for part in parts:
        parsed = rag._table_retrieval_core._parse_markdown_table(part)
        assert parsed is not None and len(parsed.rows) == 1
        cells = rag._table_retrieval_core._split_markdown_row(parsed.rows[0])
        for index, cell in enumerate(cells):
            if cell:
                observed[index].append(cell)
    assert " ".join(observed[0]) == left
    assert " ".join(observed[1]) == right


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


def test_table_header_words_do_not_impersonate_declared_caption():
    caption = SimpleNamespace(
        self_ref="#/texts/2", text="Rule Value")
    table = SimpleNamespace(
        captions=[SimpleNamespace(cref=caption.self_ref)])
    grid = "| Rule | Value |\n| --- | --- |\n| Duty | Yes |"

    restored = rag._prepend_source_table_captions(
        grid, table, {caption.self_ref: caption})

    assert restored == f"{caption.text}\n\n{grid}"


def test_source_lineage_expands_table_caption_and_geometry():
    origin = SimpleNamespace(value="BOTTOMLEFT")
    provenance = SimpleNamespace(
        page_no=7,
        charspan=(0, 12),
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
        "provenance_index": 0,
        "page": 7,
        "bbox": [1.235, 8.765, 9.0, 2.0],
        "origin": "BOTTOMLEFT",
        "charspan": [0, 12],
    }]


def test_source_oracles_group_complete_visual_children_with_their_owner():
    child = SimpleNamespace(
        self_ref="#/texts/0", label="text", text="alpha beta",
        marker="", parent=SimpleNamespace(cref="#/pictures/0"), prov=[])
    unrelated = SimpleNamespace(
        self_ref="#/texts/1", label="text", text="gamma delta",
        marker="", parent=SimpleNamespace(cref="#/body"), prov=[])
    picture = SimpleNamespace(
        self_ref="#/pictures/0", label="picture", text="", orig="",
        children=[SimpleNamespace(cref=child.self_ref)], captions=[],
        footnotes=[], parent=SimpleNamespace(cref="#/body"), prov=[])
    document = SimpleNamespace(
        texts=[child, unrelated], pictures=[picture], tables=[],
        key_value_items=[], form_items=[])
    recovery = rag.FigureTextRecovery(
        text="Figure text alpha beta", page=1,
        bbox=(0.0, 0.0, 10.0, 10.0), page_area_ratio=0.1,
        mean_confidence=0.9, method="ocr")
    enrichments = rag.BoundSourceEnrichments(
        figure_text={picture.self_ref: recovery},
        manifest_input={"sha256": "a" * 64})
    prepared = [(
        recovery.text, None, [picture, child, unrelated], True)]

    oracles = rag._source_fidelity_oracles(
        document, enrichments, prepared_chunks=prepared)

    assert oracles[child.self_ref]["transform"] == "container_alias"
    assert (oracles[child.self_ref]["oracle_group_sha256"]
            == oracles[picture.self_ref]["oracle_group_sha256"])
    assert (oracles[child.self_ref]["oracle_lexical_tokens"]
            == oracles[picture.self_ref]["oracle_lexical_tokens"])
    assert oracles[unrelated.self_ref]["transform"] == "plain"


def test_source_oracles_alias_a_symbol_fused_to_figure_possessive():
    child = SimpleNamespace(
        self_ref="#/texts/0", label="text", text="π", marker="",
        parent=SimpleNamespace(cref="#/pictures/0"), prov=[])
    picture = SimpleNamespace(
        self_ref="#/pictures/0", label="picture", text="", orig="",
        children=[SimpleNamespace(cref=child.self_ref)], captions=[],
        footnotes=[], parent=SimpleNamespace(cref="#/body"), prov=[])
    document = SimpleNamespace(
        texts=[child], pictures=[picture], tables=[],
        key_value_items=[], form_items=[])
    recovery = rag.FigureTextRecovery(
        text="π's burden", page=1, bbox=(0.0, 0.0, 10.0, 10.0),
        page_area_ratio=0.1, mean_confidence=None, method="native")
    enrichments = rag.BoundSourceEnrichments(
        figure_text={picture.self_ref: recovery},
        manifest_input={"sha256": "a" * 64})

    oracles = rag._source_fidelity_oracles(
        document, enrichments,
        prepared_chunks=[(recovery.text, None, [picture, child], True)])

    assert oracles[child.self_ref]["transform"] == "container_alias"
    assert (oracles[child.self_ref]["oracle_group_sha256"]
            == oracles[picture.self_ref]["oracle_group_sha256"])


def test_source_oracles_group_table_caption_when_prepared_entry_has_table_only():
    caption = SimpleNamespace(
        self_ref="#/texts/0", label="caption", text="Table caption",
        marker="", parent=SimpleNamespace(cref="#/tables/0"), prov=[])
    table = SimpleNamespace(
        self_ref="#/tables/0", label="table", text="", orig="",
        children=[], captions=[SimpleNamespace(cref=caption.self_ref)],
        footnotes=[], parent=SimpleNamespace(cref="#/body"), prov=[])
    document = SimpleNamespace(
        texts=[caption], pictures=[], tables=[table],
        key_value_items=[], form_items=[])
    recovered = "Table caption\n\n| A |\n|---|\n| B |"
    enrichments = rag.BoundSourceEnrichments(
        table_markdown_overrides={table.self_ref: recovered},
        manifest_input={"sha256": "b" * 64})
    # Table preparation deliberately need not repeat its referenced caption in
    # entry_items: final lineage expands captions from the source relationship.
    prepared = [(recovered, None, [table], True)]

    oracles = rag._source_fidelity_oracles(
        document, enrichments, prepared_chunks=prepared)

    assert oracles[caption.self_ref]["transform"] == "container_alias"
    assert (oracles[caption.self_ref]["oracle_group_sha256"]
            == oracles[table.self_ref]["oracle_group_sha256"])
    assert (oracles[caption.self_ref]["oracle_lexical_tokens"]
            == oracles[table.self_ref]["oracle_lexical_tokens"])


def test_document_index_table_uses_bound_layout_recovery_oracle():
    cells = [
        SimpleNamespace(text="§ 2.01 First", orig=""),
        SimpleNamespace(text="A. Detail", orig=""),
    ]
    document_index = SimpleNamespace(
        self_ref="#/tables/0", label="document_index", text="", orig="",
        data=SimpleNamespace(table_cells=cells), children=[], captions=[],
        footnotes=[], parent=SimpleNamespace(cref="#/body"), prov=[])
    document = SimpleNamespace(
        texts=[], pictures=[], tables=[document_index],
        key_value_items=[], form_items=[])
    recovered = "- § 2.01 First\n  - A. Detail"
    enrichments = rag.BoundSourceEnrichments(
        table_markdown_overrides={document_index.self_ref: recovered},
        manifest_input={"sha256": "b" * 64})

    oracle = rag._source_fidelity_oracles(
        document, enrichments)[document_index.self_ref]

    assert oracle["transform"] == "table"
    assert oracle["oracle_lexical_tokens"] == list(
        rag._source_fidelity_core.lexical_tokens(recovered))
    assert oracle["source_text_sha256"] == (
        rag._source_fidelity_core.text_sha256(
            "§ 2.01 First\nA. Detail"))


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


def test_source_text_repairs_do_not_expand_repeated_long_item_slices():
    item = SimpleNamespace(
        self_ref="#/texts/1", label="text", content_layer="body",
        text="First Ju dge passage. Second under stand passage.", prov=[])
    document = SimpleNamespace(
        texts=[item], pictures=[], tables=[], key_value_items=[],
        form_items=[])
    raw_chunks = [
        SimpleNamespace(
            text="First Ju dge passage.",
            meta=SimpleNamespace(headings=[], doc_items=[item])),
        SimpleNamespace(
            text="Second under stand passage.",
            meta=SimpleNamespace(headings=[], doc_items=[item])),
    ]

    prepared = rag._prepare_source_preserving_chunks(
        raw_chunks, document, lambda text: len(text.split()), 100,
        structural_ranges=set(),
        text_overrides={
            "#/texts/1": "First Judge passage. Second understand passage."},
        text_repair_edits={
            "#/texts/1": (
                ("Ju dge", "Judge"),
                ("under stand", "understand"),
            ),
        })

    assert [entry[0] for entry in prepared] == [
        "First Judge passage.",
        "Second understand passage.",
    ]


def test_source_text_repair_matches_chunk_normalized_source_spacing():
    item = SimpleNamespace(
        self_ref="#/texts/1", label="text", content_layer="body",
        text=("Alpha-beta, gamma-delta, and "
              "epsilon -   zeta pairings."),
        prov=[])
    document = SimpleNamespace(
        texts=[item], pictures=[], tables=[], key_value_items=[],
        form_items=[])
    raw = SimpleNamespace(
        text=("Alpha-beta, gamma-delta, and "
              "epsilon -zeta pairings."),
        meta=SimpleNamespace(headings=[], doc_items=[item]))

    prepared = rag._prepare_source_preserving_chunks(
        [raw], document, lambda text: len(text.split()), 100,
        structural_ranges=set(),
        text_overrides={
            item.self_ref: ("Alpha-beta, gamma-delta, and "
                            "epsilon-zeta pairings.")},
        text_repair_edits={
            item.self_ref: (("epsilon -   zeta", "epsilon-zeta"),)},
    )

    assert prepared[0][0] == (
        "Alpha-beta, gamma-delta, and epsilon-zeta pairings.")


def test_last_long_item_slice_restores_unique_terminal_numeric_marker():
    item = SimpleNamespace(
        self_ref="#/texts/1", label="text", content_layer="body",
        text=(
            "The opening source passage has enough stable words for a slice. "
            "The closing source passage also has enough stable words here. 1"),
        prov=[])
    document = SimpleNamespace(
        texts=[item], pictures=[], tables=[], key_value_items=[],
        form_items=[])
    raw_chunks = [
        SimpleNamespace(
            text="The opening source passage has enough stable words for a slice.",
            meta=SimpleNamespace(headings=[], doc_items=[item])),
        SimpleNamespace(
            text="The closing source passage also has enough stable words here.",
            meta=SimpleNamespace(headings=[], doc_items=[item])),
    ]

    prepared = rag._prepare_source_preserving_chunks(
        raw_chunks, document, lambda text: len(text.split()), 100,
        structural_ranges=set())

    assert [entry[0] for entry in prepared] == [
        "The opening source passage has enough stable words for a slice.",
        "The closing source passage also has enough stable words here. 1",
    ]


def test_terminal_numeric_marker_repair_fails_closed_without_unique_long_suffix():
    source = "alpha beta gamma delta epsilon zeta eta theta 1"
    assert rag._restore_source_attested_terminal_numeric_marker(
        "epsilon zeta eta theta", source) == "epsilon zeta eta theta"
    repeated = (
        "one two three four five six seven eight "
        "one two three four five six seven eight 1")
    assert rag._restore_source_attested_terminal_numeric_marker(
        "one two three four five six seven eight", repeated
    ) == "one two three four five six seven eight"


def test_source_sensitive_list_rebuild_restores_commonmark_bullets():
    items = [
        SimpleNamespace(
            self_ref="#/texts/1", label="list_item", marker="1.",
            content_layer="body", text="Main rule", prov=[]),
        SimpleNamespace(
            self_ref="#/texts/2", label="list_item", marker="-",
            content_layer="body", text="First supporting point", prov=[]),
        SimpleNamespace(
            self_ref="#/texts/3", label="list_item", marker="-",
            content_layer="body", text="Second supporting point", prov=[]),
    ]
    document = SimpleNamespace(
        texts=items, pictures=[], tables=[], key_value_items=[], form_items=[])
    raw = SimpleNamespace(
        text="1. Main rule\n\nFirst supporting point\n\nSecond supporting point",
        meta=SimpleNamespace(headings=[], doc_items=items))

    prepared = rag._prepare_source_preserving_chunks(
        [raw], document, lambda text: len(text.split()), 100,
        structural_ranges=set())

    assert [entry[0] for entry in prepared] == [
        "1. Main rule\n\n- First supporting point\n\n- Second supporting point"]


def test_middle_dot_source_bullets_keep_existing_commonmark_spelling():
    items = [
        SimpleNamespace(
            self_ref=f"#/texts/{index}", label="list_item", marker="·",
            content_layer="body", text=text, prov=[])
        for index, text in enumerate(("First point", "Second point"), 1)
    ]
    document = SimpleNamespace(
        texts=items, pictures=[], tables=[], key_value_items=[], form_items=[])
    markdown = "- First point\n\n- Second point"
    raw = SimpleNamespace(
        text=markdown, meta=SimpleNamespace(headings=[], doc_items=items))

    prepared = rag._prepare_source_preserving_chunks(
        [raw], document, lambda text: len(text.split()), 100,
        structural_ranges=set())

    assert [entry[0] for entry in prepared] == [markdown]


def test_source_alpha_list_items_drop_chunker_bullet_wrappers():
    items = [
        SimpleNamespace(
            self_ref=f"#/texts/{index}", label="list_item", marker=marker,
            content_layer="body", text=text, prov=[])
        for index, (marker, text) in enumerate((
            ("a.", "First issue."),
            ("b.", "Second issue."),
        ), 1)
    ]
    document = SimpleNamespace(
        texts=items, pictures=[], tables=[], key_value_items=[], form_items=[])
    raw = SimpleNamespace(
        text="- a. First issue.\n\n- b. Second issue.",
        meta=SimpleNamespace(headings=[], doc_items=items))

    prepared = rag._prepare_source_preserving_chunks(
        [raw], document, lambda text: len(text.split()), 100,
        structural_ranges=set())

    assert [entry[0] for entry in prepared] == [
        "a. First issue.\n\nb. Second issue."]


def test_partial_source_alpha_run_drops_chunker_bullet_wrappers():
    items = [
        SimpleNamespace(
            self_ref="#/texts/1", label="list_item", marker="3.",
            content_layer="body", text="Lead question.", prov=[]),
        SimpleNamespace(
            self_ref="#/texts/2", label="list_item", marker="c.",
            content_layer="body", text="Third choice.", prov=[]),
        SimpleNamespace(
            self_ref="#/texts/3", label="list_item", marker="d.",
            content_layer="body", text="Fourth choice.", prov=[]),
    ]
    document = SimpleNamespace(
        texts=items, pictures=[], tables=[], key_value_items=[], form_items=[])
    raw = SimpleNamespace(
        text=("3. Lead question.\n\n- c. Third choice.\n\n"
              "- d. Fourth choice."),
        meta=SimpleNamespace(headings=[], doc_items=items))

    prepared = rag._prepare_source_preserving_chunks(
        [raw], document, lambda text: len(text.split()), 100,
        structural_ranges=set())

    assert [entry[0] for entry in prepared] == [
        "3. Lead question.\n\nc. Third choice.\n\nd. Fourth choice."]


def test_single_source_alpha_item_drops_chunker_bullet_wrapper():
    item = SimpleNamespace(
        self_ref="#/texts/1", label="list_item", marker="g.",
        content_layer="body", text="Final choice.", prov=[])
    document = SimpleNamespace(
        texts=[item], pictures=[], tables=[], key_value_items=[], form_items=[])
    raw = SimpleNamespace(
        text="- g. Final choice.",
        meta=SimpleNamespace(headings=[], doc_items=[item]))

    prepared = rag._prepare_source_preserving_chunks(
        [raw], document, lambda text: len(text.split()), 100,
        structural_ranges=set())

    assert [entry[0] for entry in prepared] == ["g. Final choice."]


@pytest.mark.parametrize(("marker", "source_text", "raw_text", "expected"), [
    ("A.", "First issue.", "- A. First issue.", "A. First issue."),
    ("12.", "Twelfth issue.", "- 12. Twelfth issue.",
     "12. Twelfth issue."),
    ("", "1 . Marker stored in text.", "- 1. Marker stored in text.",
     "1. Marker stored in text."),
])
def test_source_ordered_items_drop_chunker_bullet_wrapper(
        marker, source_text, raw_text, expected):
    item = SimpleNamespace(
        self_ref="#/texts/1", label="list_item", marker=marker,
        content_layer="body", text=source_text, prov=[])
    document = SimpleNamespace(
        texts=[item], pictures=[], tables=[], key_value_items=[], form_items=[])
    raw = SimpleNamespace(
        text=raw_text, meta=SimpleNamespace(headings=[], doc_items=[item]))

    prepared = rag._prepare_source_preserving_chunks(
        [raw], document, lambda text: len(text.split()), 100,
        structural_ranges=set())

    assert [entry[0] for entry in prepared] == [expected]


def test_generic_bullets_escape_literal_alpha_text_without_reinterpretation():
    items = [
        SimpleNamespace(
            self_ref=f"#/texts/{index}", label="list_item", marker="-",
            content_layer="body", text=text, prov=[])
        for index, text in enumerate((
            "a. Literal label in a bullet.",
            "b. Another literal label in a bullet.",
        ), 1)
    ]
    document = SimpleNamespace(
        texts=items, pictures=[], tables=[], key_value_items=[], form_items=[])
    markdown = (
        "- a. Literal label in a bullet.\n\n"
        "- b. Another literal label in a bullet.")
    raw = SimpleNamespace(
        text=markdown, meta=SimpleNamespace(headings=[], doc_items=items))

    prepared = rag._prepare_source_preserving_chunks(
        [raw], document, lambda text: len(text.split()), 100,
        structural_ranges=set())

    assert [entry[0] for entry in prepared] == [
        "- a\\. Literal label in a bullet.\n\n"
        "- b\\. Another literal label in a bullet."]


@pytest.mark.parametrize(("label", "expected"), [
    ("A.", "- A\\. Literal label."),
    ("12.", "- 12\\. Literal label."),
    ("iv.", "- iv\\. Literal label."),
])
def test_generic_bullets_escape_every_ordered_looking_label(label, expected):
    item = SimpleNamespace(
        self_ref="#/texts/1", label="list_item", marker="-",
        content_layer="body", text=f"{label} Literal label.", prov=[])
    document = SimpleNamespace(
        texts=[item], pictures=[], tables=[], key_value_items=[], form_items=[])
    raw = SimpleNamespace(
        text=f"- {label} Literal label.",
        meta=SimpleNamespace(headings=[], doc_items=[item]))

    prepared = rag._prepare_source_preserving_chunks(
        [raw], document, lambda text: len(text.split()), 100,
        structural_ranges=set())

    assert [entry[0] for entry in prepared] == [expected]


def test_same_parent_list_geometry_restores_nested_markdown_indent():
    group = SimpleNamespace(cref="#/groups/17")
    specifications = (
        ("3.", "Third issue.", 350, 72),
        ("4.", "Fourth issue.", 300, 72),
        ("5.", "Parent issue.", 250, 72),
        ("a.", "First child.", 200, 93),
        ("b.", "Second child.", 150, 93),
    )
    items = [
        SimpleNamespace(
            self_ref=f"#/texts/{index}", label="list_item", marker=marker,
            parent=group, content_layer="body", text=text,
            prov=_layout_provenance(
                953, left=left, right=440, top=top, bottom=top - 30),
        )
        for index, (marker, text, top, left) in enumerate(specifications, 1)
    ]
    document = SimpleNamespace(
        texts=items, pictures=[], tables=[], key_value_items=[], form_items=[])
    raw = SimpleNamespace(
        text=("3. Third issue.\n\n4. Fourth issue.\n\n5. Parent issue."
              "\n\na. First child.\n\nb. Second child."),
        meta=SimpleNamespace(headings=[], doc_items=items))

    prepared = rag._prepare_source_preserving_chunks(
        [raw], document, lambda text: len(text.split()), 100,
        structural_ranges=set())

    assert [entry[0] for entry in prepared] == [
        "3. Third issue.\n\n4. Fourth issue.\n\n5. Parent issue."
        "\n\n   a. First child.\n\n   b. Second child."]


def test_nested_list_indent_uses_enclosing_marker_width():
    group = SimpleNamespace(cref="#/groups/1")
    items = [
        SimpleNamespace(
            self_ref="#/texts/1", label="list_item", marker="10.",
            parent=group, content_layer="body", text="Parent issue.",
            prov=_layout_provenance(
                1, left=72, right=440, top=200, bottom=170)),
        SimpleNamespace(
            self_ref="#/texts/2", label="list_item", marker="a.",
            parent=group, content_layer="body", text="Child issue.",
            prov=_layout_provenance(
                1, left=93, right=440, top=150, bottom=120)),
    ]
    document = SimpleNamespace(
        texts=items, pictures=[], tables=[], key_value_items=[], form_items=[])
    raw = SimpleNamespace(
        text="10. Parent issue.\n\na. Child issue.",
        meta=SimpleNamespace(headings=[], doc_items=items))

    prepared = rag._prepare_source_preserving_chunks(
        [raw], document, lambda text: len(text.split()), 100,
        structural_ranges=set())

    assert [entry[0] for entry in prepared] == [
        "10. Parent issue.\n\n    a. Child issue."]


def test_list_nesting_is_restored_after_whitespace_normalization():
    group = SimpleNamespace(cref="#/groups/1")
    items = [
        SimpleNamespace(
            self_ref="#/texts/1", label="list_item", marker="5.",
            parent=group, content_layer="body", text="Parent issue.",
            prov=_layout_provenance(
                1, left=72, right=440, top=200, bottom=170)),
        SimpleNamespace(
            self_ref="#/texts/2", label="list_item", marker="a.",
            parent=group, content_layer="body", text="First child.",
            prov=_layout_provenance(
                1, left=93, right=440, top=150, bottom=120)),
        SimpleNamespace(
            self_ref="#/texts/3", label="list_item", marker="b.",
            parent=group, content_layer="body", text="Second child.",
            prov=_layout_provenance(
                1, left=93, right=440, top=100, bottom=70)),
    ]
    normalized = "5. Parent issue.\n\n a. First child.\n\n b. Second child."

    restored = rag._restore_source_list_item_markdown_indents(
        normalized, items, lambda item: item.text)

    assert restored == (
        "5. Parent issue.\n\n   a. First child.\n\n   b. Second child.")


def test_list_nesting_restore_is_atomic_when_a_marker_is_missing():
    group = SimpleNamespace(cref="#/groups/1")
    items = [
        SimpleNamespace(
            self_ref="#/texts/1", label="list_item", marker="5.",
            parent=group, text="Parent issue.",
            prov=_layout_provenance(
                1, left=72, right=440, top=200, bottom=170)),
        SimpleNamespace(
            self_ref="#/texts/2", label="list_item", marker="a.",
            parent=group, text="First child.",
            prov=_layout_provenance(
                1, left=93, right=440, top=150, bottom=120)),
        SimpleNamespace(
            self_ref="#/texts/3", label="list_item", marker="b.",
            parent=group, text="Second child.",
            prov=_layout_provenance(
                1, left=93, right=440, top=100, bottom=70)),
    ]
    incomplete = "5. Parent issue.\n\n a. First child."

    assert rag._restore_source_list_item_markdown_indents(
        incomplete, items, lambda item: item.text) == incomplete


def test_list_geometry_does_not_cross_source_parent_groups():
    outer = SimpleNamespace(cref="#/groups/outer")
    inner = SimpleNamespace(cref="#/groups/inner")
    items = [
        SimpleNamespace(
            self_ref="#/texts/1", label="list_item", marker="5.",
            parent=outer, content_layer="body", text="Outer issue.",
            prov=_layout_provenance(
                1, left=72, right=440, top=200, bottom=170)),
        SimpleNamespace(
            self_ref="#/texts/2", label="list_item", marker="a.",
            parent=inner, content_layer="body", text="Separate issue.",
            prov=_layout_provenance(
                1, left=93, right=440, top=150, bottom=120)),
    ]
    document = SimpleNamespace(
        texts=items, pictures=[], tables=[], key_value_items=[], form_items=[])
    markdown = "5. Outer issue.\n\na. Separate issue."
    raw = SimpleNamespace(
        text=markdown, meta=SimpleNamespace(headings=[], doc_items=items))

    prepared = rag._prepare_source_preserving_chunks(
        [raw], document, lambda text: len(text.split()), 100,
        structural_ranges=set())

    assert [entry[0] for entry in prepared] == [markdown]


def test_geometry_attested_lower_alpha_run_recovers_aligned_plain_tail():
    group = SimpleNamespace(cref="#/groups/1")
    items = [
        SimpleNamespace(
            self_ref="#/texts/1", label="list_item", marker="a.",
            parent=group, content_layer="body", text="First calibration step.",
            prov=_layout_provenance(
                241, left=72, right=440, top=291, bottom=241)),
        SimpleNamespace(
            self_ref="#/texts/2", label="text", marker="",
            parent=SimpleNamespace(cref="#/body"), content_layer="body",
            text="b. Second calibration step.", prov=_layout_provenance(
                241, left=72, right=440, top=233, bottom=182)),
    ]
    document = SimpleNamespace(
        texts=items, pictures=[], tables=[], key_value_items=[], form_items=[])
    raw_text = (
        "- a. First calibration step.\n\nb. Second calibration step.")
    raw = SimpleNamespace(
        text=raw_text, meta=SimpleNamespace(headings=[], doc_items=items))

    prepared = rag._prepare_source_preserving_chunks(
        [raw], document, lambda text: len(text.split()), 100,
        structural_ranges=set())

    assert [entry[0] for entry in prepared] == [
        "a. First calibration step.\n\nb. Second calibration step."]
    assert prepared[0][2] == items


def test_geometry_attested_lower_alpha_run_bridges_split_docling_groups():
    first_group = SimpleNamespace(cref="#/groups/17")
    second_group = SimpleNamespace(cref="#/groups/18")
    body = SimpleNamespace(cref="#/body")
    specifications = (
        ("list_item", "a.", "Alpha calibration.", first_group, 291, 241),
        ("text", "", "b. Bravo calibration.", body, 233, 182),
        ("list_item", "c.", "Charlie calibration.", second_group, 174, 137),
        ("list_item", "d.", "Delta calibration.", second_group, 129, 92),
    )
    items = [
        SimpleNamespace(
            self_ref=f"#/texts/{index}", label=label, marker=marker,
            parent=parent, content_layer="body", text=text,
            prov=_layout_provenance(
                241, left=72, right=440, top=top, bottom=bottom))
        for index, (label, marker, text, parent, top, bottom) in enumerate(
            specifications, 1)
    ]
    document = SimpleNamespace(
        texts=items, pictures=[], tables=[], key_value_items=[], form_items=[])
    raw_text = (
        "- a. Alpha calibration.\n\n"
        "b. Bravo calibration.\n\n"
        "- c. Charlie calibration.\n\n"
        "- d. Delta calibration."
    )
    raw = SimpleNamespace(
        text=raw_text, meta=SimpleNamespace(headings=[], doc_items=items))

    prepared = rag._prepare_source_preserving_chunks(
        [raw], document, lambda text: len(text.split()), 100,
        structural_ranges=set())

    assert [entry[0] for entry in prepared] == [
        "a. Alpha calibration.\n\n"
        "b. Bravo calibration.\n\n"
        "c. Charlie calibration.\n\n"
        "d. Delta calibration."
    ]
    assert prepared[0][2] == items


def test_geometry_attested_lower_alpha_run_refuses_misaligned_plain_text():
    items = [
        SimpleNamespace(
            self_ref="#/texts/1", label="list_item", marker="a.",
            content_layer="body", text="First calibration step.",
            prov=_layout_provenance(
                241, left=72, right=440, top=291, bottom=241)),
        SimpleNamespace(
            self_ref="#/texts/2", label="text", marker="",
            content_layer="body", text="b. Second calibration step.",
            prov=_layout_provenance(
                241, left=220, right=500, top=233, bottom=182)),
    ]
    document = SimpleNamespace(
        texts=items, pictures=[], tables=[], key_value_items=[], form_items=[])
    raw_text = (
        "- a. First calibration step.\n\nb. Second calibration step.")
    raw = SimpleNamespace(
        text=raw_text, meta=SimpleNamespace(headings=[], doc_items=items))

    prepared = rag._prepare_source_preserving_chunks(
        [raw], document, lambda text: len(text.split()), 100,
        structural_ranges=set())

    assert [entry[0] for entry in prepared] == [raw_text]
    assert prepared[0][2] == items


def test_geometry_attested_lower_alpha_run_refuses_nonconsecutive_marker():
    items = [
        SimpleNamespace(
            self_ref="#/texts/1", label="list_item", marker="a.",
            content_layer="body", text="First calibration step.",
            prov=_layout_provenance(
                241, left=72, right=440, top=291, bottom=241)),
        SimpleNamespace(
            self_ref="#/texts/2", label="text", marker="",
            content_layer="body", text="c. Third calibration step.",
            prov=_layout_provenance(
                241, left=72, right=440, top=233, bottom=182)),
    ]
    document = SimpleNamespace(
        texts=items, pictures=[], tables=[], key_value_items=[], form_items=[])
    raw_text = (
        "- a. First calibration step.\n\nc. Third calibration step.")
    raw = SimpleNamespace(
        text=raw_text, meta=SimpleNamespace(headings=[], doc_items=items))

    prepared = rag._prepare_source_preserving_chunks(
        [raw], document, lambda text: len(text.split()), 100,
        structural_ranges=set())

    assert [entry[0] for entry in prepared] == [raw_text]
    assert prepared[0][2] == items


def test_verified_repeated_native_item_replaces_mixed_replay_once():
    repeated = SimpleNamespace(
        self_ref="#/texts/1", label="list_item", marker="5.",
        content_layer="body",
        text="First corrupt passage. Second corrupt passage.", prov=[])
    following = SimpleNamespace(
        self_ref="#/texts/2", label="text", content_layer="body",
        text="Following source passage.", prov=[])
    document = SimpleNamespace(
        texts=[repeated, following], pictures=[], tables=[],
        key_value_items=[], form_items=[])
    raw_chunks = [
        SimpleNamespace(
            text="First corrupt passage.",
            meta=SimpleNamespace(headings=[], doc_items=[repeated])),
        SimpleNamespace(
            text=(
                "Second corrupt passage. Second corrupt passage. "
                "Following source passage."
            ),
            meta=SimpleNamespace(
                headings=[], doc_items=[repeated, following])),
    ]
    recovered = "First restored passage. Second restored passage."

    prepared = rag._prepare_source_preserving_chunks(
        raw_chunks, document, lambda text: len(text.split()), 100,
        structural_ranges=set(),
        text_overrides={repeated.self_ref: recovered})

    assert [entry[0] for entry in prepared] == [
        f"5. {recovered}", following.text]
    assert [
        [item.self_ref for item in entry[2] or []] for entry in prepared
    ] == [[repeated.self_ref], [following.self_ref]]


def test_coupled_repeated_native_items_replace_shared_mixed_replay_once():
    first = SimpleNamespace(
        self_ref="#/texts/1", label="text", content_layer="body",
        text="First corrupt opening. First corrupt ending.",
        prov=_layout_provenance(4, left=80, top=600))
    second = SimpleNamespace(
        self_ref="#/texts/2", label="text", content_layer="body",
        text="Second corrupt opening. Second corrupt ending.",
        prov=_layout_provenance(4, left=80, top=500))
    middle = SimpleNamespace(
        self_ref="#/texts/3", label="list_item", marker="4.",
        content_layer="body", text="Unique middle source item.",
        prov=_layout_provenance(4, left=80, top=550))
    document = SimpleNamespace(
        texts=[first, middle, second], pictures=[], tables=[],
        key_value_items=[], form_items=[])
    raw_chunks = [
        SimpleNamespace(
            text="First corrupt opening.",
            meta=SimpleNamespace(headings=[], doc_items=[first])),
        SimpleNamespace(
            text=(
                "First corrupt ending. 4. Unique middle source item. "
                "Second corrupt opening. "
                "Second corrupt opening."
            ),
            meta=SimpleNamespace(
                headings=[], doc_items=[first, middle, second])),
        SimpleNamespace(
            text="Second corrupt ending.",
            meta=SimpleNamespace(headings=[], doc_items=[second])),
    ]
    restored_first = "First restored opening. First restored ending."
    restored_second = "Second restored opening. Second restored ending."

    prepared = rag._prepare_source_preserving_chunks(
        raw_chunks, document, lambda text: len(text.split()), 100,
        structural_ranges=set(),
        text_overrides={
            first.self_ref: restored_first,
            second.self_ref: restored_second,
        },
        text_rebuild_refs={first.self_ref, second.self_ref})

    assert [entry[0] for entry in prepared] == [
        restored_first, "4. Unique middle source item.", restored_second]
    assert [
        [item.self_ref for item in entry[2] or []] for entry in prepared
    ] == [[first.self_ref], [middle.self_ref], [second.self_ref]]


def test_coupled_replay_closure_rejects_untrusted_repeated_bridge():
    items = [
        SimpleNamespace(
            self_ref=f"#/texts/{index}", label="text", content_layer="body",
            text=f"Source item {index} opening. Source item {index} ending.",
            prov=[])
        for index in range(1, 4)
    ]
    first, second, untrusted = items
    document = SimpleNamespace(
        texts=items, pictures=[], tables=[], key_value_items=[], form_items=[])
    raw_chunks = [
        SimpleNamespace(
            text="Source item 1 opening.",
            meta=SimpleNamespace(headings=[], doc_items=[first])),
        SimpleNamespace(
            text="Source item 1 ending. Source item 2 opening.",
            meta=SimpleNamespace(headings=[], doc_items=[first, second])),
        SimpleNamespace(
            text="Source item 2 ending.",
            meta=SimpleNamespace(headings=[], doc_items=[second])),
        SimpleNamespace(
            text="Source item 2 ending. Source item 3 opening.",
            meta=SimpleNamespace(headings=[], doc_items=[second, untrusted])),
        SimpleNamespace(
            text="Source item 3 ending.",
            meta=SimpleNamespace(headings=[], doc_items=[untrusted])),
    ]

    prepared = rag._prepare_source_preserving_chunks(
        raw_chunks, document, lambda text: len(text.split()), 100,
        structural_ranges=set(),
        text_overrides={
            first.self_ref: "Unsafe wholesale replacement A.",
            second.self_ref: "Unsafe wholesale replacement B.",
        })

    combined = "\n".join(entry[0] for entry in prepared)
    assert "Unsafe wholesale replacement A." not in combined
    assert "Unsafe wholesale replacement B." not in combined


def test_repeated_pure_list_slices_emit_declared_marker_once():
    item = SimpleNamespace(
        self_ref="#/texts/1", label="list_item", marker="9.",
        content_layer="body",
        text="First source passage. Second source passage.", prov=[])
    document = SimpleNamespace(
        texts=[item], pictures=[], tables=[], key_value_items=[],
        form_items=[])
    raw_chunks = [
        SimpleNamespace(
            text="First source passage.",
            meta=SimpleNamespace(headings=[], doc_items=[item])),
        SimpleNamespace(
            text="Second source passage.",
            meta=SimpleNamespace(headings=[], doc_items=[item])),
    ]

    prepared = rag._prepare_source_preserving_chunks(
        raw_chunks, document, lambda text: len(text.split()), 100,
        structural_ranges=set())

    assert [entry[0] for entry in prepared] == [
        "9. First source passage.", "Second source passage."]


def test_repeated_pure_list_wholesale_rebuild_replaces_stale_slices_once():
    item = SimpleNamespace(
        self_ref="#/texts/1", label="list_item", marker="9.",
        content_layer="body",
        text="First corrupt passage. Second corrupt passage.", prov=[])
    document = SimpleNamespace(
        texts=[item], pictures=[], tables=[], key_value_items=[],
        form_items=[])
    raw_chunks = [
        SimpleNamespace(
            text="First stale passage.",
            meta=SimpleNamespace(headings=[], doc_items=[item])),
        SimpleNamespace(
            text="Second stale junk tail.",
            meta=SimpleNamespace(headings=[], doc_items=[item])),
    ]
    recovered = "First restored passage. Second restored passage."

    prepared = rag._prepare_source_preserving_chunks(
        raw_chunks, document, lambda text: len(text.split()), 100,
        structural_ranges=set(), text_overrides={item.self_ref: recovered},
        text_rebuild_refs={item.self_ref})

    assert [entry[0] for entry in prepared] == [f"9. {recovered}"]
    assert prepared[0][2] == [item]


def test_native_list_marker_keeps_full_oracle_join_repair_aligned():
    item = SimpleNamespace(
        self_ref="#/texts/1", label="list_item", marker="4.",
        content_layer="body",
        text="The operatormust confirm every setting.", prov=[])
    document = SimpleNamespace(
        texts=[item], pictures=[], tables=[], key_value_items=[],
        form_items=[])
    raw = SimpleNamespace(
        text="4. The operatormust confirm every setting.",
        meta=SimpleNamespace(headings=[], doc_items=[item]))

    prepared = rag._prepare_source_preserving_chunks(
        [raw], document, lambda text: len(text.split()), 100,
        structural_ranges=set(),
        text_overrides={
            item.self_ref: "The operator must confirm every setting."},
    )

    assert [entry[0] for entry in prepared] == [
        "4. The operator must confirm every setting."]


def test_source_oracle_repairs_attested_multi_fragment_ocr_words():
    cases = (
        ("operationally effi -cient", "operationally efficient"),
        ("the techn ici an ' s report", "the technician's report"),
        ("be fo re startup", "before startup"),
        ("one con fig ur ed mode", "one configured mode"),
        ("sensorcalibra -tion", "sensorcalibration"),
    )

    for extracted, oracle in cases:
        assert rag._repair_text_from_source_oracle(extracted, oracle) == oracle
    assert rag._repair_text_from_source_oracle(
        "error - recovery", "error - recovery") == "error - recovery"


def test_unique_native_rebuild_publishes_exact_attested_override():
    item = SimpleNamespace(
        self_ref="#/texts/1", label="text", content_layer="body",
        text="The cal ib ra ted result is sensorcalibra -tion.", prov=[])
    document = SimpleNamespace(
        texts=[item], pictures=[], tables=[], key_value_items=[],
        form_items=[])
    raw = SimpleNamespace(
        text=item.text,
        meta=SimpleNamespace(headings=[], doc_items=[item]))
    oracle = "The calibrated result is sensorcalibration."

    prepared = rag._prepare_source_preserving_chunks(
        [raw], document, lambda text: len(text.split()), 100,
        structural_ranges=set(), text_overrides={item.self_ref: oracle},
        text_rebuild_refs={item.self_ref})

    assert [entry[0] for entry in prepared] == [oracle]
    assert prepared[0][2] == [item]


def test_unique_list_run_restores_declared_markers_without_auto_numbering():
    items = [
        SimpleNamespace(
            self_ref=f"#/texts/{index}", label="list_item", marker=marker,
            content_layer="body", text=text, prov=[])
        for index, (marker, text) in enumerate((
            ("1.", "First source item"),
            ("", "Unnumbered source item"),
            ("3.", "Third source item"),
        ))
    ]
    document = SimpleNamespace(
        texts=items, pictures=[], tables=[], key_value_items=[],
        form_items=[])
    raw = SimpleNamespace(
        text=(
            "1. First source item\n"
            "2. Unnumbered source item\n"
            "3. Third source item"
        ),
        meta=SimpleNamespace(headings=[], doc_items=items),
    )

    prepared = rag._prepare_source_preserving_chunks(
        [raw], document, lambda text: len(text.split()), 100,
        structural_ranges=set())

    assert [entry[0] for entry in prepared] == [
        "1. First source item\n\n"
        "Unnumbered source item\n\n"
        "3. Third source item"
    ]


def test_unique_adjacent_items_preserve_source_token_boundaries():
    first = SimpleNamespace(
        self_ref="#/texts/0", label="text", content_layer="body",
        text="ques", prov=[])
    second = SimpleNamespace(
        self_ref="#/texts/1", label="text", content_layer="body",
        text="tion", prov=[])
    document = SimpleNamespace(
        texts=[first, second], pictures=[], tables=[], key_value_items=[],
        form_items=[])
    raw = SimpleNamespace(
        text="question",
        meta=SimpleNamespace(headings=[], doc_items=[first, second]),
    )

    prepared = rag._prepare_source_preserving_chunks(
        [raw], document, lambda text: len(text.split()), 100,
        structural_ranges=set())

    assert [entry[0] for entry in prepared] == ["ques\n\ntion"]


def test_unique_item_removes_only_source_disproved_intraword_hyphen():
    item = SimpleNamespace(
        self_ref="#/texts/0", label="text", content_layer="body",
        text="The evidence is significant.", prov=[])
    document = SimpleNamespace(
        texts=[item], pictures=[], tables=[], key_value_items=[],
        form_items=[])
    raw = SimpleNamespace(
        text="The evi-dence is significant.",
        meta=SimpleNamespace(headings=[], doc_items=[item]),
    )

    prepared = rag._prepare_source_preserving_chunks(
        [raw], document, lambda text: len(text.split()), 100,
        structural_ranges=set())

    assert [entry[0] for entry in prepared] == [item.text]


def test_ambiguous_item_local_edit_does_not_mutate_repeated_lexemes():
    item = SimpleNamespace(
        self_ref="#/texts/0", label="text", content_layer="body",
        text="First evi dence differs from second evi dence.", prov=[])
    document = SimpleNamespace(
        texts=[item], pictures=[], tables=[], key_value_items=[],
        form_items=[])
    raw = SimpleNamespace(
        text=item.text,
        meta=SimpleNamespace(headings=[], doc_items=[item]))

    prepared = rag._prepare_source_preserving_chunks(
        [raw], document, lambda text: len(text.split()), 100,
        structural_ranges=set(),
        text_repair_edits={item.self_ref: (("evi dence", "evidence"),)})

    assert [entry[0] for entry in prepared] == [item.text]


def test_verified_native_fallback_rechunks_repeated_item_once():
    item = SimpleNamespace(
        self_ref="#/texts/1", label="text", content_layer="body",
        text="First corrupt passage. Second corrupt passage.", prov=[])
    document = SimpleNamespace(
        texts=[item], pictures=[], tables=[], key_value_items=[],
        form_items=[])
    raw_chunks = [
        SimpleNamespace(
            text="First corrupt passage.",
            meta=SimpleNamespace(headings=[], doc_items=[item])),
        SimpleNamespace(
            text="Second corrupt passage.",
            meta=SimpleNamespace(headings=[], doc_items=[item])),
    ]
    recovered = "First restored passage. Second restored passage."

    prepared = rag._prepare_source_preserving_chunks(
        raw_chunks, document, lambda text: len(text.split()), 100,
        structural_ranges=set(),
        text_overrides={"#/texts/1": recovered},
        text_rebuild_refs={"#/texts/1"},
    )

    assert [entry[0] for entry in prepared] == [recovered]
    assert prepared[0][2] == [item]


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


def test_mixed_table_chunk_separates_identical_heading_occurrences():
    section = SimpleNamespace(
        self_ref="#/texts/0", label="section_header", content_layer="body",
        text="§8.03 Interpreting Sensor States",
        prov=_layout_provenance(1, left=120, top=720))
    first_notes = SimpleNamespace(
        self_ref="#/texts/1", label="section_header", content_layer="body",
        text="Notes & Questions",
        prov=_layout_provenance(1, left=150, top=680))
    first_body = SimpleNamespace(
        self_ref="#/texts/2", label="text", content_layer="body",
        text="The first occurrence ends here.",
        prov=_layout_provenance(1, left=90, top=640))
    second_notes = SimpleNamespace(
        self_ref="#/texts/3", label="section_header", content_layer="body",
        text="Notes & Questions",
        prov=_layout_provenance(2, left=150, top=720))
    second_body = SimpleNamespace(
        self_ref="#/texts/4", label="text", content_layer="body",
        text="The second occurrence begins here.",
        prov=_layout_provenance(2, left=90, top=680))

    class Table:
        self_ref = "#/tables/0"
        label = "table"
        content_layer = "body"
        captions = []
        footnotes = []
        prov = _layout_provenance(2, left=90, top=600, bottom=500)

        @staticmethod
        def export_to_markdown(*, doc):
            assert doc is document
            return "| Signal | Result |\n|---|---|\n| Active | Preserved |"

    table = Table()
    document = SimpleNamespace(
        texts=[section, first_notes, first_body, second_notes, second_body],
        pictures=[], tables=[table], key_value_items=[], form_items=[])
    raw = SimpleNamespace(
        text="flattened mixed table serialization",
        meta=SimpleNamespace(
            headings=[section.text, second_notes.text],
            doc_items=[first_body, second_body, table]))

    prepared = rag._prepare_source_preserving_chunks(
        [raw], document, lambda text: len(text.split()), 100)

    assert [entry[0] for entry in prepared] == [
        first_body.text,
        second_body.text,
        "| Signal | Result |\n|---|---|\n| Active | Preserved |",
    ]
    assert [[item.self_ref for item in (entry[2] or [])]
            for entry in prepared] == [
        [first_body.self_ref], [second_body.self_ref], [table.self_ref]]
    assert prepared[0][1] == prepared[1][1] == [
        section.text, first_notes.text]
    assert prepared[2][1] == [section.text, second_notes.text]


def test_source_preparation_preserves_headerless_single_table_row_as_data():
    class Table:
        self_ref = "#/tables/0"
        label = "table"
        captions = []
        footnotes = []
        data = SimpleNamespace(
            num_rows=1,
            table_cells=[
                SimpleNamespace(column_header=False),
                SimpleNamespace(column_header=False),
                SimpleNamespace(column_header=False),
            ],
        )

        @staticmethod
        def export_to_markdown(*, doc):
            assert doc is document
            return (
                "| Interview | Research | Investigation |\n"
                "|---|---|---|"
            )

    table = Table()
    document = SimpleNamespace(
        texts=[], pictures=[], tables=[table],
        key_value_items=[], form_items=[])
    raw = SimpleNamespace(
        text="flattened table serialization",
        meta=SimpleNamespace(headings=["Civil Process"], doc_items=[table]))

    prepared = rag._prepare_source_preserving_chunks(
        [raw], document, lambda text: len(text.split()), 100)

    assert [part[0] for part in prepared] == [
        "|  |  |  |\n"
        "|---|---|---|\n"
        "| Interview | Research | Investigation |"
    ]
    assert rag._table_retrieval_core.markdown_table_dimensions(
        prepared[0][0]) == (1, 3)


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


def test_ordinary_footnote_is_separated_from_mixed_body_chunk():
    body = SimpleNamespace(
        self_ref="#/texts/0", label="text",
        text="The controller records calibration events.")
    footnote = SimpleNamespace(
        self_ref="#/texts/1", label="footnote",
        text="12. Sample manual section 4.")
    document = SimpleNamespace(
        texts=[body, footnote], pictures=[], tables=[],
        key_value_items=[], form_items=[])
    raw = SimpleNamespace(
        text=f"{body.text}\n{footnote.text}",
        meta=SimpleNamespace(
            headings=["Calibration"], doc_items=[body, footnote]))

    prepared = rag._prepare_source_preserving_chunks(
        [raw], document, lambda text: len(text.split()), 100)

    assert [entry[0] for entry in prepared] == [body.text, footnote.text]
    assert prepared[0][2] == [body]
    assert prepared[1][2] == [footnote]
    assert prepared[1][1] == ["Calibration"]
    assert all(entry[3] is True for entry in prepared)


def test_direct_footnote_uses_its_unique_source_heading_scope():
    def source_item(index, label, text, top):
        return SimpleNamespace(
            self_ref=f"#/texts/{index}",
            label=label,
            text=text,
            content_layer="body",
            prov=_layout_provenance(
                10, left=90, top=top, right=500, bottom=top - 12),
        )

    chapter = source_item(0, "section_header", "Chapter 3 System Setup", 720)
    section = source_item(
        1, "section_header", "§3.02 Limited Device Modes", 690)
    parent = source_item(2, "section_header", "A. Primary Mode", 660)
    leaf = source_item(3, "section_header", "1. Calibration Detail", 630)
    footnote = source_item(
        4, "footnote", "3. Source note for calibration detail.", 590)
    later_heading = source_item(
        5, "section_header", "B. Backup Mode", 550)
    later_body = source_item(
        6, "text", "Discussion under the backup mode.", 510)
    document = SimpleNamespace(
        texts=[
            chapter, section, parent, leaf, footnote, later_heading,
            later_body,
        ],
        pictures=[],
        tables=[],
        key_value_items=[],
        form_items=[],
    )
    canonical_chapter = "Chapter 3 · System Setup"
    raw = SimpleNamespace(
        text=f"{footnote.text}\n{later_body.text}",
        meta=SimpleNamespace(
            # HybridChunker's chunk-level fallback describes the later body,
            # not the earlier direct footnote carried in the same chunk.
            headings=[canonical_chapter, later_heading.text],
            doc_items=[footnote, later_body],
        ),
    )
    scaffold = [
        {"title": chapter.text, "level": 1},
        {"title": section.text, "level": 2},
        {"title": parent.text, "level": 3},
        {"title": leaf.text, "level": 4},
        {"title": later_heading.text, "level": 3},
    ]

    prepared = rag._prepare_source_preserving_chunks(
        [raw], document, lambda text: len(text.split()), 100,
        structural_ranges=set(), scaffold=scaffold)
    footnote_entry = next(
        entry for entry in prepared if entry[2] == [footnote])
    later_entry = next(
        entry for entry in prepared if entry[2] == [later_body])

    assert footnote_entry[1] == [
        canonical_chapter, section.text, parent.text, leaf.text]
    assert later_entry[1] == [
        canonical_chapter, section.text, later_heading.text]


def test_leading_page_footnote_precedes_next_page_body_from_same_raw_chunk():
    prior_body = SimpleNamespace(
        self_ref="#/texts/0", label="text", content_layer="body",
        text="The discussion begins on the first page.",
        prov=_layout_provenance(10, left=80, top=500))
    footnote = SimpleNamespace(
        self_ref="#/texts/1", label="footnote", content_layer="body",
        text="1. First-page source note.",
        prov=_layout_provenance(10, left=80, top=100))
    later_body = SimpleNamespace(
        self_ref="#/texts/2", label="text", content_layer="body",
        text="The discussion continues on the next page.",
        prov=_layout_provenance(11, left=80, top=680))
    document = SimpleNamespace(
        texts=[prior_body, footnote, later_body], pictures=[], tables=[],
        key_value_items=[], form_items=[])
    raw_chunks = [
        SimpleNamespace(
            text=prior_body.text,
            meta=SimpleNamespace(
                headings=["Discussion"], doc_items=[prior_body])),
        SimpleNamespace(
            text=f"{footnote.text}\n{later_body.text}",
            meta=SimpleNamespace(
                headings=["Discussion"],
                doc_items=[footnote, later_body])),
    ]

    prepared = rag._prepare_source_preserving_chunks(
        raw_chunks, document, lambda text: len(text.split()), 100,
        structural_ranges=set())

    assert [entry[0] for entry in prepared] == [
        prior_body.text, footnote.text, later_body.text]
    assert [entry[2] for entry in prepared] == [
        [prior_body], [footnote], [later_body]]


def test_same_page_body_still_precedes_its_direct_footnote():
    body = SimpleNamespace(
        self_ref="#/texts/0", label="text", content_layer="body",
        text="The page discussion.",
        prov=_layout_provenance(10, left=80, top=500))
    footnote = SimpleNamespace(
        self_ref="#/texts/1", label="footnote", content_layer="body",
        text="1. Same-page source note.",
        prov=_layout_provenance(10, left=80, top=100))
    document = SimpleNamespace(
        texts=[body, footnote], pictures=[], tables=[],
        key_value_items=[], form_items=[])
    raw = SimpleNamespace(
        text=f"{body.text}\n{footnote.text}",
        meta=SimpleNamespace(
            headings=["Discussion"], doc_items=[body, footnote]))

    prepared = rag._prepare_source_preserving_chunks(
        [raw], document, lambda text: len(text.split()), 100,
        structural_ranges=set())

    assert [entry[0] for entry in prepared] == [body.text, footnote.text]
    assert [entry[2] for entry in prepared] == [[body], [footnote]]


def test_source_footnote_lane_repairs_promote_lane_and_demote_false_runs():
    opener = SimpleNamespace(
        self_ref="#/texts/1", label="footnote", content_layer="body",
        text="5 Source note:",
        prov=_layout_provenance(7, left=90, top=180))
    clause = SimpleNamespace(
        self_ref="#/texts/2", label="list_item", content_layer="body",
        text="(A) First clause.",
        prov=_layout_provenance(7, left=106, top=160))
    short_note = SimpleNamespace(
        self_ref="#/texts/3", label="page_footer",
        content_layer="furniture", text="b Cross-reference.",
        prov=_layout_provenance(7, left=106, top=90))
    page_number = SimpleNamespace(
        self_ref="#/texts/4", label="page_footer",
        content_layer="furniture", text="907",
        prov=_layout_provenance(7, left=260, top=45))
    dialogue = [
        SimpleNamespace(
            self_ref=f"#/texts/{index}", label="footnote",
            content_layer="body", text=text,
            prov=_layout_provenance(8, left=112, top=220 - index * 15))
        for index, text in enumerate(
            ("'Do you monitor this signal?'", "'Confirmed.'"), start=5)
    ]
    omission = SimpleNamespace(
        self_ref="#/texts/7", label="footnote", content_layer="body",
        text="* * * Opinion text continues.",
        prov=_layout_provenance(9, left=90, top=100))
    document = SimpleNamespace(
        texts=[opener, clause, short_note, page_number, *dialogue, omission],
        pictures=[], tables=[], key_value_items=[], form_items=[])

    promote, demote = rag._source_footnote_lane_repairs(document)

    assert promote == {"#/texts/2", "#/texts/3"}
    assert demote == {"#/texts/5", "#/texts/6", "#/texts/7"}


def test_source_footnote_lane_stops_at_intervening_section_header():
    opener = SimpleNamespace(
        self_ref="#/texts/1", label="footnote", content_layer="body",
        text="13. Calibration source note.",
        prov=_layout_provenance(7, left=90, top=360))
    continuation = SimpleNamespace(
        self_ref="#/texts/2", label="text", content_layer="body",
        text="The source note continues.",
        prov=_layout_provenance(7, left=90, top=340))
    heading = SimpleNamespace(
        self_ref="#/texts/3", label="section_header", content_layer="body",
        text="Verification Note",
        prov=_layout_provenance(7, left=180, top=250))
    body = SimpleNamespace(
        self_ref="#/texts/4", label="text", content_layer="body",
        text="Ordinary body text starts beneath the new section.",
        prov=_layout_provenance(7, left=70, top=225))
    document = SimpleNamespace(
        texts=[opener, continuation, heading, body], pictures=[], tables=[],
        key_value_items=[], form_items=[])

    promote, demote = rag._source_footnote_lane_repairs(document)

    assert promote == {"#/texts/2"}
    assert demote == set()


def test_editorial_boilerplate_footnote_is_filtered_before_enrichment():
    body = SimpleNamespace(
        self_ref="#/texts/0", label="text", text="Main discussion.")
    footnote = SimpleNamespace(
        self_ref="#/texts/1", label="footnote",
        text=(
            "**This and other authors' explanations draw from the comments "
            "to the Model Rules and from other sources. They are not "
            "comprehensive but highlight some important interpretive points."))
    document = SimpleNamespace(
        texts=[body, footnote], pictures=[], tables=[],
        key_value_items=[], form_items=[])
    raw = SimpleNamespace(
        text=f"{body.text}\n{footnote.text}",
        meta=SimpleNamespace(
            headings=["Rule explanation"], doc_items=[body, footnote]))

    prepared = rag._prepare_source_preserving_chunks(
        [raw], document, lambda text: len(text.split()), 100)

    assert [entry[0] for entry in prepared] == [body.text]


def test_omitted_footnote_identity_is_detached_from_flattened_body_text():
    body = SimpleNamespace(
        self_ref="#/texts/0", label="text",
        text="The controller stores confidential calibration records.",
        prov=_layout_provenance(5, left=80, top=500))
    footnote = SimpleNamespace(
        self_ref="#/texts/1", label="footnote",
        text="12. Sample manual section 4.",
        prov=_layout_provenance(5, left=80, top=100))
    document = SimpleNamespace(
        texts=[body, footnote], pictures=[], tables=[],
        key_value_items=[], form_items=[])
    raw = SimpleNamespace(
        text=f"{body.text}\n{footnote.text}",
        meta=SimpleNamespace(headings=["Calibration"], doc_items=[body]))

    prepared = rag._prepare_source_preserving_chunks(
        [raw], document, lambda text: len(text.split()), 100,
        structural_ranges=set())

    assert [entry[0] for entry in prepared] == [body.text, footnote.text]
    assert prepared[0][2] == [body]
    assert prepared[1][2] == [footnote]


@pytest.mark.parametrize(
    ("body_text", "source_note", "flattened_note"),
    [
        (
            "The text quotes 12. See Sample. before continuing.",
            "12. See Sample.",
            "12. See Sample.",
        ),
        ("Body discussion.", "12. Alpha—Beta.", "12. Alpha - Beta."),
        ("Body discussion.", "1. Note.", "1. Note."),
    ],
)
def test_orphan_footnote_detachment_handles_repetition_punctuation_and_short_text(
        body_text, source_note, flattened_note):
    body = SimpleNamespace(
        self_ref="#/texts/0", label="text", text=body_text,
        prov=_layout_provenance(5, left=80, top=500))
    footnote = SimpleNamespace(
        self_ref="#/texts/1", label="footnote", text=source_note,
        prov=_layout_provenance(5, left=80, top=100))
    document = SimpleNamespace(
        texts=[body, footnote], pictures=[], tables=[],
        key_value_items=[], form_items=[])
    raw = SimpleNamespace(
        text=f"{body_text}\n{flattened_note}",
        meta=SimpleNamespace(headings=["Calibration"], doc_items=[body]))

    prepared = rag._prepare_source_preserving_chunks(
        [raw], document, lambda text: len(text.split()), 100,
        structural_ranges=set())

    assert [entry[0] for entry in prepared] == [body_text, source_note]
    assert prepared[1][2] == [footnote]


def test_split_source_item_is_not_rebuilt_twice_when_detaching_footnotes():
    body = SimpleNamespace(
        self_ref="#/texts/0", label="text",
        text="Alpha Beta Gamma Delta.",
        prov=_layout_provenance(5, left=80, top=500))
    tail = SimpleNamespace(
        self_ref="#/texts/1", label="text", text="The body continues.",
        prov=_layout_provenance(5, left=80, top=300))
    footnote = SimpleNamespace(
        self_ref="#/texts/2", label="footnote", text="4. Source note.",
        prov=_layout_provenance(5, left=80, top=100))
    document = SimpleNamespace(
        texts=[body, tail, footnote], pictures=[], tables=[],
        key_value_items=[], form_items=[])
    first = SimpleNamespace(
        text="Alpha Beta",
        meta=SimpleNamespace(headings=["Background"], doc_items=[body]))
    second = SimpleNamespace(
        text="Gamma Delta.\nThe body continues.\n4. Source note.",
        meta=SimpleNamespace(
            headings=["Background"],
            doc_items=[body, tail, footnote]))

    prepared = rag._prepare_source_preserving_chunks(
        [first, second], document, lambda text: len(text.split()), 100,
        structural_ranges=set())

    assert [entry[0] for entry in prepared] == [
        "Alpha Beta", "Gamma Delta.\nThe body continues.",
        "4. Source note."]
    assert all(
        "Alpha Beta Gamma Delta" not in entry[0]
        for entry in prepared[1:])


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
        text="Is the status stable?", prov=provenance(500))
    second_heading = SimpleNamespace(
        self_ref="#/texts/1", label="section_header",
        text="Did the monitor record a voltage change?", prov=provenance(400))
    first_answer = SimpleNamespace(
        self_ref="#/texts/2", label="text", text="Confirmed.",
        prov=provenance(480))
    second_answer = SimpleNamespace(
        self_ref="#/texts/3", label="text", text="No.",
        prov=provenance(380))
    document = SimpleNamespace(
        texts=[first_heading, second_heading, first_answer, second_answer],
        pictures=[], tables=[], key_value_items=[], form_items=[])
    raw = SimpleNamespace(
        text="Confirmed.\nNo.",
        meta=SimpleNamespace(
            headings=["Did the monitor record a voltage change?"],
            doc_items=[first_answer, second_answer]))

    prepared = rag._prepare_source_preserving_chunks(
        [raw], document, lambda text: len(text.split()), 100)

    assert [part[0] for part in prepared] == ["Confirmed.", "No."]
    assert [part[1] for part in prepared] == [
        ["Is the status stable?"],
        ["Did the monitor record a voltage change?"],
    ]


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
        text="Preface to the Synthetic Edition", prov=provenance(19, 500))
    structural_item = SimpleNamespace(
        self_ref="#/texts/1", label="text",
        text="Final table-of-problems entry.", prov=provenance(17, 100))
    substantive_item = SimpleNamespace(
        self_ref="#/texts/2", label="text",
        text="This guide introduces the controls used by sample operators.",
        prov=provenance(19, 480))
    document = SimpleNamespace(
        texts=[preface_heading, structural_item, substantive_item],
        pictures=[], tables=[], key_value_items=[], form_items=[])
    raw = SimpleNamespace(
        text=("Final table-of-problems entry.\n"
              "This guide introduces the controls used by sample operators."),
        meta=SimpleNamespace(
            headings=["Table of Problems"],
            doc_items=[structural_item, substantive_item]))

    prepared = rag._prepare_source_preserving_chunks(
        [raw], document, lambda text: len(text.split()), 100,
        structural_ranges={(15, 17)})

    assert [part[0] for part in prepared] == [
        "Final table-of-problems entry.",
        "This guide introduces the controls used by sample operators.",
    ]
    assert prepared[0][1] == ["Table of Problems"]
    assert prepared[1][1] == ["Preface to the Synthetic Edition"]
    assert all(part[3] is True for part in prepared)


def _layout_provenance(page, *, left, top, right=None, bottom=None):
    return [SimpleNamespace(
        page_no=page,
        bbox=SimpleNamespace(
            l=left,
            r=right if right is not None else left + 80,
            t=top,
            b=bottom if bottom is not None else top - 10,
            coord_origin=SimpleNamespace(value="BOTTOMLEFT"),
        ),
    )]


def test_single_column_body_wrap_repair_pins_header_and_is_idempotent():
    def item(index, label, text, top):
        return SimpleNamespace(
            self_ref=f"#/texts/{index}",
            label=label,
            text=text,
            prov=_layout_provenance(
                64, left=90, top=top, right=500, bottom=top - 20),
        )

    header = item(0, "page_header", "64 SAMPLE MANUAL", 725)
    lower_first = item(1, "text", "Lower-page first paragraph.", 300)
    lower_second = item(2, "text", "Lower-page second paragraph.", 100)
    upper_first = item(3, "text", "Upper-page first paragraph.", 680)
    upper_second = item(4, "text", "Upper-page second paragraph.", 500)
    children = [
        SimpleNamespace(cref=value.self_ref)
        for value in (
            header, lower_first, lower_second, upper_first, upper_second)
    ]
    document = SimpleNamespace(
        texts=[
            header, lower_first, lower_second, upper_first, upper_second,
        ],
        pictures=[],
        tables=[],
        key_value_items=[],
        form_items=[],
        groups=[],
        body=SimpleNamespace(children=children),
        pages={
            64: SimpleNamespace(
                size=SimpleNamespace(width=549, height=738)),
        },
    )

    assert rag._repair_single_column_body_child_order(document) == (1, 4)
    assert [child.cref for child in document.body.children] == [
        header.self_ref,
        upper_first.self_ref,
        upper_second.self_ref,
        lower_first.self_ref,
        lower_second.self_ref,
    ]
    repaired_children = list(document.body.children)

    assert rag._repair_single_column_body_child_order(document) == (0, 0)
    assert document.body.children == repaired_children
    assert document.body.children[0] is children[0]


def test_single_column_body_wrap_repair_moves_multispan_reporter_atomically():
    def item(index, label, text, top, bottom):
        return SimpleNamespace(
            self_ref=f"#/texts/{index}",
            label=label,
            text=text,
            prov=_layout_provenance(
                73, left=115, top=top, right=479, bottom=bottom),
        )

    header = item(0, "page_header", "73 SAMPLE MANUAL", 725, 713)
    editor = item(1, "section_header", "EDITOR'S NOTE", 597, 585)
    lower_body = item(2, "text", "Lower-page calibration text.", 581, 210)
    next_section = item(
        3, "section_header", "b. Secondary Sensor Response", 196, 166)
    final_body = item(4, "text", "Final lower-page paragraph.", 160, 67)
    upper_section = item(
        5, "section_header", "a. Primary Sensor Response", 671, 658)
    caption = item(
        6, "section_header", "North Array Calibration Log", 649, 635)
    reporter = item(
        7, "text", "Archive 12-B (revision 2031)", 630, 619)
    reporter.prov.extend(_layout_provenance(
        73, left=180, top=616, right=414, bottom=605))
    source_items = [
        header, editor, lower_body, next_section, final_body,
        upper_section, caption, reporter,
    ]
    document = SimpleNamespace(
        texts=source_items,
        pictures=[],
        tables=[],
        key_value_items=[],
        form_items=[],
        groups=[],
        body=SimpleNamespace(children=[
            SimpleNamespace(cref=value.self_ref) for value in source_items
        ]),
        pages={
            73: SimpleNamespace(
                size=SimpleNamespace(width=549, height=738)),
        },
    )

    assert rag._repair_single_column_body_child_order(document) == (1, 7)
    assert [child.cref for child in document.body.children] == [
        header.self_ref,
        upper_section.self_ref,
        caption.self_ref,
        reporter.self_ref,
        editor.self_ref,
        lower_body.self_ref,
        next_section.self_ref,
        final_body.self_ref,
    ]


def test_single_column_body_wrap_repair_refuses_structural_and_ambiguous_pages():
    def document_for(tops):
        items = [
            SimpleNamespace(
                self_ref=f"#/texts/{index}",
                label="text",
                text=f"Block {index}",
                prov=_layout_provenance(
                    64, left=90, top=top, right=500, bottom=top - 20),
            )
            for index, top in enumerate(tops)
        ]
        return SimpleNamespace(
            texts=items,
            pictures=[],
            tables=[],
            key_value_items=[],
            form_items=[],
            groups=[],
            body=SimpleNamespace(children=[
                SimpleNamespace(cref=item.self_ref) for item in items
            ]),
            pages={
                64: SimpleNamespace(
                    size=SimpleNamespace(width=549, height=738)),
            },
        )

    structural = document_for([300, 100, 680, 500])
    structural_order = list(structural.body.children)
    assert rag._repair_single_column_body_child_order(
        structural, structural_ranges={(64, 64)}) == (0, 0)
    assert structural.body.children == structural_order

    ambiguous = document_for([300, 100, 680, 500, 50, 650])
    ambiguous_order = list(ambiguous.body.children)
    assert rag._repair_single_column_body_child_order(ambiguous) == (0, 0)
    assert ambiguous.body.children == ambiguous_order


def test_prepared_entry_order_repairs_one_late_single_column_group():
    def item(index, text, top):
        return SimpleNamespace(
            self_ref=f"#/texts/{index}", label="text", text=text,
            prov=_layout_provenance(
                241, left=72, top=top, right=450, bottom=top - 70),
        )

    early = item(0, "4. Input Channels.", 670)
    middle = item(1, "The quoted setting follows.", 402)
    late = item(2, "7. Quick Checks.", 291)
    entries = [
        (middle.text, ["Notes"], [middle], True),
        (late.text, ["Notes"], [late], True),
        (early.text, ["Notes"], [early], True),
    ]

    assert rag._repair_single_column_prepared_entry_order(entries) == (1, 3)
    assert [entry[0] for entry in entries] == [
        early.text, middle.text, late.text]
    assert rag._repair_single_column_prepared_entry_order(entries) == (0, 0)


def test_prepared_entry_order_refuses_parallel_lanes_and_repeated_slices():
    def item(index, text, top, left, right):
        return SimpleNamespace(
            self_ref=f"#/texts/{index}", label="text", text=text,
            prov=_layout_provenance(
                9, left=left, top=top, right=right, bottom=top - 40),
        )

    left = item(0, "Left lane.", 650, 72, 250)
    right = item(1, "Right lane.", 650, 300, 480)
    lower = item(2, "Lower left lane.", 250, 72, 250)
    parallel = [
        (lower.text, None, [lower], True),
        (right.text, None, [right], True),
        (left.text, None, [left], True),
    ]
    original_parallel = list(parallel)
    assert rag._repair_single_column_prepared_entry_order(parallel) == (0, 0)
    assert parallel == original_parallel

    repeated = [
        ("late slice", None, [lower], True),
        ("other", None, [right], True),
        ("early slice", None, [lower], True),
    ]
    original_repeated = list(repeated)
    assert rag._repair_single_column_prepared_entry_order(repeated) == (0, 0)
    assert repeated == original_repeated


def test_running_heading_refs_detect_mislabeled_list_item_division_banner():
    page_header = SimpleNamespace(
        self_ref="#/texts/0",
        label="page_header",
        text="64",
        prov=_layout_provenance(64, left=500, top=720, right=530),
    )
    running_banner = SimpleNamespace(
        self_ref="#/texts/1",
        label="list_item",
        text="6 · OUTPUTS",
        prov=_layout_provenance(64, left=180, top=719, right=380),
    )
    body_item = SimpleNamespace(
        self_ref="#/texts/2",
        label="list_item",
        text="6 · OUTPUTS",
        prov=_layout_provenance(64, left=180, top=500, right=380),
    )

    refs = rag._running_section_heading_refs(SimpleNamespace(
        texts=[page_header, running_banner, body_item]))

    assert refs == {running_banner.self_ref}


def test_source_preparation_uses_shared_numeric_page_furniture_evidence():
    def serialized_item(
            index: int, page: int, text: str, *, label: str = "text",
            top: float = 36, bottom: float = 48,
    ) -> dict:
        return {
            "self_ref": f"#/texts/{index}",
            "label": label,
            "content_layer": (
                "furniture"
                if label in {"page_header", "page_footer"} else "body"),
            "text": text,
            "prov": [{
                "page_no": page,
                "charspan": [0, len(text)],
                "bbox": {
                    "l": 40, "t": top, "r": 70, "b": bottom,
                    "coord_origin": "TOPLEFT",
                },
            }],
        }

    serialized_texts = [
        serialized_item(0, 210, "40", label="page_header"),
        serialized_item(1, 211, "41", label="page_header"),
        serialized_item(2, 212, "42", label="page_header"),
        serialized_item(3, 213, "31", label="page_header"),
        serialized_item(4, 214, "44"),
        serialized_item(5, 215, "45", top=350, bottom=365),
        serialized_item(6, 216, "36"),
        serialized_item(7, 217, "Page 47"),
    ]
    serialized_document = {
        "texts": serialized_texts,
        "pages": {
            str(page): {"size": {"width": 600, "height": 800}}
            for page in range(210, 218)
        },
    }
    inferred_refs = quality_core.running_page_number_furniture_refs(
        serialized_document)

    def source_item(value: dict) -> SimpleNamespace:
        bbox = value["prov"][0]["bbox"]
        # Convert the TOPLEFT fixture to equivalent BOTTOMLEFT coordinates.
        top = 800 - min(bbox["t"], bbox["b"])
        bottom = 800 - max(bbox["t"], bbox["b"])
        return SimpleNamespace(
            self_ref=value["self_ref"],
            label=value["label"],
            content_layer=value["content_layer"],
            text=value["text"],
            prov=_layout_provenance(
                value["prov"][0]["page_no"], left=40, top=top,
                right=70, bottom=bottom),
        )

    source_items = [source_item(value) for value in serialized_texts]
    candidates = source_items[4:]
    document = SimpleNamespace(
        texts=source_items, pictures=[], tables=[], key_value_items=[],
        form_items=[], pages={
            page: SimpleNamespace(
                size=SimpleNamespace(width=600, height=800))
            for page in range(210, 218)
        })
    raw = SimpleNamespace(
        text="\n".join(item.text for item in candidates),
        meta=SimpleNamespace(headings=[], doc_items=candidates))

    prepared = rag._prepare_source_preserving_chunks(
        [raw], document, lambda text: len(text.split()), 100,
        structural_ranges=set(),
        running_page_furniture_refs=inferred_refs)
    prepared_text = "\n".join(entry[0] for entry in prepared)
    represented_refs = {
        item.self_ref
        for entry in prepared
        for item in (entry[2] or [])
    }

    assert inferred_refs == {"#/texts/4"}
    assert "44" not in prepared_text
    assert {"45", "36", "Page 47"} <= set(prepared_text.splitlines())
    assert "#/texts/4" not in represented_refs
    assert {"#/texts/5", "#/texts/6", "#/texts/7"} <= represented_refs


def test_enrich_chunk_exposes_physical_and_printed_pages_and_table_shape():
    table = SimpleNamespace(
        self_ref="#/tables/0", label="table",
        prov=_layout_provenance(
            63, left=80, top=500, right=460, bottom=300))
    record = rag.enrich_chunk(
        "| Probe | Result |\n|---|---|\n| North | Calibrated |",
        ["Sensor Review"], 0, 1, 418,
        doc_items=[table], source_file="synthetic_operations_manual",
        page_labels={63: "27"})

    metadata = record["metadata"]
    assert metadata["page_start"] == metadata["pdf_page_start"] == 63
    assert metadata["printed_page_start"] == "27"
    assert metadata["page_range"] == "p.27"
    assert metadata["pdf_page_range"] == "PDF pp.63-63"
    assert metadata["table_rows"] == 1
    assert metadata["table_cols"] == 2


def test_native_pdf_repair_requires_local_oracle_attestation():
    source = (
        "North R idge uses Spec 4 .2 and cites Ref .A 17, "
        "Q .C. section 3, X . Ray guidance, and Lab N .O.T.E. 2031.")
    native = (
        "North Ridge uses Spec 4.2 and cites Ref .A 17, "
        "Q.C. section 3, X . Ray guidance, and Lab N.O.T.E. 2031.")

    assert rag._repair_text_from_native_pdf(source, native) == (
        "North Ridge uses Spec 4.2 and cites Ref .A 17, "
        "Q.C. section 3, X . Ray guidance, and Lab N.O.T.E. 2031.")
    assert rag._repair_text_from_native_pdf(source, "unrelated text") == source


@pytest.mark.parametrize(
    ("extracted", "source", "expected"),
    [
        (
            "The operator asked whether youready for the calibration run "
            "tomorrow morning.",
            "The operator asked whether you ready for the calibration run "
            "tomorrow morning.",
            "The operator asked whether you ready for the calibration run "
            "tomorrow morning.",
        ),
        (
            "The monitor sent a calibrationrequest before sunrise.",
            "The monitor sent a calibration request before sunrise.",
            "The monitor sent a calibration request before sunrise.",
        ),
    ],
)
def test_source_oracle_restores_fused_word_boundaries(
        extracted, source, expected):
    assert rag._repair_text_from_source_oracle(extracted, source) == expected


def test_source_oracle_joins_spurious_split_word():
    extracted = (
        "The dashboard compares isolated wave forms across the display.")
    source = (
        "The dashboard compares isolated waveforms across the display.")

    assert rag._repair_text_from_source_oracle(extracted, source) == source


def test_source_oracle_restores_cross_line_hyphen_compounds():
    extracted = (
        "The technician checked a 4stagesensor beside an overtheair module "
        "yesterday afternoon.")
    source = (
        "The technician checked a 4-\n"
        "stage-sensor beside an over-\n"
        "the-air module yesterday afternoon.")

    assert rag._repair_text_from_source_oracle(extracted, source) == (
        "The technician checked a 4-stage-sensor beside an over-the-air "
        "module yesterday afternoon.")


def test_source_oracle_drops_discretionary_line_break_hyphens():
    extracted = "The controller recorded its calibration yesterday."
    source = "The control-\nler recorded its calibra-\ntion yesterday."

    assert rag._repair_text_from_source_oracle(extracted, source) == extracted


@pytest.mark.parametrize(
    ("extracted", "source"),
    [
        (
            "alpha beta gamma y",
            "alpha beta-gamma x alpha beta gamma",
        ),
        (
            "prefix alpha beta gamma y",
            "alpha beta-gamma x prefix alpha beta gamma",
        ),
    ],
)
def test_source_oracle_does_not_guess_across_repeated_passages(
        extracted, source):
    assert rag._repair_text_from_source_oracle(extracted, source) == extracted


def test_native_pdf_word_alignment_preserves_superscript_note_boundary():
    source = "The configuration of the controller. b Backup channels."
    words = [
        (0.0, 0.0, 20.0, 12.0, "The"),
        (23.0, 0.0, 90.0, 12.0, "configuration"),
        (93.0, 0.0, 103.0, 12.0, "of"),
        (106.0, 0.0, 120.0, 12.0, "the"),
        (123.0, 0.0, 180.0, 12.0, "controller.b"),
        (183.0, 0.0, 230.0, 12.0, "Backup"),
        (233.0, 0.0, 275.0, 12.0, "channels."),
    ]
    spans = [
        {"bbox": (123.0, 0.0, 174.0, 12.0), "size": 10.5,
         "flags": 4, "origin": (123.0, 10.0), "text": "controller."},
        {"bbox": (174.0, 0.0, 180.0, 8.0), "size": 7.0,
         "flags": 5, "origin": (174.0, 7.0), "text": "b"},
    ]

    assert rag._repair_split_words_from_native_pdf(
        source, words, spans) == source


def test_native_pdf_alignment_repairs_dash_before_superscript_citation():
    source = "Logged under R -4 c"
    words = [
        (0.0, 0.0, 40.0, 12.0, "Logged"),
        (43.0, 0.0, 52.0, 12.0, "under"),
        (55.0, 0.0, 80.0, 12.0, "R–4c"),
    ]
    spans = [
        {"bbox": (55.0, 0.0, 75.0, 12.0), "size": 10.5,
         "flags": 4, "origin": (55.0, 10.0), "text": "R–4"},
        {"bbox": (75.0, 1.0, 80.0, 9.0), "size": 7.0,
         "flags": 5, "origin": (75.0, 7.0), "text": "c"},
    ]

    assert rag._repair_split_words_to_fixpoint(
        source, words, spans) == "Logged under R–4 c"


def test_native_pdf_word_alignment_uses_whole_compound_attestation():
    source = "The FailSafe mode differs from a fallback in service."
    words = [
        (0.0, 0.0, 20.0, 10.0, "The", 0, 0, 0),
        (23.0, 0.0, 55.0, 10.0, "Fail-", 0, 0, 1),
        (0.0, 12.0, 45.0, 22.0, "Safe", 0, 1, 0),
        (48.0, 12.0, 75.0, 22.0, "mode", 0, 1, 1),
        (78.0, 12.0, 110.0, 22.0, "differs", 0, 1, 2),
        (113.0, 12.0, 135.0, 22.0, "from", 0, 1, 3),
        (138.0, 12.0, 145.0, 22.0, "a", 0, 1, 4),
        (148.0, 12.0, 182.0, 22.0, "fallback", 0, 1, 5),
        (185.0, 12.0, 196.0, 22.0, "in-", 0, 1, 6),
        (0.0, 24.0, 20.0, 34.0, "service.", 0, 2, 0),
    ]

    assert rag._repair_split_words_from_native_pdf(
        source, words, [],
        hard_hyphen_attestations={"fail-safe", "maintenance-window"},
    ) == "The Fail-Safe mode differs from a fallback in service."


def test_native_pdf_cross_line_ascii_dash_needs_exact_compound_attestation():
    source = "The reading stabilizes."
    words = [
        (0.0, 0.0, 20.0, 10.0, "The", 0, 0, 0),
        # The em dash is unrelated punctuation in the same PyMuPDF word. It
        # must not turn the later ASCII line-end dash into compound evidence.
        (23.0, 0.0, 55.0, 10.0, "—read-", 0, 0, 1),
        (0.0, 12.0, 38.0, 22.0, "ing", 0, 1, 0),
        (41.0, 12.0, 78.0, 22.0, "stabilizes.", 0, 1, 1),
    ]

    assert rag._repair_split_words_from_native_pdf(
        source, words, [], hard_hyphen_attestations=set()) == source


def test_native_flattening_ignores_unrelated_dash_in_following_pdf_word():
    words = [
        (0.0, 0.0, 24.0, 10.0, "read-", 0, 0, 0),
        (0.0, 12.0, 70.0, 22.0, "ing—asides", 0, 1, 0),
    ]

    assert rag._native_text_from_words(
        words, hard_hyphen_attestations=set()) == "reading—asides"
    assert rag._native_text_from_words(
        words, hard_hyphen_attestations={"read-ing-asides"},
    ) == "read-ing—asides"


def test_native_pdf_word_alignment_preserves_suspended_hyphen_spacing():
    source = "The blue- or amber-coded indicator flashes."
    words = [
        (0.0, 0.0, 20.0, 10.0, "The", 0, 0, 0),
        (23.0, 0.0, 48.0, 10.0, "blue-", 0, 0, 1),
        (51.0, 0.0, 60.0, 10.0, "or", 0, 0, 2),
        (63.0, 0.0, 108.0, 10.0, "amber-coded", 0, 0, 3),
        (111.0, 0.0, 170.0, 10.0, "indicator", 0, 0, 4),
        (173.0, 0.0, 205.0, 10.0, "flashes.", 0, 0, 5),
    ]

    assert rag._repair_split_words_from_native_pdf(
        source, words, []) == source


def test_native_pdf_word_alignment_moves_space_before_wrapped_hyphen():
    source = "A low -power sensor uses a blue- or amber-coded indicator."
    words = [
        (0.0, 0.0, 8.0, 10.0, "A", 0, 0, 0),
        (11.0, 0.0, 30.0, 10.0, "low-", 0, 0, 1),
        (0.0, 12.0, 25.0, 22.0, "power", 0, 1, 0),
        (28.0, 12.0, 60.0, 22.0, "sensor", 0, 1, 1),
        (63.0, 12.0, 78.0, 22.0, "uses", 0, 1, 2),
        (81.0, 12.0, 88.0, 22.0, "a", 0, 1, 3),
        (91.0, 12.0, 118.0, 22.0, "blue-", 0, 1, 4),
        (121.0, 12.0, 130.0, 22.0, "or", 0, 1, 5),
        (133.0, 12.0, 180.0, 22.0, "amber-coded", 0, 1, 6),
        (183.0, 12.0, 230.0, 22.0, "indicator.", 0, 1, 7),
    ]

    assert rag._repair_split_words_from_native_pdf(
        source, words, [], hard_hyphen_attestations={"low-power"}) == (
            "A low-power sensor uses a blue- or amber-coded indicator.")


def test_native_pdf_word_alignment_removes_soft_hyphen():
    source = "The controller started."
    words = [
        (0.0, 0.0, 20.0, 10.0, "The", 0, 0, 0),
        (23.0, 0.0, 70.0, 10.0, "control\N{SOFT HYPHEN}", 0, 0, 1),
        (0.0, 12.0, 28.0, 22.0, "ler", 0, 1, 0),
        (31.0, 12.0, 55.0, 22.0, "started.", 0, 1, 1),
    ]

    assert rag._repair_split_words_from_native_pdf(
        source, words, []) == source


def test_native_hyphen_attestation_rejects_plain_and_dash_type_conflicts():
    words = [
        (0.0, 0.0, 40.0, 10.0, "Fail-Safe"),
        (45.0, 0.0, 85.0, 10.0, "sub-groups"),
        (90.0, 0.0, 130.0, 10.0, "subgroups"),
        (135.0, 0.0, 175.0, 10.0, "alpha—beta"),
    ]
    page = SimpleNamespace(
        get_text=lambda kind, sort=True: words if kind == "words" else [])

    assert rag._native_hard_hyphen_attestations([page]) == {
        "fail-safe"}


def test_split_hyphen_repair_requires_plain_source_attestation():
    value = (
        "The con -figuration and cal -ibra tion differ from fail -safe "
        "and logs -the values.")
    edits = []

    repaired = rag._repair_source_attested_split_hyphen_words(
        value,
        plain_word_keys={"configuration", "calibration", "failsafe"},
        hard_hyphen_keys={"fail-safe"},
        _edits=edits,
    )

    assert repaired == (
        "The configuration and calibration differ from fail -safe "
        "and logs -the values.")
    assert edits == [
        ("con -figuration", "configuration"),
        ("cal -ibra tion", "calibration"),
    ]


def _native_wrap_words(left, right):
    return [
        (10.0, 0.0, 30.0, 10.0, left, 0, 0, 0),
        (31.0, 0.0, 34.0, 10.0, "-", 0, 0, 1),
        (10.0, 12.0, 30.0, 22.0, right, 0, 1, 0),
    ]


def test_native_segment_split_hyphen_repair_uses_later_provenance():
    edits = []
    repaired = rag._repair_source_split_hyphens_from_native_segments(
        "Earlier notes continue with similar calibra -tions.",
        native_segments=[
            ("Earlier notes continue with similar", []),
            ("calibrations.", _native_wrap_words("calibra", "tions")),
        ],
        pdf_plain_word_keys=set(),
        hard_hyphen_keys=set(),
        _edits=edits,
    )

    assert repaired == "Earlier notes continue with similar calibrations."
    assert edits == [("calibra -tions", "calibrations")]


def test_native_segment_split_hyphen_repair_fuses_preceding_letter_fragment():
    edits = []
    repaired = rag._repair_source_split_hyphens_from_native_segments(
        "the device records c onfi -gured settings",
        native_segments=[(
            "the device records configured settings",
            _native_wrap_words("confi", "gured"),
        )],
        pdf_plain_word_keys={"configured"},
        hard_hyphen_keys=set(),
        _edits=edits,
    )

    assert repaired == "the device records configured settings"
    assert edits == [("c onfi -gured", "configured")]


def test_native_flattening_joins_cross_line_terminal_soft_hyphen():
    words = [
        (10.0, 0.0, 40.0, 10.0, "micro\N{SOFT HYPHEN}", 0, 0, 0),
        (10.0, 12.0, 50.0, 22.0, "controllers", 0, 1, 0),
    ]

    assert rag._native_text_from_words(words) == "microcontrollers"


def test_native_soft_hyphen_fragments_need_source_lexicon_and_geometry_proof():
    source = (
        "Do a monitor and an observer report diff  er  ent sample readings?")
    words = [
        (117.0, 0.0, 131.588, 10.0, "dif\N{SOFT HYPHEN}ff", 2, 2, 0),
        (131.491, 0.0, 153.432, 10.0, "er\N{SOFT HYPHEN}ent", 2, 2, 1),
    ]
    edits = []

    repaired = rag._repair_source_attested_soft_hyphen_fragment_word(
        source,
        native_segments=[("difff erent", words)],
        pdf_plain_word_keys={"different"},
        _edits=edits,
    )

    assert repaired == (
        "Do a monitor and an observer report different sample readings?")
    assert edits == [("diff  er  ent", "different")]
    assert rag._native_text_from_words(
        words, plain_word_attestations={"different"}) == "different"
    assert rag._native_text_from_words(words) == "difff erent"


def test_native_soft_hyphen_fragments_preserve_unproved_spacing():
    source = "The diff  er  ent setting concerns Sample output."
    touching_words = [
        (10.0, 0.0, 30.0, 10.0, "dif\N{SOFT HYPHEN}ff", 0, 0, 0),
        # A normal inter-word gap rejects the otherwise similar hidden text.
        (33.0, 0.0, 55.0, 10.0, "er\N{SOFT HYPHEN}ent", 0, 0, 1),
        (58.0, 0.0, 90.0, 10.0, "\N{SOFT HYPHEN}Sample", 0, 0, 2),
    ]

    assert rag._repair_source_attested_soft_hyphen_fragment_word(
        source,
        native_segments=[("difff erent Sample", touching_words)],
        pdf_plain_word_keys={"different"},
    ) == source


def test_native_segment_split_hyphen_repair_preserves_authored_separators():
    interruptions = (
        ("in -Delta's report", "in\N{EM DASH}Delta's report",
         "in \N{EM DASH} Delta's report"),
        ("a delayed signal -the fallback",
         "a delayed signal\N{EM DASH}the fallback",
         "a delayed signal \N{EM DASH} the fallback"),
        ("quiet mornings -or active mornings",
         "quiet mornings\N{EM DASH}or active mornings",
         "quiet mornings \N{EM DASH} or active mornings"),
    )
    for source, native, expected in interruptions:
        assert rag._repair_source_split_hyphens_from_native_segments(
            source,
            native_segments=[(native, [])],
            pdf_plain_word_keys=set(),
            hard_hyphen_keys=set(),
        ) == expected
    assert rag._repair_source_split_hyphens_from_native_segments(
        "sensor -   controller pairs",
        native_segments=[("sensor-controller pairs", [])],
        pdf_plain_word_keys=set(),
        hard_hyphen_keys=set(),
    ) == "sensor-controller pairs"


def test_native_segment_split_hyphen_repair_requires_unique_pdf_completion():
    words = _native_wrap_words("fi", "ure")
    assert rag._repair_source_split_hyphens_from_native_segments(
        "a schematic fi -ure appeared",
        native_segments=[("a schematic fi ure appeared", words)],
        pdf_plain_word_keys={"figures"},
        hard_hyphen_keys=set(),
    ) == "a schematic figure appeared"

    ambiguous_words = _native_wrap_words("mo", "es")
    assert rag._repair_source_split_hyphens_from_native_segments(
        "several mo -es appeared",
        native_segments=[("several mo es appeared", ambiguous_words)],
        pdf_plain_word_keys={"modes", "moves"},
        hard_hyphen_keys=set(),
    ) == "several mo -es appeared"


def _missing_url_glyph_trace(
        *, gap=5.5, sample_font="Source-Regular", sample_size=10.75):
    target = {
        "dir": (1.0, 0.0), "font": "Source-Regular", "wmode": 0,
        "type": 0, "size": 10.75, "spacewidth": 3.4,
        "chars": [
            (ord("f"), 1, (10.0, 10.0), (10.0, 0.0, 13.0, 12.0)),
            (ord("i"), 2, (13.0, 10.0), (13.0, 0.0, 16.0, 12.0)),
            (ord("d"), 3, (16.0 + gap, 10.0),
             (16.0 + gap, 0.0, 22.0 + gap, 12.0)),
            (ord("s"), 4, (22.0 + gap, 10.0),
             (22.0 + gap, 0.0, 26.0 + gap, 12.0)),
        ],
    }
    width_samples = {
        "dir": (1.0, 0.0), "font": sample_font, "wmode": 0,
        "type": 0, "size": sample_size, "spacewidth": 3.4,
        "chars": [
            (ord("n"), 5, (40.0, 10.0), (40.0, 0.0, 45.8, 12.0)),
            (ord("n"), 5, (46.0, 10.0), (46.0, 0.0, 51.8, 12.0)),
        ],
    }
    return [target, width_samples]


def test_source_attested_missing_glyph_closes_one_exact_url():
    source = (
        "Archive https:// docs . example . net / bulletins / 2031 / 07 / 04 / "
        "sensor - scan - fi ds .html; mirror "
        "https://mirror.example.org/catalog/field-note."
    )
    edits = []

    repaired = rag._repair_source_attested_url_missing_glyph(
        source,
        plain_word_keys={"finds", "archive", "sensor"},
        native_trace_spans=_missing_url_glyph_trace(),
        clip=SimpleNamespace(x0=0.0, y0=-1.0, x1=100.0, y1=20.0),
        provenance_count=1,
        _edits=edits,
    )

    assert "scan - finds .html" in repaired
    assert edits == [("fi ds", "finds")]
    normalized = rag._normalize_text(repaired)
    assert normalized == (
        "Archive https://docs.example.net/bulletins/2031/07/04/"
        "sensor-scan-finds.html; mirror "
        "https://mirror.example.org/catalog/field-note."
    )
    assert not rag._chunking_core.has_malformed_url_spacing(normalized)


@pytest.mark.parametrize(
    ("plain_word_keys", "gap"),
    [
        ({"finds", "firds"}, 5.5),
        ({"finds"}, 3.0),
    ],
)
def test_source_attested_missing_url_glyph_fails_closed(
        plain_word_keys, gap):
    source = "See https:// docs . example . net / scan - fi ds .html."

    assert rag._repair_source_attested_url_missing_glyph(
        source,
        plain_word_keys=plain_word_keys,
        native_trace_spans=_missing_url_glyph_trace(gap=gap),
        clip=SimpleNamespace(x0=0.0, y0=-1.0, x1=100.0, y1=20.0),
        provenance_count=1,
    ) == source


@pytest.mark.parametrize(
    ("source", "trace_spans", "provenance_count"),
    [
        (
            "See https:// docs . example . net / scan - fi ds.",
            _missing_url_glyph_trace(),
            1,
        ),
        (
            "See https:// docs . example . net / scan - fi ds .html.",
            _missing_url_glyph_trace(sample_font="Other-Regular"),
            1,
        ),
        (
            "See https:// docs . example . net / scan - fi ds .html.",
            _missing_url_glyph_trace(sample_size=9.0),
            1,
        ),
        (
            "See https:// docs . example . net / scan - fi ds .html.",
            _missing_url_glyph_trace()[:1],
            1,
        ),
        (
            "See https:// docs . example . net / scan - fi ds .html.",
            _missing_url_glyph_trace(),
            2,
        ),
    ],
)
def test_source_attested_missing_url_glyph_requires_closed_source_proof(
        source, trace_spans, provenance_count):
    assert rag._repair_source_attested_url_missing_glyph(
        source,
        plain_word_keys={"finds"},
        native_trace_spans=trace_spans,
        clip=SimpleNamespace(x0=0.0, y0=-1.0, x1=100.0, y1=20.0),
        provenance_count=provenance_count,
    ) == source


def test_native_override_repairs_spaced_authority_before_url_parser():
    source = (
        "https:// docs . e xample . net / archive / "
        "blue - ledger . html"
    )
    words = [
        (0.0, 0.0, 40.0, 12.0, "https://", 0, 0, 0),
        (43.0, 0.0, 310.0, 12.0,
         "docs.example.net/archive/blue-ledger.html", 0, 0, 1),
    ]
    spans = [
        {"bbox": word[:4], "size": 10.5, "flags": 4,
         "origin": (word[0], 10.0), "text": word[4]}
        for word in words
    ]

    repaired = rag._repair_split_words_to_fixpoint(source, words, spans)
    normalized = rag._normalize_text(repaired)

    assert repaired == (
        "https:// docs.example.net/archive/blue-ledger.html")
    assert normalized == (
        "https://docs.example.net/archive/blue-ledger.html")
    assert not rag._chunking_core.has_malformed_url_spacing(normalized)


def test_native_repair_oracle_uses_final_transactional_url_spelling():
    source = (
        "Field Survey Group, ht tps://archive.example . net / "
        "sensor -maps (accessed Feb. 8, 2031).")

    repaired = rag._canonicalize_native_repair_urls(source)

    assert "https://archive.example.net/sensor-maps" in repaired
    assert not rag._chunking_core.has_malformed_url_spacing(repaired)


_SYNTHETIC_SOURCE_URL = (
    "https:// docs . example . net / archive / field - sensor - "
    "calibration / 2031 / 07 / 04 / report - blue - 42 .html"
)
_SYNTHETIC_MIXED_URL = (
    "https:// docs . example . net / archive / field-sensor-calibration / "
    "2031 / 07 / 04 / report - blue - 42 .html"
)
_SYNTHETIC_CANONICAL_URL = (
    "https://docs.example.net/archive/field-sensor-calibration/"
    "2031/07/04/report-blue-42.html"
)


def test_native_url_oracle_repairs_unique_source_attested_mixed_spacing():
    source = f"Field log, Feb. 8, 2031: {_SYNTHETIC_SOURCE_URL}. End of entry."
    mixed = f"Field log, Feb. 8, 2031: {_SYNTHETIC_MIXED_URL}. End of entry."

    transactions = (
        rag._chunking_core._accepted_spaced_url_transactions(source))
    repaired = rag._canonicalize_native_repair_urls(
        mixed, source_text=source)

    assert len(transactions) == 1
    assert transactions[0].canonical == _SYNTHETIC_CANONICAL_URL
    assert repaired == (
        f"Field log, Feb. 8, 2031: {_SYNTHETIC_CANONICAL_URL}. End of entry.")
    assert not rag._chunking_core.has_malformed_url_spacing(repaired)


def test_native_url_oracle_rejects_non_whitespace_character_mutation():
    source = f"See {_SYNTHETIC_SOURCE_URL}."
    mutated = f"See {_SYNTHETIC_MIXED_URL.replace('blue', 'green')}."

    repaired = rag._canonicalize_native_repair_urls(
        mutated, source_text=source)

    assert repaired == mutated
    assert rag._chunking_core.has_malformed_url_spacing(repaired)


def test_native_url_oracle_rejects_ambiguous_duplicate_candidates():
    source = f"({_SYNTHETIC_SOURCE_URL})"
    ambiguous = (
        f"({_SYNTHETIC_MIXED_URL}) and ({_SYNTHETIC_MIXED_URL})")

    repaired = rag._canonicalize_native_repair_urls(
        ambiguous, source_text=source)

    assert repaired == ambiguous
    assert repaired.count(_SYNTHETIC_MIXED_URL) == 2


def test_native_url_oracle_requires_source_compatible_endpoint():
    source = f"({_SYNTHETIC_SOURCE_URL})"
    mismatched = f"({_SYNTHETIC_MIXED_URL}]"

    assert rag._canonicalize_native_repair_urls(
        mismatched, source_text=source) == mismatched


def test_native_url_oracle_does_not_reinterpret_prose_dash_as_url():
    source = "See https://example.com/path - commentary.html."

    assert not rag._chunking_core._accepted_spaced_url_transactions(source)
    assert rag._canonicalize_native_repair_urls(
        source, source_text=source) == source


def test_native_url_oracle_removes_only_attested_pdf_format_controls():
    source = f"See {_SYNTHETIC_SOURCE_URL}. End."
    controlled_url = (
        _SYNTHETIC_CANONICAL_URL
        .replace("https://", "https://\N{SOFT HYPHEN}")
        .replace("docs.", "docs\u200b.\N{SOFT HYPHEN}")
        .replace("/2031", "\u200b/\N{SOFT HYPHEN}2031")
        .replace("report-", "report\u200b-\N{SOFT HYPHEN}")
    )
    controlled = f"See {controlled_url}. End."

    repaired = rag._canonicalize_native_repair_urls(
        controlled, source_text=source)

    assert repaired == f"See {_SYNTHETIC_CANONICAL_URL}. End."
    assert "\N{SOFT HYPHEN}" not in repaired
    assert "\u200b" not in repaired


def test_source_group_recovery_is_one_atom_with_all_lineage_items():
    first = SimpleNamespace(
        self_ref="#/texts/1", label="text", content_layer="body",
        text="The sentence ends out of", prov=[])
    second = SimpleNamespace(
        self_ref="#/texts/2", label="text", content_layer="body",
        text="order here.", prov=[])
    document = SimpleNamespace(
        texts=[first, second], pictures=[], tables=[], key_value_items=[],
        form_items=[])
    raw = SimpleNamespace(
        text="The sentence ends out of order here.",
        meta=SimpleNamespace(headings=["Problem"], doc_items=[first, second]))
    recovery = rag.SourceTextGroupRecovery(
        text="The sentence ends here in the correct order.",
        refs=("#/texts/1", "#/texts/2"), page=7)

    prepared = rag._prepare_source_preserving_chunks(
        [raw], document, lambda text: len(text.split()), 100,
        structural_ranges=set(), text_group_recoveries=(recovery,))

    assert len(prepared) == 1
    assert prepared[0][0] == recovery.text
    assert prepared[0][2] == [first, second]
    assert prepared[0][3] is True


def test_source_group_recovery_overrides_reversed_single_member_chunks():
    first = SimpleNamespace(
        self_ref="#/texts/1", label="text", content_layer="body",
        text="nothing'", prov=_layout_provenance(7, left=420, top=520))
    second = SimpleNamespace(
        self_ref="#/texts/2", label="text", content_layer="body",
        text="The all-or proposition continues.",
        prov=_layout_provenance(7, left=80, top=520))
    document = SimpleNamespace(
        texts=[first, second], pictures=[], tables=[], key_value_items=[],
        form_items=[])
    raw_chunks = [
        SimpleNamespace(
            text=second.text,
            meta=SimpleNamespace(headings=["Defenses"], doc_items=[second])),
        SimpleNamespace(
            text=first.text,
            meta=SimpleNamespace(headings=["Defenses"], doc_items=[first])),
    ]
    recovery = rag.SourceTextGroupRecovery(
        text="The all-or-nothing proposition continues.",
        refs=(first.self_ref, second.self_ref), page=7)

    prepared = rag._prepare_source_preserving_chunks(
        raw_chunks, document, lambda text: len(text.split()), 100,
        structural_ranges=set(), text_group_recoveries=(recovery,))

    oracles = rag._source_fidelity_oracles(
        document,
        rag.BoundSourceEnrichments(
            text_group_recoveries=(recovery,)),
        prepared_chunks=prepared,
    )

    assert [(entry[0], [item.self_ref for item in entry[2]])
            for entry in prepared] == [
        (recovery.text, [second.self_ref, first.self_ref])]
    assert oracles[first.self_ref]["oracle_group_members"] == [
        second.self_ref, first.self_ref]
    assert oracles[second.self_ref]["oracle_group_members"] == [
        second.self_ref, first.self_ref]


def test_source_group_recovery_preempts_solo_plus_mixed_native_replay():
    first = SimpleNamespace(
        self_ref="#/texts/1", label="text", content_layer="body",
        text="First corrupt fragment.",
        prov=_layout_provenance(7, left=80, top=520))
    second = SimpleNamespace(
        self_ref="#/texts/2", label="text", content_layer="body",
        text="Second corrupt fragment.",
        prov=_layout_provenance(7, left=250, top=520))
    document = SimpleNamespace(
        texts=[first, second], pictures=[], tables=[], key_value_items=[],
        form_items=[])
    raw_chunks = [
        SimpleNamespace(
            text=first.text,
            meta=SimpleNamespace(headings=["Rule"], doc_items=[first])),
        SimpleNamespace(
            text=f"{first.text} {second.text}",
            meta=SimpleNamespace(
                headings=["Rule"], doc_items=[first, second])),
        SimpleNamespace(
            text=second.text,
            meta=SimpleNamespace(headings=["Rule"], doc_items=[second])),
    ]
    recovery = rag.SourceTextGroupRecovery(
        text="The two native fragments form one corrected sentence.",
        refs=(first.self_ref, second.self_ref), page=7)

    prepared = rag._prepare_source_preserving_chunks(
        raw_chunks, document, lambda text: len(text.split()), 100,
        structural_ranges=set(),
        text_overrides={
            first.self_ref: "First restored fragment.",
            second.self_ref: "Second restored fragment.",
        },
        text_group_recoveries=(recovery,))

    assert [(entry[0], [item.self_ref for item in entry[2]])
            for entry in prepared] == [
        (recovery.text, [first.self_ref, second.self_ref])]


def test_source_group_recovery_preempts_member_wholesale_rebuilds():
    first = SimpleNamespace(
        self_ref="#/texts/1", label="text", content_layer="body",
        text="First corrupt fragment.",
        prov=_layout_provenance(7, left=80, top=520))
    second = SimpleNamespace(
        self_ref="#/texts/2", label="text", content_layer="body",
        text="Second corrupt fragment.",
        prov=_layout_provenance(7, left=250, top=520))
    document = SimpleNamespace(
        texts=[first, second], pictures=[], tables=[], key_value_items=[],
        form_items=[])
    raw_chunks = [
        SimpleNamespace(
            text=first.text,
            meta=SimpleNamespace(headings=["Rule"], doc_items=[first])),
        SimpleNamespace(
            text=f"{first.text} {second.text}",
            meta=SimpleNamespace(
                headings=["Rule"], doc_items=[first, second])),
        SimpleNamespace(
            text=second.text,
            meta=SimpleNamespace(headings=["Rule"], doc_items=[second])),
    ]
    recovery = rag.SourceTextGroupRecovery(
        text="The two native fragments form one corrected sentence.",
        refs=(first.self_ref, second.self_ref), page=7)

    prepared = rag._prepare_source_preserving_chunks(
        raw_chunks, document, lambda text: len(text.split()), 100,
        structural_ranges=set(),
        text_overrides={
            first.self_ref: "First restored fragment.",
            second.self_ref: "Second restored fragment.",
        },
        text_rebuild_refs={first.self_ref, second.self_ref},
        text_group_recoveries=(recovery,))

    assert [(entry[0], [item.self_ref for item in entry[2]])
            for entry in prepared] == [
        (recovery.text, [first.self_ref, second.self_ref])]


def test_source_group_recovery_preempts_verified_neighbor_replay():
    neighbor = SimpleNamespace(
        self_ref="#/texts/0", label="text", content_layer="body",
        text="A corrupt neighboring paragraph.",
        prov=_layout_provenance(7, left=80, top=560))
    host = SimpleNamespace(
        self_ref="#/texts/1", label="text", content_layer="body",
        text="The all-or proposition continues.",
        prov=_layout_provenance(7, left=80, top=520))
    detached = SimpleNamespace(
        self_ref="#/texts/2", label="text", content_layer="body",
        text="nothing'", prov=_layout_provenance(
            7, left=420, top=520))
    document = SimpleNamespace(
        texts=[neighbor, host, detached], pictures=[], tables=[],
        key_value_items=[], form_items=[])
    raw_chunks = [
        SimpleNamespace(
            text=neighbor.text,
            meta=SimpleNamespace(
                headings=["Defenses"], doc_items=[neighbor])),
        SimpleNamespace(
            text=f"{neighbor.text} {host.text}",
            meta=SimpleNamespace(
                headings=["Defenses"], doc_items=[neighbor, host])),
        SimpleNamespace(
            text=detached.text,
            meta=SimpleNamespace(
                headings=["Defenses"], doc_items=[detached])),
    ]
    recovery = rag.SourceTextGroupRecovery(
        text="The all-or-nothing proposition continues.",
        refs=(detached.self_ref, host.self_ref), page=7)

    prepared = rag._prepare_source_preserving_chunks(
        raw_chunks, document, lambda text: len(text.split()), 100,
        structural_ranges=set(),
        text_overrides={
            neighbor.self_ref: "A restored neighboring paragraph.",
            host.self_ref: host.text,
            detached.self_ref: detached.text,
        },
        text_rebuild_refs={
            neighbor.self_ref, host.self_ref, detached.self_ref},
        text_group_recoveries=(recovery,))

    assert [(entry[0], [item.self_ref for item in entry[2]])
            for entry in prepared] == [
        ("A restored neighboring paragraph.", [neighbor.self_ref]),
        (recovery.text, [host.self_ref, detached.self_ref]),
    ]


def test_source_group_recovery_restores_first_declared_list_marker_once():
    first = SimpleNamespace(
        self_ref="#/texts/1", label="list_item", marker="(1)",
        content_layer="body", text="immedi-",
        prov=_layout_provenance(7, left=80, top=500))
    second = SimpleNamespace(
        self_ref="#/texts/2", label="list_item", marker="",
        content_layer="body", text="ate action; (2) next item",
        prov=_layout_provenance(7, left=80, top=480))
    document = SimpleNamespace(
        texts=[first, second], pictures=[], tables=[], key_value_items=[],
        form_items=[])
    raw = SimpleNamespace(
        text="immediate action; (2) next item",
        meta=SimpleNamespace(headings=["Problem"], doc_items=[first, second]))
    recovery = rag.SourceTextGroupRecovery(
        text="immediate action; (2) next item",
        refs=(first.self_ref, second.self_ref), page=7)

    prepared = rag._prepare_source_preserving_chunks(
        [raw], document, lambda text: len(text.split()), 100,
        structural_ranges=set(), text_group_recoveries=(recovery,))

    assert [entry[0] for entry in prepared] == [
        "(1) immediate action; (2) next item"]
    assert prepared[0][2] == [first, second]


def test_source_group_recovery_does_not_reemit_member_footnotes():
    first = SimpleNamespace(
        self_ref="#/texts/1", label="footnote", content_layer="body",
        text="a displaced ending", prov=[])
    second = SimpleNamespace(
        self_ref="#/texts/2", label="footnote", content_layer="body",
        text="a beginning", prov=[])
    document = SimpleNamespace(
        texts=[first, second], pictures=[], tables=[], key_value_items=[],
        form_items=[])
    raw = SimpleNamespace(
        text="a displaced ending a beginning",
        meta=SimpleNamespace(headings=["Problem"], doc_items=[first, second]))
    recovery = rag.SourceTextGroupRecovery(
        text="a beginning with the corrected ending",
        refs=("#/texts/1", "#/texts/2"), page=7)

    prepared = rag._prepare_source_preserving_chunks(
        [raw], document, lambda text: len(text.split()), 100,
        structural_ranges=set(), text_group_recoveries=(recovery,))

    assert [(entry[0], entry[2]) for entry in prepared] == [
        (recovery.text, [first, second])]


def test_source_group_recovery_owns_nonconsecutive_refs_once():
    def provenance(top, text):
        return [SimpleNamespace(
            page_no=7,
            charspan=(0, len(text)),
            bbox=SimpleNamespace(
                l=1.0, t=top, r=10.0, b=top - 1.0,
                coord_origin=SimpleNamespace(value="BOTTOMLEFT")))]

    first_text = "First displaced source fragment."
    intervening_text = "Independent intervening paragraph."
    last_text = "Last displaced source fragment."
    first = SimpleNamespace(
        self_ref="#/texts/1", label="text", content_layer="body",
        text=first_text, prov=provenance(30.0, first_text))
    intervening = SimpleNamespace(
        self_ref="#/texts/2", label="text", content_layer="body",
        text=intervening_text, prov=provenance(20.0, intervening_text))
    last = SimpleNamespace(
        self_ref="#/texts/3", label="text", content_layer="body",
        text=last_text, prov=provenance(10.0, last_text))
    document = SimpleNamespace(
        texts=[first, intervening, last], pictures=[], tables=[],
        key_value_items=[], form_items=[])
    raw_chunks = [
        SimpleNamespace(
            text=f"{first.text} {intervening.text}",
            meta=SimpleNamespace(
                headings=["Problem"], doc_items=[first, intervening])),
        SimpleNamespace(
            text=last.text,
            meta=SimpleNamespace(headings=["Problem"], doc_items=[last])),
    ]
    recovery = rag.SourceTextGroupRecovery(
        text="Recovered first and last fragments in native order.",
        refs=("#/texts/1", "#/texts/3"), page=7)

    prepared = rag._prepare_source_preserving_chunks(
        raw_chunks, document, lambda text: len(text.split()), 100,
        structural_ranges=set(), text_group_recoveries=(recovery,))
    item_by_ref, parents, captions = rag._docling_lineage_catalog(document)
    lineage_refs = [
        [item["ref"] for item in rag._source_lineage_for_items(
            entry[2], item_by_ref=item_by_ref,
            parent_refs_by_child=parents,
            caption_refs_by_parent=captions)]
        for entry in prepared
    ]

    assert [entry[0] for entry in prepared] == [
        recovery.text, intervening.text]
    assert lineage_refs == [
        ["#/texts/1", "#/texts/3"],
        ["#/texts/2"],
    ]
    published_refs = [ref for refs in lineage_refs for ref in refs]
    assert len(published_refs) == len(set(published_refs)) == 3


def test_shared_source_ref_triggers_all_recovered_atoms_in_order():
    before = SimpleNamespace(
        self_ref="#/texts/1", label="text", content_layer="body",
        text="Before fragment.", prov=[])
    shared = SimpleNamespace(
        self_ref="#/texts/2", label="text", content_layer="body",
        text="Misordered top and bottom fragments.", prov=[])
    after = SimpleNamespace(
        self_ref="#/texts/3", label="text", content_layer="body",
        text="After fragment.", prov=[])
    document = SimpleNamespace(
        texts=[before, shared, after], pictures=[], tables=[],
        key_value_items=[], form_items=[])
    raw = SimpleNamespace(
        text=shared.text,
        meta=SimpleNamespace(headings=["Problem"], doc_items=[shared]))
    recoveries = (
        rag.SourceTextGroupRecovery(
            text="Recovered physical top fragment.",
            refs=("#/texts/1", "#/texts/2"), page=7),
        rag.SourceTextGroupRecovery(
            text="Recovered physical bottom fragment.",
            refs=("#/texts/2", "#/texts/3"), page=7),
    )

    prepared = rag._prepare_source_preserving_chunks(
        [raw], document, lambda text: len(text.split()), 100,
        structural_ranges=set(), text_group_recoveries=recoveries)

    assert [entry[0] for entry in prepared] == [
        recovery.text for recovery in recoveries]
    assert [[item.self_ref for item in entry[2]] for entry in prepared] == [
        ["#/texts/1", "#/texts/2"],
        ["#/texts/2", "#/texts/3"],
    ]


def test_source_group_recovery_shared_ref_triggers_both_atoms_once():
    previous = SimpleNamespace(
        self_ref="#/texts/1", label="text", content_layer="body",
        text="Previous-page beginning.", prov=[])
    shared = SimpleNamespace(
        self_ref="#/texts/2", label="text", content_layer="body",
        text="Malformed item spanning both recovered atoms.", prov=[])
    displaced = SimpleNamespace(
        self_ref="#/texts/3", label="text", content_layer="body",
        text="Displaced paragraph.", prov=[])
    following = SimpleNamespace(
        self_ref="#/texts/4", label="text", content_layer="body",
        text="Following-page ending.", prov=[])
    document = SimpleNamespace(
        texts=[previous, shared, displaced, following], pictures=[],
        tables=[], key_value_items=[], form_items=[])
    raw = SimpleNamespace(
        text=shared.text,
        meta=SimpleNamespace(headings=["Problem"], doc_items=[shared]))
    recoveries = (
        rag.SourceTextGroupRecovery(
            text="Recovered pre-heading atom.",
            refs=("#/texts/1", "#/texts/2", "#/texts/3"), page=7),
        rag.SourceTextGroupRecovery(
            text="Recovered post-heading atom.",
            refs=("#/texts/2", "#/texts/4"), page=8),
    )

    prepared = rag._prepare_source_preserving_chunks(
        [raw], document, lambda text: len(text.split()), 100,
        structural_ranges=set(), text_group_recoveries=recoveries)

    assert [entry[0] for entry in prepared] == [
        recovery.text for recovery in recoveries]
    assert [
        [item.self_ref for item in entry[2]] for entry in prepared
    ] == [
        ["#/texts/1", "#/texts/2", "#/texts/3"],
        ["#/texts/2", "#/texts/4"],
    ]


def test_source_geometry_replaces_stale_midpage_chunk_heading():
    def provenance(page, top):
        return [SimpleNamespace(
            page_no=page,
            bbox=SimpleNamespace(
                l=90.0, t=top, r=450.0, b=top + 20.0,
                coord_origin="TOPLEFT"),
        )]

    heading_b = SimpleNamespace(
        self_ref="#/texts/1", label="section_header",
        text="B. CLASSIFYING SIGNALS", prov=provenance(1, 100))
    continuation = SimpleNamespace(
        self_ref="#/texts/2", label="text",
        text="The prior section continues here.", prov=provenance(2, 50))
    heading_c = SimpleNamespace(
        self_ref="#/texts/3", label="section_header",
        text="C. SEVERITY OF SIGNALS", prov=provenance(2, 300))
    later = SimpleNamespace(
        self_ref="#/texts/4", label="text",
        text="The new section begins here.", prov=provenance(2, 350))
    document = SimpleNamespace(
        texts=[heading_b, continuation, heading_c, later], pictures=[],
        tables=[], key_value_items=[], form_items=[])
    raw = [
        SimpleNamespace(
            text=continuation.text,
            meta=SimpleNamespace(
                headings=["Chapter 7. Monitoring Alerts", heading_c.text],
                doc_items=[continuation])),
        SimpleNamespace(
            text=later.text,
            meta=SimpleNamespace(
                headings=["Chapter 7. Monitoring Alerts", heading_c.text],
                doc_items=[later])),
    ]

    prepared = rag._prepare_source_preserving_chunks(
        raw, document, lambda text: len(text.split()), 100)

    assert prepared[0][1] == [
        "Chapter 7. Monitoring Alerts", heading_b.text]
    assert prepared[1][1] == [
        "Chapter 7. Monitoring Alerts", heading_c.text]


def test_scaffold_path_prefers_same_level_source_heading():
    source_path = (
        "Chapter 7. System Failures > "
        "C. Degrees of Failure (Primary vs. Secondary Failure)")

    reconciled = rag._reconcile_scaffold_path_with_source_headings(
        source_path, 2,
        ["Chapter 7. System Failures", "B. Categorizing Failures"],
        structure_profile="us-law-casebook-v1")

    assert reconciled == (
        "Chapter 7. System Failures > B. Categorizing Failures")


def test_source_heading_stack_uses_canonical_scaffold_chapter_title():
    reconciled = rag._reconcile_scaffold_path_with_source_headings(
        "Chapter 2 · Control Systems: Input Validation > §2.01 Startup",
        2,
        [
            "§1.08 Legacy Pipeline Workflow",
            "Accomplishment Note",
            "Chapter 2 Control Systems",
            "§2.01 Startup",
        ],
        structure_profile="us-law-casebook-v1")

    assert reconciled == (
        "Chapter 2 · Control Systems: Input Validation > §2.01 Startup")


def test_geometry_bound_stack_without_division_preserves_every_heading():
    reconciled = rag._reconcile_scaffold_path_with_source_headings(
        "Chapter 4· Signal Routing > C. Verifying Delivery > Accomplishment Note",
        3,
        [
            "§4.01 Foundations of Signal Routing",
            "Delivery Evidence Note",
            "Riley Chen & Morgan Patel, Bench Notes for Relay Networks",
        ],
        structure_profile="us-law-casebook-v1")

    assert reconciled == (
        "Chapter 4· Signal Routing > §4.01 Foundations of Signal Routing > "
        "Delivery Evidence Note > "
        "Riley Chen & Morgan Patel, Bench Notes for Relay Networks")


def test_same_page_heading_below_chunk_source_is_removed():
    metadata = {
        "page_start": 12,
        "page_end": 12,
        "source_items": [{
            "spans": [{
                "page": 12,
                "bbox": [70.0, 420.0, 470.0, 550.0],
                "origin": "BOTTOMLEFT",
            }],
        }],
    }
    spans = {
        (12, rag._source_heading_identity("Notes & Questions")): [
            (390.0, 405.0)],
        (12, rag._source_heading_identity("Existing Example")): [
            (120.0, 140.0)],
    }

    filtered = rag._filter_same_page_future_headings(
        ["§2.05 Existing Section", "Notes & Questions", "Existing Example"],
        metadata, spans, {12: 738.0})

    assert filtered == ["§2.05 Existing Section", "Existing Example"]


def test_same_page_future_heading_can_truncate_unpositioned_descendants():
    metadata = {
        "page_start": 12,
        "page_end": 12,
        "source_items": [{
            "spans": [{
                "page": 12,
                "bbox": [70.0, 420.0, 470.0, 550.0],
                "origin": "BOTTOMLEFT",
            }],
        }],
    }
    future_parent = "A. Direct Signal Verification"
    unpositioned_caption = "Orion Systems v. Delta Labs"
    spans = {
        (12, rag._source_heading_identity(future_parent)): [
            (390.0, 405.0)],
    }
    headings = [
        "Chapter 4 · Signal Routing", future_parent, unpositioned_caption]

    independently_filtered = rag._filter_same_page_future_headings(
        headings, metadata, spans, {12: 738.0})
    filtered = rag._filter_same_page_future_headings(
        headings, metadata, spans, {12: 738.0},
        truncate_at_first=True)

    assert independently_filtered == [
        "Chapter 4 · Signal Routing", unpositioned_caption]
    assert filtered == ["Chapter 4 · Signal Routing"]


def test_page_intro_does_not_inherit_future_scaffold_case_path():
    chapter = "Chapter 4· Signal Routing"
    subsection = "A. Direct Signal Verification"
    case = "Orion Systems v. Delta Labs"
    metadata = {
        "page_start": 42,
        "page_end": 42,
        "source_items": [{
            "ref": "#/texts/76",
            "spans": [{
                "page": 42,
                "bbox": [116.0, 484.0, 478.0, 673.0],
                "origin": "BOTTOMLEFT",
            }],
        }],
    }
    spans = {
        (42, rag._source_heading_identity(subsection)): [
            (555.0, 568.0)],
        (42, rag._source_heading_identity(case)): [
            (581.0, 594.0)],
    }
    scaffold_parts = rag._filter_same_page_future_headings(
        [chapter, subsection, case], metadata, spans, {42: 738.0},
        truncate_at_first=True)

    reconciled = rag._reconcile_scaffold_path_with_source_headings(
        " > ".join(scaffold_parts), 3, [],
        structure_profile="us-law-casebook-v1")

    assert scaffold_parts == [chapter]
    assert reconciled == chapter


def test_source_heading_timeline_carries_nested_problem_past_running_header():
    chapter = "Chapter 4: Signal Routing"
    ethics = SimpleNamespace(
        self_ref="#/texts/1", label="section_header",
        text="Ethics Note: Concealing Calibration Drift",
        prov=_layout_provenance(1, left=180, top=650))
    ethics_body = SimpleNamespace(
        self_ref="#/texts/2", label="text", text="Ethics discussion.",
        prov=_layout_provenance(1, left=90, top=620))
    section = SimpleNamespace(
        self_ref="#/texts/3", label="section_header",
        text="§ 4.04 Combining Signal Checks",
        prov=_layout_provenance(1, left=150, top=500))
    problem = SimpleNamespace(
        self_ref="#/texts/4", label="section_header",
        text="FAILED SENSOR ARRAY PROBLEM",
        prov=_layout_provenance(1, left=190, top=450))
    story = SimpleNamespace(
        self_ref="#/texts/5", label="text", text="The story begins.",
        prov=_layout_provenance(1, left=90, top=420))
    page_number = SimpleNamespace(
        self_ref="#/texts/6", label="page_header", text="451",
        prov=_layout_provenance(2, left=410, top=700))
    running = SimpleNamespace(
        self_ref="#/texts/7", label="section_header",
        text="§4.04 COMBINING SIGNAL CHECKS",
        prov=_layout_provenance(2, left=130, top=695))
    continuation = SimpleNamespace(
        self_ref="#/texts/8", label="text", text="The story continues.",
        prov=_layout_provenance(2, left=90, top=650))
    accomplishment = SimpleNamespace(
        self_ref="#/texts/9", label="section_header",
        text="Accomplishment Note",
        prov=_layout_provenance(2, left=180, top=300))
    later = SimpleNamespace(
        self_ref="#/texts/10", label="text", text="The chapter concludes.",
        prov=_layout_provenance(2, left=90, top=270))
    document = SimpleNamespace(
        texts=[ethics, ethics_body, section, problem, story, page_number,
               running, continuation, accomplishment, later],
        pictures=[], tables=[], key_value_items=[], form_items=[])
    raw = [
        SimpleNamespace(
            text=item.text,
            meta=SimpleNamespace(
                headings=[chapter, "Accomplishment Note"], doc_items=[item]))
        for item in (ethics_body, story, continuation, later)
    ]
    scaffold = [
        {"title": chapter, "level": 1},
        {"title": ethics.text, "level": 3},
        {"title": section.text, "level": 3},
        {"title": accomplishment.text, "level": 3},
    ]

    prepared = rag._prepare_source_preserving_chunks(
        raw, document, lambda text: len(text.split()), 100,
        scaffold=scaffold)

    assert [entry[1] for entry in prepared] == [
        [chapter, ethics.text],
        [chapter, section.text, problem.text],
        [chapter, section.text, problem.text],
        [chapter, section.text, accomplishment.text],
    ]


def test_source_heading_timeline_preserves_consecutive_same_level_titles():
    chapter = "Chapter 4: System Signals"
    panel = SimpleNamespace(
        self_ref="#/texts/1", label="section_header",
        text="Verifying Signals Note",
        prov=_layout_provenance(1, left=180, top=650))
    attribution = SimpleNamespace(
        self_ref="#/texts/2", label="section_header",
        text="Riley Chen & Morgan Patel, Guide to Sample Systems",
        prov=_layout_provenance(1, left=150, top=610))
    body = SimpleNamespace(
        self_ref="#/texts/3", label="text", text="12-16 (sample ed. 2026)",
        prov=_layout_provenance(1, left=120, top=570))
    document = SimpleNamespace(
        texts=[panel, attribution, body], pictures=[], tables=[],
        key_value_items=[], form_items=[])
    raw = SimpleNamespace(
        text=body.text,
        meta=SimpleNamespace(headings=[chapter, attribution.text],
                             doc_items=[body]))
    scaffold = [
        {"title": chapter, "level": 1},
        {"title": panel.text, "level": 3},
        {"title": attribution.text, "level": 3},
    ]

    prepared = rag._prepare_source_preserving_chunks(
        [raw], document, lambda text: len(text.split()), 100,
        scaffold=scaffold)

    assert prepared[0][1] == [chapter, panel.text, attribution.text]


def test_source_heading_timeline_preserves_roman_then_lettered_heading():
    section = SimpleNamespace(
        self_ref="#/texts/1", label="section_header",
        text="III. Reliability of Inputs as a Criterion",
        prov=_layout_provenance(1, left=150, top=650))
    subsection = SimpleNamespace(
        self_ref="#/texts/2", label="section_header",
        text="A. Sensor Integrity",
        prov=_layout_provenance(1, left=180, top=610))
    first_body = SimpleNamespace(
        self_ref="#/texts/3", label="text",
        text="The input analysis begins.",
        prov=_layout_provenance(1, left=100, top=570))
    sibling = SimpleNamespace(
        self_ref="#/texts/4", label="section_header",
        text="B. Operator Review",
        prov=_layout_provenance(1, left=180, top=520))
    second_body = SimpleNamespace(
        self_ref="#/texts/5", label="text",
        text="The review analysis follows.",
        prov=_layout_provenance(1, left=100, top=480))
    document = SimpleNamespace(
        texts=[section, subsection, first_body, sibling, second_body],
        pictures=[], tables=[],
        key_value_items=[], form_items=[])
    raw = [
        SimpleNamespace(
            text=item.text,
            meta=SimpleNamespace(headings=[heading], doc_items=[item]))
        for item, heading in (
            (first_body, subsection.text), (second_body, sibling.text))
    ]

    prepared = rag._prepare_source_preserving_chunks(
        raw, document, lambda text: len(text.split()), 100)

    assert [entry[1] for entry in prepared] == [
        [section.text, subsection.text],
        [section.text, sibling.text],
    ]


def test_source_heading_timeline_keeps_roman_parent_for_numeric_siblings():
    section = SimpleNamespace(
        self_ref="#/texts/1", label="section_header",
        text="II. Evolution of the Protocol",
        prov=_layout_provenance(1, left=150, top=650))
    first = SimpleNamespace(
        self_ref="#/texts/2", label="section_header",
        text="1. Laboratory Prototype",
        prov=_layout_provenance(1, left=180, top=610))
    first_body = SimpleNamespace(
        self_ref="#/texts/3", label="text", text="First discussion.",
        prov=_layout_provenance(1, left=100, top=570))
    second = SimpleNamespace(
        self_ref="#/texts/4", label="section_header",
        text="2. Field Revision",
        prov=_layout_provenance(1, left=180, top=520))
    second_body = SimpleNamespace(
        self_ref="#/texts/5", label="text", text="Second discussion.",
        prov=_layout_provenance(1, left=100, top=480))
    document = SimpleNamespace(
        texts=[section, first, first_body, second, second_body], pictures=[],
        tables=[], key_value_items=[], form_items=[])
    raw = [
        SimpleNamespace(
            text=item.text,
            meta=SimpleNamespace(headings=[heading], doc_items=[item]))
        for item, heading in (
            (first_body, first.text), (second_body, second.text))
    ]

    prepared = rag._prepare_source_preserving_chunks(
        raw, document, lambda text: len(text.split()), 100)

    assert [entry[1] for entry in prepared] == [
        [section.text, first.text], [section.text, second.text]]


def test_chapter_subject_outline_replaces_completed_panel():
    chapter = "Chapter 4· Signal Routing"
    section = SimpleNamespace(
        self_ref="#/texts/1", label="section_header",
        text="§4.04 Combining Signal Checks",
        prov=_layout_provenance(1, left=120, top=700))
    panel = SimpleNamespace(
        self_ref="#/texts/2", label="section_header",
        text="Accomplishment Note",
        prov=_layout_provenance(1, left=150, top=660))
    panel_body = SimpleNamespace(
        self_ref="#/texts/3", label="text", text="Panel conclusion.",
        prov=_layout_provenance(1, left=90, top=620))
    banner = SimpleNamespace(
        self_ref="#/texts/4", label="section_header", text="Signal Routing",
        prov=_layout_provenance(1, left=250, top=560))
    first = SimpleNamespace(
        self_ref="#/texts/5", label="section_header",
        text="I. Direct Delivery Checks",
        prov=_layout_provenance(1, left=120, top=520))
    first_body = SimpleNamespace(
        self_ref="#/texts/6", label="list_item", text="Loopback Probe",
        prov=_layout_provenance(1, left=140, top=480))
    second = SimpleNamespace(
        self_ref="#/texts/7", label="section_header",
        text="II. Recovery Paths",
        prov=_layout_provenance(1, left=120, top=430))
    second_body = SimpleNamespace(
        self_ref="#/texts/8", label="list_item", text="Fallback relay",
        prov=_layout_provenance(1, left=140, top=390))
    document = SimpleNamespace(
        texts=[section, panel, panel_body, banner, first, first_body, second,
               second_body], pictures=[], tables=[], key_value_items=[],
        form_items=[])
    raw = [
        SimpleNamespace(
            text=item.text,
            meta=SimpleNamespace(headings=[chapter, heading],
                                 doc_items=[item]))
        for item, heading in (
            (panel_body, panel.text),
            (first_body, first.text),
            (second_body, second.text),
        )
    ]
    scaffold = [
        {"title": chapter, "level": 1},
        {"title": section.text, "level": 2},
        {"title": panel.text, "level": 3},
    ]

    prepared = rag._prepare_source_preserving_chunks(
        raw, document, lambda text: len(text.split()), 100,
        scaffold=scaffold)

    assert [entry[1] for entry in prepared] == [
        [chapter, section.text, panel.text],
        [chapter, section.text, banner.text, first.text],
        [chapter, section.text, banner.text, second.text],
    ]


def test_source_heading_timeline_replaces_prior_problem_with_review_section():
    prior = SimpleNamespace(
        self_ref="#/texts/1", label="section_header", text="Problem 3",
        prov=_layout_provenance(1, left=150, top=700))
    prior_body = SimpleNamespace(
        self_ref="#/texts/2", label="text", text="Prior problem facts.",
        prov=_layout_provenance(1, left=100, top=660))
    review = SimpleNamespace(
        self_ref="#/texts/3", label="section_header",
        text="II. SAMPLE EXAM & REVIEW PROBLEM",
        prov=_layout_provenance(1, left=150, top=600))
    mascot = SimpleNamespace(
        self_ref="#/texts/4", label="section_header",
        text="University Mascot Problem",
        prov=_layout_provenance(1, left=180, top=560))
    review_body = SimpleNamespace(
        self_ref="#/texts/5", label="text", text="Review problem facts.",
        prov=_layout_provenance(1, left=100, top=520))
    document = SimpleNamespace(
        texts=[prior, prior_body, review, mascot, review_body], pictures=[],
        tables=[], key_value_items=[], form_items=[])
    raw = [
        SimpleNamespace(
            text=item.text,
            meta=SimpleNamespace(headings=[heading], doc_items=[item]))
        for item, heading in (
            (prior_body, prior.text), (review_body, mascot.text))
    ]

    prepared = rag._prepare_source_preserving_chunks(
        raw, document, lambda text: len(text.split()), 100)

    assert prepared[1][1] == [review.text, mascot.text]


def test_source_heading_timeline_ignores_fused_top_margin_running_header():
    chapter = "Chapter 10: Device Reliability"
    section = SimpleNamespace(
        self_ref="#/texts/1", label="section_header",
        text="§10.02 The Baseline Case for Strict Device Validation",
        prov=_layout_provenance(1, left=120, top=620))
    first = SimpleNamespace(
        self_ref="#/texts/2", label="text", text="The section begins.",
        prov=_layout_provenance(1, left=90, top=580))
    running = SimpleNamespace(
        self_ref="#/texts/3", label="section_header",
        text="§10.02 THE BASELINE CASE FOR STRICT DEVICE VALIDATION 901",
        prov=_layout_provenance(2, left=90, top=694))
    continuation = SimpleNamespace(
        self_ref="#/texts/4", label="text", text="The section continues.",
        prov=_layout_provenance(2, left=90, top=650))
    pages = {
        page: SimpleNamespace(size=SimpleNamespace(width=549, height=738))
        for page in (1, 2)
    }
    document = SimpleNamespace(
        texts=[section, first, running, continuation], pictures=[], tables=[],
        key_value_items=[], form_items=[], pages=pages)
    raw = [
        SimpleNamespace(
            text=item.text,
            meta=SimpleNamespace(headings=[chapter, section.text],
                                 doc_items=[item]))
        for item in (first, continuation)
    ]
    scaffold = [
        {"title": chapter, "level": 1},
        {"title": section.text, "level": 2},
    ]

    prepared = rag._prepare_source_preserving_chunks(
        raw, document, lambda text: len(text.split()), 100,
        scaffold=scaffold)

    assert [entry[1] for entry in prepared] == [
        [chapter, section.text], [chapter, section.text]]


def test_source_heading_timeline_keeps_heading_below_top_margin_band():
    chapter = "Chapter 4: Signal Routing"
    heading = SimpleNamespace(
        self_ref="#/texts/1", label="section_header",
        text="Delivery Evidence Note",
        prov=_layout_provenance(1, left=200, top=671))
    body = SimpleNamespace(
        self_ref="#/texts/2", label="text", text="The note begins.",
        prov=_layout_provenance(1, left=90, top=630))
    document = SimpleNamespace(
        texts=[heading, body], pictures=[], tables=[], key_value_items=[],
        form_items=[], pages={
            1: SimpleNamespace(size=SimpleNamespace(width=549, height=738)),
        })
    raw = SimpleNamespace(
        text=body.text,
        meta=SimpleNamespace(headings=[chapter, heading.text],
                             doc_items=[body]))

    prepared = rag._prepare_source_preserving_chunks(
        [raw], document, lambda text: len(text.split()), 100)

    assert prepared[0][1] == [chapter, heading.text]


def test_one_positioned_stack_overrides_unpositioned_fallback_item():
    section = SimpleNamespace(
        self_ref="#/texts/1", label="section_header",
        text="§2.05 Existing Section",
        prov=_layout_provenance(1, left=120, top=650))
    positioned = SimpleNamespace(
        self_ref="#/texts/2", label="text", text="Positioned conclusion.",
        prov=_layout_provenance(1, left=90, top=600))
    unpositioned = SimpleNamespace(
        self_ref="#/texts/3", label="text", text="Unpositioned continuation.",
        prov=[])
    future = SimpleNamespace(
        self_ref="#/texts/4", label="section_header",
        text="Notes & Questions",
        prov=_layout_provenance(1, left=150, top=450))
    document = SimpleNamespace(
        texts=[section, positioned, unpositioned, future], pictures=[],
        tables=[], key_value_items=[], form_items=[])
    raw = SimpleNamespace(
        text=f"{positioned.text} {unpositioned.text}",
        meta=SimpleNamespace(
            headings=[future.text], doc_items=[positioned, unpositioned]))

    prepared = rag._prepare_source_preserving_chunks(
        [raw], document, lambda text: len(text.split()), 100)

    assert prepared[0][1] == [section.text]


def test_identical_heading_occurrences_split_one_raw_chunk():
    section = SimpleNamespace(
        self_ref="#/texts/0", label="section_header",
        text="§8.03 Interpreting Operator Commands",
        prov=_layout_provenance(1, left=120, top=720))
    first_notes = SimpleNamespace(
        self_ref="#/texts/1", label="section_header",
        text="Notes & Questions",
        prov=_layout_provenance(1, left=150, top=680))
    conclusion = SimpleNamespace(
        self_ref="#/texts/2", label="footnote",
        text="* * * The first notes panel concludes here.",
        prov=_layout_provenance(1, left=90, top=640))
    second_notes = SimpleNamespace(
        self_ref="#/texts/3", label="section_header",
        text="Notes & Questions",
        prov=_layout_provenance(2, left=150, top=720))
    question = SimpleNamespace(
        self_ref="#/texts/4", label="list_item",
        text="1. The second notes panel begins here.",
        prov=_layout_provenance(2, left=90, top=680))
    document = SimpleNamespace(
        texts=[section, first_notes, conclusion, second_notes, question],
        pictures=[], tables=[], key_value_items=[], form_items=[])
    raw = SimpleNamespace(
        text=f"{conclusion.text}\n\n{question.text}",
        meta=SimpleNamespace(
            headings=[section.text, second_notes.text],
            doc_items=[conclusion, question]))

    prepared = rag._prepare_source_preserving_chunks(
        [raw], document, lambda text: len(text.split()), 100,
        footnote_demote_refs={conclusion.self_ref})

    assert [entry[0] for entry in prepared] == [
        conclusion.text, question.text]
    assert [[item.self_ref for item in (entry[2] or [])]
            for entry in prepared] == [
        [conclusion.self_ref], [question.self_ref]]
    assert [entry[1] for entry in prepared] == [
        [section.text, first_notes.text],
        [section.text, second_notes.text],
    ]


def test_one_heading_occurrence_keeps_compatible_body_items_grouped():
    notes = SimpleNamespace(
        self_ref="#/texts/0", label="section_header", content_layer="body",
        text="Notes & Questions",
        prov=_layout_provenance(1, left=150, top=720))
    first = SimpleNamespace(
        self_ref="#/texts/1", label="text", content_layer="body",
        text="The first paragraph remains in this notes panel.",
        prov=_layout_provenance(1, left=90, top=680))
    second = SimpleNamespace(
        self_ref="#/texts/2", label="text", content_layer="body",
        text="The second paragraph remains in this notes panel.",
        prov=_layout_provenance(1, left=90, top=640))
    document = SimpleNamespace(
        texts=[notes, first, second], pictures=[], tables=[],
        key_value_items=[], form_items=[])
    raw = SimpleNamespace(
        text=f"{first.text}\n\n{second.text}",
        meta=SimpleNamespace(
            headings=[notes.text], doc_items=[first, second]))

    prepared = rag._prepare_source_preserving_chunks(
        [raw], document, lambda text: len(text.split()), 100)

    assert len(prepared) == 1
    assert prepared[0][0] == raw.text
    assert prepared[0][1] == [notes.text]
    assert prepared[0][2] == [first, second]


def test_source_heading_timeline_repairs_stale_same_page_casebook_leaf():
    chapter = "Chapter 7: Recovery and Exceptions"
    stale_parent = SimpleNamespace(
        self_ref="#/texts/1", label="section_header",
        text="C. Shared Input Problems", prov=_layout_provenance(
            1, left=120, top=720))
    section = SimpleNamespace(
        self_ref="#/texts/2", label="section_header",
        text="§7.09 Controller Recovery", prov=_layout_provenance(
            1, left=120, top=680))
    notes = SimpleNamespace(
        self_ref="#/texts/3", label="section_header",
        text="Notes & Questions", prov=_layout_provenance(
            1, left=150, top=640))
    problems = SimpleNamespace(
        self_ref="#/texts/4", label="section_header",
        text="RECOVERY POLICY PROBLEMS", prov=_layout_provenance(
            1, left=170, top=580))
    exception = SimpleNamespace(
        self_ref="#/texts/5", label="section_header",
        text="Offline Controller Exception", prov=_layout_provenance(
            1, left=180, top=540))
    first_items = SimpleNamespace(
        self_ref="#/texts/6", label="list_item", text="Items (a)-(f).",
        prov=_layout_provenance(1, left=100, top=500))
    page_number = SimpleNamespace(
        self_ref="#/texts/7", label="page_header", text="656",
        prov=_layout_provenance(2, left=420, top=700))
    running = SimpleNamespace(
        self_ref="#/texts/8", label="section_header",
        text="7 · RECOVERY AND EXCEPTIONS", prov=_layout_provenance(
            2, left=210, top=695))
    continuation = SimpleNamespace(
        self_ref="#/texts/9", label="list_item", text="Items (g)-(m).",
        prov=_layout_provenance(2, left=100, top=650))
    ethics = SimpleNamespace(
        self_ref="#/texts/10", label="section_header",
        text="Ethics Note — Conflicting Sensor Reports", prov=_layout_provenance(
            2, left=180, top=410))
    ethics_body = SimpleNamespace(
        self_ref="#/texts/11", label="text", text="Ethics discussion.",
        prov=_layout_provenance(2, left=100, top=380))
    accomplishment = SimpleNamespace(
        self_ref="#/texts/12", label="section_header",
        text="Accomplishment Note", prov=_layout_provenance(
            2, left=180, top=225))
    accomplishment_body = SimpleNamespace(
        self_ref="#/texts/13", label="text", text="Chapter completed.",
        prov=_layout_provenance(2, left=100, top=200))
    document = SimpleNamespace(
        texts=[stale_parent, section, notes, problems, exception, first_items,
               page_number, running, continuation, ethics, ethics_body,
               accomplishment, accomplishment_body],
        pictures=[], tables=[], key_value_items=[], form_items=[])
    raw = [
        SimpleNamespace(
            text=item.text,
            meta=SimpleNamespace(
                headings=[chapter, "Accomplishment Note"], doc_items=[item]))
        for item in (
            first_items, continuation, ethics_body, accomplishment_body)
    ]
    scaffold = [
        {"title": chapter, "level": 1},
        {"title": stale_parent.text, "level": 2},
        # Reproduce the generic TOC fallback that caused the source § heading
        # to retain the stale lettered parent.
        {"title": section.text, "level": 3},
        {"title": notes.text, "level": 3},
        {"title": "Ethics Note — Confli ting Sensor Reports", "level": 3},
        {"title": accomplishment.text, "level": 3},
    ]

    prepared = rag._prepare_source_preserving_chunks(
        raw, document, lambda text: len(text.split()), 100,
        scaffold=scaffold)

    problem_path = [
        chapter, section.text, notes.text, problems.text, exception.text]
    assert [entry[1] for entry in prepared] == [
        problem_path,
        problem_path,
        [chapter, section.text, ethics.text],
        [chapter, section.text, accomplishment.text],
    ]


def test_casebook_lettered_heading_stays_below_numbered_section():
    chapter = "Chapter 2: System Review"
    section = SimpleNamespace(
        self_ref="#/texts/0", label="section_header",
        text="§ 2.02 Validation Inputs and Recorded Outcomes",
        prov=_layout_provenance(1, left=120, top=600))
    subsection = SimpleNamespace(
        self_ref="#/texts/1", label="section_header",
        text="A. Reviewing Recorded Outcomes",
        prov=_layout_provenance(1, left=150, top=550))
    body = SimpleNamespace(
        self_ref="#/texts/2", label="text", text="The analysis begins.",
        prov=_layout_provenance(1, left=90, top=500))
    document = SimpleNamespace(
        texts=[section, subsection, body], pictures=[], tables=[],
        key_value_items=[], form_items=[])
    raw = SimpleNamespace(
        text=body.text,
        meta=SimpleNamespace(headings=[chapter, subsection.text],
                             doc_items=[body]))
    scaffold = [
        {"title": chapter, "level": 1},
        {"title": section.text, "level": 3},
        {"title": subsection.text, "level": 2},
    ]

    prepared = rag._prepare_source_preserving_chunks(
        [raw], document, lambda text: len(text.split()), 100,
        scaffold=scaffold)

    assert prepared[0][1] == [chapter, section.text, subsection.text]


def test_casebook_marker_without_post_period_space_stays_below_section():
    chapter = "Chapter 6: Results"
    section = SimpleNamespace(
        self_ref="#/texts/0", label="section_header",
        text="§6.05 Access, Timing & Format Fairness in Sample Results",
        prov=_layout_provenance(1, left=120, top=650))
    section_body = SimpleNamespace(
        self_ref="#/texts/1", label="text", text="Section introduction.",
        prov=_layout_provenance(1, left=90, top=610))
    subsection = SimpleNamespace(
        self_ref="#/texts/2", label="section_header", text="A.Introduction",
        prov=_layout_provenance(1, left=150, top=560))
    body = SimpleNamespace(
        self_ref="#/texts/3", label="text", text="Subsection discussion.",
        prov=_layout_provenance(1, left=90, top=520))
    document = SimpleNamespace(
        texts=[section, section_body, subsection, body], pictures=[],
        tables=[], key_value_items=[], form_items=[])
    raw = [
        SimpleNamespace(
            text=item.text,
            meta=SimpleNamespace(headings=[chapter, heading],
                                 doc_items=[item]))
        for item, heading in (
            (section_body, section.text), (body, subsection.text))
    ]
    scaffold = [
        {"title": chapter, "level": 1},
        {"title": section.text, "level": 2},
        {"title": "A. Introduction", "level": 2},
    ]

    prepared = rag._prepare_source_preserving_chunks(
        raw, document, lambda text: len(text.split()), 100,
        scaffold=scaffold)

    assert prepared[1][1] == [chapter, section.text, subsection.text]


def test_consecutive_descriptive_and_statutory_headings_keep_source_order():
    panel = SimpleNamespace(
        self_ref="#/texts/0", label="section_header",
        text="Example Regulations",
        prov=_layout_provenance(1, left=140, top=600))
    statute = SimpleNamespace(
        self_ref="#/texts/1", label="section_header",
        text=("§31.600. Prior alert does not block retry; "
              "comparative validation protocol"),
        prov=_layout_provenance(1, left=150, top=550))
    body = SimpleNamespace(
        self_ref="#/texts/2", label="text", text="The protocol applies.",
        prov=_layout_provenance(1, left=90, top=500))
    document = SimpleNamespace(
        texts=[panel, statute, body], pictures=[], tables=[],
        key_value_items=[], form_items=[])
    raw = SimpleNamespace(
        text=body.text,
        meta=SimpleNamespace(headings=[statute.text], doc_items=[body]))

    prepared = rag._prepare_source_preserving_chunks(
        [raw], document, lambda text: len(text.split()), 100)

    assert prepared[0][1] == [panel.text, statute.text]


def test_scaffold_reconciliation_replaces_later_same_page_leaf():
    chapter = "Chapter 7: Recovery and Exceptions"
    stale_parent = "C. Shared Input Problems"
    section = "§7.09 Controller Recovery"
    notes = "Notes & Questions"
    problems = "RECOVERY POLICY PROBLEMS"
    exception = "Offline Controller Exception"
    source_path = f"{chapter} > {stale_parent} > Accomplishment Note"
    levels = {
        rag._source_heading_identity(stale_parent): 2,
        # Deliberately stale generic TOC levels: source semantics must win.
        rag._source_heading_identity(section): 3,
        rag._source_heading_identity(notes): 3,
        rag._source_heading_identity("Accomplishment Note"): 3,
    }

    problem_path = rag._reconcile_scaffold_path_with_source_headings(
        source_path, 3,
        [chapter, section, notes, problems, exception],
        heading_levels=levels)
    ethics_path = rag._reconcile_scaffold_path_with_source_headings(
        source_path, 3,
        [chapter, section, "Ethics Note — Conflicting Sensor Reports"],
        heading_levels=levels)
    accomplishment_path = rag._reconcile_scaffold_path_with_source_headings(
        source_path, 3,
        [chapter, section, "Accomplishment Note"],
        heading_levels=levels)

    assert problem_path == (
        f"{chapter} > {section} > {notes} > {problems} > {exception}")
    assert ethics_path == (
        f"{chapter} > {section} > Ethics Note — Conflicting Sensor Reports")
    assert accomplishment_path == (
        f"{chapter} > {section} > Accomplishment Note")


def test_scaffold_reconciliation_replaces_stale_parent_with_numbered_section():
    chapter = "Chapter 4: System Signals"
    parent = "B. Using Recorded Measurements"
    section = "§ 4.04 Putting Signal Analysis Together"
    problem = "BROKEN SENSOR LINE PROBLEM"
    levels = {
        rag._source_heading_identity(parent): 2,
        rag._source_heading_identity(section): 3,
    }

    reconciled = rag._reconcile_scaffold_path_with_source_headings(
        f"{chapter} > {parent} > Ethics Note: Concealing Calibration Drift", 3,
        [chapter, parent, section, problem],
        structure_profile="us-law-casebook-v1", heading_levels=levels)

    assert reconciled == f"{chapter} > {section} > {problem}"


def test_scaffold_reconciliation_keeps_previous_problem_before_midpage_section():
    chapter = "Chapter 7: Recovery and Exceptions"
    prior_parent = "A. Shared Review Problems"
    local_parent = "Two Devices Report an Event and Both Are Flagged"
    problem = "PROBLEMS 6-8"
    levels = {
        rag._source_heading_identity(prior_parent): 2,
        rag._source_heading_identity(local_parent): 3,
        rag._source_heading_identity("B. Reconciliation"): 2,
    }

    reconciled = rag._reconcile_scaffold_path_with_source_headings(
        f"{chapter} > B. Reconciliation", 2,
        [chapter, prior_parent, local_parent, problem],
        heading_levels=levels)

    assert reconciled == (
        f"{chapter} > {prior_parent} > {local_parent} > {problem}")


def test_disjoint_section_flow_recovery_joins_verified_continuations(tmp_path):
    import pymupdf

    pdf_path = tmp_path / "section-flow.pdf"
    pdf = pymupdf.open()
    for _ in range(3):
        pdf.new_page(width=612, height=792)
    previous_text = "The report found a shortage of"
    top_text = "care across the state"
    displaced_text = "This finding changed policy."
    heading_text = "D. A NEW SECTION"
    bottom_text = "The later section continues with its"
    next_text = "emphasis on better outcomes."
    pdf[0].insert_text((72, 100), previous_text)
    pdf[1].insert_text((72, 80), top_text)
    pdf[1].insert_text((72, 130), displaced_text)
    pdf[1].insert_text((72, 250), heading_text)
    pdf[1].insert_text((72, 350), bottom_text)
    pdf[2].insert_text((72, 80), next_text)
    pdf.save(pdf_path)
    pdf.close()

    def provenance(page, top, bottom, charspan):
        return SimpleNamespace(
            page_no=page,
            bbox=SimpleNamespace(
                l=68.0, t=top, r=500.0, b=bottom,
                coord_origin="TOPLEFT"),
            charspan=charspan,
        )

    previous = SimpleNamespace(
        self_ref="#/texts/1", label="text", text=previous_text,
        prov=[provenance(1, 84, 106, [0, len(previous_text)])])
    heading = SimpleNamespace(
        self_ref="#/texts/2", label="section_header", text=heading_text,
        prov=[provenance(2, 234, 256, [0, len(heading_text)])])
    candidate_text = bottom_text + " " + top_text
    candidate = SimpleNamespace(
        self_ref="#/texts/3", label="text", text=candidate_text,
        prov=[
            provenance(2, 334, 356, [0, len(bottom_text)]),
            provenance(2, 64, 86, [len(bottom_text) + 1,
                                    len(candidate_text)]),
        ])
    displaced = SimpleNamespace(
        self_ref="#/texts/4", label="text", text=displaced_text,
        prov=[provenance(2, 114, 136, [0, len(displaced_text)])])
    following = SimpleNamespace(
        self_ref="#/texts/5", label="text", text=next_text,
        prov=[provenance(3, 64, 86, [0, len(next_text)])])
    document = SimpleNamespace(
        texts=[previous, heading, candidate, displaced, following])

    recovered = rag._recover_disjoint_section_flow_groups(
        document, pdf_path)

    assert [(entry.refs, entry.text) for entry in recovered] == [
        (
            ("#/texts/1", "#/texts/3", "#/texts/4"),
            previous_text + " " + top_text + "\n\n" + displaced_text,
        ),
        (
            ("#/texts/3", "#/texts/5"),
            bottom_text + " " + next_text,
        ),
    ]


def test_overlapping_native_recovery_finds_detached_late_line_fragment(
        tmp_path):
    import pymupdf

    pdf_path = tmp_path / "detached-line-fragment.pdf"
    native_text = (
        "The monitor carries the task of verifying each recorded signal in "
        "every scheduled device audit."
    )
    pdf = pymupdf.open()
    page = pdf.new_page(width=612, height=792)
    page.insert_textbox(
        pymupdf.Rect(72, 80, 540, 150), native_text, fontsize=11)
    words = page.get_text("words", sort=True)
    verifying = next(word for word in words if word[4] == "verifying")
    text_words = [word for word in words if word[4] != "verifying"]
    line_box = (
        min(word[0] for word in words), min(word[1] for word in words),
        max(word[2] for word in words), max(word[3] for word in words),
    )
    pdf.save(pdf_path)
    pdf.close()

    parent = SimpleNamespace(cref="#/body")

    def item(ref, text, box):
        return SimpleNamespace(
            self_ref=ref, label="text", text=text, parent=parent,
            prov=[SimpleNamespace(
                page_no=1,
                bbox=SimpleNamespace(
                    l=box[0], t=box[1], r=box[2], b=box[3],
                    coord_origin="TOPLEFT"),
            )],
        )

    host_text = " ".join(str(word[4]) for word in text_words)
    host = item("#/texts/0", host_text, line_box)
    unrelated = item(
        "#/texts/1", "A later independent paragraph.",
        (72.0, 250.0, 300.0, 270.0))
    detached = item(
        "#/texts/2", "verifying",
        (verifying[0], verifying[1], verifying[2], verifying[3]))

    recovered = rag._recover_overlapping_native_text_groups(
        SimpleNamespace(texts=[host, unrelated, detached]), pdf_path)

    assert [(entry.refs, entry.text) for entry in recovered] == [(
        (host.self_ref, detached.self_ref),
        native_text,
    )]


def test_overlapping_native_recovery_accepts_same_line_multispan_fragment(
        tmp_path):
    import pymupdf

    pdf_path = tmp_path / "same-line-multispan-fragment.pdf"
    native_text = (
        "The controller accepts data not of the remote node own making today."
    )
    pdf = pymupdf.open()
    page = pdf.new_page(width=612, height=792)
    page.insert_text((72, 100), native_text, fontsize=11)
    words = page.get_text("words", sort=True)
    detached_words = [word for word in words if word[4] in {"of", "the"}]
    host_words = [word for word in words if word not in detached_words]
    line_box = (
        min(word[0] for word in words), min(word[1] for word in words),
        max(word[2] for word in words), max(word[3] for word in words),
    )
    pdf.save(pdf_path)
    pdf.close()

    parent = SimpleNamespace(cref="#/body")

    def provenance(word):
        return SimpleNamespace(
            page_no=1,
            bbox=SimpleNamespace(
                l=word[0], t=word[1], r=word[2], b=word[3],
                coord_origin="TOPLEFT"),
        )

    host = SimpleNamespace(
        self_ref="#/texts/0", label="text",
        text=" ".join(str(word[4]) for word in host_words), parent=parent,
        prov=[SimpleNamespace(
            page_no=1,
            bbox=SimpleNamespace(
                l=line_box[0], t=line_box[1], r=line_box[2], b=line_box[3],
                coord_origin="TOPLEFT"),
        )],
    )
    detached = SimpleNamespace(
        self_ref="#/texts/1", label="text", text="of the", parent=parent,
        prov=[provenance(word) for word in detached_words],
    )

    recovered = rag._recover_overlapping_native_text_groups(
        SimpleNamespace(texts=[host, detached]), pdf_path)

    assert [(entry.refs, entry.text) for entry in recovered] == [(
        (host.self_ref, detached.self_ref),
        native_text,
    )]


def test_overlapping_native_recovery_uses_native_equivalence_for_ocr_splits(
        tmp_path):
    import pymupdf

    pdf_path = tmp_path / "ocr-split-detached-fragment.pdf"
    native_text = (
        "A field operator usually has the task of verifying that the "
        "sensor reading was a valid input today."
    )
    pdf = pymupdf.open()
    page = pdf.new_page(width=612, height=792)
    page.insert_text((72, 100), native_text, fontsize=10)
    words = page.get_text("words", sort=True)
    verifying = next(word for word in words if word[4] == "verifying")
    line_box = (
        min(word[0] for word in words), min(word[1] for word in words),
        max(word[2] for word in words), max(word[3] for word in words),
    )
    pdf.save(pdf_path)
    pdf.close()

    parent = SimpleNamespace(cref="#/body")
    host_text = native_text.replace(" verifying", "")
    host_text = host_text.replace("operator", "oper ator")
    host = SimpleNamespace(
        self_ref="#/texts/0", label="text", text=host_text, parent=parent,
        prov=[SimpleNamespace(
            page_no=1,
            bbox=SimpleNamespace(
                l=line_box[0], t=line_box[1], r=line_box[2], b=line_box[3],
                coord_origin="TOPLEFT"),
        )],
    )
    detached = SimpleNamespace(
        self_ref="#/texts/1", label="text", text="verifying", parent=parent,
        prov=[SimpleNamespace(
            page_no=1,
            bbox=SimpleNamespace(
                l=verifying[0], t=verifying[1], r=verifying[2], b=verifying[3],
                coord_origin="TOPLEFT"),
        )],
    )

    recovered = rag._recover_overlapping_native_text_groups(
        SimpleNamespace(texts=[host, detached]), pdf_path)

    assert [(entry.refs, entry.text) for entry in recovered] == [(
        (host.self_ref, detached.self_ref),
        native_text,
    )]


def test_cross_page_word_recovery_joins_only_attested_compound(tmp_path):
    import pymupdf

    pdf_path = tmp_path / "cross-page-word.pdf"
    pdf = pymupdf.open()
    for _ in range(4):
        pdf.new_page(width=612, height=792)
    pdf[0].insert_text((72, 100), "An imperfect fault-")
    pdf[0].insert_text((72, 150), "fault-tolerant")
    pdf[1].insert_text((72, 80), "tolerant design applies here.")
    pdf[2].insert_text((72, 100), "The depart-")
    pdf[3].insert_text((72, 80), "ment responded.")
    pdf.save(pdf_path)
    pdf.close()

    def item(ref, page, text, top, bottom):
        return SimpleNamespace(
            self_ref=ref, label="text", text=text,
            prov=[SimpleNamespace(
                page_no=page,
                bbox=SimpleNamespace(
                    l=68.0, t=top, r=500.0, b=bottom,
                    coord_origin="TOPLEFT"),
            )],
        )

    left = item("#/texts/1", 1, "An imperfect fault-", 84, 106)
    right = item("#/texts/2", 2, "tolerant design applies here.", 64, 86)
    discretionary_left = item("#/texts/3", 3, "The depart-", 84, 106)
    discretionary_right = item(
        "#/texts/4", 4, "ment responded.", 64, 86)
    document = SimpleNamespace(texts=[
        left, right, discretionary_left, discretionary_right])

    recovered = rag._recover_cross_page_native_word_groups(
        document, pdf_path)

    assert [(entry.refs, entry.text) for entry in recovered] == [(
        ("#/texts/1", "#/texts/2"),
        "An imperfect fault-tolerant design applies here.",
    )]


@pytest.mark.parametrize(
    ("left_fragment", "right_fragment", "joined_word"),
    [
        ("num", "ber", "number"),
        ("ques", "tion", "question"),
        ("prop", "erty", "property"),
        ("Opera", "tor's", "Operator's"),
        ("min", "ing", "mining"),
    ],
)
def test_adjacent_native_split_word_group_joins_attested_plain_word(
        tmp_path, left_fragment, right_fragment, joined_word):
    import pymupdf

    pdf_path = tmp_path / f"{joined_word.casefold()}-split.pdf"
    pdf = pymupdf.open()
    for _ in range(3):
        pdf.new_page(width=612, height=792)
    left_text = f"The paragraph ends with {left_fragment}-"
    right_text = f"{right_fragment} and then continues."
    pdf[0].insert_text((72, 700), left_text)
    pdf[1].insert_text((72, 80), right_text)
    pdf[2].insert_text((72, 100), f"An intact {joined_word} appears here.")
    pdf.save(pdf_path)
    pdf.close()

    parent = SimpleNamespace(cref="#/body")

    def item(ref, page, text, top, bottom):
        return SimpleNamespace(
            self_ref=ref, label="text", text=text, parent=parent,
            prov=[SimpleNamespace(
                page_no=page,
                bbox=SimpleNamespace(
                    l=68.0, t=top, r=500.0, b=bottom,
                    coord_origin="TOPLEFT"),
            )],
        )

    left = item("#/texts/1", 1, left_text, 680, 710)
    right = item("#/texts/2", 2, right_text, 60, 90)
    document = SimpleNamespace(texts=[left, right])

    recovered = rag._recover_adjacent_native_split_word_groups(
        document, pdf_path)

    assert [(entry.refs, entry.text) for entry in recovered] == [(
        ("#/texts/1", "#/texts/2"),
        f"The paragraph ends with {joined_word} and then continues.",
    )]


def test_adjacent_native_split_word_group_supports_same_page_lines(tmp_path):
    import pymupdf

    pdf_path = tmp_path / "same-page-split.pdf"
    pdf = pymupdf.open()
    pdf.new_page(width=612, height=792)
    pdf[0].insert_text((72, 100), "The telephone num-")
    pdf[0].insert_text((72, 125), "ber appears here.")
    pdf[0].insert_text((72, 200), "Another number is listed.")
    pdf.save(pdf_path)
    pdf.close()

    parent = SimpleNamespace(cref="#/body")

    def item(ref, text, top, bottom):
        return SimpleNamespace(
            self_ref=ref, label="text", text=text, parent=parent,
            prov=[SimpleNamespace(
                page_no=1,
                bbox=SimpleNamespace(
                    l=68.0, t=top, r=500.0, b=bottom,
                    coord_origin="TOPLEFT"),
            )],
        )

    left = item("#/texts/1", "The telephone num-", 84, 106)
    right = item("#/texts/2", "ber appears here.", 109, 131)

    recovered = rag._recover_adjacent_native_split_word_groups(
        SimpleNamespace(texts=[left, right]), pdf_path)

    assert [(entry.refs, entry.text) for entry in recovered] == [(
        ("#/texts/1", "#/texts/2"),
        "The telephone number appears here.",
    )]


def test_adjacent_native_split_word_group_requires_intact_native_proof(
        tmp_path):
    import pymupdf

    pdf_path = tmp_path / "unattested-split.pdf"
    pdf = pymupdf.open()
    for _ in range(2):
        pdf.new_page(width=612, height=792)
    pdf[0].insert_text((72, 700), "The depart-")
    pdf[1].insert_text((72, 80), "ment responded.")
    pdf.save(pdf_path)
    pdf.close()

    parent = SimpleNamespace(cref="#/body")
    left = SimpleNamespace(
        self_ref="#/texts/1", label="text", text="The depart-",
        parent=parent,
        prov=[SimpleNamespace(
            page_no=1,
            bbox=SimpleNamespace(
                l=68.0, t=680.0, r=500.0, b=710.0,
                coord_origin="TOPLEFT"))])
    right = SimpleNamespace(
        self_ref="#/texts/2", label="text", text="ment responded.",
        parent=parent,
        prov=[SimpleNamespace(
            page_no=2,
            bbox=SimpleNamespace(
                l=68.0, t=60.0, r=500.0, b=90.0,
                coord_origin="TOPLEFT"))])

    assert rag._recover_adjacent_native_split_word_groups(
        SimpleNamespace(texts=[left, right]), pdf_path) == ()


def test_adjacent_native_split_word_group_rejects_unrelated_items(tmp_path):
    import pymupdf

    pdf_path = tmp_path / "unrelated-split.pdf"
    pdf = pymupdf.open()
    for _ in range(3):
        pdf.new_page(width=612, height=792)
    pdf[0].insert_text((72, 700), "A telephone num-")
    pdf[1].insert_text((72, 80), "ber starts another section.")
    pdf[2].insert_text((72, 100), "The number is intact here.")
    pdf.save(pdf_path)
    pdf.close()

    def item(ref, page, text, parent):
        top, bottom = (680.0, 710.0) if page == 1 else (60.0, 90.0)
        return SimpleNamespace(
            self_ref=ref, label="text", text=text,
            parent=SimpleNamespace(cref=parent),
            prov=[SimpleNamespace(
                page_no=page,
                bbox=SimpleNamespace(
                    l=68.0, t=top, r=500.0, b=bottom,
                    coord_origin="TOPLEFT"))])

    left = item(
        "#/texts/9001", 1, "A telephone num-", "#/groups/9001")
    right = item(
        "#/texts/9002", 2, "ber starts another section.", "#/groups/9002")

    assert rag._recover_adjacent_native_split_word_groups(
        SimpleNamespace(texts=[left, right]), pdf_path) == ()


def test_adjacent_native_split_word_group_rejects_ambiguous_spelling(
        tmp_path):
    import pymupdf

    pdf_path = tmp_path / "ambiguous-split.pdf"
    pdf = pymupdf.open()
    for _ in range(3):
        pdf.new_page(width=612, height=792)
    pdf[0].insert_text((72, 700), "The firms co-")
    pdf[1].insert_text((72, 80), "operate on the project.")
    pdf[2].insert_text((72, 100), "Both cooperate and co-operate appear.")
    pdf.save(pdf_path)
    pdf.close()

    parent = SimpleNamespace(cref="#/body")

    def item(ref, page, text):
        top, bottom = (680.0, 710.0) if page == 1 else (60.0, 90.0)
        return SimpleNamespace(
            self_ref=ref, label="text", text=text, parent=parent,
            prov=[SimpleNamespace(
                page_no=page,
                bbox=SimpleNamespace(
                    l=68.0, t=top, r=500.0, b=bottom,
                    coord_origin="TOPLEFT"))])

    left = item("#/texts/1", 1, "The firms co-")
    right = item("#/texts/2", 2, "operate on the project.")

    assert rag._recover_adjacent_native_split_word_groups(
        SimpleNamespace(texts=[left, right]), pdf_path) == ()


def test_native_pdf_word_alignment_repairs_only_the_matching_position():
    source = (
        "Au ditor's memo shows we under stand a sensorbank, a sensor bank, "
        "and verify channel b.")
    native_text = (
        "Auditor’s memo shows we understand a sensorbank, a sensor bank, "
        "and verify channelb.")
    native_words = []
    native_spans = []
    cursor = 0.0
    for word in native_text.split():
        right = cursor + max(8.0, len(word) * 5.0)
        native_words.append((cursor, 0.0, right, 12.0, word))
        if word == "channelb.":
            marker_left = right - 5.0
            native_spans.extend((
                {
                    "bbox": (cursor, 0.0, marker_left, 12.0),
                    "size": 10.5, "flags": 4, "text": "channel",
                },
                {
                    "bbox": (marker_left, 1.0, right, 9.0),
                    "size": 7.0, "flags": 21, "text": "b",
                },
            ))
        else:
            native_spans.append({
                "bbox": (cursor, 0.0, right, 12.0),
                "size": 10.5, "flags": 4, "text": word,
            })
        cursor = right + 3.0

    assert rag._repair_split_words_from_native_pdf(
        source, native_words, native_spans) == (
            "Auditor's memo shows we understand a sensorbank, a sensor bank, "
            "and verify channel b.")


def test_native_pdf_word_alignment_repairs_consecutive_split_words():
    source = "Thi s t hings example is badly split."
    native_words = [
        (index * 30.0, 0.0, index * 30.0 + 25.0, 12.0, word)
        for index, word in enumerate(
            ("This", "things", "example", "is", "badly", "split."))
    ]
    native_spans = [
        {"bbox": word[:4], "size": 10.5, "flags": 4, "text": word[4]}
        for word in native_words
    ]

    assert rag._repair_split_words_from_native_pdf(
        source, native_words, native_spans) == (
            "This things example is badly split.")


def test_native_pdf_boundary_repair_converges_across_adjacent_defects():
    source = "Field Operation Instructio ns -Revision."
    native_words = [
        (0.0, 0.0, 50.0, 12.0, "Field", 0, 0, 0),
        (53.0, 0.0, 75.0, 12.0, "Operation", 0, 0, 1),
        (78.0, 0.0, 170.0, 12.0, "Instructions—Revision.", 0, 0, 2),
    ]

    assert rag._repair_split_words_to_fixpoint(
        source, native_words, []) == (
            "Field Operation Instructions—Revision.")


def test_native_pdf_word_alignment_restores_fused_words_and_dashes():
    source = "They understoodquite why nodes—among and input -and output."
    native_text = (
        "They understood quite why nodes—among and input—and output.")
    native_words = []
    native_spans = []
    cursor = 0.0
    for word in native_text.split():
        right = cursor + max(8.0, len(word) * 5.0)
        native_words.append((cursor, 0.0, right, 12.0, word))
        native_spans.append({
            "bbox": (cursor, 0.0, right, 12.0),
            "size": 10.5, "flags": 4, "text": word,
        })
        cursor = right + 3.0

    assert rag._repair_split_words_from_native_pdf(
        source, native_words, native_spans) == native_text


def test_native_pdf_word_alignment_restores_multi_hyphen_compounds():
    source = "A 7dayold sensor used overtheair updates."
    native_text = "A 7-day-old sensor used over-the-air updates."
    native_words = []
    native_spans = []
    cursor = 0.0
    for word in native_text.split():
        right = cursor + max(8.0, len(word) * 5.0)
        native_words.append((cursor, 0.0, right, 12.0, word))
        native_spans.append({
            "bbox": (cursor, 0.0, right, 12.0),
            "size": 10.5, "flags": 4, "text": word,
        })
        cursor = right + 3.0

    assert rag._repair_split_words_from_native_pdf(
        source, native_words, native_spans) == native_text


def test_native_pdf_word_alignment_accepts_baseline_aligned_small_caps():
    source = "REVIEWER O'NE ILL approved."
    words = [
        (0.0, 0.0, 40.0, 12.0, "REVIEWER"),
        (43.0, 0.0, 95.0, 12.0, "O’NEILL"),
        (98.0, 0.0, 145.0, 12.0, "approved."),
    ]
    spans = [
        {"bbox": words[0][:4], "size": 10.5, "flags": 4,
         "origin": (0.0, 10.0), "text": "REVIEWER"},
        {"bbox": (43.0, 0.0, 60.0, 12.0), "size": 10.5, "flags": 4,
         "origin": (43.0, 10.0), "text": "O’NE"},
        {"bbox": (60.0, 1.5, 95.0, 12.0), "size": 8.5, "flags": 4,
         "origin": (60.0, 10.0), "text": "ILL"},
        {"bbox": words[2][:4], "size": 10.5, "flags": 4,
         "origin": (98.0, 10.0), "text": "approved."},
    ]

    assert rag._repair_split_words_from_native_pdf(
        source, words, spans) == "REVIEWER O'NEILL approved."


def _apply_native_fallback_edits(source, fallback):
    assert fallback is not None
    _candidate, edits = fallback
    repaired = source
    for old, new in edits:
        repaired = repaired.replace(old, new)
    return repaired


def test_native_ocr_fallback_repairs_strongly_aligned_clause_gibberish():
    source = (
        "The operator usually has responsibility for verifying nt  s s s s s "
        "before the system considers the remaining events in the record. "
        "The reviewer then checks measurements and notes for a careful audit "
        "under the configured validation policy.")
    candidate = (
        "The operator usually has responsibility for verifying that the "
        "submitted request matches the configuration before the system "
        "considers the remaining events in the record. The reviewer then "
        "checks measurements and notes for a careful audit under the configured "
        "validation policy.")
    words = [
        (index, 0, index + 1, 1, word, 0, 0, index)
        for index, word in enumerate(candidate.split())
    ]

    repaired = _apply_native_fallback_edits(
        source,
        rag._native_ocr_fallback(
            source, candidate, words, allow_wholesale=True),
    )

    assert "that the submitted request matches the configuration" in repaired
    assert rag._ocr_corruption_score(repaired) == 0


def test_native_ocr_fallback_restores_bracketed_omission_without_spelling():
    source = (
        "Operators require proof that the device before the review must "
        "identify every material event in the record for the auditor.")
    candidate = (
        "Operators require proof that the device recorded the final calibration "
        "result before the review must identify every material event in the "
        "record for the auditor.")
    words = [
        (index, 0, index + 1, 1, word, 0, 0, index)
        for index, word in enumerate(candidate.split())
    ]

    repaired = _apply_native_fallback_edits(
        source,
        rag._native_ocr_fallback(
            source, candidate, words, allow_wholesale=True),
    )

    assert "recorded the final calibration result" in repaired


def test_native_ocr_fallback_recovers_one_line_mostly_gibberish_item():
    source = "J   d  od    o  e"
    candidate = (
        "(a) The sample process records a result when the operator confirms "
        "the requested action; or")
    words = [
        (index, 0, index + 1, 1, word, 0, 0, index)
        for index, word in enumerate(candidate.split())
    ]

    repaired = _apply_native_fallback_edits(
        source,
        rag._native_ocr_fallback(
            source, candidate, words, allow_wholesale=True),
    )

    assert repaired == candidate


def test_native_ocr_fallback_recovers_short_corrupt_running_header():
    source = "SI     AW"
    candidate = "§ 1.03 a brief history of the sensor array"
    words = [
        (index, 0, index + 1, 1, word, 0, 0, index)
        for index, word in enumerate(candidate.split())
    ]

    repaired = _apply_native_fallback_edits(
        source,
        rag._native_ocr_fallback(
            source, candidate, words, allow_wholesale=True),
    )

    assert repaired == candidate


def test_native_ocr_fallback_restores_source_attested_ampersand_separator():
    source = "See Orion v. Delta R.     D. Lab for the sample protocol."
    candidate = "See Orion v. Delta R. & D. Lab for the sample protocol."
    words = [
        (index, 0, index + 1, 1, word, 0, 0, index)
        for index, word in enumerate(candidate.split())
    ]

    repaired = _apply_native_fallback_edits(
        source,
        rag._native_ocr_fallback(
            source, candidate, words, allow_wholesale=True),
    )

    assert repaired == candidate


def test_native_ocr_fallback_preserves_source_attested_glyph_gaps():
    source = "Th s sample rema ns st ble across the f rst audit pass."
    words = [
        (index, 0, index + 1, 1, word, 0, 0, index)
        for index, word in enumerate(source.split())
    ]

    assert rag._native_ocr_fallback(
        source, source, words, allow_wholesale=True) is None


def test_native_list_prefix_stripping_requires_source_attestation():
    item = SimpleNamespace(
        label="list_item", marker="13.",
        orig="13. Operators may retry failed checks.")

    assert rag._strip_native_list_prefix(
        item, "Operators may retry failed checks.",
        "13. Operators may retry failed checks.") == (
            "Operators may retry failed checks.")
    assert rag._strip_native_list_prefix(
        item, "13. Operators may retry failed checks.",
        "13. Operators may retry failed checks.") == (
            "13. Operators may retry failed checks.")


def test_aligned_side_by_side_lists_become_one_markdown_table():
    left_header = SimpleNamespace(
        self_ref="#/texts/0", label="section_header", text="Input channel",
        prov=_layout_provenance(6, left=90, top=570))
    right_header = SimpleNamespace(
        self_ref="#/texts/1", label="text", text="Expected state",
        prov=_layout_provenance(6, left=260, top=570, right=460))
    left = [
        SimpleNamespace(
            self_ref=f"#/texts/{index + 2}", label="list_item", text=value,
            prov=_layout_provenance(6, left=90, top=520 - index * 14))
        for index, value in enumerate(("Temperature", "Pressure", "Voltage"))
    ]
    right = [
        SimpleNamespace(
            self_ref=f"#/texts/{index + 5}", label="list_item", text=value,
            prov=_layout_provenance(6, left=260, top=520 - index * 14,
                                    right=460))
        for index, value in enumerate(("Stable", "Ready", "Calibrated"))
    ]
    document = SimpleNamespace(
        texts=[left_header, right_header, *left, *right])

    recovered = rag._detect_aligned_list_tables(document)

    assert len(recovered) == 1
    assert recovered[0].markdown == (
        "| Input channel | Expected state |\n"
        "|---|---|\n"
        "| Temperature | Stable |\n"
        "| Pressure | Ready |\n"
        "| Voltage | Calibrated |")
    assert len({item.self_ref for item in recovered[0].items}) == 8
    repaired = rag._detect_aligned_list_tables(
        document, text_overrides={right[0].self_ref: "Verified stable"})
    assert "| Temperature | Verified stable |" in repaired[0].markdown
    assert "| Temperature | Stable |" not in repaired[0].markdown


def test_indented_outline_is_not_misread_as_parallel_table_columns():
    headers = [
        SimpleNamespace(
            self_ref="#/texts/0", label="section_header", text="Telemetry",
            prov=_layout_provenance(6, left=120, top=600)),
        SimpleNamespace(
            self_ref="#/texts/1", label="section_header",
            text="I. Inbound Signal Checks",
            prov=_layout_provenance(6, left=120, top=570)),
        SimpleNamespace(
            self_ref="#/texts/2", label="section_header",
            text="Buffer Health",
            prov=_layout_provenance(6, left=130, top=500)),
    ]
    first_indent = [
        SimpleNamespace(
            self_ref=f"#/texts/{index + 3}", label="list_item", text=value,
            prov=_layout_provenance(6, left=142, top=550 - index * 35,
                                    right=410))
        for index, value in enumerate((
            "Checksum Validation", "Sequence Window", "Clock Drift"))
    ]
    second_indent = [
        SimpleNamespace(
            self_ref=f"#/texts/{index + 6}", label="list_item", text=value,
            prov=_layout_provenance(6, left=162, top=445 - index * 14,
                                    right=300))
        for index, value in enumerate((
            "Queue Depth", "Retry Budget", "Dead-Letter Count"))
    ]

    assert rag._detect_aligned_list_tables(SimpleNamespace(
        texts=[*headers, *first_indent, *second_indent])) == []


def test_centered_banner_outline_preserves_nested_list_indentation():
    def item(index, label, text, left, top, page=42):
        return SimpleNamespace(
            self_ref=f"#/texts/{index}", label=label, text=text,
            prov=_layout_provenance(page, left=left, top=top, right=480))

    section = "§4.04 Combining Signal Checks"
    major = item(30, "section_header", section, 120, 650, page=41)
    body = item(0, "text", "Prior operator note.", 115, 670)
    banner = item(1, "section_header", "Signal Routing", 274, 470)
    first = item(
        2, "section_header", "I. Inbound Paths", 121, 448)
    first_items = [
        item(3, "list_item", "Direct Sensor Feed", 142, 434),
        item(4, "list_item", "Buffered Telemetry", 142, 419),
        item(5, "list_item", "Manual Console", 142, 404),
    ]
    screening = item(6, "section_header", "Input Screening", 132, 389)
    screening_items = [
        item(7, "list_item", "Checksum Validation", 152, 376),
        item(8, "list_item", "Sequence Window", 152, 361),
    ]
    second = item(
        9, "section_header", "II. Recovery Paths", 122, 346)
    second_items = [item(10, "list_item", "Warm Standby", 142, 331)]
    fallback = item(
        11, "section_header", "Fallback Routing", 131, 316)
    fallback_items = [
        item(12, "list_item", "Secondary Relay", 152, 301),
        item(13, "list_item", "Store and Forward", 152, 286),
        item(14, "list_item", "Manual Dispatch", 152, 271),
    ]
    third = item(
        15, "section_header", "III. Output Records", 122, 256)
    third_items = [
        item(16, "list_item", "Operator Escalation", 142, 241),
        item(17, "list_item", "Audit Export", 142, 226),
        item(18, "list_item", "Local Archive", 152, 211),
        item(19, "list_item", "Signed Receipt", 162, 196),
        item(20, "list_item", "Retention Window", 162, 181),
    ]
    document = SimpleNamespace(texts=[
        major, body, banner, first, *first_items, screening, *screening_items,
        second, *second_items, fallback, *fallback_items, third, *third_items,
    ])

    recovered = rag._detect_indented_list_outlines(document)

    assert len(recovered) == 5
    assert [layout.headings for layout in recovered] == [
        ("Signal Routing", "I. Inbound Paths"),
        ("Signal Routing", "I. Inbound Paths", "Input Screening"),
        ("Signal Routing", "II. Recovery Paths"),
        ("Signal Routing", "II. Recovery Paths", "Fallback Routing"),
        ("Signal Routing", "III. Output Records"),
    ]
    assert [layout.markdown for layout in recovered[:4]] == [
        "- Direct Sensor Feed\n- Buffered Telemetry\n- Manual Console",
        "- Checksum Validation\n- Sequence Window",
        "- Warm Standby",
        "- Secondary Relay\n- Store and Forward\n- Manual Dispatch",
    ]
    assert recovered[4].markdown == (
        "- Operator Escalation\n"
        "- Audit Export\n"
        "  - Local Archive\n"
        "    - Signed Receipt\n"
        "    - Retention Window")
    assert tuple(
        item for layout in recovered for item in layout.items
    ) == tuple(document.texts[2:])
    assert all(layout.preserve_fallback_major for layout in recovered)
    assert rag._normalize_source_chunk_text(
        recovered[4].markdown,
        "body",
        preserve_source_identity=True,
    ) == recovered[4].markdown

    chapter = "Chapter 4· Signal Routing"
    document.pictures = []
    document.tables = []
    document.key_value_items = []
    document.form_items = []
    raw = SimpleNamespace(
        text="Outline panel",
        meta=SimpleNamespace(
            headings=[chapter, "Accomplishment Note"],
            doc_items=document.texts[2:],
        ),
    )
    prepared = rag._prepare_source_preserving_chunks(
        [raw], document, lambda text: len(text.split()), 100)
    assert [entry[0] for entry in prepared] == [
        "Prior operator note.",
        *(layout.markdown for layout in recovered),
    ]
    assert [entry[1] for entry in prepared[1:]] == [
        [chapter, section, *layout.headings] for layout in recovered]


def test_chapter_summary_continuation_recovers_header_labeled_entries():
    chapter = SimpleNamespace(
        self_ref="#/texts/0", label="section_header",
        text="Chapter 1 Sensor Operations",
        prov=_layout_provenance(1, left=150, top=700))
    summary = SimpleNamespace(
        self_ref="#/texts/1", label="section_header",
        text="Summary of Contents",
        prov=_layout_provenance(1, left=200, top=600))
    first = SimpleNamespace(
        self_ref="#/texts/2", label="list_item", text="§1.01 Boot Sequence",
        prov=_layout_provenance(1, left=120, top=550))
    continued_heading = SimpleNamespace(
        self_ref="#/texts/3", label="section_header",
        text="§1.06 Alarm Routing",
        prov=_layout_provenance(2, left=120, top=670))
    continued_item = SimpleNamespace(
        self_ref="#/texts/4", label="list_item",
        text="§1.09 Audit Export",
        prov=_layout_provenance(2, left=120, top=630))
    actual_section = SimpleNamespace(
        self_ref="#/texts/5", label="section_header",
        text="§1.01 Boot Sequence",
        prov=_layout_provenance(2, left=120, top=350))
    body = SimpleNamespace(
        self_ref="#/texts/6", label="text", text="The chapter begins.",
        prov=_layout_provenance(2, left=90, top=300))
    document = SimpleNamespace(texts=[
        chapter, summary, first, continued_heading, continued_item,
        actual_section, body,
    ])

    recovered = rag._recover_chapter_summary_lists(
        document, lambda text: len(text.split()), 100)

    assert len(recovered) == 1
    assert recovered[0].headings == ("Summary of Contents",)
    assert recovered[0].markdown == (
        "- §1.01 Boot Sequence\n"
        "- §1.06 Alarm Routing\n"
        "- §1.09 Audit Export")
    assert actual_section not in recovered[0].items
    repaired = rag._recover_chapter_summary_lists(
        document, lambda text: len(text.split()), 100,
        text_overrides={
            continued_item.self_ref:
                "§1.09 Corrected Audit Export",
        })
    assert repaired[0].markdown.endswith(
        "- §1.09 Corrected Audit Export")


def test_chapter_summary_excludes_running_header_and_preserves_markers():
    chapter = SimpleNamespace(
        self_ref="#/texts/0", label="section_header",
        text="Chapter 1 Sensor Operations",
        prov=_layout_provenance(1, left=150, top=655))
    summary = SimpleNamespace(
        self_ref="#/texts/1", label="section_header",
        text="Summary of Contents",
        prov=_layout_provenance(1, left=200, top=600))
    group = SimpleNamespace(cref="#/groups/1")
    first = SimpleNamespace(
        self_ref="#/texts/2", label="list_item", text="§1.01 Boot Sequence",
        marker="", parent=group,
        prov=_layout_provenance(1, left=72, top=550))
    letter = SimpleNamespace(
        self_ref="#/texts/3", label="list_item", text="Power Check",
        marker="A.", parent=group,
        prov=_layout_provenance(1, left=98, top=520))
    number = SimpleNamespace(
        self_ref="#/texts/4", label="list_item", text="Primary Channel",
        marker="1.", parent=group,
        prov=_layout_provenance(1, left=114, top=490))
    running = SimpleNamespace(
        self_ref="#/texts/5", label="section_header",
        text="1 · SENSOR OPERATIONS",
        prov=_layout_provenance(2, left=210, top=694))
    continued = SimpleNamespace(
        self_ref="#/texts/6", label="section_header",
        text="§1.06 Alarm Routing",
        prov=_layout_provenance(2, left=72, top=650))
    actual_section = SimpleNamespace(
        self_ref="#/texts/7", label="section_header",
        text="§1.01 Boot Sequence",
        prov=_layout_provenance(2, left=72, top=350))
    document = SimpleNamespace(
        texts=[chapter, summary, first, letter, number, running, continued,
               actual_section],
        tables=[],
        pages={
            page: SimpleNamespace(
                size=SimpleNamespace(width=549, height=738))
            for page in (1, 2)
        },
    )

    recovered = rag._recover_chapter_summary_lists(
        document, lambda text: len(text.split()), 100)

    assert len(recovered) == 1
    assert recovered[0].markdown == (
        "- §1.01 Boot Sequence\n"
        "  - A\\. Power Check\n"
        "    - 1\\. Primary Channel\n"
        "- §1.06 Alarm Routing")
    assert running not in recovered[0].items


def test_split_chapter_title_keeps_recovered_summary_heading_transient():
    chapter_title = "Chapter 6 · Signal Routing"
    chapter = SimpleNamespace(
        self_ref="#/texts/0", label="section_header", text="Chapter 6",
        content_layer="body",
        prov=_layout_provenance(1, left=70, top=700))
    subject = SimpleNamespace(
        self_ref="#/texts/1", label="section_header", text="Signal Routing",
        content_layer="body",
        prov=_layout_provenance(1, left=70, top=660))
    summary = SimpleNamespace(
        self_ref="#/texts/2", label="section_header",
        text="Summary of Contents", content_layer="body",
        prov=_layout_provenance(1, left=70, top=620))
    outline = SimpleNamespace(
        self_ref="#/texts/3", label="list_item",
        text="§6.01 Routing Overview", marker="",
        parent=SimpleNamespace(cref="#/groups/1"), content_layer="body",
        prov=_layout_provenance(1, left=70, top=580))
    actual_section = SimpleNamespace(
        self_ref="#/texts/4", label="section_header",
        text="§6.01 Routing Overview", content_layer="body",
        prov=_layout_provenance(2, left=70, top=600))
    introduction = SimpleNamespace(
        self_ref="#/texts/5", label="text",
        text="This chapter maps signals", content_layer="body",
        prov=_layout_provenance(1, left=70, top=540))
    continuation = SimpleNamespace(
        self_ref="#/texts/6", label="text",
        text="through a page break.", content_layer="body",
        prov=_layout_provenance(2, left=70, top=680))
    section_body = SimpleNamespace(
        self_ref="#/texts/7", label="text",
        text="This section explains fallback relays.",
        content_layer="body",
        prov=_layout_provenance(2, left=70, top=550))
    document = SimpleNamespace(
        texts=[
            chapter, subject, summary, outline, introduction, continuation,
            actual_section, section_body,
        ],
        pictures=[], tables=[], key_value_items=[], form_items=[])
    raw_chunks = [
        SimpleNamespace(
            text="flattened summary",
            meta=SimpleNamespace(
                headings=[chapter_title, "Summary of Contents"],
                doc_items=[outline],
            ),
        ),
        SimpleNamespace(
            text=f"{introduction.text} {continuation.text}",
            meta=SimpleNamespace(
                headings=[
                    chapter_title,
                    "Summary of Contents",
                    actual_section.text,
                ],
                doc_items=[introduction, continuation],
            ),
        ),
        SimpleNamespace(
            text=section_body.text,
            meta=SimpleNamespace(
                headings=[chapter_title, actual_section.text],
                doc_items=[section_body],
            ),
        ),
    ]
    scaffold = [
        {"title": chapter_title, "level": 1, "page": 31},
        {"title": actual_section.text, "level": 2, "page": 32},
    ]

    prepared = rag._prepare_source_preserving_chunks(
        raw_chunks, document, lambda text: len(text.split()), 100,
        scaffold=scaffold)
    by_text = {text: headings for text, headings, _, _ in prepared}

    assert by_text["- §6.01 Routing Overview"] == [
        chapter_title, "Summary of Contents"]
    introduction_text = f"{introduction.text} {continuation.text}"
    assert by_text[introduction_text] == [chapter_title]
    assert by_text[section_body.text] == [
        chapter_title, actual_section.text]
    assert subject.text not in by_text[introduction_text]
    assert summary.text not in by_text[introduction_text]


def test_chapter_summary_escapes_every_ordered_looking_bullet_label():
    chapter = SimpleNamespace(
        self_ref="#/texts/0", label="section_header", text="Chapter 8 Tests",
        prov=_layout_provenance(1, left=70, top=700))
    summary = SimpleNamespace(
        self_ref="#/texts/1", label="section_header",
        text="Summary of Contents",
        prov=_layout_provenance(1, left=70, top=650))
    outline = SimpleNamespace(
        self_ref="#/texts/2", label="code",
        text=(
            "§8.01 Opening A. Upper 1. Numeric a. Lower iv. Roman "
            "B. Another 2. Second §8.02 Closing"),
        prov=_layout_provenance(1, left=70, top=600))
    actual_section = SimpleNamespace(
        self_ref="#/texts/3", label="section_header",
        text="§8.01 Opening",
        prov=_layout_provenance(2, left=70, top=700))
    document = SimpleNamespace(
        texts=[chapter, summary, outline, actual_section], tables=[])

    recovered = rag._recover_chapter_summary_lists(
        document, lambda text: len(text.split()), 100)

    assert len(recovered) == 1
    assert recovered[0].markdown == (
        "- §8.01 Opening\n"
        "  - A\\. Upper\n"
        "    - 1\\. Numeric\n"
        "      - a\\. Lower\n"
        "      - iv\\. Roman\n"
        "  - B\\. Another\n"
        "    - 2\\. Second\n"
        "- §8.02 Closing")


def test_source_markdown_normalization_preserves_repeated_list_rows_only():
    outline = (
        "- §1.08 Release Workflow\n"
        "  - B. Intake Checks\n"
        "    - 3. Retry Queue\n"
        "  - C. Deployment Procedure\n"
        "    - 1. Validation\n"
        "    - 2. Packaging\n"
        "    - 3. Retry Queue"
    )

    normalized = rag._normalize_source_chunk_text(
        outline, "body", preserve_source_identity=True)

    assert normalized.count("    - 3. Retry Queue") == 2
    assert rag._normalize_source_chunk_text(
        normalized, "body", preserve_source_identity=True) == normalized
    assert rag._normalize_source_chunk_text(
        "Repeated prose.\nRepeated prose.", "body",
        preserve_source_identity=True) == "Repeated prose."


def test_source_normalization_preserves_only_distinct_ref_attested_duplicates():
    first_date = SimpleNamespace(
        self_ref="#/texts/0", label="text", text="Date:", orig="")
    first_signature = SimpleNamespace(
        self_ref="#/texts/1", label="text", text="Signature", orig="")
    second_date = SimpleNamespace(
        self_ref="#/texts/2", label="text", text="Date:", orig="")
    second_signature = SimpleNamespace(
        self_ref="#/texts/3", label="text", text="Signature", orig="")
    form_text = "Date:\nSignature\nDate:\nSignature"

    assert rag._normalize_source_chunk_text(
        form_text, "body", preserve_source_identity=True,
        source_items=[
            first_date, first_signature, second_date, second_signature,
        ],
    ) == form_text
    assert rag._normalize_source_chunk_text(
        "Date:\nDate:", "body", preserve_source_identity=True,
        source_items=[first_date, first_date],
    ) == "Date:"

    first_heading = SimpleNamespace(
        self_ref="#/texts/4", label="section_header",
        text="Introduction", orig="")
    second_heading = SimpleNamespace(
        self_ref="#/texts/5", label="section_header",
        text="Introduction", orig="")
    assert rag._normalize_source_chunk_text(
        "Introduction\nIntroduction", "body",
        preserve_source_identity=True,
        source_items=[first_heading, second_heading],
    ) == "Introduction"


def test_canonical_native_rebuild_authorizes_only_its_duplicate_lines():
    item = SimpleNamespace(
        self_ref="#/texts/20", label=SimpleNamespace(value="text"),
        text="corrupt source", orig="")
    canonical = "Opening\nRev.\nFirst\nSecond\nRev.\nClosing"

    assert rag._normalize_source_chunk_text(
        canonical, "body", preserve_source_identity=True,
        source_items=[item],
        canonical_text_overrides={item.self_ref: canonical},
        text_rebuild_refs={item.self_ref},
    ) == canonical
    assert rag._normalize_source_chunk_text(
        canonical, "body", preserve_source_identity=True,
        source_items=[item],
        canonical_text_overrides={item.self_ref: canonical},
    ).count("Rev.") == 1

    invented = f"Invented\n{canonical}"
    assert rag._normalize_source_chunk_text(
        invented, "body", preserve_source_identity=True,
        source_items=[item],
        canonical_text_overrides={item.self_ref: canonical},
        text_rebuild_refs={item.self_ref},
    ).count("Rev.") == 1
    heading = SimpleNamespace(
        self_ref="#/texts/21", label="section_header",
        text="Introduction", orig="")
    assert rag._normalize_source_chunk_text(
        "Introduction\nIntroduction", "body",
        preserve_source_identity=True, source_items=[heading],
        canonical_text_overrides={
            heading.self_ref: "Introduction\nIntroduction"},
        text_rebuild_refs={heading.self_ref},
    ) == "Introduction"


def test_one_line_native_oracle_authorizes_split_duplicate_lines():
    item = SimpleNamespace(
        self_ref="#/texts/22", label=SimpleNamespace(value="text"),
        text="corrupt source", orig="")
    canonical = "First Sys. Rep. 37; second Sys. Rep. 857."
    split_fragment = "First Sys.\nRep.\n37; second Sys.\nRep.\n857."

    assert rag._normalize_source_chunk_text(
        split_fragment, "body", preserve_source_identity=True,
        source_items=[item],
        canonical_text_overrides={item.self_ref: canonical},
        text_rebuild_refs={item.self_ref},
    ) == split_fragment


def test_figure_normalization_preserves_attested_curly_apostrophes():
    source = "Figure: π’s calibration and operator’s note"

    assert rag._normalize_source_chunk_text(
        source, "figure", preserve_source_identity=True) == source
    assert rag._normalize_source_chunk_text(
        source, "body", preserve_source_identity=True) == (
            "Figure: π's calibration and operator's note")


def test_chapter_summary_recovers_flat_code_outline_and_trailing_marker():
    chapter = SimpleNamespace(
        self_ref="#/texts/0", label="section_header",
        text="Chapter 3 Field Data Handbook",
        prov=_layout_provenance(1, left=70, top=655))
    summary = SimpleNamespace(
        self_ref="#/texts/1", label="section_header",
        text="Summary of Contents",
        prov=_layout_provenance(1, left=70, top=390))
    code = SimpleNamespace(
        self_ref="#/texts/2", label="code",
        text=(
            "§3.01 Collecting Measurements "
            "A. Scheduled Sampling "
            "1. Morning Sweep "
            "2. Evening Sweep "
            "B. Event-Driven Sampling "
            "1. Threshold Alerts "
            "2. Manual Requests "
            "3. Recovery Checks "
            "§ 3.02 Preparing Records "
            "A. Validation "
            "1. Required Fields "
            "a. Device Identifier "
            "b. Capture Time "
            "2. Deduplication "
            "B. Export "
            "1. Signed Bundle "
            "§3.03 Closing the Run "
            "A. Operator Checklist"
        ),
        prov=_layout_provenance(1, left=70, top=367, bottom=99))
    practice = SimpleNamespace(
        self_ref="#/texts/3", label="list_item",
        text="Archive Practice", marker="B.",
        parent=SimpleNamespace(cref="#/groups/1"),
        prov=_layout_provenance(1, left=98, top=97))
    actual_section = SimpleNamespace(
        self_ref="#/texts/4", label="section_header",
        text="§3.01 Collecting Measurements",
        prov=_layout_provenance(2, left=70, top=650))
    document = SimpleNamespace(
        texts=[chapter, summary, code, practice, actual_section], tables=[])

    recovered = rag._recover_chapter_summary_lists(
        document, lambda text: len(text.split()), 500)

    assert len(recovered) == 1
    assert len(recovered[0].markdown.splitlines()) == 19
    assert recovered[0].markdown.startswith(
        "- §3.01 Collecting Measurements\n"
        "  - A\\. Scheduled Sampling\n"
        "    - 1\\. Morning Sweep")
    assert recovered[0].markdown.endswith(
        "- §3.03 Closing the Run\n"
        "  - A\\. Operator Checklist\n"
        "  - B\\. Archive Practice")
    assert recovered[0].items == (code, practice)


def test_chapter_summary_splits_only_verified_missing_marker_sequences():
    group = SimpleNamespace(cref="#/groups/1")
    alpha = SimpleNamespace(
        label="list_item", marker="A.", parent=group,
        text="Paired Sensors B. Failover Relay")
    charlie = SimpleNamespace(
        label="list_item", marker="C.", parent=group,
        text="Cross-Zone Routing")
    numeric = SimpleNamespace(
        label="list_item", marker="3.", parent=group,
        text=(
            "Exploratory Modes for Sparse Telemetry "
            "§6.06 Offline Buffer Rules"))
    next_section = SimpleNamespace(
        label="list_item", marker="", parent=group,
        text="§6.07 Recovery Audit")
    other_parent = SimpleNamespace(
        label="list_item", marker="C.",
        parent=SimpleNamespace(cref="#/groups/2"), text="Other")

    assert rag._split_verified_chapter_summary_item(
        alpha, charlie, 7) == [
            (1, "A. Paired Sensors"),
            (1, "B. Failover Relay"),
        ]
    assert rag._split_verified_chapter_summary_item(
        numeric, next_section, 6) == [
            (2, "3. Exploratory Modes for Sparse Telemetry"),
            (0, "§6.06 Offline Buffer Rules"),
        ]
    assert rag._split_verified_chapter_summary_item(
        alpha, other_parent, 7) == []


def test_native_document_index_recovers_full_chapter_summary(tmp_path):
    import pymupdf

    authored = [
        (0, "§10.01 Input Acquisition"),
        (1, "A. Sensor Inventory"),
        (2, "1. Fixed Sensors"),
        (2, "2. Portable Sensors"),
        (1, "B. Sampling Plans"),
        (2, "1. Periodic Capture"),
        (2, "2. Event Capture"),
        (3, "a. Threshold Trigger"),
        (3, "b. Manual Trigger"),
        (1, "C. Time Alignment"),
        (2, "1. Clock Sources"),
        (2, "2. Drift Windows"),
        (0, "§10.02 Record Validation"),
        (1, "A. Required Fields"),
        (2, "1. Device Identifier"),
        (2, "2. Capture Time"),
        (2, "3. Sequence Number"),
        (1, "B. Consistency Checks"),
        (2, "1. Range Validation"),
        (3, "a. Warning Range"),
        (3, "b. Rejection Range"),
        (2, "2. Duplicate Detection"),
        (1, "C. Review Queues"),
        (2, "1. Automatic Retry"),
        (2, "2. Operator Escalation"),
        (0, "§10.03 Export Packaging"),
        (1, "A. Signed Manifest"),
        (1, "B. Archive Bundle"),
        (2, "1. Local Copy"),
        (2, "2. Remote Copy"),
        (0, "§10.04 Closing the Run"),
    ]
    pdf_path = tmp_path / "chapter-summary.pdf"
    pdf = pymupdf.open()
    page = pdf.new_page(width=504, height=738)
    left_by_depth = {0: 72, 1: 98, 2: 114, 3: 126}
    for index, (depth, text) in enumerate(authored):
        page.insert_text(
            (left_by_depth[depth], 222 + index * 13), text, fontsize=8)
    pdf.save(pdf_path)
    pdf.close()

    chapter = SimpleNamespace(
        self_ref="#/texts/0", label="section_header",
        text="Chapter 10 Telemetry Operations",
        prov=_layout_provenance(1, left=68, top=657, bottom=592))
    summary = SimpleNamespace(
        self_ref="#/texts/1", label="section_header",
        text="Summary of Contents",
        prov=_layout_provenance(1, left=70, top=543, bottom=527))
    cells = [
        SimpleNamespace(
            text="§10.01 Input Acquisition",
            start_row_offset_idx=0, start_col_offset_idx=0),
        SimpleNamespace(
            text="§10.02 Record Validation",
            start_row_offset_idx=1, start_col_offset_idx=0),
    ]
    table = SimpleNamespace(
        self_ref="#/tables/0", label="document_index",
        parent=SimpleNamespace(cref="#/body"),
        prov=_layout_provenance(
            1, left=70, top=523, right=357, bottom=78),
        data=SimpleNamespace(
            num_rows=27, num_cols=1, table_cells=cells),
    )
    document = SimpleNamespace(texts=[chapter, summary], tables=[table])

    overrides = rag._recover_incomplete_table_markdown(document, pdf_path)
    recovered = rag._recover_chapter_summary_lists(
        document, lambda text: len(text.split()), 500,
        table_markdown_overrides=overrides)

    assert len(overrides[table.self_ref].splitlines()) == 31
    assert "    - 1. Clock Sources" in overrides[table.self_ref]
    assert " = " not in overrides[table.self_ref]
    assert len(recovered) == 1
    assert "    - 1\\. Clock Sources" in recovered[0].markdown
    assert recovered[0].markdown.replace("\\.", ".") == (
        overrides[table.self_ref])
    assert recovered[0].items == (table,)


def test_untriggered_chapter_summary_is_emitted_once_in_source_position():
    chapter_title = "Chapter 10 · Telemetry Operations"
    chapter = SimpleNamespace(
        self_ref="#/texts/0", label="section_header",
        text="Chapter 10 Telemetry Operations", content_layer="body",
        prov=_layout_provenance(1, left=70, top=700))
    epigraph = SimpleNamespace(
        self_ref="#/texts/1", label="text", text="Opening epigraph.",
        content_layer="body",
        prov=_layout_provenance(1, left=70, top=650))
    summary = SimpleNamespace(
        self_ref="#/texts/2", label="section_header",
        text="Summary of Contents", content_layer="body",
        prov=_layout_provenance(1, left=70, top=600))
    outline_cell = SimpleNamespace(
        text=(
            "§10.01 Intake Pipeline "
            "A. Sensor Sources "
            "1. Fixed Nodes "
            "2. Mobile Nodes "
            "B. Capture Windows "
            "1. Scheduled Runs "
            "a. Morning Cycle "
            "§10.02 Validation Queue"
        ),
        start_row_offset_idx=0,
        start_col_offset_idx=0,
    )
    document_index = SimpleNamespace(
        self_ref="#/tables/40", label="document_index", content_layer="body",
        prov=_layout_provenance(
            1, left=70, top=580, right=450, bottom=300),
        data=SimpleNamespace(
            num_rows=1, num_cols=1, table_cells=[outline_cell]),
    )
    actual_section = SimpleNamespace(
        self_ref="#/texts/3", label="section_header",
        text="§10.01 Intake Pipeline", content_layer="body",
        prov=_layout_provenance(2, left=70, top=680))
    introduction = SimpleNamespace(
        self_ref="#/texts/4", label="text", text="The chapter begins.",
        content_layer="body",
        prov=_layout_provenance(2, left=70, top=630))
    document = SimpleNamespace(
        texts=[chapter, epigraph, summary, actual_section, introduction],
        tables=[document_index], pictures=[], key_value_items=[],
        form_items=[])
    raw_chunks = [
        SimpleNamespace(
            text=epigraph.text,
            meta=SimpleNamespace(
                headings=[chapter_title], doc_items=[epigraph])),
        SimpleNamespace(
            text=introduction.text,
            meta=SimpleNamespace(
                headings=[
                    chapter_title,
                    "Summary of Contents",
                    actual_section.text,
                ],
                doc_items=[introduction],
            ),
        ),
    ]
    scaffold = [
        {"title": chapter_title, "level": 1, "page": 81},
        {"title": actual_section.text, "level": 2, "page": 82},
    ]

    prepared = rag._prepare_source_preserving_chunks(
        raw_chunks, document, lambda text: len(text.split()), 200,
        scaffold=scaffold)

    assert [entry[0] for entry in prepared] == [
        epigraph.text,
        (
            "- §10.01 Intake Pipeline\n"
            "  - A\\. Sensor Sources\n"
            "    - 1\\. Fixed Nodes\n"
            "    - 2\\. Mobile Nodes\n"
            "  - B\\. Capture Windows\n"
            "    - 1\\. Scheduled Runs\n"
            "      - a\\. Morning Cycle\n"
            "- §10.02 Validation Queue"
        ),
        introduction.text,
    ]
    summary_entries = [
        entry for entry in prepared
        if document_index in (entry[2] or [])
    ]
    assert len(summary_entries) == 1
    assert summary_entries[0][1] == [chapter_title, "Summary of Contents"]
    assert summary_entries[0][2] == [document_index]
    assert prepared[-1][1] == [chapter_title, actual_section.text]


def test_triggered_document_index_summary_gets_canonical_chapter_heading():
    chapter_title = "Chapter 10 · Telemetry Operations"
    chapter = SimpleNamespace(
        self_ref="#/texts/0", label="section_header",
        text="Chapter 10 Telemetry Operations", content_layer="body",
        prov=_layout_provenance(1, left=70, top=700))
    summary = SimpleNamespace(
        self_ref="#/texts/1", label="section_header",
        text="Summary of Contents", content_layer="body",
        prov=_layout_provenance(1, left=70, top=600))
    outline_cell = SimpleNamespace(
        text=(
            "§10.01 Intake Pipeline "
            "A. Sensor Sources "
            "1. Fixed Nodes "
            "2. Mobile Nodes "
            "B. Capture Windows "
            "1. Scheduled Runs "
            "a. Morning Cycle "
            "§10.02 Validation Queue"
        ),
        start_row_offset_idx=0,
        start_col_offset_idx=0,
    )
    document_index = SimpleNamespace(
        self_ref="#/tables/40", label="document_index",
        parent=SimpleNamespace(cref="#/body"), content_layer="body",
        prov=_layout_provenance(
            1, left=70, top=580, right=450, bottom=300),
        data=SimpleNamespace(
            num_rows=1, num_cols=1, table_cells=[outline_cell]),
    )
    actual_section = SimpleNamespace(
        self_ref="#/texts/2", label="section_header",
        text="§10.01 Intake Pipeline", content_layer="body",
        prov=_layout_provenance(2, left=70, top=680))
    document = SimpleNamespace(
        texts=[chapter, summary, actual_section], tables=[document_index],
        pictures=[], key_value_items=[], form_items=[])
    raw_chunks = [SimpleNamespace(
        text="flattened document index",
        meta=SimpleNamespace(
            headings=["Summary of Contents"], doc_items=[document_index]),
    )]

    prepared = rag._prepare_source_preserving_chunks(
        raw_chunks, document, lambda text: len(text.split()), 200,
        scaffold=[
            {"title": chapter_title, "level": 1, "page": 81},
            {"title": actual_section.text, "level": 2, "page": 82},
        ])

    summary_entries = [
        entry for entry in prepared if document_index in (entry[2] or [])]
    assert len(summary_entries) == 1
    assert summary_entries[0][1] == [chapter_title, "Summary of Contents"]


def test_one_line_native_heading_order_repairs_exact_token_permutation():
    source = "Calibration Review and Approval 4."
    native = "4. Calibration Review and Approval"
    words = [
        (index * 30, 0, index * 30 + 25, 10, word, 1, 2, index)
        for index, word in enumerate(native.split())
    ]

    assert rag._repair_one_line_native_heading_order(
        source, native, words,
        is_section_header=True,
        provenance_count=1,
        has_provenance_overlap=False,
    ) == native


def test_one_line_native_heading_order_fails_closed_on_ambiguous_geometry():
    source = "Calibration Review and Approval 4."
    native = "4. Calibration Review and Approval"
    multiline = [
        (index * 30, 0, index * 30 + 25, 10, word, 1, index // 2, index)
        for index, word in enumerate(native.split())
    ]
    mismatch = "5. Calibration Review and Approval"

    assert rag._repair_one_line_native_heading_order(
        source, native, multiline,
        is_section_header=True,
        provenance_count=1,
        has_provenance_overlap=False,
    ) == source
    assert rag._repair_one_line_native_heading_order(
        source, mismatch, multiline[:1] + [
            (*word[:6], 0, word[7]) for word in multiline[1:]
        ],
        is_section_header=True,
        provenance_count=1,
        has_provenance_overlap=False,
    ) == source
    assert rag._repair_one_line_native_heading_order(
        source, native, [
            (*word[:6], 0, word[7]) for word in multiline
        ],
        is_section_header=False,
        provenance_count=1,
        has_provenance_overlap=False,
    ) == source

    def one_line_words(value):
        return [
            (index * 30, 0, index * 30 + 25, 10, word, 1, 0, index)
            for index, word in enumerate(value.split())
        ]

    arbitrary_source = "Signal Quality and Buffer Capacity"
    arbitrary_native = "Buffer Capacity and Signal Quality"
    assert rag._repair_one_line_native_heading_order(
        arbitrary_source, arbitrary_native, one_line_words(arbitrary_native),
        is_section_header=True,
        provenance_count=1,
        has_provenance_overlap=False,
    ) == arbitrary_source

    symbol_source = "§ 4. Input and Output"
    symbol_native = "4. Output and Input"
    assert rag._repair_one_line_native_heading_order(
        symbol_source, symbol_native, one_line_words(symbol_native),
        is_section_header=True,
        provenance_count=1,
        has_provenance_overlap=False,
    ) == symbol_source


def test_chunk_heading_repairs_require_ref_binding_or_unanimous_occurrences():
    source = "Calibration Review and Approval 4."
    first = SimpleNamespace(
        self_ref="#/texts/1", label="section_header", text=source)
    second = SimpleNamespace(
        self_ref="#/texts/2", label="section_header", text=source)
    repaired = "4. Calibration Review and Approval"

    assert rag._repair_source_bound_chunk_headings(
        [source], [first], repairs_by_ref={first.self_ref: repaired},
        unanimous_repairs={}) == [repaired]
    assert rag._repair_source_bound_chunk_headings(
        [source], [second], repairs_by_ref={first.self_ref: repaired},
        unanimous_repairs={}) == [source]
    assert rag._repair_source_bound_chunk_headings(
        [source], [], repairs_by_ref={first.self_ref: repaired},
        unanimous_repairs={}) == [source]
    assert rag._repair_source_bound_chunk_headings(
        [source], [], repairs_by_ref={first.self_ref: repaired},
        unanimous_repairs={source: repaired}) == [repaired]


def test_scaffold_reconciliation_rejects_previous_chapter_section_scope():
    chapter = "Chapter 3 · Signal Intake"

    epigraph = rag._reconcile_scaffold_path_with_source_headings(
        chapter, 1,
        [chapter, "§2.09 Combining Validation Results",
         "Accomplishment Note"],
        structure_profile="us-law-casebook-v1")
    summary = rag._reconcile_scaffold_path_with_source_headings(
        chapter, 1,
        [chapter, "§2.09 Combining Validation Results",
         "Summary of Contents"],
        structure_profile="us-law-casebook-v1")

    assert epigraph == chapter
    assert summary == f"{chapter} > Summary of Contents"


def test_unattested_page_summary_leaf_is_transient_for_intro_prose():
    path = "Chapter 6 · Signal Routing > Summary of Contents"

    assert rag._drop_unattested_transient_summary_leaf(
        path, []) == "Chapter 6 · Signal Routing"
    assert rag._drop_unattested_transient_summary_leaf(
        path, ["Summary of Contents"]) == path
    assert rag._drop_unattested_transient_summary_leaf(
        "Chapter 6 · Signal Routing > §6.01 Routing Overview", [],
    ) == "Chapter 6 · Signal Routing > §6.01 Routing Overview"


def test_contiguous_case_run_keeps_caption_bound_ancestor_prefix():
    case = "Orion Devices v. Delta Labs"
    records = [
        {"metadata": {
            "section_path": (
                f"Chapter 2 · System Verification > §2.05 Field Practice > "
                f"Notes & Questions > {case}"
            ),
            "headings": ["§2.05 Field Practice", "Notes & Questions", case],
        }},
        {"metadata": {
            "section_path": (
                f"Chapter 2 · System Verification > §2.05 Field Practice > "
                f"{case} > HARPER, Judge"
            ),
            "headings": ["§2.05 Field Practice", case, "HARPER, Judge"],
        }},
        {"metadata": {
            "section_path": (
                "Chapter 2 · System Verification > §2.06 Reference Protocols"),
            "headings": ["§2.06 Reference Protocols"],
        }},
    ]

    assert rag._stabilize_contiguous_case_ancestor_paths(records) == 1
    assert records[1]["metadata"]["section_path"] == (
        "Chapter 2 · System Verification > §2.05 Field Practice > "
        f"Notes & Questions > {case} > HARPER, Judge"
    )
    assert records[1]["metadata"]["headings"] == [
        "§2.05 Field Practice", "Notes & Questions", case, "HARPER, Judge"]
    assert rag._stabilize_contiguous_case_ancestor_paths(records) == 0


def _prepare_heading_timeline(
        entries, *, scaffold=None,
        fallback_chapter="Chapter 8 · Operational Disputes"):
    """Build source-mapped headings for compact hierarchy regressions."""
    items = []
    raw_chunks = []
    for index, (label, text) in enumerate(entries):
        item = SimpleNamespace(
            self_ref=f"#/texts/{index}", label=label, text=text,
            content_layer="body",
            prov=_layout_provenance(
                10 + index // 30, left=90,
                top=700 - (index % 30) * 18,
                right=500, bottom=688 - (index % 30) * 18),
        )
        items.append(item)
        if label != "section_header":
            raw_chunks.append(SimpleNamespace(
                text=text,
                meta=SimpleNamespace(
                    headings=[fallback_chapter],
                    doc_items=[item],
                ),
            ))
    document = SimpleNamespace(
        texts=items, pictures=[], tables=[], key_value_items=[],
        form_items=[])
    return rag._prepare_source_preserving_chunks(
        raw_chunks, document, lambda text: len(text.split()), 200,
        scaffold=scaffold or [])


def test_heading_timeline_uses_toc_attested_sibling_after_body():
    chapter = "Chapter 3 Signal Intake"
    section = "§3.02 Limited Intake Rules"
    second = "2. To Monitor Risks Reported by Remote Nodes"
    child = "Regional Alert Logs as a Source of Signals"
    third = "3. To Quarantine Corrupt Packets"
    entries = [
        ("section_header", chapter),
        ("section_header", section),
        ("section_header", second),
        ("text", "Discussion under the second subsection."),
        ("section_header", child),
        ("text", "Discussion of regional alert logs."),
        ("section_header", third),
        ("text", "Discussion under the third subsection."),
    ]
    scaffold = [
        {"title": chapter, "level": 1},
        {"title": section, "level": 2},
        {"title": second, "level": 3},
        {"title": child, "level": 3},
        {"title": third, "level": 3},
    ]

    canonical_chapter = "Chapter 3 · Signal Intake"
    prepared = _prepare_heading_timeline(
        entries, scaffold=scaffold, fallback_chapter=canonical_chapter)
    headings = next(
        values for text, values, _, _ in prepared
        if text == "Discussion under the third subsection.")

    assert headings == [
        canonical_chapter, section, third]
    assert child not in headings
    assert second not in headings


def test_heading_timeline_keeps_peer_panels_and_case_scopes_separate():
    entries = [
        ("section_header", "Chapter 8 Operational Disputes"),
        ("section_header", "§8.04 Short Problems on Commands And Alerts"),
        ("section_header", "COMMAND PROBLEMS"),
        ("text", "Command panel discussion."),
        ("section_header", "E. Command Source"),
        ("section_header", "4. To a Device"),
        ("text", "Command element discussion."),
        ("section_header", "ALERT PROBLEMS"),
        ("text", "Alert panel discussion."),
        ("section_header", "§8.09 Overrides and Exceptions"),
        ("section_header", "A. Operator Consent"),
        ("section_header", "Orion Devices v. Delta Labs"),
        ("text", "Orion opening discussion."),
        ("section_header", "W. QUILL, Judge"),
        ("text", "Orion opinion discussion."),
        ("section_header", "Beacon Works v. Northstar Systems"),
        ("text", "Beacon opening discussion."),
        ("section_header", "MORROW, Judge"),
        ("text", "Beacon opinion discussion."),
        ("section_header", "Cascade Robotics v. Vale Instruments"),
        ("text", "Cascade opening discussion."),
        ("section_header", "I. Background"),
        ("text", "Cascade background discussion."),
        ("section_header", "B. Emergency Override"),
        ("text", "The next global subsection begins here."),
    ]

    prepared = _prepare_heading_timeline(entries)
    by_text = {text: headings for text, headings, _, _ in prepared}

    assert by_text["Alert panel discussion."] == [
        "Chapter 8 · Operational Disputes",
        "§8.04 Short Problems on Commands And Alerts",
        "ALERT PROBLEMS",
    ]
    assert by_text["Orion opinion discussion."][-3:] == [
        "A. Operator Consent", "Orion Devices v. Delta Labs",
        "W. QUILL, Judge"]
    assert by_text["Beacon opening discussion."][-2:] == [
        "A. Operator Consent", "Beacon Works v. Northstar Systems"]
    assert "W. QUILL, Judge" not in by_text[
        "Beacon opening discussion."]
    assert by_text["Cascade background discussion."][-3:] == [
        "A. Operator Consent", "Cascade Robotics v. Vale Instruments",
        "I. Background"]
    assert "Beacon Works v. Northstar Systems" not in by_text[
        "Cascade background discussion."]
    assert by_text["The next global subsection begins here."][-1] == (
        "B. Emergency Override")
    assert "Cascade Robotics v. Vale Instruments" not in by_text[
        "The next global subsection begins here."]


def test_heading_timeline_replaces_statute_panel_with_following_case():
    entries = [
        ("section_header", "Chapter 6 Recovery Records"),
        ("section_header", "§6.07 Failed Export"),
        ("section_header", "Redwood Device Recovery Regulation"),
        ("text", "The regulation excerpt appears here."),
        ("section_header", "Beacon Systems v. Alder Labs"),
        ("text", "The Beacon decision begins here."),
        ("section_header", "RICHMOND, Justice"),
        ("text", "The Beacon opinion continues here."),
        ("section_header", "1. The Recovery Decision"),
        ("text", "The decision analysis continues here."),
    ]

    prepared = _prepare_heading_timeline(
        entries, fallback_chapter="Chapter 6 · Recovery Records")
    by_text = {text: headings for text, headings, _, _ in prepared}

    assert by_text["The Beacon decision begins here."][-1] == (
        "Beacon Systems v. Alder Labs")
    assert "Redwood Device Recovery Regulation" not in by_text[
        "The Beacon decision begins here."]
    assert by_text["The decision analysis continues here."][-2:] == [
        "Beacon Systems v. Alder Labs", "1. The Recovery Decision"]


def test_heading_timeline_treats_problem_and_disaster_banners_as_peers():
    entries = [
        ("section_header", "Chapter 5 Failure Scope"),
        ("section_header", "§5.05 Combining Failure Analysis"),
        ("section_header", "COOLANT LEAK PROBLEM"),
        ("text", "The coolant-leak problem appears here."),
        ("section_header", "CLOCK CASCADE DISASTER"),
        ("text", "The clock-cascade scenario appears here."),
    ]

    prepared = _prepare_heading_timeline(
        entries, fallback_chapter="Chapter 5 · Failure Scope")
    by_text = {text: headings for text, headings, _, _ in prepared}

    assert by_text["The clock-cascade scenario appears here."] == [
        "Chapter 5 · Failure Scope",
        "§5.05 Combining Failure Analysis",
        "CLOCK CASCADE DISASTER",
    ]
    assert "COOLANT LEAK PROBLEM" not in by_text[
        "The clock-cascade scenario appears here."]


@pytest.mark.parametrize(
    "heading",
    [
        "R. QUILL, Circuit Judge.",
        "MORROW, Chief Justice.",
        "PAXTON J.",
        "NOLAN, Chief Judge.",
        "IVEN, District Judge.",
        "SOREL, District Judge.",
        "R. QUILL,J.",
    ],
)
def test_casebook_judicial_attribution_recognizes_title_variants(heading):
    assert rag._is_casebook_judicial_attribution(heading)


def test_casebook_case_heading_rejects_notes_title_and_accepts_caption():
    assert not rag._is_casebook_case_heading(
        "Notes & Questions on Orion Devices v. Delta Labs")
    assert rag._is_casebook_case_heading(
        "Orion Devices v. Delta Labs")
    assert rag._is_casebook_case_heading(
        "The Northern Relay", following_text="42 F.2d 314 (4th Cir. 1951)",
        following_heading="R. QUILL, Circuit Judge.")
    assert not rag._is_casebook_case_heading(
        "Untested Inputs, Morgan Vale",
        following_text="18 J. SYS. STUD. 139 (2022)",
        following_heading="Notes & Questions")


def test_heading_timeline_uses_citation_attested_case_caption_and_byline():
    entries = [
        ("section_header", "Chapter 2 System Verification"),
        ("section_header", "§2.05 Developing the Field Check Standard"),
        ("section_header", "A. Balancing Throughput and Latency"),
        ("section_header", "The Northern Relay"),
        ("text", "42 F.2d 314 (4th Cir. 1951)"),
        ("text", "The relay facts appear here."),
        ("section_header", "R. QUILL, Circuit Judge."),
        ("text", "The relay opinion appears here."),
        ("section_header", "Orion Devices v. Delta Labs"),
        ("text", "The next case begins here."),
    ]

    prepared = _prepare_heading_timeline(
        entries, fallback_chapter="Chapter 2 · System Verification")
    by_text = {text: headings for text, headings, _, _ in prepared}

    assert by_text["The relay opinion appears here."][-3:] == [
        "A. Balancing Throughput and Latency",
        "The Northern Relay",
        "R. QUILL, Circuit Judge",
    ]
    assert by_text["The next case begins here."][-2:] == [
        "A. Balancing Throughput and Latency", "Orion Devices v. Delta Labs"]
    assert "R. QUILL, Circuit Judge" not in by_text[
        "The next case begins here."]


def test_heading_timeline_keeps_unbound_judge_byline_transient():
    entries = [
        ("section_header", "Chapter 2 System Verification"),
        ("section_header", "§2.05 Developing the Field Check Standard"),
        ("section_header", "A. Balancing Throughput and Latency"),
        ("section_header", "R. QUILL,J."),
        ("text", "The review explanation appears here."),
        ("section_header", "B. Field Practice"),
        ("text", "The next global subsection begins here."),
    ]

    prepared = _prepare_heading_timeline(
        entries, fallback_chapter="Chapter 2 · System Verification")
    by_text = {text: headings for text, headings, _, _ in prepared}

    assert by_text["The review explanation appears here."][-2:] == [
        "A. Balancing Throughput and Latency", "R. QUILL,J"]
    assert by_text["The next global subsection begins here."][-1] == (
        "B. Field Practice")
    assert "R. QUILL,J" not in by_text[
        "The next global subsection begins here."]


def test_geometry_gated_false_heading_is_emitted_as_body_once():
    chapter = SimpleNamespace(
        self_ref="#/texts/0", label="section_header",
        text="Chapter 5 Failure Scope", content_layer="body",
        prov=_layout_provenance(10, left=90, top=700, right=500, bottom=680))
    section = SimpleNamespace(
        self_ref="#/texts/1", label="section_header",
        text="§5.04 Verification Problems", content_layer="body",
        prov=_layout_provenance(10, left=90, top=650, right=500, bottom=630))
    packaging = SimpleNamespace(
        self_ref="#/texts/2", label="section_header", content_layer="body",
        text=(
            "PORTABLE SAMPLE MODULE REMAINS READY FOR CONTROLLED LABORATORY "
            "USE WITH SEALED COMPONENTS CLEAR STATUS LIGHTS REUSABLE "
            "PACKAGING SIMPLE STARTUP INSTRUCTIONS AND CONSISTENT PERFORMANCE "
            "ACROSS ROUTINE DEMONSTRATIONS"
        ),
        prov=_layout_provenance(
            10, left=130, top=580, right=370, bottom=476),
    )
    document = SimpleNamespace(
        texts=[chapter, section, packaging], pictures=[], tables=[],
        key_value_items=[], form_items=[])
    raw = SimpleNamespace(
        text=packaging.text,
        meta=SimpleNamespace(
            headings=["Chapter 5 · Failure Scope", packaging.text],
            doc_items=[packaging],
        ),
    )

    prepared = rag._prepare_source_preserving_chunks(
        [raw], document, lambda text: len(text.split()), 200)

    assert [entry[0] for entry in prepared] == [packaging.text]
    assert prepared[0][1] == [
        "Chapter 5 · Failure Scope", "§5.04 Verification Problems"]
    assert prepared[0][2] == [packaging]


def test_consecutive_chapter_heading_absolutely_resets_previous_scope():
    entries = [
        ("section_header", "Chapter 2 Input Validation"),
        ("section_header", "§2.09 Combining Validation Results"),
        ("section_header", "Accomplishment Note"),
        ("section_header", "Chapter 3 Signal Intake"),
        ("text", "The new chapter epigraph appears here."),
        ("section_header", "Summary of Contents"),
        ("text", "The new chapter summary appears here."),
    ]

    prepared = _prepare_heading_timeline(
        entries, fallback_chapter="Chapter 3 · Signal Intake")
    by_text = {text: headings for text, headings, _, _ in prepared}

    assert by_text["The new chapter epigraph appears here."] == [
        "Chapter 3 · Signal Intake"]
    assert by_text["The new chapter summary appears here."] == [
        "Chapter 3 · Signal Intake", "Summary of Contents"]


def test_sequential_lists_are_not_misread_as_parallel_table_columns():
    left_header = SimpleNamespace(
        self_ref="#/texts/0", label="section_header", text="First list",
        prov=_layout_provenance(6, left=90, top=570))
    right_header = SimpleNamespace(
        self_ref="#/texts/1", label="text", text="Second list",
        prov=_layout_provenance(6, left=260, top=420, right=460))
    left = [
        SimpleNamespace(
            self_ref=f"#/texts/{index + 2}", label="list_item", text=value,
            prov=_layout_provenance(6, left=90, top=520 - index * 14))
        for index, value in enumerate(("One", "Two", "Three"))
    ]
    right = [
        SimpleNamespace(
            self_ref=f"#/texts/{index + 5}", label="list_item", text=value,
            prov=_layout_provenance(
                6, left=260, top=370 - index * 14, right=460))
        for index, value in enumerate(("Four", "Five", "Six"))
    ]

    assert rag._detect_aligned_list_tables(SimpleNamespace(
        texts=[left_header, *left, right_header, *right])) == []


def test_cross_page_table_continuation_stitches_aligned_next_page_rows():
    def cell(text, row, col, left):
        return SimpleNamespace(
            text=text,
            start_row_offset_idx=row,
            end_row_offset_idx=row + 1,
            start_col_offset_idx=col,
            end_col_offset_idx=col + 1,
            bbox=SimpleNamespace(l=left, r=left + 100, t=100, b=80),
        )

    class Table:
        self_ref = "#/tables/0"
        label = "table"
        prov = _layout_provenance(
            1, left=50, top=200, right=400, bottom=50)
        data = SimpleNamespace(
                num_cols=2,
                table_cells=[
                    cell("Check name", 0, 0, 50),
                    cell("Expected result", 0, 1, 250),
                ],
        )

        @staticmethod
        def export_to_markdown(*, doc):
            return "| Check name | Expected result |\n|---|---|"

    repeated_left = SimpleNamespace(
        self_ref="#/texts/0", label="section_header", text="Check name",
        prov=_layout_provenance(2, left=50, top=700, right=150))
    repeated_right = SimpleNamespace(
        self_ref="#/texts/1", label="section_header", text="Expected result",
        prov=_layout_provenance(2, left=250, top=700, right=350))
    left = SimpleNamespace(
        self_ref="#/texts/2", label="text", text="Checksum validation.",
        prov=_layout_provenance(2, left=50, top=650, right=150))
    right = SimpleNamespace(
        self_ref="#/texts/3", label="text", text="The packet is accepted.",
        prov=_layout_provenance(2, left=250, top=650, right=350))
    document = SimpleNamespace(
        tables=[Table()],
        texts=[repeated_left, repeated_right, left, right],
        pages={
            1: SimpleNamespace(size=SimpleNamespace(height=736)),
            2: SimpleNamespace(size=SimpleNamespace(height=736)),
        })

    overrides, continuation_refs = (
        rag._recover_cross_page_table_continuations(document, {}))

    assert overrides["#/tables/0"].endswith(
        "| Checksum validation. | The packet is accepted. |")
    assert continuation_refs["#/tables/0"] == [
        "#/texts/0", "#/texts/1", "#/texts/2", "#/texts/3"]

    repeated_left.label = "text"
    repeated_right.label = "text"
    text_header_overrides, text_header_refs = (
        rag._recover_cross_page_table_continuations(document, {}))
    assert text_header_overrides == overrides
    assert text_header_refs == continuation_refs

    mismatched, mismatched_refs = (
        rag._recover_cross_page_table_continuations(document, {
            "#/tables/0": "| Source layout |\n|---|\n| existing row |",
        }))
    assert mismatched == {
        "#/tables/0": "| Source layout |\n|---|\n| existing row |"}
    assert mismatched_refs == {}

    incomplete_header_table = Table()
    incomplete_header_table.prov = _layout_provenance(
        1, left=50, top=240, right=400, bottom=100)
    no_headers = SimpleNamespace(
        tables=[incomplete_header_table], texts=[left, right],
        pages=document.pages)
    missing_headers, missing_header_refs = (
        rag._recover_cross_page_table_continuations(no_headers, {}))
    assert missing_headers == {}
    assert missing_header_refs == {}


def test_picture_caption_is_a_sidecar_and_cannot_interrupt_body_text():
    body_left = SimpleNamespace(
        self_ref="#/texts/0", label="text", text="The operator encoun-",
        prov=_layout_provenance(7, left=80, top=500))
    caption = SimpleNamespace(
        self_ref="#/texts/1", label="caption", text="Relay cabinet diagram",
        prov=_layout_provenance(7, left=350, top=480))
    body_right = SimpleNamespace(
        self_ref="#/texts/2", label="text", text="tered a timing issue.",
        prov=_layout_provenance(7, left=80, top=460))
    picture = SimpleNamespace(
        self_ref="#/pictures/0", label="picture",
        captions=[SimpleNamespace(cref="#/texts/1")], footnotes=[],
        prov=_layout_provenance(7, left=340, top=520, right=460, bottom=400))
    document = SimpleNamespace(
        texts=[body_left, caption, body_right], pictures=[picture], tables=[],
        key_value_items=[], form_items=[])
    raw = SimpleNamespace(
        text="The operator encoun-\nRelay cabinet diagram\ntered a timing issue.",
        meta=SimpleNamespace(
            headings=["Example"],
            doc_items=[body_left, caption, body_right, picture]))

    prepared = rag._prepare_source_preserving_chunks(
        [raw], document, lambda text: len(text.split()), 100,
        structural_ranges=set())

    assert len(prepared) == 2
    body = next(text for text, _, items, _ in prepared
                if picture not in (items or []))
    figure = next(text for text, _, items, _ in prepared
                  if picture in (items or []))
    assert rag._normalize_text(body) == "The operator encountered a timing issue."
    assert figure == "Figure: Relay cabinet diagram"


def test_large_captionless_picture_gets_nonlexical_source_carrier():
    picture = SimpleNamespace(
        self_ref="#/pictures/0", label="picture", captions=[], children=[],
        footnotes=[],
        prov=_layout_provenance(
            7, left=100, top=600, right=400, bottom=250),
    )
    document = SimpleNamespace(
        texts=[], pictures=[picture], tables=[], key_value_items=[],
        form_items=[],
        pages={
            7: SimpleNamespace(size=SimpleNamespace(
                width=500, height=700)),
        },
    )

    prepared = rag._prepare_source_preserving_chunks(
        [], document, lambda text: len(text.split()), 100,
        structural_ranges=set())

    assert prepared == [(
        rag._TEXTLESS_SOURCE_FIGURE_MARKDOWN,
        None,
        [picture],
        True,
    )]
    assert source_fidelity_core.lexical_tokens(prepared[0][0]) == ()


def test_textless_picture_splits_one_interleaved_body_chunk():
    above = SimpleNamespace(
        self_ref="#/texts/0", label="text", text="Body above the image.",
        prov=_layout_provenance(
            7, left=70, top=620, right=430, bottom=590))
    below = SimpleNamespace(
        self_ref="#/texts/1", label="text", text="Body below the image.",
        prov=_layout_provenance(
            7, left=70, top=220, right=430, bottom=190))
    picture = SimpleNamespace(
        self_ref="#/pictures/0", label="picture", captions=[], children=[],
        footnotes=[],
        prov=_layout_provenance(
            7, left=90, top=550, right=410, bottom=250))
    document = SimpleNamespace(
        texts=[above, below], pictures=[picture], tables=[],
        key_value_items=[], form_items=[],
        pages={7: SimpleNamespace(
            size=SimpleNamespace(width=500, height=700))})
    raw = SimpleNamespace(
        text=f"{above.text}\n\n{below.text}",
        meta=SimpleNamespace(
            headings=["Example"], doc_items=[above, below]))

    prepared = rag._prepare_source_preserving_chunks(
        [raw], document, lambda text: len(text.split()), 100,
        structural_ranges=set())

    assert [entry[0] for entry in prepared] == [
        above.text, rag._TEXTLESS_SOURCE_FIGURE_MARKDOWN, below.text]


def test_textless_figure_carrier_keeps_its_physical_heading_occurrence():
    def item(index, label, text, page, top):
        return SimpleNamespace(
            self_ref=f"#/texts/{index}", label=label, text=text,
            content_layer="body",
            prov=_layout_provenance(
                page, left=90, top=top, right=480, bottom=top - 18),
        )

    section = item(
        0, "section_header", "§10.01 Input Acquisition", 1, 700)
    prior_notes = item(1, "section_header", "Notes & Questions", 1, 620)
    case = item(
        2, "section_header",
        "Orion Devices v. Delta Labs", 1, 540)
    case_body = item(
        3, "text", "The report discusses the relay diagram.", 2, 680)
    later_notes = item(4, "section_header", "Notes & Questions", 2, 250)
    later_body = item(
        5, "list_item", "1. The Failed Input and the Protocol.", 2, 200)
    picture = SimpleNamespace(
        self_ref="#/pictures/0", label="picture", content_layer="body",
        captions=[], children=[], footnotes=[],
        prov=_layout_provenance(
            2, left=120, top=640, right=420, bottom=300),
    )
    document = SimpleNamespace(
        texts=[
            section, prior_notes, case, case_body, later_notes, later_body],
        pictures=[picture], tables=[], key_value_items=[], form_items=[],
        pages={
            1: SimpleNamespace(size=SimpleNamespace(width=500, height=700)),
            2: SimpleNamespace(size=SimpleNamespace(width=500, height=700)),
        },
    )
    raw = [
        SimpleNamespace(
            text=case_body.text,
            meta=SimpleNamespace(
                headings=[section.text, prior_notes.text, case.text],
                doc_items=[case_body])),
        SimpleNamespace(
            text=later_body.text,
            meta=SimpleNamespace(
                headings=[section.text, later_notes.text],
                doc_items=[later_body])),
    ]

    prepared = rag._prepare_source_preserving_chunks(
        raw, document, lambda text: len(text.split()), 100,
        structural_ranges=set())

    assert [entry[0] for entry in prepared] == [
        case_body.text,
        rag._TEXTLESS_SOURCE_FIGURE_MARKDOWN,
        later_body.text,
    ]
    carrier_headings = prepared[1][1]
    assert carrier_headings is not None
    assert prior_notes.text in carrier_headings
    assert rag._chunking_core.clean_heading_text(case.text) in carrier_headings
    later_headings = prepared[2][1]
    assert later_headings is not None
    assert later_notes.text in later_headings
    assert rag._chunking_core.clean_heading_text(case.text) not in later_headings


def test_multi_page_item_fragments_follow_each_page_geometry():
    def provenance(page, top, bottom, charspan):
        return SimpleNamespace(
            page_no=page,
            bbox=SimpleNamespace(
                l=72.0, t=top, r=450.0, b=bottom,
                coord_origin=SimpleNamespace(value="BOTTOMLEFT")),
            charspan=charspan,
        )

    first_text = "Earlier page prose."
    second_text = "EXUM, Justice."
    combined = f"{first_text} {second_text}"
    spanning = SimpleNamespace(
        self_ref="#/texts/0", label="text", text=combined,
        prov=[
            provenance(1, 180, 100, [0, len(first_text)]),
            provenance(
                2, 170, 150,
                [len(first_text) + 1, len(combined)]),
        ],
    )
    page_two_body = SimpleNamespace(
        self_ref="#/texts/1", label="text",
        text="The page begins with substantive discussion.",
        prov=[provenance(
            2, 680, 400,
            [0, len("The page begins with substantive discussion.")])],
    )
    document = SimpleNamespace(
        texts=[spanning, page_two_body], pictures=[], tables=[],
        key_value_items=[], form_items=[])
    raw_spanning = SimpleNamespace(
        text=combined,
        meta=SimpleNamespace(headings=["Example"], doc_items=[spanning]))
    raw_body = SimpleNamespace(
        text=page_two_body.text,
        meta=SimpleNamespace(
            headings=["Example"], doc_items=[page_two_body]))

    prepared = rag._prepare_source_preserving_chunks(
        [raw_spanning, raw_body], document,
        lambda text: len(text.split()), 100, structural_ranges=set())

    assert [entry[0] for entry in prepared] == [
        first_text,
        page_two_body.text,
        second_text,
    ]
    assert [entry[2][0].self_ref for entry in prepared] == [
        spanning.self_ref,
        page_two_body.self_ref,
        spanning.self_ref,
    ]
    assert [entry[2][0].prov[0].page_no for entry in prepared] == [1, 2, 2]
    first_lineage = rag._source_lineage_for_items(
        prepared[0][2], item_by_ref={spanning.self_ref: spanning},
        parent_refs_by_child={}, caption_refs_by_parent={})
    second_lineage = rag._source_lineage_for_items(
        prepared[2][2], item_by_ref={spanning.self_ref: spanning},
        parent_refs_by_child={}, caption_refs_by_parent={})
    assert first_lineage[0]["scope"] == {"provenance_indexes": [0]}
    assert second_lineage[0]["scope"] == {"provenance_indexes": [1]}
    first_record = rag.enrich_chunk(
        first_text, ["Example"], 0, 2, 2,
        doc_items=prepared[0][2], source_items=first_lineage)
    second_record = rag.enrich_chunk(
        second_text, ["Example"], 1, 2, 2,
        doc_items=prepared[2][2], source_items=second_lineage)
    assert (first_record["metadata"]["page_start"],
            first_record["metadata"]["page_end"]) == (1, 1)
    assert (second_record["metadata"]["page_start"],
            second_record["metadata"]["page_end"]) == (2, 2)


def test_page_fragments_preempt_solo_plus_mixed_whole_item_replay():
    def provenance(page, top, bottom, charspan):
        return SimpleNamespace(
            page_no=page,
            bbox=SimpleNamespace(
                l=72.0, t=top, r=450.0, b=bottom,
                coord_origin=SimpleNamespace(value="BOTTOMLEFT")),
            charspan=charspan,
        )

    first_text = "Earlier page prose."
    second_text = "EXUM, Justice."
    combined = f"{first_text} {second_text}"
    spanning = SimpleNamespace(
        self_ref="#/texts/0", label="text", text=combined,
        prov=[
            provenance(1, 180, 100, [0, len(first_text)]),
            provenance(
                2, 170, 150,
                [len(first_text) + 1, len(combined)]),
        ],
    )
    intervening = SimpleNamespace(
        self_ref="#/texts/1", label="text",
        text="The next page begins with independent discussion.",
        prov=[provenance(
            2, 680, 400,
            [0, len("The next page begins with independent discussion.")])],
    )
    document = SimpleNamespace(
        texts=[spanning, intervening], pictures=[], tables=[],
        key_value_items=[], form_items=[])
    raw_chunks = [
        SimpleNamespace(
            text=first_text,
            meta=SimpleNamespace(
                headings=["Example"], doc_items=[spanning])),
        SimpleNamespace(
            text=f"{second_text} {intervening.text}",
            meta=SimpleNamespace(
                headings=["Example"],
                doc_items=[spanning, intervening])),
    ]

    prepared = rag._prepare_source_preserving_chunks(
        raw_chunks, document, lambda text: len(text.split()), 100,
        structural_ranges=set(), text_overrides={
            spanning.self_ref: combined,
        }, text_rebuild_refs={spanning.self_ref})

    assert [entry[0] for entry in prepared] == [
        first_text, intervening.text, second_text]
    assert [
        [item.self_ref for item in entry[2]] for entry in prepared
    ] == [
        [spanning.self_ref], [intervening.self_ref], [spanning.self_ref]]
    assert all(entry[0] != combined for entry in prepared)


def test_page_fragment_splits_a_bracketing_multi_item_body_entry():
    def provenance(page, top, bottom, charspan):
        return SimpleNamespace(
            page_no=page,
            bbox=SimpleNamespace(
                l=72.0, t=top, r=450.0, b=bottom,
                coord_origin=SimpleNamespace(value="BOTTOMLEFT")),
            charspan=charspan,
        )

    prior = "Earlier page prose."
    byline = "EXUM, Justice."
    combined = f"{prior} {byline}"
    spanning = SimpleNamespace(
        self_ref="#/texts/0", label="text", text=combined,
        prov=[
            provenance(1, 180, 100, [0, len(prior)]),
            provenance(
                2, 170, 160, [len(prior) + 1, len(combined)]),
        ],
    )
    reporter = SimpleNamespace(
        self_ref="#/texts/1", label="text", text="Reporter citation.",
        prov=[provenance(2, 190, 180, [0, len("Reporter citation.")])],
    )
    complaint = SimpleNamespace(
        self_ref="#/texts/2", label="text", text="Complaint begins here",
        prov=[provenance(2, 152, 75, [0, len("Complaint begins here")])],
    )
    continuation = SimpleNamespace(
        self_ref="#/texts/3", label="text", text="and continues.",
        prov=[provenance(3, 670, 640, [0, len("and continues.")])],
    )
    document = SimpleNamespace(
        texts=[spanning, reporter, complaint, continuation],
        pictures=[], tables=[], key_value_items=[], form_items=[])
    raw = [
        SimpleNamespace(
            text=combined,
            meta=SimpleNamespace(headings=["Case"], doc_items=[spanning])),
        SimpleNamespace(
            text=(f"{reporter.text}\n\n{complaint.text} "
                  f"{continuation.text}"),
            meta=SimpleNamespace(
                headings=["Case"],
                doc_items=[reporter, complaint, continuation])),
    ]

    prepared = rag._prepare_source_preserving_chunks(
        raw, document, lambda text: len(text.split()), 100,
        structural_ranges=set())

    assert [entry[0] for entry in prepared] == [
        prior,
        reporter.text,
        byline,
        f"{complaint.text}\n\n{continuation.text}",
    ]
    assert [[item.self_ref for item in entry[2]] for entry in prepared] == [
        [spanning.self_ref],
        [reporter.self_ref],
        [spanning.self_ref],
        [complaint.self_ref, continuation.self_ref],
    ]


def test_repaired_multi_page_item_uses_an_exact_edge_span_as_split_proof():
    source = "Th ere is earlier prose. EXUM, Justice."
    repaired = "There is earlier prose. EXUM, Justice."
    suffix = "EXUM, Justice."
    boundary = source.index(suffix)

    def provenance(page, charspan):
        return SimpleNamespace(
            page_no=page, charspan=charspan,
            bbox=SimpleNamespace(
                l=72.0, t=500.0, r=450.0, b=400.0,
                coord_origin=SimpleNamespace(value="BOTTOMLEFT")))

    item = SimpleNamespace(
        self_ref="#/texts/0", label="text", text=source,
        prov=[
            provenance(1, [0, boundary - 1]),
            provenance(2, [boundary, len(source)]),
        ])

    fragments = rag._source_item_page_fragments(item, repaired)

    assert [fragment.text for fragment in fragments] == [
        "There is earlier prose.", suffix]
    assert [fragment.prov[0].page_no for fragment in fragments] == [1, 2]


def test_page_fragment_split_rejects_a_token_cut_across_pages():
    text = "Discuss this with colleagues and compare answers."
    split = text.index("colleagues") + 4

    def provenance(page, charspan):
        return SimpleNamespace(
            page_no=page, charspan=charspan,
            bbox=SimpleNamespace(
                l=72.0, t=500.0, r=450.0, b=400.0,
                coord_origin=SimpleNamespace(value="BOTTOMLEFT")),
        )

    item = SimpleNamespace(
        self_ref="#/texts/0", label="text", text=text,
        prov=[
            provenance(1, [0, split]),
            provenance(2, [split + 1, len(text) + 2]),
        ],
    )

    assert rag._source_item_page_fragments(item) == ()


def test_text_bearing_picture_splits_interleaved_body_chunk_in_source_order():
    above = SimpleNamespace(
        self_ref="#/texts/0", label="text", text="Body above the diagram.",
        prov=_layout_provenance(
            7, left=70, top=620, right=430, bottom=590))
    first_child = SimpleNamespace(
        self_ref="#/texts/1", label="text", text="Diagram opening",
        prov=_layout_provenance(
            7, left=180, top=500, right=320, bottom=480),
        parent=SimpleNamespace(cref="#/pictures/0"))
    second_child = SimpleNamespace(
        self_ref="#/texts/2", label="text", text="Diagram conclusion",
        prov=_layout_provenance(
            7, left=180, top=350, right=320, bottom=330),
        parent=SimpleNamespace(cref="#/pictures/0"))
    # This item slightly overlaps the picture's lower edge, matching observed
    # mixed-layout geometry instead of requiring a synthetic clean gap.
    below = SimpleNamespace(
        self_ref="#/texts/3", label="text", text="Body below the diagram.",
        prov=_layout_provenance(
            7, left=70, top=260, right=430, bottom=240))
    picture = SimpleNamespace(
        self_ref="#/pictures/0", label="picture", captions=[], footnotes=[],
        children=[SimpleNamespace(cref=item.self_ref)
                  for item in (first_child, second_child)],
        prov=_layout_provenance(
            7, left=90, top=550, right=410, bottom=250))
    document = SimpleNamespace(
        texts=[above, first_child, second_child, below], pictures=[picture],
        tables=[], key_value_items=[], form_items=[])
    raw_body = SimpleNamespace(
        text=f"{above.text}\n\n{below.text}",
        meta=SimpleNamespace(headings=["Example"], doc_items=[above, below]))
    raw_first_child = SimpleNamespace(
        text=first_child.text,
        meta=SimpleNamespace(
            headings=["Example"], doc_items=[first_child]))
    raw_second_child = SimpleNamespace(
        text=second_child.text,
        meta=SimpleNamespace(
            headings=["Example"], doc_items=[second_child]))

    prepared = rag._prepare_source_preserving_chunks(
        [raw_body, raw_first_child, raw_second_child], document,
        lambda text: len(text.split()), 100, structural_ranges=set())

    assert [entry[0] for entry in prepared] == [
        above.text, first_child.text, second_child.text, below.text]
    assert [[item.self_ref for item in (entry[2] or [])]
            for entry in prepared] == [
        [above.self_ref], [first_child.self_ref],
        [second_child.self_ref], [below.self_ref]]


def test_side_column_picture_does_not_split_independent_body_lane():
    above = SimpleNamespace(
        self_ref="#/texts/0", label="text", text="First body paragraph.",
        prov=_layout_provenance(
            7, left=70, top=620, right=280, bottom=590))
    below = SimpleNamespace(
        self_ref="#/texts/1", label="text", text="Second body paragraph.",
        prov=_layout_provenance(
            7, left=70, top=260, right=280, bottom=240))
    child = SimpleNamespace(
        self_ref="#/texts/2", label="text", text="Margin diagram",
        prov=_layout_provenance(
            7, left=350, top=450, right=430, bottom=430),
        parent=SimpleNamespace(cref="#/pictures/0"))
    picture = SimpleNamespace(
        self_ref="#/pictures/0", label="picture", captions=[], footnotes=[],
        children=[SimpleNamespace(cref=child.self_ref)],
        prov=_layout_provenance(
            7, left=330, top=550, right=450, bottom=250))
    document = SimpleNamespace(
        texts=[above, below, child], pictures=[picture], tables=[],
        key_value_items=[], form_items=[])
    raw_body = SimpleNamespace(
        text=f"{above.text}\n\n{below.text}",
        meta=SimpleNamespace(headings=["Example"], doc_items=[above, below]))
    raw_child = SimpleNamespace(
        text=child.text,
        meta=SimpleNamespace(headings=["Example"], doc_items=[child]))

    prepared = rag._prepare_source_preserving_chunks(
        [raw_body, raw_child], document, lambda text: len(text.split()), 100,
        structural_ranges=set())

    assert prepared[0][0] == f"{above.text}\n\n{below.text}"
    assert prepared[0][2] == [above, below]
    assert prepared[1][0] == child.text


def test_short_figure_children_attach_only_to_their_recovered_parent():
    decision = SimpleNamespace(
        self_ref="#/texts/0", label="text", text="Decision node",
        prov=_layout_provenance(7, left=150, top=450))
    yes = SimpleNamespace(
        self_ref="#/texts/1", label="text", text="Yes",
        prov=_layout_provenance(7, left=150, top=400))
    no = SimpleNamespace(
        self_ref="#/texts/2", label="text", text="No",
        prov=_layout_provenance(7, left=250, top=400))
    unrelated_yes = SimpleNamespace(
        self_ref="#/texts/3", label="text", text="Yes",
        prov=_layout_provenance(7, left=80, top=100))
    picture = SimpleNamespace(
        self_ref="#/pictures/0", label="picture", captions=[], footnotes=[],
        children=[SimpleNamespace(cref=item.self_ref)
                  for item in (decision, yes, no)],
        prov=_layout_provenance(
            7, left=120, top=500, right=360, bottom=300))
    document = SimpleNamespace(
        texts=[decision, yes, no, unrelated_yes], pictures=[picture],
        tables=[], key_value_items=[], form_items=[])
    recovery = rag.FigureTextRecovery(
        text="Figure text:\n- Decision node Yes No", page=7,
        bbox=(120.0, 300.0, 360.0, 500.0), page_area_ratio=0.2,
        mean_confidence=None, method="native")

    prepared = rag._prepare_source_preserving_chunks(
        [], document, lambda text: len(text.split()), 100,
        structural_ranges=set(),
        figure_text_overrides={picture.self_ref: recovery})

    figure = next(entry for entry in prepared if picture in (entry[2] or []))
    assert figure[0] == recovery.text
    assert {item.self_ref for item in figure[2]} == {
        picture.self_ref, decision.self_ref, yes.self_ref, no.self_ref}
    standalone = [entry for entry in prepared if unrelated_yes in (entry[2] or [])]
    assert [entry[0] for entry in standalone] == ["Yes"]


def test_unrecovered_picture_child_collision_stays_source_positioned():
    introducing = SimpleNamespace(
        self_ref="#/texts/0", label="text", content_layer="body",
        text="INTRODUCING:",
        prov=_layout_provenance(7, left=200, top=600, right=350),
        parent=SimpleNamespace(cref="#/pictures/0"))
    brand = SimpleNamespace(
        self_ref="#/texts/1", label="text", content_layer="body",
        text="SHOPSMITH",
        prov=_layout_provenance(7, left=220, top=580, right=330),
        parent=SimpleNamespace(cref="#/pictures/0"))
    slogan = SimpleNamespace(
        self_ref="#/texts/2", label="text", content_layer="body",
        text="It Cuts!",
        prov=_layout_provenance(7, left=230, top=560, right=320),
        parent=SimpleNamespace(cref="#/pictures/0"))
    notes_heading = SimpleNamespace(
        self_ref="#/texts/3", label="section_header", content_layer="body",
        text="Notes & Questions",
        prov=_layout_provenance(7, left=200, top=300, right=350))
    notes = SimpleNamespace(
        self_ref="#/texts/4", label="list_item", content_layer="body",
        text="The SHOPSMITH defect and the law are discussed here.",
        prov=_layout_provenance(
            7, left=100, top=250, right=450, bottom=200))
    picture = SimpleNamespace(
        self_ref="#/pictures/0", label="picture", content_layer="body",
        captions=[], footnotes=[],
        children=[SimpleNamespace(cref=item.self_ref)
                  for item in (introducing, brand, slogan)],
        prov=_layout_provenance(
            7, left=80, top=650, right=420, bottom=350))
    document = SimpleNamespace(
        texts=[introducing, brand, slogan, notes_heading, notes],
        pictures=[picture], tables=[], key_value_items=[], form_items=[])
    raw = SimpleNamespace(
        text=notes.text,
        meta=SimpleNamespace(
            headings=[notes_heading.text], doc_items=[notes]))

    prepared = rag._prepare_source_preserving_chunks(
        [raw], document, lambda text: len(text.split()), 100,
        structural_ranges=set())

    assert [entry[0] for entry in prepared] == [
        introducing.text, brand.text, slogan.text, notes.text]
    assert [[item.self_ref for item in (entry[2] or [])]
            for entry in prepared] == [
        [introducing.self_ref], [brand.self_ref], [slogan.self_ref],
        [notes.self_ref],
    ]


def test_picture_table_container_emits_preamble_and_table_once():
    heading = SimpleNamespace(
        self_ref="#/texts/0", label="section_header", content_layer="body",
        text="PROBLEMS 6-8",
        prov=_layout_provenance(7, left=220, top=390, right=340, bottom=375))
    preamble = SimpleNamespace(
        self_ref="#/texts/1", label="text", content_layer="body",
        text="For these problems, assume both parties are harmed.",
        prov=_layout_provenance(7, left=100, top=360, right=450, bottom=320))
    cell_one = SimpleNamespace(
        self_ref="#/texts/2", label="text", content_layer="body", text="CO",
        prov=_layout_provenance(7, left=180, top=220, right=210, bottom=205),
        parent=SimpleNamespace(cref="#/pictures/0"))
    cell_two = SimpleNamespace(
        self_ref="#/texts/3", label="text", content_layer="body", text="$",
        prov=_layout_provenance(7, left=230, top=170, right=245, bottom=150),
        parent=SimpleNamespace(cref="#/pictures/0"))

    class Table:
        self_ref = "#/tables/0"
        label = "table"
        captions = []
        footnotes = []
        children = []
        prov = _layout_provenance(
            7, left=100, top=250, right=450, bottom=100)
        data = SimpleNamespace(
            num_rows=2, num_cols=2,
            table_cells=[
                SimpleNamespace(
                    text="State", start_row_offset_idx=0,
                    end_row_offset_idx=1, start_col_offset_idx=0,
                    end_col_offset_idx=1, column_header=True),
                SimpleNamespace(
                    text="Reading", start_row_offset_idx=0,
                    end_row_offset_idx=1, start_col_offset_idx=1,
                    end_col_offset_idx=2, column_header=True),
            ])

        @staticmethod
        def export_to_markdown(*, doc):
            return "| Sensor | Reading |\n|---|---|\n| S-1 | $ |"

    table = Table()
    picture = SimpleNamespace(
        self_ref="#/pictures/0", label="picture", captions=[], footnotes=[],
        children=[SimpleNamespace(cref=ref) for ref in (
            heading.self_ref, preamble.self_ref,
            cell_one.self_ref, cell_two.self_ref)],
        prov=_layout_provenance(
            7, left=90, top=400, right=460, bottom=90))
    document = SimpleNamespace(
        texts=[heading, preamble, cell_one, cell_two], pictures=[picture],
        tables=[table], key_value_items=[], form_items=[])
    raw = SimpleNamespace(
        text="flattened picture table",
        meta=SimpleNamespace(headings=["Stale heading"], doc_items=[picture, table]))
    recovery = rag.FigureTextRecovery(
        text="Figure text: duplicate", page=7,
        bbox=(90.0, 90.0, 460.0, 400.0), page_area_ratio=0.2,
        mean_confidence=0.9, method="ocr")

    prepared = rag._prepare_source_preserving_chunks(
        [raw], document, lambda text: len(text.split()), 100,
        figure_text_overrides={picture.self_ref: recovery})

    assert [entry[0] for entry in prepared] == [
        preamble.text, "| Sensor | Reading |\n|---|---|\n| S-1 | $ |"]
    table_items = prepared[1][2]
    assert [item.self_ref for item in table_items] == [
        table.self_ref, cell_one.self_ref, cell_two.self_ref, picture.self_ref]
    assert all("duplicate" not in entry[0] for entry in prepared)


def _captioned_table_fixture(exported_markdown):
    caption = SimpleNamespace(
        self_ref="#/texts/0", label="caption", content_layer="body",
        text="Table 1. Sensor Readings", marker="", parent=None,
        prov=_layout_provenance(
            7, left=100, top=270, right=450, bottom=255))
    table = SimpleNamespace(
        self_ref="#/tables/0", label="table", content_layer="body",
        text="", orig="", children=[], footnotes=[],
        captions=[SimpleNamespace(cref=caption.self_ref)],
        parent=SimpleNamespace(cref="#/body"),
        prov=_layout_provenance(
            7, left=100, top=250, right=450, bottom=100),
        data=SimpleNamespace(
            num_rows=2, num_cols=1,
            table_cells=[SimpleNamespace(
                text="Reading", orig="Reading", start_row_offset_idx=0,
                end_row_offset_idx=1, start_col_offset_idx=0,
                end_col_offset_idx=1, column_header=True)]),
        export_to_markdown=lambda *, doc: exported_markdown,
    )
    document = SimpleNamespace(
        texts=[caption], pictures=[], tables=[table],
        key_value_items=[], form_items=[])
    return caption, table, document


def test_separate_caption_chunk_is_owned_only_by_orphan_table():
    table_markdown = (
        "Table 1. Sensor Readings\n\n| Reading |\n|---|\n| $100 |")
    caption, table, document = _captioned_table_fixture(table_markdown)
    caption_chunk = SimpleNamespace(
        text=caption.text,
        meta=SimpleNamespace(headings=["Example"], doc_items=[caption]))

    prepared = rag._prepare_source_preserving_chunks(
        [caption_chunk], document, lambda text: len(text.split()), 100,
        structural_ranges=set())

    assert [entry[0] for entry in prepared] == [table_markdown]
    assert prepared[0][2] == [table]
    assert prepared[0][0].count(caption.text) == 1


def test_missing_table_caption_is_prepended_once_and_alias_bound():
    grid = "| Reading |\n|---|\n| $100 |"
    caption, table, document = _captioned_table_fixture(grid)
    raw_chunks = [
        SimpleNamespace(
            text=caption.text,
            meta=SimpleNamespace(headings=["Example"], doc_items=[caption])),
        SimpleNamespace(
            text="flattened table",
            meta=SimpleNamespace(headings=["Example"], doc_items=[table])),
    ]

    prepared = rag._prepare_source_preserving_chunks(
        raw_chunks, document, lambda text: len(text.split()), 100,
        structural_ranges=set())
    expected = f"{caption.text}\n\n{grid}"

    assert [entry[0] for entry in prepared] == [expected]
    assert prepared[0][0].count(caption.text) == 1
    oracles = rag._source_fidelity_oracles(
        document,
        rag.BoundSourceEnrichments(manifest_input={"source": "test"}),
        prepared_chunks=prepared)
    assert oracles[caption.self_ref]["transform"] == "container_alias"
    assert (oracles[caption.self_ref]["oracle_group_sha256"]
            == oracles[table.self_ref]["oracle_group_sha256"])
    assert oracles[table.self_ref]["oracle_group_members"] == [
        table.self_ref, caption.self_ref]


def test_synthetic_layout_neighbor_does_not_intercept_picture_table(
        monkeypatch):
    before = SimpleNamespace(
        self_ref="#/texts/0", label="text", content_layer="body",
        text="Before the table.",
        prov=_layout_provenance(
            7, left=100, top=310, right=450, bottom=280))
    caption = SimpleNamespace(
        self_ref="#/texts/4", label="caption", content_layer="body",
        text="Table caption",
        prov=_layout_provenance(
            7, left=100, top=275, right=450, bottom=260))
    cell = SimpleNamespace(
        self_ref="#/texts/1", label="text", content_layer="body", text="CO",
        prov=_layout_provenance(
            7, left=180, top=220, right=210, bottom=205),
        parent=SimpleNamespace(cref="#/pictures/0"))
    layout_left = SimpleNamespace(
        self_ref="#/texts/2", label="list_item", content_layer="body",
        text="Left choice",
        prov=_layout_provenance(
            7, left=100, top=80, right=230, bottom=60))
    layout_right = SimpleNamespace(
        self_ref="#/texts/3", label="list_item", content_layer="body",
        text="Right choice",
        prov=_layout_provenance(
            7, left=280, top=80, right=450, bottom=60))

    class Table:
        self_ref = "#/tables/0"
        label = "table"
        captions = [SimpleNamespace(cref=caption.self_ref)]
        footnotes = []
        children = []
        prov = _layout_provenance(
            7, left=100, top=250, right=450, bottom=100)
        data = SimpleNamespace(
            num_rows=2, num_cols=2,
            table_cells=[
                SimpleNamespace(
                    text="State", start_row_offset_idx=0,
                    end_row_offset_idx=1, start_col_offset_idx=0,
                    end_col_offset_idx=1, column_header=True),
                SimpleNamespace(
                    text="Reading", start_row_offset_idx=0,
                    end_row_offset_idx=1, start_col_offset_idx=1,
                    end_col_offset_idx=2, column_header=True),
            ])

        @staticmethod
        def export_to_markdown(*, doc):
            return (
                "Table caption\n\n"
                "| Sensor | Reading |\n|---|---|\n| S-1 | $ |")

    table = Table()
    picture = SimpleNamespace(
        self_ref="#/pictures/0", label="picture", captions=[], footnotes=[],
        children=[SimpleNamespace(cref=cell.self_ref)],
        prov=_layout_provenance(
            7, left=90, top=260, right=460, bottom=90))
    document = SimpleNamespace(
        texts=[before, caption, cell, layout_left, layout_right],
        pictures=[picture], tables=[table], key_value_items=[], form_items=[])
    synthetic_markdown = (
        "| Left | Right |\n|---|---|\n| Left choice | Right choice |")
    monkeypatch.setattr(
        rag, "_detect_aligned_list_tables",
        lambda _document: [rag.SyntheticLayoutTable(
            markdown=synthetic_markdown, items=(layout_left, layout_right))])
    raw = SimpleNamespace(
        text="flattened table and layout",
        meta=SimpleNamespace(
            headings=["Example"],
            doc_items=[before, caption, table, layout_left]))

    prepared = rag._prepare_source_preserving_chunks(
        [raw], document, lambda text: len(text.split()), 100,
        structural_ranges=set())

    assert [entry[0] for entry in prepared] == [
        before.text,
        "Table caption\n\n| Sensor | Reading |\n|---|---|\n| S-1 | $ |",
        synthetic_markdown,
    ]
    assert "\n".join(entry[0] for entry in prepared).count(caption.text) == 1
    assert [item.self_ref for item in prepared[1][2]] == [
        table.self_ref, cell.self_ref, picture.self_ref]
    assert [item.self_ref for item in prepared[2][2]] == [
        layout_left.self_ref, layout_right.self_ref]


def test_orphan_picture_table_uses_complete_container_lineage():
    cell = SimpleNamespace(
        self_ref="#/texts/0", label="text", content_layer="body", text="CO",
        prov=_layout_provenance(
            7, left=180, top=220, right=210, bottom=205),
        parent=SimpleNamespace(cref="#/pictures/0"))

    class Table:
        self_ref = "#/tables/0"
        label = "table"
        captions = []
        footnotes = []
        children = []
        prov = _layout_provenance(
            7, left=100, top=250, right=450, bottom=100)
        data = SimpleNamespace(
            num_rows=2, num_cols=2,
            table_cells=[
                SimpleNamespace(
                    text="State", start_row_offset_idx=0,
                    end_row_offset_idx=1, start_col_offset_idx=0,
                    end_col_offset_idx=1, column_header=True),
                SimpleNamespace(
                    text="Reading", start_row_offset_idx=0,
                    end_row_offset_idx=1, start_col_offset_idx=1,
                    end_col_offset_idx=2, column_header=True),
            ])

        @staticmethod
        def export_to_markdown(*, doc):
            return "| Sensor | Reading |\n|---|---|\n| S-1 | $ |"

    table = Table()
    picture = SimpleNamespace(
        self_ref="#/pictures/0", label="picture", captions=[], footnotes=[],
        children=[SimpleNamespace(cref=cell.self_ref)],
        prov=_layout_provenance(
            7, left=90, top=260, right=460, bottom=90))
    document = SimpleNamespace(
        texts=[cell], pictures=[picture], tables=[table],
        key_value_items=[], form_items=[])

    prepared = rag._prepare_source_preserving_chunks(
        [], document, lambda text: len(text.split()), 100,
        structural_ranges=set())

    assert [entry[0] for entry in prepared] == [
        "| Sensor | Reading |\n|---|---|\n| S-1 | $ |"]
    assert [item.self_ref for item in prepared[0][2]] == [
        table.self_ref, cell.self_ref, picture.self_ref]


def test_full_width_table_title_becomes_preamble_instead_of_repeated_cells():
    title = "EXERCISE 9 Compare Runs 6, 7, and 8."
    data = SimpleNamespace(
        num_cols=4,
        table_cells=[SimpleNamespace(
            text=title, start_row_offset_idx=0, end_row_offset_idx=1,
            start_col_offset_idx=0, end_col_offset_idx=4)])
    markdown = (
        f"| {title} | {title} | {title} | {title} |\n"
        "|---|---|---|---|\n"
        "| With Retry | Run 6 | Run 7 | Run 8 |\n"
        "| Recorded total | $ | $ | $ |")

    promoted = rag._promote_leading_full_width_table_row(markdown, data)

    assert promoted == (
        f"{title}\n\n"
        "| With Retry | Run 6 | Run 7 | Run 8 |\n"
        "|---|---|---|---|\n"
        "| Recorded total | $ | $ | $ |")


def test_source_answer_blanks_are_restored_only_with_exact_native_count():
    cells = [
        SimpleNamespace(text="$"), SimpleNamespace(text="$_"),
        SimpleNamespace(text="$600")]
    markdown = "| A | B | C |\n|---|---|---|\n| $ | $_ | $600 |"

    restored = rag._restore_source_table_answer_blanks(
        markdown, "$_____ $_____ $600", cells)

    assert restored == (
        "| A | B | C |\n|---|---|---|\n"
        r"| $\_\_\_\_\_ | $\_\_\_\_\_ | $600 |")
    assert rag._restore_source_table_answer_blanks(
        markdown, "$_____ $600", cells) is None


def test_figure_sidecar_stays_before_the_first_later_page():
    body = SimpleNamespace(
        self_ref="#/texts/0", label="text", content_layer="body",
        text="Page one discussion.",
        prov=_layout_provenance(1, left=80, top=500))
    caption = SimpleNamespace(
        self_ref="#/texts/1", label="caption", content_layer="body",
        text="Relay cabinet diagram",
        prov=_layout_provenance(1, left=350, top=480))
    later = SimpleNamespace(
        self_ref="#/texts/2", label="text", content_layer="body",
        text="Page two discussion.",
        prov=_layout_provenance(2, left=80, top=500))
    footnote = SimpleNamespace(
        self_ref="#/texts/3", label="footnote", content_layer="body",
        text="1. Diagram source note.",
        prov=_layout_provenance(1, left=80, top=80))
    picture = SimpleNamespace(
        self_ref="#/pictures/0", label="picture",
        captions=[SimpleNamespace(cref="#/texts/1")], footnotes=[],
        prov=_layout_provenance(
            1, left=340, top=520, right=460, bottom=400))
    document = SimpleNamespace(
        texts=[body, caption, later, footnote], pictures=[picture], tables=[],
        key_value_items=[], form_items=[])
    raw = [
        SimpleNamespace(
            text=body.text,
            meta=SimpleNamespace(headings=["Example"], doc_items=[body])),
        SimpleNamespace(
            text=later.text,
            meta=SimpleNamespace(headings=["Example"], doc_items=[later])),
        SimpleNamespace(
            text=footnote.text,
            meta=SimpleNamespace(headings=["Example"], doc_items=[footnote])),
    ]

    prepared = rag._prepare_source_preserving_chunks(
        raw, document, lambda text: len(text.split()), 100,
        structural_ranges=set())

    assert [entry[0] for entry in prepared] == [
        body.text,
        "Figure: Relay cabinet diagram",
        later.text,
        footnote.text,
    ]


def test_substantive_furniture_footer_is_recovered_as_source_bound_sidecar():
    body = SimpleNamespace(
        self_ref="#/texts/0", label="text", content_layer="body",
        text="Main discussion.",
        prov=_layout_provenance(9, left=80, top=500))
    citation = SimpleNamespace(
        self_ref="#/texts/1", label="page_footer", content_layer="furniture",
        text="46. https://example.test/archive/ABCD-1234",
        prov=_layout_provenance(9, left=100, top=80))
    document = SimpleNamespace(
        texts=[body, citation], pictures=[], tables=[],
        key_value_items=[], form_items=[])
    raw = SimpleNamespace(
        text=body.text,
        meta=SimpleNamespace(headings=["Section"], doc_items=[body]))

    prepared = rag._prepare_source_preserving_chunks(
        [raw], document, lambda text: len(text.split()), 100,
        structural_ranges=set())

    assert [entry[0] for entry in prepared] == [
        "Main discussion.", "46. https://example.test/archive/ABCD-1234"]
    assert prepared[1][2] == [citation]
    assert prepared[1][3] is True


def test_repeated_running_footer_is_removed_even_when_raw_chunk_includes_it():
    body = SimpleNamespace(
        self_ref="#/texts/0", label="text", content_layer="body",
        text="Main discussion.",
        prov=_layout_provenance(9, left=80, top=500))
    footers = [
        SimpleNamespace(
            self_ref=f"#/texts/{index + 1}", label="page_footer",
            content_layer="furniture", text="SYSTEM OPERATIONS",
            prov=_layout_provenance(9 + index, left=100, top=80))
        for index in range(3)
    ]
    document = SimpleNamespace(
        texts=[body, *footers], pictures=[], tables=[],
        key_value_items=[], form_items=[])
    raw = SimpleNamespace(
        text=f"{body.text}\n{footers[0].text}",
        meta=SimpleNamespace(
            headings=["Section"], doc_items=[body, footers[0]]))

    prepared = rag._prepare_source_preserving_chunks(
        [raw], document, lambda text: len(text.split()), 100,
        structural_ranges=set())

    assert [entry[0] for entry in prepared] == [body.text]


def test_footnote_sidecar_moves_after_cross_page_sentence_and_backlinks():
    body = {
        "text": "The controller records measurements that",
        "metadata": {
            "content_type": "author_narrative", "content_source": "body",
            "source_file": "book", "chapter_num": 1,
            "page_start": 51, "page_end": 51, "page_range": "p.11",
        },
    }
    footnote = {
        "text": "12. Sample protocol section 69.",
        "metadata": {
            "content_type": "footnote", "content_source": "footnote",
            "source_file": "book", "chapter_num": 1,
            "page_start": 51, "page_end": 51, "page_range": "p.11",
        },
    }
    continuation = {
        "text": "continue onto the following page.",
        "metadata": {
            "content_type": "author_narrative", "content_source": "body",
            "source_file": "book", "chapter_num": 1,
            "page_start": 52, "page_end": 52, "page_range": "p.12",
        },
    }

    records = rag._reorder_footnote_sidecars(
        [body, footnote, continuation])
    assert records == [body, continuation, footnote]
    assert footnote["metadata"][rag._quality_core.PAGE_ORDER_REASON_FIELD] == (
        rag._quality_core.FOOTNOTE_AFTER_CONTINUATION_REASON)

    rag._retrieval_core._attach_retrieval_linkage(records)
    rag._attach_footnote_backlinks(records)
    assert footnote["metadata"]["footnote_parent_stable_id"] == (
        body["metadata"]["stable_id"])
    assert footnote["metadata"]["footnote_number"] == "12"
    assert body["metadata"]["footnote_stable_ids"] == [
        footnote["metadata"]["stable_id"]]
    assert footnote["metadata"]["context_parent_id"] == ""


def test_footnote_sidecar_uses_scoped_geometry_between_same_page_body():
    def source_item(index, top, bottom, *, label="text"):
        return {
            "ref": f"#/texts/{index}",
            "label": label,
            "spans": [{
                "provenance_index": 0,
                "page": 68,
                "bbox": [72.0, top, 450.0, bottom],
                "origin": "BOTTOMLEFT",
                "charspan": [0, 20],
            }],
            "scope": {"provenance_indexes": [0]},
        }

    above = _record("Body above the note.", "Example", 68)
    above["metadata"]["source_items"] = [source_item(0, 600, 500)]
    below = _record("Body below the note.", "Example", 68)
    below["metadata"]["source_items"] = [source_item(2, 300, 200)]
    footnote = _record(
        "* Synthetic source note.", "Example", 68, content_type="footnote")
    footnote["metadata"]["content_source"] = "footnote"
    footnote["metadata"]["source_items"] = [
        source_item(1, 400, 350, label="footnote")]

    reordered = rag._reorder_footnote_sidecars([above, footnote, below])

    assert reordered == [above, footnote, below]


def test_explicit_body_numbered_list_stays_before_hypothetical():
    numbered_list = {
        "text": "9. Missing Calibration Seal. Body discussion.",
        "metadata": {
            "content_type": "footnote", "content_source": "body",
            "page_start": 979, "page_end": 979,
            "source_items": [
                {"ref": "#/texts/9", "label": "list_item"},
                {"ref": "#/texts/10", "label": "list_item"},
            ],
        },
    }
    promoted_footnote = {
        "text": "12. A synthetic source footnote.",
        "metadata": {
            "content_type": "footnote", "content_source": "footnote",
            "page_start": 979, "page_end": 979,
            "source_items": [
                {"ref": "#/texts/12", "label": "list_item"}],
        },
    }
    hypothetical = {
        "text": "Assume the controller receives a stale status packet.",
        "metadata": {
            "content_type": "problem_hypothetical", "content_source": "body",
            "page_start": 979, "page_end": 979,
            "source_items": [
                {"ref": "#/texts/11", "label": "text"}],
        },
    }

    records = [numbered_list, promoted_footnote, hypothetical]
    assert rag._lock_explicit_source_body_types(records) == 1
    reordered = rag._reorder_footnote_sidecars(records)

    assert numbered_list["metadata"]["content_type"] == "author_narrative"
    assert promoted_footnote["metadata"]["content_type"] == "footnote"
    assert reordered == [numbered_list, hypothetical, promoted_footnote]


def test_picture_ancestor_has_one_proven_owner_and_children_keep_parent_refs():
    picture_ref = "#/pictures/7"
    figure = {
        "text": "Recovered figure text",
        "metadata": {
            "content_source": "figure",
            "source_items": [{
                "ref": picture_ref, "label": "picture",
                "transform": "figure", "parent_refs": [],
            }],
        },
    }
    children = [
        {
            "text": f"child {index}",
            "metadata": {
                "content_source": "body",
                "source_items": [
                    {
                        "ref": f"#/texts/{index}", "label": "text",
                        "transform": "plain",
                        "parent_refs": [picture_ref],
                    },
                    {
                        "ref": picture_ref, "label": "picture",
                        "transform": "figure", "parent_refs": [],
                    },
                ],
            },
        }
        for index in range(2)
    ]

    assert rag._assign_opaque_picture_ownership([figure, *children]) == 2
    assert figure["metadata"]["source_items"][0]["ref"] == picture_ref
    assert all(
        record["metadata"]["source_items"][0]["parent_refs"]
        == [picture_ref]
        and len(record["metadata"]["source_items"]) == 1
        for record in children)


def test_picture_without_true_figure_uses_first_exact_descendant_owner():
    picture_ref = "#/pictures/1"
    records = []
    for index in range(3):
        records.append({
            "text": f"child {index}",
            "metadata": {
                "content_source": "body",
                "source_items": [
                    {
                        "ref": f"#/texts/{index}", "label": "text",
                        "transform": "plain",
                        "parent_refs": [picture_ref],
                    },
                    {
                        "ref": picture_ref, "label": "picture",
                        "transform": "figure", "parent_refs": [],
                    },
                ],
            },
        })

    assert rag._assign_opaque_picture_ownership(records) == 2
    assert len(records[0]["metadata"]["source_items"]) == 2
    assert all(
        len(record["metadata"]["source_items"]) == 1
        for record in records[1:])


def test_unproved_duplicate_picture_claim_is_left_for_fidelity_to_reject():
    picture_ref = "#/pictures/3"
    records = [
        {
            "text": "first",
            "metadata": {"content_source": "body", "source_items": [{
                "ref": picture_ref, "label": "picture",
                "transform": "figure", "parent_refs": [],
            }]},
        },
        {
            "text": "replay",
            "metadata": {"content_source": "body", "source_items": [{
                "ref": picture_ref, "label": "picture",
                "transform": "figure", "parent_refs": [],
            }]},
        },
    ]

    assert rag._assign_opaque_picture_ownership(records) == 0
    assert all(len(record["metadata"]["source_items"]) == 1
               for record in records)


def test_source_content_lock_overrides_structural_citation_heuristic():
    record = {
        "text": "53. Sample Protocol § 401.12-.19 (2026).",
        "metadata": {"content_type": "structural"},
    }

    rag._apply_source_content_lock(record, "footnote")

    assert record["metadata"]["content_source"] == "footnote"
    assert record["metadata"]["content_type"] == "footnote"


def test_source_bound_body_text_overrides_structural_prose_heuristic():
    record = {"text": "SYSTEM READY.", "metadata": {
        "content_type": "structural", "content_source": "body"}}
    source_item = SimpleNamespace(label="text")

    retained = rag._retain_source_bound_structural_text(
        record, [source_item])

    assert retained is True
    assert record["metadata"]["content_type"] == "author_narrative"


def test_source_lineage_keeps_structural_override_after_items_are_detached():
    record = {"text": "CALIBRATION FAILED, RETRY QUEUED.", "metadata": {
        "content_type": "structural", "content_source": "body",
        "source_items": [{"ref": "#/texts/1", "label": "text"}],
    }}

    retained = rag._retain_source_bound_structural_text(record, None)

    assert retained is True
    assert record["metadata"]["content_type"] == "author_narrative"


def test_source_bound_section_header_stays_structural():
    record = {"text": "CONTENTS", "metadata": {
        "content_type": "structural", "content_source": "body"}}
    source_item = SimpleNamespace(label="section_header")

    retained = rag._retain_source_bound_structural_text(
        record, [source_item])

    assert retained is False
    assert record["metadata"]["content_type"] == "structural"


def test_verified_chapter_summary_document_index_survives_structural_filter():
    record = {"text": "- §10.01 Inputs\n  - A. Sensors", "metadata": {
        "content_type": "structural",
        "content_source": "body",
        "chapter_num": 10,
        "headings": ["Summary of Contents"],
    }}
    source_item = SimpleNamespace(label="document_index")

    assert rag._retain_source_bound_structural_text(
        record, [source_item]) is True
    assert record["metadata"]["content_type"] == "author_narrative"

    ordinary_index = {"text": "Cases, 900", "metadata": {
        "content_type": "structural",
        "content_source": "body",
        "chapter_num": 10,
        "headings": ["Index"],
    }}
    assert rag._retain_source_bound_structural_text(
        ordinary_index, [source_item]) is False


def test_source_footnote_marker_is_not_removed_as_a_page_number():
    assert rag._normalize_source_chunk_text("7", "footnote") == "7"
    assert rag._normalize_source_chunk_text("7", "body") == ""
    assert rag._normalize_source_chunk_text(
        "2003", "body", preserve_source_identity=True) == "2003"


def test_coalescing_preserves_source_bound_all_caps_body_type():
    left = _record("CALIBRATION FAILED,", "Figure discussion", 367)
    right = _record("RETRY QUEUED.", "Figure discussion", 367)
    left["metadata"]["source_items"] = [
        {"ref": "#/texts/0", "label": "text",
         "spans": [{"provenance_index": 0}],
         "scope": {"provenance_indexes": [0]}}]
    right["metadata"]["source_items"] = [
        {"ref": "#/texts/1", "label": "text",
         "spans": [{"provenance_index": 0}],
         "scope": {"provenance_indexes": [0]}}]

    repaired = rag._coalesce_chunk_boundaries(
        [left, right], lambda text: len(text.split()), 100)

    assert len(repaired) == 1
    assert repaired[0]["metadata"]["content_type"] == "author_narrative"
    assert {item["ref"] for item in repaired[0]["metadata"]["source_items"]} == {
        "#/texts/0", "#/texts/1"}


def test_footnote_before_later_body_keeps_page_order_and_nullable_page_end():
    footnote = {
        "text": "1. Early source note.",
        "metadata": {
            "content_type": "footnote", "content_source": "footnote",
            "source_file": "book", "page_start": 1, "page_end": 1,
        },
    }
    later_body = {
        "text": "Later body text.",
        "metadata": {
            "content_type": "author_narrative", "content_source": "body",
            "source_file": "book", "page_start": 5, "page_end": None,
        },
    }

    records = rag._reorder_footnote_sidecars([later_body, footnote])
    assert records == [footnote, later_body]
    rag._retrieval_core._attach_retrieval_linkage(records)
    rag._attach_footnote_backlinks(records)
    assert "footnote_parent_stable_id" not in footnote["metadata"]


def test_ordinary_footnote_backlink_prefers_body_over_nearby_figure():
    body = {
        "text": "Body discussion.",
        "metadata": {
            "content_type": "author_narrative", "content_source": "body",
            "source_file": "book", "page_start": 1, "page_end": 1,
            "source_items": [{"ref": "#/texts/0", "parent_refs": []}],
        },
    }
    figure = {
        "text": "Figure: process diagram.",
        "metadata": {
            "content_type": "figure", "content_source": "figure",
            "source_file": "book", "page_start": 1, "page_end": 1,
            "source_items": [{"ref": "#/pictures/0", "parent_refs": []}],
        },
    }
    footnote = {
        "text": "2. Ordinary discussion note.",
        "metadata": {
            "content_type": "footnote", "content_source": "footnote",
            "source_file": "book", "page_start": 1, "page_end": 1,
            "source_items": [{"ref": "#/texts/1", "parent_refs": []}],
        },
    }
    records = [body, figure, footnote]
    rag._retrieval_core._attach_retrieval_linkage(records)
    rag._attach_footnote_backlinks(records)

    assert footnote["metadata"]["footnote_parent_stable_id"] == (
        body["metadata"]["stable_id"])


def test_nested_figure_footnote_backlink_uses_declared_source_parent():
    body = {
        "text": "Body discussion.",
        "metadata": {
            "content_type": "author_narrative", "content_source": "body",
            "source_file": "book", "page_start": 1, "page_end": 1,
            "source_items": [{"ref": "#/texts/0", "parent_refs": []}],
        },
    }
    figure = {
        "text": "Figure: process diagram.",
        "metadata": {
            "content_type": "figure", "content_source": "figure",
            "source_file": "book", "page_start": 1, "page_end": 1,
            "source_items": [{"ref": "#/pictures/0", "parent_refs": []}],
        },
    }
    footnote = {
        "text": "3. Figure source.",
        "metadata": {
            "content_type": "footnote", "content_source": "footnote",
            "source_file": "book", "page_start": 1, "page_end": 1,
            "source_items": [{
                "ref": "#/texts/2", "parent_refs": ["#/pictures/0"]}],
        },
    }
    records = rag._reorder_footnote_sidecars([body, footnote, figure])
    assert records == [body, figure, footnote]
    rag._retrieval_core._attach_retrieval_linkage(records)
    rag._attach_footnote_backlinks(records)

    assert footnote["metadata"]["footnote_parent_stable_id"] == (
        figure["metadata"]["stable_id"])


def test_source_text_splitter_bounds_one_unbroken_ocr_token():
    text = "ABCDEFGHIJKLMNOPQRSTUVWXYZ"
    parts = rag._split_source_text_by_tokens(text, len, 10)

    assert "".join(parts) == text
    assert all(len(part) <= 10 for part in parts)


def test_problem_and_figure_types_are_locked_from_optional_classifiers():
    problem = {"metadata": {
        "content_type": "problem_hypothetical", "content_source": "body"}}
    figure = {"metadata": {
        "content_type": "author_narrative", "content_source": "figure"}}

    assert rag._source_locked_content_type(problem) == "problem_hypothetical"
    assert rag._source_locked_content_type(figure) == "figure"
    assert "problem_hypothetical" in rag._CLASSIFY_PROMPT
    assert rag._ZS_LABEL_MAP[
        "assigned legal problem or hypothetical fact pattern"
    ] == "problem_hypothetical"


def test_coalescing_never_merges_figure_sidecar_into_body_flow():
    body = _record("The sequence continues", "Discipline process", 5)
    figure = _record("with an investigation step.", "Discipline process", 5)
    figure["metadata"].update({
        "content_type": "figure", "content_source": "figure"})

    repaired = rag._coalesce_chunk_boundaries(
        [body, figure], lambda text: len(text.split()), 100)

    assert repaired == [body, figure]


def test_bookmark_scaffold_warnings_matrix():
    scaffold = [
        {"level": 1, "title": "Chapter 1  The Courts", "page": 1,
         "chapter_num": 1, "path": "Chapter 1 The Courts"},
        {"level": 2, "title": "A. Jurisdiction", "page": 3,
         "chapter_num": 1, "path": "Chapter 1 > A. Jurisdiction"},
    ]
    outline = [
        (1, "Chapter 1 The Courts", 1),
        (1, "A. Jurisdiction", 3),
        (1, "Chapter 9 Remedies", 200),
    ]
    warnings = rag._bookmark_scaffold_warnings(outline, scaffold)
    assert any("level mismatch" in w and "A. Jurisdiction" in w
               for w in warnings)
    assert any("missing from scaffold" in w and "Chapter 9 Remedies" in w
               for w in warnings)
    assert rag._bookmark_scaffold_warnings([], scaffold) == []


def test_bookmark_scaffold_warnings_flags_scaffold_only_chapters():
    scaffold = [{"level": 1, "title": "Chapter 2 Contracts", "page": 30,
                 "chapter_num": 2, "path": "Chapter 2 Contracts"}]
    outline = [(1, "Chapter 1 Torts", 1)]
    warnings = rag._bookmark_scaffold_warnings(outline, scaffold)
    assert any("missing from bookmarks" in w and "Chapter 2 Contracts" in w
               for w in warnings)
    assert any("missing from scaffold" in w and "Chapter 1 Torts" in w
               for w in warnings)


class _FakeTelemetry:
    def __init__(self):
        self.observations = []

    def stage_observation(self, stage, *, metrics=None):
        self.observations.append((stage, metrics))


def test_run_bookmark_cross_check_warns_and_records_telemetry_on_mismatch(
        caplog):
    scaffold = [{"level": 1, "title": "Chapter 2 Contracts", "page": 30,
                 "chapter_num": 2, "path": "Chapter 2 Contracts"}]
    outline = [(1, "Chapter 1 Torts", 1)]
    telemetry = _FakeTelemetry()

    with caplog.at_level(logging.WARNING):
        rag._run_bookmark_cross_check(outline, scaffold, telemetry=telemetry)

    assert "missing from scaffold" in caplog.text
    assert "Chapter 1 Torts" in caplog.text
    assert "missing from bookmarks" in caplog.text
    assert "Chapter 2 Contracts" in caplog.text
    assert telemetry.observations == [
        ("chunk_bookmarks", {"bookmark_entries": 1, "bookmark_warnings": 2})]


def test_run_bookmark_cross_check_is_a_noop_when_outline_unavailable(
        caplog):
    telemetry = _FakeTelemetry()

    with caplog.at_level(logging.WARNING):
        rag._run_bookmark_cross_check(None, [{"level": 1, "title": "x"}],
                                       telemetry=telemetry)

    assert caplog.text == ""
    assert telemetry.observations == []


def test_recover_bound_toc_cell_repairs_reads_outline_from_one_snapshot(
        monkeypatch, tmp_path):
    """The bookmark outline must come from the same snapshot used for TOC
    cell repairs — not a second physical copy+hash of the source PDF."""
    snapshot_opens = []

    @contextmanager
    def counting_snapshot(*args, **kwargs):
        snapshot_opens.append((args, kwargs))
        yield SimpleNamespace(
            pdf=SimpleNamespace(path=tmp_path / "book.pdf"))

    monkeypatch.setattr(
        rag, "_open_docling_source_pdf_snapshot", counting_snapshot)
    monkeypatch.setattr(
        rag, "_recover_native_toc_cell_repairs",
        lambda *_a, **_k: {(0, 0): "Repaired"})
    monkeypatch.setattr(
        rag, "_read_source_pdf_outline",
        lambda _path: [(1, "Chapter 9 Remedies", 1)])

    repairs, outline = rag._recover_bound_toc_cell_repairs(
        {}, tmp_path / "book.json", None,
        source_pdf_path=None, book_sections={})

    assert len(snapshot_opens) == 1
    assert repairs == {(0, 0): "Repaired"}
    assert outline == [(1, "Chapter 9 Remedies", 1)]


def test_recover_bound_toc_cell_repairs_returns_none_outline_without_source(
        monkeypatch, tmp_path, caplog):
    @contextmanager
    def fake_snapshot(*_args, **_kwargs):
        yield None

    monkeypatch.setattr(
        rag, "_open_docling_source_pdf_snapshot", fake_snapshot)

    with caplog.at_level(logging.WARNING):
        repairs, outline = rag._recover_bound_toc_cell_repairs(
            {}, tmp_path / "book.json", None,
            source_pdf_path=None, book_sections={})

    assert repairs == {}
    assert outline is None
    assert "Bookmark cross-check skipped" in caplog.text


def test_recover_bound_toc_cell_repairs_keeps_repairs_when_outline_fails(
        monkeypatch, tmp_path, caplog):
    """A bookmark-outline read failure must not discard TOC cell repairs
    already recovered from the same snapshot (isolated warn-and-continue)."""
    @contextmanager
    def fake_snapshot(*_args, **_kwargs):
        yield SimpleNamespace(
            pdf=SimpleNamespace(path=tmp_path / "book.pdf"))

    monkeypatch.setattr(
        rag, "_open_docling_source_pdf_snapshot", fake_snapshot)
    monkeypatch.setattr(
        rag, "_recover_native_toc_cell_repairs",
        lambda *_a, **_k: {(0, 0): "Repaired"})

    def failing_outline(_path):
        raise RuntimeError("corrupt bookmark tree")

    monkeypatch.setattr(rag, "_read_source_pdf_outline", failing_outline)

    with caplog.at_level(logging.WARNING):
        repairs, outline = rag._recover_bound_toc_cell_repairs(
            {}, tmp_path / "book.json", None,
            source_pdf_path=None, book_sections={})

    assert repairs == {(0, 0): "Repaired"}
    assert outline is None
    assert "Bookmark cross-check skipped: corrupt bookmark tree" in (
        caplog.text)
