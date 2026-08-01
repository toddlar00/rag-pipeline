"""Deterministic source-token and same-page geometry fidelity checks.

This module is deliberately standard-library-only.  It treats the captured
Docling JSON as the textual/layout oracle and the chunk lineage metadata as a
versioned attestation.  PDF-native, table, and figure recovery are supported
only through a small closed set of typed transforms with hash-bound oracles.
"""

from __future__ import annotations

from collections import Counter, defaultdict
import hashlib
import json
import math
from pathlib import Path
import re
import unicodedata
from typing import Iterable, Mapping, Sequence


SOURCE_FIDELITY_SCHEMA_VERSION = 7
SOURCE_ORACLE_REGISTRY_SCHEMA_VERSION = 3
MAX_SOURCE_ORACLE_REGISTRY_BYTES = 16 * 1024 * 1024

SOURCE_COLLECTIONS = (
    "texts", "tables", "pictures", "key_value_items", "form_items",
)
ALLOWED_TRANSFORMS = frozenset({
    "plain", "list", "table", "figure", "native_repair",
    "container_alias",
})
OPAQUE_TRANSFORMS = frozenset({
    "table", "figure", "native_repair", "container_alias",
})
ALLOWED_ORDER_EXEMPTIONS = frozenset({
    "figure_sidecar",
    "footnote_sidecar",
    "table_fragment",
})

LINEAGE_SPAN_FIELDS = frozenset({
    "provenance_index", "page", "bbox", "origin", "charspan",
})
LINEAGE_SCOPE_FIELDS = frozenset({"provenance_indexes"})
LINEAGE_ITEM_FIELDS = frozenset({
    "ref", "label", "parent_refs", "spans", "source_text_sha256",
    "source_lexical_sha256", "source_lexical_count", "transform",
    "oracle_text_sha256", "oracle_lexical_sha256",
    "oracle_lexical_count", "recovery_sha256", "scope",
})
RECORD_ATTESTATION_FIELDS = frozenset({
    "schema_version", "output_text_sha256", "output_lexical_sha256",
    "output_lexical_count", "opaque_output_sha256", "opaque_source_refs",
})
ORDER_EXEMPTION_FIELDS = frozenset({
    "before_ref", "after_ref", "page", "reason",
})
SUMMARY_FIELDS = frozenset({
    "schema_version", "source_tokens", "covered_source_tokens",
    "output_tokens", "covered_output_tokens", "source_hash_mismatches",
    "lineage_issues", "output_coverage_issues",
    "source_coverage_issues", "geometry_issues", "geometry_constraints",
    "geometry_violations", "order_exemptions", "transform_counts",
    "source_descriptor_root_sha256", "output_attestation_root_sha256",
    "source_oracle_root_sha256", "geometry_root_sha256", "evidence_sha256",
})
ORACLE_DESCRIPTOR_FIELDS = frozenset({
    "ref", "transform", "source_text_sha256", "oracle_text_sha256",
    "oracle_lexical_sha256", "oracle_lexical_count",
    "oracle_lexical_tokens", "oracle_group_sha256", "oracle_group_members",
    "recovery_sha256",
})
ORACLE_REGISTRY_FIELDS = frozenset({
    "schema_version", "kind", "source_fidelity_schema_version", "inputs",
    "oracles", "oracle_root_sha256", "evidence_sha256",
})

_TOKEN_RE = re.compile(r"[^\W_]+(?:'[^\W_]+)*|\d+", re.UNICODE)
_DEHYPHENATE_RE = re.compile(
    r"(?<=[^\W\d_])-\s*(?:\r?\n)\s*(?=[^\W\d_])", re.UNICODE)
_SHA256_RE = re.compile(r"[0-9a-f]{64}")
_CURLY_APOSTROPHE_TRANSLATION = str.maketrans({
    "\u2018": "'",  # LEFT SINGLE QUOTATION MARK
    "\u2019": "'",  # RIGHT SINGLE QUOTATION MARK
})


def _canonical_json(value: object) -> bytes:
    return json.dumps(
        value, ensure_ascii=False, sort_keys=True, separators=(",", ":"),
    ).encode("utf-8")


def text_sha256(text: str) -> str:
    """Return the exact UTF-8 text digest used in lineage attestations."""
    return hashlib.sha256(text.encode("utf-8")).hexdigest()


def _normalized_lexical_text(text: str) -> str:
    normalized = unicodedata.normalize("NFKC", str(text or ""))
    normalized = normalized.translate(_CURLY_APOSTROPHE_TRANSLATION)
    normalized = normalized.replace("\u00ad", "")
    return _DEHYPHENATE_RE.sub("", normalized)


def lexical_tokens(text: str) -> tuple[str, ...]:
    """Return stable lexical tokens while ignoring presentation punctuation."""
    normalized = _normalized_lexical_text(text)
    return tuple(match.group(0).casefold() for match in _TOKEN_RE.finditer(
        normalized))


def _punctuation_list_marker(item: dict) -> str | None:
    """Return one normalized marker that lexical tokenization cannot see."""
    if _label(item.get("label")) != "list_item":
        return None
    marker = unicodedata.normalize(
        "NFKC", str(item.get("marker") or "").strip())
    if marker in {"\u00b7", "\u2022"}:
        marker = "-"
    return marker if marker and not lexical_tokens(marker) else None


def _punctuation_marker_occurrences(
        normalized_text: str, marker: str,
) -> tuple[tuple[int, int, int], ...]:
    """Return line-bound marker spans plus their containing line end."""
    pattern = re.compile(
        rf"(?m)^[^\S\r\n]*{re.escape(marker)}(?=[^\S\r\n]|$)")
    result = []
    for match in pattern.finditer(normalized_text):
        line_end = normalized_text.find("\n", match.end())
        result.append((
            match.start(), match.end(),
            len(normalized_text) if line_end < 0 else line_end,
        ))
    return tuple(result)


def lexical_sha256(value: str | Sequence[str]) -> str:
    tokens = lexical_tokens(value) if isinstance(value, str) else tuple(value)
    return hashlib.sha256("\0".join(tokens).encode("utf-8")).hexdigest()


def native_recovery_group_sha256(
        refs: Sequence[str], oracle_text_sha256: str) -> str:
    """Bind one native recovery group to its ordered refs and exact oracle."""
    ordered_refs = tuple(str(ref) for ref in refs)
    if (len(ordered_refs) < 2 or len(set(ordered_refs)) != len(ordered_refs)
            or any(not ref for ref in ordered_refs)
            or not _valid_sha256(oracle_text_sha256)):
        raise ValueError("native recovery group binding is invalid")
    return hashlib.sha256(_canonical_json({
        "kind": "source_recovery_group",
        "refs": ordered_refs,
        "oracle_text_sha256": oracle_text_sha256,
    })).hexdigest()


def single_source_oracle_group_sha256(ref: str) -> str:
    """Bind an opaque oracle group that has exactly one source owner."""
    if not isinstance(ref, str) or not ref:
        raise ValueError("single source oracle ref is invalid")
    return hashlib.sha256(_canonical_json({
        "kind": "single_source_oracle", "ref": ref,
    })).hexdigest()


def visual_container_alias_group_sha256(
        owner_ref: str, alias_refs: Sequence[str],
        oracle_text_sha256: str) -> str:
    """Bind one visual owner to its exact sorted alternate segmentations."""
    aliases = tuple(str(ref) for ref in alias_refs)
    if (not isinstance(owner_ref, str) or not owner_ref
            or not aliases or tuple(sorted(aliases)) != aliases
            or len(set(aliases)) != len(aliases)
            or owner_ref in aliases or any(not ref for ref in aliases)
            or not _valid_sha256(oracle_text_sha256)):
        raise ValueError("visual container alias group binding is invalid")
    return hashlib.sha256(_canonical_json({
        "kind": "visual_container_alias_group",
        "owner_ref": owner_ref,
        "alias_refs": aliases,
        "oracle_text_sha256": oracle_text_sha256,
    })).hexdigest()


def recovery_binding_sha256(
        *, ref: str, transform: str, source_text_sha256: str,
        oracle_text_sha256: str, source_binding: object,
) -> str:
    """Bind one opaque oracle to the exact captured recovery generation."""
    return hashlib.sha256(_canonical_json({
        "schema_version": SOURCE_FIDELITY_SCHEMA_VERSION,
        "ref": ref,
        "transform": transform,
        "source_text_sha256": source_text_sha256,
        "oracle_text_sha256": oracle_text_sha256,
        "source_binding": source_binding,
    })).hexdigest()


def _label(value: object) -> str:
    label = str(value or "").strip().casefold()
    return label.rsplit(".", 1)[-1]


def source_item_text(item: dict) -> str:
    """Return the exact captured text oracle for a serialized Docling item."""
    for field in ("text", "orig"):
        value = item.get(field)
        if isinstance(value, str) and value:
            return value
    if _label(item.get("label")) not in {"table", "document_index"}:
        return ""
    data = item.get("data")
    cells = data.get("table_cells") if isinstance(data, dict) else None
    if not isinstance(cells, list):
        return ""
    values = []
    for cell in cells:
        if not isinstance(cell, dict):
            continue
        value = cell.get("text")
        if not isinstance(value, str):
            value = cell.get("orig")
        if isinstance(value, str) and value:
            values.append(value)
    return "\n".join(values)


