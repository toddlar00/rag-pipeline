#!/usr/bin/env python3
"""Plan or synchronize byte-pinned model artifacts for offline runtime use."""

from __future__ import annotations

import argparse
from collections.abc import Callable, Sequence
from dataclasses import dataclass, replace
import hashlib
import json
import os
from pathlib import Path
import shutil
import sys


PROJECT_ROOT = Path(__file__).resolve().parents[1]
if str(PROJECT_ROOT) not in sys.path:
    sys.path.insert(0, str(PROJECT_ROOT))

import model_artifacts  # noqa: E402
import release_security  # noqa: E402


PLAN_SCHEMA = "rag.model-sync-plan"
PLAN_SCHEMA_VERSION = 1
_FREE_SPACE_RESERVE_MIN_BYTES = 64 * 1024 * 1024

# Presets name independently published runtime bundles. They intentionally do
# not collapse consumers that happen to use the same source bytes: the runtime
# publisher materializes and verifies each consumer as a separate bundle.
TASK_PRESETS: dict[str, tuple[tuple[str, str], ...]] = {
    "pdf-ingestion": (
        ("nomic-ai/nomic-embed-text-v2-moe", "chunk_tokenizer"),
        ("docling-project/docling-layout-heron", "docling_layout"),
        ("docling-project/docling-models", "docling_table_structure"),
    ),
    "default-retrieval": (
        ("nomic-ai/nomic-embed-text-v2-moe", "embedding"),
        ("nomic-ai/nomic-embed-text-v2-moe", "token_counter"),
        ("BAAI/bge-reranker-v2-m3", "reranker"),
    ),
    "classification": (
        ("facebook/bart-large-mnli", "zero_shot_classifier"),
    ),
}


class ModelSyncCapacityError(model_artifacts.ModelArtifactError):
    """Raised when a synchronization cannot satisfy its disk-space contract."""


@dataclass(frozen=True)
class BundlePlan:
    """One exact, independently published runtime model bundle."""

    model_id: str
    revision: str
    consumer: str
    identity_sha256: str
    target: Path
    status: str
    primary_download_bytes: int
    primary_runtime_bytes: int
    auxiliary_model_id: str | None
    auxiliary_download_bytes: int
    auxiliary_runtime_bytes: int
    transformed_bytes_removed: int

    @property
    def download_bytes(self) -> int:
        return self.primary_download_bytes + self.auxiliary_download_bytes

    @property
    def runtime_bytes(self) -> int:
        return self.primary_runtime_bytes + self.auxiliary_runtime_bytes

    @property
    def required_download_bytes(self) -> int:
        return self.download_bytes if self.status == "missing" else 0

    @property
    def additional_runtime_bytes(self) -> int:
        return self.runtime_bytes if self.status == "missing" else 0


@dataclass(frozen=True)
class BlockedConsumerPlan:
    """One selected lock consumer intentionally excluded from publication."""

    model_id: str
    consumer: str
    reason: str
    paths: tuple[str, ...]


@dataclass(frozen=True)
class ModelSyncPlan:
    """Immutable, content-free model synchronization plan."""

    schema_version: int
    lock_sha256: str
    plan_sha256: str
    task_names: tuple[str, ...]
    model_ids: tuple[str, ...]
    select_all: bool
    cache_root: Path
    bundles: tuple[BundlePlan, ...]
    blocked_consumers: tuple[BlockedConsumerPlan, ...]
    selected_download_bytes_if_uncached: int
    required_download_bytes: int
    selected_runtime_bytes: int
    already_present_runtime_bytes: int
    additional_runtime_bytes: int
    peak_additional_bytes: int
    disk_probe_path: Path
    margin_bytes: int
    required_free_bytes: int
    available_free_bytes: int
    sufficient_space: bool


def _disable_auxiliary_telemetry() -> None:
    """Disable Hub/Transformers usage telemetry before any network import."""
    os.environ["HF_HUB_DISABLE_TELEMETRY"] = "1"
    os.environ["DO_NOT_TRACK"] = "1"
    os.environ["HF_HUB_DISABLE_XET"] = "1"
    os.environ["HF_HUB_ENABLE_HF_TRANSFER"] = "0"


