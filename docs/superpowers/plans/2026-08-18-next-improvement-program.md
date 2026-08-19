# Next Improvement Program: Release Readiness, Qualification, and Safe Evolution

**Status:** Active. `ROADMAP.md` remains the scheduling authority. This plan
does not override an owner decision, authorize private-source disclosure, or
expand the roadmap's current work-authorization table.

**Goal:** Turn the already-hardened local pipeline into an installable,
versioned, operationally understandable product; make retrieval and
answer-evidence quality
an explicit qualification state; and establish enough risk-based evidence to
decompose the remaining `rag.py` runtime safely.

**Current next executable slice:** Task 0.2, after the mechanically complete
Task 0.1 reconciliation. Owner-pending R0C and private-source decisions retain
their fail-closed gates.

## Evidence-bound observed baseline

This plan was derived from clean `main` at `443dce4` on 2026-08-18. Exact local
test results, hosted workflow identities, dependency findings, and limitations
are preserved in the
[`443dce4` evidence record](../../evidence/2026-08-18-main-443dce4.md).
Obtain current architecture aggregates with
`python tools/check_architecture_inventory.py`; this active plan does not
repeat volatile counts.

At that baseline, the release-readiness gaps were: no installable product
metadata or stable entry points; branch-name-dependent heavy CI; no Node audit
ownership, content-aware secret scan, or narrow static-security gate; an
unresolved transitive-advisory policy; and unresolved private-source,
support-tier, and distribution decisions.

## Brainstormed improvements

| Priority | Improvement | Why it matters | Effort | Disposition |
| --- | --- | --- | --- | --- |
| P0 | Reconcile live roadmap state and owner gates | Contradictory freeze and evidence language makes the next legal change ambiguous | Small-medium | Do first |
| P0 | Replace branch-name CI promotion with a tested risk classifier | Safety-sensitive PRs can currently be green while all relevant heavy jobs are skipped | Medium | Do first |
| P0 | Resolve the transitive-advisory policy deadlock | Known fixes are parked and the current exception deadline is near | Medium plus owner decision | Do first |
| P0/P1 | Add Node, secret, and narrow static-security coverage | One committed dependency ecosystem and repository content are outside the current security scan | Medium | Phase 0 |
| P1 | Add PEP 621 packaging, stable commands, version plumbing, and release-spec scaffolding | Operators currently depend on a repository checkout and internal script names | Medium-large | Phase 1; release promotion in 2.4 |
| P1 | Finish the 24-cell owner-reviewed retrieval calibration | The current portable suites cannot qualify a production corpus or retrieval configuration | Medium plus human review | Phase 2 |
| P1 | Make readiness and qualification distinct product states | `READY` proves artifact coherence, not retrieval or answer-evidence quality | Medium | Phase 2 |
| P1 | Add branch/subprocess coverage, typed leaves, warning and lint ratchets | The large compatibility surface is protected mainly by tests and a very small lint rule set | Large, incremental | Phase 3 |
| P1/P2 | Add a manifest-backed run catalog plus unified plan/inspect commands | Users must currently copy long path/model/backend commands and interpret several separate diagnostics | Medium-large | Deferred until after Phase 4 |
| P1/P2 | Add privacy-safe service observability and retrieval explanations | Request IDs exist, but operators lack bounded route latency, health transitions, and rank provenance | Medium-large | Deferred until after Phase 4 |
| P2 | Add A1 command/retrieval/service performance baselines | A0 protects architectural identity; it does not yet establish capacity budgets | Large, incremental | Phase 3/4 |
| P2 | Move pipeline ownership behind the `rag.py` facade | The facade is the dominant runtime hotspot, but a broad move is unsafe without stronger guardrails | Large | Phase 4 |
| P2 | Split source reconstruction from chunk publication | The largest chunking/orchestration functions combine many failure domains | Large | Phase 4 |
| P2 | Generate quality and OpenAPI structures from separate single-source descriptors | Builders and validators/routes and schemas are manually mirrored | Medium-large | Separate Phase 4 slices |
| P3 | Add field-aware BM25/fusion experiments | It may improve legal retrieval, but only a frozen calibration can justify promotion | Medium-large | After Phase 2 |
| P3 | Calibrate selective low-confidence OCR repair | Potentially valuable, but it needs authorized failure labels before adding parser complexity | Large | Defer |

## Cross-cutting rules

- Preserve `rag.py` public names, mutation seams, CLI behavior, artifact bytes,
  schema compatibility, error precedence, and process-isolation contracts
  unless a task explicitly declares and tests a versioned behavior change.
- Treat private PDFs, derived text, queries, judgments, and prompts as
  sensitive. Commit only generated/CC0 fixtures and content-free receipts.
- Keep dependency changes in one compatibility domain per pull request. Never
  hand-edit generated lockfiles.
- Every source-changing checkpoint refreshes the architecture inventory. Freeze
  paired Phase A0 evidence only after the intended source checkpoint is stable,
  then keep the evidence commit documentation-only as required by the current
  benchmark policy.
- Use characterization and differential tests before moving established code.
  A refactor does not absorb discovered behavior fixes.
- Every new persisted artifact or report gets strict size limits, a schema
  version, atomic publication, canonical serialization, explicit privacy
  classification, and malformed/stale/future-version tests.
- Human decisions are gates, not checkboxes an implementation agent may infer.
- Tasks are workstreams, not instructions to combine every checkbox into one
  pull request. Follow the per-pull-request gate below and split independently
  governed files, dependency domains, or behavior changes.

## Phase 0: Restore planning and gate trust

### Task 0.1: Reconcile the roadmap and evidence ledger

**Files:** `ROADMAP.md`, `docs/README.md`, new `docs/evidence/` ledger files;
optionally a dependency-light architecture-summary tool and test.

