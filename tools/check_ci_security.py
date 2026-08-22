#!/usr/bin/env python3
"""Enforce repository-owned GitHub Actions security invariants.

The checker deliberately parses only the small YAML surface it owns.  It does
not try to interpret arbitrary YAML, execute workflow expressions, or depend on
a package that must itself be bootstrapped by CI.
"""

from __future__ import annotations

import argparse
import ast
from dataclasses import dataclass
import hashlib
import json
from pathlib import Path, PurePosixPath
import re
import sys
import textwrap
from typing import Any, Sequence


PROJECT_ROOT = Path(__file__).resolve().parents[1]
POLICY_PATH = "ci-security-ownership.json"
CI_WORKFLOW_PATH = ".github/workflows/ci.yml"
ACTIVE_CI_FIXTURE_PATH = ".github/ci/active-ci.yml"
DEPENDENCY_WORKFLOW_PATH = ".github/workflows/dependency-compatibility.yml"
SECURITY_WORKFLOW_PATH = ".github/workflows/security.yml"
CI_PROMOTION_TOOL_PATH = "tools/ci_promotion.py"
POLICY_SCHEMA_VERSION = 1
_POLICY_KEYS = frozenset({
    "schema_version",
    "current_implementation_owners",
    "reserved_implementation_owners",
    "governance_paths",
})
_REQUIRED_CURRENT_OWNERS = frozenset({
    "embedding_policy.py",
    "endpoint_policy.py",
    "index_state.py",
    "llm_adapters.py",
    "llm_output_contracts.py",
    "llm_runtime.py",
    "model_artifacts.py",
    "provider_transport.py",
    "rag.py",
    "release_security.py",
    "resource_lease.py",
    "retrieval_core.py",
    "tools/check_model_artifacts.py",
    "tools/refresh_model_artifacts.py",
    "tools/sync_model_artifacts.py",
    "vector_lifecycle.py",
})
_REQUIRED_RESERVED_OWNERS = frozenset({"pipeline_runtime.py"})
_REQUIRED_GOVERNANCE_PATHS = frozenset({
    ACTIVE_CI_FIXTURE_PATH,
    CI_WORKFLOW_PATH,
    DEPENDENCY_WORKFLOW_PATH,
    SECURITY_WORKFLOW_PATH,
    "ci-risk-policy.json",
    POLICY_PATH,
    "dependency-license-policy.json",
    "dependency-vulnerability-policy.json",
    "model-artifact-policy.json",
    "model-artifacts.lock.json",
    "node-vulnerability-policy.json",
    "tests/test_ci_promotion.py",
    "tests/test_ci_security.py",
    "tests/test_node_supply_chain.py",
    "tools/check_ci_security.py",
    "tools/check_licenses.py",
    "tools/check_node_vulnerabilities.py",
    "tools/check_vulnerabilities.py",
    "tools/ci_promotion.py",
    "tools/normalize_node_sbom.py",
    "tools/zettlr-markdown-validator/package-lock.json",
    "tools/zettlr-markdown-validator/package.json",
})
_REQUIRED_SECURITY_FILTERS = frozenset({
    "requirements*.lock",
    "requirements*.txt",
})
_ACTION_SHA_RE = re.compile(r"[0-9a-fA-F]{40}")
_BLOCK_SCALAR_RE = re.compile(
    r"(?:^|:)\s*[>|](?:[1-9]?[-+]?|[-+]?[1-9]?)\s*(?:#.*)?$"
)
_QUOTED_MAPPING_KEY_RE = re.compile(
    r'''^(?:-\s+)?(?:"(?:[^"\\]|\\.)*"|'(?:[^']|'')*')\s*:'''
)
_FLOW_MAPPING_RE = re.compile(
    r"^(?:\{|-\s*(?:&\S+\s*)?\{|[^:#]+:\s*(?:&\S+\s*)?\{)"
)
_CANDIDATE_REF = "${{ github.sha }}"
_PHASE_A0_BOOTSTRAP_REF = (
    "${{ github.event.pull_request.head.sha || github.sha }}"
)
_ALLOWED_RISK_GROUPS = frozenset({
    "documentation_only",
    "evaluation",
    "packaging",
    "process_supervision",
    "security",
    "service",
    "storage_publication",
    "unknown",
    "vector",
})
_ALWAYS_JOBS = frozenset({"quality", "evaluation-smoke", "unit-linux"})
_HEAVY_JOB_OUTPUTS = {
    "unit-windows": "require_unit_windows",
    "service-api": "require_service_api",
    "phase-a0": "require_phase_a0",
    "vector-store-smoke": "require_vector_store_smoke",
    "full-integration": "require_full_integration",
}
_MATRIX_JOBS = frozenset({
    "unit-linux",
    "service-api",
    "phase-a0",
    "vector-store-smoke",
})
_EXPECTED_EXECUTION_JOBS = _ALWAYS_JOBS | frozenset(_HEAVY_JOB_OUTPUTS)
_EXPECTED_LANE_OUTPUTS = {
    "classifier_ok": "${{ steps.classify.outputs.classifier_ok }}",
    "decision": "${{ steps.classify.outputs.decision }}",
    "requirements": "${{ steps.classify.outputs.requirements }}",
    "pythons": "${{ steps.classify.outputs.pythons }}",
    **{
        output: f"${{{{ steps.classify.outputs.{output} }}}}"
        for output in _HEAVY_JOB_OUTPUTS.values()
    },
    "base_sha": "${{ steps.classify.outputs.base_sha }}",
    "head_sha": "${{ steps.classify.outputs.head_sha }}",
    "candidate_sha": "${{ steps.classify.outputs.candidate_sha }}",
    "trusted_sha": "${{ steps.classify.outputs.trusted_sha }}",
    "policy_sha256": "${{ steps.classify.outputs.policy_sha256 }}",
    "decision_sha256": "${{ steps.classify.outputs.decision_sha256 }}",
    "groups": "${{ steps.classify.outputs.groups }}",
    "changed_count": "${{ steps.classify.outputs.changed_count }}",
}
_UNIT_LINUX_MATRIX = (
    "${{ fromJSON(needs.lane.outputs.pythons || "
    "'[\"3.10\",\"3.11\",\"3.12\",\"3.13\",\"3.14\"]') }}"
)
_BOOTSTRAP_LANE_TEXT = """  lane:
    name: Force full CI bootstrap
    runs-on: ubuntu-24.04
    timeout-minutes: 2
    outputs:
      pythons: ${{ steps.force.outputs.pythons }}
      require_unit_windows: ${{ steps.force.outputs.require_unit_windows }}
      require_service_api: ${{ steps.force.outputs.require_service_api }}
      require_phase_a0: ${{ steps.force.outputs.require_phase_a0 }}
      require_vector_store_smoke: ${{ steps.force.outputs.require_vector_store_smoke }}
      require_full_integration: ${{ steps.force.outputs.require_full_integration }}
    steps:
      - name: Force every heavy job for the trusted seed checkpoint
        id: force
        shell: bash
        run: |
          {
            echo 'pythons=["3.10","3.11","3.12","3.13","3.14"]'
            echo 'require_unit_windows=true'
            echo 'require_service_api=true'
            echo 'require_phase_a0=true'
            echo 'require_vector_store_smoke=true'
            echo 'require_full_integration=true'
          } >> "$GITHUB_OUTPUT"
"""
_BOOTSTRAP_LANE_LINES = tuple(
    line.strip() for line in _BOOTSTRAP_LANE_TEXT.splitlines() if line.strip()
)
_EXPECTED_TOP_ENV = {
    "GIT_NO_REPLACE_OBJECTS": '"1"',
    "PIP_DISABLE_PIP_VERSION_CHECK": '"1"',
    "PYTHONUTF8": '"1"',
}
_EXPECTED_MATRIX_BLOCKS = {
    "unit-linux": (
        "matrix:",
        f"python-version: {_UNIT_LINUX_MATRIX}",
    ),
    "service-api": (
        "matrix:",
        "os: [ubuntu-24.04, windows-latest]",
    ),
    "phase-a0": (
        "matrix:",
        "include:",
        "- os: ubuntu-24.04",
        "platform: Linux",
        "baseline: benchmarks/phase-a0-linux-cpython312.json",
        "artifact: linux",
        "- os: windows-latest",
        "platform: Windows",
        "baseline: benchmarks/phase-a0-windows-cpython312.json",
        "artifact: windows",
    ),
    "vector-store-smoke": (
        "matrix:",
        "os: [ubuntu-24.04, windows-latest]",
    ),
}
_ACTIVE_LANE_SHA256 = (
    "a795b35343bb72108bf58530775327665a8c54e8fe6d6ba80d0b71e2280e8f78"
)
_ACTIVE_PROMOTION_SHA256 = (
    "3d944775fbaf16ce791513f824173a55f384bc94c38159ddb411e0d53066d2b2"
)
_ACTIVE_WORKFLOW_SHA256 = (
    "d6bc0efa241964d3aa3bcd5803f39bb7da5f0b522f8e2ebfff12b343fc0fff07"
)
_BOOTSTRAP_WORKFLOW_SHA256 = (
    "49f2b074ff288462e4a0fcf9afde47ea2f7b6ec1671d777e60b893750540866b"
)


