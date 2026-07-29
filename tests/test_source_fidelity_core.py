import copy
import hashlib

import pytest

import source_fidelity_core as fidelity


def _item(ref, text, *, page=1, top=10, left=10, right=200,
          label="text", marker=None):
    item = {
        "self_ref": ref,
        "label": label,
        "text": text,
        "prov": [{
            "page_no": page,
            "charspan": [0, len(text)],
            "bbox": {
                "l": left, "t": top, "r": right, "b": top + 10,
                "coord_origin": "TOPLEFT",
            },
        }],
    }
    if marker is not None:
        item.update(enumerated=True, marker=marker)
        item["prov"][0]["charspan"] = [
            0, len(f"{marker} {text}")]
    return item


def _lineage(item, *, transform=None, oracle_text=None):
    descriptor, issues = fidelity.source_descriptor(item)
    assert issues == []
    transform = transform or fidelity.default_transform(item["label"])
    if oracle_text is None:
        oracle_text = (
            fidelity.list_item_oracle_text(item)
            if transform == "list" else fidelity.source_item_text(item))
    oracle_tokens = fidelity.lexical_tokens(oracle_text)
    recovery = None
    if transform in fidelity.OPAQUE_TRANSFORMS:
        recovery = fidelity.recovery_binding_sha256(
            ref=item["self_ref"], transform=transform,
            source_text_sha256=descriptor["source_text_sha256"],
            oracle_text_sha256=fidelity.text_sha256(oracle_text),
            source_binding=None)
    return {
        "ref": item["self_ref"],
        "label": item["label"],
        "parent_refs": [],
        "spans": descriptor["spans"],
        "scope": {"provenance_indexes": list(range(
            len(descriptor["spans"])))},
        "source_text_sha256": descriptor["source_text_sha256"],
        "source_lexical_sha256": descriptor["source_lexical_sha256"],
        "source_lexical_count": descriptor["source_lexical_count"],
        "transform": transform,
        "oracle_text_sha256": fidelity.text_sha256(oracle_text),
        "oracle_lexical_sha256": fidelity.lexical_sha256(oracle_tokens),
        "oracle_lexical_count": len(oracle_tokens),
        "recovery_sha256": recovery,
    }


def _record(index, text, *items, exemptions=None):
    record = {
        "text": text,
        "metadata": {
            "chunk_index": index,
            "source_lineage_schema_version": 5,
            "source_items": list(items),
        },
    }
    if exemptions is not None:
        record["metadata"]["source_order_exemptions"] = exemptions
    fidelity.attach_record_attestations([record])
    return record


def _trusted_oracles(records):
    result = {}
    for record in records:
        record_tokens = fidelity.lexical_tokens(record.get("text", ""))
        for entry in record.get("metadata", {}).get("source_items", []):
            if entry.get("transform") not in fidelity.OPAQUE_TRANSFORMS:
                continue
            width = entry["oracle_lexical_count"]
            oracle_tokens = ()
            if width:
                oracle_tokens = next((
                    record_tokens[start:start + width]
                    for start in range(len(record_tokens) - width + 1)
                    if fidelity.lexical_sha256(
                        record_tokens[start:start + width])
                    == entry["oracle_lexical_sha256"]
                ), ())
                assert len(oracle_tokens) == width
            result.setdefault(entry["ref"], {
                "transform": entry["transform"],
                "source_text_sha256": entry["source_text_sha256"],
                "oracle_text_sha256": entry["oracle_text_sha256"],
                "oracle_lexical_sha256": entry["oracle_lexical_sha256"],
                "oracle_lexical_count": entry["oracle_lexical_count"],
                "oracle_lexical_tokens": list(oracle_tokens),
                "oracle_group_sha256": (
                    fidelity.single_source_oracle_group_sha256(entry["ref"])),
                "oracle_group_members": [entry["ref"]],
                "recovery_sha256": entry["recovery_sha256"],
            })
    return result


def _trusted_oracle(
        entry, oracle_text, *, group_sha256=None, group_members=None):
    tokens = fidelity.lexical_tokens(oracle_text)
    assert len(tokens) == entry["oracle_lexical_count"]
    assert fidelity.lexical_sha256(tokens) == entry["oracle_lexical_sha256"]
    return {
        "transform": entry["transform"],
        "source_text_sha256": entry["source_text_sha256"],
        "oracle_text_sha256": entry["oracle_text_sha256"],
        "oracle_lexical_sha256": entry["oracle_lexical_sha256"],
        "oracle_lexical_count": entry["oracle_lexical_count"],
        "oracle_lexical_tokens": list(tokens),
        "oracle_group_sha256": (
            group_sha256
            or fidelity.single_source_oracle_group_sha256(entry["ref"])),
        "oracle_group_members": list(group_members or (entry["ref"],)),
        "recovery_sha256": entry["recovery_sha256"],
    }


def _audit(records, items, *, source_oracles=None):
    collections = {"texts": [], "tables": [], "pictures": []}
    for item in items:
        label = item["label"]
        collection = (
            "tables" if label == "table"
            else "pictures" if label == "picture" else "texts")
        collections[collection].append(item)
    return fidelity.audit_source_fidelity(
        records=records,
        document=collections,
        eligible_refs={item["self_ref"] for item in items},
        source_oracles=(
            _trusted_oracles(records)
            if source_oracles is None else source_oracles),
    )


def test_lineage_descriptor_retains_exact_charspan_order_and_hashes():
    item = _item("#/texts/0", "Exact source text")
    item["prov"].append({
        "page_no": 2,
        "charspan": [5, 10],
        "bbox": {
            "l": 20, "t": 30, "r": 210, "b": 40,
            "coord_origin": "TOPLEFT",
        },
    })

    descriptor, issues = fidelity.source_descriptor(item)

    assert issues == []
    assert [span["provenance_index"] for span in descriptor["spans"]] == [0, 1]
    assert descriptor["spans"][1]["charspan"] == [5, 10]
    assert descriptor["source_text_sha256"] == fidelity.text_sha256(
        "Exact source text")


