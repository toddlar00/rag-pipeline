#!/usr/bin/env python3
"""Enforce the repository policy against a pip-licenses JSON report."""

from __future__ import annotations

import argparse
from datetime import date
import json
import re
import sys
from pathlib import Path
from typing import Any


PROJECT_ROOT = Path(__file__).resolve().parents[1]
DEFAULT_POLICY = PROJECT_ROOT / "dependency-license-policy.json"


def _normalize_package(name: str) -> str:
    return re.sub(r"[-_.]+", "-", name).lower()


def evaluate(
    report: Any, policy: Any, *, as_of: date | None = None
) -> tuple[list[str], list[str]]:
    """Return ``(violations, warnings)`` for decoded report and policy JSON."""
    if not isinstance(report, list):
        raise ValueError("license report must be a JSON list")
    if not isinstance(policy, dict) or policy.get("schema_version") != 1:
        raise ValueError("license policy must use schema_version 1")
    pattern_values = policy.get("denied_license_patterns")
    allowed_values = policy.get("allowed_packages", {})
    allowed_unknown_values = policy.get("allowed_unknown_packages", {})
    if not isinstance(pattern_values, list) or not all(
        isinstance(value, str) and value for value in pattern_values
    ):
        raise ValueError("denied_license_patterns must be a non-empty string list")
    if not isinstance(allowed_values, dict):
        raise ValueError("allowed_packages must be an object")
    if not isinstance(allowed_unknown_values, dict):
        raise ValueError("allowed_unknown_packages must be an object")
    patterns = [re.compile(value, re.IGNORECASE) for value in pattern_values]
    today = as_of or date.today()
    allowed: dict[str, tuple[str, date]] = {}
    for name, item in allowed_values.items():
        if not isinstance(name, str) or not isinstance(item, dict):
            raise ValueError("denied-license exceptions must be objects")
        for field in ("reason", "scope", "owner", "version"):
            value = item.get(field)
            if not isinstance(value, str) or not value.strip():
                raise ValueError(f"{name}: license exception needs a {field}")
        reference = item.get("reference")
        if not isinstance(reference, str) or not reference.startswith("https://"):
            raise ValueError(f"{name}: license exception needs an HTTPS reference")
        expires_text = item.get("expires")
        if not isinstance(expires_text, str):
            raise ValueError(f"{name}: license exception needs an expires date")
        try:
            expires = date.fromisoformat(expires_text)
        except ValueError as exc:
            raise ValueError(
                f"{name}: license exception has an invalid expires date"
            ) from exc
        allowed[_normalize_package(name)] = (item["version"].strip(), expires)
    allowed_unknown: dict[str, tuple[str, date]] = {}
    for name, item in allowed_unknown_values.items():
        if not isinstance(name, str) or not isinstance(item, dict):
            raise ValueError("allowed unknown packages must be objects")
        reason = item.get("reason")
        owner = item.get("owner")
        version = item.get("version")
        expires_text = item.get("expires")
        reference = item.get("reference")
        if not isinstance(reason, str) or not reason.strip():
            raise ValueError(f"{name}: unknown-license exception needs a reason")
        if not isinstance(reference, str) or not reference.startswith("https://"):
            raise ValueError(f"{name}: unknown-license exception needs an HTTPS reference")
        if not isinstance(owner, str) or not owner.strip():
            raise ValueError(f"{name}: unknown-license exception needs an owner")
        if not isinstance(version, str) or not version.strip():
            raise ValueError(f"{name}: unknown-license exception needs a version")
        if not isinstance(expires_text, str):
            raise ValueError(f"{name}: unknown-license exception needs an expires date")
        try:
            expires = date.fromisoformat(expires_text)
        except ValueError as exc:
            raise ValueError(
                f"{name}: unknown-license exception has an invalid expires date"
            ) from exc
        allowed_unknown[_normalize_package(name)] = (version.strip(), expires)

    violations: list[str] = []
    warnings: list[str] = []
    for index, item in enumerate(report):
        if not isinstance(item, dict):
            raise ValueError(f"license report item {index} must be an object")
        name = str(item.get("Name") or item.get("name") or "").strip()
        version = str(item.get("Version") or item.get("version") or "").strip()
        license_name = str(
            item.get("License") or item.get("license") or ""
        ).strip()
        if not name:
            raise ValueError(f"license report item {index} has no package name")
        if not version:
            raise ValueError(f"license report item {index} has no package version")
        if not license_name or license_name.upper() in {"UNKNOWN", "NOASSERTION"}:
            exception = allowed_unknown.get(_normalize_package(name))
            if exception is None:
                violations.append(f"{name}: license metadata is unknown")
            elif version != exception[0]:
                violations.append(
                    f"{name}: unknown-license exception covers {exception[0]}, "
                    f"not {version}"
                )
            elif exception[1] < today:
                violations.append(
                    f"{name}: unknown-license exception expired on {exception[1]}"
                )
            else:
                warnings.append(
                    f"{name} {version}: unknown license metadata is allowlisted "
                    f"through {exception[1]}"
                )
            continue
        matched = [pattern.pattern for pattern in patterns if pattern.search(license_name)]
        if not matched:
            continue
        exception = allowed.get(_normalize_package(name))
        if exception is None:
            violations.append(f"{name}: {license_name}")
            continue
        expected_version, expires = exception
        if version != expected_version:
            violations.append(
                f"{name}: license exception covers {expected_version}, not {version}"
            )
        elif expires < today:
            violations.append(f"{name}: license exception expired on {expires}")
        else:
            warnings.append(
                f"{name}: denied license is allowlisted through {expires}"
            )
    return violations, warnings


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("report", type=Path, help="pip-licenses JSON report")
    parser.add_argument(
        "--policy", type=Path, default=DEFAULT_POLICY,
        help="license policy JSON (default: repository policy)",
    )
    parser.add_argument(
        "--as-of", type=date.fromisoformat,
        help="override the policy date for deterministic validation",
    )
    args = parser.parse_args(argv)
    try:
        report = json.loads(args.report.read_text(encoding="utf-8"))
        policy = json.loads(args.policy.read_text(encoding="utf-8"))
        violations, warnings = evaluate(report, policy, as_of=args.as_of)
    except (OSError, ValueError, json.JSONDecodeError, re.error) as exc:
        print(f"License policy error: {exc}", file=sys.stderr)
        return 2
    for warning in warnings:
        print(f"WARNING: {warning}")
    if violations:
        print("Denied dependency licenses:", file=sys.stderr)
        for violation in violations:
            print(f"- {violation}", file=sys.stderr)
        return 1
    print("Dependency license policy passed.")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
