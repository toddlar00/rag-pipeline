import copy
import json
from pathlib import Path

import pytest

import heading_lineage
import quality_core
import retrieval_core
import source_fidelity_core
import table_retrieval_core


_LAST_SOURCE_ORACLE_REGISTRY = None


def _source_item(ref: str, page: int, *, label: str = "text",
                 text: str = "Source text") -> dict:
    return {
        "self_ref": ref,
        "label": label,
        "content_layer": "body",
        "text": text,
        "prov": [{
            "page_no": page,
            "charspan": [0, len(text)],
            "bbox": {
                "l": 10, "t": 10, "r": 200, "b": 20,
                "coord_origin": "TOPLEFT",
            },
        }],
    }


def _record(index: int, ref: str, page: int, *, text: str = "Source text",
            content_type: str = "author_narrative",
            content_source: str = "body") -> dict:
    source_label = {
        "table": "table",
        "footnote": "footnote",
        "figure": "picture",
    }.get(content_source, "text")
    synthetic_item = _source_item(ref, page, label=source_label, text=text)
    descriptor, _ = source_fidelity_core.source_descriptor(synthetic_item)
    transform = source_fidelity_core.default_transform(source_label)
    oracle_tokens = source_fidelity_core.lexical_tokens(text)
    metadata = {
        "chunk_index": index,
        "source_lineage_schema_version": (
            quality_core.SOURCE_LINEAGE_SCHEMA_VERSION),
        "source_items": [{
            "ref": ref,
            "label": source_label,
            "parent_refs": [],
            "spans": descriptor["spans"],
            "scope": {"provenance_indexes": list(range(
                len(descriptor["spans"])))},
            "source_text_sha256": descriptor["source_text_sha256"],
            "source_lexical_sha256": descriptor["source_lexical_sha256"],
            "source_lexical_count": descriptor["source_lexical_count"],
            "transform": transform,
            "oracle_text_sha256": source_fidelity_core.text_sha256(text),
            "oracle_lexical_sha256": (
                source_fidelity_core.lexical_sha256(oracle_tokens)),
            "oracle_lexical_count": len(oracle_tokens),
            "recovery_sha256": (
                source_fidelity_core.recovery_binding_sha256(
                    ref=ref, transform=transform,
                    source_text_sha256=descriptor["source_text_sha256"],
                    oracle_text_sha256=(
                        source_fidelity_core.text_sha256(text)),
                    source_binding=None)
                if transform in source_fidelity_core.OPAQUE_TRANSFORMS
                else None),
        }],
        "page_start": page,
        "page_end": page,
        "chapter_num": 1,
        "source_file": "book.json",
        "content_type": content_type,
        "content_source": content_source,
        "token_count": 4,
        "embedding_token_count": 6,
        "case_names": [],
        "primary_case": None,
    }
    if content_source == "table":
        metadata.update(table_rows=3, table_cols=1)
    return {"text": text, "metadata": metadata}


def _build(records: list[dict], document: dict, **overrides) -> dict:
    global _LAST_SOURCE_ORACLE_REGISTRY
    attach_headings = overrides.pop("_attach_headings", True)
    values = {
        "records": records,
        "stable_ids": [f"chunk_{index}" for index in range(len(records))],
        "chunk_hashes": [f"{index + 1:016x}" for index in range(len(records))],
        "document": document,
        "structural_ranges": [],
        "recovered_table_refs": [],
        "source_name": "book.json",
        "source_sha256": "a" * 64,
        "chunks_name": "book_chunks.jsonl",
        "chunks_sha256": "b" * 64,
        "chunks_size": 123,
        "parameters_sha256": "c" * 64,
        "embedding_model": "model-a",
        "embedding_limit": 512,
        "input_bindings": {
            "docling_json": {
                "name": "book.json",
                "size": 50,
                "sha256": "a" * 64,
            },
            "conversion_manifest": None,
            "table_recovery": None,
            "source_fidelity_oracles": {
                "name": "book_chunks.source-oracles.json",
                "size": 1,
                "sha256": "d" * 64,
                "schema_version": (
                    source_fidelity_core
                    .SOURCE_ORACLE_REGISTRY_SCHEMA_VERSION),
            },
        },
        "source_oracle_registry": None,
    }
    values.update(overrides)
    values["input_bindings"].setdefault(
        "source_fidelity_oracles", {
            "name": "book_chunks.source-oracles.json",
            "size": 1,
            "sha256": "d" * 64,
            "schema_version": (
                source_fidelity_core.SOURCE_ORACLE_REGISTRY_SCHEMA_VERSION),
        })
    source_items = {
        item["self_ref"]: item
        for collection in source_fidelity_core.SOURCE_COLLECTIONS
        for item in document.get(collection, [])
        if isinstance(item, dict) and isinstance(item.get("self_ref"), str)
    }
    for record in values["records"]:
        metadata = record["metadata"]
        metadata["source_lineage_schema_version"] = (
            quality_core.SOURCE_LINEAGE_SCHEMA_VERSION)
        for entry in metadata.get("source_items", []):
            item = source_items.get(entry.get("ref"))
            if item is None:
                continue
            descriptor, _ = source_fidelity_core.source_descriptor(item)
            for field in (
                    "source_text_sha256", "source_lexical_sha256",
                    "source_lexical_count"):
                entry[field] = descriptor[field]
            transform = entry.get("transform")
            if transform in source_fidelity_core.OPAQUE_TRANSFORMS:
                entry["spans"] = descriptor["spans"]
                entry["recovery_sha256"] = (
                    source_fidelity_core.recovery_binding_sha256(
                        ref=entry["ref"], transform=transform,
                        source_text_sha256=descriptor[
                            "source_text_sha256"],
                        oracle_text_sha256=entry["oracle_text_sha256"],
                        source_binding=values["input_bindings"].get(
                            "table_recovery")))
            if transform in {"plain", "list"}:
                oracle = (
                    source_fidelity_core.list_item_oracle_text(item)
                    if transform == "list"
                    else source_fidelity_core.source_item_text(item))
                tokens = source_fidelity_core.lexical_tokens(oracle)
                entry.update({
                    "oracle_text_sha256": (
                        source_fidelity_core.text_sha256(oracle)),
                    "oracle_lexical_sha256": (
                        source_fidelity_core.lexical_sha256(tokens)),
                    "oracle_lexical_count": len(tokens),
                    "recovery_sha256": None,
                })
        source_fidelity_core.attach_record_attestations([record])
    source_oracles = {}
    if values["source_oracle_registry"] is None:
        for record in values["records"]:
            record_tokens = source_fidelity_core.lexical_tokens(record["text"])
            for entry in record["metadata"].get("source_items", []):
                if entry.get("transform") not in (
                        source_fidelity_core.OPAQUE_TRANSFORMS):
                    continue
                if entry["ref"] in source_oracles:
                    continue
                width = entry["oracle_lexical_count"]
                oracle_tokens = ()
                if width:
                    oracle_tokens = next((
                        record_tokens[start:start + width]
                        for start in range(len(record_tokens) - width + 1)
                        if source_fidelity_core.lexical_sha256(
                            record_tokens[start:start + width])
                        == entry["oracle_lexical_sha256"]
                    ), ())
                    if len(oracle_tokens) != width:
                        raise AssertionError(
                            "test fixture cannot recover oracle "
                            f"{entry['ref']}")
                source_oracles.setdefault(entry["ref"], {
                    "transform": entry["transform"],
                    "source_text_sha256": entry["source_text_sha256"],
                    "oracle_text_sha256": entry["oracle_text_sha256"],
                    "oracle_lexical_sha256": entry["oracle_lexical_sha256"],
                    "oracle_lexical_count": entry["oracle_lexical_count"],
                    "oracle_lexical_tokens": list(oracle_tokens),
                    "oracle_group_sha256": (
                        source_fidelity_core
                        .single_source_oracle_group_sha256(entry["ref"])),
                    "oracle_group_members": [entry["ref"]],
                    "recovery_sha256": entry["recovery_sha256"],
                })
    if values["source_oracle_registry"] is None:
        values["source_oracle_registry"] = (
            source_fidelity_core.build_source_oracle_registry(
                oracles=source_oracles,
                input_bindings=quality_core._source_oracle_upstream_inputs(
                    values["input_bindings"])))
    _LAST_SOURCE_ORACLE_REGISTRY = values["source_oracle_registry"]
    if not all(
            "retrieval_linkage_schema_version" in record["metadata"]
            for record in values["records"]):
        retrieval_core._attach_retrieval_linkage(
            values["records"], stable_ids=values["stable_ids"])
    excluded_heading_refs = {
        item["self_ref"] for item in document.get("texts", [])
        if (isinstance(item, dict)
            and quality_core._label(item.get("label")) == "section_header"
            and quality_core.chunking_core
            .is_probable_misclassified_section_header(
                quality_core._source_text(item),
                bbox_height=quality_core._source_bbox_height(item)))
    }
    if attach_headings:
        heading_lineage.attach_heading_bindings(
            document, values["records"],
            structural_ranges=values["structural_ranges"],
            excluded_heading_refs=excluded_heading_refs,
        )
    return quality_core.build_quality_report(**values)


