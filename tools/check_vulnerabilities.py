#!/usr/bin/env python3
"""Enforce time-bounded exceptions against a pip-audit JSON report."""

from __future__ import annotations

import argparse
from datetime import date
import json
import re
import sys
from pathlib import Path
from typing import Any


PROJECT_ROOT = Path(__file__).resolve().parents[1]
DEFAULT_POLICY = PROJECT_ROOT / "dependency-vulnerability-policy.json"
DEFAULT_SKIP_AUDIT_REQUIREMENTS = PROJECT_ROOT / "requirements-audit.txt"
_ADVISORY_ID_RE = re.compile(r"^[A-Z0-9]+(?:-[A-Z0-9]+)+$")


def _normalize_package(name: str) -> str:
    return re.sub(r"[-_.]+", "-", name).lower()


def _nonempty_text(item: dict[str, Any], field: str, context: str) -> str:
    value = item.get(field)
    if not isinstance(value, str) or not value.strip():
        raise ValueError(f"{context} needs a non-empty {field}")
    return value.strip()


def _allowed_skip_rules(
    policy: dict[str, Any],
) -> dict[str, tuple[str, re.Pattern[str], date]]:
    values = policy.get("allowed_skips", {})
    if not isinstance(values, dict):
        raise ValueError("allowed_skips must be an object")
    rules: dict[str, tuple[str, re.Pattern[str], date]] = {}
    for package, item in values.items():
        context = f"allowed skip {package}"
        if not isinstance(package, str) or not isinstance(item, dict):
            raise ValueError(f"{context} must be an object")
        _nonempty_text(item, "reason", context)
        version = _nonempty_text(item, "version", context)
        pattern_text = _nonempty_text(
            item, "expected_reason_pattern", context
        )
        try:
            pattern = re.compile(pattern_text)
        except re.error as exc:
            raise ValueError(f"{context} has an invalid reason pattern") from exc
        expires_text = _nonempty_text(item, "expires", context)
        try:
            expires = date.fromisoformat(expires_text)
        except ValueError as exc:
            raise ValueError(f"{context} has an invalid expires date") from exc
        reference = _nonempty_text(item, "reference", context)
        if not reference.startswith("https://"):
            raise ValueError(f"{context} needs an HTTPS reference")
        rules[_normalize_package(package)] = (version, pattern, expires)
    return rules


def validate_skip_audit_requirements(
    path: Path, policy: dict[str, Any]
) -> None:
    """Require exact normalized pins for every explicitly skipped package."""
    actual: dict[str, str] = {}
    for line_number, raw_line in enumerate(
        path.read_text(encoding="utf-8").splitlines(), 1
    ):
        line = raw_line.split("#", 1)[0].strip()
        if not line:
            continue
        match = re.fullmatch(
            r"(?P<name>[A-Za-z0-9][A-Za-z0-9_.-]*)==(?P<version>[^;\s]+)",
            line,
        )
        if match is None:
            raise ValueError(
                f"{path.name}:{line_number}: expected an exact unmarked pin"
            )
        actual[_normalize_package(match.group("name"))] = match.group("version")
    expected = {
        package: rule[0]
        for package, rule in _allowed_skip_rules(policy).items()
    }
    if actual != expected:
        raise ValueError(
            f"{path.name}: pins must exactly match vulnerability-policy skips"
        )


