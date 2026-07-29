import copy

import heading_lineage


def _item(ref, label, text, *, page=1, top=600, level=None):
    item = {
        "self_ref": ref,
        "label": label,
        "content_layer": "body",
        "text": text,
        "children": [],
        "prov": [{
            "page_no": page,
            "bbox": {
                "l": 70, "r": 430, "t": top, "b": top - 15,
                "coord_origin": "BOTTOMLEFT",
            },
        }],
    }
    if level is not None:
        item["level"] = level
    return item


def _document(items):
    return {
        "texts": items,
        "tables": [],
        "pictures": [],
        "groups": [],
        "body": {"children": [
            {"cref": item["self_ref"]} for item in items
        ]},
        "pages": {
            str(page): {"size": {"width": 500, "height": 800}}
            for page in {
                span["page_no"] for item in items
                for span in item.get("prov") or []
            }
        },
    }


def _record(refs, path, *, headings=None, content_source="body"):
    return {
        "text": "Body passage",
        "metadata": {
            "section_path": path,
            "headings": list(headings or []),
            "content_source": content_source,
            "source_items": [
                {"ref": ref, "label": "text", "spans": []}
                for ref in refs
            ],
        },
    }


def test_duplicate_display_text_keeps_distinct_occurrence_identity():
    document = _document([
        _item("#/texts/0", "section_header", "Notes & Questions", top=700),
        _item("#/texts/1", "text", "First body", top=650),
        _item("#/texts/2", "section_header", "Notes & Questions", top=500),
        _item("#/texts/3", "text", "Second body", top=450),
    ])
    records = [
        _record(["#/texts/1"], "Notes & Questions"),
        _record(["#/texts/3"], "Notes & Questions"),
    ]

    expected = heading_lineage.attach_heading_bindings(document, records)

    assert expected["expected_paths"] == [
        ["#/texts/0"], ["#/texts/2"]]
    assert expected["expected_direct"] == [
        ["#/texts/0"], ["#/texts/2"]]
    assert expected["missing_refs"] == []

    forged = copy.deepcopy(records)
    forged[1]["metadata"][heading_lineage.HEADING_PATH_FIELD] = [
        "#/texts/0"]
    audit = heading_lineage.audit_heading_bindings(document, forged)
    assert audit["invalid_binding_records"] == [1]


def test_running_header_is_an_exact_exception_not_a_text_match():
    document = _document([
        _item("#/texts/99", "page_header", "1", top=780),
        _item("#/texts/0", "section_header", "Repeated Heading", top=780),
        _item("#/texts/1", "section_header", "Repeated Heading", top=600),
        _item("#/texts/2", "text", "Body", top=550),
    ])
    records = [_record(["#/texts/2"], "Repeated Heading")]

    expected = heading_lineage.attach_heading_bindings(document, records)

    assert expected["exception_refs"] == {
        heading_lineage.RUNNING_FURNITURE_REASON: ["#/texts/0"],
        heading_lineage.EMBEDDED_OUTLINE_REASON: [],
    }
    assert expected["attachable_refs"] == ["#/texts/1"]
    assert expected["expected_direct"] == [["#/texts/1"]]


def test_page_number_inherits_only_same_row_running_header_proof():
    role = _item(
        "#/texts/0", "section_header", "2. The Role of Overrides",
        page=1, top=700)
    first_body = _item("#/texts/1", "text", "First body", page=1, top=650)
    copyright_two = _item(
        "#/texts/2", "page_header", "Copyright", page=2, top=790)
    page_number = _item(
        "#/texts/3", "section_header", "142", page=2, top=760)
    page_number["prov"][0]["bbox"].update(l=115, r=135)
    running_two = _item(
        "#/texts/4", "section_header",
        "2 · Sample Systems: Configuration Review", page=2, top=760)
    running_two["prov"][0]["bbox"].update(l=195, r=400)
    second_body = _item("#/texts/5", "text", "Second body", page=2, top=700)
    copyright_three = _item(
        "#/texts/6", "page_header", "Copyright", page=3, top=790)
    running_three = _item(
        "#/texts/7", "section_header",
        "2 · Sample Systems: Configuration Review", page=3, top=760)
    running_three["prov"][0]["bbox"].update(l=195, r=400)
    document = _document([
        role, first_body, copyright_two, page_number, running_two,
        second_body, copyright_three, running_three,
    ])
    record = _record(["#/texts/5"], "2. The Role of Overrides")

    expected = heading_lineage.attach_heading_bindings(document, [record])

    assert expected["exception_refs"][
        heading_lineage.RUNNING_FURNITURE_REASON] == [
            "#/texts/3", "#/texts/4", "#/texts/7"]
    assert expected["source_scope_paths"] == [["#/texts/0"]]
    assert expected["expected_direct"] == [["#/texts/0"]]


