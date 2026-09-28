# Hard-scan checkpoint copied-source qualification — 2026-09-14

**Fresh V2 qualification passed all nine gates and independent retained verification.**

## Retained first run

The first copied-source run failed: **2 failed, 14,654 passed, seven skipped,
six warnings and 17 teardown errors**. Pytest exited 1; the other eight gates
exited 0. The [independent failed-outcome review](../../tmp/ocr_hardscan_checkpoint_uncapped_qualification_v1/independent-failed-qualification-outcome-v1.json)
retains the two cleanup-confirmation assertion failures and the preview-root
teardown errors. All 14,663 collected identities are represented in JUnit; one
case has separate call-failure and teardown-error elements. No passing verifier
addendum was produced for that run.

The first run's frozen source had 637 files, including 455 Python files; its
2,010,977-byte inventory covered 195 modules and 646 import edges, acyclic.
Retained before/after source snapshots agree. The failed copy and its evidence
remain preserved. These observations do not establish a wall-clock cause.

| First-run record | SHA-256 |
| --- | --- |
| Gate receipt | `00d70863a84fb632bfd42d9b7e2b35b0b5fc3a4645e8ef669a245ea3b453f190` |
| Independent failed-outcome review | `7924a26c126433f3b549026d8e0bea87d5e7f83052a15ea4fbedb099833992a4` |

The first receipt remains at
`%LOCALAPPDATA%/rag-pipeline/ocr-hardscan-checkpoint-uncapped-qualification-v1/evidence/gates-v1/receipt.json`.

## Timeout repair and fresh qualification

The [hard-scan development record](2026-09-13-ocr-hardscan-checkpoint-development.md)
records the timeout repair and its actual focused results:
**two expected prechange failures, two postchange passes and 606 focused passes, with lint passing. The deterministic regression demonstrates binary floating-point rounding beyond the allowed timeout; it does not prove the unlogged cause of the historical failure**. Its earlier 587-case focused result and
generated native cancellation/resume/reopen at 130/3/3 remain separate evidence.

Fresh frozen source and official inventory: **637 source files, including 455 Python files, comprising 520 tracked files plus 117 admitted files and 16,685,064 bytes. Two sequential official generations produced the same 2,010,977-byte inventory: 195 modules and 646 import edges, acyclic, under the uncapped policy**.

Gate outcome: **all nine gates exited 0; 14,658 tests passed, seven skipped and six warnings, with no failures or errors. The retained collection and JUnit identities match exactly across all 14,665 cases**.

Independent retained verification: **accepted all 28 retained artifacts and 40 retrieval predicates, with the real Zettlr validation case passing. Both new timeout regressions passed in this full run**.

Source and protected-state readback: **original baseline/index/history and source bindings remained unchanged; only the approved architecture baseline changed in the private copy. The runner recorded 1,276 non-gating handle-ctime drift checks, retaining 128 details and omitting 1,148. The verifier recorded four checks before publication and four after publication. These counts are observations, not distinct mutations or proof of continuous metadata stability**.

Fresh controls are under `tmp/ocr_hardscan_checkpoint_uncapped_qualification_v2`.
The following evidence paths are relative to
`%LOCALAPPDATA%/rag-pipeline/ocr-hardscan-checkpoint-uncapped-qualification-v2/evidence`.

| Fresh record | SHA-256 |
| --- | --- |
| Source freeze in the control directory | `5fbcc461521f839312b8734453281418dea96131019a367dab7cfeedf4c6aa53` |
| Official architecture candidate | `4426a6f677dfde5bf8e5feb4fb0560264ee7d323619633fa8f6ad833b518ffb6` |
| `gates-v1/receipt.json` | `1ad3704bbfb07f1782a87e09d93ed125cfcadaa29f217032568f4c159bb989ac` |
| `gates-v1/verification-addendum-v1.json` | `115755215baa9e0c2b227448ad024a9b43d2c133a15044863975c55c18a73d8b` |
| [Independent retained outcome](../../tmp/ocr_hardscan_checkpoint_uncapped_qualification_v2/independent-retained-verifier-outcome-v1.json) | `79b2c7602456eb0227a260519a7bf5f9a606dd4a4ea3cb5314b7a7a0fd7b9587` |

## Scope

The fresh outcome applies to its frozen copied source. Closing documentation
follows that snapshot; all 67 requirement/acceptance pairs and the
[earlier region qualification](2026-09-13-ocr-region-checkpoint-qualification.md)
remain preserved. The [context history](2026-09-12-ocr-context-authoring-development.md#2026-09-13-copied-source-qualification),
including its failed restore and accepted recovery, is unchanged. Annotation-layer
proposals are outside this qualification. Generated fixtures and local gates do
not establish representative OCR fidelity or completion of the broader program.
