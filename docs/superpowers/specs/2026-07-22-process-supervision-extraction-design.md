# Design: Extract process supervision from `rag.py`

Date: 2026-07-22
Status: Approved design, implementation blocked (see Sequencing)
Milestone: First slice of the ROADMAP P3 "Runtime decomposition" track

## 1. Goal and constraints

Move the deadline-supervision core out of `rag.py` into a new stdlib-only
module with identical observable behavior.

- Strictly behavior-preserving: any defect discovered during extraction is
  recorded in the ROADMAP as follow-up work, not fixed in this milestone.
- `rag.py` remains the stable compatibility facade; all existing names keep
  working.
- `job_manager.py`, `service_runtime.py`, and `supervised_worker.py` require
  zero edits.
- Scope is process supervision only. Vector lifecycle extraction is a separate
  later milestone, as is any dependency-direction change between `rag.py` and
  `job_manager.py`.

## 2. Baseline survey (2026-07-22, approximate — see Sequencing)

`rag.py` is ~13,300 lines. The supervision core is ~700 lines (roughly
11000-11720): `_WindowsKillJob` containment, POSIX/Windows start gates,
`_terminate_supervised_process`, `_run_cli_with_deadline`,
`_run_rag_entrypoint`.

Supervision is layered, not duplicated: `job_manager.py` injects
`rag._run_cli_with_deadline` as its supervisor and separately owns detached
job spawning; `service_runtime.py` calls `rag._run_cli_with_deadline`;
`supervised_worker.py` is the already-extracted child-side gate-wait
bootstrap. Both `job_manager.py` and `service_runtime.py` import `rag`
(circular-ish dependency, retained and documented in this milestone).

Coupling into the rest of `rag.py` is limited to module constants
(`_SUPERVISED_*`, `_RUN_ID_ENV`) and telemetry hooks. The vector-lease
machinery is not involved.

## 3. Module boundary

New top-level `process_supervision.py` (flat repo layout), stdlib-only.

Owns:

- Windows kill-job containment (`_WindowsKillJob`, handle management)
- POSIX/Windows start gates and gate-release logic
- `_terminate_supervised_process` (verified-handle termination)
- The deadline supervisor loop (`_run_cli_with_deadline`)
- The supervised entrypoint runner (`_run_rag_entrypoint`)

Stays in `rag.py`:

- CLI/argparse policy and storage/jobs command handlers
- Telemetry emission
- Concrete values of `_SUPERVISED_*` / `_RUN_ID_ENV` constants (passed in)
- All wiring between consumers and the new module

The exact function list is pinned in the implementation plan after the
baseline settles (see Sequencing).

## 4. Public API and collaborator seams

Same pattern as `retrieval_core.py` and `index_state.py` (PRs #15-#21):

- A frozen `SupervisionConfig` dataclass: env-var names (run ID, start
  gates), deadline and poll intervals.
- Callback protocols for mutable collaborators: `TelemetryFn`, `WarningFn`,
  and an injectable monotonic clock plus sleep function for deterministic
  tests. Defaults reproduce current behavior exactly.
- `rag.py` rebinds the old private names (for example
  `_run_cli_with_deadline = ...`) with rag's collaborators pre-bound, so
  `job_manager`'s injection of `rag._run_cli_with_deadline` and
  `service_runtime`'s call sites work unchanged.

## 5. Data flow (unchanged)

`job_manager` / `service_runtime` -> `rag` facade -> `process_supervision`
-> subprocess/OS APIs -> `supervised_worker` child. The supervisor-to-child
environment-variable contract is untouched.

## 6. Error handling (unchanged semantics)

Exception types raised by supervision move with the code; `rag.py`
re-exports their names. Timeout, kill, orphan-propagation, and
unconfirmed-cleanup semantics are preserved verbatim.

## 7. Testing and failure injection

- Phase 1 — characterization first: tests written against current `rag.py`
  behavior before any code moves: deadline expiry kills the process tree;
  job-object containment reaps grandchildren; start-gate timeout;
  terminating an already-exited child; handle verification across
  termination.
- Phase 2 — move and verify: the same characterization tests pass unchanged
  against the facade; new direct unit tests on `process_supervision` use the
  injected clock/spawner (no real sleeps).
- Injections: child ignores termination; child spawns a grandchild; kill-job
  creation failure; start gate never released. Windows-specific cases are
  platform-marked; CI stays Linux plus Windows.
- Full suite, static checks, and real-artifact smoke per the repository
  completion rule.

## 8. Acceptance evidence

Per the ROADMAP completion rule: failure-injected tests committed, full
suite green on both OSes, independent review recorded durably, published as
a mergeable draft PR, ROADMAP updated (P3 first slice).

## 9. Sequencing

Implementation is blocked until:

1. The locally validated Ethics-coherence milestone is committed, reviewed,
   and merged through the repository's normal process.
2. The in-flight Codex changes to the codebase land.

After both land, re-survey the supervision region (line ranges and function
inventory in section 2 will have drifted) and pin the exact extraction list
in the implementation plan.
