#!/usr/bin/env python3
"""Validate direct dependency bounds and reproducible automation pins."""

from __future__ import annotations

import argparse
import json
import re
import subprocess
import sys
from collections.abc import Iterable
from pathlib import Path
from typing import Any


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
    "requirements-service.txt",
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
    "requirements-service.lock": ("requirements-service.txt",),
    "requirements-smoke.lock": (
        "requirements-test.txt",
        "requirements-smoke.txt",
    ),
    "requirements-security.lock": ("requirements-security.txt",),
    "requirements-lock-tools.lock": ("requirements-lock-tools.txt",),
}
DIRECT_INPUT_LOCKS = {
    "requirements.txt": (
        "requirements-core.lock", "requirements-full.lock",
    ),
    "requirements-optional.txt": ("requirements-full.lock",),
    "requirements-smoke.txt": ("requirements-smoke.lock",),
    "requirements-audit.txt": ("requirements-full.lock",),
    "requirements-test.txt": (
        "requirements-test.lock", "requirements-smoke.lock",
    ),
    "requirements-service.txt": ("requirements-service.lock",),
    "requirements-security.txt": ("requirements-security.lock",),
    "requirements-lock-tools.txt": ("requirements-lock-tools.lock",),
}
AGGREGATE_FILES = {
    "requirements-all.txt": (
        "requirements.txt",
        "requirements-optional.txt",
    ),
}
DEPENDENCY_DOMAIN_POLICY = "dependency-compatibility-domains.json"
DEPENDABOT_CONFIG = ".github/dependabot.yml"
DEPENDENCY_DOMAIN_SCHEMA_VERSION = 1
# Dependabot monitors these eight governed requirement manifests. GPU helper
# commands and future packaging metadata have separate support contracts and
# must not be implied to be covered by this manifest-domain map.
DIRECT_DEPENDENCY_FILES = BOUNDED_FILES + PINNED_FILES
# These providers use the repository-owned Requests transport. Reintroducing an
# SDK expands endpoint, retry, credential, and transitive-package behavior and
# therefore requires an explicit policy/code review rather than lock drift.
OWNED_TRANSPORT_PROVIDER_SDKS = frozenset({"cohere", "openai", "voyageai"})
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


def _normalize_package(name: str) -> str:
    return re.sub(r"[-_.]+", "-", name).lower()


def _reject_duplicate_json_keys(
    pairs: list[tuple[str, Any]],
) -> dict[str, Any]:
    """Build one JSON object while rejecting last-key-wins ambiguity."""
    result: dict[str, Any] = {}
    for key, value in pairs:
        if key in result:
            raise ValueError(f"duplicate JSON key {key!r}")
        result[key] = value
    return result


