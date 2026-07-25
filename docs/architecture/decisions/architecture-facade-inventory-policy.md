# Architecture and Facade Inventory Policy

- **Status:** Implementation included in cumulative draft PR #44; frozen-head
  hosted matrix and independent technical audit passed; not merged or
  human-reviewed
- **Milestones:** Minimum R7 inventory prerequisite for R8 ownership changes
- **Schema:** `tracked-python-ast-v3`

## Context

`rag.py` is both a large implementation module and a compatibility facade.
Tests and production consumers observe more than normal imports: they read
private names, follow re-exported aliases, patch nested attributes, mutate or
delete bindings, inspect signatures and type hints, use import-star behavior,
reload the module, and pickle selected values. A simple line count or import
graph cannot characterize that change surface before implementation ownership
moves behind the facade.

## Decision

[`tools/check_architecture_inventory.py`](../../../tools/check_architecture_inventory.py)
generates schema-v3 canonical JSON in
[`architecture-inventory.json`](../../../architecture-inventory.json). The
inventory has three complementary parts:

- a full static graph of Git-tracked, non-test Python under production, tools,
  and scripts/other sources, including static and constant dynamic imports with
  context and origin provenance;
- flow-aware facade evidence for direct and re-exported `rag` reads,
  import-from use, string and attribute patch targets, and assignment,
  deletion, and nested mutation seams; and
- an isolated runtime contract for observable namespace, import-star,
  signatures and type hints, module/type/pickle identity, mutation and
  restoration behavior, and reload compatibility.

Large definition and runtime details use domain-separated semantic hashes;
counts, names, dependency edges, consumer locations, and mutation categories
remain reviewable. Definition-location churn is hashed separately from callable
semantics so a move and an interface change are not conflated.

The runtime probe executes from an empty temporary working directory with
credentials scrubbed and Python-level network, subprocess, home, and tilde
access denied. An isolated `PYTHONUSERBASE` lets CPython initialize its own
`sysconfig` state without resolving the operator's home or weakening those
denials. Its output is strictly parsed, bounded, and normalized across
supported CPython 3.10-3.14 interpreter differences. The exact public
`pathlib.Path` object is recorded under its stable public identity even on
CPython 3.13, where its implementation module is private; unrelated objects
that merely claim the same private metadata are not normalized. Windows and
Linux fresh checkouts must reproduce the normalized baseline before it can
authorize an R8 facade move; any deliberate platform variance belongs in an
explicit, non-gating field or a separately reviewed platform contract.

The checked-in baseline must be strict canonical JSON with repository-relative
paths and stable ordering. CI checks; it never refreshes automatically. A
refresh requires a stated architectural reason, a named reviewer, review of the
bounded semantic drift summary, and the full baseline diff when hashes change.

## Boundaries

This is characterization, not an API declaration. Recording a private name or
monkeypatch seam does not promise indefinite support; it makes removal an
intentional, reviewed migration. The isolated probe's denials improve
determinism and protect routine generation, but they are not an OS sandbox and
do not make imported code untrusted-safe. Static analysis is conservative and
does not prove runtime reachability.

## Evidence

- Inventory and strict baseline checker:
  [`tools/check_architecture_inventory.py`](../../../tools/check_architecture_inventory.py)
- Adversarial static, runtime, canonicalization, and drift tests:
  [`tests/test_architecture_inventory.py`](../../../tests/test_architecture_inventory.py)
- First-party dependency invariants:
  [`tests/test_architecture.py`](../../../tests/test_architecture.py)

Schema v3 was first implemented at `62cb574`. The replacement baseline bound to
frozen R1 pre-gate source `fdb08d2` is 1,005,966 bytes with full
inventory SHA-256
`2b0e2b6d24f494f305c99a28e76e8146cf1f56987d8f859469db0506a8cf6b4d`.
It records 150 tracked Python sources, 69 non-test modules, 1,981 functions,
565 static and 573 runtime `rag` bindings, and 377 compact runtime callable
records. The complete 43-test inventory suite passes on Windows and Linux
CPython 3.12. The canonical baseline reproduces on Windows CPython
3.10-3.14 and Linux CPython 3.10-3.14.

Independent review previously found and verified closure of an origin/context
pair-correlation collision. The replacement review additionally caught and
rejected an over-broad tilde redirect before the baseline refresh, then exposed
and closed CPython 3.13's private `Path` module identity without hiding spoofed
or project-owned identities. These are bounded compatibility and isolation
repairs, not an unreviewed relaxation of the architecture contract. Gate head
`ed2995e` / tree `c438c82` preserves that source ancestry; pull-request and
direct-dispatch Windows/Linux hosted evidence passed and all four retained A0
bundles were independently verified. A named human reviewer is still required
before integration or any later baseline refresh is accepted.
