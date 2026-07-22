import ast
from datetime import date
import json
from pathlib import Path

from tools import (
    check_dependency_policy,
    check_licenses,
    check_vulnerabilities,
    refresh_locks,
)


def _write(path: Path, content: str) -> None:
    path.write_text(content, encoding="utf-8")


def _valid_policy_tree(root: Path) -> None:
    lock_hash = "0" * 64
    _write(root / "requirements.txt", "core>=1,<2\n")
    _write(root / "requirements-optional.txt", "extra>=2,<3\n")
    _write(
        root / "requirements-smoke.txt",
        "-r requirements-test.txt\nbackend>=1,<2\n",
    )
    _write(root / "requirements-test.txt", "pytest==9.1.1\n")
    _write(
        root / "requirements-service.txt",
        "fastapi==0.139.2\nqdrant-client==1.18.0\ntqdm==4.69.0\n",
    )
    _write(root / "requirements-audit.txt", "core==1.5\n")
    _write(root / "requirements-security.txt", "pip-audit==2.10.1\n")
    _write(root / "requirements-lock-tools.txt", "uv==0.11.31\n")
    _write(
        root / "requirements-core.lock",
        f"core==1.5 --hash=sha256:{lock_hash}\n",
    )
    _write(
        root / "requirements-full.lock",
        f"core==1.5 --hash=sha256:{lock_hash}\n"
        f"extra==2.5 --hash=sha256:{lock_hash}\n",
    )
    _write(
        root / "requirements-test.lock",
        f"pytest==9.1.1 --hash=sha256:{lock_hash}\n",
    )
    _write(
        root / "requirements-service.lock",
        f"fastapi==0.139.2 --hash=sha256:{lock_hash}\n"
        f"qdrant-client==1.18.0 --hash=sha256:{lock_hash}\n"
        f"tqdm==4.69.0 --hash=sha256:{lock_hash}\n",
    )
    _write(
        root / "requirements-smoke.lock",
        f"pytest==9.1.1 --hash=sha256:{lock_hash}\n"
        f"backend==1.5 --hash=sha256:{lock_hash}\n",
    )
    _write(
        root / "requirements-security.lock",
        f"pip-audit==2.10.1 --hash=sha256:{lock_hash}\n",
    )
    _write(
        root / "requirements-lock-tools.lock",
        f"uv==0.11.31 --hash=sha256:{lock_hash}\n",
    )
    _write(
        root / "requirements-all.txt",
        "-r requirements.txt\n-r requirements-optional.txt\n",
    )


def test_dependency_policy_accepts_bounded_runtime_and_pinned_tools(tmp_path):
    _valid_policy_tree(tmp_path)

    assert check_dependency_policy.validate(tmp_path) == []


def test_dependency_policy_reports_unbounded_and_unpinned_entries(tmp_path):
    _valid_policy_tree(tmp_path)
    _write(tmp_path / "requirements.txt", "core>=1\n")
    _write(tmp_path / "requirements-test.txt", "pytest>=9\n")

    errors = check_dependency_policy.validate(tmp_path)

    assert any("core needs an upper bound" in error for error in errors)
    assert any("pytest must use one exact == pin" in error for error in errors)


def test_dependency_policy_rejects_incomplete_or_unpinned_lock(tmp_path):
    _valid_policy_tree(tmp_path)
    _write(tmp_path / "requirements-full.lock", "core>=1,<2\n")

    errors = check_dependency_policy.validate(tmp_path)

    assert any("core must use one exact == pin" in error for error in errors)
    assert any("locked requirement has no hashes" in error for error in errors)
    assert any("missing direct dependencies: extra" in error for error in errors)


def test_dependency_policy_matches_normalized_audit_pins_to_full_lock(tmp_path):
    _valid_policy_tree(tmp_path)
    _write(tmp_path / "requirements-audit.txt", "core==9.9\n")

    errors = check_dependency_policy.validate(tmp_path)

    assert any(
        "normalized pin for core does not match" in error for error in errors
    )


def test_dependency_policy_requires_smoke_only_backends_in_smoke_lock(tmp_path):
    _valid_policy_tree(tmp_path)
    _write(
        tmp_path / "requirements-smoke.lock",
        f"pytest==9.1.1 --hash=sha256:{'0' * 64}\n",
    )

    errors = check_dependency_policy.validate(tmp_path)

    assert any("missing direct dependencies: backend" in error for error in errors)