def list_item_oracle_text(item: dict) -> str:
    """Include Docling's separately serialized enumeration marker."""
    text = source_item_text(item)
    if _label(item.get("label")) != "list_item":
        return text
    marker = item.get("marker")
    if isinstance(marker, str) and marker.strip():
        return f"{marker.strip()} {text}".strip()
    return text


def default_transform(label: object) -> str:
    value = _label(label)
    if value == "list_item":
        return "list"
    if value == "table":
        return "table"
    if value in {"picture", "figure"}:
        return "figure"
    return "plain"


def _finite_number(value: object) -> float | None:
    if (not isinstance(value, (int, float)) or isinstance(value, bool)
            or not math.isfinite(float(value))):
        return None
    return round(float(value), 3)


def provenance_spans(item: dict) -> tuple[list[dict], list[str]]:
    """Serialize exact provenance positions and report fail-closed issues."""
    result: list[dict] = []
    issues: list[str] = []
    values = item.get("prov")
    if not isinstance(values, list) or not values:
        return [], ["missing_provenance"]
    for index, provenance in enumerate(values):
        prefix = f"prov[{index}]"
        if not isinstance(provenance, dict):
            issues.append(f"{prefix}:invalid")
            continue
        page = provenance.get("page_no")
        bbox = provenance.get("bbox")
        charspan = provenance.get("charspan")
        if (not isinstance(page, int) or isinstance(page, bool) or page < 1):
            issues.append(f"{prefix}:page")
            continue
        coordinates: list[float] = []
        if isinstance(bbox, dict):
            for name in ("l", "t", "r", "b"):
                coordinate = _finite_number(bbox.get(name))
                if coordinate is None:
                    coordinates = []
                    break
                coordinates.append(coordinate)
        if len(coordinates) != 4:
            issues.append(f"{prefix}:bbox")
            continue
        left, top, right, bottom = coordinates
        if left > right or top == bottom:
            issues.append(f"{prefix}:bbox_order")
            continue
        origin = str(bbox.get("coord_origin") or "").upper()
        if origin not in {"BOTTOMLEFT", "TOPLEFT"}:
            issues.append(f"{prefix}:origin")
            continue
        if (not isinstance(charspan, (list, tuple)) or len(charspan) != 2
                or any(not isinstance(value, int) or isinstance(value, bool)
                       for value in charspan)
                or charspan[0] < 0 or charspan[1] < charspan[0]):
            issues.append(f"{prefix}:charspan")
            continue
        result.append({
            "provenance_index": index,
            "page": page,
            "bbox": coordinates,
            "origin": origin,
            "charspan": list(charspan),
        })
    return result, issues


def source_descriptor(item: dict) -> tuple[dict, list[str]]:
    text = source_item_text(item)
    tokens = lexical_tokens(text)
    spans, issues = provenance_spans(item)
    return {
        "source_text_sha256": text_sha256(text),
        "source_lexical_sha256": lexical_sha256(tokens),
        "source_lexical_count": len(tokens),
        "spans": spans,
    }, issues


def lineage_scope_indexes(entry: object) -> tuple[int, ...] | None:
    """Return one canonical provenance subset or reject the scope proof.

    ``spans`` always describes the complete immutable source item. ``scope``
    identifies the exact provenance atoms rendered by this record, allowing a
    page-local slice to retain full source identity without claiming pages that
    occur only in another slice.
    """
    if not isinstance(entry, dict):
        return None
    spans = entry.get("spans")
    scope = entry.get("scope")
    if (not isinstance(spans, list) or not spans
            or not isinstance(scope, dict)
            or set(scope) != LINEAGE_SCOPE_FIELDS):
        return None
    indexes = scope.get("provenance_indexes")
    if (not isinstance(indexes, list) or not indexes
            or any(not isinstance(index, int) or isinstance(index, bool)
                   for index in indexes)
            or indexes != sorted(set(indexes))):
        return None
    canonical_indexes = {
        span.get("provenance_index")
        for span in spans if isinstance(span, dict)
    }
    if (len(canonical_indexes) != len(spans)
            or any(not isinstance(index, int) or isinstance(index, bool)
                   or index < 0 for index in canonical_indexes)
            or not set(indexes) <= canonical_indexes):
        return None
    return tuple(indexes)


def lineage_scope_pages(entry: object) -> frozenset[int] | None:
    """Resolve the page set attested by a valid record-local scope proof."""
    indexes = lineage_scope_indexes(entry)
    if indexes is None or not isinstance(entry, dict):
        return None
    selected = set(indexes)
    pages = {
        span.get("page")
        for span in entry["spans"]
        if span.get("provenance_index") in selected
    }
    if (not pages
            or any(not isinstance(page, int) or isinstance(page, bool)
                   or page < 1 for page in pages)):
        return None
    return frozenset(pages)


def record_attestation(record: dict) -> dict:
    text = str(record.get("text") or "")
    tokens = lexical_tokens(text)
    metadata = record.get("metadata")
    items = metadata.get("source_items") if isinstance(metadata, dict) else []
    items = items if isinstance(items, list) else []
    opaque = any(
        isinstance(item, dict) and item.get("transform") in OPAQUE_TRANSFORMS
        for item in items)
    source_refs = [
        item["ref"] for item in items
        if isinstance(item, dict) and isinstance(item.get("ref"), str)
    ]
    output_lexical_sha256 = lexical_sha256(tokens)
    return {
        "schema_version": SOURCE_FIDELITY_SCHEMA_VERSION,
        "output_text_sha256": text_sha256(text),
        "output_lexical_sha256": output_lexical_sha256,
        "output_lexical_count": len(tokens),
        "opaque_output_sha256": output_lexical_sha256 if opaque else None,
        "opaque_source_refs": source_refs if opaque else [],
    }


def attach_record_attestations(records: Iterable[dict]) -> None:
    """Attach final text digests after all split/merge/dedup operations."""
    for record in records:
        metadata = record.setdefault("metadata", {})
        metadata["source_fidelity"] = record_attestation(record)


def output_attestation_root_sha256(records: Sequence[dict]) -> str:
    evidence = [
        {"record_index": index, "attestation": record_attestation(record)}
        for index, record in enumerate(records)
        if not isinstance(record.get("metadata"), dict)
        or record["metadata"].get("retrieval_role") != "table_child"
    ]
    return hashlib.sha256(_canonical_json(evidence)).hexdigest()


def output_lexical_count(records: Sequence[dict]) -> int:
    return sum(
        len(lexical_tokens(str(record.get("text") or "")))
        for record in records
        if not isinstance(record.get("metadata"), dict)
        or record["metadata"].get("retrieval_role") != "table_child"
    )


def _valid_sha256(value: object) -> bool:
    return isinstance(value, str) and _SHA256_RE.fullmatch(value) is not None


def source_oracle_registry_path(chunks_path: Path) -> Path:
    """Return the exact adjacent opaque-oracle registry path."""
    chunks_path = Path(chunks_path)
    return chunks_path.with_name(
        f"{chunks_path.stem}.source-oracles.json")


def _oracle_descriptor(ref: str, value: object) -> dict:
    if not isinstance(ref, str) or not ref:
        raise ValueError("source oracle ref must be nonempty")
    if not isinstance(value, dict):
        raise ValueError(f"source oracle descriptor is invalid: {ref}")
    descriptor = {"ref": ref, **value}
    if set(descriptor) != ORACLE_DESCRIPTOR_FIELDS:
        raise ValueError(f"source oracle descriptor fields are invalid: {ref}")
    transform = descriptor.get("transform")
    count = descriptor.get("oracle_lexical_count")
    tokens = descriptor.get("oracle_lexical_tokens")
    group_members = descriptor.get("oracle_group_members")
    if (transform not in OPAQUE_TRANSFORMS
            or not _valid_sha256(descriptor.get("source_text_sha256"))
            or not _valid_sha256(descriptor.get("oracle_text_sha256"))
            or not _valid_sha256(descriptor.get("oracle_lexical_sha256"))
            or not isinstance(count, int) or isinstance(count, bool)
            or count < 0
            or not isinstance(tokens, list)
            or any(not isinstance(token, str) or not token
                   for token in tokens)
            or len(tokens) != count
            or lexical_sha256(tokens)
            != descriptor.get("oracle_lexical_sha256")
            or not _valid_sha256(descriptor.get("oracle_group_sha256"))
            or not isinstance(group_members, list)
            or not group_members
            or any(not isinstance(member, str) or not member
                   for member in group_members)
            or len(set(group_members)) != len(group_members)
            or ref not in group_members
            or not _valid_sha256(descriptor.get("recovery_sha256"))):
        raise ValueError(f"source oracle descriptor values are invalid: {ref}")
    return descriptor


def _canonical_oracle_descriptors(
        oracles: Mapping[str, object],
) -> list[dict]:
    if not isinstance(oracles, Mapping):
        raise ValueError("source oracle map must be an object")
    return [
        _oracle_descriptor(ref, oracles[ref])
        for ref in sorted(oracles)
    ]


