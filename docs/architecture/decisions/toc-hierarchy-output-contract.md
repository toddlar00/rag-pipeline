# TOC hierarchy output contract

Status: proposed implementation for owner review

Date: 2026-07-25

## Context

The opt-in TOC enrichment path asks a model to turn source TOC lines into a
hierarchy. Both `toc.scaffold` and `toc.parse` consume the same array of
`level`, `title`, and `page` values. Previously, each helper removed thinking
tags, searched for the first and last square brackets, decoded whatever was
between them, coerced values, and retained individually usable entries. That
made prose wrappers, ambiguous JSON, coercive types, and partially invalid
batches eligible to influence document structure after the runtime had already
treated the provider response as successful.

TOC hierarchy affects chunk section paths and therefore retrieval and output
reuse. Model text and earlier model-generated layout hints are hostile input,
including when they originate from a configured provider or a local cache. A
contract rejection must not retain private source text or rejected response
text in exceptions, events, reports, or public provenance.

## Proposed decision

This implementation is a reviewable candidate, not an approved policy. It
extends the dependency-light contract authority in
[`llm-output-contracts.md`](llm-output-contracts.md) to one bounded response
family only: the hierarchy arrays returned by `toc.scaffold` and `toc.parse`.

The predecessor decision's rule that a non-empty semantic rejection stops the
provider chain is inherited unchanged. That authority choice remains pending
owner approval before either proposal may merge.

### Exact `toc-hierarchy-v1` value

The response must contain exactly one JSON value, apart from outer ASCII space,
tab, carriage return, or newline. The value must be an array with 1-100 items.
Every item must be an object with exactly these three fields and no others:

```json
{"level": 2, "page": 37, "title": "B. Personal Jurisdiction"}
```

- `level` is a JSON integer, not a Boolean, float, or string, from 1 through 5.
- `page` is a JSON integer, not a Boolean, float, or string, from 0 through
  1,000,000.
- JSON integer tokens are limited to seven digits before integer conversion,
  including on Python versions without a standard-library digit guard.
- `title` is a non-empty JSON string after trimming ordinary U+0020 spaces from
  its ends. It is limited to 512 characters and 512 UTF-8 bytes.
- Titles reject surrogates, Unicode control/format/private-use/unassigned
  categories, line and paragraph separators, and whitespace other than U+0020.
  Unicode normalization is intentionally absent, so visually similar strings
  do not become equal by compatibility or canonical normalization.

Those Unicode categories come from the running CPython interpreter's Unicode
database. Because supported CPython versions do not all ship the same database,
the exact `major.minor.patch` Unicode data version is included in contract and
chunk-completion provenance. It is also placed in trusted `CONTRACT_JSON` in
each production hierarchy prompt, so the prompt hash, runtime request ID,
cache key, and shared-flight identity differ when character semantics differ.
The stable `toc-hierarchy-v1` name identifies the schema family; its provenance
identifies the exact Unicode category revision.

The raw response is limited to 128 KiB of valid UTF-8 and structural JSON depth
2. The strict decoder rejects duplicate object keys, trailing or concatenated
values, non-standard and non-finite numbers, malformed syntax, prose, Markdown
fences, and thinking-tag wrappers. One invalid sibling rejects the entire
array.

An accepted value is serialized as compact UTF-8 JSON with lexically sorted
object keys, no non-ASCII escaping, and no non-finite numbers. The canonical
text is checked against the same byte ceiling. Live responses are canonicalized
before they gain authority or persistence; cache hits and same-key shared
results are revalidated by the runtime before use.

### Untrusted prompt framing

Both operations use prompt version 2 and place source TOC lines in `SOURCE_JSON`
as one ASCII JSON line. Quotes, backslashes, newlines, controls, and
bidirectional formatting in source text are JSON escapes or string data, not
prompt structure. A separate one-line `CONTRACT_JSON` binds the contract ID and
Unicode data version into the request. The framing policy is version 1 and
enforces:

- at most 80 source lines per `toc.scaffold` request and 100 per `toc.parse`
  request;
- at most 512 characters and 2,048 encoded JSON bytes per source line;
- a preserved 128-character tail, which retains the likely trailing page
  number when a long line is shortened; and
- at most 256 KiB for the complete source JSON value.

