"""Synthetic-only saved Docling geometry, structure and editor import contracts."""

from __future__ import annotations

import copy
import json
from pathlib import Path
import subprocess
import sys

import pytest

import ocr_docling as policy
import ocr_recovery


SOURCE = "a" * 64
DIGESTS = {"recovery_sha256": "b" * 64, "docling_sha256": "c" * 64, "manifest_sha256": "d" * 64}
PRIVATE = "SYNTHETIC_PRIVATE_TEXT"


def candidate():
    boxes = [(10, 10, 90, 15), (10, 30, 40, 35), (60, 30, 90, 35),
             (10, 40, 40, 45), (60, 40, 90, 45), (10, 75, 90, 85)]
    lines = [{"text": f"{PRIVATE} {i}", "score": .9,
              "box": [[left, top], [right, top], [right, bottom], [left, bottom]]}
             for i, (left, top, right, bottom) in enumerate(boxes)]
    return {"text": "\n".join(line["text"] for line in lines), "lines": lines, "mean_confidence": .9,
            "raster": {"width": 100, "height": 100, "dpi": 300, "coordinate_system": "rendered_image_pixels"},
            "engine": {"name": "rapidocr", "version": "synthetic", "min_score": 0.0, "max_side": 6000}}


def recovery(values=None, *, source=SOURCE, max_pages=20):
    values = [candidate()] if values is None else values

    class Reader:
        page_count = len(values)

        def native_text(self, _number):
            return ""

        def retry(self, number):
            value = values[number-1]
            if isinstance(value, BaseException):
                raise value
            return value

    result = ocr_recovery.build_recovery_report(
        Reader(), source_sha256=source, policy=ocr_recovery.RetryPolicy(max_pages=max_pages))
    result["evidence_sha256"] = None
    return result


def bbox(bounds, *, origin="TOPLEFT"):
    left, top, right, bottom = [n * .24 for n in bounds]
    if origin == "BOTTOMLEFT":
        top, bottom = 24-top, 24-bottom
    return {"l": left, "t": top, "r": right, "b": bottom, "coord_origin": origin}


def document(*, origin="TOPLEFT"):
    texts = []
    for i, (label, bounds) in enumerate([
        ("section_header", (5, 5, 95, 20)), ("text", (5, 25, 45, 50)), ("text", (55, 25, 95, 50))]):
        texts.append({"self_ref": f"#/texts/{i}", "label": label, "text": PRIVATE,
                      "children": [], "prov": [{"page_no": 1, "bbox": bbox(bounds, origin=origin)}]})
    texts[0]["children"] = [{"$ref": "#/texts/1"}, {"$ref": "#/texts/2"}, {"$ref": "#/tables/0"}]
    table = {"self_ref": "#/tables/0", "label": "table", "children": [],
             "prov": [{"page_no": 1, "bbox": bbox((5, 70, 95, 90), origin=origin)}],
             "data": {"num_rows": 1, "num_cols": 1, "table_cells": [{
                 "start_row_offset_idx": 0, "end_row_offset_idx": 1,
                 "start_col_offset_idx": 0, "end_col_offset_idx": 1,
                 "text": PRIVATE, "bbox": bbox((5, 70, 95, 90), origin=origin)}]}}
    return {"schema_name": "DoclingDocument", "version": "1.7.0", "name": PRIVATE,
            "pages": {"1": {"page_no": 1, "size": {"width": 24, "height": 24}}},
            "texts": texts, "tables": [table], "pictures": [], "groups": [],
            "body": {"self_ref": "#/body", "children": [{"$ref": "#/texts/0"}]},
            "furniture": {"self_ref": "#/furniture", "children": []}}


def build(doc=None, report=None, *, effective_input_kind="original"):
    return policy.build_docling_proposals(document() if doc is None else doc,
        recovery() if report is None else report, **DIGESTS, effective_input_kind=effective_input_kind)


