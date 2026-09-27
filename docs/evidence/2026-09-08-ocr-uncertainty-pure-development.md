# Crop uncertainty pure-component development, 2026-09-08

The new pure raster-view and authoring-journal components pass **476 focused
tests** (338 geometry, 138 journal). Together with the unchanged v1 crop
comparison suite, **570 tests passed in 5.43 seconds**, with no failures, errors,
skips or warnings reported. These are generated scalar/numeric controls, not
OCR accuracy, actual rendering, UI acceptance or full-source qualification.

## Implemented scope

- `ocr_crop_raster_view.py`: closed, source-bound metadata; separate commanded
  and native numeric representations; exact binary32 rounding; bounded pixel-
  edge mapping; refusal of unsupported geometry and outside-crop selections;
  forward overlays without changing stored source coordinates.
- `ocr_crop_uncertainty_journal.py`: bounded, detached authoring state; exact
  hash-linked atomic revisions; stable IDs and retained dismissed annotations;
  explicit resolutions, raw Unicode spans and resets; cumulative replay limits.
  Journal calls do not invoke an OCR scorer or confer source-review consent.

The geometry suite uses independent numeric expectations and retained numeric
observations. It does not execute a native renderer or authenticate actual RGB
bytes. The journal fixtures use the real pure geometry builder and existing
generated report validators. Scorer sentinels guard journal calls; the separate
v1 parity control deliberately restores the scorer only around its legacy calls.
Exact rational midpoint analysis independently confirmed the high-exponent
ties-to-even vector; it was not changed to fit an implementation result.

Independent agent source/fixture review found no actionable defect. This was
not human adjudication, exhaustive transition coverage, maximum-size performance
qualification, parent-history authentication or a pack/UI review.

## Preserved failure and repair

The first run reported **475 passed and two errors**: setup and teardown of the
same oversized Unicode-parameter case. Pytest's automatically escaped parameter
name exceeded Windows' 32,767-character environment-variable limit before that
test body ran. The retained JUnit contains 476 test cases and both error records.
The production components did not change. Short, descriptive parameter IDs were
added; input sizes and assertions were retained exactly. The corrected full
focused run passed all 476 cases in 5.05 seconds.

Private local JUnit evidence:

| Observation | Artifact | SHA-256 |
| --- | --- | --- |
| First run, exit 1 | `tmp/ocr_crop_pure_focused_v1.xml` | `7b5196a22e4e5e86ca2636094ab9988fd18aaf74b4b76a1e17240ac4ee5afdfe` |
| Corrected focused run, exit 0 | `tmp/ocr_crop_pure_focused_v2.xml` | `4399e8176e32c2d1eb20d8727d1d8cb3071439648a75d8be3a65728ec81755a9` |
| V1 compatibility plus focused run, exit 0 | `tmp/ocr_crop_pure_compatibility_v1.xml` | `21250fbddb0f142a61b6ce4195ca52f53b221678cc3bb7bd075cbc1726c6c6db` |

The passing commands used the previously qualified local Python 3.12 interpreter,
`-I -B -m pytest -q -p no:cacheprovider`, explicit test files and
`PYTEST_DISABLE_PLUGIN_AUTOLOAD=1`. Ruff also passed for the two production
modules and both promoted test files after the ID repair. The held staging
drafts and failed-run evidence remain unchanged.

## Source identity

Original base HEAD: `e34103f70b676eacc8d55badb2468b8a10004ef4`;
tree: `af0a69ccde82b6b679580f07813495721978efe0`. These are the original base,
not a commit of the new implementation. The following SHA-256 and computed
Git blobs identify the actual tested additions; computing blobs wrote no object.

| File | SHA-256 | Computed Git blob |
| --- | --- | --- |
| `ocr_crop_raster_view.py` | `f763f95db3699fcaa71edfc6b2bc74410c4daca4a3f0f7d5d57de484149f0c64` | `6ad159b77d90d5f2f3a7ca1f02e2e8c075932832` |
| `ocr_crop_uncertainty_journal.py` | `a45a84c689944db2a17240999c2de183821a466e87faab607d997d6bd813c9eb` | `def994a3c1e2335d3ed6b9a6ce7ce8e237f67e13` |
| `tests/test_ocr_crop_raster_view.py` | `0b70b690ab175ae718ccaef02612e53c6837d20e8b3f8e2daaf4da755f22eeba` | `2123613f25b7f325d1571b2643df781ec66d2881` |
| `tests/test_ocr_crop_uncertainty_journal.py` | `0b5a9c36040fcd506902c5653d4215c89da549a9bcd8a8acc9f38330fbbf30a8` | `7fc1171165334c366a2149766ea851d148085128` |

Original index SHA-256 remains
`6a841daa2ac15f397750fc0f88f1bd6e93b78326823d2452e3ab14c88d0335f3`;
original architecture baseline remains
`8c9d9991aa6864131915b6f0392ae4bf4c3f9a4af5e8ae0757e78e9d47aa792e`.
No completed qualification copy or retained evidence was repurposed for these
new sources. The earlier [12,127-test checkpoint](2026-09-08-ocr-preview-image-ownership-qualification.md)
does not cover them.

## Remaining integration

This record does not establish the later v2 reference/comparison adapter,
same-render native metadata or its worker transport. Those are being integrated
separately. Parent-pack continuity, versioned save/replay/recovery, stale-action
and failed-image delivery safeguards, pointer/keyboard annotation UI and the
generated uncertainty-to-resolution journey remain required.

The full [67-requirement program](../ocr-improvement-program.md) remains in scope.
No private corpus, new model, canonical correction adoption, representative
accuracy gain, independent human adjudication or release is authorized or proved.