def _canonical_selection(
        model_ids: Sequence[str], *, task_names: Sequence[str],
        select_all: bool,
) -> tuple[
    tuple[model_artifacts.PinnedModelArtifact, ...],
    tuple[str, ...], tuple[str, ...], tuple[tuple[str, str], ...],
]:
    artifacts = model_artifacts.load_model_artifacts()
    if select_all and (model_ids or task_names):
        raise model_artifacts.ModelArtifactError(
            "--all cannot be combined with --model or --task")

    unknown_tasks = sorted(set(task_names) - set(TASK_PRESETS))
    if unknown_tasks:
        raise model_artifacts.ModelArtifactError(
            "unknown model synchronization task: " + ", ".join(unknown_tasks))
    canonical_tasks = tuple(
        task_name for task_name in TASK_PRESETS if task_name in set(task_names)
    )

    aliases: dict[str, str] = {}
    by_id = {artifact.model_id: artifact for artifact in artifacts}
    for artifact in artifacts:
        for name in (artifact.model_id, *artifact.aliases):
            aliases[name] = artifact.model_id
    requested_models: set[str] = set()
    for model_id in model_ids:
        canonical = aliases.get(model_id)
        if canonical is None:
            raise model_artifacts.ModelArtifactError(
                f"model is not in the reviewed lock: {model_id}")
        requested_models.add(canonical)
    canonical_models = tuple(
        artifact.model_id for artifact in artifacts
        if artifact.model_id in requested_models
    )

    requested_keys: set[tuple[str, str]] = set()
    if select_all:
        for artifact in artifacts:
            requested_keys.update(
                (artifact.model_id, consumer)
                for consumer in artifact.consumers
                if not consumer.endswith("_remote_code")
            )
    else:
        for task_name in canonical_tasks:
            requested_keys.update(TASK_PRESETS[task_name])
        for model_id in canonical_models:
            artifact = by_id[model_id]
            requested_keys.update(
                (model_id, consumer)
                for consumer in artifact.consumers
                if not consumer.endswith("_remote_code")
            )
        if not requested_keys:
            if canonical_models:
                raise model_artifacts.ModelArtifactError(
                    "selected models expose only dependency-only runtime code; "
                    "select the primary model or a task preset")
            raise model_artifacts.ModelArtifactError(
                "select at least one --task or --model, or pass --all")

    available_keys = {
        (artifact.model_id, consumer)
        for artifact in artifacts for consumer in artifact.consumers
    }
    invalid_keys = sorted(requested_keys - available_keys)
    if invalid_keys:
        rendered = ", ".join(f"{model}:{consumer}" for model, consumer in invalid_keys)
        raise model_artifacts.ModelArtifactError(
            f"task preset references an unavailable runtime consumer: {rendered}")
    ordered_keys = tuple(
        (artifact.model_id, consumer)
        for artifact in artifacts for consumer in artifact.consumers
        if (artifact.model_id, consumer) in requested_keys
    )
    return artifacts, canonical_tasks, canonical_models, ordered_keys


def _disk_usage_root(cache_root: Path) -> Path:
    candidate = cache_root
    while not candidate.exists():
        parent = candidate.parent
        if parent == candidate:
            raise OSError("model cache has no existing filesystem ancestor")
        candidate = parent
    if not candidate.is_dir():
        raise OSError("model cache path resolves beneath a non-directory")
    return candidate


def _consumer_partition(
        artifacts: Sequence[model_artifacts.PinnedModelArtifact],
        ordered_keys: Sequence[tuple[str, str]],
) -> tuple[tuple[tuple[str, str], ...], tuple[BlockedConsumerPlan, ...]]:
    by_id = {artifact.model_id: artifact for artifact in artifacts}
    safe: list[tuple[str, str]] = []
    blocked: list[BlockedConsumerPlan] = []
    for model_id, consumer in ordered_keys:
        artifact = by_id[model_id]
        classification = model_artifacts.classify_runtime_consumer(
            artifact, consumer)
        if classification == "dependency-only":
            continue
        if classification == "unsafe-pickle":
            blocked.append(BlockedConsumerPlan(
                model_id=model_id,
                consumer=consumer,
                reason="unsafe-pickle",
                paths=tuple(sorted(
                    file.path for file in artifact.files_for(consumer)
                    if file.path.casefold().endswith(".bin")
                )),
            ))
            continue
        safe.append((model_id, consumer))
    return tuple(safe), tuple(blocked)


