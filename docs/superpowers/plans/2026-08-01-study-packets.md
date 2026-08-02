# Syllabus-Driven Study Packets Implementation Plan

> **For agentic workers:** Execute this plan task-by-task on
> `agent/study-packets-implementation`. Use the repository's agentic-plan
> skill when available; otherwise use an equivalent test-first, reviewed
> subagent workflow. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Build `study_packets.py` (deterministic packet policy) plus a
`rag.py packets` subcommand that turns a `study-syllabus-v1` JSON file and an
indexed casebook into one validated Markdown study packet per syllabus entry
plus a course index, per
`docs/superpowers/specs/2026-08-01-study-packets-design.md`.

**Architecture:** All policy (syllabus schema, selection, section assembly,
LLM output contracts, rendering) lives in a new dependency-light typed module
`study_packets.py` with injected callables for retrieval and LLM calls.
`rag.py` gains a thin handler `build_study_packets(...)` wiring existing
collaborators through a new lease-held strict packet-snapshot facade,
`search_index`, `_call_llm`,
`markdown_validation.validate_markdown_candidate`,
the repository output-lease/atomic-write primitives, and
`index_state._load_index_manifest`.
No behavior of existing commands changes.

**Tech Stack:** Python 3.10–3.14 standard library only in `study_packets.py`
(plus first-party `llm_output_contracts`); pytest; the repository's existing
retrieval/LLM/validation modules via injection.

## Audit-hardening contract (binding)

The completed gate/interface/safety audit found requirements that the
original illustrative snippets did not fully encode. This section is binding
and supersedes any older line reference or snippet below. When a snippet is
retained as a coding sketch, extend or reshape it to satisfy every item here;
never copy a conflicting shortcut.

1. **Bounded syllabus boundary.** Read at most the declared syllabus byte
   limit, then parse JSON with duplicate-key and non-finite-number rejection.
   Enforce explicit bounds for entries, selector/query counts, and scalar
   lengths. Reject unknown fields, control characters, embedded newlines,
   leading/trailing or non-canonical whitespace, duplicate selectors, unsafe
   slugs, Windows device names, and the reserved `index` ID. The registered
   casebook profile used by fixtures is `us-law-casebook-v1`.
2. **Exact heading identity.** A `chapters` selector is an exact
   occurrence-bound ID found in `metadata["heading_path_ids"]`. It is not a
   display title, chapter number, normalized heading, or `section_path`
   prefix.
3. **One coherent input generation.** Add a packet-specific strict loader
   that, under the same `_chunk_output_lease`, loads the strict canonical
   snapshot and requires/validates the adjacent chunk-completion artifact.
   Return records, `source_sha256`, snapshot fingerprint, and the validated
   `structure_profile` provenance together; do not reopen the completion
   sidecar after releasing the lease. Compare its complete provenance to the
   syllabus profile.
4. **Exact index binding.** Require the manifest and validate its backend,
   collection, embedding model, and `source_sha256` against the request and
   leased snapshot. `_load_index_manifest`'s warning callback accepts printf
   arguments, so use `lambda *_args, **_kwargs: None` or a validated facade.
5. **Trusted retrieval mapping.** Treat vector hits as ranked stable IDs only.
   Remap every ID to the immutable canonical strict-snapshot record and never
   trust hit text/metadata. Unknown/missing IDs and retrieval/effective-mode
   warnings are explicit header notices. Canonicalize table families for
   chapter selection, query selection, rules, case groups, and outline input.
6. **Prompt-visible grounding.** Apply source/count/character budgets before
   constructing a contract. Its allowed citation IDs are exactly the IDs
   present in the bounded one-line evidence JSON. Record every source,
   character, group, and query truncation plus every degradation in the packet
   header. Required locators must be exact and non-empty.
7. **Case binding and defensive contracts.** Bind each digest to the current
   case identity. Facts, holding, and significance each carry non-empty
   citations (or one equivalently strict digest-level set) restricted to that
   case's prompt-visible IDs. Revalidate returned canonical payloads, reject
   controls/non-ASCII/Markdown injection, and catch documented LLM budget,
   security, timeout, transport, and contract failures for safe degradation.
   A digest fallback may quote only an exact prompt-visible excerpt with its
   real locator; never render `unknown source`. The outline fallback is an
   explicit omission.
8. **Safe Markdown.** Escape every scalar for its Markdown context. Validate
   each packet and `index.md` with the required Pandoc/Zettlr policy; missing
   external validation is an error, not a silent downgrade.
9. **Atomic logical publication without a new sidecar.** Under a packet-output
   lease, build and validate the complete candidate set in staging. Promote
   only verified owned files, safely clean stale owned packets, and write the
   validated `packets/index.md` last as the logical commit. The index records
   generation/index binding and each packet's SHA-256. Test crash recovery,
   index failure, concurrency, and Dropbox-like filesystem errors. Do not add
   a receipt/manifest sidecar.
10. **Failure taxonomy.** Invalid syllabus; snapshot/completion/profile or
    manifest incoherence; lease/staging/promotion/index-commit failure; and
    validator unavailability are run-fatal. Bad selectors, empty trusted
    selection, missing locators, unsafe text, and packet validation failures
    are entry-local. Known LLM/runtime/contract failures are section-local
    fallbacks. Unexpected invariant/programmer errors are never swallowed.
