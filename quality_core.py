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
from typing import Iterable, NamedTuple, Sequence

import chunking_core
import heading_lineage
import retrieval_core
import source_fidelity_core
import table_retrieval_core

QUALITY_REPORT_SCHEMA_VERSION = 12
SOURCE_LINEAGE_SCHEMA_VERSION = 5
SOURCE_ANALYSIS_SCHEMA_VERSION = 4
# Mirrors rag.CONVERSION_COMPLETION_SCHEMA_VERSION; this leaf cannot import
# rag.py without creating an import cycle, so the two must be bumped together.
CONVERSION_COMPLETION_SCHEMA_VERSION = 3
PAGE_ORDER_REASON_FIELD = "page_order_reason"
FOOTNOTE_AFTER_CONTINUATION_REASON = (
    "footnote_after_cross_page_continuation")
MAX_QUALITY_REPORT_BYTES = 16 * 1024 * 1024
_QUALITY_REPORT_FIELDS = {
    "schema_version", "kind", "status", "source", "parameters_sha256",
    "inputs", "embedding", "source_lineage", "tables", "corpus",
    "normalization", "classification", "entities", "retrieval",
    "table_retrieval", "source_analysis", "hashes", "checks",
}
_EMBEDDING_FIELDS = {
    "model", "limit", "raw_token_min", "raw_token_max", "raw_token_p50",
    "raw_token_p95", "raw_token_p99", "raw_token_count", "input_token_min",
    "input_token_max", "input_token_p50", "input_token_p95",
    "input_token_p99", "inputs_over_limit",
}
_SOURCE_LINEAGE_FIELDS = {
    "schema_version", "inventory_issues", "eligible_items",
    "represented_items", "coverage_ppm", "missing_refs", "unknown_refs",
    "chunks_without_lineage", "invalid_entries", "metadata_mismatches",
    "schema_issues", "excluded_items_by_reason", "fidelity",
}
_TABLE_FIELDS = {
    "eligible_source_tables", "represented_source_tables", "missing_refs",
    "recovered_from_pdf", "recovered_refs", "published_table_chunks",
    "issues",
}
_CORPUS_FIELDS = {
    "content_types", "content_sources", "chapter_counts",
    "page_metadata_issues", "page_regressions", "allowed_page_regressions",
    "unexpected_page_regressions", "structural_leaks",
    "chunk_index_issues", "canonical_duplicate_groups",
}
_RETRIEVAL_FIELDS = {
    "schema_version", "context_parents", "linked_chunks",
    "isolated_chunks", "issues",
}
_TABLE_RETRIEVAL_FIELDS = {
    "schema_version", "parent_tables", "expanded_parents", "child_chunks",
    "issues",
}
_SOURCE_ANALYSIS_FIELDS = {
    "schema_version", "available", "substantive_page_footers", "pictures",
    "section_headings",
}
_PAGE_FOOTER_ANALYSIS_FIELDS = {
    "total", "page_label_like", "repeating_or_short",
    "substantive_detected", "substantive_represented",
    "substantive_risk", "substantive_risk_refs",
}
_PICTURE_ANALYSIS_FIELDS = {
    "total", "with_linked_text", "covered_by_linked_text",
    "covered_by_figure_text", "missing_linked_text", "large_unlinked",
    "other_unlinked", "substantive_risk", "substantive_risk_refs",
}
_SECTION_HEADING_ANALYSIS_FIELDS = {
    "schema_version", "policy", "source_items", "attachable_items",
    "attached_items", "directly_owned_items", "exceptions", "artifacts",
    "item_coverage_ppm", "missing_refs", "unattached_refs",
    "missing_direct_refs", "duplicate_direct_refs",
    "invalid_binding_records", "scope_conflict_records",
    "display_binding_records", "ancestor_resumption_count",
    "evidence_sha256",
}

_SOURCE_LABELS = {
    "text", "list_item", "footnote", "caption", "code", "table",
    "document_index", "picture",
}
_KNOWN_CONTENT_TYPES = {
    "case_opinion", "notes_and_questions", "author_narrative",
    "statutory_excerpt", "table", "footnote", "chapter_introduction",
    "problem_hypothetical", "figure",
}
_SOURCE_COLLECTIONS = (
    "texts", "tables", "pictures", "key_value_items", "form_items",
)
_CANONICAL_TEXT_RE = re.compile(r"[^a-z0-9]+")
_PAGE_LABEL_RE = re.compile(
    r"(?:page\s+)?(?:[0-9]{1,6}|[ivxlcdm]{1,16})", re.IGNORECASE)
_FOOTNOTE_PREFIX_RE = re.compile(
    r"(?:[*\u2020\u2021]+\s*)?(?:[0-9]{1,4}|[a-z])[.)]\s+\S",
    re.IGNORECASE)
_CITATION_LEAD_RE = re.compile(
    r"(?:id\.|ibid\.|see\b|accord\b|cf\.\s|supra\b|infra\b)",
    re.IGNORECASE)
_LEGAL_CITATION_RE = re.compile(
    r"\b[0-9]{1,4}\s+[A-Z][A-Za-z. '&-]{0,28}\s+[0-9]{1,6}\b")
_LARGE_PICTURE_AREA_RATIO = 0.15
_PRINTED_PAGE_NUMBER_RE = re.compile(r"[1-9][0-9]{0,5}")
_RUNNING_PAGE_MARGIN_RATIO = 0.12
_RUNNING_PAGE_LANE_TOLERANCE_RATIO = 0.025
_RUNNING_PAGE_DELTA_MIN_SUPPORT = 3
_EMPHASIS_BOILERPLATE_RE = re.compile(
    r"^\**\s*all\s+emphasis\s+added\.?\s*\**$", re.IGNORECASE)
_SINGLE_LETTER_SECTION_MARKER_RE = re.compile(r"^[A-Z]$")
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
    "source_geometry_complete",
    "source_token_fidelity",
    "source_tables_represented",
    "page_metadata_valid",
    "page_regressions",
    "same_page_reading_order",
    "structural_ranges_excluded",
    "contiguous_chunk_indexes",
    "retrieval_linkage_invariants",
    "table_retrieval_invariants",
    "normalization_invariants",
    "raw_token_counts_present",
    "embedding_counts_present",
    "embedding_limit",
    "known_content_types",
    "classification_invariants",
    "entity_invariants",
    "table_invariants",
    "section_heading_attachment",
)
_WARNING_CHECK_NAMES = (
    "canonical_text_duplicates",
    "substantive_excluded_page_footers",
    "uncovered_substantive_pictures",
)
_QUALITY_CHECK_NAMES = frozenset(
    _HARD_CHECK_NAMES + _WARNING_CHECK_NAMES)
