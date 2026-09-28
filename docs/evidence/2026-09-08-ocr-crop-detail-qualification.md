# Crop-detail copied-source qualification, 2026-09-08

All nine local v2 gates, the retained-artifact verifier and the final independent
agent audit passed. Pytest reported **11,839 passed, seven skipped and six
warnings in 1,461.15 seconds (24 minutes 21 seconds)**. The full 11,846 collected
test identities reconcile with JUnit; this is not a count inferred from progress.

This checkpoint covers fixed Fit/288-DPI/576-DPI source-detail rendering,
profile-bound isolated preview requests, presentation and draft-preserving
reload controls, and typed-reference feedback in the live/archive crop panels.
The [development record](2026-09-08-ocr-crop-detail-development.md) separately
preserves generated browser/native observations and their failures and limits.
No new representative-document accuracy experiment was performed here.

This is a **local copied-source Windows/Python-3.12 checkpoint**, not an original
tracked-index gate pass, hosted CI, authenticated human review or release. It
does not complete the [full OCR/AI program](../ocr-improvement-program.md),
authorize correction publication or indexing, or broaden AI-reader authority.

## Source scope and identities

The private qualification copy is
`%LOCALAPPDATA%/rag-pipeline/ocr-crop-detail-qualification-v1/worktree`;
its sibling `evidence/` retains the preparation and both gate generations.

- The copy contains **545 admitted files, including 396 Python sources**:
  520 tracked original working-tree files plus 25 reviewed additions (six
  production modules, 13 test modules and six documents). Exact names and hashes
  are retained in the provenance and source snapshots. Ignored private PDFs,
  runtime outputs, model caches and original Git storage were not copied.
- Original dirty-worktree base HEAD is
  `e34103f70b676eacc8d55badb2468b8a10004ef4`, tree
  `af0a69ccde82b6b679580f07813495721978efe0`. These IDs alone do not contain the
  new working-tree implementation.
- The copy owns synthetic HEAD `a8943a0852fcf8c3f5926ca5708251ba16f514fb`, tree
  `fefbfb3bd00bc39d79a807af23dfae1d51004905`. It is not project history or a
  release commit. Every admitted blob matched its copied source before the
  separately approved copy-only architecture replacement.
- The sole origin/copy source-content difference at qualification is
  `architecture-inventory.json`. The original baseline remains
  `8c9d9991aa6864131915b6f0392ae4bf4c3f9a4af5e8ae0757e78e9d47aa792e`.
  The existing architecture size guard was not changed.
- Original raw index SHA-256 is
  `6a841daa2ac15f397750fc0f88f1bd6e93b78326823d2452e3ab14c88d0335f3`;
  logical-entry SHA-256 is
  `affc4116b70e1a0f5dceedab99de84135c9924226eb039df4ee5fbd4c69708f2`.
  The original index and history were not modified. The default original
  tracked-source inventory still excludes the admitted untracked additions.

The v2 before/after source snapshots are byte-identical, SHA-256
`817e4791d283bf44a66dcf180abc6f7b6d2ba572b64e551590aa7560c47f28e7`.
Their computed Git blob is `77a4562935c59902ba76fb736026dfe87e3f2e8a`; computing
it did not write an object. Both repositories' complete source maps, raw and
logical indexes, HEAD/tree and tracked status stayed equal throughout the gates
and final checks. These are point-in-time checks, not continuous metadata or
loaded/native-byte attestation. This record and linked guide updates follow
that frozen checkpoint; they were not part of its source snapshot.

## Reviewed architecture and retained bindings

Two fresh generations and an independent regeneration produced identical
1,872,205-byte architecture candidates. Root and Peirce read the complete
96-change keyed diff and full presentation diff against the preceding qualified
copied baseline. The expected profile/presentation/feedback changes added one
presentation module and five inward edges; whole facade, consumer and runtime
contract sections remained unchanged. Only the copy received the approved
candidate. The unchanged 1,881,119-byte test guard retained 8,914 bytes of room.

| Retained item | SHA-256 |
| --- | --- |
| `copy-preparation.json` | `dd3435dbf3f01654239c3bdaf3b517fffb74c63a9b2fe63bbc19a1c8e6d5f27c` |
| Original `provenance.json` | `a225b81f1c89325f8541ed526611fb5067f150b2410775844f21b257aeafa53d` |
| Approved copied architecture | `8e83592b9ce78ef4401e20735df08f93879bb8b95e984d4628e91605c458887b` |
| Independent architecture-review summary | `069db3bafaeec114a4fac918763de121d7cb85d8e54d85d8278ec2bed0e77604` |
| `provenance-v2-preparation.json` | `553f0ed58b065875fdb0fda8953231239f168ff7213f98cd1d6e804e30360de4` |
| `provenance-v2.json` | `0d26c507707656e8b1a5ee5f3929f01b8571108faa107ae5e899119fb5219976` |
| `copy-index-before-v2.bin` (54,239 bytes) | `5cb779b098ec2b9b943f39ff25591c841937970b9586b405f0253048118b8142` |
| Reviewed v2 gate runner | `84bb1715350fdb5a2852e02916cf34c3866928a9a4374ee8abf2f265723d84d6` |
| Reviewed v2 verifier | `2206a1169346c95e2b4236aa9cf5e2045eab210678be0759af37a768cc07433f` |
| `gates-v2/receipt.json` | `bf52eae9048ec7a6fa57d026b41e0edc4a778565ba8aca5e6b56f6c54c771b37` |
| `gates-v2/verification-addendum-v2.json` | `3d5277e1c4f18f47e7554ebecb54c26d71884ba63e0d68cc94f17c83ee712c36` |

