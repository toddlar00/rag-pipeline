"""Fixed-artifact, create-only Docling proposal publication; no PDF/model parsing."""

from __future__ import annotations

import os
from pathlib import Path
import stat

from evaluation_inputs import _read_snapshot
from ocr_docling import build_docling_proposals
from ocr_recovery_comparison import _load
from resource_lease import PathLease
import ocr_recovery
import storage_policy


MAX_SOURCE_BYTES = 256 * 1024 * 1024
MAX_DOCLING_BYTES = 64 * 1024 * 1024
MAX_RECOVERY_BYTES = 64 * 1024 * 1024
MAX_MANIFEST_BYTES = 1024 * 1024


def load_conversion_binding(docling_path: Path, *, document_sha256: str, document_size: int):
    """Use the facade's sole completion parser, only on an actual operation.

    The fixed sidecar is snapshotted before and after its existing strict v3
    parser. No file named inside the manifest is opened by this adapter.
    """
    import rag

    path = rag._artifact_completion_path(docling_path, stage="conversion")
    _distinct([path])
    _, before = _read_snapshot(path, label="conversion completion", max_bytes=MAX_MANIFEST_BYTES)
    binding = rag._load_conversion_source_binding(
        docling_path, document_sha256=document_sha256, document_size=document_size)
    if (binding is None or binding.schema_version != 3 or not binding.capture_verified
            or binding.manifest_path.absolute() != path.absolute() or binding.manifest_sha256 != before):
        raise ValueError("a matching strict v3 conversion completion is required")
    _, after = _read_snapshot(path, label="conversion completion", max_bytes=MAX_MANIFEST_BYTES)
    if before != after:
        raise RuntimeError("conversion completion changed while validating")
    if binding.ocr_angle_classifier is False:
        # Retry OCR always runs RapidOCR's angle classifier; its text must not
        # be spliced into a conversion that was made without it.
        raise ValueError("conversions made without the OCR angle classifier are unsupported")
    return binding


def _distinct(paths: list[Path]) -> None:
    for path in paths:
        storage_policy.assert_no_link_components(path)
        if path.exists():
            info = path.stat()
            if not stat.S_ISREG(info.st_mode) or info.st_nlink != 1:
                raise ValueError("Docling proposal files must be single-linked regular files")
    for i, path in enumerate(paths):
        for other in paths[i + 1:]:
            if path.resolve() == other.resolve() or (path.exists() and other.exists() and os.path.samefile(path, other)):
                raise ValueError("Docling proposal inputs and output must be distinct")


def propose_docling_files(pdf_path: Path, docling_path: Path, recovery_path: Path, output_path: Path) -> dict:
    """Bind the source bytes, saved structure, recovery, and completion before commit.

    Reading the source hashes bytes only; it does not render/open a PDF parser.
    A late cancellation or cleanup failure may follow a successful publication.
    """
    pdf_path, docling_path, recovery_path, output_path = (
        Path(path).absolute() for path in (pdf_path, docling_path, recovery_path, output_path))
    _distinct([pdf_path, docling_path, recovery_path, output_path])
    with PathLease(output_path, backend="ocr-docling", collection_name="review", operation="propose",
                   timeout=0, resource_description="OCR Docling proposal report"):
        if output_path.exists():
            raise FileExistsError("Docling proposal output already exists")
        source, source_digest = _read_snapshot(pdf_path, label="Docling source", max_bytes=MAX_SOURCE_BYTES)
        source_size = len(source)
        del source
        docling, docling_digest = _load(docling_path, label="saved Docling JSON", limit=MAX_DOCLING_BYTES)
        raw, digest = _read_snapshot(docling_path, label="saved Docling JSON", max_bytes=MAX_DOCLING_BYTES)
        if digest != docling_digest:
            raise RuntimeError("Docling document changed while loading")
        document_size = len(raw)
        del raw
        recovery, recovery_digest = _load(recovery_path, label="OCR recovery", limit=MAX_RECOVERY_BYTES)
        binding = load_conversion_binding(docling_path, document_sha256=docling_digest, document_size=document_size)
        manifest_path = Path(binding.manifest_path).absolute()
        _distinct([pdf_path, docling_path, recovery_path, manifest_path, output_path])
        if (binding.source_sha256 != source_digest or binding.source_size != source_size
                or recovery.get("source_sha256") != source_digest):
            raise ValueError("Docling conversion and recovery do not bind the supplied source")
        report = build_docling_proposals(
            docling, recovery, recovery_sha256=recovery_digest, docling_sha256=docling_digest,
            manifest_sha256=binding.manifest_sha256, effective_input_kind=binding.effective_input_kind)
        specs = [(pdf_path, source_digest, MAX_SOURCE_BYTES),
                 (docling_path, docling_digest, MAX_DOCLING_BYTES),
                 (recovery_path, recovery_digest, MAX_RECOVERY_BYTES),
                 (manifest_path, binding.manifest_sha256, MAX_MANIFEST_BYTES)]

        def publish(temporary, destination):
            _distinct([path for path, _, _ in specs] + [output_path])
            for path, expected, limit in specs:
                _, current = _read_snapshot(path, label="Docling proposal input", max_bytes=limit)
                if current != expected:
                    raise RuntimeError("Docling proposal input changed before publication")
            ocr_recovery._publish_new_report(temporary, destination)

        storage_policy.atomic_write_private_json(output_path, report, indent=2, replace_fn=publish)
    return report
