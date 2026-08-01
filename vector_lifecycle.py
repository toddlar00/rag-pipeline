"""Dependency-free vector-index mutation lifecycle policy.

This module owns the backend-neutral ordering rules for incremental vector
updates.  Physical Chroma/Qdrant operations are callbacks supplied by the
runtime facade, so importing this leaf never imports a vector client, model
runtime, or ``rag``.
"""

from __future__ import annotations

from collections.abc import Callable, Iterable, Iterator, Mapping
from contextlib import contextmanager
from dataclasses import dataclass
from pathlib import Path
import threading
from typing import Generic, TypeVar
from uuid import uuid4


RecordT = TypeVar("RecordT")
HandleT = TypeVar("HandleT")
VerificationT = TypeVar("VerificationT")
MutationT = TypeVar("MutationT")


@dataclass(frozen=True, slots=True)
class RecordFingerprint(Generic[RecordT]):
    """One source record and its durable vector-index identity."""

    record: RecordT
    stable_id: str
    content_hash: str

    def __post_init__(self) -> None:
        if not isinstance(self.stable_id, str) or not self.stable_id:
            raise ValueError("stable IDs must be non-empty strings")
        if not isinstance(self.content_hash, str) or not self.content_hash:
            raise ValueError("content hashes must be non-empty strings")


@dataclass(frozen=True, slots=True)
class ReconciliationPlan(Generic[RecordT]):
    """Immutable identity plan for one exact source generation."""

    items: tuple[RecordFingerprint[RecordT], ...]
    old_hash_items: tuple[tuple[str, str], ...]
    changed_items: tuple[RecordFingerprint[RecordT], ...]
    removed_ids: tuple[str, ...]
    changed_existing_ids: tuple[str, ...]

    @property
    def new_hashes(self) -> dict[str, str]:
        return {item.stable_id: item.content_hash for item in self.items}

    @property
    def old_hashes(self) -> dict[str, str]:
        return dict(self.old_hash_items)

    @property
    def old_ids(self) -> frozenset[str]:
        return frozenset(key for key, _value in self.old_hash_items)

    @property
    def target_ids(self) -> frozenset[str]:
        return frozenset(item.stable_id for item in self.items)

    @property
    def deletion_ids(self) -> tuple[str, ...]:
        return self.removed_ids + self.changed_existing_ids

    @property
    def expected_after_delete(self) -> frozenset[str]:
        return self.old_ids.difference(self.deletion_ids)

    @property
    def changed_records(self) -> tuple[RecordT, ...]:
        return tuple(item.record for item in self.changed_items)

    @property
    def total_count(self) -> int:
        return len(self.items)

    @property
    def changed_count(self) -> int:
        return len(self.changed_items)

    @property
    def unchanged_count(self) -> int:
        return self.total_count - self.changed_count

    @property
    def removed_count(self) -> int:
        return len(self.removed_ids)


def plan_reconciliation(
        chunk_info: Iterable[tuple[RecordT, str, str]],
        old_hashes: Mapping[str, str]) -> ReconciliationPlan[RecordT]:
    """Plan deterministic additions, replacements, removals, and reuse."""
    if not isinstance(old_hashes, Mapping):
        raise TypeError("old_hashes must be a mapping")
    old_hash_items: list[tuple[str, str]] = []
    for stable_id, content_hash in old_hashes.items():
        if not isinstance(stable_id, str) or not stable_id:
            raise ValueError("old stable IDs must be non-empty strings")
        if not isinstance(content_hash, str):
            raise ValueError("old content hashes must be strings")
        old_hash_items.append((stable_id, content_hash))

    items: list[RecordFingerprint[RecordT]] = []
    observed: set[str] = set()
    for raw_item in chunk_info:
        try:
            record, stable_id, content_hash = raw_item
        except (TypeError, ValueError) as exc:
            raise ValueError(
                "chunk_info entries must contain record, stable ID, and hash"
            ) from exc
        item = RecordFingerprint(record, stable_id, content_hash)
        if item.stable_id in observed:
            raise ValueError(
                f"duplicate stable ID in source generation: {item.stable_id}")
        observed.add(item.stable_id)
        items.append(item)

    old = dict(old_hash_items)
    changed = tuple(
        item for item in items
        if old.get(item.stable_id) != item.content_hash
    )
    removed = tuple(
        stable_id for stable_id, _content_hash in old_hash_items
        if stable_id not in observed
    )
    changed_existing = tuple(
        item.stable_id for item in changed if item.stable_id in old
    )
    return ReconciliationPlan(
        items=tuple(items),
        old_hash_items=tuple(old_hash_items),
        changed_items=changed,
        removed_ids=removed,
        changed_existing_ids=changed_existing,
    )