11. **CLI and gates.** Add `packets` to `cli_policy._namespace_uses_llm`, use
    `--db` default `None` for backend-aware resolution, forward provider and
    release-security kwargs, and add `p_pkt` to the shared run-telemetry parser
    tuple. Add `tests/test_cli_policy.py` coverage. Refresh
    `architecture-inventory.json` only after all new Python sources are
    tracked/staged, and only with the refresh tool. Do not blindly add
    `build_study_packets` to the existing `(chunks, output)` publication
    parametrization; add packet-specific strict-loader/publication tests.

## Global Constraints (from the spec and repo policy)

- Codex works on `agent/study-packets-implementation`; never commits to
  `main`.
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
- Apply the explicit failure taxonomy above; the run exits non-zero if any
  entry failed.
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
  `heading_path_ids` (occurrence-bound identity list), `headings` (display
  list), `section_path` (display string), `chapter_num`,
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
- Snapshot seam: `_load_index_snapshot_strict` returns
  `(records, source_sha256, fingerprint)`; it does not return a receipt. The
  packet facade must pair this with the existing completion validation while
  holding `_chunk_output_lease` once.
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
  `security_policy` defaulting set at rag.py:31058-31068. LLM classification
  itself lives in `cli_policy._namespace_uses_llm`; `packets` must be an
  always-LLM command there. Shared telemetry flags are installed through the
  parser tuple near rag.py:30957-30961.
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
  strict packet-snapshot facade + output lease/staging + subparser/dispatch +
  membership in the `security_policy` defaulting and telemetry sets.
- Modify: `cli_policy.py` — classify `packets` as always using the LLM path.
- Create: `tests/test_study_packets_cli.py` — handler/CLI wiring tests.
- Modify: `tests/test_cli_policy.py` — provider/security forwarding policy.

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

Export or centralize explicit limits for encoded syllabus bytes, entry count,
selectors/queries per entry, and each scalar type. `load_syllabus_text` uses
`json.loads` with `object_pairs_hook` duplicate detection and a
`parse_constant` rejection hook. Text validation accepts only already
canonical non-empty values: no stripping as normalization, no controls or
newlines, and no over-limit values. IDs also reject `index` and all
case-insensitive Windows device stems. Chapter selector validation preserves
exact occurrence IDs; it does not accept display names as an alternate form.

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
        "structure_profile": "us-law-casebook-v1",
        "entries": [
            {
                "id": "week-03-conflicts",
                "title": "Conflicts of Interest",
                "chapters": ["#/texts/42"],
                "queries": ["conflicts of interest current clients"],
            },
        ],
    }
    payload.update(overrides)
    return payload


def test_parse_syllabus_accepts_minimal_valid_payload():
    syllabus = study_packets.parse_syllabus(_syllabus_payload())
    assert syllabus.course == "Legal Ethics"
    assert syllabus.structure_profile == "us-law-casebook-v1"
    assert len(syllabus.entries) == 1
    entry = syllabus.entries[0]
    assert entry.entry_id == "week-03-conflicts"
    assert entry.title == "Conflicts of Interest"
    assert entry.chapters == ("#/texts/42",)
    assert entry.queries == ("conflicts of interest current clients",)


def test_parse_syllabus_accepts_chapters_only_and_queries_only():
    chapters_only = _syllabus_payload(entries=[
        {"id": "a", "title": "A", "chapters": ["#/texts/1"]}])
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
    ({"id": "Bad Slug!", "title": "X", "chapters": ["#/texts/1"]}, "id"),
    ({"id": "x", "title": "", "chapters": ["#/texts/1"]}, "title"),
    ({"id": "x", "title": "X", "chapters": [""]}, "chapters"),
    ({"id": "x", "title": "X", "chapters": ["#/texts/1"],
      "bogus": 1}, "unexpected"),
])
def test_parse_syllabus_rejects_invalid_entries(entry, match):
    with pytest.raises(study_packets.SyllabusError, match=match):
        study_packets.parse_syllabus(_syllabus_payload(entries=[entry]))


def test_parse_syllabus_rejects_duplicate_entry_ids():
    entries = [
        {"id": "same", "title": "A", "chapters": ["#/texts/1"]},
        {"id": "same", "title": "B", "chapters": ["#/texts/2"]},
    ]
    with pytest.raises(study_packets.SyllabusError, match="duplicate"):
        study_packets.parse_syllabus(_syllabus_payload(entries=entries))


def test_load_syllabus_text_rejects_non_json_and_non_object():
    with pytest.raises(study_packets.SyllabusError, match="JSON"):
        study_packets.load_syllabus_text("not json")
    with pytest.raises(study_packets.SyllabusError, match="object"):
        study_packets.load_syllabus_text(json.dumps([1, 2]))