def _load_dependency_domains(
    root: Path,
) -> tuple[dict[str, tuple[str, ...]], list[str]]:
    """Load the canonical compatibility-domain map with strict shape checks."""
    errors: list[str] = []
    path = root / DEPENDENCY_DOMAIN_POLICY
    try:
        payload: Any = json.loads(
            path.read_text(encoding="utf-8"),
            object_pairs_hook=_reject_duplicate_json_keys,
        )
    except (OSError, json.JSONDecodeError, ValueError) as exc:
        return {}, [f"{DEPENDENCY_DOMAIN_POLICY}: cannot load policy: {exc}"]
    if not isinstance(payload, dict):
        return {}, [f"{DEPENDENCY_DOMAIN_POLICY}: top level must be an object"]
    expected_fields = {"schema_version", "domains"}
    if set(payload) != expected_fields:
        errors.append(
            f"{DEPENDENCY_DOMAIN_POLICY}: fields must be exactly "
            "domains and schema_version"
        )
    schema_version = payload.get("schema_version")
    if (
        type(schema_version) is not int
        or schema_version != DEPENDENCY_DOMAIN_SCHEMA_VERSION
    ):
        errors.append(
            f"{DEPENDENCY_DOMAIN_POLICY}: schema_version must be "
            f"the integer {DEPENDENCY_DOMAIN_SCHEMA_VERSION}"
        )
    raw_domains = payload.get("domains")
    if not isinstance(raw_domains, dict) or not raw_domains:
        errors.append(
            f"{DEPENDENCY_DOMAIN_POLICY}: domains must be a non-empty object"
        )
        return {}, errors

    domains: dict[str, tuple[str, ...]] = {}
    assigned: dict[str, str] = {}
    for domain, raw_packages in raw_domains.items():
        context = f"{DEPENDENCY_DOMAIN_POLICY}: domain {domain!r}"
        if (
            not isinstance(domain, str)
            or re.fullmatch(r"[a-z0-9]+(?:-[a-z0-9]+)*", domain) is None
        ):
            errors.append(
                f"{DEPENDENCY_DOMAIN_POLICY}: invalid domain name {domain!r}"
            )
            continue
        if not isinstance(raw_packages, list) or not raw_packages:
            errors.append(f"{context} must be a non-empty package list")
            continue
        packages: list[str] = []
        for package in raw_packages:
            if (
                not isinstance(package, str)
                or not package
                or package != _normalize_package(package)
                or re.fullmatch(r"[a-z0-9]+(?:-[a-z0-9]+)*", package) is None
            ):
                errors.append(f"{context} has invalid package {package!r}")
                continue
            previous = assigned.setdefault(package, domain)
            if previous != domain or package in packages:
                errors.append(
                    f"{DEPENDENCY_DOMAIN_POLICY}: package {package!r} is "
                    "assigned more than once"
                )
            packages.append(package)
        if packages != sorted(packages):
            errors.append(f"{context} packages must use canonical sort order")
        domains[domain] = tuple(packages)
    return domains, errors


def _yaml_scalar(value: str) -> str:
    """Decode one scalar from the repository's constrained YAML dialect."""
    value = value.strip()
    if not value:
        raise ValueError("scalar must not be empty")
    if value.startswith('"'):
        try:
            decoded: Any = json.loads(value)
        except json.JSONDecodeError as exc:
            raise ValueError(f"invalid quoted scalar: {exc.msg}") from exc
        if not isinstance(decoded, str):
            raise ValueError("quoted scalar must decode to a string")
        return decoded
    if value.startswith("'"):
        if len(value) < 2 or value[-1] != "'":
            raise ValueError("unterminated quoted scalar")
        inner = value[1:-1]
        if "'" in inner.replace("''", ""):
            raise ValueError("invalid single-quoted scalar")
        return inner.replace("''", "'")
    if re.fullmatch(r"(?:/|[A-Za-z0-9][A-Za-z0-9._/-]*)", value) is None:
        raise ValueError("noncanonical bare scalar")
    return value


