# Source-aware cleanup observations

The source-normalization observer passed independent source/test review and
495 focused checks, including 106 new observer checks. It is included in the
later [passing copied-source qualification](evidence/2026-09-10-efficiency-development.md#2026-09-11-follow-up).
The separate chunk-deduplication profile also passed the
[copied-source qualification](#copied-source-qualification) below.

`source_cleanup_audit` observes actual calls to
`rag._normalize_source_chunk_text`. It records the original and returned text,
the attempted cleanup passes, reversible text edits, and available primitive
context. It uses the existing cleanup policy and callbacks.

```python
import rag
from source_cleanup_audit import SourceCleanupCollector, capture_source_cleanup

collector = SourceCleanupCollector()
with capture_source_cleanup(collector):
    result = rag._normalize_source_chunk_text(
        "2003", "text", preserve_source_identity=True
    )
report = collector.report()
```

Each collector supports one capture. Read its detached report after leaving
the context. Nested captures use distinct capture IDs and restore the previous
capture on exit. Frame identity is the pair of capture ID and frame ID.

## Reading an observation

The report uses `schema_version: 1`, `kind: "source_cleanup_observation"`, and
`profile: "source_normalization_v1"`. Each wrapper invocation has its own frame.
Check its status before interpreting edits:

| Status | Meaning |
| --- | --- |
| `complete` | The observed text history and selected output pass are complete within the declared bounds. |
| `failed` | The wrapper raised an exception; it has no committed successful return. |
| `abstained` | A budget or unsupported transformation prevented a complete trace; unavailable text/history fields are cleared and a reason is retained. |

`complete` does not mean the cleanup was semantically correct. Context events
record a site, an `observed` flag and a value. Opaque raw objects have
`observed: false` and a null value; their properties, truthiness or string
conversions are not repeated for observation. A later primitive value produced
by the actual cleanup can be recorded separately. Complete text history does
not imply that every source object's internals were observed or authenticated.

Passes distinguish auxiliary source normalization from output processing.
Ordinary processing can be discarded when it erases an identity-preserved
numeric fragment; the numeric rescue then starts from the original input.
Footnote markers use their separate protected pass. Exactly one successful
output pass is committed. Auxiliary and discarded passes are useful evidence,
but their edits must not be counted as changes to the returned wrapper text.

Pass `steps` describe changes to that pass's text. Core `operations` retain
their own detailed `edits` and parent operation IDs, including nested callback
work. These are two views of the same processing: do not sum both layers as
independent damage. A parent callback's edits can also include a nested core
operation's effects; parent and child histories are not independent final-text
changes to add together. Each changed step includes before/after hashes, replacement
spans, and removed/inserted text. Temporary Markdown protection and restoration
can produce edits whose final net effect is unchanged. Callback rule IDs
attribute the callback as a whole, without claiming its internal rules.

## Bounds and scope

`LIMITS` is immutable. The collector allows 64 frames, depth 8, 4,096 input
characters, 16,384 characters per intermediate state, 4,096 retained event
reservations, 65,536 payload characters per frame, and 262,144 per capture.
Payload accounting includes repeated input/output snapshots and context
strings. Fixed record metadata and hashes are separately constrained by the
frame/event bounds. Exhaustion stops observation while ordinary cleanup
continues. This is a trace budget, not a limit on the cleanup's own work.

The scope is source-aware wrapper cleanup. Earlier watermark removal, later
indent restoration/filtering, chunk splitting/merging/deduplication, and
representative semantic fidelity still need separate evidence. This API does
not change the existing `core_normalization_v1` replay API or add a CLI/UI.

See the [development record](evidence/2026-09-09-source-cleanup-observer-development.md)
and the broader [OCR improvement program](ocr-improvement-program.md).

## Separate chunk-deduplication observation

The `chunk_deduplication_v1` profile observes actual `rag._deduplicate_chunks`
calls. It is separate from both normalization profiles above:

```python
from chunk_dedup_audit import ChunkDedupCollector, capture_chunk_dedup

collector = ChunkDedupCollector()
with capture_chunk_dedup(collector):
    retained = rag._deduplicate_chunks(chunks)
report = collector.report()
```

Each frame records the exact string consumed by each fingerprint call, its
SHA-256, and its actual kept/removed disposition. Zero-based input ordinals
identify occurrences in iteration order, including repeated appearances of one
object and changes to the input list made by existing callbacks. Each removal
points to the earlier retained occurrence that actually satisfied the existing
deduplication policy. `output_ordinals` preserves returned order. Ordinals are
local to that frame; they are not persistent source IDs or pre-call list indexes.

A terminal observation after successful removal logging binds the ledger to
the returned list identity and count. Reports contain no live input records.
Consumed text is not a snapshot of later mutable record contents or metadata.
The profile does not reconstruct every comparison or explain policy refusals;
it preserves the current source-identity and table-occurrence protections.

The collector is single-use and reports are detached. Capture applies only to
its active thread/context; it does not propagate into parallel enrichment.
Nested calls retain ancestry. A saved context cannot change a closed frame;
a new call while its collector remains active starts a new root frame.
An empty report does not establish pipeline coverage.

Limits are 64 frames, depth 8, 4,096 consumed occurrences per frame, 4,096
characters per string, and 16,384 events / 262,144 retained text characters
per capture. Repeated strings count separately. Unsupported text, a replaced
core, or exhausted observation explicitly abstains while ordinary deduplication
continues. Original exceptions propagate and are recorded as failed calls;
an already-abstained frame keeps that status with the exception type recorded.
Trace limits do not cap the cleanup operation's own work.

Focused generated evidence and the later copied-source qualification are
recorded below.
Complete observation is not a semantic-fidelity verdict. Watermark removal,
indent restoration/filtering, splitting/merging, threaded normalization coverage,
publication and representative semantic judgments remain separate work.

### Development evidence and efficiency

Independent source review and 495 focused checks passed, including 29 new
observer cases. The test wrapper's final verification failed because it split
namespace separators inside five IPv6 parameter names. A separate retained
verification matched all 495 actual JUnit identities, all three test phases,
lint and 622 source/index pins; the original failed receipt is preserved.
One later record-lifetime regression passed separately, with test-module lint.
That is 496 distinct checks across two frozen generations, including 30 new
observer cases. These focused runs preceded the copied-source qualification below.

The generated cost study compared the actual prechange dedup functions with
the current facade, both without capture and with capture plus detached report
creation. All four inputs returned identical objects in identical order.
Nine batches rotated method order; these are warmed in-process medians:

| Generated input | Prechange (ms) | Capture off (ms) | Capture and report (ms) |
| --- | ---: | ---: | ---: |
| Empty | 0.00036 | 0.00064 | 0.01333 |
| 512 short chunks | 0.13998 | 0.14274 | 2.10613 |
| 256 duplicates | 2.05284 | 2.14138 | 3.43172 |
| 64 source-protected chunks | 5.65330 | 5.41180 | 5.87747 |

The timing differences do not establish a speed improvement. Capture-off
traced peaks increased by 440–936 bytes on the three nonempty inputs; on those nonempty inputs, active
capture plus report peaked at 149,220–313,100 bytes. These measurements exclude
input construction/import and do not measure process RSS or whole-pipeline
performance. No OCR engine or representative source was used.

This capability adds 200 production Python lines / 8,599 bytes and 689 test
lines / 27,280 bytes: net Python growth of 889 lines / 35,879 bytes. It adds
one standard-library-only production module and reuses the actual dedup loop.
The ledger stores primitive text/disposition evidence without copying input
records, rereading their fields or repeating similarity decisions. Separate
normalization and collection lifecycles retain their own contracts; combining
their collectors has no demonstrated maintenance or runtime benefit here.
The reviewed canonical inventory is 1,972,968 bytes, 5,314 bytes above the
previous qualified candidate. The original baseline remains unchanged, and
there is no growth cap.

Exact source pins, review findings, generated measurements, retained wrapper
failure and independent verification are in
[`tmp/chunk_dedup_observer_v1/`](../tmp/chunk_dedup_observer_v1/).

### Copied-source qualification

The frozen chunk-deduplication generation passed all nine gates: **14,377 tests
passed, seven skipped and six warnings**. Independent verification matched all
14,384 collected identities, 621 source files (442 Python), 28 retained artifacts
and 40 retrieval predicates. The required real Zettlr validation case passed.
The reviewed architecture has 189 modules and 608 edges and is acyclic.

The [qualification completion record](../tmp/ocr_chunk_dedup_uncapped_qualification_v1/qualification-completion.json)
binds the exact candidate, runner, receipt and verifier hashes, with the skip
reasons and source checkpoint. The refreshed baseline was installed only in the
fresh copied worktree; the original baseline, index and history remain unchanged.
These documentation updates follow that qualified source checkpoint. The earlier
focused wrapper failure and its separate verification remain retained above.

The runner observed 2,595.641 seconds for this pytest gate, versus 2,261.422 seconds
for the previous qualified run. This is an uncontrolled single-run comparison;
it establishes neither a causal slowdown nor a speed improvement. The generated
cost study above retains its narrower scope. The [efficiency follow-up](../tmp/ocr_chunk_dedup_uncapped_qualification_v1/efficiency-followup.md)
records unimplemented candidates reviewed during the frozen run.

This qualification does not establish representative semantic fidelity or finish
the 67-requirement program. Other cleanup stages, merges and representative
source evidence remain pending.

### Subsequent observer simplification (2026-09-11)

The sole-call `_record` method was folded into `observe`, and inactive capture
now avoids frame lookup: **two production lines / 35 bytes removed**, with all
tests unchanged. Fresh focused validation passed **496 tests**, with no skips,
errors or failures, and Ruff passed. Setup, call and teardown each recorded
496 passes. This later generation has focused validation; the copied-source
qualification above remains evidence for its earlier frozen generation. No new
canonical inventory or full qualification is claimed here.

The same facade and callbacks were compared with separate before/after observer
globals in 12 balanced timing batches. Returned object/order controls and exact
reports matched. Medians in milliseconds per call are:

| Generated input | Off before | Off after | Capture + report before | Capture + report after |
| --- | ---: | ---: | ---: | ---: |
| Empty | 0.000586 | 0.000576 | 0.011827 | 0.012159 |
| 512 short chunks | 0.118602 | 0.120034 | 1.948220 | 1.857057 |
| 256 duplicates | 1.954450 | 1.930127 | 2.911034 | 2.885246 |
| 64 source-protected chunks | 4.497217 | 4.546395 | 4.962732 | 4.882905 |

Imports and input construction are excluded; active timing includes detached
report creation. Capture-off changes are mixed and empty active capture became
slower. These finite generated results establish no broad performance gain.

A separate construction-only fixture probe measured 163 builds at **80.9246 ms**
versus one seed build plus 163 reuses at **0.4056 ms**. The roughly 80.5 ms saving
was too small to justify additional fixture machinery, so caching was not
implemented. This probe excludes per-case file/digest reads and real IO; it
does not identify IO's causal share of test time.

Exact inputs, scripts and retained evidence are linked from the
[local-call development controls](../tmp/local_call_efficiency_v1/).
