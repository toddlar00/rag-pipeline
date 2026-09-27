# Generated crop-edge paired experiment

2026-09-09. OCR, deterministic scoring and independent retained-artifact reviews
are complete for these five generated cases.

OCR on the proposed padded crops recovered the expected text in two generated
single-text crops. Two neighboring-text controls demonstrate a separate limit:
a transcription can match the wider crop while still containing text beyond the
fixed task target. The nontext rule control returned no text in either arm.

## Fixed design and reference coverage

Five image-only PDF pages contain generated Arial text or a nontext rule.
Each original crop was profiled by the actual production advice implementation
at 288 DPI. Its actual proposal, bounded to two source points per side, was
rendered separately with the original crop at 300 DPI. All ten native crop
images were visually reviewed before OCR. No fallback proposal was substituted.

References were source-authored and fixed before inference, with an explicit
assistant visual admission of each native crop. They are not independent human
transcriptions or reviewed crop-reference/journal approvals. The source raster's
glyph-support cells are geometric evidence, not exact final PDF interpolated
ink support. Seven scope references are scorable; three original crops retain
null references because they cut source glyph support. An empty reference for
the nontext control is distinct from a null, unscorable reference.

| Case | Original scope reference | Proposed scope reference | Fixed task target |
| --- | --- | --- | --- |
| Complete tight text | `NOT 12` | `NOT 12` | `NOT 12` |
| Cut right glyph | Unscorable partial | `NOT 12` | `NOT 12` |
| Duplicate to right | Unscorable partial neighbor | `NOT 12 NOT 12` | `NOT 12` |
| Duplicate below | Unscorable partial neighbor | `NOT 12 NOT 12` | `NOT 12` |
| Nontext rule | Empty | Empty | Empty |

In the duplicate cases, most neighboring text already lies inside the original
crop; its final one point of glyph support is cut. Padding does not newly
introduce an entire neighboring occurrence. Identical strings have distinct
source occurrence IDs. String counts alone cannot locate those occurrences.

## Actual OCR observations

The reviewed helper ran once in the qualified local Python 3.12 environment,
using RapidOCR 3.9.2 and the existing verified model loader. The fixed recipe was
300 DPI, preprocessing `none`, minimum score 0, maximum side 6,000 and maximum
pixels 25 million. Each arm received one attempt. A contained worker had a
600-second deadline, with retained stdout/stderr and per-arm results.

Root launch `0c3a5a`, session 81400, ended exit 0 in `bdeadc`. The parent receipt
reports 24.890 seconds, all ten arm artifacts readable, no missing result arms,
and owned process cleanup confirmed by the existing supervisor. The worker
reports 20.813 seconds. These clocks measure different scopes and establish no
performance improvement. No inference was repeated for scoring.

| Case | Original raw output | Proposed raw output | Detector boxes, original → proposed |
| --- | --- | --- | ---: |
| Complete tight text | Empty | `NOT\n12` | 0 → 2 |
| Cut right glyph | Empty | `NOT\n12` | 0 → 2 |
| Duplicate to right | `NOT 1\n12 NOT 1\n12` | `NOT\n12 NOT\n12` | 3 → 3 |
| Duplicate below | `NOT 12\nNOT 12` | `NOT 12\nNOT 12` | 2 → 2 |
| Nontext rule | Empty | Empty | 0 → 0 |

Literal `\n` denotes a retained newline. No output was manufactured for failed
or unavailable work: all ten calls actually completed. Every observed raw BGR
digest matched its independently staged native crop, and every candidate's
geometry matched the preflight. Actual role observations distinguish detector
execution from recognition: the four zero-box arms never ran classification or
recognition. Constructed sessions alone would not establish that distinction.

The fixed recipe does not keep internal engine geometry constant. For example,
the original horizontal-duplicate crop is 357 × 39 pixels and the engine pads
it to 357 × 87. Its proposed crop is 374 × 56, with no such padding. The retained
preflight predicts detector dimensions of 3,008 × 736 and 4,928 × 736 respectively;
these are not independent observations of native tensor bytes. Scope,
margin and internal scaling therefore change together; this experiment cannot
isolate restored glyph support as the cause of a change in OCR.

## Scoring and spatial interpretation

The separately reviewed scoring helper completed once (`f341e5`, exit 0), using
the existing `evaluate_ocr` and `compare_ocr` APIs. All ten full raw candidates,
their references and scoring inputs were saved before the first metric call.
No inference, trimming, deduplication or new critical-token policy was used.
Normalization is unchanged NFC plus whitespace collapse, preserving case and
punctuation. It discards line-break differences, so exact normalized text is not
a layout or reading-order verdict.

All seven admitted scope references were evaluated. Six match exactly after
normalization, including all five proposed crops and the original nontext crop.
The original complete-tight crop has six character deletions and two word
deletions (CER and WER both 1). The three partial original references remain
explicitly unscorable; no empty reference was substituted for them. The two
empty-reference scope evaluations have null CER/WER and zero insertions.

All five fixed-target pairs were also compared. These distances measure
end-to-end recovery and neighboring-text contamination against the fixed task
target, including source support unavailable to some original crops. They are
not per-scope recognition-error comparisons or same-scope crop-review tickets.

