# Syllabus-Driven Topic Study Packets Design

Date: 2026-08-01
Status: Approved by owner (Cade); gate/interface/safety audit incorporated.
Implementer: Codex executes the implementation plan on
`agent/study-packets-implementation`; the Phase A0
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
budgets, `markdown_validation`, and the repository's output-lease and atomic
write primitives. This
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
  "structure_profile": "us-law-casebook-v1",
  "entries": [
    {
      "id": "week-03-conflicts",
      "title": "Conflicts of Interest — Current Clients",
      "chapters": ["#/texts/42", "..."],
      "queries": ["conflicts of interest current clients", "..."]
    }
  ]
}
```

- Input is bounded before parsing. The implementation sets explicit maximums
  for syllabus bytes, entry count, selectors/queries per entry, and every
  scalar length. JSON is parsed strictly: duplicate object keys and non-finite
  numbers are rejected. Unknown fields, control characters, embedded newlines,
  and leading/trailing or non-canonical whitespace are rejected rather than
  silently normalized.
- `entries[].id` must be a filesystem-safe lowercase slug, unique within the
  file. It cannot be `index` (which would collide with `packets/index.md`) or
  a Windows device name such as `con`, `prn`, `aux`, `nul`, `com1`, or `lpt1`.
- Each entry needs at least one of `chapters` or `queries`.
- `chapters` values are exact occurrence-bound heading IDs from
  `metadata["heading_path_ids"]` (Docling `self_ref` identities). Display
  titles, chapter numbers, normalized heading text, and `section_path`
  prefixes are never identity selectors. A record matches only when the exact
  selector occurs in its `heading_path_ids`; an unresolvable selector is a
  per-entry error (reported, entry skipped, run continues, non-zero exit at
  the end).
- The syllabus references one indexed collection; the packet header records
  the exact index-generation binding it was built from.
- `structure_profile` is a consistency check only: it must equal the
  profile recorded in the collection's chunk receipts, and a mismatch is a
  run-level error before any packet is built.

## Generation-coherence boundary

Packet construction starts through a dedicated strict snapshot facade. While
holding the same `_chunk_output_lease`, it loads the canonical chunk snapshot,
requires and validates the adjacent chunk-completion artifact, and returns the
records, source SHA-256, snapshot fingerprint, and validated
`structure_profile` provenance together. The completion artifact is required,
not optional, and it is never reopened after the lease is released. The
syllabus profile must resolve to the registered `us-law-casebook-v1` profile
and match that receipt exactly.

The index manifest is then required and validated against the request and the
leased snapshot: backend, collection, embedding model, and `source_sha256`
must all match exactly. A missing or malformed manifest, or any mismatch, is a
run-level failure before staging begins. Retrieval hits are identifiers only:
each `stable_id` is remapped to the immutable canonical record from the strict
snapshot, and hit text/metadata are never trusted. Unknown or missing IDs are
reported as degradation notices (and cannot enter the evidence universe);
retrieval warnings and effective-mode degradation are preserved in the packet
header.

## Selection policy

Per entry, deterministically: chapter selectors gather their chunks in
published order; each query adds hybrid-retrieval hits restricted to the
collection with the existing retrieval semantics; results are deduplicated
by stable chunk ID. Chapter selection, query-hit mapping, rules extraction,
case grouping, and outline evidence all canonicalize table families with the
existing table-retrieval policy, so parent/child rows never become competing
or duplicated evidence. A selection that is empty after trusted remapping and
canonicalization is an entry error.

Fixed source, character, group, query-hit, and output budgets bound packet
size. Every truncation and retrieval degradation is recorded in the packet
header, never silently. Each LLM contract receives only the stable IDs that
are actually present in its bounded, prompt-visible evidence JSON; the broader
entry selection is not an allowed citation universe. Every source admitted to
rendered output must also have an exact non-empty locator.

## Section builders

1. **Rules & definitions (extractive, no LLM).** Verbatim quotes from the
   selection's `statutory_excerpt` chunks, plus `table` chunks whose family
   the selection includes (element/comparison tables), each with its page
   locator, in published order. Tables are admitted only as one strict
   captioned pipe table, with literal cells context-safely escaped and the
   canonical publication marker added; trailing non-table blocks fail closed.
   Selection remains label-driven and fully deterministic, with no heuristic
   text matching.
2. **Key-cases digest (LLM under strict contract).** For each
   `case_opinion` group in the selection, one distillation call bounded by
   the same JSON prompt-evidence framing the classification/TOC contracts
   use. The output contract binds `case_name` to the current group and
   requires facts, holding, and significance to carry their own non-empty
   citation lists (or an equivalently strict digest-level set) drawn only
   from that case's prompt-visible IDs. Text and lists are bounded,
   ASCII-safe, control-free, Markdown-safe, and schema-validated even if an
   injected caller returns apparently canonical JSON. One retry on a known
   LLM/runtime/contract failure; on persistent failure the packet may include
   only an exact prompt-visible excerpt with that excerpt's real locator plus
   a visible "digest unavailable" notice. If no safely locatable excerpt
   exists, the digest is omitted or the entry fails; `unknown source` is never
   emitted.
3. **Issue outline/checklist (LLM, grounded fail-closed).** One synthesis
   call over the entry's selection. Contract: an ordered outline whose
   every line carries at least one chunk-ID citation drawn from the
   bounded prompt-visible evidence; lines citing unknown IDs or carrying no
   citation are
   rejected. One retry; on persistent failure the section is omitted with
   a visible notice. Citations render as the book's page locators.

Prompts explicitly mark source text as untrusted evidence, never
instructions. Prompt inputs and accepted outputs reject control characters,
non-ASCII framing violations, and scalar Markdown injection. Known LLM budget,
security-policy, timeout, transport, and strict-output failures degrade through
the documented fallbacks; programmer/invariant errors remain fail-closed.

## Output and validation

- Files: `packets/<entry-id>.md` per entry and `packets/index.md` for the
  course, under the run's private output directory. Corpus-derived content
  never leaves private outputs; nothing new is Git-tracked.
- Every scalar inserted into Markdown (titles, course, case names, locators,
  notices, failure reasons, and binding values) is escaped for its context.
  Every packet and `index.md` must pass the existing Pandoc/Zettlr Markdown
  validation before publication. The build fails closed when the required
  external validator is unavailable; it does not silently downgrade to a
  weaker parser.
- A rerun is a coherent directory-level publication, not a sequence of live
  per-file writes. Under a packet-output lease, all candidate packets and the
  course index are written to a staging directory, validated, and then
  promoted. Previously owned stale packet files are removed safely. The
  validated `index.md` is written last and is the logical commit marker; it
  records the generation/index binding and SHA-256 of each published packet.
  No new receipt or sidecar is introduced. A crash, failed index validation,
  or concurrent/Dropbox-observed rerun must leave either the previous logical
  generation or a detectably incomplete generation, never a falsely committed
  mixed set.
- Packet headers record: course, entry id/title, index-generation binding,
  selection digest (stable hash of the ordered chunk-ID list), generation
  timestamp, and all selection, truncation, retrieval-degradation, and
  fallback notices. A notices footer may repeat them, but is not a substitute
  for the header.

## CLI

`python rag.py packets --syllabus <path> [--collection ...]` plus the
existing shared LLM flags. Defaults follow release security policy
(local-only egress; cloud only under the existing explicit consent flags).
Exit code is non-zero if any entry failed; the summary lists per-entry
outcomes. `packets` is classified as an always-LLM command by `cli_policy`,
so provider kwargs are not discarded. The parser uses `--db` default `None`
so the existing backend-aware default resolver selects Chroma or Qdrant, and
the packets parser participates in the same shared run-telemetry flags as the
other LLM commands. Tests assert provider, security-policy, backend, and
telemetry forwarding.

## Error handling summary

Run-fatal errors occur before publication for invalid/unreadable syllabus,
incoherent snapshot/completion/profile data, missing or mismatched manifest,
output-lease failure, staging/promotion/index-commit failure, and required
validator unavailability. Entry-local errors are unresolved selectors, empty
trusted selections, missing required locators, unsafe source/output text, and
packet Markdown validation failures; they are recorded and other entries may
continue. Known LLM/runtime/contract failures are section-local degradations
through the explicit cited-excerpt/omission fallbacks. Unexpected programmer
or invariant errors are not swallowed as entry failures. Any entry failure
makes the final exit non-zero.

## Testing

- Synthetic CC0 fixture corpus in the existing evaluation-suite style; no
  private-source text in any committed artifact.
- TDD unit coverage in `tests/test_study_packets.py`: syllabus validation
  (accept/reject matrices), deterministic selection and dedup, budget
  truncation recording, both LLM contracts (acceptance, rejection, retry,
  fallback), citation-universe enforcement, Markdown validity of rendered
  packets and course index, safe scalar escaping, missing-locator behavior,
  and CLI wiring (argument parsing, provider/security/telemetry forwarding,
  exit codes, entry-level isolation).
- Integration tests inject crashes at staging, promotion, stale cleanup, and
  final-index commit; exercise concurrent output leases and Dropbox-like
  filesystem errors; verify retrieval-ID remapping and manifest binding; and
  prove that `index.md` is last and its packet hashes match the published set.
- The full dependency-light suite, ruff, compile/policy gates, and the
  architecture-inventory refresh must stay green.

## Codex handoff boundary

Codex implements on `agent/study-packets-implementation` and keeps the local
gates green. It must not: hand-edit lockfiles, benchmarks, or the architecture
inventory
baseline (regenerate the inventory with the tool only); claim any hosted or
Phase A0 result; regenerate Phase A0; open a candidate pull request; edit
`ROADMAP.md`; or touch `.github/workflows/`. Python-source changes
invalidate the Phase A0 baselines by policy — evidence regeneration,
candidate publication, and merge remain with the owner's machine and
follow the established candidate ritual.

## Out of scope

- Practice questions, flashcard/Anki upgrades, NotebookLM/AI-project
  bundling of packets (deferred features).
- Any change to existing export, publication, or receipt machinery.
- R0C, R7, and R12-A (sub-projects 2-4 of the series, each with its own
  spec).
