#!/usr/bin/env python3
"""Classify CI risk from a trusted policy and verify promotion results.

The classifier is deliberately dependency-free.  Its inputs are exact commit
identities plus NUL-delimited Git output, and its reports contain only known
group names, counts, and digests--never repository path names.
"""

from __future__ import annotations

import argparse
from collections import Counter
from dataclasses import dataclass
import hashlib
import json
import os
from pathlib import Path, PurePosixPath
import re
import subprocess
import sys
import tempfile
from typing import Any, Mapping, Sequence


PROJECT_ROOT = Path(__file__).resolve().parents[1]
POLICY_PATH = "ci-risk-policy.json"
POLICY_SCHEMA_VERSION = 1
DECISION_SCHEMA_VERSION = 2

ALWAYS_REQUIRED_JOBS = (
    "quality",
    "evaluation-smoke",
    "unit-linux",
)
HEAVY_JOBS = (
    "unit-windows",
    "service-api",
    "phase-a0",
    "vector-store-smoke",
    "full-integration",
)
EXECUTION_JOBS = ALWAYS_REQUIRED_JOBS + HEAVY_JOBS
FAST_PYTHON_VERSIONS = ("3.12",)
FULL_PYTHON_VERSIONS = ("3.10", "3.11", "3.12", "3.13", "3.14")
REQUIRED_GROUPS = frozenset({
    "service",
    "vector",
    "process_supervision",
    "storage_publication",
    "evaluation",
    "packaging",
    "security",
    "documentation_only",
})
DOCUMENTATION_GROUP = "documentation_only"
ALLOWED_GROUPS = REQUIRED_GROUPS | {"unknown"}
ALLOWED_REASON_CODES = frozenset({
    "candidate_force_heavy",
    "documentation_only",
    "forced_heavy",
    "manual_force_heavy",
    "merge_group_force_heavy",
    "non_documentation_change",
    "push_force_heavy",
    "unknown_change",
    "unsafe_or_unverified_mode",
})

_SHA_RE = re.compile(r"[0-9a-f]{40}")
_SHA256_RE = re.compile(r"[0-9a-f]{64}")
_GROUP_RE = re.compile(r"[a-z][a-z0-9_]*")
_STATUS_RE = re.compile(r"[ADMT]")
_RENAME_STATUS_RE = re.compile(r"([RC])([0-9]{1,3})")
_POLICY_KEYS = frozenset({
    "schema_version",
    "execution_jobs",
    "fast_python_versions",
    "full_python_versions",
    "documentation_group",
    "groups",
    "unknown",
})
_GROUP_KEYS = frozenset({"selectors", "python_lane", "required_jobs"})
_SELECTOR_KEYS = frozenset({"exact", "trees", "root_prefixes"})
_TREE_KEYS = frozenset({"prefix", "suffixes"})
_UNKNOWN_KEYS = frozenset({"python_lane", "required_jobs"})
_DECISION_KEYS = frozenset({
    "schema_version",
    "classifier_ok",
    "event_name",
    "base_sha",
    "head_sha",
    "candidate_sha",
    "trusted_sha",
    "policy_sha256",
    "change_set_sha256",
    "changed_count",
    "groups",
    "requirements",
    "pythons",
    "forced_heavy",
    "reason_codes",
    "decision_sha256",
})


class PromotionError(ValueError):
    """A policy, change set, decision, or promotion result is invalid."""


def _reject_duplicate_pairs(
    pairs: list[tuple[str, Any]],
) -> dict[str, Any]:
    value: dict[str, Any] = {}
    for key, item in pairs:
        if key in value:
            raise PromotionError(f"JSON object repeats key {key!r}")
        value[key] = item
    return value


def _loads_strict(text: str, context: str) -> Any:
    try:
        return json.loads(text, object_pairs_hook=_reject_duplicate_pairs)
    except json.JSONDecodeError as exc:
        raise PromotionError(f"cannot parse {context}: {exc}") from exc


def _canonical_json(value: object) -> str:
    return json.dumps(
        value,
        ensure_ascii=True,
        sort_keys=True,
        separators=(",", ":"),
    )


def _sha256(data: bytes) -> str:
    return hashlib.sha256(data).hexdigest()


def _strict_keys(
    value: object,
    expected: frozenset[str],
    context: str,
) -> dict[str, Any]:
    if not isinstance(value, dict) or any(
        not isinstance(key, str) for key in value
    ):
        raise PromotionError(f"{context} must be an object with text keys")
    keys = set(value)
    if keys != expected:
        missing = ", ".join(sorted(expected.difference(keys))) or "none"
        extra = ", ".join(sorted(keys.difference(expected))) or "none"
        raise PromotionError(
            f"{context} fields differ (missing: {missing}; extra: {extra})"
        )
    return value


def _text_list(
    value: object,
    context: str,
    *,
    nonempty: bool = True,
) -> tuple[str, ...]:
    if not isinstance(value, list) or any(
        not isinstance(item, str) or not item for item in value
    ):
        raise PromotionError(f"{context} must be a list of non-empty text")
    items = tuple(value)
    if nonempty and not items:
        raise PromotionError(f"{context} must not be empty")
    if len(items) != len(set(items)):
        raise PromotionError(f"{context} must not contain duplicates")
    return items


def _validate_sha(value: str, context: str) -> str:
    if _SHA_RE.fullmatch(value) is None:
        raise PromotionError(f"{context} must be an exact lowercase commit SHA")
    return value


def _is_policy_path(value: str) -> bool:
    if (
        not value
        or "\\" in value
        or any(character in value for character in "*?[]")
        or any(ord(character) < 32 for character in value)
    ):
        return False
    path = PurePosixPath(value)
    return (
        not path.is_absolute()
        and bool(path.parts)
        and all(part not in {"", ".", ".."} for part in path.parts)
        and path.as_posix() == value
    )


def _validate_change_path(value: str) -> str:
    if not value or "\x00" in value:
        raise PromotionError("Git emitted an empty or NUL-containing path")
    path = PurePosixPath(value)
    if (
        path.is_absolute()
        or not path.parts
        or any(part in {"", ".", ".."} for part in path.parts)
        or path.as_posix() != value
    ):
        raise PromotionError("Git emitted a non-repository path")
    return value


