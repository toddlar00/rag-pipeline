import copy
import re

import pytest

import rag


def test_chunk_hash_distinguishes_text_context_boundaries():
    left = {"text": "ab", "metadata": {"context": "c"}}
    right = {"text": "a", "metadata": {"context": "bc"}}

    assert rag._chunk_hash(left) != rag._chunk_hash(right)


def test_chunk_hash_is_stable_for_unicode_content():
    record = {"text": "Marbury v. Madison — § 1", "metadata": {"context": "Résumé"}}

    assert rag._chunk_hash(record) == rag._chunk_hash(record)
    assert rag._chunk_id(record).startswith("chunk_")


def test_chunk_hash_tracks_metadata_but_not_sequence_position():
    original = {
        "text": "A legal rule.",
        "metadata": {"chapter_num": 1, "content_type": "author_narrative", "chunk_index": 2},
    }
    reordered = {
        "text": original["text"],
        "metadata": {**original["metadata"], "chunk_index": 99},
    }
    reclassified = {
        "text": original["text"],
        "metadata": {**original["metadata"], "content_type": "statutory_excerpt"},
    }

    assert rag._chunk_hash(original) == rag._chunk_hash(reordered)
    assert rag._chunk_hash(original) != rag._chunk_hash(reclassified)
    assert rag._chunk_id(original) == rag._chunk_id(reordered)
    assert rag._chunk_id(original) == rag._chunk_id(reclassified)


def test_chunk_source_id_tracks_text_and_page_provenance():
    original = {
        "text": "The same quoted rule.",
        "metadata": {
            "source_file": "Book.pdf",
            "page_start": 10,
            "page_end": 10,
            "page_range": "p.10",
        },
    }
    other_page = {
        "text": original["text"],
        "metadata": {**original["metadata"], "page_start": 11,
                     "page_end": 11, "page_range": "p.11"},
    }

    assert rag._chunk_id(original) != rag._chunk_id(other_page)


def test_enrich_chunk_deduplicates_cross_references_in_source_order():
    record = rag.enrich_chunk(
        "See Chapter 12, Section A.1, then Chapter 3 and Chapter 12, Section A.1.",
        headings=None,
        chunk_index=0,
        total_chunks=1,
        total_pages=1,
    )

    assert record["metadata"]["cross_references"] == ["Ch.12.A.1", "Ch.3"]


def test_cluster_embeddings_handles_empty_input():
    assert rag._cluster_embeddings([], 3) == []


def test_cluster_embeddings_handles_identical_vectors():
    clusters = rag._cluster_embeddings([[1.0, 1.0]] * 4, 3)

    assert sorted(index for cluster in clusters for index in cluster) == [0, 1, 2, 3]


def test_chroma_filter_uses_and_for_multiple_conditions():
    assert rag._build_chroma_where("case_opinion", 4) == {
        "$and": [
            {"content_type": {"$eq": "case_opinion"}},
            {"chapter_num": {"$eq": 4}},
        ]
    }
    assert rag._build_chroma_where(chapter_num=0) == {
        "chapter_num": {"$eq": 0}
    }
    assert rag._build_chroma_where() is None


def test_markdown_assembly_understands_scaffold_separator():
    record = {
        "text": "The court applies the rule.",
        "metadata": {
            "chapter_num": 2,
            "chapter_title": "Jurisdiction",
            "section_path": "Chapter 2 > A. Personal Jurisdiction > 1. Minimum Contacts",
            "content_type": "author_narrative",
        },
    }

    markdown = rag._assemble_markdown([record])

    assert markdown.startswith("# Chapter 2 - Jurisdiction\n")
    assert "# Chapter 2 - Jurisdiction" in markdown
    assert "## A. Personal Jurisdiction" in markdown
    assert "### 1. Minimum Contacts" in markdown


def test_markdown_assembly_preserves_roman_part_designation():
    record = {
        "text": "Institutions shape professional practice.",
        "metadata": {
            "chapter_num": 4,
            "chapter_title": "Part IV: Institutions and Practice",
            "section_path": (
                "Part IV: Institutions and Practice > I. Institutions"),
            "content_type": "author_narrative",
        },
    }

    markdown = rag._assemble_markdown([record])

    assert "# Part IV: Institutions and Practice" in markdown
    assert "Chapter 4" not in markdown
    assert markdown.count("Institutions and Practice") == 1
    assert "## I. Institutions" in markdown


def test_markdown_assembly_embeds_verified_figure_asset_path():
    record = {
        "text": "Figure: Source diagram referenced by the adjacent text.",
        "metadata": {
            "chapter_num": 10,
            "chapter_title": "Sample Devices",
            "section_path": "Chapter 10 > Calibration Problem",
            "content_type": "figure",
            "page_start": 981,
            "figure_image_path": "assets/figure-p0981-23.png",
        },
    }

    markdown = rag._assemble_markdown(
        [record], figure_asset_prefix="../")

    assert (
        "![Source figure from PDF page 981]"
        "(../assets/figure-p0981-23.png)" in markdown)


def _markdown_record(text, *, content_type="author_narrative", chapter=1,
                     page=10, stable_id="chunk_1111111111111111"):
    return {
        "text": text,
        "metadata": {
            "content_type": content_type,
            "content_source": (
                "footnote" if content_type == "footnote" else "body"),
            "chapter_num": chapter,
            "chapter_title": f"Chapter {chapter}" if chapter else None,
            "section_path": f"Chapter {chapter}" if chapter else "Preface",
            "page_start": page,
            "page_end": page,
            "stable_id": stable_id,
        },
    }


def _bind_exact_markdown_source(
        record, *, ref, source_text=None, label="text", parent_refs=(),
        extra_items=()):
    source_text = str(record["text"] if source_text is None else source_text)
    page = record["metadata"]["page_start"]
    digest = rag._source_fidelity_core.text_sha256(source_text)
    record["metadata"].update({
        "source_lineage_schema_version": (
            rag._quality_core.SOURCE_LINEAGE_SCHEMA_VERSION),
        "source_items": [{
            "ref": ref,
            "label": label,
            "parent_refs": list(parent_refs),
            "spans": [{
                "provenance_index": 0,
                "page": page,
                "bbox": [0.0, 1.0, 1.0, 0.0],
                "origin": "BOTTOMLEFT",
                "charspan": [0, len(source_text)],
            }],
            "source_text_sha256": digest,
            "transform": "plain",
            "oracle_text_sha256": digest,
        }, *extra_items],
    })
    return record


