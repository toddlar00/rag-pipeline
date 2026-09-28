#!/usr/bin/env python3
"""Run bounded local OCR retries into a private, source-bound review report."""

from __future__ import annotations

import argparse
from pathlib import Path
import sys

if __package__ in {None, ""}:
    sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from ocr_recovery import ReportCleanupError, RetryPolicy, recover_pdf
from ocr_preprocessing import PREPROCESSING_MODES


class _ArgumentParser(argparse.ArgumentParser):
    def error(self, message: str) -> None:
        # argparse can otherwise echo source paths or arbitrary invalid values.
        self.print_usage(sys.stderr)
        self.exit(
            2, "OCR retry arguments are invalid. Use --help for required options "
            "and supported ranges; see docs/ocr-accuracy.md.\n")


_FAILURE_GUIDANCE = {
    "retry_limit_or_validation": (
        "validation or raster limit failures. Inspect the report and configured "
        "raster limits. For a raster limit failure at 400 DPI, try 300 DPI with "
        "a new output; other validation failures need the OCR setup checked."),
    "retry_runtime_unavailable": (
        "failures from unavailable OCR dependencies. Check that the project's required OCR "
        "dependencies and locked local models are installed, then retry with "
        "a new output."),
    "retry_runtime_failed": (
        "OCR runtime failures. Check the verified local models and runtime "
        "setup before retrying with a new output."),
}


def _print_failure_guidance(report: dict) -> None:
    pages = report.get("pages")
    if not isinstance(pages, list):
        return
    counts = dict.fromkeys(_FAILURE_GUIDANCE, 0)
    for page in pages:
        if not isinstance(page, dict) or page.get("status") != "retry_failed":
            continue
        code = page.get("error_code")
        if isinstance(code, str) and code in counts:
            counts[code] += 1
    for code, count in counts.items():
        if count:
            guidance = _FAILURE_GUIDANCE[code]
            if count == 1:
                guidance = guidance.replace("failures", "failure", 1)
            print(f"Failure guidance: {count} {guidance}")
    if any(counts.values()):
        print("See docs/ocr-accuracy.md for the retry and review workflow.")


def _print_report(report: dict, preprocessing: str) -> None:
    summary = report["summary"]
    if preprocessing != "none":
        print(f"Preprocessing experiment: {preprocessing}. Review transformation metadata against the original scan.")
    print(
        f"OCR review: {summary['selected']} selected, {summary['deferred']} deferred, "
        f"{summary['review_required']} candidates, {summary['empty']} empty, "
        f"{summary['failed']} failed. Original extraction unchanged."
    )
    if summary["selected"] == 0:
        print("No heuristic retries needed. This does not verify accuracy; use --pages for known problem pages.")
    else:
        print("Review report against source pages; confidence is not measured accuracy.")
    _print_failure_guidance(report)


def run_retry_with_deadline(argv: list[str], *, timeout: float) -> int:
    from ocr_recovery_supervision import run_retry_with_deadline as run

    return run(argv, timeout=timeout)


def load_supervised_report(output: Path) -> dict:
    from ocr_recovery_comparison import MAX_RECOVERY_BYTES, _load, validate_recovery_report

    payload, _ = _load(output, label="supervised OCR report", limit=MAX_RECOVERY_BYTES)
    return validate_recovery_report(payload)


def _report_exit_code(report: dict) -> int:
    summary = report["summary"]
    return 3 if summary["failed"] or summary["empty"] or summary["deferred"] else 0


