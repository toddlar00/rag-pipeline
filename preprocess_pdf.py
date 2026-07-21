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


def analyze_pdf(input_path: Path, min_dimension: int = 1000) -> dict[str, Any]:
    """Analyze a PDF for raster images above the stripping threshold."""
    import pymupdf

    _require_positive_dimension(min_dimension)
    doc = pymupdf.open(str(input_path))
    stats: dict[str, Any] = {
        "total_pages": len(doc),
        "pages_with_large_images": 0,
        "unique_image_dims": set(),
        "total_image_xrefs": set(),
        "text_ok_pages": 0,
    }

    try:
        for page_index in range(len(doc)):
            page = doc[page_index]
            page_has_large_image = False

            for image_info in page.get_images(full=True):
                xref = image_info[0]
                try:
                    image = doc.extract_image(xref)
                except Exception:
                    continue
                if not image:
                    continue
                width, height = image["width"], image["height"]
                if width > min_dimension and height > min_dimension:
                    page_has_large_image = True
                    stats["unique_image_dims"].add(f"{width}x{height}")
                    stats["total_image_xrefs"].add(xref)

            if page_has_large_image:
                stats["pages_with_large_images"] += 1

            if len(page.get_text().strip()) > 50:
                stats["text_ok_pages"] += 1
    finally:
        doc.close()

    return stats


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
    failed_pages: list[tuple[int, str]] = []

    try:
        for page_index in range(total_pages):
            page = doc[page_index]

            try:
                images = page.get_images(full=True)
                for image_info in images:
                    xref = image_info[0]
                    try:
                        image = doc.extract_image(xref)
                        if not image:
                            continue
                        width, height = image["width"], image["height"]
                        if width > min_dimension and height > min_dimension:
                            page.delete_image(xref)
                            removed += 1
                    except Exception:
                        # Some XObject types cannot be extracted or deleted.
                        continue
            except Exception as exc:
                failed_pages.append((page_index + 1, str(exc)))

            if (page_index + 1) % 100 == 0:
                elapsed = time.time() - started_at
                print(
                    f"  {page_index + 1}/{total_pages} pages "
                    f"({elapsed:.0f}s, {removed} images stripped)"
                )

        print(f"\nSaving to {output_path} (garbage collecting)...")
        doc.save(str(output_path), garbage=4, deflate=True, clean=True)
    finally:
        doc.close()

    elapsed = time.time() - started_at
    original_mb = os.path.getsize(input_path) / 1e6
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

        if stats["pages_with_large_images"] > stats["total_pages"] * 0.5:
            print("\n  DIAGNOSIS: This PDF has full-page background scans.")
            print("  Run without --analyze to create a text-only version.")
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