def test_markdown_currency_escape_is_idempotent_and_parity_aware():
    source = (
        "less than $100,000, receive $50,000; "
        "$ 1.3 million; $.50; $,50; $-25; $(30); attention & $$,' then")

    escaped = rag._escape_markdown_currency(source)

    assert escaped == (
        r"less than \$100,000, receive \$50,000; "
        r"\$ 1.3 million; \$.50; \$,50; \$-25; \$(30); "
        r"attention & \$\$,' then")
    assert rag._escape_markdown_currency(escaped) == escaped
    odd = "\\" + "$100"
    even = "\\" * 2 + "$100"
    assert rag._escape_markdown_currency(odd) == odd
    assert rag._escape_markdown_currency(even) == "\\" * 3 + "$100"


def test_markdown_currency_escape_preserves_protected_markdown_spans():
    source = (
        "`$100`\n```text\n$200\n```\n"
        "https://example.test/$300 "
        "[target](https://example.test/$400) "
        '<span data-price="$500">value</span> '
        "$$100+200$$ $x+1$ visible $600")

    escaped = rag._escape_markdown_currency(source)

    assert escaped == source.replace("visible $600", r"visible \$600")


def test_markdown_pandoc_literal_escape_keeps_social_handles_literal():
    source = (
        "Profile @sample_ reader; email reader@example.edu; "
        "`@code` $x@x$ https://example.test/@path; price $100")

    escaped = rag._escape_markdown_pandoc_literals(source)

    assert escaped == (
        r"Profile \@sample_ reader; email reader@example.edu; "
        r"`@code` $x@x$ https://example.test/@path; price \$100")
    assert rag._escape_markdown_pandoc_literals(escaped) == escaped


def test_markdown_source_literals_escape_hashtags_omissions_and_symbols():
    source = (
        "#witchhunt\n"
        "* * * What followed was omitted.\n"
        "[JUROR]: Yes.\n"
        "See [Sample Reference Code], [link](target), and [^1].\n"
        "The symbol $ motivates conduct; variables include & $$, too.")

    escaped = rag._escape_markdown_pandoc_literals(source)

    assert escaped == (
        r"\#witchhunt" "\n"
        r"\* \* \* What followed was omitted." "\n"
        r"\[JUROR]: Yes." "\n"
        r"See \[Sample Reference Code], [link](target), and [^1]." "\n"
        r"The symbol \$ motivates conduct; variables include & \$\$, too.")
    assert rag._escape_markdown_pandoc_literals(escaped) == escaped


def test_markdown_source_literals_escape_long_bracketed_attribution():
    source = (
        "[A separate review by Rivera, J., added a criticism of the sample "
        "policy rationale]: * * * Significantly, text.\n")

    escaped = rag._escape_markdown_pandoc_literals(source)

    assert escaped == (
        r"\[A separate review by Rivera, J., added a criticism of the sample "
        r"policy rationale]: \* \* \* Significantly, text."
        "\n")
    assert rag._escape_markdown_pandoc_literals(escaped) == escaped


@pytest.mark.parametrize("source", ("[Q]: Yes.\n", "[a]: Attribution.\n"))
def test_markdown_source_literals_escape_single_character_attribution(source):
    escaped = rag._escape_markdown_pandoc_literals(source)

    assert escaped == "\\" + source
    assert rag._escape_markdown_pandoc_literals(escaped) == escaped


def test_markdown_source_literals_escape_only_unmatched_fence_lines():
    unmatched = "```\nTreatise § 3.02[C][3].\n"

    escaped = rag._escape_markdown_pandoc_literals(unmatched)
    markdown = rag._escape_markdown_implicit_reference_literals(escaped)

    assert markdown == (
        r"\`\`\`" "\n"
        r"Treatise § 3.02\[C]\[3]." "\n")
    assert rag._escape_markdown_pandoc_literals(markdown) == markdown

    paired = "~~~text\n[x]\n~~~\n"
    assert rag._escape_markdown_pandoc_literals(paired) == paired
    assert rag._escape_markdown_implicit_reference_literals(paired) == paired


def test_markdown_source_literals_escape_zettlr_list_and_asterisk_syntax():
    source = "- 1) First item\n***\nredacted m****k and w*rd\n"

    escaped = rag._escape_markdown_pandoc_literals(source)

    assert escaped == (
        "- 1\\) First item\n"
        r"\*\*\*" "\n"
        r"redacted m\*\*\*\*k and w\*rd" "\n")
    assert rag._escape_markdown_pandoc_literals(escaped) == escaped
    assert rag._escape_markdown_pandoc_literals("- 2) Standalone\n") == (
        "- 2\\) Standalone\n")


def test_markdown_emphasis_uses_underscore_and_escapes_content():
    assert rag._markdown_emphasis("A_B costs $100 for @client") == (
        r"_A\_B costs \$100 for \@client_")
    assert rag._markdown_emphasis("path\\") == r"_path\\_"


def test_markdown_thematic_break_normalization_is_conservative():
    source = (
        "Before\n\n"
        "- - - - -- -\n\n"
        "Rule before source\n\n"
        "-----\n"
        "Following source text\n\n"
        "After\n\n"
        "Setext title\n-----\n\n"
        "```text\n- - - -\n```\n\n"
        "    - - - -\n")

    normalized = rag._normalize_markdown_thematic_breaks(source)

    expected = source.replace("- - - - -- -", "---", 1)
    expected = expected.replace("-----\nFollowing source text", (
        "---\n\nFollowing source text"), 1)
    assert normalized == expected
    assert rag._normalize_markdown_thematic_breaks(normalized) == normalized


def test_markdown_large_ordered_list_literal_escape_is_conservative():
    source = (
        "1) deliberate list\n"
        "99) deliberate list\n"
        "1983) wrapped citation\n"
        "1983\\) already escaped\n"
        "```text\n1983) code\n```\n"
        "    1983) indented code\n"
    )

    escaped = rag._escape_markdown_large_ordered_list_literals(source)

    assert escaped == source.replace(
        "1983) wrapped citation", r"1983\) wrapped citation", 1)
    assert rag._escape_markdown_large_ordered_list_literals(escaped) == escaped


def test_markdown_implicit_reference_escape_preserves_real_syntax():
    source = (
        "[a] ![b] [c](target) [d][ref] [e][] [f]{.class} [[g]] [^1]\n"
        "[h]: target\n[h]\n"
        r"\[q] \\[r] `code [s]` $[t]$ <span data-x=\"[u]\">[v]</span>"
        "\n    [w]\n~~~text\n[x]\n~~~\n")

    escaped = rag._escape_markdown_implicit_reference_literals(source)

    assert escaped.startswith(
        r"\[a] ![b] [c](target) [d][ref] [e][] [f]{.class} [[g]] [^1]")
    assert "[h]: target\n[h]\n" in escaped
    assert r"\[q] \\\[r]" in escaped
    assert r"`code [s]` $[t]$" in escaped
    assert r'<span data-x=\"[u]\">\[v]</span>' in escaped
    assert "    [w]\n~~~text\n[x]\n~~~" in escaped
    assert rag._escape_markdown_implicit_reference_literals(escaped) == escaped