def _dependabot_pip_groups(
    path: Path,
) -> tuple[dict[str, tuple[str, ...]], list[str]]:
    """Validate the full constrained Dependabot document and return pip groups.

    This is intentionally not a general YAML parser.  The repository permits a
    small block-style dialect so the dependency-light policy gate can reject
    every uninspected update entry, alias, flow mapping, or unknown field.
    """
    try:
        lines = path.read_text(encoding="utf-8").splitlines()
    except OSError as exc:
        return {}, [f"{DEPENDABOT_CONFIG}: cannot load configuration: {exc}"]
    errors: list[str] = []
    significant = [
        (number, line)
        for number, line in enumerate(lines, 1)
        if line.strip() and not line.lstrip().startswith("#")
    ]
    if (
        len(significant) < 2
        or [line for _number, line in significant[:2]]
        != ["version: 2", "updates:"]
    ):
        errors.append(
            f"{DEPENDABOT_CONFIG}: document must begin with canonical "
            "version: 2 and updates keys"
        )
        return {}, errors

    updates: list[dict[str, Any]] = []
    current_update: dict[str, Any] | None = None
    current_section: str | None = None
    current_group: str | None = None
    nested_sections = {"schedule", "groups", "commit-message"}
    scalar_fields = {"directory", "open-pull-requests-limit"}
    nested_fields = {
        "schedule": {"interval", "day", "time", "timezone"},
        "commit-message": {"prefix"},
    }

    for line_number, line in significant[2:]:
        if "\t" in line or line.rstrip() != line:
            errors.append(
                f"{DEPENDABOT_CONFIG}:{line_number}: tabs and trailing "
                "whitespace are not allowed"
            )
            continue
        update_match = re.fullmatch(
            r"  - package-ecosystem:\s*(.+)", line
        )
        if update_match is not None:
            try:
                ecosystem = _yaml_scalar(update_match.group(1))
            except ValueError as exc:
                errors.append(
                    f"{DEPENDABOT_CONFIG}:{line_number}: invalid ecosystem: "
                    f"{exc}"
                )
                ecosystem = ""
            current_update = {"package-ecosystem": ecosystem}
            updates.append(current_update)
            current_section = None
            current_group = None
            continue

        if current_update is None:
            errors.append(
                f"{DEPENDABOT_CONFIG}:{line_number}: content must belong to "
                "a canonical update item"
            )
            continue

        top_field = re.fullmatch(r"    ([a-z][a-z0-9-]*):\s*(.*)", line)
        if top_field is not None:
            field, raw_value = top_field.groups()
            if field in current_update:
                errors.append(
                    f"{DEPENDABOT_CONFIG}:{line_number}: duplicate update "
                    f"field {field!r}"
                )
                continue
            if field in scalar_fields:
                if field == "open-pull-requests-limit":
                    if re.fullmatch(r"[1-9][0-9]*", raw_value) is None:
                        errors.append(
                            f"{DEPENDABOT_CONFIG}:{line_number}: "
                            "open-pull-requests-limit must be an unquoted "
                            "positive integer"
                        )
                    else:
                        current_update[field] = raw_value
                else:
                    try:
                        current_update[field] = _yaml_scalar(raw_value)
                    except ValueError as exc:
                        errors.append(
                            f"{DEPENDABOT_CONFIG}:{line_number}: invalid "
                            f"{field}: {exc}"
                        )
                if field not in current_update:
                    errors.append(
                        f"{DEPENDABOT_CONFIG}:{line_number}: update field "
                        f"{field!r} was not accepted"
                    )
                current_section = None
                current_group = None
            elif field in nested_sections and not raw_value:
                current_update[field] = {}
                current_section = field
                current_group = None
            else:
                errors.append(
                    f"{DEPENDABOT_CONFIG}:{line_number}: unknown or "
                    f"noncanonical update field {field!r}"
                )
            continue

        group_match = re.fullmatch(
            r"      ([a-z0-9]+(?:-[a-z0-9]+)*):\s*", line
        )
        if current_section == "groups" and group_match is not None:
            current_group = group_match.group(1)
            groups = current_update["groups"]
            if current_group in groups:
                errors.append(
                    f"{DEPENDABOT_CONFIG}:{line_number}: duplicate group "
                    f"{current_group!r}"
                )
            else:
                groups[current_group] = {}
            continue

        nested_field = re.fullmatch(
            r"      ([a-z][a-z0-9-]*):\s*(.+)", line
        )
        if current_section in nested_fields and nested_field is not None:
            field, raw_value = nested_field.groups()
            section = current_update[current_section]
            if field not in nested_fields[current_section]:
                errors.append(
                    f"{DEPENDABOT_CONFIG}:{line_number}: unknown "
                    f"{current_section} field {field!r}"
                )
            elif field in section:
                errors.append(
                    f"{DEPENDABOT_CONFIG}:{line_number}: duplicate "
                    f"{current_section} field {field!r}"
                )
            else:
                try:
                    section[field] = _yaml_scalar(raw_value)
                except ValueError as exc:
                    errors.append(
                        f"{DEPENDABOT_CONFIG}:{line_number}: invalid "
                        f"{current_section} field {field!r}: {exc}"
                    )
            continue

        group_field = re.fullmatch(
            r"        ([a-z][a-z0-9-]*):\s*(.+)", line
        )
        if (
            current_section == "groups"
            and current_group is not None
            and group_field is not None
        ):
            field, raw_value = group_field.groups()
            rules = current_update["groups"].get(current_group, {})
            if field != "patterns":
                errors.append(
                    f"{DEPENDABOT_CONFIG}:{line_number}: group "
                    f"{current_group!r} may contain only patterns"
                )
                continue
            if field in rules:
                errors.append(
                    f"{DEPENDABOT_CONFIG}:{line_number}: group "
                    f"{current_group!r} repeats patterns"
                )
                continue
            try:
                values: Any = json.loads(raw_value)
            except json.JSONDecodeError as exc:
                errors.append(
                    f"{DEPENDABOT_CONFIG}:{line_number}: patterns must be an "
                    f"inline JSON string list: {exc.msg}"
                )
                continue
            if not isinstance(values, list) or not values or not all(
                isinstance(value, str) and value for value in values
            ):
                errors.append(
                    f"{DEPENDABOT_CONFIG}:{line_number}: patterns must be a "
                    "non-empty string list"
                )
                continue
            rules[field] = tuple(values)
            continue

        errors.append(
            f"{DEPENDABOT_CONFIG}:{line_number}: unsupported YAML syntax or "
            "indentation"
        )

    ecosystems = [update.get("package-ecosystem") for update in updates]
    if sorted(ecosystems) != ["github-actions", "pip"]:
        errors.append(
            f"{DEPENDABOT_CONFIG}: expected exactly one pip and one "
            "github-actions update block"
        )

    expected_update_fields = {
        "package-ecosystem",
        "directory",
        "schedule",
        "open-pull-requests-limit",
        "groups",
        "commit-message",
    }
    expected_controls = {
        "pip": {
            "open-pull-requests-limit": "10",
            "schedule": {
                "interval": "weekly",
                "day": "monday",
                "time": "08:00",
                "timezone": "America/Denver",
            },
            "prefix": "deps",
        },
        "github-actions": {
            "open-pull-requests-limit": "5",
            "schedule": {
                "interval": "weekly",
                "day": "monday",
                "time": "08:30",
                "timezone": "America/Denver",
            },
            "prefix": "ci",
        },
    }
    for update in updates:
        ecosystem = update.get("package-ecosystem") or "<invalid>"
        if set(update) != expected_update_fields:
            errors.append(
                f"{DEPENDABOT_CONFIG}: {ecosystem} update fields must be "
                "exactly commit-message, directory, groups, "
                "open-pull-requests-limit, package-ecosystem, and schedule"
            )
        if update.get("directory") != "/":
            errors.append(
                f"{DEPENDABOT_CONFIG}: {ecosystem} directory must be /"
            )
        limit = update.get("open-pull-requests-limit")
        if not isinstance(limit, str) or not limit.isdigit() or int(limit) < 1:
            errors.append(
                f"{DEPENDABOT_CONFIG}: {ecosystem} needs a positive "
                "open-pull-requests-limit"
            )
        schedule = update.get("schedule")
        if not isinstance(schedule, dict) or set(schedule) != {
            "interval", "day", "time", "timezone"
        }:
            errors.append(
                f"{DEPENDABOT_CONFIG}: {ecosystem} schedule fields must be "
                "exactly day, interval, time, and timezone"
            )
        commit_message = update.get("commit-message")
        if not isinstance(commit_message, dict) or set(commit_message) != {
            "prefix"
        }:
            errors.append(
                f"{DEPENDABOT_CONFIG}: {ecosystem} commit-message needs "
                "exactly one prefix"
            )
        groups = update.get("groups")
        if not isinstance(groups, dict) or not groups:
            errors.append(
                f"{DEPENDABOT_CONFIG}: {ecosystem} needs non-empty groups"
            )
        elif any(set(rules) != {"patterns"} for rules in groups.values()):
            errors.append(
                f"{DEPENDABOT_CONFIG}: every {ecosystem} group needs exactly "
                "one patterns field"
            )
        controls = expected_controls.get(ecosystem)
        if controls is not None:
            if limit != controls["open-pull-requests-limit"]:
                errors.append(
                    f"{DEPENDABOT_CONFIG}: {ecosystem} pull-request limit "
                    "differs from the declared repository control"
                )
            if schedule != controls["schedule"]:
                errors.append(
                    f"{DEPENDABOT_CONFIG}: {ecosystem} schedule differs "
                    "from the declared repository control"
                )
            if (
                not isinstance(commit_message, dict)
                or commit_message.get("prefix") != controls["prefix"]
            ):
                errors.append(
                    f"{DEPENDABOT_CONFIG}: {ecosystem} commit prefix differs "
                    "from the declared repository control"
                )
        if ecosystem == "github-actions" and groups != {
            "github-actions": {"patterns": ("*",)}
        }:
            errors.append(
                f"{DEPENDABOT_CONFIG}: github-actions group must remain the "
                "single declared wildcard group"
            )

    pip_updates = [
        update for update in updates
        if update.get("package-ecosystem") == "pip"
    ]
    if len(pip_updates) != 1:
        return {}, errors
    raw_groups = pip_updates[0].get("groups")
    if not isinstance(raw_groups, dict):
        return {}, errors
    groups: dict[str, tuple[str, ...]] = {}
    for group, rules in raw_groups.items():
        patterns = rules.get("patterns") if isinstance(rules, dict) else None
        if not isinstance(patterns, tuple):
            continue
        if any(any(mark in value for mark in "*?[]") for value in patterns):
            errors.append(
                f"{DEPENDABOT_CONFIG}: dependency-domain patterns must be "
                "exact package names"
            )
        groups[group] = patterns
    return groups, errors


