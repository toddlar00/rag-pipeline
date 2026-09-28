# Crop-review pack backend development checks, 2026-09-07

Status: the historical reader, private pack contract/store and live raw-capture
boundary passed focused development checks. This is not complete Save/Open UI
delivery, a full-suite checkpoint, representative accuracy or release approval.
The [full OCR/AI program](../ocr-improvement-program.md) remains active. See the
[pack contract and outstanding integration](../ocr-crop-review-packs.md).

## Source identity and scope

Dirty-worktree base HEAD: `e34103f70b676eacc8d55badb2468b8a10004ef4`;
HEAD tree: `af0a69ccde82b6b679580f07813495721978efe0`.
The changes below are not claimed to be in that commit. Blob IDs are computed
with `git hash-object`, not claims that those objects were committed or stored.

| File | SHA-256 | Computed Git blob |
| --- | --- | --- |
| `ocr_disposition_archive.py` | `d3a18b3cbf313dad897dc43627645ad3f1f54379b8a7b058f85d99b53ffd46c8` | `5f131a881e235fb9a9aff345760dd3f6926dfc2e` |
| `ocr_crop_review_pack.py` | `c54842e90cb52550f5b17924fef5a330ea9fec6d88a3af69e0513c0d16a97bc8` | `7c2a3deb5d9529bd9414e3ee507afa472fba5ada` |
| `ocr_crop_review_pack_io.py` | `434b8c0395416c35a836e087d41c0c94d0fb2528ee1c4cb300558c81f2bbd384` | `eaedd2eed90c395ff4e2c1279a15815ea648a661` |
| `ocr_review_execution.py` | `1d134c2106c2141522c565e315653da0fe8c58773efbed1357137bb8c3e0a5c2` | `188ceea25972762090ccdd2dac4d7e1fa4116962` |

No prior checkpoint artifacts, canonical extraction, index, default OCR recipe,
legacy draft schema, model/dependency lock or AI-reader authority were changed.
No representative private documents or derived reference text were introduced.

## Focused tests and reviews

The qualified Python 3.12 environment completed this selection with **469 passed
in 128.83 seconds**, terminal exit 0 (`b94504`, original session `64734`):

```text
python -m pytest -q tests/test_ocr_disposition_archive.py tests/test_ocr_crop_review_pack.py tests/test_ocr_crop_review_pack_io.py tests/test_ocr_review_crop_pack.py tests/test_ocr_review_crop_comparison.py
```

The selection contains 186 archive, 165 pack-policy, 86 storage, 14 new live
capture and 18 existing live-comparison cases. Counts from earlier overlapping
runs must not be added. Policy doubles are explicitly distinguished from full
generated/inert report/receipt/disposition validation. Live-capture fixtures
control native rendering, inference and runtime/model observations; they do not
establish native accuracy. Focused production/test Ruff checks also passed.

Independent agent review found and closed two main-boundary issues: the final
raw-file rehash loop needed a subsequent source/workspace verification, and
fixed slot-map cardinality/type checks needed to precede set allocation. The
first has a targeted source-mutation regression; the pure pack's allocation
controls use module-local set sentinels. Storage review also separated draft-only
recovery from unrelated proof traversal, moved the lease sentinel outside the
catalog and added per-save generation checks. The final source generations have
independent scoped read approval; this is not human adjudication.

## Retained actual-result save and fresh-process reopen

The original generated Phase 11 browser runs were retained unchanged. The new
historical reader replayed their actual 300/400-DPI reports and exact displayed
comparison after subsequent source changes. No new OCR or rendering was used.

The reviewed helper `tmp/check_crop_pack_storage_v1.py` was invoked twice with
the qualified interpreter and `-I -B`, first `publish`, then `reopen` after the
first process returned terminal exit 0. It refuses optimized execution. The
publication process was PID 46776 (4.487 seconds); the fresh reopen process was
PID 25856 (0.735 seconds). These are small generated-case observations, not a
general performance estimate.

Publication preserved 25 payload files totaling 1,147,919 bytes, with a final
canonical manifest and 68 per-save helper-generation checks. Restart discovery
returned a fresh unverified catalog capability without selecting it. Strict
readback then reproduced the original comparison, and separate draft recovery
returned no candidate, metric, image or approval. No PDF or model binary was
copied. The saved image hash remains a historical declaration, not image proof.

Evidence is under `output/pdf/ocr-crop-pack-retained-v1/`:

| Artifact | SHA-256 |
| --- | --- |
| `publication.json` | `2260e39b0fd5e656254b6b95cd5dad3cca847a9377f559e41cbc1ba82a073932` |
| `reopen.json` | `0dbade39d7309e70d99a3dab1fb0ed7d37d17c5dce10f4b58830a5bc9528a259` |
| Saved canonical pack manifest | `1e20a5cd3b6a7fc1b57daee9aa75c0880feca4aee2ea95f838322d3a3dcd100b` |
| Original UI comparison bytes | `f1ef1327da5f36e7a287b56b6cd7051b77bcdb1e3f2aa1e693cbeb9e253a8456` |
| `tmp/check_crop_pack_storage_v1.py` | `c0e3c0e949da4ac1630498bc4c1b0a3e9c7374c1c81cd791ff9e6c18fa30e18e` |
| Imported `tmp/check_crop_pack_retained_v1.py` | `8791ad530aa62c646d30c1a45831107bf63f9d8a9a244f44afc03ea329e01f65` |

Both processes disabled selected current-runtime/model-capture functions and
blocked native OCR/render imports. The restart also guarded Python opens of
the old run/UI directory. These are cooperative checks, not an OS sandbox or
universal filesystem isolation. Six selected producer hashes were checked, not
a complete frozen-source closure. The earlier read-only helper's zero-write
field describes explicit artifact publication, not a bytecode-write audit; the
two-process storage check explicitly disabled bytecode writes with `-B`.

Independent retained readback (`03662f`, exit 0, 0.622 seconds) verified the
26-file tree, all payload bytes, six selected producer pins and exact original
copies from 20 distinct fixed inputs. It also replayed both archives and the
original UI comparison without OCR, rendering or publication. Recorded process
IDs are distinct and cross-bound; separate invocation is established by the
root's terminal tool results. Fresh catalog-handle inequality was asserted by
the successful reviewed helper, but its new handle value was not retained for
an independent retrospective comparison.

## Outstanding work

Save/Open controls, fresh-source display and renewed confirmation, consistent
UI/store lock ordering, browser race/restart checks, architecture inventory
refresh and a new full-suite/all-gate checkpoint remain pending. The unchanged
architecture inventory still belongs to Phase 12, not these new modules.
Representative accuracy and the remaining AI/evaluation/publication requirements
are not established by this development record.