def test_markdown_assembly_allows_at_most_one_blank_line_between_nodes():
    records = [
        _markdown_record(
            "First paragraph.", stable_id="chunk_aaaaaaaaaaaaaaaa"),
        _markdown_record(
            "Second paragraph.", stable_id="chunk_bbbbbbbbbbbbbbbb"),
    ]

    markdown = rag._assemble_markdown(records)

    assert not markdown.startswith("\n")
    assert markdown.endswith("\n")
    assert re.search(r"\n(?:[ \t]*\n){2,}", markdown) is None


def test_markdown_table_padding_is_canonical_and_parser_aware():
    source = (
        "Comparison table\n"
        "| Heading | Value | Empty |\n"
        "|:---------|------:|---|\n"
        r"| A\|B | $100 |  |")

    normalized = rag._normalize_markdown_table_cell_padding(source)

    assert normalized == (
        "Comparison table\n\n"
        "| Heading | Value | Empty |\n"
        "| :--------- | ------: | --- |\n"
        r"| A\|B | $100 |  |")
    assert rag._normalize_markdown_table_cell_padding(normalized) == normalized


def test_markdown_table_padding_fails_closed_for_malformed_input():
    malformed = "| A | B |\n|---|---|\n| only one cell |"

    assert rag._normalize_markdown_table_cell_padding(malformed) == malformed


def test_markdown_assembly_normalizes_table_padding_and_literals():
    table = _markdown_record(
        "| Amount | Source |\n|-----:|:---|\n| $100 | A\\|B |",
        content_type="table", stable_id="chunk_aaaaaaaaaaaaaaaa")

    markdown = rag._assemble_markdown([table])

    assert "| Amount | Source |" in markdown
    assert "| -----: | :--- |" in markdown
    assert r"| \$100 | A\|B |" in markdown


def test_markdown_assembly_separates_table_caption_from_header():
    table = _markdown_record(
        "Rule 1.18 Duties to Prospective Client\n"
        "| Rule language | Explanation |\n"
        "|---|---|\n"
        r"| A\|B | Text |",
        content_type="table", stable_id="chunk_aaaaaaaaaaaaaaaa")

    markdown = rag._assemble_markdown([table])

    assert (
        "Rule 1.18 Duties to Prospective Client\n\n"
        "| Rule language | Explanation |"
        in markdown)
    assert "| --- | --- |" in markdown
    assert r"| A\|B | Text |" in markdown
    assert re.search(r"\n(?:[ \t]*\n){2,}", markdown) is None


def test_page_markers_precede_new_headings_without_breaking_table_or_endnotes():
    table = _markdown_record(
        "| Stage | Result |\n|---|---|\n| Interview | Advice |",
        content_type="table", page=10,
        stable_id="chunk_aaaaaaaaaaaaaaaa")
    table["metadata"].update({
        "section_path": "Chapter 1 > First Page Heading",
        "pdf_page_start": 10,
        "pdf_page_end": 10,
    })
    same_page = _bind_exact_markdown_source(_markdown_record(
        "Supporting point.¹", page=10,
        stable_id="chunk_bbbbbbbbbbbbbbbb"), ref="#/texts/100")
    same_page["metadata"].update({
        "section_path": "Chapter 1 > First Page Heading",
        "pdf_page_start": 10,
        "pdf_page_end": 10,
    })
    footnote = _markdown_record(
        "1. Supporting authority.", content_type="footnote", page=10,
        stable_id="chunk_1111111111111111")
    footnote["metadata"].update({
        "section_path": "Chapter 1 > First Page Heading",
        "pdf_page_start": 10,
        "pdf_page_end": 10,
    })
    next_page = _markdown_record(
        "Second page body.", page=11,
        stable_id="chunk_cccccccccccccccc")
    next_page["metadata"].update({
        "section_path": "Chapter 1 > Second Page Heading",
        "pdf_page_start": 11,
        "pdf_page_end": 11,
    })

    markdown = rag._assemble_markdown(
        [table, same_page, footnote, next_page])

    assert re.findall(
        r"(?m)^\[PDF page [0-9]+\]$", markdown
    ) == ["[PDF page 10]", "[PDF page 11]"]
    assert markdown.index("[PDF page 10]") < markdown.index(
        "## First Page Heading") < markdown.index("<!-- TABLE -->")
    assert markdown.index("[PDF page 11]") < markdown.index(
        "## Second Page Heading") < markdown.index("Second page body.")
    assert "| Stage | Result |" in markdown
    assert "| --- | --- |" in markdown
    assert "Supporting point.[^1]" in markdown
    assert "[^1]: Supporting authority." in markdown
    assert markdown.count("## Endnotes") == 1


def test_markdown_assembly_uses_zettlr_emphasis_without_rewriting_source():
    case = _markdown_record(
        "The source quotes f * * * verbatim.", content_type="case_opinion",
        stable_id="chunk_aaaaaaaaaaaaaaaa")
    case["metadata"].update({
        "context": "A_client paid $100 to @counsel",
        "page_range": "p.10",
        "primary_case": None,
    })

    markdown = rag._assemble_markdown([case])

    assert r"_A\_client paid \$100 to \@counsel_" in markdown
    assert "_p.10_" in markdown
    assert r"The source quotes f \* \* \* verbatim." in markdown
    assert "<!-- CASE OPINION -->" in markdown
    assert "##### None" not in markdown
    assert "CASE: None" not in markdown


def test_markdown_chapter_introduction_uses_source_heading_without_label():
    introduction = _markdown_record(
        "The chapter begins with its substantive overview.",
        content_type="chapter_introduction",
        stable_id="chunk_aaaaaaaaaaaaaaaa",
    )
    introduction["metadata"].update({
        "chapter_title": "Chapter 1 · Introduction to Sample Systems",
        "section_path": (
            "Chapter 1 · Introduction to Sample Systems > §1.01 Introduction"
        ),
    })

    continuation = copy.deepcopy(introduction)
    continuation["text"] = "The overview continues in a second source chunk."
    continuation["metadata"]["stable_id"] = "chunk_bbbbbbbbbbbbbbbb"

    markdown = rag._assemble_markdown([introduction, continuation])

    assert "# Chapter 1 · Introduction to Sample Systems" in markdown
    assert "## §1.01 Introduction" in markdown
    assert markdown.count("<!-- CHAPTER INTRODUCTION -->") == 1
    assert "**Chapter Introduction**" not in markdown
    assert markdown.casefold().count("introduction") == 3


