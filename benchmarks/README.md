# Phase A0 Baselines

These files are the canonical per-platform Phase A0 baseline candidates for
the R10/A0b architecture gate. They contain no private corpus content or
credentials. Each report covers the complete nine-scenario set with five
fresh-process repetitions.

## Provenance

Both current reports were generated from the clean static-security S324
retirement source checkpoint `3af52f8cbe41b9c3872e6af6c5c7230659243018`
(tree `02da93a1d8de080254ceef63ed7ae02ed0053bfc`), a static-security gate
follow-up after a series of one-domain dependency updates on `main` after
the history-preserving merges of #121 (`602bec1`), of that series, #122 to
#130 (`1cffc58`), of its documentation follow-up #135 (`9eb6f94`), of the
TOC glyph-leader fix #138 (`a2c5629`), of the job-heartbeat hardening #137
(`634c38b`), of the Nomic contract test #136 (`2f07510`), of the oauthlib
promotion #139 (`123bedb`), of the tokenless-glyph rotation fix #140
(`6baf14b`), of the table header-row oracle fix #145 (`ce73b61`), of the
soft-hyphen item-seam fix #146 (`8cc89b0`), of the post-series
documentation #147 (`9fff7bb`) and of the heartbeat test fix #148
(`3aede7d`). That source contains everything through the
Task 0.8 static-security merge `e34103f` plus the local OCR accuracy, retry
and guided-review program, opt-in AI evidence search, the passive cleanup
audits, the LLM transport and worker-launch repairs, the casebook
excerpt/supplement pipeline (including the opt-in OCR angle-classifier and
interleaved-region-merge flags, the footnote placement replay, the
split-item duplicate retraction and quality schema 13), four reviewed
architecture-inventory refreshes, the POSIX symlinked-interpreter identity
fix, an LF attribute for top-level test fixtures, dependency-light test
guards, a Python 3.10/3.11-tolerant import-guard test, a 60-minute Windows
unit-lane timeout and the test-time economy changes (a session-shared
architecture inventory, an indexed inventory builder, cached lock-record
parsing and executed-source digests, and a Windows unit lane split into
three shards), pytest-xdist for the unit lanes, gradio import guards in
seven OCR review test modules, FlagEmbedding 1.4.2 for Transformers 5
reranking and the fresh-OCR token fidelity fixes, followed by stacked
one-domain dependency updates for vector stores, Service/UI, test-audit
tooling, ML/runtime, docling-core, ML advisory promotions, h2, cryptography,
supply-chain policy renewals and oauthlib. The lock changes add pytest-xdist
3.8.0 and execnet 2.1.2 to `requirements-test.lock` and
`requirements-smoke.lock` and move FlagEmbedding from 1.4.0 to 1.4.2 in
`requirements-core.lock` and `requirements-full.lock`; the stacked
vector-stores update moves qdrant-client from 1.19.0 to 1.19.1 and
onnxruntime from 1.29.0 to 1.30.0 (CPython 3.11+) in its mapped locks; the
stacked Service/UI update moves gradio from 6.25.0 to 6.28.0 (with
gradio-client 2.7.1) and uvicorn from 0.52.4 to 0.54.0; the stacked
test-audit tooling update moves uv from 0.12.5 to 0.12.20 and ruff from
0.16.3 to 0.16.9; the stacked ML/runtime update moves torch to 2.14.0,
torchvision to 0.29.0, sentence-transformers to 6.1.0 and tqdm to 4.70.1;
the stacked docling-core update moves docling-core from 2.92.0 to 2.99.0;
the stacked ML/runtime advisory promotion moves transformers to 5.16.1
(tokenizers 0.23.2), datasets to 5.0.1 and aiohttp to 3.14.3; the stacked h2
promotion moves h2 from 4.3.0 to 4.4.1; the stacked cryptography promotion
moves cryptography from 49.0.0 to 50.0.1; the stacked supply-chain renewal
changes no lock; the post-merge documentation changes no lock; the TOC
glyph-leader fix changes no lock; the job-heartbeat hardening changes no
lock; the Nomic contract test changes no lock; the stacked oauthlib
promotion moves oauthlib from 3.3.1 to 4.0.0; the tokenless-glyph rotation
fix changes no lock; the table header-row oracle fix changes no lock; the
soft-hyphen item-seam fix changes no lock; the post-series documentation
changes no lock; the heartbeat test fix changes no lock; the static-security
S324 retirement changes no lock; no model lock changed. The executing
environments were synchronized with repository-pinned uv 0.12.20 against the
exact CPU application/test lock union plus its retained bootstrapper:

- `requirements-full.lock`
- `requirements-test.lock`
- `requirements-lock-tools.lock`

The Windows environment used a temporary direct, uv-managed CPython
interpreter rather than a virtual-environment redirector so supervised-child
parent identities remained exact. The Linux environment used a direct POSIX
virtual-environment interpreter. `uv pip sync --strict --torch-backend cpu
--require-hashes` and `uv pip check` passed for 193 Windows and 191 Linux
marker-resolved distributions before generation.

| Platform | Runtime | Report | Bytes | File SHA-256 | Embedded report SHA-256 |
|---|---:|---|---:|---|---|
| Windows x86-64 | CPython 3.12.13 | `phase-a0-windows-cpython312.json` | 51,079 | `2152ee96bcc52c716aabde1714c93d2e133f9e33bf2a10444b7cf4d507d2d661` | `ed4f99984f1cb7f2818933556cce705201a9e9f94326321308f2f7a063c989ad` |
| Linux x86-64 | CPython 3.12.13 | `phase-a0-linux-cpython312.json` | 50,486 | `d6ad0f18151ddfee025862804d8ad889846abbee7c4cfd8f623f1c299a138ff3` | `6726e05ae916102c5fa7a55be5f64412c2115088c972726b45a8e76b5bb861bd` |

The reports attest the same source commit, clean-worktree state, tracked-diff
digest, eight LF and `HEAD`-identical dependency/model-lock inputs, authoritative
nine-scenario set, and five repetitions. Platform, patch-level runtime,
hardware, wall time, and process-only RSS diagnostics are intentionally
platform-specific. Repository Git attributes preserve LF for the reports,
locks, and architecture inventory so their reviewed bytes survive Windows and
Linux checkouts unchanged. The harness independently rejects any lock input
whose worktree bytes differ from the exact blob at `HEAD`.

## Hosted diagnosis and replacement state

The first published frozen candidate, `ba9c66d`, did not satisfy A0b. Its hosted
A0 cells exposed checkout line-ending drift in lock bytes; a Linux source-gate
cell also showed that host-native `pathlib` parsing accepted the Windows
drive-relative inventory entry `C:escape.py`, while the Windows full unit cell
found CRLF drift in the architecture inventory. Those are gate defects, not
benchmark regressions, and the old baselines are invalid for the repaired
source.

Checkpoint `7594f8b` repairs those defects with platform-neutral lexical source
path validation, explicit LF attributes, and the lock-versus-`HEAD` invariant.
It also gives failures stable content-free stages and diagnostic codes, writes
a validated candidate report before baseline comparison, and makes pull-request
A0 jobs check out the exact PR head. Subsequent Python 3.10-3.14 qualification
found additional interpreter-only drift in `ast.TryStar`, lazy `sysconfig`
initialization, inherited `Path.home`, the public `pathlib.Path` identity,
`typing.Any`, implicit optional annotations, and nested forward references.
Pre-gate source `fdb08d2` closed those cases without weakening home/tilde
denial. At that historical checkpoint its architecture baseline reproduced on
Windows and Linux across Python 3.10-3.14; fresh locked full suites passed
2,233 tests with 7 skips on Windows and 2,236 tests with 4 skips on native
Linux, and the inventory recorded 1,981 functions and 377 compact runtime
callables. Gate-only commit `ed2995e` froze those candidates. R2 preparation
then produced clean source `537f72b` and gate-only refresh `b813aa7`.

