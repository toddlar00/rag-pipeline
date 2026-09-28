# Native uncertainty repair qualification: failed checkpoint

Date: 2026-09-08. The fresh copied-source run is **not qualified**: pytest
reported **13,727 passed, two failed, seven skipped and six warnings in
2,014.52 seconds (33 minutes 34 seconds)**. All eight other gates passed.
Independent retained-result review reconciled all 13,736 collected identities
with JUnit, including the exact failures/skips, and all 40 retrieval predicates.
No passing-verifier addendum was created or claimed.

This attempt follows the [native initialization/environment repairs](2026-09-08-ocr-uncertainty-native-development.md)
and the successful bounded [generated native round trip](2026-09-08-ocr-uncertainty-native-roundtrip.md).
The earlier [13,709-test passing checkpoint](2026-09-08-ocr-uncertainty-qualification.md)
predates these repairs and does not qualify them. Its evidence remains unchanged.
This record is local Windows/Python-3.12 evidence, not hosted CI, release,
representative OCR accuracy, or completion of the [67-requirement program](../ocr-improvement-program.md).

## Results and diagnosis boundary

| Gate | Exit | Result |
| --- | ---: | --- |
| Full pytest | 1 | 13,727 passed; two failed; seven skipped; six warnings |
| Ruff | 0 | Passed |
| Tracked Python compilation | 0 | Passed |
| Dependency policy | 0 | Passed |
| Model-artifact policy | 0 | Passed |
| Architecture inventory | 0 | Passed with the reviewed copy-only baseline |
| Property retrieval | 0 | All 12 configured predicates passed |
| Constitutional-law retrieval | 0 | All 12 configured predicates passed |
| Table-family retrieval | 0 | All 16 configured predicates passed |

The two failures are in `tests/test_phase_a0_benchmark.py`:

- `test_real_cli_info_proves_outer_supervision_and_empty_output_root`;
- `test_repository_runner_publishes_full_five_repetition_smoke_report`.

Both fail the `cli_info_empty` scenario at `tools/benchmark_phase_a0.py:2598`
with the static `phase-a0-probe-process-failure` classification. That branch
means nonzero probe exit or nonempty stderr, not a timeout/cleanup diagnosis.
The inner exception and guard trace are not retained by these tests: the probe
redacts its error and its temporary directory is cleaned. No Phase A0 timing
or memory report from this failed smoke test is accepted as a benchmark result.

Independent code reviews by Anscombe and Socrates support this hypothesis:
the benchmark explicitly supplies a generated `sitecustomize` guard through
trusted `PYTHONPATH` overrides for its probe and outer CLI, but the real nested
`rag.py` supervision call supplies no such overrides. The later inherited
Python-selector scrub therefore removes that startup path. This conflicts with
the benchmark's requirement for two linked, guarded outer/inner processes.
It is a code-backed explanation, **not a confirmed observation of the deleted
inner trace**. A new bounded diagnostic is needed before claiming root cause.

The proposed repair boundary is the benchmark-owned generated guard/bootstrap,
using the existing explicit trusted-override seam for its fixed startup mapping,
including generated `PYTHONUSERBASE`. Preserve ambient selector scrubbing,
actual supervision/containment/cleanup, both trace events and the negative
supervision-bypass control. No production/test repair or diagnostic rerun was
performed as part of this checkpoint.

## Offline retrieval measurements

| Fixed suite | Queries / judged | Success@1 | Recall@1 | Success@3 / Recall@3 | nDCG@3 | MRR |
| --- | ---: | ---: | ---: | ---: | ---: | ---: |
| Property | 8 / 7 | 1.000 | 1.000 | 1.000 / 1.000 | 1.000 | 1.000 |
| Constitutional law | 8 / 7 | 1.000 | 0.929 | 1.000 / 1.000 | 0.971 | 1.000 |
| Table family | 6 / 6 | 0.667 | 0.583 | 1.000 / 1.000 | 0.877 | 0.833 |

Property and constitutional law each include one abstention query. These are
network-free BM25 fixture checks, not OCR accuracy or live-corpus answer-quality
measurements. Their aggregate quality metrics match the previous retained run.
No fresh OCR accuracy experiment was performed here; the separate native round
trip's zero CER/WER covers only one generated 23-character title crop.

## Exact frozen cohort and architecture review

Base: `%LOCALAPPDATA%/rag-pipeline/ocr-uncertainty-qualification-v3`.
The private `worktree/` contains 584 admitted files / 424 Python sources:
520 original tracked files plus 64 explicitly admitted additions. The new
environment regression file and repaired editor tests are included. Private
PDFs, model caches, runtime outputs and original Git storage were not copied.

- Original base HEAD: `e34103f70b676eacc8d55badb2468b8a10004ef4`;
  tree: `af0a69ccde82b6b679580f07813495721978efe0`.
- Copy-owned synthetic HEAD: `6f3798aafb91eaa3cc4074a32a222480980b5708`;
  tree: `655795b8e8d300167a6bc08829c4db52104a1092`.
