# Evaluation input-contract dependency boundary

- **Status:** Implemented locally; pending exact-head review and integration
- **Decision date:** 2026-07-24
- **Milestones:** R7 import-DAG gate and the first R8 evaluation slice

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
review workflow. A wholesale move of query validation would be a larger
behavioral change, so this decision deliberately covers only the independently
characterized input helpers.

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

The current dependency shape is:

```text
eval --lazy--> evaluation_release ----\
  |                                    +--> evaluation_inputs --> artifact_io
  `--lazy--> evaluation_review --------/             |                |
                 `--lazy--> eval                     |                v
                                                     `------> storage_policy
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

`eval.load_queries` remains the legacy JSONL parser and then applies
`eval._validate_query`. It is not silently described as strict and was not
moved in this slice. Moving that parser and query/schema validation into a
shared evaluation domain module is the next evaluation-side R8 boundary.
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

## Enforcement and residual work

The tracked-source AST gate fixes the current first-party SCC inventory at
exactly `{eval, evaluation_review}` and `{rag, job_manager}`. It also pins the
leaf's direct/transitive dependencies and the one-way release edge. Subprocess
tests cover all evaluation-module import orders, eager-import isolation, helper
compatibility, strict parsing, snapshot bounds/link rejection, and corpus
normalization/failures.

This is not completion of R7 or R8. `eval.py` and `evaluation_review.py` still
form a lazy cycle until query validation moves inward. Separately,
`job_manager.py` still imports `rag.py`, `rag.py` lazily imports the manager,
and `service_runtime.py` still depends on that facade. Coverage ratchets,
typing, the broader architecture inventory, runtime protocols, and composition
inversion remain planned. This local decision has not been pushed, reviewed,
merged, tagged, or released.