class UpdateGuard:
    """Own and revalidate one collection-scoped dirty marker."""

    def __init__(
            self, *, backend: str, collection_name: str,
            marker_path: Path, active_token: str | None,
            marker_owned_fn: Callable[[str | None], bool],
            begin_update_fn: Callable[[str, bool], object],
            finish_update_fn: Callable[[str], None],
            token_factory: Callable[[], str] | None = None) -> None:
        if not isinstance(backend, str) or not backend:
            raise ValueError("backend must be a non-empty string")
        if not isinstance(collection_name, str) or not collection_name:
            raise ValueError("collection_name must be a non-empty string")
        self.backend = backend
        self.collection_name = collection_name
        self.marker_path = Path(marker_path)
        self._marker_owned_fn = marker_owned_fn
        self._begin_update_fn = begin_update_fn
        self._finish_update_fn = finish_update_fn
        self._token_factory = token_factory or (lambda: uuid4().hex)
        self._token = (
            active_token if marker_owned_fn(active_token) else None
        )

    @property
    def active(self) -> bool:
        return self._token is not None

    @property
    def token(self) -> str | None:
        return self._token

    def _ownership_error(self) -> RuntimeError:
        return RuntimeError(
            f"{self.backend.title()} index update marker ownership changed "
            "before commit; the collection remains dirty and must be rebuilt"
        )

    def assert_owned(self) -> None:
        """Fail if this lifecycle no longer owns its active marker."""
        if self._token is not None and not self._marker_owned_fn(self._token):
            raise self._ownership_error()

    def ensure_owned(self) -> str:
        """Create or revalidate the marker before physical mutation."""
        if self._token is not None:
            self.assert_owned()
            return self._token

        token = self._token_factory()
        if not isinstance(token, str) or not token:
            raise ValueError("update token factory returned an invalid token")
        try:
            self._begin_update_fn(token, self.marker_path.exists())
        except BaseException:
            self._token = None
            raise
        if not self._marker_owned_fn(token):
            self._token = None
            raise self._ownership_error()
        self._token = token
        return token

    def finish(self) -> None:
        """Remove an owned marker only after the manifest is durable."""
        if self._token is None:
            return
        self.assert_owned()
        self._finish_update_fn(self._token)
        self._token = None


@dataclass(frozen=True, slots=True)
class VerificationReceipt(Generic[VerificationT]):
    """Opaque evidence that one lifecycle epoch matched expected IDs."""

    _owner: object
    _epoch: int
    expected_ids: frozenset[str]
    value: VerificationT


