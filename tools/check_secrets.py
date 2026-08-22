"""Scan repository content for committed credentials, redacted by design.

The scanner never prints, stores, or uploads matched bytes: every finding
carries only a rule id, a location, and a SHA-256 digest of the exact
match. Suppression happens exclusively through narrow, expiring,
reviewer-bound records in the committed policy; there is no baseline file.
Tracked mode scans every Git-tracked file; history mode scans every blob
reachable from any ref and refuses shallow repositories.
"""

from __future__ import annotations

import argparse
import hashlib
import json
import re
import subprocess
from datetime import date, datetime, timezone
from pathlib import Path
from typing import Any, Callable, Iterable

_HEX_VALUE = re.compile(r"^(sha(?:1|256|512):)?[0-9a-fA-F]{32,128}$")
_REPEATED_CHAR = re.compile(r"^(.)\1{7,}$")
_LOWERCASE_WORDS = re.compile(r"^[a-z]+(?:[-_.][a-z]+){0,5}$")
_PLACEHOLDER_CREDENTIAL = re.compile(
    r"(?i)^(pass|password|secret|user|token|example|placeholder|redacted"
    r"|\{[^}]*\}|[A-Z0-9_]*canary[A-Z0-9_]*)$"
)


def _plausible_secret(value: str) -> bool:
    """Reject values that are structurally incapable of being a secret."""
    if len(value) < 16:
        return False
    if _HEX_VALUE.fullmatch(value):
        return False
    if _REPEATED_CHAR.fullmatch(value):
        return False
    if _LOWERCASE_WORDS.fullmatch(value):
        return False
    if "CANARY" in value.upper():
        return False
    return True


def _url_credential_gate(match: re.Match[str]) -> bool:
    username = match.group("username")
    password = match.group("password")
    if _PLACEHOLDER_CREDENTIAL.fullmatch(username):
        return False
    if _PLACEHOLDER_CREDENTIAL.fullmatch(password):
        return False
    return _plausible_secret(password)


def _assignment_gate(match: re.Match[str]) -> bool:
    return _plausible_secret(match.group("value"))


_RULES: tuple[tuple[str, re.Pattern[str], Callable[..., bool] | None], ...] = (
    (
        "github-token",
        re.compile(r"\b(?:ghp|gho|ghu|ghs|ghr)_[A-Za-z0-9]{36,255}\b"),
        None,
    ),
    (
        "github-fine-grained-token",
        re.compile(r"\bgithub_pat_[A-Za-z0-9_]{22,255}\b"),
        None,
    ),
    (
        "slack-token",
        re.compile(r"\bxox[baprs]-[A-Za-z0-9-]{10,}\b"),
        None,
    ),
    (
        "aws-access-key-id",
        re.compile(r"\b(?:AKIA|ASIA|ABIA|ACCA)[0-9A-Z]{16}\b"),
        None,
    ),
    (
        "aws-secret-access-key",
        re.compile(
            r"(?i)\b(?:aws_)?secret_access_key\b\s*[:=]\s*"
            r"['\"](?P<value>[A-Za-z0-9/+=]{40})['\"]"
        ),
        _assignment_gate,
    ),
    (
        "google-api-key",
        re.compile(r"\bAIza[0-9A-Za-z_-]{35}\b"),
        None,
    ),
    (
        "openai-style-key",
        re.compile(
            r"\bsk-(?:proj-|ant-api03-)?[A-Za-z0-9_-]{32,}\b"
        ),
        None,
    ),
    (
        "stripe-live-key",
        re.compile(r"\b(?:sk|rk)_live_[A-Za-z0-9]{16,}\b"),
        None,
    ),
    (
        "npm-token",
        re.compile(r"\bnpm_[A-Za-z0-9]{36}\b"),
        None,
    ),
    (
        "gitlab-token",
        re.compile(r"\bglpat-[A-Za-z0-9_-]{20,}\b"),
        None,
    ),
    (
        "sendgrid-key",
        re.compile(r"\bSG\.[A-Za-z0-9_-]{22}\.[A-Za-z0-9_-]{43}\b"),
        None,
    ),
    (
        "private-key-block",
        re.compile(
            r"-----BEGIN (?:RSA |EC |DSA |OPENSSH |PGP |ENCRYPTED )?"
            r"PRIVATE KEY(?: BLOCK)?-----"
        ),
        None,
    ),
    (
        "url-embedded-credential",
        re.compile(
            r"://(?P<username>[^/\s:@'\"]{1,64}):"
            r"(?P<password>[^/\s@'\"]{1,128})@"
        ),
        _url_credential_gate,
    ),
    (
        "keyword-assignment",
        re.compile(
            r"(?i)\b(?:api_key|api_token|auth_token|access_token|password"
            r"|client_secret)\b\s*[:=]\s*['\"](?P<value>[^'\"\n]{8,256})['\"]"
        ),
        _assignment_gate,
    ),
)

