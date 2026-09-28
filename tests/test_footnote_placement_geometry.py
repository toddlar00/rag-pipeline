"""Footnote sidecar placement by the geometry the fidelity audit observes.

``_reorder_footnote_sidecars`` places a note by comparing page boxes.  A
plain tokenless entry, such as an ornament row printed among the notes,
widens its record's box although the audit registers no occurrence for it.
On ``observed_geometry_pages`` (only ever a gate-driven replay's) both boxes
are built from the observed lineage; every other page keeps today's
placement.  ``test_footnote_placement_replay`` pins the trigger and the
replay.  All text is synthetic.
"""

import pytest

import rag


REASON = rag._quality_core.PAGE_ORDER_REASON_FIELD
CONTINUATION = rag._quality_core.FOOTNOTE_AFTER_CONTINUATION_REASON
PAGES = frozenset({5})
ORNAMENT = (5, (200, 114, 220, 105), 0)
BROKEN = [3]


def _record(name, entries, *, source="body", text=None):
    """A record with one ``(page, BOTTOMLEFT box, tokens)`` entry per item."""
    pages = sorted({page for page, _box, _count in entries})
    return {"text": text or f"Synthetic {source} {name}.", "metadata": {
        "content_type": source, "content_source": source,
        "page_start": pages[0], "page_end": pages[-1],
        "source_items": [{
            "ref": f"#/texts/{name}{index}",
            "label": "text" if source == "body" else source,
            "transform": "text",
            "oracle_lexical_count": count,
            "spans": [{"provenance_index": 0, "page": page,
                       "bbox": list(box), "origin": "BOTTOMLEFT"}],
            "scope": {"provenance_indexes": [0]},
        } for index, (page, box, count) in enumerate(entries)]}}


def _note(name, box, *rows):
    return _record(name, [(5, box, 3), *rows], source="footnote")


def _break(record, index):
    record["metadata"]["source_items"][index]["scope"][
        "provenance_indexes"] = BROKEN
    return record


def _page(*, ornament=True, count=9, extra=(), notes=None):
    """A page-5 passage continuing onto page 6, the page-6 closing, notes.

    The ornament sits between the upper notes U1, U2 and the lower note L1.
    The passage ends without punctuation and the closing opens lowercase, so
    a note placed semantically follows the closing.
    """
    passage = _record("A", [
        (5, (50, 600, 400, 300), count), *((ORNAMENT,) if ornament else ()),
        (6, (50, 700, 400, 600), 7)], text="Synthetic passage continues")
    closing = _record("B", [(6, (50, 580, 400, 500), 8)],
                      text="and closes on the next page.")
    if notes is None:
        notes = [_note("U1", (60, 140, 300, 127)),
                 _note("U2", (60, 127, 380, 114)),
                 _note("L1", (60, 102, 380, 89))]
    return [passage, *extra, closing, *notes]


def _names(records):
    return [record["metadata"]["source_items"][0]["ref"][8:-1]
            for record in records]


def _reasons(records):
    return {name: record["metadata"][REASON]
            for name, record in zip(_names(records), records)
            if REASON in record["metadata"]}


def _both(build):
    """Place fresh copies without and with the replay's page set."""
    return (rag._reorder_footnote_sidecars(build()),
            rag._reorder_footnote_sidecars(
                build(), observed_geometry_pages=PAGES))


@pytest.mark.parametrize("pages", [frozenset(), frozenset({6, 7})])
def test_first_pass_placement_is_unchanged(pages):
    """The widened passage box overlaps U1 and U2; they follow B."""
    placed = rag._reorder_footnote_sidecars(
        _page(), observed_geometry_pages=pages)

    assert _names(placed) == ["A", "L1", "B", "U1", "U2"]
    assert _reasons(placed) == dict.fromkeys(("U1", "U2"), CONTINUATION)


# A single-token passage entry is observed as well.
@pytest.mark.parametrize("count", [1, 9])
def test_replay_places_notes_on_named_pages_by_observed_geometry(count):
    placed = rag._reorder_footnote_sidecars(
        _page(count=count), observed_geometry_pages=PAGES)

    assert _names(placed) == ["A", "U1", "U2", "L1", "B"]
    assert _reasons(placed) == {}