_ZERO_COUNT_CHECK_NAMES = frozenset({
    "valid_chunk_hashes",
    "source_inventory_integrity",
    "source_lineage_schema",
    "chunks_have_source_lineage",
    "eligible_source_items_represented",
    "source_refs_resolve",
    "source_lineage_matches_source",
    "source_geometry_complete",
    "source_token_fidelity",
    "source_tables_represented",
    "page_metadata_valid",
    "page_regressions",
    "same_page_reading_order",
    "structural_ranges_excluded",
    "contiguous_chunk_indexes",
    "retrieval_linkage_invariants",
    "table_retrieval_invariants",
    "normalization_invariants",
    "embedding_limit",
    "classification_invariants",
    "entity_invariants",
    "table_invariants",
    "section_heading_attachment",
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


def _valid_count_mapping(
        value: object, *, expected_total: int | None = None) -> bool:
    if (not isinstance(value, dict)
            or any(not isinstance(key, str) or not key
                   or not _is_nonnegative_int(count) or count < 1
                   for key, count in value.items())):
        return False
    return expected_total is None or sum(value.values()) == expected_total


def _valid_index_list(
        value: object, *, record_count: int, minimum_length: int = 0) -> bool:
    return (
        isinstance(value, list)
        and len(value) >= minimum_length
        and all(_is_nonnegative_int(index) and index < record_count
                for index in value)
        and value == sorted(set(value))
    )


def _valid_issue_mapping(
        value: object, *, record_count: int, require_empty: bool) -> bool:
    if (not isinstance(value, dict)
            or any(not isinstance(key, str) or not key
                   or not _valid_index_list(
                       indexes, record_count=record_count)
                   for key, indexes in value.items())):
        return False
    return not require_empty or not any(value.values())


def _normalization_issues(records: Sequence[dict]) -> dict[str, list[int]]:
    """Derive the exact normalization findings for a record snapshot."""
    issues: defaultdict[str, list[int]] = defaultdict(list)
    for index, record in enumerate(records):
        metadata = record.get("metadata") or {}
        text = str(record.get("text") or "")
        if "\ufffd" in text:
            issues["replacement_character"].append(index)
        if re.search(
                r"(?:(?<=[A-Za-z0-9])-\s+(?=[a-z])|"
                r"(?<=[A-Za-z0-9])\s+-(?=[A-Za-z0-9]))",
                text):
            issues["split_hyphen"].append(index)
        if (metadata.get("content_source") not in {"figure", "table"}
                and re.search(
                    r"(?<![A-Za-z0-9_])(?:[A-Za-z]\s+){3,}"
                    r"[A-Za-z](?![A-Za-z0-9_])",
                    text)):
            issues["ocr_gibberish"].append(index)
        if chunking_core.has_malformed_url_spacing(text):
            issues["split_url"].append(index)
        if "This and other authors' explanations draw" in text:
            issues["editorial_boilerplate"].append(index)
        if re.search(
                r"\b(?:clientlawyer|lawyerclient|plaintiffdefendant|"
                r"threejudge|Aconcluding)\b", text, re.I):
            issues["known_fused_term"].append(index)
    return {
        key: indexes for key, indexes in sorted(issues.items())
    }


def _valid_sha256(value: object) -> bool:
    return (isinstance(value, str)
            and re.fullmatch(r"[0-9a-f]{64}", value) is not None)


def _valid_file_binding(value: object, *, capture_policy: bool = False) -> bool:
    expected = {"name", "size", "sha256"}
    if capture_policy:
        expected.add("capture_policy")
    if not isinstance(value, dict) or set(value) != expected:
        return False
    return (
        isinstance(value.get("name"), str)
        and bool(value["name"])
        and Path(value["name"]).name == value["name"]
        and _is_nonnegative_int(value.get("size"))
        and value["size"] > 0
        and _valid_sha256(value.get("sha256"))
        and (not capture_policy
             or value.get("capture_policy") == "stream-copy-v1")
    )


def _valid_input_bindings(value: object) -> bool:
    if (not isinstance(value, dict)
            or set(value) != {
                "docling_json", "conversion_manifest", "table_recovery",
                "source_fidelity_oracles"}
            or not _valid_file_binding(value.get("docling_json"))):
        return False
    oracle_registry = value.get("source_fidelity_oracles")
    if (not isinstance(oracle_registry, dict)
            or set(oracle_registry) != {
                "name", "size", "sha256", "schema_version"}
            or not _valid_file_binding({
                key: oracle_registry.get(key)
                for key in ("name", "size", "sha256")
            })
            or oracle_registry.get("schema_version")
            != source_fidelity_core.SOURCE_ORACLE_REGISTRY_SCHEMA_VERSION):
        return False
    conversion = value.get("conversion_manifest")
    if conversion is not None and (
            not isinstance(conversion, dict)
            or set(conversion) != {"name", "sha256", "schema_version"}
            or not isinstance(conversion.get("name"), str)
            or not conversion["name"]
            or Path(conversion["name"]).name != conversion["name"]
            or not _valid_sha256(conversion.get("sha256"))
            or conversion.get("schema_version")
            != CONVERSION_COMPLETION_SCHEMA_VERSION):
        return False
    recovery = value.get("table_recovery")
    if recovery is None:
        return True
    return (
        conversion is not None
        and isinstance(recovery, dict)
        and set(recovery) == {"pdf", "conversion_manifest", "discovery"}
        and _valid_file_binding(recovery.get("pdf"), capture_policy=True)
        and recovery.get("conversion_manifest") == conversion
        and recovery.get("discovery") in {"explicit", "adjacent", "ancestor"}
    )


def _source_oracle_upstream_inputs(value: dict) -> dict:
    """Return the exact inputs captured before the oracle sidecar existed."""
    return {
        key: value[key]
        for key in ("docling_json", "conversion_manifest", "table_recovery")
    }


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


def _allowed_cross_page_footnote_regression(
        previous_metadata: dict, metadata: dict, *,
        previous_page: int, page: int) -> bool:
    """Recognize a footnote delayed to preserve a cross-page sentence."""
    return (
        previous_page == page + 1
        and previous_metadata.get("content_source") == "body"
        and metadata.get("content_source") == "footnote"
        and metadata.get("content_type") == "footnote"
        and isinstance(metadata.get("section_path"), str)
        and metadata.get(PAGE_ORDER_REASON_FIELD)
        == FOOTNOTE_AFTER_CONTINUATION_REASON
    )


def _valid_page_regression_evidence(entry: object, *, allowed: bool) -> bool:
    base_fields = {
            "chunk_index", "previous_chunk_index", "previous_page_start",
            "page_start", "section_path", "previous_content_type",
            "content_type", "previous_headings", "headings", "reason",
    }
    footnote_fields = base_fields | {
        "previous_content_source", "content_source", PAGE_ORDER_REASON_FIELD,
    }
    entry_fields = frozenset(entry) if isinstance(entry, dict) else frozenset()
    if (not isinstance(entry, dict)
            or entry_fields not in {frozenset(base_fields),
                                    frozenset(footnote_fields)}):
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
    if entry["reason"] == "rule_language_to_authors_explanation":
        if entry_fields != frozenset(base_fields):
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
    if entry["reason"] != FOOTNOTE_AFTER_CONTINUATION_REASON \
            or entry_fields != frozenset(footnote_fields):
        return False
    previous_metadata = {
        "content_source": entry["previous_content_source"],
    }
    metadata = {
        "section_path": entry["section_path"],
        "content_source": entry["content_source"],
        "content_type": entry["content_type"],
        PAGE_ORDER_REASON_FIELD: entry[PAGE_ORDER_REASON_FIELD],
    }
    return _allowed_cross_page_footnote_regression(
        previous_metadata, metadata,
        previous_page=previous_page, page=page)


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
    spans, _ = source_fidelity_core.provenance_spans(item)
    return spans


def _relationship_ref(value: object) -> str:
    if not isinstance(value, dict):
        return ""
    ref = value.get("cref")
    return ref if isinstance(ref, str) else ""


def _inside_ranges(page: int,
                   structural_ranges: Sequence[tuple[int, int]]) -> bool:
    return any(start <= page <= end for start, end in structural_ranges)


def _running_division_furniture_refs(document: dict) -> set[str]:
    """Return mislabeled list/text banners aligned with real page headers."""
    texts = document.get("texts") if isinstance(document, dict) else None
    values = texts if isinstance(texts, list) else []

    def position(item: dict) -> tuple[int, float] | None:
        provenance = item.get("prov")
        if not isinstance(provenance, list) or not provenance:
            return None
        first = provenance[0]
        bbox = first.get("bbox") if isinstance(first, dict) else None
        page = first.get("page_no") if isinstance(first, dict) else None
        if (not isinstance(page, int) or isinstance(page, bool)
                or not isinstance(bbox, dict)):
            return None
        try:
            top = float(bbox.get("t"))
        except (TypeError, ValueError):
            return None
        if not math.isfinite(top):
            return None
        origin = str(bbox.get("coord_origin") or "").upper()
        return page, -top if origin == "BOTTOMLEFT" else top

    header_verticals: defaultdict[int, list[float]] = defaultdict(list)
    for item in values:
        if (not isinstance(item, dict)
                or _label(item.get("label")) != "page_header"):
            continue
        item_position = position(item)
        if item_position is not None:
            header_verticals[item_position[0]].append(item_position[1])

    refs = set()
    for item in values:
        if not isinstance(item, dict):
            continue
        if _label(item.get("label")) not in {"text", "list_item"}:
            continue
        ref = item.get("self_ref")
        item_position = position(item)
        if (not isinstance(ref, str) or not ref or item_position is None
                or not chunking_core.is_probable_running_division_banner(
                    _source_text(item))):
            continue
        if any(
                abs(item_position[1] - vertical) <= 12.0
                for vertical in header_verticals.get(item_position[0], [])):
            refs.add(ref)
    return refs


class _PrintedPageNumberEvidence(NamedTuple):
    """One numeric source item with page-edge geometry."""

    ref: str
    page: int
    printed_page: int
    edge: str
    edge_distance_ratio: float

    @property
    def delta(self) -> int:
        return self.page - self.printed_page


def _serialized_page_height(document: dict, page_number: int) -> float | None:
    pages = document.get("pages")
    page = None
    if isinstance(pages, dict):
        page = pages.get(str(page_number))
        if page is None:
            page = pages.get(page_number)
    elif isinstance(pages, list) and 0 <= page_number - 1 < len(pages):
        page = pages[page_number - 1]
    size = page.get("size") if isinstance(page, dict) else None
    height = size.get("height") if isinstance(size, dict) else None
    try:
        value = float(height)
    except (TypeError, ValueError):
        return None
    return value if math.isfinite(value) and value > 0 else None


def _printed_page_number_evidence(
        document: dict, item: dict,
) -> _PrintedPageNumberEvidence | None:
    """Return exact numeric text plus normalized physical-margin evidence."""
    text = _source_text(item).strip()
    if _PRINTED_PAGE_NUMBER_RE.fullmatch(text) is None:
        return None
    ref = item.get("self_ref")
    provenance = item.get("prov")
    if (not isinstance(ref, str) or not ref
            or not isinstance(provenance, list) or len(provenance) != 1):
        return None
    span = provenance[0]
    if not isinstance(span, dict):
        return None
    page = span.get("page_no")
    bbox = span.get("bbox")
    if (not isinstance(page, int) or isinstance(page, bool) or page < 1
            or not isinstance(bbox, dict)):
        return None
    height = _serialized_page_height(document, page)
    origin = str(bbox.get("coord_origin") or "").upper()
    if height is None or origin not in {"BOTTOMLEFT", "TOPLEFT"}:
        return None
    try:
        low, high = sorted((float(bbox.get("b")), float(bbox.get("t"))))
    except (TypeError, ValueError):
        return None
    if (not math.isfinite(low) or not math.isfinite(high)
            or low < -2.0 or high > height + 2.0 or low > high):
        return None
    if origin == "BOTTOMLEFT":
        distances = {"bottom": low, "top": height - high}
    else:
        distances = {"top": low, "bottom": height - high}
    edge, distance = min(distances.items(), key=lambda entry: entry[1])
    ratio = max(0.0, distance) / height
    if ratio > _RUNNING_PAGE_MARGIN_RATIO:
        return None
    return _PrintedPageNumberEvidence(
        ref=ref,
        page=page,
        printed_page=int(text),
        edge=edge,
        edge_distance_ratio=ratio,
    )


def running_page_number_furniture_refs(document: dict) -> set[str]:
    """Infer numeric ``text`` items that are mislabeled page furniture.

    The inference is deliberately document-level and fail-closed.  Correctly
    labeled numeric ``page_header`` and ``page_footer`` objects establish a
    unique, strict-majority PDF-page/printed-page delta.  A candidate must be
    an exact decimal ``text`` item, match that delta, and occupy the same
    physical top- or bottom-margin lane as the supporting furniture.  Each
    source page gets one vote so duplicated OCR objects cannot create a false
    dominant offset.
    """
    texts = document.get("texts") if isinstance(document, dict) else None
    values = texts if isinstance(texts, list) else []
    labeled_evidence: list[_PrintedPageNumberEvidence] = []
    for item in values:
        if not isinstance(item, dict):
            continue
        label = _label(item.get("label"))
        if label not in {"page_header", "page_footer"}:
            continue
        evidence = _printed_page_number_evidence(document, item)
        expected_edge = "top" if label == "page_header" else "bottom"
        if evidence is not None and evidence.edge == expected_edge:
            labeled_evidence.append(evidence)

    deltas_by_page: defaultdict[int, set[int]] = defaultdict(set)
    for evidence in labeled_evidence:
        deltas_by_page[evidence.page].add(evidence.delta)
    delta_counts = Counter(
        next(iter(deltas))
        for deltas in deltas_by_page.values()
        if len(deltas) == 1
    )
    if not delta_counts:
        return set()
    dominant_delta, support = delta_counts.most_common(1)[0]
    total_support = sum(delta_counts.values())
    if (support < _RUNNING_PAGE_DELTA_MIN_SUPPORT
            or support * 2 <= total_support
            or sum(count == support for count in delta_counts.values()) != 1):
        return set()

    lane_ratios: defaultdict[str, list[float]] = defaultdict(list)
    for evidence in labeled_evidence:
        if evidence.delta == dominant_delta:
            lane_ratios[evidence.edge].append(evidence.edge_distance_ratio)
    lane_centers = {
        edge: sorted(ratios)[len(ratios) // 2]
        for edge, ratios in lane_ratios.items()
        if ratios
    }

    refs: set[str] = set()
    for item in values:
        if (not isinstance(item, dict)
                or _label(item.get("label")) != "text"):
            continue
        evidence = _printed_page_number_evidence(document, item)
        if evidence is None or evidence.delta != dominant_delta:
            continue
        lane_center = lane_centers.get(evidence.edge)
        if (lane_center is not None
                and abs(evidence.edge_distance_ratio - lane_center)
                <= _RUNNING_PAGE_LANE_TOLERANCE_RATIO):
            refs.add(evidence.ref)
    return refs


def _source_exclusion_reason(
        item: dict, *, structural_ranges: Sequence[tuple[int, int]],
        page_footer_frequencies: Counter[str] | None = None,
        substantive_picture: bool = False) -> str | None:
    label = _label(item.get("label"))
    text = str(item.get("text") or item.get("orig") or "").strip()
    canonical = _canonical_text(text)
    substantive_page_footer = (
        label == "page_footer"
        and _looks_substantive_page_footer(
            text,
            canonical_frequency=(page_footer_frequencies or {}).get(
                canonical, 0),
        )
    )
    if label not in _SOURCE_LABELS and not substantive_page_footer:
        return f"label:{label or 'unknown'}"
    content_layer = str(item.get("content_layer") or "").lower()
    if "furniture" in content_layer and not substantive_page_footer:
        return "furniture"
    pages = _item_pages(item)
    if not pages:
        return "no_provenance"
    if all(_inside_ranges(page, structural_ranges) for page in pages):
        return "structural_range"
    if label == "picture" and not substantive_picture:
        return "decorative_picture"
    if label not in {"table", "document_index", "picture"} and not text:
        return "empty"
    if (label == "text"
            and _SINGLE_LETTER_SECTION_MARKER_RE.fullmatch(text)):
        return "section_marker"
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
    source_texts = document.get("texts")
    source_item_map = _source_items_by_ref(document)
    substantive_picture_refs = {
        ref for ref, item in source_item_map.items()
        if (_label(item.get("label")) == "picture"
            and (_picture_text_refs(item, source_item_map)
                 or _large_picture(item, document)))
    }
    sparse_ocr_heading_artifact_refs = (
        heading_lineage.sparse_ocr_heading_artifact_refs(document))
    running_furniture_refs = (
        _running_division_furniture_refs(document)
        | running_page_number_furniture_refs(document)
    )
    page_footer_frequencies = Counter(
        _canonical_text(_source_text(item))
        for item in (source_texts if isinstance(source_texts, list) else [])
        if (isinstance(item, dict)
            and _label(item.get("label")) == "page_footer"
            and _canonical_text(_source_text(item)))
    )
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
            fidelity_descriptor, provenance_issues = (
                source_fidelity_core.source_descriptor(item))
            if provenance_issues:
                integrity_issues["invalid_provenance"].extend(
                    f"{location}.{issue}" for issue in provenance_issues)
            descriptor = {
                "label": _label(item.get("label")),
                "pages": list(_item_pages(item)),
                "spans": fidelity_descriptor["spans"],
                "parent_refs": [],
                "source_text_sha256": fidelity_descriptor[
                    "source_text_sha256"],
                "source_lexical_sha256": fidelity_descriptor[
                    "source_lexical_sha256"],
                "source_lexical_count": fidelity_descriptor[
                    "source_lexical_count"],
            }
            all_items[ref] = descriptor
            raw_items[ref] = item
            item_locations[ref] = location
            reason = (
                heading_lineage.SPARSE_OCR_HEADING_ARTIFACT_REASON
                if ref in sparse_ocr_heading_artifact_refs
                else "running_page_furniture"
                if ref in running_furniture_refs
                else _source_exclusion_reason(
                    item, structural_ranges=ranges,
                    page_footer_frequencies=page_footer_frequencies,
                    substantive_picture=ref in substantive_picture_refs)
            )
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


def _source_table_dimensions(document: dict) -> dict[str, tuple[int, int]]:
    """Return exact Markdown data-row/column dimensions from Docling tables.

    Docling's matrix row count includes the first row that its Markdown export
    publishes as the header.  The strict retrieval parser counts only rows
    after that header, hence the ordinary single-row subtraction.  A table
    with exactly one source row whose cells are explicitly not headers is
    normalized to a blank Markdown header plus one data row, so its attested
    data-row count remains one.  A leading source cell spanning every column
    is published as table preamble rather than as a duplicated Markdown row,
    so that source-attested title row is also excluded from the data count.
    """
    dimensions = {}
    tables = document.get("tables") if isinstance(document, dict) else None
    if not isinstance(tables, list):
        return dimensions
    for table in tables:
        if not isinstance(table, dict):
            continue
        ref = table.get("self_ref")
        data = table.get("data")
        rows = data.get("num_rows") if isinstance(data, dict) else None
        columns = data.get("num_cols") if isinstance(data, dict) else None
        if (not isinstance(ref, str) or not ref
                or not _is_nonnegative_int(rows) or rows < 1
                or not _is_nonnegative_int(columns) or columns < 1):
            continue
        cells = data.get("table_cells")
        column_header_flags = [
            cell.get("column_header") if isinstance(cell, dict) else None
            for cell in (cells if isinstance(cells, list) else [])
        ]
        headerless_single_row = (
            table_retrieval_core.source_table_has_single_headerless_row(
                rows, column_header_flags)
        )
        first_row = [
            cell for cell in (cells if isinstance(cells, list) else [])
            if (isinstance(cell, dict)
                and cell.get("start_row_offset_idx") == 0)
        ]
        promoted_full_width_title = (
            rows >= 3 and len(first_row) == 1
            and first_row[0].get("start_col_offset_idx") == 0
            and first_row[0].get("end_col_offset_idx") == columns
            and first_row[0].get("end_row_offset_idx") == 1
            and bool(str(first_row[0].get("text") or "").strip())
        )
        dimensions[ref] = (
            (rows if headerless_single_row else rows - 1)
            - int(promoted_full_width_title),
            columns,
        )
    return dimensions


def _canonical_text(text: str) -> str:
    return _CANONICAL_TEXT_RE.sub("", text.casefold())


def _source_text(item: dict) -> str:
    return str(item.get("text") or item.get("orig") or "").strip()


def _source_bbox_height(item: dict) -> float | None:
    """Return one finite provenance height for geometry-gated QC rules."""
    provenance = item.get("prov")
    if not isinstance(provenance, list) or len(provenance) != 1:
        return None
    bbox = provenance[0].get("bbox") if isinstance(
        provenance[0], dict) else None
    if not isinstance(bbox, dict):
        return None
    try:
        top = float(bbox.get("t"))
        bottom = float(bbox.get("b"))
    except (TypeError, ValueError):
        return None
    height = abs(top - bottom)
    return height if math.isfinite(height) else None


def _valid_source_ref(value: object) -> str:
    if not isinstance(value, dict):
        return ""
    ref = value.get("cref")
    return ref if isinstance(ref, str) and ref else ""


def _source_items_by_ref(document: dict) -> dict[str, dict]:
    items = {}
    if not isinstance(document, dict):
        return items
    for collection in _SOURCE_COLLECTIONS:
        values = document.get(collection)
        if not isinstance(values, list):
            continue
        for item in values:
            if not isinstance(item, dict):
                continue
            ref = item.get("self_ref")
            if isinstance(ref, str) and ref and ref not in items:
                items[ref] = item
    return items


def _outside_structural_ranges(
        item: dict, ranges: Sequence[tuple[int, int]]) -> bool:
    pages = _item_pages(item)
    return bool(pages) and not all(
        _inside_ranges(page, ranges) for page in pages)


def _looks_substantive_page_footer(
        text: str, *, canonical_frequency: int) -> bool:
    compact = " ".join(text.split())
    if not compact or _PAGE_LABEL_RE.fullmatch(compact):
        return False
    if (re.search(r"https?://|\bwww\s*\.|\bdoi\b", compact, re.I)
            or _FOOTNOTE_PREFIX_RE.match(compact)
            or _CITATION_LEAD_RE.match(compact)
            or _LEGAL_CITATION_RE.search(compact)):
        return True
    # Repeated non-citation text is normally running furniture.  Unique prose
    # of meaningful length is retained as a risk signal without making a
    # document-profile-specific claim that it is definitely a footnote.
    return canonical_frequency < 3 and len(compact.split()) >= 6


def _page_footer_analysis(
        document: dict, *, represented_refs: set[str],
        ranges: Sequence[tuple[int, int]]) -> dict:
    texts = document.get("texts") if isinstance(document, dict) else None
    footers = [
        item for item in (texts if isinstance(texts, list) else [])
        if (isinstance(item, dict)
            and _label(item.get("label")) == "page_footer"
            and _outside_structural_ranges(item, ranges))
    ]
    frequencies = Counter(
        _canonical_text(_source_text(item)) for item in footers
        if _canonical_text(_source_text(item)))
    page_labels = 0
    substantive_refs = []
    for item in footers:
        text = " ".join(_source_text(item).split())
        if _PAGE_LABEL_RE.fullmatch(text):
            page_labels += 1
            continue
        canonical = _canonical_text(text)
        if _looks_substantive_page_footer(
                text, canonical_frequency=frequencies.get(canonical, 0)):
            ref = item.get("self_ref")
            if isinstance(ref, str) and ref:
                substantive_refs.append(ref)
    substantive_refs = sorted(set(substantive_refs))
    represented_substantive = sorted(
        set(substantive_refs) & represented_refs)
    risk_refs = sorted(set(substantive_refs) - represented_refs)
    return {
        "total": len(footers),
        "page_label_like": page_labels,
        "repeating_or_short": len(footers) - page_labels
        - len(substantive_refs),
        "substantive_detected": len(substantive_refs),
        "substantive_represented": len(represented_substantive),
        "substantive_risk": len(risk_refs),
        "substantive_risk_refs": risk_refs,
    }


def _page_area(document: dict, page: int) -> float | None:
    pages = document.get("pages") if isinstance(document, dict) else None
    if not isinstance(pages, dict):
        return None
    value = pages.get(str(page), pages.get(page))
    size = value.get("size") if isinstance(value, dict) else None
    width = size.get("width") if isinstance(size, dict) else None
    height = size.get("height") if isinstance(size, dict) else None
    if (not isinstance(width, (int, float)) or isinstance(width, bool)
            or not math.isfinite(float(width)) or width <= 0
            or not isinstance(height, (int, float)) or isinstance(height, bool)
            or not math.isfinite(float(height)) or height <= 0):
        return None
    return float(width) * float(height)


def _large_picture(item: dict, document: dict) -> bool:
    for provenance in item.get("prov") or []:
        if not isinstance(provenance, dict):
            continue
        page = provenance.get("page_no")
        bbox = provenance.get("bbox")
        if (not isinstance(page, int) or isinstance(page, bool) or page < 1
                or not isinstance(bbox, dict)):
            continue
        coordinates = [bbox.get(name) for name in ("l", "t", "r", "b")]
        if any(not isinstance(value, (int, float))
               or isinstance(value, bool) or not math.isfinite(float(value))
               for value in coordinates):
            continue
        width = abs(float(coordinates[2]) - float(coordinates[0]))
        height = abs(float(coordinates[1]) - float(coordinates[3]))
        page_area = _page_area(document, page)
        if (page_area is not None
                and width * height / page_area >= _LARGE_PICTURE_AREA_RATIO):
            return True
    return False


def _picture_text_refs(item: dict, item_by_ref: dict[str, dict]) -> set[str]:
    refs = set()
    # Picture footnotes in casebooks are commonly copyright/source credits;
    # representing that credit does not represent the figure's information.
    for relationship in ("captions", "children"):
        values = item.get(relationship)
        if not isinstance(values, list):
            continue
        for value in values:
            ref = _valid_source_ref(value)
            related = item_by_ref.get(ref)
            if (related is not None
                    and _label(related.get("label")) in {
                        "text", "list_item", "footnote", "caption", "code",
                        "section_header",
                    }):
                refs.add(ref)
    return refs


def _picture_analysis(
        document: dict, *, represented_refs: set[str],
        ranges: Sequence[tuple[int, int]],
) -> dict:
    pictures = document.get("pictures") if isinstance(document, dict) else None
    values = [
        item for item in (pictures if isinstance(pictures, list) else [])
        if (isinstance(item, dict)
            and isinstance(item.get("self_ref"), str)
            and bool(item["self_ref"])
            and _outside_structural_ranges(item, ranges))
    ]
    item_by_ref = _source_items_by_ref(document)
    covered = 0
    covered_by_figure_text = 0
    missing_linked = 0
    large_unlinked = 0
    other_unlinked = 0
    risk_refs = []
    for item in values:
        linked_refs = _picture_text_refs(item, item_by_ref)
        if linked_refs:
            if linked_refs & represented_refs:
                covered += 1
            else:
                missing_linked += 1
                risk_refs.append(item["self_ref"])
        elif item["self_ref"] in represented_refs:
            covered_by_figure_text += 1
        elif _large_picture(item, document):
            large_unlinked += 1
            risk_refs.append(item["self_ref"])
        else:
            other_unlinked += 1
    risk_refs = sorted(set(risk_refs))
    with_linked = covered + missing_linked
    return {
        "total": len(values),
        "with_linked_text": with_linked,
        "covered_by_linked_text": covered,
        "covered_by_figure_text": covered_by_figure_text,
        "missing_linked_text": missing_linked,
        "large_unlinked": large_unlinked,
        "other_unlinked": other_unlinked,
        "substantive_risk": len(risk_refs),
        "substantive_risk_refs": risk_refs,
    }


def _section_heading_analysis(
        document: dict, *, records: Sequence[dict],
        ranges: Sequence[tuple[int, int]],
) -> dict:
    """Recompute occurrence-bound heading ownership from exact source refs."""
    texts = document.get("texts") if isinstance(document, dict) else None
    excluded_refs = {
        item["self_ref"]
        for item in (texts if isinstance(texts, list) else [])
        if (isinstance(item, dict)
            and isinstance(item.get("self_ref"), str)
            and bool(item["self_ref"])
            and _label(item.get("label")) == "section_header"
            and chunking_core.is_probable_misclassified_section_header(
                _source_text(item), bbox_height=_source_bbox_height(item)))
    }
    excluded_refs.update(
        heading_lineage.sparse_ocr_heading_artifact_refs(document))
    audit = heading_lineage.audit_heading_bindings(
        document, records, structural_ranges=ranges,
        excluded_heading_refs=excluded_refs)
    exception_refs = {
        reason: sorted(refs)
        for reason, refs in sorted(audit["exception_refs"].items())
    }
    artifact_refs = {
        reason: sorted(refs)
        for reason, refs in sorted(audit["artifact_refs"].items())
    }
    attachable_items = len(audit["attachable_refs"])
    attached_items = attachable_items - len(audit["unattached_refs"])
    directly_owned_items = (
        attachable_items - len(audit["missing_direct_refs"]))
    binding_evidence = []
    for record in records:
        metadata = record.get("metadata") if isinstance(record, dict) else None
        binding_evidence.append({
            heading_lineage.HEADING_SCHEMA_FIELD: (
                metadata.get(heading_lineage.HEADING_SCHEMA_FIELD)
                if isinstance(metadata, dict) else None),
            heading_lineage.HEADING_PATH_FIELD: (
                metadata.get(heading_lineage.HEADING_PATH_FIELD)
                if isinstance(metadata, dict) else None),
            heading_lineage.DIRECT_HEADING_FIELD: (
                metadata.get(heading_lineage.DIRECT_HEADING_FIELD)
                if isinstance(metadata, dict) else None),
            heading_lineage.HEADING_COMPONENTS_FIELD: (
                metadata.get(heading_lineage.HEADING_COMPONENTS_FIELD)
                if isinstance(metadata, dict) else None),
        })
    ancestor_resumptions = {
        index: sorted(
            proofs,
            key=lambda proof: json.dumps(
                proof, ensure_ascii=False, sort_keys=True,
                separators=(",", ":")),
        )
        for index, proofs in sorted(audit["ancestor_resumptions"].items())
    }
    evidence_payload = {
        "schema_version": heading_lineage.HEADING_LINEAGE_SCHEMA_VERSION,
        "policy": heading_lineage.HEADING_LINEAGE_POLICY,
        "source_refs": audit["source_refs"],
        "attachable_refs": audit["attachable_refs"],
        "exceptions": exception_refs,
        "artifacts": artifact_refs,
        "record_bindings": binding_evidence,
        "ancestor_resumptions": ancestor_resumptions,
    }
    evidence_sha256 = hashlib.sha256(json.dumps(
        evidence_payload, ensure_ascii=False, sort_keys=True,
        separators=(",", ":"),
    ).encode("utf-8")).hexdigest()
    return {
        "schema_version": heading_lineage.HEADING_LINEAGE_SCHEMA_VERSION,
        "policy": heading_lineage.HEADING_LINEAGE_POLICY,
        "source_items": len(audit["source_refs"]),
        "attachable_items": attachable_items,
        "attached_items": attached_items,
        "directly_owned_items": directly_owned_items,
        "exceptions": exception_refs,
        "artifacts": artifact_refs,
        "item_coverage_ppm": (
            attached_items * 1_000_000 // attachable_items
            if attachable_items else 1_000_000),
        "missing_refs": sorted(audit["missing_refs"]),
        "unattached_refs": sorted(audit["unattached_refs"]),
        "missing_direct_refs": sorted(audit["missing_direct_refs"]),
        "duplicate_direct_refs": sorted(audit["duplicate_direct_refs"]),
        "invalid_binding_records": sorted(audit["invalid_binding_records"]),
        "scope_conflict_records": sorted(audit["scope_conflict_records"]),
        "display_binding_records": sorted(audit["display_binding_records"]),
        "ancestor_resumption_count": sum(
            len(proofs) for proofs in ancestor_resumptions.values()),
        "evidence_sha256": evidence_sha256,
    }


def _source_analysis(
        document: dict, *, records: Sequence[dict],
        represented_refs: set[str], ranges: Sequence[tuple[int, int]],
) -> dict:
    return {
        "schema_version": SOURCE_ANALYSIS_SCHEMA_VERSION,
        "available": True,
        "substantive_page_footers": _page_footer_analysis(
            document, represented_refs=represented_refs, ranges=ranges),
        "pictures": _picture_analysis(
            document, represented_refs=represented_refs, ranges=ranges),
        "section_headings": _section_heading_analysis(
            document, records=records, ranges=ranges),
    }


def _unavailable_source_analysis() -> dict:
    return {
        "schema_version": SOURCE_ANALYSIS_SCHEMA_VERSION,
        "available": False,
        "substantive_page_footers": {
            "total": 0,
            "page_label_like": 0,
            "repeating_or_short": 0,
            "substantive_detected": 0,
            "substantive_represented": 0,
            "substantive_risk": 0,
            "substantive_risk_refs": [],
        },
        "pictures": {
            "total": 0,
            "with_linked_text": 0,
            "covered_by_linked_text": 0,
            "covered_by_figure_text": 0,
            "missing_linked_text": 0,
            "large_unlinked": 0,
            "other_unlinked": 0,
            "substantive_risk": 0,
            "substantive_risk_refs": [],
        },
        "section_headings": {
            "schema_version": heading_lineage.HEADING_LINEAGE_SCHEMA_VERSION,
            "policy": heading_lineage.HEADING_LINEAGE_POLICY,
            "source_items": 0,
            "attachable_items": 0,
            "attached_items": 0,
            "directly_owned_items": 0,
            "exceptions": {
                heading_lineage.EMBEDDED_OUTLINE_REASON: [],
                heading_lineage.RUNNING_FURNITURE_REASON: [],
            },
            "artifacts": {
                heading_lineage.SPARSE_OCR_HEADING_ARTIFACT_REASON: [],
            },
            "item_coverage_ppm": 1_000_000,
            "missing_refs": [],
            "unattached_refs": [],
            "missing_direct_refs": [],
            "duplicate_direct_refs": [],
            "invalid_binding_records": [],
            "scope_conflict_records": [],
            "display_binding_records": [],
            "ancestor_resumption_count": 0,
            "evidence_sha256": hashlib.sha256(b"").hexdigest(),
        },
    }


def _valid_source_ref_list(value: object) -> bool:
    return (
        isinstance(value, list)
        and all(isinstance(ref, str) and ref for ref in value)
        and value == sorted(set(value))
    )


def _valid_record_index_list(value: object, record_count: int) -> bool:
    return (
        isinstance(value, list)
        and all(_is_nonnegative_int(index) and index < record_count
                for index in value)
        and value == sorted(set(value))
    )


def _valid_source_analysis(
        value: object, *, record_count: int,
        allow_unavailable: bool = False) -> bool:
    if (not isinstance(value, dict)
            or set(value) != _SOURCE_ANALYSIS_FIELDS
            or value.get("schema_version") != SOURCE_ANALYSIS_SCHEMA_VERSION
            or not isinstance(value.get("available"), bool)):
        return False
    if not value["available"]:
        return allow_unavailable and value == _unavailable_source_analysis()

    footers = value.get("substantive_page_footers")
    pictures = value.get("pictures")
    headings = value.get("section_headings")
    if (not isinstance(footers, dict)
            or set(footers) != _PAGE_FOOTER_ANALYSIS_FIELDS
            or not all(_is_nonnegative_int(footers.get(field))
                       for field in _PAGE_FOOTER_ANALYSIS_FIELDS
                       if field != "substantive_risk_refs")
            or not _valid_source_ref_list(
                footers.get("substantive_risk_refs"))
            or footers["substantive_risk"]
            != len(footers["substantive_risk_refs"])
            or footers["substantive_detected"] != (
                footers["substantive_represented"]
                + footers["substantive_risk"])
            or footers["total"] != (
                footers["page_label_like"]
                + footers["repeating_or_short"]
                + footers["substantive_detected"])):
        return False
    if (not isinstance(pictures, dict)
            or set(pictures) != _PICTURE_ANALYSIS_FIELDS
            or not all(_is_nonnegative_int(pictures.get(field))
                       for field in _PICTURE_ANALYSIS_FIELDS
                       if field != "substantive_risk_refs")
            or not _valid_source_ref_list(
                pictures.get("substantive_risk_refs"))
            or pictures["with_linked_text"] != (
                pictures["covered_by_linked_text"]
                + pictures["missing_linked_text"])
            or pictures["total"] != (
                pictures["with_linked_text"]
                + pictures["covered_by_figure_text"]
                + pictures["large_unlinked"]
                + pictures["other_unlinked"])
            or pictures["substantive_risk"] != (
                pictures["missing_linked_text"]
                + pictures["large_unlinked"])
            or pictures["substantive_risk"]
            != len(pictures["substantive_risk_refs"])):
        return False
    if (not isinstance(headings, dict)
            or set(headings) != _SECTION_HEADING_ANALYSIS_FIELDS
            or headings.get("schema_version")
            != heading_lineage.HEADING_LINEAGE_SCHEMA_VERSION
            or headings.get("policy") != heading_lineage.HEADING_LINEAGE_POLICY
            or not all(_is_nonnegative_int(headings.get(field))
                       for field in (
                           "source_items", "attachable_items",
                           "attached_items", "directly_owned_items",
                           "item_coverage_ppm",
                           "ancestor_resumption_count"))
            or not _valid_sha256(headings.get("evidence_sha256"))
            or not all(_valid_source_ref_list(headings.get(field))
                       for field in (
                           "missing_refs", "unattached_refs",
                           "missing_direct_refs", "duplicate_direct_refs"))
            or not all(_valid_record_index_list(
                headings.get(field), record_count) for field in (
                    "invalid_binding_records", "scope_conflict_records",
                    "display_binding_records"))
            or not isinstance(headings.get("exceptions"), dict)
            or set(headings["exceptions"]) != {
                heading_lineage.RUNNING_FURNITURE_REASON,
                heading_lineage.EMBEDDED_OUTLINE_REASON,
            }
            or not all(_valid_source_ref_list(refs)
                       for refs in headings["exceptions"].values())
            or not isinstance(headings.get("artifacts"), dict)
            or set(headings["artifacts"]) != {
                heading_lineage.SPARSE_OCR_HEADING_ARTIFACT_REASON,
            }
            or not all(_valid_source_ref_list(refs)
                       for refs in headings["artifacts"].values())
            or len({
                ref
                for values in (
                    *headings["exceptions"].values(),
                    *headings["artifacts"].values(),
                )
                for ref in values
            }) != sum(
                len(refs) for refs in (
                    *headings["exceptions"].values(),
                    *headings["artifacts"].values(),
                ))
            or headings["source_items"] != (
                headings["attachable_items"]
                + sum(len(refs) for refs in headings["exceptions"].values())
                + sum(len(refs) for refs in headings["artifacts"].values()))
            or headings["attached_items"] > headings["source_items"]
            or headings["attached_items"] > headings["attachable_items"]
            or headings["directly_owned_items"]
            > headings["attachable_items"]
            or headings["attachable_items"] != (
                headings["attached_items"]
                + len(headings["unattached_refs"]))
            or headings["directly_owned_items"] != (
                headings["attachable_items"]
                - len(headings["missing_direct_refs"]))
            or not set(headings["missing_refs"]).issubset(
                set(headings["unattached_refs"])
                & set(headings["missing_direct_refs"]))
            or headings["item_coverage_ppm"] != (
                headings["attached_items"] * 1_000_000
                // headings["attachable_items"]
                if headings["attachable_items"] else 1_000_000)):
        return False
    return True


def _source_refs(record: dict) -> tuple[set[str], int, list[dict]]:
    metadata = record.get("metadata") or {}
    values = metadata.get("source_items")
    if not isinstance(values, list):
        return set(), 1, []
    refs: set[str] = set()
    invalid = 0
    valid_entries = []
    for item in values:
        if (not isinstance(item, dict)
                or set(item) != source_fidelity_core.LINEAGE_ITEM_FIELDS):
            invalid += 1
            continue
        ref = item.get("ref")
        spans = item.get("spans")
        parent_refs = item.get("parent_refs", [])
        label = item.get("label")
        transform = item.get("transform")
        recovery_sha256 = item.get("recovery_sha256")
        if (not isinstance(ref, str) or not ref
                or not isinstance(label, str) or not label
                or not isinstance(spans, list)
                or source_fidelity_core.lineage_scope_indexes(item) is None
                or not isinstance(parent_refs, list)
                or any(not isinstance(parent, str) or not parent
                       for parent in parent_refs)
                or not _valid_sha256(item.get("source_text_sha256"))
                or not _valid_sha256(item.get("source_lexical_sha256"))
                or not _is_nonnegative_int(item.get("source_lexical_count"))
                or transform not in source_fidelity_core.ALLOWED_TRANSFORMS
                or not _valid_sha256(item.get("oracle_text_sha256"))
                or not _valid_sha256(item.get("oracle_lexical_sha256"))
                or not _is_nonnegative_int(item.get("oracle_lexical_count"))
                or (recovery_sha256 is not None
                    and not _valid_sha256(recovery_sha256))):
            invalid += 1
            continue
        entry_valid = True
        for span in spans:
            if (not isinstance(span, dict)
                    or set(span) != source_fidelity_core.LINEAGE_SPAN_FIELDS):
                invalid += 1
                entry_valid = False
                continue
            page = span.get("page")
            bbox = span.get("bbox")
            provenance_index = span.get("provenance_index")
            charspan = span.get("charspan")
            if (not isinstance(page, int) or isinstance(page, bool) or page < 1
                    or not _is_nonnegative_int(provenance_index)
                    or not isinstance(bbox, list) or len(bbox) != 4
                    or any(not isinstance(value, (int, float))
                           or isinstance(value, bool)
                           or not math.isfinite(value) for value in bbox)
                    or not isinstance(charspan, list) or len(charspan) != 2
                    or any(not _is_nonnegative_int(value)
                           for value in charspan)
                    or charspan[1] < charspan[0]):
                invalid += 1
                entry_valid = False
            origin = span.get("origin")
            if origin not in {"BOTTOMLEFT", "TOPLEFT"}:
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
        input_bindings: dict, source_oracle_registry: dict,
) -> dict:
    """Build a deterministic, release-gating report for one chunks artifact."""
    if len(stable_ids) != len(records) or len(chunk_hashes) != len(records):
        raise ValueError(
            "stable_ids and chunk_hashes must align one-to-one with records")
    if not _valid_input_bindings(input_bindings):
        raise ValueError("quality input bindings are invalid")
    trusted_source_oracles = (
        source_fidelity_core.validate_source_oracle_registry(
            source_oracle_registry,
            expected_input_bindings=_source_oracle_upstream_inputs(
                input_bindings)))

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
    normalization_issues = _normalization_issues(records)
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
                field for field in (
                    "label", "spans", "parent_refs", "source_text_sha256",
                    "source_lexical_sha256", "source_lexical_count")
                if entry.get(field) != expected_item[field]
            ]
            if mismatched_fields:
                lineage_metadata_mismatches.append({
                    "chunk_index": index,
                    "ref": ref,
                    "fields": mismatched_fields,
                })

        scoped_spans_by_entry = [
            [
                span for span in entry["spans"]
                if span["provenance_index"] in set(
                    source_fidelity_core.lineage_scope_indexes(entry) or ())
            ]
            for entry in lineage_entries
        ]
        lineage_pages = {
            span["page"]
            for spans in scoped_spans_by_entry
            for span in spans
        }
        lineage_structural_leak = any(
            spans
            and all(_inside_ranges(span["page"], ranges)
                    for span in spans)
            for spans in scoped_spans_by_entry
        )

        page_start = metadata.get("page_start")
        page_end = metadata.get("page_end")
        valid_pages = (
            isinstance(page_start, int) and not isinstance(page_start, bool)
            and isinstance(page_end, int) and not isinstance(page_end, bool)
            and 1 <= page_start <= page_end
        )
        lineage_bounds_match = (
            not lineage_pages
            or (valid_pages
                and page_start == min(lineage_pages)
                and page_end == max(lineage_pages))
        )
        if not valid_pages or not lineage_bounds_match:
            page_metadata_issues.append(index)
        if (lineage_structural_leak
                or (valid_pages and any(
                    start <= page_start and page_end <= end
                    for start, end in ranges))):
            structural_leaks.append(index)
        if valid_pages and not table_retrieval_core.is_table_child(metadata):
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
                elif (previous_page_index == index - 1
                      and previous_page_metadata is not None
                      and _allowed_cross_page_footnote_regression(
                          previous_page_metadata, metadata,
                          previous_page=previous_page, page=page_start)):
                    evidence.update({
                        "previous_content_source": previous_page_metadata.get(
                            "content_source"),
                        "content_source": metadata.get("content_source"),
                        PAGE_ORDER_REASON_FIELD: metadata.get(
                            PAGE_ORDER_REASON_FIELD),
                        "reason": FOOTNOTE_AFTER_CONTINUATION_REASON,
                    })
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
        text = str(record.get("text") or "")
        canonical = _canonical_text(text)
        if canonical:
            canonical_groups[canonical].append(index)
        content_type = str(metadata.get("content_type") or "")
        content_source = str(metadata.get("content_source") or "")
        if not content_type:
            classification_issues["missing_content_type"].append(index)
        if content_source not in {
                "body", "footnote", "table", "mixed", "figure"}:
            classification_issues["unknown_content_source"].append(index)
        if content_source == "table" and content_type != "table":
            classification_issues["table_source_mismatch"].append(index)
        if content_source == "footnote" and content_type != "footnote":
            classification_issues["footnote_source_mismatch"].append(index)
        if content_source == "figure" and content_type != "figure":
            classification_issues["figure_source_mismatch"].append(index)

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
    retrieval = retrieval_core._retrieval_linkage_summary(
        records, stable_ids=stable_ids)
    retrieval_issue_count = sum(
        len(indexes) for indexes in retrieval["issues"].values())
    table_retrieval = table_retrieval_core._table_retrieval_summary(
        records, stable_ids=stable_ids,
        source_table_dimensions=_source_table_dimensions(document))
    table_retrieval_issue_count = sum(
        len(indexes) for indexes in table_retrieval["issues"].values())
    source_analysis = _source_analysis(
        document, records=records, represented_refs=represented_refs,
        ranges=ranges)
    footer_risk_count = source_analysis[
        "substantive_page_footers"]["substantive_risk"]
    picture_risk_count = source_analysis["pictures"]["substantive_risk"]
    heading_analysis = source_analysis["section_headings"]
    heading_issue_count = sum(len(heading_analysis[field]) for field in (
        "missing_refs", "unattached_refs", "missing_direct_refs",
        "duplicate_direct_refs", "invalid_binding_records",
        "scope_conflict_records", "display_binding_records",
    ))
    fidelity = source_fidelity_core.audit_source_fidelity(
        records=records, document=document, eligible_refs=eligible_refs,
        recovery_binding=input_bindings.get("table_recovery"),
        source_oracles=trusted_source_oracles)
    source_geometry_issue_count = len(fidelity["geometry_issues"])
    source_token_issue_count = (
        len(fidelity["source_hash_mismatches"])
        + len(fidelity["lineage_issues"])
        + len(fidelity["output_coverage_issues"])
        + len(fidelity["source_coverage_issues"])
    )

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
        _check("source_geometry_complete",
               failed=bool(source_geometry_issue_count),
               observed=source_geometry_issue_count, required=0),
        _check("source_token_fidelity",
               failed=bool(source_token_issue_count),
               observed=source_token_issue_count, required=0),
        _check("source_tables_represented",
               failed=bool(missing_table_refs),
               observed=len(missing_table_refs), required=0),
        _check("page_metadata_valid",
               failed=bool(page_metadata_issues),
               observed=len(page_metadata_issues), required=0),
        _check("page_regressions", failed=bool(unexpected_page_regressions),
               observed=len(unexpected_page_regressions), required=0),
        _check("same_page_reading_order",
               failed=bool(fidelity["geometry_violations"]),
               observed=len(fidelity["geometry_violations"]), required=0),
        _check("structural_ranges_excluded",
               failed=bool(structural_leaks),
               observed=len(structural_leaks), required=0),
        _check("contiguous_chunk_indexes", failed=bool(chunk_index_issues),
               observed=len(chunk_index_issues), required=0),
        _check("retrieval_linkage_invariants",
               failed=bool(retrieval_issue_count),
               observed=retrieval_issue_count, required=0),
        _check("table_retrieval_invariants",
               failed=bool(table_retrieval_issue_count),
               observed=table_retrieval_issue_count, required=0),
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
        _check("substantive_excluded_page_footers",
               failed=bool(footer_risk_count), observed=footer_risk_count,
               required=0, warning=True),
        _check("uncovered_substantive_pictures",
               failed=bool(picture_risk_count), observed=picture_risk_count,
               required=0, warning=True),
        _check("section_heading_attachment",
               failed=bool(heading_issue_count),
               observed=heading_issue_count, required=0),
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
        "inputs": input_bindings,
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
            "fidelity": fidelity,
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
        "normalization": normalization_issues,
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
        "retrieval": retrieval,
        "table_retrieval": table_retrieval,
        "source_analysis": source_analysis,
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
        input_bindings: dict | None = None,
        source_oracle_registry: dict | None = None,
        recovered_table_count: int | None = None,
        recovered_table_refs: Sequence[str] | None = None,
        records: Sequence[dict] | None = None,
        document: dict | None = None,
        structural_ranges: Iterable[tuple[int, int]] = (),
        _allow_unavailable_source_analysis: bool = False,
) -> dict:
    """Validate report schema, pass state, and exact chunks binding."""
    if not isinstance(payload, dict):
        raise ValueError("corpus quality report must be a JSON object")
    if set(payload) != _QUALITY_REPORT_FIELDS:
        raise ValueError("corpus quality report has an invalid field set")
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
    docling = source.get("docling_json") if isinstance(source, dict) else None
    if (not isinstance(source, dict)
            or set(source) != {"docling_json", "chunks_jsonl"}
            or not isinstance(docling, dict)
            or set(docling) != {"name", "sha256"}
            or not isinstance(docling.get("name"), str)
            or not docling["name"]
            or Path(docling["name"]).name != docling["name"]
            or not _valid_sha256(docling.get("sha256"))
            or not isinstance(chunks, dict)
            or set(chunks) != {
                "name", "sha256", "size", "record_count"}
            or not isinstance(chunks.get("name"), str)
            or not chunks["name"]
            or Path(chunks["name"]).name != chunks["name"]
            or not _valid_sha256(chunks.get("sha256"))
            or not _is_nonnegative_int(chunks.get("size"))
            or chunks["size"] <= 0
            or not _is_nonnegative_int(chunks.get("record_count"))):
        raise ValueError("corpus quality report source binding is invalid")
    expected = {
        "name": chunks_name,
        "sha256": chunks_sha256,
        "size": chunks_size,
        "record_count": record_count,
    }
    if not isinstance(chunks, dict) or any(
            chunks.get(key) != value for key, value in expected.items()):
        raise ValueError("corpus quality report does not match chunks artifact")
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
    manifested_inputs = payload.get("inputs")
    if not _valid_input_bindings(manifested_inputs):
        raise ValueError("corpus quality report inputs are invalid")
    docling_input = manifested_inputs["docling_json"]
    if docling != {
            "name": docling_input["name"],
            "sha256": docling_input["sha256"],
    }:
        raise ValueError(
            "corpus quality report source and input bindings disagree")
    if input_bindings is not None and manifested_inputs != input_bindings:
        raise ValueError("corpus quality report does not match input bindings")
    try:
        trusted_source_oracles = (
            source_fidelity_core.validate_source_oracle_registry(
                source_oracle_registry,
                expected_input_bindings=_source_oracle_upstream_inputs(
                    manifested_inputs)))
    except ValueError as exc:
        raise ValueError(
            "corpus quality report source oracle registry is invalid") \
            from exc
    if not _valid_sha256(payload.get("parameters_sha256")):
        raise ValueError("corpus quality report parameters are invalid")
    tables = payload.get("tables")
    recovered_count = (
        tables.get("recovered_from_pdf") if isinstance(tables, dict) else None)
    if (not _is_nonnegative_int(recovered_count)
            or (recovered_count > 0
                and manifested_inputs.get("table_recovery") is None)):
        raise ValueError("corpus quality report recovery binding is invalid")
    if (recovered_table_count is not None
            and (not _is_nonnegative_int(recovered_table_count)
                 or recovered_count != recovered_table_count)):
        raise ValueError(
            "corpus quality report does not match recovered source tables")
    recovered_refs = tables.get("recovered_refs") if isinstance(tables, dict) \
        else None
    if (not isinstance(recovered_refs, list)
            or any(not isinstance(ref, str) or not ref
                   for ref in recovered_refs)
            or recovered_refs != sorted(set(recovered_refs))
            or len(recovered_refs) != recovered_count):
        raise ValueError("corpus quality report recovered refs are invalid")
    if recovered_table_refs is not None:
        expected_recovered_refs = sorted(set(recovered_table_refs))
        if (any(not isinstance(ref, str) or not ref
                for ref in recovered_table_refs)
                or recovered_refs != expected_recovered_refs):
            raise ValueError(
                "corpus quality report does not match recovered source refs")
    embedding = payload.get("embedding")
    if (not isinstance(embedding, dict)
            or set(embedding) != _EMBEDDING_FIELDS
            or not isinstance(embedding.get("model"), str)
            or not embedding["model"]
            or (embedding.get("limit") is not None
                and not _is_nonnegative_int(embedding["limit"]))
            or any(
                value is not None and not _is_nonnegative_int(value)
                for key, value in embedding.items()
                if key not in {"model", "limit"}
            )
            or embedding.get("inputs_over_limit") != 0):
        raise ValueError("corpus quality report embedding is invalid")
    for prefix in ("raw_token", "input_token"):
        ordered = [
            embedding[f"{prefix}_{suffix}"]
            for suffix in ("min", "p50", "p95", "p99", "max")
        ]
        if any(not _is_nonnegative_int(value) for value in ordered) \
                or ordered != sorted(ordered):
            raise ValueError(
                "corpus quality report embedding statistics are invalid")
    if (embedding["limit"] is not None
            and embedding["input_token_max"] > embedding["limit"]):
        raise ValueError(
            "corpus quality report embedding statistics exceed the limit")
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
            or set(hashes) != {
                "stable_id_root_sha256", "chunk_hash_root_sha256",
                "unique_stable_ids"}
            or hashes.get("stable_id_root_sha256") != expected_stable_root
            or hashes.get("chunk_hash_root_sha256") != expected_chunk_root
            or not _is_nonnegative_int(hashes.get("unique_stable_ids"))
            or hashes.get("unique_stable_ids") != len(set(stable_ids))):
        raise ValueError("corpus quality report hash roots do not match chunks")
    checks = payload.get("checks")
    if not isinstance(checks, list):
        raise ValueError("corpus quality report has invalid checks")
    checks_by_name = {}
    for check in checks:
        if (not isinstance(check, dict)
                or set(check) != {"name", "status", "observed", "required"}):
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
    if set(checks_by_name) != _QUALITY_CHECK_NAMES:
        raise ValueError(
            "corpus quality report check set does not match schema")
    if len(checks_by_name) != len(checks):
        raise ValueError("corpus quality report contains duplicate checks")
    source_analysis = payload.get("source_analysis")
    if not _valid_source_analysis(
            source_analysis, record_count=record_count,
            allow_unavailable=_allow_unavailable_source_analysis):
        raise ValueError("corpus quality report source analysis is invalid")
    if source_analysis["available"]:
        expected_warning_counts = {
            "substantive_excluded_page_footers": source_analysis[
                "substantive_page_footers"]["substantive_risk"],
            "uncovered_substantive_pictures": source_analysis[
                "pictures"]["substantive_risk"],
        }
        if any(checks_by_name[name].get("observed") != count
               for name, count in expected_warning_counts.items()):
            raise ValueError(
                "corpus quality report source warning checks disagree")
        heading_analysis = source_analysis["section_headings"]
        heading_issue_count = sum(len(heading_analysis[field]) for field in (
            "missing_refs", "unattached_refs", "missing_direct_refs",
            "duplicate_direct_refs", "invalid_binding_records",
            "scope_conflict_records", "display_binding_records",
        ))
        if checks_by_name["section_heading_attachment"].get(
                "observed") != heading_issue_count:
            raise ValueError(
                "corpus quality report heading check disagrees")
    lineage = payload.get("source_lineage")
    lineage_version = (
        lineage.get("schema_version") if isinstance(lineage, dict) else None)
    if (not isinstance(lineage, dict)
            or set(lineage) != _SOURCE_LINEAGE_FIELDS
            or not isinstance(lineage_version, int)
            or isinstance(lineage_version, bool)
            or lineage_version != SOURCE_LINEAGE_SCHEMA_VERSION
            or lineage.get("inventory_issues") != {}
            or lineage.get("missing_refs") != []
            or lineage.get("unknown_refs") != []
            or lineage.get("chunks_without_lineage") != []
            or not _is_nonnegative_int(lineage.get("invalid_entries"))
            or lineage.get("invalid_entries") != 0
            or lineage.get("metadata_mismatches") != []
            or lineage.get("schema_issues") != []
            or not source_fidelity_core.validate_summary(
                lineage.get("fidelity"))
            or lineage["fidelity"].get("source_oracle_root_sha256")
            != source_fidelity_core.source_oracle_root_sha256(
                trusted_source_oracles)
            or not _is_nonnegative_int(lineage.get("eligible_items"))
            or not _is_nonnegative_int(lineage.get("represented_items"))
            or lineage.get("represented_items")
            != lineage.get("eligible_items")
            or lineage.get("coverage_ppm") != 1_000_000
            or not _valid_count_mapping(
                lineage.get("excluded_items_by_reason"))):
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
            or set(corpus) != _CORPUS_FIELDS
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
    duplicate_groups = corpus.get("canonical_duplicate_groups")
    warning_check = checks_by_name.get("canonical_text_duplicates")
    if (not _valid_count_mapping(
            corpus.get("content_types"), expected_total=record_count)
            or not _valid_count_mapping(
                corpus.get("content_sources"), expected_total=record_count)
            or not _valid_count_mapping(
                corpus.get("chapter_counts"), expected_total=record_count)
            or corpus.get("page_metadata_issues") != []
            or corpus.get("structural_leaks") != []
            or corpus.get("chunk_index_issues") != []
            or not isinstance(duplicate_groups, list)
            or any(not _valid_index_list(
                group, record_count=record_count, minimum_length=2)
                for group in duplicate_groups)
            or warning_check.get("observed") != len(duplicate_groups)):
        raise ValueError("corpus quality report corpus details are invalid")
    retrieval = payload.get("retrieval")
    if (not isinstance(retrieval, dict)
            or set(retrieval) != _RETRIEVAL_FIELDS
            or retrieval.get("schema_version")
            != retrieval_core.RETRIEVAL_LINKAGE_SCHEMA_VERSION
            or not _is_nonnegative_int(retrieval.get("context_parents"))
            or retrieval["context_parents"] > record_count
            or not _is_nonnegative_int(retrieval.get("linked_chunks"))
            or not _is_nonnegative_int(retrieval.get("isolated_chunks"))
            or retrieval["linked_chunks"] + retrieval["isolated_chunks"]
            != record_count
            or not _valid_issue_mapping(
                retrieval.get("issues"), record_count=record_count,
                require_empty=True)):
        raise ValueError(
            "corpus quality report retrieval linkage gate is invalid")
    table_retrieval = payload.get("table_retrieval")
    if (not isinstance(table_retrieval, dict)
            or set(table_retrieval) != _TABLE_RETRIEVAL_FIELDS
            or table_retrieval.get("schema_version")
            != table_retrieval_core.TABLE_RETRIEVAL_SCHEMA_VERSION
            or not _is_nonnegative_int(
                table_retrieval.get("parent_tables"))
            or table_retrieval["parent_tables"] > record_count
            or not _is_nonnegative_int(
                table_retrieval.get("expanded_parents"))
            or table_retrieval["expanded_parents"]
            > table_retrieval["parent_tables"]
            or not _is_nonnegative_int(
                table_retrieval.get("child_chunks"))
            or table_retrieval["child_chunks"] > record_count
            or table_retrieval["parent_tables"]
            + table_retrieval["child_chunks"] > record_count
            or not _valid_issue_mapping(
                table_retrieval.get("issues"), record_count=record_count,
                require_empty=True)):
        raise ValueError(
            "corpus quality report table retrieval gate is invalid")
    raw_token_count = (
        embedding.get("raw_token_count")
        if isinstance(embedding, dict) else None)
    if (not isinstance(embedding, dict)
            or not _is_nonnegative_int(raw_token_count)
            or raw_token_count != record_count):
        raise ValueError("corpus quality report raw token gate is invalid")
    eligible_tables = tables.get("eligible_source_tables") \
        if isinstance(tables, dict) else None
    represented_tables = tables.get("represented_source_tables") \
        if isinstance(tables, dict) else None
    if (not isinstance(tables, dict) or set(tables) != _TABLE_FIELDS
            or not _is_nonnegative_int(eligible_tables)
            or not _is_nonnegative_int(represented_tables)
            or represented_tables != eligible_tables
            or tables.get("missing_refs") != []
            or recovered_count > represented_tables
            or not _is_nonnegative_int(tables.get("published_table_chunks"))
            or tables.get("published_table_chunks")
            != corpus["content_types"].get("table", 0)
            or not _valid_issue_mapping(
                tables.get("issues"), record_count=record_count,
                require_empty=True)
            or not _valid_issue_mapping(
                payload.get("normalization"), record_count=record_count,
                require_empty=True)
            or not isinstance(payload.get("classification"), dict)
            or set(payload["classification"]) != {"issues"}
            or not _valid_issue_mapping(
                payload["classification"]["issues"],
                record_count=record_count, require_empty=True)
            or not isinstance(payload.get("entities"), dict)
            or set(payload["entities"]) != {"issues", "mentions", "unique"}
            or not _valid_issue_mapping(
                payload["entities"]["issues"],
                record_count=record_count, require_empty=True)
            or not _is_nonnegative_int(payload["entities"]["mentions"])
            or not _is_nonnegative_int(payload["entities"]["unique"])
            or payload["entities"]["unique"]
            > payload["entities"]["mentions"]):
        raise ValueError("corpus quality report detail schema is invalid")
    if records is not None:
        if len(records) != record_count:
            raise ValueError(
                "corpus quality report record summary input is misaligned")
        if payload["normalization"] != _normalization_issues(records):
            raise ValueError(
                "corpus quality report normalization does not match records")
        for record in records:
            metadata = record.get("metadata")
            if (not isinstance(metadata, dict)
                    or metadata.get("retrieval_role") == "table_child"):
                continue
            if metadata.get("source_fidelity") \
                    != source_fidelity_core.record_attestation(record):
                raise ValueError(
                    "corpus quality report output attestation is stale")
        expected_output_tokens = source_fidelity_core.output_lexical_count(
            records)
        fidelity_summary = lineage["fidelity"]
        if (fidelity_summary.get("output_tokens") != expected_output_tokens
                or fidelity_summary.get("covered_output_tokens")
                != expected_output_tokens
                or fidelity_summary.get("output_attestation_root_sha256")
                != source_fidelity_core.output_attestation_root_sha256(
                    records)):
            raise ValueError(
                "corpus quality report output fidelity does not match records")
        expected_retrieval = retrieval_core._retrieval_linkage_summary(
            records, stable_ids=list(stable_ids))
        if retrieval != expected_retrieval:
            raise ValueError(
                "corpus quality report retrieval summary does not match "
                "records")
        expected_table_retrieval = (
            table_retrieval_core._table_retrieval_summary(
                records, stable_ids=list(stable_ids)))
        if table_retrieval != expected_table_retrieval:
            raise ValueError(
                "corpus quality report table retrieval summary does not "
                "match records")
        expected_types = Counter()
        expected_sources = Counter()
        expected_chapters = Counter()
        raw_counts = []
        input_counts = []
        case_names = []
        for record in records:
            metadata = record.get("metadata")
            if not isinstance(metadata, dict):
                raise ValueError(
                    "corpus quality report record metadata is invalid")
            expected_types[str(metadata.get("content_type") or "")] += 1
            expected_sources[str(metadata.get("content_source") or "")] += 1
            expected_chapters[str(metadata.get("chapter_num"))] += 1
            raw_counts.append(metadata.get("token_count"))
            input_counts.append(metadata.get("embedding_token_count"))
            names = metadata.get("case_names")
            if (not isinstance(names, list)
                    or any(not isinstance(name, str) for name in names)):
                raise ValueError(
                    "corpus quality report case-name summary is invalid")
            case_names.extend(names)
        if (corpus["content_types"] != dict(sorted(expected_types.items()))
                or corpus["content_sources"]
                != dict(sorted(expected_sources.items()))
                or corpus["chapter_counts"]
                != dict(sorted(expected_chapters.items()))):
            raise ValueError(
                "corpus quality report summaries do not match records")
        if (any(not _is_nonnegative_int(value) for value in raw_counts)
                or any(not _is_nonnegative_int(value)
                       for value in input_counts)):
            raise ValueError(
                "corpus quality report token summaries are invalid")
        expected_embedding = {
            "raw_token_min": min(raw_counts),
            "raw_token_max": max(raw_counts),
            "raw_token_p50": _nearest_rank(raw_counts, 50),
            "raw_token_p95": _nearest_rank(raw_counts, 95),
            "raw_token_p99": _nearest_rank(raw_counts, 99),
            "raw_token_count": len(raw_counts),
            "input_token_min": min(input_counts),
            "input_token_max": max(input_counts),
            "input_token_p50": _nearest_rank(input_counts, 50),
            "input_token_p95": _nearest_rank(input_counts, 95),
            "input_token_p99": _nearest_rank(input_counts, 99),
            "inputs_over_limit": (
                sum(value > embedding["limit"] for value in input_counts)
                if embedding["limit"] is not None else 0),
        }
        if any(embedding.get(key) != value
               for key, value in expected_embedding.items()):
            raise ValueError(
                "corpus quality report token summaries do not match records")
        actual_names = [name for name in case_names if isinstance(name, str)]
        if (payload["entities"]["mentions"] != len(case_names)
                or payload["entities"]["unique"] != len(set(actual_names))):
            raise ValueError(
                "corpus quality report entity summaries do not match records")
    if document is not None:
        if records is None:
            raise ValueError(
                "source-backed quality validation requires exact records")
        represented_refs = set()
        for record in records:
            refs, _, _ = _source_refs(record)
            represented_refs.update(refs)
        ranges = tuple(sorted(set(structural_ranges)))
        expected_source_analysis = _source_analysis(
            document, records=records, represented_refs=represented_refs,
            ranges=ranges)
        if source_analysis != expected_source_analysis:
            raise ValueError(
                "corpus quality report source analysis does not match source")
        (_, expected_eligible, _, _) = source_inventory(
            document, structural_ranges=ranges)
        expected_fidelity = source_fidelity_core.audit_source_fidelity(
            records=records, document=document,
            eligible_refs=set(expected_eligible),
            recovery_binding=manifested_inputs.get("table_recovery"),
            source_oracles=trusted_source_oracles)
        if payload["source_lineage"].get("fidelity") != expected_fidelity:
            raise ValueError(
                "corpus quality report fidelity does not match source")
    return payload


