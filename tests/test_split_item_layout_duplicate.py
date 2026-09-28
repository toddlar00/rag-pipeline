"""A split list item is never repeated by a per-item whole publication.

HybridChunker can cite one long source item from two or more raw chunks.  An
ordinary chunk publishes only its chunk-local slice, but a later chunk that
also holds a synthetic-layout item or a source table takes a per-item path
and publishes each ordinary item whole.  Before this fix, the whole item
repeated the slices other chunks had published, and the repeated source
tokens failed ``source_token_fidelity``.  A repeated slice with source tokens
can never pass that gate, so only failing output changes.  The layout
detector is stubbed; chunking, enrichment, the fidelity audit and quality
publication are real.  All text is synthetic.
"""

import json
from collections import Counter

import pytest

import rag
from test_list_boundary_characterization import _stub_word_tokenized_models


PROFILE = "us-law-casebook-excerpt-v1"
SHORT = "The harbor office kept the old schedule on file."


def _long(sentences):
    return " ".join(
        f"Sentence {number} explains how the ferry office posted revised "
        "departure times for travelers."
        for number in range(1, sentences + 1))


def _write_document(path, notes, *, table=False):
    """A numbered list of ``(marker, text)`` notes, then optionally a table."""
    doc = pytest.importorskip("docling_core.types.doc")
    document = doc.DoclingDocument(name="book")
    document.add_page(page_no=1, size=doc.Size(width=612, height=792))
    group = document.add_list_group()
    top = 60

    def prov(length, height):
        return doc.ProvenanceItem(
            page_no=1, charspan=(0, length),
            bbox=doc.BoundingBox(l=72, t=top, r=540, b=top + height,
                                 coord_origin=doc.CoordOrigin.TOPLEFT))

    for marker, text in notes:
        height = 14 * max(1, len(text) // 80)
        document.add_list_item(
            text=text, marker=marker, enumerated=True, parent=group,
            prov=prov(len(marker) + 1 + len(text), height))
        top += height + 8
    if table:
        cells = [doc.TableCell(
            text=text, start_row_offset_idx=row, end_row_offset_idx=row + 1,
            start_col_offset_idx=col, end_col_offset_idx=col + 1)
            for row, col, text in ((0, 0, "Port"), (0, 1, "Hour"),
                                   (1, 0, "North"), (1, 1, "Nine"))]
        document.add_table(
            data=doc.TableData(num_rows=2, num_cols=2, table_cells=cells),
            prov=prov(0, 30))
    path.write_text(document.model_dump_json(indent=2), encoding="utf-8")


def _stub_layout(monkeypatch, text=SHORT):
    """Claim the short note as a synthetic layout, as a detector would."""
    def detect(dl_doc, **_kwargs):
        item = next(item for item in dl_doc.texts if item.text == text)
        return [rag.SyntheticLayoutTable(
            markdown=f"{item.marker} {item.text}", items=(item,))]

    monkeypatch.setattr(rag, "_detect_aligned_list_tables", detect)


def _chunk(monkeypatch, tmp_path, notes, *, table=False):
    _stub_word_tokenized_models(monkeypatch)
    document = tmp_path / "book.json"
    _write_document(document, notes, table=table)
    chunks = tmp_path / "book_chunks.jsonl"
    rag._chunk_document_locked(
        document, chunks, max_tokens=70, structure_profile=PROFILE)
    records = [json.loads(line) for line in chunks.read_text(
        encoding="utf-8").splitlines()]
    report = json.loads(rag._quality_core.quality_report_path(
        chunks).read_text(encoding="utf-8"))
    return records, report


def _sentence_counts(records):
    return Counter(
        word for record in records for word in record["text"].split()
        if word.rstrip(".").isdigit() and not word.endswith("."))


def _published_once(records, *longs):
    expected = Counter(
        word for text in longs for word in text.split()
        if word.rstrip(".").isdigit() and not word.endswith("."))
    assert _sentence_counts(records) == expected
    texts = [record["text"] for record in records]
    assert len(texts) == len(set(texts))


@pytest.mark.parametrize("sentences", [8, 16])
def test_split_item_is_published_once_beside_a_synthetic_layout(
        monkeypatch, tmp_path, sentences):
    """The note's slices precede the chunk holding its tail and the layout
    item; the layout path publishes the note whole and retracts them all."""
    _stub_layout(monkeypatch)
    long = _long(sentences)

    records, report = _chunk(
        monkeypatch, tmp_path, [("4.", long), ("5.", SHORT)])

    assert report["status"] == "pass"
    _published_once(records, long)
    assert sum(SHORT in record["text"] for record in records) == 1


def test_split_item_is_published_once_beside_a_source_table(
        monkeypatch, tmp_path):
    """The mixed-table per-item path publishes the note whole as well."""
    long = _long(8)

    records, report = _chunk(
        monkeypatch, tmp_path, [("4.", long)], table=True)

    assert report["status"] == "pass"
    _published_once(records, long)


def test_split_item_outside_any_layout_keeps_its_slices(
        monkeypatch, tmp_path):
    """Only the note beside the layout item is published whole."""
    _stub_layout(monkeypatch)
    first, second = _long(8), _long(8).replace("ferry", "river")

    records, report = _chunk(monkeypatch, tmp_path, [
        ("3.", second), ("4.", first), ("5.", SHORT)])

    assert report["status"] == "pass"
    river = [record["text"] for record in records if "river" in record["text"]]
    assert len(river) == 2
    assert river[0].startswith("3. Sentence 1 ")


@pytest.mark.parametrize("layout", [False, True])
def test_characterization_split_item_that_never_shares_a_layout_chunk(
        monkeypatch, tmp_path, layout):
    """With the short note first, the long note never shares a raw chunk
    with it, so both keep the previous chunk-local slices."""
    if layout:
        _stub_layout(monkeypatch)

    records, report = _chunk(
        monkeypatch, tmp_path, [("5.", SHORT), ("4.", _long(8))])

    assert report["status"] == "pass"
    assert [len(record["text"].split()) for record in records] == [
        10, 66, 39]
