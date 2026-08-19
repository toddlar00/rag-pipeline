# CI Security Ownership Policy

- **Status:** Implementation merged to `main` on 2026-08-01 through the
  history-preserving integration of cumulative
  [PR #44](https://github.com/toddlar00/rag-pipeline/pull/44); the all-green
  hosted run at exact head `ed2995e` and the owner merge decision satisfied
  replacement-head validation and review
- **2026-08-18 amendment status:** This amendment defines a staged activation
  contract. `ROADMAP.md` and exact-head evidence record whether the change is
  still a working-tree candidate, has reached the trusted seed, or is active.
  Activation requires two promoted stages with three immutable identities
  (`S → E → A`), refreshed source-bound Phase A0 evidence, hosted fork-PR
  validation, and the exact-SHA records defined below.
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

[`ci-risk-policy.json`](../../../ci-risk-policy.json) separately owns CI
promotion. Its bounded path groups distinguish service, vector, process
supervision, storage/publication, evaluation, packaging, security, and
documentation-only changes; anything unmatched is `unknown`. The initial
policy is deliberately conservative: only a wholly documentation-only diff may
use the fast lane, while every other group and every mixed or unknown diff
requires all heavy jobs.

For pull requests, the lane decision must execute the evaluator and policy
extracted from the event's exact base SHA, never code from the pull-request
head. The evaluator validates canonical base/head SHAs and parses
`git diff --name-status -z -M` output without a shell. Deletions use their old
path, and copies or renames classify both endpoints. A documentation-only
decision additionally requires every surviving endpoint to be a regular Git
blob; type changes, symbolic links, gitlinks, and unverifiable modes select the
full lane. Workflow, classifier, and policy changes therefore select the full
lane even when the head attempts to weaken its own classifier. Push, manual,
and merge-queue controls may force the full lane, but they may never narrow a
policy decision.

The risk diff remains the exact event base versus pull-request head. Execution
jobs, however, check out the event's exact tested candidate SHA. For a pull
request this is GitHub's synthetic merge commit, whose two parents are verified
against the bound base and head; for a merge group it is the queue candidate;
for push or manual runs it is the pushed or dispatched commit. A decision binds
all four identities—base, head, trusted evaluator, and tested candidate—so a
green raw-head run cannot stand in for an untested prospective merge tree.
This required fourth identity is decision schema version 2. In a merge-group
event, `head_sha` names the whole queue candidate rather than an individual
pull-request head, and the evaluator therefore requires head and candidate to
be identical.

The temporary force-full evidence bootstrap has one deliberately narrower
checkout rule: its Phase A0 job checks the literal pull-request head so the
retained report and attestation can bind exact evidence child `E`. The
bootstrap has no promotion aggregate, and that raw-head result is not active
promotion evidence. Its other execution jobs continue to test the synthetic
candidate. The deterministic renderer makes only this Phase A0 ref rewrite;
the reviewed active fixture checks out the tested candidate in every execution
job, including Phase A0, before the promotion gate can accept it.

If GitHub regenerates a pull-request merge candidate after the target base
moves, an event whose advertised base and candidate parents no longer agree
fails closed. It must be retriggered against the new base; neither the stale
decision nor its run may be reused as promotion evidence.

Classification deliberately compares the event's exact target-base tip to the
raw pull-request head. A head that has diverged from a newer base can therefore
surface conservative apparent deletions and select the full lane even when the
authored patch is documentation-only. Updating the branch restores the normal
fast-lane comparison; this availability cost cannot weaken required coverage.

The stable `CI promotion gate` job is a direct dependent of every CI job and
runs under `if: always()`. It checks the classifier result, exact event and
tested-candidate identities, and policy digest. It parses the classifier's
complete canonical decision, recomputes its digest, checks every scalar output
against that decision, and independently enforces the event/branch force
reason plus the group-to-requirements semantics. It requires every
always-selected or policy-selected job to succeed and accepts `skipped` only
for a heavy job the trusted decision marked not required. Failure,
cancellation, timeout (reported by Actions as a non-success), missing output,
an unavailable classifier, semantic inconsistency, and an unexpected skip all
reject promotion. Its decision and promotion summaries are canonical,
content-free JSON: they contain SHAs, digests, group names, counts, and result
states, never changed paths, path-derived digests, or repository content.

Changes that add, remove, rename, or transfer a security-critical owner must
update the policy, workflow filters, checker expectations, and tests together.
The checker runs as a general CI quality gate so edits to workflow security are
not dependent solely on the filtered security workflow.

Repository rulesets and branch protection were rechecked on 2026-08-18 and
were unavailable. Until a required check can be configured, each merge must
therefore have an external exact-SHA promotion record. The reviewer records the
tested candidate commit and tree, pull-request head SHA, target-base SHA,
current workflow run ID and attempt, successful `CI promotion gate` job URL,
policy and decision digests, reviewer, and timestamp; verifies every separately
applicable dependency/security workflow against the same bound source; and
rechecks both the pull-request head and target-base SHA immediately before
merge. Either identity changing invalidates the record and requires a new
synthetic candidate and run. A record inside the candidate is not sufficient
because adding it changes the candidate SHA. Governance changes to the
workflow, policy, evaluator, or checker always require this review even after
automated required checks become available.

The supply-chain and dependency-compatibility workflows also subscribe to
`merge_group: checks_requested`, so a queue-based record can verify those
separately applicable checks on the queue candidate. Their earlier
pull-request result remains necessary for any pull-request-only diff-policy
check. A local fork-ref fixture proves that the classifier can use only the
base repository's `refs/pull/<n>/head` and `refs/pull/<n>/merge` objects with no
fork remote; hosted contributor-approval, token, and ephemeral-ref behavior is
still an activation-checkpoint acceptance item.

Activation has two promoted stages and three immutable identities. Clean source
commit `S` integrates the inert policy, evaluator, tests, checker/ownership
changes, refreshed architecture inventory, and the exact `Force full CI
bootstrap` topology. That lane executes no repository code, emits the full
Python matrix and `true` for every heavy-job flag, and has no branch-name fast
path. Because the checked-in baselines still bind the prior source, `S` may
correctly fail the old Phase A0 gate and is not itself the promoted seed head.
Generate both platform reports from clean `S`, then create direct evidence
child `E` containing only the fixed Phase A0 evidence/provenance allowlist.
`E` receives the external exact-SHA review after every heavy job passes. Only
the following workflow-only activation `A` enables base-SHA extraction and the
aggregate; its hosted acceptance includes a fork pull request using only the
base repository's read-only pull refs. The checker accepts the bootstrap and
active topologies only when their normalized full-workflow digests match the
reviewed forms, then independently checks their triggers, environment, exact
matrices and checkouts, lane semantics, and aggregate contract. It rejects any
weakened flag, extra lane command, repository checkout, omitted heavy job, or
unreviewed execution-step change. `S → E → A` ancestry must be preserved; no
squash, rebase, or amendment may separate a report from its recorded source.

The reviewed active bytes are preserved in the security-owned canonical
fixture [`.github/ci/active-ci.yml`](../../../.github/ci/active-ci.yml), which
remains executable only after it is copied to the workflows directory. At
evidence/seed head `E`, the checker and tests validate that fixture against the
exact active digest and topology, validate the live workflow against the exact
force-full bootstrap digest and topology, and prove that the bootstrap is the
deterministic rendering of the fixture, including its sole literal-head Phase
A0 evidence checkout. The activation checkpoint is therefore a workflow-only
copy of the fixture to `.github/workflows/ci.yml`; validation then requires the
live workflow's normalized bytes, digest, topology, and all-candidate checkout
contract to match the fixture. The rollout never depends on an external patch,
side branch, or one-way transformation that leaves only an unreproducible
digest.

If an emergency single-checkpoint bootstrap is unavoidable, a missing base
evaluator may select only the full lane and the change is not promotable
without the same external exact-SHA review; it may never fall back to a fast
decision from head code.

## Boundaries and residual work

This policy prevents known trigger-ownership drift and enforces selected
workflow and promotion invariants. A normal `pull_request` workflow still reads
its YAML from the candidate, so base-trusted classification cannot by itself
make a self-modifying workflow a complete trust root; the exact-SHA review
above closes that boundary while repository rulesets are unavailable. The
aggregate sees matrix job conclusions, not individual matrix-cell counts, and
cannot aggregate jobs from separate workflows. Static matrix checks and the
external record cover those limits.

The dependency-free checker recognizes the workflow surface this repository
owns; it is not a complete GitHub Actions schema or expression interpreter.
Both exact workflow forms must therefore pass a pinned `actionlint` preflight
when their reviewed digests are established and GitHub's hosted parser at
their respective seed and activation checkpoints. Recomputing a digest alone
is not review evidence. The 2026-08-18 local preflight used `actionlint`
1.7.12's Windows amd64 archive at SHA-256
`6e7241b51e6817ea6a047693d8e6fed13b31819c9a0dd6c5a726e1592d22f6e9`;
both exact forms returned no findings.

The policy does not prove that an action SHA is trustworthy or detect
credentials committed in source. A pinned, content-aware repository secret
scan with an explicitly reviewed suppression policy remains required. Its
action, configuration, canaries, and ownership paths must be brought under this
policy when introduced.

## Evidence

- Checker behavior and adversarial fixtures:
  [`tests/test_ci_security.py`](../../../tests/test_ci_security.py)
- Risk-classifier and promotion truth tables:
  [`tests/test_ci_promotion.py`](../../../tests/test_ci_promotion.py)
- Machine-readable promotion policy and evaluator:
  [`ci-risk-policy.json`](../../../ci-risk-policy.json),
  [`tools/ci_promotion.py`](../../../tools/ci_promotion.py)
- Enforced workflow:
  [`.github/workflows/security.yml`](../../../.github/workflows/security.yml)
- General quality-gate invocation:
  [`.github/workflows/ci.yml`](../../../.github/workflows/ci.yml)
