"""Shared immutable schema and scoring contract for retrieval evaluation."""

from __future__ import annotations

import re
from collections.abc import Callable, Mapping, Sequence
from dataclasses import dataclass, field
from types import MappingProxyType

import table_retrieval_core


REPORT_SCHEMA_VERSION = 6
GROUNDING_SCORER_VERSION = 2
JUDGMENT_SCORER_VERSION = 2
TABLE_FAMILY_JUDGMENT_SCHEMA_VERSION = 1
_ATTESTATION_FACTORY_TOKEN = object()
_HEX64_RE = re.compile(r"[0-9a-f]{64}")


@dataclass(frozen=True)
class TableFamilyAttestation:
    """Opaque, immutable table families bound to one exact corpus snapshot."""

    corpus_sha256: str
    corpus_record_count: int
    id_scheme: str
    members: Mapping[str, frozenset[str]]
    _factory_token: object = field(repr=False, compare=False)

    def __post_init__(self) -> None:
        if self._factory_token is not _ATTESTATION_FACTORY_TOKEN:
            raise TypeError(
                "table-family attestations must be derived from corpus records")


def attest_table_families(
        records: Sequence[dict], *, stable_id_fn: Callable[[dict], str],
        corpus_sha256: str, id_scheme: str,
) -> TableFamilyAttestation:
    """Validate table lineage and seal its members to an artifact binding."""
    if (not isinstance(corpus_sha256, str)
            or _HEX64_RE.fullmatch(corpus_sha256) is None):
        raise ValueError(
            "table-family corpus SHA-256 must be a lowercase digest")
    if not isinstance(id_scheme, str) or not id_scheme.strip():
        raise ValueError("table-family ID scheme must be non-empty text")
    members = table_retrieval_core.validated_table_family_members(
        records, stable_id_fn=stable_id_fn)
    immutable_members = MappingProxyType({
        parent_id: frozenset(member_ids)
        for parent_id, member_ids in members.items()
    })
    return TableFamilyAttestation(
        corpus_sha256=corpus_sha256,
        corpus_record_count=len(records),
        id_scheme=id_scheme.strip(),
        members=immutable_members,
        _factory_token=_ATTESTATION_FACTORY_TOKEN,
    )


def table_retrieval_policy_contract() -> dict:
    """Return the retrieval-generation and family-collapse semantic contract."""
    return {
        "schema_version": table_retrieval_core.TABLE_RETRIEVAL_SCHEMA_VERSION,
        "minimum_source_rows": (
            table_retrieval_core.DEFAULT_TABLE_CHILD_MIN_ROWS),
        "max_children_per_parent": (
            table_retrieval_core.MAX_TABLE_CHILDREN_PER_PARENT),
        "max_children_per_corpus": (
            table_retrieval_core.MAX_TABLE_CHILDREN_PER_CORPUS),
        "collapse_version": (
            table_retrieval_core.TABLE_FAMILY_COLLAPSE_VERSION),
    }


def scoring_contract() -> dict:
    """Return every evaluator semantic version bound by release policies."""
    return {
        "report_schema_version": REPORT_SCHEMA_VERSION,
        "grounding_scorer_version": GROUNDING_SCORER_VERSION,
        "judgment_scorer_version": JUDGMENT_SCORER_VERSION,
        "table_family_judgment_schema_version": (
            TABLE_FAMILY_JUDGMENT_SCHEMA_VERSION),
        "table_retrieval_policy": table_retrieval_policy_contract(),
    }
