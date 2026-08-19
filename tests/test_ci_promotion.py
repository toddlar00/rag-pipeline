import json
from dataclasses import replace
import os
from pathlib import Path
import subprocess
import sys

import pytest

from tools import ci_promotion


BASE_SHA = "a" * 40
HEAD_SHA = "b" * 40
CANDIDATE_SHA = "c" * 40
ALL_REQUIRED = {
    job: "required" for job in ci_promotion.EXECUTION_JOBS
}


def _policy() -> ci_promotion.RiskPolicy:
    return ci_promotion.load_policy(
        ci_promotion.PROJECT_ROOT / ci_promotion.POLICY_PATH
    )


def _write_policy(tmp_path: Path, payload: object) -> Path:
    path = tmp_path / "policy.json"
    path.write_text(json.dumps(payload, indent=2) + "\n", encoding="utf-8")
    return path


def _policy_payload() -> dict:
    return json.loads(
        (ci_promotion.PROJECT_ROOT / ci_promotion.POLICY_PATH).read_text(
            encoding="utf-8"
        )
    )


def _change(status: str, first: str, second: str | None = None):
    return ci_promotion.Change(status, first, second)


def _with_regular_modes(
    change: ci_promotion.Change,
) -> ci_promotion.Change:
    endpoints = tuple(
        ci_promotion.TreeEndpoint(
            side=side,
            endpoint=role,
            exists=exists,
            mode="100644" if exists else None,
            object_type="blob" if exists else None,
        )
        for side, role, exists in ci_promotion._expected_tree_endpoints(change)
    )
    return ci_promotion.Change(
        change.status,
        change.old_path,
        change.new_path,
        endpoints,
    )


def _decision(
    *changes: ci_promotion.Change,
    event_name: str = "pull_request",
    head_ref: str = "feature",
) -> ci_promotion.Decision:
    trusted = (
        BASE_SHA
        if event_name in {"pull_request", "merge_group"}
        else HEAD_SHA
    )
    return ci_promotion.classify(
        _policy(),
        tuple(_with_regular_modes(change) for change in changes),
        base_sha=BASE_SHA,
        head_sha=HEAD_SHA,
        candidate_sha=(
            CANDIDATE_SHA if event_name == "pull_request" else HEAD_SHA
        ),
        trusted_sha=trusted,
        event_name=event_name,
        head_ref=head_ref,
    )


def _git(root: Path, *args: str) -> str:
    return subprocess.check_output(
        ["git", *args], cwd=root, text=True
    ).strip()


def _init_git(root: Path) -> None:
    subprocess.run(["git", "init", "-q"], cwd=root, check=True)
    subprocess.run(
        ["git", "config", "user.email", "ci@example.invalid"],
        cwd=root,
        check=True,
    )
    subprocess.run(
        ["git", "config", "user.name", "CI Test"],
        cwd=root,
        check=True,
    )


def _commit(root: Path, message: str) -> str:
    subprocess.run(["git", "add", "-A"], cwd=root, check=True)
    subprocess.run(
        ["git", "commit", "-m", message],
        cwd=root,
        check=True,
        stdout=subprocess.DEVNULL,
    )
    return _git(root, "rev-parse", "HEAD")


def _commit_index(root: Path, message: str) -> str:
    subprocess.run(
        ["git", "commit", "-m", message],
        cwd=root,
        check=True,
        stdout=subprocess.DEVNULL,
    )
    return _git(root, "rev-parse", "HEAD")


def _merge_candidate(root: Path, base: str, head: str) -> str:
    tree = _git(root, "rev-parse", f"{head}^{{tree}}")
    result = subprocess.run(
        ["git", "commit-tree", tree, "-p", base, "-p", head],
        cwd=root,
        check=True,
        input="synthetic candidate\n",
        text=True,
        stdout=subprocess.PIPE,
    )
    return result.stdout.strip()


def _index_symlink(root: Path, path: str, target: str) -> None:
    object_id = subprocess.check_output(
        ["git", "hash-object", "-w", "--stdin"],
        cwd=root,
        input=target.encode("utf-8"),
    ).decode("ascii").strip()
    subprocess.run(
        [
            "git",
            "update-index",
            "--add",
            "--cacheinfo",
            f"120000,{object_id},{path}",
        ],
        cwd=root,
        check=True,
    )


def test_repository_policy_is_valid_and_complete():
    policy = _policy()

    assert set(policy.groups) == ci_promotion.REQUIRED_GROUPS
    assert policy.fast_python_versions == ("3.12",)
    assert policy.full_python_versions == (
        "3.10", "3.11", "3.12", "3.13", "3.14",
    )
    assert policy.unknown_required_jobs == frozenset(
        ci_promotion.EXECUTION_JOBS
    )