def test_replay_is_identical_where_no_tokenless_entry_widens_a_box():
    first, replay = _both(lambda: _page(ornament=False))

    assert replay == first
    assert _names(replay) == ["A", "U1", "U2", "L1", "B"]


def test_tokenless_row_in_a_note_no_longer_widens_the_note():
    """W's own tokenless row sits inside the passage's box."""
    def build():
        return _page(ornament=False, notes=[
            _note("W", (60, 140, 300, 127), (5, (60, 450, 300, 440), 0))])

    first, replay = _both(build)

    assert (_names(first), _reasons(first)) == (
        ["A", "B", "W"], {"W": CONTINUATION})
    assert (_names(replay), _reasons(replay)) == (["A", "W", "B"], {})


def test_note_the_audit_does_not_observe_keeps_its_placement():
    first, replay = _both(lambda: _page(ornament=False, notes=[
        _record("T", [(5, (60, 140, 300, 127), 0)], source="footnote")]))

    assert replay == first
    assert _names(replay) == ["A", "T", "B"]


def test_body_the_audit_does_not_observe_on_the_page_does_not_constrain():
    """C prints only a tokenless row beside the notes, not above them."""
    beside = _record("C", [(5, (500, 114, 520, 105), 0)],
                     text="Synthetic ornament row")

    first, replay = _both(lambda: _page(ornament=False, extra=(beside,)))

    assert _names(first) == ["A", "C", "B", "U1", "U2", "L1"]
    assert _names(replay) == ["A", "U1", "U2", "L1", "C", "B"]


def test_zero_width_opaque_body_entry_still_constrains():
    """The audit registers a zero-width opaque recovery's occurrence."""
    def build():
        records = _page(ornament=True)
        records[0]["metadata"]["source_items"][1]["transform"] = "figure"
        return records

    first, replay = _both(build)

    assert replay == first


@pytest.mark.parametrize("build", [
    # The passage's tokened entry is malformed.
    lambda: [_break(_page()[0], 0), *_page()[1:]],
    # A body record whose only, tokenless, entry is malformed.
    lambda: _page(ornament=False, extra=(_break(_record(
        "C", [(5, (60, 330, 380, 320), 0)], text="Synthetic interlude"), 0),)),
    # A note whose tokenless entry is malformed.
    lambda: _page(ornament=False, notes=[_break(_note(
        "N", (60, 140, 300, 127), (5, (60, 450, 300, 440), 0)), 1)]),
])
def test_malformed_lineage_still_fails_closed_on_named_pages(build):
    first, replay = _both(build)

    assert replay == first


@pytest.mark.parametrize(("entries", "expected"), [
    ([(5, (50, 600, 400, 300), 9), ORNAMENT], (50.0, -600.0, 400.0, -300.0)),
    ([(5, (50, 600, 400, 300), 1), ORNAMENT], (50.0, -600.0, 400.0, -300.0)),
    ([ORNAMENT], None),
    ([(6, (50, 700, 400, 600), 7), ORNAMENT], None),
])
def test_observed_box_keeps_only_entries_with_source_tokens(entries, expected):
    assert rag._record_observed_source_box(
        _record("A", entries), 5) == expected


@pytest.mark.parametrize("transform", sorted(
    rag._source_fidelity_core.OPAQUE_TRANSFORMS))
def test_observed_box_keeps_zero_width_opaque_entries(transform):
    record = _record("A", [ORNAMENT])
    record["metadata"]["source_items"][0]["transform"] = transform

    assert rag._record_observed_source_box(record, 5) == (
        200.0, -114.0, 220.0, -105.0)


@pytest.mark.parametrize("count", [True, "9", None, -1, 0])
def test_observed_box_rejects_counts_that_are_not_positive_integers(count):
    record = _record("A", [(5, (50, 600, 400, 300), 9)])
    record["metadata"]["source_items"][0]["oracle_lexical_count"] = count

    assert rag._record_observed_source_box(record, 5) is None


@pytest.mark.parametrize("damage", [
    lambda items: items.append("not an entry"),
    lambda items: items[0]["scope"].update(provenance_indexes=BROKEN),
])
def test_observed_box_fails_closed_on_a_malformed_entry(damage):
    record = _record("A", [(5, (50, 600, 400, 300), 9)])
    damage(record["metadata"]["source_items"])

    assert rag._record_observed_source_box(record, 5) is None
