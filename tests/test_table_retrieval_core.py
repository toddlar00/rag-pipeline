from __future__ import annotations

import copy
import subprocess
import sys

import pytest

import rag
import retrieval_core
import table_retrieval_core as tables


def _table_record(text: str) -> dict:
    return {
        "text": text,
        "metadata": {
            "content_type": "table",
            "content_source": "table",
            "source_file": "Ethics",
            "section_path": "Fees > Restrictions",
            "chapter_num": 9,
            "chapter_title": "Fees",
            "case_names": [],
            "primary_case": None,
            "page_range": "pp.100-101",
            "page_start": 100,
            "page_end": 101,
            "cross_references": [],
            "headings": ["Fees", "Restrictions"],
            "context": "A comparison of fee rules.",
            "chunk_index": 0,
            "token_count": 40,
            "embedding_token_count": 42,
            "source_lineage_schema_version": 1,
            "source_items": [{
                "ref": "#/tables/0",
                "label": "table",
                "spans": [],
                "parent_refs": [],
            }],
            "table_rows": 5,
            "table_cols": 2,
        },
    }


def _large_table() -> str:
    return """Fee restrictions
| Arrangement | Required safeguard |
| --- | --- |
| Contingent fee | Written agreement |
| Division with another lawyer | Client consent |
| Contingent fee | Written agreement |
| Business transaction | Independent advice |
"""


def test_table_retrieval_core_is_a_standard_library_only_leaf():
    result = subprocess.run(
        [
            sys.executable,
            "-c",
            (
                "import sys; import table_retrieval_core; "
                "forbidden = {'rag', 'retrieval_core', 'quality_core', "
                "'llm_runtime', 'requests', 'docling', 'chromadb', "
                "'qdrant_client', 'numpy', 'torch'}; "
                "loaded = forbidden.intersection(sys.modules); "
                "assert not loaded, sorted(loaded)"
            ),
        ],
        capture_output=True,
        check=False,
        text=True,
        timeout=30,
    )

    assert result.returncode == 0, result.stderr


def test_strict_parser_preserves_caption_escaped_pipes_and_blank_cells():
    parsed = tables._parse_markdown_table("""Comparison | explanatory caption
| Rule | Value\\|alternative | Note |
| :--- | ---: | :---: |
| A | one\\|two | |
| B | three | four |
""")

    assert parsed is not None
    assert parsed.preamble == ("Comparison | explanatory caption",)
    assert parsed.column_count == 3
    assert parsed.rows[0] == "| A | one\\|two | |"
    assert parsed.row_document(0).endswith("| A | one\\|two | |")
    assert tables._split_markdown_row(r"| A\|B | C |") == (
        r"A\|B", "C")
    assert tables._split_markdown_row(r"| A\\| B |") == (r"A\\", "B")


def test_strict_parser_rejects_missing_separator_ragged_and_trailing_prose():
    invalid_tables = [
        "| A | B |\n| one | two |\n| three | four |",
        "| A | B |\n| --- | --- |\n| one |",
        "| A | B |\n| --- | --- |\n| one | two |\ntrailing prose",
        ("| A | B |\n| --- | --- |\n| one | two |\n"
         "| C | D |\n| --- | --- |\n| three | four |"),
    ]

    assert all(tables._parse_markdown_table(value) is None
               for value in invalid_tables)


def test_expansion_preserves_parent_and_assigns_unique_child_citations():
    original = _table_record(_large_table())
    snapshot = copy.deepcopy(original)
    parent_id = retrieval_core._chunk_id(original)

    expanded = tables.expand_table_records(
        [original], stable_id_fn=retrieval_core._chunk_id,
        token_count_fn=lambda value: len(value.split()),
    )

    assert original == snapshot
    assert len(expanded) == 5
    parent = expanded[0]
    children = expanded[1:]
    assert parent["text"] == snapshot["text"]
    assert retrieval_core._chunk_id(parent) == parent_id
    assert parent["metadata"]["retrieval_role"] == tables.TABLE_PARENT_ROLE
    assert parent["metadata"]["table_parent_stable_id"] == parent_id
    assert parent["metadata"]["table_child_count"] == 4
    assert parent["metadata"]["table_source_row_count"] == 4
    assert parent["metadata"]["table_source_fragment_count"] == 1
    assert [child["metadata"]["table_child_index"] for child in children] == (
        [0, 1, 2, 3])
    assert all(child["metadata"]["table_rows"] == 1 for child in children)
    assert all(child["text"].startswith(
        "Fee restrictions\n| Arrangement | Required safeguard |")
        for child in children)

    # Rows 0 and 2 have identical source text but remain independently citable.
    stable_ids = [retrieval_core._chunk_id(record) for record in expanded]
    assert len(set(stable_ids)) == len(stable_ids)
    assert stable_ids[1] != stable_ids[3]


