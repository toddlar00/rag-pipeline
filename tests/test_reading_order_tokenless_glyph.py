"""A detached tokenless glyph must not block the single-wrap page rotation.

Docling can capture a page's lower half before its upper half.  The
single-wrap repair (``rag._repair_single_column_body_child_order`` and its
validator mirror ``heading_lineage._ordered_source_refs``) rotates such a page
intact only when the two captured runs are vertically disjoint.  A line-final
soft hyphen emitted as its own zero-token text item can sit beside a
lower-half paragraph yet be captured in the upper-half run, which alone broke
that proof.  The relaxation sets aside only such late-run glyphs, only when
the captured order inverts a token-bearing pair that same-page reading order
checks, and every near miss (including each boundary) still refuses.

Every fixture is synthetic.
"""

import copy
from types import SimpleNamespace

import pytest

import heading_lineage
import rag


PAGE = 7
SOFT_HYPHEN = "\N{SOFT HYPHEN}"
GROUP = "#/groups/0"


def _t(index):
    return f"#/texts/{index}"


HDR, A, B, C, D, E, L1, L2, L3, F, G, H, S = (_t(i) for i in range(13))
NOTE = _t(14)
# Captured order: prefix run (lower half), then late run (upper half).
PREFIX = [A, B, C, D]
LATE = [E, GROUP, F, G, H, S]
LATE_TEXT = [E, L1, F, G, H, S]
CAPTURED = [HDR, A, B, C, D, E, L1, L2, L3, F, G, H, S]
ROTATED = [HDR, E, L1, L2, L3, F, G, H, S, A, B, C, D]


def _item(ref, label, text, left, right, top, bottom):
    return {
        "self_ref": ref, "label": label, "content_layer": "body",
        "text": text, "children": [],
        "prov": [{"page_no": PAGE, "bbox": {
            "l": left, "r": right, "t": top, "b": bottom,
            "coord_origin": "BOTTOMLEFT"}}],
    }


def _document(*, late_list=True):
    """A 549x738 page whose lower half Docling captured first.

    The geometry follows the diagnosed page: prefix A (short text), B
    (paragraph), C (centred heading), D (paragraph); late E (centred
    heading), L (list or long text), F and G (headings), H (short centred
    text), and S, a lone soft hyphen 0.7 pt right of B inside B's band.
    """
    words = ("sample calibration step records the sensor drift and the "
             "operator confirms each reading before storage").split()
    texts = [
        _item(HDR, "page_header", "7 SAMPLE MANUAL", 90, 460, 725, 713),
        _item(A, "text", "Lower remark.", 117.0, 180.9, 211.6, 202.1),
        _item(B, "text", " ".join(words * 3), 117.0, 476.3, 194.6, 144.3),
        _item(C, "section_header", "Lower Sample Section",
              214.0, 380.0, 132.2, 122.7),
        _item(D, "text", " ".join(words * 2), 117.0, 479.9, 115.2, 78.5),
        _item(E, "section_header", "Upper Sample Overview",
              231.3, 362.6, 691.5, 682.6),
    ]
    if late_list:
        texts += [
            _item(L1, "list_item", " ".join(words), 138.0, 496.5, 670.2, 560),
            _item(L2, "list_item", " ".join(words), 138.0, 496.5, 550, 430),
            _item(L3, "list_item", " ".join(words), 138.0, 496.5, 420, 313.8),
        ]
    else:
        texts.append(_item(
            L1, "text", " ".join(words * 4), 138.0, 496.5, 670.2, 313.8))
    texts += [
        _item(F, "section_header", "First Upper Topic",
              238.9, 355.1, 273.3, 261.8),
        _item(G, "section_header", "Second Upper Topic",
              243.9, 350.1, 250.8, 238.4),
        _item(H, "text", "A short centred remark here.",
              233.3, 360.7, 232.0, 222.5),
        _item(S, "text", SOFT_HYPHEN, 477.0, 479.4, 167.4, 157.9),
    ]
    late_children = LATE if late_list else LATE_TEXT
    return {
        "texts": texts, "tables": [], "pictures": [],
        "groups": [{
            "self_ref": GROUP, "label": "list", "content_layer": "body",
            "children": [{"cref": ref} for ref in (L1, L2, L3)],
        }] if late_list else [],
        "body": {"children": [
            {"cref": ref} for ref in [HDR, *PREFIX, *late_children]]},
        "pages": {str(PAGE): {"size": {"width": 549, "height": 738}}},
    }