@pytest.mark.parametrize("origin", ["TOPLEFT", "BOTTOMLEFT"])
def test_body_order_heading_table_and_exact_permutation_without_source_text(origin):
    doc, report = document(origin=origin), recovery()
    before = copy.deepcopy((doc, report))
    result = build(doc, report)
    page = result["pages"][0]
    assert page["status"] == "proposed"
    assert page["line_order"] == [0, 1, 3, 2, 4, 5]
    assert page["spanning_heading_refs"] == ["#/texts/0"]
    assert [r["kind"] for r in page["regions"]] == ["heading", "text", "text", "table"]
    assert page["regions"][-1]["table"]["cells"][0]["row_end"] == 1
    assert (doc, report) == before
    assert PRIVATE not in json.dumps(result)
    assert result["requires_attention"] is True
    assert result["acceptance"] == "manual_review_required"
    assert result["canonical_extraction_modified"] is False
    assert result["recognition_rerun"] is False
    assert policy.validate_docling_proposals(result, recovery=report, recovery_sha256=DIGESTS["recovery_sha256"]) == result


def test_furniture_keeps_exact_engine_slots():
    doc = document()
    doc["texts"][0]["children"].remove({"$ref": "#/tables/0"})
    doc["furniture"]["children"] = [{"$ref": "#/tables/0"}]
    value = candidate()
    value["lines"].insert(2, value["lines"].pop())
    value["text"] = "\n".join(line["text"] for line in value["lines"])
    assert build(doc, recovery([value]))["pages"][0]["line_order"] == [0, 1, 2, 4, 3, 5]


@pytest.mark.parametrize("mutate,reason", [
    (lambda d: d["pages"].clear(), "missing_page_geometry"),
    (lambda d: d["pages"]["1"]["size"].update(width=48), "page_geometry_mismatch"),
    (lambda d: d["texts"][1].update(prov=[]), "missing_provenance"),
    (lambda d: d["texts"][1]["prov"][0]["bbox"].update(l=-1), "invalid_region_geometry"),
    (lambda d: d["texts"][1]["prov"].append(copy.deepcopy(d["texts"][1]["prov"][0])), "multi_page_item"),
    (lambda d: d["texts"][0]["children"].pop(), "unattached_items"),
    (lambda d: d["tables"][0]["data"].update(table_cells=[]), "table_structure_unavailable"),
    (lambda d: d["tables"][0]["data"]["table_cells"][0].update(bbox=bbox((1, 1, 3, 3))), "table_structure_unavailable"),
    (lambda d: d["texts"][1]["prov"][0].update(bbox=bbox((5, 25, 95, 50))), "ambiguous_line_assignment"),
    (lambda d: d["texts"][1]["prov"][0].update(bbox=bbox((5, 25, 35, 50))), "unassigned_lines"),
])
def test_ambiguous_or_missing_layout_abstains(mutate, reason):
    doc = document()
    mutate(doc)
    page = build(doc)["pages"][0]
    assert page["status"] == "abstained"
    assert reason in page["reasons"]
    assert page["line_order"] is None


def test_preprocessed_effective_input_has_no_geometry_suggestions():
    page = build(effective_input_kind="preprocessed")["pages"][0]
    assert page["status"] == "abstained"
    assert page["reasons"] == ["effective_input_preprocessed"]
    assert page["regions"] == []


def test_failed_empty_deferred_cohort_is_never_perfect():
    empty = candidate()
    empty.update(text="", lines=[], mean_confidence=None)
    result = build(report=recovery([RuntimeError(), empty, candidate()], max_pages=2))
    assert [p["status"] for p in result["pages"]] == ["unavailable", "abstained"]
    assert result["coverage"] == {"selected_pages": [1, 2], "deferred_pages": [3], "not_selected_pages": []}
    assert result["summary"]["proposed"] == 0


