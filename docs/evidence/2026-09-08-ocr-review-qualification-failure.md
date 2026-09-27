# Save feedback and spot-audit qualification — failed attempt

Observation date: 2026-09-08. **This copied-source qualification failed.**
Pytest reported **13,970 passed, three failed, seven skipped, six warnings and
zero errors** in **2,714.51 seconds**. All eight other gates and all 40 offline
retrieval predicates passed. Those results do not override the failed pytest
gate. No success-only verifier or success addendum was run or published.

This attempt follows the separate [Save-feedback development](2026-09-08-ocr-save-feedback-development.md)
and [random spot-audit development](2026-09-08-ocr-spot-audit-development.md)
records. The [prior v4 passing qualification](2026-09-08-ocr-uncertainty-repair-qualification.md)
remains intact but does not qualify these later changes.

## Frozen scope and actual result

The isolated copy contains **597 files / 431 Python files**, comprising 520
original tracked files plus 77 explicit admissions. Only its architecture
baseline differs from the pinned original union. Two official generations of
the reviewed candidate matched: **1,951,646 bytes**, **492 bytes below** the
unchanged approved **1,952,138-byte cap**. Its 184 modules and 599 acyclic edges
are architecture evidence, not proof of implementation behavior or OCR accuracy.

The fixed evidence root is
`%LOCALAPPDATA%/rag-pipeline/ocr-review-qualification-v1/evidence`;
the execution root is its sibling `worktree`. All nine commands ran once.

| Gate | Exit | Runner-observed seconds |
| --- | ---: | ---: |
| Full pytest | 1 | 2734.843 |
| Ruff | 0 | 0.125 |
| Python-source policy | 0 | 2.313 |
| Dependency policy | 0 | 0.078 |
| Model-artifact policy | 0 | 0.141 |
| Architecture inventory | 0 | 37.813 |
| Property retrieval | 0 | 2.078 |
| Constitutional-law retrieval | 0 | 2.110 |
| Table-family retrieval | 0 | 2.063 |

All **13,980 independently collected node IDs** match the complete retained
JUnit identity multiset, including failures and skips. The required real-Zettlr
case `tests/test_ai_project_export.py::test_multiline_reference_and_srcset_fixtures_are_zettlr_valid`
passed, not skipped. Seven existing Windows/platform skips remain explicit in
the audit note. Test and runner clocks are separate observations, not throughput.

## Three failures and next repair

All three failures are in the copied `tests/test_architecture.py`:

- `test_guided_ocr_review_has_exact_inward_and_host_only_boundaries`: the common
  UI helper's exact inbound set omits its actual `ocr_spot_audit_ui` consumer
  (assertion at line 705).
- `test_crop_pack_direct_imports_have_only_explicit_stdlib_and_lazy_ui_dependencies[ocr_review_crop_archive_ui-first_party4-standard_library4-optional4]`:
  the archive import set omits `ocr_review_save_feedback` and standard-library
  `json` (line 871).
- `test_crop_pack_direct_imports_have_only_explicit_stdlib_and_lazy_ui_dependencies[ocr_review_crop_uncertainty_live_ui-first_party12-standard_library12-optional12]`:
  the live import set omits `ocr_review_save_feedback` (line 871).

Actual terminal tracebacks, not provisional progress markers, establish these
stale expectations for the already reviewed feature dependencies. The separate
inventory gate passed. A **test-only architecture repair is in progress** after
the failure audit released the source hold; no production change is included
in that repair. It must retain exact edge assertions, acyclicity and import
denials. **Neither a focused repair pass nor a fresh full pass is claimed here.**
Any repaired generation needs its own results; this failed copy is not rebased,
rerun or combined with other test results to manufacture a pass.

## Independent retained-failure audit and identities

The root-owned runner session 33662 ended exit 1 (`c0d8e6`). Peirce's independent
AI-agent failure audit ended exit 0 (`f2a79e`, 6.247 seconds), meaning the failed
record is consistent, not that qualification passed. It checked all 28 artifacts,
all 597 current source bindings, original/copy raw and logical indexes, HEAD/tree,
helper/executable pins, exact commands and all 40 retrieval predicates, with end
rechecks before source editing resumed. No tests, collection, gates, OCR or native
rendering were rerun. Only reviewed non-main verifier support and frozen copied
pure threshold functions were used. The full ledger and exact skip identities
are in the [retained failure audit](../../tmp/ocr_review_qualification_v1_failure_audit.md).

Runner metadata records **1,166 non-gating handle-ctime checks**, with 128 details
and 1,038 omitted; the failure audit observed four such checks. These are checks,
not distinct files, source mutations or causal explanations. Content, path
identity and all source/index freeze checks passed. Original status/diff was not
invoked; only the copy's stat cache was primed before freezing.