def _texts(document):
    return {item["self_ref"]: item for item in document["texts"]}


def _bbox(document, ref):
    return _texts(document)[ref]["prov"][0]["bbox"]


def _namespace(value):
    if isinstance(value, dict):
        return SimpleNamespace(**{
            key: _namespace(child) for key, child in value.items()})
    if isinstance(value, list):
        return [_namespace(child) for child in value]
    return value


def _docling(document):
    """A Docling-shaped object view of one serialized document."""
    view = _namespace({key: value for key, value in document.items()
                       if key != "pages"})
    view.pages = {int(key): _namespace(page)
                  for key, page in document["pages"].items()}
    return view


def _mirror_order(document):
    items, groups = heading_lineage._catalog(document)
    return heading_lineage._ordered_source_refs(document, items, groups, ())


def _repaired_order(document):
    view = _docling(document)
    result = rag._repair_single_column_body_child_order(view)
    return result, rag._docling_serialized_refs(view), view


# -- Near misses: each changes one premise and must still refuse ------------


def _observable_glyph(document):
    _texts(document)[S]["text"] = "1"


def _glyph_overlaps_paragraph(document):
    _bbox(document, S).update(l=470.0, r=476.0)


def _no_inverted_observable_pair(document):
    # The late text/list items move to a narrow margin lane: every late item
    # overlapping a prefix paragraph horizontally is then a heading.
    for ref in (L1, L2, L3, H):
        _bbox(document, ref).update(l=20.0, r=100.0)


def _late_run_only_glyphs(document):
    # Five tokenless glyphs form the late run (too many to insert); none is
    # order-observable, so no proof exists for that run.
    texts = _texts(document)
    for ref in (E, F, G, H):
        texts[ref].update(label="text", text=SOFT_HYPHEN)
        _bbox(document, ref).update(l=300.0, r=302.4)
    document["texts"] = [
        item for item in document["texts"]
        if item["self_ref"] not in {L1, L2, L3}]
    document["groups"] = []
    document["body"]["children"] = [
        {"cref": ref} for ref in [HDR, *PREFIX, E, F, G, H, S]]


def _unexplained_second_glyph(document):
    # A second glyph beside the late list (no prefix band to explain it).
    document["texts"].append(_item(
        _t(13), "text", SOFT_HYPHEN, 497.0, 499.4, 600.0, 590.5))
    document["body"]["children"].append({"cref": _t(13)})


def _late_item_crosses_prefix_top(document):
    # A token-bearing late item reaches into the prefix band: the runs are
    # not disjoint even without the glyph.
    _bbox(document, L3).update(b=205.0)


def _tokenless_heading(document):
    # Only a plain-text item can be set aside, never a heading.
    _texts(document)[S]["label"] = "section_header"


def _glyph_with_children(document):
    # Only a childless item can be set aside.
    _texts(document)[S]["children"] = [{"cref": _t(77)}]


def _prefix_run_glyph(document):
    # A glyph captured with the lower half that reaches above it stays in the
    # proof, so the ordinary refusal stands.
    _bbox(document, S).update(t=320.0, b=190.0)
    document["body"]["children"] = [
        {"cref": ref} for ref in [HDR, A, S, B, C, D, E, GROUP, F, G, H]]


def _glyphs_in_both_runs(document):
    # The diagnosed late-run glyph stays, but a second glyph captured with
    # the lower half reaches above it; that one still blocks the proof.
    document["texts"].append(_item(
        _t(13), "text", SOFT_HYPHEN, 477.0, 479.4, 320.0, 190.0))
    document["body"]["children"].insert(2, {"cref": _t(13)})