@pytest.mark.parametrize("mutation", [
    lambda d: d.update(schema_name="other"), lambda d: d.update(version="2.0.0"),
    lambda d: d["texts"][0].update(self_ref="#/texts/999"),
    lambda d: d["texts"][0]["children"].append({"$ref": "#/texts/0"}),
    lambda d: d["body"]["children"].append({"$ref": "#/texts/999"}),
    lambda d: d["body"]["children"].append({"$ref": "#/texts/1"}),
    lambda d: d["pages"]["1"].update(page_no=True),
])
def test_malformed_structure_rejected(mutation):
    doc = document()
    mutation(doc)
    with pytest.raises(ValueError):
        build(doc)


@pytest.mark.parametrize("field,value", [("l", float("nan")), ("r", float("inf")), ("t", True),
                                         ("b", 10**1000), ("coord_origin", "CENTER")])
def test_nonfinite_enormous_or_unsupported_coordinates_abstain(field, value):
    doc = document()
    doc["texts"][1]["prov"][0]["bbox"][field] = value
    assert "invalid_region_geometry" in build(doc)["pages"][0]["reasons"]


@pytest.mark.parametrize("mutate", [
    lambda r: r.update(source_sha256="f"*64), lambda r: r.update(recovery_sha256="f"*64),
    lambda r: r.update(requires_attention=False), lambda r: r.update(schema_version=True),
    lambda r: r["pages"][0].update(line_order=[0, 1, 2, 3, 4, 5]),
    lambda r: r["pages"][0].update(line_order=[0, 1, 3, 2, 4, True]),
    lambda r: r["pages"][0]["page_geometry"].update(raster_width=101),
    lambda r: r["pages"][0]["regions"][0].update(ref="javascript:alert(1)"),
    lambda r: r["pages"][0]["regions"][0].update(text=PRIVATE),
    lambda r: r["pages"][0].update(spanning_heading_refs=[]),
    lambda r: r["pages"][0]["regions"][-1]["table"]["cells"][0].update(bbox=[0, 0, .1, .1]),
    lambda r: r["summary"].update(proposed=True),
])
def test_editor_import_rejects_forged_order_binding_geometry_and_text(mutate):
    result = build()
    mutate(result)
    with pytest.raises(ValueError):
        policy.validate_docling_proposals(result, recovery=recovery(), recovery_sha256=DIGESTS["recovery_sha256"])


def test_resource_limits_are_bounded_and_reported(monkeypatch):
    monkeypatch.setattr(policy, "MAX_REGIONS", 2)
    page = build()["pages"][0]
    assert len(page["regions"]) == 2
    assert "region_limit" in page["reasons"]


def test_import_loads_no_docling_facade_pdf_image_or_ocr_runtime():
    run = subprocess.run([sys.executable, "-c", "import sys; import ocr_docling, ocr_docling_io; "
        "assert not {'rag','docling','docling_core','pymupdf','cv2','numpy','rapidocr','onnxruntime'} & set(sys.modules)"],
        cwd=Path(__file__).resolve().parents[1], capture_output=True, text=True, timeout=30)
    assert run.returncode == 0, run.stderr


@pytest.mark.parametrize("serialization", ["save_as_json", "model_dump", "export_to_dict"])
def test_actual_docling_document_export_roundtrip(tmp_path, serialization):
    doc = pytest.importorskip("docling_core.types.doc")
    from docling_core.types.doc import DocItemLabel, ProvenanceItem, BoundingBox, Size, TableData, TableCell

    model = doc.DoclingDocument(name="synthetic")
    model.add_page(page_no=1, size=Size(width=24, height=24))
    heading = model.add_text(label=DocItemLabel.SECTION_HEADER, text=PRIVATE,
                            prov=ProvenanceItem(page_no=1, bbox=BoundingBox(**bbox((5, 5, 95, 20))), charspan=(0, len(PRIVATE))))
    for bounds in ((5, 25, 45, 50), (55, 25, 95, 50)):
        model.add_text(label=DocItemLabel.TEXT, text=PRIVATE, parent=heading,
                       prov=ProvenanceItem(page_no=1, bbox=BoundingBox(**bbox(bounds)), charspan=(0, len(PRIVATE))))
    table_box = BoundingBox(**bbox((5, 70, 95, 90)))
    model.add_table(data=TableData(num_rows=1, num_cols=1, table_cells=[TableCell(
        text=PRIVATE, bbox=table_box, start_row_offset_idx=0, end_row_offset_idx=1,
        start_col_offset_idx=0, end_col_offset_idx=1)]), parent=heading,
        prov=ProvenanceItem(page_no=1, bbox=table_box, charspan=(0, 0)))
    path = tmp_path / "synthetic-docling.json"
    if serialization == "save_as_json":
        model.save_as_json(path)
    else:
        payload = model.model_dump(mode="json") if serialization == "model_dump" else model.export_to_dict()
        path.write_text(json.dumps(payload), encoding="utf-8")
        reference_key = "cref" if serialization == "model_dump" else "$ref"
        assert set(payload["body"]["children"][0]) == {reference_key}
    result = build(json.loads(path.read_bytes()))
    assert result["pages"][0]["line_order"] == [0, 1, 3, 2, 4, 5]
    assert result["pages"][0]["regions"][-1]["table"]["cells"][0]["column_end"] == 1
    assert PRIVATE not in json.dumps(result)


