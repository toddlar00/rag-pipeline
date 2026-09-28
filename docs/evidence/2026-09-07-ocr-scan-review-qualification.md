# Local scan inspection and annotation review, 2026-09-07

Status: independent focused, native, historical-reader and retained-browser
review plus the Phase 7 frozen-source local checkpoint passed. This is
dirty-worktree Windows development evidence, not hosted CI, release approval,
representative OCR accuracy or loaded-native-byte attestation. The
[complete OCR/AI improvement program](../ocr-improvement-program.md) remains
active; this record does not complete its outstanding requirements.

## Qualified behavior

The [source-pixel inspection workflow](../ocr-scan-inspection.md) adds a strict
pure policy, model-free native collector, contained CLI/private fixed-file
bundle and separate original-raster editor adapter. Discovery receives only
original grayscale pixels, not OCR, layout or references. Saved boxes are
compared afterward; failed or incompatible comparisons stay unevaluated.
Text-like, rule-like and ambiguous ink hypotheses remain available for review.
No hypothesis certifies text, correct recognition or complete page coverage.

The fixed recipe renders annotated original pages at 300 DPI and applies a
global dark-Otsu threshold. Page, raster, component, pair and region budgets
precede bounded work. Requested but unavailable pages remain explicit. Pale,
colored or reversed text and non-text false alarms remain unmeasured limits;
accounting for threshold foreground is not accounting for every source glyph.

Fresh bundle completion rechecks exact request, source, optional comparison
inputs, recorded producers/locks and artifact bytes. Historical review instead
validates the original declarations and bindings without pretending today's
environment executed the historical run. It rebuilds current comparison
diagnostics separately, preserving the original bundle. Before an editor
overlay or crop is exposed, the adapter reproduces and hashes the original
gray raster; it never maps original hypotheses onto a preprocessed canvas.
Adding a hypothesis creates only a crop draft, with separate export review.

## Intentional annotation-context repair

The preserved installed-Gradio queue probe is
`tmp/ocr_annotation_queue_probe_v1.py`, SHA-256
`248f31124a47cfa688dbfc64a10302e56fb6287811eaa7bc1793e06a504dfe57`.
Its pre-fix observation is
`evaluation-reports/ocr-annotation-context-probe-v1/observation.json`, SHA-256
`0cdcc4ada039f96e9ca2fc3486a221bee88a9dbd78afe88f47ea30f161889ee8`.
Actual `Queue.push`/`Blocks.process_api` calls reproduced stale reference,
crop and omission actions being combined with a newer annotation context.
This is an intentional correctness repair, not a transparent refactor.

Captured non-State annotation tokens now bind actions to the displayed view.
Page, tool, focus and annotation changes rotate the token; successful event
chains synchronize it with unchecked confirmations. Stale actions fail before
mutation/export, including new scan page/region selection and crop controls.
Draft restart restores neither approval nor transient scan focus. These tokens
prevent stale-event correspondence errors; they do not authenticate human
review. The Gradio-guided event checks and Playwright browser workflow are
complementary: screenshots alone do not reproduce a deterministic queue race.

The seven added test files contribute 422 collected cases: 83 scan policy,
56 native runtime, 117 bundle IO, 46 scan CLI, 34 original-raster adapter,
49 annotation-context and 37 scan UI/CLI controls. They are included in the
full collection below, not counted as an additional independent full suite.
Peirce, Anscombe and Socrates independently reviewed their respective policy,
runtime/IO, adapter and UI boundaries; these are agent reviews, not owner or
human approval.

## Generated native and browser evidence

The existing eight-page generated challenge has source SHA-256
`fb337c41515e517f09750a58f8935a8f927fa7a19085099438f28b5e4ffe7e6f`.
The actual contained source-only scan CLI completed with manual-review exit 3;
a separate strict fresh readback exited zero. All eight 1,800 by 2,400 rasters
were available: 34,560,000 pixels, 801,440 threshold-foreground pixels,
108 text-like, one rule-like and 394 ambiguous hypotheses (503 total).
All 27 declared input/producer bindings stayed unchanged. All 503 OCR/layout
comparisons were unevaluated because no comparison inputs were supplied;
zero potential-omission flags must not be interpreted as zero omissions.