def _validate_oracle_group_bindings(descriptors: Sequence[dict]) -> None:
    """Validate complete opaque group membership, shape, and digest."""
    grouped: defaultdict[str, list[dict]] = defaultdict(list)
    for descriptor in descriptors:
        grouped[descriptor["oracle_group_sha256"]].append(descriptor)
    for group_sha256, members in grouped.items():
        oracle_signatures = {
            (
                descriptor["oracle_text_sha256"],
                descriptor["oracle_lexical_sha256"],
                descriptor["oracle_lexical_count"],
                tuple(descriptor["oracle_lexical_tokens"]),
            )
            for descriptor in members
        }
        if len(oracle_signatures) != 1:
            raise ValueError(
                "source oracle registry group has inconsistent oracles")
        declared_members = {
            tuple(descriptor["oracle_group_members"])
            for descriptor in members
        }
        member_refs = {descriptor["ref"] for descriptor in members}
        if (len(declared_members) != 1
                or set(next(iter(declared_members))) != member_refs):
            raise ValueError(
                "source oracle registry group membership is inconsistent")
        ordered_members = next(iter(declared_members))
        oracle_text_sha256 = members[0]["oracle_text_sha256"]
        transforms = [descriptor["transform"] for descriptor in members]
        if len(members) == 1:
            if transforms == ["container_alias"]:
                raise ValueError(
                    "source oracle registry container alias has no visual "
                    "owner")
            expected_group_sha256 = single_source_oracle_group_sha256(
                members[0]["ref"])
            if ordered_members != (members[0]["ref"],):
                raise ValueError(
                    "single source oracle group membership is invalid")
        elif set(transforms) == {"native_repair"}:
            expected_group_sha256 = native_recovery_group_sha256(
                ordered_members, oracle_text_sha256)
        else:
            owners = [
                descriptor for descriptor in members
                if descriptor["transform"] in {"table", "figure"}
            ]
            aliases = sorted(
                descriptor["ref"] for descriptor in members
                if descriptor["transform"] == "container_alias")
            if (len(owners) != 1
                    or len(aliases) != len(members) - 1
                    or ordered_members
                    != (owners[0]["ref"], *aliases)):
                raise ValueError(
                    "source oracle registry shared group shape is invalid")
            expected_group_sha256 = visual_container_alias_group_sha256(
                owners[0]["ref"], aliases, oracle_text_sha256)
        if group_sha256 != expected_group_sha256:
            raise ValueError(
                "source oracle registry group binding is invalid")


def source_oracle_root_sha256(oracles: Mapping[str, object]) -> str:
    """Hash one independently produced, canonical opaque-oracle map."""
    return hashlib.sha256(_canonical_json(
        _canonical_oracle_descriptors(oracles))).hexdigest()


def build_source_oracle_registry(
        *, oracles: Mapping[str, object], input_bindings: object,
) -> dict:
    """Build the strict sidecar that authorizes opaque transform output.

    ``oracles`` must come from the source/PDF recovery pass, before records are
    formed.  The resulting sidecar is committed independently of the chunks,
    so record metadata cannot appoint its own recovery oracle.
    """
    descriptors = _canonical_oracle_descriptors(oracles)
    _validate_oracle_group_bindings(descriptors)
    canonical_inputs = json.loads(_canonical_json(input_bindings))
    recovery_source_binding = (
        canonical_inputs.get("table_recovery")
        if isinstance(canonical_inputs, dict) else None)
    for descriptor in descriptors:
        expected_recovery = recovery_binding_sha256(
            ref=descriptor["ref"],
            transform=descriptor["transform"],
            source_text_sha256=descriptor["source_text_sha256"],
            oracle_text_sha256=descriptor["oracle_text_sha256"],
            source_binding=recovery_source_binding,
        )
        if descriptor["recovery_sha256"] != expected_recovery:
            raise ValueError(
                "source oracle registry recovery binding is invalid")
    core = {
        "schema_version": SOURCE_ORACLE_REGISTRY_SCHEMA_VERSION,
        "kind": "source_fidelity_oracle_registry",
        "source_fidelity_schema_version": SOURCE_FIDELITY_SCHEMA_VERSION,
        "inputs": canonical_inputs,
        "oracles": descriptors,
        "oracle_root_sha256": hashlib.sha256(
            _canonical_json(descriptors)).hexdigest(),
    }
    return {
        **core,
        "evidence_sha256": hashlib.sha256(_canonical_json(core)).hexdigest(),
    }


def validate_source_oracle_registry(
        value: object, *, expected_input_bindings: object | None = None,
) -> dict[str, dict]:
    """Validate a current registry and return its trusted ref map."""
    if not isinstance(value, dict) or set(value) != ORACLE_REGISTRY_FIELDS:
        raise ValueError("source oracle registry has an invalid field set")
    if (value.get("schema_version")
            != SOURCE_ORACLE_REGISTRY_SCHEMA_VERSION
            or value.get("kind") != "source_fidelity_oracle_registry"
            or value.get("source_fidelity_schema_version")
            != SOURCE_FIDELITY_SCHEMA_VERSION
            or (expected_input_bindings is not None
                and value.get("inputs") != expected_input_bindings)):
        raise ValueError("source oracle registry is stale or detached")
    raw_descriptors = value.get("oracles")
    if not isinstance(raw_descriptors, list):
        raise ValueError("source oracle registry has no oracle list")
    result: dict[str, dict] = {}
    canonical = []
    registry_inputs = value.get("inputs")
    recovery_binding = (
        registry_inputs.get("table_recovery")
        if isinstance(registry_inputs, dict) else None)
    for raw in raw_descriptors:
        if not isinstance(raw, dict):
            raise ValueError("source oracle registry entry is invalid")
        ref = raw.get("ref")
        descriptor = _oracle_descriptor(
            ref, {key: entry for key, entry in raw.items() if key != "ref"})
        if ref in result:
            raise ValueError("source oracle registry contains duplicate refs")
        expected_recovery = recovery_binding_sha256(
            ref=ref,
            transform=descriptor["transform"],
            source_text_sha256=descriptor["source_text_sha256"],
            oracle_text_sha256=descriptor["oracle_text_sha256"],
            source_binding=recovery_binding,
        )
        if descriptor["recovery_sha256"] != expected_recovery:
            raise ValueError(
                "source oracle registry recovery binding is invalid")
        result[ref] = {
            key: descriptor[key] for key in descriptor if key != "ref"
        }
        canonical.append(descriptor)
    if canonical != sorted(canonical, key=lambda item: item["ref"]):
        raise ValueError("source oracle registry entries are not canonical")
    _validate_oracle_group_bindings(canonical)
    expected_root = hashlib.sha256(_canonical_json(canonical)).hexdigest()
    if value.get("oracle_root_sha256") != expected_root:
        raise ValueError("source oracle registry root does not match entries")
    core = {key: value[key] for key in value if key != "evidence_sha256"}
    if (not _valid_sha256(value.get("evidence_sha256"))
            or value["evidence_sha256"]
            != hashlib.sha256(_canonical_json(core)).hexdigest()):
        raise ValueError("source oracle registry evidence is invalid")
    return result


def _source_catalog(document: dict) -> dict[str, dict]:
    catalog: dict[str, dict] = {}
    if not isinstance(document, dict):
        return catalog
    for collection in SOURCE_COLLECTIONS:
        for item in document.get(collection) or []:
            if not isinstance(item, dict):
                continue
            ref = item.get("self_ref")
            if isinstance(ref, str) and ref and ref not in catalog:
                catalog[ref] = item
    return catalog


def _record_attestation_valid(record: dict) -> bool:
    metadata = record.get("metadata")
    value = metadata.get("source_fidelity") if isinstance(metadata, dict) \
        else None
    return (isinstance(value, dict)
            and set(value) == RECORD_ATTESTATION_FIELDS
            and value == record_attestation(record))


def _lineage_entry_valid(
        entry: object, item: dict | None, recovery_binding: object,
        source_oracles: Mapping[str, dict],
) -> bool:
    if (not isinstance(entry, dict) or set(entry) != LINEAGE_ITEM_FIELDS
            or item is None):
        return False
    descriptor, geometry_issues = source_descriptor(item)
    transform = entry.get("transform")
    label = _label(item.get("label"))
    if (geometry_issues or transform not in ALLOWED_TRANSFORMS
            or lineage_scope_indexes(entry) is None
            or entry.get("spans") != descriptor["spans"]
            or entry.get("source_text_sha256")
            != descriptor["source_text_sha256"]
            or entry.get("source_lexical_sha256")
            != descriptor["source_lexical_sha256"]
            or entry.get("source_lexical_count")
            != descriptor["source_lexical_count"]):
        return False
    if transform == "list" and label != "list_item":
        return False
    if transform == "table" and label not in {"table", "document_index"}:
        return False
    if transform == "figure" and label not in {"picture", "figure"}:
        return False
    if transform == "container_alias" and label in {
            "table", "picture", "figure"}:
        return False
    if transform == "plain" and default_transform(label) != "plain":
        return False
    if (not _valid_sha256(entry.get("oracle_text_sha256"))
            or not _valid_sha256(entry.get("oracle_lexical_sha256"))
            or not isinstance(entry.get("oracle_lexical_count"), int)
            or isinstance(entry.get("oracle_lexical_count"), bool)
            or entry["oracle_lexical_count"] < 0):
        return False
    recovery = entry.get("recovery_sha256")
    if transform in {"plain", "list"}:
        oracle_text = (
            list_item_oracle_text(item) if transform == "list"
            else source_item_text(item))
        oracle_tokens = lexical_tokens(oracle_text)
        return (
            recovery is None
            and entry["oracle_text_sha256"]
            == text_sha256(oracle_text)
            and entry["oracle_lexical_sha256"]
            == lexical_sha256(oracle_tokens)
            and entry["oracle_lexical_count"]
            == len(oracle_tokens)
        )
    trusted = source_oracles.get(entry["ref"])
    expected_oracle = {
        "transform": transform,
        "source_text_sha256": descriptor["source_text_sha256"],
        "oracle_text_sha256": entry["oracle_text_sha256"],
        "oracle_lexical_sha256": entry["oracle_lexical_sha256"],
        "oracle_lexical_count": entry["oracle_lexical_count"],
        "recovery_sha256": recovery,
    }
    if (not isinstance(trusted, dict)
            or any(trusted.get(field) != expected
                   for field, expected in expected_oracle.items())
            or not isinstance(trusted.get("oracle_lexical_tokens"), list)
            or not _valid_sha256(trusted.get("oracle_group_sha256"))):
        return False
    # A nonempty captured source may be transformed, but it may never be
    # attested as a zero-token recovery.  That would turn an omission into
    # complete source coverage.
    if (descriptor["source_lexical_count"] > 0
            and entry["oracle_lexical_count"] == 0):
        return False
    return recovery == recovery_binding_sha256(
        ref=entry["ref"], transform=transform,
        source_text_sha256=descriptor["source_text_sha256"],
        oracle_text_sha256=entry["oracle_text_sha256"],
        source_binding=recovery_binding)


