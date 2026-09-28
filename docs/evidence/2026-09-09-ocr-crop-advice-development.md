# Source-crop advice — focused development verification

Observation date: 2026-09-09 UTC. Four completed focused runs passed **779 unique
tests**, with zero failures, errors or skips. This is original-working-tree
development evidence, **not full copied-source qualification**. The earlier
[review v2 full qualification](2026-09-08-ocr-review-qualification.md) remains an
immutable, point-in-time result for its older generation, not this feature.

## Implemented scope

`ocr_crop_quality.py` measures bounded, host-admitted RGB bytes: integer
luminance histogram/percentiles, adjacent differences, and independent two-pixel
inward edge bands. It distinguishes continuous geometric loss from missing
pixel centers and retains uniform, narrow, empty-band and partial-coverage
ambiguity. Low/high counts are polarity hypotheses, not text detection,
clipping proof, blur scores, scanner resolution or accuracy judgments.

`ocr_crop_advice.py` combines those observations with a nonexecuting proposal
for up to two original-source points of additional context per side. Page and
profile/raster limits remain explicit. A proposed bbox is a **new physical
scope** requiring separate explicit selection, reference and approval; no wider
preview, OCR retry, fallback profile or reference transplantation is performed.
Pre-run review corrections tightened exact-endpoint margin arithmetic and
formatter semantic/binding checks.
The two-point bound uses exact declared endpoint extents; native clipping and
raster rounding may differ, so it does not prove a native edge moved at most two points.

