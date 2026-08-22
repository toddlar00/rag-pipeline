"""Enforce the Node vulnerability policy against an exact npm audit report.

The npm advisory feed is live: findings drift as the GitHub Advisory
Database updates, so this gate never claims byte stability. Instead it
binds each conclusion to the scanner identity, scan time, and registry
recorded in a content-free envelope, and it accepts findings only through
narrow, expiring, GHSA-keyed policy records.
"""

from __future__ import annotations

import argparse
import json
import re
from datetime import date, datetime, timezone
from pathlib import Path
from typing import Any

_REGISTRY = "https://registry.npmjs.org"
_GHSA_URL_PATTERN = re.compile(
    r"^https://github\.com/advisories/(GHSA-[0-9a-z]{4}-[0-9a-z]{4}-[0-9a-z]{4})$"
)
_SEVERITIES = ("info", "low", "moderate", "high", "critical")


def _reject_duplicate_pairs(pairs: list[tuple[str, Any]]) -> dict[str, Any]:
    value: dict[str, Any] = {}
    for key, item in pairs:
        if key in value:
            raise ValueError(f"duplicate JSON key {key!r}")
        value[key] = item
    return value


def _load_strict_json(path: Path, context: str) -> Any:
    try:
        text = path.read_text(encoding="utf-8")
    except OSError as exc:
        raise ValueError(f"cannot read {context}: {exc}") from exc
    try:
        return json.loads(
            text,
            object_pairs_hook=_reject_duplicate_pairs,
            parse_constant=lambda token: (_ for _ in ()).throw(
                ValueError(f"non-standard JSON number {token}")
            ),
        )
    except ValueError as exc:
        raise ValueError(f"{context} is not strict JSON: {exc}") from exc


def _nonempty_text(item: dict[str, Any], field: str, context: str) -> str:
    value = item.get(field)
    if not isinstance(value, str) or not value.strip():
        raise ValueError(f"{context} must carry a non-empty {field}")
    return value


def _policy_exceptions(policy: Any) -> dict[str, tuple[str, date]]:
    if not isinstance(policy, dict) or policy.get("schema_version") != 1:
        raise ValueError("node vulnerability policy must use schema_version 1")
    if set(policy) != {"schema_version", "exceptions"}:
        raise ValueError("node vulnerability policy has unexpected fields")
    raw = policy.get("exceptions")
    if not isinstance(raw, dict):
        raise ValueError("node vulnerability policy exceptions must be an object")
    exceptions: dict[str, tuple[str, date]] = {}
    for advisory_id, item in raw.items():
        context = f"exception {advisory_id}"
        if not re.fullmatch(
            r"GHSA-[0-9a-z]{4}-[0-9a-z]{4}-[0-9a-z]{4}", advisory_id
        ):
            raise ValueError(f"{context} must be keyed by an exact GHSA id")
        if not isinstance(item, dict):
            raise ValueError(f"{context} must be an object")
        if set(item) != {
            "package",
            "expires",
            "reason",
            "mitigation",
            "references",
        }:
            raise ValueError(f"{context} has unexpected fields")
        package = _nonempty_text(item, "package", context)
        _nonempty_text(item, "reason", context)
        _nonempty_text(item, "mitigation", context)
        references = item.get("references")
        if (
            not isinstance(references, list)
            or not references
            or any(
                not isinstance(ref, str) or not ref.startswith("https://")
                for ref in references
            )
        ):
            raise ValueError(f"{context} must list HTTPS references")
        expires_text = _nonempty_text(item, "expires", context)
        try:
            expires = date.fromisoformat(expires_text)
        except ValueError as exc:
            raise ValueError(f"{context} has an invalid expires date") from exc
        exceptions[advisory_id] = (package, expires)
    return exceptions