def _find_alignment(
        needles: Sequence[str], haystack: Sequence[tuple[str, int, str]],
        already_covered: set[tuple[str, int]],
        minimum_offsets: dict[str, int] | None = None,
) -> list[tuple[str, int]] | None:
    if not needles:
        return []
    tokens = [value[2] for value in haystack]
    candidates = []
    minimum_offsets = minimum_offsets or {}
    width = len(needles)
    for start in range(0, len(tokens) - width + 1):
        if tokens[start:start + width] == list(needles):
            positions = [
                (ref, offset) for ref, offset, _ in haystack[start:start + width]
            ]
            if any(offset <= minimum_offsets.get(ref, -1)
                   for ref, offset in positions if offset >= 0):
                continue
            novelty = sum(position not in already_covered
                          for position in positions)
            candidates.append((novelty, -start, positions))
    if candidates:
        novelty, _, positions = max(
            candidates, key=lambda value: (value[0], value[1]))
        return positions if novelty == len(positions) else None

    # Permit source-preserving omissions between cited items, but never an
    # insertion or permutation of lexical tokens.
    positions = []
    cursor = 0
    local_minimums = dict(minimum_offsets)
    for needle in needles:
        while cursor < len(haystack) and (
                haystack[cursor][2] != needle
                or (haystack[cursor][0], haystack[cursor][1])
                in already_covered
                or (haystack[cursor][1] >= 0
                    and haystack[cursor][1] <= local_minimums.get(
                        haystack[cursor][0], -1))):
            cursor += 1
        if cursor >= len(haystack):
            return None
        positions.append((haystack[cursor][0], haystack[cursor][1]))
        if haystack[cursor][1] >= 0:
            local_minimums[haystack[cursor][0]] = haystack[cursor][1]
        cursor += 1
    return positions


def _normalized_box(span: dict) -> tuple[float, float, float, float]:
    left, top, right, bottom = (float(value) for value in span["bbox"])
    x0, x1 = sorted((left, right))
    if span["origin"] == "BOTTOMLEFT":
        y0, y1 = -max(top, bottom), -min(top, bottom)
    else:
        y0, y1 = min(top, bottom), max(top, bottom)
    return x0, y0, x1, y1


def _horizontal_overlap(first: tuple[float, float, float, float],
                        second: tuple[float, float, float, float]) -> bool:
    overlap = min(first[2], second[2]) - max(first[0], second[0])
    minimum_width = min(first[2] - first[0], second[2] - second[0])
    return minimum_width > 0 and overlap >= max(2.0, minimum_width * 0.20)


def _source_token_pages(item: dict) -> dict[int, frozenset[int]]:
    """Map source lexical offsets to their captured provenance pages.

    Docling's provenance charspans are the only source-backed way to
    distinguish separate page occurrences of one multi-page item.  Prefer the
    next exact token run for each span, while permitting overlapping spans to
    bind the same run (some serializers repeat a full-item charspan).  List
    item charspans cover ``marker + space + text`` even though the serialized
    item ``text`` field excludes the marker, so align against that exact
    source form and translate only body-token offsets back to ``text``.
    """
    text = source_item_text(item)
    tokens = lexical_tokens(text)
    spans, issues = provenance_spans(item)
    if issues or not tokens:
        return {}
    provenance_text = list_item_oracle_text(item)
    provenance_tokens = lexical_tokens(provenance_text)
    marker_width = max(0, len(provenance_tokens) - len(tokens))
    if tuple(provenance_tokens[marker_width:]) != tuple(tokens):
        return {}
    pages_by_offset: defaultdict[int, set[int]] = defaultdict(set)
    cursor = 0
    maximum_overrun = max(2, len(spans))

    def lexical_character(character: str) -> bool:
        return bool(character) and (
            character == "\N{SOFT HYPHEN}"
            or character in "'\N{LEFT SINGLE QUOTATION MARK}"
            "\N{RIGHT SINGLE QUOTATION MARK}"
            or (character != "_" and (character.isalpha()
                                      or character.isdigit()))
        )

    def token_complete_bounds(start: int, end: int) -> tuple[int, int]:
        """Expand a provenance cut only when it bisects one raw token."""
        while (start > 0 and start < len(provenance_text)
               and lexical_character(provenance_text[start - 1])
               and lexical_character(provenance_text[start])):
            start -= 1
        while (0 < end < len(provenance_text)
               and lexical_character(provenance_text[end - 1])
               and lexical_character(provenance_text[end])):
            end += 1
        return start, end

    for span in spans:
        start, end = span["charspan"]
        if (start > len(provenance_text)
                or end > len(provenance_text) + maximum_overrun):
            return {}
        end = min(end, len(provenance_text))
        start, end = token_complete_bounds(start, end)
        span_tokens = lexical_tokens(provenance_text[start:end])
        if not span_tokens:
            continue
        width = len(span_tokens)
        candidates = [
            index for index in range(0, len(provenance_tokens) - width + 1)
            if tuple(provenance_tokens[index:index + width]) == span_tokens
        ]
        if not candidates:
            continue
        token_start = next(
            (index for index in candidates if index >= cursor),
            candidates[0],
        )
        for provenance_offset in range(token_start, token_start + width):
            body_offset = provenance_offset - marker_width
            if 0 <= body_offset < len(tokens):
                pages_by_offset[body_offset].add(span["page"])
        cursor = max(cursor, token_start + width)
    return {
        offset: frozenset(pages)
        for offset, pages in pages_by_offset.items()
    }


def _source_reference(value: object) -> str | None:
    if isinstance(value, str) and value:
        return value
    if not isinstance(value, dict):
        return None
    for field in ("$ref", "cref", "ref"):
        ref = value.get(field)
        if isinstance(ref, str) and ref:
            return ref
    return None


def _source_relationships(
        catalog: dict[str, dict],
) -> dict[frozenset[str], set[str]]:
    """Return exact, source-derived direct relationships between items."""
    relationships: defaultdict[frozenset[str], set[str]] = defaultdict(set)
    for parent_ref, item in catalog.items():
        direct_parent = _source_reference(item.get("parent"))
        if direct_parent in catalog and direct_parent != parent_ref:
            relationships[frozenset((parent_ref, direct_parent))].add(
                "parent")
        for field in ("captions", "footnotes", "children"):
            values = item.get(field)
            if not isinstance(values, list):
                continue
            for value in values:
                child_ref = _source_reference(value)
                if child_ref in catalog and child_ref != parent_ref:
                    relationships[frozenset((parent_ref, child_ref))].add(
                        field)
    return dict(relationships)


def _item_primary_box(
        item: dict,
) -> tuple[int, float, float, float, float] | None:
    """Return one page-local box for strict visual-container validation."""
    spans, issues = provenance_spans(item)
    if issues or not spans:
        return None
    span = spans[0]
    left, top, right, bottom = (float(value) for value in span["bbox"])
    return (
        span["page"], min(left, right), min(top, bottom),
        max(left, right), max(top, bottom),
    )


def _container_picture_tables(catalog: dict[str, dict]) -> dict[str, str]:
    """Infer the same strict picture-over-table wrappers used at emission."""
    tables = [
        (ref, item) for ref, item in catalog.items()
        if _label(item.get("label")) == "table"]
    result = {}
    for picture_ref, picture in catalog.items():
        if (_label(picture.get("label")) not in {"picture", "figure"}
                or not isinstance(picture.get("children"), list)
                or not picture["children"]):
            continue
        picture_box = _item_primary_box(picture)
        if picture_box is None:
            continue
        page, left, top, right, bottom = picture_box
        picture_area = max(1e-9, (right - left) * (bottom - top))
        candidates = []
        for table_ref, table in tables:
            table_box = _item_primary_box(table)
            if table_box is None or table_box[0] != page:
                continue
            _, table_left, table_top, table_right, table_bottom = table_box
            width = max(
                0.0, min(right, table_right) - max(left, table_left))
            height = max(
                0.0, min(bottom, table_bottom) - max(top, table_top))
            overlap = width * height
            table_area = max(
                1e-9,
                (table_right - table_left) * (table_bottom - table_top),
            )
            table_coverage = overlap / table_area
            picture_coverage = overlap / picture_area
            if table_coverage >= 0.90 and picture_coverage >= 0.45:
                candidates.append(
                    (picture_coverage, table_coverage, table_ref))
        if candidates:
            result[picture_ref] = max(candidates)[2]
    return result