```

Extend that matrix with: oversized UTF-8 input, duplicate JSON keys,
`NaN`/`Infinity`, too many entries/selectors/queries, overlong scalars,
leading/trailing whitespace, controls/newlines, duplicate selectors, `index`,
and every Windows device-name family. Include boundary-value acceptance tests
and prove a title-like chapter value such as `"Chapter 3"` remains mere text
until selection rejects it as an unresolved occurrence ID.

- [ ] **Step 2: Run tests to verify they fail**

Run: `python -m pytest tests/test_study_packets.py -q`
Expected: FAIL/ERROR with `ModuleNotFoundError: No module named
'study_packets'`.

- [ ] **Step 3: Implement the module skeleton and hardened syllabus parser**

Create the typed dataclasses and helpers listed above. Keep the parser
dependency-light, but implement every bounded/strict rule in the binding
audit contract. The file-reading facade in Task 7 must check file size before
allocating/decoding; `load_syllabus_text` must independently enforce the
encoded-byte limit so direct callers cannot bypass it. Use immutable tuples in
the accepted model and never retain caller-owned mutable objects.

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

`records` are canonical, published-order strict-snapshot records
`{"text": ..., "metadata": ...}` whose metadata carries `stable_id` and an
occurrence identity list at `heading_path_ids`. A chapter selector matches a
record only when it exactly equals one element of that list. A selector
matching zero records raises `EntrySelectionError`. Query results passed by
the injected `search_fn` have already been remapped from hit IDs to those same
trusted canonical records and carry retrieval/degradation notices. They append
in rank order, are capped at `max_query_hits` per query with a recorded notice,
and deduplicate by canonical stable ID. Records without a valid stable ID or
heading identity fail closed. An entry whose final trusted/canonical selection
is empty raises `EntrySelectionError`.

Before this function is called, Task 7 uses the existing table-retrieval
policy to build a canonical snapshot plus a family alias map. Chapter-selected
records and query IDs both resolve through that map, so a child-row hit selects
the trusted canonical table parent/family once. The same canonical items feed
all later sections.

- [ ] **Step 1: Write the failing tests** (append to
  `tests/test_study_packets.py`)

```python
def _record(stable_id, text, **metadata):
    metadata.setdefault("source_file", "book.pdf")
    metadata.setdefault("page_range", "12-13")
    metadata.setdefault("section_path", "Chapter 3 > Conflicts")
    metadata.setdefault("chapter_num", 3)
    metadata.setdefault("chapter_title", "Chapter 3")
    metadata.setdefault("heading_path_ids", ["#/texts/42"])
    metadata.setdefault("content_type", "author_narrative")
    metadata["stable_id"] = stable_id
    return {"text": text, "metadata": metadata}


def _entry(**overrides):
    values = {"entry_id": "week-03", "title": "Conflicts",
              "chapters": ("#/texts/42",), "queries": ()}
    values.update(overrides)
    return study_packets.SyllabusEntry(**values)


def _item(stable_id, text="hit", **metadata):
    record = _record(stable_id, text, **metadata)
    return study_packets.SelectionItem(
        stable_id=stable_id, text=record["text"],
        metadata=record["metadata"])


def test_select_entry_matches_only_exact_occurrence_heading_id():
    records = [
        _record("chunk_a", "one", heading_path_ids=["#/texts/42"]),
        _record("chunk_b", "two", heading_path_ids=["#/texts/42",
                                                      "#/texts/43"]),
        _record("chunk_c", "three", chapter_title="#/texts/42",
                chapter_num=42, section_path="#/texts/42 > display only",
                heading_path_ids=["#/texts/99"]),
    ]
    exact = study_packets.select_entry(
        _entry(), records, search_fn=lambda q: [])
    assert [i.stable_id for i in exact.items] == ["chunk_a", "chunk_b"]
    with pytest.raises(study_packets.EntrySelectionError):
        study_packets.select_entry(
            _entry(chapters=("Chapter 3",)), records,
            search_fn=lambda q: [])


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
            _entry(chapters=("#/texts/999",),), [_record("chunk_a", "x")],
            search_fn=lambda q: [])
    bare = {"text": "x", "metadata": {
        "heading_path_ids": ["#/texts/42"]}}
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
    heading_ids = metadata.get("heading_path_ids")
    return (
        isinstance(heading_ids, (list, tuple))
        and all(isinstance(value, str) for value in heading_ids)
        and selector in heading_ids
    )


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
    if not chosen:
        raise EntrySelectionError(
            f"entry {entry.entry_id!r}: selection is empty")
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
    included chunk. `table` chunks are parsed as one strict captioned pipe
    table, retain their literal cell text through context-safe Markdown
    escaping, receive the repository's canonical `<!-- TABLE -->` marker and
    block spacing, and are counted in `table_count`; embedded or trailing
    non-table blocks are rejected.
    Input has already been canonicalized by table family; assert that a raw
    table child never reaches rendering instead of relying only on a skip.
    Truncation past
    `MAX_RULES_CHUNKS` adds a notice. Zero eligible chunks yields the
    section header plus the line `_No statutory or table material in this
    selection._` and no notice. Every rendered item requires a non-empty
    source/page locator; a missing locator is an entry error, never the text
    `unknown source`. Corpus text and locator scalars use context-aware safe
    Markdown rendering and reject controls.

- [ ] **Step 1: Write the failing tests** (append)

