# Same-crop OCR comparison — development preview

The local Python adapter and opt-in browser controls compare two explicitly
selected crop occurrences against one operator-reviewed transcription. They do
not treat saved whole-page text as a crop reference, choose a winning candidate,
publish a correction or change an index. The ordinary run-result panes remain
unscored; a separate comparison panel requires an independently authored reference.

Use approved generated inputs until the owner authorizes representative source
and reference retention. These APIs and generated controls do not establish
representative accuracy, authenticated human review or full workflow qualification.
The [Phase 11 checkpoint](evidence/2026-09-07-ocr-guided-review-qualification.md)
records the preceding guided-review generation and its browser observations.
The later isolated same-crop preview generation passed a separate
[Phase 12 local checkpoint](evidence/2026-09-07-ocr-preview-isolation-qualification.md):
10,697 passing tests, seven platform skips, six warnings and all nine selected
gates, with independent exact collection/source verification. The new worker
has generated native checks, not a new browser run. Neither checkpoint completes
the broader workflow or replaces its remaining evidence requirements.

The subsequent [durable crop-review pack implementation](ocr-crop-review-packs.md)
adds historical validation and private Save/Open controls. Its separate
[copied-source checkpoint](evidence/2026-09-07-ocr-crop-pack-qualification.md)
passed. The later [crop-detail checkpoint](evidence/2026-09-08-ocr-crop-detail-qualification.md)
separately qualifies the magnification and field-feedback generation below:
11,839 passing tests and all nine local gates, with independent retained checks.
Neither checkpoint establishes representative accuracy or resolves the original
intermittent preview refusal and newly identified late-failure image-cleanup gap.

## Browser workflow

