"""Fixed-input, private publication of unconfirmed column-layout suggestions.

The original source is hashed only: no PDF parser, image or OCR runtime is
loaded. Suggestions contain geometry and indexes, never recognized text.
"""

from __future__ import annotations

import json
import os
from pathlib import Path
import stat

from evaluation_inputs import _read_snapshot, _strict_json_bytes
from ocr_column_suggestions import build_column_suggestions, validate_column_suggestions
from ocr_recovery import _publish_new_report
from resource_lease import PathLease
import storage_policy


MAX_SOURCE_BYTES = 256 * 1024 * 1024
MAX_RECOVERY_BYTES = 64 * 1024 * 1024
MAX_REPORT_BYTES = 1024 * 1024


def _distinct(paths: list[Path]) -> None:
    for path in paths:
        storage_policy.assert_no_link_components(path)
        try:
            info = path.lstat()
        except FileNotFoundError:
            continue
        if not stat.S_ISREG(info.st_mode) or info.st_nlink != 1:
            raise ValueError("column suggestion artifacts must be single-linked regular files")
    for index, path in enumerate(paths):
        for other in paths[index + 1:]:
            if path.resolve() == other.resolve() or (
                    path.exists() and other.exists() and os.path.samefile(path, other)):
                raise ValueError("column suggestion input and output paths must be distinct")


def _snapshot(path: Path, limit: int) -> tuple[bytes, str, tuple[int, int]]:
    storage_policy.assert_no_link_components(path)
    before = path.lstat()
    if (not stat.S_ISREG(before.st_mode) or before.st_nlink != 1
            or not 1 <= before.st_size <= limit):
        raise ValueError("column suggestion input must be a bounded single-linked regular file")
    raw, digest = _read_snapshot(path, label="column suggestion input", max_bytes=limit)
    storage_policy.assert_no_link_components(path)
    after = path.lstat()
    identity = (before.st_dev, before.st_ino)
    if (not stat.S_ISREG(after.st_mode) or after.st_nlink != 1
            or identity != (after.st_dev, after.st_ino)):
        raise RuntimeError("column suggestion input identity changed")
    return raw, digest, identity


def suggest_column_files(source_path: Path, recovery_path: Path, output_path: Path, *,
                         requested_pages: list[int]) -> dict:
    """Bind a fixed source and recovery snapshot, then publish a new private report.

    The existing output parent generation and both input generations are
    rechecked inside the no-clobber commit callback. A late cancellation or
    cleanup failure can leave the complete output present. Path checks are not
    a component-pinned OS no-follow guarantee against a hostile local actor.
    """
    if (type(requested_pages) is not list or not 1 <= len(requested_pages) <= 20
            or any(type(page) is not int or not 1 <= page <= 5000 for page in requested_pages)
            or len(set(requested_pages)) != len(requested_pages)):
        raise ValueError("column suggestions require 1 to 20 distinct one-based pages")
    requested = tuple(sorted(requested_pages))
    paths = [Path(path).absolute() for path in (source_path, recovery_path, output_path)]
    source_path, recovery_path, output_path = paths
    _distinct(paths)
    parent_identity = storage_policy._parent_identity(output_path.parent)
    specs = [(source_path, MAX_SOURCE_BYTES), (recovery_path, MAX_RECOVERY_BYTES)]

    def check_parent() -> None:
        storage_policy.assert_no_link_components(output_path.parent)
        if storage_policy._parent_identity(output_path.parent) != parent_identity:
            raise RuntimeError("column suggestion output directory changed")

    with PathLease(output_path, backend="ocr-column-suggestions", collection_name="suggestions",
                   operation="suggest", timeout=0, resource_description="OCR column suggestions"):
        check_parent()
        _distinct(paths)
        if output_path.exists():
            raise FileExistsError("column suggestion output already exists")
        # Source bytes are never interpreted and are discarded immediately.
        source_raw, source_digest, source_identity = _snapshot(source_path, MAX_SOURCE_BYTES)
        del source_raw
        recovery_raw, recovery_digest, recovery_identity = _snapshot(recovery_path, MAX_RECOVERY_BYTES)
        try:
            recovery = _strict_json_bytes(
                recovery_raw, label="column suggestion recovery", max_bytes=MAX_RECOVERY_BYTES)
        except ValueError:
            raise ValueError("column suggestion recovery must be bounded strict UTF-8 JSON") from None
        del recovery_raw
        report = build_column_suggestions(
            recovery, recovery_sha256=recovery_digest, requested_pages=list(requested))
        report = validate_column_suggestions(report, recovery=recovery, recovery_sha256=recovery_digest)
        if report["coverage"]["requested_pages"] != list(requested):
            raise ValueError("column suggestion report differs from requested pages")
        if report["source_sha256"] != source_digest:
            raise ValueError("column suggestion source differs from bound recovery")
        raw = (json.dumps(report, ensure_ascii=False, sort_keys=True, allow_nan=False, indent=2) + "\n").encode("utf-8")
        if len(raw) > MAX_REPORT_BYTES:
            raise ValueError("column suggestion report exceeds its byte budget")
        snapshots = [(source_digest, source_identity), (recovery_digest, recovery_identity)]

        def commit(temporary: Path, destination: Path) -> None:
            check_parent()
            _distinct(paths)
            for (path, limit), (digest, identity) in zip(specs, snapshots):
                _, current, current_identity = _snapshot(path, limit)
                if current != digest or current_identity != identity:
                    raise RuntimeError("column suggestion input changed before publication")
            staged, _, _ = _snapshot(Path(temporary), MAX_REPORT_BYTES)
            if staged != raw:
                raise RuntimeError("column suggestion staged report changed before publication")
            check_parent()
            _publish_new_report(temporary, destination)

        check_parent()
        storage_policy.atomic_write_private(
            output_path, lambda handle: handle.write(raw), text=False, replace_fn=commit)
    return report
