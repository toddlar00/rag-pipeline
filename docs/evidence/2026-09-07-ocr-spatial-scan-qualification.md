# Local spatial scan qualification, 2026-09-07

Status: the opt-in dense-page scan recipe passed focused, native, actual CLI,
historical-reader and independent retained-evidence review, followed by the
Phase 8 frozen-source local checkpoint. This records dirty-worktree Windows
development evidence, not hosted CI, release approval, representative OCR
accuracy, human review or loaded-native-byte attestation. The
[complete OCR/AI improvement program](../ocr-improvement-program.md) remains
active. This extends, but does not rewrite, the earlier
[Phase 7 record](2026-09-07-ocr-scan-review-qualification.md).

## Qualified behavior and compatibility

The [scan inspection CLI](../ocr-scan-inspection.md) accepts explicit
`--recipe spatial-v2`. The default remains `legacy-v1`; its exact configuration,
five-field work shape and default worker argument forwarding remain unchanged.
The spatial recipe uses a bounded 128-pixel grid and canonical unique neighbor
pairs instead of pre-admitting every possible glyph pair. The original raster,
dark-Otsu threshold, morphology, grouping predicate, region assembly and IDs
are unchanged. This intentionally admits some previously unavailable dense
pages; it is not a purely behavior-preserving refactor or a new OCR engine.

Observation schema 1 accepts only the exact legacy recipe; schema 2 accepts
only the exact spatial recipe. V2 adds classified glyph count, phase, index
entries, bucket lookups and bucket visits. Validators reject hybrid recipes,
impossible counter/phase combinations and inconsistent component/foreground
partitions. Work exhaustion retains an unavailable page and measured work,
never partially grouped regions as a successful result. Pixel, component,
region and cohort limits remain in force. Counters establish consistency, not
authenticated execution, full glyph coverage or a total native-memory bound.

Fresh request/worker/result/readback joins require the exact selected recipe.
Historical reading preserves original v1/v2 declarations and artifacts; it does
not require today's environment to match the historical producer. Diagnostic
and bundle schema 1 remain unchanged and bind the full versioned observation.
The original-raster review adapter is unchanged. No canonical OCR text, index,
AI service permission, model policy or dependency lock changes in this increment.

Root's final coherent focused runs passed 730 distinct cases: 422 policy,
runtime and scan-review cases, then 308 IO, CLI, scan UI and annotation-context
cases. The two new test modules contribute 159 spatial policy and 90 spatial
runtime cases; changed existing IO/CLI modules add 48/11 cases. These focused
counts are subsets of the full suite below, not additional full-suite evidence.
Peirce, Anscombe and Socrates reviewed the runtime, IO/CLI and policy/architecture
boundaries independently of their authors. These are agent reviews, not human
approval or an owner decision.

## Generated native comparison

Source: the existing eight-page generated challenge, SHA-256
`fb337c41515e517f09750a58f8935a8f927fa7a19085099438f28b5e4ffe7e6f`.
Pre-change evidence is retained in
`evaluation-reports/ocr-scan-spatial-prechange-v1/`; its receipt SHA-256 is
`7ac2063ab9520e1bb7170395746b7857968795d405cd732b0b0eca1c4b02be78`.
The final default collector exactly matched all seven pre-change array cases
and the original challenge scan bytes, SHA-256
`adf77ba9f133c6dc578a0133afff97ee06a566a0c5425e89d509713cad5cc9aa`.

The actual NumPy/OpenCV/PyMuPDF comparison preserved all eight pages' complete
geometry, raster evidence, ordered regions, IDs and foreground accounting,
excluding only declared schema/configuration/work differences. There are 503
regions: 108 text-like, one rule-like and 394 ambiguous, with 801,440 threshold
foreground pixels across 34,560,000 raster pixels. Grouping predicate calls
fell from 642,533 to 15,537, with 45,141 spatial bucket visits. These are work
counts, not a calibrated speedup or accuracy gain.

Two physically distinct 1,600-component arrays previously unavailable at the
legacy pair limit became available. Separated glyphs produced 1,600 ambiguous
regions; aligned non-text marks produced 32 text-like groups of 50 marks each.
The latter is a retained false-positive control, not evidence of correct text
classification. No OCR or models were run by the scan experiment.

