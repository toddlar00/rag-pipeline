#!/usr/bin/env python3
"""Capture OCR runtime version agreement without claiming artifact attestation."""

from __future__ import annotations

import argparse
from pathlib import Path
import sys


ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))


class _Parser(argparse.ArgumentParser):
    def error(self, message):
        raise ValueError("invalid runtime inspection arguments")


def main(argv=None) -> int:
    try:
        parser = _Parser(description=__doc__, allow_abbrev=False)
        parser.add_argument("--lock", type=Path, required=True, help="Generated hash-locked dependency file")
        parser.add_argument("--output", type=Path, required=True, help="New private runtime manifest")
        parser.add_argument("--package", action="append", help="Package to inspect; repeat for each requested package")
        parser.add_argument("--effective-runtime", type=Path, help="Optional strict actual-execution observation JSON; never inferred")
        parser.add_argument("--require-version-match", action="store_true", help="Refuse publication on requested-package version disagreement (not wheel verification)")
        args = parser.parse_args(argv)
        from ocr_benchmark_io import inspect_runtime_file
        from ocr_experiment_runtime import DEFAULT_OCR_PACKAGES
        from ocr_recovery import ReportCleanupError

        try:
            report = inspect_runtime_file(args.lock, args.output, effective_path=args.effective_runtime,
                                          packages=args.package if args.package is not None else DEFAULT_OCR_PACKAGES,
                                          require_version_match=args.require_version_match)
        except ReportCleanupError:
            print("Runtime manifest may already exist; staging cleanup failed. Inspect the chosen output before retrying.", file=sys.stderr)
            return 2
        mismatch = sum(item["status"] != "version_match" or item["loaded_version_agrees"] is False
                       for item in report["packages"])
        print(f"Runtime inspection: {len(report['packages'])} requested packages; {mismatch} version checks need attention. "
              "Private manifest created. Wheel, model, and loaded native-library bytes remain unverified by this capture.")
        return 3 if report["requires_attention"] else 0
    except KeyboardInterrupt:
        print("Runtime inspection cancelled; an output may already exist. Inspect the chosen output before retrying.", file=sys.stderr)
        return 130
    except Exception:
        print("Runtime inspection failed. Check the bounded generated lock, package selection, execution-observation schema, "
              "distinct unlinked inputs, version gate, and a new output path. No input was changed. "
              "An output may already exist after a late failure; inspect the chosen output before retrying.", file=sys.stderr)
        return 2


if __name__ == "__main__":
    raise SystemExit(main())
