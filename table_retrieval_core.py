"""Deterministic row-level retrieval records for Markdown tables.

The primary table remains the publication source of truth.  Optional child
records repeat its caption/header and one data row so dense and lexical search
can match a specific rule, amount, or exception without losing column meaning.
This module is standard-library-only and contains no backend or CLI concerns.
"""

from __future__ import annotations

import copy
import re
from collections import defaultdict
from collections.abc import Callable, Mapping, Sequence
from dataclasses import dataclass
from typing import Any


TABLE_RETRIEVAL_SCHEMA_VERSION = 1
TABLE_FAMILY_COLLAPSE_VERSION = 1
TABLE_PARENT_ROLE = "table_parent"
TABLE_CHILD_ROLE = "table_child"
DEFAULT_TABLE_CHILD_MIN_ROWS = 4
MAX_TABLE_CHILDREN_PER_PARENT = 128
MAX_TABLE_CHILDREN_PER_CORPUS = 10_000
TABLE_FRAGMENT_OCCURRENCE_FIELD = "table_fragment_occurrence"

_TABLE_RETRIEVAL_FIELDS = frozenset({
    "table_retrieval_schema_version",
    "retrieval_role",
    "table_parent_stable_id",
    "table_child_index",
    "table_child_count",
    "table_source_row_count",
    "table_source_fragment_count",
})
_RETRIEVAL_LINKAGE_FIELDS = frozenset({
    "retrieval_linkage_schema_version",
    "stable_id",
    "context_parent_id",
    "previous_stable_id",
    "next_stable_id",
})
_FAMILY_METADATA_FIELDS = (
    "content_type",
    "content_source",
    "source_file",
    "section_path",
    "chapter_num",
    "chapter_title",
    "case_names",
    "primary_case",
    "page_range",
    "page_start",
    "page_end",
    "cross_references",
    "headings",
    "context",
    "source_lineage_schema_version",
    "source_items",
    "table_cols",
    TABLE_FRAGMENT_OCCURRENCE_FIELD,
    "table_source_row_count",
    "table_source_fragment_count",
    "table_recovered_from_pdf",
)
_SEPARATOR_CELL_RE = re.compile(r"^:?-{3,}:?$")


@dataclass(frozen=True)
class MarkdownTable:
    """A conservative, source-preserving parse of one Markdown table."""

    preamble: tuple[str, ...]
    header: str
    separator: str
    rows: tuple[str, ...]
    column_count: int

    def row_document(self, row_index: int) -> str:
        """Return caption/header plus exactly one source data row."""
        return "\n".join(
            (*self.preamble, self.header, self.separator, self.rows[row_index])
        )


def _split_markdown_row(line: str) -> tuple[str, ...] | None:
    """Split a leading/trailing-pipe row while respecting escaped pipes."""
    stripped = line.strip()
    if not stripped.startswith("|") or not stripped.endswith("|"):
        return None
    body = stripped[1:-1]
    parts = []
    start = 0
    for index, character in enumerate(body):
        if character != "|":
            continue
        backslashes = 0
        cursor = index - 1
        while cursor >= 0 and body[cursor] == "\\":
            backslashes += 1
            cursor -= 1
        if backslashes % 2 == 0:
            parts.append(body[start:index].strip())
            start = index + 1
    parts.append(body[start:].strip())
    cells = tuple(parts)
    return cells if cells else None


def _is_separator_row(line: str) -> bool:
    cells = _split_markdown_row(line)
    return bool(cells and all(_SEPARATOR_CELL_RE.fullmatch(cell) for cell in cells))


def source_table_has_single_headerless_row(
        row_count: object, column_header_flags: Sequence[object]) -> bool:
    """Return whether Docling identified one data row and no header row.

    Docling's Markdown exporter must put some row before the delimiter, so it
    serializes a headerless one-row source table as a header-only Markdown
    table.  Requiring a nonempty, explicitly false flag for every source cell
    keeps the exception source-attested and fail-closed when table semantics
    are missing or ambiguous.
    """
    return (
        isinstance(row_count, int)
        and not isinstance(row_count, bool)
        and row_count == 1
        and bool(column_header_flags)
        and all(flag is False for flag in column_header_flags)
    )


