# Source-cleanup qualification — failed attempt

Observation date: 2026-09-09. **This copied-source qualification failed.**
Pytest reported **14,259 passed, one failed, seven skipped and six warnings**
in 2,084.22 seconds. All eight other gates passed. Independent verification of
the retained failure matched all 14,267 collection/JUnit identities, 614 source
files, 28 artifacts and 40 retrieval predicates. This is evidence that the failed
record is consistent, not a qualification pass or test waiver.

The [source-aware cleanup observer](2026-09-09-source-cleanup-observer-development.md)
retains its independently reviewed implementation and 495 focused checks.
The [crop-advice checkpoint](2026-09-09-ocr-crop-advice-qualification.md) remains
the most recent passing full qualification; it does not qualify these later changes.

## Frozen scope and approved cap

The fresh copy contains 614 files, including 438 Python files: 520 original
tracked files plus 94 explicit admissions. The user approved the exact inventory
cap increase from 1,957,857 to **1,967,738 bytes**, with zero additional headroom.
Two generated candidates matched, and the full named architecture review passed.
The inventory has 187 modules and 607 acyclic edges; the only new module edge
is `rag` to `source_cleanup_audit`.

Only the original guard's cap/comment changed during finalization. The copied
guard and baseline received the reviewed values. The original baseline, original
history and both Git indexes remain unchanged. All nine gates ran once in the
copy. Before/after source snapshots are identical; every per-gate source/helper
flag remained true. The required real-Zettlr test passed. Existing platform
skips and warnings remain retained in the independent addendum.

## Failure and bounded follow-up

The failed node is
`tests/test_ocr_review_crop_preview_ui.py::test_opt_in_launcher_installs_only_fixed_preview_script_and_preserves_security[both]`.
At line 173, the assertion expected `review_ocr.main(args)` to return 0 and
observed 2. The launcher's stderr redacts the underlying exception. The retained
fixture representation is truncated and does not establish the failure location.

A separate diagnostic v1 refused at its combined child-startup binding check
before importing pytest or executing the test. Its parent then found no
telemetry. That bootstrap failure is retained; it does not identify which
binding predicate failed or explain the application failure.

A reviewed v2 used an exact bounded stdin intent channel and recorded controller
and direct-parent PIDs separately. It executed the existing node **once** in a
fresh private directory with the same frozen source and qualified interpreter.
The test passed in 0.21 seconds. Bounded telemetry observed one main call, one
pack-close call with the existing 40.0-second timeout, close returning true, and
main returning 0. No exception was observed; the trace was restored. Source,
Git, fixed-input and private-directory checks remained true.

**The original failure remains unexplained.** A fresh passing diagnostic cannot
establish its cause or replace the failed full-suite result. No production/test
repair, assertion change, test waiver or full-suite replay followed. Future
investigation needs evidence that distinguishes the failure conditions; repeated
uninstrumented passes would not do so. Historical temporary/cache contents were
not inspected, and all completed copies and artifacts remain immutable.

## Retained identities

Qualification evidence root:
`%LOCALAPPDATA%/rag-pipeline/ocr-source-cleanup-qualification-v1/evidence`.

| Artifact | SHA-256 |
| --- | --- |
| `gates-v1/receipt.json` | `7342eddc3acf81332d39a685e0dc388bd9bb6d590bb4be75cbb768af3f7c8386` |
| `gates-v1/failure-verification-addendum-v1.json` | `fc7675b2dd1530dd92df828c369497fa5a83c27b5049719d6395cfdd27ad601e` |
| `gates-v1/source-before.json` and `source-after.json` | `a9db3a36a277b1f9e2349554c996908f51bc23da45727a6fd7a01f63b6bc0c5f` |
| `provenance.json` | `8d1aaab03f51b5fb29c3134094e55d4f6401cf4e68daccb26898a7cfda1cc88c` |
| Copied architecture inventory | `5c7a37ac334452acf6eef3cb8b583b681565896b6b03e236a9e4c55899718600` |

Diagnostic roots are the distinct siblings `ocr-review-launcher-diagnostic-v1`
and `ocr-review-launcher-diagnostic-v2` under the same local application directory.
V1 receipt SHA is `f2639489318715027c4d80f17524e7023251d71b33c454475aa94fd83dd865cd`.
V2 receipt SHA is `662cfc5adc3030de7a88b02f712879f39aada70065068bc539c37da119a19ede`;
its telemetry SHA is `270cd5cf314d96ab954ffe5c843ed5702b7552861bda8a35864057d1121cb9ca`.
Root read the complete failure addendum and both diagnostic records after their
terminal outcomes. All runs are complete; none needs polling or replay.

These documentation updates follow the frozen checkpoint. All 67 ordered
requirement/acceptance pairs remain in scope. Later cleanup stages, representative
semantic evidence and the broader OCR/AI program remain unfinished; no release,
new corpus authority or representative accuracy claim is made.
