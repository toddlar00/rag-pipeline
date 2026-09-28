# Occurrence-bound critical OCR evaluation

This opt-in evaluator checks reviewed numbers, units, negations, names, and
footnote identifiers in explicitly corresponding OCR contexts. It catches
errors that global token counts cannot locate. It neither corrects text nor
changes the existing CER/WER scorer, recovery schemas, canonical extraction,
or indexes. The evaluator loads no OCR model and only hashes the source PDF bytes.

For example, `A=10; B=20.` and `A=20; B=10.` contain exactly the same characters
and numeric occurrences. Reviewed checks anchored to `A=` and `B=` fail both
swapped values. A correct `not` elsewhere cannot rescue a missing negation in
its reviewed context.

This is deterministic occurrence checking, **not semantic inference**. A
category such as `negation` is a reviewed label, not a linguistic classifier.
One passed check on a page does not verify the rest of that page. Matching
hashes bind bytes, not authorship, reference accuracy, correct physical
correspondence, or approval authenticity.

## Workflow

1. Choose an authorized source and a strict saved page-recovery v1/v2 report
   for its exact bytes. This adapter does not accept region/hard-scan/layout
   reports as if they were page-recovery reports.
2. Review the source pixels and author a fixed reference cohort. Give each
   sentence, region, or table cell an original-page display-fraction box and
   exact reference text. Do not author ground truth by copying OCR unchecked.
3. Mark the exact reference character span of each critical occurrence and
   its unique, immediately adjacent left/right anchors. Use sufficiently
   specific anchors to identify the relevant subject, row, unit, or clause.
4. Independently inspect the candidate and author correspondence to exact
   `candidate.text` spans. Bind the correspondence to the precise saved recovery
   and reference file hashes. Mark missing or ambiguous contexts explicitly;
   do not drop them or point at a different occurrence that happens to match.
5. Run the command to a new private output path. Inspect localized failures,
   abstentions, and coverage. Keep ordinary transcription CER/WER as separate
   evidence using the existing comparison tools.

```powershell
python tools/evaluate_ocr_context.py --pdf approved-source.pdf --recovery private-recovery.json --reference private-context-reference.json --correspondence private-context-correspondence.json --output evaluation-reports/private-context-checks.json
```

The CLI prints only fixed labels and counts. Exit `0` means every check in the
declared cohort passed and no selected/deferred recovery page was left without
a reference context; it is **not** document certification. Exit `3` means a
failed or abstained check, or selected/deferred pages without any reference
context. Other source pages without references remain explicitly counted even
if they were never selected for recovery. Exit `2` is an invalid input or
publication failure; `130` is cancellation. A late failure/cancellation can
occur after publication: inspect the output before choosing a new output path.

## Editor exports and result navigation

