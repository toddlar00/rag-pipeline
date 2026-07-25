# Evaluation input-contract dependency boundary

- **Status:** Implemented locally; pending exact-head review and integration
- **Decision date:** 2026-07-24
- **Milestones:** R7 import-DAG gate and R8a/R8b evaluation inversion

## Context

The evaluation entry points shared strict snapshot, JSON-object, digest, and
corpus-binding behavior through private functions defined in
`evaluation_review.py`. `evaluation_release.py` imported that application only
to reuse those functions, while `evaluation_review.py` imported `eval.py`
lazily for query validation and `eval.py` imported both applications lazily.
The resulting first-party strongly connected component was:

```text
eval --lazy--> evaluation_release --> evaluation_review --lazy--> eval
  `----------------------------------> evaluation_review
```

That direction made a release-policy import load the private owner-review
application and obscured which behavior was an input contract rather than a
review workflow. A wholesale move of query validation was a larger behavioral
change, so R8a first covered only the independently characterized input
helpers. R8b followed after the legacy loader, query validation, corpus
policies, and review consumers had explicit characterization coverage.

## Decision

`evaluation_inputs.py` now owns four shared helpers:

- `_hex_digest` validates one canonical lowercase SHA-256 string;
- `_strict_json_bytes` parses one bounded UTF-8 JSON object while rejecting
  duplicate fields and non-finite numbers;
- `_read_snapshot` rejects link-like path components, then delegates to the
  exact, single-generation artifact snapshot reader; and
- `_corpus_contract` requires every query to bind the same digest, positive
  record count, and non-empty stable-ID scheme.

The module imports only Python's standard library and the existing
standard-library-only `artifact_io` and `storage_policy` leaves. It does not
import the evaluator, review/release applications, retrieval policy, model
policy, runtime composition, or physical clients. The module objects are
imported eagerly, while calls resolve their attributes at runtime; existing
failure injection can therefore replace those attributes without copying or
bypassing the underlying security checks.

R8b adds `evaluation_queries.py` as the dependency-closed query domain. It owns
the query schema, finite-number helper, required/optional corpus-pin checks,
snapshot comparison, judged-ID and table-family attestation, grounding-evidence
binding, and their slug/answer/payload helpers. Its only first-party
dependencies are `evaluation_contract`, `retrieval_core`, and
`table_retrieval_core`; it does not import the evaluator, owner review, release
policy, `rag`, model policy, orchestration, providers, or physical clients.

The current dependency shape is:

```text
eval ----------------> evaluation_queries <------ evaluation_review
 |                                                  |
 |--lazy receipt----------------------------------->|
 |                                                  v
 `--lazy policy--> evaluation_release ------> evaluation_inputs
                                                       |
                                                       v
                                                 artifact_io
                                                       |
                                                       v
                                                 storage_policy
```

`evaluation_release.py` therefore no longer imports
`evaluation_review.py`. The review module explicitly re-exports the four leaf
functions under their former private names, and its own calls continue through
those names. Existing Python callers retain the same call signatures and the
same results for established valid inputs. The aliases are object-identical to
the leaf functions; their implementation module and traceback location
necessarily change, and patching a former review alias is not an injection
mechanism for release-policy loading. The controlled rejection paths described
below are deliberate hardening, not a claim of error-for-error compatibility
for malformed inputs that previously escaped through incidental exceptions.

`evaluation_review.py` no longer imports `eval.py`, even lazily. It calls the
query domain for strict-record schema checks and full input/corpus validation.
`eval.py` preserves all 13 moved private function names and five constants as
object-identical aliases, while retaining evaluator/runtime composition. The
legacy `load_queries` implementation and `_query_snapshot_sha256` cache remain
in `eval.py`: successful loads still publish the SHA-256 of the exact raw bytes
under the resolved path only after every row validates, and existing CLI tests
can still replace the eval-local `load_queries` or `_validate_query` name.
Nested calls inside the moved functions resolve sibling helpers in
`evaluation_queries`; failure injection for those private internals must patch
that owning module rather than rebinding a same-named `eval` alias. No tracked
consumer depended on the former cross-helper rebinding seam.

