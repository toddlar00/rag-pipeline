#!/usr/bin/env python3
"""Audit actual core normalization rules on saved OCR fragments without accepting edits."""

from __future__ import annotations

import argparse
from pathlib import Path
import sys

if __package__ in {None, ""}:
    sys.path.insert(0, str(Path(__file__).resolve().parents[1]))


class _ArgumentParser(argparse.ArgumentParser):
    def error(self, message: str) -> None:
        self.print_usage(sys.stderr)
        self.exit(2, "Cleanup audit arguments are invalid. Use --help; see docs/cleanup-audit.md.\n")


def audit_cleanup_files(pdf_path: Path, recovery_path: Path, plan_path: Path, output_path: Path) -> dict:
    from cleanup_audit_io import audit_cleanup_files as audit

    return audit(pdf_path, recovery_path, plan_path, output_path)


def main(argv: list[str] | None = None) -> int:
    try:
        parser = _ArgumentParser(prog="audit_cleanup.py", description=__doc__, allow_abbrev=False,
                                 epilog="Exit 0: unchanged fully selected candidate text; "
                                        "3: changes/warnings/abstention/coverage gaps; 2: failure; 130: cancelled.")
        for flag, help_text in (
                ("pdf", "Exact source PDF; hashed only, never parsed"),
                ("recovery", "Strict saved OCR recovery v1/v2 JSON"),
                ("plan", "Exact-source/recovery-bound core cleanup span plan"),
                ("output", "New private audit report path; must not exist")):
            parser.add_argument("--" + flag, required=True, type=Path, help=help_text)
        args = parser.parse_args(argv)
        from ocr_recovery import ReportCleanupError

        try:
            report = audit_cleanup_files(args.pdf, args.recovery, args.plan, args.output)
        except ReportCleanupError:
            print("Cleanup audit report was created, but temporary-file cleanup failed. "
                  "Inspect --output before retrying.", file=sys.stderr)
            return 2
        summary, coverage = report["summary"], report["coverage"]
        print(f"Cleanup audit: {summary['changed']} changed, {summary['unchanged']} unchanged, "
              f"{summary['abstained']} abstained; {summary['changed_steps']} observed changed steps.")
        print(f"Potential-risk entries: {summary['risk_entries']}. Candidate coverage: "
              f"{len(coverage['fully_selected_candidate_pages'])} fully selected of "
              f"{coverage['source_page_count']} source pages; "
              f"{len(coverage['unselected_recovery_pages'])} recovery pages unselected; "
              f"{len(coverage['deferred_pages'])} deferred.")
        print("Core normalization only; source-aware wrappers and chunk deduplication are excluded. "
              "Changes and heuristic warnings do not prove errors or preserved meaning. "
              "No source, canonical extraction or index was changed. See docs/cleanup-audit.md.")
        return 3 if report["requires_attention"] else 0
    except KeyboardInterrupt:
        print("Cleanup audit cancelled. A report may already exist; inspect --output before retrying.", file=sys.stderr)
        return 130
    except (ValueError, OSError, RuntimeError, ImportError):
        print("Cleanup audit could not finish. Check bounded strict JSON, source/report bindings, selected "
              "spans and output permissions. A report may already exist; inspect --output before retrying. "
              "See docs/cleanup-audit.md.", file=sys.stderr)
        return 2


if __name__ == "__main__":
    raise SystemExit(main())
