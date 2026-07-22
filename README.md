# Legal RAG Pipeline

GPU-accelerated pipeline for converting law school textbooks (and other dense
PDFs) into LLM-ready markdown, queryable vector indexes, case briefs, exam
questions, and Anki flashcards. Built for legal education with optional cloud
or local LLM assistance.

Handles scanned PDFs, structure-aware chunking, TOC-based hierarchy detection,
hybrid search (BM25 + vector + cross-encoder reranking), LLM classification,
contextual retrieval, RAPTOR multi-level summaries, citation graph extraction,
and source-grounded answer generation with explicit abstention.

See [`ROADMAP.md`](ROADMAP.md) for implemented hardening milestones, merge
status, and the ordered improvement backlog.

## Architecture

```
PDF
 |
 v
[preprocess] -----> Inspect scan images + text-layer quality
 |                  - Strip background scans only when text is preserved
 |                  - Keep image-only scans intact for OCR
 |
 v
[convert] --------> Docling GPU layout model --> DoclingDocument (JSON + markdown)
 |                  - OCR auto-detection (`--ocr` / `--no-ocr` override)
 |                  - Heading hierarchy detection
 |                  - Table structure detection
 |                  - Watermark removal
 |                  - Encoding normalization (UTF-8)
 |
 v
[chunk] ----------> HybridChunker (structure-aware, token-budgeted)
 |                  - TOC-based hierarchical section paths
 |                  - Content classification (regex / LLM / zero-shot)
 |                  - LLM heading reconstruction for bare markers
 |                  - Contextual retrieval prefixes (LLM)
 |                  - Quality scoring (LLM, 1-5 scale)
 |                  - Footnote separation
 |                  - Structural filtering (TOC, index, front matter)
 |                  - Trigram Jaccard deduplication
 |                  - Chapter detection & propagation
 |
 v
Enriched Chunks (JSONL, including raw + embedding token counts)
 |
 +---> [index] ---------> ChromaDB or Qdrant (manifest-validated incremental index)
 |
 +---> [query] ---------> Hybrid search + reranking + grounded, cited answers
 |
 +---> [export] --------> Markdown / Plain text / Anki flashcards
 |
 +---> [raptor] --------> 3-level recursive summary tree
 |
 +---> [brief] ---------> Structured case briefs (Facts/Issue/Holding/Reasoning)
 |
 +---> [generate-questions] -> Exam-style hypotheticals & doctrinal questions
 |
 +---> [extract-questions] --> Q&A pairs from Notes & Questions sections
 |
 +---> [citations] -----> Citation graph (cases, statutes, cross-refs)
```

`rag.py` remains the stable command and Python compatibility facade.
`retrieval_core.py` is its standard-library-only retrieval domain: structured
search/grounding results, stable chunk identity, legal lexical analysis,
rank fusion, metadata filters, grounded prompt construction, and citation
validation. Backend clients, mutable caches, LLM calls, and CLI orchestration
remain outside that leaf module.

`artifact_io.py` is a second standard-library-only leaf for fail-closed chunk
snapshot reads, atomic file replacement, and completion-record validation.
`rag.py` injects stable-ID, hashing, cleanup, and schema policy through its
existing compatibility functions.

`chunking_core.py` is the standard-library-only text preparation layer. It
owns deterministic normalization, structural filtering, near-duplicate
detection, rule-based content classification, and basic chunk metadata helpers;
Docling, LLM enrichment, and chunk publication remain orchestrated by `rag.py`.

`index_state.py` is the standard-library-only index policy layer. It owns
collection-scoped manifest and dirty-marker rules, incremental rebuild
decisions, and query compatibility checks; `rag.py` injects schema, logging,
atomic publication, artifact hashing, and vector-store lease collaborators.

`llm_adapters.py` translates Ollama, Gemini, and OpenAI-compatible transport
responses into the typed, provider-neutral contracts in `llm_runtime.py`.
Gemini remains lazily imported, while `rag.py` retains provider selection,
runtime composition, mutable caches/throttles, and the compatibility facades.

`cli_policy.py` is the standard-library-only command policy layer for timeout
validation and scanning, resume-command serialization, provider and credential
option mapping, menu LLM detection, and secret redaction/environment routing.
`rag.py` injects live defaults and endpoint predicates while retaining argparse
definitions and dispatch, process supervision, environment mutation/restoration,
runtime mutation, pipeline execution, and output/artifact behavior.

## Quick Start

The portable command-line and CPU dependency profiles are tested on CPython
3.10 through 3.14. Clean core and core-plus-optional environments are installed
and tested on Python 3.12, with resolution checks at both ends of that range.
The RTX 50-series/CUDA 12.8 setup below intentionally requires Python 3.12-3.14.

```bash
# 1. Install PyTorch with CUDA (must come first for GPU support)
pip install "torch>=2.7,<3" --index-url https://download.pytorch.org/whl/cu128

# 2. Install the core dependencies
pip install -r requirements.txt

# 3. Full pipeline -- one command
python rag.py full --pdf Civil_procedure.pdf --force

# 4. Interactive menu (no arguments)
python rag.py
```

For a first run of `Civil_procedure.pdf`, the main artifacts are scoped to one
book directory. A temporary preprocessed PDF, when needed, is run-named beside
that directory:

```
output/
|-- Civil_procedure/
|   |-- Civil_procedure.json             # DoclingDocument
|   |-- Civil_procedure_docling.md        # Raw Docling conversion markdown
|   |-- Civil_procedure_chunks.jsonl      # Enriched chunks
|   |-- Civil_procedure.md                # Final, filtered unified export
|   |-- Civil_procedure_chroma/           # Chroma index (default backend)
|   `-- Chapters/                         # Only with --split-chapters
`-- Civil_procedure_preprocessed.pdf      # Only when preprocessing is needed
```

The raw `*_docling.md` conversion and final `.md` export are distinct files.
The default collection for this run is `civil_procedure`.

A new `full` or `batch` run never reuses an existing book directory: it creates
`Civil_procedure_2/`, then `_3/`, continuing after the highest existing run
number. The suffix is also applied to every artifact and the collection name
(for example, `civil_procedure_2`). `--force` does not change this allocation;
`--resume` reuses the latest existing run and skips its completed stages.

### Multiple textbooks

```bash
# One at a time
python rag.py full --pdf Civil_procedure.pdf
python rag.py full --pdf Torts_casebook.pdf

# Batch mode with resume (processes all, skips completed steps)
python rag.py batch Civil_procedure.pdf Torts_casebook.pdf Con_law.pdf --resume
```

## Commands

| Command | Purpose |
|---------|---------|
| `preprocess` | Safely inspect/strip background scans when a usable text layer exists |
| `convert` | PDF to DoclingDocument via GPU layout model, with automatic OCR detection |
| `chunk` | DoclingDocument to enriched chunks with metadata |
| `index` | Chunks to a manifest-validated incremental ChromaDB or Qdrant index |
| `export` | Chunks to markdown, plain text, or Anki flashcards |
| `query` | Search with auto hybrid retrieval and adaptive reranking |
| `brief` | Generate structured case briefs via LLM |
| `generate-questions` | Generate exam-style questions via LLM |
| `extract-questions` | Extract Q&A pairs from Notes & Questions sections |
| `citations` | Build citation graph (cases, statutes, cross-refs) |
| `raptor` | Build RAPTOR recursive summary tree |
| `info` | Inspect output artifacts and pipeline status |
| `full` | End-to-end: all steps in one command (with `--resume`) |
| `batch` | Process multiple PDFs end-to-end with per-PDF resume |

Global flags: `-v` / `--verbose` (DEBUG output), `--quiet` (warnings only).

### Hard operation deadlines

Commands that can open a vector store run in an isolated worker process. Use
`--operation-timeout SECONDS` to replace the positive wall-clock deadline; a
timeout terminates the Windows worker Job Object or the POSIX worker process
group, releases its operating-system lease, retains any interrupted-update
marker, prints no command arguments or secrets, and exits with status `124`.
Guided-menu index, query, info, full, and batch actions use the same boundary.
On POSIX, a descendant that deliberately starts a new session is outside the
process-group guarantee. Defaults are:

| Command | Deadline |
|---------|----------|
| `query` | 300 seconds |
| `info` | 120 seconds |
| `index` | 7,200 seconds |
| `full` | 14,400 seconds |
| `batch` | 43,200 seconds |
| `eval.py` | 14,400 seconds |

