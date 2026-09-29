# Phase A0 Benchmark Policy

- **Status:** A0a integrated; the latest completed hosted A0b technical
  checkpoint passed at Task 0.8 evidence head `1533164` and merged through
  `e34103f`; the OCR-program and casebook-excerpt replacement pair at source
  `29ea76b` passed hosted CI on PR #116, the stacked test-time economy pair
  at `365de1c` passed on PR #117, the pytest-xdist pair at `ff5e64f` passed
  on PR #118, the FlagEmbedding pair at `ddbef38` passed on PR #119 (all
  four merged through `ab6e159`), and the fresh-OCR fidelity pair at
  `1ba9415` passed on PR #121 (merged through `602bec1`); the dependency
  series pairs passed hosted CI on PRs #122 to #130 (merged through
  `1cffc58`), the post-merge documentation pair passed on PR #135 (merged
  through `9eb6f94`), the TOC glyph-leader fix pair passed on PR #138
  (merged through `a2c5629`), and the job-heartbeat hardening pair at source
  `da6540d` is pending; exact-head human review and separate R8 owner
  authorization are not recorded
- **Milestone:** R10 prerequisite for the R8 pipeline-ownership move
- **Report schema:** `phase-a0` v3

## Context

The next architecture move needs evidence that facade and composition changes
preserve cheap startup, no-op resume behavior, worker isolation, and offline
operation. Timing alone is too noisy to establish that contract, and a local
smoke report cannot stand in for a reproducible release checkpoint.

## Decision

Phase A0 is intentionally split:

- **A0a** is the contained harness, schema, negative controls, and tests. It may
  produce local smoke evidence but does not approve an architecture move.
- **A0b** is the later authoritative checkpoint: separate reviewed Windows and
  Linux baselines from a clean pre-gate source checkpoint, each generated on
  CPython 3.12 x86-64 with the locked CPU dependency profile, plus hosted CI
  comparison and retained current-report artifacts. The clean pre-gate source
  must already contain the reviewed harness, CI wiring, and LF policy used for
  generation. A following gate-only evidence commit may add only the two
  baselines and their provenance/status documentation, but no Python source,
  workflow, attribute policy, or dependency/model lock. CI and review bind the
  resulting evidence candidate commit/tree; any intervening source or lock
  change invalidates and regenerates both baselines.

[`tools/benchmark_phase_a0.py`](../../../tools/benchmark_phase_a0.py) runs five
or more repetitions of nine fresh-process scenarios:

1. cold `import rag`;
2. real CLI help;
3. real supervised `info` against an empty workspace;
4. service application composition;
5. worker import isolation;
6. actual UI and service worker entrypoints;
7. inherited isolation-guard enforcement;
8. generated completed-artifact no-op resume; and
9. offline export and retrieval.

Authoritative comparison gates deterministic contracts: exact calls and return
categories, output byte identities, first- and third-party import roots,
complete scenario-set identity, and source and dependency/model-lock identities
captured before and after all repetitions. Wall time, distribution summaries,
and process-only peak RSS (which excludes descendants) remain diagnostic until
repeatability supports separately reviewed budgets.

Every authoritative run must start and finish on the same clean source and lock
state. Reports and baselines use strict duplicate-key- and non-standard-number-
rejecting JSON. Output is redirected to bounded files and checked before it is
read. Fixed locale and hash settings reduce ambient variation.

Each probe runs under process-tree supervision with a deadline and confirmed
cleanup. A generated guard inherited by nested Python children scrubs ambient
credentials/profile paths and denies Python-level network, DNS, home, and tilde
access. Validator negative controls prove that broken completion validation,
wrong call wiring, failed containment, or altered isolation cannot pass. The
no-op resume scenario delegates to real completion validators and exercises
real interprocess vector-lock contention both while held and after release.

