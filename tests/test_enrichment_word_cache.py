"""Source enrichment reads each page's sorted native words once.

The five native text-group stages of ``_recover_bound_source_enrichments``
all read unclipped ``get_text("words", sort=True)`` from the same private,
hash-verified PDF snapshot, and four of them recompute the document's
hard-hyphen attestations.  These tests pin the stage and enrichment outputs
on one synthetic book so that sharing that work cannot change a result.
Every fixture is synthetic.
"""

from contextlib import contextmanager
from pathlib import Path
from types import SimpleNamespace

import pytest

import rag

pymupdf = pytest.importorskip("pymupdf")

_BODY = SimpleNamespace(cref="#/body")

# Disjoint section flow (pages 1-3).
_PREVIOUS = "The report found a shortage of"
_TOP = "care across the state"
_DISPLACED = "This finding changed policy."
_HEADING = "D. A NEW SECTION"
_BOTTOM = "The later section continues with its"
_NEXT = "emphasis on better outcomes."
# Cross-page hard-hyphen word (pages 4-5) and its attesting occurrence.
_CROSS_LEFT = "An imperfect fault-"
_CROSS_RIGHT = "tolerant design applies here."
# Adjacent same-page discretionary hyphen (page 6).
_ADJACENT_LEFT = "The telephone num-"
_ADJACENT_RIGHT = "ber appears here."
# Detached late fragment inside one native line (page 7).
_DETACHED_NATIVE = (
    "The monitor carries the task of verifying each recorded signal in "
    "every scheduled device audit.")
# Native text repair and a hard-hyphen word with a plain twin (page 8).
_REPAIR_NATIVE = "The auditor reviewed the telephone records carefully."
_REPAIR_SOURCE = "The auditor reviewed the tele phone records carefully."
_TWINS = "Both cooperate and co-operate appear."
# One paragraph split into two stacked native blocks (page 9).
_PART_A = (
    "The agency opened a formal hearing on the permit and asked the "
    "applicant for a detailed record of every thermal discharge event "
    "that occurred during the prior season of plant operation.")
_PART_B = (
    "Counsel then requested an evidentiary hearing before the review "
    "board convened.")
_LATER = (
    "A later independent paragraph discusses the statute and its "
    "procedural requirements in general terms for all agencies.")
# A page break inside the hard-hyphen word whose plain twin is native
# (pages 10-11): neither the adjacent nor the cross-page stage may join it.
_TWIN_LEFT = "The firms co-"
_TWIN_RIGHT = "operate on the project."


def _provenance(page: int, box, charspan=None):
    left, top, right, bottom = box
    value = SimpleNamespace(
        page_no=page,
        bbox=SimpleNamespace(
            l=left, t=top, r=right, b=bottom, coord_origin="TOPLEFT"))
    if charspan is not None:
        value.charspan = charspan
    return value


def _item(ref: str, text: str, *provenance, label: str = "text",
          parent=_BODY):
    item = SimpleNamespace(
        self_ref=ref, label=label, text=text, prov=list(provenance))
    if parent is not None:
        item.parent = parent
    return item


def _word_box(words):
    return (
        min(word[0] for word in words), min(word[1] for word in words),
        max(word[2] for word in words), max(word[3] for word in words))