@dataclass(frozen=True, slots=True)
class TreeSelector:
    prefix: str
    suffixes: tuple[str, ...]

    def matches(self, path: str) -> bool:
        return path.startswith(self.prefix) and path.endswith(self.suffixes)


@dataclass(frozen=True, slots=True)
class Selectors:
    exact: frozenset[str]
    trees: tuple[TreeSelector, ...]
    root_prefixes: tuple[str, ...]

    def matches(self, path: str) -> bool:
        return (
            path in self.exact
            or any(selector.matches(path) for selector in self.trees)
            or (
                "/" not in path
                and any(path.startswith(prefix) for prefix in self.root_prefixes)
            )
        )


@dataclass(frozen=True, slots=True)
class GroupPolicy:
    selectors: Selectors
    python_lane: str
    required_jobs: frozenset[str]


@dataclass(frozen=True, slots=True)
class RiskPolicy:
    fast_python_versions: tuple[str, ...]
    full_python_versions: tuple[str, ...]
    groups: Mapping[str, GroupPolicy]
    unknown_python_lane: str
    unknown_required_jobs: frozenset[str]
    sha256: str


@dataclass(frozen=True, slots=True)
class TreeEndpoint:
    """Mode/type evidence for one logical path in one exact commit tree."""

    side: str
    endpoint: str
    exists: bool
    mode: str | None
    object_type: str | None


@dataclass(frozen=True, slots=True)
class Change:
    status: str
    old_path: str
    new_path: str | None = None
    tree_endpoints: tuple[TreeEndpoint, ...] = ()

    @property
    def paths(self) -> tuple[str, ...]:
        if self.new_path is None:
            return (self.old_path,)
        return (self.old_path, self.new_path)


@dataclass(frozen=True, slots=True)
class ChangeSet:
    changes: tuple[Change, ...]
    sha256: str


@dataclass(frozen=True, slots=True)
class Decision:
    event_name: str
    base_sha: str
    head_sha: str
    candidate_sha: str
    trusted_sha: str
    policy_sha256: str
    change_set_sha256: str
    changed_count: int
    groups: tuple[str, ...]
    requirements: Mapping[str, str]
    pythons: tuple[str, ...]
    forced_heavy: bool
    reason_codes: tuple[str, ...]

    def unsigned_payload(self) -> dict[str, object]:
        return {
            "schema_version": DECISION_SCHEMA_VERSION,
            "classifier_ok": True,
            "event_name": self.event_name,
            "base_sha": self.base_sha,
            "head_sha": self.head_sha,
            "candidate_sha": self.candidate_sha,
            "trusted_sha": self.trusted_sha,
            "policy_sha256": self.policy_sha256,
            "change_set_sha256": self.change_set_sha256,
            "changed_count": self.changed_count,
            "groups": list(self.groups),
            "requirements": dict(self.requirements),
            "pythons": list(self.pythons),
            "forced_heavy": self.forced_heavy,
            "reason_codes": list(self.reason_codes),
        }

    @property
    def decision_sha256(self) -> str:
        return _sha256(_canonical_json(self.unsigned_payload()).encode("ascii"))

    def payload(self) -> dict[str, object]:
        payload = self.unsigned_payload()
        payload["decision_sha256"] = self.decision_sha256
        return payload


@dataclass(frozen=True, slots=True)
class PromotionVerification:
    ok: bool
    errors: tuple[str, ...]


def _load_selectors(value: object, context: str) -> Selectors:
    payload = _strict_keys(value, _SELECTOR_KEYS, context)
    exact = _text_list(payload["exact"], f"{context}.exact", nonempty=False)
    for path in exact:
        if not _is_policy_path(path):
            raise PromotionError(f"{context}.exact has invalid path {path!r}")

    root_prefixes = _text_list(
        payload["root_prefixes"],
        f"{context}.root_prefixes",
        nonempty=False,
    )
    for prefix in root_prefixes:
        if (
            "/" in prefix
            or "\\" in prefix
            or any(character in prefix for character in "*?[]")
            or any(ord(character) < 32 for character in prefix)
        ):
            raise PromotionError(
                f"{context}.root_prefixes has invalid prefix {prefix!r}"
            )

    trees_value = payload["trees"]
    if not isinstance(trees_value, list):
        raise PromotionError(f"{context}.trees must be a list")
    trees: list[TreeSelector] = []
    for index, tree_value in enumerate(trees_value):
        tree_context = f"{context}.trees[{index}]"
        tree = _strict_keys(tree_value, _TREE_KEYS, tree_context)
        prefix = tree["prefix"]
        if (
            not isinstance(prefix, str)
            or not prefix.endswith("/")
            or not _is_policy_path(prefix[:-1])
        ):
            raise PromotionError(
                f"{tree_context}.prefix must be a safe directory prefix"
            )
        suffixes = _text_list(tree["suffixes"], f"{tree_context}.suffixes")
        if any(
            not suffix.startswith(".")
            or "/" in suffix
            or "\\" in suffix
            or any(character in suffix for character in "*?[]")
            or any(ord(character) < 32 for character in suffix)
            for suffix in suffixes
        ):
            raise PromotionError(f"{tree_context}.suffixes are invalid")
        trees.append(TreeSelector(prefix, suffixes))

    if len(trees) != len(set(trees)):
        raise PromotionError(f"{context}.trees must not contain duplicates")

    if not exact and not trees and not root_prefixes:
        raise PromotionError(f"{context} must declare at least one selector")
    return Selectors(frozenset(exact), tuple(trees), root_prefixes)