NEAR_MISSES = {
    "observable-glyph": _observable_glyph,
    "glyph-overlaps-paragraph": _glyph_overlaps_paragraph,
    "no-inverted-observable-pair": _no_inverted_observable_pair,
    "late-run-only-glyphs": _late_run_only_glyphs,
    "unexplained-second-glyph": _unexplained_second_glyph,
    "late-item-crosses-prefix-top": _late_item_crosses_prefix_top,
    "tokenless-heading": _tokenless_heading,
    "glyph-with-children": _glyph_with_children,
    "prefix-run-glyph": _prefix_run_glyph,
    "glyphs-in-both-runs": _glyphs_in_both_runs,
}


# -- The repaired page -------------------------------------------------------


@pytest.mark.parametrize("late_list", [True, False], ids=["list", "text"])
def test_tokenless_glyph_no_longer_blocks_the_intact_rotation(late_list):
    document = _document(late_list=late_list)
    view = _docling(document)
    captured = list(view.body.children)

    assert rag._repair_single_column_body_child_order(view) == (1, 10)
    late = LATE if late_list else LATE_TEXT
    assert [child.cref for child in view.body.children] == [
        HDR, *late, *PREFIX]
    # The rotation is intact: the glyph keeps its captured run and every
    # child object is the one Docling produced.
    assert all(any(child is original for original in captured)
               for child in view.body.children)
    repaired = list(view.body.children)

    assert rag._repair_single_column_body_child_order(view) == (0, 0)
    assert view.body.children == repaired


@pytest.mark.parametrize("late_list", [True, False], ids=["list", "text"])
def test_mirror_rotates_the_same_page_identically(late_list):
    document = _document(late_list=late_list)
    expected = ROTATED if late_list else [
        ref for ref in ROTATED if ref not in {L2, L3}]

    _, repaired, _ = _repaired_order(document)

    assert repaired == expected
    assert _mirror_order(document) == expected


@pytest.mark.parametrize("change", NEAR_MISSES.values(), ids=NEAR_MISSES)
def test_near_misses_still_refuse_in_both_mirrors(change):
    document = _document()
    change(document)
    captured = [child["cref"] for child in document["body"]["children"]]

    result, repaired, view = _repaired_order(document)

    assert result == (0, 0)
    assert [child.cref for child in view.body.children] == captured
    assert _mirror_order(document) == repaired


def test_group_members_alone_prove_the_inversion_in_both_mirrors():
    # With the centred remark moved aside, only list-group members overlap a
    # prefix paragraph, so each mirror must look inside the group.
    document = _document()
    _bbox(document, H).update(l=20.0, r=100.0)

    result, repaired, _ = _repaired_order(document)

    assert result == (1, 10)
    assert repaired == ROTATED
    assert _mirror_order(document) == ROTATED


def _single_pair_page(clearance):
    """Only NOTE, ``clearance`` pt above A's top, can prove the inversion.

    The late list and remark move to the margin and B and D start right of
    A's column, so NOTE over A is the page's one horizontally overlapping
    token-bearing pair.
    """
    document = _document()
    _no_inverted_observable_pair(document)
    for ref in (B, D):
        _bbox(document, ref).update(l=200.0)
    bottom = _bbox(document, A)["t"] + clearance
    document["texts"].append(_item(
        NOTE, "text", "A short synthetic note.", 117.0, 180.9, 220.0, bottom))
    document["body"]["children"].insert(-1, {"cref": NOTE})
    return document


@pytest.mark.parametrize(("clearance", "admitted"), [
    (0.4, False), (0.5, True),
], ids=["0.4pt-refuses", "0.5pt-admits"])
def test_the_inverted_pair_needs_half_a_point_of_clearance(
        clearance, admitted):
    document = _single_pair_page(clearance)
    captured = [*CAPTURED[:-1], NOTE, S]
    rotated = [HDR, E, L1, L2, L3, F, G, H, NOTE, S, A, B, C, D]

    result, repaired, _ = _repaired_order(document)

    assert result == ((1, 11) if admitted else (0, 0))
    assert repaired == (rotated if admitted else captured)
    assert _mirror_order(document) == repaired