The Web UI applies the same isolated boundary to the vector-store work in each
Search and Info callback; its defaults are 300 and 120 seconds. Override them
with `--search-timeout` and `--info-timeout` when starting `ui.py`.

`--db-lock-timeout` only bounds how long a worker waits to acquire the database
lease. `--operation-timeout` bounds the whole isolated command, including a
storage call that never returns. Direct Python API calls remain in the caller's
process; applications requiring a hard cancellation boundary should invoke the
CLI or isolate those calls in their own supervised process.

### Interactive Menu

Running `python rag.py` with no arguments launches a guided menu that walks
through file selection, options, and command construction. All commands are
accessible through the menu, with file path validation and sensible defaults.
For LLM-backed operations, the menu also offers DeepSeek V4 Pro/Flash, MiniMax,
Ollama, Gemini, and custom providers. API keys entered there use a hidden prompt
and are redacted from the generated command shown on screen. They are removed
from worker process arguments and supplied only through that worker's child-only
environment; press Enter at the key prompt to use the corresponding ambient
environment variable instead.

## Searching

The standalone `query` command cannot infer which book run to use. Pass that
run's database, chunks file, and collection explicitly. These examples target
the first `Civil_procedure.pdf` run shown above.

```bash
# Auto mode (default): calibrated hybrid when available; vector fallback
python rag.py query "minimum contacts personal jurisdiction" \
  --db output/Civil_procedure/Civil_procedure_chroma \
  --chunks output/Civil_procedure/Civil_procedure_chunks.jsonl \
  --collection civil_procedure

# Require hybrid search rather than allowing a vector fallback
python rag.py query "Rule 12(b)(6) motion to dismiss" --hybrid \
  --db output/Civil_procedure/Civil_procedure_chroma \
  --chunks output/Civil_procedure/Civil_procedure_chunks.jsonl \
  --collection civil_procedure

# Search + source-grounded LLM answer with validated citation IDs
python rag.py query "minimum contacts test" --answer \
  --db output/Civil_procedure/Civil_procedure_chroma \
  --chunks output/Civil_procedure/Civil_procedure_chunks.jsonl \
  --collection civil_procedure

# Filter by content type
python rag.py query "stream of commerce" --type case_opinion \
  --db output/Civil_procedure/Civil_procedure_chroma \
  --chunks output/Civil_procedure/Civil_procedure_chunks.jsonl \
  --collection civil_procedure

# Filter by chapter
python rag.py query "supplemental jurisdiction" --chapter 12 \
  --db output/Civil_procedure/Civil_procedure_chroma \
  --chunks output/Civil_procedure/Civil_procedure_chunks.jsonl \
  --collection civil_procedure

# JSON output for programmatic use
python rag.py query "Erie doctrine" --json -n 10 \
  --db output/Civil_procedure/Civil_procedure_chroma \
  --chunks output/Civil_procedure/Civil_procedure_chunks.jsonl \
  --collection civil_procedure

# Force vector-only retrieval and cross-encoder reranking
python rag.py query "due process" --vector-only --rerank \
  --db output/Civil_procedure/Civil_procedure_chroma \
  --chunks output/Civil_procedure/Civil_procedure_chunks.jsonl \
  --collection civil_procedure

# Qdrant backend with native hybrid search
python rag.py query "Rule 12(b)(6)" --db-backend qdrant --hybrid \
  --db output/Civil_procedure/Civil_procedure_qdrant \
  --chunks output/Civil_procedure/Civil_procedure_chunks.jsonl \
  --collection civil_procedure
```

### How search works

```
Query --> [1] Embedding (nomic MoE) --> Vector similarity (ChromaDB/Qdrant)
                                              |
      --> [2] Legal analyzer + BM25 ---> weighted RRF (auto/--hybrid)
                                              |
                                              v
                                  [3] Adaptive reranker (BGE)
                                              |
                                              v
                                        Top-K results
                                              |
                                              v
                                     [4] LLM answer (if --answer)
```

1. **Vector search** retrieves semantically similar chunks.
2. **Legal lexical search** normalizes citation typography and aliases such as
   `§ 1332`, `U.S.C.`, and `Fed. R. Civ. P. 12(b)(6)`, then searches raw text
   plus bounded case, section, heading, and context metadata.
3. **RRF fusion** combines the rankings. Chroma defaults to the judged-set
   calibration `dense=0.5`, `lexical=1.0`, `k=10`; Qdrant uses native RRF.
4. **Adaptive reranking** reranks vector fallback automatically but preserves
   calibrated hybrid order. `--rerank` forces BGE reranking and `--no-rerank`
   disables it.
5. **Answer generation** (optional) gives each retrieved source a stable ID and
   requires the configured LLM to cite those sources as `[S1]`, `[S2]`, and so on.

Use `--vector-only` to suppress lexical retrieval. Advanced reproducibility
controls are `--overfetch`, `--rrf-k`, `--dense-weight`, `--sparse-weight`, and
`--reranker-model`. Search responses distinguish the requested mode (`auto`,
`hybrid`, or `vector`) from the effective mode after any safe fallback.

### Answer Generation (`--answer`)

After retrieval and reranking, the `--answer` flag assigns the retrieved chunks
query-local citation labels such as `[S1]`, while retaining a stable source ID
for each chunk. Retrieved text is marked as untrusted evidence in the prompt.
The generated answer may cite only supplied source labels. Unknown labels,
uncited answer paragraphs, or direct quotations absent from the exact supplied
source excerpt cause the pipeline to withhold the entire answer. Long chunks use
a query-centered excerpt so matching evidence near the end is not discarded.
Empty responses and explicit insufficient-evidence responses also abstain.

The terminal output prints the answer and cited source locations before the
ordinary ranked results. Combine `--answer` with `--json` for a structured
payload:

```json
{
  "answer": "The governing rule is ... [S1].",
  "citations": ["S1"],
  "sources": {
    "S1": {
      "source_id": "chunk_0123456789abcdef",
      "score": 0.92,
      "metadata": {"page_range": "pp.101-103"},
      "excerpt": "..."
    }
  },
  "answer_warnings": [],
  "abstained": false,
  "results": []
}
```

`sources` maps query-local labels to stable IDs, retrieval scores, metadata,
and excerpts. `answer_warnings` records removed/invalid citations or provider
failures, and `abstained` is `true` whenever an unsupported answer was withheld.

```bash
python rag.py query "What is the minimum contacts test?" --answer \
  --db output/Civil_procedure/Civil_procedure_chroma \
  --chunks output/Civil_procedure/Civil_procedure_chunks.jsonl \
  --collection civil_procedure
```

## Optional LLM Intelligence Layer

