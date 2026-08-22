import json
from datetime import date
from pathlib import Path

import pytest

from tools import check_static_security


_TODAY = date(2026, 8, 22)

_POSITIVE_SOURCES = {
    "S102": "exec(payload)\n",
    "S104": 'HOST = "0.0.0.0"\n',
    "S301": "import pickle\npickle.loads(payload)\n",
    "S302": "import marshal\nmarshal.loads(payload)\n",
    "S307": "eval(payload)\n",
    "S324": "import hashlib\nhashlib.md5(payload)\n",
    "S501": (
        "import requests\n"
        "requests.get(url, verify=False, timeout=5)\n"
    ),
    "S506": "import yaml\nyaml.load(stream)\n",
    "S602": (
        "import subprocess\n"
        "subprocess.run(command, shell=True)\n"
    ),
    "S605": 'import os\nos.system("ls " + name)\n',
    "S608": (
        'QUERY = "SELECT * FROM users WHERE name = \'%s\'" % name\n'
    ),
}

_NEGATIVE_SOURCES = {
    "S104": 'HOST = "127.0.0.1"\n',
    "S301": "import json\njson.loads(payload)\n",
    "S324": "import hashlib\nhashlib.sha256(payload)\n",
    "S501": (
        "import requests\n"
        "requests.get(url, timeout=5)\n"
    ),
    "S506": "import yaml\nyaml.safe_load(stream)\n",
    "S602": (
        "import subprocess\n"
        "subprocess.run(command, check=True)\n"
    ),
}


def _policy(**overrides):
    policy = {
        "schema_version": 1,
        "ruff_version": "0.16.3",
        "blocking_rules": [
            "S102", "S104", "S301", "S302", "S307", "S324", "S501",
            "S506", "S602", "S604", "S605", "S606", "S608",
        ],
        "test_exempt_rules": ["S102", "S104", "S301", "S302", "S307"],
        "suppressions": [],
    }
    policy.update(overrides)
    return policy


def _suppression(**overrides):
    item = {
        "rule": "S324",
        "path": "module.py",
        "count": 1,
        "expires": "2026-12-31",
        "reason": "synthetic fixture suppression",
        "references": ["https://example.com/review"],
    }
    item.update(overrides)
    return item


def _finding(rule="S324", path="module.py", row=10):
    return {"path": path, "row": row, "rule": rule}


def test_per_rule_positive_fixtures_alert(tmp_path):
    for index, (rule, source) in enumerate(_POSITIVE_SOURCES.items()):
        (tmp_path / f"positive_{index}_{rule.lower()}.py").write_text(
            source, encoding="utf-8"
        )

    raw = check_static_security._run_ruff(
        sorted(_POSITIVE_SOURCES), tmp_path
    )
    caught = {item["code"] for item in raw}

    assert caught == set(_POSITIVE_SOURCES)


def test_per_rule_negative_fixtures_stay_quiet(tmp_path):
    for index, (rule, source) in enumerate(_NEGATIVE_SOURCES.items()):
        (tmp_path / f"negative_{index}_{rule.lower()}.py").write_text(
            source, encoding="utf-8"
        )

    raw = check_static_security._run_ruff(
        sorted(_POSITIVE_SOURCES), tmp_path
    )

    assert raw == []


def test_unaccepted_finding_fails():
    errors, reported = check_static_security.evaluate(
        [_finding()], _policy(), today=_TODAY
    )

    assert errors == ["module.py:10: S324 is not accepted by policy"]
    assert reported[0]["suppressed_through"] is None


def test_current_suppression_accepts_counted_finding(capsys):
    errors, reported = check_static_security.evaluate(
        [_finding()],
        _policy(suppressions=[_suppression()]),
        today=_TODAY,
    )

    assert errors == []
    assert reported[0]["suppressed_through"] == "2026-12-31"
    assert "suppressed through 2026-12-31" in capsys.readouterr().out


def test_expired_suppression_fails():
    errors, _reported = check_static_security.evaluate(
        [_finding()],
        _policy(suppressions=[_suppression(expires="2026-08-01")]),
        today=_TODAY,
    )

    assert errors == [
        "module.py:10: S324 suppression expired on 2026-08-01"
    ]


def test_unused_suppression_fails():
    errors, _reported = check_static_security.evaluate(
        [], _policy(suppressions=[_suppression()]), today=_TODAY
    )

    assert errors == [
        "suppression record (S324, module.py) matched nothing"
    ]