Release-defect checkpoint `c1bc042` had itself invalidated the earlier report
pair and recorded 1,995 functions and 378 compact runtime callables. The
publication-readiness implementation in `e904fa6` and the strict LLM/TOC
output-contract line each superseded that checkpoint with their own interim
candidate pairs. The R2 convergence checkpoint `8f95872` merges both lines
over the banked R1 head and the two-lane CI split and changes Python source
again, so it invalidates every earlier report under this policy. Its
canonical inventory records 2,597 functions with 943 static and 951 runtime
`rag` bindings, and its full local suite passes 3,376 tests with 7
platform/optional skips. That convergence candidate passed all 27 hosted
checks — including both hosted Phase A0 cells with retained evidence
bundles — at its exact head and merged to `main` on 2026-08-01 through
history-preserving [#76](https://github.com/toddlar00/rag-pipeline/pull/76).
The one-domain PDF/Docling dependency checkpoint `c73a155` then changed the
two mapped locks, invalidating the prior pair; its own pair passed all 27
hosted checks at the exact #83 head and merged on 2026-08-01. The read-only
PDF triage scan merge `4975793` repeated that cycle, passing its hosted
cells on the post-merge `main` workflow at evidence head `782c986`. The
dependency domain-gate decomposition merge `71b4a23` repeated the cycle,
passing its hosted cells at evidence head `6efe0bc`, and the ingestion
quick-wins merge `3b2188c` passed its hosted cells on the post-merge
`main` workflow at evidence head `05eb6df`, and the glyph-truth and
evaluation-significance merge `db8132b` passed its hosted cells at
evidence head `eb29f86`. The transport and logging hardening merge
`d68af4f` (checkpoint `4551c50`, its post-merge inventory refresh)
changed Python source again and superseded that pair in turn. Its
canonical inventory records 2,625
functions across 168 tracked sources with 956 static and 964 runtime
`rag` bindings, and at the checkpoint tree the full locked suites pass
3,460 tests with 9 skips on Linux CPython 3.12.13 and 3,461 with 8
skips on Windows CPython 3.12.13. Reports-and-documentation-only head
`443dce4` froze that historical pair, and both hosted A0 cells passed with
retained evidence.

Task 0.2 source `b07e327` superseded that pair. Its reports passed both
hosted Phase A0 cells with retained evidence at exact seed head `aa7a23b`,
received the external exact-SHA promotion record on
[#93](https://github.com/toddlar00/rag-pipeline/pull/93), and merged to
`main` on 2026-08-21 through history-preserving merge `d142065`. The
pre-activation hardening checkpoint `376750d` superseded that pair in turn;
its evidence child `f8fc95b` passed both hosted cells with retained
artifacts and merged through `f3bcb91`
([#94](https://github.com/toddlar00/rag-pipeline/pull/94)), after which the
workflow-only activation checkpoint merged through `1e79540`
([#95](https://github.com/toddlar00/rag-pipeline/pull/95)). The one-domain
vector-stores dependency checkpoint `0703dde` then changed the four mapped
locks and completed the same cycle: its gate-only child `fa71ff3` passed
the hosted full lane with retained artifacts, received the external
exact-SHA record on
[#97](https://github.com/toddlar00/rag-pipeline/pull/97), and merged
through history-preserving `b3c7cf7`. The one-domain ML/runtime checkpoint
`6f65acb` repeated the cycle: its gate-only child `78a99c2` passed the
hosted full lane with retained artifacts, received the external exact-SHA
record on [#98](https://github.com/toddlar00/rag-pipeline/pull/98), and
merged through history-preserving `d27d85c`. The one-domain Service/UI
checkpoint `248c57a` repeated the cycle: its gate-only child `4945878`
passed the hosted full lane with retained artifacts, received the external
exact-SHA record on
[#99](https://github.com/toddlar00/rag-pipeline/pull/99), and merged
through history-preserving `f1dae3b`. The one-domain test-audit tooling
checkpoint `ca3886c` repeated the cycle: its gate-only child `a129f37`
passed the hosted full lane with retained artifacts, received the external
exact-SHA record on
[#100](https://github.com/toddlar00/rag-pipeline/pull/100), and merged
through history-preserving `0ec2639`. The one-domain PDF/Docling
checkpoint `ed1f370` repeated the cycle: its gate-only child `33c61ff`
passed the hosted full lane with retained artifacts, received the external
exact-SHA record on
[#101](https://github.com/toddlar00/rag-pipeline/pull/101), and merged
through history-preserving `39f6c9e`, completing the Dependabot
supersession queue. The Task 0.6 Node supply-chain ownership checkpoint
`5f45757` repeated the cycle: its gate-only child `344e873` passed the
hosted full lane with retained artifacts, received the external exact-SHA
record on [#102](https://github.com/toddlar00/rag-pipeline/pull/102), and
merged through history-preserving `893c4a0`. The Task 0.7 secret-scan
checkpoint `e055bd0` repeated the cycle: its gate-only child `32153e9`
passed the hosted full lane with retained artifacts, received the
external exact-SHA record on
[#106](https://github.com/toddlar00/rag-pipeline/pull/106), and merged
through history-preserving `376277c`. The Task 0.8 static-security
checkpoint `8891e1b` repeated the cycle: its gate-only child `1533164`
passed the hosted CI lane, including both Phase A0 cells, received the
external exact-SHA record on
[#107](https://github.com/toddlar00/rag-pipeline/pull/107), and merged
through history-preserving `e34103f` (the separate networked supply-chain
vulnerability/SBOM jobs failed at that head). The OCR-program and
casebook-excerpt checkpoint `1ae8502` then changed Python source (no lock
change), superseding the `8891e1b` pair; its gate-only child `766feaf`
passed both hosted Phase A0 cells on [PR #116](https://github.com/toddlar00/rag-pipeline/pull/116), but that head's
dependency-light unit lanes failed at collection. Source `a15232d`
(test guards for those lanes and the casebook fixes, still no lock
change) superseded it before promotion. Source `a15232d` then passed both
hosted Phase A0 cells on [PR
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
The `da6540d` pair (gate-only child `92c92cf`) passed the hosted CI
promotion gate on [PR
#137](https://github.com/toddlar00/rag-pipeline/pull/137), which merged into
`main` as `634c38b`. The test-only source `28ba800` rebases the Nomic
contract test of [PR
#136](https://github.com/toddlar00/rag-pipeline/pull/136) onto that merge;
it pins the Transformers names the pinned Nomic embedding code uses in
`tests/test_embedding_runtime_compat.py`, changes no dependency or lock, and
supersedes the `da6540d` pair.
An independent review of the `28ba800` pair's pull request
([#136](https://github.com/toddlar00/rag-pipeline/pull/136)) approved it
with nits. The review-fix source `8e0b2a6` makes the contract derivation
fail closed on module imports and computed attribute names, tracks
definitions per class, re-derives over every pinned remote-code file, adds a
dependency-free derivation test, and supersedes the `28ba800` pair.
The `8e0b2a6` pair (gate-only child `3062ab3`) passed the hosted CI
promotion gate on [PR
#136](https://github.com/toddlar00/rag-pipeline/pull/136), which merged into
`main` as `2f07510`. The vector-stores promotion source `0333947` makes
oauthlib a governed direct input and moves it from 3.3.1 to 4.0.0
(CVE-2026-49264 and CVE-2026-49265) in the core, full and smoke locks, and
supersedes the `8e0b2a6` pair.
The `0333947` pair (gate-only child `63b22e2`) passed the hosted CI
promotion gate on [PR
#139](https://github.com/toddlar00/rag-pipeline/pull/139), which merged into
`main` as `123bedb`. The tokenless-glyph rotation fix source `c2d7b57` lets
the single-wrap reading-order repair and its lineage mirror rotate a page
intact when only detached zero-token glyphs captured with the upper block
break the run-disjointness proof; it changes no dependency or lock and
supersedes the `0333947` pair.
The `c2d7b57` pair (gate-only child `193e202`) passed the hosted CI
promotion gate on [PR
#140](https://github.com/toddlar00/rag-pipeline/pull/140), which merged into
`main` as `6baf14b`. The table header-row oracle fix source `09340ed` makes
the native table row-count oracle count stacked Markdown header rows the way
docling-core 2.99 exports them; it changes no dependency or lock and
supersedes the `c2d7b57` pair.
The `09340ed` pair (gate-only child `1353ffa`) passed the hosted CI
promotion gate on [PR
#145](https://github.com/toddlar00/rag-pipeline/pull/145), which merged into
`main` as `ce73b61`. The soft-hyphen item-seam fix source `a7847ce` joins a
cited item that ends in a hyphen plus soft hyphen to the next cited item
exactly where the fidelity audit would otherwise fuse the two items'
boundary tokens; it changes no dependency or lock and supersedes the
`09340ed` pair.
The `a7847ce` pair (gate-only child `f7dd3fe`) passed the hosted CI
promotion gate on [PR
#146](https://github.com/toddlar00/rag-pipeline/pull/146), which merged into
`main` as `8cc89b0`. The documentation-only source `578a13a` records the
integrated post-series fixes, relabels the sections integrated by #116,
brings the README dependency blocks up to the declared requirements and
records the private excerpt reprocessing; it changes no code, dependency or
lock and supersedes the `a7847ce` pair.
An independent accuracy review of the `578a13a` pair's pull request
([#147](https://github.com/toddlar00/rag-pipeline/pull/147)) approved it
with nits. The documentation source `f3ec23a` addresses them, records the
published private tort-law re-run and supersedes the `578a13a` pair.
The `f3ec23a` pair (gate-only child `df8623e`) passed the hosted CI
promotion gate on [PR
#147](https://github.com/toddlar00/rag-pipeline/pull/147), which merged into
`main` as `9fff7bb`. The test-only source `e5966e1` makes the heartbeat
failure-budget tests independent of runner speed and raises the Node
subprocess ceilings of three OCR review UI tests; it changes no pipeline
behavior, dependency or lock and supersedes the `f3ec23a` pair.
The `e5966e1` pair (gate-only child `b93eb1f`) passed the hosted CI
promotion gate on [PR
#148](https://github.com/toddlar00/rag-pipeline/pull/148), which merged into
`main` as `3aede7d`. The static-security source `3af52f8` retires the only
static-security suppression (S324 on the non-cryptographic MD5
sparse-vector token hash, expiring 2026-11-30) by marking that hash
`usedforsecurity=False`, which keeps its digests byte-identical, so no
index migration or Qdrant re-index is needed. It runs ruff with
`--isolated --ignore-noqa`, so inline, file-level and range suppression
comments and project ruff configuration cannot hide findings, covers every
blocking rule (including S604 and S606) with positive and negative
fixtures, removes the dead `evaluation/` exemption and pins the Hub Git
blob identity to `git hash-object` values. It changes no pipeline output,
dependency or lock and supersedes the `e5966e1` pair.
The current reports above
bind
that exact clean source and each passes an independent complete
same-platform 9×5 comparison. No hosted result for this replacement pair
is claimed here yet; its hosted checks and external exact-SHA record
remain pending.

## Checking

Provision the exact full/test CPU lock union on CPython 3.12 x86-64, then run
the matching command from a clean checkout. The provisioning commands below
are for an ephemeral CI or disposable managed interpreter only; do not use
`--system` against a durable user Python installation.

```text
python -m pip install --require-hashes -r requirements-lock-tools.lock
uv pip sync --system --strict --torch-backend cpu --require-hashes requirements-full.lock requirements-test.lock requirements-lock-tools.lock
uv pip check --system
python tools/benchmark_phase_a0.py --require-clean --check benchmarks/phase-a0-linux-cpython312.json --output phase-a0-current-linux.json
python tools/benchmark_phase_a0.py --require-clean --check benchmarks/phase-a0-windows-cpython312.json --output phase-a0-current-windows.json
```

Use only the command matching the current operating system. Do not copy one
platform's baseline to the other or hand-edit canonical JSON. Any Python source
or dependency/model-lock change invalidates both baselines and requires a new
clean pre-gate checkpoint plus regeneration on both platforms.

The hosted source-delta gate also requires the pre-gate commit to remain an
ancestor of the final head. Integrate a passing candidate with a
history-preserving merge; a squash or history-rewriting rebase invalidates this
evidence and requires regeneration from the replacement history.

The Task 0.2 gate-only delta after source `b07e327` is limited to the two
reviewed reports and their provenance/status documentation. The force-full
bootstrap, active-workflow fixture, deterministic classifier, terminal
promotion contract, line-ending policy, and Python gates are already part of
the pre-gate source and were exercised while generating and comparing the
baselines. Each successful
hosted cell strictly revalidates the generated report, requires its clean source
identity to equal the exact job head, and builds a verified
two-file evidence directory containing that report and a content-free
`phase-a0-ci-attestation-v1` record. The attestation binds candidate and
pre-gate commit/tree identities, baseline and current-report hashes, normalized
platform, and sorted gate-only paths. CI checks the exact file set and
copied-report hash immediately before upload; missing or extra evidence is an
error, not a warning. Successful evidence is retained for 30 days. A failed
comparison keeps its already-validated candidate report for 7 days; failures
before candidate publication remain content-free in logs and produce no
misleading artifact.