def normalize_source_table_markdown(
        markdown: str, *, row_count: object,
        column_header_flags: Sequence[object]) -> str:
    """Preserve a source-attested headerless row as Markdown table data.

    Only an otherwise exact header-plus-delimiter serialization is changed.
    A blank synthetic header is inserted and the source row is moved below
    the delimiter.  Existing data rows, ragged tables, non-table prose, and
    ambiguous source metadata are returned byte-for-byte unchanged.
    """
    if (not isinstance(markdown, str)
            or not source_table_has_single_headerless_row(
                row_count, column_header_flags)):
        return markdown
    lines = markdown.strip().splitlines()
    candidates: list[tuple[int, tuple[str, ...]]] = []
    for index in range(len(lines) - 1):
        header_cells = _split_markdown_row(lines[index])
        separator_cells = _split_markdown_row(lines[index + 1])
        if (header_cells is not None and separator_cells is not None
                and _is_separator_row(lines[index + 1])
                and len(header_cells) == len(separator_cells)):
            candidates.append((index, header_cells))
    if len(candidates) != 1:
        return markdown
    table_start, header_cells = candidates[0]
    if any(line.strip() for line in lines[table_start + 2:]):
        return markdown
    source_row = lines[table_start].rstrip()
    separator = lines[table_start + 1].rstrip()
    blank_header = "| " + " | ".join("" for _ in header_cells) + " |"
    return "\n".join((
        *lines[:table_start], blank_header, separator, source_row,
    ))


def _parse_markdown_table(markdown: str) -> MarkdownTable | None:
    """Parse one strict Markdown table, retaining its non-table preamble.

    Ambiguous or malformed layouts are deliberately rejected.  Producing no
    child is safer than turning prose or a partially recovered table into a
    misleading retrieval record.
    """
    if not isinstance(markdown, str) or not markdown.strip():
        return None
    lines = tuple(line.rstrip() for line in markdown.strip().splitlines()
                  if line.strip())
    table_start = None
    for index in range(len(lines) - 1):
        header_cells = _split_markdown_row(lines[index])
        separator_cells = _split_markdown_row(lines[index + 1])
        if (header_cells is not None and separator_cells is not None
                and _is_separator_row(lines[index + 1])
                and len(header_cells) == len(separator_cells)):
            table_start = index
            break
    if table_start is None or table_start + 2 >= len(lines):
        return None

    header = lines[table_start]
    separator = lines[table_start + 1]
    header_cells = _split_markdown_row(header)
    if header_cells is None:
        return None
    data_rows = lines[table_start + 2:]
    if not data_rows:
        return None
    for row in data_rows:
        cells = _split_markdown_row(row)
        if (cells is None or len(cells) != len(header_cells)
                or _is_separator_row(row)):
            return None
    return MarkdownTable(
        preamble=lines[:table_start],
        header=header,
        separator=separator,
        rows=data_rows,
        column_count=len(header_cells),
    )


def markdown_table_dimensions(markdown: str) -> tuple[int, int] | None:
    """Return ``(data rows, columns)`` for one strict Markdown table.

    The Markdown header and separator are structural and therefore never
    contribute to the row count. PDF-recovered tables use the same strict
    representation as native tables, including the one-column source-layout
    fallback, so callers do not need recovery-specific dimension heuristics.
    Malformed or ambiguous input fails closed with ``None``.
    """
    table = _parse_markdown_table(markdown)
    if table is None:
        return None
    return len(table.rows), table.column_count


def _is_nonnegative_int(value: object) -> bool:
    return isinstance(value, int) and not isinstance(value, bool) and value >= 0