def validate_dependency_domains(root: Path = PROJECT_ROOT) -> list[str]:
    """Require one exact compatibility domain for every direct dependency."""
    domains, errors = _load_dependency_domains(root)
    yaml_groups, yaml_errors = _dependabot_pip_groups(
        root / DEPENDABOT_CONFIG
    )
    errors.extend(yaml_errors)

    direct: set[str] = set()
    for filename in DIRECT_DEPENDENCY_FILES:
        path = root / filename
        if not path.is_file():
            continue
        for line_number, line in _logical_lines(path):
            try:
                parsed = _requirement(path, line_number, line)
            except ValueError:
                continue
            if parsed is not None:
                direct.add(_normalize_package(parsed[0].split("[", 1)[0]))

    assigned = {
        package for packages in domains.values() for package in packages
    }
    missing = sorted(direct.difference(assigned))
    unknown = sorted(assigned.difference(direct))
    if missing:
        errors.append(
            f"{DEPENDENCY_DOMAIN_POLICY}: unassigned direct dependencies: "
            + ", ".join(missing)
        )
    if unknown:
        errors.append(
            f"{DEPENDENCY_DOMAIN_POLICY}: unknown assigned dependencies: "
            + ", ".join(unknown)
        )
    if yaml_groups != domains:
        errors.append(
            f"{DEPENDABOT_CONFIG}: pip groups differ from "
            f"{DEPENDENCY_DOMAIN_POLICY}"
        )
    return errors


