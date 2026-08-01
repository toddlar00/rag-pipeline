# Document-structure profile boundary

- **Status:** Accepted in draft implementation
- **Implementation:** PR #35 (`agent/document-structure-profiles`)
- **Historical input:** `docs/superpowers/plans/2026-07-23-codex-improvements-structure-profiles.md`

## Context

Document hierarchy, front/back matter, canonical titles, cross-references, and
classification were formerly a set of casebook-specific rules distributed
through `rag.py`. The historical Claude plan correctly proposed one reviewed
profile object, but its sketches also allowed a mutable active profile, runtime
fallback, and compatibility with evidence that did not attest the selected
profile. Those choices would make concurrent runs and resumed publication
dependent on ambient process state.

## Decision

`document_profiles.py` is the standard-library-only policy authority. A caller
selects a registered immutable `StructureProfile` explicitly and passes it down
the call graph. The registry is code-reviewed; arbitrary runtime JSON is not a
profile-loading surface. `us-law-casebook-v1` is the production-qualified
default. `roman-parts-book-v1` is registered for controlled qualification but
remains synthetic-only until R11 records authorized real-corpus evidence.

Profile name, schema, revision, and canonical digest join chunk parameters and
receipts. Publication and resume validate that binding. Unknown profiles,
layouts with no recognized primary divisions, missing legacy profile evidence,
and digest/revision mismatch fail closed or require regeneration; they do not
silently inherit the default.

## Invariants

- Selection is explicit per operation and never stored in mutable global state.
- Every structure-sensitive stage receives the same resolved profile: TOC and
  division parsing, canonicalization, classification, quality, and export.
- Concurrent operations cannot change each other's profile.
- A profile-policy change changes provenance and invalidates stale derived
  artifacts through the normal rebuild path.
- Registration is not a production-support claim; qualification is a separate
  corpus-owner evidence decision.

## Consequences

The default profile can preserve characterized behavior while new layout
families remain reviewable and reproducible. Schema migration is stricter than
the historical plan proposed: older evidence without the complete binding is
rebuilt rather than guessed compatible. Adding a profile requires code review,
synthetic fixtures, quality/failure coverage, and the R11 qualification record
before production support is advertised.

## Rejected alternatives

- A module-global active profile: unsafe for concurrent jobs and tests.
- Auto-detecting or falling back from an unknown layout: converts uncertainty
  into silently incorrect structure.
- User-supplied runtime profile JSON: expands the trusted policy and provenance
  surface without review.
- Treating legacy receipts as profile-compatible: they cannot prove which
  structure rules produced the artifacts.
