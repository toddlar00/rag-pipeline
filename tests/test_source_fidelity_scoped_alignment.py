"""Scoped page views and the fidelity audit's greedy alignment fallback.

A multi-page plain item may be published as separate page views, each record
claiming only its own provenance.  When a record's tokens are not contiguous
in its haystack, the greedy fallback must not lend a token of the next cited
item to an omitted page view.  Every fixture is synthetic.
"""

import source_fidelity_core as fidelity
from test_source_fidelity_core import (
    _item, _lineage, _record, _trusted_oracles)


def _two_page_item(ref, first, second, *, top=700):
    item = _item(ref, f"{first} {second}", top=top)
    item["prov"][0]["charspan"] = [0, len(first)]
    item["prov"].append({
        "page_no": 2,
        "charspan": [len(first) + 1, len(item["text"])],
        "bbox": {
            "l": 10, "t": 10, "r": 200, "b": 20,
            "coord_origin": "TOPLEFT",
        },
    })
    return item


def _scoped(item, *indexes, **lineage_options):
    entry = _lineage(item, **lineage_options)
    entry["scope"] = {"provenance_indexes": list(indexes)}
    return entry


def _audit(records, items, *, eligible=None):
    records = [
        _record(index, text, *entries)
        for index, (text, entries) in enumerate(records)
    ]
    return fidelity.audit_source_fidelity(
        records=records,
        document={"texts": items, "tables": [], "pictures": []},
        eligible_refs={
            item["self_ref"]
            for item in (items if eligible is None else eligible)},
        source_oracles=_trusted_oracles(records),
    )


def _assert_exact(audit, source_tokens):
    assert audit["lineage_issues"] == []
    assert audit["output_coverage_issues"] == []
    assert audit["source_coverage_issues"] == []
    assert audit["geometry_violations"] == []
    assert audit["covered_output_tokens"] == audit["output_tokens"]
    assert audit["covered_source_tokens"] == audit["source_tokens"] == (
        source_tokens)
    assert fidelity.validate_summary(audit)


# --- An omitted page view must not lend a token to the next cited item. ---

def test_scoped_page_view_does_not_lend_a_later_page_token_to_the_next_item():
    body = _two_page_item("#/texts/0", "Alpha beta gamma", "delta b epsilon")
    note = _item("#/texts/1", "B zeta eta", top=750)

    audit = _audit([
        ("Alpha beta gamma\n\nB zeta eta",
         [_scoped(body, 0), _scoped(note, 0)]),
        ("delta b epsilon", [_scoped(body, 1)]),
    ], [body, note])

    _assert_exact(audit, 9)


def test_scoped_page_view_does_not_lend_a_later_page_token_to_opaque_repair():
    # The h17 shape: the next cited item is a native repair whose single-source
    # oracle is one indivisible unit, so a lent first token would leave the
    # whole repair uncovered and block the later page view's own record.
    body = _two_page_item("#/texts/0", "Alpha beta gamma", "delta b epsilon")
    note = _item("#/texts/1", "B zeta eta", top=750)

    audit = _audit([
        ("Alpha beta gamma\n\nB zeta eta", [
            _scoped(body, 0),
            _lineage(note, transform="native_repair",
                     oracle_text="B zeta eta"),
        ]),
        ("delta b epsilon", [_scoped(body, 1)]),
    ], [body, note])

    _assert_exact(audit, 9)
    assert audit["transform_counts"] == {"native_repair": 1, "plain": 2}


def test_scoped_realignment_still_prefers_leaving_an_erased_marker_unmatched():
    # Excluding the omitted page view leaves the erased marker glyph as the
    # next greedy candidate; the scoped realignment keeps the marker retry.
    body = _two_page_item("#/texts/0", "Alpha beta gamma", "delta b epsilon")
    marker = _item("#/texts/1", "B", top=740, left=5, right=10)
    note = _item("#/texts/2", "B zeta eta", top=750)

    audit = _audit([
        ("Alpha beta gamma\n\nB zeta eta",
         [_scoped(body, 0), _lineage(marker), _scoped(note, 0)]),
        ("delta b epsilon", [_scoped(body, 1)]),
    ], [body, marker, note], eligible=[body, note])

    _assert_exact(audit, 9)


# --- Characterization: behavior the scoped realignment must not change. ---

def test_scoped_page_view_still_rejects_text_from_its_other_page():
    body = _two_page_item("#/texts/0", "Alpha beta gamma", "delta b epsilon")

    audit = _audit([
        ("Alpha beta gamma delta b epsilon", [_scoped(body, 0)]),
    ], [body])

    # The scope violation is still diagnosed as a lineage issue, not merely
    # as an unexplained output.
    assert audit["lineage_issues"] == [0]
    assert audit["output_coverage_issues"] == [0]


def test_greedy_scope_violation_without_an_in_scope_explanation_is_kept():
    body = _two_page_item("#/texts/0", "Alpha beta gamma", "delta b epsilon")
    note = _item("#/texts/1", "B zeta eta", top=750)

    audit = _audit([
        ("Alpha beta gamma delta\n\nB zeta eta",
         [_scoped(body, 0), _scoped(note, 0)]),
    ], [body, note])

    # Only the omitted page view explains "delta"; excluding it finds no
    # alignment, so today's out-of-scope alignment and diagnosis stand.
    assert audit["lineage_issues"] == [0]
    assert audit["output_coverage_issues"] == [0]


def test_erased_marker_retry_explanation_is_unchanged_for_a_scoped_record():
    # A first greedy alignment here takes both the omitted page-2 token and
    # the erased marker; the marker retry then finds an exact in-scope window.
    # An audit that passes today must keep that explanation, not replace it
    # with a scoped realignment that never touches the marker.
    spanning = _two_page_item("#/texts/0", "c", "d", top=800)
    other = _item("#/texts/1", "c", top=750)
    marker = _item("#/texts/2", "B", top=760, left=5, right=10)
    tail = _item("#/texts/3", "d b w", top=770)

    audit = _audit([
        ("c\n\nd b w", [
            _scoped(spanning, 0), _lineage(other), _lineage(marker),
            _lineage(tail),
        ]),
        ("c", [_scoped(spanning, 0)]),
        ("d", [_scoped(spanning, 1)]),
    ], [spanning, other, marker, tail], eligible=[spanning, other, tail])

    _assert_exact(audit, 6)