def _validation_kwargs(*, record_count: int = 1) -> dict:
    return {
        "chunks_name": "book_chunks.jsonl",
        "chunks_sha256": "b" * 64,
        "chunks_size": 123,
        "record_count": record_count,
        "stable_ids": [
            f"chunk_{index}" for index in range(record_count)],
        "chunk_hashes": [
            f"{index + 1:016x}" for index in range(record_count)],
        "source_oracle_registry": _LAST_SOURCE_ORACLE_REGISTRY,
    }


def test_quality_report_path_is_canonical_and_adjacent(tmp_path):
    chunks = tmp_path / "book_chunks.jsonl"
    assert quality_core.quality_report_path(chunks) == (
        tmp_path / "book_chunks.quality.json")


def test_quality_report_rejects_left_space_hyphen_boundary():
    text = "A low -level offense."
    document = {"texts": [
        _source_item("#/texts/0", 1, text=text)]}

    report = _build([
        _record(0, "#/texts/0", 1, text=text)], document)

    assert report["normalization"]["split_hyphen"] == [0]
    assert next(
        check for check in report["checks"]
        if check["name"] == "normalization_invariants"
    )["status"] == "fail"


def test_quality_report_preserves_punctuation_dash_before_uppercase_dialogue():
    text = "I'm sorry, Mr. Boomer- We really cannot shut the plant down."
    document = {"texts": [
        _source_item("#/texts/0", 1, text=text)]}

    report = _build([
        _record(0, "#/texts/0", 1, text=text)], document)

    assert report["normalization"].get("split_hyphen", []) == []
    assert next(
        check for check in report["checks"]
        if check["name"] == "normalization_invariants"
    )["status"] == "pass"


def test_quality_report_rejects_standalone_letter_ocr_gibberish():
    text = "The plaintiff must prove t h a t c a u s e in fact."
    document = {"texts": [
        _source_item("#/texts/0", 1, text=text)]}

    report = _build([
        _record(0, "#/texts/0", 1, text=text)], document)

    assert report["normalization"]["ocr_gibberish"] == [0]
    assert next(
        check for check in report["checks"]
        if check["name"] == "normalization_invariants"
    )["status"] == "fail"


def test_quality_report_allows_single_letter_matrix_in_figure_text():
    text = "Figure text: Colorado columns A B A B A B"
    document = {"pictures": [{
        "self_ref": "#/pictures/0", "label": "picture",
        "prov": [{"page_no": 1}],
    }]}
    record = _record(0, "#/pictures/0", 1, text=text)
    record["metadata"].update({
        "content_source": "figure", "content_type": "figure"})

    report = _build([record], document)

    assert report["normalization"].get("ocr_gibberish", []) == []


@pytest.mark.parametrize("text, expected_issue", [
    ("A low -level offense.", "split_hyphen"),
    ("Visit https://www . example . com / source . html", "split_url"),
])
def test_validate_quality_report_rejects_forged_empty_normalization(
        text, expected_issue):
    document = {"texts": [
        _source_item("#/texts/0", 1, text=text)]}
    record = _record(0, "#/texts/0", 1, text=text)
    report = _build([record], document)
    assert report["normalization"] == {expected_issue: [0]}

    report["status"] = "pass"
    report["normalization"] = {}
    normalization_check = next(
        check for check in report["checks"]
        if check["name"] == "normalization_invariants")
    normalization_check.update(status="pass", observed=0)

    with pytest.raises(ValueError, match="normalization does not match"):
        quality_core.validate_quality_report(
            report, **_validation_kwargs(), records=[record])


def test_source_inventory_explains_exclusions():
    document = {
        "texts": [
            _source_item("#/texts/0", 1),
            _source_item("#/texts/1", 2, text="All emphasis added."),
            _source_item("#/texts/2", 9),
            {**_source_item("#/texts/3", 3),
             "content_layer": "furniture"},
            {"self_ref": "#/texts/4", "label": "text", "text": "No page"},
            _source_item("#/texts/5", 4, label="section_header"),
            _source_item("#/texts/6", 5, text="A"),
        ]
    }

    all_items, eligible, exclusions, issues = quality_core.source_inventory(
        document, structural_ranges=[(8, 10)])

    assert len(all_items) == 7
    assert set(eligible) == {"#/texts/0"}
    assert exclusions == {
        "editorial_boilerplate": 1,
        "furniture": 1,
        "label:section_header": 1,
        "no_provenance": 1,
        "section_marker": 1,
        "structural_range": 1,
    }
    assert issues == {
        "invalid_provenance": ["texts[4].missing_provenance"]}


def test_source_inventory_excludes_geometrically_aligned_running_banner():
    page_header = _source_item(
        "#/texts/0", 1, label="page_header", text="SAMPLE SYSTEMS")
    page_header["prov"][0]["bbox"] = {
        "l": 36,
        "t": 756,
        "r": 576,
        "b": 744,
        "coord_origin": "BOTTOMLEFT",
    }
    running_banner = _source_item(
        "#/texts/1", 1, label="list_item",
        text="1 · INTRODUCTION TO SAMPLE SYSTEMS")
    running_banner["prov"][0]["bbox"] = {
        "l": 180,
        "t": 754,
        "r": 432,
        "b": 742,
        "coord_origin": "BOTTOMLEFT",
    }
    running_banner["prov"].append({
        "page_no": 1,
        "charspan": [0, 1],
        "bbox": {
            "l": 180,
            "t": 742,
            "r": 432,
            "b": 730,
            "coord_origin": "BOTTOMLEFT",
        },
    })
    body = _source_item(
        "#/texts/2", 1, text="Substantive discussion of sample operation.")

    all_items, eligible, exclusions, issues = quality_core.source_inventory(
        {"texts": [page_header, running_banner, body]},
        structural_ranges=[])

    assert set(all_items) == {"#/texts/0", "#/texts/1", "#/texts/2"}
    assert set(eligible) == {"#/texts/2"}
    assert exclusions == {
        "label:page_header": 1,
        "running_page_furniture": 1,
    }
    assert issues == {}


def test_numeric_running_page_furniture_requires_shared_document_evidence():
    def numeric_item(
            index: int, page: int, text: str, *, label: str = "text",
            top: float = 36, bottom: float = 48,
    ) -> dict:
        item = _source_item(
            f"#/texts/{index}", page, label=label, text=text)
        item["content_layer"] = (
            "furniture"
            if label in {"page_header", "page_footer"} else "body")
        item["prov"][0]["bbox"] = {
            "l": 40, "t": top, "r": 70, "b": bottom,
            "coord_origin": "TOPLEFT",
        }
        return item

    texts = [
        numeric_item(0, 110, "100", label="page_header"),
        numeric_item(1, 111, "101", label="page_header"),
        numeric_item(2, 112, "102", label="page_header"),
        # A correctly labeled outlier cannot override the dominant +10 delta.
        numeric_item(3, 113, "93", label="page_header"),
        numeric_item(4, 114, "104"),
        # Exact value and delta, but body geometry rather than a margin lane.
        numeric_item(5, 115, "105", top=350, bottom=365),
        # Exact margin number, but it follows the non-dominant +20 delta.
        numeric_item(6, 116, "96"),
        # Matching numbers still require the exact mislabeled ``text`` label.
        numeric_item(7, 117, "107", label="list_item"),
        # Page-number-like prose is not an exact numeric source item.
        numeric_item(8, 118, "Page 108"),
    ]
    document = {
        "texts": texts,
        "pages": {
            str(page): {"size": {"width": 600, "height": 800}}
            for page in range(110, 119)
        },
    }

    refs = quality_core.running_page_number_furniture_refs(document)
    _, eligible, exclusions, issues = quality_core.source_inventory(
        document, structural_ranges=[])

    assert refs == {"#/texts/4"}
    assert set(eligible) == {
        "#/texts/5", "#/texts/6", "#/texts/7", "#/texts/8"}
    assert exclusions == {
        "label:page_header": 4,
        "running_page_furniture": 1,
    }
    assert issues == {}