```python
def test_locator_line_composes_available_fields():
    line = study_packets.locator_line({
        "source_file": "book.pdf", "page_range": "12-13",
        "section_path": "Chapter 3 > Conflicts"})
    assert line == "book.pdf · pp. 12-13 · Chapter 3 > Conflicts"
    with pytest.raises(study_packets.EntrySelectionError, match="locator"):
        study_packets.locator_line({})


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
    source = metadata.get("source_file")
    pages = metadata.get("page_range")
    if (not isinstance(source, str) or not source
            or not isinstance(pages, (str, int)) or not str(pages)):
        raise EntrySelectionError("record has no exact source/page locator")
    parts = [source, f"pp. {pages}"]
    section = metadata.get("section_path")
    if isinstance(section, str) and section:
        parts.append(section)
    return " · ".join(parts)


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
    if any(item.metadata.get("retrieval_role") == "table_child"
           for item in selection.items):
        raise EntrySelectionError(
            "non-canonical table child reached rules rendering")
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
  - `case_digest_contract(expected_case_name: str,
    allowed_ids: Collection[str]) -> _contracts.ExactJSONContract` — canonical
    value has exactly `case_name`, `facts`, `holding`, and `significance`.
    `case_name` must exactly equal `expected_case_name`; each other value is
    exactly `{"text": str, "citations": [str, ...]}`, with bounded safe text,
    a non-empty citation list, and every ID in the prompt-visible
    `allowed_ids` for this case only.
  - `outline_contract(allowed_ids: Collection[str]) ->
    _contracts.ExactJSONContract` — canonical value is
    `{"outline": [{"text": str, "citations": [str, ...]}, ...]}` with 1..
    `OUTLINE_MAX_LINES` lines, each text non-empty and bounded, each line
    carrying at least one citation, and every citation a member of
    `allowed_ids`. Both validators reject controls, line breaks, non-ASCII
    output, duplicate citations, and Markdown-structural injection; rendering
    still escapes accepted scalar text defensively.

- [ ] **Step 1: Write the failing tests** (append)

```python
def test_case_digest_contract_accepts_exact_payload():
    contract = study_packets.case_digest_contract(
        "In re Example", {"chunk_a", "chunk_b"})
    payload = json.dumps({
        "case_name": "In re Example",
        "facts": {"text": "F", "citations": ["chunk_a"]},
        "holding": {"text": "H", "citations": ["chunk_b"]},
        "significance": {"text": "S", "citations": ["chunk_a"]}})
    canonical = contract(payload)
    parsed = json.loads(canonical)
    assert parsed["case_name"] == "In re Example"
    provenance = contract.provenance(
        fallback_id=study_packets.CASE_DIGEST_FALLBACK_ID)
    assert provenance["contract_id"] == study_packets.CASE_DIGEST_CONTRACT_ID


@pytest.mark.parametrize("payload", [
    {"case_name": "C", "facts": {"text": "F", "citations": ["a"]}},
    {"case_name": "Wrong", "facts": {"text": "F", "citations": ["a"]},
     "holding": {"text": "H", "citations": ["a"]},
     "significance": {"text": "S", "citations": ["a"]}},
    {"case_name": "C", "facts": {"text": "F", "citations": ["alien"]},
     "holding": {"text": "H", "citations": ["a"]},
     "significance": {"text": "S", "citations": ["a"]}},
    {"case_name": "C", "facts": {"text": "# injected", "citations": ["a"]},
     "holding": {"text": "H", "citations": ["a"]},
     "significance": {"text": "S", "citations": ["a"]}},
])
def test_case_digest_contract_rejects_bad_payloads(payload):
    contract = study_packets.case_digest_contract("C", {"a"})
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


def _validated_contract_text(value: Any, *, field: str,
                             max_characters: int) -> str:
    """Require canonical printable ASCII with no Markdown control syntax."""
    # Implement the shared audit-hardening rules here: non-empty, already
    # stripped, length-bounded, characters U+0020..U+007E only, and rejection
    # of raw Markdown/HTML structural injection. Rendering escapes the
    # remaining inline punctuation as a second layer.
    ...


def _validated_citations(value: Any, universe: frozenset[str]) -> list[str]:
    """Require a bounded, non-empty, unique subset of the visible universe."""
    ...


