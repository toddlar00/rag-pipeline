import hashlib
import json
import os
from pathlib import Path
import subprocess
import sys
import textwrap

import pytest

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


def _ci_workflow_text() -> str:
    return (
        check_ci_security.PROJECT_ROOT / check_ci_security.CI_WORKFLOW_PATH
    ).read_text(encoding="utf-8")


def _active_ci_workflow_text() -> str:
    return (
        check_ci_security.PROJECT_ROOT
        / check_ci_security.ACTIVE_CI_FIXTURE_PATH
    ).read_text(encoding="utf-8")


def _bootstrap_ci_workflow_text() -> str:
    return check_ci_security.render_force_full_bootstrap(
        _active_ci_workflow_text()
    )


def _ci_promotion_tool_text() -> str:
    return (
        check_ci_security.PROJECT_ROOT
        / check_ci_security.CI_PROMOTION_TOOL_PATH
    ).read_text(encoding="utf-8")


def _promotion_validator_source() -> str:
    raw_lines = _active_ci_workflow_text().splitlines()
    lines = check_ci_security._structural_workflow_lines(raw_lines)
    errors: list[str] = []
    jobs = check_ci_security._job_ranges(
        lines, check_ci_security.CI_WORKFLOW_PATH, errors
    )
    assert errors == []
    assert "promotion-gate" in jobs
    step = check_ci_security._named_step_range(
        lines,
        jobs["promotion-gate"],
        "Validate the exact promotion decision and every job result",
    )
    assert step is not None
    starts = [
        index for index in range(step[0], step[1])
        if raw_lines[index].strip() == "python - <<'PY'"
    ]
    assert len(starts) == 1
    ends = [
        index for index in range(starts[0] + 1, step[1])
        if raw_lines[index].strip() == "PY"
    ]
    assert len(ends) == 1
    return textwrap.dedent(
        "\n".join(raw_lines[starts[0] + 1:ends[0]]) + "\n"
    )


def _promotion_validator_environment(
    summary_path: Path,
    *,
    groups: list[str],
    full: bool,
    head_ref: str = "docs-update",
) -> dict[str, str]:
    always = {"quality", "evaluation-smoke", "unit-linux"}
    conditional = {
        "unit-windows",
        "service-api",
        "phase-a0",
        "vector-store-smoke",
        "full-integration",
    }
    governed = always | conditional
    requirements = {
        job: "required" if full or job in always else "not_required"
        for job in governed
    }
    pythons = (
        ["3.10", "3.11", "3.12", "3.13", "3.14"]
        if full else ["3.12"]
    )
    reason_codes = (
        ["non_documentation_change"]
        if groups != ["documentation_only"]
        else ["documentation_only"]
    )
    base_sha = "a" * 40
    head_sha = "b" * 40
    candidate_sha = "c" * 40
    policy_sha256 = "d" * 64
    decision = {
        "base_sha": base_sha,
        "candidate_sha": candidate_sha,
        "change_set_sha256": "e" * 64,
        "changed_count": 1,
        "classifier_ok": True,
        "event_name": "pull_request",
        "forced_heavy": False,
        "groups": groups,
        "head_sha": head_sha,
        "policy_sha256": policy_sha256,
        "pythons": pythons,
        "reason_codes": reason_codes,
        "requirements": requirements,
        "schema_version": 2,
        "trusted_sha": base_sha,
    }
    def canonical(value: object) -> str:
        return json.dumps(
            value,
            ensure_ascii=True,
            sort_keys=True,
            separators=(",", ":"),
        )
    decision_sha256 = hashlib.sha256(
        canonical(decision).encode("ascii")
    ).hexdigest()
    decision["decision_sha256"] = decision_sha256
    outputs = {
        "base_sha": base_sha,
        "candidate_sha": candidate_sha,
        "changed_count": "1",
        "classifier_ok": "true",
        "decision": canonical(decision),
        "decision_sha256": decision_sha256,
        "groups": canonical(groups),
        "head_sha": head_sha,
        "policy_sha256": policy_sha256,
        "pythons": canonical(pythons),
        "requirements": canonical(requirements),
        "trusted_sha": base_sha,
    }
    for job in conditional:
        outputs[f"require_{job.replace('-', '_')}"] = (
            "true" if full else "false"
        )
    needs = {
        "lane": {"outputs": outputs, "result": "success"},
        **{
            job: {
                "outputs": {},
                "result": (
                    "success"
                    if requirements[job] == "required"
                    else "skipped"
                ),
            }
            for job in governed
        },
    }
    return {
        "EXPECTED_BASE_SHA": base_sha,
        "EXPECTED_CANDIDATE_SHA": candidate_sha,
        "EXPECTED_EVENT_NAME": "pull_request",
        "EXPECTED_HEAD_REF": head_ref,
        "EXPECTED_HEAD_SHA": head_sha,
        "EXPECTED_TRUSTED_SHA": base_sha,
        "GITHUB_STEP_SUMMARY": str(summary_path),
        "NEEDS_JSON": canonical(needs),
    }


