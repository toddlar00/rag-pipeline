"""A soft hyphen after a line-end hyphen must not fuse two source items.

Some native PDF text layers spell a line-end hyphen as U+002D U+00AD. When
Docling ends one source item there (``Cabinet-`` + U+00AD) and the next
item continues the word (``level``), the HybridChunker's peer merge joins
the two items with a newline. Source-bound normalization turns a plain
``Cabinet-`` newline ``level`` seam into ``Cabinet-level`` (its
``spaced_hyphen`` rule), but the soft hyphen blocked that rule. The fidelity
audit then deleted the soft hyphen and dehyphenated across the newline,
reading one token that no source item owns, so the record could never align.

Such a seam is now joined as ``spaced_hyphen`` joins it without the soft
hyphen, which keeps both source tokens. The rule is failing-only: it fires
only at an item seam whose fused reading no source item, marker or oracle in
the document carries. All text is synthetic.
"""

import dataclasses
from types import SimpleNamespace

import pytest

import rag
import source_fidelity_core
from test_list_boundary_characterization import (
    _published,
    _stub_word_tokenized_models,
)

SOFT = "\u00ad"
LEFT = "The agencies are sited in one of the Cabinet-"
RIGHT = "level departments of the executive branch, which report to the President."
NEXT = ("A second paragraph explains how those departments are organized "
        "and staffed.")
PROFILE = "us-law-casebook-excerpt-v1"


def _write_document(path, *, item, right=RIGHT, two_pages=True):
    """Write a heading, a one-item enumerated list and two paragraphs."""
    doc = pytest.importorskip("docling_core.types.doc")
    document = doc.DoclingDocument(name="book")
    for page in (1, 2):
        document.add_page(page_no=page, size=doc.Size(width=612, height=792))

    def prov(text, page, top):
        return doc.ProvenanceItem(
            page_no=page, charspan=(0, len(text)),
            bbox=doc.BoundingBox(l=72, t=top, r=420, b=top + 14,
                                 coord_origin=doc.CoordOrigin.TOPLEFT))

    heading = "Notes and Questions"
    document.add_heading(text=heading, level=1, prov=prov(heading, 1, 600))
    group = document.add_list_group()
    document.add_list_item(text=item, enumerated=True, marker="1.",
                           parent=group, prov=prov("1. " + item, 1, 640))
    page, top = (2, 60) if two_pages else (1, 680)
    document.add_text(label=doc.DocItemLabel.TEXT, text=right,
                      prov=prov(right, page, top))
    document.add_text(label=doc.DocItemLabel.TEXT, text=NEXT,
                      prov=prov(NEXT, page, top + 20))
    path.write_text(document.model_dump_json(indent=2), encoding="utf-8")


def _chunk(monkeypatch, tmp_path, **document):
    _stub_word_tokenized_models(monkeypatch)
    source = tmp_path / "book.json"
    chunks = tmp_path / "book_chunks.jsonl"
    _write_document(source, **document)
    rag.chunk_document(source, chunks, structure_profile=PROFILE)
    return _published(chunks)


@pytest.mark.parametrize("two_pages", [True, False],
                         ids=["cross_page", "same_page"])
def test_soft_hyphen_item_seam_keeps_both_source_tokens(
        monkeypatch, tmp_path, two_pages):
    # Fails on the base: the gate raises on source_token_fidelity (output
    # record 0; source items #/texts/1-3) and the record reads
    # "Cabinet-" U+00AD newline "level".
    texts, report = _chunk(monkeypatch, tmp_path, item=LEFT + SOFT,
                           two_pages=two_pages)

    assert texts == [f"1. {LEFT}{RIGHT}\n{NEXT}"]
    assert "Cabinet-level departments" in texts[0]
    assert report["status"] == "pass"


def test_plain_hyphen_item_seam_is_unchanged(monkeypatch, tmp_path):
    # Control: spaced_hyphen already joins a plain seam this way.
    texts, report = _chunk(monkeypatch, tmp_path, item=LEFT)

    assert texts == [f"1. {LEFT}{RIGHT}\n{NEXT}"]
    assert report["status"] == "pass"


def test_soft_hyphen_line_break_inside_one_item_is_unchanged(
        monkeypatch, tmp_path):
    # The audit fuses this break in the item's own tokens too, so the record
    # already passes; it is not an item seam and keeps its source spelling.
    tail = "level departments are sited in the capital."
    texts, report = _chunk(monkeypatch, tmp_path,
                           item=f"{LEFT}{SOFT}\n{tail}")

    # The list serializer leaves a space before the item's own line break.
    assert texts == [f"1. {LEFT}{SOFT} \n{tail}\n{RIGHT}\n{NEXT}"]
    assert report["status"] == "pass"


