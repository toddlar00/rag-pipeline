# Preview image-ownership copied-source qualification, 2026-09-08

All nine local gates and the retained-artifact verifier passed. Pytest reported
**12,127 passed, seven skipped and six warnings in 1,549.92 seconds
(25 minutes 49 seconds)**. All 12,134 independently collected test identities
match JUnit. A final independent
agent audit also passed without rerunning any gate.

This checkpoint qualifies the same-crop preview image-ownership hardening:
failed ownership transfers attempt to close the image without replacing the
primary error or cancellation; successfully returned images remain usable.
It covers renderer, worker, supervisor, coordinator, pack-service and live/archive
callback paths. The [development record](2026-09-08-ocr-preview-image-ownership-development.md)
retains the seven production SHA-256/computed blob identities, scoped regression
checks, actual Gradio PNG postprocessing controls and earlier development failures.

This is local copied-source Windows/Python-3.12 evidence, not an original
tracked-index gate pass, hosted CI, release or representative OCR accuracy
experiment. It does not complete the [67-requirement OCR/AI program](../ocr-improvement-program.md),
authorize correction publication or broaden AI-reader/corpus authority.

## Frozen cohort and protected original

The private qualification base is
`%LOCALAPPDATA%/rag-pipeline/ocr-preview-image-ownership-qualification-v1`.
Its `worktree/` contains **548 admitted files, including 397 Python sources**:
520 tracked original files plus 28 explicitly reviewed additions (six production
modules, 14 test modules and eight documents). Exact names and SHA-256 values
are retained in `evidence/provenance.json` and the source snapshots. Private
PDFs, model caches, runtime outputs and original Git storage were not copied.

- Original base HEAD: `e34103f70b676eacc8d55badb2468b8a10004ef4`;
  tree: `af0a69ccde82b6b679580f07813495721978efe0`. These alone do not
  identify the newer dirty-worktree implementation.
- Copy-owned synthetic HEAD: `d791ea85919668ebff79b374943985c444aed5d2`;
  tree: `fb3001444d34eb319e5c95a3b4dd175bfe364304`. This is not project
  history or a release commit.
- Original raw index:
  `6a841daa2ac15f397750fc0f88f1bd6e93b78326823d2452e3ab14c88d0335f3`;
  logical entries:
  `affc4116b70e1a0f5dceedab99de84135c9924226eb039df4ee5fbd4c69708f2`.
- Frozen copied raw index:
  `505336de20ce52ab1e0b9c0867372ce2266f3b2cf9769d9a06c3f8934af23071`;
  logical entries:
  `33ff1aa8a50f4a5496cc4b1663d41c3632c0e9d7d7a6ad196d7101a2ca306b73`.

Original source/index/history and both source maps remained unchanged during
the gates and verification. The only origin/copy source-byte exception is the
independently approved copied architecture inventory. The original architecture
baseline remains `8c9d9991aa6864131915b6f0392ae4bf4c3f9a4af5e8ae0757e78e9d47aa792e`.
Original default tracked-source inventory still excludes the admitted additions.

The before/after source snapshots are byte-identical, SHA-256
`b1502bad1b68e6859fe0132b1889cce6a2c0ca5bacd223c7191c1f015f0b560e`,
computed Git blob `d3bc17f1d2406a7484260fcba6ea17da85d1a418`.
Computing that blob did not write an object. This record and linked status
updates follow the frozen checkpoint and are not part of that source cohort.

## Preparation and architecture

The new copy's own Git stat cache was populated with an explicit
`update-index --refresh` before freezing. Two subsequent ordinary
status/binary-diff/cached-binary-diff sequences returned empty results and
preserved its raw index. Its pre-refresh index was archived separately; the
original index and older qualification copies were not refreshed or repaired.

Two fresh inventory generations and a separate independent regeneration
produced the same 1,872,733-byte candidate. Root and Peirce reviewed the complete
36-entry keyed diff and presentation diff: seven existing ownership modules,
two private close helpers and one private operation signature changed.
There were no added modules/import edges or facade/runtime/consumer contract
changes. The inventory contains 173 non-test modules, 3,913 functions,
310 classes and 554 acyclic edges. The unchanged 1,881,119-byte guard retains
8,386 bytes of room. Only the copy received the approved candidate.

## Retained verification bindings