def test_operator_confirmation_exports_separate_exact_derived_text():
    original = recovery()
    proposals = build(report=original)
    review = policy.build_docling_review(proposals, recovery=original, recovery_sha256=DIGESTS["recovery_sha256"],
                                         proposals_sha256="e"*64, confirmed_pages=[1])
    assert review["pages"][0]["original_text"] == original["pages"][0]["candidate"]["text"]
    assert review["pages"][0]["proposed_text"] == "\n".join(
        original["pages"][0]["candidate"]["lines"][i]["text"] for i in (0, 1, 3, 2, 4, 5))
    assert review["operator_review"] == "declared_by_local_operator_not_authenticated"
    assert review["accuracy_verified"] is False
    assert review["canonical_extraction_modified"] is False
    assert policy.validate_docling_review(review, proposals=proposals, recovery=original,
        recovery_sha256=DIGESTS["recovery_sha256"], proposals_sha256="e"*64) == review


@pytest.mark.parametrize("pages", [[], [True], [0], [2], [1, 1], "1", None])
def test_confirmation_requires_explicit_unique_proposed_pages(pages):
    with pytest.raises(ValueError):
        policy.build_docling_review(build(), recovery=recovery(), recovery_sha256=DIGESTS["recovery_sha256"],
                                    proposals_sha256="e"*64, confirmed_pages=pages)


def test_abstained_page_cannot_be_confirmed():
    with pytest.raises(ValueError):
        policy.build_docling_review(build(effective_input_kind="preprocessed"), recovery=recovery(),
            recovery_sha256=DIGESTS["recovery_sha256"], proposals_sha256="e"*64, confirmed_pages=[1])


@pytest.mark.parametrize("mutate", [
    lambda r: r.update(accuracy_verified=True), lambda r: r.update(schema_version=True),
    lambda r: r.update(proposals_sha256="f"*64), lambda r: r.update(operator_review="authenticated"),
    lambda r: r["pages"][0].update(proposed_text="unapproved text"),
    lambda r: r["pages"][0].update(original_text="unapproved original"),
    lambda r: r["pages"][0]["line_order"].__setitem__(0, False),
])
def test_review_revalidation_rejects_text_order_or_attestation_forgery(mutate):
    proposals, original = build(), recovery()
    review = policy.build_docling_review(proposals, recovery=original, recovery_sha256=DIGESTS["recovery_sha256"],
                                         proposals_sha256="e"*64, confirmed_pages=[1])
    mutate(review)
    with pytest.raises(ValueError):
        policy.validate_docling_review(review, proposals=proposals, recovery=original,
            recovery_sha256=DIGESTS["recovery_sha256"], proposals_sha256="e"*64)


