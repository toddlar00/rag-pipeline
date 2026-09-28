"""Hard-scan checkpoint entry points over the fixed shared crop workflow."""

from __future__ import annotations

from pathlib import Path

import ocr_recovery
from ocr_region_checkpoint_io import _checkpoint_request, _read_checkpoint_completion, _run_checkpointed


def hardscan_checkpoint_request(pdf_path: Path, output_dir: Path, *, operation: str = "hardscan",
                                policy: ocr_recovery.RetryPolicy | None = None,
                                requested_pages: tuple[int, ...] = (), evidence_path: Path | None = None,
                                installation_path: Path | None = None, recovery_path: Path | None = None,
                                plan_path: Path | None = None, resume: bool = False) -> tuple:
    """Validate exact original hard-scan plan, recovery and runtime before journal work."""
    return _checkpoint_request(pdf_path, output_dir, profile="hardscan", operation=operation, policy=policy,
                               requested_pages=requested_pages, evidence_path=evidence_path,
                               installation_path=installation_path, recovery_path=recovery_path,
                               plan_path=plan_path, resume=resume)


def run_checkpointed_hardscan(pdf_path: Path, output_dir: Path, *, resume: bool = False,
                             reader_factory=None, **options) -> dict:
    """Run only unstarted hard-scan attempts, preserving canonical vertex usage."""
    return _run_checkpointed(pdf_path, output_dir, profile="hardscan", resume=resume,
                             reader_factory=reader_factory, **options)


def read_hardscan_checkpoint_completion(output_dir: Path, *, request: dict, specs: list,
                                        installation_path: Path | None = None) -> dict:
    """Revalidate completed hard-scan records without constructing a PDF/OCR reader."""
    return _read_checkpoint_completion(output_dir, profile="hardscan", request=request, specs=specs,
                                       installation_path=installation_path)