def test_sparse_ocr_heading_artifacts_are_typed_in_inventory_and_analysis():
    texts = [
        _source_item(
            "#/texts/0", 1, label="section_header", text="A     IE  7ES"),
        _source_item(
            "#/texts/1", 2, label="section_header", text="L     IE  ES"),
        _source_item(
            "#/texts/2", 3, label="section_header", text="IE  ES"),
    ]
    for item in texts:
        item["prov"][0]["bbox"] = {
            "l": 20, "t": 10, "r": 280, "b": 25,
            "coord_origin": "TOPLEFT",
        }
    document = {
        "texts": texts,
        "pages": {
            str(page): {"size": {"width": 300, "height": 400}}
            for page in range(1, 4)
        },
    }

    _, eligible, exclusions, issues = quality_core.source_inventory(
        document, structural_ranges=[])
    analysis = quality_core._section_heading_analysis(
        document, records=[], ranges=[])

    assert eligible == {}
    assert exclusions == {
        heading_lineage.SPARSE_OCR_HEADING_ARTIFACT_REASON: 3}
    assert issues == {}
    assert analysis["source_items"] == 3
    assert analysis["attachable_items"] == 0
    assert analysis["exceptions"] == {
        heading_lineage.EMBEDDED_OUTLINE_REASON: [],
        heading_lineage.RUNNING_FURNITURE_REASON: [],
    }
    assert analysis["artifacts"] == {
        heading_lineage.SPARSE_OCR_HEADING_ARTIFACT_REASON: [
            "#/texts/0", "#/texts/1", "#/texts/2"],
    }


def test_source_inventory_serializes_geometry_and_relationships():
    parent = _source_item(
        "#/texts/0", 1, label="section_header", text="Heading")
    parent["children"] = [{"cref": "#/texts/1"}]
    child = _source_item("#/texts/1", 2)
    child["prov"][0]["bbox"] = {
        "l": 1.23456,
        "t": 8.76543,
        "r": 9,
        "b": 2,
        "coord_origin": "bottomleft",
    }

    all_items, eligible, _, issues = quality_core.source_inventory(
        {"texts": [parent, child]}, structural_ranges=[])

    assert set(eligible) == {"#/texts/1"}
    assert all_items["#/texts/1"] == {
        "label": "text",
        "pages": [2],
        "parent_refs": ["#/texts/0"],
        "spans": [{
            "provenance_index": 0,
            "page": 2,
            "bbox": [1.235, 8.765, 9.0, 2.0],
            "origin": "BOTTOMLEFT",
            "charspan": [0, len("Source text")],
        }],
        "source_text_sha256": source_fidelity_core.text_sha256(
            "Source text"),
        "source_lexical_sha256": source_fidelity_core.lexical_sha256(
            "Source text"),
        "source_lexical_count": 2,
    }
    assert issues == {}


@pytest.mark.parametrize("document", [
    {"texts": [
        _source_item("#/texts/0", 1),
        {"label": "text", "text": "Unidentified substantive source"},
    ]},
    {"texts": [
        _source_item("#/texts/0", 1),
        _source_item("#/texts/0", 2, text="Different source object"),
    ]},
    {"texts": {"self_ref": "#/texts/0"}},
    {"texts": [
        _source_item("#/texts/0", 1),
        {**_source_item("#/texts/1", 2),
         "prov": [{"page_no": "two"}]},
    ]},
    {"texts": [
        {**_source_item("#/texts/0", 1), "children": "#/texts/1"},
    ]},
    {"texts": [
        {**_source_item("#/texts/0", 1),
         "children": [{"cref": "#/texts/999"}]},
    ]},
])
def test_source_inventory_integrity_is_a_hard_failure(document):
    report = _build([_record(0, "#/texts/0", 1)], document)

    assert report["status"] == "fail"
    assert report["source_lineage"]["inventory_issues"]
    check = next(check for check in report["checks"]
                 if check["name"] == "source_inventory_integrity")
    assert check["status"] == "fail"


def test_build_quality_report_passes_and_is_deterministic():
    document = {"texts": [_source_item("#/texts/0", 1)]}
    records = [_record(0, "#/texts/0", 1)]

    first = _build(records, document)
    second = _build(copy.deepcopy(records), copy.deepcopy(document))

    assert first["status"] == "pass"
    assert first["source_lineage"]["coverage_ppm"] == 1_000_000
    assert first["hashes"]["unique_stable_ids"] == 1
    assert json.dumps(first, sort_keys=True, separators=(",", ":")) == (
        json.dumps(second, sort_keys=True, separators=(",", ":")))


def test_source_analysis_tracks_recovered_substantive_page_footers():
    body = _source_item("#/texts/0", 1)
    page_label = _source_item(
        "#/texts/1", 1, label="page_footer", text="1")
    citation = _source_item(
        "#/texts/2", 2, label="page_footer",
        text="46. https://example.test/archive/ABCD-1234")
    repeated = [
        _source_item(
            f"#/texts/{index}", index, label="page_footer",
            text="Confidential draft")
        for index in range(3, 6)
    ]
    for item in (page_label, citation, *repeated):
        item["content_layer"] = "furniture"
    document = {"texts": [body, page_label, citation, *repeated]}
    recovered = _record(
        1, "#/texts/2", 2, text=citation["text"],
        content_type="footnote", content_source="footnote")
    recovered["metadata"]["source_items"][0]["label"] = "page_footer"
    records = [_record(0, "#/texts/0", 1), recovered]

    report = _build(records, document)

    assert report["status"] == "pass"
    assert report["source_analysis"]["substantive_page_footers"] == {
        "total": 5,
        "page_label_like": 1,
        "repeating_or_short": 3,
        "substantive_detected": 1,
        "substantive_represented": 1,
        "substantive_risk": 0,
        "substantive_risk_refs": [],
    }
    check = next(
        item for item in report["checks"]
        if item["name"] == "substantive_excluded_page_footers")
    assert check == {
        "name": "substantive_excluded_page_footers",
        "status": "pass",
        "observed": 0,
        "required": 0,
    }


def test_source_analysis_reports_picture_text_coverage_and_large_risk():
    body = _source_item("#/texts/0", 1)
    caption = _source_item(
        "#/texts/1", 1, label="caption", text="Process diagram")
    pictures = []
    for index, size in enumerate((80, 80, 20)):
        picture = _source_item(
            f"#/pictures/{index}", 1, label="picture", text="")
        picture["prov"][0]["bbox"] = {
            "l": 0, "t": size, "r": size, "b": 0,
            "coord_origin": "BOTTOMLEFT",
        }
        pictures.append(picture)
    pictures[0]["captions"] = [{"cref": "#/texts/1"}]
    records = [
        _record(0, "#/texts/0", 1),
        _record(1, "#/texts/1", 1, text="Process diagram"),
    ]
    records[1]["metadata"]["source_items"][0].update({
        "label": "caption",
        "parent_refs": ["#/pictures/0"],
    })
    document = {
        "texts": [body, caption],
        "pictures": pictures,
        "pages": {"1": {
            "page_no": 1,
            "size": {"width": 100, "height": 100},
        }},
    }

    report = _build(records, document)

    assert report["status"] == "fail"
    assert report["source_lineage"]["missing_refs"] == [
        "#/pictures/0", "#/pictures/1"]
    assert report["source_analysis"]["pictures"] == {
        "total": 3,
        "with_linked_text": 1,
        "covered_by_linked_text": 1,
        "covered_by_figure_text": 0,
        "missing_linked_text": 0,
        "large_unlinked": 1,
        "other_unlinked": 1,
        "substantive_risk": 1,
        "substantive_risk_refs": ["#/pictures/1"],
    }
    check = next(
        item for item in report["checks"]
        if item["name"] == "uncovered_substantive_pictures")
    assert check["status"] == "warn"
    assert check["observed"] == 1


