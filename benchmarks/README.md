# Phase A0 Baselines

These files are the canonical per-platform Phase A0 baseline candidates for
the R10/A0b architecture gate. They contain no private corpus content or
credentials. Each report covers the complete nine-scenario set with five
fresh-process repetitions.

## Provenance

Both reports were generated from the clean pre-gate source checkpoint
`0fe69f3559d5d3dffc77b1a9b36034e4e3c470ae` (tree
`f97aa2baab1d2264c1ea30d36f3be4bdea2a85fe`). The executing environments were
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
| Windows x86-64 | CPython 3.12.13 | `phase-a0-windows-cpython312.json` | 49,069 | `cb2ae7619ed21ab139e1842c7bc758883c3b4dbf63c251268cc34ada2e3517ad` | `1bd87c3d7ba0863da788b0f5f3f71309d6cc4cfb50d78d38cd1cea45b0fec1e2` |
| Linux x86-64 | CPython 3.12.3 | `phase-a0-linux-cpython312.json` | 48,491 | `a099d0741c31384e45628b0f0a7e993958ee7e0da0e9384dbf7e0c19b67bca38` | `c89880c706068ea84ad6bf7262c4fda3ce3f53c53af1e408ab7b2fc4f08095b6` |

The reports attest the same source commit, clean-worktree state, tracked-diff
digest, eight dependency/model lock identities, and authoritative scenario-set
digest. Platform, patch-level runtime, hardware, wall time, and process-only
RSS diagnostics are intentionally platform-specific. A path-scoped Git
attribute preserves LF for these JSON reports on Windows so the byte counts and
file SHA-256 identities above survive checkout unchanged.

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

Gate-only checkpoint `144a434` establishes the local A0b candidate and passes
both matching final-head local comparisons. A0b remains pending until one
frozen publishable commit/tree passes both hosted CI jobs and retains their
current-report artifacts. Each successful hosted cell strictly revalidates the
generated report, requires its clean source identity to equal the exact job
head, and builds a verified two-file evidence directory containing that report
and a content-free `phase-a0-ci-attestation-v1` record. The attestation binds
candidate and pre-gate commit/tree identities, baseline and current-report
hashes, normalized platform, and sorted gate-only paths. CI checks the exact
file set and copied-report hash immediately before upload; missing or extra
evidence is an error, not a warning.