def _build_book(directory: Path):
    """Write an eleven-page PDF in which every native group stage fires."""
    path = directory / "book.pdf"
    with pymupdf.open() as pdf:
        for _ in range(11):
            pdf.new_page(width=612, height=792)
        pdf[0].insert_text((72, 100), _PREVIOUS)
        pdf[1].insert_text((72, 80), _TOP)
        pdf[1].insert_text((72, 130), _DISPLACED)
        pdf[1].insert_text((72, 250), _HEADING)
        pdf[1].insert_text((72, 350), _BOTTOM)
        pdf[2].insert_text((72, 80), _NEXT)
        pdf[3].insert_text((72, 100), _CROSS_LEFT)
        pdf[3].insert_text((72, 150), "fault-tolerant")
        pdf[4].insert_text((72, 80), _CROSS_RIGHT)
        pdf[5].insert_text((72, 100), _ADJACENT_LEFT)
        pdf[5].insert_text((72, 125), _ADJACENT_RIGHT)
        pdf[5].insert_text((72, 200), "Another number is listed.")
        pdf[6].insert_textbox(
            pymupdf.Rect(72, 80, 540, 150), _DETACHED_NATIVE, fontsize=11)
        detached_words = pdf[6].get_text("words", sort=True)
        pdf[7].insert_text((72, 100), _REPAIR_NATIVE)
        pdf[7].insert_text((72, 200), _TWINS)
        pdf[8].insert_textbox(
            pymupdf.Rect(72, 100, 420, 150), _PART_A, fontsize=11)
        pdf[8].insert_textbox(
            pymupdf.Rect(72, 150, 420, 200), _PART_B, fontsize=11)
        pdf[8].insert_textbox(
            pymupdf.Rect(72, 230, 420, 290), _LATER, fontsize=11)
        run_words = pdf[8].get_text("words", sort=True)
        pdf[9].insert_text((72, 700), _TWIN_LEFT)
        pdf[10].insert_text((72, 80), _TWIN_RIGHT)
        pdf.save(path)

    candidate = _BOTTOM + " " + _TOP
    texts = [
        _item("#/texts/1", _PREVIOUS,
              _provenance(1, (68, 84, 500, 106), [0, len(_PREVIOUS)]),
              parent=None),
        _item("#/texts/2", _HEADING,
              _provenance(2, (68, 234, 500, 256), [0, len(_HEADING)]),
              label="section_header", parent=None),
        _item("#/texts/3", candidate,
              _provenance(2, (68, 334, 500, 356), [0, len(_BOTTOM)]),
              _provenance(2, (68, 64, 500, 86),
                          [len(_BOTTOM) + 1, len(candidate)]),
              parent=None),
        _item("#/texts/4", _DISPLACED,
              _provenance(2, (68, 114, 500, 136), [0, len(_DISPLACED)]),
              parent=None),
        _item("#/texts/5", _NEXT,
              _provenance(3, (68, 64, 500, 86), [0, len(_NEXT)]),
              parent=None),
        _item("#/texts/6", _CROSS_LEFT, _provenance(4, (68, 84, 500, 106))),
        _item("#/texts/7", _CROSS_RIGHT, _provenance(5, (68, 64, 500, 86))),
        _item("#/texts/8", _ADJACENT_LEFT,
              _provenance(6, (68, 84, 500, 106))),
        _item("#/texts/9", _ADJACENT_RIGHT,
              _provenance(6, (68, 109, 500, 131))),
    ]

    verifying = next(
        word for word in detached_words if word[4] == "verifying")
    texts += [
        _item("#/texts/10", " ".join(
            str(word[4]) for word in detached_words
            if word[4] != "verifying"),
              _provenance(7, _word_box(detached_words))),
        _item("#/texts/11", "A later independent paragraph.",
              _provenance(7, (72.0, 250.0, 300.0, 270.0))),
        _item("#/texts/12", "verifying",
              _provenance(7, tuple(verifying[:4]))),
        _item("#/texts/13", _REPAIR_SOURCE,
              _provenance(8, (68, 84, 500, 106))),
    ]

    later_start = next(
        index for index, word in enumerate(run_words)
        if word[4] == "A" and index and run_words[index - 1][4] == "convened.")
    paragraph, later = run_words[:later_start], run_words[later_start:]
    lines = list(dict.fromkeys(
        (int(word[5]), int(word[6])) for word in paragraph))
    penultimate_first = next(
        word for word in paragraph
        if (int(word[5]), int(word[6])) == lines[-2])
    detached = [
        word for word in paragraph
        if (int(word[5]), int(word[6])) == lines[-1]
        or word is penultimate_first]
    host = [
        word for word in paragraph
        if not any(word is fragment for fragment in detached)]
    texts += [
        _item("#/texts/14", " ".join(str(word[4]) for word in host),
              _provenance(9, _word_box(host))),
        _item("#/texts/15", " ".join(str(word[4]) for word in later),
              _provenance(9, _word_box(later))),
        _item("#/texts/16", " ".join(str(word[4]) for word in detached),
              _provenance(9, _word_box(detached))),
        _item("#/texts/17", _TWIN_LEFT,
              _provenance(10, (68, 680, 500, 710))),
        _item("#/texts/18", _TWIN_RIGHT,
              _provenance(11, (68, 60, 500, 90))),
    ]
    document = SimpleNamespace(
        texts=texts, groups=[], pictures=[], tables=[], key_value_items=[],
        form_items=[])
    return document, path


