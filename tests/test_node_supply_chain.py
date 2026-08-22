import json
from datetime import date
from pathlib import Path

import pytest

from tools import check_node_vulnerabilities, normalize_node_sbom


_TODAY = date(2026, 8, 21)
_GHSA = "GHSA-aaaa-bbbb-cccc"


def _clean_report() -> dict:
    return {
        "auditReportVersion": 2,
        "vulnerabilities": {},
        "metadata": {
            "vulnerabilities": {
                "info": 0,
                "low": 0,
                "moderate": 0,
                "high": 0,
                "critical": 0,
                "total": 0,
            },
            "dependencies": {"prod": 131, "total": 130},
        },
    }


def _vulnerable_report() -> dict:
    report = _clean_report()
    report["vulnerabilities"] = {
        "katex": {
            "name": "katex",
            "severity": "high",
            "isDirect": False,
            "via": [
                {
                    "source": 1104001,
                    "name": "katex",
                    "url": f"https://github.com/advisories/{_GHSA}",
                    "title": "synthetic fixture advisory",
                    "severity": "high",
                    "range": "<0.99.0",
                }
            ],
            "effects": [],
            "range": "<0.99.0",
            "nodes": ["node_modules/katex"],
            "fixAvailable": True,
        }
    }
    report["metadata"]["vulnerabilities"]["high"] = 1
    report["metadata"]["vulnerabilities"]["total"] = 1
    return report


def _exception(**overrides) -> dict:
    item = {
        "package": "katex",
        "expires": "2026-12-31",
        "reason": "synthetic fixture acceptance",
        "mitigation": "synthetic fixture mitigation",
        "references": [f"https://github.com/advisories/{_GHSA}"],
    }
    item.update(overrides)
    return item


def _policy(exceptions: dict | None = None) -> dict:
    return {"schema_version": 1, "exceptions": exceptions or {}}


def _evaluate(report: dict, policy: dict):
    return check_node_vulnerabilities.evaluate(report, policy, today=_TODAY)


def test_clean_report_passes_empty_policy():
    errors, findings = _evaluate(_clean_report(), _policy())

    assert errors == []
    assert findings == []


def test_unaccepted_advisory_fails():
    errors, findings = _evaluate(_vulnerable_report(), _policy())

    assert errors == [f"katex: {_GHSA} is not accepted by policy"]
    assert findings == [
        {
            "accepted_through": None,
            "ghsa": _GHSA,
            "package": "katex",
            "severity": "high",
        }
    ]


def test_current_exception_accepts_advisory(capsys):
    errors, findings = _evaluate(
        _vulnerable_report(), _policy({_GHSA: _exception()})
    )

    assert errors == []
    assert findings[0]["accepted_through"] == "2026-12-31"
    assert f"{_GHSA} accepted through 2026-12-31" in capsys.readouterr().out


def test_expired_exception_fails():
    errors, _findings = _evaluate(
        _vulnerable_report(),
        _policy({_GHSA: _exception(expires="2026-08-20")}),
    )

    assert errors == [f"katex: {_GHSA} exception expired on 2026-08-20"]


def test_exception_for_wrong_package_fails():
    errors, _findings = _evaluate(
        _vulnerable_report(),
        _policy({_GHSA: _exception(package="remark")}),
    )

    assert errors == [f"katex: {_GHSA} exception names remark"]


def test_unused_exception_fails():
    errors, _findings = _evaluate(
        _clean_report(), _policy({_GHSA: _exception()})
    )

    assert errors == [
        f"policy exception {_GHSA} matched no reported advisory"
    ]


@pytest.mark.parametrize("mutate", (
    lambda report: report.update(auditReportVersion=1),
    lambda report: report.update(rogue=True),
    lambda report: report.pop("metadata"),
    lambda report: report["vulnerabilities"].update(bad="not-an-object"),
))
def test_malformed_report_fails_closed(mutate):
    report = _vulnerable_report()
    mutate(report)

    with pytest.raises(ValueError):
        _evaluate(report, _policy())