@pytest.mark.parametrize(("reach", "admitted"), [
    (4.0, True), (4.1, False),
], ids=["4.0pt-admits", "4.1pt-refuses"])
def test_late_items_may_reach_the_ordinary_tolerance_into_the_prefix(
        reach, admitted):
    # The disjointness proof without the glyph keeps the ordinary 4 pt
    # tolerance: the late list may end at most that far below A's top.
    document = _document()
    _bbox(document, L3).update(b=_bbox(document, A)["t"] - reach)

    result, repaired, _ = _repaired_order(document)

    assert result == ((1, 10) if admitted else (0, 0))
    assert repaired == (ROTATED if admitted else CAPTURED)
    assert _mirror_order(document) == repaired


@pytest.mark.parametrize(("top", "admitted"), [
    (144.4, True), (144.3, False), (144.0, False),
], ids=["overlap-0.1pt-admits", "touching-refuses", "gap-0.3pt-refuses"])
def test_the_glyph_must_overlap_a_prefix_item_vertically(top, admitted):
    # B's bottom edge is at 144.3; the glyph sits just below it.
    document = _document()
    _bbox(document, S).update(t=top, b=top - 9.5)

    result, repaired, _ = _repaired_order(document)

    assert result == ((1, 10) if admitted else (0, 0))
    assert repaired == (ROTATED if admitted else CAPTURED)
    assert _mirror_order(document) == repaired


def test_short_late_run_keeps_the_insertion_branch():
    # A late run the short-label insertion already accepted keeps that
    # decision even though the relaxed rotation proof would also hold.
    document = _document()
    document["texts"] = [
        item for item in document["texts"]
        if item["self_ref"] not in {L1, L2, L3, F, G}]
    document["groups"] = []
    document["body"]["children"] = [
        {"cref": ref} for ref in [HDR, *PREFIX, E, H, S]]
    inserted = [HDR, E, H, A, B, S, C, D]

    result, repaired, _ = _repaired_order(document)

    assert result == (1, 7)
    assert repaired == inserted
    assert _mirror_order(document) == inserted


def test_ordinary_disjoint_rotation_is_unchanged_by_a_glyph_in_band():
    # With the glyph inside the late run's own band the ordinary proof holds,
    # so the page rotates exactly as before the relaxation existed.
    document = _document()
    _bbox(document, S).update(l=497.0, r=499.4, t=600.0, b=590.5)

    result, repaired, _ = _repaired_order(document)

    assert result == (1, 10)
    assert repaired == ROTATED
    assert _mirror_order(document) == ROTATED


# -- End to end: heading lineage binds once the page rotates -----------------


def _record(refs, path):
    return {
        "text": "Body passage",
        "metadata": {
            "section_path": path,
            "headings": [],
            "content_source": "body",
            "source_items": [
                {"ref": ref, "label": "text", "spans": []} for ref in refs],
        },
    }


def test_heading_lineage_binds_the_lower_half_under_the_upper_headings():
    document = _document()
    upper = "Upper Sample Overview > First Upper Topic > Second Upper Topic"
    records = [
        _record([A], upper),
        _record([B], upper),
        _record([D], upper + " > Lower Sample Section"),
        _record([L1], "Upper Sample Overview"),
    ]

    expected = heading_lineage.attach_heading_bindings(document, records)
    audit = heading_lineage.audit_heading_bindings(document, records)

    assert expected["source_scope_paths"] == [
        [E, F, G], [E, F, G], [E, F, G, C], [E]]
    assert audit["display_binding_records"] == []
    assert audit["missing_refs"] == []
    assert audit["missing_direct_refs"] == []

    # The same records against the refused near miss keep failing: the
    # relaxation, not the records, is what binds the lower half.
    refused = copy.deepcopy(document)
    _glyph_overlaps_paragraph(refused)
    audit = heading_lineage.audit_heading_bindings(refused, records)
    assert audit["display_binding_records"] == [0, 1, 2]
