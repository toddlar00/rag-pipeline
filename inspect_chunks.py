#!/usr/bin/env python3
"""Inspect chunk quality before indexing a RAG corpus.

Usage:
    python inspect_chunks.py output/Book/Book_chunks.jsonl
    python inspect_chunks.py output/Book/Book_chunks.jsonl --type case_opinion
    python inspect_chunks.py output/Book/Book_chunks.jsonl --show 42
    python inspect_chunks.py output/Book/Book_chunks.jsonl --cases
"""

from __future__ import annotations

import argparse
import json
import sys
from collections import Counter
from collections.abc import Sequence
from pathlib import Path
from statistics import mean, median
from typing import Any, TextIO

Chunk = dict[str, Any]


class ChunkFileError(ValueError):
    """Raised when a chunk JSONL file cannot be read or validated."""


def _configure_utf8_stream(stream: TextIO) -> None:
    """Use deterministic UTF-8 output, including on legacy Windows consoles."""
    reconfigure = getattr(stream, "reconfigure", None)
    if not callable(reconfigure):
        return
    try:
        reconfigure(encoding="utf-8", errors="replace")
    except (OSError, TypeError, ValueError):
        # Test doubles and streams owned by a host application may reject
        # reconfiguration. In that case, leave the host's stream untouched.
        pass


def _configure_utf8_output() -> None:
    _configure_utf8_stream(sys.stdout)
    _configure_utf8_stream(sys.stderr)


def _validate_chunk(value: object, path: Path, line_number: int) -> Chunk:
    location = f"{path}: line {line_number}"
    if not isinstance(value, dict):
        raise ChunkFileError(f"{location}: expected a JSON object")

    text = value.get("text")
    if not isinstance(text, str):
        raise ChunkFileError(f"{location}: 'text' must be a string")

    metadata = value.get("metadata")
    if not isinstance(metadata, dict):
        raise ChunkFileError(f"{location}: 'metadata' must be a JSON object")

    content_type = metadata.get("content_type")
    if not isinstance(content_type, str) or not content_type.strip():
        raise ChunkFileError(
            f"{location}: 'metadata.content_type' must be a non-empty string"
        )
    return value


def load_chunks(path: Path) -> list[Chunk]:
    """Load and minimally validate UTF-8 JSON Lines chunk records."""
    path = Path(path)
    chunks: list[Chunk] = []

    try:
        with path.open("r", encoding="utf-8-sig") as chunk_file:
            for line_number, line in enumerate(chunk_file, start=1):
                if not line.strip():
                    continue
                try:
                    value = json.loads(line)
                except json.JSONDecodeError as exc:
                    raise ChunkFileError(
                        f"{path}: invalid JSON on line {line_number}, "
                        f"column {exc.colno}: {exc.msg}"
                    ) from exc
                chunks.append(_validate_chunk(value, path, line_number))
    except FileNotFoundError as exc:
        raise ChunkFileError(f"chunk file not found: {path}") from exc
    except UnicodeDecodeError as exc:
        raise ChunkFileError(
            f"{path}: file is not valid UTF-8 near byte {exc.start}"
        ) from exc
    except OSError as exc:
        detail = exc.strerror or str(exc)
        raise ChunkFileError(f"could not read chunk file {path}: {detail}") from exc

    if not chunks:
        raise ChunkFileError(
            f"{path}: no chunk records found (the file is empty or blank)"
        )
    return chunks


def _indexed_chunks(
    chunks: Sequence[Chunk],
    source_indices: Sequence[int] | None = None,
) -> list[tuple[int, Chunk]]:
    if source_indices is None:
        return list(enumerate(chunks))
    if len(source_indices) != len(chunks):
        raise ValueError("source_indices must contain one index per chunk")
    return list(zip(source_indices, chunks, strict=True))


def _metadata_strings(metadata: dict[str, Any], key: str) -> list[str]:
    value = metadata.get(key, [])
    if not isinstance(value, list):
        return []
    return [item for item in value if isinstance(item, str)]


