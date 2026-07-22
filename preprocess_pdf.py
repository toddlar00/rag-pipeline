#!/usr/bin/env python3
"""Strip full-page background scan images from OCR'd PDFs.

Many scanned-then-OCR'd textbooks have a full-page raster image behind the
text layer on every page. The text is extractable, but a PDF parser may try to
decompress every image and exhaust memory. This script creates a smaller PDF
while preserving the text layer.

Usage:
    python preprocess_pdf.py input.pdf
    python preprocess_pdf.py input.pdf -o clean.pdf
    python preprocess_pdf.py input.pdf --min-dim 500

Requires: PyMuPDF (pip install PyMuPDF)
"""

from __future__ import annotations

import argparse
import os
import subprocess
import time
from collections.abc import Sequence
from pathlib import Path
from typing import Any

import ingestion_core as _ingestion_core


def _require_positive_dimension(min_dimension: int) -> None:
    if isinstance(min_dimension, bool) or min_dimension <= 0:
        raise ValueError("minimum image dimension must be greater than zero")


def _positive_int(value: str) -> int:
    try:
        parsed = int(value)
    except ValueError as exc:
        raise argparse.ArgumentTypeError("must be an integer") from exc
    if parsed <= 0:
        raise argparse.ArgumentTypeError("must be greater than zero")
    return parsed


def _same_path(left: Path, right: Path) -> bool:
    return left.resolve(strict=False) == right.resolve(strict=False)


def _standalone_page_number(number: int | str) -> int | str:
    return number + 1 if isinstance(number, int) else number


def _page_text_is_usable(text: str) -> bool:
    return _ingestion_core.pdf_page_text_is_usable(text)


def _inspect_page_background_images(
        document, page, min_dimension: int
) -> _ingestion_core.PageBackgroundInspection:
    return _ingestion_core.inspect_page_background_images(
        document, page, min_dimension)


def _legacy_analysis_stats(
        analysis: _ingestion_core.PDFAnalysis) -> dict[str, Any]:
    """Expose canonical safety statistics plus historical standalone keys."""
    stats: dict[str, Any] = dict(analysis.stats)
    stats.update({
        "unique_image_dims": stats["unique_dims"],
        "total_image_xrefs": stats["image_xrefs"],
        "text_ok_pages": stats["pages_with_usable_text"],
        "inspection_issues": analysis.issues,
    })
    return stats


def analyze_pdf(input_path: Path, min_dimension: int = 1000) -> dict[str, Any]:
    """Analyze a PDF for raster images above the stripping threshold."""
    import pymupdf

    _require_positive_dimension(min_dimension)
    doc = pymupdf.open(str(input_path))
    try:
        analysis = _ingestion_core.analyze_pdf_document(
            doc,
            min_dimension,
            page_text_is_usable_fn=_page_text_is_usable,
            page_background_inspection_fn=_inspect_page_background_images,
        )
    finally:
        doc.close()
    return _legacy_analysis_stats(analysis)


def strip_background_images(
    input_path: Path,
    output_path: Path,
    min_dimension: int = 1000,
) -> dict[str, Any]:
    """Remove large raster images from each page while preserving text.

    Images are removed only when both their width and height exceed
    ``min_dimension``. The output is saved with garbage collection so orphaned
    image streams do not remain in the file.
    """
    import pymupdf

    _require_positive_dimension(min_dimension)
    if _same_path(input_path, output_path):
        raise ValueError("input and output paths must be different")

    started_at = time.time()
    doc = pymupdf.open(str(input_path))
    total_pages = len(doc)
    removed = 0
    failed_pages: list[tuple[int | str, str]] = []
    output_written = False

    try:
        plan = _ingestion_core.plan_background_image_removals(
            doc,
            min_dimension,
            page_text_is_usable_fn=_page_text_is_usable,
            page_background_inspection_fn=_inspect_page_background_images,
        )
        failed_pages.extend(
            (_standalone_page_number(issue.page_number), issue.detail)
            for issue in plan.issues
        )
        if plan.complete:
            removed_during_progress: set[int] = set()

            def delete_image(page, xref):
                page.delete_image(xref)
                removed_during_progress.add(xref)

            def progress_pages(pages):
                for page_index, assessment in enumerate(pages, 1):
                    yield assessment
                    if page_index % 100 == 0:
                        elapsed = time.time() - started_at
                        print(
                            f"  {page_index}/{total_pages} pages "
                            f"({elapsed:.0f}s, "
                            f"{len(removed_during_progress)} images stripped)"
                        )

            outcome = _ingestion_core.apply_background_image_removals(
                plan,
                delete_image_fn=delete_image,
                progress_pages_fn=progress_pages,
            )
            removed = outcome.removed_count
            failed_pages.extend(
                (_standalone_page_number(issue.page_number), issue.detail)
                for issue in outcome.deletion_issues
            )
            print(f"\nSaving to {output_path} (garbage collecting)...")
            doc.save(str(output_path), garbage=4, deflate=True, clean=True)
            output_written = True
    finally:
        doc.close()

    elapsed = time.time() - started_at
    original_mb = os.path.getsize(input_path) / 1e6
    if not output_written:
        return {
            "elapsed_sec": elapsed,
            "images_removed": 0,
            "original_mb": original_mb,
            "stripped_mb": original_mb,
            "failed_pages": failed_pages,
            "text_verify_passed": 0,
            "text_verify_total": 0,
            "output_written": False,
        }
    stripped_mb = os.path.getsize(output_path) / 1e6

    print("Verifying text extraction...")
    verification_doc = pymupdf.open(str(output_path))
    sample_pages = sorted(
        {
            page_index
            for page_index in (
                0,
                total_pages // 4,
                total_pages // 2,
                3 * total_pages // 4,
                total_pages - 1,
            )
            if 0 <= page_index < total_pages
        }
    )
    verify_ok = 0
    try:
        for page_index in sample_pages:
            if len(verification_doc[page_index].get_text().strip()) > 50:
                verify_ok += 1
    finally:
        verification_doc.close()

    return {
        "elapsed_sec": elapsed,
        "images_removed": removed,
        "original_mb": original_mb,
        "stripped_mb": stripped_mb,
        "failed_pages": failed_pages,
        "text_verify_passed": verify_ok,
        "text_verify_total": len(sample_pages),
        "output_written": True,
    }