@pytest.mark.parametrize("mutation", [
    lambda r: r.update(effective_input_kind="preprocessed"),
    lambda r: r["pages"][0]["page_geometry"].update(width_points=48),
    lambda r: r["pages"][0].update(page_geometry=None),
    lambda r: r["coverage"]["selected_pages"].__setitem__(0, True),
    lambda r: r["parameters"].update(raster_rounding_tolerance_pixels=2.0),
])
def test_abstained_geometry_and_numeric_bool_forgery_rejected(mutation):
    result = build()
    page = result["pages"][0]
    page.update(status="abstained", reasons=["unassigned_lines"], line_order=None)
    result["summary"].update(proposed=0, abstained=1)
    mutation(result)
    with pytest.raises(ValueError):
        policy.validate_docling_proposals(result, recovery=recovery(), recovery_sha256=DIGESTS["recovery_sha256"])


def test_geometry_abstention_cannot_smuggle_regions_even_with_correct_reason():
    result = build()
    result["effective_input_kind"] = "preprocessed"
    result["pages"][0].update(status="abstained", reasons=["effective_input_preprocessed"], line_order=None)
    result["summary"].update(proposed=0, abstained=1)
    with pytest.raises(ValueError):
        policy.validate_docling_proposals(result, recovery=recovery(), recovery_sha256=DIGESTS["recovery_sha256"])


def test_review_rejects_deep_or_cyclic_extra_without_serialization():
    proposals = build()
    review = policy.build_docling_review(proposals, recovery=recovery(), recovery_sha256=DIGESTS["recovery_sha256"],
                                         proposals_sha256="e"*64, confirmed_pages=[1])
    cyclic = []
    cyclic.append(cyclic)
    review["extra"] = cyclic
    with pytest.raises(ValueError):
        policy.validate_docling_review(review, proposals=proposals, recovery=recovery(),
            recovery_sha256=DIGESTS["recovery_sha256"], proposals_sha256="e"*64)


def test_v2_candidate_preprocessing_abstains_even_when_transform_is_identity():
    from test_ocr_layout import _metadata

    original = recovery()
    original["schema_version"] = 2
    original["retry_configuration"]["preprocessing"] = "contrast"
    value = original["pages"][0]["candidate"]
    value["raster"]["coordinate_system"] = "preprocessed_image_pixels"
    metadata = _metadata("contrast")
    metadata["original_raster"] = metadata["processed_raster"] = {"width": 100, "height": 100}
    value["preprocessing"] = metadata
    result = build(report=original)
    assert result["pages"][0]["reasons"] == ["candidate_preprocessed"]
    assert result["pages"][0]["regions"] == []
    result["pages"][0]["regions"] = build()["pages"][0]["regions"]
    result["pages"][0]["spanning_heading_refs"] = ["#/texts/0"]
    result["summary"]["regions"] = 4
    with pytest.raises(ValueError):
        policy.validate_docling_proposals(result, recovery=original, recovery_sha256=DIGESTS["recovery_sha256"])


@pytest.mark.parametrize("limit,reason", [("MAX_LINES", "line_limit"), ("MAX_CELLS", "table_structure_unavailable")])
def test_line_and_table_limits_abstain(monkeypatch, limit, reason):
    monkeypatch.setattr(policy, limit, 0)
    assert reason in build()["pages"][0]["reasons"]


def test_item_resource_limit_rejects_before_traversal(monkeypatch):
    monkeypatch.setattr(policy, "MAX_ITEMS", 1)
    with pytest.raises(ValueError, match="limit"):
        build()


@pytest.mark.parametrize("number", [0, 2, 10**1000])
def test_provenance_outside_source_page_count_rejected(number):
    doc = document()
    doc["texts"][2]["prov"][0]["page_no"] = number
    with pytest.raises(ValueError):
        build(doc)


@pytest.mark.parametrize("child", [
    {"$ref": "#/texts/0", "cref": "#/texts/0"},
    {"cref": "#/texts/0", "extra": True},
    {"$ref": "#/texts/0", "extra": True},
    {"cref": "#/texts/999"}, {"cref": 0}, {},
])
def test_child_reference_encodings_are_exact_not_permissive(child):
    doc = document()
    doc["body"]["children"] = [child]
    with pytest.raises(ValueError):
        build(doc)
