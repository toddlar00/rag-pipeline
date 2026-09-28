"""Bounded input generations and private create-only context-check publication."""

from __future__ import annotations

import hashlib
import os
from pathlib import Path
import stat

from evaluation_inputs import _read_snapshot, _strict_json_bytes
from ocr_context_evaluation import evaluate_context_checks
import ocr_recovery
from ocr_recovery_comparison import validate_recovery_report
from resource_lease import PathLease
import storage_policy


MAX_PDF_BYTES = 512 * 1024 * 1024
MAX_RECOVERY_BYTES = 64 * 1024 * 1024
MAX_REFERENCE_BYTES = 8 * 1024 * 1024
MAX_CORRESPONDENCE_BYTES = 1024 * 1024
_CHUNK_BYTES = 1024 * 1024


def _identity(value) -> tuple:
    identity = (value.st_dev, value.st_ino, value.st_size, value.st_mtime_ns)
    # Windows ctime is creation time, not a change counter, and can settle
    # after reopening. Same-handle double hashing verifies content on all OSes.
    return identity if os.name == "nt" else identity + (value.st_ctime_ns,)


def _source_digest(path: Path) -> str:
    """Hash two bounded passes through one regular generation; retain no PDF bytes."""
    storage_policy.assert_no_link_components(path)
    observed = path.stat()
    if not stat.S_ISREG(observed.st_mode) or not 1 <= observed.st_size <= MAX_PDF_BYTES:
        raise ValueError("OCR context source exceeds regular-file bounds")
    with path.open("rb") as handle:
        before = os.fstat(handle.fileno())
        if not stat.S_ISREG(before.st_mode) or _identity(before) != _identity(observed):
            raise RuntimeError("OCR context source generation changed")
        digests = []
        for _ in range(2):
            handle.seek(0)
            digest, count = hashlib.sha256(), 0
            while True:
                block = handle.read(min(_CHUNK_BYTES, MAX_PDF_BYTES + 1 - count))
                if not block:
                    break
                count += len(block)
                if count > MAX_PDF_BYTES:
                    raise ValueError("OCR context source exceeds streaming byte budget")
                digest.update(block)
            if count != before.st_size or _identity(os.fstat(handle.fileno())) != _identity(before):
                raise RuntimeError("OCR context source changed during hashing")
            digests.append(digest.hexdigest())
    storage_policy.assert_no_link_components(path)
    if digests[0] != digests[1] or _identity(path.stat()) != _identity(before):
        raise RuntimeError("OCR context source changed during hashing")
    return digests[0]


def evaluate_context_files(pdf_path: Path, recovery_path: Path, reference_path: Path,
                           correspondence_path: Path, output_path: Path) -> dict:
    """Check saved OCR against an exact PDF hash; never parse a PDF or execute OCR.

    Inputs are rechecked inside the writer's actual no-clobber commit callback.
    A late cleanup/cancellation may follow publication; no retry or deletion is
    attempted. Link checks do not claim component-pinned OS no-follow security.
    """
    paths = [Path(p).absolute() for p in
             (pdf_path, recovery_path, reference_path, correspondence_path, output_path)]
    pdf_path, recovery_path, reference_path, correspondence_path, output_path = paths
    for index, path in enumerate(paths):
        storage_policy.assert_no_link_components(path)
        for other in paths[index + 1:]:
            if path.resolve() == other.resolve() or (
                    path.exists() and other.exists() and os.path.samefile(path, other)):
                raise ValueError("OCR context inputs and output must be distinct")
    with PathLease(output_path, backend="ocr-context", collection_name="report", operation="evaluate",
                   timeout=0, resource_description="OCR context evaluation report"):
        if output_path.exists():
            raise FileExistsError("OCR context output exists")
        specs = [(recovery_path, MAX_RECOVERY_BYTES), (reference_path, MAX_REFERENCE_BYTES),
                 (correspondence_path, MAX_CORRESPONDENCE_BYTES)]
        snapshots = [_read_snapshot(path, label="OCR context input", max_bytes=limit) for path, limit in specs]
        payloads = [_strict_json_bytes(raw, label="OCR context input", max_bytes=limit)
                    for (raw, _), (_, limit) in zip(snapshots, specs)]
        recovery = validate_recovery_report(payloads[0])
        source_digest = _source_digest(pdf_path)
        if source_digest != recovery["source_sha256"]:
            raise ValueError("OCR context source differs from saved recovery")
        pages = [{"page_number": page["page_number"],
                  "status": "retry_failed" if page["candidate"] is None else "available",
                  "text": None if page["candidate"] is None else page["candidate"]["text"]}
                 for page in recovery["pages"]]
        pages.extend({"page_number": page["page_number"], "status": "deferred", "text": None}
                     for page in recovery["deferred_pages"])
        result = evaluate_context_checks(
            payloads[1], payloads[2], source_sha256=source_digest,
            recovery_sha256=snapshots[0][1], reference_sha256=snapshots[1][1],
            page_count=recovery["page_count"], candidate_pages=pages)
        result["inputs"] = {"pdf_sha256": source_digest, "recovery_sha256": snapshots[0][1],
                            "reference_sha256": snapshots[1][1], "correspondence_sha256": snapshots[2][1]}

        def publish(temporary: Path, destination: Path) -> None:
            if _source_digest(pdf_path) != source_digest:
                raise RuntimeError("OCR context source changed before publication")
            for (path, limit), (_, digest) in zip(specs, snapshots):
                _, current = _read_snapshot(path, label="OCR context input", max_bytes=limit)
                if current != digest:
                    raise RuntimeError("OCR context input changed before publication")
            ocr_recovery._publish_new_report(temporary, destination)

        storage_policy.atomic_write_private_json(output_path, result, indent=2, replace_fn=publish)
    return result
