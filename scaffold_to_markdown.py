#!/usr/bin/env python3
"""
scaffold_to_markdown.py — Layer a TOC scaffold onto PDF body text to produce
hierarchical markdown where heading depths are authoritative from the Table
of Contents, not guessed from body font sizes.

Pipeline:
  1. Load scaffold JSON
  2. Flatten the TOC tree into page-ordered entries
  3. Extract body text from each page range (stripping headings, chrome)
  4. Emit markdown with TOC-driven heading levels

Usage:
  python3 scaffold_to_markdown.py <scaffold.json> <source.pdf> [-o output.md]
  python3 scaffold_to_markdown.py --all [--root workspace] [-o output_dir]

Requires: PyMuPDF (pip install pymupdf)
"""

import argparse
import json
import os
import re
from dataclasses import dataclass
from pathlib import Path
from collections.abc import Sequence
from typing import Optional

import fitz


# ─────────────────────────────────────────────────────────────
# Configuration
# ─────────────────────────────────────────────────────────────

# Margin zones (fraction of page height) — content in these zones
# is treated as header/footer chrome and stripped.
HEADER_ZONE = 0.045    # top 4.5% of page
FOOTER_ZONE = 0.045    # bottom 4.5% of page

# Font size threshold: spans with size > body_size * this multiplier
# are treated as headings (stripped from body, replaced by TOC headings)
HEADING_SIZE_MULTIPLIER = 1.25

# Maximum heading depth in markdown (h1 through h6)
MAX_HEADING_DEPTH = 6

# File naming convention used by the scaffold extraction step.
SCAFFOLD_SUFFIX = '_scaffold.json'


# ─────────────────────────────────────────────────────────────
# Data structures
# ─────────────────────────────────────────────────────────────

@dataclass
class FlatEntry:
    """A single TOC entry with its page range computed."""
    level: int
    entry_type: str
    title: str
    page_pdf: Optional[int]
    page_book: Optional[int]
    page_pdf_start: Optional[int] = None   # first PDF page (inclusive)
    page_pdf_end: Optional[int] = None     # last PDF page (inclusive)
    breadcrumb: str = ''                    # full path: "Ch2 > B > 3 > d"


# ─────────────────────────────────────────────────────────────
# Phase 1: Flatten the scaffold tree
# ─────────────────────────────────────────────────────────────

def flatten_toc(entries: list, parent_chain: list = None, depth: int = 0) -> list:
    """Flatten nested TOC into ordered list with breadcrumb chains."""
    parent_chain = parent_chain or []
    flat = []

    for entry in entries:
        title = entry.get('title', '')
        chain = parent_chain + [title]

        flat.append(FlatEntry(
            level=entry.get('level', depth),
            entry_type=entry.get('type', 'section'),
            title=title,
            page_pdf=entry.get('page_pdf'),
            page_book=entry.get('page_book'),
            breadcrumb=' > '.join(chain),
        ))

        if 'children' in entry:
            flat.extend(flatten_toc(entry['children'], chain, depth + 1))

    return flat


def compute_page_ranges(flat: list, total_pages: int) -> list:
    """Compute exclusive page ranges for each entry.

    Each entry "owns" from its page_pdf through the page before the next
    entry's page_pdf.  When entries share a page, only the first entry
    on that page gets the body text; subsequent entries on the same page
    get an empty range (they still emit their heading).
    """
    # Forward-fill page_pdf for entries that don't have one
    # (use the previous entry's page as a best guess)
    last_known = None
    for e in flat:
        if e.page_pdf:
            last_known = e.page_pdf
        else:
            e.page_pdf = last_known  # may still be None for leading entries

    # Compute ranges
    pages_already_assigned = set()

    for i, entry in enumerate(flat):
        if not entry.page_pdf:
            entry.page_pdf_start = None
            entry.page_pdf_end = None
            continue

        start = entry.page_pdf

        # Find the next entry's page
        next_page = None
        for j in range(i + 1, len(flat)):
            if flat[j].page_pdf and flat[j].page_pdf > start:
                next_page = flat[j].page_pdf
                break

        if next_page:
            end = next_page - 1
        else:
            # The final entry owns the rest of the document.  Silently
            # truncating it at an arbitrary page count loses legitimate body
            # text in long final sections; any index/back-matter filtering
            # should instead be represented by the scaffold itself.
            end = total_pages

        # If this start page was already assigned to an earlier entry,
        # don't extract text again — just emit the heading
        if start in pages_already_assigned:
            entry.page_pdf_start = None
            entry.page_pdf_end = None
        else:
            entry.page_pdf_start = start
            entry.page_pdf_end = end
            for p in range(start, end + 1):
                pages_already_assigned.add(p)

    return flat


