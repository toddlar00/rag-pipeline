# Release Security Policy

- **Status:** Implementation included in cumulative draft PR #44; not merged or
  human-reviewed, with the security-owner decision, exact replacement-head
  review, and integration pending
- **Milestone:** R0B
- **Policy schema:** `release-security/v1`
- **Decision owner:** Repository owner for release and private-source policy

## Context

The pipeline processes private books, queries, retrieved passages, generated
answers, and evaluation judgments. Earlier controls validated caller-supplied
LLM URLs and kept the Gradio and service listeners on literal loopback, but
those controls did not answer several separate trust questions:

- whether any cloud data path is permitted for a particular run;
- whether an unauthenticated loopback UI is safe on a shared OS session;
- whether generated text may be persisted in a plaintext cache;
- whether one custom gateway URL represents multiple credential tenants;
- whether ambient proxy, custom-CA, or SDK endpoint configuration is trusted;
- whether runtime model loaders may initiate network access; and
- whether policy survives worker, durable-job, evaluation, and resume
  boundaries without persisting secrets.

URL syntax validation does not authenticate a local principal, attest DNS
resolution, configure an OS firewall, or establish the trustworthiness of an
HTTPS interception proxy. These controls therefore need one explicit,
versioned record and a narrowly stated support boundary.

## Decision

`release_security.ReleaseSecurityPolicy` is the single immutable policy record
for release-facing CLI, Python, UI, service, evaluation, worker, job, cache, and
model-loader boundaries. Direct Python callers receive the same fail-closed
release defaults as command-line callers.

Policy version 1 has these defaults:

| Control | `release` default | Explicit alternative |
|---|---|---|
| Provider/data network | `local-only` | `--network-policy allow-cloud` |
| Reviewed model artifacts | `cache-only` | inspect an offline task/model plan, then pre-sync that explicit selection with `tools/sync_model_artifacts.py`; or explicitly select `allow-reviewed-sync` |
| LLM response cache | `off` | an explicit cache mode; the current store is plaintext |
| Inline key arguments | rejected | development profile only; still discouraged |
| Custom gateway tenant identity | required, nonsecret | `--llm-cache-namespace LABEL`; only its SHA-256 identity persists |
| Proxy/custom-CA/SDK endpoint environment | ignored or rejected | `--trust-environment-network` after operator review |
| Gradio principal boundary | disabled | `--trust-local-user` for a trusted single-user OS session |
| Auxiliary analytics/telemetry | disabled | no release override |

Unknown policy versions or fields, invalid enum/boolean values, malformed
namespace identities, and receipts with missing or extra fields fail closed.
Workers remain trusted code; this schema validation is not a cryptographic
tamper proof. Provenance is strict and value-free: it includes the schema,
selected controls, opaque namespace identity, telemetry state, and UI boundary,
never credentials or the raw namespace label.

## Supported trust boundary

The shipped UI is an unauthenticated, literal-loopback application. It is
supported only in a trusted single-user OS session and refuses to build or
launch without `--trust-local-user`. Search, Info, Export, and Jobs share this
boundary. Loopback is not authentication; shared-host or remote UI use requires
a separate design with principals, sessions, origin/CSRF protections,
authorization, TLS, rate and tenant isolation, audit retention, and corpus
distribution approval.

The authenticated service remains literal-loopback-only and uses distinct
reader/admin tokens. Its listener boundary and its provider-egress boundary are
independent: a cloud embedding still sends private query or chunk text away
from the machine and therefore requires `allow-cloud`.

Workers and extensions are trusted code. Process supervision and policy
receipts are containment and consistency mechanisms, not a sandbox.

## Data-flow matrix

