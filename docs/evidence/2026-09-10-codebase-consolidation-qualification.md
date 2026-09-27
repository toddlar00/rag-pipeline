# Codebase consolidation qualification

2026-09-10. The shared JSON preflight and bounded file-snapshot consolidation
**passed all nine local copied-source gates**: **14,275 tests passed, seven
skipped, six warnings and zero failures/errors**. Independent verification
matched all **14,282 collected identities**, **615 admitted source files**,
**28 retained artifacts** and **40 retrieval predicates**. Pytest reported
2,365.41 seconds; the runner observed 2,380.125 seconds for that process.

The maintained Python change removes **40 lines and 942 bytes net**, including
permanent regressions and the approved guard-comment adjustment. No production
module or first-party import edge was added. This establishes a bounded local
regression result; elapsed-time or representative OCR-quality improvement was
not measured. The broader 67-requirement OCR/AI program remains unfinished.

## Behavior and development evidence

JSON preflight now resides in the existing crop-comparison module, which the
uncertainty journal already imports. Each caller retains its byte encoder,
limits, validation/error order and exception policy. In particular, journal
finite-float checks and narrower integer bounds remain distinct from comparison
serialization. Characterization caught a custom-metaclass equality difference;
it was corrected before freezing and retained as a permanent regression.

Omission, checkpoint and cleanup readers share the bounded snapshot mechanism
in `storage_policy.py`: two bounded hash passes on one handle, stable identity
and path/link checks. Caller-specific parsing, error translation, retention and
final comparison order remain local. The stage reader retains its distinct
policy. Live identity/chunk lookups and ordinary member monkeypatch seams were
preserved; whole-module alias replacement was not an admitted compatibility seam.

Production changes remove 81 lines and 2,651 bytes. Permanent regressions add
41 lines and 1,710 bytes: two golden cases, twelve encoder-stage/error cases and
the metaclass regression. The approved guard comment removes one additional
byte. Focused validation passed **1,389 tests with one Windows skip**, and lint
passed. The characterization probe and focused receipt remain retained; an
earlier selector error collected no tests and is not counted as validation.

## Frozen cohort and exact cap

The cohort is **520 original tracked files + 95 explicit admissions = 615 files**,
including **438 Python files**, under
`%LOCALAPPDATA%/rag-pipeline/ocr-simplification-qualification-v2/worktree`.
Its sibling `evidence/gates-v1` contains the completed gate artifacts and
verification addendum. The user approved **1,967,738 → 1,967,765 bytes**, exactly
27 additional inventory bytes with zero extra headroom. That guard/comment
change was applied before freezing; no further approval is pending.

Two independently reviewed official generations produced the identical
1,967,765-byte inventory, with the existing acyclic graph of **187 modules and
607 edges**. The copy alone received the reviewed baseline. Its content is
byte-identical to the earlier simplification candidate that exceeded the old
cap by 27 bytes. The held v1 copy and completed evidence remain immutable.

The original architecture baseline, raw/logical Git index, HEAD and tree stayed
unchanged. The copied cohort matches the frozen origin union except for the
approved copied baseline. Source, helper and interpreter checks passed before,
between and after gates; the independent verifier also checked current source
and index correspondence before publishing its addendum. These checks attest
observed bytes at checkpoints, not continuously loaded code or metadata.

## Full result and retained limitations

The nine gates are pytest, Ruff, Python-source compilation, dependency policy,
model-artifact policy, architecture inventory, and the three offline BM25
retrieval suites (property, constitutional law and table family). All exited
zero with unchanged-source/helper flags. The independent verifier reconciled
every collected identity with JUnit and recomputed all 40 CI threshold and
baseline-regression predicates from the retained reports and pinned inputs.

The required real Zettlr multiline-reference/srcset case passed. The seven
skips concern Windows symlink permissions, newline paths, three POSIX process
tests and two POSIX permission tests. Six warnings remain: five pytest
parameter-iterator deprecations and one Starlette/httpx deprecation.

The combined-preview launcher case that failed in the [earlier source-cleanup
attempt](2026-09-09-source-cleanup-qualification-failure.md) passed in this full
run. The old failure remains unexplained and unwaived; this passing observation
does not supply a retrospective cause or invalidate that failed receipt.