@pytest.mark.parametrize(
    "mutation,match",
    [
        (lambda value: value.update({"extra": True}), "fields differ"),
        (lambda value: value.pop("security"), "groups differ"),
    ],
)
def test_policy_rejects_unknown_fields_and_missing_groups(
    tmp_path, mutation, match
):
    payload = _policy_payload()
    target = payload if match == "fields differ" else payload["groups"]
    mutation(target)

    with pytest.raises(ci_promotion.PromotionError, match=match):
        ci_promotion.load_policy(_write_policy(tmp_path, payload))


@pytest.mark.parametrize("bad_path", ["../escape.py", "/absolute.py", "*.py"])
def test_policy_rejects_unsafe_or_glob_exact_paths(tmp_path, bad_path):
    payload = _policy_payload()
    payload["groups"]["security"]["selectors"]["exact"].append(bad_path)

    with pytest.raises(ci_promotion.PromotionError, match="invalid path"):
        ci_promotion.load_policy(_write_policy(tmp_path, payload))


def test_policy_rejects_weakened_named_and_unknown_lanes(tmp_path):
    payload = _policy_payload()
    payload["groups"]["service"]["required_jobs"].remove("phase-a0")

    with pytest.raises(ci_promotion.PromotionError, match="weakens"):
        ci_promotion.load_policy(_write_policy(tmp_path, payload))

    payload = _policy_payload()
    payload["unknown"]["required_jobs"].remove("phase-a0")
    with pytest.raises(ci_promotion.PromotionError, match="complete lane"):
        ci_promotion.load_policy(_write_policy(tmp_path, payload))


def test_policy_rejects_non_markdown_documentation_selector(tmp_path):
    payload = _policy_payload()
    payload["groups"]["documentation_only"]["selectors"]["exact"].append(
        "unsafe.py"
    )

    with pytest.raises(ci_promotion.PromotionError, match="Markdown files only"):
        ci_promotion.load_policy(_write_policy(tmp_path, payload))


def test_policy_rejects_duplicate_json_keys(tmp_path):
    path = tmp_path / "policy.json"
    path.write_text(
        '{"schema_version":1,"schema_version":1}\n', encoding="utf-8"
    )

    with pytest.raises(ci_promotion.PromotionError, match="repeats key"):
        ci_promotion.load_policy(path)


def test_policy_rejects_boolean_schema_version(tmp_path):
    payload = _policy_payload()
    payload["schema_version"] = True

    with pytest.raises(ci_promotion.PromotionError, match="schema_version"):
        ci_promotion.load_policy(_write_policy(tmp_path, payload))


@pytest.mark.parametrize("status", ["A", "D", "M", "T"])
def test_parse_single_path_statuses(status):
    assert ci_promotion.parse_name_status_z(
        status.encode("ascii") + b"\0path with space.py\0"
    ) == (_change(status, "path with space.py"),)


@pytest.mark.parametrize("status", ["R100", "R72", "C100", "C5"])
def test_parse_rename_and_copy_statuses(status):
    assert ci_promotion.parse_name_status_z(
        status.encode("ascii") + b"\0old.py\0new.py\0"
    ) == (_change(status, "old.py", "new.py"),)


def test_parse_preserves_nul_safe_unusual_repository_names():
    unusual = "-unicode-λ name\twith\nnewline.py"

    assert ci_promotion.parse_name_status_z(
        b"M\0" + unusual.encode("utf-8") + b"\0"
    ) == (_change("M", unusual),)


@pytest.mark.parametrize(
    "raw,match",
    [
        (b"M\0file.py", "not terminated"),
        (b"R100\0old.py\0", "truncated"),
        (b"R101\0old.py\0new.py\0", "invalid rename"),
        (b"U\0file.py\0", "unsupported status"),
        (b"X\0file.py\0", "unsupported status"),
        (b"B\0file.py\0", "unsupported status"),
        (b"M\0\xff\0", "non-UTF-8"),
        (b"M\0../escape.py\0", "non-repository"),
    ],
)
def test_parse_rejects_malformed_or_unsafe_records(raw, match):
    with pytest.raises(ci_promotion.PromotionError, match=match):
        ci_promotion.parse_name_status_z(raw)


@pytest.mark.parametrize(
    "path,group",
    [
        ("service_api.py", "service"),
        ("vector_lifecycle.py", "vector"),
        ("process_supervision.py", "process_supervision"),
        ("retention.py", "storage_publication"),
        ("evaluation/suites/property/queries.jsonl", "evaluation"),
        ("requirements-full.lock", "packaging"),
        ("tools/ci_promotion.py", "security"),
        ("ci-risk-policy.json", "security"),
        (".github/workflows/ci.yml", "security"),
    ],
)
def test_named_non_documentation_groups_require_every_job(path, group):
    decision = _decision(_change("M", path))

    assert decision.groups == (group,)
    assert decision.requirements == ALL_REQUIRED
    assert decision.pythons == _policy().full_python_versions