def test_unique_top_margin_heading_is_not_running_furniture():
    document = _document([
        _item("#/texts/0", "section_header", "One-off Topic", top=780),
        _item("#/texts/1", "text", "Body", top=650),
    ])
    records = [_record(["#/texts/1"], "One-off Topic")]

    expected = heading_lineage.attach_heading_bindings(document, records)

    assert expected["exception_refs"][
        heading_lineage.RUNNING_FURNITURE_REASON] == []
    assert expected["attachable_refs"] == ["#/texts/0"]
    assert expected["expected_direct"] == [["#/texts/0"]]


def test_forged_path_cannot_delete_ancestor_or_substitute_body_text():
    document = _document([
        _item("#/texts/0", "section_header", "Chapter 1", top=700),
        _item("#/texts/1", "section_header", "§ 1.01 Introduction", top=650),
        _item("#/texts/2", "text", "Ordinary source label", top=625),
        _item("#/texts/3", "text", "Body", top=600),
    ])
    record = _record(
        ["#/texts/3"], "Chapter 1 > § 1.01 Introduction",
        headings=["§ 1.01 Introduction"])
    heading_lineage.attach_heading_bindings(document, [record])

    missing_ancestor = copy.deepcopy(record)
    missing_ancestor["metadata"]["section_path"] = "§ 1.01 Introduction"
    expected = heading_lineage.expected_heading_bindings(
        document, [missing_ancestor])
    assert expected["display_binding_records"] == [0]

    substituted = copy.deepcopy(record)
    substituted["metadata"]["section_path"] = (
        "Chapter 1 > Ordinary source label")
    expected = heading_lineage.expected_heading_bindings(
        document, [substituted])
    assert expected["display_binding_records"] == [0]


def test_geometry_rolls_back_only_a_same_page_future_heading():
    document = _document([
        _item("#/texts/0", "section_header", "Chapter 1", top=700),
        _item("#/texts/1", "section_header", "Panel", top=680),
        _item("#/texts/2", "section_header", "A. Later", top=250),
        # Serialized after A, but physically above it.
        _item("#/texts/3", "text", "Parallel label", top=600),
    ])
    records = [_record(["#/texts/3"], "Chapter 1 > Panel")]

    expected = heading_lineage.attach_heading_bindings(document, records)

    assert expected["source_scope_paths"] == [["#/texts/0", "#/texts/1"]]
    assert expected["display_binding_records"] == []

    ordinary_order = copy.deepcopy(document)
    ordinary_order["texts"][3]["prov"][0]["bbox"].update(t=200, b=185)
    expected = heading_lineage.expected_heading_bindings(
        ordinary_order,
        [_record(["#/texts/3"], "Chapter 1 > Panel > A. Later")],
    )
    assert expected["source_scope_paths"] == [[
        "#/texts/0", "#/texts/1", "#/texts/2"]]


def test_composite_member_alias_is_bound_to_the_same_exact_occurrence():
    document = _document([
        _item(
            "#/texts/0", "section_header",
            "Chapter 5 Layered Configuration", top=700),
        _item("#/texts/1", "section_header", "(Fallback Rules)", top=660),
        _item("#/texts/2", "text", "Body", top=620),
    ])
    records = [_record(
        ["#/texts/2"],
        "Chapter 5 Layered Configuration (Fallback Rules) > (Fallback Rules)",
    )]

    expected = heading_lineage.attach_heading_bindings(document, records)

    assert expected["display_binding_records"] == []
    assert expected["expected_components"][0][0]["occurrence_ids"] == [
        "#/texts/0", "#/texts/1"]
    assert expected["expected_components"][0][1]["occurrence_ids"] == [
        "#/texts/1"]


