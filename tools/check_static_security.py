"""Enforce the narrow, pinned Python static-security ruleset.

The gate runs the hash-locked ruff's flake8-bandit rules restricted to a
reviewed high-confidence blocking subset. Findings are normalized to rule
id, repository-relative path, and line only — messages, source snippets,
and absolute paths never reach the console or the retained envelope.
Deliberate dangerous constructs inside tests (exec, pickle, bind-all, and
suspicious-import probes that exist to prove rejection paths) are exempt
by declared rule scope, and everything else is suppressed only through
narrow, counted, expiring policy records.
"""

from __future__ import annotations

import argparse
import json
import re
import subprocess
import sys
from datetime import date, datetime, timezone
from pathlib import Path
from typing import Any

_RULE_PATTERN = re.compile(r"^S[0-9]{3}$")


def _reject_duplicate_pairs(pairs: list[tuple[str, Any]]) -> dict[str, Any]:
    value: dict[str, Any] = {}
    for key, item in pairs:
        if key in value:
            raise ValueError(f"duplicate JSON key {key!r}")
        value[key] = item
    return value


def _load_policy(path: Path) -> dict[str, Any]:
    try:
        text = path.read_text(encoding="utf-8")
    except OSError as exc:
        raise ValueError(f"cannot read static security policy: {exc}") from exc
    try:
        policy = json.loads(
            text,
            object_pairs_hook=_reject_duplicate_pairs,
            parse_constant=lambda token: (_ for _ in ()).throw(
                ValueError(f"non-standard JSON number {token}")
            ),
        )
    except ValueError as exc:
        raise ValueError(
            f"static security policy is not strict JSON: {exc}"
        ) from exc
    if not isinstance(policy, dict) or policy.get("schema_version") != 1:
        raise ValueError("static security policy must use schema_version 1")
    if set(policy) != {
        "schema_version",
        "ruff_version",
        "blocking_rules",
        "test_exempt_rules",
        "suppressions",
    }:
        raise ValueError("static security policy has unexpected fields")
    ruff_version = policy.get("ruff_version")
    if not isinstance(ruff_version, str) or not re.fullmatch(
        r"[0-9]+\.[0-9]+\.[0-9]+", ruff_version
    ):
        raise ValueError("static security policy must pin an exact ruff version")
    for field in ("blocking_rules", "test_exempt_rules"):
        rules = policy.get(field)
        if (
            not isinstance(rules, list)
            or not rules
            or rules != sorted(set(rules))
            or any(
                not isinstance(rule, str) or _RULE_PATTERN.fullmatch(rule) is None
                for rule in rules
            )
        ):
            raise ValueError(
                f"static security policy {field} must be a sorted list of "
                "exact S-rule ids"
            )
    if not set(policy["test_exempt_rules"]) <= set(policy["blocking_rules"]):
        raise ValueError(
            "test-exempt rules must be a subset of the blocking rules"
        )
    suppressions = policy.get("suppressions")
    if not isinstance(suppressions, list):
        raise ValueError("static security policy suppressions must be a list")
    seen: set[tuple[str, str]] = set()
    for index, item in enumerate(suppressions):
        context = f"suppression record {index}"
        if not isinstance(item, dict) or set(item) != {
            "rule",
            "path",
            "count",
            "expires",
            "reason",
            "references",
        }:
            raise ValueError(f"{context} has unexpected fields")
        if item.get("rule") not in policy["blocking_rules"]:
            raise ValueError(f"{context} names a rule outside the blocking set")
        path_value = item.get("path")
        if not isinstance(path_value, str) or not path_value:
            raise ValueError(f"{context} must carry a non-empty path")
        count = item.get("count")
        if not isinstance(count, int) or isinstance(count, bool) or count < 1:
            raise ValueError(f"{context} must carry a positive finding count")
        reason = item.get("reason")
        if not isinstance(reason, str) or not reason.strip():
            raise ValueError(f"{context} must carry a non-empty reason")
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
        try:
            date.fromisoformat(item.get("expires", ""))
        except (TypeError, ValueError) as exc:
            raise ValueError(f"{context} has an invalid expires date") from exc
        key = (item["rule"], path_value)
        if key in seen:
            raise ValueError(f"{context} duplicates an earlier record")
        seen.add(key)
    return policy


def _run_ruff(rules: list[str], root: Path) -> list[dict[str, Any]]:
    command = (
        sys.executable,
        "-m",
        "ruff",
        "check",
        "--select",
        ",".join(rules),
        "--output-format",
        "json",
        "--exit-zero",
        ".",
    )
    completed = subprocess.run(
        command,
        cwd=root,
        capture_output=True,
        check=False,
    )
    if completed.returncode != 0:
        raise ValueError(
            f"ruff invocation failed with status {completed.returncode}"
        )
    try:
        payload = json.loads(completed.stdout.decode("utf-8"))
    except ValueError as exc:
        raise ValueError("ruff produced unparseable JSON output") from exc
    if not isinstance(payload, list):
        raise ValueError("ruff JSON output must be a list")
    return payload


