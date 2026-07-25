#!/usr/bin/env python3
"""Enforce repository-owned GitHub Actions security invariants.

The checker deliberately parses only the small YAML surface it owns.  It does
not try to interpret arbitrary YAML, execute workflow expressions, or depend on
a package that must itself be bootstrapped by CI.
"""

from __future__ import annotations

import argparse
import ast
from dataclasses import dataclass
import json
from pathlib import Path, PurePosixPath
import re
import sys
from typing import Any, Sequence


PROJECT_ROOT = Path(__file__).resolve().parents[1]
POLICY_PATH = "ci-security-ownership.json"
SECURITY_WORKFLOW_PATH = ".github/workflows/security.yml"
POLICY_SCHEMA_VERSION = 1
_POLICY_KEYS = frozenset({
    "schema_version",
    "current_implementation_owners",
    "reserved_implementation_owners",
    "governance_paths",
})
_REQUIRED_CURRENT_OWNERS = frozenset({
    "endpoint_policy.py",
    "index_state.py",
    "llm_adapters.py",
    "llm_runtime.py",
    "model_artifacts.py",
    "provider_transport.py",
    "rag.py",
    "release_security.py",
    "resource_lease.py",
    "retrieval_core.py",
    "tools/sync_model_artifacts.py",
    "vector_lifecycle.py",
})
_REQUIRED_RESERVED_OWNERS = frozenset({"pipeline_runtime.py"})
_REQUIRED_GOVERNANCE_PATHS = frozenset({
    POLICY_PATH,
    SECURITY_WORKFLOW_PATH,
    "tools/check_ci_security.py",
})
_ACTION_SHA_RE = re.compile(r"[0-9a-fA-F]{40}")


@dataclass(frozen=True, slots=True)
class OwnershipPolicy:
    """Validated workflow-trigger ownership declared by the repository."""

    current_paths: frozenset[str]
    reserved_paths: frozenset[str]
    governance_paths: frozenset[str]

    @property
    def security_trigger_paths(self) -> frozenset[str]:
        return self.current_paths | self.reserved_paths | self.governance_paths


def _indent(line: str) -> int:
    return len(line) - len(line.lstrip(" "))


def _is_repository_path(value: object) -> bool:
    if not isinstance(value, str) or not value or "\\" in value:
        return False
    path = PurePosixPath(value)
    return (
        not path.is_absolute()
        and bool(path.parts)
        and all(part not in {"", ".", ".."} for part in path.parts)
        and path.as_posix() == value
        and not any(character in value for character in "*?[]")
    )


def _path_groups(
    value: object,
    *,
    field: str,
    errors: list[str],
) -> frozenset[str]:
    if not isinstance(value, dict) or not value:
        errors.append(f"{POLICY_PATH}: {field} must be a non-empty object")
        return frozenset()
    paths: set[str] = set()
    for group, group_paths in value.items():
        if not isinstance(group, str) or not group:
            errors.append(f"{POLICY_PATH}: {field} has an invalid group name")
            continue
        if not isinstance(group_paths, list) or not group_paths:
            errors.append(
                f"{POLICY_PATH}: {field}.{group} must be a non-empty list"
            )
            continue
        for path in group_paths:
            if not _is_repository_path(path):
                errors.append(
                    f"{POLICY_PATH}: {field}.{group} has invalid path {path!r}"
                )
                continue
            if path in paths:
                errors.append(
                    f"{POLICY_PATH}: {field} declares {path!r} more than once"
                )
            paths.add(path)
    return frozenset(paths)


def _path_list(
    value: object,
    *,
    field: str,
    errors: list[str],
) -> frozenset[str]:
    if not isinstance(value, list) or not value:
        errors.append(f"{POLICY_PATH}: {field} must be a non-empty list")
        return frozenset()
    paths: set[str] = set()
    for path in value:
        if not _is_repository_path(path):
            errors.append(f"{POLICY_PATH}: {field} has invalid path {path!r}")
            continue
        if path in paths:
            errors.append(f"{POLICY_PATH}: {field} repeats {path!r}")
        paths.add(path)
    return frozenset(paths)


