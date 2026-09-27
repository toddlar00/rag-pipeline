# Same-crop preview image ownership hardening, 2026-09-08

This is intentional resource-safety hardening of the same-crop preview path,
not a behavior-preserving extraction, an OCR accuracy experiment, or a new
full-suite qualification. It follows the separately frozen
[crop-detail checkpoint](2026-09-08-ocr-crop-detail-qualification.md); that
checkpoint and its failed first attempt remain unchanged.

## Changed ownership contract

A function that creates or receives an image retains it until its successful
return, including any intervening context exits or finalizers. A failed transfer
now attempts `close()` exactly once. A secondary close failure, including
`KeyboardInterrupt` or `SystemExit`, cannot replace the already-selected
primary failure/cancellation. A close attempt is not proof that disposal
succeeded if close itself raises.

The repair covers:

- PDF raster image creation through the PDF document context's exit.
- Source/binding/snapshot checks after rendering.
- Host RGB reconstruction, worker result checks and staging cleanup.
- Controller and direct-coordinator cancellation and cleanup finalizers.
- Private crop-pack open/readback and its operation bookkeeping finalizer.
- Live/archive panel hashing, stale-context/profile refusal, output construction,
  and session-lock exit before returning the output.

The child worker separately closes its image after copying detached RGB bytes,
before rechecking inputs or publishing. With no earlier failure, an ordinary
close failure follows the existing static `pdf_render` refusal policy; a
close-only cancellation propagates. Failed serialization preserves its primary
error even if disposal also raises.

Healthy host/UI returns remain open for the next owner. Profiles, rendered
pixels, schemas, retry behavior, private source boundaries, consent/draft state,
and event wiring are unchanged. Existing supervisor cleanup-uncertainty
precedence is preserved rather than newly redefined.

## Source identity

Original dirty-worktree base HEAD:
`e34103f70b676eacc8d55badb2468b8a10004ef4`; tree:
`af0a69ccde82b6b679580f07813495721978efe0`. These IDs alone do not contain
the working-tree implementation. SHA-256 and computed Git blobs below identify
the seven changed production files; blob computation wrote no Git objects.

| Production file | SHA-256 | Computed Git blob |
| --- | --- | --- |
| `ocr_crop_review_runtime.py` | `13c2a360266b58826bc0b5c88b2b782e5410dfaf5e9967e9908ac9be9b554229` | `ca0d182ae6145f2fdbf3febedabac8d5d0cc051f` |
| `ocr_crop_preview_supervision.py` | `15027da1b3f8d4899bcefddf0eacc3e7a753604ed77742e1a336f7754937ef3b` | `7f9aae01771805504b7d4a73c11b5a8d74d9a06b` |
| `ocr_crop_preview_worker.py` | `31bc09ee6547bc9c93744aa128b83c9ac22afe96551754d3e1aa5c8b0b29c060` | `447ee93a6ab2fc8595123e5534ea621242225251` |
| `ocr_review_execution.py` | `739e06d8ce5df9998322bb90018d4d244a443031c761d386732bf9d114a1eaea` | `65b9416ae8e4ca5ce6341eb4aa0084bd5c893595` |
| `ocr_review_crop_packs.py` | `027864ad3ed752db5d87eb0bb4b91a5df4e29066446a2534b416d840702194d6` | `6a92271d043f8178492b35efba3e767794c6677e` |
| `ocr_review_crop_ui.py` | `d0d51779ceb0ccbd451ff6f874df0a9bb02e587d1d43d4b7e4031c9a44534f2d` | `fbd5fe72a873f2dc0312b3f49d0dded79a41cf4b` |
| `ocr_review_crop_archive_ui.py` | `6ee0c096c2f9b8fb4b4a29960037c598957c9a34111eb30833474a2fa29b7cc0` | `f228bee69c4b28d3ef4cba30c0835f35702777d7` |

Original index and architecture baseline remain protected and unchanged.
No commit, staging, history rewrite or baseline promotion was performed.

## Verification

Qualified local Windows Python 3.12.10 was used with `-I -B` and
third-party pytest plugin autoload disabled. Root's runs disabled pytest's
cache provider; the consumer owner runs used its default. All fixtures are
generated/inert; no private PDF or OCR model was
used. Existing supervisor tests include generated native PDF/isolated-child
rendering. These are not new browser observations or memory measurements.