def read_quality_report(
        path: Path, *, chunks_name: str, chunks_sha256: str,
        chunks_size: int, record_count: int, stable_ids: Sequence[str],
        chunk_hashes: Sequence[str], source_name: str | None = None,
        source_sha256: str | None = None,
        parameters_sha256: str | None = None,
        embedding_model: str | None = None,
        embedding_limit: int | None = None,
        input_bindings: dict | None = None,
        source_oracle_registry: dict | None = None,
        recovered_table_count: int | None = None,
        recovered_table_refs: Sequence[str] | None = None,
        records: Sequence[dict] | None = None,
        document: dict | None = None,
        structural_ranges: Iterable[tuple[int, int]] = (),
        compatible_schema_versions: Sequence[int] = (),
) -> dict:
    try:
        with Path(path).open("rb") as handle:
            raw = handle.read(MAX_QUALITY_REPORT_BYTES + 1)
    except OSError as exc:
        raise ValueError(f"cannot read corpus quality report: {path}") from exc
    return parse_quality_report_bytes(
        raw, chunks_name=chunks_name, chunks_sha256=chunks_sha256,
        chunks_size=chunks_size, record_count=record_count,
        stable_ids=stable_ids, chunk_hashes=chunk_hashes,
        source_name=source_name, source_sha256=source_sha256,
        parameters_sha256=parameters_sha256,
        embedding_model=embedding_model, embedding_limit=embedding_limit,
        input_bindings=input_bindings,
        source_oracle_registry=source_oracle_registry,
        recovered_table_count=recovered_table_count,
        recovered_table_refs=recovered_table_refs,
        records=records, document=document,
        structural_ranges=structural_ranges,
        compatible_schema_versions=compatible_schema_versions)