def load_ownership_policy(
    root: Path,
) -> tuple[OwnershipPolicy | None, list[str]]:
    """Load and validate the future-extensible security ownership manifest."""
    errors: list[str] = []
    path = root / POLICY_PATH
    try:
        payload: Any = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError) as exc:
        return None, [f"{POLICY_PATH}: cannot load policy: {exc}"]
    if not isinstance(payload, dict):
        return None, [f"{POLICY_PATH}: top level must be an object"]
    unexpected = set(payload).difference(_POLICY_KEYS)
    missing = _POLICY_KEYS.difference(payload)
    if unexpected:
        errors.append(
            f"{POLICY_PATH}: unexpected fields: {', '.join(sorted(unexpected))}"
        )
    if missing:
        errors.append(
            f"{POLICY_PATH}: missing fields: {', '.join(sorted(missing))}"
        )
    if payload.get("schema_version") != POLICY_SCHEMA_VERSION:
        errors.append(
            f"{POLICY_PATH}: schema_version must be {POLICY_SCHEMA_VERSION}"
        )

    current = _path_groups(
        payload.get("current_implementation_owners"),
        field="current_implementation_owners",
        errors=errors,
    )
    reserved = _path_groups(
        payload.get("reserved_implementation_owners"),
        field="reserved_implementation_owners",
        errors=errors,
    )
    governance = _path_list(
        payload.get("governance_paths"),
        field="governance_paths",
        errors=errors,
    )
    overlaps = {
        path_value
        for paths in (current, reserved, governance)
        for path_value in paths
        if sum(path_value in candidate for candidate in (
            current, reserved, governance
        )) > 1
    }
    if overlaps:
        errors.append(
            f"{POLICY_PATH}: paths have more than one ownership role: "
            f"{', '.join(sorted(overlaps))}"
        )

    for label, expected, actual in (
        ("current owner", _REQUIRED_CURRENT_OWNERS, current),
        ("reserved owner", _REQUIRED_RESERVED_OWNERS, reserved),
        ("governance path", _REQUIRED_GOVERNANCE_PATHS, governance),
    ):
        for required in sorted(expected.difference(actual)):
            errors.append(f"{POLICY_PATH}: missing required {label}: {required}")

    for declared in sorted(current | governance):
        declared_path = root / PurePosixPath(declared)
        if not declared_path.is_file():
            errors.append(
                f"{POLICY_PATH}: declared current path is missing: {declared}"
            )

    return OwnershipPolicy(current, reserved, governance), errors


def _mapping_line_indices(
    lines: list[str],
    *,
    key: str,
    parent_start: int,
    parent_indent: int,
) -> list[int]:
    indices: list[int] = []
    pattern = re.compile(rf"^{re.escape(key)}\s*:\s*(?:#.*)?$")
    for index in range(parent_start + 1, len(lines)):
        line = lines[index]
        stripped = line.strip()
        if not stripped or stripped.startswith("#"):
            continue
        indentation = _indent(line)
        if indentation <= parent_indent:
            break
        if indentation == parent_indent + 2 and pattern.fullmatch(stripped):
            indices.append(index)
    return indices


def _single_mapping_line(
    lines: list[str],
    *,
    key: str,
    parent_start: int,
    parent_indent: int,
    context: str,
    errors: list[str],
) -> int | None:
    indices = _mapping_line_indices(
        lines,
        key=key,
        parent_start=parent_start,
        parent_indent=parent_indent,
    )
    if len(indices) != 1:
        errors.append(f"{context}: expected exactly one {key!r} mapping")
        return None
    return indices[0]


