# Syllabus-Driven Study Packets Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Build `study_packets.py` (deterministic packet policy) plus a
`rag.py packets` subcommand that turns a `study-syllabus-v1` JSON file and an
indexed casebook into one validated Markdown study packet per syllabus entry
plus a course index, per
`docs/superpowers/specs/2026-08-01-study-packets-design.md`.

**Architecture:** All policy (syllabus schema, selection, section assembly,
LLM output contracts, rendering) lives in a new dependency-light typed module
`study_packets.py` with injected callables for retrieval and LLM calls.
`rag.py` gains a thin handler `build_study_packets(...)` wiring existing
collaborators: `_load_index_snapshot_strict`, `search_index`, `_call_llm`,
`markdown_validation.validate_markdown_candidate`,
`_artifact_io._atomic_write_text`, and `index_state._load_index_manifest`.
No behavior of existing commands changes.

**Tech Stack:** Python 3.10–3.14 standard library only in `study_packets.py`
(plus first-party `llm_output_contracts`); pytest; the repository's existing
retrieval/LLM/validation modules via injection.

## Global Constraints (verbatim from the spec and repo policy)

- Codex works on a feature branch off current `main`; never commits to `main`.
- NEVER hand-edit `*.lock`, `benchmarks/phase-a0-*.json`, or
  `architecture-inventory.json`. The inventory is refreshed ONLY via
  `python tools/check_architecture_inventory.py --refresh` (Task 8).
- Do not touch `.github/workflows/` or any file under `benchmarks/`.
- Do not claim any hosted or Phase A0 result anywhere. Evidence regeneration,
  candidate publication, and merge remain with the owner's machine.