def test_running_occurrence_can_resume_an_exact_ancestor_only():
    document = _document([
        _item("#/texts/0", "section_header", "Chapter 1", top=700),
        _item("#/texts/1", "section_header", "§ 1.01 Main", top=650),
        _item("#/texts/2", "section_header", "Notes & Questions", top=600),
        _item("#/texts/3", "text", "Notes body", top=550),
        _item("#/texts/99", "page_header", "12", page=2, top=780),
        _item(
            "#/texts/4", "section_header", "§ 1.01 MAIN 12",
            page=2, top=780),
        _item("#/texts/5", "text", "Resumed body", page=2, top=650),
    ])
    records = [
        _record(
            ["#/texts/3"],
            "Chapter 1 > § 1.01 Main > Notes & Questions"),
        _record(["#/texts/5"], "Chapter 1 > § 1.01 Main"),
    ]

    expected = heading_lineage.attach_heading_bindings(document, records)

    assert expected["display_binding_records"] == []
    assert expected["ancestor_resumptions"] == {1: [{
        "kind": "running_ancestor_resumption",
        "running_ref": "#/texts/4",
        "ancestor_ref": "#/texts/1",
        "superseded_refs": ["#/texts/2"],
    }]}

    not_running = copy.deepcopy(document)
    next(item for item in not_running["texts"]
         if item["self_ref"] == "#/texts/4")["prov"][0]["bbox"].update(
             t=650, b=635)
    expected = heading_lineage.expected_heading_bindings(
        not_running, records)
    assert expected["ancestor_resumptions"] == {}
    assert "#/texts/4" in expected["attachable_refs"]


def test_nearest_running_occurrence_wins_independent_of_set_iteration(
        monkeypatch):
    document = _document([
        _item("#/texts/0", "section_header", "Chapter 1", top=700),
        _item("#/texts/1", "section_header", "§ 1.01 Main", top=650),
        _item("#/texts/2", "section_header", "Notes & Questions", top=600),
        _item("#/texts/3", "text", "Notes body", top=550),
        _item("#/texts/90", "page_header", "12", page=2, top=780),
        _item(
            "#/texts/4", "section_header", "§ 1.01 MAIN 12",
            page=2, top=780),
        _item("#/texts/99", "page_header", "13", page=3, top=780),
        _item(
            "#/texts/6", "section_header", "§ 1.01 MAIN 13",
            page=3, top=780),
        _item("#/texts/5", "text", "Resumed body", page=3, top=650),
    ])
    records = [
        _record(
            ["#/texts/3"],
            "Chapter 1 > § 1.01 Main > Notes & Questions"),
        _record(["#/texts/5"], "Chapter 1 > § 1.01 Main"),
    ]

    class EarlierFirstRunningRefs:
        def __iter__(self):
            return iter(("#/texts/4", "#/texts/6"))

        def __contains__(self, value):
            return value in {"#/texts/4", "#/texts/6"}

        def __sub__(self, _other):
            return self

        def __or__(self, other):
            return set(self) | set(other)

    monkeypatch.setattr(
        heading_lineage, "running_section_heading_refs",
        lambda _document: EarlierFirstRunningRefs())

    expected = heading_lineage.attach_heading_bindings(document, records)

    assert expected["ancestor_resumptions"] == {1: [{
        "kind": "running_ancestor_resumption",
        "running_ref": "#/texts/6",
        "ancestor_ref": "#/texts/1",
        "superseded_refs": ["#/texts/2"],
    }]}


