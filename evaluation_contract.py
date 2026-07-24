"""Shared immutable schema and scoring contract for retrieval evaluation."""

from __future__ import annotations

import table_retrieval_core


REPORT_SCHEMA_VERSION = 6
GROUNDING_SCORER_VERSION = 2
JUDGMENT_SCORER_VERSION = 2
TABLE_FAMILY_JUDGMENT_SCHEMA_VERSION = 1


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