- [x] Move point-in-time histories and exact test-count narratives into
  immutable evidence-ledger entries keyed by commit/tree and PR.
- [x] Keep the live roadmap limited to current status, dependencies, owner
  decisions, active constraints, next actions, and acceptance gates.
- [x] Reconcile every Phase A0 and R1/R2 statement with the current integrated
  state; remove or explicitly renew the pre-R1 freeze.
- [x] Record one unambiguous answer to: may R7/A1, R8c-6, packaging, and product
  work start now?
- [ ] **Owner decision pending:** record the R0C disposition for cloud/provider
  and remote-model-code paths: unsupported, experimental with explicit
  consent, or qualified. Do not infer a support tier from existing code.
- [ ] **Owner decision pending:** resolve the private-source policy for tracked
  evaluation metadata, retained CI artifacts, owner-only diagnostics, and
  release evidence before any new private-corpus artifact class is created.
- [x] Route current architecture aggregates through
  `python tools/check_architecture_inventory.py` rather than copying volatile
  counts into live prose.
- [x] Preserve old evidence by moving it; do not delete provenance.

**Acceptance:** One current-status section has no contradictory pending/frozen
claims; every historical claim links to an immutable evidence entry; the docs
map identifies the live plan and authority order; and the change introduces no
new or expanded private-derived content.

**Mechanical status:** Complete. The R0C and private-source owner decisions
remain open and retain their fail-closed interim rules. They do not block Task
0.2, but they block the dependent work named in `ROADMAP.md`.

**Deferred, non-blocking follow-up:** Add a generated hotspot view at the next
authorized source-changing checkpoint. Do not invalidate the current docs-only
A0 evidence pair solely to add it.

### Task 0.2: Make CI promotion deterministic

**Files:** `.github/workflows/ci.yml`, the security-owned canonical active
workflow at `.github/ci/active-ci.yml`, `ci-security-ownership.json`,
`tools/check_ci_security.py`, the CI-security ADR, new risk-classifier
policy/tool, and focused tests.

- [x] Define machine-readable path/ownership groups for service, vector,
  process supervision, storage/publication, evaluation, packaging, security,
  documentation-only, and unknown changes.
- [x] Run the promotion bootstrap from base-branch-trusted code and policy,
  using the event's exact base/head SHAs and a NUL-delimited, rename-aware
  `git diff --name-status -z -M`. Never use `pull_request_target` with a head
  checkout or execute classifier code from the pull-request head.
- [x] Permit the documentation fast lane only when every surviving diff
  endpoint is a regular Git blob. Type changes, symbolic links, gitlinks, and
  missing or unverifiable modes fail closed to the full lane.
- [x] Select required jobs from those groups. Unknown, policy, workflow, or
  classifier changes fail closed to the full lane.
- [x] Retain manual and branch-name controls only as force-heavy overrides;
  they must never be the sole trigger for required validation.
- [x] Add an `if: always()` aggregate job that distinguishes "passed,"
  "intentionally not required," and "incorrectly skipped" by inspecting every
  `needs.*.result`; failed, cancelled, timed-out, unexpectedly skipped, or
  unavailable classifier/heavy jobs fail the aggregate.
- [x] Bind the active decision to the exact tested candidate separately from
  the raw event head. In the active topology, run every execution job against
  GitHub's synthetic merge SHA and verify its exact base/head parents; on merge
  queues, use the merge-group candidate. The force-full seed bootstrap has no
  promotion aggregate: Phase A0 alone checks raw evidence head `E` so its
  retained report can bind that immutable commit, while the other seven jobs
  continue to test the synthetic candidate. That bootstrap artifact is not
  active promotion evidence.
- [x] Test representative service, vector, worker, retention, dependency,
  evaluator, packaging, and documentation diffs plus rename/delete, malicious
  classifier/workflow edits, a digest-valid but semantically weakened
  decision, a local upstream-only fork-ref simulation, job failure,
  cancellation, upstream failure, and classifier failure.
- [ ] Pass the activation checkpoint's hosted fork pull request, including its
  read-only token, contributor-approval, and ephemeral-ref behavior. Exercise
  target-base movement: a stale candidate must reject, and the retriggered run
  must bind the regenerated candidate's exact base/head parents.
- [x] Run a checksum-pinned `actionlint` preflight over both full-workflow
  digest forms; do not treat digest rebaselining as schema or expression
  validation. Local `actionlint` 1.7.12 returned no findings for either form
  from the Windows amd64 archive with SHA-256
  `6e7241b51e6817ea6a047693d8e6fed13b31819c9a0dd6c5a726e1592d22f6e9`.
- [x] Preserve the reviewed active workflow bytes as the security-owned
  `.github/ci/active-ci.yml`. Seed validation checks that fixture as the exact
  active topology and checks the live workflow as its exact rendered
  force-full bootstrap. The renderer changes only the Phase A0 checkout from
  active candidate SHA to literal seed head in addition to replacing the lane
  and removing the aggregate. Activation copies the fixture to the live
  workflow and revalidates its normalized bytes, digest, all-candidate
  checkouts, and topology.
- [x] Dry-run the complete `S → E` evidence choreography in an isolated clone
  on CPython 3.12.13 x86-64. Clean Windows and Linux 9×5 reports shared one
  source/lock/scenario contract, `E` changed only the two allowed baselines,
  and both clean-head comparisons at `E` passed. These reports bind the
  disposable simulation commit and must not be copied into the real branch.
- [ ] Freeze the final clean source commit `S`, regenerate both reports from
  that exact commit, and create direct child `E` containing only the reports
  and fixed evidence/provenance allowlist. Preserve `S → E` without squash,
  rebase, or source amendment; `E`, not red-against-old-baseline `S`, is the
  force-full seed head submitted for promotion.
