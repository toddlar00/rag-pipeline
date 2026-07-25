# Phase A0 Baselines

These files are the canonical per-platform Phase A0 baseline candidates for
the current documentation-roadmap successor. They contain no private corpus
content or credentials. Each report covers the complete nine-scenario set with
five fresh-process repetitions. Because the report contract binds the complete
clean source identity, a documentation-only successor still requires its own
reports even though it does not change Python behavior.

## Current documentation-successor provenance

Both current reports were generated from the clean documentation-successor
pre-gate checkpoint `c1161ed21d2f0815fa9d555faf7210cc3d6f6315` (tree
`e6ac49dc865ca42bbe38e4cf25bb09abb06d0fe9`). That checkpoint descends from
the frozen R1 candidate and reconciles the roadmap, audit, and status
documentation without changing Python source, CI behavior, attributes, or any
dependency/model lock. The executing environments were synchronized with
repository-pinned uv 0.11.31 against the exact CPU application/test lock union
plus its retained bootstrapper:

- `requirements-full.lock`
- `requirements-test.lock`
- `requirements-lock-tools.lock`

Windows and Linux both used disposable, direct uv-managed CPython 3.12.13
interpreters rather than virtual-environment redirectors, so supervised-child
parent identities remained exact. Hash-locked `uv pip sync --strict
--torch-backend cpu --require-hashes` and `uv pip check` passed for 189 Windows
and 187 Linux marker-resolved distributions before generation.

| Platform | Runtime | Report | Bytes | File SHA-256 | Embedded report SHA-256 |
|---|---:|---|---:|---|---|
| Windows x86-64 | CPython 3.12.13 | `phase-a0-windows-cpython312.json` | 49,082 | `37e5ca7a7fd01d4b85295e008692fa4db138f63bbbd1419c25040bdeabe23474` | `fceda35dc18b999644f97d3e5e1a0893e399172460ef53870d71fa721799045d` |
| Linux x86-64 (WSL2 kernel) | CPython 3.12.13 | `phase-a0-linux-cpython312.json` | 48,491 | `186b2dc2df21ea31b1bc8c0c0f251b943820ffb820e127b3aee5c12d8a919773` | `2fddf8cf432cbc22eacefaece7fe48afde50ea61ca2f684f2be832a8705d6830` |

The reports attest the same source commit, clean-worktree state, tracked-diff
digest, eight LF and `HEAD`-identical dependency/model-lock inputs, authoritative
nine-scenario set, and five repetitions. Platform, patch-level runtime,
hardware, wall time, and process-only RSS diagnostics are intentionally
platform-specific. Repository Git attributes preserve LF for the reports,
locks, and architecture inventory so their reviewed bytes survive Windows and
Linux checkouts unchanged. The harness independently rejects any lock input
whose worktree bytes differ from the exact blob at `HEAD`.

## Frozen R1 publication record

The first published frozen candidate, `ba9c66d`, did not satisfy A0b. Its hosted
A0 cells exposed checkout line-ending drift in lock bytes; a Linux source-gate
cell also showed that host-native `pathlib` parsing accepted the Windows
drive-relative inventory entry `C:escape.py`, while the Windows full unit cell
found CRLF drift in the architecture inventory. Those are gate defects, not
benchmark regressions, and the old baselines are invalid for the repaired
source.

Checkpoint `7594f8b` repairs those defects with platform-neutral lexical source
path validation, explicit LF attributes, and the lock-versus-`HEAD` invariant.
It also gives failures stable content-free stages and diagnostic codes, writes
a validated candidate report before baseline comparison, and makes pull-request
A0 jobs check out the exact PR head. Subsequent Python 3.10-3.14 qualification
found additional interpreter-only drift in `ast.TryStar`, lazy `sysconfig`
initialization, inherited `Path.home`, the public `pathlib.Path` identity,
`typing.Any`, implicit optional annotations, and nested forward references.
Pre-gate source `fdb08d2` closes those cases without weakening home/tilde
denial. Its architecture baseline reproduces on Windows and Linux across
Python 3.10-3.14. Fresh locked full suites pass 2,233 tests with 7 skips on
Windows and 2,236 tests with 4 skips on native Linux; the sole warning is the
R2-owned Starlette/httpx dependency deprecation. The canonical architecture
inventory records 1,981 functions and 377 compact runtime callables.

The predecessor reports at these paths are preserved by Git in frozen R1 gate
head `ed2995e` / tree `c438c82`. Each passed an independent complete
same-platform 9×5 comparison, and pull-request plus direct-dispatch
Windows/Linux hosted cells passed at that exact head. All four retained
two-file evidence bundles were downloaded and independently verified. Human
review and history-preserving R1 integration remain pending. The current files
do not rewrite that evidence; they bind the documentation successor's distinct
clean source identity.

## Documentation-successor evidence state

The current reports each passed complete 9×5 generation from pre-gate source
`c1161ed`. The gate-only successor delta is limited to these two reports and
their permitted provenance/status documentation. Independent same-platform
comparison at the final candidate and exact-PR-head hosted Windows/Linux cells
must pass before this successor can be integrated. Human review and a
history-preserving merge remain required.

## Checking

Provision the exact full/test CPU lock union on CPython 3.12 x86-64, then run
the matching command from a clean checkout. The provisioning commands below
are for an ephemeral CI or disposable managed interpreter only; never pass
`--break-system-packages` to a durable user Python installation.

```text
<disposable-python> -m pip install --require-hashes -r requirements-lock-tools.lock
uv pip sync --python <disposable-python> --break-system-packages --strict --torch-backend cpu --require-hashes requirements-full.lock requirements-test.lock requirements-lock-tools.lock
uv pip check --python <disposable-python>
<disposable-python> tools/benchmark_phase_a0.py --require-clean --check benchmarks/phase-a0-linux-cpython312.json --output phase-a0-current-linux.json
<disposable-python> tools/benchmark_phase_a0.py --require-clean --check benchmarks/phase-a0-windows-cpython312.json --output phase-a0-current-windows.json
```

Use only the command matching the current operating system. Do not copy one
platform's baseline to the other or hand-edit canonical JSON. Any Python source
or dependency/model-lock change invalidates both baselines and requires a new
clean pre-gate checkpoint plus regeneration on both platforms.

The hosted source-delta gate also requires the pre-gate commit to remain an
ancestor of the final head. Integrate a passing candidate with a
history-preserving merge; a squash or history-rewriting rebase invalidates this
evidence and requires regeneration from the replacement history.

For the documentation successor, the gate-only delta after `c1161ed` is
limited to the two reviewed reports and their provenance/status documentation.
The repaired workflow, line-ending policy, and Python gates are already part of
the pre-gate source and were exercised while generating the baselines. Each
successful hosted cell strictly revalidates the generated report, requires its
clean source identity to equal the exact job head, and builds a verified
two-file evidence directory containing that report and a content-free
`phase-a0-ci-attestation-v1` record. The attestation binds candidate and
pre-gate commit/tree identities, baseline and current-report hashes, normalized
platform, and sorted gate-only paths. CI checks the exact file set and
copied-report hash immediately before upload; missing or extra evidence is an
error, not a warning. Successful evidence is retained for 30 days. A failed
comparison keeps its already-validated candidate report for 7 days; failures
before candidate publication remain content-free in logs and produce no
misleading artifact.
