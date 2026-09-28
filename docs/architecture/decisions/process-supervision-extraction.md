# Process-supervision extraction

- **Status:** Accepted; merged to `main` on 2026-08-01 through the
  history-preserving integration of
  [#44](https://github.com/toddlar00/rag-pipeline/pull/44)
- **Decision date:** 2026-07-22
- **Implementation:** [PR #33](https://github.com/toddlar00/rag-pipeline/pull/33)
- **Integration state:** Integrated; consult
  [ROADMAP.md](../../../ROADMAP.md) for current status

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
dedicated child. The subsequent
[service job-coordination decision](service-job-coordination-binding.md) moves
the durable engine inward, retains `job_manager.py` as its executable/import
facade, and removes the service-to-manager edge. R8c-4 subsequently routes the
CLI and UI through `job_application.py`, leaving no manager-shell Python import
consumer while retaining detached execution of that shell. R8c-5 supplies a
lazy outer root for the production service role. Pipeline and UI composition
through `rag.py` remain intentional compatibility debt rather than evidence
that supervision belongs in the facade.

## Evidence and history

### Windows virtual-environment worker identity repair (2026-09-06)

Local full-lock CPython 3.12 verification exposed the Windows venv executable's
redirector process: the PID returned by `Popen` differed from the Python worker's
own PID. That violated existing registration and exact-ready-handshake contracts.
The shared `python_worker_launch` helper now selects the current CPython base
executable and supplies the current virtual-environment executable through a
child-only `__PYVENV_LAUNCHER__`, matching the mechanism in
[CPython 3.12 multiprocessing](https://github.com/python/cpython/blob/v3.12.10/Lib/multiprocessing/popen_spawn_win32.py).
The child retains its virtual-environment executable, prefix and package search
environment; the returned process owns the actual worker PID.

This is a deliberate platform correctness repair, not an ownership extraction.
Caller environments are copied, never reconstructed from ambient variables;
Windows caller-supplied launcher overrides are removed case-insensitively. The
base executable comes only from the running interpreter, and an unavailable or
non-absolute base identity fails closed. Non-Windows launch behavior and frozen
or non-CPython executable selection remain unchanged. Startup gates, containment,
PID/birth/nonce handshakes, failure sanitization and cleanup confirmation are not
relaxed. The isolated benchmark's controlled descendant uses the same helper;
its ambient-environment scrub and exact parent/descendant checks remain intact.

The initial full-suite failures and passing local repair qualification are
recorded in the live roadmap and
[qualification evidence](../../evidence/2026-09-06-ocr-local-qualification.md).
Focused tests cover helper environment copying, reserved-key handling, invalid
base identities and actual worker PID/interpreter/package identity. The reviewed
architecture snapshot and replacement full frozen-source Windows run passed
with 6,871 tests passing and seven platform-specific skips; this is not a hosted
cross-platform or release qualification.

- Direct failure-injection coverage:
  [`tests/test_process_supervision_module.py`](../../../tests/test_process_supervision_module.py)
- Real-process facade characterization:
  [`tests/test_process_supervision.py`](../../../tests/test_process_supervision.py)
- Historical implementation plan:
  [`2026-07-22-process-supervision-extraction-plan.md`](../../superpowers/plans/2026-07-22-process-supervision-extraction-plan.md)
- The original design rationale was published separately in
  [PR #32](https://github.com/toddlar00/rag-pipeline/pull/32). This ADR is the
  maintained local decision record and reflects the implemented boundary.