| Artifact | SHA-256 |
| --- | --- |
| `copy-preparation.json` | `839660f3b3ff6ec4b5d54354f8257b1bc347d9422f2c2b1f1e0e6dbc4f1cf51a` |
| `provenance.json` | `b88a2f8b15994dbe231e054f9ce7b7fd539fa3bf158266b7dbded4a3fda9d1dd` |
| Approved copied architecture | `3e5a8c0a68b52942cc1af4beb2b8fe357715cb19ff36fc58a842c65e251c90b5` |
| Independent architecture summary | `ceecf95b78a5bb5eb6f854a5f78af80cb1bf33ccac4c09c950d7b31bae4217da` |
| Gate runner | `8d7ce5bae50287e21d3b08cdc7894b15de31d41a4672c47faa9f4dc8c8564e5b` |
| Retained-artifact verifier | `8d6ca3b36183b32f8bca5904ec2f54f94cf1f5a2ef8860748938005ad6ccffa3` |
| `gates-v1/receipt.json` | `aa3ddf2cb0d6882cac15d394e905a411c887f9a034daaddb1682cb7f6cb1a3c5` |
| `gates-v1/verification-addendum-v1.json` | `3104362b3da2a6022a4c17c35951c95a61c38a4726552578d8e9a82cda8d59c8` |

All nine gates exited zero: pytest, Ruff, compilation, dependency policy, model
artifact policy, architecture inventory and three offline retrieval suites
(property, constitutional law and table family). Qualified Python 3.12.10
executable SHA-256 is
`0b471133e110cfb53a061cad528ce8e517d7b9ac41a0a396c39ad795a487fc14`;
pytest is 9.1.1. The runner observed 1,562.547 seconds for the pytest
subprocess; this differs from pytest's own 1,549.92-second test duration.

Independent collection retained 12,134 ordered unique cases, digest
`0e2c905666aa70dbdaefd03ebbc09336e23cd0c726f34cd47ef6d44656574c5a`.
The seven skips are explicit Windows/POSIX path, symlink, process-group,
watchdog and permissions differences. The required real Zettlr multiline/
srcset compatibility test passed. Five warnings concern deprecated pytest
parametrization iterators; one concerns the installed Starlette/httpx test client.

The success-only verifier ran once after the actual nine-gate terminal result.
It independently captured 28 retained artifacts, reconciled every collection/
JUnit identity, verified all **40 current CI retrieval predicates** (12/12/16),
and rechecked full-query/input and baseline-configuration bindings.
The generated offline retrieval results are regression evidence, not a paired
OCR-to-answer accuracy study. Anscombe's final
retained audit repeated the artifact, source/Git, collection and predicate
checks, including all skip/warning identities. It performed 3,392 bounded
double reads across 1,140 captured paths and 32 allowlisted Git identity/listing
queries; it did not run status/diff. Status hashes were compared as retained
declarations, not newly observed status output. Runner terminal: `c1477c`;
verifier terminal: `d65131`; final audit terminal: `232f79`, all exit zero.
The audit helper SHA-256 is
`dd12305732912c64693847dbfd465947255b7b4ab36c4542b7f3d9608cf75391`.
Agent audit is not human reference adjudication.

The runner recorded 210 within-handle ctime-drift checks (128 details retained,
82 omitted) under its predeclared Windows read policy; the verifier recorded
zero and the final audit recorded two checks on one original development
document, with unchanged content/path identity. These count checks, not distinct mutations. Path/content identity,
double-read bytes, raw-index freezes and source/HEAD/tree checks stayed strict.
These are point-in-time checks, not continuous metadata or loaded/native-byte
attestation. Installed Node dependencies and all native-library bytes are not
attested by this checkpoint.

## Preserved history and outstanding limits

The [prior crop-detail qualification](2026-09-08-ocr-crop-detail-qualification.md),
including its failed v1 copied-index freeze and separate successful v2, remains
unchanged. This new qualification used a fresh copy and one complete gate run;
no old pass was substituted, failed receipt overwritten or raw-index guard
weakened. The development record preserves earlier collection/fixture/disposal
failures and their scoped repairs separately from this passing frozen cohort.

A close attempt can still fail; this does not establish eventual framework
image disposal, persistent-leak absence, a total-process memory cap or the
cause of the historical intermittent `input_changed` preview refusal.
Explicit reference uncertainty/annotation history is next and is not implemented
by this checkpoint. Representative held-out accuracy, independent adjudication,
broader platform/accessibility and every other pending OCR/AI outcome remain
in scope. No private-corpus or model-acquisition authority was inferred.

