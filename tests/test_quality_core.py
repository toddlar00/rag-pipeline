import copy
import json
from pathlib import Path

import pytest

import quality_core


def _source_item(ref: str, page: int, *, label: str = "text",
                 text: str = "Source text") -> dict:
    return {
        "self_ref": ref,
        "label": label,
        "content_layer": "body",
        "text": text,
        "prov": [{"page_no": page}],
    }


def _record(index: int, ref: str, page: int, *, text: str = "Source text",
            content_type: str = "author_narrative",
            content_source: str = "body") -> dict:
    metadata = {
        "chunk_index": index,
        "source_lineage_schema_version": 1,
        "source_items": [{
            "ref": ref,
            "label": "table" if content_source == "table" else "text",
            "parent_refs": [],
            "spans": [{"page": page}],
        }],
        "page_start": page,
        "page_end": page,
        "chapter_num": 1,
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
        },
    }
    values.update(overrides)
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
    }


def test_quality_report_path_is_canonical_and_adjacent(tmp_path):
    chunks = tmp_path / "book_chunks.jsonl"
    assert quality_core.quality_report_path(chunks) == (
        tmp_path / "book_chunks.quality.json")


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
        ]
    }

    all_items, eligible, exclusions, issues = quality_core.source_inventory(
        document, structural_ranges=[(8, 10)])

    assert len(all_items) == 6
    assert set(eligible) == {"#/texts/0"}
    assert exclusions == {
        "editorial_boilerplate": 1,
        "furniture": 1,
        "label:section_header": 1,
        "no_provenance": 1,
        "structural_range": 1,
    }
    assert issues == {"invalid_provenance": ["texts[4].prov"]}


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
            "page": 2,
            "bbox": [1.235, 8.765, 9.0, 2.0],
            "origin": "BOTTOMLEFT",
        }],
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
    ("spans", [{"page": 3}]),
    ("parent_refs", ["#/texts/999"]),
))
def test_lineage_fields_must_match_source_inventory(field, replacement):
    parent = _source_item(
        "#/texts/0", 1, label="section_header", text="Heading")
    parent["children"] = [{"cref": "#/texts/1"}]
    child = _source_item("#/texts/1", 2)
    record = _record(0, "#/texts/1", 2)
    record["metadata"]["source_items"][0]["parent_refs"] = ["#/texts/0"]

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
        _source_item("#/texts/0", 1),
        _source_item("#/texts/1", 2),
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

    bad = copy.deepcopy(good)
    bad["text"] = "flattened table prose"
    assert _build([bad], {"tables": [table]})["status"] == "fail"


def test_page_regression_is_a_hard_failure():
    document = {"texts": [
        _source_item("#/texts/0", 2),
        _source_item("#/texts/1", 1),
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
        _source_item("#/texts/0", 3),
        _source_item("#/texts/1", 2),
    ]}
    rule = _record(
        0, "#/texts/0", 3, text="Rule content",
        content_type="statutory_excerpt")
    explanation = _record(
        1, "#/texts/1", 2, text="Author explanation",
        content_type="author_narrative")
    for record in (rule, explanation):
        record["metadata"]["section_path"] = "Chapter 1 > Conflicts"
    rule["metadata"]["headings"] = ["Rule language**"]
    explanation["metadata"]["headings"] = ["Authors' explanation***"]

    report = _build([rule, explanation], document)

    assert report["status"] == "pass"
    assert report["corpus"]["page_regressions"] == [1]
    assert report["corpus"]["unexpected_page_regressions"] == []
    assert report["corpus"]["allowed_page_regressions"] == [{
        "chunk_index": 1,
        "previous_chunk_index": 0,
        "previous_page_start": 3,
        "page_start": 2,
        "section_path": "Chapter 1 > Conflicts",
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
        report, **_validation_kwargs(record_count=2))


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
        "schema_version": 2,
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
    with pytest.raises(ValueError, match="recovered source tables"):
        quality_core.validate_quality_report(
            tampered, **_validation_kwargs(), recovered_table_count=1)


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