def test_advisory_without_exact_ghsa_url_fails_closed():
    report = _vulnerable_report()
    report["vulnerabilities"]["katex"]["via"][0]["url"] = (
        "https://example.com/advisories/GHSA-aaaa-bbbb-cccc"
    )

    with pytest.raises(ValueError, match="exact GHSA url"):
        _evaluate(report, _policy())


def test_transitive_reference_to_unreported_package_fails_closed():
    report = _vulnerable_report()
    report["vulnerabilities"]["katex"]["via"] = ["unreported-package"]

    with pytest.raises(ValueError, match="unreported package"):
        _evaluate(report, _policy())


@pytest.mark.parametrize("mutate", (
    lambda policy: policy.update(schema_version=2),
    lambda policy: policy.update(rogue=True),
    lambda policy: policy["exceptions"].update({"CVE-2026-1": _exception()}),
    lambda policy: policy["exceptions"].update(
        {_GHSA: _exception(references=["http://insecure.example"])}
    ),
    lambda policy: policy["exceptions"].update(
        {_GHSA: _exception(expires="not-a-date")}
    ),
))
def test_malformed_policy_fails_closed(mutate):
    policy = _policy()
    mutate(policy)

    with pytest.raises(ValueError):
        _evaluate(_vulnerable_report(), policy)


def test_duplicate_json_keys_fail_closed(tmp_path):
    report_path = tmp_path / "audit.json"
    report_path.write_text(
        '{"auditReportVersion": 2, "auditReportVersion": 2,'
        ' "vulnerabilities": {}, "metadata": {}}',
        encoding="utf-8",
    )

    with pytest.raises(ValueError, match="duplicate JSON key"):
        check_node_vulnerabilities._load_strict_json(
            report_path, "npm audit report"
        )


def test_main_writes_identity_envelope_and_passes(tmp_path, capsys):
    report_path = tmp_path / "audit.json"
    report_path.write_text(json.dumps(_clean_report()), encoding="utf-8")
    policy_path = tmp_path / "policy.json"
    policy_path.write_text(json.dumps(_policy()), encoding="utf-8")
    envelope_path = tmp_path / "envelope.json"

    status = check_node_vulnerabilities.main([
        str(report_path),
        "--policy", str(policy_path),
        "--scanner-npm", "11.12.0",
        "--scanner-node", "v24.0.0",
        "--output-envelope", str(envelope_path),
    ])

    assert status == 0
    assert "Node vulnerability policy passed" in capsys.readouterr().out
    envelope = json.loads(envelope_path.read_text(encoding="utf-8"))
    assert envelope["schema_version"] == 1
    assert envelope["scanner"] == {
        "node_version": "v24.0.0",
        "npm_version": "11.12.0",
        "tool": "npm-audit",
    }
    assert envelope["registry"] == "https://registry.npmjs.org"
    assert envelope["audit_report_version"] == 2
    assert envelope["findings"] == []
    assert envelope["totals"]["total"] == 0
    assert envelope["scan_time"]


def test_main_fails_but_retains_envelope_for_violation(tmp_path, capsys):
    report_path = tmp_path / "audit.json"
    report_path.write_text(json.dumps(_vulnerable_report()), encoding="utf-8")
    policy_path = tmp_path / "policy.json"
    policy_path.write_text(json.dumps(_policy()), encoding="utf-8")
    envelope_path = tmp_path / "envelope.json"

    status = check_node_vulnerabilities.main([
        str(report_path),
        "--policy", str(policy_path),
        "--scanner-npm", "11.12.0",
        "--scanner-node", "v24.0.0",
        "--output-envelope", str(envelope_path),
    ])

    assert status == 1
    assert f"{_GHSA} is not accepted by policy" in capsys.readouterr().out
    envelope = json.loads(envelope_path.read_text(encoding="utf-8"))
    assert envelope["findings"][0]["ghsa"] == _GHSA
    assert envelope["findings"][0]["accepted_through"] is None


def test_repository_policy_is_strict_and_currently_empty():
    policy = check_node_vulnerabilities._load_strict_json(
        Path("node-vulnerability-policy.json"), "node vulnerability policy"
    )

    exceptions = check_node_vulnerabilities._policy_exceptions(policy)

    assert exceptions == {}


