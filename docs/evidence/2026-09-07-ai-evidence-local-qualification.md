# Local AI evidence-search qualification, 2026-09-07

Status: native generated integration, independent retained-artifact review and
the complete Phase 5 frozen-source local checkpoint passed. This is dirty-worktree
local development evidence, not hosted CI, a release, owner approval, native
loaded-byte attestation or representative OCR accuracy. The complete
[OCR/AI improvement program](../ocr-improvement-program.md) remains active.

## Implemented boundary and review

The opt-in text-evidence companion has strict public/private contracts, a fixed
contained-worker action, authenticated HTTP, a reader client and a stdin CLI.
It preserves ordinary v1 search and its committed OpenAPI bytes. Exact record
and source-generation checks precede return; related-page recovery states do
not become occurrence-level correction adoption or verified text accuracy.
Table-derived rows disclose parent-table provenance, not invented row geometry.
The [usage guide](../ai-evidence-access.md) and
[architecture decision](../architecture/decisions/service-evidence-companion.md)
define the boundaries and remaining scan/adoption work.

Integration reproduced the HTTP lifecycle's thread-owned lease mismatch.
The explicit repair uses one per-lifespan owner thread, copied call contexts
and cancellation draining. This is intentional hardening, not a pure extraction.
Focused independent agent reviews covered contracts/client/CLI, exact evidence
joins, runtime/HTTP/composition, lifecycle and legacy sidecar admission. The
core/integration/base-HTTP group observed 214 passing tests; that is not the
full-suite result.

Named architecture reviewer: Peirce (independent agent, not human approval).
The reviewer inspected the full earlier addition and the complete final v2-to-v3
keyed/hash delta, then independently regenerated the final candidate. Root
promoted and byte-checked that approved candidate:

- SHA-256: `78a798231a9313d6a61220f5eca51bc98ec411dc82485792c09a29e3a2970c91`.
- 1,681,122 bytes; 313 tracked Python sources; 144 non-test modules;
  3,315 functions; 956 static and 964 runtime `rag` bindings;
  161 top-level and 26 nested literal patch seams.
- Additive evidence boundaries, explicit lifecycle repair and test consumers
  account for the drift. The final legacy-proof/fixture delta adds no facade
  ownership move, import cycle or changed prior callable signature.
- The existing 1,700,000-byte size guard is unchanged.

## Preserved failed native attempt

The first isolated generated run indexed 12 records but evidence search refused
the raw-record/index-manifest join; the supervisor exited 2. It has a retained
failure, not a success receipt. No original artifact was overwritten.

An actual Qdrant payload read, with zero embedding/OCR calls, found that 11 of
12 records differed only in `embedding_token_count`: the synthetic fixture used
estimates, while indexing recomputed exact counts. Text and all other metadata
were unchanged; indexed payload hashes matched the actual manifest. Diagnosis:
`evaluation-reports/ai-evidence-native-diagnosis-v1/diagnosis.json`, SHA-256
`ec8aa86c8cd001a10125974ab0fc3ea20ebc0f69044178ff05e9749f801c1fcb`.

The second run used a new fixture generation with exact verified-tokenizer
counts materialized before publishing source chunks and proof artifacts. Four
portable regressions cover that preparation boundary. Production matching was
not weakened. Legacy/mismatched indexing derivations still require an explicit
future correspondence contract; the reader never repairs or republishes input.

## Successful native generated execution

Bundle: `evaluation-reports/ai-evidence-native-smoke-v2/`.
Receipt: `receipt.json`, SHA-256
`98934ae2204b47405e0830c0ef1489f45f7ce226c83da787c36da5dd0baab5b9`.
Archived producer: `helper.py`, SHA-256
`d38f0579c4cb1eb7dd3ffdb1e987b03bd26f404c60f438eb3a88a6888389da27`.

The qualified Windows CPython 3.12.10 process used the existing hash-locked
environment and cached `nomic-ai/nomic-embed-text-v2-moe`, revision
`1066b6599d099fbb93dfcb64f9c37a7c9e503e85`. The declared verified bundle has
12 files / 1,923,450,201 bytes, identity SHA-256
`1d8b85c46ed354eed6345b39c5e43269ebb82d809a6612e7718250255d086acb`.
No dependencies or models were downloaded or changed.