@dataclass(frozen=True, slots=True)
class OwnershipPolicy:
    """Validated workflow-trigger ownership declared by the repository."""

    current_paths: frozenset[str]
    reserved_paths: frozenset[str]
    governance_paths: frozenset[str]

    @property
    def security_trigger_paths(self) -> frozenset[str]:
        return self.current_paths | self.reserved_paths | self.governance_paths


def _indent(line: str) -> int:
    return len(line) - len(line.lstrip(" "))


def _structural_workflow_lines(lines: list[str]) -> list[str]:
    """Blank block-scalar bodies while preserving workflow line numbers."""
    structural: list[str] = []
    block_parent_indent: int | None = None
    for line in lines:
        stripped = line.strip()
        indentation = _indent(line)
        if block_parent_indent is not None:
            if not stripped or indentation > block_parent_indent:
                structural.append("")
                continue
            block_parent_indent = None
        structural.append(line)
        if stripped and _BLOCK_SCALAR_RE.search(stripped):
            block_parent_indent = indentation
    return structural


def _validate_supported_workflow_syntax(
    workflow_path: str,
    lines: list[str],
) -> list[str]:
    """Reject YAML forms that could hide security-sensitive mapping keys."""
    errors: list[str] = []
    for index, line in enumerate(lines):
        stripped = line.strip()
        if not stripped or stripped.startswith("#"):
            continue
        if _QUOTED_MAPPING_KEY_RE.search(stripped):
            errors.append(
                f"{workflow_path}:{index + 1}: quoted mapping keys are "
                "unsupported by the security validator"
            )
        if _FLOW_MAPPING_RE.search(stripped):
            errors.append(
                f"{workflow_path}:{index + 1}: flow mappings are unsupported "
                "by the security validator"
            )
        if re.match(r"^\?\s+", stripped):
            errors.append(
                f"{workflow_path}:{index + 1}: explicit mapping keys are "
                "unsupported by the security validator"
            )
    return errors


def _mapping_values(
    lines: list[str],
    *,
    key: str,
    indent: int,
    start: int,
    end: int,
) -> list[str]:
    pattern = re.compile(
        rf"^ {{{indent}}}{re.escape(key)}\s*:\s*(.*?)\s*(?:#.*)?$"
    )
    return [
        match.group(1)
        for line in lines[start:end]
        if (match := pattern.fullmatch(line)) is not None
    ]


def _job_ranges(
    lines: list[str], workflow_path: str, errors: list[str],
) -> dict[str, tuple[int, int]]:
    jobs_headers = [
        index for index, line in enumerate(lines)
        if _indent(line) == 0
        and re.fullmatch(r"jobs\s*:\s*(?:#.*)?", line.strip())
    ]
    if len(jobs_headers) != 1:
        errors.append(f"{workflow_path}: expected exactly one top-level jobs map")
        return {}
    jobs_start = jobs_headers[0]
    jobs_end = len(lines)
    starts: list[tuple[str, int]] = []
    job_pattern = re.compile(r" {2}([A-Za-z0-9_-]+)\s*:\s*(?:#.*)?$")
    for index in range(jobs_start + 1, len(lines)):
        line = lines[index]
        stripped = line.strip()
        if not stripped or stripped.startswith("#"):
            continue
        if _indent(line) == 0:
            jobs_end = index
            break
        match = job_pattern.fullmatch(line)
        if match is not None:
            starts.append((match.group(1), index))
    ranges: dict[str, tuple[int, int]] = {}
    for position, (job, start) in enumerate(starts):
        end = starts[position + 1][1] if position + 1 < len(starts) else jobs_end
        if job in ranges:
            errors.append(f"{workflow_path}: duplicate job id {job!r}")
        ranges[job] = (start, end)
    if not ranges:
        errors.append(f"{workflow_path}: jobs map must not be empty")
    return ranges


def _direct_value(
    lines: list[str],
    job_range: tuple[int, int],
    key: str,
    *,
    indent: int = 4,
) -> str | None:
    values = _mapping_values(
        lines,
        key=key,
        indent=indent,
        start=job_range[0] + 1,
        end=job_range[1],
    )
    return values[0] if len(values) == 1 else None


def _child_mapping(
    lines: list[str],
    parent_range: tuple[int, int],
    parent_key: str,
    *,
    parent_indent: int,
) -> dict[str, str] | None:
    header_pattern = re.compile(
        rf"^ {{{parent_indent}}}{re.escape(parent_key)}\s*:\s*(?:#.*)?$"
    )
    headers = [
        index
        for index in range(parent_range[0] + 1, parent_range[1])
        if header_pattern.fullmatch(lines[index]) is not None
    ]
    if len(headers) != 1:
        return None
    values: dict[str, str] = {}
    entry_pattern = re.compile(
        rf"^ {{{parent_indent + 2}}}([A-Za-z0-9_-]+)\s*:\s*(.*?)\s*$"
    )
    for index in range(headers[0] + 1, parent_range[1]):
        line = lines[index]
        stripped = line.strip()
        if not stripped or stripped.startswith("#"):
            continue
        if _indent(line) <= parent_indent:
            break
        match = entry_pattern.fullmatch(line)
        if match is None or match.group(1) in values:
            return None
        values[match.group(1)] = match.group(2)
    return values


def _matrix_block_lines(
    lines: list[str], job_range: tuple[int, int],
) -> tuple[str, ...] | None:
    headers = [
        index for index in range(job_range[0] + 1, job_range[1])
        if _indent(lines[index]) == 6 and lines[index].strip() == "matrix:"
    ]
    if len(headers) != 1:
        return None
    block: list[str] = ["matrix:"]
    for line in lines[headers[0] + 1:job_range[1]]:
        stripped = line.strip()
        if not stripped:
            continue
        if _indent(line) <= 6:
            break
        block.append(stripped)
    return tuple(block)


def _direct_needs(
    lines: list[str], job_range: tuple[int, int],
) -> frozenset[str] | None:
    values = _mapping_values(
        lines,
        key="needs",
        indent=4,
        start=job_range[0] + 1,
        end=job_range[1],
    )
    if len(values) != 1:
        return None
    if values[0]:
        return frozenset({values[0]})
    header = next(
        index
        for index in range(job_range[0] + 1, job_range[1])
        if re.fullmatch(r" {4}needs\s*:\s*(?:#.*)?$", lines[index])
        is not None
    )
    needs: set[str] = set()
    for index in range(header + 1, job_range[1]):
        line = lines[index]
        stripped = line.strip()
        if not stripped or stripped.startswith("#"):
            continue
        if _indent(line) <= 4:
            break
        match = re.fullmatch(r" {6}-\s+([A-Za-z0-9_-]+)\s*", line)
        if match is None or match.group(1) in needs:
            return None
        needs.add(match.group(1))
    return frozenset(needs)


def _named_step_range(
    lines: list[str],
    job_range: tuple[int, int],
    name: str,
) -> tuple[int, int] | None:
    starts = [
        index
        for index in range(job_range[0] + 1, job_range[1])
        if re.fullmatch(r" {6}-\s+name\s*:\s*(.*?)\s*", lines[index])
        is not None
    ]
    matches = [
        index for index in starts
        if re.fullmatch(r" {6}-\s+name\s*:\s*(.*?)\s*", lines[index])
        .group(1) == name
    ]
    if len(matches) != 1:
        return None
    start = matches[0]
    following = [index for index in starts if index > start]
    return start, following[0] if following else job_range[1]


def _stripped_lines(
    lines: list[str], block_range: tuple[int, int],
) -> list[str]:
    return [
        line.strip() for line in lines[block_range[0]:block_range[1]]
        if line.strip()
    ]