def test_a_native_repair_seam_keeps_both_source_tokens(monkeypatch, tmp_path):
    # The real path: both items are native repairs marked for rebuild, but
    # the merged chunk takes item-local repair edits. The first override
    # drops the soft hyphen ("Cabinet-"), no edit touches its ending, so the
    # chunk keeps the Docling "-" U+00AD seam. The override also corrects
    # the last word ("Cabinct"), so its tokens, not Docling's, anchor the
    # seam. Fails before the Docling ending was read (seam kept, gate fails).
    recover = rag._recover_bound_source_enrichments

    def enrichments(*args, **kwargs):
        return dataclasses.replace(
            recover(*args, **kwargs),
            text_overrides={"#/texts/1": LEFT, "#/texts/2": RIGHT},
            text_repair_edits={"#/texts/1": (("Cabinct", "Cabinet"),)},
            text_rebuild_refs=frozenset({"#/texts/1", "#/texts/2"}))

    monkeypatch.setattr(rag, "_recover_bound_source_enrichments", enrichments)
    texts, report = _chunk(monkeypatch, tmp_path,
                           item=LEFT.replace("Cabinet", "Cabinct") + SOFT)

    assert texts == [f"1. {LEFT}{RIGHT}\n{NEXT}"]
    assert report["status"] == "pass"


# --- The seam rule itself ------------------------------------------------

def _item(ref, text, *, label="text", marker=""):
    return SimpleNamespace(self_ref=ref, label=SimpleNamespace(value=label),
                           text=text, orig=text, marker=marker)


def _oracle(text, group):
    tokens = source_fidelity_core.lexical_tokens(text)
    return {"oracle_lexical_tokens": list(tokens),
            "oracle_group_sha256": group}


def _join(text, items, *, shared_group=(), extra_items=()):
    """Run the rule with oracles and a vocabulary built like the pipeline's.

    ``shared_group`` names refs that share one native recovery oracle, whose
    text is their joined source with the seam's hyphen kept.
    """
    oracles = {}
    for item in (*items, *extra_items):
        group = source_fidelity_core.single_source_oracle_group_sha256(
            item.self_ref)
        oracles[item.self_ref] = _oracle(item.text, group)
    if shared_group:
        group_text = " ".join(
            item.text for item in items if item.self_ref in shared_group
        ).replace(SOFT + " ", "")
        group = source_fidelity_core.native_recovery_group_sha256(
            shared_group, source_fidelity_core.text_sha256(group_text))
        for ref in shared_group:
            oracles[ref] = _oracle(group_text, group)
    item_by_ref = {item.self_ref: item for item in (*items, *extra_items)}
    vocabulary = rag._lazy_source_lexical_vocabulary(item_by_ref, oracles)
    return rag._join_soft_hyphen_item_seams(
        text, items, item_text=lambda item: item.text,
        fidelity_oracles=oracles, source_vocabulary=vocabulary)


def _pair(left_tail=SOFT, right=RIGHT):
    return [_item("#/texts/1", LEFT + left_tail), _item("#/texts/2", right)]


def _per_item_tokens(items):
    return [token for item in items
            for token in source_fidelity_core.lexical_tokens(item.text)]


@pytest.mark.parametrize("seam", [
    SOFT + "\n", SOFT * 2 + "\n", SOFT + " \n", SOFT + "\n\n", SOFT + "\n ",
])
def test_the_seam_is_joined_with_its_hyphen(seam):
    items = _pair()
    text = LEFT + seam + RIGHT
    # The audit reads one fused token here, so the record cannot align.
    assert source_fidelity_core.lexical_tokens(text) != tuple(
        _per_item_tokens(items))

    joined = _join(text, items)

    assert joined == LEFT + RIGHT
    assert "Cabinet-level" in joined
    assert list(source_fidelity_core.lexical_tokens(joined)) == (
        _per_item_tokens(items))


@pytest.mark.parametrize("docling_tail, override_tail", [
    (SOFT, ""),   # item-local repair: the chunk keeps Docling's ending
    ("", SOFT),   # rebuild from an override that keeps the soft hyphen
], ids=["docling_ending", "override_ending"])
def test_the_item_ending_may_come_from_either_source_text(
        docling_tail, override_tail):
    items = _pair(left_tail=docling_tail)
    overrides = {"#/texts/1": LEFT + override_tail}
    oracles = {
        item.self_ref: _oracle(
            overrides.get(item.self_ref, item.text),
            source_fidelity_core.single_source_oracle_group_sha256(
                item.self_ref))
        for item in items}

    joined = rag._join_soft_hyphen_item_seams(
        f"{LEFT}{SOFT}\n{RIGHT}", items,
        item_text=lambda item: overrides.get(item.self_ref, item.text),
        fidelity_oracles=oracles, source_vocabulary=frozenset)

    assert joined == LEFT + RIGHT