def _direct_requirement_records(text: str, filename: str) -> dict[str, tuple[str, ...]]:
    """Return normalized direct-input records from one requirement document."""
    records: dict[str, set[str]] = {}
    path = Path(filename)
    for line_number, raw_line in enumerate(text.splitlines(), 1):
        line = raw_line.split("#", 1)[0].strip()
        if not line:
            continue
        try:
            parsed = _requirement(path, line_number, line)
        except ValueError:
            continue
        if parsed is None:
            continue
        name = _normalize_package(parsed[0].split("[", 1)[0])
        records.setdefault(name, set()).add(" ".join(line.split()))
    return {
        name: tuple(sorted(values)) for name, values in records.items()
    }


def _lock_requirement_records(
    text: str, filename: str,
) -> dict[str, tuple[str, ...]]:
    """Return normalized locked direct-package records from lock text."""
    records: dict[str, set[str]] = {}
    pending = ""
    start_line = 0
    for line_number, raw_line in enumerate(text.splitlines(), 1):
        line = raw_line.split("#", 1)[0].strip()
        if not line:
            continue
        if not pending:
            start_line = line_number
        continuation = line.endswith("\\")
        fragment = line[:-1].rstrip() if continuation else line
        pending = f"{pending} {fragment}".strip()
        if continuation:
            continue
        requirement_line = pending.split(" --hash=", 1)[0]
        pending = ""
        if requirement_line.startswith("--index-url "):
            continue
        try:
            parsed = _requirement(
                Path(filename), start_line, requirement_line,
            )
        except ValueError:
            continue
        if parsed is None:
            continue
        name = _normalize_package(parsed[0].split("[", 1)[0])
        records.setdefault(name, set()).add(
            " ".join(requirement_line.split())
        )
    return {
        name: tuple(sorted(values)) for name, values in records.items()
    }


