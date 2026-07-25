# Code, Pull-Request, and Documentation Audit

- **Audit date:** 2026-07-25
- **Frozen R1 commit:** `ed2995e4d5467a8999b3fa88634dc8af02d0e0f3`
- **Frozen R1 tree:** `c438c82933ec4c68c1bf019d81588915801a2916`
- **Clean pre-gate source:** `fdb08d26e3fc9fd2b80b983eeab7b1f353b96707`
- **Integration candidate:** draft [PR #44](https://github.com/toddlar00/rag-pipeline/pull/44)
- **Successor branch:** `agent/r2-dependency-domains`, based directly on the
  frozen R1 commit
- **Scope:** tracked code, tests, workflows, requirement policy, open and
  historical pull requests, maintained ADRs and roadmap, governance proposal,
  and the two Claude-authored plans under `docs/superpowers/plans/`

This is a point-in-time technical audit and improvement roadmap. It does not
approve private-source disclosure, corpus judgments, licensing, release scope,
cloud processing, or remote model code. Those decisions are explicitly listed
below.

## Executive conclusion

The project is a local-first PDF retrieval-augmented-generation pipeline. It
turns a source PDF into provenance-bound chunks, optional LLM enrichments,
embeddings, vector indexes, searchable results, cited answer candidates,
evaluation reports, service endpoints, and a local UI. Its strongest qualities
are deterministic artifact identity, fail-closed publication and resume rules,
process containment, dependency and model-artifact controls, unusually broad
failure-injection tests, and exact-head cross-platform evidence.

The frozen R1 candidate's exact-head automated gates are green, but it is not
release-approved. All 26 PR checks and the exact-head CI, dependency,
supply-chain, and Phase A0 Windows/Linux workflows passed. It still has no submitted human review, and
privacy/history, repository and dependency licensing, cloud/model-code support,
and corpus-owner decisions remain unresolved. The frozen commit must therefore
remain unchanged while successor work proceeds in separate, reviewable slices.

The highest material engineering risks are:

1. LLM enrichment responses are parsed too permissively in several ingestion
   paths and can be mistaken for trusted labels or structures.
2. Source-controlled text and exception details reach Markdown-oriented UI
   rendering without a complete hostile-content browser proof.
3. Dependency updates were previously grouped across unrelated compatibility
   domains; stale PR #30 does not exercise the versions it proposes.
4. Coverage, typing, broader lint, secret/static-security analysis, packaging,
   installed-command tests, and real-browser tests are not yet release gates.
5. `rag.py` is both a 16,000-line implementation and a compatibility facade
   observed through hundreds of public, private, import-star, and monkeypatch
   seams. Direct decomposition would be high risk.

The recommended order is: preserve and review R1; enforce dependency domains;
resolve owner-bound release decisions; introduce strict LLM and UI boundaries;
add gradual quality and packaging gates; then move `rag.py` implementation
ownership mechanically before decomposing orchestration. Capacity and storage
redesign should follow measurements rather than precede them.

## System purpose and execution map

The main data path is:

```text
authorized PDF
  -> extract/OCR and normalize
  -> recover scaffold, chapters, tables, and source lineage
  -> create bounded chunks and optional LLM enrichments
  -> validate and publish exact artifacts plus quality receipt
  -> embed and publish Chroma or Qdrant index generation
  -> retrieve, filter, hybrid-rank, rerank, and assemble neighbors
  -> optionally generate a cited answer or abstain
  -> evaluate, inspect, serve over loopback HTTP, or present in local UI
```

The important trust distinction is that retrieval evidence and model output are
not inherently trusted. Existing code strongly validates source identity,
publication generations, citations, and exact quotations. It does not prove
general semantic entailment or that a live model resists every prompt
injection. Release claims and UI wording must retain that distinction.

## Evidence examined

The audit used four evidence classes:

- the frozen commit and tree identities above, rather than a moving branch;
- tracked architecture inventory, source, tests, policies, and workflow files;
- GitHub PR topology, check suites, reviews, and dependency proposals; and
- maintained documentation plus the explicitly historical Claude plans.

Ignored `output/` data, local PDFs, and mutable workstation state are not
durable repository evidence. Corpus-derived facts must be represented only in a
form allowed by the eventual private-source policy.

The exact-head hosted run record is:

| Workflow | Pull-request run | Direct-dispatch run |
|---|---|---|
| CI | [30156965565](https://github.com/toddlar00/rag-pipeline/actions/runs/30156965565) | [30157011511](https://github.com/toddlar00/rag-pipeline/actions/runs/30157011511) |
| Dependency compatibility | [30156965536](https://github.com/toddlar00/rag-pipeline/actions/runs/30156965536) | [30157012241](https://github.com/toddlar00/rag-pipeline/actions/runs/30157012241) |
| Supply-chain security | [30156965542](https://github.com/toddlar00/rag-pipeline/actions/runs/30156965542) | [30157013125](https://github.com/toddlar00/rag-pipeline/actions/runs/30157013125) |

GitHub reports SHA-256 archive digests for the four retained A0 artifacts:
PR Linux `1c46f864e8200db8479007662b3b90743807edfffeedc3575afd4311bd3527f1`,
PR Windows `2b7c477081388e901e13df9a16bda17e4f57e0e986500235279ad4cd18c73e0f`,
direct Linux `002d4c846aaf9c7db866d799f16c5abdf4de50a284633c165b1258ba52eaffa7`,
and direct Windows
`9cdcaaf824780a3cd9ec22c16f4d36981071340f4aaea7c755c3bb8ce3e81825`.
These hosted archives remain retention-bound; the proposed manifest/ledger
must preserve their run IDs, digests, and independently verified internal
attestations before expiry.

### Quantified frozen-head architecture

| Measure | Frozen R1 value |
|---|---:|
| Tracked Python files | 150 |
| Source/tool modules | 69 |
| Test modules | 81 |
| Non-test Python lines | 61,568 |
| Test Python lines | 44,865 |
| Collected tests | 2,240 |
| Functions | 1,981 |
| Classes | 189 |
| Modules at least 1,000 lines | 15 |
| Functions at least 100 lines | 91 |
| Functions with at least 10 parameters | 56 |
| Literal `TODO`/`FIXME`/`HACK`/`XXX` markers | 0 |

The tracked first-party import graph is acyclic at the frozen inventory. The
absence of debt markers does not imply the absence of architectural debt; the
inventory and this audit make that debt explicit instead.

### Largest compatibility and maintenance hot spots

- `rag.main` is about 1,100 lines.
- `_chunk_document_locked` is about 876 lines with 25 parameters.
- `query_index` and `query_index_qdrant` each expose 30 parameters.
- The hand-built OpenAPI document is about 510 lines.
- Quality report construction and validation are each over 400 lines.
- Evaluation execution and CLI paths are each over 300 lines.
- Three test modules exceed 2,000 lines, led by index-manifest tests.

The `rag` facade records 565 explicit bindings, 573 runtime names, 377 callable
contracts, and 119 import-star names. Forty-one test modules consume it; tests
perform 919 reads of 223 distinct private names and 450 monkeypatch operations
against 143 top-level targets. This is why the facade must first be preserved
mechanically, not redesigned in place.

## What is already strong

The forward plan should preserve these capabilities:

- stable source, chunk, artifact, index-generation, and run provenance;
- exact generation and resume compatibility checks;
- atomic publication and stale-artifact rejection;
- contained child-process supervision, deadlines, cancellation, and cleanup;
- local endpoint validation, credential isolation, and default network denial;
- deterministic, hash-locked dependency resolution across supported Python
  versions;
- vulnerability, license, model-artifact, workflow-owner, and CI invariant
  checks;
- real Chroma and Qdrant client tests on Windows and Linux;
- synthetic, redistributable evaluation fixtures with explicit denominators;
- strict citation identity, placement, and exact-quotation validation;
- cross-platform architecture characterization and retained Phase A0 evidence;
- extensive failure-injection coverage for partial writes, retries, locks,
  crashes, sharing violations, and stale generations.

Improvements should add contracts around these behaviors, not casually replace
them.

## Pull-request audit

| PRs | Current meaning | Recommended disposition |
|---|---|---|
| #1-#2 | Merged histories later reconciled in #28 | Retain as audit history |
| #3-#27 | Closed after their exact histories were combined | Leave closed |
| #28-#29 | Merged cumulative implementation and integration record | Treat as current `main` baseline |
| #30 | Stale wildcard Dependabot proposal, old base, no regenerated locks | Supersede after domain policy is published; do not rebase mechanically |
| #31, #33-#38 | Focused draft implementation stack | Preserve focused review history |
| #32 | Standalone design draft superseded by implemented supervision work and ADR | Close after owner/documentation review |
| #39 | Cumulative only through #38 | Retain as migration rehearsal, not current candidate |
| #40-#43 | Later focused draft stack | Preserve as reviewable ancestry of #44 |
| #44 | Exact cumulative R1 candidate at `ed2995e` | Keep frozen; obtain human review and owner decisions; merge history-preservingly |

No open implementation PR has a submitted human review. GitHub reports #44 as
mergeable, but repository-plan limits mean required review and exact-head checks
are not mechanically protected. The merge checklist and evidence therefore
need a durable human-controlled record.

### Why PR #30 is not compatibility evidence

PR #30 edits direct requirement manifests but not the generated locks used by
its installed-suite jobs. Nine of its remaining ten target versions are already
selected in the frozen locks; only `google-genai` would materially resolve from
1.75.0 to 2.13.0. It also proposes Voyage, OpenAI, and Cohere SDK declarations
that the R1 tree deliberately removed or forbids. Raising already-satisfied
lower bounds would reduce compatibility without testing a different install,
while the Gemini major change needs its own adapter and transport proof. The
bounded provider-transport Dependabot group may propose `google-genai` and
`requests` together; when that happens, the PR must identify and test the SDK
major and HTTP-library delta as separate evidence lanes rather than claiming the
SDK changed alone. This follows
[GitHub's documented group behavior](https://docs.github.com/en/code-security/tutorials/secure-your-dependencies/optimizing-pr-creation-version-updates),
which consolidates matching dependencies into one update proposal.

The R2b-0 successor therefore replaces wildcard grouping with six exact,
non-overlapping domains and machine-checks all 26 normalized direct inputs in
the eight governed requirement manifests. Its PR-base gate rejects changes
spanning domains and requires every lock affected by each changed manifest;
each changed direct package must also change a selected record in at least one
mapped lock, so unrelated transitive churn cannot mask a no-op bound. There is
no inline no-op-bound exception. The legacy CUDA helper, including its separate
Torchaudio input, remains an
unqualified R5 support-tier surface. No dependency or lock version changes
belong in this policy-only slice.

## Code findings and improvement slices

### P0: strict LLM enrichment contracts

Several paths accept model output using substring matching or by extracting the
text between the first and last brace/bracket before ordinary `json.loads`.
Examples include content classification, TOC layout and verification, scaffold
generation, and TOC parsing. Reproducible frozen-head examples are
[`_analyze_toc_layout`](../../rag.py#L2633), TOC verification
[(truthy `verified`)](../../rag.py#L2788), scaffold extraction
[(first/last bracket)](../../rag.py#L3227), TOC parsing
[(first/last bracket)](../../rag.py#L4111), and substring classification in
[`_llm_classify`](../../rag.py#L4799). Other enrichment paths accept loosely
formatted context, summaries, scores, case briefs, flashcards, or RAPTOR
output.

Create a dependency-light `llm_output_contracts.py` and migrate one bounded
response family per PR. The shared parser should enforce:

- one exact JSON value, with no leading/trailing prose or second value;
- duplicate-key and non-finite-number rejection;
- UTF-8/control, depth, response-byte, array, item, and string limits;
- exact fields and types rather than Python truthiness;
- allowed enums and valid page/level/document ranges;
- explicit diagnostic codes for fallback or degradation; and
- no source text, prompt fragments, or provider responses in public errors.

Classification should accept exact normalized equality to one allowed label,
never a label merely appearing inside an explanation, negation, or multi-label
response. Tests must cover multiple JSON blocks, duplicate keys, `NaN`,
`Infinity`, deep nesting, oversized fields, false-like strings, embedded prompt
instructions, ambiguous labels, and valid boundary values.

Exit evidence: adversarial tests, fallback receipts, provider-independent
fixtures, unchanged local-only behavior when enrichment is disabled, and full
locked tests.

### P0/P1: safe UI presentation and child envelopes

Search results currently interpolate source text, metadata, warnings, links,
identifiers, and some exception strings into Markdown-oriented output. Unit
tests prove selected provenance and redaction behavior, but do not prove that
hostile HTML, Markdown, link protocols, forms, fetches, WebSockets, or raw
exceptions remain inert in a real browser. The frozen formatting and raw-error
path is visible in [`ui.py`](../../ui.py#L324), through the Markdown result
return at line 376.

Introduce a presentation/view-model boundary that:

- escapes or sanitizes every untrusted field under one documented policy;
- allows only reviewed link schemes and local targets;
- returns stable public errors plus a local correlation identifier;
- records detail in private logs with existing redaction rules;
- strictly bounds and validates supervised child request/result envelopes; and
- keeps the supported UI loopback-only and trusted-single-user unless a new
  threat model is approved.

Exit evidence: unit tests for every field, real-browser hostile-content tests,
network interception proving no unintended origin access, cancellation/resume
tests, accessibility checks, bounded content-free screenshots/traces, and both
Windows and Linux runs.

### P1: gradual quality ratchets

Current Ruff configuration selects only `E4`, `E7`, `E9`, and `F`. There is no
authoritative branch-coverage gate, gradual type gate, content-aware secret
scan, or narrowly configured static-security gate.

Add them without a single broad-suppression migration:

1. Measure branch coverage with subprocess data combination and publish the
   exact baseline. Ratchet no regression globally and set higher floors only
   for changed safety-critical modules.
2. Pin mypy or Pyright and start with existing dependency-light leaves. A
   read-only mypy probe found only two initial errors in `operation_contracts`.
3. Expand Ruff rule families in reviewed batches; gate new findings before
   burning down existing ones.
4. Mark the intentional sparse-vector MD5 call as non-security use without
   changing its identity output.
5. Add pinned secret scanning and high-confidence static analysis with narrow,
   expiring suppressions.
6. Add a hosted Windows full-environment release profile or an explicitly
   curated equivalent; the complete locked CPU job is currently Linux-only.

Exit evidence: version-pinned tools, documented baselines, no-regression tests,
changed-code policy tests, and content-free CI artifacts.

### P1: packaging and installed commands

`pyproject.toml` currently configures tools but has no PEP 517 build system,
PEP 621 project metadata, package version, dependency metadata, or console
scripts. User guidance therefore assumes a repository checkout, and selected
entrypoints adjust `sys.path`.

Add stable installed commands for pipeline, evaluation, service, UI,
inspection, and model synchronization. Build from a clean source archive,
inspect wheel/sdist contents, install from locked local inputs, run `pip check`,
and exercise every command and supervised child role from outside the repository
working directory. Explicitly prove that PDFs, ignored output, caches, secrets,
private receipts, and local paths cannot enter the distribution.

Do not combine this with a `src/` move or facade redesign. Those would obscure
whether failures come from packaging or architecture.

### P1/P2: mechanical facade ownership move

Run the already-designed forwarding feasibility spike across all supported
Python minors. If the runtime identity, monkeypatch, import-star, signature,
exception, and help-text contracts survive, move implementation ownership
wholesale to `pipeline_runtime.py` while leaving `rag.py` as the exact facade.
Migrate service-search, UI, and evaluator consumers in separate commits. Only
after those gates pass should functional decomposition begin.

Subsequent reversible slices are:

- one shared error taxonomy;
- a typed CLI command registry and extracted interactive menu;
- a chunk transaction with preparation, classification, assembly, validation,
  and publication stages;
- frozen internal operation configuration objects while preserving external
  signatures;
- explicit ownership for LLM, embedding, reranker, throttle, artifact-hash,
  BM25, and vector-client lifecycles;
- evaluation validation/execution/scoring/publication stages;
- one declarative quality-report schema; and
- declarative OpenAPI generation.

Split oversized tests alongside their corresponding ownership seam, not as an
unrelated rewrite.

### P2: capacity before streaming redesign

Docling output, evaluation chunk lookup, offline BM25 corpora, and UI startup
metadata are materialized in memory. That is a capacity risk, not yet proof that
streaming will improve the supported workload.

Measure small, medium, and large synthetic corpora for peak RSS, startup,
ingestion throughput, index time, query latency, artifact size, cancellation,
and recovery. Set Tier-1 budgets and then stream only paths that exceed them.
The Phase A0 architecture benchmark is not a corpus-capacity benchmark.

### P2: vector-client compatibility debt

The local Windows Qdrant cleanup path reaches into a private `_client` and
forces garbage collection. After the domain-scoped client upgrade, run repeated
fresh-process handle-release tests. Remove the workaround if the supported
client fixes it; otherwise isolate and version-gate it inside the physical
Qdrant adapter with a deadline and documented support range.

### Compatibility migration requiring a decision

`eval.load_queries` deliberately preserves ordinary JSON parsing, including
last-duplicate-key-wins behavior. Add an explicit strict release loader or flag,
warn during one declared compatibility window, and flip or remove legacy
behavior only through a versioned migration. Do not silently alter this contract
during facade work.

## Documentation audit

### Authority and drift

The maintained authority order is sound: owner-approved governance, ADRs,
`ROADMAP.md`, operator README, then historical plans. However, mutable status
was repeated across large files and drifted after the final R1 hosted run. This
successor reconciles the current R1 identity and evidence in `ROADMAP.md`, the
documentation map, benchmark guide, and the two A0/inventory ADRs.

Because R1 is frozen, its pre-merge evidence must first be made durable without
adding a commit to that branch: use a PR-hosted review manifest or attached
signed content-free record containing exact run IDs, artifact names and hashes,
check conclusions, reviewer identity, and intended merge method. Archive that
record after integration in a `docs/evidence/<commit>/` ledger on `main` or an
explicit successor. Current status should then be generated or mechanically
checked from one small manifest; prose should link to it rather than restating
mutable facts.

`INTEGRATION_AUDIT.md` covers only PRs #1-#28 and must remain visibly historical.
R1 needs its own exact-head review manifest, integration audit, and merge
checklist.

### Claude-plan reconciliation

The two Claude-authored plans are valuable design provenance but are not current
execution plans. Their warnings correctly say that branch instructions, line
numbers, test totals, unchecked boxes, and code sketches are historical.

| Claude plan theme | Current disposition |
|---|---|
| Process-supervision extraction | Implemented through a dependency-light module, stable facade, and maintained supervision ADRs |
| Repository hygiene | Mechanical work is in frozen draft #44; privacy/history and the no-license-state disposition remain R1 owner decisions, while license selection is a first-release decision |
| Configurable document profiles | Implemented with immutable registry, strict provenance, and fail-closed validation rather than the mutable sketch |
| Context-aware retrieval | Implemented with bounded adjacency and independent citation identities |
| Table retrieval/evaluation | Implemented in code and tests; lifecycle/generation ADR coverage remains incomplete |
| Vector lifecycle | Substantial implementation exists; a dedicated maintained lifecycle ADR is still missing |
| Leaf typing | Open; belongs in the gradual R7 ratchet |
| README decomposition | Open; belongs after status/evidence extraction and packaging contracts stabilize |
| Pipeline/UI/evaluator composition | Open; belongs after the facade feasibility and mechanical ownership move |

Do not execute commands copied from the historical plans without reconciling
them against the current branch, locks, ADRs, and roadmap.

### Documentation work still needed

- Resolve the conflict between strict private-source non-derivation and current
  private-corpus descriptions/assets, then audit the tree, history, PRs, issues,
  artifacts, and logs under the chosen policy.
- Use `Accepted`, `Implemented`, and `Reviewed` consistently; an agent-authored
  ADR is not human-approved merely because its code exists.
- Split the 2,900-line README into installation, operation, security,
  evaluation, and troubleshooting guides while preserving one tested quickstart.
- Make quickstart commands parser-checked and genuinely cross-platform; avoid
  CUDA/global-pip defaults, POSIX-only copying, shell-variable mismatches, and
  ambiguous glob behavior.
- Keep citation/quotation validation distinct from semantic entailment and live
  prompt-injection resistance in every product claim.
- Preserve the educational-use, not-legal-advice, and required-human-review
  disclaimer now present in the root README; do not imply professional
  reliability from retrieval or citation checks.
- Add maintained ADRs for vector lifecycle, table generation/retrieval,
  immutable source generation, pipeline/facade composition, and release support
  tiers, plus an ADR template and link/status lint.
- Add `SECURITY.md`, `CONTRIBUTING.md`, a PR template, and an owner-selected
  `LICENSE`. Do not invent the license or vulnerability contact.
- Add a provider-contract inventory covering operation, endpoint, credentials,
  data class, response budget, retry, redaction, and receipt behavior.
- Preserve the clarified defensive legacy "shared UI" wording in the
  job-application ADR; the supported launcher has no public-share mode.

## Owner decision register

| Decision | Default while unresolved | Why it matters |
|---|---|---|
| Private-source derivation, history, PR, issue, artifact, and log policy | Content-free, private, no new corpus-derived publication | Determines what evidence may be retained or shared |
| Repository license | No distribution claim | The repository has no owner-selected license |
| PyMuPDF basis | Distribution/hosted use blocked | Current exception needs a recorded legal basis or replacement |
| Cloud-assisted ingestion/enrichment support | Experimental/unsupported for release | Determines whether strict provider and data-scoped consent is mandatory |
| Remote model-code support | Unsupported unless separately qualified | Hash review is not operating-system confinement |
| Release-security granularity | Preserve local-only default | Current policy does not bind consent to provider, operation, data class, or corpus |
| `rag` private/import-star compatibility lifetime | Preserve through mechanical move | Determines later deprecation and facade simplification |
| Tier-1 OS/Python/CPU/CUDA/browser scope | Do not broaden claims | Determines required matrices and support burden |
| Remote or multi-user UI/service | Loopback, trusted single user only | Requires a separate auth/TLS/proxy/tenant/rate/audit threat model |
| Ethics thresholds and judgments | No release qualification claim | Only the authorized corpus owner can approve them |
| Corpus sizes, hardware, latency, memory, and cost | No scale claim | Needed before capacity budgets and streaming changes |
| Version, distribution channel, release approver, and rollback authority | No versioned release | Defines who may publish, what is published, and who can halt or reverse it |

Five time-bounded R2 decision records expire on 2026-08-31. Their work is split
into [#61](https://github.com/toddlar00/rag-pipeline/issues/61) Chroma,
[#62](https://github.com/toddlar00/rag-pipeline/issues/62) Torch,
[#63](https://github.com/toddlar00/rag-pipeline/issues/63) Torchvision,
[#64](https://github.com/toddlar00/rag-pipeline/issues/64) PyMuPDF, and
[#65](https://github.com/toddlar00/rag-pipeline/issues/65) FlagEmbedding. The
decision and technical owners remain explicitly unassigned; the 2026-08-15
checkpoint must not silently renew an exception.

The roles differ by issue: #61 needs a security/release decision owner and a
vector technical owner; #62/#63 need a supply-chain decision owner and an ML
technical owner; #64 needs a legal/repository decision owner and a PDF technical
owner; #65 begins as a metadata/supply-chain technical investigation and needs
a legal/release exception owner only if adequate evidence cannot replace the
exception.

## Recommended execution roadmap

### Wave 0: freeze, review, and decide

| Slice | Gate class | Depends on | Exit condition |
|---|---|---|---|
| R1 exact-head review | R1 integration | Frozen #44 | Named human review and PR-hosted manifest; no unresolved R1-integration blocker, with later release/tier blockers explicitly assigned or deferred |
| R0 privacy/history decision | R1 integration | Repository owner | Approved policy plus tree/GitHub/artifact/history audit |
| Repository no-license-state disposition | R1 integration | Repository owner | Record either that private integration may proceed with distribution deferred, or the selected license path; do not infer permission from a private repository |
| Repository license selection and PyMuPDF basis | First release/distribution | Repository/legal owner | Recorded repository license or proprietary notice plus disposition of #64 |
| FlagEmbedding metadata evidence | First release/deadline | Supply-chain technical owner | Replace #65 with authoritative metadata evidence or escalate an explicit exception decision |
| R2 exception ownership | First release/deadline | Repository and technical owners | Named disposition paths for #61-#65 before the 2026-08-15 checkpoint |
| R3 Ethics calibration | Ethics corpus release | Authorized corpus owner | Approved judgments, thresholds, and content-free receipt |
| Strict LLM contracts | Cloud-assisted enrichment tier | R0C support decision | Exact schemas, adversarial tests, scoped consent, and degradation evidence |
| Safe UI presentation | First release if UI is included | Product/security owner | Sanitized view models, fixed errors, and hostile-content browser proof |
| R1 integration | R1 integration gates only | R1 review, R0 privacy/history decision, and repository no-license-state disposition | History-preserving merge with exact resulting identity and post-merge checks |

Do not add implementation commits to #44. Any defect repair requires a new
candidate commit/tree, replacement A0 evidence, and exact-head review.

### Wave 1: dependency safety and release trust boundaries

1. **R2b-0 dependency domains:** publish the policy-only successor, prove locks
   unchanged and idempotent, run exact-head CI, and then supersede PR #30.
2. **R2 service-test compatibility:** investigate the Starlette/httpx warning in
   an isolated service-domain PR; prove fresh-process `TestClient` behavior and
   make new deprecations fatal.
3. **R2 provider transport:** qualify Google GenAI 2 inside the bounded
   provider-transport domain, including endpoint, credential, retry,
   response-budget, redaction, release-security, and actual changed-lock tests.
   If Dependabot also proposes `requests`, enumerate and exercise the two
   package deltas separately even if they share one PR.
4. **R2 vector/PDF/ML decisions:** resolve #61-#65 before expiration; never
   combine owner decisions with unrelated package upgrades.
5. **R6 vector workaround retest:** immediately after the vector-client domain
   changes, repeat fresh-process Windows handle-release tests and remove or
   isolate the private-client workaround.
6. **R0C strict enrichment:** implement exact LLM contracts and provider,
   feature, operation, data-class, and corpus-scoped egress consent.
7. **UI security slice:** safe view models, fixed public errors, strict child
   envelopes, and real-browser hostile-content tests.

### Wave 2: release contract and pre-refactor gates

1. R3 corpus-owner judgments and thresholds, then R4 private four-mode and
   table/context ablations against those frozen judgments.
2. R12 PEP 621 packaging and installed-command gates.
3. R12 parser-checked Windows/POSIX quickstarts and documentation split.
4. R12 content-free evidence ledger, `SECURITY.md`, contributing guide, PR
   template, and owner-selected license.
5. R5 version, support tier, deprecation, artifact-schema, and migration
   contract.
6. R7 coverage/subprocess baseline and no-regression ratchet, then typed leaves,
   staged Ruff, secret scanning, and static-security ratchets.
7. R10 A1a/A1b/A1c semantic-hotspot baselines required before R8/R9 behavior
   and ownership moves.

### Wave 3: architecture ownership and decomposition

1. R8 facade-forwarding feasibility across supported Python versions.
2. Mechanical `pipeline_runtime.py` ownership move with unchanged `rag` facade.
3. Separate service-search, UI, and evaluator consumer migrations.
4. R9 error taxonomy and typed command registry.
5. R9 chunk transaction and internal operation objects.
6. R9 lifecycle, evaluation, quality schema, and OpenAPI slices.

Every slice must run the architecture checker. Refresh the inventory only for
intentional, explained drift and obtain the required reviewer before accepting
that new baseline. Any Python source or dependency/model-lock change requires a
new clean pre-gate checkpoint and new per-OS Phase A0 baselines before an
authoritative comparison; comparing a changed source to an older baseline is
diagnostic only. A failed comparison is a design signal, not permission to
weaken the benchmark.

### Wave 4: measured scale and broader qualification

1. R10 synthetic capacity matrix and explicit budgets.
2. Stream or page only the paths that exceed approved budgets.
3. R11 additional authorized corpus/profile/table evaluations using only
   owner-approved, content-free evidence.
4. Qualify any broader OS, CUDA, browser, cloud, multi-user, or hosted tier only
   after its threat model and support matrix are approved.

## Pull-request slicing rules

Each successor PR should:

- start from the exact declared ancestor and state it;
- change one compatibility or trust domain;
- include machine-checked policy and adversarial tests with the implementation;
- record exact lock, artifact, inventory, warning, and test identities;
- distinguish local evidence, hosted evidence, human review, and owner approval;
- avoid private-source text, excerpts, paths, or derived judgments unless policy
  explicitly permits them;
- name rollback conditions and exception deadlines; and
- preserve focused PR history even when a later cumulative integration PR is
  used.

### Required split of the current local review tree

The work was reviewed together so its contracts could be reconciled, then split
before publication into two independent successors from the frozen R1 ancestor:

1. **R2b-0 dependency policy:** `.github/dependabot.yml`,
   `.github/workflows/dependency-compatibility.yml`,
   `dependency-compatibility-domains.json`,
   `tools/check_dependency_policy.py`, `tests/test_dependency_policy.py`, the
   deliberate `architecture-inventory.json` refresh, and
   `docs/architecture/decisions/dependency-compatibility-domains.md`, plus only
   the corresponding minimal index link in `docs/README.md` and the root
   README's R2-specific file-inventory/operator-policy hunks.
2. **Documentation truth and roadmap:** this audit, `ROADMAP.md`, the root and
   documentation indexes/guides except for the R2-specific hunks above,
   historical-audit labeling, and ADR status wording only. The root README's
   disclaimer and general gate updates belong here. This PR must not change
   dependency-policy behavior.

For each branch, first commit the complete clean source/documentation state,
then generate and compare fresh Windows and Linux A0 baselines from that exact
pre-gate commit. A later gate-only commit may contain only the baseline and
provenance/status paths allowed by the source-delta policy. Evidence from one
successor cannot validate the other.

## Immediate next actions

1. Finish validation and publish the R2b-0 dependency-domain successor as a
   draft stacked on the frozen R1 branch.
2. Publish the documentation-truth successor independently after its own A0
   cycle; do not mix it into the policy PR.
3. Attach #61-#65 and the policy successor PR to parent R2 issue #50.
4. Close PR #30 as superseded only after the replacement policy is visible and
   exact-head checks pass.
5. Attach the R1 human-review manifest to PR #44 without changing the frozen
   source; archive it in a commit-keyed repository ledger after integration or
   on an explicit successor.
6. Obtain the owner decisions in Wave 0.
7. Start strict LLM-output contracts as the next code-safety milestone; keep UI
   rendering as the following independent pre-release security slice.

## Completion rule

The roadmap is complete only when implementation, exact-head automated gates,
independent technical review, named human review, required owner decisions,
durable content-free evidence, and history-preserving integration all agree on
the same commit/tree. A passing local suite, a draft PR, a historical Claude
checkbox, or an agent-authored ADR is not sufficient by itself.