def _is_positive_int(value: object) -> bool:
    return isinstance(value, int) and not isinstance(value, bool) and value > 0


def _table_source_key(
        metadata: dict[str, Any], fallback: object,
) -> tuple[str, str, str]:
    """Return one exact source-table family key, or a record-local fallback."""
    refs = sorted({
        item.get("ref")
        for item in metadata.get("source_items", [])
        if (isinstance(item, dict)
            and item.get("label") == "table"
            and isinstance(item.get("ref"), str)
            and item.get("ref"))
    }) if isinstance(metadata.get("source_items"), list) else []
    if len(refs) == 1:
        source_file = metadata.get("source_file")
        return (
            "source_ref",
            source_file if isinstance(source_file, str) else "",
            refs[0],
        )
    return "record", "", str(fallback)


def is_table_child(metadata: dict[str, Any] | None) -> bool:
    """Return whether metadata identifies a retrieval-only table child."""
    return isinstance(metadata, dict) and metadata.get("retrieval_role") == (
        TABLE_CHILD_ROLE)


def table_child_count(records: Sequence[dict]) -> int:
    """Count row-level retrieval children in one exact corpus generation."""
    return sum(
        is_table_child(record.get("metadata"))
        for record in records
        if isinstance(record, dict)
    )


def has_table_retrieval_metadata(records: Sequence[dict]) -> bool:
    """Return whether any record claims part of the table-family contract."""
    return any(
        isinstance(metadata := record.get("metadata"), dict)
        and (bool(_TABLE_RETRIEVAL_FIELDS.intersection(metadata))
             or TABLE_FRAGMENT_OCCURRENCE_FIELD in metadata)
        for record in records
        if isinstance(record, dict)
    )


def canonical_records(records: Sequence[dict]) -> list[dict]:
    """Exclude retrieval-only children from publication/study consumers."""
    return [
        record for record in records
        if isinstance(record, dict)
        and not is_table_child(record.get("metadata"))
    ]


def annotate_table_fragment_occurrences(
        records: Sequence[dict], *, stable_id_fn: Callable[[dict], str],
) -> int:
    """Disambiguate otherwise identical fragments of one source table.

    Row packing can legitimately produce byte-identical fragments when a
    source table repeats a long row.  Mark every member of each colliding
    primary group with its deterministic occurrence ordinal.  Ordinal zero is
    deliberately excluded from stable-ID material, preserving the pre-feature
    identity of the first published parent.
    """
    collision_groups: defaultdict[str, list[dict]] = defaultdict(list)
    for record in records:
        if not isinstance(record, dict):
            raise ValueError("table fragment records must be objects")
        metadata = record.get("metadata")
        if not isinstance(metadata, dict):
            raise ValueError("table fragment records require object metadata")
        if TABLE_FRAGMENT_OCCURRENCE_FIELD in metadata:
            raise ValueError("table fragment annotation cannot run twice")
        if (metadata.get("content_type") != "table"
                or metadata.get("content_source") != "table"
                or _parse_markdown_table(str(record.get("text") or ""))
                is None):
            continue
        stable_id = stable_id_fn(record)
        if not isinstance(stable_id, str) or not stable_id:
            raise ValueError("table fragment stable ID must be nonempty")
        collision_groups[stable_id].append(record)

    annotated = 0
    for group in collision_groups.values():
        if len(group) < 2:
            continue
        for occurrence, record in enumerate(group):
            record["metadata"][TABLE_FRAGMENT_OCCURRENCE_FIELD] = occurrence
            annotated += 1
    return annotated


