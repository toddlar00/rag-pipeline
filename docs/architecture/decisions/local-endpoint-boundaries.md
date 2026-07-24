# Local UI and LLM endpoint boundaries

- Status: Accepted locally; pending review and integration
- Date: 2026-07-24
- Milestone: R0A

## Context

The product handles private source documents, prompts, retrieved passages, and
generated study artifacts. Its service API is explicitly loopback-only, but the
Gradio launcher previously exposed a supported `--share` path that could create
an unauthenticated public tunnel.

LLM endpoint handling also had several independent interpretations of a URL.
Provider-specific environment keys were selected from a hostname-only check,
while transport, cache identity, artifact provenance, menu display, resume
commands, and background-job persistence each handled the raw string
differently. A plaintext or credential-bearing URL could therefore be
classified as an official provider before its complete transport contract was
validated.

Requests followed redirects by default. One admitted LLM transport could make
multiple uncounted HTTP requests, and a 307 or 308 could forward a private
prompt to a different origin. Literal-loopback requests also inherited ambient
proxy configuration.

## Decision

The supported Gradio launcher is local-only. It has no `--share` option, binds
`127.0.0.1` explicitly, and passes `share=False` explicitly. Public deployment
is a separate product and security decision, not a launcher convenience flag.

`endpoint_policy.py` is the standard-library-only authority for user-supplied
OpenAI-compatible and Ollama base URLs. Its versioned contract is shared by CLI
interpretation, credential selection, direct runtime composition, adapters,
background jobs, cache/report identity, and artifact parameters.

The contract:

- accepts only absolute ASCII HTTP(S) URLs without whitespace, controls,
  backslashes, user information, queries, or fragments;
- requires canonical DNS names, IP literals, ports, and base paths, rejecting
  trailing dots, IDNA/punycode, localhost aliases, alternate numeric IP
  spellings, unspecified or multicast targets, IPv4-mapped IPv6, zone IDs,
  dot segments, encoded delimiters, and lookalike official provider subdomains;
- permits plaintext HTTP only for a canonical literal loopback IP in
  `127.0.0.0/8` or `[::1]`; and
- classifies DeepSeek and MiniMax only at their exact reviewed HTTPS host,
  default port, and supported base path.

Validation and canonicalization occur before environment-key resolution and,
for direct Python callers, before the runtime can consult its response cache.
Invalid values raise fixed messages that do not include submitted URL text.
Official endpoints receive readable versioned identities; custom, loopback,
and rejected endpoints receive opaque SHA-256 identities. No URL-carried
credential is accepted for later redaction.

The OpenAI-compatible and Ollama adapters validate again immediately before
network I/O. Requests redirects are disabled and every 3xx response is a
single-attempt configuration error. Literal-loopback calls use a short-lived
Requests session with `trust_env=False`; public HTTPS calls retain normal
operator-managed certificate and proxy behavior. Credentialed Requests calls
also pass an explicit Bearer authentication object so ambient `.netrc` entries
cannot replace or add credentials. Fixed MiniMax embedding and Jina reranking
Requests calls refuse redirects explicitly, reject every 3xx body, and have a
finite timeout.

Background-job submission validates and canonicalizes endpoint arguments
before creating a job document. Endpoint and secret option abbreviations are
rejected, and application parsers disable long-option abbreviation. Commands
that cannot invoke an LLM do not resolve provider credentials.

## Consequences

Previously accepted values such as `http://localhost:11434` must use a literal
loopback form such as `http://127.0.0.1:11434`. Custom remote gateways must use
HTTPS. Base paths are intentionally conservative; a future provider requiring
additional URL syntax needs an explicit policy revision and adversarial tests.

Changing canonical endpoint equivalence changes cache and artifact identity.
The endpoint policy version is embedded in identities, and chunking policy is
advanced so stale completion evidence cannot silently cross the boundary.

Remote Gradio access is no longer a supported command. Restoring it requires a
separate reviewed threat model covering authentication, TLS, authorization,
CSRF/origin controls, tenant and rate isolation, audit retention, corpus
disclosure, and distribution licensing.

## Rejected alternatives

- **Keep `--share` with a warning.** A warning supplies neither authentication
  nor authorization for private corpus surfaces.
- **Classify providers by hostname alone.** Scheme, port, authority, path, and
  redirect behavior are part of the credential trust boundary.
- **Accept URL credentials and redact them later.** Menu output, process
  arguments, job specs, logs, and cache lookup exist before late redaction.
- **Allow `localhost` over HTTP.** Name resolution and ambient proxy behavior
  make a literal loopback address the smaller, auditable exception.
- **Follow redirects after revalidation.** Redirect chains break the exact
  transport-attempt budget and recorded endpoint provenance; callers must
  configure the final base URL directly.
- **Hash API keys into cache namespaces.** Credential-derived persistence adds
  a new secret-handling surface. A future nonsecret tenant namespace is the
  safer compatibility mechanism when shared custom gateways require it.
