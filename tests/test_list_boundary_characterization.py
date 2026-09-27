"""Characterization of today's boundary join after a list lead-in.

Boundary repair joins a same-heading sentence continuation inline, so a
record that opens with a list item's punctuation marker has that marker
placed mid-line.  The documents here pass every quality gate with that
inline join and must keep their exact published text in a single chunking
pass: an ineligible opening bullet, and a long item whose split tail opens
with an in-text dash.  Every case passes on the code before the list
boundary replay; ``test_list_boundary_replay`` pins the replay itself.
All text is synthetic.
"""

import json

import pytest

import rag
from test_native_group_order_replay import _stub_chunk_models


BULLET = "\u00b7"
LEAD = ("The court considered several earlier decisions on the same "
        "question, for example,")
ITEMS = ("Alpha v. Beta held that notice was required.",
         "Gamma v. Delta held that it was not.")
TAIL = "The later decisions resolved the conflict in favor of notice."
SHORT_LEAD = "Consider these holdings, for example:"
# One list item long enough for a 44-token budget to split it before its
# in-text dash, so the tail record opens with the list's canonical marker.
SPLIT_ITEM = (
    "Alpha held X, - beta clause here concerns the remedy that the agency "
    "adopted after notice and comment and whether that remedy is consistent "
    "with the governing statute and the purposes it was enacted to serve in "
    "the first place and the court weighed the timing.")


def _lineage(ref):
    return {"ref": ref, "label": "text" if ref == "#/texts/0" else "list_item",
            "spans": [{"provenance_index": 0}],
            "scope": {"provenance_indexes": [0]}}


def _record(text, refs):
    return {"text": text, "metadata": {
        "page_start": 1, "page_end": 1, "headings": ["Deference"],
        "content_type": "author_narrative", "content_source": "body",
        "source_items": [_lineage(ref) for ref in refs]}}


def _coalesce(records, **kwargs):
    return rag._coalesce_chunk_boundaries(
        records, lambda value: len(value.split()), 200, **kwargs)


def test_boundary_join_places_a_list_marker_mid_line():
    joined = _coalesce([
        _record(LEAD, ["#/texts/0"]), _record(f"- {ITEMS[0]}", ["#/texts/1"])])

    assert [record["text"] for record in joined] == [f"{LEAD} - {ITEMS[0]}"]


def test_period_ended_lead_in_keeps_the_list_record_separate():
    records = [_record(LEAD.rstrip(",") + ".", ["#/texts/0"]),
               _record(f"- {ITEMS[0]}", ["#/texts/1"])]

    kept = _coalesce(records)

    assert [record["text"] for record in kept] == [
        records[0]["text"], records[1]["text"]]


def _write_document(path, *, lead=LEAD, items=ITEMS):
    """Write a lead-in, one bullet list and a closing paragraph on page 1."""
    doc = pytest.importorskip("docling_core.types.doc")
    document = doc.DoclingDocument(name="book")
    document.add_page(page_no=1, size=doc.Size(width=612, height=792))

    def prov(text, top, prefix=""):
        return doc.ProvenanceItem(
            page_no=1, charspan=(0, len(prefix + text)),
            bbox=doc.BoundingBox(l=72, t=top, r=420, b=top + 14,
                                 coord_origin=doc.CoordOrigin.TOPLEFT))

    document.add_text(label=doc.DocItemLabel.TEXT, text=lead,
                      prov=prov(lead, 60))
    group = document.add_list_group()
    for index, text in enumerate(items):
        document.add_list_item(
            text=text, marker=BULLET, parent=group,
            prov=prov(text, 80 + 20 * index, f"{BULLET} "))
    document.add_text(label=doc.DocItemLabel.TEXT, text=TAIL,
                      prov=prov(TAIL, 80 + 20 * len(items)))
    # Conversions are persisted without aliases (``cref`` relationships).
    path.write_text(document.model_dump_json(indent=2), encoding="utf-8")


def _stub_word_tokenized_models(monkeypatch):
    """Keep the real HybridChunker, splitting by a word-count budget."""
    base = pytest.importorskip(
        "docling_core.transforms.chunker.tokenizer.base")
    tokenizers = pytest.importorskip(
        "docling_core.transforms.chunker.tokenizer.huggingface")

    class WordTokenizer(base.BaseTokenizer):
        max_tokens: int

        def count_tokens(self, text):
            return len(text.split())

        def get_max_tokens(self):
            return self.max_tokens

        def get_tokenizer(self):
            return lambda text: len(text.split())

    monkeypatch.setattr(
        rag, "_model_loader_source", lambda *_args, **_kwargs: ("stub", True))
    monkeypatch.setattr(
        tokenizers.HuggingFaceTokenizer, "from_pretrained",
        classmethod(lambda _cls, **kwargs: WordTokenizer(
            max_tokens=kwargs["max_tokens"])))
    monkeypatch.setattr(
        rag, "_count_embedding_text_tokens", lambda texts, _model: (
            [len(text.split()) for text in texts], False))


def _count_locked_passes(monkeypatch):
    passes = []
    locked = rag._chunk_document_locked

    def spy(*args, **kwargs):
        passes.append(None)
        return locked(*args, **kwargs)

    monkeypatch.setattr(rag, "_chunk_document_locked", spy)
    return passes


def _published(chunks):
    texts = [json.loads(line)["text"] for line in chunks.read_text(
        encoding="utf-8").splitlines()]
    report = json.loads(rag._quality_core.quality_report_path(
        chunks).read_text(encoding="utf-8"))
    return texts, report


@pytest.mark.parametrize("opening", ["", "All emphasis added."])
def test_ineligible_opening_bullet_is_published_inline_in_one_pass(
        monkeypatch, tmp_path, opening):
    _stub_chunk_models(monkeypatch)
    document = tmp_path / "book.json"
    _write_document(document, items=(opening, *ITEMS))
    passes = _count_locked_passes(monkeypatch)
    chunks = tmp_path / "book_chunks.jsonl"

    rag.chunk_document(
        document, chunks, structure_profile="us-law-casebook-excerpt-v1")

    texts, report = _published(chunks)
    assert len(passes) == 1
    assert texts[0] == "\n".join(
        (f"{LEAD} - {opening}", f"- {ITEMS[0]}", f"- {ITEMS[1]}"))
    assert report["status"] == "pass"


def test_split_item_tail_opening_with_a_dash_is_joined_inline_in_one_pass(
        monkeypatch, tmp_path):
    _stub_word_tokenized_models(monkeypatch)
    document = tmp_path / "book.json"
    _write_document(document, lead=SHORT_LEAD, items=(SPLIT_ITEM, ITEMS[1]))
    passes = _count_locked_passes(monkeypatch)
    chunks = tmp_path / "book_chunks.jsonl"

    rag.chunk_document(
        document, chunks, max_tokens=44,
        structure_profile="us-law-casebook-excerpt-v1")

    texts, report = _published(chunks)
    assert len(passes) == 1
    assert texts == [f"{SHORT_LEAD}\n- {SPLIT_ITEM}",
                     f"- {ITEMS[1]}\n{TAIL}"]
    assert report["status"] == "pass"
