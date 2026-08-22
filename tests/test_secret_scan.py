import hashlib
import json
import subprocess
import sys
from datetime import date
from pathlib import Path

import pytest

from tools import check_secrets


_TODAY = date(2026, 8, 22)


def _canary(prefix: str, body_char: str = "Q", length: int = 40) -> str:
    """Build a format-valid synthetic token at runtime.

    The assembled value never appears literally in this source file, so the
    repository itself keeps scanning clean.
    """
    body = (body_char + "7") * (length // 2)
    return prefix + body[:length]


_POSITIVE_CASES = (
    ("github-token", lambda: _canary("ghp_", length=36)),
    ("github-fine-grained-token", lambda: _canary("github_pat_", length=24)),
    ("slack-token", lambda: "xoxb-" + "12345678901-Ab9" * 2),
    ("aws-access-key-id", lambda: "AKIA" + "J7Q2" * 4),
    ("google-api-key", lambda: "AIza" + "Qw8-" * 8 + "Qw8"),
    ("openai-style-key", lambda: _canary("sk-", length=40)),
    ("openai-style-key", lambda: _canary("sk-ant-api03-", "R", 84)),
    ("stripe-live-key", lambda: _canary("sk_live_", length=24)),
    ("npm-token", lambda: _canary("npm_", length=36)),
    ("gitlab-token", lambda: _canary("glpat-", length=22)),
    (
        "sendgrid-key",
        lambda: "SG." + "a1B2" * 5 + "cd" + "." + "e3F4" * 10 + "gh9",
    ),
    (
        "private-key-block",
        lambda: "-----BEGIN " + "OPENSSH PRIVATE KEY-----",
    ),
    (
        "url-embedded-credential",
        lambda: "https://deploy:" + _canary("Zx9", length=20) + "@host.example/",
    ),
    (
        "keyword-assignment",
        lambda: 'api_key = "' + _canary("Vt5", length=24) + '"',
    ),
    (
        "aws-secret-access-key",
        lambda: 'aws_secret_access_key = "' + ("wJalrXUtnFEMI/K7MDENG" * 2)[:40] + '"',
    ),
)


@pytest.mark.parametrize(
    ("rule_id", "build"),
    _POSITIVE_CASES,
    ids=[f"{rule}-{index}" for index, (rule, _b) in enumerate(_POSITIVE_CASES)],
)
def test_each_rule_catches_its_positive_canary(rule_id, build):
    text = "prefix text\n" + build() + "\nsuffix text\n"

    findings = check_secrets.scan_text(text)

    assert any(found_rule == rule_id for found_rule, _line, _digest in findings)


_NEGATIVE_CASES = (
    "sha256:" + "ab12" * 16,
    "ab12" * 16,
    "--hash=sha256:" + "cd34" * 16,
    'api_key = "key"',
    'api_key = "generic-key"',
    'api_key = "explicit-local-key"',
    'api_key = "sk-deepseek"',
    'token = "' + "r" * 48 + '"',
    'password = "URL_SECRET_CANARY_7fa2"',
    "https://user:pass@gateway.example/v1",
    "https://user:secret@api.deepseek.com/v1",
    "https://user:URL_SECRET_CANARY_7fa2@example.com/",
    "uses: actions/checkout@3d3c42e5aac5ba805825da76410c181273ba90b1",
    "xox is a fragment",
    "-----BEGIN PUBLIC KEY-----",
    "sk-short",
)


@pytest.mark.parametrize("text", _NEGATIVE_CASES)
def test_negative_fixtures_do_not_alert(text):
    assert check_secrets.scan_text("start\n" + text + "\nend\n") == []


def test_finding_carries_digest_and_line_never_bytes(capsys):
    secret = _canary("ghp_", length=36)
    findings = check_secrets.scan_text("first line\n" + secret + "\n")

    assert len(findings) == 1
    rule_id, line, digest = findings[0]
    assert rule_id == "github-token"
    assert line == 2
    assert digest == hashlib.sha256(secret.encode()).hexdigest()
    assert secret not in json.dumps(findings)


def _policy(records=()):
    return {"schema_version": 1, "allowlist": list(records)}


def _record(**overrides):
    item = {
        "rule_id": "github-token",
        "path": "docs/example.md",
        "match_sha256": "ab" * 32,
        "expires": "2026-12-31",
        "reason": "synthetic fixture acceptance",
        "references": ["https://example.com/review"],
    }
    item.update(overrides)
    return item


def _write_policy(tmp_path, policy) -> Path:
    path = tmp_path / "policy.json"
    path.write_text(json.dumps(policy), encoding="utf-8")
    return path


def test_unaccepted_finding_fails_and_output_is_redacted(tmp_path, capsys):
    secret = _canary("ghp_", length=36)
    target = tmp_path / "leaky.txt"
    target.write_text(secret + "\n", encoding="utf-8")
    policy_path = _write_policy(tmp_path, _policy())
    envelope_path = tmp_path / "envelope.json"

    status = check_secrets.main([
        "--policy", str(policy_path),
        "--scan-file", str(target),
        "--output-envelope", str(envelope_path),
    ])

    out = capsys.readouterr().out
    assert status == 1
    assert "github-token match" in out
    assert secret not in out
    envelope_text = envelope_path.read_text(encoding="utf-8")
    assert secret not in envelope_text
    envelope = json.loads(envelope_text)
    assert envelope["violations"] == 1
    assert envelope["findings"][0]["match_sha256"] == hashlib.sha256(
        secret.encode()
    ).hexdigest()


def test_current_allowlist_record_accepts_exact_match(tmp_path, capsys):
    secret = _canary("ghp_", length=36)
    target = tmp_path / "accepted.txt"
    target.write_text(secret + "\n", encoding="utf-8")
    digest = hashlib.sha256(secret.encode()).hexdigest()
    policy_path = _write_policy(
        tmp_path,
        _policy([_record(path=str(target), match_sha256=digest)]),
    )

    status = check_secrets.main([
        "--policy", str(policy_path), "--scan-file", str(target),
    ])

    assert status == 0
    assert "accepted through 2026-12-31" in capsys.readouterr().out


def test_expired_allowlist_record_fails(tmp_path, capsys):
    secret = _canary("ghp_", length=36)
    target = tmp_path / "expired.txt"
    target.write_text(secret + "\n", encoding="utf-8")
    digest = hashlib.sha256(secret.encode()).hexdigest()
    policy_path = _write_policy(
        tmp_path,
        _policy([
            _record(
                path=str(target), match_sha256=digest, expires="2026-08-01"
            )
        ]),
    )

    status = check_secrets.main([
        "--policy", str(policy_path), "--scan-file", str(target),
    ])

    assert status == 1
    assert "expired on 2026-08-01" in capsys.readouterr().out


def test_unused_allowlist_record_fails(tmp_path, capsys):
    target = tmp_path / "clean.txt"
    target.write_text("nothing here\n", encoding="utf-8")
    policy_path = _write_policy(tmp_path, _policy([_record()]))

    status = check_secrets.main([
        "--policy", str(policy_path), "--scan-file", str(target),
    ])

    assert status == 1
    assert "matched nothing" in capsys.readouterr().out


@pytest.mark.parametrize("mutate", (
    lambda policy: policy.update(schema_version=2),
    lambda policy: policy.update(rogue=True),
    lambda policy: policy["allowlist"].append(_record(rule_id="unknown")),
    lambda policy: policy["allowlist"].append(_record(match_sha256="short")),
    lambda policy: policy["allowlist"].append(_record(expires="not-a-date")),
    lambda policy: policy["allowlist"].append(
        _record(references=["http://insecure.example"])
    ),
    lambda policy: policy["allowlist"].extend([_record(), _record()]),
))
def test_malformed_policy_fails_closed(tmp_path, mutate, capsys):
    policy = _policy()
    mutate(policy)
    target = tmp_path / "clean.txt"
    target.write_text("nothing\n", encoding="utf-8")
    policy_path = _write_policy(tmp_path, policy)

    status = check_secrets.main([
        "--policy", str(policy_path), "--scan-file", str(target),
    ])

    assert status == 1
    assert "error:" in capsys.readouterr().out


def test_duplicate_policy_json_keys_fail_closed(tmp_path, capsys):
    policy_path = tmp_path / "policy.json"
    policy_path.write_text(
        '{"schema_version": 1, "schema_version": 1, "allowlist": []}',
        encoding="utf-8",
    )
    target = tmp_path / "clean.txt"
    target.write_text("nothing\n", encoding="utf-8")

    status = check_secrets.main([
        "--policy", str(policy_path), "--scan-file", str(target),
    ])

    assert status == 1
    assert "duplicate JSON key" in capsys.readouterr().out


def test_repository_tracked_tree_scans_clean_with_committed_policy():
    status = check_secrets.main([
        "--policy", "secret-scan-policy.json", "--mode", "tracked",
    ])

    assert status == 0


def _git(cwd: Path, *arguments: str) -> None:
    subprocess.run(
        ("git", *arguments),
        cwd=cwd,
        check=True,
        capture_output=True,
        env={
            "GIT_AUTHOR_NAME": "fixture",
            "GIT_AUTHOR_EMAIL": "fixture@example.com",
            "GIT_COMMITTER_NAME": "fixture",
            "GIT_COMMITTER_EMAIL": "fixture@example.com",
            "GIT_CONFIG_GLOBAL": "/dev/null" if sys.platform != "win32" else "NUL",
            "GIT_CONFIG_SYSTEM": "/dev/null" if sys.platform != "win32" else "NUL",
            "PATH": __import__("os").environ["PATH"],
        },
    )


def test_history_mode_finds_deleted_secret(tmp_path, capsys):
    repo = tmp_path / "repo"
    repo.mkdir()
    _git(repo, "init", "--quiet")
    secret = _canary("ghp_", length=36)
    leaky = repo / "config.txt"
    leaky.write_text("token file\n" + secret + "\n", encoding="utf-8")
    _git(repo, "add", "config.txt")
    _git(repo, "commit", "--quiet", "-m", "add config")
    leaky.write_text("token file\nrotated\n", encoding="utf-8")
    _git(repo, "add", "config.txt")
    _git(repo, "commit", "--quiet", "-m", "remove secret")
    policy_path = _write_policy(tmp_path, _policy())

    tracked_status = check_secrets.main([
        "--policy", str(policy_path), "--mode", "tracked",
        "--root", str(repo),
    ])
    history_status = check_secrets.main([
        "--policy", str(policy_path), "--mode", "history",
        "--root", str(repo),
    ])

    out = capsys.readouterr().out
    assert tracked_status == 0
    assert history_status == 1
    assert "github-token match" in out
    assert secret not in out


def test_history_mode_rejects_shallow_repository(tmp_path, capsys):
    repo = tmp_path / "repo"
    repo.mkdir()
    _git(repo, "init", "--quiet")
    (repo / "file.txt").write_text("content\n", encoding="utf-8")
    _git(repo, "add", "file.txt")
    _git(repo, "commit", "--quiet", "-m", "initial")
    head = subprocess.run(
        ("git", "rev-parse", "HEAD"),
        cwd=repo,
        check=True,
        capture_output=True,
        text=True,
    ).stdout.strip()
    (repo / ".git" / "shallow").write_text(head + "\n", encoding="utf-8")
    policy_path = _write_policy(tmp_path, _policy())

    status = check_secrets.main([
        "--policy", str(policy_path), "--mode", "history",
        "--root", str(repo),
    ])

    assert status == 1
    assert "requires a full clone" in capsys.readouterr().out


def test_envelope_is_content_free_for_clean_scan(tmp_path):
    target = tmp_path / "clean.txt"
    target.write_text("nothing secret\n", encoding="utf-8")
    policy_path = _write_policy(tmp_path, _policy())
    envelope_path = tmp_path / "envelope.json"

    status = check_secrets.main([
        "--policy", str(policy_path),
        "--scan-file", str(target),
        "--output-envelope", str(envelope_path),
    ])

    assert status == 0
    envelope = json.loads(envelope_path.read_text(encoding="utf-8"))
    assert envelope["schema_version"] == 1
    assert envelope["mode"] == "scan-file"
    assert envelope["scanned_documents"] == 1
    assert envelope["violations"] == 0
    assert envelope["findings"] == []
    assert set(envelope["rule_ids"]) == set(check_secrets._RULE_IDS)
    assert envelope["scan_time"]
