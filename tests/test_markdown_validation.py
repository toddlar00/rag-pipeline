import json
from types import SimpleNamespace

import pytest

import markdown_validation as validation


VALID_MARKDOWN = """# Title

<!-- TABLE -->

Fee schedule

| Item | Amount |
| --- | ---: |
| Filing | \\$100 |

Text with a note.[^1]

## Endnotes

[^1]: Supporting authority.
"""


def test_internal_policy_validates_structure_and_combines_receipts():
    receipt = validation.validate_markdown_candidate(
        VALID_MARKDOWN, expected_table_count=1, policy="internal")

    assert receipt["publishable"] is True
    assert receipt["internal"]["table_count"] == 1
    assert receipt["internal"]["note_count"] == 1
    assert receipt["pandoc"]["status"] == "skipped_by_policy"
    combined = validation.combine_validation_receipts(
        [receipt, receipt], policy="internal")
    assert combined["candidate_count"] == 2
    assert combined["internal"]["table_count"] == 2
    assert validation.validation_receipt_is_complete(
        combined, policy="internal", candidate_count=2)


@pytest.mark.parametrize(("markdown", "message"), [
    ("# Title\n\n\nText\n", "consecutive blank"),
    ("# Title\n\nThe fee is $100.\n", "inline math"),
    ("# A\n\nQuoted insertion [a].\n", "implicit reference"),
    ("# Title\n\n```\nunclosed\n", "unterminated fenced"),
    ("---\n\n# Title\n", "YAML front matter"),
    ('# Title\n\n<a name="rag-fn-deadbeef"></a>\n', "legacy"),
    ("# Title\n\n##### None\n", "heading contains None"),
    ("# Title\n\n[Source insertion]: literal text\n", "hide source prose"),
    ("# Title\n\n<!-- TABLE -->\n\nnot a table\n", "not followed"),
])
def test_internal_gate_rejects_known_editor_regressions(markdown, message):
    expected_tables = 1 if _table_marker(markdown) else 0
    with pytest.raises(validation.MarkdownValidationError, match=message):
        validation.validate_markdown_candidate(
            markdown, expected_table_count=expected_tables,
            policy="internal")


@pytest.mark.parametrize(("indent", "label"), (
    ("", "1."),
    ("  ", "A."),
    ("    ", "a."),
    ("      ", "iv."),
))
def test_internal_gate_rejects_bullet_wrapped_ordered_labels(indent, label):
    with pytest.raises(
            validation.MarkdownValidationError, match="ordered-list label"):
        validation.validate_markdown_candidate(
            f"# Contents\n\n{indent}- {label} Entry\n",
            expected_table_count=0, policy="internal")


def test_internal_gate_accepts_pandoc_fancy_alphabetic_list():
    receipt = validation.validate_markdown_candidate(
        "# Problems\n\na. Alpha\n\nb. Beta\n\nc. Gamma\n",
        expected_table_count=0, policy="internal")

    assert receipt["internal"]["status"] == "pass"


def test_internal_gate_accepts_escaped_ordered_labels_in_generic_bullets():
    receipt = validation.validate_markdown_candidate(
        "# Labels\n\n"
        "- 1\\. Numeric label\n\n"
        "  - A\\. Upper label\n\n"
        "    - a\\. Lower label\n\n"
        "      - iv\\. Roman label\n",
        expected_table_count=0, policy="internal")

    assert receipt["internal"]["status"] == "pass"


def test_internal_gate_accepts_ordinary_bullets_and_fenced_examples():
    receipt = validation.validate_markdown_candidate(
        "# Labels\n\n"
        "- Ordinary bullet text\n\n"
        "  - §1.01 Section label\n\n"
        "    ```markdown\n"
        "    - 1. Literal example inside a fence\n"
        "    ```\n",
        expected_table_count=0, policy="internal")

    assert receipt["internal"]["status"] == "pass"


def test_internal_gate_allows_reference_definition_example_in_fence():
    receipt = validation.validate_markdown_candidate(
        "# Example\n\n```markdown\n[reference]: target.md\n```\n",
        expected_table_count=0, policy="internal")

    assert receipt["internal"]["status"] == "pass"


def _table_marker(markdown):
    return "<!-- TABLE -->" in markdown


def test_internal_gate_rejects_table_count_and_padding_mismatches():
    with pytest.raises(validation.MarkdownValidationError, match="source tables"):
        validation.validate_markdown_candidate(
            VALID_MARKDOWN, expected_table_count=2, policy="internal")

    compact = VALID_MARKDOWN.replace("| --- | ---: |", "|---|---:|")
    with pytest.raises(validation.MarkdownValidationError, match="padded"):
        validation.validate_markdown_candidate(
            compact, expected_table_count=1, policy="internal")

    attached_caption = VALID_MARKDOWN.replace(
        "Fee schedule\n\n| Item", "Fee schedule\n| Item")
    with pytest.raises(validation.MarkdownValidationError, match="caption"):
        validation.validate_markdown_candidate(
            attached_caption, expected_table_count=1, policy="internal")