def test_source_analysis_attests_exact_section_heading_occurrences():
    document = {"texts": [
        _source_item(
            "#/texts/2", 1, label="section_header",
            text="Operator-Access Policy"),
        _source_item("#/texts/0", 1),
        _source_item(
            "#/texts/3", 2, label="section_header",
            text="Unattached Heading"),
        _source_item("#/texts/1", 2),
    ]}
    records = [
        _record(0, "#/texts/0", 1),
        _record(1, "#/texts/1", 2),
    ]
    records[0]["metadata"]["section_path"] = "Operator-  Access Policy"
    records[1]["metadata"]["section_path"] = "Unattached Heading"

    report = _build(records, document)

    assert report["status"] == "pass"
    analysis = report["source_analysis"]["section_headings"]
    assert analysis["schema_version"] == (
        heading_lineage.HEADING_LINEAGE_SCHEMA_VERSION)
    assert analysis["policy"] == heading_lineage.HEADING_LINEAGE_POLICY
    assert analysis["source_items"] == analysis["attachable_items"] == 2
    assert analysis["attached_items"] == analysis["directly_owned_items"] == 2
    assert analysis["item_coverage_ppm"] == 1_000_000
    assert all(analysis[field] == [] for field in (
        "missing_refs", "unattached_refs", "missing_direct_refs",
        "duplicate_direct_refs", "invalid_binding_records",
        "scope_conflict_records", "display_binding_records"))
    assert len(analysis["evidence_sha256"]) == 64
    check = next(
        item for item in report["checks"]
        if item["name"] == "section_heading_attachment")
    assert check["status"] == "pass"
    assert check["observed"] == 0
    assert quality_core.validate_quality_report(
        report, **_validation_kwargs(record_count=2), records=records,
        document=document) is report

    tampered_records = copy.deepcopy(records)
    tampered_records[1]["metadata"][heading_lineage.HEADING_PATH_FIELD] = [
        "#/texts/2"]
    with pytest.raises(ValueError, match="does not match source"):
        quality_core.validate_quality_report(
            report, **_validation_kwargs(record_count=2),
            records=tampered_records, document=document)


def test_section_heading_evidence_canonicalizes_resumption_proof_order(
        monkeypatch):
    first = {
        "kind": "running_ancestor_resumption",
        "running_ref": "#/texts/20",
        "ancestor_ref": "#/texts/2",
        "superseded_refs": ["#/texts/8"],
    }
    second = {
        "kind": "running_ancestor_resumption",
        "running_ref": "#/texts/30",
        "ancestor_ref": "#/texts/3",
        "superseded_refs": ["#/texts/9"],
    }
    base_audit = {
        "source_refs": ["#/texts/2", "#/texts/3"],
        "attachable_refs": ["#/texts/2", "#/texts/3"],
        "exception_refs": {
            heading_lineage.RUNNING_FURNITURE_REASON: [],
            heading_lineage.EMBEDDED_OUTLINE_REASON: [],
        },
        "artifact_refs": {
            heading_lineage.SPARSE_OCR_HEADING_ARTIFACT_REASON: [],
        },
        "unattached_refs": [],
        "missing_direct_refs": [],
        "missing_refs": [],
        "duplicate_direct_refs": [],
        "invalid_binding_records": [],
        "scope_conflict_records": [],
        "display_binding_records": [],
    }

    def evidence_for(proofs):
        audit = copy.deepcopy(base_audit)
        audit["ancestor_resumptions"] = {0: proofs}
        monkeypatch.setattr(
            heading_lineage, "audit_heading_bindings",
            lambda *_args, **_kwargs: audit)
        return quality_core._section_heading_analysis(
            {"texts": []}, records=[], ranges=())["evidence_sha256"]

    assert evidence_for([first, second]) == evidence_for([second, first])


def test_heading_occurrence_tampering_is_a_hard_quality_failure():
    document = {"texts": [
        _source_item(
            "#/texts/0", 1, label="section_header", text="Chapter 1"),
        _source_item("#/texts/1", 1, text="Body"),
    ]}
    records = [_record(0, "#/texts/1", 1, text="Body")]
    records[0]["metadata"]["section_path"] = "Chapter 1"
    assert _build(records, document)["status"] == "pass"

    records[0]["metadata"][heading_lineage.HEADING_PATH_FIELD] = []
    failed = _build(records, document, _attach_headings=False)
    check = next(item for item in failed["checks"]
                 if item["name"] == "section_heading_attachment")

    assert failed["status"] == "fail"
    assert check["status"] == "fail"
    assert check["observed"] > 0
    assert failed["source_analysis"]["section_headings"][
        "invalid_binding_records"] == [0]


def test_section_heading_analysis_uses_geometry_for_packaging_copy_only():
    packaging = _source_item(
        "#/texts/0", 1, label="section_header",
        text=(
            "PORTABLE SAMPLE MODULE REMAINS READY FOR CONTROLLED LABORATORY "
            "USE WITH SEALED COMPONENTS CLEAR STATUS LIGHTS REUSABLE "
            "PACKAGING SIMPLE STARTUP INSTRUCTIONS AND CONSISTENT PERFORMANCE "
            "ACROSS ROUTINE DEMONSTRATIONS"
        ))
    packaging["prov"][0]["bbox"] = {
        "l": 100, "r": 400, "t": 600, "b": 496,
        "coord_origin": "BOTTOMLEFT",
    }
    legitimate = _source_item(
        "#/texts/1", 1, label="section_header",
        text=(
            "THE RESPONSE MUST FOLLOW EACH CLEAR INSTRUCTION IN THE SAMPLE "
            "REQUEST AND USE THE SPECIFIED FORMAT FOR DELIVERY"
        ))
    legitimate["prov"][0]["bbox"] = {
        "l": 100, "r": 400, "t": 400, "b": 337.6,
        "coord_origin": "BOTTOMLEFT",
    }

    analysis = quality_core._section_heading_analysis(
        {"texts": [packaging, legitimate]}, records=[], ranges=[])

    assert analysis["source_items"] == 1
    assert analysis["missing_refs"] == ["#/texts/1"]


def test_source_analysis_respects_document_profile_structural_ranges():
    body = _source_item("#/texts/0", 1)
    footer = _source_item(
        "#/texts/1", 8, label="page_footer",
        text="46. https://example.test/archive/ABCD-1234")
    heading = _source_item(
        "#/texts/2", 8, label="section_header",
        text="Back Matter Heading")
    picture = _source_item(
        "#/pictures/0", 8, label="picture", text="")
    picture["prov"][0]["bbox"] = {
        "l": 0, "t": 80, "r": 80, "b": 0,
        "coord_origin": "BOTTOMLEFT",
    }
    document = {
        "texts": [body, footer, heading],
        "pictures": [picture],
        "pages": {"8": {
            "page_no": 8,
            "size": {"width": 100, "height": 100},
        }},
    }
    records = [_record(0, "#/texts/0", 1)]

    report = _build(records, document, structural_ranges=[(8, 8)])

    analysis = report["source_analysis"]
    assert analysis["substantive_page_footers"]["total"] == 0
    assert analysis["pictures"]["total"] == 0
    headings = analysis["section_headings"]
    assert headings["source_items"] == headings["attachable_items"] == 0
    assert headings["attached_items"] == headings["directly_owned_items"] == 0
    assert headings["item_coverage_ppm"] == 1_000_000
    assert all(headings[field] == [] for field in (
        "missing_refs", "unattached_refs", "missing_direct_refs",
        "duplicate_direct_refs", "invalid_binding_records",
        "scope_conflict_records", "display_binding_records"))
    assert quality_core.validate_quality_report(
        report, **_validation_kwargs(), records=records,
        document=document, structural_ranges=[(8, 8)]) is report


