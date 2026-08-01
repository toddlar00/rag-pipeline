# Service HTTP boundary and lazy application composition

- **Status:** Accepted; implementation merged to `main` via cumulative
  [PR #44](https://github.com/toddlar00/rag-pipeline/pull/44)
- **Decision date:** 2026-07-24
- **Milestone:** R8c-5 service-adapter and composition-root inversion
- **Integration state:** Merged to `main` on 2026-08-01 through the
  history-preserving integration of PR #44, after its all-green hosted run at
  exact head `ed2995e` and the owner merge decision. Consult
  [ROADMAP.md](../../../ROADMAP.md) for current validation and merge state

## Context

The local service previously placed four responsibilities in
`service_api.py`: the authenticated FastAPI adapter, the OpenAPI document,
credential-file and command-line handling, and concrete construction of
`RagApplicationService`. That shape was acyclic, but it made the executable
facade own the implementation and remain a separate construction site. An
outer composition module could not replace that construction without either
depending back on the facade or moving its HTTP implementation first.

The split must preserve a security-sensitive boundary. Host and peer checks,
role authentication, bounded bodies and query values, concurrency, redacted
problem responses, lifecycle cleanup, and the static OpenAPI v1 contract may
not drift. Direct embedded `service_api.create_app(...)` callers, flat-script
operation, token-file hardening, public aliases, and the legacy
`ServiceCredentials` pickle path must also remain compatible.

## Decision

[`service_http.py`](../../../service_http.py) owns the HTTP implementation. It
defines a structural `ServiceRuntimePort` for the operations used by the
adapter and imports only `service_contracts.py` from the first-party graph. It
does not import or construct `service_runtime.py`, `rag.py`, a manager shell,
or an outer application root.

One frozen `ServiceHttpBinding` supplies the runtime exception class and the
default and maximum job-page limits. Construction accepts an `Exception`
subclass, rejects `BaseException`, validates positive non-boolean integer
limits, and requires the default not to exceed the maximum. `create_app`
captures that complete object once; request handlers and the reconciliation
loop cannot mix policy generations. The adapter retains the runtime at
`app.state.runtime`, while ASGI lifespan remains solely responsible for
starting, reconciling, and closing it.

[`application_composition.py`](../../../application_composition.py) is the
dependency-light outer root for the production service role. Cold importing
it executes only standard-library imports; the static one-way concrete imports
are function-local inside a lazy, thread-safe builder. That builder returns one
frozen `ServiceApplicationComposition` containing:

- the runtime and HTTP factories;
- the service-runtime binding;
- the service job-coordination binding; and
- the HTTP policy binding.

`create_service_runtime`, `create_service_http_app`, and
`create_service_application` accept an explicit composition for tests and
embedding. The combined factory resolves one generation, constructs the
runtime first, and passes the same generation to the HTTP factory. Explicit
bindings take precedence. Private sentinels distinguish an omitted search
runner or launcher from explicit `None`, preserving the concrete runtime's
established default-selection behavior. A failed lazy build is not cached and
can be retried.

[`service_api.py`](../../../service_api.py) remains the stable executable and
import facade. It owns credential files, argument parsing, configuration and
release-policy loading, lazy Uvicorn startup, and the flat
`python service_api.py` command. Its supported HTTP names are object-identical
aliases from `service_http.py`; `ServiceCredentials.__module__` remains
`service_api` for representation and pickle continuity. The legacy
`create_app` signature delegates through the root, and `main(serve)` now calls
the combined service factory after loading its non-secret registry and private
credentials. `init-tokens` resolves neither the composition singleton nor
Uvicorn.

## Invariants

- The tracked first-party graph is acyclic. `service_http` depends only on
  `service_contracts`; only the outer root and service facade import the HTTP
  implementation; no tracked production Python module imports `service_api`.
  That does not make the facade unused: `python service_api.py` is the
  supported service host entrypoint.
- Importing the outer root loads no service, FastAPI/Starlette, Uvicorn,
  pipeline facade, manager shell, vector client, or model stack. Resolving the
  service composition still loads neither `service_api`, `rag`, nor
  `job_manager`.
- Importing `service_runtime` loads no HTTP/root/facade modules. Importing
  `service_http` loads no concrete runtime or pipeline implementation.
- One application build cannot mix runtime, job-coordination, or HTTP policy
  generations. Runtime construction failure prevents HTTP construction;
  `Exception` and `BaseException` failures propagate without partial fallback.
- The HTTP lifecycle preserves exact startup/shutdown redaction. Fatal bound
  reconciliation failures and unexpected exceptions mark the runtime
  unhealthy; nonfatal bound failures do not; `BaseException` is not swallowed;
  cleanup still runs.
- Token contents, filesystem paths, raw exception text, job arguments, and
  provider identities remain outside public responses and CLI error text.
- The OpenAPI schema and network authority do not change. The normalized
  OpenAPI v1 document retains SHA-256
  `169b09c0a820c72483ff74da3e8a48175371b1c6d5ee9dbb4371f5d1953b02ce`.
- `job_manager.py` has zero production Python-import consumers, while detached
  coordination still executes its stable sibling path. The physical
  `service_search_worker.py` and `supervised_worker.py` process-entry shells
  likewise remain intentional and unchanged.

## Compatibility and consequences

The authenticated routes, response schemas and headers, embedded app factory,
direct `RagApplicationService` construction, credential type, token commands,
flat service command, Uvicorn options, and service child processes remain
compatible. Existing callers may still construct a runtime directly and wrap
it through `service_api.create_app`; new executable composition is visible at
one explicit root.

This is a real outer root for the production **service role**, not completion
of R8 and not a universal application root. `rag.py` still owns both pipeline
implementation and its CLI facade, the local UI still composes pipeline
operations through that facade, and the physical children remain separate
composition shells by design. A later characterized slice must separate the
narrow pipeline implementation from `rag.py` before the root can compose CLI
or UI roles without creating a cycle. The large OpenAPI builder also remains a
later R9 decomposition target; moving it here does not make it smaller.

## Evidence

- Lazy singleton construction, exact concrete wiring, override precedence,
  omission semantics, failure atomicity, retry, and real-factory compatibility:
  [`tests/test_application_composition.py`](../../../tests/test_application_composition.py)
- HTTP binding validation, structural-runtime operation, paging/error policy,
  lifecycle and reconciliation failures, facade identities, pickle continuity,
  delegation, and normalized OpenAPI bytes:
  [`tests/test_service_http.py`](../../../tests/test_service_http.py)
- Full HTTP contract, token-file safety, CLI construction/redaction, flat-script
  help, and embedded compatibility:
  [`tests/test_service_api.py`](../../../tests/test_service_api.py)
- Real loopback Uvicorn behavior and graceful lease release:
  [`tests/test_service_live.py`](../../../tests/test_service_live.py)
- Exact dependency graph, raw imports, acyclicity, isolated imports, ordering,
  aliases, and facade/root laziness:
  [`tests/test_architecture.py`](../../../tests/test_architecture.py)
