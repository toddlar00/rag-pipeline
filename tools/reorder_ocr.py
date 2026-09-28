#!/usr/bin/env python3
"""Review explicit line-order plans against saved OCR; never run OCR or change it."""

from __future__ import annotations

import argparse
from pathlib import Path
import sys

if __package__ in {None, ""}:
    sys.path.insert(0, str(Path(__file__).resolve().parents[1]))


class _ArgumentParser(argparse.ArgumentParser):
    def error(self, message: str) -> None:
        self.print_usage(sys.stderr)
        self.exit(2, "OCR layout arguments are invalid. Use --help; "
                  "see docs/ocr-accuracy.md.\n")


def review_layout_files(
        recovery_path: Path, plan_path: Path, output_path: Path, *,
        reference_path: Path | None = None) -> dict:
    """Load saved-artifact dependencies only for an actual review."""
    from ocr_layout_io import review_layout_files as review

    return review(recovery_path, plan_path, output_path, reference_path=reference_path)


def _rate(value: float | None) -> str:
    return "n/a (empty reference)" if value is None else f"{value:.2%}"


def main(argv: list[str] | None = None) -> int:
    parser = _ArgumentParser(
        prog="reorder_ocr.py", description=__doc__, allow_abbrev=False,
        epilog="Exit 0: scored without detected issues; 3: review needed; "
               "2: invalid input/publication failure; 130: cancelled.")
    parser.add_argument("--recovery", required=True, type=Path,
                        help="Existing source-bound OCR recovery JSON; never modified")
    parser.add_argument("--plan", required=True, type=Path,
                        help="Explicit layout plan bound to the exact recovery-file digest")
    parser.add_argument("--output", required=True, type=Path,
                        help="New private JSON review report; must not exist")
    parser.add_argument("--references", type=Path,
                        help="Optional fixed reference text bound to the same source PDF digest")
    args = parser.parse_args(argv)

    try:
        from ocr_recovery import ReportCleanupError

        try:
            report = review_layout_files(
                args.recovery, args.plan, args.output, reference_path=args.references)
        except ReportCleanupError:
            print(
                "OCR layout report was created, but temporary-file cleanup failed. "
                "Inspect --output before retrying. See docs/ocr-accuracy.md.", file=sys.stderr)
            return 2
        summary = report["summary"]
        print(
            f"OCR layout review: {summary['review_pages']} review pages; "
            f"{summary['planned_pages']} planned; {summary['reordered']} reordered, "
            f"{summary['unchanged']} unchanged, {summary['abstained']} abstained, "
            f"{summary['unavailable']} unavailable.")
        comparison = report["comparison"]
        if comparison is not None:
            measured = comparison["summary"]
            before, after = measured["baseline"], measured["retry"]
            print(
                f"CER {_rate(before['character']['error_rate'])} -> "
                f"{_rate(after['character']['error_rate'])}; "
                f"WER {_rate(before['word']['error_rate'])} -> "
                f"{_rate(after['word']['error_rate'])}.")
            if comparison["regression_detected"]:
                print("Attention: regression detected on compared text.")
        elif args.references is None:
            print("No reference comparison was requested; accuracy is unverified.")
        else:
            print("No paired pages to score; inspect reference coverage in the report.")
        if args.references is not None:
            coverage = report["reference_coverage"]
            print(f"Reference coverage: {coverage['paired_pages']} paired of "
                  f"{coverage['reference_pages']} reference pages.")
            for key, label in (
                ("unpaired_pages", "unpaired reference pages"),
                ("unreferenced_selected_pages", "selected pages without references"),
                ("unreferenced_planned_pages", "planned pages without references"),
            ):
                if coverage[key]:
                    print(f"Coverage warning: {len(coverage[key])} {label}.")
        for key, label in (
            ("deferred_pages", "deferred pages"),
            ("empty_candidate_pages", "empty candidate pages"),
            ("failed_pages", "failed candidate pages"),
        ):
            if report["coverage"][key]:
                print(f"Coverage warning: {len(report['coverage'][key])} {label}.")
        if summary["abstained"]:
            print("Abstention guidance: inspect the explicit plan and line geometry "
                  "against the source layout before changing the plan.")
        if report["requires_attention"]:
            print("Attention: inspect coverage, abstentions, and comparison details in the report.")
        print("Explicit plans are not table classification. Manual review is required. "
              "Original OCR and canonical extraction unchanged.")
        return 3 if args.references is None or report["requires_attention"] else 0
    except KeyboardInterrupt:
        print(
            "OCR layout review was cancelled. A completed report may already exist; "
            "inspect --output before retrying. Original OCR and canonical extraction unchanged.",
            file=sys.stderr)
        return 130
    except FileExistsError:
        print("OCR layout refused: output exists. Choose a new --output path. "
              "See docs/ocr-accuracy.md.", file=sys.stderr)
        return 2
    except (ValueError, OSError, RuntimeError, ImportError):
        print(
            "OCR layout review could not complete. Check strict JSON formats, matching "
            "source/recovery hashes, explicit plans, and output permissions. A report may "
            "already exist; inspect --output before retrying. See docs/ocr-accuracy.md.",
            file=sys.stderr)
        return 2


if __name__ == "__main__":
    raise SystemExit(main())
