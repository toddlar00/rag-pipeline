# Measured efficiency changes

2026-09-10. This pass implements bounded Git blob batching and archive-test
seed reuse. The final focused check passed **591 tests and Ruff**, including
all five archive-fixture consumers and 27 Git protocol controls. Another
**20 inert snapshot characterization cases passed**. Application OCR, retrieval,
review and CLI implementations remain unchanged.

## Git verification

`tools/qualification_git.py` verifies the same ordered `HEAD:<path>` requests
using plain `git cat-file --batch`. Expected sizes determine groups that fit
the existing 8 MiB accepted-stdout limit, including framing. Each response must
match the expected object ID, blob type, canonical decimal size, SHA-256,
payload delimiter and exact final EOF. Near-limit singleton blobs retain the
old `git show` route. Failed batches never fall back or retry.

The existing Git runner retains its 60-second timeout and 1 MiB stderr limit.
Its stdout limit is checked after capture; this change does not introduce a
hard capture-memory bound. Batching can change malformed-input failure
precedence, but invalid evidence cannot qualify through a partial batch.

Four fresh-process measurements used the same reviewed Git runner and all
615 raw blobs / 16,220,087 source bytes from the retained simplification-v2
synthetic HEAD. Expected hashes, sizes, stage object IDs, source/helper pins,
both indexes and HEAD/tree were checked. No historical source or index changed.
The balanced order was legacy, batch, batch, legacy, without competing tests.

| Verification method | Git launches | Observed wall time | Python traced allocation peak |
| --- | --- | --- | --- |
| Per-file `show` | 615 | 13.01–13.60 s | 2.00 MiB |
| Bounded batches | 2 | 0.357–0.378 s | 8.08 MiB |

These are blob-verification measurements, including tracing/call-log overhead
and possible cache effects. They do not establish whole-qualification or
application speedup. Python allocation peak excludes child and other process
memory; the increased peak is an explicit tradeoff.

The independently reviewed future copy helper archives the source-pinned module
and executes those verified bytes, then calls the shared verifier at the former
per-file loop. It preserves raw-object creation, private directories, index
priming, source/index/HEAD/tree checks and immutable prior evidence. Its separate
create-only preparation receipt records actual execution; this development
record does not substitute for a preparation or full gate result.

## Test setup

The archive fixture now constructs its invariant historical comparison once in
the existing module fixture and deepcopies the complete seed for each session.
Explicit scoring actions still perform a fresh comparison. The change removes
168 repeated historical calculations across the original module's 169 cases;
an added isolation test verifies that session mutations cannot alter the seed.
All 277 original cases in the two UI modules retain their identities and pass.

A smaller crop-only app fixture was also measured. Saving approximately 0.229
seconds across 15 cases did not justify 35 extra lines, so that variant was
rejected and the original crop-UI test file restored exactly. The combined
before/variant wall times were 33.986 and 31.999 seconds, but unchanged cases
also varied and archive setup totals increased. No isolated archive speedup
is claimed. Rejected source, observations and preimages remain retained.

Detailed review also deferred crop-validator and backend/CLI extraction:
preserving distinct diagnostics, callback order and return values reduced the
actual saving to a few lines while adding configuration or forwarding code.
Full runner/verifier configuration remains a larger, separate artifact-schema
and import-bootstrap change. The stable Git mechanism is a smaller first step.

## Scope and retained evidence

This is a runtime/maintenance improvement, **not a net code-size reduction**:
60 tool lines and 145 protocol-test lines are added, plus eight archive-test
lines for isolation. The archive setup mechanism itself has no net line growth.
No regression test was deleted. The ignored new copy helper adds 23 lines;
historical generated helpers were not deleted to claim savings.

The new helper also exposes a fresh union snapshot with a compatible default
wrapper. Twenty inert cases prove default serialization/command parity, one
read per union member, fresh reads at successive checkpoints, and refusal of
index drift, missing/changed additions and invalid paths. Future finalizer
callers can use this to remove duplicate reads within a checkpoint; no saving
is claimed for a finalizer that has not adopted it.

| Evidence | SHA-256 |
| --- | --- |
| Final 591-case/Ruff receipt | `d46e9b9cdb54f16c7bba271b3d7a3acc36cc5ba40cecec8502dc2ef09665696c` |
| Final JUnit | `7bb9ba215a5e9af15162a0086ec65572bbfa550f3e9c8ab137c39bfbf09bf24b` |
| Final retained readback | `7da929a238b4c063b791ebf443655161de7b7fc3faa9c657424f5825b8ff41c5` |
| Snapshot characterization receipt | `599cfc8a28d825c6d48703da3da101c00678f4824e88226696f0b9db4281aaa0` |
| Git benchmark harness | `6022774d69cc79358eb149ae981a7b59404238b660f153a49480c64f9fb00564` |
| Legacy measurement 1 | `314495fe9441cbbe38956dce14e9e022cce9acae4228a05f1ae5fa894a8ea338` |
| Batch measurement 1 | `25be1d93c117712cee044885c9d33003d8c9f4b7532392667c01e17cb82f94a5` |
| Batch measurement 2 | `af2446a7ad4bd9fb6e0dfa35b59ec124eaec48f020cc6108f18ec2e4f7942ac5` |
| Legacy measurement 2 | `79d789a507a967d1f7fbc6870939632dffc2edc75261c485013aded2bb169497` |