MiniMax M2.7-highspeed remains the default cloud model. DeepSeek is also
supported directly through its official `https://api.deepseek.com` endpoint,
with `deepseek-v4-pro` and `deepseek-v4-flash` as the built-in model choices.
See the official [DeepSeek API documentation](https://api-docs.deepseek.com/)
and [thinking-mode guide](https://api-docs.deepseek.com/guides/thinking_mode/).

LLM-enabled operations include content classification, contextual retrieval
prefixes, RAPTOR summaries, case briefs, exam questions, flashcards, quality
scoring, heading reconstruction, answer generation, and optional TOC scaffold
parsing/review (`--llm-scaffold`).

**Provider chain**: configured OpenAI-compatible cloud API -> Ollama (local) ->
Gemini (API) -> deterministic fallback where the feature supports one. A cloud
provider is skipped when it has no key; a failed or empty response falls through
to the next provider. Features without a deterministic fallback return no LLM
result after all configured providers fail.

### Reproducible LLM execution

Every LLM-backed CLI command uses a shared execution layer that records the
actual provider/model selected, applies run-wide budgets, coalesces identical
in-flight requests, and can reuse successful results across runs. Existing
Python integrations remain compatible: `_call_llm(...)` still returns
`str | None`, while `_call_llm_result(...)` exposes structured provenance.

CLI calls use a persistent, machine-local response cache by default. Direct
Python calls default to `off`, preserving the original library behavior. A
cache key covers the exact prompt digest, operation and prompt versions,
generation settings, timeout, fallback policy, and ordered provider/model/
endpoint identities. Prompts, API keys, and raw endpoint URLs are not stored in
the cache key or record. Only non-empty successful responses are cached, and
entries use integrity hashes plus atomic replacement so truncated or tampered
records become misses and are repaired by the next successful call.

The cache itself contains successful response text in plaintext. Treat its
directory as sensitive when textbook excerpts, client facts, or other private
material can appear in model output. Use `--llm-cache-mode off` for no disk
cache, `readonly` to consume existing entries without writing, or a protected
`--llm-cache-dir`. The default location is the platform user-cache directory
(`%LOCALAPPDATA%/rag-pipeline/llm-cache` on Windows when available,
`$XDG_CACHE_HOME/rag-pipeline/llm-cache` on Linux when configured).

| Option | Purpose |
|--------|---------|
| `--llm-cache-mode readwrite|readonly|refresh|off` | Read/write policy; `refresh` bypasses a hit and replaces it after live success |
| `--llm-cache-dir PATH` | Override the machine-local response-cache directory |
| `--llm-events PATH` | Append one prompt-free JSONL event per logical request |
| `--llm-report PATH` | Atomically write a prompt-free aggregate run report, including on handled failures |
| `--llm-fallback ordered|none` | Use the provider chain or only its first configured provider |
| `--llm-failure-policy best-effort|strict` | Preserve feature-level fallbacks or fail when no provider returns output |
| `--max-llm-calls N` | Hard cap on logical provider callback dispatches during the run |
| `--max-llm-reserved-tokens N` | Hard cap on conservative prompt-plus-maximum-output token reservations |

```bash
# Reproducible generation with an aggregate report and explicit ceilings
python rag.py generate-questions \
  --chunks output/Civil_procedure/Civil_procedure_chunks.jsonl \
  --llm-report output/Civil_procedure/llm-run.json \
  --max-llm-calls 50 --max-llm-reserved-tokens 250000

# Retry the preferred cloud provider even if a fallback result is cached
python rag.py brief \
  --chunks output/Civil_procedure/Civil_procedure_chunks.jsonl \
  --llm-cache-mode refresh

# Require the selected first provider and fail on unavailable output
python rag.py query "What is the Erie doctrine?" --answer \
  --llm-fallback none --llm-failure-policy strict \
  --db output/Civil_procedure/Civil_procedure_chroma \
  --chunks output/Civil_procedure/Civil_procedure_chunks.jsonl \
  --collection civil_procedure
```

Caching is chain-level: if Ollama or Gemini succeeds after the preferred cloud
provider fails, that successful fallback remains the warm result. Use
`--llm-cache-mode refresh` to retry the preferred provider and replace it.
Single-flight coalescing and budgets are process-local; separate processes do
not share admissions. Event logs and reports include stable request IDs,
operation labels, provider/model names, latency, fallback paths, cache status,
logical provider dispatches, underlying transport attempts/retries, and a
secret-safe category for each failed attempt. They omit prompts, responses,
credentials, raw endpoints, response bodies, and exception messages.

Token accounting uses provider-native fields when available: OpenAI-compatible
`usage`, Ollama's prompt/evaluation counts, and Gemini usage metadata. Reports
separate exact, estimated, and unavailable usage and retain cached-prompt and
reasoning-token breakdowns. Legacy/custom callbacks without native counts use
the conservative character estimate. Cache hits preserve the selected result's
token accounting but correctly report zero live provider or transport attempts.

Budget terminology is intentionally conservative. `--max-llm-calls` counts
logical provider dispatches; the OpenAI-compatible adapter may make one extra
HTTP attempt after a 429 within a dispatch, and the report records that retry
separately. Gemini receives the configured timeout and has SDK retries disabled
so its observed transport count remains explicit. Reserved tokens use a
provider-neutral `characters / 4 + max output` estimate and accumulate for each
fallback attempt. Cache hits and callers sharing an in-flight request consume
no additional budget.

### DeepSeek V4 configuration

The shorter LLM option names and the existing cloud option names are aliases:

| Option | Equivalent option | Purpose |
|--------|-------------------|---------|
| `--llm-url URL` | `--cloud-url URL` | OpenAI-compatible API base URL |
| `--llm-model MODEL` | `--cloud-model MODEL` | Cloud model name |
| `--api-key KEY` | `--cloud-key KEY` | One-off, hand-entered cloud API key |
| `--thinking` | — | Enable supported model reasoning |
| `--no-thinking` | — | Explicitly disable supported model reasoning (the default) |

Selecting a `deepseek-*` model while the URL is still at its MiniMax default
automatically selects the official DeepSeek endpoint. Conversely, selecting the
official DeepSeek endpoint without changing the default model selects
`deepseek-v4-pro`. `--thinking` / `--no-thinking` controls DeepSeek's thinking
mode and is also forwarded to Ollama's `think` option.

Cloud API keys are resolved without reusing a provider-specific secret for an
unrelated host:

1. `--api-key` / `--cloud-key`, when supplied, always wins.
2. DeepSeek uses `DEEPSEEK_API_KEY`, then falls back to `CLOUD_API_KEY`.
3. MiniMax uses `MINIMAX_API_KEY`, then falls back to `CLOUD_API_KEY`.
4. Any other custom cloud endpoint uses only `CLOUD_API_KEY`.

```bash
# DeepSeek V4 Pro with thinking (set DEEPSEEK_API_KEY first)
python rag.py generate-questions \
  --chunks output/Civil_procedure/Civil_procedure_chunks.jsonl \
  --llm-model deepseek-v4-pro --thinking

# DeepSeek V4 Flash with thinking explicitly disabled
python rag.py brief \
  --chunks output/Civil_procedure/Civil_procedure_chunks.jsonl \
  --llm-url https://api.deepseek.com \
  --llm-model deepseek-v4-flash --no-thinking

# Use the default MiniMax cloud model (requires MINIMAX_API_KEY)
python rag.py chunk \
  --doc output/Civil_procedure/Civil_procedure.json \
  --out output/Civil_procedure/Civil_procedure_chunks.jsonl \
  --llm-classify --contextualize

# Configure the local Ollama fallback (unset cloud keys to use it first)
python rag.py chunk \
  --doc output/Civil_procedure/Civil_procedure.json \
  --out output/Civil_procedure/Civil_procedure_chunks.jsonl \
  --llm-classify --ollama-url http://127.0.0.1:11434 --ollama-model qwen3:30b

# Configure the Gemini fallback
python rag.py chunk \
  --doc output/Civil_procedure/Civil_procedure.json \
  --out output/Civil_procedure/Civil_procedure_chunks.jsonl \
  --llm-classify --gemini-key YOUR_KEY

# Custom OpenAI-compatible endpoint
python rag.py chunk \
  --doc output/Civil_procedure/Civil_procedure.json \
  --out output/Civil_procedure/Civil_procedure_chunks.jsonl \
  --llm-classify --cloud-url https://api.example.com/v1 \
  --cloud-model model-name --cloud-key KEY
```

### Adaptive Rate Limiting

The pipeline automatically handles API rate limits (429 errors) with an adaptive
throttle. Starting at `--llm-workers` (default 10), it halves active workers on
each 429, adds cooldown delays, and gradually recovers after 20 consecutive
successes. No manual intervention needed.

```bash
# Start with 10 parallel workers (default)
python rag.py full --pdf book.pdf --llm-classify --llm-workers 10

# Conservative start for strict rate limits
python rag.py full --pdf book.pdf --llm-classify --llm-workers 4
```

## Case Briefs

Generate structured case briefs from every `case_opinion` chunk. Each brief
extracts Facts, Issue, Holding, and Reasoning.

```bash
python rag.py brief \
  --chunks output/Civil_procedure/Civil_procedure_chunks.jsonl \
  -o output/Civil_procedure/Civil_procedure_briefs.jsonl
```

Output (`output/Civil_procedure/Civil_procedure_briefs.jsonl`):
```json
{
  "case_name": "International Shoe Co. v. Washington",
  "facts": "International Shoe, a Delaware corporation with its principal place...",
  "issue": "Whether a state may exercise personal jurisdiction over a corporation...",
  "holding": "The Court held that a state may exercise jurisdiction over...",
  "reasoning": "The Court established the 'minimum contacts' framework...",
  "page_range": "pp.101-106",
  "chapter_num": 3
}
```

## Exam Question Generation

Generate law school exam-style questions from chapter content. For each chapter,
the LLM produces 5 questions: 2 issue-spotter hypotheticals, 2 doctrinal
questions, and 1 policy question.

```bash
python rag.py generate-questions \
  --chunks output/Civil_procedure/Civil_procedure_chunks.jsonl \
  -o output/Civil_procedure/Civil_procedure_exam_questions.jsonl
```

Output (`output/Civil_procedure/Civil_procedure_exam_questions.jsonl`):
```json
{
  "question": "Plaintiff, a Texas resident, purchases a defective widget online...",
  "question_type": "issue_spotter",
  "chapter_num": 3,
  "chapter_title": "Personal Jurisdiction",
  "suggested_answer": "The key issue is whether the defendant has sufficient...",
  "source_chunks": [42, 43, 47]
}
```

## RAPTOR Summaries

Build a 3-level recursive summary tree (Recursive Abstractive Processing for
Tree-Organized Retrieval). Level 0 = raw chunks, Level 1 = section summaries
(~5-10 chunks clustered), Level 2 = chapter summaries.

```bash
python rag.py raptor \
  --chunks output/Civil_procedure/Civil_procedure_chunks.jsonl
python rag.py full --pdf book.pdf --raptor
```

RAPTOR nodes are written to a separate summary-tree JSON file. The pipeline
does not add those summaries to the vector index automatically.

## Export Formats

Single-file markdown is the default. `full --split-chapters` keeps that unified
file and additionally creates `Chapters/`; without the flag, no chapter
directory is created. On the standalone `export` command, `--split-chapters`
writes the chapter files instead of a unified markdown file.

```bash
# Markdown -- readable, with headings and formatting
python rag.py export \
  --chunks output/Civil_procedure/Civil_procedure_chunks.jsonl \
  -o output/Civil_procedure/Civil_procedure.md --format markdown

# Split into one file per chapter (ideal for Claude Projects / NotebookLM)
python rag.py export \
  --chunks output/Civil_procedure/Civil_procedure_chunks.jsonl \
  -o output/Civil_procedure/Civil_procedure.md \
  --format markdown --split-chapters

# Plain text + JSON metadata sidecar (for Anthropic citations API)
python rag.py export \
  --chunks output/Civil_procedure/Civil_procedure_chunks.jsonl \
  -o output/Civil_procedure/Civil_procedure.txt --format plaintext

# Anki flashcards -- LLM-generated Q&A pairs as TSV
python rag.py export \
  --chunks output/Civil_procedure/Civil_procedure_chunks.jsonl \
  -o output/Civil_procedure/Civil_procedure_flashcards.tsv --format flashcards

# Filter by content type
python rag.py export \
  --chunks output/Civil_procedure/Civil_procedure_chunks.jsonl \
  -o output/Civil_procedure/Civil_procedure_cases.md \
  --include-types case_opinion notes_and_questions

# Filter by chapter
python rag.py export \
  --chunks output/Civil_procedure/Civil_procedure_chunks.jsonl \
  -o output/Civil_procedure/Civil_procedure_selected.md --chapters 3 5 12

# Combine filters
python rag.py export \
  --chunks output/Civil_procedure/Civil_procedure_chunks.jsonl \
  -o output/Civil_procedure/Civil_procedure.md \
  --chapters 3 --include-types case_opinion --split-chapters
```

### Using with Claude Projects / NotebookLM

Upload the exported markdown for clean, structured content. Split chapters give
best retrieval:

```bash
python rag.py export \
  --chunks output/Civil_procedure/Civil_procedure_chunks.jsonl \
  -o output/Civil_procedure/Civil_procedure.md --split-chapters
```

Output:
```
output/Civil_procedure/Chapters/
  ch02_Subject_Matter_Jurisdiction.md
  ch03_Personal_Jurisdiction.md
  ch13_Special_Multiparty_Litigation.md
  front_matter.md
```

## AI Model Integration

### Embedding Models (`--embedding-model`)

Embeddings are computed at `index` time and stored. Chunking automatically caps
`--max-tokens` to a known model input limit; when `--contextualize` is enabled,
it also reserves room for the contextual prefix. Every new chunk records both
its raw `token_count` and its final `embedding_token_count` (context included).
Indexing recomputes final counts with the provider/model tokenizer where
available (using a conservative fallback), rejects oversized chunks, and sizes
API batches by aggregate tokens rather than record count. Legacy JSONL without
count fields is rechecked during indexing.

| Model | Type | Cost | Max Tokens | Best for |
|-------|------|------|-----------|----------|
| `nomic-ai/nomic-embed-text-v2-moe` | Local GPU | Free | 8192 | **Default.** Best open-source. |
| `voyage-law-2` | Voyage API | ~$0.12/M tokens | 16000 | Legal-specific. Trained on case law. |
| `voyage-3-large` | Voyage API | ~$0.18/M tokens | 16000 | Best general Voyage model. |
| `text-embedding-3-large` | OpenAI API | $0.13/M tokens | 8191 | Best commercial general-purpose. |
| `embed-v4.0` | Cohere API | $0.10/M tokens | - | Multilingual. |
| `dunzhang/stella_en_400M_v5` | Local GPU | Free | 8192 | High quality (requires xformers). |
| `nlpaueb/legal-bert-base-uncased` | Local GPU | Free | 512 | Legacy, EU legal text. |

API models require env vars: `VOYAGE_API_KEY`, `OPENAI_API_KEY`, or `COHERE_API_KEY`.
The pipeline validates keys at startup before any heavy processing.

### Cross-Encoder Reranker

Auto mode reranks vector-only results but leaves the calibrated hybrid ranking
intact. `--rerank` forces reranking for either retrieval mode. The pipeline
over-fetches 4x candidates by default, then BGE
(`BAAI/bge-reranker-v2-m3`) scores each query against a bounded representation
containing case, section, heading, chapter, context, and raw text. Returned text
remains the original chunk. Local models are lazy-loaded and cached by exact
model name, so switching rerankers cannot silently reuse the wrong model.

Additional reranker backends: Cohere (`cohere-rerank-*`, requires API key) and
Jina (`jina-reranker-*`, requires API key).

Use `--no-rerank` to disable it, `--reranker-model` to select a model, and
`--overfetch` to control candidate depth.

### LLM Features

LLM-enabled features try the configured OpenAI-compatible cloud endpoint first
(MiniMax M2.7-highspeed by default, or DeepSeek/custom when selected), then
local Ollama at `http://127.0.0.1:11434`, then Gemini when configured. See
[DeepSeek V4 configuration](#deepseek-v4-configuration) for model, key, and
thinking options. Ordinary chunking and TOC scaffold construction are
deterministic and make no LLM calls unless an LLM feature flag is supplied.

| Feature | Flag / Command | What it does |
|---------|---------------|--------------|
| Content classification | `--llm-classify` | Replaces regex type detection with LLM inference per chunk |
| Contextual retrieval | `--contextualize` | Generates 1-2 sentence context prefix per chunk (Anthropic pattern) |
| Heading reconstruction | `--reconstruct-headings` | Infers full section paths for bare headings ("B", "III") |
| Quality scoring | `--quality-score` | Rates each chunk 1-5 for RAG usefulness |
| Answer generation | `--answer` (on query) | Produces source-cited answers and abstains when citations are unsupported |
| Case briefs | `brief` command | Structured Facts/Issue/Holding/Reasoning per case |
| Exam questions | `generate-questions` command | Issue-spotters, doctrinal, and policy questions |
| Flashcard export | `export --format flashcards` | Anki-compatible Q&A pairs |
| RAPTOR summaries | `raptor` command | 3-level recursive summary tree |
| TOC scaffold review | `--llm-scaffold` | Adds LLM layout analysis, hierarchy parsing, and validation to deterministic TOC parsing |

### Vector Database (`--db-backend`)

| Backend | Hybrid Search | Incremental | Best for |
|---------|--------------|-------------|----------|
| `chroma` (default) | BM25 + RRF (manual) | Manifest + hashes | Simple local use |
| `qdrant` | Native dense + sparse fusion | Manifest + hashes | Better hybrid, faster queries |

```bash
python rag.py full --pdf Civil_procedure.pdf --db-backend qdrant
python rag.py query "jurisdiction" --db-backend qdrant --hybrid \
  --db output/Civil_procedure/Civil_procedure_qdrant \
  --chunks output/Civil_procedure/Civil_procedure_chunks.jsonl \
  --collection civil_procedure
```

### Incremental Re-indexing

The `index` command hashes each chunk's text and indexable metadata and stores
the hashes in an atomic, versioned manifest scoped to the database backend and
collection. The manifest also records its schema version, embedding model,
embedding dimension, exact source JSONL SHA-256, and source record count. A
compatible rerun embeds only changed/new chunks, removes chunks no longer
present, and skips unchanged chunks. Qdrant incremental runs scan payload-only
stable IDs before mutation and again after writes, refusing to advance the
manifest if points are missing, unexpected, duplicated, untracked, or returned
through a cyclic pagination sequence. Each audit is bounded by exact point
counts taken before and after its payload-only scroll, and rejects count drift,
premature termination, oversized/non-progressing pages, and repeated physical
point IDs. Use `--full-reindex` to recover from a physical collection/manifest
mismatch. Waited point deletes and upserts must also report Qdrant's
`completed` status before reconciliation or manifest commit can continue.
Chroma compatible runs perform the corresponding count-sandwiched, bounded
scan of API-visible document IDs and their `stable_id` metadata before any
incremental mutation, after deletions, and after all upserts. Changed durable
IDs are deleted and verified absent before replacement, so silent delete or
upsert no-ops cannot advance the manifest. Chroma delete batches respect the
client's advertised maximum size when available.

Before its first collection mutation, either backend creates a collection-
scoped recovery marker. The marker is removed only after all writes finish and
the atomic manifest replacement succeeds (including Qdrant's exact post-write
verification). A per-update token binds marker reuse and cleanup to the run that
created or safely replaced it, so a stale or concurrently replaced marker
cannot be declared clean. If a run is interrupted or fails after mutation
begins, queries, exact count inspection, and corpus-pinned evaluations fail
closed while the marker remains; the next `index` or `full --resume` run
rebuilds only that backend/collection and clears the marker after the recovered
index is committed. These identity scans detect count and set drift during
pagination; the recovery marker records crash state and is never used as a
mutex.

Every operation that opens a local vector client or relies on marker/manifest
consistency now takes a bounded, OS-backed exclusive lease for the canonical
database directory. The path-wide policy deliberately serializes reads as well
as writes for both backends because Qdrant local mode permits only one process
to open a database path. Artifact discovery and approximate directory-size
display remain best-effort filesystem diagnostics outside the lease; exact
collection counts are leased. The default wait is 30 seconds; use
`--db-lock-timeout SECONDS` on `index`, `query`, `info`, `full`, or `batch`
(`0` means fail fast). Same-thread nested validation is reentrant, lock release
runs after client/manifest cleanup, and a killed process releases the kernel
lock. Persistent `.rag-locks/*.lock` sidecars are only locking inodes: their
existence never means a process owns the lease, and they must not be manually
deleted while operations may be active. These OS locks coordinate processes on
one filesystem host; a Dropbox-synchronized copy on another computer is a
separate concurrency domain. Windows extended drive/UNC spellings are
normalized, but equivalent drive-letter, administrative-UNC, and SUBST aliases
are not a supported way to access one live database. Use one path spelling and
do not move or rename a database directory while any operation may be active.
The lease timeout bounds only lock contention. The outer CLI/evaluation/menu
operation deadline bounds filesystem hydration, sentinel setup, and
storage-client calls. Web UI vector-client calls have their own isolated
deadlines; its best-effort artifact-size scan remains outside that boundary.

`full` and `batch` also serialize output-run allocation before choosing a book
suffix. When they regenerate chunks, they create the collection recovery marker
before work begins, publish the JSONL via atomic replacement, and retain the
vector lease until the matching index commits. A crash therefore exposes
neither partial JSONL nor an apparently clean old index paired with a new
corpus. Chroma hybrid search independently compares the chunks SHA-256 with the
manifest, parses and hashes one exact file-handle snapshot, and refuses
cross-generation lexical/vector fusion until reindexing. A legacy Chroma index
without a manifested source SHA-256 falls back to vector retrieval with a
warning instead of fusing unproven lexical data. Reranking begins only after the
retrieval lease and vector client are released, so model or API latency does not
block unrelated index access.

Conversion JSON/Markdown pairs, unified Markdown, chapter sets, RAPTOR trees,
and evaluation reports are published through flushed same-directory temporary
files. Versioned completion metadata binds resumable pipeline artifacts to the
exact source digest, record count, credential-free parameters, and output
hashes. A hard kill before the final completion commit therefore leaves the
stage fail-closed: `full --resume` regenerates it instead of accepting a
truncated, stale, or partial artifact set. Legacy pipeline artifacts without
completion metadata regenerate once when resumed.

The sequential background-upsert paths for both backends share teardown that
requests a worker stop, waits for completion, and attempts both executor and
progress closure; progress advances only after a write succeeds, and a
secondary cleanup error does not replace the original producer error. Chroma's
parallel API-embedding path likewise shuts down its executor and closes its
progress display before committing the manifest; an embedding or upsert error
takes precedence over either cleanup failure.

Every short-lived Chroma and Qdrant client used by indexing, search, and status
inspection is also closed deterministically. Indexing closes the client after
physical reconciliation but before manifest commit, so a cleanup failure keeps
the recovery marker and old manifest instead of reporting success while a
Windows database lock remains held. Operation errors take precedence over
secondary close errors. Chroma 1.5.2 is the minimum supported release because
it provides the public, reference-counted `close()` needed to release shared
local database handles without invalidating another live client.

If the model, vector dimension, or manifest schema changes—or an older shared
`chunk_hashes.json` sidecar is encountered—the pipeline safely rebuilds only
the requested collection. Sibling collections in the same database directory
are not deleted. The old sidecar is left in place for compatibility while the
rebuilt collection receives its own manifest. Manifest replacement is atomic,
so an interrupted write cannot leave partially written incremental state.
Chunk JSONL is parsed and schema-checked strictly before a collection can be
changed. `full --resume` always revalidates the manifest and hashes, and queries
refuse a model or vector dimension that conflicts with an existing manifest.

```bash
python rag.py index \
  --chunks output/Civil_procedure/Civil_procedure_chunks.jsonl \
  --db output/Civil_procedure/Civil_procedure_chroma \
  --collection civil_procedure                    # first run: indexes all
python rag.py index \
  --chunks output/Civil_procedure/Civil_procedure_chunks.jsonl \
  --db output/Civil_procedure/Civil_procedure_chroma \
  --collection civil_procedure                    # unchanged chunks are skipped
python rag.py index --full-reindex \
  --chunks output/Civil_procedure/Civil_procedure_chunks.jsonl \
  --db output/Civil_procedure/Civil_procedure_chroma \
  --collection civil_procedure                    # force complete rebuild
```

Use the same `--embedding-model` when querying the collection. Passing a new
model to `index` automatically triggers the safe collection rebuild described
above; `--full-reindex` remains available when an unconditional rebuild is
desired.

## TOC-Based Hierarchy Detection

The pipeline extracts authoritative document structure from the Table of Contents
rather than relying solely on heading detection. It supports two methods:

1. **Column-position parsing**: Scans first 25 pages for TOC tables, maps column
   positions to heading depth (col 0 = chapter, col 1 = section, col 2 = sub).
2. **LLM-assisted parsing** (opt-in with `--llm-scaffold`): Sends TOC text to the
   configured provider in ~100-line batches for structured extraction and
   validation of levels, titles, and page numbers.

This produces section paths like `Chapter 3 > B. Federalism > 2. Specific Jurisdiction`
instead of flat `B` or `III`. In testing, TOC detection raised multi-level
section paths from 0% to 84%.

## Scaffold-to-Markdown CLI

`scaffold_to_markdown.py` applies an existing JSON TOC scaffold to its source
PDF. Its argparse interface has two mutually exclusive modes:

```bash
# One book: both positional paths are required; -o/--out is an output file
python scaffold_to_markdown.py Book_scaffold.json Book.pdf -o Book.md

# Batch discovery: no positional paths; -o/--out is an output directory
python scaffold_to_markdown.py --all --root path/to/workspace \
  --out path/to/markdown
```

Without `-o`, each output is written beside its scaffold (using the scaffold's
source filename when available). `--all` recursively discovers
`*_scaffold.json` files beneath `--root` (the current directory by default),
prefers a case-insensitive exact PDF stem match, and falls back to a prefix
match. `--root` is valid only with `--all`, and `--all` cannot be combined with
the positional scaffold or PDF paths.

## Resume Capability

The `--resume` flag on `full` and `batch` commands skips completed stages after
validating their artifacts as follows:

| Stage | Checks for |
|-------|-----------|
| Convert | Valid, non-empty DoclingDocument JSON |
| Chunk | Valid JSONL containing chunk records |
| Index | Vector DB collection whose document count matches the chunks file |
| Export | Markdown file |
| Chapter export | Non-empty `Chapters/` directory (when requested) |
| RAPTOR | Summary tree JSON |

```bash
# Start a long pipeline
python rag.py full --pdf big_book.pdf --llm-classify --contextualize --raptor

# Interrupted? Resume where it left off
python rag.py full --pdf big_book.pdf --llm-classify --contextualize --raptor --resume

# Batch mode resumes per PDF
python rag.py batch *.pdf --resume
```

On failure, the pipeline prints a ready-to-paste resume command.

## Question Extraction

Extract individual Q&A pairs from "Notes and Questions" sections. Each question
is paired with the preceding chunk as context (usually the case it follows).

```bash
python rag.py extract-questions \
  --chunks output/Civil_procedure/Civil_procedure_chunks.jsonl \
  -o output/Civil_procedure/Civil_procedure_questions.jsonl
```

Output (`output/Civil_procedure/Civil_procedure_questions.jsonl`):
```json
{
  "question": "Does Hanna overrule Byrd?",
  "context_chunk_index": 41,
  "context_text": "In Byrd v. Blue Ridge...",
  "context_case": "Byrd v. Blue Ridge Rural Electrical Cooperative, Inc",
  "chapter_num": 3,
  "section_path": "Chapter 3 > Erie Doctrine"
}
```

## Citation Graph

Parse case citations, statute references, and chapter cross-references from
all chunks into a structured graph.

```bash
python rag.py citations \
  --chunks output/Civil_procedure/Civil_procedure_chunks.jsonl \
  -o output/Civil_procedure/Civil_procedure_citations.json
```

Output (`output/Civil_procedure/Civil_procedure_citations.json`):
```json
{
  "nodes": [
    {"id": "chunk_42", "type": "chunk", "label": "Chapter 3 > Minimum Contacts"},
    {"id": "international_shoe_co__v__washington", "type": "case", "label": "International Shoe Co. v. Washington", "year": "1945"},
    {"id": "28_u_s_c__1332", "type": "statute", "label": "28 U.S.C. 1332"}
  ],
  "edges": [
    {"source": "chunk_42", "target": "international_shoe_co__v__washington", "type": "cites_case"},
    {"source": "chunk_42", "target": "28_u_s_c__1332", "type": "cites_statute"}
  ],
  "stats": {"chunk_nodes": 1093, "case_nodes": 1446, "statute_nodes": 287, "total_edges": 2788}
}
```

## Chunk Metadata Schema

Each enriched chunk carries:

| Field | Type | Description |
|-------|------|-------------|
| `content_type` | str | `case_opinion`, `notes_and_questions`, `author_narrative`, `statutory_excerpt`, `table`, `footnote`, `chapter_introduction`, `structural` |
| `section_path` | str | Heading breadcrumb: `Chapter 3 > B. Federalism > 2. Specific Jurisdiction` |
| `chapter_num` | int/null | Chapter number (propagated forward through chunks) |
| `chapter_title` | str/null | Chapter title |
| `case_names` | list[str] | All case names detected in the chunk |
| `primary_case` | str/null | First case name |
| `page_range` | str | `pp.101-106` (from Docling provenance or estimated) |
| `cross_references` | list[str] | Chapter cross-refs: `Ch.12`, `Ch.12.F.2` |
| `context` | str | LLM-generated context prefix (with `--contextualize`) |
| `headings` | list[str] | Full heading hierarchy from Docling layout model |
| `quality_score` | int/null | LLM-rated usefulness 1-5 (with `--quality-score`) |
| `token_count` | int | Token count of the raw chunk before any contextual prefix |
| `embedding_token_count` | int | Token count of the exact contextualized text sent for embedding |
| `chunk_index` | int | Positional index in output |

### Content Types

| Type | Detection | Example |
|------|-----------|---------|
| `case_opinion` | Judge names, procedural terms, holdings | *International Shoe Co. v. Washington* |
| `notes_and_questions` | "Notes and Questions" headings, numbered prompts | Discussion questions after cases |
| `author_narrative` | Default for expository text | Professor's analysis and commentary |
| `statutory_excerpt` | U.S.C., Rule, statute markers | Fed. R. Civ. P. 12(b)(6) text |
| `table` | Pipe/tab-delimited lines | Jurisdiction comparison charts |
| `footnote` | Numbered refs + citation density (Id., supra) | Case footnotes |
| `chapter_introduction` | Chapter heading + "Introduction" | Opening overview |
| `structural` | TOC, index, title pages | Filtered out automatically |

## Evaluation Harness

Measure retrieval quality across different configurations. Legacy query files
using `expected_keywords` remain supported. For reproducible information
retrieval metrics, use explicit finite judgments keyed by stable chunk or
source IDs stored in search results:

```json
{"query_id":"pj-1","query":"minimum contacts test","judgments":[{"chunk_id":"chunk_0123456789abcdef","relevance":3},{"chunk_id":"chunk_fedcba9876543210","relevance":1}],"expected_type":"case_opinion"}
```

Each judgment must contain exactly one `chunk_id` or `source_id` and a finite,
non-negative `relevance` value. Use one ID type consistently within a query.
Zero means not relevant; larger values express stronger relevance. A judged
query must contain at least one positive judgment.
`stable_id` and `source_file` returned by existing indexes are accepted as
compatibility aliases when matching results.

```bash
# Single config with custom cutoffs and a detailed JSON report
python eval.py \
  --queries my_judged_queries.jsonl \
  --chunks output/Civil_procedure/Civil_procedure_chunks.jsonl \
  --db output/Civil_procedure/Civil_procedure_chroma \
  --collection civil_procedure \
  --k 1 5 10 \
  --depth 100 \
  --json-report output/eval/current.json

# Compare 4 configs side-by-side
python eval.py --compare \
  --chunks output/Civil_procedure/Civil_procedure_chunks.jsonl \
  --db output/Civil_procedure/Civil_procedure_chroma \
  --collection civil_procedure
```

All query schemas report Success@k, MRR, and optional top-result type accuracy.
Queries with explicit judgments additionally report Recall@k, nDCG@k, and MAP;
those judged metrics are averaged only across judged queries. Detailed JSON
reports contain a schema version, retrieval configuration, aggregate metrics,
and per-query ranked-result identities and relevance matches.

`--depth` controls the fixed retrieval depth used for MRR and MAP and must be at
least the largest `--k`. The included `eval_queries_judged.jsonl` contains 24
manually reviewed Civil Procedure queries pinned to the declared 728-record
chunks snapshot. Before scoring a pinned set, the evaluator verifies the chunks
SHA-256 and record count, requires a compatible collection-scoped manifest,
checks the manifest's exact stable-ID set and source fingerprint, and confirms
the physical vector count. A partial or stale index fails before metrics are
produced.

The current depth-20 calibration on that exact snapshot is:

| Configuration | MRR | Recall@10 | nDCG@10 | MAP |
|---------------|----:|----------:|--------:|----:|
| Vector only | 0.736 | 0.729 | 0.639 | 0.581 |
| Vector + BGE reranker | 0.773 | 0.833 | 0.711 | 0.593 |
| Calibrated hybrid | **0.822** | **0.882** | **0.768** | **0.670** |
| Calibrated hybrid + BGE | 0.751 | 0.875 | 0.706 | 0.595 |

These figures justify the Chroma defaults (`dense=0.5`, `lexical=1.0`,
`rrf-k=10`) and adaptive reranking policy for this corpus; use a separate judged
set before treating them as universal. The machine-readable run is stored at
`output/Civil Procedure I/Civil Procedure I_retrieval_eval.json`.

Use repeatable thresholds to make a single-configuration evaluation fail with
exit code 2 when quality is below a required floor:

```bash
python eval.py \
  --queries my_judged_queries.jsonl \
  --chunks output/Civil_procedure/Civil_procedure_chunks.jsonl \
  --db output/Civil_procedure/Civil_procedure_chroma \
  --collection civil_procedure \
  --json-report output/eval/current.json \
  --fail-under recall@10=0.80 \
  --fail-under ndcg@10=0.70
```

Compare a run against a previous JSON report with repeatable regression limits:

```bash
python eval.py \
  --queries my_judged_queries.jsonl \
  --chunks output/Civil_procedure/Civil_procedure_chunks.jsonl \
  --db output/Civil_procedure/Civil_procedure_chroma \
  --collection civil_procedure \
  --json-report output/eval/current.json \
  --baseline-report output/eval/baseline.json \
  --max-regression mrr=0.02 \
  --max-regression ndcg@10=0.03
```

Threshold and regression checks apply to single-configuration runs, not
`--compare`. The repository's `eval_queries.jsonl` remains a ten-query,
keyword-based starter set. `eval_queries_judged.jsonl` is the corpus-pinned
Civil Procedure set; create a separate stable-ID set for any other book.

## Web UI

Gradio web interface with three tabs: Search, Export, and Info.

```bash
pip install -r requirements-optional.txt
python ui.py \
  --chunks output/Civil_procedure/Civil_procedure_chunks.jsonl \
  --db output/Civil_procedure/Civil_procedure_chroma \
  --collection civil_procedure             # http://localhost:7860

# Qdrant run
python ui.py --db-backend qdrant \
  --chunks output/Civil_procedure/Civil_procedure_chunks.jsonl \
  --db output/Civil_procedure/Civil_procedure_qdrant \
  --collection civil_procedure
```

Add `--share` to either complete command to create a public Gradio link.
Search retrieval and Info's exact vector count execute in killable workers. Use
`--search-timeout SECONDS`, `--info-timeout SECONDS`, and
`--db-lock-timeout SECONDS` to tune their hard deadlines and local lease wait
independently.

**Search tab**: query box, content type/chapter filters, three-state retrieval
and reranker controls (Auto/forced/disabled), formatted results with metadata.

**Export tab**: single-file or split-chapter export with content type filters;
returns one file download or a ZIP archive for split chapters.

**Info tab**: pipeline status, chunk statistics, vector DB info.

## Content Processing

### Preprocessing

- **Text-layer safety check**: `convert`, `full`, and `batch` inspect page text
  coverage and replacement-character quality before deciding whether OCR is
  needed. OCR is enabled automatically for incomplete or low-quality text
  layers.
- **Safe background scan stripping**: Paper Capture PDFs may contain a
  full-page raster behind a usable text layer. Only images >1000px that cover at
  least 70% of a page are candidates, and only pages with reliable text are
  stripped. Mixed scan-only pages retain their pixels. Whenever OCR is enabled,
  preprocessing is skipped so OCR keeps the original source images.
- **Overrides**: `--ocr` forces OCR and `--no-ocr` disables it. Disabling OCR on
  a weak text layer emits a warning. `--no-preprocess` skips background-image
  stripping but does not disable automatic OCR selection.

```bash
# Default: inspect text quality and choose OCR automatically
python rag.py convert --pdf book.pdf

# Explicit overrides
python rag.py convert --pdf scanned_book.pdf --ocr
python rag.py convert --pdf born_digital_book.pdf --no-ocr
```

### Text Cleaning

Applied to all output (markdown export and chunks):

- Non-breaking spaces, smart quotes, em/en dashes normalized
- Ligatures expanded (fi, fl, ff, ffi, ffl)
- Replacement characters removed
- Watermark stripped (configurable regex via `--watermark`, or `""` to disable)
- Whitespace collapsed, paragraph breaks preserved
- UTF-8 output with latin-1 fallback for legacy files

### Deduplication

Trigram Jaccard similarity with a 20% length pre-filter. Default threshold 0.95
(configurable via `--dedup-threshold`). Also removes exact-match lines within a
5-line sliding window.

### Chapter Detection

Extracts chapter boundaries from DoclingDocument page headers using 4 regex
patterns covering multiple textbook formats:

- `Chapter 4  Limits on Personal Jurisdiction`
- `3 . PERSONAL JURISDICTION` (number + separator)
- `CHAPTER FIVE: The Federal Courts` (word numbers one-twenty)
- `Part III - Due Process` (Roman numerals i-xx)

Fallback: scans section headers if page headers are empty.

## GPU Setup

### RTX 5060 / Blackwell

This CUDA profile requires CPython 3.12-3.14 even though the portable CPU
profile supports CPython 3.10-3.14. The committed reproducibility lockfiles are
CPU-only; install the CUDA wheel first and then use the bounded direct
requirements for a GPU environment.

| Component | Minimum | Why |
|-----------|---------|-----|
| NVIDIA driver | 570+ | Blackwell hardware support |
| CUDA | 12.8+ | sm_120 kernel compilation |
| PyTorch | 2.7+ | First stable with sm_120 bins |
| PyTorch index | `cu128` | Must use cu128 wheels |

```bash
pip install "torch>=2.7,<3" --index-url https://download.pytorch.org/whl/cu128
python -c "import torch; print(torch.cuda.is_available(), torch.version.cuda)"
```

VRAM-aware batch sizing is automatic:

| VRAM | Batch size | GPUs |
|------|-----------|------|
| 8 GB | 8 | RTX 5060, RTX 4060 |
| 12 GB | 16 | RTX 5070, RTX 4070 |
| 16 GB | 32 | RTX 5060 Ti 16GB, RTX 4080 |
| 24 GB | 64 | RTX 4090 |
| >24 GB | 128 | RTX 5090, A100 |

Override with `--batch-size`. Falls back to CPU gracefully if no GPU.

## Troubleshooting

### `std::bad_alloc` during conversion

PDF has full-page background scans. The `convert` command auto-detects and
strips them only when a reliable text layer makes removal safe. If it does not
trigger, inspect the PDF first; image-only scans should use OCR rather than
manual image removal:

```bash
python rag.py preprocess --pdf book.pdf --analyze
python rag.py full --pdf book.pdf --ocr --force
```

### GPU OOM during conversion

Reduce batch size:
```bash
python rag.py convert --pdf book.pdf --batch-size 4
python rag.py convert --pdf book.pdf --batch-size 2
```

### Rate limiting (429 errors)

The adaptive throttle handles this automatically. To start more conservatively:
```bash
python rag.py full --pdf book.pdf --llm-classify --llm-workers 4
```

### Missing API key

API-based models validate keys at startup. Set the appropriate env var:
```bash
set VOYAGE_API_KEY=voy-...
set OPENAI_API_KEY=sk-...
set COHERE_API_KEY=...
set GEMINI_API_KEY=...
set MINIMAX_API_KEY=...
set DEEPSEEK_API_KEY=...
set CLOUD_API_KEY=...
```

### Encoding artifacts

The pipeline normalizes encoding automatically. Run the pipeline again to
produce a cleaned, newly numbered book directory; `--force` forces conversion
but does not overwrite or reuse the previous run directory.

### Vector store is busy

Another local process is indexing, querying, evaluating, or inspecting the same
database directory. Let it finish or raise `--db-lock-timeout`; use `0` only
when fail-fast behavior is preferable. A `.rag-locks` sidecar left after a
crash is harmless and should not be deleted—the operating-system lock, not the
file's existence, determines ownership. Always access a live database through
one stable path spelling; do not use drive/UNC/SUBST aliases or rename the
directory while another process may have it open. The timeout applies to lock
contention, not cloud-drive hydration or a storage call that has already begun.

### Operation exceeded its deadline

The supervised Windows Job Object or POSIX worker process group was terminated
with exit status `124`. For an interrupted index/full/batch run, keep the
recovery and artifact-completion metadata in place and rerun the same command
(or `full --resume`); incomplete stages regenerate and the indexer rebuilds the
affected collection before declaring it clean. Increase
`--operation-timeout` for an expected long model or storage operation. Do not
delete `.rag-locks` sidecars or `.updating.json` recovery markers manually.

### Stale index after re-chunking

The incremental indexer uses collection-scoped content hashes and validates the
manifest's schema, embedding model, and vector dimension. Changed chunks are
re-embedded automatically; incompatible index state safely rebuilds only the
requested collection. Force an unconditional rebuild with `--full-reindex` if
needed.

## Security Notes

- Embedding and reranker models are loaded with `trust_remote_code=True` (required
  by some HuggingFace models). Only use trusted model names from verified publishers.
- API keys can come from environment variables or the interactive menu's hidden
  prompt; menu-entered keys are redacted from the displayed command, removed
  from child process arguments, scoped to the child environment, and not
  written to a configuration file.
- Direct CLI key flags (`--api-key`, `--cloud-key`, and `--gemini-key`) are
  supported, but their values can be visible in process listings and shell
  history. Prefer environment variables or the interactive menu.
- All file paths are user-specified; no path validation/sandboxing is applied.

## File Structure

```
rag.py                  # Stable command/API facade and pipeline orchestration
retrieval_core.py       # Stdlib-only retrieval models and pure algorithms
artifact_io.py          # Stdlib-only strict reads and atomic publication
chunking_core.py        # Stdlib-only text preparation and classification
index_state.py          # Stdlib-only index manifests and compatibility policy
llm_adapters.py         # Typed LLM provider transport adapters
cli_policy.py           # Stdlib-only CLI interpretation and serialization policy
eval.py                 # Evaluation harness (success@k, MRR, type accuracy)
eval_queries.jsonl      # Starter evaluation queries (10 CivPro)
ui.py                   # Gradio web UI (Search, Export, Info tabs)
scaffold_to_markdown.py # Apply an existing TOC scaffold to PDF text
requirements.txt        # Direct core dependencies
requirements-optional.txt # Direct optional dependencies
requirements-all.txt    # Aggregate core-plus-optional input
requirements-audit.txt  # Normalized CPU versions for advisory lookup
requirements-test.txt   # Exact CI/test tool pins
requirements-smoke.txt  # Lightweight real-vector-store test input
requirements-security.txt # Exact audit/reporting tool pins
requirements-lock-tools.txt # Exact lockfile-generator pin
requirements-*.lock     # Universal exact CPU locks with SHA-256 hashes
dependency-license-policy.json # Denied licenses and reviewed exceptions
dependency-vulnerability-policy.json # Expiring advisory exceptions and audit skips
tools/                  # Lock refresh and dependency/license policy checks
.github/workflows/      # CI, dependency compatibility, and security automation
output/                 # Per-run book directories (auto-created)
```

## Dependencies

### Required (`requirements.txt`)

```
PyMuPDF>=1.24,<2                # PDF preprocessing and scaffold conversion
docling>=2.31,<3                # PDF layout detection and conversion
docling-core[chunking]>=2.70,<3 # HybridChunker and chunking extras
pypdfium2>=4.30,<6              # PDF page counting and conversion backend
sentence-transformers>=3.0,<6   # Local embedding models
chromadb>=1.5.2,<2              # Default vector database; deterministic close()
FlagEmbedding>=1.3,<2           # BGE cross-encoder reranker
rank-bm25>=0.2,<0.3             # BM25 keyword search
tqdm>=4.66,<5                   # Progress bars
requests>=2.31,<3               # Cloud embedding and LLM HTTP calls
numpy>=1.26,<3                  # RAPTOR clustering
```

### Optional (`requirements-optional.txt`)

```
qdrant-client>=1.17,<2       # Qdrant vector DB backend
voyageai>=0.2,<1             # Voyage AI embeddings
openai>=1.0,<3               # OpenAI embeddings
cohere>=5.0,<6               # Cohere embeddings and reranking
google-genai>=1.68,<2        # Gemini fallback + timeout/retry controls
gradio>=6.0,<7               # Web UI
```

These are the project's direct declarations; transitive packages are omitted.
Lower bounds preserve the established feature floor; upper bounds cap the
admitted compatibility range. Dependabot proposes bounded updates weekly.

For reproducible CPU installs, use the committed universal lockfiles. They pin
the complete transitive graph, include SHA-256 artifact hashes, and carry Python
and platform markers for the supported CPython 3.10-3.14 range:

```bash
# In an activated virtual environment
pip install --require-hashes -r requirements-lock-tools.lock
uv pip install --torch-backend cpu --require-hashes \
  -r requirements-full.lock
```

Use `requirements-core.lock` instead for the core-only runtime. CUDA users
should follow the GPU setup above; the CPU locks deliberately cannot reproduce
a CUDA environment.

### Development and supply-chain checks

```bash
pip install --require-hashes -r requirements-test.lock
python tools/check_dependency_policy.py
python -m ruff check .
python -m pytest -q
```

To reproduce the full CPU development environment, install both exact locks:

```bash
pip install --require-hashes -r requirements-lock-tools.lock
uv pip install --torch-backend cpu --require-hashes \
  -r requirements-full.lock -r requirements-test.lock
```

Regenerate locks without changing compatible versions with
`python tools/refresh_locks.py`. Use `python tools/refresh_locks.py --upgrade`
for an intentional dependency refresh, then review and test the lockfile diff.
Dependabot can propose direct-input changes but cannot regenerate these custom
universal locks; refresh and commit the locks on each Dependabot dependency PR.

GitHub Actions runs that dependency-light suite across Python 3.10-3.14 and on
Windows, exercises real local Chroma and Qdrant clients on Linux and Windows,
installs the full locked CPU environment for every source PR, and separately
checks both runtime dependency sets. A scheduled
workflow audits the active Linux/Python 3.12 full development environment with
`pip-audit`, retains its JSON findings, a CycloneDX SBOM, and a dependency-
license inventory, and enforces both dependency policy files. Platform- and
Python-specific inactive branches in the universal locks are resolution-tested
but are not represented in that single-environment SBOM.
Downloaded Hugging Face/Docling model artifacts and any model-provided remote
code are also outside the Python-package SBOM; treat their revision, checksum,
and license pinning as a separate model-supply-chain requirement.

As of 2026-07-21, every published ChromaDB 1.x release is affected by
`PYSEC-2026-311`/`CVE-2026-45829`, a critical pre-authentication code-injection
issue in Chroma's HTTP server, and no patched release exists. This pipeline uses
only the embedded, filesystem-local `chromadb.PersistentClient`; it does not
launch that HTTP server or accept remote collection model configuration. A
documented exception in `dependency-vulnerability-policy.json` expires on
2026-08-31 and makes that deployment constraint explicit. Do not expose a
Chroma server from this environment; use Qdrant for networked deployments and
remove the exception as soon as a fixed Chroma release is available.

PyMuPDF is dual-licensed under AGPL-3.0 or a commercial Artifex license. Its
time-bounded policy exception permits only private, filesystem-local evaluation
through 2026-08-31; no commercial basis has been recorded. This repository also
has no repository-wide `LICENSE` file. Distribution or hosted/network use is a
release blocker until the owner selects and records the applicable PyMuPDF and
repository licensing basis.

### PyTorch (install first)

```bash
pip install "torch>=2.7,<3" --index-url https://download.pytorch.org/whl/cu128
```

## Full Pipeline Example

```bash
# Maximum intelligence: all LLM features enabled
python rag.py full --pdf CivPro_Casebook.pdf \
  --llm-classify \
  --contextualize \
  --reconstruct-headings \
  --quality-score \
  --llm-scaffold \
  --raptor \
  --db-backend qdrant \
  --embedding-model voyage-law-2 \
  --llm-workers 10

# Then query with answer generation
python rag.py query "minimum contacts test" --answer --hybrid \
  --db-backend qdrant \
  --db output/CivPro_Casebook/CivPro_Casebook_qdrant \
  --chunks output/CivPro_Casebook/CivPro_Casebook_chunks.jsonl \
  --collection civpro_casebook \
  --embedding-model voyage-law-2

# Generate study materials
python rag.py brief \
  --chunks output/CivPro_Casebook/CivPro_Casebook_chunks.jsonl \
  -o output/CivPro_Casebook/CivPro_Casebook_briefs.jsonl
python rag.py generate-questions \
  --chunks output/CivPro_Casebook/CivPro_Casebook_chunks.jsonl \
  -o output/CivPro_Casebook/CivPro_Casebook_exam_questions.jsonl
python rag.py export --format flashcards \
  --chunks output/CivPro_Casebook/CivPro_Casebook_chunks.jsonl \
  -o output/CivPro_Casebook/CivPro_Casebook_flashcards.tsv
```