_RULE_IDS = frozenset(rule_id for rule_id, _pattern, _gate in _RULES)


def _reject_duplicate_pairs(pairs: list[tuple[str, Any]]) -> dict[str, Any]:
    value: dict[str, Any] = {}
    for key, item in pairs:
        if key in value:
            raise ValueError(f"duplicate JSON key {key!r}")
        value[key] = item
    return value


def _load_policy(path: Path) -> dict[tuple[str, str, str], date]:
    try:
        text = path.read_text(encoding="utf-8")
    except OSError as exc:
        raise ValueError(f"cannot read secret scan policy: {exc}") from exc
    try:
        policy = json.loads(
            text,
            object_pairs_hook=_reject_duplicate_pairs,
            parse_constant=lambda token: (_ for _ in ()).throw(
                ValueError(f"non-standard JSON number {token}")
            ),
        )
    except ValueError as exc:
        raise ValueError(f"secret scan policy is not strict JSON: {exc}") from exc
    if not isinstance(policy, dict) or policy.get("schema_version") != 1:
        raise ValueError("secret scan policy must use schema_version 1")
    if set(policy) != {"schema_version", "allowlist"}:
        raise ValueError("secret scan policy has unexpected fields")
    raw = policy.get("allowlist")
    if not isinstance(raw, list):
        raise ValueError("secret scan policy allowlist must be a list")
    records: dict[tuple[str, str, str], date] = {}
    for index, item in enumerate(raw):
        context = f"allowlist record {index}"
        if not isinstance(item, dict) or set(item) != {
            "rule_id",
            "path",
            "match_sha256",
            "expires",
            "reason",
            "references",
        }:
            raise ValueError(f"{context} has unexpected fields")
        rule_id = item.get("rule_id")
        if rule_id not in _RULE_IDS:
            raise ValueError(f"{context} names an unknown rule {rule_id!r}")
        path_value = item.get("path")
        if not isinstance(path_value, str) or not path_value:
            raise ValueError(f"{context} must carry a non-empty path")
        digest = item.get("match_sha256")
        if not isinstance(digest, str) or not re.fullmatch(
            r"[0-9a-f]{64}", digest
        ):
            raise ValueError(f"{context} must carry an exact match sha256")
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
        expires_text = item.get("expires")
        if not isinstance(expires_text, str):
            raise ValueError(f"{context} must carry an expires date")
        try:
            expires = date.fromisoformat(expires_text)
        except ValueError as exc:
            raise ValueError(f"{context} has an invalid expires date") from exc
        key = (rule_id, path_value, digest)
        if key in records:
            raise ValueError(f"{context} duplicates an earlier record")
        records[key] = expires
    return records


def _git_lines(root: Path, arguments: tuple[str, ...]) -> list[str]:
    completed = subprocess.run(
        ("git", "-C", str(root), *arguments),
        capture_output=True,
        check=False,
    )
    if completed.returncode != 0:
        raise ValueError(
            "git " + " ".join(arguments[:2]) + " failed with status "
            + str(completed.returncode)
        )
    return completed.stdout.decode("utf-8", errors="replace").split("\0")


def scan_text(text: str) -> list[tuple[str, int, str]]:
    """Return (rule_id, line_number, match_sha256) rows for one document."""
    findings: list[tuple[str, int, str]] = []
    for rule_id, pattern, gate in _RULES:
        for match in pattern.finditer(text):
            if gate is not None and not gate(match):
                continue
            digest = hashlib.sha256(
                match.group(0).encode("utf-8", errors="replace")
            ).hexdigest()
            line_number = text.count("\n", 0, match.start()) + 1
            findings.append((rule_id, line_number, digest))
    return findings


def _tracked_documents(root: Path) -> Iterable[tuple[str, str]]:
    for entry in _git_lines(root, ("ls-files", "-z")):
        if not entry:
            continue
        payload = (root / entry).read_bytes()
        yield entry, payload.decode("utf-8", errors="replace")


