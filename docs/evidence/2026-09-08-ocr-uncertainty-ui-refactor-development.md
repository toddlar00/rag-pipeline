# Uncertainty UI dependency refactor — development checkpoint

The static cycles found after [visual uncertainty development](2026-09-08-ocr-uncertainty-ui-development.md)
are removed through an inward shared state/validation module. This is local
development evidence, not full-project qualification or improved OCR accuracy.
All 67 outcomes in the [program](../ocr-improvement-program.md) remain in scope.

## Change and independent review

`ocr_review_crop_ui_common.py` now owns the existing bounded state classes,
field/context/image helpers, typed field errors and catalog metadata validation.
The three consumer panels retain their direct private helper aliases. Common
imports no panel, host, renderer or service; PIL remains a function-local import
used only when explicitly validating an image. Archive keeps its distinct stale
exception, 32-hex identifier policy and local 4 MiB display bound; shared defaults
remain 2 MiB. The live panel no longer imports archive merely to validate Save
metadata. No backend capability, consent, retry or publication behavior changes.

Peirce's named source review (terminal `a0a254`) checked the complete extraction
patch and current shared source. Independent AST comparison found all 16 moved
definitions and all 11 remaining top-level definitions (including nested event
callbacks/wiring) unchanged, apart from the declared archive regex rename and
removal of the live-to-archive local import. The comparison used root's complete
LF-normalized pre-extraction read reconstructions under
`tmp/ocr_ui_cycle_before_v1/`, not independently retained exact old bytes.
Function globals and class `__module__` intentionally move to common; no fake
module identity or dynamic-import concealment is used.

One existing negative serialization sentinel now patches both its consumer
alias and the helper's defining module, with a valid-value positive control
proving the hook remains effective. Existing callback-level monkeypatch aliases
remain usable. The first Ruff pass found an unused compatibility `_hash` import;
explicit re-export repaired that lint issue without deleting the alias.

## Frozen production and focused test identities

| File | SHA-256 | Computed Git blob (not written/staged) |
| --- | --- | --- |
| ocr_review_crop_ui_common.py | 67cc84473b628962f56cd16e80fe389abd2709231d93c46a03945cb6319f9fc5 | 30326db75db31371f577d742e7d051ee969b76b8 |
| ocr_review_crop_ui.py | e17ca1ac367de32aa321689ec3ef505f44d30d67ed2f205b00ffeab8ac5531ec | 083b76e677f048310e539312c418d399de1c339b |
| ocr_review_crop_archive_ui.py | 8300bf8bd42fd026ec947897409491c3b27223811888f56a0f8b7d7b20e8604b | 75800b0bef8d37740c7f1de259a11d889e9cda4d |
| ocr_review_crop_uncertainty_live_ui.py | 0eea67745dde168c4b686f352d5d24aff05d26c9e6da11c4f570125adc0d7ba1 | dc0b097064b064489134152ec1622ba9aaf9992b |
| tests/test_ocr_review_crop_ui.py | 3cd45cf3f3469232c225a6fb9b8b4a1729c08a5b2bcc6424bc2692391ba145a6 | 8de2f9493d699df23adacc5ff16f7dd7a947feea |
| tests/test_ocr_review_crop_ui_common.py | 8c5e1df59f294944b0f8359112320f149873cd9255f2feac6d9d1474e7e9fdf6 | f7b244e9d4e66bcb512fac989bdb0f7ecd1be7ef |
| tests/test_architecture.py | 5a15d352232f4bac1a1df1b939f235ca8adda2485781861c9db7440050424ff5 | 3cda358dcb08a3e55d3c96954fd908a97075e469 |

Qualified Python 3.12 was invoked with `-I -B`, plugin autoload disabled, and
pytest's cache provider disabled. All seven source/test files pass Ruff.

