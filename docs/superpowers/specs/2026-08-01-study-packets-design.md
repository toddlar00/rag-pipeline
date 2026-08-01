# Syllabus-Driven Topic Study Packets Design

Date: 2026-08-01
Status: Approved by owner (Cade) in brainstorming session; pending spec review.
Implementer: Codex executes the implementation plan on a branch; the Phase A0
evidence and candidate publication ritual stays with the owner's machine.
Series: this is sub-project 1 of the agreed sequence — study packets, then
R0C egress least-privilege, then R7 quality-gate expansion, then R12-A
packaging/UX. Each later sub-project gets its own spec and plan.

## Purpose

Build semester-ready topic study packets from an indexed casebook. One
command reads a course syllabus file and emits, per syllabus entry, a
validated Markdown packet containing the topic's black-letter rules and
definitions, a distilled key-cases digest, and a grounded issue
outline/checklist — every claim pinned to the book's page locators. This is
the textbook-side counterpart of the owner's lecture study packets.

## Owner decisions (2026-08-01)

1. Feature scope: topic study packets only. Practice-question generation,
   flashcard upgrades, and NotebookLM bundling are explicitly deferred.
2. Topic input: a syllabus file drives whole-course batch builds.
3. Packet sections: rules & definitions, key-cases digest, issue
   outline/checklist. No practice-questions section.
4. Output: one validated Markdown packet per syllabus entry plus a course
   index page, in the private per-run output directory. No new publication
   or receipt surface.

## Architecture

A new dependency-light, typed policy module `study_packets.py` owns all
deterministic policy: syllabus schema validation, selection resolution,
section assembly, citation binding, Markdown rendering, and the two LLM
output contracts. `rag.py` gains a thin `packets` subcommand that wires the
module to existing collaborators: retrieval (`retrieval_core` context
assembly and chapter/filter isolation), the shared LLM runtime and its
budgets, `markdown_validation`, and `artifact_io` atomic writes. This
follows the repository's established facade-plus-policy-module extraction
pattern; no behavior of existing commands changes.

## Syllabus contract (`study-syllabus-v1`)

A strict, versioned JSON document parsed with the standard library and
validated fail-closed (unknown fields, wrong types, empty selectors, or
duplicate entry ids are errors):

```json
{
  "schema": "study-syllabus-v1",
  "course": "Legal Ethics",
  "structure_profile": "<registered profile id>",
  "entries": [
    {
      "id": "week-03-conflicts",
      "title": "Conflicts of Interest — Current Clients",
      "chapters": ["<heading-lineage selector>", "..."],
      "queries": ["conflicts of interest current clients", "..."]
    }
  ]
}
```

- `entries[].id` must be a filesystem-safe slug, unique within the file.
- Each entry needs at least one of `chapters` or `queries`.
- `chapters` selectors resolve against the occurrence-bound heading lineage
  recorded in the chunks; an unresolvable selector is a per-entry error
  (reported, entry skipped, run continues, non-zero exit at the end).
- The syllabus references one indexed collection; the packet header records
  the exact index-generation binding it was built from.
- `structure_profile` is a consistency check only: it must equal the
  profile recorded in the collection's chunk receipts, and a mismatch is a
  run-level error before any packet is built.

## Selection policy

Per entry, deterministically: chapter selectors gather their chunks in
published order; each query adds hybrid-retrieval hits restricted to the
collection with the existing retrieval semantics; results are deduplicated
by stable chunk ID and table families collapse per the existing
table-retrieval policy. Fixed per-section chunk budgets bound packet size;
truncation is recorded in the packet, never silent. The final selection
(chunk IDs plus locators) is the citation universe for all three sections.

## Section builders

1. **Rules & definitions (extractive, no LLM).** Verbatim quotes from the
   selection's `statutory_excerpt` chunks, plus `table` chunks whose family
   the selection includes (element/comparison tables), each with its page
   locator, in published order. Label-driven and fully deterministic; no
   heuristic text matching.
2. **Key-cases digest (LLM under strict contract).** For each
   `case_opinion` group in the selection, one distillation call bounded by
   the same JSON prompt-evidence framing the classification/TOC contracts
   use. The output contract requires exactly: case name, facts, holding,
   significance — bounded lengths, ASCII-safe framing, schema-validated.
   One retry on contract violation; on persistent failure the packet
   includes a cited verbatim excerpt for that case plus a visible
   "digest unavailable" notice. Never silent, never uncited.
3. **Issue outline/checklist (LLM, grounded fail-closed).** One synthesis
   call over the entry's selection. Contract: an ordered outline whose
   every line carries at least one chunk-ID citation drawn from the
   selection; lines citing unknown IDs or carrying no citation are
   rejected. One retry; on persistent failure the section is omitted with
   a visible notice. Citations render as the book's page locators.

## Output and validation

- Files: `packets/<entry-id>.md` per entry and `packets/index.md` for the
  course, under the run's private output directory. Corpus-derived content
  never leaves private outputs; nothing new is Git-tracked.
- Every packet must pass the existing Pandoc/Zettlr Markdown validation
  before publication; a validation failure fails that entry, not the run.
- Writes are atomic via `artifact_io`; a rerun atomically replaces packets.
- Packet headers record: course, entry id/title, index-generation binding,
  selection digest (stable hash of the ordered chunk-ID list), generation
  timestamp, and per-section truncation/fallback notices.

## CLI

`python rag.py packets --syllabus <path> [--collection ...]` plus the
existing shared LLM flags. Defaults follow release security policy
(local-only egress; cloud only under the existing explicit consent flags).
Exit code is non-zero if any entry failed; the summary lists per-entry
outcomes.

## Error handling summary

Fail-closed at every trust boundary: invalid syllabus → no run; bad
selector, failed validation, or persistent contract failure → entry-level
failure with recorded reason; LLM unavailability degrades the two LLM
sections per their fallback rules while the extractive section still
publishes. No partial file is ever observable (atomic writes only).

## Testing

- Synthetic CC0 fixture corpus in the existing evaluation-suite style; no
  private-source text in any committed artifact.
- TDD unit coverage in `tests/test_study_packets.py`: syllabus validation
  (accept/reject matrices), deterministic selection and dedup, budget
  truncation recording, both LLM contracts (acceptance, rejection, retry,
  fallback), citation-universe enforcement, Markdown validity of rendered
  packets, atomic-write failure injection, and CLI wiring (argument
  parsing, exit codes, entry-level isolation).
- The full dependency-light suite, ruff, compile/policy gates, and the
  architecture-inventory refresh must stay green.

## Codex handoff boundary

Codex implements on a feature branch and keeps the local gates green. It
must not: hand-edit lockfiles, benchmarks, or the architecture inventory
baseline (regenerate the inventory with the tool only); claim any hosted or
Phase A0 result; or touch `.github/workflows/`. Python-source changes
invalidate the Phase A0 baselines by policy — evidence regeneration,
candidate publication, and merge remain with the owner's machine and
follow the established candidate ritual.

## Out of scope

- Practice questions, flashcard/Anki upgrades, NotebookLM/AI-project
  bundling of packets (deferred features).
- Any change to existing export, publication, or receipt machinery.
- R0C, R7, and R12-A (sub-projects 2-4 of the series, each with its own
  spec).
