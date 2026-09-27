# Native uncertainty repairs: copied-source qualification

Date: 2026-09-08. **All nine local gates and independent retained-artifact
verification passed.** Pytest reported **13,749 passed, seven skipped and six
warnings in 2,060.69 seconds (34 minutes 20 seconds)**. All 13,756 independently
collected identities match JUnit; all 40 retrieval predicates passed. The
runner's separately observed pytest process duration was 2,075.703 seconds.

This fresh v4 checkpoint qualifies the later native-uncertainty initialization,
child-environment and benchmark-guard repairs within its frozen copied-source
cohort. The [failed v3 qualification](2026-09-08-ocr-uncertainty-repair-qualification-failure.md)
remains failed: its two probe failures are not rewritten as passing. The later
[generated diagnostic and scoped repair](2026-09-08-phase-a0-guard-repair-development.md)
confirmed the nested guard-bootstrap incompatibility and separately passed
168 scoped tests. This new full run passes all 77 Phase A0 benchmark tests,
including both formerly failing cases and the five-repetition smoke test.
Production inherited-environment scrubbing was not weakened.

The [native browser round trip](2026-09-08-ocr-uncertainty-native-roundtrip.md)
retains its own two generated OCR calls, unresolved review/Save, fresh-host Open,
explicit resolution and immutable child Save. This full-suite run does not
enlarge those browser observations or establish new OCR accuracy measurements.
It is local Windows/Python-3.12 copied-source qualification, not an original
tracked-index gate pass, hosted CI, release or completion of the
[67-requirement OCR/AI program](../ocr-improvement-program.md).

## Actual gate results

| Gate | Observed result |
| --- | --- |
| Full pytest suite | 13,749 passed; seven skipped; six warnings; zero failures/errors |
| Ruff | Passed |
| Python source compilation | Passed: 424 Python files |
| Dependency policy | Passed |
| Model-artifact policy | Passed |
| Architecture inventory | Passed |
| Property retrieval | 8 queries; 12/12 predicates; success@1 1.000; nDCG@3 1.000 |
| Constitutional-law retrieval | 8 queries; 12/12 predicates; success@1 1.000; nDCG@3 0.971 |
| Table-family retrieval | 6 queries; 16/16 predicates; success@3 and recall@3 1.000; nDCG@3 0.877; MAP 0.833 |

These are fixed offline retrieval regression suites, not representative OCR or
answer-quality experiments. Baseline configurations, ordered query details and
all required no-regression predicates matched the frozen inputs.

The seven skips are the existing Windows capability exclusions: one symlink
permission case, one newline-path case, three POSIX process-supervision cases
and two POSIX permission-mode cases. The required real Zettlr multiline-reference/
srcset validation test passed. Warnings are five pytest iterator-parametrization
deprecations and one Starlette test-client/httpx deprecation; none was suppressed
or repaired in this run.

## Frozen source and protected original

Qualification base:
`%LOCALAPPDATA%/rag-pipeline/ocr-uncertainty-qualification-v4`.
The private `worktree/` contains **586 admitted files / 424 Python sources**:
520 original tracked files plus 66 explicit admissions. Exact names and hashes
are retained in `evidence/provenance.json` and the source snapshots. Relative
to failed v3, five shared source/document files changed and two evidence records
were admitted; no other cohort drift was accepted. Private PDFs, model caches,
runtime outputs and original Git storage were not copied into the cohort.

- Original HEAD: `e34103f70b676eacc8d55badb2468b8a10004ef4`;
  tree: `af0a69ccde82b6b679580f07813495721978efe0`.
- Copy-owned synthetic HEAD: `9f44bfcb608485a3a365cd6e8fde7cc988e3d369`;
  tree: `52741d085db9d193e9f717ee0660dd0c5fec9086`.
- Original raw index: `6a841daa2ac15f397750fc0f88f1bd6e93b78326823d2452e3ab14c88d0335f3`;
  logical entries: `affc4116b70e1a0f5dceedab99de84135c9924226eb039df4ee5fbd4c69708f2`.
- Frozen copy raw index: `0455b0e8cf17c31f798b534bc2a10f70c615b7abcbcba2a1a44060404d1f22b3`;
  logical entries: `6b1e198a62a3b065e249b75f995230193f59a7326b5a2d1051425840e5180378`.

Original source bytes, indexes and history remained unchanged during the gates
and verification. The only origin/copy source-byte exception is the approved
copied architecture baseline. The original baseline remains
`8c9d9991aa6864131915b6f0392ae4bf4c3f9a4af5e8ae0757e78e9d47aa792e`;
its default tracked-source inventory does not include these admissions.
The synthetic copy commit is not project history or a release commit.

Before/after source snapshots are byte-identical, SHA-256
`42f7b44ce6603a550afeb28474d4b83258ea392371e81b7a78765ec12f27da01`,
computed Git blob `d03e655497f0dfed03060a50cedb2c948cc17692`.
Computing the blob did not write a Git object. This record and its status-link
updates follow the frozen checkpoint and are not part of that tested cohort.

## Architecture preparation and review