def _raw_sbom(serial: str, timestamp: str) -> dict:
    return {
        "$schema": "http://cyclonedx.org/schema/bom-1.5.schema.json",
        "bomFormat": "CycloneDX",
        "specVersion": "1.5",
        "serialNumber": serial,
        "version": 1,
        "metadata": {
            "timestamp": timestamp,
            "lifecycles": [{"phase": "build"}],
            "tools": [{"vendor": "npm", "name": "cli", "version": "11.12.0"}],
            "component": {
                "bom-ref": "root@1.0.0",
                "type": "application",
                "name": "root",
            },
        },
        "components": [
            {"bom-ref": "pkg:npm/b@2.0.0", "type": "library", "name": "b"},
            {"bom-ref": "pkg:npm/a@1.0.0", "type": "library", "name": "a"},
        ],
        "dependencies": [
            {
                "ref": "root@1.0.0",
                "dependsOn": ["pkg:npm/b@2.0.0", "pkg:npm/a@1.0.0"],
            },
            {"ref": "pkg:npm/a@1.0.0", "dependsOn": []},
            {"ref": "pkg:npm/b@2.0.0"},
        ],
    }


def test_normalization_is_byte_stable_across_volatile_fields():
    first = normalize_node_sbom.serialized_bytes(
        normalize_node_sbom.normalize(
            _raw_sbom("urn:uuid:11111111", "2026-08-21T01:00:00.000Z")
        )
    )
    second = normalize_node_sbom.serialized_bytes(
        normalize_node_sbom.normalize(
            _raw_sbom("urn:uuid:22222222", "2026-08-22T02:00:00.000Z")
        )
    )

    assert first == second
    assert first.endswith(b"\n")


def test_normalization_orders_collections_and_strips_volatility():
    normalized = normalize_node_sbom.normalize(
        _raw_sbom("urn:uuid:x", "2026-08-21T01:00:00.000Z")
    )

    assert "serialNumber" not in normalized
    assert "timestamp" not in normalized["metadata"]
    assert normalized["metadata"]["tools"][0]["version"] == "11.12.0"
    assert [c["bom-ref"] for c in normalized["components"]] == [
        "pkg:npm/a@1.0.0",
        "pkg:npm/b@2.0.0",
    ]
    assert [d["ref"] for d in normalized["dependencies"]] == [
        "pkg:npm/a@1.0.0",
        "pkg:npm/b@2.0.0",
        "root@1.0.0",
    ]
    assert normalized["dependencies"][2]["dependsOn"] == [
        "pkg:npm/a@1.0.0",
        "pkg:npm/b@2.0.0",
    ]


@pytest.mark.parametrize("mutate", (
    lambda sbom: sbom.update(bomFormat="SPDX"),
    lambda sbom: sbom.pop("metadata"),
    lambda sbom: sbom["metadata"].pop("tools"),
    lambda sbom: sbom["components"].append(
        {"bom-ref": "pkg:npm/a@1.0.0", "type": "library", "name": "dup"}
    ),
    lambda sbom: sbom["dependencies"].append({"ref": "root@1.0.0"}),
    lambda sbom: sbom["components"].append({"type": "library"}),
))
def test_malformed_sbom_fails_closed(mutate):
    sbom = _raw_sbom("urn:uuid:x", "2026-08-21T01:00:00.000Z")
    mutate(sbom)

    with pytest.raises(ValueError):
        normalize_node_sbom.normalize(sbom)


def test_normalize_main_round_trip(tmp_path, capsys):
    raw_path = tmp_path / "raw.json"
    raw_path.write_text(
        json.dumps(_raw_sbom("urn:uuid:x", "2026-08-21T01:00:00.000Z")),
        encoding="utf-8",
    )
    out_path = tmp_path / "normalized.json"

    status = normalize_node_sbom.main([
        str(raw_path), "--output", str(out_path)
    ])

    assert status == 0
    assert "Normalized Node SBOM: 2 components" in capsys.readouterr().out
    document = json.loads(out_path.read_text(encoding="utf-8"))
    assert document["bomFormat"] == "CycloneDX"
    assert "serialNumber" not in document