def _run_supervised(args: argparse.Namespace, policy: RetryPolicy) -> int:
    child_arguments = [
        f"--pdf={args.pdf}", f"--output={args.output}",
        "--dpi", str(policy.dpi), "--max-pages", str(policy.max_pages),
        "--preprocess", policy.preprocessing,
    ]
    if args.evidence is not None:
        child_arguments.append(f"--evidence={args.evidence}")
    if args.pages:
        child_arguments.extend(["--pages", *(str(number) for number in args.pages)])
    print(
        f"OCR retry worker starting with a {args.timeout_seconds:g}s execution deadline. "
        "Native output is suppressed; the report will be checked before summarizing.",
        flush=True)
    try:
        status = run_retry_with_deadline(child_arguments, timeout=args.timeout_seconds)
    except KeyboardInterrupt:
        status = 130
    except (ValueError, OSError, RuntimeError, ImportError):
        print(
            "OCR supervised retry could not finish safely. Worker cleanup may not "
            "be confirmed. Inspect --output and private temporary files before "
            "retrying; see docs/ocr-accuracy.md.", file=sys.stderr)
        return 2
    if status in (124, 130):
        reason = "deadline exceeded" if status == 124 else "cancelled"
        print(
            f"OCR retry {reason}. Inspect --output before retrying; a completed "
            "report or private temporary files may already exist. "
            "This is not a successful or partial OCR result.", file=sys.stderr)
        return status
    if status not in (0, 3):
        print(
            "OCR retry worker failed. Check inputs, output permissions and the "
            "locked local OCR setup. Inspect --output before retrying; a report "
            "may already exist. See docs/ocr-accuracy.md.", file=sys.stderr)
        return 2
    try:
        report = load_supervised_report(args.output)
        configuration = dict(report["retry_configuration"])
        configuration.setdefault("preprocessing", "none")
        if (configuration != {
                "dpi": policy.dpi, "max_pixels": policy.max_pixels,
                "max_side": policy.max_side, "attempts_per_page": 1,
                "preprocessing": policy.preprocessing,
            } or report["selection"] != {
                "requested_pages": sorted(set(args.pages)), "max_pages": policy.max_pages,
                "min_chars": policy.min_chars, "order": "requested_then_page_number",
            } or (report["evidence_sha256"] is None) != (args.evidence is None)
                or _report_exit_code(report) != status):
            raise ValueError("supervised report disagrees with requested operation")
    except (ValueError, OSError, RuntimeError, ImportError):
        print(
            "OCR worker returned, but its report could not be validated against "
            "the requested settings and exit status. Inspect --output before "
            "retrying; no accuracy or completion claim can be made. "
            "See docs/ocr-accuracy.md.", file=sys.stderr)
        return 2
    _print_report(report, policy.preprocessing)
    return status


def main(argv: list[str] | None = None) -> int:
    parser = _ArgumentParser(
        prog="retry_ocr.py", description=__doc__,
        epilog=("Exit 0: report written; 3: unresolved retries; 2: invalid input "
                "or publication failure; 124: supervised deadline; 130: supervised "
                "cancellation. Accuracy still requires source review. "
                "See docs/ocr-accuracy.md."))
    parser.add_argument("--pdf", required=True, type=Path, help="Source PDF (never modified)")
    parser.add_argument("--output", required=True, type=Path, help="New private JSON report; must not exist")
    parser.add_argument("--pages", type=int, nargs="+", default=[], help="One-based PDF pages to prioritize (1..5000)")
    parser.add_argument("--dpi", type=int, choices=(300, 400), default=300, help="Render resolution (default: 300)")
    parser.add_argument("--max-pages", type=int, default=5, help="Maximum one-shot retries, 1..20 (default: 5)")
    parser.add_argument("--evidence", type=Path, help="Optional v1 source-hash-bound page text/quality JSON")
    parser.add_argument(
        "--preprocess", choices=PREPROCESSING_MODES, default="none",
        help="Opt-in image experiment (default: none); originals stay unchanged")
    parser.add_argument(
        "--timeout-seconds", type=float,
        help="Optional worker execution deadline, 1..86400 seconds; startup/cleanup are additional")
    args = parser.parse_args(argv)
    try:
        policy = RetryPolicy(
            dpi=args.dpi, max_pages=args.max_pages, preprocessing=args.preprocess)
        if args.timeout_seconds is not None:
            from ocr_recovery_supervision import validate_timeout

            args.timeout_seconds = validate_timeout(args.timeout_seconds)
    except ValueError:
        parser.error("invalid retry policy")
    if args.timeout_seconds is not None:
        try:
            return _run_supervised(args, policy)
        except KeyboardInterrupt:
            print(
                "OCR supervised retry was cancelled while checking or summarizing "
                "its result. Inspect --output before retrying; a completed report "
                "or private temporary files may already exist.", file=sys.stderr)
            return 130
    try:
        report = recover_pdf(
            args.pdf, args.output, policy=policy,
            requested_pages=tuple(args.pages), evidence_path=args.evidence)
    except ReportCleanupError:
        print(
            "OCR retry report was created, but temporary-file cleanup failed. "
            "Inspect --output before retrying. No canonical extraction was changed.",
            file=sys.stderr,
        )
        return 2
    except FileExistsError:
        print("OCR retry refused: output exists. Choose a new --output path.", file=sys.stderr)
        return 2
    except (ValueError, OSError, RuntimeError, ImportError):
        # Do not echo third-party exceptions, paths, OCR text or model internals.
        print(
            "OCR retry could not publish a report. Check PDF/evidence, page ranges, "
            "output permissions, and the locked local OCR dependencies/models. "
            "See docs/ocr-accuracy.md. No canonical extraction was changed.",
            file=sys.stderr,
        )
        return 2
    _print_report(report, args.preprocess)
    return _report_exit_code(report)


if __name__ == "__main__":
    raise SystemExit(main())
