# Local OCR engine-guard qualification, 2026-09-07

Status: the shared allocation guard and corrected raw-dispatch accounting passed
focused failure injection, generated native compatibility checks, independent
code/architecture review and the Phase 9 frozen-source local checkpoint. This
is dirty-worktree Windows development evidence, not hosted CI, release approval,
representative OCR accuracy or loaded-native-byte attestation. The
[complete OCR/AI improvement program](../ocr-improvement-program.md) remains
active. Earlier [Phase 8 evidence](2026-09-07-ocr-spatial-scan-qualification.md)
and all executed helpers/bundles are preserved separately.

## Qualified behavior and limits

The [shared guard](../ocr-engine-allocation.md) checks selected Global resize,
padding, detector, rectified-crop, aggregate-crop, classifier/recognizer batch
and remap allocations across ordinary-page, region, hard-scan and stage readers.
It preserves existing models, settings, pixels and admitted candidates. This is
intentional safety hardening, not a universally behavior-preserving extraction.
A conservative float32 crop margin can refuse a near-cap crop whose exact
native dimensions would fit; the documented 6,000 versus 6,001 boundary is an
explicit regression control. These checks do not bound model weights, all
decoder/native intermediates, arbitrary model outputs or total process memory.

The accounting repair records raw RapidOCR dispatch only after guard setup.
Preparation and partial hook-installation failures consume no call ID. Raw
exceptions record failed calls; a normal raw return remains completed even if
later guard restoration, suppressed-failure checks or candidate validation
reject its output. A completed call is not an accepted candidate. Stage reports
use the existing `invalid_stage_output` reason where appropriate; their strict
schema/join validator was not weakened. Hook cleanup drains all restorations,
preserves cancellation and poisons an incompletely restored engine.

The supported recorder compositions are `TimedEngine(Guard)` and
`StageRecorder(Guard)`, not arbitrary nesting. The observer must dispatch once,
on its original thread and within its lifetime, returning the identical result.
Generic callable behavior remains separate. Timings exclude guard setup and
restoration, include in-call checks, and are not compared with earlier wrapper
timings as evidence of a speedup. No report/receipt schema, canonical text,
index, model lock or AI service permission changed in this increment.

## Failure-injection and native evidence

New existing-API controls first produced 18 expected failures and 16 passing
controls against the pre-repair wrappers. All 34 passed after the repair;
53 further observer/lifecycle controls cover misuse, cancellation, budgets,
reentry, partial setup and restoration. Root's final combined 14-file run
passed 765 tests, including architecture boundaries. These are subsets of the
full suite below, not extra whole-suite results. Earlier 4,580-test OCR-only
evidence belongs to the preceding guard revision and did not detect the later
setup-accounting defect. Peirce and Anscombe independently reviewed the applied
repair; the original policy, guard and reader wiring also had separate agent
reviews. Agent reviews are not human approval.

The retained eight-page generated source has SHA-256
`fb337c41515e517f09750a58f8935a8f927fa7a19085099438f28b5e4ffe7e6f`.
The final native replay used the production execution recorder and one cached
guarded engine per route: eight ordinary pages, two regions and two explicit
hard-scan orientations, totaling 12 actual calls. All three complete candidate
files are byte-identical to the pre-guard baseline: 118 ordinary lines, 24
region lines and 24 hard-scan lines. Text, scores, boxes, crop/transform geometry
and exact BGR input bindings match; runtime and model/session configuration
match except separately retained durations. Real receipt IDs stay sequential;
a distinct ordered mapping binds them to fixture selections without renaming.

| Artifact | SHA-256 |
| --- | --- |
| Pre-change manifest, `ocr-engine-guard-native-prechange-v1/manifest.json` | `31202cbb9b08c8d4c256a44af77e9d886890a2c7eb5c63c011ad4210a3f0089c` |
| Final native helper, `tmp/ocr_engine_guard_native_postchange_v2.py` | `c2df61f04a56171703108a6217934a0a50ff41fb982437df8281119d89cbe533` |
| Final native manifest, `ocr-engine-guard-native-postchange-v2/manifest.json` | `95ff97681b38356252a9db2c2903da487461907d6b46baa5665e720801c07af3` |
| Final complete comparison | `375e991ec7e90104260b1c9e3d5f53677d710657f427726382c4a8c2bb54068b` |
| Final dispatch mapping | `b8e788ae859c041128aaa13c3275a282e7a38535239a78f10932ebf7b569f352` |

Native bundle paths above are under `evaluation-reports/`. Root checked complete
candidate bytes and all 12 mappings directly. Peirce independently reread the
helper and checked all eight final artifacts, baseline bindings, 35 fixed inputs,
three model files and receipt source maps. This healthy generated comparison
does not demonstrate accuracy gains, native failure-path coverage, native
StageRecorder composition, total-memory bounds or universal compatibility.
Prepare ordering/input immutability are observations of the reviewed producer,
not an independently retained native execution trace.

