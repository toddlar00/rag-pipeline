# Resumable page, region and hard-scan OCR checkpoints

Page, region and hard-scan OCR can opt into a durable, private execution journal.
It commits each selected page or planned crop before starting the next attempt.
A later contained worker can reuse those exact results and continue unstarted
work after a deadline, cancellation or crash. Ordinary execution bundles,
closed recovery report v1/v2 formats, and ordinary region/hard-scan review
reports are unchanged.

All three operations support `--checkpoint`. UI drafts are a separate annotation
workflow, not execution checkpoints. No models or dependencies are downloaded
by this command.

## Start and resume

Use the same qualified interpreter and exact inputs/settings on both commands.
Page operations are the default:

```powershell
& C:/path/to/locked-env/Scripts/python.exe tools/run_ocr_experiment.py --checkpoint --pdf C:/approved/source.pdf --output-dir C:/existing-parent/page-checkpoint --max-pages 8 --installation C:/install-evidence/installation.json
& C:/path/to/locked-env/Scripts/python.exe tools/run_ocr_experiment.py --checkpoint --resume --pdf C:/approved/source.pdf --output-dir C:/existing-parent/page-checkpoint --max-pages 8 --installation C:/install-evidence/installation.json
```

The first command requires a new directory under an existing parent. Resume
requires an existing valid checkpoint request, never implicitly reuses an
ordinary bundle, and never changes the stored recipe. For pages, reordering repeated
`--page` arguments is equivalent; changing the requested set, DPI, preprocessing,
cap, source, evidence, locks, models, environment, or producer files is not.
`--resume` without `--checkpoint` is rejected.

For region checkpoints, supply the saved recovery report and the original
source-bound region plan:

```powershell
& C:/path/to/locked-env/Scripts/python.exe tools/run_ocr_experiment.py --checkpoint --operation regions --pdf C:/approved/source.pdf --recovery C:/review/recovery.json --plan C:/review/regions.json --output-dir C:/existing-parent/region-checkpoint --dpi 300 --installation C:/install-evidence/installation.json
& C:/path/to/locked-env/Scripts/python.exe tools/run_ocr_experiment.py --checkpoint --resume --operation regions --pdf C:/approved/source.pdf --recovery C:/review/recovery.json --plan C:/review/regions.json --output-dir C:/existing-parent/region-checkpoint --dpi 300 --installation C:/install-evidence/installation.json
```

Hard-scan checkpoints require the saved recovery report and an explicitly
approved recipe plan. Supply the original plan file with kind
`ocr_hardscan_plan`, approval `operator_approved`, and an orientation,
illumination and bow recipe for each crop. Checkpointing does not choose or
approve a recipe automatically.

```powershell
& C:/path/to/locked-env/Scripts/python.exe tools/run_ocr_experiment.py --checkpoint --operation hardscan --pdf C:/approved/source.pdf --recovery C:/review/recovery.json --plan C:/review/hardscan-plan.json --output-dir C:/existing-parent/hardscan-checkpoint --dpi 300 --installation C:/install-evidence/installation.json
& C:/path/to/locked-env/Scripts/python.exe tools/run_ocr_experiment.py --checkpoint --resume --operation hardscan --pdf C:/approved/source.pdf --recovery C:/review/recovery.json --plan C:/review/hardscan-plan.json --output-dir C:/existing-parent/hardscan-checkpoint --dpi 300 --installation C:/install-evidence/installation.json
```

Regions and hard-scan accept DPI 300 or 400 and require both `--recovery` and
`--plan`. They reject `--page`, `--evidence`, a nondefault `--max-pages`, and
preprocessing other than `none`. Keep the CLI defaults `--max-pages 5` and
`--preprocessing none`; that page-count default does not cap either crop plan
at five items. Original source, recovery and plan bytes must match on resume,
including JSON whitespace and ordering, along with the same recipe and runtime
bindings. An equivalent normalized plan does not replace the raw file identity.

