"""Private create-only OCR benchmark and runtime-inspection file adapters."""

from __future__ import annotations

import os
import stat
from pathlib import Path

from evaluation_inputs import _read_snapshot, _strict_json_bytes
from ocr_benchmark import evaluate_structure
from ocr_experiment_runtime import DEFAULT_OCR_PACKAGES, MAX_LOCK_BYTES, capture_runtime_manifest
from ocr_recovery import _publish_new_report
from resource_lease import PathLease
import storage_policy


MAX_STRUCTURE_BYTES = 16 * 1024 * 1024
MAX_OBSERVATION_BYTES = 64 * 1024


def _input_snapshot(path: Path, limit: int) -> tuple[bytes, str]:
    before = path.lstat()
    if not stat.S_ISREG(before.st_mode) or before.st_nlink != 1:
        raise ValueError("benchmark input must be a single-linked regular file")
    result = _read_snapshot(path, label="benchmark input", max_bytes=limit)
    after = path.lstat()
    if (not stat.S_ISREG(after.st_mode) or after.st_nlink != 1
            or (before.st_dev, before.st_ino) != (after.st_dev, after.st_ino)):
        raise RuntimeError("benchmark input identity changed")
    return result


def _publish(specs: list[tuple[Path, str, int]], output_path: Path, builder) -> dict:
    output_path = Path(output_path).absolute()
    specs = [(Path(path).absolute(), key, limit) for path, key, limit in specs]
    paths = [path for path, _, _ in specs] + [output_path]
    for path in paths:
        storage_policy.assert_no_link_components(path)
    for index, path in enumerate(paths):
        for other in paths[index + 1:]:
            if path.resolve() == other.resolve() or (path.exists() and other.exists() and os.path.samefile(path, other)):
                raise ValueError("benchmark input and output paths must be distinct")
    with PathLease(output_path, backend="ocr-benchmark", collection_name="report", operation="evaluate",
                   timeout=0, resource_description="OCR benchmark report"):
        if output_path.exists():
            raise FileExistsError("benchmark output already exists")
        snapshots = [_input_snapshot(path, limit) for path, _, limit in specs]
        report = builder([raw for raw, _ in snapshots])
        report["inputs"] = {key: digest for (_, key, _), (_, digest) in zip(specs, snapshots)}

        def commit(temporary, destination):
            for (path, _, limit), (_, digest) in zip(specs, snapshots):
                _, current = _input_snapshot(path, limit)
                if digest != current:
                    raise RuntimeError("benchmark input changed before publication")
            _publish_new_report(temporary, destination)

        storage_policy.atomic_write_private_json(output_path, report, indent=2, replace_fn=commit)
    return report


def evaluate_structure_files(cohort_path: Path, prediction_path: Path, output_path: Path) -> dict:
    """Reject incomplete cohorts and publish metrics only, never annotation text."""
    def build(raw):
        payloads = [_strict_json_bytes(item, label="structural benchmark", max_bytes=MAX_STRUCTURE_BYTES)
                    for item in raw]
        return evaluate_structure(*payloads)

    return _publish([(cohort_path, "cohort_file_sha256", MAX_STRUCTURE_BYTES),
                     (prediction_path, "predictions_file_sha256", MAX_STRUCTURE_BYTES)], output_path, build)


def inspect_runtime_file(lock_path: Path, output_path: Path, *, effective_path: Path | None = None,
                         packages=DEFAULT_OCR_PACKAGES, require_version_match: bool = False) -> dict:
    """Capture metadata, without importing an engine or asserting wheel proof."""
    specs = [(lock_path, "lock_file_sha256", MAX_LOCK_BYTES)]
    if effective_path is not None:
        specs.append((effective_path, "effective_observation_file_sha256", MAX_OBSERVATION_BYTES))

    def build(raw):
        observation = (_strict_json_bytes(raw[1], label="effective runtime observation", max_bytes=MAX_OBSERVATION_BYTES)
                       if effective_path is not None else None)
        report = capture_runtime_manifest(lock_path, packages=packages, effective_runtime=observation,
                                          require_version_match=require_version_match)
        # Capture reopens the lock to inspect it. Bind that second snapshot to
        # this adapter's first snapshot, including replacement during capture.
        import hashlib
        if report["lock_sha256"] != hashlib.sha256(raw[0]).hexdigest():
            raise RuntimeError("runtime dependency lock changed between snapshots")
        return report

    return _publish(specs, output_path, build)