def test_legacy_v3_report_requires_reprocessing_even_if_requested():
    document = {"texts": [_source_item("#/texts/0", 1)]}
    records = [_record(0, "#/texts/0", 1)]
    current = _build(records, document)
    legacy = copy.deepcopy(current)
    legacy["schema_version"] = 3
    legacy.pop("table_retrieval")
    legacy.pop("source_analysis")
    legacy["checks"] = [
        check for check in legacy["checks"]
        if check["name"] not in {
            "table_retrieval_invariants",
            "substantive_excluded_page_footers",
            "uncovered_substantive_pictures",
            "section_heading_attachment",
        }
    ]
    raw = json.dumps(legacy, sort_keys=True).encode("utf-8")
    kwargs = _validation_kwargs()
    kwargs["records"] = records

    with pytest.raises(ValueError, match="invalid field set"):
        quality_core.parse_quality_report_bytes(raw, **kwargs)
    with pytest.raises(ValueError, match="invalid field set"):
        quality_core.parse_quality_report_bytes(
            raw, compatible_schema_versions=(3,), **kwargs)


def test_legacy_v4_report_requires_reprocessing_even_if_requested():
    document = {"texts": [_source_item("#/texts/0", 1)]}
    records = [_record(0, "#/texts/0", 1)]
    current = _build(records, document)
    legacy = copy.deepcopy(current)
    legacy["schema_version"] = 4
    legacy.pop("source_analysis")
    legacy["checks"] = [
        check for check in legacy["checks"]
        if check["name"] not in {
            "substantive_excluded_page_footers",
            "uncovered_substantive_pictures",
            "section_heading_attachment",
        }
    ]
    raw = json.dumps(legacy, sort_keys=True).encode("utf-8")
    kwargs = _validation_kwargs()
    kwargs["records"] = records

    with pytest.raises(ValueError, match="invalid field set"):
        quality_core.parse_quality_report_bytes(raw, **kwargs)
    with pytest.raises(ValueError, match="invalid field set"):
        quality_core.parse_quality_report_bytes(
            raw, compatible_schema_versions=(4,), **kwargs)

    legacy["source_analysis"] = current["source_analysis"]
    with pytest.raises(ValueError, match="unsupported"):
        quality_core.parse_quality_report_bytes(
            json.dumps(legacy).encode("utf-8"),
            compatible_schema_versions=(4,), **kwargs)


def test_retrieval_linkage_is_attested_and_fails_closed_on_tampering():
    document = {"texts": [
        _source_item("#/texts/0", 1, text="First source passage"),
        _source_item("#/texts/1", 2, text="Second source passage"),
    ]}
    records = [
        _record(0, "#/texts/0", 1, text="First source passage"),
        _record(1, "#/texts/1", 2, text="Second source passage"),
    ]
    report = _build(records, document)

    assert report["status"] == "pass"
    assert report["retrieval"] == {
        "schema_version": 2,
        "context_parents": 1,
        "linked_chunks": 2,
        "isolated_chunks": 0,
        "issues": {},
    }

    records[0]["metadata"]["next_stable_id"] = (
        records[0]["metadata"]["stable_id"])
    tampered = _build(records, document)
    assert tampered["status"] == "fail"
    assert tampered["retrieval"]["issues"] == {"next_stable_id": [0]}
    check = next(
        item for item in tampered["checks"]
        if item["name"] == "retrieval_linkage_invariants")
    assert check["status"] == "fail"


def test_table_retrieval_hierarchy_is_attested_and_children_do_not_regress_pages():
    table_text = """Fee restrictions
| Arrangement | Safeguard |
| --- | --- |
| Contingent fee | Written agreement |
| Fee division | Client consent |
| Contingent fee | Written agreement |
| Business transaction | Independent advice |
"""
    document = {
        "tables": [_source_item(
            "#/tables/0", 1, label="table", text=table_text)],
        "texts": [_source_item("#/texts/0", 5, text="Later narrative")],
    }
    primary = [
        _record(0, "#/tables/0", 1, text=table_text,
                content_type="table", content_source="table"),
        _record(1, "#/texts/0", 5, text="Later narrative"),
    ]
    records = table_retrieval_core.expand_table_records(
        primary, stable_id_fn=retrieval_core._chunk_id,
        token_count_fn=lambda value: len(value.split()),
    )
    for index, record in enumerate(records):
        record["metadata"]["chunk_index"] = index
        record["metadata"]["embedding_token_count"] = (
            record["metadata"]["token_count"] + 1)
    stable_ids = [retrieval_core._chunk_id(record) for record in records]
    retrieval_core._attach_retrieval_linkage(
        records, stable_ids=stable_ids)
    chunk_hashes = [f"{index + 1:016x}" for index in range(len(records))]

    report = _build(
        records, document, stable_ids=stable_ids,
        chunk_hashes=chunk_hashes)

    assert report["status"] == "pass"
    assert report["corpus"]["page_regressions"] == []
    assert report["table_retrieval"] == {
        "schema_version": 1,
        "parent_tables": 1,
        "expanded_parents": 1,
        "child_chunks": 4,
        "issues": {},
    }
    kwargs = _validation_kwargs(record_count=len(records))
    kwargs.update(
        stable_ids=stable_ids, chunk_hashes=chunk_hashes, records=records)
    assert quality_core.validate_quality_report(report, **kwargs) is report

    source_shape_mismatch = copy.deepcopy(document)
    source_shape_mismatch["tables"][0]["data"] = {
        "num_rows": 6,
        "num_cols": 3,
    }
    source_attested = _build(
        records, source_shape_mismatch, stable_ids=stable_ids,
        chunk_hashes=chunk_hashes)
    assert source_attested["status"] == "fail"
    assert source_attested["table_retrieval"]["issues"][
        "source_native_row_count_mismatch"] == [0]
    assert source_attested["table_retrieval"]["issues"][
        "source_native_column_count_mismatch"] == [0]

    tampered_records = copy.deepcopy(records)
    tampered_records[-1]["metadata"]["table_parent_stable_id"] = "missing"
    tampered = _build(
        tampered_records, document, stable_ids=stable_ids,
        chunk_hashes=chunk_hashes)
    assert tampered["status"] == "fail"
    assert tampered["table_retrieval"]["issues"]
    check = next(
        item for item in tampered["checks"]
        if item["name"] == "table_retrieval_invariants")
    assert check["status"] == "fail"


def test_missing_source_identity_is_a_hard_failure():
    document = {"texts": [
        _source_item("#/texts/0", 1),
        _source_item("#/texts/1", 2),
    ]}

    report = _build([_record(0, "#/texts/0", 1)], document)

    assert report["status"] == "fail"
    assert report["source_lineage"]["missing_refs"] == ["#/texts/1"]
    failed = {check["name"] for check in report["checks"]
              if check["status"] == "fail"}
    assert "eligible_source_items_represented" in failed


@pytest.mark.parametrize(("field", "replacement"), (
    ("label", "caption"),
    ("spans", [{
        "provenance_index": 0,
        "page": 3,
        "bbox": [10.0, 10.0, 200.0, 20.0],
        "origin": "TOPLEFT",
        "charspan": [0, len("Source text")],
    }]),
    ("parent_refs", ["#/texts/999"]),
))
def test_lineage_fields_must_match_source_inventory(field, replacement):
    parent = _source_item(
        "#/texts/0", 1, label="section_header", text="Heading")
    parent["children"] = [{"cref": "#/texts/1"}]
    child = _source_item("#/texts/1", 2)
    record = _record(0, "#/texts/1", 2)
    record["metadata"]["source_items"][0]["parent_refs"] = ["#/texts/0"]
    record["metadata"]["section_path"] = "Heading"

    assert _build([record], {"texts": [parent, child]})["status"] == "pass"

    record["metadata"]["source_items"][0][field] = replacement
    report = _build([record], {"texts": [parent, child]})

    assert report["status"] == "fail"
    assert report["source_lineage"]["metadata_mismatches"] == [{
        "chunk_index": 0,
        "ref": "#/texts/1",
        "fields": [field],
    }]
    check = next(check for check in report["checks"]
                 if check["name"] == "source_lineage_matches_source")
    assert check["status"] == "fail"


