"""Synthetic correspondences exercise missing-data and holdout failure modes."""

import copy
import itertools
import json

import pytest

import ocr_benchmark as benchmark


def structure(order=("a", "b", "c"), cells=()):
    return {"region_ids": list(order), "reading_order": list(order), "table_cells": list(cells)}


def cell(region="a", table="t", row=1, column=1, row_span=1, column_span=1):
    return {"region_id": region, "table_id": table, "row": row, "column": column,
            "row_span": row_span, "column_span": column_span}


def cohort():
    return {"schema_version": 1, "cohort_id": "synthetic-test", "approval": "operator_approved",
            "records": [{"id": "page-" + str(index), "source_sha256": str(index) * 64,
                         "document_family_id": "family-" + str(index), "page_number": 1,
                         "split": split, "reference_sha256": "a" * 64, "structure": structure()}
                        for index, split in enumerate(("calibration", "held_out"), 1)]}


def predictions(value):
    return {"schema_version": 1, "cohort_id": value["cohort_id"],
            "cohort_sha256": benchmark.cohort_digest(value),
            "records": [{"id": item["id"], "structure": copy.deepcopy(item["structure"])}
                        for item in value["records"]]}


def test_perfect_full_cohort_is_separate_and_detached():
    value = cohort()
    actual = predictions(value)
    saved = copy.deepcopy((value, actual))
    result = benchmark.evaluate_structure(value, actual)
    assert (value, actual) == saved
    assert not result["requires_attention"]
    assert result["summary"]["held_out"]["order_inversion_rate"] == 0
    assert result["summary"]["held_out"]["order_pair_coverage"] == 1
    assert result["summary"]["calibration"]["records"] == 1
    validated = benchmark.validate_cohort(value)
    validated["records"][0]["structure"]["reading_order"].reverse()
    assert value == saved[0]


def test_order_inversions_exact_all_small_permutations():
    for size in range(7):
        for permutation in itertools.permutations(range(size)):
            expected = sum(a > b for index, a in enumerate(permutation) for b in permutation[index + 1:])
            assert benchmark._inversions(list(permutation)) == expected


def test_reverse_order_is_not_hidden_by_perfect_region_recall():
    value = cohort()
    actual = predictions(value)
    actual["records"][1]["structure"]["reading_order"].reverse()
    result = benchmark.evaluate_structure(value, actual)
    assert result["requires_attention"]
    assert result["summary"]["calibration"]["order_inversions"] == 0
    assert result["summary"]["held_out"]["order_inversions"] == 3
    assert result["summary"]["held_out"]["order_inversion_rate"] == 1
    assert result["summary"]["held_out"]["region_recall"] == 1


@pytest.mark.parametrize("order,missing,extra,pairs", [([], 3, 0, 0), (["a"], 2, 0, 0),
                                                       (["a", "c"], 1, 0, 1), (["x"], 3, 1, 0)])
def test_missing_or_extra_regions_are_not_perfect_order(order, missing, extra, pairs):
    value = cohort()
    actual = predictions(value)
    actual["records"][1]["structure"] = structure(order)
    result = benchmark.evaluate_structure(value, actual)
    score = result["summary"]["held_out"]
    assert score["missing_regions"] == missing
    assert score["extra_regions"] == extra
    assert score["shared_order_pairs"] == pairs
    assert score["order_inversion_rate"] == (0 if pairs else None)
    assert score["order_pair_coverage"] == pairs / 3
    assert result["requires_attention"]


def test_empty_annotation_cohort_does_not_claim_a_successful_evaluation():
    value = cohort()
    for item in value["records"]:
        item["structure"] = structure(())
    result = benchmark.evaluate_structure(value, predictions(value))
    assert result["requires_attention"]
    assert result["summary"]["held_out"]["region_recall"] is None
    assert result["summary"]["held_out"]["order_inversion_rate"] is None
    assert result["summary"]["held_out"]["table_cell_accuracy"] is None