def expand_table_records(
        records: Sequence[dict], *,
        stable_id_fn: Callable[[dict], str],
        token_count_fn: Callable[[str], int],
        min_data_rows: int = DEFAULT_TABLE_CHILD_MIN_ROWS,
) -> list[dict]:
    """Return primary records plus row children for eligible large tables.

    All input records are copied.  Children are appended after the primary
    corpus so they cannot alter the stable adjacency of ordinary source chunks.
    """
    if (isinstance(min_data_rows, bool) or not isinstance(min_data_rows, int)
            or min_data_rows < 2):
        raise ValueError("min_data_rows must be an integer of at least 2")

    primary_records = copy.deepcopy(list(records))
    candidates = []
    source_stats: defaultdict[tuple[str, str, str], dict[str, int]] = (
        defaultdict(lambda: {"rows": 0, "fragments": 0}))
    source_schemas: dict[
        tuple[str, str, str], tuple[tuple[str, ...], str, str, int]
    ] = {}
    for record_index, record in enumerate(primary_records):
        if not isinstance(record, dict):
            raise ValueError("table retrieval records must be objects")
        metadata = record.get("metadata")
        if not isinstance(metadata, dict):
            raise ValueError("table retrieval records require object metadata")
        if any(field in metadata for field in _TABLE_RETRIEVAL_FIELDS):
            raise ValueError("table retrieval expansion cannot run twice")
        if (metadata.get("content_type") != "table"
                or metadata.get("content_source") != "table"):
            continue
        table = _parse_markdown_table(str(record.get("text") or ""))
        if table is None:
            continue
        source_key = _table_source_key(metadata, record_index)
        source_schema = (
            table.preamble, table.header, table.separator,
            table.column_count,
        )
        previous_schema = source_schemas.setdefault(source_key, source_schema)
        if previous_schema != source_schema:
            raise ValueError(
                "source table fragments have inconsistent Markdown schemas")
        candidates.append((record, table, source_key))
        source_stats[source_key]["rows"] += len(table.rows)
        source_stats[source_key]["fragments"] += 1

    children: list[dict] = []
    parent_stable_ids: set[str] = set()
    for record, table, source_key in candidates:
        metadata = record["metadata"]
        source_stat = source_stats[source_key]
        if source_stat["rows"] < min_data_rows:
            continue
        if len(table.rows) > MAX_TABLE_CHILDREN_PER_PARENT:
            raise ValueError(
                "table retrieval expansion exceeds the per-parent child cap")
        if len(children) + len(table.rows) > MAX_TABLE_CHILDREN_PER_CORPUS:
            raise ValueError(
                "table retrieval expansion exceeds the corpus child cap")

        parent_stable_id = stable_id_fn(record)
        if not isinstance(parent_stable_id, str) or not parent_stable_id:
            raise ValueError("table parent stable ID must be a nonempty string")
        if parent_stable_id in parent_stable_ids:
            raise ValueError(
                "table parent stable IDs collide; annotate repeated source "
                "fragments before expansion")
        parent_stable_ids.add(parent_stable_id)
        child_count = len(table.rows)
        metadata.update({
            "table_retrieval_schema_version": TABLE_RETRIEVAL_SCHEMA_VERSION,
            "retrieval_role": TABLE_PARENT_ROLE,
            "table_parent_stable_id": parent_stable_id,
            "table_child_count": child_count,
            "table_source_row_count": source_stat["rows"],
            "table_source_fragment_count": source_stat["fragments"],
            "table_rows": child_count,
            "table_cols": table.column_count,
        })

        for child_index in range(child_count):
            child = copy.deepcopy(record)
            child_text = table.row_document(child_index)
            child["text"] = child_text
            child_metadata = child["metadata"]
            for field in _RETRIEVAL_LINKAGE_FIELDS:
                child_metadata.pop(field, None)
            child_metadata.update({
                "table_retrieval_schema_version": (
                    TABLE_RETRIEVAL_SCHEMA_VERSION),
                "retrieval_role": TABLE_CHILD_ROLE,
                "table_parent_stable_id": parent_stable_id,
                "table_child_index": child_index,
                "table_child_count": child_count,
                "table_rows": 1,
                "table_cols": table.column_count,
                "token_count": int(token_count_fn(child_text)),
            })
            child_metadata.pop("embedding_token_count", None)
            children.append(child)
    return primary_records + children