@pytest.mark.parametrize("path", ["README.md", "docs/guide.md"])
def test_documentation_only_changes_use_the_fast_lane(path):
    decision = _decision(_change("M", path))

    assert decision.groups == ("documentation_only",)
    assert decision.pythons == ("3.12",)
    assert all(
        decision.requirements[job] == "required"
        for job in ci_promotion.ALWAYS_REQUIRED_JOBS
    )
    assert all(
        decision.requirements[job] == "not_required"
        for job in ci_promotion.HEAVY_JOBS
    )


def test_unknown_mixed_and_empty_changes_fail_closed_to_full():
    unknown = _decision(_change("A", "future_runtime.py"))
    mixed = _decision(
        _change("M", "README.md"),
        _change("M", "service_api.py"),
    )
    empty = _decision()

    assert unknown.groups == ("unknown",)
    assert mixed.groups == ("documentation_only", "service")
    assert empty.groups == ("unknown",)
    assert unknown.requirements == mixed.requirements == empty.requirements
    assert unknown.requirements == ALL_REQUIRED


@pytest.mark.parametrize(
    "old,new",
    [
        ("release_security.py", "docs/release-security.md"),
        ("docs/release-security.md", "release_security.py"),
    ],
)
def test_rename_classifies_both_endpoints_and_stays_heavy(old, new):
    decision = _decision(_change("R100", old, new))

    assert "security" in decision.groups
    assert "documentation_only" in decision.groups
    assert decision.requirements == ALL_REQUIRED


def test_documentation_to_documentation_rename_stays_fast():
    decision = _decision(
        _change("R100", "docs/old.md", "docs/new.md")
    )

    assert decision.groups == ("documentation_only",)
    assert decision.requirements["phase-a0"] == "not_required"


def test_unverified_type_changed_or_inconsistent_markdown_modes_force_full():
    policy = _policy()
    common = {
        "base_sha": BASE_SHA,
        "head_sha": HEAD_SHA,
        "candidate_sha": CANDIDATE_SHA,
        "trusted_sha": BASE_SHA,
        "event_name": "pull_request",
    }
    unverified = ci_promotion.classify(
        policy, (_change("M", "README.md"),), **common
    )
    type_changed = ci_promotion.classify(
        policy,
        (_with_regular_modes(_change("T", "README.md")),),
        **common,
    )
    added = _with_regular_modes(_change("A", "docs/new.md"))
    unexpected_base_entry = ci_promotion.TreeEndpoint(
        "base", "old", True, "100644", "blob"
    )
    inconsistent = ci_promotion.classify(
        policy,
        (ci_promotion.Change(
            added.status,
            added.old_path,
            added.new_path,
            (unexpected_base_entry, *added.tree_endpoints[1:]),
        ),),
        **common,
    )

    for decision in (unverified, type_changed, inconsistent):
        assert decision.requirements == ALL_REQUIRED
        assert "unsafe_or_unverified_mode" in decision.reason_codes


def test_content_free_change_identity_binds_mode_facts_not_paths():
    first = _with_regular_modes(_change("M", "docs/client-one.md"))
    second = _with_regular_modes(_change("M", "docs/client-two.md"))
    executable = ci_promotion.Change(
        first.status,
        first.old_path,
        first.new_path,
        tuple(
            ci_promotion.TreeEndpoint(
                endpoint.side,
                endpoint.endpoint,
                endpoint.exists,
                "100755" if endpoint.exists else None,
                endpoint.object_type,
            )
            for endpoint in first.tree_endpoints
        ),
    )

    assert (
        ci_promotion._change_set_sha256((first,))
        == ci_promotion._change_set_sha256((second,))
    )
    assert (
        ci_promotion._change_set_sha256((first,))
        != ci_promotion._change_set_sha256((executable,))
    )


def test_deleting_a_safety_owned_path_stays_heavy():
    decision = _decision(_change("D", "service_http.py"))

    assert decision.groups == ("service",)
    assert decision.requirements == ALL_REQUIRED


@pytest.mark.parametrize(
    "event_name,head_ref,reason",
    [
        ("push", "", "push_force_heavy"),
        ("workflow_dispatch", "", "manual_force_heavy"),
        ("merge_group", "", "merge_group_force_heavy"),
        ("pull_request", "release-candidate", "candidate_force_heavy"),
    ],
)
def test_force_heavy_overrides_never_weaken_path_classification(
    event_name, head_ref, reason
):
    decision = _decision(
        _change("M", "README.md"),
        event_name=event_name,
        head_ref=head_ref,
    )

    assert decision.forced_heavy is True
    assert reason in decision.reason_codes
    assert decision.requirements == ALL_REQUIRED


