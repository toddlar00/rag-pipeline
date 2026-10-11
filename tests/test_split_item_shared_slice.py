"""A split item's slice beside another item is retracted by its whole publication.

PR #116 retracts the recorded slices of a long item HybridChunker cites from
two raw chunks when a later per-item path publishes it whole, but it recorded
only a slice whose chunk held that item alone. When the first slice shares
its raw chunk with a short preceding item (an opinion's "delivered the
opinion" line merged with the first part of a long paragraph), the slice
stayed published beside that item, the whole item repeated it, and
``source_token_fidelity`` failed.

Such a slice is now recorded too. Retracting it republishes the chunk's other
items whole, each as its own entry, as the per-item paths publish them, and
the whole item takes the later chunk's place. Only output that fails today
changes. The raw chunks are those the casebook produced; the chunker and
layout detector are stubbed, while preparation, enrichment, the fidelity
audit and quality publication are real. All text is synthetic.
"""

import json

import pytest

import rag
from test_list_boundary_characterization import _stub_word_tokenized_models


PROFILE = "us-law-casebook-excerpt-v1"
HEADING = "Orion Systems v. Delta Labs"
OPENING = "Judge Rivera delivered the opinion of the court."
HEAD = " ".join(
    f"Sentence {n} explains how the ferry office posted revised departure "
    "times for travelers." for n in range(1, 5))
TAIL = " ".join(
    f"Sentence {n} explains how the ferry office posted revised departure "
    "times for travelers." for n in range(5, 9))
LONG = f"{HEAD} {TAIL}"
NEXT = "Port North opens at nine and closes at five every weekday."


def _write_document(path):
    doc = pytest.importorskip("docling_core.types.doc")
    document = doc.DoclingDocument(name="book")
    document.add_page(page_no=1, size=doc.Size(width=612, height=792))

    def prov(text, top, height):
        return doc.ProvenanceItem(
            page_no=1, charspan=(0, len(text)),
            bbox=doc.BoundingBox(l=72, t=top, r=540, b=top + height,
                                 coord_origin=doc.CoordOrigin.TOPLEFT))

    document.add_heading(text=HEADING, level=1, prov=prov(HEADING, 60, 14))
    for text, top, height in ((OPENING, 90, 14), (LONG, 120, 140),
                              (NEXT, 280, 14)):
        document.add_text(label=doc.DocItemLabel.TEXT, text=text,
                          prov=prov(text, top, height))
    path.write_text(document.model_dump_json(indent=2), encoding="utf-8")


def _stub_raw_chunks(monkeypatch, pieces):
    """Emit ``(text, item texts)`` raw chunks, as HybridChunker split them."""
    chunkers = pytest.importorskip("docling_core.transforms.chunker")
    from docling_core.transforms.chunker.doc_chunk import DocChunk, DocMeta

    class Chunker:
        def __init__(self, **_kwargs):
            pass

        def chunk(self, dl_doc):
            by_text = {item.text: item for item in dl_doc.texts}
            for text, item_texts in pieces:
                yield DocChunk(text=text, meta=DocMeta(
                    doc_items=[by_text[value] for value in item_texts],
                    headings=[HEADING]))

    monkeypatch.setattr(chunkers, "HybridChunker", Chunker)


def _stub_layout(monkeypatch, text):
    def detect(dl_doc, **_kwargs):
        item = next(item for item in dl_doc.texts if item.text == text)
        return [rag.SyntheticLayoutTable(markdown=item.text, items=(item,))]

    monkeypatch.setattr(rag, "_detect_aligned_list_tables", detect)


def _chunk(monkeypatch, tmp_path, pieces, *, layout=None):
    _stub_word_tokenized_models(monkeypatch)
    _stub_raw_chunks(monkeypatch, pieces)
    if layout is not None:
        _stub_layout(monkeypatch, layout)
    document = tmp_path / "book.json"
    _write_document(document)
    chunks = tmp_path / "book_chunks.jsonl"
    rag._chunk_document_locked(
        document, chunks, max_tokens=200, structure_profile=PROFILE)
    records = [json.loads(line) for line in chunks.read_text(
        encoding="utf-8").splitlines()]
    report = json.loads(rag._quality_core.quality_report_path(
        chunks).read_text(encoding="utf-8"))
    return [record["text"] for record in records], report


def _count(texts, value):
    return sum(text.count(value) for text in texts)


SPLIT = [(f"{OPENING}\n{HEAD}", [OPENING, LONG]),
         (f"{TAIL}\n{NEXT}", [LONG, NEXT])]


def test_a_slice_sharing_its_chunk_is_retracted_by_the_whole_item(
        monkeypatch, tmp_path):
    # Fails before the change: the corpus gate raises on
    # source_token_fidelity, because HEAD is published twice.
    texts, report = _chunk(monkeypatch, tmp_path, SPLIT, layout=NEXT)

    assert report["status"] == "pass"
    assert _count(texts, OPENING) == 1
    assert _count(texts, "Sentence 1 ") == 1
    assert _count(texts, "Sentence 8 ") == 1
    assert _count(texts, NEXT) == 1
    order = [next(i for i, text in enumerate(texts) if value in text)
             for value in (OPENING, "Sentence 1 ", NEXT)]
    assert order == sorted(order)


def test_without_a_whole_publication_the_shared_slice_is_unchanged(
        monkeypatch, tmp_path):
    # Characterization: no layout, so no per-item path publishes the long
    # item whole and both slices stay where HybridChunker put them.
    texts, report = _chunk(monkeypatch, tmp_path, SPLIT)

    assert report["status"] == "pass"
    assert _count(texts, "Sentence 1 ") == 1
    assert _count(texts, "Sentence 8 ") == 1
    assert any(OPENING in text and "Sentence 1 " in text for text in texts)