def _available_free_bytes(
        probe_path: Path, *,
        disk_usage_fn: Callable[[Path], object] | None = None,
) -> int:
    usage_fn = shutil.disk_usage if disk_usage_fn is None else disk_usage_fn
    usage = usage_fn(probe_path)
    free = getattr(usage, "free", None)
    if isinstance(free, bool) or not isinstance(free, int) or free < 0:
        raise OSError("filesystem free-space result is invalid")
    return free


def _peak_additional_bytes(bundles: Sequence[BundlePlan]) -> int:
    persistent = 0
    peak = 0
    for bundle in bundles:
        if bundle.status != "missing":
            continue
        peak = max(
            peak,
            persistent + bundle.download_bytes + bundle.runtime_bytes,
        )
        persistent += bundle.runtime_bytes
    return max(peak, persistent)


def _space_margin(peak_additional_bytes: int) -> int:
    if peak_additional_bytes == 0:
        return 0
    five_percent_ceiling = (peak_additional_bytes + 19) // 20
    return max(_FREE_SPACE_RESERVE_MIN_BYTES, five_percent_ceiling)


def _bundle_digest_record(bundle: BundlePlan) -> dict[str, object]:
    return {
        "auxiliary_download_bytes": bundle.auxiliary_download_bytes,
        "auxiliary_model_id": bundle.auxiliary_model_id,
        "auxiliary_runtime_bytes": bundle.auxiliary_runtime_bytes,
        "consumer": bundle.consumer,
        "download_bytes": bundle.download_bytes,
        "identity_sha256": bundle.identity_sha256,
        "model_id": bundle.model_id,
        "primary_download_bytes": bundle.primary_download_bytes,
        "primary_runtime_bytes": bundle.primary_runtime_bytes,
        "revision": bundle.revision,
        "runtime_bytes": bundle.runtime_bytes,
        "status": bundle.status,
        "transformed_bytes_removed": bundle.transformed_bytes_removed,
    }


def _plan_digest_payload(
        *, lock_sha256: str, task_names: tuple[str, ...],
        model_ids: tuple[str, ...], select_all: bool,
        bundles: tuple[BundlePlan, ...],
        blocked_consumers: tuple[BlockedConsumerPlan, ...],
        peak_additional_bytes: int, margin_bytes: int,
) -> dict[str, object]:
    return {
        "schema": PLAN_SCHEMA,
        "schema_version": PLAN_SCHEMA_VERSION,
        "lock_sha256": lock_sha256,
        "selection": {
            "all": select_all,
            "models": list(model_ids),
            "tasks": list(task_names),
        },
        "bundles": [_bundle_digest_record(bundle) for bundle in bundles],
        "blocked_consumers": [
            {
                "consumer": blocked.consumer,
                "model_id": blocked.model_id,
                "paths": list(blocked.paths),
                "reason": blocked.reason,
            }
            for blocked in blocked_consumers
        ],
        "space_contract": {
            "margin_bytes": margin_bytes,
            "peak_additional_bytes": peak_additional_bytes,
            "required_free_bytes": peak_additional_bytes + margin_bytes,
        },
    }