def test_embedded_outline_exception_is_proved_from_source_order():
    document = _document([
        _item("#/texts/0", "section_header", "Summary of Contents", top=700),
        _item("#/texts/1", "list_item", "First entry", top=670),
        _item("#/texts/2", "section_header", "Second entry", top=640),
        _item("#/texts/3", "list_item", "Third entry", top=610),
        _item("#/texts/4", "section_header", "Substantive Topic", top=580),
        _item("#/texts/5", "text", "Body", top=550),
    ])
    records = [
        _record(["#/texts/1", "#/texts/3"], "Summary of Contents"),
        _record(["#/texts/5"], "Substantive Topic"),
    ]

    expected = heading_lineage.attach_heading_bindings(document, records)

    assert expected["exception_refs"][
        heading_lineage.EMBEDDED_OUTLINE_REASON] == ["#/texts/2"]
    assert "#/texts/4" in expected["attachable_refs"]
    assert expected["missing_refs"] == []


def test_forged_summary_metadata_cannot_create_outline_exception():
    document = _document([
        _item("#/texts/0", "section_header", "Summary of Contents", top=700),
        _item("#/texts/1", "list_item", "First entry", top=670),
        _item("#/texts/2", "list_item", "Second entry", top=640),
        _item("#/texts/3", "section_header", "Substantive Topic", top=610),
        _item("#/texts/4", "text", "Body", top=580),
        _item("#/texts/5", "section_header", "Arbitrary Heading", top=520),
        _item("#/texts/6", "text", "Later body", top=490),
    ])
    forged = _record(["#/texts/5"], "Summary of Contents")

    expected = heading_lineage.expected_heading_bindings(
        document, [forged])

    assert "#/texts/5" not in {
        ref for refs in expected["exception_refs"].values() for ref in refs
    }
    assert "#/texts/5" in expected["attachable_refs"]


def test_one_occurrence_cannot_satisfy_duplicate_path_components():
    document = _document([
        _item("#/texts/0", "section_header", "Notes & Questions", top=700),
        _item("#/texts/1", "text", "Body", top=650),
    ])
    record = _record(
        ["#/texts/1"], "Notes & Questions > Notes & Questions")

    expected = heading_lineage.attach_heading_bindings(document, [record])
    audit = heading_lineage.audit_heading_bindings(document, [record])

    assert expected["expected_components"][0][0]["occurrence_ids"] == [
        "#/texts/0"]
    assert expected["expected_components"][0][1]["binding"] == "unbound"
    assert audit["display_binding_records"] == [0]


def test_arbitrary_ordinary_annotation_cannot_enter_or_change_lineage():
    document = _document([
        _item(
            "#/texts/0", "section_header",
            "Chapter 1 Foundations", top=750),
        _item("#/texts/1", "text", "Fake Layer", top=710),
        _item("#/texts/2", "section_header", "Parent Topic", top=670),
        _item("#/texts/3", "text", "First body", top=630),
        _item("#/texts/4", "section_header", "Child Topic", top=590),
        _item("#/texts/5", "text", "Second body", top=550),
    ])
    records = [
        _record(["#/texts/1"], "Chapter 1 Foundations"),
        _record(
            ["#/texts/3"],
            "Chapter 1 Foundations > Fake Layer > Parent Topic"),
        _record(
            ["#/texts/5"],
            "Chapter 1 Foundations > Parent Topic > Child Topic"),
    ]

    expected = heading_lineage.attach_heading_bindings(document, records)
    audit = heading_lineage.audit_heading_bindings(document, records)

    assert expected["expected_components"][1][1] == {
        "display": "Fake Layer",
        "occurrence_ids": [],
        "binding": "unbound",
    }
    assert expected["expected_paths"][1] == ["#/texts/0", "#/texts/2"]
    assert expected["source_scope_paths"][2] == [
        "#/texts/0", "#/texts/2", "#/texts/4"]
    assert "#/texts/1" not in records[1]["metadata"][
        heading_lineage.HEADING_PATH_FIELD]
    assert audit["display_binding_records"] == [1]
    assert audit["invalid_binding_records"] == []


