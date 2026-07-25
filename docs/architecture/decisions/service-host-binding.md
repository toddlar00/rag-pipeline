# Service-host binding and isolated search composition

- **Status:** Accepted; implemented locally
- **Decision date:** 2026-07-24
- **Milestone:** R8c-2 service-host dependency inversion
- **Integration state:** Local and unpublished; consult
  [ROADMAP.md](../../../ROADMAP.md) for current validation and merge state

## Context

After the durable-job supervision inversion, `service_runtime.py` still used
the large `rag.py` compatibility facade as a service locator for five concrete
values: `search_index`, the supervised process runner, its cleanup exception,
the remote-embedding prefix tuple, and the path-wide service-instance lease.
That coupling made a host-only import load pipeline composition and allowed
tests to replace individual policy fields independently.

The parent and search child have deliberately different authority. The parent
owns service state, deadlines, private request files, cleanup, health, and
public-response validation. Only the child should load physical retrieval
adapters. The refactor also had to preserve the existing service-instance lock
identity: changing from the hashed sidecar under `.rag-locks` to a JobStore
lock would let old and new processes take different locks during an upgrade.

## Decision

[`service_runtime_binding.py`](../../../service_runtime_binding.py) defines one
frozen `ServiceRuntimeBinding`. It carries the fixed child script path,
supervisor, cleanup exception type, instance-lease factory, and remote-model
predicate. Construction validates the complete capability set. A service
instance snapshots one binding during construction; a direct
`supervised_search` call resolves one binding before creating its private
request directory.

The concrete fields point inward to three shared modules:

- [`runtime_supervision.py`](../../../runtime_supervision.py) supplies the
  reviewed process runner and cleanup type;
- [`embedding_policy.py`](../../../embedding_policy.py) owns the exact,
  case-sensitive remote embedding families shared with `rag.py`; and
- [`resource_lease.py`](../../../resource_lease.py) owns the canonical
  path-wide OS lease, reentrant process state, fork reset, and persistent
  `.rag-locks` sidecar policy.

`rag.py` retains late-bound private lease facades and the public
`VectorStoreBusyError`, but those facades now use the same resource-lease
registry as the service. Existing vector-store monkeypatch seams, cleanup
precedence, retention-aware sidecar placement, and cross-process crash release
remain characterized.

[`service_search_worker.py`](../../../service_search_worker.py) is the small
child composition shell that imports both `service_runtime` and `rag`. The
parent launches this fixed sibling path with only the internal action and two
private file paths. The shell injects `rag.search_index` into
`search_worker_main`; neither the query nor corpus configuration appears on
the command line. `service_runtime.py` no longer imports `rag`, directly or
transitively.

## Invariants

- One service/search operation uses one complete binding generation; replacing
  the default cannot mix a supervisor with a different cleanup policy or
  instance-lease factory mid-operation.
- Local-only cloud-model refusal happens before service filesystem creation.
  The host constructor performs no model/cache access, credential lookup, or
  transport construction; physical embedding work remains in the child.
  Supported and known-but-unsupported remote families receive the same egress
  gate.
- Search stays Qdrant-only with the exact collection, embedding model, filters,
  result limit, hybrid tri-state, lock timeout, disabled reranker, chunks path,
  and immutable release-security policy.
- Query/configuration values cross the process boundary only in owner-private,
  bounded request files. Worker output is silenced; its result envelope is
  bounded, strict, and redacted. Uncertain cleanup is fatal.
- The singleton lease still targets `service_state_root / "instance"`, uses a
  zero-timeout try-lock, and creates its hashed sentinel under
  `service_state_root / ".rag-locks"` without creating an `instance`
  directory. Same-process reentrancy, cross-process exclusion, and kernel
  release after process death remain intact.
- Public service schema v1, internal search-envelope schema v2, corpus config
  schema v1, OpenAPI bytes, durable-job schemas, error codes, and HTTP exposure
  policy do not change.
- This worker is trusted contained code, not a sandbox. The existing process
  containment limitations and local-only product boundary remain unchanged.

## Consequences and residual work

A host-only import can now validate configuration, manage jobs, and construct
the service without loading `rag` or optional vector/ML clients. Physical
search composition is visible at one small child boundary, and the shared
lease module prevents a split-lock migration.

This is R8c-2, not completion of R8. The subsequent
[service job-coordination decision](service-job-coordination-binding.md)
removes the service's direct and transitive `job_manager` shell dependency by
moving the durable engine inward and snapshotting one frozen capability.
`service_api.main`, the UI, and the `rag.py` CLI still construct concrete
applications separately. A later characterized slice must converge those roots
without weakening the service ownership, idempotency, recovery, or
process-containment contracts. R12 packaging must also replace or explicitly
validate sibling-file worker paths when installed console entry points exist.

The former direct `python service_runtime.py _search_worker ...` path was an
internal implementation action, not the documented service CLI. The runtime
script now rejects every direct invocation with exit code 2; the hidden action
belongs to `service_search_worker.py`. The supported host entrypoint remains
`python service_api.py serve ...`.

## Evidence

- Binding validation, immutability, and exact production wiring:
  [`tests/test_service_runtime_binding.py`](../../../tests/test_service_runtime_binding.py)
- Worker composition, argument rejection, and real redacted subprocess:
  [`tests/test_service_search_worker.py`](../../../tests/test_service_search_worker.py)
- Exact search arguments, cleanup/deadline mapping, binding snapshots,
  sidecar continuity, startup rollback, and cross-process service exclusion:
  [`tests/test_service_runtime.py`](../../../tests/test_service_runtime.py)
- Shared lease compatibility and existing concurrency/fork behavior:
  [`tests/test_resource_lease.py`](../../../tests/test_resource_lease.py) and
  [`tests/test_vector_store_concurrency.py`](../../../tests/test_vector_store_concurrency.py)
- Import-DAG, composition-shell, raw-import, and isolated-import gates:
  [`tests/test_architecture.py`](../../../tests/test_architecture.py)

The successful physical Qdrant adapter test uses deterministic in-process
embeddings, while the real hidden-worker subprocess test exercises routing,
silencing, and the stable failure envelope. Those are deliberately orthogonal:
monkeypatched deterministic embeddings do not survive process execution, and
a full successful child search would otherwise require a preinstalled reviewed
model. A cache-gated end-to-end worker profile remains optional release
evidence rather than a hermetic unit gate.