def test_trusted_sha_and_lowercase_exact_sha_are_enforced():
    policy = _policy()
    kwargs = {
        "base_sha": BASE_SHA,
        "head_sha": HEAD_SHA,
        "candidate_sha": CANDIDATE_SHA,
        "trusted_sha": HEAD_SHA,
        "event_name": "pull_request",
    }
    with pytest.raises(ci_promotion.PromotionError, match="trusted_sha"):
        ci_promotion.classify(policy, (), **kwargs)

    kwargs["base_sha"] = BASE_SHA.upper()
    kwargs["trusted_sha"] = BASE_SHA.upper()
    with pytest.raises(ci_promotion.PromotionError, match="lowercase"):
        ci_promotion.classify(policy, (), **kwargs)


def test_non_pull_candidate_must_equal_the_event_head():
    with pytest.raises(ci_promotion.PromotionError, match="candidate_sha"):
        ci_promotion.classify(
            _policy(),
            (_with_regular_modes(_change("M", "README.md")),),
            base_sha=BASE_SHA,
            head_sha=HEAD_SHA,
            candidate_sha=CANDIDATE_SHA,
            trusted_sha=HEAD_SHA,
            event_name="push",
        )

def test_decision_and_outputs_are_deterministic_and_omit_raw_paths(tmp_path):
    unsafe_name = "private-client-name\nsecret.py"
    first = _decision(_change("M", unsafe_name))
    second = _decision(_change("M", unsafe_name))
    output = tmp_path / "github-output"

    ci_promotion.write_outputs(first, output)
    rendered = output.read_text(encoding="utf-8")

    assert first.payload() == second.payload()
    assert first.decision_sha256 == second.decision_sha256
    assert replace(
        first, candidate_sha="d" * 40
    ).decision_sha256 != first.decision_sha256
    assert unsafe_name not in rendered
    keys = {line.split("=", 1)[0] for line in rendered.splitlines()}
    assert keys == {
        "classifier_ok",
        "decision",
        "requirements",
        "pythons",
        "require_unit_windows",
        "require_service_api",
        "require_phase_a0",
        "require_vector_store_smoke",
        "require_full_integration",
        "base_sha",
        "head_sha",
        "candidate_sha",
        "trusted_sha",
        "policy_sha256",
        "decision_sha256",
        "groups",
        "changed_count",
    }
    output_values = dict(
        line.split("=", 1) for line in rendered.splitlines()
    )
    assert json.loads(output_values["decision"]) == first.payload()


def test_canonical_decision_round_trip_accepts_sorted_json_keys():
    decision = _decision(_change("M", "README.md"))
    canonical = json.loads(
        json.dumps(decision.payload(), sort_keys=True)
    )

    loaded = ci_promotion._decision_from_payload(canonical)

    assert loaded == decision


def test_decision_tampering_is_detected():
    payload = _decision(_change("M", "README.md")).payload()
    payload["changed_count"] = 99

    with pytest.raises(ci_promotion.PromotionError, match="does not match"):
        ci_promotion._decision_from_payload(payload)


def test_decision_rejects_boolean_schema_version():
    payload = _decision(_change("M", "README.md")).payload()
    payload["schema_version"] = True
    unsigned = dict(payload)
    unsigned.pop("decision_sha256")
    payload["decision_sha256"] = ci_promotion._sha256(
        ci_promotion._canonical_json(unsigned).encode("ascii")
    )

    with pytest.raises(ci_promotion.PromotionError, match="schema_version"):
        ci_promotion._decision_from_payload(payload)


def test_recomputed_digest_cannot_make_a_weakened_decision_semantic():
    full = _decision(_change("M", "service_api.py"))
    weakened_requirements = {
        job: (
            "required"
            if job in ci_promotion.ALWAYS_REQUIRED_JOBS
            else "not_required"
        )
        for job in ci_promotion.EXECUTION_JOBS
    }
    forged = replace(
        full,
        requirements=weakened_requirements,
        pythons=ci_promotion.FAST_PYTHON_VERSIONS,
    )
    payload = forged.payload()

    with pytest.raises(
        ci_promotion.PromotionError, match="semantic invariants"
    ):
        ci_promotion._decision_from_payload(payload)

    apparent_results = {
        job: (
            "success"
            if job in ci_promotion.ALWAYS_REQUIRED_JOBS
            else "skipped"
        )
        for job in ci_promotion.EXECUTION_JOBS
    }
    verification = ci_promotion.verify_promotion(forged, apparent_results)
    assert verification.ok is False
    assert any("risk groups" in error for error in verification.errors)