def _ruff_version() -> str:
    completed = subprocess.run(
        (sys.executable, "-m", "ruff", "--version"),
        capture_output=True,
        check=False,
        text=True,
    )
    match = re.search(r"([0-9]+\.[0-9]+\.[0-9]+)", completed.stdout or "")
    if completed.returncode != 0 or match is None:
        raise ValueError("cannot determine the installed ruff version")
    return match.group(1)


def _normalize(root: Path, raw: list[dict[str, Any]]) -> list[dict[str, Any]]:
    findings: list[dict[str, Any]] = []
    for item in raw:
        if not isinstance(item, dict):
            raise ValueError("ruff finding entries must be objects")
        code = item.get("code")
        filename = item.get("filename")
        location = item.get("location")
        if (
            not isinstance(code, str)
            or not isinstance(filename, str)
            or not isinstance(location, dict)
            or not isinstance(location.get("row"), int)
        ):
            raise ValueError("ruff finding entry is missing required fields")
        path = Path(filename)
        if path.is_absolute():
            path = path.relative_to(root.resolve())
        findings.append(
            {
                "path": path.as_posix(),
                "row": location["row"],
                "rule": code,
            }
        )
    findings.sort(key=lambda row: (row["path"], row["rule"], row["row"]))
    return findings


def evaluate(
    findings: list[dict[str, Any]], policy: dict[str, Any], *, today: date
) -> tuple[list[str], list[dict[str, Any]]]:
    """Return (errors, reported rows) for normalized findings under policy."""
    test_exempt = set(policy["test_exempt_rules"])
    suppressions = {
        (item["rule"], item["path"]): item for item in policy["suppressions"]
    }
    errors: list[str] = []
    reported: list[dict[str, Any]] = []
    observed: dict[tuple[str, str], int] = {}
    for finding in findings:
        rule = finding["rule"]
        path = finding["path"]
        if rule in test_exempt and (
            path.startswith("tests/") or path.startswith("evaluation/")
        ):
            continue
        row = dict(finding)
        key = (rule, path)
        record = suppressions.get(key)
        if record is None:
            row["suppressed_through"] = None
            errors.append(
                f"{path}:{finding['row']}: {rule} is not accepted by policy"
            )
        else:
            observed[key] = observed.get(key, 0) + 1
            expires = date.fromisoformat(record["expires"])
            if expires < today:
                row["suppressed_through"] = None
                errors.append(
                    f"{path}:{finding['row']}: {rule} suppression expired "
                    f"on {expires}"
                )
            else:
                row["suppressed_through"] = expires.isoformat()
                print(
                    f"{path}:{finding['row']}: {rule} suppressed through "
                    f"{expires}"
                )
        reported.append(row)
    for item in policy["suppressions"]:
        key = (item["rule"], item["path"])
        count = observed.get(key, 0)
        if count == 0:
            errors.append(
                f"suppression record ({item['rule']}, {item['path']}) "
                "matched nothing"
            )
        elif count != item["count"]:
            errors.append(
                f"suppression record ({item['rule']}, {item['path']}) "
                f"declares {item['count']} finding(s) but {count} were "
                "observed"
            )
    return errors, reported


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--policy", type=Path, required=True)
    parser.add_argument("--root", type=Path, default=Path("."))
    parser.add_argument("--output-envelope", type=Path, default=None)
    args = parser.parse_args(argv)
    try:
        policy = _load_policy(args.policy)
        installed = _ruff_version()
        if installed != policy["ruff_version"]:
            raise ValueError(
                f"installed ruff {installed} differs from the pinned "
                f"{policy['ruff_version']}"
            )
        raw = _run_ruff(policy["blocking_rules"], args.root)
        findings = _normalize(args.root, raw)
        errors, reported = evaluate(
            findings, policy, today=datetime.now(timezone.utc).date()
        )
    except ValueError as exc:
        print(f"error: {exc}")
        return 1
    if args.output_envelope is not None:
        envelope = {
            "blocking_rules": policy["blocking_rules"],
            "findings": reported,
            "ruff_version": policy["ruff_version"],
            "scan_time": datetime.now(timezone.utc).isoformat(),
            "schema_version": 1,
            "violations": len(errors),
        }
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
        "Static security policy passed: "
        f"{len(reported)} suppressed finding(s), 0 violations."
    )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
