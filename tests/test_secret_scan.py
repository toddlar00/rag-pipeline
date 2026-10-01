import hashlib
import io
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


def _git(cwd: Path, *arguments: str, data: bytes | None = None) -> str:
    return subprocess.run(
        ("git", *arguments),
        cwd=cwd,
        check=True,
        capture_output=True,
        input=data,
        env={
            "GIT_AUTHOR_NAME": "fixture",
            "GIT_AUTHOR_EMAIL": "fixture@example.com",
            "GIT_COMMITTER_NAME": "fixture",
            "GIT_COMMITTER_EMAIL": "fixture@example.com",
            "GIT_CONFIG_GLOBAL": "/dev/null" if sys.platform != "win32" else "NUL",
            "GIT_CONFIG_SYSTEM": "/dev/null" if sys.platform != "win32" else "NUL",
            "PATH": __import__("os").environ["PATH"],
        },
    ).stdout.decode("utf-8")


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


def _frozen_history_documents(root: Path):
    """Frozen copy of the per-object history reader at 3aede7d (the oracle)."""
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


def _history_fixture(repo: Path, object_format: str) -> str:
    """Build a synthetic history; return the runtime-built deleted canary."""
    repo.mkdir()
    _git(repo, "init", "--quiet", f"--object-format={object_format}")
    secret = _canary("ghp_", length=36)
    (repo / "config.txt").write_text("token\n" + secret + "\n", encoding="utf-8")
    (repo / "binary.dat").write_bytes(b"\x00\xff\r\n\x01" * 64 + b"no newline")
    (repo / "empty.txt").write_bytes(b"")
    (repo / "big.txt").write_bytes(b"large blob line\n" * 8192)
    (repo / "sp ace.txt").write_text("space name\n", encoding="utf-8")
    _git(repo, "add", "-A")
    _git(repo, "commit", "--quiet", "-m", "one")
    _git(repo, "mv", "sp ace.txt", "renamed space.txt")
    (repo / "renamed space.txt").write_text("renamed\n", encoding="utf-8")
    (repo / "config.txt").unlink()
    _git(repo, "add", "-A")
    _git(repo, "commit", "--quiet", "-m", "rename and delete")
    _git(repo, "tag", "-a", "v1", "-m", "annotated")
    loose = _git(repo, "hash-object", "-w", "--stdin", data=b"tag blob\n").strip()
    _git(repo, "tag", "blob-tag", loose)
    _git(repo, "checkout", "--quiet", "-b", "side")
    (repo / "side.txt").write_text("side branch\n", encoding="utf-8")
    _git(repo, "add", "side.txt")
    _git(repo, "commit", "--quiet", "-m", "side")
    _git(repo, "checkout", "--quiet", "-")
    # Plumbing-only names that the listing parse splits: a tab, a line
    # separator, a revision expression, and canonical-looking ids (one
    # absent, one aliasing a real blob) after a line separator.
    big = _git(repo, "rev-parse", "HEAD:big.txt").strip()
    odd_names = (
        "tab\tname.txt",
        "with sep.txt",
        "x HEAD:big.txt y",
        "z " + "0" * (len(big) - 1) + "1 absent",
        "alias\u0085" + big + " alias",
    )
    entries = b""
    for index, name in enumerate(odd_names):
        blob = _git(
            repo, "hash-object", "-w", "--stdin", data=b"odd %d\n" % index
        ).strip()
        entries += f"100644 blob {blob}\t{name}\0".encode("utf-8")
    tree = _git(repo, "mktree", "-z", data=entries).strip()
    commit = _git(repo, "commit-tree", tree, "-m", "odd names").strip()
    _git(repo, "update-ref", "refs/heads/odd", commit)
    return secret


@pytest.mark.parametrize("object_format", ("sha1", "sha256"))
def test_history_reader_matches_frozen_per_object_reader(
    tmp_path, capsys, object_format
):
    repo = tmp_path / "repo"
    secret = _history_fixture(repo, object_format)

    expected = list(_frozen_history_documents(repo))
    actual = list(check_secrets._history_documents(repo))

    assert actual == expected
    assert check_secrets.evaluate(
        iter(actual), {}, today=_TODAY
    ) == check_secrets.evaluate(iter(expected), {}, today=_TODAY)
    locations = [location for location, _text in actual]
    assert "HEAD:big.txt:y" in locations
    assert not any(location.startswith("000000000000") for location in locations)
    assert any(text == "" for _location, text in actual)
    assert any("\x00" in text for _location, text in actual)
    status = check_secrets.main([
        "--policy", str(_write_policy(tmp_path, _policy())),
        "--mode", "history", "--root", str(repo),
    ])
    out = capsys.readouterr().out
    assert status == 1
    assert out.count("github-token match") == 1
    assert secret not in out