def _history_documents(root: Path) -> Iterable[tuple[str, str]]:
    shallow = subprocess.run(
        ("git", "-C", str(root), "rev-parse", "--is-shallow-repository"),
        capture_output=True,
        check=False,
    )
    if shallow.stdout.decode("ascii", errors="replace").strip() != "false":
        raise ValueError(
            "history scan requires a full clone; repository is shallow "
            "or unreadable"
        )
    listing = subprocess.run(
        ("git", "-C", str(root), "rev-list", "--objects", "--all"),
        capture_output=True,
        check=False,
    )
    if listing.returncode != 0:
        raise ValueError("git rev-list --objects --all failed")
    seen: set[str] = set()
    for line in listing.stdout.decode("utf-8", errors="replace").splitlines():
        if not line:
            continue
        object_id, _, object_path = line.partition(" ")
        if not object_path or object_id in seen:
            continue
        seen.add(object_id)
        kind = subprocess.run(
            ("git", "-C", str(root), "cat-file", "-t", object_id),
            capture_output=True,
            check=False,
        )
        if kind.stdout.decode("ascii", errors="replace").strip() != "blob":
            continue
        blob = subprocess.run(
            ("git", "-C", str(root), "cat-file", "blob", object_id),
            capture_output=True,
            check=False,
        )
        if blob.returncode != 0:
            raise ValueError(f"cannot read blob {object_id}")
        yield (
            f"{object_id[:12]}:{object_path}",
            blob.stdout.decode("utf-8", errors="replace"),
        )


def evaluate(
    documents: Iterable[tuple[str, str]],
    allowlist: dict[tuple[str, str, str], date],
    *,
    today: date,
) -> tuple[list[str], list[dict[str, Any]], int]:
    """Return (errors, finding rows, scanned document count)."""
    errors: list[str] = []
    findings: list[dict[str, Any]] = []
    used: set[tuple[str, str, str]] = set()
    scanned = 0
    for location, text in documents:
        scanned += 1
        for rule_id, line_number, digest in scan_text(text):
            key = (rule_id, location, digest)
            expires = allowlist.get(key)
            accepted_through: str | None = None
            if expires is None:
                errors.append(
                    f"{location}:{line_number}: {rule_id} match "
                    f"{digest[:16]} is not accepted by policy"
                )
            else:
                used.add(key)
                if expires < today:
                    errors.append(
                        f"{location}:{line_number}: {rule_id} allowlist "
                        f"record expired on {expires}"
                    )
                else:
                    accepted_through = expires.isoformat()
                    print(
                        f"{location}:{line_number}: {rule_id} accepted "
                        f"through {expires}"
                    )
            findings.append(
                {
                    "accepted_through": accepted_through,
                    "line": line_number,
                    "location": location,
                    "match_sha256": digest,
                    "rule_id": rule_id,
                }
            )
    for key in sorted(set(allowlist) - used):
        errors.append(
            "allowlist record "
            f"({key[0]}, {key[1]}, {key[2][:16]}) matched nothing"
        )
    return errors, findings, scanned


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--policy", type=Path, required=True)
    parser.add_argument(
        "--mode", choices=("tracked", "history"), default="tracked"
    )
    parser.add_argument("--root", type=Path, default=Path("."))
    parser.add_argument(
        "--scan-file",
        type=Path,
        action="append",
        default=None,
        help="scan exactly these files instead of the repository "
        "(deployed-gate live-fire only)",
    )
    parser.add_argument("--output-envelope", type=Path, default=None)
    args = parser.parse_args(argv)
    try:
        allowlist = _load_policy(args.policy)
        if args.scan_file is not None:
            documents: Iterable[tuple[str, str]] = (
                (str(path), path.read_bytes().decode("utf-8", "replace"))
                for path in args.scan_file
            )
        elif args.mode == "tracked":
            documents = _tracked_documents(args.root)
        else:
            documents = _history_documents(args.root)
        errors, findings, scanned = evaluate(
            documents, allowlist, today=datetime.now(timezone.utc).date()
        )
    except (OSError, ValueError) as exc:
        print(f"error: {exc}")
        return 1
    if args.output_envelope is not None:
        envelope = {
            "findings": findings,
            "mode": args.mode if args.scan_file is None else "scan-file",
            "rule_ids": sorted(_RULE_IDS),
            "scan_time": datetime.now(timezone.utc).isoformat(),
            "scanned_documents": scanned,
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
        f"Secret scan passed: {scanned} documents, "
        f"{len(findings)} accepted finding(s), 0 violations."
    )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