Measurement receipts are in `%LOCALAPPDATA%/rag-pipeline/git-batch-efficiency-v1`;
the final focused run is in its sibling `fixture-efficiency-final-v1`.
Source-review, rejected-variant and snapshot records are linked from
[`tmp/efficiency_execution_2026-09-10.md`](../../tmp/efficiency_execution_2026-09-10.md).

The [prior full consolidation qualification](2026-09-10-codebase-consolidation-qualification.md)
remains unchanged. These new tooling/test changes have focused validation;
that earlier nine-gate pass does not retroactively cover them. Original
baseline/index/history and the existing inventory cap remain unchanged.
At this 2026-09-10 checkpoint, architecture regeneration/review and another
full qualification remained pending; the [follow-up below](#2026-09-11-follow-up)
records the later qualified generation.
All 67 OCR/AI requirement/acceptance pairs remain in scope; this pass does not
complete the broader program or establish representative OCR-quality gains.

## 2026-09-11 follow-up

The later frozen generation passed **all nine local copied-source gates: 14,347
tests passed, seven skipped, six warnings and no failures/errors**. Independent
verification matched all **14,354 collected identities**, **619 source files**
(440 Python; 520 tracked plus 99 admissions), 28 artifacts and all 40 retrieval
predicates. The guided-review graph, baseline-current and real Zettlr cases
passed. The [completion record](../../tmp/efficiency_budget_v1/qualification-completion.json)
binds the actual receipt, JUnit, independent addendum and evidence directory.
Compilation, Ruff, architecture, model-policy and dependency checks, pytest and
all three retrieval gates passed without changing their acceptance thresholds.

Five reviewed code patches remove six unused private definitions, reuse
record-local lineage pages, inline two single-use LLM wrappers, share the
existing digest validator and share the byte-identical model-tool writer.
Non-test Python decreases **160 lines / 5,983 bytes**; meaningful invalid-input
and writer-fault controls add **65 test lines / 2,882 bytes**. The net reduction
is **95 Python lines / 3,101 bytes**, with **ten fewer non-test definitions**. Writer ordering,
cleanup-error precedence and local tool aliases remain; removed private wrapper
names and the writer's former module-global ownership are documented migration
limits. [Per-patch reviews](../../tmp/efficiency_budget_v1/development-checkpoint.json)
retain the development history; no historical helper or regression test was
deleted to claim savings.

The temporary cap increase was superseded by the user's instruction to find
efficiency improvements after each patch. The guard was restored to **1,967,765
bytes**. Paired official generation and named review accepted the same
**1,967,654-byte** inventory, leaving **111 bytes** of headroom and reducing the
previous candidate by 2,315 bytes. This is separate from source-size reduction.
The [ADR](../architecture/decisions/architecture-facade-inventory-policy.md)
now requires per-patch efficiency/capability review; its paragraph adds 852
documentation bytes. Only the copy received the refreshed baseline; original
baseline/index/history and the restored guard stayed unchanged during qualification.

Generated quality characterization compared seven complete before/after reports,
including malformed/empty scopes. For 24 entries, isolated projection medians
at 8/32/128 spans fell from 0.29270/3.12555/40.96185 ms to
0.05150/0.15425/0.47240 ms. One span rose slightly, 0.02205 to 0.02675 ms.
These are projection timings, not whole-report or OCR speedups. LLM report
values and key order remained equal apart from completion time; timings were
essentially unchanged and nonempty cases added **272 Python traced bytes**.
The existing one-sort behavior remains. Neither study establishes process-memory
or whole-pipeline gains; [study pins](../../tmp/efficiency_budget_v1/development-checkpoint.json)
retain the evidence.

The [v4 navigation readback](../../tmp/ocr_navigation_next_v4/browser-readback.json)
and [89-case final focused audit](../../tmp/ocr_navigation_next_v4/focused-verification.json)
cover generated next-unresolved review. Enter/Space/Enter selects displayed
original lines 2/4/2, with explicit wrap, exact duplicate text and corresponding
scan highlights. Unconfirmed reference text survives tab return while its real
update request is held before transmission and after release. Approvals remain
false. Settled positive navigation and one held-request control do not prove
arbitrary races, every approval-reset transition, representative reviewer effort
or broad accessibility. No OCR/model inference ran in this browser evidence.

The earlier focused run remains **772 passed, one Git-cohort failure and one
baseline deselection**; the new copied pass does not erase it. Earlier browser
failures and unsuccessful instrumentation remain retained. The first gate
launcher [failed before creating gate evidence or starting gates](../../tmp/efficiency_budget_v1/launch-gates.failure.json);
the replacement used the reviewed strict Python symlink/attribute/tag checks.
The observed PowerShell/Python metadata disagreement has no established cause.

The seven platform-specific skip reasons remain in the addendum. The log retains
five pytest parametrization deprecations and one `StarletteDeprecationWarning`.
This qualifies the frozen local source, not hosted CI, installed native-binary
attestation or representative OCR accuracy. These document updates follow the
frozen checkpoint. All 67 requirement/acceptance pairs remain intact; later
cleanup/merge/dedup work and the broader OCR/AI program remain pending.
