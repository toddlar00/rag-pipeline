# Phase A0 Baselines

These files are the canonical per-platform Phase A0 baseline candidates for
the R10/A0b architecture gate. They contain no private corpus content or
credentials. Each report covers the complete nine-scenario set with five
fresh-process repetitions.

## Provenance

Both replacement reports were generated from the clean pre-gate source
checkpoint `fdb08d26e3fc9fd2b80b983eeab7b1f353b96707` (tree
`d7e758c6002144acff0c8d58e54c9980a6ffd7c7`). The executing environments were
synchronized with repository-pinned uv 0.11.31 against the exact CPU
application/test lock union plus its retained bootstrapper:

- `requirements-full.lock`
- `requirements-test.lock`
- `requirements-lock-tools.lock`

The Windows environment used a temporary direct, uv-managed CPython
interpreter rather than a virtual-environment redirector so supervised-child
parent identities remained exact. The Linux environment used a direct POSIX
virtual-environment interpreter. `uv pip sync --strict --torch-backend cpu
--require-hashes` and `uv pip check` passed for 189 Windows and 187 Linux
marker-resolved distributions before generation.

| Platform | Runtime | Report | Bytes | File SHA-256 | Embedded report SHA-256 |
|---|---:|---|---:|---|---|
| Windows x86-64 | CPython 3.12.13 | `phase-a0-windows-cpython312.json` | 49,077 | `0363e499ab69570be978d7c714e3c3cf38adf955ad00720927d81b283f1d6ee5` | `3f76c476d2877537fe2da6dd334c40902146b2264d3f61ea5b3c8e7b57239db1` |
| Linux x86-64 | CPython 3.12.3 | `phase-a0-linux-cpython312.json` | 48,488 | `3617fb7a90edabc6e45a6ef6ac694f093171188ea70fb3b6f456253d5119c799` | `d24c4e3444f0c5afb11c76bac65d8148258d842b569dcdf4128b61ea2cb57147` |

The reports attest the same source commit, clean-worktree state, tracked-diff
digest, eight LF and `HEAD`-identical dependency/model-lock inputs, authoritative
nine-scenario set, and five repetitions. Platform, patch-level runtime,
hardware, wall time, and process-only RSS diagnostics are intentionally
platform-specific. Repository Git attributes preserve LF for the reports,
locks, and architecture inventory so their reviewed bytes survive Windows and
Linux checkouts unchanged. The harness independently rejects any lock input
whose worktree bytes differ from the exact blob at `HEAD`.

## Hosted diagnosis and replacement state

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

The replacement reports above are local baseline candidates, and each passes an
independent complete same-platform 9×5 comparison. The gate-only commit that
contains these reports is the final local replacement candidate; its exact
commit/tree must be recorded on PR #44 after creation. No successful hosted
replacement run is claimed. A0b remains pending until both exact-head hosted
cells pass and retain their successful evidence bundles.

## Checking

Provision the exact full/test CPU lock union on CPython 3.12 x86-64, then run
the matching command from a clean checkout. The provisioning commands below
are for an ephemeral CI or disposable managed interpreter only; do not use
`--system` against a durable user Python installation.

```text
python -m pip install --require-hashes -r requirements-lock-tools.lock
uv pip sync --system --strict --torch-backend cpu --require-hashes requirements-full.lock requirements-test.lock requirements-lock-tools.lock
uv pip check --system
python tools/benchmark_phase_a0.py --require-clean --check benchmarks/phase-a0-linux-cpython312.json --output phase-a0-current-linux.json
python tools/benchmark_phase_a0.py --require-clean --check benchmarks/phase-a0-windows-cpython312.json --output phase-a0-current-windows.json
```

Use only the command matching the current operating system. Do not copy one
platform's baseline to the other or hand-edit canonical JSON. Any Python source
or dependency/model-lock change invalidates both baselines and requires a new
clean pre-gate checkpoint plus regeneration on both platforms.

The hosted source-delta gate also requires the pre-gate commit to remain an
ancestor of the final head. Integrate a passing candidate with a
history-preserving merge; a squash or history-rewriting rebase invalidates this
evidence and requires regeneration from the replacement history.

The replacement gate-only delta after `fdb08d2` is limited to the two reviewed
reports and their provenance/status documentation. The repaired workflow,
line-ending policy, and Python gates are already part of the pre-gate source and
were exercised while generating and comparing the baselines. Each successful
hosted cell strictly revalidates the generated report, requires its clean source
identity to equal the exact job head, and builds a verified
two-file evidence directory containing that report and a content-free
`phase-a0-ci-attestation-v1` record. The attestation binds candidate and
pre-gate commit/tree identities, baseline and current-report hashes, normalized
platform, and sorted gate-only paths. CI checks the exact file set and
copied-report hash immediately before upload; missing or extra evidence is an
error, not a warning. Successful evidence is retained for 30 days. A failed
comparison keeps its already-validated candidate report for 7 days; failures
before candidate publication remain content-free in logs and produce no
misleading artifact.