def _reviewed_block_sha256(
    raw_lines: list[str], block_range: tuple[int, int],
) -> str:
    payload = "\n".join(_stripped_lines(raw_lines, block_range)) + "\n"
    return hashlib.sha256(payload.encode("utf-8")).hexdigest()


def _normalized_workflow_text(text: str) -> str:
    """Normalize only transport-level newlines for exact workflow review."""
    normalized = text.replace("\r\n", "\n").replace("\r", "\n")
    return normalized if normalized.endswith("\n") else normalized + "\n"


def _workflow_sha256(text: str) -> str:
    return hashlib.sha256(
        _normalized_workflow_text(text).encode("utf-8")
    ).hexdigest()


def render_force_full_bootstrap(text: str) -> str:
    """Render the one reviewed seed workflow from the active workflow."""
    normalized = _normalized_workflow_text(text)
    bootstrap_marker = "    name: Force full CI bootstrap\n"
    if bootstrap_marker in normalized:
        if normalized.count(bootstrap_marker) != 1:
            raise ValueError("force-full bootstrap marker must occur once")
        return normalized

    markers = ("  lane:\n", "\n  quality:\n", "\n  promotion-gate:\n")
    if any(normalized.count(marker) != 1 for marker in markers):
        raise ValueError(
            "active workflow must contain one lane, quality, and promotion gate"
        )
    lane_start = normalized.index(markers[0])
    quality_start = normalized.index(markers[1], lane_start)
    promotion_start = normalized.index(markers[2], quality_start)
    phase_marker = "\n  phase-a0:\n"
    next_marker = "\n  vector-store-smoke:\n"
    if normalized.count(phase_marker) != 1 or normalized.count(next_marker) != 1:
        raise ValueError(
            "active workflow must contain one phase-a0 and vector-store-smoke job"
        )
    bootstrap = (
        normalized[:lane_start]
        + _BOOTSTRAP_LANE_TEXT
        + normalized[quality_start:promotion_start]
    )
    if bootstrap.count(phase_marker) != 1 or bootstrap.count(next_marker) != 1:
        raise ValueError(
            "bootstrap must contain one phase-a0 and vector-store-smoke job"
        )
    phase_start = bootstrap.index(phase_marker)
    phase_end = bootstrap.index(next_marker, phase_start)
    phase_block = bootstrap[phase_start:phase_end]
    candidate_ref = f"          ref: {_CANDIDATE_REF}\n"
    bootstrap_ref = f"          ref: {_PHASE_A0_BOOTSTRAP_REF}\n"
    if phase_block.count(candidate_ref) != 1 or bootstrap_ref in phase_block:
        raise ValueError(
            "active phase-a0 must contain one exact candidate checkout ref"
        )
    phase_block = phase_block.replace(candidate_ref, bootstrap_ref, 1)
    return bootstrap[:phase_start] + phase_block + bootstrap[phase_end:]


def _require_markers(
    *,
    workflow_path: str,
    context: str,
    lines: list[str],
    markers: Sequence[str],
    errors: list[str],
) -> None:
    missing = [marker for marker in markers if marker not in lines]
    if missing:
        errors.append(
            f"{workflow_path}: {context} is missing stable marker(s): "
            + ", ".join(repr(marker) for marker in missing)
        )


def _checkout_step_ranges(
    lines: list[str], job_range: tuple[int, int],
) -> list[tuple[int, int]]:
    step_starts = [
        index
        for index in range(job_range[0] + 1, job_range[1])
        if _indent(lines[index]) == 6 and lines[index].strip().startswith("- ")
    ]
    checkout_ranges: list[tuple[int, int]] = []
    for position, start in enumerate(step_starts):
        end = (
            step_starts[position + 1]
            if position + 1 < len(step_starts)
            else job_range[1]
        )
        if any(
            re.match(r"^actions/checkout@", match.group(1)) is not None
            for line in lines[start:end]
            if (match := re.match(
                r"^\s*uses\s*:\s*([^#\s]+)", line,
            )) is not None
        ):
            checkout_ranges.append((start, end))
    return checkout_ranges


def _validate_ci_triggers(
    lines: list[str], top_on: int, errors: list[str],
) -> None:
    expected = {
        "pull_request": (),
        "merge_group": ("types: [checks_requested]",),
        "push": ("branches: [main]",),
        "workflow_dispatch": (),
    }
    event_pattern = re.compile(
        r" {2}([A-Za-z0-9_-]+)\s*:\s*(.*?)\s*(?:#.*)?$"
    )
    starts: list[tuple[str, str, int]] = []
    for index in range(top_on + 1, len(lines)):
        line = lines[index]
        stripped = line.strip()
        if not stripped or stripped.startswith("#"):
            continue
        if _indent(line) == 0:
            break
        if _indent(line) == 2:
            match = event_pattern.fullmatch(line)
            if match is None:
                errors.append(
                    f"{CI_WORKFLOW_PATH}: CI triggers must use canonical "
                    "block mappings"
                )
                return
            starts.append((match.group(1), match.group(2), index))

    observed: dict[str, tuple[str, ...]] = {}
    for position, (event, value, start) in enumerate(starts):
        end = starts[position + 1][2] if position + 1 < len(starts) else len(lines)
        children: list[str] = []
        for index in range(start + 1, end):
            line = lines[index]
            stripped = line.strip()
            if not stripped or stripped.startswith("#"):
                continue
            if _indent(line) == 0:
                break
            children.append(stripped)
        if event in observed or value:
            errors.append(
                f"{CI_WORKFLOW_PATH}: CI triggers must match the exact "
                "reviewed event contract"
            )
            return
        observed[event] = tuple(children)
    if observed != expected:
        errors.append(
            f"{CI_WORKFLOW_PATH}: CI triggers must be exactly bare "
            "pull_request, merge_group checks_requested, push main, and "
            "bare workflow_dispatch"
        )


def validate_merge_group_trigger(workflow_path: str, text: str) -> list[str]:
    """Require queue candidates for workflows used by promotion review."""
    lines = _structural_workflow_lines(text.splitlines())
    errors: list[str] = []
    top_on = [
        index for index, line in enumerate(lines)
        if _indent(line) == 0
        and re.fullmatch(r"on\s*:\s*(?:#.*)?", line.strip())
    ]
    if len(top_on) != 1:
        return [f"{workflow_path}: expected exactly one top-level 'on' map"]
    indices = _mapping_line_indices(
        lines,
        key="merge_group",
        parent_start=top_on[0],
        parent_indent=0,
    )
    if len(indices) != 1:
        return [
            f"{workflow_path}: merge_group checks_requested trigger is required"
        ]
    children: list[str] = []
    for line in lines[indices[0] + 1:]:
        stripped = line.strip()
        if not stripped or stripped.startswith("#"):
            continue
        if _indent(line) <= 2:
            break
        children.append(stripped)
    if children != ["types: [checks_requested]"]:
        errors.append(
            f"{workflow_path}: merge_group must use only checks_requested"
        )
    return errors


def _validator_python_tree(
    raw_lines: list[str],
    step_range: tuple[int, int],
    errors: list[str],
) -> ast.Module | None:
    starts = [
        index for index in range(step_range[0], step_range[1])
        if raw_lines[index].strip() == "python - <<'PY'"
    ]
    if len(starts) != 1:
        errors.append(
            f"{CI_WORKFLOW_PATH}: promotion validator must contain one "
            "literal Python heredoc"
        )
        return None
    ends = [
        index for index in range(starts[0] + 1, step_range[1])
        if raw_lines[index].strip() == "PY"
    ]
    if len(ends) != 1:
        errors.append(
            f"{CI_WORKFLOW_PATH}: promotion validator Python heredoc "
            "must terminate exactly once"
        )
        return None
    source = textwrap.dedent("\n".join(raw_lines[starts[0] + 1:ends[0]]))
    try:
        return ast.parse(source)
    except SyntaxError as exc:
        errors.append(
            f"{CI_WORKFLOW_PATH}: promotion validator Python is invalid: "
            f"line {exc.lineno}"
        )
        return None