Native receipt: `evaluation-reports/ocr-scan-native-v1/receipt.json`, SHA-256
`4f845ce66b9814643d6e7dc5407b663ea0df06b941fe04cf8de82a21da6fd446`.
Fresh readback SHA-256:
`abdf2bf2577ddc40c642a6421810885795ce9fc8d0cbf1af78f1ea89e14b0524`.
Installation evidence was explicitly not supplied to this invocation. The
model-free native claim rests on reviewed producer code and declarations, not
an OS-level module/network trace; the fresh readback separately blocked native
and model imports.

The real historical reader/editor probe reproduced all eight original gray
pixel digests and returned 1,050 by 1,400 previews. It performed 16 raster
renders: eight previews and eight first-region crop lookups, not 503 crop
checks. Its 15 before/after bindings matched, with no attempted model imports.
Receipt: `evaluation-reports/ocr-scan-review-native-v1/receipt.json`, SHA-256
`2d2e81de59fc2134ec314d3e19ffac8699530dd24568fdf907c2ee7cb1c1cc88`.
Saved recovery/layout comparisons are generated/scripted fixtures, not reviewed
representative references.

Authenticated loopback browser runs are retained in
`output/playwright/ocr-scan-v1` and `ocr-scan-v2`. The first inspected a source
hypothesis on a failed-OCR page, added its exact original-space crop, refused
unconfirmed export, then exported after confirmation and saved a draft. The
second restarted that draft, retained the crop with approval unchecked and
refused export. The exact pixel box `[153,132,799,188]` on a 1,800 by 2,400
raster matches the exported original-page fractions. No OCR was run by the
editor, and no canonical text or index was changed.

Anscombe independently checked 53 retained/current bindings, exact 16/17
browser input sets, four PNGs and seven DOM snapshots, including strict
draft/export replay. Root read the complete verifier/summary and independently
rehashed all 53 bindings. Review helper SHA-256:
`f2132d36e68fdca7ca94c69b1eabd7539009b85ebfe484ac3f51197c134a385f`.
Summary: `evaluation-reports/ocr-scan-browser-review-v1/summary.json`, SHA-256
`f58ff1122c68a9b5f2833bf0bfdf515c31081a02896370322ed4b7f8676c93ea`.
Both server exits were separately observed as zero by root; owned browsers
were closed. Authentication files and private image caches are absent.
Auxiliary browser/lease artifacts remain outside that cleanup claim. Retained
views are not a complete click/network transcript, execution authenticity,
human consent or proof that no transient file ever existed.

## Architecture and frozen-source qualification

Peirce independently approved the complete candidate diff and rechecked it
against current tracked sources. Root regenerated the canonical inventory and
required exact equality with approved SHA-256
`3a4467b08e90ad892dbb7a720cf51dfd9b3bc5300d53a45a03a75e0ee91975ad`.
It records 333 tracked Python sources, 152 non-test modules and 3,415 functions;
the full `rag` contract, 956/964 static/runtime bindings and 161/26 patch seams
remain unchanged, with no import cycles or ownership move. The inventory is
1,715,928 bytes; the explicitly reviewed repository size guard increased from
1,700,000 to 1,800,000 bytes, without changing canonical equality, semantic
validation or the separate 4 MiB runtime-probe bound. Full diff/review remain
in `evaluation-reports/ocr-scan-architecture-v2.diff` and
`evaluation-reports/ocr-scan-architecture-v2-review.md`.
The previous canonical bytes remain in the independently regenerated Phase 6
candidate, SHA-256 `31261a745342012a2984a28e2644b58786614f196ec2288f72cf3323db2ea106`.

Bundle: `evaluation-reports/ocr-phase7-gates-v1/`. Existing qualified Windows
CPython 3.12.10 ran all nine selected local gates: pytest, Ruff, source
compilation, dependency policy, model policy, architecture and the three
unchanged CI BM25 threshold/baseline commands. No dependencies or models were
acquired. Results: **8,652 passed, seven skipped, six warnings in 810.97 seconds**.
The independent preflight collected 8,659 unique nodes; every node matches the
JUnit identity multiset, with no failures, errors or omitted tests. Skips are
seven Windows/POSIX capability controls, not missing OCR dependencies. Warnings
are five pytest iterable-parametrization and one Starlette/httpx deprecation.

