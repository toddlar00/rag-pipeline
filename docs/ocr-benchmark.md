# OCR cohorts, structural metrics, and runtime evidence

These opt-in tools complement CER/WER; they do not change canonical extraction,
indexes, OCR models, or existing recovery/comparison schemas. Inputs must be
approved for local annotation retention. Do not commit private page content or
annotations merely because a tool accepts them.

## Representative evaluation and held-out families

`ocr_benchmark.validate_cohort` accepts one exact v1 object:

```json
{
  "schema_version": 1,
  "cohort_id": "approved-cohort-v1",
  "approval": "operator_approved",
  "records": [
    {
      "id": "calibration-page-1",
      "source_sha256": "1111111111111111111111111111111111111111111111111111111111111111",
      "document_family_id": "family-a",
      "page_number": 1,
      "split": "calibration",
      "reference_sha256": "aaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaa",
      "structure": {
        "region_ids": ["left", "right"],
        "reading_order": ["left", "right"],
        "table_cells": []
      }
    },
    {
      "id": "held-out-page-1",
      "source_sha256": "2222222222222222222222222222222222222222222222222222222222222222",
      "document_family_id": "family-b",
      "page_number": 1,
      "split": "held_out",
      "reference_sha256": "bbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbb",
      "structure": {
        "region_ids": ["cell-a", "cell-b"],
        "reading_order": ["cell-a", "cell-b"],
        "table_cells": [
          {"region_id": "cell-a", "table_id": "table-1", "row": 1, "column": 1, "row_span": 1, "column_span": 1},
          {"region_id": "cell-b", "table_id": "table-1", "row": 1, "column": 2, "row_span": 1, "column_span": 1}
        ]
      }
    }
  ]
}
```

The example hashes are placeholders, not approved documents. Actual digests
must identify the operator's source PDF and reviewed reference artifact. The
validator checks their syntax and declared binding; it does not open either
artifact, verify the transcription, authenticate approval, or infer a family.

Both splits must be nonempty. Record IDs and source/page pairs must be unique.
Neither a source PDF nor a document family may occur in both splits. Assign
related volumes, editions, duplicates, and scans to the same family when they
could leak layout or content information. The validator cannot detect relabeled
duplicates, previously viewed held-out pages, or unrepresentative sampling.

Choose approved pages covering the intended distribution, including clean and
degraded scans, small print, columns, tables, headings, marginalia, and relevant
languages. Establish the split before tuning; tune only on calibration families.
Freeze the cohort digest and recipes before a held-out evaluation. A page used
to debug a failure is calibration evidence thereafter, not fresh holdout proof.
The eight-page synthetic challenge is diagnostic calibration evidence, not a
representative held-out accuracy claim.

`cohort_digest(payload)` returns SHA-256 of canonical **validated** JSON: sorted
object keys, records, region-ID sets, and cell assignments; meaningful reading
order is preserved. Formatting and list order for unordered fields do not
change this digest. A reference digest, split, correspondence, or meaningful
order change does. Freeze that value before producing predictions.

## Explicit structural correspondences

Predictions use exact top fields `schema_version`, `cohort_id`,
`cohort_sha256`, and `records`. Every prediction record has exactly `id` and
`structure`; its structure has the same three fields shown above. Every cohort
record must appear exactly once, even when recognition is poor. Unknown,
duplicate, missing, or stale-bound records are errors; no silent intersection
is scored.

Region IDs are reviewed correspondences between reference and prediction,
not matches inferred from OCR text, confidence, or bounding boxes. A genuinely
empty prediction is represented by three empty lists and scores omissions.
Do not substitute that for an OCR failure or a page never evaluated: obtain a
reviewed prediction before publishing this full-cohort evaluation.

Each reading order must contain its own region IDs exactly once. Predicted
regions may be missing or extra relative to the reference. Each table region
may have one assignment, with positive one-based row/column indices and spans.
Cells in the same table may touch but must not overlap. Identical coordinates
in different tables are allowed. These are explicit cell assignments, not a
table detector, a text-cell metric, or a geometric overlap metric.

```powershell
python tools/evaluate_ocr_structure.py --cohort approved-cohort.json --predictions reviewed-structures.json --output evaluation-reports/structure-new.json
```

The report contains record IDs, counts, and rates, not region IDs, family IDs,
cell annotations, reference text, or file paths. It keeps calibration and
held-out micro-aggregated summaries separate. No automatic acceptance follows.

| Metric | Exact meaning |
| --- | --- |
| Region recall | Shared reviewed region IDs / reference region IDs; missing and extra region counts remain explicit. |
| Order inversion rate | Reversed pairs / all unordered pairs of **shared** regions, using their annotated reading orders. |
| Order pair coverage | Shared-region pairs / reference-region pairs; prevents zero inversions on a tiny subset from looking complete. |
| Table-cell accuracy | Exactly correct `(table_id, row, column, row_span, column_span)` assignments / reference assignments. Missing, wrong, and extra assignments are separate counts. |