def test_history_reads_listed_objects_through_one_batch_process(
    tmp_path, monkeypatch
):
    repo = tmp_path / "repo"
    _history_fixture(repo, "sha1")
    calls = []
    real_popen = subprocess.Popen

    def spy(arguments, **kwargs):
        calls.append(tuple(arguments[3:]))
        return real_popen(arguments, **kwargs)

    monkeypatch.setattr(check_secrets.subprocess, "Popen", spy)
    list(check_secrets._history_documents(repo))

    assert calls.count(("cat-file", "--batch")) == 1
    per_object = {
        call[2] for call in calls
        if call[:2] in (("cat-file", "-t"), ("cat-file", "blob"))
    }
    # Only the listing-parse fragments that rev-list never listed.
    assert per_object == {"HEAD:big.txt", "0" * 39 + "1"}


_BATCH_OID = "ab" * 20


@pytest.mark.parametrize("reply", (
    b"",
    f"{_BATCH_OID} missing\n".encode(),
    f"{_BATCH_OID} ambiguous\n".encode(),
    ("cd" * 20 + " blob 3\nabc\n").encode(),
    f"{_BATCH_OID.upper()} blob 3\nabc\n".encode(),
    f"{_BATCH_OID} symlink 3\nabc\n".encode(),
    f"{_BATCH_OID} blob 03\nabc\n".encode(),
    f"{_BATCH_OID} blob {'1' * 100}\n".encode(),
    f"{_BATCH_OID} blob 5\nabc\n".encode(),
    f"{_BATCH_OID} blob 3\nabcX".encode(),
    f"{_BATCH_OID} blob 3\nabc".encode(),
))
def test_batch_record_framing_violations_fail_closed(reply):
    with pytest.raises(ValueError, match="cannot read blob " + _BATCH_OID):
        check_secrets._read_batch_record(io.BytesIO(reply), _BATCH_OID)


def test_batch_records_are_read_by_size_in_lock_step():
    body = b"\x00\r\n\nline\n"
    tree_oid = "cd" * 32
    stream = io.BytesIO(
        f"{_BATCH_OID} blob {len(body)}\n".encode() + body + b"\n"
        + f"{tree_oid} tree 0\n\n".encode()
    )

    assert check_secrets._read_batch_record(stream, _BATCH_OID) == (
        b"blob", body,
    )
    assert check_secrets._read_batch_record(stream, tree_oid) == (b"tree", b"")
    assert stream.read() == b""


class _RecordingSink(io.BytesIO):
    captured = b""

    def close(self):
        if not self.closed:
            self.captured = self.getvalue()
        super().close()


class _FakeBatchProcess:
    def __init__(self, reply: bytes, status: int) -> None:
        self.stdin = _RecordingSink()
        self.stdout = io.BytesIO(reply)
        self.returncode = None
        self._status = status

    def poll(self):
        return self.returncode

    def kill(self):
        self.returncode = -9

    def wait(self):
        if self.returncode is None:
            self.returncode = self._status
        return self.returncode


@pytest.mark.parametrize(("reply", "status", "message"), (
    ("{oid} blob 8\ncontent\n\ntrailing", 0, "git cat-file --batch failed"),
    ("{oid} blob 8\ncontent\n\n", 1, "git cat-file --batch failed"),
    ("{oid} missing\n", 0, "cannot read blob"),
))
def test_history_batch_process_faults_fail_closed(
    tmp_path, monkeypatch, capsys, reply, status, message
):
    repo = tmp_path / "repo"
    repo.mkdir()
    _git(repo, "init", "--quiet")
    (repo / "file.txt").write_text("content\n", encoding="utf-8")
    _git(repo, "add", "file.txt")
    _git(repo, "commit", "--quiet", "-m", "initial")
    oid = _git(repo, "rev-parse", "HEAD:file.txt").strip()
    fakes = []
    real_popen = subprocess.Popen

    def fake_popen(arguments, **kwargs):
        if tuple(arguments[3:]) != ("cat-file", "--batch"):
            return real_popen(arguments, **kwargs)
        fakes.append(_FakeBatchProcess(reply.format(oid=oid).encode(), status))
        return fakes[-1]

    monkeypatch.setattr(check_secrets.subprocess, "Popen", fake_popen)
    result = check_secrets.main([
        "--policy", str(_write_policy(tmp_path, _policy())),
        "--mode", "history", "--root", str(repo),
    ])

    assert result == 1
    assert f"error: {message}" in capsys.readouterr().out
    assert fakes[0].stdin.captured == (oid + "\n").encode()
    assert fakes[0].returncode is not None