- Original raw/logical index SHA-256:
  `6a841daa2ac15f397750fc0f88f1bd6e93b78326823d2452e3ab14c88d0335f3` /
  `affc4116b70e1a0f5dceedab99de84135c9924226eb039df4ee5fbd4c69708f2`.
- Copied raw/logical index SHA-256:
  `14f1ad80ad04230ce0d8d58c1504aace7072e98fca9e077bbe347fddba8813d2` /
  `6ab5e06aca279c3c9d55d9b240af578c15ec91a7a545828cdc17203f31861143`.

The copied index alone was explicitly stat-cache-primed before freezing.
Independent binary audit checked all 584 entries, unchanged object IDs/extensions
and the six retained normal-command records. Both index archives are 58,623
bytes. No original index/history/baseline was changed. During the frozen run,
the sole source-byte exception between original and copy was the reviewed
copied architecture baseline.

Two unchanged-official-generator runs produced exactly identical candidate bytes:
SHA-256 `d735a227abf25b7df8fd0617ee0b8bf58a64423868aa33587249f2f42865a0de`,
1,938,592 bytes. The approved cap remains 1,952,138 bytes, leaving 13,546 bytes.
No cap increase was needed. Relative to the prior qualified candidate, only the
two repaired modules' location hashes/line counts and one added test path/count
change; all recorded facade/runtime contracts and the 181-module/591-edge
acyclic graph remain unchanged.

Socrates read the complete 4,011-line original-baseline diff; Anscombe separately
read all 6,607 keyed-diff lines. Peirce independently reproduced the candidate
and verified its full path/source/Git bindings. The [named review](../../tmp/ocr_uncertainty_architecture_review_v3/named-review-v1.md)
and full/narrow diff material distinguish carried-forward implementation from
the current repair delta. Architecture approval is not a passing test result.

## Retained evidence and limitations

All gate artifacts are under the base's `evidence/gates-v3/`. The root-owned
runner completed once, exit 1; no retry or result composition occurred. Its
pytest process duration was 2,030.469 seconds, distinct from pytest's own
2,014.52-second duration. Independent full collection took 9.891 seconds.

| Artifact | SHA-256 |
| --- | --- |
| `evidence/copy-preparation.json` | `1adaaa19c53058928834f48031816fb1fab8497fa354df8ab8f809e3b24df067` |
| `evidence/provenance.json` | `6ec11fa0293e531f48c362e774c0f2af9f6687be91a06cb1bc4503c77d47f01f` |
| `receipt.json` | `37fc06277de3b0d9360e5b24fa2f3ec6f873a3c57a228ff3455a0d6f35db369e` |
| `source-before.json` and `source-after.json` | `936411f18fddcb9c2d79607bcb2b4eed60fca8d3b85faa33eab4aad6e04afedb` |
| `pytest.xml` | `6777b39344e4bb0ab58c3d7b3bb6e67a1c2c9941e730f0d26c1b6795fbda310e` |
| `pytest.log` | `cd6476dc7ab7820ace0c4eee986f09714c19ca75c9973a508299f8aadaff6045` |
| `retrieval-property.json` | `f4e937cadf5b5fd3a03ea8317b492139ad2bc69403b5834439207bcc904ce859` |
| `retrieval-constitutional-law.json` | `764c7aee1ace49fe3d10de5099a7303c97fe497603101e97389a9c85aaa90c88` |
| `retrieval-table-family.json` | `3caa07ff231d336b9da89f43e764f8398ebae52d48c42930cef38e8ec6ee8ffe` |

The unchanged source snapshot's computed Git blob is
`a694085b3974823a14cdd2f7db97dcd8f19a2649`; computing it wrote no Git object.
The runner is pinned at `e4f89ac0c741662b91edf764ebbde101221aea11ab9426f1adca1edaaa29be12`.
The held success-only verifier remains unchanged and was not run.

Peirce's independent [retained-failure audit](../../tmp/ocr_uncertainty_qualification_v3_failure_audit.md)
completed successfully as an audit
of a **failed** run: all 28 artifacts, exact collection/JUnit identities, exact
failures/skips, required non-skipped real Zettlr test, nine exits, 40 retrieval
predicates and both complete source/Git bindings matched. Root terminal/hash
observations are `89b6f3` / `71d9dc`; the independent audit is `a629af`.
The audit note SHA-256 is
`cd931fb39023a140f97d0f33ed201296385d53c67cc287366b1c4725bd0358d2`.
Preparation recorded 380 handle-ctime-only observations in 10,716 successful
reads. The runner recorded 392 non-gating handle-ctime checks, with 128 details
retained and 264 omitted. These counts do not identify distinct files or edits;
no cause is inferred, and the exact byte/Git checks remained enforced.
These are point-in-time content checks, not continuous metadata, loaded-byte,
native-dependency, cross-platform or release attestation.

This new record and the ledger/program status edits follow the frozen run and
audit; they are not part of its tested cohort. The repaired implementation still
needs a passing fresh qualification after diagnosis/repair. Representative
sources/reference policy, human adjudication, broader usability and every other
outstanding requirement remain in scope. No model acquisition, canonical
correction publication, broader AI access or rate-limit reset was performed.