def _snapshot(monkeypatch, path: Path) -> None:
    @contextmanager
    def snapshot(*_args, **_kwargs):
        yield SimpleNamespace(
            pdf=SimpleNamespace(path=path), source_path=path,
            manifest_input=lambda: None)

    monkeypatch.setattr(rag, "_open_docling_source_pdf_snapshot", snapshot)


def _enrich(document, directory: Path, **options):
    return rag._recover_bound_source_enrichments(
        document, directory / "book.json", None, **options)


def _groups(recoveries):
    return [(entry.refs, entry.page, entry.text) for entry in recoveries]


def _run_stages(document, path: Path, **cache):
    """Run the five native text-group stages in pipeline order."""
    edits: dict[str, tuple[tuple[str, str], ...]] = {}
    rebuild: set[str] = set()
    retries: dict[str, object] = {}
    overrides = rag._recover_native_text_repairs(
        document, path, repair_edits=edits, rebuild_refs=rebuild,
        structural_ranges=None, letter_spaced_retries=retries, **cache)
    deferred: list[object] = []
    overlapping = rag._recover_overlapping_native_text_groups(
        document, path, structural_ranges=None,
        reading_order_violations=frozenset(), deferred_groups=deferred,
        **cache)
    claimed = {ref for entry in overlapping for ref in entry.refs}
    adjacent = rag._recover_adjacent_native_split_word_groups(
        document, path, text_overrides=overrides, structural_ranges=None,
        claimed_refs=set(claimed), **cache)
    claimed.update(ref for entry in adjacent for ref in entry.refs)
    cross_page = rag._recover_cross_page_native_word_groups(
        document, path, text_overrides=overrides, structural_ranges=None,
        claimed_refs=set(claimed), **cache)
    disjoint = rag._recover_disjoint_section_flow_groups(
        document, path, structural_ranges=None, **cache)
    return {
        "native": (overrides, edits, rebuild, retries),
        "overlapping": (_groups(overlapping), _groups(deferred)),
        "adjacent": _groups(adjacent),
        "cross_page": _groups(cross_page),
        "disjoint": _groups(disjoint),
    }


def _inject_page_fault(monkeypatch, page_number: int, *, during=None):
    """Fail one page's unclipped sorted-word extraction.

    The fault is persistent, or holds only while the ``during`` stage runs.
    """
    original = pymupdf.Page.get_text
    active = [during is None]

    def get_text(self, option="text", *args, **kwargs):
        if (active[-1] and option == "words" and not args
                and kwargs == {"sort": True}
                and self.number == page_number - 1):
            raise RuntimeError(f"injected fault on page {page_number}")
        return original(self, option, *args, **kwargs)

    monkeypatch.setattr(pymupdf.Page, "get_text", get_text)
    if during is not None:
        stage = getattr(rag, during)

        def scoped(*args, **kwargs):
            active.append(True)
            try:
                return stage(*args, **kwargs)
            finally:
                active.pop()

        monkeypatch.setattr(rag, during, scoped)


# -- Golden values captured from main 3aede7d ---------------------------------

_OVERRIDES = {
    "#/texts/13": "The auditor reviewed the telephone records carefully."}
_EDITS = {"#/texts/13": (("tele phone", "telephone"),)}
_OVERLAPPING = [(
    ("#/texts/10", "#/texts/12"), 7,
    "The monitor carries the task of verifying each recorded signal in "
    "every scheduled device audit.")]