def test_verify_promotion_accepts_only_exact_required_truth_table():
    full = _decision(_change("M", "service_api.py"))
    docs = _decision(_change("M", "README.md"))
    all_success = {job: "success" for job in ci_promotion.EXECUTION_JOBS}
    docs_results = {
        job: (
            "success"
            if job in ci_promotion.ALWAYS_REQUIRED_JOBS
            else "skipped"
        )
        for job in ci_promotion.EXECUTION_JOBS
    }

    assert ci_promotion.verify_promotion(full, all_success).ok is True
    assert ci_promotion.verify_promotion(docs, docs_results).ok is True

    for bad in ("success", "failure", "cancelled"):
        altered = dict(docs_results, **{"phase-a0": bad})
        verification = ci_promotion.verify_promotion(docs, altered)
        assert verification.ok is False
        assert "expected skipped" in verification.errors[0]

    altered = dict(all_success, **{"phase-a0": "skipped"})
    assert ci_promotion.verify_promotion(full, altered).ok is False

    for bad in ("failure", "cancelled"):
        altered = dict(all_success, **{"phase-a0": bad})
        assert ci_promotion.verify_promotion(full, altered).ok is False


def test_verify_promotion_rejects_missing_extra_and_unknown_results():
    decision = _decision(_change("M", "service_api.py"))
    results = {job: "success" for job in ci_promotion.EXECUTION_JOBS}
    results.pop("quality")
    assert ci_promotion.verify_promotion(decision, results).ok is False

    results["quality"] = "success"
    results["surprise"] = "success"
    assert ci_promotion.verify_promotion(decision, results).ok is False

    results.pop("surprise")
    results["quality"] = "timed_out"
    verification = ci_promotion.verify_promotion(decision, results)
    assert verification.ok is False
    assert "unexpected result" in verification.errors[0]


def test_git_changes_requires_exact_commit_objects_and_detects_rename(tmp_path):
    subprocess.run(["git", "init", "-q"], cwd=tmp_path, check=True)
    subprocess.run(
        ["git", "config", "user.email", "ci@example.invalid"],
        cwd=tmp_path,
        check=True,
    )
    subprocess.run(
        ["git", "config", "user.name", "CI Test"],
        cwd=tmp_path,
        check=True,
    )
    (tmp_path / "release_security.py").write_text("old\n", encoding="utf-8")
    base = _commit(tmp_path, "base")
    (tmp_path / "release_security.py").rename(tmp_path / "moved.py")
    head = _commit(tmp_path, "rename")

    changes = ci_promotion.git_changes(tmp_path, base, head)

    assert len(changes.changes) == 1
    assert changes.changes[0].status == "R100"
    assert changes.changes[0].paths == (
        "release_security.py", "moved.py",
    )
    assert ci_promotion._regular_blob_modes_verified(changes.changes[0])
    with pytest.raises(ci_promotion.PromotionError, match="lowercase"):
        ci_promotion.git_changes(tmp_path, base.upper(), head)
    with pytest.raises(ci_promotion.PromotionError, match="cannot resolve"):
        ci_promotion.git_changes(tmp_path, "0" * 40, head)


def test_pull_request_candidate_requires_exact_base_and_head_parents(tmp_path):
    _init_git(tmp_path)
    (tmp_path / "README.md").write_text("base\n", encoding="utf-8")
    base = _commit(tmp_path, "base")
    (tmp_path / "README.md").write_text("head\n", encoding="utf-8")
    head = _commit(tmp_path, "head")
    candidate = _merge_candidate(tmp_path, base, head)
    environment = {**os.environ, "GIT_OPTIONAL_LOCKS": "0"}

    ci_promotion._verify_pull_request_candidate(
        tmp_path,
        base_sha=base,
        head_sha=head,
        candidate_sha=candidate,
        environment=environment,
    )
    with pytest.raises(ci_promotion.PromotionError, match="exact base/head"):
        ci_promotion._verify_pull_request_candidate(
            tmp_path,
            base_sha=base,
            head_sha=head,
            candidate_sha=head,
            environment=environment,
        )