def _has_exact_terminal_rejection(tree: ast.Module) -> bool:
    candidates = [
        node for node in ast.walk(tree)
        if isinstance(node, ast.If)
        and isinstance(node.test, ast.Name)
        and node.test.id == "errors"
    ]
    if len(candidates) != 1 or len(candidates[0].body) != 2:
        return False
    print_statement, raise_statement = candidates[0].body
    if (
        not isinstance(print_statement, ast.Expr)
        or not isinstance(print_statement.value, ast.Call)
        or not isinstance(print_statement.value.func, ast.Name)
        or print_statement.value.func.id != "print"
        or len(print_statement.value.args) != 2
        or not isinstance(print_statement.value.args[0], ast.Constant)
        or print_statement.value.args[0].value != "CI promotion rejected:"
        or not isinstance(print_statement.value.args[1], ast.Starred)
        or not isinstance(print_statement.value.args[1].value, ast.Name)
        or print_statement.value.args[1].value.id != "errors"
    ):
        return False
    keywords = {keyword.arg: keyword.value
                for keyword in print_statement.value.keywords}
    separator = keywords.get("sep")
    destination = keywords.get("file")
    if (
        set(keywords) != {"sep", "file"}
        or not isinstance(separator, ast.Constant)
        or separator.value != "\n- "
        or not isinstance(destination, ast.Attribute)
        or destination.attr != "stderr"
        or not isinstance(destination.value, ast.Name)
        or destination.value.id != "sys"
    ):
        return False
    if (
        not isinstance(raise_statement, ast.Raise)
        or not isinstance(raise_statement.exc, ast.Call)
        or not isinstance(raise_statement.exc.func, ast.Name)
        or raise_statement.exc.func.id != "SystemExit"
        or len(raise_statement.exc.args) != 1
        or not isinstance(raise_statement.exc.args[0], ast.Constant)
        or raise_statement.exc.args[0].value != 1
        or raise_statement.cause is not None
    ):
        return False
    return True


def _allowed_group_assignment(tree: ast.Module) -> frozenset[str] | None:
    assignments = [
        node for node in ast.walk(tree)
        if isinstance(node, ast.Assign)
        and len(node.targets) == 1
        and isinstance(node.targets[0], ast.Name)
        and node.targets[0].id == "allowed_groups"
    ]
    if len(assignments) != 1:
        return None
    try:
        value = ast.literal_eval(assignments[0].value)
    except (ValueError, TypeError):
        return None
    if (
        not isinstance(value, set)
        or any(not isinstance(item, str) for item in value)
    ):
        return None
    return frozenset(value)


def _validate_force_full_bootstrap(
    raw_lines: list[str],
    lines: list[str],
    jobs: dict[str, tuple[int, int]],
    errors: list[str],
) -> None:
    """Validate the safe checkpoint that seeds base-trusted classifier files."""
    expected_jobs = _EXPECTED_EXECUTION_JOBS | {"lane"}
    if set(jobs) != expected_jobs:
        errors.append(
            f"{CI_WORKFLOW_PATH}: force-full bootstrap jobs are incomplete "
            "or unexpected"
        )
        return
    lane_range = jobs["lane"]
    if tuple(_stripped_lines(raw_lines, lane_range)) != _BOOTSTRAP_LANE_LINES:
        errors.append(
            f"{CI_WORKFLOW_PATH}: force-full bootstrap lane must match the "
            "exact reviewed seed contract"
        )
    if _checkout_step_ranges(lines, lane_range):
        errors.append(
            f"{CI_WORKFLOW_PATH}: force-full bootstrap lane must not checkout "
            "or execute repository code"
        )

    quality_lines = _stripped_lines(lines, jobs["quality"])
    if quality_lines.count("run: python tools/check_ci_security.py") != 1:
        errors.append(
            f"{CI_WORKFLOW_PATH}: quality must invoke check_ci_security.py once"
        )
    for job in ("quality", "evaluation-smoke"):
        if _direct_value(lines, jobs[job], "if") is not None:
            errors.append(
                f"{CI_WORKFLOW_PATH}: bootstrap {job} must not be conditional"
            )
        if _direct_needs(lines, jobs[job]):
            errors.append(
                f"{CI_WORKFLOW_PATH}: bootstrap {job} must not depend on "
                "another job"
            )
    for job, output in _HEAVY_JOB_OUTPUTS.items():
        expected_if = (
            "${{ always() && needs.lane.outputs."
            f"{output} != 'false' }}}}"
        )
        if _direct_value(lines, jobs[job], "if") != expected_if:
            errors.append(
                f"{CI_WORKFLOW_PATH}: bootstrap {job} must retain its "
                "fail-closed guard"
            )
        if _direct_needs(lines, jobs[job]) != frozenset({"lane"}):
            errors.append(
                f"{CI_WORKFLOW_PATH}: bootstrap {job} must directly need lane"
            )
    if _direct_value(lines, jobs["unit-linux"], "if") != "${{ always() }}":
        errors.append(
            f"{CI_WORKFLOW_PATH}: bootstrap unit-linux must always run"
        )
    if _direct_needs(lines, jobs["unit-linux"]) != frozenset({"lane"}):
        errors.append(
            f"{CI_WORKFLOW_PATH}: bootstrap unit-linux must directly need lane"
        )
    if _stripped_lines(lines, jobs["unit-linux"]).count(
        f"python-version: {_UNIT_LINUX_MATRIX}"
    ) != 1:
        errors.append(
            f"{CI_WORKFLOW_PATH}: bootstrap unit-linux must retain the "
            "full fallback matrix"
        )

    observed_matrix_jobs: set[str] = set()
    for job, job_range in jobs.items():
        matrix_values = _mapping_values(
            lines,
            key="matrix",
            indent=6,
            start=job_range[0] + 1,
            end=job_range[1],
        )
        if not matrix_values:
            continue
        observed_matrix_jobs.add(job)
        fail_fast = _mapping_values(
            lines,
            key="fail-fast",
            indent=6,
            start=job_range[0] + 1,
            end=job_range[1],
        )
        if fail_fast != ["false"]:
            errors.append(
                f"{CI_WORKFLOW_PATH}: bootstrap {job} matrix must set "
                "fail-fast: false"
            )
    if observed_matrix_jobs != set(_MATRIX_JOBS):
        errors.append(
            f"{CI_WORKFLOW_PATH}: bootstrap matrix jobs are incomplete or "
            "unexpected"
        )
    for job, expected_block in _EXPECTED_MATRIX_BLOCKS.items():
        if _matrix_block_lines(lines, jobs[job]) != expected_block:
            errors.append(
                f"{CI_WORKFLOW_PATH}: bootstrap {job} matrix must match the "
                "exact reviewed cells"
            )

    for job, job_range in jobs.items():
        if job == "lane":
            continue
        expected_ref = (
            _PHASE_A0_BOOTSTRAP_REF
            if job == "phase-a0"
            else _CANDIDATE_REF
        )
        checkouts = _checkout_step_ranges(lines, job_range)
        if len(checkouts) != 1:
            errors.append(
                f"{CI_WORKFLOW_PATH}: bootstrap {job} must have one "
                "candidate checkout"
            )
        for checkout in checkouts:
            checkout_with = _child_mapping(
                lines, checkout, "with", parent_indent=8
            )
            if (
                checkout_with is None
                or checkout_with.get("ref") != expected_ref
                or checkout_with.get("persist-credentials") != "false"
                or set(checkout_with).difference({
                    "fetch-depth", "persist-credentials", "ref",
                })
                or (
                    "fetch-depth" in checkout_with
                    and checkout_with["fetch-depth"] != "0"
                )
            ):
                errors.append(
                    f"{CI_WORKFLOW_PATH}: bootstrap {job} checkout must have "
                    "one exact approved execution ref, disabled credentials, "
                    "and only the approved fetch-depth option"
                )