def test_markdown_chapter_introduction_tag_survives_interleaved_table_once():
    introduction = _markdown_record(
        "The chapter begins with its substantive overview.",
        content_type="chapter_introduction",
        stable_id="chunk_aaaaaaaaaaaaaaaa",
    )
    introduction["metadata"].update({
        "chapter_title": "Chapter 1 · Sample Operation",
        "section_path": "Chapter 1 · Sample Operation > §1.01 Introduction",
    })
    table = _markdown_record(
        "| Rule | Value |\n| --- | --- |\n| One | Two |",
        content_type="table", stable_id="chunk_bbbbbbbbbbbbbbbb")
    table["metadata"].update({
        "chapter_title": "Chapter 1 · Sample Operation",
        "section_path": "Chapter 1 · Sample Operation > §1.01 Introduction",
    })
    continuation = copy.deepcopy(introduction)
    continuation["text"] = "The overview resumes after the table."
    continuation["metadata"]["stable_id"] = "chunk_cccccccccccccccc"

    markdown = rag._assemble_markdown([introduction, table, continuation])

    assert markdown.count("<!-- CHAPTER INTRODUCTION -->") == 1


def test_markdown_case_heading_ignores_inferred_cross_case_citation():
    case = _markdown_record(
        "Delta is discussed inside this opinion.",
        content_type="case_opinion",
        stable_id="chunk_aaaaaaaaaaaaaaaa")
    case["metadata"].update({
        "primary_case": "Delta v. Echo",
        "section_path": "Chapter 8 > Sample v. Example",
        "headings": ["RIVERA, J."],
    })

    markdown = rag._assemble_markdown([case])

    assert "##### Delta v. Echo" not in markdown
    assert "<!-- CASE: Delta v. Echo -->" not in markdown
    assert "<!-- CASE OPINION -->" in markdown


def test_markdown_case_heading_uses_source_structural_path_without_duplicate():
    case = _markdown_record(
        "The Court states its holding.", content_type="case_opinion",
        stable_id="chunk_aaaaaaaaaaaaaaaa")
    case["metadata"].update({
        "primary_case": "Sample v. Example",
        "section_path": "Chapter 8 > SAMPLE V. EXAMPLE",
        "headings": ["RIVERA, J."],
    })

    markdown = rag._assemble_markdown([case])

    assert re.findall(
        r"(?mi)^#{1,6} sample v\. example$", markdown
    ) == ["## SAMPLE V. EXAMPLE"]
    assert "##### Sample v. Example" not in markdown
    assert "<!-- CASE: Sample v. Example -->" in markdown


def test_markdown_assembly_escapes_implicit_links_but_not_footnotes():
    body = _bind_exact_markdown_source(_markdown_record(
        "Quoted insertion [a].¹", stable_id="chunk_aaaaaaaaaaaaaaaa"),
        ref="#/texts/101")
    body["metadata"]["section_path"] = "Chapter 1 > a"
    footnote = _markdown_record(
        "1. Supporting authority.", content_type="footnote",
        stable_id="chunk_1111111111111111")

    markdown = rag._assemble_markdown([body, footnote])

    assert r"Quoted insertion \[a].[^1]" in markdown
    assert "[^1]: Supporting authority." in markdown
    assert r"\[^1]" not in markdown


def test_markdown_escapes_adjacent_bare_bracket_tokens_idempotently():
    source = "Treatise § 32.04[B][1]."

    escaped = rag._escape_markdown_implicit_reference_literals(source)

    assert escaped == r"Treatise § 32.04\[B]\[1]."
    assert rag._escape_markdown_implicit_reference_literals(escaped) == escaped


def test_markdown_assembly_escapes_editorial_brackets_in_endnote_blocks():
    body = _bind_exact_markdown_source(_markdown_record(
        "Quoted proposition.¹", stable_id="chunk_aaaaaaaaaaaaaaaa"),
        ref="#/texts/102")
    footnote = _markdown_record(
        "1. The comment gives this example:\n\n[A] seller must disclose.",
        content_type="footnote", stable_id="chunk_1111111111111111")

    markdown = rag._assemble_markdown([body, footnote])

    assert "[^1]: The comment gives this example:" in markdown
    assert r"    \[A] seller must disclose." in markdown


def test_markdown_currency_escape_covers_body_context_heading_and_endnote():
    body = _bind_exact_markdown_source(_markdown_record(
        "Repairs cost $100.¹", stable_id="chunk_aaaaaaaaaaaaaaaa"),
        ref="#/texts/103")
    body["metadata"]["context"] = "A $200 dispute"
    body["metadata"]["section_path"] = "Chapter 1 > Awards over $300"
    footnote = _markdown_record(
        "1. Filing fee of $50.", content_type="footnote",
        stable_id="chunk_1111111111111111")

    markdown = rag._assemble_markdown([body, footnote])

    assert r"Repairs cost \$100." in markdown
    assert r"_A \$200 dispute_" in markdown
    assert r"## Awards over \$300" in markdown
    assert r"[^1]: Filing fee of \$50." in markdown
    assert "1111111111111111" not in markdown
    assert "$x+1$" == rag._escape_markdown_currency("$x+1$")


def test_markdown_serializes_footnotes_and_links_exact_source_markers():
    body = _bind_exact_markdown_source(_markdown_record(
        "First proposition.¹ Second proposition.²",
        stable_id="chunk_aaaaaaaaaaaaaaaa"), ref="#/texts/104")
    first = _markdown_record(
        "1. Alpha citation.", content_type="footnote",
        stable_id="chunk_1111111111111111")
    second = _markdown_record(
        "2. Beta citation.", content_type="footnote",
        stable_id="chunk_2222222222222222")

    markdown = rag._assemble_markdown([body, first, second])

    assert "<details><summary>Footnote" not in markdown
    assert markdown.count("## Endnotes") == 1
    assert (
        "First proposition.[^1] Second proposition.[^2]"
        in markdown)
    assert "[^1]: Alpha citation." in markdown
    assert "[^2]: Beta citation." in markdown
    assert "1111111111111111" not in markdown
    assert "2222222222222222" not in markdown
    assert '<a name="rag-fn' not in markdown
    assert "](#rag-fn" not in markdown
    assert "Back to reference" not in markdown