# ─────────────────────────────────────────────────────────────
# Phase 2: Extract body text from PDF pages
# ─────────────────────────────────────────────────────────────

def detect_body_size(doc: fitz.Document, sample_pages: list) -> float:
    """Detect the most common font size (= body text) from sample pages."""
    size_counts = {}
    for pg in sample_pages:
        if pg < 1 or pg > doc.page_count:
            continue
        page = doc[pg - 1]
        for block in page.get_text("dict")["blocks"]:
            if "lines" not in block:
                continue
            for line in block["lines"]:
                for span in line["spans"]:
                    t = span["text"].strip()
                    if len(t) < 5:
                        continue
                    sz = round(span["size"], 1)
                    size_counts[sz] = size_counts.get(sz, 0) + len(t)

    if not size_counts:
        return 10.0  # fallback
    return max(size_counts, key=size_counts.get)


def extract_body_text(doc: fitz.Document, start_page: int, end_page: int,
                      body_size: float, entry_title: str = '') -> str:
    """Extract cleaned body text from a page range.

    Strips:
    - Header/footer zones (top/bottom margins)
    - Copyright lines
    - Standalone page numbers
    - Heading-sized text (replaced by TOC headings)
    - Running headers that repeat chapter/section titles
    """
    heading_threshold = body_size * HEADING_SIZE_MULTIPLIER
    paragraphs = []

    for pg in range(start_page, min(end_page + 1, doc.page_count + 1)):
        page = doc[pg - 1]
        page_h = page.rect.height
        margin_top = page_h * HEADER_ZONE
        margin_bottom = page_h * (1 - FOOTER_ZONE)

        page_lines = []
        current_line_y = None
        current_line_parts = []

        blocks = page.get_text("dict")["blocks"]
        for block in blocks:
            if "lines" not in block:
                continue
            for line in block["lines"]:
                for span in line["spans"]:
                    y = span["origin"][1]
                    text = span["text"]
                    size = span["size"]
                    stripped = text.strip()

                    if not stripped:
                        continue

                    # ── Skip: header/footer zones ──
                    if y < margin_top or y > margin_bottom:
                        continue

                    # ── Skip: copyright lines ──
                    if 'copyright' in stripped.lower() or 'all rights reserved' in stripped.lower():
                        continue

                    # ── Skip: heading-sized text ──
                    # These are the visual headings in the PDF body that we're
                    # replacing with TOC-driven markdown headings.
                    if size > heading_threshold:
                        continue

                    # ── Skip: standalone page numbers ──
                    if re.match(r'^\d{1,4}$', stripped) and size <= body_size:
                        continue

                    # ── Skip: running headers that match chapter pattern ──
                    if re.match(r'^Chapter\s+\d', stripped) and size > body_size:
                        continue

                    # Accumulate into lines (same y = same line)
                    if current_line_y is not None and abs(y - current_line_y) > 3:
                        # Flush previous line
                        line_text = ' '.join(current_line_parts).strip()
                        if line_text:
                            page_lines.append(line_text)
                        current_line_parts = []

                    current_line_y = y
                    # Clean the text
                    cleaned = text.replace('\b', '').replace('\x07', '')
                    cleaned = re.sub(r'[\ufffd]+', '', cleaned)
                    cleaned = re.sub(r'\u00ad', '-', cleaned)  # soft hyphen
                    current_line_parts.append(cleaned)

        # Flush final line
        if current_line_parts:
            line_text = ' '.join(current_line_parts).strip()
            if line_text:
                page_lines.append(line_text)

        if page_lines:
            paragraphs.append('\n'.join(page_lines))

    return '\n\n'.join(paragraphs)


# ─────────────────────────────────────────────────────────────
# Phase 3: Assemble hierarchical markdown
# ─────────────────────────────────────────────────────────────

def entry_to_heading(entry: FlatEntry) -> str:
    """Convert a TOC entry to a markdown heading line."""
    depth = min(entry.level + 1, MAX_HEADING_DEPTH)
    prefix = '#' * depth

    # Clean the title
    title = entry.title.strip()
    title = re.sub(r'[\ufffd]+', '', title)
    title = title.replace('\b', '')

    # Add type tag for cases (useful for downstream processing)
    if entry.entry_type == 'case':
        return f"{prefix} *{title}*"
    else:
        return f"{prefix} {title}"