| Scope | Result | Retained JUnit SHA-256 |
| --- | --- | --- |
| UI, Queue, in-flight edit, lease, compatibility, field feedback and disposal; 18 explicit files | 1,004 passed; 0 failures/errors/skips; console 284.25s, XML 284.225s | tmp/ocr_uncertainty_ui_cycle_regression_v1.xml — c60791b33e4eb9fde888755a182635b5d14a72f6490854a5ee2ca7af0c52f394 |
| New shared seam; no Blocks/browser/native execution | 39 passed; 0 failures/errors/skips; console 0.35s | tmp/ocr_review_crop_ui_common_v1.xml — 4ec87b985696d852bb28ccda99acbfb01e717658dc59a9222988fb1944dc3d48 |
| Exact raw imports and fresh-process heavy-backend/live-host denial | 47 passed; 0 failures/errors/skips; console 3.46s | tmp/ocr_uncertainty_architecture_imports_v1.xml — 5129cbb78c180be43e43131523c950910967e9fdb2435ae8cb7c9a6ef913454d |

The 1,004-check producer session38757 ended with exit0 (`2c870d`); the console
result is retained at `6cfc8c`. Root parsed all 1,004 JUnit testcase nodes. The
39-check producer ended with exit0 (`b49651`), and root read its complete test
source and rehashed the XML. Its controls cover alias/type identity, fresh
subclass deepcopy without copied authority, revoke semantics, closed metadata,
exact authored strings, context binding, salted RGB identity, exception-safe
disposal, and a real isolated import with UI/heavy-backend attempts denied.

The 18-file regression command selected `test_ocr_review_crop_uncertainty`,
`test_ocr_review_crop_uncertainty_editor`, `test_ocr_review_crop_preview_ui`,
`test_ocr_review_crop_uncertainty_live_ui`, `test_ocr_review_crop_archive_uncertainty`,
`test_ocr_review_crop_archive_uncertainty_queue`, `test_ocr_uncertainty_inflight_edits`,
`test_ocr_uncertainty_edit_lease`, `test_ocr_review_crop_ui`,
`test_ocr_review_crop_ui_integration`, `test_ocr_review_crop_save_ui`,
`test_ocr_review_crop_archive_ui`, `test_ocr_review_crop_archive_integration`,
`test_review_ocr_execution_cli`, `test_review_ocr_crop_packs_cli`,
`test_ocr_crop_reference_feedback`, `test_ocr_crop_reference_feedback_integration`,
and `test_ocr_crop_consumer_disposal` (each under `tests/` with `.py` suffix).
The prior 830-check UI receipt overlaps and must not be added to these counts.

## Architecture and remaining acceptance

Anscombe's 47 focused architecture/import-denial checks ended with exit0
(`7b9c70`), with Ruff clean (`98d67e`). The independent supplemental admitted
AST check (`afbc7c`, exit0) inspected exactly 520 tracked plus 59 explicitly
untracked paths: 579 files, 423 Python files, 181 production modules and 582
edges, with no cyclic components. Exact guided-review inbound/outbound sets and
inward dependency closures passed. The later addition of this evidence document
adds no Python. Production bytes and original Git bindings stayed unchanged
through that observation. This supplemental graph is not the authoritative
tracked-source gate: tests retain tracked-only discovery, acyclicity and an
independent inventory-versus-AST equality check. A separate generated
candidate must still satisfy the unchanged 1,881,119-byte guard and independent
review before any copied-baseline installation or full nine-gate qualification.
No candidate size or full-suite result is claimed by this development record.

Original bindings remain HEAD `e34103f70b676eacc8d55badb2468b8a10004ef4`, tree
`af0a69ccde82b6b679580f07813495721978efe0`, raw index
`6a841daa2ac15f397750fc0f88f1bd6e93b78326823d2452e3ab14c88d0335f3`, logical index
`affc4116b70e1a0f5dceedab99de84135c9924226eb039df4ee5fbd4c69708f2` and architecture
baseline `8c9d9991aa6864131915b6f0392ae4bf4c3f9a4af5e8ae0757e78e9d47aa792e`.
The 67 joined requirement-name/acceptance rows retain SHA-256
`1b4a1f4a0e61011413337007bc2efbcdba3626d678d5f22b6dfc920081d7d698`.

The existing generated browser evidence remains scoped to its pre-refactor
producer; no new browser run or native live end-to-end result is claimed here.
Broader accessibility/usability, representative accuracy, independent human
adjudication and every other pending program outcome remain open. The original
Git index/history and architecture baseline were not edited. No private PDF,
new model, remote service or canonical extraction was used or changed.