def test_markdown_links_synthetic_caption_terminal_numeric_source_marker():
    caption = (
        "2026 Summary of Simulated Device Checks Requiring Manual Review1")
    body = _bind_exact_markdown_source(_markdown_record(
        caption + "\n| Status | Total |\n|---|---:|\n| Review | 10 |",
        content_type="table", stable_id="chunk_aaaaaaaaaaaaaaaa", page=12),
        ref="#/texts/7", source_text=caption, label="caption",
        parent_refs=("#/tables/2",),
        extra_items=({"ref": "#/tables/2", "label": "table"},))
    footnote = _markdown_record(
        "1. Source for sample statistics: Example Research Group.",
        content_type="footnote", stable_id="chunk_2222222222222222",
        page=12)

    endnotes = rag._markdown_endnote_records([body, footnote])
    body_replacements, heading_replacements = (
        rag._markdown_endnote_reference_replacements([body, footnote], endnotes))
    replacement = body_replacements[id(body)][0]
    assert heading_replacements == {}
    assert replacement[:2] == (len(caption) - 1, len(caption))
    assert endnotes[0]["reference_proof"] == "caption_terminal_decimal"
    assert endnotes[0]["reference_source_ref"] == "#/texts/7"
    assert endnotes[0]["reference_source_offset"] == len(caption) - 1
    assert endnotes[0]["reference_page"] == 12
    assert endnotes[0]["reference_chapter"] == 1
    rendered = rag._markdown_with_endnote_references(body, [replacement])["text"]
    assert rendered[:replacement[0]] == body["text"][:replacement[0]]
    assert rendered[replacement[0] + len(replacement[2]):] == (
        body["text"][replacement[1]:])

    markdown = rag._assemble_markdown([body, footnote])

    assert (
        "2026 Summary of Simulated Device Checks Requiring Manual Review[^1]"
        in markdown)
    assert "| Status | Total |" in markdown
    assert (
        "[^1]: Source for sample statistics: Example Research Group."
        in markdown)


def test_markdown_links_synthetic_star_and_heading_superscript_callouts():
    counsel_text = (
        "ALEX A. EXAMPLE, P.C. by ALEX A. EXAMPLE and TAYLOR B. SAMPLE, "
        "P.C. by TAYLOR B. SAMPLE, REDWOOD, for appellants.*")
    counsel = _bind_exact_markdown_source(_markdown_record(
        counsel_text, page=13, stable_id="chunk_aaaaaaaaaaaaaaaa"),
        ref="#/texts/8")
    heading = _markdown_record(
        "The report summarized the simulated sequence.", page=13,
        stable_id="chunk_bbbbbbbbbbbbbbbb")
    heading_display = "Stage¹ and Review Background"
    heading_ref = "#/texts/9"
    heading["metadata"].update({
        "section_path": f"Chapter 1 > {heading_display}",
        rag._heading_lineage.HEADING_SCHEMA_FIELD: (
            rag._heading_lineage.HEADING_LINEAGE_SCHEMA_VERSION),
        rag._heading_lineage.HEADING_PATH_FIELD: [heading_ref],
        rag._heading_lineage.DIRECT_HEADING_FIELD: [heading_ref],
        rag._heading_lineage.HEADING_COMPONENTS_FIELD: [{
            "display": heading_display,
            "occurrence_ids": [heading_ref],
            "binding": "source_heading",
        }],
    })
    star_note = _markdown_record(
        "* The contributor names in this synthetic fixture appear only in "
        "the sample record.", content_type="footnote", page=13,
        stable_id="chunk_1111111111111111")
    star_note["metadata"]["footnote_parent_stable_id"] = (
        "chunk_aaaaaaaaaaaaaaaa")
    numeric_note = _markdown_record(
        "1. The synthetic note links directly to the simulated heading "
        "marker.", content_type="footnote",
        page=13, stable_id="chunk_2222222222222222")
    numeric_note["metadata"]["footnote_parent_stable_id"] = (
        "chunk_bbbbbbbbbbbbbbbb")

    records = [counsel, star_note, heading, numeric_note]
    endnotes = rag._markdown_endnote_records(records)
    body_replacements, heading_replacements = (
        rag._markdown_endnote_reference_replacements(records, endnotes))
    evidence = {endnote["opening_label"]: endnote for endnote in endnotes}
    assert evidence["*"]["reference_proof"] == "terminal_star"
    assert evidence["*"]["reference_source_ref"] == "#/texts/8"
    assert evidence["*"]["reference_source_offset"] == len(counsel_text) - 1
    assert evidence["1"]["reference_proof"] == "unicode_superscript_digits"
    assert evidence["1"]["reference_source_ref"] == heading_ref
    assert evidence["1"]["reference_source_offset"] == 5
    assert evidence["1"]["reference_target"] == "heading"
    assert all(endnote["reference_page"] == 13 for endnote in endnotes)
    counsel_replacement = body_replacements[id(counsel)][0]
    rendered_counsel = rag._markdown_with_endnote_references(
        counsel, [counsel_replacement])["text"]
    assert rendered_counsel[:counsel_replacement[0]] == (
        counsel_text[:counsel_replacement[0]])
    assert rendered_counsel[
        counsel_replacement[0] + len(counsel_replacement[2]):
    ] == counsel_text[counsel_replacement[1]:]
    assert heading_replacements[id(heading)][heading_display][0][:2] == (5, 6)

    markdown = rag._assemble_markdown(
        records)

    assert counsel_text[:-1] + "[^1]" in markdown
    assert "## Stage[^2] and Review Background" in markdown
    assert "[^1]:" in markdown and "[^2]:" in markdown
    assert markdown.count("Related page-context") == 0
    assert markdown.count("[^1]") == 2
    assert markdown.count("[^2]") == 2


@pytest.mark.parametrize((
    "source_text", "source_bound", "body_page", "note_page", "note_count",
), [
    ("The literal edition number is 1.", True, 10, 10, 1),
    ("Two purported markers¹ remain ambiguous¹", True, 10, 10, 1),
    ("One marker¹ has duplicate notes.", True, 10, 10, 2),
    ("The source marks an omission * * *", True, 10, 10, 1),
    ("The marker is on another page¹", True, 10, 11, 1),
    ("The source lineage is unavailable¹", False, 10, 10, 1),
])
def test_markdown_exact_callouts_fail_closed_on_adversarial_evidence(
        source_text, source_bound, body_page, note_page, note_count):
    body = _markdown_record(
        source_text, page=body_page, stable_id="chunk_aaaaaaaaaaaaaaaa")
    if source_bound:
        _bind_exact_markdown_source(body, ref="#/texts/500")
    notes = []
    for index in range(note_count):
        label = "*" if "* * *" in source_text else "1."
        note = _markdown_record(
            f"{label} Adversarial supporting note {index + 1}.",
            content_type="footnote", page=note_page,
            stable_id=f"chunk_{index + 1:016x}")
        note["metadata"]["footnote_parent_stable_id"] = (
            "chunk_aaaaaaaaaaaaaaaa")
        notes.append(note)

    markdown = rag._assemble_markdown([body, *notes])

    assert source_text.replace("* * *", r"\* \* \*") in markdown
    assert "Related page-context note" in markdown
    assert markdown.count(
        "_Related page context; exact source marker unresolved._") == note_count