| Repository | HEAD | Tree |
| --- | --- | --- |
| Original | `e34103f70b676eacc8d55badb2468b8a10004ef4` | `af0a69ccde82b6b679580f07813495721978efe0` |
| Copy | `b063f000124050b36ccb40ca5e24d3ce58378c0b` | `90cfc9c7573d3a7a5416dca35c7f076594f8978f` |

| Binding | SHA-256 |
| --- | --- |
| Original raw index | `6a841daa2ac15f397750fc0f88f1bd6e93b78326823d2452e3ab14c88d0335f3` |
| Original logical index | `affc4116b70e1a0f5dceedab99de84135c9924226eb039df4ee5fbd4c69708f2` |
| Copied raw index | `01a945a12f9e0ea9fd456ac6d55027021254ddfc863661d2585001d1e1e78002` |
| Copied logical index | `e69c252adcdc01fd1da3ce71e4ff49fa8cf3093cc8c450708b68cd34a4305502` |
| Original architecture baseline | `8c9d9991aa6864131915b6f0392ae4bf4c3f9a4af5e8ae0757e78e9d47aa792e` |
| Approved copied architecture | `a1835ffadffe7a2044835376868b306352942dd7e106fc4283a7b6080df21ad9` |
| Preparation | `d8fa407d196149bffe66f10148d971e219ea55cec2a4a59cdb3e23b2014273f5` |
| Provenance | `3130cc725d689305a9e946ee35ec0f09aa83ea3beeef6946b34b9d1344d57f34` |
| Finalization | `96d95d0d4e94e11bac412b89274496264d1482d5d830e87b9405237eee58e41a` |
| `gates-v1/receipt.json` | `fb05325f65ff3f2d3bbe304ae9a2b060ec76052c073f46d63fccac8c61778d74` |
| Identical source-before/source-after | `e2e3373fafdaaa2a930b23f59bb6df4b13d1fb352ae8f147e2e39466346ea34a` |
| `gates-v1/collection.json` | `9c07c09e1d77b8fa22f30f3b1c29a51d0de790ebeca7a75ee98f435a8d5a82ff` |
| `gates-v1/pytest.xml` | `d5ad16e7c76e07b584f79feb4fd37879cba98d55361f9e5e083b326fa90d6527` |
| Frozen `tests/test_architecture.py` | `5a15d352232f4bac1a1df1b939f235ca8adda2485781861c9db7440050424ff5` |
| Runner `tmp/ocr_review_frozen_gates_v1.py` | `03fe2a4916a6fd3db8006ac310b851c0511054cd7a4c69f53541f695acf85859` |
| Non-main verifier support | `48eb2e0be04112f9d425e5d6e8262cc91a977d7b6d42a8a62cc317a9556acd91` |
| Independent failure-audit note | `e71954db6c9a246c02d883c6732415127f6559ccedd380f75ea978a46e7d905a` |

## Measurements and limits

The actual retained BM25 reports under `gates-v1/` pass their 40 existing CI
threshold/regression predicates. They are offline retrieval fixtures, not OCR.

| Report | Queries / chunks | Success@1 | Recall@3 | nDCG@3 | MRR |
| --- | ---: | ---: | ---: | ---: | ---: |
| `retrieval-property.json` | 8 / 11 | 1.000 | 1.000 | 1.000 | 1.000 |
| `retrieval-constitutional-law.json` | 8 / 11 | 1.000 | 1.000 | 0.971 | 1.000 |
| `retrieval-table-family.json` | 6 / 10 | 0.667 | 1.000 | 0.877 | 0.833 |

Existing [eight-page OCR evidence](../../evaluation/ocr/challenge-observation-v1.json)
remains **4.3414% CER / 5.7491% WER**, seven exact pages and 36/36 critical
occurrences matched; all four preprocessing modes were identical. The
[known-order correction](../../evaluation/ocr/layout-observation-v1.json)
reached zero CER/WER on that same synthetic cohort without recognition rerun.
Neither is new representative held-out evidence from this qualification.

The earlier **482 Save-feedback** and **419 spot-audit focused passes** and their
generated browser journeys remain separate, overlapping development evidence,
not additional unique full-suite cases. The Save hold is not a speed benchmark.
The spot-audit browser's `private_caches_absent: false` and retained unexpected
reparse/history entries remain unresolved; contents were not inspected and
nothing was deleted. Software gates do not qualify automatic cache cleanup.

The original Git index/history/baseline, prior copies and prior records remain
unchanged. Documentation and any test-only repair follow the audited frozen
attempt. Full qualification of the repaired generation, independent human
references, representative accuracy, broader accessibility/usability and all
other pending OCR/AI outcomes remain outstanding. No hosted-CI, release,
continuous loaded-byte attestation, canonical adoption or broader corpus/AI
authority is established; all 67 requirement/acceptance pairs are preserved.