def _advisories(report: Any) -> list[dict[str, Any]]:
    if not isinstance(report, dict):
        raise ValueError("npm audit report must be a JSON object")
    if report.get("auditReportVersion") != 2:
        raise ValueError("npm audit report must use auditReportVersion 2")
    if set(report) != {"auditReportVersion", "metadata", "vulnerabilities"}:
        raise ValueError("npm audit report has unexpected top-level fields")
    vulnerabilities = report.get("vulnerabilities")
    metadata = report.get("metadata")
    if not isinstance(vulnerabilities, dict) or not isinstance(metadata, dict):
        raise ValueError("npm audit report structure is invalid")
    advisories: list[dict[str, Any]] = []
    for package, entry in vulnerabilities.items():
        context = f"vulnerability entry {package}"
        if not isinstance(entry, dict):
            raise ValueError(f"{context} must be an object")
        severity = entry.get("severity")
        if severity not in _SEVERITIES:
            raise ValueError(f"{context} has an invalid severity {severity!r}")
        via = entry.get("via")
        if not isinstance(via, list) or not via:
            raise ValueError(f"{context} must carry a non-empty via list")
        for item in via:
            if isinstance(item, str):
                if item not in vulnerabilities:
                    raise ValueError(
                        f"{context} references an unreported package {item!r}"
                    )
                continue
            if not isinstance(item, dict):
                raise ValueError(f"{context} has an invalid via entry")
            url = item.get("url")
            match = (
                _GHSA_URL_PATTERN.fullmatch(url)
                if isinstance(url, str)
                else None
            )
            if match is None:
                raise ValueError(
                    f"{context} advisory does not carry an exact GHSA url"
                )
            advisory_severity = item.get("severity")
            if advisory_severity not in _SEVERITIES:
                raise ValueError(
                    f"{context} advisory has an invalid severity"
                )
            advisories.append(
                {
                    "ghsa": match.group(1),
                    "package": package,
                    "severity": advisory_severity,
                }
            )
    return advisories


def evaluate(
    report: Any, policy: Any, *, today: date
) -> tuple[list[str], list[dict[str, Any]]]:
    """Return (errors, finding rows) for the report under the policy."""
    exceptions = _policy_exceptions(policy)
    advisories = _advisories(report)
    errors: list[str] = []
    findings: list[dict[str, Any]] = []
    used: set[str] = set()
    for advisory in sorted(
        advisories, key=lambda item: (item["ghsa"], item["package"])
    ):
        ghsa = advisory["ghsa"]
        package = advisory["package"]
        exception = exceptions.get(ghsa)
        accepted_through: str | None = None
        if exception is None:
            errors.append(f"{package}: {ghsa} is not accepted by policy")
        else:
            expected_package, expires = exception
            used.add(ghsa)
            if expected_package != package:
                errors.append(
                    f"{package}: {ghsa} exception names {expected_package}"
                )
            elif expires < today:
                errors.append(f"{package}: {ghsa} exception expired on {expires}")
            else:
                accepted_through = expires.isoformat()
                print(f"{package}: {ghsa} accepted through {expires}")
        findings.append(
            {
                "accepted_through": accepted_through,
                "ghsa": ghsa,
                "package": package,
                "severity": advisory["severity"],
            }
        )
    for advisory_id in sorted(set(exceptions) - used):
        errors.append(
            f"policy exception {advisory_id} matched no reported advisory"
        )
    return errors, findings


def _envelope(
    report: dict[str, Any],
    findings: list[dict[str, Any]],
    *,
    npm_version: str,
    node_version: str,
    scan_time: str,
) -> dict[str, Any]:
    metadata = report["metadata"]
    totals = metadata.get("vulnerabilities")
    if not isinstance(totals, dict):
        raise ValueError("npm audit metadata must carry vulnerability totals")
    return {
        "audit_report_version": 2,
        "findings": findings,
        "registry": _REGISTRY,
        "scan_time": scan_time,
        "scanner": {
            "node_version": node_version,
            "npm_version": npm_version,
            "tool": "npm-audit",
        },
        "schema_version": 1,
        "totals": {
            severity: totals.get(severity)
            for severity in (*_SEVERITIES, "total")
        },
    }


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("report", type=Path, help="npm audit --json output")
    parser.add_argument("--policy", type=Path, required=True)
    parser.add_argument("--scanner-npm", required=True)
    parser.add_argument("--scanner-node", required=True)
    parser.add_argument("--output-envelope", type=Path, required=True)
    args = parser.parse_args(argv)
    try:
        report = _load_strict_json(args.report, "npm audit report")
        policy = _load_strict_json(args.policy, "node vulnerability policy")
        errors, findings = evaluate(
            report, policy, today=datetime.now(timezone.utc).date()
        )
        envelope = _envelope(
            report,
            findings,
            npm_version=args.scanner_npm.strip(),
            node_version=args.scanner_node.strip(),
            scan_time=datetime.now(timezone.utc).isoformat(),
        )
    except ValueError as exc:
        print(f"error: {exc}")
        return 1
    args.output_envelope.write_text(
        json.dumps(envelope, ensure_ascii=False, indent=2, sort_keys=True)
        + "\n",
        encoding="utf-8",
        newline="\n",
    )
    if errors:
        for error in errors:
            print(f"error: {error}")
        return 1
    print(
        "Node vulnerability policy passed: "
        f"{len(findings)} accepted finding(s), 0 violations."
    )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
