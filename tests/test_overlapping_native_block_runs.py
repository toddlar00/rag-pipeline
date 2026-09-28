"""Multi-block overlapping-group recovery proofs.

A PDF text layer can split one paragraph into consecutive native blocks while
Docling publishes a detached fragment of it after lower text.  A first
chunking pass keeps the previous output and only defers such a group when the
text layer shows one contained, ordered run with identical lexemes.  The group
is admitted only by one replay, and only after that published output failed
``same_page_reading_order`` with a violation whose ``before_ref`` is a member
on the group's page.  Output that passes the gates is therefore never changed.
The replay control itself is pinned in ``test_native_group_order_replay.py``.
"""

from contextlib import contextmanager
from types import SimpleNamespace

import pymupdf
import pytest

import rag


PART_A = (
    "The agency opened a formal hearing on the permit and asked the "
    "applicant for a detailed record of every thermal discharge event "
    "that occurred during the prior season of plant operation."
)
PART_B = (
    "Counsel then requested an evidentiary hearing before the review "
    "board convened."
)
LATER = (
    "A later independent paragraph discusses the statute and its "
    "procedural requirements in general terms for all agencies."
)
PARAGRAPH_A = pymupdf.Rect(72, 100, 420, 150)
PARAGRAPH_B = pymupdf.Rect(72, 150, 420, 200)
LATER_PARAGRAPH = pymupdf.Rect(72, 230, 420, 290)


def _build_pdf(tmp_path, variant="stacked"):
    """Write one page whose text layer holds the paragraph as two blocks."""
    path = tmp_path / f"{variant}.pdf"
    pdf = pymupdf.open()
    page = pdf.new_page(width=612, height=792)
    if variant == "reversed_stream":
        page.insert_textbox(PARAGRAPH_B, PART_B, fontsize=11)
        page.insert_textbox(PARAGRAPH_A, PART_A, fontsize=11)
        page.insert_textbox(LATER_PARAGRAPH, LATER, fontsize=11)
    elif variant == "continued":
        page.insert_textbox(PARAGRAPH_A, PART_A, fontsize=11)
        page.insert_textbox(
            pymupdf.Rect(72, 150, 420, 260), PART_B + "\n" + LATER,
            fontsize=11)
    else:
        page.insert_textbox(PARAGRAPH_A, PART_A, fontsize=11)
        page.insert_textbox(PARAGRAPH_B, PART_B, fontsize=11)
        page.insert_textbox(LATER_PARAGRAPH, LATER, fontsize=11)
    words = page.get_text("words", sort=True)
    pdf.save(path)
    pdf.close()
    return path, words


def _box(words):
    return (
        min(word[0] for word in words), min(word[1] for word in words),
        max(word[2] for word in words), max(word[3] for word in words),
    )


def _item(ref, words, text=None):
    box = _box(words)
    return SimpleNamespace(
        self_ref=ref, label="text", parent=SimpleNamespace(cref="#/body"),
        text=text or " ".join(str(word[4]) for word in words),
        prov=[SimpleNamespace(
            page_no=1,
            bbox=SimpleNamespace(
                l=box[0], t=box[1], r=box[2], b=box[3],
                coord_origin="TOPLEFT"),
        )],
    )


def _docling_items(words, *, altered_lexeme=False):
    """Split the paragraph like h03 p4: a host plus a detached cluster.

    The detached item holds the first word of the paragraph's penultimate line
    and its whole last line, so its box overlaps the host's box.
    """
    later_start = next(
        index for index, word in enumerate(words)
        if word[4] == "A" and index and words[index - 1][4] == "convened.")
    paragraph, later = words[:later_start], words[later_start:]
    lines = list(dict.fromkeys(
        (int(word[5]), int(word[6])) for word in paragraph))
    penultimate_first = next(
        word for word in paragraph
        if (int(word[5]), int(word[6])) == lines[-2])
    detached = [
        word for word in paragraph
        if (int(word[5]), int(word[6])) == lines[-1]
        or word is penultimate_first
    ]
    host = [
        word for word in paragraph
        if not any(word is fragment for fragment in detached)
    ]
    host_text = " ".join(str(word[4]) for word in host)
    if altered_lexeme:
        host_text = host_text.replace("thermal", "thermic", 1)
    return (
        _item("#/texts/0", host, host_text),
        _item("#/texts/1", later),
        _item("#/texts/2", detached),
    )


