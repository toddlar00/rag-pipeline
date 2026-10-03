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
  (merged through `a2c5629`), the job-heartbeat hardening pair passed on PR
  #137 (merged through `634c38b`), the Nomic contract-test pair passed on PR
  #136 (merged through `2f07510`), the oauthlib promotion pair passed on PR
  #139 (merged through `123bedb`), the tokenless-glyph rotation fix pair
  passed on PR #140 (merged through `6baf14b`), the table header-row oracle
  fix pair passed on PR #145 (merged through `ce73b61`), the soft-hyphen
  item-seam fix pair passed on PR #146 (merged through `8cc89b0`), the
  post-series documentation pair passed on PR #147 (merged through
  `9fff7bb`), the heartbeat test fix pair passed on PR #148 (merged through
  `3aede7d`), the static-security S324 retirement pair at source `3af52f8`
  passed on PR #150 (merged through `76f98d6`), the stacked hosted-flake
  fix pair at source `6b97fc5` passed on PR #151 (merged through
  `584c4a5`), the urllib3 promotion pair at source `aab0326` passed on PR
  #152 (merged through `25f9ba1`), the stacked query-path speedup pair at
  source `18a70ab` passed on PR #157 (merged through `d3754d5`), the
  stacked model-load speedup pair at source `8ce1a8d` passed on PR #158
  (merged through `cc39af5`), the stacked ingestion speedup pair at source
  `6fc0c36` passed on PR #159 (merged through `139e2dc`), the stacked
  Docling conversion hardening pair at source `18de7d6` passed on PR #160
  (merged through `4994191`), the stacked job-manager transient-write
  tolerance pair at source `9e4d7be` passed on PR #161 (merged through
  `d27af15`), the stacked CI and secret-scan tooling pair at source
  `e706fd2` passed on PR #162 (merged through `40994b4`), the stacked
  lexical accuracy toolkit pair at source `5d22bb9` passed on PR #163
  (merged through `6303d8b`), the robustness follow-up pair at source
  `6e7a1e7` passed on PR #165 (merged through `717ac9e`), the
  provider-transport cryptography 50.0.2 pair at source `0b1bbe7` passed
  on PR #166 (pending, not merged), the stacked Service/UI fastapi 0.142.2
  pair at source `f4204f8` passed on PR #167 (pending, not merged), and
  the stacked ML/runtime torch 2.14.1 pair at source `718ee9f` is pending;
  exact-head human review and separate R8 owner authorization are not
  recorded
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
current replacement pair uses the clean ML/runtime dependency source
`718ee9f1eac2bbb4e149141f666ca839d86a18fc` (tree
`57832a08c64f896577a886ccfca1aa20ff321641`), which moves torch to 2.14.1
and torchvision to 0.29.1 and changes no Python source, the last of three
stacked one-domain dependency upgrades, stacked on the Service/UI fastapi
0.142.2 source `f4204f8` (gate-only child `43a93e1`, pending on PR #167,
not merged), which is stacked on the provider-transport cryptography
50.0.2 source `0b1bbe7` (gate-only child `488c848`, pending on PR #166,
not merged), after a series of one-domain
dependency updates, on `main` after the merges of the robustness
follow-ups #165 (`717ac9e`, the base of the provider-transport source's
single commit), the
research-pass status update #164 (`1c348da`), the lexical accuracy toolkit
#163 (`6303d8b`), the CI and
secret-scan tooling #162 (`40994b4`), the job-manager transient-write
tolerance #161 (`d27af15`), the
Docling conversion hardening #160 (`4994191`), the ingestion speedups #159
(`139e2dc`), the model-load speedups #158 (`cc39af5`), the query-path
speedups #157
(`d3754d5`), the urllib3 promotion #152 (`25f9ba1`), the hosted-flake fixes
#151 (`584c4a5`), the static-security S324 retirement #150 (`76f98d6`), the
heartbeat test fix #148 (`3aede7d`), the post-series
documentation #147 (`9fff7bb`), the soft-hyphen item-seam fix
#146 (`8cc89b0`), the table header-row oracle fix #145 (`ce73b61`), the
tokenless-glyph rotation fix #140 (`6baf14b`), the oauthlib promotion #139
(`123bedb`), the Nomic contract test #136 (`2f07510`), the job-heartbeat
hardening #137 (`634c38b`), the TOC glyph-leader fix #138 (`a2c5629`), the
documentation follow-up #135 (`9eb6f94`), the dependency series #122 to #130
(`1cffc58`), the fresh-OCR fidelity source `1ba9415`, the FlagEmbedding
source `ddbef38`, the pytest-xdist source `ff5e64f`, the test-time economy
source `365de1c` and the OCR-program and casebook-excerpt source `29ea76b`;
together they changed Python source, CI configuration, `.gitattributes`, two
test-tooling locks (pytest-xdist and execnet in `requirements-test.lock` and
`requirements-smoke.lock`) and the FlagEmbedding record of
`requirements-core.lock` and `requirements-full.lock`; the stacked
dependency updates also change the vector-stores, Service/UI, test-audit
tooling, ML/runtime, PDF/Docling (docling-core), promoted ML/runtime,
promoted h2, promoted cryptography, promoted oauthlib and promoted urllib3
records of their mapped locks, the provider-transport refresh changes the
cryptography record of `requirements-full.lock`, the stacked Service/UI
refresh changes the fastapi and starlette records of
`requirements-service.lock` and `requirements-full.lock`, the gradio and
gradio-client records of `requirements-full.lock` and adds an
opentelemetry-api record to `requirements-service.lock`, and the stacked
ML/runtime refresh changes the torch and torchvision records of
`requirements-core.lock` and `requirements-full.lock`, but no model
lock. It supersedes the
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
The `3af52f8` pair (gate-only child `f0513b7`) passed the hosted CI
promotion gate on [PR
#150](https://github.com/toddlar00/rag-pipeline/pull/150), which merged into
`main` as `76f98d6`; the separate networked vulnerability/SBOM jobs failed at
that head on three new urllib3 advisories. The stacked hosted-flake fix source
`6b97fc5` makes three hosted-CI reliability fixes: the Phase A0 contained
runner retries transient Windows errors when it removes its capture
directory, so a WinError 32 no longer masks the primary error; the OCR
review launcher and the crop-pack service clamp the remaining close time
they hand on to the 40-second close bound, which Windows monotonic-clock
rounding could exceed; and the POSIX SIGTERM-escalation test holds its
deadline until the grandchild has installed SIG_IGN and written its first
heartbeat. It changes no pipeline output, dependency or lock and supersedes
the `3af52f8` pair for its own pull request.
The `6b97fc5` pair (gate-only child `f5af79a`) passed the hosted CI
promotion gate on [PR
#151](https://github.com/toddlar00/rag-pipeline/pull/151), which merged into
`main` as `584c4a5`. The provider-transport promotion source `aab0326` makes
urllib3 a governed direct input and moves it from 2.7.0 to 2.8.0
(CVE-2026-97687, CVE-2026-97688 and CVE-2026-97689) in the core, full, test,
service, smoke and security locks, and supersedes the `6b97fc5` pair.
The `aab0326` pair (gate-only child `19b9ef9`) passed the hosted CI
promotion gate on [PR
#152](https://github.com/toddlar00/rag-pipeline/pull/152), which merged into
`main` as `25f9ba1`; the networked vulnerability/SBOM jobs passed at that
head. The stacked query-path speedup source `18a70ab` makes three
output-identical query-path speedups: local reranking scores each pool
without FlagEmbedding 1.4.2's discarded batch-size probe forward pass (one
pass instead of two for pools of up to 128 pairs), with float.hex-identical
scores; the legal lexical
analyzer skips each pass whose required literal is absent from the current
string and puts the leading literal first in its slow patterns; and the
strict chunks parser runs a path-free non-finite scan before its field-path
walk, with identical records and error text. It changes no pipeline output,
dependency or lock and supersedes the `aab0326` pair for its own pull
request.
The `18a70ab` pair (gate-only child `e91d42f`) passed the hosted CI
promotion gate on [PR
#157](https://github.com/toddlar00/rag-pipeline/pull/157), which merged into
`main` as `d3754d5`; the networked vulnerability/SBOM jobs passed at that
head. The stacked model-load speedup source `8ce1a8d` makes two
output-identical model-load speedups: model-artifact verification hashes
each file with `readinto` into one reused 1 MiB buffer instead of
allocating a new block per read, with identical sizes, digests and error
text; and a verified load of the pinned Nomic embedding model skips the
`torch.nn.init` calls that its remote code runs before the verified
checkpoint overwrites them, and caches the model only after a
fail-closed proof that the checkpoint header declares every transformer
state-dict entry at its exact shape and that no parameter lies outside the
transformer, with bitwise-identical weights and embeddings. A checkpoint
that omits a parameter now fails closed instead of silently keeping random
weights. It changes no pipeline output, dependency or lock and supersedes
the `18a70ab` pair for its own pull request.
The `8ce1a8d` pair (gate-only child `33d6a26`) passed the hosted CI
promotion gate on [PR
#158](https://github.com/toddlar00/rag-pipeline/pull/158), which merged into
`main` as `cc39af5`; the networked vulnerability/SBOM jobs passed at that
head. The stacked ingestion speedup source `6fc0c36` makes four
output-identical ingestion and indexing speedups: chunk near-duplicate
removal skips each pair whose trigram-count ratio is already below the
threshold and compares the rest with integer bitsets, with identical
keep/remove decisions and audit stream; source enrichment extracts each
page's sorted native words and the hard-hyphen attestations once per
enrichment call through a snapshot-bound, fail-closed cache that is
cleared when the native text-group stages finish; quality validation
attests each record once, and the index quality binding reads and verifies
the source-oracle registry once instead of twice when it loads the chunk
completion itself; and the embedding token counter keeps one verified
tokenizer resident, still re-verifying its bundle on every call, and counts
in fixed slices of 256 texts, with identical counts. If the registry
sidecar is replaced between what used to be two reads, the binding now
proceeds with the first verified snapshot instead of failing with "source
oracle registry file binding changed". It changes no pipeline output,
dependency or lock and supersedes the `8ce1a8d` pair for its own pull
request.
The `6fc0c36` pair (gate-only child `0d7b128`) passed the hosted CI
promotion gate on [PR
#159](https://github.com/toddlar00/rag-pipeline/pull/159), which merged into
`main` as `139e2dc`; the networked vulnerability/SBOM jobs passed at that
head. The stacked Docling conversion hardening source `18de7d6` makes PDF
conversion fail closed on incomplete results and pins Docling's runtime
knobs against the ambient environment: a Docling result that is not a clean
success, including Docling 2.121's PARTIAL_SUCCESS with the failed pages
re-added empty, now raises `DoclingConversionIncompleteError` with a
bounded, content-free summary before any Docling JSON, Markdown or
conversion manifest is written, instead of publishing; conversion passes
explicit accelerator options (4 threads, CPU or CUDA) and runs inside
Docling's scoped default settings, so `OMP_NUM_THREADS` and `DOCLING_*`
variables no longer change its output or write debug renders; the inert
`DOCLING_PDF_BACKEND` write is removed and the log names the backend
Docling actually uses; and the progress bar counts Docling's profiling
records while their DEBUG flood is dropped at INFO, with `--verbose` output
unchanged. In the default environment, complete conversions keep
byte-identical artifacts and receipt digests. It changes no dependency or
lock and supersedes the `6fc0c36` pair for its own pull request.
The `18de7d6` pair (gate-only child `6b2e3f9`) passed the hosted CI
promotion gate on [PR
#160](https://github.com/toddlar00/rag-pipeline/pull/160), which merged into
`main` as `4994191`; the networked vulnerability/SBOM jobs passed at that
head. The stacked job-manager transient-write tolerance source `9e4d7be`
makes the durable-job manager retry transient Windows replace failures of
its state writes: a write whose two-path atomic replace fails with WinError
5, 32 or 33, exactly as `storage_policy.is_transient_replace_error` accepts,
is retried as a fresh atomic write that repeats every storage identity and
DACL check, with backoff of 0.05, 0.1, 0.25, 0.5 and then 1.0 s until a
monotonic deadline, while any other error still propagates at once; the
writes before the ready marker share 3 s (within the UI's default 5 s
`--job-ready-timeout`), the two cancellation-evidence writes share 2 s, and
each later write (the terminal runtime and attempt report, the pre-launch
cancellation's terminal writes and the closed worker-log cap) has its own
8 s. A held `runtime.json` or `attempt.report.json` therefore no longer
fails a healthy job, or makes `run_job` raise `PermissionError` after the
store has committed `succeeded`, while the hold fits those budgets. Store
commit writes are not retried, the heartbeat keeps its own tolerance, and
the success path makes the same writes in the same order. It changes no
pipeline output, dependency or lock and supersedes the `18de7d6` pair for
its own pull request.
The `9e4d7be` pair (gate-only child `209767c`) passed the hosted CI
promotion gate on [PR
#161](https://github.com/toddlar00/rag-pipeline/pull/161), which merged into
`main` as `d27af15`. The stacked CI and secret-scan tooling source
`e706fd2` makes two fail-closed security-tooling changes. The workflow
security validator (`tools/check_ci_security.py`) now rejects YAML
anchors, aliases, tags and merge keys at every node start, which could
hide unpinned actions and write-all permissions from its line-based
checks; rejects every block-scalar header it cannot blank, and measures a
compact `- key: |` body from the key column; and rejects non-space
indentation, non-ASCII whitespace after a structural line's indentation
and line breaks other than LF and CR, where Python's string handling and
YAML read a line differently. The tracked workflows and the rendered
bootstrap keep identical structural lines and still validate cleanly. The
secret scanner's history mode (`tools/check_secrets.py`) reads the objects
`git rev-list` lists through one lock-step `git cat-file --batch` process
instead of two git processes per object, with identical documents and
findings; a framing fault, a missing or ambiguous reply, trailing output
or a non-zero exit now fails the scan with an error, where the old reader
silently skipped a vanished object. It changes no pipeline output,
dependency or lock and supersedes the `9e4d7be` pair for its own pull
request.
The `e706fd2` pair (gate-only child `78777a8`) passed the hosted CI
promotion gate on [PR
#162](https://github.com/toddlar00/rag-pipeline/pull/162), which merged into
`main` as `40994b4`; the networked vulnerability/SBOM jobs passed at that
head. The stacked lexical accuracy toolkit source `5d22bb9` adds
evaluation-only lexical tooling and changes no default.
`eval.py --retriever lexical` scores each query through the production
lexical leg (`rag._bm25_search`), pinned to the chunks snapshot SHA-256 it
validated, instead of the offline fixture adapter that `--retriever bm25`
uses; its reports record the scorer, the rank-bm25 version and the lexical
query policy, and lexical baselines are strict and never compare silently
with offline-BM25 or index reports. An opt-in, default-off query policy,
`function-words-v1` (a versioned, frozen 49-word function-word list in
`retrieval_core` that keeps modals, negation and conditionals), filters
only the query tokens, keeping order and duplicates and falling back to the
unfiltered tokens when nothing would remain, so no chunk or index changes.
`rag._bm25_search` gains a keyword-only `query_policy` that defaults to
`none` and is validated before any I/O, and only
`eval.py --lexical-query-policy` selects another; the default path is
bitwise identical over 8,000 fuzzed comparisons, and the offline-BM25 and
index report shapes and committed baselines are unchanged. It changes no
pipeline output, dependency or lock and supersedes the `e706fd2` pair for
its own pull request.
The `5d22bb9` pair (gate-only child `9b243c3`) passed the hosted CI
promotion gate on [PR
#163](https://github.com/toddlar00/rag-pipeline/pull/163), which merged into
`main` as `6303d8b`; the networked vulnerability/SBOM jobs passed at that
head. The status-only [PR
#164](https://github.com/toddlar00/rag-pipeline/pull/164) then merged into
`main` as `1c348da` without changing the pair. The robustness follow-up
source `6e7a1e7`, on `main` after #164, fixes three causes of intermittent
test failures, two of them product races. Snapshot cleanup
(`artifact_io._PinnedSnapshotDirectory.unlink_regular`) now retries an
unlink that Windows refuses with WinError 5, 32 or 33 after 0.01, 0.05 and
0.15 s, re-validating the pinned directory and the entry's type, link
count and identity before each retry and re-raising the last attempt's
error unchanged if the hold persists, so cleanup still fails closed: each
snapshot first runs a stale-scratch janitor over the shared scratch root,
and another process's janitor briefly reading a live run's owner marker
without `FILE_SHARE_DELETE` made the owner's unlink fail with WinError 32.
The job-manager test's check that the private attempt report omits the
worker PID now decodes the report and compares its keys and values
structurally instead of searching its JSON text for the PID's digits,
which also matched inside timestamps. The strict job-document reader
(`job_runtime._read_private_json`) re-reads, at most four more times
within about 62 ms, when it observes the signature of a legitimate atomic
replace, so an unleased `get_job` or `load_execution` no longer reports a
racing state transition as `JobCorruptError`; every other violation,
including an in-place change the open descriptor shows during the read,
still fails closed at once, and a replace that persists through every
re-read raises the same error. It changes no pipeline output, dependency
or lock and supersedes the `5d22bb9` pair.
The `6e7a1e7` pair (gate-only child `91db280`) passed the hosted CI
promotion gate on [PR
#165](https://github.com/toddlar00/rag-pipeline/pull/165), which merged into
`main` as `717ac9e`. The provider-transport cryptography 50.0.2 source
`0b1bbe7`, on `main` after #165, supersedes the `6e7a1e7` pair and is the
first of three stacked one-domain dependency upgrades (provider transport,
then Service/UI, then ML/runtime). It is lock-only: under the pinned uv
0.12.20, `tools/refresh_locks.py --upgrade-package cryptography` moves
cryptography from 50.0.1 to 50.0.2 in `requirements-full.lock` and changes
no other record, and a second plain regeneration is byte-stable.
`requirements-optional.txt` already allows `cryptography>=50.0.0,<51`, so
no manifest changes. cryptography 50.0.2 rebuilds its Windows, macOS and
Linux wheels against OpenSSL 4.0.3 (50.0.1 bundled 4.0.2) and names no
CVE. It changes no Python source, pipeline output or model lock.
The `0b1bbe7` pair (gate-only child `488c848`) passed the hosted CI
promotion gate on [PR
#166](https://github.com/toddlar00/rag-pipeline/pull/166), whose hosted
checks are green; that pull request is pending and not merged. The stacked
Service/UI source `f4204f8` supersedes the `0b1bbe7` pair for its own pull
request and is the second of the three stacked one-domain dependency
upgrades. Its first commit, `a377dcb`, moves the exact fastapi pin in
`requirements-service.txt` from 0.141.1 to 0.142.2, while gradio stays
within the `>=6.28.0,<7` range of `requirements-optional.txt`. Under the
pinned uv 0.12.20, `tools/refresh_locks.py --upgrade-package fastapi
--upgrade-package gradio --upgrade-package starlette` moves fastapi to
0.142.2 and starlette from 1.3.1 to 1.7.0 in `requirements-service.lock`
and `requirements-full.lock`, moves gradio from 6.28.0 to 6.29.1 and
gradio-client from 2.7.1 to 2.7.2 in `requirements-full.lock`, and adds
opentelemetry-api 1.45.0, the API package that fastapi 0.142 requires (no
SDK or exporter), to `requirements-service.lock`; no other version moves,
and a second plain regeneration is byte-stable. fastapi 0.142 instruments
every FastAPI app by default and, at lifespan startup, adds OTLP exporters
chosen by `OTEL_*` variables; 0.141.1 had none of this. Its second commit,
`f4204f8`, therefore passes FastAPI's `telemetry` argument with
auto-configuration, tracing, metrics, logs and operation spans all off at
the service app (`service_http.create_app`) and, through Gradio's
`app_kwargs`, at the UI launches in `ui.main` and `tools/review_ocr.main`,
restoring the pre-upgrade behaviour; it adds a FastAPI row to the
release-security policy and six new or updated tests that fail before the
change and pass after it. OSV lists no advisory for the replaced or new
versions. It changes no pipeline output or model lock.
The `f4204f8` pair (gate-only child `43a93e1`) passed the hosted CI
promotion gate on [PR
#167](https://github.com/toddlar00/rag-pipeline/pull/167), whose hosted
checks are green; that pull request is stacked on #166, whose hosted
checks are also green, and neither is merged. The stacked ML/runtime
source `718ee9f` supersedes the `f4204f8` pair for its own pull request
and is the last of the three stacked one-domain dependency upgrades. Under
the pinned uv 0.12.20, `tools/refresh_locks.py --upgrade-package torch
--upgrade-package torchvision` moves torch from 2.14.0 to 2.14.1 and
torchvision from 0.29.0 to 0.29.1 (the `+cpu` records and the macOS
records) in `requirements-core.lock` and `requirements-full.lock` and
changes no other record, and a second plain regeneration is byte-stable.
`requirements-audit.txt` moves the normalized versions that pip-audit
checks for the `+cpu` wheels to torch 2.14.1 and torchvision 0.29.1, and
`dependency-vulnerability-policy.json` moves the two `+cpu`
`allowed_skips` (version and expected reason pattern) to the same
versions, keeping their reasons and expiries. numpy stays 2.5.2 (the OCR
observer recipe pin) and transformers 5.16.1 (below 5.17 for the Nomic
embedding). OSV lists no advisory for torch 2.14.0 or torchvision 0.29.0.
Real-model checks on local published runs found no output change: fixed
queries on three runs, plain and reranked, kept the same results, order
and recorded scores; scratch re-conversions of three runs with their
published jobs' arguments (batch jobs narrowed to the one PDF) reached
READY with identical chunk text (38, 50 and 325 chunks); and their stored
embeddings were bitwise equal to the published ones. It changes no Python source or model lock.
Its Windows and
Linux CPython 3.12.13 reports were
generated after the same strict hash-locked synchronization, to its locks
(urllib3 2.8.0, cryptography 50.0.2, fastapi 0.142.2, gradio 6.29.1,
torch 2.14.1 and torchvision 0.29.1), and dependency-consistency checks:
193 marker-resolved distributions on Windows and 191 on Linux.
They bind that one clean source, the same eight
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
then the stacked job-heartbeat hardening source `da6540d`,
then the stacked Nomic contract-test source `28ba800`,
then the stacked Nomic contract-test source `8e0b2a6`,
then the stacked oauthlib promotion source `0333947`,
then the stacked tokenless-glyph rotation fix source `c2d7b57`,
then the stacked table header-row oracle fix source `09340ed`,
then the stacked soft-hyphen item-seam fix source `a7847ce`,
then the stacked post-series documentation source `578a13a`,
then the stacked post-series documentation source `f3ec23a`,
then the stacked heartbeat test fix source `e5966e1`,
then the stacked static-security S324 retirement source `3af52f8`,
then the stacked hosted-flake fix source `6b97fc5`,
then the stacked urllib3 promotion source `aab0326`,
then the stacked query-path speedup source `18a70ab`,
then the stacked model-load speedup source `8ce1a8d`,
then the stacked ingestion speedup source `6fc0c36`,
then the stacked Docling conversion hardening source `18de7d6`,
then the stacked job-manager transient-write tolerance source `9e4d7be`,
then the stacked CI and secret-scan tooling source `e706fd2`,
then the stacked lexical accuracy toolkit source `5d22bb9`,
then the stacked robustness follow-up source `6e7a1e7`,
then the stacked provider-transport cryptography 50.0.2 source `0b1bbe7`,
then the stacked Service/UI fastapi 0.142.2 source `f4204f8`,
then the stacked ML/runtime torch 2.14.1 source `718ee9f`.
Its Windows report
is 51,202 bytes (file SHA-256
`273209a72e4ab5bfbf91c1c37d41861c8ba16d21afcc7e5301051551f02b804d`;
embedded report SHA-256
`81f9c7272807c659a0731cc81bc7643a969809d8ca19a884e198ceb89b820c43`).
Its Linux report is 50,601 bytes (file SHA-256
`2138cb4993f1317dc1d97fb5df15cc8088efe1ba1cbbaf8b4dddb04e542fa2be`;
embedded report SHA-256
`d7090eaf95c8e81a16f2459c6e8e35e671166cb5af27a51ed566980f21a5ce8d`).
Both use CPython 3.12.13 and pass their complete local same-platform 9×5
comparisons. Hosted promotion remains pending.