def validate_ci_topology(text: str) -> list[str]:
    """Validate the fail-closed CI classifier, execution, and aggregate graph."""
    workflow_path = CI_WORKFLOW_PATH
    raw_lines = text.splitlines()
    lines = _structural_workflow_lines(raw_lines)
    errors: list[str] = []

    top_on = [
        index for index, line in enumerate(lines)
        if _indent(line) == 0
        and re.fullmatch(r"on\s*:\s*(?:#.*)?", line.strip())
    ]
    if len(top_on) != 1:
        errors.append(f"{workflow_path}: expected exactly one top-level 'on' map")
    else:
        if _mapping_line_indices(
            lines,
            key="pull_request_target",
            parent_start=top_on[0],
            parent_indent=0,
        ):
            errors.append(f"{workflow_path}: pull_request_target is forbidden")
        _validate_ci_triggers(lines, top_on[0], errors)

    if any(
        re.match(r"continue-on-error\s*:", line.strip()) is not None
        for line in lines
    ):
        errors.append(f"{workflow_path}: continue-on-error is forbidden")

    top_env = _child_mapping(
        lines, (-1, len(lines)), "env", parent_indent=0
    )
    if top_env != _EXPECTED_TOP_ENV:
        errors.append(
            f"{workflow_path}: top-level environment must match the exact "
            "reviewed non-replacement contract"
        )

    jobs = _job_ranges(lines, workflow_path, errors)
    if (
        "lane" in jobs
        and _direct_value(lines, jobs["lane"], "name")
        == "Force full CI bootstrap"
    ):
        if _workflow_sha256(text) != _BOOTSTRAP_WORKFLOW_SHA256:
            errors.append(
                f"{workflow_path}: force-full bootstrap differs from the "
                "reviewed exact workflow"
            )
        _validate_force_full_bootstrap(raw_lines, lines, jobs, errors)
        return errors

    if _workflow_sha256(text) != _ACTIVE_WORKFLOW_SHA256:
        errors.append(
            f"{workflow_path}: active CI differs from the reviewed exact "
            "workflow"
        )
    required_jobs = _EXPECTED_EXECUTION_JOBS | {"lane", "promotion-gate"}
    missing_jobs = sorted(required_jobs.difference(jobs))
    if missing_jobs:
        errors.append(
            f"{workflow_path}: missing required jobs: {', '.join(missing_jobs)}"
        )
        return errors

    lane_range = jobs["lane"]
    if _reviewed_block_sha256(raw_lines, lane_range) != _ACTIVE_LANE_SHA256:
        errors.append(
            f"{workflow_path}: active classifier lane differs from the "
            "reviewed exact block"
        )
    lane_outputs = _child_mapping(
        lines, lane_range, "outputs", parent_indent=4)
    if lane_outputs != _EXPECTED_LANE_OUTPUTS:
        errors.append(
            f"{workflow_path}: lane outputs must match the stable "
            "fail-closed contract"
        )

    event_step = _named_step_range(
        lines, lane_range, "Bind exact event identities")
    trusted_checkout = _named_step_range(
        lines, lane_range, "Check out the exact trusted revision")
    candidate_fetch = _named_step_range(
        lines, lane_range, "Fetch and verify the exact candidate revision")
    classifier_step = _named_step_range(
        lines, lane_range, "Run the exact trusted classifier")
    for label, step in (
        ("event identity step", event_step),
        ("trusted checkout step", trusted_checkout),
        ("candidate verification step", candidate_fetch),
        ("trusted classifier step", classifier_step),
    ):
        if step is None:
            errors.append(f"{workflow_path}: lane is missing its {label}")

    lane_checkouts = _checkout_step_ranges(lines, lane_range)
    if trusted_checkout is None or lane_checkouts != [trusted_checkout]:
        errors.append(
            f"{workflow_path}: lane must have exactly one trusted checkout"
        )

    if event_step is not None:
        _require_markers(
            workflow_path=workflow_path,
            context="event identity step",
            lines=_stripped_lines(raw_lines, event_step),
            markers=(
                'base_sha = pull["base"]["sha"]',
                'head_sha = pull["head"]["sha"]',
                "candidate_sha = github_sha",
                "trusted_sha = base_sha",
                'candidate_ref = f"refs/pull/{pull[\'number\']}/merge"',
                'if github_ref != candidate_ref:',
                '"pull-request GITHUB_REF is not its exact merge ref"',
                'base_sha = group["base_sha"]',
                'head_sha = group["head_sha"]',
                'if github_sha != head_sha or github_ref != group["head_ref"]:',
                '"merge-group SHA/ref does not match the queue candidate"',
                'elif event_name == "workflow_dispatch":',
                'if github_ref != "refs/heads/main":',
                '"manual CI dispatch is restricted to refs/heads/main"',
            ),
            errors=errors,
        )
    if trusted_checkout is not None:
        trusted_lines = _stripped_lines(lines, trusted_checkout)
        _require_markers(
            workflow_path=workflow_path,
            context="trusted checkout step",
            lines=trusted_lines,
            markers=(
                "repository: ${{ github.repository }}",
                "ref: ${{ steps.event.outputs.trusted_sha }}",
                "fetch-depth: 0",
                "persist-credentials: false",
            ),
            errors=errors,
        )
        trusted_with = _child_mapping(
            lines, trusted_checkout, "with", parent_indent=8
        )
        if trusted_with != {
            "repository": "${{ github.repository }}",
            "ref": "${{ steps.event.outputs.trusted_sha }}",
            "fetch-depth": "0",
            "persist-credentials": "false",
        }:
            errors.append(
                f"{workflow_path}: trusted checkout with-map must match the "
                "exact base-repository contract"
            )
    if candidate_fetch is not None:
        _require_markers(
            workflow_path=workflow_path,
            context="candidate verification step",
            lines=_stripped_lines(raw_lines, candidate_fetch),
            markers=(
                'refspecs=("+$HEAD_FETCH_REF:refs/remotes/ci-head")',
                'if [ "$CANDIDATE_REF" != "$HEAD_FETCH_REF" ]; then',
                'refspecs+=("+$CANDIDATE_REF:refs/remotes/ci-candidate")',
                '"${refspecs[@]}"',
                "git --no-replace-objects \\",
                'test "$(git --no-replace-objects rev-parse refs/remotes/ci-head^{commit})" = "$HEAD_SHA"',
                'test "$(git --no-replace-objects rev-parse "$BASE_SHA^{commit}")" = "$BASE_SHA"',
                'test "$(git --no-replace-objects rev-parse "$CANDIDATE_SHA^{commit}")" = "$CANDIDATE_SHA"',
                'if [ "$EVENT_NAME" = "pull_request" ]; then',
                'git --no-replace-objects rev-list --parents -n 1 "$CANDIDATE_SHA"',
                'test "$parent_one" = "$BASE_SHA"',
                'test "$parent_two" = "$HEAD_SHA"',
                'test -z "${extra:-}"',
            ),
            errors=errors,
        )
    if classifier_step is not None:
        classifier_lines = _stripped_lines(raw_lines, classifier_step)
        _require_markers(
            workflow_path=workflow_path,
            context="trusted classifier step",
            lines=classifier_lines,
            markers=(
                'trusted_dir="$RUNNER_TEMP/ci-promotion-trusted"',
                'git --no-replace-objects show "$TRUSTED_SHA:tools/ci_promotion.py" \\',
                '> "$trusted_dir/tools/ci_promotion.py"',
                'git --no-replace-objects show "$TRUSTED_SHA:ci-risk-policy.json" \\',
                '> "$trusted_dir/ci-risk-policy.json"',
                'python -I "$trusted_dir/tools/ci_promotion.py" classify \\',
                '--candidate-sha "$CANDIDATE_SHA" \\',
            ),
            errors=errors,
        )
    lane_invocations = [
        line for line in _stripped_lines(raw_lines, lane_range)
        if re.match(r"^(?:python|python3)\b.*ci_promotion\.py", line)
    ]
    if lane_invocations != [
        'python -I "$trusted_dir/tools/ci_promotion.py" classify \\'
    ]:
        errors.append(
            f"{workflow_path}: classifier may execute only the trusted "
            "temporary tool copy"
        )

    quality_lines = _stripped_lines(lines, jobs["quality"])
    if quality_lines.count("run: python tools/check_ci_security.py") != 1:
        errors.append(
            f"{workflow_path}: quality must invoke check_ci_security.py once"
        )

    for job, output in _HEAVY_JOB_OUTPUTS.items():
        expected_if = (
            "${{ always() && needs.lane.outputs."
            f"{output} != 'false' }}}}"
        )
        if _direct_value(lines, jobs[job], "if") != expected_if:
            errors.append(
                f"{workflow_path}: {job} must use its exact fail-closed guard"
            )
        if _direct_needs(lines, jobs[job]) != frozenset({"lane"}):
            errors.append(f"{workflow_path}: {job} must directly need lane")

    if _direct_value(lines, jobs["unit-linux"], "if") != "${{ always() }}":
        errors.append(
            f"{workflow_path}: unit-linux must run with exact always() guard"
        )
    if _direct_needs(lines, jobs["unit-linux"]) != frozenset({"lane"}):
        errors.append(f"{workflow_path}: unit-linux must directly need lane")
    unit_linux_lines = _stripped_lines(lines, jobs["unit-linux"])
    if unit_linux_lines.count(f"python-version: {_UNIT_LINUX_MATRIX}") != 1:
        errors.append(
            f"{workflow_path}: unit-linux must retain its fail-closed "
            "dynamic Python matrix"
        )

    observed_matrix_jobs: set[str] = set()
    for job, job_range in jobs.items():
        matrix_values = _mapping_values(
            lines,
            key="matrix",
            indent=6,
            start=job_range[0] + 1,
            end=job_range[1],
        )
        if matrix_values:
            observed_matrix_jobs.add(job)
            fail_fast = _mapping_values(
                lines,
                key="fail-fast",
                indent=6,
                start=job_range[0] + 1,
                end=job_range[1],
            )
            if fail_fast != ["false"]:
                errors.append(
                    f"{workflow_path}: {job} matrix must set fail-fast: false"
                )
    if observed_matrix_jobs != set(_MATRIX_JOBS):
        errors.append(
            f"{workflow_path}: matrix job set is incomplete or unexpected"
        )
    for job, expected_block in _EXPECTED_MATRIX_BLOCKS.items():
        if _matrix_block_lines(lines, jobs[job]) != expected_block:
            errors.append(
                f"{workflow_path}: {job} matrix must match the exact "
                "reviewed cells"
            )

    for job, job_range in jobs.items():
        if job in {"lane", "promotion-gate"}:
            continue
        expected_ref = _CANDIDATE_REF
        checkouts = _checkout_step_ranges(lines, job_range)
        if job in _EXPECTED_EXECUTION_JOBS and len(checkouts) != 1:
            errors.append(
                f"{workflow_path}: {job} must have one candidate checkout"
            )
        for checkout in checkouts:
            checkout_with = _child_mapping(
                lines, checkout, "with", parent_indent=8
            )
            if (
                checkout_with is None
                or checkout_with.get("ref") != expected_ref
                or checkout_with.get("persist-credentials") != "false"
                or set(checkout_with).difference({
                    "fetch-depth", "persist-credentials", "ref",
                })
                or (
                    "fetch-depth" in checkout_with
                    and checkout_with["fetch-depth"] != "0"
                )
            ):
                errors.append(
                    f"{workflow_path}: {job} checkout must have one exact "
                    "approved execution ref, disabled credentials, and only "
                    "the approved fetch-depth option"
                )

    promotion_range = jobs["promotion-gate"]
    if (
        _reviewed_block_sha256(raw_lines, promotion_range)
        != _ACTIVE_PROMOTION_SHA256
    ):
        errors.append(
            f"{workflow_path}: active promotion gate differs from the "
            "reviewed exact block"
        )
    if _direct_value(lines, promotion_range, "name") != "CI promotion gate":
        errors.append(
            f"{workflow_path}: promotion-gate display name must be "
            "'CI promotion gate'"
        )
    if _direct_value(lines, promotion_range, "if") != "${{ always() }}":
        errors.append(
            f"{workflow_path}: promotion-gate must use exact always() guard"
        )
    expected_needs = frozenset(set(jobs).difference({"promotion-gate"}))
    if _direct_needs(lines, promotion_range) != expected_needs:
        errors.append(
            f"{workflow_path}: promotion-gate must directly need every "
            "other job exactly once"
        )

    validator_step = _named_step_range(
        lines,
        promotion_range,
        "Validate the exact promotion decision and every job result",
    )
    if validator_step is None:
        errors.append(
            f"{workflow_path}: promotion-gate validator step is missing"
        )
    else:
        if _direct_value(
            lines, validator_step, "if", indent=8,
        ) != "${{ always() }}":
            errors.append(
                f"{workflow_path}: promotion validator must use exact "
                "always() guard"
            )
        validator_lines = _stripped_lines(raw_lines, validator_step)
        _require_markers(
            workflow_path=workflow_path,
            context="promotion validator",
            lines=validator_lines,
            markers=(
                "NEEDS_JSON: ${{ toJSON(needs) }}",
                "EXPECTED_BASE_SHA: ${{ github.event.pull_request.base.sha || github.event.merge_group.base_sha || (github.event.before != '0000000000000000000000000000000000000000' && github.event.before) || github.sha }}",
                "EXPECTED_CANDIDATE_SHA: ${{ github.sha }}",
                "EXPECTED_HEAD_SHA: ${{ github.event.pull_request.head.sha || github.event.merge_group.head_sha || github.sha }}",
                "EXPECTED_TRUSTED_SHA: ${{ github.event.pull_request.base.sha || github.event.merge_group.base_sha || github.sha }}",
                'expected_needs = governed | {classifier}',
                "if not isinstance(needs, dict) or set(needs) != expected_needs:",
                'if lane.get("result") != "success":',
                'if outputs.get("classifier_ok") != "true":',
                "if not isinstance(requirements, dict) or set(requirements) != governed:",
                "if actual_result != expected_result:",
                "if outputs.get(output_name) != expected_flag:",
                "if pythons != expected_pythons:",
                '"candidate",',
                '"candidate_sha",',
                'os.environ["EXPECTED_CANDIDATE_SHA"],',
                "if sha_pattern.fullmatch(actual) is None or actual != expected:",
                'for label in ("policy_sha256", "decision_sha256"):',
                "or any(group not in allowed_groups for group in groups)",
            ),
            errors=errors,
        )
        validator_tree = _validator_python_tree(
            raw_lines, validator_step, errors)
        if validator_tree is not None:
            if not _has_exact_terminal_rejection(validator_tree):
                errors.append(
                    f"{workflow_path}: promotion validator must reject "
                    "accumulated errors on stderr and raise SystemExit(1)"
                )
            if _allowed_group_assignment(
                validator_tree,
            ) != _ALLOWED_RISK_GROUPS:
                errors.append(
                    f"{workflow_path}: promotion validator allowed_groups "
                    "must equal the exact classifier group vocabulary"
                )
    return errors