All 468 tracked files, HEAD, index entries and tracked status stayed unchanged
through collection, every gate, verification and independent audit. All 8/8/6
retrieval queries and 11/11/10 source records were retained; 40 threshold and
regression predicates passed. This is not the complete hosted CI matrix.

| Identity | Value |
| --- | --- |
| HEAD | `e34103f70b676eacc8d55badb2468b8a10004ef4` |
| HEAD tree | `af0a69ccde82b6b679580f07813495721978efe0` |
| Before/after snapshot SHA-256 | `74d161a3b2b8ae3b1cc43898b9560e6365efe33584e08af0291795acf4c3617b` |
| Snapshot Git blob identity | `ff601041e333976798beb3746ba67e807164263f` |
| Index entries SHA-256 | `d8a9b03392bce1167760058432bab95e0baa947fc660e34a7986d15de94d83d2` |
| Tracked status SHA-256 | `262a4f49f9183eb9d639fb66e6a38fe479e4991750ec91acc254b8ec45fc6a1f` |
| Ordered collected IDs SHA-256 | `d40ce6e080b3c3107f6bb5f1d076b83e66161a2569105f8d68a936fab9fce6f6` |
| Runner SHA-256 | `d75249081e8d1a7c220b8ca8644520791861fbc93258bf4bf1aff40be99f6834` |
| Receipt SHA-256 | `0fb0a666a8a790bba9144ed68fab998f84ba4407ccf0d87cf70646d3d260237b` |
| Verification helper SHA-256 | `34396bbdcfb9df5c2aa322d40c1eee6d70149535a487e378ed84098e6ec136ef` |
| Verification addendum SHA-256 | `cc31ed9b0f0cef4bf6bb2748cc43a6ecee039944f556aa897a26b021ee1ea252` |
| Independent audit SHA-256 | `f29c2476120043b3e6873922afc3a189da6d2c0a0058b3be0aab254c85eefaec` |

Root fully reviewed the runner/verifier and supplied separately observed
receipt and independently approved architecture pins. The create-only verifier
checked 28 retained artifacts without rerunning gates. Socrates independently
read back the artifacts, collection/JUnit identities, CI arguments, thresholds
and current frozen source/index with a stdlib-only auditor; root read that
complete helper. Audit helper SHA-256:
`dc0c55ddf2b9760867884ded20fd5573db767a4e130ffee5edd990f967c6bca6`.
Third-party pytest autoload and user-site overrides were disabled under the
recorded policy; other ambient state and native loaded bytes are not fully
attested. Documentation-only result updates follow this frozen checkpoint.

## Next increment, explicitly not qualified production behavior

An isolated, independently reviewed stdlib geometry prototype matched complete
ordered edges and canonical glyph-group records in 323 generated cases (322
old-admitted, one newly admitted) and passed seven budget-failure controls.
It finished its measured work in 3.48 seconds. A 1,600-distant-glyph case that
exceeds the old pair cap used 1,596 bucket-entry visits and zero final predicate
tests, retaining the exhaustive oracle's 1,600 ambiguous glyph regions. This
is not measured OCR accuracy or a production fix; rule/non-glyph interleaving,
whole-page IDs, native foreground accounting and process memory remain outside
the experiment. The elapsed-time ceiling is cooperative, not hard termination.

Observation: `evaluation-reports/ocr-scan-spatial-probe-v1/observation.json`,
SHA-256 `7e6fadd12e091b62dc6c6b5113954c193241469265de8a230ad79fada92116cd`.
Its helper SHA-256 is
`26da9e1bfed7a1c67e9f675f515e40d31749f9853c8e9b3ddc271d7892ea36d4`.
An explicit new recipe/work contract and native regression evidence are still
needed before production adoption. Representative approved inputs, independent
OCR, adaptive retries, scan-backed AI serving and correction publication/rollback
also remain open; none is completed by this local checkpoint.