def _recover(path, texts, **options):
    return rag._recover_overlapping_native_text_groups(
        SimpleNamespace(texts=list(texts)), path, **options)


def _summary(recoveries):
    return [(entry.refs, entry.page, entry.text) for entry in recoveries]


def test_fixture_group_union_holds_two_native_blocks(tmp_path):
    """Pin the fixture shape: the one-native-block gate applies."""
    path, words = _build_pdf(tmp_path)
    host, _following, detached = _docling_items(words)

    with pymupdf.open(path) as pdf:
        page = pdf[0]
        union = (
            rag._pdf_clip_for_provenance(page, host.prov[0])
            | rag._pdf_clip_for_provenance(page, detached.prov[0]))
        blocks = [
            block for block in page.get_text("blocks", clip=union, sort=True)
            if block[6] == 0 and str(block[4]).strip()
        ]

    assert len({int(word[5]) for word in words}) == 3
    assert len(blocks) == 2


def test_docling_emission_order_alone_admits_no_multi_block_group(tmp_path):
    """Chunk preparation can reorder items, so emission order is no proof."""
    path, words = _build_pdf(tmp_path)
    host, following, detached = _docling_items(words)
    document = SimpleNamespace(texts=[host, following, detached])
    document.iterate_items = lambda: (
        (item, 1) for item in (host, following, detached))

    assert rag._recover_overlapping_native_text_groups(document, path) == ()


def test_proven_multi_block_group_is_deferred_without_a_named_violation(
        tmp_path):
    path, words = _build_pdf(tmp_path)
    host, following, detached = _docling_items(words)
    deferred = []

    assert _recover(
        path, [host, following, detached], deferred_groups=deferred) == ()
    assert _summary(deferred) == [(
        (host.self_ref, detached.self_ref), 1, PART_A + " " + PART_B,
    )]


def test_multi_block_group_recovered_when_a_violation_names_a_member(
        tmp_path):
    path, words = _build_pdf(tmp_path)
    host, following, detached = _docling_items(words)

    for member in (detached, host):
        deferred = []
        recovered = _recover(
            path, [host, following, detached],
            reading_order_violations=frozenset({(member.self_ref, 1)}),
            deferred_groups=deferred)

        assert _summary(recovered) == [(
            (host.self_ref, detached.self_ref), 1, PART_A + " " + PART_B,
        )]
        assert deferred == []


def test_violation_admits_only_a_member_on_the_group_page(tmp_path):
    path, words = _build_pdf(tmp_path)
    host, following, detached = _docling_items(words)

    for violation in ((detached.self_ref, 2), (following.self_ref, 1)):
        deferred = []

        assert _recover(
            path, [host, following, detached],
            reading_order_violations=frozenset({violation}),
            deferred_groups=deferred) == ()
        assert [entry.refs for entry in deferred] == [
            (host.self_ref, detached.self_ref)]


@pytest.mark.parametrize(("variant", "altered_lexeme"), [
    # The text-layer block continues past the union (h18 shape).
    ("continued", False),
    # One changed lexeme still passes the 95% gate but not this proof.
    ("stacked", True),
    # Pins the ordered-run proof as a whole; its isolated stream-order and
    # line-center checks are pinned by the unit tests below.
    ("reversed_stream", False),
])
def test_named_multi_block_group_still_requires_every_text_layer_proof(
        tmp_path, variant, altered_lexeme):
    path, words = _build_pdf(tmp_path, variant)
    host, following, detached = _docling_items(
        words, altered_lexeme=altered_lexeme)
    deferred = []

    assert _recover(
        path, [host, following, detached],
        reading_order_violations=frozenset({(detached.self_ref, 1)}),
        deferred_groups=deferred) == ()
    assert deferred == []


