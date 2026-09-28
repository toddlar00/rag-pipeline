"""Source-order fixes found on page-bounded casebook excerpts.

Every fixture is synthetic; no private textbook text is used.
"""

from types import SimpleNamespace

import source_fidelity_core
import rag


_ONE_LINE_SOURCE = (
    'Alpha held so .... " Roe v.  Delta Co., 1 U.S. 2 (1900). '
    'Beta agreed .... " Roe v.  Echo Co., 3 U.S. 4 (1901).')
# Token-bounded splitting breaks after "...." and after the case-name "v.".
_SPLIT_FRAGMENT = (
    'Alpha held so ....\n" Roe v.\nDelta Co., 1 U.S.\n2 (1900).\n'
    'Beta agreed ....\n" Roe v.\nEcho Co., 3 U.S.\n4 (1901).')


def _plain_item(text, ref="#/texts/9"):
    return SimpleNamespace(
        self_ref=ref, label=SimpleNamespace(value="text"), text=text, orig=text)


def test_plain_one_line_source_authorizes_split_duplicate_lines():
    normalized = rag._normalize_source_chunk_text(
        _SPLIT_FRAGMENT, "body", preserve_source_identity=True,
        source_items=[_plain_item(_ONE_LINE_SOURCE)])

    assert normalized.count('" Roe v.') == 2
    assert source_fidelity_core.lexical_tokens(normalized) == (
        source_fidelity_core.lexical_tokens(_SPLIT_FRAGMENT))


def test_split_duplicate_lines_without_a_source_window_are_still_deduplicated():
    unrelated = rag._normalize_source_chunk_text(
        _SPLIT_FRAGMENT, "body", preserve_source_identity=True,
        source_items=[_plain_item("An unrelated one-line source sentence.")])
    once = rag._normalize_source_chunk_text(
        _SPLIT_FRAGMENT, "body", preserve_source_identity=True,
        source_items=[_plain_item(
            'Alpha held so .... " Roe v.  Delta Co., 1 U.S. 2 (1900).')])

    assert unrelated.count('" Roe v.') == 1
    assert once.count('" Roe v.') == 1


def _footnote(name, boxes, *, page=5, count=3, source="footnote"):
    """A sidecar record whose lineage spans the given BOTTOMLEFT boxes."""
    return {
        "text": f"Synthetic note {name}.",
        "metadata": {
            "content_type": source, "content_source": source,
            "page_start": page, "page_end": page,
            "source_items": [{
                "ref": f"#/texts/{name}{index}", "label": source,
                "oracle_lexical_count": count,
                "spans": [{"provenance_index": 0, "page": page,
                           "bbox": list(box), "origin": "BOTTOMLEFT"}],
                "scope": {"provenance_indexes": [0]},
            } for index, box in enumerate(boxes)],
        },
    }


def _names(records):
    return [record["text"].split()[-1].rstrip(".") for record in records]


def _body_then(*footnotes):
    body = _footnote("body", [(50, 600, 400, 500)], source="body")
    return rag._reorder_footnote_sidecars([body, *footnotes])


def test_grouped_bottom_row_footnote_follows_the_notes_above_it():
    # Two same-row fragments recovered as one group, emitted before notes
    # that sit above them on the page (left, top, right, bottom).
    group = _footnote("G", [(50, 100, 150, 80), (120, 100, 400, 80)])
    upper = _footnote("C", [(50, 300, 400, 260)])
    lower = _footnote("D", [(60, 200, 380, 180)])

    assert _names(_body_then(group, upper, lower)) == ["body", "C", "D", "G"]


def test_footnote_rows_follow_geometry_when_source_order_disagrees():
    left_top = _footnote("A", [(89, 300, 143, 290)])
    right_top = _footnote("B", [(143, 300, 229, 290)])
    left_mid = _footnote("P", [(89, 280, 137, 270)])
    lower = _footnote("X", [(70, 250, 162, 230)])
    right_mid = _footnote("Q", [(136, 280, 363, 270)])

    assert _names(_body_then(left_top, right_top, left_mid, lower, right_mid)) == [
        "body", "A", "B", "P", "Q", "X"]


def test_geometrically_valid_footnote_slot_is_returned_unchanged():
    upper = _footnote("C", [(50, 300, 400, 260)])
    # Same row, right before left: no vertical constraint between them.
    right = _footnote("R", [(220, 200, 400, 180)])
    left = _footnote("L", [(50, 200, 200, 180)])
    tokenless = _footnote("Z", [(50, 400, 60, 395)], count=0)

    slot = [upper, right, left, tokenless]
    reordered = _body_then(*slot)

    assert [id(record) for record in reordered[1:]] == [id(record) for record in slot]


