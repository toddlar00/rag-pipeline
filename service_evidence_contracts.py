"""Strict, dependency-light contracts for opt-in read-only search evidence.

Digests are correlation labels, never scan capabilities or claims of OCR
accuracy. Config paths are private operator bindings; this module performs no IO.
"""

from __future__ import annotations

from dataclasses import dataclass, field
import hashlib
import json
import os
from pathlib import Path
import re
from typing import Mapping

import service_contracts as contracts


EVIDENCE_SCHEMA_VERSION = 1
MAX_PUBLIC_EVIDENCE_RESPONSE_BYTES = 2_000_000
MAX_EVIDENCE_BYTES = 96_000
MAX_EVIDENCE_CORPORA = 64
MAX_EVIDENCE_PAGES = 5_000
MAX_PROVENANCE_ATOMS = 100_000
MAX_CONFIG_PATH_CHARACTERS = 4096
TRANSFORMS = frozenset({"plain", "list", "table", "figure", "native_repair", "container_alias"})
PROVENANCE_SCOPES = frozenset({"record_source_items", "table_parent_source_items", "unavailable"})
PAGE_SPACES = frozenset({"original_pdf", "conversion_input", "unavailable"})
RELATED_PAGE_STATUSES = frozenset({"review_required", "empty_candidate", "retry_failed", "deferred", "not_selected"})
ISSUES = frozenset({"source_provenance_unavailable", "original_page_mapping_unavailable", "table_parent_scope"})
GENERATION_DIGEST_FIELDS = frozenset({
    "index_manifest_sha256", "chunks_sha256", "quality_sha256",
    "chunk_completion_sha256", "source_oracles_sha256", "docling_sha256",
    "conversion_manifest_sha256", "recovery_sha256",
})
_HEX = re.compile(r"^[0-9a-f]{64}$")
_SCOPE_FIELDS = frozenset({"provenance_scope", "page_space", "pages", "transforms",
                           "provenance_atom_count", "provenance_sha256"})
_ENTRY_FIELDS = frozenset({"source_id", "record_sha256", "text_sha256", "evidence_id",
                           "source_scope", "related_page_ocr", "text_accuracy", "review_scope", "issues"})


def _fail() -> None:
    raise contracts.ServiceContractError("service_unavailable")


def _shape(value: object, fields: frozenset | set) -> dict:
    if type(value) is not dict or value.keys() != fields:
        _fail()
    return value


def _digest(value: object, *, nullable: bool = False) -> str | None:
    if value is None and nullable:
        return None
    if type(value) is not str or _HEX.fullmatch(value) is None:
        _fail()
    return value


def _canonical(value: dict) -> bytes:
    # Called only after shape and bounded scalar/list validation.
    return json.dumps(value, ensure_ascii=False, sort_keys=True,
                      separators=(",", ":"), allow_nan=False).encode("utf-8")


def _pages(value: object) -> list[int]:
    if (type(value) is not list or len(value) > MAX_EVIDENCE_PAGES
            or any(type(page) is not int or not 1 <= page <= MAX_EVIDENCE_PAGES for page in value)
            or value != sorted(set(value))):
        _fail()
    return list(value)


def _enums(value: object, allowed: frozenset) -> list[str]:
    if (type(value) is not list or len(value) > len(allowed)
            or any(type(item) is not str or item not in allowed for item in value)
            or value != sorted(set(value))):
        _fail()
    return list(value)


def _scope(value: object) -> dict:
    value = _shape(value, _SCOPE_FIELDS)
    provenance = value["provenance_scope"]
    space = value["page_space"]
    if (type(provenance) is not str or provenance not in PROVENANCE_SCOPES
            or type(space) is not str or space not in PAGE_SPACES):
        _fail()
    pages = _pages(value["pages"])
    transforms = _enums(value["transforms"], TRANSFORMS)
    count = value["provenance_atom_count"]
    if type(count) is not int or not 0 <= count <= MAX_PROVENANCE_ATOMS:
        _fail()
    digest = _digest(value["provenance_sha256"], nullable=True)
    if provenance == "unavailable":
        if space != "unavailable" or pages or transforms or count != 0 or digest is not None:
            _fail()
    elif (space == "unavailable" or not pages or not transforms or count < 1 or digest is None
          or len(pages) > count or len(transforms) > count):
        _fail()
    return {"provenance_scope": provenance, "page_space": space, "pages": pages,
            "transforms": transforms, "provenance_atom_count": count, "provenance_sha256": digest}