def build_sync_plan(
        model_ids: Sequence[str] = (), *, task_names: Sequence[str] = (),
        select_all: bool = False, cache_root: Path | None = None,
        disk_usage_fn: Callable[[Path], object] | None = None,
) -> ModelSyncPlan:
    """Build a deterministic offline plan shared with the executor."""
    artifacts, tasks, models, ordered_keys = _canonical_selection(
        model_ids, task_names=task_names, select_all=select_all)
    safe_keys, blocked_tuple = _consumer_partition(artifacts, ordered_keys)
    resolved_cache = model_artifacts.model_artifact_cache_root(cache_root)
    bundles: list[BundlePlan] = []
    for model_id, consumer in safe_keys:
        spec = model_artifacts.runtime_bundle_spec(
            model_id, consumer, cache_root=resolved_cache)
        present = model_artifacts.verify_cached_runtime_bundle(spec)
        bundles.append(BundlePlan(
            model_id=spec.model_id,
            revision=spec.revision,
            consumer=spec.consumer,
            identity_sha256=spec.identity_sha256,
            target=spec.target,
            status="verified-present" if present else "missing",
            primary_download_bytes=spec.primary_download_bytes,
            primary_runtime_bytes=spec.primary_runtime_bytes,
            auxiliary_model_id=spec.auxiliary_model_id,
            auxiliary_download_bytes=spec.auxiliary_download_bytes,
            auxiliary_runtime_bytes=spec.auxiliary_runtime_bytes,
            transformed_bytes_removed=spec.transformed_bytes_removed,
        ))
    if not bundles:
        raise model_artifacts.ModelArtifactError(
            "selection has no independently syncable safe runtime bundles")

    bundle_tuple = tuple(bundles)
    selected_download = sum(bundle.download_bytes for bundle in bundle_tuple)
    required_download = sum(
        bundle.required_download_bytes for bundle in bundle_tuple)
    selected_runtime = sum(bundle.runtime_bytes for bundle in bundle_tuple)
    already_present = sum(
        bundle.runtime_bytes for bundle in bundle_tuple
        if bundle.status == "verified-present")
    additional_runtime = sum(
        bundle.additional_runtime_bytes for bundle in bundle_tuple)
    peak = _peak_additional_bytes(bundle_tuple)
    margin = _space_margin(peak)
    required_free = peak + margin
    probe_path = _disk_usage_root(resolved_cache)
    available_free = _available_free_bytes(
        probe_path, disk_usage_fn=disk_usage_fn)
    lock_sha256 = model_artifacts.model_artifact_lock_sha256()
    digest_payload = _plan_digest_payload(
        lock_sha256=lock_sha256,
        task_names=tasks,
        model_ids=models,
        select_all=select_all,
        bundles=bundle_tuple,
        blocked_consumers=blocked_tuple,
        peak_additional_bytes=peak,
        margin_bytes=margin,
    )
    plan_sha256 = hashlib.sha256(json.dumps(
        digest_payload, sort_keys=True, separators=(",", ":"),
    ).encode("utf-8")).hexdigest()
    return ModelSyncPlan(
        schema_version=PLAN_SCHEMA_VERSION,
        lock_sha256=lock_sha256,
        plan_sha256=plan_sha256,
        task_names=tasks,
        model_ids=models,
        select_all=select_all,
        cache_root=resolved_cache,
        bundles=bundle_tuple,
        blocked_consumers=blocked_tuple,
        selected_download_bytes_if_uncached=selected_download,
        required_download_bytes=required_download,
        selected_runtime_bytes=selected_runtime,
        already_present_runtime_bytes=already_present,
        additional_runtime_bytes=additional_runtime,
        peak_additional_bytes=peak,
        disk_probe_path=probe_path,
        margin_bytes=margin,
        required_free_bytes=required_free,
        available_free_bytes=available_free,
        sufficient_space=available_free >= required_free,
    )