- [x] Re-verify whether branch protection/rulesets are available. If so, make
  the aggregate a required check; otherwise require an exact-SHA manual or
  merge-queue promotion record that verifies its success before merge. The
  2026-08-18 recheck found neither capability, so the external record is the
  current requirement.

**Acceptance:** A safety-sensitive fixture diff cannot produce a successful
aggregate result unless its mapped heavy jobs ran and passed. A docs-only diff
uses the fast lane. Classifier changes always exercise the full lane, including
when the head attempts to weaken its own classification. Integration has two
promoted stages and three immutable identities. First freeze the inert trusted
implementation and force-full workflow as clean source `S`. The old baselines
are expected to reject `S`; generate the two new platform reports from clean
`S`, then create its direct gate-only evidence child `E`. Promote `E` as the
force-full seed head after every heavy job passes while retaining and
validating `.github/ci/active-ci.yml` as the canonical active form. The seed's
Phase A0 evidence binds raw `E`; the external record separately identifies the
synthetic candidate tested by the other jobs and must not describe the raw-head
artifact as proof that candidate ran Phase A0. Then create workflow-only
activation `A` by copying that fixture to
`.github/workflows/ci.yml` from `E`. `A` must prove the live workflow matches
the fixture, restore Phase A0 to the synthetic candidate alongside all other
execution jobs, pass a hosted fork-PR case, and bind the exact tested candidate,
base, and head in its external promotion record. History-preserving
`S → E → A` ancestry is mandatory; a squash, rebase, source amendment, or
material target base movement invalidates the generated reports.

**Source-candidate preparation status (recorded before `S`):** The force-full
source-candidate tree is authored and locally validated. The live workflow
exactly equals the bootstrap rendered from the governed active fixture, the
refreshed candidate architecture inventory passes, and the full local suite,
focused adversarial gates, and a non-authoritative local 9×5 Phase A0 run are
green. A disposable cross-platform simulation also proved the `S → E`
choreography, but its reports are not branch evidence. At this preparation
point no final exact `S` or source-bound evidence head `E` exists. Once those
identities exist, record them and the live Task 0.2 state only in `ROADMAP.md`,
`benchmarks/README.md`, and the Phase A0 ADR, which are members of the fixed
evidence allowlist; do not amend this plan in `E`. Task 0.3 must not start until
`E` and activation `A` have the required hosted and external promotion records.

### Task 0.3: Establish the transitive-advisory policy

**Files:** dependency-domain ADR and policy, `dependency-compatibility-domains.json`,
`dependency-vulnerability-policy.json`, dependency checker/tests,
and supply-chain tests.

- [ ] Obtain the owner decision between direct promotion and the ADR's
  reviewer-bound exception path. Recommended default: add the narrow,
  machine-readable transitive override because recurring transitive CVEs should
  not require pretending every package is a product-owned direct dependency.
- [ ] Bind each override to exact package names, reason/advisory, reviewer,
  source compatibility domain, expiry, and permitted lock delta; fail closed on
  unrelated or direct-package changes.
- [ ] Land and independently review this checker/policy mechanism before any
  transitive-only lock change uses it.

**Acceptance:** Positive and adversarial policy fixtures prove that only the
named transitive packages may move, direct or unrelated packages cannot hide
inside the exception, expiry fails closed, and the reviewed mechanism is
integrated before the lock-remediation pull request begins.

### Task 0.4: Remediate the urgent dependency findings

**Files:** `tools/refresh_locks.py`, affected requirement locks,
`dependency-vulnerability-policy.json`, security evidence, and compatibility
tests.

- [ ] Rebase and validate the parked aiohttp/cryptography/h2 lock refresh under
  the integrated Task 0.3 mechanism; prove a second no-flag refresh is
  byte-identical and run the full locked gates.
- [ ] Resolve or renew the Chroma exception before its deadline.
- [ ] Do not wait for Node or repository-scanner work to dispose of the urgent
  Python advisories.

**Acceptance:** The exact Python locks pass vulnerability, license,
compatibility-domain, full-suite, real-client, and transport-conformance gates;
no exception is expired; generated locks are byte-stable on a no-change rerun.

### Task 0.5: Resolve distribution and repository licensing

**Files:** owner-approved legal/governance ADRs or notices, package/release
metadata, and license-policy tests. No dependency locks in this change.

- [ ] Record the applicable PyMuPDF commercial or AGPL distribution basis, or
  remove/replace the dependency before distribution.
- [ ] Choose an explicit repository code license or record intentional
  proprietary/all-rights-reserved status.
- [ ] Bind packaging and release eligibility to those decisions without
  embedding private legal records in public artifacts.

**Acceptance:** An identified owner approves both dispositions; packaging and
release gates fail when either decision is missing, expired, or incompatible
with the requested distribution profile.

### Task 0.6: Add Node supply-chain ownership

**Files:** `.github/dependabot.yml`, security workflow and policy,
`ci-security-ownership.json`, Node validator metadata, and supply-chain tests.

- [ ] Add the Node ecosystem to Dependabot and the security ownership map.
- [ ] Audit the exact `package-lock.json`, produce an SBOM, and retain a
  content-free report under the same expiry policy as Python artifacts.
- [ ] Pin every scanner/action to an exact reviewed version. Put Node manifests,
  scan configuration, and workflow paths into `ci-security-ownership.json` with
  symmetric pull/push/schedule coverage.

**Acceptance:** The exact Node lock passes its policy; a vulnerable-lock fixture
fails. A normalized SBOM is byte-stable for the same inputs/tool/build epoch.
Live advisory results bind scanner, scan time, and advisory-database identity
rather than claiming byte stability across feed updates.

### Task 0.7: Add redacted repository secret scanning

**Files:** security workflow and policy, `ci-security-ownership.json`, pinned
scanner inputs/locks, suppression policy, and secret canary fixtures.