def test_table_cell_assignment_includes_missing_extra_and_wrong():
    value = cohort()
    value["records"][1]["structure"] = structure(("a", "b", "c", "d"),
                                                [cell("a"), cell("b", column=2), cell("c", row=2)])
    actual = predictions(value)
    actual["records"][1]["structure"]["table_cells"] = [cell("a"), cell("b", column=3), cell("d", row=3)]
    result = benchmark.evaluate_structure(value, actual)
    score = result["summary"]["held_out"]
    assert score["correct_table_cells"] == score["wrong_table_cells"] == 1
    assert score["missing_table_cells"] == score["extra_table_cells"] == 1
    assert score["table_cell_accuracy"] == 1 / 3
    assert score["order_inversion_rate"] == 0
    assert result["requires_attention"]


@pytest.mark.parametrize("field,value", [("table_id", "other"), ("row", 2), ("column", 2),
                                         ("row_span", 2), ("column_span", 2)])
def test_every_cell_assignment_dimension_matters(field, value):
    source = cohort()
    source["records"][1]["structure"]["table_cells"] = [cell()]
    actual = predictions(source)
    actual["records"][1]["structure"]["table_cells"][0][field] = value
    score = benchmark.evaluate_structure(source, actual)["summary"]["held_out"]
    assert score["wrong_table_cells"] == 1
    assert score["table_cell_accuracy"] == 0


@pytest.mark.parametrize("field", ["document_family_id", "source_sha256"])
def test_cross_split_family_or_source_leakage_is_rejected(field):
    value = cohort()
    value["records"][1][field] = value["records"][0][field]
    value["records"][1]["page_number"] = 2
    with pytest.raises(ValueError, match="isolated"):
        benchmark.validate_cohort(value)


def test_same_family_can_have_multiple_calibration_documents():
    value = cohort()
    extra = copy.deepcopy(value["records"][0])
    extra.update(id="extra", source_sha256="3" * 64)
    value["records"].append(extra)
    assert len(benchmark.validate_cohort(value)["records"]) == 3


@pytest.mark.parametrize("mutate", [
    lambda v: v.update(extra="PRIVATE-TEXT"),
    lambda v: v.update(schema_version=True),
    lambda v: v.update(approval="pending"),
    lambda v: v.update(cohort_id="private/path"),
    lambda v: v.update(records=[]),
    lambda v: v["records"].pop(),
    lambda v: v["records"].append(copy.deepcopy(v["records"][0])),
    lambda v: v["records"][0].update(extra=True),
    lambda v: v["records"][0].update(page_number=True),
    lambda v: v["records"][0].update(page_number=0),
    lambda v: v["records"][0].update(page_number=5001),
    lambda v: v["records"][0].update(source_sha256="A" * 64),
    lambda v: v["records"][0].update(reference_sha256="short"),
    lambda v: v["records"][0].update(split=[]),
], ids=["unknown-top", "bool-version", "approval", "unsafe-id", "empty", "one-split", "duplicate",
        "unknown-record", "bool-page", "zero-page", "large-page", "upper-digest", "short-digest", "bad-split"])
def test_strict_cohort_rejects_invalid_inputs_without_echo(mutate):
    value = cohort()
    mutate(value)
    with pytest.raises(ValueError) as error:
        benchmark.validate_cohort(value)
    assert "PRIVATE-TEXT" not in str(error.value)


def test_duplicate_source_page_even_with_new_id_is_rejected():
    value = cohort()
    extra = copy.deepcopy(value["records"][0])
    extra["id"] = "different"
    value["records"].append(extra)
    with pytest.raises(ValueError, match="unique"):
        benchmark.validate_cohort(value)


@pytest.mark.parametrize("mutate", [
    lambda v: v.update(extra=True),
    lambda v: v.update(schema_version=True),
    lambda v: v.update(cohort_id="other"),
    lambda v: v.update(cohort_sha256="0" * 64),
    lambda v: v["records"].pop(),
    lambda v: v["records"][0].update(id="unknown"),
    lambda v: v["records"].append(copy.deepcopy(v["records"][0])),
    lambda v: v["records"][0].update(extra="sensitive"),
], ids=["unknown-top", "bool-version", "id-binding", "digest-binding", "missing-record", "extra-record", "duplicate", "unknown-record"])
def test_predictions_never_silently_intersect_or_accept_stale_binding(mutate):
    value = cohort()
    actual = predictions(value)
    mutate(actual)
    with pytest.raises(ValueError):
        benchmark.evaluate_structure(value, actual)