def load_policy(path: Path | str = POLICY_PATH) -> RiskPolicy:
    """Load and strictly validate the conservative v1 risk policy."""
    policy_path = Path(path)
    try:
        raw = policy_path.read_bytes()
        text = raw.decode("utf-8")
        value: Any = _loads_strict(text, "risk policy")
    except (OSError, UnicodeDecodeError, PromotionError) as exc:
        raise PromotionError(f"cannot load risk policy: {exc}") from exc
    payload = _strict_keys(value, _POLICY_KEYS, "risk policy")
    if (
        not isinstance(payload["schema_version"], int)
        or isinstance(payload["schema_version"], bool)
        or payload["schema_version"] != POLICY_SCHEMA_VERSION
    ):
        raise PromotionError(
            f"risk policy schema_version must be {POLICY_SCHEMA_VERSION}"
        )
    jobs = _text_list(payload["execution_jobs"], "execution_jobs")
    if jobs != EXECUTION_JOBS:
        raise PromotionError(
            "execution_jobs must equal the ordered workflow execution jobs"
        )
    fast_pythons = _text_list(
        payload["fast_python_versions"], "fast_python_versions"
    )
    full_pythons = _text_list(
        payload["full_python_versions"], "full_python_versions"
    )
    if fast_pythons != FAST_PYTHON_VERSIONS:
        raise PromotionError("fast_python_versions must be exactly ['3.12']")
    if full_pythons != FULL_PYTHON_VERSIONS:
        raise PromotionError("full_python_versions must be CPython 3.10-3.14")
    if payload["documentation_group"] != DOCUMENTATION_GROUP:
        raise PromotionError(
            f"documentation_group must be {DOCUMENTATION_GROUP!r}"
        )

    groups_value = payload["groups"]
    if not isinstance(groups_value, dict) or any(
        not isinstance(name, str) for name in groups_value
    ):
        raise PromotionError("groups must be an object")
    if set(groups_value) != REQUIRED_GROUPS:
        missing = ", ".join(sorted(REQUIRED_GROUPS.difference(groups_value)))
        extra = ", ".join(sorted(set(groups_value).difference(REQUIRED_GROUPS)))
        raise PromotionError(
            f"groups differ (missing: {missing or 'none'}; "
            f"extra: {extra or 'none'})"
        )

    groups: dict[str, GroupPolicy] = {}
    exact_owners: dict[str, str] = {}
    for name in sorted(groups_value):
        if _GROUP_RE.fullmatch(name) is None:
            raise PromotionError(f"invalid group name {name!r}")
        group_value = _strict_keys(
            groups_value[name], _GROUP_KEYS, f"groups.{name}"
        )
        selectors = _load_selectors(
            group_value["selectors"], f"groups.{name}.selectors"
        )
        lane = group_value["python_lane"]
        if lane not in {"fast", "full"}:
            raise PromotionError(f"groups.{name}.python_lane is invalid")
        required_jobs = frozenset(
            _text_list(group_value["required_jobs"], f"groups.{name}.required_jobs")
        )
        if not required_jobs.issubset(EXECUTION_JOBS):
            raise PromotionError(f"groups.{name}.required_jobs has unknown jobs")
        expected_jobs = (
            frozenset(ALWAYS_REQUIRED_JOBS)
            if name == DOCUMENTATION_GROUP
            else frozenset(EXECUTION_JOBS)
        )
        expected_lane = "fast" if name == DOCUMENTATION_GROUP else "full"
        if required_jobs != expected_jobs or lane != expected_lane:
            raise PromotionError(
                f"groups.{name} weakens the conservative v1 lane"
            )
        if name == DOCUMENTATION_GROUP and (
            any(not path.endswith(".md") for path in selectors.exact)
            or selectors.root_prefixes
            or any(tree.suffixes != (".md",) for tree in selectors.trees)
        ):
            raise PromotionError(
                "documentation_only selectors must match Markdown files only"
            )
        for exact in selectors.exact:
            previous = exact_owners.setdefault(exact, name)
            if previous != name:
                raise PromotionError(
                    f"exact path {exact!r} belongs to both {previous} and {name}"
                )
        groups[name] = GroupPolicy(selectors, lane, required_jobs)

    unknown_value = _strict_keys(payload["unknown"], _UNKNOWN_KEYS, "unknown")
    unknown_lane = unknown_value["python_lane"]
    unknown_jobs = frozenset(
        _text_list(unknown_value["required_jobs"], "unknown.required_jobs")
    )
    if unknown_lane != "full" or unknown_jobs != frozenset(EXECUTION_JOBS):
        raise PromotionError("unknown changes must require the complete lane")

    return RiskPolicy(
        fast_python_versions=fast_pythons,
        full_python_versions=full_pythons,
        groups=groups,
        unknown_python_lane=unknown_lane,
        unknown_required_jobs=unknown_jobs,
        sha256=_sha256(raw),
    )


def parse_name_status_z(raw: bytes) -> tuple[Change, ...]:
    """Parse strict ``git diff --name-status -z`` output."""
    if not isinstance(raw, bytes):
        raise TypeError("raw diff output must be bytes")
    if not raw:
        return ()
    if not raw.endswith(b"\0"):
        raise PromotionError("NUL-delimited Git diff is not terminated")
    fields = raw[:-1].split(b"\0")
    changes: list[Change] = []
    index = 0
    while index < len(fields):
        status_raw = fields[index]
        index += 1
        try:
            status = status_raw.decode("ascii")
        except UnicodeDecodeError as exc:
            raise PromotionError("Git emitted a non-ASCII status") from exc
        rename = _RENAME_STATUS_RE.fullmatch(status)
        if rename is not None:
            if int(rename.group(2)) > 100:
                raise PromotionError("Git emitted an invalid rename/copy score")
            path_count = 2
        elif _STATUS_RE.fullmatch(status) is not None:
            path_count = 1
        else:
            raise PromotionError(f"Git emitted unsupported status {status!r}")
        if index + path_count > len(fields):
            raise PromotionError("NUL-delimited Git diff is truncated")
        decoded: list[str] = []
        for field in fields[index:index + path_count]:
            try:
                path = field.decode("utf-8")
            except UnicodeDecodeError as exc:
                raise PromotionError("Git emitted a non-UTF-8 path") from exc
            decoded.append(_validate_change_path(path))
        index += path_count
        changes.append(Change(
            status=status,
            old_path=decoded[0],
            new_path=decoded[1] if path_count == 2 else None,
        ))
    return tuple(changes)