def _generation_digests(value: object) -> dict:
    value = _shape(value, GENERATION_DIGEST_FIELDS)
    return {name: _digest(value[name], nullable=name not in {"index_manifest_sha256", "chunks_sha256"})
            for name in sorted(GENERATION_DIGEST_FIELDS)}


def generation_id(digests: dict, original_source_sha256: str | None = None) -> str:
    """Domain-separated opaque generation ID; private source hash is not returned."""
    checked = _generation_digests(digests)
    original = _digest(original_source_sha256, nullable=True)
    payload = {"kind": "service_evidence_generation", "schema_version": 1,
               "digests": checked, "original_source_sha256": original}
    return hashlib.sha256(_canonical(payload)).hexdigest()


def evidence_id(generation: str, record_sha256: str, source_scope: dict) -> str:
    """Bind a full manifested record and its scope to a non-redeemable generation."""
    payload = {"kind": "service_evidence_identity", "schema_version": 1,
               "generation_id": _digest(generation), "record_sha256": _digest(record_sha256),
               "source_scope": _scope(source_scope)}
    return hashlib.sha256(_canonical(payload)).hexdigest()


@dataclass(frozen=True, slots=True)
class EvidenceCorpusConfig:
    corpus_id: str
    docling_path: Path | None = field(default=None, repr=False)
    recovery_path: Path | None = field(default=None, repr=False)

    def __post_init__(self) -> None:
        contracts.validate_corpus_id(self.corpus_id)
        if self.recovery_path is not None and self.docling_path is None:
            raise contracts.ServiceContractError()
        for value in (self.docling_path, self.recovery_path):
            if value is not None and (not isinstance(value, Path) or not value.is_absolute()
                                      or not _valid_path_string(str(value))):
                raise contracts.ServiceContractError()


def _valid_path_string(value: object) -> bool:
    return (type(value) is str and bool(value.strip()) and len(value) <= MAX_CONFIG_PATH_CHARACTERS
            and not any(ord(char) < 32 for char in value)
            and not any(0xD800 <= ord(char) <= 0xDFFF for char in value))


def parse_evidence_corpus(payload: object, *, base: Path) -> EvidenceCorpusConfig:
    """Parse one exact operator entry without resolving symlinks or opening paths."""
    if (type(payload) is not dict or payload.keys() != {"corpus_id", "docling_path", "recovery_path"}
            or not isinstance(base, Path) or not base.is_absolute()):
        raise contracts.ServiceContractError()
    paths = []
    for name in ("docling_path", "recovery_path"):
        value = payload[name]
        if value is None:
            paths.append(None)
        elif not _valid_path_string(value):
            raise contracts.ServiceContractError()
        else:
            # abspath performs lexical normalization only; no exists/resolve/stat.
            paths.append(Path(os.path.abspath(base / value)))
    return EvidenceCorpusConfig(payload["corpus_id"], *paths)


def parse_evidence_registry(payload: object, *, base: Path,
                            corpora: Mapping[str, contracts.CorpusConfig]) -> dict[str, EvidenceCorpusConfig]:
    """Require a bounded subset of the existing closed corpus registry."""
    if (type(payload) is not dict or payload.keys() != {"schema_version", "kind", "corpora"}
            or type(payload["schema_version"]) is not int or payload["schema_version"] != 1
            or type(payload["kind"]) is not str or payload["kind"] != "service_evidence_config"
            or type(payload["corpora"]) is not list or len(payload["corpora"]) > MAX_EVIDENCE_CORPORA
            or not isinstance(base, Path) or not base.is_absolute()
            or not isinstance(corpora, Mapping) or len(corpora) > MAX_EVIDENCE_CORPORA):
        raise contracts.ServiceContractError()
    result = {}
    for entry in payload["corpora"]:
        config = parse_evidence_corpus(entry, base=base)
        existing = corpora.get(config.corpus_id)
        if (config.corpus_id in result or not isinstance(existing, contracts.CorpusConfig)
                or existing.corpus_id != config.corpus_id):
            raise contracts.ServiceContractError()
        result[config.corpus_id] = config
    return result


