#!/usr/bin/env python3
"""Run explicit hard-scan recipes into a new private, manual-review-only report."""

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
        self.exit(2, "Hard-scan arguments are invalid. Use --help; see docs/ocr-hardscan.md.\n")


def recover_hardscan(*args, **kwargs):
    from ocr_hardscan_io import recover_hardscan as recover

    return recover(*args, **kwargs)


def load_report(path):
    from ocr_hardscan_io import load_report as load

    return load(path)


def verify_request(report, **kwargs):
    from ocr_hardscan_io import verify_request as verify

    return verify(report, **kwargs)


def run_with_deadline(argv: list[str], *, timeout: float) -> int:
    from ocr_recovery_supervision import validate_timeout
    import process_supervision

    timeout = validate_timeout(timeout)
    if any(argument.split("=", 1)[0] == "--timeout-seconds" for argument in argv):
        raise ValueError("hard-scan worker arguments must not include a timeout")
    config = process_supervision.SupervisionConfig(
        supervised_child_env="RAG_PIPELINE_SUPERVISED_CHILD", run_id_env="RAG_PIPELINE_RUN_ID",
        terminate_grace=5.0, poll_interval=0.2, start_gate_timeout=60.0)

    def warn(_message):
        print("Hard-scan worker exceeded its deadline. A completed report or private temporary files "
              "may remain; inspect --output before retrying.", file=sys.stderr)

    return process_supervision._run_cli_with_deadline(
        Path(__file__), list(argv), operation="ocr-hardscan-retry", timeout=timeout, config=config,
        warn_fn=warn, stdout_target=subprocess.DEVNULL, stderr_target=subprocess.DEVNULL)


def _print_report(report):
    summary = report["summary"]
    print(f"Hard-scan review: {summary['requested']} requested, {summary['candidates']} candidates, "
          f"{summary['empty']} empty, {summary['failed']} failed, {summary['abstained']} abstained.")
    print("Manual review required. Bow correction is a limited operator-declared model, not general "
          "page dewarping. Source, saved OCR and canonical extraction unchanged.")
    if summary["abstained"]:
        print("Inspect abstention reasons and geometry in the private report; do not assume the requested correction ran.")


def main(argv: list[str] | None = None) -> int:
    parser = _ArgumentParser(prog="retry_ocr_hardscan.py", description=__doc__, allow_abbrev=False)
    parser.add_argument("--pdf", required=True, type=Path, help="Original source-bound PDF; never modified")
    parser.add_argument("--recovery", required=True, type=Path, help="Saved page recovery JSON for context")
    parser.add_argument("--plan", required=True, type=Path, help="Explicit approved hard-scan plan, 1..20 crops")
    parser.add_argument("--output", required=True, type=Path, help="New private report; must not exist")
    parser.add_argument("--dpi", type=int, choices=(300, 400), default=300)
    parser.add_argument("--timeout-seconds", type=float,
                        help="Optional whole-worker deadline,1..86400 seconds; startup/cleanup additional")
    try:
        args = parser.parse_args(argv)
        from ocr_recovery import ReportCleanupError

        try:
            if args.timeout_seconds is None:
                report = recover_hardscan(args.pdf, args.recovery, args.plan, args.output, dpi=args.dpi)
            else:
                from ocr_recovery_supervision import validate_timeout

                timeout = validate_timeout(args.timeout_seconds)
                child = [f"--pdf={args.pdf}", f"--recovery={args.recovery}", f"--plan={args.plan}",
                         f"--output={args.output}", "--dpi", str(args.dpi)]
                print("Starting bounded hard-scan worker; native output is suppressed.", flush=True)
                status = run_with_deadline(child, timeout=timeout)
                if status == 124:
                    return 124
                if status == 130:
                    raise KeyboardInterrupt
                if status != 3:
                    raise RuntimeError("hard-scan worker did not complete its review")
                report = verify_request(load_report(args.output), pdf_path=args.pdf,
                                         recovery_path=args.recovery, plan_path=args.plan, dpi=args.dpi)
        except ReportCleanupError:
            print("Hard-scan report was created, but temporary-file cleanup failed. Inspect --output "
                  "before retrying.", file=sys.stderr)
            return 2
        _print_report(report)
        return 3
    except KeyboardInterrupt:
        print("Hard-scan review was cancelled. A completed report or private temporary files may remain; "
              "inspect --output before retrying. Original extraction unchanged.", file=sys.stderr)
        return 130
    except FileExistsError:
        print("Hard-scan output exists. Choose a new --output path.", file=sys.stderr)
        return 2
    except (ValueError, OSError, RuntimeError, ImportError):
        print("Hard-scan review could not complete. Check source/recovery/plan hashes, recipe assumptions, "
              "raster limits, permissions and verified local dependencies. A completed report or private "
              "temporary files may remain; inspect --output before retrying. See docs/ocr-hardscan.md.", file=sys.stderr)
        return 2


if __name__ == "__main__":
    raise SystemExit(main())