## Strict and legacy parsing surfaces

The strict parser is used for owner-review JSON/JSONL inputs and release-policy
objects. It counts the original bytes, permits a UTF-8 BOM, requires exactly
one top-level object, rejects duplicate fields at every object depth, and
rejects `NaN`, `Infinity`, and `-Infinity`. A lightweight string-aware scan
caps array/object nesting at 64 before recursive decoding.

Python's JSON decoder can also turn a syntactically standard exponent such as
`1e999` into an infinite float without invoking `parse_constant`. The extracted
parser now supplies finite numeric hooks: floating-point overflow receives the
same labelled non-finite-number error family, and an integer whose magnitude
cannot be represented in the finite runtime range receives a controlled
labelled error. Decoder recursion errors are normalized instead of escaping.
These depth/numeric checks and controlled corpus-field type failures are the
intentional input hardening in the slice; accepted finite values retain their
normal `int` or `float` result types.

`eval.load_queries` remains the legacy JSONL parser and then applies the
`evaluation_queries._validate_query` compatibility alias. It is not
silently described as strict and was not moved in R8b. It still performs direct
raw-byte reads, UTF-8-SIG decoding, blank-line skipping with physical line
numbers, ordinary `json.loads`, legacy-capitalized errors, and success-only
digest publication. Duplicate-key and otherwise unused non-standard-number
behavior therefore remains intentionally distinct from strict owner-review
parsing. Any strictification requires a separate input-policy migration.
Its relevance validator, the direct release-policy validator, and the baseline
metric loader now use guarded finite conversion so oversized Python integers
produce their labelled `ValueError` contracts rather than leaking
`OverflowError`; the legacy parser's other JSON behaviors are unchanged.

## Byte and schema invariants

- Review/release byte ceilings remain owned by their existing constants and
  are passed unchanged into the leaf.
- Snapshot bytes and their SHA-256 come from the same verified file generation;
  the extraction does not replace that operation with separate reads.
- Link components are rejected as observed before the snapshot open. The check
  and open remain separate operations, so this is not an operating-system
  no-follow guarantee against a malicious local path-replacement race. Such a
  threat would require component-pinned/no-follow opening or post-open reparse
  validation in a separate storage-policy milestone.
- Canonical review packet, approved-query, receipt, release-policy, report, and
  baseline serialization is unchanged. No schema version or field set changes.
- Corpus digests remain case-normalized to lowercase; record counts reject
  booleans and non-positive values; ID schemes retain their established
  trimming and cross-query consistency rules. Malformed digest, count, and
  scheme types now fail with the same controlled contract error families
  instead of leaking incidental attribute/hash errors.
- Compatibility aliases preserve the former `evaluation_review._...` call
  surface while new consumers import the leaf directly.
- The three corpus policies remain separate: general query schema accepts a
  digest, count, or both; required pin coverage demands both on every query;
  owner review/release additionally requires one common digest, count, and ID
  scheme. Validation is observational and does not normalize authored query
  objects or their canonical bytes.

## Enforcement and residual work

The tracked-source AST gate now requires an acyclic first-party import graph.
It pins both shared domains' direct/transitive dependencies, the one-way
release edge, and review's inability to reach `eval.py` transitively.
Subprocess tests cover all evaluation-module import orders, eager-import
isolation, helper compatibility, strict parsing, snapshot bounds/link
rejection, and full review input validation without loading the evaluator.
Characterization also fixes legacy loader bytes/types/errors/digest timing,
eval-local validator injection, query non-mutation, the three corpus policies,
and strict-versus-legacy duplicate-key behavior.

This completes the evaluation-side dependency inversion, not R7 or all of R8.
The later R8c-1 runtime binding removes `job_manager.py`'s former `rag.py`
import and therefore the last import cycle, but `service_runtime.py` still
depends on the facade and application composition has not moved to one root.
Coverage ratchets, typing, the broader architecture inventory, remaining
runtime protocols, and service/root composition inversion remain planned. This
local decision has not been pushed, reviewed, merged, tagged, or released.
