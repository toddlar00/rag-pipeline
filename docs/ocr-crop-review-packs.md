# Durable crop-review packs — local Save/Open

The development APIs and optional browser controls preserve crop-review work
independently of live run history and the currently installed OCR environment.
Save/Open, fresh-view confirmation and launcher integration are implemented
locally. Generated-data browser runs exercised archive Save/Open, native previews
and fresh-host reopen. A discovered selection race was repaired and retested;
the later [copied-source checkpoint](evidence/2026-09-07-ocr-crop-pack-qualification.md)
passed 11,476 tests, seven platform skips, six warnings and all nine local gates.
Independent verification covered all 11,483 collected identities, 538 admitted
files and 40 retrieval predicates. The reviewed architecture baseline belongs
only to the isolated copy; this is not an original-index, hosted CI, release or
representative-accuracy qualification. The later
[crop-detail checkpoint](evidence/2026-09-08-ocr-crop-detail-qualification.md)
separately passed 11,839 tests and all nine gates for magnification and field
feedback. Later [image-ownership qualification](evidence/2026-09-08-ocr-preview-image-ownership-qualification.md)
covers late-failure cleanup. The subsequent
[uncertainty checkpoint](evidence/2026-09-08-ocr-uncertainty-qualification.md)
passed 13,709 tests and all nine local gates for the then-frozen implementation.
Visual uncertainty editing is now implemented locally; the later
[generated native round trip](evidence/2026-09-08-ocr-uncertainty-native-roundtrip.md)
demonstrated unresolved review/Save, fresh-host Open, explicit resolution and
reviewed child Save. The latest
[fresh repair qualification](evidence/2026-09-08-ocr-uncertainty-repair-qualification.md)
passed 13,749 tests, all nine local gates and independent verification of 586
files and 40 retrieval predicates, with seven skips and six warnings. The
[earlier failed attempt](evidence/2026-09-08-ocr-uncertainty-repair-qualification-failure.md)
and its subsequent guarded-CLI repair remain separately recorded. This new
checkpoint qualifies the later repairs locally. Broader usability, representative accuracy, independent
human adjudication and the remaining OCR/AI outcomes remain pending. The earlier
[Phase 12 checkpoint](evidence/2026-09-07-ocr-preview-isolation-qualification.md)
does not qualify this later implementation.

The subsequent [long-Save feedback changes](evidence/2026-09-08-ocr-save-feedback-development.md)
have focused development/browser evidence; the v4 checkpoint does not qualify them.

[Focused backend checks](evidence/2026-09-07-ocr-crop-pack-backend-development.md)
passed 469 tests and exercised private publication followed by a fresh-process
reopen of retained generated OCR results. That backend-only record does not
qualify the later UI or host-service changes.

The later [Save/Open integration checks](evidence/2026-09-07-ocr-crop-pack-ui-development.md)
cover an initial 624 and a later 659 focused generated cases, including real UI callbacks through the host,
store and scorer with explicitly inert source verification and rendering. The
same record separately describes the original browser failure, its regression
and repair, and the successful fresh-host observation. Counts overlap and must
not be added. The later copied-source checkpoint supplies separate full-suite
evidence without retroactively changing these browser/development observations.

## Enable the private controls

The operator supplies `--crop-review-pack-dir` to `tools/review_ocr.py`, alongside
the existing fixed PDF, matching recovery report, private output directory and
`--trusted-local-session`. `RAG_OCR_REVIEW_TOKEN` remains required. Use a dedicated,
existing private directory: only host-generated pack directories belong there,
not the general output directory or Gradio cache. Supplying the option enables
explicit private retention; it does not copy the source PDF into a pack or
authorize OCR. Opening a fresh crop may stage a private PDF copy for the isolated
renderer. The launcher explicitly selects the uncertainty-aware v2 panels;
embedded panel builders retain their legacy defaults unless `uncertainty=True` is
chosen explicitly.

The **Saved crop review packs** tab works without `--enable-ocr-execution`.
Refresh its catalog, select an entry explicitly, then choose **Open pack and
fresh original crop**. Saved authored text and separately labeled historical
declarations return, but current metrics and approval start empty. Check the
fresh source image and complete authored declaration, prepare it, confirm it,
then choose **Review confirmed declaration once (score only if resolved)**.
**Save new v2 reviewed revision once** retains that confirmed review, including
explicit unscorable coverage when uncertainty remains. **Save new v2 draft
revision** instead preserves unfinished fields/history without carrying any
previous reviewed result.

When OCR execution is separately enabled, the live crop comparison panel also
offers **Save draft** and **Save reviewed result**. These save the two explicitly
captured live crops, not all run history. A reviewed save requires the exact
current confirmed review, which may be explicitly unscorable; browser-supplied
metric text cannot provide that authority. Saving consumes that authorization
even if storage fails. Both panels require fresh preparation, confirmation and
review to save another reviewed result.

