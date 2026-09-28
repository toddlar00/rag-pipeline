# Phase A0 Baselines

These files are the canonical per-platform Phase A0 baseline candidates for
the R10/A0b architecture gate. They contain no private corpus content or
credentials. Each report covers the complete nine-scenario set with five
fresh-process repetitions.

## Provenance

Both current reports were generated from the clean pytest-xdist source
checkpoint
`c31f4c930d7a3a1c0410a2583bbc2ec69cf1b87f` (tree
`cdc6c47ea99358110861bef8e858b47db362e4e1`), stacked on the test-time economy checkpoint
`365de1c` and the OCR-program and casebook-excerpt checkpoint `29ea76b`.
That source contains everything through the Task 0.8
static-security merge `e34103f` plus the local OCR accuracy, retry and
guided-review program, opt-in AI evidence search, the passive cleanup
audits, the LLM transport and worker-launch repairs, the casebook
excerpt/supplement pipeline (including the opt-in OCR angle-classifier
and interleaved-region-merge flags, the footnote placement replay, the
split-item duplicate retraction and quality schema 13), four reviewed
architecture-inventory refreshes, the POSIX symlinked-interpreter identity
fix, an LF attribute for top-level test fixtures, dependency-light
test guards, a Python 3.10/3.11-tolerant import-guard test, a
60-minute Windows unit-lane timeout and the test-time economy changes
(a session-shared architecture inventory, an indexed inventory builder,
cached lock-record parsing and executed-source digests, and a Windows
unit lane split into three shards) and pytest-xdist for the unit lanes.
The only lock change adds pytest-xdist 3.8.0 and execnet 2.1.2 to
`requirements-test.lock` and `requirements-smoke.lock`; no model lock
changed. The executing
environments were
synchronized with repository-pinned uv 0.12.5 against the exact CPU
application/test lock union plus its retained bootstrapper:

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
| Windows x86-64 | CPython 3.12.13 | `phase-a0-windows-cpython312.json` | 51,080 | `86f4911ff2870bcd8dbf2ba14ea577358a2860d12ef0017d191d4227dfe4993d` | `08a434303d78a0cf087c38d77eaed1c53551d0c31b746bc02a8b881fe002914b` |
| Linux x86-64 | CPython 3.12.13 | `phase-a0-linux-cpython312.json` | 50,458 | `529ef6a6debb456a474dbe9c8ca5c15b58323301ab331887d6e35e2155ee6d7b` | `df87bba7440da86d9a2cd1e2c810ef577d64b2529f373081c69fe4fb096fe9cc` |

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
supersedes the `365de1c` pair for its own pull request.
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
