# Local visual OCR review

The review editor displays a source-bound scan, numbered OCR quadrilaterals,
original/proposed reading order and plain-text differences. Draw rectangles by
clicking two opposite corners. It exports validated, private JSON sidecars;
it never modifies a PDF, recovery report, canonical extraction or index.

Use approved generated examples until the owner records permission for retaining
annotations and reference text derived from representative private books. The
editor is not a production qualification or a way to bypass that decision.

## Launch

Install the project's appropriate locked optional runtime using its documented
lock workflow. Gradio, PyMuPDF, NumPy and Pillow are needed for display;
preprocessed reports also need OpenCV. Default review loads no OCR model.

Set `RAG_OCR_REVIEW_TOKEN` in the launching process to a private, random password
of 32-256 printable non-space ASCII characters. Do not put it in command-line
arguments, source files, screenshots or chat. Then run, for example:

```powershell
python tools/review_ocr.py `
  --pdf output/pdf/ocr-challenge-v1-verified/challenge.pdf `
  --recovery evaluation-reports/ocr-challenge-v1/none.json `
  --output-dir evaluation-reports/ocr-editor-v1 `
  --trusted-local-session
```

The output directory must already exist and be approved for private annotations.
Open `http://127.0.0.1:7861` and sign in as `review` with that password. The
listener is fixed to literal loopback; sharing, public API discovery, monitoring
and MCP serving are disabled. This is trusted single-user local operation, not
a sandbox or a remotely deployable review service. Do not tunnel it.

## Opt-in random spot audits (development preview)

Add `--enable-spot-audit` for the separate **Random spot audit** lane, or use
`--audit PATH` to resume a private source-bound audit snapshot. It samples retained
high-confidence candidates, displays original pages, separates unfinished,
reviewed and unresolved work, and scores only on explicit request. It runs no
OCR and changes no canonical text. See [the workflow and limits](ocr-spot-audits.md)
and [development evidence](evidence/2026-09-08-ocr-spot-audit-development.md).

## Opt-in OCR execution (development preview)

Add `--enable-ocr-execution` to explicitly enable the **Run OCR review** tab.
The default command above remains model-free and does not import the execution
backend. Use the characterized local Python 3.12 OCR environment described in
[same-call diagnostics](ocr-detection-disposition.md). This integration follows
the Phase 10 frozen checkpoint and has separate generated browser/native
observations and [Phase 11 local qualification](evidence/2026-09-07-ocr-guided-review-qualification.md):
10,508 passing tests, seven platform skips, six warnings, reviewed architecture
and all nine selected gates. Phase 10 evidence belongs to the preceding code
generation; the later local checkpoint is not hosted CI or full workflow completion.
The subsequent [Phase 12 checkpoint](evidence/2026-09-07-ocr-preview-isolation-qualification.md)
qualifies fixed-child same-crop previews, private host-temporary staging and
per-view cancellation: 10,697 passing tests and all nine local gates, with
independent exact collection/source verification. It has generated native
evidence, not a new whole-browser run; other page/scan renderers remain in-process.

`--ocr-timeout-seconds` accepts 1-3600 seconds and defaults to 600 when execution
is enabled. Optional `--installation-evidence` selects one fixed private
installation record at launch. Both options require execution enablement.
Browser events cannot choose source/output paths, executables, environments,
process IDs or installation files. No models are downloaded.

1. Choose explicit pages (at most 20), the current page, or the currently stored
   draft crop plan. An unadded rectangle is not part of a run. Hard-scan runs
   require explicit orientation, illumination and bow-applicability settings;
   the displayed recipe applies to every selected crop.
2. Click **Prepare exact OCR scope**. Preparation binds the source, saved
   recovery, selected pages/crops, recipe and producer generation without OCR
   or plan publication. Inspect the preview, then separately authorize this
   one execution and click **Start confirmed OCR run**. Changed views/settings
   revoke consent; a saved draft never restores execution approval.
3. Use **Refresh run status** or **Cancel selected run** independently of the
   preparation/result queue. One run is active per host, including other tabs.
   Cancellation is a request, not proof that no OCR occurred. Actual call counts
   remain unknown after handoff until strict receipt readback establishes them.
   Status displays the last observed artifact state/counts without hashing the
   files again; loading results performs fresh verification.
