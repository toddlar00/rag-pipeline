"""Private create-only publication of explicitly reviewed layout assignments."""

from __future__ import annotations

import os
from pathlib import Path
import stat

from evaluation_inputs import _read_snapshot
from ocr_layout_assignment import build_assignment_review
from ocr_recovery import _publish_new_report
from ocr_recovery_comparison import _load
from resource_lease import PathLease
import storage_policy


MAX_SOURCE_BYTES = 256 * 1024 * 1024
MAX_RECOVERY_BYTES = 64 * 1024 * 1024
MAX_PROPOSALS_BYTES = 32 * 1024 * 1024
MAX_PLAN_BYTES = 8 * 1024 * 1024


def _distinct(paths: list[Path]) -> None:
    for path in paths:
        storage_policy.assert_no_link_components(path)
        if path.exists():
            info = path.lstat()
            if not stat.S_ISREG(info.st_mode) or info.st_nlink != 1:
                raise ValueError("layout assignment artifacts must be single-linked regular files")
    for index, path in enumerate(paths):
        for other in paths[index + 1:]:
            if path.resolve() == other.resolve() or (path.exists() and other.exists() and os.path.samefile(path, other)):
                raise ValueError("layout assignment input and output paths must be distinct")


def _snapshot(path: Path, limit: int, *, json_input: bool) -> tuple[object, str]:
    storage_policy.assert_no_link_components(path)
    before = path.lstat()
    if not stat.S_ISREG(before.st_mode) or before.st_nlink != 1:
        raise ValueError("layout assignment input must be a single-linked regular file")
    value = (_load(path, label="layout assignment input", limit=limit) if json_input else
             _read_snapshot(path, label="layout assignment source", max_bytes=limit))
    after = path.lstat()
    if (not stat.S_ISREG(after.st_mode) or after.st_nlink != 1
            or (before.st_dev, before.st_ino) != (after.st_dev, after.st_ino)):
        raise RuntimeError("layout assignment input identity changed")
    return value


def review_assignment_files(source_path: Path, recovery_path: Path, proposals_path: Path,
                            plan_path: Path, output_path: Path, *, confirmed_pages: object) -> dict:
    """Hash original bytes and fixed saved inputs at load and immediately before commit.

    No PDF parser, OCR engine, model, or canonical artifact is opened for writes.
    A late publication/cleanup failure or cancellation can leave the complete
    output present; callers must inspect it and choose a new path for a retry.
    """
    paths = [Path(p).absolute() for p in (source_path, recovery_path, proposals_path, plan_path, output_path)]
    source_path, recovery_path, proposals_path, plan_path, output_path = paths
    specs = [(source_path, MAX_SOURCE_BYTES, False), (recovery_path, MAX_RECOVERY_BYTES, True),
             (proposals_path, MAX_PROPOSALS_BYTES, True), (plan_path, MAX_PLAN_BYTES, True)]
    _distinct(paths)
    with PathLease(output_path, backend="ocr-layout-assignment", collection_name="review", operation="review",
                   timeout=0, resource_description="OCR layout assignment review"):
        if output_path.exists():
            raise FileExistsError("layout assignment output already exists")
        snapshots = [_snapshot(path, limit, json_input=is_json) for path, limit, is_json in specs]
        (_, source_digest), (recovery, recovery_digest), (proposals, proposals_digest), (plan, plan_digest) = snapshots
        report = build_assignment_review(plan, recovery=recovery, recovery_sha256=recovery_digest,
            proposals=proposals, proposals_sha256=proposals_digest, confirmed_pages=confirmed_pages,
            plan_sha256=plan_digest)
        if report["source_sha256"] != source_digest:
            raise ValueError("layout assignment source differs from bound recovery")

        def commit(temporary, destination):
            _distinct(paths)
            for (path, limit, is_json), (_, digest) in zip(specs, snapshots):
                _, current = _snapshot(path, limit, json_input=is_json)
                if current != digest:
                    raise RuntimeError("layout assignment input changed before publication")
            _publish_new_report(temporary, destination)

        storage_policy.atomic_write_private_json(output_path, report, indent=2, replace_fn=commit)
    return report
