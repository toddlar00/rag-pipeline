"""Evaluate OCR transcriptions without printing source text or file paths."""

from __future__ import annotations

import argparse
import sys
from pathlib import Path
from typing import Sequence


PROJECT_ROOT = Path(__file__).resolve().parents[1]
if str(PROJECT_ROOT) not in sys.path:
    sys.path.insert(0, str(PROJECT_ROOT))

from ocr_evaluation import evaluate_ocr_file  # noqa: E402


def _rate(value: float | None) -> str:
    return "n/a (empty reference)" if value is None else f"{value:.2%}"


def main(argv: Sequence[str] | None = None) -> int:
    parser = argparse.ArgumentParser(
        description=("Measure OCR character/word error against reference transcriptions. "
                     "Input is a v1 JSON object containing records with id, reference, "
                     "prediction, and optional critical_tokens. Output contains metrics only."))
    parser.add_argument("--input", required=True, type=Path, help="Reference and prediction JSON")
    parser.add_argument("--output", required=True, type=Path, help="Private atomic metrics JSON (distinct from input)")
    args = parser.parse_args(argv)
    try:
        report = evaluate_ocr_file(args.input, args.output)
    except ValueError as exc:
        # Validation errors from this evaluator are deliberately content-free.
        print(f"OCR evaluation failed: {exc}", file=sys.stderr)
        return 2
    except (OSError, RuntimeError):
        print("OCR evaluation could not read the input or publish the report. "
              "Check that paths are distinct, accessible, and contain no links or junctions.",
              file=sys.stderr)
        return 2
    summary = report["summary"]
    print(f"OCR evaluation: {summary['record_count']} records; "
          f"CER {_rate(summary['character']['error_rate'])}; "
          f"WER {_rate(summary['word']['error_rate'])}; "
          f"exact matches {summary['exact_match_count']}/{summary['record_count']}. "
          "Metrics report written.")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