def validate_ci_promotion_contract(text: str) -> list[str]:
    """Keep the trusted tool's required candidate contract in lockstep."""
    errors: list[str] = []
    try:
        tree = ast.parse(text)
    except SyntaxError as exc:
        return [
            f"{CI_PROMOTION_TOOL_PATH}: invalid Python at line {exc.lineno}"
        ]

    schema_versions = [
        node.value.value
        for node in tree.body
        if isinstance(node, ast.Assign)
        and len(node.targets) == 1
        and isinstance(node.targets[0], ast.Name)
        and node.targets[0].id == "DECISION_SCHEMA_VERSION"
        and isinstance(node.value, ast.Constant)
    ]
    if schema_versions != [2]:
        errors.append(
            f"{CI_PROMOTION_TOOL_PATH}: candidate-bound decision schema "
            "must be version 2"
        )

    decision_classes = [
        node for node in tree.body
        if isinstance(node, ast.ClassDef) and node.name == "Decision"
    ]
    candidate_fields: list[ast.AnnAssign] = []
    if len(decision_classes) == 1:
        candidate_fields = [
            node for node in decision_classes[0].body
            if isinstance(node, ast.AnnAssign)
            and isinstance(node.target, ast.Name)
            and node.target.id == "candidate_sha"
        ]
    if len(candidate_fields) != 1:
        errors.append(
            f"{CI_PROMOTION_TOOL_PATH}: Decision must bind candidate_sha"
        )

    candidate_arguments = []
    for node in ast.walk(tree):
        if (
            isinstance(node, ast.Call)
            and isinstance(node.func, ast.Attribute)
            and node.func.attr == "add_argument"
            and node.args
            and isinstance(node.args[0], ast.Constant)
            and node.args[0].value == "--candidate-sha"
        ):
            candidate_arguments.append(node)
    if len(candidate_arguments) != 1 or not any(
        keyword.arg == "required"
        and isinstance(keyword.value, ast.Constant)
        and keyword.value.value is True
        for keyword in (
            candidate_arguments[0].keywords if candidate_arguments else []
        )
    ):
        errors.append(
            f"{CI_PROMOTION_TOOL_PATH}: classify CLI must require "
            "--candidate-sha exactly once"
        )

    markers = (
        '"candidate_sha",',
        '"candidate_sha": self.candidate_sha,',
        '("candidate_sha", decision.candidate_sha),',
        '("decision", _canonical_json(decision.payload())),',
        "candidate_sha=args.candidate_sha,",
        "_verify_pull_request_candidate(",
        '"GIT_NO_REPLACE_OBJECTS": "1",',
        '"--no-replace-objects",',
    )
    for marker in markers:
        if text.count(marker) < 1:
            errors.append(
                f"{CI_PROMOTION_TOOL_PATH}: candidate contract is missing "
                f"{marker!r}"
            )
    return errors