4. Load verified result choices and select an item. The saved page context and
   new candidate remain separate. Page-to-page text differences are not accuracy
   scores; whole-page context is not a crop reference. Failed, empty, deferred,
   unselected and abstained outcomes remain distinct. For two crop results,
   expand **Compare two retained crop runs**, capture each selected item, load
   the original scan and independently author a reference. A separate prepared
   reference and one-use confirmation are required to score it. Follow the
   [same-crop comparison workflow](ocr-crop-comparison.md); these ordinary
   result panes remain unscored. Linked word/symbol overlays and private
   crop-reference export/restart remain further work.

Only the existing contained diagnostic worker runs OCR. The review process can
prepare its request but cannot execute that worker's bundle operation in-process.
The exact approved crop plan is published privately before worker dispatch;
source/recipe/producer changes refuse dispatch rather than authorize a newer run.
The new result never replaces the editor's baseline, annotations or cached view.

Process outcome and artifact availability are separate. A failed, cancelled or
timed-out operation can leave a complete bundle that still passes strict
readback; incomplete/unverified files are not presented as accepted results.
Cleanup uncertainty prevents another execution. Shutdown revokes new starts,
requests cancellation and waits for a bounded cleanup allowance before closing
the UI; it reports failure if cleanup is unconfirmed. Completed and partial run
artifacts and approved plan files are retained, not deleted or silently reused.
There is no automatic retry, checkpoint resume, correction publication or index
update in this panel. Its session history keeps at most eight run handles;
eviction does not delete files and reopening the editor restores no run authority.

The integration has focused lifecycle/Gradio tests and generated native/browser
development observations, not representative book accuracy evidence. The crop
reference editor's [value-change safeguards](ocr-crop-comparison.md#browser-workflow)
now cover observed typing, input-only fill, delete, clear and reload controls;
earlier failed generations remain retained. Reprepare and explicitly confirm
the exact reference before scoring. Broader browser/platform coverage, durable
crop-review restart and representative accuracy remain pending. No new scan,
write or execution authority is added to the separate AI reader service.

## Review actions

1. Choose any source page. Failed, empty, deferred and unselected pages appear
   before pages with candidates; this is a coverage-first queue, not calibrated
   error prediction. Fullscreen provides a larger scan view. Without a candidate,
   the editor displays the original scan and explicitly unavailable OCR.
2. For a **retry crop**, draw a rectangle, click **Add selected crop**, and review
   the generated plan. **Undo last crop** removes a stored selection. Export
   after checking the review box. Up to 20 regions can span multiple pages.
3. For **two-column order**, click **Suggest column bounds** or draw the body
   band and gutter manually. A suggestion shows bounds without changing text
   or numbering. Confirm that the scan shows **left-to-right two-column prose,
   not a table**, then click **Preview column order**. Inspect numbered lines
   and differences, check the separate review box, and export the layout plan.
   **Dismiss column suggestion** restores previous work. Ambiguous geometry
   abstains; all order exports are blocked while a suggestion is pending.
   Changing/clearing a layout selection invalidates that page's stored plan
   and confirmation. See [column suggestion limits and CLI](ocr-column-suggestions.md).
4. For **references**, transcribe from the scan into the initially blank field,
   confirm line-by-line review, and store the page. Export the stored references
   after checking the separate export confirmation. Reference text is bounded
   to 20,000 characters per page by the existing scorer contract.

An optional fixed `--scan-bundle` adds a separate original-page pixel-hypothesis
panel. Select an observed region and explicitly add its original-coordinate
crop to the existing retry plan. This does not run OCR or approve an export.
The original gray raster must reproduce its declared pixel digest; incompatible
or unavailable evidence stays visible as unavailable. See
[independent scan inspection](ocr-scan-inspection.md) for limits, historical
bundle matching and the distinction between ink geometry and verified text.

Reference, crop and omission actions now bind captured inputs to the displayed
annotation generation. Page/focus/selection changes and user text/decision input
revoke the affected confirmations. A stale queued action must be retried after
the current view arrives and is inspected; it cannot borrow a later page's
approval. These local consistency checks are not authenticated human consent.

## Context checks in the reference tab

Use **Context checks** in **Reference transcription** for occurrence-bound
checks. Page-reference exports above remain a separate workflow. The
[context evaluator contracts](ocr-context-evaluation.md) define the reference,
correspondence, categories, exact-match rules and bounds.

1. Click **New context**, choose its **Context source page**, and click **Set
   context page** when changing it. Transcribe the source into **Context
   reference text**. Choose sentence, region or cell; for a cell, enter its
   one-based row and column. **Open original source preview** and click two
   opposite corners around the context. The box uses the original displayed
   page, including its cropbox and rotation.
