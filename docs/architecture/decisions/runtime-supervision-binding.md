# Runtime-supervision binding

- **Status:** Accepted; implemented locally
- **Decision date:** 2026-07-24
- **Milestone:** R8c-1 runtime dependency inversion
- **Integration state:** Local and unpublished; consult
  [ROADMAP.md](../../../ROADMAP.md) for current validation and merge state

## Context

The generic deadline and process-containment loop was already isolated in
[`process_supervision.py`](../../../process_supervision.py), but durable jobs
still used `rag.py` as a service locator. `job_manager.py` imported `rag` to
obtain the pipeline script path, operation timeouts, concrete supervisor, and
cleanup exception type. `rag.py` lazily imported `job_manager` for job CLI
dispatch and exception handling, making those two modules the remaining
first-party strongly connected component.

Removing only the import was not sufficient. The four values form one runtime
capability: mixing a supervisor from one policy generation with timeouts or an
exception type from another could change failure handling. Existing callers
also rely on explicit `script_path` and `supervisor` overrides, while the
`rag.py` compatibility facade and its late-bound monkeypatch seams must remain
available.

## Decision

[`runtime_supervision.py`](../../../runtime_supervision.py) is the concrete,
prompt-free supervision composition boundary shared by runtime consumers. It
binds the generic process loop to:

- the stable sibling `rag.py` worker entrypoint;
- the reviewed per-operation deadline defaults;
- the supervised-child and run-ID environment names;
- termination, polling, and startup-gate timing;
- timeout normalization; and
- run-telemetry start and interrupted-run finalization callbacks.

The frozen `PipelineRuntimeBinding` carries the script path, supervisor
callable, cleanup exception type, and an immutable copied timeout mapping.
`default_runtime_binding()` exposes the production value. The durable engine
now owned by `job_coordination.py` resolves exactly one binding at the start of
each run or detached launch and uses fields from that snapshot together. At the
time of this decision that implementation lived in `job_manager.py`; the later
facade split preserves its public surface and explicit override precedence.

`runtime_supervision.py` imports the narrow policy/core modules it composes and
does not import `rag`, `job_manager`, or the service layer. `rag.py` imports
shared constants for compatible defaults and retains its late-bound
supervision facade for established direct callers and tests. The generic
containment implementation remains in `process_supervision.py`; this decision
does not duplicate or move that loop.

## Invariants

- The durable engine and `job_manager.py` facade must not import `rag.py`,
  directly or transitively through the runtime binding.
- One job attempt uses one binding snapshot for its entrypoint, supervisor,
  timeout policy, and cleanup exception handling.
- Binding construction rejects a non-callable supervisor, a cleanup type that
  is not a `RuntimeError` subclass, invalid timeout names, and non-finite or
  non-positive timeout values. Its timeout mapping cannot be mutated after
  construction.
- Explicit `script_path`, `supervisor`, and per-execution timeout overrides
  preserve their established precedence.
- Cleanup remains fail-closed, and telemetry ordering, cancellation,
  containment, ready-handshake, and durable recovery behavior remain governed
  by their existing contracts.
- This boundary changes no corpus, artifact, receipt, or public service schema
  and introduces no new data egress.

## Consequences and residual work

The tracked first-party import graph is now acyclic: `rag.py` may lazily call
the manager, but the manager points inward to the runtime binding instead of
back to the facade. The manager can also be imported and characterized without
loading the large pipeline facade.

This decision is R8c-1, not completion of R8. The later
[service-host binding decision](service-host-binding.md) removes
`service_runtime.py`'s `rag` dependency and gives physical search a dedicated
child composition shell. The subsequent
[service job-coordination decision](service-job-coordination-binding.md) moves
the engine to `job_coordination.py`, leaves `job_manager.py` as its stable
shell, and removes the service-to-manager edge. Application composition remains
split between `rag.py`, the service modules, the UI, and their CLIs; `rag.py`
retains lazy job dispatch for compatibility. A later slice must introduce one
application root before removing any proven facade aliases.

The sibling-file entrypoint is correct for the current flat repository. R12
packaging work must deliberately replace or validate that assumption when
stable installed console entry points are introduced.

## Evidence and history

- Frozen binding and concrete adapter characterization:
  [`tests/test_runtime_supervision.py`](../../../tests/test_runtime_supervision.py)
- Durable job and binding characterization:
  [`tests/test_job_manager.py`](../../../tests/test_job_manager.py)
- Generic and facade supervision behavior:
  [`tests/test_process_supervision_module.py`](../../../tests/test_process_supervision_module.py)
  and [`tests/test_process_supervision.py`](../../../tests/test_process_supervision.py)
- Tracked-source import-DAG enforcement:
  [`tests/test_architecture.py`](../../../tests/test_architecture.py)
- Prior extraction decision:
  [Process-supervision extraction](process-supervision-extraction.md)
- Subsequent service decision:
  [Service-host binding and isolated search composition](service-host-binding.md)
- Subsequent job-coordination decision:
  [Service job-coordination binding and manager facade](service-job-coordination-binding.md)