- Do not edit `ROADMAP.md` (the owner's candidate ritual updates it).
- `study_packets.py` imports only the standard library, `typing`, and
  `llm_output_contracts`. No third-party imports, no `rag` import.
- Corpus-derived content never leaves private outputs; tests use only
  synthetic fixture text (no real casebook text in any committed file).
- Fail-closed at every boundary: invalid syllabus → no run; per-entry errors
  are recorded and skip the entry; the run exits non-zero if any entry failed.
- Keep files under 500 lines where feasible; `study_packets.py` may reach
  ~600 given contracts + rendering — do not split it preemptively.
- Line length and style: match `ruff` config (target py310, E4/E7/E9/F);
  run `python -m ruff check .` before every commit.
- All quality gates must be green before the final commit:
  `python -m pytest -q`, `python -m ruff check .`,
  `python tools/check_python_sources.py`,
  `python tools/check_dependency_policy.py`,
  `python tools/check_model_artifacts.py`,
  `python tools/check_architecture_inventory.py` (after `--refresh`).

## Repository orientation (read once before Task 1)

- Spec: `docs/superpowers/specs/2026-08-01-study-packets-design.md`.
- Chunk records are `{"text": str, "metadata": dict}` (rag.py:7738-7741).
  Metadata keys used here: `content_type` (one of the labels at
  rag.py:333-337, incl. `case_opinion`, `statutory_excerpt`, `table`),
  `headings` (list[str]), `section_path` (str), `chapter_num`,
  `chapter_title`, `page_range`, `source_file`, `primary_case`,
  `case_names`, `stable_id` (added by retrieval linkage,
  retrieval_core.py:564-597), and for tables `retrieval_role` /
  `table_parent_stable_id` (table_retrieval_core.py:19-36).
- Retrieval entry point: `rag.search_index(query, db_dir, *, db_backend,
  n_results, content_type, chapter_num, collection_name, embedding_model,
  ..., chunks_path, ..., security_policy) -> SearchResponse` (rag.py:23314).
  `SearchResponse.hits` is a list of `SearchHit(text, metadata, score, ...)`
  (retrieval_core.py:65,125).
- LLM seam: `rag._call_llm(prompt, *, ..., max_tokens, operation,
  prompt_version, timeout, output_contract_id, output_fallback_id,
  output_validator, security_policy) -> Optional[str]` (rag.py:5926).
- Contracts: `llm_output_contracts.ExactJSONContract(contract_id, max_bytes,
  max_depth, schema_validator, ...)` — instance is callable:
  `contract(response_text) -> canonical_json_text` or raises
  `OutputContractRejected`; `contract.provenance(fallback_id=...)` returns
  the reuse-binding dict (llm_output_contracts.py:322-403).
- Markdown validation: `markdown_validation.validate_markdown_candidate(
  markdown, *, expected_table_count, source_name, policy="auto",
  require_table_markers=True) -> dict` raising `MarkdownValidationError`
  (markdown_validation.py:825).
- Atomic writes: `artifact_io._atomic_write_text(path, content)`
  (artifact_io.py:1342).
- Index manifest: `index_state._load_index_manifest(db_dir, *, backend,
  collection_name, manifest_path_fn, warning_fn) -> dict | None`
  (index_state.py:166) with
  `manifest_path_fn=index_state._index_manifest_path`. Manifest fields
  include `source_sha256`, `embedding_model`, `collection`, `backend`.
- Profiles: `document_profiles.get_profile(value)`,
  `profile_sha256(profile)`, `profile_from_provenance(receipt)`
  (document_profiles.py:231-576). The chunk-stage receipt's field is named
  `structure_profile` (validated near rag.py:1517-1539).
- CLI: subparsers are added inside `rag.main` (rag.py:30345, `sub` at
  :30361); the `brief` block (rag.py:30648-30652) and its dispatch
  (rag.py:31153-31154) are the model to copy. Shared flag helpers:
  `add_llm_provider_flags(p)` (rag.py:30492), `add_collection_flag(p)`
  (rag.py:30408), `add_db_backend_flag(p)` (rag.py:30415),
  `add_embedding_flags(p)` (rag.py:30400). LLM kwargs come from
  `_llm_kwargs_from_args(args, include_workers=True)` and the
  `security_policy` defaulting set at rag.py:31058-31068.
- Test idioms: monkeypatch `rag._call_llm` (see
  tests/test_llm_gating.py:1115-1174); build synthetic hits like
  tests/test_grounded_answers.py:8-48; drive the CLI with
  `rag.main([...])` after monkeypatching the handler (see
  tests/test_cli_run_telemetry.py:41-46).

## File structure

- Create: `study_packets.py` — syllabus schema, selection policy, section
  builders, contracts, rendering. Pure functions; injected callables.
- Create: `tests/test_study_packets.py` — all unit tests for the module.
- Modify: `rag.py` — `build_study_packets(...)` handler + `packets`
  subparser + dispatch + membership in the `security_policy` defaulting set.
- Create: `tests/test_study_packets_cli.py` — handler/CLI wiring tests.

---

### Task 1: Syllabus contract (`study-syllabus-v1`)

**Files:**
- Create: `study_packets.py`
- Test: `tests/test_study_packets.py`

**Interfaces:**
- Consumes: nothing (pure stdlib).
- Produces (used by every later task):
  - `SYLLABUS_SCHEMA = "study-syllabus-v1"`
  - `class SyllabusError(ValueError)`
  - `@dataclass(frozen=True) SyllabusEntry(entry_id: str, title: str,
    chapters: tuple[str, ...], queries: tuple[str, ...])`
  - `@dataclass(frozen=True) Syllabus(course: str, structure_profile: str,
    entries: tuple[SyllabusEntry, ...])`
  - `parse_syllabus(payload: object) -> Syllabus`
  - `load_syllabus_text(text: str) -> Syllabus`

- [ ] **Step 1: Write the failing tests**

```python
"""Tests for the study-packet policy module."""

import json

import pytest

import study_packets


def _syllabus_payload(**overrides):
    payload = {
        "schema": "study-syllabus-v1",
        "course": "Legal Ethics",
        "structure_profile": "legal-casebook-v1",
        "entries": [
            {
                "id": "week-03-conflicts",
                "title": "Conflicts of Interest",
                "chapters": ["Chapter 3"],
                "queries": ["conflicts of interest current clients"],
            },
        ],
    }
    payload.update(overrides)
    return payload


def test_parse_syllabus_accepts_minimal_valid_payload():
    syllabus = study_packets.parse_syllabus(_syllabus_payload())
    assert syllabus.course == "Legal Ethics"
    assert syllabus.structure_profile == "legal-casebook-v1"
    assert len(syllabus.entries) == 1
    entry = syllabus.entries[0]
    assert entry.entry_id == "week-03-conflicts"
    assert entry.title == "Conflicts of Interest"
    assert entry.chapters == ("Chapter 3",)
    assert entry.queries == ("conflicts of interest current clients",)


def test_parse_syllabus_accepts_chapters_only_and_queries_only():
    chapters_only = _syllabus_payload(entries=[
        {"id": "a", "title": "A", "chapters": ["Chapter 1"]}])
    queries_only = _syllabus_payload(entries=[
        {"id": "b", "title": "B", "queries": ["duty of candor"]}])
    assert study_packets.parse_syllabus(chapters_only).entries[0].queries == ()
    assert study_packets.parse_syllabus(queries_only).entries[0].chapters == ()


@pytest.mark.parametrize("mutation, match", [
    ({"schema": "study-syllabus-v2"}, "schema"),
    ({"course": ""}, "course"),
    ({"course": 7}, "course"),
    ({"structure_profile": ""}, "structure_profile"),
    ({"entries": []}, "entries"),
    ({"entries": "nope"}, "entries"),
    ({"extra_field": 1}, "unexpected"),
])
def test_parse_syllabus_rejects_invalid_top_level(mutation, match):
    with pytest.raises(study_packets.SyllabusError, match=match):
        study_packets.parse_syllabus(_syllabus_payload(**mutation))


@pytest.mark.parametrize("entry, match", [
    ({"id": "x", "title": "X"}, "chapters or queries"),
    ({"id": "x", "title": "X", "chapters": []}, "chapters or queries"),
    ({"id": "Bad Slug!", "title": "X", "chapters": ["c"]}, "id"),
    ({"id": "x", "title": "", "chapters": ["c"]}, "title"),
    ({"id": "x", "title": "X", "chapters": [""]}, "chapters"),
    ({"id": "x", "title": "X", "chapters": ["c"], "bogus": 1}, "unexpected"),
])
def test_parse_syllabus_rejects_invalid_entries(entry, match):
    with pytest.raises(study_packets.SyllabusError, match=match):
        study_packets.parse_syllabus(_syllabus_payload(entries=[entry]))


def test_parse_syllabus_rejects_duplicate_entry_ids():
    entries = [
        {"id": "same", "title": "A", "chapters": ["c"]},
        {"id": "same", "title": "B", "chapters": ["d"]},
    ]
    with pytest.raises(study_packets.SyllabusError, match="duplicate"):
        study_packets.parse_syllabus(_syllabus_payload(entries=entries))


def test_load_syllabus_text_rejects_non_json_and_non_object():
    with pytest.raises(study_packets.SyllabusError, match="JSON"):
        study_packets.load_syllabus_text("not json")
    with pytest.raises(study_packets.SyllabusError, match="object"):
        study_packets.load_syllabus_text(json.dumps([1, 2]))
```

- [ ] **Step 2: Run tests to verify they fail**

Run: `python -m pytest tests/test_study_packets.py -q`
Expected: FAIL/ERROR with `ModuleNotFoundError: No module named
'study_packets'`.

- [ ] **Step 3: Implement the module skeleton and syllabus parsing**

```python
"""Deterministic policy for syllabus-driven topic study packets.

Standard-library-only. ``rag.py`` injects retrieval and LLM callables; this
module owns the syllabus schema, selection policy, section assembly, LLM
output contracts, and Markdown rendering. See
docs/superpowers/specs/2026-08-01-study-packets-design.md.
"""

from __future__ import annotations

from dataclasses import dataclass, field
import hashlib
import json
import re
from typing import Any, Callable, Collection, Optional

import llm_output_contracts as _contracts

SYLLABUS_SCHEMA = "study-syllabus-v1"
_ENTRY_ID_RE = re.compile(r"^[a-z0-9][a-z0-9-]{0,63}$")
_SYLLABUS_KEYS = frozenset({"schema", "course", "structure_profile",
                            "entries"})
_ENTRY_KEYS = frozenset({"id", "title", "chapters", "queries"})


class SyllabusError(ValueError):
    """Invalid ``study-syllabus-v1`` document; the run must not start."""


@dataclass(frozen=True)
class SyllabusEntry:
    entry_id: str
    title: str
    chapters: tuple[str, ...]
    queries: tuple[str, ...]


@dataclass(frozen=True)
class Syllabus:
    course: str
    structure_profile: str
    entries: tuple[SyllabusEntry, ...]


def _required_text(payload: dict, key: str) -> str:
    value = payload.get(key)
    if not isinstance(value, str) or not value.strip():
        raise SyllabusError(f"{key} must be non-empty text")
    return value


def _selector_tuple(entry: dict, key: str, entry_id: str) -> tuple[str, ...]:
    value = entry.get(key, [])
    if not isinstance(value, list) or any(
            not isinstance(item, str) or not item.strip() for item in value):
        raise SyllabusError(
            f"entry {entry_id!r}: {key} must be a list of non-empty text")
    return tuple(value)


def parse_syllabus(payload: object) -> Syllabus:
    """Validate one ``study-syllabus-v1`` object fail-closed."""
    if not isinstance(payload, dict):
        raise SyllabusError("syllabus must be a JSON object")
    unexpected = sorted(set(payload) - _SYLLABUS_KEYS)
    if unexpected:
        raise SyllabusError(f"unexpected fields: {', '.join(unexpected)}")
    if payload.get("schema") != SYLLABUS_SCHEMA:
        raise SyllabusError(f"schema must be {SYLLABUS_SCHEMA!r}")
    course = _required_text(payload, "course")
    profile = _required_text(payload, "structure_profile")
    raw_entries = payload.get("entries")
    if not isinstance(raw_entries, list) or not raw_entries:
        raise SyllabusError("entries must be a non-empty list")
    entries: list[SyllabusEntry] = []
    seen_ids: set[str] = set()
    for raw in raw_entries:
        if not isinstance(raw, dict):
            raise SyllabusError("entries must contain objects")
        unexpected = sorted(set(raw) - _ENTRY_KEYS)
        if unexpected:
            raise SyllabusError(
                f"entry has unexpected fields: {', '.join(unexpected)}")
        entry_id = raw.get("id")
        if not isinstance(entry_id, str) or not _ENTRY_ID_RE.fullmatch(
                entry_id):
            raise SyllabusError(
                "entry id must be a lowercase filesystem-safe slug")
        if entry_id in seen_ids:
            raise SyllabusError(f"duplicate entry id {entry_id!r}")
        seen_ids.add(entry_id)
        title = raw.get("title")
        if not isinstance(title, str) or not title.strip():
            raise SyllabusError(f"entry {entry_id!r}: title must be text")
        chapters = _selector_tuple(raw, "chapters", entry_id)
        queries = _selector_tuple(raw, "queries", entry_id)
        if not chapters and not queries:
            raise SyllabusError(
                f"entry {entry_id!r} needs chapters or queries")
        entries.append(SyllabusEntry(entry_id, title, chapters, queries))
    return Syllabus(course, profile, tuple(entries))


def load_syllabus_text(text: str) -> Syllabus:
    """Parse syllabus JSON text fail-closed."""
    try:
        payload = json.loads(text)
    except json.JSONDecodeError as exc:
        raise SyllabusError(f"syllabus is not valid JSON: {exc}") from None
    if not isinstance(payload, dict):
        raise SyllabusError("syllabus must be a JSON object")
    return parse_syllabus(payload)
```

- [ ] **Step 4: Run tests to verify they pass**

Run: `python -m pytest tests/test_study_packets.py -q`
Expected: all PASS.

- [ ] **Step 5: Lint and commit**

```bash
python -m ruff check study_packets.py tests/test_study_packets.py
git add study_packets.py tests/test_study_packets.py
git commit -m "Add strict study-syllabus-v1 parsing"
```

---

### Task 2: Deterministic selection policy

**Files:**
- Modify: `study_packets.py`
- Test: `tests/test_study_packets.py`

**Interfaces:**
- Consumes: `SyllabusEntry` from Task 1.
- Produces:
  - `MAX_QUERY_HITS_PER_QUERY = 8`
  - `@dataclass(frozen=True) SelectionItem(stable_id: str, text: str,
    metadata: dict)`
  - `@dataclass(frozen=True) EntrySelection(items: tuple[SelectionItem, ...],
    notices: tuple[str, ...])`
  - `class EntrySelectionError(ValueError)`
  - `select_entry(entry: SyllabusEntry, records: list[dict], *,
    search_fn: Callable[[str], list[SelectionItem]],
    max_query_hits: int = MAX_QUERY_HITS_PER_QUERY) -> EntrySelection`
  - `selection_digest(selection: EntrySelection) -> str` (sha256 hex of the
    ordered stable-ID list)

`records` are published-order chunk records `{"text": ..., "metadata": ...}`
whose metadata carries `stable_id`. A chapter selector matches a record when
it equals `metadata["chapter_title"]`, equals `str(metadata["chapter_num"])`,
or is a prefix of `metadata["section_path"]`. A selector matching zero
records raises `EntrySelectionError`. Query hits (already `SelectionItem`s
from the injected `search_fn`) append in rank order, capped at
`max_query_hits` per query with a recorded notice when truncated, and are
deduplicated against already-selected stable IDs. Records without a
`stable_id` raise `EntrySelectionError` (the index snapshot must be
linkage-bearing).

- [ ] **Step 1: Write the failing tests** (append to
  `tests/test_study_packets.py`)

```python
def _record(stable_id, text, **metadata):
    metadata.setdefault("source_file", "book.pdf")
    metadata.setdefault("page_range", "12-13")
    metadata.setdefault("section_path", "Chapter 3 > Conflicts")
    metadata.setdefault("chapter_num", 3)
    metadata.setdefault("chapter_title", "Chapter 3")
    metadata.setdefault("content_type", "author_narrative")
    metadata["stable_id"] = stable_id
    return {"text": text, "metadata": metadata}


def _entry(**overrides):
    values = {"entry_id": "week-03", "title": "Conflicts",
              "chapters": ("Chapter 3",), "queries": ()}
    values.update(overrides)
    return study_packets.SyllabusEntry(**values)


def _item(stable_id, text="hit", **metadata):
    record = _record(stable_id, text, **metadata)
    return study_packets.SelectionItem(
        stable_id=stable_id, text=record["text"],
        metadata=record["metadata"])


def test_select_entry_matches_chapter_by_title_number_and_path_prefix():
    records = [
        _record("chunk_a", "one", chapter_title="Chapter 3"),
        _record("chunk_b", "two", chapter_title="Other",
                chapter_num=99, section_path="Chapter 3 > Deep > Leaf"),
        _record("chunk_c", "three", chapter_title="Chapter 9",
                chapter_num=9, section_path="Chapter 9 > Elsewhere"),
    ]
    by_title = study_packets.select_entry(
        _entry(), records, search_fn=lambda q: [])
    assert [i.stable_id for i in by_title.items] == ["chunk_a", "chunk_b"]
    by_number = study_packets.select_entry(
        _entry(chapters=("9",)), records, search_fn=lambda q: [])
    assert [i.stable_id for i in by_number.items] == ["chunk_c"]


def test_select_entry_preserves_published_order_and_dedups_queries():
    records = [_record("chunk_a", "one"), _record("chunk_b", "two")]
    hits = [_item("chunk_b"), _item("chunk_q", section_path="Ch 9 > X")]
    selection = study_packets.select_entry(
        _entry(queries=("conflicts",)), records, search_fn=lambda q: hits)
    assert [i.stable_id for i in selection.items] == [
        "chunk_a", "chunk_b", "chunk_q"]


def test_select_entry_caps_query_hits_and_records_notice():
    records = [_record("chunk_a", "one")]
    hits = [_item(f"chunk_q{i}") for i in range(10)]
    selection = study_packets.select_entry(
        _entry(queries=("q1",)), records, search_fn=lambda q: hits,
        max_query_hits=3)
    assert len(selection.items) == 1 + 3
    assert any("truncated" in notice for notice in selection.notices)


def test_select_entry_unresolvable_selector_and_missing_stable_id():
    with pytest.raises(study_packets.EntrySelectionError, match="selector"):
        study_packets.select_entry(
            _entry(chapters=("Nowhere",),), [_record("chunk_a", "x")],
            search_fn=lambda q: [])
    bare = {"text": "x", "metadata": {"chapter_title": "Chapter 3",
                                      "section_path": "", "chapter_num": 3}}
    with pytest.raises(study_packets.EntrySelectionError, match="stable_id"):
        study_packets.select_entry(_entry(), [bare], search_fn=lambda q: [])


def test_selection_digest_is_order_sensitive_and_stable():
    a = study_packets.EntrySelection(
        items=(_item("chunk_a"), _item("chunk_b")), notices=())
    b = study_packets.EntrySelection(
        items=(_item("chunk_b"), _item("chunk_a")), notices=())
    assert study_packets.selection_digest(a) != study_packets.selection_digest(b)
    assert study_packets.selection_digest(a) == study_packets.selection_digest(
        study_packets.EntrySelection(
            items=(_item("chunk_a"), _item("chunk_b")), notices=("n",)))
```

- [ ] **Step 2: Run tests to verify they fail**

Run: `python -m pytest tests/test_study_packets.py -q -k "select or digest"`
Expected: FAIL with `AttributeError` (`select_entry` not defined).

- [ ] **Step 3: Implement selection** (append to `study_packets.py`)

```python
MAX_QUERY_HITS_PER_QUERY = 8


class EntrySelectionError(ValueError):
    """The entry cannot be resolved against the published records."""


@dataclass(frozen=True)
class SelectionItem:
    stable_id: str
    text: str
    metadata: dict


@dataclass(frozen=True)
class EntrySelection:
    items: tuple[SelectionItem, ...]
    notices: tuple[str, ...]


def _chapter_selector_matches(selector: str, metadata: dict) -> bool:
    if selector == str(metadata.get("chapter_title", "")):
        return True
    if selector == str(metadata.get("chapter_num", "")):
        return True
    section_path = metadata.get("section_path")
    return isinstance(section_path, str) and section_path.startswith(selector)


def _selection_item(record: dict) -> SelectionItem:
    metadata = record.get("metadata")
    if not isinstance(metadata, dict):
        raise EntrySelectionError("record has no metadata object")
    stable_id = metadata.get("stable_id")
    if not isinstance(stable_id, str) or not stable_id:
        raise EntrySelectionError(
            "record has no stable_id; rebuild the index with retrieval "
            "linkage before building packets")
    text = record.get("text")
    if not isinstance(text, str):
        raise EntrySelectionError("record has no text")
    return SelectionItem(stable_id=stable_id, text=text, metadata=metadata)


def select_entry(entry: SyllabusEntry, records: list[dict], *,
                 search_fn: Callable[[str], list[SelectionItem]],
                 max_query_hits: int = MAX_QUERY_HITS_PER_QUERY,
                 ) -> EntrySelection:
    """Resolve one syllabus entry into an ordered, deduplicated selection."""
    notices: list[str] = []
    chosen: list[SelectionItem] = []
    seen: set[str] = set()
    for selector in entry.chapters:
        matched = False
        for record in records:
            metadata = record.get("metadata")
            if not isinstance(metadata, dict):
                continue
            if _chapter_selector_matches(selector, metadata):
                matched = True
                item = _selection_item(record)
                if item.stable_id not in seen:
                    seen.add(item.stable_id)
                    chosen.append(item)
        if not matched:
            raise EntrySelectionError(
                f"entry {entry.entry_id!r}: chapter selector {selector!r} "
                "matched no records")
    for query in entry.queries:
        hits = search_fn(query)
        if len(hits) > max_query_hits:
            notices.append(
                f"query {query!r}: truncated to the top {max_query_hits} "
                f"of {len(hits)} hits")
            hits = hits[:max_query_hits]
        for item in hits:
            if item.stable_id not in seen:
                seen.add(item.stable_id)
                chosen.append(item)
    return EntrySelection(items=tuple(chosen), notices=tuple(notices))


def selection_digest(selection: EntrySelection) -> str:
    """Stable hash of the ordered stable-ID list for the packet header."""
    payload = json.dumps([item.stable_id for item in selection.items],
                         separators=(",", ":"))
    return hashlib.sha256(payload.encode("utf-8")).hexdigest()
```

- [ ] **Step 4: Run tests to verify they pass**

Run: `python -m pytest tests/test_study_packets.py -q`
Expected: all PASS.

- [ ] **Step 5: Lint and commit**

```bash
python -m ruff check study_packets.py tests/test_study_packets.py
git add study_packets.py tests/test_study_packets.py
git commit -m "Add deterministic packet selection policy"
```

---

### Task 3: Rules & definitions section (extractive)

**Files:**
- Modify: `study_packets.py`
- Test: `tests/test_study_packets.py`

**Interfaces:**
- Consumes: `EntrySelection`, `SelectionItem`.
- Produces:
  - `MAX_RULES_CHUNKS = 40`
  - `locator_line(metadata: dict) -> str` — e.g.
    `book.pdf · pp. 12-13 · Chapter 3 > Conflicts`
  - `build_rules_section(selection: EntrySelection) ->
    tuple[str, int, tuple[str, ...]]` returning (markdown, table_count,
    notices). Markdown is `## Rules and definitions` with one blockquote per
    included chunk; `table` chunks are included verbatim (their text is
    already pipe-table Markdown) and counted in `table_count`. Table-child
    rows (`metadata["retrieval_role"] == "table_child"`) are skipped in
    favor of their parent text appearing once. Truncation past
    `MAX_RULES_CHUNKS` adds a notice. Zero eligible chunks yields the
    section header plus the line `_No statutory or table material in this
    selection._` and no notice.

- [ ] **Step 1: Write the failing tests** (append)

```python
def test_locator_line_composes_available_fields():
    line = study_packets.locator_line({
        "source_file": "book.pdf", "page_range": "12-13",
        "section_path": "Chapter 3 > Conflicts"})
    assert line == "book.pdf · pp. 12-13 · Chapter 3 > Conflicts"
    assert study_packets.locator_line({}) == "unknown source"


def test_build_rules_section_extracts_statutes_and_tables_verbatim():
    selection = study_packets.EntrySelection(items=(
        _item("chunk_s", text="Rule 1.7(a): a lawyer shall not...",
              content_type="statutory_excerpt"),
        _item("chunk_n", text="narrative", content_type="author_narrative"),
        _item("chunk_t", text="| a | b |\n|---|---|\n| 1 | 2 |",
              content_type="table"),
    ), notices=())
    markdown, table_count, notices = study_packets.build_rules_section(
        selection)
    assert markdown.startswith("## Rules and definitions")
    assert "Rule 1.7(a)" in markdown
    assert "| a | b |" in markdown
    assert "narrative" not in markdown
    assert table_count == 1
    assert notices == ()


def test_build_rules_section_skips_table_children_and_caps():
    items = [_item(f"chunk_{i}", text=f"Rule {i}",
                   content_type="statutory_excerpt")
             for i in range(study_packets.MAX_RULES_CHUNKS + 2)]
    items.append(_item("chunk_child", text="row", content_type="table",
                       retrieval_role="table_child"))
    selection = study_packets.EntrySelection(items=tuple(items), notices=())
    markdown, table_count, notices = study_packets.build_rules_section(
        selection)
    assert "chunk_child" not in markdown and "row" not in markdown
    assert table_count == 0
    assert any("truncated" in n for n in notices)


def test_build_rules_section_empty_placeholder():
    selection = study_packets.EntrySelection(
        items=(_item("chunk_n", text="n", content_type="author_narrative"),),
        notices=())
    markdown, table_count, notices = study_packets.build_rules_section(
        selection)
    assert "_No statutory or table material in this selection._" in markdown
    assert table_count == 0 and notices == ()
```

- [ ] **Step 2: Run tests to verify they fail**

Run: `python -m pytest tests/test_study_packets.py -q -k rules`
Expected: FAIL with `AttributeError`.

- [ ] **Step 3: Implement** (append)

```python
MAX_RULES_CHUNKS = 40


def locator_line(metadata: dict) -> str:
    """Human-readable source locator for one chunk."""
    parts: list[str] = []
    source = metadata.get("source_file")
    if isinstance(source, str) and source:
        parts.append(source)
    pages = metadata.get("page_range")
    if isinstance(pages, (str, int)) and str(pages):
        parts.append(f"pp. {pages}")
    section = metadata.get("section_path")
    if isinstance(section, str) and section:
        parts.append(section)
    return " · ".join(parts) if parts else "unknown source"


def _blockquote(text: str) -> str:
    return "\n".join(f"> {line}" if line else ">"
                     for line in text.strip().splitlines())


def build_rules_section(selection: EntrySelection,
                        ) -> tuple[str, int, tuple[str, ...]]:
    """Render the extractive rules-and-definitions section."""
    notices: list[str] = []
    eligible = [
        item for item in selection.items
        if item.metadata.get("content_type") in {"statutory_excerpt", "table"}
        and item.metadata.get("retrieval_role") != "table_child"
    ]
    if len(eligible) > MAX_RULES_CHUNKS:
        notices.append(
            f"rules section truncated to {MAX_RULES_CHUNKS} of "
            f"{len(eligible)} eligible chunks")
        eligible = eligible[:MAX_RULES_CHUNKS]
    lines = ["## Rules and definitions", ""]
    table_count = 0
    if not eligible:
        lines.append("_No statutory or table material in this selection._")
        lines.append("")
    for item in eligible:
        if item.metadata.get("content_type") == "table":
            table_count += 1
            lines.append(item.text.strip())
        else:
            lines.append(_blockquote(item.text))
        lines.append(f"— {locator_line(item.metadata)}")
        lines.append("")
    return "\n".join(lines).rstrip() + "\n", table_count, tuple(notices)
```

- [ ] **Step 4: Run tests to verify they pass**

Run: `python -m pytest tests/test_study_packets.py -q`
Expected: all PASS.

- [ ] **Step 5: Lint and commit**

```bash
python -m ruff check study_packets.py tests/test_study_packets.py
git add study_packets.py tests/test_study_packets.py
git commit -m "Add extractive rules-and-definitions section"
```

---

### Task 4: LLM output contracts for digests and outlines

**Files:**
- Modify: `study_packets.py`
- Test: `tests/test_study_packets.py`

**Interfaces:**
- Consumes: `llm_output_contracts.ExactJSONContract` and
  `OutputContractRejected` (llm_output_contracts.py:108,322).
- Produces:
  - `CASE_DIGEST_CONTRACT_ID = "study-packet-case-digest-v1"`
  - `CASE_DIGEST_FALLBACK_ID = "cited-verbatim-excerpt"`
  - `OUTLINE_CONTRACT_ID = "study-packet-outline-v1"`
  - `OUTLINE_FALLBACK_ID = "omit-outline-section"`
  - `CASE_DIGEST_MAX_FIELD_CHARACTERS = 700`
  - `OUTLINE_MAX_LINES = 80`, `OUTLINE_MAX_LINE_CHARACTERS = 300`
  - `case_digest_contract() -> _contracts.ExactJSONContract` — canonical
    value is a dict with exactly the string keys `case_name`, `facts`,
    `holding`, `significance`, each non-empty and at most
    `CASE_DIGEST_MAX_FIELD_CHARACTERS` characters.
  - `outline_contract(allowed_ids: Collection[str]) ->
    _contracts.ExactJSONContract` — canonical value is
    `{"outline": [{"text": str, "citations": [str, ...]}, ...]}` with 1..
    `OUTLINE_MAX_LINES` lines, each text non-empty and bounded, each line
    carrying at least one citation, and every citation a member of
    `allowed_ids`.

- [ ] **Step 1: Write the failing tests** (append)

```python
def test_case_digest_contract_accepts_exact_payload():
    contract = study_packets.case_digest_contract()
    payload = json.dumps({
        "case_name": "In re Example", "facts": "F", "holding": "H",
        "significance": "S"})
    canonical = contract(payload)
    parsed = json.loads(canonical)
    assert parsed["case_name"] == "In re Example"
    provenance = contract.provenance(
        fallback_id=study_packets.CASE_DIGEST_FALLBACK_ID)
    assert provenance["contract_id"] == study_packets.CASE_DIGEST_CONTRACT_ID


@pytest.mark.parametrize("payload", [
    {"case_name": "C", "facts": "F", "holding": "H"},
    {"case_name": "C", "facts": "F", "holding": "H", "significance": "S",
     "extra": "x"},
    {"case_name": "", "facts": "F", "holding": "H", "significance": "S"},
    {"case_name": "C", "facts": 7, "holding": "H", "significance": "S"},
    {"case_name": "C", "facts": "x" * 701, "holding": "H",
     "significance": "S"},
])
def test_case_digest_contract_rejects_bad_payloads(payload):
    contract = study_packets.case_digest_contract()
    import llm_output_contracts
    with pytest.raises(llm_output_contracts.OutputContractRejected):
        contract(json.dumps(payload))


def test_outline_contract_enforces_citation_universe():
    contract = study_packets.outline_contract({"chunk_a", "chunk_b"})
    good = json.dumps({"outline": [
        {"text": "Issue: conflict?", "citations": ["chunk_a"]},
        {"text": "Rule 1.7 elements", "citations": ["chunk_a", "chunk_b"]},
    ]})
    assert json.loads(contract(good))["outline"][1]["citations"] == [
        "chunk_a", "chunk_b"]
    import llm_output_contracts
    for bad in (
        {"outline": []},
        {"outline": [{"text": "no cite", "citations": []}]},
        {"outline": [{"text": "alien", "citations": ["chunk_zz"]}]},
        {"outline": [{"text": "", "citations": ["chunk_a"]}]},
        {"outline": [{"text": "x", "citations": ["chunk_a"], "extra": 1}]},
    ):
        with pytest.raises(llm_output_contracts.OutputContractRejected):
            contract(json.dumps(bad))
```

- [ ] **Step 2: Run tests to verify they fail**

Run: `python -m pytest tests/test_study_packets.py -q -k contract`
Expected: FAIL with `AttributeError`.

- [ ] **Step 3: Implement contracts** (append). First read
  `llm_output_contracts.py:322-420` to confirm the `ExactJSONContract`
  constructor argument names and how `schema_validator` errors are surfaced
  (raise `ValueError` from the validator; the contract wraps it into
  `OutputContractRejected`). Then:

```python
CASE_DIGEST_CONTRACT_ID = "study-packet-case-digest-v1"
CASE_DIGEST_FALLBACK_ID = "cited-verbatim-excerpt"
OUTLINE_CONTRACT_ID = "study-packet-outline-v1"
OUTLINE_FALLBACK_ID = "omit-outline-section"
CASE_DIGEST_MAX_FIELD_CHARACTERS = 700
OUTLINE_MAX_LINES = 80
OUTLINE_MAX_LINE_CHARACTERS = 300
_CASE_DIGEST_KEYS = ("case_name", "facts", "holding", "significance")
_CASE_DIGEST_MAX_BYTES = 8 * 1024
_OUTLINE_MAX_BYTES = 64 * 1024


def _validated_case_digest(value: Any) -> dict:
    if not isinstance(value, dict) or set(value) != set(_CASE_DIGEST_KEYS):
        raise ValueError("case digest must have exactly the four fields")
    for key in _CASE_DIGEST_KEYS:
        text = value[key]
        if not isinstance(text, str) or not text.strip():
            raise ValueError(f"case digest field {key} must be text")
        if len(text) > CASE_DIGEST_MAX_FIELD_CHARACTERS:
            raise ValueError(f"case digest field {key} is too long")
    return {key: value[key] for key in _CASE_DIGEST_KEYS}


def case_digest_contract() -> _contracts.ExactJSONContract:
    """Contract for one distilled case digest."""
    return _contracts.ExactJSONContract(
        contract_id=CASE_DIGEST_CONTRACT_ID,
        max_bytes=_CASE_DIGEST_MAX_BYTES,
        max_depth=4,
        schema_validator=_validated_case_digest,
    )


def outline_contract(allowed_ids: Collection[str],
                     ) -> _contracts.ExactJSONContract:
    """Contract for the grounded issue outline of one entry."""
    universe = frozenset(allowed_ids)

    def _validated_outline(value: Any) -> dict:
        if not isinstance(value, dict) or set(value) != {"outline"}:
            raise ValueError("outline payload must have exactly 'outline'")
        lines = value["outline"]
        if (not isinstance(lines, list) or not lines
                or len(lines) > OUTLINE_MAX_LINES):
            raise ValueError("outline must have 1..%d lines"
                             % OUTLINE_MAX_LINES)
        canonical: list[dict] = []
        for line in lines:
            if not isinstance(line, dict) or set(line) != {
                    "text", "citations"}:
                raise ValueError("outline lines need exactly text/citations")
            text = line["text"]
            if (not isinstance(text, str) or not text.strip()
                    or len(text) > OUTLINE_MAX_LINE_CHARACTERS):
                raise ValueError("outline line text is empty or too long")
            citations = line["citations"]
            if (not isinstance(citations, list) or not citations
                    or any(not isinstance(c, str) for c in citations)):
                raise ValueError("every outline line needs citations")
            unknown = [c for c in citations if c not in universe]
            if unknown:
                raise ValueError("outline cites unknown chunk ids")
            canonical.append({"text": text, "citations": list(citations)})
        return {"outline": canonical}

    return _contracts.ExactJSONContract(
        contract_id=OUTLINE_CONTRACT_ID,
        max_bytes=_OUTLINE_MAX_BYTES,
        max_depth=6,
        schema_validator=_validated_outline,
    )
```

If the `ExactJSONContract` constructor rejects these arguments (e.g., a
required `provenance_fields`), adapt ONLY the constructor call to the real
signature at llm_output_contracts.py:322 — do not change the validators or
the test expectations.

- [ ] **Step 4: Run tests to verify they pass**

Run: `python -m pytest tests/test_study_packets.py -q`
Expected: all PASS.

- [ ] **Step 5: Lint and commit**

```bash
python -m ruff check study_packets.py tests/test_study_packets.py
git add study_packets.py tests/test_study_packets.py
git commit -m "Add study-packet LLM output contracts"
```

---

### Task 5: Cases digest and issue outline builders

**Files:**
- Modify: `study_packets.py`
- Test: `tests/test_study_packets.py`

**Interfaces:**
- Consumes: Tasks 2–4 symbols.
- Produces:
  - `MAX_CASE_GROUPS = 12`
  - `CASE_SOURCE_CHARACTER_LIMIT = 2000`, `OUTLINE_SOURCE_CHARACTER_LIMIT
    = 1200`, `OUTLINE_MAX_SOURCES = 60`
  - `LLMFn = Callable[[str, object, str], Optional[str]]` — the injected
    callable `(prompt, contract, operation) -> raw canonical JSON text or
    None`. The contract instance is passed so the caller can forward it to
    `_call_llm` as `output_validator`.
  - `@dataclass(frozen=True) CaseGroup(case_name: str,
    items: tuple[SelectionItem, ...])`
  - `case_groups(selection: EntrySelection) ->
    tuple[tuple[CaseGroup, ...], tuple[str, ...]]`
  - `case_digest_prompt(group: CaseGroup) -> str` — bounded one-physical-line
    JSON evidence framing (mirror the SOURCE_JSON style at rag.py:6002-6029).
  - `outline_prompt(selection: EntrySelection) -> str`
  - `build_cases_section(groups: tuple[CaseGroup, ...], llm_fn: LLMFn) ->
    tuple[str, tuple[str, ...]]` — one LLM call per group, one retry, then
    a cited verbatim-excerpt fallback with a visible notice.
  - `build_outline_section(selection: EntrySelection, llm_fn: LLMFn) ->
    tuple[Optional[str], tuple[str, ...]]` — one call, one retry, else
    `(None, notices)`.

- [ ] **Step 1: Write the failing tests** (append)

```python
def test_case_groups_groups_by_primary_case_and_caps():
    items = [
        _item("chunk_1", content_type="case_opinion",
              primary_case="A v. B"),
        _item("chunk_2", content_type="case_opinion",
              primary_case="A v. B"),
        _item("chunk_3", content_type="case_opinion",
              primary_case="C v. D"),
        _item("chunk_4", content_type="author_narrative"),
    ]
    groups, notices = study_packets.case_groups(
        study_packets.EntrySelection(items=tuple(items), notices=()))
    assert [g.case_name for g in groups] == ["A v. B", "C v. D"]
    assert [i.stable_id for i in groups[0].items] == ["chunk_1", "chunk_2"]
    assert notices == ()


def test_case_groups_truncates_with_notice():
    items = tuple(
        _item(f"chunk_{i}", content_type="case_opinion",
              primary_case=f"Case {i}")
        for i in range(study_packets.MAX_CASE_GROUPS + 3))
    groups, notices = study_packets.case_groups(
        study_packets.EntrySelection(items=items, notices=()))
    assert len(groups) == study_packets.MAX_CASE_GROUPS
    assert any("truncated" in n for n in notices)


def test_case_digest_prompt_is_bounded_single_line_json():
    group = study_packets.CaseGroup(case_name="A v. B", items=(
        _item("chunk_1", text="x" * 5000, content_type="case_opinion",
              primary_case="A v. B"),))
    prompt = study_packets.case_digest_prompt(group)
    marker = "SOURCE_JSON (one physical line; bounded case excerpts):"
    assert marker in prompt
    payload_line = prompt.split(marker)[1].strip().splitlines()[0]
    payload = json.loads(payload_line)
    assert payload["case_name"] == "A v. B"
    assert len(payload["sources"][0]["text"]) <= (
        study_packets.CASE_SOURCE_CHARACTER_LIMIT)


def test_build_cases_section_uses_llm_and_falls_back():
    group_ok = study_packets.CaseGroup(case_name="A v. B", items=(
        _item("chunk_1", content_type="case_opinion",
              primary_case="A v. B"),))
    group_bad = study_packets.CaseGroup(case_name="C v. D", items=(
        _item("chunk_2", text="verbatim opinion text",
              content_type="case_opinion", primary_case="C v. D"),))
    calls = []

    def llm_fn(prompt, contract, operation):
        calls.append(operation)
        if "A v. B" in prompt:
            return json.dumps({"case_name": "A v. B", "facts": "F",
                               "holding": "H", "significance": "S"})
        return None

    markdown, notices = study_packets.build_cases_section(
        (group_ok, group_bad), llm_fn)
    assert "### A v. B" in markdown and "**Holding.** H" in markdown
    assert "verbatim opinion text" in markdown
    assert any("digest unavailable" in n for n in notices)
    assert "digest unavailable" in markdown
    assert calls.count("study_packet_case_digest") >= 2


def test_build_cases_section_retries_once_then_falls_back():
    group = study_packets.CaseGroup(case_name="A v. B", items=(
        _item("chunk_1", content_type="case_opinion",
              primary_case="A v. B"),))
    attempts = []

    def llm_fn(prompt, contract, operation):
        attempts.append(1)
        return None

    markdown, notices = study_packets.build_cases_section((group,), llm_fn)
    assert len(attempts) == 2
    assert any("digest unavailable" in n for n in notices)


def test_build_outline_section_grounded_and_omission():
    selection = study_packets.EntrySelection(items=(
        _item("chunk_a"), _item("chunk_b")), notices=())

    def good_llm(prompt, contract, operation):
        assert operation == "study_packet_outline"
        return json.dumps({"outline": [
            {"text": "Issue one", "citations": ["chunk_a"]}]})

    markdown, notices = study_packets.build_outline_section(
        selection, good_llm)
    assert markdown is not None and "Issue one" in markdown
    assert "chunk_a" not in markdown  # rendered as locators, not raw ids
    assert notices == ()

    markdown, notices = study_packets.build_outline_section(
        selection, lambda p, c, o: None)
    assert markdown is None
    assert any("outline omitted" in n for n in notices)
```

- [ ] **Step 2: Run tests to verify they fail**

Run: `python -m pytest tests/test_study_packets.py -q -k "case_group or prompt or cases_section or outline_section"`
Expected: FAIL with `AttributeError`.

- [ ] **Step 3: Implement** (append)

```python
MAX_CASE_GROUPS = 12
CASE_SOURCE_CHARACTER_LIMIT = 2000
OUTLINE_SOURCE_CHARACTER_LIMIT = 1200
OUTLINE_MAX_SOURCES = 60
LLMFn = Callable[[str, object, str], Optional[str]]

_CASE_DIGEST_PROMPT = """You distill judicial opinions from a law-school \
casebook into exam-ready digests.

Reply with exactly one JSON object and no other text:
{{"case_name": "...", "facts": "...", "holding": "...", \
"significance": "..."}}
Every field is plain ASCII text of at most {max_field} characters grounded \
ONLY in the sources below. Do not invent parties, procedural posture, or \
outcomes.

SOURCE_JSON (one physical line; bounded case excerpts):
{source_json}"""

_OUTLINE_PROMPT = """You build a grounded exam-issue outline for one topic \
from a law-school casebook.

Reply with exactly one JSON object and no other text:
{{"outline": [{{"text": "...", "citations": ["chunk id", "..."]}}, ...]}}
Between 1 and {max_lines} lines, each at most {max_chars} characters, in a \
sensible exam-analysis order. Every line MUST cite at least one chunk id \
from the sources below, and only those ids. Do not invent law that the \
sources do not contain.

SOURCE_JSON (one physical line; bounded topic sources):
{source_json}"""


def case_groups(selection: EntrySelection,
                ) -> tuple[tuple["CaseGroup", ...], tuple[str, ...]]:
    """Group the selection's case_opinion chunks by case identity."""
    notices: list[str] = []
    grouped: dict[str, list[SelectionItem]] = {}
    order: list[str] = []
    for item in selection.items:
        if item.metadata.get("content_type") != "case_opinion":
            continue
        name = item.metadata.get("primary_case")
        if not isinstance(name, str) or not name:
            case_names = item.metadata.get("case_names")
            if isinstance(case_names, (list, tuple)) and case_names:
                name = str(case_names[0])
            else:
                name = str(item.metadata.get("section_path", "Unnamed case"))
        if name not in grouped:
            grouped[name] = []
            order.append(name)
        grouped[name].append(item)
    if len(order) > MAX_CASE_GROUPS:
        notices.append(
            f"cases section truncated to {MAX_CASE_GROUPS} of "
            f"{len(order)} case groups")
        order = order[:MAX_CASE_GROUPS]
    groups = tuple(CaseGroup(case_name=name, items=tuple(grouped[name]))
                   for name in order)
    return groups, tuple(notices)


@dataclass(frozen=True)
class CaseGroup:
    case_name: str
    items: tuple[SelectionItem, ...]


def _bounded_sources(items: tuple[SelectionItem, ...], *, limit: int,
                     max_sources: int) -> list[dict]:
    return [
        {"stable_id": item.stable_id, "locator": locator_line(item.metadata),
         "text": item.text[:limit]}
        for item in items[:max_sources]
    ]


def case_digest_prompt(group: CaseGroup) -> str:
    """One-physical-line bounded JSON evidence prompt for one case."""
    source_json = json.dumps(
        {"case_name": group.case_name,
         "sources": _bounded_sources(
             group.items, limit=CASE_SOURCE_CHARACTER_LIMIT,
             max_sources=8)},
        ensure_ascii=True, sort_keys=True, separators=(",", ":"))
    return _CASE_DIGEST_PROMPT.format(
        max_field=CASE_DIGEST_MAX_FIELD_CHARACTERS, source_json=source_json)


def outline_prompt(selection: EntrySelection) -> str:
    """One-physical-line bounded JSON evidence prompt for the outline."""
    source_json = json.dumps(
        {"sources": _bounded_sources(
            selection.items, limit=OUTLINE_SOURCE_CHARACTER_LIMIT,
            max_sources=OUTLINE_MAX_SOURCES)},
        ensure_ascii=True, sort_keys=True, separators=(",", ":"))
    return _OUTLINE_PROMPT.format(
        max_lines=OUTLINE_MAX_LINES, max_chars=OUTLINE_MAX_LINE_CHARACTERS,
        source_json=source_json)


def _call_with_retry(llm_fn: LLMFn, prompt: str, contract: object,
                     operation: str) -> Optional[str]:
    for _attempt in range(2):
        raw = llm_fn(prompt, contract, operation)
        if raw is not None:
            return raw
    return None


def build_cases_section(groups: tuple[CaseGroup, ...], llm_fn: LLMFn,
                        ) -> tuple[str, tuple[str, ...]]:
    """Render the key-cases digest with fail-closed fallbacks."""
    notices: list[str] = []
    lines = ["## Key cases", ""]
    if not groups:
        lines.append("_No case opinions in this selection._")
        lines.append("")
    contract = case_digest_contract()
    for group in groups:
        lines.append(f"### {group.case_name}")
        lines.append("")
        raw = _call_with_retry(
            llm_fn, case_digest_prompt(group), contract,
            "study_packet_case_digest")
        if raw is not None:
            digest = json.loads(raw)
            lines.append(f"**Facts.** {digest['facts']}")
            lines.append("")
            lines.append(f"**Holding.** {digest['holding']}")
            lines.append("")
            lines.append(f"**Why it matters.** {digest['significance']}")
        else:
            notices.append(
                f"case {group.case_name!r}: digest unavailable; verbatim "
                "excerpt included")
            lines.append("_Digest unavailable; verbatim excerpt follows._")
            lines.append("")
            lines.append(_blockquote(
                group.items[0].text[:CASE_SOURCE_CHARACTER_LIMIT]))
        for item in group.items:
            lines.append(f"— {locator_line(item.metadata)}")
        lines.append("")
    return "\n".join(lines).rstrip() + "\n", tuple(notices)


def build_outline_section(selection: EntrySelection, llm_fn: LLMFn,
                          ) -> tuple[Optional[str], tuple[str, ...]]:
    """Render the grounded issue outline or omit it with a notice."""
    contract = outline_contract(
        {item.stable_id for item in selection.items})
    raw = _call_with_retry(
        llm_fn, outline_prompt(selection), contract, "study_packet_outline")
    if raw is None:
        return None, ("outline omitted: the model produced no "
                      "contract-conforming grounded outline",)
    by_id = {item.stable_id: item for item in selection.items}
    lines = ["## Issue outline", ""]
    for index, line in enumerate(json.loads(raw)["outline"], start=1):
        locators = "; ".join(
            locator_line(by_id[cid].metadata) for cid in line["citations"])
        lines.append(f"{index}. {line['text']}  \n   _({locators})_")
    lines.append("")
    return "\n".join(lines).rstrip() + "\n", ()
```

- [ ] **Step 4: Run tests to verify they pass**

Run: `python -m pytest tests/test_study_packets.py -q`
Expected: all PASS.

- [ ] **Step 5: Lint and commit**

```bash
python -m ruff check study_packets.py tests/test_study_packets.py
git add study_packets.py tests/test_study_packets.py
git commit -m "Add case digest and grounded outline builders"
```

---

### Task 6: Packet assembly, headers, and course index rendering

**Files:**
- Modify: `study_packets.py`
- Test: `tests/test_study_packets.py`

**Interfaces:**
- Consumes: all prior tasks.
- Produces:
  - `@dataclass(frozen=True) PacketHeader(course: str, entry_id: str,
    title: str, index_binding: dict, selection_digest: str,
    generated_at: str)` — `generated_at` is caller-supplied ISO-8601 text
    (the module never reads the clock).
  - `@dataclass(frozen=True) EntryPacket(entry_id: str, title: str,
    markdown: str, table_count: int, notices: tuple[str, ...])`
  - `@dataclass(frozen=True) EntryFailure(entry_id: str, title: str,
    reason: str)`
  - `build_entry_packet(course, entry, selection, header, llm_fn) ->
    EntryPacket` — assembles header + three sections + a notices footer.
  - `render_course_index(course: str, packets: tuple[EntryPacket, ...],
    failures: tuple[EntryFailure, ...]) -> str`

- [ ] **Step 1: Write the failing tests** (append)

```python
def _header(**overrides):
    values = {
        "course": "Legal Ethics", "entry_id": "week-03",
        "title": "Conflicts",
        "index_binding": {"backend": "chroma", "collection": "ethics",
                          "source_sha256": "abc123",
                          "embedding_model": "model-a"},
        "selection_digest": "d" * 64,
        "generated_at": "2026-08-01T12:00:00+00:00",
    }
    values.update(overrides)
    return study_packets.PacketHeader(**values)


def _ok_llm(prompt, contract, operation):
    if operation == "study_packet_case_digest":
        return json.dumps({"case_name": "A v. B", "facts": "F",
                           "holding": "H", "significance": "S"})
    return json.dumps({"outline": [
        {"text": "Issue one", "citations": ["chunk_s"]}]})


def test_build_entry_packet_composes_all_sections_and_header():
    selection = study_packets.EntrySelection(items=(
        _item("chunk_s", text="Rule 1.7", content_type="statutory_excerpt"),
        _item("chunk_c", content_type="case_opinion", primary_case="A v. B"),
    ), notices=("query notice",))
    packet = study_packets.build_entry_packet(
        "Legal Ethics", _entry(), selection, _header(), _ok_llm)
    assert packet.entry_id == "week-03"
    assert packet.markdown.startswith("# Conflicts")
    for expected in ("Legal Ethics", "week-03", "abc123", "d" * 64,
                     "## Rules and definitions", "## Key cases",
                     "## Issue outline", "query notice"):
        assert expected in packet.markdown
    assert packet.table_count == 0


def test_build_entry_packet_records_section_notices_in_footer():
    selection = study_packets.EntrySelection(items=(
        _item("chunk_c", text="opinion", content_type="case_opinion",
              primary_case="C v. D"),
    ), notices=())
    packet = study_packets.build_entry_packet(
        "Legal Ethics", _entry(), selection, _header(),
        lambda p, c, o: None)
    assert "digest unavailable" in packet.markdown
    assert "outline omitted" in packet.markdown
    assert any("outline omitted" in n for n in packet.notices)


def test_render_course_index_lists_packets_and_failures():
    packet = study_packets.EntryPacket(
        entry_id="week-03", title="Conflicts", markdown="# x\n",
        table_count=0, notices=())
    failure = study_packets.EntryFailure(
        entry_id="week-04", title="Candor", reason="selector matched nothing")
    index = study_packets.render_course_index(
        "Legal Ethics", (packet,), (failure,))
    assert "# Legal Ethics — study packets" in index
    assert "[Conflicts](week-03.md)" in index
    assert "week-04" in index and "selector matched nothing" in index
```

- [ ] **Step 2: Run tests to verify they fail**

Run: `python -m pytest tests/test_study_packets.py -q -k "packet or index"`
Expected: FAIL with `AttributeError`.

- [ ] **Step 3: Implement** (append)

```python
@dataclass(frozen=True)
class PacketHeader:
    course: str
    entry_id: str
    title: str
    index_binding: dict
    selection_digest: str
    generated_at: str


@dataclass(frozen=True)
class EntryPacket:
    entry_id: str
    title: str
    markdown: str
    table_count: int
    notices: tuple[str, ...]


@dataclass(frozen=True)
class EntryFailure:
    entry_id: str
    title: str
    reason: str


def _header_block(header: PacketHeader) -> str:
    binding = json.dumps(header.index_binding, ensure_ascii=True,
                         sort_keys=True, separators=(",", ":"))
    return "\n".join([
        f"# {header.title}",
        "",
        f"- Course: {header.course}",
        f"- Entry: {header.entry_id}",
        f"- Generated: {header.generated_at}",
        f"- Selection digest: {header.selection_digest}",
        f"- Index binding: `{binding}`",
        "",
    ])


def build_entry_packet(course: str, entry: SyllabusEntry,
                       selection: EntrySelection, header: PacketHeader,
                       llm_fn: LLMFn) -> EntryPacket:
    """Assemble one packet's Markdown from the three sections."""
    notices: list[str] = list(selection.notices)
    rules_markdown, table_count, rules_notices = build_rules_section(
        selection)
    notices.extend(rules_notices)
    groups, group_notices = case_groups(selection)
    notices.extend(group_notices)
    cases_markdown, case_notices = build_cases_section(groups, llm_fn)
    notices.extend(case_notices)
    outline_markdown, outline_notices = build_outline_section(
        selection, llm_fn)
    notices.extend(outline_notices)
    parts = [_header_block(header), rules_markdown, cases_markdown]
    if outline_markdown is not None:
        parts.append(outline_markdown)
    else:
        parts.append("## Issue outline\n\n_Outline omitted; see notices._\n")
    if notices:
        parts.append("## Notices\n\n" + "\n".join(
            f"- {notice}" for notice in notices) + "\n")
    return EntryPacket(entry_id=entry.entry_id, title=entry.title,
                       markdown="\n".join(parts), table_count=table_count,
                       notices=tuple(notices))


def render_course_index(course: str, packets: tuple[EntryPacket, ...],
                        failures: tuple[EntryFailure, ...]) -> str:
    """Render the course index page linking every packet."""
    lines = [f"# {course} — study packets", ""]
    for packet in packets:
        lines.append(f"- [{packet.title}]({packet.entry_id}.md)")
    if failures:
        lines.append("")
        lines.append("## Failed entries")
        lines.append("")
        for failure in failures:
            lines.append(f"- {failure.entry_id} ({failure.title}): "
                         f"{failure.reason}")
    lines.append("")
    return "\n".join(lines)
```

- [ ] **Step 4: Run tests to verify they pass**

Run: `python -m pytest tests/test_study_packets.py -q`
Expected: all PASS.

- [ ] **Step 5: Lint and commit**

```bash
python -m ruff check study_packets.py tests/test_study_packets.py
git add study_packets.py tests/test_study_packets.py
git commit -m "Assemble packet and course-index rendering"
```

---

### Task 7: `rag.py` handler and `packets` CLI command

**Files:**
- Modify: `rag.py` (three places: import block near rag.py:50-62, a new
  `build_study_packets` function next to `generate_briefs` at rag.py:26898,
  the CLI parser + dispatch inside `main`)
- Test: `tests/test_study_packets_cli.py`

**Interfaces:**
- Consumes: everything `study_packets` exports; `rag` collaborators listed
  in Repository orientation.
- Produces: `rag.build_study_packets(syllabus_path: Path, *,
  chunks_path: Path, db_dir: Path, db_backend: str = DEFAULT_DB_BACKEND,
  collection_name: str = DEFAULT_COLLECTION,
  embedding_model: str = DEFAULT_EMBEDDING_MODEL,
  cloud_url: str = DEFAULT_CLOUD_URL, cloud_model: str = DEFAULT_CLOUD_MODEL,
  cloud_key: str = "", ollama_url: str = DEFAULT_OLLAMA_URL,
  ollama_model: str = DEFAULT_OLLAMA_MODEL, gemini_key: str = "",
  llm_workers: int = DEFAULT_LLM_WORKERS, thinking: bool = False,
  security_policy: _release_security.ReleaseSecurityPolicy | None = None,
  ) -> None` — raises `SystemExit(1)` if any entry failed or run-level
  validation failed; `SystemExit(2)` for an invalid syllabus.

Behavioral requirements (each is asserted by a test below):

1. Read the syllabus with `study_packets.load_syllabus_text`; on
   `SyllabusError` print the reason and `raise SystemExit(2)`.
2. Load records via the same strict snapshot loader `generate_briefs` uses
   (`_load_index_snapshot_strict(chunks_path)` — read rag.py:26898-26930
   first and reuse its loading/receipt pattern exactly, including the
   quality-report requirement).
3. Profile check: locate the chunk-stage receipt the snapshot loader
   validates (the assertion block near rag.py:1517-1539 names the field
   `structure_profile`); compare
   `_document_profiles.profile_sha256(_document_profiles.profile_from_provenance(receipt["structure_profile"]))`
   against
   `_document_profiles.profile_sha256(_document_profiles.get_profile(syllabus.structure_profile))`;
   mismatch → print and `raise SystemExit(1)` before building anything.
4. Index binding: `manifest = _index_state._load_index_manifest(db_dir,
   backend=db_backend, collection_name=collection_name,
   manifest_path_fn=_index_state._index_manifest_path,
   warning_fn=lambda message: None)`; `None` → print and
   `raise SystemExit(1)`. The header's `index_binding` dict is exactly
   `{"backend": ..., "collection": ..., "source_sha256":
   manifest.get("source_sha256"), "embedding_model":
   manifest.get("embedding_model")}`.
5. `search_fn` for one query: call `search_index(query, db_dir,
   db_backend=db_backend, n_results=study_packets.MAX_QUERY_HITS_PER_QUERY,
   collection_name=collection_name, embedding_model=embedding_model,
   chunks_path=chunks_path, security_policy=security_policy)` and map each
   hit to `study_packets.SelectionItem(stable_id=hit.metadata["stable_id"],
   text=hit.text, metadata=hit.metadata)`; hits without a `stable_id` are
   skipped with a collected warning.
6. `llm_fn`: forward to `_call_llm(prompt, ollama_url=..., ollama_model=...,
   gemini_key=..., cloud_url=..., cloud_model=..., cloud_key=...,
   llm_workers=..., thinking=..., max_tokens=4096, operation=operation,
   prompt_version="1", timeout=60,
   output_contract_id=contract.contract_id,
   output_fallback_id=(study_packets.CASE_DIGEST_FALLBACK_ID if operation
   == "study_packet_case_digest" else study_packets.OUTLINE_FALLBACK_ID),
   output_validator=contract, security_policy=security_policy)`.
7. Per entry: `select_entry` → `selection_digest` → `PacketHeader`
   (`generated_at` from
   `datetime.datetime.now(datetime.timezone.utc).isoformat(timespec="seconds")`)
   → `build_entry_packet` → validate with
   `_markdown_validation.validate_markdown_candidate(packet.markdown,
   expected_table_count=packet.table_count,
   source_name=f"packets/{entry.entry_id}.md", policy="auto",
   require_table_markers=False)` → on success
   `_artifact_io._atomic_write_text(packets_dir / f"{entry.entry_id}.md",
   packet.markdown)`. `EntrySelectionError` and
   `_markdown_validation.MarkdownValidationError` become
   `study_packets.EntryFailure` records; the loop continues.
8. Packets directory: `chunks_path.parent / "packets"`, created with
   `mkdir(parents=True, exist_ok=True)`.
9. After the loop: write `packets/index.md` via `render_course_index` +
   `_atomic_write_text`; print a one-line summary per entry (built or
   failed+reason); `raise SystemExit(1)` if any failures.
10. CLI: copy the `brief` block pattern (rag.py:30648-30652) —

```python
    # packets
    p_pkt = sub.add_parser(
        "packets", help="Build syllabus-driven study packets")
    p_pkt.add_argument("--syllabus", type=Path, required=True)
    p_pkt.add_argument("--chunks", type=Path, default=DEFAULT_CHUNKS_PATH)
    p_pkt.add_argument("--db", type=Path, default=DEFAULT_CHROMA_DIR)
    add_db_backend_flag(p_pkt)
    add_collection_flag(p_pkt)
    add_embedding_flags(p_pkt)
    add_llm_provider_flags(p_pkt)
    add_release_security_flags(p_pkt)
```

    Dispatch next to the `brief` dispatch (rag.py:31153-31154):

```python
    elif args.command == "packets":
        build_study_packets(
            args.syllabus, chunks_path=args.chunks, db_dir=args.db,
            db_backend=args.db_backend, collection_name=args.collection,
            embedding_model=args.embedding_model, **llm_kwargs)
```

    and add `"packets"` to the `security_policy` defaulting set at
    rag.py:31060 (`if args.command in {"chunk", "query"}:` becomes
    `{"chunk", "query", "packets"}`).

- [ ] **Step 1: Write the failing tests** (`tests/test_study_packets_cli.py`)

```python
"""Handler and CLI wiring tests for rag.py packets."""

import json
from pathlib import Path

import pytest

import rag
import study_packets


def _write_syllabus(tmp_path, entries=None):
    payload = {
        "schema": "study-syllabus-v1",
        "course": "Legal Ethics",
        "structure_profile": "legal-casebook-v1",
        "entries": entries or [
            {"id": "week-03", "title": "Conflicts",
             "chapters": ["Chapter 3"]}],
    }
    path = tmp_path / "syllabus.json"
    path.write_text(json.dumps(payload), encoding="utf-8")
    return path


def test_packets_invalid_syllabus_exits_2(tmp_path, capsys):
    path = tmp_path / "syllabus.json"
    path.write_text("not json", encoding="utf-8")
    with pytest.raises(SystemExit) as excinfo:
        rag.build_study_packets(
            path, chunks_path=tmp_path / "chunks.jsonl",
            db_dir=tmp_path / "db")
    assert excinfo.value.code == 2
    assert "JSON" in capsys.readouterr().out


def test_packets_cli_dispatch_forwards_arguments(monkeypatch, tmp_path):
    captured = {}

    def fake_handler(syllabus_path, **kwargs):
        captured["syllabus"] = syllabus_path
        captured.update(kwargs)

    monkeypatch.setattr(rag, "build_study_packets", fake_handler)
    syllabus = _write_syllabus(tmp_path)
    rag.main(["packets", "--syllabus", str(syllabus),
              "--chunks", str(tmp_path / "chunks.jsonl"),
              "--db", str(tmp_path / "db"),
              "--collection", "ethics"])
    assert captured["syllabus"] == syllabus
    assert captured["chunks_path"] == tmp_path / "chunks.jsonl"
    assert captured["db_dir"] == tmp_path / "db"
    assert captured["collection_name"] == "ethics"


def test_packets_writes_validated_packets_and_index(monkeypatch, tmp_path):
    chunks_path = tmp_path / "run" / "chunks.jsonl"
    chunks_path.parent.mkdir(parents=True)
    records = [
        {"text": "Rule 1.7(a) text", "metadata": {
            "stable_id": "chunk_s", "content_type": "statutory_excerpt",
            "chapter_title": "Chapter 3", "chapter_num": 3,
            "section_path": "Chapter 3 > Rules", "page_range": "12",
            "source_file": "book.pdf"}},
        {"text": "Opinion text", "metadata": {
            "stable_id": "chunk_c", "content_type": "case_opinion",
            "primary_case": "A v. B", "chapter_title": "Chapter 3",
            "chapter_num": 3, "section_path": "Chapter 3 > Cases",
            "page_range": "14", "source_file": "book.pdf"}},
    ]
    monkeypatch.setattr(
        rag, "_load_index_snapshot_strict",
        lambda path: (records, {"structure_profile": {
            "schema_version": 1, "name": "legal-casebook-v1",
            "revision": 1, "sha256": "x"}}))
    monkeypatch.setattr(
        rag._document_profiles, "profile_from_provenance",
        lambda receipt: "profile-object")
    monkeypatch.setattr(
        rag._document_profiles, "get_profile", lambda name: "profile-object")
    monkeypatch.setattr(
        rag._document_profiles, "profile_sha256", lambda profile: "same")
    monkeypatch.setattr(
        rag._index_state, "_load_index_manifest",
        lambda db_dir, **kwargs: {
            "backend": "chroma", "collection": "ethics",
            "source_sha256": "abc", "embedding_model": "model-a"})

    def fake_call_llm(prompt, **kwargs):
        contract = kwargs["output_validator"]
        if kwargs["operation"] == "study_packet_case_digest":
            return contract(json.dumps({
                "case_name": "A v. B", "facts": "F", "holding": "H",
                "significance": "S"}))
        return contract(json.dumps({"outline": [
            {"text": "Issue", "citations": ["chunk_s"]}]}))

    monkeypatch.setattr(rag, "_call_llm", fake_call_llm)
    monkeypatch.setattr(
        rag._markdown_validation, "validate_markdown_candidate",
        lambda markdown, **kwargs: {"publishable": True})

    rag.build_study_packets(
        _write_syllabus(tmp_path), chunks_path=chunks_path,
        db_dir=tmp_path / "db", collection_name="ethics")

    packet_path = chunks_path.parent / "packets" / "week-03.md"
    index_path = chunks_path.parent / "packets" / "index.md"
    assert packet_path.is_file() and index_path.is_file()
    packet = packet_path.read_text(encoding="utf-8")
    assert "## Rules and definitions" in packet
    assert "A v. B" in packet
    assert "[Conflicts](week-03.md)" in index_path.read_text(
        encoding="utf-8")


def test_packets_entry_failure_isolated_and_exit_1(monkeypatch, tmp_path):
    chunks_path = tmp_path / "run" / "chunks.jsonl"
    chunks_path.parent.mkdir(parents=True)
    records = [{"text": "Rule", "metadata": {
        "stable_id": "chunk_s", "content_type": "statutory_excerpt",
        "chapter_title": "Chapter 3", "chapter_num": 3,
        "section_path": "Chapter 3 > Rules", "page_range": "12",
        "source_file": "book.pdf"}}]
    monkeypatch.setattr(
        rag, "_load_index_snapshot_strict",
        lambda path: (records, {"structure_profile": {}}))
    monkeypatch.setattr(
        rag._document_profiles, "profile_from_provenance",
        lambda receipt: "p")
    monkeypatch.setattr(
        rag._document_profiles, "get_profile", lambda name: "p")
    monkeypatch.setattr(
        rag._document_profiles, "profile_sha256", lambda profile: "same")
    monkeypatch.setattr(
        rag._index_state, "_load_index_manifest",
        lambda db_dir, **kwargs: {"backend": "chroma",
                                  "collection": "ethics",
                                  "source_sha256": "abc",
                                  "embedding_model": "m"})
    monkeypatch.setattr(rag, "_call_llm", lambda prompt, **kwargs: None)
    monkeypatch.setattr(
        rag._markdown_validation, "validate_markdown_candidate",
        lambda markdown, **kwargs: {"publishable": True})

    syllabus = _write_syllabus(tmp_path, entries=[
        {"id": "good", "title": "Good", "chapters": ["Chapter 3"]},
        {"id": "bad", "title": "Bad", "chapters": ["Nowhere"]},
    ])
    with pytest.raises(SystemExit) as excinfo:
        rag.build_study_packets(
            syllabus, chunks_path=chunks_path, db_dir=tmp_path / "db")
    assert excinfo.value.code == 1
    packets_dir = chunks_path.parent / "packets"
    assert (packets_dir / "good.md").is_file()
    assert not (packets_dir / "bad.md").exists()
    index_text = (packets_dir / "index.md").read_text(encoding="utf-8")
    assert "bad" in index_text and "selector" in index_text
```

Also add this atomic-write failure-injection test (spec requirement) to
`tests/test_study_packets_cli.py`, reusing the monkeypatch stack from
`test_packets_writes_validated_packets_and_index` verbatim except for the
write seam:

```python
def test_packets_atomic_write_failure_is_entry_isolated(
        monkeypatch, tmp_path):
    # ... same monkeypatch stack as
    # test_packets_writes_validated_packets_and_index, then:
    real_write = rag._artifact_io._atomic_write_text
    def failing_write(path, content, **kwargs):
        if path.name == "week-03.md":
            raise OSError("disk full")
        return real_write(path, content, **kwargs)
    monkeypatch.setattr(
        rag._artifact_io, "_atomic_write_text", failing_write)
    with pytest.raises(SystemExit) as excinfo:
        rag.build_study_packets(
            _write_syllabus(tmp_path), chunks_path=chunks_path,
            db_dir=tmp_path / "db", collection_name="ethics")
    assert excinfo.value.code == 1
    index_text = (chunks_path.parent / "packets" / "index.md").read_text(
        encoding="utf-8")
    assert "disk full" in index_text
```

NOTE: the exact return shape of `_load_index_snapshot_strict` must be
checked against rag.py:26914 before writing the handler — if it returns
records only (no receipt tuple), obtain the receipt through the loader's
own receipt helper and adjust the two monkeypatched fakes to match the real
seam. The tests above then patch whatever pair of seams the handler
actually calls; keep the behavioral assertions identical.

- [ ] **Step 2: Run tests to verify they fail**

Run: `python -m pytest tests/test_study_packets_cli.py -q`
Expected: FAIL with `AttributeError: ... build_study_packets`.

- [ ] **Step 3: Implement the handler and CLI wiring in `rag.py`**

Add `import study_packets as _study_packets` to the first-party import
block (alphabetical position, near `import source_fidelity_core` /
`import table_retrieval_core`). Implement `build_study_packets` directly
below `generate_briefs` (rag.py:26898), following behavioral requirements
1-9 above. Skeleton:

```python
def build_study_packets(syllabus_path: Path, *, chunks_path: Path,
                        db_dir: Path,
                        db_backend: str = DEFAULT_DB_BACKEND,
                        collection_name: str = DEFAULT_COLLECTION,
                        embedding_model: str = DEFAULT_EMBEDDING_MODEL,
                        cloud_url: str = DEFAULT_CLOUD_URL,
                        cloud_model: str = DEFAULT_CLOUD_MODEL,
                        cloud_key: str = "",
                        ollama_url: str = DEFAULT_OLLAMA_URL,
                        ollama_model: str = DEFAULT_OLLAMA_MODEL,
                        gemini_key: str = "",
                        llm_workers: int = DEFAULT_LLM_WORKERS,
                        thinking: bool = False,
                        security_policy: (
                            _release_security.ReleaseSecurityPolicy | None
                        ) = None) -> None:
    """Build one validated Markdown study packet per syllabus entry."""
    try:
        syllabus = _study_packets.load_syllabus_text(
            syllabus_path.read_text(encoding="utf-8"))
    except (OSError, _study_packets.SyllabusError) as exc:
        print(f"Invalid syllabus: {exc}")
        raise SystemExit(2)
    # requirements 2-4: strict snapshot + receipt, profile check, manifest
    # requirement 5-6: search_fn and llm_fn closures
    # requirement 7-9: entry loop, validation, atomic writes, index, summary
```

The entry loop shape:

```python
    packets: list[_study_packets.EntryPacket] = []
    failures: list[_study_packets.EntryFailure] = []
    packets_dir = chunks_path.parent / "packets"
    packets_dir.mkdir(parents=True, exist_ok=True)
    for entry in syllabus.entries:
        try:
            selection = _study_packets.select_entry(
                entry, records, search_fn=search_fn)
            header = _study_packets.PacketHeader(
                course=syllabus.course, entry_id=entry.entry_id,
                title=entry.title, index_binding=index_binding,
                selection_digest=_study_packets.selection_digest(selection),
                generated_at=generated_at)
            packet = _study_packets.build_entry_packet(
                syllabus.course, entry, selection, header, llm_fn)
            _markdown_validation.validate_markdown_candidate(
                packet.markdown, expected_table_count=packet.table_count,
                source_name=f"packets/{entry.entry_id}.md", policy="auto",
                require_table_markers=False)
            _artifact_io._atomic_write_text(
                packets_dir / f"{entry.entry_id}.md", packet.markdown)
            packets.append(packet)
            print(f"built {entry.entry_id}")
        except (_study_packets.EntrySelectionError,
                _markdown_validation.MarkdownValidationError,
                OSError) as exc:
            failures.append(_study_packets.EntryFailure(
                entry_id=entry.entry_id, title=entry.title,
                reason=str(exc)))
            print(f"failed {entry.entry_id}: {exc}")
    _artifact_io._atomic_write_text(
        packets_dir / "index.md",
        _study_packets.render_course_index(
            syllabus.course, tuple(packets), tuple(failures)))
    if failures:
        raise SystemExit(1)
```

Then add the parser block, dispatch, and the `security_policy` set
membership exactly as shown in the Interfaces block above.

- [ ] **Step 4: Run tests to verify they pass**

Run: `python -m pytest tests/test_study_packets_cli.py tests/test_study_packets.py -q`
Expected: all PASS.

- [ ] **Step 5: Lint and commit**

```bash
python -m ruff check rag.py tests/test_study_packets_cli.py
git add rag.py tests/test_study_packets_cli.py
git commit -m "Wire the packets CLI command to the packet policy"
```

---

### Task 8: Full-suite integration and quality gates

**Files:**
- Modify: `architecture-inventory.json` (tool-generated ONLY)
- Possibly modify: `tests/test_output_publication.py` and
  `tests/test_scaffold_structure.py` (only if their gates flag the new
  module/command — see below)

- [ ] **Step 1: Run the full suite**

Run: `python -m pytest -q`
Expected: some failures are ANTICIPATED and legitimate:
- `tests/test_architecture_inventory.py::test_repository_baseline_is_current_and_canonical`
  — the inventory hashes every tracked Python source; fix with
  `python tools/check_architecture_inventory.py --refresh` (never by hand).
- `tests/test_output_publication.py` has a parametrized list of downstream
  consumers requiring a quality report (entry `"generate_briefs"` at
  :793). If the suite demands new entry points appear there, add
  `"build_study_packets"` following the existing parametrization exactly.
- Structure/architecture tests may pin module counts or import graphs; fix
  by following each failure message's own instruction (these gates print
  what they expect). Do NOT weaken a gate to pass it; extend its expected
  list with the new module/command only.

