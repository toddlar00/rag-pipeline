# Phase A0 Benchmark Policy

- **Status:** A0a implemented locally; A0b baselines and CI wiring implemented
  locally, hosted frozen-head checkpoint pending
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
  comparison and retained current-report artifacts. A following gate-only
  evidence commit may add those baselines, CI wiring, evidence docs, and a
  path-scoped LF rule that preserves canonical report bytes, but no Python
  source or dependency/model lock. CI and review bind the resulting final R1
  candidate commit/tree; any intervening source or lock change invalidates and
  regenerates both baselines.

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

The local gate candidate uses clean pre-gate source `0fe69f3` and separate
Windows and Linux CPython 3.12 x86-64 reports. Both were generated under the
exact `requirements-full.lock`, `requirements-test.lock`, and retained
`requirements-lock-tools.lock` union after strict hash-locked synchronization
and dependency-consistency checks; each passes an independent complete 9×5
same-platform comparison. The gate-only candidate adds those reports, their
path-scoped LF normalization, matching CI matrix cells, and 30-day
current-report retention without changing Python or dependency/model locks.

A0b nevertheless remains pending. No local smoke report, subset, noncanonical
interpreter, baseline copied between operating systems, or local-only
comparison is authoritative. Both Tier-1 hosted jobs must pass against their
matching baseline at one frozen final commit/tree and retain their reports.
Until that exact-head evidence exists, Phase A0 does not authorize the R8
ownership move.

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