def test_trusted_git_inspection_ignores_repository_replace_refs(tmp_path):
    _init_git(tmp_path)
    (tmp_path / "README.md").write_text("base\n", encoding="utf-8")
    base = _commit(tmp_path, "base")
    (tmp_path / "README.md").write_text("docs\n", encoding="utf-8")
    substitute = _commit(tmp_path, "docs substitute")
    workflow = tmp_path / ".github" / "workflows" / "ci.yml"
    workflow.parent.mkdir(parents=True)
    workflow.write_text("name: malicious replacement target\n", encoding="utf-8")
    head = _commit(tmp_path, "actual workflow change")
    replace_enabled_env = {
        key: value
        for key, value in os.environ.items()
        if key.upper() != "GIT_NO_REPLACE_OBJECTS"
    }
    subprocess.run(
        ["git", "replace", head, substitute],
        cwd=tmp_path,
        check=True,
        env=replace_enabled_env,
    )

    replaced_diff = subprocess.check_output(
        ["git", "diff", "--name-only", base, head],
        cwd=tmp_path,
        text=True,
        env=replace_enabled_env,
    ).splitlines()
    assert replaced_diff == ["README.md"]

    change_set = ci_promotion.git_changes(tmp_path, base, head)
    decision = ci_promotion.classify(
        _policy(),
        change_set.changes,
        base_sha=base,
        head_sha=head,
        candidate_sha=head,
        trusted_sha=base,
        event_name="pull_request",
    )

    assert {path for change in change_set.changes for path in change.paths} == {
        ".github/workflows/ci.yml",
        "README.md",
    }
    assert decision.groups == ("documentation_only", "security")
    assert decision.requirements == ALL_REQUIRED


@pytest.mark.parametrize(
    "operation,expected_status",
    [
        ("add", "A"),
        ("modify", "M"),
        ("delete", "D"),
        ("type_change", "T"),
    ],
)
def test_exact_tree_symlink_modes_force_markdown_changes_full(
    tmp_path, operation, expected_status
):
    _init_git(tmp_path)
    (tmp_path / "README.md").write_text("seed\n", encoding="utf-8")
    link_path = "docs/link.md"

    if operation == "add":
        base = _commit(tmp_path, "base")
        _index_symlink(tmp_path, link_path, "first-target")
        head = _commit_index(tmp_path, "add symlink")
    elif operation == "type_change":
        (tmp_path / "docs").mkdir()
        (tmp_path / link_path).write_text("regular\n", encoding="utf-8")
        base = _commit(tmp_path, "regular base")
        _index_symlink(tmp_path, link_path, "first-target")
        head = _commit_index(tmp_path, "change to symlink")
    else:
        _commit(tmp_path, "seed")
        _index_symlink(tmp_path, link_path, "first-target")
        base = _commit_index(tmp_path, "symlink base")
        if operation == "modify":
            _index_symlink(tmp_path, link_path, "second-target")
        else:
            subprocess.run(
                ["git", "update-index", "--force-remove", link_path],
                cwd=tmp_path,
                check=True,
            )
        head = _commit_index(tmp_path, f"{operation} symlink")

    change_set = ci_promotion.git_changes(tmp_path, base, head)
    assert len(change_set.changes) == 1
    assert change_set.changes[0].status == expected_status
    assert any(
        endpoint.mode == "120000"
        for endpoint in change_set.changes[0].tree_endpoints
    )
    decision = ci_promotion.classify(
        _policy(),
        change_set.changes,
        base_sha=base,
        head_sha=head,
        candidate_sha=head,
        trusted_sha=base,
        event_name="pull_request",
    )
    assert decision.groups == ("documentation_only",)
    assert decision.requirements == ALL_REQUIRED
    assert "unsafe_or_unverified_mode" in decision.reason_codes


def test_exact_tree_gitlink_mode_for_markdown_name_forces_full(tmp_path):
    _init_git(tmp_path)
    (tmp_path / "README.md").write_text("seed\n", encoding="utf-8")
    base = _commit(tmp_path, "base")
    gitlink_path = "docs/module.md"
    subprocess.run(
        [
            "git",
            "update-index",
            "--add",
            "--cacheinfo",
            f"160000,{base},{gitlink_path}",
        ],
        cwd=tmp_path,
        check=True,
    )
    head = _commit_index(tmp_path, "add gitlink")

    change_set = ci_promotion.git_changes(tmp_path, base, head)
    endpoint = next(
        endpoint
        for endpoint in change_set.changes[0].tree_endpoints
        if endpoint.exists
    )
    assert (endpoint.mode, endpoint.object_type) == ("160000", "commit")
    decision = ci_promotion.classify(
        _policy(),
        change_set.changes,
        base_sha=base,
        head_sha=head,
        candidate_sha=head,
        trusted_sha=base,
        event_name="pull_request",
    )
    assert decision.requirements == ALL_REQUIRED
    assert "unsafe_or_unverified_mode" in decision.reason_codes


