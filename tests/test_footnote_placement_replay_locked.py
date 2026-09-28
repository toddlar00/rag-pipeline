"""The footnote placement replay through the real locked chunking pass.

Models are stubbed, but enrichment, the fidelity audit and quality
publication are real.  On page 1 a tokenless ornament row sits between the
notes, inside the passage that continues onto page 2, so the passage's page-1
box overlaps the upper notes.  The first pass places them after the next
body record and fails ``same_page_reading_order``; the replay places them by
the lineage the audit observes and passes.  Without the ornament one pass
publishes.  All text is synthetic.
"""

import json

import pytest

import rag
from test_list_boundary_characterization import _stub_word_tokenized_models


PROFILE = "us-law-casebook-excerpt-v1"
ORNAMENT = "★★★"
PASSAGE = (
    "The ferry schedule changed twice during the season, and the harbor "
    "office posted each revision on the pier beside the ticket window while "
    "travelers compared the old departure times with the new ones and")
CONTINUATION = (
    "the crews adjusted their breaks to match the revised departures, which "
    "meant that the evening boat now waited for the last train from")
CLOSING = (
    "the inland towns, and the office promised to keep that connection "
    "through the end of the year.")
HEADING = "Connections"
# As scanned, without note numbers, so no marker opens a footnote lane.
NOTES = ("Synthetic upper note citing an earlier order.",
         "Synthetic second note citing the same order.",
         "Synthetic lower note after omitted notes.")
# Refs: passage 0, notes 1, 2 and 4 around the ornament 3, continuation 5,
# heading 6 and closing 7.  Without the ornament, the refs after 2 shift.
PLACED = [["#/texts/0", "#/texts/3", "#/texts/5"], ["#/texts/1"],
          ["#/texts/2"], ["#/texts/4"], ["#/texts/7"]]
MISPLACED = [["#/texts/0", "#/texts/3", "#/texts/5"], ["#/texts/4"],
             ["#/texts/7"], ["#/texts/1"], ["#/texts/2"]]


def _write_document(path, *, ornament=True):
    doc = pytest.importorskip("docling_core.types.doc")
    document = doc.DoclingDocument(name="book")
    for page in (1, 2):
        document.add_page(page_no=page, size=doc.Size(width=612, height=792))

    def prov(text, page, left, top, right, bottom):
        return doc.ProvenanceItem(
            page_no=page, charspan=(0, len(text)),
            bbox=doc.BoundingBox(l=left, t=top, r=right, b=bottom,
                                 coord_origin=doc.CoordOrigin.TOPLEFT))

    def add(label, text, *box):
        document.add_text(label=label, text=text, prov=prov(text, *box))

    text, footnote = doc.DocItemLabel.TEXT, doc.DocItemLabel.FOOTNOTE
    add(text, PASSAGE, 1, 70, 100, 430, 400)
    add(footnote, NOTES[0], 1, 90, 652, 300, 665)
    add(footnote, NOTES[1], 1, 90, 665, 380, 678)
    if ornament:
        add(text, ORNAMENT, 1, 240, 678, 262, 687)
    add(footnote, NOTES[2], 1, 90, 690, 380, 703)
    add(text, CONTINUATION, 2, 70, 100, 430, 200)
    document.add_heading(
        text=HEADING, prov=prov(HEADING, 2, 70, 210, 200, 224))
    add(text, CLOSING, 2, 70, 230, 430, 300)
    path.write_text(document.model_dump_json(indent=2), encoding="utf-8")


def _locked_passes(monkeypatch):
    calls = []
    locked = rag._chunk_document_locked

    def spy(*args, **kwargs):
        calls.append({key: kwargs.get(key) for key in (
            "footnote_replay_requests", "observed_footnote_geometry_pages")})
        return locked(*args, **kwargs)

    monkeypatch.setattr(rag, "_chunk_document_locked", spy)
    return calls


def _published(chunks):
    records = [json.loads(line) for line in chunks.read_text(
        encoding="utf-8").splitlines()]
    report = json.loads(rag._quality_core.quality_report_path(
        chunks).read_text(encoding="utf-8"))
    refs = [[item["ref"] for item in record["metadata"]["source_items"]]
            for record in records]
    reasons = [record["metadata"].get(rag._quality_core.PAGE_ORDER_REASON_FIELD)
               for record in records]
    return refs, reasons, report


def _chunk(monkeypatch, tmp_path, **kwargs):
    _stub_word_tokenized_models(monkeypatch)
    document = tmp_path / "book.json"
    _write_document(document, **kwargs)
    return document, tmp_path / "book_chunks.jsonl"