A zero denominator produces JSON `null`, not a perfect score. Counts are
micro-aggregated before division, not averaged across pages. Any omission,
extra region/assignment, wrong assignment, or inversion requires attention.
A split with no annotated regions also requires attention. Correct order does
not imply correct text; use the existing CER/WER and contextual critical-phrase
evaluation separately. In particular, repeated standalone critical tokens do
not establish which entity, cell, or proposition they belong to.

Bounds: 256 records, 2,000 regions per record, 20,000 regions per entire input,
128-character safe ASCII identifiers, pages 1–5,000, table axes/spans up to
10,000 without expanded-grid allocation, and 16 MiB per JSON input. Inversions
use an `O(n log n)` algorithm. Table overlap validation is bounded by the region
limits. Exact schemas reject unknown fields and invalid/nonfinite values.

## Runtime observations versus verified artifacts

```powershell
python tools/inspect_ocr_runtime.py --lock requirements-core.lock --output evaluation-reports/runtime-new.json
python tools/inspect_ocr_runtime.py --lock requirements-core.lock --package rapidocr --package onnxruntime --require-version-match --output evaluation-reports/runtime-gated-new.json
```

The default package set is PyMuPDF, OpenCV, NumPy, RapidOCR, and ONNX Runtime.
`--package` replaces that set and can be repeated. The tool snapshots the exact
lock bytes and records Python, platform, installed package metadata, and version
attributes of relevant modules **already loaded in the current process**. It
does not import an OCR engine just to inspect it. A standalone inspector cannot
observe modules loaded in another OCR process; call `capture_runtime_manifest`
inside the executing adapter for that observation.

Generated-lock markers are resolved for the current interpreter/environment.
The supported bounded subset is comparisons of known Python/platform variables
with quoted strings, `and`, `or`, parentheses, and Python release-version
comparisons including `==`/`!=` release wildcards. Unsupported markers, duplicate
active pins, missing packages, metadata failures, and loaded-version mismatch
never pass the version gate. OpenCV's known distribution build suffix is
distinguished from the three-component `cv2.__version__` value.

`--require-version-match` refuses publication when a **requested package** does
not match. It is not a complete-environment, wheel-hash, native-library, model,
provider, or execution verification gate. The `package_versions_match` field
describes only that version check. Matching installed metadata can be forged;
even matching imported version strings do not attest executable bytes.
Consequently, `locked_environment_verified` is always `false` in this capture,
and artifact-verification limitations remain explicit. Do not change it to true
on the basis of install metadata or these version checks.

The optional `--effective-runtime observed.json` input records evidence obtained
by an executing adapter. It must have exactly this shape:

```json
{
  "engine": {"name": "rapidocr", "version": "3.9.2"},
  "execution_providers": ["CPUExecutionProvider"],
  "thread_settings": {"intra_op": 2, "inter_op": 1},
  "elapsed_seconds": 1.25,
  "model_artifacts": [{"id": "recognizer", "sha256": "aaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaa"}]
}
```

This is an illustrative shape, not evidence that this execution occurred.
Supply actual session providers, not merely available providers; supply observed
duration and the model digests checked by the executing adapter. Unknown thread
settings can be `null`; no observations are invented when the file is omitted.
Names and versions are bounded safe strings; settings, durations, list sizes,
and digests are strictly bounded. Observations are explicitly labeled
`caller_observation`, not authenticated receipts, and cannot upgrade the
capture's artifact-verification claim.

Full locked-runtime validation still requires executing the same frozen
experiment in the project's approved hash-locked environment, byte-verifying
approved model artifacts before loading, and retaining the installation,
effective execution, input, recipe, and result evidence together. These tools
surface prerequisites and mismatches; they neither install packages nor download
models. A mismatched local run remains diagnostic evidence.

## Private publication and exit status

Both tools accept only distinct, single-linked regular input files. They reject
link components and aliases, read bounded strict snapshots, recheck every input
digest inside the publication callback, and create a new private report under a
path lease. Existing outputs are never overwritten, including a competing
writer's output. Reports add `inputs` with exact input-file SHA-256 values; the
structural `cohort_sha256` remains the separate canonical annotation binding.
No input path or content is printed by the CLI.

Exit `0` means a structural report has no measured discrepancy; `3` means a
report was written but needs attention; `2` means invalid input, a failed gate,
or an I/O/cleanup failure; `130` means cancellation. Runtime inspection currently
returns `3` even when all requested versions match, because artifact identity
remains unverified. A late cancellation or cleanup error may follow publication;
the static notice tells the operator to inspect the chosen output before retrying.
No report is an authorization to overwrite canonical text or promote a model.
