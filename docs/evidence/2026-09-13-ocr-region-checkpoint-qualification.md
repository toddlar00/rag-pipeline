# Region checkpoint copied-source qualification — 2026-09-13

**All nine gates passed; independent retained-artifact verification accepted.** The
[independent outcome](../../tmp/ocr_region_checkpoint_uncapped_qualification_v1/independent-retained-verifier-outcome-v1.json)
accepts the actual terminal evidence summarized below.

## Frozen source and development evidence

The [source freeze](../../tmp/ocr_region_checkpoint_uncapped_qualification_v1/source-freeze.json)
binds **632 source files: 452 Python, 520 originally tracked and 112 admissions**.
Both separately generated inventories are byte-identical at **2,001,767 bytes**.
The [named semantic review](../../tmp/ocr_region_checkpoint_uncapped_qualification_v1/architecture-review.json)
records **193 modules and 634 import edges, acyclic**: 625 static edges plus nine
retained dynamic edges. The reviewed inventory was installed only in the copy;
the retained finalization and provenance records
retain the admitted source and copy-only installation lineage.

The [development record](../ocr-checkpoints.md#generated-region-checkpoint-evidence-2026-09-13)
separately records **337 distinct focused cases and passing lint**, followed by
one independently reviewed generated three-region native run. Its cancellation,
resume and completed reopen returned **130/3/3**. Region 1 remained byte-exact,
region 2's durable start was consumed and sealed interrupted without a receipt
or another attempt, and only region 3 ran during resume. All 14 journal files
remained unchanged on completed reopen. These are preservation/runtime facts,
not representative OCR accuracy results.

## Terminal qualification and retained verification

**Gate outcome: All nine gates passed. The full suite recorded 14,626 passed, seven skipped and six warnings, with no failures or errors.**

**Independent verification: The retained verifier exited 0 with empty stderr. Its independently accepted readback rehashed all 28 artifacts, matched all 14,633 collected identities to JUnit, checked all 40 retrieval predicates (12 property, 12 constitutional-law, 16 table-family), and confirmed the real Zettlr validation case passed.**

Required source, index, baseline and Git-object readback, plus any nongating
metadata observations: **Required content, path/handle identity, source/index, baseline and Git-object joins passed for the original and copy, with only the approved copied architecture baseline differing. The runner recorded 1,204 nongating Windows handle-ctime check observations (128 retained details; 1,076 omitted), including repeated paths. The retained verifier recorded zero. These metadata observations do not denote content changes**.

The gate receipt and addendum below are under
`%LOCALAPPDATA%/rag-pipeline/ocr-region-checkpoint-uncapped-qualification-v1/evidence`.
Source-freeze and independent-outcome records use the repository qualification
control directory linked above.

| Principal record | SHA-256 |
| --- | --- |
| Source freeze | `9e6c29f5f71b850261d85307e63b2622ef8172b99d79fa0af0be03f537ac8e01` |
| `architecture-candidate-v1.json` and `architecture-candidate-v2.json` | `6a4aa0786defb4427e37352d3c8e50100ac68d517be66e9d39ad82c9e103cbd8` |
| `finalization.json` | `af987102a0ad327fefe341457d8c28059e9862516822f4d0bdbf6f2405c939f8` |
| `gates-v1/receipt.json` | `eabbc967c5533163465dd7c323ae3d3fcc4bd05bc7b1ba995374f90068d0afdd` |
| `gates-v1/verification-addendum-v1.json` | `3fd0279c46e5241981daae39d428e5fe49576c5727ec1e06566ac813c3f2871f` |
| Independent retained-artifact outcome | `336cf1f2a388bd2af8c56c0215d05e352b379a8f49bf50980937ad360895a923` |

## Scope

The qualification applies to the frozen copied source. It cannot
establish that the original checkout's default tracked-source gates or hosted
CI passed. Closing documentation follows that frozen snapshot and preserves all
67 requirement/acceptance pairs. Later hard-scan proposals are outside it.
The [earlier context qualification](2026-09-12-ocr-context-authoring-development.md#2026-09-13-copied-source-qualification),
its failed V12 restore and separately verified recovery remain unchanged in
their original record. Generated tests, native preservation and local gates do
not establish representative OCR fidelity or complete the broader OCR/AI program.