def test_duplicate_text_at_distinct_sources_warns_without_hiding_lineage():
    document = {"texts": [
        _source_item(
            "#/texts/0", 1, text="Repeated substantive language"),
        _source_item(
            "#/texts/1", 2, text="Repeated substantive language"),
    ]}
    records = [
        _record(0, "#/texts/0", 1, text="Repeated substantive language"),
        _record(1, "#/texts/1", 2, text="Repeated substantive language"),
    ]

    report = _build(records, document)

    assert report["status"] == "pass"
    duplicate_check = next(
        check for check in report["checks"]
        if check["name"] == "canonical_text_duplicates")
    assert duplicate_check["status"] == "warn"
    assert report["source_lineage"]["represented_items"] == 2


def test_table_source_requires_markdown_shape_and_metadata():
    table = _source_item("#/tables/0", 3, label="table", text="")
    good = _record(
        0, "#/tables/0", 3,
        text="| Rule |\n|---|\n| Value |",
        content_type="table", content_source="table")
    good["metadata"]["source_items"][0]["label"] = "table"

    assert _build([good], {"tables": [table]})["status"] == "pass"
    trusted_registry = copy.deepcopy(_LAST_SOURCE_ORACLE_REGISTRY)

    bad = copy.deepcopy(good)
    bad["text"] = "flattened table prose"
    assert _build(
        [bad], {"tables": [table]},
        source_oracle_registry=trusted_registry)["status"] == "fail"


def test_headerless_single_row_table_shape_is_source_attested():
    table = _source_item("#/tables/0", 3, label="table", text="")
    table["data"] = {
        "num_rows": 1,
        "num_cols": 3,
        "table_cells": [
            {"column_header": False},
            {"column_header": False},
            {"column_header": False},
        ],
    }
    text = (
        "|  |  |  |\n"
        "|---|---|---|\n"
        "| Interview | Research | Investigation |"
    )
    record = _record(
        0, "#/tables/0", 3, text=text,
        content_type="table", content_source="table")
    record["metadata"].update(table_rows=1, table_cols=3)

    report = _build([record], {"tables": [table]})

    assert report["status"] == "pass"
    assert report["table_retrieval"]["issues"] == {}

    ambiguous = copy.deepcopy(table)
    del ambiguous["data"]["table_cells"][1]["column_header"]
    rejected = _build([record], {"tables": [ambiguous]})
    assert rejected["status"] == "fail"
    assert rejected["table_retrieval"]["issues"][
        "source_native_row_count_mismatch"] == [0]


def test_page_regression_is_a_hard_failure():
    document = {"texts": [
        _source_item("#/texts/0", 2, text="First"),
        _source_item("#/texts/1", 1, text="Second"),
    ]}
    records = [
        _record(0, "#/texts/0", 2, text="First"),
        _record(1, "#/texts/1", 1, text="Second"),
    ]

    report = _build(records, document)

    assert report["status"] == "fail"
    assert report["corpus"]["page_regressions"] == [1]
    assert report["corpus"]["allowed_page_regressions"] == []
    assert [entry["chunk_index"] for entry in report["corpus"][
        "unexpected_page_regressions"]] == [1]
    check = next(check for check in report["checks"]
                 if check["name"] == "page_regressions")
    assert check["status"] == "fail"


def test_rule_to_authors_lane_transition_is_an_audited_page_regression():
    document = {"texts": [
        _source_item(
            "#/texts/0", 3, label="section_header",
            text="Rule Section"),
        _source_item("#/texts/1", 3, text="Rule content"),
        _source_item("#/texts/2", 2, text="Author explanation"),
    ]}
    heading = _record(
        0, "#/texts/0", 3, text="Rule Section")
    heading["metadata"]["source_items"][0]["label"] = "section_header"
    heading["metadata"]["section_path"] = ""
    rule = _record(
        1, "#/texts/1", 3, text="Rule content",
        content_type="statutory_excerpt")
    explanation = _record(
        2, "#/texts/2", 2, text="Author explanation",
        content_type="author_narrative")
    for record in (rule, explanation):
        record["metadata"]["section_path"] = "Rule Section"
    rule["metadata"]["headings"] = ["Rule language**"]
    explanation["metadata"]["headings"] = ["Authors' explanation***"]

    report = _build([heading, rule, explanation], document)

    assert report["status"] == "pass"
    assert report["corpus"]["page_regressions"] == [2]
    assert report["corpus"]["unexpected_page_regressions"] == []
    assert report["corpus"]["allowed_page_regressions"] == [{
        "chunk_index": 2,
        "previous_chunk_index": 1,
        "previous_page_start": 3,
        "page_start": 2,
        "section_path": "Rule Section",
        "previous_content_type": "statutory_excerpt",
        "content_type": "author_narrative",
        "previous_headings": ["Rule language**"],
        "headings": ["Authors' explanation***"],
        "reason": "rule_language_to_authors_explanation",
    }]
    check = next(check for check in report["checks"]
                 if check["name"] == "page_regressions")
    assert check["status"] == "pass"
    quality_core.validate_quality_report(
        report, **_validation_kwargs(record_count=3))


def test_cross_page_continuation_footnote_is_an_audited_regression():
    document = {"texts": [
        _source_item(
            "#/texts/0", 2, text="continues on the next page."),
        _source_item("#/texts/1", 1, label="footnote",
                     text="1. 321 Ex. 456 (2025)."),
    ]}
    continuation = _record(
        0, "#/texts/0", 2, text="continues on the next page.")
    footnote = _record(
        1, "#/texts/1", 1, text="1. 321 Ex. 456 (2025).",
        content_type="footnote", content_source="footnote")
    continuation["metadata"]["section_path"] = ""
    footnote["metadata"]["section_path"] = ""
    footnote["metadata"][quality_core.PAGE_ORDER_REASON_FIELD] = (
        quality_core.FOOTNOTE_AFTER_CONTINUATION_REASON)

    report = _build([continuation, footnote], document)

    assert report["status"] == "pass"
    assert report["corpus"]["unexpected_page_regressions"] == []
    evidence = report["corpus"]["allowed_page_regressions"]
    assert len(evidence) == 1
    assert evidence[0]["reason"] == (
        quality_core.FOOTNOTE_AFTER_CONTINUATION_REASON)
    assert evidence[0]["previous_content_source"] == "body"
    assert evidence[0]["content_source"] == "footnote"
    quality_core.validate_quality_report(
        report, **_validation_kwargs(record_count=2))


def test_unmarked_cross_page_footnote_regression_remains_a_failure():
    document = {"texts": [
        _source_item("#/texts/0", 2),
        _source_item("#/texts/1", 1, label="footnote",
                     text="1. 321 Ex. 456 (2025)."),
    ]}
    continuation = _record(0, "#/texts/0", 2)
    footnote = _record(
        1, "#/texts/1", 1, text="1. 321 Ex. 456 (2025).",
        content_type="footnote", content_source="footnote")

    report = _build([continuation, footnote], document)

    assert report["status"] == "fail"
    assert report["corpus"]["allowed_page_regressions"] == []
    assert len(report["corpus"]["unexpected_page_regressions"]) == 1


def test_raw_token_count_is_required_for_every_record():
    document = {"texts": [_source_item("#/texts/0", 1)]}
    record = _record(0, "#/texts/0", 1)
    del record["metadata"]["token_count"]

    report = _build([record], document)

    assert report["status"] == "fail"
    assert report["embedding"]["raw_token_count"] == 0
    check = next(check for check in report["checks"]
                 if check["name"] == "raw_token_counts_present")
    assert check == {
        "name": "raw_token_counts_present",
        "status": "fail",
        "observed": 0,
        "required": 1,
    }


def test_validate_quality_report_binds_source_parameters_and_hash_roots():
    document = {"texts": [_source_item("#/texts/0", 1)]}
    report = _build([_record(0, "#/texts/0", 1)], document)
    kwargs = {
        "chunks_name": "book_chunks.jsonl",
        "chunks_sha256": "b" * 64,
        "chunks_size": 123,
        "record_count": 1,
        "stable_ids": ["chunk_0"],
        "chunk_hashes": ["0000000000000001"],
        "source_name": "book.json",
        "source_sha256": "a" * 64,
        "parameters_sha256": "c" * 64,
        "embedding_model": "model-a",
        "embedding_limit": 512,
        "source_oracle_registry": _LAST_SOURCE_ORACLE_REGISTRY,
    }

    assert quality_core.validate_quality_report(report, **kwargs) is report

    for key, replacement in (
            ("chunks_sha256", "d" * 64),
            ("source_sha256", "e" * 64),
            ("parameters_sha256", "f" * 64),
            ("stable_ids", ["chunk_changed"]),
            ("chunk_hashes", ["1234567890abcdef"])):
        changed = dict(kwargs)
        changed[key] = replacement
        with pytest.raises(ValueError):
            quality_core.validate_quality_report(report, **changed)


