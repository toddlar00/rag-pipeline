# Reference-uncertainty copied-source qualification, 2026-09-08

All nine local gates and the retained-artifact verifier passed. Pytest reported
**13,709 passed, seven skipped and six warnings in 2,180.71 seconds
(36 minutes 20 seconds)**. All 13,716 independently collected test identities
match JUnit; all 40 retrieval predicates passed. The runner's observed pytest
process duration was 2,196.328 seconds, a separate measurement from pytest's
reported duration.

This checkpoint qualifies the reference-uncertainty implementation and shared
UI dependency refactor within the local copied-source cohort. Earlier
[backend](2026-09-08-ocr-uncertainty-backend-development.md),
[pack/host](2026-09-08-ocr-uncertainty-pack-host-development.md),
[visual UI](2026-09-08-ocr-uncertainty-ui-development.md) and
[dependency-refactor](2026-09-08-ocr-uncertainty-ui-refactor-development.md)
records retain their individual source identities, generated checks, actual
Queue/browser observations and development failures. This full run does not
replace or enlarge the scope of those observations.

This is local Windows/Python-3.12 copied-source evidence, not an original
tracked-index gate pass, hosted CI, release or representative OCR accuracy
experiment. It does not complete the [67-requirement OCR/AI program](../ocr-improvement-program.md),
authorize correction publication or broaden AI-reader/corpus authority.

## Frozen cohort and protected original

The qualification base is
`%LOCALAPPDATA%/rag-pipeline/ocr-uncertainty-qualification-v2`.
Its `worktree/` contains **580 admitted files, including 423 Python sources**:
520 tracked original files plus 60 explicitly admitted additions. Exact names
and content identities are retained in `evidence/provenance.json` and the source
snapshots. Private PDFs, model caches, runtime outputs and original Git storage
were not copied.

- Original base HEAD: `e34103f70b676eacc8d55badb2468b8a10004ef4`;
  tree: `af0a69ccde82b6b679580f07813495721978efe0`. These alone do not
  identify the newer dirty-worktree implementation.
- Copy-owned synthetic HEAD: `21002d82fa062d3161315197a62e33773a1dfad6`;
  tree: `e889751ac8136ef30f634775db348f67487e0397`. This is not project
  history or a release commit.
- Original raw index:
  `6a841daa2ac15f397750fc0f88f1bd6e93b78326823d2452e3ab14c88d0335f3`;
  logical entries:
  `affc4116b70e1a0f5dceedab99de84135c9924226eb039df4ee5fbd4c69708f2`.
- Frozen copied raw index:
  `5c1022e735b6eb1ea12633fab67f59eddee46ebe8cfd1459e9c2ae59c9b05c62`;
  logical entries:
  `3459e2f37f37712f88689a4e503455311a0e99ee93d66193c11ac05e4b18906f`.

Original source/index/history and both source maps remained unchanged during
the gates and verification. The sole origin/copy source-byte exception is the
reviewed copied architecture inventory. The original architecture baseline
remains `8c9d9991aa6864131915b6f0392ae4bf4c3f9a4af5e8ae0757e78e9d47aa792e`;
the original default tracked-source inventory still excludes the additions.

The before/after source snapshots are byte-identical, SHA-256
`b1c95a394b5c16246e1a726112c42ca3ea3bf6cd9613a1570d9f4143abe5a602`,
computed Git blob `0e184dc75f4c29a34ce797ee43af42a33288cf4d`.
Computing that blob did not write an object. This record and its linked status
updates follow the frozen checkpoint and are not part of that tested cohort.

## Approved guard change and architecture review

The user explicitly approved the pending architecture-inventory cap increase.
Before preparing the new copy, root changed only the measured-size comment and
assertion in `tests/test_architecture_inventory.py`: **1,881,119 to 1,952,138
bytes**. That file is SHA-256
`8d5767bf8a1f428554077a2b16ca7209feee0a0b2d7fb8984e2abcfec248b38b`,
computed Git blob `855022d1289335732e4f852e699c11be450fe798`.
Canonical equality, semantic drift checks, the separate 4 MiB runtime-probe
output cap and OCR resource limits were not relaxed. The earlier missing-cap
approval condition is resolved; it is not a current blocker.

Two fresh candidate generations were byte-identical at **1,938,533 bytes**,
leaving **13,605 bytes** under the approved cap. They also exactly match the
pre-guard candidate previously reviewed by root and Peirce. That identity binds
the complete 209-entry/2,791-line content review; it is not a claim that the same
full reading was repeated after the guard change. The retained named-review
erratum corrects a helper-digest transcription, not candidate or source bytes.

Relative to the earlier ownership checkpoint, the reviewed inventory adds eight
modules and changes 11, removing none. It records 181 non-test modules, 4,129
functions, 315 classes and 591 acyclic all-context import edges (582 static-only).
Only source-architecture and tracked-source sections differ; seven other
sections, including facade/runtime/consumer contracts, remain unchanged.
The original baseline was not replaced: only the new copy received the candidate.