def case_digest_contract(expected_case_name: str,
                         allowed_ids: Collection[str],
                         ) -> _contracts.ExactJSONContract:
    """Contract for one distilled case digest."""
    universe = frozenset(allowed_ids)
    if not universe:
        raise ValueError("case digest citation universe cannot be empty")

    def _validated_case_digest(value: Any) -> dict:
        if not isinstance(value, dict) or set(value) != set(_CASE_DIGEST_KEYS):
            raise ValueError("case digest must have exactly the four fields")
        if value["case_name"] != expected_case_name:
            raise ValueError("case digest identity does not match its group")
        canonical: dict[str, Any] = {"case_name": expected_case_name}
        for key in ("facts", "holding", "significance"):
            field = value[key]
            if not isinstance(field, dict) or set(field) != {
                    "text", "citations"}:
                raise ValueError(f"{key} needs exactly text/citations")
            canonical[key] = {
                "text": _validated_contract_text(
                    field["text"], field=key,
                    max_characters=CASE_DIGEST_MAX_FIELD_CHARACTERS),
                "citations": _validated_citations(
                    field["citations"], universe),
            }
        return canonical

    return _contracts.ExactJSONContract(
        contract_id=CASE_DIGEST_CONTRACT_ID,
        max_bytes=_CASE_DIGEST_MAX_BYTES,
        max_depth=6,
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
            text = _validated_contract_text(
                line["text"], field="outline text",
                max_characters=OUTLINE_MAX_LINE_CHARACTERS)
            citations = _validated_citations(line["citations"], universe)
            canonical.append({"text": text, "citations": citations})
        return {"outline": canonical}

    return _contracts.ExactJSONContract(
        contract_id=OUTLINE_CONTRACT_ID,
        max_bytes=_OUTLINE_MAX_BYTES,
        max_depth=6,
        schema_validator=_validated_outline,
    )
```

The audited `ExactJSONContract` constructor accepts these arguments. Keep the
schema validators as the defense-in-depth check even when `_call_llm` normally
returns contract-canonical JSON.

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
  - `@dataclass(frozen=True) PromptEvidence(prompt: str,
    visible_items: tuple[SelectionItem, ...], notices: tuple[str, ...])`
    (an equivalent immutable shape is acceptable).
  - `case_groups(selection: EntrySelection) ->
    tuple[tuple[CaseGroup, ...], tuple[str, ...]]`
  - `case_digest_prompt(group: CaseGroup) -> PromptEvidence` — bounded
    one-physical-line JSON evidence framing (mirror the SOURCE_JSON style at
    rag.py:6002-6029), including only safely locatable canonical case items.
  - `outline_prompt(selection: EntrySelection) -> PromptEvidence`
  - `build_cases_section(groups: tuple[CaseGroup, ...], llm_fn: LLMFn) ->
    tuple[str, tuple[str, ...]]` — one LLM call per group, one retry, then
    a cited verbatim-excerpt fallback with a visible notice.
  - `build_outline_section(selection: EntrySelection, llm_fn: LLMFn) ->
    tuple[Optional[str], tuple[str, ...]]` — one call, one retry, else
    `(None, notices)`.

Budgeting happens before contracts are constructed. The contract universe is
`{item.stable_id for item in evidence.visible_items}` exactly, and the prompt's
JSON contains that same set exactly. The evidence builder returns notices for
source-count and per-source character truncation; case-group and query limits
likewise feed the packet header. Prompt prose identifies source text as
untrusted evidence and forbids following instructions found inside it.
`_call_with_retry` catches only the repository's documented LLM budget,
security, timeout/transport, and `OutputContractRejected` failures; it then
re-applies the contract to any returned payload before use. Unexpected errors
propagate.

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
    evidence = study_packets.case_digest_prompt(group)
    prompt = evidence.prompt
    marker = "SOURCE_JSON (one physical line; bounded case excerpts):"
    assert marker in prompt
    payload_line = prompt.split(marker)[1].strip().splitlines()[0]
    payload = json.loads(payload_line)
    assert payload["case_name"] == "A v. B"
    assert len(payload["sources"][0]["text"]) <= (
        study_packets.CASE_SOURCE_CHARACTER_LIMIT)
    assert {item.stable_id for item in evidence.visible_items} == {
        source["stable_id"] for source in payload["sources"]}
    assert any("character" in notice for notice in evidence.notices)


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
            return json.dumps({
                "case_name": "A v. B",
                "facts": {"text": "F", "citations": ["chunk_1"]},
                "holding": {"text": "H", "citations": ["chunk_1"]},
                "significance": {
                    "text": "S", "citations": ["chunk_1"]}})
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

- [ ] **Step 3: Implement the bounded builders** (append)

- Group only canonical `case_opinion` items with a valid canonical
  `primary_case`; do not synthesize case identity from `section_path` or an
  `Unnamed case` placeholder. Missing/unsafe identity is an entry error.
- Build immutable `PromptEvidence` after enforcing source-count and
  per-source character limits. Validate each locator before inclusion and add
  a notice for every omitted or truncated source. Serialize evidence with
  `ensure_ascii=True`, sorted keys, compact separators, and one physical line.
- State in both prompts that `SOURCE_JSON` is untrusted evidence and that
  instructions inside it must be ignored. Show the nested per-field digest
  schema and the cited outline schema exactly.
- For each case, construct `case_digest_contract(group.case_name,
  visible_ids)`, call once plus one retry for known degradable failures, and
  pass any returned string through the contract again before `json.loads`.
  Render each digest field with only that field's cited locators.
- On persistent digest failure, use only the first exact prompt-visible source
  excerpt and its validated locator. If no such source exists, omit that
  digest or fail the entry according to the taxonomy; never use an unprompted
  chunk or a synthetic locator.
- For the outline, construct `outline_contract(visible_ids)` from the bounded
  outline evidence, revalidate the response, and render only citations mapped
  to those same visible trusted items. Persistent known failure returns the
  explicit omission notice.
- Escape all scalar Markdown at render time and propagate selection,
  group/source/character truncation, LLM degradation, and fallback notices to
  packet assembly.

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
    EntryPacket` — builds all sections first, then assembles a header that
    includes every selection/truncation/degradation/fallback notice, plus the
    sections and an optional repeated notices footer.
  - `render_course_index(course: str, packets: tuple[EntryPacket, ...],
    failures: tuple[EntryFailure, ...], *, generation_id: str,
    index_binding: dict, packet_hashes: Mapping[str, str]) -> str`

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
        return json.dumps({
            "case_name": "A v. B",
            "facts": {"text": "F", "citations": ["chunk_c"]},
            "holding": {"text": "H", "citations": ["chunk_c"]},
            "significance": {"text": "S", "citations": ["chunk_c"]}})
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


def test_build_entry_packet_records_all_notices_in_header():
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
    header_text = packet.markdown.split("## Rules and definitions", 1)[0]
    assert "digest unavailable" in header_text
    assert "outline omitted" in header_text


def test_render_course_index_lists_packets_and_failures():
    packet = study_packets.EntryPacket(
        entry_id="week-03", title="Conflicts", markdown="# x\n",
        table_count=0, notices=())
    failure = study_packets.EntryFailure(
        entry_id="week-04", title="Candor", reason="selector matched nothing")
    index = study_packets.render_course_index(
        "Legal Ethics", (packet,), (failure,), generation_id="generation-1",
        index_binding={"source_sha256": "abc"},
        packet_hashes={"week-03.md": "f" * 64})
    assert "# Legal Ethics — study packets" in index
    assert "[Conflicts](week-03.md)" in index
    assert "week-04" in index and "selector matched nothing" in index
    assert "generation-1" in index and "f" * 64 in index
```

Add hostile-scalar tests for course/title/case/locator/notice/failure values,
packet-hash inventory mismatch tests, and Markdown-validation tests for both a
packet and the course index. Required external-validator unavailability must
fail rather than silently accepting a fallback parser.

- [ ] **Step 2: Run tests to verify they fail**

Run: `python -m pytest tests/test_study_packets.py -q -k "packet or index"`
Expected: FAIL with `AttributeError`.

- [ ] **Step 3: Implement safe packet and index rendering** (append)

Keep the immutable shapes above, but render only after all section builders
have returned so the header can enumerate every notice. Use a single
context-aware scalar escaping helper throughout; never interpolate raw source
metadata, model output, or exception text into headings, links, list items, or
code spans. Keep the index deterministic: record the generation identifier,
full validated index binding, each successful packet filename and SHA-256, and
escaped failed-entry summaries. Validate the packet-hash mapping is an exact
match for the successful packet set before rendering.

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
- Modify: `rag.py` (imports including `datetime`, a lease-held strict packet
  snapshot helper, `build_study_packets`, packet output lease/staging helpers,
  and parser/dispatch/telemetry wiring)
- Modify: `cli_policy.py`
- Test: `tests/test_study_packets_cli.py`
- Test: `tests/test_cli_policy.py`

**Interfaces:**
- Consumes: everything `study_packets` exports; `rag` collaborators listed
  in Repository orientation.
- Produces: `rag.build_study_packets(syllabus_path: Path, *,
  chunks_path: Path, db_dir: Path | None = None,
  db_backend: str = DEFAULT_DB_BACKEND,
  collection_name: str = DEFAULT_COLLECTION,
  embedding_model: str = DEFAULT_EMBEDDING_MODEL,
  cloud_url: str = DEFAULT_CLOUD_URL, cloud_model: str = DEFAULT_CLOUD_MODEL,
  cloud_key: str = "", ollama_url: str = DEFAULT_OLLAMA_URL,
  ollama_model: str = DEFAULT_OLLAMA_MODEL, gemini_key: str = "",
  llm_workers: int = DEFAULT_LLM_WORKERS, thinking: bool = False,
  security_policy: _release_security.ReleaseSecurityPolicy | None = None,
  ) -> None` — raises `SystemExit(1)` for any run-fatal or entry failure and
  `SystemExit(2)` for an invalid/unreadable syllabus.

Audited behavioral requirements (each needs a direct test):

1. Check syllabus file size before reading/decoding, then call the bounded
   strict parser. `OSError` or `SyllabusError` prints a safe reason and exits
   2 without creating/staging output.
2. Add a dedicated packet snapshot helper. Inside one `_chunk_output_lease`,
   perform the same strict canonical snapshot and quality validation used by
   `generate_briefs`, require/validate the adjacent chunk-completion inputs,
   and return `(records, source_sha256, fingerprint,
   structure_profile_provenance)`. The existing
   `_load_index_snapshot_strict` returns only three values; do not pretend it
   returns a receipt, and do not read the completion artifact after releasing
   the lease.
3. Resolve syllabus profile `us-law-casebook-v1` with `get_profile`, validate
   the complete receipt provenance with `profile_from_provenance`, and compare
   canonical profile SHA-256 values. Any malformed/missing/mismatched receipt
   exits 1 before output staging.
4. Resolve `db_dir` with the existing backend-aware default when it is `None`.
   Require `_load_index_manifest(...)` using
   `warning_fn=lambda *_args, **_kwargs: None`; validate exact backend,
   collection, embedding model, and `manifest["source_sha256"] ==
   source_sha256`. Build the header binding only from these validated values
   plus the snapshot fingerprint.
5. Canonicalize strict-snapshot table families with the existing
   `_table_retrieval_core` policy and build a trusted alias resolver from every
   snapshot stable ID to its canonical family record. Reject duplicate or
   malformed trusted IDs as run-level snapshot incoherence.
6. For each query call `search_index(...)` with resolved database/backend,
   requested collection/model, chunks path, security policy, and enough hits
   to detect truncation. Consume `SearchResponse.hits`, warnings, and effective
   mode. Treat each hit as an ID only: remap it through the trusted resolver;
   never use `hit.text` or other hit metadata. Drop unknown/missing IDs with a
   header notice and preserve retrieval warnings/degradation.
7. The injected LLM closure forwards every provider/security option,
   `llm_workers`, thinking mode, operation/version/budget/timeout, contract and
   fallback IDs, and `output_validator`. Convert only the repository's known
   LLM runtime/budget/security/transport/strict-output failures into `None` for
   the policy fallbacks; propagate invariant/programming failures.
8. Per entry, select trusted canonical items, build all sections/notices,
   render, and require exact Markdown validation. Selection/locator/unsafe-text
   and packet-validation failures become escaped `EntryFailure` records;
   unexpected exceptions do not. Keep successful bytes only in a unique
   staging directory and compute their SHA-256 values.
9. Render the course index with generation ID, validated binding, exact packet
   inventory/hashes, and entry failures; validate it too. Under a dedicated
   packet-output lease, promote verified staged packet files, remove only
   previously owned stale packet files, and atomically write `index.md` last.
   Its successful write is the logical commit. Always clean abandoned staging
   safely. No new receipt/manifest sidecar is allowed.
10. Print one safe summary line per entry and exit 1 if any entry failed. A
    lease/staging/promotion/stale-cleanup/index validation or index-commit
    failure is run-fatal and must not be recast as an entry failure.
11. CLI parser: `--db` has `default=None`; add database/backend, collection,
    embedding, provider, and release-security flags. Dispatch with
    `_llm_kwargs_from_args(..., include_workers=True)` and the resolved
    `security_policy`. Add `p_pkt` to the shared run-telemetry parser tuple.
    Add `"packets"` to the security-policy defaulting set and to
    `cli_policy._namespace_uses_llm` as an always-LLM command.

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
        "structure_profile": "us-law-casebook-v1",
        "entries": entries or [
            {"id": "week-03", "title": "Conflicts",
             "chapters": ["#/texts/42"]}],
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
            "heading_path_ids": ["#/texts/42"],
            "chapter_title": "Chapter 3", "chapter_num": 3,
            "section_path": "Chapter 3 > Rules", "page_range": "12",
            "source_file": "book.pdf"}},
        {"text": "Opinion text", "metadata": {
            "stable_id": "chunk_c", "content_type": "case_opinion",
            "heading_path_ids": ["#/texts/42"],
            "primary_case": "A v. B", "chapter_title": "Chapter 3",
            "chapter_num": 3, "section_path": "Chapter 3 > Cases",
            "page_range": "14", "source_file": "book.pdf"}},
    ]
    monkeypatch.setattr(
        rag, "_load_packet_snapshot_strict",
        lambda path: (records, "abc", "snapshot-fingerprint", {
            "schema_version": 1, "name": "us-law-casebook-v1",
            "revision": 1, "sha256": "x"}))
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
                "case_name": "A v. B",
                "facts": {"text": "F", "citations": ["chunk_c"]},
                "holding": {"text": "H", "citations": ["chunk_c"]},
                "significance": {
                    "text": "S", "citations": ["chunk_c"]}}))
        return contract(json.dumps({"outline": [
            {"text": "Issue", "citations": ["chunk_s"]}]}))

    monkeypatch.setattr(rag, "_call_llm", fake_call_llm)
    monkeypatch.setattr(
        rag._markdown_validation, "validate_markdown_candidate",
        lambda markdown, **kwargs: {"publishable": True})

    rag.build_study_packets(
        _write_syllabus(tmp_path), chunks_path=chunks_path,
        db_dir=tmp_path / "db", collection_name="ethics",
        embedding_model="model-a")

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
        "heading_path_ids": ["#/texts/42"],
        "chapter_title": "Chapter 3", "chapter_num": 3,
        "section_path": "Chapter 3 > Rules", "page_range": "12",
        "source_file": "book.pdf"}}]
    monkeypatch.setattr(
        rag, "_load_packet_snapshot_strict",
        lambda path: (records, "abc", "snapshot-fingerprint", {
            "schema_version": 1, "name": "us-law-casebook-v1",
            "revision": 1, "sha256": "x"}))
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
        {"id": "good", "title": "Good", "chapters": ["#/texts/42"]},
        {"id": "bad", "title": "Bad", "chapters": ["#/texts/999"]},
    ])
    with pytest.raises(SystemExit) as excinfo:
        rag.build_study_packets(
            syllabus, chunks_path=chunks_path, db_dir=tmp_path / "db",
            collection_name="ethics", embedding_model="m")
    assert excinfo.value.code == 1
    packets_dir = chunks_path.parent / "packets"
    assert (packets_dir / "good.md").is_file()
    assert not (packets_dir / "bad.md").exists()
    index_text = (packets_dir / "index.md").read_text(encoding="utf-8")
    assert "bad" in index_text and "selector" in index_text
```

Extend `tests/test_study_packets_cli.py` beyond the happy-path sketches above:

- Assert the strict helper holds one chunk-output lease while reading both
  snapshot and required completion inputs, returns the real four-part shape,
  and fails on a missing/changed sidecar.
- Parameterize manifest source/backend/collection/model mismatch and verify no
  output staging begins. Verify the warning callback tolerates printf args.
- Return poisoned text/metadata from vector hits and prove only the trusted
  strict-snapshot record renders. Cover unknown/missing IDs, retrieval
  warnings/effective-mode notices, and table-child-to-family remapping.
- Assert full provider, security-policy, and telemetry forwarding, backend-
  aware `db_dir` resolution when `--db` is omitted, and safe translation of
  run-level `OSError`/`ValueError` to exit 1.
- Seed a previously committed generation, then inject failure during staging,
  promotion, stale cleanup, index validation, and final index write. Also
  contend the output lease and simulate Dropbox-style rename/share errors.
  In every case the old `index.md` must not falsely commit a mixed generation;
  staging is recoverably cleaned. On success, `index.md` is observed last and
  every listed hash matches its packet.
- Add packet-specific strict-loader/publication failure tests rather than
  placing `build_study_packets` in the existing generic handler
  parametrization whose call shape is `(chunks, output)`.

- [ ] **Step 2: Run tests to verify they fail**

Run: `python -m pytest tests/test_study_packets_cli.py -q`
Expected: FAIL with `AttributeError: ... build_study_packets`.

- [ ] **Step 3: Implement the handler and CLI wiring in `rag.py`**

Add `import study_packets as _study_packets` to the first-party import
block (alphabetical position, near `import source_fidelity_core` /
`import table_retrieval_core`). Add `import datetime` with the standard-library
imports. Implement the helper, handler, staging/promotion functions, parser,
dispatch, security default, and telemetry tuple exactly according to the 11
audited requirements above. Keep generation time caller-independent from the
policy module via
`datetime.datetime.now(datetime.timezone.utc).isoformat(timespec="seconds")`.

In `cli_policy.py`, include `packets` in the unconditional LLM-command branch;
do not depend on provider flags being present to infer it. The CLI parser must
set `--db` to `None`, allowing the same backend-aware resolver used by query.
Tests must verify that `_llm_kwargs_from_args` retains provider settings and
that release-security and telemetry flags reach the handler. Do not directly
write live packet paths inside the entry loop: the only live commit sequence
is the output-lease promotion with validated `index.md` written last.

- [ ] **Step 4: Run tests to verify they pass**

Run: `python -m pytest tests/test_study_packets_cli.py tests/test_study_packets.py tests/test_cli_policy.py -q`
Expected: all PASS.

- [ ] **Step 5: Lint and commit**

```bash
python -m ruff check rag.py cli_policy.py tests/test_study_packets_cli.py tests/test_cli_policy.py
git add rag.py cli_policy.py tests/test_study_packets_cli.py tests/test_cli_policy.py
git commit -m "Wire the packets CLI command to the packet policy"
```

---

### Task 8: Full-suite integration and quality gates

**Files:**
- Modify: `architecture-inventory.json` (tool-generated ONLY)
- Possibly modify: `tests/test_scaffold_structure.py` (only if its gate flags
  the new module/command). Packet-specific publication tests belong in
  `tests/test_study_packets_cli.py`, not in an incompatible generic
  parametrization.

- [ ] **Step 1: Run the full suite**

Run: `python -m pytest -q`
Expected: some failures are ANTICIPATED and legitimate:
- `tests/test_architecture_inventory.py::test_repository_baseline_is_current_and_canonical`
  — the inventory hashes every tracked Python source; fix with
  `python tools/check_architecture_inventory.py --refresh` (never by hand).
- Do not add `build_study_packets` blindly to
  `tests/test_output_publication.py`'s existing `(chunks, output)` handler
  parametrization: the packet handler intentionally has a different call
  shape. Preserve its integrity coverage through the packet-specific strict
  loader, staging, and logical-commit tests from Task 7.
- Structure/architecture tests may pin module counts or import graphs; fix
  by following each failure message's own instruction (these gates print
  what they expect). Do NOT weaken a gate to pass it; extend its expected
  list with the new module/command only.