def test_first_pass_alone_misplaces_the_upper_notes(monkeypatch, tmp_path):
    """Before the replay, this gate failure propagated unchanged."""
    document, chunks = _chunk(monkeypatch, tmp_path)

    with pytest.raises(RuntimeError, match=(
            "^Corpus quality gate failed: same_page_reading_order$")):
        rag._chunk_document_locked(
            document, chunks, max_tokens=200, structure_profile=PROFILE)

    refs, reasons, report = _published(chunks)
    assert refs == MISPLACED
    assert reasons[-2:] == [
        rag._quality_core.FOOTNOTE_AFTER_CONTINUATION_REASON] * 2
    assert {(violation["before_ref"], violation["after_ref"], violation["page"])
            for violation in report["source_lineage"]["fidelity"][
                "geometry_violations"]} == {
        ("#/texts/1", "#/texts/4", 1), ("#/texts/2", "#/texts/4", 1)}


def test_locked_pass_replays_notes_misplaced_by_a_tokenless_row(
        monkeypatch, tmp_path):
    document, chunks = _chunk(monkeypatch, tmp_path)
    calls = _locked_passes(monkeypatch)

    rag.chunk_document(
        document, chunks, max_tokens=200, structure_profile=PROFILE)

    assert calls == [
        {"footnote_replay_requests": [frozenset({1})],
         "observed_footnote_geometry_pages": None},
        {"footnote_replay_requests": None,
         "observed_footnote_geometry_pages": frozenset({1})}]
    refs, reasons, report = _published(chunks)
    assert refs == PLACED
    assert reasons == [None] * len(PLACED)
    assert report["status"] == "pass"
    assert report["source_lineage"]["fidelity"]["geometry_violations"] == []


def test_passing_first_pass_without_the_ornament_is_published_once(
        monkeypatch, tmp_path):
    document, chunks = _chunk(monkeypatch, tmp_path, ornament=False)
    calls = _locked_passes(monkeypatch)

    rag.chunk_document(
        document, chunks, max_tokens=200, structure_profile=PROFILE)

    assert calls == [{"footnote_replay_requests": [],
                      "observed_footnote_geometry_pages": None}]
    refs, _reasons, report = _published(chunks)
    assert refs == [["#/texts/0", "#/texts/4"], ["#/texts/1"], ["#/texts/2"],
                    ["#/texts/3"], ["#/texts/6"]]
    assert report["status"] == "pass"


def _inject_first_violation(monkeypatch, before, after):
    """Fail only the first pass's real report with one extra page-1 edge."""
    build = rag._quality_core.build_quality_report
    reports = []

    def build_report(**kwargs):
        report = build(**kwargs)
        reports.append(report)
        if len(reports) == 1:
            report["status"] = "fail"
            report["source_lineage"]["fidelity"]["geometry_violations"].append(
                {"before_ref": before, "after_ref": after, "page": 1})
            for check in report["checks"]:
                if check["name"] == "same_page_reading_order":
                    check["status"] = "fail"
        return report

    monkeypatch.setattr(rag._quality_core, "build_quality_report", build_report)


def test_body_only_reading_order_failure_requests_no_footnote_replay(
        monkeypatch, tmp_path):
    """Only refs the first pass published in footnote sidecars qualify."""
    document, chunks = _chunk(monkeypatch, tmp_path, ornament=False)
    _inject_first_violation(monkeypatch, "#/texts/6", "#/texts/0")
    calls = _locked_passes(monkeypatch)

    with pytest.raises(RuntimeError, match=(
            "^Corpus quality gate failed: same_page_reading_order$")):
        rag.chunk_document(
            document, chunks, max_tokens=200, structure_profile=PROFILE)

    assert calls == [{"footnote_replay_requests": [],
                      "observed_footnote_geometry_pages": None}]


def test_failure_naming_a_published_footnote_requests_its_page(
        monkeypatch, tmp_path):
    document, chunks = _chunk(monkeypatch, tmp_path, ornament=False)
    _inject_first_violation(monkeypatch, "#/texts/3", "#/texts/1")
    calls = _locked_passes(monkeypatch)

    rag.chunk_document(
        document, chunks, max_tokens=200, structure_profile=PROFILE)

    assert [call["observed_footnote_geometry_pages"] for call in calls] == [
        None, frozenset({1})]
    assert _published(chunks)[2]["status"] == "pass"
