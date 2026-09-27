#!/usr/bin/env python3
"""Inspect saved Docling regions for potential OCR omissions; no OCR or PDF parsing."""

from __future__ import annotations

import argparse
from pathlib import Path
import sys

if __package__ in {None, ""}:
    sys.path.insert(0, str(Path(__file__).resolve().parents[1]))


class _ArgumentParser(argparse.ArgumentParser):
    def error(self, message: str) -> None:
        self.print_usage(sys.stderr)
        self.exit(2, "OCR omission arguments are invalid. Use --help; see docs/ocr-omissions.md.\n")


def inspect_omission_files(source_path: Path, recovery_path: Path, proposals_path: Path,
                           output_path: Path) -> dict:
    """Keep help and argument parsing free of policy/runtime imports."""
    from ocr_omission_io import inspect_omission_files as inspect

    return inspect(source_path, recovery_path, proposals_path, output_path)


def _print_report(report: dict) -> None:
    summary = report["summary"]
    fields = ("evaluated_pages", "source_pages", "unevaluated_pages", "saved_regions", "target_regions",
              "no_candidate_line_overlap", "empty_text_only", "boundary_or_ambiguous_overlap",
              "body_attention_regions", "furniture_attention_regions", "incomplete_layout_pages")
    if (any(type(summary[key]) is not int or not 0 <= summary[key] <= 10_000 for key in fields)
            or type(report["requires_attention"]) is not bool):
        raise ValueError("invalid OCR omission summary")
    print(f"OCR omission geometry: {summary['evaluated_pages']} evaluated of {summary['source_pages']} source pages; "
          f"{summary['unevaluated_pages']} unevaluated.")
    print(f"Saved regions: {summary['target_regions']} text/heading/table targets of {summary['saved_regions']} regions.")
    print(f"Region warnings: {summary['no_candidate_line_overlap']} without line overlap; "
          f"{summary['empty_text_only']} whitespace-only; "
          f"{summary['boundary_or_ambiguous_overlap']} boundary/ambiguous.")
    print(f"Attention regions: {summary['body_attention_regions']} body, "
          f"{summary['furniture_attention_regions']} furniture; "
          f"{summary['incomplete_layout_pages']} pages have incomplete saved layout evidence.")
    print("Review warnings against the original scan. These checks cover saved Docling regions only, "
          "not independent scan ink or complete source text. No OCR rerun or canonical changes.")


def main(argv: list[str] | None = None) -> int:
    try:
        parser = _ArgumentParser(prog="inspect_ocr_omissions.py", description=__doc__, allow_abbrev=False,
            epilog="Exit 0: no scoped warnings, not completeness certification; 3: warnings/unevaluated coverage; "
                   "2: invalid input/publication failure; 130: cancelled. See docs/ocr-omissions.md.")
        for flag, help_text in (
                ("pdf", "Exact original source; hashed only, bounded at 256 MiB"),
                ("recovery", "Strict saved OCR recovery v1/v2 JSON; at most 64 MiB"),
                ("proposals", "Fixed source/recovery-bound Docling proposals v1 JSON; at most 32 MiB"),
                ("output", "New private diagnostic JSON path; must not exist")):
            parser.add_argument("--" + flag, required=True, type=Path, help=help_text)
        args = parser.parse_args(argv)
        from ocr_recovery import ReportCleanupError

        try:
            report = inspect_omission_files(args.pdf, args.recovery, args.proposals, args.output)
        except ReportCleanupError:
            print("OCR omission report was created, but temporary-file cleanup failed. "
                  "Inspect --output before retrying with a new path.", file=sys.stderr)
            return 2
        _print_report(report)
        return 3 if report["requires_attention"] else 0
    except KeyboardInterrupt:
        print("OCR omission inspection cancelled. A report may already exist; inspect --output before retrying.",
              file=sys.stderr)
        return 130
    except FileExistsError:
        print("OCR omission output already exists. Preserve it and choose a new --output path.", file=sys.stderr)
        return 2
    except Exception:
        print("OCR omission inspection could not complete. Check bounded strict JSON, exact source/report "
              "bindings and output permissions. A report may already exist; inspect --output before retrying. "
              "See docs/ocr-omissions.md.", file=sys.stderr)
        return 2


if __name__ == "__main__":
    raise SystemExit(main())
