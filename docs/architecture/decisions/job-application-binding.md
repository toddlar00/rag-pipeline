# Job-application binding and manager-shell isolation

- **Status:** Accepted; implemented locally
- **Decision date:** 2026-07-24
- **Milestone:** R8c-4 application-shell dependency inversion
- **Integration state:** Local and unpublished; consult
  [ROADMAP.md](../../../ROADMAP.md) for current validation and merge state

## Context

R8c-3 moved durable execution and recovery into `job_coordination.py` and left
`job_manager.py` as a compatible import and executable shell. The service then
consumed an inward coordination binding, but the legacy `rag.py jobs` command
and local UI still imported the manager shell as a service locator. Their
tests replaced attributes on that shell, so merely changing the imports would
have silently changed launch, reconciliation, cancellation, resume, rollback,
and redaction behavior.

Those two application surfaces also have different lifetime rules from the
service. The CLI opens a store for one command. Each UI callback reads the
current mutable local configuration and opens a fresh store; a nested refresh
must use the same operation generation but may observe an updated configured
root. The service owns a separate `.rag-service-jobs` store with an ownership
marker allowlist. Converging those store instances would be incorrect.

## Decision

[`job_application.py`](../../../job_application.py) defines the frozen,
dependency-inward `JobApplicationBinding`. One generation carries exactly:

- the job-store factory;
- detached launch;
- single-job reconciliation;
- all-job reconciliation; and
- the canonical manager-error type used at the CLI boundary.

Construction rejects every non-callable operation and any error type outside
the `JobManagerError` hierarchy. The production accessor returns one immutable
singleton composed from `job_runtime.JobStore`, the exact
`job_coordination.py` functions, and the canonical contract error. The module
does not import `rag`, `ui`, the service, an API adapter, or `job_manager`.

`rag.py` resolves one binding at the start of a jobs command. The same object
provides store construction, every action operation, and manager-error
classification in `main`; a replacement default cannot be mixed into an
in-flight command. The private command function also accepts an explicit
binding for direct characterization.

The UI resolves one binding after its shared-mode and configuration gates.
Submit, cancel, and resume pass that same object into their nested refresh.
Each store request still invokes the captured factory with the current
`_config["job_root"]`, preserving fresh-store and call-time configuration
semantics. Shared mode returns before binding resolution or storage access.

`job_manager.py` remains unchanged as the stable public/executable facade.
No production module imports it. Detached children still execute its exact
sibling path and it continues to expose object-identical functions, results,
errors, process helpers, environment constants, pickle identities, and CLI.

## Invariants

- The tracked first-party import graph is acyclic and no production Python
  module imports or transitively imports the manager shell. Detached execution
  still invokes that shell as a process entrypoint.
- Importing `job_application` loads no outward facade, UI, service, API, or
  physical retrieval implementation.
- One CLI command or UI action cannot mix store, launch, reconciliation, or
  manager-error policy from different binding generations.
- CLI submit and resume preserve exact timeout forwarding, `BaseException`
  propagation, best-effort failed-state publication, and primary-error
  precedence when rollback also fails.
- UI refresh, reindex, cancel, and resume preserve their exact operation order,
  current-root lookup, fresh-store behavior, ready timeout, and type-name-only
  error rendering. Private arguments and exception messages are not rendered.
- Publicly shared UI mode touches neither the binding nor durable storage.
- CLI/UI job roots remain distinct from the service-owned job root and marker
  policy. No durable schema, service/OpenAPI schema, process command, or network
  authority changes.

## Compatibility and consequences

Supported `python rag.py jobs ...`, local UI controls, direct manager imports,
`python job_manager.py`, detached execution, restart recovery, and public
result/error identities remain compatible. Tests that formerly monkeypatched
the manager shell to influence a different application now inject a complete
binding generation at that application's explicit seam. Direct consumers of
the manager facade retain the same aliases.

This is R8c-4, not completion of R8. It removes the remaining production
manager-shell import/service-locator edges and prepares a real outer composition
root. A root cannot
honestly import the current `rag.py` implementation and also be consumed by
that same executable facade without a cycle. The next slices must separate the
HTTP adapter and the narrow pipeline implementations from their executable
facades before `application_composition.py` can be the sole outer constructor.
The physical search, manager, and supervision children remain intentional
process-entry shells.

## Evidence

- Binding validation, immutability, exact production identity, and isolated
  import behavior:
  [`tests/test_job_application.py`](../../../tests/test_job_application.py)
- Every CLI job action, one-generation lookup, exact polling/resume/timeout
  order, rollback and process-control failures, and redaction:
  [`tests/test_jobs_cli.py`](../../../tests/test_jobs_cli.py)
- UI generation/store timing, reindex/cancel/resume/refresh order, shared-mode
  isolation, launch failure, and redaction:
  [`tests/test_ui.py`](../../../tests/test_ui.py)
- Manager facade alias, type, pickle, executable, and real detached continuity:
  [`tests/test_job_coordination_contracts.py`](../../../tests/test_job_coordination_contracts.py)
  and [`tests/test_job_manager.py`](../../../tests/test_job_manager.py)
- Exact dependency direction, zero manager import consumers, raw imports,
  acyclicity, and fresh-process import order:
  [`tests/test_architecture.py`](../../../tests/test_architecture.py)