def test_plain_output_must_be_source_lexical_content():
    item = _item("#/texts/0", "Authentic source language")
    authentic = _record(0, item["text"], _lineage(item))
    fabricated = _record(0, "Invented unrelated language", _lineage(item))

    assert fidelity.validate_summary(_audit([authentic], [item]))
    failed = _audit([fabricated], [item])
    assert failed["output_coverage_issues"] == [0]
    assert failed["source_coverage_issues"] == ["#/texts/0"]
    assert not fidelity.validate_summary(failed)


@pytest.mark.parametrize("curly", ["\u2018", "\u2019"])
def test_curly_apostrophes_are_lexically_equivalent_to_straight(curly):
    straight = "π's client can't object"
    typographic = f"π{curly}s client can{curly}t object"

    assert fidelity.text_sha256(typographic) != fidelity.text_sha256(straight)
    assert fidelity.lexical_tokens(typographic) == fidelity.lexical_tokens(
        straight)
    assert fidelity.lexical_sha256(typographic) == fidelity.lexical_sha256(
        straight)


@pytest.mark.parametrize("lookalike", ["\u02bc", "\u2032", "`", "\u00b4"])
def test_non_curly_apostrophe_lookalikes_remain_lexically_distinct(lookalike):
    assert fidelity.lexical_tokens(f"π{lookalike}s") != (
        fidelity.lexical_tokens("π's"))


def test_duplicate_output_cannot_reuse_one_source_token_range():
    item = _item("#/texts/0", "One unique source passage")
    records = [
        _record(0, item["text"], _lineage(item)),
        _record(1, item["text"], _lineage(item)),
    ]

    audit = _audit(records, [item])

    assert audit["output_coverage_issues"] == [1]
    assert audit["covered_output_tokens"] < audit["output_tokens"]


def test_repeated_phrase_splits_choose_distinct_source_occurrences():
    item = _item("#/texts/0", "repeat phrase middle repeat phrase")
    records = [
        _record(0, "repeat phrase", _lineage(item)),
        _record(1, "middle", _lineage(item)),
        _record(2, "repeat phrase", _lineage(item)),
    ]

    assert fidelity.validate_summary(_audit(records, [item]))


def test_split_records_for_one_ref_must_advance_source_offsets():
    item = _item("#/texts/0", "alpha beta gamma delta")
    ordered = [
        _record(0, "alpha beta", _lineage(item)),
        _record(1, "gamma delta", _lineage(item)),
    ]
    reversed_records = [
        _record(0, "gamma delta", _lineage(item)),
        _record(1, "alpha beta", _lineage(item)),
    ]

    assert fidelity.validate_summary(_audit(ordered, [item]))
    audit = _audit(reversed_records, [item])
    assert audit["output_coverage_issues"] == [1]


def test_list_marker_is_part_of_the_derived_list_oracle():
    item = _item(
        "#/texts/0", "Enumerated source text", label="list_item", marker="(7)")
    record = _record(0, "(7) Enumerated source text", _lineage(item))

    assert fidelity.validate_summary(_audit([record], [item]))


def test_list_marker_omission_is_not_treated_as_source_complete():
    item = _item(
        "#/texts/0", "Enumerated source text", label="list_item", marker="(7)")
    record = _record(0, "Enumerated source text", _lineage(item))

    audit = _audit([record], [item])

    assert audit["output_coverage_issues"] == []
    assert audit["source_coverage_issues"] == [item["self_ref"]]
    assert audit["covered_source_tokens"] + 1 == audit["source_tokens"]
    assert not fidelity.validate_summary(audit)


def test_punctuation_only_list_marker_is_mandatory_source_content():
    item = _item(
        "#/texts/0", "Bulleted source text", label="list_item", marker="-")
    entry = _lineage(item)
    complete = _record(0, "- Bulleted source text", entry)
    omitted = _record(0, "Bulleted source text", entry)

    assert fidelity.validate_summary(_audit([complete], [item]))
    audit = _audit([omitted], [item])

    assert audit["output_coverage_issues"] == []
    assert audit["source_coverage_issues"] == [item["self_ref"]]
    assert audit["covered_source_tokens"] + 1 == audit["source_tokens"]
    assert not fidelity.validate_summary(audit)


def test_source_bullet_glyphs_are_attested_by_commonmark_dash():
    for marker in ("·", "•"):
        item = _item(
            "#/texts/0", "Bulleted source text",
            label="list_item", marker=marker)
        record = _record(
            0, "- Bulleted source text", _lineage(item))

        assert fidelity.validate_summary(_audit([record], [item]))


def test_marker_inclusive_list_charspan_maps_body_tokens_to_its_page():
    item = _item(
        "#/texts/0", "Enumerated source text", label="list_item", marker="14.")
    item["prov"][0]["charspan"] = [
        0, len("14. Enumerated source text")]
    record = _record(0, "14. Enumerated source text", _lineage(item))

    audit = _audit([record], [item])

    assert audit["geometry_issues"] == []
    assert fidelity.validate_summary(audit)


def test_same_page_vertical_constraint_rejects_reversed_chunks():
    first = _item("#/texts/0", "First source", top=10)
    second = _item("#/texts/1", "Second source", top=40)
    records = [
        _record(0, second["text"], _lineage(second)),
        _record(1, first["text"], _lineage(first)),
    ]

    audit = _audit(records, [first, second])

    assert audit["geometry_constraints"] == 1
    assert audit["geometry_violations"] == [{
        "before_ref": "#/texts/0",
        "after_ref": "#/texts/1",
        "page": 1,
        "before_chunk_index": 1,
        "after_chunk_index": 0,
        "before_output_token": 1,
        "after_output_token": 0,
    }]