| Feature | Data that can leave the process | Destination | Required policy | Persistence/provenance |
|---|---|---|---|---|
| Deterministic conversion/chunk/export | none | local files only | default | artifact receipts bind policy where it changes output |
| Local embeddings/reranker/zero-shot models | none at runtime | verified local model tree | default `cache-only` | model-lock digest binds generated artifacts |
| Offline model-sync planning | none | reviewed lock, local cache, and local filesystem-capacity metadata | default; no network policy opt-in | schema-v1 plan binds the lock, selection, bundle identities/statuses, blocked consumers, and space contract |
| Reviewed model synchronization | reviewed public model IDs and file requests; no corpus text | official Hugging Face or explicitly reviewed mirror plus required CDN redirects | explicit reviewed sync; reviewed environment trust when overrides exist | bytes are size-bounded, allowlisted, hash-verified, and atomically published |
| Voyage/OpenAI/Cohere embeddings | full chunk text during indexing; query text during search | literal reviewed provider origin | `allow-cloud` | policy receipt binds jobs/evaluation; no provider SDK endpoint selection; MiniMax embedding IDs fail closed pending a reviewed current contract |
| Cohere/Jina reranking | query plus bounded candidate text and metadata | literal reviewed provider origin | `allow-cloud` | reranker response is not separately cached |
| OpenAI-compatible generation | prompts containing chunks, query/evidence, or generated-work inputs | validated official/custom endpoint | `allow-cloud`; custom release gateways also require namespace | release cache defaults off; events/reports carry only opaque identities |
| Gemini generation | the same feature-specific prompt | pinned Gemini origin with Vertex mode disabled | `allow-cloud` | finite one-attempt SDK request; policy-aware client cache |
| Ollama on literal loopback | prompt to a local process | canonical loopback IP | default | proxy and ambient credential lookup disabled |
| Ollama on a public HTTPS endpoint | prompt | validated public endpoint | `allow-cloud`; custom release gateway namespace required | same LLM cache/receipt rules |
| Evaluation | query, candidate, and optional answer data according to selected providers | same destinations as runtime | same policy as runtime | report configuration includes value-free policy provenance |
| Service search/reindex | query or chunks according to configured embedding provider | same destinations as runtime | one immutable server policy | strict policy receipt crosses worker boundary; reindex argv pins it |
| UI search/reindex | query or chunks according to configured providers | same destinations as runtime | trusted UI plus the relevant network policy | strict policy receipt crosses worker/job boundary |
| Chroma/Gradio/Hugging Face auxiliary telemetry | none | disabled | no override | process environment and explicit client settings disable it |

Cloud feature gates execute before credential lookup, provider import,
tokenizer import, cache lookup, worker launch, or transport construction. API
token accounting uses a conservative local estimate so an apparently local
preflight cannot trigger a tokenizer/model download.

## Transport decision

Official API embeddings and reranking use pinned literal HTTPS URLs through a
Requests session with:

- `trust_env` equal to the policy's explicit environment-trust flag;
- finite 60-second deadlines;
- redirects disabled and every 3xx response rejected;
- explicit redacted bearer authentication; and
- no implicit provider-SDK retries or ambient base-URL selection;
- session-owned streaming through response close; and
- strict JSON MIME/UTF-8/framing/depth validation before parsing, with decoded
  ceilings of 32 MiB for embeddings and 8 MiB for reranking.

The Voyage, OpenAI, and Cohere SDK distributions are not runtime dependencies;
the full lock omits them and their SDK-only transitive graph. Dependency policy
fails if those packages return without an explicit transport decision change.

OpenAI-compatible and Ollama generation use the same reader with a 16 MiB
decoded ceiling. It permits absent `Content-Length` for legitimate chunked
responses, but rejects malformed, oversized, ambiguous, or dishonest lengths.
Decoded-byte accounting bounds compression expansion; duplicate fields,
non-standard numbers, excessive nesting, invalid UTF-8, and truncated JSON fail
with body-free diagnostics. The streaming phase has an overall operation
deadline in addition to the Requests per-read timeout. Response and owning
session close on success and every rejection path.

Gemini explicitly selects non-Vertex mode, its reviewed
`generativelanguage.googleapis.com` base, the current stable
`gemini-3.6-flash` model, automatic retries disabled, a finite timeout, redirect refusal, and
matching sync/async HTTPX environment trust. Deprecated sampling parameters
are omitted; the request's thinking flag maps to high/minimal thinking. The
client cache is keyed by API key and transport-trust mode so trusted and
untrusted clients cannot be reused across policy changes.
The Google SDK materializes Gemini's typed response before this application
adapter receives it, so this decision does not claim the Requests byte ceiling
for Gemini. R2 must prove an equivalent SDK transport limit or replace that path
with an owned REST transport before the all-provider ceiling is complete.

