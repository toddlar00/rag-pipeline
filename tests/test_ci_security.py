import json
from pathlib import Path

from tools import check_ci_security


_CHECKOUT_SHA = "3d3c42e5aac5ba805825da76410c181273ba90b1"


def _write(path: Path, content: str) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(content, encoding="utf-8")


def _policy() -> dict:
    return json.loads(
        (check_ci_security.PROJECT_ROOT / check_ci_security.POLICY_PATH)
        .read_text(encoding="utf-8")
    )


def _workflow(paths: list[str]) -> str:
    rendered_paths = "\n".join(f'      - "{path}"' for path in paths)
    return f"""name: Security fixture

on:
  pull_request:
    paths:
{rendered_paths}
  push:
    paths:
{rendered_paths}

permissions:
  contents: read

jobs:
  test:
    runs-on: ubuntu-latest
    steps:
      - name: Check out repository
        uses: actions/checkout@{_CHECKOUT_SHA}
        with:
          persist-credentials: false
"""


def _valid_tree(root: Path) -> tuple[dict, Path]:
    policy = _policy()
    _write(
        root / check_ci_security.POLICY_PATH,
        json.dumps(policy, indent=2) + "\n",
    )
    current = {
        path
        for paths in policy["current_implementation_owners"].values()
        for path in paths
    }
    governance = set(policy["governance_paths"])
    for relative in current | governance:
        _write(root / relative, "")
    declared = sorted(
        current
        | governance
        | {
            path
            for paths in policy["reserved_implementation_owners"].values()
            for path in paths
        }
    )
    workflow_path = root / check_ci_security.SECURITY_WORKFLOW_PATH
    _write(workflow_path, _workflow(declared))
    return policy, workflow_path


def test_repository_workflows_satisfy_ci_security_policy():
    assert check_ci_security.validate() == []


def test_llm_output_contract_owner_cannot_be_removed(tmp_path):
    policy, _workflow_path = _valid_tree(tmp_path)
    policy["current_implementation_owners"]["provider_runtime"].remove(
        "llm_output_contracts.py")
    _write(
        tmp_path / check_ci_security.POLICY_PATH,
        json.dumps(policy, indent=2) + "\n",
    )

    errors = check_ci_security.validate(tmp_path)

    assert any(
        "missing required current owner: llm_output_contracts.py" in error
        for error in errors
    )


def test_checkout_must_disable_persisted_credentials(tmp_path):
    _policy_data, workflow_path = _valid_tree(tmp_path)
    workflow_path.write_text(
        workflow_path.read_text(encoding="utf-8").replace(
            "persist-credentials: false", "persist-credentials: true"
        ),
        encoding="utf-8",
    )

    errors = check_ci_security.validate(tmp_path)

    assert any(
        "actions/checkout must set persist-credentials: false" in error
        for error in errors
    )


def test_compact_checkout_steps_are_hardened_too(tmp_path):
    _policy_data, workflow_path = _valid_tree(tmp_path)
    workflow_path.write_text(
        workflow_path.read_text(encoding="utf-8").replace(
            "      - name: Check out repository\n"
            f"        uses: actions/checkout@{_CHECKOUT_SHA}\n"
            "        with:\n"
            "          persist-credentials: false",
            f"      - uses: actions/checkout@{_CHECKOUT_SHA}\n"
            "        with:\n"
            "          persist-credentials: true",
        ),
        encoding="utf-8",
    )

    errors = check_ci_security.validate(tmp_path)

    assert any(
        "actions/checkout must set persist-credentials: false" in error
        for error in errors
    )


def test_all_declared_current_and_future_owners_need_both_filters(tmp_path):
    policy, workflow_path = _valid_tree(tmp_path)
    policy["reserved_implementation_owners"]["future_provider"] = [
        "future_provider_runtime.py"
    ]
    _write(
        tmp_path / check_ci_security.POLICY_PATH,
        json.dumps(policy, indent=2) + "\n",
    )

    errors = check_ci_security.validate(tmp_path)

    assert any(
        "pull_request.paths does not cover declared security owner "
        "future_provider_runtime.py" in error
        for error in errors
    )
    assert any(
        "push.paths does not cover declared security owner "
        "future_provider_runtime.py" in error
        for error in errors
    )
    workflow_path.write_text(
        workflow_path.read_text(encoding="utf-8").replace(
            '      - "vector_lifecycle.py"',
            '      - "vector_lifecycle.py"\n'
            '      - "future_provider_runtime.py"',
        ),
        encoding="utf-8",
    )
    assert check_ci_security.validate(tmp_path) == []


def test_current_owner_declarations_must_resolve_to_files(tmp_path):
    policy, _workflow_path = _valid_tree(tmp_path)
    policy["current_implementation_owners"]["provider_runtime"].append(
        "missing_provider.py"
    )
    _write(
        tmp_path / check_ci_security.POLICY_PATH,
        json.dumps(policy, indent=2) + "\n",
    )

    errors = check_ci_security.validate(tmp_path)

    assert any(
        "declared current path is missing: missing_provider.py" in error
        for error in errors
    )


def test_actions_stay_sha_pinned_and_permissions_stay_read_only(tmp_path):
    _policy_data, workflow_path = _valid_tree(tmp_path)
    workflow_path.write_text(
        workflow_path.read_text(encoding="utf-8")
        .replace(f"actions/checkout@{_CHECKOUT_SHA}", "actions/checkout@main")
        .replace("contents: read", "contents: write"),
        encoding="utf-8",
    )

    errors = check_ci_security.validate(tmp_path)

    assert any("40-character commit SHA" in error for error in errors)
    assert any(
        "permissions must be exactly 'contents: read'" in error
        for error in errors
    )


def test_job_level_permissions_cannot_override_workflow_policy(tmp_path):
    _policy_data, workflow_path = _valid_tree(tmp_path)
    workflow_path.write_text(
        workflow_path.read_text(encoding="utf-8").replace(
            "  test:\n    runs-on:",
            "  test:\n    permissions: write-all\n    runs-on:",
        ),
        encoding="utf-8",
    )

    errors = check_ci_security.validate(tmp_path)

    assert any(
        "require one top-level permissions mapping and no job-level overrides"
        in error
        for error in errors
    )