def _alias_source_tokens(item: dict) -> tuple[str, ...]:
    text = (
        list_item_oracle_text(item)
        if _label(item.get("label")) == "list_item"
        else source_item_text(item)
    )
    return lexical_tokens(text)


def _joint_nonoverlapping_alias_offsets(
        owner_tokens: Sequence[str], alias_tokens: Mapping[str, Sequence[str]],
) -> dict[str, dict[int, int]] | None:
    """Map every complete child oracle to distinct owner-token offsets."""
    candidates: list[
        tuple[str, tuple[str, ...], list[tuple[int, ...]]]
    ] = []
    for ref, raw_tokens in alias_tokens.items():
        tokens = tuple(raw_tokens)
        if not tokens:
            return None
        width = len(tokens)
        starts = [
            tuple(range(start, start + width))
            for start in range(0, len(owner_tokens) - width + 1)
            if _container_alias_tokens_match(
                owner_tokens[start:start + width], tokens)
        ]
        if not starts:
            return None
        candidates.append((ref, tokens, starts))
    candidates.sort(key=lambda value: (len(value[2]), value[0]))

    assignment: dict[str, dict[int, int]] = {}

    def search(index: int, used: frozenset[int]) -> bool:
        if index == len(candidates):
            return True
        ref, tokens, intervals = candidates[index]
        for interval in intervals:
            if not used.isdisjoint(interval):
                continue
            assignment[ref] = {
                owner_offset: child_offset
                for child_offset, owner_offset in enumerate(interval)
            }
            if search(index + 1, used | frozenset(interval)):
                return True
            assignment.pop(ref, None)
        return False

    return assignment if search(0, frozenset()) else None


def _container_alias_tokens_match(
        owner_tokens: Sequence[str], child_tokens: Sequence[str]) -> bool:
    """Match an exact child span, including a fused possessive boundary.

    PDF segmentation can put a diagram's symbol (for example ``π``) in one
    child item and its possessive suffix in the neighboring item, while the
    figure recovery correctly tokenizes the visible word as ``π's``.  Admit
    only that one-token apostrophe-s fusion; ordinary lexical prefixes remain
    invalid aliases.
    """
    owner = tuple(owner_tokens)
    child = tuple(child_tokens)
    if owner == child:
        return True
    return bool(
        len(owner) == len(child) == 1
        and owner[0] == f"{child[0]}'s")


def _validated_container_aliases(
        catalog: dict[str, dict], source_oracles: Mapping[str, dict],
) -> tuple[
    dict[str, str], dict[str, dict[int, int]], set[str],
    dict[str, frozenset[str]],
]:
    """Validate trusted visual aliases independently against captured source."""
    grouped: defaultdict[str, list[tuple[str, dict]]] = defaultdict(list)
    invalid: set[str] = set()
    for ref, descriptor in source_oracles.items():
        if not isinstance(descriptor, dict):
            continue
        group = descriptor.get("oracle_group_sha256")
        if _valid_sha256(group):
            grouped[group].append((ref, descriptor))

    relationships = _source_relationships(catalog)
    picture_tables = _container_picture_tables(catalog)
    owners: dict[str, str] = {}
    offset_maps: dict[str, dict[int, int]] = {}
    valid_groups: dict[str, frozenset[str]] = {}
    for group_sha256, members in grouped.items():
        aliases = [
            (ref, descriptor) for ref, descriptor in members
            if descriptor.get("transform") == "container_alias"]
        if not aliases:
            continue
        group_refs = {ref for ref, _ in members}
        owner_candidates = [
            (ref, descriptor) for ref, descriptor in members
            if descriptor.get("transform") in {"table", "figure"}]
        if len(owner_candidates) != 1:
            invalid.update(group_refs)
            continue
        owner_ref, owner = owner_candidates[0]
        owner_item = catalog.get(owner_ref)
        owner_tokens = owner.get("oracle_lexical_tokens")
        alias_refs = tuple(sorted(ref for ref, _ in aliases))
        declared_members = {
            tuple(descriptor.get("oracle_group_members") or ())
            for _, descriptor in members
        }
        expected_members = (owner_ref, *alias_refs)
        try:
            expected_group_sha256 = visual_container_alias_group_sha256(
                owner_ref, alias_refs, owner.get("oracle_text_sha256"))
        except ValueError:
            expected_group_sha256 = None
        if (len(aliases) != len(members) - 1
                or declared_members != {expected_members}
                or owner_item is None or not isinstance(owner_tokens, list)
                or not owner_tokens
                or group_sha256 != expected_group_sha256):
            invalid.update(group_refs)
            continue

        alias_token_map: dict[str, tuple[str, ...]] = {}
        group_valid = True
        for alias_ref, alias in aliases:
            alias_item = catalog.get(alias_ref)
            if (alias_item is None
                    or _label(alias_item.get("label")) in {
                        "table", "picture", "figure"}
                    or alias.get("oracle_lexical_tokens") != owner_tokens):
                group_valid = False
                break
            directly_related = bool(relationships.get(
                frozenset((owner_ref, alias_ref)), set()))
            indirectly_related = any(
                table_ref == owner_ref
                and bool(relationships.get(
                    frozenset((picture_ref, alias_ref)), set()))
                for picture_ref, table_ref in picture_tables.items()
            )
            if not directly_related and not indirectly_related:
                group_valid = False
                break
            alias_token_map[alias_ref] = _alias_source_tokens(alias_item)
        mappings = (
            _joint_nonoverlapping_alias_offsets(owner_tokens, alias_token_map)
            if group_valid else None)
        if mappings is None:
            invalid.update(group_refs)
            continue
        for alias_ref, _ in aliases:
            owners[alias_ref] = owner_ref
            offset_maps[alias_ref] = mappings[alias_ref]
        valid_groups[group_sha256] = frozenset(group_refs)
    return owners, offset_maps, invalid, valid_groups


def _validated_native_recovery_groups(
        catalog: dict[str, dict], source_oracles: Mapping[str, dict],
) -> tuple[dict[str, frozenset[str]], dict[str, str]]:
    """Validate complete native groups and their narrower atomic geometry."""
    grouped: defaultdict[str, list[tuple[str, dict]]] = defaultdict(list)
    for ref, descriptor in source_oracles.items():
        if (isinstance(descriptor, dict)
                and descriptor.get("transform") == "native_repair"
                and _valid_sha256(descriptor.get("oracle_group_sha256"))):
            grouped[descriptor["oracle_group_sha256"]].append(
                (ref, descriptor))

    valid_groups: dict[str, frozenset[str]] = {}
    indivisible_by_ref: dict[str, str] = {}
    for group_sha256, members in grouped.items():
        if len(members) < 2:
            continue
        oracle_signatures = {
            (
                descriptor.get("oracle_text_sha256"),
                descriptor.get("oracle_lexical_sha256"),
                descriptor.get("oracle_lexical_count"),
                tuple(descriptor.get("oracle_lexical_tokens") or ()),
            )
            for _, descriptor in members
        }
        declared_members = {
            tuple(descriptor.get("oracle_group_members") or ())
            for _, descriptor in members
        }
        if len(oracle_signatures) != 1 or len(declared_members) != 1:
            continue
        oracle_text_sha256 = next(iter(oracle_signatures))[0]
        positioned_members = []
        group_pages = set()
        for ref, _ in members:
            item = catalog.get(ref)
            if item is None:
                positioned_members = []
                break
            source, issues = source_descriptor(item)
            spans = source.get("spans", [])
            if issues or not spans:
                positioned_members = []
                break
            span_pages = {span["page"] for span in spans}
            boxes = [_normalized_box(span) for span in spans]
            if len(span_pages) != 1:
                positioned_members = []
                break
            # Docling may serialize one detached phrase as several word boxes
            # on the same visual line (for example ``of`` and ``the``).  That
            # still has one deterministic member position: the union of the
            # vertically aligned boxes.  Multi-page or vertically disjoint
            # spans remain fail-closed.
            first_box = boxes[0]
            if any(
                    min(first_box[3], box[3])
                    - max(first_box[1], box[1])
                    < 0.50 * min(
                        first_box[3] - first_box[1], box[3] - box[1])
                    for box in boxes[1:]):
                positioned_members = []
                break
            box = (
                min(value[0] for value in boxes),
                min(value[1] for value in boxes),
                max(value[2] for value in boxes),
                max(value[3] for value in boxes),
            )
            page = next(iter(span_pages))
            group_pages.add(page)
            positioned_members.append((
                (page, box[1], box[0], box[3], box[2], ref), ref))
        if not positioned_members:
            continue
        ordered_refs = tuple(ref for _, ref in sorted(positioned_members))
        if (next(iter(declared_members)) != ordered_refs
                or {ref for ref, _ in members} != set(ordered_refs)):
            continue
        try:
            expected_group_sha256 = native_recovery_group_sha256(
                ordered_refs, oracle_text_sha256)
        except ValueError:
            continue
        if group_sha256 != expected_group_sha256:
            continue
        valid_groups[group_sha256] = frozenset(ordered_refs)
        if len(group_pages) == 1:
            for ref in ordered_refs:
                indivisible_by_ref[ref] = group_sha256
    return valid_groups, indivisible_by_ref