Two fresh generations produced the same canonical candidate and identical
generation receipts. Candidate size is **1,938,831 bytes**, leaving **13,307
bytes** under the unchanged, previously user-approved 1,952,138-byte cap.
The reviewed 14-leaf delta from v3 adds one benchmark helper/signature change
and associated counts/locations, plus four shifted test-consumer locations.
It adds 239 bytes; the 424 Python paths, 181-module/591-edge acyclic graph and
facade/runtime contracts remain unchanged. No further cap increase occurred.

Socrates independently checked all 586 original/copy bytes, admissions, Git blob
bindings and retained index archives. Copy-only stat-cache preparation changed
only the five initially-zero stat fields per entry, not object IDs/extensions.
Peirce independently generated the second candidate and verified exact equality.
Root and Socrates reviewed the complete narrow keyed delta; the prior full
cumulative review was explicitly carried forward by verified identity, not
claimed as a new whole-codebase reread. Named v4 approval is retained at
`tmp/ocr_uncertainty_architecture_review_v4/named-review-v1.md`, SHA-256
`057ed2ee0492d11ea75a7f6fc17982186b7b21d4ff625aa6178fdd3879b4be43`.
Only the new private copy received the approved baseline. These are AI-agent
reviews, not independent human transcription or release approval.

## Retained bindings and verification

Paths below are relative to the qualification base's `evidence/`, except the
runner/verifier under the original workspace's ignored `tmp/`.

| Artifact | SHA-256 |
| --- | --- |
| `copy-preparation.json` | `37a966434d9a7c73d4dde955afe89e4b21be89d747fa664c112b3ea70c481dad` |
| `provenance.json` | `24bb8f0e1b38b92fff2c53e0c666f433bfe555b38ef320e0afae1fe1d7bb0ed4` |
| `finalization.json` | `f5f97ff5208d22b999c5c0a74af2b633d6892a9419487331dd519993ca76bf8b` |
| Approved copied architecture | `6391d7c306c52434fc2696d5bf517c43a147a9d5fce69ffc6d88c9847cdc2c5a` |
| Runner `ocr_uncertainty_frozen_gates_v4.py` | `3787230c7b4aea20d0bab3e4fe5ca906748d49d2ec68c4bfe135834fbdb2b4e8` |
| Verifier `verify_ocr_uncertainty_gates_v4.py` | `95f18822316c6e5dbcb8fa7a2f7bb313b79f7c1d7495e2d8e7daa37ed60dbe2c` |
| `gates-v4/receipt.json` | `5fa10ca7ab91d189728ca7c4af12b4fd7a34b6a6c2e50d6cc232102511b7bf42` |
| `gates-v4/verification-addendum-v4.json` | `e4fcca63415813c05094096b555675b166486dbef8fa7b14076a978f1deacede` |
| `gates-v4/collection.json` | `f5243ccc411d56a7e6acc1150e0cd1edbe83dfdf0e65e9276e5b6583bc3e337c` |
| `gates-v4/pytest.xml` | `b56c77b77eaf60b18e36aca8c1cda2f315fbd93bf96c29a4a3fdf244b7bb55fd` |

Root's runner terminated with exit zero (`251f7e`). Peirce executed the held
verifier exactly once with independently supplied receipt/candidate/runner/
manifest pins; it terminated with exit zero (`8b8310`). Retained readback
(`98a5dc`, root `e18e4b`) confirmed the published addendum. Verification checked
all 28 expected artifacts, collection/JUnit correspondence, nine gate commands,
40 current CI predicates, the required real Zettlr case, qualified interpreter
and both source/index/HEAD/tree bindings. It did not rerun collection, gates,
retrieval, OCR or models, or replace the original receipt.

The fixed interpreter is Python 3.12.10, executable SHA-256
`0b471133e110cfb53a061cad528ce8e517d7b9ac41a0a396c39ad795a487fc14`;
collection used pytest 9.1.1. The reviewed environment policy scrubs inherited
Python/Git/pytest overrides, disables third-party pytest autoload and optional
Git index writes, and binds the tracked Zettlr validator bundle. Installed
Node/dependency bytes, other installed packages, system initialization and
remaining local Git configuration are not attested by that policy.

Preparation and its independent audit recorded zero handle-ctime drift checks.
The runner recorded **1,156** such checks, with 128 details and 1,028 omitted
details; the verifier recorded zero before and after publication. These are
non-gating handle/path metadata observations under the existing reviewed policy,
not distinct mutations or an inferred cause. Content and identity checks passed;
they do not establish continuous metadata, atime or loaded-byte attestation.

## Remaining outcomes

Clearer long-Save feedback is the next small usability slice. Read-only review
identified targeted built-in queued progress as a smaller candidate than a new
generator lifecycle; no such UI change or browser acceptance ran in this cohort.
Broader accessibility, approved representative held-out accuracy, independent
human reference adjudication and the remaining OCR/AI outcomes stay pending.
The retained synthetic accuracy and five-repetition runtime observations remain
separate evidence; software test counts are not OCR accuracy gains.

All 67 requirement/acceptance pairs remain intact. This checkpoint does not
authorize canonical correction publication, new model acquisition, private-source
disclosure or wider AI-reader access. Earlier failures remain recorded; no usage
reset, original baseline replacement, index update or history mutation occurred.