def _change_set_sha256(changes: Sequence[Change]) -> str:
    status_counts = Counter(change.status for change in changes)
    endpoint_counts = Counter(
        (
            endpoint.side,
            endpoint.endpoint,
            endpoint.exists,
            endpoint.mode,
            endpoint.object_type,
        )
        for change in changes
        for endpoint in change.tree_endpoints
    )
    payload = {
        "status_counts": sorted(status_counts.items()),
        "endpoint_counts": [
            [*key, count]
            for key, count in sorted(
                endpoint_counts.items(),
                key=lambda item: tuple(
                    "" if value is None else str(value) for value in item[0]
                ),
            )
        ],
    }
    return _sha256(_canonical_json(payload).encode("utf-8"))


def _expected_tree_endpoints(
    change: Change,
) -> tuple[tuple[str, str, bool], ...]:
    status = change.status[0]
    if status == "A":
        return (("base", "old", False), ("head", "old", True))
    if status == "D":
        return (("base", "old", True), ("head", "old", False))
    if status in {"M", "T"}:
        return (("base", "old", True), ("head", "old", True))
    if status == "R":
        return (
            ("base", "old", True),
            ("head", "old", False),
            ("base", "new", False),
            ("head", "new", True),
        )
    if status == "C":
        return (
            ("base", "old", True),
            ("head", "old", True),
            ("base", "new", False),
            ("head", "new", True),
        )
    raise PromotionError("cannot derive tree endpoints for Git status")


def _regular_blob_modes_verified(change: Change) -> bool:
    if change.status.startswith("T"):
        return False
    expected = _expected_tree_endpoints(change)
    if len(change.tree_endpoints) != len(expected):
        return False
    observed: dict[tuple[str, str], TreeEndpoint] = {}
    for endpoint in change.tree_endpoints:
        if not isinstance(endpoint, TreeEndpoint):
            return False
        key = (endpoint.side, endpoint.endpoint)
        if key in observed:
            return False
        observed[key] = endpoint
    for side, role, should_exist in expected:
        endpoint = observed.get((side, role))
        if endpoint is None or endpoint.exists is not should_exist:
            return False
        if should_exist:
            if (
                endpoint.mode not in {"100644", "100755"}
                or endpoint.object_type != "blob"
            ):
                return False
        elif endpoint.mode is not None or endpoint.object_type is not None:
            return False
    return True


def _tree_entries(
    repository: Path,
    commit_sha: str,
    paths: Sequence[str],
    *,
    environment: Mapping[str, str],
) -> dict[str, tuple[str, str]]:
    if not paths:
        return {}
    command = [
        "git",
        "--no-replace-objects",
        "ls-tree",
        "-r",
        "-z",
        "--full-tree",
        commit_sha,
        "--",
        *(f":(literal){path}" for path in paths),
    ]
    try:
        result = subprocess.run(
            command,
            cwd=repository,
            check=False,
            stdout=subprocess.PIPE,
            stderr=subprocess.PIPE,
            env=environment,
        )
    except OSError as exc:
        raise PromotionError("cannot inspect exact Git tree entries") from exc
    if result.returncode != 0:
        raise PromotionError(
            f"Git tree inspection failed with exit status {result.returncode}"
        )
    if result.stdout and not result.stdout.endswith(b"\0"):
        raise PromotionError("Git tree inspection was not NUL terminated")

    requested = set(paths)
    entries: dict[str, tuple[str, str]] = {}
    records = result.stdout[:-1].split(b"\0") if result.stdout else []
    for record in records:
        header, separator, raw_path = record.partition(b"\t")
        fields = header.split(b" ")
        if not separator or len(fields) != 3:
            raise PromotionError("Git tree inspection returned malformed data")
        raw_mode, raw_type, raw_object = fields
        try:
            mode = raw_mode.decode("ascii")
            object_type = raw_type.decode("ascii")
            object_id = raw_object.decode("ascii")
            path = _validate_change_path(raw_path.decode("utf-8"))
        except (UnicodeDecodeError, PromotionError) as exc:
            raise PromotionError("Git tree inspection returned invalid data") from exc
        if (
            re.fullmatch(r"[0-7]{6}", mode) is None
            or re.fullmatch(r"[a-z]+", object_type) is None
            or re.fullmatch(r"(?:[0-9a-f]{40}|[0-9a-f]{64})", object_id) is None
            or path not in requested
            or path in entries
        ):
            raise PromotionError("Git tree inspection returned unexpected data")
        entries[path] = (mode, object_type)
    return entries


def _attach_tree_endpoints(
    changes: Sequence[Change],
    base_entries: Mapping[str, tuple[str, str]],
    head_entries: Mapping[str, tuple[str, str]],
) -> tuple[Change, ...]:
    attached: list[Change] = []
    sides = {"base": base_entries, "head": head_entries}
    for change in changes:
        endpoints: list[TreeEndpoint] = []
        for side, role, _expected_exists in _expected_tree_endpoints(change):
            path = change.old_path if role == "old" else change.new_path
            if path is None:
                raise PromotionError("Git change is missing a rename/copy endpoint")
            entry = sides[side].get(path)
            endpoints.append(TreeEndpoint(
                side=side,
                endpoint=role,
                exists=entry is not None,
                mode=entry[0] if entry is not None else None,
                object_type=entry[1] if entry is not None else None,
            ))
        attached.append(Change(
            status=change.status,
            old_path=change.old_path,
            new_path=change.new_path,
            tree_endpoints=tuple(endpoints),
        ))
    return tuple(attached)


def _verify_exact_commit(
    repository: Path,
    commit_sha: str,
    context: str,
    *,
    environment: Mapping[str, str],
) -> None:
    commit_sha = _validate_sha(commit_sha, context)
    try:
        resolved = subprocess.run(
            [
                "git",
                "--no-replace-objects",
                "rev-parse",
                "--verify",
                f"{commit_sha}^{{commit}}",
            ],
            cwd=repository,
            check=False,
            stdout=subprocess.PIPE,
            stderr=subprocess.PIPE,
            env=environment,
        )
    except OSError as exc:
        raise PromotionError(f"cannot verify Git {context}: {exc}") from exc
    try:
        resolved_sha = resolved.stdout.decode("ascii").strip()
    except UnicodeDecodeError as exc:
        raise PromotionError(f"Git returned an invalid {context}") from exc
    if resolved.returncode != 0 or resolved_sha != commit_sha:
        raise PromotionError(f"Git cannot resolve exact {context} object")


