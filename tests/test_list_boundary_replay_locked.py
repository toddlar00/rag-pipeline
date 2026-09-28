"""The list boundary replay through the real locked chunking pass.

Models are stubbed, but enrichment, the fidelity audit and quality
publication are real.  A first pass whose report fails
``source_token_fidelity`` on a list item that its boundary repair joined
inline is replayed once with only that join separated; a first pass that passes
is published once, even when it observes such a join.  When the same failed
report also names a deferred native group, only the order replay runs, so a
document that needs both repairs still fails.  All text is synthetic.
"""

import dataclasses

import pytest

import rag
from test_list_boundary_characterization import (
    BULLET, ITEMS, LEAD, SHORT_LEAD, SPLIT_ITEM, TAIL, _published,
    _stub_word_tokenized_models, _write_document)
from test_native_group_order_replay import _stub_chunk_models


PROFILE = "us-law-casebook-excerpt-v1"
DEFERRED = (rag.SourceTextGroupRecovery(
    text="deferred run", refs=("#/texts/37", "#/texts/42"), page=4),)
NAMED = frozenset({("#/texts/42", 4)})
SECOND_LEAD = "Later courts relied on other authority, for example,"
SECOND_ITEMS = ("All emphasis added.",
                "Epsilon v. Zeta held that the remedy was void.")


def _spy_coalescer(monkeypatch):
    calls = []
    coalesce = rag._coalesce_chunk_boundaries

    def spy(records, *args, **kwargs):
        calls.append(kwargs)
        return coalesce(records, *args, **kwargs)

    monkeypatch.setattr(rag, "_coalesce_chunk_boundaries", spy)
    return calls


def test_locked_pass_replays_an_uncovered_inline_list_join(
        monkeypatch, tmp_path):
    """Before the replay, this gate failure propagated unchanged."""
    _stub_chunk_models(monkeypatch)
    document = tmp_path / "book.json"
    _write_document(document)
    calls = _spy_coalescer(monkeypatch)
    chunks = tmp_path / "book_chunks.jsonl"

    rag.chunk_document(document, chunks, structure_profile=PROFILE)

    first, replay = calls
    assert first["list_markers"] == {"#/texts/1": "-", "#/texts/2": "-"}
    assert first["separated_list_refs"] == frozenset()
    assert first["inline_list_joins"] == {"#/texts/1"}
    assert replay["separated_list_refs"] == frozenset({"#/texts/1"})
    texts, report = _published(chunks)
    assert texts[0] == f"{LEAD}\n\n- {ITEMS[0]}\n- {ITEMS[1]}"
    assert report["status"] == "pass"
    assert report["source_lineage"]["fidelity"][
        "source_coverage_issues"] == []


def _write_two_lists(path):
    """Write two lead-in and bullet list blocks, then a closing paragraph.

    Refs: first lead-in ``#/texts/0``, items 1-2; second lead-in 3, items
    4-5; closing paragraph 6.  The second list opens with an ineligible
    bullet, so its inline join passes the fidelity audit.
    """
    doc = pytest.importorskip("docling_core.types.doc")
    document = doc.DoclingDocument(name="book")
    document.add_page(page_no=1, size=doc.Size(width=612, height=792))
    top = 60

    def add(text, prefix=""):
        nonlocal top
        prov = doc.ProvenanceItem(
            page_no=1, charspan=(0, len(prefix + text)),
            bbox=doc.BoundingBox(l=72, t=top, r=420, b=top + 14,
                                 coord_origin=doc.CoordOrigin.TOPLEFT))
        top += 20
        return prov

    for lead, items in ((LEAD, ITEMS), (SECOND_LEAD, SECOND_ITEMS)):
        document.add_text(
            label=doc.DocItemLabel.TEXT, text=lead, prov=add(lead))
        group = document.add_list_group()
        for text in items:
            document.add_list_item(text=text, marker=BULLET, parent=group,
                                   prov=add(text, f"{BULLET} "))
    document.add_text(label=doc.DocItemLabel.TEXT, text=TAIL, prov=add(TAIL))
    path.write_text(document.model_dump_json(indent=2), encoding="utf-8")


def test_replay_separates_only_the_join_the_failed_report_names(
        monkeypatch, tmp_path):
    """An observed join whose item the report covers stays inline."""
    _stub_chunk_models(monkeypatch)
    document = tmp_path / "book.json"
    _write_two_lists(document)
    calls = _spy_coalescer(monkeypatch)
    chunks = tmp_path / "book_chunks.jsonl"

    rag.chunk_document(document, chunks, structure_profile=PROFILE)

    first, replay = calls
    assert first["inline_list_joins"] == {"#/texts/1", "#/texts/4"}
    assert replay["separated_list_refs"] == frozenset({"#/texts/1"})
    texts, report = _published(chunks)
    text = "\n".join(texts)
    assert f"{LEAD}\n\n- {ITEMS[0]}" in text
    assert f"{SECOND_LEAD} - {SECOND_ITEMS[0]}\n" in text
    assert report["status"] == "pass"


