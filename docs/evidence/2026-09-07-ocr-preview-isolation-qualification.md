# Local isolated crop-preview qualification, 2026-09-07

Status: the fixed same-crop preview worker, private host-temporary staging and
per-view cancellation passed the Phase 12 frozen-source local checkpoint.
All nine selected gates passed, with independent retained-result verification.
This is dirty-worktree Windows development evidence, not hosted CI, release,
representative OCR accuracy or authenticated human approval. The
[complete OCR/AI improvement program](../ocr-improvement-program.md) remains
active. [Phase 11](2026-09-07-ocr-guided-review-qualification.md) records the
preceding guided-review generation and its separate browser observations.

## Qualified behavior and limits

The browser's [same-crop preview](../ocr-crop-comparison.md#isolated-previews-post-phase-11-development)
now uses a fixed child process through the review coordinator. The host stages
an immutable source copy and a closed, bounded request, verifies exact source,
scope, producer and file bindings, and accepts only bounded raw RGB with a bound
completion record. The child alone opens that copy with the PDF decoder. The
public in-process compatibility renderer and other page/scan renderers remain
in-process; this is not isolation of every review renderer.

One preview may run per coordinator. Explicit per-view cancellation revokes
approval without cancelling unrelated OCR or another view. Changed context,
nonzero exit, cancellation and uncertain cleanup cannot yield an accepted image.
Cleanup uncertainty blocks another preview or OCR start. Seven renderer codes
remain distinct from four host-only lifecycle codes. No private exception
details, automatic retries, fallback render route or relaxed integrity guards
were introduced.

The worker deadline is 30 seconds after shared-supervisor startup. Host input
hashing/staging, termination grace and the coordinator's shared bounded shutdown
allowance are separate. Existing image bounds and process deadlines are not a
total decoder/process-tree memory cap or an OS sandbox.

Private staging uses a unique owned root under the host platform temporary
directory, separate from the review output and Gradio cache. The launcher blocks
that exact root from file serving. Parent/root and original workspace identities
are separately pinned; known repository/output/cache staging locations are
refused. This is not a universal sync-software detector. Cleanup removes only
known owned files and empty directories, never the shared temporary parent.
Uncertain/interrupted cleanup may retain a private PDF copy outside the review
output. No stale-directory sweep or secure-erasure claim is made.

No canonical extraction, index, OCR default, legacy report schema or AI-reader
permission changes. No representative private sources, references, packages or
models were acquired. AI access remains the existing authenticated reader-only
text-evidence surface, not scan serving, execution or correction publication.

## Retained native observations and failures

The standalone preview/resource observations below used the existing generated
eight-page source, SHA-256
`fb337c41515e517f09750a58f8935a8f927fa7a19085099438f28b5e4ffe7e6f`,
and one physical crop, scope SHA-256
`85b9a1309317f2f2bbbea53a4c56ce887e3f7c278774e12cc536bc6cdf57bf77`.
No OCR calls were made by these preview checks.

The initial output-directory staging generation passed 619 combined checks but
failed the separate `ocr-crop-preview-isolation-v1` native check. Its cleanup
assertion obscured the original error code; that attempt remains unexplained.
The timing-affected v2 host trace separately observed staged-request ctime-only
drift with unchanged digest/device/inode/size/mtime, followed by a Windows
sharing violation on empty-root removal after request-leaf cleanup. The child
returned 2, but its inner failure and the metadata-changing actor were not
observed. This does not retrospectively explain v1 or prove that relocation
cures every filesystem failure. Failed attempts and retained empty roots remain.

After the host-temporary staging change, 133 backend checks and the broader
639-check integration selection passed. The guided/acyclic/independent-AST
architecture selection passed 12 tests with 20 deselected. Focused scopes overlap
the full suite and must not be added to its count.

The uninstrumented isolation-v3 helper called the actual controller once and
reported a 311-by-28 RGB image in 0.985 seconds, byte-equal to its separately
labelled in-process comparator. It observed cleanup and root removal, with
source/recovery and all nine producer hashes unchanged. The PNG was visually
inspected. Independent readback checked the retained image pixels/digest and
current root absence without rerendering. Comparator pixels were not separately
retained, so the original equality observation is not an independent rerender.

