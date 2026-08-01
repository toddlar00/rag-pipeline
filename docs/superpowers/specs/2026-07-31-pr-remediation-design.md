# PR Stack and CI Remediation Design

Date: 2026-07-31
Status: Approved by owner (Cade) in brainstorming session; pending spec review.
Scope: Remediate the open-PR backlog, the GitHub Actions outage blocking it,
and the dependency-update policy conflict. This is a repository-management
design; it changes no pipeline behavior by itself.

## Problem statement

As of 2026-07-31 the repository has 15 open pull requests and every check on
every PR updated after 2026-07-25 is failing. The failures are not code
defects: each failed check carries the GitHub annotation "The job was not
started because recent account payments have failed or your spending limit
needs to be increased." GitHub Actions billing broke around midday 2026-07-25
and no hosted job has started since. The repository's release process requires
hosted exact-head validation before merge, so the entire release train is
blocked.

Verified branch topology (2026-07-31):

```
main
 └─ #44  agent/r1-cumulative-gate        R1 cumulative candidate, 69 commits
         all checks green at its exact head (hosted run 2026-07-25)
     └─ #66  agent/r2-dependency-domains
         ├─ #68 → #69 → #70 → #71        strict-contract stack (8 commits)
         └─ #73  agent/release-defect-remediation (4 commits)
                 head c633363 is a frozen docs/reports-only evidence child
                 over source checkpoint e904fa6
 #67  agent/docs-roadmap-audit           independent; content now stale
 #72  dependabot                         17 bumps; edits hash-locked files
                                         directly, violating lockfile policy
```

A dry-run `git merge-tree` of #71 into #73 shows exactly one source-code
conflict (`rag.py`) plus conflicts only in regenerable artifacts
(`architecture-inventory.json`, `benchmarks/phase-a0-*.json`,
`benchmarks/README.md`) and three documentation files.

## Decisions made (owner-selected)

1. Scope: full phased remediation (CI, PR convergence, dependencies).
2. CI restoration: fix billing AND slim the per-PR check matrix.
3. PR convergence: bank #44 first on its existing green exact-head evidence,
   then build one new cumulative candidate for everything else.
4. Dependabot #72: close it; redo the updates through
   `tools/refresh_locks.py` under the #66 dependency-domain process.

## Phases

### Phase 0 — Restore CI (owner action + verification)

- Owner fixes payment method or spending limit in GitHub Settings →
  Billing & plans. Only the account owner can do this.
- Verification: re-run one failed check run on PR #73 and confirm jobs start
  (any state other than the instant billing failure).
- Deadline pressure: #44's green hosted run is dated 2026-07-25. Actions log
  retention is approximately 30 days, so that evidence should be treated as
  reliable only until about 2026-08-24. Phase 1 must complete before then, or
  hosted validation must be re-run at #44's exact head.

### Phase 1 — Bank #44

- Merge #44 into `main` at its exact validated head. `main` has not moved
  since #44 branched, so the post-merge tree is identical to the validated
  tree and no new hosted run is required.
- This merge is the owner's review sign-off under the repository's
  independent-review requirement.
- The heads of #31 and #33–#43 are verified ancestors of #44's head, so
  GitHub auto-marks those PRs merged when #44 lands; #32
  (process-supervision-design) is not an ancestor and is closed manually
  after confirming its design content was superseded by the merged ADR.
- PR #43's code therefore merges with #44. The Ethics calibration OWNER
  DECISION (thresholds) remains open as a ROADMAP "Owner review pending"
  item, not as an open PR, and is explicitly out of scope here.

### Phase 2 — Slim the CI matrix

- Two lanes:
  - Draft-PR fast lane: ruff, policy gates (`check_python_sources`,
    `check_dependency_policy`, `check_model_artifacts`, CI-security gate),
    compile checks, Linux CPython 3.12 unit tests, and the three offline
    evaluation suites. These are the cheap, fast subset.
  - Candidate lane (the full current matrix, including Windows jobs at 2x
    minute billing, CPython 3.10–3.14, service API, real vector stores, and
    Phase A0 baselines): runs on `main` pushes, integration-candidate
    branches, and manual `workflow_dispatch`. Candidate branches are
    identified by a documented branch-name pattern chosen during
    implementation (default proposal: `agent/*-candidate`), with
    `workflow_dispatch` as the always-available fallback for any head.
- Constraint: the CI-security gate proves workflow invariants (full-SHA action
  pinning, symmetric security-workflow coverage, checkout credential
  isolation, read-only permissions). The trigger changes must keep that gate
  green; the gate itself must not be weakened.
- Lands as one small workflow-only PR on the new `main` after Phase 1, so all
  later work burns the reduced minutes. It is validated by its own full-matrix
  run.

### Phase 3 — R2 convergence candidate

- Branch from #73's head (which already contains #44 and #66); merge #71's
  eight strict-contract commits.
- Resolution work:
  - `rag.py`: the one real code conflict, resolved by hand,
    behavior-preserving for both lines.
  - Regenerate `architecture-inventory.json` and fresh paired Windows/Linux
    Phase A0 evidence at the merged head (the per-branch evidence is
    head-bound and stale after the merge by design).
  - Reconcile the three conflicted documentation files.
  - Refresh `ROADMAP.md` and documentation reconciliation as part of this
    candidate, subsuming stale #67.
- Full local gate battery before publication: `python -m pytest -q`, ruff,
  compile and policy gates, the three offline evaluation suites, real pinned
  Zettlr profile, real Chroma/Qdrant smoke, and paired Phase A0 comparisons.
- Open one integration PR, run hosted exact-head validation on the candidate
  lane, obtain owner review, merge.
- Close as superseded: #66, #67, #68, #69, #70, #71, #73.

### Phase 4 — Dependency remediation

- Close #72 with a comment linking the lockfile policy; keep its 17-update
  list as the input checklist.
- Run `tools/refresh_locks.py --upgrade`; review the diff domain-by-domain
  under the #66 dependency compatibility domains; open one policy-compliant
  dependency PR; validate on the candidate lane; merge.
- Lock changes invalidate both Phase A0 baselines (`benchmarks/README.md`),
  so this PR also regenerates paired Windows/Linux Phase A0 evidence at its
  own clean pre-gate checkpoint.

## End state / success criteria

- Hosted CI runs start and complete; monthly Actions burn fits the owner's
  budget under the two-lane matrix.
- `main` contains all validated work from #31–#42, #44, #66, #68–#71, #73,
  and the #67 documentation reconciliation.
- Open PRs reduced to: #43 (awaiting owner calibration decisions) and the
  new dependency PR until it merges.

## Risks and handling

- Billing fix slips past ~2026-08-24: re-run hosted validation at #44's exact
  head before merging. Extra minutes, process-clean.
- `rag.py` conflict resolution: both branches' full test suites must pass at
  the merged head before the candidate publishes.
- Dropbox-synced working copy (known ROADMAP P0 hazard): local Phase A0
  evidence regeneration uses the established retry-safe flow; re-read files
  after writes before trusting results.
- CI lane change could accidentally weaken security invariants: the existing
  CI-security gate is the guard and must pass unmodified.

## Out of scope

- PR #43 threshold decisions (owner-only).
- Any pipeline behavior change; all remediation work is repository
  management, merge mechanics, CI policy, and evidence regeneration.
- The unresolved private-source documentation policy (tracked separately in
  `docs/governance/`).
