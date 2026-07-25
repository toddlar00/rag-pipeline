# Phase A0 Baselines

These files are the canonical per-platform Phase A0 baseline candidates for
the R10/A0b architecture gate. They contain no private corpus content or
credentials. Each report covers the complete nine-scenario set with five
fresh-process repetitions.

## Provenance

Both replacement reports were generated from the clean pre-gate source
checkpoint `6d9e814a9e5c1f071214f42d69ee4b0623e799a7` (tree
`b33fa91b3aac80ae14084c54dbf6059176213723`). The executing environments were
synchronized with repository-pinned uv 0.11.31 against the exact CPU
application/test lock union plus its retained bootstrapper:

- `requirements-full.lock`
- `requirements-test.lock`
- `requirements-lock-tools.lock`

Both environments used disposable direct uv-managed CPython interpreters
rather than durable user installations. The Windows interpreter also avoids a
virtual-environment redirector so supervised-child parent identities remain
exact. Each disposable interpreter was targeted explicitly with `uv pip sync
--python <path> --break-system-packages --strict --torch-backend cpu
--require-hashes`; `uv pip check --python <path>` then passed for 189 Windows
and 187 Linux marker-resolved distributions before generation. The
`--break-system-packages` use was confined to these disposable uv-managed
interpreters, which declare themselves externally managed.

| Platform | Runtime | Report | Bytes | File SHA-256 | Embedded report SHA-256 |
|---|---:|---|---:|---|---|
| Windows x86-64 | CPython 3.12.13 | `phase-a0-windows-cpython312.json` | 49,245 | `4529674ebee037be5b627a725dbd824ebd88ce7a34d12b82267aeda52e18a6b3` | `caeff095147e9e0a9275d27fad97d6281e769c556b6ed982078ab0584795862e` |
| Linux x86-64 | CPython 3.12.13 | `phase-a0-linux-cpython312.json` | 48,657 | `6cfa3c933b6d8fcf7e9955373e9174bee567cb98a50bb0b07bc23754f2881735` | `567679c11b3bfeb9db64e6d0552fe38ef6ff706809d2df70fd277e2edd24813c` |

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
Pre-gate source `fdb08d2` closed those cases without weakening home/tilde
denial, and its architecture baseline reproduced on Windows and Linux across
Python 3.10-3.14. Current pre-gate source `6d9e814` retains those controls and
the strict classification contract, then adds the strict shared TOC hierarchy
output contract. Its complete locked Windows suite passes 2,425 tests with 7
skips. All dependency, model-artifact, CI-security, architecture, Ruff, and
tracked-source compilation gates pass. The refreshed canonical architecture
inventory records 2,020 functions and 380 compact runtime callables.

The replacement reports above are local baseline candidates, and each passes an
independent complete same-platform 9×5 comparison. The gate-only commit that
contains these reports freezes the final local replacement candidate; its exact
commit/tree must be recorded on the stacked pull request. No successful hosted
replacement run is claimed. A0b remains pending until both exact-head hosted
cells pass and retain their successful evidence bundles.

## Checking

Provision the exact full/test CPU lock union on CPython 3.12 x86-64, then run
the matching command from a clean checkout. The `--system` provisioning
commands below are for an ephemeral hosted-CI interpreter only; do not run them
against a durable user Python installation. For a disposable standalone
uv-managed interpreter, target its path explicitly with the
`--break-system-packages` command documented under Provenance above.

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

The replacement gate-only delta after `6d9e814` is limited to the two reviewed
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