The existing process-tree supervisor contains the worker. Native stdout/stderr
remain suppressed. Completion returns 3 because accuracy/manual review is still
required; errors return 2, deadlines 124, cancellation 130. These outcomes do not
assert that no private output exists. Inspect the chosen directory after failure.
The in-process API does not itself impose a deadline; use the CLI for containment.

## Attempts and origin evidence

Each selected page or planned crop gets at most one attempt in a generation:

- A committed result is reused byte-for-byte. It is never sent to OCR again
  during resume, including a committed empty candidate or ordinary retry failure.
- A durable start without a durable result is **interrupted**. A later exclusive
  lease holder seals it as interrupted without rerunning OCR. The engine may
  have run, completed, or never started; its outcome is unknown.
- Only items without a durable start are executed by the new worker.

Both crop operations identify items by canonical ordinal, exact case-sensitive
region ID and page number. The canonical plan is ordered by
`(page_number, region_id)`; distinct IDs on the same page remain separate attempts.
A result for one crop does not authorize reuse for another crop on that page.
Hard-scan items also retain the approved recipe and exact geometry/transform.

An interrupted page becomes `retry_failed` / `retry_runtime_failed` in the
unchanged closed recovery report; an interrupted region uses the same status and
error in its ordinary region review report. Separate checkpoint results and
completion coverage retain the explicit `interrupted` distinction. No missing
candidate is fabricated as an empty string. To deliberately reattempt an
interrupted or failed item, create a new generation and compare it on the same
references.

An interrupted hard-scan crop with a geometrically ineligible retained transform
projects its known abstention reason in the ordinary report; otherwise it
projects an ordinary retry failure. Its checkpoint state remains interrupted,
with no receipt and an unknown actual-call count. It is not a verified zero-call
commitment. A lost image-level abstention cannot be inferred from geometry.

Each committed result includes its original segment, candidate or ordinary
failure, and an item-specific execution receipt. Pages retain their baseline;
regions retain their exact identity and geometry, and hard-scan crops retain
their approved recipes and transforms. Each receipt binds the result,
its durable start, derived plan, request, and applicable original inputs. It
records only actual engine calls observed for that item, with actual constructed
session observations where available. A completed engine call does not prove a
valid/nonempty candidate. An interrupted result has no fabricated receipt.
The manifest summarizes calls by **origin segment**, not by the final resumer.
Unknown interrupted calls are listed separately, not counted as zero actual work.

A committed hard-scan geometry or image abstention has an actual origin receipt
with zero OCR calls. A committed candidate, including an empty candidate, needs
one completed observed call. An ordinary retry failure may have zero or one
observed call. These outcomes consume the attempt and are reused on resume.
Hard-scan receipts use operation `hardscan` and `hardscan_start_sha256`; their
input, request, derived-plan and outcome bindings remain operation-specific.

## Generation checks and limits

Before OCR, during each record publication, and at completion, the worker
rechecks source and applicable evidence/recovery/plan snapshots, all three
dependency locks, both model policy/lock files, requested recipe, interpreter
files, environment pathname identity, complete installed package/version
inventory digest, approved local
model bytes, and producer inventory. Requested OCR package versions must match
the committed full lock even without optional installation evidence. Supplied
installation evidence adds the existing complete local installation checks.

The conservative producer set contains every immediate root/tools Python file.
Editing or adding even an unrelated file in that set blocks resume. Tests and
documentation are not part of that producer set. This is consistency evidence,
not signed attestation, loaded-Python-byte proof, wheel/native-library validation,
or accuracy certification. Package metadata and local files remain editable.
References and held-out accuracy evaluation are separate inputs to evaluation.

One exclusive path lease covers the generation. All records are private,
single-linked regular files, published atomically with create-only hard links.
Input/output aliases, symlink components, malformed records, conflicting final
artifacts, and changed directory generations fail closed. Existing results are
not overwritten and no recursive cleanup is performed.