def test_base_policy_defeats_malicious_head_policy_change(tmp_path):
    subprocess.run(["git", "init", "-q"], cwd=tmp_path, check=True)
    subprocess.run(
        ["git", "config", "user.email", "ci@example.invalid"],
        cwd=tmp_path,
        check=True,
    )
    subprocess.run(
        ["git", "config", "user.name", "CI Test"],
        cwd=tmp_path,
        check=True,
    )
    policy_path = tmp_path / ci_promotion.POLICY_PATH
    policy_path.write_bytes(
        (ci_promotion.PROJECT_ROOT / ci_promotion.POLICY_PATH).read_bytes()
    )
    (tmp_path / "README.md").write_text("base\n", encoding="utf-8")
    base = _commit(tmp_path, "trusted base")
    trusted_policy_path = tmp_path / "trusted-policy.json"
    trusted_policy_path.write_bytes(
        subprocess.check_output(
            ["git", "show", f"{base}:{ci_promotion.POLICY_PATH}"],
            cwd=tmp_path,
        )
    )

    malicious = _policy_payload()
    malicious["groups"]["documentation_only"]["selectors"]["exact"].append(
        "danger.py"
    )
    policy_path.write_text(json.dumps(malicious), encoding="utf-8")
    (tmp_path / "danger.py").write_text("print('head')\n", encoding="utf-8")
    head = _commit(tmp_path, "malicious head")

    change_set = ci_promotion.git_changes(tmp_path, base, head)
    decision = ci_promotion.classify(
        ci_promotion.load_policy(trusted_policy_path),
        change_set.changes,
        base_sha=base,
        head_sha=head,
        candidate_sha=head,
        trusted_sha=base,
        event_name="pull_request",
    )

    assert "security" in decision.groups
    assert "unknown" in decision.groups
    assert decision.requirements == ALL_REQUIRED


def test_classify_cli_writes_bound_outputs_and_content_free_decision(tmp_path):
    subprocess.run(["git", "init", "-q"], cwd=tmp_path, check=True)
    subprocess.run(
        ["git", "config", "user.email", "ci@example.invalid"],
        cwd=tmp_path,
        check=True,
    )
    subprocess.run(
        ["git", "config", "user.name", "CI Test"],
        cwd=tmp_path,
        check=True,
    )
    (tmp_path / "README.md").write_text("one\n", encoding="utf-8")
    base = _commit(tmp_path, "base")
    (tmp_path / "README.md").write_text("two\n", encoding="utf-8")
    head = _commit(tmp_path, "docs")
    candidate = _merge_candidate(tmp_path, base, head)
    output = tmp_path / "outputs.txt"
    report = tmp_path / "decision.json"

    result = ci_promotion.main([
        "classify",
        "--policy", str(ci_promotion.PROJECT_ROOT / ci_promotion.POLICY_PATH),
        "--root", str(tmp_path),
        "--base-sha", base,
        "--head-sha", head,
        "--candidate-sha", candidate,
        "--trusted-sha", base,
        "--event-name", "pull_request",
        "--head-ref", "docs",
        "--github-output", str(output),
        "--decision-out", str(report),
    ])

    assert result == 0
    assert "require_phase_a0=false" in output.read_text(encoding="utf-8")
    payload = json.loads(report.read_text(encoding="utf-8"))
    assert payload["groups"] == ["documentation_only"]
    assert payload["candidate_sha"] == candidate
    assert "README.md" not in report.read_text(encoding="utf-8")

    results = {
        job: (
            "success"
            if job in ci_promotion.ALWAYS_REQUIRED_JOBS
            else "skipped"
        )
        for job in ci_promotion.EXECUTION_JOBS
    }
    results_path = tmp_path / "results.json"
    results_path.write_text(json.dumps(results), encoding="utf-8")
    verify_args = [
        "verify",
        "--decision", str(report),
        "--results", str(results_path),
        "--policy", str(ci_promotion.PROJECT_ROOT / ci_promotion.POLICY_PATH),
        "--root", str(tmp_path),
        "--head-ref", "docs",
    ]
    assert ci_promotion.main(verify_args) == 0

    loaded = ci_promotion._decision_from_payload(payload)
    forged = replace(
        loaded,
        groups=("service",),
        requirements=ALL_REQUIRED,
        pythons=ci_promotion.FULL_PYTHON_VERSIONS,
        reason_codes=("non_documentation_change",),
    )
    report.write_text(json.dumps(forged.payload()), encoding="utf-8")
    assert ci_promotion.main(verify_args) == 2


