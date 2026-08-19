# Improvement Roadmap

- **Status:** Live scheduling authority
- **Current as of:** 2026-08-19
- **Baseline:** `main` and `origin/main` at
  `443dce4c312737eebb57a5bba6fc0abb9ace1a26` (tree
  `344a9b224dd5942a50d370bc10dfb62384962440`)
- **Active implementation plan:**
  [`docs/superpowers/plans/2026-08-18-next-improvement-program.md`](docs/superpowers/plans/2026-08-18-next-improvement-program.md)
- **Point-in-time evidence:** [`docs/evidence/`](docs/evidence/README.md)

This file contains current status, authorization, dependencies, blockers, next
actions, and acceptance gates. The former multi-thousand-line R0-R12 chronology
is preserved byte-for-byte in the
[`443dce4` roadmap snapshot](docs/evidence/roadmap-through-2026-08-18-443dce4.md).
Its pending/frozen statements are historical and are not live instructions.

For policy and work authorization, use this authority order:

1. approved governance decisions;
2. maintained architecture decisions, machine-readable policy, and schemas;
3. this live roadmap;
4. operator documentation; and
5. implementation plans and historical evidence.

Runtime source and executable tests establish observed behavior and expose
drift. They cannot override an owner decision or authorize work.

An implementation plan may refine execution details but cannot infer an owner
decision, weaken a maintained policy, authorize private-source disclosure, or
publish a release.

## Active status and constraints

### Integrated convergence