def _build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        description="Strip background scan images from Paper Capture PDFs"
    )
    parser.add_argument("input", type=Path, help="Source PDF file")
    parser.add_argument(
        "-o",
        "--output",
        type=Path,
        help="Output path (default: <input>_textonly.pdf)",
    )
    parser.add_argument(
        "--min-dim",
        type=_positive_int,
        default=1000,
        help="Min pixel dimension (both W and H) to strip (default: 1000)",
    )
    parser.add_argument("--analyze", action="store_true", help="Analyze only")
    return parser


def main(argv: Sequence[str] | None = None) -> int:
    parser = _build_parser()
    args = parser.parse_args(argv)

    if not args.input.is_file():
        parser.error(f"source PDF not found or is not a file: {args.input}")

    if args.analyze:
        print(f"Analyzing {args.input}...")
        try:
            stats = analyze_pdf(args.input, args.min_dim)
        except Exception as exc:
            parser.exit(1, f"ERROR: could not analyze {args.input}: {exc}\n")

        dimensions = ", ".join(sorted(stats["unique_image_dims"])) or "none"
        print(f"\n  Total pages:              {stats['total_pages']}")
        print(f"  Pages with large images:  {stats['pages_with_large_images']}")
        print(f"  Unique image dimensions:  {dimensions}")
        print(f"  Unique image xrefs:       {len(stats['total_image_xrefs'])}")
        print(f"  Pages with readable text: {stats['text_ok_pages']}")

        scan_pages = stats["pages_with_large_images"]
        usable_scan_pages = stats.get("large_image_pages_with_usable_text")
        if stats.get("inspection_complete") is False:
            print(
                "\n  DIAGNOSIS: PDF inspection was incomplete; no images "
                "can be stripped safely."
            )
        elif scan_pages > stats["total_pages"] * 0.5:
            if usable_scan_pages is None or usable_scan_pages == scan_pages:
                print("\n  DIAGNOSIS: This PDF has full-page background scans.")
                print("  Run without --analyze to create a text-only version.")
            elif usable_scan_pages:
                print(
                    "\n  DIAGNOSIS: Mixed PDF; only scan pages with reliable "
                    "text can be stripped."
                )
            else:
                print(
                    "\n  DIAGNOSIS: Scan pages lack a reliable text layer; "
                    "keep their images and use OCR."
                )
        else:
            print("\n  This PDF has few large images; stripping may not be needed.")
        return 0

    output = args.output or args.input.with_stem(f"{args.input.stem}_textonly")
    if _same_path(args.input, output):
        parser.error("--output must be different from the input path")

    print(f"Stripping background images from {args.input}")
    print(f"  Output: {output}")
    print(f"  Min dimension threshold: {args.min_dim}px")
    print()

    try:
        stats = strip_background_images(args.input, output, args.min_dim)
    except Exception as exc:
        parser.exit(1, f"ERROR: could not preprocess {args.input}: {exc}\n")

    if stats.get("output_written", True) is False:
        print(f"\n{'=' * 60}")
        print("  No output was written because PDF inspection was incomplete.")
        print(f"  Original size:     {stats['original_mb']:.1f} MB")
        print(f"  Time:              {stats['elapsed_sec']:.0f}s")
        if stats["failed_pages"]:
            print(f"  Failed pages:      {stats['failed_pages'][:5]}")
        print(f"{'=' * 60}")
        return 1

    print(f"\n{'=' * 60}")
    print(f"  Images removed:    {stats['images_removed']}")
    print(f"  Original size:     {stats['original_mb']:.1f} MB")
    print(f"  Stripped size:     {stats['stripped_mb']:.1f} MB")
    print(f"  Time:              {stats['elapsed_sec']:.0f}s")
    print(
        f"  Text verification: {stats['text_verify_passed']}/"
        f"{stats['text_verify_total']} pages OK"
    )
    if stats["failed_pages"]:
        print(f"  Failed pages:      {stats['failed_pages'][:5]}")
    print(f"{'=' * 60}")
    next_command = subprocess.list2cmdline(
        ["python", "rag.py", "convert", "--pdf", str(output)]
    )
    print("\nDone. Use this file with the RAG pipeline:")
    print(f"  {next_command}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