Pages retain the maximum of 20 selected pages and 5,000 inspected pages. Both
crop operations permit at most 20 explicit crops in a source of at most 5,000
pages. Each raw or processed raster is bounded at 25,000,000 pixels and 6,000
pixels per side. The complete plan is bounded at 100,000,000 pixels; hard-scan
counts each raw crop plus the processed canvas of every geometrically eligible
transform. These are work bounds, not measured memory limits. Saved recovery
input is capped at 64 MiB and each original crop plan at 1 MiB. Hard-scan
image-level illumination checks run during the individual retry before OCR.

Hard-scan source polygons share a limit of 100,000 retained vertices, with at
most 20,000 vertices and 1,000 OCR lines per crop. Resume validates results in
canonical order, reconstructs retained usage and gives later crops only the
remaining budget. Failed, abstained and interrupted outcomes add no retained
vertices. The budget does not reset on resume and a saved counter is not trusted.
A mapping-budget failure may discard a candidate after its OCR call; it does not
erase that call or authorize another attempt.

All three operations permit at most 64 execution segments, 256 immediate directory
entries, and 256 MiB total immediate artifact bytes. Their record limits differ:

| Record | Page checkpoint | Region checkpoint | Hard-scan checkpoint |
| --- | --- | --- | --- |
| One result | 8 MiB | 8 MiB | 8 MiB |
| Derived `plan.json` | 16 MiB | 1 MiB | 1 MiB |
| Ordinary `report.json` | 64 MiB | 128 MiB | 128 MiB |
| Request, start, segment or manifest record | 128 KiB | 128 KiB | 128 KiB |

Canonical UTF-8 JSON size is checked before staging and again before publication.
Temporary staging bytes count toward the generation limit. A report's individual
limit does not override the generation-wide byte budget during publication.

## Files and completion

`request.json` and `plan.json` have distinct checkpoint kinds; neither is a
completed OCR result. All three operations use immutable `segment-NNNN.start/end.json`
records with the existing common segment kinds. Item files and completion kinds
are specific to the adapter:

| Record | Page checkpoint | Region checkpoint | Hard-scan checkpoint |
| --- | --- | --- | --- |
| Request kind | `ocr_checkpoint_request` | `ocr_region_checkpoint_request` | `ocr_hardscan_checkpoint_request` |
| Plan kind | `ocr_checkpoint_plan` | `ocr_region_checkpoint_plan` | `ocr_hardscan_checkpoint_plan` |
| Item filenames | `page-NNNNN.start/result.json` | `region-NNNN.start/result.json` | `region-NNNN.start/result.json` |
| Start kind | `ocr_checkpoint_page_start` | `ocr_region_checkpoint_start` | `ocr_hardscan_checkpoint_start` |
| Result kind | `ocr_checkpoint_page_result` | `ocr_region_checkpoint_result` | `ocr_hardscan_checkpoint_result` |
| Completion kind | `ocr_checkpoint_completion` | `ocr_region_checkpoint_completion` | `ocr_hardscan_checkpoint_completion` |

Crop filenames use canonical ordinals, not page numbers or user-supplied IDs.
Matching filenames do not make region and hard-scan journals interchangeable:
the operation, every record kind and every input binding must agree. Baselines and
candidate text are sensitive and stay inside the private generation directory.
`report.json` is the ordinary operation-specific review report. `manifest.json`
is published **last** only after all selected items have committed or interrupted
outcomes and all segments are closed.

For both crop operations, input `plan_sha256` binds the **original plan file
bytes**. The journal's `plan.json` is a separate derived plan, bound by
`checkpoint_plan_sha256`. It records the ordered cohort, source-page dimensions,
crop/raster geometry and aggregate pixels; hard-scan also records approved
recipes and forward/inverse transforms. Original input hashes remain in the
request and ordinary report. Each receipt additionally binds the checkpoint
request, derived plan and its own start; these hashes are not interchangeable.

