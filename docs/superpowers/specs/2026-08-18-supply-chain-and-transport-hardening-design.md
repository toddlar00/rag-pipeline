# Supply-Chain and Transport Hardening Design

Date: 2026-08-18
Status: Designed and implemented in an autonomous goal session ("make
robust and harden the code"); validated by independent subagent review
and the full repository gate battery. The owner review gate is the merge
decision, as with the prior cycles.
Origin: the 2026-08-15 scan recorded the scheduled supply-chain audit
failing with three new advisories against locked transitive
dependencies; this cycle closes them and hardens two robustness
papercuts found while surveying the transport and logging seams.

## Purpose

1. Close the three live advisories — aiohttp PYSEC-2026-3545 (fixed
   3.14.3), cryptography PYSEC-2026-3552 (fixed 50.0.0), h2
   PYSEC-2026-3628 (fixed 4.4.1) — through a targeted, policy-compliant
   lock regeneration that moves only those transitive records.
2. Add a fail-safe timeout backstop to the two policy transport helpers
   so an untimed caller can never hang a run indefinitely.
3. Stop per-page inspection-issue warning floods (thousands of lines on
   a systematic per-page failure) by aggregating repeats.

## Design

### 1. Targeted transitive lock refresh

- `tools/refresh_locks.py` gains a repeatable `--upgrade-package NAME`
  option passed through to `uv pip compile`; it is rejected when
  combined with `--upgrade` (which already moves everything). Locks
  remain generated only by this tool.
- Probe-verified on 2026-08-18 against the current manifests: with pin
  preservation from the existing output files, upgrading exactly
  `aiohttp`, `cryptography`, and `h2` moves exactly those three resolved
  records in `requirements-full.lock` (3.14.3 / 50.0.0 / 4.4.1) and
  nothing else. The regeneration covers all seven governed locks; locks
  that do not contain the targets are byte-identical.
- Domain-policy compliance: no direct requirement record changes —
  transitive packages are explicitly not direct policy inputs
  (dependency-compatibility-domains ADR), and the Dependabot #72
  precedent already established the policy-compliant
  `tools/refresh_locks.py` pass. A second regeneration must be
  byte-stable before the locks are committed.
- No vulnerability-policy exception changes: the three advisories become
  clean; the existing chromadb PYSEC-2026-311 acceptance (expires
  2026-08-31) and the two `+cpu` torch audit skips are untouched.
- Lock changes invalidate both Phase A0 baselines: both provisioned
  environments are re-synchronized against the new lock union
  (`uv pip check` must pass) before the full suites and the paired
  evidence regeneration run at the merged checkpoint.

### 2. Transport timeout backstop

- `rag.py`'s `_post_loopback_without_environment` and
  `_post_cloud_with_policy` currently default `stream=True` but leave
  `timeout` entirely to callers. Every production caller passes an
  explicit timeout today; the backstop
  `kwargs.setdefault("timeout", _TRANSPORT_FALLBACK_TIMEOUT_SECONDS)`
  (new module constant, 300.0 seconds) converts a future untimed call
  from an indefinite hang into a bounded failure without changing any
  explicit caller's behavior.
- The constant is advisory-infrastructure, not a receipt or contract
  input; no schema changes.

### 3. Inspection-issue warning aggregation

- The scan and preprocess paths log one WARNING per
  `PDFInspectionIssue`; a systematic per-page failure (for example a
  PyMuPDF build whose texttrace raises on every page) emits one line per
  page. A new `rag.py` helper `_log_inspection_issues(issues)` groups
  issues by stage, logs the first three of each stage verbatim through
  the existing message forms, then one summary line
  `... and N more pages with <stage> inspection issues` per stage.
  Both call sites route through it; ordering within a stage is
  preserved; three or fewer issues per stage log exactly as today.

## Error handling summary

Item 1 fails closed at every step: resolution failure, non-byte-stable
second regeneration, direct-record drift, or environment resync failure
aborts the cycle with the locks untouched. Items 2 and 3 are pure
robustness seams: the backstop only engages for callers that omitted a
timeout, and aggregation never drops information silently — the summary
line always carries the exact remainder count.

## Testing

- Item 1: unit tests for `lock_commands` covering `--upgrade-package`
  passthrough (repeatable, ordering, rejection alongside `--upgrade`);
  lock-content assertions happen operationally (fixed versions selected,
  three-record delta, byte-stable second run) and are recorded in the
  cycle evidence rather than as committed tests, since tests must not
  invoke network resolution.
- Item 2: tests that an untimed call through each helper receives the
  fallback timeout and that an explicit timeout passes through unchanged
  (transport stubbed; no network).
- Item 3: caplog tests for below-threshold passthrough, above-threshold
  aggregation with exact summary counts, and multi-stage independence.
- Full repository gate battery green on both platforms with the new
  locks; offline eval suites unchanged; fresh paired Phase A0 evidence
  at the merged checkpoint.

## Out of scope

- Any direct dependency movement (the ordered one-domain backlog is
  unchanged; targets were refreshed by the 2026-08-15 scan).
- The chromadb PYSEC-2026-311 acceptance decision (owner call, expires
  2026-08-31).
- R0A/R0B/R0C security programs and content-aware secret scanning (R7).