- [ ] Add a pinned content-aware secret scanner with reviewed fixture and
  suppression policy. Scan the full tracked tree on every pull request and the
  intended history on schedule/release; prohibit an open-ended baseline file.
  Ensure console and uploaded-artifact output cannot echo matched values.
- [ ] Pin every scanner/action to an exact reviewed version. Put scanner inputs,
  scan configuration, scanner workflow paths, and owners into
  `ci-security-ownership.json` with symmetric pull/push/schedule coverage.

**Acceptance:** Secret canaries fail without their matched bytes appearing in
console output or uploaded artifacts; clean full-tree and history scans pass;
configuration/workflow changes remain security-owned and symmetric;
suppression requires a narrow reviewed record with an expiry.

### Task 0.8: Add narrow Python static-security scanning

**Files:** test/audit requirements and locks, security workflow/policy,
`ci-security-ownership.json`, and positive/negative rule fixtures.

- [ ] Select one pinned, high-confidence Python static-security ruleset only
  after its false positives are reviewed and owned.
- [ ] Add positive and negative fixtures for every selected rule; do not enable
  an unbounded generic ruleset or mix remediation with the gate-introduction
  change.
- [ ] Redact and retain results under the same workflow ownership and expiry
  rules as the secret scan.

**Acceptance:** Every selected rule catches its positive fixture and accepts its
negative fixture; the clean tree passes; output and artifacts contain no source
snippets, absolute paths, credentials, or private values.

## Phase 1: Establish an installable packaging candidate

Start only after every Phase 0 exit passes and `ROADMAP.md` records explicit
product-work and distribution authorization.
This phase adds distribution mechanics, not a release tag, production support
claim, or assertion that the corpus/configuration is qualified.

### Task 1.1: Create product and compatibility metadata

**Files:** new dependency-light product metadata module, schema-compatibility
registry, `service_http.py`, `service-openapi-v1.json`, CLI tests, release tests.

- [ ] Define one product version source with an explicit unreleased development
  value and expose it through Python, CLI `--version`, service metadata, and
  packaging records. Selecting the first release version remains deferred.
- [ ] Replace hard-coded service `1.0.0` values without coupling independent
  artifact schemas to the product version.
- [ ] Inventory every persisted schema/policy version and define current,
  readable, writable, migration-required, and unsupported ranges.
- [ ] Add a generated compatibility report and tests that prevent an
  unregistered persisted schema from entering a release.
- [ ] Freeze supported exit codes and the public facade policy.

**Acceptance:** CLI and service development versions agree and are visibly
unreleased; the static OpenAPI snapshot is exact; every persisted schema has an
owner and compatibility disposition; old, future, and malformed versions fail
according to the registry.

### Task 1.2: Add PEP 621 packaging and stable entry points

**Files:** `pyproject.toml`, small entry-point adapters, requirements-policy
checker, launcher/packaging tests, README quickstart.

- [ ] Keep the reviewed `requirements*.txt` inputs as dependency authority and
  expose the applicable direct sets through dynamic build metadata; add a gate
  that fails on metadata/input drift.
- [ ] Add a pinned build backend and package-data allowlist. A `src/` move is
  explicitly out of scope.
- [ ] Add stable installed commands for pipeline, evaluation, service,
  inspection, and model synchronization.
- [ ] Preserve the physical `job_manager`, supervised worker, and search worker
  roles when installed outside the repository working directory.
- [ ] Build from a clean source archive, inspect the wheel inventory, and prove
  it contains no PDFs, outputs, caches, credentials, evaluation reports, or
  other ignored/private data.
- [ ] Build twice with a normalized build epoch and require byte-identical
  artifacts on each named Windows/Linux packaging cell; compare inventory and
  metadata across operating systems even where wheel container bytes
  legitimately differ. Passing these cells does not yet make them Tier 1.
- [ ] Install with `--no-index` from a hash-verified wheelhouse, or preinstall
  the exact locked environment and install the product wheel with `--no-deps`.
  From a working directory outside the checkout, run `pip check`, all
  entry-point help/version commands, a generated no-op flow, and each physical
  child role.

**Acceptance:** A clean machine can install from the documented locked inputs
and use supported commands without a repository checkout or `python rag.py`.
Wheel contents and metadata are deterministic and privacy-safe on the named
Windows and Linux packaging cells.

### Task 1.3: Build executable operator docs and release-spec scaffolding

**Files:** release-spec contract/tool, `README.md`, focused guides under
`docs/`, documentation-example checker, migration/rollback tests.

- [ ] Define a source-controlled release-spec schema that can record
  `pending`, `unqualified`, and `unsupported` states. It must not try to contain
  its own final commit identity or post-commit workflow conclusions.
- [ ] Convert the existing vector migration rehearsal into documented backup,
  preflight, restore, rollback, corrupt-backup, and sibling-preservation flows.
- [ ] Split the README into a short quickstart/architecture index and focused
  installation, ingestion, retrieval, evaluation, service, privacy/security,
  migration, and contributor guides.
- [ ] Extract every shell example into a machine-readable inventory; parse all
  commands and run safe examples against installed entry points.
- [ ] Add `SECURITY.md`, vulnerability-reporting/support policy, credential
  rotation, incident handling, and accurate logical-deletion language.

**Acceptance:** A disposable environment reproduces the pending release spec, migrates and
rolls back both vector backends, rejects corrupt backup input without touching
the active generation, and executes every supported documentation example. The
release spec remains explicitly pending and makes no unapproved support claim.

## Phase 2: Make semantic qualification explicit

Owner review may proceed in parallel with Phase 1, but private calibration must
not run until the owner freezes the query and judgment identities.

### Task 2.1: Complete corpus-owner review

