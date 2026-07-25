# Table-family evaluation semantics

- Status: Implementation included in cumulative draft PR #44; frozen-head
  technical gates passed; not merged or human-reviewed
- Date: 2026-07-24
- Milestone: R4

## Context

Row-level table children improve retrieval for questions answered by one row,
but existing relevance judgments identify canonical parent chunks. Treating a
retrieved child as an unrelated ID creates false misses. Treating every child as
equivalent to its parent is worse: the first irrelevant sibling could earn full
credit, and several siblings could inflate finite-qrel metrics.

Backend result metadata is not sufficient evidence of equivalence. It may be
stale or malformed, and family lineage establishes ancestry rather than
query-specific relevance.

## Decision

A positive parent `chunk_id` judgment may contain this optional contract:

```json
{
  "chunk_id": "chunk_parent",
  "relevance": 3,
  "table_family": {
    "schema_version": 1,
    "accepted_child_chunk_ids": ["chunk_answer_row"]
  }
}
```

The accepted-child list is exhaustive for that query and parent judgment. It is
sorted, unique, non-empty, bounded by the table-family child cap, corpus-pinned,
and included in corpus-owner review. Family aliases are not allowed on source-ID
or zero-relevance judgments.

Before scoring, the evaluator recomputes the complete parent/child contract from
the exact chunks artifact. The canonical parent must be an attested table parent,
and every accepted child must be an attested child of that parent. Retrieved
payload claims never create equivalence.

The CLI and direct Python API share that boundary. Direct callers receive a
sealed immutable `TableFamilyAttestation` only from the corpus-derived factory;
raw membership mappings are not an accepted evaluator input. The attestation
binds the corpus SHA-256, record count, and stable-ID scheme declared by every
family-bearing query.

The scorer maps the parent and its explicitly accepted children to one logical
gold key. The first matching result receives the parent grade; later parent,
accepted-child, or duplicate hits receive no additional credit. Unlisted
siblings and unrelated tables receive zero credit.

Detailed results record the canonical judgment ID, the retrieved ID that
satisfied it, and whether the match was `exact` or `accepted_table_child`.
Summary reports hash both identities while retaining the match kind. This
retrieval-relevance alias is deliberately not reused by claim-level grounding;
grounding evidence must still name the exact model-visible source ID.

## Version and review boundaries

- Retrieval reports advance to schema 6.
- Judgment scoring advances to version 2.
- The table-family judgment schema starts at version 1.
- Review packets advance to schema 2; content-free review receipts remain schema
  1 because they already bind the exact query and packet bytes.
- Release policies advance to schema 2 and bind report, grounding, judgment,
  table-generation, and family-collapse versions.
- Strict baselines also bind context budgets, table policy, and the exact indexed
  table-child count. Older reports and policies fail compatibility checks.

## Consequences

Table-enabled and table-disabled corpora require separately pinned query bytes
when their stable IDs differ. Existing owner approvals are never transferred to
new child aliases automatically. Historical schema-5 diagnostics remain useful
as non-gating evidence, but they are not comparable release baselines.

A portable CC0 suite exercises two real generated table families through the
public BM25 CLI path. Its baseline uses both lower and upper bounds for sibling
and unrelated-family hard-negative slices: blanket aliasing is a regression
even though it would superficially increase ordinary ranking metrics. Owner
review packet preparation also checks its exact serialized size before the
atomic write, so a packet cannot be created that the strict parser must reject
as oversized.

## Rejected alternatives

- **Alias every child to its parent.** This credits irrelevant siblings.
- **Trust result metadata at scoring time.** This permits stale or spoofed
  lineage to affect metrics.
- **Accept a caller-supplied family dictionary.** This gives direct callers an
  apparently attested input without binding it to corpus bytes or ID policy.
- **Count each accepted child as a separate qrel.** This inflates denominators
  and rewards duplicate evidence from one logical source.
- **Silently reuse schema-5 baselines.** This compares different scorer semantics
  under one provenance contract.