Any OTHER failure means a behavior regression — fix the new code, not the
existing test.

- [ ] **Step 2: Refresh the inventory and re-run gates**

```bash
python tools/check_architecture_inventory.py --refresh
python tools/check_architecture_inventory.py
python -m pytest -q
python -m ruff check .
python tools/check_python_sources.py
python tools/check_dependency_policy.py
python tools/check_model_artifacts.py
```

Expected: everything green; pytest reports 0 failures (skips for absent
optional dependencies are fine). Record the observed pass count.

- [ ] **Step 3: Offline evaluation suites (regression guard)**

```bash
python eval.py --retriever bm25 --queries evaluation/suites/property/queries.jsonl --chunks evaluation/suites/property/chunks.jsonl --k 1 3 5 --depth 10
python eval.py --retriever bm25 --queries evaluation/suites/constitutional_law/queries.jsonl --chunks evaluation/suites/constitutional_law/chunks.jsonl --k 1 3 5 --depth 10
python eval.py --retriever bm25 --queries evaluation/suites/table_family/queries.jsonl --chunks evaluation/suites/table_family/chunks.jsonl --k 1 3 5 --depth 10
```

Expected: all three complete without threshold failures (packets code must
not perturb retrieval behavior at all).

- [ ] **Step 4: Final commit**

```bash
git add -A
git commit -m "Integrate study packets with repository gates

python -m pytest -q: <observed counts>; ruff, compile, dependency,
model-artifact, and architecture-inventory gates green; offline
evaluation suites unchanged."
```

- [ ] **Step 5: Hand off**

Push the branch and report: branch name, final commit SHA, observed test
counts, and any gate whose expected list was extended (with the exact
diff). Do NOT open a candidate PR, regenerate benchmarks, or edit
ROADMAP.md — the owner's machine performs the evidence and candidate
ritual.

---

## Deviations and escalation

- If a documented interface differs from the line references above (the
  file drifts), trust the code, keep the behavioral requirement, and note
  the difference in the final report.
- If `_load_index_snapshot_strict`'s return shape or receipt access does
  not match Task 7's assumption, adapt the handler and the two test fakes
  to the real seam — the behavioral assertions must not weaken.
- If `ExactJSONContract` cannot express a validator (unexpected constructor
  contract), stop and report rather than hand-rolling JSON parsing outside
  `llm_output_contracts`.
- Never commit with a failing gate "to be fixed later".
