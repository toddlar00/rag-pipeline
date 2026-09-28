# Core cleanup fidelity audit

This opt-in, local audit answers a narrow question: which existing core
normalization rules would change exact selected fragments of saved OCR text?
It does not correct the source, accept a retry, regenerate OCR, or change an
index. The output is a private review artifact containing sensitive original
and transformed text. Keep it out of Git, logs and shared evaluation assets.

## What is implemented

`chunking_core._normalize_text` has an optional observer that receives actual
changed rule boundaries. Its default outputs, injected-helper behavior and
error behavior remain compatible. The standalone audit calls the default core
rules, with no user-supplied callbacks and no dynamic code in the plan.

The audit records typography replacements, safe zero-width handling, private-use
digit handling, editorial/section/decorative-line removal, URL repairs, spaced
hyphens, bracketed contractions, known fused terms, sentence/whitespace cleanup,
header/footer stripping and nearby-line deduplication. Unchanged rules do not
produce an edit. This is observation of the current rules, not evidence that
those rules are defective or safe in every context.

The pre-instrumentation output fingerprint covers 542 deterministic generated
cases, including combined rules and meaningful punctuation/repetition controls.
Focused tests also cover legacy callbacks/errors, exact forward/backward replay,
limits, unavailable inputs, strict JSON, source-generation races, cancellation
and private create-only publication. These are mechanics and compatibility
checks, not representative OCR accuracy evidence.

## Explicitly excluded

This is **not the complete pipeline cleanup audit**. It does not instrument
Docling extraction, `rag._normalize_source_chunk_text`, source-attested repairs,
the provenance-aware duplicate-line/numeric protections, chunk-level
deduplication, splitting/merging, final Markdown publication or retrieval/index
construction. Those stages remain pending cleanup-audit coverage.

In particular, the pipeline already protects many legitimate repeated lines,
table rows and source-bound numeric fragments. A change observed by this default
core replay may be prevented by the production source-aware wrapper. Do not
describe this replay as the actual final pipeline output, or remove existing
protections based on it. Replaying a fragment can also change rule behavior at
its edges; the plan's selected fragment boundaries are part of the experiment.

## Fixed inputs

```text
python tools/audit_cleanup.py --pdf synthetic.pdf --recovery recovery.json \
  --plan cleanup-plan.json --output evaluation-reports/cleanup-v1/audit.json
```

The PDF is only hashed, never parsed or rendered. Recovery must be a strict
existing v1/v2 OCR recovery report. The plan selects exact intervals from its
`candidate.text`; it cannot supply replacement text. All source/report/plan
bindings must match actual bytes. Hash agreement is a consistency check, not
authentication or human approval.

Plan schema v1 has exactly these fields:

```json
{
  "schema_version": 1,
  "kind": "cleanup_audit_plan",
  "source_sha256": "<64 lowercase hexadecimal characters: actual PDF SHA-256>",
  "recovery_sha256": "<64 lowercase hexadecimal characters: exact recovery JSON SHA-256>",
  "profile": "core_normalization_v1",
  "offset_unit": "raw_unicode_code_points",
  "entries": [
    {"page_number": 1, "candidate_span": [0, 20]},
    {"page_number": 2, "candidate_span": null}
  ]
}
```

Replace the digest placeholders and interval with actual values. Offsets are
zero-based half-open Python-string intervals, without Unicode normalization.
They are not UTF-8 byte offsets, UTF-16 offsets, grapheme counts, line numbers or
PDF coordinates. Pages are one-based physical page numbers from the recovery.

Entries must be ordered by page and interval, non-overlapping and non-repeated.
Adjacent intervals are allowed but each is normalized independently: splitting
may alter cross-boundary cleanup behavior. A null interval is permitted only
when the candidate is unavailable: failed, deferred, or not selected by the
saved recovery. Such an entry abstains; it never substitutes blank text.
An actually available empty candidate must explicitly select `[0, 0]` and
receives an empty-input warning. Nonempty candidates cannot select empty spans.

## Output and interpretation