def config_payload(config: EvidenceCorpusConfig) -> dict:
    """Private worker serialization only; paths must never enter public responses."""
    if not isinstance(config, EvidenceCorpusConfig):
        raise contracts.ServiceContractError()
    return {"corpus_id": config.corpus_id,
            "docling_path": str(config.docling_path) if config.docling_path is not None else None,
            "recovery_path": str(config.recovery_path) if config.recovery_path is not None else None}


def _preflight_search_metadata(search: object) -> None:
    # The v1 serializer may truncate source metadata. Here an already-public
    # envelope must be small before that serializer can copy any input lists.
    if type(search) is not dict:
        _fail()
    hits = search.get("hits")
    if type(hits) is not list or len(hits) > contracts.MAX_SEARCH_LIMIT:
        _fail()
    for hit in hits:
        if type(hit) is not dict:
            _fail()
        metadata = hit.get("metadata")
        if (type(metadata) is not dict or len(metadata) > len(contracts.PUBLIC_METADATA_FIELDS)
                or any(type(key) is not str or key not in contracts.PUBLIC_METADATA_FIELDS for key in metadata)):
            _fail()
        for value in metadata.values():
            if type(value) is list:
                if len(value) > 16:
                    _fail()
                values = value
            else:
                values = (value,)
            for item in values:
                if type(item) not in {type(None), bool, int, float, str}:
                    _fail()
                if type(item) is str and len(item) > 512:
                    _fail()