def _verify_pull_request_candidate(
    repository: Path,
    *,
    base_sha: str,
    head_sha: str,
    candidate_sha: str,
    environment: Mapping[str, str],
) -> None:
    """Require the tested PR candidate to be the exact two-parent merge."""
    try:
        result = subprocess.run(
            [
                "git",
                "--no-replace-objects",
                "rev-list",
                "--parents",
                "-n",
                "1",
                candidate_sha,
            ],
            cwd=repository,
            check=False,
            stdout=subprocess.PIPE,
            stderr=subprocess.PIPE,
            env=environment,
        )
    except OSError as exc:
        raise PromotionError("cannot inspect pull-request candidate") from exc
    try:
        fields = result.stdout.decode("ascii").strip().split()
    except UnicodeDecodeError as exc:
        raise PromotionError("pull-request candidate parents are invalid") from exc
    if result.returncode != 0 or fields != [candidate_sha, base_sha, head_sha]:
        raise PromotionError(
            "pull-request candidate is not the exact base/head merge commit"
        )


def git_changes(root: Path | str, base_sha: str, head_sha: str) -> ChangeSet:
    """Read a rename-aware exact-tree diff without invoking diff drivers."""
    base_sha = _validate_sha(base_sha, "base_sha")
    head_sha = _validate_sha(head_sha, "head_sha")
    repository = Path(root).resolve()
    environment = {
        **os.environ,
        "GIT_NO_REPLACE_OBJECTS": "1",
        "GIT_OPTIONAL_LOCKS": "0",
    }
    for label, commit_sha in (("base_sha", base_sha), ("head_sha", head_sha)):
        _verify_exact_commit(
            repository,
            commit_sha,
            label,
            environment=environment,
        )
    command = [
        "git",
        "--no-replace-objects",
        "diff",
        "--name-status",
        "-z",
        "-M",
        "--no-ext-diff",
        "--no-textconv",
        base_sha,
        head_sha,
        "--",
    ]
    try:
        result = subprocess.run(
            command,
            cwd=repository,
            check=False,
            stdout=subprocess.PIPE,
            stderr=subprocess.PIPE,
            env=environment,
        )
    except OSError as exc:
        raise PromotionError(f"cannot execute Git diff: {exc}") from exc
    if result.returncode != 0:
        raise PromotionError(
            f"Git diff failed with exit status {result.returncode}"
        )
    parsed_changes = parse_name_status_z(result.stdout)
    paths = tuple(sorted({
        path
        for change in parsed_changes
        for path in change.paths
    }))
    base_entries = _tree_entries(
        repository, base_sha, paths, environment=environment
    )
    head_entries = _tree_entries(
        repository, head_sha, paths, environment=environment
    )
    changes = _attach_tree_endpoints(
        parsed_changes, base_entries, head_entries
    )
    return ChangeSet(changes, _change_set_sha256(changes))


def _matching_groups(policy: RiskPolicy, path: str) -> frozenset[str]:
    return frozenset(
        name
        for name, group in policy.groups.items()
        if group.selectors.matches(path)
    )


def classify(
    policy: RiskPolicy,
    changes: Sequence[Change],
    *,
    base_sha: str,
    head_sha: str,
    candidate_sha: str,
    trusted_sha: str,
    event_name: str,
    head_ref: str = "",
) -> Decision:
    """Return a deterministic, fail-closed CI requirement decision."""
    base_sha = _validate_sha(base_sha, "base_sha")
    head_sha = _validate_sha(head_sha, "head_sha")
    candidate_sha = _validate_sha(candidate_sha, "candidate_sha")
    trusted_sha = _validate_sha(trusted_sha, "trusted_sha")
    if event_name not in {
        "pull_request", "merge_group", "push", "workflow_dispatch",
    }:
        raise PromotionError(f"unsupported event_name {event_name!r}")
    expected_trusted = (
        base_sha
        if event_name in {"pull_request", "merge_group"}
        else head_sha
    )
    if trusted_sha != expected_trusted:
        raise PromotionError(
            "trusted_sha does not identify the event's trusted evaluator commit"
        )
    if event_name != "pull_request" and candidate_sha != head_sha:
        raise PromotionError(
            "candidate_sha must equal head_sha outside pull-request events"
        )
    if not isinstance(head_ref, str) or "\x00" in head_ref:
        raise PromotionError("head_ref must be text without NUL")

    change_tuple = tuple(changes)
    groups: set[str] = set()
    unknown = False
    unsafe_or_unverified_mode = False
    only_documentation = bool(change_tuple)
    for change in change_tuple:
        if not isinstance(change, Change):
            raise PromotionError("changes must contain Change records")
        if not _regular_blob_modes_verified(change):
            unsafe_or_unverified_mode = True
            only_documentation = False
        for path in change.paths:
            matches = _matching_groups(policy, path)
            if not matches:
                unknown = True
                only_documentation = False
            else:
                groups.update(matches)
                if matches != {DOCUMENTATION_GROUP}:
                    only_documentation = False
    if not change_tuple:
        unknown = True
        only_documentation = False
    if unknown:
        groups.add("unknown")

    forced_reasons: set[str] = set()
    if event_name == "push":
        forced_reasons.add("push_force_heavy")
    elif event_name == "workflow_dispatch":
        forced_reasons.add("manual_force_heavy")
    elif event_name == "merge_group":
        forced_reasons.add("merge_group_force_heavy")
    if event_name == "pull_request" and head_ref.endswith("-candidate"):
        forced_reasons.add("candidate_force_heavy")
    forced_heavy = bool(forced_reasons)

    if only_documentation and not forced_heavy:
        required_jobs = frozenset(ALWAYS_REQUIRED_JOBS)
        pythons = policy.fast_python_versions
        reasons = {"documentation_only"}
    else:
        required_jobs = frozenset(EXECUTION_JOBS)
        pythons = policy.full_python_versions
        reasons = set(forced_reasons)
        if unknown:
            reasons.add("unknown_change")
        elif groups - {DOCUMENTATION_GROUP}:
            reasons.add("non_documentation_change")
        else:
            reasons.add("forced_heavy")
        if unsafe_or_unverified_mode:
            reasons.add("unsafe_or_unverified_mode")

    requirements = {
        job: "required" if job in required_jobs else "not_required"
        for job in EXECUTION_JOBS
    }
    decision = Decision(
        event_name=event_name,
        base_sha=base_sha,
        head_sha=head_sha,
        candidate_sha=candidate_sha,
        trusted_sha=trusted_sha,
        policy_sha256=policy.sha256,
        change_set_sha256=_change_set_sha256(change_tuple),
        changed_count=len(change_tuple),
        groups=tuple(sorted(groups)),
        requirements=requirements,
        pythons=pythons,
        forced_heavy=forced_heavy,
        reason_codes=tuple(sorted(reasons)),
    )
    _require_decision_semantics(decision)
    return decision


