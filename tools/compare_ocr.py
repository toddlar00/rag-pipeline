#!/usr/bin/env python3
"""Compare original/retry OCR or two OCR runs against source-bound references."""

from __future__ import annotations

import argparse
from pathlib import Path
import sys

if __package__ in {None, ""}:
    sys.path.insert(0, str(Path(__file__).resolve().parents[1]))


class _ArgumentParser(argparse.ArgumentParser):
    def error(self, message: str) -> None:
        # argparse's default errors can echo unknown arguments containing paths.
        self.print_usage(sys.stderr)
        self.exit(2, "OCR comparison arguments are invalid. Use --help; "
                  "see docs/ocr-accuracy.md.\n")


def compare_recovery_file(
        recovery_path: Path, reference_path: Path, output_path: Path) -> dict:
    """Load comparison dependencies only when a comparison is requested."""
    from ocr_recovery_comparison import compare_recovery_file as compare

    return compare(recovery_path, reference_path, output_path)


def compare_recovery_run_files(
        baseline_path: Path, retry_path: Path, reference_path: Path,
        output_path: Path) -> dict:
    """Load the direct candidate comparison only when explicitly selected."""
    from ocr_run_comparison import compare_recovery_run_files as compare

    return compare(baseline_path, retry_path, reference_path, output_path)


def _rate(value: float | None) -> str:
    return "n/a (empty reference)" if value is None else f"{value:.2%}"


def main(argv: list[str] | None = None) -> int:
    parser = _ArgumentParser(prog="compare_ocr.py", description=__doc__)
    parser.add_argument(
        "--recovery", required=True, type=Path,
        help="Existing recovery JSON; the retry run when --baseline-recovery is given")
    parser.add_argument(
        "--baseline-recovery", type=Path,
        help="Optional first recovery JSON; compare both runs' candidates, not native text")
    parser.add_argument(
        "--references", required=True, type=Path,
        help="Reference transcription JSON bound to the same source PDF hash")
    parser.add_argument(
        "--output", required=True, type=Path,
        help="New private comparison JSON report; must not exist")
    args = parser.parse_args(argv)

    from ocr_recovery import ReportCleanupError

    try:
        if args.baseline_recovery is None:
            report = compare_recovery_file(args.recovery, args.references, args.output)
        else:
            report = compare_recovery_run_files(
                args.baseline_recovery, args.recovery, args.references, args.output)
    except ReportCleanupError:
        print(
            "OCR comparison report was created, but temporary-file cleanup failed. "
            "Inspect --output before retrying. See docs/ocr-accuracy.md.",
            file=sys.stderr)
        return 2
    except FileExistsError:
        print(
            "OCR comparison refused: output exists. Choose a new --output path. "
            "See docs/ocr-accuracy.md.", file=sys.stderr)
        return 2
    except (ValueError, OSError, RuntimeError, ImportError):
        # Never echo source text, paths, or exception details.
        print(
            "OCR comparison could not publish a report. Check recovery/reference "
            "formats, matching source hashes, and output permissions. "
            "See docs/ocr-accuracy.md.", file=sys.stderr)
        return 2

    coverage = report["coverage"]
    print(
        f"OCR comparison: {coverage['reference_pages']} reference pages; "
        f"{coverage['paired_pages']} paired pages. Comparison report written.")
    if args.baseline_recovery is not None:
        print("Comparing baseline recovery candidate -> retry recovery candidate (--recovery).")
        differences = report["setting_differences"]
        if differences:
            print(
                f"Run settings differ in {len(differences)} recorded fields; "
                "inspect the report before attributing changes to one setting.")
        confounders = report["confounders"]
        if confounders["evidence_binding_differs"]:
            print("Review notice: runs have different operator-evidence bindings.")
        if confounders["paired_engine_settings_differ"]:
            print(
                "Review notice: engine settings differ on "
                f"{len(confounders['paired_engine_difference_pages'])} paired pages.")
        if confounders["paired_preprocessing_libraries_differ"]:
            print(
                "Review notice: preprocessing library versions differ on "
                f"{len(confounders['paired_preprocessing_library_difference_pages'])} paired pages.")
    comparison = report["comparison"]
    if comparison is None:
        print("No paired pages to score.")
    else:
        summary = comparison["summary"]
        baseline, retry = summary["baseline"], summary["retry"]
        print(
            f"CER {_rate(baseline['character']['error_rate'])} -> "
            f"{_rate(retry['character']['error_rate'])}; "
            f"WER {_rate(baseline['word']['error_rate'])} -> "
            f"{_rate(retry['word']['error_rate'])}.")
        outcomes = summary["outcomes"]
        print(
            f"Outcomes: {outcomes['improved']} improved, "
            f"{outcomes['unchanged']} unchanged, {outcomes['regressed']} regressed, "
            f"{outcomes['mixed']} mixed.")
        if comparison["regression_detected"]:
            print("Attention: regression detected on compared text.")

    _print_coverage(coverage, run_comparison=args.baseline_recovery is not None)
    print("Candidates require manual review. Original extraction unchanged.")
    return 3 if report["requires_attention"] else 0


def _print_coverage(coverage: dict, *, run_comparison: bool) -> None:
    if coverage["unpaired_pages"]:
        print(f"Coverage warning: {len(coverage['unpaired_pages'])} unpaired reference pages.")
    if run_comparison:
        warnings = (
            ("unreferenced_selected_pages", "selected pages without references"),
            ("deferred_pages", "deferred pages"),
            ("empty_candidate_pages", "empty candidates"),
            ("failed_pages", "failed candidates"),
        )
        for side in ("baseline", "retry"):
            for key, label in warnings:
                if coverage[side][key]:
                    print(f"Coverage warning: {len(coverage[side][key])} {side} {label}.")
    else:
        warnings = (
            ("unreferenced_selected_pages", "selected pages without references"),
            ("deferred_pages", "deferred pages"),
            ("empty_candidate_pages", "empty retry candidates"),
        )
        for key, label in warnings:
            if coverage[key]:
                print(f"Coverage warning: {len(coverage[key])} {label}.")


if __name__ == "__main__":
    raise SystemExit(main())