The separate `ocr-crop-preview-resources-v1` helper made two actual controller
attempts with bound-worker Windows memory sampling. It preserved the real
supervisor, worker arguments and original admission callback. The normal attempt
returned the same RGB digest; five samples reported a maximum process peak
working set of 322,199,552 bytes. The cancellation attempt observed its own bound
worker alive after start-gate release, requested cancellation once, returned
`preview_cancelled` with no image, and recorded successful cleanup. Its three
samples reported a peak of 292,265,984 bytes. Supervisor exit 130 represents
cancellation; the killed process's 124 is Windows Job termination status, not
evidence of deadline expiry.

These counters include worker startup/imports, not decoder-only or whole-tree
memory. Sampling affects timing and does not enforce a cap. A live post-release
heartbeat does not prove entry into active decoding. The resource artifacts
retain neither ephemeral request files nor private root names; their cleanup
claims are original helper observations, not independent present-time absence
checks. Independent readback verified four artifacts, exact attempt copies and
13 input fingerprints. Two preliminary resource-checker failures before
publication are retained in its addendum, not presented as production failures.
No new browser run or representative accuracy measurement was performed.

| Artifact | SHA-256 |
| --- | --- |
| `output/pdf/ocr-crop-preview-isolation-v3/observation.json` | `3cc63508f662751e3651d557910f18eafd5c7bd65675ae5db0e63205ef21fbc2` |
| Isolation-v3 `independent-verification-v1.json` | `c50d921140fd8cf16a27bff97066e748c814ca4f6f8b49030cfb2f669d0bd70d` |
| `output/pdf/ocr-crop-preview-resources-v1/observation.json` | `f24c100612489d3df862a71fb0c7f642c67fe03f211c09f64d8cc9030875326d` |
| Resources-v1 `independent-verification-v1.json` | `99cb75eadd3c4bd0e523db21948540949f94086f0a52cc707328fc7318226b37` |

## Reviewed architecture refresh

Socrates reviewed all 37 keyed changes and the complete 19,981-byte pretty diff,
then independently regenerated the exact candidate before root promoted it with
the unchanged official generator. The reason is fixed preview-worker/controller
ownership, host-private staging, coordinator shutdown and cancellation. There
is no RAG facade ownership move or AI-authority expansion.

The inventory records 377 tracked Python files, 167 non-test modules, 3,770
functions, 302 classes and 517 acyclic first-party edges. The complete facade,
runtime and consumer objects remain identical: 956 static/964 runtime bindings
and 161 top-level/26 nested patch seams. The measured 1,826,395-byte inventory
has a deliberately reviewed 1,840,000-byte guard, raised from 1,825,000; no
canonical comparison, schema or separate 4 MiB runtime-probe limit was relaxed.

Review found a stale exact-boundary test. Its narrow repair added the actual
new import/inbound edges and two fresh-process heavy-import controls without
weakening assertions. This test-only delta followed independent generation and
was separately reviewed; the generation snapshots were not rewritten. Root's
subsequent official refresh matched the approved candidate exactly.

Canonical inventory SHA-256:
`8c9d9991aa6864131915b6f0392ae4bf4c3f9a4af5e8ae0757e78e9d47aa792e`.
Named approval:
`evaluation-reports/ocr-preview-isolation-architecture-v1/independent-v1/approval.md`,
SHA-256 `1e51eaf21284f46574f1a3c98bca83332943c811323a90ccba8632befbc5b1f7`.

## Frozen-source local checkpoint

The create-only bundle is `evaluation-reports/ocr-phase12-gates-v1/`.
Qualified Windows CPython 3.12.10 ran pytest, Ruff, source compilation,
dependency policy, model policy, architecture and three unchanged offline CI
BM25 threshold/baseline gates. All nine exited zero. Pytest 9.1.1 collected
10,704 unique nodes, exactly matching the complete JUnit identity multiset:
**10,697 passed, seven skipped, six warnings in 1,359.08 seconds**, with no
failures, errors or omitted collected tests. The runner's broader pytest
subprocess elapsed time was 1,370.000 seconds.