def test_markdown_lettered_note_uses_related_link_without_replacing_article():
    body = _markdown_record(
        "The rule gives a lawyer discretion.",
        stable_id="chunk_aaaaaaaaaaaaaaaa")
    footnote = _markdown_record(
        "a Casebook note.", content_type="footnote",
        stable_id="chunk_bbbbbbbbbbbbbbbb")
    footnote["metadata"]["footnote_parent_stable_id"] = (
        "chunk_aaaaaaaaaaaaaaaa")

    markdown = rag._assemble_markdown([body, footnote])

    assert "The rule gives a lawyer discretion." in markdown
    assert "The rule gives[^" not in markdown
    assert "Related page-context note[^1]" in markdown
    assert (
        "[^1]: _Related page context; exact source marker unresolved._ "
        "_(source footnote a)_ Casebook note."
        in markdown)


def test_markdown_definitions_follow_first_reference_order():
    body = _bind_exact_markdown_source(_markdown_record(
        "Second proposition.² First proposition.¹",
        stable_id="chunk_aaaaaaaaaaaaaaaa"), ref="#/texts/105")
    first = _markdown_record(
        "1. Alpha citation.", content_type="footnote",
        stable_id="chunk_1111111111111111")
    second = _markdown_record(
        "2. Beta citation.", content_type="footnote",
        stable_id="chunk_2222222222222222")

    markdown = rag._assemble_markdown([body, first, second])

    second_reference = "[^1]"
    first_reference = "[^2]"
    second_definition = f"{second_reference}: Beta citation."
    first_definition = f"{first_reference}: Alpha citation."
    body_end = markdown.index("## Endnotes")
    assert markdown.index(second_reference) < markdown.index(first_reference)
    assert markdown.index(second_definition, body_end) < markdown.index(
        first_definition, body_end)
    assert "1111111111111111" not in markdown
    assert "2222222222222222" not in markdown


def test_markdown_uses_one_final_endnote_section_across_chapters():
    chapter_one = [
        _bind_exact_markdown_source(_markdown_record(
            "First chapter.¹", chapter=1, page=10,
            stable_id="chunk_aaaaaaaaaaaaaaaa"), ref="#/texts/106"),
        _markdown_record(
            "1. First source.", content_type="footnote", chapter=1, page=10,
            stable_id="chunk_1111111111111111"),
    ]
    chapter_two = [
        _bind_exact_markdown_source(_markdown_record(
            "Second chapter.¹", chapter=2, page=20,
            stable_id="chunk_bbbbbbbbbbbbbbbb"), ref="#/texts/107"),
        _markdown_record(
            "1. Second source.", content_type="footnote", chapter=2, page=20,
            stable_id="chunk_2222222222222222"),
    ]

    markdown = rag._assemble_markdown(chapter_one + chapter_two)

    assert markdown.startswith("# Chapter 1\n")
    assert "\n---\n\n# Chapter 2\n" in markdown
    assert markdown.count("## Endnotes") == 1
    endnotes_start = markdown.index("## Endnotes")
    assert markdown.index("# Chapter 2") < endnotes_start
    assert markdown.index(
        "[^1]: First source.",
        endnotes_start) < markdown.index(
            "[^2]: Second source.",
            endnotes_start)
    assert "1111111111111111" not in markdown
    assert "2222222222222222" not in markdown


def test_markdown_indents_multiline_pandoc_footnote_continuations():
    body = _bind_exact_markdown_source(_markdown_record(
        "A proposition.¹", stable_id="chunk_aaaaaaaaaaaaaaaa"),
        ref="#/texts/108")
    footnote = _markdown_record(
        "1. First line.\nContinuation line.\n\nSecond paragraph.",
        content_type="footnote", stable_id="chunk_1111111111111111")

    markdown = rag._assemble_markdown([body, footnote])

    assert (
        "[^1]: First line.\n"
        "    Continuation line.\n"
        "    \n"
        "    Second paragraph."
        in markdown)


def test_markdown_does_not_guess_ambiguous_or_compound_footnote_markers():
    body = _markdown_record(
        "Two unrelated values are 49 and 49.",
        stable_id="chunk_aaaaaaaaaaaaaaaa")
    compound = _markdown_record(
        "49. Rounding guidance.\n1. Drop the digit.\n2. Round upward.",
        content_type="footnote", stable_id="chunk_3333333333333333")

    markdown = rag._assemble_markdown([body, compound])

    assert "## Endnotes" not in markdown
    assert markdown.count("## Unlinked source notes") == 1
    assert "[^" not in markdown
    assert "3333333333333333" not in markdown
    assert "**Unlinked source note 1.** 49. Rounding guidance." in markdown
    assert "49 and 49" in markdown
    assert "1. Drop the digit" in markdown
    assert "2. Round upward" in markdown


def test_markdown_invalid_note_id_and_continuation_fail_closed():
    body = _markdown_record(
        "The citation continues on the next line.",
        stable_id="chunk_aaaaaaaaaaaaaaaa")
    continuation = _markdown_record(
        "2025, at S4.", content_type="footnote", stable_id="unsafe-id")

    markdown = rag._assemble_markdown([body, continuation])

    assert "## Unlinked source notes" in markdown
    assert "## Endnotes" not in markdown
    assert "**Unlinked source note 1.** 2025, at S4." in markdown
    assert "[^" not in markdown
    assert "[^unsafe-id]" not in markdown


def test_markdown_ambiguous_note_links_only_to_verified_related_context():
    body = _markdown_record(
        "The page contains two ordinary values: 7 and 7.",
        stable_id="chunk_aaaaaaaaaaaaaaaa")
    continuation = _markdown_record(
        "continued authority without an opening marker.",
        content_type="footnote", stable_id="chunk_7777777777777777")
    continuation["metadata"]["footnote_parent_stable_id"] = (
        "chunk_aaaaaaaaaaaaaaaa")

    markdown = rag._assemble_markdown([body, continuation])

    assert "7 and 7" in markdown
    assert (
        "Related page-context note[^1]"
        in markdown)
    assert (
        "[^1]: "
        "_Related page context; exact source marker unresolved._ "
        "continued authority without an opening marker."
        in markdown)
    assert "7777777777777777" not in markdown
    assert "## Unlinked source notes" not in markdown
    assert '<a name="rag-fn' not in markdown
    assert "](#rag-fn" not in markdown


