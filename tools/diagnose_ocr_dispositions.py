#!/usr/bin/env python3
"""Explain observed detection outcomes from the same bounded OCR execution."""

from __future__ import annotations

import argparse
import os
from pathlib import Path
import subprocess
import sys


ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))


class _Parser(argparse.ArgumentParser):
    def error(self, message):
        raise ValueError("invalid OCR disposition arguments")


def _warning(_message=None):
    print("OCR disposition diagnostics did not finish normally. A private complete or incomplete bundle may remain; "
          "inspect the chosen directory before retrying. No canonical extraction was changed.", file=sys.stderr)


def _print_summary(bundle):
    summary = bundle["disposition"]["summary"]
    print(f"OCR disposition bundle verified: {summary['covered_items']} covered items, "
          f"{summary['raw_calls']} actual OCR calls.")
    print(f"Observed detections: {summary['known_detections']}; blank removals: {summary['removed_blank']}; "
          f"score removals: {summary['removed_score']}; retained: {summary['retained']}; "
          f"unresolved: {summary['unresolved']}.")
    print(f"Diagnostics: {summary['diagnostics_complete']} complete, {summary['diagnostics_partial']} partial, "
          f"{summary['diagnostics_unavailable']} unavailable, {summary['diagnostics_not_run']} not run.")
    print("These counts describe observed engine outcomes, not complete text capture or accuracy. "
          "Candidates still require source review; no correction was adopted.")


def main(argv=None):
    try:
        parser = _Parser(prog="diagnose_ocr_dispositions.py", description=__doc__, allow_abbrev=False,
                         epilog="Uses characterized local runtime/model bytes without downloads. "
                         "New bundles only; checkpoint resume is not supported. Exit 3 means review is required.")
        parser.add_argument("--pdf", type=Path, required=True, help="Exact source PDF; content remains private")
        parser.add_argument("--output-dir", type=Path, required=True, help="New private bundle, or existing bundle with --verify-only")
        parser.add_argument("--operation", choices=("pages", "regions", "hardscan"), default="pages")
        parser.add_argument("--dpi", type=int, default=300, help="Page DPI 72..600; region/hard-scan DPI 300 or 400")
        parser.add_argument("--page", type=int, action="append", default=[], help="Explicit page request; repeat at most 20 times")
        parser.add_argument("--max-pages", type=int, default=5)
        parser.add_argument("--min-chars", type=int, default=40)
        parser.add_argument("--max-pixels", type=int, default=25_000_000)
        parser.add_argument("--max-side", type=int, default=6000)
        parser.add_argument("--preprocessing", choices=("none", "deskew", "contrast", "deskew-contrast"), default="none")
        parser.add_argument("--evidence", type=Path, help="Optional exact-source page selection evidence")
        parser.add_argument("--recovery", type=Path, help="Saved source-bound recovery required for crop operations")
        parser.add_argument("--plan", type=Path, help="Explicit source/recovery-bound region or hard-scan plan")
        parser.add_argument("--installation", type=Path, help="Optional completed evidence for this exact qualified interpreter")
        parser.add_argument("--timeout-seconds", type=int, default=600, help="Contained worker deadline, 1..3600 seconds")
        parser.add_argument("--verify-only", action="store_true", help="Validate existing complete bundle against current inputs; no OCR")
        parser.add_argument("--worker", action="store_true", help=argparse.SUPPRESS)
        parser.add_argument("--expected-request-sha256", help=argparse.SUPPRESS)
        args = parser.parse_args(argv)
        if not 1 <= args.timeout_seconds <= 3600:
            raise ValueError("OCR disposition deadline outside bounds")
        if (args.worker and args.verify_only) or (not args.worker and args.expected_request_sha256 is not None):
            raise ValueError("invalid disposition worker mode")
        from ocr_detection_disposition_io import disposition_request, read_disposition_completion, run_disposition_bundle
        from ocr_recovery import RetryPolicy

        options = dict(operation=args.operation,
                       policy=RetryPolicy(dpi=args.dpi, max_pages=args.max_pages, min_chars=args.min_chars,
                                          max_pixels=args.max_pixels, max_side=args.max_side, preprocessing=args.preprocessing),
                       requested_pages=tuple(args.page), evidence_path=args.evidence, recovery_path=args.recovery,
                       plan_path=args.plan, installation_path=args.installation)
        if args.worker:
            if os.environ.get("RAG_OCR_DISPOSITION_CHILD") != "1" or args.expected_request_sha256 is None:
                raise ValueError("OCR disposition worker requires its contained parent request")
            run_disposition_bundle(args.pdf, args.output_dir, expected_request_sha256=args.expected_request_sha256, **options)
            return 3
        request = disposition_request(args.pdf, args.output_dir, readback=args.verify_only, **options)
        if args.verify_only:
            _print_summary(read_disposition_completion(args.output_dir, request=request))
            return 3
        import process_supervision

        arguments = [f"--pdf={args.pdf}", f"--output-dir={args.output_dir}", f"--operation={args.operation}",
                     f"--dpi={args.dpi}", f"--max-pages={args.max_pages}", f"--min-chars={args.min_chars}",
                     f"--max-pixels={args.max_pixels}", f"--max-side={args.max_side}", f"--preprocessing={args.preprocessing}"]
        arguments.extend(f"--page={page}" for page in args.page)
        for name in ("evidence", "recovery", "plan", "installation"):
            if getattr(args, name) is not None:
                arguments.append(f"--{name}={getattr(args, name)}")
        arguments.extend((f"--expected-request-sha256={request.sha256}", "--worker"))
        config = process_supervision.SupervisionConfig("RAG_OCR_DISPOSITION_CHILD", "RAG_OCR_DISPOSITION_RUN", 5., .2, 30.)
        print("Starting contained same-call OCR diagnostics; native worker output is suppressed.", flush=True)
        code = process_supervision._run_cli_with_deadline(
            Path(__file__), arguments, operation="ocr-disposition-diagnostics", timeout=float(args.timeout_seconds),
            config=config, environment_overrides={"RAG_OCR_DISPOSITION_CHILD": "1", "HF_HUB_OFFLINE": "1",
                "TRANSFORMERS_OFFLINE": "1", "HF_HUB_DISABLE_TELEMETRY": "1", "DO_NOT_TRACK": "1", "PYTHONNOUSERSITE": "1"},
            stdout_target=subprocess.DEVNULL, stderr_target=subprocess.DEVNULL, warn_fn=_warning)
        if code in (124, 130):
            _warning()
            return code
        if code != 3:
            raise RuntimeError("OCR disposition worker did not publish completion")
        _print_summary(read_disposition_completion(args.output_dir, request=request))
        return 3
    except KeyboardInterrupt:
        _warning()
        return 130
    except Exception:
        print("OCR disposition diagnostics failed. Check matching source, plan and runtime evidence; "
              "choose a new output directory for execution. A private complete or incomplete bundle may remain. "
              "No canonical extraction was changed.", file=sys.stderr)
        return 2


if __name__ == "__main__":
    raise SystemExit(main())