def test_validated_table_family_members_fail_closed_on_incomplete_lineage():
    expanded = tables.expand_table_records(
        [_table_record(_large_table())],
        stable_id_fn=retrieval_core._chunk_id,
        token_count_fn=lambda value: len(value.split()),
    )
    parent_id = retrieval_core._chunk_id(expanded[0])
    child_ids = {
        retrieval_core._chunk_id(record) for record in expanded[1:]
    }

    members = tables.validated_table_family_members(
        expanded, stable_id_fn=retrieval_core._chunk_id)

    assert members == {parent_id: frozenset({parent_id, *child_ids})}
    tampered = copy.deepcopy(expanded)
    tampered[-1]["metadata"]["table_child_count"] = 3
    with pytest.raises(ValueError, match="corpus attestation"):
        tables.validated_table_family_members(
            tampered, stable_id_fn=retrieval_core._chunk_id)


def test_table_family_attestation_independently_enforces_child_caps(monkeypatch):
    first = _table_record(_large_table())
    second = _table_record(_large_table())
    second["metadata"]["chunk_index"] = 1
    second["metadata"]["source_items"][0]["ref"] = "#/tables/1"
    expanded = tables.expand_table_records(
        [first, second], stable_id_fn=retrieval_core._chunk_id,
        token_count_fn=lambda value: len(value.split()),
    )
    stable_ids = retrieval_core._attach_retrieval_linkage(expanded)

    monkeypatch.setattr(tables, "MAX_TABLE_CHILDREN_PER_PARENT", 4)
    monkeypatch.setattr(tables, "MAX_TABLE_CHILDREN_PER_CORPUS", 8)
    assert tables._table_retrieval_summary(
        expanded, stable_ids=stable_ids)["issues"] == {}

    monkeypatch.setattr(tables, "MAX_TABLE_CHILDREN_PER_PARENT", 3)
    parent_overflow = tables._table_retrieval_summary(
        expanded, stable_ids=stable_ids)
    assert parent_overflow["issues"][
        "family_child_count_exceeds_parent_cap"] == [0, 1]
    assert parent_overflow["issues"][
        "declared_child_count_exceeds_parent_cap"] == list(range(10))
    with pytest.raises(ValueError, match="corpus attestation"):
        tables.validated_table_family_members(
            expanded, stable_id_fn=retrieval_core._chunk_id)

    monkeypatch.setattr(tables, "MAX_TABLE_CHILDREN_PER_PARENT", 4)
    monkeypatch.setattr(tables, "MAX_TABLE_CHILDREN_PER_CORPUS", 7)
    corpus_overflow = tables._table_retrieval_summary(
        expanded, stable_ids=stable_ids)
    assert corpus_overflow["issues"][
        "corpus_child_count_exceeds_cap"] == [9]
    with pytest.raises(ValueError, match="corpus attestation"):
        tables.validated_table_family_members(
            expanded, stable_id_fn=retrieval_core._chunk_id)


def test_continued_source_table_qualifies_across_all_fragments():
    fragments = [
        _table_record(
            "| Rule | Value |\n| --- | --- |\n| A | one |"),
        _table_record(
            "| Rule | Value |\n| --- | --- |\n"
            "| B | two |\n| C | three |\n| D | four |"),
        _table_record(
            "| Rule | Value |\n| --- | --- |\n| E | five |"),
    ]

    expanded = tables.expand_table_records(
        fragments, stable_id_fn=retrieval_core._chunk_id,
        token_count_fn=lambda value: len(value.split()),
    )

    parents = expanded[:3]
    children = expanded[3:]
    assert len(expanded) == 8
    assert [parent["metadata"]["table_child_count"] for parent in parents] == (
        [1, 3, 1])
    assert all(parent["metadata"]["retrieval_role"]
               == tables.TABLE_PARENT_ROLE for parent in parents)
    assert all(parent["metadata"]["table_source_row_count"] == 5
               for parent in parents)
    assert all(parent["metadata"]["table_source_fragment_count"] == 3
               for parent in parents)
    assert len(children) == 5
    assert all(child["metadata"]["table_source_row_count"] == 5
               for child in children)
    assert all(child["metadata"]["table_source_fragment_count"] == 3
               for child in children)

    stable_ids = retrieval_core._attach_retrieval_linkage(expanded)
    assert tables._table_retrieval_summary(
        expanded, stable_ids=stable_ids)["issues"] == {}