The reviewed MiniMax generation default is M3. Its adapter uses
`max_completion_tokens`, temperature zero, an explicit adaptive/disabled
thinking mode, and `reasoning_split=true` so final text never incorporates the
private reasoning field. M2.x cannot honor disabled thinking and is rejected in
that configuration; explicitly enabled M2.x calls receive a completion floor
before runtime budget admission.

Known proxy, CA, TLS key-logging, model-hub endpoint, and provider SDK
endpoint/mode environment variables are checked for non-empty values without
persisting, echoing, or reporting those values. `SSLKEYLOGFILE` is included
because urllib3 and httpx read it directly from the process environment when
constructing an SSL context: a per-session `trust_env = False` suppresses the
proxy, CA, and `.netrc` variables but not TLS session-key export, so policy is
its only control point.
Release cloud/model synchronization refuses them unless the operator passes
`--trust-environment-network`. When the flag is absent, direct cloud sessions
also set `trust_env=False`; loopback always bypasses ambient proxy and netrc
configuration. When the flag is present, the operator accepts the configured
proxy/CA environment and may explicitly authorize a non-default `HF_ENDPOINT`;
provider API origins and non-Vertex Gemini mode remain fixed.

The application does not claim to control the OS resolver, route table,
firewall, enterprise TLS interception, or a malicious trusted proxy. Production
deployments that require destination enforcement must add external DNS/egress
controls and review them independently.

## Secrets, tenancy, and caches

Release-mode CLI parsing rejects `--api-key`, `--cloud-key`, and
`--gemini-key` before the requested operation and emits a value-free error.
Use provider environment variables or the interactive menu's hidden prompt.
Secrets do not enter reports, manifests, job specs, resume commands, endpoint
identities, or namespace identities.

A custom gateway can map one URL/model pair to several accounts. Release mode
therefore requires an operator-declared, nonsecret tenant/trust label even when
the response cache is off. The raw label is validated, hashed, and discarded;
its opaque identity participates in LLM cache keys, single-flight identity,
events, reports, and resume/job provenance. Different identities cannot share
results. Cache-record schema v4 and cache-key schema v3 reject ambiguous legacy
records. Versioned contract and deterministic-fallback IDs participate in the
key, and a contracted cache hit is accepted only after revalidation.

The current LLM cache is a private-permission plaintext store, not encrypted
storage. Release CLI calls default it to `off`, and development CLI calls
default to `readwrite`. Direct Python calls use their explicit request mode or
the process `LLMRuntimeConfig` (initially `off`); a security-profile object does
not silently replace an already configured runtime. Existing retention tooling
remains dry-run-first.

## Model acquisition

Runtime model loading is cache-only by default and verifies every selected byte
against `model-artifacts.lock.json` before loading. A cache miss fails before
constructing a model network transport. For the normal first full run, the
operator first inspects the offline PDF-ingestion plus default-retrieval plan,
then synchronizes that same explicit task selection:

```bash
python tools/sync_model_artifacts.py \
  --task pdf-ingestion --task default-retrieval --plan
python tools/sync_model_artifacts.py \
  --task pdf-ingestion --task default-retrieval
```

`pdf-ingestion` selects the reviewed Docling layout/table and Nomic tokenizer
consumers. `default-retrieval` adds the Nomic embedding/token-counter and BGE
reranker consumers. `classification` is a separate optional BART preset, and
individual reviewed model IDs remain selectable with repeatable `--model`.
Selections are canonicalized and deduplicated in lock order. Synchronizing all
independently safe consumers is an explicit `--all` operation; it does not
override a blocked consumer.

Planning is offline: it resolves the current lock, verifies selected local
cache bundles, and reads filesystem capacity without constructing a downloader,
changing telemetry environment state, or sending a request. Text output reports
exact per-bundle and aggregate byte counts, already-present runtime bytes, peak
simultaneous staging/destination space, margin, and free-space sufficiency.
`--plan --json` emits the same decision as a content-free schema-v1 record with
the model-lock and plan SHA-256 identities. The plan fails nonzero when space is
insufficient. Execution revalidates the lock, cache identities, and free space
before transport, and repeats the capacity check before each missing bundle is
published.

