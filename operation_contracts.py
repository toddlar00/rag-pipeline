"""Stable, dependency-free contracts shared by CLI, UI, and services."""

from __future__ import annotations

import math
from dataclasses import dataclass


_INDEX_BACKENDS = frozenset({"chroma", "qdrant"})
_INDEX_DISPOSITIONS = frozenset({
    "created", "rebuilt", "updated", "unchanged",
})


@dataclass(frozen=True)
class IndexOperationMetrics:
    """Content-free physical mutation and queue measurements."""

    collection_delete_calls: int
    collection_create_calls: int
    record_delete_calls: int
    upsert_calls: int
    queue_put_count: int
    queue_saturation_events: int
    queue_wait_ms: float

    def __post_init__(self) -> None:
        for name in (
                "collection_delete_calls", "collection_create_calls",
                "record_delete_calls", "upsert_calls", "queue_put_count",
                "queue_saturation_events"):
            value = getattr(self, name)
            if (isinstance(value, bool) or not isinstance(value, int)
                    or value < 0):
                raise ValueError(f"{name} must be a non-negative integer")
        if (isinstance(self.queue_wait_ms, bool)
                or not isinstance(self.queue_wait_ms, (int, float))
                or not math.isfinite(float(self.queue_wait_ms))
                or self.queue_wait_ms < 0):
            raise ValueError("queue_wait_ms must be finite and non-negative")
        if not self.queue_put_count and (
                self.queue_saturation_events or self.queue_wait_ms):
            raise ValueError(
                "queue waits and saturation require an admitted queue item")

    @property
    def physical_mutation_calls(self) -> int:
        return (
            self.collection_delete_calls
            + self.collection_create_calls
            + self.record_delete_calls
            + self.upsert_calls
        )

    def telemetry_metrics(self) -> dict[str, int | float]:
        return {
            "collection_delete_calls": self.collection_delete_calls,
            "collection_create_calls": self.collection_create_calls,
            "record_delete_calls": self.record_delete_calls,
            "upsert_calls": self.upsert_calls,
            "physical_mutation_calls": self.physical_mutation_calls,
            "queue_put_count": self.queue_put_count,
            "queue_saturation_events": self.queue_saturation_events,
            "queue_wait_ms": round(float(self.queue_wait_ms), 3),
        }


@dataclass(frozen=True)
class IndexOutcome:
    """Committed index mutation counts without paths or corpus content."""

    backend: str
    disposition: str
    total_records: int
    changed_records: int
    unchanged_records: int
    removed_records: int
    upserted_records: int
    batch_count: int
    physical_count: int
    committed: bool
    operations: IndexOperationMetrics | None = None

    def __post_init__(self) -> None:
        if self.backend not in _INDEX_BACKENDS:
            raise ValueError(f"unsupported index backend: {self.backend}")
        if self.disposition not in _INDEX_DISPOSITIONS:
            raise ValueError(
                f"unsupported index disposition: {self.disposition}")
        for name in (
                "total_records", "changed_records", "unchanged_records",
                "removed_records", "upserted_records", "batch_count",
                "physical_count"):
            value = getattr(self, name)
            if (isinstance(value, bool) or not isinstance(value, int)
                    or value < 0):
                raise ValueError(f"{name} must be a non-negative integer")
        if not isinstance(self.committed, bool):
            raise TypeError("committed must be a boolean")
        if not self.committed:
            raise ValueError("IndexOutcome represents only committed state")
        if self.changed_records + self.unchanged_records != self.total_records:
            raise ValueError(
                "changed plus unchanged records must equal total records")
        if self.physical_count != self.total_records:
            raise ValueError(
                "a committed outcome must match the physical record count")
        if self.upserted_records != self.changed_records:
            raise ValueError("upserted records must equal changed records")
        if bool(self.batch_count) != bool(self.upserted_records):
            raise ValueError(
                "batch count must be positive exactly when records were upserted")
        if self.disposition == "unchanged" and (
                self.changed_records or self.removed_records):
            raise ValueError("an unchanged outcome cannot contain mutations")
        if self.disposition in {"created", "rebuilt"} and (
                self.unchanged_records or self.removed_records):
            raise ValueError(
                "created or rebuilt outcomes cannot reuse or remove records")
        if self.disposition == "updated" and not (
                self.changed_records or self.removed_records):
            raise ValueError("an updated outcome must contain a mutation")
        if self.operations is not None:
            operations = self.operations
            if not isinstance(operations, IndexOperationMetrics):
                raise TypeError("operations must be IndexOperationMetrics")
            if operations.upsert_calls != self.batch_count:
                raise ValueError("upsert calls must equal the committed batch count")
            if (self.backend == "qdrant"
                    and operations.queue_put_count != self.batch_count):
                raise ValueError(
                    "Qdrant queue puts must equal the committed batch count")
            if (self.backend == "chroma"
                    and operations.queue_put_count not in {
                        0, self.batch_count}):
                raise ValueError(
                    "queue puts must be zero or equal the committed batch count")
            if self.disposition == "unchanged" and (
                    operations.physical_mutation_calls
                    or operations.queue_put_count):
                raise ValueError(
                    "an unchanged outcome cannot report physical operations")
            if self.disposition == "created" and (
                    operations.collection_create_calls != 1
                    or operations.collection_delete_calls
                    or operations.record_delete_calls):
                raise ValueError(
                    "a created outcome requires only one collection create")
            if self.disposition == "rebuilt" and (
                    operations.collection_create_calls != 1
                    or operations.collection_delete_calls != 1
                    or operations.record_delete_calls):
                raise ValueError(
                    "a rebuilt outcome requires one collection delete and create")
            if self.disposition == "updated" and (
                    operations.collection_create_calls
                    or operations.collection_delete_calls):
                raise ValueError(
                    "an incremental update cannot report collection replacement")
            if (self.disposition == "updated" and self.removed_records
                    and not operations.record_delete_calls):
                raise ValueError(
                    "an update with removals requires a record delete call")
            if (self.backend == "qdrant"
                    and operations.record_delete_calls > 1):
                raise ValueError(
                    "Qdrant uses at most one batched point-delete call")

    def telemetry_metrics(self) -> dict[str, int | float | bool]:
        """Return numeric/boolean fields accepted by run telemetry."""
        metrics = {
            "total_records": self.total_records,
            "changed_records": self.changed_records,
            "unchanged_records": self.unchanged_records,
            "removed_records": self.removed_records,
            "upserted_records": self.upserted_records,
            "batch_count": self.batch_count,
            "physical_count": self.physical_count,
            "committed": self.committed,
            "created": self.disposition == "created",
            "rebuilt": self.disposition == "rebuilt",
            "updated": self.disposition == "updated",
            "unchanged": self.disposition == "unchanged",
        }
        if self.operations is not None:
            metrics.update(self.operations.telemetry_metrics())
        return metrics