`build_cleanup_audit(recovery, plan, *, recovery_sha256)` returns strict generated
v1 `cleanup_fidelity_audit` data. `audit_cleanup_files(pdf, recovery, plan, output)`
adds actual input and observed implementation-source digests and safely
publishes the report. There is no schema change to existing recovery formats.
No caller-supplied summary is trusted: every result comes from validated input
and a fresh execution of the core normalizer.

Each selected entry is `changed`, `unchanged` or `abstained`. Completed entries
contain original/normalized text and exact UTF-8 text hashes. Changed entries
contain ordered edits with fixed rule IDs, whole-step before/after hashes,
removed/inserted text, and before/after spans. An edit uses one linear
common-prefix/common-suffix replacement envelope for the entire rule:

```text
next = previous[:before_span[0]] + inserted + previous[before_span[1]:]
```

The inverse uses `after_span` and `removed`. Replay and reverse replay are exact.
An envelope may include unchanged text between separate changes. It is not a
minimal character alignment, an occurrence-level explanation inside the rule,
or a mapping to the original fragment/PDF. Edit offsets refer to successive
rule states; only the entry's candidate span refers to the saved OCR text.

Heuristic risk flags identify punctuation/symbol changes, changed numeric
sequences, changed words/identifier boundaries, reduced repeated lines,
non-whitespace removal, complete text removal, and empty input. They are review
warnings, not verified errors, a complete semantic check, or calibrated risk
probabilities. Typography changes may legitimately warn. The absence of a
warning does not prove preserved meaning. CER/WER and reviewed contextual
checks remain separate evaluators.

Coverage distinguishes selected/audited pages, fully versus partially selected
candidate text, unselected recovery pages, deferred pages, unselected source
page count, selected characters, and retained trace characters. Fully selected
**candidate text** does not establish complete source-page OCR or semantic
verification. A trace-budget abstention can still have fully selected input;
its entry explicitly has no normalized result or partial edit ledger.

Exit codes:

- `0`: all source pages have fully selected candidate text, every selected
  fragment completed unchanged, and no heuristic warning was emitted. This is
  not an accuracy verdict.
- `3`: any change, warning, abstention or selection gap needs review.
- `2`: invalid inputs, unavailable dependencies or publication failure.
- `130`: cancellation.

Console output contains only fixed labels and counts, not source text, paths,
authored IDs or raw exception details. A late failure or cancellation can follow
publication; inspect the chosen output before retrying. Complete and partial
artifacts are never automatically overwritten or deleted.

## Bounds and provenance

The fixed recipe allows 1–64 entries, 4,096 code points per fragment and 65,536
selected code points total. An intermediate rule state is capped at 16,384
code points. Retained removed-plus-inserted text is capped at 65,536 per entry
and 262,144 total. Exceeding a trace/intermediate budget discards that entry's
partial trace and emits an explicit `trace_budget` abstention. Other malformed
inputs fail the whole request. Rule order and identities are checked, and an
unobserved output change fails closed.

PDF input is capped at 512 MiB and streamed without retaining PDF bytes;
recovery JSON is capped at 64 MiB, plan JSON at 1 MiB, each recorded module
source at 1 MiB, and report JSON at 32 MiB. Both same-handle hash passes are
bounded, including if an input grows. JSON rejects duplicate keys, non-finite
numbers, invalid UTF-8 bytes and excessive nesting through strict shared input
validation. Every selected fragment must also encode as strict UTF-8; escaped
lone surrogates in selected text are rejected. The output byte ceiling includes
the writer's final line feed; the staged file is bounded and digest-checked
against the computed serialization before publication.

Input/output and observed module-source paths must be distinct, link-aware,
single-linked regular files. Publication holds a private output lease, refuses
clobbering, and rechecks all PDF/JSON/module generations inside the final commit
callback, after serialization. Link checks do not claim component-pinned
operating-system no-follow protection against a malicious local path-swap race.
Module hashes bind contemporaneously observed source files; they are not proof
of loaded bytecode or native binary identity. Runtime attestation is separate.

Representative evaluation still needs owner-approved held-out sources and
reviewed before/after judgments. Use generated fixtures until that policy is
settled. Do not promote audit text into canonical extraction or an index
without a separately authorized reviewed publication workflow.