Socrates independently checked preparation and final provenance, including all
580 source files, exact additions, both Git bindings and copied index archives.
The fresh copy's own stat cache was primed before freezing; its decoded index
changed only the five initially-zero stat-cache fields, not object IDs or
extensions. Original index/history and older helpers, copies and evidence were
not refreshed or repaired. Peirce independently generated the second candidate,
confirmed exact source coverage/equality and approved copied-baseline finalization.
These are AI-agent reviews, not independent human transcription or release approval.

## Retained verification bindings

Paths below are relative to the qualification base's `evidence/`, except the
named runner and verifier retained under the original workspace's ignored `tmp/`.

| Artifact | SHA-256 |
| --- | --- |
| `copy-preparation.json` | `e942328069b0c973b25dade14ac486d96537104d84cde031499e2505b663d132` |
| `provenance.json` | `b76918d76a726e16ee73005fa98e5329c911d5b4ad0df37208eea0cf5694dfe7` |
| `finalization.json` | `835b2b6048ced54fc439bc406ca7e1320ec3502b2c2a02a02d1fe4aa1224326c` |
| Approved copied architecture | `669c406bf67102d49616ca8f48fe9faa657f3f94b2244e1837251e2cbec789b4` |
| Gate runner `ocr_uncertainty_frozen_gates_v2.py` | `1229e7e7dfb60f42805149576f3aeedd33f66e77111b7b91ee20c18410ecf546` |
| Verifier `verify_ocr_uncertainty_gates_v2.py` | `4cfb507ffe8e813a57b8f764af49c95c77c5706a259434080d96ec8fcdbe1c33` |
| `gates-v2/receipt.json` | `d719213e51a6edb9ffe1f8d623639419bc693210e8140791a7b236b26e123e0b` |
| `gates-v2/verification-addendum-v2.json` | `d196b7b9cf1cdfe4a0adeee7672aec1da7a9cd19497ded43e9222bbb903ead2f` |
| `gates-v2/collection.json` | `b9d751703155c0c1a2cdbe9df3a50cc79f59673356904ed7829e880bbc828028` |
| `gates-v2/pytest.xml` | `66603b24eb5bc69f526230b06221acb4ed1ef94d0cd8af2310f3377658723a26` |

The runner terminated with exit zero. The subsequent verifier also terminated
with exit zero after checking all 28 expected artifacts, the independent ordered
collection against JUnit, all nine gate commands/results, current CI thresholds,
input/report identities, the qualified executable and both source/index/HEAD/tree
bindings. It did not rerun collection, gates, retrieval, OCR or models, and did not
rewrite the original receipt. The addendum retains every artifact digest.

Anscombe's subsequent independent retained-only audit rechecked all 28 artifact
hashes before and after its reads, the collection/JUnit results, exact skips,
required Zettlr pass and all 40 predicates. It passed without rerunning any gate
or writing artifacts. It did not assert that the origin remained frozen after
the post-checkpoint documentation edits.

## Gate results

| Gate | Observed result |
| --- | --- |
| Full pytest suite | 13,709 passed; seven skipped; six warnings; zero failures/errors |
| Ruff | Passed |
| Python source compilation | Passed |
| Dependency policy | Passed |
| Model-artifact policy | Passed |
| Architecture inventory | Passed |
| Property retrieval | 12/12 predicates; success@1 1.000; nDCG@3 1.000 |
| Constitutional-law retrieval | 12/12 predicates; success@1 1.000; nDCG@3 0.971 |
| Table-family retrieval | 16/16 predicates; success@3 and recall@3 1.000; nDCG@3 0.877; MAP 0.833 |

Retrieval remains offline fixed-fixture regression evidence, not a new OCR
accuracy experiment. Baseline configurations and ordered query details match
the frozen inputs, and all required no-regression predicates passed.

The seven skips are the existing Windows/POSIX capability cases: one symlink
permission case, one newline-path case, three POSIX process-supervision cases
and two POSIX permission-mode cases. The real optional Zettlr multiline-reference/
srcset validation test **passed**, rather than being counted as a platform skip.
Warnings comprise five pytest iterator-parametrization deprecations and one
Starlette test-client/httpx deprecation; none was hidden or fixed in this run.

The qualified interpreter reports Python 3.12.10; collection reports pytest 9.1.1.
The runner scrubs Python/Git/pytest overrides, disables third-party pytest
autoload and optional Git index writes, and explicitly binds the tracked Zettlr
validator bundle. Installed Node/dependency bytes, other installed packages,
system site initialization and remaining local Git configuration are not
attested by that environment policy.

Preparation recorded 571 handle-ctime-only drift checks across 10,664 successful
reads. Independent preparation/final-provenance audits recorded zero; runner
and verifier also recorded zero such drift checks. No cause is inferred.
These are point-in-time content and identity checks, not continuous metadata,
atime, native loaded-byte or total-memory attestation.

## Remaining outcomes

Native live end-to-end uncertainty workflow, broader accessibility/usability,
independent human reference adjudication and approved representative held-out
accuracy remain outstanding. Baseline CER/WER semantics are unchanged; this run
provides no new OCR accuracy measurement. Earlier preview refusals and development
failures remain in their original records, without guessed causes.

All 67 requirement/acceptance pairs remain intact. The remaining independent
engine, adaptive-retry, downstream-evaluation, AI-access and publication outcomes
are not completed by this local checkpoint. New implementation requires its own
evidence; these post-checkpoint documentation updates do not expand qualification.