def _output_text(decision: Decision) -> str:
    requirements = dict(decision.requirements)
    values: list[tuple[str, str]] = [
        ("classifier_ok", "true"),
        ("decision", _canonical_json(decision.payload())),
        ("requirements", _canonical_json(requirements)),
        ("pythons", _canonical_json(list(decision.pythons))),
    ]
    for job in HEAVY_JOBS:
        values.append((
            f"require_{job.replace('-', '_')}",
            "true" if requirements[job] == "required" else "false",
        ))
    values.extend((
        ("base_sha", decision.base_sha),
        ("head_sha", decision.head_sha),
        ("candidate_sha", decision.candidate_sha),
        ("trusted_sha", decision.trusted_sha),
        ("policy_sha256", decision.policy_sha256),
        ("decision_sha256", decision.decision_sha256),
        ("groups", _canonical_json(list(decision.groups))),
        ("changed_count", str(decision.changed_count)),
    ))
    if any("\n" in value or "\r" in value for _key, value in values):
        raise PromotionError("refusing to write a multiline workflow output")
    return "".join(f"{key}={value}\n" for key, value in values)


def write_outputs(decision: Decision, path: Path | str) -> None:
    """Append one fully validated fixed-key output block for GitHub Actions."""
    output_path = Path(path)
    output_path.parent.mkdir(parents=True, exist_ok=True)
    rendered = _output_text(decision)
    try:
        with output_path.open("a", encoding="utf-8", newline="\n") as stream:
            stream.write(rendered)
    except OSError as exc:
        raise PromotionError(f"cannot write workflow outputs: {exc}") from exc


def _write_json_atomic(path: Path | str, payload: object) -> None:
    target = Path(path)
    target.parent.mkdir(parents=True, exist_ok=True)
    rendered = json.dumps(
        payload,
        ensure_ascii=True,
        sort_keys=True,
        indent=2,
    ) + "\n"
    temporary: Path | None = None
    try:
        with tempfile.NamedTemporaryFile(
            mode="w",
            encoding="utf-8",
            newline="\n",
            dir=target.parent,
            prefix=f".{target.name}.",
            suffix=".tmp",
            delete=False,
        ) as stream:
            temporary = Path(stream.name)
            stream.write(rendered)
            stream.flush()
            os.fsync(stream.fileno())
        os.replace(temporary, target)
    except OSError as exc:
        if temporary is not None:
            try:
                temporary.unlink(missing_ok=True)
            except OSError:
                pass
        raise PromotionError(f"cannot write JSON report: {exc}") from exc


