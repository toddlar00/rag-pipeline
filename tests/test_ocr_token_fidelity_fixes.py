"""Fresh-OCR text keeps its source tokens through merges and normalization.

Fresh OCR of a scanned excerpt split a paragraph's first line into its own
layout item, so a line-end hyphen can end one source item (``Cabinet-``)
while the next starts with the rest (``level``). OCR can also read a hyphen
away (``threejudge``). Three pipeline steps changed such lexical tokens and
so failed ``source_token_fidelity``, or its companion structural check:

* the same-heading continuation merge removed the hyphen between two
  records (``Cabinetlevel``);
* normalization applied the known fused-term spellings to source-bound text
  (``threejudge`` -> ``three-judge``);
* the publication check rejected every known fused term, even one a source
  item attests.

Source-bound text now keeps both tokens and attested spellings; text without
lineage keeps the established behaviour. All text is synthetic.
"""

import pytest

import chunking_core
import quality_core
import rag
import source_fidelity_core


def _record(text, *, refs=None, page=4, heading="Discussion"):
    metadata = {
        "headings": [heading],
        "section_path": heading,
        "content_type": "author_narrative",
        "content_source": "body",
        "page_start": page,
        "page_end": page,
        "page_range": f"pp.{page}-{page}",
        "case_names": [],
        "primary_case": None,
        "cross_references": [],
        "chapter_num": None,
        "chapter_title": None,
        "token_count": len(text.split()),
    }
    if refs is not None:
        metadata["source_items"] = [
            {"ref": ref, "label": "text",
             "spans": [{"provenance_index": 0}],
             "scope": {"provenance_indexes": [0]}}
            for ref in refs]
    return {"text": text, "metadata": metadata}


def _coalesce(records):
    return rag._coalesce_chunk_boundaries(
        records, lambda text: len(text.split()), 100)


def _tokens(records):
    return [token for record in records
            for token in source_fidelity_core.lexical_tokens(record["text"])]


LEFT = "The agencies are sited in one of the Cabinet-"
RIGHT = "level departments of the executive branch."


def test_source_bound_merge_keeps_the_hyphen_between_source_items():
    records = [_record(LEFT, refs=["#/texts/0"]),
               _record(RIGHT, refs=["#/texts/1"])]
    expected = _tokens(records)

    repaired = _coalesce(records)

    assert len(repaired) == 1
    assert "Cabinet-level departments" in repaired[0]["text"]
    assert _tokens(repaired) == expected


def test_one_source_bound_side_is_enough_to_keep_the_hyphen():
    records = [_record(LEFT, refs=["#/texts/0"]), _record(RIGHT)]

    repaired = _coalesce(records)

    assert "Cabinet-level" in repaired[0]["text"]


def test_lineage_free_merge_keeps_the_discretionary_join():
    # Characterization: text without lineage still reads a lowercase
    # continuation after a line-end hyphen as one wrapped word.
    repaired = _coalesce([_record("the ques-"), _record("tion was open.")])

    assert len(repaired) == 1
    assert "question was open." in repaired[0]["text"]


def test_source_bound_normalization_keeps_an_attested_fused_spelling():
    text = "Friendly, writing for a threejudge District Court, held"

    normalized = rag._normalize_source_chunk_text(text, "body")

    assert "threejudge" in normalized
    assert (source_fidelity_core.lexical_tokens(normalized)
            == source_fidelity_core.lexical_tokens(text))


def test_source_bound_list_markdown_also_skips_fused_terms():
    # Protected Markdown list rows take a separate normalization branch.
    text = "- a clientlawyer privilege\n- a second row"

    normalized = rag._normalize_source_chunk_text(text, "body")

    assert "clientlawyer" in normalized and "client-lawyer" not in normalized


def test_generic_normalization_still_repairs_fused_terms():
    assert rag._normalize_text("a threejudge court") == "a three-judge court"
    assert (chunking_core._normalize_text("a clientlawyer privilege")
            == "a client-lawyer privilege")


def test_core_option_skips_only_the_fused_terms():
    text = "a clientlawyer privilege"

    assert (chunking_core._normalize_text(text, repair_fused_terms=False)
            == "a clientlawyer privilege")


def test_the_source_bound_flag_does_not_outlive_its_call(monkeypatch):
    rag._normalize_source_chunk_text("a threejudge court", "body")
    assert rag._normalize_text("a threejudge court") == "a three-judge court"

    def failing(*args, **kwargs):
        raise RuntimeError("synthetic failure")

    monkeypatch.setattr(rag, "_normalize_source_bound_chunk_text", failing)
    with pytest.raises(RuntimeError, match="synthetic failure"):
        rag._normalize_source_chunk_text("a threejudge court", "body")
    assert rag._SOURCE_BOUND_NORMALIZATION.get() is False
    assert rag._normalize_text("a threejudge court") == "a three-judge court"


def _validate(record):
    title = "Chapter 1: Admission"
    record["metadata"].update(
        {"chapter_num": 1, "chapter_title": title, "section_path": title})
    rag._validate_chunk_structure_for_publication(
        [record],
        scaffold=[{"level": 1, "title": title, "page": 4, "chapter_num": 1}],
        book_sections={},
        chapter_map={1: {"min_page": 1, "max_page": 19, "title": "Admission"}},
        chapter_titles={1: title},
    )


def test_publication_check_accepts_a_source_attested_fused_spelling():
    _validate(_record("writing for a threejudge District Court",
                      refs=["#/texts/0"]))


def test_publication_check_still_rejects_a_lineage_free_fused_term():
    with pytest.raises(RuntimeError, match="known fused term"):
        _validate(_record("writing for a threejudge District Court"))


def test_quality_invariant_matches_the_publication_check():
    attested = _record("writing for a threejudge District Court",
                       refs=["#/texts/0"])
    lineage_free = _record("writing for a threejudge District Court")

    issues = quality_core._normalization_issues([attested, lineage_free])

    assert issues == {"known_fused_term": [1]}