def test_same_page_constraint_uses_last_before_and_first_after_occurrences():
    first = _item(
        "#/texts/0", "alpha beta gamma delta", top=10)
    second = _item("#/texts/1", "later source block", top=40)
    interleaved = [
        _record(0, "alpha beta", _lineage(first)),
        _record(1, second["text"], _lineage(second)),
        _record(2, "gamma delta", _lineage(first)),
    ]

    audit = _audit(interleaved, [first, second])

    assert audit["output_coverage_issues"] == []
    assert audit["geometry_violations"] == [{
        "before_ref": first["self_ref"],
        "after_ref": second["self_ref"],
        "page": 1,
        "before_chunk_index": 2,
        "after_chunk_index": 1,
        "before_output_token": 1,
        "after_output_token": 0,
    }]
    assert not fidelity.validate_summary(audit)


def test_typed_sidecar_exemption_requires_exact_source_relationship():
    body = _item("#/texts/0", "Body text", top=10)
    note = _item("#/texts/1", "1. Footnote text", top=40, label="footnote")
    body["footnotes"] = [{"$ref": note["self_ref"]}]
    exemption = {
        "before_ref": body["self_ref"],
        "after_ref": note["self_ref"],
        "page": 1,
        "reason": "footnote_sidecar",
    }
    reversed_records = [
        _record(0, note["text"], _lineage(note), exemptions=[exemption]),
        _record(1, body["text"], _lineage(body)),
    ]
    ordered_records = [
        _record(0, body["text"], _lineage(body), exemptions=[exemption]),
        _record(1, note["text"], _lineage(note)),
    ]

    assert fidelity.validate_summary(_audit(reversed_records, [body, note]))
    unused = _audit(ordered_records, [body, note])
    assert unused["lineage_issues"] == [0]

    body.pop("footnotes")
    unrelated = _audit(reversed_records, [body, note])
    assert unrelated["lineage_issues"] == [0]
    assert len(unrelated["geometry_violations"]) == 1


def test_two_column_lane_transition_rejects_swapped_column_blocks():
    left_top = _item(
        "#/texts/0", "Left top", top=10, left=10, right=100)
    left_bottom = _item(
        "#/texts/1", "Left bottom", top=40, left=10, right=100)
    right_top = _item(
        "#/texts/2", "Right top", top=10, left=180, right=270)
    right_bottom = _item(
        "#/texts/3", "Right bottom", top=40, left=180, right=270)
    items = [left_top, left_bottom, right_top, right_bottom]
    ordered = [
        _record(index, item["text"], _lineage(item))
        for index, item in enumerate(items)
    ]
    swapped_items = [right_top, right_bottom, left_top, left_bottom]
    swapped = [
        _record(index, item["text"], _lineage(item))
        for index, item in enumerate(swapped_items)
    ]

    passing = _audit(ordered, items)
    failing = _audit(swapped, items)

    assert fidelity.validate_summary(passing)
    assert {
        (entry["before_ref"], entry["after_ref"])
        for entry in failing["geometry_violations"]
    } == {("#/texts/1", "#/texts/2")}


def test_sparse_two_column_page_still_enforces_left_to_right_order():
    left = _item(
        "#/texts/0", "Left column", top=10, left=10, right=100)
    right = _item(
        "#/texts/1", "Right column", top=10, left=180, right=270)
    swapped = [
        _record(0, right["text"], _lineage(right)),
        _record(1, left["text"], _lineage(left)),
    ]

    audit = _audit(swapped, [left, right])

    assert audit["geometry_constraints"] == 1
    assert {
        (entry["before_ref"], entry["after_ref"])
        for entry in audit["geometry_violations"]
    } == {(left["self_ref"], right["self_ref"])}
    assert not fidelity.validate_summary(audit)


def test_narrow_inline_fragment_does_not_create_a_sparse_second_column():
    body = _item(
        "#/texts/0", "Main body block", top=40, left=10, right=220)
    inline = _item(
        "#/texts/1", "is", top=40, left=250, right=270)
    records = [
        _record(0, inline["text"], _lineage(inline)),
        _record(1, body["text"], _lineage(body)),
    ]

    audit = _audit(records, [body, inline])

    assert audit["geometry_constraints"] == 0
    assert audit["geometry_violations"] == []
    assert fidelity.validate_summary(audit)


def test_scoped_native_fragments_register_only_their_provenance_page():
    spanning = _item("#/texts/0", "alpha beta", page=1, top=10)
    spanning["prov"][0]["charspan"] = [0, len("alpha")]
    spanning["prov"].append({
        "page_no": 2,
        "charspan": [len("alpha "), len("alpha beta")],
        "bbox": {
            "l": 10, "t": 10, "r": 200, "b": 20,
            "coord_origin": "TOPLEFT",
        },
    })
    later = _item("#/texts/1", "later page-one body", page=1, top=40)
    first_entry = _lineage(
        spanning, transform="native_repair", oracle_text="alpha beta")
    first_entry["scope"] = {"provenance_indexes": [0]}
    second_entry = copy.deepcopy(first_entry)
    second_entry["scope"] = {"provenance_indexes": [1]}
    records = [
        _record(0, "alpha", first_entry),
        _record(1, later["text"], _lineage(later)),
        _record(2, "beta", second_entry),
    ]
    trusted = {
        spanning["self_ref"]: _trusted_oracle(
            first_entry, "alpha beta"),
    }

    audit = _audit(
        records, [spanning, later], source_oracles=trusted)

    assert audit["geometry_violations"] == []
    assert fidelity.validate_summary(audit)


