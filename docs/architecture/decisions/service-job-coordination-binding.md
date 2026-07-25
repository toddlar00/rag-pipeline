# Service job-coordination binding and manager facade

- **Status:** Accepted; implemented locally
- **Decision date:** 2026-07-24
- **Milestone:** R8c-3 service job-coordination inversion
- **Integration state:** Local and unpublished; consult
  [ROADMAP.md](../../../ROADMAP.md) for current validation and merge state

## Context

R8c-1 removed the durable manager's dependency on the large `rag.py` facade,
and R8c-2 did the same for the service host and physical search child. One
concrete application edge remained: `service_runtime.py` imported
`job_manager.py` for detached launch, conservative reconciliation, and the
manager-corruption exception. That made a host-only service import load the
executable manager shell and let launch, reconcile, and corruption policy be
replaced independently.

Launch and recovery share the manager's private ready marker, attempt report,
process identity, cleanup, telemetry, and lease machinery. Extracting only two
functions would either duplicate that policy or create dozens of private
cross-module calls. A thin adapter that still imported the manager would hide,
not remove, the transitive dependency. Requiring an external composition
bootstrap would also break the existing directly constructed service surface.

## Decision

[`job_coordination.py`](../../../job_coordination.py) owns the mechanically
moved durable engine: detached launch, ready-handshake verification, execution,
cancellation observation, exact-process recovery, terminal evidence repair,
and reconciliation. It retains the R8c-1 supervision binding and has no
dependency on `rag.py`, the service, or the manager facade.

[`job_manager.py`](../../../job_manager.py) is a thin compatibility and
executable shell. Its supported errors, result records, launch/reconcile/run
functions, environment constants, process identity helpers, and CLI remain
available at their previous names. Detached launch explicitly executes the
stable sibling `job_manager.py` path; the shell delegates `_manage` and
`reconcile` to the engine. Engine tests now patch the module that owns the
implementation. R8c-4 later gives CLI/UI callers an explicit complete binding
seam instead of using the facade as a mutable service locator.

[`job_coordination_contracts.py`](../../../job_coordination_contracts.py)
contains the manager errors and redacted result records plus the frozen
`ServiceJobCoordinationBinding`. The legacy facade re-exports the exact same
objects, and their historical `job_manager` module identity is retained for
representation and pickle compatibility. A binding carries exactly:

- the detached-launch callable;
- the single-job reconciliation callable; and
- a `JobManagerCorruptError` type used for integrity classification.

Binding construction rejects non-callables and any corruption type outside the
manager-corruption hierarchy. The production accessor returns one immutable
generation containing the exact engine functions and exception type.

`RagApplicationService` resolves and stores one binding during construction.
Every reconciliation site uses that snapshot, including the exact active lease
and `fail_queued` value used by startup, concurrent launch, and idempotent
replay. An explicitly supplied `launcher=` still overrides only launch; the
paired reconcile and corruption policy remain from the snapshot. An omission
sentinel preserves the distinction between an omitted production launcher and
an explicitly supplied invalid value.

## Invariants

- Importing, constructing, starting, and closing `service_runtime` must not
  import `job_manager`, `rag`, physical vector clients, or ML frameworks.
- A service lifetime cannot mix launch, reconciliation, or corruption policy
  from different default binding generations.
- Startup reconciliation never auto-resumes work. Crash-left queued service
  attempts become explicit resumable failures, except a job currently inside
  the service's exact launch window retains `fail_queued=False`.
- Reconciliation receives an already-held exact job lease where the service
  acquired one. Foreign job specifications and terminal service jobs are not
  handed to the coordinator.
- Manager or store corruption is fatal and makes the service unhealthy. Busy
  races remain retryable and do not poison health; not-found and state races
  retain their stable public mappings.
- Detached launch either proves its exact ready handshake or raises. A normal
  launch exception still best-effort terminalizes the attempt. Failure of that
  terminalization is fatal and marks the service unhealthy.
- Process-control exceptions are not swallowed as ordinary launch failures.
- No private job specification, path, exception message, query, token, manager
  metadata, or new network authority enters the service contract.

## Compatibility and consequences

The `rag.py` job CLI, UI, direct manager imports, direct service construction,
manager CLI, detached child command, durable state schema, redacted manager
schema, service/OpenAPI schema, and recovery state machine remain compatible.
The tracked first-party graph stays acyclic, and `service_runtime` has no direct
or transitive manager-shell edge.

The engine move intentionally changes ownership of private implementation
names. Tests or unsupported callers that monkeypatch manager-private helpers
must target `job_coordination`; the supported facade names remain aliases.

This is R8c-3, not completion of R8. `service_runtime` still selects the
production coordination generation for compatible direct construction. The
subsequent R8c-4
[job-application binding](job-application-binding.md) removes the remaining
`rag.py` and UI dependencies on the manager shell while preserving that facade.
R8c-5 then separates the HTTP implementation and routes the service executable
through a lazy outer root. Direct `RagApplicationService` construction remains
supported; the service role is converged, while `rag.py` and the UI still own
separate pipeline composition. Later characterized slices must separate that
facade implementation ownership before widening the root. R12 must also replace
or explicitly validate sibling-file entrypoints for an installed package.

## Evidence

- Binding validation, immutability, stable type identity, pickle continuity,
  facade aliases, and child script identity:
  [`tests/test_job_coordination_contracts.py`](../../../tests/test_job_coordination_contracts.py)
- Durable execution, real detached ready handshake, recovery fault drills, and
  stable manager child command:
  [`tests/test_job_manager.py`](../../../tests/test_job_manager.py)
- Atomic service snapshots, launcher precedence, exact lease/queue arguments,
  corruption/contention mapping, launch rollback, restart recovery,
  idempotency, foreign-job isolation, and cross-process import isolation:
  [`tests/test_service_runtime.py`](../../../tests/test_service_runtime.py)
- Acyclic dependency direction, pinned raw imports, and fresh-process import
  isolation: [`tests/test_architecture.py`](../../../tests/test_architecture.py)
- Legacy job CLI and UI composition:
  [`tests/test_jobs_cli.py`](../../../tests/test_jobs_cli.py) and
  [`tests/test_ui.py`](../../../tests/test_ui.py)
