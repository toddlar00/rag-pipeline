#!/usr/bin/env python3
"""Export unconfirmed geometry-only column suggestions; never apply OCR corrections."""

from __future__ import annotations

import argparse
from pathlib import Path
import sys

if __package__ in {None, ""}:
    sys.path.insert(0, str(Path(__file__).resolve().parents[1]))


class _ArgumentParser(argparse.ArgumentParser):
    def error(self, message: str) -> None:
        self.print_usage(sys.stderr)
        self.exit(2, "OCR column suggestion arguments are invalid. Use --help.\n")


def _pages(value: str) -> int:
    if not value.isascii() or not value.isdecimal() or not 1 <= len(value) <= 4:
        raise argparse.ArgumentTypeError("invalid page")
    number = int(value)
    if not 1 <= number <= 5000:
        raise argparse.ArgumentTypeError("invalid page")
    return number


def suggest_column_files(source_path: Path, recovery_path: Path, output_path: Path, *,
                         requested_pages: list[int]) -> dict:
    """Keep help and argument parsing independent of policy/runtime imports."""
    from ocr_column_suggestions_io import suggest_column_files as suggest

    return suggest(source_path, recovery_path, output_path, requested_pages=requested_pages)


def _print_report(report: dict) -> None:
    summary = report["summary"]
    fields = {"source_pages", "requested_pages", "suggested_pages", "abstained_pages",
              "unavailable_pages", "empty_candidate_pages"}
    if (type(summary) is not dict or set(summary) != fields
            or any(type(summary[key]) is not int or not 0 <= summary[key] <= 5000 for key in fields)
            or not 1 <= summary["requested_pages"] <= min(20, summary["source_pages"])
            or sum(summary[key] for key in fields - {"source_pages", "requested_pages"}) != summary["requested_pages"]
            or report["requires_attention"] is not True):
        raise ValueError("invalid column suggestion summary")
    print(f"Column suggestions: {summary['requested_pages']} requested of {summary['source_pages']} source pages; "
          f"{summary['suggested_pages']} unconfirmed suggestions.")
    print(f"Other requested pages: {summary['abstained_pages']} abstained, "
          f"{summary['unavailable_pages']} unavailable, {summary['empty_candidate_pages']} empty candidates.")
    print("Operator prose classification and source review are required. Geometry cannot distinguish prose "
          "from tables or establish missing text. No OCR rerun, approval, or canonical text changes.")


def main(argv: list[str] | None = None) -> int:
    try:
        parser = _ArgumentParser(prog="suggest_ocr_columns.py", description=__doc__, allow_abbrev=False,
            epilog="Exit 3: report created, manual review always required; 2: invalid input/publication failure; "
                   "130: cancelled. No PDF parser or model is loaded.")
        for flag, help_text in (
                ("pdf", "Exact original source; hashed only, at most 256 MiB"),
                ("recovery", "Strict saved OCR recovery v1/v2 JSON; at most 64 MiB"),
                ("output", "New private suggestion JSON path; must not exist (at most 1 MiB)")):
            parser.add_argument("--" + flag, required=True, type=Path, help=help_text)
        parser.add_argument("--pages", required=True, nargs="+", type=_pages,
                            help="Explicit 1 to 20 distinct one-based page numbers; no ranges or default all-pages")
        args = parser.parse_args(argv)
        if len(args.pages) > 20 or len(set(args.pages)) != len(args.pages):
            parser.error("invalid page selection")
        from ocr_recovery import ReportCleanupError

        try:
            report = suggest_column_files(
                args.pdf, args.recovery, args.output, requested_pages=args.pages)
        except ReportCleanupError:
            print("OCR column suggestion report was created, but temporary-file cleanup failed. "
                  "Inspect --output before retrying with a new path.", file=sys.stderr)
            return 2
        _print_report(report)
        return 3
    except KeyboardInterrupt:
        print("OCR column suggestions cancelled. A report may already exist; inspect --output before retrying.",
              file=sys.stderr)
        return 130
    except FileExistsError:
        print("OCR column suggestion output already exists. Preserve it and choose a new --output path.",
              file=sys.stderr)
        return 2
    except Exception:
        print("OCR column suggestions could not complete. Check explicit page numbers, bounded strict JSON, "
              "exact source/report bindings and output permissions. A report may already exist; "
              "inspect --output before retrying.", file=sys.stderr)
        return 2


if __name__ == "__main__":
    raise SystemExit(main())
