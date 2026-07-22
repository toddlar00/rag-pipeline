#!/usr/bin/env python3
"""Validate direct dependency bounds and reproducible automation pins."""

from __future__ import annotations

import argparse
import re
import sys
from collections.abc import Iterable
from pathlib import Path


PROJECT_ROOT = Path(__file__).resolve().parents[1]
BOUNDED_FILES = (
    "requirements.txt",
    "requirements-optional.txt",
    "requirements-smoke.txt",
)
BOUNDED_INCLUDES = {
    "requirements.txt": (),
    "requirements-optional.txt": (),
    "requirements-smoke.txt": ("requirements-test.txt",),
}
PINNED_FILES = (
    "requirements-audit.txt",
    "requirements-test.txt",
    "requirements-security.txt",
    "requirements-lock-tools.txt",
)
LOCK_FILES = {
    "requirements-core.lock": ("requirements.txt",),
    "requirements-full.lock": (
        "requirements.txt",
        "requirements-optional.txt",
    ),
    "requirements-test.lock": ("requirements-test.txt",),
    "requirements-smoke.lock": (
        "requirements-test.txt",
        "requirements-smoke.txt",
    ),
    "requirements-security.lock": ("requirements-security.txt",),
    "requirements-lock-tools.lock": ("requirements-lock-tools.txt",),
}
AGGREGATE_FILES = {
    "requirements-all.txt": (
        "requirements.txt",
        "requirements-optional.txt",
    ),
}
_REQUIREMENT_RE = re.compile(
    r"^(?P<name>[A-Za-z0-9][A-Za-z0-9_.-]*"
    r"(?:\[[A-Za-z0-9_,.-]+\])?)\s*(?P<spec>.*)$"
)
_SPECIFIER_RE = re.compile(r"(===|==|~=|!=|<=|>=|<|>)\s*([^,\s]+)")


def _logical_lines(path: Path) -> Iterable[tuple[int, str]]:
    for line_number, raw_line in enumerate(
        path.read_text(encoding="utf-8").splitlines(), 1
    ):
        line = raw_line.split("#", 1)[0].strip()
        if line:
            yield line_number, line


def _requirement(
    path: Path, line_number: int, line: str
) -> tuple[str, list[tuple[str, str]]] | None:
    if line.startswith(("-r ", "--requirement ")):
        return None
    if line.startswith("-") or " @ " in line or "://" in line:
        raise ValueError(
            f"{path.name}:{line_number}: direct URLs and installer options "
            "are not allowed"
        )
    requirement = line.split(";", 1)[0].strip()
    match = _REQUIREMENT_RE.fullmatch(requirement)
    if not match:
        raise ValueError(
            f"{path.name}:{line_number}: cannot parse requirement {line!r}"
        )
    spec = match.group("spec").strip()
    specifiers = _SPECIFIER_RE.findall(spec)
    reconstructed = ",".join(
        f"{operator}{version}" for operator, version in specifiers
    )
    if not specifiers or reconstructed.replace(" ", "") != spec.replace(" ", ""):
        raise ValueError(
            f"{path.name}:{line_number}: requirement needs explicit version "
            f"specifiers: {line!r}"
        )
    return match.group("name"), specifiers


def _include_target(line: str) -> str | None:
    for prefix in ("-r ", "--requirement "):
        if line.startswith(prefix):
            return line[len(prefix):].strip()
    return None


def _lock_lines(path: Path) -> Iterable[tuple[int, str]]:
    """Yield complete requirement records from a hashed compiled lockfile."""
    pending = ""
    start_line = 0
    for line_number, raw_line in enumerate(
        path.read_text(encoding="utf-8").splitlines(), 1
    ):
        line = raw_line.split("#", 1)[0].strip()
        if not line:
            continue
        if not pending:
            start_line = line_number
        continuation = line.endswith("\\")
        fragment = line[:-1].rstrip() if continuation else line
        pending = f"{pending} {fragment}".strip()
        if not continuation:
            yield start_line, pending
            pending = ""
    if pending:
        yield start_line, pending