Any OTHER failure means a behavior regression — fix the new code, not the
existing test.

- [ ] **Step 2: Refresh the inventory and re-run gates**

First ensure every new Python source and test is known to Git (committed by the
earlier task steps or explicitly staged). An untracked source is invisible to
the inventory tool and makes a refresh invalid.

```bash
git add study_packets.py rag.py cli_policy.py tests/test_study_packets.py tests/test_study_packets_cli.py tests/test_cli_policy.py
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

Push `agent/study-packets-implementation` and report: branch name, final commit
SHA, observed test
counts, and any gate whose expected list was extended (with the exact
diff). Do NOT open a candidate PR, regenerate Phase A0 or benchmarks, or edit
`ROADMAP.md` — the owner's machine performs the evidence and candidate ritual.

---

## Deviations and escalation

- If a documented interface differs from the line references above (the
  file drifts), trust the code, keep the behavioral requirement, and note
  the difference in the final report.
- The audited `_load_index_snapshot_strict` shape is
  `(records, source_sha256, fingerprint)` and `ExactJSONContract` accepts the
  planned constructor arguments. If repository drift changes either, inspect
  the new implementation and preserve the coherence/contract requirements;
  never weaken the tests or read a receipt outside the chunk-output lease.
- Never commit with a failing gate "to be fixed later".
