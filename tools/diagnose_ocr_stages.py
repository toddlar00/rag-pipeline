#!/usr/bin/env python3
"""Run bounded OCR-stage diagnostics against declared gold line annotations."""

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
        raise ValueError("invalid stage diagnostic arguments")


def _warning(_message=None):
    print("OCR stage diagnostics did not finish normally. A private complete or incomplete bundle may remain; "
          "inspect the chosen directory before retrying. No canonical extraction was changed.", file=sys.stderr)


def _print_summary(bundle):
    coverage = bundle["diagnostics"]["coverage"]
    print(f"OCR stage bundle verified: {len(coverage['selected_pages'])} selected pages, "
          f"{coverage['reference_lines']} declared gold lines.")
    print(f"Gold-crop recognition: {coverage['available_gold_crops']}/{coverage['selected_gold_crops']} observed; "
          f"{coverage['paired_full_and_gold_crops']} paired full-page line comparisons.")
    print("Detector geometry and recognition-only crops are separate observations, not an additive causal explanation. "
          "Declared gold and accuracy conclusions require review. See docs/ocr-stage-diagnostics.md.")


def main(argv=None) -> int:
    try:
        parser = _Parser(prog="diagnose_ocr_stages.py", description=__doc__, allow_abbrev=False,
                         epilog="Fixed 300 DPI, original displayed geometry, at most 8 pages and 64 gold line crops. "
                         "Requires qualified local installation evidence; no model downloads. See docs/ocr-stage-diagnostics.md.")
        parser.add_argument("--pdf", type=Path, required=True, help="Exact original source bound by the gold reference")
        parser.add_argument("--references", type=Path, required=True, help="Strict source-bound gold line/cell annotations")
        parser.add_argument("--output-dir", type=Path, required=True, help="New private bundle directory; manifest published last")
        parser.add_argument("--installation", type=Path, required=True, help="Completed evidence for this exact qualified interpreter")
        parser.add_argument("--timeout-seconds", type=int, default=600, help="Worker deadline, 1 through 3600 seconds (default 600)")
        parser.add_argument("--worker", action="store_true", help=argparse.SUPPRESS)
        args = parser.parse_args(argv)
        if not 1 <= args.timeout_seconds <= 3600:
            raise ValueError("stage deadline outside bounds")
        from ocr_stage_io import read_stage_completion, run_stage_bundle, stage_request

        options = {"installation_path": args.installation}
        if args.worker:
            if os.environ.get("RAG_OCR_STAGE_CHILD") != "1":
                raise ValueError("stage worker requires containment")
            run_stage_bundle(args.pdf, args.references, args.output_dir, **options)
            return 3
        request = stage_request(args.pdf, args.references, args.output_dir, **options)
        import process_supervision

        arguments = [f"--pdf={args.pdf}", f"--references={args.references}", f"--output-dir={args.output_dir}",
                     f"--installation={args.installation}", "--worker"]
        config = process_supervision.SupervisionConfig("RAG_OCR_STAGE_CHILD", "RAG_OCR_STAGE_RUN", 5., .2, 30.)
        print("Starting contained OCR stage diagnostics; native worker output is suppressed.", flush=True)
        code = process_supervision._run_cli_with_deadline(
            Path(__file__), arguments, operation="ocr-stage-diagnostics", timeout=float(args.timeout_seconds),
            config=config, environment_overrides={"RAG_OCR_STAGE_CHILD": "1", "HF_HUB_OFFLINE": "1",
                "TRANSFORMERS_OFFLINE": "1", "HF_HUB_DISABLE_TELEMETRY": "1", "DO_NOT_TRACK": "1", "PYTHONNOUSERSITE": "1"},
            stdout_target=subprocess.DEVNULL, stderr_target=subprocess.DEVNULL, warn_fn=_warning)
        if code in (124, 130):
            _warning()
            return code
        if code != 3:
            raise RuntimeError("stage worker did not publish completion")
        bundle = read_stage_completion(args.output_dir, request=request)
        _print_summary(bundle)
        return 3
    except KeyboardInterrupt:
        _warning()
        return 130
    except Exception:
        print("OCR stage diagnostics failed. Check source/reference/installation bindings and choose a new output directory. "
              "A private complete or incomplete bundle may remain. See docs/ocr-stage-diagnostics.md.", file=sys.stderr)
        return 2


if __name__ == "__main__":
    raise SystemExit(main())
