# Hard-scan checkpoint development — 2026-09-13

**Integrated focused checks and generated native interruption/resume/reopen passed; independent retained reviews accepted.** The actual
[focused review](../../tmp/ocr_hardscan_checkpoint_implementation_v1/independent-focused-outcome-review-v1.json)
and [native review](../../tmp/ocr_hardscan_checkpoint_implementation_v1/native/independent-native-outcome-review-v1.json)
bind the outcomes below.

Hard-scan checkpoints preserve work across cancellation or process interruption
for an explicitly approved recipe plan. They reuse the crop journal while
retaining distinct hard-scan records, original plan bytes, derived transforms,
origin receipts and the cumulative source-polygon vertex budget. The
[checkpoint guide](../ocr-checkpoints.md) documents commands, APIs and limits.

Before extraction, **eight characterization cases passed**, with all 24 test
phases and JUnit identities matched, Ruff passing and all 460 admitted pins
unchanged. One existing parametrization deprecation warning is retained. The
[independent BEFORE review](../../tmp/ocr_hardscan_checkpoint_implementation_v1/characterization/independent-before-outcome-review-v1.json)
accepted this original-behavior baseline. These eight cases are also included in
the later integrated selection; they are not a separate additional cohort.

## Integrated focused and generated native evidence

**587 distinct cases passed across all 14 modules**, with all 1,761 phases and
JUnit identities matched, Ruff passing and no failures, errors or skips. One
existing parametrization deprecation warning remains. This total includes the
eight BEFORE cases and 22 new checkpoint cases. The independently accepted
result retains exact agreement among the 463-pin admission, parent-start and
terminal source maps.

One contained native run used three approved 90-degree crops on page 1 of the
existing generated challenge. Cancellation after crop 2's durable start returned
**130**; resume and completed reopen each returned **3**. Crop 1's result and
crop 2's start bytes were preserved. Crop 2 was sealed interrupted with no
receipt or another attempt; its original raw-call count remains unknown. Only
crop 3 executed during resume, in origin segment 2. All **14 journal files**
remained unchanged on completed reopen.

The ordinary report retained **two candidates and one failure**, with no empty
candidates or abstentions. The derived plan recorded **13,227,840 planned pixels**
and the completed results retained **48 source-polygon vertices**. Both committed
receipts show one completed observed engine call in their original segment;
each has three constructed CPU sessions with intra-op 2 and inter-op 1. The
independent native review accepted the retained outcome, original/derived plan
bindings and runtime/source evidence.

The integrated selection covers ordinary hard-scan extraction, page/region
regressions, consumed attempts, canonical geometry/vertex accounting, origin
receipts, strict readback and CLI containment. Lint covered all eight changed
targets. Native evidence remains a separate generated preservation/resume run.

| Principal record | SHA-256 |
| --- | --- |
| BEFORE `ocr-hardscan-characterization-v1/receipt.json` | `638a6b21be07adb81e0e31e9b64a89ff1fd6662fee922c530ccfd7ba25a18a54` |
| Independent BEFORE outcome | `d697d3b8c2e65047e60d867a2c9b32f795e35aae44efb515287901bb6c373a4d` |
| Integrated focused receipt | `bee808ed146f50547c7ca9262dc23dc9e4c6b3fe88aa90af5556a3bda44b4ca5` |
| Independent focused outcome | `276fd02a4925217287cc683228481b2d2a80d99d47df79594c2000e11c8e9d9b` |
| Generated native observation | `4b1aca9d030273283f93ce9cfbabc3e384c896b47954b22e3d7b4f47c6e248be` |
| Independent native outcome | `8611ea832db16ef534cefdcd61303081a4a59954d87c3c915e781f2fa1ea0132` |

The test/native receipts are retained beneath
`%LOCALAPPDATA%/rag-pipeline/`; independent review and application
records are under `tmp/ocr_hardscan_checkpoint_implementation_v1` in the repository.

This later implementation is outside the [completed region qualification](2026-09-13-ocr-region-checkpoint-qualification.md).
Fresh copied-source qualification remains pending. Generated tests and native
preservation establish neither representative OCR fidelity nor accuracy
improvement, speed, signed attestation or completion of the broader OCR/AI
program. All 67 requirement/acceptance pairs and prior page, region and context
evidence remain preserved.


## Qualification failure and close-timeout repair — 2026-09-14

The first copied hard-scan qualification reported **2 failed, 14,654 passed,
7 skipped and 17 teardown errors**, with six warnings. The other eight gates
passed. The [independent failed-run review](../../tmp/ocr_hardscan_checkpoint_uncapped_qualification_v1/independent-failed-qualification-outcome-v1.json)
retains all 28 artifacts and the failed checkout. Both failures concerned close
confirmation, and all teardown errors reported leftover preview directories.
The original rejected timeout or underlying exception was not logged.

A separate deterministic regression reproduced a timeout arithmetic defect:
same-tick clock readings near a binary precision boundary can yield
`40.00000000001455` seconds, above the preview controller's strict 40-second
limit. Both new cases failed at close confirmation against the original source,
while their setup and teardown passed. After bounding the coordinator's remaining
join and preview timeouts at the existing limit, both cases passed with the real
preview controller and actual removal of its empty owned directory. The clock
substitution is confined to the coordinator. The [before review](../../tmp/ocr_review_close_timeout_repair_v1/independent-before-verification-v1.json)
and [after review](../../tmp/ocr_review_close_timeout_repair_v1/independent-after-verification-v1.json)
bind this reproduction and repair; they do not establish the unlogged cause of
every failure in the first full run.

All **606 cases passed across six affected review and preview modules**, with
all 1,818 test phases passing, no failures, errors, skips or warnings, and Ruff
passing. This selection includes both new regression cases and all 18 distinct
tests that had a failure or teardown error in the first full qualification.
The [repair validation review](../../tmp/ocr_review_close_timeout_repair_v1/independent-focused-verification-v1.json)
binds the affected test selection and source hashes. The production repair adds
137 bytes and one physical line, with no new function, class, module or import;
the two regression cases add 1,001 bytes and 19 lines in one existing test module.
Strict timeout validation, the shared deadline and ownership checks remain in
place. The 587-case and native results above are separate earlier evidence.
Fresh copied qualification of the repaired source remains pending.
