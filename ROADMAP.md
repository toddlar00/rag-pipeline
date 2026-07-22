# Improvement Roadmap

This roadmap was reconstructed on 2026-07-21 from the repository, tests, and
published pull requests after the original chat brainstorm was not preserved as
a durable artifact. It records the recoverable milestones and turns the
remaining improvement themes into an explicit, ordered backlog.

Status terms:

- **Baseline** — present in the initial private-repository snapshot on `main`.
- **Implemented (draft)** — coded, validated, pushed, and opened as a draft PR,
  but not yet merged into `main`.
- **In progress** — active branch; not yet published as a PR.
- **Planned** — scoped direction, not yet implemented.

## Current status

| Milestone | Status | Durable result |
|---|---|---|
| Foundation | Baseline | End-to-end PDF ingestion, enriched chunking, shared LLM runtime, grounded answers, hybrid retrieval/reranking, evaluation harness, Chroma/Qdrant indexing, CLI, UI, docs, and tests |
| Exact LLM transport budget | Implemented (draft) | [PR #1](https://github.com/toddlar00/rag-pipeline/pull/1) |
| Qdrant manifest reconciliation | Implemented (draft) | [PR #2](https://github.com/toddlar00/rag-pipeline/pull/2) |
| Qdrant interrupted-update guard | Implemented (draft) | [PR #3](https://github.com/toddlar00/rag-pipeline/pull/3) |
| Bounded Qdrant integrity scans | Implemented (draft) | [PR #4](https://github.com/toddlar00/rag-pipeline/pull/4) |
| Qdrant mutation-status validation | Implemented (draft) | [PR #5](https://github.com/toddlar00/rag-pipeline/pull/5) |
| Qdrant worker lifecycle cleanup | Implemented (draft) | [PR #6](https://github.com/toddlar00/rag-pipeline/pull/6) |
| Chroma worker lifecycle cleanup | Implemented (draft) | [PR #7](https://github.com/toddlar00/rag-pipeline/pull/7) |
| Chroma interrupted-update guard | Implemented (draft) | [PR #8](https://github.com/toddlar00/rag-pipeline/pull/8) |
| Chroma parallel/API lifecycle cleanup | Implemented (draft) | [PR #9](https://github.com/toddlar00/rag-pipeline/pull/9) |
| Chroma record reconciliation and no-op detection | Implemented (draft) | [PR #10](https://github.com/toddlar00/rag-pipeline/pull/10) |
| Deterministic vector-client lifecycle | Implemented (draft) | [PR #11](https://github.com/toddlar00/rag-pipeline/pull/11) |
| Cross-process vector-store concurrency | Implemented (draft) | [PR #12](https://github.com/toddlar00/rag-pipeline/pull/12) |
| Hard operation deadlines and crash-safe publication | Implemented (draft) | [PR #13](https://github.com/toddlar00/rag-pipeline/pull/13) |

PR #1 is independent of the index-integrity stack and can be reviewed or merged
separately. The index-integrity stack must be reviewed and merged in order:
**#2 -> #3 -> #4 -> #5 -> #6 -> #7 -> #8 -> #9 -> #10 -> #11 -> #12 -> #13**.
Until those PRs merge,
implementation progress is ahead of integration progress.

## Ordered next milestones

### 1. Integrate and automate quality gates

- Add GitHub Actions for Ruff, bytecode compilation, the full unit suite, and
  optional real Chroma/Qdrant local-mode smoke tests.
- Test the supported Python-version matrix and both minimal/core and optional
  dependency sets.
- Add dependency update, vulnerability, and license checks with deliberate
  version constraints or lockfiles for reproducible environments.
- Merge the current draft stack in dependency order after review.

### 2. Reduce monolith and coupling risk

- Split `rag.py` into focused ingestion, chunking, indexing, retrieval, LLM,
  artifact, and CLI modules while preserving the public command surface.
- Replace loosely shaped dictionaries at module boundaries with typed records
  and explicit backend/provider protocols.
- Move shared lifecycle and transaction logic behind small tested abstractions,
  keeping backend-specific payload and validation rules local.

### 3. Expand retrieval evaluation

- Build corpus-pinned judged sets for additional subjects and books rather than
  treating the Civil Procedure calibration as universal.
- Add adversarial citation, abstention, filter, and long-context cases.
- Run threshold and baseline-regression checks in CI and retain machine-readable
  evaluation artifacts for comparisons.
- Measure latency, memory, index size, and LLM/embedding cost alongside relevance.

### 4. Improve operations, privacy, and product surfaces

- Emit structured stage/index/LLM metrics with run IDs and actionable failure
  diagnostics.
- Add cancellation and resumable background jobs for the UI and long-running
  indexing/generation commands.
- Harden cache and artifact permissions for sensitive source text and model
  output; document retention and deletion workflows.
- Add a stable service/API layer only after core modules and lifecycle contracts
  are separated from the CLI.

## Completion rule

A milestone is complete only when its behavior is implemented, failure-injected,
covered by the full test suite and static checks, exercised against the relevant
real optional client where practical, independently reviewed, and published as a
mergeable draft PR. It becomes integrated only after merge into `main`.