def test_count_drift_fails_closed():
    errors, _reported = check_static_security.evaluate(
        [_finding(row=10), _finding(row=20)],
        _policy(suppressions=[_suppression(count=1)]),
        today=_TODAY,
    )

    assert errors == [
        "suppression record (S324, module.py) declares 1 finding(s) but 2 "
        "were observed"
    ]


def test_exempt_rules_skip_test_paths_only():
    findings = [
        _finding(rule="S301", path="tests/test_something.py"),
        _finding(rule="S301", path="module.py"),
        _finding(rule="S324", path="tests/test_other.py"),
    ]

    errors, reported = check_static_security.evaluate(
        findings, _policy(), today=_TODAY
    )

    assert errors == [
        "module.py:10: S301 is not accepted by policy",
        "tests/test_other.py:10: S324 is not accepted by policy",
    ]
    assert len(reported) == 2


@pytest.mark.parametrize("mutate", (
    lambda policy: policy.update(schema_version=2),
    lambda policy: policy.update(rogue=True),
    lambda policy: policy.update(ruff_version="latest"),
    lambda policy: policy.update(blocking_rules=["S999", "S102"]),
    lambda policy: policy.update(test_exempt_rules=["S110"]),
    lambda policy: policy.update(suppressions=[_suppression(count=0)]),
    lambda policy: policy.update(
        suppressions=[_suppression(references=["http://insecure"])]
    ),
    lambda policy: policy.update(
        suppressions=[_suppression(), _suppression()]
    ),
    lambda policy: policy.update(
        suppressions=[_suppression(expires="soon")]
    ),
))
def test_malformed_policy_fails_closed(tmp_path, mutate):
    policy = _policy()
    mutate(policy)
    path = tmp_path / "policy.json"
    path.write_text(json.dumps(policy), encoding="utf-8")

    with pytest.raises(ValueError):
        check_static_security._load_policy(path)


def test_duplicate_policy_json_keys_fail_closed(tmp_path):
    path = tmp_path / "policy.json"
    path.write_text(
        '{"schema_version": 1, "schema_version": 1}', encoding="utf-8"
    )

    with pytest.raises(ValueError, match="duplicate JSON key"):
        check_static_security._load_policy(path)


def test_repository_policy_is_valid_and_narrow():
    policy = check_static_security._load_policy(
        Path("static-security-policy.json")
    )

    assert policy["ruff_version"] == "0.16.3"
    assert "S101" not in policy["blocking_rules"]
    assert "S603" not in policy["blocking_rules"]
    assert len(policy["suppressions"]) == 1
    assert policy["suppressions"][0]["path"] == "retrieval_core.py"


def test_normalize_relativizes_and_orders(tmp_path):
    raw = [
        {
            "code": "S102",
            "filename": str((tmp_path / "b.py").resolve()),
            "location": {"row": 3, "column": 1},
            "message": "should never be surfaced",
        },
        {
            "code": "S102",
            "filename": str((tmp_path / "a.py").resolve()),
            "location": {"row": 7, "column": 1},
            "message": "should never be surfaced",
        },
    ]

    findings = check_static_security._normalize(tmp_path, raw)

    assert findings == [
        {"path": "a.py", "row": 7, "rule": "S102"},
        {"path": "b.py", "row": 3, "rule": "S102"},
    ]
    assert "message" not in json.dumps(findings)


def _pinned_ruff_available() -> bool:
    try:
        return check_static_security._ruff_version() == "0.16.3"
    except ValueError:
        return False


@pytest.mark.skipif(
    not _pinned_ruff_available(),
    reason="requires the hash-locked ruff 0.16.3",
)
def test_repository_tree_passes_with_committed_policy(tmp_path, capsys):
    envelope_path = tmp_path / "envelope.json"

    status = check_static_security.main([
        "--policy", "static-security-policy.json",
        "--output-envelope", str(envelope_path),
    ])

    assert status == 0
    envelope = json.loads(envelope_path.read_text(encoding="utf-8"))
    assert envelope["violations"] == 0
    assert envelope["findings"] == [
        {
            "path": "retrieval_core.py",
            "row": 166,
            "rule": "S324",
            "suppressed_through": "2026-11-30",
        }
    ]
    payload = json.dumps(envelope)
    assert "\\\\" not in payload
    assert "message" not in payload
