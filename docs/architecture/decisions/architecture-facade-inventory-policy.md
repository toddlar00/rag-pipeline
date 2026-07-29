# Architecture and Facade Inventory Policy

- **Status:** Implementation included in cumulative draft PR #44; replacement
  compatibility refresh local and independently red-team-audited; not merged
  or human-reviewed, with exact replacement-head review pending
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

Schema v3 was first accepted at `62cb574`. The replacement baseline bound to
pre-gate source `e904fa6` is 1,360,825 bytes with full
inventory SHA-256
`03b1a7b9a1fda04270d9e155abacefafcc6a25acb32e8b7e21737b48e63b2130`.
It records 162 tracked Python sources, 74 non-test modules, 2,563 functions,
877 static and 885 runtime `rag` bindings, and 607 compact runtime callable
records. The complete 43-test inventory suite passes on Windows CPython 3.12.
The earlier `fdb08d2` baseline reproduced on Windows and Linux CPython
3.10-3.14; this expanded source checkpoint has not repeated that ten-cell
matrix.

Independent review previously found and verified closure of an origin/context
pair-correlation collision. The replacement review additionally caught and
rejected an over-broad tilde redirect before the baseline refresh, then exposed
and closed CPython 3.13's private `Path` module identity without hiding spoofed
or project-owned identities. These are bounded compatibility and isolation
repairs, not an unreviewed relaxation of the architecture contract. The local
source checkpoint is `e904fa6ea7419dac6797dc11af7cb1e507fb456c` (tree
`4fe1f457d6a60668319234988fb181bb989ad2fb`); hosted replacement evidence
remains pending.
