# Local guided OCR review qualification, 2026-09-07

Status: the opt-in execution coordinator, same-crop comparison, original-source
preview and reference-edit safeguards passed the Phase 11 frozen-source local
checkpoint. All nine selected gates passed, with independent retained-result
verification. This is dirty-worktree Windows development evidence, not hosted
CI, release, representative OCR accuracy or authenticated human approval.
The [complete OCR/AI improvement program](../ocr-improvement-program.md) remains
active. [Phase 10](2026-09-07-ocr-disposition-qualification.md) describes the
preceding generation; its failures and evidence remain unchanged.

## Qualified behavior and limits

The [opt-in review workflow](../ocr-review.md#opt-in-ocr-execution-development-preview)
prepares exact page, region or hard-scan requests, requires one-use execution
consent and runs only the fixed supervised diagnostic worker. Fresh private
bundle validation precedes result use. Cancellation, process outcome and verified
artifact availability remain distinct; uncertain cleanup blocks another start.
The editor's baseline is never replaced, and its bounded eight-run history
does not restore execution authority after restart.

[Same-crop comparison](../ocr-crop-comparison.md) binds exact source, page,
requested rectangle and occurrence identities, including different DPI/recipes.
It discloses rounded raster-edge differences and scores all predicted text.
Unavailable candidates are not fabricated as empty predictions. A fresh original
RGB crop precedes independent reference authoring; a separate one-use ticket
binds the displayed view, pair, source/context and exact reference revision.
Changed reference fields revoke that ticket. Stale/unchanged edit events return
no replacement values; they neither renew authority nor raise routine typing
errors. A failed image-delivery path cannot gain approval through a later edit.
These checks do not attest browser decoding or human inspection.

Seven allowlisted preview codes expose failure stages without private exception
details or guessed historical causes. There is no new render retry. The preview
is capped at 1,400 pixels per side, but the PDF decoder remains in-process;
raster bounds do not bound all decoder memory. Candidate text remains visible
during transcription, so this is not blinded reference adjudication.

The focused 595-test scope includes 93 crop UI cases, 102 renderer cases and
21 full-app Gradio cases. Full-app cases explicitly substitute inert rendering,
inference, supervision and producer/model observations; they do not establish
native failure-path containment or browser visibility. Focused scopes overlap
the full suite and must not be added to its count.

No canonical extraction, index, OCR default or existing report schema changes.
The AI reader retains its existing text-evidence capability, with no new scan,
shell, execution, administrative, cloud or automatic-adoption authority. No
private representative sources, references, models or packages were acquired.

## Generated browser/native observations and preserved failures

The generated eight-page source has SHA-256
`fb337c41515e517f09750a58f8935a8f927fa7a19085099438f28b5e4ffe7e6f`.
Browser v1 retained two actual contained crop calls but its label obscured the
short crop; transcription/scoring was not attempted. Browser v2 fixed the
caption and retained actual scoring, but also 22 rapid-typing HTTP errors and
an input-only edit gap. Both generations remain preserved, not reclassified as
successful full workflows. The earlier standalone preview v1 refusal is a
separate unexplained failure; later rendering success does not establish its
inner cause.

Browser v3 used two fresh actual calls at 300 and 400 DPI for the same physical
crop. The original image was read before authoring the generated reference.
Observed controls cover rapid typing with zero console errors, input-only fill
after scoring, single-character deletion, restoration without restored approval,
critical-field clearing, editable trailing newlines, pair reload and renewed
explicit preparation/confirmation/scoring. Scoring without fresh preparation
was visibly refused. Physical clipboard paste, IME, all platforms and
deterministic network response/paint ordering were not exercised.

Both candidates had zero normalized CER/WER, while the retry split the title
across lines. This is neither an accuracy gain nor structural equivalence.
Initial/final actual UI comparison bytes matched. Complete metrics are retained
in raw textbox JSON; score-element screenshots show only excerpts. Independent
readback verified both native bundles and compared the actual UI result with a
separately labelled pure recomputation, without OCR or rendering. Its first
verifier failed a newline assertion, published no addendum and remains retained;
the corrected verifier succeeded. This was not a production failure.

Browser/server shutdown completed, temporary auth/cache files were removed,
and all six actual native calls across v1/v2/v3 remain retained. Agent visual
review is not authenticated human adjudication. Artifacts below are under
`output/playwright/ocr-crop-comparison-development-v3/` unless shown otherwise.

| Artifact or binding | SHA-256 |
| --- | --- |
| Crop UI source, `ocr_review_crop_ui.py` | `cdb9a195582b63d33ae7978a69afd1d656258323ed16632fd1df276ce95a38eb` |
| Exact physical scope | `85b9a1309317f2f2bbbea53a4c56ce887e3f7c278774e12cc536bc6cdf57bf77` |
| Selected crop pair | `b75a39a1c79d7d884e3a423bfbb7fd10b6a41e88e23a08720b0ecacced0eadd8` |
| Actual UI comparison bytes | `f1ef1327da5f36e7a287b56b6cd7051b77bcdb1e3f2aa1e693cbeb9e253a8456` |
| Actual UI score observation | `674d5987c1ca1e9e1a3d70e79bfd4da849e8ae7715a96ac863ebeeff479db0c8` |
| Actual UI edit observation | `a899c23437dc056b8d48cbc19276ffc4b98098758747821e30f2e65fccaea5f9` |
| Corrected verifier, `tmp/verify_ocr_crop_browser_development_v3_2.py` | `a87e2ec6078952232598d09e5b1be5b815bd0c83f70e2eb0e79311a8a664ed9b` |
| `independent-verification-v1.json` | `603faae58280171e80909722b8276c56c49484c7add8714fa00034a63a12b9f1` |

## Reviewed architecture refresh

Socrates reviewed all 29 keyed changes and the complete 45,628-byte,
1,301-line diff, then independently regenerated the exact candidate before
root promoted it with the unchanged official generator. The architectural
reason is opt-in coordinator/UI composition, strict same-crop comparison,
original-source preview and read-only review-host request admission. In-process
diagnostic execution remains refused in the review host. There is no RAG facade
ownership move or AI-authority expansion. The comparison adapter reuses hard-scan
I/O validation and is not a dependency-free leaf.

The inventory adds five production modules and nine test files, with no removed
Python paths. It records 372 tracked Python sources, 165 non-test modules,
3,731 functions, 298 classes and 507 acyclic first-party edges. The complete
facade ownership/runtime/consumer objects remain identical: 956 static and
964 runtime bindings, 161 top-level and 26 nested patch seams.

Eight added architecture controls test exact inward/inbound/transitive edges
and seven fresh-process import boundaries. Heavy-import attempts are recorded
even when an ImportError is swallowed. All 30 architecture tests passed.
The measured canonical inventory is 1,813,396 bytes; its finite test guard was
deliberately reviewed and raised from 1,800,000 to 1,825,000 bytes, leaving
11,604 bytes of headroom. No schema fields, canonical checks, generator behavior
or separate runtime-probe limit changed.

Canonical inventory SHA-256:
`1146afcd2330ef8db8605a3609de8d012c0668953d44cb507eb3e841b13b8075`.
Named approval:
`evaluation-reports/ocr-crop-architecture-review-v1/independent-v1/approval.md`,
SHA-256 `60b06fdd685c5dddf2e8d39c635fb68e769a8094bc289d0721e5d1b32ee48b94`.

## Frozen-source local checkpoint

The create-only bundle is `evaluation-reports/ocr-phase11-gates-v1/`.
Qualified Windows CPython 3.12.10 ran pytest, Ruff, source compilation,
dependency policy, model policy, architecture and the three unchanged offline
CI BM25 threshold/baseline gates. All nine exited zero. Pytest 9.1.1 collected
10,515 unique nodes, exactly matching the complete JUnit identity multiset:
**10,508 passed, seven skipped, six warnings in 1,259.50 seconds**, with no
failures, errors or omitted collected tests. The runner's broader pytest
subprocess elapsed time was 1,272.031 seconds.

All 8/8/6 retrieval queries and 11/11/10 source records were accounted for;
all 40 threshold/regression predicates passed. All 514 tracked files, logical
index entries, tracked status and HEAD matched at every recorded collection,
per-gate and subsequent independent-verification check. These are point-in-time
hash checks, not continuous loaded-byte attestation. HEAD alone does not
identify the dirty-worktree tested bytes.

| Identity | Value |
| --- | --- |
| HEAD | `e34103f70b676eacc8d55badb2468b8a10004ef4` |
| HEAD tree | `af0a69ccde82b6b679580f07813495721978efe0` |
| Before/after snapshot SHA-256 | `fd804617d2de11acac88ad0d90a00391c67419eb9ed649a2802548bc1cb0fb35` |
| Snapshot computed Git blob identity | `297de6547d165348b41081af3a38a249b8a043ff` |
| Index entries SHA-256 | `a317717dba67d5dafcb5490840e1dcb3dada1a4e7b515339581be1955277631c` |
| Tracked status SHA-256 | `5f5ae76b60ba21110ebd663d65c6aee04b0f834487f6a677b453ee99e0480102` |
| Ordered collection IDs SHA-256 | `f66aa29b466d9df9ab0b9c395838e733adba64bb363735c876b4b56ccb114ce0` |
| Runner SHA-256 | `ad41b442f51db151c59d6044267ee8b143b2a1c769b7f1819e42c24c129abb4e` |
| Receipt SHA-256 | `a7c705fd1a0ba3dd3533c780bdc624e18590b23faba43c4643369ccb00ae1480` |
| Verification helper SHA-256 | `472d715d3ee8551fd4b0d21cf9be01f69c02187a9426c94038f2cc49822f469a` |
| Verification addendum SHA-256 | `6751127ed667b9ee4ec2004b6fcaa2da04d832223b12c1a88feb1980c8f2650d` |

Anscombe's independently owned retained verifier exited zero, checking all
28 retained artifacts, exact collection/JUnit correspondence, CI commands,
40 predicates and current source/index bindings without rerunning any gates.
Root fully read its helper/addendum and independently rehashed all 28 artifacts,
checked raw JUnit counts/skips and read the warning log. Peirce's separate
read-only inline stdlib audit also exited zero, checking all 514 current tracked
files, exact logical index/status/HEAD/tree, helper bindings and collection/JUnit
identities. That audit created no separate file. Agent verification is not human
approval. The computed snapshot Git blob identity does not imply a stored object.

The seven skips are Windows symlink capability, forbidden newline paths,
three POSIX watchdog/process-group regressions, and two POSIX mode versus
Windows ACL controls. Exact test identities/reasons are retained in JUnit and
the addendum. The five pytest iterable-parametrization warnings are in
`test_ocr_hardscan_io` (one), `test_ocr_layout` (one) and
`test_ocr_run_comparison` (three); the sixth is Starlette's httpx TestClient
deprecation. None is an absent OCR dependency. Warnings remain unresolved.

Third-party pytest autoload and Python/user-site overrides were controlled
under the recorded policy. Other ambient state, installed/native loaded bytes
and the full hosted OS/Python/client matrix are not attested. The gate runner
has no separate subprocess deadline or log-size cap. These documentation-only
updates follow release of the frozen checkpoint; they are not part of its
exact source snapshot. No contents were staged, committed, pushed or released.

## Remaining work

Durable private crop-reference/comparison packs and restart, source-resolution
magnification, explicit reference uncertainty and isolated preview decoding
remain pending. Broader browser/clipboard/IME coverage, independent reference
adjudication, representative held-out accuracy, independent-engine comparison,
adaptive retries and review-effort/downstream measurement remain separate
outcomes. AI scan access and explicit correction publication/rollback are not
implemented by this checkpoint. The complete pending program remains active.