def _is_repository_path(value: object) -> bool:
    if not isinstance(value, str) or not value or "\\" in value:
        return False
    path = PurePosixPath(value)
    return (
        not path.is_absolute()
        and bool(path.parts)
        and all(part not in {"", ".", ".."} for part in path.parts)
        and path.as_posix() == value
        and not any(character in value for character in "*?[]")
    )


def _path_groups(
    value: object,
    *,
    field: str,
    errors: list[str],
) -> frozenset[str]:
    if not isinstance(value, dict) or not value:
        errors.append(f"{POLICY_PATH}: {field} must be a non-empty object")
        return frozenset()
    paths: set[str] = set()
    for group, group_paths in value.items():
        if not isinstance(group, str) or not group:
            errors.append(f"{POLICY_PATH}: {field} has an invalid group name")
            continue
        if not isinstance(group_paths, list) or not group_paths:
            errors.append(
                f"{POLICY_PATH}: {field}.{group} must be a non-empty list"
            )
            continue
        for path in group_paths:
            if not _is_repository_path(path):
                errors.append(
                    f"{POLICY_PATH}: {field}.{group} has invalid path {path!r}"
                )
                continue
            if path in paths:
                errors.append(
                    f"{POLICY_PATH}: {field} declares {path!r} more than once"
                )
            paths.add(path)
    return frozenset(paths)


def _path_list(
    value: object,
    *,
    field: str,
    errors: list[str],
) -> frozenset[str]:
    if not isinstance(value, list) or not value:
        errors.append(f"{POLICY_PATH}: {field} must be a non-empty list")
        return frozenset()
    paths: set[str] = set()
    for path in value:
        if not _is_repository_path(path):
            errors.append(f"{POLICY_PATH}: {field} has invalid path {path!r}")
            continue
        if path in paths:
            errors.append(f"{POLICY_PATH}: {field} repeats {path!r}")
        paths.add(path)
    return frozenset(paths)


class _DuplicatePolicyKeyError(ValueError):
    """Raised when the ownership manifest repeats a JSON object key."""


def _reject_duplicate_policy_pairs(
    pairs: list[tuple[str, Any]],
) -> dict[str, Any]:
    value: dict[str, Any] = {}
    for key, item in pairs:
        if key in value:
            raise _DuplicatePolicyKeyError(
                f"JSON object repeats key {key!r}")
        value[key] = item
    return value


def load_ownership_policy(
    root: Path,
) -> tuple[OwnershipPolicy | None, list[str]]:
    """Load and validate the future-extensible security ownership manifest."""
    errors: list[str] = []
    path = root / POLICY_PATH
    try:
        payload: Any = json.loads(
            path.read_text(encoding="utf-8"),
            object_pairs_hook=_reject_duplicate_policy_pairs,
        )
    except (OSError, json.JSONDecodeError, _DuplicatePolicyKeyError) as exc:
        return None, [f"{POLICY_PATH}: cannot load policy: {exc}"]
    if not isinstance(payload, dict):
        return None, [f"{POLICY_PATH}: top level must be an object"]
    unexpected = set(payload).difference(_POLICY_KEYS)
    missing = _POLICY_KEYS.difference(payload)
    if unexpected:
        errors.append(
            f"{POLICY_PATH}: unexpected fields: {', '.join(sorted(unexpected))}"
        )
    if missing:
        errors.append(
            f"{POLICY_PATH}: missing fields: {', '.join(sorted(missing))}"
        )
    schema_version = payload.get("schema_version")
    if (
        not isinstance(schema_version, int)
        or isinstance(schema_version, bool)
        or schema_version != POLICY_SCHEMA_VERSION
    ):
        errors.append(
            f"{POLICY_PATH}: schema_version must be integer "
            f"{POLICY_SCHEMA_VERSION}"
        )

    current = _path_groups(
        payload.get("current_implementation_owners"),
        field="current_implementation_owners",
        errors=errors,
    )
    reserved = _path_groups(
        payload.get("reserved_implementation_owners"),
        field="reserved_implementation_owners",
        errors=errors,
    )
    governance = _path_list(
        payload.get("governance_paths"),
        field="governance_paths",
        errors=errors,
    )
    overlaps = {
        path_value
        for paths in (current, reserved, governance)
        for path_value in paths
        if sum(path_value in candidate for candidate in (
            current, reserved, governance
        )) > 1
    }
    if overlaps:
        errors.append(
            f"{POLICY_PATH}: paths have more than one ownership role: "
            f"{', '.join(sorted(overlaps))}"
        )

    for label, expected, actual in (
        ("current owner", _REQUIRED_CURRENT_OWNERS, current),
        ("reserved owner", _REQUIRED_RESERVED_OWNERS, reserved),
        ("governance path", _REQUIRED_GOVERNANCE_PATHS, governance),
    ):
        for required in sorted(expected.difference(actual)):
            errors.append(f"{POLICY_PATH}: missing required {label}: {required}")

    for role, declared_paths in (
        ("current", current),
        ("governance", governance),
    ):
        for declared in sorted(declared_paths):
            declared_path = root / PurePosixPath(declared)
            if not declared_path.is_file():
                errors.append(
                    f"{POLICY_PATH}: declared {role} path is missing: "
                    f"{declared}"
                )

    return OwnershipPolicy(current, reserved, governance), errors


def _mapping_line_indices(
    lines: list[str],
    *,
    key: str,
    parent_start: int,
    parent_indent: int,
) -> list[int]:
    indices: list[int] = []
    pattern = re.compile(rf"^{re.escape(key)}\s*:\s*(?:#.*)?$")
    for index in range(parent_start + 1, len(lines)):
        line = lines[index]
        stripped = line.strip()
        if not stripped or stripped.startswith("#"):
            continue
        indentation = _indent(line)
        if indentation <= parent_indent:
            break
        if indentation == parent_indent + 2 and pattern.fullmatch(stripped):
            indices.append(index)
    return indices


def _single_mapping_line(
    lines: list[str],
    *,
    key: str,
    parent_start: int,
    parent_indent: int,
    context: str,
    errors: list[str],
) -> int | None:
    indices = _mapping_line_indices(
        lines,
        key=key,
        parent_start=parent_start,
        parent_indent=parent_indent,
    )
    if len(indices) != 1:
        errors.append(f"{context}: expected exactly one {key!r} mapping")
        return None
    return indices[0]


def _yaml_scalar(value: str) -> str:
    value = value.strip()
    if value.startswith(("'", '"')):
        parsed = ast.literal_eval(value)
        if not isinstance(parsed, str):
            raise ValueError("path entry is not text")
        return parsed
    return value.split(" #", 1)[0].strip()


def event_paths(
    workflow_text: str,
    event: str,
    *,
    workflow_path: str = SECURITY_WORKFLOW_PATH,
) -> tuple[frozenset[str], list[str]]:
    """Extract one top-level event's simple ``paths`` list."""
    lines = workflow_text.splitlines()
    errors: list[str] = []
    top = [
        index for index, line in enumerate(lines)
        if _indent(line) == 0 and re.fullmatch(
            r"on\s*:\s*(?:#.*)?", line.strip()
        )
    ]
    if len(top) != 1:
        return frozenset(), [
            f"{workflow_path}: expected exactly one top-level 'on' mapping"
        ]
    event_index = _single_mapping_line(
        lines,
        key=event,
        parent_start=top[0],
        parent_indent=0,
        context=workflow_path,
        errors=errors,
    )
    if event_index is None:
        return frozenset(), errors
    paths_index = _single_mapping_line(
        lines,
        key="paths",
        parent_start=event_index,
        parent_indent=2,
        context=f"{workflow_path}: {event}",
        errors=errors,
    )
    if paths_index is None:
        return frozenset(), errors

    paths: set[str] = set()
    for index in range(paths_index + 1, len(lines)):
        line = lines[index]
        stripped = line.strip()
        if not stripped or stripped.startswith("#"):
            continue
        if _indent(line) <= 4:
            break
        if _indent(line) != 6 or not stripped.startswith("- "):
            errors.append(
                f"{workflow_path}:{index + 1}: paths must be a simple list"
            )
            continue
        try:
            path = _yaml_scalar(stripped[2:])
        except (SyntaxError, ValueError) as exc:
            errors.append(
                f"{workflow_path}:{index + 1}: invalid path scalar: {exc}"
            )
            continue
        if not path:
            errors.append(f"{workflow_path}:{index + 1}: empty path filter")
        elif path in paths:
            errors.append(
                f"{workflow_path}:{index + 1}: duplicate path filter {path!r}"
            )
        else:
            paths.add(path)
    if not paths:
        errors.append(f"{workflow_path}: {event}.paths must not be empty")
    return frozenset(paths), errors