class VectorUpdateLifecycle:
    """Serialize guarded mutations, verification, and durable commit."""

    def __init__(self, *, target_ids: Iterable[str], guard: UpdateGuard) -> None:
        target = frozenset(target_ids)
        if not all(isinstance(value, str) and value for value in target):
            raise ValueError("target_ids must contain non-empty strings")
        if not isinstance(guard, UpdateGuard):
            raise TypeError("guard must be an UpdateGuard")
        self.target_ids = target
        self.guard = guard
        self._lock = threading.RLock()
        self._receipt_owner = object()
        self._epoch = 0
        self._committed = False
        self._active_operation: str | None = None

    @property
    def committed(self) -> bool:
        return self._committed

    def _require_open(self) -> None:
        if self._committed:
            raise RuntimeError("vector update lifecycle is already committed")
        if self._active_operation is not None:
            raise RuntimeError(
                "vector update lifecycle reentry is not allowed while "
                f"{self._active_operation} is in progress")

    @contextmanager
    def _operation(self, name: str) -> Iterator[None]:
        """Reject same-thread callback reentry while preserving serialization."""
        self._require_open()
        self._active_operation = name
        try:
            yield
        finally:
            self._active_operation = None

    def prepare_mutation(self) -> None:
        """Durably establish and verify the guard before worker startup."""
        with self._lock:
            with self._operation("marker preparation"):
                self.guard.ensure_owned()

    def mutate(self, callback: Callable[[], MutationT]) -> MutationT:
        """Run one physical mutation after revalidating marker ownership."""
        with self._lock:
            with self._operation("mutation"):
                self.guard.ensure_owned()
                self._epoch += 1
                return callback()

    def verify(
            self, expected_ids: Iterable[str],
            callback: Callable[[frozenset[str]], VerificationT],
            ) -> VerificationReceipt[VerificationT]:
        """Verify physical IDs and bind the result to the current epoch."""
        expected = frozenset(expected_ids)
        if not all(isinstance(value, str) and value for value in expected):
            raise ValueError("expected IDs must contain non-empty strings")
        with self._lock:
            with self._operation("verification"):
                self.guard.assert_owned()
                verification_epoch = self._epoch
                value = callback(expected)
                return VerificationReceipt(
                    self._receipt_owner, verification_epoch, expected, value)

    def commit(
            self, receipt: VerificationReceipt[VerificationT], *,
            close_client_fn: Callable[[], None],
            save_manifest_fn: Callable[[], object],
            ) -> VerificationT:
        """Close, publish the manifest, then clear the marker in that order."""
        with self._lock:
            with self._operation("commit"):
                if (not isinstance(receipt, VerificationReceipt)
                        or receipt._owner is not self._receipt_owner
                        or receipt._epoch != self._epoch
                        or receipt.expected_ids != self.target_ids):
                    raise RuntimeError(
                        "current target IDs must be verified after the last "
                        "physical mutation before commit")
                self.guard.assert_owned()
                close_client_fn()
                self.guard.assert_owned()
                save_manifest_fn()
                self.guard.finish()
                self._committed = True
                return receipt.value


@dataclass(frozen=True, slots=True)
class ReconciledCollection(Generic[HandleT, VerificationT]):
    """Backend handle plus the latest valid physical-state receipt."""

    handle: HandleT
    receipt: VerificationReceipt[VerificationT] | None


def reconcile_collection(
        *, lifecycle: VectorUpdateLifecycle,
        plan: ReconciliationPlan[RecordT],
        handle: HandleT | None,
        collection_exists: bool,
        rebuild_collection: bool,
        delete_collection_fn: Callable[[], None],
        create_collection_fn: Callable[[], HandleT],
        verify_stable_ids_fn: Callable[
            [HandleT, frozenset[str]], VerificationT],
        delete_stable_ids_fn: Callable[
            [HandleT, tuple[str, ...], VerificationT], None],
        ) -> ReconciledCollection[HandleT, VerificationT]:
    """Reconcile collection existence and all stale durable identities."""
    if not isinstance(collection_exists, bool):
        raise TypeError("collection_exists must be a boolean")
    if not isinstance(rebuild_collection, bool):
        raise TypeError("rebuild_collection must be a boolean")

    compatible_existing = collection_exists and not rebuild_collection
    if rebuild_collection:
        lifecycle.mutate(delete_collection_fn)
        handle = None
        collection_exists = False

    if not collection_exists:
        handle = lifecycle.mutate(create_collection_fn)
        collection_exists = True

    if handle is None:
        raise RuntimeError("collection adapter did not provide a handle")

    receipt: VerificationReceipt[VerificationT] | None = None
    if plan.old_ids:
        receipt = lifecycle.verify(
            plan.old_ids,
            lambda expected: verify_stable_ids_fn(handle, expected),
        )
        if plan.deletion_ids:
            previous = receipt.value
            lifecycle.mutate(
                lambda: delete_stable_ids_fn(
                    handle, plan.deletion_ids, previous))
            receipt = lifecycle.verify(
                plan.expected_after_delete,
                lambda expected: verify_stable_ids_fn(handle, expected),
            )
    elif compatible_existing:
        receipt = lifecycle.verify(
            frozenset(),
            lambda expected: verify_stable_ids_fn(handle, expected),
        )

    if not plan.changed_items and receipt is None:
        receipt = lifecycle.verify(
            plan.target_ids,
            lambda expected: verify_stable_ids_fn(handle, expected),
        )
    return ReconciledCollection(handle=handle, receipt=receipt)


__all__ = [
    "RecordFingerprint",
    "ReconciledCollection",
    "ReconciliationPlan",
    "UpdateGuard",
    "VectorUpdateLifecycle",
    "VerificationReceipt",
    "plan_reconciliation",
    "reconcile_collection",
]