Only uncertainty-enabled live/archive Load and Reload display advice, in the
existing details JSON and success status. These paths capture admitted RGB
once, preserving image/view/profile/hash checks and intentionally tightening
the returned-byte type/length checks. Measurement runs outside session locks
before the existing late context checks. Advice adds no session-loaded, pack,
journal, consent or execution field; callback inputs, outputs and event wiring
are unchanged. Legacy PIL routes remain separate. The Gradio skill and
[official Blocks/event guide](https://www.gradio.app/guides/blocks-and-event-listeners)
informed this preservation of the existing event contract.

The new UI tests use installed Gradio and explicitly inert generated render,
retained-input and publication ports. They cover refusal/owner cleanup,
partial draft/history preservation, cancel/profile/source-binding changes
during held measurement, stale completion and Image.postprocess failure without
reviving unseen-image authority. They do not establish browser paint order,
human inspection or continuous filesystem-change detection after host return.

## Completed runs and independent readback

Artifacts are retained under
`%LOCALAPPDATA%/rag-pipeline/ocr-crop-advice-development-v1/`.
Root ran qualified Python 3.12.10 with `-I -B`, first collect-only and then pytest
with fresh per-run basetemp/JUnit paths. Ambient Python/Pytest/Git selectors were
scrubbed, plugin autoload and cacheprovider disabled, Gradio analytics disabled,
and temporary/Gradio/example caches directed into the owned run directories.
This is an execution policy, not a complete environment or sandbox attestation.

| Run directory | Exact collected / passed | Collection seconds | Pytest seconds | Root terminal evidence |
| --- | ---: | ---: | ---: | --- |
| `pure-v1` | 107 / 107 | 0.23 | 1.65 | `919a4e` |
| `ui-v1` | 56 / 56 | 0.56 | 8.47 | `6d496d`, closed `947cb1` |
| `regression-v1` | 557 / 557 | 0.99 | 123.51 | `5689a5` |
| `architecture-v1` | 59 / 59 | 0.16 | 4.23 | `e01999` |

The pure run contains 65 pixel-quality and 42 advice cases. Regression covers
the existing live/archive, uncertainty/queue, common/preview and consumer-disposal
files. Architecture runs only three fixed-path/import families: 28 heavy-import
denial cases, 20 direct-import policy cases and 11 archive-no-live-host cases.
It adds six cases for the two new pure modules and characterizes seven new
first-party edges plus two lazy PIL import roots. **No tracked graph, inventory
generation, full architecture file, cap change or baseline installation ran.**
Ruff over all eight changed source/test files passed; its final retained log is
`architecture-v1/ruff.log` (root also retained earlier seven-file success `355e6d`).

Independent AI-agent readback (`efdde9`, then `3b7e20`, both exit 0) reconciled
every ordered collection identity against JUnit, including unique identities
across all four runs, and rehashed all 13 retained log/XML artifacts before and
after reading. No cases overlap. JUnit suite clocks are separately 1.623,
8.466, 123.501 and 4.209 seconds; these are not native runtime measurements.
No tests were rerun by the auditor. An initial read-only PowerShell hash command
had a parse-time error (`fef0cc`), corrected before execution (`906fa2`); it was
not a test failure or artifact discrepancy.

Independent source review read the complete quality module/tests (`2122e4`,
`8c3cd8`, `1f6acc`, unchanged rehash `83a6a2`) and the UI delta/new tests against
the prior immutable copy. Review approval is from collaborating AI agents,
not independent human adjudication or proof of native/browser behavior.

## Source and retained artifact identities

Root rechecked all eight source/test pins and protected original bindings after
the four runs (`613b6e`). HEAD remains
`e34103f70b676eacc8d55badb2468b8a10004ef4`, tree
`af0a69ccde82b6b679580f07813495721978efe0`. Original raw index SHA-256 remains
`6a841daa2ac15f397750fc0f88f1bd6e93b78326823d2452e3ab14c88d0335f3`;
the retained original logical-entry pin is
`affc4116b70e1a0f5dceedab99de84135c9924226eb039df4ee5fbd4c69708f2`.
Original architecture baseline SHA-256 remains
`8c9d9991aa6864131915b6f0392ae4bf4c3f9a4af5e8ae0757e78e9d47aa792e`;
guard SHA-256 remains
`8d5767bf8a1f428554077a2b16ca7209feee0a0b2d7fb8984e2abcfec248b38b`,
with its **1,952,138-byte cap unchanged**. These are uncommitted source changes;
there is no new frozen full-source cohort or modified original index/history.

| File | SHA-256 |
| --- | --- |
| `ocr_crop_quality.py` | `731ffd5348baa31f15a9d3ded980757f7589c08b1eda9b838ebce6fd07c6cfde` |
| `ocr_crop_advice.py` | `30f5d9188d40f5e6b73515ebac5e5c9b23494ec18411dedf80f01a5ca4909e1d` |
| `ocr_review_crop_uncertainty_live_ui.py` | `1f2ee6bf690547f12386916138fe2b4f1e03fdad92eb2c25d7472173bd65a2f5` |
| `ocr_review_crop_archive_ui.py` | `4f94b32721a55cc5138e91cad0045308fedc7042d63570975d1ca9a0ed0d955a` |
| `tests/test_ocr_crop_quality.py` | `d1de9ed4a0e8f7b5d1de9f0d9f6c390b92bd689863e2bdb41f9a85576335808d` |
| `tests/test_ocr_crop_advice.py` | `cc47aaf5106cf479d6174aa1384b47c1dd5fdb484e17cd1c55441bdde438071c` |
| `tests/test_ocr_review_crop_advice_ui.py` | `904d26b1bf7184a4e2c0b4164fc75f5fb2fa20be358f1ebd1197ec0cb43bc360` |
| `tests/test_architecture.py` | `23d515fcd889e410b924514c9ae1ba1acba1a0d70d1165f41f482afe9807e7b7` |

| Relative retained artifact | SHA-256 |
| --- | --- |
| `pure-v1/collection.log` | `f836fcce1ac5d6210a6ad90a9bffa0492f2b19ef930f5742b10b2b5a506ebbd9` |
| `pure-v1/pytest.log` | `8a604aa6f3387e41b48d25e837907625a05f31aef04d78f81e71648435676a4b` |
| `pure-v1/pytest.xml` | `db25d604d5bc19dfc83f4ed1faefec8ab4f0bb6e7c4282944169ab1b42c21c06` |
| `ui-v1/collection.log` | `5d6b7494c8b2d36bb8386f4dc432b8e5525c6f749b3e3ab213a042c7035c308f` |
| `ui-v1/pytest.log` | `2fab50e2f8399915787faeeb89a0a0c640f480a29ad1180da04ddc3fb7668186` |
| `ui-v1/pytest.xml` | `83d1da4eb0236f7c39484c8cd6d39c7f21876bf6d6b7b6cb4bcf30b7744e3a07` |
| `regression-v1/collection.log` | `2c80c1648c57d9ace51c139ec94ff2016c871c094a2fc6264d7f092c8c1f2561` |
| `regression-v1/pytest.log` | `3401703e8f5b2368de3b4e607b961cb65f79d9be96a8269d2c8fe2b0b67e4f8f` |
| `regression-v1/pytest.xml` | `036388b90830706620494131f2267f99f134433a5013385126119a5b869f5250` |
| `architecture-v1/collection.log` | `c1bba0cc6e53c84b658d3ebd505868d831ee17bf2797a63771b57c8e03a49e92` |
| `architecture-v1/pytest.log` | `238fbe23e2b7d729a3354ebec8e2fcfef90d76c49f555e67ed64fa5c2b67e116` |
| `architecture-v1/pytest.xml` | `e5af25d4707bb23db4a025593517710b0611baec9715b8c7ff3c50f33909df10` |
| `architecture-v1/ruff.log` | `a4443afdcfb6d7363adb285762515ccf7cf50473b1a05c20c1a50f6bed4d26b0` |

## Remaining acceptance

No native PDF/OCR, real browser, private corpus, model download or second-engine
experiment was run for this change. No representative held-out accuracy gain,
clipping-detection sensitivity, image-quality calibration, latency improvement,
human-review independence, release or complete-program result is established.
Existing crop bounds, explicit confirmations, same-scope scoring and immutable
pack rules remain in force. The wider-context proposal has no execution control
and does not grant new corpus, canonical-text or AI-reader authority. Prior
private-cache cleanup limitations are unchanged, not resolved by these tests.
Positive native/browser advice acceptance and a new complete copied-source
qualification, including fresh inventory review under the unchanged cap, remain
pending. All prior qualification and failed-attempt records are retained.