Linux's standard-library `sysconfig` can otherwise resolve `~/.local` while
third-party service dependencies import. The child environment therefore
replaces any ambient `PYTHONUSERBASE` with a run-local temporary directory and
sets `PYTHONNOUSERSITE=1`. It does not set or repurpose `HOME` or `CODEX_HOME`;
the inherited guard continues to reject home and tilde resolution. A contained
regression probe asserts both that `sysconfig` uses the redirected user base and
that `Path.home()` remains denied.

## A0b publication gate

The latest completed hosted replacement gate used clean pre-gate source
`4551c50970a07ac120d192802bcc692e39e3ece6` — the post-merge inventory
refresh over the transport and logging hardening merge `d68af4f` on the
glyph-truth head `eb29f86`, whose own pair passed both hosted Phase A0
cells on the post-merge `main` workflow at that evidence head — and separate
Windows and Linux CPython 3.12.13 x86-64 reports. Both were generated under
the exact `requirements-full.lock`, `requirements-test.lock`, and retained
`requirements-lock-tools.lock` union after strict hash-locked synchronization
and dependency-consistency checks: 189 marker-resolved distributions on
Windows and 187 on Linux. They bind one clean source, the same eight LF and
`HEAD`-identical dependency/model inputs, and the complete 9×5 scenario
contract. Each passes an independent complete same-platform comparison. The
repaired CI matrix, LF policy, source gates, Python 3.10-3.14 compatibility
normalization, and the two-lane CI split precede `4551c50` and were exercised
by generation and comparison. Reports-and-documentation-only head
`443dce4c312737eebb57a5bba6fc0abb9ace1a26` freezes the two reports and their
provenance/status documentation.

Both Tier-1 hosted jobs passed against their matching baselines at that exact
evidence head and retained their reports. This closed that A0b _technical_
publication checkpoint. A local smoke report, subset,
noncanonical interpreter, baseline copied between operating systems, or
local-only comparison remains non-authoritative. No submitted exact-head human
review or separate owner authorization for R8c-6 was found; passing the hosted
A0b jobs does not supply that authorization or complete the review binding.

