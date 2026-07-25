# Architecture and Facade Inventory Policy

- **Status:** Implemented locally and independently audited; publication and
  cumulative integration pending
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
access denied. Its output is strictly parsed, bounded, and normalized across
supported CPython 3.12-3.14 interpreter differences. Windows and Linux fresh
checkouts must reproduce the normalized baseline before it can authorize an R8
facade move; any deliberate platform variance belongs in an explicit,
non-gating field or a separately reviewed platform contract.

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

The accepted local checkpoint is `62cb574`. Its 1,005,471-byte canonical
baseline and full inventory SHA-256
`d3aea6ab563b609369f9566c17d6f4bc170ecbbf1e7bc58f294ad1e7290ba1ab`
reproduce on Windows CPython 3.12 and 3.14 and WSL Ubuntu CPython 3.12. All 53
focused tests pass. Independent review found and then verified closure of
an origin/context pair-correlation collision; provenance is now stored and
strictly validated as sorted pairs rather than independent sets.