## Executed checks

The qualified Python executable SHA-256 is
`0b471133e110cfb53a061cad528ce8e517d7b9ac41a0a396c39ad795a487fc14`.
Python 3.12.10 and pytest 9.1.1 were used. The exact interpreter, source cohort,
helper, environment policy and command arguments are retained in the receipts.
System/global Git configuration and inherited Git/Python selection overrides
were excluded; third-party pytest plugin autoload was disabled. The fixed
existing Zettlr validator installation was explicitly available. Installed Node
dependency and native-library bytes are not fully attested by these checks.

All nine gates exited zero: pytest, Ruff, compilation, dependency policy, model
artifact policy, architecture inventory, and the property, constitutional-law
and table-family offline retrieval suites. Compilation covered 396 Python
sources; architecture reported 173 non-test modules and 3,911 functions.
Pytest subprocess duration was 1,473.516 seconds; its own reported test duration
was 1,461.15 seconds. The distinct durations must not be conflated.

The independent collection ran before pytest and contained 11,846 ordered,
unique identities, digest
`73311fc3439fc2375540ca3dccda9a56ee19ac282ac21e47459f5f488a20550e`.
All 13 admitted test files were represented; the four new detail/feedback test
files contributed 215 cases, with no skips. The seven overall skips concern
Windows/POSIX path, symlink, process-group/watchdog or permission differences.
The real `test_multiline_reference_and_srcset_fixtures_are_zettlr_valid` case
passed rather than being skipped.

The success-only verifier independently read the 28 initial gate artifacts,
reconciled every JUnit/collection identity, rechecked all 40 current CI retrieval
predicates (12/12/16), and checked query/input and baseline-configuration
bindings. Peirce's final retained audit repeated the artifact, identity,
predicate and complete current-source/Git checks. Agent review is not human
transcription adjudication. Root runner terminal: `4d557b`; verifier terminal:
`39f85f`; final independent audit terminal: `c78a06`, all exit zero.

The runner retained 188 within-handle ctime-drift checks (128 details, 60 omitted)
under its predeclared Windows metadata policy; these count checks, not distinct
mutations. The verifier and final policy-reader audit recorded zero such checks.
Content/path identity, byte checks and both raw-index freezes remained strict.

## Failed v1 and audit-only refusals remain preserved

The first full run passed 11,839 tests, with seven skips and six warnings, in
1,502.67 seconds, but failed its copied raw-index freeze after pytest. Its eight
remaining gates did not run. Failed receipt SHA-256:
`a6aea28f3f0316ddf01cbe44a075a33461383bff3e452eb5e3a49aa71188e08d`.
No success verifier was run on that failure and no v1 result was composed into v2.

Both pre/post binary indexes were independently decoded. Exactly 544 of 545
entries populated previously zero ctime, mtime and size-cache fields. Paths,
object IDs, flags, modes and TREE extension bytes stayed identical, and both
checksums validated. The deliberately modified architecture baseline was the
sole unchanged entry. All source bytes and original Git state remained equal.
This establishes a metadata-only transition, not the historical writer's identity.

The before-index SHA-256 was
`66e4ec983c07d13d596dfecdaff1496411e5523a4c8d30c7e3b62d6004f5c8af`;
the after-index is the v2 starting archive listed above. A reviewed create-only
adapter checked that this was the only change, archived the existing after-index
without modifying it, and created separate v2 provenance. V2 then reran all nine
gates from scratch. Future fresh copies should populate their own stat cache
before freezing; neither index repair nor weakening of the raw-index freeze
checks was used here.

The final independent audit initially selected an architecture-reader helper
that rejected the known Windows executable pathname/handle mode difference
(`0dd8bc`). It completed with the already approved fixed-executable read policy;
no gate, source or verifier was changed or rerun. A tool-string syntax error
prevented a separate composed read command from launching and executed no
filesystem code. An earlier index-audit size assertion wrongly included the
intentionally modified baseline; the corrected audit explicitly retained that
exception rather than treating its cached size as current file size.

## Outstanding limits and next implementation

The original intermittent live-preview `input_changed` refusal remains
unexplained. A successful explicit retry and later stable hashes do not identify
its cause. The disabled diagnostic launcher and unexecuted launcher-test draft
are not operational evidence and are not part of this qualification.

Separate source review identified a deterministic image-cleanup gap: late
verification, cancellation or cleanup refusal can occur after image allocation
but before ownership transfers to the caller. The same-crop runtime, controller
and direct coordinator have paths without an explicit close; existing negative
tests check refusal, not disposal. Close-unless-transferred hardening is next.
This is not a measured persistent memory leak or the cause of the old refusal,
and the passing suite does not resolve it.

Representative accuracy, independent reference adjudication, explicit reference
uncertainty, broader platform/accessibility and total-memory evidence, and the
remaining OCR/AI requirements stay pending. The full 67-requirement objective
remains active; this checkpoint is not a release or a completion claim.