def build_markdown(flat: list, doc: fitz.Document, body_size: float,
                   scaffold_data: dict) -> str:
    """Assemble the complete hierarchical markdown document."""
    lines = []

    # Document header
    book_title = scaffold_data.get('title', 'Untitled')
    offset = scaffold_data.get('page_offset', '?')
    lines.append(f"# {book_title}")
    lines.append('')
    lines.append(f"> Scaffold-driven markdown. "
                 f"Body offset: {offset}. "
                 f"TOC entries: {scaffold_data.get('toc_entry_count', '?')}. "
                 f"Source: `{scaffold_data.get('filename', '?')}`")
    lines.append('')
    lines.append('---')
    lines.append('')

    # Track progress
    total = len(flat)
    emitted = 0
    skipped_no_page = 0

    for i, entry in enumerate(flat):
        # Skip front-matter entries with no page or very early pages
        # (Preface, Acknowledgments, etc. — typically not useful for RAG)
        if entry.entry_type == 'front_matter':
            continue

        # Emit the heading
        heading = entry_to_heading(entry)
        lines.append(heading)
        lines.append('')

        # Extract and emit body text for this entry's page range
        if entry.page_pdf_start and entry.page_pdf_end:
            body = extract_body_text(
                doc,
                entry.page_pdf_start,
                entry.page_pdf_end,
                body_size,
                entry.title,
            )
            if body.strip():
                lines.append(body)
                lines.append('')

            emitted += 1
        else:
            skipped_no_page += 1

        # Progress
        if (i + 1) % 50 == 0:
            print(f"    [{i+1}/{total}] entries processed...")

    # Separator before end
    lines.append('---')
    lines.append('')
    lines.append(f"*Generated from scaffold. "
                 f"{emitted} sections with body text, "
                 f"{skipped_no_page} heading-only entries.*")

    return '\n'.join(lines)


# ─────────────────────────────────────────────────────────────
# Main
# ─────────────────────────────────────────────────────────────

def process_book(scaffold_path: str, pdf_path: str, output_path: str = None):
    """Process a single book: scaffold + PDF → hierarchical markdown."""

    print(f"\n{'='*60}")
    print(f"Scaffold: {os.path.basename(scaffold_path)}")
    print(f"PDF:      {os.path.basename(pdf_path)}")

    # Load scaffold
    with open(scaffold_path) as f:
        scaffold = json.load(f)

    print(f"  Title: {scaffold.get('title', '?')}")
    print(f"  TOC entries: {scaffold.get('toc_entry_count', '?')}")
    print(f"  Page offset: {scaffold.get('page_offset', '?')}")

    # Open PDF
    doc = fitz.open(pdf_path)
    total_pages = doc.page_count

    # Phase 1: Flatten and compute page ranges
    print("\n  Phase 1: Flattening TOC tree...")
    flat = flatten_toc(scaffold['toc'])
    flat = compute_page_ranges(flat, total_pages)

    entries_with_range = sum(1 for e in flat if e.page_pdf_start)
    entries_heading_only = sum(1 for e in flat if e.page_pdf and not e.page_pdf_start)
    print(f"    {len(flat)} entries total")
    print(f"    {entries_with_range} with exclusive page ranges")
    print(f"    {entries_heading_only} heading-only (shared page)")

    # Phase 2: Detect body text size
    print("\n  Phase 2: Detecting body text size...")
    # Sample pages from the middle of the book
    sample_pages = [e.page_pdf for e in flat
                    if e.page_pdf and e.entry_type == 'section'][:20]
    body_size = detect_body_size(doc, sample_pages)
    print(f"    Body size: {body_size}pt")
    print(f"    Heading threshold: >{body_size * HEADING_SIZE_MULTIPLIER:.1f}pt")

    # Phase 3: Assemble markdown
    print("\n  Phase 3: Assembling markdown...")
    markdown = build_markdown(flat, doc, body_size, scaffold)

    doc.close()

    # Write output
    if not output_path:
        output_path = str(_default_output_path(scaffold_path, scaffold))

    output_parent = Path(output_path).expanduser().parent
    output_parent.mkdir(parents=True, exist_ok=True)

    with open(output_path, 'w', encoding='utf-8') as f:
        f.write(markdown)

    # Stats
    md_lines = markdown.count('\n')
    md_size = len(markdown)
    heading_count = sum(1 for line in markdown.split('\n') if line.startswith('#'))
    print(f"\n  Output: {output_path}")
    print(f"    {md_lines} lines, {md_size/1024:.0f} KB")
    print(f"    {heading_count} markdown headings")

    return output_path