2. Click **New context check**, choose a category, and select the exact critical
   occurrence in the reference. Inspect the proposed left/right anchors and
   diagnostic; correct them or the raw offsets as needed. Equal repeated values
   need distinct occurrences. Store changes, inspect the original source, then
   explicitly **Review current context reference**. Review every context before
   separately confirming **Export context reference**.
3. Select the corresponding complete context in **Saved candidate text for
   context selection**. For manual entry, set **Context correspondence status**
   to **mapped** and enter the raw start/end offsets. Mark missing or ambiguous
   correspondence explicitly when appropriate. After manual edits, click
   **Store context changes** before reviewing. The read-only display uses the
   saved candidate, never native PDF text or the authored reference as a
   fallback. Inspect the source and candidate, **Review current context
   correspondence**, then confirm **Export context correspondence** for the
   same exported reference cohort.
4. Click **Evaluate exported context checks** to create the report. Inspect
   coverage, failures and abstentions. Selecting a result row immediately
   navigates to its source. Alternatively, enter context and check ordinals and
   click **Show context result source**. Both paths recheck the exact source,
   recovery, reference, correspondence and report bytes without repeating scoring.

Browser selections convert UTF-16 positions and native textarea LF display
back to raw source offsets; manual offset fields use raw Unicode code points.
An unchanged LF display preserves stored CRLF/CR text and anchors. Editing the
reference clears its prior occurrence spans and correspondence. Source/page,
reference and check edits revoke affected reviews and exports; mapping-only
edits retain an unchanged reference export but revoke correspondence/results.
Wait for the current view before confirming again after a stale-view notice.

New contexts and checks receive opaque IDs preserved by switching and draft
restart. Exports use new private filenames and exact byte bindings owned by
the current editor session; browser fields cannot supply artifact paths. Input
exports and anchor previews do not evaluate or certify accuracy. Restarted
drafts need fresh source inspection, reviews and exports before evaluation.

Files offered for download are also saved in the chosen private output folder.
The browser receives a separate, verified copy from the editor's private cache;
the saved original remains available after the editor closes. If the editor says
an artifact was saved but its download could not be verified, check the private
output folder before exporting again. An unavailable download does not mean
that the export failed to save.

## Save and resume unfinished review

New snapshots use `ocr_review_draft` version 4. They retain omission assessments,
partial line-to-region assignments and complete assignment preview selections,
and add partial `context_authoring` contexts with selected context/check IDs.
Contexts retain their page, completed source anchor, reference, checks and
correspondence; unfinished checks or mappings can remain unresolved. Strict
versions 1-3 remain readable and acquire empty collections for absent features,
including empty context authoring; their next save is version 4. Unknown fields
or versions are rejected. Migration grants no review authority. Context reviews,
source previews, first corners, export bindings and results are never restored;
all confirmation checkboxes start unchecked.
The exact UTF-8 JSON encoding, including escaped characters, is checked against
the same 16 MiB limit used at restart before any draft is published. Oversized
snapshots are not saved; the current session and previous exports remain intact.

**Save review draft** creates a new `ocr_review_draft` v4 JSON snapshot. It keeps
the current rectangle selections, stored crops and orders, reviewed references,
and unfinished reference text separately. Changing pages retains typed reference
drafts in session memory; saving makes them persistent. Partial first-corner
clicks are not saved. The global Save first captures the displayed pending
context fields against their current view. Pre-anchor source kind/cell values
remain session-only until a real rectangle is stored; Save/restart does not
invent an anchor for them. There is no background autosave or browser file upload.
Pending column suggestions and their transient view tokens are not saved;
previously stored plans remain intact. Suggested bounds enter stored selections
only after explicit prose confirmation and a successful preview.

Restart the same command with `--draft path/to/ocr-draft-....json`. The operator
selects this fixed path at launch; browser events cannot choose files. The source
and recovery digests must match, and all confirmation checkboxes start unchecked.
Unconfirmed transcription is never silently promoted to a reviewed reference.
Saved line orders are recomputed from the bound report and plans. Snapshots made
from a loaded draft retain its exact digest as their parent; siblings do not
overwrite each other.

