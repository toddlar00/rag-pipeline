# Phase A0 Benchmark Policy

- **Status:** A0a integrated; the latest completed hosted A0b technical
  checkpoint passed at test-audit evidence head `a129f37` and merged
  through `0ec2639`; the PDF/Docling domain replacement pair at source
  `ed1f370` is local-only pending hosted promotion; exact-head human
  review and separate R8 owner authorization are not recorded
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
([PR #100](https://github.com/toddlar00/rag-pipeline/pull/100)). The
current replacement pair uses the clean PDF/Docling dependency-domain
source `ed1f370260dff29a42bb3251198fa1e52e6252ba` (tree
`3247ae5aaeb6f9d6f9357f1ce81b533ff37ba92d`), which changed the four mapped
core/full/smoke/test locks. Its Windows and Linux CPython 3.12.13 reports were
generated after the same strict hash-locked synchronization and
dependency-consistency checks: 189 marker-resolved distributions on
Windows and 187 on Linux. They bind that one clean source, the same eight
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
one-domain PDF/Docling dependency source `ed1f370` supersedes that pair in
turn. Its Windows report is 50,388 bytes (file SHA-256
`3efe2c40bba882be0d3790b98d54583fe8bf70ff986e2d92ef0bc41d6de1fcfd`;
embedded report SHA-256
`968a3443a2a94bb0892253027808a9eaff9b85e6e24717881b4abf8b4d2397e7`).
Its Linux report is 49,800 bytes (file SHA-256
`17f0b96ccdfb6a2d3201cf35700b93d50a8b80ceeb9724d7acbc792ffc1b8d4e`;
embedded report SHA-256
`2babaaa1de42a2a621b2f5fbfd78e1b36216ce678ae08542d1351200a21995c1`).
Both use CPython 3.12.13 and pass their complete local same-platform 9×5
comparisons. Hosted promotion remains pending.