The v2 panels show a dedicated inline Save status. **Save requested** acknowledges
the browser request, not server admission. **Saving and verifying** reports
admitted processing, not completed publication; only a checked successful
response reports **Saved**. An interrupted or refused attempt can be
**unconfirmed** even if partial or complete data remains. Inspect the saved catalog
before retrying. Edits can invalidate an in-flight Save; no retry, rollback or
approval restoration is automatic. For a stale view, use Cancel, explicitly
Reload the exact crop while keeping your draft, then prepare and confirm again.

Each save creates a new pack; revisions retain their parent manifest digest.
Refresh the catalog explicitly after a save. Catalog IDs change on host restart,
and no entry is automatically selected. The default store admits at most 32
entries, including retained partial attempts; the UI never silently overwrites,
deletes, repairs or retries one to make space.

## Failure recovery and authority boundaries

**Recover authored draft and uncertainty only** can recover intact bound
authored text and history even when other proof is damaged. It restores no crop
image, candidate, metric or approval and cannot score. Saving a new complete
revision still needs the full historical proof; recovery is not a way to
manufacture missing verification evidence.

Changing context or selecting a different pack removes the displayed fields and
approval. An admitted current initial open/recovery failure clears that view,
not its retained pack; stale callbacks preserve any newer view. The newer detail
reload and Cancel paths preserve authored fields while clearing image/approval.
If an interrupted live Prepare/Score leaves a stale view, use **Cancel crop
preview and clear approval**, then **Reload detail, keeping my draft** when the
same pair's draft binding is still available. In the v2 live panel, loading the
same exact pair preserves its draft/history; a different pair starts a new
draft. After a failed save, inspect the catalog instead of assuming that no
partial or complete pack was written.
The first development browser run encountered a stale Open immediately after
selection. Duplicate/stale selection handling is now repaired and regression
tested. If an interrupted operation still leaves a stale view, explicit
**Cancel archive preview and clear approval** resynchronizes detail selection.
Use **Reload archive detail, keeping my draft and history** to retain current
unsaved fields; initial Open instead restores the stored draft. A different pack/source requires
a new Open and cannot reuse the preceding draft binding.