**Files:** private review packet/receipt outside tracked source; existing review
and release-policy code; content-free tracked status only.

- [ ] Have the corpus owner accept, edit, reject, or defer every current query
  and judgment.
- [ ] Freeze one immutable reviewed generation before inspecting calibration
  outcomes.
- [ ] Record owner, date, corpus/query/judgment digests, allowed report fields,
  and re-review triggers in a content-free receipt.
- [ ] For the current small packet, finish review directly. Build a resumable
  append-only TTY review workbench only before expanding the judged set.

**Acceptance:** No code path or agent-generated label can promote a release;
the release policy binds the exact owner-reviewed generation and fails closed
on any mismatch.

### Task 2.2: Add a declarative 24-cell calibration runner

**Files:** new dependency-light evaluation-matrix domain, `eval.py` facade,
evaluation contracts/release policy, synthetic fixtures, focused tests.

- [ ] Declare the exact product of 4 retrieval modes, context windows 0/1/2,
  and table children disabled/enabled.
- [ ] Bind both table-disabled and table-enabled chunk/index generations to one
  canonical parent corpus generation. Attest that their permitted difference
  is exactly the versioned table-child policy; never treat them as one index.
- [ ] Bind model locks, both index generations, filters, seeds, scorer versions,
  and every parameter before the first cell runs.
- [ ] Support crash-safe resume without accepting duplicate, missing, stale, or
  foreign cells.
- [ ] Keep owner-only full diagnostics in approved private storage with explicit
  retention. Emit content-free per-cell summaries plus one content-free
  completeness and selection receipt containing only approved hashes, counts,
  and metrics; never report only favorable cells.
- [ ] Measure ranking, reviewed-fixture claim/citation support against explicit
  owner-approved `entailed_by` labels, unsupported claims, abstention,
  prompt/evidence size, latency, and parent/child displacement. This metric is
  not automated proof of general semantic entailment or answer correctness.
- [ ] Extend paired analysis to Recall, nDCG, MAP, grounding, and safety; declare
  one primary metric, tie-breakers, and a reviewed multiplicity policy.
- [ ] Prove matrix mechanics with generated/CC0 data before running private
  owner-reviewed calibration.

**Acceptance:** Exactly 24 unique cells share one immutable experiment identity;
interruption/resume is deterministic; missing/duplicate/drifted cells fail;
selection rules are predeclared; tracked/retained content-free evidence contains
no source text, while owner diagnostics follow the approved private policy.

### Task 2.3: Add a content-free qualification receipt

**Files:** new qualification contract/policy, `rag.py` compatibility surface,
query/answer guards, `info` output, and qualification tests. UI, catalog, and
final release-attestation integration remain later tasks.

- [ ] Model `artifact_ready`, `structure_qualified`, `retrieval_qualified`,
  `answer_evidence_qualified`, and `production_qualified` as separate states.
- [ ] Define independent evidence for each axis. Answer-evidence qualification
  binds a human-reviewed answer suite, exact prompt contract, provider/model
  revision, decoding parameters, and grounding scorer; it is not a certificate
  of general semantic correctness.
- [ ] Bind qualification to the READY receipt, exact corpus/profile generation,
  chunk/index/model identities, review receipt, evaluation policy/reports,
  policy-approved or hashed owner identity, review date, expiry, and
  invalidation triggers.
- [ ] Define allowed transitions and issuers/revokers. Compute
  `production_qualified` from required prerequisite axes; do not allow it to be
  asserted independently.
- [ ] Build and test the contract on generated/CC0 fixtures early, but do not
  activate private-corpus warnings/refusals until Task 2.2 has all 24 cells,
  table-child aliases and floors are owner-approved, and the qualification
  policy is signed off.
- [ ] In a development profile, unqualified use may proceed only through an
  explicit acknowledgement and produces evidence marked unqualified. The
  release profile fails closed when a required axis is absent, stale, expired,
  or overridden.
- [ ] Surface the axes in `info`. Defer UI/catalog display to the post-ownership
  product phase and populate the release attestation only at final promotion.
- [ ] Add downgrade/invalidation tests for profile, chunks, index, model,
  evaluator, prompt/provider/decoding, owner-review, expiry, override, issuance,
  and revocation changes.

**Acceptance:** Operators can distinguish internal artifact coherence from
retrieval and answer-evidence qualification. No stale qualification survives
any bound identity change.

### Task 2.4: Promote the first release only after all roadmap gates

**Dependencies:** Phase 0; R0C and private-source decisions; completed R2/R3/R4
evidence; R6 vector-client disposition; Tasks 1.1-1.3; and owner release review.

**Files:** release specification, post-commit attestation workflow/tool,
version metadata, compatibility/support documentation, changelog/release notes,
and release tests.

- [ ] Select the first product version and replace the unreleased development
  value only after every dependency is satisfied.
- [ ] Define Tier 1/2/3 claims for OS/Python, CPU/CUDA, vector backend,
  structure profile, and cloud/model-code support from actual evidence and the
  R0C/R6 decisions.
- [ ] Finalize the source-controlled release specification with dependency,
  model, schema, migration, evaluation, and qualification identities. It must
  remain non-circular and cannot name its own eventual commit.
- [ ] After the immutable release commit exists, generate a separate CI
  attestation/release asset that binds commit/tree, release-spec digest, lock
  digests, and exact workflow conclusions/URLs.
- [ ] Choose one authenticity model: a protected CI artifact attestation or an
  owner-controlled signature with documented custody, rotation, revocation,
  and verification. A plain digest may provide integrity but must not be
  described as publisher authentication.
- [ ] Tag and publish only the exact reviewed commit; attach no private corpus
  or owner-only diagnostic artifact.

**Acceptance:** Version, CLI, service, release spec, post-commit attestation,
tag, and release notes agree; every claimed support cell and qualification axis
has applicable passing evidence; authenticity verifies under the selected
trust model; rollback and clean-environment reproduction pass.

