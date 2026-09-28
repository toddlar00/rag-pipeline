# Local column suggestions and reading-order review, 2026-09-07

Status: local implementation, independent focused/browser-artifact review and
the complete Phase 6 frozen-source local checkpoint passed. This is dirty-worktree
Windows development evidence, not hosted CI, a release, representative OCR
accuracy, native loaded-byte attestation or approval to use private sources.
The [complete improvement program](../ocr-improvement-program.md) remains active.

## Implemented behavior

The new pure policy, fixed-file adapter and CLI produce distinct unconfirmed
`ocr_column_suggestions` v1 reports. Exact source/recovery/candidate bindings,
explicit requested-page coverage, bounded geometry work, visible abstention,
create-only output and failure-path tests are retained. No PDF parsing, OCR,
model acquisition, reference-driven inference or canonical adoption occurs.
The [guide](../ocr-column-suggestions.md) documents the contract and exit codes.

The editor keeps pending bounds outside stored selections and plans. It shows
original numbering/text until explicit left-to-right prose confirmation and
preview; export requires separate review. Pending suggestions block all three
order exports, and dismissal restores prior work. Drafts omit pending bounds
and confirmation state. Fixed input bindings are rechecked around inference
and validation, including when the scan renderer's cache is populated.

## Intentional queue-consent hardening

An installed-Gradio `Queue.push`/`Blocks.process_api` reproduction showed that
queued checkbox values can be combined with newer session State. A stale
prose confirmation could preview another page, and a stale export confirmation
could then publish its layout despite returned checkbox resets. The ignored
original probe is retained as `tmp/ocr_column_queue_probe_v1.py`, SHA-256
`c4b78a0ce2f03c015975e7bc37d81a4845f3e844f4dea12b87a20fae79b16223`.
It used generated temporary data; browser timing was not reproduced.

This is an intentional behavior fix, not a transparent refactor. Every initial
reading-order view now has a client-side non-State token. Preview and manual,
Docling and assignment-order exports compare the captured token before work.
Order changes rotate it. Successful view chains return the new token together
with four unchecked confirmation boxes; failed chains cannot synchronize it.
Tests cover stale requests before and after synchronization, first-view manual
and Docling flows, restart, and successful matching-context exports. The token
is a stale-event guard, not authenticated proof of human review. Reference,
crop and omission approval-context audits remain separate pending work.

## Architecture review

Socrates independently regenerated and approved the full keyed and canonical
diff. Root promoted exactly SHA-256
`31261a745342012a2984a28e2644b58786614f196ec2288f72cf3323db2ea106`:
1,691,616 bytes; 321 tracked Python sources, 147 non-test modules and 3,342
functions. The 956 static and 964 runtime `rag` bindings, complete facade
contracts and consumer sections are unchanged. There is no import cycle or
ownership move. The existing 1,700,000-byte size guard is unchanged.

The full diff and independent reproduction are retained in
`evaluation-reports/ocr-columns-architecture-review-v1/`; named approval digest
is `93e51a2be47aefc8ad9537df51c9a0e00d3b9b290fa9992cafa1262d4e7d83d8`.
The previous architecture bytes were preserved before promotion.

## Focused and browser evidence

Independent final-source checks observed 169 core/IO/CLI tests, 190 review
compatibility/queue tests, and 53 additional boundary tests passing. These
groups are focused evidence, not the full repository suite. Independent
reviewers were Socrates and Peirce (agents, not human approval).

Generated browser artifacts are retained under `output/playwright/ocr-columns-v1`,
`ocr-columns-v2` and `ocr-columns-v3`. The first flow exercised the initial
feature, the second restarted a pending-only draft, and the third exercised
the final view-token source. The final flow covered unconfirmed original order,
refused missing confirmations, successful explicit preview/export, blocking an
older stored plan while a new suggestion is pending, dismissal and a failed
OCR page. Real PyMuPDF rendering and authenticated loopback Gradio were used;
saved OCR was not rerun. Each run's producer/input hashes stayed unchanged,
and shutdown recorded removal of temporary authentication and scan caches.

Anscombe independently reviewed the retained browser artifacts, replaying strict
draft/layout policy without rerunning the browser, PDF parser, OCR or models.
Root read the complete helper/summary and separately rehashed all 41 bindings.
The review confirms three equal before/after snapshots, current v3 listed
producer hashes, exact pending-only restart draft, and the complete 14-line
permutation with original text/geometry/confidence unchanged. V1/v2 are historical
UI generations, not current-source qualification. Screenshots are point-in-time
evidence, not authenticated human consent or a browser queue-race reproduction.
Earlier export/error widgets persist in later views. Auxiliary `.playwright-cli`
and `.rag-locks` directories remain outside scan-cache cleanup claims.