The plan and executor select only reviewed consumers, exact revisions, and
exact file allowlists; block pickle-only weights; verify source and transformed
bytes; and publish isolated byte-verified cache trees atomically. LegalBERT's
pickle-only embedding consumer is reported as `unsafe-pickle` and never
downloaded. Any independently safe consumer remains a separate bundle.
Plan totals cover synchronizer-created reviewed Hub runtime bundles only; they
exclude derived Docling composite caches and RapidOCR assets supplied and
verified by the installed package.

Reviewed synchronization does not use the Hugging Face SDK: an owned Requests
session sends no ambient Hub/netrc authorization, uses a fixed non-identifying
user agent, explicitly binds the official or operator-reviewed Hub endpoint,
maps environment trust to `trust_env`, performs no automatic retries, uses one
top-level transfer per file with 10/60-second connect/read deadlines, caps
redirects at five, rejects any redirect or final destination that is not
credential-free HTTPS, and enforces each locked size while streaming. Required
HTTPS Hub CDN redirects remain enabled; exact hashes still decide acceptance.
Run the sync before opening private documents. `--model-download-policy
allow-reviewed-sync` is an explicit runtime convenience for the same
reviewed-byte path, not the default.

Unknown models require all three development-only conditions:
`--security-profile development`, explicit reviewed-sync policy, and
`RAG_ALLOW_UNPINNED_MODELS=1`. The first release profile will reject that
escape hatch entirely.

## Process and durable boundaries

Policy receipts cross supervised UI/service search workers. Durable reindex and
generic background job argv pin the policy version, profile, network mode,
model-download mode, opaque namespace identity, and environment-trust flag.
The serializer preserves the inner `--` suffix exactly and never persists the
raw namespace or a key. Service filesystem-owner markers deliberately exclude
the policy so a legitimate policy change does not corrupt ownership state.

Every parser disables ambiguous abbreviation where security options cross a
service boundary. Unknown future policy versions cannot be silently interpreted
with old defaults.

## Migration and compatibility

- Existing commands now default to local-only cloud egress, cache-only model
  loading, and release CLI cache mode `off`.
- Cloud commands must add `--network-policy allow-cloud` and may need
  `--trust-environment-network` after reviewing active proxy/CA configuration.
- UI commands must add `--trust-local-user`.
- Local models must be planned offline and synchronized by explicit task/model
  selection (or explicit `--all`) before first private-data use.
- Runtime-bundle identity schema v2 binds the complete primary, transform, and
  auxiliary inventory. Older Nomic directories whose paths use the former
  partial identity are treated as cache misses; resynchronize the relevant task
  preset. This migration sends no source or private-corpus data.
- Release inline key arguments no longer work; move keys to the environment or
  hidden interactive prompt.
- Legacy ambiguous LLM cache records fail closed. No automatic tenant guess is
  attempted.
- Generated classification text is treated as hostile until it satisfies the
  reviewed exact-label contract. Events and reports retain only the bounded
  contract/fallback IDs, status, and stable diagnostic code; rejected text and
  validator exceptions are not retained.
- Durable jobs and worker messages carry an exact versioned receipt; an older
  worker cannot silently accept a newer policy.
- Historical evaluation reports without this optional comparison key remain
  readable for migration, while new reports emit the policy receipt.

## Consequences and follow-up

The policy makes the safe path more explicit and adds setup steps for first
model use, cloud features, and the local UI. That friction is intentional for a
private-corpus tool. It does not replace the unresolved private-source
documentation decision, corpus-owner evaluation approval, exact-head human
review, external network controls, encrypted cache design, or a future
authenticated multi-user product.

R2 must close the Gemini response-ceiling gap and re-audit these transport assumptions whenever Requests, HTTPX,
google-genai, Hugging Face Hub, or provider packages change. R5 must compose
this record into the first release manifest, and R7 must keep the provider
transport and no-network matrices as security-critical coverage targets.