## Phase 3: Add evolution guardrails and capacity evidence

Start only after Task 2.4 establishes the first release and `ROADMAP.md`
records Phase 3 authorization.

### Task 3.1: Add branch and subprocess coverage

**Files:** `pyproject.toml`, test/audit requirements and locks, CI, coverage
helpers, focused tests.

- [ ] Pin Coverage.py through the test/audit compatibility domain.
- [ ] Enable branch measurement and subprocess startup/combination for
  supervised workers through a test-only wrapper or frozen binding. Prove the
  production release profile rejects or strips coverage injection exactly as
  it does other unapproved child environment state.
- [ ] Publish a diagnostic full-tree baseline first; do not choose a vanity
  whole-repository percentage.
- [ ] Define the critical-module and changed-line denominator in a
  machine-readable policy; ratchet safety-critical leaves and changed
  safety-sensitive lines with explicit exclusions for generated/static
  contract snapshots.
- [ ] Use relative file identities. Retain content-free machine-readable
  coverage, JUnit, slow-test, and warning summaries; do not upload HTML/source
  views or absolute paths.

**Acceptance:** A fixture proves child-process branches contribute to the
combined report; a changed critical branch cannot lose coverage; report paths
and contexts reveal no private data.

### Task 3.2: Add typed leaves, staged lint, and warning budgets

**Files:** `pyproject.toml`, selected stdlib-only leaves, test/audit locks, CI.

- [ ] Pin one type checker and start with stable contract/policy leaves such as
  endpoint, embedding, operation, evaluation contract/inputs, release security,
  and table retrieval modules.
- [ ] Prohibit blanket ignores and track each narrow suppression with an owner
  and reason.
- [ ] Add Ruff families one at a time after a zero-diagnostic cleanup; avoid a
  whole-tree formatting or semantic rewrite.
- [ ] Establish per-OS/Python warning budgets using a normalized fingerprint of
  category, owning distribution/module, and stable message (never line number).
  Unknown first-party warnings fail immediately; known third-party warnings
  have explicit count ceilings. Retire the current FastAPI/Starlette/websockets
  Python 3.16 deprecations through the service/UI dependency domain instead of
  globally suppressing them.

**Acceptance:** Selected leaves are type-clean on supported Python versions;
new warnings and newly enabled lint violations fail; compatibility tests prove
that cleanups do not change contracts.

### Task 3.3: Implement A1a/A1b/A1c benchmarks

**Files:** benchmark domain/tool, CC0/generated scenarios, baseline records,
CI and benchmark documentation.

- [ ] A1a: parser/dispatch, artifact construction/publication, quality
  build/validation, and OpenAPI generation.
- [ ] A1b: cold/warm lexical retrieval, context assembly, table collapse,
  vector planning, evaluation scoring, and review-packet materialization.
- [ ] A1c: service construction/search, bounded queue saturation,
  cancellation, recovery, containment, and cleanup.
- [ ] Run at least five repetitions and bind source, OS/Python, hardware,
  locks, scenario, warm/cold state, and exact deterministic outputs.
- [ ] Gate deterministic counts/bytes first. Activate wall-time/RSS ceilings
  only after a predeclared number of independent runs on each Tier-1 OS records
  the percentile formula, outlier policy, variability statistic/ceiling, and
  platform-specific budget formula.

**Acceptance:** Injected deterministic regressions always fail; timing/RSS
reports are redacted and comparable; wrong platform/runtime/lock/scenario,
partial repetitions, non-finite metrics, and dirty-source reports fail; each A1
group gates only its corresponding later architecture slice.

### Task 3.4: Evaluate field-aware lexical/fusion changes after A1b

**Dependencies:** Task 2.4 first release, recorded Phase 3 authorization, the
owner-frozen Task 2.2 matrix, and a passing A1b controlled retrieval benchmark.

**Files:** `retrieval_core.py`, evaluation policy and fixtures, versioned
experimental configuration.

- [ ] Implement an in-repository field-aware scorer for body, headings, case
  names, citations/statutes, and table captions/headers behind an experimental
  flag.
- [ ] Keep the current behavior as the release default and treat scorer version
  as an experiment dimension: baseline and experimental policies each run the
  complete 24-cell matrix, for 48 bound cells in the paired comparison.
- [ ] Include expanded hard-negative, ambiguity, numeric, multi-hop, filter,
  abstention, reviewed-fixture support, and A1b latency/evidence-budget results.
- [ ] Before results are visible, predeclare the estimator/test, alpha or
  interval, multiplicity correction, minimum effect, minimum effective sample
  size or power target, and tie policy. `Inconclusive` is an explicit outcome
  and cannot promote a new default.
- [ ] Promote only when the predeclared primary-metric rule passes and there is
  no grounding-support, safety, hard-negative, latency, or evidence-budget
  regression.

**Acceptance:** Both scorer policies are versioned and reproducible across all
48 cells; default bytes remain unchanged until an owner-approved qualification
record promotes the new policy.

## Deferred product backlog: execute after runtime Phase 4

These contracts may be refined on paper earlier, but do not add their
`rag.py`, UI, service, or persisted-production wiring until the Phase 4
ownership-only move and applicable consumer migration are complete. This avoids
growing the compatibility surface immediately before moving it.

### Task D.1: Add a manifest-backed run catalog

**Files:** new run-catalog domain, `rag.py` CLI facade, job/UI adapters, tests.

- [ ] Discover runs only from validated ownership/publication manifests.
- [ ] Add `runs list/show` plus `query --run`, `inspect --run`, and `eval --run`.
- [ ] Never silently choose the newest run when more than one candidate exists.
- [ ] Resolve chunks, backend, collection, embedding, profile, READY receipt,
  and qualification identities atomically.