def plan_payload(plan: ModelSyncPlan) -> dict[str, object]:
    """Return the stable schema-v1 JSON representation of *plan*."""
    return {
        "blocked_consumers": [
            {
                "consumer": blocked.consumer,
                "model_id": blocked.model_id,
                "paths": list(blocked.paths),
                "reason": blocked.reason,
            }
            for blocked in plan.blocked_consumers
        ],
        "bundles": [
            _bundle_digest_record(bundle) | {
                "additional_runtime_bytes": bundle.additional_runtime_bytes,
                "required_download_bytes": bundle.required_download_bytes,
                "target": str(bundle.target),
            }
            for bundle in plan.bundles
        ],
        "cache_root": str(plan.cache_root),
        "disk": {
            "free_bytes": plan.available_free_bytes,
            "margin_bytes": plan.margin_bytes,
            "probe_path": str(plan.disk_probe_path),
            "required_free_bytes": plan.required_free_bytes,
            "sufficient": plan.sufficient_space,
        },
        "lock_sha256": plan.lock_sha256,
        "offline": True,
        "plan_sha256": plan.plan_sha256,
        "schema": PLAN_SCHEMA,
        "schema_version": plan.schema_version,
        "scope": {
            "excludes": [
                "derived Docling composite caches",
                "RapidOCR assets verified from the installed package",
            ],
            "includes": "synchronizer-created reviewed Hub runtime bundles",
        },
        "selection": {
            "all": plan.select_all,
            "models": list(plan.model_ids),
            "tasks": list(plan.task_names),
        },
        "totals": {
            "additional_runtime_bytes": plan.additional_runtime_bytes,
            "already_present_runtime_bytes": plan.already_present_runtime_bytes,
            "blocked_consumer_count": len(plan.blocked_consumers),
            "missing_bundle_count": sum(
                bundle.status == "missing" for bundle in plan.bundles),
            "peak_additional_bytes": plan.peak_additional_bytes,
            "required_download_bytes": plan.required_download_bytes,
            "selected_bundle_count": len(plan.bundles),
            "selected_download_bytes_if_uncached": (
                plan.selected_download_bytes_if_uncached),
            "selected_runtime_bytes": plan.selected_runtime_bytes,
            "verified_present_bundle_count": sum(
                bundle.status == "verified-present" for bundle in plan.bundles),
        },
    }


def _assert_plan_bundle_matches_spec(
        bundle: BundlePlan, spec: model_artifacts.RuntimeBundleSpec,
) -> None:
    observed = (
        spec.model_id,
        spec.revision,
        spec.consumer,
        spec.identity_sha256,
        spec.target,
        spec.primary_download_bytes,
        spec.primary_runtime_bytes,
        spec.auxiliary_model_id,
        spec.auxiliary_download_bytes,
        spec.auxiliary_runtime_bytes,
        spec.transformed_bytes_removed,
    )
    expected = (
        bundle.model_id,
        bundle.revision,
        bundle.consumer,
        bundle.identity_sha256,
        bundle.target,
        bundle.primary_download_bytes,
        bundle.primary_runtime_bytes,
        bundle.auxiliary_model_id,
        bundle.auxiliary_download_bytes,
        bundle.auxiliary_runtime_bytes,
        bundle.transformed_bytes_removed,
    )
    if observed != expected:
        raise model_artifacts.ModelArtifactError(
            "model synchronization plan no longer matches the reviewed lock")


def _validate_plan_contract(plan: ModelSyncPlan) -> None:
    """Reject caller-constructed, truncated, duplicated, or stale plans."""
    if plan.schema_version != PLAN_SCHEMA_VERSION:
        raise model_artifacts.ModelArtifactError(
            "unsupported model synchronization plan schema")
    if model_artifacts.model_artifact_cache_root(plan.cache_root) != plan.cache_root:
        raise model_artifacts.ModelArtifactError(
            "model synchronization plan cache root is not canonical")
    digest_payload = _plan_digest_payload(
        lock_sha256=plan.lock_sha256,
        task_names=plan.task_names,
        model_ids=plan.model_ids,
        select_all=plan.select_all,
        bundles=plan.bundles,
        blocked_consumers=plan.blocked_consumers,
        peak_additional_bytes=plan.peak_additional_bytes,
        margin_bytes=plan.margin_bytes,
    )
    observed_digest = hashlib.sha256(json.dumps(
        digest_payload, sort_keys=True, separators=(",", ":"),
    ).encode("utf-8")).hexdigest()
    if observed_digest != plan.plan_sha256:
        raise model_artifacts.ModelArtifactError(
            "model synchronization plan digest is invalid")
    if any(
            bundle.status not in {"missing", "verified-present"}
            for bundle in plan.bundles):
        raise model_artifacts.ModelArtifactError(
            "model synchronization plan has an invalid bundle status")
    selected_download = sum(bundle.download_bytes for bundle in plan.bundles)
    required_download = sum(
        bundle.required_download_bytes for bundle in plan.bundles)
    selected_runtime = sum(bundle.runtime_bytes for bundle in plan.bundles)
    already_present = sum(
        bundle.runtime_bytes for bundle in plan.bundles
        if bundle.status == "verified-present")
    additional_runtime = sum(
        bundle.additional_runtime_bytes for bundle in plan.bundles)
    peak = _peak_additional_bytes(plan.bundles)
    margin = _space_margin(peak)
    expected_totals = (
        selected_download,
        required_download,
        selected_runtime,
        already_present,
        additional_runtime,
        peak,
        margin,
        peak + margin,
        plan.available_free_bytes >= peak + margin,
    )
    observed_totals = (
        plan.selected_download_bytes_if_uncached,
        plan.required_download_bytes,
        plan.selected_runtime_bytes,
        plan.already_present_runtime_bytes,
        plan.additional_runtime_bytes,
        plan.peak_additional_bytes,
        plan.margin_bytes,
        plan.required_free_bytes,
        plan.sufficient_space,
    )
    if expected_totals != observed_totals:
        raise model_artifacts.ModelArtifactError(
            "model synchronization plan totals are inconsistent")
    if (
        isinstance(plan.available_free_bytes, bool)
        or not isinstance(plan.available_free_bytes, int)
        or plan.available_free_bytes < 0
    ):
        raise model_artifacts.ModelArtifactError(
            "model synchronization plan free-space value is invalid")

    artifacts, tasks, models, ordered_keys = _canonical_selection(
        plan.model_ids,
        task_names=plan.task_names,
        select_all=plan.select_all,
    )
    safe_keys, blocked = _consumer_partition(artifacts, ordered_keys)
    planned_keys = tuple(
        (bundle.model_id, bundle.consumer) for bundle in plan.bundles)
    if (
        tasks != plan.task_names
        or models != plan.model_ids
        or safe_keys != planned_keys
        or blocked != plan.blocked_consumers
    ):
        raise model_artifacts.ModelArtifactError(
            "model synchronization plan selection is not canonical")