The activity trail retains the latest 512 entries and an explicit total count.
It is operator-editable history, not authenticated identity or proof of review.
The total activity count is capped at one billion; a terminal snapshot remains
viewable/exportable, but further recorded edits are rejected at that budget.
Snapshots allow at most 256 reference pages and 20 crops. The two-million
combined character budget includes page references, critical phrases, unfinished
transcriptions, context references and both anchors per check; the input limit
remains 16 MiB. Context-specific bounds also apply as documented by the
[context evaluator](ocr-context-evaluation.md#bounds-and-storage). Empty navigation
does not consume reference slots. Stored references remain unchanged when their
current draft text is edited; explicitly store a reviewed replacement to update
the export set.

## Docling-assisted regions and reading order

Generate a source-bound proposal artifact using
[the saved Docling proposal workflow](ocr-docling.md), then add
`--proposals path/to/proposals.json` at editor startup. The **Docling suggestions**
tab lists proposed regions and available table-cell boxes. **Show suggested
crop** draws the selected box; inspect it before adding it to the ordinary crop
plan. Geometrically supported region suggestions can still be reviewed when
the full reading order abstains.

**Preview Docling reading order** uses only complete, validated line
permutations. It retains spanning-heading/body order and displays the exact
text difference and numbered boxes. Confirm all stored page orders explicitly
before **Export reviewed Docling orders**. This creates a distinct
`ocr_docling_review` v1 sidecar, with original/proposed text and exact
source/recovery/proposal digests. It is an operator declaration, not measured
accuracy, authenticated approval, or canonical publication. Column and Docling
orders are mutually exclusive for each page.

Drafts also bind their optional proposal file and retain Docling preview pages.
Resuming requires the same `--proposals` input; a missing, changed or different
proposal generation is rejected. Proposal source/manifest digests describe the
generation that created the proposals; the editor does not reopen arbitrary
Docling paths or independently authenticate its predictions.

These confirmation controls record operator intent, not authenticated human
identity or factual correctness. Do not let an AI click them as an approval of
real corpus ground truth. AI-generated proposals still need source review.

From the initial view, a per-view client token binds Preview and all three
reading-order exports to the displayed review generation. Page/order changes
rotate it; a successful view update delivers the token with unchecked review
boxes. Delayed requests containing an earlier view's checked boxes are refused.
This addresses Gradio's captured-checkbox/live-session-state queue behavior;
it is not cryptographic proof of human inspection. A view update failure does
not issue a fresh token. Wait for the updated view and confirm again after a
stale-view notice. Draft restart resets all confirmations and view tokens.

Each export receives a fresh filename. Use layout plans with
`tools/reorder_ocr.py`, reference manifests with `tools/compare_ocr.py`, and
region plans with `tools/retry_ocr_regions.py`:

```powershell
python tools/retry_ocr_regions.py --pdf source.pdf --recovery recovery.json `
  --plan regions.json --output new-region-review.json --dpi 400 --timeout-seconds 120
```

## Geometry, bounds and failure behavior

### Resolve incomplete layout suggestions

The **Resolve layout** tab opens draft assignments for the current page using
the fixed Docling proposals. Only uniquely contained rectangular OCR lines are
initially assigned; unmatched or ambiguous lines remain unresolved. Choose an
original OCR line to highlight it in red, then choose a saved target region
(orange outline) and apply the assignment. An explicit **retain original engine
slot** choice keeps an unmatched line in place without claiming a region match.
Furniture lines are locked to their original engine slots. Outside-region and
ambiguous assignments remain labeled in the diagnostic evidence.

Use **Body regions in reading order** and **Move region earlier/later** to correct
the saved body order. The original line order within each region is preserved;
tables do not acquire an inferred cell order. Restoring the saved Docling order
is explicit. These controls never resize OCR boxes or rewrite recognized text.

Preview requires every line on that page to be assigned or explicitly retained.
Every original line occurs exactly once in the derived order. Complete assignment
coverage is not recognition accuracy. Editing an assignment or region order
invalidates the page's preview and resets export confirmation. Column, automatic
Docling, and manual-assignment previews cannot be active together on one page.

The separate `ocr_layout_assignment_review` export includes only explicitly
previewed pages after fresh confirmation. Other partial pages remain in the
review draft, not in that export. Clearing a visual selection deactivates its
preview but preserves assignment work for **Start or continue line assignments**.
See [the strict assignment contracts](ocr-layout-assignment.md).

### Inspect possible omissions

The **Omission checks** tab compares saved Docling regions to saved OCR line
geometry. It distinguishes no overlap, whitespace-only recognition, ambiguous
overlap, and nonempty line geometry. It also reports failed, empty, unselected,
deferred and geometry-unsupported pages. Missing evidence is not a zero-error
result. Furniture-region warnings are counted explicitly; footnote-like material
must not disappear from review merely because its layout label is furniture.

Choose a saved region to show its orange outline when coordinates are compatible
with the preview. **Show this region as a retry crop** uses the existing crop-plan
workflow; it does not run OCR. Mismatched or preprocessed proposal coordinates
cannot be used as a suggested crop or misleading outline.

Record **Suspected missing text**, **Not text**, or **False alarm after scan
inspection**. **Reset assessment to unresolved** removes that decision. Every
original diagnostic warning remains, including after a false-alarm assessment.
The v4 draft stores decisions, not diagnostic statuses or ephemeral highlights;
diagnostics are rebuilt from the fixed recovery and proposals when needed.

**Export unreviewed geometry diagnostics** creates a distinct diagnostic sidecar
without declaring review. **Export reviewed omission assessments** requires fresh
confirmation and preserves unresolved counts, so partial reviews are possible.
Neither export establishes authenticated review, full-page completeness, or OCR
accuracy. One recognized line cannot prove a whole paragraph or table was read.
These saved-region checks do not inspect scan pixels or find regions missed by
both systems. The separate [independent scan panel](ocr-scan-inspection.md)
adds source-pixel hypotheses and has its own
[local checkpoint](evidence/2026-09-07-ocr-scan-review-qualification.md);
it does not turn saved-region overlap into complete-source evidence.

### Rendering and publication bounds

- Page rendering is capped at 6,000 pixels per side and 25 million pixels,
  checked against saved geometry before allocation. Input PDF/report snapshots
  are bounded to 256/64 MiB. The interactive overlay is capped at 2,000 lines.
  Above that limit, the entire OCR overlay is withheld with an explicit notice;
  the scan and available text remain reviewable, without a misleading truncated
  set of boxes.
- The display replays saved affine deskew and contrast settings; it does not
  re-estimate them. Current rendering libraries can differ from the original
  run, so the display is not a byte-identical raster attestation.
- Layout coordinates remain candidate-raster fractions. Region crops are
  inverse-mapped into original displayed-page fractions. Deskew can expand a
  crop envelope; padding-only selections are rejected. Intrinsic PDF rotation
  and cropboxes belong to the displayed-page coordinate system.
- Every export revalidates its schema and source/report digests under its output
  lease, again at publication. Changed inputs or output-directory generations
  fail closed. Existing exports cannot be overwritten.
- Candidate failures are not blank source pages. Candidate-less pages use a
  bounded original-page preview (at most 1,400 display pixels per side), support
  crop selection and reference transcription, but cannot reorder nonexistent
  OCR boxes. Unsaved original extraction for deferred/unselected pages remains
  unavailable; the editor does not rerun native extraction or invent text.
- Review rendering/actions recheck the fixed inputs outside the image cache.
  Cached previews cannot authorize work against changed source/report/proposal
  generations. Exports also recheck loaded draft/proposal digests immediately
  before publication.
- Gradio caches displayed scans in a process-owned private temporary directory
  inside the chosen output directory. Normal shutdown attempts cleanup. Forced
  termination or cleanup failures can leave a private cache; this is not secure
  erasure. Exported annotations persist intentionally.
- Opt-in same-crop previews additionally stage private PDF copies in a unique
  owned root under the host platform temporary directory, outside that image
  cache. The launcher blocks the exact root from file serving. Interrupted or
  unconfirmed cleanup may leave a private source copy there; restarting does not
  prove earlier residue was removed. There is no automatic stale-root sweep.
- A post-commit cleanup failure means an export may already exist. Check the
  configured output directory before retrying; do not assume nothing was saved.

Region retries are a separate `ocr_region_review` v1 contract. They retain saved
page context separately from crop candidates and report crop-local boxes plus
original-page fractional boxes. Crop rendering is capped at 25 million pixels /
6,000 pixels per side, with 100 million planned pixels across the request. No
page-wide raster is allocated for a crop, and no candidate replaces source text.
The report's recognition-workflow flag does not prove successful OCR; inspect
the per-region candidate/empty/failed statuses and counts.

See [the full improvement program](ocr-improvement-program.md) for outcomes still
pending, and [AI pipeline access](ai-pipeline-access.md) for the separate reader
client. Neither this editor nor the client automatically publishes OCR changes.
