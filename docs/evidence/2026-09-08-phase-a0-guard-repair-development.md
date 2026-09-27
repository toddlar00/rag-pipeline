# Phase A0 guarded CLI startup repair: development checkpoint

Date: 2026-09-08. The two benchmark tests that failed in the
[native-repair qualification](2026-09-08-ocr-uncertainty-repair-qualification-failure.md)
now pass in a fresh private copied-source regression run. **168 tests passed,
three existing POSIX-only tests skipped on Windows, zero failures**, in 91.82
seconds of pytest time. This is scoped development evidence, **not a new full
qualification**, OCR accuracy improvement, release, or completion of the
[67-requirement program](../ocr-improvement-program.md).

## Confirmed reproduction and repair

A generated-only instrumented reproduction retained the actual nested launch
boundary. The real CLI exited zero with empty stderr and confirmed cleanup;
the benchmark rejected its missing second guard event. The outer process had
all seven benchmark startup selectors, while its supervised child lacked
`PYTHONPATH`, `PYTHONUSERBASE`, `PYTHONHASHSEED` and `PYTHONIOENCODING`.
The three production-safe defaults remained. Only one unsupervised `rag-info`
guard event was recorded.

This demonstrates an incompatibility between the benchmark's inherited guard
bootstrap and intentional inherited-Python-setting scrubbing in
`process_supervision._run_cli_with_deadline`. The diagnostic observer delegates
the actual `python_worker_launch` once and returns its command/environment
unchanged. It is not a retrospective observation of the original failed run's
deleted inner trace. Peirce independently reviewed this distinction and the
retained diagnostic joins; no diagnostic rerun was used to replace a failure.

The repair changes only benchmark startup plumbing. CLI-info guard generation
explicitly embeds seven fixed settings derived from its generated fixture and
guard roots, and passes them through the existing trusted environment-override
parameter of the real entrypoint. Role, outer/inner state, script and supervisor
configuration restrict forwarding. Conflicting startup overrides are refused;
unrelated explicit overrides, arguments, return values and exception identity
are preserved. Production supervision and ambient scrubbing are unchanged.
All other probes retain the original guard source and import behavior. The
supervision-bypass negative control still runs after the forwarding hook.

Twenty new regression cases cover the fixed mapping, hostile/mixed-case
selectors, default guard, scope, conflicts, delegation and a real two-hop
guard/sysconfig control. That generated control verifies all seven settings,
generated user-base handling, linked guard events and home/tilde/socket/DNS
denials. It complements—not substitutes for—the unchanged actual CLI-positive
and bypass-negative tests. Peirce independently approved source and test changes.
The initial focused run passed 22 tests with 55 deselected; Ruff passed.

## Five-repetition performance observations

The full smoke test completed all nine scenarios, five fresh-process repetitions
each, on 64-bit Windows CPython 3.12. Its original benchmark function was called
once with unchanged arguments; a node-scoped pytest hook retained the validated
report and returned that same object. Anscombe independently reviewed the
capture wrapper, report and JUnit outcomes.

| Scenario | Median wall ms | p95 wall ms | Median process peak RSS MiB |
| --- | ---: | ---: | ---: |
| Cold RAG import | 1047.822 | 1081.384 | 108.660 |
| CLI help | 1061.105 | 1070.836 | 110.328 |
| CLI info, empty output | 2174.010 | 2199.104 | 25.934 |
| Service composition | 429.983 | 439.209 | 47.938 |
| Worker imports | 1074.632 | 1089.945 | 109.426 |
| Worker entrypoints | 2224.681 | 2230.040 | 26.875 |
| Isolation guard | 262.998 | 279.814 | 25.852 |
| No-op resume | 3944.527 | 3987.456 | 108.938 |
| Offline export/retrieval | 1055.460 | 1071.475 | 109.418 |

Wall time includes probe startup, supervision and fixture work, not just the
named operation. RSS measures the benchmark-probe process only and excludes
descendants; it is not total process-tree memory. With five samples, the
nearest-rank p95 is the maximum. These are machine-specific diagnostics, not
paired speed improvements, long-run tail estimates, native OCR throughput or
representative corpus measurements. No new OCR experiment ran here.

