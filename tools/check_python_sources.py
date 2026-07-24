#!/usr/bin/env python3
"""Compile every Git-tracked Python source through one deterministic gate."""

from __future__ import annotations

import argparse
import os
import py_compile
import subprocess
import sys
import tempfile
from collections.abc import Iterable, Sequence
from pathlib import Path


PROJECT_ROOT = Path(__file__).resolve().parents[1]


class SourceGateError(RuntimeError):
    """A tracked-source inventory or compilation invariant failed."""


def parse_tracked_python_paths(output: bytes) -> tuple[Path, ...]:
    """Parse the NUL-delimited output of ``git ls-files -z -- *.py``."""
    if not output:
        raise SourceGateError("Git reported no tracked Python sources")
    if not output.endswith(b"\0"):
        raise SourceGateError("Git's tracked-source output was not NUL-terminated")

    paths: list[Path] = []
    seen: set[str] = set()
    for raw_path in output[:-1].split(b"\0"):
        if not raw_path:
            raise SourceGateError("Git's tracked-source output contained an empty path")
        relative_text = os.fsdecode(raw_path)
        relative = Path(relative_text)
        if (
            relative.is_absolute()
            or bool(relative.drive)
            or not relative.parts
            or any(part in {"", ".", ".."} for part in relative.parts)
            or relative.suffix != ".py"
        ):
            raise SourceGateError(
                f"Git reported an invalid Python source path: {relative_text!r}"
            )
        normalized = relative.as_posix()
        if normalized in seen:
            raise SourceGateError(
                f"Git reported a duplicate Python source path: {normalized!r}"
            )
        seen.add(normalized)
        paths.append(relative)

    return tuple(sorted(paths, key=lambda path: path.as_posix()))


def tracked_python_paths(root: Path = PROJECT_ROOT) -> tuple[Path, ...]:
    """Return the validated relative paths of all tracked Python sources."""
    result = subprocess.run(
        ["git", "ls-files", "-z", "--", "*.py"],
        cwd=root,
        check=False,
        stdout=subprocess.PIPE,
        stderr=subprocess.PIPE,
    )
    if result.returncode != 0:
        detail = os.fsdecode(result.stderr).strip()
        suffix = f": {detail}" if detail else ""
        raise SourceGateError(
            f"git ls-files failed with exit code {result.returncode}{suffix}"
        )
    return parse_tracked_python_paths(result.stdout)


def validate_source_paths(root: Path, relative_paths: Iterable[Path]) -> tuple[Path, ...]:
    """Resolve tracked paths without permitting missing or out-of-tree sources."""
    root = root.resolve(strict=True)
    validated: list[Path] = []
    resolved_sources: set[Path] = set()
    for relative in relative_paths:
        if (
            relative.is_absolute()
            or bool(relative.drive)
            or any(part == ".." for part in relative.parts)
        ):
            raise SourceGateError(f"Python source escapes the repository: {relative}")
        source = root / relative
        try:
            resolved = source.resolve(strict=True)
            resolved.relative_to(root)
        except (FileNotFoundError, OSError, ValueError) as exc:
            raise SourceGateError(
                f"Tracked Python source is missing or outside the repository: {relative}"
            ) from exc
        if not resolved.is_file():
            raise SourceGateError(f"Tracked Python source is not a file: {relative}")
        if resolved in resolved_sources:
            raise SourceGateError(
                f"Multiple tracked paths resolve to the same Python source: {relative}"
            )
        resolved_sources.add(resolved)
        validated.append(relative)
    if not validated:
        raise SourceGateError("No Python sources were available to compile")
    return tuple(validated)


def compile_python_sources(root: Path, relative_paths: Sequence[Path]) -> int:
    """Compile sources into a temporary tree and return the exact file count."""
    root = root.resolve(strict=True)
    paths = validate_source_paths(root, relative_paths)
    with tempfile.TemporaryDirectory(prefix="rag-python-compile-") as temp_name:
        compile_root = Path(temp_name)
        for relative in paths:
            cache_path = compile_root / relative.with_suffix(".pyc")
            cache_path.parent.mkdir(parents=True, exist_ok=True)
            try:
                py_compile.compile(
                    str(root / relative),
                    cfile=str(cache_path),
                    dfile=relative.as_posix(),
                    doraise=True,
                )
            except py_compile.PyCompileError as exc:
                raise SourceGateError(
                    f"Python compilation failed for {relative.as_posix()}: {exc.msg}"
                ) from exc
    return len(paths)


def validate(root: Path = PROJECT_ROOT) -> int:
    """Discover and compile the complete tracked Python inventory."""
    return compile_python_sources(root, tracked_python_paths(root))


def _parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        description="Compile every Git-tracked Python source"
    )
    parser.add_argument(
        "--root",
        type=Path,
        default=PROJECT_ROOT,
        help="Git worktree root (defaults to this repository)",
    )
    return parser


def main(argv: Sequence[str] | None = None) -> int:
    args = _parser().parse_args(argv)
    try:
        count = validate(args.root)
    except (OSError, SourceGateError) as exc:
        print(f"Python source gate failed: {exc}", file=sys.stderr)
        return 1
    print(f"Compiled {count} Git-tracked Python sources.")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