- **R1:** [PR #44](https://github.com/toddlar00/rag-pipeline/pull/44)
  merged on 2026-08-01 at validated head `ed2995e` (merge `2d8e4f9`).
- **R2 convergence:**
  [PR #76](https://github.com/toddlar00/rag-pipeline/pull/76) merged at
  `ddcbe89` (merge `471381c`), and the first policy-compliant PDF/Docling
  domain update merged through
  [PR #83](https://github.com/toddlar00/rag-pipeline/pull/83).
- Those merges close the old convergence state. They do **not** close the
  broader dependency, vulnerability, licensing, qualification, packaging, or
  release milestones.

### Phase A0

A0a is integrated. The latest completed hosted A0b technical checkpoint binds
clean pre-gate source `4551c50970a07ac120d192802bcc692e39e3ece6` to
evidence head `443dce4`. Both hosted Windows and Linux Phase A0 jobs passed at
that exact head, and retained evidence artifacts were present when audited.
That pair remains historical evidence; see its
[exact-head evidence record](docs/evidence/2026-08-18-main-443dce4.md).

The current Task 0.2 evidence candidate is the direct gate-only child of clean
source `76291e1a7a2a3cd0b22ecaf3430205c926ba7769` (tree
`aa2375f1acb9c26eb4e8b0e89f6c1dc0ea8f1bfc`). Its separate Windows and
Linux CPython 3.12.13 reports each cover the authoritative 9x5 scenario set,
share the exact source, lock, and scenario contract, and pass a local
same-platform comparison. Hosted checks and the external exact-SHA promotion
record are pending, so this candidate does not yet replace the earlier pair as
the completed hosted checkpoint.

No submitted exact-head human review or separate owner authorization for the
R8c-6 ownership move was found. Technical A0 success is therefore not that
authorization. Any later source or lock checkpoint must follow the maintained
[Phase A0 policy](docs/architecture/decisions/phase-a0-benchmark-policy.md)
rather than reusing a report for a different source identity.

### Security and dependency state

At `443dce4`, the CI and dependency-compatibility workflows passed, while the
supply-chain workflow failed both audit jobs. It reported unaccepted
`aiohttp`, `cryptography`, and `h2` advisories. Existing Chroma, Torch,
Torchvision, PyMuPDF, and FlagEmbedding policy records are time-bounded through
2026-08-31.

The validated remediation branch `agent/transitive-advisory-locks` is parked at
`1138398`, has no pull request or hosted checks, and is seven commits behind
`main`. Its four-lock delta is transitive-only, which the maintained
[dependency-domain policy](docs/architecture/decisions/dependency-compatibility-domains.md)
rejects. Do not merge it until the owner selects and the repository first
implements the applicable policy path.

### Release posture

There is no release candidate and no authorization to tag, publish a package,
or advertise a production support tier. The currently enforced default
security posture remains local-only, loopback-only, trusted single-user
operation with reviewed local model artifacts. Existing cloud/provider paths
and three locked remote-model-code bundles are capabilities, not
release-qualified support.

## Owner decisions and fail-closed interim rules

| Decision | Current state | Interim rule until an owner records a decision |
| --- | --- | --- |
| Private-source documentation and evaluation metadata ([issue #45](https://github.com/toddlar00/rag-pipeline/issues/45)) | Neither strict non-derivation nor controlled private evaluation metadata is approved | Introduce no new private-source excerpt or private-derived artifact class. Do not add or expand tracked/retained copies of authored private-corpus queries, judgments, or owner diagnostics. Existing occurrences await the selected data-class audit and are not precedent. Use generated/CC0 fixtures and content-free receipts. |
| R0C cloud/provider and remote-model-code support ([issue #48](https://github.com/toddlar00/rag-pipeline/issues/48)) | Owner support tier is unresolved | Treat these paths as **unsupported and unqualified for release**. Keep the release default local-only. This fail-closed posture is not an inferred permanent owner decision. |
| Transitive advisory remediation | Direct promotion, a reviewer-bound exception mechanism, or narrow time-boxed acceptances remain available under the ADR | Select none automatically. Keep the lock branch parked and the supply-chain gate red until the chosen path is reviewed and implemented. |
| PyMuPDF distribution basis and repository code license | No approved distribution basis and no repository license decision are recorded | Private filesystem-local evaluation only; no package or release publication. |
| Private retrieval/answer qualification | Corpus-owner judgments, family aliases, floors, and promotion statistics remain unresolved | No production-qualified corpus or generalized quality claim. Portable generated/CC0 suites validate mechanics only. |
| First version and platform/support tiers | Not selected | Development-only identity; no release tag or Tier-1 claim. |

Owner decisions are gates, not checkboxes an implementation agent may infer.
A technical change may prepare a bounded decision mechanism, but it must pause
before choosing or claiming the owner's result.

## Current work authorization

The archived “Immediate change freeze” described a pre-R1 repository state and
is superseded by this table. Superseding stale wording does not silently
authorize broader work. Anything not allowed below remains held.

| Work | May start now? | Gate |
| --- | --- | --- |
| Task 0.1 status/evidence reconciliation | Yes; current slice | Documentation and content-free evidence only; do not select an owner policy |
| Task 0.2 trusted CI promotion classifier | Yes, after Task 0.1's mechanical acceptance | Base-branch-trusted evaluation, fail-closed heavy selection, and exact-SHA aggregate/manual promotion evidence |
| Tasks 0.3-0.4 transitive policy and lock remediation | Decision preparation only | Owner selects the ADR path before its mechanism or lock delta is integrated |
| Task 0.5 licensing/distribution | Owner decision records only | Both dispositions must be approved before packaging eligibility |
| Tasks 0.6-0.8 Node, secret, and static-security gates | Yes in sequence after their prerequisites | Pinned tools, synthetic canaries, redaction, security ownership, and no private source retention |
| Phase 1 installable packaging/product work | No | All Phase 0 gates plus explicit product-work and distribution authorization |
| Phase 2 private qualification and release | No | Private-source choice, owner-reviewed labels/floors, licensing, packaging, and exact-head release gates |
| Broader R7 coverage/typing/lint and A1 performance work | No | Recorded authorization after the preceding release-readiness gates in the active plan |
| R8c-6/R9 ownership and decomposition | No | Recorded owner/reviewer, current guard evidence, A1 prerequisite for the affected slice, bounded manifest, and tested rollback |
| Deferred run catalog, unified commands, observability, retrieval explanations, and experiments | No | Reach their ordered phase and satisfy its privacy, qualification, and compatibility gates |

Decision-neutral, release-blocking Phase 0 work may use synthetic/CC0 inputs.
Each owner-gated stream pauses at its decision boundary. A source-changing gate
slice refreshes the architecture inventory and, where required by the Phase A0
policy, establishes a new source/evidence pair.

## Active execution order

The detailed checklists and adversarial acceptance cases live in the
[next improvement program](docs/superpowers/plans/2026-08-18-next-improvement-program.md).
Execute it in this order:

| Sequence | Outcome | Entry condition | Exit condition |
| ---: | --- | --- | --- |
| 0.1 | One live roadmap and append-only evidence ledger | Current docs-only slice | No contradictory live status; old history preserved; owner gates explicit; no new private-derived content |
| 0.2 | Deterministic CI promotion | Task 0.1 mechanical acceptance | Trusted, rename-aware risk decision; required heavy jobs cannot be self-skipped; aggregate result is exact-SHA promotable |
| 0.3-0.5 | Valid dependency and distribution policy | Relevant owner decisions | Reviewed transitive mechanism/path; urgent advisories resolved or narrowly accepted; license/distribution decisions enforced |
| 0.6-0.8 | Complete repository supply-chain ownership | CI promotion gate | Node audit/SBOM, full-tree secret scan, and narrow Python static scan pass with pinned tools and redacted artifacts |
| Phase 1 | Installable development candidate | All Phase 0 exits and product-work authorization | Deterministic privacy-safe wheel, stable commands, version/schema registry, executable docs, external-cwd child-role tests |
| Phase 2 | Qualified first release | Packaging candidate plus owner/private-source decisions | Frozen 24-cell evidence, distinct readiness/qualification states, owner-approved statistical promotion, signed exact-head release record |
| Phase 3 | Risk-based guard and A1 capacity evidence | First release and recorded authorization | Branch/subprocess coverage, typed safety leaves, warning/lint ratchets, and stable platform-specific performance budgets |
| Phase 4 | Safe runtime ownership and decomposition | Phase 3 evidence plus explicit R8/R9 authorization | Characterized facade, bounded ownership slices, privacy-safe service telemetry, per-slice rollback and exact evidence |
| Deferred | Product simplification and retrieval experiments | Phase 4 and applicable qualification gates | Each product/quality change proves its own privacy, usability, and promotion contract |

Task 0.2 now has exact clean source `S`
(`76291e1a7a2a3cd0b22ecaf3430205c926ba7769`, tree
`aa2375f1acb9c26eb4e8b0e89f6c1dc0ea8f1bfc`) and this branch is its direct
gate-only evidence child `E`. The live CI workflow remains the reviewed
force-full bootstrap rendering, the future active workflow remains preserved
as a security-owned fixture, and the paired local Phase A0 comparisons pass.
The next action is to promote `E` through its hosted force-full checks and
external exact-SHA record. Only after that evidence head is the trusted base
may a later workflow-only commit `A` copy the fixture into the live workflow,
pass its hosted activation cases, and receive its own external exact-SHA
record. Neither local success nor the as-yet-unrecorded identity of `E` closes
Task 0.2. Task 0.3 must not start before the activation checkpoint is accepted;
it then stops for the transitive-policy owner choice if that choice has not
been recorded.

## Acceptance gates by phase

### Phase 0: gate trust

- CI classification runs only base-branch-trusted evaluator code/data, uses
  exact event SHAs and a NUL-delimited rename-aware diff, and sends unknown or
  policy/workflow changes to the full lane. Documentation-only fast decisions
  require verified regular Git blobs; type changes, links, gitlinks, and
  unverifiable modes run full.
- The trusted files land first under the exact force-full seed topology. Only a
  later workflow-only checkpoint may activate classification, after which all
  execution jobs test the bound synthetic-merge or merge-group candidate rather
  than an unmerged pull-request head.
- An `if: always()` aggregate inspects every required job result and fails on
  failure, cancellation, timeout, classifier failure, or unexpected skip. If
  repository rulesets remain unavailable, an exact-SHA manual or merge-queue
  record substitutes for no required check.
- Dependency exceptions are narrow, reviewer-bound, expiring, and incapable of
  hiding direct or unrelated package changes.
- Node manifests and every scanner input/workflow are security-owned. Secret
  and static scans use exact tool pins, synthetic positive/negative fixtures,
  full tracked-tree PR scope, redacted console/artifacts, and no open-ended
  suppression baseline.
- Deterministic SBOM bytes bind normalized inputs/tool/build epoch. Live
  vulnerability conclusions instead record scanner, scan time, and advisory
  database identity.

### Phase 1: installable candidate

- One development version appears consistently in Python, CLI, service, and
  package metadata without coupling product and artifact-schema versions.
- Wheels are built from clean sources with a normalized epoch, contain only an
  allowlisted inventory, and are installed with `--no-index` from
  hash-verified inputs (or exact preinstalled locks plus `--no-deps`).
- Windows and Linux each reproduce their own wheel bytes; cross-OS inventory
  and metadata agree. Entry points and every physical worker role run from a
  working directory outside the checkout.
- Executable operator docs contain no repository-only command dependency and
  no private path, credential, or generated content.

### Phase 2: qualification and release

- Structure-ready, retrieval-qualified, grounded-answer-qualified, and
  production-qualified remain separate states with exact corpus, profile,
  model, index, label, owner, expiry, and invalidation identities.
- The owner freezes one label generation before inspecting calibration. A
  predeclared estimator/test, interval or alpha, multiplicity correction,
  minimum effect/effective sample size or power, tie policy, and explicit
  non-promoting `inconclusive` outcome govern promotion.
- Every release gate binds one clean commit/tree, locks, runtime/platform,
  schema registry, migration/rollback evidence, licenses, security results,
  qualification state, and signed review. No tag or publication precedes it.

### Phase 3: guardrails and capacity

- Coverage includes fresh subprocess branches through a test-only activation
  that production/release workers reject. Reports use relative paths and omit
  source-bearing HTML or absolute paths.
- Warning fingerprints use category, owning distribution/module, and stable
  message with separate OS/Python budgets. Unknown first-party warnings fail
  immediately; known third-party counts may only ratchet downward.
- Timing gates activate only after the named number of independent Tier-1 runs
  demonstrates the predeclared percentile, outlier, variability, and
  platform-specific budget formula. Dirty, partial, non-finite, wrong-lock, or
  wrong-platform reports fail closed.

### Phase 4: safe evolution

- Characterization freezes public names, signatures, mutation seams, import
  order, CLI/worker routing, artifacts, errors, and cleanup precedence before
  ownership moves.
- Every slice names an owner/reviewer, source commit/tree, bounded files and
  capabilities, rollback procedure/triggers, and exact acceptance evidence.
- Service observability, when reached, remains local first-party telemetry with
  no exporter/network egress. It uses a private bounded sink, template routes,
  capped cardinality/retention, authenticated access, redaction canaries, and
  explicit sink-failure semantics. Correlation IDs are never metric labels.

## Integrated foundation

The following foundation is already on `main` and must be preserved:

- deterministic ingestion, chunk/publication evidence, hybrid retrieval,
  evaluation, Chroma/Qdrant lifecycle controls, CLI/UI, durable jobs, and the
  authenticated loopback service;
- R0A/R0B endpoint, credential, model-artifact, egress, local-UI, and transport
  controls;
- architecture/facade inventory, CI-security ownership, dependency domains,
  process supervision, service/job/evaluation composition seams, and Phase A0;
- table-family and context-aware retrieval correctness, portable generated/CC0
  evaluation suites, evidence-grounded answer checks, and private calibration
  mechanics whose owner decisions remain open; and
- historical PR #1-#83 implementation and validation narratives, preserved in
  the [roadmap evidence snapshot](docs/evidence/roadmap-through-2026-08-18-443dce4.md).

“Integrated” means present on `main`; it does not by itself mean
release-qualified, owner-approved, or supported for remote/multi-user use.

## Enduring constraints

- Preserve `rag.py` public names, mutation seams, CLI behavior, artifact bytes,
  schemas, error precedence, and process-isolation contracts unless a task
  explicitly versions and tests a behavior change.
- Keep the service and UI literal-loopback-only. Remote or multi-user operation
  requires a separate threat model, authentication/authorization, TLS/proxy,
  rate/tenant isolation, audit retention, and distribution approval.
- Treat workers/extensions as trusted code. Process supervision is not a
  sandbox.
- Change one dependency compatibility domain per pull request. Do not hand-edit
  generated locks or combine a policy introduction with the dependency delta
  that consumes it.
- Give every persisted artifact a version, strict bounds, canonical
  serialization, atomic publication, privacy classification, and malformed,
  stale, and future-version tests.
- Use characterization and differential tests before moving established code.
  Keep refactors separate from discovered behavior fixes.
- Do not claim semantic entailment, production corpus quality, secure erasure,
  public-network safety, or a support tier beyond the evidence actually
  reviewed.

## Volatile architecture metrics

Do not copy current module, function, binding, or hotspot counts into this live
roadmap. The machine-readable authority is
[`architecture-inventory.json`](architecture-inventory.json). Validate it and
print the current aggregate summary with:

```console
python tools/check_architecture_inventory.py
```

Point-in-time counts belong in a commit/tree-bound
[evidence record](docs/evidence/2026-08-18-main-443dce4.md). A richer generated
hotspot summary is deferred until a source-changing checkpoint is authorized;
adding that Python tool now would itself invalidate the paired source baseline.

## Non-goals

- Do not infer owner approvals, fabricate relevance judgments, or derive
  thresholds from a small portable suite.
- Do not merge broad dependency upgrades, extend exceptions silently, or use a
  transitive-only lock change to bypass the domain policy.
- Do not use project-wide typing, a vanity coverage percentage, or a permanent
  suppression inventory as a proxy for risk reduction.
- Do not perform a broad `rag.py` rewrite before the ordered guard,
  characterization, ownership, and rollback gates.
- Do not rewrite Git history, delete provenance, publish a release, or expose a
  listener beyond the approved local boundary without the corresponding owner
  decision.

## Completion rule

A technical milestone is complete only when its behavior is implemented,
failure-injected, covered by proportionate tests and static checks, exercised
against relevant real optional clients where practical, independently
reviewed, and bound to an exact commit/tree. Integration additionally requires
a merge or an owner-signed exact-SHA manual promotion record.

Release readiness is separate. It also requires every applicable privacy,
corpus-owner, dependency, vulnerability, license, migration, rollback,
platform, and publication gate. A green implementation or hosted workflow
cannot satisfy an unresolved human decision.