Actual execution used the ordinary Qdrant indexer, 768-dimensional embeddings,
default production service composition, three contained search calls, literal
loopback authentication, two successful reader searches and one fresh CLI search.
The helper also performs an authenticated schema read and a refused wrong-token
attempt; the receipt's `authenticated_reader_calls: 2` counts the successful
searches, not every reader HTTP request. There were
no fake vectors, retrieval overrides or supervisor overrides. It indexed 12
generated records and returned 11 hits. CLI and supervisor exited 0; receipt
elapsed time was 45.703 seconds.

Saved results preserve exact ordinary-search hit parity, all five related-page
OCR states, explicit `not_verified` text accuracy and `unmapped` review scope.
Four table-derived hits disclose parent scope; excluded frontmatter stays out.
A deliberately false negation candidate was not adopted. The producer observed
wrong-token refusal, service closure, completed private-request cleanup,
unchanged corpus/index-manifest inputs and model verification before/after.

Eleven retained artifact hashes and eight source-input hashes are recorded.
Source-before and source-after bytes match, SHA-256
`d11aeac63c9483c490a65edc972098cf934c31f52923a6a4eecf07718531eeae`;
the native freeze covers 318 producer/policy files and scoped Git state, not
documentation or the architecture inventory. Ephemeral request envelopes and
native sessions were not retained: their execution/cleanup observations are
not independently replayable from public results alone.

Source bytes, Docling/conversion and recovery declarations were scripted
synthetic inputs. This run did not render a PDF, execute OCR, authorize scans,
publish corrections or test a representative book. Artifact correlation is not
an attestation of every vector-store byte or evidence of OCR correctness.

## Independent native verification

Anscombe independently verified the retained native inputs, all 318
producer/policy file hashes and scoped Git state, 24 artifact bindings, the
strict proof graph, generation/evidence digests, 12 indexed records and 11
returned hits. Four table children and two multipage fragments retain their
proper scope. The create-only native `verification-addendum-v1.json` has SHA-256
`8f7390f6db936ec615f7c3d0b7057c10d721b628261aa43752096e95203809a8`;
ignored verifier `tmp/verify_ai_evidence_native_v2.py` has SHA-256
`364e79452f1d13ab991ffc978427b80b53ae153bdc4c086f9a81fd65b8d84819`.
Root read the complete verifier. No model, OCR, search or HTTP execution was
repeated, and original evidence was not changed. The addendum explicitly
records the request-count caveat and producer-versus-retained-evidence limits.

## Phase 5 collection coverage and preserved preflight failure

Pre-run independent review found that inherited pytest options could silently
select a subset while producing a passing log and JUnit report. The local
Phase 5 helper now strips selection/import overrides in child processes,
disables third-party pytest autoload and retains a separate complete collection
preflight. The verifier requires every collected node to correspond to the
actual JUnit testcase multiset, as well as matching counts and terminal log.
Tracked repository pytest settings and built-in plugins remain in effect.
These are deliberately controlled local checks, not a claim to reproduce the
complete hosted-CI environment or plugin matrix.

The first Phase 5 preflight (`ocr-phase5-gates-v1`) collected all 7,959 tests
with pytest exit 0 but its parser rejected two existing oversized-input test
IDs (20,076 and 20,094 characters) above the initial 16,384-character bound.
The helper exited 1 before any gate ran; there is no passing gate receipt.
Its collection log remains preserved, SHA-256
`e4f87467b404f766a89089e74785e02d66de970c1c226854fab0db0745b4f560`.
The reviewed retry uses a 32,768-character per-ID bound, retains the 16 MiB
aggregate bound and exact untruncated identities, and targets a new
`ocr-phase5-gates-v2` directory. No production code or tests were changed to
make collection pass.

## Passing Phase 5 frozen-source checkpoint

The retry `evaluation-reports/ocr-phase5-gates-v2/` completed with runner exit 0
and all nine gate exit codes 0. The qualified interpreter was Windows CPython
3.12.10 in the existing full/test/tools hash-locked environment. The observed
HEAD was `e34103f70b676eacc8d55badb2468b8a10004ef4`, commit tree
`af0a69ccde82b6b679580f07813495721978efe0`; neither alone identifies the dirty
worktree. The full tracked snapshot below binds the actual tested files.

- Full pytest: 7,952 passed, 7 skipped, 6 warnings in 437.62 seconds; zero
  failures/errors. The runner measured 442.968 seconds including process work.
- Separate collection: all 7,959 ordered node IDs matched the complete JUnit
  testcase multiset, including skips. Ordered-node digest:
  `384bb2f1a988c476347da0726e898897a609fc5695d5d84a5018195cfdd88f43`.
