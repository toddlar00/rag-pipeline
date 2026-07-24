#!/usr/bin/env python3
"""Run disposable hard-kill and synced-publication recovery drills."""

from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path


PROJECT_ROOT = Path(__file__).resolve().parents[1]
if str(PROJECT_ROOT) not in sys.path:
    sys.path.insert(0, str(PROJECT_ROOT))

import operational_drills  # noqa: E402


def _parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        description=(
            "Run bounded operational recovery drills and publish one private, "
            "redacted evidence report."))
    parser.add_argument("--output-dir", type=Path, required=True)
    parser.add_argument("--ready-timeout", type=float, default=5.0)
    parser.add_argument("--kill-timeout", type=float, default=5.0)
    return parser


def main(argv: list[str] | None = None) -> int:
    args = _parser().parse_args(argv)
    try:
        report = operational_drills.run_operational_drills(
            args.output_dir, ready_timeout=args.ready_timeout,
            kill_timeout=args.kill_timeout)
    except Exception:
        print("Operational drills could not start or publish.", file=sys.stderr)
        return 2
    print(json.dumps(
        report.to_payload(), ensure_ascii=False, sort_keys=True,
        allow_nan=False))
    return 0 if report.status == "succeeded" else 1


if __name__ == "__main__":
    raise SystemExit(main())
