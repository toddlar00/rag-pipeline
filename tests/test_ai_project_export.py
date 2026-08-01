import hashlib
from copy import deepcopy

import pytest

import ai_project_export as project_export
import markdown_validation


def _chapter(number: int, *, note: bool = False) -> project_export.SourceDocument:
    footnote = (
        "\nA proposition.[^1]\n\n---\n\n## Endnotes\n\n"
        "[^1]: Supporting authority.\n"
        if note else "\nBody text.\n"
    )
    return project_export.SourceDocument(
        f"ch{number:02d}_Chapter_{number}.md",
        f"# Chapter {number}\n\n[PDF page {number}]\n{footnote}",
    )


def test_target_profiles_choose_markdown_or_normalized_text():
    assert project_export.target_profile("notebooklm").extension == ".md"
    assert project_export.target_profile("chatgpt").extension == ".md"
    assert project_export.target_profile("claude").extension == ".txt"


def test_normalization_materializes_comments_and_externalizes_no_local_links():
    source = project_export.SourceDocument(
        "ch01_One.md",
        """# Chapter 1

[PDF page 12]

<!-- NOTES AND QUESTIONS -->

**Notes and Questions** (p. 2)

Questions.

<!-- CASE OPINION -->

Opinion.

<!-- TABLE -->

| A |
| --- |
| B |

[Chapter 2](ch02_Two.md)

![Source figure from PDF page 12](../assets/figure-p0012-1.png)
""",
    )
    second = project_export.SourceDocument(
        "ch02_Two.md", "# Chapter 2\n\n[PDF page 20]\n\nSecond.\n")

    outputs, _ = project_export.build_project_documents(
        [second, source], target="notebooklm")

    text = outputs[0].text
    assert outputs[0].name == "ch01_One.md"
    assert "##### Notes and Questions (p. 2)" in text
    assert text.count("Notes and Questions") == 1
    assert "_Case opinion._" in text
    assert "<!--" not in text
    assert "(ch02_Two.md)" not in text
    assert "../assets" not in text
    assert "Source figure from PDF page 12" in text
    assert "| A |" in text
    assert "[PDF page 12]" in text


def test_unknown_hidden_comment_fails_closed():
    with pytest.raises(
            project_export.AIProjectExportError,
            match="unknown hidden Markdown comment"):
        project_export.build_project_documents([
            project_export.SourceDocument(
                "ch01_One.md", "# One\n\n<!-- UNKNOWN -->\n\nBody.\n")
        ], target="chatgpt")


def test_textless_source_figure_sentinel_is_removed():
    outputs, _ = project_export.build_project_documents([
        project_export.SourceDocument(
            "ch01_One.md",
            "# One\n\n[PDF page 1]\n\nBefore.\n\n<!-- -->\n\nAfter.\n",
        ),
    ], target="notebooklm")

    assert outputs[0].text == (
        "# One\n\n[PDF page 1]\n\nBefore.\n\nAfter.\n")
    assert "<!--" not in outputs[0].text


@pytest.mark.parametrize("local_markup", [
    "[Next][next]\n\n[next]: ch02_Two.md",
    "[Next][next]\n\n[next]:\n  ch02_Two.md",
    '<a href="ch02_Two.md">Next</a>',
    '<img src="../assets/other.png" alt="Other">',
    '<img srcset="../assets/other.png 2x" alt="Other">',
    '<img srcset="https://example.com/a.png 1x, ../assets/other.png 2x" '
    'alt="Other">',
    "<ch02_Two.md>",
    "<appendix.pdf>",
    "<../assets/figure.jpg#detail>",
    r"<C:\books\appendix.pdf>",
    "[Local](file:///tmp/ch02_Two.md)",
    "[Nested](folder(name).md)",
    "![Other](../assets/other.png)",
])
def test_noncanonical_local_link_forms_fail_closed(local_markup):
    with pytest.raises(
            project_export.AIProjectExportError,
            match="package-external local link"):
        project_export.build_project_documents([
            project_export.SourceDocument(
                "ch01_One.md",
                f"# One\n\n[PDF page 1]\n\n{local_markup}\n",
            ),
            project_export.SourceDocument(
                "ch02_Two.md", "# Two\n\n[PDF page 2]\n\nBody.\n"),
        ], target="chatgpt")


