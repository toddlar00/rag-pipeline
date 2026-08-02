"""Focused tests for the hardened study-packet policy module."""

from __future__ import annotations

import json

import pytest

import llm_output_contracts
import markdown_validation
import study_packets


SOURCE_SHA = "a" * 64
SELECTION_SHA = "b" * 64


def _syllabus_payload(**overrides):
    payload = {
        "schema": "study-syllabus-v1",
        "course": "Legal Ethics",
        "structure_profile": "us-law-casebook-v1",
        "entries": [{
            "id": "week-03-conflicts",
            "title": "Conflicts of Interest",
            "chapters": ["#/texts/12"],
            "queries": ["current client conflicts"],
        }],
    }
    payload.update(overrides)
    return payload


def _metadata(**overrides):
    metadata = {
        "stable_id": "chunk_0000000000000001",
        "content_type": "author_narrative",
        "heading_path_ids": ["#/texts/12"],
        "source_file": "fixture.pdf",
        "page_range": "12-13",
        "section_path": "Chapter 3 > Conflicts",
    }
    metadata.update(overrides)
    return metadata


def _record(stable_id, text="Synthetic source text.", **overrides):
    return {
        "text": text,
        "metadata": _metadata(stable_id=stable_id, **overrides),
    }


def _item(stable_id, text="Synthetic source text.", **overrides):
    record = _record(stable_id, text, **overrides)
    return study_packets.SelectionItem(
        stable_id, record["text"], record["metadata"])


def _entry(**overrides):
    values = {
        "entry_id": "week-03-conflicts",
        "title": "Conflicts of Interest",
        "chapters": ("#/texts/12",),
        "queries": (),
    }
    values.update(overrides)
    return study_packets.SyllabusEntry(**values)


def _binding(**overrides):
    result = {
        "backend": "chroma",
        "collection": "ethics",
        "source_sha256": SOURCE_SHA,
        "embedding_model": "model-a",
        "snapshot_fingerprint_sha256": "d" * 64,
    }
    result.update(overrides)
    return result


def _header(**overrides):
    values = {
        "course": "Legal Ethics",
        "entry_id": "week-03-conflicts",
        "title": "Conflicts of Interest",
        "index_binding": _binding(),
        "selection_digest": SELECTION_SHA,
        "generated_at": "2026-08-01T12:00:00+00:00",
    }
    values.update(overrides)
    return study_packets.PacketHeader(**values)


def _digest_payload(case_name, stable_id):
    return {
        "case_name": case_name,
        "facts": {"text": "Relevant facts.", "citations": [stable_id]},
        "holding": {"text": "The court held for the client.",
                    "citations": [stable_id]},
        "significance": {"text": "Shows the governing limit.",
                         "citations": [stable_id]},
    }


def _ok_llm(prompt, contract, operation):
    if operation == "study_packet_case_digest":
        payload = json.loads(
            prompt.split(
                "SOURCE_JSON (one physical line; bounded case excerpts):\n",
            )[1].splitlines()[0])
        stable_id = payload["sources"][0]["stable_id"]
        return json.dumps(_digest_payload(payload["case_name"], stable_id))
    payload = json.loads(
        prompt.split(
            "SOURCE_JSON (one physical line; bounded topic sources):\n",
        )[1].splitlines()[0])
    return json.dumps({
        "outline": [{
            "text": "Identify the governing duty.",
            "citations": [payload["sources"][0]["stable_id"]],
        }],
    })


def test_parse_syllabus_accepts_exact_occurrence_selectors():
    syllabus = study_packets.parse_syllabus(_syllabus_payload())
    assert syllabus.course == "Legal Ethics"
    assert syllabus.structure_profile == "us-law-casebook-v1"
    assert syllabus.entries[0].chapters == ("#/texts/12",)


def test_parse_syllabus_accepts_chapter_only_and_query_only():
    chapter = _syllabus_payload(entries=[{
        "id": "chapter", "title": "Chapter", "chapters": ["#/texts/1"],
    }])
    query = _syllabus_payload(entries=[{
        "id": "query", "title": "Query", "queries": ["duty of candor"],
    }])
    assert study_packets.parse_syllabus(chapter).entries[0].queries == ()
    assert study_packets.parse_syllabus(query).entries[0].chapters == ()


@pytest.mark.parametrize(("mutation", "match"), [
    ({"schema": "study-syllabus-v2"}, "schema"),
    ({"course": ""}, "course"),
    ({"course": " padded"}, "normalized"),
    ({"course": "line\nbreak"}, "whitespace/control"),
    ({"course": "Cafe\u0301"}, "NFC"),
    ({"structure_profile": "Legal Profile"}, "canonical profile"),
    ({"entries": []}, "non-empty"),
    ({"entries": "bad"}, "non-empty"),
    ({"unexpected": True}, "unexpected"),
])
def test_parse_syllabus_rejects_invalid_top_level(mutation, match):
    with pytest.raises(study_packets.SyllabusError, match=match):
        study_packets.parse_syllabus(_syllabus_payload(**mutation))


@pytest.mark.parametrize(("entry", "match"), [
    ({"id": "x", "title": "X"}, "chapters or queries"),
    ({"id": "Bad Slug", "title": "X", "queries": ["q"]}, "id"),
    ({"id": "index", "title": "X", "queries": ["q"]}, "reserved"),
    ({"id": "con", "title": "X", "queries": ["q"]}, "reserved"),
    ({"id": "com9", "title": "X", "queries": ["q"]}, "reserved"),
    ({"id": "x", "title": "", "queries": ["q"]}, "title"),
    ({"id": "x", "title": "X", "chapters": ["#/texts/1\n"]},
     "whitespace/control"),
    ({"id": "x", "title": "X", "queries": ["same", "same"]},
     "duplicate queries"),
    ({"id": "x", "title": "X", "queries": ["q"], "extra": 1},
     "unexpected"),
])
def test_parse_syllabus_rejects_invalid_entries(entry, match):
    with pytest.raises(study_packets.SyllabusError, match=match):
        study_packets.parse_syllabus(_syllabus_payload(entries=[entry]))