def _add_issue(issues: defaultdict[str, list[int]], name: str,
               index: int) -> None:
    issues[name].append(index)


def _table_retrieval_summary(
        records: Sequence[dict], *, stable_ids: Sequence[str],
        min_data_rows: int = DEFAULT_TABLE_CHILD_MIN_ROWS,
        source_table_dimensions: Mapping[str, tuple[int, int]] | None = None,
) -> dict[str, Any]:
    """Recompute table-family invariants for a corpus-quality report."""
    if len(records) != len(stable_ids):
        raise ValueError("stable IDs must align one-to-one with records")
    if source_table_dimensions is not None:
        for ref, dimensions in source_table_dimensions.items():
            if (not isinstance(ref, str) or not ref
                    or not isinstance(dimensions, tuple)
                    or len(dimensions) != 2
                    or not _is_nonnegative_int(dimensions[0])
                    or not _is_positive_int(dimensions[1])):
                raise ValueError("source table dimensions are invalid")

    issues: defaultdict[str, list[int]] = defaultdict(list)
    families: defaultdict[str, dict[str, list[int]]] = defaultdict(
        lambda: {"parents": [], "children": []})
    source_tables: defaultdict[
        tuple[str, str, str], list[tuple[int, MarkdownTable]]
    ] = defaultdict(list)
    fragment_identity_groups: defaultdict[tuple, list[int]] = defaultdict(list)
    parent_tables = 0
    expanded_parents = 0
    child_chunks = 0
    child_record_indexes = []
    seen_child = False

    for index, (record, stable_id) in enumerate(zip(records, stable_ids)):
        metadata = record.get("metadata") if isinstance(record, dict) else None
        if not isinstance(metadata, dict):
            _add_issue(issues, "invalid_metadata", index)
            continue
        role = metadata.get("retrieval_role")
        if role == TABLE_CHILD_ROLE:
            seen_child = True
        elif seen_child:
            _add_issue(issues, "canonical_record_after_children", index)
        present_fields = _TABLE_RETRIEVAL_FIELDS.intersection(metadata)
        is_primary_table = (
            role != TABLE_CHILD_ROLE
            and metadata.get("content_type") == "table"
            and metadata.get("content_source") == "table"
        )
        primary_table = (
            _parse_markdown_table(str(record.get("text") or ""))
            if is_primary_table else None)
        if primary_table is not None:
            source_key = _table_source_key(metadata, stable_id)
            source_tables[source_key].append((index, primary_table))
            source_refs = tuple(sorted({
                item.get("ref")
                for item in metadata.get("source_items", [])
                if (isinstance(item, dict)
                    and isinstance(item.get("ref"), str)
                    and item.get("ref"))
            })) if isinstance(metadata.get("source_items"), list) else ()
            fragment_identity_groups[(
                source_key,
                str(record.get("text") or ""),
                metadata.get("source_file"),
                metadata.get("page_start"),
                metadata.get("page_end"),
                metadata.get("page_range"),
                source_refs,
            )].append(index)
        occurrence = metadata.get(TABLE_FRAGMENT_OCCURRENCE_FIELD)
        if (TABLE_FRAGMENT_OCCURRENCE_FIELD in metadata
                and not _is_nonnegative_int(occurrence)):
            _add_issue(issues, "invalid_table_fragment_occurrence", index)
        if (TABLE_FRAGMENT_OCCURRENCE_FIELD in metadata
                and role != TABLE_CHILD_ROLE and primary_table is None):
            _add_issue(issues, "unexpected_table_fragment_occurrence", index)
        if ("table_recovered_from_pdf" in metadata
                and not isinstance(
                    metadata.get("table_recovered_from_pdf"), bool)):
            _add_issue(issues, "invalid_table_recovery_flag", index)
        if role is None:
            if present_fields:
                _add_issue(issues, "orphan_retrieval_fields", index)
            if (metadata.get("content_type") == "table"
                    and metadata.get("content_source") == "table"):
                parent_tables += 1
            continue
        if role not in {TABLE_PARENT_ROLE, TABLE_CHILD_ROLE}:
            _add_issue(issues, "unknown_retrieval_role", index)
            continue
        schema_version = metadata.get("table_retrieval_schema_version")
        if (not _is_positive_int(schema_version)
                or schema_version != TABLE_RETRIEVAL_SCHEMA_VERSION):
            _add_issue(issues, "schema_version_mismatch", index)
        if (metadata.get("content_type") != "table"
                or metadata.get("content_source") != "table"):
            _add_issue(issues, "non_table_retrieval_record", index)

        parent_id = metadata.get("table_parent_stable_id")
        if not isinstance(parent_id, str) or not parent_id:
            _add_issue(issues, "invalid_parent_stable_id", index)
            parent_id = f"__invalid_parent_{index}"
        families[parent_id][
            "parents" if role == TABLE_PARENT_ROLE else "children"
        ].append(index)

        child_count = metadata.get("table_child_count")
        if not _is_positive_int(child_count):
            _add_issue(issues, "invalid_child_count", index)
        elif child_count > MAX_TABLE_CHILDREN_PER_PARENT:
            _add_issue(
                issues, "declared_child_count_exceeds_parent_cap", index)
        if role == TABLE_PARENT_ROLE:
            parent_tables += 1
            expanded_parents += 1
            if parent_id != stable_id:
                _add_issue(issues, "parent_identity_mismatch", index)
            if "table_child_index" in metadata:
                _add_issue(issues, "parent_has_child_index", index)
            if primary_table is None:
                _add_issue(issues, "invalid_parent_table", index)
            else:
                table_rows = metadata.get("table_rows")
                table_cols = metadata.get("table_cols")
                if (not _is_positive_int(table_rows)
                        or table_rows != len(primary_table.rows)):
                    _add_issue(issues, "parent_table_rows_mismatch", index)
                if (not _is_positive_int(table_cols)
                        or table_cols != primary_table.column_count):
                    _add_issue(issues, "parent_table_cols_mismatch", index)
        else:
            child_chunks += 1
            child_record_indexes.append(index)
            if not _is_nonnegative_int(metadata.get("table_child_index")):
                _add_issue(issues, "invalid_child_index", index)
            child_table = _parse_markdown_table(
                str(record.get("text") or ""))
            if child_table is None or len(child_table.rows) != 1:
                _add_issue(issues, "invalid_child_table", index)
            else:
                table_rows = metadata.get("table_rows")
                table_cols = metadata.get("table_cols")
                if not _is_positive_int(table_rows) or table_rows != 1:
                    _add_issue(issues, "child_table_rows_mismatch", index)
                if (not _is_positive_int(table_cols)
                        or table_cols != child_table.column_count):
                    _add_issue(issues, "child_table_cols_mismatch", index)
        for field in (
                "table_source_row_count", "table_source_fragment_count"):
            if not _is_positive_int(metadata.get(field)):
                _add_issue(issues, f"invalid_{field}", index)

    for index in child_record_indexes[MAX_TABLE_CHILDREN_PER_CORPUS:]:
        _add_issue(issues, "corpus_child_count_exceeds_cap", index)

    for indexes in fragment_identity_groups.values():
        occurrences = [
            records[index]["metadata"].get(TABLE_FRAGMENT_OCCURRENCE_FIELD)
            for index in indexes
        ]
        if len(indexes) > 1:
            if occurrences != list(range(len(indexes))):
                for index in indexes:
                    _add_issue(
                        issues, "table_fragment_occurrences_invalid", index)
        elif TABLE_FRAGMENT_OCCURRENCE_FIELD in records[
                indexes[0]]["metadata"]:
            _add_issue(
                issues, "unexpected_table_fragment_occurrence", indexes[0])

    expansion_present = bool(expanded_parents or child_chunks)
    for source_key, fragments in source_tables.items():
        source_row_count = sum(len(table.rows) for _, table in fragments)
        source_fragment_count = len(fragments)
        source_is_eligible = source_row_count >= min_data_rows
        source_schemas = {
            (table.preamble, table.header, table.separator, table.column_count)
            for _, table in fragments
        }
        if len(source_schemas) != 1:
            for index, _table in fragments:
                _add_issue(issues, "source_table_schema_mismatch", index)
        recovery_values = {
            records[index]["metadata"].get(
                "table_recovered_from_pdf", False) is True
            for index, _table in fragments
        }
        if len(recovery_values) != 1:
            for index, _table in fragments:
                _add_issue(issues, "source_table_recovery_mismatch", index)
        source_recovered_from_pdf = True in recovery_values
        if (source_table_dimensions is not None
                and not source_recovered_from_pdf
                and source_key[0] == "source_ref"
                and source_key[2] in source_table_dimensions):
            expected_rows, expected_cols = source_table_dimensions[
                source_key[2]]
            if source_row_count != expected_rows:
                for index, _table in fragments:
                    _add_issue(
                        issues, "source_native_row_count_mismatch", index)
            if any(table.column_count != expected_cols
                   for _, table in fragments):
                for index, _table in fragments:
                    _add_issue(
                        issues, "source_native_column_count_mismatch", index)
        for index, _table in fragments:
            metadata = records[index]["metadata"]
            is_expanded = metadata.get("retrieval_role") == TABLE_PARENT_ROLE
            if expansion_present and source_is_eligible and not is_expanded:
                _add_issue(issues, "eligible_source_table_not_expanded", index)
            if not is_expanded:
                continue
            if not source_is_eligible:
                _add_issue(issues, "ineligible_source_table_expanded", index)
            source_rows = metadata.get("table_source_row_count")
            source_fragments = metadata.get("table_source_fragment_count")
            if (not _is_positive_int(source_rows)
                    or source_rows != source_row_count):
                _add_issue(issues, "source_table_row_count_mismatch", index)
            if (not _is_positive_int(source_fragments)
                    or source_fragments != source_fragment_count):
                _add_issue(
                    issues, "source_table_fragment_count_mismatch", index)

    for _parent_id, family in sorted(families.items()):
        parents = family["parents"]
        children = family["children"]
        if len(children) > MAX_TABLE_CHILDREN_PER_PARENT:
            for index in (parents or children[:1]):
                _add_issue(
                    issues, "family_child_count_exceeds_parent_cap", index)
        if len(parents) != 1:
            for index in parents + children:
                _add_issue(
                    issues,
                    "missing_parent" if not parents else "duplicate_parent",
                    index,
                )
            continue
        parent_index = parents[0]
        parent = records[parent_index]
        parent_metadata = parent["metadata"]
        expected_count = parent_metadata.get("table_child_count")
        if not _is_positive_int(expected_count):
            continue
        if len(children) != expected_count:
            _add_issue(issues, "family_child_count_mismatch", parent_index)

        valid_indexes = []
        for child_index in children:
            child_metadata = records[child_index]["metadata"]
            value = child_metadata.get("table_child_index")
            if _is_nonnegative_int(value):
                valid_indexes.append(value)
            if child_metadata.get("table_child_count") != expected_count:
                _add_issue(issues, "child_count_mismatch", child_index)
            if any(child_metadata.get(field) != parent_metadata.get(field)
                   for field in _FAMILY_METADATA_FIELDS):
                _add_issue(issues, "family_metadata_mismatch", child_index)
        if valid_indexes != list(range(expected_count)):
            _add_issue(issues, "family_child_indexes_invalid", parent_index)

        parent_table = _parse_markdown_table(str(parent.get("text") or ""))
        if parent_table is None or len(parent_table.rows) != expected_count:
            _add_issue(issues, "parent_row_count_mismatch", parent_index)
            continue
        for child_record_index in children:
            child = records[child_record_index]
            child_metadata = child["metadata"]
            row_index = child_metadata.get("table_child_index")
            if not _is_nonnegative_int(row_index) or row_index >= expected_count:
                continue
            if child.get("text") != parent_table.row_document(row_index):
                _add_issue(issues, "child_text_mismatch", child_record_index)

    return {
        "schema_version": TABLE_RETRIEVAL_SCHEMA_VERSION,
        "parent_tables": parent_tables,
        "expanded_parents": expanded_parents,
        "child_chunks": child_chunks,
        "issues": {
            key: sorted(set(indexes)) for key, indexes in sorted(issues.items())
        },
    }