def test_promoted_case_caption_is_a_bounded_non_heading_annotation():
    document = _document([
        _item(
            "#/texts/0", "section_header",
            "Chapter 1 Foundations", top=750),
        _item("#/texts/1", "text", "Smith v. Jones", top=700),
        _item("#/texts/2", "text", "First opinion passage", top=650),
        _item("#/texts/3", "text", "Second opinion passage", top=600),
    ])
    anchor = _record(
        ["#/texts/1", "#/texts/2"],
        "Chapter 1 Foundations > Smith v. Jones")
    anchor["metadata"].update({
        "content_type": "case_opinion",
        "primary_case": "Smith v. Jones",
    })
    continuation = _record(
        ["#/texts/3"], "Chapter 1 Foundations > Smith v. Jones")
    continuation["metadata"]["content_type"] = "case_opinion"

    expected = heading_lineage.attach_heading_bindings(
        document, [anchor, continuation])
    audit = heading_lineage.audit_heading_bindings(
        document, [anchor, continuation])

    assert expected["expected_components"][0][1] == {
        "display": "Smith v. Jones",
        "occurrence_ids": ["#/texts/1"],
        "binding": "source_item",
    }
    assert expected["expected_components"][1][1]["occurrence_ids"] == [
        "#/texts/1"]
    assert expected["expected_paths"] == [
        ["#/texts/0"], ["#/texts/0"]]
    assert audit["display_binding_records"] == []


def test_cross_page_list_sequence_cannot_forge_outline_exception():
    document = _document([
        _item(
            "#/texts/0", "section_header", "Summary of Contents",
            page=1, top=700),
        _item("#/texts/1", "list_item", "First", page=1, top=650),
        _item("#/texts/2", "list_item", "Second", page=1, top=600),
        _item(
            "#/texts/3", "section_header", "Substantive Topic",
            page=4, top=650),
        _item("#/texts/4", "list_item", "A later list", page=5, top=600),
        _item(
            "#/texts/5", "section_header", "Final Topic",
            page=6, top=650),
        _item("#/texts/6", "text", "Body", page=6, top=600),
    ])

    expected = heading_lineage.expected_heading_bindings(document, [])

    assert "#/texts/3" not in expected["exception_refs"][
        heading_lineage.EMBEDDED_OUTLINE_REASON]
    assert "#/texts/3" in expected["attachable_refs"]


def test_repeated_layout_without_text_or_page_header_is_not_furniture():
    document = _document([
        _item(
            "#/texts/0", "section_header", "First Substantive Topic",
            page=1, top=780),
        _item("#/texts/1", "text", "First body", page=1, top=650),
        _item(
            "#/texts/2", "section_header", "Second Substantive Topic",
            page=2, top=780),
        _item("#/texts/3", "text", "Second body", page=2, top=650),
        _item(
            "#/texts/4", "section_header", "Third Substantive Topic",
            page=3, top=780),
        _item("#/texts/5", "text", "Third body", page=3, top=650),
    ])
    records = [
        _record(["#/texts/1"], "First Substantive Topic"),
        _record(["#/texts/3"], "Second Substantive Topic"),
        _record(["#/texts/5"], "Third Substantive Topic"),
    ]

    expected = heading_lineage.attach_heading_bindings(document, records)

    assert expected["exception_refs"][
        heading_lineage.RUNNING_FURNITURE_REASON] == []
    assert expected["attachable_refs"] == [
        "#/texts/0", "#/texts/2", "#/texts/4"]


def test_repeated_sparse_page_top_ocr_is_a_typed_nonattachable_artifact():
    document = _document([
        _item(
            "#/texts/0", "section_header", "A     IE  7ES",
            page=1, top=780),
        _item(
            "#/texts/1", "section_header", "L     IE  ES",
            page=2, top=780),
        _item(
            "#/texts/2", "section_header", "IE  ES",
            page=3, top=780),
    ])

    refs = heading_lineage.sparse_ocr_heading_artifact_refs(document)
    expected = heading_lineage.expected_heading_bindings(
        document, [], excluded_heading_refs=refs)

    assert refs == frozenset({
        "#/texts/0", "#/texts/1", "#/texts/2"})
    assert expected["source_refs"] == [
        "#/texts/0", "#/texts/1", "#/texts/2"]
    assert expected["attachable_refs"] == []
    assert expected["exception_refs"] == {
        heading_lineage.RUNNING_FURNITURE_REASON: [],
        heading_lineage.EMBEDDED_OUTLINE_REASON: [],
    }
    assert expected["artifact_refs"] == {
        heading_lineage.SPARSE_OCR_HEADING_ARTIFACT_REASON: [
            "#/texts/0", "#/texts/1", "#/texts/2"],
    }


