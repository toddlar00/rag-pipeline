#!/usr/bin/env python3
"""Propose review regions and reading order from verified saved Docling artifacts."""

from __future__ import annotations

import argparse
from pathlib import Path
import sys

if __package__ in {None, ""}:
    sys.path.insert(0, str(Path(__file__).resolve().parents[1]))


class _ArgumentParser(argparse.ArgumentParser):
    def error(self, message: str) -> None:
        self.print_usage(sys.stderr)
        self.exit(2, "Invalid Docling proposal arguments. Use --help; see docs/ocr-docling.md.\n")


def propose_docling_files(pdf_path: Path, docling_path: Path, recovery_path: Path, output_path: Path) -> dict:
    from ocr_docling_io import propose_docling_files as propose

    return propose(pdf_path, docling_path, recovery_path, output_path)


def main(argv: list[str] | None = None) -> int:
    try:
        parser = _ArgumentParser(prog="propose_ocr_layout.py", description=__doc__, allow_abbrev=False,
                                 epilog="Exit 3: manual review required; 2: input/publication failure; "
                                        "130: cancelled. No OCR runs or canonical changes. See docs/ocr-docling.md.")
        parser.add_argument("--pdf", required=True, type=Path, help="Original source; hashed only, never rendered")
        parser.add_argument("--docling", required=True, type=Path, help="Saved Docling JSON with matching v3 conversion completion")
        parser.add_argument("--recovery", required=True, type=Path, help="Existing source-bound OCR recovery JSON")
        parser.add_argument("--output", required=True, type=Path, help="New private proposal JSON; must not exist")
        args = parser.parse_args(argv)
        from ocr_recovery import ReportCleanupError

        try:
            report = propose_docling_files(args.pdf, args.docling, args.recovery, args.output)
        except ReportCleanupError:
            print("Docling proposals were created, but private temporary-file cleanup failed. "
                  "Inspect --output before retrying. See docs/ocr-docling.md.", file=sys.stderr)
            return 2
        summary = report["summary"]
        print(f"Docling layout proposals: {summary['pages']} pages; {summary['proposed']} proposed, "
              f"{summary['abstained']} abstained, {summary['unavailable']} unavailable; {summary['regions']} regions.")
        for key, label in (("deferred_pages", "deferred pages"), ("not_selected_pages", "pages without selected OCR")):
            if report["coverage"][key]:
                print(f"Coverage warning: {len(report['coverage'][key])} {label}.")
        print("Manual review against the source is required. Docling regions, tables, and order are suggestions, "
              "not verified accuracy. OCR and canonical extraction unchanged.")
        return 3
    except KeyboardInterrupt:
        print("Docling proposal creation was cancelled. A completed output may already exist; "
              "inspect --output before retrying. Source and canonical extraction unchanged.", file=sys.stderr)
        return 130
    except FileExistsError:
        print("Docling proposals refused: output exists. Choose a new --output. See docs/ocr-docling.md.", file=sys.stderr)
        return 2
    except Exception:
        print("Docling proposals could not complete. Check matching source/recovery hashes, saved Docling JSON, "
              "strict v3 conversion completion, input limits, and output permissions. An output may already "
              "exist; inspect --output before retrying. See docs/ocr-docling.md.", file=sys.stderr)
        return 2


if __name__ == "__main__":
    raise SystemExit(main())