def test_identical_row_packed_fragments_survive_with_unique_identities():
    repeated_value = " ".join(["repeated"] * 40)
    markdown = (
        "| Rule | Value |\n| --- | --- |\n"
        f"| Same | {repeated_value} |\n"
        f"| Same | {repeated_value} |"
    )
    parts = rag._split_markdown_table_by_rows(
        markdown, lambda value: len(value.split()), max_tokens=50)
    assert len(parts) == 2
    assert parts[0] == parts[1]

    fragments = [_table_record(part) for part in parts]
    original_parent_id = retrieval_core._chunk_id(fragments[0])
    assert retrieval_core._chunk_id(fragments[1]) == original_parent_id

    assert tables.annotate_table_fragment_occurrences(
        fragments, stable_id_fn=retrieval_core._chunk_id) == 2
    assert [record["metadata"][tables.TABLE_FRAGMENT_OCCURRENCE_FIELD]
            for record in fragments] == [0, 1]
    parent_ids = [retrieval_core._chunk_id(record) for record in fragments]
    assert parent_ids[0] == original_parent_id
    assert len(set(parent_ids)) == 2
    assert rag._deduplicate_chunks(fragments) == fragments

    default_records = rag._deduplicate_chunks(copy.deepcopy(fragments))
    default_ids = retrieval_core._attach_retrieval_linkage(default_records)
    assert all("retrieval_role" not in record["metadata"]
               for record in default_records)
    assert tables._table_retrieval_summary(
        default_records, stable_ids=default_ids,
        source_table_dimensions={"#/tables/0": (2, 2)})["issues"] == {}

    expanded = tables.expand_table_records(
        fragments, stable_id_fn=retrieval_core._chunk_id,
        token_count_fn=lambda value: len(value.split()), min_data_rows=2,
    )
    stable_ids = retrieval_core._attach_retrieval_linkage(expanded)
    assert len(expanded) == 4
    assert len(set(stable_ids)) == 4
    assert tables._table_retrieval_summary(
        expanded, stable_ids=stable_ids, min_data_rows=2)["issues"] == {}

    occurrence_tampered = copy.deepcopy(expanded)
    occurrence_tampered[1]["metadata"][
        tables.TABLE_FRAGMENT_OCCURRENCE_FIELD] = True
    occurrence_summary = tables._table_retrieval_summary(
        occurrence_tampered, stable_ids=stable_ids, min_data_rows=2)
    assert occurrence_summary[
        "issues"]["invalid_table_fragment_occurrence"] == [1]


def test_source_table_fragments_must_share_one_markdown_schema():
    fragments = [
        _table_record(
            "| A | B |\n| --- | --- |\n| 1 | 2 |\n| 3 | 4 |"),
        _table_record(
            "| A | B | C |\n| --- | --- | --- |\n"
            "| 5 | 6 | 7 |\n| 8 | 9 | 10 |"),
    ]

    with pytest.raises(ValueError, match="inconsistent Markdown schemas"):
        tables.expand_table_records(
            fragments, stable_id_fn=retrieval_core._chunk_id,
            token_count_fn=lambda value: len(value.split()))

    stable_ids = retrieval_core._attach_retrieval_linkage(fragments)
    summary = tables._table_retrieval_summary(
        fragments, stable_ids=stable_ids)
    assert summary["issues"]["source_table_schema_mismatch"] == [0, 1]


def test_expansion_is_opt_in_by_size_and_fails_closed_on_reentry_or_caps():
    small = _table_record(
        "| A | B |\n| --- | --- |\n"
        "| 1 | one |\n| 2 | two |\n| 3 | three |")
    unchanged = tables.expand_table_records(
        [small], stable_id_fn=retrieval_core._chunk_id,
        token_count_fn=lambda value: len(value.split()))
    assert unchanged == [small]
    assert not tables.has_table_retrieval_metadata(unchanged)

    expanded = tables.expand_table_records(
        [_table_record(_large_table())],
        stable_id_fn=retrieval_core._chunk_id,
        token_count_fn=lambda value: len(value.split()),
    )
    with pytest.raises(ValueError, match="cannot run twice"):
        tables.expand_table_records(
            expanded, stable_id_fn=retrieval_core._chunk_id,
            token_count_fn=lambda value: len(value.split()))

    too_many_rows = "\n".join([
        "| A | B |",
        "| --- | --- |",
        *[
            f"| {index} | value {index} |"
            for index in range(tables.MAX_TABLE_CHILDREN_PER_PARENT + 1)
        ],
    ])
    with pytest.raises(ValueError, match="per-parent child cap"):
        tables.expand_table_records(
            [_table_record(too_many_rows)],
            stable_id_fn=retrieval_core._chunk_id,
            token_count_fn=lambda value: len(value.split()),
        )