Before new hard-scan work, the complete crop cohort and every transform are
checked. When resume has unstarted crops, both operations describe the complete
cohort once before that execution segment's first new attempt and require exact
agreement with the retained geometry, including previously completed crops.
Hard-scan also rechecks every transform. Each individual retry retains its own
geometry/input checks. Resume may first seal an already consumed start as
interrupted.

Crash-left staging files with recognized private-writer names are preserved,
bounded, and never parsed or adopted as committed results. Other unknown entries
or unsafe links block resume. A crash before `request.json` commits leaves no
safely resumable generation. A post-link cleanup failure may leave a completed
file or a hard-link alias; inspect it rather than assuming publication failed or
blindly retrying.

If interruption occurs after `report.json` commits, resume adopts it only when
its exact bytes match a report reconstructed from every validated item record.
An already completed generation is read back without additional OCR. Completed
region and hard-scan readback also construct no PDF/OCR reader; they still
rehash original inputs and check current runtime and installed model bindings.
Hard-scan readback also reconstructs retained vertex usage. Parent readback
validates every origin receipt, all derived summaries, report bytes and manifest
bindings. An editable manifest alone is not an authorization or trusted import
boundary.

The page policy APIs remain in `ocr_checkpoint`; `checkpoint_request`,
`run_checkpointed_pages`, and `read_checkpoint_completion` in `ocr_checkpoint_io`
perform read-only preflight, leased execution/resume, and strict readback.
Region record policy lives in `ocr_region_checkpoint`; the corresponding
`region_checkpoint_request`, `run_checkpointed_regions`, and
`read_region_checkpoint_completion` APIs live in `ocr_region_checkpoint_io`.
Hard-scan policy lives in `ocr_hardscan_checkpoint`; `hardscan_checkpoint_request`,
`run_checkpointed_hardscan` and `read_hardscan_checkpoint_completion` live in
`ocr_hardscan_checkpoint_io` and perform the same three public roles.

Hard-scan preflight returns `(source, output, policy, specs, request)`. Its runner
returns a dictionary containing `manifest` and `report`. Completed readback takes
`output_dir`, keyword `request` and `specs`, and optional `installation_path`,
and returns the validated manifest. Existing page and region signatures remain
unchanged. `ocr_checkpoint_runtime` owns shared identity capture. The in-process
runners do not add a deadline; use the contained CLI when one is required.

## Observed generated-fixture smoke

A contained run in the qualified locked Python 3.12 environment was cancelled
through the supervisor's cancellation callback after page 2's durable start on
the existing eight-page synthetic challenge. The unchanged CLI returned 130;
the same request with `--resume` returned 3. Page 1's committed result bytes were
preserved, page 2 was sealed interrupted without a fabricated receipt or another
attempt, and pages 3–8 executed in the second origin segment. Final coverage was
seven retained candidates, one interrupted failure, and zero deferred, unstarted,
or empty pages.

All seven retained actual-call receipts passed the qualified installation,
model, and runtime checks. Their three constructed sessions reported
`CPUExecutionProvider`, intra-op 2 and inter-op 1. The source PDF hash was
unchanged. Private evidence is retained under
`evaluation-reports/ocr-checkpoint-smoke-v1/`; its completion manifest SHA-256 is
`c6bbcdf0831b7983974f26159b51187ab311d57b7d95fc62e6a344e7b01683b3`.
This demonstrates synthetic preservation/resume mechanics and runtime
compatibility, not representative OCR accuracy or held-out improvement.

## Generated region checkpoint evidence 2026-09-13