def test_passing_first_pass_without_an_inline_join_is_published_once(
        monkeypatch, tmp_path):
    _stub_chunk_models(monkeypatch)
    document = tmp_path / "book.json"
    _write_document(document, lead=LEAD[:-len(" for example,")] + ".")
    calls = _spy_coalescer(monkeypatch)

    rag.chunk_document(
        document, tmp_path / "book_chunks.jsonl", structure_profile=PROFILE)

    assert len(calls) == 1
    assert calls[0]["inline_list_joins"] == set()


@pytest.mark.parametrize("opening", ["", "All emphasis added."])
def test_observed_join_whose_report_passes_is_not_replayed(
        monkeypatch, tmp_path, opening):
    """The observation alone never changes output.

    ``test_list_boundary_characterization`` pins the published text.
    """
    _stub_chunk_models(monkeypatch)
    document = tmp_path / "book.json"
    _write_document(document, items=(opening, *ITEMS))
    calls = _spy_coalescer(monkeypatch)

    rag.chunk_document(
        document, tmp_path / "book_chunks.jsonl", structure_profile=PROFILE)

    assert [call["inline_list_joins"] for call in calls] == [{"#/texts/1"}]
    assert calls[0]["separated_list_refs"] == frozenset()


def test_observed_split_item_tail_whose_report_passes_is_not_replayed(
        monkeypatch, tmp_path):
    """An in-text dash opening a split item's tail is joined back inline."""
    _stub_word_tokenized_models(monkeypatch)
    document = tmp_path / "book.json"
    _write_document(document, lead=SHORT_LEAD, items=(SPLIT_ITEM, ITEMS[1]))
    calls = _spy_coalescer(monkeypatch)
    chunks = tmp_path / "book_chunks.jsonl"

    rag.chunk_document(
        document, chunks, max_tokens=44, structure_profile=PROFILE)

    assert [call["inline_list_joins"] for call in calls] == [{"#/texts/1"}]
    texts, report = _published(chunks)
    assert texts[0] == f"{SHORT_LEAD}\n- {SPLIT_ITEM}"
    assert report["status"] == "pass"


def test_both_triggers_replay_only_the_order_and_still_fail(
        monkeypatch, tmp_path):
    """h03 precedence is fail-closed: the list item stays uncovered.

    Only the first pass's real report also gains a named deferred-group
    edge; its real fidelity audit names the inline-joined item.  The order
    replay runs exactly as before, and its own real report still fails
    ``source_token_fidelity``.
    """
    _stub_chunk_models(monkeypatch)
    document = tmp_path / "book.json"
    _write_document(document)
    calls = _spy_coalescer(monkeypatch)
    violations = []
    recover = rag._recover_bound_source_enrichments
    build = rag._quality_core.build_quality_report

    def enrichments(*args, **kwargs):
        violations.append(kwargs["reading_order_violations"])
        return dataclasses.replace(
            recover(*args, **kwargs), deferred_text_groups=DEFERRED)

    def build_report(**kwargs):
        report = build(**kwargs)
        if len(violations) == 1:
            report["status"] = "fail"
            report["source_lineage"]["fidelity"]["geometry_violations"] = [
                *report["source_lineage"]["fidelity"]["geometry_violations"],
                {"before_ref": "#/texts/42", "after_ref": "#/texts/39",
                 "page": 4}]
            for check in report["checks"]:
                if check["name"] == "same_page_reading_order":
                    check["status"] = "fail"
        return report

    monkeypatch.setattr(rag, "_recover_bound_source_enrichments", enrichments)
    monkeypatch.setattr(rag._quality_core, "build_quality_report", build_report)
    chunks = tmp_path / "book_chunks.jsonl"

    with pytest.raises(RuntimeError, match=(
            "^Corpus quality gate failed: source_token_fidelity$")):
        rag.chunk_document(document, chunks, structure_profile=PROFILE)

    assert violations == [frozenset(), NAMED]
    assert [call["separated_list_refs"] for call in calls] == [
        frozenset(), frozenset()]
    assert calls[0]["inline_list_joins"] == {"#/texts/1"}
    texts, report = _published(chunks)
    assert texts[0].startswith(f"{LEAD} - {ITEMS[0]}")
    assert report["source_lineage"]["fidelity"][
        "source_coverage_issues"] == ["#/texts/1"]