def test_markdown_pluralizes_multiple_related_page_context_notes():
    body = _markdown_record(
        "The parent passage has no exact source markers.",
        stable_id="chunk_aaaaaaaaaaaaaaaa")
    first = _markdown_record(
        "First continuation.", content_type="footnote",
        stable_id="chunk_1111111111111111")
    second = _markdown_record(
        "Second continuation.", content_type="footnote",
        stable_id="chunk_2222222222222222")
    for footnote in (first, second):
        footnote["metadata"]["footnote_parent_stable_id"] = (
            "chunk_aaaaaaaaaaaaaaaa")

    markdown = rag._assemble_markdown([body, first, second])

    assert (
        "Related page-context notes: "
        "[^1] [^2]"
        in markdown)
    assert markdown.count(
        "_Related page context; exact source marker unresolved._") == 2
    assert "1111111111111111" not in markdown
    assert "2222222222222222" not in markdown


def test_markdown_coalesces_lineaged_physical_footnote_continuations():
    first = _markdown_record(
        "1. The source note introduces this quotation:",
        content_type="footnote", stable_id="chunk_1111111111111111")
    continuation = _markdown_record(
        "Quoted material in a separate source block.",
        content_type="footnote", stable_id="chunk_2222222222222222")
    second = _markdown_record(
        "2. A distinct source note.",
        content_type="footnote", stable_id="chunk_3333333333333333")
    for record, ref in zip(
            (first, continuation, second),
            ("#/texts/10", "#/texts/11", "#/texts/12")):
        record["metadata"].update({
            "source_file": "book",
            "source_items": [{"ref": ref, "label": "footnote"}],
            "footnote_parent_stable_id": "chunk_aaaaaaaaaaaaaaaa",
        })

    logical = rag._logical_markdown_footnote_records(
        [first, continuation, second])

    assert len(logical) == 2
    assert logical[0]["text"] == (
        "1. The source note introduces this quotation:\n\n"
        "Quoted material in a separate source block.")
    assert [item["ref"] for item in logical[0]["metadata"]["source_items"]] == [
        "#/texts/10", "#/texts/11",
    ]
    assert logical[1]["text"] == "2. A distinct source note."


def test_markdown_keeps_adjacent_bare_lettered_notes_separate():
    first = _markdown_record(
        "a First author note.", content_type="footnote",
        stable_id="chunk_1111111111111111")
    second = _markdown_record(
        "b Second author note.", content_type="footnote",
        stable_id="chunk_2222222222222222")
    for record, ref in zip((first, second), ("#/texts/10", "#/texts/11")):
        record["metadata"].update({
            "source_file": "book",
            "source_items": [{"ref": ref, "label": "footnote"}],
            "footnote_parent_stable_id": "chunk_aaaaaaaaaaaaaaaa",
        })

    logical = rag._logical_markdown_footnote_records([first, second])

    assert [record["text"] for record in logical] == [
        "a First author note.", "b Second author note."]


def test_markdown_coalesces_shared_ref_even_with_internal_numbered_line():
    first = _markdown_record(
        "4. A long source note split by the tokenizer.",
        content_type="footnote", stable_id="chunk_1111111111111111")
    continuation = _markdown_record(
        "2. This is an internal numbered point, not a new source note.",
        content_type="footnote", stable_id="chunk_2222222222222222")
    for record in (first, continuation):
        record["metadata"].update({
            "source_file": "book",
            "source_items": [{"ref": "#/texts/20", "label": "footnote"}],
        })

    logical = rag._logical_markdown_footnote_records([first, continuation])

    assert len(logical) == 1
    assert logical[0]["text"].count("source note") == 2


def test_markdown_coalesces_cross_page_sentence_only_when_unambiguous():
    first = _markdown_record(
        "9. The sentence continues with the words",
        content_type="footnote", page=10,
        stable_id="chunk_1111111111111111")
    continuation = _markdown_record(
        "on the next source page.", content_type="footnote", page=11,
        stable_id="chunk_2222222222222222")
    independent = _markdown_record(
        "An unnumbered but independent block.", content_type="footnote",
        page=12, stable_id="chunk_3333333333333333")
    for index, record in enumerate((first, continuation, independent), 30):
        record["metadata"].update({
            "source_file": "book",
            "source_items": [{
                "ref": f"#/texts/{index}", "label": "footnote",
            }],
        })

    logical = rag._logical_markdown_footnote_records(
        [first, continuation, independent])

    assert len(logical) == 2
    assert "words on the next" in logical[0]["text"]
    assert logical[1]["text"] == "An unnumbered but independent block."


def test_markdown_relocates_same_baseline_delayed_footnote_marker():
    leading = _markdown_record(
        "sample configuration applies a",
        content_type="footnote", page=10,
        stable_id="chunk_1111111111111111")
    delayed = _markdown_record(
        "a deliberately broad threshold.", content_type="footnote", page=10,
        stable_id="chunk_2222222222222222")
    continuation = _markdown_record(
        "Additional supporting authority.", content_type="footnote", page=10,
        stable_id="chunk_3333333333333333")
    for record, ref, top, bottom in (
            (leading, "#/texts/10", 90.6, 83.5),
            (delayed, "#/texts/11", 90.4, 74.5),
            (continuation, "#/texts/12", 70.0, 60.0)):
        record["metadata"].update({
            "source_file": "book",
            "footnote_parent_stable_id": "chunk_aaaaaaaaaaaaaaaa",
            "source_items": [{
                "ref": ref, "label": "footnote",
                "spans": [{
                    "page": 10, "bbox": [90.0, top, 450.0, bottom],
                    "origin": "BOTTOMLEFT",
                }],
            }],
        })

    logical = rag._logical_markdown_footnote_records(
        [leading, delayed, continuation])

    assert len(logical) == 1
    assert logical[0]["text"] == (
        "a sample configuration applies a deliberately broad threshold."
        "\n\nAdditional supporting authority.")


def test_markdown_orders_shuffled_footnote_blocks_by_source_geometry():
    opener = _markdown_record(
        "1 The source note introduces a transcript:",
        content_type="footnote", page=10,
        stable_id="chunk_1111111111111111")
    question = _markdown_record(
        "'Q. What happened next?", content_type="footnote", page=10,
        stable_id="chunk_2222222222222222")
    answer = _markdown_record(
        "A. The witness answered.", content_type="footnote", page=10,
        stable_id="chunk_3333333333333333")
    follow_up = _markdown_record(
        "Q. Why?", content_type="footnote", page=10,
        stable_id="chunk_4444444444444444")
    for record, ref, top, bottom in (
            (opener, "#/texts/10", 246.0, 221.0),
            (question, "#/texts/11", 217.0, 201.0),
            (answer, "#/texts/12", 197.0, 172.0),
            (follow_up, "#/texts/13", 159.0, 152.0)):
        record["metadata"].update({
            "source_file": "book",
            "footnote_parent_stable_id": "chunk_aaaaaaaaaaaaaaaa",
            "source_items": [{
                "ref": ref, "label": "footnote",
                "spans": [{
                    "page": 10,
                    "bbox": [90.0, top, 450.0, bottom],
                    "origin": "BOTTOMLEFT",
                }],
            }],
        })

    logical = rag._logical_markdown_footnote_records(
        [answer, opener, follow_up, question])

    assert len(logical) == 1
    assert logical[0]["text"] == (
        "1 The source note introduces a transcript:\n\n"
        "'Q. What happened next?\n\n"
        "A. The witness answered.\n\n"
        "Q. Why?")