The [independent focused review](../tmp/ocr_region_checkpoint_implementation_v1/independent-final-focused-review-v1.json)
reconciled **337 distinct passing cases and passing lint**: 333 integrated cases
plus four separate boundary cases. The eight pre-extraction characterization
cases are already included in the 333. Coverage includes public writer lease and
input-alias refusal, retained origin/chronology checks, and actual UTF-8 storage
JSON above 64 MiB accepted by the region profile and refused by the page profile.
The transport case is not a large valid OCR report or a 128 MiB upper-limit test.

A separate contained native run used three regions on page 1 of the existing
generated challenge and its saved recovery. Cancellation after region 2's durable
start returned **130**; resume and completed reopen each returned **3**. Region 1's
committed result bytes were preserved. Region 2 was sealed interrupted without a
receipt or another attempt, and region 3 ran in the second origin segment. Final
coverage is committed ordinals `[1, 3]`, interrupted `[2]`, and no unstarted items;
the ordinary report has two candidates, one failure, and no empty candidates.
Completed reopen preserved every journal file's bytes.

Both committed receipts retain their original segment and one completed observed
engine call. Their three constructed sessions report `CPUExecutionProvider`,
intra-op 2 and inter-op 1. Region 2's original raw-call count remains unknown.
The original plan bytes retain their own digest, separate from the derived
geometry-plan digest.

Native records below are relative to
`%LOCALAPPDATA%/rag-pipeline/ocr-region-checkpoint-native-v1`.
The three CLI records and terminal `control/native-process-v1/outcome.json` retain
the actual exit codes; the latter completed with exit 0 and empty stderr.

| Record | SHA-256 |
| --- | --- |
| `evidence/observation.json` | `01c52ba5bc610676239848ce396aaaecc7fe2dad6514464c32d8a4bacbbcc4b8` |
| `generation/manifest.json` | `a40cda32a429f2958d100d7366891c0610d37a5a6f2bdfa3aba6db4becba139e` |
| `generation/report.json` | `423736fa05629eb88296878027013363c073130c672d07fa30063189b913545a` |
| `inputs/regions-plan.json` | `30cc0712fbc8db16e449d110a81f639a0d795ce5672fa9c74448e40687afa886` |

The [independent native retained review](../tmp/ocr_region_checkpoint_implementation_v1/native/independent-native-outcome-review-v1.json)
accepted these outcomes and bindings; its SHA-256 is
`271aba21272cc78953da3dd1b6a20b78097cc248f773d92c54f6f9b7f5c02305`.

This is generated preservation/resume evidence with local runtime bindings. It
does not establish representative OCR fidelity, accuracy improvement or signed
attestation. The later [copied-source qualification](evidence/2026-09-13-ocr-region-checkpoint-qualification.md)
has this retained outcome: **all nine gates passed, with 14,626 tests passed, seven skipped and six warnings; independent retained-artifact verification accepted**.
That record covers the frozen 632-file source and keeps focused/native evidence
separate from full gates. Hard-scan checkpoints and the broader OCR/AI program
remain pending.

## Hard-scan checkpoint development

The [hard-scan development record](evidence/2026-09-13-ocr-hardscan-checkpoint-development.md)
keeps the original eight-case characterization, integrated focused checks and
generated native interruption/resume evidence separate.
All **587 distinct focused cases and lint passed**; independent review accepted
the result. The generated native cancellation/resume/reopen returned **130/3/3**,
preserving the first result, sealing the second crop interrupted and running only
the third crop on resume. Independent retained verification accepted the native
result and unchanged completed journal.
The first [hard-scan copied-source qualification](evidence/2026-09-14-ocr-hardscan-checkpoint-qualification.md)
failed. The [development record](evidence/2026-09-13-ocr-hardscan-checkpoint-development.md)
contains the timeout-repair follow-up. Fresh V2 qualification passed all nine gates: **14,658 tests passed, seven skipped
and six warnings**, with no failures or errors. Independent retained verification
matched all 14,665 collected identities, 637 source files, 28 artifacts and
40 retrieval predicates; the real Zettlr validation case passed.
Representative OCR fidelity and the broader program remain pending.