def _decision_semantic_errors(decision: Decision) -> tuple[str, ...]:
    """Return violations that a recomputed digest alone cannot detect."""
    errors: list[str] = []
    allowed_events = {
        "pull_request", "merge_group", "push", "workflow_dispatch",
    }
    if decision.event_name not in allowed_events:
        errors.append("event_name is unsupported")

    for field in ("base_sha", "head_sha", "candidate_sha", "trusted_sha"):
        value = getattr(decision, field)
        if not isinstance(value, str) or _SHA_RE.fullmatch(value) is None:
            errors.append(f"{field} is not an exact lowercase commit SHA")

    if not errors and decision.event_name in allowed_events:
        expected_trusted = (
            decision.base_sha
            if decision.event_name in {"pull_request", "merge_group"}
            else decision.head_sha
        )
        if decision.trusted_sha != expected_trusted:
            errors.append("trusted_sha is inconsistent with event_name")
        if (
            decision.event_name != "pull_request"
            and decision.candidate_sha != decision.head_sha
        ):
            errors.append("candidate_sha is inconsistent with event_name")

    groups = decision.groups
    if (
        not isinstance(groups, tuple)
        or not groups
        or any(not isinstance(group, str) for group in groups)
        or groups != tuple(sorted(set(groups)))
        or any(group not in ALLOWED_GROUPS for group in groups)
    ):
        errors.append("groups are not a canonical allowed set")

    reasons = decision.reason_codes
    if (
        not isinstance(reasons, tuple)
        or not reasons
        or any(not isinstance(reason, str) for reason in reasons)
        or reasons != tuple(sorted(set(reasons)))
        or any(reason not in ALLOWED_REASON_CODES for reason in reasons)
    ):
        errors.append("reason_codes are not a canonical allowed set")

    if (
        not isinstance(decision.changed_count, int)
        or isinstance(decision.changed_count, bool)
        or decision.changed_count < 0
    ):
        errors.append("changed_count is invalid")
    elif decision.changed_count == 0 and groups != ("unknown",):
        errors.append("an empty change set must use only the unknown group")

    requirements = decision.requirements
    if (
        not isinstance(requirements, Mapping)
        or set(requirements) != set(EXECUTION_JOBS)
        or any(
            requirements.get(job) not in {"required", "not_required"}
            for job in EXECUTION_JOBS
        )
    ):
        errors.append("requirements do not cover the exact execution jobs")

    if not isinstance(decision.pythons, tuple):
        errors.append("pythons is not canonical")
    if not isinstance(decision.forced_heavy, bool):
        errors.append("forced_heavy is not Boolean")

    if errors:
        return tuple(errors)

    event_forced_reasons: set[str] = set()
    if decision.event_name == "push":
        event_forced_reasons.add("push_force_heavy")
    elif decision.event_name == "workflow_dispatch":
        event_forced_reasons.add("manual_force_heavy")
    elif decision.event_name == "merge_group":
        event_forced_reasons.add("merge_group_force_heavy")
    elif "candidate_force_heavy" in reasons:
        event_forced_reasons.add("candidate_force_heavy")

    all_force_reasons = {
        "candidate_force_heavy",
        "manual_force_heavy",
        "merge_group_force_heavy",
        "push_force_heavy",
    }
    observed_force_reasons = set(reasons) & all_force_reasons
    if observed_force_reasons != event_forced_reasons:
        errors.append("force reason is inconsistent with event_name")
    if decision.forced_heavy is not bool(event_forced_reasons):
        errors.append("forced_heavy is inconsistent with force reasons")

    unsafe_modes = "unsafe_or_unverified_mode" in reasons
    should_fast = (
        groups == (DOCUMENTATION_GROUP,)
        and not event_forced_reasons
        and not unsafe_modes
    )
    expected_requirements = {
        job: (
            "required"
            if not should_fast or job in ALWAYS_REQUIRED_JOBS
            else "not_required"
        )
        for job in EXECUTION_JOBS
    }
    if dict(requirements) != expected_requirements:
        errors.append("requirements are inconsistent with risk groups")
    expected_pythons = (
        FAST_PYTHON_VERSIONS if should_fast else FULL_PYTHON_VERSIONS
    )
    if decision.pythons != expected_pythons:
        errors.append("pythons are inconsistent with risk groups")

    if should_fast:
        expected_reasons = {"documentation_only"}
        if decision.changed_count == 0:
            errors.append("documentation-only fast decisions cannot be empty")
    else:
        expected_reasons = set(event_forced_reasons)
        if "unknown" in groups:
            expected_reasons.add("unknown_change")
        elif set(groups) - {DOCUMENTATION_GROUP}:
            expected_reasons.add("non_documentation_change")
        else:
            expected_reasons.add("forced_heavy")
        if unsafe_modes:
            expected_reasons.add("unsafe_or_unverified_mode")
        if decision.changed_count == 0 and unsafe_modes:
            errors.append("an empty change set cannot carry mode evidence")
    if set(reasons) != expected_reasons:
        errors.append("reason_codes are inconsistent with risk groups")
    return tuple(errors)


def _require_decision_semantics(decision: Decision) -> None:
    errors = _decision_semantic_errors(decision)
    if errors:
        raise PromotionError(
            "decision semantic invariants failed: " + "; ".join(errors)
        )


def _decision_from_payload(value: object) -> Decision:
    payload = _strict_keys(value, _DECISION_KEYS, "decision")
    if (
        not isinstance(payload["schema_version"], int)
        or isinstance(payload["schema_version"], bool)
        or payload["schema_version"] != DECISION_SCHEMA_VERSION
    ):
        raise PromotionError("decision schema_version is invalid")
    if payload["classifier_ok"] is not True:
        raise PromotionError("decision classifier_ok must be true")
    for field in ("base_sha", "head_sha", "candidate_sha", "trusted_sha"):
        if not isinstance(payload[field], str):
            raise PromotionError(f"decision {field} must be text")
        _validate_sha(payload[field], f"decision {field}")
    for field in ("policy_sha256", "change_set_sha256", "decision_sha256"):
        if not isinstance(payload[field], str) or _SHA256_RE.fullmatch(
            payload[field]
        ) is None:
            raise PromotionError(f"decision {field} must be lowercase SHA-256")
    groups = _text_list(payload["groups"], "decision groups")
    if groups != tuple(sorted(groups)):
        raise PromotionError("decision groups must be sorted")
    reasons = _text_list(payload["reason_codes"], "decision reason_codes")
    if reasons != tuple(sorted(reasons)):
        raise PromotionError("decision reason_codes must be sorted")
    pythons = _text_list(payload["pythons"], "decision pythons")
    requirements_value = payload["requirements"]
    if not isinstance(requirements_value, dict):
        raise PromotionError("decision requirements must be an object")
    if set(requirements_value) != set(EXECUTION_JOBS) or any(
        not isinstance(job, str)
        or not isinstance(result, str)
        or result not in {"required", "not_required"}
        for job, result in requirements_value.items()
    ):
        raise PromotionError("decision requirements are invalid")
    changed_count = payload["changed_count"]
    if (
        not isinstance(changed_count, int)
        or isinstance(changed_count, bool)
        or changed_count < 0
    ):
        raise PromotionError("decision changed_count must be a non-negative integer")
    forced_heavy = payload["forced_heavy"]
    if not isinstance(forced_heavy, bool):
        raise PromotionError("decision forced_heavy must be Boolean")
    event_name = payload["event_name"]
    if not isinstance(event_name, str):
        raise PromotionError("decision event_name must be text")
    decision = Decision(
        event_name=event_name,
        base_sha=payload["base_sha"],
        head_sha=payload["head_sha"],
        candidate_sha=payload["candidate_sha"],
        trusted_sha=payload["trusted_sha"],
        policy_sha256=payload["policy_sha256"],
        change_set_sha256=payload["change_set_sha256"],
        changed_count=changed_count,
        groups=groups,
        requirements={job: requirements_value[job] for job in EXECUTION_JOBS},
        pythons=pythons,
        forced_heavy=forced_heavy,
        reason_codes=reasons,
    )
    if decision.decision_sha256 != payload["decision_sha256"]:
        raise PromotionError("decision_sha256 does not match decision content")
    _require_decision_semantics(decision)
    return decision


