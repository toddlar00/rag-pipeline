# Save feedback and spot-audit copied-source qualification

Observation date: 2026-09-08. The fresh v2 copied-source checkpoint **passed all
nine local gates**: **13,980 tests passed, seven skipped, six warnings and zero
failures/errors**, in **2,303.30 seconds** of pytest time. Independent retained
verification matched all **13,987 collected identities**, **598 source files**,
**28 artifacts** and **40 offline retrieval predicates**. This qualifies the
bounded local source generation, not representative OCR accuracy or release.

The [failed v1 attempt](2026-09-08-ocr-review-qualification-failure.md) remains
immutable. Its three failures were stale exact architecture expectations for
reviewed Save-feedback and spot-audit dependencies. A test-only repair updated
those expectations and added seven strict import-boundary cases; production
was unchanged. The fresh complete run stands on its own, without combining
v1 results or focused results into a pass.

## Frozen scope and preparation

The execution root is
`%LOCALAPPDATA%/rag-pipeline/ocr-review-qualification-v2/worktree`;
its sibling `evidence` contains preparation/provenance and `gates-v1` results.
This fresh base intentionally retains the `gates-v1` leaf name. The copy contains
**520 original tracked files + 78 explicit admissions = 598 files**, including
**431 Python files**. The additional admission relative to v1 is its failure
record. Only the copied architecture baseline differs from the original union.

Two official inventory generations exactly matched the previously reviewed
candidate: **1,951,646 bytes**, **492 bytes below** the unchanged user-approved
**1,952,138-byte cap**. No guard relaxation, original baseline installation,
index/history change or original status/diff was used. Only the newly owned
copy's stat cache was primed before preparation; pre/post raw index archives
and the final pre-gate archive remain retained.

Before finalization, the complete architecture file passed **77 tests**, with
zero failures/errors/skips/warnings in **79.46 seconds**; its complete collection
and scoped Ruff also passed. All seven new IDs match JUnit. The
[independent focused audit](../../tmp/ocr_review_architecture_focused_v2_audit.md)
is separate development evidence, not 77 additional unique full-suite cases.

## Actual complete run

Root-owned runner session 62751 ended exit 0 (`67c80a`). The independent full
collection preflight found **13,987 nodes in 14.234 seconds**. All nine commands
then completed once, with source/helper/executable freeze markers true.

| Gate | Exit | Runner-observed seconds |
| --- | ---: | ---: |
| Full pytest | 0 | 2318.516 |
| Ruff | 0 | 0.125 |
| Python-source policy | 0 | 2.391 |
| Dependency policy | 0 | 0.078 |
| Model-artifact policy | 0 | 0.125 |
| Architecture inventory | 0 | 27.359 |
| Property retrieval | 0 | 2.125 |
| Constitutional-law retrieval | 0 | 2.156 |
| Table-family retrieval | 0 | 2.109 |

The required real-Zettlr case
`tests/test_ai_project_export.py::test_multiline_reference_and_srcset_fixtures_are_zettlr_valid`
passed, not skipped. The seven existing skips concern Windows symlink
permissions, newline-path restrictions, three POSIX watchdog/process-group
cases, and two POSIX-mode versus Windows-DACL cases. Their exact identities and
reasons are retained in the verification addendum. The six warnings are five
pytest iterator-parametrization deprecations and one Starlette/httpx test-client
deprecation; they are not suppressed or reclassified as passes.

Pytest's 2,303.30 seconds and the runner's 2,318.516 seconds are separate clocks.
The earlier serial v1 pytest took 2,714.51 seconds. No parallel execution or
production optimization was introduced between these runs; this difference is
**not an established speedup**. A performance comparison needs a matched scope,
serial baseline, repeated measurements and order/load controls.

## Independent verification and identities

Peirce (`/root/ocr_accuracy_review`), an independent AI agent rather than a human
reviewer, executed the fully read, independently reviewed success verifier once.
It ended exit 0 (`a72fc9`, 5.296 seconds), publishing the create-only
`gates-v1/verification-addendum-v1.json`. Subsequent retained readback
(`7c87e4`, `eea606`) completed before root released the source hold for these
documentation-only updates. No gate, collection, OCR or retrieval was rerun by
the verifier. Root independently rehashed its addendum and completed receipt.

Verification checked external receipt/runner/architecture/provenance pins,
exact 28-artifact coverage, collection/JUnit identity equality, all nine current
CI commands and all 40 threshold/regression predicates. Original/copy source
bytes, raw and logical indexes, HEAD/tree, the sole copied-baseline exception,
live/archived helpers and the qualified executable matched at verification and
before publication. These are point-in-time checks, not loaded-byte attestation.

Runner telemetry records **680 non-gating Windows handle-ctime observations**:
128 details retained and 552 omitted. The verifier recorded **two**, both
labelled `origin:ocr_review_drafts.py`, with the same total before and after
publication. These count checks, not distinct mutations, files or actors;
no cause is inferred. Pathname/content-identity, byte equality and source/index
freeze checks remained gating and passed.