def test_sparse_ocr_artifact_proof_rejects_adversarial_near_misses():
    values = [
        _item("#/texts/0", "section_header", "PART   I", page=1, top=780),
        _item("#/texts/1", "section_header", "RULE   10", page=2, top=780),
        _item(
            "#/texts/2", "section_header", "A.   SAMPLE RULE",
            page=3, top=780),
        # Sparse-looking but unique geometry/text is not repeated proof.
        _item("#/texts/3", "section_header", "A   IE  7ES", page=4, top=780),
        # Even a matching-looking fragment is ineligible without geometry.
        _item("#/texts/4", "section_header", "L   IE  ES", page=5, top=780),
    ]
    values[-1]["prov"] = []
    document = _document(values)

    assert heading_lineage.sparse_ocr_heading_artifact_refs(
        document) == frozenset()


def test_heading_audit_rejects_stale_v2_binding_evidence():
    document = _document([
        _item("#/texts/0", "section_header", "Chapter 1", top=700),
        _item("#/texts/1", "text", "Body", top=650),
    ])
    record = _record(["#/texts/1"], "Chapter 1")
    heading_lineage.attach_heading_bindings(document, [record])
    record["metadata"][heading_lineage.HEADING_SCHEMA_FIELD] = 2

    audit = heading_lineage.audit_heading_bindings(document, [record])

    assert audit["invalid_binding_records"] == [0]


def test_future_heading_rollback_repeats_until_all_retained_are_prior():
    document = _document([
        _item("#/texts/0", "section_header", "Chapter 1", top=700),
        _item("#/texts/1", "section_header", "A. Future Parent", top=300),
        _item("#/texts/2", "section_header", "1. Future Child", top=250),
        _item("#/texts/3", "text", "Physically earlier body", top=600),
        _item("#/texts/4", "text", "Later body", top=200),
    ])
    # Keep the serialized source order so this exercises occurrence rollback,
    # not the separate bounded page-wrap repair.
    document["pages"] = {}
    records = [
        _record(["#/texts/3"], "Chapter 1"),
        _record(
            ["#/texts/4"],
            "Chapter 1 > A. Future Parent > 1. Future Child"),
    ]

    expected = heading_lineage.attach_heading_bindings(document, records)
    audit = heading_lineage.audit_heading_bindings(document, records)

    assert expected["source_scope_paths"][0] == ["#/texts/0"]
    assert audit["display_binding_records"] == []
    assert audit["missing_refs"] == []
    assert audit["missing_direct_refs"] == []