def test_fork_pull_refs_need_only_the_upstream_remote_and_bind_merge_candidate(
    tmp_path,
):
    upstream = tmp_path / "upstream.git"
    seed = tmp_path / "seed"
    fork_bare = tmp_path / "fork.git"
    fork_work = tmp_path / "fork-work"
    runner = tmp_path / "runner"
    subprocess.run(["git", "init", "--bare", "-q", upstream], check=True)
    seed.mkdir()
    _init_git(seed)
    (seed / "tools").mkdir()
    (seed / "docs").mkdir()
    (seed / "tools" / "ci_promotion.py").write_bytes(
        (ci_promotion.PROJECT_ROOT / "tools" / "ci_promotion.py").read_bytes()
    )
    (seed / ci_promotion.POLICY_PATH).write_bytes(
        (ci_promotion.PROJECT_ROOT / ci_promotion.POLICY_PATH).read_bytes()
    )
    (seed / "docs" / "base.md").write_text("base\n", encoding="utf-8")
    base = _commit(seed, "trusted base")
    subprocess.run(
        ["git", "remote", "add", "upstream", str(upstream)],
        cwd=seed,
        check=True,
    )
    subprocess.run(
        ["git", "push", "-q", "upstream", "HEAD:refs/heads/main"],
        cwd=seed,
        check=True,
    )
    subprocess.run(
        ["git", "--git-dir", str(upstream), "symbolic-ref", "HEAD", "refs/heads/main"],
        check=True,
    )

    subprocess.run(["git", "clone", "-q", "--bare", upstream, fork_bare], check=True)
    subprocess.run(["git", "clone", "-q", fork_bare, fork_work], check=True)
    subprocess.run(
        ["git", "config", "user.email", "fork@example.invalid"],
        cwd=fork_work,
        check=True,
    )
    subprocess.run(
        ["git", "config", "user.name", "Fork Contributor"],
        cwd=fork_work,
        check=True,
    )
    subprocess.run(
        ["git", "checkout", "-q", "-b", "feature"],
        cwd=fork_work,
        check=True,
    )
    (fork_work / "docs" / "fork.md").write_text(
        "fork documentation\n", encoding="utf-8"
    )
    head = _commit(fork_work, "fork head")
    candidate = _merge_candidate(fork_work, base, head)
    subprocess.run(
        [
            "git", "push", "-q", str(upstream),
            f"{head}:refs/pull/17/head",
            f"{candidate}:refs/pull/17/merge",
        ],
        cwd=fork_work,
        check=True,
    )

    subprocess.run(
        ["git", "clone", "-q", "--no-checkout", upstream, runner],
        check=True,
    )
    subprocess.run(
        [
            "git", "fetch", "-q", "origin",
            "+refs/pull/17/head:refs/remotes/ci-head",
            "+refs/pull/17/merge:refs/remotes/ci-candidate",
        ],
        cwd=runner,
        check=True,
    )
    assert _git(runner, "remote") == "origin"
    assert _git(runner, "rev-parse", "refs/remotes/ci-head^{commit}") == head
    assert (
        _git(runner, "rev-parse", "refs/remotes/ci-candidate^{commit}")
        == candidate
    )

    trusted_dir = tmp_path / "trusted"
    (trusted_dir / "tools").mkdir(parents=True)
    (trusted_dir / "tools" / "ci_promotion.py").write_bytes(
        subprocess.check_output(
            ["git", "show", f"{base}:tools/ci_promotion.py"], cwd=runner
        )
    )
    (trusted_dir / ci_promotion.POLICY_PATH).write_bytes(
        subprocess.check_output(
            ["git", "show", f"{base}:{ci_promotion.POLICY_PATH}"], cwd=runner
        )
    )
    event = {
        "pull_request": {
            "number": 17,
            "base": {"sha": base},
            "head": {"sha": head, "ref": "feature"},
        },
        "ref": "refs/pull/17/merge",
        "sha": candidate,
    }
    event_path = tmp_path / "event.json"
    event_path.write_text(json.dumps(event), encoding="utf-8")
    output = tmp_path / "fork-output"
    report = tmp_path / "fork-decision.json"
    result = subprocess.run(
        [
            sys.executable,
            "-I",
            str(trusted_dir / "tools" / "ci_promotion.py"),
            "classify",
            "--policy",
            str(trusted_dir / ci_promotion.POLICY_PATH),
            "--root",
            str(runner),
            "--base-sha",
            base,
            "--head-sha",
            head,
            "--candidate-sha",
            candidate,
            "--trusted-sha",
            base,
            "--event-name",
            "pull_request",
            "--head-ref",
            "feature",
            "--github-output",
            str(output),
            "--decision-out",
            str(report),
        ],
        check=False,
        stdout=subprocess.PIPE,
        stderr=subprocess.PIPE,
        text=True,
    )

    assert result.returncode == 0, result.stderr
    decision = json.loads(report.read_text(encoding="utf-8"))
    assert decision["base_sha"] == base
    assert decision["head_sha"] == head
    assert decision["candidate_sha"] == candidate
    assert decision["groups"] == ["documentation_only"]
    assert "docs/fork.md" not in report.read_text(encoding="utf-8")
    assert f"candidate_sha={candidate}" in output.read_text(encoding="utf-8")

    subprocess.run(
        ["git", "checkout", "-q", "--detach", candidate],
        cwd=runner,
        check=True,
    )
    assert _git(runner, "rev-parse", "HEAD") == candidate