def _domain_assignments_from_text(
    text: str, source: str,
) -> tuple[dict[str, str], list[str]]:
    """Read package-to-domain assignments from a committed policy snapshot."""
    try:
        payload: Any = json.loads(
            text, object_pairs_hook=_reject_duplicate_json_keys,
        )
    except (json.JSONDecodeError, ValueError) as exc:
        return {}, [f"{source}: cannot load domain policy: {exc}"]
    raw_domains = payload.get("domains") if isinstance(payload, dict) else None
    if not isinstance(raw_domains, dict):
        return {}, [f"{source}: domain policy has no domains object"]
    assignments: dict[str, str] = {}
    for domain, packages in raw_domains.items():
        if not isinstance(domain, str) or not isinstance(packages, list):
            return {}, [f"{source}: malformed domain assignment"]
        for package in packages:
            if not isinstance(package, str) or package in assignments:
                return {}, [f"{source}: malformed package assignment"]
            assignments[package] = domain
    return assignments, []


def _subprocess_detail(exc: Exception) -> str:
    """Return the diagnostic text for a failed git or filesystem call."""
    if isinstance(exc, subprocess.CalledProcessError) and isinstance(
            exc.output, str):
        return exc.output.strip()
    return str(exc)


def _committed_text(root: Path, revision: str, filename: str) -> str:
    """Return one committed file's text, or empty text when absent."""
    try:
        return subprocess.check_output(
            ["git", "-C", str(root), "show", f"{revision}:{filename}"],
            stderr=subprocess.STDOUT,
            text=True,
            encoding="utf-8",
        )
    except subprocess.CalledProcessError as exc:
        if "does not exist" in exc.output or "exists on disk" in exc.output:
            return ""
        raise


def _committed_object_id(
    root: Path, revision: str, filename: str,
) -> str | None:
    """Return the committed object ID for one exact repository path."""
    output = subprocess.check_output(
        ["git", "-C", str(root), "ls-tree", revision, "--", filename],
        stderr=subprocess.STDOUT,
        text=True,
    ).strip()
    if not output:
        return None
    metadata = output.split("\t", 1)[0].split()
    if len(metadata) != 3:
        raise ValueError(f"unexpected git ls-tree output for {filename}")
    return metadata[2]


def _domain_diff_base(
    root: Path, base_ref: str,
) -> tuple[str | None, set[str], list[str]]:
    """Resolve the exact base commit and the base..HEAD changed paths."""
    if re.fullmatch(r"[0-9a-f]{40}", base_ref) is None:
        return None, set(), [
            "--base-ref must be one exact lowercase 40-character commit SHA"]
    try:
        base_sha = subprocess.check_output(
            [
                "git", "-C", str(root), "rev-parse", "--verify",
                "--end-of-options", f"{base_ref}^{{commit}}",
            ],
            stderr=subprocess.STDOUT,
            text=True,
        ).strip()
        changed_paths = set(
            subprocess.check_output(
                [
                    "git", "-C", str(root), "diff", "--name-only",
                    base_sha, "HEAD", "--",
                ],
                stderr=subprocess.STDOUT,
                text=True,
            ).splitlines()
        )
    except (OSError, subprocess.CalledProcessError) as exc:
        return None, set(), [
            f"cannot inspect dependency-domain base {base_ref}: "
            f"{_subprocess_detail(exc)}"]
    return base_sha, changed_paths, []


