"""Launch the opinionated Civil Procedure pipeline from this repository."""

import subprocess
import sys
from pathlib import Path


def main() -> int:
    workspace = Path(__file__).resolve().parent
    command = [
        sys.executable,
        str(workspace / "rag.py"),
        "full",
        "--pdf",
        str(workspace / "Civil Procedure I.pdf"),
        "--llm-classify",
        "--contextualize",
        "--reconstruct-headings",
        "--quality-score",
        "--llm-scaffold",
        "--raptor",
        "--split-chapters",
        "--llm-workers",
        "10",
        "--force",
    ]
    return subprocess.run(command, cwd=workspace, check=False).returncode


if __name__ == "__main__":
    raise SystemExit(main())