def _execution_preflight(
        plan: ModelSyncPlan, *,
        disk_usage_fn: Callable[[Path], object] | None,
) -> tuple[tuple[model_artifacts.RuntimeBundleSpec, ...], tuple[bool, ...]]:
    if not isinstance(plan, ModelSyncPlan):
        raise TypeError("model synchronization requires a validated plan")
    _validate_plan_contract(plan)
    if model_artifacts.model_artifact_lock_sha256() != plan.lock_sha256:
        raise model_artifacts.ModelArtifactError(
            "model synchronization plan lock digest is stale")
    specs: list[model_artifacts.RuntimeBundleSpec] = []
    missing: list[bool] = []
    refreshed: list[BundlePlan] = []
    for bundle in plan.bundles:
        spec = model_artifacts.runtime_bundle_spec(
            bundle.model_id, bundle.consumer, cache_root=plan.cache_root)
        _assert_plan_bundle_matches_spec(bundle, spec)
        present = model_artifacts.verify_cached_runtime_bundle(spec)
        specs.append(spec)
        missing.append(not present)
        refreshed.append(replace(
            bundle,
            status="missing" if not present else "verified-present",
        ))
    peak = _peak_additional_bytes(refreshed)
    required = peak + _space_margin(peak)
    available = _available_free_bytes(
        _disk_usage_root(plan.cache_root), disk_usage_fn=disk_usage_fn)
    if available < required:
        raise ModelSyncCapacityError(
            "insufficient free space for reviewed model synchronization: "
            f"required {required} bytes; available {available} bytes")
    return tuple(specs), tuple(missing)