Both optional panels expose the developing
[Fit/288-DPI/576-DPI detail controls](ocr-crop-comparison.md#source-detail-magnification--in-development).
Detail changes and reloads require fresh preparation, confirmation and review
before a reviewed save. The selected display density is not restored as current
state. Old v1 packs did not record preview density; their historical
`source_image` dimensions and RGB digest must not be used to infer it. V2
annotation history can retain the bound raster-view declarations used for
authoring, including profile and geometry; those are not retained pixels or
proof of review. A fresh host starts at Fit with no restored approval.
These newer controls have their own
[copied-source qualification](evidence/2026-09-08-ocr-crop-detail-qualification.md);
the earlier crop-pack checkpoint is not substituted for it.

Queued actions bind client-captured revisions as well as server state. A Save
queued before a second score on the same image and identical text cannot borrow
the second score's authorization. Reopening an old declaration never creates
an OCR execution ticket, a live run ID or correction-publication permission.

`ocr_review_crop_packs.CropReviewPackService` supplies bounded views, fresh
original previews, comparison and immutable saves. The store's host-only
`read_for_revision` retains the exact bytes from its strict read; raw archives
never become browser outputs. One host archive operation is admitted at a time.
UI calls release session locks before entering storage; publication callbacks
then check the current session revision without reversing that lock order.
The launcher revokes archive and OCR admission before draining work. Shutdown
has bounded waits and reports uncertainty rather than claiming cleanup while
a save is still active. This is not a total process-memory guarantee.

## What is retained

### Uncertainty v2 — explicit visual authoring and retained history

Explicit v2 backend APIs preserve source-bound uncertainty annotations and the
complete authored/adjudication journal. Any unresolved annotation makes the
whole requested crop pair unscorable: candidates remain visible, but metrics
and unknown character/word counts stay null. Fully resolved references use the
unchanged full-text scorer. These are local declarations, not authenticated
reviewer identity or measured accuracy on representative documents.

The live coordinator exposes `author_crops_v2`, `compare_crops_v2` and
`render_crop_preview_with_view`. The archive service exposes `author_v2`,
`compare_v2`, `open_with_view`, `recover_draft_v2`, `save_live_v2` and
`save_revision_v2`. These are trusted host interfaces, not public browser APIs
or permission for AI to publish canonical corrections. The launcher shares the
metadata-capable preview port only when the execution coordinator is already
explicitly enabled; archive-only operation does not enable OCR.

New v2 saves retain exact verification bytes and parent-history continuity.
Reopening restores authored history, never current approval. Recovery reads
only the manifest and authored review file and returns unverified draft/history,
not candidates, scores, images or execution authority. Budget exhaustion refuses
growth without dropping old annotations, views or revisions.

Both v2 panels expose **Browse** and **Annotate uncertainty** modes. A selected
rectangle or whole requested crop is only a pending selection: choose an
explicit annotation action and reason, then Apply to record a revision. Text
edits invalidate approval and keep history dirty until an explicit authoring
action records the required reset; changing text back does not restore approval.
After applying changes, Prepare, inspect the complete declaration, confirm it
and Review. Unresolved areas remain visible and make the whole crop unscorable;
explicit resolution permits full-reference scoring, not automatic OCR adoption.

The v2 archive panel can open an old v1 pack and save a new v2 child while
preserving the parent; existing v2 history is not silently reset or downgraded.
Legacy v1 APIs remain separate and refuse v2 state. The
[visual UI record](evidence/2026-09-08-ocr-uncertainty-ui-development.md) retains
focused Queue/browser evidence, and the
[native round trip](evidence/2026-09-08-ocr-uncertainty-native-roundtrip.md)
demonstrates the generated-fixture workflow after the initialization repair.
These observations do not establish representative accuracy, human adjudication
or broad accessibility. The later repairs now have the passing copied-source
qualification noted above; the [pack/host record](evidence/2026-09-08-ocr-uncertainty-pack-host-development.md)
remains historical backend evidence, not a current claim that the editor is absent.

### Existing v1 archive contents

A closed `ocr_crop_review_pack` v1 contains two complete historical verification
bundles, the exact selected crop occurrences, and separately authored draft or
historical reviewed state. Existing review-draft v1/v2/v3 schemas are unchanged.

Each side retains the original bytes of `manifest.json`, `report.json`,
`execution.json`, `disposition.json` and `work/report.json`, plus its recovery,
plan, three dependency locks, model policy and model lock. A historical
installation JSON is retained only when the original request binds one.
`review.json` holds the exact authored reference and raw critical-entry text.
No PDF, raster, model binary, installer log, Python environment, browser session,
execution ticket or current approval is copied into the pack.

The pack has 25–27 payload files, at most 256 MiB including a reserved 64 KiB
manifest allowance. Review state is bounded to 3 MiB; transcription to 20,000
characters and raw critical entries to 16,448 characters. Existing per-artifact
bounds also apply. These are serialized-data limits, not total process-memory
guarantees. Oversized saves must refuse without truncating verification evidence.

## Historical reading is not execution admission

`ocr_disposition_archive.validate_disposition_archive` checks retained raw hashes,
request/producer/model declarations, report context, receipt accounting and
disposition joins without inspecting the current installed packages or models.
The supported recipe remains the characterized RapidOCR 3.9.2 region/hard-scan
recipe. Whole-page runs and unsupported historical schemas are refused.

Its `historical_local_declarations` scope does not authenticate execution or
verify historical loaded binaries. Full-environment, installation-log and
installer-source hashes remain declarations because those files are not retained.
The reader never reconstructs a live `DispositionRequest`, run or execution token.

`ocr_crop_review_pack.validate_crop_review_pack` additionally binds both sides to
the same source, saved recovery and exact requested physical crop, preserving
distinct region occurrences and original raw report digests. Different DPI and
supported recipes remain visible. A saved reviewed reference and comparison are
replayed with the existing scorer; a failed candidate is not an empty prediction.
The saved preview hash is a declaration, not a retained image or proof of review.

## Draft recovery and private publication

Drafts preserve empty values, spacing and unfinished critical entries exactly.
They never silently inherit an earlier reviewed transcription. The explicit v1
`recover_crop_pack_draft` path returns authored text and declared scope only—no
candidate, metric, image, approval or execution capability. Missing or damaged
other verification files must not prevent recovering an intact bound draft.

The private store publishes fixed filenames into a new host-generated directory,
with a canonical manifest committed last and no replacement of existing files.
The host supplies source/input checks and a per-save view-generation guard.
Interrupted or cleanup-uncertain publications remain retained, explicitly
unverified, and are never automatically retried, repaired or deleted.

Restart discovery assigns fresh opaque catalog IDs under an operator-fixed root;
it does not select the newest entry. Browser fields must never supply paths.
Manifest-only discovery is not full verification. Historical evidence requires
strict readback; new scoring additionally requires the fixed original source,
a freshly rendered crop and fresh explicit reference confirmation.

Use generated inputs until representative-source and reference-retention policy
is approved. Packs are private annotation artifacts, outside the AI reader
service and canonical correction/index publication. Saving or reopening one does
not establish representative OCR accuracy, correction approval or release status.