def _yaml_scalar(value: str) -> str:
    value = value.strip()
    if value.startswith(("'", '"')):
        parsed = ast.literal_eval(value)
        if not isinstance(parsed, str):
            raise ValueError("path entry is not text")
        return parsed
    return value.split(" #", 1)[0].strip()


def event_paths(
    workflow_text: str,
    event: str,
    *,
    workflow_path: str = SECURITY_WORKFLOW_PATH,
) -> tuple[frozenset[str], list[str]]:
    """Extract one top-level event's simple ``paths`` list."""
    lines = workflow_text.splitlines()
    errors: list[str] = []
    top = [
        index for index, line in enumerate(lines)
        if _indent(line) == 0 and re.fullmatch(
            r"on\s*:\s*(?:#.*)?", line.strip()
        )
    ]
    if len(top) != 1:
        return frozenset(), [
            f"{workflow_path}: expected exactly one top-level 'on' mapping"
        ]
    event_index = _single_mapping_line(
        lines,
        key=event,
        parent_start=top[0],
        parent_indent=0,
        context=workflow_path,
        errors=errors,
    )
    if event_index is None:
        return frozenset(), errors
    paths_index = _single_mapping_line(
        lines,
        key="paths",
        parent_start=event_index,
        parent_indent=2,
        context=f"{workflow_path}: {event}",
        errors=errors,
    )
    if paths_index is None:
        return frozenset(), errors

    paths: set[str] = set()
    for index in range(paths_index + 1, len(lines)):
        line = lines[index]
        stripped = line.strip()
        if not stripped or stripped.startswith("#"):
            continue
        if _indent(line) <= 4:
            break
        if _indent(line) != 6 or not stripped.startswith("- "):
            errors.append(
                f"{workflow_path}:{index + 1}: paths must be a simple list"
            )
            continue
        try:
            path = _yaml_scalar(stripped[2:])
        except (SyntaxError, ValueError) as exc:
            errors.append(
                f"{workflow_path}:{index + 1}: invalid path scalar: {exc}"
            )
            continue
        if not path:
            errors.append(f"{workflow_path}:{index + 1}: empty path filter")
        elif path in paths:
            errors.append(
                f"{workflow_path}:{index + 1}: duplicate path filter {path!r}"
            )
        else:
            paths.add(path)
    if not paths:
        errors.append(f"{workflow_path}: {event}.paths must not be empty")
    return frozenset(paths), errors


def _validate_permissions(workflow_path: str, lines: list[str]) -> list[str]:
    errors: list[str] = []
    declarations = [
        (index, _indent(line), line.strip())
        for index, line in enumerate(lines)
        if re.match(r"permissions\s*:", line.strip())
    ]
    if (
        len(declarations) != 1
        or declarations[0][1] != 0
        or re.fullmatch(
            r"permissions\s*:\s*(?:#.*)?", declarations[0][2]
        ) is None
    ):
        errors.append(
            f"{workflow_path}: require one top-level permissions mapping and "
            "no job-level overrides"
        )
        return errors
    start = declarations[0][0]
    entries: dict[str, str] = {}
    for index in range(start + 1, len(lines)):
        line = lines[index]
        stripped = line.strip()
        if not stripped or stripped.startswith("#"):
            continue
        indentation = _indent(line)
        if indentation == 0:
            break
        match = re.fullmatch(r"([A-Za-z-]+)\s*:\s*([^#\s]+)\s*(?:#.*)?", stripped)
        if indentation != 2 or match is None:
            errors.append(
                f"{workflow_path}:{index + 1}: invalid permissions entry"
            )
            continue
        if match.group(1) in entries:
            errors.append(
                f"{workflow_path}:{index + 1}: duplicate permissions entry"
            )
        entries[match.group(1)] = match.group(2)
    if entries != {"contents": "read"}:
        errors.append(
            f"{workflow_path}: permissions must be exactly 'contents: read'"
        )
    return errors


