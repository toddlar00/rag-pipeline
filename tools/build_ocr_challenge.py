"""Create an eight-page synthetic OCR challenge and fixed reference text."""

from __future__ import annotations

import argparse
from pathlib import Path
import sys

if __package__ in {None, ""}:
    sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from ocr_challenge import build_challenge


class _ArgumentParser(argparse.ArgumentParser):
    def error(self, message: str) -> None:
        self.print_usage(sys.stderr)
        self.exit(2, "Invalid challenge arguments. Use --help for supported options.\n")


def main(argv: list[str] | None = None) -> int:
    parser = _ArgumentParser(
        prog="build_ocr_challenge.py", description=__doc__,
        epilog="Exit 0: fixture created; 2: build failed; 130: cancelled. No OCR or model download.")
    parser.add_argument("--output-dir", required=True, type=Path,
                        help="New directory under an existing parent; must not already exist")
    args = parser.parse_args(argv)
    try:
        build_challenge(args.output_dir)
        print("Synthetic OCR challenge: 8 image-only pages created with fixed references "
              "and a manifest. No OCR was run.")
        print("Synthetic evidence only; review layout and use the same PDF/references "
              "for every run. See docs/ocr-accuracy.md.")
    except KeyboardInterrupt:
        print("Synthetic challenge build cancelled. Partial files may remain; "
              "inspect the output and choose a new directory before retrying.", file=sys.stderr)
        return 130
    except (OSError, ValueError, RuntimeError, ImportError):
        print("Synthetic challenge could not finish. Check the existing parent and installed "
              "locked PyMuPDF, OpenCV and NumPy dependencies. Output may contain partial files; "
              "inspect it and choose a new directory. See docs/ocr-accuracy.md.", file=sys.stderr)
        return 2
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