Native receipt: `evaluation-reports/ocr-scan-spatial-native-v1/receipt.json`,
SHA-256 `4003c391e274c0f0578e78640833e6e8f242c2e3ac10069aaca68ef3a1bc1e69`.
Spatial scan SHA-256:
`93f989e88e61fc551a5699929b9b7275c6ec6c120e243f9d79dc18058bb8759b`.
Socrates' stdlib-only retained audit rechecked 14 captured files and full
comparison scope without rerendering or rerunning discovery. Its
`independent-audit-v1.json` SHA-256 is
`cfc476b80e898ec7cc39259bc579ff0b1829afa95c206a681b1fd75eabf0d3dc`.

## Memory and CLI/replay observations

Eight separately contained Windows native workers exercised four generated
cases under both recipes: the challenge, dense glyphs, dense non-text marks,
and a 5,000 by 5,000 raster with 10,000 components. The last spatial case
reached `region_limit` and returned no partial regions; legacy stopped at its
pair limit. All eight workers exited zero. Largest observed spatial counters
were `peak_wset=306155520` and `peak_pagefile=930033664` bytes, versus
305,889,280 and 929,574,912 bytes for legacy. These cumulative per-process
Windows counters were sampled after discovery but before JSON serialization
and publication. They are not whole-worker-lifetime or process-tree peaks,
worst-case bounds, or evidence that v2 always consumes less memory.

Memory receipt: `evaluation-reports/ocr-scan-spatial-memory-v1/receipt.json`,
SHA-256 `e5f77ba1498a59bfd60cd19bba2236e699581a4a0261aa9314057797c6033653`.
The reviewed helper SHA-256 is
`6c8ddcc14dd910742b3d8963a75aa49fd01b535c822c767ac1aad0ef62f8d25a`.
Its nine artifact and 12 source/interpreter bindings were rechecked separately;
the samples do not change existing resource limits.

The actual public CLI then produced a new source-only spatial bundle. Exit
codes were 3 for the completed manual-review bundle, 2 for the attempted reuse
of its output directory, and 0 for strict fresh/historical readback. Its
`scan.json` exactly matches the native spatial scan hash above. All 30 fixed
input bindings remained unchanged. A valid but wrong legacy request was
refused. No comparison report or installation evidence was supplied to discovery;
historical review separately compared the existing generated recovery report.

The real `ScanReview` renderer replayed eight old and eight new pages, with
discovery forbidden during replay. Each paired 1,050 by 1,400 RGB preview had
identical bytes; original gray digests and first-region IDs/fractions matched.
This covers 16 actual renders and eight first-region pairs, not all 503 crops.
The RGB buffers were not retained: subsequent audit verifies hash-bound
execution assertions and actual renderer delegation, not independently
recomputed preview pixels. The old v1 bundle remained unchanged.

CLI receipt: `evaluation-reports/ocr-scan-spatial-cli-v1/receipt.json`, SHA-256
`f15541f4e726d8add9f132e6a535550fc49a56eaf9d390e7d4e4a4398cb21bca`.
Fresh/historical readback SHA-256:
`afa48343dea7bc7c68ed38a0d46094b6c40aaa425517dce1ef8e3bf969304cd8`.
Socrates independently audited 43 captured files without another native or CLI
run; audit SHA-256:
`84053e5bb21effdd18dad800113575401d1afb7ff451948f788f4fcf173236b3`.
No new browser workflow was executed for this recipe; Phase 7 retains its
separate browser/annotation evidence.

## Architecture and frozen-source checkpoint

Socrates read the complete generated architecture diff, all 33 keyed changes,
and independently regenerated the exact candidate before root promoted it.
Approved canonical SHA-256:
`fd5ff10f872b96a87f11e7a270f54cc0d7d3f27e5bf5c36d3bd1d18d04e3f8f7`.
It records 335 tracked Python sources, 152 non-test modules and 3,422 functions.
All 445 first-party edges and the complete facade owner/runtime/consumer
contracts are unchanged and acyclic. Static/runtime facade bindings remain
956/964, with 161 top-level and 26 nested patch seams. The 1,717,668-byte
inventory remains within the unchanged 1,800,000-byte guard.
Named approval: `evaluation-reports/ocr-scan-spatial-architecture-review-v1/approval.md`,
SHA-256 `4f8f331c828c13b6ddd31f45343a69efc524800e71d10dc8bbeb1c34d8f1617c`.
The same directory retains the full pretty/keyed diff and bounded drift;
the previous canonical generation remains preserved separately.