def test_dependency_policy_restricts_bounded_file_includes(tmp_path):
    _valid_policy_tree(tmp_path)
    _write(root_file := tmp_path / "requirements.txt", "-r surprise.txt\n")
    _write(
        smoke_file := tmp_path / "requirements-smoke.txt",
        "-r surprise.txt\nbackend>=1,<2\n",
    )

    errors = check_dependency_policy.validate(tmp_path)

    assert any(root_file.name in error and "includes: none" in error for error in errors)
    assert any(
        smoke_file.name in error and "requirements-test.txt" in error
        for error in errors
    )


def test_lock_refresh_commands_cover_every_lock_and_cpu_runtime():
    commands = refresh_locks.lock_commands("uv", upgrade=True)

    assert [command[-2] for command in commands] == [
        output for _source, output, _cpu in refresh_locks.LOCK_SPECS
    ]
    assert all(command[-1] == "--upgrade" for command in commands)
    for command, (_source, _output, cpu) in zip(
        commands, refresh_locks.LOCK_SPECS, strict=True
    ):
        assert ("--torch-backend" in command) is cpu
        assert "--generate-hashes" in command


def test_license_policy_rejects_denied_license_and_honors_documented_exception():
    report = [
        {
            "Name": "unsafe",
            "Version": "1.0",
            "License": "GNU General Public License v3 (GPLv3)",
        },
        {"Name": "PyMuPDF", "Version": "1.28.0", "License": "GNU AFFERO GPL 3.0"},
        {"Name": "mystery", "Version": "1.0", "License": "UNKNOWN"},
    ]
    policy = {
        "schema_version": 1,
        "denied_license_patterns": ["AGPL|AFFERO", "(?<!L)GPL"],
        "allowed_packages": {
            "pymupdf": {
                "reason": "reviewed dual-license dependency",
                "scope": "private local evaluation",
                "owner": "repository owner",
                "version": "1.28.0",
                "expires": "2099-01-01",
                "reference": "https://example.com/license",
            }
        },
        "allowed_unknown_packages": {
            "mystery": {
                "reason": "upstream metadata review",
                "owner": "repository owner",
                "version": "1.0",
                "expires": "2099-01-01",
                "reference": "https://example.com/license",
            }
        },
    }

    violations, warnings = check_licenses.evaluate(report, policy)

    assert violations == ["unsafe: GNU General Public License v3 (GPLv3)"]
    assert any("PyMuPDF" in warning for warning in warnings)
    assert any("mystery" in warning for warning in warnings)


def test_license_policy_rejects_unreviewed_unknown_metadata():
    report = [{"Name": "mystery", "Version": "1.0", "License": "UNKNOWN"}]
    policy = {
        "schema_version": 1,
        "denied_license_patterns": ["GPL"],
        "allowed_packages": {},
        "allowed_unknown_packages": {},
    }

    violations, warnings = check_licenses.evaluate(report, policy)

    assert violations == ["mystery: license metadata is unknown"]
    assert warnings == []


def test_license_policy_does_not_carry_unknown_review_to_new_version():
    report = [{"Name": "mystery", "Version": "2.0", "License": "UNKNOWN"}]
    policy = {
        "schema_version": 1,
        "denied_license_patterns": ["GPL"],
        "allowed_packages": {},
        "allowed_unknown_packages": {
            "mystery": {
                "reason": "reviewed wheel metadata",
                "owner": "repository owner",
                "version": "1.0",
                "expires": "2099-01-01",
                "reference": "https://example.com/license",
            }
        },
    }

    violations, _warnings = check_licenses.evaluate(report, policy)

    assert violations == [
        "mystery: unknown-license exception covers 1.0, not 2.0"
    ]


def test_license_policy_rejects_expired_denied_license_exception():
    report = [{"Name": "copyleft", "Version": "1.0", "License": "GPLv3"}]
    policy = {
        "schema_version": 1,
        "denied_license_patterns": ["GPL"],
        "allowed_packages": {
            "copyleft": {
                "reason": "temporary review",
                "scope": "private evaluation",
                "owner": "repository owner",
                "version": "1.0",
                "expires": "2026-07-01",
                "reference": "https://example.com/license",
            }
        },
        "allowed_unknown_packages": {},
    }

    violations, _warnings = check_licenses.evaluate(
        report, policy, as_of=date(2026, 7, 21)
    )

    assert violations == ["copyleft: license exception expired on 2026-07-01"]


