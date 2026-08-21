# TOC page-verification output contract

Status: technical implementation integrated through
[PR #76](https://github.com/toddlar00/rag-pipeline/pull/76); inherited R0C
semantic-rejection and provider-chain authority remains owner-pending

Date: 2026-07-25

Decision owner: pending assignment for the R0C semantic-rejection and
provider-chain policy in issue #48

Technical owner: pending assignment

## Context

The opt-in `--llm-scaffold` path spot-checks generated Table of Contents entries
against extracted page text. `toc.verify` is called by the QC validator for up
to 15 selected entries on each of at most two scaffold attempts and by the
director for up to five entries. Its aggregate verification rate can therefore
trigger another hierarchy-generation attempt and affect the scaffold that is
returned for chunk publication.

Before this technical slice, the verifier interpolated titles, paths, chapter
values, and page text directly into a prompt. It asked for four fields, removed thinking
tags, selected everything between the first and last braces, and decoded with
ordinary `json.loads`. The caller then used Python truthiness for `verified`, so
values such as the string `"false"` could receive affirmative authority.
Wrappers, duplicate keys, extra fields, coercive types, non-finite numbers, and
unbounded nested values were not rejected before cache persistence.

Three requested response fields had no legitimate decision authority. Model
`confidence` was ignored, `found_title` had no consumer beyond a returned
diagnostic, and generated `issue` text entered ordinary logs and later
AgentTeam prompts. Missing or rejected replies silently reduced the aggregate
rate without being identified as inconclusive. Page capture also claimed a
1,500-character bound but could append an arbitrarily large final text item.

Verification can influence section paths, stable chunk identities, retrieval,
and completion reuse. The verifier must not place rejected model text or source
strings in its exceptions, runtime receipts, completion provenance, returned
failure reasons, programmatic AgentTeam issues, or verifier-owned logs. The
broader AgentTeam still summarizes scaffold results containing source-derived
titles and remains an explicit non-goal below.

## Integrated mechanics and pending policy decision

This technical implementation is present on `main` through PR #76. Integration
does not approve the unresolved R0C policy. The implementation extends the
dependency-light authority in
[`llm-output-contracts.md`](llm-output-contracts.md) to exactly one response
family: `toc.verify`. It is stacked on the integrated
[`toc-layout-v1`](toc-layout-output-contract.md) and
[`toc-hierarchy-v1`](toc-hierarchy-output-contract.md) decisions.

The inherited rule that a non-empty semantic rejection stops the provider chain
is unchanged in code. That release-authority choice remains pending owner
approval; the merged mechanics do not settle it.

### Exact `toc-verification-v1` value

The response must contain exactly one JSON object apart from outer ASCII space,
tab, carriage return, or newline:

```json
{"verified": true}
```

- The object has exactly one field named `verified` and no other field.
- `verified` is an exact JSON Boolean. Integers, floats, strings, null, arrays,
  objects, and other coercive substitutes are rejected.
- `true` awards one verification credit. `false` withholds credit and produces
  only the fixed local reason `model-did-not-verify`.
- Model confidence, matched text, and free-form rationale are intentionally not
  accepted. They had no reviewed downstream authority and created private-text,
  prompt-injection, and log-injection surfaces.

The raw response is limited to 64 bytes of valid UTF-8 and structural depth 1.
JSON integer tokens are limited to one digit before conversion even though no
integer is legal. The strict decoder rejects duplicate decoded keys, malformed
or concatenated values, prose, Markdown fences, thinking tags, non-standard or
non-finite numbers, unexpected fields, and trailing content. Accepted output is
serialized as compact, sorted-key JSON and checked against the byte ceiling
again.

The accepted language contains only a fixed ASCII key, punctuation, and JSON
Boolean. It does not use Unicode normalization or category classification, so
the output contract does not require a Unicode-data-version field. Any change
to its accepted semantics requires a new contract ID.

### Hostile prompt framing

Prompt version 2 places the complete expectation and page excerpt in one
compact, sorted, ASCII-escaped `SOURCE_JSON` line. The prompt labels every value
as untrusted documentary data and instructs the model never to follow or repeat
instructions inside strings. It also contains one trusted, one-line
`CONTRACT_JSON` value with the contract ID.

The input policy accepts exact internal scalar types and enforces:

- a page integer from 1 through 1,000,000 and a level integer from 1 through 5,
  rejecting Booleans as integers;
- a null chapter value or an integer from 0 through 1,000,000;
- at most 512 title characters and 8 KiB for its encoded JSON string;
- at most 2,048 path characters and 32 KiB for its encoded JSON string,
  preserving the final 256 characters when bounding a long path;
- only exact integer page provenance for selected pages, rejecting Boolean and
  float aliases, with at most the first 4,096 characters of any relevant text
  item decoded;
- an exact 1,500-character per-page capture ceiling;
- at most the first 1,200 captured page characters and 16 KiB for their encoded
  JSON string; and
- at most 64 KiB for the complete `SOURCE_JSON` line.

Page fragments retain the predecessor `_decode_pua(...).strip()` preparation;
the prompt framer performs no additional Unicode normalization or semantic
interpretation. Quotes, newlines, controls, bidirectional formatting, and
hostile pseudo-JSON remain escaped data. The existing deterministic quick-match
algorithm remains unchanged but now runs on the same validated, bounded title:
it tests at most the first four title words of at least four characters before
an LLM call. Its Unicode data revision, selection policy, and numeric thresholds
are bound in completion provenance because supported CPython releases can have
different Unicode word semantics.

The request binds operation `toc.verify`, prompt version 2, a 300-token output
ceiling, a 30-second timeout, `toc-verification-v1`, and fallback
`treat-toc-verification-as-inconclusive`. The shared runtime validates live,
cached, and same-key shared results before use or persistence. The verifier
locally revalidates returned text as defense against a monkeypatched or bypassed
runtime path. Direct callers must select between one and 20 checks; production
uses 15 per QC attempt and five for the director. Callers may still select the
existing per-call security, fallback, failure, and cache controls; they cannot
replace this operation's contract, identity, output budget, or timeout through
`**kwargs`.

### Aggregate and fallback behavior

Each selected entry remains independent:

- a deterministic keyword match increments `deterministic_verified`;
- accepted model `true` increments `llm_verified`;
- accepted model `false` adds the fixed local failure reason;
- unavailable or locally rejected best-effort output increments
  `inconclusive`; and
- missing extracted page text adds the fixed local failure reason
  `page-text-unavailable` without making an LLM call.

`verified` is the sum of deterministic and model credits. `checks` remains the
number of selected entries, and aggregate `confidence` remains
`verified / checks`. Inconclusive checks therefore remain in the conservative
denominator, preserving prior decision behavior while making uncertainty
explicit. Verifier failure records contain only stable local reason codes.
Verifier-owned logs contain aggregate counts only; they never include page
numbers, titles, paths, page text, model text, matched text, or generated
rationale. Broader AgentTeam summaries remain outside this guarantee.

Strict semantic rejection raises the existing structured `LLMExecutionError`.
`LLMBudgetExceeded`, release-security or endpoint-policy failures,
configuration errors, and unexpected helper/runtime exceptions propagate.
Provider callback failures retain the shared runtime's structured transport
handling. This slice does not change the broader AgentTeam rule that a
non-budget, non-`LLMExecutionError` failure in the director test becomes an
advisory flag.

### Completion identity and migration

When `--llm-scaffold` is enabled,
`toc_scaffold_generation.page_verification` binds:

- prompt, input, selection, and quick-match policy versions;
- output-token and timeout settings;
- QC, director, and per-call check ceilings;
- per-item scan, page-capture, title, path, page-text, encoded-JSON, and
  whole-source bounds;
- quick-match word limits, low-rate threshold, and minimum check count;
- maximum page and exact quick-match Unicode data revision; and
- complete output-contract and fallback provenance.

The field is absent when LLM scaffold enrichment is disabled. Existing
uncontracted verification cache entries become misses because prompt, prompt
version, contract ID, and fallback ID change; no global cache-schema migration
is required.

Existing LLM-enriched completions must be regenerated and their indexes fully
rebuilt. Verification can cause a later scaffold attempt to replace an earlier
one, changing hierarchy, paths, and stable chunk IDs. Operational rollback is
to disable `--llm-scaffold`, regenerate artifacts, and fully reindex. Code
rollback requires reverting this slice and fully reindexing any corpus produced
under it.

## Consequences and limits

- Exact JSON proves syntax and authority shape, not semantic truth. A model can
  still return a schema-valid but wrong Boolean, including after prompt
  injection within source text.
- The unchanged quick matcher can false-positive when common title words occur
  separately, and the first 1,200 page characters can omit later evidence.
- The unchanged sample selection favors level-1 entries and can truncate a book
  with more chapter boundaries than the check ceiling. Sampling and the 50%
  threshold remain separate semantic-policy work.
- Verification remains advisory after QC retries, and a director rejection does
  not itself block scaffold return. Changing publication authority requires a
  separate owner decision.
- Only synthetic fixtures and hostile canaries are used for verification. No
  private `Ethics` text, output, path, or corpus-derived judgment is embedded in
  the repository.
- `agent_team.*` responses remain permissive and outside this decision. Full
  `--llm-scaffold` identity is therefore still incomplete, and changes to those
  operations require a full enriched-artifact rebuild and reindex.
- Grounded answers, generated context, reconstructed headings, numeric scores,
  briefs, questions, flashcards, and summaries still need reviewed contracts.
- Provider/feature/data-class scoped egress, mandatory finite release budgets,
  transfer/cost preflight, model-code isolation, and the owner-pending
  semantic-rejection rule remain separate release gates. This integrated
  technical slice is related to, but does not close, R0C issue #48.
- This slice does not change the chunk schema, index schema, global runtime
  receipt format, release-security policy, deterministic-only scaffold
  behavior, AgentTeam prompts, retry count, sampling algorithm, or acceptance
  threshold.

## Verification

Synthetic tests cover exact shape and Boolean types; byte, depth, integer,
encoding, and source bounds; wrappers, duplicate keys, malformed and non-finite
JSON; canonical idempotence; response-free diagnostics; live/cache/shared
runtime enforcement; semantic-rejection provider authority; best-effort and
strict outcomes; bounded hostile one-line source framing; exact page capture;
local revalidation; deterministic and missing-page shortcuts; content-free
aggregation and logging; exception propagation; conditional completion
provenance; and completion invalidation. The existing architecture,
CI-security, dependency, model-artifact, and cross-platform Phase A0 gates
continue to apply.