- [ ] Reject aliases, replaced paths, incomplete manifests, and stale indexes.

**Acceptance:** One stable run ID replaces manual copying of path/model/backend
tuples without weakening current identity, lease, or publication checks.

### Task D.2: Unify preflight and post-run diagnosis

**Files:** ingestion/model-plan composition adapters, new plan/inspection
contracts, CLI/UI, tests and short guides.

- [ ] Add a read-only `plan --pdf ... --json` that combines triage, resolved
  settings, model/cache/download/disk needs, egress features, output target, and
  known/unknown cost bounds without writing or network access.
- [ ] Add `inspect --run ... --json` for conversion confidence, glyph/OCR
  issues, structure and quality gates, table/linkage health, publication/vector
  parity, qualification, stable issue codes, and remediation actions.
- [ ] Define separate private-local and redacted JSON views. The redacted view
  uses opaque run/path hashes, issue codes, and `--run` IDs or placeholders;
  it never emits credentials, endpoints, filenames, raw paths, or source text.
  The private-local view is access-controlled and retained under private policy.
- [ ] Make both contracts bounded, deterministic, and reusable by the UI.
- [ ] Fix the existing document-stage/images-stage warning wording and avoid
  retrying one failed shared background-image xref for every referencing page.

**Acceptance:** Operators can answer "what will this run need?" and "why is
this run not usable/qualified?" through stable JSON without reading logs. The
redacted contract is safe to retain; the private-local view never crosses its
approved storage/access boundary.

### Task D.3: Add redacted local service telemetry

**Files:** `service_http.py`, `service_runtime.py`, `operational_metrics.py`,
`run_telemetry.py`, and service telemetry tests.

- [ ] Emit bounded route-template, status, duration-bucket, queue pressure, and
  health-transition events linked by request, job, attempt, and run IDs.
- [ ] Keep telemetry local and first-party: no exporter or network egress. Use a
  private bounded sink with rotation, retention, size, and cardinality limits,
  authenticated access, and explicit best-effort versus readiness-affecting
  failure semantics for each event class.
- [ ] Explicitly prohibit query text, answer text, tokens, credentials, source
  paths, bodies, raw exceptions, and private metadata from telemetry.
- [ ] Use route templates only, never raw paths or corpus/run IDs as metric
  labels. Correlation IDs may join bounded events but must not become
  unbounded-cardinality metric dimensions.
- [ ] Preserve the release policy that disables auxiliary external telemetry;
  sink publication is local-only and cannot load an exporter dynamically.

**Acceptance:** Fault injection proves end-to-end correlation and useful
operator signals while canary secrets/source/query/exception values never
appear in events. Full, corrupt, and unwritable sinks plus restart recovery
follow the declared failure policy.

### Task D.4: Add bounded retrieval explanations and evidence inspection

**Files:** retrieval result contracts, CLI adapter, `ui.py`, browser/unit tests;
service contracts only in a separately authorized API-version change.

- [ ] Add a bounded retrieval explanation containing lexical/dense ranks,
  fusion contribution, reranker delta, filters, table collapse, context budget,
  and requested-versus-effective mode.
- [ ] Add an evidence drawer with complete bounded excerpt, page/section,
  stable source ID, aliases, and context provenance. This is an explicitly
  private-local source display, not content-free telemetry.
- [ ] Replace UI raw exception/path rendering with fixed issue codes and local
  correlation IDs.
- [ ] Keep explanations CLI/UI-only initially. Adding them to service v1 is not
  implicit; it requires a versioned bounded schema, OpenAPI migration, privacy
  review, and explicit authorization.
- [ ] Add real-browser tests for first run, empty/error states, citations,
  cancellation/resume, keyboard/labels, hostile origins, injected markup, and
  recovery guidance using generated/CC0 fixtures. Screenshots and traces must
  be private-source-free even when they intentionally contain fixture text.

**Acceptance:** Explanations are bounded and identity-preserving; canary
secrets/private-source/query/exception values never appear in redacted errors,
telemetry, screenshots, or traces; the evidence drawer shows only the selected
private-local or generated/CC0 source under the declared access policy.

## Phase 4: Decompose the runtime in reversible slices

Start only after Task 2.4 establishes the release baseline, Phase 3 guardrails
and the applicable A1 baselines pass, the installable facade is frozen, and
`ROADMAP.md` records explicit R8/R9 authorization.

### Task 4.1: Prove the facade ownership design

**Files:** isolated feasibility tests, pipeline-composition ADR,
architecture-inventory policy/tests.

- [ ] Test a `ModuleType`-style single-namespace forwarding spike against the
  complete generated facade contract: reads, assignment/deletion, reload,
  `vars`, import-star, annotations, logger identity, monkeypatch restore, and
  pickle/import paths.
- [ ] Record the go/no-go result and rollback in the composition ADR.
- [ ] If proxy semantics fail, use explicit generated forwarding; do not ship a
  partially compatible module proxy.

**Acceptance:** The spike either reproduces every supported facade behavior or
is rejected with an explicit alternative. It changes no production ownership.

### Task 4.2: Move pipeline ownership behind the stable facade

**Files:** new `pipeline_runtime.py`, `rag.py`, architecture inventory and
facade/consumer tests.

- [ ] Perform a near-total Git-recognized move so the initial change is
  mechanically reviewable.
- [ ] Keep `rag.py` as import/executable facade with the exact supported mutable
  namespace and CLI behavior.
- [ ] Land the ownership-only move with zero consumer migration.
- [ ] In later, separately reviewed changes, migrate
  `service_search_worker.py`, then UI capabilities, then evaluator capabilities
  to narrow frozen bindings. Do not combine those three migrations.
- [ ] Preserve intentional physical child roles and the acyclic first-party
  graph.
