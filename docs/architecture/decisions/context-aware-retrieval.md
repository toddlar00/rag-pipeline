# Context-aware retrieval assembly

- **Status:** Accepted; merged to `main` on 2026-08-01 through the
  history-preserving integration of
  [#44](https://github.com/toddlar00/rag-pipeline/pull/44)
- **Implementation:** PR #36 (`agent/context-aware-retrieval`)
- **Historical input:** `docs/superpowers/plans/2026-07-23-codex-improvements-structure-profiles.md`

## Context

A ranked chunk may omit the immediately preceding definition or following
qualification. Adding neighbors can improve evidence completeness, but it must
not change primary ranking, cross source or filter boundaries, create duplicate
evidence, or make one citation appear to support text from another chunk. The
historical Claude plan proposed supplementary neighbors while leaving citations
pointed at the ranked hit; the implemented contract intentionally strengthens
that identity boundary.

## Decision

Canonical chunks receive stable previous/next linkage only within the same
explicit source-and-chapter parent and final published order. Context assembly
is opt-in and occurs after primary retrieval. It preserves ranked hits, applies
the active filters, verifies the exact chunks/index generation, collapses
equivalent evidence with provenance, and adds neighbors under total and
per-neighbor serialized-character budgets.

Every neighbor retains its own stable source identity and receives its own
citation. Supplementary text never borrows the ranked hit's citation. The same
assembly contract is used by Chroma, Qdrant, CLI, UI, grounded answers, and the
evaluation harness.

## Invariants

- Context cannot alter primary scores, order, or primary ranking metrics.
- Linkage never crosses a source, explicit chapter parent, or active filter.
- Missing, stale, asymmetric, or tampered linkage fails closed.
- Primary and supplementary evidence IDs are unique in one result.
- Prompt and returned-evidence budgets include serialized metadata as well as
  text; truncation preserves independent citation identity.
- Context configuration and generation identity are bound into evaluation and
  index compatibility evidence.

## Consequences

Callers can compare context windows without conflating a ranking change with an
evidence-assembly change. More evidence increases prompt size and latency, so
R4 still requires owner-reviewed 0/1/2-window ablations and grounded-answer
gates. The stronger citation rule differs deliberately from the historical plan
and prevents a ranked chunk from being presented as the source of neighboring
words.

## Rejected alternatives

- Re-ranking after neighbor insertion: obscures the primary retriever contract.
- Crossing chapters when a neighbor is nearby in file order: violates the
  semantic parent boundary.
- Pointing every neighbor at the primary citation: loses source identity and
  can overstate support.
- Unbounded context windows: make cost, latency, and prompt behavior unstable.