def test_materialized_markdown_validates_tables_without_hidden_markers():
    materialized = VALID_MARKDOWN.replace("<!-- TABLE -->\n\n", "")

    receipt = validation.validate_markdown_candidate(
        materialized, expected_table_count=1, policy="internal",
        require_table_markers=False)

    assert receipt["internal"]["table_count"] == 1
    with pytest.raises(validation.MarkdownValidationError, match="source tables"):
        validation.validate_markdown_candidate(
            materialized, expected_table_count=2, policy="internal",
            require_table_markers=False)


def test_auto_records_missing_external_tools_without_blocking(monkeypatch, caplog):
    monkeypatch.setattr(
        validation, "_probe_pandoc",
        lambda: validation._ToolProbe("unavailable"))
    monkeypatch.setattr(
        validation, "_probe_zettlr_validator",
        lambda: validation._ToolProbe("unavailable"))

    receipt = validation.validate_markdown_candidate(
        VALID_MARKDOWN, expected_table_count=1, policy="auto")

    assert receipt["pandoc"]["status"] == "skipped_unavailable"
    assert receipt["zettlr"]["status"] == "skipped_unavailable"
    assert "mandatory internal checks still ran" in caplog.text


def test_strict_requires_both_pinned_external_validators(monkeypatch):
    monkeypatch.setattr(
        validation, "_probe_pandoc",
        lambda: validation._ToolProbe("unavailable"))
    monkeypatch.setattr(
        validation, "_probe_zettlr_validator",
        lambda: validation._ToolProbe("unavailable"))

    with pytest.raises(validation.MarkdownValidationError, match="requires pinned"):
        validation.validate_markdown_candidate(
            VALID_MARKDOWN, expected_table_count=1, policy="strict")


def test_auto_blocks_detected_external_semantic_mismatch(monkeypatch):
    available = validation._ToolProbe(
        "available", ("validator",), version=validation.PANDOC_VERSION)
    monkeypatch.setattr(validation, "_probe_pandoc", lambda: available)
    monkeypatch.setattr(
        validation, "_probe_zettlr_validator",
        lambda: validation._ToolProbe("unavailable"))

    def mismatch(*_args, **_kwargs):
        raise validation.MarkdownValidationError("Pandoc table mismatch")

    monkeypatch.setattr(validation, "_validate_pandoc", mismatch)

    with pytest.raises(validation.MarkdownValidationError, match="table mismatch"):
        validation.validate_markdown_candidate(
            VALID_MARKDOWN, expected_table_count=1, policy="auto")


def test_auto_allows_zettlr_style_warnings_but_strict_blocks(monkeypatch):
    available_pandoc = validation._ToolProbe(
        "available", ("pandoc",), version=validation.PANDOC_VERSION)
    available_zettlr = validation._ToolProbe(
        "available", ("node", "validate.mjs"),
        version=validation.ZETTLR_PROFILE)
    monkeypatch.setattr(validation, "_probe_pandoc", lambda: available_pandoc)
    monkeypatch.setattr(
        validation, "_probe_zettlr_validator", lambda: available_zettlr)
    monkeypatch.setattr(validation, "_validate_pandoc", lambda *_a, **_k: {
        "status": "pass", "profile": validation.PANDOC_PROFILE,
        "diagnostic_count": 0,
    })

    def zettlr_result(*_args, strict, **_kwargs):
        if strict:
            raise validation.MarkdownValidationError("style diagnostics")
        return {
            "status": "warnings",
            "profile": validation.ZETTLR_PROFILE,
            "diagnostic_count": 1,
            "semantic_count": 0,
            "style_count": 1,
            "rules": {"emphasis-marker": 1},
        }

    monkeypatch.setattr(validation, "_validate_zettlr", zettlr_result)

    receipt = validation.validate_markdown_candidate(
        VALID_MARKDOWN, expected_table_count=1, policy="auto")
    assert receipt["zettlr"]["status"] == "warnings"
    with pytest.raises(validation.MarkdownValidationError, match="style"):
        validation.validate_markdown_candidate(
            VALID_MARKDOWN, expected_table_count=1, policy="strict")


def test_pandoc_ast_count_mismatch_blocks_even_after_successful_parse(
        monkeypatch):
    ast = {
        "pandoc-api-version": [1, 23, 1],
        "meta": {},
        "blocks": [
            {"t": "RawBlock", "c": ["html", "<!-- TABLE -->"]},
            {"t": "Table", "c": []},
            {"t": "Para", "c": [{"t": "Note", "c": []}]},
        ],
    }
    monkeypatch.setattr(
        validation, "_pandoc_parse", lambda *_a, **_k: (ast, b""))
    probe = validation._ToolProbe(
        "available", ("pandoc",), version=validation.PANDOC_VERSION)

    passed = validation._validate_pandoc(
        VALID_MARKDOWN, expected_table_count=1, expected_note_count=1,
        probe=probe, strict=False)
    assert passed["status"] == "pass"

    with pytest.raises(validation.MarkdownValidationError, match="note count"):
        validation._validate_pandoc(
            VALID_MARKDOWN, expected_table_count=1, expected_note_count=2,
            probe=probe, strict=False)