| Fixed task target case | Character edits, original → proposed | Word edits, original → proposed | Count-based outcome |
| --- | ---: | ---: | --- |
| Complete tight text | 6 → 0 | 2 → 0 | Improved |
| Cut right glyph | 6 → 0 | 2 → 0 | Improved |
| Duplicate to right | 11 → 7 | 4 → 2 | Improved, still extra text |
| Duplicate below | 7 → 7 | 2 → 2 | Unchanged, still extra text |
| Nontext rule | 0 → 0 | 0 → 0 | Unchanged, empty |

The duplicate cases' remaining seven character edits and two word edits are
insertions relative to one `NOT 12` target. The API reports three improvements
and two unchanged count outcomes, with no worsening in those counts. That is
not a crop-safety approval: both proposed duplicate crops still exceed the task
target despite matching their own wider-scope references. Nontext rate deltas
remain null. Empty critical-token arrays/counts are not a measured critical
result, and no benchmark family split or automatic adoption is inferred.

The horizontal duplicate's middle detection spans portions of both source
occurrences. The vertical duplicate produces two spatially separated line
boxes. The [independent geometry review](../../tmp/ocr_crop_edge_pair_occurrence_geometry_review_v1.md)
supports upper-target/lower-neighbor line association for the vertical cases,
while retaining mixed and overlapping horizontal boxes. Neither repeated
strings nor confidence scores establish token-level source correspondence.

## Evidence and limits

| Retained artifact | SHA-256 |
| --- | --- |
| Generated PDF | `154a702a190eee9ebbd1dd140a3490e1bdd1c2a462ccff1da2b3f2b5f828d680` |
| Native receipt | `cb22fb8fe67e7341b3e506a2eef375170a6fa98dfeee953dc6c0409dec6b8ab7` |
| Independent native audit | `948be6cfe3b86e9e9c91329020f19a9175425f30b50e8042c2c4b95c84fbe097` |
| Pre-OCR admission | `b7ee3c50fcc3da36ba0e30f7eb01a3fed1e738d9c95797449cc1ab77ac7f5ebf` |
| OCR helper | `2733ed461f01a15e6b7e68ead3c34f49cb706fe8a43fc9c945a570b4135980f5` |
| OCR parent receipt | `bc51f51ab99b87fc9730384d1edbf7f2d2201a29300fae403007faf3f5b9c655` |
| OCR predictions index | `f182ce0ebfb09f7233516f908c2b56e5379677662f83c301af5b63c0e947bd5d` |
| OCR execution receipt | `fa9bee5edf2c596d35ec6cf6f2f08871ab3a8ba60aa66ff5285fe0b2258e0ba8` |
| Independent OCR audit | `f533fcd391d5a50768a39a808af5f5c2e32428c202c05ac44f6f019b859bc976` |
| Frozen scoring inputs | `247f3cd705e0983fc21fd27223d7e4eeaa895cf8a6a20686971ccdd15089e9b8` |
| Scores | `79616ffe21f615cbcc22064bcca4d0b6e0a83531a9a5398b8ba110bd84b2308b` |
| Scoring receipt | `dd637c8413ca6a6c3b0d3efc81fa1e0d215271d81ba021d6ea2cdb889e8f80fb` |
| Independent scoring audit | `b0b29821e3c5b8c55f4cd26363d8d8c5e34cf6a77388fa70894ea2360a86bdff` |

The [independent retained OCR audit](../../tmp/ocr_crop_edge_pair_ocr_audit_v1.md)
passed (`08465e`, exit 0): 492 fixed inputs including 436 Python sources,
20 OCR artifacts, ten native BGR/dispatch joins and three installed model files
matched. It did not replay OCR, rendering or scoring. A separate root lifecycle
readback found both recorded PIDs absent and the retained temporary directory's
identity unchanged, without reading its contents. These are point-in-time
observations alongside the supervisor's recorded cleanup contract.

The [independent retained scoring audit](../../tmp/ocr_crop_edge_pair_scoring_audit_v1.md)
passed (`936503`, exit 0), matching all 15 score files, 476 inputs including
436 Python sources, seven scored/three unscorable scopes and five pairs.
Direct manual edit counts agree with the saved scores; the auditor did not
call the evaluation/comparison APIs or rerun inference. Root read and rehashed
both the audit and its concise note (`004dfd`).

Raw source/authoring artifacts are under `output/pdf/ocr-crop-edge-pair-v1/`;
native artifacts under `output/pdf/ocr-crop-edge-pair-native-v1/`; OCR under
`output/pdf/ocr-crop-edge-pair-ocr-v1/`; and all 15 scoring artifacts under
`output/pdf/ocr-crop-edge-pair-scores-v1/`. Pins above bind the complete indices.

Source, native pixels, reference admission, geometry, dispatch tickets, calls,
models and output records are separately bound. The execution receipt explicitly
has no installation evidence supplied and does not attest all loaded package or
native-library bytes. Before/after model observations agree; selected source
files and package versions are checked separately. The deadline limits worker
lifetime plus termination grace, not decoder memory. Logs have a polled size
limit. Temporary contents remain retained without inspection or deletion.

The generated source authoring, Poppler preflight failures and corrected native
audit failure remain retained. No failed evidence was replaced by an inference
retry. Production Python source remained fixed for the experiment. This follows
the 14,154-test, nine-gate copied-source qualification and is a separate generated
experiment, not a rerun or extension of that frozen source snapshot.

Representative accuracy, useful warning thresholds, region/token localization
error rates, rescan gains, repeatability, human adjudication and general review
usability remain unestablished. The broader 67-requirement OCR/AI program remains
unfinished. No source replacement, journal approval, release, new model
acquisition or expansion of AI access occurred.