def test_multiline_reference_and_srcset_fixtures_are_zettlr_valid():
    probe = markdown_validation._probe_zettlr_validator()
    if probe.status != "available":
        pytest.skip("bundled Zettlr-compatible validator is unavailable")
    fixtures = (
        "# One\n\n[PDF page 1]\n\n"
        "[Next][next]\n\n[next]:\n  ch02_Two.md\n",
        "# One\n\n[PDF page 1]\n\n"
        '<img srcset="../assets/other.png 2x" alt="Other">\n',
    )
    for markdown in fixtures:
        receipt = markdown_validation._validate_zettlr(
            markdown, source_name="compatibility-fixture.md",
            probe=probe, strict=True)
        assert receipt["status"] == "pass"


def test_canonical_link_fragments_and_titles_are_materialized():
    outputs, _ = project_export.build_project_documents([
        project_export.SourceDocument(
            "ch01_One.md",
            "# One\n\n[PDF page 1]\n\n"
            "[Fragment](ch02_Two.md#part) and "
            '[Title](ch02_Two.md "Chapter two").\n',
        ),
        project_export.SourceDocument(
            "ch02_Two.md", "# Two\n\n[PDF page 2]\n\nBody.\n"),
    ], target="chatgpt")

    assert "Fragment and Title." in outputs[0].text
    assert "ch02_Two.md" not in outputs[0].text


@pytest.mark.parametrize("external_markup", [
    "<https://example.com/appendix.pdf>",
    "<reader@example.com>",
    "<section>",
    '<a href="https://example.com/appendix">Appendix</a>',
])
def test_external_autolinks_and_ordinary_html_tags_are_not_local(
        external_markup):
    outputs, _ = project_export.build_project_documents([
        project_export.SourceDocument(
            "ch01_One.md",
            f"# One\n\n[PDF page 1]\n\n{external_markup}\n",
        ),
    ], target="chatgpt")

    assert external_markup in outputs[0].text


def test_every_canonical_source_requires_its_own_page_marker():
    with pytest.raises(
            project_export.AIProjectExportError,
            match="ch02_Two.md"):
        project_export.build_project_documents([
            project_export.SourceDocument(
                "ch01_One.md", "# One\n\n[PDF page 1]\n\nBody.\n"),
            project_export.SourceDocument(
                "ch02_Two.md", "# Two\n\nBody without a page marker.\n"),
        ], target="notebooklm")


@pytest.mark.parametrize("hidden_marker", [
    "```text\n[PDF page 1]\n```",
    "<pre>\n[PDF page 1]\n</pre>",
])
def test_page_marker_in_code_or_raw_html_does_not_satisfy_source_proof(
        hidden_marker):
    with pytest.raises(
            project_export.AIProjectExportError,
            match="lacks visible PDF page markers"):
        project_export.build_project_documents([
            project_export.SourceDocument(
                "ch01_One.md", f"# One\n\n{hidden_marker}\n\nBody.\n"),
        ], target="notebooklm")


@pytest.mark.parametrize("endnote", [
    "Body.[^note]\n\n[^note]: Authority.",
    "Body.[^0]\n\n[^0]: Authority.",
    "Body.[^]\n\n[^]: Authority.",
    "Body.[^1\n\n[^1]: Authority.",
])
def test_nonnumeric_or_malformed_endnotes_fail_closed(endnote):
    with pytest.raises(
            project_export.AIProjectExportError,
            match="serialized endnote"):
        project_export.build_project_documents([
            project_export.SourceDocument(
                "ch01_One.md",
                f"# One\n\n[PDF page 1]\n\n{endnote}\n"),
        ], target="chatgpt")


def test_normalization_removes_bom_and_uses_exact_lf_termination():
    outputs, _ = project_export.build_project_documents([
        project_export.SourceDocument(
            "ch01_One.md",
            "\ufeff# One\r\n\r\n[PDF page 1]\r\n\r\nBody.\r\n\r\n",
        )
    ], target="claude")

    text = outputs[0].text
    assert not text.startswith("\ufeff")
    assert "\r" not in text
    assert text.endswith("\n") and not text.endswith("\n\n")
    assert not text.encode("utf-8").startswith(b"\xef\xbb\xbf")