def test_table_children_are_isolated_from_canonical_neighbor_context():
    before = {
        "text": "Before",
        "metadata": {"source_file": "Ethics", "chapter_num": 9},
    }
    table = _table_record(_large_table())
    after = {
        "text": "After",
        "metadata": {"source_file": "Ethics", "chapter_num": 9},
    }
    expanded = tables.expand_table_records(
        [before, table, after], stable_id_fn=retrieval_core._chunk_id,
        token_count_fn=lambda value: len(value.split()),
    )
    stable_ids = retrieval_core._attach_retrieval_linkage(expanded)

    assert expanded[0]["metadata"]["next_stable_id"] == stable_ids[1]
    assert expanded[1]["metadata"]["previous_stable_id"] == stable_ids[0]
    assert expanded[1]["metadata"]["next_stable_id"] == stable_ids[2]
    assert expanded[2]["metadata"]["previous_stable_id"] == stable_ids[1]
    for child in expanded[3:]:
        assert child["metadata"]["context_parent_id"] == ""
        assert child["metadata"]["previous_stable_id"] == ""
        assert child["metadata"]["next_stable_id"] == ""


def test_table_retrieval_summary_recomputes_family_invariants():
    expanded = tables.expand_table_records(
        [_table_record(_large_table())],
        stable_id_fn=retrieval_core._chunk_id,
        token_count_fn=lambda value: len(value.split()),
    )
    stable_ids = retrieval_core._attach_retrieval_linkage(expanded)

    summary = tables._table_retrieval_summary(
        expanded, stable_ids=stable_ids)

    assert summary == {
        "schema_version": 1,
        "parent_tables": 1,
        "expanded_parents": 1,
        "child_chunks": 4,
        "issues": {},
    }

    dimension_tampered = copy.deepcopy(expanded)
    for record in dimension_tampered:
        record["metadata"]["table_rows"] = 99
        record["metadata"]["table_cols"] = 99
        record["metadata"]["table_source_row_count"] = 99
        record["metadata"]["table_source_fragment_count"] = 99
    dimension_summary = tables._table_retrieval_summary(
        dimension_tampered, stable_ids=stable_ids)
    assert dimension_summary["issues"]["parent_table_rows_mismatch"] == [0]
    assert dimension_summary["issues"]["parent_table_cols_mismatch"] == [0]
    assert dimension_summary["issues"]["child_table_rows_mismatch"] == [
        1, 2, 3, 4]
    assert dimension_summary["issues"]["child_table_cols_mismatch"] == [
        1, 2, 3, 4]
    assert dimension_summary["issues"]["source_table_row_count_mismatch"] == [
        0]
    assert dimension_summary[
        "issues"]["source_table_fragment_count_mismatch"] == [0]

    numeric_lookalikes = copy.deepcopy(expanded)
    for record in numeric_lookalikes:
        metadata = record["metadata"]
        metadata["table_retrieval_schema_version"] = True
        metadata["table_rows"] = True
        metadata["table_cols"] = 2.0
        metadata["table_source_row_count"] = 4.0
        metadata["table_source_fragment_count"] = True
    lookalike_summary = tables._table_retrieval_summary(
        numeric_lookalikes, stable_ids=stable_ids)
    assert lookalike_summary["issues"]["schema_version_mismatch"] == [
        0, 1, 2, 3, 4]
    assert lookalike_summary["issues"]["invalid_table_source_row_count"] == [
        0, 1, 2, 3, 4]
    assert lookalike_summary[
        "issues"]["invalid_table_source_fragment_count"] == [0, 1, 2, 3, 4]
    assert lookalike_summary["issues"]["child_table_rows_mismatch"] == [
        1, 2, 3, 4]
    assert lookalike_summary["issues"]["child_table_cols_mismatch"] == [
        1, 2, 3, 4]

    native_shape_summary = tables._table_retrieval_summary(
        expanded, stable_ids=stable_ids,
        source_table_dimensions={"#/tables/0": (5, 3)})
    assert native_shape_summary[
        "issues"]["source_native_row_count_mismatch"] == [0]
    assert native_shape_summary[
        "issues"]["source_native_column_count_mismatch"] == [0]

    recovered = copy.deepcopy(expanded)
    for record in recovered:
        record["metadata"]["table_recovered_from_pdf"] = True
    recovered_summary = tables._table_retrieval_summary(
        recovered, stable_ids=stable_ids,
        source_table_dimensions={"#/tables/0": (5, 3)})
    assert recovered_summary["issues"] == {}

    expanded[2]["text"] = expanded[2]["text"].replace(
        "Client consent", "No consent")
    tampered = tables._table_retrieval_summary(
        expanded, stable_ids=stable_ids)
    assert tampered["issues"]["child_text_mismatch"] == [2]

    reordered = [expanded[0], expanded[2], expanded[1], *expanded[3:]]
    reordered_ids = [stable_ids[0], stable_ids[2], stable_ids[1], *stable_ids[3:]]
    reordered_summary = tables._table_retrieval_summary(
        reordered, stable_ids=reordered_ids)
    assert reordered_summary["issues"]["family_child_indexes_invalid"] == [0]

    parent_after_child = [expanded[1], expanded[0], *expanded[2:]]
    parent_after_child_ids = [stable_ids[1], stable_ids[0], *stable_ids[2:]]
    order_summary = tables._table_retrieval_summary(
        parent_after_child, stable_ids=parent_after_child_ids)
    assert order_summary["issues"]["canonical_record_after_children"] == [1]