def test_unclaimed_provenance_atom_does_not_create_a_geometry_constraint():
    spanning = _item("#/texts/0", "alpha beta", page=1, top=10)
    spanning["prov"][0]["charspan"] = [0, len("alpha")]
    spanning["prov"].append({
        "page_no": 2,
        "charspan": [len("alpha "), len("alpha beta")],
        "bbox": {
            "l": 10, "t": 10, "r": 200, "b": 20,
            "coord_origin": "TOPLEFT",
        },
    })
    page_two = _item("#/texts/1", "page two body", page=2, top=40)
    scoped = _lineage(
        spanning, transform="native_repair", oracle_text="alpha beta")
    scoped["scope"] = {"provenance_indexes": [0]}
    records = [
        _record(0, "alpha", scoped),
        _record(1, page_two["text"], _lineage(page_two)),
    ]
    trusted = {
        spanning["self_ref"]: _trusted_oracle(scoped, "alpha beta"),
    }

    audit = _audit(
        records, [spanning, page_two], source_oracles=trusted)

    assert audit["geometry_constraints"] == 0
    assert audit["geometry_violations"] == []
    assert audit["source_coverage_issues"] == [spanning["self_ref"]]


def test_multi_page_item_uses_page_specific_token_occurrences_for_order():
    spanning = _item("#/texts/0", "alpha beta gamma delta", page=1, top=10)
    spanning["prov"][0]["charspan"] = [0, len("alpha beta")]
    spanning["prov"].append({
        "page_no": 2,
        "charspan": [len("alpha beta "), len(spanning["text"])],
        "bbox": {
            "l": 10, "t": 10, "r": 200, "b": 20,
            "coord_origin": "TOPLEFT",
        },
    })
    page_two_later = _item(
        "#/texts/1", "page two later", page=2, top=40)
    records = [
        _record(0, "alpha beta", _lineage(spanning)),
        _record(1, page_two_later["text"], _lineage(page_two_later)),
        _record(2, "gamma delta", _lineage(spanning)),
    ]

    audit = _audit(records, [spanning, page_two_later])

    assert audit["output_coverage_issues"] == []
    assert {
        (entry["before_ref"], entry["after_ref"], entry["page"])
        for entry in audit["geometry_violations"]
    } == {(spanning["self_ref"], page_two_later["self_ref"], 2)}
    assert not fidelity.validate_summary(audit)


def test_token_page_mapping_expands_small_malformed_cross_page_word_cut():
    text = "alpha colleagues and omega"
    item = _item("#/texts/0", text, page=1, top=10)
    token_start = text.index("colleagues")
    split = token_start + 4
    item["prov"][0]["charspan"] = [0, split]
    item["prov"].append({
        "page_no": 2,
        "charspan": [split + 1, len(text) + 2],
        "bbox": {
            "l": 10, "t": 10, "r": 200, "b": 20,
            "coord_origin": "TOPLEFT",
        },
    })

    pages = fidelity._source_token_pages(item)

    assert pages == {
        0: frozenset({1}),
        1: frozenset({1, 2}),
        2: frozenset({2}),
        3: frozenset({2}),
    }
    record = _record(0, text, _lineage(item))
    audit = _audit([record], [item])
    assert audit["geometry_issues"] == []
    assert fidelity.validate_summary(audit)


def test_token_page_mapping_rejects_unbounded_charspan_overrun():
    item = _item("#/texts/0", "alpha beta", page=1, top=10)
    item["prov"][0]["charspan"] = [0, len(item["text"]) + 40]
    record = _record(0, item["text"], _lineage(item))

    assert fidelity._source_token_pages(item) == {}
    audit = _audit([record], [item])
    assert audit["geometry_issues"] == [
        f"{item['self_ref']}:incomplete_token_page_mapping"]
    assert not fidelity.validate_summary(audit)


def test_mixed_opaque_transform_requires_exact_record_binding():
    table = _item("#/tables/0", "", top=10, label="table")
    table["data"] = {"table_cells": [{"text": "Rule"}, {"text": "Value"}]}
    prose = _item("#/texts/0", "Adjacent prose", top=40)
    record = _record(
        0, "| Rule | Value |\n\nAdjacent prose",
        _lineage(table, transform="table", oracle_text="| Rule | Value |"),
        _lineage(prose),
    )

    assert fidelity.validate_summary(_audit([record], [table, prose]))

    tampered = copy.deepcopy(record)
    tampered["text"] += " fabricated"
    fidelity.attach_record_attestations([tampered])
    failed = _audit([tampered], [table, prose])
    assert failed["output_coverage_issues"] == [0]


def test_opaque_oracle_cannot_self_assert_fabricated_lexical_content():
    table = _item("#/tables/0", "Authentic source", label="table")
    authentic_entry = _lineage(
        table, transform="table", oracle_text="| Authentic | source |")
    trusted = _trusted_oracles([
        _record(0, "| Authentic | source |", authentic_entry)])
    fabricated_entry = copy.deepcopy(authentic_entry)
    fabricated_tokens = fidelity.lexical_tokens("| Fabricated | output |")
    fabricated_entry["oracle_lexical_sha256"] = fidelity.lexical_sha256(
        fabricated_tokens)
    fabricated_entry["oracle_lexical_count"] = len(fabricated_tokens)
    fabricated = _record(
        0, "| Fabricated | output |", fabricated_entry)

    audit = _audit(
        [fabricated], [table], source_oracles=trusted)

    assert audit["lineage_issues"] == [0]
    assert audit["output_coverage_issues"] == [0]
    assert not fidelity.validate_summary(audit)


def test_nonempty_opaque_source_rejects_zero_width_oracle():
    table = _item("#/tables/0", "Critical omitted rule", label="table")
    prose = _item("#/texts/0", "Remaining prose", top=40)
    empty_entry = _lineage(table, transform="table", oracle_text="")
    record = _record(0, prose["text"], empty_entry, _lineage(prose))
    trusted = _trusted_oracles([record])

    audit = _audit([record], [table, prose], source_oracles=trusted)

    assert audit["lineage_issues"] == [0]
    assert audit["source_coverage_issues"] == [table["self_ref"]]
    assert not fidelity.validate_summary(audit)