Bundle: `evaluation-reports/ocr-phase8-gates-v1/`. The existing qualified
Windows CPython 3.12.10 ran pytest, Ruff, source compilation, dependency policy,
model policy, architecture, and the three unchanged offline CI BM25
threshold/baseline gates. All nine exited zero. No dependency/model acquisition
or hosted workflow was performed.

The independent preflight collected 8,967 unique nodes. Every node matches
the JUnit identity multiset: **8,960 passed, seven skipped, six warnings in
822.40 seconds**, with no failures, errors or omitted collected tests. The
seven skips are the specifically reviewed Windows/POSIX capability controls,
not absent OCR dependencies. Warnings are five pytest iterable-parametrization
deprecations and one Starlette/httpx deprecation. All 8/8/6 retrieval queries
and 11/11/10 source records remain accounted for; 40 threshold/regression
predicates passed.

All 471 tracked files, HEAD, index entries and tracked status stayed unchanged
through collection, every gate, subsequent verification and independent audit.
Because this is a dirty worktree, HEAD alone does not identify tested bytes.

| Identity | Value |
| --- | --- |
| HEAD | `e34103f70b676eacc8d55badb2468b8a10004ef4` |
| HEAD tree | `af0a69ccde82b6b679580f07813495721978efe0` |
| Before/after snapshot SHA-256 | `3b42fa5a1180e14984f7415fdda216409db2e2e76a8b4395cbed7183028496ea` |
| Snapshot Git blob identity | `2772fcff7d4aa3ba009ab270756995cd7c4c98df` |
| Index entries SHA-256 | `5d1e877da5ead987eb748098279eee6b75b39802ce3b811e051b58d8d87034eb` |
| Tracked status SHA-256 | `08c4fe01b0b3af575eb65733bed44c5b3724ac69eae64f94505b1ac7e6ca1613` |
| Ordered collected IDs SHA-256 | `b047f9333d986ccfb1ab383dc2456a3fcc31d6c974ff48dec1d2528bcc0131be` |
| Runner SHA-256 | `eb86bd9093e92d915537b09ceb18180c2f7d4534f9960f4cc359d4d52bcb1cde` |
| Receipt SHA-256 | `1d477df71a7ca0bb419528e7f4da054392626b62b62e44089220d9cdde61fca0` |
| Verification helper SHA-256 | `b9f4996107bb2f9f0d9777252dfbe754e471063b55aa6ec370cffaedbfa42d6e` |
| Verification addendum SHA-256 | `fbdcc93706a42a986f352f72e5d05d85ee05a18209a0f9187067fb4fda645331` |
| Independent audit helper SHA-256 | `ecff03d5e537df8bb170fe13fdb83d71a0a8cfc799f7645f52b24b1d4ea10e83` |
| Independent audit SHA-256 | `cbf1594566b729ec00374c18102b11457b9780db54bb9e06c9af2dcf8d6fd87b` |

Root read the complete runner/verifier and independently observed receipt and
architecture pins. The create-only verifier checked 28 retained artifacts,
complete collection/JUnit correspondence, CI commands/thresholds and the frozen
source/index without rerunning gates. Socrates' separately read stdlib-only
auditor checked those bindings and exact skip reasons independently. Third-party
pytest autoload and user-site overrides were disabled under the recorded policy;
other ambient state and loaded native bytes are not fully attested.
Documentation-only result updates follow this checkpoint, not a new code run.

## Remaining work

The dense-page increment is locally qualified within this generated scope;
representative omission recall, non-text false alarms and OCR accuracy remain
unestablished. Crop-edge safety, shared internal-engine allocation safeguards,
missing-text accounting, word/symbol-linked review, independent OCR comparison,
adaptive retries, approved references, AI scan serving and correction
publication/rollback remain work in the full program. Existing AI reader-only
text evidence is unchanged and does not acquire scan or write authority here.