The local review editor provides the [context authoring workflow](ocr-review.md#context-checks-in-the-reference-tab)
using the same reference and correspondence v1 schemas below. Source inspection
uses its original-page renderer; exporting inputs or viewing anchor diagnostics
does not score them. Only **Evaluate exported context checks** runs evaluation.

Editor reference, correspondence and result files remain in the chosen private
output folder. Download links deliver verified copies from the editor's private
cache. If download verification fails, the editor reports the saved artifact
separately; check the private output folder before exporting again.

Correspondence export binds the actual raw bytes of the preceding reference
export, the fixed source and saved recovery. Evaluation accepts only unchanged
artifacts belonging to that editor session. Result navigation verifies those
four input digests and the report file digest before resolving its ordinal
indices to stable draft IDs and a source box; it never repeats scoring. Changed
files or mismatched generations refuse the operation while retaining old files.
A restored partial draft carries no context review declarations, source preview,
export handles or results. See [draft migration and limits](ocr-review.md#save-and-resume-unfinished-review).
The evaluator's schema bounds and coverage limitations below apply to editor
exports; the editor's own input and rendering limits also apply.

## Reference v1

All fields below are required; unknown fields fail. Digests are lowercase
SHA-256 hex strings. Replace illustrative repeated-letter digests with hashes
of the actual files. These synthetic examples contain no private-source text.

```json
{
  "schema_version": 1,
  "kind": "ocr_context_reference",
  "source_sha256": "aaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaa",
  "page_count": 1,
  "approval": "human_reviewed",
  "coordinate_system": "original_page_display_fraction",
  "contexts": [
    {
      "context_id": "ctx-001",
      "page_number": 1,
      "source_anchor": {
        "kind": "sentence",
        "bbox": [0.1, 0.2, 0.9, 0.3],
        "cell": null
      },
      "reference": "A=10; B=20.",
      "checks": [
        {
          "check_id": "amount-a",
          "category": "number",
          "reference_span": [2, 4],
          "left_anchor": "A=",
          "right_anchor": "; B="
        },
        {
          "check_id": "amount-b",
          "category": "number",
          "reference_span": [8, 10],
          "left_anchor": "B=",
          "right_anchor": "."
        }
      ]
    }
  ]
}
```

The source reference binds the PDF, not a particular OCR run. Keep it fixed
across experiments; only the run-specific, reviewed correspondence changes.
The reference file's *raw-byte* hash is bound by each correspondence. Even
whitespace-only JSON edits require an updated reviewed binding.

`source_anchor.kind` is `sentence`, `region`, or `cell`. The box is
`[left, top, right, bottom]` in `[0,1]` fractions of the original displayed PDF
page, including its intrinsic rotation/cropbox, never a deskewed OCR canvas.
Boxes must have positive area. For `cell`, `cell` must contain one-based
`row` and `column`; otherwise it must be `null`. These physical locations and
row/column identities are operator claims. The evaluator does not parse the PDF,
detect tables, verify its reported page count, or prove the claimed source box
contains the reference.

Context IDs are unique, safe ASCII identifiers; check IDs are unique within a
context. Use opaque identifiers, not source names. Categories are exactly
`number`, `unit`, `negation`, `name`, and `footnote_identifier`.

Every span is a half-open `[start,end]` interval in **raw Unicode code points**:
Python `text[start:end]`. There is no NFC/NFKC normalization, whitespace
collapsing, case folding, or punctuation removal. An emoji is one code point
when represented as one scalar; combining marks count separately. These are
not UTF-8 byte offsets or JavaScript UTF-16 offsets. The review editor converts
native UTF-16 selections explicitly, including textarea CRLF/CR-to-LF display
normalization, and verifies the selected text against raw content. Manual
editor offsets remain raw code-point positions. The evaluator itself performs
no display normalization.

Reference check spans must contain a non-whitespace character and cannot overlap within a context.
Repeated equal values need distinct occurrence spans. Each nonempty anchor
must occur exactly once in the entire reference context, immediately adjoining
its checked span. Overlapping substring matches count as ambiguity. An empty
left anchor means the start of the context; an empty right anchor means its
end. Both can be empty for a whole-cell/context-value check. This still depends
on a correctly reviewed physical and candidate correspondence.

## Correspondence v1

```json
{
  "schema_version": 1,
  "kind": "ocr_context_correspondence",
  "source_sha256": "aaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaa",
  "recovery_sha256": "bbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbb",
  "reference_sha256": "cccccccccccccccccccccccccccccccccccccccccccccccccccccccccccccccc",
  "approval": "human_reviewed",
  "offset_unit": "raw_unicode_code_points",
  "contexts": [
    {
      "context_id": "ctx-001",
      "status": "mapped",
      "candidate_span": [0, 11]
    }
  ]
}
```

This example maps a complete `A=20; B=10.` candidate, whose length is 11 code
points, to the reviewed reference above. Both numeric checks fail. For a
larger page, use that sentence's actual offsets in `candidate.text`, not
offsets in native PDF text, a line array, or a normalized transcription.

Every reference context must have exactly one correspondence entry, with no
extra or duplicate IDs. `mapped` requires a bounded span in an available
candidate; its length may be zero for a genuinely empty candidate context.
`missing` and `ambiguous` require `candidate_span: null`. Candidate contexts on
the same page cannot overlap or reuse the same span. Place multiple critical
checks in one context rather than duplicating its candidate occurrence.

A failed/deferred/not-selected candidate cannot have a fabricated mapped span;
such a mapping is invalid input. With an explicit unresolved mapping, every
reference check remains in the report as an abstention with the actual
candidate-availability reason. An available but genuinely empty mapped
candidate is a failed check, not an unavailable candidate and never a pass.
Whitespace-only candidate contexts receive the same empty-context failure;
this availability check does not normalize text during occurrence comparison.

The `human_reviewed` fields are declarations made by the artifact author.
There is no reviewer authentication or signature, and an incorrect/malicious
mapping can select the wrong physical occurrence. Protect and review these
files as evaluation inputs, not model-generated approval receipts.

## Status, localization, and coverage

Within a mapped context, each nonempty anchor must also occur exactly once in
the candidate context. The candidate substring between the anchors is compared
exactly with the reviewed reference occurrence:

- `passed`: exact occurrence match;
- `failed`: the occurrence differs, or the explicitly mapped context is empty;
- `abstained`: context/candidate unavailable, missing or ambiguous anchors, or
  anchor order changed.

Ambiguous/missing anchors are not searched elsewhere on the page. The checker
does not choose the closest numeric token or apply fuzzy semantic alignment.
Conservative abstention can increase when an unrelated typo damages an anchor;
review that context rather than interpreting abstention as success.

The versioned `ocr_context_evaluation` report contains source/input digests,
page numbers, source boxes, ordinal context/check indices, raw reference and
candidate span positions, static status/reason fields, category totals and
coverage. It excludes reference text, candidate text, anchor text, and authored
IDs. Input reference order determines the ordinal indices, so immutable input
digests are necessary for click-through/review consumers.

Coverage is reported separately for checks, contexts, and pages with any
reference context. Every requested check remains in the denominator, including
failures and abstentions. Context status is failed if any check fails,
otherwise abstained if any check abstains, otherwise passed. Per-check counts
still reveal mixed outcomes. Page coverage records failed/empty candidates,
unchecked selected/deferred pages, and source pages with no reviewed context.
`full_page_verification_claimed` is always false. The report contains no
combined accuracy score, CER/WER, automatic correction, or acceptance decision.

## Bounds and storage

The core uses only Python's standard library. It accepts 1–256 contexts, at
most 1,024 checks total, 20,000 code points per context, one million reference
code points total, and 256 code points per anchor. Candidate page text is
bounded at 100,000 code points per page and two million total. PDF page count
claims must be integers from 1 to 5,000; cell row/column claims from 1 to
100,000. Booleans are not integers. Surrogates, nonfinite coordinates,
unbounded numeric values, duplicate IDs/JSON fields, and unknown fields fail.

File limits are 512 MiB source PDF, 64 MiB recovery JSON, 8 MiB reference JSON,
and 1 MiB correspondence JSON. Source hashing uses two bounded streaming reads
through one regular-file generation without retaining PDF bytes. Windows
creation time is not treated as a content-change counter; same-handle digests,
file identity, size and modification time are checked. JSON reads use existing
bounded, link-aware, strict snapshot helpers.

Every input/output path must be distinct, including observed hard-link aliases.
Link-like path components are rejected. A nonwaiting output path lease covers
reads, evaluation and commit. Immediately before create-only publication, the
source and all three JSON digests are rechecked inside the private writer's
commit callback, including changes after serialization begins. The existing
atomic hard-link publisher refuses overwrites even against a noncooperating
writer. No report/source is deleted during recovery from failure.

As with the existing storage layer, observed link checks are not an OS-level
component-pinned no-follow guarantee against a malicious local path swap. Hash
rechecks establish observed consistency, not a multi-file tamperproof
transaction. Inputs and localized metadata remain sensitive even though the
report excludes transcription text. Keep private-source material out of Git,
PRs and logs under the [interim governance policy](governance/private-source-documentation-policy-proposal.md).

## Verification and limits of evidence

Synthetic tests exercise swapped values with unchanged global counts,
swapped reviewed table rows, moved negation, repeated numbers, all five check
categories, ambiguous/missing anchors, exact Unicode offsets, unavailable and
empty candidates, complete correspondence, byte/work bounds, aliases,
non-clobber publication, source changes inside commit staging, and private
CLI/cancellation diagnostics. An actual CLI subprocess evaluates generated
source bytes and saved JSON without importing any OCR engine or parsing PDFs.

These tests establish deterministic mechanics and negative-control behavior,
not representative document accuracy, semantic understanding, or reference
quality. Representative claims still require owner-approved inputs, independently
reviewed held-out references, and explicit reference coverage. No private PDF,
model download, dependency change, or new corpus access is needed for this tool.