def test_conflicting_footnote_geometry_leaves_the_slot_unchanged():
    # R spans rows above and below S, so each must precede the other.
    straddling = _footnote("R", [(50, 300, 400, 280), (50, 100, 400, 80)])
    middle = _footnote("S", [(60, 200, 380, 180)])

    assert _names(_body_then(middle, straddling)) == ["body", "S", "R"]


def test_footnote_slot_with_page_order_reason_is_not_permuted():
    group = _footnote("G", [(50, 100, 400, 80)])
    upper = _footnote("C", [(50, 300, 400, 260)])
    body = _footnote("body", [(50, 600, 400, 500)], source="body")
    slot = [group, upper]
    placed = rag._order_footnote_slot_by_source_geometry(slot)
    group["metadata"][
        rag._quality_core.PAGE_ORDER_REASON_FIELD] = (
            rag._quality_core.FOOTNOTE_AFTER_CONTINUATION_REASON)
    guarded = rag._order_footnote_slot_by_source_geometry(slot)

    assert _names([body, *placed]) == ["body", "C", "G"]
    assert guarded == slot


def test_multi_line_plain_source_keeps_nearby_line_deduplication():
    source = "Hdr\nBody one.\nHdr\nBody two."

    normalized = rag._normalize_source_chunk_text(
        source, "body", preserve_source_identity=True,
        source_items=[_plain_item(source)])

    assert normalized.count("Hdr") == 1


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
    return [
        token for record in records
        for token in source_fidelity_core.lexical_tokens(record["text"])]


def test_lineage_free_numbered_edge_keeps_legacy_relocation():
    # Characterization: records without source lineage keep today's merge.
    records = [
        _record("The committee describes the procedure as follows:"),
        _record("1. under rule 4 of the manual.\n\nChoose a sample."),
    ]

    repaired = _coalesce(records)

    assert len(repaired) == 1
    assert repaired[0]["text"].endswith("1. under rule 4 of the manual.")


def test_lineage_bound_numbered_edge_is_never_moved_out_of_source_order():
    records = [
        _record("The committee describes the procedure as follows:",
                refs=["#/texts/0"]),
        _record(
            "1. under rule 4 of the manual.\n\nChoose a sample and inspect it."
            "\n\n2. Remove the old layer.",
            refs=["#/texts/2", "#/texts/1", "#/texts/3"]),
    ]
    expected = _tokens(records)

    repaired = _coalesce(records)

    # Relocating the numbered line would break the records' lineage order,
    # which the fidelity gate rejects; the records stay as they were.
    assert _tokens(repaired) == expected
    assert [record["text"] for record in repaired] == [
        record["text"] for record in records]


def test_lineage_bound_continuation_without_numbered_edge_still_merges():
    # Characterization: only the numbered-edge relocation is refused.
    records = [
        _record("Institutions have", refs=["#/texts/0"]),
        _record("increasingly resisted inquiry.", refs=["#/texts/1"]),
    ]

    repaired = _coalesce(records)

    assert len(repaired) == 1
    assert repaired[0]["text"] == "Institutions have increasingly resisted inquiry."


# --- Erased section-marker glyphs must not take an eligible item's token. ---

def _fidelity_item(ref, text, *, top=10, left=10, right=200):
    return {
        "self_ref": ref, "label": "text", "text": text,
        "prov": [{"page_no": 1, "charspan": [0, len(text)],
                  "bbox": {"l": left, "t": top, "r": right, "b": top + 10,
                           "coord_origin": "TOPLEFT"}}],
    }


def _fidelity_lineage(item):
    descriptor, issues = source_fidelity_core.source_descriptor(item)
    assert issues == []
    text = source_fidelity_core.source_item_text(item)
    tokens = source_fidelity_core.lexical_tokens(text)
    return {
        "ref": item["self_ref"], "label": item["label"], "parent_refs": [],
        "spans": descriptor["spans"],
        "scope": {"provenance_indexes": list(range(len(descriptor["spans"])))},
        "source_text_sha256": descriptor["source_text_sha256"],
        "source_lexical_sha256": descriptor["source_lexical_sha256"],
        "source_lexical_count": descriptor["source_lexical_count"],
        "transform": "plain",
        "oracle_text_sha256": source_fidelity_core.text_sha256(text),
        "oracle_lexical_sha256": source_fidelity_core.lexical_sha256(tokens),
        "oracle_lexical_count": len(tokens),
        "recovery_sha256": None,
    }


