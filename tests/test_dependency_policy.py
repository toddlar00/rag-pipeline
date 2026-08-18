import ast
from datetime import date
import json
from pathlib import Path
import subprocess

from tools import (
    check_dependency_policy,
    check_licenses,
    check_vulnerabilities,
    refresh_locks,
)


EXPECTED_DEPENDENCY_DOMAINS = {
    "pdf-docling": ["docling", "docling-core", "pymupdf", "pypdfium2"],
    "vector-stores": ["chromadb", "onnxruntime", "qdrant-client"],
    "ml-runtime": [
        "einops",
        "flagembedding",
        "numpy",
        "rank-bm25",
        "sentence-transformers",
        "torch",
        "torchvision",
        "tqdm",
    ],
    "service-ui": ["fastapi", "gradio", "uvicorn"],
    "provider-transport": ["google-genai", "requests"],
    "test-audit-tooling": [
        "pip",
        "pip-audit",
        "pip-licenses",
        "pytest",
        "ruff",
        "uv",
    ],
}


def _write(path: Path, content: str) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(content, encoding="utf-8")


def _git(root: Path, *args: str) -> str:
    return subprocess.check_output(
        ["git", "-C", str(root), *args], text=True,
    ).strip()


def _commit_policy_tree(root: Path, message: str) -> str:
    if not (root / ".git").exists():
        _git(root, "init", "-b", "main")
        _git(root, "config", "user.name", "Dependency Policy Test")
        _git(root, "config", "user.email", "dependency-policy@example.invalid")
    _git(root, "add", ".")
    _git(root, "commit", "-m", message)
    return _git(root, "rev-parse", "HEAD")


