"""Bounded fixed-input snapshots and private omission-diagnostic publication."""

from __future__ import annotations

import os
from pathlib import Path
import stat

from evaluation_inputs import _strict_json_bytes
from ocr_omission import build_omission_diagnostics
from ocr_recovery import _publish_new_report
from resource_lease import PathLease
import storage_policy


MAX_SOURCE_BYTES = 256 * 1024 * 1024
MAX_RECOVERY_BYTES = 64 * 1024 * 1024
MAX_PROPOSALS_BYTES = 32 * 1024 * 1024
_CHUNK_BYTES = 1024 * 1024


def _distinct(paths: list[Path]) -> None:
    for path in paths:
        storage_policy.assert_no_link_components(path)
        if path.exists():
            info = path.lstat()
            if not stat.S_ISREG(info.st_mode) or info.st_nlink != 1:
                raise ValueError("omission artifacts must be single-linked regular files")
    for index, path in enumerate(paths):
        for other in paths[index + 1:]:
            if path.resolve() == other.resolve() or (
                    path.exists() and other.exists() and os.path.samefile(path, other)):
                raise ValueError("omission input and output paths must be distinct")


def _identity(info) -> tuple:
    result = (info.st_dev, info.st_ino, info.st_size, info.st_mtime_ns)
    # Windows creation time can settle on reopen. Double hashing also verifies
    # content when a writer restores mtime or metadata-only churn occurs.
    return result if os.name == "nt" else result + (info.st_ctime_ns,)


def _snapshot(path: Path, limit: int, *, json_input: bool) -> tuple[object, str]:
    raw, digest = storage_policy._bounded_snapshot(
        path, limit, min_size=1, retain=json_input, chunk_size=lambda: _CHUNK_BYTES,
        identity=lambda info: _identity(info), final_before_first=True,
        invalid="omission input must be a bounded single-linked regular file",
        opened_changed="OCR omission input identity changed",
        oversized="OCR omission input exceeds streaming byte budget",
        changed="OCR omission input changed during reading")
    try:
        value = _strict_json_bytes(raw, label="OCR omission input", max_bytes=limit) if json_input else None
    except ValueError:
        raise ValueError("OCR omission input must be bounded strict UTF-8 JSON") from None
    return value, digest


def inspect_omission_files(source_path: Path, recovery_path: Path, proposals_path: Path,
                           output_path: Path) -> dict:
    """Hash only the original source; inspect saved geometry without PDF/OCR work.

    All inputs are fixed, bounded, distinct, single-linked regular files. Every
    input is rechecked inside the final create-only publication callback. A
    late cancellation or cleanup failure can leave the complete output present.
    Link checks are not a component-pinned OS no-follow security guarantee.
    """
    paths = [Path(p).absolute() for p in (source_path, recovery_path, proposals_path, output_path)]
    source_path, recovery_path, proposals_path, output_path = paths
    specs = [(source_path, MAX_SOURCE_BYTES, False), (recovery_path, MAX_RECOVERY_BYTES, True),
             (proposals_path, MAX_PROPOSALS_BYTES, True)]
    _distinct(paths)
    with PathLease(output_path, backend="ocr-omission", collection_name="diagnostics", operation="inspect",
                   timeout=0, resource_description="OCR omission diagnostics"):
        if output_path.exists():
            raise FileExistsError("OCR omission output already exists")
        snapshots = [_snapshot(path, limit, json_input=is_json) for path, limit, is_json in specs]
        (_, source_digest), (recovery, recovery_digest), (proposals, proposals_digest) = snapshots
        report = build_omission_diagnostics(recovery, proposals, recovery_sha256=recovery_digest,
                                            proposals_sha256=proposals_digest)
        if report["source_sha256"] != source_digest:
            raise ValueError("OCR omission source differs from bound recovery")

        def commit(temporary, destination):
            _distinct(paths)
            for (path, limit, is_json), (_, digest) in zip(specs, snapshots):
                _, current = _snapshot(path, limit, json_input=is_json)
                if current != digest:
                    raise RuntimeError("OCR omission input changed before publication")
            _publish_new_report(temporary, destination)

        storage_policy.atomic_write_private_json(output_path, report, indent=2, replace_fn=commit)
    return report