def test_validate_quality_report_rejects_failed_or_tampered_gates():
    document = {"texts": [_source_item("#/texts/0", 1)]}
    report = _build([_record(0, "#/texts/0", 1)], document)
    kwargs = {
        "chunks_name": "book_chunks.jsonl",
        "chunks_sha256": "b" * 64,
        "chunks_size": 123,
        "record_count": 1,
        "stable_ids": ["chunk_0"],
        "chunk_hashes": ["0000000000000001"],
        "source_oracle_registry": _LAST_SOURCE_ORACLE_REGISTRY,
    }

    failed = copy.deepcopy(report)
    failed["status"] = "fail"
    with pytest.raises(ValueError, match="did not pass"):
        quality_core.validate_quality_report(failed, **kwargs)

    tampered = copy.deepcopy(report)
    tampered["source_lineage"]["missing_refs"] = ["#/texts/1"]
    with pytest.raises(ValueError, match="lineage gate"):
        quality_core.validate_quality_report(tampered, **kwargs)

    tampered = copy.deepcopy(report)
    tampered["source_lineage"]["metadata_mismatches"] = [{
        "chunk_index": 0,
        "ref": "#/texts/0",
        "fields": ["label"],
    }]
    with pytest.raises(ValueError, match="lineage gate"):
        quality_core.validate_quality_report(tampered, **kwargs)

    tampered = copy.deepcopy(report)
    tampered["corpus"]["page_regressions"] = [0]
    with pytest.raises(ValueError, match="page order gate"):
        quality_core.validate_quality_report(tampered, **kwargs)

    tampered = copy.deepcopy(report)
    tampered["embedding"]["raw_token_count"] = 0
    with pytest.raises(ValueError, match="raw token gate"):
        quality_core.validate_quality_report(tampered, **kwargs)


@pytest.mark.parametrize("mutation", [
    lambda report: report["source"].pop("docling_json"),
    lambda report: report.__setitem__("parameters_sha256", None),
    lambda report: report["embedding"].__setitem__("model", None),
    lambda report: report["source"]["docling_json"].__setitem__(
        "sha256", "d" * 64),
    lambda report: report["source"].__setitem__("unexpected", {}),
    lambda report: report["tables"].__setitem__(
        "issues", {"hidden_failure": [0]}),
    lambda report: report["corpus"]["content_types"].__setitem__(
        "author_narrative", 2),
    lambda report: report["source_lineage"].__setitem__(
        "eligible_items", 2),
    lambda report: report["embedding"].__setitem__(
        "raw_token_p50", report["embedding"]["raw_token_max"] + 1),
    lambda report: report["embedding"].__setitem__("inputs_over_limit", 77),
    lambda report: report["hashes"].__setitem__("unique_stable_ids", True),
    lambda report: report["source_lineage"].__setitem__(
        "invalid_entries", False),
    lambda report: report["tables"].__setitem__(
        "represented_source_tables", False),
    lambda report: report["embedding"].update({
        f"input_token_{suffix}": 999
        for suffix in ("min", "p50", "p95", "p99", "max")
    }),
    lambda report: report["corpus"].update({
        "content_types": {"case_opinion": 1},
        "content_sources": {"table": 1},
        "chapter_counts": {"999": 1},
    }),
])
def test_validate_quality_report_requires_canonical_v2_provenance(mutation):
    document = {"texts": [_source_item("#/texts/0", 1)]}
    record = _record(0, "#/texts/0", 1)
    report = _build([record], document)
    mutation(report)

    with pytest.raises(ValueError):
        quality_core.validate_quality_report(
            report, **_validation_kwargs(), records=[record])


def test_validate_quality_report_binds_recovery_to_actual_record_refs():
    table = _source_item("#/tables/0", 3, label="table", text="")
    record = _record(
        0, "#/tables/0", 3,
        text="| Rule |\n|---|\n| Value |",
        content_type="table", content_source="table")
    record["metadata"]["source_items"][0]["label"] = "table"
    record["metadata"]["table_recovered_from_pdf"] = True
    conversion = {
        "name": ".book.json.conversion.complete.json",
        "sha256": "d" * 64,
        "schema_version": quality_core.CONVERSION_COMPLETION_SCHEMA_VERSION,
    }
    bindings = {
        "docling_json": {
            "name": "book.json", "size": 50, "sha256": "a" * 64},
        "conversion_manifest": conversion,
        "table_recovery": {
            "pdf": {
                "name": "book.pdf", "size": 100,
                "sha256": "e" * 64,
                "capture_policy": "stream-copy-v1",
            },
            "conversion_manifest": conversion,
            "discovery": "explicit",
        },
    }
    report = _build(
        [record], {"tables": [table]},
        recovered_table_refs=["#/tables/0"], input_bindings=bindings)
    assert quality_core.validate_quality_report(
        report, **_validation_kwargs(), recovered_table_count=1,
        recovered_table_refs=["#/tables/0"]) is report

    wrong_ref = copy.deepcopy(report)
    wrong_ref["tables"]["recovered_refs"] = ["#/tables/other"]
    with pytest.raises(ValueError, match="recovered source refs"):
        quality_core.validate_quality_report(
            wrong_ref, **_validation_kwargs(), recovered_table_count=1,
            recovered_table_refs=["#/tables/0"])

    tampered = copy.deepcopy(report)
    tampered["tables"]["recovered_from_pdf"] = 0
    tampered["tables"]["recovered_refs"] = []
    tampered["inputs"]["table_recovery"] = None
    with pytest.raises(
            ValueError, match="recovered source tables|source oracle registry"):
        quality_core.validate_quality_report(
            tampered, **_validation_kwargs(), recovered_table_count=1)


def test_quality_gate_rejects_fabricated_opaque_oracle_against_registry():
    table = _source_item(
        "#/tables/0", 3, label="table",
        text="Authentic source table words")
    authentic = _record(
        0, table["self_ref"], 3,
        text="| Authentic | source | table | words |",
        content_type="table", content_source="table")
    _build([authentic], {"tables": [table]})
    trusted_registry = copy.deepcopy(_LAST_SOURCE_ORACLE_REGISTRY)

    fabricated = copy.deepcopy(authentic)
    fabricated_text = "| Fabricated | unrelated | output |"
    fabricated["text"] = fabricated_text
    entry = fabricated["metadata"]["source_items"][0]
    fabricated_tokens = source_fidelity_core.lexical_tokens(fabricated_text)
    entry.update({
        "oracle_text_sha256": source_fidelity_core.text_sha256(
            fabricated_text),
        "oracle_lexical_sha256": source_fidelity_core.lexical_sha256(
            fabricated_tokens),
        "oracle_lexical_count": len(fabricated_tokens),
    })

    report = _build(
        [fabricated], {"tables": [table]},
        source_oracle_registry=trusted_registry)

    assert report["status"] == "fail"
    assert report["source_lineage"]["fidelity"]["lineage_issues"] == [0]
    with pytest.raises(ValueError, match="did not pass"):
        quality_core.validate_quality_report(
            report, **_validation_kwargs(), records=[fabricated],
            document={"tables": [table]})


def test_quality_gate_rejects_zero_width_opaque_omission():
    table = _source_item(
        "#/tables/0", 3, label="table", text="Critical omitted rule")
    authentic = _record(
        0, table["self_ref"], 3, text="| Critical | omitted | rule |",
        content_type="table", content_source="table")
    _build([authentic], {"tables": [table]})
    trusted_registry = copy.deepcopy(_LAST_SOURCE_ORACLE_REGISTRY)

    omitted = copy.deepcopy(authentic)
    omitted["text"] = "| |"
    entry = omitted["metadata"]["source_items"][0]
    entry.update({
        "oracle_text_sha256": source_fidelity_core.text_sha256("| |"),
        "oracle_lexical_sha256": source_fidelity_core.lexical_sha256(()),
        "oracle_lexical_count": 0,
    })

    report = _build(
        [omitted], {"tables": [table]},
        source_oracle_registry=trusted_registry)

    assert report["status"] == "fail"
    fidelity = report["source_lineage"]["fidelity"]
    assert fidelity["lineage_issues"] == [0]
    assert fidelity["source_coverage_issues"] == [table["self_ref"]]


