# LLM output contracts

Status: technical implementation integrated through
[PR #76](https://github.com/toddlar00/rag-pipeline/pull/76); R0C
semantic-rejection and provider-chain authority remains owner-pending

Date: 2026-07-25

## Context

Provider adapters validate HTTP framing, media type, JSON structure, byte
ceilings, and typed transport metadata. Those checks establish that a provider
returned text; they do not establish that the text is safe to use as an
operation-specific decision. Chunk classification formerly searched the model
response for a known label after removing thinking tags. Explanations,
negations, multiple labels, Markdown, JSON, and injected instructions could
therefore acquire classification authority through a matching substring. The
runtime could also cache that response before `rag.py` interpreted it.

This tool processes private legal material. Rejected model text must not leak
through exceptions, events, reports, or per-chunk metadata, and a cache or
single-flight optimization must not bypass the same decision boundary.

## Integrated mechanics and pending policy decision

The implementation and tests below are present on `main` through PR #76. Their
integration does not settle the separate R0C support policy. In particular,
whether a non-empty semantic rejection must stop the provider chain as a
release-qualified authority rule remains subject to owner approval. Until that
decision, the integrated behavior remains fail-closed but unqualified for a
release support claim.

### Dependency-light contract authority

`llm_output_contracts.py` owns generated-text contracts without importing
`rag.py`, provider SDKs, HTTP clients, vector databases, or ML libraries. An
output contract has a bounded safe identifier, a reviewed set of canonical
values, and a raw-response byte ceiling. Its exception retains only a stable
diagnostic code.

The classification contract is `chunk-classification-v2`. It accepts exactly
one of the seven reviewed ASCII labels after trimming only outer ASCII space,
tab, carriage return, or newline and applying ASCII case-insensitive matching.
It rejects blank values, malformed UTF-8 strings, responses above 128 UTF-8
bytes, embedded control or format characters, non-ASCII confusables,
explanations, structured wrappers, and label substrings. Unicode compatibility
normalization is intentionally absent. The returned value is the declared
canonical lowercase label.

Contract and fallback identifiers are 1-128 character lowercase safe
identifiers. A validator that declares `contract_id` must exactly match the
request. The reviewed runtime path requires that declaration, so a caller
cannot accidentally bind a differently versioned validator to a cache key.

### Untrusted prompt framing

Chunk text and headings are data, never instructions. The classification prompt
labels them as untrusted and places them in one ASCII JSON line. It includes at
most eight headings of 160 characters each and the first 600 text characters;
JSON escaping prevents source newlines, quotes, controls, or bidirectional
formatting from altering the prompt structure. The response budget is 16
tokens, and the prompt version is 2. Disabled deterministic classification
retains prompt version 1 in chunk-completion provenance, so inactive behavior
does not create needless completion drift.

### Validate before authority or persistence

`LLMRuntime` validates non-empty generated text before marking a provider
attempt successful, writing a response cache, publishing an event, or returning
text to the caller. Accepted output is canonicalized first. Cache hits must
already contain the canonical value and are revalidated. Same-key
single-flight waiters independently revalidate successful shared output with
their request contract.

An empty or whitespace-only provider response remains the pre-existing
`empty_response` transport/content failure and may proceed through the ordered
provider chain. A non-empty semantic rejection is `invalid_response` and stops
the chain: another model is not silently granted authority to reinterpret the
same operation. This is an explicit authority decision, not a retry policy.

### Failure and fallback semantics

In best-effort mode, a rejected or unevaluated classification result is an
ordinary failed `LLMResult`; `rag.py` returns `None` and preserves the existing
deterministic `content_type`. Strict mode raises the existing
`LLMExecutionError`. Budget exhaustion continues to raise
`LLMBudgetExceeded` in every mode and is not recorded as a deterministic
fallback. No contract receipt is added to chunk metadata, stable IDs, or chunk
hashes.

### Content-free receipts and schemas

Every contracted terminal result carries its contract ID, fallback ID, and one
of `accepted`, `rejected`, or `not_evaluated`; its request/event supplies the
operation used for aggregate grouping. Rejection receipts may carry only a
stable code such as `llm-output-label-mismatch`,
`llm-output-byte-limit`, or `llm-output-contract-internal-error`. Events and
reports never contain rejected response text or validator exception messages.
Reports aggregate status, diagnostic, and selected-fallback counts by operation
and contract.

The migration uses cache-key schema 3, cache-record schema 4, event schema 4,
and report schema 5. Runtime cache reads accept only the current record schema;
older records are cache misses and require a successful live call to repair.
Dry-run retention recognizes owned cache schemas 1-4 so supported deletion does
not strand plaintext responses. Evaluation usage parsing recognizes compatible
report schemas 2-5 while new runtime reports write schema 5.

## Consequences

- Classification text cannot gain authority through substring matching,
  compatibility case folding, a corrupted cache, or a shared-flight shortcut.
- JSON framing and exact validation do not prove semantic correctness. Source
  text could still persuade a model to emit an allowed but wrong label;
  adversarial classification-accuracy evaluation remains follow-up work.
- A semantic rejection uses the deterministic feature fallback instead of
  silently trying another provider. Operators selecting strict mode receive a
  structured failure.
- Cache and report consumers move with the writer schema, preserving deletion
  and evaluation workflows.
- Classification prompt/completion provenance changes only when the opt-in
  classifier is active, and the global chunking and index schemas do not
  change. The runtime-wide cache-key and record schema migration deliberately
  changes request IDs and invalidates older cache records for every LLM
  operation; this avoids mixed-schema interpretation during review.
- The shared `toc.scaffold`/`toc.parse` hierarchy array is addressed by the
  integrated companion
  [TOC hierarchy output contract](toc-hierarchy-output-contract.md). Its
  upstream hint object is addressed separately by the integrated
  [TOC layout output contract](toc-layout-output-contract.md), and its later
  page spot-check by the integrated
  [TOC page-verification output contract](toc-verification-output-contract.md).
  None broadens this classification decision: every `agent_team.*` response,
  grounded-answer structure, case briefs, questions, flashcards, summaries,
  context generation, heading reconstruction, and numeric quality scores still
  need reviewed contracts. Scoped egress and provider model-code qualification
  also remain separate roadmap work; none of these slices completes R0C.

## Verification

Tests cover every allowed label; byte, encoding, control, confusable, wrapper,
and injection rejection; response-free exceptions; declaration validation;
live/cache/shared enforcement; semantic versus empty-response provider flow;
strict and best-effort behavior; budget accounting; secret-free receipts;
stable prompt bounds; conditional prompt provenance; current-schema retention;
and current-runtime evaluation parsing. Security ownership places the new
module under the provider-runtime workflow trigger, and architecture inventory
records its dependency-light import direction.