def _validate_permissions(workflow_path: str, lines: list[str]) -> list[str]:
    errors: list[str] = []
    declarations = [
        (index, _indent(line), line.strip())
        for index, line in enumerate(lines)
        if re.match(r"permissions\s*:", line.strip())
    ]
    if (
        len(declarations) != 1
        or declarations[0][1] != 0
        or re.fullmatch(
            r"permissions\s*:\s*(?:#.*)?", declarations[0][2]
        ) is None
    ):
        errors.append(
            f"{workflow_path}: require one top-level permissions mapping and "
            "no job-level overrides"
        )
        return errors
    start = declarations[0][0]
    entries: dict[str, str] = {}
    for index in range(start + 1, len(lines)):
        line = lines[index]
        stripped = line.strip()
        if not stripped or stripped.startswith("#"):
            continue
        indentation = _indent(line)
        if indentation == 0:
            break
        match = re.fullmatch(r"([A-Za-z-]+)\s*:\s*([^#\s]+)\s*(?:#.*)?", stripped)
        if indentation != 2 or match is None:
            errors.append(
                f"{workflow_path}:{index + 1}: invalid permissions entry"
            )
            continue
        if match.group(1) in entries:
            errors.append(
                f"{workflow_path}:{index + 1}: duplicate permissions entry"
            )
        entries[match.group(1)] = match.group(2)
    if entries != {"contents": "read"}:
        errors.append(
            f"{workflow_path}: permissions must be exactly 'contents: read'"
        )
    return errors


def _validate_action_pins(workflow_path: str, lines: list[str]) -> list[str]:
    errors: list[str] = []
    checkout_lines: list[tuple[int, int]] = []
    for index, line in enumerate(lines):
        match = re.match(r"^(\s*)(-\s+)?uses\s*:\s*([^#\s]+)", line)
        if match is None:
            continue
        reference = match.group(3)
        if reference.startswith("./"):
            continue
        if "@" not in reference or not _ACTION_SHA_RE.fullmatch(
            reference.rsplit("@", 1)[1]
        ):
            errors.append(
                f"{workflow_path}:{index + 1}: action must use a full "
                f"40-character commit SHA: {reference}"
            )
        if reference.startswith("actions/checkout@"):
            statement_indent = len(match.group(1))
            step_indent = (
                statement_indent if match.group(2) else statement_indent - 2
            )
            checkout_lines.append((index, max(step_indent, 0)))

    for index, step_indent in checkout_lines:
        values: list[str] = []
        for child_index in range(index + 1, len(lines)):
            line = lines[child_index]
            stripped = line.strip()
            if not stripped or stripped.startswith("#"):
                continue
            indentation = _indent(line)
            if indentation < step_indent or (
                indentation == step_indent and stripped.startswith("- ")
            ):
                break
            match = re.fullmatch(
                r"persist-credentials\s*:\s*([^#\s]+)\s*(?:#.*)?",
                stripped,
            )
            if match is not None and indentation > step_indent:
                values.append(match.group(1))
        if values != ["false"]:
            errors.append(
                f"{workflow_path}:{index + 1}: actions/checkout must set "
                "persist-credentials: false exactly once"
            )
    return errors


def validate_workflow(workflow_path: str, text: str) -> list[str]:
    """Validate action pins, read-only permissions, and checkout credentials."""
    lines = _structural_workflow_lines(text.splitlines())
    return (
        _validate_supported_workflow_syntax(workflow_path, lines)
        + _validate_permissions(workflow_path, lines)
        + _validate_action_pins(workflow_path, lines)
    )


def validate(root: Path = PROJECT_ROOT) -> list[str]:
    """Return every CI security-policy violation below *root*."""
    root = root.resolve()
    policy, errors = load_ownership_policy(root)
    promotion_tool = root / CI_PROMOTION_TOOL_PATH
    try:
        promotion_text = promotion_tool.read_text(encoding="utf-8")
    except OSError as exc:
        errors.append(
            f"{CI_PROMOTION_TOOL_PATH}: cannot read trusted tool: {exc}"
        )
    else:
        errors.extend(validate_ci_promotion_contract(promotion_text))
    workflow_root = root / ".github" / "workflows"
    workflow_paths = sorted({
        *workflow_root.glob("*.yml"),
        *workflow_root.glob("*.yaml"),
    })
    if not workflow_paths:
        errors.append(".github/workflows: no workflow files found")
    ci_text: str | None = None
    for path in workflow_paths:
        relative = path.relative_to(root).as_posix()
        try:
            text = path.read_text(encoding="utf-8")
        except OSError as exc:
            errors.append(f"{relative}: cannot read workflow: {exc}")
            continue
        errors.extend(validate_workflow(relative, text))
        if relative == CI_WORKFLOW_PATH:
            ci_text = text
            errors.extend(validate_ci_topology(text))
        if relative in {DEPENDENCY_WORKFLOW_PATH, SECURITY_WORKFLOW_PATH}:
            errors.extend(validate_merge_group_trigger(relative, text))

    fixture_path = root / ACTIVE_CI_FIXTURE_PATH
    try:
        fixture_text = fixture_path.read_text(encoding="utf-8")
    except OSError as exc:
        errors.append(
            f"{ACTIVE_CI_FIXTURE_PATH}: cannot read reviewed active "
            f"workflow fixture: {exc}"
        )
    else:
        errors.extend(validate_workflow(ACTIVE_CI_FIXTURE_PATH, fixture_text))
        if _workflow_sha256(fixture_text) != _ACTIVE_WORKFLOW_SHA256:
            errors.append(
                f"{ACTIVE_CI_FIXTURE_PATH}: fixture differs from the "
                "reviewed exact active workflow"
            )
        fixture_topology_errors = validate_ci_topology(fixture_text)
        errors.extend(
            error.replace(CI_WORKFLOW_PATH, ACTIVE_CI_FIXTURE_PATH, 1)
            for error in fixture_topology_errors
        )
        try:
            bootstrap_text = render_force_full_bootstrap(fixture_text)
        except ValueError as exc:
            errors.append(
                f"{ACTIVE_CI_FIXTURE_PATH}: cannot render reviewed "
                f"force-full bootstrap: {exc}"
            )
        else:
            if _workflow_sha256(bootstrap_text) != _BOOTSTRAP_WORKFLOW_SHA256:
                errors.append(
                    f"{ACTIVE_CI_FIXTURE_PATH}: rendered force-full "
                    "bootstrap differs from the reviewed exact workflow"
                )
            bootstrap_topology_errors = validate_ci_topology(bootstrap_text)
            errors.extend(
                error.replace(
                    CI_WORKFLOW_PATH,
                    f"{ACTIVE_CI_FIXTURE_PATH} (rendered bootstrap)",
                    1,
                )
                for error in bootstrap_topology_errors
            )
            if ci_text is not None:
                actual = _normalized_workflow_text(ci_text)
                approved = {
                    _normalized_workflow_text(fixture_text),
                    _normalized_workflow_text(bootstrap_text),
                }
                if actual not in approved:
                    errors.append(
                        f"{CI_WORKFLOW_PATH}: workflow must exactly match "
                        "the reviewed active fixture or its force-full "
                        "bootstrap rendering"
                    )

    security_path = root / SECURITY_WORKFLOW_PATH
    try:
        security_text = security_path.read_text(encoding="utf-8")
    except OSError as exc:
        errors.append(f"{SECURITY_WORKFLOW_PATH}: cannot read workflow: {exc}")
        return errors
    if policy is None:
        return errors
    for event in ("pull_request", "push"):
        paths, path_errors = event_paths(security_text, event)
        errors.extend(path_errors)
        required_paths = (
            policy.security_trigger_paths | _REQUIRED_SECURITY_FILTERS
        )
        for missing in sorted(required_paths.difference(paths)):
            errors.append(
                f"{SECURITY_WORKFLOW_PATH}: {event}.paths does not cover "
                f"declared security owner {missing}"
            )
    return errors


def _parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        description="Validate GitHub Actions security ownership and hardening"
    )
    parser.add_argument(
        "--root",
        type=Path,
        default=PROJECT_ROOT,
        help="repository root (defaults to this repository)",
    )
    return parser


def main(argv: Sequence[str] | None = None) -> int:
    args = _parser().parse_args(argv)
    errors = validate(args.root)
    if errors:
        for error in errors:
            print(error, file=sys.stderr)
        return 1
    print("GitHub Actions security policy is valid.")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
