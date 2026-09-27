"""Private, create-only publication for explicit OCR line-order reviews.

Only bounded saved JSON artifacts are read. This layer never opens a PDF,
loads an OCR model, or mutates the recovery report or canonical extraction.
"""

from __future__ import annotations

import os
from pathlib import Path

from evaluation_inputs import _read_snapshot
from ocr_layout import build_layout_review
from ocr_recovery_comparison import _load
import ocr_recovery
from resource_lease import PathLease
import storage_policy


MAX_RECOVERY_BYTES = 64 * 1024 * 1024
MAX_PLAN_BYTES = 1024 * 1024
MAX_REFERENCE_BYTES = 16 * 1024 * 1024


def review_layout_files(
        recovery_path: Path, plan_path: Path, output_path: Path, *,
        reference_path: Path | None = None) -> dict:
    """Snapshot every input, bind the exact recovery digest, then publish once.

    The output contains original and proposed OCR text and is always private.
    Input digests are rechecked under the output lease before the no-clobber
    commit. Cancellation propagates; a late failure may follow publication.
    """
    recovery_path, plan_path, output_path = (
        Path(path).absolute() for path in (recovery_path, plan_path, output_path))
    reference_path = Path(reference_path).absolute() if reference_path is not None else None
    paths = [recovery_path, plan_path, output_path]
    if reference_path is not None:
        paths.append(reference_path)
    for path in paths:
        storage_policy.assert_no_link_components(path)
    for index, path in enumerate(paths):
        for other in paths[index + 1:]:
            if path.resolve() == other.resolve() or (
                path.exists() and other.exists() and os.path.samefile(path, other)):
                raise ValueError("OCR layout inputs and output must be distinct")

    with PathLease(
        output_path, backend="ocr-layout", collection_name="report", operation="reorder",
        timeout=0, resource_description="OCR layout review report",
    ):
        if output_path.exists():
            raise FileExistsError("OCR layout output exists; choose a new path")
        specs = [
            (recovery_path, "recovery_sha256", "OCR recovery report", MAX_RECOVERY_BYTES),
            (plan_path, "plan_sha256", "OCR layout plan", MAX_PLAN_BYTES),
        ]
        if reference_path is not None:
            specs.append((reference_path, "references_sha256", "OCR references", MAX_REFERENCE_BYTES))
        snapshots = [_load(path, label=label, limit=limit) for path, _, label, limit in specs]
        recovery, recovery_digest = snapshots[0]
        result = build_layout_review(
            recovery, snapshots[1][0], recovery_sha256=recovery_digest,
            references=snapshots[2][0] if reference_path is not None else None)
        result["inputs"] = {
            "recovery_sha256": recovery_digest, "plan_sha256": snapshots[1][1],
            "references_sha256": snapshots[2][1] if reference_path is not None else None,
        }
        for (path, _, _, limit), (_, digest) in zip(specs, snapshots):
            try:
                _, current = _read_snapshot(path, label="OCR layout input", max_bytes=limit)
            except ValueError:
                raise ValueError("OCR layout input became an invalid bounded artifact") from None
            if current != digest:
                raise RuntimeError("OCR layout input changed before publication")
        storage_policy.atomic_write_private_json(
            output_path, result, indent=2, replace_fn=ocr_recovery._publish_new_report)
    return result
