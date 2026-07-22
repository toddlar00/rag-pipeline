"""Stable, dependency-free contracts shared by CLI, UI, and services."""

from __future__ import annotations

from dataclasses import dataclass


_INDEX_BACKENDS = frozenset({"chroma", "qdrant"})
_INDEX_DISPOSITIONS = frozenset({
    "created", "rebuilt", "updated", "unchanged",
})


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

    def telemetry_metrics(self) -> dict[str, int | bool]:
        """Return numeric/boolean fields accepted by run telemetry."""
        return {
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