- Ruff, compilation of all 313 tracked Python sources, dependency policy,
  model-artifact policy and the reviewed architecture baseline passed.
- The exact three CI offline BM25 command/threshold/baseline configurations
  passed, with full query-detail/input correspondence: property 8 queries,
  constitutional law 8, table family 6. Baseline regressions and all lower/upper
  metric bounds were independently recomputed from retained reports.
- Every per-gate source-freeze flag is true. All 445 tracked files, HEAD,
  Git index entries and tracked dirty status stayed unchanged through the
  subsequent independent verification publication and fresh final readback.

The seven skips are platform applicability/permissions: three POSIX process
cases, two POSIX permission-mode cases, one Windows-invalid newline-path case
and one Windows symlink-permission case. None is a missing OCR/model dependency.
The six warnings are five existing iterator-parametrization deprecations and
one Starlette/httpx deprecation; no dependency change was made to suppress them.

Exact retained identities:

- Receipt: `a3d9e6bf77c6aed6f6a0c80abc20661e1a143b3f3b8752f552ad2b44ac921968`.
- Before/after snapshot: `af3518699746461fc2dc7fd4b1000c896402af25874a4103280268bbf6e86383`.
- Index entries: `e8d9741454a2463576de7844c7f2a26ba4c765ad4d94d17afb81d31b21cbd665`.
- Tracked status: `a33609e546ca21d4152df559858bf2b5448fd28dced976e5ccd9818905ff6c80`.
- Archived/live gate helper: `315787cf7289cffd6f3afdb41c4e2641c6b18f799692037000311dd60d2e2068`.
- Independent verifier: `tmp/verify_ocr_phase5_gates_v1.py`,
  `ff96fcddf3f8707bbca0c16544c93b8892f4a183c9579e2b925a07abf270c9c1`.
- Create-only `verification-addendum-v1.json`:
  `523f977364b5f9680ed01365831c01665e4da330ed17fef2f6d2d010f55affbf`.

Root ran the reviewed verifier with the independently observed receipt pin;
it verified all 28 retained artifact bindings, full collection/JUnit coverage,
all result records, runtime/helper identities, current source/index agreement
and retrieval thresholds. Peirce independently repeated bounded readback and
the collection/JUnit correspondence using installed pytest normalization,
including fresh full-snapshot checks before and after that readback. Neither
verification reran a gate, OCR, model or retrieval execution, and no original
receipt was rewritten.

Post-gate changes only document these results and pending work. No tracked
Python, dependency/model policy, architecture inventory or Git index change
followed the passing checkpoint. This is one controlled local Windows generation
with third-party pytest autoload disabled, not the hosted OS/Python matrix,
loaded-byte attestation, a mergeable draft PR, a release or whole-program
completion. Scan serving, occurrence-level correction publication/rollback,
representative references and the remaining accuracy outcomes stay pending.

## Separate offline ordering feasibility, not a delivered feature

During this qualification, an ignored boxes-only prototype inferred a gutter
and body band before opening any references. It then evaluated all eight saved
generated recovery pages; seven abstained unchanged. The known column page's
unconfirmed hypothesis reduced 15/91 inversions to zero, 147 character edits
to zero and 33 word edits to zero. This is previously seen generated calibration,
not held-out evidence or a default OCR behavior change.

The identical-geometry table control received the same hypothesis and worsened
from zero to 80 character / 32 word edits. A scripted jointly missing line still
left 16 character / 4 word edits despite preserving all 13 observed lines out
of 14 printed lines. These controls rule out geometry-only automatic approval
and show that ordering does not prove source completeness.

Observation: `evaluation-reports/ocr-layout-feasibility-v1/observation.json`,
SHA-256 `ba2a4879169aa915f9499c7e4ddab3ac9483bfc2132ddb2e039f735930800cff`;
archived helper: `cc7c5c1ed9e13ee7c8d92a8ae9d434f79b09709d4d2b58e4a6b00fee0a9c3b22`.
Anscombe independently checked four input bindings, all 17 scoring records,
68 character/word distances, 48 stage-line correspondences and 4,433,244 scoring
cells. No PDF, OCR or model was opened/run. The prototype does not score the
configured critical phrases, and its receipt does not bind all imported policy
modules; current replay is not complete historical runtime attestation.

The next production increment is an explicitly unconfirmed, source-bound
column-layout suggestion with operator prose classification, preview approval,
unchanged line data, visible abstention and the retained negative controls.
Canonical adoption and representative accuracy require their separate gates.