| Scoped run | Observed result | Terminal |
| --- | --- | --- |
| Runtime and direct coordinator | 334 passed in 79.48s | `6e4780` |
| Child worker | 109 passed in 12.24s | `b24d4e` |
| Host supervisor | 209 passed in 84.25s | `88513c` |
| Consumer disposal plus existing service/live/archive/save | 477 passed in 206.65s, including the 69 dedicated cases | `628208` |
| Eight surrounding existing review-workflow files | 593 passed in 299.27s | `1ecc57` |

Tests assert late refusal and cancellation identity, exactly-once close
attempts, no outer double-close after failed inner transfer, and successful
image usability. Two dedicated positive controls pass the exact live/archive
PIL outputs through the installed Gradio `Image.postprocess` and reopen its
generated PNG to verify dimensions/mode/pixels. They do not establish eventual
framework disposal of the original image.

The consumer combined run overlaps four files in the surrounding run; their
counts are not summed into a distinct-test total.

The eight surrounding files cover crop-pack service, live/archive panels,
live integration, Save controls, detail reload, reference feedback and its
component integration. All seven production and five modified/new test hashes
were identical before/after that run (`6f1f6d`, `dc477c`). Final Ruff passed on
those 12 files (`ec714e`). After both regression processes terminated, only the
dedicated test module's header was corrected to acknowledge the PNG controls
and close-attempt limitation. Its tested SHA-256 was
`8757e401558210358911ccff151111e2867b6f2fa14734bade8493af802e3406`;
the header-corrected SHA-256 is
`6a6a310ecd2e6c1b0aef4f58d0e0fcb620d81b99e26acaf02010e9e6f08d5e11`.
An exact inverse substitution reproduced the tested bytes (`33c07d`). No
test body or production code changed and no rerun is claimed for the header.

Root read the worker/supervisor source and appended test deltas. Anscombe
independently reviewed the runtime/coordinator changes and corrected fixture.
Socrates independently reviewed all three consumer sources and the dedicated
tests. These are agent reviews, not human approval or a release audit.
All scoped regression processes are terminal. Full current-generation gates
are still pending.

## Failed and intermediate checks retained

- A concurrent supervisor characterization attempt observed root's runtime file
  between two editing patches and refused collection with an indentation error
  (`18c0c6`). This was corrected before the held production generation; the
  later 209-case supervisor run is separate.
- The first runtime/coordinator run reported 330 passed and four failed in
  83.83s (`f06ba0`). The new cleanup-injection fixture targeted nonexistent
  controller `_cleanup_uncertain`, raising before the image could return.
  It now targets actual `_uncertain` inside a scoped patch, restoring only
  the simulated controller latch before real fixture cleanup while leaving the
  coordinator's propagated latch asserted. Production code was unchanged;
  the entire two-file run was repeated successfully.
- The consumer pre-change run reported 58 failed and three passed
  (`78f889`). Forty-six failures reached missing-close controls; twelve
  stopped at a test assertion that incorrectly expected an image update dict
  rather than the established cleared `None` output. That assertion was
  corrected. The first post-change run passed 61 tests in 56.07s
  (`9b5c4a`). The next pytest run passed 69 tests in 55.53s, but its composed
  shell exited one because subsequent Ruff reported five import/fixture-name
  issues (`7ab6e9`). Import-only test cleanup followed, Ruff passed
  (`af410b`), and all final dedicated cases were rerun in the separately
  passing 477-case run above. The shell failure is not hidden or reported as
  a successful terminal.

## Still outstanding

This repair does not explain the historical intermittent `input_changed`
refusal, establish a persistent-memory-leak diagnosis, or guarantee successful
disposal after `close()` raises. Eventual successful-output disposal inside
Gradio, other renderers, active-decoder cancellation and total-memory bounds
remain separate work. The disabled historical-refusal diagnostic was not run.

Full architecture/all-gate qualification of this new source generation remains
pending. Explicit source-bound reference uncertainty and the remaining
[67-requirement OCR/AI program](../ocr-improvement-program.md) remain in scope.
No representative OCR accuracy improvement or full-goal completion is claimed.
