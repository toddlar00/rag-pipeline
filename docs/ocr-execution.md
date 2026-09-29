# OCR execution evidence

The opt-in execution wrapper runs page, region, or hard-scan OCR in a contained
worker and writes a separate provenance receipt. Existing OCR report schemas,
canonical extraction files, model locks, and default commands are unchanged.
Use it when an experiment needs evidence of the runtime that actually executed
OCR, rather than an inventory of what could have executed it.

This is local evidence, not signed attestation or accuracy certification.
Representative approved references and held-out evaluation remain separate
requirements; the synthetic challenge is not a substitute.

## Create an isolated installation

Use an existing Python 3.12 interpreter and new directories, preferably outside
the Dropbox workspace. This command refuses existing environment or evidence
directories; it never synchronizes the global interpreter or `.venv`.

```powershell
python tools/install_ocr_environment.py --python C:/path/to/python312/python.exe --environment C:/path/to/new-ocr-env --evidence-dir C:/path/to/new-install-evidence --timeout-seconds 1800
```

The fixed recipe creates a private environment, bootstraps the unchanged
hash-locked lock tools, checks `uv==0.12.20`, and runs `uv --no-config pip sync`
with `--strict --torch-backend cpu --require-hashes --link-mode copy` against
all three committed full, test, and lock-tool locks. It then runs dependency
consistency checking and records the complete installed distribution inventory.
The installer does not regenerate locks or download Python or Hub models.
Package installation can use the network and package cache. Hash-locked source
distributions may still build; neither a wheel-only environment nor a locked
build environment is claimed.

Each fixed installer step runs under the existing process-tree supervisor.
The timeout is **per step**, not a total installation deadline. Child installer
environment variables are allowlisted; ambient credentials, index overrides,
and proxy settings are not forwarded. Network/cache requirements, available
disk, and permissions can therefore cause a clear installation failure.

Private evidence contains six step logs, an exact installer source snapshot,
and `installation.json`. A 16 MiB log threshold is monitored periodically; it
is **not a strict write cap**. Native output can overshoot between checks.
Oversized logs cannot qualify a completed step. Failure retains partial
directories and available diagnostics; failures before receipt publication may
leave no receipt. No recursive cleanup or automatic reuse occurs.

Installation validation rechecks all step statuses and log digests, archived
installer source, current lock digests, interpreter executable hash, Python
version, and the entire current installed package/version inventory. The local
environment identifier is a normalized pathname hash, not a directory-generation
identifier. The executable hash is not proof of the base interpreter or loaded
DLL bytes. Editable metadata, logs, and receipts are not authenticated; matching
versions do not attest installed wheel bytes, source-build inputs, or native
libraries. The older runtime manifest deliberately retains
`locked_environment_verified: false`.

## Run an observed experiment

Invoke the wrapper using the interpreter whose installation receipt is supplied:

```powershell
& C:/path/to/new-ocr-env/Scripts/python.exe tools/run_ocr_experiment.py --pdf C:/approved/source.pdf --output-dir C:/existing-parent/new-page-run --page 1 --page 2 --installation C:/path/to/new-install-evidence/installation.json
```

The output directory must be new and its parent must already exist. Native
worker stdout/stderr are discarded. The parent accepts completion only after
strict report/receipt validation and exact requested input/configuration checks.
The default deadline is 600 seconds; `--timeout-seconds` accepts 1–3600.

For page experiments, `--dpi 300|400`, `--preprocessing
none|deskew|contrast|deskew-contrast`, repeated `--page`, `--max-pages`, and
optional `--evidence` retain the existing recovery policy. Requested pages
remain subject to the cap; omitted or deferred pages are not silently scored.

For region or hard-scan experiments, supply the existing saved recovery report
and its source-bound, human-confirmed plan:

```powershell
& C:/path/to/new-ocr-env/Scripts/python.exe tools/run_ocr_experiment.py --operation regions --pdf C:/approved/source.pdf --recovery C:/review/recovery.json --plan C:/review/regions.json --output-dir C:/existing-parent/new-region-run --installation C:/path/to/new-install-evidence/installation.json
& C:/path/to/new-ocr-env/Scripts/python.exe tools/run_ocr_experiment.py --operation hardscan --pdf C:/approved/source.pdf --recovery C:/review/recovery.json --plan C:/review/hardscan.json --output-dir C:/existing-parent/new-hardscan-run --installation C:/path/to/new-install-evidence/installation.json
```