def validate_public_evidence_search_response(payload: object, *, expected_corpus_id: str,
                                              expected_request_id: str,
                                              expected_request: contracts.SearchRequest | None = None) -> dict:
    """Validate bounded fields and ordered associations, not private source truth.

    The worker establishes exact hit/manifest/source correspondence. This public
    validator cannot authenticate digests, verify OCR, or prove retry adoption.
    """
    checked_request = None
    if expected_request is not None:
        if (not isinstance(expected_request, contracts.SearchRequest)
                or not isinstance(expected_request.filters, contracts.SearchFilters)):
            _fail()
        try:
            checked_request = contracts.parse_search_request({
                "query": expected_request.query, "mode": expected_request.mode,
                "limit": expected_request.limit, "filters": expected_request.filters.as_dict()})
        except (TypeError, ValueError, OverflowError, RecursionError):
            _fail()
    value = _shape(payload, {"schema_version", "kind", "search", "generation", "evidence"})
    if (type(value["schema_version"]) is not int or value["schema_version"] != 1
            or type(value["kind"]) is not str or value["kind"] != "service_evidence_search"):
        _fail()
    _preflight_search_metadata(value["search"])
    try:
        search = contracts.validate_public_search_response(
            value["search"], expected_corpus_id=expected_corpus_id,
            expected_request_id=expected_request_id)
    except (TypeError, ValueError, OverflowError, RecursionError):
        _fail()
    if checked_request is not None and (search["requested_mode"] != checked_request.mode
                                       or len(search["hits"]) > checked_request.limit):
        _fail()
    if checked_request is not None:
        for hit in search["hits"]:
            for name, expected in checked_request.filters.as_dict().items():
                actual = hit["metadata"].get(name)
                if type(actual) is not type(expected) or actual != expected:
                    _fail()
    raw_generation = _shape(value["generation"], GENERATION_DIGEST_FIELDS | {"generation_id"})
    generation = _generation_digests({name: raw_generation[name] for name in GENERATION_DIGEST_FIELDS})
    generation["generation_id"] = _digest(raw_generation["generation_id"])
    proof_available = [generation[name] is not None for name in (
        "quality_sha256", "chunk_completion_sha256", "source_oracles_sha256")]
    if (any(proof_available) != all(proof_available)
            or (generation["docling_sha256"] is None) != (generation["conversion_manifest_sha256"] is None)
            or generation["docling_sha256"] is not None and not all(proof_available)):
        _fail()
    if generation["recovery_sha256"] is not None and (generation["docling_sha256"] is None
                                                     or generation["conversion_manifest_sha256"] is None):
        _fail()
    raw_entries = value["evidence"]
    if type(raw_entries) is not list or len(raw_entries) != len(search["hits"]):
        _fail()
    entries = []
    seen = set()
    for raw, hit in zip(raw_entries, search["hits"]):
        raw = _shape(raw, _ENTRY_FIELDS)
        source_id = raw["source_id"]
        if (type(source_id) is not str or source_id != hit["source_id"] or source_id in seen
                or type(raw["text_accuracy"]) is not str or raw["text_accuracy"] != "not_verified"
                or type(raw["review_scope"]) is not str or raw["review_scope"] != "unmapped"):
            _fail()
        seen.add(source_id)
        record_digest = _digest(raw["record_sha256"])
        text_digest = _digest(raw["text_sha256"])
        scope = _scope(raw["source_scope"])
        if scope["provenance_scope"] != "unavailable" and any(generation[name] is None for name in (
                "quality_sha256", "chunk_completion_sha256", "source_oracles_sha256")):
            _fail()
        if scope["page_space"] == "original_pdf" and generation["conversion_manifest_sha256"] is None:
            _fail()
        identifier = evidence_id(generation["generation_id"], record_digest, scope)
        if _digest(raw["evidence_id"]) != identifier:
            _fail()
        if not hit["text_truncated"] and hashlib.sha256(hit["text"].encode("utf-8")).hexdigest() != text_digest:
            _fail()
        issues = _enums(raw["issues"], ISSUES)
        expected_issues = []
        if scope["provenance_scope"] == "unavailable":
            expected_issues.append("source_provenance_unavailable")
        if scope["page_space"] == "conversion_input":
            expected_issues.append("original_page_mapping_unavailable")
        if scope["provenance_scope"] == "table_parent_source_items":
            expected_issues.append("table_parent_scope")
        if issues != sorted(expected_issues):
            _fail()
        related = _shape(raw["related_page_ocr"], {"status", "reason", "pages"})
        if type(related["pages"]) is not list or len(related["pages"]) > MAX_EVIDENCE_PAGES:
            _fail()
        if generation["recovery_sha256"] is None:
            reason = "not_configured"
        elif scope["provenance_scope"] == "unavailable":
            reason = "source_provenance_unavailable"
        elif scope["page_space"] != "original_pdf":
            reason = "original_page_mapping_unavailable"
        else:
            reason = None
        related_pages = []
        if reason is not None:
            if related != {"status": "unavailable", "reason": reason, "pages": []}:
                _fail()
        else:
            if related["status"] != "available" or related["reason"] is not None or len(related["pages"]) != len(scope["pages"]):
                _fail()
            for page, expected_page in zip(related["pages"], scope["pages"]):
                page = _shape(page, {"page_number", "status"})
                if (type(page["page_number"]) is not int or page["page_number"] != expected_page
                        or type(page["status"]) is not str or page["status"] not in RELATED_PAGE_STATUSES):
                    _fail()
                related_pages.append(dict(page))
        entries.append({"source_id": source_id, "record_sha256": record_digest, "text_sha256": text_digest,
                        "evidence_id": identifier, "source_scope": scope,
                        "related_page_ocr": {"status": "available" if reason is None else "unavailable",
                                             "reason": reason, "pages": related_pages},
                        "text_accuracy": "not_verified", "review_scope": "unmapped", "issues": issues})
    companion = {"schema_version": 1, "kind": "service_evidence_search", "generation": generation,
                 "evidence": entries}
    if len(_canonical(companion)) > MAX_EVIDENCE_BYTES:
        _fail()
    result = {**companion, "search": search}
    encoded = _canonical(result)
    if len(encoded) > MAX_PUBLIC_EVIDENCE_RESPONSE_BYTES:
        _fail()
    return json.loads(encoded)


validate_evidence_response = validate_public_evidence_search_response