def _valid_exemption(value: object, catalog: dict[str, dict]) -> bool:
    if not isinstance(value, dict) or set(value) != ORDER_EXEMPTION_FIELDS:
        return False
    before = value.get("before_ref")
    after = value.get("after_ref")
    page = value.get("page")
    reason = value.get("reason")
    if (not isinstance(before, str) or before not in catalog
            or not isinstance(after, str) or after not in catalog
            or before == after
            or not isinstance(page, int) or isinstance(page, bool) or page < 1
            or reason not in ALLOWED_ORDER_EXEMPTIONS):
        return False
    before_spans, before_issues = provenance_spans(catalog[before])
    after_spans, after_issues = provenance_spans(catalog[after])
    if (before_issues or after_issues
            or not any(span["page"] == page for span in before_spans)
            or not any(span["page"] == page for span in after_spans)):
        return False
    relationship_kinds = _source_relationships(catalog).get(
        frozenset((before, after)), set())
    if not relationship_kinds:
        return False
    labels = {_label(catalog[before].get("label")),
              _label(catalog[after].get("label"))}
    if reason == "footnote_sidecar":
        return ("footnote" in labels
                and bool(relationship_kinds & {"footnotes", "parent"}))
    if reason == "table_fragment":
        return ("table" in labels
                and bool(relationship_kinds & {
                    "captions", "footnotes", "children", "parent"}))
    if reason == "figure_sidecar":
        return (bool(labels & {"picture", "figure"})
                and bool(relationship_kinds & {
                    "captions", "children", "parent"}))
    return False


