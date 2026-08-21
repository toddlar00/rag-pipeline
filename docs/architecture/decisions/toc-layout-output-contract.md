# TOC layout output contract

Status: technical implementation integrated through
[PR #76](https://github.com/toddlar00/rag-pipeline/pull/76); inherited R0C
semantic-rejection and provider-chain authority remains owner-pending

Date: 2026-07-25

Decision owner: pending assignment for the R0C semantic-rejection and
provider-chain policy in issue #48

Technical owner: pending assignment

## Context

The opt-in `--llm-scaffold` path first asks `toc.layout` to describe patterns
in a bounded sample of a document's Table of Contents. Its result is not
published directly, but selected values become untrusted hints in the later
`toc.scaffold` hierarchy request. Before this technical slice, the layout helper
interpolated raw source lines into its prompt, removed thinking tags, selected
the text between the first and last braces, and decoded it with ordinary
`json.loads`. Missing and extra fields, duplicate keys, coercive types,
wrappers, non-finite values, and unbounded strings could therefore gain cache
and hint authority before the strict hierarchy boundary was reached.

The helper also logged model-generated hierarchy labels, division examples,
and section markers. Those strings can reproduce private source material or
contain hostile log formatting. The requested `other_elements` field was never
consumed at all.

Layout hints can change the generated hierarchy, section paths, stable chunk
identities, retrieval, and output reuse. A rejected response must not appear in
exceptions, runtime receipts, completion provenance, or ordinary logs.

## Integrated mechanics and pending policy decision

This technical implementation is present on `main` through PR #76. Integration
does not approve the unresolved R0C policy. The implementation extends the
dependency-light authority in
[`llm-output-contracts.md`](llm-output-contracts.md) to one response family:
`toc.layout`. It is stacked on the integrated
[`toc-hierarchy-v1`](toc-hierarchy-output-contract.md) decision.

The predecessor rule that a non-empty semantic rejection stops the provider
chain is inherited unchanged in code. That release-authority choice remains
pending owner approval; the merged mechanics do not settle it.

### Exact `toc-layout-v1` value

The response must contain exactly one JSON object apart from outer ASCII space,
tab, carriage return, or newline. It has exactly these eight fields:

```json
{
  "division_examples": ["Part I"],
  "division_pattern": "Part followed by a Roman numeral",
  "hierarchy_levels": {
    "1": "Primary division",
    "2": "Section",
    "3": "Subsection",
    "4": "Named item",
    "5": ""
  },
  "hierarchy_order": ["Primary division", "Section", "Subsection", "Named item"],
  "named_item_format": "Indented case or problem title",
  "page_number_format": "Trailing Arabic or Roman number",
  "section_markers": ["A.", "B."],
  "subsection_markers": ["1.", "a."]
}
```

- `page_number_format`, `division_pattern`, and `named_item_format` are strings
  and may be empty when the pattern was not observed.
- `division_examples`, `section_markers`, and `subsection_markers` are arrays
  with zero through 16 non-empty string items.
- `hierarchy_order` is an array with zero through five non-empty string items.
- `hierarchy_levels` has exactly the string keys `"1"` through `"5"`. Each
  description is a string and may be empty, so the exact shape does not require
  the model to invent absent levels.
- Every string is trimmed only of outer U+0020 spaces and limited to 512
  characters and 512 UTF-8 bytes. Array ordering and duplicates are preserved;
  no semantic normalization is attempted.
- Strings reject surrogates, Unicode control/format/private-use/unassigned
  categories, line and paragraph separators, and whitespace other than U+0020.
  Unicode normalization is intentionally absent, so NFC and NFD values remain
  distinct.

The `other_elements` field is intentionally absent. It had no downstream
consumer, so accepting it would grant unused generated text authority.

The raw response is limited to 64 KiB of valid UTF-8 and structural depth 2.
JSON integer tokens are limited to seven digits before conversion even though
no numeric value is legal in this schema. The strict decoder rejects duplicate
decoded keys, malformed or concatenated values, prose, Markdown fences,
thinking-tag wrappers, non-standard or non-finite numbers, and unexpected
fields. One invalid member rejects the complete object; individual hints are
never salvaged.

The maximum schema-valid object, including quote/backslash-heavy values at all
limits, fits under the same 64 KiB ceiling. Accepted output is serialized as
compact UTF-8 JSON with lexically sorted object keys, no non-ASCII escaping,
and no non-finite values, then checked against that ceiling again.

Unicode category behavior comes from the running CPython interpreter. Its
exact `major.minor.patch` Unicode data revision is recorded in contract and
chunk-completion provenance and placed in trusted `CONTRACT_JSON` in the
production prompt. Request hashes, cache keys, and shared-flight identities
therefore differ when supported interpreters use different character
semantics. Other schema changes require a new contract ID.

### Hostile prompt framing

Prompt version 2 places at most the first 120 source TOC lines in `SOURCE_JSON`
as one ASCII JSON line. Source strings are labeled as untrusted documentary
data, never instructions. The existing input policy enforces:

- at most 512 characters and 2,048 encoded JSON bytes per source line;
- preservation of the final 128 characters when a long line is shortened, to
  retain a likely trailing page number; and
- at most 256 KiB for the complete source JSON value.

The prompt also contains one trusted `CONTRACT_JSON` line with the contract ID
and exact Unicode data version. It explicitly requires the eight-field object,
empty values for unobserved patterns, the string/list bounds, and no wrappers.
The reviewed structure-profile description remains trusted code and is already
bound by the existing structure-profile provenance.

The request binds operation `toc.layout`, prompt version 2, a 1,200-token
output ceiling, a 30-second timeout, `toc-layout-v1`, and fallback
`use-no-generated-toc-layout-hints`. The shared runtime validates live, cached,
and same-key shared results before use or persistence. The helper locally
revalidates returned text as defense against a monkeypatched or bypassed
runtime path.

### Fallback and failure behavior

In best-effort mode, a missing or rejected response returns `{}`. The later
hierarchy request proceeds with reviewed profile guidance and bounded source
text but no generated layout hints; the deterministic table/scaffold fallback
remains available. Strict semantic rejection raises the existing structured
`LLMExecutionError`. `LLMBudgetExceeded`, release-security or endpoint-policy
failures, configuration errors, and unexpected exceptions raised at the
helper/runtime boundary also propagate and are never mislabeled as a successful
deterministic fallback. Provider-callback failures retain the shared runtime's
structured transport-error handling.

The permissive thinking-tag removal, brace slicing, partial interpretation,
and generated-value logs are removed. Success logs contain counts only, and
rejection logs contain fixed text only.

### Completion identity and migration

When `--llm-scaffold` is enabled,
`toc_scaffold_generation.layout_analysis` binds the layout prompt and input
policy versions, source framing bounds, output-token and timeout settings, and
the complete contract/fallback provenance. A change invalidates chunk
completion reuse. The field is absent when enrichment is disabled, so
deterministic-only completion identity does not drift.

Existing uncontracted layout cache records become misses because the prompt,
prompt version, contract ID, and fallback ID change; no cache-schema migration
is required. Existing LLM-enriched completions must be regenerated and their
indexes fully rebuilt because earlier permissive hints may have changed
hierarchy, section paths, and stable chunk IDs. Operational rollback is to
disable `--llm-scaffold`, regenerate artifacts, and fully reindex. Code rollback
requires reverting this slice and fully reindexing any corpus produced under
it.

## Consequences and limits

- Schema-valid layout text can still be false or ungrounded and can influence
  the hierarchy prompt. Exact syntax is not semantic correctness or
  prompt-injection immunity.
- `division_pattern` is documentary description only. It is never compiled or
  executed as a regular expression.
- The first-120-lines sample can omit patterns that appear later.
- Schema-valid strings may contain Markdown- or HTML-looking punctuation.
  Rendering safety remains a separate UI boundary.
- Only synthetic fixtures and hostile canaries are used for verification. No
  private `Ethics` text, output, or corpus-derived judgment is embedded in the
  repository.
- The later page spot-check is addressed by the integrated
  [TOC page-verification output contract](toc-verification-output-contract.md).
  Every `agent_team.*` response remains permissive and outside this decision.
  Full `--llm-scaffold` generation identity is therefore still incomplete;
  changes to those operations require an explicit full reindex.
- Grounded answers, generated context, reconstructed headings, numeric scores,
  briefs, questions, flashcards, and summaries still need their own reviewed
  response contracts.
- Provider/feature/data-class scoped egress, transfer and cost preflight,
  model-code isolation, and the owner-pending semantic-rejection rule remain
  separate release gates. This integrated technical slice is related to, but
  does not close,
  R0C issue #48.
- This slice does not change the chunk schema, index schema, global runtime
  receipt format, release-security policy, or deterministic scaffold behavior.

## Verification

Synthetic tests cover exact fields and types; empty values; item, depth,
response, character, and UTF-8 bounds; hostile Unicode; wrappers, duplicate
keys, malformed and non-finite JSON; canonical idempotence; response-free
diagnostics; live/cache runtime enforcement; best-effort and strict outcomes;
bounded one-line source framing; local revalidation; content-free logging;
layout-hint forwarding; deterministic fallback; and conditional completion
provenance. The existing architecture, CI-security, dependency, model-artifact,
and cross-platform Phase A0 gates continue to apply.
