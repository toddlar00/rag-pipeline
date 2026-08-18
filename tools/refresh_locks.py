#!/usr/bin/env python3
"""Regenerate all universal, hash-locked dependency environments with uv."""

from __future__ import annotations

import argparse
import shutil
import subprocess
import sys
from pathlib import Path


PROJECT_ROOT = Path(__file__).resolve().parents[1]
LOCK_SPECS = (
    ("requirements.txt", "requirements-core.lock", True),
    ("requirements-all.txt", "requirements-full.lock", True),
    ("requirements-test.txt", "requirements-test.lock", False),
    ("requirements-service.txt", "requirements-service.lock", False),
    ("requirements-smoke.txt", "requirements-smoke.lock", False),
    ("requirements-security.txt", "requirements-security.lock", False),
    ("requirements-lock-tools.txt", "requirements-lock-tools.lock", False),
)


def lock_commands(
        uv: str, *, upgrade: bool = False,
        upgrade_packages: tuple[str, ...] = ()) -> list[list[str]]:
    """Build deterministic commands for each committed lockfile."""
    commands: list[list[str]] = []
    for source, output, use_cpu_torch in LOCK_SPECS:
        command = [
            uv,
            "pip",
            "compile",
            source,
            "--universal",
            "--python-version",
            "3.10",
        ]
        if use_cpu_torch:
            command.extend(("--torch-backend", "cpu"))
        command.extend((
            "--emit-index-url",
            "--generate-hashes",
            "--output-file",
            output,
        ))
        if upgrade:
            command.append("--upgrade")
        for package in upgrade_packages:
            command.extend(("--upgrade-package", package))
        commands.append(command)
    return commands


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument(
        "--upgrade",
        action="store_true",
        help="refresh every compatible pin instead of preserving existing pins",
    )
    parser.add_argument(
        "--upgrade-package",
        action="append",
        default=[],
        metavar="NAME",
        help=(
            "refresh only the named package while preserving every other "
            "pin; repeatable"
        ),
    )
    args = parser.parse_args(argv)
    if args.upgrade and args.upgrade_package:
        print(
            "--upgrade already refreshes everything; drop --upgrade-package "
            "or use it alone",
            file=sys.stderr,
        )
        return 2
    uv = shutil.which("uv")
    if uv is None:
        print(
            "uv is required; install requirements-lock-tools.lock first",
            file=sys.stderr,
        )
        return 2
    try:
        for command in lock_commands(
                uv, upgrade=args.upgrade,
                upgrade_packages=tuple(args.upgrade_package)):
            subprocess.run(command, cwd=PROJECT_ROOT, check=True)
    except subprocess.CalledProcessError as exc:
        return exc.returncode or 1
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
