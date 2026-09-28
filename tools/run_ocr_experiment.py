#!/usr/bin/env python3
"""Run an OCR experiment in a contained worker and publish provenance last."""

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
        raise ValueError("invalid OCR execution arguments")


def main(argv=None) -> int:
    try:
        arguments = list(sys.argv[1:] if argv is None else argv)
        parser = _Parser(description=__doc__, allow_abbrev=False)
        parser.add_argument("--pdf", type=Path, required=True)
        parser.add_argument("--output-dir", type=Path, required=True, help="New private bundle directory; completion manifest is written last")
        parser.add_argument("--operation", choices=("pages", "regions", "hardscan"), default="pages")
        parser.add_argument("--dpi", type=int, choices=(300, 400), default=300)
        parser.add_argument("--preprocessing", choices=("none", "deskew", "contrast", "deskew-contrast"), default="none")
        parser.add_argument("--page", type=int, action="append", default=[])
        parser.add_argument("--max-pages", type=int, default=5)
        parser.add_argument("--evidence", type=Path)
        parser.add_argument("--recovery", type=Path)
        parser.add_argument("--plan", type=Path)
        parser.add_argument("--installation", type=Path, help="Completed installation evidence for this exact interpreter environment")
        parser.add_argument("--timeout-seconds", type=int, default=600)
        parser.add_argument("--checkpoint", action="store_true", help="Opt in to durable page, region or hard-scan checkpoints")
        parser.add_argument("--resume", action="store_true", help="Resume the same checkpoint directory and exact inputs/runtime; requires --checkpoint")
        parser.add_argument("--worker", action="store_true", help=argparse.SUPPRESS)
        args = parser.parse_args(arguments)
        if not 1 <= args.timeout_seconds <= 3600:
            raise ValueError("execution deadline is outside bounds")
        if args.resume and not args.checkpoint:
            raise ValueError("resume requires checkpoint mode")
        if args.checkpoint and args.operation not in ("pages", "regions", "hardscan"):
            raise ValueError("checkpoints support page, region and hardscan operations only")
        from ocr_execution_io import execution_request, read_execution_completion, run_execution_bundle
        from ocr_recovery import RetryPolicy
        request = dict(operation=args.operation,
                       policy=RetryPolicy(dpi=args.dpi, max_pages=args.max_pages, preprocessing=args.preprocessing),
                       requested_pages=tuple(args.page), evidence_path=args.evidence,
                       recovery_path=args.recovery, plan_path=args.plan, installation_path=args.installation)
        if args.worker:
            if os.environ.get("RAG_OCR_EXECUTION_CHILD") != "1":
                raise ValueError("execution worker requires containment")
            if args.checkpoint:
                if args.operation == "pages":
                    from ocr_checkpoint_io import run_checkpointed_pages as run_checkpointed
                elif args.operation == "regions":
                    from ocr_region_checkpoint_io import run_checkpointed_regions as run_checkpointed
                elif args.operation == "hardscan":
                    from ocr_hardscan_checkpoint_io import run_checkpointed_hardscan as run_checkpointed
                run_checkpointed(args.pdf, args.output_dir, resume=args.resume, **request)
            else:
                run_execution_bundle(args.pdf, args.output_dir, **request)
            return 3
        import process_supervision
        from evaluation_inputs import _read_snapshot
        if args.checkpoint:
            if args.operation == "pages":
                from ocr_checkpoint_io import checkpoint_request, read_checkpoint_completion
            elif args.operation == "regions":
                from ocr_region_checkpoint_io import (
                    region_checkpoint_request as checkpoint_request,
                    read_region_checkpoint_completion as read_checkpoint_completion)
            elif args.operation == "hardscan":
                from ocr_hardscan_checkpoint_io import (
                    hardscan_checkpoint_request as checkpoint_request,
                    read_hardscan_checkpoint_completion as read_checkpoint_completion)
            from ocr_checkpoint_runtime import bounded_snapshot
            _, _, _, specs, expected_request = checkpoint_request(args.pdf, args.output_dir, resume=args.resume, **request)
            expected_inputs = expected_request["inputs"]
        else:
            _, _, _, specs, expected_inputs, expected_configuration = execution_request(args.pdf, args.output_dir, **request)

        config = process_supervision.SupervisionConfig("RAG_OCR_EXECUTION_CHILD", "RAG_OCR_EXECUTION_RUN", 5., .2, 30.)
        def warning(_message):
            print("OCR execution worker did not finish normally. A report, incomplete bundle, or private scratch may remain; inspect the chosen directory.", file=sys.stderr)
        overrides = {"RAG_OCR_EXECUTION_CHILD": "1", "HF_HUB_OFFLINE": "1", "TRANSFORMERS_OFFLINE": "1",
                     "HF_HUB_DISABLE_TELEMETRY": "1", "DO_NOT_TRACK": "1", "PYTHONNOUSERSITE": "1"}
        print("Starting contained OCR execution; native worker output is suppressed.", flush=True)
        code = process_supervision._run_cli_with_deadline(
            Path(__file__), arguments + ["--worker"], operation="ocr-execution", timeout=float(args.timeout_seconds),
            config=config, environment_overrides=overrides, stdout_target=subprocess.DEVNULL,
            stderr_target=subprocess.DEVNULL, warn_fn=warning)
        if code in (124, 130):
            warning("")
            return code
        if code != 3:
            raise RuntimeError("execution worker did not complete")
        for path, key, limit in specs:
            digest = (bounded_snapshot(path, limit)[1] if args.checkpoint
                      else _read_snapshot(path, label="execution input", max_bytes=limit)[1])
            if digest != expected_inputs[key]:
                raise ValueError("execution input changed")
        if args.checkpoint:
            read_checkpoint_completion(args.output_dir, request=expected_request, specs=specs,
                                       installation_path=args.installation)
            item_kind = "page" if args.operation == "pages" else "region"
            checkpoint_kind = "hard-scan region" if args.operation == "hardscan" else item_kind
            print(f"OCR {checkpoint_kind} checkpoint generation completed with retained per-{item_kind} origin evidence. Interrupted attempts are not retried; accuracy review remains required.")
        else:
            read_execution_completion(args.output_dir, inputs=expected_inputs, configuration=expected_configuration,
                                      pdf_path=args.pdf, recovery_path=args.recovery, plan_path=args.plan,
                                      installation_path=args.installation)
            print("OCR execution bundle created with report and effective runtime receipt. Accuracy review remains required.")
        return 3
    except KeyboardInterrupt:
        print("OCR execution cancelled. An incomplete or completed private bundle may remain; inspect the chosen directory.", file=sys.stderr)
        return 130
    except Exception:
        print("OCR execution failed. Check matching source/plan/installation evidence and a new output directory. "
              "For explicit checkpoint resume, keep the same generation inputs and runtime. "
              "A report, incomplete bundle, or private scratch may remain; no canonical extraction was changed.", file=sys.stderr)
        return 2


if __name__ == "__main__":
    raise SystemExit(main())