def test_opaque_oracle_cannot_be_replayed_across_normal_records():
    table = _item("#/tables/0", "Rule Value", label="table")
    entry = _lineage(
        table, transform="table", oracle_text="| Rule | Value |")
    records = [
        _record(0, "| Rule | Value |", entry),
        _record(1, "| Rule | Value |", entry),
    ]

    audit = _audit(records, [table])

    assert audit["output_coverage_issues"] == [1]
    assert audit["covered_output_tokens"] < audit["output_tokens"]
    assert not fidelity.validate_summary(audit)


def test_opaque_oracle_may_be_exactly_partitioned_without_gap_or_overlap():
    table = _item("#/tables/0", "Recovered table", label="table")
    oracle_text = "alpha beta gamma delta"
    entry = _lineage(table, transform="table", oracle_text=oracle_text)
    trusted = {table["self_ref"]: _trusted_oracle(entry, oracle_text)}
    records = [
        _record(0, "alpha beta", entry),
        _record(1, "gamma delta", entry),
    ]

    assert fidelity.validate_summary(
        _audit(records, [table], source_oracles=trusted))

    gap = [
        _record(0, "alpha beta", entry),
        _record(1, "delta", entry),
    ]
    gap_audit = _audit(gap, [table], source_oracles=trusted)
    assert gap_audit["source_coverage_issues"] == [table["self_ref"]]

    overlap = [
        _record(0, "alpha beta gamma", entry),
        _record(1, "gamma delta", entry),
    ]
    overlap_audit = _audit(overlap, [table], source_oracles=trusted)
    assert overlap_audit["output_coverage_issues"] == [1]


def test_explicit_opaque_group_maps_one_recovery_to_every_group_ref():
    first = _item("#/texts/0", "broken alpha")
    second = _item("#/texts/1", "broken beta", top=30)
    oracle_text = "recovered alpha beta"
    first_entry = _lineage(
        first, transform="native_repair", oracle_text=oracle_text)
    second_entry = _lineage(
        second, transform="native_repair", oracle_text=oracle_text)
    group_sha256 = fidelity.native_recovery_group_sha256(
        (first["self_ref"], second["self_ref"]),
        fidelity.text_sha256(oracle_text),
    )
    group_members = (first["self_ref"], second["self_ref"])
    trusted = {
        first["self_ref"]: _trusted_oracle(
            first_entry, oracle_text, group_sha256=group_sha256,
            group_members=group_members),
        second["self_ref"]: _trusted_oracle(
            second_entry, oracle_text, group_sha256=group_sha256,
            group_members=group_members),
    }
    record = _record(0, oracle_text, first_entry, second_entry)

    audit = _audit([record], [first, second], source_oracles=trusted)

    assert audit["geometry_violations"] == []
    assert fidelity.validate_summary(audit)


def test_native_repair_group_binding_rejects_forged_or_reversed_ref_order():
    first = _item("#/texts/0", "broken alpha")
    second = _item("#/texts/1", "broken beta", top=30)
    oracle_text = "recovered alpha beta"
    first_entry = _lineage(
        first, transform="native_repair", oracle_text=oracle_text)
    second_entry = _lineage(
        second, transform="native_repair", oracle_text=oracle_text)
    record = _record(0, oracle_text, first_entry, second_entry)

    forged_group = hashlib.sha256(b"forged shared group").hexdigest()
    forward_members = (first["self_ref"], second["self_ref"])
    forged = {
        first["self_ref"]: _trusted_oracle(
            first_entry, oracle_text, group_sha256=forged_group,
            group_members=forward_members),
        second["self_ref"]: _trusted_oracle(
            second_entry, oracle_text, group_sha256=forged_group,
            group_members=forward_members),
    }
    forged_audit = _audit(
        [record], [first, second], source_oracles=forged)
    assert forged_audit["lineage_issues"] == [0]
    assert forged_audit["output_coverage_issues"] == [0]
    assert set(forged_audit["source_coverage_issues"]) == {
        first["self_ref"], second["self_ref"]}
    assert not fidelity.validate_summary(forged_audit)

    reversed_group = fidelity.native_recovery_group_sha256(
        (second["self_ref"], first["self_ref"]),
        fidelity.text_sha256(oracle_text),
    )
    reversed_trusted = {
        first["self_ref"]: _trusted_oracle(
            first_entry, oracle_text, group_sha256=reversed_group,
            group_members=(second["self_ref"], first["self_ref"])),
        second["self_ref"]: _trusted_oracle(
            second_entry, oracle_text, group_sha256=reversed_group,
            group_members=(second["self_ref"], first["self_ref"])),
    }
    reversed_audit = _audit(
        [record], [first, second], source_oracles=reversed_trusted)
    assert reversed_audit["lineage_issues"] == [0]
    assert reversed_audit["output_coverage_issues"] == [0]
    assert not fidelity.validate_summary(reversed_audit)