def _chapter_sort_key(value: object) -> tuple[int, float | str]:
    if isinstance(value, (int, float)) and not isinstance(value, bool):
        return (0, float(value))
    return (1, str(value))


def report(
    chunks: Sequence[Chunk],
    source_indices: Sequence[int] | None = None,
) -> None:
    """Print a quality report for the chunk set."""
    indexed = _indexed_chunks(chunks, source_indices)

    print(f"\n{'=' * 70}")
    print(f"CHUNK QUALITY REPORT — {len(chunks)} chunks")
    print(f"{'=' * 70}\n")

    if not chunks:
        print("No chunks matched the current selection.\n")
        return

    types = Counter(chunk["metadata"]["content_type"] for chunk in chunks)
    print("Content Type Distribution:")
    for content_type, count in types.most_common():
        percentage = count / len(chunks) * 100
        bar = "█" * int(percentage / 2)
        print(
            f"  {content_type:<25} {count:>4}  "
            f"({percentage:5.1f}%)  {bar}"
        )
    print()

    word_counts = [len(chunk["text"].split()) for chunk in chunks]
    print("Chunk Size (words):")
    print(f"  Min:    {min(word_counts)}")
    print(f"  Max:    {max(word_counts)}")
    print(f"  Mean:   {mean(word_counts):.0f}")
    print(f"  Median: {median(word_counts):g}")
    print()

    buckets = {"<50": 0, "50-200": 0, "200-500": 0, "500-1000": 0, ">1000": 0}
    for word_count in word_counts:
        if word_count < 50:
            buckets["<50"] += 1
        elif word_count < 200:
            buckets["50-200"] += 1
        elif word_count < 500:
            buckets["200-500"] += 1
        elif word_count < 1000:
            buckets["500-1000"] += 1
        else:
            buckets[">1000"] += 1

    print("Size Buckets:")
    for bucket, count in buckets.items():
        percentage = count / len(chunks) * 100
        bar = "█" * int(percentage / 2)
        print(f"  {bucket:<10} {count:>4}  ({percentage:5.1f}%)  {bar}")
    print()

    chapter_counts = Counter(
        chunk["metadata"]["chapter_num"]
        for chunk in chunks
        if chunk["metadata"].get("chapter_num") is not None
    )
    print("Chunks per Chapter:")
    for chapter in sorted(chapter_counts, key=_chapter_sort_key):
        title = next(
            (
                chunk["metadata"]["chapter_title"]
                for chunk in chunks
                if chunk["metadata"].get("chapter_num") == chapter
                and chunk["metadata"].get("chapter_title")
            ),
            "?",
        )
        print(f"  Ch.{str(chapter):>2}: {chapter_counts[chapter]:>4} chunks — {title}")
    unassigned = sum(
        1 for chunk in chunks if chunk["metadata"].get("chapter_num") is None
    )
    if unassigned:
        print(f"  (none): {unassigned:>4} chunks — no chapter detected")
    print()

    all_cases: set[str] = set()
    chunks_with_cases = 0
    for chunk in chunks:
        names = _metadata_strings(chunk["metadata"], "case_names")
        if names:
            chunks_with_cases += 1
            all_cases.update(names)
    print(
        f"Case Detection: {len(all_cases)} unique case names across "
        f"{chunks_with_cases} chunks"
    )
    print()

    issues = []
    for source_index, chunk in indexed:
        word_count = len(chunk["text"].split())
        if word_count < 20:
            issues.append(
                f"  Chunk {source_index}: only {word_count} words "
                "(may be too small)"
            )
        if word_count > 2000:
            issues.append(
                f"  Chunk {source_index}: {word_count} words "
                "(may need further splitting)"
            )
        if (
            chunk["metadata"]["content_type"] == "author_narrative"
            and "v." in chunk["text"][:100]
        ):
            issues.append(
                f"  Chunk {source_index}: classified as narrative but starts "
                "with case-like text"
            )

    if issues:
        print(f"Potential Issues ({len(issues)}):")
        for issue in issues[:20]:
            print(issue)
        if len(issues) > 20:
            print(f"  ... and {len(issues) - 20} more")
    else:
        print("No obvious quality issues detected.")
    print()


