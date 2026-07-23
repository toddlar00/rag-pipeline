"""Deterministic corpus-quality reporting and validation policy.

This module is intentionally standard-library-only.  Runtime orchestration,
Docling objects, atomic publication, and stable-ID generation remain in
``rag.py``; this leaf receives plain records and serialized document data.
"""

from __future__ import annotations

import json
import hashlib
import math
import re
from collections import Counter, defaultdict
from pathlib import Path
from typing import Iterable, Sequence


QUALITY_REPORT_SCHEMA_VERSION = 1
SOURCE_LINEAGE_SCHEMA_VERSION = 1
MAX_QUALITY_REPORT_BYTES = 16 * 1024 * 1024

_SOURCE_LABELS = {
    "text", "list_item", "footnote", "caption", "code", "table",
}
_KNOWN_CONTENT_TYPES = {
    "case_opinion", "notes_and_questions", "author_narrative",
    "statutory_excerpt", "table", "footnote", "chapter_introduction",
}
_SOURCE_COLLECTIONS = (
    "texts", "tables", "pictures", "key_value_items", "form_items",
)
_CANONICAL_TEXT_RE = re.compile(r"[^a-z0-9]+")
_EMPHASIS_BOILERPLATE_RE = re.compile(
    r"^\**\s*all\s+emphasis\s+added\.?\s*\**$", re.IGNORECASE)
_RULE_LANGUAGE_HEADING_RE = re.compile(
    r"^\s*rule\s+language\s*\**\s*$", re.IGNORECASE)
_AUTHORS_EXPLANATION_HEADING_RE = re.compile(
    r"^\s*authors?['’]\s+explanation\s*\**\s*$", re.IGNORECASE)

_HARD_CHECK_NAMES = (
    "nonempty_corpus",
    "unique_stable_ids",
    "valid_chunk_hashes",
    "source_inventory_integrity",
    "source_lineage_schema",
    "chunks_have_source_lineage",
    "eligible_source_items_represented",
    "source_refs_resolve",
    "source_lineage_matches_source",
    "source_tables_represented",
    "page_metadata_valid",
    "page_regressions",
    "structural_ranges_excluded",
    "contiguous_chunk_indexes",
    "normalization_invariants",
    "raw_token_counts_present",
    "embedding_counts_present",
    "embedding_limit",
    "known_content_types",
    "classification_invariants",
    "entity_invariants",
    "table_invariants",
)
_WARNING_CHECK_NAMES = ("canonical_text_duplicates",)
_SCHEMA_V1_CHECK_NAMES = frozenset(
    _HARD_CHECK_NAMES + _WARNING_CHECK_NAMES)
_ZERO_COUNT_CHECK_NAMES = frozenset({
    "valid_chunk_hashes",
    "source_inventory_integrity",
    "source_lineage_schema",
    "chunks_have_source_lineage",
    "eligible_source_items_represented",
    "source_refs_resolve",
    "source_lineage_matches_source",
    "source_tables_represented",
    "page_metadata_valid",
    "page_regressions",
    "structural_ranges_excluded",
    "contiguous_chunk_indexes",
    "normalization_invariants",
    "embedding_limit",
    "classification_invariants",
    "entity_invariants",
    "table_invariants",
})


def quality_report_path(chunks_path: Path) -> Path:
    """Return the canonical adjacent report path for a chunks artifact."""
    chunks_path = Path(chunks_path)
    return chunks_path.with_name(f"{chunks_path.stem}.quality.json")


def _sha256_lines(lines: Iterable[str]) -> str:
    payload = "\n".join(sorted(lines)).encode("utf-8")
    return hashlib.sha256(payload).hexdigest()


