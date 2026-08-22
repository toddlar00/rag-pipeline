# Dependency Compatibility Domains

- **Status:** Integrated on `main` through
  [PR #76](https://github.com/toddlar00/rag-pipeline/pull/76); the optional
  reviewer-bound transitive exception mechanism is not implemented
- **Milestone:** R2b-0, before dependency version changes
- **Schema:** `dependency-compatibility-domains-v1`

## Context

The repository uses several requirement manifests and seven generated universal
hash locks. A single wildcard Dependabot group combined unrelated PDF, vector,
ML, UI, and provider changes in PR #30. That proposal was based on an older
`main`, changed no generated lock, and allowed installed-suite jobs to keep
testing the old locked environment. It therefore could not prove compatibility
for the versions named in its manifest diff.

[GitHub's grouping contract](https://docs.github.com/en/code-security/reference/supply-chain-security/dependabot-options-reference#groups--)
consolidates matching dependency updates into a group and gives the first
matching group precedence. Exact non-overlapping package patterns therefore
define review boundaries; they do not merely rename pull requests.

The frozen R1 tree also removed the unused Voyage, OpenAI, and Cohere SDKs in
favor of repository-owned HTTP transports. Mechanically rebasing PR #30 would
reintroduce dependencies that policy now forbids. Of its other proposed lower
bounds, all but Google GenAI 2 were already selected by the frozen locks; raising
those bounds would reduce compatibility without exercising a different install.

## Decision

Every normalized direct dependency declared in the eight governed requirement
input manifests belongs to exactly one declared compatibility domain:

| Domain | Direct packages |
| --- | --- |
| PDF/Docling | `docling`, `docling-core`, `pymupdf`, `pypdfium2` |
| Vector stores | `chromadb`, `onnxruntime`, `qdrant-client` |
| ML/runtime | `einops`, `flagembedding`, `numpy`, `rank-bm25`, `sentence-transformers`, `torch`, `torchvision`, `tqdm` |
| Service/UI | `fastapi`, `gradio`, `uvicorn` |
| Provider transport | `google-genai`, `requests` |
| Test/audit tooling | `pip`, `pip-audit`, `pip-licenses`, `pytest`, `ruff`, `uv` |

[`dependency-compatibility-domains.json`](../../../dependency-compatibility-domains.json)
is the canonical mapping. The pip groups in
[`.github/dependabot.yml`](../../../.github/dependabot.yml) must match it exactly.
[`tools/check_dependency_policy.py`](../../../tools/check_dependency_policy.py)
strictly validates the schema, canonical package names and ordering, exact
patterns, uniqueness, direct-manifest coverage, and JSON/YAML agreement. It
accepts only the repository's complete constrained block-style Dependabot
dialect, so a second or reordered update item, flow mapping, alias, malformed
later block, unknown field, or duplicate key cannot remain uninspected. A
wildcard, an unassigned input, an unknown package, a duplicate assignment, or
configuration drift is a gate failure.

On pull requests, the checker also compares direct requirement records with the
exact 40-character base commit. A change spanning more than one declared domain
fails. An explicit impact map requires every lock generated from each changed
manifest to change Git blob identity. Every changed direct package must also
change its selected record in at least one mapped lock, so unrelated transitive
churn cannot satisfy the gate. There is no inline support-floor exception: a
justified no-op bound change first requires its own policy/checker change and
review rather than being smuggled through a dependency PR.

Repeated declarations and environment-marker variants count once after standard
Python package-name normalization. Transitive packages do not become direct
policy inputs merely because they appear in a generated lock. The legacy
`setup_rtx5060.sh` helper directly requests CUDA-specific Torch, Torchvision,
and Torchaudio wheels outside these universal CPU manifests. That unqualified
GPU helper is an R5 support-tier and packaging input, not a Dependabot-managed
R2 domain; this policy must not be cited as qualification for it.

The repository's single Node ecosystem
(`tools/zettlr-markdown-validator`) is Dependabot-managed through its own
exactly-validated npm update block with one pinned wildcard group, added by
Task 0.6. Node packages are not direct inputs of this Python domain policy:
they carry no domain assignment, and `package-lock.json` changes are
invisible to the one-domain requirement/lock diff gate above. Node
supply-chain review is owned by the separate Node audit/SBOM gate
(`node-vulnerability-policy.json`, `tools/check_node_vulnerabilities.py`,
`tools/normalize_node_sbom.py`) and the security ownership map. Any Node
dependency upgrade must regenerate `package-lock.json` and co-update the
frozen validator bundle digests in `markdown_validation.py` in the same
reviewed change, or the validator probe fails closed as unsupported.

## Change protocol

At most one compatibility domain is upgraded per pull request, enforced against
the exact PR base. Each such change must:

1. start from the frozen release candidate or current `main` identity;
2. state the intended package/version changes and affected contracts;
3. regenerate every affected universal lock, then prove a second regeneration
   is byte-stable;
4. install and test the changed locks rather than merely editing lower bounds;
5. run the domain-specific gates in addition to the complete dependency,
   security, license, model-artifact, and supported-Python checks; and
6. record the exact commit, lock identities, warnings, and rollback condition
   before the next domain proceeds.

Provider changes replay endpoint, credential, retry, response-size, and release
security tests. Vector changes replay both real-client migration and lock-release
probes. PDF changes replay ingestion, structure, and corpus-coherence fixtures.
ML changes replay model-artifact and offline retrieval gates. Service/UI changes
replay loopback authentication and browser-facing security contracts.

Security fixes may be expedited, but they do not waive lock regeneration or
domain-specific tests or the one-domain gate. If a fix necessarily affects
multiple domains, publish an ordered series of independently reviewable
one-domain PRs. A true atomic cross-domain exception requires a prior,
machine-readable policy/checker change with its own review; prose in the
dependency PR is not an override.

A lock-only change must alter the selected record for at least one governed
direct package. Transitive-only changes fail closed. To carry an urgent
transitive fix without an exception, promote that package into a governed direct
input and assigned domain, change its selected record, and regenerate its mapped
locks in the same one-domain PR. Alternatively, implement a reviewer-bound
exception mechanism before proposing the lock change; the current schema
provides no inline waiver.

## Boundaries

This policy does not approve Google GenAI 2, renew any vulnerability or license
exception, choose the repository's license, or name accountable people. Those
remain separate R2 owner decisions. PR #30 was superseded when this policy was
integrated; its historical discussion remains useful, but its checks are not
compatibility evidence for a new lock. The 2026-08-18 transitive advisory
branch remains prohibited by the current rule until an owner chooses direct
promotion, a separately reviewed exception mechanism, or narrow time-boxed
vulnerability acceptances. The point-in-time branch and workflow state is
recorded in the
[`443dce4` evidence entry](../../evidence/2026-08-18-main-443dce4.md).
