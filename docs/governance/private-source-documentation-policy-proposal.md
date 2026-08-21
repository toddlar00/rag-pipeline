# Private-source documentation policy proposal

- **Status:** Owner decision required
- **Interim rule:** Introduce no new private-source excerpts
- **Scope:** Git history, pull requests and comments, CI artifacts, releases,
  issue trackers, documentation, evaluation assets, and generated reports
- **Status authority:**
  [ROADMAP.md owner decisions](../../ROADMAP.md#owner-decisions-and-fail-closed-interim-rules)

## Why a decision is required

The repository consistently treats root PDF inputs and generated pipeline
artifacts as private and Git-ignored. Historical contributor guidance also
prohibited committing excerpts or paraphrases from those inputs. Existing
tracked documentation and one historical PR description nevertheless contain
source-specific examples, and the draft private-corpus evaluation set contains
corpus-derived queries. A private GitHub repository reduces exposure but does
not define which of those data classes the owner intends to permit.

This proposal records the unresolved boundary without reproducing any source
content. Existing material is not precedent for adding more. History rewriting,
PR-description edits, and corpus-label approval are intentionally deferred
until the owner selects a policy.

## Data classes

| Data class | Interim treatment | Recommended long-term treatment |
| --- | --- | --- |
| Source PDFs, extracted text, chunks, screenshots, tables, model-visible excerpts, prompts containing excerpts, and generated study output | Prohibited from Git, PRs, issues, CI artifacts, and releases | Prohibited; keep only in verified private storage governed by retention policy |
| Verbatim source quotations or recovered rows in prose, tests, evaluation reports, or PR descriptions | Do not add | Prohibit; use synthetic or provenance-recorded CC0 replacements |
| Corpus-derived paraphrases, authored queries, relevance labels, and answer judgments | Keep private and draft pending owner decision | Permit only in an explicitly approved private evaluation class, or prohibit and retain solely in ignored owner-review packets |
| Corpus title, filename, page/section reference, stable record ID, chapter label, and other identifying metadata | Minimize and do not expand | Owner must enumerate which fields are permitted; prefer opaque digests and content-free aliases |
| Counts, rates, latency, rank, coverage, schema versions, cryptographic digests, configuration identities, and content-free receipts | Permitted when they cannot reconstruct source content | Permit with schema validation, minimum necessary detail, and private-path redaction |
| Synthetic fixtures and pinned CC0 evaluation material | Permitted with no private-source transformation | Permit with explicit provenance and license metadata |
| Credentials, tokens, private paths, process identities, raw exceptions, and private logs | Prohibited | Prohibited under the existing security and redaction policies |

## Owner choices

The owner should select and sign one of these boundaries before any new
private-derived artifact class, private qualification evidence, or release
evidence is created:

1. **Strict non-derivation.** Corpus-derived queries, paraphrases, labels,
   stable IDs, page references, and identifying metadata remain outside Git and
   GitHub. Only aggregates, opaque digests, content-free receipts, and
   synthetic/CC0 fixtures may be committed.
2. **Controlled private evaluation metadata.** The owner explicitly enumerates
   which corpus-derived query, label, stable-ID, and location fields may live in
   this private repository. Raw/verbatim source content remains prohibited.

The decision must also say whether existing occurrences are merely redacted in
the current tree and active PR surfaces or require a separately approved Git
history review/remediation. No history rewrite should occur implicitly.

## Known surfaces to audit after the decision

Without repeating their content, the current inventory includes:

- source-specific examples in `README.md` and the exact-byte
  [historical roadmap snapshot](../evidence/roadmap-through-2026-08-18-443dce4.md);
- the historical description of PR #37;
- `eval_queries_ethics_draft.jsonl` and the ignored owner-review packet/report;
- applicable PR comments, retained CI artifacts, and any future GitHub release
  notes; and
- contributor guidance in `CLAUDE.md` and the archived Claude plans.

The audit must search by data class, not only by the currently known strings.
It must distinguish a content-free aggregate from source-derived wording and
record the disposition of every match.

## Completion evidence

R0's policy portion is complete only when:

- the owner choice, date, scope, and reviewer are recorded;
- `CLAUDE.md`, README guidance, PR templates, evaluation tooling, and release
  instructions express the same boundary;
- the current tree, active GitHub surfaces, and retained artifacts have a
  content-class audit with no unexplained matches;
- any allowed private evaluation asset has an explicit owner approval and
  immutable digest; and
- automated checks enforce every deterministic part of the chosen policy while
  human review covers semantic paraphrase and minimum-necessary disclosure.
