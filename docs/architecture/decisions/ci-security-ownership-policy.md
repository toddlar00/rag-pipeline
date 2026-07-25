# CI Security Ownership Policy

- **Status:** Implemented locally; pending exact-head review and integration
- **Milestone:** R7 quality gates
- **Policy record:** [`ci-security-ownership.json`](../../../ci-security-ownership.json)

## Context

Security checks that run only when a hand-maintained path list happens to match
can be bypassed accidentally when implementation ownership moves. Workflow
hardening also needs repository-wide invariants: a secure security workflow
does not compensate for an unpinned action or a checkout credential retained by
another workflow.

## Decision

[`ci-security-ownership.json`](../../../ci-security-ownership.json) is the
machine-readable ownership record for security-trigger coverage. It separates:

- current release-policy, provider, model-supply, and vector-runtime owners;
- reserved future owners, presently including `pipeline_runtime.py`, so a
  planned move cannot create an unmonitored gap; and
- governance files whose changes can alter the check itself.

[`tools/check_ci_security.py`](../../../tools/check_ci_security.py) validates
that record and the repository workflows. The security workflow's pull-request
and push filters must cover the declared current, reserved, and governance
paths symmetrically. Every external `uses:` reference must be pinned to a full
commit SHA, every checkout must set `persist-credentials: false`, and workflow
permissions must be exactly `contents: read`, with no job-level override. Any
future permission expansion requires an explicit policy, checker, and ADR
amendment before the workflow change.

Changes that add, remove, rename, or transfer a security-critical owner must
update the policy, workflow filters, checker expectations, and tests together.
The checker runs as a general CI quality gate so edits to workflow security are
not dependent solely on the filtered security workflow.

## Boundaries and residual work

This policy prevents known trigger-ownership drift and enforces selected
workflow invariants. It does not interpret arbitrary workflow behavior, prove
that an action SHA is trustworthy, or detect credentials committed in source.
A pinned, content-aware repository secret scan with an explicitly reviewed
baseline remains required. Its action, configuration, baseline, and ownership
paths must be brought under this policy when introduced.

## Evidence

- Checker behavior and adversarial fixtures:
  [`tests/test_ci_security.py`](../../../tests/test_ci_security.py)
- Enforced workflow:
  [`.github/workflows/security.yml`](../../../.github/workflows/security.yml)
- General quality-gate invocation:
  [`.github/workflows/ci.yml`](../../../.github/workflows/ci.yml)