def audit_source_fidelity(
        *, records: Sequence[dict], document: dict,
        eligible_refs: Iterable[str],
        recovery_binding: object = None,
        source_oracles: Mapping[str, dict] | None = None,
) -> dict:
    """Audit lexical coverage and same-page geometric partial ordering."""
    catalog = _source_catalog(document)
    eligible = set(eligible_refs)
    source_tokens = {
        ref: lexical_tokens(source_item_text(item))
        for ref, item in catalog.items()
    }
    source_token_pages = {
        ref: _source_token_pages(item)
        for ref, item in catalog.items()
    }
    source_oracle_tokens = {}
    source_marker_tokens = {}
    source_punctuation_markers = {}
    for ref, item in catalog.items():
        tokens = source_tokens[ref]
        if _label(item.get("label")) == "list_item":
            marker_tokens = lexical_tokens(str(item.get("marker") or ""))
            source_marker_tokens[ref] = tuple(
                (index - len(marker_tokens), token)
                for index, token in enumerate(marker_tokens))
            punctuation_marker = _punctuation_list_marker(item)
            if punctuation_marker is not None:
                source_punctuation_markers[ref] = punctuation_marker
            source_oracle_tokens[ref] = tuple(
                [*source_marker_tokens[ref], *enumerate(tokens)])
        else:
            source_marker_tokens[ref] = ()
            source_oracle_tokens[ref] = tuple(enumerate(tokens))
    covered_positions: set[tuple[str, int]] = set()
    last_source_offset: dict[str, int] = {}
    opaque_covered_offsets: defaultdict[str, set[int]] = defaultdict(set)
    zero_width_opaque_refs: set[str] = set()
    output_tokens = 0
    covered_output_tokens = 0
    source_hash_mismatches: set[str] = set()
    lineage_issue_records: set[int] = set()
    output_coverage_issues: set[int] = set()
    geometry_issues: set[str] = set()
    transform_counts: Counter[str] = Counter()
    page_occurrence: dict[
        tuple[str, int],
        tuple[tuple[int, int, int], tuple[int, int, int]],
    ] = {}
    represented_entries: dict[str, dict] = {}
    represented_scope_indexes: defaultdict[str, set[int]] = defaultdict(set)
    exemptions: list[tuple[int, dict]] = []
    source_oracles = source_oracles or {}
    (container_alias_owners,
     container_alias_offsets,
     invalid_container_alias_refs,
     valid_visual_groups) = _validated_container_aliases(
         catalog, source_oracles)
    (valid_native_groups,
     indivisible_native_group_by_ref) = _validated_native_recovery_groups(
         catalog, source_oracles)
    grouped_opaque_oracles: defaultdict[str, list[str]] = defaultdict(list)
    for ref, descriptor in source_oracles.items():
        if isinstance(descriptor, dict) and _valid_sha256(
                descriptor.get("oracle_group_sha256")):
            grouped_opaque_oracles[
                descriptor["oracle_group_sha256"]].append(ref)
    valid_opaque_groups: dict[str, frozenset[str]] = {
        **valid_visual_groups,
        **valid_native_groups,
    }
    for group_sha256, refs in grouped_opaque_oracles.items():
        if len(refs) != 1:
            continue
        ref = refs[0]
        descriptor = source_oracles.get(ref, {})
        if (descriptor.get("oracle_group_members") == [ref]
                and group_sha256
                == single_source_oracle_group_sha256(ref)):
            valid_opaque_groups[group_sha256] = frozenset({ref})
    claimed_zero_width_opaque_refs: set[str] = set()
    aligned_ref_records: defaultdict[str, set[int]] = defaultdict(set)
    covered_punctuation_marker_refs: set[str] = set()

    def register_page_occurrence(
            ref: str, page: int, candidate: tuple[int, int, int]) -> None:
        """Track both edges of one ref's emitted interval on a source page."""
        key = (ref, page)
        current = page_occurrence.get(key)
        if current is None:
            page_occurrence[key] = (candidate, candidate)
            return
        page_occurrence[key] = (
            min(current[0], candidate), max(current[1], candidate))

    for ref, item in catalog.items():
        _, issues = source_descriptor(item)
        if ref in eligible:
            geometry_issues.update(f"{ref}:{issue}" for issue in issues)

    for record_index, record in enumerate(records):
        metadata = record.get("metadata")
        metadata = metadata if isinstance(metadata, dict) else {}
        if metadata.get("retrieval_role") == "table_child":
            continue
        normalized_record_text = _normalized_lexical_text(
            str(record.get("text") or ""))
        record_token_matches = tuple(
            _TOKEN_RE.finditer(normalized_record_text))
        record_tokens = tuple(
            match.group(0).casefold() for match in record_token_matches)
        output_tokens += len(record_tokens)
        if not _record_attestation_valid(record):
            output_coverage_issues.add(record_index)
        values = metadata.get("source_items")
        if not isinstance(values, list) or not values:
            lineage_issue_records.add(record_index)
            output_coverage_issues.add(record_index)
            continue
        entries: list[dict] = []
        lineage_positions: dict[str, int] = {}
        for position, entry in enumerate(values):
            ref = entry.get("ref") if isinstance(entry, dict) else None
            item = catalog.get(ref) if isinstance(ref, str) else None
            if not _lineage_entry_valid(
                    entry, item, recovery_binding, source_oracles):
                lineage_issue_records.add(record_index)
                if isinstance(ref, str):
                    source_hash_mismatches.add(ref)
                continue
            entries.append(entry)
            transform = entry["transform"]
            transform_counts[transform] += 1
            represented_entries.setdefault(ref, entry)
            represented_scope_indexes[ref].update(
                lineage_scope_indexes(entry))
            lineage_positions.setdefault(ref, position)

        scope_pages_by_ref = {
            entry["ref"]: lineage_scope_pages(entry)
            for entry in entries
        }

        raw_exemptions = metadata.get("source_order_exemptions", [])
        if not isinstance(raw_exemptions, list):
            lineage_issue_records.add(record_index)
        else:
            for exemption in raw_exemptions:
                if not _valid_exemption(exemption, catalog):
                    lineage_issue_records.add(record_index)
                else:
                    exemptions.append((record_index, dict(exemption)))

        if not entries:
            output_coverage_issues.add(record_index)
            continue
        haystack: list[tuple[str, int, str]] = []
        opaque_aliases: defaultdict[tuple[str, int], list[str]] = (
            defaultdict(list))
        opaque_entries: dict[str, dict] = {}
        seen_opaque_groups: set[str] = set()
        zero_width_entries: list[dict] = []
        opaque_valid = True
        record_zero_width_refs: set[str] = set()
        record_opaque_refs: set[str] = set()
        record_container_alias_refs: set[str] = set()
        record_opaque_groups: defaultdict[str, set[str]] = defaultdict(set)
        for entry in entries:
            ref = entry["ref"]
            if entry["transform"] not in OPAQUE_TRANSFORMS:
                haystack.extend(
                    (ref, offset, token)
                    for offset, token in source_oracle_tokens.get(ref, ()))
                continue
            trusted = source_oracles.get(ref)
            if ref in record_opaque_refs or not isinstance(trusted, dict):
                opaque_valid = False
                break
            record_opaque_refs.add(ref)
            # Native repair replaces only the item's body oracle.  A Docling
            # list marker remains independently source-derived and therefore
            # aligns through its negative source offsets; it is deliberately
            # not folded into the opaque recovery oracle.
            if entry["transform"] != "container_alias":
                haystack.extend(
                    (ref, offset, token)
                    for offset, token in source_marker_tokens.get(ref, ()))
            else:
                record_container_alias_refs.add(ref)
                if (ref in invalid_container_alias_refs
                        or ref not in container_alias_owners
                        or ref not in container_alias_offsets):
                    opaque_valid = False
                    break
            oracle_tokens = trusted.get("oracle_lexical_tokens")
            group_sha256 = trusted.get("oracle_group_sha256")
            if (not isinstance(oracle_tokens, list)
                    or not _valid_sha256(group_sha256)
                    or group_sha256 not in valid_opaque_groups
                    or ref not in valid_opaque_groups[group_sha256]):
                opaque_valid = False
                break
            record_opaque_groups[group_sha256].add(ref)
            if not oracle_tokens:
                if (ref in record_zero_width_refs
                        or ref in claimed_zero_width_opaque_refs):
                    opaque_valid = False
                    break
                record_zero_width_refs.add(ref)
                zero_width_entries.append(entry)
                continue
            group_key = f"opaque:{group_sha256}"
            if group_sha256 not in seen_opaque_groups:
                seen_opaque_groups.add(group_sha256)
                haystack.extend(
                    (group_key, offset, token)
                    for offset, token in enumerate(oracle_tokens))
            alias_offsets = (
                container_alias_offsets[ref]
                if entry["transform"] == "container_alias"
                else range(len(oracle_tokens))
            )
            for offset in alias_offsets:
                aliases = opaque_aliases[(group_key, offset)]
                if ref not in aliases:
                    aliases.append(ref)
            opaque_entries[ref] = entry

        if any(
                container_alias_owners.get(ref) not in record_opaque_refs
                for ref in record_container_alias_refs):
            opaque_valid = False
        if any(
                frozenset(refs) != valid_opaque_groups.get(group_sha256)
                for group_sha256, refs in record_opaque_groups.items()):
            # A shared oracle is one indivisible source unit. Partial member
            # claims or unrelated extra members must not appoint aliases for
            # the same output token interval.
            opaque_valid = False

        alignment = _find_alignment(
            record_tokens, haystack, covered_positions,
            last_source_offset)
        scope_alignment_valid = True
        if not opaque_valid:
            lineage_issue_records.add(record_index)
        if (not opaque_valid or alignment is None
                or not _record_attestation_valid(record)):
            output_coverage_issues.add(record_index)
        else:
            covered_output_tokens += len(record_tokens)
            covered_positions.update(alignment)
            claimed_zero_width_opaque_refs.update(record_zero_width_refs)
            zero_width_opaque_refs.update(record_zero_width_refs)
            output_refs_by_index: dict[int, frozenset[str]] = {}
            for entry in zero_width_entries:
                ref = entry["ref"]
                candidate = (
                    record_index, 0, lineage_positions.get(ref, 0))
                for span_page in scope_pages_by_ref.get(ref) or ():
                    register_page_occurrence(ref, span_page, candidate)
            for alignment_ref, source_offset in alignment:
                if source_offset >= 0:
                    last_source_offset[alignment_ref] = max(
                        last_source_offset.get(alignment_ref, -1),
                        source_offset)
            for output_index, (alignment_ref, source_offset) in enumerate(
                    alignment):
                aliases = opaque_aliases.get(
                    (alignment_ref, source_offset))
                actual_refs = aliases or [alignment_ref]
                output_refs_by_index[output_index] = frozenset(actual_refs)
                for ref in actual_refs:
                    if aliases:
                        opaque_covered_offsets[ref].add(source_offset)
                        represented = opaque_entries.get(ref)
                        if (represented or {}).get(
                                "transform") == "container_alias":
                            child_offset = container_alias_offsets.get(
                                ref, {}).get(source_offset)
                            if child_offset is None:
                                pages = frozenset()
                            else:
                                marker_width = len(
                                    source_marker_tokens.get(ref, ()))
                                body_offset = child_offset - marker_width
                                pages = (
                                    source_token_pages.get(ref, {}).get(
                                        body_offset, frozenset())
                                    if body_offset >= 0 else frozenset({
                                        represented["spans"][0]["page"]
                                    })
                                )
                        else:
                            pages = scope_pages_by_ref.get(ref) or frozenset()
                    else:
                        pages = source_token_pages.get(ref, {}).get(
                            source_offset, frozenset())
                        represented = next((
                            entry for entry in entries
                            if entry.get("ref") == ref
                        ), represented_entries.get(ref))
                        if source_offset < 0 and not pages \
                                and represented is not None \
                                and represented["spans"]:
                            pages = frozenset({
                                represented["spans"][0]["page"]})
                    scoped_pages = scope_pages_by_ref.get(ref)
                    if scoped_pages is None:
                        scope_alignment_valid = False
                        pages = frozenset()
                    elif not aliases:
                        if pages and not pages <= scoped_pages:
                            scope_alignment_valid = False
                        pages &= scoped_pages
                    elif (represented or {}).get(
                            "transform") == "container_alias":
                        if pages and not pages <= scoped_pages:
                            scope_alignment_valid = False
                        pages &= scoped_pages
                    aligned_ref_records[ref].add(record_index)
                    candidate = (
                        record_index, output_index,
                        lineage_positions.get(ref, 0))
                    for span_page in pages:
                        register_page_occurrence(ref, span_page, candidate)
            if not scope_alignment_valid:
                lineage_issue_records.add(record_index)
                output_coverage_issues.add(record_index)
            used_marker_occurrences: set[tuple[int, int]] = set()
            for entry in entries:
                ref = entry["ref"]
                marker = source_punctuation_markers.get(ref)
                if marker is None or ref in covered_punctuation_marker_refs:
                    continue
                for start, end, line_end in _punctuation_marker_occurrences(
                        normalized_record_text, marker):
                    occurrence = (start, end)
                    if occurrence in used_marker_occurrences:
                        continue
                    following_index = next((
                        index for index, match in enumerate(
                            record_token_matches)
                        if end <= match.start() < line_end
                    ), None)
                    if (following_index is None
                            or ref not in output_refs_by_index.get(
                                following_index, frozenset())):
                        continue
                    used_marker_occurrences.add(occurrence)
                    covered_punctuation_marker_refs.add(ref)
                    break

    for ref, entry in represented_entries.items():
        if (ref not in eligible
                or entry["transform"] not in {"plain", "list"}
                or not source_tokens.get(ref)):
            continue
        token_pages = source_token_pages.get(ref, {})
        if set(token_pages) != set(range(len(source_tokens[ref]))):
            geometry_issues.add(f"{ref}:incomplete_token_page_mapping")
        span_pages = {span["page"] for span in entry["spans"]}
        if (len(span_pages) > 1
                and len(aligned_ref_records.get(ref, ())) > 1
                and any(len(pages) != 1 for pages in token_pages.values())):
            geometry_issues.add(f"{ref}:ambiguous_token_page_mapping")

    required_source_tokens = sum(
        len(source_tokens.get(ref, ()))
        + len(source_marker_tokens.get(ref, ()))
        + (1 if ref in source_punctuation_markers else 0)
        for ref in eligible)
    covered_source_tokens = 0
    source_coverage_issues = []
    for ref in sorted(eligible):
        tokens = source_tokens.get(ref, ())
        marker_offsets = tuple(
            offset for offset, _ in source_marker_tokens.get(ref, ()))
        covered_markers = sum(
            (ref, offset) in covered_positions for offset in marker_offsets)
        punctuation_marker_required = ref in source_punctuation_markers
        punctuation_marker_complete = (
            not punctuation_marker_required
            or ref in covered_punctuation_marker_refs)
        covered_punctuation_markers = int(
            punctuation_marker_required and punctuation_marker_complete)
        represented = represented_entries.get(ref)
        if (represented is not None
                and represented["transform"] in OPAQUE_TRANSFORMS):
            trusted = source_oracles.get(ref, {})
            oracle_tokens = trusted.get("oracle_lexical_tokens", []) \
                if isinstance(trusted, dict) else []
            expected_opaque_offsets = (
                set(container_alias_offsets.get(ref, {}))
                if represented["transform"] == "container_alias"
                else set(range(len(oracle_tokens)))
            )
            complete = (
                ref in zero_width_opaque_refs
                if not oracle_tokens else
                opaque_covered_offsets.get(ref, set())
                == expected_opaque_offsets
            )
            if complete:
                covered_source_tokens += len(tokens)
            if represented["transform"] == "container_alias":
                markers_complete = complete and punctuation_marker_complete
                if markers_complete:
                    covered_source_tokens += (
                        len(marker_offsets) + covered_punctuation_markers)
            else:
                covered_source_tokens += (
                    covered_markers + covered_punctuation_markers)
                markers_complete = (
                    covered_markers == len(marker_offsets)
                    and punctuation_marker_complete)
            if not complete or not markers_complete:
                source_coverage_issues.append(ref)
            continue
        covered = sum((ref, index) in covered_positions
                      for index in range(len(tokens)))
        covered_source_tokens += (
            covered + covered_markers + covered_punctuation_markers)
        if (covered != len(tokens)
                or covered_markers != len(marker_offsets)
                or not punctuation_marker_complete):
            source_coverage_issues.append(ref)

    page_entries: defaultdict[int, list[tuple[str, dict]]] = defaultdict(list)
    for ref, entry in represented_entries.items():
        if entry["transform"] == "container_alias":
            # These OCR children are alternate segmentations of the visual
            # owner's already checked geometry, not independent reading-order
            # blocks.  Including both would manufacture duplicate constraints.
            continue
        for provenance_index in sorted(represented_scope_indexes.get(ref, ())):
            span = entry["spans"][provenance_index]
            page_entries[span["page"]].append((ref, span))
    exemption_keys = {
        (entry["before_ref"], entry["after_ref"], entry["page"]): (
            record_index, entry)
        for record_index, entry in exemptions
    }
    constraints = 0
    violations = []
    used_exemptions = []
    seen_edges: set[tuple[str, str, int]] = set()

    def enforce_edge(before_ref: str, after_ref: str, page: int) -> None:
        nonlocal constraints
        edge = (before_ref, after_ref, page)
        if before_ref == after_ref or edge in seen_edges:
            return
        before_group = indivisible_native_group_by_ref.get(before_ref)
        if (before_group is not None
                and before_group
                == indivisible_native_group_by_ref.get(after_ref)
                and aligned_ref_records.get(before_ref, set())
                & aligned_ref_records.get(after_ref, set())):
            # One PDF-bound native recovery can join a word across two ordered
            # Docling items. Both refs intentionally own the same indivisible
            # output interval, so only their mutual edge is unobservable; all
            # edges between this unit and external source blocks remain strict.
            return
        seen_edges.add(edge)
        constraints += 1
        before_occurrence = page_occurrence.get((before_ref, page))
        after_occurrence = page_occurrence.get((after_ref, page))
        if before_occurrence is None or after_occurrence is None:
            return
        before_position = before_occurrence[1]
        after_position = after_occurrence[0]
        if before_position < after_position:
            return
        exemption = exemption_keys.get(edge)
        if exemption is not None:
            used_exemptions.append(exemption[1])
            return
        violations.append({
            "before_ref": before_ref,
            "after_ref": after_ref,
            "page": page,
            "before_chunk_index": before_position[0],
            "after_chunk_index": after_position[0],
            "before_output_token": before_position[1],
            "after_output_token": after_position[1],
        })

    for page, values in sorted(page_entries.items()):
        for first_index, (first_ref, first_span) in enumerate(values):
            first_box = _normalized_box(first_span)
            for second_ref, second_span in values[first_index + 1:]:
                if first_ref == second_ref:
                    continue
                second_box = _normalized_box(second_span)
                if not _horizontal_overlap(first_box, second_box):
                    continue
                if first_box[3] <= second_box[1] - 0.5:
                    before_ref, after_ref = first_ref, second_ref
                elif second_box[3] <= first_box[1] - 0.5:
                    before_ref, after_ref = second_ref, first_ref
                else:
                    continue
                enforce_edge(before_ref, after_ref, page)

        # Infer a conventional two-column lane when two horizontally separated
        # clusters substantially overlap vertically. Sparse pages with only
        # one block in each lane still have a source-backed left-to-right edge.
        # Within-lane edges above enforce top-to-bottom order; this transition
        # edge proves the complete left lane precedes the right lane.
        unique_boxes: dict[str, tuple[float, float, float, float]] = {}
        for ref, span in values:
            unique_boxes.setdefault(ref, _normalized_box(span))
        candidates = sorted(
            unique_boxes.items(), key=lambda value: (
                (value[1][0] + value[1][2]) / 2, value[1][1], value[0]))
        if len(candidates) < 2:
            continue
        centers = [(box[0] + box[2]) / 2 for _, box in candidates]
        gaps = [centers[index + 1] - centers[index]
                for index in range(len(centers) - 1)]
        split_index = max(range(len(gaps)), key=gaps.__getitem__)
        page_left = min(box[0] for _, box in candidates)
        page_right = max(box[2] for _, box in candidates)
        page_width = page_right - page_left
        if gaps[split_index] < max(18.0, page_width * 0.08):
            continue
        left_lane = candidates[:split_index + 1]
        right_lane = candidates[split_index + 1:]
        # A detached glyph or short inline repair near the outside margin is
        # not evidence of a second column.  Each inferred lane must contain at
        # least one block wide enough to represent conventional column flow.
        minimum_lane_width = max(18.0, page_width * 0.15)
        if (max(box[2] - box[0] for _, box in left_lane)
                < minimum_lane_width
                or max(box[2] - box[0] for _, box in right_lane)
                < minimum_lane_width):
            continue
        if max(box[2] for _, box in left_lane) \
                > min(box[0] for _, box in right_lane) + 2.0:
            continue
        left_top = min(box[1] for _, box in left_lane)
        left_bottom = max(box[3] for _, box in left_lane)
        right_top = min(box[1] for _, box in right_lane)
        right_bottom = max(box[3] for _, box in right_lane)
        vertical_overlap = min(left_bottom, right_bottom) - max(
            left_top, right_top)
        minimum_height = min(
            left_bottom - left_top, right_bottom - right_top)
        if minimum_height <= 0 or vertical_overlap < minimum_height * 0.25:
            continue
        left_last_ref = max(
            left_lane, key=lambda value: (value[1][3], value[1][1], value[0])
        )[0]
        right_first_ref = min(
            right_lane, key=lambda value: (value[1][1], value[1][3], value[0])
        )[0]
        enforce_edge(left_last_ref, right_first_ref, page)

    used_exemption_keys = {
        (entry["before_ref"], entry["after_ref"], entry["page"])
        for entry in used_exemptions
    }
    for record_index, exemption in exemptions:
        key = (exemption["before_ref"], exemption["after_ref"],
               exemption["page"])
        if key not in used_exemption_keys:
            lineage_issue_records.add(record_index)

    descriptor_evidence = []
    for ref in sorted(eligible):
        item = catalog.get(ref)
        if item is None:
            descriptor_evidence.append({"ref": ref, "missing": True})
            continue
        descriptor, _ = source_descriptor(item)
        descriptor_evidence.append({"ref": ref, **descriptor})
    geometry_evidence = {
        "constraints": [
            {"before_ref": before, "after_ref": after, "page": page}
            for before, after, page in sorted(seen_edges)
        ],
        "violations": violations,
        "order_exemptions": used_exemptions,
    }
    core = {
        "schema_version": SOURCE_FIDELITY_SCHEMA_VERSION,
        "source_tokens": required_source_tokens,
        "covered_source_tokens": covered_source_tokens,
        "output_tokens": output_tokens,
        "covered_output_tokens": covered_output_tokens,
        "source_hash_mismatches": sorted(source_hash_mismatches),
        "lineage_issues": sorted(lineage_issue_records),
        "output_coverage_issues": sorted(output_coverage_issues),
        "source_coverage_issues": source_coverage_issues,
        "geometry_issues": sorted(geometry_issues),
        "geometry_constraints": constraints,
        "geometry_violations": violations,
        "order_exemptions": used_exemptions,
        "transform_counts": dict(sorted(transform_counts.items())),
        "source_descriptor_root_sha256": hashlib.sha256(
            _canonical_json(descriptor_evidence)).hexdigest(),
        "output_attestation_root_sha256": output_attestation_root_sha256(
            records),
        "source_oracle_root_sha256": source_oracle_root_sha256(
            source_oracles),
        "geometry_root_sha256": hashlib.sha256(
            _canonical_json(geometry_evidence)).hexdigest(),
    }
    return {
        **core,
        "evidence_sha256": hashlib.sha256(_canonical_json(core)).hexdigest(),
    }