Retained review: `evaluation-reports/ocr-columns-browser-review-v1/`:

- `verify.py`: SHA-256 `c971c45d6e9f805737f83b509b24b197c151eb22e77a67860d127b0ecec648e7`.
- `summary.json`: SHA-256 `b0732638e7919918d9e2ff0a4ee858e1b83314d8dd190460844988e1aa9c3b7a`.

## Phase 6 frozen-source local checkpoint

Bundle: `evaluation-reports/ocr-phase6-gates-v1/`. The controlled Windows
CPython 3.12.10 run used the existing qualified locked environment. No dependency
or model acquisition occurred. All nine gates exited zero: full pytest, Ruff,
Python source compilation, dependency policy, model-artifact policy, architecture
inventory, and the property/constitutional-law/table-family BM25 CI regression
commands with unchanged thresholds and baselines. The three retrieval suites
retained all 8/8/6 queries respectively; independent threshold checks passed.

Pytest reported **8,230 passed, seven skipped, six warnings in 566.69 seconds**.
An independent preflight collected 8,237 unique ordered nodes; every node matches
the exact JUnit identity multiset, with no failures, errors or omitted tests.
The seven skips are Windows/POSIX capability cases: one symlink-permission test,
one newline-path test, three POSIX process/watchdog tests and two POSIX-mode
permission tests. The six warnings are five pytest iterable-parametrization
deprecations and one Starlette/httpx deprecation; these were not suppressed.

All 455 tracked file snapshots, HEAD, index entries and tracked status stayed
unchanged through collection, every gate and independent verification:

| Identity | Value |
| --- | --- |
| HEAD | `e34103f70b676eacc8d55badb2468b8a10004ef4` |
| HEAD tree | `af0a69ccde82b6b679580f07813495721978efe0` |
| Before/after snapshot SHA-256 | `1014708bfb2932f0cda626066dbec6a94914245f672cd979b65bc5d60533732d` |
| Index entries SHA-256 | `bdb587b7a94f7f9fc65fba8e6a112026c143bc5dabe113ce1871a1a0bdb76b74` |
| Tracked status SHA-256 | `7cd3aaf261b5e4695828d5514a8c2c402e200eb2956eed3500cf873841e00953` |
| Ordered collection IDs SHA-256 | `b255fe7993f8960f28ce2c582153d8a798351911aa60b845341b8f6065717aa5` |
| Runner SHA-256 | `b9a1b65dd07223ddd6fe7342c71cd0b5892b5c335a813426e5133332b46b73ab` |
| Receipt SHA-256 | `4b6dcec51290d70ba89367625c7416dbc80033eead4b71807824c827a3371625` |
| Verification helper SHA-256 | `c5e315eeef07c97a9190d911b7baba7448ab24ea20bf416a0a1fbdbf5cdd8674` |
| Verification addendum SHA-256 | `e27bce843de4aa8204c0794a2bf701108afffd61a9842339c44d10957754692d` |

Root reviewed the complete runner/verifier and supplied the receipt digest
observed after terminal completion. The create-only verifier bound 28 retained
artifacts, the approved architecture, exact gate commands, complete test coverage
and retrieval input/threshold correspondence without rerunning gates. Peirce
independently checked the completed receipt/addendum, all captured hashes, current
frozen source/index and collection/JUnit identities using installed pytest
normalization. Both are agent verification, not human approval. Previous
Phase 4/5 receipts and helpers remain unchanged.

Third-party pytest plugin autoload and user-site import overrides were disabled
under the recorded child-environment policy. Other ambient environment, installed
packages, system initialization and native loaded bytes are not fully attested.
This is nine selected local gates, not the full hosted operating-system/Python
matrix or a production qualification.

After independent readback, only documentation result updates were made to this
record, the root/documentation READMEs, evidence index, column-suggestion guide
and improvement program. Python sources, architecture, locks and index entries
were not changed after the checkpoint. These documentation bytes postdate its
frozen snapshot; the original receipt and addendum are not rewritten.

## Remaining outcomes

The known geometry-only prose gain and identical-table regression remain
calibration controls, not proof of correct semantic classification. Preserving
all observed lines cannot reveal text missed by both OCR and layout detection.
Representative references, scan-backed AI evidence, correction publication and
rollback, and the other program outcomes remain pending.