def test_annotation_change_invalidates_prediction_binding():
    value = cohort()
    actual = predictions(value)
    value["records"][0]["structure"]["reading_order"].reverse()
    with pytest.raises(ValueError, match="binding"):
        benchmark.evaluate_structure(value, actual)


def test_digest_and_results_are_order_stable():
    value = cohort()
    actual = predictions(value)
    expected = benchmark.evaluate_structure(value, actual)
    value["records"].reverse()
    actual["records"].reverse()
    for record in value["records"]:
        record["structure"]["region_ids"].reverse()
    assert benchmark.evaluate_structure(value, actual) == expected


@pytest.mark.parametrize("mutate", [
    lambda v: v.update(extra=0),
    lambda v: v.update(region_ids=["a", "a", "c"]),
    lambda v: v.update(reading_order=["a", "a", "c"]),
    lambda v: v.update(reading_order=["a", "c"]),
    lambda v: v.update(reading_order=["a", "b", "x"]),
    lambda v: v.update(table_cells=[cell("x")]),
    lambda v: v.update(table_cells=[cell(), cell()]),
    lambda v: v.update(table_cells=[cell("a"), cell("b")]),
    lambda v: v.update(table_cells=[cell("a", column_span=2), cell("b", column=2)]),
    lambda v: v.update(table_cells=[cell(row=10000, row_span=2)]),
    lambda v: v.update(table_cells=[cell(column=10000, column_span=2)]),
], ids=["unknown", "duplicate-regions", "duplicate-order", "incomplete-order", "different-order",
        "unknown-cell", "duplicate-cell", "overlap", "span-overlap", "row-overflow", "column-overflow"])
def test_structure_is_strict_and_unambiguous(mutate):
    value = structure()
    mutate(value)
    with pytest.raises(ValueError):
        benchmark.validate_structure(value)


@pytest.mark.parametrize("field", ["row", "column", "row_span", "column_span"])
@pytest.mark.parametrize("number", [True, False, 0, -1, 10001, 1.0, float("nan"), float("inf"), None])
def test_cell_numeric_bounds(field, number):
    value = cell()
    value[field] = number
    with pytest.raises(ValueError):
        benchmark.validate_structure(structure(cells=[value]))


def test_separate_tables_and_touching_spans_are_valid():
    value = structure(cells=[cell("a", row_span=2), cell("b", row=3), cell("c", table="other")])
    assert len(benchmark.validate_structure(value)["table_cells"]) == 3


def test_region_and_total_limits(monkeypatch):
    with pytest.raises(ValueError, match="sizes"):
        benchmark.validate_structure(structure(["r" + str(index) for index in range(2001)]))
    monkeypatch.setattr(benchmark, "MAX_TOTAL_REGIONS", 5)
    with pytest.raises(ValueError, match="budget"):
        benchmark.validate_cohort(cohort())


def test_prediction_budget_cannot_expand_beyond_cohort(monkeypatch):
    value = cohort()
    actual = predictions(value)
    actual["records"][0]["structure"] = structure(("a", "b", "c", "x"))
    monkeypatch.setattr(benchmark, "MAX_TOTAL_REGIONS", 6)
    with pytest.raises(ValueError, match="budget"):
        benchmark.evaluate_structure(value, actual)


def test_maximum_reverse_order_is_bounded_and_exact():
    maximum = benchmark.MAX_REGIONS
    assert benchmark._inversions(list(reversed(range(maximum)))) == maximum * (maximum - 1) // 2


def test_report_contains_only_record_ids_and_metrics_not_annotations():
    value = cohort()
    value["records"][0]["structure"] = structure(("sensitive-region-id",))
    result = json.dumps(benchmark.evaluate_structure(value, predictions(value)))
    for excluded in ("sensitive-region-id", "family-1", "reading_order", "reference_sha256", "operator_approved"):
        assert excluded not in result
