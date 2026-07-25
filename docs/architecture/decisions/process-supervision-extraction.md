# Process-supervision extraction

- **Status:** Accepted; implemented in draft PR
- **Decision date:** 2026-07-22
- **Implementation:** [PR #33](https://github.com/toddlar00/rag-pipeline/pull/33)
- **Integration state:** Included in the cumulative draft stack; consult
  [ROADMAP.md](../../../ROADMAP.md) for current merge status

## Context

Process-tree containment and deadline handling were embedded in the large
`rag.py` compatibility facade. The code is safety-critical and operating-system
specific, but its direct coupling to telemetry, CLI constants, and mutable
facade names made deterministic failure injection difficult.

Existing consumers also resolve private `rag` names at call time. In
particular, background-job and service code depend on the facade, and the
characterization suite monkeypatches its job, gate, termination, telemetry, and
entrypoint collaborators. Moving the implementation could not silently change
those seams, timeout behavior, cleanup confirmation, exit codes, or telemetry
ordering.

## Decision

The deadline-supervision core lives in the standard-library-only
[`process_supervision.py`](../../../process_supervision.py). It owns:

- the frozen `SupervisionConfig` value;
- Windows Job Object containment;
- POSIX and Windows supervised-start gates;
- confirmed process-tree termination;
- deadline, cancellation, signal, and cleanup handling; and
- the generic supervised-entrypoint runner.

[`rag.py`](../../../rag.py) remains the compatibility facade. It owns concrete
CLI/telemetry policy and constructs the supervision configuration for each
operation. Its wrappers resolve replaceable collaborators from `rag` globals on
every call and pass them into the extracted core. Exception and gate types are
also rebound through the facade so established consumers and monkeypatch seams
remain compatible.

`job_manager.py`, `service_runtime.py`, and `supervised_worker.py` were not
rewired as part of this decision. Dependency-direction cleanup is a separate
milestone because it changes a consumer boundary rather than merely extracting
deterministic supervision policy.

The later
[runtime-supervision binding decision](runtime-supervision-binding.md) performs
the first such consumer migration. It gives `job_manager.py` a frozen concrete
binding without routing it through `rag.py`; the facade path remains available
for compatibility.

## Invariants

- A timed-out or cancelled operation is not reported complete until direct
  worker cleanup is confirmed.
- Unconfirmed cleanup fails closed.
- Child startup remains gated until containment and registration succeed.
- Telemetry start/finalization ordering and public exit behavior remain
  compatible with the characterized facade.
- Mutable collaborators are late-bound; import-time partial application is not
  an acceptable facade replacement.
- The extracted module imports only the Python standard library.

## Consequences

The process loop and containment policy can be tested directly with injected
clocks, factories, and callbacks, while real-process characterization continues
through `rag.py`. The facade is substantially smaller without forcing a broad
consumer migration.

The former `job_manager` to `rag` edge is resolved by the subsequent runtime
binding decision, and the tracked first-party import graph is now acyclic. The
later [service-host binding decision](service-host-binding.md) also removes the
service host's `rag` dependency by moving physical retrieval composition to a
dedicated child. `service_runtime.py` still composes job operations through
`job_manager`, while `rag.py` retains lazy job CLI dispatch. That root
composition is intentional compatibility debt, not evidence that supervision
belongs in the facade. Later changes must characterize job coordination and
recovery before replacing the remaining seam with narrow protocols and one
application root.

## Evidence and history

- Direct failure-injection coverage:
  [`tests/test_process_supervision_module.py`](../../../tests/test_process_supervision_module.py)
- Real-process facade characterization:
  [`tests/test_process_supervision.py`](../../../tests/test_process_supervision.py)
- Historical implementation plan:
  [`2026-07-22-process-supervision-extraction-plan.md`](../../superpowers/plans/2026-07-22-process-supervision-extraction-plan.md)
- The original design rationale was published separately in
  [PR #32](https://github.com/toddlar00/rag-pipeline/pull/32). This ADR is the
  maintained local decision record and reflects the implemented boundary.