def validate(root: Path = PROJECT_ROOT) -> list[str]:
    """Return all dependency-policy violations below *root*."""
    errors: list[str] = []
    runtime_names: dict[str, str] = {}
    runtime_names_by_file: dict[str, set[str]] = {}
    pinned_versions_by_file: dict[str, dict[str, set[str]]] = {}
    locked_versions_by_file: dict[str, dict[str, set[str]]] = {}

    for filename in BOUNDED_FILES:
        path = root / filename
        if not path.is_file():
            errors.append(f"missing dependency file: {filename}")
            continue
        include_targets: list[str] = []
        for line_number, line in _logical_lines(path):
            if (include_target := _include_target(line)) is not None:
                include_targets.append(include_target)
            try:
                parsed = _requirement(path, line_number, line)
            except ValueError as exc:
                errors.append(str(exc))
                continue
            if parsed is None:
                continue
            name, specifiers = parsed
            operators = {operator for operator, _version in specifiers}
            if not operators.intersection({">", ">="}):
                errors.append(
                    f"{filename}:{line_number}: {name} needs a lower bound"
                )
            if not operators.intersection({"<", "<="}):
                errors.append(
                    f"{filename}:{line_number}: {name} needs an upper bound"
                )
            normalized = re.sub(r"[-_.]+", "-", name.split("[", 1)[0]).lower()
            runtime_names_by_file.setdefault(filename, set()).add(normalized)
            if filename != "requirements-smoke.txt":
                previous = runtime_names.setdefault(normalized, filename)
                if previous != filename:
                    errors.append(
                        f"{filename}:{line_number}: {name} is already declared "
                        f"in {previous}"
                    )
        expected_includes = BOUNDED_INCLUDES[filename]
        if tuple(include_targets) != expected_includes:
            expected = ", ".join(expected_includes) or "none"
            errors.append(
                f"{filename}: expected requirement includes: {expected}"
            )

    for filename in PINNED_FILES:
        path = root / filename
        if not path.is_file():
            errors.append(f"missing dependency file: {filename}")
            continue
        for line_number, line in _logical_lines(path):
            try:
                parsed = _requirement(path, line_number, line)
            except ValueError as exc:
                errors.append(str(exc))
                continue
            if parsed is None:
                errors.append(
                    f"{filename}:{line_number}: pinned files cannot include "
                    "other requirement files"
                )
                continue
            name, specifiers = parsed
            normalized = re.sub(
                r"[-_.]+", "-", name.split("[", 1)[0]
            ).lower()
            runtime_names_by_file.setdefault(filename, set()).add(normalized)
            if len(specifiers) != 1 or specifiers[0][0] != "==":
                errors.append(
                    f"{filename}:{line_number}: {name} must use one exact == pin"
                )
            else:
                pinned_versions_by_file.setdefault(filename, {}).setdefault(
                    normalized, set()
                ).add(specifiers[0][1])

    for filename, source_files in LOCK_FILES.items():
        path = root / filename
        if not path.is_file():
            errors.append(f"missing dependency lockfile: {filename}")
            continue
        locked_names: set[str] = set()
        for line_number, line in _lock_lines(path):
            if line.startswith("--index-url "):
                if line != "--index-url https://pypi.org/simple":
                    errors.append(
                        f"{filename}:{line_number}: unexpected package index"
                    )
                continue
            parts = line.split(" --hash=")
            requirement_line = parts[0]
            hashes = parts[1:]
            if not hashes:
                errors.append(
                    f"{filename}:{line_number}: locked requirement has no hashes"
                )
            else:
                if any(not re.fullmatch(r"sha256:[0-9a-f]{64}", value)
                       for value in hashes):
                    errors.append(
                        f"{filename}:{line_number}: invalid SHA-256 hash"
                    )
            try:
                parsed = _requirement(path, line_number, requirement_line)
            except ValueError as exc:
                errors.append(str(exc))
                continue
            if parsed is None:
                errors.append(
                    f"{filename}:{line_number}: lockfiles cannot include "
                    "other requirement files"
                )
                continue
            name, specifiers = parsed
            if len(specifiers) != 1 or specifiers[0][0] != "==":
                errors.append(
                    f"{filename}:{line_number}: {name} must use one exact == pin"
                )
            locked_names.add(
                normalized := re.sub(
                    r"[-_.]+", "-", name.split("[", 1)[0]
                ).lower()
            )
            if len(specifiers) == 1 and specifiers[0][0] == "==":
                locked_versions_by_file.setdefault(filename, {}).setdefault(
                    normalized, set()
                ).add(specifiers[0][1])
        expected_names = set().union(*(
            runtime_names_by_file.get(source_file, set())
            for source_file in source_files
        ))
        missing_names = sorted(expected_names - locked_names)
        if missing_names:
            errors.append(
                f"{filename}: missing direct dependencies: "
                + ", ".join(missing_names)
            )

    full_versions = locked_versions_by_file.get("requirements-full.lock", {})
    for name, versions in pinned_versions_by_file.get(
        "requirements-audit.txt", {}
    ).items():
        normalized_locked = {
            version.split("+", 1)[0]
            for version in full_versions.get(name, set())
        }
        if not versions.issubset(normalized_locked):
            errors.append(
                "requirements-audit.txt: normalized pin for "
                f"{name} does not match requirements-full.lock"
            )

    for filename, expected_targets in AGGREGATE_FILES.items():
        path = root / filename
        if not path.is_file():
            errors.append(f"missing dependency file: {filename}")
            continue
        targets = [
            target
            for _line_number, line in _logical_lines(path)
            if (target := _include_target(line)) is not None
        ]
        unexpected = [
            line
            for _line_number, line in _logical_lines(path)
            if _include_target(line) is None
        ]
        if tuple(targets) != expected_targets or unexpected:
            errors.append(
                f"{filename}: expected only these includes in order: "
                + ", ".join(expected_targets)
            )
    return errors


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument(
        "--root", type=Path, default=PROJECT_ROOT,
        help="repository root containing requirement files",
    )
    args = parser.parse_args(argv)
    errors = validate(args.root.resolve())
    if errors:
        print("Dependency policy violations:", file=sys.stderr)
        for error in errors:
            print(f"- {error}", file=sys.stderr)
        return 1
    print("Dependency policy passed.")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