def parse_quality_report_bytes(
        raw: bytes, *, chunks_name: str, chunks_sha256: str,
        chunks_size: int, record_count: int, stable_ids: Sequence[str],
        chunk_hashes: Sequence[str], source_name: str | None = None,
        source_sha256: str | None = None,
        parameters_sha256: str | None = None,
        embedding_model: str | None = None,
        embedding_limit: int | None = None,
        input_bindings: dict | None = None,
        source_oracle_registry: dict | None = None,
        recovered_table_count: int | None = None,
        recovered_table_refs: Sequence[str] | None = None,
        records: Sequence[dict] | None = None,
        document: dict | None = None,
        structural_ranges: Iterable[tuple[int, int]] = (),
        compatible_schema_versions: Sequence[int] = (),
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
    # Historical reports cannot attest lineage-v2 token or geometry proofs.
    # ``compatible_schema_versions`` remains an API placeholder for callers
    # performing migrations, but never inflates stale evidence into a pass.
    return validate_quality_report(
        payload, chunks_name=chunks_name,
        chunks_sha256=chunks_sha256,
        chunks_size=chunks_size, record_count=record_count,
        stable_ids=stable_ids, chunk_hashes=chunk_hashes,
        source_name=source_name, source_sha256=source_sha256,
        parameters_sha256=parameters_sha256,
        embedding_model=embedding_model, embedding_limit=embedding_limit,
        input_bindings=input_bindings,
        source_oracle_registry=source_oracle_registry,
        recovered_table_count=recovered_table_count,
        recovered_table_refs=recovered_table_refs, records=records,
        document=document, structural_ranges=structural_ranges)