def validated_table_family_members(
        records: Sequence[dict], *, stable_id_fn: Callable[[dict], str],
) -> dict[str, frozenset[str]]:
    """Return attested parent/member IDs for every complete table family.

    Evaluation callers must not infer family equivalence from a retrieved
    result's metadata: a backend payload can be stale or malformed, and lineage
    alone cannot say which sibling row answers a particular query.  This helper
    first recomputes the complete corpus-level family contract, then returns the
    members that a separately reviewed judgment may select explicitly.
    """
    stable_ids = tuple(stable_id_fn(record) for record in records)
    if (any(not isinstance(value, str) or not value for value in stable_ids)
            or len(set(stable_ids)) != len(stable_ids)):
        raise ValueError("table-family validation requires unique stable IDs")
    summary = _table_retrieval_summary(records, stable_ids=stable_ids)
    if summary["issues"]:
        issue_names = ", ".join(summary["issues"])
        raise ValueError(
            "table-family metadata fails corpus attestation: " + issue_names)

    families: defaultdict[str, set[str]] = defaultdict(set)
    for record, stable_id in zip(records, stable_ids):
        metadata = record.get("metadata")
        if not isinstance(metadata, dict):
            continue
        role = metadata.get("retrieval_role")
        if role not in {TABLE_PARENT_ROLE, TABLE_CHILD_ROLE}:
            continue
        parent_id = metadata["table_parent_stable_id"]
        families[parent_id].add(stable_id)
    return {
        parent_id: frozenset(member_ids)
        for parent_id, member_ids in sorted(families.items())
    }