def _changed_direct_records(
    root: Path, base_sha: str,
) -> tuple[set[str], dict[str, set[str]], str]:
    """Diff the direct requirement manifests and read the base policy."""
    changed_packages: set[str] = set()
    direct_package_locks: dict[str, set[str]] = {}
    for filename in DIRECT_DEPENDENCY_FILES:
        before = _direct_requirement_records(
            _committed_text(root, base_sha, filename), filename,
        )
        current_path = root / filename
        after = _direct_requirement_records(
            current_path.read_text(encoding="utf-8")
            if current_path.is_file() else "",
            filename,
        )
        for package in set(before).union(after):
            if before.get(package) != after.get(package):
                changed_packages.add(package)
                direct_package_locks.setdefault(package, set()).update(
                    DIRECT_INPUT_LOCKS[filename])
    base_policy = _committed_text(root, base_sha, DEPENDENCY_DOMAIN_POLICY)
    return changed_packages, direct_package_locks, base_policy


def _content_changed_lock_paths(
    root: Path, base_sha: str, changed_lock_paths: set[str],
) -> set[str]:
    """Return the lock paths whose committed blob identity changed."""
    return {
        filename
        for filename in changed_lock_paths
        if _committed_object_id(root, base_sha, filename)
        != _committed_object_id(root, "HEAD", filename)
    }


def _lock_changed_packages(
    root: Path, base_sha: str, relevant_packages: set[str],
) -> dict[str, set[str]]:
    """Return each lock's governed packages whose selected record changed."""
    lock_changed_packages_by_file: dict[str, set[str]] = {}
    for filename in LOCK_FILES:
        file_changes = lock_changed_packages_by_file.setdefault(
            filename, set())
        before = _lock_requirement_records(
            _committed_text(root, base_sha, filename), filename,
        )
        current_path = root / filename
        after = _lock_requirement_records(
            current_path.read_text(encoding="utf-8")
            if current_path.is_file() else "",
            filename,
        )
        for package in relevant_packages:
            if before.get(package) != after.get(package):
                file_changes.add(package)
    return lock_changed_packages_by_file


def _domain_attribution_errors(
    changed_packages: set[str],
    base_assignments: dict[str, str],
    current_assignments: dict[str, str],
) -> list[str]:
    """Reject changes without a domain or spanning more than one domain."""
    errors: list[str] = []
    changed_domains: set[str] = set()
    unassigned: list[str] = []
    for package in sorted(changed_packages):
        package_domains = {
            assignment for assignment in (
                base_assignments.get(package),
                current_assignments.get(package),
            ) if assignment is not None
        }
        if not package_domains:
            unassigned.append(package)
        changed_domains.update(package_domains)
    if unassigned:
        errors.append("changed direct dependencies have no current or base "
                      "domain: " + ", ".join(unassigned))
    if len(changed_domains) > 1:
        errors.append("direct dependency changes span more than one "
                      "compatibility domain: "
                      + ", ".join(sorted(changed_domains)))
    return errors


