# Source-first random OCR spot audits

The opt-in **Random spot audit** panel checks a fixed sample of retained OCR
candidates against original PDF pages. It is a separate audit snapshot, not a
change to legacy review drafts, OCR output, indexes, or canonical extraction.
Engine confidence controls eligibility; it is not measured accuracy.

## Start or resume

Use the existing authenticated, trusted single-user loopback review launcher
with an approved PDF, its matching recovery report, and an existing private
output directory. Set `RAG_OCR_REVIEW_TOKEN` through your normal private launch
procedure; do not paste tokens into reports or shared command histories.

```powershell
python tools/review_ocr.py --pdf source.pdf --recovery recovery.json --output-dir private-review --trusted-local-session --enable-spot-audit
```

To resume, restart with the exact path returned by a successful Save:

```powershell
python tools/review_ocr.py --pdf source.pdf --recovery recovery.json --output-dir private-review --trusted-local-session --audit private-review/ocr-audit-SAVED_ID.json
```

Replace the example paths with your approved local inputs. `--audit` enables the
panel without also needing `--enable-spot-audit`. Neither flag enables OCR
execution. Use only approved annotation inputs; a saved operator declaration is
not an authenticated approval receipt.

## What is sampled

The frozen frame includes every physical PDF page, using one-based PDF page
numbers rather than printed page labels. A page is eligible only when the
recovery report retains a candidate with non-whitespace text, at most 20,000
raw characters, and a finite mean confidence at least **Minimum mean confidence**.
The default threshold is 0.95. Selection does not inspect whether the text is
actually correct.

The frame retains explicit exclusions for unselected, deferred, failed, empty,
overlong, and low-confidence candidates. The schema also has a defensive
missing-confidence exclusion. This is not a sample of all apparently-good PDF
text: the current recovery format admits at most 20 retained retry candidates
and documents of at most 5,000 pages. Pages outside that retained candidate set
remain visible in coverage, not silently eligible.

**Requested sample size** accepts 1–100; the actual sample is the smaller of
that request and the eligible count. Zero eligible pages produces an explicit
empty sample. Coverage separates excluded pages, eligible-but-unsampled pages,
and the four sampled outcomes: pending, reviewed, unresolved, and unavailable.

One plan is created or restored per session. Changing the threshold or size
afterward does not resample it. The snapshot retains the frame, threshold,
64-lowercase-hex seed, selected pages in draw order, and versioned algorithm
`sha256-counter-rejection-fisher-yates-v1`. It uses ascending eligible IDs,
SHA-256 counter draws with rejection-based range reduction, and partial
Fisher–Yates sampling without replacement. Restarting the same valid snapshot
reproduces the plan; a fresh session without it can create a different sample.

The retained inclusion fraction `n/N` describes the nominal uniform sampling
design over this eligible frame. An empty frame records `0/0`, not an accuracy
rate. Seed independence, unbiased operator seed selection, and representative
population coverage are not proved.

## Review workflow

1. Choose the threshold and size, then **Create / restore frozen sample**.
   Restoration retains historical outcomes and drafts, but no displayed image,
   confirmation, or computed scores.
2. Choose **Sample page (draw order)** and **Open original page**. The preview
   renders the original PDF page without OCR transformations or overlays.
   It bounds the allocated raster to 1,400 pixels per side. Rendering is
   in-process: this is not a native decoder deadline or memory-containment
   guarantee, nor a recreation of the historic OCR raster.
3. Transcribe from the original into **Your source transcription / unfinished
   draft**. This lane keeps the OCR candidate hidden until an explicit reviewed
   or unresolved Store; other panels may already reveal OCR. It is source-first,
   not blinded.
4. Choose an outcome. For **reviewed**, check **I checked this exact transcription
   against the displayed original page**; an intentionally empty reference is
   allowed. For **unresolved**, leave confirmation off and supply a nonempty
   note. **Unavailable** is permitted only after that page's original Open has
   failed while fixed inputs still verify; it also needs a nonempty note and
   no confirmation. Failed or unresolved pages are not scored as correct.
5. Click **Store outcome**. This records the outcome, reference where applicable,
   and note in the session; it does not save a file or score anything. A reviewed
   or unresolved Store then reveals the retained OCR candidate. Edits clear
   current confirmation and displayed scores; confirm the exact revised text
   again before replacing a reviewed outcome.
6. Use **Score stored reviewed references** only when you want metrics. It calls
   the unchanged transcription scorer on stored reviewed references and their
   bound candidates. Pending, unresolved, unavailable, excluded, and unsampled
   pages remain separate. A scoring budget or validation failure retains the
   reviews and drafts and reports scoring unavailable, never a substituted zero.
7. Click **Save audit snapshot (including unfinished transcription)** and retain
   the exact **New private audit snapshot path (restart with --audit)**. Restart
   with that file to continue its lineage. If Save cannot be confirmed, a private
   file may already exist: inspect the output before an explicit retry.

**Reset view (keep typed text)** clears the current image and confirmation while
preserving typed fields. Open the original again before reviewing.

## Drafts, stored reviews, and restart

Unfinished transcription and stored reviewed text are deliberately separate.
Changing the visible draft does not replace an earlier stored reference: **Score
uses that stored reference even when a different unfinished draft is visible**.
Inspect **Stored outcomes (historical declarations, not restored approval)** and
use Store to make your intended change. Switching pages and Save retain the
unfinished transcription; notes and outcomes are retained only by Store.

Each transcription is bounded to 20,000 characters and each note to 2,000.
The snapshot also enforces aggregate authored-text and serialized-byte limits;
over-budget operations refuse rather than publishing partial clean results.
Snapshots use strict closed schema version 1, have an 8 MiB limit, and bind the
source PDF, recovery report, complete plan, and declared parent digest. Save
revalidates fixed inputs and publishes a new private file without overwriting
the loaded snapshot. A restarted child names the actual loaded file's SHA-256
as its parent; repeated saves in one session retain that same loaded parent.
These checks establish consistency, not independent provenance or fresh consent.
Restore and Save do not implicitly invoke the scorer or persist metrics.

## Evidence and limits

Results describe only the completed reviewed subset. They are not representative
document error rates, confidence intervals, proof of an accuracy gain, or proof
of review by an independent human. Historical records are operator declarations.
This feature acquires no new models, runs no OCR, and grants no index-update or
canonical-publication authority.

The [spot-audit development record](evidence/2026-09-08-ocr-spot-audit-development.md)
records 419 passing focused/compatibility tests, lint and a generated actual
browser/real-storage Save/restart/child workflow. It also retains earlier fixture
failures and a cache-cleanup limitation. This is not full copied-source,
representative-corpus or broader qualification. See also
[OCR accuracy and retries](ocr-accuracy.md).