def test_load_syllabus_text_rejects_duplicate_keys_and_nonfinite_numbers():
    duplicate = (
        '{"schema":"study-syllabus-v1","course":"A","course":"B",'
        '"structure_profile":"us-law-casebook-v1","entries":[]}'
    )
    with pytest.raises(study_packets.SyllabusError, match="duplicate key"):
        study_packets.load_syllabus_text(duplicate)
    nonfinite = json.dumps(_syllabus_payload()).replace(
        '"course": "Legal Ethics"', '"course": NaN')
    with pytest.raises(study_packets.SyllabusError, match="non-finite"):
        study_packets.load_syllabus_text(nonfinite)
    infinity = json.dumps(_syllabus_payload()).replace(
        '"course": "Legal Ethics"', '"course": Infinity')
    with pytest.raises(study_packets.SyllabusError, match="non-finite"):
        study_packets.load_syllabus_text(infinity)


def test_load_syllabus_text_and_path_enforce_byte_ceiling(tmp_path):
    oversized = " " * (study_packets.MAX_SYLLABUS_BYTES + 1)
    with pytest.raises(study_packets.SyllabusError, match="byte limit"):
        study_packets.load_syllabus_text(oversized)
    path = tmp_path / "syllabus.json"
    path.write_bytes(oversized.encode("ascii"))
    with pytest.raises(study_packets.SyllabusError, match="byte limit"):
        study_packets.load_syllabus_path(path)


def test_load_syllabus_text_rejects_excessive_nesting():
    text = "[" * (study_packets.MAX_SYLLABUS_JSON_DEPTH + 1)
    with pytest.raises(study_packets.SyllabusError, match="nesting"):
        study_packets.load_syllabus_text(text)


@pytest.mark.parametrize("entry_field", [False, True])
def test_syllabus_unknown_field_errors_do_not_emit_raw_controls(entry_field):
    payload = _syllabus_payload()
    if entry_field:
        payload["entries"][0]["evil\nfield"] = True
    else:
        payload["evil\nfield"] = True
    with pytest.raises(study_packets.SyllabusError) as caught:
        study_packets.parse_syllabus(payload)
    assert "evil\\nfield" in str(caught.value)
    assert "evil\nfield" not in str(caught.value)