def validate_dependency_domain_diff(
    base_ref: str, root: Path = PROJECT_ROOT,
) -> list[str]:
    """Reject direct-input changes spanning domains or omitting lock changes."""
    base_sha, changed_paths, base_errors = _domain_diff_base(root, base_ref)
    if base_sha is None:
        return base_errors

    errors: list[str] = []
    try:
        changed_packages, direct_package_locks, base_policy = (
            _changed_direct_records(root, base_sha))
    except (OSError, subprocess.CalledProcessError) as exc:
        return [f"cannot compare dependency-domain inputs: "
                f"{_subprocess_detail(exc)}"]

    current_domains, current_errors = _load_dependency_domains(root)
    errors.extend(current_errors)
    current_assignments = {
        package: domain
        for domain, packages in current_domains.items()
        for package in packages
    }
    changed_lock_paths = changed_paths.intersection(LOCK_FILES)
    try:
        content_changed_locks = _content_changed_lock_paths(
            root, base_sha, changed_lock_paths)
    except (OSError, subprocess.CalledProcessError, ValueError) as exc:
        return errors + [f"cannot compare dependency lock objects: "
                         f"{_subprocess_detail(exc)}"]
    if not base_policy.strip():
        # The policy-introduction PR intentionally changes no dependency or
        # lock input. Ordinary later PRs take the committed map as their base.
        if changed_packages or changed_lock_paths:
            errors.append("the domain-policy introduction may not change "
                          "direct dependencies or lockfiles")
        return errors
    base_assignments, base_assignment_errors = _domain_assignments_from_text(
        base_policy, f"{base_sha}:{DEPENDENCY_DOMAIN_POLICY}",
    )
    errors.extend(base_assignment_errors)

    relevant_packages = set(base_assignments).union(current_assignments)
    try:
        lock_changed_packages_by_file = _lock_changed_packages(
            root, base_sha, relevant_packages)
    except (OSError, subprocess.CalledProcessError) as exc:
        return errors + [f"cannot compare dependency lock records: "
                         f"{_subprocess_detail(exc)}"]

    changed_packages.update(*lock_changed_packages_by_file.values())
    unchanged_direct_packages = sorted(
        package
        for package, mapped_locks in direct_package_locks.items()
        if not any(package in lock_changed_packages_by_file.get(lock, set())
                   for lock in mapped_locks)
    )
    if unchanged_direct_packages:
        errors.append(
            "direct dependency changes leave selected records unchanged in "
            "every mapped lock: " + ", ".join(unchanged_direct_packages)
        )
    errors.extend(_domain_attribution_errors(
        changed_packages, base_assignments, current_assignments))
    if (changed_lock_paths and not any(lock_changed_packages_by_file.values())
            and not direct_package_locks):
        errors.append(
            "lockfile changes are not attributable to a changed governed "
            "direct dependency; promote it into the governed input/domain and "
            "change its selected record in this one-domain PR, or add a "
            "machine-readable exception mechanism before the lock change"
        )
    required_locks = set().union(*direct_package_locks.values())
    missing_locks = sorted(required_locks.difference(content_changed_locks))
    if missing_locks:
        errors.append(
            "direct dependency changes are missing required regenerated lockfile "
            "changes: " + ", ".join(missing_locks))
    return errors


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
            if normalized in OWNED_TRANSPORT_PROVIDER_SDKS:
                errors.append(
                    f"{filename}:{line_number}: {name} is forbidden while the "
                    "provider uses the owned Requests transport"
                )
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
            if (filename == "requirements-full.lock"
                    and normalized in OWNED_TRANSPORT_PROVIDER_SDKS):
                errors.append(
                    f"{filename}:{line_number}: {name} unexpectedly expands "
                    "the owned provider transport dependency surface"
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
    errors.extend(validate_dependency_domains(root))
    return errors


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument(
        "--root", type=Path, default=PROJECT_ROOT,
        help="repository root containing requirement files",
    )
    parser.add_argument(
        "--base-ref",
        help="exact base commit SHA for one-domain-per-PR validation",
    )
    args = parser.parse_args(argv)
    root = args.root.resolve()
    errors = validate(root)
    if args.base_ref:
        errors.extend(validate_dependency_domain_diff(args.base_ref, root))
    if errors:
        print("Dependency policy violations:", file=sys.stderr)
        for error in errors:
            print(f"- {error}", file=sys.stderr)
        return 1
    print("Dependency policy passed.")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