- [ ] Give the ownership move and each consumer slice its own named
  owner/reviewer, exact commit/tree, architecture delta, and demonstrated
  rollback before the next slice starts.

**Acceptance:** Facade inventory, A0/A1, full failure-injection suite, exact
outputs, imports, mutation seams, and rollback all pass after each consumer
slice.

### Task 4.3: Establish the cross-surface error taxonomy

**Files:** error-policy/contract domain, CLI/service/job adapters, redaction and
failure-injection tests, architecture ADR.

- [ ] Characterize usage, policy, integrity, dependency, transient,
  cancellation, deadline, cleanup, and internal failures.
- [ ] Map each category to CLI exit code, HTTP problem, job terminal state,
  retryability, redaction, correlation, and operator remediation.
- [ ] Preserve primary-versus-cleanup failure precedence and exact legacy
  behavior until each surface explicitly migrates.
- [ ] Add differential tests across CLI, service, durable jobs, and supervised
  child boundaries before any large R9 hot-spot extraction.

**Acceptance:** Every characterized failure has one stable category and
consistent surface mapping; raw private paths/exceptions never escape; cleanup
cannot mask the primary failure; existing facade behavior remains exact until
its separately versioned migration.

### Task 4.4: Split source reconstruction and chunk publication

**Files:** new source-preparation and chunk-transaction domains,
`pipeline_runtime.py`, chunk/fidelity/quality/publication tests.

- [ ] Introduce immutable `SourcePreparationInput` and result records; model
  ordered recovery stages without publication side effects.
- [ ] Introduce a frozen chunk-operation configuration, injected collaborators,
  explicit stage results, and one publication coordinator.
- [ ] Characterize exact prepared order, source lineage, stable IDs, hashes,
  report bytes, error precedence, and every injected failure before moving.
- [ ] Move one stage per reviewable change; leave cache/lifecycle ownership for
  its own later slice.

**Acceptance:** No token is lost, duplicated, or reordered; semantic hierarchy,
quality/fidelity reports, and five-gate publication bytes remain exact; no
failed stage publishes prematurely.

### Task 4.5: Make the quality-report schema single-source

**Files:** `quality_core.py`, quality contract descriptors and differential
tests.

- [ ] Derive quality-report builder structure and structural validation from
  immutable field descriptors while keeping cross-field semantic invariants
  explicit.
- [ ] Run old and new implementations in differential tests before deleting
  mirrored code.

**Acceptance:** Quality report bytes/digests plus contractual validation
precedence and messages remain exact; rollback is demonstrated independently of
the HTTP/OpenAPI work.

### Task 4.6: Make route registration and OpenAPI single-source

**Files:** `service_http.py`, static OpenAPI snapshot, route descriptors and
differential tests.

- [ ] Drive FastAPI route registration and OpenAPI generation from one immutable
  route/operation descriptor.
- [ ] Run old and new implementations in differential tests before deleting
  mirrored route/schema code.
- [ ] Keep this change independent from the quality-report descriptor work.

**Acceptance:** Route identities, auth/role policy, validation behavior, and
`service-openapi-v1.json` remain exact; rollback is demonstrated without
touching quality-report code.

## Dependency map and execution rule

```text
0.1 roadmap truth
 └── 0.2 deterministic CI promotion
      └── 0.3 transitive policy decision/mechanism
           └── 0.4 urgent lock remediation
                └── 0.5 distribution/license decisions
                     └── 0.6 Node supply-chain ownership
                          └── 0.7 secret scanning
                               └── 0.8 static-security scanning
                                    └── Phase 0 complete plus explicit
                                         product/distribution authorization
                                         └── Phase 1 packaging candidate
                                              └── 2.1 owner label freeze
                                                   └── 2.2 matrix
                                                        └── 2.3 qualification
                                                             └── 2.4 release
                                                                  └── Phase 3
                                                                       guards/A1
                                                                       └── 3.4 experiment
                                                                       └── explicit R8/R9 authorization
                                                                            └── Phase 4 decomposition
                                                                                 └── deferred product backlog
```

The graph records dependency eligibility, not permission to execute
simultaneously. For this program, implementation follows the current
`ROADMAP.md` sequence. Owner review and paper design may proceed in parallel
without changing governed files. Never parallelize changes that edit the same
lock domain, facade namespace, schema bytes, or Phase A0 source checkpoint.

## Per-pull-request completion gate

Every implementation pull request must include:

- [ ] A single primary task and explicit non-goals.
- [ ] Characterization/failing tests before behavior or ownership changes.
- [ ] Focused tests plus `python -m pytest -q`.
- [ ] `python -m ruff check .` and `python tools/check_python_sources.py`.
- [ ] Applicable dependency, model, CI-security, architecture, evaluation,
  real-client, browser, packaging, and migration gates.
- [ ] `git diff --check` and a private-data/output inventory review.
- [ ] Updated ADR/contract and exact rollback instructions when a boundary,
  threat model, schema, support tier, or persisted artifact changes.
- [ ] Fresh architecture inventory for source changes and policy-compliant
  Phase A0 evidence at the stable checkpoint.
- [ ] Independent review and the owner decision for governance/release items.

## Explicit non-goals

- No public or multi-user service exposure.
- No new LLM provider, vector backend, arbitrary runtime structure profile, or
  remote plugin system.
- No whole-project typing, formatting, warning suppression, or coverage target.
- No broad `rag.py` rewrite, `src/` relocation, or simultaneous parser/chunk/
  backend extraction.
- No retrieval-score tuning against the tiny portable suites or unreviewed
  private labels.
- No claim that `READY` means semantically correct, secure erase means physical
  erasure, or process supervision is a sandbox.
- No private corpus content in tracked fixtures, reports, screenshots, logs,
  SBOMs, plans, or release assets.