Crop experiments accept DPI, not page-selection/preprocessing overrides; their
recipe and geometry come from the existing plan. Irrelevant nondefault page
settings are rejected rather than recorded as though they were applied.
The region and hard-scan report validators also verify current source,
recovery, plan, and retained context bindings.

Installation evidence is optional for a diagnostic run. Without it, the receipt
says `installation_evidence: not_supplied` and requires attention. With it,
the executing environment must pass the full local installation binding check
and requested OCR package version gate before OCR starts. A runtime mismatch
afterward is preserved as a mismatch, not relabeled as qualification.

## Bundle and observation contract

The create-only private bundle contains:

- `report.json`: the unchanged operation-specific OCR review report, including
  sensitive candidate text. Page reports retain their 64 MiB contract; region
  and hard-scan reports retain their 128 MiB contracts.
- `execution.json`: schema 1, kind `ocr_execution_receipt`; content-free input
  and output hashes, effective runtime observations, actual engine-call counts
  and timings, installation binding status, and explicit verification limits.
- `manifest.json`: schema 1, kind `ocr_execution_bundle`; published **last** as
  the completion marker, binding both files and normalized requested settings.

The established lease system can also retain a `.rag-locks` directory. The
three JSON files are not a multi-file transaction. Input mutation, failure,
deadline, cancellation, or cleanup failure can leave a partial or even completed
bundle; inspect it and choose a new directory before retrying. A completed
manifest alone is not an authorization or trusted import boundary.

Input bindings cover source PDF, normalized configuration, all three dependency
locks, both model policy/lock files, and applicable evidence/recovery/plan and
installation receipt. They are rechecked before receipt and manifest publication.
No reference transcript is used by OCR execution itself; evaluation commands
must bind references separately. Input aliases and linked paths are rejected.

The opt-in reader recorder intercepts actual RapidOCR `__call__` invocations.
Preflight, renderer failure, and preprocessing abstention do not count as OCR.
`ocr_executed` means at least one invocation, including a failed invocation;
the summary distinguishes attempted, completed, and failed calls. A completed
engine call is not necessarily a valid or nonempty OCR candidate. The unchanged
report remains the authority for candidate failures and coverage.

For the known shared RapidOCR guard, the recorder starts only after repeated
validation and hook installation succeed. Call timing surrounds raw engine
dispatch, including in-call allocation checks, but excludes guard setup and
restoration. A normally returned raw call stays `completed` if later guard
validation or cleanup rejects its candidate; an exception escaping raw dispatch
is `failed`. Generic injected test callables retain their callable-level scope.
Do not compare these call durations with earlier guard-wrapper timings as an
OCR speedup measurement. Overall experiment duration still includes setup and
cleanup work.

Before reader close, the observer reads the three actual constructed ONNX
sessions' providers and intra/inter-op thread settings, plus the approved
package model identities reverified before loading and at observation. Session
construction does not prove that every role ran on every image; a detector may
produce no recognition work. Path-based model loading is not immutable
byte-to-session attestation. Strict parent readback rechecks approved identities
and local model files, not authenticity of an editable receipt.

`project_sources_at_observation` hashes loaded first-party modules' files on
disk at observation. It is not a complete tracked-tree inventory, proof of
loaded Python bytes, or a frozen-source acceptance baseline. The receipt and
manifest omit PDF text, OCR text, token strings, paths, and raw native errors;
the report and installation logs can contain sensitive data and stay private.

Successful review-bundle creation returns exit **3**, because accuracy/manual
review remains required; failure returns 2, deadline 124, cancellation 130.
A timeout or cancellation never asserts that no report exists. The in-process
`run_execution_bundle` API does not itself provide a deadline; use the CLI's
contained worker for that protection.

## Observed compatibility run

On 2026-09-06, a fresh outside-workspace Python 3.12.10 environment completed
the unchanged strict CPU installation recipe. A contained run of the approved
eight-page synthetic challenge completed eight engine calls, with no failed,
empty, or deferred pages. All three observed sessions used
`CPUExecutionProvider`, intra-op 2 and inter-op 1. Requested OCR package versions
matched the committed lock. This was scoped, pre-freeze runtime evidence—not
a representative-data accuracy result, holdout evaluation, complete-source
qualification, or native-binary attestation. The separate diagnostic first
environment and qualified installation evidence were preserved; no existing
environment or model/dependency lock was changed.