def test_grouping_is_ordered_exhaustive_and_relabels_endnotes():
    sources = [_chapter(index, note=True) for index in range(1, 5)]

    outputs, parameters = project_export.build_project_documents(
        list(reversed(sources)), target="chatgpt", max_files=2)

    assert parameters["max_files"] == 2
    assert len(outputs) == 2
    assert [source for output in outputs for source in output.sources] == [
        source.name for source in sources]
    assert len({source for output in outputs for source in output.sources}) == 4
    for output in outputs:
        assert output.name.endswith(".md")
        assert output.text.count("[^1]") == 2
        assert output.text.count("[^2]") == 2
        assert "[^3]" not in output.text


def test_claude_changes_only_extension_for_ungrouped_normalized_document():
    source = _chapter(1, note=True)
    markdown, _ = project_export.build_project_documents(
        [source], target="notebooklm")
    text, _ = project_export.build_project_documents(
        [source], target="claude")

    assert markdown[0].name.endswith(".md")
    assert text[0].name.endswith(".txt")
    assert text[0].text == markdown[0].text


def test_strict_export_receipt_round_trip_and_overlap_rejection():
    documents, parameters = project_export.build_project_documents(
        [_chapter(1)], target="notebooklm")
    raw = documents[0].text.encode("utf-8")
    output_records = [{
        "role": documents[0].name,
        "name": documents[0].name,
        "size": len(raw),
        "sha256": hashlib.sha256(raw).hexdigest(),
    }]
    receipt = project_export.build_export_receipt(
        target="notebooklm",
        publication_receipt_sha256="a" * 64,
        publication_evidence_root_sha256="b" * 64,
        canonical_split_manifest_sha256="c" * 64,
        canonical_chunks_sha256="d" * 64,
        source_record_count=1,
        parameters=parameters,
        documents=documents,
        output_records=output_records,
    )

    serialized = project_export.serialize_export_receipt(receipt)
    assert project_export.parse_export_receipt_bytes(serialized) == receipt
    assert serialized == project_export.serialize_export_receipt(receipt)

    receipt["groups"].append(dict(receipt["groups"][0]))
    receipt["outputs"].append(dict(receipt["outputs"][0]))
    receipt["content_validation"]["document_count"] = 2
    receipt["content_validation"]["source_document_count"] = 2
    with pytest.raises(
            project_export.AIProjectExportError, match="overlap"):
        project_export.validate_export_receipt(receipt)


def test_strict_receipt_rejects_reordered_groups_and_missing_page_proof():
    documents, parameters = project_export.build_project_documents(
        [_chapter(1), _chapter(2)], target="chatgpt", max_files=1)
    raw = documents[0].text.encode("utf-8")
    receipt = project_export.build_export_receipt(
        target="chatgpt",
        publication_receipt_sha256="a" * 64,
        publication_evidence_root_sha256="b" * 64,
        canonical_split_manifest_sha256="c" * 64,
        canonical_chunks_sha256="d" * 64,
        source_record_count=2,
        parameters=parameters,
        documents=documents,
        output_records=[{
            "role": documents[0].name,
            "name": documents[0].name,
            "size": len(raw),
            "sha256": hashlib.sha256(raw).hexdigest(),
        }],
    )

    reordered = deepcopy(receipt)
    reordered["groups"][0]["sources"].reverse()
    with pytest.raises(
            project_export.AIProjectExportError,
            match="canonical source order"):
        project_export.validate_export_receipt(reordered)

    missing_page_proof = deepcopy(receipt)
    missing_page_proof["content_validation"]["page_marker_count"] = 0
    with pytest.raises(
            project_export.AIProjectExportError,
            match="content validation"):
        project_export.validate_export_receipt(missing_page_proof)

    missing_source_proof = deepcopy(receipt)
    missing_source_proof["source_page_markers"][1]["count"] = 0
    with pytest.raises(
            project_export.AIProjectExportError,
            match="page-marker proof"):
        project_export.validate_export_receipt(missing_source_proof)
