# Explicit OCR layout resolution

Saved Docling proposals remain suggestions. Different detectors can draw slightly
different boxes around the same printed line, so strict whole-line containment
can leave otherwise useful proposals abstained. This layer lets an operator
resolve those lines without broadening geometry tolerances or changing either
input artifact. It does not rerun OCR or correct recognition errors.

## Contract and review

`ocr_layout_assignment_plan` version 1 is separate from existing Docling proposal
and review schemas. It binds the original source digest, exact recovery-file and
fixed proposal-file digests, and the proposal's Docling/completion digests. Only
original-input, unpreprocessed candidates with compatible saved page geometry
are eligible. Failed, absent, empty, oversized, or unsupported-geometry pages
cannot be made eligible by a manual assignment.

Each page contains every original zero-based line index exactly once, in original
index order, with `region_ref` and `retain_engine_slot`. A region reference must
identify an existing saved region. `null` with retention false is unresolved;
`null` with retention true is an explicit decision to preserve that line in its
original engine slot. Retention is recorded separately from region assignment.

The suggestion helper seeds only unique whole-line containment. Unmatched,
multiply contained, and ambiguous-shape lines remain unresolved. An operator
can assign one of those lines to a saved region. The preview labels assignments
outside the region, ambiguity, and retained slots; it never edits boxes, adds a
tolerance, or presents these choices as geometric proof. Lines fully contained
in any furniture region cannot be assigned to body regions. Such furniture
lines, all furniture-assigned lines, and explicitly retained lines preserve their
exact original slots, including when identical text occurs on multiple lines.

Body regions use the full saved Docling tree order, including spanning headings.
An explicit human order must be a complete permutation of every saved body
region reference, including regions with no assigned lines. Within each region,
original engine order is retained; table-cell order is not inferred or changed.
No new region, cell, text, or omitted line is invented.

A partial preview has no `line_order` or `proposed_text`. A complete preview is
an exact permutation of all original OCR lines, with text derived directly from
those raw lines. `build_assignment_review` requires an explicit nonempty list
of complete pages to confirm. The separate `ocr_layout_assignment_review`
version 1 embeds the plan, confirmed-page previews, original text, diagnostics,
and proposed text. Unconfirmed pages can remain in the embedded draft plan but
are not included among reviewed pages. Loading a plan or generating a preview
is not confirmation. Operator declarations and history are not authenticated
approval. Every artifact retains `accuracy_verified: false`,
`canonical_extraction_modified: false`, and `recognition_rerun: false`.

## API and bounds

`ocr_layout_assignment.py` exposes `suggest_assignment_page`,
`build_assignment_plan`, `validate_assignment_plan`, `preview_assignment_plan`,
`build_assignment_review`, and `validate_assignment_review`. Each accepts the
current full recovery and fixed proposals plus their exact file digests. Strict
validators rebuild expected fields rather than trusting claimed text, geometry,
coverage, or permutations. Partial plans contain no OCR text.

Plans permit at most 20 pages, 2,000 OCR lines per page, the existing 500 saved
regions per page, and 20,000 line-to-region containment diagnostics per page.
History is optional and limited to 512 fixed-action, increasing-sequence entries;
sequence values cannot exceed one billion. History does not prove who performed
an action or whether a preview was actually read.

`ocr_layout_assignment_io.review_assignment_files` accepts fixed original-source,
recovery, proposal, plan, and new output paths plus explicit confirmed pages. It
hashes source bytes without parsing a PDF, checks strict JSON bindings, rejects
links and input/output aliases, and rechecks all four inputs immediately before
private create-only publication. Source bytes are capped at 256 MiB; recovery,
proposal, and plan JSON at 64, 32, and 8 MiB respectively. A saved-plan digest is
recorded by this adapter; in-memory UI plans use `plan_sha256: null` and retain
the exact embedded plan. The adapter does not reopen the earlier Docling JSON or
completion manifest: their digest claims come from the fixed, validated proposal
produced by the existing completion-verifying adapter, not a new authentication
claim. Protect local input files from untrusted writers.

Late cancellation or publication-cleanup failure can leave a complete output
present. Inspect it and use a new output name for a retry; never overwrite a
prior review. Exported reviews contain source-derived OCR text and must be kept
private. Human review of the original image and held-out accuracy measurements
remain necessary; a complete permutation alone does not establish correct
reading order or recognition.