def test_markdown_marker_only_record_starts_new_cross_page_note():
    prior = _markdown_record(
        "4 Prior note.", content_type="footnote", page=10,
        stable_id="chunk_1111111111111111")
    marker = _markdown_record(
        "5", content_type="footnote", page=11,
        stable_id="chunk_2222222222222222")
    continuation = _markdown_record(
        "New note text.", content_type="footnote", page=11,
        stable_id="chunk_3333333333333333")
    for record, ref, page, top, bottom in (
            (prior, "#/texts/10", 10, 90.0, 74.0),
            (marker, "#/texts/11", 11, 120.0, 113.0),
            (continuation, "#/texts/12", 11, 109.0, 90.0)):
        record["metadata"].update({
            "source_file": "book",
            "footnote_parent_stable_id": "chunk_aaaaaaaaaaaaaaaa",
            "source_items": [{
                "ref": ref, "label": "footnote",
                "spans": [{
                    "page": page, "bbox": [90.0, top, 450.0, bottom],
                    "origin": "BOTTOMLEFT",
                }],
            }],
        })

    logical = rag._logical_markdown_footnote_records(
        [prior, marker, continuation])

    assert len(logical) == 2
    assert logical[1]["text"] == "5\n\nNew note text."


def test_markdown_keeps_triple_asterisk_omission_in_body_flow():
    omission = _markdown_record(
        "* * * Opinion text continues.", content_type="footnote",
        stable_id="chunk_1111111111111111")

    markdown = rag._assemble_markdown([omission])

    assert r"\* \* \* Opinion text continues." in markdown
    assert "## Endnotes" not in markdown
    assert "<details>" not in markdown


def test_markdown_places_unlinked_notes_before_final_endnotes_section():
    body = _bind_exact_markdown_source(_markdown_record(
        "A linked proposition.¹",
        stable_id="chunk_aaaaaaaaaaaaaaaa"), ref="#/texts/109")
    linked = _markdown_record(
        "1. Linked authority.", content_type="footnote",
        stable_id="chunk_1111111111111111")
    unlinked = _markdown_record(
        "49. Compound source note.\n1. First item.\n2. Second item.",
        content_type="footnote", stable_id="chunk_2222222222222222")

    markdown = rag._assemble_markdown([body, linked, unlinked])

    assert markdown.count("## Unlinked source notes") == 1
    assert markdown.count("## Endnotes") == 1
    assert markdown.index("## Unlinked source notes") < markdown.index(
        "## Endnotes")
    assert "**Unlinked source note 1.** 49. Compound source note." in markdown
    assert "[^1]: Linked authority." in markdown
    assert "1111111111111111" not in markdown
    assert "2222222222222222" not in markdown


def test_markdown_footnote_filter_produces_no_dangling_links():
    records = [
        _markdown_record(
            "A proposition. 1", stable_id="chunk_aaaaaaaaaaaaaaaa"),
        _markdown_record(
            "1. Source.", content_type="footnote",
            stable_id="chunk_1111111111111111"),
    ]
    filtered = rag._filter_chunk_records(
        records, exclude_types=["footnote"])

    markdown = rag._assemble_markdown(filtered)

    assert "A proposition. 1" in markdown
    assert "## Endnotes" not in markdown
    assert "rag-fn" not in markdown


def test_markdown_export_parameters_bind_markdown_policy_versions():
    parameters = rag._markdown_export_parameters(
        include_types=None, exclude_types=None, chapters=None,
        split_chapters=False)

    assert parameters["endnote_serialization_version"] == 10
    assert parameters["footnote_dialect"] == "pandoc"
    assert parameters["currency_escape_version"] == 3
    assert parameters["source_literal_escape_version"] == 5
    assert parameters["table_cell_padding_version"] == 2
    assert parameters["emphasis_marker"] == "_"
    assert parameters["thematic_break_normalization_version"] == 2
    assert parameters["implicit_reference_escape_version"] == 2
    assert parameters["ordered_list_literal_escape_version"] == 1
    assert parameters["split_matter_partition_version"] == 1
    assert parameters["heading_hierarchy_version"] == 2
    assert parameters["case_heading_version"] == 2
    assert parameters["content_type_rendering_version"] == 2
    assert parameters["markdown_validation_policy_version"] == 5
    assert parameters["markdown_validation"] == "auto"


@pytest.mark.parametrize(("markdown", "error"), [
    (
        "Body[^1]",
        "dangling serialized footnote references",
    ),
    (
        "## Endnotes\n\n[^1]: Orphan.",
        "orphaned serialized footnote definitions",
    ),
    (
        "Body[^1] and again[^1].\n\n"
        "[^1]: Repeated reference.",
        "duplicate serialized footnote references",
    ),
    (
        "Body[^1].\n\n"
        "[^1]: First definition.\n\n"
        "[^1]: Second definition.",
        "duplicate serialized footnote definitions",
    ),
    (
        "Body[^2].\n\n[^2]: Nonsequential label.",
        "serialized footnote labels are not in first-reference order",
    ),
])
def test_markdown_footnote_validator_rejects_invalid_native_links(
        markdown, error):
    with pytest.raises(ValueError, match=error):
        rag._validate_markdown_endnote_links(markdown)


def test_normalization_preserves_legal_headings_and_roman_numerals():
    text = "PERSONAL JURISDICTION\nIV.\nThis is substantive text."

    normalized = rag._normalize_text(text)

    assert "PERSONAL JURISDICTION" in normalized
    assert "IV." in normalized


def test_page_estimate_maps_first_and_last_chunks_to_document_endpoints():
    assert rag.estimate_page_range(0, total_chunks=10, total_pages=100) == "~p.1"
    assert rag.estimate_page_range(9, total_chunks=10, total_pages=100) == "~p.100"


def test_dedup_preserves_low_signal_chunks_consistently():
    chunks = [
        {"text": "", "metadata": {"chunk_index": 0}},
        {"text": "x", "metadata": {"chunk_index": 1}},
        {"text": "", "metadata": {"chunk_index": 2}},
    ]

    assert rag._deduplicate_chunks(chunks) == chunks
