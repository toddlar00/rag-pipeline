#!/usr/bin/env python3
"""Evaluate explicit OCR structural annotations over a fixed approved cohort."""

from __future__ import annotations

import argparse
from pathlib import Path
import sys


ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))


class _Parser(argparse.ArgumentParser):
    def error(self, message):
        raise ValueError("invalid structural evaluation arguments")


def main(argv=None) -> int:
    try:
        parser = _Parser(description=__doc__, allow_abbrev=False)
        parser.add_argument("--cohort", type=Path, required=True, help="Approved v1 cohort with isolated calibration/held_out families")
        parser.add_argument("--predictions", type=Path, required=True, help="Complete v1 structure predictions bound to the cohort digest")
        parser.add_argument("--output", type=Path, required=True, help="New private metrics report; existing files are refused")
        args = parser.parse_args(argv)
        from ocr_benchmark_io import evaluate_structure_files
        from ocr_recovery import ReportCleanupError

        try:
            report = evaluate_structure_files(args.cohort, args.predictions, args.output)
        except ReportCleanupError:
            print("Structural report may already exist; staging cleanup failed. Inspect the chosen output before retrying.", file=sys.stderr)
            return 2
        counts = report["summary"]
        print(f"Structural evaluation: {counts['calibration']['records']} calibration and "
              f"{counts['held_out']['records']} held-out records. Private metrics report created. "
              "Correspondences and holdout status are operator declarations, not authenticated ground truth.")
        return 3 if report["requires_attention"] else 0
    except KeyboardInterrupt:
        print("Structural evaluation cancelled; an output may already exist. Inspect the chosen output before retrying.", file=sys.stderr)
        return 130
    except Exception:
        print("Structural evaluation failed. Check strict schemas, complete cohort bindings, isolated splits, "
              "distinct unlinked input paths, and a new output path. No input was changed. "
              "An output may already exist after a late failure; inspect the chosen output before retrying.", file=sys.stderr)
        return 2


if __name__ == "__main__":
    raise SystemExit(main())