All 8/8/6 retrieval queries and 11/11/10 source records were accounted for;
all 40 threshold/regression predicates passed. All 520 tracked-file content
hashes, logical index entries, tracked status and HEAD matched the recorded
collection, per-gate and subsequent verification checks. These are point-in-time
content/hash checks, not continuous metadata or loaded-byte attestation. HEAD
alone does not identify the dirty-worktree tested bytes.

| Identity | Value |
| --- | --- |
| HEAD | `e34103f70b676eacc8d55badb2468b8a10004ef4` |
| HEAD tree | `af0a69ccde82b6b679580f07813495721978efe0` |
| Before/after snapshot SHA-256 | `37bce2aff26e25f42eb24de9bdea8e143c841f231fde24499a62c6780f641529` |
| Snapshot computed Git blob identity | `73e8a2f70534cceade7a76054fddecd712a0d5d7` |
| Index entries SHA-256 | `affc4116b70e1a0f5dceedab99de84135c9924226eb039df4ee5fbd4c69708f2` |
| Tracked status SHA-256 | `ddcd13f2665ffb2986f1871ae8cd1bf49636ece76d256a9444fa81a78f5e18fc` |
| Ordered collection IDs SHA-256 | `c0bc8b366949ac08c0b5f3e1764a02ed350b081b25a29138b98a30a05a258853` |
| Runner SHA-256 | `b157ad626abd0f8fc5a528e0f772faabf55bf4d551de4fd1b6fa9c7689fcac99` |
| Receipt SHA-256 | `86c4b09378c3be670151a6135aa8ff2ef0305d62dd535870d5084b76032992c9` |
| Verification helper SHA-256 | `314d8cfa5208fe57c1c8f2000987d2530cc61e88f2857a6ca42e10bdb2bf660b` |
| Verification addendum SHA-256 | `c8cd0c5da9e6e34bf6b02f3f1a6af2b4599130d7c9c43e096b244495e69ebc93` |

The independently reviewed retained verifier exited zero, checking all 28
artifacts, exact collection/JUnit correspondence, CI commands, 40 predicates and
current source/index bindings without rerunning gates. Root fully read its
helper/addendum, independently rehashed all 28 artifacts and 520 source files,
and read the raw JUnit counts and complete warning section.

Socrates's separate read-only audit reconciled the same complete evidence and
ended with a successful dual-read/content-hash and source/index/HEAD/tree check
(`32b0a7`, exit 0). Two earlier audit-only refusals remain recorded: an unlocalized
file-identity assertion (`cf2c68`), and a ctime-only change while reading
`model-artifact-policy.json` after the test/retrieval checks (`7d46ac`). A final
readback observed three additional ctime-only changes, in
`model-artifacts.lock.json`, `process_supervision.py` and
`tests/test_ocr_crop_preview_supervision.py`, with identical two-pass content
hashes. The final audit verifies device/inode/size/mtime and content identity,
not continuous ctime stability. No actor, content change or historical failure
cause is inferred. No separate audit file was created. Agent verification is
not human approval; the computed Git blob identity does not imply a stored object.

Seven skips cover Windows symlink capability, forbidden newline paths, three
POSIX watchdog/process-group controls and two POSIX-mode/Windows-ACL controls.
Exact identities/reasons remain in JUnit and the addendum. Five warnings concern
pytest iterable parametrization in hard-scan I/O, layout and run-comparison
tests; the sixth is Starlette's httpx TestClient deprecation. None is an absent
OCR dependency, and these warnings remain unresolved.

Third-party pytest autoload and Python/user-site overrides were controlled
under the recorded policy. Other ambient state, installed/native loaded bytes
and the hosted OS/Python/client matrix are not attested. The gate runner has no
separate subprocess deadline or streaming log-size cap. These documentation-only
updates follow release of the frozen checkpoint and are outside its exact source
snapshot. No contents were committed, pushed or released.

## Remaining work

Durable private crop-reference/comparison packs and restart, source-resolution
magnification, explicit reference uncertainty, other renderer isolation and
broader clipboard/IME/platform coverage remain pending. Active-decoder
cancellation and a total memory bound are not established by this checkpoint.
Representative held-out accuracy, independent-engine comparison, adaptive
retries, independent reference adjudication and review-effort/downstream
measurement remain separate outcomes. AI scan access and explicit correction
publication/rollback are not implemented here. The full pending program remains
active; a passing local suite is not an OCR accuracy gain.