def sync_models(
        plan_or_model_ids: ModelSyncPlan | Sequence[str], *,
        cache_root: Path | None = None,
        security_policy: release_security.ReleaseSecurityPolicy | None = None,
        disk_usage_fn: Callable[[Path], object] | None = None,
) -> list[Path]:
    """Synchronize exactly one plan, retaining the legacy model-list API."""
    if isinstance(plan_or_model_ids, ModelSyncPlan):
        if cache_root is not None:
            raise TypeError("cache_root is already bound by the synchronization plan")
        plan = plan_or_model_ids
    else:
        if isinstance(plan_or_model_ids, (str, bytes)):
            raise TypeError("model IDs must be a sequence of strings")
        model_ids = tuple(plan_or_model_ids)
        plan = build_sync_plan(
            model_ids,
            select_all=not model_ids,
            cache_root=cache_root,
            disk_usage_fn=disk_usage_fn,
        )

    specs, initially_missing = _execution_preflight(
        plan, disk_usage_fn=disk_usage_fn)
    _disable_auxiliary_telemetry()
    policy = security_policy or release_security.ReleaseSecurityPolicy(
        model_download_policy="allow-reviewed-sync")
    endpoint = model_artifacts.HUGGINGFACE_HUB_OFFICIAL_ENDPOINT
    if policy.trust_environment_network:
        endpoint = os.environ.get("HF_ENDPOINT", endpoint) or endpoint
    for blocked in plan.blocked_consumers:
        print(
            f"SKIP {blocked.model_id}:{blocked.consumer} ({blocked.reason})",
            file=sys.stderr,
        )

    synchronized: list[Path] = []
    for index, spec in enumerate(specs):
        if initially_missing[index]:
            remaining = [
                BundlePlan(
                    model_id=later.model_id,
                    revision=later.revision,
                    consumer=later.consumer,
                    identity_sha256=later.identity_sha256,
                    target=later.target,
                    status="missing",
                    primary_download_bytes=later.primary_download_bytes,
                    primary_runtime_bytes=later.primary_runtime_bytes,
                    auxiliary_model_id=later.auxiliary_model_id,
                    auxiliary_download_bytes=later.auxiliary_download_bytes,
                    auxiliary_runtime_bytes=later.auxiliary_runtime_bytes,
                    transformed_bytes_removed=later.transformed_bytes_removed,
                )
                for later, was_missing in zip(
                    specs[index:], initially_missing[index:], strict=True)
                if was_missing and not later.target.exists()
            ]
            peak = _peak_additional_bytes(remaining)
            required = peak + _space_margin(peak)
            available = _available_free_bytes(
                _disk_usage_root(plan.cache_root),
                disk_usage_fn=disk_usage_fn,
            )
            if available < required:
                raise ModelSyncCapacityError(
                    "insufficient free space before model bundle publication: "
                    f"required {required} bytes; available {available} bytes")
            path = model_artifacts.verified_model_directory(
                spec.model_id,
                spec.consumer,
                cache_root=plan.cache_root,
                allow_download=True,
                authorize_download_fn=lambda: (
                    release_security.require_model_download(
                        policy, feature="reviewed model synchronization")),
                download_endpoint=endpoint,
                trust_environment_network=policy.trust_environment_network,
            )
        else:
            if not model_artifacts.verify_cached_runtime_bundle(spec):
                raise model_artifacts.ModelArtifactError(
                    "verified model cache changed after synchronization preflight")
            path = spec.target
        synchronized.append(path)
        print(f"SYNCED {spec.model_id}:{spec.consumer}")
    return synchronized


def _human_bytes(value: int) -> str:
    if value < 1024:
        return f"{value} B"
    units = ("KiB", "MiB", "GiB", "TiB")
    amount = float(value)
    for unit in units:
        amount /= 1024
        if amount < 1024 or unit == units[-1]:
            return f"{amount:.2f} {unit}"
    raise AssertionError("unreachable")