def test_a_docling_ending_without_the_override_hyphen_is_unchanged():
    # Docling ends "Cabinet-" U+00AD but the override ends "Cabinet": the
    # override did more than drop the soft hyphen, so the rule declines.
    items = _pair()
    overrides = {"#/texts/1": LEFT[:-1]}
    oracles = {
        item.self_ref: _oracle(
            overrides.get(item.self_ref, item.text),
            source_fidelity_core.single_source_oracle_group_sha256(
                item.self_ref))
        for item in items}
    text = f"{LEFT}{SOFT}\n{RIGHT}"

    joined = rag._join_soft_hyphen_item_seams(
        text, items,
        item_text=lambda item: overrides.get(item.self_ref, item.text),
        fidelity_oracles=oracles, source_vocabulary=frozenset)

    assert joined == text


def test_the_rule_is_idempotent():
    items = _pair()
    once = _join(LEFT + SOFT + "\n" + RIGHT, items)

    assert _join(once, items) == once


def test_a_list_marker_before_the_seam_is_kept():
    items = [_item("#/texts/1", LEFT + SOFT, label="list_item", marker="1."),
             _item("#/texts/2", RIGHT)]

    joined = _join(f"1. {LEFT}{SOFT}\n{RIGHT}\n{NEXT}", items)

    assert joined == f"1. {LEFT}{RIGHT}\n{NEXT}"


def test_a_soft_hyphen_seam_without_a_hyphen_is_unchanged():
    # The audit deletes the soft hyphen and, with no hyphen left, keeps both
    # tokens: this seam already passes.
    items = [_item("#/texts/1", LEFT[:-1] + SOFT), _item("#/texts/2", RIGHT)]
    text = LEFT[:-1] + SOFT + "\n" + RIGHT

    assert _join(text, items) == text


def test_a_soft_hyphen_seam_without_a_line_break_is_unchanged():
    items = _pair()
    text = LEFT + SOFT + " " + RIGHT

    assert _join(text, items) == text


@pytest.mark.parametrize("left, right", [
    ("Title 42-", "level departments are sited in the capital."),
    (LEFT, "2020 levels of staffing were reported."),
], ids=["digit_before", "digit_after"])
def test_a_non_letter_beside_the_hyphen_is_unchanged(left, right):
    # The audit dehyphenates only between letters, so these seams pass.
    items = [_item("#/texts/1", left + SOFT), _item("#/texts/2", right)]
    text = left + SOFT + "\n" + right

    assert _join(text, items) == text


def test_a_soft_hyphen_line_break_inside_one_item_is_unchanged():
    item = _item("#/texts/1", f"{LEFT}{SOFT}\n{RIGHT}")
    text = f"{LEFT}{SOFT}\n{RIGHT}\n{NEXT}"

    assert _join(text, [item, _item("#/texts/2", NEXT)]) == text


def test_a_break_introduced_inside_one_item_is_not_an_item_seam():
    # The item reads "Cabinet-" U+00AD space "level"; a line break placed
    # there is not the boundary between two source items.
    items = [_item("#/texts/1", f"{LEFT}{SOFT} {RIGHT} It is {LEFT}{SOFT}"),
             _item("#/texts/2", "Staffing is reported yearly.")]
    text = f"{LEFT}{SOFT}\n{RIGHT} It is {LEFT}{SOFT} Staffing is reported yearly."

    assert _join(text, items) == text


def test_a_seam_shape_before_an_item_end_is_not_an_item_seam():
    # The first item ends in "Cabinet", not in the seam shape, so the
    # soft-hyphen break inside it is not the boundary between the items.
    items = [_item("#/texts/1", f"{LEFT}{SOFT} {RIGHT} It is the Cabinet"),
             _item("#/texts/2", RIGHT)]
    text = f"{LEFT}{SOFT}\n{RIGHT} It is the Cabinet\n{RIGHT}"

    assert _join(text, items) == text


def test_an_in_item_break_is_not_taken_for_a_space_joined_seam():
    # The first item breaks "W-" U+00AD newline "level" inside itself and
    # ends in "Cabinet-" U+00AD; the chunk joins the real seam with a space.
    # The audit fuses the in-item break on both sides: this passes today.
    left = f"Its W-{SOFT}\nlevel staff sit in one of the Cabinet-{SOFT}"
    items = [_item("#/texts/1", left), _item("#/texts/2", RIGHT)]
    text = f"{left} {RIGHT}"
    assert list(source_fidelity_core.lexical_tokens(text)) == (
        _per_item_tokens(items))

    assert _join(text, items) == text


def test_a_seam_after_the_first_item_pair_is_joined():
    items = [_item("#/texts/0", NEXT), *_pair()]

    joined = _join(f"{NEXT}\n{LEFT}{SOFT}\n{RIGHT}", items)

    assert joined == f"{NEXT}\n{LEFT}{RIGHT}"