def test_license_policy_cli_rejects_malformed_report(tmp_path):
    report_path = tmp_path / "licenses.json"
    policy_path = tmp_path / "policy.json"
    report_path.write_text(json.dumps({"not": "a list"}), encoding="utf-8")
    policy_path.write_text(
        json.dumps({
            "schema_version": 1,
            "denied_license_patterns": ["GPL"],
            "allowed_packages": {},
            "allowed_unknown_packages": {},
        }),
        encoding="utf-8",
    )

    assert check_licenses.main([
        str(report_path), "--policy", str(policy_path)
    ]) == 2


def test_vulnerability_policy_accepts_scoped_exception_and_audit_skip():
    report = {
        "dependencies": [
            {
                "name": "chromadb",
                "version": "1.5.9",
                "vulns": [{"id": "PYSEC-2026-311"}],
            },
            {"name": "torch", "skip_reason": "local CPU version"},
        ]
    }
    policy = {
        "schema_version": 1,
        "exceptions": {
            "PYSEC-2026-311": {
                "package": "chromadb",
                "expires": "2026-08-31",
                "reason": "no upstream patch",
                "mitigation": "embedded client only",
                "references": ["https://osv.dev/vulnerability/PYSEC-2026-311"],
            }
        },
        "allowed_skips": {
            "torch": {
                "reason": "local CPU wheel",
                "version": "2.13.0",
                "expected_reason_pattern": "^local CPU version$",
                "expires": "2026-08-31",
                "reference": "https://example.com/audit",
            }
        },
    }

    violations, warnings = check_vulnerabilities.evaluate(
        report, policy, as_of=date(2026, 7, 21)
    )

    assert violations == []
    assert any("accepted through" in warning for warning in warnings)
    assert any("audit skip" in warning for warning in warnings)


def test_vulnerability_policy_rejects_changed_skip_reason_or_version():
    report = {
        "dependencies": [
            {"name": "torch", "skip_reason": "different local version"}
        ]
    }
    policy = {
        "schema_version": 1,
        "exceptions": {},
        "allowed_skips": {
            "torch": {
                "reason": "local CPU wheel",
                "version": "2.13.0",
                "expected_reason_pattern": "^expected local version$",
                "expires": "2026-08-31",
                "reference": "https://example.com/audit",
            }
        },
    }

    violations, _warnings = check_vulnerabilities.evaluate(
        report, policy, as_of=date(2026, 7, 21)
    )

    assert violations == [
        "torch: audit skip reason/version does not match policy"
    ]


def test_vulnerability_policy_rejects_expired_unaccepted_and_stale_entries():
    report = {
        "dependencies": [
            {
                "name": "chromadb",
                "version": "1.5.9",
                "vulns": [
                    {"id": "PYSEC-2026-311"},
                    {"id": "CVE-2026-99999"},
                ],
            }
        ]
    }
    policy = {
        "schema_version": 1,
        "exceptions": {
            advisory_id: {
                "package": "chromadb",
                "expires": "2026-07-01",
                "reason": "temporary",
                "mitigation": "local only",
                "references": ["https://example.com/advisory"],
            }
            for advisory_id in ("PYSEC-2026-311", "PYSEC-2026-STALE")
        },
        "allowed_skips": {},
    }

    violations, _warnings = check_vulnerabilities.evaluate(
        report, policy, as_of=date(2026, 7, 21)
    )

    assert any("expired" in violation for violation in violations)
    assert any("CVE-2026-99999" in violation for violation in violations)
    assert any("PYSEC-2026-STALE" in violation for violation in violations)


def test_chroma_advisory_exception_requires_embedded_persistent_client_only():
    root = Path(__file__).resolve().parents[1]
    policy = json.loads(
        (root / "dependency-vulnerability-policy.json").read_text(
            encoding="utf-8"
        )
    )
    assert "PYSEC-2026-311" in policy["exceptions"]

    tree = ast.parse((root / "rag.py").read_text(encoding="utf-8"))
    constructor_names = {
        node.func.attr
        for node in ast.walk(tree)
        if isinstance(node, ast.Call)
        and isinstance(node.func, ast.Attribute)
        and isinstance(node.func.value, ast.Name)
        and node.func.value.id == "chromadb"
        and node.func.attr.endswith("Client")
    }
    imported_client_names = {
        alias.name
        for node in ast.walk(tree)
        if isinstance(node, ast.ImportFrom)
        and node.module is not None
        and node.module.startswith("chromadb")
        for alias in node.names
        if alias.name.endswith("Client")
    }

    assert constructor_names == {"PersistentClient"}
    assert imported_client_names == set()
