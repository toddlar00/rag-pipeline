# Phase A0 Baselines

These files are the canonical per-platform Phase A0 baseline candidates for
the R10/A0b architecture gate. They contain no private corpus content or
credentials. Each report covers the complete nine-scenario set with five
fresh-process repetitions.

## Provenance

Both current reports were generated from the clean Task 0.2 pre-gate source
checkpoint `ca7f54c3192c83e3d0ee99135480a454acdd3af4` (tree
`0a1aaf8e8fbeb1a06bc6462d98bf982bbd81c517`). That source contains the
reviewed deterministic CI classifier, terminal promotion contract, force-full
bootstrap workflow, and security-owned active-workflow fixture. The executing
environments were
synchronized with repository-pinned uv 0.11.31 against the exact CPU
application/test lock union plus its retained bootstrapper:

- `requirements-full.lock`
- `requirements-test.lock`
- `requirements-lock-tools.lock`

The Windows environment used a temporary direct, uv-managed CPython
interpreter rather than a virtual-environment redirector so supervised-child
parent identities remained exact. The Linux environment used a direct POSIX
virtual-environment interpreter. `uv pip sync --strict --torch-backend cpu
--require-hashes` and `uv pip check` passed for 189 Windows and 187 Linux
marker-resolved distributions before generation.

| Platform | Runtime | Report | Bytes | File SHA-256 | Embedded report SHA-256 |
|---|---:|---|---:|---|---|
| Windows x86-64 | CPython 3.12.13 | `phase-a0-windows-cpython312.json` | 50,411 | `6a5388ae25df06a671165c1137ec13b37edacd1c2ba16664e87d97261913368c` | `6fb0a38d1fc5effcb5326484fe1cde8bac78e54b193511e23cf94c32f2fbbbf7` |
| Linux x86-64 | CPython 3.12.13 | `phase-a0-linux-cpython312.json` | 49,828 | `d6bbc5e4b5c111afcec8bb64765ea205d0281167dd1ed034fc4135bb1e3c11b2` | `99183dcf5bf14eac82d36200b89f32b737276736bb742842f967f213f3c3e09d` |

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

Task 0.2 source `ca7f54c` now supersedes that pair. The current reports above
bind that exact clean source and each passes an independent complete
same-platform 9×5 comparison. This direct reports-and-documentation-only child
is the force-full seed candidate. No hosted result or evidence-child SHA is
claimed here yet; hosted checks and the external exact-SHA promotion record
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

The Task 0.2 gate-only delta after source `ca7f54c` is limited to the two
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