def test_quality_gate_rejects_opaque_replay_across_records():
    table = _source_item(
        "#/tables/0", 3, label="table", text="Rule Value")
    authentic = _record(
        0, table["self_ref"], 3, text="| Rule | Value |",
        content_type="table", content_source="table")
    _build([authentic], {"tables": [table]})
    trusted_registry = copy.deepcopy(_LAST_SOURCE_ORACLE_REGISTRY)
    replay = copy.deepcopy(authentic)
    replay["metadata"]["chunk_index"] = 1

    report = _build(
        [authentic, replay], {"tables": [table]},
        source_oracle_registry=trusted_registry)

    assert report["status"] == "fail"
    fidelity = report["source_lineage"]["fidelity"]
    assert fidelity["output_coverage_issues"] == [1]
    assert fidelity["covered_output_tokens"] < fidelity["output_tokens"]


def test_page_metadata_and_structural_gate_use_lineage_span_pages():
    item = _source_item(
        "#/texts/0", 1, text="Structural source text")
    record = _record(0, item["self_ref"], 2, text=item["text"])
    descriptor, _ = source_fidelity_core.source_descriptor(item)
    record["metadata"]["source_items"][0]["spans"] = descriptor["spans"]

    report = _build(
        [record], {"texts": [item]}, structural_ranges=[(1, 1)])

    assert report["status"] == "fail"
    assert report["corpus"]["page_metadata_issues"] == [0]
    assert report["corpus"]["structural_leaks"] == [0]


def test_page_metadata_uses_record_local_provenance_scope():
    item = _source_item("#/texts/0", 1, text="alpha beta")
    item["prov"][0]["charspan"] = [0, len("alpha")]
    item["prov"].append({
        "page_no": 2,
        "charspan": [len("alpha "), len("alpha beta")],
        "bbox": {
            "l": 10, "t": 10, "r": 200, "b": 20,
            "coord_origin": "TOPLEFT",
        },
    })
    record = _record(0, item["self_ref"], 1, text="alpha")
    descriptor, _ = source_fidelity_core.source_descriptor(item)
    entry = record["metadata"]["source_items"][0]
    entry["spans"] = descriptor["spans"]
    entry["scope"] = {"provenance_indexes": [0]}

    report = _build([record], {"texts": [item]})

    assert report["corpus"]["page_metadata_issues"] == []


def test_validate_quality_report_requires_exact_schema_v1_check_set():
    document = {"texts": [_source_item("#/texts/0", 1)]}
    report = _build([_record(0, "#/texts/0", 1)], document)
    kwargs = _validation_kwargs()

    missing = copy.deepcopy(report)
    missing["checks"].pop()
    with pytest.raises(ValueError, match="check set"):
        quality_core.validate_quality_report(missing, **kwargs)

    unknown = copy.deepcopy(report)
    unknown["checks"].append({
        "name": "unrecognized_gate",
        "status": "pass",
        "observed": 0,
        "required": 0,
    })
    with pytest.raises(ValueError, match="check set"):
        quality_core.validate_quality_report(unknown, **kwargs)

    downgraded = copy.deepcopy(report)
    hard_check = next(check for check in downgraded["checks"]
                      if check["name"] == "page_regressions")
    hard_check["status"] = "warn"
    with pytest.raises(ValueError, match="failed check"):
        quality_core.validate_quality_report(downgraded, **kwargs)

    invalid_warning = copy.deepcopy(report)
    warning_check = next(check for check in invalid_warning["checks"]
                         if check["name"] == "canonical_text_duplicates")
    warning_check["status"] = "warn"
    with pytest.raises(ValueError, match="warning check"):
        quality_core.validate_quality_report(invalid_warning, **kwargs)

    invalid_semantics = copy.deepcopy(report)
    hard_check = next(check for check in invalid_semantics["checks"]
                      if check["name"] == "page_regressions")
    hard_check["observed"] = 1
    with pytest.raises(ValueError, match="semantics"):
        quality_core.validate_quality_report(invalid_semantics, **kwargs)


def test_validate_quality_report_rejects_boolean_schema_versions():
    document = {"texts": [_source_item("#/texts/0", 1)]}
    report = _build([_record(0, "#/texts/0", 1)], document)
    kwargs = _validation_kwargs()

    top_level = copy.deepcopy(report)
    top_level["schema_version"] = True
    with pytest.raises(ValueError, match="unsupported"):
        quality_core.validate_quality_report(top_level, **kwargs)

    lineage = copy.deepcopy(report)
    lineage["source_lineage"]["schema_version"] = True
    with pytest.raises(ValueError, match="lineage gate"):
        quality_core.validate_quality_report(lineage, **kwargs)


def test_validate_quality_report_rejects_stale_heading_schema_stack():
    document = {"texts": [_source_item("#/texts/0", 1)]}
    records = [_record(0, "#/texts/0", 1)]
    report = _build(records, document)
    kwargs = _validation_kwargs(record_count=1)
    kwargs.update(records=records, document=document)

    stale_report = copy.deepcopy(report)
    stale_report["schema_version"] = 8
    with pytest.raises(ValueError, match="unsupported"):
        quality_core.validate_quality_report(stale_report, **kwargs)

    stale_analysis = copy.deepcopy(report)
    stale_analysis["source_analysis"]["schema_version"] = 2
    with pytest.raises(ValueError, match="source analysis"):
        quality_core.validate_quality_report(stale_analysis, **kwargs)

    stale_headings = copy.deepcopy(report)
    stale_headings["source_analysis"]["section_headings"][
        "schema_version"] = 2
    with pytest.raises(ValueError, match="source analysis"):
        quality_core.validate_quality_report(stale_headings, **kwargs)


def test_report_byte_parser_rejects_duplicate_keys_and_nonfinite_numbers():
    document = {"texts": [_source_item("#/texts/0", 1)]}
    report = _build([_record(0, "#/texts/0", 1)], document)
    kwargs = {
        "chunks_name": "book_chunks.jsonl",
        "chunks_sha256": "b" * 64,
        "chunks_size": 123,
        "record_count": 1,
        "stable_ids": ["chunk_0"],
        "chunk_hashes": ["0000000000000001"],
    }
    raw = json.dumps(report, sort_keys=True).encode("utf-8")

    with pytest.raises(ValueError, match="duplicate"):
        quality_core.parse_quality_report_bytes(
            b'{"status":"pass",' + raw[1:], **kwargs)
    with pytest.raises(ValueError, match="numeric constant"):
        quality_core.parse_quality_report_bytes(
            raw[:-1] + b',"not_finite":NaN}', **kwargs)


def test_quality_report_file_reader_uses_bounded_read(monkeypatch):
    requested_sizes = []

    class GuardedFile:
        def __enter__(self):
            return self

        def __exit__(self, *_args):
            return False

        def read(self, size=-1):
            requested_sizes.append(size)
            return b"{}"

    monkeypatch.setattr(
        Path, "open", lambda *_args, **_kwargs: GuardedFile())
    monkeypatch.setattr(
        quality_core, "parse_quality_report_bytes",
        lambda raw, **_kwargs: raw)

    result = quality_core.read_quality_report(
        Path("report.json"),
        chunks_name="chunks.jsonl",
        chunks_sha256="a" * 64,
        chunks_size=1,
        record_count=1,
        stable_ids=["stable"],
        chunk_hashes=["b" * 16],
    )

    assert result == b"{}"
    assert requested_sizes == [quality_core.MAX_QUALITY_REPORT_BYTES + 1]


def test_quality_report_rejects_nonstring_case_name_summary():
    document = {"texts": [_source_item("#/texts/0", 1)]}
    record = _record(0, "#/texts/0", 1)
    report = _build([record], document)
    report["entities"]["mentions"] = 1
    invalid_record = copy.deepcopy(record)
    invalid_record["metadata"]["case_names"] = [7]

    with pytest.raises(ValueError, match="case-name summary"):
        quality_core.validate_quality_report(
            report, **_validation_kwargs(), records=[invalid_record])