The runner recorded zero handle-ctime drift checks. The verifier recorded eight
checks involving four origin paths (`.gitignore`, inventory, guard and index),
without content or required identity drift. These are check counts, not counts
of distinct mutations, under the already reviewed Windows metadata policy.

The executable was pinned to qualified Python 3.12.10. Installed packages,
Node/dependency bytes, system site initialization and remaining local Git
configuration were not independently attested. The real Zettlr validator used
its explicit source-bound origin JavaScript root. This is local copied-source
qualification, not an original default tracked-source run, hosted CI, release,
representative OCR accuracy or performance qualification. No new model or
private corpus was used for this consolidation assessment.

## Evidence bindings

The full runner completed once, session 40298, exit 0 (`64af96`). Root observed
the completed receipt hash separately (`de9409`), then invoked the reviewed
success-only verifier once, exit 0 (`f10639`). No gate was replayed for
verification. The verifier addendum is separate from the original receipt.

| Retained binding | SHA-256 |
| --- | --- |
| Full nine-gate receipt | `54e94ea7cecfe0dfbbb5649444d6cb47c0e1b3446c843cac58fb0a2e040e81fb` |
| Independent verification addendum | `9b2b47470959b457cdf7dda8c8e1c0c071a2f5a577705058116d3b4da30cd4a6` |
| Collection record | `6c5fc78880e456cbac895ec051e53c9058a110fbbd628721d5734acc1457daba` |
| JUnit XML | `8c025e3d7b690aaacf0fb24e0052a56275dff8aa2fabdbe4dd2ac133aaf9ee89` |
| Pytest log | `af03e4d12758dd3e21c69bc48157129e3eb21e824e188cb791939bed47251fc4` |
| Matching source-before/after snapshots | `c6eaa56e31f212a74febe00a755e22054f9a7ebaa7db63984c2c33f72cbd1359` |
| Source freeze v2 | `1c4bdd8f8e68bf5e36ad196b8e75b6597ea0fa7b52d1cdc886d048bd51a3cdf9` |
| Copy preparation | `b22ef5116ed4825bde1a23edc4ac81ed15523cb6f3b43b319b5d25160324e071` |
| Both generation receipts | `fd192a2614dd37f13722d161944d723d1678c518a01c21f3fb3d3b28ed677456` |
| Reviewed copied architecture | `13d853815e81db1dc3d05b26ebdb61b46cd69a2f27fd224284ffdf3d67f5b88d` |
| Named architecture review v2 | `8d9efa7ab53e01e24605740ac8c4279232b448cf7d614d1cee15f0d45a2f53a6` |
| Approved guard in both roots | `f5ab2075a734fed57598fe30610a12c821fd6e1f5235c023f514718a9c6a47e3` |
| Finalization receipt | `21ecfc4804eb79605e290032d9501ef7cecc50bc32f289a0db512325544dcad6` |
| Final provenance | `0b662e3eb9bbacc43fbc2b51f558cfb979ce9b013f9746e8a3f65157a7ff03cd` |
| Reviewed runner | `583767ec18a242e10cc5a1b91ee944b97921506ecb2a8ae586fbcd5c93463cbf` |
| Independent verifier | `6a84b820b8697d831d424e807c53da39fdd825c259c719976f22cdca0d10cc3b` |
| Qualified executable | `0b471133e110cfb53a061cad528ce8e517d7b9ac41a0a396c39ad795a487fc14` |
| Original baseline, unchanged | `8c9d9991aa6864131915b6f0392ae4bf4c3f9a4af5e8ae0757e78e9d47aa792e` |
| Original raw index, unchanged | `6a841daa2ac15f397750fc0f88f1bd6e93b78326823d2452e3ab14c88d0335f3` |
| Copied raw index, unchanged | `7c7540be2bc06babce23ac39a7380439df513d8f737f016214ca2312e01635e9` |

The [further efficiency assessment](../../tmp/codebase_efficiency_followup_2026-09-10.md)
ranks Git batching, narrower test fixtures, small crop validators, backend/CLI
preparation and stable qualification commands. Those additional changes remain
proposals. It distinguishes counted duplication from unmeasured runtime benefit
and specifies the behavioral checks each consolidation must retain.

This report and the updated program status were written after successful
verification. They are outside the frozen source cohort and are not
retroactively covered by the completed run. All 67 ordered requirement and
acceptance pairs remain unchanged; only the cleanup-fidelity status gains this
new qualification evidence.
