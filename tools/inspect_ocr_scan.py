#!/usr/bin/env python3
"""Inspect original scan pixels for independent, unconfirmed ink-coverage warnings."""

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
        raise ValueError("invalid scan diagnostic arguments")


def _warning(_message=None):
    print("Scan inspection did not finish normally. A private complete or incomplete bundle may remain; "
          "inspect --output-dir before retrying. No canonical extraction was changed.", file=sys.stderr)


def _print_summary(bundle):
    scan = bundle["scan"]
    count, requested = scan["page_count"], scan["requested_pages"]
    if (type(count) is not int or not 1 <= count <= 5000 or type(requested) is not list
            or not 1 <= len(requested) <= 8 or any(type(n) is not int or not 1 <= n <= count for n in requested)
            or requested != sorted(set(requested)) or type(scan["pages"]) is not list
            or [page["page_number"] for page in scan["pages"]] != requested
            or bundle["manifest"]["requires_attention"] is not True):
        raise ValueError("invalid scan coverage summary")
    print(f"Scan bundle verified: {len(requested)} explicitly selected pages of {count} source pages.")
    print("Independent ink geometry is an unconfirmed review aid. Threshold blankness and overlapping saved boxes "
          "do not establish text completeness. No OCR, model loading, or canonical changes.")


def main(argv: list[str] | None = None) -> int:
    try:
        parser = _Parser(prog="inspect_ocr_scan.py", description=__doc__, allow_abbrev=False,
            epilog="Fixed 300 DPI; 1 to 8 explicit pages, bounded original rasters. Exit 3: completed manual-review bundle; "
                   "2: failure; 124: deadline; 130: cancelled. No OCR/models/downloads.")
        parser.add_argument("--pdf", type=Path, required=True, help="Exact original source; bounded at 256 MiB")
        parser.add_argument("--pages", type=int, nargs="+", required=True, help="1 to 8 distinct one-based page numbers")
        parser.add_argument("--output-dir", type=Path, required=True, help="New private bundle directory under an existing parent")
        parser.add_argument("--recovery", type=Path, help="Optional strict source-bound saved recovery v1/v2")
        parser.add_argument("--proposals", type=Path, help="Optional strict Docling proposals; requires --recovery")
        parser.add_argument("--installation", type=Path, help="Optional qualified installation evidence for this interpreter")
        parser.add_argument("--recipe", choices=("legacy-v1", "spatial-v2"), default="legacy-v1",
                            help="Fixed discovery recipe; spatial-v2 is explicit opt-in")
        parser.add_argument("--timeout-seconds", type=int, default=600, help="Contained deadline, 1 through 3600 seconds")
        parser.add_argument("--worker", action="store_true", help=argparse.SUPPRESS)
        args = parser.parse_args(argv)
        if (not 1 <= args.timeout_seconds <= 3600 or not 1 <= len(args.pages) <= 8
                or any(not 1 <= n <= 5000 for n in args.pages) or len(set(args.pages)) != len(args.pages)
                or (args.proposals is not None and args.recovery is None)):
            raise ValueError("scan request exceeds bounds or lacks a required binding")
        from ocr_scan_io import read_scan_completion, run_scan_bundle, scan_request

        options = {"requested_pages": sorted(args.pages), "recovery_path": args.recovery,
                   "proposals_path": args.proposals, "installation_path": args.installation}
        if args.recipe != "legacy-v1":
            options["recipe"] = args.recipe
        if args.worker:
            if os.environ.get("RAG_OCR_SCAN_CHILD") != "1":
                raise ValueError("scan worker requires containment")
            run_scan_bundle(args.pdf, args.output_dir, **options)
            return 3
        request = scan_request(args.pdf, args.output_dir, **options)
        import process_supervision

        arguments = [f"--pdf={args.pdf}", f"--output-dir={args.output_dir}",
                     "--pages", *(str(n) for n in options["requested_pages"])]
        for name, path in (("recovery", args.recovery), ("proposals", args.proposals), ("installation", args.installation)):
            if path is not None:
                arguments.append(f"--{name}={path}")
        if args.recipe != "legacy-v1":
            arguments.append(f"--recipe={args.recipe}")
        arguments.append("--worker")
        config = process_supervision.SupervisionConfig("RAG_OCR_SCAN_CHILD", "RAG_OCR_SCAN_RUN", 5., .2, 30.)
        print("Starting contained original-scan inspection; native worker output is suppressed.", flush=True)
        code = process_supervision._run_cli_with_deadline(
            Path(__file__), arguments, operation="ocr-scan-inspection", timeout=float(args.timeout_seconds),
            config=config, environment_overrides={"RAG_OCR_SCAN_CHILD": "1", "HF_HUB_OFFLINE": "1",
                "TRANSFORMERS_OFFLINE": "1", "HF_HUB_DISABLE_TELEMETRY": "1", "DO_NOT_TRACK": "1", "PYTHONNOUSERSITE": "1"},
            stdout_target=subprocess.DEVNULL, stderr_target=subprocess.DEVNULL, warn_fn=_warning)
        if type(code) is not int:
            raise RuntimeError("scan worker returned an invalid exit status")
        if code in (124, 130):
            _warning()
            return code
        if code != 3:
            raise RuntimeError("scan worker did not publish completion")
        bundle = read_scan_completion(args.output_dir, request=request)
        _print_summary(bundle)
        return 3
    except KeyboardInterrupt:
        _warning()
        return 130
    except Exception:
        print("Scan inspection failed. Check exact source/comparison bindings, locked native packages and output permissions. "
              "A private complete or incomplete bundle may remain; inspect --output-dir and choose a new directory before retrying.",
              file=sys.stderr)
        return 2


if __name__ == "__main__":
    raise SystemExit(main())
