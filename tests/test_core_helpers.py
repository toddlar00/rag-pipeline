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