def test_cross_page_native_group_binds_members_and_source_order():
    first = _item("#/texts/0", "broken alpha", page=1)
    second = _item("#/texts/1", "broken beta", page=2)
    oracle_text = "recovered alpha beta"
    entries = [
        _lineage(item, transform="native_repair", oracle_text=oracle_text)
        for item in (first, second)
    ]
    members = (first["self_ref"], second["self_ref"])
    group_sha256 = fidelity.native_recovery_group_sha256(
        members, fidelity.text_sha256(oracle_text))
    trusted = {
        item["self_ref"]: _trusted_oracle(
            entry, oracle_text, group_sha256=group_sha256,
            group_members=members)
        for item, entry in zip((first, second), entries)
    }
    record = _record(0, oracle_text, *entries)
    inputs = {
        "docling_json": {
            "name": "book.json", "size": 100, "sha256": "a" * 64},
        "conversion_manifest": None,
        "table_recovery": None,
    }

    registry = fidelity.build_source_oracle_registry(
        oracles=trusted, input_bindings=inputs)
    assert fidelity.validate_source_oracle_registry(registry) == trusted
    assert fidelity.validate_summary(
        _audit([record], [first, second], source_oracles=trusted))

    forged = copy.deepcopy(trusted)
    forged_group = hashlib.sha256(b"forged cross-page group").hexdigest()
    for descriptor in forged.values():
        descriptor["oracle_group_sha256"] = forged_group
    with pytest.raises(ValueError, match="group binding"):
        fidelity.build_source_oracle_registry(
            oracles=forged, input_bindings=inputs)
    forged_audit = _audit(
        [record], [first, second], source_oracles=forged)
    assert forged_audit["output_coverage_issues"] == [0]
    assert not fidelity.validate_summary(forged_audit)

    reversed_members = tuple(reversed(members))
    reversed_group = fidelity.native_recovery_group_sha256(
        reversed_members, fidelity.text_sha256(oracle_text))
    reversed_trusted = copy.deepcopy(trusted)
    for descriptor in reversed_trusted.values():
        descriptor["oracle_group_sha256"] = reversed_group
        descriptor["oracle_group_members"] = list(reversed_members)
    # Registry shape/hash validation is source-agnostic; the audit binds that
    # declared order to the captured cross-page source geometry.
    fidelity.build_source_oracle_registry(
        oracles=reversed_trusted, input_bindings=inputs)
    reversed_audit = _audit(
        [record], [first, second], source_oracles=reversed_trusted)
    assert reversed_audit["output_coverage_issues"] == [0]
    assert not fidelity.validate_summary(reversed_audit)


def test_equal_native_oracles_do_not_form_an_implicit_atomic_group():
    first = _item("#/texts/0", "broken alpha")
    second = _item("#/texts/1", "broken beta", top=30)
    oracle_text = "recovered alpha beta"
    first_entry = _lineage(
        first, transform="native_repair", oracle_text=oracle_text)
    second_entry = _lineage(
        second, transform="native_repair", oracle_text=oracle_text)
    record = _record(0, oracle_text, first_entry, second_entry)
    trusted = {
        first["self_ref"]: _trusted_oracle(first_entry, oracle_text),
        second["self_ref"]: _trusted_oracle(second_entry, oracle_text),
    }

    audit = _audit([record], [first, second], source_oracles=trusted)

    assert second["self_ref"] in audit["source_coverage_issues"]
    assert not fidelity.validate_summary(audit)


def test_native_group_atomicity_requires_members_to_align_in_one_record():
    later = _item("#/texts/0", "broken later", top=30)
    earlier = _item("#/texts/1", "broken earlier", top=10)
    oracle_text = "alpha beta"
    later_entry = _lineage(
        later, transform="native_repair", oracle_text=oracle_text)
    earlier_entry = _lineage(
        earlier, transform="native_repair", oracle_text=oracle_text)
    group_sha256 = fidelity.native_recovery_group_sha256(
        (earlier["self_ref"], later["self_ref"]),
        fidelity.text_sha256(oracle_text),
    )
    group_members = (earlier["self_ref"], later["self_ref"])
    trusted = {
        later["self_ref"]: _trusted_oracle(
            later_entry, oracle_text, group_sha256=group_sha256,
            group_members=group_members),
        earlier["self_ref"]: _trusted_oracle(
            earlier_entry, oracle_text, group_sha256=group_sha256,
            group_members=group_members),
    }
    records = [
        _record(0, "alpha", later_entry),
        _record(1, "beta", earlier_entry),
    ]

    audit = _audit(
        records, [later, earlier], source_oracles=trusted)

    assert audit["lineage_issues"] == [0, 1]
    assert audit["output_coverage_issues"] == [0, 1]
    assert not fidelity.validate_summary(audit)


def test_native_group_external_geometry_edges_remain_strict():
    before = _item("#/texts/0", "before", top=0)
    first = _item("#/texts/1", "broken alpha", top=20)
    second = _item("#/texts/2", "broken beta", top=40)
    after = _item("#/texts/3", "after", top=60)
    oracle_text = "recovered alpha beta"
    before_entry = _lineage(before)
    first_entry = _lineage(
        first, transform="native_repair", oracle_text=oracle_text)
    second_entry = _lineage(
        second, transform="native_repair", oracle_text=oracle_text)
    after_entry = _lineage(after)
    group_sha256 = fidelity.native_recovery_group_sha256(
        (first["self_ref"], second["self_ref"]),
        fidelity.text_sha256(oracle_text),
    )
    group_members = (first["self_ref"], second["self_ref"])
    trusted = {
        first["self_ref"]: _trusted_oracle(
            first_entry, oracle_text, group_sha256=group_sha256,
            group_members=group_members),
        second["self_ref"]: _trusted_oracle(
            second_entry, oracle_text, group_sha256=group_sha256,
            group_members=group_members),
    }
    valid = _record(
        0, f"before {oracle_text} after",
        before_entry, first_entry, second_entry, after_entry)
    assert fidelity.validate_summary(_audit(
        [valid], [before, first, second, after], source_oracles=trusted))

    reversed_before = _record(
        0, f"{oracle_text} before after",
        first_entry, second_entry, before_entry, after_entry)
    audit = _audit(
        [reversed_before], [before, first, second, after],
        source_oracles=trusted)
    assert any(
        issue["before_ref"] == before["self_ref"]
        and issue["after_ref"] == first["self_ref"]
        for issue in audit["geometry_violations"])
    assert not fidelity.validate_summary(audit)