def _scaffold_base(scaffold_path: Path) -> str:
    """Return the book name encoded in a scaffold filename."""
    name = scaffold_path.name
    if name.casefold().endswith(SCAFFOLD_SUFFIX):
        return name[:-len(SCAFFOLD_SUFFIX)]
    return scaffold_path.stem


def _default_output_path(scaffold_path: str | Path,
                         scaffold_data: dict) -> Path:
    """Derive a local output path without assuming a hosted filesystem."""
    scaffold_path = Path(scaffold_path).expanduser()
    source_name = scaffold_data.get('filename')
    base = Path(str(source_name)).stem if source_name else _scaffold_base(
        scaffold_path)
    return scaffold_path.parent / f"{base}.md"


def _pdf_match_key(scaffold: Path, pdf: Path) -> tuple[int, str]:
    """Prefer a PDF beside its scaffold, then use a stable path ordering."""
    same_directory = pdf.parent.resolve() == scaffold.parent.resolve()
    return (0 if same_directory else 1, str(pdf).casefold())


def discover_book_pairs(root: str | Path) -> list[tuple[Path, Path]]:
    """Discover scaffold/PDF pairs recursively beneath *root*.

    Exact, case-insensitive stem matches win.  A prefix match is accepted as
    a fallback for common variants such as ``Book_OCR.pdf``.
    """
    root = Path(root).expanduser().resolve()
    if not root.is_dir():
        raise NotADirectoryError(f"Discovery root is not a directory: {root}")

    files = [path for path in root.rglob('*') if path.is_file()]
    scaffolds = sorted(
        (path for path in files
         if path.name.casefold().endswith(SCAFFOLD_SUFFIX)),
        key=lambda path: str(path).casefold(),
    )
    pdfs = [path for path in files if path.suffix.casefold() == '.pdf']

    pairs = []
    for scaffold in scaffolds:
        base = _scaffold_base(scaffold).casefold()
        exact = [pdf for pdf in pdfs if pdf.stem.casefold() == base]
        candidates = exact or [
            pdf for pdf in pdfs
            if pdf.stem.casefold().startswith(base)
        ]
        if candidates:
            pairs.append((scaffold, min(
                candidates,
                key=lambda pdf: _pdf_match_key(scaffold, pdf),
            )))

    return pairs


def build_parser() -> argparse.ArgumentParser:
    """Build the command-line parser independently of PDF processing."""
    parser = argparse.ArgumentParser(
        description=(
            "Apply a JSON TOC scaffold to a PDF and emit hierarchical "
            "Markdown."
        ),
    )
    parser.add_argument('scaffold', nargs='?', type=Path,
                        help='path to one scaffold JSON file')
    parser.add_argument('pdf', nargs='?', type=Path,
                        help='path to the corresponding source PDF')
    parser.add_argument(
        '-o', '--out', type=Path,
        help=(
            'output Markdown file; with --all, the directory for generated '
            'Markdown files'
        ),
    )
    parser.add_argument(
        '--all', action='store_true',
        help='discover and process every scaffold/PDF pair in the workspace',
    )
    parser.add_argument(
        '--root', type=Path,
        help='workspace root searched recursively by --all (default: cwd)',
    )
    return parser


def parse_args(argv: Sequence[str] | None = None) -> argparse.Namespace:
    """Parse and validate CLI arguments."""
    parser = build_parser()
    args = parser.parse_args(argv)

    if args.all:
        if args.scaffold is not None or args.pdf is not None:
            parser.error('--all cannot be combined with scaffold or PDF paths')
    elif args.scaffold is None or args.pdf is None:
        parser.error('provide both scaffold and PDF paths, or use --all')
    elif args.root is not None:
        parser.error('--root is only valid with --all')

    return args


def main(argv: Sequence[str] | None = None) -> int:
    args = parse_args(argv)

    if not args.all:
        output_path = str(args.out) if args.out is not None else None
        process_book(str(args.scaffold), str(args.pdf), output_path)
        return 0

    root = (args.root or Path.cwd()).expanduser().resolve()
    pairs = discover_book_pairs(root)
    if not pairs:
        print(f"No scaffold/PDF pairs found beneath {root}")
        return 1

    output_dir = args.out.expanduser() if args.out is not None else None
    if output_dir is not None:
        output_dir.mkdir(parents=True, exist_ok=True)

    for scaffold_path, pdf_path in pairs:
        output_path = (
            output_dir / f"{_scaffold_base(scaffold_path)}.md"
            if output_dir is not None else None
        )
        process_book(
            str(scaffold_path),
            str(pdf_path),
            str(output_path) if output_path is not None else None,
        )

    return 0


if __name__ == '__main__':
    raise SystemExit(main())