## Architecture and frozen-source checkpoint

Peirce read all 41 keyed changes and the complete 685-line architecture diff,
then independently regenerated the exact candidate before root promoted it.
Canonical SHA-256:
`21cd4e3db059c4e653673bd2e97feb7c0722abc54064762f155e844f50915845`.
It records 344 tracked Python sources, 154 non-test modules, 3,471 functions and
453 acyclic first-party edges. Two new production modules and seven new test
files are included. Facade ownership/runtime/consumer contracts remain unchanged:
956 static and 964 runtime bindings, with 161 top-level and 26 nested patch
seams. Its 1,732,779 bytes fit the unchanged 1,800,000-byte guard.
Named approval: `evaluation-reports/ocr-engine-guard-architecture-review-v2/approval.md`,
SHA-256 `82d1aacf8751207a271d8240acd6f24ff60c5baf821641f7451d31b899ccb329`.

Bundle: `evaluation-reports/ocr-phase9-gates-v1/`. Qualified Windows CPython
3.12.10 ran pytest, Ruff, source compilation, dependency policy, model policy,
architecture and the three unchanged offline CI BM25 threshold/baseline gates.
All nine exited zero. Independent collection found 9,394 unique nodes; every
node matches the JUnit identity multiset: **9,387 passed, seven skipped and six
warnings in 850.48 seconds**, with no failures, errors or omitted collected tests.
The seven skips are specific Windows/POSIX capability controls, not missing OCR
dependencies. Warnings are five existing pytest iterable-parametrization
deprecations and one Starlette/httpx deprecation. All 8/8/6 retrieval queries and
11/11/10 source records are accounted for; all 40 threshold/regression predicates
passed. No model/dependency acquisition or hosted workflow was performed.

All 482 tracked files, logical index entries, tracked status and HEAD stayed
unchanged through collection, every gate, verification and independent audit.
Raw index byte identity was additionally checked during the independent audit;
the original gate snapshots did not attest raw index bytes. HEAD alone cannot
identify these dirty-worktree tested bytes.

| Identity | Value |
| --- | --- |
| HEAD | `e34103f70b676eacc8d55badb2468b8a10004ef4` |
| HEAD tree | `af0a69ccde82b6b679580f07813495721978efe0` |
| Before/after snapshot SHA-256 | `6cfb5dea26df0cd982f1a25f68e1bd4b5fcbea82c4429270b9c95a1fec74f110` |
| Snapshot Git blob | `561c857af2d5fd874510f4914721b769768c1acb` |
| Index entries SHA-256 | `e3184bfd0ab455c79ceef78705590d1d21ab80acb882a56fa8d1b93ea51cc279` |
| Tracked status SHA-256 | `7d24f62db088cd011667c54325b132feb6b14402ad159097e3f8cf095a3350ee` |
| Ordered collection IDs SHA-256 | `5fbc26fbea8b14a71a8a570af84fea818dbf734db2f19b6fbbc7d352603fd492` |
| Runner SHA-256 | `2cb973d8ceeb66a46088c43805b39190adbdd144309940f314f9b367a8756e4f` |
| Receipt SHA-256 | `a36cc9c256867b581b52372469ae5b077de20ea247a7b0b1f4b30586ccd99b19` |
| Verification helper SHA-256 | `2218985355e6a78e1f809593045ae9fce60af5153450785b0a4de4ec88bb33f7` |
| Verification addendum SHA-256 | `b9532e1c912900dfae31cf557b395f023cd3ded4e6e99375307861dfaca328f7` |
| Independent audit helper SHA-256 | `96510faabae525f6456c872cd098e24bcf9686a58e431b1c5ee9e6b0a2a3ab69` |
| Independent audit SHA-256 | `e18fcd35838c515a4a784483cdc0f178c1de405a06a216c5967b824693f3ccb5` |

Root fully read the runner, verifier and independent audit helper. The verifier
checked 28 retained artifacts, complete collection/JUnit correspondence, CI
commands/thresholds and source/index bindings without rerunning gates. Peirce's
separate stdlib-only audit checked those bindings and every allowed skip reason
independently. Third-party pytest autoload and user-site overrides were disabled
under the recorded policy; other ambient state and loaded native bytes are not
fully attested. These documentation-only result updates follow the frozen
checkpoint; later implementation needs its own qualification.

## Remaining work

Same-call missing-text disposition is still a design, not a delivered diagnostic
at this checkpoint. Representative accuracy, independent-engine comparison,
adaptive retries, review-effort measurement, approved reference inputs, AI scan
serving and correction publication/rollback remain unfinished. Existing AI
reader-only text evidence stays unchanged and acquires no scan/write authority.