def _split_fixture_domains(root: Path) -> None:
    runtime = ["backend", "core", "extra"]
    tooling = [
        "fastapi", "pip-audit", "pytest", "qdrant-client", "tqdm", "uv",
    ]
    policy_path = root / "dependency-compatibility-domains.json"
    _write(
        policy_path,
        json.dumps({
            "schema_version": 1,
            "domains": {"fixture-runtime": runtime, "fixture-tooling": tooling},
        }, indent=2) + "\n",
    )
    dependabot_path = root / ".github" / "dependabot.yml"
    original = dependabot_path.read_text(encoding="utf-8")
    all_packages = sorted(runtime + tooling)
    old = (
        "      fixture-dependencies:\n"
        f"        patterns: {json.dumps(all_packages)}\n"
    )
    new = (
        "      fixture-runtime:\n"
        f"        patterns: {json.dumps(runtime)}\n"
        "      fixture-tooling:\n"
        f"        patterns: {json.dumps(tooling)}\n"
    )
    assert old in original
    _write(dependabot_path, original.replace(old, new, 1))


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
    packages = [
        "backend",
        "core",
        "extra",
        "fastapi",
        "pip-audit",
        "pytest",
        "qdrant-client",
        "tqdm",
        "uv",
    ]
    _write(
        root / "dependency-compatibility-domains.json",
        json.dumps({
            "schema_version": 1,
            "domains": {"fixture-dependencies": packages},
        }, indent=2) + "\n",
    )
    _write(
        root / ".github" / "dependabot.yml",
        "version: 2\n"
        "updates:\n"
        "  - package-ecosystem: pip\n"
        "    directory: \"/\"\n"
        "    schedule:\n"
        "      interval: weekly\n"
        "      day: monday\n"
        "      time: \"08:00\"\n"
        "      timezone: America/Denver\n"
        "    open-pull-requests-limit: 10\n"
        "    groups:\n"
        "      fixture-dependencies:\n"
        f"        patterns: {json.dumps(packages)}\n"
        "    commit-message:\n"
        "      prefix: deps\n"
        "  - package-ecosystem: github-actions\n"
        "    directory: \"/\"\n"
        "    schedule:\n"
        "      interval: weekly\n"
        "      day: monday\n"
        "      time: \"08:30\"\n"
        "      timezone: America/Denver\n"
        "    open-pull-requests-limit: 5\n"
        "    groups:\n"
        "      github-actions:\n"
        "        patterns: [\"*\"]\n"
        "    commit-message:\n"
        "      prefix: ci\n",
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


def test_dependency_policy_rejects_sdks_for_owned_provider_transports(tmp_path):
    _valid_policy_tree(tmp_path)
    _write(
        tmp_path / "requirements-optional.txt",
        "extra>=2,<3\nopenai>=1,<3\n",
    )
    _write(
        tmp_path / "requirements-full.lock",
        f"core==1.5 --hash=sha256:{'0' * 64}\n"
        f"extra==2.5 --hash=sha256:{'0' * 64}\n"
        f"openai==2.0 --hash=sha256:{'0' * 64}\n",
    )

    errors = check_dependency_policy.validate(tmp_path)

    assert any(
        "openai is forbidden while the provider uses" in error
        for error in errors
    )
    assert any(
        "openai unexpectedly expands" in error
        for error in errors
    )


def test_repository_dependency_domains_cover_every_direct_input_exactly():
    root = Path(__file__).resolve().parents[1]

    assert check_dependency_policy.validate_dependency_domains(root) == []
    payload = json.loads(
        (root / "dependency-compatibility-domains.json").read_text(
            encoding="utf-8"
        )
    )
    assert payload["domains"] == EXPECTED_DEPENDENCY_DOMAINS
    packages = [
        package
        for domain_packages in payload["domains"].values()
        for package in domain_packages
    ]
    assert len(payload["domains"]) == 6
    assert len(packages) == len(set(packages)) == 26


def test_dependency_domains_reject_unassigned_direct_package(tmp_path):
    _valid_policy_tree(tmp_path)
    policy_path = tmp_path / "dependency-compatibility-domains.json"
    policy = json.loads(policy_path.read_text(encoding="utf-8"))
    policy["domains"]["fixture-dependencies"].remove("extra")
    _write(policy_path, json.dumps(policy, indent=2) + "\n")

    errors = check_dependency_policy.validate_dependency_domains(tmp_path)

    assert any("unassigned direct dependencies: extra" in error for error in errors)


def test_dependency_domains_reject_duplicate_assignment(tmp_path):
    _valid_policy_tree(tmp_path)
    policy_path = tmp_path / "dependency-compatibility-domains.json"
    policy = json.loads(policy_path.read_text(encoding="utf-8"))
    policy["domains"]["second-domain"] = ["core"]
    _write(policy_path, json.dumps(policy, indent=2) + "\n")

    errors = check_dependency_policy.validate_dependency_domains(tmp_path)

    assert any("package 'core' is assigned more than once" in error for error in errors)


def test_dependency_domains_reject_unknown_assignment(tmp_path):
    _valid_policy_tree(tmp_path)
    policy_path = tmp_path / "dependency-compatibility-domains.json"
    policy = json.loads(policy_path.read_text(encoding="utf-8"))
    policy["domains"]["fixture-dependencies"].append("unknown-package")
    _write(policy_path, json.dumps(policy, indent=2) + "\n")

    errors = check_dependency_policy.validate_dependency_domains(tmp_path)

    assert any(
        "unknown assigned dependencies: unknown-package" in error
        for error in errors
    )


def test_dependency_domains_reject_wildcard_dependabot_group(tmp_path):
    _valid_policy_tree(tmp_path)
    path = tmp_path / ".github" / "dependabot.yml"
    text = path.read_text(encoding="utf-8")
    start = text.index("        patterns:")
    end = text.index("\n", start)
    _write(path, text[:start] + '        patterns: ["*"]' + text[end:])

    errors = check_dependency_policy.validate_dependency_domains(tmp_path)

    assert any("patterns must be exact package names" in error for error in errors)


def test_dependency_domains_reject_malformed_policy(tmp_path):
    _valid_policy_tree(tmp_path)
    _write(tmp_path / "dependency-compatibility-domains.json", "{}\n")

    errors = check_dependency_policy.validate_dependency_domains(tmp_path)

    assert any("fields must be exactly" in error for error in errors)
    assert any("domains must be a non-empty object" in error for error in errors)


def test_dependency_domains_reject_noninteger_schema_versions(tmp_path):
    _valid_policy_tree(tmp_path)
    policy_path = tmp_path / "dependency-compatibility-domains.json"
    for invalid in (True, 1.0):
        policy = json.loads(policy_path.read_text(encoding="utf-8"))
        policy["schema_version"] = invalid
        _write(policy_path, json.dumps(policy, indent=2) + "\n")

        errors = check_dependency_policy.validate_dependency_domains(tmp_path)

        assert any("must be the integer 1" in error for error in errors)


def test_dependency_domains_reject_duplicate_json_keys(tmp_path):
    _valid_policy_tree(tmp_path)
    policy_path = tmp_path / "dependency-compatibility-domains.json"
    text = policy_path.read_text(encoding="utf-8")
    _write(
        policy_path,
        text.replace(
            '  "schema_version": 1,',
            '  "schema_version": 1,\n  "schema_version": 1,',
            1,
        ),
    )

    errors = check_dependency_policy.validate_dependency_domains(tmp_path)

    assert any("duplicate JSON key 'schema_version'" in error for error in errors)


def test_dependency_domains_reject_dependabot_policy_drift(tmp_path):
    _valid_policy_tree(tmp_path)
    path = tmp_path / ".github" / "dependabot.yml"
    _write(
        path,
        path.read_text(encoding="utf-8").replace('"extra"', '"surprise"'),
    )

    errors = check_dependency_policy.validate_dependency_domains(tmp_path)

    assert any("pip groups differ" in error for error in errors)


def test_dependency_domains_reject_second_flow_style_pip_update(tmp_path):
    _valid_policy_tree(tmp_path)
    path = tmp_path / ".github" / "dependabot.yml"
    _write(
        path,
        path.read_text(encoding="utf-8")
        + '  - {package-ecosystem: pip, directory: "/", '
        + 'target-branch: legacy, groups: {all: {patterns: ["*"]}}}\n',
    )

    errors = check_dependency_policy.validate_dependency_domains(tmp_path)

    assert any("unsupported YAML syntax" in error for error in errors)


def test_dependency_domains_reject_reordered_update_keys(tmp_path):
    _valid_policy_tree(tmp_path)
    path = tmp_path / ".github" / "dependabot.yml"
    text = path.read_text(encoding="utf-8")
    _write(
        path,
        text.replace(
            '  - package-ecosystem: pip\n    directory: "/"',
            '  - directory: "/"\n    package-ecosystem: pip',
            1,
        ),
    )

    errors = check_dependency_policy.validate_dependency_domains(tmp_path)

    assert any("canonical update item" in error for error in errors)


def test_dependency_domains_reject_malformed_nonpip_block(tmp_path):
    _valid_policy_tree(tmp_path)
    path = tmp_path / ".github" / "dependabot.yml"
    text = path.read_text(encoding="utf-8")
    _write(
        path,
        text.replace(
            '        patterns: ["*"]',
            '        patterns: ["*"',
            1,
        ),
    )

    errors = check_dependency_policy.validate_dependency_domains(tmp_path)

    assert any("inline JSON string list" in error for error in errors)


def test_dependency_domains_reject_undeclared_dependabot_controls(tmp_path):
    _valid_policy_tree(tmp_path)
    path = tmp_path / ".github" / "dependabot.yml"
    _write(
        path,
        path.read_text(encoding="utf-8").replace(
            '      time: "08:30"', '      time: "23:59"', 1,
        ),
    )

    errors = check_dependency_policy.validate_dependency_domains(tmp_path)

    assert any("github-actions schedule differs" in error for error in errors)


def test_dependency_domains_reject_quoted_pull_request_limit(tmp_path):
    _valid_policy_tree(tmp_path)
    path = tmp_path / ".github" / "dependabot.yml"
    _write(
        path,
        path.read_text(encoding="utf-8").replace(
            "    open-pull-requests-limit: 10",
            '    open-pull-requests-limit: "10"',
            1,
        ),
    )

    errors = check_dependency_policy.validate_dependency_domains(tmp_path)

    assert any("must be an unquoted positive integer" in error for error in errors)


def test_dependency_domains_require_one_pip_and_one_actions_block(tmp_path):
    _valid_policy_tree(tmp_path)
    path = tmp_path / ".github" / "dependabot.yml"
    original = path.read_text(encoding="utf-8")
    for replacement in ("uv", "pip"):
        _write(
            path,
            original.replace(
                "package-ecosystem: github-actions",
                f"package-ecosystem: {replacement}",
                1,
            ),
        )

        errors = check_dependency_policy.validate_dependency_domains(tmp_path)

        assert any("exactly one pip and one github-actions" in error for error in errors)


def test_dependency_domains_reject_duplicate_group_and_patterns(tmp_path):
    _valid_policy_tree(tmp_path)
    path = tmp_path / ".github" / "dependabot.yml"
    original = path.read_text(encoding="utf-8")
    pattern_line = (
        '        patterns: ["backend", "core", "extra", "fastapi", '
        '"pip-audit", "pytest", "qdrant-client", "tqdm", "uv"]'
    )
    _write(
        path,
        original.replace(pattern_line, pattern_line + "\n" + pattern_line, 1),
    )
    errors = check_dependency_policy.validate_dependency_domains(tmp_path)
    assert any("repeats patterns" in error for error in errors)

    _write(
        path,
        original.replace(
            pattern_line,
            pattern_line
            + "\n      fixture-dependencies:\n"
            + pattern_line,
            1,
        ),
    )
    errors = check_dependency_policy.validate_dependency_domains(tmp_path)
    assert any("duplicate group" in error for error in errors)


def test_dependency_domains_reject_missing_patterns_and_unknown_group_field(tmp_path):
    _valid_policy_tree(tmp_path)
    path = tmp_path / ".github" / "dependabot.yml"
    original = path.read_text(encoding="utf-8")
    pattern_line = (
        '        patterns: ["backend", "core", "extra", "fastapi", '
        '"pip-audit", "pytest", "qdrant-client", "tqdm", "uv"]'
    )
    _write(path, original.replace(pattern_line + "\n", "", 1))
    errors = check_dependency_policy.validate_dependency_domains(tmp_path)
    assert any("needs exactly one patterns field" in error for error in errors)

    _write(path, original.replace("        patterns:", "        update-types:", 1))
    errors = check_dependency_policy.validate_dependency_domains(tmp_path)
    assert any("may contain only patterns" in error for error in errors)


def test_dependency_domains_reject_invalid_names_and_sort_order(tmp_path):
    _valid_policy_tree(tmp_path)
    policy_path = tmp_path / "dependency-compatibility-domains.json"
    policy = json.loads(policy_path.read_text(encoding="utf-8"))
    policy["domains"] = {
        "Invalid Domain": policy["domains"].pop("fixture-dependencies")
    }
    _write(policy_path, json.dumps(policy, indent=2) + "\n")
    errors = check_dependency_policy.validate_dependency_domains(tmp_path)
    assert any("invalid domain name" in error for error in errors)

    _valid_policy_tree(tmp_path)
    policy = json.loads(policy_path.read_text(encoding="utf-8"))
    policy["domains"]["fixture-dependencies"] = list(
        reversed(policy["domains"]["fixture-dependencies"])
    )
    _write(policy_path, json.dumps(policy, indent=2) + "\n")
    errors = check_dependency_policy.validate_dependency_domains(tmp_path)
    assert any("canonical sort order" in error for error in errors)


def test_dependency_domain_diff_accepts_one_domain_with_lock_change(tmp_path):
    _valid_policy_tree(tmp_path)
    _split_fixture_domains(tmp_path)
    base = _commit_policy_tree(tmp_path, "base")
    _write(tmp_path / "requirements.txt", "core>=1.1,<2\n")
    _write(tmp_path / "requirements-optional.txt", "extra>=2.1,<3\n")
    core_lock = tmp_path / "requirements-core.lock"
    _write(
        core_lock,
        core_lock.read_text(encoding="utf-8").replace("core==1.5", "core==1.6"),
    )
    full_lock = tmp_path / "requirements-full.lock"
    _write(
        full_lock,
        full_lock.read_text(encoding="utf-8")
        .replace("core==1.5", "core==1.6")
        .replace("extra==2.5", "extra==2.6"),
    )
    _commit_policy_tree(tmp_path, "one runtime domain")

    assert check_dependency_policy.validate_dependency_domain_diff(
        base, tmp_path,
    ) == []


def test_dependency_domain_diff_accepts_policy_introduction(tmp_path):
    _valid_policy_tree(tmp_path)
    policy_path = tmp_path / "dependency-compatibility-domains.json"
    policy_path.unlink()
    base = _commit_policy_tree(tmp_path, "base without domain policy")
    _valid_policy_tree(tmp_path)
    _commit_policy_tree(tmp_path, "introduce domain policy")

    assert check_dependency_policy.validate_dependency_domain_diff(
        base, tmp_path,
    ) == []


def test_dependency_domain_diff_rejects_multiple_domains(tmp_path):
    _valid_policy_tree(tmp_path)
    _split_fixture_domains(tmp_path)
    base = _commit_policy_tree(tmp_path, "base")
    _write(tmp_path / "requirements.txt", "core>=1.1,<2\n")
    _write(tmp_path / "requirements-test.txt", "pytest==9.2.0\n")
    replacements = {
        "requirements-core.lock": ("core==1.5", "core==1.6"),
        "requirements-full.lock": ("core==1.5", "core==1.6"),
        "requirements-test.lock": ("pytest==9.1.1", "pytest==9.2.0"),
        "requirements-smoke.lock": ("pytest==9.1.1", "pytest==9.2.0"),
    }
    for filename, (before, after) in replacements.items():
        path = tmp_path / filename
        _write(path, path.read_text(encoding="utf-8").replace(before, after))
    _commit_policy_tree(tmp_path, "cross-domain change")

    errors = check_dependency_policy.validate_dependency_domain_diff(
        base, tmp_path,
    )

    assert any("span more than one" in error for error in errors)
    assert any("fixture-runtime" in error and "fixture-tooling" in error
               for error in errors)


def test_dependency_domain_diff_requires_changed_locks(tmp_path):
    _valid_policy_tree(tmp_path)
    _split_fixture_domains(tmp_path)
    base = _commit_policy_tree(tmp_path, "base")
    _write(tmp_path / "requirements.txt", "core>=1.1,<2\n")
    _commit_policy_tree(tmp_path, "bound without lock")

    errors = check_dependency_policy.validate_dependency_domain_diff(
        base, tmp_path,
    )

    assert any("missing required regenerated lockfile changes" in error
               for error in errors)
    assert any("requirements-core.lock" in error
               and "requirements-full.lock" in error for error in errors)


def test_dependency_domain_diff_rejects_unrelated_lock_change(tmp_path):
    _valid_policy_tree(tmp_path)
    _split_fixture_domains(tmp_path)
    base = _commit_policy_tree(tmp_path, "base")
    _write(tmp_path / "requirements.txt", "core>=1.1,<2\n")
    unrelated = tmp_path / "requirements-security.lock"
    _write(
        unrelated,
        unrelated.read_text(encoding="utf-8") + "# unrelated\n",
    )
    _commit_policy_tree(tmp_path, "wrong lock")

    errors = check_dependency_policy.validate_dependency_domain_diff(
        base, tmp_path,
    )

    assert any("requirements-core.lock" in error
               and "requirements-full.lock" in error for error in errors)


def test_dependency_domain_diff_rejects_noop_bound_with_transitive_locks(
    tmp_path,
):
    _valid_policy_tree(tmp_path)
    _split_fixture_domains(tmp_path)
    base = _commit_policy_tree(tmp_path, "base")
    _write(tmp_path / "requirements.txt", "core>=1.1,<2\n")
    transitive = f"transitive==9.9 --hash=sha256:{'1' * 64}\n"
    for filename in ("requirements-core.lock", "requirements-full.lock"):
        path = tmp_path / filename
        _write(path, path.read_text(encoding="utf-8") + transitive)
    _commit_policy_tree(tmp_path, "no-op bound with transitive lock changes")

    errors = check_dependency_policy.validate_dependency_domain_diff(
        base, tmp_path,
    )

    assert any("selected records unchanged in every mapped lock: core" in error
               for error in errors)


def test_dependency_domain_diff_rejects_mode_only_required_locks(tmp_path):
    _valid_policy_tree(tmp_path)
    _split_fixture_domains(tmp_path)
    base = _commit_policy_tree(tmp_path, "base")
    _write(tmp_path / "requirements.txt", "core>=1.1,<2\n")
    _git(tmp_path, "add", ".")
    for filename in ("requirements-core.lock", "requirements-full.lock"):
        _git(tmp_path, "update-index", "--chmod=+x", filename)
    _git(tmp_path, "commit", "-m", "mode-only lock changes")

    for filename in ("requirements-core.lock", "requirements-full.lock"):
        assert _git(tmp_path, "rev-parse", f"{base}:{filename}") == _git(
            tmp_path, "rev-parse", f"HEAD:{filename}",
        )
        assert _git(tmp_path, "ls-tree", base, "--", filename).startswith(
            "100644 "
        )
        assert _git(tmp_path, "ls-tree", "HEAD", "--", filename).startswith(
            "100755 "
        )

    errors = check_dependency_policy.validate_dependency_domain_diff(
        base, tmp_path,
    )

    assert any("missing required regenerated lockfile changes" in error
               for error in errors)
    assert any("requirements-core.lock" in error
               and "requirements-full.lock" in error for error in errors)


def test_dependency_domain_diff_accepts_lock_only_same_domain(tmp_path):
    _valid_policy_tree(tmp_path)
    _split_fixture_domains(tmp_path)
    base = _commit_policy_tree(tmp_path, "base")
    for filename in ("requirements-core.lock", "requirements-full.lock"):
        path = tmp_path / filename
        _write(
            path,
            path.read_text(encoding="utf-8").replace("core==1.5", "core==1.6"),
        )
    _commit_policy_tree(tmp_path, "lock-only runtime update")

    assert check_dependency_policy.validate_dependency_domain_diff(
        base, tmp_path,
    ) == []


def test_dependency_domain_diff_rejects_lock_only_cross_domain(tmp_path):
    _valid_policy_tree(tmp_path)
    _split_fixture_domains(tmp_path)
    base = _commit_policy_tree(tmp_path, "base")
    replacements = {
        "requirements-core.lock": ("core==1.5", "core==1.6"),
        "requirements-full.lock": ("core==1.5", "core==1.6"),
        "requirements-test.lock": ("pytest==9.1.1", "pytest==9.2.0"),
        "requirements-smoke.lock": ("pytest==9.1.1", "pytest==9.2.0"),
    }
    for filename, (before, after) in replacements.items():
        path = tmp_path / filename
        _write(path, path.read_text(encoding="utf-8").replace(before, after))
    _commit_policy_tree(tmp_path, "lock-only cross-domain update")

    errors = check_dependency_policy.validate_dependency_domain_diff(
        base, tmp_path,
    )

    assert any("span more than one" in error for error in errors)
    assert any("fixture-runtime" in error and "fixture-tooling" in error
               for error in errors)


def test_dependency_domain_diff_rejects_transitive_only_lock_change(tmp_path):
    _valid_policy_tree(tmp_path)
    _split_fixture_domains(tmp_path)
    base = _commit_policy_tree(tmp_path, "base")
    path = tmp_path / "requirements-core.lock"
    _write(
        path,
        path.read_text(encoding="utf-8")
        + f"transitive==9.9 --hash=sha256:{'1' * 64}\n",
    )
    _commit_policy_tree(tmp_path, "transitive-only lock change")

    errors = check_dependency_policy.validate_dependency_domain_diff(
        base, tmp_path,
    )

    assert any("not attributable to a changed governed direct dependency"
               in error for error in errors)


def test_dependency_domain_diff_rejects_comment_only_lock_change(tmp_path):
    _valid_policy_tree(tmp_path)
    _split_fixture_domains(tmp_path)
    base = _commit_policy_tree(tmp_path, "base")
    path = tmp_path / "requirements-core.lock"
    _write(
        path,
        path.read_text(encoding="utf-8") + "# comment-only lock change\n",
    )
    _commit_policy_tree(tmp_path, "comment-only lock change")

    errors = check_dependency_policy.validate_dependency_domain_diff(
        base, tmp_path,
    )

    assert any("not attributable to a changed governed direct dependency"
               in error for error in errors)


def test_dependency_domain_diff_requires_exact_base_sha(tmp_path):
    _valid_policy_tree(tmp_path)

    errors = check_dependency_policy.validate_dependency_domain_diff(
        "main", tmp_path,
    )

    assert errors == [
        "--base-ref must be one exact lowercase 40-character commit SHA"
    ]


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


def test_lock_refresh_upgrade_package_passthrough():
    commands = refresh_locks.lock_commands(
        "uv", upgrade_packages=("aiohttp", "cryptography", "h2"))
    for command in commands:
        tail = command[command.index("--output-file") + 2:]
        assert tail == [
            "--upgrade-package", "aiohttp",
            "--upgrade-package", "cryptography",
            "--upgrade-package", "h2",
        ]
        assert "--upgrade" not in command


def test_lock_refresh_rejects_upgrade_flag_combination(capsys):
    assert refresh_locks.main(
        ["--upgrade", "--upgrade-package", "aiohttp"]) == 2
    assert "--upgrade-package" in capsys.readouterr().err


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