def test_two_seams_in_one_chunk_are_both_joined():
    middle = "level departments report to an Under-"
    last = "secretary of each department."
    items = [_item("#/texts/1", LEFT + SOFT),
             _item("#/texts/2", middle + SOFT), _item("#/texts/3", last)]

    joined = _join(f"{LEFT}{SOFT}\n{middle}{SOFT}\n{last}", items)

    assert joined == f"{LEFT}{middle}{last}"
    assert list(source_fidelity_core.lexical_tokens(joined)) == (
        _per_item_tokens(items))


def test_an_item_ending_in_whitespace_still_forms_the_seam():
    items = [_item("#/texts/1", LEFT + SOFT + " "), _item("#/texts/2", RIGHT)]

    assert _join(f"{LEFT}{SOFT} \n{RIGHT}", items) == LEFT + RIGHT


@pytest.mark.parametrize("label", ["footnote", "caption"])
def test_footnote_and_caption_seams_are_joined(label):
    items = [_item("#/texts/1", LEFT + SOFT, label=label),
             _item("#/texts/2", RIGHT, label=label)]

    assert _join(f"{LEFT}{SOFT}\n{RIGHT}", items) == LEFT + RIGHT


def test_items_that_are_not_consecutive_are_unchanged():
    left, right = _pair()
    between = _item("#/pictures/0", "", label="picture")
    text = LEFT + SOFT + "\n" + RIGHT

    assert _join(text, [left, between, right]) == text


@pytest.mark.parametrize("left_label, right_label", [
    ("page_footer", "text"), ("list_item", "section_header"),
])
def test_a_seam_beside_an_item_without_body_text_is_unchanged(
        left_label, right_label):
    # Only text, list, footnote and caption items keep their chunk
    # published; a chunk without them can be dropped as structural text.
    items = [_item("#/texts/1", LEFT + SOFT, label=left_label),
             _item("#/texts/2", RIGHT, label=right_label)]
    text = LEFT + SOFT + "\n" + RIGHT

    assert _join(text, items) == text


def test_a_pair_sharing_one_recovery_oracle_is_unchanged():
    # A native recovery group rebuilds such a pair from its own oracle.
    items = _pair()
    text = LEFT + SOFT + "\n" + RIGHT

    assert _join(text, items, shared_group=("#/texts/1", "#/texts/2")) == text


def test_a_fused_spelling_attested_elsewhere_is_unchanged():
    # Failing-only guard: with "Cabinetlevel" a source token anywhere in the
    # document, the fused reading could align, so the seam is left alone.
    items = _pair()
    text = LEFT + SOFT + "\n" + RIGHT
    elsewhere = _item("#/texts/9", "The Cabinetlevel spelling is attested.")

    assert _join(text, items, extra_items=(elsewhere,)) == text


def test_an_ambiguous_seam_is_unchanged():
    # Two places read the pair's boundary words across the seam shape, so
    # neither is known to be the item seam.
    items = [_item("#/texts/1", f"{LEFT}{SOFT} {RIGHT} Yet {LEFT}{SOFT}"),
             _item("#/texts/2", RIGHT)]
    text = f"{LEFT}{SOFT}\n{RIGHT} Yet {LEFT}{SOFT}\n{RIGHT}"

    assert _join(text, items) == text


@pytest.mark.parametrize("left_tail, reads", [("", 0), (SOFT, 1)])
def test_the_vocabulary_is_read_only_for_a_qualifying_seam(left_tail, reads):
    items = _pair(left_tail=left_tail)
    oracles = {
        item.self_ref: _oracle(
            item.text,
            source_fidelity_core.single_source_oracle_group_sha256(
                item.self_ref))
        for item in items}
    calls = []

    def vocabulary():
        calls.append(None)
        return frozenset()

    rag._join_soft_hyphen_item_seams(
        LEFT + left_tail + "\n" + RIGHT, items,
        item_text=lambda item: item.text, fidelity_oracles=oracles,
        source_vocabulary=vocabulary)

    assert len(calls) == reads


def test_the_document_vocabulary_is_built_once():
    reads = []

    class Oracles(dict):
        def values(self):
            reads.append(None)
            return super().values()

    item = _item("#/texts/1", "Cabinetlevel", label="list_item", marker="a.")
    oracles = Oracles({"#/texts/2": {"oracle_lexical_tokens": ["recovered"]}})
    vocabulary = rag._lazy_source_lexical_vocabulary(
        {"#/texts/1": item}, oracles)

    assert vocabulary() == {"cabinetlevel", "a", "recovered"}
    assert vocabulary() is vocabulary()
    assert len(reads) == 1