Launch the review editor with `--enable-ocr-execution` as described in
[OCR review](ocr-review.md#opt-in-ocr-execution-development-preview). Inside
**Run OCR review**, expand **Compare two retained crop runs**:

1. Explicitly prepare and confirm a region or hard-scan run. Load its verified
   result choices, select a crop item and capture it as the baseline crop.
2. Execute a second approved crop run, select its verified item and capture it
   as the retry crop. Captured identities survive navigation; old preview,
   transcription and confirmation do not. Both runs must remain in this
   session's bounded retained history.
3. Select **Load original crop and verified pair**. Review the original crop,
   exact physical scope, candidate availability, settings and crop-edge details.
   Reference and critical-entry fields start empty, never copied from OCR.
4. Author a transcription from the original scan. Optionally enter up to 64
   literal critical entries, one nonempty entry per line. Select **Prepare exact
   reference revision**, inspect the prepared text and ordered entries, then
   check the separate crop-review confirmation.
5. Select **Score this confirmed crop reference once**. The result is specific
   to this requested crop and reference; unavailable metrics remain explicit.
   No winner, correction publication or index update follows.

The one-use reference ticket binds the pair, original RGB preview, raw reference
revision, annotation/configuration context and live run/result generation.
Observed changes to either reference field, including editing away and back,
invalidate the ticket. The controls use value-change events, not the installed
textbox's keypress-only input event. A bounded raw-content digest distinguishes
real edits from unchanged values and the empty fields returned with a fresh
crop. Stale edit events return no replacement values or tokens; they neither
raise routine typing errors nor erase a newer ticket. Prepare synchronizes the
marker to its exact validated text so a delayed identical-value notification
does not erase the newly prepared ticket. Invalid current raw fields revoke
consent before semantic reference validation. Draft edit tracking retains a
digest, not raw transcription. The optional Save-enabled state separately
retains one bounded successful comparison declaration, including its reviewed
reference, until revocation or one save attempt.

Wait for the updated view, prepare the exact reference and confirm it explicitly
before scoring. Score checks the submitted text against its one-use ticket.
The retained v3 checks below exercise typed and input-only edits, deletion,
clearing and reload; they are not exhaustive clipboard, IME, platform or browser
transport-order proof. No automatic correction publication is available.

Run/item/context changes clear the displayed comparison; stale
queued submissions and observed changes during rendering/scoring refuse results.
A stale preparation cannot erase a newer ticket. These server checks do not
claim control over responses already in network transport or authenticate that
a person inspected the scan. Candidate panes are visible during transcription;
a blinded scan-first adjudication mode remains further work.

Reference-edit callbacks must carry the preceding crop-view token too. If
backend rendering succeeds but Gradio image postprocessing fails, a later edit
cannot obtain confirmation authority for that undelivered image response.
Explicitly reset/reselect and load the pair again after a failed display. This
does not attest to successful browser image decoding or human inspection.

Only host-provided capabilities and session-permitted run/item IDs are used;
the panel accepts no paths, executables, uploaded images or report bodies. The
original image uses the authenticated host's private temporary Gradio cache,
not per-browser-session file secrecy. The normal launcher owns cache cleanup.
Without the separate opt-in pack service, this panel adds no reference/comparison
export or restart retention. Pack saving is manual; neither mode adds autosave.

## Source-detail magnification — in development

The new **Original crop detail** selector offers **Fit**, **288 DPI** and
**576 DPI**. Fit remains the default and retains the prior renderer's exact
scale and rounding. Detail profiles render the same physical source rectangle
again at fixed scales of four or eight pixels per PDF point. They do not resize
the previous preview, change an OCR recipe, run OCR, or recover information
missing from an embedded low-resolution scan.

Changing the selector alone starts no rendering. It clears the old image,
confirmation, prepared reference and current metrics, while keeping authored
reference and raw critical-entry fields. Choose **Reload detail, keeping my
draft** for an already loaded pair. A reload retains partial fields, including
edits made while rendering, rather than restoring an older captured draft.
Prepare and confirm the reference again against the new image before scoring
or saving a reviewed result. Initial **Load original crop and verified pair**
still starts a new transcription with empty fields; it is not the draft-preserving
reload action.

Locally invalid reference fields now produce inline correction guidance without
discarding the image or authored text. Remove an unintended blank critical-entry
line, then prepare and confirm again. Nothing is trimmed automatically, and an
empty critical-entry field or intentionally empty reviewed transcription remains
allowed. Invalid or stale review authority is still refused separately.

All profiles retain the 1,400-pixel side and 1,960,000-pixel image caps. The
outward-rounded raster is checked before native raster allocation. Oversized
detail refuses without silently reducing density or cropping the scope; select
a lower detail profile or Fit. If the original Load/Open has never succeeded,
use that initial action again; there is no established draft binding to reload.
For an already loaded draft, use the separate draft-preserving Reload action.
Initial Load clears typed fields and initial archive Open restores stored ones,
so preserve any unsaved work before deliberately starting a new view. These image limits do
not bound all PDF-decoder memory. Existing worker deadlines and cancellation
remain in force.

The presentation helper is designed to show one image pixel per CSS pixel in
detail mode, inside a bounded scroll area. Arrow keys, Page Up/Down, Space and
Shift+Space scroll; Home/End reach opposite corners and Tab leaves the preview.
Fit retains ordinary image sizing. A
[minimal synthetic browser check](evidence/2026-09-08-ocr-crop-detail-development.md)
passed after repairing corner clipping and the inset focus ring. An actual
archive probe also verified the three rendered sizes, draft-preserving reload,
fresh 576-DPI scoring and approval clearing. Its incomplete-critical-entry
feedback finding was repaired and verified in a separate archive browser run,
including correction without image reload and draft-only recovery. A later live
browser check verified the three profiles, correction/scoring without image
reload, and draft-preserving Fit/Cancel after two explicitly authorized generated
OCR calls. Its first preview refused an input-generation check; one explicit
retry succeeded, but the intermittent refusal's cause remains unproven. A
separate real isolated-controller check refused oversized 288/576-DPI full-page
previews and recovered identical Fit pixels; it does not establish oversized UI
draft recovery. The [local copied-source qualification](evidence/2026-09-08-ocr-crop-detail-qualification.md)
passed; the fixed selectors depend on the qualified Gradio frontend and need
rechecking when it changes. Broader platform and image-cleanup work remains.
This is not a device-pixel or screen-reader qualification.

Queued actions bind both the selected profile and current view generation.
Rapid stale profile changes may require **Cancel crop preview and clear
approval** to resynchronize the displayed selection, followed by explicit
reload. Cancel keeps the authored fields but removes approval; it does not
prove worker cleanup. A genuine source, pair or annotation-context change
invalidates draft reuse. No arbitrary-network-order last-choice-wins claim is
made.

The worker request/result protocol is now version 2 and binds the exact profile
on success and refusal; mixed versions or mismatched profiles are rejected.
Crop-scope, reference and pack schemas remain unchanged. The new full-suite
checkpoint is separate from the earlier development/browser observations; it
is not a new OCR accuracy result. Previous copied sources and evidence remain
unchanged, including the failed first detail qualification attempt.

## Reading crop advice — focused development

With uncertainty review enabled, a successful original-crop Load/Open or
draft-preserving Reload now adds a short notice and an `advice` object to the
read-only crop details. It examines the same displayed source pixels, including
PDF annotations. It does not run OCR, alter a candidate or save a correction.
Legacy views without uncertainty review do not gain this advice.

Use the notice as a prompt to inspect the source, not as a quality verdict:

- Luminance ranges and percentiles describe this rendered image. A mostly blank
  page with a few characters can have identical percentiles; a strong black
  border can have high contrast without containing any readable text.
- Each edge entry describes an inward two-pixel band. Low/high counts are
  alternative dark/light-pixel observations, not detected cut characters.
  Corners belong to both adjoining bands. `partial` means some geometric
  coverage is missing; `unavailable` means there are no available pixel
  centers. Neither a zero count nor a uniform band means a clear edge.
- Fit, 288 DPI and 576 DPI can yield different measurements of the same crop.
  These are rendering profiles, not measurements of the original scanner's
  resolution. Enlarging a low-resolution source cannot restore lost detail.

If context appears cut off, the notice may suggest a wider rectangle as page
fractions `[left, top, right, bottom]`. It requests at most two PDF points per
side in the exact declared page extents, stopping at page boundaries. Native
clipping/raster rounding can differ. The details retain the actual available
margins and page/rounding limitations. An unavailable proposal does not silently
switch profiles, and a whole-page crop has no wider in-page alternative.

**The proposal has not been rendered or OCRed.** To try it, explicitly select a
new region in the source editor and follow the normal plan/review workflow.
Keep its new reference and results separate from the original crop. Do not paste
its rectangle over the original scope or reuse the original crop's approval,
reference or history as proof for the new region. There is no automatic
"apply padding" action, and wider Fit previews can have a different scale.

Changing detail profiles or cancelling still clears image/review authority.
Use the draft-preserving Reload action for an already loaded crop; do not use
initial Load/Open as a substitute when retaining unsaved text. If the source
itself is illegible, a better source or rescan may be worth obtaining; this
notice does not diagnose blur or promise that a rescan will improve OCR.

The [779-check focused record](evidence/2026-09-09-ocr-crop-advice-development.md)
includes binding, ambiguity, cancellation, draft and import-boundary controls.
The later [native and partial browser record](evidence/2026-09-09-ocr-crop-advice-native-browser.md)
adds 29 real generated-PDF previews and one expected refusal, with independent
pixel accounting and visual review; its partial browser attempt remains unchanged.
The fresh [browser follow-up](evidence/2026-09-09-ocr-crop-advice-browser-followup.md)
records the selector feedback repair, 289 passing scoped tests, and generated
archive Fit/288/576/Cancel/Fit observations with partial draft/history retention
and passing final source/pack checks. The detailed image intentionally scrolls
at natural CSS-pixel size; focus it and use arrow keys or Home/End to navigate.
The v6 journey did not exercise right-edge navigation at 576 DPI. Its record also
preserves the truncated original test-collection log and separates later audit
evidence from the original test run.
Warning usefulness, paired crop/rescan errors and full qualification remain
separate pending evidence.

## Image ownership — locally qualified

The subsequent [image-ownership hardening](evidence/2026-09-08-ocr-preview-image-ownership-development.md)
adds explicit close attempts when a preview fails before reaching its caller,
including late checks, cancellation and finalizer failures. Its separate
[copied-source qualification](evidence/2026-09-08-ocr-preview-image-ownership-qualification.md)
passed 12,127 tests and all nine gates with independent verification of 548 files,
12,134 collected identities and 40 retrieval predicates. The earlier detail
checkpoint remains a separate generation. A successful API render still transfers an open
image: its caller should close it after the last consumer finishes. Live/archive
callbacks keep successful images usable for Gradio postprocessing; eventual
framework disposal and total-memory safety are not established by this repair.

## What can be paired

Both complete region/hard-scan reports must validate. A pair names exact region
occurrences; duplicate rectangles do not make their region IDs interchangeable.
Source digest, PDF page count, physical page number, requested original-display
rectangle and stable page display/cropbox/rotation geometry must match.

Different DPI, pixel dimensions and hard-scan recipes are supported. Each side
is validated at its own resolution and transform. The result retains both
rounded raster extents and their physical-page extents, discloses differing
support and settings, and keeps all predicted text in the score. Extra text
introduced at a crop edge is not silently removed to improve the result.
Changing the requested crop padding or rectangle requires a matching new
reference; overlapping rectangles are not automatically treated as equivalent.

Actual empty candidates can be scored as empty. Failed, abstained or missing
candidates remain unavailable rather than becoming fabricated empty predictions.
A whole-page recovery report is not a crop report, even when a crop covers the
full page. Unavailable comparisons retain explicit reasons and coverage.

## Trusted local coordinator API

An existing `ReviewRunCoordinator` accepts retained run/item IDs, not browser
paths, executables or supplied report bodies:

```python
pair = coordinator.crop_pair(
    baseline_run_id, baseline_item_id, retry_run_id, retry_item_id,
)
# Review the exact source crop and independently author reviewed_crop_text.
# Do not prefill that reference from either OCR candidate or page context.
result = coordinator.compare_crops(
    baseline_run_id, baseline_item_id, retry_run_id, retry_item_id,
    expected_pair_sha256=pair["pair_sha256"],
    reference=reviewed_crop_text,
    critical_tokens=[],
    confirmed=True,  # An explicit operator declaration, not authentication.
)
```

`crop_pair` supplies bounded candidate text, exact occurrence/artifact bindings,
physical scope and recipe information without an accuracy score. The captured
pair digest must still match when comparison starts. Both retained bundles are
freshly validated before and after scoring; changed source, completion artifacts
or evicted run handles refuse the result. This is bounded repeated readback,
not an OS-level atomic snapshot of the filesystem.

The returned reference is anchored to the baseline report digest, exact region,
record and recipe. Editing transcription or ordered critical entries changes
its reference digest. The UI must additionally bind confirmation to the displayed
crop and transcription revision; a Python `confirmed=True` argument does not
prove that a person inspected them. Comparison performs no OCR and publishes no
files. Reference text and returned candidate text are private source-derived
data; retain them only under the applicable annotation policy.

## Original-source crop preview API

`ocr_crop_review_runtime.render_crop_scope(workspace, pair["scope"])` returns
a detached RGB image from the original PDF. It checks the fixed source and
recovery generations, actual physical page/cropbox/rotation, bounded raster
dimensions and input identities again after rendering. It retains annotations
and does not replay candidate preprocessing or load an OCR model. The preview
is at most 1400 pixels per side and 1,960,000 pixels in total; these caps do not
bound all memory used by a PDF decoder. This is a display preview, not a
byte-identical replay of either OCR input raster.

The standalone generated original-preview v2 has separate retained source/producer/image evidence
and independent Poppler visual comparison. Root and independent review checked
the original crop against its full-page location. The preceding v1 helper's
first crop refusal is preserved and remains unexplained; later matching
geometry and successful rendering do not explain that initial failure. No
production automatic retry or guard relaxation was added. Browser-linked
preview/reference authoring now exists as a development workflow, with the
separate browser observations below. These do not complete broader qualification.

## Retained development checks (2026-09-07)

- The preceding caption generation passed 572 focused coordinator, policy,
  renderer, launcher and Gradio checks. Eighteen full-app integration cases use
  actual `process_api`, session/component handling, bundle validation and scoring,
  but explicitly inert rendering, inference, supervision and producer/model
  observations. They are not browser visibility or native containment evidence.
- Browser comparison v1 retained two actual contained crop runs and passed fresh
  independent readback. Its image label hid part of the short original crop, so
  transcription/scoring was not attempted. This layout failure is distinct from
  the earlier standalone preview v1's unexplained rendering refusal.
- The caption-only repair places its static caption outside the image, preserving
  label metadata and exact pixels. Three added controls cover layout and exact
  short/tall RGB-to-PNG persistence; the browser v2 short crop was visibly clear.
- Browser comparison v2 used two fresh actual calls (300 and 400 DPI) against the
  same generated physical crop. The original scan was inspected before typing
  `Synthetic OCR challenge`, with the literal critical entry `OCR`. Separate
  preparation, initially unchecked confirmation and scoring produced a retained
  actual UI result. Both predictions scored zero normalized character/word edits;
  the retry splits the title across lines, which baseline whitespace normalization
  collapses. This is no measured accuracy gain or structural-fidelity claim.
- A freshly prepared/checked reference lost confirmation and its displayed
  result after a keyboard `!` edit and Backspace. Scoring without new preparation
  was refused. The same run also retained 22 rapid-typing HTTP errors and the
  input-only edit gap. At that checkpoint, the whole editing workflow remained
  open; the later correction is recorded separately below.
- Both browser runs closed normally, removed their generated temporary auth/cache
  files and retained OCR bundles, screenshots and observations under
  `output/playwright/ocr-crop-comparison-development-v1` and `-v2`. The UI v2's
  complete textbox values are retained separately from any later recomputation.
- Independent browser v2 readback verified both original execution bundles and
  the exact displayed pair/reference/result correspondence. A separately labelled
  pure recomputation matched the retained UI comparison bytes; it performed no
  OCR or rerendering. Its create-only addendum is `independent-verification-v1.json`
  inside the browser v2 directory, SHA-256
  `ed915216bc836e1f6784a905f977ebb6586c55d05a22274bb3cd78cf641e6d20`.
  It explicitly retains both editing issues, rather than qualifying all edit paths.

That preceding UI SHA-256 is
`f796d01dc46dde63ab4c782ba5e3a7862b6447e89e15e1e8ec9ec87e28706712`.
Its failures and successful checks remain unchanged in the retained artifacts.

### Edit-event correction

The next source generation replaces those keypress listeners with token-bound
value-change handling and silent no-value skips for stale/unchanged edits.
Independent source/test review found no blocker. Its 595 focused checks passed,
including 93 crop UI tests and 21 full-app integration cases. Concurrent captured
event, malformed-input, same-value Prepare, programmatic reset and failed-image
controls preserve the same explicit inert inference boundaries described above.

Generated browser comparison v3 ran two fresh actual OCR calls, then observed:

- Rapid typing in both authored fields with no browser console errors.
- Input-only `fill()` after scoring clearing the prepared reference and metrics.
- A single Backspace after fresh approval revoking it; restoring the text did
  not restore approval, and scoring without new preparation was refused.
- Clearing critical entries revoking approval; partial trailing-newline edits
  remaining editable without a routine error.
- Reloading the same pair clearing authored fields while preserving the fresh
  crop view, followed by successful new preparation, confirmation and scoring.

The original crop was visually read before transcription. Initial and final
actual UI comparison text matched exactly, with no measured normalized CER/WER
gain from the higher DPI. Browser/server shutdown completed with temporary
authentication and scan caches removed; both actual OCR bundles and observations
remain in `output/playwright/ocr-crop-comparison-development-v3`. Physical
clipboard paste and IME composition were not exercised. These observed controls
do not establish every possible edit sequence or network response order.

Independent v3 readback verified both native bundles, the retained edit controls,
and exact initial/final UI comparison correspondence with a separately labelled
pure recomputation. It performed no OCR or rendering. Its create-only addendum
is `independent-verification-v1.json` in that v3 directory, SHA-256
`603faae58280171e80909722b8276c56c49484c7add8714fa00034a63a12b9f1`.
The first verifier's newline-assertion error is preserved and disclosed; it
published no addendum and was not a production failure. Independent visual/docs
review also passed. Complete metrics are retained in the raw textbox JSON;
the score-element screenshots show only an excerpt.

This UI SHA-256 is
`cdb9a195582b63d33ae7978a69afd1d656258323ed16632fd1df276ce95a38eb`.
It is not the Phase 10 generation. The subsequent
[Phase 11 checkpoint](evidence/2026-09-07-ocr-guided-review-qualification.md)
qualifies these bytes through the full local suite and selected policy/retrieval
gates, not hosted CI, authenticated human adjudication or representative accuracy.

### Phase 11 preview failure diagnostics

`CropPreviewError.code` now supplies one of seven fixed, non-sensitive codes;
the exception text remains `original crop preview unavailable or inputs changed`.
No paths, source text or underlying exception strings are included. Codes report
the observed failure stage, not a guessed root cause:

| Code | Meaning |
| --- | --- |
| `scope_validation` | The requested nominal scope did not validate. |
| `input_verification` | Input/access verification failed without an established change cause. |
| `input_changed` | An explicit identity, digest or workspace-binding mismatch was observed. |
| `pdf_render` | PDF decoding/rendering or returned RGB-format validation failed. |
| `geometry_mismatch` | Actual page or raster geometry disagreed with the requested scope. |
| `raster_limit` | The fixed preview dimensions/pixel guard refused the raster. |
| `preview_unavailable` | No more specific safe stage code is available. |

Unknown constructor codes fall back to `preview_unavailable`. Cancellation
continues to propagate. No new preview/render retry or guard relaxation was
added; the shared file reader's existing bounded snapshot retry policy is
unchanged. These diagnostics do not add worker isolation or bound all decoder
memory. They cannot retrospectively explain the preserved v1 failure. This
source change has 102 focused renderer tests and independent read-only review;
it follows, rather than inherits, the earlier retained v2 visual evidence and
is included in the separately verified Phase 11 local gates.

### Isolated previews (post-Phase 11 development)

The browser's same-crop preview now delegates through
`ReviewRunCoordinator.render_crop_preview` to a fixed child process. The public
`render_crop_scope` compatibility API and the other page/scan-view APIs remain
in-process. This is not isolation of every review renderer.

The host still checks the fixed source, recovery, scope and workspace before
and after rendering. It stages an immutable PDF copy and a bounded request;
the child alone opens that copy with the PDF decoder. The host accepts only
bounded raw RGB bytes and a strictly bound completion record, not a returned
PNG, arbitrary file path or serialized Python object. No OCR model is loaded,
and no canonical text, report, index or AI-reader permission changes.

Only one preview runs per coordinator. **Cancel crop preview and clear approval**
revokes that view's approval and requests cancellation of its active preview.
It does not cancel an unrelated OCR run or another view's generation. A
cancellation request is not evidence of finished cleanup. Changed context,
cancellation, nonzero exit and uncertain cleanup cannot supply an accepted image.
Unconfirmed cleanup blocks further preview/OCR starts in that coordinator.

The fixed worker deadline is 30 seconds after shared-supervisor startup;
host-side input hashing/staging and bounded termination grace are separate.
Coordinator shutdown shares a bounded wait across OCR and preview owners.
These deadlines and the existing RGB bounds do not impose a total PDF-decoder
or operating-system memory limit.

Private staging uses the host's platform temporary-directory policy, with a
new uniquely named root, verified private permissions and pinned parent/root
identities. It is separate from the review output and Gradio image cache, and
the launcher blocks that exact root from browser file serving. Known staging
locations beneath the repository, review output or image cache are refused;
this is not a universal detector of cloud-sync software. The shared temporary
parent is not hardened or deleted. Cleanup removes only exact known files and
empty owned directories; replacements or unexplained residue are retained.
Interrupted/failed cleanup can leave a private PDF copy in the host temporary
directory, in addition to any displayed-image cache in the review output.
There is no automatic retry, staging relocation during an operation or stale
directory sweep.

The controller adds four static lifecycle codes to the seven renderer codes:

| Code | Meaning |
| --- | --- |
| `preview_timeout` | The supervised preview exceeded its worker deadline. |
| `preview_cancelled` | Preview cancellation or a closed owner refused the result. |
| `preview_busy` | Another preview already owns this coordinator. |
| `cleanup_unconfirmed` | Cleanup could not be verified; restart is required before new work. |

These are host lifecycle states, not accepted child-reported failure codes.
No private exception details are exposed. Explicitly restarting does not itself
prove that residue from a prior interrupted owner has been removed.

This increment has its own [Phase 12 checkpoint](evidence/2026-09-07-ocr-preview-isolation-qualification.md),
not Phase 11 coverage. Its initial output-directory staging
generation passed 619 combined regression tests, but the separate generated
`ocr-crop-preview-isolation-v1` check failed and obscured the original error code
with its cleanup assertion. That attempt remains unchanged and unexplained.
A new, timing-affected host trace in `ocr-crop-preview-isolation-v2` retained a
staged request's change-time-only mismatch with unchanged digest, device/inode,
size and modification time. It separately observed a Windows sharing violation
on empty-root removal, after request-leaf cleanup completed. The child returned
2; its internal failure and the external metadata-changing actor were not
observed. All recorded producer hashes stayed unchanged. This evidence motivates
separate temporary staging; it does not retrospectively explain v1 or establish
that relocation cures every filesystem failure.

After the staging change, 133 backend checks and the broader 639-check
integration selection passed. A fresh uninstrumented
`ocr-crop-preview-isolation-v3` run used the actual controller once and returned
the generated 311-by-28 RGB crop in about one second. Its bytes exactly matched
the explicitly labelled in-process comparator, and the request files and
private host-temp root were removed successfully. Source/recovery and all nine
recorded producer hashes stayed unchanged. The crop was visually inspected.
This is one generated success, not proof of universal filesystem reliability,
active-decoder cancellation, memory bounds or OCR accuracy.

A separate instrumented resource check made two actual controller attempts,
without OCR or an extra in-process comparator. The normal worker returned the
same RGB digest; five samples reported a peak process working set of
322,199,552 bytes. The second attempt cancelled its bound worker after a live
post-start-gate heartbeat, returned no image and recorded successful cleanup.
Its supervisor returned 130; the killed process's 124 is Windows Job termination
status, not deadline evidence. This is not proof of active decoder entry,
decoder-only or process-tree memory, an enforced memory cap or general performance.
Independent retained readback verified the artifacts and inputs; resource root
names/request files were not retained for independent absence checks or replay.

The final local checkpoint passed all nine gates with 10,697 tests passing,
seven platform skips and six deprecation warnings. Every one of the 10,704
collected identities matched JUnit, and all 520 tracked content hashes remained
bound through independent verification. Audit-time ctime-only observations are
retained separately; this is point-in-time content/hash evidence, not continuous
metadata stability. See the checkpoint for exact hashes, failures and limits.

## Metrics and limits

`ocr_crop_comparison.py` reuses the complete existing report validators and
`ocr_comparison.compare_ocr` without changing scoring or normalization. Its
comparison wrapper contains no transcription text. The coordinator separately
returns the explicitly authored reference, which does contain private text.

Character/word errors, critical-token occurrence counts and regressions use
the existing scorer's semantics. Critical counts do not establish correct
location, negation scope, table-cell assignment or reading order. An empty
reference can have insertion counts while its error rates remain undefined.
Candidate availability, reference coverage and metric-limit failures remain
distinct. No input or prediction is truncated to make an oversized comparison
pass. A measured improvement on one crop is not a document-wide accuracy gain
or proof that a particular setting caused it.

The reader-only [AI evidence service](ai-evidence-access.md) is unchanged.
These trusted local Python methods do not add remote execution, administrative
authority, automatic adoption or cloud access to that service.

## Remaining integration

- Broader clipboard/IME/platform and browser interaction coverage beyond the
  retained generated edit controls.
- Extend the locally qualified browser comparison and diagnostic renderer;
  the first retained preview development attempt still has no established inner cause.
- Broader workflow export/restart beyond the locally qualified manual crop packs.
- Hosted OS/Python/client qualification beyond the selected local gates and
  retained generated browser flows.
- Detail accessibility/device-pixel validation, explicit reference uncertainty,
  eventual framework disposal/failed-close recovery, other renderer isolation, active-decoder
  cancellation and total-memory safety evidence.
- Approved document-family-held-out references and downstream retrieval/answer
  evaluation before making representative improvement claims.
