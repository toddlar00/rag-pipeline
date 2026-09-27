#!/usr/bin/env python3
"""Evaluate reviewed critical OCR occurrences without correcting or rerunning OCR."""

from __future__ import annotations

import argparse
from pathlib import Path
import sys

if __package__ in {None, ""}:
    sys.path.insert(0, str(Path(__file__).resolve().parents[1]))


class _ArgumentParser(argparse.ArgumentParser):
    def error(self, message: str) -> None:
        self.print_usage(sys.stderr)
        self.exit(2, "OCR context arguments are invalid. Use --help; see docs/ocr-context-evaluation.md.\n")


def evaluate_context_files(pdf_path: Path, recovery_path: Path, reference_path: Path,
                           correspondence_path: Path, output_path: Path) -> dict:
    """Keep help and argument validation free of evaluation/runtime imports."""
    from ocr_context_evaluation_io import evaluate_context_files as evaluate

    return evaluate(pdf_path, recovery_path, reference_path, correspondence_path, output_path)


def main(argv: list[str] | None = None) -> int:
    try:
        parser = _ArgumentParser(prog="evaluate_ocr_context.py", description=__doc__, allow_abbrev=False,
                                 epilog="Exit 0: scoped checks pass; 3: failure/abstention/coverage gap; "
                                        "2: invalid input/publication failure; 130: cancelled.")
        for flag, help_text in (
                ("pdf", "Exact source PDF; hashed only, never parsed"),
                ("recovery", "Strict saved OCR recovery v1/v2 JSON"),
                ("reference", "Source-bound reviewed contextual reference JSON"),
                ("correspondence", "Reviewed mapping bound to exact recovery and reference bytes"),
                ("output", "New private report path; must not exist")):
            parser.add_argument("--" + flag, required=True, type=Path, help=help_text)
        args = parser.parse_args(argv)
        from ocr_recovery import ReportCleanupError

        try:
            report = evaluate_context_files(args.pdf, args.recovery, args.reference, args.correspondence, args.output)
        except ReportCleanupError:
            print("OCR context report was created, but temporary-file cleanup failed. "
                  "Inspect --output before retrying.", file=sys.stderr)
            return 2
        coverage = report["coverage"]
        checks, contexts, pages = coverage["checks"], coverage["contexts"], coverage["pages"]
        print(f"OCR context checks: {checks['passed']} passed, {checks['failed']} failed, "
              f"{checks['abstained']} abstained; {checks['evaluated']} evaluated of {checks['total']}.")
        print(f"Context coverage: {contexts['mapped']} mapped of {contexts['total']}; "
              f"{len(pages['with_reference_contexts'])} pages have reviewed contexts "
              f"out of {pages['source_page_count']} source pages.")
        print(f"Unchecked recovery pages: {len(pages['unchecked_selected_pages'])} selected; "
              f"{len(pages['unchecked_deferred_pages'])} deferred.")
        print("Selected occurrence checks are not full-page or semantic verification. "
              "Correspondence and approval are operator claims. CER/WER are separate. "
              "Original OCR and canonical extraction unchanged.")
        return 3 if report["requires_attention"] else 0
    except KeyboardInterrupt:
        print("OCR context evaluation cancelled. A report may already exist; inspect --output before retrying.",
              file=sys.stderr)
        return 130
    except (ValueError, OSError, RuntimeError, ImportError):
        print("OCR context evaluation could not complete. Check bounded strict JSON, exact source/report "
              "bindings, reviewed anchors and correspondence, and output permissions. A report may already "
              "exist; inspect --output before retrying. See docs/ocr-context-evaluation.md.", file=sys.stderr)
        return 2


if __name__ == "__main__":
    raise SystemExit(main())