def validate_summary(value: object) -> bool:
    """Validate the strict passing shape of a source-fidelity summary."""
    if not isinstance(value, dict) or set(value) != SUMMARY_FIELDS:
        return False
    if value.get("schema_version") != SOURCE_FIDELITY_SCHEMA_VERSION:
        return False
    for field in (
            "source_tokens", "covered_source_tokens", "output_tokens",
            "covered_output_tokens", "geometry_constraints"):
        number = value.get(field)
        if (not isinstance(number, int) or isinstance(number, bool)
                or number < 0):
            return False
    if (value["source_tokens"] != value["covered_source_tokens"]
            or value["output_tokens"] != value["covered_output_tokens"]):
        return False
    for field in (
            "source_hash_mismatches", "lineage_issues",
            "output_coverage_issues", "source_coverage_issues",
            "geometry_issues", "geometry_violations"):
        if value.get(field) != []:
            return False
    if (not isinstance(value.get("order_exemptions"), list)
            or any(not isinstance(entry, dict)
                   or set(entry) != ORDER_EXEMPTION_FIELDS
                   for entry in value["order_exemptions"])
            or not isinstance(value.get("transform_counts"), dict)
            or any(transform not in ALLOWED_TRANSFORMS
                   or not isinstance(count, int) or isinstance(count, bool)
                   or count < 0
                   for transform, count in value["transform_counts"].items())
            or any(not _valid_sha256(value.get(field)) for field in (
                "source_descriptor_root_sha256",
                "output_attestation_root_sha256",
                "source_oracle_root_sha256",
                "geometry_root_sha256", "evidence_sha256"))):
        return False
    core = {key: value[key] for key in value if key != "evidence_sha256"}
    return value["evidence_sha256"] == hashlib.sha256(
        _canonical_json(core)).hexdigest()