def _validate_action_pins(workflow_path: str, lines: list[str]) -> list[str]:
    errors: list[str] = []
    checkout_lines: list[tuple[int, int]] = []
    for index, line in enumerate(lines):
        match = re.match(r"^(\s*)(-\s+)?uses\s*:\s*([^#\s]+)", line)
        if match is None:
            continue
        reference = match.group(3)
        if reference.startswith("./"):
            continue
        if "@" not in reference or not _ACTION_SHA_RE.fullmatch(
            reference.rsplit("@", 1)[1]
        ):
            errors.append(
                f"{workflow_path}:{index + 1}: action must use a full "
                f"40-character commit SHA: {reference}"
            )
        if reference.startswith("actions/checkout@"):
            statement_indent = len(match.group(1))
            step_indent = (
                statement_indent if match.group(2) else statement_indent - 2
            )
            checkout_lines.append((index, max(step_indent, 0)))

    for index, step_indent in checkout_lines:
        values: list[str] = []
        for child_index in range(index + 1, len(lines)):
            line = lines[child_index]
            stripped = line.strip()
            if not stripped or stripped.startswith("#"):
                continue
            indentation = _indent(line)
            if indentation < step_indent or (
                indentation == step_indent and stripped.startswith("- ")
            ):
                break
            match = re.fullmatch(
                r"persist-credentials\s*:\s*([^#\s]+)\s*(?:#.*)?",
                stripped,
            )
            if match is not None and indentation > step_indent:
                values.append(match.group(1))
        if values != ["false"]:
            errors.append(
                f"{workflow_path}:{index + 1}: actions/checkout must set "
                "persist-credentials: false exactly once"
            )
    return errors


def validate_workflow(workflow_path: str, text: str) -> list[str]:
    """Validate action pins, read-only permissions, and checkout credentials."""
    lines = text.splitlines()
    return (
        _validate_permissions(workflow_path, lines)
        + _validate_action_pins(workflow_path, lines)
    )


def validate(root: Path = PROJECT_ROOT) -> list[str]:
    """Return every CI security-policy violation below *root*."""
    root = root.resolve()
    policy, errors = load_ownership_policy(root)
    workflow_root = root / ".github" / "workflows"
    workflow_paths = sorted({
        *workflow_root.glob("*.yml"),
        *workflow_root.glob("*.yaml"),
    })
    if not workflow_paths:
        errors.append(".github/workflows: no workflow files found")
    for path in workflow_paths:
        relative = path.relative_to(root).as_posix()
        try:
            text = path.read_text(encoding="utf-8")
        except OSError as exc:
            errors.append(f"{relative}: cannot read workflow: {exc}")
            continue
        errors.extend(validate_workflow(relative, text))

    security_path = root / SECURITY_WORKFLOW_PATH
    try:
        security_text = security_path.read_text(encoding="utf-8")
    except OSError as exc:
        errors.append(f"{SECURITY_WORKFLOW_PATH}: cannot read workflow: {exc}")
        return errors
    if policy is None:
        return errors
    for event in ("pull_request", "push"):
        paths, path_errors = event_paths(security_text, event)
        errors.extend(path_errors)
        for missing in sorted(policy.security_trigger_paths.difference(paths)):
            errors.append(
                f"{SECURITY_WORKFLOW_PATH}: {event}.paths does not cover "
                f"declared security owner {missing}"
            )
    return errors


def _parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        description="Validate GitHub Actions security ownership and hardening"
    )
    parser.add_argument(
        "--root",
        type=Path,
        default=PROJECT_ROOT,
        help="repository root (defaults to this repository)",
    )
    return parser


def main(argv: Sequence[str] | None = None) -> int:
    args = _parser().parse_args(argv)
    errors = validate(args.root)
    if errors:
        for error in errors:
            print(error, file=sys.stderr)
        return 1
    print("GitHub Actions security policy is valid.")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