def _nearest_rank(values: Sequence[int], percentile: int) -> int | None:
    if not values:
        return None
    ordered = sorted(values)
    rank = max(1, (len(ordered) * percentile + 99) // 100)
    return ordered[min(rank, len(ordered)) - 1]


def _is_nonnegative_int(value: object) -> bool:
    return (isinstance(value, int) and not isinstance(value, bool)
            and value >= 0)


def _headings(metadata: dict) -> tuple[str, ...]:
    values = metadata.get("headings")
    if isinstance(values, str):
        return (values,)
    if isinstance(values, list):
        return tuple(value for value in values if isinstance(value, str))
    return ()


def _allowed_semantic_lane_regression(
        previous_metadata: dict, metadata: dict) -> bool:
    """Recognize the source-order reset between parallel textbook lanes."""
    previous_path = previous_metadata.get("section_path")
    return (
        isinstance(previous_path, str)
        and bool(previous_path)
        and metadata.get("section_path") == previous_path
        and previous_metadata.get("content_type") == "statutory_excerpt"
        and metadata.get("content_type") == "author_narrative"
        and any(_RULE_LANGUAGE_HEADING_RE.fullmatch(value)
                for value in _headings(previous_metadata))
        and any(_AUTHORS_EXPLANATION_HEADING_RE.fullmatch(value)
                for value in _headings(metadata))
    )


def _valid_page_regression_evidence(entry: object, *, allowed: bool) -> bool:
    if not isinstance(entry, dict) or set(entry) != {
            "chunk_index", "previous_chunk_index", "previous_page_start",
            "page_start", "section_path", "previous_content_type",
            "content_type", "previous_headings", "headings", "reason",
    }:
        return False
    index = entry["chunk_index"]
    previous_index = entry["previous_chunk_index"]
    previous_page = entry["previous_page_start"]
    page = entry["page_start"]
    if (not _is_nonnegative_int(index) or index < 1
            or not _is_nonnegative_int(previous_index)
            or previous_index != index - 1
            or not _is_nonnegative_int(previous_page) or previous_page < 1
            or not _is_nonnegative_int(page) or page < 1
            or page >= previous_page
            or not isinstance(entry["section_path"], str)
            or not isinstance(entry["previous_headings"], list)
            or any(not isinstance(value, str)
                   for value in entry["previous_headings"])
            or not isinstance(entry["headings"], list)
            or any(not isinstance(value, str) for value in entry["headings"])):
        return False
    if not allowed:
        return entry["reason"] == "unexpected_page_decrease"
    if entry["reason"] != "rule_language_to_authors_explanation":
        return False
    previous_metadata = {
        "section_path": entry["section_path"],
        "content_type": entry["previous_content_type"],
        "headings": entry["previous_headings"],
    }
    metadata = {
        "section_path": entry["section_path"],
        "content_type": entry["content_type"],
        "headings": entry["headings"],
    }
    return _allowed_semantic_lane_regression(previous_metadata, metadata)


def _label(value: object) -> str:
    label = str(value or "").strip().lower()
    return label.rsplit(".", 1)[-1]


def _item_pages(item: dict) -> tuple[int, ...]:
    pages = []
    for provenance in item.get("prov") or []:
        if not isinstance(provenance, dict):
            continue
        page = provenance.get("page_no")
        if isinstance(page, int) and not isinstance(page, bool) and page > 0:
            pages.append(page)
    return tuple(dict.fromkeys(pages))


def _source_spans(item: dict) -> list[dict]:
    """Serialize source provenance exactly as ``rag`` serializes lineage."""
    spans = []
    for provenance in item.get("prov") or []:
        if not isinstance(provenance, dict):
            continue
        page = provenance.get("page_no")
        if not isinstance(page, int) or isinstance(page, bool) or page < 1:
            continue
        span: dict[str, object] = {"page": page}
        bbox = provenance.get("bbox")
        if isinstance(bbox, dict):
            coordinates = []
            for name in ("l", "t", "r", "b"):
                value = bbox.get(name)
                if (not isinstance(value, (int, float))
                        or isinstance(value, bool)
                        or not math.isfinite(value)):
                    coordinates = []
                    break
                coordinates.append(round(float(value), 3))
            if len(coordinates) == 4:
                span["bbox"] = coordinates
                origin = str(bbox.get("coord_origin") or "")
                if origin:
                    span["origin"] = origin.upper()
        spans.append(span)
    spans.sort(key=lambda value: (
        value["page"], value.get("bbox", []), value.get("origin", "")))
    return spans


def _relationship_ref(value: object) -> str:
    if not isinstance(value, dict):
        return ""
    ref = value.get("cref")
    return ref if isinstance(ref, str) else ""


def _inside_ranges(page: int,
                   structural_ranges: Sequence[tuple[int, int]]) -> bool:
    return any(start <= page <= end for start, end in structural_ranges)


def _source_exclusion_reason(
        item: dict, *, structural_ranges: Sequence[tuple[int, int]]) -> str | None:
    label = _label(item.get("label"))
    if label not in _SOURCE_LABELS:
        return f"label:{label or 'unknown'}"
    content_layer = str(item.get("content_layer") or "").lower()
    if "furniture" in content_layer:
        return "furniture"
    pages = _item_pages(item)
    if not pages:
        return "no_provenance"
    if all(_inside_ranges(page, structural_ranges) for page in pages):
        return "structural_range"
    text = str(item.get("text") or item.get("orig") or "").strip()
    if label != "table" and not text:
        return "empty"
    if _EMPHASIS_BOILERPLATE_RE.fullmatch(text):
        return "editorial_boilerplate"
    if "This and other authors' explanations draw" in text:
        return "editorial_boilerplate"
    return None


def source_inventory(
        document: dict, *,
        structural_ranges: Iterable[tuple[int, int]],
) -> tuple[
        dict[str, dict], dict[str, dict], dict[str, int],
        dict[str, list[object]],
]:
    """Return items, eligible items, exclusions, and integrity issues.

    The eligible set is deliberately source-identity based.  Textual fidelity
    remains a separate concern, while this gate proves that no substantive
    source object silently disappears during chunk preparation or deduplication.
    """
    ranges = tuple(sorted(set(structural_ranges)))
    all_items: dict[str, dict] = {}
    raw_items: dict[str, dict] = {}
    item_locations: dict[str, str] = {}
    eligible: dict[str, dict] = {}
    exclusions: Counter[str] = Counter()
    integrity_issues: defaultdict[str, list[object]] = defaultdict(list)
    if not isinstance(document, dict):
        return {}, {}, {}, {"invalid_document": ["root"]}
    for collection in _SOURCE_COLLECTIONS:
        values = document.get(collection, [])
        if values is None:
            values = []
        if not isinstance(values, list):
            integrity_issues["invalid_collection"].append(collection)
            continue
        expected_ref = re.compile(
            rf"^#/{re.escape(collection)}/[0-9]+$")
        for index, item in enumerate(values):
            location = f"{collection}[{index}]"
            if not isinstance(item, dict):
                integrity_issues["invalid_item"].append(location)
                continue
            ref = item.get("self_ref")
            if (not isinstance(ref, str)
                    or expected_ref.fullmatch(ref) is None):
                integrity_issues["invalid_ref"].append(location)
                continue
            if ref in all_items:
                integrity_issues["duplicate_ref"].append({
                    "ref": ref,
                    "first": item_locations[ref],
                    "duplicate": location,
                })
                continue
            provenance = item.get("prov")
            if not isinstance(provenance, list) or not provenance:
                integrity_issues["invalid_provenance"].append(
                    f"{location}.prov")
            else:
                for prov_index, prov in enumerate(provenance):
                    prov_location = f"{location}.prov[{prov_index}]"
                    if not isinstance(prov, dict):
                        integrity_issues["invalid_provenance"].append(
                            prov_location)
                        continue
                    page = prov.get("page_no")
                    bbox = prov.get("bbox")
                    valid_bbox = bbox is None or (
                        isinstance(bbox, dict)
                        and all(
                            isinstance(bbox.get(name), (int, float))
                            and not isinstance(bbox.get(name), bool)
                            and math.isfinite(float(bbox[name]))
                            for name in ("l", "t", "r", "b"))
                    )
                    if (not isinstance(page, int) or isinstance(page, bool)
                            or page < 1 or not valid_bbox):
                        integrity_issues["invalid_provenance"].append(
                            prov_location)
            descriptor = {
                "label": _label(item.get("label")),
                "pages": list(_item_pages(item)),
                "spans": _source_spans(item),
                "parent_refs": [],
            }
            all_items[ref] = descriptor
            raw_items[ref] = item
            item_locations[ref] = location
            reason = _source_exclusion_reason(
                item, structural_ranges=ranges)
            if reason is None:
                eligible[ref] = descriptor
            else:
                exclusions[reason] += 1
    parent_refs_by_child: defaultdict[str, set[str]] = defaultdict(set)
    known_refs = set(raw_items)
    for parent_ref, item in raw_items.items():
        parent_value = item.get("parent")
        direct_parent = _relationship_ref(parent_value)
        if parent_value is not None and not direct_parent:
            integrity_issues["invalid_relationship"].append(
                f"{item_locations[parent_ref]}.parent")
        if direct_parent in known_refs:
            parent_refs_by_child[parent_ref].add(direct_parent)
        for relationship in ("captions", "footnotes", "children"):
            values = item.get(relationship, [])
            if values is None:
                values = []
            if not isinstance(values, list):
                integrity_issues["invalid_relationship"].append(
                    f"{item_locations[parent_ref]}.{relationship}")
                continue
            for relation_index, value in enumerate(values):
                child_ref = _relationship_ref(value)
                relation_location = (
                    f"{item_locations[parent_ref]}.{relationship}"
                    f"[{relation_index}]")
                if not child_ref:
                    integrity_issues["invalid_relationship"].append(
                        relation_location)
                elif child_ref not in known_refs:
                    integrity_issues["dangling_relationship"].append({
                        "location": relation_location,
                        "ref": child_ref,
                    })
                else:
                    parent_refs_by_child[child_ref].add(parent_ref)
    for ref, descriptor in all_items.items():
        descriptor["parent_refs"] = sorted(parent_refs_by_child.get(ref, ()))
    return (
        all_items,
        eligible,
        dict(sorted(exclusions.items())),
        {key: values for key, values in sorted(integrity_issues.items())},
    )


def _canonical_text(text: str) -> str:
    return _CANONICAL_TEXT_RE.sub("", text.casefold())


def _source_refs(record: dict) -> tuple[set[str], int, list[dict]]:
    metadata = record.get("metadata") or {}
    values = metadata.get("source_items")
    if not isinstance(values, list):
        return set(), 1, []
    refs: set[str] = set()
    invalid = 0
    valid_entries = []
    for item in values:
        if not isinstance(item, dict):
            invalid += 1
            continue
        ref = item.get("ref")
        spans = item.get("spans")
        parent_refs = item.get("parent_refs", [])
        label = item.get("label")
        if (not isinstance(ref, str) or not ref
                or not isinstance(label, str) or not label
                or not isinstance(spans, list)
                or not isinstance(parent_refs, list)
                or any(not isinstance(parent, str) or not parent
                       for parent in parent_refs)):
            invalid += 1
            continue
        entry_valid = True
        for span in spans:
            if not isinstance(span, dict):
                invalid += 1
                entry_valid = False
                continue
            page = span.get("page")
            bbox = span.get("bbox")
            if (not isinstance(page, int) or isinstance(page, bool) or page < 1
                    or (bbox is not None and (
                        not isinstance(bbox, list) or len(bbox) != 4
                        or any(not isinstance(value, (int, float))
                               or isinstance(value, bool)
                               or not math.isfinite(value)
                               for value in bbox)))):
                invalid += 1
                entry_valid = False
            origin = span.get("origin")
            if origin is not None and (
                    not isinstance(origin, str) or not origin):
                invalid += 1
                entry_valid = False
        if ref in refs:
            invalid += 1
            entry_valid = False
        refs.add(ref)
        if entry_valid:
            valid_entries.append(item)
    return refs, invalid, valid_entries


def _check(name: str, *, failed: bool, observed: object,
           required: object, warning: bool = False) -> dict:
    return {
        "name": name,
        "status": "warn" if warning and failed else "fail" if failed else "pass",
        "observed": observed,
        "required": required,
    }


def build_quality_report(
        *, records: list[dict], stable_ids: list[str],
        chunk_hashes: list[str], document: dict,
        structural_ranges: Iterable[tuple[int, int]],
        recovered_table_refs: Iterable[str], source_name: str,
        source_sha256: str, chunks_name: str, chunks_sha256: str,
        chunks_size: int, parameters_sha256: str,
        embedding_model: str, embedding_limit: int | None,
) -> dict:
    """Build a deterministic, release-gating report for one chunks artifact."""
    if len(stable_ids) != len(records) or len(chunk_hashes) != len(records):
        raise ValueError(
            "stable_ids and chunk_hashes must align one-to-one with records")

    ranges = tuple(sorted(set(structural_ranges)))
    (all_items, eligible_items, exclusion_counts,
     source_inventory_issues) = source_inventory(
        document, structural_ranges=ranges)

    represented_refs: set[str] = set()
    chunks_without_lineage = []
    invalid_lineage_entries = 0
    lineage_metadata_mismatches = []
    lineage_schema_issues = []
    page_metadata_issues = []
    structural_leaks = []
    chunk_index_issues = []
    page_regressions = []
    allowed_page_regressions = []
    unexpected_page_regressions = []
    normalization_issues: defaultdict[str, list[int]] = defaultdict(list)
    classification_issues: defaultdict[str, list[int]] = defaultdict(list)
    entity_issues: defaultdict[str, list[int]] = defaultdict(list)
    table_issues: defaultdict[str, list[int]] = defaultdict(list)
    content_types: Counter[str] = Counter()
    content_sources: Counter[str] = Counter()
    chapters: Counter[str] = Counter()
    raw_counts = []
    embedding_counts = []
    canonical_groups: defaultdict[str, list[int]] = defaultdict(list)
    previous_page: int | None = None
    previous_page_index: int | None = None
    previous_page_metadata: dict | None = None

    for index, record in enumerate(records):
        metadata = record.get("metadata") or {}
        lineage_version = metadata.get("source_lineage_schema_version")
        if (not isinstance(lineage_version, int)
                or isinstance(lineage_version, bool)
                or lineage_version != SOURCE_LINEAGE_SCHEMA_VERSION):
            lineage_schema_issues.append(index)
        refs, invalid, lineage_entries = _source_refs(record)
        represented_refs.update(refs)
        invalid_lineage_entries += invalid
        if not refs:
            chunks_without_lineage.append(index)
        for entry in lineage_entries:
            ref = entry["ref"]
            expected_item = all_items.get(ref)
            if expected_item is None:
                continue
            mismatched_fields = [
                field for field in ("label", "spans", "parent_refs")
                if entry.get(field) != expected_item[field]
            ]
            if mismatched_fields:
                lineage_metadata_mismatches.append({
                    "chunk_index": index,
                    "ref": ref,
                    "fields": mismatched_fields,
                })

        page_start = metadata.get("page_start")
        page_end = metadata.get("page_end")
        valid_pages = (
            isinstance(page_start, int) and not isinstance(page_start, bool)
            and isinstance(page_end, int) and not isinstance(page_end, bool)
            and 1 <= page_start <= page_end
        )
        if not valid_pages:
            page_metadata_issues.append(index)
        elif any(start <= page_start and page_end <= end
                 for start, end in ranges):
            structural_leaks.append(index)
        if valid_pages:
            if previous_page is not None and page_start < previous_page:
                page_regressions.append(index)
                evidence = {
                    "chunk_index": index,
                    "previous_chunk_index": previous_page_index,
                    "previous_page_start": previous_page,
                    "page_start": page_start,
                    "section_path": metadata.get("section_path"),
                    "previous_content_type": (
                        previous_page_metadata.get("content_type")
                        if previous_page_metadata is not None else None),
                    "content_type": metadata.get("content_type"),
                    "previous_headings": list(_headings(
                        previous_page_metadata or {})),
                    "headings": list(_headings(metadata)),
                }
                if (previous_page_index == index - 1
                        and previous_page_metadata is not None
                        and _allowed_semantic_lane_regression(
                            previous_page_metadata, metadata)):
                    evidence["reason"] = (
                        "rule_language_to_authors_explanation")
                    allowed_page_regressions.append(evidence)
                else:
                    evidence["reason"] = "unexpected_page_decrease"
                    unexpected_page_regressions.append(evidence)
            previous_page = page_start
            previous_page_index = index
            previous_page_metadata = metadata

        if metadata.get("chunk_index") != index:
            chunk_index_issues.append(index)

        content_types[str(metadata.get("content_type") or "")] += 1
        content_sources[str(metadata.get("content_source") or "")] += 1
        chapters[str(metadata.get("chapter_num"))] += 1
        raw = metadata.get("token_count")
        embedded = metadata.get("embedding_token_count")
        if isinstance(raw, int) and not isinstance(raw, bool) and raw >= 0:
            raw_counts.append(raw)
        if (isinstance(embedded, int) and not isinstance(embedded, bool)
                and embedded >= 0):
            embedding_counts.append(embedded)
        canonical = _canonical_text(str(record.get("text") or ""))
        if canonical:
            canonical_groups[canonical].append(index)
        text = str(record.get("text") or "")
        if "\ufffd" in text:
            normalization_issues["replacement_character"].append(index)
        if re.search(r"(?<=[A-Za-z0-9])-\s+(?=[A-Za-z0-9])", text):
            normalization_issues["split_hyphen"].append(index)
        if re.search(r"https?://\s|\bwww\s+\.|\bperma\.cc/\s", text, re.I):
            normalization_issues["split_url"].append(index)
        if "This and other authors' explanations draw" in text:
            normalization_issues["editorial_boilerplate"].append(index)
        if re.search(
                r"\b(?:clientlawyer|lawyerclient|plaintiffdefendant|"
                r"threejudge|Aconcluding)\b", text, re.I):
            normalization_issues["known_fused_term"].append(index)

        content_type = str(metadata.get("content_type") or "")
        content_source = str(metadata.get("content_source") or "")
        if not content_type:
            classification_issues["missing_content_type"].append(index)
        if content_source not in {"body", "footnote", "table", "mixed"}:
            classification_issues["unknown_content_source"].append(index)
        if content_source == "table" and content_type != "table":
            classification_issues["table_source_mismatch"].append(index)
        if content_source == "footnote" and content_type != "footnote":
            classification_issues["footnote_source_mismatch"].append(index)

        case_names = metadata.get("case_names")
        if not isinstance(case_names, list):
            entity_issues["invalid_case_names"].append(index)
            case_names = []
        elif any(
                not isinstance(name, str) or len(name) > 160
                or not (" v. " in name
                        or name.startswith(("In re ", "Ex parte ")))
                for name in case_names):
            entity_issues["invalid_case_names"].append(index)
        primary_case = metadata.get("primary_case")
        expected_primary = case_names[0] if case_names else None
        if primary_case != expected_primary:
            entity_issues["primary_case_mismatch"].append(index)

        if content_source == "table":
            if not any(line.lstrip().startswith("|")
                       for line in text.splitlines()):
                table_issues["missing_markdown_table"].append(index)
            if (not isinstance(metadata.get("table_rows"), int)
                    or isinstance(metadata.get("table_rows"), bool)
                    or metadata["table_rows"] < 1):
                table_issues["invalid_row_count"].append(index)
            if (not isinstance(metadata.get("table_cols"), int)
                    or isinstance(metadata.get("table_cols"), bool)
                    or metadata["table_cols"] < 1):
                table_issues["invalid_column_count"].append(index)

    eligible_refs = set(eligible_items)
    missing_refs = sorted(eligible_refs - represented_refs)
    unknown_refs = sorted(represented_refs - set(all_items))
    eligible_table_refs = {
        ref for ref, item in eligible_items.items()
        if item["label"] == "table"
    }
    represented_table_refs = eligible_table_refs & represented_refs
    missing_table_refs = sorted(eligible_table_refs - represented_refs)
    recovered = sorted(set(recovered_table_refs) & eligible_table_refs)
    over_limit = (
        [index for index, record in enumerate(records)
         if isinstance((record.get("metadata") or {}).get(
             "embedding_token_count"), int)
         and not isinstance((record.get("metadata") or {}).get(
             "embedding_token_count"), bool)
         and embedding_limit is not None
         and (record.get("metadata") or {})["embedding_token_count"]
         > embedding_limit]
        if embedding_limit is not None else []
    )
    duplicate_groups = [
        indexes for indexes in canonical_groups.values() if len(indexes) > 1
    ]
    unknown_content_types = sorted(
        value for value in content_types
        if value not in _KNOWN_CONTENT_TYPES)
    invalid_stable_ids = [
        index for index, value in enumerate(stable_ids)
        if not isinstance(value, str) or not value]
    invalid_chunk_hashes = [
        index for index, value in enumerate(chunk_hashes)
        if not isinstance(value, str)
        or re.fullmatch(r"[0-9a-f]{16,64}", value) is None]
    normalization_issue_count = sum(map(len, normalization_issues.values()))
    classification_issue_count = sum(map(len, classification_issues.values()))
    entity_issue_count = sum(map(len, entity_issues.values()))
    table_issue_count = sum(map(len, table_issues.values()))

    checks = [
        _check("nonempty_corpus", failed=not records,
               observed=len(records), required="> 0"),
        _check("unique_stable_ids",
               failed=(bool(invalid_stable_ids)
                       or len(set(stable_ids)) != len(stable_ids)),
               observed=len(set(stable_ids)), required=len(stable_ids)),
        _check("valid_chunk_hashes", failed=bool(invalid_chunk_hashes),
               observed=len(invalid_chunk_hashes), required=0),
        _check("source_inventory_integrity",
               failed=bool(source_inventory_issues),
               observed=sum(map(len, source_inventory_issues.values())),
               required=0),
        _check("source_lineage_schema",
               failed=bool(invalid_lineage_entries or lineage_schema_issues),
               observed=(invalid_lineage_entries
                         + len(lineage_schema_issues)), required=0),
        _check("chunks_have_source_lineage",
               failed=bool(chunks_without_lineage),
               observed=len(chunks_without_lineage), required=0),
        _check("eligible_source_items_represented",
               failed=bool(missing_refs), observed=len(missing_refs), required=0),
        _check("source_refs_resolve",
               failed=bool(unknown_refs), observed=len(unknown_refs), required=0),
        _check("source_lineage_matches_source",
               failed=bool(lineage_metadata_mismatches),
               observed=len(lineage_metadata_mismatches), required=0),
        _check("source_tables_represented",
               failed=bool(missing_table_refs),
               observed=len(missing_table_refs), required=0),
        _check("page_metadata_valid",
               failed=bool(page_metadata_issues),
               observed=len(page_metadata_issues), required=0),
        _check("page_regressions", failed=bool(unexpected_page_regressions),
               observed=len(unexpected_page_regressions), required=0),
        _check("structural_ranges_excluded",
               failed=bool(structural_leaks),
               observed=len(structural_leaks), required=0),
        _check("contiguous_chunk_indexes", failed=bool(chunk_index_issues),
               observed=len(chunk_index_issues), required=0),
        _check("normalization_invariants",
               failed=bool(normalization_issue_count),
               observed=normalization_issue_count, required=0),
        _check("raw_token_counts_present",
               failed=len(raw_counts) != len(records),
               observed=len(raw_counts), required=len(records)),
        _check("embedding_counts_present",
               failed=len(embedding_counts) != len(records),
               observed=len(embedding_counts), required=len(records)),
        _check("embedding_limit",
               failed=bool(over_limit), observed=len(over_limit), required=0),
        _check("known_content_types",
               failed=bool(unknown_content_types),
               observed=unknown_content_types, required=[]),
        _check("classification_invariants",
               failed=bool(classification_issue_count),
               observed=classification_issue_count, required=0),
        _check("entity_invariants", failed=bool(entity_issue_count),
               observed=entity_issue_count, required=0),
        _check("table_invariants", failed=bool(table_issue_count),
               observed=table_issue_count, required=0),
        _check("canonical_text_duplicates", failed=bool(duplicate_groups),
               observed=len(duplicate_groups), required=0, warning=True),
    ]
    status = "fail" if any(check["status"] == "fail" for check in checks) \
        else "pass"
    eligible_count = len(eligible_refs)
    represented_eligible_count = eligible_count - len(missing_refs)

    return {
        "schema_version": QUALITY_REPORT_SCHEMA_VERSION,
        "kind": "corpus_quality_report",
        "status": status,
        "source": {
            "docling_json": {
                "name": source_name,
                "sha256": source_sha256,
            },
            "chunks_jsonl": {
                "name": chunks_name,
                "sha256": chunks_sha256,
                "size": chunks_size,
                "record_count": len(records),
            },
        },
        "parameters_sha256": parameters_sha256,
        "embedding": {
            "model": embedding_model,
            "limit": embedding_limit,
            "raw_token_min": min(raw_counts) if raw_counts else None,
            "raw_token_max": max(raw_counts) if raw_counts else None,
            "raw_token_p50": _nearest_rank(raw_counts, 50),
            "raw_token_p95": _nearest_rank(raw_counts, 95),
            "raw_token_p99": _nearest_rank(raw_counts, 99),
            "raw_token_count": len(raw_counts),
            "input_token_min": min(embedding_counts) if embedding_counts else None,
            "input_token_max": max(embedding_counts) if embedding_counts else None,
            "input_token_p50": _nearest_rank(embedding_counts, 50),
            "input_token_p95": _nearest_rank(embedding_counts, 95),
            "input_token_p99": _nearest_rank(embedding_counts, 99),
            "inputs_over_limit": len(over_limit),
        },
        "source_lineage": {
            "schema_version": SOURCE_LINEAGE_SCHEMA_VERSION,
            "inventory_issues": source_inventory_issues,
            "eligible_items": eligible_count,
            "represented_items": represented_eligible_count,
            "coverage_ppm": (
                represented_eligible_count * 1_000_000 // eligible_count
                if eligible_count else 1_000_000),
            "missing_refs": missing_refs,
            "unknown_refs": unknown_refs,
            "chunks_without_lineage": chunks_without_lineage,
            "invalid_entries": invalid_lineage_entries,
            "metadata_mismatches": lineage_metadata_mismatches,
            "schema_issues": lineage_schema_issues,
            "excluded_items_by_reason": exclusion_counts,
        },
        "tables": {
            "eligible_source_tables": len(eligible_table_refs),
            "represented_source_tables": len(represented_table_refs),
            "missing_refs": missing_table_refs,
            "recovered_from_pdf": len(recovered),
            "recovered_refs": recovered,
            "published_table_chunks": content_types.get("table", 0),
            "issues": {
                key: values for key, values in sorted(table_issues.items())
            },
        },
        "corpus": {
            "content_types": dict(sorted(content_types.items())),
            "content_sources": dict(sorted(content_sources.items())),
            "chapter_counts": dict(sorted(chapters.items())),
            "page_metadata_issues": page_metadata_issues,
            "page_regressions": page_regressions,
            "allowed_page_regressions": allowed_page_regressions,
            "unexpected_page_regressions": unexpected_page_regressions,
            "structural_leaks": structural_leaks,
            "chunk_index_issues": chunk_index_issues,
            "canonical_duplicate_groups": duplicate_groups,
        },
        "normalization": {
            key: values for key, values in sorted(normalization_issues.items())
        },
        "classification": {
            "issues": {
                key: values
                for key, values in sorted(classification_issues.items())
            },
        },
        "entities": {
            "issues": {
                key: values for key, values in sorted(entity_issues.items())
            },
            "mentions": sum(len((record.get("metadata") or {}).get(
                "case_names") or []) for record in records),
            "unique": len({
                name for record in records
                for name in ((record.get("metadata") or {}).get(
                    "case_names") or [])
                if isinstance(name, str)
            }),
        },
        "hashes": {
            "stable_id_root_sha256": _sha256_lines(stable_ids),
            "chunk_hash_root_sha256": _sha256_lines(
                f"{stable_id}\0{chunk_hash}"
                for stable_id, chunk_hash in zip(stable_ids, chunk_hashes)),
            "unique_stable_ids": len(set(stable_ids)),
        },
        "checks": checks,
    }


def validate_quality_report(
        payload: object, *, chunks_name: str, chunks_sha256: str,
        chunks_size: int, record_count: int, stable_ids: Sequence[str],
        chunk_hashes: Sequence[str], source_name: str | None = None,
        source_sha256: str | None = None,
        parameters_sha256: str | None = None,
        embedding_model: str | None = None,
        embedding_limit: int | None = None,
) -> dict:
    """Validate report schema, pass state, and exact chunks binding."""
    if not isinstance(payload, dict):
        raise ValueError("corpus quality report must be a JSON object")
    schema_version = payload.get("schema_version")
    if (not isinstance(schema_version, int)
            or isinstance(schema_version, bool)
            or schema_version != QUALITY_REPORT_SCHEMA_VERSION):
        raise ValueError("unsupported corpus quality report schema")
    if payload.get("kind") != "corpus_quality_report":
        raise ValueError("invalid corpus quality report kind")
    if payload.get("status") != "pass":
        raise ValueError("corpus quality report did not pass")
    source = payload.get("source")
    chunks = source.get("chunks_jsonl") if isinstance(source, dict) else None
    expected = {
        "name": chunks_name,
        "sha256": chunks_sha256,
        "size": chunks_size,
        "record_count": record_count,
    }
    if not isinstance(chunks, dict) or any(
            chunks.get(key) != value for key, value in expected.items()):
        raise ValueError("corpus quality report does not match chunks artifact")
    docling = source.get("docling_json") if isinstance(source, dict) else None
    if source_name is not None and (
            not isinstance(docling, dict)
            or docling.get("name") != source_name):
        raise ValueError("corpus quality report does not match source name")
    if source_sha256 is not None and (
            not isinstance(docling, dict)
            or docling.get("sha256") != source_sha256):
        raise ValueError("corpus quality report does not match source artifact")
    if (parameters_sha256 is not None
            and payload.get("parameters_sha256") != parameters_sha256):
        raise ValueError("corpus quality report does not match parameters")
    embedding = payload.get("embedding")
    if embedding_model is not None and (
            not isinstance(embedding, dict)
            or embedding.get("model") != embedding_model):
        raise ValueError("corpus quality report does not match embedding model")
    if embedding_limit is not None and (
            not isinstance(embedding, dict)
            or embedding.get("limit") != embedding_limit):
        raise ValueError("corpus quality report does not match embedding limit")
    hashes = payload.get("hashes")
    expected_stable_root = _sha256_lines(stable_ids)
    expected_chunk_root = _sha256_lines(
        f"{stable_id}\0{chunk_hash}"
        for stable_id, chunk_hash in zip(stable_ids, chunk_hashes))
    if (not isinstance(hashes, dict)
            or hashes.get("stable_id_root_sha256") != expected_stable_root
            or hashes.get("chunk_hash_root_sha256") != expected_chunk_root
            or hashes.get("unique_stable_ids") != len(set(stable_ids))):
        raise ValueError("corpus quality report hash roots do not match chunks")
    checks = payload.get("checks")
    if not isinstance(checks, list):
        raise ValueError("corpus quality report has invalid checks")
    checks_by_name = {}
    for check in checks:
        if not isinstance(check, dict):
            raise ValueError("corpus quality report contains an invalid check")
        name = check.get("name")
        status = check.get("status")
        if not isinstance(name, str) or not name:
            raise ValueError("corpus quality report contains an invalid check")
        if name in checks_by_name:
            raise ValueError("corpus quality report contains duplicate checks")
        checks_by_name[name] = check
        if name in _HARD_CHECK_NAMES:
            if status != "pass":
                raise ValueError(
                    "corpus quality report contains a failed check")
            observed = check.get("observed")
            required = check.get("required")
            valid_semantics = False
            if name == "nonempty_corpus":
                valid_semantics = (
                    observed == record_count and _is_nonnegative_int(observed)
                    and observed > 0 and required == "> 0")
            elif name == "unique_stable_ids":
                valid_semantics = (
                    observed == record_count and required == record_count
                    and _is_nonnegative_int(observed)
                    and _is_nonnegative_int(required))
            elif name in {
                    "raw_token_counts_present",
                    "embedding_counts_present",
            }:
                valid_semantics = (
                    observed == record_count and required == record_count
                    and _is_nonnegative_int(observed)
                    and _is_nonnegative_int(required))
            elif name == "known_content_types":
                valid_semantics = observed == [] and required == []
            elif name in _ZERO_COUNT_CHECK_NAMES:
                valid_semantics = (
                    observed == 0 and required == 0
                    and _is_nonnegative_int(observed)
                    and _is_nonnegative_int(required))
            if not valid_semantics:
                raise ValueError(
                    "corpus quality report check semantics are invalid")
        if name in _WARNING_CHECK_NAMES:
            observed = check.get("observed")
            required = check.get("required")
            if (not _is_nonnegative_int(observed)
                    or not _is_nonnegative_int(required) or required != 0
                    or status != ("warn" if observed else "pass")):
                raise ValueError(
                    "corpus quality report contains an invalid warning check")
    if set(checks_by_name) != _SCHEMA_V1_CHECK_NAMES:
        raise ValueError(
            "corpus quality report check set does not match schema")
    if len(checks_by_name) != len(checks):
        raise ValueError("corpus quality report contains duplicate checks")
    lineage = payload.get("source_lineage")
    lineage_version = (
        lineage.get("schema_version") if isinstance(lineage, dict) else None)
    if (not isinstance(lineage, dict)
            or not isinstance(lineage_version, int)
            or isinstance(lineage_version, bool)
            or lineage_version != SOURCE_LINEAGE_SCHEMA_VERSION
            or lineage.get("inventory_issues") != {}
            or lineage.get("missing_refs") != []
            or lineage.get("unknown_refs") != []
            or lineage.get("chunks_without_lineage") != []
            or lineage.get("invalid_entries") != 0
            or lineage.get("metadata_mismatches") != []
            or lineage.get("schema_issues") != []):
        raise ValueError("corpus quality report lineage gate is invalid")
    corpus = payload.get("corpus")
    page_regressions = (
        corpus.get("page_regressions") if isinstance(corpus, dict) else None)
    allowed_regressions = (
        corpus.get("allowed_page_regressions")
        if isinstance(corpus, dict) else None)
    unexpected_regressions = (
        corpus.get("unexpected_page_regressions")
        if isinstance(corpus, dict) else None)
    if (not isinstance(corpus, dict)
            or not isinstance(page_regressions, list)
            or any(not _is_nonnegative_int(index) or index < 1
                   for index in page_regressions)
            or not isinstance(allowed_regressions, list)
            or any(not _valid_page_regression_evidence(
                entry, allowed=True) for entry in allowed_regressions)
            or not isinstance(unexpected_regressions, list)
            or unexpected_regressions != []
            or sorted(
                entry["chunk_index"] for entry in allowed_regressions
            ) != page_regressions):
        raise ValueError("corpus quality report page order gate is invalid")
    raw_token_count = (
        embedding.get("raw_token_count")
        if isinstance(embedding, dict) else None)
    if (not isinstance(embedding, dict)
            or not _is_nonnegative_int(raw_token_count)
            or raw_token_count != record_count):
        raise ValueError("corpus quality report raw token gate is invalid")
    return payload


def read_quality_report(
        path: Path, *, chunks_name: str, chunks_sha256: str,
        chunks_size: int, record_count: int, stable_ids: Sequence[str],
        chunk_hashes: Sequence[str], source_name: str | None = None,
        source_sha256: str | None = None,
        parameters_sha256: str | None = None,
        embedding_model: str | None = None,
        embedding_limit: int | None = None,
) -> dict:
    try:
        raw = Path(path).read_bytes()
    except OSError as exc:
        raise ValueError(f"cannot read corpus quality report: {path}") from exc
    return parse_quality_report_bytes(
        raw, chunks_name=chunks_name, chunks_sha256=chunks_sha256,
        chunks_size=chunks_size, record_count=record_count,
        stable_ids=stable_ids, chunk_hashes=chunk_hashes,
        source_name=source_name, source_sha256=source_sha256,
        parameters_sha256=parameters_sha256,
        embedding_model=embedding_model, embedding_limit=embedding_limit)


def parse_quality_report_bytes(
        raw: bytes, *, chunks_name: str, chunks_sha256: str,
        chunks_size: int, record_count: int, stable_ids: Sequence[str],
        chunk_hashes: Sequence[str], source_name: str | None = None,
        source_sha256: str | None = None,
        parameters_sha256: str | None = None,
        embedding_model: str | None = None,
        embedding_limit: int | None = None,
) -> dict:
    """Strictly parse and validate one exact report byte snapshot."""
    if len(raw) > MAX_QUALITY_REPORT_BYTES:
        raise ValueError("corpus quality report is too large")

    def reject_constant(value: str):
        raise ValueError(f"invalid JSON numeric constant: {value}")

    def reject_duplicate_keys(pairs):
        result = {}
        for key, value in pairs:
            if key in result:
                raise ValueError(
                    f"duplicate corpus quality report field: {key}")
            result[key] = value
        return result

    try:
        payload = json.loads(
            raw.decode("utf-8"), parse_constant=reject_constant,
            object_pairs_hook=reject_duplicate_keys)
    except (UnicodeError, json.JSONDecodeError) as exc:
        raise ValueError("cannot parse corpus quality report") from exc
    return validate_quality_report(
        payload, chunks_name=chunks_name, chunks_sha256=chunks_sha256,
        chunks_size=chunks_size, record_count=record_count,
        stable_ids=stable_ids, chunk_hashes=chunk_hashes,
        source_name=source_name, source_sha256=source_sha256,
        parameters_sha256=parameters_sha256,
        embedding_model=embedding_model, embedding_limit=embedding_limit)