def _audit_glyphs(text, items, eligible):
    record = {"text": text, "metadata": {
        "chunk_index": 0, "source_lineage_schema_version": 5,
        "source_items": [_fidelity_lineage(item) for item in items]}}
    source_fidelity_core.attach_record_attestations([record])
    return source_fidelity_core.audit_source_fidelity(
        records=[record], document={"texts": items, "tables": [], "pictures": []},
        eligible_refs={item["self_ref"] for item in eligible})


def test_erased_capital_marker_does_not_take_an_eligible_glyph_token():
    body = _fidelity_item("#/texts/0", "Alpha beta gamma")
    marker = _fidelity_item("#/texts/1", "I", top=300, left=5, right=10)
    glyph = _fidelity_item("#/texts/2", "i", top=315, left=5, right=12)

    audit = _audit_glyphs(
        "Alpha beta gamma\n\ni", [body, marker, glyph], [body, glyph])

    assert audit["source_coverage_issues"] == []
    assert audit["output_coverage_issues"] == []


def test_erased_marker_does_not_take_the_first_word_of_later_prose():
    first = _fidelity_item("#/texts/0", "End of the paragraph.")
    marker = _fidelity_item("#/texts/1", "A", top=30)
    prose = _fidelity_item("#/texts/2", "A court held that", top=50)

    audit = _audit_glyphs(
        "End of the paragraph.\n\nA court held that",
        [first, marker, prose], [first, prose])

    assert audit["source_coverage_issues"] == []


def test_kept_capital_outside_the_erased_range_is_still_explained():
    body = _fidelity_item("#/texts/0", "Alpha")
    capital = _fidelity_item("#/texts/1", "K", top=300)
    lower = _fidelity_item("#/texts/2", "k", top=315)

    audit = _audit_glyphs("Alpha\n\nK\n\nk", [body, capital, lower], [body, lower])

    assert audit["source_coverage_issues"] == []
    assert audit["output_coverage_issues"] == []


def test_marker_retry_still_rejects_insertions_and_reports_omissions():
    body = _fidelity_item("#/texts/0", "Alpha beta gamma")
    marker = _fidelity_item("#/texts/1", "I", top=300, left=5, right=10)
    glyph = _fidelity_item("#/texts/2", "i", top=315, left=5, right=12)
    items, eligible = [body, marker, glyph], [body, glyph]

    inserted = _audit_glyphs("Alpha beta gamma\n\ni\n\ni\n\ni", items, eligible)
    omitted = _audit_glyphs("Alpha beta gamma", items, eligible)

    assert inserted["output_coverage_issues"] == [0]
    assert omitted["source_coverage_issues"] == ["#/texts/2"]


def test_erased_marker_predicate_mirrors_chunker_section_marker_erasure():
    import chunking_core

    for text in ("I", "J", " A ", "\tC", "K", "i", "AB", "A.", "a"):
        assert source_fidelity_core.chunker_erases_section_marker(text) == (
            chunking_core._normalize_text(text) == ""), text


# --- A native-rebuilt paragraph split across raw chunks is emitted once. ---

def _text_item(ref, text):
    return SimpleNamespace(
        self_ref=ref, label="text", content_layer="body", text=text, prov=[])


def _raw(text, *items):
    return SimpleNamespace(
        text=text, meta=SimpleNamespace(headings=[], doc_items=list(items)))


def test_native_rebuild_split_into_a_later_mixed_chunk_is_emitted_once():
    paragraph = _text_item(
        "#/texts/1", "First corrupt half. Second corrupt half.")
    following = _text_item("#/texts/2", "Following paragraph text.")
    note = SimpleNamespace(
        self_ref="#/texts/3", label="footnote", content_layer="body",
        text="7 Synthetic footnote source.", prov=[])
    document = SimpleNamespace(
        texts=[paragraph, following, note], pictures=[], tables=[],
        key_value_items=[], form_items=[])
    recovered = "First restored half. Second restored half."

    # The footnote in the mixed chunk is held back for sidecar emission, so
    # the remaining items are rebuilt from their full display text.
    prepared = rag._prepare_source_preserving_chunks(
        [_raw("First corrupt half.", paragraph),
         _raw("Second corrupt half. Following paragraph text. "
              "7 Synthetic footnote source.", paragraph, following, note)],
        document, lambda text: len(text.split()), 100,
        structural_ranges=set(),
        text_overrides={paragraph.self_ref: recovered},
        text_rebuild_refs={paragraph.self_ref})

    published = "\n\n".join(entry[0] for entry in prepared)
    assert published.count("First restored half.") == 1
    assert published.count("Second restored half.") == 1
    assert "Following paragraph text." in published
    assert [str(item.self_ref) for entry in prepared for item in entry[2]].count(
        paragraph.self_ref) == 1