def _print_plan(plan: ModelSyncPlan) -> None:
    print(f"Model lock SHA-256: {plan.lock_sha256}")
    print(f"Plan SHA-256: {plan.plan_sha256}")
    print(f"Cache root: {plan.cache_root}")
    print("Runtime bundles:")
    for bundle in plan.bundles:
        dependency = (
            f", auxiliary {_human_bytes(bundle.auxiliary_download_bytes)}"
            if bundle.auxiliary_model_id is not None else ""
        )
        print(
            f"  {bundle.model_id}:{bundle.consumer} [{bundle.status}] — "
            f"download {_human_bytes(bundle.required_download_bytes)}, runtime "
            f"{_human_bytes(bundle.runtime_bytes)}{dependency}")
    if plan.blocked_consumers:
        print("Blocked consumers:")
        for blocked in plan.blocked_consumers:
            print(
                f"  {blocked.model_id}:{blocked.consumer} — {blocked.reason}")
    print(
        "Uncached selected download: "
        f"{plan.selected_download_bytes_if_uncached} "
        f"({_human_bytes(plan.selected_download_bytes_if_uncached)})")
    print(
        f"Required download: {plan.required_download_bytes} "
        f"({_human_bytes(plan.required_download_bytes)})")
    print(
        f"Selected runtime: {plan.selected_runtime_bytes} "
        f"({_human_bytes(plan.selected_runtime_bytes)})")
    print(
        f"Already present runtime: {plan.already_present_runtime_bytes} "
        f"({_human_bytes(plan.already_present_runtime_bytes)})")
    print(
        f"Additional runtime: {plan.additional_runtime_bytes} "
        f"({_human_bytes(plan.additional_runtime_bytes)})")
    print(
        f"Peak additional space: {plan.peak_additional_bytes} "
        f"({_human_bytes(plan.peak_additional_bytes)})")
    print(
        f"Free-space margin: {plan.margin_bytes} "
        f"({_human_bytes(plan.margin_bytes)})")
    print(
        f"Required free space with margin: {plan.required_free_bytes} "
        f"({_human_bytes(plan.required_free_bytes)})")
    print(
        f"Available free space: {plan.available_free_bytes} "
        f"({_human_bytes(plan.available_free_bytes)})")
    print(f"Sufficient free space: {'yes' if plan.sufficient_space else 'no'}")


def _parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        description=("Plan or synchronize reviewed public model bytes before "
                     "processing private documents"),
        allow_abbrev=False,
    )
    parser.add_argument(
        "--task", action="append", default=[], choices=tuple(TASK_PRESETS),
        help="Reviewed task preset (repeatable)")
    parser.add_argument(
        "--model", action="append", default=[],
        help="Reviewed model ID or alias (repeatable)")
    parser.add_argument(
        "--all", action="store_true",
        help="Explicitly select every independently syncable safe bundle")
    parser.add_argument("--cache-root", type=Path)
    parser.add_argument(
        "--security-profile", choices=["release", "development"],
        default="release")
    parser.add_argument(
        "--trust-environment-network", action="store_true",
        help="Allow reviewed proxy/custom-CA environment overrides")
    output = parser.add_mutually_exclusive_group()
    output.add_argument(
        "--plan", action="store_true",
        help="Print the offline byte/space plan without downloading")
    output.add_argument(
        "--list", action="store_true",
        help="List safe selected runtime bundles without downloading")
    parser.add_argument(
        "--json", action="store_true",
        help="Emit the offline plan as deterministic schema-v1 JSON")
    return parser


def main(argv: list[str] | None = None) -> int:
    parser = _parser()
    args = parser.parse_args(argv)
    if args.all and (args.model or args.task):
        parser.error("--all cannot be combined with --model or --task")
    if args.json and args.list:
        parser.error("--json cannot be combined with --list")
    offline = args.plan or args.json or args.list
    select_all = args.all or (offline and not args.model and not args.task)
    if not select_all and not args.model and not args.task:
        parser.error("select at least one --task or --model, or pass --all")
    try:
        plan = build_sync_plan(
            args.model,
            task_names=args.task,
            select_all=select_all,
            cache_root=args.cache_root,
        )
        if args.list:
            for bundle in plan.bundles:
                print(f"{bundle.model_id}:{bundle.consumer}")
            for blocked in plan.blocked_consumers:
                print(
                    f"BLOCKED {blocked.model_id}:{blocked.consumer} "
                    f"({blocked.reason})",
                    file=sys.stderr,
                )
            return 0
        if args.plan or args.json:
            if args.json:
                sys.stdout.write(json.dumps(
                    plan_payload(plan), sort_keys=True, indent=2) + "\n")
            else:
                _print_plan(plan)
            return 0 if plan.sufficient_space else 1
        policy = release_security.ReleaseSecurityPolicy(
            profile=args.security_profile,
            model_download_policy="allow-reviewed-sync",
            trust_environment_network=args.trust_environment_network,
        )
        synchronized = sync_models(plan, security_policy=policy)
    except ModelSyncCapacityError as exc:
        print(f"ERROR: {exc}", file=sys.stderr)
        return 1
    except (OSError, TypeError, ValueError) as exc:
        parser.error(str(exc))
    print(f"Synchronized {len(synchronized)} verified runtime bundle(s).")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