def evaluate(
    report: Any, policy: Any, *, as_of: date | None = None
) -> tuple[list[str], list[str]]:
    """Return ``(violations, warnings)`` for decoded pip-audit policy data."""
    if not isinstance(report, dict) or not isinstance(
        report.get("dependencies"), list
    ):
        raise ValueError("vulnerability report needs a dependencies list")
    if not isinstance(policy, dict) or policy.get("schema_version") != 1:
        raise ValueError("vulnerability policy must use schema_version 1")
    exception_values = policy.get("exceptions", {})
    if not isinstance(exception_values, dict):
        raise ValueError("exceptions must be an object")

    today = as_of or date.today()
    exceptions: dict[str, tuple[str, date]] = {}
    for advisory_id, item in exception_values.items():
        context = f"exception {advisory_id}"
        if not isinstance(advisory_id, str) or not _ADVISORY_ID_RE.fullmatch(
            advisory_id
        ):
            raise ValueError(f"invalid advisory ID: {advisory_id!r}")
        if not isinstance(item, dict):
            raise ValueError(f"{context} must be an object")
        package = _normalize_package(_nonempty_text(item, "package", context))
        _nonempty_text(item, "reason", context)
        _nonempty_text(item, "mitigation", context)
        expires_text = _nonempty_text(item, "expires", context)
        try:
            expires = date.fromisoformat(expires_text)
        except ValueError as exc:
            raise ValueError(f"{context} has an invalid expires date") from exc
        references = item.get("references")
        if not isinstance(references, list) or not references or not all(
            isinstance(reference, str) and reference.startswith("https://")
            for reference in references
        ):
            raise ValueError(f"{context} needs HTTPS references")
        exceptions[advisory_id] = (package, expires)

    allowed_skips = _allowed_skip_rules(policy)

    violations: list[str] = []
    warnings: list[str] = []
    observed_exceptions: set[str] = set()
    observed_skips: set[str] = set()
    for index, dependency in enumerate(report["dependencies"]):
        if not isinstance(dependency, dict):
            raise ValueError(f"dependency {index} must be an object")
        name = _nonempty_text(dependency, "name", f"dependency {index}")
        normalized_name = _normalize_package(name)
        skip_reason = dependency.get("skip_reason")
        if skip_reason is not None:
            if not isinstance(skip_reason, str) or not skip_reason.strip():
                raise ValueError(f"{name}: skip_reason must be non-empty")
            observed_skips.add(normalized_name)
            skip_rule = allowed_skips.get(normalized_name)
            if skip_rule is None:
                violations.append(f"{name}: audit skipped: {skip_reason}")
            elif skip_rule[2] < today:
                violations.append(
                    f"{name}: audit-skip exception expired on {skip_rule[2]}"
                )
            elif skip_rule[1].fullmatch(skip_reason) is None:
                violations.append(
                    f"{name}: audit skip reason/version does not match policy"
                )
            else:
                warnings.append(
                    f"{name}: audit skip is allowlisted through {skip_rule[2]}"
                )
            continue
        vulnerabilities = dependency.get("vulns")
        if not isinstance(vulnerabilities, list):
            raise ValueError(f"{name}: vulns must be a list")
        for vulnerability in vulnerabilities:
            if not isinstance(vulnerability, dict):
                raise ValueError(f"{name}: vulnerability must be an object")
            advisory_id = _nonempty_text(
                vulnerability, "id", f"{name} vulnerability"
            )
            exception = exceptions.get(advisory_id)
            if exception is None:
                violations.append(f"{name}: {advisory_id}")
                continue
            observed_exceptions.add(advisory_id)
            expected_package, expires = exception
            if normalized_name != expected_package:
                violations.append(
                    f"{name}: {advisory_id} exception is scoped to "
                    f"{expected_package}"
                )
            elif expires < today:
                violations.append(
                    f"{name}: {advisory_id} exception expired on {expires}"
                )
            else:
                warnings.append(
                    f"{name}: {advisory_id} accepted through {expires}"
                )

    for advisory_id in sorted(exceptions.keys() - observed_exceptions):
        violations.append(
            f"{advisory_id}: configured exception is stale or not observed"
        )
    for package in sorted(allowed_skips.keys() - observed_skips):
        warnings.append(f"{package}: configured audit skip was not observed")
    return violations, warnings


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("report", type=Path, help="pip-audit JSON report")
    parser.add_argument(
        "--policy", type=Path, default=DEFAULT_POLICY,
        help="vulnerability policy JSON (default: repository policy)",
    )
    parser.add_argument(
        "--as-of", type=date.fromisoformat,
        help="override the policy date for deterministic validation",
    )
    parser.add_argument(
        "--skip-audit-requirements",
        type=Path,
        default=DEFAULT_SKIP_AUDIT_REQUIREMENTS,
        help="exact base-version pins audited for local-version skips",
    )
    args = parser.parse_args(argv)
    try:
        report = json.loads(args.report.read_text(encoding="utf-8"))
        policy = json.loads(args.policy.read_text(encoding="utf-8"))
        validate_skip_audit_requirements(
            args.skip_audit_requirements, policy
        )
        violations, warnings = evaluate(report, policy, as_of=args.as_of)
    except (OSError, ValueError, json.JSONDecodeError) as exc:
        print(f"Vulnerability policy error: {exc}", file=sys.stderr)
        return 2
    for warning in warnings:
        print(f"WARNING: {warning}")
    if violations:
        print("Unaccepted dependency vulnerabilities:", file=sys.stderr)
        for violation in violations:
            print(f"- {violation}", file=sys.stderr)
        return 1
    print("Dependency vulnerability policy passed.")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