def _container_alias_fixture(*child_texts, oracle_text=None):
    table = _item("#/tables/0", "", label="table")
    table["children"] = [
        {"cref": f"#/texts/{index}"}
        for index in range(len(child_texts))
    ]
    children = [
        _item(f"#/texts/{index}", text, top=30 + index * 20)
        for index, text in enumerate(child_texts)
    ]
    oracle_text = oracle_text or " ".join(child_texts)
    owner_entry = _lineage(
        table, transform="table", oracle_text=oracle_text)
    alias_entries = [
        _lineage(
            child, transform="container_alias", oracle_text=oracle_text)
        for child in children
    ]
    alias_refs = tuple(child["self_ref"] for child in children)
    group_members = (table["self_ref"], *alias_refs)
    group_sha256 = fidelity.visual_container_alias_group_sha256(
        table["self_ref"], alias_refs, fidelity.text_sha256(oracle_text))
    trusted = {
        table["self_ref"]: _trusted_oracle(
            owner_entry, oracle_text, group_sha256=group_sha256,
            group_members=group_members),
        **{
            child["self_ref"]: _trusted_oracle(
                entry, oracle_text, group_sha256=group_sha256,
                group_members=group_members)
            for child, entry in zip(children, alias_entries)
        },
    }
    return table, children, owner_entry, alias_entries, trusted, oracle_text


def test_visual_alias_group_rejects_an_unrelated_native_member():
    (table, children, owner_entry, alias_entries,
     trusted, oracle_text) = _container_alias_fixture("alpha beta")
    native = _item("#/texts/9", "broken native", page=2)
    native_entry = _lineage(
        native, transform="native_repair", oracle_text=oracle_text)
    forged_group = hashlib.sha256(b"mixed visual native group").hexdigest()
    members = (table["self_ref"], children[0]["self_ref"], native["self_ref"])
    forged = copy.deepcopy(trusted)
    for descriptor in forged.values():
        descriptor["oracle_group_sha256"] = forged_group
        descriptor["oracle_group_members"] = list(members)
    forged[native["self_ref"]] = _trusted_oracle(
        native_entry, oracle_text, group_sha256=forged_group,
        group_members=members)
    inputs = {
        "docling_json": {
            "name": "book.json", "size": 100, "sha256": "a" * 64},
        "conversion_manifest": None,
        "table_recovery": None,
    }

    with pytest.raises(ValueError, match="shared group shape"):
        fidelity.build_source_oracle_registry(
            oracles=forged, input_bindings=inputs)
    record = _record(0, oracle_text, owner_entry, *alias_entries, native_entry)
    audit = _audit(
        [record], [table, *children, native], source_oracles=forged)
    assert audit["output_coverage_issues"] == [0]
    assert not fidelity.validate_summary(audit)


def test_source_oracle_registry_rejects_standalone_container_alias():
    alias = _item("#/texts/0", "alpha beta")
    alias_entry = _lineage(
        alias, transform="container_alias", oracle_text="alpha beta")
    trusted = {
        alias["self_ref"]: _trusted_oracle(alias_entry, "alpha beta")}
    inputs = {
        "docling_json": {
            "name": "book.json", "size": 100, "sha256": "a" * 64},
        "conversion_manifest": None,
        "table_recovery": None,
    }

    with pytest.raises(ValueError, match="no visual owner"):
        fidelity.build_source_oracle_registry(
            oracles=trusted, input_bindings=inputs)


def test_container_alias_credits_complete_related_children_once():
    (table, children, owner_entry, alias_entries,
     trusted, oracle_text) = _container_alias_fixture(
         "alpha beta", "gamma delta")
    record = _record(0, oracle_text, owner_entry, *alias_entries)

    audit = _audit(
        [record], [table, *children], source_oracles=trusted)

    assert audit["source_coverage_issues"] == []
    assert audit["output_coverage_issues"] == []
    assert fidelity.validate_summary(audit)


def test_container_alias_accepts_only_fused_possessive_symbol_boundary():
    (table, children, owner_entry, alias_entries,
     trusted, oracle_text) = _container_alias_fixture(
         "π", oracle_text="π's burden")
    record = _record(0, oracle_text, owner_entry, *alias_entries)

    assert fidelity.validate_summary(
        _audit([record], [table, *children], source_oracles=trusted))
    assert fidelity._joint_nonoverlapping_alias_offsets(
        ("lawyer",), {"#/texts/0": ("law",)}) is None


def test_container_alias_requires_visual_owner_in_the_same_record():
    (table, children, _owner_entry, alias_entries,
     trusted, oracle_text) = _container_alias_fixture("alpha beta")
    record = _record(0, oracle_text, *alias_entries)

    audit = _audit(
        [record], [table, *children], source_oracles=trusted)

    assert audit["lineage_issues"] == [0]
    assert audit["output_coverage_issues"] == [0]
    assert not fidelity.validate_summary(audit)


def test_container_alias_rejects_unrelated_or_reused_child_offsets():
    (table, children, owner_entry, alias_entries,
     trusted, oracle_text) = _container_alias_fixture(
         "alpha", "alpha", oracle_text="alpha")
    reused = _record(0, oracle_text, owner_entry, *alias_entries)
    reused_audit = _audit(
        [reused], [table, *children], source_oracles=trusted)

    assert reused_audit["lineage_issues"] == [0]
    assert not fidelity.validate_summary(reused_audit)

    table["children"] = []
    unrelated = _record(0, oracle_text, owner_entry, *alias_entries)
    unrelated_audit = _audit(
        [unrelated], [table, *children], source_oracles=trusted)
    assert unrelated_audit["lineage_issues"] == [0]
    assert not fidelity.validate_summary(unrelated_audit)


def test_container_alias_credits_only_covered_owner_offsets():
    (table, children, owner_entry, alias_entries,
     trusted, _oracle_text) = _container_alias_fixture("alpha beta")
    partial = _record(0, "alpha", owner_entry, *alias_entries)

    audit = _audit(
        [partial], [table, *children], source_oracles=trusted)

    assert audit["output_coverage_issues"] == []
    assert children[0]["self_ref"] in audit["source_coverage_issues"]
    assert not fidelity.validate_summary(audit)


