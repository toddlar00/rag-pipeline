# Source cleanup observer development — 2026-09-09

Status: independent source/test review, focused lint and all 495 focused checks
passed. Fresh architecture review and copied-source qualification remain pending.

The new opt-in collector records actual source-wrapper cleanup passes while
preserving their existing outputs, callback arguments, property reads and
exception behavior. The [API and report guide](../source-cleanup-audit.md)
explains the distinction between committed output edits, discarded passes and
auxiliary source normalization.

## Prechange observations

A reviewed helper captured 24 generated cases once, before production edits.
It retained 21 ordinary returns and three deliberately raised errors, together
with callback and scripted property/truth/conversion/lookup observations.
Root and an independent reviewer inspected the actual outcomes. Capture
completion records what happened; it does not establish semantic correctness.

- Helper: `tmp/capture_source_cleanup_baseline_v1.py`, SHA-256
  `79250ef57571368d7fbcdbe6cb141f477bef863c7011d88e9ba4b72a6f22caff`.
- Receipt: `tmp/ocr_source_cleanup_baseline_v1/baseline-receipt.json`, SHA-256
  `cb0954db857a71b94f8b5718bf91dd984a11e882a16a640570f42625b5d1212c`.
- Portable fixture: `tests/fixtures/source_cleanup_baseline_v1.json`, SHA-256
  `aa3e5d5b74b5f178de77f4ee39c21d5f216978eebc42ce786c709662d49120bb`.

The portable fixture retains all 24 inputs/outcomes and 282 stable ordered
observations, derived from verified artifacts without replaying the baseline.
It omits incidental profiler frame-local snapshots and private absolute paths.
Prechange `rag.py` SHA-256:
`9746038156805ee9493d06bd26bb97f6732f19baf9f3623cc37fc1781081bdba`.
Unchanged `chunking_core.py` SHA-256:
`7e30e05114e04a430ca1dcbc47deceab5d232d5d97d8951c0015f31d523c4938`.

## Review findings and validation

Independent source review identified stale copied-context writes into a
completed invocation, exception-type metadata access that could invoke a
custom metaclass, primitive-type membership that could invoke class equality,
and an output pass that could be marked complete without an observed genuine
core operation. Review of the first repairs also found that an open abstained
invocation/pass could lose its ancestry and reset the apparent nesting depth.
All identified defects were repaired before execution and covered by targeted
regression controls. The initial draft and intermediate review findings remain
retained; no execution result is claimed for those superseded source versions.

One root-owned focused run completed successfully after independent source and
test review. All 24 retained prechange cases matched on both default and observed
paths, including their exact output/error and ordered callback/property events.
Additional controls passed for ordered side effects, mutable legacy callables,
forward/reverse pass and core-operation replay, numeric/footnote branches,
Markdown/figure decisions, capture lifecycle and all fixed budget dimensions.

| Focused file/scope | Passed |
| --- | ---: |
| New source cleanup observer | 106 |
| Existing core cleanup audit | 64 |
| Existing cleanup audit IO | 44 |
| Existing cleanup audit CLI | 19 |
| Chunking core compatibility | 204 |
| Source fidelity core compatibility | 52 |
| Selected source-wrapper scaffold cases | 6 |
| Total | 495 |

Ruff passed for the changed source/test files. All 495 collected identities
matched JUnit exactly, with zero failures, errors or skips; pytest reported
3.95 seconds (the subprocess observation was 4.204 seconds). This is a focused
regression result, not a pipeline performance measurement. All 614 admitted
source files, the original index/history and helper pins stayed unchanged
through the run. The final verification phase recorded 1,134 successful source
reads and zero nongating handle-ctime changes. Temporary test contents remain
retained and were not inspected or deleted for this report.

- Focused runner: `tmp/run_source_cleanup_focused_v1.py`, SHA-256
  `6feb5c3d86e824052ac262ff28048511b816d7672dd0fad62e118fdecc8abeb3`.
- Receipt: `tmp/ocr_source_cleanup_focused_v1/receipt.json`, SHA-256
  `455ff6282df9fe8cd7a3e681c62272305e6ecf64522eef9a5d613e09fb92f634`.
- JUnit: `tmp/ocr_source_cleanup_focused_v1/junit.xml`, SHA-256
  `a64371042a3aac659e37d13ec9d5541c29d201c728da31915f616c849d63dcdb`.
- Superseding source review: `tmp/source_cleanup_observer_final_source_review_v2.md`,
  SHA-256 `ac32b15c29a0678e96342bc5e23ad00ae1f530fd0396602e06819434e157905a`.

The exercised source pins are `rag.py`
`8ee2cc449d7210ac3bac688303914592d4ac49ac2e05ad93db97d9e42e1ed562`,
`source_cleanup_audit.py`
`19d025e8b18786544e19f7260c6c6af75e2bcbf82421149cdc4d785ddb1965a4`,
and `tests/test_source_cleanup_audit.py`
`50998cf741748d6a2d6d1d714d4c3f6910209fc5ff5c775a22b9f0076c7664fa`.
The existing core and portable fixture retain their prechange pins above.
Fresh inventory candidates and copied-source qualification remain pending
under the approved 1,957,857-byte cap.

The original Git index, history and architecture baseline remain protected;
completed crop-advice and crop-edge evidence is immutable. These generated
mechanical checks do not complete the broader cleanup-fidelity requirement or
the 67-requirement OCR/AI program.