| Repository | HEAD | Tree |
| --- | --- | --- |
| Original | `e34103f70b676eacc8d55badb2468b8a10004ef4` | `af0a69ccde82b6b679580f07813495721978efe0` |
| Copy | `7955ba06897cb56e42102431976565d9ee3fb8dd` | `10c73d4273bff822eb6e481e9e95e5cbb59bc767` |

| Binding | SHA-256 |
| --- | --- |
| Original raw index | `6a841daa2ac15f397750fc0f88f1bd6e93b78326823d2452e3ab14c88d0335f3` |
| Original logical index | `affc4116b70e1a0f5dceedab99de84135c9924226eb039df4ee5fbd4c69708f2` |
| Copied raw index | `237faf7e3ecc3a30cfdcec232ce208c5fce0a203669a21c042fddd23df8f5edc` |
| Copied logical index | `ca9e70f892417b8debbcc4c0cfe4e4398078b414a63e41331687f9db38241849` |
| Original architecture baseline | `8c9d9991aa6864131915b6f0392ae4bf4c3f9a4af5e8ae0757e78e9d47aa792e` |
| Approved copied architecture | `a1835ffadffe7a2044835376868b306352942dd7e106fc4283a7b6080df21ad9` |
| Preparation | `b593d05d7e1dfee25086143c4c7dc0e6cf61436e849453fc8f7998184485ca38` |
| Provenance | `5694161f932bf478b7671432193f0fba52b9f5d89967295acd6ffc37eff6c72c` |
| Finalization | `229880a888711550593496682d54e989ea9be879fcfd9366be00373cd6483f3f` |
| Full receipt | `6fab0248da779d42f8b6200ef9c867e883b9ace140caa14ff683cc21dd86f948` |
| Verification addendum | `816130effa8cebb6f77e62407ffd09f54a22f3860d546a03afd7f9033218eab7` |
| Identical source-before/source-after | `6035ae0d4ade4adefd656f625ffe23c74188ca37b85e6838f3047740606243b3` |
| Collection JSON | `ef8d077d9c56f8fa88f20d3deca8c00f485e31b97dc7a26df0b30d1497a3ec50` |
| JUnit XML | `5a30c1ad31ede3587a3a1eeabd0d553e6a233419a40b8228ffe906a83b854183` |
| Runner `tmp/ocr_review_frozen_gates_v2.py` | `0f895d5f13f5f4daadccbd01feca062aa75a6148b2b0a9fa53014c1d15de2371` |
| Verifier `tmp/verify_ocr_review_gates_v2.py` | `a03c13838661d8000554410e1a0f82b89c74e05182d56597618f6cfa5394a7d4` |
| Repaired architecture test | `f11fd0a1f1a5bfa58da65675048918a26640a78b340df6d5028c580546e1b7d4` |

The identical 188,344-byte source snapshot has computed Git blob identity
`8cd3f4dfeffbead50f9ec3f8ef4b92b4f2962068`; this was calculated without storing
an object or modifying an index. The
[v2 handoff](../../tmp/ocr_review_qualification_v2_handoff.md) retains the fuller
copy/generator/finalizer and focused-audit ledger. The qualified Python
executable SHA remains
`0b471133e110cfb53a061cad528ce8e517d7b9ac41a0a396c39ad795a487fc14`.

## Measurements and remaining limits

The three retained BM25 reports in `gates-v1/` passed all 40 existing CI
threshold/regression predicates. These are offline retrieval fixtures, not OCR.

| Report | Queries / chunks | Success@1 | Recall@3 | nDCG@3 | MRR |
| --- | ---: | ---: | ---: | ---: | ---: |
| `retrieval-property.json` | 8 / 11 | 1.000 | 1.000 | 1.000 | 1.000 |
| `retrieval-constitutional-law.json` | 8 / 11 | 1.000 | 1.000 | 0.971 | 1.000 |
| `retrieval-table-family.json` | 6 / 10 | 0.667 | 1.000 | 0.877 | 0.833 |

The separate [482-test Save-feedback development](2026-09-08-ocr-save-feedback-development.md)
and [419-test spot-audit development](2026-09-08-ocr-spot-audit-development.md)
records retain overlapping focused/browser evidence. Their generated workflows
do not establish representative accuracy, independent human review, measured
Save speedup or broader assistive-technology coverage. Existing synthetic OCR
benchmarks are unchanged; this checkpoint adds no new OCR accuracy result.

The spot-audit browser's `private_caches_absent: false` and retained unexpected
reparse/history entries remain unresolved. Contents were not inspected and
nothing was deleted; these gates do not qualify automatic private-cache cleanup.
Confidence remains an eligibility signal, not evidence of transcription accuracy.

These documentation-only updates follow the frozen source qualification; they
are not retroactively included in its 598-file snapshot. The original default
tracked-source gates, original Git baseline/index/history, old copies and
evidence are not rebased or represented as newly qualified. All **67 requirement/
acceptance pairs** and broader pending outcomes remain intact. No representative
held-out accuracy gain, human adjudication, canonical adoption, expanded private
corpus/AI-reader authority, hosted-CI result, release status or continuous
metadata/native-loaded-byte attestation is established.
