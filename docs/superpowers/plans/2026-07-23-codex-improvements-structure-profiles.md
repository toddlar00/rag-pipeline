# Code Improvements: Hygiene + Configurable Document-Structure Profiles

> [!IMPORTANT]
> **Historical plan — mostly implemented and superseded.** Document profiles,
> context assembly, table retrieval, process supervision, and vector lifecycle
> are implemented in draft PRs
> [#35](https://github.com/toddlar00/rag-pipeline/pull/35),
> [#36](https://github.com/toddlar00/rag-pipeline/pull/36),
> [#37](https://github.com/toddlar00/rag-pipeline/pull/37),
> [#33](https://github.com/toddlar00/rag-pipeline/pull/33), and
> [#38](https://github.com/toddlar00/rag-pipeline/pull/38), respectively.
> [PR #43](https://github.com/toddlar00/rag-pipeline/pull/43) implements the
> Ethics review/release machinery, but corpus-owner decisions remain pending.
> Repository-hygiene mechanics are implemented locally in commit `88fd301`;
> the private-source owner decision, publication, scoped static typing, README
> decomposition, and the residual pipeline/UI/evaluator-to-`rag` composition
> work remain open. R8c-1 through R8c-5 have since resolved the former
> job/manager dependency seam and established a lazy service-role composition
> root; the broader pipeline/UI root remains future work.
> The unchecked boxes, code sketches, branch instructions, baselines, and test
> counts below are retained as historical design context, not live execution
> instructions. [ROADMAP.md](../../../ROADMAP.md) is the status authority.
> Private-corpus rules in this plan record a conservative historical working
> constraint; the owner's longer-term policy for corpus-derived evaluation
> metadata is not settled. Introduce no new source excerpts while it remains
> unresolved.

> **For the implementing agent (Codex):** Work task-by-task, top to bottom. Steps
> use checkbox (`- [ ]`) syntax for tracking. Every task ends with the full
> verification gate green before its commit. Do not start a later task while an
> earlier one is red. Read the Context Primer and Global Constraints before
> touching anything.

**Goal:** Land two mergeable milestones: (A) a small repository-hygiene PR, and
(B) the ROADMAP P2 "Configurable document-structure profiles" milestone —
front/back-matter labels, chapter patterns, and canonical-title rules move out
of hardcoded `rag.py` logic into a tested, stdlib-only `document_profiles.py`
leaf module with a default profile that reproduces current behavior exactly.

**Architecture:** Follow the repository's established extraction pattern:
stdlib-only leaf module owning deterministic policy and typed records; `rag.py`
stays the compatibility facade and threads the profile through its existing
structure functions via keyword parameters that late-bind to a module-global
active profile. Behavior under the default profile is pinned by
characterization tests written *before* any threading change.

**Tech Stack:** Python 3.10+ stdlib only in the new module (`dataclasses`,
`re`, `typing`). No new dependencies anywhere.

---

## Context Primer (read first)

This repo converts law-school textbook PDFs into chunked markdown, vector
indexes (Chroma default, Qdrant optional), and study products, with hybrid
retrieval and grounded answers. Key facts an outside agent must know:

- `rag.py` (~14,400 lines) is the stable command/API compatibility facade.
  Domain logic is progressively extracted into stdlib-only "leaf" modules
  (`retrieval_core`, `chunking_core`, `artifact_io`, `index_state`,
  `cli_policy`, `ingestion_core`, `quality_core`, ...). The pattern: the leaf
  module owns deterministic policy and typed records; mutable collaborators are
  injected through callback/protocol parameters; `rag.py` rebinds the old
  private names so consumers and tests never change.
- Extractions are **strictly behavior-preserving**. Defects discovered during a
  refactor are recorded in `ROADMAP.md` as follow-ups, never fixed in the same
  change. Characterization tests are written against current behavior first.
- Publication is fail-closed and atomic; resume validates provenance and
  invalidates cached output when a policy input changes (see how
  `max_llm_transport_attempts` participates in chunk-completion provenance —
  the profile must join provenance the same way).
- Dependencies are hash-locked; never hand-edit `*.lock` files. Model artifacts
  are byte-verified; unknown model IDs fail closed.
- **The root-level `*.pdf` files are the private source corpus. Never commit,
  excerpt, or paraphrase their text into fixtures, tests, or docs.** All new
  fixtures must be synthetic.
- The working copy lives in a Dropbox-synced folder on Windows; CI runs Linux
  (3.10–3.14) and Windows (3.12). Anything filesystem-sensitive must pass both.
- Verification gate used throughout this plan:

  ```bash
  python -m pytest -q
  python -m ruff check .
  python tools/check_dependency_policy.py
  python tools/check_model_artifacts.py
  python -m compileall -q rag.py document_profiles.py tests
  git diff --check
  ```

  Baseline expectation before this plan: 1,127 passed / 7 skipped.

**Baseline / branching:** PR #31 (`agent/ethics-corpus-coherence`) is a draft
touching the same chunking/scaffold regions as Milestone B. If PR #31 has
merged, branch from `main`. If not, branch from `agent/ethics-corpus-coherence`
and say so in the PR body. Milestone A is independent and can branch from
`main` at any time. Use one branch and one draft PR per milestone:
`agent/repo-hygiene` and `agent/document-structure-profiles`.

---

## Brainstormed improvement inventory (2026-07-23)

Recorded here so the brainstorm survives as a durable artifact (the original
project brainstorm was lost once already — see `ROADMAP.md` preamble).

| # | Improvement | Assessment | Disposition |
|---|---|---|---|
| 1 | Repo hygiene: stray root scripts, untracked `CLAUDE.md`/`docs/`, unignored `tmp/`, dangling spec link | Mechanical scope implemented and validated in local commit `88fd301`; owner privacy policy, publication, and PR disposition remain in current R0 | **Mechanics implemented locally; historical Task 1 is superseded** |
| 2 | Configurable document-structure profiles (ROADMAP P2) | Implemented with an immutable explicit registry, strict receipts, and fail-closed layout validation rather than the mutable-active-profile sketch below | **Implemented in draft PR #35; Tasks 2–7 are superseded** |
| 3 | Stable adjacency/parent identifiers + context-aware retrieval assembly (ROADMAP P2) | Stable linkage and bounded, independently citable context assembly are implemented; full owner-reviewed evaluation ablation remains follow-up evidence | **Implemented in draft PR #36** |
| 4 | Table-specific retrieval (ROADMAP P2) | Caption/header-propagated row children and family collapse are implemented; family-aware judged evaluation remains a follow-up | **Implemented in draft PR #37** |
| 5 | Process-supervision extraction (ROADMAP P3, slice 1) | Implemented behind late-bound `rag.py` wrappers; see the maintained ADR and historical process plan | **Implemented in draft PR #33** |
| 6 | Vector-lifecycle extraction (ROADMAP P3, slice 2) | Deterministic reconciliation, guarded mutation, verification receipts, and ordered commit are implemented | **Implemented in draft PR #38** |
| 7 | Ethics retrieval calibration (ROADMAP P1) | Review and release tooling is implemented, but **an agent must not fabricate expert judgments**; owner approval and final thresholds remain pending | **In progress in draft PR #43; owner decision required** |
| 8 | Static typing gate (mypy/pyright on leaf modules only) | Worthwhile stretch; current lint gate (`E4,E7,E9,F`) is deliberate, so any broadening needs owner sign-off first | Optional, ask owner |
| 9 | Split the large `README.md` into `docs/` pages | The README has grown beyond the size recorded by this plan; separating operator, architecture, evaluation, and migration guidance is now a maintainability improvement | Open follow-up |
| 10 | `job_manager`/`service_runtime` ↔ `rag` circular import seam | Still present after supervision and vector-lifecycle extraction; changing it requires a separate characterization-first consumer-boundary milestone | Open follow-up |

## Global Constraints

- Strictly behavior-preserving under the default profile: any behavioral
  difference found by the full suite is a defect in the extraction, not an
  improvement to keep. Record discovered pre-existing defects in `ROADMAP.md`
  as follow-ups instead of fixing them here.
- `document_profiles.py` is stdlib-only and must not import `rag`,
  `chunking_core`, or any repo module.
- All existing test files pass **unmodified** except where a task explicitly
  says otherwise. New tests go in `tests/test_document_profiles.py` and
  `tests/test_structure_profiles_integration.py`.
- No text from the private PDFs in any committed file. Synthetic fixtures only.
- Never hand-edit `*.lock` files; this plan needs no dependency changes at all.
- Flat repo layout for modules (leaf modules sit at the root, like
  `chunking_core.py`); utility launchers go under `scripts/`.
- Keep the facade monkeypatch-friendly: profile resolution inside `rag.py`
  wrappers must happen **at call time** (late binding), never captured at
  import time, so `monkeypatch.setattr(rag, ...)` keeps working.
- One milestone per branch/PR. Commit after every green task.

---

### Task 1: Repository hygiene (Milestone A — its own branch + PR)

**Files:**
- Move: `_run_civpro.py` → `scripts/run_civpro.py`
- Move: `_resume_civpro.py` → `scripts/resume_civpro.py`
- Modify: `.gitignore` (ignore `tmp/`)
- Add to git: `CLAUDE.md`, `docs/superpowers/plans/*.md` (currently untracked)
- Modify: `docs/superpowers/plans/2026-07-22-process-supervision-extraction-plan.md` (fix dangling spec link)

**Interfaces:** none — no importable code changes.

- [ ] **Step 1: Confirm the scripts are unreferenced**

Run: `git grep -n "_run_civpro\|_resume_civpro"`
Expected: no matches outside the two files themselves. (Verified 2026-07-23;
re-verify at execution time. If a match appears, update it as part of the move.)

- [ ] **Step 2: Move the launchers**

```bash
git checkout -b agent/repo-hygiene main
mkdir -p scripts
git mv _run_civpro.py scripts/run_civpro.py
git mv _resume_civpro.py scripts/resume_civpro.py
```

Both scripts compute `workspace = Path(__file__).resolve().parent` and expect
the repo root. Update that line in **both** moved files to:

```python
    workspace = Path(__file__).resolve().parent.parent
```

- [ ] **Step 3: Ignore `tmp/`**

Append to `.gitignore`:

```
tmp/
```

- [ ] **Step 4: Track the untracked project docs**

```bash
git add CLAUDE.md docs/
```

(`docs/superpowers/plans/` currently holds the process-supervision plan and
this plan; both belong in history. Do **not** add `tmp/` — Step 3 ignores it.)

- [ ] **Step 5: Repair the process-design reference (historical; resolved)**

The original design was published separately in
[PR #32](https://github.com/toddlar00/rag-pipeline/pull/32) but was not part of
the implementation stack. The process plan now links the maintained local
[process-supervision ADR](../../architecture/decisions/process-supervision-extraction.md),
which reflects the implemented boundary in PR #33. No dangling local spec link
remains.

- [ ] **Step 6: Full verification gate**

Run the six-command gate from the Context Primer.
Expected: identical results to baseline (the moved scripts are not on any
import path; `pyproject.toml` `testpaths` is unaffected).

- [ ] **Step 7: Commit and publish**

```bash
git add .gitignore scripts docs
git commit -m "Relocate launcher scripts and track project docs"
git push -u origin agent/repo-hygiene
```

Open a draft PR titled "Repository hygiene: scripts/, tracked docs, tmp ignore"
listing the four changes and the verification output.

---

### Task 2: `document_profiles.py` leaf module (Milestone B starts here)

Branch first: `git checkout -b agent/document-structure-profiles <baseline>`
(see Context Primer for baseline choice).

**Files:**
- Create: `document_profiles.py`
- Create: `tests/test_document_profiles.py`

**Interfaces:**
- Produces (later tasks and `rag.py` rely on these exact names):
  - `class StructureProfile` — frozen dataclass, fields listed below
  - `class UnknownProfileError(KeyError)`
  - `DEFAULT_PROFILE_NAME: str = "us-casebook-v1"`
  - `PROFILES: dict[str, StructureProfile]`
  - `get_profile(name: str) -> StructureProfile` (raises `UnknownProfileError`)
  - `compiled_chapter_patterns(profile: StructureProfile) -> tuple[re.Pattern, ...]`
  - `profile_provenance(profile: StructureProfile) -> dict[str, object]`

- [ ] **Step 1: Write the failing tests**

Create `tests/test_document_profiles.py`:

```python
"""Unit tests for the document_profiles leaf module."""

import dataclasses

import pytest

import document_profiles as dp


def test_default_profile_is_registered():
    profile = dp.get_profile(dp.DEFAULT_PROFILE_NAME)
    assert profile.name == dp.DEFAULT_PROFILE_NAME
    assert profile.version >= 1
    assert profile is dp.PROFILES[dp.DEFAULT_PROFILE_NAME]


def test_unknown_profile_fails_closed():
    with pytest.raises(dp.UnknownProfileError):
        dp.get_profile("not-a-profile")


def test_profiles_are_immutable():
    profile = dp.get_profile(dp.DEFAULT_PROFILE_NAME)
    with pytest.raises(dataclasses.FrozenInstanceError):
        profile.version = 99


def test_chapter_patterns_compile_and_match_plain_chapter():
    profile = dp.get_profile(dp.DEFAULT_PROFILE_NAME)
    compiled = dp.compiled_chapter_patterns(profile)
    assert compiled
    assert any(p.search("Chapter 3: Personal Jurisdiction") for p in compiled)


def test_provenance_binds_name_and_version():
    profile = dp.get_profile(dp.DEFAULT_PROFILE_NAME)
    assert dp.profile_provenance(profile) == {
        "structure_profile": profile.name,
        "structure_profile_version": profile.version,
    }
```

- [ ] **Step 2: Run tests to verify they fail**

Run: `python -m pytest tests/test_document_profiles.py -q`
Expected: FAIL with `ModuleNotFoundError: No module named 'document_profiles'`

- [ ] **Step 3: Create the module**

```python
"""Document-structure profiles for textbook ingestion.

Owns the deterministic layout policy that was previously hardcoded in
``rag.py``: front/back-matter labels, chapter-heading patterns, and
canonical-title rules.  ``rag.py`` remains the compatibility facade and
threads the active profile into its structure functions at call time.

This module is stdlib-only and must not import other repository modules.
Behavior under the default profile is intentionally identical to the
pre-extraction hardcoded rules; any defect discovered here is recorded in
``ROADMAP.md`` as follow-up work rather than fixed during the move.
"""

import re
from dataclasses import dataclass
from functools import lru_cache


class UnknownProfileError(KeyError):
    """Raised when a structure profile name is not registered."""


@dataclass(frozen=True)
class StructureProfile:
    """Deterministic layout policy for one publisher/document family."""

    name: str
    version: int
    # Regexes (strings) that recognize a chapter heading and capture the
    # chapter number in group 1 and title in group 2 where present.
    chapter_heading_patterns: tuple[str, ...]
    # Case-insensitive labels that mark front-matter sections/pages.
    front_matter_labels: tuple[str, ...]
    # Case-insensitive labels that mark back-matter sections/pages.
    back_matter_labels: tuple[str, ...]
    # Labels that identify the table-of-contents span.
    toc_labels: tuple[str, ...]
    # Canonical rebuilt chapter title, e.g. "Chapter {number}: {title}".
    canonical_chapter_title_format: str
    # Page-number styles treated as front matter (e.g. roman numerals).
    front_matter_page_number_styles: tuple[str, ...] = ("roman",)
    # One-line prose description of the chapter designation, used in the
    # LLM layout-analysis prompt ("Chapter designation: ...").
    chapter_pattern_description: str = ""


# The default profile's literal values are populated in Task 3 by copying
# the exact legacy constants out of rag.py.  Until then this placeholder
# keeps the registry importable for the unit tests above.
_US_CASEBOOK_V1 = StructureProfile(
    name="us-casebook-v1",
    version=1,
    chapter_heading_patterns=(
        r"^Chapter\s+(\d+)[:.\s]\s*(.+)$",
    ),
    front_matter_labels=("table of contents", "preface", "copyright"),
    back_matter_labels=("index", "glossary", "appendix"),
    toc_labels=("table of contents", "contents"),
    canonical_chapter_title_format="Chapter {number}: {title}",
    chapter_pattern_description="Chapters numbered 'Chapter N: Title'",
)

DEFAULT_PROFILE_NAME = _US_CASEBOOK_V1.name

PROFILES: dict[str, StructureProfile] = {
    _US_CASEBOOK_V1.name: _US_CASEBOOK_V1,
}


def get_profile(name: str) -> StructureProfile:
    """Return the registered profile or fail closed on unknown names."""
    try:
        return PROFILES[name]
    except KeyError:
        raise UnknownProfileError(
            f"Unknown document-structure profile: {name!r}. "
            f"Registered: {sorted(PROFILES)}") from None


@lru_cache(maxsize=None)
def _compile(pattern: str) -> re.Pattern:
    return re.compile(pattern, re.IGNORECASE | re.MULTILINE)


def compiled_chapter_patterns(
        profile: StructureProfile) -> tuple[re.Pattern, ...]:
    """Compiled chapter-heading regexes for *profile*."""
    return tuple(_compile(p) for p in profile.chapter_heading_patterns)


def profile_provenance(profile: StructureProfile) -> dict[str, object]:
    """Provenance fields that bind derived artifacts to their profile."""
    return {
        "structure_profile": profile.name,
        "structure_profile_version": profile.version,
    }
```

- [ ] **Step 4: Run tests to verify they pass**

Run: `python -m pytest tests/test_document_profiles.py -q`
Expected: 5 passed

- [ ] **Step 5: Commit**

```bash
git add document_profiles.py tests/test_document_profiles.py
git commit -m "Add document-structure profile leaf module"
```

---

### Task 3: Legacy-rule inventory and characterization tests

This task pins current behavior **before** any `rag.py` change. It is the
safety net for the whole milestone.

**Files:**
- Modify: `document_profiles.py` (replace the Task 2 placeholder literals in
  `_US_CASEBOOK_V1` with the exact legacy values)
- Modify: `tests/test_document_profiles.py` (append)
- Create: `tests/test_structure_profiles_integration.py`

**Interfaces:**
- Consumes: Task 2's module surface.
- Produces: `_US_CASEBOOK_V1` whose field values are the **verbatim** legacy
  constants from `rag.py`, and characterization tests that pin scaffold /
  front-matter / canonical-title behavior on synthetic fixtures.

- [ ] **Step 1: Inventory the legacy rules in rag.py**

Read these regions of `rag.py` (symbol names verified against the current
tree; use `grep -n` to locate them — line numbers drift):

| Symbol / region | What to extract into the profile |
|---|---|
| `_identify_book_sections` (front/back-matter page-range detection; includes the "Table of Cases appears in front matter but is not the back-of-book Index" rule) | front/back-matter label sets, TOC labels |
| `_analyze_toc_layout` (docstring: "Law school textbooks have front matter (roman numerals) followed by ...") and the layout-schema JSON block containing `"chapter_pattern"` | `chapter_pattern_description`, `front_matter_page_number_styles` |
| the local `chapter_patterns = [...]` list inside the scaffold-normalization region (near `_normalize_scaffold_metadata`) and its two downstream match loops | `chapter_heading_patterns` (verbatim regex strings, same order) |
| `_canonical_chapter_titles` (builds `"Chapter {n}: {title}"` forms) | `canonical_chapter_title_format` |
| the `# --- Front/back matter detection ---` block | any additional label/keyword literals |
| roman-numeral "Strategy C" in the TOC page-number parser (maps roman pages to page 0 / front matter) | confirms `front_matter_page_number_styles` |

Copy every literal **verbatim** into `_US_CASEBOOK_V1`, replacing the Task 2
placeholders. If a legacy rule does not fit the existing dataclass fields,
**add a precisely named field** rather than forcing it — the Task 2 schema is
the starting contract, and exact legacy reproduction wins over schema
minimalism. Keep the Task 2 unit tests passing (update the plain-chapter match
test only if the verbatim legacy pattern legitimately differs).

- [ ] **Step 2: Add the constants-equality test**

Append to `tests/test_document_profiles.py` a test that imports `rag` and
asserts the profile fields equal the legacy constants **as re-exported by the
facade after Task 4**. Until Task 4 lands, pin the values directly:

```python
def test_default_profile_pins_legacy_chapter_patterns():
    profile = dp.get_profile(dp.DEFAULT_PROFILE_NAME)
    # These literals are copied verbatim from rag.py's legacy
    # chapter_patterns list; the integration suite proves rag.py now
    # consumes them from this profile.
    assert len(profile.chapter_heading_patterns) >= 1
    for pattern in profile.chapter_heading_patterns:
        assert pattern == pattern.strip()
```

and extend it with one exact-equality assertion per copied literal list
(chapter patterns, front-matter labels, back-matter labels, TOC labels),
writing the expected values inline as copied from `rag.py`.

- [ ] **Step 3: Write characterization tests on synthetic fixtures**

Create `tests/test_structure_profiles_integration.py`. Build small synthetic
inputs (never corpus text) and pin today's outputs by calling the existing
`rag` functions **before any threading change**:

```python
"""Characterization + profile-threading tests for document structure.

Fixtures are fully synthetic.  The characterization tests were written
against pre-profile rag.py behavior and must keep passing unchanged once
the profile is threaded through (default profile == legacy behavior).
"""

import rag


def _synthetic_scaffold():
    # Shape mirrors the scaffold dicts rag.py builds from a parsed TOC:
    # one front-matter entry, two chapters, one back-matter entry.
    return [
        {"title": "Preface", "page": 0, "level": 1},
        {"title": "Chapter 1: Duties to Clients", "page": 1, "level": 1},
        {"title": "chapter 2 - Conflicts", "page": 40, "level": 1},
        {"title": "Index", "page": 90, "level": 1},
    ]


def test_canonical_chapter_titles_characterization():
    scaffold = _synthetic_scaffold()
    chapter_map = {1: {"title": "Duties to Clients", "start_page": 1},
                   2: {"title": "Conflicts", "start_page": 40}}
    result = rag._canonical_chapter_titles(scaffold, chapter_map)
    # Record the EXACT current output of this call as the expected value
    # before making any rag.py change, then never edit this assertion.
    assert result[1].startswith("Chapter 1")
    assert result[2].startswith("Chapter 2")
```

Execution instruction (this is the characterization discipline, not a
placeholder): **run each fixture through the real current function first,
paste the observed output into the assertion as an exact equality, then freeze
the test.** If a fixture doesn't satisfy a function's input contract, adjust
the fixture shape by reading the function body — never weaken the assertion to
`startswith`/`in` once the exact value is known. Cover at minimum:

1. `rag._canonical_chapter_titles` — mixed-case and separator-variant chapter
   titles (as above).
2. The chapter-pattern match loops — one synthetic title per legacy regex in
   the `chapter_patterns` list, plus one non-matching title.
3. Front/back-matter label handling in `_identify_book_sections` — a minimal
   synthetic `doc` dict with a "Preface" page, a "Table of Cases" page early,
   and an "Index" page late (read the function to learn the minimal `doc`
   shape it needs; keep the fixture as small as possible).
4. An **unknown-layout** fixture: a scaffold whose titles match *no* chapter
   pattern. Pin whatever rag.py currently does (this becomes the "safe
   unknown-layout behavior" guarantee).

- [ ] **Step 4: Run the new tests against unmodified rag.py**

Run: `python -m pytest tests/test_structure_profiles_integration.py tests/test_document_profiles.py -q`
Expected: all pass, with `git diff rag.py` still empty.

- [ ] **Step 5: Commit**

```bash
git add document_profiles.py tests/test_document_profiles.py tests/test_structure_profiles_integration.py
git commit -m "Pin legacy structure rules and characterization behavior"
```

---

### Task 4: Thread the profile through rag.py

**Files:**
- Modify: `rag.py`

**Interfaces:**
- Consumes: `document_profiles.get_profile`, `compiled_chapter_patterns`,
  `DEFAULT_PROFILE_NAME`.
- Produces (used by Tasks 5–6):
  - `rag._ACTIVE_STRUCTURE_PROFILE_NAME: str` (module global)
  - `rag._active_structure_profile() -> document_profiles.StructureProfile`
  - every structure function below accepts keyword-only
    `profile: StructureProfile | None = None` and resolves
    `profile = profile or _active_structure_profile()` at call time.

- [ ] **Step 1: Add the facade plumbing**

Next to the existing repo-module imports in `rag.py` (match the
`import chunking_core as _chunking_core` style):

```python
import document_profiles as _document_profiles
```

Near the other module-level configuration globals:

```python
_ACTIVE_STRUCTURE_PROFILE_NAME = _document_profiles.DEFAULT_PROFILE_NAME


def _active_structure_profile() -> _document_profiles.StructureProfile:
    """Resolve the active structure profile at call time (late-binding)."""
    return _document_profiles.get_profile(_ACTIVE_STRUCTURE_PROFILE_NAME)
```

- [ ] **Step 2: Replace each hardcoded rule with a profile lookup**

For every region inventoried in Task 3 Step 1, in this order:

1. Add keyword-only `profile=None` to the function signature and
   `profile = profile or _active_structure_profile()` as the first body line.
2. Replace the hardcoded literal with the corresponding profile field
   (`chapter_patterns` list → `_document_profiles.compiled_chapter_patterns(profile)`;
   label literals → the profile label tuples; the layout-prompt
   "Chapter designation" line → `profile.chapter_pattern_description`; the
   canonical `f"Chapter {chapter_num}: {title}"` construction →
   `profile.canonical_chapter_title_format.format(number=chapter_num, title=title)`).
3. Where an inventoried function calls another inventoried function, pass
   `profile=profile` through explicitly.

Do **not** touch export artifact naming (`front_matter.md` filenames in the
chapter-split writer) — that is output naming, not detection policy.

- [ ] **Step 3: Run the frozen characterization + full suites**

Run: `python -m pytest tests/test_structure_profiles_integration.py -q`
Expected: passes **without editing the test file** (`git diff --stat tests/` empty for it).

Run: `python -m pytest -q`
Expected: full suite green. Any failure means the default profile does not
reproduce legacy behavior — fix the extraction, never the expectation.

- [ ] **Step 4: Commit**

```bash
git add rag.py
git commit -m "Thread document-structure profile through scaffold policy"
```

---

### Task 5: CLI selection and fail-closed provenance

**Files:**
- Modify: `rag.py` (CLI arg + provenance)
- Modify: `tests/test_structure_profiles_integration.py` (append)

**Interfaces:**
- Consumes: Task 4's `_ACTIVE_STRUCTURE_PROFILE_NAME` global.
- Produces: `--structure-profile <name>` CLI option on the ingestion-facing
  commands (`full` and the chunk/convert/resume paths that reach scaffold
  logic — locate the argparse definitions for existing pipeline flags such as
  `--llm-scaffold` and declare the new option adjacent to them, wired to set
  `rag._ACTIVE_STRUCTURE_PROFILE_NAME`); profile name+version recorded in
  chunk-run provenance.

- [ ] **Step 1: Write the failing tests** (append to the integration test file)

```python
import document_profiles as dp
import pytest


def test_unknown_profile_name_fails_closed_before_work():
    with pytest.raises(dp.UnknownProfileError):
        dp.get_profile("no-such-profile")


def test_active_profile_is_late_bound(monkeypatch):
    monkeypatch.setattr(
        rag, "_ACTIVE_STRUCTURE_PROFILE_NAME", dp.DEFAULT_PROFILE_NAME)
    assert rag._active_structure_profile().name == dp.DEFAULT_PROFILE_NAME
```

Add a CLI-level test following the existing patterns in
`tests/test_cli_policy.py` / `tests/test_pipeline_runner.py`: invoking the
pipeline entry with `--structure-profile no-such-profile` must fail with a
clear error **before** any conversion or chunking work starts, and
`--structure-profile us-casebook-v1` must be accepted. Reuse whatever CLI
harness those suites already use rather than inventing a new one.

- [ ] **Step 2: Implement the CLI option**

In the argparse block(s) for the pipeline commands:

```python
parser.add_argument(
    "--structure-profile",
    default=_document_profiles.DEFAULT_PROFILE_NAME,
    help="Document-structure profile for scaffold/front-matter policy "
         f"(registered: {', '.join(sorted(_document_profiles.PROFILES))})")
```

At command start (before conversion/chunking), validate and activate it:

```python
profile = _document_profiles.get_profile(args.structure_profile)
global _ACTIVE_STRUCTURE_PROFILE_NAME
_ACTIVE_STRUCTURE_PROFILE_NAME = profile.name
```

(Adapt to the facade's existing configuration-handling style — if commands
pass settings explicitly instead of via globals, follow that style and keep
the global only as the default.)

- [ ] **Step 3: Join chunk-run provenance**

Find where `max_llm_transport_attempts` enters chunk-completion provenance
(the precedent from the LLM transport-budget milestone: a budget change
invalidates resumable chunk output). Add
`document_profiles.profile_provenance(profile)` fields to the same record so a
profile change invalidates resume the same way. Add a test in the style of the
existing transport-budget provenance tests (find them via
`git grep -n max_llm_transport_attempts tests/`): resuming with a different
profile name or version must not silently reuse chunks.

- [ ] **Step 4: Run tests, full gate, commit**

Run: `python -m pytest -q` then the full verification gate.
Expected: green.

```bash
git add rag.py tests/
git commit -m "Add structure-profile selection with fail-closed provenance"
```

---

### Task 6: Second profile + unknown-layout safety

**Files:**
- Modify: `document_profiles.py` (register `part-based-v1`)
- Modify: `tests/test_document_profiles.py`,
  `tests/test_structure_profiles_integration.py` (append)

**Interfaces:**
- Produces: registered `part-based-v1` profile proving multi-publisher
  configurability (chapters designated "Part I / Part II ..." with roman
  numerals).

- [ ] **Step 1: Write the failing tests**

```python
def test_part_based_profile_matches_part_headings():
    profile = dp.get_profile("part-based-v1")
    compiled = dp.compiled_chapter_patterns(profile)
    assert any(p.search("Part IV: The Judicial Power") for p in compiled)
    assert not any(p.search("Chapter 4: The Judicial Power") for p in compiled)
```

Integration side: run the Task 3 synthetic scaffold fixtures through the
threaded functions with `profile=dp.get_profile("part-based-v1")` and assert
(a) "Part"-designated titles are recognized as chapters, and (b) the
unknown-layout fixture behaves **identically to the pinned legacy fallback**
under both profiles (the safety guarantee: an unmatched layout degrades the
same way regardless of profile).

- [ ] **Step 2: Register the profile**

```python
_PART_BASED_V1 = StructureProfile(
    name="part-based-v1",
    version=1,
    chapter_heading_patterns=(
        r"^Part\s+([IVXLCDM]+)[:.\s]\s*(.+)$",
    ),
    front_matter_labels=_US_CASEBOOK_V1.front_matter_labels,
    back_matter_labels=_US_CASEBOOK_V1.back_matter_labels,
    toc_labels=_US_CASEBOOK_V1.toc_labels,
    canonical_chapter_title_format="Part {number}: {title}",
    chapter_pattern_description=(
        "Top-level divisions designated 'Part <roman numeral>: Title'"),
)
PROFILES[_PART_BASED_V1.name] = _PART_BASED_V1
```

If Task 3 renamed/extended dataclass fields, mirror those here. Note: if the
canonical-title code path assumes integer chapter numbers, record that as a
ROADMAP follow-up limitation for roman-numeral profiles rather than changing
number handling in this milestone — the test then pins the current (possibly
imperfect) behavior explicitly.

- [ ] **Step 3: Run tests, full gate, commit**

```bash
git add document_profiles.py tests/
git commit -m "Register part-based profile and pin unknown-layout safety"
```

---

### Task 7: Real-corpus smoke, docs, and publication

**Files:**
- Modify: `ROADMAP.md`
- Modify: `README.md` (document `--structure-profile` where the pipeline flags
  are documented — find the flag table/section via `grep -n "llm-scaffold" README.md`)

- [ ] **Step 1: Real-artifact no-op smoke**

If the local Ethics artifacts are present, run a resume against the existing
run with the default profile and confirm it reports the corpus unchanged
(0 changed / 1,715 unchanged / 0 removed — same as the PR #31 no-op
evidence). This proves the default profile + provenance change does not
invalidate existing artifacts. If Task 5 provenance necessarily marks legacy
chunk runs stale (missing profile fields), implement the same legacy
compatibility path used for pre-quality-report corpora (absent fields = legacy
run, explicitly logged) and re-run. If artifacts are absent locally, note the
skip in the PR body.

- [ ] **Step 2: Update ROADMAP.md**

Add to the milestone table:

```markdown
| Configurable document-structure profiles | In progress | Front/back-matter labels, chapter patterns, and canonical-title rules live in stdlib-only `document_profiles.py`; default profile reproduces legacy behavior under frozen characterization tests; profile joins chunk-run provenance fail-closed; second registered profile and unknown-layout safety are fixture-tested |
```

and update the P2 row accordingly. Record every defect or limitation noticed
during extraction (e.g., roman-numeral chapter numbers) as follow-up rows.

- [ ] **Step 3: Full verification gate**

Run the six-command gate. Expected: all green (suite grows by the new tests).

- [ ] **Step 4: Commit and publish**

```bash
git add ROADMAP.md README.md
git commit -m "Record document-structure profile milestone status"
git push -u origin agent/document-structure-profiles
```

Open a draft PR titled "Extract document-structure policy behind tested
profiles" whose body records: the behavior-preservation contract, the
characterization-tests-unchanged evidence (`git diff --stat` on the frozen
test file between Task 3 and head), provenance fail-closed behavior, the
no-op resume smoke transcript (or its skip reason), full-suite counts, and
Linux + Windows CI expectations. Watch CI to completion.

---

## Follow-on milestones (historical acceptance notes)

The core context, table, process-supervision, and vector-lifecycle work below
has since been implemented by PRs #36, #37, #33, and #38. These notes remain
useful as original acceptance intent; they are not authorization to recreate or
replace the implemented designs. Remaining evaluation evidence belongs in the
current `ROADMAP.md` backlog after the corpus-owner review boundary is resolved.

**Context-aware retrieval assembly (ROADMAP P2), split in two:**

1. *Stable adjacency/parent identifiers.* Chunk records gain
   `prev_stable_id` / `next_stable_id` (document order, never crossing a
   chapter boundary) alongside the existing `stable_id` / `section_path`.
   Acceptance: identifier derivation never changes existing `stable_id`
   values; quality report gains adjacency-coverage checks (every non-terminal
   record in a chapter lane has resolvable neighbors); index manifest schema
   version bumps with a legacy compatibility path; a regenerated corpus
   passes a no-op second resume.
2. *Query-time neighbor stitching.* A pure function in `retrieval_core`
   assembles top hits plus available neighbors under an explicit token
   budget, deduplicates by `stable_id`, marks stitched neighbors as
   supplementary so citations still point at the ranked hit, and is exposed
   behind an off-by-default CLI flag. Acceptance: offline eval on the pinned
   CC0 suites shows no regression with the flag off; a with/without ablation
   on the Ethics draft queries is reported as diagnostic evidence.

**Table-specific retrieval (ROADMAP P2):** after adjacency identifiers exist —
header-propagated child records for large preserved tables linked to their
parent table chunk, duplicate collapse at fusion time, table-focused relevance
fixtures.

**Process supervision / vector lifecycle (ROADMAP P3):** implemented in draft
PRs #33 and #38. The
[process-supervision ADR](../../architecture/decisions/process-supervision-extraction.md)
records the maintained boundary; the plan sequencing below is historical.

**Explicitly not agent work:** Ethics retrieval calibration (P1) requires
corpus-owner relevance judgments; do not fabricate or extend
`eval_queries_ethics_draft.jsonl` judgments autonomously.