def table_candidate_keep_indexes(metadatas: Sequence[dict]) -> list[int]:
    """Return candidate indexes after parent/child overlap suppression."""
    child_families = {
        metadata.get("table_parent_stable_id")
        for metadata in metadatas
        if (isinstance(metadata, dict)
            and metadata.get("retrieval_role") == TABLE_CHILD_ROLE
            and isinstance(metadata.get("table_parent_stable_id"), str)
            and metadata.get("table_parent_stable_id"))
    }
    return [
        index for index, metadata in enumerate(metadatas)
        if not (
            isinstance(metadata, dict)
            and metadata.get("retrieval_role") == TABLE_PARENT_ROLE
            and metadata.get("table_parent_stable_id") in child_families
        )
    ]


def collapse_table_families(
        documents: Sequence[str], metadatas: Sequence[dict],
        scores: Sequence[float],
) -> tuple[list[str], list[dict], list[float]]:
    """Suppress a table parent when one of its row children was retrieved.

    Distinct row children remain independently ranked evidence.  Collapsing all
    siblings would make questions whose answer spans multiple rows impossible.
    """
    if not (len(documents) == len(metadatas) == len(scores)):
        raise ValueError("table-family candidates must be aligned")
    collapsed_documents = []
    collapsed_metadatas = []
    collapsed_scores = []
    for index in table_candidate_keep_indexes(metadatas):
        document = documents[index]
        metadata = metadatas[index]
        score = scores[index]
        collapsed_documents.append(str(document))
        collapsed_metadatas.append(dict(metadata or {}))
        collapsed_scores.append(float(score))
    return collapsed_documents, collapsed_metadatas, collapsed_scores