def show_chunk(
    chunks: Sequence[Chunk],
    index: int,
    source_indices: Sequence[int] | None = None,
) -> None:
    """Pretty-print a chunk, addressed by its original source index."""
    indexed = _indexed_chunks(chunks, source_indices)
    selected = next((chunk for source_index, chunk in indexed if source_index == index), None)
    if selected is None:
        raise IndexError(f"chunk #{index} does not exist in the current selection")

    metadata = selected["metadata"]
    case_names = _metadata_strings(metadata, "case_names")
    cross_references = _metadata_strings(metadata, "cross_references")

    print(f"\n{'=' * 70}")
    print(f"CHUNK #{index}")
    print(f"{'=' * 70}")
    print(f"  Content Type:   {metadata['content_type']}")
    print(f"  Section Path:   {metadata.get('section_path', '?')}")
    print(
        f"  Chapter:        {metadata.get('chapter_num', '?')} — "
        f"{metadata.get('chapter_title', '?')}"
    )
    print(f"  Primary Case:   {metadata.get('primary_case') or 'n/a'}")
    print(f"  All Cases:      {', '.join(case_names)}")
    print(f"  Page Range:     {metadata.get('page_range', '?')}")
    print(f"  Cross-refs:     {', '.join(cross_references)}")
    print(f"  Word Count:     {len(selected['text'].split())}")
    print(f"  Headings:       {metadata.get('headings', [])}")
    print("\n--- TEXT ---\n")
    print(selected["text"])
    print()


def list_cases(
    chunks: Sequence[Chunk],
    source_indices: Sequence[int] | None = None,
) -> None:
    """List detected case names with their original chunk locations."""
    case_map: dict[str, list[int]] = {}
    for source_index, chunk in _indexed_chunks(chunks, source_indices):
        for name in _metadata_strings(chunk["metadata"], "case_names"):
            case_map.setdefault(name, []).append(source_index)

    print(f"\n{'=' * 70}")
    print(f"DETECTED CASES — {len(case_map)} unique")
    print(f"{'=' * 70}\n")

    for name in sorted(case_map, key=str.lower):
        locations = case_map[name]
        location_text = ", ".join(str(location) for location in locations[:8])
        if len(locations) > 8:
            location_text += f" (+{len(locations) - 8} more)"
        print(f"  {name}")
        print(f"    Chunks: {location_text}")


def _build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description="Inspect RAG chunk quality")
    parser.add_argument("chunks_file", type=Path, help="UTF-8 JSONL chunk file")
    parser.add_argument("--type", help="Filter by metadata content type")
    parser.add_argument(
        "--show",
        type=int,
        metavar="INDEX",
        help="Show a chunk by its original zero-based index",
    )
    parser.add_argument("--cases", action="store_true", help="List detected cases")
    return parser


def main(argv: Sequence[str] | None = None) -> int:
    _configure_utf8_output()
    parser = _build_parser()
    args = parser.parse_args(argv)

    try:
        all_chunks = load_chunks(args.chunks_file)
    except ChunkFileError as exc:
        parser.error(str(exc))

    indexed = list(enumerate(all_chunks))
    if args.type:
        indexed = [
            (source_index, chunk)
            for source_index, chunk in indexed
            if chunk["metadata"]["content_type"] == args.type
        ]
        print(f"(Filtered to {len(indexed)} chunks of type '{args.type}')")

    source_indices = [source_index for source_index, _ in indexed]
    chunks = [chunk for _, chunk in indexed]

    if args.show is not None:
        try:
            show_chunk(chunks, args.show, source_indices)
        except IndexError as exc:
            parser.error(str(exc))
    elif args.cases:
        list_cases(chunks, source_indices)
    else:
        report(chunks, source_indices)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