The scaffold operation also places at most 32 earlier layout hints in a
separate one-line `LAYOUT_JSON` value. Each hint is limited to 512 characters
and 2,048 encoded JSON bytes; the complete value is limited to 128 KiB.
Non-string/non-list values and non-string list members are omitted. Those hints
are labeled as untrusted generated data. This framing does not make the earlier
layout response authoritative or give it the exact hierarchy-array contract.
The separate `toc.layout` request still receives its own permissively framed
sample and remains outside this proposal on both its input and output sides.

### Atomic multi-batch fallback

Each batch is independently validated under `toc-hierarchy-v1`, but the
operation is atomic. A missing result or contract rejection in any batch
discards every hierarchy entry accumulated from earlier batches. The helper
returns no LLM hierarchy (`[]`), leaving the reviewed deterministic
table/column-position fallback or merge path in authority. It never publishes
a partially model-derived hierarchy. Budget, strict-mode, security-policy,
configuration, and unexpected programming exceptions continue to propagate;
they are not mislabeled as successful deterministic fallback selection.

The two operations use distinct content-free fallback identifiers:

- `toc.scaffold`: `use-deterministic-toc-scaffold`
- `toc.parse`: `use-deterministic-toc-parser`

In best-effort mode, semantic rejection is a failed LLM result and the helper
selects that deterministic behavior. In strict mode, the runtime raises the
existing structured `LLMExecutionError`; it does not silently downgrade to the
deterministic path. `LLMBudgetExceeded` continues to raise in every mode and is
not recorded as a deterministic fallback.

### Runtime and chunk provenance

Both runtime requests bind operation, prompt version, `toc-hierarchy-v1`, and
the operation-specific fallback ID into request/cache identity. Contract
receipts remain content-free and use the existing accepted, rejected, or
not-evaluated statuses and stable diagnostic codes.

When `--llm-scaffold` is enabled, chunk-completion parameters additionally bind
the hierarchy prompt and input-policy versions, output-token and timeout
settings, all source and layout JSON bounds, and the full hierarchy
contract/fallback provenance. Changing any of those values invalidates
completion reuse. The field is absent when LLM scaffold enrichment is disabled,
so deterministic-only completion identity does not drift. This slice does not
change the global chunking or index schema.

## Consequences and limits

- Wrapper extraction, value coercion, and per-item salvage can no longer grant
  document-structure authority to malformed model text.
- Exact syntax and shape validation do not prove that a valid hierarchy is
  semantically correct. A source line can still persuade a model to return a
  schema-valid but wrong level, title, or page; grounded hierarchy evaluation
  remains required.
- Schema-valid title strings may contain Markdown- or HTML-looking punctuation.
  Rendering and presentation safety remain separate UI-boundary work.
- Atomic fallback trades partial enrichment for deterministic authority or a
  fail-closed outcome, with a simpler provenance boundary.
- Only synthetic fixtures and hostile canaries are used for verification. No
  private `Ethics` text, output, or corpus-derived judgment is embedded in the
  repository.
- `toc.layout`, `toc.verify`, and every `agent_team.*` response remain
  permissive and outside this decision. Grounded answers, case briefs,
  questions, flashcards, summaries, contextual prefixes, reconstructed
  headings, and quality scores also still need reviewed contracts.
- Consequently, `toc_scaffold_generation` is hierarchy-step provenance, not a
  complete identity for every permissive operation used by `--llm-scaffold`.
  Until those remaining prompts and contracts are versioned, changes to them
  require an explicit full reindex instead of relying on completion reuse.
- Provider model-code qualification, scoped egress, and the owner-pending
  semantic-rejection provider-chain rule remain separate release gates. This
  proposal is related to, but does not close, R0C issue #48.
- `toc.parse` is a retained private helper with no current production call site;
  the live `--llm-scaffold` hierarchy path uses `toc.scaffold`.
- Refactoring the shared bounded-text helper also removes a predecessor
  classification edge case where a rejected surrogate retained its raw response
  through `UnicodeEncodeError.__context__`. Classification labels, request
  identity, and accepted/rejected semantics are otherwise unchanged.

## Verification

Synthetic tests cover strict JSON decoding; exact fields and integer types;
item, depth, response, title, level, and page bounds; hostile controls and
Unicode; canonical idempotence; response-free diagnostics; live/cache/shared
runtime validation; best-effort and strict outcomes; prompt framing; multi-batch
atomicity; deterministic fallback; and conditional chunk-completion
provenance. The existing architecture and CI-security ownership gates continue
to govern the dependency-light contract module.