def test_named_multi_block_group_still_stops_at_a_crossing_heading(
        tmp_path):
    """The pre-existing section-heading gate also binds multi-block groups."""
    path, words = _build_pdf(tmp_path)
    host, following, detached = _docling_items(words)
    # Recovery compares heading provenance with the union flipped to
    # bottom-left page coordinates; this box crosses the paragraph.
    heading = SimpleNamespace(
        self_ref="#/texts/3", label="section_header",
        parent=SimpleNamespace(cref="#/body"), text="Formal Hearings",
        prov=[SimpleNamespace(page_no=1, bbox=SimpleNamespace(
            l=72.0, t=660.0, r=300.0, b=640.0,
            coord_origin="BOTTOMLEFT"))],
    )
    named = frozenset({(detached.self_ref, 1)})
    deferred = []

    assert [entry.refs for entry in _recover(
        path, [host, following, detached],
        reading_order_violations=named)] == [
            (host.self_ref, detached.self_ref)]
    assert _recover(
        path, [host, following, detached, heading],
        reading_order_violations=named, deferred_groups=deferred) == ()
    assert deferred == []


def test_bound_enrichments_carry_deferred_groups_to_the_replay(
        monkeypatch, tmp_path):
    path, words = _build_pdf(tmp_path)
    host, following, detached = _docling_items(words)

    @contextmanager
    def snapshot(*_args, **_kwargs):
        yield SimpleNamespace(
            pdf=SimpleNamespace(path=path), source_path=path,
            manifest_input=lambda: None)

    monkeypatch.setattr(rag, "_open_docling_source_pdf_snapshot", snapshot)
    document = SimpleNamespace(
        texts=[host, following, detached], tables=[], pictures=[])
    group = ((host.self_ref, detached.self_ref), 1, PART_A + " " + PART_B)

    first = rag._recover_bound_source_enrichments(
        document, tmp_path / "doc.json", None)
    replay = rag._recover_bound_source_enrichments(
        document, tmp_path / "doc.json", None,
        reading_order_violations=frozenset({(detached.self_ref, 1)}))

    assert _summary(first.deferred_text_groups) == [group]
    assert group not in _summary(first.text_group_recoveries)
    assert group in _summary(replay.text_group_recoveries)
    assert replay.deferred_text_groups == ()


def _word(x0, y0, text, block, line, number, *, width=40.0):
    return (x0, y0, x0 + width, y0 + 11.0, text, block, line, number)


RUN_UNION = pymupdf.Rect(60, 90, 460, 160)


def test_ordered_run_accepts_contained_stacked_blocks():
    words = [
        _word(72, 100, "one", 0, 0, 0), _word(120, 100, "two", 0, 0, 1),
        _word(72, 114, "three", 0, 1, 0),
        _word(72, 128, "four", 1, 0, 0), _word(120, 128, "five", 1, 0, 1),
    ]

    assert rag._native_blocks_form_one_ordered_run(words, RUN_UNION) is True


def test_ordered_run_rejects_a_single_block():
    words = [
        _word(72, 100, "one", 0, 0, 0), _word(72, 114, "two", 0, 1, 0),
    ]

    assert rag._native_blocks_form_one_ordered_run(words, RUN_UNION) is False


def test_ordered_run_rejects_block_continuing_outside_union():
    words = [
        _word(72, 100, "one", 0, 0, 0),
        _word(72, 128, "two", 1, 0, 0),
        _word(72, 200, "outside", 1, 1, 0),
    ]

    assert rag._native_blocks_form_one_ordered_run(words, RUN_UNION) is False


def test_ordered_run_rejects_stream_order_that_disagrees_with_geometry():
    """Isolates the stream-order check: line centers still descend."""
    words = [
        _word(72, 100, "second", 0, 0, 1), _word(120, 100, "first", 0, 0, 0),
        _word(72, 128, "next", 1, 0, 0),
    ]

    assert sorted(
        words, key=lambda word: (word[5], word[6], word[7])) != words
    assert rag._native_blocks_form_one_ordered_run(words, RUN_UNION) is False


def test_ordered_run_rejects_side_by_side_lanes_on_one_baseline():
    """Isolates the line-center check: stream order equals geometry here."""
    words = [
        _word(72, 100, "left", 0, 0, 0), _word(120, 100, "lane", 0, 0, 1),
        _word(300, 100, "right", 1, 0, 0), _word(348, 100, "lane", 1, 0, 1),
    ]

    assert sorted(
        words, key=lambda word: (word[5], word[6], word[7])) == words
    assert rag._native_blocks_form_one_ordered_run(words, RUN_UNION) is False