def test_native_repair_list_marker_has_one_separate_source_ownership_path():
    item = _item(
        "#/texts/0", "broken source body", label="list_item", marker="4.")
    entry = _lineage(
        item, transform="native_repair", oracle_text="repaired source body")
    trusted = {
        item["self_ref"]: _trusted_oracle(entry, "repaired source body")}
    complete = _record(0, "4. repaired source body", entry)
    omitted = _record(0, "repaired source body", entry)

    assert fidelity.validate_summary(
        _audit([complete], [item], source_oracles=trusted))
    audit = _audit([omitted], [item], source_oracles=trusted)
    assert audit["source_coverage_issues"] == [item["self_ref"]]
    assert not fidelity.validate_summary(audit)


def test_native_repair_group_still_requires_its_first_list_marker_in_audit():
    first = _item(
        "#/texts/0", "broken source", label="list_item", marker="(1)")
    second = _item(
        "#/texts/1", "body continuation", label="list_item", top=30)
    oracle_text = "repaired source body continuation"
    first_entry = _lineage(
        first, transform="native_repair", oracle_text=oracle_text)
    second_entry = _lineage(
        second, transform="native_repair", oracle_text=oracle_text)
    group_sha256 = fidelity.native_recovery_group_sha256(
        (first["self_ref"], second["self_ref"]),
        fidelity.text_sha256(oracle_text),
    )
    group_members = (first["self_ref"], second["self_ref"])
    trusted = {
        first["self_ref"]: _trusted_oracle(
            first_entry, oracle_text, group_sha256=group_sha256,
            group_members=group_members),
        second["self_ref"]: _trusted_oracle(
            second_entry, oracle_text, group_sha256=group_sha256,
            group_members=group_members),
    }

    complete = _record(
        0, f"(1) {oracle_text}", first_entry, second_entry)
    assert fidelity.validate_summary(_audit(
        [complete], [first, second], source_oracles=trusted))

    omitted = _record(0, oracle_text, first_entry, second_entry)
    audit = _audit(
        [omitted], [first, second], source_oracles=trusted)
    assert first["self_ref"] in audit["source_coverage_issues"]
    assert not fidelity.validate_summary(audit)


def test_zero_width_picture_replay_still_fails_without_owner_pruning():
    picture = _item("#/pictures/0", "", label="picture")
    entry = _lineage(picture, transform="figure", oracle_text="")
    trusted = {picture["self_ref"]: _trusted_oracle(entry, "")}
    records = [_record(0, "", entry), _record(1, "", entry)]

    audit = _audit(records, [picture], source_oracles=trusted)

    assert audit["output_coverage_issues"] == [1]
    assert not fidelity.validate_summary(audit)


def test_source_oracle_registry_is_canonical_and_input_bound():
    table = _item("#/tables/0", "Rule Value", label="table")
    record = _record(
        0, "| Rule | Value |",
        _lineage(table, transform="table", oracle_text="| Rule | Value |"))
    oracles = _trusted_oracles([record])
    inputs = {
        "docling_json": {
            "name": "book.json", "size": 100, "sha256": "a" * 64},
        "conversion_manifest": None,
        "table_recovery": None,
    }

    registry = fidelity.build_source_oracle_registry(
        oracles=oracles, input_bindings=inputs)
    inputs["docling_json"]["size"] = 200

    assert fidelity.validate_source_oracle_registry(
        registry,
        expected_input_bindings={
            **inputs,
            "docling_json": {
                "name": "book.json", "size": 100, "sha256": "a" * 64},
        },
    ) == oracles

    tampered = copy.deepcopy(registry)
    tampered["oracles"][0]["oracle_lexical_count"] += 1
    with pytest.raises(ValueError, match="values|root"):
        fidelity.validate_source_oracle_registry(tampered)
    with pytest.raises(ValueError, match="detached"):
        fidelity.validate_source_oracle_registry(
            registry, expected_input_bindings=inputs)


def test_source_oracle_registry_builder_rejects_bogus_recovery_binding():
    table = _item("#/tables/0", "Rule Value", label="table")
    record = _record(
        0, "| Rule | Value |",
        _lineage(table, transform="table", oracle_text="| Rule | Value |"))
    oracles = _trusted_oracles([record])
    oracles[table["self_ref"]]["recovery_sha256"] = "b" * 64
    inputs = {
        "docling_json": {
            "name": "book.json", "size": 100, "sha256": "a" * 64},
        "conversion_manifest": None,
        "table_recovery": None,
    }

    with pytest.raises(ValueError, match="recovery binding"):
        fidelity.build_source_oracle_registry(
            oracles=oracles, input_bindings=inputs)


def test_same_record_opaque_and_plain_order_is_source_aligned():
    table = _item("#/tables/0", "", top=10, label="table")
    table["data"] = {"table_cells": [{"text": "Rule"}, {"text": "Value"}]}
    prose = _item("#/texts/0", "Adjacent prose", top=40)
    table_lineage = _lineage(
        table, transform="table", oracle_text="| Rule | Value |")
    prose_lineage = _lineage(prose)
    authentic = _record(
        0, "| Rule | Value |\n\nAdjacent prose",
        table_lineage, prose_lineage)
    swapped = _record(
        0, "Adjacent prose\n\n| Rule | Value |",
        table_lineage, prose_lineage)

    assert fidelity.validate_summary(_audit([authentic], [table, prose]))
    audit = _audit([swapped], [table, prose])
    assert audit["output_coverage_issues"] == [0]
    assert audit["covered_output_tokens"] == 0


def test_missing_bbox_or_charspan_is_fail_closed():
    missing_bbox = _item("#/texts/0", "Text")
    del missing_bbox["prov"][0]["bbox"]
    missing_charspan = _item("#/texts/1", "Text", top=40)
    del missing_charspan["prov"][0]["charspan"]

    _, bbox_issues = fidelity.source_descriptor(missing_bbox)
    _, charspan_issues = fidelity.source_descriptor(missing_charspan)

    assert bbox_issues == ["prov[0]:bbox"]
    assert charspan_issues == ["prov[0]:charspan"]