def test_pandoc_differential_rejects_only_implicit_fragment_links(monkeypatch):
    link = {
        "t": "Link",
        "c": [["", [], []], [{"t": "Str", "c": "a"}], ["#a", ""]],
    }
    primary = {"blocks": [{"t": "Para", "c": [link]}]}
    control = {"blocks": []}
    probe = validation._ToolProbe(
        "available", ("pandoc",), version=validation.PANDOC_VERSION)

    monkeypatch.setattr(
        validation, "_pandoc_parse",
        lambda _command, _markdown, reader: (
            primary if reader == "markdown" else control, b""))
    with pytest.raises(validation.MarkdownValidationError, match="implicit"):
        validation._validate_pandoc(
            "# A\n\n[a]\n", expected_table_count=0,
            expected_note_count=0, probe=probe, strict=False)

    monkeypatch.setattr(
        validation, "_pandoc_parse",
        lambda *_args, **_kwargs: (primary, b""))
    receipt = validation._validate_pandoc(
        "# A\n\n[a](#a)\n", expected_table_count=0,
        expected_note_count=0, probe=probe, strict=False)
    assert receipt["status"] == "pass"


def test_external_runner_uses_fixed_argv_stdin_timeout_and_no_shell(monkeypatch):
    observed = {}

    def run(command, **kwargs):
        observed["command"] = command
        observed.update(kwargs)
        return SimpleNamespace(returncode=0, stdout=b"{}", stderr=b"")

    monkeypatch.setattr(validation.subprocess, "run", run)
    result = validation._run_command(("tool", "--fixed"), b"private input")

    assert result.returncode == 0
    assert observed["command"] == ["tool", "--fixed"]
    assert observed["input"] == b"private input"
    assert observed["shell"] is False
    assert observed["timeout"] == 30


def test_receipt_rejects_wrong_policy_count_or_missing_attestation():
    receipt = validation.validate_markdown_candidate(
        VALID_MARKDOWN, expected_table_count=1, policy="internal")

    assert not validation.validation_receipt_is_complete(
        receipt, policy="auto", candidate_count=1)
    assert not validation.validation_receipt_is_complete(
        receipt, policy="internal", candidate_count=2)
    missing = json.loads(json.dumps(receipt))
    del missing["internal"]
    assert not validation.validation_receipt_is_complete(
        missing, policy="internal", candidate_count=1)


def _valid_strict_receipt():
    return {
        "schema_version": (
            validation.MARKDOWN_VALIDATION_RECEIPT_SCHEMA_VERSION),
        "policy_version": validation.MARKDOWN_VALIDATION_POLICY_VERSION,
        "policy": "strict",
        "candidate_count": 1,
        "publishable": True,
        "internal": {
            "status": "pass", "table_count": 1, "note_count": 1,
            "diagnostic_count": 0,
        },
        "pandoc": {
            "status": "pass", "profile": validation.PANDOC_PROFILE,
            "version": validation.PANDOC_VERSION, "table_count": 1,
            "note_count": 1, "warning_count": 0,
        },
        "zettlr": {
            "status": "pass", "profile": validation.ZETTLR_PROFILE,
            "version": validation.ZETTLR_PROFILE,
            "config_source": "export-default", "italic_formatting": "_",
            "bold_formatting": "**", "diagnostic_count": 0,
            "semantic_count": 0, "style_count": 0, "rules": {},
        },
    }


def test_strict_receipt_requires_exact_pinned_attestation_schema():
    receipt = _valid_strict_receipt()
    assert validation.validation_receipt_is_complete(
        receipt, policy="strict", candidate_count=1)

    minimal_forgery = {
        "schema_version": validation.MARKDOWN_VALIDATION_RECEIPT_SCHEMA_VERSION,
        "policy_version": validation.MARKDOWN_VALIDATION_POLICY_VERSION,
        "policy": "strict", "candidate_count": 1,
        "publishable": True, "internal": {"status": "pass"},
        "pandoc": {"status": "pass"}, "zettlr": {"status": "pass"},
    }
    assert not validation.validation_receipt_is_complete(
        minimal_forgery, policy="strict", candidate_count=1)

    for mutation in (
        lambda value: value.update(schema_version=True),
        lambda value: value.update(policy_version=True),
        lambda value: value.update(candidate_count=True),
        lambda value: value["internal"].update(table_count=True),
        lambda value: value["pandoc"].update(profile="untrusted"),
        lambda value: value["zettlr"].pop("config_source"),
        lambda value: value.update(unexpected="field"),
    ):
        forged = json.loads(json.dumps(receipt))
        mutation(forged)
        assert not validation.validation_receipt_is_complete(
            forged, policy="strict", candidate_count=1)