The Task 0.2 replacement pair used clean source
`b07e3270881376cf60586c363e6285722562c7f5`; its gate-only child `aa7a23b`
completed the hosted checkpoint and merged through `d142065`
([PR #93](https://github.com/toddlar00/rag-pipeline/pull/93)). The
pre-activation hardening pair used clean source
`376750d6b992b04064d61e142d516c2ebda3b696`; its gate-only child `f8fc95b`
passed all hosted force-full checks with retained Phase A0 artifacts,
received the external exact-SHA record on
[PR #94](https://github.com/toddlar00/rag-pipeline/pull/94), and merged
through `f3bcb91`, completing that hosted checkpoint before the activation
merge `1e79540`. The vector-stores domain pair at source `0703dde`
repeated the cycle through gate-only child `fa71ff3` and merge `b3c7cf7`
([PR #97](https://github.com/toddlar00/rag-pipeline/pull/97)), and the
ML/runtime domain pair at source `6f65acb` repeated it through gate-only
child `78a99c2` and merge `d27d85c`
([PR #98](https://github.com/toddlar00/rag-pipeline/pull/98)), and the
Service/UI domain pair at source `248c57a` repeated it through gate-only
child `4945878` and merge `f1dae3b`
([PR #99](https://github.com/toddlar00/rag-pipeline/pull/99)). The test-audit tooling
domain pair at source `ca3886c` repeated it through gate-only child
`a129f37` and merge `0ec2639`
([PR #100](https://github.com/toddlar00/rag-pipeline/pull/100)), and the
PDF/Docling domain pair at source `ed1f370` through gate-only child
`33c61ff` and merge `39f6c9e`
([PR #101](https://github.com/toddlar00/rag-pipeline/pull/101)). The
Task 0.6 Node supply-chain pair at source `5f45757` repeated it through
gate-only child `344e873` and merge `893c4a0`
([PR #102](https://github.com/toddlar00/rag-pipeline/pull/102)). The
Task 0.7 secret-scan pair at source `e055bd0` repeated it through
gate-only child `32153e9` and merge `376277c`
([PR #106](https://github.com/toddlar00/rag-pipeline/pull/106)). The
Task 0.8 static-security pair at source `8891e1b` repeated it through
gate-only child `1533164` and merge `e34103f`
([PR #107](https://github.com/toddlar00/rag-pipeline/pull/107)). The
current replacement pair uses the clean job-heartbeat hardening source
`da6540d143dadb8c4be0a007cebdb3b4f0d14749` (tree
`ae7af01aa824b6287423e3848ead7675895163af`), a job-manager hardening
follow-up to a series of one-domain dependency updates, on `main` after the
merges of the TOC glyph-leader fix #138 (`a2c5629`), the documentation
follow-up #135 (`9eb6f94`), the dependency series #122 to #130 (`1cffc58`),
the fresh-OCR fidelity source `1ba9415`, the FlagEmbedding source `ddbef38`,
the pytest-xdist source `ff5e64f`, the test-time economy source `365de1c`
and the OCR-program and casebook-excerpt source `29ea76b`; together they
changed Python source, CI configuration, `.gitattributes`, two test-tooling
locks (pytest-xdist and execnet in `requirements-test.lock` and
`requirements-smoke.lock`) and the FlagEmbedding record of
`requirements-core.lock` and `requirements-full.lock`; the stacked
dependency updates also change the vector-stores, Service/UI, test-audit
tooling, ML/runtime, PDF/Docling (docling-core), promoted ML/runtime,
promoted h2 and promoted cryptography records of their mapped locks, but no
model lock. It supersedes the
earlier `1ae8502` pair, whose child `766feaf` passed both hosted Phase A0
cells but whose dependency-light unit lanes failed at collection. Source
`a15232d` then passed both hosted Phase A0 cells on [PR
#116](https://github.com/toddlar00/rag-pipeline/pull/116) as well, but its
Python 3.10/3.11 unit lanes each failed one import-guard test and its
Windows unit lane exceeded the workflow's 20-minute timeout; the test-only
source `8aa08b2` superseded it before promotion. Source `8aa08b2` fixed
those lanes, and its hosted run passed every CI lane except the Windows unit
lane, which again reached the 20-minute timeout with no test failure. On the
owner's decision, source `29ea76b` raises that limit to 60 minutes (the live
workflow, its reviewed fixture and the two pinned workflow hashes) and
supersedes the `8aa08b2` pair before promotion. The `29ea76b` pair
(gate-only child `ed0a0d3`) then passed the hosted CI promotion gate on [PR
#116](https://github.com/toddlar00/rag-pipeline/pull/116); the separate
networked vulnerability/SBOM jobs failed at that head. The stacked test-time
economy source `365de1c` changes Python source and CI configuration (the
Windows unit lane runs as three shards) but no dependency or model lock, and
supersedes that pair for its own pull request. The `365de1c` pair (gate-only
child `b33a433`) passed the hosted CI promotion gate on [PR
#117](https://github.com/toddlar00/rag-pipeline/pull/117). The stacked
pytest-xdist source `c31f4c9` adds pytest-xdist 3.8.0 and execnet 2.1.2 to
`requirements-test.lock` and `requirements-smoke.lock` (test/audit tooling
domain) and runs the dependency-light unit lanes with `-n auto`; it
supersedes the `365de1c` pair for its own pull request. The `c31f4c9` pair
(gate-only child `52a400c`) passed the hosted CI promotion gate on [PR
#118](https://github.com/toddlar00/rag-pipeline/pull/118). Its lock change
also triggered the dependency workflow's core-lock job, which failed because
seven OCR review test modules imported gradio unconditionally. The test-only
source `ff5e64f` guards those imports and supersedes the `c31f4c9` pair. The
`ff5e64f` pair (gate-only child `419e901`) passed the hosted CI promotion
gate on [PR #118](https://github.com/toddlar00/rag-pipeline/pull/118). The
stacked FlagEmbedding source `515ed91` (ML/runtime domain) moves
FlagEmbedding from 1.4.0 to 1.4.2 in `requirements-core.lock` and
`requirements-full.lock`, so local reranking works with Transformers 5, and
supersedes the `ff5e64f` pair for its own pull request. On that pair's
hosted run (gate-only child `c804d7b`, [PR
#119](https://github.com/toddlar00/rag-pipeline/pull/119)) one test in the
Linux 3.12 unit lane failed on a one-second access-time tick in a
whole-`lstat` comparison. The test-only source `ddbef38` compares identity
without the access time and supersedes the `515ed91` pair. The `ddbef38`
pair (gate-only child `43d7a09`) passed the hosted CI promotion gate on [PR
#119](https://github.com/toddlar00/rag-pipeline/pull/119), and #116 to #119
merged into `main` in order (`0a04207`, `e15448b`, `3b430bf`, `ab6e159`).
The fresh-OCR token fidelity source `e8b3205`, on that `main`, keeps source
tokens through chunk merges and normalization and hardens the receipt digest
cache; it changes no dependency or model lock and supersedes the `ddbef38`
pair. An independent review of the `e8b3205` pair's pull request
([#121](https://github.com/toddlar00/rag-pipeline/pull/121)) found that
duplicate-line allowance keys still used the repaired fused spelling; the
review-fix source `1ba9415` corrects that, closes the per-call scope and
retries mismatched receipt reads, and supersedes the `e8b3205` pair.
The `1ba9415` pair (gate-only child `6be805d`) passed the hosted CI
promotion gate on [PR
#121](https://github.com/toddlar00/rag-pipeline/pull/121), which merged into
`main` as `602bec1`. On that `main`, the stacked vector-stores dependency
source `db030f1` (superseding Dependabot #114: qdrant-client 1.19.1, and
onnxruntime 1.30.0 for CPython 3.11+) opens a series of one-domain
dependency pull requests and supersedes the `1ba9415` pair for its own pull
request.
The `db030f1` pair (gate-only child `33397de`) passed the hosted CI
promotion gate on [PR
#122](https://github.com/toddlar00/rag-pipeline/pull/122); its Linux
environment was synchronized with uv 0.11.31 rather than the pinned 0.12.5,
although the hash-locked lock union alone determines the installed set. The
stacked Service/UI dependency source `eccc146` (superseding Dependabot #115:
gradio 6.28.0 and uvicorn 0.54.0) adapts two test harness assumptions to
Gradio 6.28 (an event id for streamed `process_api` runs and decoded file
URLs), synchronizes both platforms with the pinned uv 0.12.5, and supersedes
the `db030f1` pair for its own pull request.
The stacked test-audit tooling dependency source `c11099b` (superseding
Dependabot #111: uv 0.12.20, the pinned lock resolver, and ruff 0.16.9)
moves the OCR installer's uv check, its documentation and the
static-security ruff pin with the locks, and supersedes the `eccc146` pair
for its own pull request.
The `c11099b` pair (gate-only child `f113e00`) passed the hosted CI
promotion gate on [PR
#124](https://github.com/toddlar00/rag-pipeline/pull/124). A PDF/Docling
checkpoint (Dependabot #108: docling 2.130.0) passed its suites and pair but
was withdrawn before promotion because re-converting audited private
readings with it worsened their reading order; docling stays at 2.121.0. The
stacked ML/runtime dependency source `e579090` (superseding Dependabot #120:
torch 2.14.0, torchvision 0.29.0, sentence-transformers 6.1.0 and tqdm
4.70.1) keeps numpy at 2.5.2, which the OCR disposition observer's verified
recipe requires, and supersedes the `c11099b` pair for its own pull request.
The `e579090` pair (gate-only child `2bb7a54`) passed the hosted CI
promotion gate on [PR
#125](https://github.com/toddlar00/rag-pipeline/pull/125). The stacked
docling-core dependency source `13f3b5d` moves docling-core alone from
2.92.0 to 2.99.0 (docling stays at 2.121.0), lifting the macOS transformers
cap that blocks the transformers advisory fix, and supersedes the `e579090`
pair for its own pull request.
The `13f3b5d` pair (gate-only child `d0800ea`) passed the hosted CI
promotion gate on [PR
#126](https://github.com/toddlar00/rag-pipeline/pull/126). The stacked
ML/runtime advisory-promotion source `8c12135` promotes transformers,
datasets and aiohttp to governed direct inputs to take their advisory fixes
(transformers 5.16.1, capped below 5.17 because 5.17 removed a helper the
pinned Nomic embedding code calls; datasets 5.0.1; aiohttp 3.14.3), adds a
guard test and refreshes the inventory, and supersedes the `13f3b5d` pair
for its own pull request.
The stacked vector-stores h2-promotion source `21d66eb` promotes h2 to a
governed direct input to take its advisory fix (4.4.1) and supersedes the
`8c12135` pair for its own pull request.
The stacked provider-transport cryptography-promotion source `3e23b50`
promotes cryptography to a governed direct input to take its advisory fix
(50.0.1) and supersedes the `21d66eb` pair for its own pull request.
The stacked supply-chain renewal source `01adb4f` (owner-authorized on
2026-09-28) renews the expired license and vulnerability exceptions through
2026-12-27, records time-boxed acceptances for the unpatched accelerate and
chromadb advisories, changes no dependency or lock, and supersedes the
`3e23b50` pair for its own pull request.
The `01adb4f` pair (gate-only child `125cb6a`) passed every hosted check on
[PR #130](https://github.com/toddlar00/rag-pipeline/pull/130), including
both networked supply-chain jobs, and #122 to #130 merged into `main` in
order (`e8b7e41`, `ac997dc`, `94c2c47`, `049a57e`, `0a3b7f1`, `ca9c6df`,
`a18e7fc`, `18e9221`, `1cffc58`). The post-merge documentation source
`da13c4c` records the integrated series and its follow-ups in the README and
ROADMAP, changes no dependency or lock, and supersedes the `01adb4f` pair.
The `da13c4c` pair (gate-only child `fab0d70`) passed the hosted CI
promotion gate on [PR
#135](https://github.com/toddlar00/rag-pipeline/pull/135), which merged into
`main` as `9eb6f94`. The failing-only TOC glyph-leader fix source `e7b60cd`
splits table-of-contents chapter rows whose dot leaders were extracted as
control characters or U+FFFD, only when the ordinary parse leaves a fused
chapter title; it changes no dependency or lock and supersedes the `da13c4c`
pair.
The `e7b60cd` pair (gate-only child `2643774`) passed the hosted CI
promotion gate on [PR
#138](https://github.com/toddlar00/rag-pipeline/pull/138), which merged into
`main` as `a2c5629`. The job-heartbeat hardening source `da6540d` rebases
the reviewed heartbeat change of [PR
#137](https://github.com/toddlar00/rag-pipeline/pull/137) (an independent
review approved it with nits, all addressed) onto that merge; it tolerates
transient Windows replace failures of the advisory heartbeat for a bounded
time, changes no dependency or lock and supersedes the `e7b60cd` pair.
Its Windows and
Linux CPython 3.12.13 reports were
generated after the same strict hash-locked synchronization and
dependency-consistency checks: 193 marker-resolved distributions on
Windows and 191 on Linux. They bind that one clean source, the same eight
LF and `HEAD`-identical dependency/model inputs, and the complete 9×5
scenario contract. Each passes an independent complete local same-platform
comparison. The direct gate-only evidence child contains only the reports
and permitted provenance/status documentation; its hosted checks, retained
current-report artifacts, external exact-SHA promotion record, and own
commit identity are still pending.

The temporary force-full seed matrix explicitly checks out the exact PR head
rather than the synthetic merge ref so its report can bind immutable evidence
child `E`. That bootstrap has no promotion aggregate, and its raw-head Phase A0
result is not proof that the prospective merge candidate ran Phase A0. The
reviewed active workflow instead checks out the synthetic candidate for Phase
A0, as it does for every execution job. In both forms, the report and
attestation bind whichever exact commit/tree the job checked out. Before
comparison, the harness validates and publishes the candidate report. Failures
identify only a stable stage and content-free diagnostic code; raw paths,
corpus material, exception text, and credentials are not logged. A failed
comparison report is retained for 7 days for diagnosis.

After a successful comparison, CI strictly revalidates the generated current
report and requires its clean source commit to equal the job's exact `HEAD` and
its platform to equal the matrix cell. It then emits a content-free schema-v1
attestation binding the candidate and pre-gate commit/tree pairs, raw and
embedded report hashes, platform, and sorted gate-only paths. CI copies the
report and places the attestation in an exact two-file evidence directory,
verifies the file set and copied-report hash, and retains that directory for 30
days. A missing, extra, substituted, or non-regular file fails the cell.

The source-delta check is intentionally ancestor-bound. The final candidate
must reach `main` through a history-preserving merge that retains the pre-gate
source commit. Squashing or rewriting that history invalidates the evidence and
requires new baselines from the replacement source checkpoint.

## Boundaries

Containment and the inherited guard are deterministic test controls, not a
sandbox. They do not prevent native extensions, hostile trusted code, or every
OS-level egress path; OS firewall or VM isolation is required for that claim.
The generated fixtures are content-free architecture probes, not production
capacity evidence and not a substitute for later Phase A1 or authorized Phase
B workloads.

## Evidence

- Harness: [`tools/benchmark_phase_a0.py`](../../../tools/benchmark_phase_a0.py)
- Schema, containment, negative-control, and contract tests:
  [`tests/test_phase_a0_benchmark.py`](../../../tests/test_phase_a0_benchmark.py)
- Baseline provenance and checking procedure:
  [`benchmarks/README.md`](../../../benchmarks/README.md)
- Windows and Linux canonical candidates:
  [`phase-a0-windows-cpython312.json`](../../../benchmarks/phase-a0-windows-cpython312.json)
  and
  [`phase-a0-linux-cpython312.json`](../../../benchmarks/phase-a0-linux-cpython312.json)

A0a was introduced at `64843d1`; the cross-platform user-base isolation closure
is `77a0f70`. All 51 A0a tests pass under the locked Windows and WSL Linux test
environments. The complete 104-test Windows architecture/A0 set, the 51-test
WSL A0 set, and canonical architecture checks on Windows CPython 3.12/3.14 and
WSL CPython 3.12 pass at that checkpoint. These results establish A0a only;
they do not replace the separate A0b baselines and hosted exact-head checks.

The first frozen hosted attempt at `ba9c66d` was diagnostic, not passing A0b
evidence. Both A0 cells found checkout-EOL lock-byte drift. The same hosted run
also exposed POSIX acceptance of the adversarial Windows drive-relative source
inventory path `C:escape.py` and CRLF drift in the Windows architecture
inventory. Replacement source `7594f8b` closes those defects through
platform-neutral lexical source validation, explicit LF attributes, a
lock-versus-`HEAD` invariant, content-free staged diagnostics, pre-comparison
candidate publication, 7-day failed-comparison retention, and exact-PR-head
checkout. Cross-version qualification then exposed CPython 3.10's missing
`ast.TryStar`, different `typing.Any` and default-`None` behavior, and nested
PEP 585 forward-reference handling; CPython 3.13/3.14 lazy `sysconfig`
initialization; and CPython 3.13's inherited `Path.home` descriptor and private
implementation identity for public `pathlib.Path`. The earlier normalization
checkpoint `fdb08d2` closed those cases, including inherited cross-module type
hints, without permitting operator-home or tilde access.

At `fdb08d2`, all 56 A0 tests passed in each locked full suite. Fresh complete
suites passed 2,233 tests with 7 skips on Windows and 2,236 tests with 4 skips
on native Linux. The canonical architecture inventory reported 1,981 functions
and its runtime contract reproduced across Windows and Linux CPython
3.10-3.14. Gate-only commit `ed2995e` froze those local candidates. R2
preparation later produced clean source `537f72b` and gate-only refresh
`b813aa7`.

Release-defect source `c1bc042` changed Python and invalidated those reports;
publication-readiness source `e904fa6` and the strict output-contract line
each superseded that paired refresh with their own interim candidates; the R2
convergence checkpoint `8f95872` superseded both, passed its hosted Phase A0
cells at the exact #76 candidate head, and merged on 2026-08-01. The
one-domain PDF/Docling dependency checkpoint `c73a155` repeated that cycle
via #83, the read-only PDF triage scan merge `4975793` passed its hosted
cells at evidence head `782c986`, the dependency domain-gate decomposition
merge `71b4a23` passed its cells at evidence head `6efe0bc`, the ingestion
quick-wins merge `3b2188c` passed its cells at evidence head `05eb6df`,
the glyph-truth and evaluation-significance merge `db8132b` passed its cells
at evidence head `eb29f86`, and the transport and logging hardening merge
`d68af4f` (checkpoint `4551c50`) changed Python source again and superseded its
prior pair. The committed architecture inventory at that checkpoint is
validated by `python tools/check_architecture_inventory.py`; volatile counts
are not repeated in the live roadmap. After strict synchronization and
dependency checks for 189 Windows and 187 Linux distributions, the Windows
replacement report is 50,390 bytes
under CPython 3.12.13 (file SHA-256
`f958ac2d47e46a7a3660a408b5b011ac809586810cc16623e67db128bd7a7816`;
embedded report SHA-256
`a41545b403ed2d5f9dbea529ce86096550dd1114232c955b910b4f76336e496b`).
The Linux report is 49,801 bytes under CPython 3.12.13 (file SHA-256
`b9f740b44be219f8d2eb5bb903ec0c3a655d265c4fe7fcea6fc5da8deff3b0bb`;
embedded report SHA-256
`93ef091dd2828b24fcd2d875320a79ddd5435cb71e99ac97ca20b82a8ffb46c2`).
Each report passed an independent complete same-platform 9×5 comparison.
Reports-and-documentation-only evidence head `443dce4` froze that historical
pair, and both matching hosted jobs passed with retained evidence. The exact
identities, workflow run, and limitation that no submitted human review was
found are recorded in the
[`443dce4` evidence entry](../../evidence/2026-08-18-main-443dce4.md).

Task 0.2 source `b07e327` superseded that historical pair; its evidence
child `aa7a23b` passed both hosted Phase A0 cells with retained artifacts
and merged through `d142065` with the external exact-SHA promotion record on
[PR #93](https://github.com/toddlar00/rag-pipeline/pull/93). The
pre-activation hardening source `376750d` repeated the full cycle: its
child `f8fc95b` passed both hosted cells with retained artifacts and merged
through `f3bcb91`
([PR #94](https://github.com/toddlar00/rag-pipeline/pull/94)), and the
activation checkpoint merged through `1e79540`. The one-domain
vector-stores dependency source `0703dde` repeated the cycle: its child
`fa71ff3` passed the hosted full lane with retained artifacts and merged
through `b3c7cf7`
([PR #97](https://github.com/toddlar00/rag-pipeline/pull/97)). The
one-domain ML/runtime dependency source `6f65acb` repeated the cycle: its
child `78a99c2` passed the hosted full lane with retained artifacts and
merged through `d27d85c`
([PR #98](https://github.com/toddlar00/rag-pipeline/pull/98)). The
one-domain Service/UI dependency source `248c57a` repeated the cycle: its
child `4945878` passed the hosted full lane with retained artifacts and
merged through `f1dae3b`
([PR #99](https://github.com/toddlar00/rag-pipeline/pull/99)). The
one-domain test-audit tooling dependency source `ca3886c` repeated the
cycle: its child `a129f37` passed the hosted full lane with retained
artifacts and merged through `0ec2639`
([PR #100](https://github.com/toddlar00/rag-pipeline/pull/100)). The
one-domain PDF/Docling dependency source `ed1f370` repeated the cycle: its
child `33c61ff` passed the hosted full lane with retained artifacts and
merged through `39f6c9e`
([PR #101](https://github.com/toddlar00/rag-pipeline/pull/101)). The
Task 0.6 Node supply-chain ownership source `5f45757` repeated the
cycle: its child `344e873` passed the hosted full lane with retained
artifacts and merged through `893c4a0`
([PR #102](https://github.com/toddlar00/rag-pipeline/pull/102)). The
Task 0.7 secret-scan source `e055bd0` repeated the cycle: its child
`32153e9` passed the hosted full lane with retained artifacts and merged
through `376277c`
([PR #106](https://github.com/toddlar00/rag-pipeline/pull/106)). The
Task 0.8 static-security source `8891e1b` repeated the cycle: its child
`1533164` passed the hosted CI lane, including both Phase A0 cells, and
merged through `e34103f` ([PR #107](https://github.com/toddlar00/rag-pipeline/pull/107); the separate
networked supply-chain vulnerability/SBOM jobs failed at that head). The
OCR-program and casebook-excerpt source `1ae8502` superseded that pair,
and its own pair was superseded before promotion by source `a15232d`
(dependency-light test guards and the casebook fixes), whose pair was in
turn superseded by the test-only source `8aa08b2`, then by the
Windows-timeout source `29ea76b`, the stacked test-time economy
source `365de1c`, the stacked pytest-xdist source `c31f4c9`, its
test-only successor `ff5e64f`, the stacked FlagEmbedding source
`515ed91`, its test-only successor `ddbef38` and then the fresh-OCR
fidelity source `e8b3205` on `main` and then its review-fix successor
`1ba9415`,
then the stacked vector-stores dependency source `db030f1`,
then the stacked Service/UI dependency source `eccc146`,
then the stacked test-audit tooling dependency source `c11099b`,
then the stacked ML/runtime dependency source `e579090`,
then the stacked docling-core dependency source `13f3b5d`,
then the stacked ML/runtime advisory-promotion source `8c12135`,
then the stacked vector-stores h2-promotion source `21d66eb`,
then the stacked provider-transport cryptography-promotion source `3e23b50`,
then the stacked supply-chain renewal source `01adb4f`,
then the stacked post-merge documentation source `da13c4c`,
then the stacked TOC glyph-leader fix source `e7b60cd`,
then the stacked job-heartbeat hardening source `da6540d`.
Its Windows report
is 51,083 bytes (file SHA-256
`30e7cd0d4bba0773765349c93feb98f7278704f5c35f90eeb4f9f630479005b7`;
embedded report SHA-256
`076ae7c7c434fad43c8ab1e69b354d7780ed13582e5c3498571b765f2ea7efb4`).
Its Linux report is 50,480 bytes (file SHA-256
`614b9e2859868fefab020fa837f9f297bb490b63a167db8004ad4582b7bb39b0`;
embedded report SHA-256
`0d7ad9d15b3092a626d9e696dcab5cc234c23a6f7f7d18014e986d665d41e3bf`).
Both use CPython 3.12.13 and pass their complete local same-platform 9×5
comparisons. Hosted promotion remains pending.
