"""Approved, family-isolated OCR cohorts and explicit structural scoring.

Region IDs are operator-reviewed correspondences, not inferred matches. Order
inversions are conditional on shared regions and always accompanied by coverage.
No text, geometry inference, authorization attestation, or automatic promotion
is provided. A declared held-out split does not prove it was never inspected.
"""

from __future__ import annotations

import hashlib
import json
import re

from evaluation_inputs import _hex_digest


MAX_RECORDS = 256
MAX_REGIONS = 2000
MAX_TOTAL_REGIONS = 20_000
MAX_TABLE_AXIS = 10_000
_ID = re.compile(r"[A-Za-z0-9][A-Za-z0-9._:-]{0,127}")
_SPLITS = ("calibration", "held_out")
_CELL_FIELDS = {"region_id", "table_id", "row", "column", "row_span", "column_span"}


def _object(value: object, keys: set[str], label: str) -> dict:
    if not isinstance(value, dict) or set(value) != keys:
        raise ValueError(f"{label} has invalid fields")
    return value


def _identifier(value: object) -> str:
    if not isinstance(value, str) or _ID.fullmatch(value) is None:
        raise ValueError("structural identifier must be bounded safe ASCII")
    return value


def _integer(value: object, maximum: int) -> int:
    if type(value) is not int or not 1 <= value <= maximum:
        raise ValueError("structural integer is outside its permitted range")
    return value


def _records(value: object) -> list:
    if not isinstance(value, list) or not 1 <= len(value) <= MAX_RECORDS:
        raise ValueError("structural record count is outside its permitted range")
    return value


def validate_structure(payload: object) -> dict:
    """Detach a bounded, complete order and nonoverlapping cell annotation."""
    value = _object(payload, {"region_ids", "reading_order", "table_cells"}, "structure")
    ids, order, cells = (value[key] for key in ("region_ids", "reading_order", "table_cells"))
    if (not isinstance(ids, list) or len(ids) > MAX_REGIONS
            or not isinstance(order, list) or len(order) != len(ids)
            or not isinstance(cells, list) or len(cells) > len(ids)):
        raise ValueError("structure lists have invalid sizes")
    ids = [_identifier(item) for item in ids]
    order = [_identifier(item) for item in order]
    if len(set(ids)) != len(ids) or len(set(order)) != len(order) or set(ids) != set(order):
        raise ValueError("reading_order must be an exact region permutation")
    known, assigned, rectangles, result = set(ids), set(), {}, []
    for raw in cells:
        cell = dict(_object(raw, _CELL_FIELDS, "table cell"))
        region = _identifier(cell["region_id"])
        table = _identifier(cell["table_id"])
        if region not in known or region in assigned:
            raise ValueError("table cells require unique known region assignments")
        assigned.add(region)
        row, col, rows, cols = (
            _integer(cell[key], MAX_TABLE_AXIS)
            for key in ("row", "column", "row_span", "column_span"))
        bottom, right = row + rows, col + cols
        if bottom > MAX_TABLE_AXIS + 1 or right > MAX_TABLE_AXIS + 1:
            raise ValueError("table cell span exceeds axis bounds")
        prior = rectangles.setdefault(table, [])
        if any(row < b and a < bottom and col < d and c < right for a, b, c, d in prior):
            raise ValueError("table cells overlap")
        prior.append((row, bottom, col, right))
        result.append(cell)
    return {"region_ids": sorted(ids), "reading_order": order,
            "table_cells": sorted(result, key=lambda cell: cell["region_id"])}


def validate_cohort(payload: object) -> dict:
    """Validate declared approval and isolation; neither is authenticated here."""
    value = _object(payload, {"schema_version", "cohort_id", "approval", "records"}, "cohort")
    if type(value["schema_version"]) is not int or value["schema_version"] != 1:
        raise ValueError("cohort schema_version must be 1")
    if value["approval"] != "operator_approved":
        raise ValueError("cohort requires explicit operator approval declaration")
    identifier = _identifier(value["cohort_id"])
    results, ids, pages, family_splits, source_splits = [], set(), set(), {}, {}
    total = 0
    for raw in _records(value["records"]):
        record = dict(_object(raw, {"id", "source_sha256", "document_family_id", "page_number",
                                   "split", "reference_sha256", "structure"}, "cohort record"))
        record_id = _identifier(record["id"])
        source = _hex_digest(record["source_sha256"], label="cohort source digest")
        _hex_digest(record["reference_sha256"], label="cohort reference digest")
        family = _identifier(record["document_family_id"])
        page = _integer(record["page_number"], 5000)
        split = record["split"]
        if not isinstance(split, str) or split not in _SPLITS:
            raise ValueError("cohort split must be calibration or held_out")
        if record_id in ids or (source, page) in pages:
            raise ValueError("cohort records and source-page pairs must be unique")
        ids.add(record_id)
        pages.add((source, page))
        for key, mapping in ((family, family_splits), (source, source_splits)):
            if key in mapping and mapping[key] != split:
                raise ValueError("document family and source must be isolated across splits")
            mapping[key] = split
        record["structure"] = validate_structure(record["structure"])
        total += len(record["structure"]["region_ids"])
        if total > MAX_TOTAL_REGIONS:
            raise ValueError("cohort exceeds total region budget")
        results.append(record)
    if {item["split"] for item in results} != set(_SPLITS):
        raise ValueError("cohort requires nonempty calibration and held_out splits")
    return {"schema_version": 1, "cohort_id": identifier, "approval": "operator_approved",
            "records": sorted(results, key=lambda item: item["id"])}