## Scoped run, skips and source identity

The six files contributed 171 JUnit cases: Phase A0 benchmark 77, process
supervision 42, supervision module 17, environment 14, Python worker launch 15,
and job-manager Python launch six. All benchmark cases passed. The three skips
are unchanged `os.name == "nt"` exclusions in `test_process_supervision.py`:

- `test_posix_watchdog_normal_exit_ignores_inherited_sigterm_disposition`;
- `test_posix_watchdog_kills_worker_tree_when_supervisor_is_killed`;
- `test_posix_escalation_kills_descendant_that_ignores_sigterm`.

The actual supervised pytest exit was zero; its process duration was 92.141
seconds. The retaining runner deliberately returned one because its conservative
summary requires review of **any** skip. Root and Anscombe matched all three
to existing platform guards; this is not a failed pytest test or a hidden retry.
Terminal `b305aa` retains both statuses. No rerun occurred.

The private copy contains 585 files / 424 Python sources. Source bytes and both
original/copy raw indexes matched before and after the test run. No original
Git status/diff, index mutation, commit, architecture-baseline update or new full
architecture generation was performed. Copy-only Git identity supports the
benchmark report; it is not the original repository's commit or hosted CI.

- Original HEAD: `e34103f70b676eacc8d55badb2468b8a10004ef4`;
  tree: `af0a69ccde82b6b679580f07813495721978efe0`.
- Original raw index: `6a841daa2ac15f397750fc0f88f1bd6e93b78326823d2452e3ab14c88d0335f3`.
- Synthetic copy HEAD: `3d99ab7a635ef156a7751509354c7ed9a9260d80`;
  tree: `b72537bd8029949d997eb11dfba2b0a2da4c1794`.
- Copy raw index: `02967ec2782d17a19f4ddbbe9ef0014199f7933320d7c75a33786b212e5d3a9e`.

Local generated artifacts are retained under
`%LOCALAPPDATA%/rag-pipeline/phase-a0-guard-diagnostic-v1/`
and `%LOCALAPPDATA%/rag-pipeline/phase-a0-repair-tests-v1/evidence/`.
The regression artifacts below are in `scoped-regression-v1/`.

| Artifact | SHA-256 |
| --- | --- |
| Diagnostic `receipt.json` | `0d7f7dd67f698adfee97e766d34842c02db14fbbfceca908c3cc49a26afda5ce` |
| Copy `copy-preparation.json` | `bebbf167537fd2a60a52164349114511f70fcf68efb0fec45ddcaa49813597d3` |
| Regression `receipt.json` | `9e9c8700d8eef1af89bb286ef75e16f690076371a929e26f1c9b18d39d85d62e` |
| `phase-a0-smoke-report.json` | `0460271412ac58372795574d5c6697f8332020806363da11d1a092d9d1ef5c6c` |
| `pytest.xml` | `d8868dad0dfd4b76e53b6d3346f1c0067de459e5ff3948d31b3d5a98187fba1f` |
| `pytest.log` | `e581428c9fff189b0a3e0e61c881c4c4222b483787af6bb96937f5ee607ad038` |
| `tools/benchmark_phase_a0.py` | `bef061b8714e35ce1de39aa62f6c09eec51bd3567b841902894cf9f7ca75f8e1` |
| `tests/test_phase_a0_benchmark.py` | `fafd829da76873e94d3dd1993acd89b6825ef8347d1ce69876dbcf765f076776` |
| Unchanged `process_supervision.py` | `dbec069eab58ac8e07441ada1c37c914f86cddcc3380a439e94ce14d877ddafc` |

The current [crop-pack guide](../ocr-crop-review-packs.md) was also corrected:
visual v2 editing is implemented and demonstrated on generated fixtures, but
later implementation is not yet freshly qualified. Historical failed evidence
is preserved. This new record and subsequent ledger/program status edits were
not in the frozen test cohort. Next: fresh full qualification, then clearer
long-Save feedback and the remaining OCR/AI requirements. Representative source,
reference-retention and AI disclosure choices remain unresolved; no model
acquisition, canonical publication, broader AI authority or usage reset occurred.