def test_syllabus_unknown_field_diagnostic_is_bounded():
    payload = _syllabus_payload()
    payload["x" * (study_packets.MAX_SYLLABUS_BYTES // 2)] = True
    with pytest.raises(study_packets.SyllabusError) as caught:
        study_packets.parse_syllabus(payload)
    assert len(str(caught.value)) < 256


def test_title_like_selector_is_preserved_then_fails_exact_resolution():
    payload = _syllabus_payload(entries=[{
        "id": "week-03", "title": "Conflicts",
        "chapters": ["Chapter 3"],
    }])
    entry = study_packets.parse_syllabus(payload).entries[0]
    assert entry.chapters == ("Chapter 3",)
    with pytest.raises(study_packets.EntrySelectionError, match="exact heading"):
        study_packets.select_entry(
            entry, [_record("chunk_0000000000000001")],
            search_fn=lambda _query: [])


@pytest.mark.parametrize("entry_id", [
    "index", "con", "prn", "aux", "nul",
    *(f"com{number}" for number in range(1, 10)),
    *(f"lpt{number}" for number in range(1, 10)),
])
def test_all_reserved_output_and_windows_device_ids_are_rejected(entry_id):
    with pytest.raises(study_packets.SyllabusError, match="reserved"):
        study_packets.parse_syllabus(_syllabus_payload(entries=[{
            "id": entry_id, "title": "Reserved", "queries": ["query"],
        }]))


@pytest.mark.parametrize(("payload", "match"), [
    (_syllabus_payload(course="x" * (study_packets.MAX_COURSE_CHARACTERS + 1)),
     "course exceeds"),
    (_syllabus_payload(structure_profile=(
        "p" * (study_packets.MAX_PROFILE_CHARACTERS + 1))),
     "structure_profile exceeds"),
    (_syllabus_payload(entries=[{
        "id": "x", "title": "t" * (
            study_packets.MAX_ENTRY_TITLE_CHARACTERS + 1),
        "queries": ["q"],
    }]), "title exceeds"),
    (_syllabus_payload(entries=[{
        "id": "x", "title": "X",
        "chapters": ["x" * (
            study_packets.MAX_CHAPTER_SELECTOR_CHARACTERS + 1)],
    }]), r"chapters\[0\] exceeds"),
    (_syllabus_payload(entries=[{
        "id": "x", "title": "X",
        "queries": ["q" * (study_packets.MAX_QUERY_CHARACTERS + 1)],
    }]), r"queries\[0\] exceeds"),
])
def test_syllabus_rejects_overlong_scalars(payload, match):
    with pytest.raises(study_packets.SyllabusError, match=match):
        study_packets.parse_syllabus(payload)


def test_syllabus_rejects_over_limit_counts_and_accepts_boundaries():
    too_many_entries = [{
        "id": f"entry-{index}", "title": f"Entry {index}",
        "queries": ["query"],
    } for index in range(study_packets.MAX_SYLLABUS_ENTRIES + 1)]
    with pytest.raises(study_packets.SyllabusError, match="entry limit"):
        study_packets.parse_syllabus(
            _syllabus_payload(entries=too_many_entries))
    with pytest.raises(study_packets.SyllabusError, match="item limit"):
        study_packets.parse_syllabus(_syllabus_payload(entries=[{
            "id": "x", "title": "X",
            "chapters": [
                f"#/texts/{index}"
                for index in range(study_packets.MAX_CHAPTER_SELECTORS + 1)
            ],
        }]))
    with pytest.raises(study_packets.SyllabusError, match="item limit"):
        study_packets.parse_syllabus(_syllabus_payload(entries=[{
            "id": "x", "title": "X",
            "queries": [
                f"query {index}"
                for index in range(study_packets.MAX_QUERIES + 1)
            ],
        }]))
    boundary = study_packets.parse_syllabus(_syllabus_payload(
        course="c" * study_packets.MAX_COURSE_CHARACTERS,
        entries=[{
            "id": "x", "title": "t" * (
                study_packets.MAX_ENTRY_TITLE_CHARACTERS),
            "chapters": [
                f"#/texts/{index}"
                for index in range(study_packets.MAX_CHAPTER_SELECTORS)
            ],
            "queries": [
                f"query {index}"
                for index in range(study_packets.MAX_QUERIES)
            ],
        }]))
    assert len(boundary.entries[0].chapters) == (
        study_packets.MAX_CHAPTER_SELECTORS)
    assert len(boundary.entries[0].queries) == study_packets.MAX_QUERIES


def test_selection_item_defensively_freezes_metadata():
    metadata = _metadata(heading_path_ids=["#/texts/12"])
    item = study_packets.SelectionItem(
        "chunk_0000000000000001", "text", metadata)
    metadata["heading_path_ids"].append("#/texts/99")
    assert item.metadata["heading_path_ids"] == ("#/texts/12",)
    with pytest.raises(TypeError):
        item.metadata["new"] = "value"


def test_select_entry_uses_only_exact_heading_path_ids():
    records = [
        _record("chunk_0000000000000001", heading_path_ids=["#/texts/12"],
                chapter_title="Chapter 3", section_path="Chapter 3 > A"),
        _record("chunk_0000000000000002", heading_path_ids=["#/texts/120"],
                chapter_title="#/texts/12", section_path="#/texts/12 > B"),
    ]
    selection = study_packets.select_entry(
        _entry(), records, search_fn=lambda _query: [])
    assert [item.stable_id for item in selection.items] == [
        "chunk_0000000000000001"]
    with pytest.raises(study_packets.EntrySelectionError, match="exact heading"):
        study_packets.select_entry(
            _entry(chapters=("#/texts/9",)), records,
            search_fn=lambda _query: [])


def test_select_entry_rejects_missing_or_malformed_heading_identity():
    missing = _record("chunk_0000000000000001")
    missing["metadata"].pop("heading_path_ids")
    with pytest.raises(study_packets.EntrySelectionError, match="heading_path_ids"):
        study_packets.select_entry(
            _entry(chapters=(), queries=("q",)), [missing],
            search_fn=lambda _query: [])
    malformed = _record(
        "chunk_0000000000000001", heading_path_ids="#/texts/12")
    with pytest.raises(study_packets.EntrySelectionError, match="heading_path_ids"):
        study_packets.select_entry(
            _entry(), [malformed], search_fn=lambda _query: [])


@pytest.mark.parametrize("heading_ids", [
    ["Chapter 3"],
    ["#/texts/12", "#/texts/12"],
    ["#/texts/12\n#/texts/13"],
    ["#/groups/12"],
])
def test_select_entry_rejects_noncanonical_snapshot_heading_ids(heading_ids):
    record = _record(
        "chunk_0000000000000001", heading_path_ids=heading_ids)
    with pytest.raises(study_packets.EntrySelectionError, match="heading_path_ids"):
        study_packets.select_entry(
            _entry(chapters=(), queries=("query",)), [record],
            search_fn=lambda _query: [])


def test_select_entry_allows_an_empty_heading_path_for_query_only_sources():
    record = _record(
        "chunk_0000000000000001", heading_path_ids=[])
    selection = study_packets.select_entry(
        _entry(chapters=(), queries=("query",)), [record],
        search_fn=lambda _query: [_item("chunk_0000000000000001")])
    assert selection.items[0].stable_id == "chunk_0000000000000001"


def test_select_entry_maps_search_hits_to_trusted_snapshot():
    trusted = _record("chunk_0000000000000001", "Trusted snapshot text")
    forged = _item(
        "chunk_0000000000000001", "Forged vector payload",
        source_file="forged.pdf", page_range="999")
    selection = study_packets.select_entry(
        _entry(chapters=(), queries=("query",)), [trusted],
        search_fn=lambda _query: [forged])
    assert selection.items[0].text == "Trusted snapshot text"
    assert selection.items[0].metadata["source_file"] == "fixture.pdf"


def test_select_entry_collapses_table_child_to_canonical_parent():
    parent_id = "chunk_0000000000000001"
    child_id = "chunk_0000000000000002"
    records = [
        _record(parent_id, "| A |\n|---|\n| P |", content_type="table",
                retrieval_role="table_parent",
                table_parent_stable_id=parent_id),
        _record(child_id, "| A |\n|---|\n| C |", content_type="table",
                retrieval_role="table_child",
                table_parent_stable_id=parent_id),
    ]
    selection = study_packets.select_entry(
        _entry(chapters=(), queries=("row",)), records,
        search_fn=lambda _query: [_item(
            child_id, retrieval_role="table_child",
            table_parent_stable_id=parent_id)])
    assert [item.stable_id for item in selection.items] == [parent_id]
    assert "| P |" in selection.items[0].text


def test_select_entry_records_retrieval_warnings_truncation_and_unknown_ids():
    records = [_record("chunk_0000000000000001")]
    hits = tuple(
        _item(f"unknown_{index}")
        for index in range(study_packets.MAX_QUERY_HITS_PER_QUERY + 1)
    )
    hits += (_item("chunk_0000000000000001"),)
    result = study_packets.SearchResult(hits, ("hybrid mode degraded",))
    selection = study_packets.select_entry(
        _entry(chapters=(), queries=("query",)), records,
        search_fn=lambda _query: result)
    assert [item.stable_id for item in selection.items] == [
        "chunk_0000000000000001"]
    assert any("hybrid mode degraded" in notice for notice in selection.notices)
    assert any("unknown result ID" in notice for notice in selection.notices)


def test_select_entry_caps_only_trusted_unique_query_hits():
    records = [
        _record(f"chunk_{index:016d}")
        for index in range(study_packets.MAX_QUERY_HITS_PER_QUERY + 1)
    ]
    hits = tuple(_item(f"unknown_{index}") for index in range(3))
    hits += tuple(
        _item(f"chunk_{index:016d}")
        for index in range(study_packets.MAX_QUERY_HITS_PER_QUERY + 1)
    )
    selection = study_packets.select_entry(
        _entry(chapters=(), queries=("query",)), records,
        search_fn=lambda _query: hits)
    assert len(selection.items) == study_packets.MAX_QUERY_HITS_PER_QUERY
    assert any("trusted returned hits" in notice
               for notice in selection.notices)


def test_selection_digest_is_stable_and_order_sensitive():
    one = study_packets.EntrySelection((
        _item("chunk_0000000000000001"),
        _item("chunk_0000000000000002"),
    ), ())
    two = study_packets.EntrySelection(tuple(reversed(one.items)), ())
    assert study_packets.selection_digest(one) != study_packets.selection_digest(two)
    assert study_packets.selection_digest(one) == study_packets.selection_digest(
        study_packets.EntrySelection(one.items, ("notice",)))


def test_locator_line_requires_source_and_exact_pages():
    assert study_packets.locator_line(_metadata()) == (
        "fixture.pdf · pp. 12-13 · Chapter 3 > Conflicts")
    assert study_packets.locator_line(_metadata(
        page_range=None, page_start=4, page_end=5)) == (
        "fixture.pdf · pp. 4-5 · Chapter 3 > Conflicts")
    with pytest.raises(study_packets.CitationBindingError, match="page"):
        study_packets.locator_line(_metadata(page_range=None))
    with pytest.raises(study_packets.CitationBindingError, match="source_file"):
        study_packets.locator_line(_metadata(source_file=None))


@pytest.mark.parametrize("unsafe", [" ", " padded", "padded ", "a\u00a0b"])
def test_locator_line_rejects_blank_padded_or_unusual_whitespace(unsafe):
    with pytest.raises(study_packets.CitationBindingError):
        study_packets.locator_line(_metadata(source_file=unsafe))


def test_build_rules_section_is_cited_bounded_and_uses_canonical_tables():
    items = [
        _item("chunk_0000000000000001", "Rule *literal*.",
              content_type="statutory_excerpt"),
        _item("chunk_0000000000000002", "Narrative.",
              content_type="author_narrative"),
        _item("chunk_0000000000000003", "| A | B |\n|---|---|\n| 1 | 2 |",
              content_type="table", retrieval_role="table_parent",
              table_parent_stable_id="chunk_0000000000000003"),
    ]
    markdown, table_count, notices = study_packets.build_rules_section(
        study_packets.EntrySelection(tuple(items), ()))
    assert "Rule \\*literal\\*" in markdown
    assert "Narrative" not in markdown
    assert "| A | B |" in markdown
    assert table_count == 1
    assert notices == ()


def test_build_rules_section_rejects_missing_locator_and_table_child():
    missing = study_packets.EntrySelection((
        _item("chunk_0000000000000001", content_type="statutory_excerpt",
              page_range=None),
    ), ())
    with pytest.raises(study_packets.CitationBindingError, match="locator"):
        study_packets.build_rules_section(missing)
    child = study_packets.EntrySelection((
        _item("chunk_0000000000000002", content_type="table",
              retrieval_role="table_child",
              table_parent_stable_id="chunk_0000000000000001"),
    ), ())
    with pytest.raises(study_packets.EntrySelectionError, match="table child"):
        study_packets.build_rules_section(child)


def test_build_rules_section_escapes_table_caption_and_cell_markup():
    table = (
        "![remote](https://example.invalid/a.png)\n"
        "| Rule | Effect |\n"
        "|---|---|\n"
        "| [link](javascript:alert(1)) | <script>bad</script> |"
    )
    markdown, table_count, _notices = study_packets.build_rules_section(
        study_packets.EntrySelection((
            _item("chunk_0000000000000001", table, content_type="table",
                  retrieval_role="table_parent",
                  table_parent_stable_id="chunk_0000000000000001"),
        ), ()))
    assert table_count == 1
    assert "![remote](https://" not in markdown
    assert "[link](javascript:" not in markdown
    assert "<script>" not in markdown
    assert r"\!\[remote\]\(https://example\.invalid/a\.png\)" in markdown


def test_rendered_canonical_table_passes_real_marker_validation():
    table = "Caption\n| Rule | Effect |\n| --- | ---: |\n| One | Two |"
    rules, table_count, _notices = study_packets.build_rules_section(
        study_packets.EntrySelection((
            _item("chunk_0000000000000001", table, content_type="table",
                  retrieval_role="table_parent",
                  table_parent_stable_id="chunk_0000000000000001"),
        ), ()))
    candidate = "# Packet\n\n" + rules
    receipt = markdown_validation.validate_markdown_candidate(
        candidate, expected_table_count=table_count,
        source_name="packets/week-03-conflicts.md", policy="internal",
        require_table_markers=True)
    assert receipt["internal"]["table_count"] == 1
    assert "<!-- TABLE -->\n\nCaption\n\n| Rule | Effect |" in candidate


@pytest.mark.parametrize("suffix", ["## Injected", "plain trailing text"])
def test_build_rules_section_rejects_non_table_trailing_material(suffix):
    table = f"| Rule |\n|---|\n| Safe |\n{suffix}"
    selection = study_packets.EntrySelection((
        _item("chunk_0000000000000001", table, content_type="table",
              retrieval_role="table_parent",
              table_parent_stable_id="chunk_0000000000000001"),
    ), ())
    with pytest.raises(study_packets.PacketBuildError, match="trailing"):
        study_packets.build_rules_section(selection)


def test_build_rules_section_records_budget_truncation():
    items = tuple(
        _item(f"chunk_{index:016x}", f"Rule {index}.",
              content_type="statutory_excerpt")
        for index in range(study_packets.MAX_RULES_CHUNKS + 1)
    )
    markdown, _count, notices = study_packets.build_rules_section(
        study_packets.EntrySelection(items, ()))
    assert f"Rule {study_packets.MAX_RULES_CHUNKS}." not in markdown
    assert any("truncated" in notice for notice in notices)


def test_case_digest_contract_binds_identity_and_each_field_citation():
    stable_id = "chunk_0000000000000001"
    contract = study_packets.case_digest_contract("A v. B", {stable_id})
    parsed = json.loads(contract(json.dumps(_digest_payload("A v. B", stable_id))))
    assert parsed["holding"]["citations"] == [stable_id]
    provenance = contract.provenance(
        fallback_id=study_packets.CASE_DIGEST_FALLBACK_ID)
    assert provenance["contract_id"] == study_packets.CASE_DIGEST_CONTRACT_ID


@pytest.mark.parametrize("case_name", ["García v. State", "In re [Example]"])
def test_case_digest_contract_accepts_exact_safe_source_case_identity(case_name):
    stable_id = "chunk_0000000000000001"
    contract = study_packets.case_digest_contract(case_name, {stable_id})
    payload = _digest_payload(case_name, stable_id)
    assert json.loads(contract(json.dumps(payload)))["case_name"] == case_name


@pytest.mark.parametrize("mutator", [
    lambda payload: payload.update(case_name="C v. D"),
    lambda payload: payload["facts"].update(citations=[]),
    lambda payload: payload["holding"].update(citations=["unknown"]),
    lambda payload: payload["facts"].update(text="non-ASCII §"),
    lambda payload: payload["facts"].update(text="Markdown *injection*"),
    lambda payload: payload["facts"].update(text="line\nbreak"),
    lambda payload: payload.update(extra=True),
])
def test_case_digest_contract_rejects_unbound_or_unsafe_payload(mutator):
    stable_id = "chunk_0000000000000001"
    payload = _digest_payload("A v. B", stable_id)
    mutator(payload)
    contract = study_packets.case_digest_contract("A v. B", {stable_id})
    with pytest.raises(llm_output_contracts.OutputContractRejected):
        contract(json.dumps(payload))


def test_outline_contract_uses_only_prompt_visible_ids_and_safe_text():
    stable_id = "chunk_0000000000000001"
    contract = study_packets.outline_contract({stable_id})
    good = {"outline": [{
        "text": "Apply the governing duty.", "citations": [stable_id],
    }]}
    assert json.loads(contract(json.dumps(good))) == good
    bad_values = [
        {"outline": []},
        {"outline": [{"text": "No citation.", "citations": []}]},
        {"outline": [{"text": "Unknown.", "citations": ["unknown"]}]},
        {"outline": [{"text": "Unsafe [link].", "citations": [stable_id]}]},
        {"outline": [{"text": "Non-ASCII §", "citations": [stable_id]}]},
    ]
    for bad in bad_values:
        with pytest.raises(llm_output_contracts.OutputContractRejected):
            contract(json.dumps(bad))


def test_prompt_bundles_expose_exact_visible_universe_and_all_truncations():
    items = tuple(
        _item(f"chunk_{index:016x}", "x" * 1300)
        for index in range(study_packets.OUTLINE_MAX_SOURCES + 1)
    )
    bundle = study_packets.outline_prompt_bundle(
        study_packets.EntrySelection(items, ()))
    payload_line = bundle.prompt.split(
        "SOURCE_JSON (one physical line; bounded topic sources):\n",
    )[1].splitlines()[0]
    payload = json.loads(payload_line)
    assert len(payload["sources"]) == study_packets.OUTLINE_MAX_SOURCES
    assert len(bundle.items) == study_packets.OUTLINE_MAX_SOURCES
    assert all(len(source["text"]) == 1200 for source in payload["sources"])
    assert any("prompt sources truncated" in notice for notice in bundle.notices)
    assert any("source excerpt" in notice for notice in bundle.notices)


def test_case_groups_require_primary_case_identity():
    items = [
        _item("chunk_0000000000000001", content_type="case_opinion",
              primary_case="A v. B"),
        _item("chunk_0000000000000002", content_type="case_opinion",
              primary_case="A v. B"),
    ]
    groups, notices = study_packets.case_groups(
        study_packets.EntrySelection(tuple(items), ()))
    assert [group.case_name for group in groups] == ["A v. B"]
    assert len(groups[0].items) == 2
    assert notices == ()
    missing = study_packets.EntrySelection((
        _item("chunk_0000000000000003", content_type="case_opinion",
              primary_case=None, case_names=["Fallback v. Name"]),
    ), ())
    with pytest.raises(study_packets.PacketBuildError, match="primary_case"):
        study_packets.case_groups(missing)


@pytest.mark.parametrize("unsafe", [" ", " A v. B", "A v. B ", "A\u00a0v. B"])
def test_case_groups_reject_blank_padded_or_unusual_case_identity(unsafe):
    selection = study_packets.EntrySelection((
        _item("chunk_0000000000000001", content_type="case_opinion",
              primary_case=unsafe),
    ), ())
    with pytest.raises(study_packets.PacketBuildError, match="primary_case"):
        study_packets.case_groups(selection)


def test_build_cases_section_renders_per_field_citations():
    stable_id = "chunk_0000000000000001"
    group = study_packets.CaseGroup("A v. B", (
        _item(stable_id, content_type="case_opinion", primary_case="A v. B"),
    ))
    markdown, notices = study_packets.build_cases_section((group,), _ok_llm)
    assert r"### A v\. B" in markdown
    assert r"**Holding.** The court held for the client\." in markdown
    assert markdown.count(r"fixture\.pdf") == 3
    assert notices == ()


def test_build_cases_section_retries_then_uses_cited_exact_excerpt():
    stable_id = "chunk_0000000000000001"
    source = "Exact synthetic opinion excerpt."
    group = study_packets.CaseGroup("A v. B", (
        _item(stable_id, source, content_type="case_opinion",
              primary_case="A v. B"),
    ))
    attempts = []

    def unavailable(_prompt, _contract, _operation):
        attempts.append(1)
        return None

    markdown, notices = study_packets.build_cases_section((group,), unavailable)
    assert len(attempts) == 2
    assert "Exact synthetic opinion excerpt\\." in markdown
    assert r"fixture\.pdf" in markdown
    assert any("digest unavailable" in notice for notice in notices)


def test_build_cases_section_does_not_swallow_unexpected_llm_error():
    group = study_packets.CaseGroup("A v. B", (
        _item("chunk_0000000000000001", content_type="case_opinion",
              primary_case="A v. B"),
    ))

    def broken(_prompt, _contract, _operation):
        raise AssertionError("programming defect")

    with pytest.raises(AssertionError, match="programming defect"):
        study_packets.build_cases_section((group,), broken)


def test_build_cases_section_degrades_explicit_runtime_failure():
    group = study_packets.CaseGroup("A v. B", (
        _item("chunk_0000000000000001", content_type="case_opinion",
              primary_case="A v. B"),
    ))

    def unavailable(_prompt, _contract, _operation):
        raise study_packets.LLMSectionUnavailable("budget")

    markdown, notices = study_packets.build_cases_section((group,), unavailable)
    assert "Digest unavailable" in markdown
    assert any("digest unavailable" in notice for notice in notices)


def test_build_outline_section_renders_locators_not_stable_ids():
    selection = study_packets.EntrySelection((
        _item("chunk_0000000000000001"),
    ), ())
    markdown, notices = study_packets.build_outline_section(selection, _ok_llm)
    assert markdown is not None
    assert r"Identify the governing duty\." in markdown
    assert "chunk_" not in markdown
    assert r"fixture\.pdf" in markdown
    assert notices == ()


def test_build_outline_section_omits_when_no_locator_or_valid_output():
    missing = study_packets.EntrySelection((
        _item("chunk_0000000000000001", page_range=None),
    ), ())
    markdown, notices = study_packets.build_outline_section(missing, _ok_llm)
    assert markdown is None
    assert any("no citation-ready" in notice for notice in notices)
    valid = study_packets.EntrySelection((
        _item("chunk_0000000000000001"),
    ), ())
    markdown, notices = study_packets.build_outline_section(
        valid, lambda *_args: None)
    assert markdown is None
    assert any("outline omitted" in notice for notice in notices)


def test_build_entry_packet_places_every_notice_in_header_and_escapes_scalars():
    selection = study_packets.EntrySelection((
        _item("chunk_0000000000000001", "Rule *literal*.",
              content_type="statutory_excerpt"),
    ), ("retrieval degraded",))
    packet = study_packets.build_entry_packet(
        "Legal Ethics", _entry(), selection, _header(), _ok_llm)
    header, _sections = packet.markdown.split("## Rules and definitions", 1)
    assert "retrieval degraded" in header
    assert "Build notices" in header
    assert "Rule \\*literal\\*" in packet.markdown
    assert "## Key cases" in packet.markdown
    assert "## Issue outline" in packet.markdown
    study_packets.validate_packet_markdown(packet.markdown)


def test_build_entry_packet_rejects_header_mismatch():
    selection = study_packets.EntrySelection((
        _item("chunk_0000000000000001"),
    ), ())
    with pytest.raises(study_packets.PacketBuildError, match="does not match"):
        study_packets.build_entry_packet(
            "Other Course", _entry(), selection, _header(), _ok_llm)


def test_packet_notices_cannot_inject_new_markdown_structure():
    selection = study_packets.EntrySelection((
        _item("chunk_0000000000000001"),
    ), ("warning\n## injected",))
    with pytest.raises(study_packets.PacketBuildError, match="controls"):
        study_packets.build_entry_packet(
            "Legal Ethics", _entry(), selection, _header(), _ok_llm)


def test_markdown_scalar_escapes_currency_delimiters():
    assert study_packets.escape_markdown_scalar(
        "Damages $500; ~~hidden~~ ^up^ @cite &amp; x=y.") == (
        r"Damages \$500; \~\~hidden\~\~ \^up\^ \@cite \&amp; x\=y\.")


@pytest.mark.parametrize("unsafe", [
    "~~hidden~~",
    "line ^up^",
    "$500",
    "@citation",
])
def test_llm_contracts_reject_markdown_extension_delimiters(unsafe):
    digest = _digest_payload(
        "A v. B", "chunk_0000000000000001")
    digest["holding"]["text"] = unsafe
    with pytest.raises(llm_output_contracts.OutputContractRejected):
        study_packets.case_digest_contract(
            "A v. B", {"chunk_0000000000000001"})(json.dumps(digest))
    outline = {"outline": [{
        "text": unsafe,
        "citations": ["chunk_0000000000000001"],
    }]}
    with pytest.raises(llm_output_contracts.OutputContractRejected):
        study_packets.outline_contract(
            {"chunk_0000000000000001"})(json.dumps(outline))


def test_packet_header_requires_complete_exact_index_binding():
    with pytest.raises(study_packets.PacketBuildError, match="source_sha256"):
        _header(index_binding=_binding(source_sha256="abc"))
    incomplete = _binding()
    incomplete.pop("embedding_model")
    with pytest.raises(study_packets.PacketBuildError, match="index binding"):
        _header(index_binding=incomplete)
    with pytest.raises(
            study_packets.PacketBuildError,
            match="snapshot_fingerprint_sha256"):
        _header(index_binding=_binding(snapshot_fingerprint_sha256="bad"))


@pytest.mark.parametrize("field", ["backend", "collection", "embedding_model"])
@pytest.mark.parametrize("unsafe", [" ", " padded", "padded ", "a\u00a0b"])
def test_packet_header_rejects_noncanonical_index_binding_scalars(field, unsafe):
    with pytest.raises(study_packets.PacketBuildError, match="index binding"):
        _header(index_binding=_binding(**{field: unsafe}))


def test_course_index_binds_generation_source_and_exact_packet_bytes():
    packet = study_packets.EntryPacket(
        "week-03-conflicts", "Conflicts [Current]", "# Packet\n", 0, ())
    failure = study_packets.EntryFailure(
        "week-04", "Candor *Duty*", "exact selector matched nothing")
    record = study_packets.packet_output_record(packet)
    index = study_packets.render_course_index(
        "Legal Ethics", (packet,), (failure,), index_binding=_binding(),
        generated_at="2026-08-01T12:00:00+00:00",
        output_records=(record,))
    generation_id = study_packets.course_generation_id(
        "Legal Ethics", _binding(), (record,), (failure,))
    assert f"`{generation_id}`" in index
    assert f'"source_sha256":"{SOURCE_SHA}"' in index
    assert f"`{record.sha256}`" in index
    assert "[Conflicts \\[Current\\]](week-03-conflicts.md)" in index
    assert "Candor \\*Duty\\*" in index
    study_packets.validate_course_index_markdown(
        index, output_records=(record,), generation_id=generation_id)


def test_course_index_inventory_parsing_ignores_safe_binding_text():
    packet = study_packets.EntryPacket(
        "week-03-conflicts", "Conflicts", "# Packet\n", 0, ())
    record = study_packets.packet_output_record(packet)
    binding = _binding(
        embedding_model="model ## Packets ](evil.md)")
    index = study_packets.render_course_index(
        "Legal Ethics", (packet,), (), index_binding=binding,
        generated_at="2026-08-01T12:00:00+00:00",
        output_records=(record,))
    study_packets.validate_course_index_markdown(
        index, output_records=(record,))


def test_course_index_validation_translates_malformed_inventory_marker():
    malformed = "# Course\n\ntext ## Packets text\n"
    with pytest.raises(study_packets.PacketBuildError, match="inventory"):
        study_packets.validate_course_index_markdown(malformed)


def test_course_index_validation_rejects_tampered_hash_binding():
    packet = study_packets.EntryPacket(
        "week-03-conflicts", "Conflicts", "# Packet\n", 0, ())
    record = study_packets.packet_output_record(packet)
    index = study_packets.render_course_index(
        "Legal Ethics", (packet,), (), index_binding=_binding(),
        generated_at="2026-08-01T12:00:00+00:00",
        output_records=(record,))
    tampered = index.replace(record.sha256, "c" * 64)
    with pytest.raises(study_packets.PacketBuildError, match="does not bind"):
        study_packets.validate_course_index_markdown(
            tampered, output_records=(record,))


@pytest.mark.parametrize("tamper", [
    lambda index: index.replace(
        "\n## Failed entries", "\n- [Extra](extra.md)\n"
        "  - SHA-256: `" + "e" * 64 + "`\n"
        "  - Bytes: `9`\n\n## Failed entries"),
    lambda index: index.replace(
        "\n## Failed entries", "\n- [Conflicts](week-03-conflicts.md)\n"
        "  - SHA-256: `" + "e" * 64 + "`\n"
        "  - Bytes: `9`\n\n## Failed entries"),
])
def test_course_index_validation_rejects_extra_or_duplicate_inventory(tamper):
    packet = study_packets.EntryPacket(
        "week-03-conflicts", "Conflicts", "# Packet\n", 0, ())
    failure = study_packets.EntryFailure(
        "week-04", "Candor", "exact selector matched nothing")
    record = study_packets.packet_output_record(packet)
    index = study_packets.render_course_index(
        "Legal Ethics", (packet,), (failure,), index_binding=_binding(),
        generated_at="2026-08-01T12:00:00+00:00",
        output_records=(record,))
    with pytest.raises(study_packets.PacketBuildError, match="exact outputs"):
        study_packets.validate_course_index_markdown(
            tamper(index), output_records=(record,))


@pytest.mark.parametrize("link", ["../evil.md", r"..\evil.md", "C:evil.md"])
def test_course_index_validation_rejects_cross_platform_unsafe_links(link):
    markdown = f"# Course\n\n## Packets\n\n- [Bad]({link})\n"
    with pytest.raises(study_packets.PacketBuildError, match="non-local"):
        study_packets.validate_course_index_markdown(markdown)


def test_course_index_rejects_forged_output_record_for_packet_bytes():
    packet = study_packets.EntryPacket(
        "week-03-conflicts", "Conflicts", "# Packet\n", 0, ())
    forged = study_packets.PacketOutputRecord(
        packet.entry_id, packet.title, f"{packet.entry_id}.md",
        "c" * 64, 999)
    with pytest.raises(study_packets.PacketBuildError, match="exact packet bytes"):
        study_packets.render_course_index(
            "Legal Ethics", (packet,), (), index_binding=_binding(),
            generated_at="2026-08-01T12:00:00+00:00",
            output_records=(forged,))


def test_course_index_rejects_duplicate_or_overlapping_entry_outcomes():
    packet = study_packets.EntryPacket(
        "week-03-conflicts", "Conflicts", "# Packet\n", 0, ())
    duplicate = study_packets.EntryFailure(
        "week-04", "Candor", "exact selector matched nothing")
    with pytest.raises(study_packets.PacketBuildError, match="unique and disjoint"):
        study_packets.render_course_index(
            "Legal Ethics", (), (duplicate, duplicate),
            index_binding=_binding(),
            generated_at="2026-08-01T12:00:00+00:00")
    overlap = study_packets.EntryFailure(
        packet.entry_id, packet.title, "packet build failed")
    with pytest.raises(study_packets.PacketBuildError, match="unique and disjoint"):
        study_packets.render_course_index(
            "Legal Ethics", (packet,), (overlap,),
            index_binding=_binding(),
            generated_at="2026-08-01T12:00:00+00:00")


def test_course_generation_id_binds_full_failure_and_rejects_forgery():
    first = study_packets.EntryFailure(
        "week-04", "Candor", "exact selector matched nothing")
    changed_title = study_packets.EntryFailure(
        "week-04", "Candor duties", "exact selector matched nothing")
    changed_reason = study_packets.EntryFailure(
        "week-04", "Candor", "LLM contract rejected")
    baseline = study_packets.course_generation_id(
        "Legal Ethics", _binding(), (), (first,))
    assert baseline != study_packets.course_generation_id(
        "Legal Ethics", _binding(), (), (changed_title,))
    assert baseline != study_packets.course_generation_id(
        "Legal Ethics", _binding(), (), (changed_reason,))
    with pytest.raises(study_packets.PacketBuildError, match="exact outcomes"):
        study_packets.render_course_index(
            "Legal Ethics", (), (first,), index_binding=_binding(),
            generated_at="2026-08-01T12:00:00+00:00",
            generation_id="f" * 64)


def test_course_generation_id_rejects_impossible_duplicate_outcomes():
    packet = study_packets.EntryPacket(
        "week-03-conflicts", "Conflicts", "# Packet\n", 0, ())
    record = study_packets.packet_output_record(packet)
    with pytest.raises(study_packets.PacketBuildError, match="unique and disjoint"):
        study_packets.course_generation_id(
            "Legal Ethics", _binding(), (record, record), ())
    overlap = study_packets.EntryFailure(
        record.entry_id, record.title, "failed")
    with pytest.raises(study_packets.PacketBuildError, match="unique and disjoint"):
        study_packets.course_generation_id(
            "Legal Ethics", _binding(), (record,), (overlap,))


@pytest.mark.parametrize("reason", [
    "",
    " ",
    "failed\n## injected",
    "x" * (study_packets.MAX_FAILURE_REASON_CHARACTERS + 1),
])
def test_entry_failure_rejects_unsafe_or_unbounded_reason(reason):
    with pytest.raises(study_packets.PacketBuildError, match="reason"):
        study_packets.EntryFailure("week-04", "Candor", reason)


def test_course_index_validation_enforces_byte_ceiling():
    oversized = (
        "# Course study packets\n\n## Packets\n\n"
        + "x" * study_packets.MAX_COURSE_INDEX_BYTES
        + "\n"
    )
    with pytest.raises(study_packets.PacketBuildError, match="byte limit"):
        study_packets.validate_course_index_markdown(oversized)


def test_course_outcome_count_is_bounded_before_hashing_or_rendering():
    failures = tuple(
        study_packets.EntryFailure(
            f"entry-{index}", f"Entry {index}", "failed")
        for index in range(study_packets.MAX_SYLLABUS_ENTRIES + 1)
    )
    with pytest.raises(study_packets.PacketBuildError, match="entry limit"):
        study_packets.course_generation_id(
            "Legal Ethics", _binding(), (), failures)
    with pytest.raises(study_packets.PacketBuildError, match="entry limit"):
        study_packets.render_course_index(
            "Legal Ethics", (), failures, index_binding=_binding(),
            generated_at="2026-08-01T12:00:00+00:00")


def test_course_index_failure_reason_cannot_inject_markdown():
    with pytest.raises(study_packets.PacketBuildError, match="reason is invalid"):
        study_packets.EntryFailure(
            "week-04", "Candor", "failed\n## injected")