def _positioned_item(ref, text, top, *, label="text"):
    return SimpleNamespace(
        self_ref=ref, label=label, content_layer="body", text=text,
        prov=[SimpleNamespace(
            page_no=7, charspan=(0, len(text)),
            bbox=SimpleNamespace(
                l=1.0, t=top, r=10.0, b=top - 1.0,
                coord_origin=SimpleNamespace(value="BOTTOMLEFT")))])


def test_native_rebuild_tail_in_a_layout_chunk_is_not_emitted_again():
    paragraph = _positioned_item(
        "#/texts/1", "First corrupt half. Second corrupt half.", 40.0)
    following = _positioned_item("#/texts/2", "Following paragraph text.", 30.0)
    left = _positioned_item("#/texts/3", "Grouped left fragment.", 20.0)
    right = _positioned_item("#/texts/4", "Grouped right fragment.", 20.0)
    # A footnote in the mixed chunk rules out the verified-replay path.
    note = _positioned_item(
        "#/texts/5", "7 Synthetic footnote source.", 5.0, label="footnote")
    document = SimpleNamespace(
        texts=[paragraph, following, left, right, note], pictures=[],
        tables=[], key_value_items=[], form_items=[])
    recovered = "First restored half. Second restored half."
    group = rag.SourceTextGroupRecovery(
        text="Grouped left fragment. Grouped right fragment.",
        refs=("#/texts/3", "#/texts/4"), page=7)

    # The mixed chunk holds a recovered group, so it takes the layout path.
    prepared = rag._prepare_source_preserving_chunks(
        [_raw("First corrupt half.", paragraph),
         _raw("Second corrupt half. Following paragraph text. Grouped left "
              "fragment. Grouped right fragment. 7 Synthetic footnote source.",
              paragraph, following, left, right, note)],
        document, lambda text: len(text.split()), 100,
        structural_ranges=set(),
        text_overrides={paragraph.self_ref: recovered},
        text_rebuild_refs={paragraph.self_ref},
        text_group_recoveries=(group,))

    published = "\n\n".join(entry[0] for entry in prepared)
    assert published.count("First restored half.") == 1
    assert "Following paragraph text." in published
    assert [str(item.self_ref) for entry in prepared for item in entry[2]].count(
        paragraph.self_ref) == 1


def test_native_rebuild_published_by_a_layout_chunk_is_not_repeated_later():
    paragraph = _positioned_item(
        "#/texts/1", "First corrupt half. Second corrupt half.", 40.0)
    following = _positioned_item("#/texts/2", "Following paragraph text.", 30.0)
    left = _positioned_item("#/texts/3", "Grouped left fragment.", 20.0)
    right = _positioned_item("#/texts/4", "Grouped right fragment.", 20.0)
    note = _positioned_item(
        "#/texts/5", "7 Synthetic footnote source.", 5.0, label="footnote")
    document = SimpleNamespace(
        texts=[paragraph, following, left, right, note], pictures=[],
        tables=[], key_value_items=[], form_items=[])
    group = rag.SourceTextGroupRecovery(
        text="Grouped left fragment. Grouped right fragment.",
        refs=("#/texts/3", "#/texts/4"), page=7)

    # The paragraph opens in the layout chunk and continues alone afterwards.
    prepared = rag._prepare_source_preserving_chunks(
        [_raw("First corrupt half. Following paragraph text. Grouped left "
              "fragment. Grouped right fragment. 7 Synthetic footnote source.",
              paragraph, following, left, right, note),
         _raw("Second corrupt half.", paragraph)],
        document, lambda text: len(text.split()), 100,
        structural_ranges=set(),
        text_overrides={
            paragraph.self_ref: "First restored half. Second restored half."},
        text_rebuild_refs={paragraph.self_ref},
        text_group_recoveries=(group,))

    published = "\n\n".join(entry[0] for entry in prepared)
    assert published.count("First restored half.") == 1
    assert [str(item.self_ref) for entry in prepared for item in entry[2]].count(
        paragraph.self_ref) == 1