def _run_promotion_validator(
    tmp_path: Path, environment: dict[str, str],
) -> subprocess.CompletedProcess[str]:
    return subprocess.run(
        [sys.executable, "-c", _promotion_validator_source()],
        cwd=tmp_path,
        env={**os.environ, **environment},
        stdin=subprocess.DEVNULL,
        stdout=subprocess.PIPE,
        stderr=subprocess.PIPE,
        text=True,
        timeout=10,
        check=False,
    )


def _mutated_ci(old: str, new: str, *, count: int = 1) -> str:
    text = _active_ci_workflow_text()
    assert text.count(old) == count
    return text.replace(old, new, 1)


def _workflow(paths: list[str]) -> str:
    rendered_paths = "\n".join(f'      - "{path}"' for path in paths)
    return f"""name: Security fixture

on:
  pull_request:
    paths:
{rendered_paths}
  merge_group:
    types: [checks_requested]
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
    declared = sorted(
        current
        | governance
        | check_ci_security._REQUIRED_SECURITY_FILTERS
        | {
            path
            for paths in policy["reserved_implementation_owners"].values()
            for path in paths
        }
    )
    workflow_governance = {
        path for path in governance
        if path.startswith(".github/workflows/")
    }
    for relative in (current | governance) - {
        check_ci_security.POLICY_PATH,
        *workflow_governance,
    }:
        if relative == check_ci_security.CI_PROMOTION_TOOL_PATH:
            content = _ci_promotion_tool_text()
        elif relative == check_ci_security.ACTIVE_CI_FIXTURE_PATH:
            content = _active_ci_workflow_text()
        else:
            content = ""
        _write(root / relative, content)
    for relative in workflow_governance:
        content = (
            (check_ci_security.PROJECT_ROOT / relative).read_text(
                encoding="utf-8")
            if relative == check_ci_security.CI_WORKFLOW_PATH
            else _workflow(declared)
        )
        _write(root / relative, content)
    workflow_path = root / check_ci_security.SECURITY_WORKFLOW_PATH
    return policy, workflow_path


def test_repository_workflows_satisfy_ci_security_policy():
    assert check_ci_security.validate() == []


def test_repository_policy_owns_ci_promotion_boundary():
    policy, errors = check_ci_security.load_ownership_policy(
        check_ci_security.PROJECT_ROOT)

    assert errors == []
    assert policy is not None
    assert policy.current_paths == check_ci_security._REQUIRED_CURRENT_OWNERS
    assert check_ci_security._REQUIRED_GOVERNANCE_PATHS <= (
        policy.governance_paths)

    workflow_text = (
        check_ci_security.PROJECT_ROOT
        / check_ci_security.SECURITY_WORKFLOW_PATH
    ).read_text(encoding="utf-8")
    pull_request_paths, pull_request_errors = check_ci_security.event_paths(
        workflow_text, "pull_request")
    push_paths, push_errors = check_ci_security.event_paths(
        workflow_text, "push")
    assert pull_request_errors == []
    assert push_errors == []
    assert pull_request_paths == push_paths
    assert check_ci_security._REQUIRED_GOVERNANCE_PATHS <= pull_request_paths
    assert check_ci_security._REQUIRED_SECURITY_FILTERS <= pull_request_paths


def test_repository_ci_topology_is_fail_closed():
    assert check_ci_security.validate_ci_topology(_ci_workflow_text()) == []


def test_reviewed_active_fixture_preserves_both_rollout_forms():
    active = _active_ci_workflow_text()
    bootstrap = check_ci_security.render_force_full_bootstrap(active)
    actual = check_ci_security._normalized_workflow_text(_ci_workflow_text())

    assert check_ci_security._workflow_sha256(active) == (
        check_ci_security._ACTIVE_WORKFLOW_SHA256
    )
    assert check_ci_security._workflow_sha256(bootstrap) == (
        check_ci_security._BOOTSTRAP_WORKFLOW_SHA256
    )
    assert check_ci_security.validate_workflow(
        check_ci_security.ACTIVE_CI_FIXTURE_PATH, active
    ) == []
    assert check_ci_security.validate_ci_topology(active) == []
    assert check_ci_security.validate_ci_topology(bootstrap) == []
    assert actual in {
        check_ci_security._normalized_workflow_text(active),
        check_ci_security._normalized_workflow_text(bootstrap),
    }


def test_repository_validator_accepts_fixture_backed_seed_form(tmp_path):
    _policy_data, _workflow_path = _valid_tree(tmp_path)
    ci_path = tmp_path / check_ci_security.CI_WORKFLOW_PATH
    ci_path.write_text(_bootstrap_ci_workflow_text(), encoding="utf-8")

    assert check_ci_security.validate(tmp_path) == []


def test_repository_validator_accepts_fixture_backed_active_form(tmp_path):
    _policy_data, _workflow_path = _valid_tree(tmp_path)
    ci_path = tmp_path / check_ci_security.CI_WORKFLOW_PATH
    ci_path.write_text(_active_ci_workflow_text(), encoding="utf-8")

    assert check_ci_security.validate(tmp_path) == []


def test_repository_validator_rejects_live_third_form(tmp_path):
    _policy_data, _workflow_path = _valid_tree(tmp_path)
    ci_path = tmp_path / check_ci_security.CI_WORKFLOW_PATH
    ci_path.write_text(
        _active_ci_workflow_text() + "# unreviewed third form\n",
        encoding="utf-8",
    )

    errors = check_ci_security.validate(tmp_path)

    assert any("active CI differs from the reviewed exact workflow" in error
               for error in errors)
    assert any(
        "workflow must exactly match the reviewed active fixture or its "
        "force-full bootstrap rendering" in error
        for error in errors
    )


def test_repository_validator_rejects_changed_active_fixture(tmp_path):
    _policy_data, _workflow_path = _valid_tree(tmp_path)
    fixture_path = tmp_path / check_ci_security.ACTIVE_CI_FIXTURE_PATH
    fixture_path.write_text(
        fixture_path.read_text(encoding="utf-8").replace(
            "name: CI\n", "name: Unreviewed CI\n", 1
        ),
        encoding="utf-8",
    )

    errors = check_ci_security.validate(tmp_path)

    assert any(
        f"{check_ci_security.ACTIVE_CI_FIXTURE_PATH}: fixture differs"
        in error
        for error in errors
    )


def test_reviewed_seed_topology_forces_every_heavy_job_without_head_code():
    bootstrap = _bootstrap_ci_workflow_text()

    assert check_ci_security.validate_ci_topology(bootstrap) == []
    assert "ci_promotion.py" not in bootstrap
    assert "promotion-gate:" not in bootstrap

    weakened = bootstrap.replace(
        "echo 'require_phase_a0=true'",
        "echo 'require_phase_a0=false'",
        1,
    )
    errors = check_ci_security.validate_ci_topology(weakened)
    assert any("exact reviewed seed contract" in error for error in errors)


@pytest.mark.parametrize(("old", "new"), (
    (
        "concurrency:\n",
        "defaults:\n  run:\n    shell: python\n\nconcurrency:\n",
    ),
    (
        '  PYTHONUTF8: "1"\n',
        '  PYTHONUTF8: "1"\n  PATH: /tmp/untrusted-bin\n',
    ),
))
def test_ci_topology_rejects_unreviewed_top_level_execution_defaults(
    old, new,
):
    text = _mutated_ci(old, new)

    errors = check_ci_security.validate_ci_topology(text)

    assert any("reviewed exact workflow" in error for error in errors)


def test_active_topology_rejects_unreviewed_execution_step_change():
    text = _mutated_ci(
        "        run: python tools/check_dependency_policy.py\n",
        "        run: python -c \"print('dependency gate bypassed')\"\n",
    )

    errors = check_ci_security.validate_ci_topology(text)

    assert any("active CI differs from the reviewed exact workflow" in error
               for error in errors)


def test_seed_topology_rejects_unreviewed_execution_step_change():
    bootstrap = _bootstrap_ci_workflow_text()
    marker = "        run: python tools/check_dependency_policy.py\n"
    assert bootstrap.count(marker) == 1
    weakened = bootstrap.replace(
        marker,
        "        run: python -c \"print('dependency gate bypassed')\"\n",
        1,
    )

    errors = check_ci_security.validate_ci_topology(weakened)

    assert any("force-full bootstrap differs from the reviewed exact workflow"
               in error for error in errors)


@pytest.mark.parametrize("job", ("quality", "evaluation-smoke"))
def test_seed_topology_cannot_skip_an_always_job(job):
    bootstrap = _bootstrap_ci_workflow_text()
    marker = f"  {job}:\n"
    assert bootstrap.count(marker) == 1
    weakened = bootstrap.replace(
        marker,
        marker + "    if: ${{ false }}\n",
        1,
    )

    errors = check_ci_security.validate_ci_topology(weakened)

    assert any(
        f"bootstrap {job} must not be conditional" in error
        for error in errors
    )


@pytest.mark.parametrize("injection", (
    "        exclude:\n          - python-version: '3.10'\n",
    "        python-version: []\n",
))
def test_seed_topology_rejects_matrix_exclusions_and_overrides(injection):
    bootstrap = _bootstrap_ci_workflow_text()
    marker = (
        "        python-version: ${{ fromJSON(needs.lane.outputs.pythons || "
        "'[\"3.10\",\"3.11\",\"3.12\",\"3.13\",\"3.14\"]') }}\n"
    )
    assert bootstrap.count(marker) == 1
    weakened = bootstrap.replace(marker, marker + injection, 1)

    errors = check_ci_security.validate_ci_topology(weakened)

    assert any(
        "bootstrap unit-linux matrix must match" in error for error in errors
    )


def test_active_topology_rejects_partial_matrix_exclusion():
    marker = "        os: [ubuntu-24.04, windows-latest]\n"
    text = _mutated_ci(
        marker,
        marker + "        exclude:\n          - os: windows-latest\n",
        count=2,
    )

    errors = check_ci_security.validate_ci_topology(text)

    assert any("matrix must match the exact reviewed cells" in error
               for error in errors)


def test_repository_ci_promotion_tool_matches_workflow_contract():
    assert check_ci_security.validate_ci_promotion_contract(
        _ci_promotion_tool_text()
    ) == []


@pytest.mark.parametrize(("groups", "full"), (
    (["documentation_only"], False),
    (["security"], True),
))
def test_promotion_validator_executes_canonical_decision_contract(
    tmp_path, groups, full,
):
    environment = _promotion_validator_environment(
        tmp_path / "summary.md", groups=groups, full=full
    )

    result = _run_promotion_validator(tmp_path, environment)

    assert result.returncode == 0, result.stderr
    assert "CI promotion accepted" in result.stdout


def test_promotion_validator_rejects_digest_valid_security_fast_decision(
    tmp_path,
):
    environment = _promotion_validator_environment(
        tmp_path / "summary.md", groups=["security"], full=False
    )

    result = _run_promotion_validator(tmp_path, environment)

    assert result.returncode == 1
    assert "requirements are inconsistent with risk groups" in result.stderr


def test_promotion_validator_rejects_unbound_candidate_branch_force(
    tmp_path,
):
    environment = _promotion_validator_environment(
        tmp_path / "summary.md",
        groups=["documentation_only"],
        full=False,
        head_ref="release-candidate",
    )

    result = _run_promotion_validator(tmp_path, environment)

    assert result.returncode == 1
    assert "force reason disagrees with the event" in result.stderr


@pytest.mark.parametrize("workflow_path", (
    check_ci_security.DEPENDENCY_WORKFLOW_PATH,
    check_ci_security.SECURITY_WORKFLOW_PATH,
))
def test_promotion_side_workflows_cover_merge_queue_candidate(workflow_path):
    text = (check_ci_security.PROJECT_ROOT / workflow_path).read_text(
        encoding="utf-8"
    )
    assert check_ci_security.validate_merge_group_trigger(
        workflow_path, text
    ) == []
    weakened = text.replace(
        "  merge_group:\n    types: [checks_requested]\n", "", 1
    )
    errors = check_ci_security.validate_merge_group_trigger(
        workflow_path, weakened
    )
    assert any("merge_group checks_requested" in error for error in errors)


def test_ci_topology_rejects_pull_request_target():
    text = _mutated_ci(
        "  pull_request:\n",
        "  pull_request_target:\n",
    )

    errors = check_ci_security.validate_ci_topology(text)

    assert any("pull_request_target is forbidden" in error for error in errors)


@pytest.mark.parametrize(("old", "new"), (
    (
        "  pull_request:\n  merge_group:\n",
        "  pull_request:\n    paths: [\"**\"]\n  merge_group:\n",
    ),
    (
        "    types: [checks_requested]\n",
        "    types: [opened]\n",
    ),
    (
        "    branches: [main]\n",
        "    branches: [development]\n",
    ),
    (
        "  workflow_dispatch:\n\npermissions:\n",
        "  workflow_dispatch:\n"
        "    inputs:\n"
        "      weaken:\n"
        "        required: false\n\n"
        "permissions:\n",
    ),
    (
        "  workflow_dispatch:\n",
        "  schedule:\n"
        "    - cron: '0 0 * * *'\n"
        "  workflow_dispatch:\n",
    ),
))
def test_ci_topology_requires_exact_trigger_contract(old, new):
    text = _mutated_ci(old, new)

    errors = check_ci_security.validate_ci_topology(text)

    assert any("CI triggers must" in error for error in errors)


def test_ci_topology_requires_quality_checker_invocation():
    text = _mutated_ci(
        "        run: python tools/check_ci_security.py\n",
        "        run: python -c \"print('checker omitted')\"\n",
    )

    errors = check_ci_security.validate_ci_topology(text)

    assert any("quality must invoke" in error for error in errors)


def test_ci_topology_requires_stable_lane_outputs():
    text = _mutated_ci(
        "      base_sha: ${{ steps.classify.outputs.base_sha }}\n",
        "      base_sha: ${{ steps.event.outputs.base_sha }}\n",
    )

    errors = check_ci_security.validate_ci_topology(text)

    assert any("lane outputs must match" in error for error in errors)


@pytest.mark.parametrize(("old", "new"), (
    (
        'classify_parser.add_argument("--candidate-sha", required=True)',
        'classify_parser.add_argument("--candidate-sha")',
    ),
    ("DECISION_SCHEMA_VERSION = 2", "DECISION_SCHEMA_VERSION = 1"),
    ('("candidate_sha", decision.candidate_sha),', ""),
))
def test_ci_promotion_tool_contract_rejects_candidate_drift(old, new):
    text = _ci_promotion_tool_text()
    assert text.count(old) == 1

    errors = check_ci_security.validate_ci_promotion_contract(
        text.replace(old, new, 1)
    )

    assert errors


def test_ci_promotion_tool_contract_requires_replace_ref_suppression():
    text = _ci_promotion_tool_text()
    assert text.count('"--no-replace-objects",') >= 1
    weakened = text.replace('"--no-replace-objects",', '"--paginate",')

    errors = check_ci_security.validate_ci_promotion_contract(weakened)

    assert any("--no-replace-objects" in error for error in errors)


def test_ci_topology_rejects_head_classifier_execution():
    text = _mutated_ci(
        '          python -I "$trusted_dir/tools/ci_promotion.py" classify \\\n',
        "          python tools/ci_promotion.py classify \\\n",
    )

    errors = check_ci_security.validate_ci_topology(text)

    assert any("classifier may execute only" in error for error in errors)


def test_ci_topology_rejects_extra_preclassifier_git_state_step():
    marker = "      - name: Run the exact trusted classifier\n"
    text = _mutated_ci(
        marker,
        "      - name: Mutate Git state\n"
        "        run: git replace HEAD HEAD^\n"
        + marker,
    )

    errors = check_ci_security.validate_ci_topology(text)

    assert any("active classifier lane differs" in error for error in errors)


def test_ci_topology_requires_main_only_manual_dispatch():
    text = _mutated_ci(
        '              if github_ref != "refs/heads/main":\n',
        "              if not github_ref:\n",
    )

    errors = check_ci_security.validate_ci_topology(text)

    assert any("event identity step" in error for error in errors)


def test_ci_topology_rejects_continue_on_error():
    text = _mutated_ci(
        "  quality:\n    name: Quality gates\n",
        "  quality:\n    name: Quality gates\n"
        "    continue-on-error: true\n",
    )

    errors = check_ci_security.validate_ci_topology(text)

    assert any("continue-on-error is forbidden" in error for error in errors)


def test_ci_topology_rejects_wrong_heavy_guard():
    text = _mutated_ci(
        "    if: ${{ always() && "
        "needs.lane.outputs.require_unit_windows != 'false' }}\n",
        "    if: ${{ needs.lane.outputs.require_unit_windows == 'true' }}\n",
    )

    errors = check_ci_security.validate_ci_topology(text)

    assert any("unit-windows must use" in error for error in errors)


def test_ci_topology_requires_unit_linux_matrix_fallback():
    text = _mutated_ci(
        "        python-version: ${{ fromJSON(needs.lane.outputs.pythons || "
        "'[\"3.10\",\"3.11\",\"3.12\",\"3.13\",\"3.14\"]') }}\n",
        "        python-version: ${{ fromJSON(needs.lane.outputs.pythons) }}\n",
    )

    errors = check_ci_security.validate_ci_topology(text)

    assert any("dynamic Python matrix" in error for error in errors)


def test_ci_topology_requires_matrix_fail_fast_false():
    text = _mutated_ci(
        "      fail-fast: false\n",
        "      fail-fast: true\n",
        count=4,
    )

    errors = check_ci_security.validate_ci_topology(text)

    assert any("matrix must set fail-fast: false" in error for error in errors)


def test_ci_topology_requires_exact_candidate_checkout_ref():
    ref_line = "          ref: ${{ github.sha }}\n"
    text = _mutated_ci(ref_line, "", count=8)

    errors = check_ci_security.validate_ci_topology(text)

    assert any("quality checkout must have one exact" in error
               for error in errors)


def test_ci_topology_rejects_fork_repository_override():
    ref_line = "          ref: ${{ github.sha }}\n"
    text = _mutated_ci(
        ref_line,
        ref_line
        + "          repository: "
        + "${{ github.event.pull_request.head.repo.full_name }}\n",
        count=8,
    )

    errors = check_ci_security.validate_ci_topology(text)

    assert any("quality checkout must have one exact" in error
               for error in errors)


def test_ci_topology_rejects_duplicate_candidate_ref_key():
    ref_line = "          ref: ${{ github.sha }}\n"
    text = _mutated_ci(
        ref_line,
        ref_line
        + "          ref: ${{ github.event.pull_request.head.sha }}\n",
        count=8,
    )

    errors = check_ci_security.validate_ci_topology(text)

    assert any("quality checkout must have one exact" in error
               for error in errors)


def test_ci_topology_rejects_new_job_absent_from_aggregate():
    text = _active_ci_workflow_text() + (
        "\n  ungoverned-job:\n"
        "    name: Ungoverned job\n"
        "    runs-on: ubuntu-24.04\n"
        "    steps:\n"
        "      - name: No-op\n"
        "        run: echo no-op\n"
    )

    errors = check_ci_security.validate_ci_topology(text)

    assert any("directly need every other job" in error for error in errors)


def test_ci_topology_rejects_removed_aggregate_need():
    text = _mutated_ci("      - full-integration\n", "")

    errors = check_ci_security.validate_ci_topology(text)

    assert any("directly need every other job" in error for error in errors)


def test_ci_topology_rejects_conditional_aggregate():
    text = _mutated_ci(
        "  promotion-gate:\n"
        "    name: CI promotion gate\n"
        "    if: ${{ always() }}\n",
        "  promotion-gate:\n"
        "    name: CI promotion gate\n"
        "    if: ${{ success() }}\n",
    )

    errors = check_ci_security.validate_ci_topology(text)

    assert any("promotion-gate must use exact" in error for error in errors)


def test_ci_topology_rejects_conditional_aggregate_validator():
    text = _mutated_ci(
        "      - name: Validate the exact promotion decision and every job result\n"
        "        if: ${{ always() }}\n",
        "      - name: Validate the exact promotion decision and every job result\n"
        "        if: ${{ success() }}\n",
    )

    errors = check_ci_security.validate_ci_topology(text)

    assert any("validator must use exact" in error for error in errors)


@pytest.mark.parametrize(("old", "new"), (
    (
        "              if actual_result != expected_result:\n",
        "              if False:\n",
    ),
    (
        "          if not isinstance(requirements, dict) or "
        "set(requirements) != governed:\n",
        "          if not isinstance(requirements, dict):\n",
    ),
    (
        "              if sha_pattern.fullmatch(actual) is None or "
        "actual != expected:\n",
        "              if sha_pattern.fullmatch(actual) is None:\n",
    ),
))
def test_ci_topology_requires_aggregate_consistency_markers(old, new):
    text = _mutated_ci(old, new)

    errors = check_ci_security.validate_ci_topology(text)

    assert any("promotion validator" in error for error in errors)


def test_ci_topology_requires_exact_allowed_group_vocabulary():
    text = _mutated_ci(
        '              "vector",\n',
        '              "arbitrary",\n',
    )

    errors = check_ci_security.validate_ci_topology(text)

    assert any("allowed_groups must equal" in error for error in errors)


def test_ci_topology_requires_allowed_group_membership_check():
    text = _mutated_ci(
        "              or any(group not in allowed_groups for group in groups)\n",
        "              or False\n",
    )

    errors = check_ci_security.validate_ci_topology(text)

    assert any("promotion validator" in error for error in errors)


def test_ci_topology_rejects_nonterminal_error_guard():
    text = _mutated_ci(
        "          if errors:\n",
        "          if False and errors:\n",
    )

    errors = check_ci_security.validate_ci_topology(text)

    assert any("reject accumulated errors" in error for error in errors)


def test_ci_topology_rejects_error_list_clear_before_terminal_guard():
    marker = "          if errors:\n"
    text = _mutated_ci(
        marker,
        "          errors.clear()\n" + marker,
    )

    errors = check_ci_security.validate_ci_topology(text)

    assert any("active promotion gate differs" in error for error in errors)


@pytest.mark.parametrize(
    "required_owner", sorted(check_ci_security._REQUIRED_CURRENT_OWNERS))
def test_required_current_owner_cannot_be_removed(
    tmp_path, required_owner,
):
    policy, _workflow_path = _valid_tree(tmp_path)
    owner_group = next(
        group
        for group, paths in policy["current_implementation_owners"].items()
        if required_owner in paths
    )
    policy["current_implementation_owners"][owner_group].remove(
        required_owner)
    _write(
        tmp_path / check_ci_security.POLICY_PATH,
        json.dumps(policy, indent=2) + "\n",
    )

    errors = check_ci_security.validate(tmp_path)

    assert any(
        f"missing required current owner: {required_owner}" in error
        for error in errors
    )


@pytest.mark.parametrize(
    "required_path", sorted(check_ci_security._REQUIRED_GOVERNANCE_PATHS))
def test_required_governance_path_cannot_be_removed(
    tmp_path, required_path,
):
    policy, _workflow_path = _valid_tree(tmp_path)
    policy["governance_paths"].remove(required_path)
    _write(
        tmp_path / check_ci_security.POLICY_PATH,
        json.dumps(policy, indent=2) + "\n",
    )

    errors = check_ci_security.validate(tmp_path)

    assert any(
        f"missing required governance path: {required_path}" in error
        for error in errors
    )


@pytest.mark.parametrize(
    "required_filter", sorted(check_ci_security._REQUIRED_SECURITY_FILTERS))
def test_required_security_filter_cannot_be_removed(
    tmp_path, required_filter,
):
    _policy_data, workflow_path = _valid_tree(tmp_path)
    text = workflow_path.read_text(encoding="utf-8")
    assert text.count(f'      - "{required_filter}"') == 2
    workflow_path.write_text(
        text.replace(f'      - "{required_filter}"\n', "", 1),
        encoding="utf-8",
    )

    errors = check_ci_security.validate(tmp_path)

    assert any(
        f"declared security owner {required_filter}" in error
        for error in errors
    )


def test_ownership_policy_rejects_duplicate_json_keys(tmp_path):
    _policy_data, _workflow_path = _valid_tree(tmp_path)
    policy_path = tmp_path / check_ci_security.POLICY_PATH
    policy_path.write_text(
        policy_path.read_text(encoding="utf-8").replace(
            '  "schema_version": 1,\n',
            '  "schema_version": 1,\n  "schema_version": 1,\n',
            1,
        ),
        encoding="utf-8",
    )

    errors = check_ci_security.validate(tmp_path)

    assert any("JSON object repeats key 'schema_version'" in error
               for error in errors)


def test_ownership_policy_rejects_boolean_schema_version(tmp_path):
    policy, _workflow_path = _valid_tree(tmp_path)
    policy["schema_version"] = True
    _write(
        tmp_path / check_ci_security.POLICY_PATH,
        json.dumps(policy, indent=2) + "\n",
    )

    errors = check_ci_security.validate(tmp_path)

    assert any("schema_version must be integer 1" in error for error in errors)


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


def test_quoted_action_keys_cannot_hide_unpinned_actions(tmp_path):
    _policy_data, workflow_path = _valid_tree(tmp_path)
    workflow_path.write_text(
        workflow_path.read_text(encoding="utf-8").replace(
            f"uses: actions/checkout@{_CHECKOUT_SHA}",
            '"uses": actions/checkout@main',
        ),
        encoding="utf-8",
    )

    errors = check_ci_security.validate(tmp_path)

    assert any("quoted mapping keys are unsupported" in error
               for error in errors)


def test_flow_action_steps_cannot_hide_unpinned_actions(tmp_path):
    _policy_data, workflow_path = _valid_tree(tmp_path)
    workflow_path.write_text(
        workflow_path.read_text(encoding="utf-8").replace(
            "      - name: Check out repository\n"
            f"        uses: actions/checkout@{_CHECKOUT_SHA}\n"
            "        with:\n"
            "          persist-credentials: false",
            "      - {uses: actions/checkout@main}",
        ),
        encoding="utf-8",
    )

    errors = check_ci_security.validate(tmp_path)

    assert any("flow mappings are unsupported" in error for error in errors)


def test_flow_job_permissions_cannot_hide_write_access(tmp_path):
    _policy_data, workflow_path = _valid_tree(tmp_path)
    prefix = workflow_path.read_text(encoding="utf-8").split("jobs:", 1)[0]
    workflow_path.write_text(
        prefix
        + "jobs:\n"
        + "  test: {permissions: write-all, runs-on: ubuntu-latest, "
        + "steps: [{run: echo ok}]}\n",
        encoding="utf-8",
    )

    errors = check_ci_security.validate(tmp_path)

    assert any("flow mappings are unsupported" in error for error in errors)


def test_quoted_job_permissions_cannot_hide_write_access(tmp_path):
    _policy_data, workflow_path = _valid_tree(tmp_path)
    workflow_path.write_text(
        workflow_path.read_text(encoding="utf-8").replace(
            "  test:\n    runs-on:",
            '  test:\n    "permissions": write-all\n    runs-on:',
        ),
        encoding="utf-8",
    )

    errors = check_ci_security.validate(tmp_path)

    assert any("quoted mapping keys are unsupported" in error
               for error in errors)


def test_block_scalar_content_is_not_interpreted_as_workflow_yaml(tmp_path):
    _policy_data, workflow_path = _valid_tree(tmp_path)
    workflow_path.write_text(
        workflow_path.read_text(encoding="utf-8")
        + "      - name: Render inert fixture\n"
        + "        run: |\n"
        + "          payload = {\n"
        + '              "uses": "actions/checkout@main",\n'
        + '              "permissions": "write-all",\n'
        + "          }\n",
        encoding="utf-8",
    )

    assert check_ci_security.validate(tmp_path) == []


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