def test_table_occurrence_rejects_future_heading_and_children_do_not_own():
    texts = [
        _item(
            "#/texts/0", "section_header",
            "Chapter 1 Foundations", top=750),
        _item("#/texts/1", "section_header", "§ 1.01 Main", top=700),
        _item("#/texts/2", "section_header", "A. Later", top=600),
        _item("#/texts/3", "text", "Later body", top=550),
    ]
    table = _item("#/tables/0", "table", "", top=650)
    document = _document(texts)
    document["tables"] = [table]
    document["body"]["children"] = [
        {"cref": "#/texts/0"},
        {"cref": "#/texts/1"},
        {"cref": "#/tables/0"},
        {"cref": "#/texts/2"},
        {"cref": "#/texts/3"},
    ]
    forged = _record(
        ["#/tables/0", "#/texts/3"],
        "Chapter 1 Foundations > § 1.01 Main > A. Later",
        content_source="table",
    )

    expected = heading_lineage.attach_heading_bindings(document, [forged])
    assert expected["source_scope_paths"] == [[
        "#/texts/0", "#/texts/1"]]
    assert expected["display_binding_records"] == [0]

    physically_prior = copy.deepcopy(document)
    next(item for item in physically_prior["texts"]
         if item["self_ref"] == "#/texts/2")["prov"][0]["bbox"].update(
             t=675, b=660)
    geometric_table = _record(
        ["#/tables/0"],
        "Chapter 1 Foundations > § 1.01 Main > A. Later",
        content_source="table",
    )
    expected = heading_lineage.attach_heading_bindings(
        physically_prior, [geometric_table])
    assert expected["source_scope_paths"] == [[
        "#/texts/0", "#/texts/1", "#/texts/2"]]
    assert expected["display_binding_records"] == []

    parent = _record(
        ["#/tables/0"], "Chapter 1 Foundations > § 1.01 Main",
        content_source="table")
    child = copy.deepcopy(parent)
    child["metadata"]["retrieval_role"] = "table_child"
    expected = heading_lineage.attach_heading_bindings(
        document, [parent, child])
    assert expected["expected_paths"] == [
        ["#/texts/0", "#/texts/1"],
        ["#/texts/0", "#/texts/1"],
    ]
    assert expected["expected_direct"] == [
        ["#/texts/0", "#/texts/1"], []]


def test_ancestor_picture_container_does_not_compete_with_included_child():
    chapter = _item(
        "#/texts/0", "section_header", "Chapter 4", top=750, level=1)
    problem = _item(
        "#/texts/1", "section_header", "PROBLEMS 3-5", top=650, level=2)
    body = _item("#/texts/2", "text", "For these sample problems", top=600)
    picture = _item("#/pictures/0", "picture", "", top=680)
    picture["children"] = [
        {"cref": "#/texts/1"}, {"cref": "#/texts/2"}]
    problem["parent"] = {"cref": "#/pictures/0"}
    body["parent"] = {"cref": "#/pictures/0"}
    document = _document([chapter, problem, body])
    document["pictures"] = [picture]
    document["body"]["children"] = [
        {"cref": "#/texts/0"}, {"cref": "#/pictures/0"}]
    record = _record(
        ["#/texts/2", "#/pictures/0"],
        "Chapter 4 > PROBLEMS 3-5")

    expected = heading_lineage.attach_heading_bindings(document, [record])

    assert expected["source_scope_paths"] == [[
        "#/texts/0", "#/texts/1"]]
    assert expected["scope_conflict_records"] == []
    assert expected["display_binding_records"] == []

    unrelated = copy.deepcopy(document)
    other_picture = _item("#/pictures/1", "picture", "", top=700)
    unrelated["pictures"].append(other_picture)
    unrelated["body"]["children"].insert(
        1, {"cref": "#/pictures/1"})
    unrelated_record = _record(
        ["#/texts/2", "#/pictures/1"],
        "Chapter 4 > PROBLEMS 3-5")
    expected = heading_lineage.expected_heading_bindings(
        unrelated, [unrelated_record])
    assert expected["scope_conflict_records"] == [0]
    assert expected["display_binding_records"] == [0]

    figure_record = _record(
        ["#/texts/2", "#/pictures/0"],
        "Chapter 4 > PROBLEMS 3-5", content_source="figure")
    figure_record["metadata"]["content_type"] = "figure"
    expected = heading_lineage.expected_heading_bindings(
        document, [figure_record])
    assert expected["scope_conflict_records"] == [0]
    assert expected["display_binding_records"] == [0]

    physically_future = copy.deepcopy(document)
    next(item for item in physically_future["texts"]
         if item["self_ref"] == "#/texts/2")["prov"][0]["bbox"].update(
             t=700, b=685)
    future_record = _record(
        ["#/texts/2", "#/pictures/0"],
        "Chapter 4 > PROBLEMS 3-5")
    expected = heading_lineage.expected_heading_bindings(
        physically_future, [future_record])
    assert expected["source_scope_paths"] == [["#/texts/0"]]
    assert expected["scope_conflict_records"] == []
    assert expected["display_binding_records"] == [0]
