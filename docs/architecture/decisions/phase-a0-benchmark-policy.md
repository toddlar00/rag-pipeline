# Phase A0 Benchmark Policy

- **Status:** A0a implemented locally; A0b replacement baselines and repaired
  CI wiring implemented locally, hosted replacement frozen-head checkpoint
  pending
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
  resulting final R1 candidate commit/tree; any intervening source or lock
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

The replacement gate uses clean pre-gate source
`c1bc042c862c42964e6084967f57987944e29f6a` (tree
`4a37989c32d2a6743ccdef47bfe20460d165af32`) and separate Windows and Linux
CPython 3.12 x86-64 reports. Both were generated under the
exact `requirements-full.lock`, `requirements-test.lock`, and retained
`requirements-lock-tools.lock` union after strict hash-locked synchronization
and dependency-consistency checks: 189 marker-resolved distributions on
Windows and 187 on Linux. They bind one clean source, the same eight LF and
`HEAD`-identical dependency/model inputs, and the complete 9×5 scenario
contract. Each passes an independent complete same-platform comparison. The
repaired CI matrix, LF policy, source gates, and Python 3.10-3.14 compatibility
normalization precede `c1bc042` and were exercised by generation and
comparison. The following gate-only delta is limited to the two reports and
their provenance/status documentation.

A0b nevertheless remains pending. No local smoke report, subset, noncanonical
interpreter, baseline copied between operating systems, or local-only
comparison is authoritative. Both Tier-1 hosted jobs must pass against their
matching baseline at one frozen final commit/tree and retain their reports.
Until that exact-head evidence exists, Phase A0 does not authorize the R8
ownership move.

The pull-request matrix explicitly checks out the exact PR head rather than the
synthetic merge ref. Before comparison, the harness validates and publishes the
candidate report. Failures identify only a stable stage and content-free
diagnostic code; raw paths, corpus material, exception text, and credentials are
not logged. A failed comparison report is retained for 7 days for diagnosis.

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

Release-defect source `c1bc042` then changed Python and invalidated those
reports. Its current inventory records 1,995 functions and 378 compact runtime
callables. After strict synchronization and dependency checks for 189 Windows
and 187 Linux distributions, the Windows replacement report is 49,077 bytes
under CPython 3.12.13 (file SHA-256
`931758678b0d6c9859b35f95ec0eea80f9ee60d09ac6f9e10ca7c79c57860c0f`;
embedded report SHA-256
`a583d8578f182d07d7aa9439c7debc87e98b62d711db82530b6c9dd00888491a`).
The Linux report is 48,495 bytes under CPython 3.12.3 (file SHA-256
`77f2147c4810143d06de66b7f4239aed55fe2a4951104ceaf9bddd9e10dc197e`;
embedded report SHA-256
`c13233980a212da3a121d0af362098a2c865de5ff8e5e748c839fe272e5b91e2`).
Each current candidate passes an independent complete same-platform 9×5
comparison. The following reports-and-documentation-only commit freezes the
new local candidate; publishing its exact commit/tree plus hosted execution,
retained evidence, and review at that head remain outstanding.
