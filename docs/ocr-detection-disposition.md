# Same-call missing-text diagnostics

Status: locally qualified through the
[Phase 10 checkpoint](evidence/2026-09-07-ocr-disposition-qualification.md):
9,958 tests passed, seven platform skips, six warnings and all nine selected
gates, with independent collection/source/artifact verification. Focused controls
cover all three routes, diagnostic-only faults and healthy peers after failures.
Separate generated posthoc verification preserves all 12 actual OCR candidates;
the original outer pixel comparison failed and remains incomplete. The record
retains both events and the failed first full gate run. This is local consistency
and compatibility evidence, not representative accuracy or complete-source proof.
Phase 9 does **not** qualify these later changes.

## What this explains

This opt-in workflow follows detections returned during the **same OCR call**
that produced a page, region or hard-scan candidate. It distinguishes blank
recognition, engine score filtering, retained output and unresolved outcomes.
It does not run a separate detector pass and pretend the results are the same
detections. Duplicate boxes or text retain separate ordinal identities.

The following remain separate:

- A raw call completed versus the final orchestrator accepted its candidate.
- An observed engine outcome versus an unavailable or partially observed stage.
- Engine-input coordinates versus available physical source geometry.
- Complete diagnostic accounting versus complete or accurate source text.

In particular, no returned detection does not establish that a page contained
no text. Empty recognition, filtering, failure and no OCR dispatch must not be
combined into one apparent zero-detection success. Returned detector scores are
not attached to detections: the characterized upstream sort does not preserve
their correspondence. Recognition scores are observations, not accuracy grades.

The first recipe observes the characterized full detection/classification/
recognition route with engine and reader score thresholds of zero and a
classification threshold of 0.9. Different observed settings make diagnostics
unavailable; they are not silently changed to obtain a supported result.
Invalid recorder ownership is refused before OCR dispatch. This is distinct
from an ordinary diagnostic failure during a valid call, which must preserve
the candidate and mark its diagnostic details unavailable.

## New execution and readback

Use the characterized, verified local Python 3.12 environment. The observation
recipe requires RapidOCR 3.9.2, NumPy 2.5.2, the fixed inspected upstream source
bytes and approved installed models. No models are downloaded. Optional
installation evidence adds its existing exact-environment bindings.

Example paths below are placeholders for approved private inputs and an
existing parent directory; no private corpus is selected implicitly.

```console
python tools/diagnose_ocr_dispositions.py --pdf approved/source.pdf --output-dir private/new-page-run --page 3 --max-pages 5
python tools/diagnose_ocr_dispositions.py --operation regions --pdf approved/source.pdf --recovery private/recovery.json --plan private/regions.json --output-dir private/new-region-run
python tools/diagnose_ocr_dispositions.py --operation hardscan --pdf approved/source.pdf --recovery private/recovery.json --plan private/hardscan.json --output-dir private/new-hardscan-run
```

Repeat the original options with `--verify-only` to validate an existing bundle
without OCR. The verifier uses the independently supplied current source,
policy, plan and producer generation; it does not take expected inputs from the
bundle's own declarations. Changing producer code invalidates that comparison.
Historical-reader migration would require a separately reviewed contract.

Page selection and preprocessing options retain their existing meanings.
Region/hard-scan operations accept their source-bound recovery and plan plus
300 or 400 DPI, not ignored page-only settings. This version admits no checkpoint
resume, engine-threshold override, arbitrary callback, implicit retry or
automatic correction adoption. The parent supervises a fixed worker with a
bounded deadline and verifies the original request digest before child work.

Exit codes are `3` for a verified bundle requiring review, `2` for failure,
`124` for timeout and `130` for cancellation. Native worker output is suppressed;
the CLI prints bounded counts and static errors, not OCR text or private paths.

## Private artifacts and interruption

The new directory contains:

- `work/report.json`: the unchanged normal orchestrator's private intermediate.
- `report.json`: an exact byte copy, published only after strict final-report
  and request validation. Existing report schemas and candidates are unchanged.
- `execution.json`: the existing execution-receipt schema, joined to actual
  raw dispatches and the exact final report bytes.
- `disposition.json`: the new closed companion with full item coverage,
  observed stages, independent candidate outcomes and recomputed summaries.
- `manifest.json`: the completion marker, published last, binding all final
  artifacts and the captured request/producer generation.

The intermediate and final report remain byte-bound during completion and
readback. Every final commit checks input and earlier-artifact fingerprints,
digests, bundle/work directory identity and exact staged bytes. Files are
private and create-only. Existing directories are not reused or recursively
removed. The bundle may also contain private lease bookkeeping.

Cancellation or a post-publication cleanup failure can leave partial files—or
even a manifest already linked before the error. Neither file existence nor an
exit code alone is completion evidence. Inspect with strict readback and retain
the partial directory; a new attempt uses a new directory and never silently
reruns completed OCR work.

## Bounds and interpretation

The recipe caps work at 20 calls, 1,000 returned detections per call and 20,000
per run. Text fingerprints, raster hashing, geometry and serialized output have
separate per-call/run limits. The compact companion is capped at 16 MiB including
its final newline. Full-coverage fallback space is reserved before OCR, so an
optional diagnostic refusal cannot omit a failed, deferred or unselected item.
These are selected work/allocation controls, not a total-process-memory guarantee.

Internal observation snapshots have a separate 64 MiB admission ceiling because
they retain both detection geometry and an independently observed raw-output
vector. The final companion still uses the 16 MiB ceiling: oversized diagnostic
details become explicit unavailable entries while candidate identities, actual
dispatches, consumed work and the entire requested coverage remain present.

Physical mapping reserves capacity for 128 vertices before invoking its helper.
A verified available polygon is charged at its actual size; failed or unavailable
mapping retains the reservation. Later diagnostic fallback never refunds earlier
work. This bounds output-vertex capacity, not every arithmetic operation or peak
memory. A failed method can contain a successful model call, so stage and model
role outcomes are reported separately rather than forcing them to agree.

Ordinary diagnostic failures should leave healthy OCR candidates unchanged and
make diagnostics explicitly unavailable. Genuine model/guard failures and
cancellation remain genuine failures. Unknown observations are not replaced by
zeros or inferred per-crop batch results. Input, report or receipt contradictions
fail completion rather than fabricate correspondence.

This is local consistency evidence, not signed execution attestation, human
approval or measured accuracy. Text digests and coordinates are still sensitive.
The companion does not publish corrections, rebuild indexes, extend AI access or
send scans to an external service. See the [full improvement program](ocr-improvement-program.md)
for remaining accuracy, review, AI evidence and correction-publication work.