def cohort_digest(payload: object) -> str:
    """Digest canonical validated annotations, independent of JSON formatting."""
    raw = json.dumps(validate_cohort(payload), sort_keys=True, separators=(",", ":"),
                     ensure_ascii=True, allow_nan=False).encode("ascii")
    return hashlib.sha256(raw).hexdigest()


def _predictions(payload: object, cohort: dict) -> dict[str, dict]:
    value = _object(payload, {"schema_version", "cohort_id", "cohort_sha256", "records"}, "predictions")
    if type(value["schema_version"]) is not int or value["schema_version"] != 1:
        raise ValueError("prediction schema_version must be 1")
    digest = _hex_digest(value["cohort_sha256"], label="prediction cohort digest")
    if value["cohort_id"] != cohort["cohort_id"] or digest != cohort_digest(cohort):
        raise ValueError("prediction cohort binding does not match")
    results, total = {}, 0
    for raw in _records(value["records"]):
        record = _object(raw, {"id", "structure"}, "prediction record")
        identifier = _identifier(record["id"])
        if identifier in results:
            raise ValueError("prediction records must have unique IDs")
        structure = validate_structure(record["structure"])
        total += len(structure["region_ids"])
        if total > MAX_TOTAL_REGIONS:
            raise ValueError("predictions exceed total region budget")
        results[identifier] = structure
    if set(results) != {item["id"] for item in cohort["records"]}:
        raise ValueError("predictions must contain every cohort record exactly once")
    return results


def _inversions(order: list[int]) -> int:
    # Fenwick tree: O(n log n), including a fully reversed maximum-size page.
    tree, inversions = [0] * (len(order) + 1), 0
    for seen, rank in enumerate(order):
        index, prior = rank + 1, 0
        while index:
            prior += tree[index]
            index -= index & -index
        inversions += seen - prior
        index = rank + 1
        while index < len(tree):
            tree[index] += 1
            index += index & -index
    return inversions


def _ratio(numerator: int, denominator: int) -> float | None:
    return numerator / denominator if denominator else None


def _rates(counts: dict) -> dict:
    return {**counts,
            "region_recall": _ratio(counts["matched_regions"], counts["reference_regions"]),
            "order_inversion_rate": _ratio(counts["order_inversions"], counts["shared_order_pairs"]),
            "order_pair_coverage": _ratio(counts["shared_order_pairs"], counts["reference_order_pairs"]),
            "table_cell_accuracy": _ratio(counts["correct_table_cells"], counts["reference_table_cells"])}


def _score(reference: dict, prediction: dict) -> dict:
    expected, actual = set(reference["region_ids"]), set(prediction["region_ids"])
    common = expected & actual
    reference_order = [item for item in reference["reading_order"] if item in common]
    ranks = {item: index for index, item in enumerate(reference_order)}
    actual_order = [ranks[item] for item in prediction["reading_order"] if item in common]
    expected_cells = {item["region_id"]: item for item in reference["table_cells"]}
    actual_cells = {item["region_id"]: item for item in prediction["table_cells"]}
    shared_cells = expected_cells.keys() & actual_cells.keys()
    correct = sum(expected_cells[key] == actual_cells[key] for key in shared_cells)
    return _rates({
        "reference_regions": len(expected), "prediction_regions": len(actual),
        "matched_regions": len(common), "missing_regions": len(expected - actual),
        "extra_regions": len(actual - expected),
        "reference_order_pairs": len(expected) * (len(expected) - 1) // 2,
        "shared_order_pairs": len(common) * (len(common) - 1) // 2,
        "order_inversions": _inversions(actual_order),
        "reference_table_cells": len(expected_cells), "prediction_table_cells": len(actual_cells),
        "correct_table_cells": correct, "wrong_table_cells": len(shared_cells) - correct,
        "missing_table_cells": len(expected_cells.keys() - actual_cells.keys()),
        "extra_table_cells": len(actual_cells.keys() - expected_cells.keys()),
    })


def evaluate_structure(cohort_payload: object, prediction_payload: object) -> dict:
    """Score the full declared cohort, with separate calibration/held-out totals."""
    cohort = validate_cohort(cohort_payload)
    predictions = _predictions(prediction_payload, cohort)
    records = [{"id": item["id"], "split": item["split"],
                "metrics": _score(item["structure"], predictions[item["id"]])}
               for item in cohort["records"]]
    summaries = {}
    count_keys = [key for key, value in records[0]["metrics"].items() if type(value) is int]
    for split in _SPLITS:
        selected = [item["metrics"] for item in records if item["split"] == split]
        summaries[split] = {"records": len(selected), **_rates({
            key: sum(item[key] for item in selected) for key in count_keys})}
    errors = ("missing_regions", "extra_regions", "order_inversions", "wrong_table_cells",
              "missing_table_cells", "extra_table_cells")
    return {"schema_version": 1, "kind": "ocr_structural_evaluation",
            "cohort_id": cohort["cohort_id"], "cohort_sha256": cohort_digest(cohort),
            "metric_profile": "explicit-region-correspondence-v1",
            "scope": "Operator-declared approval and holdout; no source authentication, geometry matching, or text accuracy claim.",
            "summary": summaries, "records": records,
            "requires_attention": (any(item["metrics"][key] for item in records for key in errors)
                                   or any(not item["reference_regions"] for item in summaries.values()))}