def verify_promotion(
    decision: Decision,
    results: Mapping[str, str],
) -> PromotionVerification:
    """Apply the strict required/success and unrequired/skipped truth table."""
    if not isinstance(results, Mapping):
        raise TypeError("results must be a mapping")
    semantic_errors = _decision_semantic_errors(decision)
    if semantic_errors:
        return PromotionVerification(False, tuple(
            f"decision: {error}" for error in semantic_errors
        ))
    if set(results) != set(EXECUTION_JOBS):
        missing = ", ".join(sorted(set(EXECUTION_JOBS).difference(results)))
        extra = ", ".join(sorted(set(results).difference(EXECUTION_JOBS)))
        return PromotionVerification(False, (
            f"result jobs differ (missing: {missing or 'none'}; "
            f"extra: {extra or 'none'})",
        ))
    errors: list[str] = []
    for job in EXECUTION_JOBS:
        result = results[job]
        if result not in {"success", "failure", "cancelled", "skipped"}:
            errors.append(f"{job}: unexpected result {result!r}")
            continue
        required = decision.requirements[job] == "required"
        expected = "success" if required else "skipped"
        if result != expected:
            errors.append(
                f"{job}: expected {expected}, observed {result}"
            )
    return PromotionVerification(not errors, tuple(errors))


def _read_json(path: Path | str, context: str) -> Any:
    try:
        return _loads_strict(Path(path).read_text(encoding="utf-8"), context)
    except (OSError, UnicodeDecodeError, PromotionError) as exc:
        raise PromotionError(f"cannot load {context}: {exc}") from exc


def _classify_command(args: argparse.Namespace) -> int:
    policy = load_policy(args.policy)
    change_set = git_changes(args.root, args.base_sha, args.head_sha)
    repository = Path(args.root).resolve()
    environment = {
        **os.environ,
        "GIT_NO_REPLACE_OBJECTS": "1",
        "GIT_OPTIONAL_LOCKS": "0",
    }
    _verify_exact_commit(
        repository,
        args.candidate_sha,
        "candidate_sha",
        environment=environment,
    )
    if args.event_name == "pull_request":
        _verify_pull_request_candidate(
            repository,
            base_sha=args.base_sha,
            head_sha=args.head_sha,
            candidate_sha=args.candidate_sha,
            environment=environment,
        )
    decision = classify(
        policy,
        change_set.changes,
        base_sha=args.base_sha,
        head_sha=args.head_sha,
        candidate_sha=args.candidate_sha,
        trusted_sha=args.trusted_sha,
        event_name=args.event_name,
        head_ref=args.head_ref,
    )
    if decision.change_set_sha256 != change_set.sha256:
        raise PromotionError("internal change-set identity mismatch")
    _write_json_atomic(args.decision_out, decision.payload())
    write_outputs(decision, args.github_output)
    return 0


def _verify_command(args: argparse.Namespace) -> int:
    decision = _decision_from_payload(_read_json(args.decision, "decision"))
    policy = load_policy(args.policy)
    change_set = git_changes(args.root, decision.base_sha, decision.head_sha)
    repository = Path(args.root).resolve()
    environment = {
        **os.environ,
        "GIT_NO_REPLACE_OBJECTS": "1",
        "GIT_OPTIONAL_LOCKS": "0",
    }
    _verify_exact_commit(
        repository,
        decision.candidate_sha,
        "candidate_sha",
        environment=environment,
    )
    if decision.event_name == "pull_request":
        _verify_pull_request_candidate(
            repository,
            base_sha=decision.base_sha,
            head_sha=decision.head_sha,
            candidate_sha=decision.candidate_sha,
            environment=environment,
        )
    recomputed = classify(
        policy,
        change_set.changes,
        base_sha=decision.base_sha,
        head_sha=decision.head_sha,
        candidate_sha=decision.candidate_sha,
        trusted_sha=decision.trusted_sha,
        event_name=decision.event_name,
        head_ref=args.head_ref,
    )
    if recomputed.payload() != decision.payload():
        raise PromotionError(
            "decision does not match the trusted policy and exact Git endpoints"
        )
    results = _read_json(args.results, "job results")
    if not isinstance(results, dict) or any(
        not isinstance(key, str) or not isinstance(value, str)
        for key, value in results.items()
    ):
        raise PromotionError("job results must be an object of text values")
    verification = verify_promotion(decision, results)
    if args.report is not None:
        _write_json_atomic(args.report, {
            "schema_version": 1,
            "decision_sha256": decision.decision_sha256,
            "ok": verification.ok,
            "errors": list(verification.errors),
        })
    if not verification.ok:
        for error in verification.errors:
            print(error, file=sys.stderr)
        return 1
    print("CI promotion requirements are satisfied.")
    return 0


def _parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        description="Classify CI risk and verify exact promotion results"
    )
    commands = parser.add_subparsers(dest="command", required=True)

    classify_parser = commands.add_parser("classify")
    classify_parser.add_argument("--policy", type=Path, required=True)
    classify_parser.add_argument("--root", type=Path, required=True)
    classify_parser.add_argument("--base-sha", required=True)
    classify_parser.add_argument("--head-sha", required=True)
    classify_parser.add_argument("--candidate-sha", required=True)
    classify_parser.add_argument("--trusted-sha", required=True)
    classify_parser.add_argument(
        "--event-name",
        choices=("pull_request", "merge_group", "push", "workflow_dispatch"),
        required=True,
    )
    classify_parser.add_argument("--head-ref", default="")
    classify_parser.add_argument("--github-output", type=Path, required=True)
    classify_parser.add_argument("--decision-out", type=Path, required=True)
    classify_parser.set_defaults(handler=_classify_command)

    verify_parser = commands.add_parser("verify")
    verify_parser.add_argument("--decision", type=Path, required=True)
    verify_parser.add_argument("--results", type=Path, required=True)
    verify_parser.add_argument("--policy", type=Path, required=True)
    verify_parser.add_argument("--root", type=Path, required=True)
    verify_parser.add_argument("--head-ref", default="")
    verify_parser.add_argument("--report", type=Path)
    verify_parser.set_defaults(handler=_verify_command)
    return parser


def main(argv: Sequence[str] | None = None) -> int:
    args = _parser().parse_args(argv)
    try:
        return int(args.handler(args))
    except PromotionError as exc:
        print(f"CI promotion error: {exc}", file=sys.stderr)
        return 2


if __name__ == "__main__":
    raise SystemExit(main())