_DEFERRED = [(
    ("#/texts/14", "#/texts/16"), 9,
    "The agency opened a formal hearing on the permit and asked the "
    "applicant for a detailed record of every thermal discharge event that "
    "occurred during the prior season of plant operation. Counsel then "
    "requested an evidentiary hearing before the review board convened.")]
_ADJACENT = [(
    ("#/texts/8", "#/texts/9"), 6, "The telephone number appears here.")]
_CROSS_PAGE = [(
    ("#/texts/6", "#/texts/7"), 4,
    "An imperfect fault-tolerant design applies here.")]
_DISJOINT = [
    (("#/texts/1", "#/texts/3", "#/texts/4"), 1,
     "The report found a shortage of care across the state\n\n"
     "This finding changed policy."),
    (("#/texts/3", "#/texts/5"), 2,
     "The later section continues with its emphasis on better outcomes."),
]


def _recoveries(groups):
    return tuple(
        rag.SourceTextGroupRecovery(text=text, refs=refs, page=page)
        for refs, page, text in groups)


def _expected(*, overrides=_OVERRIDES, edits=_EDITS, groups=(),
              deferred=()):
    return rag.BoundSourceEnrichments(
        text_overrides=dict(overrides), text_repair_edits=dict(edits),
        text_rebuild_refs=frozenset(),
        text_group_recoveries=_recoveries(groups),
        deferred_text_groups=_recoveries(deferred))


_FIRST_PASS = [*_OVERLAPPING, *_ADJACENT, *_CROSS_PAGE, *_DISJOINT]
_REPLAY_PASS = [
    *_OVERLAPPING, *_DEFERRED, *_ADJACENT, *_CROSS_PAGE, *_DISJOINT]
_REPLAY_VIOLATION = frozenset({("#/texts/16", 9)})


def test_each_native_stage_output_is_pinned(tmp_path):
    document, path = _build_book(tmp_path)

    stages = _run_stages(document, path)

    assert stages == {
        "native": (_OVERRIDES, _EDITS, set(), {}),
        "overlapping": (_OVERLAPPING, _DEFERRED),
        "adjacent": _ADJACENT,
        "cross_page": _CROSS_PAGE,
        "disjoint": _DISJOINT,
    }


def test_enrichment_and_its_order_replay_are_pinned(tmp_path, monkeypatch):
    document, path = _build_book(tmp_path)
    _snapshot(monkeypatch, path)

    first = _enrich(document, tmp_path)
    replay = _enrich(
        document, tmp_path, reading_order_violations=_REPLAY_VIOLATION)

    assert first == _expected(groups=_FIRST_PASS, deferred=_DEFERRED)
    assert replay == _expected(groups=_REPLAY_PASS)


def test_a_native_stage_fault_leaves_the_later_stages_unchanged(
        tmp_path, monkeypatch, caplog):
    document, path = _build_book(tmp_path)
    _snapshot(monkeypatch, path)
    _inject_page_fault(
        monkeypatch, 5, during="_recover_native_text_repairs")

    enrichments = _enrich(document, tmp_path)

    assert enrichments == _expected(
        overrides={}, edits={}, groups=_FIRST_PASS, deferred=_DEFERRED)
    assert [record.getMessage() for record in caplog.records] == [
        "Could not recover native-PDF text repairs: "
        "injected fault on page 5"]


def test_a_persistent_page_fault_fails_every_native_stage(
        tmp_path, monkeypatch, caplog):
    document, path = _build_book(tmp_path)
    _snapshot(monkeypatch, path)
    _inject_page_fault(monkeypatch, 5)

    enrichments = _enrich(document, tmp_path)

    assert enrichments == _expected(overrides={}, edits={})
    assert [record.getMessage() for record in caplog.records] == [
        f"Could not recover {name}: injected fault on page 5"
        for name in (
            "native-PDF text repairs",
            "overlapping native-PDF text groups",
            "adjacent native-PDF split-word groups",
            "cross-page native-PDF word groups",
            "disjoint section-flow text groups",
        )]
