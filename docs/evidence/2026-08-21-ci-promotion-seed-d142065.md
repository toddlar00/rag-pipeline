# Task 0.2 seed promotion at `d142065`

- **Observation date:** 2026-08-21
- **Subject:** promotion and integration of the deterministic CI promotion
  seed ([PR #93](https://github.com/toddlar00/rag-pipeline/pull/93))
- **Merge identity:** history-preserving merge
  `d142065cdcc890e2ad64057e0c9f627a079f93d6` (tree
  `1c2a1c7ec6e3dcafc3b7265ca1c4b2d925f0b336`), parents exact base
  `443dce4c312737eebb57a5bba6fc0abb9ace1a26` and exact evidence head
  `aa7a23bbb0179beab001261da43529d670ee5720`
- **Classification:** content-free aggregate; hosted-workflow metadata from
  the private repository; independent agent review (Claude Code session
  operated on behalf of the repository owner). This record contains no human
  review and no owner decision; those classes remain distinct.

## Bound identities

| Identity | Value |
| --- | --- |
| Raw evidence head `E3` | `aa7a23bbb0179beab001261da43529d670ee5720`; tree `1c2a1c7ec6e3dcafc3b7265ca1c4b2d925f0b336` |
| Clean source parent `S3` | `b07e3270881376cf60586c363e6285722562c7f5`; tree `b287ef0452ffd9d35d7ec361d5e7a03b50f01258` |
| Exact base | `443dce4c312737eebb57a5bba6fc0abb9ace1a26` |
| Synthetic merge candidate tested by the seven non-A0 execution jobs | `07593887f7836b1ed6e5f8c87fdc6ee47155d01e`; tree equals the head tree; parents exactly base plus `E3` |
| `ci-risk-policy.json` SHA-256 at `E3` | `7eee091dfbc6c41cb3878c354b91b52f0cdcb6fb1923c97cce72d0b016f19c22` |
| Live bootstrap `.github/workflows/ci.yml` SHA-256 at `E3` | `49f2b074ff288462e4a0fcf9afde47ea2f7b6ec1671d777e60b893750540866b` |
| Fixture `.github/ci/active-ci.yml` SHA-256 at `E3` | `d161677a0eb4bfa80140c88a97081a63e3c25ec4ec4be3c00b247587911ce722` |

The `S3..E3` delta was verified to touch exactly the six approved gate-only
evidence/status paths. Both workflow digests are byte-identical to the
reviewed constants embedded in `tools/check_ci_security.py` at `E3`.

## Hosted evidence at `E3` (pull request head)

- CI run [32270045114](https://github.com/toddlar00/rag-pipeline/actions/runs/32270045114),
  attempt 1, head `E3`: success; all 16 jobs completed successfully; none
  skipped or cancelled.
- Dependency compatibility run
  [32270044983](https://github.com/toddlar00/rag-pipeline/actions/runs/32270044983),
  attempt 1, head `E3`: success; all 9 jobs.
- Phase A0 Linux job
  [96124018031](https://github.com/toddlar00/rag-pipeline/actions/runs/32270045114/job/96124018031):
  success; artifact 9371842625, digest
  `sha256:b8fab520baced5b2659b2d7cc88852d925c9ae5a4d8da6c371e71c40dd938e68`,
  expires 2026-09-18.
- Phase A0 Windows job
  [96124017957](https://github.com/toddlar00/rag-pipeline/actions/runs/32270045114/job/96124017957):
  success; artifact 9371956375, digest
  `sha256:cd5940b47bcf91efcbdb13608181df321bd6ddf2fdbe14f7af8c7eb5cd521595`,
  expires 2026-09-18.
- Report bytes at `E3`: Windows 50,409 bytes, file SHA-256
  `b8a3bb3dce7733a445f3c880ab83a15b1e719788457ca5b0d8f75665e8199c06`,
  embedded report SHA-256
  `6ff8b67fe5c24a3eb08950f38a23860f47f63866cc4587585cbb140c8372414a`;
  Linux 49,825 bytes, file SHA-256
  `acf7885edde5c6fab49e823c9501bf70af606474c9aa10fbf0ff6af725e867e2`,
  embedded report SHA-256
  `2befec9250e6ee99524f7d9dd72ce10b10abc211db07ea1571dd6e013026fa2f`. Both
  reports bind `source.commit = S3` with a clean worktree.
- Supply-chain run 32270044993 remained red only for the pre-existing
  owner-gated `aiohttp`, `cryptography`, `datasets`, and `h2` findings; no
  lock, manifest, audit, or acceptance changed in PR #93.

## Hosted evidence at `d142065` (push to `main`)

- CI run
  [32490579396](https://github.com/toddlar00/rag-pipeline/actions/runs/32490579396):
  success; all 16 jobs completed successfully.
- Dependency compatibility run
  [32490579397](https://github.com/toddlar00/rag-pipeline/actions/runs/32490579397):
  success; all 9 jobs completed successfully.
- Supply-chain security run 32490579518: failure, consistent with the
  pre-existing owner-gated advisory state described above.

## Local verification at `E3`

- Focused suite `tests/test_ci_promotion.py` plus `tests/test_ci_security.py`
  with `GIT_NO_REPLACE_OBJECTS=1`: 180 passed.
- `tools/check_python_sources.py`, `tools/check_dependency_policy.py`,
  `tools/check_model_artifacts.py`, `tools/check_ci_security.py`,
  `tools/check_architecture_inventory.py`, and `ruff`: all passed.
- Workflow topology confirmed by direct inspection: the bootstrap is
  7-candidate/1-raw-head (only the Phase A0 job checks out the literal pull
  request head), and the fixture is 8-candidate/0-raw-head with the classify
  job checked out at the trusted base commit.

## External exact-SHA review record

The review verdict, its verification transcript summary, and the findings
below were posted before the merge as the
[PR #93 review record comment](https://github.com/toddlar00/rag-pipeline/pull/93#issuecomment-5364353254)
(2026-08-21T02:11Z). Three parallel independent reviewers covered the
classifier/checker pair, the active workflow fixture with its test coverage,
and the documentation/evidence restructure. No High or Medium defects were
found in shipped behavior; the docs restructure's byte-fidelity and
private-content checks passed.

## Logged follow-ups (none blocking the seed)

| ID | Severity | Summary | Due |
| --- | --- | --- | --- |
| F1 | Medium (test adequacy) | The fixture's inline promotion-gate validator has its fail-closed branches (required-job failure/cancel/skip, SHA-binding, digest-tamper rejection) marker-pinned but not executed by tests; only three validator scenarios run. The equivalent `ci_promotion.verify_promotion` truth table is executable-tested. | No later than activation-checkpoint acceptance |
| F2 | Low | Fixture event step writes `head_ref` to `GITHUB_OUTPUT` without the multiline guard applied to other outputs; currently non-exploitable, sole consumer fails toward the heavy lane. | Pre-activation hardening slice |
| F3 | Low | Fixture event-identity step branches are marker-pinned, not executable-tested; classifier independently re-verifies candidate parentage downstream. | Pre-activation hardening slice |
| F4 | Low | `_validate_supported_workflow_syntax` does not reject YAML anchors/aliases/merge keys; latent, not demonstrably exploitable for non-pinned workflows. | Checker hardening follow-up |
| F5 | Low (cosmetic) | Stray indent in `toc-hierarchy-output-contract.md`; stale Task 0.1 "current slice" label in the roadmap authorization table. | Fixed in this documentation slice |

Fixing F1-F4 inside PR #93 would have moved its head and invalidated the
frozen `S3 -> E3` evidence chain; they were deliberately deferred under the
append-only evidence rule.

**Resolution status (2026-08-21):** F1, F2, F3, and F5 are resolved by the
pre-activation hardening checkpoint that carries this record
([PR #94](https://github.com/toddlar00/rag-pipeline/pull/94)): executable
validator-branch and event-identity-step coverage, the fixture's multiline
output guard with its updated reviewed lane/workflow digests, and both
cosmetic corrections. F4 remains open as a checker-hardening follow-up.

## Boundaries

This record does not activate classification. The workflow-only activation
checkpoint `A` remains a separate change that must pass its own hosted
activation cases and receive its own external exact-SHA record. This record
settles no owner decision and does not alter the supply-chain advisory state.
