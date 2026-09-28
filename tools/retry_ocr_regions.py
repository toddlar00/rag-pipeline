#!/usr/bin/env python3
"""Retry explicit source-bound PDF regions into a new private review report."""

from __future__ import annotations

import argparse
from pathlib import Path
import subprocess
import sys

if __package__ in {None, ""}:
    sys.path.insert(0, str(Path(__file__).resolve().parents[1]))


class _ArgumentParser(argparse.ArgumentParser):
    def error(self, message: str) -> None:
        self.print_usage(sys.stderr)
        self.exit(2, "OCR region arguments are invalid. Use --help; see docs/ocr-accuracy.md.\n")


def recover_regions(*args, **kwargs) -> dict:
    from ocr_regions import recover_regions as recover

    return recover(*args, **kwargs)


def load_region_review(path: Path) -> dict:
    from ocr_regions import load_region_review as load

    return load(path)


def verify_region_review_request(report: dict, **kwargs) -> dict:
    from ocr_regions import verify_region_review_request as verify

    return verify(report, **kwargs)


def run_with_deadline(argv: list[str], *, timeout: float) -> int:
    from ocr_recovery_supervision import validate_timeout
    import process_supervision

    timeout = validate_timeout(timeout)
    if any(argument.split("=", 1)[0] == "--timeout-seconds" for argument in argv):
        raise ValueError("region worker arguments must not include a timeout")
    config = process_supervision.SupervisionConfig(
        supervised_child_env="RAG_PIPELINE_SUPERVISED_CHILD", run_id_env="RAG_PIPELINE_RUN_ID",
        terminate_grace=5.0, poll_interval=0.2, start_gate_timeout=60.0)

    def warn(_message):
        print("OCR region worker exceeded its deadline. A completed report or private temporary "
              "files may remain; inspect --output before retrying.", file=sys.stderr)

    return process_supervision._run_cli_with_deadline(
        Path(__file__), list(argv), operation="ocr-region-retry", timeout=timeout, config=config,
        warn_fn=warn, stdout_target=subprocess.DEVNULL, stderr_target=subprocess.DEVNULL)


def _print_report(report: dict) -> None:
    summary = report["summary"]
    print(f"OCR region review: {summary['requested']} requested, {summary['candidates']} candidates, "
          f"{summary['empty']} empty, {summary['failed']} failed.")
    print("Independent region candidates require manual review. Page context is not a region "
          "reference. Original OCR and canonical extraction unchanged.")


def main(argv: list[str] | None = None) -> int:
    parser = _ArgumentParser(prog="retry_ocr_regions.py", description=__doc__, allow_abbrev=False)
    parser.add_argument("--pdf", required=True, type=Path, help="Original PDF; never modified")
    parser.add_argument("--recovery", required=True, type=Path, help="Saved source-bound page recovery JSON")
    parser.add_argument("--plan", required=True, type=Path, help="Source/recovery-bound plan with 1..20 display-fraction regions")
    parser.add_argument("--output", required=True, type=Path, help="New private JSON report; must not exist")
    parser.add_argument("--dpi", type=int, choices=(300, 400), default=300)
    parser.add_argument("--timeout-seconds", type=float,
                        help="Optional whole-worker deadline, 1..86400 seconds; cleanup grace is additional")
    try:
        args = parser.parse_args(argv)
        from ocr_recovery import ReportCleanupError

        try:
            if args.timeout_seconds is None:
                report = recover_regions(args.pdf, args.recovery, args.plan, args.output, dpi=args.dpi)
            else:
                from ocr_recovery_supervision import validate_timeout

                timeout = validate_timeout(args.timeout_seconds)
                child_arguments = [f"--pdf={args.pdf}", f"--recovery={args.recovery}",
                                   f"--plan={args.plan}", f"--output={args.output}",
                                   "--dpi", str(args.dpi)]
                print("Starting bounded OCR region worker; native worker output is suppressed.", flush=True)
                code = run_with_deadline(child_arguments, timeout=timeout)
                if code == 124:
                    return 124
                if code == 130:
                    raise KeyboardInterrupt
                if code != 3:
                    raise RuntimeError("OCR region worker did not complete its report")
                report = load_region_review(args.output)
                report = verify_region_review_request(
                    report, pdf_path=args.pdf, recovery_path=args.recovery,
                    plan_path=args.plan, dpi=args.dpi)
        except ReportCleanupError:
            print("OCR region report was created, but temporary-file cleanup failed. "
                  "Inspect --output before retrying.", file=sys.stderr)
            return 2
        _print_report(report)
        return 3
    except KeyboardInterrupt:
        print("OCR region review was cancelled. A completed report or private temporary files may "
              "remain; inspect --output before retrying. Original extraction unchanged.", file=sys.stderr)
        return 130
    except FileExistsError:
        print("OCR region output exists. Choose a new --output path.", file=sys.stderr)
        return 2
    except (ValueError, OSError, RuntimeError, ImportError):
        print("OCR region review could not complete. Check source/recovery/plan hashes, region "
              "bounds, output permissions, and verified local OCR dependencies. A completed report "
              "or private temporary files may remain; inspect --output before retrying. "
              "See docs/ocr-accuracy.md.", file=sys.stderr)
        return 2


if __name__ == "__main__":
    raise SystemExit(main())