def test_parent_child_collapse_suppresses_only_parent_and_retains_siblings():
    parent = {
        "retrieval_role": tables.TABLE_PARENT_ROLE,
        "table_parent_stable_id": "table-1",
        "stable_id": "table-1",
    }
    child_one = {
        "retrieval_role": tables.TABLE_CHILD_ROLE,
        "table_parent_stable_id": "table-1",
        "stable_id": "row-1",
    }
    child_two = {
        "retrieval_role": tables.TABLE_CHILD_ROLE,
        "table_parent_stable_id": "table-1",
        "stable_id": "row-2",
    }

    docs, metas, scores = tables.collapse_table_families(
        ["parent", "row one", "unrelated", "row two"],
        [parent, child_one, {"stable_id": "other"}, child_two],
        [0.99, 0.95, 0.9, 0.85],
    )

    assert docs == ["row one", "unrelated", "row two"]
    assert [meta["stable_id"] for meta in metas] == [
        "row-1", "other", "row-2"]
    assert scores == [0.95, 0.9, 0.85]
    assert tables.collapse_table_families(
        ["parent"], [parent], [0.99])[0] == ["parent"]


def test_canonical_consumers_exclude_only_table_children():
    expanded = tables.expand_table_records(
        [_table_record(_large_table())],
        stable_id_fn=retrieval_core._chunk_id,
        token_count_fn=lambda value: len(value.split()),
    )

    assert tables.canonical_records(expanded) == [expanded[0]]
    assert tables.table_child_count(expanded) == 4
    assert rag._filter_chunk_records(
        expanded, include_types=["table"]) == [expanded[0]]


def test_table_family_metadata_always_requires_quality_attestation(tmp_path):
    records = [{
        "text": "row",
        "metadata": {"retrieval_role": tables.TABLE_CHILD_ROLE},
    }]

    assert tables.has_table_retrieval_metadata(records)
    assert rag._quality_report_required(tmp_path / "chunks.jsonl", records)


@pytest.mark.parametrize("command", ["chunk", "full", "batch"])
def test_ingestion_cli_forwards_table_children(monkeypatch, tmp_path, command):
    observed = []
    pdf = tmp_path / "Book.pdf"
    pdf.write_bytes(b"fixture")
    paths = rag._output_paths_for_name("Book")

    monkeypatch.setattr(rag, "_validate_api_key", lambda _model: None)
    monkeypatch.setattr(
        rag,
        "chunk_document",
        lambda *_args, **kwargs: observed.append(kwargs["table_children"]),
    )

    def fake_pipeline(_pdf, args, **_kwargs):
        observed.append(args.table_children)
        return paths, {"collection": "book", "db_dir": paths["chroma"]}

    monkeypatch.setattr(rag, "_run_pipeline_job", fake_pipeline)
    argv = {
        "chunk": ["chunk", "--table-children"],
        "full": ["full", "--pdf", str(pdf), "--table-children"],
        "batch": ["batch", str(pdf), "--table-children"],
    }[command]

    rag.main(argv)

    assert observed == [True]
