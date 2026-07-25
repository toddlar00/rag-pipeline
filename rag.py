#!/usr/bin/env python3
"""
rag.py — Docling-based RAG pipeline for legal textbooks.

Usage:
    python rag.py preprocess --pdf <path>                  # Strip background scan images
    python rag.py convert    --pdf <path>                  # PDF to DoclingDocument
    python rag.py chunk --doc output/Book/Book.json --out output/Book/Book_chunks.jsonl
    python rag.py index --chunks output/Book/Book_chunks.jsonl --db output/Book/Book_chroma
    python rag.py export --chunks output/Book/Book_chunks.jsonl -o output/Book/Book.md
    python rag.py query "personal jurisdiction" --db output/Book/Book_chroma
    python rag.py brief --chunks output/Book/Book_chunks.jsonl  # Generate case briefs
    python rag.py full --pdf <path>                        # All steps end-to-end
    python rag.py info                                     # Inspect output artifacts
"""

import argparse
from contextlib import ExitStack, contextmanager
import errno
import gc
from getpass import getpass
import hashlib
import json
import logging
import math
import os
import queue
import re
import requests
import shutil
import signal  # noqa: F401 - shared module retained for facade monkeypatching
import stat
import subprocess  # noqa: F401 - shared module retained for facade monkeypatching
import sys
import threading as _threading
import time
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, Callable, Optional, TypedDict
from uuid import UUID, uuid4

import artifact_io as _artifact_io
import chunking_core as _chunking_core
import cli_policy as _cli_policy
import document_profiles as _document_profiles
import endpoint_policy as _endpoint_policy
import ingestion_core as _ingestion_core
import index_state as _index_state
import job_runtime as _job_runtime
import llm_adapters as _llm_adapters
import model_artifacts as _model_artifacts
import operation_contracts as _operation_contracts
import operational_metrics as _operational_metrics
import process_supervision as _process_supervision
import provider_transport as _provider_transport
import quality_core as _quality_core
import release_security as _release_security
import retention as _retention
import retrieval_core as _retrieval_core
import run_telemetry as _run_telemetry
import runtime_supervision as _runtime_supervision
import storage_policy as _storage_policy
import table_retrieval_core as _table_retrieval_core
import vector_lifecycle as _vector_lifecycle

from llm_runtime import (
    LLMBudgetExceeded,
    LLMExecutionError,
    LLMRequest,
    LLMResult,
    LLMRuntime,
    LLMRuntimeConfig,
    ProviderCallError,
    ProviderResponse,
    ProviderSpec,
)

# Runtime ML/database libraries are never allowed to emit auxiliary analytics
# or version-check traffic from this private-data process. Provider calls are
# governed separately by the explicit release-security policy.
os.environ["HF_HUB_DISABLE_TELEMETRY"] = "1"
os.environ["ANONYMIZED_TELEMETRY"] = "False"
os.environ["DO_NOT_TRACK"] = "1"

# ---------------------------------------------------------------------------
# Logging — configured in main() based on -v / --quiet flags
# ---------------------------------------------------------------------------

log = logging.getLogger(__name__)

# ---------------------------------------------------------------------------
# Defaults
# ---------------------------------------------------------------------------

DEFAULT_EMBEDDING_MODEL_LEGAL = "voyage-law-2"
DEFAULT_EMBEDDING_MODEL_GENERAL = "nomic-ai/nomic-embed-text-v2-moe"
DEFAULT_EMBEDDING_MODEL = DEFAULT_EMBEDDING_MODEL_GENERAL  # free, local GPU, no API key needed
# The default embedding model accepts 512 tokens including its retrieval-task
# prefix and special tokens.  Five hundred leaves a small safety margin while
# preserving ordinary sentence-boundary repair.
DEFAULT_MAX_TOKENS = 500
DEFAULT_COLLECTION = "civpro"
DEFAULT_DB_LOCK_TIMEOUT = 30.0
DEFAULT_OPERATION_TIMEOUTS = dict(
    _runtime_supervision.DEFAULT_OPERATION_TIMEOUTS)
ARTIFACT_COMPLETION_SCHEMA_VERSION = 1
CONVERSION_COMPLETION_SCHEMA_VERSION = 2
CHUNK_COMPLETION_SCHEMA_VERSION = 3
_CONVERSION_CAPTURE_POLICY = "stream-copy-v1"
_MAX_CONVERSION_MANIFEST_BYTES = 1024 * 1024
_MAX_CHUNK_COMPLETION_BYTES = 1024 * 1024
_SNAPSHOT_SCRATCH_ENV = "RAG_SNAPSHOT_SCRATCH"
_SUPERVISED_CHILD_ENV = _runtime_supervision.SUPERVISED_CHILD_ENV
_RUN_ID_ENV = _runtime_supervision.RUN_ID_ENV
_SUPERVISED_TERMINATE_GRACE = (
    _runtime_supervision.SUPERVISED_TERMINATE_GRACE)
_SUPERVISED_POLL_INTERVAL = _runtime_supervision.SUPERVISED_POLL_INTERVAL
_SUPERVISED_START_GATE_TIMEOUT = (
    _runtime_supervision.SUPERVISED_START_GATE_TIMEOUT)


def _structure_profile_parameters_binding(
        parameters_sha256: str, receipt: object,
) -> str:
    """Bind a public profile receipt to a parameter digest without secrets."""
    if (not isinstance(parameters_sha256, str)
            or re.fullmatch(r"[0-9a-f]{64}", parameters_sha256) is None):
        raise ValueError("invalid chunk parameter digest")
    profile = _document_profiles.profile_from_provenance(receipt)
    return _artifact_io._artifact_parameters_sha256({
        "parameters_sha256": parameters_sha256,
        "structure_profile": _document_profiles.profile_provenance(profile),
    })

# Embedding model max token limits (for validation)
EMBEDDING_MAX_TOKENS = {
    "voyage-law-2": 16000,
    "voyage-3-large": 32000,
    "voyage-4-large": 32000,
    "voyage-4": 32000,
    "voyage-4-lite": 32000,
    "dunzhang/stella_en_400M_v5": 8192,
    "nomic-ai/nomic-embed-text-v2-moe": 512,
    "text-embedding-3-large": 8191,
    "nlpaueb/legal-bert-base-uncased": 512,
}
_API_EMBEDDING_BATCH_TOKEN_BUDGET = 100_000
_SUPPORTED_API_EMBEDDING_MODEL_PREFIXES = (
    "voyage-", "text-embedding-", "embed-", "cohere-",
)
_UNSUPPORTED_API_EMBEDDING_MODEL_PREFIXES = ("embo-", "minimax-emb")
_API_EMBEDDING_MODEL_PREFIXES = (
    *_SUPPORTED_API_EMBEDDING_MODEL_PREFIXES,
    *_UNSUPPORTED_API_EMBEDDING_MODEL_PREFIXES,
)
_VOYAGE_EMBEDDINGS_URL = "https://api.voyageai.com/v1/embeddings"
_COHERE_EMBEDDINGS_URL = "https://api.cohere.com/v1/embed"
_OPENAI_EMBEDDINGS_URL = "https://api.openai.com/v1/embeddings"
_COHERE_RERANK_URL = "https://api.cohere.com/v2/rerank"
_JINA_RERANK_URL = "https://api.jina.ai/v1/rerank"
_GEMINI_API_BASE_URL = "https://generativelanguage.googleapis.com"
_ALLOW_UNPINNED_MODELS_ENV = "RAG_ALLOW_UNPINNED_MODELS"
# Backward-compatible defaults for standalone commands. ``full`` and
# ``batch`` derive collision-free, book-scoped paths for every run.
DEFAULT_DOC_PATH = Path("output/docling_doc.json")
DEFAULT_CHUNKS_PATH = Path("output/chunks.jsonl")
DEFAULT_CHROMA_DIR = Path("output/chroma_db")
DEFAULT_QDRANT_DIR = Path("output/qdrant_db")
DEFAULT_DB_BACKEND = "chroma"  # "chroma" or "qdrant"
DEFAULT_STRUCTURE_PROFILE = _document_profiles.DEFAULT_STRUCTURE_PROFILE
DEFAULT_EXPORT_PATH = Path("output/textbook.md")
OUTPUT_DIR = Path(os.environ.get(
    _job_runtime.OUTPUT_ROOT_ENV, "output"))


def _model_artifact_lock_sha256() -> str:
    """Return the validated model lock identity used by derived artifacts."""
    return _model_artifacts.model_artifact_lock_sha256()


def _model_download_transport(
        policy: _release_security.ReleaseSecurityPolicy) -> dict[str, object]:
    """Bind Hub endpoint and ambient transport trust to one policy."""
    endpoint = _model_artifacts.HUGGINGFACE_HUB_OFFICIAL_ENDPOINT
    if policy.trust_environment_network:
        endpoint = os.environ.get("HF_ENDPOINT", endpoint) or endpoint
    return {
        "download_endpoint": endpoint,
        "trust_environment_network": policy.trust_environment_network,
    }


def _model_loader_source(
        model_id: str, consumer: str, *,
        execute_remote_code: bool = False,
        security_policy: (
            _release_security.ReleaseSecurityPolicy | None) = None,
        ) -> tuple[str, bool]:
    """Resolve a reviewed model to a verified local tree.

    Unknown models fail closed unless the operator selects the development
    profile, reviewed-sync policy, and ``RAG_ALLOW_UNPINNED_MODELS=1``
    together.
    """
    policy = _effective_security_policy(security_policy)
    artifact = _model_artifacts.model_artifact(model_id)
    if artifact is not None:
        if execute_remote_code:
            if not artifact.trust_remote_code:
                raise _model_artifacts.ModelArtifactError(
                    f"remote code is not approved for {model_id}")
            _model_artifacts.configure_transformers_dynamic_module_cache()
        allow_download = policy.model_download_policy == "allow-reviewed-sync"
        return str(_model_artifacts.verified_model_directory(
            model_id, consumer, allow_download=allow_download,
            authorize_download_fn=(
                lambda: _release_security.require_model_download(
                    policy,
                    feature=f"model synchronization for {model_id}")
            ) if allow_download else None,
            **(_model_download_transport(policy) if allow_download else {}),
        )), True
    if (
        policy.profile == "development"
        and policy.model_download_policy == "allow-reviewed-sync"
        and os.environ.get(_ALLOW_UNPINNED_MODELS_ENV) == "1"
    ):
        _release_security.require_model_download(
            policy, feature=f"unpinned model loading for {model_id}")
        if execute_remote_code:
            _model_artifacts.configure_transformers_dynamic_module_cache()
        log.warning(
            "Loading unpinned model %s because %s=1; artifact provenance and "
            "runtime byte verification are disabled.",
            model_id,
            _ALLOW_UNPINNED_MODELS_ENV,
        )
        return model_id, False
    raise _model_artifacts.ModelArtifactError(
        f"model is not in the reviewed artifact lock: {model_id}; unpinned "
        "loading requires the development profile, explicit reviewed-sync "
        f"policy, and {_ALLOW_UNPINNED_MODELS_ENV}=1")


class PipelinePaths(TypedDict):
    """Filesystem artifacts produced by one named pipeline run."""

    doc: Path
    converted_markdown: Path
    chunks: Path
    quality_report: Path
    export: Path
    chapters_dir: Path
    chroma: Path
    qdrant: Path
    preprocessed: Path
    collection: str


class VectorStoreBusyError(TimeoutError):
    """Raised when another operation retains the local vector-store lease."""


SearchHit = _retrieval_core.SearchHit
ContextSourceAlias = _retrieval_core.ContextSourceAlias
ContextSegment = _retrieval_core.ContextSegment
GroundedSource = _retrieval_core.GroundedSource
GroundedAnswer = _retrieval_core.GroundedAnswer
SearchResponse = _retrieval_core.SearchResponse

# Minimum word count for a chunk to be kept (filters frontmatter scraps)
MIN_CHUNK_WORDS = _chunking_core.MIN_CHUNK_WORDS
# Similarity threshold for deduplication (1.0 = identical)
DEDUP_THRESHOLD = _chunking_core.DEDUP_THRESHOLD

# LLM classification / contextual retrieval defaults
DEFAULT_OLLAMA_URL = "http://127.0.0.1:11434"
DEFAULT_OLLAMA_MODEL = "qwen3:30b"
DEFAULT_GEMINI_MODEL = "gemini-3.6-flash"
DEFAULT_CLOUD_URL = "https://api.minimax.io/v1"
DEFAULT_CLOUD_MODEL = "MiniMax-M3"
DEFAULT_DEEPSEEK_URL = "https://api.deepseek.com"
DEFAULT_DEEPSEEK_MODEL = "deepseek-v4-pro"
DEFAULT_LLM_WORKERS = 10
DEFAULT_RERANKER_MODEL = "BAAI/bge-reranker-v2-m3"
DEFAULT_ZEROSHOT_MODEL = "facebook/bart-large-mnli"
RERANK_OVERFETCH = 4  # retrieve N*4 from ChromaDB, rerank to N
DEFAULT_RRF_K = _retrieval_core.DEFAULT_RRF_K
DEFAULT_DENSE_RRF_WEIGHT = 0.5
DEFAULT_SPARSE_RRF_WEIGHT = 1.0
MAX_CONTEXT_WINDOW = _retrieval_core.MAX_CONTEXT_WINDOW
MAX_CONTEXT_CHARACTERS = _retrieval_core.MAX_CONTEXT_CHARACTERS
MAX_CONTEXT_SEGMENT_CHARACTERS = (
    _retrieval_core.MAX_CONTEXT_SEGMENT_CHARACTERS)
DEFAULT_CONTEXT_MAX_CHARACTERS = (
    _retrieval_core.DEFAULT_CONTEXT_MAX_CHARACTERS)
DEFAULT_CONTEXT_SEGMENT_CHARACTERS = (
    _retrieval_core.DEFAULT_CONTEXT_SEGMENT_CHARACTERS)

# Direct Python callers retain the historical uncached behavior. ``main``
# enables the persistent cache for CLI LLM workflows unless explicitly
# disabled by the user.
_llm_runtime = LLMRuntime()

_MIN_USABLE_PAGE_CHARS = 40
_MIN_USABLE_TEXT_PAGE_RATIO = 0.60
_MIN_USABLE_SCAN_TEXT_RATIO = 1.00
_MAX_REPLACEMENT_CHAR_RATIO = 0.02
_MIN_BACKGROUND_IMAGE_PAGE_COVERAGE = 0.70
_CONTEXT_TOKEN_RESERVE = 192

# Incremental vector-index metadata. Bump this whenever the indexed payload or
# vector layout changes in a way that requires rebuilding existing collections.
INDEX_MANIFEST_SCHEMA_VERSION = 8
_LEGACY_QUERY_SCHEMA_BINDINGS = ((6, 2), (7, 3))
_CONTEXT_QUERY_SCHEMA_BINDINGS = ((7, 3),)

# Content type labels for LLM classification prompt
_CONTENT_LABELS = [
    "case_opinion", "notes_and_questions", "author_narrative",
    "statutory_excerpt", "table", "chapter_introduction", "footnote",
]


_provider_hostname = _llm_adapters._provider_hostname
_validate_cloud_endpoint = _endpoint_policy.validate_cloud_endpoint


def _is_deepseek_cloud(url: str, model: str = "") -> bool:
    """Return whether an API URL is the official DeepSeek endpoint."""
    return _llm_adapters._is_deepseek_cloud(
        url, model, validate_endpoint_fn=_validate_cloud_endpoint)


def _is_minimax_cloud(url: str) -> bool:
    """Return whether an API URL is the official MiniMax endpoint."""
    return _llm_adapters._is_minimax_cloud(
        url, validate_endpoint_fn=_validate_cloud_endpoint)


def _effective_chunk_token_limit(embedding_model: str,
                                 requested_tokens: int, *,
                                 reserve_tokens: int = 0) -> int:
    """Cap chunk size to a known embedding-model input limit."""
    if requested_tokens < 1:
        raise ValueError("max_tokens must be at least 1")
    if reserve_tokens < 0:
        raise ValueError("reserve_tokens cannot be negative")
    model_limit = EMBEDDING_MAX_TOKENS.get(embedding_model)
    if model_limit is None:
        return requested_tokens
    special_token_reserve = (
        0 if embedding_model.startswith(_API_EMBEDDING_MODEL_PREFIXES) else 2
    )
    # Nomic's SentenceTransformer input includes a four-token
    # ``search_document: `` task prefix in addition to BOS/EOS.  Chunker token
    # counts exclude both, so reserve all six tokens before publication.
    task_prefix_reserve = (
        4
        if embedding_model == "nomic-ai/nomic-embed-text-v2-moe"
        else 0
    )
    return min(
        requested_tokens,
        max(
            1,
            model_limit - reserve_tokens - special_token_reserve
            - task_prefix_reserve,
        ),
    )


def _embedding_text(record: dict) -> str:
    metadata = record.get("metadata", {})
    context = metadata.get("context", "")
    text = record.get("text", "")
    if context:
        return f"{context}\n\n{text}"
    if len(text.split()) < MIN_CHUNK_WORDS:
        headings = metadata.get("headings") or []
        if headings:
            return f"{headings[-1]}\n\n{text}"
    return text


def _embedding_task_prefix(embedding_model: str,
                           input_type: str) -> str:
    """Return the exact retrieval-task prefix applied at model inference."""
    if embedding_model.startswith("nomic-ai/nomic-embed-text"):
        return (
            "search_query: " if input_type == "query"
            else "search_document: "
        )
    return ""


def _prepare_embedding_inputs(texts, embedding_model: str,
                              input_type: str) -> list[str]:
    """Apply the same task prefix for token validation and model inference."""
    prefix = _embedding_task_prefix(embedding_model, input_type)
    return [
        text if not prefix or text.startswith(prefix) else prefix + text
        for text in texts
    ]


def _conservative_token_estimate(text: str) -> int:
    """Estimate tokens defensively when a provider tokenizer is unavailable."""
    byte_estimate = (len(text.encode("utf-8")) + 2) // 3
    lexical_estimate = len(re.findall(r"\w+|[^\w\s]", text, re.UNICODE))
    return max(1, byte_estimate, lexical_estimate)


def _count_embedding_text_tokens(texts: list[str],
                                 embedding_model: str) -> tuple[list[int], bool]:
    """Count model input tokens, returning ``(counts, exact_tokenizer)``."""
    texts = _prepare_embedding_inputs(
        texts, embedding_model, "document")
    try:
        # API tokenizers can perform hidden model/blob downloads on a cache
        # miss.  Runtime execution never invokes them: the conservative local
        # estimator preserves the no-network contract and merely reduces the
        # effective provider batch size.
        if not embedding_model.startswith(_API_EMBEDDING_MODEL_PREFIXES):
            from transformers import AutoTokenizer

            model_source, verified = _model_loader_source(
                embedding_model, "token_counter")
            loader_kwargs = {
                "trust_remote_code": not verified,
                **({"local_files_only": True} if verified else {}),
            }
            tokenizer = AutoTokenizer.from_pretrained(
                model_source, **loader_kwargs)
            return [
                len(tokenizer.encode(
                    text, add_special_tokens=True, truncation=False))
                for text in texts
            ], True
    except Exception as exc:
        log.warning(
            "Could not load the exact tokenizer for %s (%s); using a "
            "conservative token estimate.", embedding_model, exc,
        )
    return [_conservative_token_estimate(text) for text in texts], False


def _validate_embedding_token_counts(records: list[dict],
                                     embedding_model: str, *,
                                     recompute: bool = False) -> None:
    """Reject indexed chunks known to exceed an embedding model's limit.

    Legacy JSONL files may not contain ``token_count``; those records remain
    accepted because an exact count cannot be reconstructed without knowing
    which tokenizer originally produced them.
    """
    model_limit = EMBEDDING_MAX_TOKENS.get(embedding_model)
    if model_limit is None:
        if recompute:
            counts, _ = _count_embedding_text_tokens(
                [_embedding_text(record) for record in records],
                embedding_model,
            )
            for record, count in zip(records, counts):
                record["metadata"]["embedding_token_count"] = count
        return
    exact = True
    recomputed_counts = None
    if recompute:
        recomputed_counts, exact = _count_embedding_text_tokens(
            [_embedding_text(record) for record in records], embedding_model)
        for record, count in zip(records, recomputed_counts):
            record["metadata"]["embedding_token_count"] = count
    effective_limit = model_limit if exact else max(1, int(model_limit * 0.9))
    oversized = []
    for index, record in enumerate(records):
        metadata = record.get("metadata", {})
        token_count = (
            recomputed_counts[index] if recomputed_counts is not None
            else metadata.get(
                "embedding_token_count", metadata.get("token_count"))
        )
        if isinstance(token_count, int) and token_count > effective_limit:
            oversized.append(token_count)
    if oversized:
        limit_note = (
            f"the {model_limit}-token input limit"
            if exact else
            f"a conservative {effective_limit}-token safety limit for the "
            f"{model_limit}-token model"
        )
        raise ValueError(
            f"{len(oversized)} chunks exceed {limit_note} for "
            f"'{embedding_model}' (largest: {max(oversized)}). "
            "Re-run chunking with a compatible --max-tokens value."
        )


def _batch_index_records(records: list[dict], embedding_model: str, *,
                         max_records: int) -> list[list[dict]]:
    """Batch records by both record count and provider request token budget."""
    if not embedding_model.startswith(_API_EMBEDDING_MODEL_PREFIXES):
        return [records[start:start + max_records]
                for start in range(0, len(records), max_records)]

    batches = []
    batch = []
    batch_tokens = 0
    for record in records:
        stored_count = record.get("metadata", {}).get(
            "embedding_token_count")
        token_count = (
            stored_count if isinstance(stored_count, int) and stored_count > 0
            else _conservative_token_estimate(_embedding_text(record))
        )
        if token_count > _API_EMBEDDING_BATCH_TOKEN_BUDGET:
            raise ValueError(
                f"One embedding input is approximately {token_count} tokens, "
                f"above the {_API_EMBEDDING_BATCH_TOKEN_BUDGET}-token safe "
                "API request budget. Re-run chunking with smaller chunks."
            )
        if batch and (
                len(batch) >= max_records
                or batch_tokens + token_count
                > _API_EMBEDDING_BATCH_TOKEN_BUDGET):
            batches.append(batch)
            batch = []
            batch_tokens = 0
        batch.append(record)
        batch_tokens += token_count
    if batch:
        batches.append(batch)
    return batches


def _pdf_ingestion_thresholds() -> _ingestion_core.PDFIngestionThresholds:
    """Return current facade thresholds for PDF ingestion safety policy."""
    return _ingestion_core.PDFIngestionThresholds(
        min_usable_page_chars=_MIN_USABLE_PAGE_CHARS,
        min_usable_text_page_ratio=_MIN_USABLE_TEXT_PAGE_RATIO,
        min_usable_scan_text_ratio=_MIN_USABLE_SCAN_TEXT_RATIO,
        max_replacement_char_ratio=_MAX_REPLACEMENT_CHAR_RATIO,
        min_background_image_page_coverage=(
            _MIN_BACKGROUND_IMAGE_PAGE_COVERAGE),
    )


def _pdf_page_text_is_usable(text: str) -> bool:
    """Return whether one page has enough clean extracted text to preserve."""
    return _ingestion_core.pdf_page_text_is_usable(
        text, thresholds=_pdf_ingestion_thresholds())


def _pdf_text_layer_is_usable(stats: dict, *,
                              large_image_pages_only: bool = False) -> bool:
    """Assess whether extracted PDF text is safe to preserve without OCR."""
    return _ingestion_core.pdf_text_layer_is_usable(
        stats,
        large_image_pages_only=large_image_pages_only,
        thresholds=_pdf_ingestion_thresholds(),
    )

# Watermark text repeated on every page of the source PDF.
# Override with --watermark or pass "" to disable.
DEFAULT_WATERMARK = (
    r"Copyright\s*©?\s*2024\s+Carolina Academic Press,?\s+LLC\.?"
    r"\s*All rights reserved\.?\s*Do not post or distribute\.?\s*"
)

# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

def _require_file(path: Path, label: str = "Input file") -> None:
    """Exit with a clear message if *path* doesn't exist or is empty."""
    if not path.exists():
        log.error(f"{label} not found: {path}")
        log.error("  Check the path or run the previous pipeline step first.")
        sys.exit(1)
    if path.stat().st_size == 0:
        log.error(f"{label} is empty (0 bytes): {path}")
        sys.exit(1)


def _compile_watermark(pattern: str) -> Optional[re.Pattern]:
    """Compile watermark regex, returning None if pattern is empty."""
    if not pattern:
        return None
    return re.compile(pattern, re.IGNORECASE)


def strip_watermark(text: str, wm: Optional[re.Pattern] = None) -> str:
    """Remove publisher watermark lines from extracted text."""
    if wm is None:
        return text
    return wm.sub("", text).strip()


def _validate_api_key(model_name: str) -> None:
    """Check that required API key is set for API-based models. Call early."""
    if model_name.startswith(_UNSUPPORTED_API_EMBEDDING_MODEL_PREFIXES):
        log.error(
            f"MiniMax embedding model '{model_name}' is unsupported: "
            "there is no current reviewed MiniMax embedding API contract")
        sys.exit(1)
    checks = [
        ("voyage-", "VOYAGE_API_KEY", "https://dash.voyageai.com/"),
        ("cohere-", "COHERE_API_KEY", "https://dashboard.cohere.com/"),
        ("embed-", "COHERE_API_KEY", "https://dashboard.cohere.com/"),
        ("text-embedding-", "OPENAI_API_KEY", "https://platform.openai.com/"),
    ]
    for prefix, env_var, url in checks:
        if model_name.startswith(prefix):
            if not os.environ.get(env_var):
                log.error(f"{env_var} required for model '{model_name}'")
                log.error(f"  Get one at {url}")
                sys.exit(1)
            return


def _validated_embedding_vectors(
        value: object, *, expected_count: int, provider: str,
) -> list[list[float]]:
    """Validate one provider response without retaining arbitrary payloads."""
    if not isinstance(value, list) or len(value) != expected_count:
        raise RuntimeError(f"{provider} returned an invalid embedding count")
    vectors: list[list[float]] = []
    dimension: int | None = None
    for raw_vector in value:
        if not isinstance(raw_vector, list) or not raw_vector:
            raise RuntimeError(f"{provider} returned an invalid embedding")
        vector = []
        for raw_number in raw_vector:
            if isinstance(raw_number, bool) or not isinstance(
                    raw_number, (int, float)):
                raise RuntimeError(
                    f"{provider} returned a nonnumeric embedding")
            number = float(raw_number)
            if not math.isfinite(number):
                raise RuntimeError(
                    f"{provider} returned a non-finite embedding")
            vector.append(number)
        if dimension is None:
            dimension = len(vector)
        elif len(vector) != dimension:
            raise RuntimeError(
                f"{provider} returned inconsistent embedding dimensions")
        vectors.append(vector)
    return vectors


def _data_embedding_vectors(
        payload: object, *, expected_count: int, provider: str,
) -> list[list[float]]:
    if not isinstance(payload, dict):
        raise RuntimeError(f"{provider} returned an invalid response")
    data = payload.get("data")
    if not isinstance(data, list):
        raise RuntimeError(f"{provider} returned an invalid response")
    rows: list[tuple[int, object]] = []
    for position, item in enumerate(data):
        if not isinstance(item, dict):
            raise RuntimeError(f"{provider} returned an invalid response")
        index = item.get("index", position)
        if isinstance(index, bool) or not isinstance(index, int):
            raise RuntimeError(f"{provider} returned an invalid response index")
        rows.append((index, item.get("embedding")))
    if sorted(index for index, _ in rows) != list(range(expected_count)):
        raise RuntimeError(f"{provider} returned invalid embedding indexes")
    rows.sort(key=lambda item: item[0])
    return _validated_embedding_vectors(
        [embedding for _, embedding in rows],
        expected_count=expected_count, provider=provider)


def _get_embedding_fn(
        model_name: str, *, input_type: str = "document",
        security_policy: (
            _release_security.ReleaseSecurityPolicy | None
        ) = None):
    """Return a vector-client-compatible embedding function for *model_name*.

    Automatically routes to the right backend:
      - voyage-*     → Voyage AI API (requires VOYAGE_API_KEY)
      - cohere-*     → Cohere API (requires COHERE_API_KEY)
      - text-embedding-* → OpenAI API (requires OPENAI_API_KEY)
      - anything else → sentence-transformers (local GPU/CPU)

    ``input_type`` is either ``"document"`` or ``"query"``.  Providers with
    asymmetric retrieval embeddings receive the corresponding provider role;
    providers without role support simply ignore it.
    """
    if input_type not in {"document", "query"}:
        raise ValueError("input_type must be 'document' or 'query'")
    policy = _effective_security_policy(security_policy)
    is_api_embedding = model_name.startswith(_API_EMBEDDING_MODEL_PREFIXES)

    def _authorize_api_embedding() -> None:
        if not is_api_embedding:
            return
        _release_security.require_cloud_egress(
            policy, feature="cloud embedding")

    _authorize_api_embedding()

    if model_name.startswith(_UNSUPPORTED_API_EMBEDDING_MODEL_PREFIXES):
        raise ValueError(
            "MiniMax embedding models are unsupported because there is no "
            "current reviewed MiniMax embedding API contract")

    # These adapters are plain callables.  Chroma validates the ``__call__``
    # signature structurally, so inheriting its optional typing protocol only
    # coupled Qdrant/API-only deployments to the Chroma package at runtime.
    Documents = list[str]
    Embeddings = list[list[float]]

    # --- Voyage AI (voyage-law-2, voyage-3, etc.) ---
    if model_name.startswith("voyage-"):
        class _VoyageEmbedFn:
            def __init__(self, name: str, role: str):
                self._name = name
                self._role = role

            def _embed(self, texts: list[str]) -> Embeddings:
                _authorize_api_embedding()
                api_key = os.environ.get("VOYAGE_API_KEY", "")
                if not api_key:
                    raise ValueError(
                        "VOYAGE_API_KEY env var required for Voyage AI embeddings. "
                        "Get one at https://dash.voyageai.com/")
                response = _post_cloud_with_policy(
                    policy, _VOYAGE_EMBEDDINGS_URL,
                    headers={"Accept": "application/json"},
                    auth=_llm_adapters._BearerAuth(api_key),
                    json={"input": texts, "model": self._name,
                          "input_type": self._role},
                    timeout=60, allow_redirects=False, stream=True,
                )
                payload = _read_provider_json_response(
                    response,
                    feature="Voyage embedding",
                    max_bytes=(
                        _provider_transport.EMBEDDING_RESPONSE_MAX_BYTES),
                    deadline_seconds=60,
                )
                return _data_embedding_vectors(
                    payload, expected_count=len(texts),
                    provider="Voyage")

            def __call__(self, input: Documents) -> Embeddings:
                # Voyage API batch limit: 1000 items or 120K tokens.
                # Use 128-item batches for safety with long legal texts.
                BATCH = 128
                if len(input) <= BATCH:
                    return self._embed(list(input))
                all_embs: Embeddings = []
                for i in range(0, len(input), BATCH):
                    all_embs.extend(self._embed(list(input[i:i + BATCH])))
                return all_embs

        return _VoyageEmbedFn(model_name, input_type)

    # --- Cohere (embed-v4.0, embed-english-v3.0, etc.) ---
    if model_name.startswith(("embed-", "cohere-")):
        class _CohereEmbedFn:
            def __init__(self, name: str, role: str):
                self._name = name.removeprefix("cohere-")
                self._role = role

            def _embed(self, texts: list[str]) -> Embeddings:
                _authorize_api_embedding()
                api_key = os.environ.get("COHERE_API_KEY", "")
                if not api_key:
                    raise ValueError(
                        "COHERE_API_KEY env var required for Cohere embeddings.")
                response = _post_cloud_with_policy(
                    policy, _COHERE_EMBEDDINGS_URL,
                    headers={"Accept": "application/json"},
                    auth=_llm_adapters._BearerAuth(api_key),
                    json={
                        "texts": texts,
                        "model": self._name,
                        "input_type": (
                            "search_query" if self._role == "query"
                            else "search_document"),
                    },
                    timeout=60, allow_redirects=False, stream=True,
                )
                payload = _read_provider_json_response(
                    response,
                    feature="Cohere embedding",
                    max_bytes=(
                        _provider_transport.EMBEDDING_RESPONSE_MAX_BYTES),
                    deadline_seconds=60,
                )
                if not isinstance(payload, dict):
                    raise RuntimeError("Cohere returned an invalid response")
                return _validated_embedding_vectors(
                    payload.get("embeddings"), expected_count=len(texts),
                    provider="Cohere")

            def __call__(self, input: Documents) -> Embeddings:
                BATCH = 96
                if len(input) <= BATCH:
                    return self._embed(list(input))
                all_embs: Embeddings = []
                for i in range(0, len(input), BATCH):
                    all_embs.extend(self._embed(list(input[i:i + BATCH])))
                return all_embs

        return _CohereEmbedFn(model_name, input_type)

    # --- OpenAI (text-embedding-3-large, text-embedding-3-small) ---
    if model_name.startswith("text-embedding-"):
        class _OpenAIEmbedFn:
            def __init__(self, name: str):
                self._name = name

            def _embed(self, texts: list[str]) -> Embeddings:
                _authorize_api_embedding()
                api_key = os.environ.get("OPENAI_API_KEY", "")
                if not api_key:
                    raise ValueError(
                        "OPENAI_API_KEY env var required for OpenAI embeddings.")
                response = _post_cloud_with_policy(
                    policy, _OPENAI_EMBEDDINGS_URL,
                    headers={"Accept": "application/json"},
                    auth=_llm_adapters._BearerAuth(api_key),
                    json={"model": self._name, "input": texts},
                    timeout=60, allow_redirects=False, stream=True,
                )
                payload = _read_provider_json_response(
                    response,
                    feature="OpenAI embedding",
                    max_bytes=(
                        _provider_transport.EMBEDDING_RESPONSE_MAX_BYTES),
                    deadline_seconds=60,
                )
                return _data_embedding_vectors(
                    payload, expected_count=len(texts),
                    provider="OpenAI")

            def __call__(self, input: Documents) -> Embeddings:
                # Keep a legitimate 3,072-dimension response comfortably below
                # the decoded 32 MiB provider-response ceiling.
                BATCH = 256
                if len(input) <= BATCH:
                    return self._embed(list(input))
                all_embs: Embeddings = []
                for i in range(0, len(input), BATCH):
                    all_embs.extend(self._embed(list(input[i:i + BATCH])))
                return all_embs

        return _OpenAIEmbedFn(model_name)

    # --- Local sentence-transformers (nomic, legal-bert, etc.) ---
    # WARNING: trust_remote_code=True allows model repos to execute arbitrary
    # Python. Only use with trusted models (HuggingFace verified publishers).
    class _LocalEmbedFn:
        def __init__(self, name: str, role: str):
            self._name = name
            self._role = role
            self._model = None

        def _load(self):
            if self._model is None:
                log.debug(f"Loading embedding model '{self._name}' "
                          f"from verified local artifacts")
                artifact = _model_artifacts.model_artifact(self._name)
                model_source, verified = _model_loader_source(
                    self._name, "embedding",
                    execute_remote_code=(
                        artifact.trust_remote_code
                        if artifact is not None else True),
                    security_policy=policy,
                )
                from sentence_transformers import SentenceTransformer
                loader_kwargs = {
                    "trust_remote_code": (
                        artifact.trust_remote_code
                        if verified and artifact is not None else True),
                }
                if verified:
                    loader_kwargs.update({
                        "local_files_only": True,
                        "model_kwargs": {"use_safetensors": True},
                    })
                self._model = SentenceTransformer(
                    model_source, **loader_kwargs,
                )
                declared_limit = EMBEDDING_MAX_TOKENS.get(self._name)
                runtime_limit = getattr(self._model, "max_seq_length", None)
                if (
                    declared_limit is not None
                    and isinstance(runtime_limit, int)
                    and runtime_limit > 0
                    and declared_limit > runtime_limit
                ):
                    raise RuntimeError(
                        f"Configured input limit for {self._name} is "
                        f"{declared_limit} tokens, but the verified embedding "
                        f"artifact truncates at {runtime_limit}."
                    )
            return self._model

        def __call__(self, input: Documents) -> Embeddings:
            model = self._load()
            # Keep token validation and inference on one exact input contract.
            texts = _prepare_embedding_inputs(
                list(input), self._name, self._role)
            embeddings = model.encode(texts, convert_to_numpy=True)
            return [e.tolist() for e in embeddings]

    return _LocalEmbedFn(model_name, input_type)


def _output_paths_for_name(name: str) -> PipelinePaths:
    """Build the artifact paths for one named pipeline run."""
    book_dir = OUTPUT_DIR / name
    chunks = book_dir / f"{name}_chunks.jsonl"
    return {
        "doc":          book_dir / f"{name}.json",
        "converted_markdown": book_dir / f"{name}_docling.md",
        "chunks":       chunks,
        "quality_report": _quality_core.quality_report_path(chunks),
        "export":       book_dir / f"{name}.md",
        "chapters_dir": book_dir / "Chapters",
        "chroma":       book_dir / f"{name}_chroma",
        "qdrant":       book_dir / f"{name}_qdrant",
        "preprocessed": OUTPUT_DIR / f"{name}_preprocessed.pdf",
        "collection":   name.lower().replace(" ", "_"),
    }


def _output_run_number(name: str, stem: str) -> int | None:
    """Return the run number encoded by *name*, or ``None`` if unrelated."""
    if name == stem:
        return 1
    prefix = f"{stem}_"
    if not name.startswith(prefix):
        return None
    suffix = name[len(prefix):]
    if not suffix.isdecimal():
        return None
    run_number = int(suffix)
    return run_number if run_number >= 2 else None


def _existing_output_runs(stem: str) -> list[tuple[int, str]]:
    """Find existing book-directory and legacy flat-file runs for *stem*."""
    if not OUTPUT_DIR.is_dir():
        return []

    runs: dict[int, str] = {}
    for candidate in OUTPUT_DIR.iterdir():
        if candidate.is_dir():
            name = candidate.name
        elif candidate.is_file() and candidate.suffix.lower() == ".json":
            # Older versions wrote Docling JSON directly under output/.
            name = candidate.stem
        else:
            continue

        run_number = _output_run_number(name, stem)
        if run_number is not None:
            runs[run_number] = name

    return sorted(runs.items())


def _derive_output_paths(pdf_path: Path) -> PipelinePaths:
    """Allocate artifact paths for a new run without reusing prior output.

    Uses the PDF stem as the base name. If a book directory (or a legacy flat
    Docling JSON file) already exists, appends ``_2``, ``_3``, and so on.
    Numbering continues after the highest existing run, even if there are gaps.
    """
    stem = pdf_path.stem
    runs = _existing_output_runs(stem)
    run_number = runs[-1][0] + 1 if runs else 1
    name = stem if run_number == 1 else f"{stem}_{run_number}"
    return _output_paths_for_name(name)


def _derive_output_paths_existing(pdf_path: Path) -> PipelinePaths:
    """Find the *most recent* existing output directory for a PDF.

    Unlike ``_derive_output_paths`` (which always allocates a new counter),
    this walks backwards from the highest counter to find the last directory
    that was actually created.  Used by ``--resume`` to locate partial runs.

    Falls back to ``_derive_output_paths`` (new directory) if nothing exists.
    """
    stem = pdf_path.stem
    runs = _existing_output_runs(stem)
    if not runs:
        # Nothing exists yet — fall through to normal allocation
        return _derive_output_paths(pdf_path)

    # Use the latest run, including when numbering has gaps.
    return _output_paths_for_name(runs[-1][1])


def _derive_output_paths_exact(
        pdf_path: Path, run_name: str) -> PipelinePaths:
    """Resolve one existing run bound to *pdf_path* without latest-run drift."""
    if (
        not isinstance(run_name, str)
        or not run_name
        or Path(run_name).name != run_name
        or _output_run_number(run_name, Path(pdf_path).stem) is None
    ):
        raise ValueError("exact resume run name does not match the PDF stem")
    paths = _output_paths_for_name(run_name)
    run_root = paths["doc"].parent
    if not run_root.is_dir():
        raise FileNotFoundError(
            f"exact resume run does not exist: {run_name}")
    _storage_policy.assert_no_link_components(run_root)
    return paths


def _file_exists_nonempty(p: Path) -> bool:
    """Return True if *p* exists and is non-empty (file) or non-empty dir."""
    if not p.exists():
        return False
    if p.is_file():
        return p.stat().st_size > 0
    if p.is_dir():
        return any(p.iterdir())
    return False


def _json_file_is_valid(path: Path) -> bool:
    """Return whether *path* is a non-empty, parseable JSON document."""
    if not path.is_file() or path.stat().st_size == 0:
        return False
    try:
        json.loads(path.read_text(encoding="utf-8"))
        return True
    except (OSError, UnicodeError, json.JSONDecodeError):
        return False


def _chroma_settings_kwargs(chromadb_module) -> dict[str, object]:
    """Construct an explicit no-telemetry Chroma settings object."""
    settings_factory = getattr(chromadb_module, "Settings", None)
    if settings_factory is None:
        # Lightweight unit fakes intentionally expose only PersistentClient.
        # An installed Chroma package must expose Settings or fail closed.
        if getattr(chromadb_module, "__file__", None) is None:
            return {}
        raise RuntimeError(
            "installed Chroma does not expose telemetry controls")
    settings = settings_factory(anonymized_telemetry=False)
    if getattr(settings, "anonymized_telemetry", None) is not False:
        raise RuntimeError("Chroma telemetry could not be disabled")
    return {"settings": settings}


def _chunk_record_count(path: Path) -> int | None:
    """Strictly validate a chunk JSONL file and return its record count.

    ``None`` means the file is missing, empty, malformed, or has an invalid
    record shape. Resume logic must not treat such a partial artifact as a
    completed chunking stage.
    """
    if not path.is_file() or path.stat().st_size == 0:
        return None
    try:
        count = 0
        with path.open(encoding="utf-8-sig") as handle:
            for line in handle:
                if not line.strip():
                    continue
                record = json.loads(line)
                if (not isinstance(record, dict)
                        or not isinstance(record.get("text"), str)
                        or not isinstance(record.get("metadata"), dict)):
                    return None
                count += 1
        return count if count else None
    except (OSError, UnicodeError, json.JSONDecodeError):
        return None


def _index_collection_count_impl(db_dir: Path, collection_name: str,
                                 db_backend: str = "chroma") -> int:
    """Return the exact physical collection count without creating a database."""
    db_dir = Path(db_dir)
    if not db_dir.is_dir():
        raise FileNotFoundError(f"Index directory not found: {db_dir}")
    if db_backend not in {"chroma", "qdrant"}:
        raise ValueError("db_backend must be 'chroma' or 'qdrant'")
    if db_backend == "qdrant":
        from qdrant_client import QdrantClient
        client = None
        operation_error = None
        try:
            client = QdrantClient(path=str(db_dir))
            if not client.collection_exists(collection_name):
                raise LookupError(
                    f"Collection '{collection_name}' not found in {db_dir}")
            result = client.count(
                collection_name=collection_name, exact=True)
            return int(result.count)
        except BaseException as exc:
            operation_error = exc
            raise
        finally:
            _finish_vector_client(
                client, client_name="Qdrant",
                primary_error=operation_error)

    import chromadb
    client = None
    operation_error = None
    try:
        client = chromadb.PersistentClient(
            path=str(db_dir), **_chroma_settings_kwargs(chromadb))
        try:
            collection = client.get_collection(collection_name)
        except Exception as exc:
            raise LookupError(
                f"Collection '{collection_name}' not found in {db_dir}") from exc
        return int(collection.count())
    except BaseException as exc:
        operation_error = exc
        raise
    finally:
        _finish_vector_client(
            client, client_name="Chroma",
            primary_error=operation_error)


def _index_collection_count(
        db_dir: Path, collection_name: str, db_backend: str = "chroma", *,
        lock_timeout: float = DEFAULT_DB_LOCK_TIMEOUT) -> int:
    """Return the physical count under the database's exclusive lease."""
    db_path = Path(db_dir)
    if db_backend not in {"chroma", "qdrant"}:
        raise ValueError("db_backend must be 'chroma' or 'qdrant'")
    with _vector_store_lock(
            db_path, backend=db_backend, collection_name=collection_name,
            operation="collection inspection", timeout=lock_timeout):
        db_path = _storage_policy.ensure_private_tree(db_path)
        marker_path = _index_update_marker_path(
            db_path, backend=db_backend, collection_name=collection_name)
        if marker_path.exists():
            raise ValueError(
                f"Index update is incomplete for {db_backend.title()} "
                f"collection '{collection_name}': {marker_path}. Re-run "
                "indexing to rebuild the collection before inspecting it."
            )
        return _index_collection_count_impl(
            db_path, collection_name, db_backend=db_backend)


def _index_has_data(db_dir: Path, collection_name: str,
                    db_backend: str = "chroma",
                    expected_count: int | None = None) -> bool:
    """Check whether a collection exists with a complete non-empty index."""
    try:
        count = _index_collection_count(
            db_dir, collection_name, db_backend=db_backend)
        return count > 0 and (
            expected_count is None or count == expected_count)
    except Exception:
        return False


def _provider_cli_defaults() -> _cli_policy.ProviderCliDefaults:
    """Return current facade defaults for pure CLI policy helpers."""
    return _cli_policy.ProviderCliDefaults(
        cloud_url=DEFAULT_CLOUD_URL,
        cloud_model=DEFAULT_CLOUD_MODEL,
        deepseek_url=DEFAULT_DEEPSEEK_URL,
        deepseek_model=DEFAULT_DEEPSEEK_MODEL,
        ollama_url=DEFAULT_OLLAMA_URL,
        ollama_model=DEFAULT_OLLAMA_MODEL,
        llm_workers=DEFAULT_LLM_WORKERS,
    )


def _resume_command_defaults() -> _cli_policy.ResumeCommandDefaults:
    """Return current facade defaults for resume-command serialization."""
    return _cli_policy.ResumeCommandDefaults(
        executable=sys.executable,
        script_name="rag.py",
        embedding_model=DEFAULT_EMBEDDING_MODEL,
        structure_profile=DEFAULT_STRUCTURE_PROFILE,
        db_backend=DEFAULT_DB_BACKEND,
        conversion_backend="pypdfium2",
        max_tokens=DEFAULT_MAX_TOKENS,
        min_words=MIN_CHUNK_WORDS,
        dedup_threshold=DEDUP_THRESHOLD,
        db_lock_timeout=DEFAULT_DB_LOCK_TIMEOUT,
        full_operation_timeout=DEFAULT_OPERATION_TIMEOUTS["full"],
        llm_cache_mode="off",
        llm_fallback="ordered",
        llm_failure_policy="best-effort",
        provider=_provider_cli_defaults(),
    )


def _build_resume_cmd(pdf: Path, args, extra_flags: str = "") -> str:
    """Build a shell command string to resume a failed ``full`` pipeline."""
    return _cli_policy._build_resume_cmd(
        pdf, args, extra_flags, defaults=_resume_command_defaults())


def _load_jsonl(path: Path) -> list[dict]:
    """Load a JSONL file with UTF-8/latin-1 fallback encoding.

    Skips malformed lines instead of discarding the entire file.
    """
    for enc in ("utf-8", "latin-1"):
        try:
            records = []
            bad_lines = 0
            with open(path, encoding=enc) as f:
                for lineno, line in enumerate(f, 1):
                    line = line.strip()
                    if not line:
                        continue
                    try:
                        records.append(json.loads(line))
                    except json.JSONDecodeError:
                        bad_lines += 1
                        if bad_lines <= 3:
                            log.warning(f"Skipping malformed JSON at {path.name}:{lineno}")
            if bad_lines > 3:
                log.warning(f"Skipped {bad_lines} malformed lines in {path.name}")
            return records
        except UnicodeDecodeError:
            continue
    return []


_artifact_stat_fingerprint = _artifact_io._artifact_stat_fingerprint


_read_index_artifact_snapshot = _artifact_io._read_index_artifact_snapshot


def _parse_index_records_strict(raw: bytes, path: Path) -> list[dict]:
    """Parse strictly through the facade's current stable-ID function."""
    return _artifact_io._parse_index_records_strict(
        raw, path, chunk_id_fn=_chunk_id)


def _quality_report_required(chunks_path: Path, records: list[dict]) -> bool:
    """Return whether this corpus participates in the quality contract."""
    return (
        _quality_core.quality_report_path(chunks_path).is_file()
        or _table_retrieval_core.has_table_retrieval_metadata(records)
        or any(
            "source_lineage_schema_version" in record.get("metadata", {})
            for record in records
        )
    )


def _load_index_chunk_completion_inputs(
        chunks_path: Path, *, chunks_sha256: str, chunks_size: int,
        records: list[dict]) -> tuple[dict, str]:
    """Validate the adjacent chunk-v3 completion used by index readers.

    Indexing does not need the live Docling/PDF inputs, but it must not accept
    a quality report whose provenance was detached from the chunk completion
    that committed this exact JSONL generation.
    """
    chunks_path = Path(chunks_path)
    manifest_path = _artifact_completion_path(
        chunks_path, stage="chunking")
    try:
        raw, _, _ = _read_index_artifact_snapshot(
            manifest_path, max_bytes=_MAX_CHUNK_COMPLETION_BYTES)
    except FileNotFoundError as exc:
        raise ValueError(
            "Quality-bound chunks require an adjacent chunk-v3 completion: "
            f"{manifest_path}"
        ) from exc
    payload = _strict_json_object(raw, description="chunk completion")
    if payload.get("schema_version") != CHUNK_COMPLETION_SCHEMA_VERSION:
        raise ValueError(
            "Quality-bound chunks require a supported chunk-v3 completion")
    if set(payload) != {
            "schema_version", "stage", "source_sha256",
            "source_record_count", "parameters_sha256", "outputs",
            "inputs", "structure_profile",
            "structure_profile_parameters_sha256"}:
        raise ValueError("chunk completion has an invalid field set")

    inputs = payload.get("inputs")
    if (payload.get("stage") != "chunking"
            or payload.get("source_record_count") is not None
            or not isinstance(payload.get("parameters_sha256"), str)
            or re.fullmatch(
                r"[0-9a-f]{64}", payload["parameters_sha256"]) is None
            or not _quality_core._valid_input_bindings(inputs)):
        raise ValueError("chunk completion header or inputs are invalid")
    if payload.get("source_sha256") != inputs["docling_json"]["sha256"]:
        raise ValueError("chunk completion header or inputs are invalid")
    receipt = payload.get("structure_profile")
    if payload.get("structure_profile_parameters_sha256") != (
            _structure_profile_parameters_binding(
                payload["parameters_sha256"], receipt)):
        raise ValueError(
            "chunk completion structure profile is detached from parameters")

    outputs = payload.get("outputs")
    output = (
        outputs[0]
        if isinstance(outputs, list) and len(outputs) == 1
        else None
    )
    if (not isinstance(output, dict)
            or set(output) != {"role", "name", "size", "sha256"}
            or output.get("role") != "chunks_jsonl"
            or not _valid_manifest_file_record({
                key: output.get(key) for key in ("name", "size", "sha256")
            })
            or output.get("name") != chunks_path.name
            or output.get("size") != chunks_size
            or output.get("sha256") != chunks_sha256):
        raise ValueError(
            "chunk completion does not bind this chunks generation")

    if (any(
            isinstance(metadata := record.get("metadata"), dict)
            and metadata.get("table_recovered_from_pdf")
            for record in records)
            and inputs.get("table_recovery") is None):
        raise ValueError(
            "recovered tables lack a bound source-PDF input")
    return inputs, payload["parameters_sha256"]


def _validated_quality_report_binding(
        chunks_path: Path, records: list[dict], chunks_sha256: str,
        chunks_size: int, *, source_name: str | None = None,
        source_sha256: str | None = None,
        parameters_sha256: str | None = None,
        embedding_model: str | None = None,
        embedding_limit: int | None = None,
        input_bindings: dict | None = None,
        allow_legacy_quality: bool = True,
) -> tuple[int | None, str | None, dict | None]:
    """Validate one exact adjacent report snapshot and return its binding."""
    chunks_path = Path(chunks_path)
    if not _quality_report_required(chunks_path, records):
        return None, None, None
    report_path = _quality_core.quality_report_path(chunks_path)
    report_raw, report_sha256, _ = _read_index_artifact_snapshot(
        report_path, max_bytes=_quality_core.MAX_QUALITY_REPORT_BYTES)
    stable_ids = [_retrieval_core._chunk_id(record) for record in records]
    chunk_hashes = [_retrieval_core._chunk_hash(record) for record in records]
    recovered_table_refs = _recovered_table_refs_from_records(records)
    payload = _quality_core.parse_quality_report_bytes(
        report_raw,
        chunks_name=chunks_path.name,
        chunks_sha256=chunks_sha256,
        chunks_size=chunks_size,
        record_count=len(records),
        stable_ids=stable_ids,
        chunk_hashes=chunk_hashes,
        source_name=source_name,
        source_sha256=source_sha256,
        parameters_sha256=parameters_sha256,
        embedding_model=embedding_model,
        embedding_limit=embedding_limit,
        input_bindings=input_bindings,
        recovered_table_count=len(recovered_table_refs),
        recovered_table_refs=sorted(recovered_table_refs),
        records=records,
        compatible_schema_versions=(
            (_quality_core.LEGACY_QUALITY_REPORT_SCHEMA_VERSION,)
            if allow_legacy_quality else ()),
    )
    if input_bindings is None:
        completion_inputs, completion_parameters_sha256 = (
            _load_index_chunk_completion_inputs(
            chunks_path,
            chunks_sha256=chunks_sha256,
            chunks_size=chunks_size,
            records=records,
        ))
        if (payload.get("inputs") != completion_inputs
                or payload.get("parameters_sha256")
                != completion_parameters_sha256):
            raise ValueError(
                "corpus quality report does not match input bindings or "
                "parameters")
    return payload["schema_version"], report_sha256, payload


def _load_index_snapshot_with_quality(
        path: Path, *, allow_legacy_quality: bool = True,
) -> tuple[
        list[dict], str, tuple[int, int, int, int, int],
        tuple[int | None, str | None],
]:
    """Load one exact chunks snapshot plus its validated quality binding."""
    path = Path(path)
    with _chunk_output_lease(path):
        raw, source_sha256, fingerprint = _read_index_artifact_snapshot(path)
        records = _parse_index_records_strict(raw, path)
        schema_version, report_sha256, _ = _validated_quality_report_binding(
            path, records, source_sha256, len(raw),
            allow_legacy_quality=allow_legacy_quality)
        return (
            records, source_sha256, fingerprint,
            (schema_version, report_sha256),
        )


def _load_index_snapshot_strict(
        path: Path,
) -> tuple[list[dict], str, tuple[int, int, int, int, int]]:
    """Load records, SHA-256, and identity from one exact byte snapshot."""
    records, source_sha256, fingerprint, _ = (
        _load_index_snapshot_with_quality(path))
    return records, source_sha256, fingerprint


def _load_index_records_strict(path: Path) -> list[dict]:
    """Load a complete, schema-valid chunks artifact or fail without indexing.

    The general JSONL reader is intentionally tolerant for best-effort exports.
    An index update cannot be tolerant: skipped or malformed rows would look
    like authoritative deletions to incremental indexing.
    """
    records, _, _ = _load_index_snapshot_strict(path)
    return records


@dataclass
class _IndexOperationalTracker:
    collection_delete_calls: int = 0
    collection_create_calls: int = 0
    record_delete_calls: int = 0
    upsert_calls: int = 0
    committed: bool = False
    queue: _operational_metrics.QueueBackpressureMetrics = field(
        default_factory=_operational_metrics.QueueBackpressureMetrics)

    def contract(self) -> _operation_contracts.IndexOperationMetrics:
        queue_metrics = self.queue.snapshot()
        return _operation_contracts.IndexOperationMetrics(
            collection_delete_calls=self.collection_delete_calls,
            collection_create_calls=self.collection_create_calls,
            record_delete_calls=self.record_delete_calls,
            upsert_calls=self.upsert_calls,
            queue_put_count=int(queue_metrics["queue_put_count"]),
            queue_saturation_events=int(
                queue_metrics["queue_saturation_events"]),
            queue_wait_ms=float(queue_metrics["queue_wait_ms"]),
        )

    def attempted_telemetry_metrics(self) -> dict[str, int | float | bool]:
        """Return exact content-free attempts after an uncommitted failure."""
        operations = self.contract()
        return {
            "committed": self.committed,
            "attempted_collection_delete_calls": (
                operations.collection_delete_calls),
            "attempted_collection_create_calls": (
                operations.collection_create_calls),
            "attempted_record_delete_calls": operations.record_delete_calls,
            "attempted_upsert_calls": operations.upsert_calls,
            "attempted_physical_mutation_calls": (
                operations.physical_mutation_calls),
            "attempted_queue_put_count": operations.queue_put_count,
            "queue_saturation_events": operations.queue_saturation_events,
            "queue_wait_ms": operations.queue_wait_ms,
        }


def _put_unless_worker_failed(
        work_queue: queue.Queue, item, worker_future, *,
        metrics: _operational_metrics.QueueBackpressureMetrics | None = None,
        poll_interval: float = 0.1,
        monotonic_clock=time.monotonic) -> None:
    """Put with backpressure while surfacing a failed consumer promptly."""
    _operational_metrics.put_unless_worker_failed(
        work_queue, item, worker_future, metrics=metrics,
        poll_interval=poll_interval, monotonic_clock=monotonic_clock)


def _log_cleanup_error(message: str, *args,
                       error: BaseException) -> None:
    """Best-effort cleanup diagnostics that cannot alter error precedence."""
    try:
        log.error(
            message,
            *args,
            exc_info=(type(error), error, error.__traceback__),
        )
    except BaseException:
        # User-installed logging handlers are allowed to raise. Cleanup
        # diagnostics must never replace the operation/cleanup error selected
        # by the caller's explicit precedence rules.
        pass


def _observe_failed_index_operation(
        observer: Callable[[dict[str, int | float | bool]], None] | None,
        tracker: _IndexOperationalTracker) -> None:
    """Publish failure counters without changing primary-error precedence."""
    if observer is None:
        return
    try:
        observer(tracker.attempted_telemetry_metrics())
    except BaseException:
        _log_cleanup_error(
            "Index failure-metric observation also failed",
            error=RuntimeError("redacted index metric observer failure"))


@dataclass
class _VectorStoreLockState:
    """One reentrant in-process gate for a canonical database directory."""

    thread_lock: Any = field(default_factory=_threading.RLock)
    handle: Any = None


_vector_store_lock_states: dict[str, _VectorStoreLockState] = {}
_vector_store_lock_states_guard = _threading.Lock()
_vector_store_lock_local = _threading.local()


def _reset_vector_store_locks_after_fork() -> None:
    """Drop inherited handles and synchronization state in a forked child."""
    global _vector_store_lock_states, _vector_store_lock_states_guard
    global _vector_store_lock_local
    global _artifact_sha256_cache, _artifact_sha256_cache_lock
    global _bm25_cache, _bm25_cache_lock
    global _reranker_instances, _reranker_lock
    for state in _vector_store_lock_states.values():
        if state.handle is not None:
            try:
                # Close only: explicitly unlocking an inherited POSIX flock
                # could release the parent's shared open-file-description lock.
                state.handle.close()
            except BaseException:
                pass
    _vector_store_lock_states = {}
    _vector_store_lock_states_guard = _threading.Lock()
    _vector_store_lock_local = _threading.local()
    if "_artifact_sha256_cache" in globals():
        _artifact_sha256_cache = {}
        _artifact_sha256_cache_lock = _threading.Lock()
    if "_bm25_cache" in globals():
        _bm25_cache = {}
        _bm25_cache_lock = _threading.Lock()
    if "_reranker_instances" in globals():
        # Model objects and a mutex inherited from a multithreaded parent are
        # not safe to reuse in the child. Reload lazily on first use instead.
        _reranker_instances = {}
        _reranker_lock = _threading.Lock()


if hasattr(os, "register_at_fork"):
    os.register_at_fork(after_in_child=_reset_vector_store_locks_after_fork)


def _normalize_db_lock_timeout(timeout: float) -> float:
    """Validate a finite, bounded vector-store lease timeout."""
    if isinstance(timeout, bool):
        raise ValueError("db lock timeout must be a finite non-negative number")
    try:
        value = float(timeout)
    except (TypeError, ValueError) as exc:
        raise ValueError(
            "db lock timeout must be a finite non-negative number") from exc
    if (not math.isfinite(value) or value < 0
            or value > _threading.TIMEOUT_MAX):
        raise ValueError(
            "db lock timeout must be a finite non-negative number no greater "
            f"than {_threading.TIMEOUT_MAX:g} seconds")
    return value


def _strip_windows_extended_path_prefix(value: str) -> str:
    """Normalize Win32 extended paths to their ordinary drive/UNC spelling."""
    if os.name != "nt":
        return value
    if value.casefold().startswith("\\\\?\\unc\\"):
        return "\\\\" + value[8:]
    if re.match(r"^\\\\\?\\[A-Za-z]:[\\/]", value):
        return value[4:]
    return value


def _resolved_vector_store_path(db_dir: Path) -> Path:
    """Resolve one database path while retaining its filesystem casing."""
    path = Path(_strip_windows_extended_path_prefix(str(Path(db_dir))))
    try:
        resolved = path.resolve(strict=False)
    except (OSError, RuntimeError):
        resolved = Path(os.path.abspath(path))
    return Path(_strip_windows_extended_path_prefix(str(resolved)))


def _vector_store_lock_identity(db_dir: Path) -> tuple[Path, str]:
    """Return the filesystem path and normalized comparison key together."""
    resolved = _resolved_vector_store_path(db_dir)
    return resolved, os.path.normcase(str(resolved))


def _canonical_vector_store_key(db_dir: Path) -> str:
    """Return one platform-normalized identity for a database directory."""
    return _vector_store_lock_identity(db_dir)[1]


def _vector_store_lock_directory(resolved: Path) -> Path:
    """Keep owned-run sentinels outside the deletable run directory."""
    run_root = resolved.parent
    output_root = run_root.parent
    marker = run_root / _retention.RUN_MANIFEST_NAME
    if marker.exists() or _storage_policy.path_is_link_like(marker):
        _, manifest = _retention.load_pipeline_run_manifest(
            output_root, run_root.name)
        relative = resolved.relative_to(output_root).as_posix()
        if any(record["path"] == relative
               for record in manifest["vector_stores"]):
            return output_root / ".rag-locks"
    return run_root / ".rag-locks"


def _vector_store_lock_path(db_dir: Path) -> Path:
    """Return the persistent sidecar used only as an OS-locking inode."""
    resolved, key = _vector_store_lock_identity(db_dir)
    safe_name = re.sub(r"[^A-Za-z0-9_.-]+", "_", resolved.name)
    safe_name = safe_name.strip("._")[:40] or "vector-store"
    digest = hashlib.sha256(key.encode("utf-8")).hexdigest()[:20]
    return _vector_store_lock_directory(
        resolved) / f"{safe_name}-{digest}.lock"


def _vector_store_lock_state(key: str) -> _VectorStoreLockState:
    with _vector_store_lock_states_guard:
        return _vector_store_lock_states.setdefault(
            key, _VectorStoreLockState())


def _try_vector_file_lock(handle) -> bool:
    """Attempt one non-blocking exclusive OS lock of the sentinel's byte 0."""
    handle.seek(0)
    if os.name == "nt":
        import msvcrt
        try:
            msvcrt.locking(handle.fileno(), msvcrt.LK_NBLCK, 1)
        except OSError as exc:
            if (exc.errno in {errno.EACCES, errno.EAGAIN, errno.EDEADLK}
                    or getattr(exc, "winerror", None) in {33, 36, 158}):
                return False
            raise
        return True

    import fcntl
    try:
        fcntl.flock(handle.fileno(), fcntl.LOCK_EX | fcntl.LOCK_NB)
    except OSError as exc:
        if exc.errno in {errno.EACCES, errno.EAGAIN}:
            return False
        raise
    return True


def _unlock_vector_file(handle) -> None:
    """Release the platform OS lock held by *handle*."""
    handle.seek(0)
    if os.name == "nt":
        import msvcrt
        msvcrt.locking(handle.fileno(), msvcrt.LK_UNLCK, 1)
        return

    import fcntl
    fcntl.flock(handle.fileno(), fcntl.LOCK_UN)


def _finish_vector_file_lock(handle, *, primary_error: BaseException | None,
                             lock_path: Path) -> None:
    """Unlock and close a lease handle without changing error precedence."""
    cleanup_errors: list[BaseException] = []
    try:
        _unlock_vector_file(handle)
    except BaseException as exc:
        cleanup_errors.append(exc)
    try:
        handle.close()
    except BaseException as exc:
        cleanup_errors.append(exc)

    if not cleanup_errors:
        return
    selected_error = primary_error or cleanup_errors[0]
    for cleanup_error in cleanup_errors:
        if cleanup_error is selected_error:
            continue
        _log_cleanup_error(
            "Vector-store lease cleanup also failed for %s",
            lock_path,
            error=cleanup_error,
        )
    if primary_error is None:
        raise selected_error.with_traceback(selected_error.__traceback__)


class _VectorStoreLease:
    """Bounded, reentrant, process-safe exclusive database-directory lease."""

    def __init__(self, db_dir: Path, *, backend: str, collection_name: str,
                 operation: str, timeout: float,
                 resource_description: str | None = None,
                 timeout_option: str = "--db-lock-timeout"):
        self.db_dir = Path(db_dir)
        self.backend = backend
        self.collection_name = collection_name
        self.operation = operation
        self.resource_description = resource_description or (
            f"vector store ({backend} collection '{collection_name}')")
        self.timeout_option = timeout_option
        self.timeout = _normalize_db_lock_timeout(timeout)
        resolved, self.key = _vector_store_lock_identity(self.db_dir)
        safe_name = re.sub(r"[^A-Za-z0-9_.-]+", "_", resolved.name)
        safe_name = safe_name.strip("._")[:40] or "vector-store"
        digest = hashlib.sha256(self.key.encode("utf-8")).hexdigest()[:20]
        self.lock_path = (
            _vector_store_lock_directory(resolved)
            / f"{safe_name}-{digest}.lock")
        self.state = _vector_store_lock_state(self.key)
        self._process_id = os.getpid()
        self._owner_pid = None
        self._entered = False
        self._reentrant = False
        self._handle = None

    def _busy_error(self) -> VectorStoreBusyError:
        return VectorStoreBusyError(
            f"Timed out after {self.timeout:g}s waiting for exclusive "
            f"access during {self.operation}: {self.db_dir} "
            f"[{self.resource_description}]. Another local operation is "
            f"using this resource; retry after it finishes or increase "
            f"{self.timeout_option}."
        )

    def __enter__(self):
        if self._entered:
            raise RuntimeError("Vector-store lease objects cannot be reused")
        current_pid = os.getpid()
        if current_pid != self._process_id:
            # A lease object constructed (but not entered) before fork must use
            # the child's freshly initialized synchronization registry.
            self.state = _vector_store_lock_state(self.key)
            self._process_id = current_pid
        deadline = time.monotonic() + self.timeout
        if not self.state.thread_lock.acquire(timeout=self.timeout):
            raise self._busy_error()

        thread_leases = getattr(
            _vector_store_lock_local, "leases", None)
        if thread_leases is None:
            thread_leases = {}
            _vector_store_lock_local.leases = thread_leases
        if self.key in thread_leases:
            thread_leases[self.key] += 1
            self._entered = True
            self._reentrant = True
            self._owner_pid = current_pid
            return self

        handle = None
        os_locked = False
        try:
            _storage_policy.ensure_private_directory(self.lock_path.parent)
            _storage_policy.assert_no_link_components(self.lock_path)
            flags = os.O_RDWR | os.O_APPEND | os.O_CREAT
            flags |= getattr(os, "O_NOFOLLOW", 0)
            descriptor = os.open(
                self.lock_path, flags, _storage_policy.PRIVATE_FILE_MODE)
            handle = os.fdopen(descriptor, "a+b")
            lock_stat = os.fstat(handle.fileno())
            if (not stat.S_ISREG(lock_stat.st_mode)
                    or lock_stat.st_nlink != 1):
                raise _storage_policy.StoragePolicyError(
                    "vector-store lock must be one regular, unlinked file")
            _storage_policy.enforce_private_path(
                self.lock_path, directory=False)
            handle.seek(0, os.SEEK_END)
            if handle.tell() == 0:
                handle.write(b"\0")
                handle.flush()
                os.fsync(handle.fileno())

            first_attempt = True
            while True:
                remaining = deadline - time.monotonic()
                # Opening, permission-checking, and initially syncing the
                # private sidecar can consume a very small timeout on a slow
                # filesystem.  The operating-system lock attempt is
                # nonblocking, so always make exactly one attempt before
                # treating the deadline as exhausted.  This preserves true
                # zero-timeout try-lock semantics without turning unrelated,
                # uncontended paths into false busy results.
                if remaining <= 0 and not first_attempt:
                    raise self._busy_error()
                if _try_vector_file_lock(handle):
                    os_locked = True
                    break
                first_attempt = False
                remaining = deadline - time.monotonic()
                if remaining <= 0:
                    raise self._busy_error()
                time.sleep(min(0.05, remaining))

            thread_leases[self.key] = 1
            self._handle = handle
            self.state.handle = handle
            self._entered = True
            self._owner_pid = current_pid
            return self
        except BaseException as exc:
            thread_leases.pop(self.key, None)
            if self.state.handle is handle:
                self.state.handle = None
            if handle is not None:
                if os_locked:
                    _finish_vector_file_lock(
                        handle, primary_error=exc,
                        lock_path=self.lock_path)
                else:
                    try:
                        handle.close()
                    except BaseException as close_error:
                        _log_cleanup_error(
                            "Vector-store lease handle cleanup failed for %s",
                            self.lock_path,
                            error=close_error,
                        )
            self.state.thread_lock.release()
            raise

    def __exit__(self, exc_type, exc, traceback):
        if not self._entered:
            return False
        if self._owner_pid != os.getpid():
            # ``after_in_child`` already closed the inherited descriptor and
            # replaced the lock registry. Unwinding the parent's context in a
            # forked child must not touch either parent's lock or stale TLS.
            self._entered = False
            self._handle = None
            return False
        thread_leases = _vector_store_lock_local.leases
        cleanup_error = None
        try:
            depth = thread_leases.get(self.key)
            if not isinstance(depth, int) or depth < 1:
                raise RuntimeError("Vector-store lease ownership was lost")
            if depth > 1:
                thread_leases[self.key] = depth - 1
            else:
                thread_leases.pop(self.key)
                if self._handle is None:
                    raise RuntimeError("Vector-store lease handle was lost")
                try:
                    _finish_vector_file_lock(
                        self._handle, primary_error=exc,
                        lock_path=self.lock_path)
                except BaseException as release_error:
                    cleanup_error = release_error
                finally:
                    self.state.handle = None
        finally:
            self._entered = False
            self.state.thread_lock.release()

        if cleanup_error is not None:
            raise cleanup_error.with_traceback(cleanup_error.__traceback__)
        return False


def _vector_store_lock(
        db_dir: Path, *, backend: str, collection_name: str,
        operation: str, timeout: float = DEFAULT_DB_LOCK_TIMEOUT,
) -> _VectorStoreLease:
    """Create the path-wide lease used by every local vector-store access."""
    if backend not in {"chroma", "qdrant"}:
        raise ValueError("backend must be 'chroma' or 'qdrant'")
    return _VectorStoreLease(
        db_dir, backend=backend, collection_name=collection_name,
        operation=operation, timeout=timeout)


def _pipeline_job_lock(
        pdf_path: Path, *, timeout: float = DEFAULT_DB_LOCK_TIMEOUT,
        output_root: Path | None = None,
) -> _VectorStoreLease:
    """Serialize output-run allocation and execution for one PDF stem."""
    root = Path(OUTPUT_DIR) if output_root is None else Path(output_root)
    scope = root / f".pipeline-job-{Path(pdf_path).stem}"
    return _VectorStoreLease(
        scope, backend="pipeline", collection_name=Path(pdf_path).stem,
        operation="pipeline output allocation and execution",
        timeout=timeout,
        resource_description=f"pipeline outputs for '{Path(pdf_path).stem}'",
        timeout_option="--db-lock-timeout",
    )


@contextmanager
def _conversion_output_lease(
        *targets: Path, timeout: float = DEFAULT_DB_LOCK_TIMEOUT):
    """Serialize writers for every conversion output pathname."""
    canonical_targets = sorted({
        str(Path(target).resolve(strict=False)) for target in targets
    }, key=os.path.normcase)
    with ExitStack() as leases:
        for value in canonical_targets:
            target = Path(value)
            leases.enter_context(_VectorStoreLease(
                target,
                backend="conversion",
                collection_name=target.name,
                operation="conversion artifact publication",
                timeout=timeout,
                resource_description=f"conversion output '{target}'",
                timeout_option="conversion lock timeout",
            ))
        yield


@contextmanager
def _chunk_output_lease(
        chunks_output: Path, *, timeout: float = DEFAULT_DB_LOCK_TIMEOUT):
    """Serialize chunks, completion, and quality-report publication."""
    chunks_output = Path(chunks_output)
    targets = {
        chunks_output,
        _artifact_completion_path(chunks_output, stage="chunking"),
        _quality_core.quality_report_path(chunks_output),
    }
    canonical_targets = sorted({
        str(target.resolve(strict=False)) for target in targets
    }, key=os.path.normcase)
    with ExitStack() as leases:
        for value in canonical_targets:
            target = Path(value)
            leases.enter_context(_VectorStoreLease(
                target,
                backend="chunking",
                collection_name=chunks_output.name,
                operation="chunk artifact publication",
                timeout=timeout,
                resource_description=f"chunk output '{target}'",
                timeout_option="chunk output lock timeout",
            ))
        yield


def _finish_executor_progress(executor, progress, *, operation_name: str,
                              primary_error: BaseException | None) -> None:
    """Close parallel resources without masking an operation failure.

    ``primary_error`` must be the exception caught from the parallel operation,
    not ambient ``sys.exc_info()``.  Every created resource gets a cleanup
    attempt.  An operation failure takes precedence over cleanup failures;
    otherwise executor shutdown takes precedence over progress-bar closure.
    """
    cleanup_errors: list[BaseException] = []
    if executor is not None:
        try:
            executor.shutdown(wait=True)
        except BaseException as exc:
            cleanup_errors.append(exc)
    try:
        progress.close()
    except BaseException as exc:
        cleanup_errors.append(exc)

    if not cleanup_errors:
        return

    if primary_error is not None:
        for cleanup_error in cleanup_errors:
            if cleanup_error is primary_error:
                continue
            _log_cleanup_error(
                "%s cleanup also failed while preserving the active error",
                operation_name,
                error=cleanup_error,
            )
        return

    first_error, *additional_errors = cleanup_errors
    for cleanup_error in additional_errors:
        _log_cleanup_error(
            "%s cleanup encountered an additional error",
            operation_name,
            error=cleanup_error,
        )
    raise first_error.with_traceback(first_error.__traceback__)


def _finish_vector_client(client, *, client_name: str,
                          primary_error: BaseException | None) -> None:
    """Require vector-client closure without masking primary work."""
    if client is None:
        return
    try:
        close = getattr(client, "close", None)
        if not callable(close):
            raise RuntimeError(
                f"{client_name} client does not expose required close()")
        close()
        if sys.platform == "win32" and client_name == "Qdrant":
            inner_client = getattr(client, "_client", None)
            is_local_client = type(inner_client).__module__.startswith(
                "qdrant_client.local.")
        else:
            is_local_client = False
        if is_local_client:
            # qdrant-client local persistence creates short-lived sqlite
            # cursors that can retain a Windows file handle after close().
            # One explicit full collection finalizes those unreachable cursors
            # so a caller can immediately move or remove a multi-collection DB.
            gc.collect()
    except BaseException as close_error:
        if primary_error is None:
            raise
        if close_error is not primary_error:
            _log_cleanup_error(
                "%s client cleanup also failed while preserving the "
                "active error",
                client_name,
                error=close_error,
            )


class _VectorClientOwner:
    """Track vector clients for explicit pre-commit and fallback cleanup."""

    def __init__(self, client_name: str):
        self.client_name = client_name
        self._clients = []

    def own(self, client):
        if any(owned is client for owned in self._clients):
            raise RuntimeError(f"{self.client_name} client is already owned")
        self._clients.append(client)
        return client

    def close(self, client) -> None:
        for index in range(len(self._clients) - 1, -1, -1):
            if self._clients[index] is client:
                self._clients.pop(index)
                _finish_vector_client(
                    client, client_name=self.client_name,
                    primary_error=None)
                return
        raise RuntimeError(f"{self.client_name} client is not owned")

    def finish(self, primary_error: BaseException | None) -> None:
        cleanup_errors = []
        while self._clients:
            client = self._clients.pop()
            try:
                _finish_vector_client(
                    client, client_name=self.client_name,
                    primary_error=None)
            except BaseException as exc:
                cleanup_errors.append(exc)

        if not cleanup_errors:
            return
        if primary_error is not None:
            for cleanup_error in cleanup_errors:
                if cleanup_error is primary_error:
                    continue
                _log_cleanup_error(
                    "%s client cleanup also failed while preserving the "
                    "active error",
                    self.client_name,
                    error=cleanup_error,
                )
            return

        first_error, *additional_errors = cleanup_errors
        for cleanup_error in additional_errors:
            _log_cleanup_error(
                "%s client cleanup encountered an additional error",
                self.client_name,
                error=cleanup_error,
            )
        raise first_error.with_traceback(first_error.__traceback__)


def _finish_queue_worker(work_queue: queue.Queue, worker_future,
                         worker_executor, progress, *,
                         worker_name: str,
                         primary_error: BaseException | None) -> None:
    """Stop a queue worker and close resources without masking a primary error.

    This helper is intended for a ``finally`` block.  ``primary_error`` must be
    the exception caught from this producer, not ambient ``sys.exc_info()``.
    When present, worker or cleanup failures are logged so the producer's
    original exception keeps its traceback.  Otherwise the first cleanup
    failure is propagated after every resource has had a chance to close.
    """
    cleanup_errors: list[BaseException] = []

    if worker_future is not None:
        worker_was_done = worker_future.done()
        try:
            if not worker_was_done:
                _put_unless_worker_failed(work_queue, None, worker_future)
        except BaseException as exc:
            cleanup_errors.append(exc)
        else:
            try:
                worker_error = worker_future.exception()
            except BaseException as exc:
                cleanup_errors.append(exc)
            else:
                if worker_error is not None:
                    cleanup_errors.append(worker_error)
                elif worker_was_done:
                    cleanup_errors.append(RuntimeError(
                        f"{worker_name} exited before it was asked to stop"))

    close_resources = [progress.close]
    if worker_executor is not None:
        close_resources.insert(
            0, lambda: worker_executor.shutdown(wait=True))
    for close_resource in close_resources:
        try:
            close_resource()
        except BaseException as exc:
            cleanup_errors.append(exc)

    if not cleanup_errors:
        return

    if primary_error is not None:
        for cleanup_error in cleanup_errors:
            if cleanup_error is primary_error:
                continue
            _log_cleanup_error(
                "%s cleanup also failed while preserving the active error",
                worker_name,
                error=cleanup_error,
            )
        return

    first_error, *additional_errors = cleanup_errors
    for cleanup_error in additional_errors:
        _log_cleanup_error(
            "%s cleanup encountered an additional error",
            worker_name,
            error=cleanup_error,
        )
    raise first_error.with_traceback(first_error.__traceback__)


# ---------------------------------------------------------------------------
# PUA digit decoder (shared utility)
# ---------------------------------------------------------------------------
_PUA_DIGIT_MAP_GLOBAL = {chr(0xF643 + i): str(i) for i in range(10)}

def _decode_pua(s: str) -> str:
    """Replace Private Use Area font glyphs with ASCII digits."""
    if not any(0xE000 <= ord(c) <= 0xF8FF for c in s):
        return s
    return "".join(_PUA_DIGIT_MAP_GLOBAL.get(c, c) for c in s)


# ---------------------------------------------------------------------------
# Agent Team Orchestration — multi-tier hierarchy for pipeline tasks
# ---------------------------------------------------------------------------
#
# Every pipeline task that modifies data flows through a 4-tier agent team:
#
#   USER (0) ↔ Director (I) ↔ Manager (II) ↔ QC (III) ↔ Expert(s) (IV)
#
# Forward pass (planning):
#   I  — Director receives request, creates high-level plan
#   II — Manager decomposes into concrete subtasks with edge cases
#   III — QC defines measurable acceptance criteria
#   IV — Expert(s) execute subtasks
#
# Return pass (validation):
#   IV results → III validates (2-strike retry rule)
#   → II reviews for edge cases and purpose alignment
#   → I performs acceptance test against actual data
#
# Communication: each tier only talks to its adjacent tier.
# ---------------------------------------------------------------------------

class _AgentTeam:
    """Multi-tier LLM agent team for pipeline tasks.

    Create one team per major pipeline operation. The team orchestrates
    planning, execution, validation, and acceptance testing through
    four tiers of LLM agents.

    Usage::

        team = _AgentTeam("Scaffold Construction",
                          context="Property I, TOC pp.16-24",
                          **llm_kwargs)

        result = team.run(
            request="Build authoritative hierarchy from TOC",
            expert_fn=lambda: do_the_work(),
            validator_fn=lambda result: {"passed": bool, "flagged": [...]},
            test_fn=lambda result: acceptance_test(result),
        )
        # result["result"], result["accepted"], result["flagged"], result["audit"]
    """

    _TIER = {1: "Director", 2: "Manager", 3: "QC", 4: "Expert"}

    def __init__(self, task_name: str, context: str = "", **llm_kwargs):
        self.task_name = task_name
        self.context = context
        self.enabled = bool(llm_kwargs.pop("enabled", True))
        self.kw = {k: v for k, v in llm_kwargs.items()
                   if k in ("cloud_url", "cloud_model", "cloud_key",
                            "ollama_url", "ollama_model", "gemini_key",
                            "llm_workers", "thinking", "security_policy")}
        self.audit: list[dict] = []
        self.flagged: list[str] = []

    # -- Logging ----------------------------------------------------------

    def _log(self, tier: int, action: str, detail: str = ""):
        name = self._TIER.get(tier, f"Tier-{tier}")
        self.audit.append({"tier": tier, "name": name,
                           "action": action, "detail": detail[:300]})
        msg = f"  [{name}] {action}"
        if detail:
            msg += f": {detail[:120]}"
        log.info(msg)

    # -- LLM call wrapper -------------------------------------------------

    def _ask(self, role: str, prompt: str) -> str:
        """LLM call with role context prepended."""
        if not self.enabled:
            return ""
        full = f"[ROLE: {role}]\n\n{prompt}"
        role_label = role.partition(" ")[0].casefold()
        r = _call_llm(
            full, operation=f"agent_team.{role_label}", **self.kw)
        return r.strip() if r else ""

    # -- Summarizer -------------------------------------------------------

    @staticmethod
    def _summarize(obj) -> str:
        """Brief text summary for LLM review prompts."""
        if isinstance(obj, list):
            if not obj:
                return "Empty list"
            sample = json.dumps(obj[:2], default=str)[:200]
            return f"List of {len(obj)} items. Sample: {sample}"
        if isinstance(obj, dict):
            return json.dumps(obj, default=str)[:400]
        if obj is None:
            return "None"
        return str(obj)[:400]

    # -- Individual tier methods (use for fine-grained control) ------------

    def director_plan(self, request: str) -> str:
        """Tier I → II: Director creates execution plan."""
        self._log(1, "Received task", request[:100])
        plan = self._ask(
            "Director Agent (Tier I) — you plan and oversee pipeline tasks, "
            "and perform final acceptance testing against real data",
            f"Task: {self.task_name}\nRequest: {request}\n"
            f"Context: {self.context}\n\n"
            "Create a brief execution plan (3-6 numbered steps). "
            "State what success looks like. No preamble.")
        self._log(1, "Plan created", plan[:200])
        return plan

    def manager_decompose(self, plan: str) -> str:
        """Tier II → III: Manager creates concrete subtasks."""
        self._log(2, "Decomposing plan")
        tasks = self._ask(
            "Manager Agent (Tier II) — you decompose plans into subtasks, "
            "identify edge cases, and review results for purpose alignment",
            f"Task: {self.task_name}\nDirector's plan:\n{plan}\n\n"
            "List concrete subtasks. For each: action, expected output, "
            "edge cases to watch for. Numbered list, no preamble.")
        self._log(2, "Subtasks", tasks[:200])
        return tasks

    def qc_criteria(self, subtasks: str) -> str:
        """Tier III → IV: QC defines acceptance criteria."""
        self._log(3, "Defining criteria")
        criteria = self._ask(
            "QC Agent (Tier III) — you define pass/fail criteria "
            "and validate expert output. 2-strike rule: flag after 2 failures",
            f"Task: {self.task_name}\nSubtasks:\n{subtasks}\n\n"
            "Define measurable pass/fail criteria for each subtask. "
            "Include numeric thresholds where applicable. Numbered list.")
        self._log(3, "Criteria defined", criteria[:200])
        return criteria

    def expert_execute(self, fn, *args, **kwargs):
        """Tier IV: Expert executes work by calling a pipeline function."""
        fn_name = getattr(fn, "__name__", str(fn))
        self._log(4, f"Executing {fn_name}")
        try:
            result = fn(*args, **kwargs)
            self._log(4, f"{fn_name} completed",
                      self._summarize(result)[:100])
            return result
        except Exception as e:
            self._log(4, f"{fn_name} FAILED", str(e))
            raise

    def qc_validate(self, results_summary: str, criteria: str,
                    programmatic_check: dict = None) -> dict:
        """Tier III ← IV: QC validates expert output.

        Combines programmatic check (fast/definitive) with LLM review
        (nuanced). Returns ``{"passed": bool, "issues": [...]}``.
        """
        self._log(3, "Validating")
        issues = []

        # Programmatic check
        if programmatic_check and not programmatic_check.get("passed", True):
            issues.extend(programmatic_check.get("flagged", []))

        # LLM review
        qc_review = self._ask(
            "QC Agent (Tier III) — validate results against criteria",
            f"Task: {self.task_name}\nCriteria:\n{criteria}\n\n"
            f"Results:\n{results_summary}\n"
            + (f"Programmatic check: "
               f"{json.dumps(programmatic_check, default=str)[:300]}\n"
               if programmatic_check else "")
            + "\nVerdict: PASS or FAIL. If FAIL, list unmet criteria.")

        if qc_review and "FAIL" in qc_review.upper():
            issues.append(qc_review[:200])

        passed = len(issues) == 0
        self._log(3, "PASSED" if passed else "FAILED",
                  "; ".join(issues)[:150] if issues else "All criteria met")
        return {"passed": passed, "issues": issues, "details": qc_review}

    def manager_review(self, results_summary: str, qc_report: dict) -> str:
        """Tier II ← III: Manager reviews for edge cases and purpose."""
        self._log(2, "Reviewing results")
        review = self._ask(
            "Manager Agent (Tier II) — review for edge cases and purpose",
            f"Task: {self.task_name}\nContext: {self.context}\n"
            f"QC: {'PASSED' if qc_report.get('passed') else 'FAILED'}\n"
            f"Issues: {qc_report.get('issues', [])}\n\n"
            f"Results:\n{results_summary}\n\n"
            "Check: 1) Does output serve stated purpose? "
            "2) Edge cases that could break downstream? "
            "3) Data quality? Brief assessment (2-3 sentences).")
        self._log(2, "Review complete", review[:200])
        return review

    def director_accept(self, results_summary: str, manager_review: str,
                        test_fn=None, test_arg=None) -> dict:
        """Tier I ← II: Director performs final acceptance test.

        If *test_fn* is provided, it is called with *test_arg* (or no args)
        to run a concrete test against actual data.

        Returns ``{"accepted": bool, "issues": [...], "review": str}``.
        """
        self._log(1, "Acceptance testing")

        test_output = None
        if test_fn:
            try:
                test_output = (test_fn(test_arg) if test_arg is not None
                               else test_fn())
                self._log(1, "Test completed",
                          self._summarize(test_output)[:100])
            except (LLMBudgetExceeded, LLMExecutionError):
                raise
            except Exception as e:
                self._log(1, "Test FAILED", str(e))
                self.flagged.append(f"Director test failed: {e}")

        decision = self._ask(
            "Director Agent (Tier I) — final authority on acceptance",
            f"Task: {self.task_name}\n"
            f"Manager review: {manager_review[:300]}\n"
            f"Results: {results_summary[:400]}\n"
            + (f"Acceptance test: {self._summarize(test_output)[:200]}\n"
               if test_output else "")
            + "\nFinal decision: ACCEPT or REJECT. Brief explanation.")

        accepted = ("REJECT" not in decision.upper()) if decision else True
        if not accepted:
            self.flagged.append(f"Director rejected: {decision[:200]}")

        self._log(1, "ACCEPTED" if accepted else "REJECTED",
                  decision[:100] if decision else "")
        return {"accepted": accepted, "issues": self.flagged, "review": decision}

    # -- Full automated workflow ------------------------------------------

    def run(self, request: str, expert_fn, *,
            validator_fn=None, test_fn=None, max_retries: int = 2) -> dict:
        """Full I → II → III → IV → III → II → I orchestration.

        Args:
            request: What needs to be done (from USER or calling code)
            expert_fn: ``callable()`` → result (Tier IV work)
            validator_fn: ``callable(result)`` → ``{"passed": bool, "flagged": [...]}``
            test_fn: ``callable(result)`` → test output (Director acceptance)
            max_retries: QC retry limit before flagging (default 2)

        Returns::

            {"result": ..., "accepted": bool, "flagged": [...], "audit": [...]}
        """
        log.info(f"{'─' * 60}")
        log.info(f"TEAM: {self.task_name}")
        log.info(f"{'─' * 60}")

        # I. Director plans
        plan = self.director_plan(request)

        # II. Manager decomposes
        subtasks = self.manager_decompose(plan)

        # III. QC defines criteria
        criteria = self.qc_criteria(subtasks)

        # IV ↔ III loop: Execute + Validate (2-strike rule)
        result = None
        qc_report: dict = {"passed": False, "issues": [], "details": ""}

        for attempt in range(1, max_retries + 1):
            # IV. Expert executes
            self._log(4, f"Attempt {attempt}/{max_retries}")
            try:
                result = expert_fn()
            except (LLMBudgetExceeded, LLMExecutionError):
                raise
            except Exception as e:
                self._log(4, "FAILED", str(e))
                qc_report = {"passed": False,
                             "issues": [f"Expert error: {e}"], "details": ""}
                continue
            self._log(4, "Completed", self._summarize(result)[:100])

            # III. QC validates
            if validator_fn is None:
                qc_report = {"passed": True, "issues": [], "details": ""}
                break

            prog_check = validator_fn(result)
            summary = self._summarize(result)
            qc_report = self.qc_validate(summary, criteria, prog_check)

            if qc_report["passed"]:
                break
            if attempt < max_retries:
                self._log(3, f"Strike {attempt} — retrying")

        if not qc_report["passed"]:
            self.flagged.extend(qc_report["issues"])
            self._log(3, "FLAGGED for manual review after 2 strikes")

        # II. Manager reviews
        summary = self._summarize(result)
        mgr_review = self.manager_review(summary, qc_report)

        # I. Director acceptance test
        dir_result = self.director_accept(
            summary, mgr_review,
            test_fn=test_fn, test_arg=result)

        status = "ACCEPTED" if dir_result["accepted"] else "REJECTED"
        log.info(f"TEAM {self.task_name}: {status}")
        if self.flagged:
            for f in self.flagged:
                log.warning(f"  [FLAG] {f}")
        log.info(f"{'─' * 60}")

        return {
            "result": result,
            "accepted": dir_result["accepted"],
            "flagged": self.flagged,
            "audit": self.audit,
        }


# ---------------------------------------------------------------------------
# Book scaffold — TOC / Contents / Index as authoritative hierarchy
# ---------------------------------------------------------------------------

def _identify_book_sections(
        doc: dict, *,
        structure_profile: (
            str | _document_profiles.StructureProfile
        ) = DEFAULT_STRUCTURE_PROFILE,
) -> dict:
    """Identify contiguous front- and back-matter page ranges.

    Explicit section/page headers are authoritative. Table-layout evidence is
    used only to extend a single explicit TOC seed, or as a conservative
    fallback when no TOC heading survived extraction. An isolated body table
    must never widen the TOC across intervening chapters.
    """
    profile = _document_profiles.get_profile(structure_profile)
    texts = doc.get("texts", [])
    tables = doc.get("tables", [])
    section_pages: dict[str, list[int]] = {
        rule.key: [] for rule in profile.section_rules}

    def _normalized_heading(raw: str, label: str) -> str:
        value = re.sub(r"\s+", " ", _decode_pua(raw)).strip(" \t:.-")
        if label == "page_header":
            # Running headers may prefix an Arabic or Roman printed page.
            value = re.sub(
                r"^(?:(?:[ivxlcdm]+|\d{1,4})\s+)", "", value,
                flags=re.I,
            )
        return value

    # Running headers define section ends without guessing from table density.
    for item in texts:
        label = item.get("label", "")
        if label not in ("section_header", "page_header", "title"):
            continue
        raw = _normalized_heading(item.get("text", ""), label)
        if not raw or len(raw) > 100:
            continue
        prov = item.get("prov", [])
        page = prov[0].get("page_no") if prov else None
        if page is None:
            continue

        rule = _document_profiles.section_rule_for_heading(raw, profile)
        if rule is not None:
            section_pages[rule.key].append(page)

    # Find the first observed chapter before considering table-layout
    # evidence. This hard boundary prevents ordinary body tables from being
    # interpreted as part of the TOC.
    chapter_starts: list[int] = []
    for item in texts:
        if item.get("label") not in ("section_header", "page_header", "title"):
            continue
        raw = re.sub(
            r"\s+", " ", _decode_pua(item.get("text", ""))).strip()
        prov = item.get("prov", [])
        page = prov[0].get("page_no") if prov else None
        if (page is not None
                and _document_profiles.match_division(
                    raw, profile, "section_boundary") is not None):
            chapter_starts.append(page)

    total_pages = len(doc.get("pages", {}))
    first_chapter = min(chapter_starts) if chapter_starts else None
    front_cutoff = (
        max(1, first_chapter - 1)
        if first_chapter is not None
        else min(total_pages or 60, max(60, int((total_pages or 400) * 0.1)))
    )

    # Scan front-matter tables for TOC-like content.
    _trailing_num_re = re.compile(r"\b\d{1,4}\s*$")
    toc_table_pages: list[int] = []
    for t in tables:
        prov = t.get("prov", [])
        page = prov[0].get("page_no") if prov else None
        if page is None or page > front_cutoff:
            continue
        cells = t.get("data", {}).get("table_cells", [])
        # Count how many cells look like TOC entries
        toc_hits = 0
        for cell in cells[:40]:  # sample first 40 cells
            txt = _decode_pua(cell.get("text", "")).strip()
            if not txt or len(txt) < 3:
                continue
            if (any(re.search(pattern, txt, re.I)
                    for pattern in profile.toc_entry_hint_patterns)
                    or _trailing_num_re.search(txt)):
                toc_hits += 1
        if toc_hits >= 3:
            toc_table_pages.append(page)

    def _contiguous_runs(pages: list[int]) -> list[list[int]]:
        runs: list[list[int]] = []
        for page in sorted(set(pages)):
            if not runs or page != runs[-1][-1] + 1:
                runs.append([page])
            else:
                runs[-1].append(page)
        return runs

    table_runs = _contiguous_runs(toc_table_pages)
    toc_seed_names = _document_profiles.toc_seed_keys(profile)
    explicit_toc_pages = {
        page
        for section in toc_seed_names
        for page in section_pages[section]
    }
    if explicit_toc_pages:
        # Repeated running headers already give a complete range. A lone
        # heading may be extended only through the contiguous run containing
        # that page.
        for section in toc_seed_names:
            seeds = set(section_pages[section])
            if len(seeds) != 1:
                continue
            for run in table_runs:
                if seeds.intersection(run):
                    section_pages[section].extend(run)
                    break
    else:
        # With no heading evidence, require multiple consecutive table pages.
        # Isolated tables are too common in textbook body content.
        candidates = [run for run in table_runs if len(run) >= 2]
        if candidates:
            best = max(candidates, key=lambda run: (len(run), -run[0]))
            fallback_toc_name = toc_seed_names[0]
            section_pages[fallback_toc_name].extend(best)

    # 4. Fallback: if no TOC/contents detected yet, scan plain "text" items
    #    for TOC keywords.  Some scanned PDFs produce all items as "text"
    #    (e.g., Criminal Law) with no section_header labels.
    has_toc = any(section_pages[key] for key in toc_seed_names)
    if not has_toc:
        for item in texts:
            if item.get("label") != "text":
                continue
            raw = _decode_pua(item.get("text", "")).strip()
            if not raw or len(raw) > 60:
                continue
            # Normalize whitespace for OCR artifacts ("SUMMARY  OF CONTENTS")
            norm = re.sub(r"\s+", " ", raw).strip()
            prov = item.get("prov", [])
            page = prov[0].get("page_no") if prov else None
            if page is None:
                continue
            rule = _document_profiles.section_rule_for_heading(norm, profile)
            if rule is not None and rule.toc_seed:
                section_pages[rule.key].append(page)

    # Apply profile-declared positional constraints to back matter.  This
    # prevents front-matter mentions from becoming publication exclusions.
    total_pages = len(doc.get("pages", {})) or 100
    for rule in profile.section_rules:
        if rule.minimum_page_fraction is None:
            continue
        earliest = int(total_pages * rule.minimum_page_fraction)
        section_pages[rule.key] = [
            page for page in section_pages[rule.key] if page >= earliest]

    # Build ranges
    result = {}
    for sec_type, pages in section_pages.items():
        if pages:
            result[sec_type] = {"start": min(pages), "end": max(pages)}
        else:
            result[sec_type] = None

    # Merge toc/contents/summary into a usable scaffold span:
    # - "summary" is the brief Summary of Contents (e.g., ConLaw p.9)
    # - "contents" is the detailed Contents listing
    # - "toc" is the "Table of Contents" header
    # If only one exists, alias the others.
    all_toc_types = [key for key in toc_seed_names if result.get(key)]
    if all_toc_types:
        # ``toc`` is the canonical full scaffold span. Keep the distinct
        # summary and detailed-contents ranges for precise filtering.
        widest_start = min(result[k]["start"] for k in all_toc_types)
        widest_end = max(result[k]["end"] for k in all_toc_types)
        result["toc"] = {"start": widest_start, "end": widest_end}
        if not result.get("contents"):
            result["contents"] = {"start": widest_start, "end": widest_end}

    found = [f"{k}: pp.{v['start']}-{v['end']}"
             for k, v in result.items() if v]
    if found:
        log.info(f"Book sections identified: {', '.join(found)}")
    else:
        log.warning("No Contents/TOC/Index sections found in document")

    return result


def _calculate_page_delta(doc: dict) -> int:
    """Calculate the delta between printed page numbers and PDF page numbers.

    Law school textbooks have front matter (roman numerals) followed by
    body text (arabic numerals starting at 1).  The delta is the constant
    offset such that:  printed_page + delta = pdf_page_no.

    Strategy: find page_footer items that contain a bare arabic number
    and correlate with their prov[0].page_no.  Take the median delta
    to be robust against footnote numbers or other noise.

    Returns 0 if the delta cannot be determined.
    """
    texts = doc.get("texts", [])
    _bare_num = re.compile(r"^\d{1,4}$")
    deltas: list[int] = []

    # Check page_footer items first (most books put page numbers here)
    for item in texts:
        if item.get("label") != "page_footer":
            continue
        raw = _decode_pua(item.get("text", "")).strip()
        if not _bare_num.match(raw):
            continue
        prov = item.get("prov", [])
        pdf_page = prov[0].get("page_no") if prov else None
        if pdf_page is None:
            continue
        printed = int(raw)
        if printed < 1 or printed > 5000:
            continue
        deltas.append(pdf_page - printed)

    # Fallback: check page_header items
    if len(deltas) < 3:
        for item in texts:
            if item.get("label") != "page_header":
                continue
            raw = _decode_pua(item.get("text", "")).strip()
            if not _bare_num.match(raw):
                continue
            prov = item.get("prov", [])
            pdf_page = prov[0].get("page_no") if prov else None
            if pdf_page is None:
                continue
            printed = int(raw)
            if printed < 1 or printed > 5000:
                continue
            deltas.append(pdf_page - printed)

    if not deltas:
        log.warning("Cannot determine page delta — no numeric page footers found")
        return 0

    # Take the most common delta (mode) — robust against outliers
    from collections import Counter
    delta_counts = Counter(deltas)
    delta = delta_counts.most_common(1)[0][0]

    # Sanity check: verify consistency
    most_common_count = delta_counts.most_common(1)[0][1]
    consistency = most_common_count / len(deltas) if deltas else 0
    log.info(f"Page delta: printed + {delta} = PDF page "
             f"({most_common_count}/{len(deltas)} samples, "
             f"{consistency:.0%} consistent)")

    if consistency < 0.6:
        log.warning(f"Page delta inconsistent — deltas: {delta_counts.most_common(5)}")

    return delta


_TOC_LAYOUT_PROMPT = """You are analyzing a Table of Contents from this reviewed document family:
{profile_description}
Your job is to identify the LAYOUT PATTERNS used to organize entries on these pages.
Study the text carefully and answer:

1. PAGE NUMBERS: Where do page numbers appear? (e.g., "right-aligned at end of line",
   "trailing after dots/leaders", "in a separate column"). What format are they in?
   (plain digits, Roman numerals, etc.)

2. PRIMARY DIVISION DESIGNATION: How are top-level divisions identified? What is the
   exact pattern? List the designations you see without inventing absent levels.

3. SECTION MARKERS: How are major sections within chapters marked?
   (e.g., "A.", "B.", "I.", "II.", bold text, indented). List examples.

4. SUBSECTION MARKERS: How are sub-sections marked?
   (e.g., "1.", "2.", "a.", "b.", further indentation). List examples.

5. NAMED ITEMS: How are the narrowest named items formatted and nested? List examples.

6. OTHER ELEMENTS: Any other notable elements (e.g., "Notes and Questions",
   "Problems", part/unit groupings, appendices).

7. HIERARCHY SUMMARY: Describe the complete nesting order from broadest to narrowest.

Output ONLY valid JSON:
{{
    "page_number_format": "description",
    "division_pattern": "regex-friendly pattern description",
    "division_examples": ["first observed primary division", "second observed primary division"],
    "section_markers": ["A.", "B.", "I.", "II."],
    "subsection_markers": ["1.", "2.", "a.", "b."],
    "named_item_format": "description",
    "other_elements": ["Notes and Questions", "Problems"],
    "hierarchy_order": ["Primary division", "Section", "Subsection", "Named item"],
    "hierarchy_levels": {{
        "1": "description of what level 1 represents",
        "2": "description of what level 2 represents",
        "3": "description of what level 3 represents",
        "4": "description of what level 4 represents",
        "5": "description of what level 5 represents"
    }}
}}

Table of Contents text:
{toc_text}

JSON:"""


def _analyze_toc_layout(
        toc_text: str, *,
        structure_profile: (
            str | _document_profiles.StructureProfile
        ) = DEFAULT_STRUCTURE_PROFILE,
        **llm_kwargs,
) -> dict:
    """Ask LLM to identify the organizational patterns in the TOC.

    Returns a layout schema describing how chapters, sections, cases, and
    page numbers are designated in this specific book.
    """
    # Send a representative sample (first ~120 lines covers most patterns)
    lines = toc_text.split("\n")
    sample = "\n".join(lines[:120])

    profile = _document_profiles.get_profile(structure_profile)
    prompt = _TOC_LAYOUT_PROMPT.format(
        toc_text=sample, profile_description=profile.document_description)
    result = _call_llm(
        prompt, max_tokens=1200, operation="toc.layout", **llm_kwargs)
    if not result:
        log.warning("TOC layout analysis: LLM returned no result")
        return {}

    # Remove <think> tags
    result = re.sub(r"<think>.*?</think>", "", result, flags=re.DOTALL)

    start = result.find("{")
    end = result.rfind("}")
    if start == -1 or end == -1:
        log.warning("TOC layout analysis: no JSON in LLM response")
        return {}
    try:
        schema = json.loads(result[start:end + 1])
        log.info(f"TOC layout schema: hierarchy = "
                 f"{' > '.join(schema.get('hierarchy_order', []))}")
        if schema.get("division_examples"):
            log.info(
                f"  Division examples: {schema['division_examples'][:3]}")
        if schema.get("section_markers"):
            log.info(f"  Section markers: {schema['section_markers'][:6]}")
        return schema
    except (json.JSONDecodeError, ValueError):
        log.warning("TOC layout analysis: failed to parse JSON")
        return {}


_VERIFY_PROMPT = """You are verifying that a Table of Contents entry matches the actual page content.

TOC says this page should contain:
  Title: {title}
  Level: {level_desc}
  Chapter: {chapter_info}
  Expected section path: {path}

Here is the actual text found on page {page}:
---
{page_text}
---

Does this page contain the element described in the TOC entry?
Look for: the title text (or close variant), section markers, chapter headers,
case names, or any text that confirms this TOC entry corresponds to this page.

Output ONLY valid JSON:
{{
    "verified": true/false,
    "confidence": 0.0-1.0,
    "found_title": "the matching text found on the page (or empty)",
    "issue": "description of mismatch if not verified (or empty)"
}}

JSON:"""


def _verify_scaffold_against_pages(
    scaffold: list[dict],
    doc: dict,
    max_checks: int = 15,
    **llm_kwargs,
) -> dict:
    """Spot-check scaffold entries by reading the actual pages they reference.

    Selects a representative sample of scaffold entries (favoring level-1
    chapter boundaries and spread across the book), fetches the text on
    those pages from the DoclingDocument, and asks the LLM to confirm
    the TOC entry matches what's actually on the page.

    Returns::
        {
            "checks": int,
            "verified": int,
            "failed": [{"title": ..., "page": ..., "issue": ...}],
            "confidence": float,  # fraction verified
        }
    """
    if not scaffold:
        return {"checks": 0, "verified": 0, "failed": [], "confidence": 0.0}

    texts = doc.get("texts", [])

    # Build page → text content lookup (first 1500 chars per page)
    page_text: dict[int, str] = {}
    for item in texts:
        label = item.get("label", "")
        if label in ("page_header", "page_footer"):
            continue
        prov = item.get("prov", [])
        pg = prov[0].get("page_no") if prov else None
        if pg is None:
            continue
        raw = _decode_pua(item.get("text", "")).strip()
        if raw:
            page_text.setdefault(pg, "")
            if len(page_text[pg]) < 1500:
                page_text[pg] += raw + "\n"

    # Select entries to verify: all level-1 (chapters) + a spread of deeper entries
    level1 = [e for e in scaffold if e.get("level") == 1 and e.get("page", 0) > 0]
    deeper = [e for e in scaffold if e.get("level", 0) > 1 and e.get("page", 0) > 0]

    # Take all chapter boundaries + sample deeper entries evenly
    to_check = list(level1)
    remaining = max_checks - len(to_check)
    if remaining > 0 and deeper:
        step = max(1, len(deeper) // remaining)
        to_check.extend(deeper[::step][:remaining])

    to_check = to_check[:max_checks]
    if not to_check:
        log.warning("Scaffold verification: no entries with page numbers to check")
        return {"checks": 0, "verified": 0, "failed": [], "confidence": 0.0}

    level_descs = {
        1: "Chapter / Part",
        2: "Major section (A., B., I., II.)",
        3: "Subsection (1., 2.)",
        4: "Sub-subsection (a., b.)",
        5: "Case name / Notes and Questions",
    }

    verified_count = 0
    failed = []

    for entry in to_check:
        pg = entry["page"]
        pt = page_text.get(pg, "")
        if not pt:
            # No text on this page — might be a blank page or image-only
            failed.append({
                "title": entry["title"], "page": pg,
                "issue": "No text content found on this page",
            })
            continue

        # Quick regex check first (avoid LLM call for obvious matches)
        title_words = re.sub(r"[^\w\s]", "", entry["title"]).strip()
        if title_words and len(title_words) > 3:
            # Check if key words appear on the page
            key_words = [w for w in title_words.split() if len(w) > 3][:4]
            if key_words and all(
                re.search(re.escape(w), pt, re.I) for w in key_words
            ):
                verified_count += 1
                continue

        # LLM verification for non-obvious matches
        prompt = _VERIFY_PROMPT.format(
            title=entry["title"],
            level_desc=level_descs.get(entry.get("level", 1), "Unknown"),
            chapter_info=f"Chapter {entry.get('chapter_num', '?')}",
            path=entry.get("path", entry["title"]),
            page=pg,
            page_text=pt[:1200],
        )
        result = _call_llm(
            prompt, max_tokens=300, operation="toc.verify", **llm_kwargs)
        if not result:
            # LLM unavailable — skip but don't count as failure
            continue

        result = re.sub(r"<think>.*?</think>", "", result, flags=re.DOTALL)
        start = result.find("{")
        end = result.rfind("}")
        if start == -1 or end == -1:
            continue
        try:
            vr = json.loads(result[start:end + 1])
            if vr.get("verified"):
                verified_count += 1
            else:
                failed.append({
                    "title": entry["title"],
                    "page": pg,
                    "issue": vr.get("issue", "LLM could not verify"),
                    "found": vr.get("found_title", ""),
                })
        except (json.JSONDecodeError, ValueError):
            continue

    checks = len(to_check)
    confidence = verified_count / checks if checks else 0.0

    if failed:
        log.warning(f"Scaffold verification: {verified_count}/{checks} verified, "
                    f"{len(failed)} failed:")
        for f in failed[:5]:
            log.warning(f"  p.{f['page']} '{f['title']}': {f['issue']}")
    else:
        log.info(f"Scaffold verification: {verified_count}/{checks} entries "
                 f"verified against actual pages ({confidence:.0%} confidence)")

    return {
        "checks": checks,
        "verified": verified_count,
        "failed": failed,
        "confidence": confidence,
    }


def _build_scaffold(doc: dict, book_sections: dict, *,
                    structure_profile: (
                        str | _document_profiles.StructureProfile
                    ) = DEFAULT_STRUCTURE_PROFILE,
                    cloud_url: str = "", cloud_model: str = "",
                    cloud_key: str = "", ollama_url: str = "",
                    ollama_model: str = "", gemini_key: str = "",
                    llm_workers: int = DEFAULT_LLM_WORKERS,
                    thinking: bool = False,
                    use_llm: bool = False,
                    security_policy: (
                        _release_security.ReleaseSecurityPolicy | None
                    ) = None,
                    ) -> list[dict]:
    """Build an authoritative book scaffold from TOC/Contents text items.

    By default this uses deterministic table/column parsing and makes no LLM
    calls. With ``use_llm=True``, layout analysis and hierarchy parsing are
    added, table-derived page numbers are merged, and an ``_AgentTeam`` reviews
    the result against representative source pages.

    Returns sorted hierarchy entries with level, title, page, chapter number,
    and full hierarchical path.
    """
    profile = _document_profiles.get_profile(structure_profile)
    llm_kwargs = dict(cloud_url=cloud_url, cloud_model=cloud_model,
                      cloud_key=cloud_key, ollama_url=ollama_url,
                      ollama_model=ollama_model, gemini_key=gemini_key,
                      llm_workers=llm_workers, thinking=thinking,
                      security_policy=security_policy)

    texts = doc.get("texts", [])
    tables = doc.get("tables", [])

    def _raise_profile_layout_error(reason: str) -> None:
        log.warning(reason)
        raise ValueError(
            f"TOC layout does not match structure profile "
            f"{profile.name!r}; choose the reviewed profile for this "
            "publisher before chunking")

    # Determine TOC page range (use the widest available)
    toc_range = book_sections.get("toc") or book_sections.get("contents")
    contents_range = book_sections.get("contents")
    if not toc_range:
        _raise_profile_layout_error("No TOC page range was recognized")

    # Use the widest range across toc and contents
    toc_start = toc_range["start"]
    toc_end = toc_range["end"]
    if contents_range:
        toc_start = min(toc_start, contents_range["start"])
        toc_end = max(toc_end, contents_range["end"])

    # --- Calculate printed-to-PDF page delta ---
    page_delta = _calculate_page_delta(doc)

    # --- Collect TOC content from TABLES (primary source) ---
    # All three test books store TOC entries in table_cells.  Entries look
    # like "Chapter 1 The Concept of Property ....1" or "A. First Possession  5"
    # with the printed page number at the end.
    toc_lines: list[str] = []
    def _is_toc_header(value: str) -> bool:
        return any(
            re.fullmatch(pattern, value, re.I)
            for pattern in profile.toc_skip_patterns)

    for t in tables:
        prov = t.get("prov", [])
        tbl_page = prov[0].get("page_no") if prov else None
        if tbl_page is None or tbl_page < toc_start or tbl_page > toc_end:
            continue
        cells = t.get("data", {}).get("table_cells", [])
        for cell in cells:
            raw = _decode_pua(cell.get("text", "")).strip()
            if not raw or len(raw) < 3:
                continue
            if _is_toc_header(raw):
                continue
            toc_lines.append(raw)

    # Also collect text items (some books mix text + table items in TOC)
    for item in texts:
        label = item.get("label", "")
        if label in ("page_header", "page_footer"):
            continue
        prov = item.get("prov", [])
        page = prov[0].get("page_no") if prov else None
        if page is None or page < toc_start or page > toc_end:
            continue
        raw = _decode_pua(item.get("text", "")).strip()
        if not raw or len(raw) < 3:
            continue
        if _is_toc_header(raw):
            continue
        # Avoid duplicates (table cells may repeat text items)
        if raw not in toc_lines:
            toc_lines.append(raw)

    if not toc_lines:
        _raise_profile_layout_error(
            "No TOC entries were found in tables or text items")

    toc_text = "\n".join(toc_lines)
    log.info(f"TOC: {len(toc_lines)} entries from pp.{toc_start}-{toc_end} "
             f"(page delta: +{page_delta})")

    # Pin profile evidence to deterministic source parsing. LLM output may
    # enrich hierarchy, but it cannot invent a profile-conforming division
    # that is absent from the source TOC.
    table_scaffold = _parse_toc_tables(
        doc, toc_start, toc_end, structure_profile=profile)

    def _division_key(title: object) -> tuple[str, int] | None:
        division = _document_profiles.match_division(
            str(title), profile, "toc_entry")
        if division is None:
            return None
        return division.kind.casefold(), division.ordinal

    table_primary = [
        entry for entry in table_scaffold if entry.get("level") == 1
    ]
    source_primary: dict[tuple[str, int], dict] = {}
    for value in table_primary or toc_lines:
        title = value if isinstance(value, str) else value.get("title", "")
        key = _division_key(title)
        if key is None:
            continue
        source_title = _chunking_core.clean_heading_text(str(title))
        if isinstance(value, str):
            division = _document_profiles.match_division(
                source_title, profile, "toc_entry")
            if division is not None and division.title:
                source_title = re.sub(
                    r"(?:\s*[.\u2026·]){2,}\s*\d{1,4}\s*$", "",
                    source_title).strip()
                source_title = re.sub(
                    r"\s+\d{1,4}\s*$", "", source_title).strip()
        source_primary.setdefault(key, {
            "title": source_title,
            "page": (
                value.get("page", 0) if isinstance(value, dict) else 0),
        })
    source_division_keys = set(source_primary)

    def _division_keys(values) -> set[tuple[str, int]]:
        return {
            key for value in values
            if (key := _division_key(
                value if isinstance(value, str)
                else value.get("title", ""))) is not None
        }

    def _expert_build_scaffold():
        """Expert (IV): Layout analysis → hierarchy parsing → delta conversion."""
        # Phase 1: Layout analysis
        layout = (
            _analyze_toc_layout(
                toc_text, structure_profile=profile, **llm_kwargs)
            if use_llm else {}
        )

        # Phase 2: Hierarchy parsing (LLM extracts printed page numbers)
        s = []
        if use_llm and (cloud_key or gemini_key or ollama_url):
            s = _llm_parse_scaffold(
                toc_text, layout_schema=layout,
                structure_profile=profile, **llm_kwargs)

        # Table-based fallback/merge
        ts = [dict(entry) for entry in table_scaffold]
        if ts and not s:
            s = ts
        elif ts and s:
            _merge_page_numbers(s, ts)

        # Primary display titles and pages remain deterministic source facts.
        # The LLM may enrich only the subordinate hierarchy around them.
        for entry in s:
            if entry.get("level") != 1:
                continue
            source = source_primary.get(_division_key(entry.get("title", "")))
            if source is None:
                continue
            entry["title"] = source["title"]
            if source["page"] > 0:
                entry["page"] = source["page"]

        # Phase 3: Convert printed page numbers → PDF page numbers
        # TOC entries contain printed page numbers (e.g., "Chapter 1...1")
        # but we need PDF page_no for the scaffold lookup.
        if page_delta != 0:
            converted = 0
            for e in s:
                pg = e.get("page", 0)
                if pg > 0:
                    e["printed_page"] = pg  # preserve original
                    e["page"] = pg + page_delta  # convert to PDF page
                    converted += 1
            log.info(f"Page delta applied: {converted} entries converted "
                     f"(printed + {page_delta} = PDF page)")

        # Assign chapter numbers
        cur_ch = None
        for e in s:
            if e["level"] == 1:
                division = _document_profiles.match_division(
                    e["title"], profile, "toc_entry")
                if division is not None:
                    cur_ch = division.ordinal
                    e["division_kind"] = division.kind
                    e["division_number"] = division.raw_number
            e["chapter_num"] = cur_ch

        # Build hierarchical paths
        stack: dict[int, str] = {}
        for e in s:
            for k in list(stack):
                if k >= e["level"]:
                    del stack[k]
            stack[e["level"]] = e["title"]
            e["path"] = " > ".join(
                stack[level] for level in sorted(stack))

        if s:
            chs = len(set(x["chapter_num"] for x in s if x["chapter_num"] is not None))
            log.info(f"Expert: {len(s)} entries, {chs} chapters, "
                     f"levels 1-{max(x['level'] for x in s)}")
        return s

    def _validator(scaffold):
        """QC (III): Verify scaffold against actual pages + programmatic checks."""
        if not scaffold:
            return {"passed": False, "flagged": ["Empty scaffold — no entries parsed"]}

        verification = _verify_scaffold_against_pages(
            scaffold, doc, max_checks=15, **llm_kwargs)

        chapters = set(e.get("chapter_num") for e in scaffold
                       if e.get("chapter_num") is not None)
        pages_with_num = sum(1 for e in scaffold if e.get("page", 0) > 0)

        issues = []
        if len(chapters) == 0:
            issues.append("No chapters detected in scaffold")
        if pages_with_num < len(scaffold) * 0.5:
            issues.append(f"Only {pages_with_num}/{len(scaffold)} entries have page numbers")
        if verification["confidence"] < 0.5 and verification["checks"] >= 3:
            issues.append(f"Low page verification: {verification['confidence']:.0%}")
            for f in verification.get("failed", [])[:3]:
                issues.append(f"  p.{f['page']} '{f['title']}': {f['issue']}")

        return {"passed": len(issues) == 0, "flagged": issues}

    def _director_test(scaffold):
        """Director (I): Acceptance test — additional spot-checks on different pages."""
        if not scaffold:
            return {"checks": 0, "verified": 0, "confidence": 0.0, "failed": []}
        return _verify_scaffold_against_pages(
            scaffold, doc, max_checks=5, **llm_kwargs)

    def _require_profile_divisions(scaffold: list[dict]) -> None:
        """Fail closed when a TOC does not match the selected profile."""
        primary = [entry for entry in scaffold if entry.get("level") == 1]
        generated_keys = _division_keys(primary)
        if (not primary or len(generated_keys) != len(primary)
                or not source_division_keys
                or generated_keys != source_division_keys):
            _raise_profile_layout_error(
                "The generated scaffold does not exactly match deterministic "
                "source evidence for the primary divisions")

    if not use_llm:
        log.info("Scaffold: deterministic TOC parsing (LLM review disabled)")
        deterministic = _expert_build_scaffold()
        _require_profile_divisions(deterministic)
        return deterministic

    # ── Optional LLM team review ──
    team = _AgentTeam(
        "Scaffold Construction",
        context=f"TOC pp.{toc_start}-{toc_end}, {len(toc_lines)} lines",
        **llm_kwargs,
    )

    outcome = team.run(
        request="Build authoritative book hierarchy from Table of Contents",
        expert_fn=_expert_build_scaffold,
        validator_fn=_validator,
        test_fn=_director_test,
    )

    scaffold = outcome["result"] or []
    _require_profile_divisions(scaffold)

    # Write flagged issues if any
    if outcome["flagged"]:
        log.warning(f"Scaffold team flagged {len(outcome['flagged'])} issues")

    return scaffold


def _llm_parse_scaffold(
        toc_text: str, *, layout_schema: dict = None,
        structure_profile: (
            str | _document_profiles.StructureProfile
        ) = DEFAULT_STRUCTURE_PROFILE,
        **llm_kwargs,
) -> list[dict]:
    """Send TOC text to LLM for authoritative hierarchy parsing.

    If *layout_schema* is provided (from ``_analyze_toc_layout``), it is
    injected into the prompt so the LLM knows exactly how this book designates
    chapters, sections, cases, and page numbers.
    """
    # Build a layout hint block from the schema
    profile = _document_profiles.get_profile(structure_profile)
    layout_hint = ""
    if layout_schema:
        parts = []
        if layout_schema.get("hierarchy_order"):
            parts.append("Hierarchy (broadest → narrowest): "
                         + " > ".join(layout_schema["hierarchy_order"]))
        if layout_schema.get("division_pattern"):
            parts.append(
                f"Primary-division designation: "
                f"{layout_schema['division_pattern']}")
        if layout_schema.get("division_examples"):
            parts.append("Division examples: " +
                         ", ".join(layout_schema["division_examples"][:4]))
        if layout_schema.get("section_markers"):
            parts.append("Section markers: " +
                         ", ".join(layout_schema["section_markers"][:6]))
        if layout_schema.get("subsection_markers"):
            parts.append("Subsection markers: " +
                         ", ".join(layout_schema["subsection_markers"][:6]))
        if layout_schema.get("named_item_format"):
            parts.append(
                f"Named items: {layout_schema['named_item_format']}")
        if layout_schema.get("page_number_format"):
            parts.append(f"Page numbers: {layout_schema['page_number_format']}")
        hl = layout_schema.get("hierarchy_levels", {})
        if hl:
            for lvl in sorted(hl.keys()):
                parts.append(f"  Level {lvl}: {hl[lvl]}")
        if parts:
            layout_hint = ("\n\nBOOK-SPECIFIC LAYOUT (use this to assign levels "
                           "correctly):\n" + "\n".join(parts) + "\n")

    hierarchy_guidance = [
        "Level 1: a primary division matching the selected reviewed profile.",
        *[
            f"Level {rule.level}: a title matching /{rule.pattern}/."
            for rule in profile.hierarchy_rules
        ],
    ]
    scaffold_prompt = (
        "You are parsing a Table of Contents from this reviewed document "
        f"family: {profile.document_description}.\n"
        "Convert it into a structured hierarchy. Assign levels only from "
        "the following deterministic policy:\n\n"
        + "\n".join(hierarchy_guidance) + "\n"
        + layout_hint +
        "\nExtract the page number from each line (usually the last number on the line).\n\n"
        "Output ONLY a JSON array. Each entry: {{\"level\": N, \"title\": \"...\", \"page\": N}}\n"
        "No explanation, no markdown, ONLY the JSON array.\n\n"
        "Table of Contents:\n{toc_text}\n\n"
        "JSON array:"
    )

    # Split into ~80-line batches to fit context
    lines = toc_text.split("\n")
    batches = []
    for i in range(0, len(lines), 80):
        batch = "\n".join(lines[i:i + 80])
        if batch.strip():
            batches.append(batch)

    all_entries = []
    for batch_text in batches:
        prompt = scaffold_prompt.format(toc_text=batch_text)
        try:
            result = _call_llm(
                prompt, max_tokens=4096, operation="toc.scaffold", **{
                k: v for k, v in llm_kwargs.items()
                if k in ("cloud_url", "cloud_model", "cloud_key",
                          "ollama_url", "ollama_model", "gemini_key",
                          "llm_workers", "thinking", "security_policy")})
        except (LLMBudgetExceeded, LLMExecutionError):
            raise
        except Exception:
            continue

        if not result:
            continue

        # Remove <think> tags
        result = re.sub(r"<think>.*?</think>", "", result, flags=re.DOTALL)

        # Extract JSON array
        start = result.find("[")
        end = result.rfind("]")
        if start == -1 or end == -1:
            continue
        try:
            entries = json.loads(result[start:end + 1])
            for e in entries:
                if isinstance(e, dict) and "level" in e and "title" in e:
                    entry = {
                        "level": int(e["level"]),
                        "title": str(e["title"]).strip(),
                        "page": int(e.get("page", 0)) if e.get("page") else 0,
                    }
                    if entry["title"] and 1 <= entry["level"] <= 5:
                        all_entries.append(entry)
        except (json.JSONDecodeError, ValueError, TypeError):
            continue

    if all_entries:
        log.info(f"LLM scaffold: {len(all_entries)} entries parsed from TOC")
    return all_entries


def _toc_visual_subrows(cells: list[dict]) -> list[list[dict]]:
    """Split unreliable declared table rows using vertical cell geometry.

    Docling occasionally assigns the last line on a page and the next chapter
    heading to one logical row. Cells in a real visual row overlap vertically;
    disjoint bands are therefore safer than the declared row index alone.
    If geometry is missing, preserve the declared row unchanged.
    """
    declared: dict[int, list[dict]] = {}
    for cell in cells:
        declared.setdefault(cell.get("start_row_offset_idx", 0), []).append(cell)

    output: list[list[dict]] = []
    for row_idx in sorted(declared):
        row_cells = declared[row_idx]
        if len(row_cells) < 2 or any(
                not isinstance(cell.get("bbox"), dict)
                or cell["bbox"].get("t") is None
                or cell["bbox"].get("b") is None
                for cell in row_cells):
            output.append(row_cells)
            continue

        bands: list[dict] = []
        positioned = []
        for cell in row_cells:
            bbox = cell["bbox"]
            low = min(float(bbox["t"]), float(bbox["b"]))
            high = max(float(bbox["t"]), float(bbox["b"]))
            positioned.append(((low + high) / 2, low, high, cell))

        for center, low, high, cell in sorted(positioned, key=lambda x: x[0]):
            overlapping = [
                band for band in bands
                if low <= band["high"] + 2.0 and high >= band["low"] - 2.0
            ]
            if overlapping:
                band = min(overlapping, key=lambda value: abs(
                    center - value["center"]))
                band["cells"].append(cell)
                band["low"] = min(band["low"], low)
                band["high"] = max(band["high"], high)
                band["center"] = sum(
                    (min(float(item["bbox"]["t"]), float(item["bbox"]["b"]))
                     + max(float(item["bbox"]["t"]), float(item["bbox"]["b"])))
                    / 2 for item in band["cells"]
                ) / len(band["cells"])
            else:
                bands.append({
                    "center": center, "low": low, "high": high,
                    "cells": [cell],
                })

        for band in sorted(bands, key=lambda value: value["center"]):
            output.append(band["cells"])
    return output


def _parse_toc_tables(
        doc: dict, toc_start: int, toc_end: int, *,
        structure_profile: (
            str | _document_profiles.StructureProfile
        ) = DEFAULT_STRUCTURE_PROFILE,
) -> list[dict]:
    """Parse TOC from DoclingDocument tables via row reconstruction.

    Handles three live textbook formats:

    1. **CivPro**: title spans cols 0-2 (or 0-3 for chapters), page in last col.
    2. **Property I**: chapter name col 0, subtitle+dots+page in col 2/3.
    3. **ConLaw**: 2-column — ``CHAPTER N`` on its own row (no page),
       title + page on the next row.

    Strategy: reconstruct full rows from cells, then extract title + page
    from each row as a unit.  This avoids the old single-cell / multi-column
    split that produced wrong results when cells partially matched.
    """
    profile = _document_profiles.get_profile(structure_profile)
    tables = doc.get("tables", [])
    if not tables:
        return []

    # Regex helpers
    _page_arabic = re.compile(r"[.\u2026·\s]{2,}\s*(\d{1,4})\s*$")
    _bare_number = re.compile(r"^\s*(\d{1,4})\s*$")
    _trailing_roman = re.compile(
        r"[.\u2026·\s]{2,}\s*((?:x{0,3}(?:ix|iv|v?i{0,3})|"
        r"(?:l?x{0,3})(?:ix|iv|v?i{0,3})))\s*$", re.I)
    _leader_run = re.compile(r"(?:\s*[.\u2026·]\s*){2,}")

    def _skip_row(value: str) -> bool:
        return any(
            re.fullmatch(pattern, value, re.I)
            for pattern in profile.toc_skip_patterns)

    entries: list[dict] = []
    pending_chapter: str | None = None  # ConLaw: CHAPTER row without page

    for t in tables:
        prov = t.get("prov", [])
        page = prov[0].get("page_no", 999) if prov else 999
        if page < toc_start or page > toc_end:
            continue

        cells = t.get("data", {}).get("table_cells", [])
        if not cells:
            continue

        # --- Step 1: reconstruct visual rows from cells ----------------
        visual_rows = _toc_visual_subrows(cells)
        row_queue = list(visual_rows)

        # --- Step 2: parse each visual row ------------------------------
        while row_queue:
            row_cells = row_queue.pop(0)
            row: dict[int, str] = {}
            spans: dict[int, tuple[int, int]] = {}
            for cell in sorted(
                    row_cells,
                    key=lambda value: value.get("start_col_offset_idx", 0)):
                col_start = cell.get("start_col_offset_idx", 0)
                col_end = cell.get("end_col_offset_idx", col_start + 1)
                text = _decode_pua(cell.get("text", "")).strip()
                if not text:
                    continue
                row[col_start] = " ".join(
                    part for part in (row.get(col_start, ""), text) if part)
                old_span = spans.get(col_start, (col_start, col_end))
                spans[col_start] = (
                    min(old_span[0], col_start), max(old_span[1], col_end))
            if not row:
                continue
            max_col = max(row.keys()) if row else 0

            # Some merged cells contain two visual lines and the page column
            # contains the corresponding two numbers. Recover those pairs
            # before applying the ordinary single-row parser.
            if max_col > 0:
                page_values = re.findall(r"\b\d{1,4}\b", row[max_col])
                joined_title = " ".join(
                    row[col] for col in sorted(row) if col != max_col)
                title_segments = [
                    segment.strip()
                    for segment in _leader_run.split(joined_title)
                    if segment.strip()
                ]
                if (len(page_values) > 1
                        and len(title_segments) == len(page_values)):
                    synthetic_rows = []
                    for segment, value in zip(title_segments, page_values):
                        synthetic_rows.append([
                            {
                                "text": segment,
                                "start_col_offset_idx": 0,
                                "end_col_offset_idx": max_col,
                            },
                            {
                                "text": value,
                                "start_col_offset_idx": max_col,
                                "end_col_offset_idx": max_col + 1,
                            },
                        ])
                    row_queue[0:0] = synthetic_rows
                    continue

            # Collect all text from this row
            all_texts = [row[c] for c in sorted(row.keys())]
            full_text = " ".join(all_texts).strip()

            # Skip header/label rows
            if _skip_row(full_text):
                continue
            if len(full_text) < 3:
                continue

            title: str | None = None
            page_num: int | None = None

            # --- Strategy A: page number in last (highest) column ------
            last_val = row.get(max_col, "")
            m_bare = _bare_number.match(last_val)
            if m_bare and max_col > 0:
                page_num = int(m_bare.group(1))
                # Title = all cells except the page-number cell, joined
                title_parts = [row[c] for c in sorted(row.keys())
                               if c != max_col]
                title = " ".join(title_parts).strip()
                # Clean trailing dots/separators from title
                title = re.sub(r"[\s.·\u2026]+$", "", title)
            else:
                # --- Strategy B: page embedded in dot leaders ----------
                # Check last cell first (Property I puts dots+page there)
                m_dots = _page_arabic.search(last_val)
                if m_dots:
                    page_num = int(m_dots.group(1))
                    # Title = everything before the dots+page
                    cleaned_last = _page_arabic.sub("", last_val).strip()
                    cleaned_last = re.sub(r"[\s.·\u2026]+$", "",
                                          cleaned_last)
                    title_parts = [row[c] for c in sorted(row.keys())
                                   if c != max_col]
                    if cleaned_last:
                        title_parts.append(cleaned_last)
                    title = " ".join(title_parts).strip()
                else:
                    # Check full row text for embedded page
                    m_full = _page_arabic.search(full_text)
                    if m_full:
                        page_num = int(m_full.group(1))
                        title = _page_arabic.sub("", full_text).strip()
                        title = re.sub(r"[\s.·\u2026]+$", "", title)

            # --- Strategy C: roman numeral page (front matter) ---------
            if title is None:
                m_rom = _trailing_roman.search(full_text)
                if m_rom:
                    title = _trailing_roman.sub("", full_text).strip()
                    title = re.sub(r"[\s.·\u2026]+$", "", title)
                    page_num = 0  # front matter

            # --- Handle ConLaw split-chapter rows ----------------------
            # ConLaw pattern: "CHAPTER N" alone in col 0, title on next row.
            # CivPro pattern: "Chapter N · Full Title" spanning all columns.
            # Distinguish by checking if col-0 cell spans most columns
            # (full-width = self-contained header, narrow = split label).
            row_text_bare = row.get(0, "")
            row_division = _document_profiles.match_division(
                row_text_bare, profile, "toc_entry")
            if row_division is not None:
                col0_span = spans.get(0, (0, 1))
                col0_width = col0_span[1] - col0_span[0]
                # Two split-chapter scenarios:
                # 1. ConLaw: narrow label (≤2 cols, ≤2 cells), title on
                #    next row.  Page number in this row is stray.
                # 2. CivPro Ch13: wide span but title wraps to next row
                #    (no page extracted from this row at all).
                is_narrow_label = (col0_width <= 2
                                   and len(row_text_bare) < 25
                                   and len(row) <= 2)
                no_page_in_row = (page_num is None or page_num == 0)
                if is_narrow_label or (no_page_in_row and len(row) == 1):
                    pending_chapter = row_text_bare.strip()
                    continue
            # If previous row was a pending chapter label, prepend it
            if pending_chapter:
                if title and page_num and page_num > 0:
                    title = f"{pending_chapter} \u2014 {title}"
                    pending_chapter = None
                else:
                    # Next row didn't yield a usable title+page — keep
                    # pending for the row after, or discard if we got
                    # a title but no page (continuation text, not the
                    # real chapter start).
                    if title:
                        pending_chapter = None  # discard — garbled
                    # else: keep pending_chapter for next row

            if not title:
                continue

            # Clean the fully joined title, not merely its final cell.
            clean = _chunking_core.clean_heading_text(title)
            clean = re.sub(r"\s+", " ", _leader_run.sub(" ", clean)).strip()
            if not clean:
                continue

            # A Table-of-Problems row can contain a chapter title followed by
            # "N-1 Problem". It is not a second chapter boundary.
            division_match = _document_profiles.match_division(
                clean, profile, "toc_entry")
            if (division_match is not None
                    and _document_profiles.contains_division_subnumber(
                        clean, division_match)):
                continue

            # --- Infer hierarchy level ---------------------------------
            level = _document_profiles.hierarchy_level(clean, profile)

            entries.append({
                "level": level,
                "title": clean,
                "page": page_num if page_num is not None else 0,
            })

    # Remove front-matter-only entries (page=0) unless they're chapters
    entries = [e for e in entries
               if e["page"] > 0 or e["level"] == 1]

    # Deduplicate: same title + page, and same-page chapter entries
    seen: set[tuple[str, int]] = set()
    ch_by_page: dict[int, int] = {}  # page → index in deduped
    deduped: list[dict] = []
    for e in entries:
        key = (e["title"].lower(), e["page"])
        if key in seen:
            continue
        seen.add(key)
        # For chapter entries on the same page, keep the longer title
        if e["level"] == 1 and e["page"] in ch_by_page:
            idx = ch_by_page[e["page"]]
            if len(e["title"]) > len(deduped[idx]["title"]):
                deduped[idx] = e
            continue
        if e["level"] == 1:
            ch_by_page[e["page"]] = len(deduped)
        deduped.append(e)
    entries = deduped

    # Sort by page number (stable — preserves order for same-page entries)
    entries.sort(key=lambda x: x["page"])

    if entries:
        log.info(f"Table-parsed TOC: {len(entries)} entries, "
                 f"pages {entries[0]['page']}-{entries[-1]['page']}")
    return entries


def _merge_page_numbers(scaffold: list[dict],
                        table_entries: list[dict]) -> None:
    """Fill in missing page numbers in scaffold from table-parsed entries.

    Matches by title similarity. Modifies scaffold in-place.
    """
    # Build lookup by normalized title
    table_by_title = {}
    for e in table_entries:
        key = re.sub(r"\s+", " ", e["title"].lower().strip())
        table_by_title[key] = e["page"]

    for entry in scaffold:
        if entry.get("page", 0) <= 0:
            key = re.sub(r"\s+", " ", entry["title"].lower().strip())
            if key in table_by_title:
                entry["page"] = table_by_title[key]


def _build_scaffold_lookup(scaffold: list[dict],
                           max_page: int = 2000) -> dict[int, dict]:
    """Build a page-to-scaffold-entry lookup with forward-fill.

    Returns: {page_number: {"path": str, "chapter_num": int, ...}}
    """
    if not scaffold:
        return {}

    # Build page → scaffold entry (direct matches)
    page_map: dict[int, dict] = {}
    for entry in scaffold:
        pg = entry.get("page", 0)
        if pg > 0:
            previous = page_map.get(pg)
            previous_chapter = (
                previous.get("chapter_num") if previous else None)
            current_chapter = entry.get("chapter_num")
            # On a shared boundary page, a new chapter must reset any
            # preceding-chapter subsection. Within one chapter, retain the
            # last (deepest) entry on that page.
            if (previous is None
                    or previous_chapter == current_chapter
                    or ((entry.get("level") == 1
                         or (entry.get("path")
                             and " > " not in entry["path"]))
                        and current_chapter is not None
                        and current_chapter != previous_chapter)):
                page_map[pg] = entry

    # Forward-fill: each page inherits the most recent scaffold entry
    lookup: dict[int, dict] = {}
    current = None
    min_pg = min(page_map.keys()) if page_map else 1
    max_pg = min(max(page_map.keys()) + 50, max_page) if page_map else max_page
    for pg in range(min_pg, max_pg + 1):
        if pg in page_map:
            current = page_map[pg]
        if current:
            lookup[pg] = current

    return lookup


def _canonical_chapter_titles(
        scaffold: list[dict], chapter_map: dict[int, dict], *,
        structure_profile: (
            str | _document_profiles.StructureProfile
        ) = DEFAULT_STRUCTURE_PROFILE,
) -> dict[int, str]:
    """Choose one clean, stable title for every observed chapter."""
    profile = _document_profiles.get_profile(structure_profile)
    canonical: dict[int, str] = {}

    indexed = list(enumerate(scaffold))
    indexed.sort(key=lambda pair: (pair[1].get("page", 0), pair[0]))
    for _, entry in indexed:
        if entry.get("level") != 1:
            continue
        title = _chunking_core.clean_heading_text(entry.get("title", ""))
        division = _document_profiles.match_division(
            title, profile, "canonical_title")
        if division is None:
            continue
        chapter_num = division.ordinal
        if entry.get("chapter_num") not in (None, chapter_num):
            continue
        if _document_profiles.contains_division_subnumber(
                title, division) or re.search(
                r"\s+(?:Chapter|Part|Unit)\s+"
                r"(?:\d{1,3}|[IVXLCDM]+)\b", title, re.I):
            continue
        canonical.setdefault(chapter_num, title)

    for chapter_num, info in sorted(chapter_map.items()):
        if chapter_num in canonical:
            continue
        title = _chunking_core.clean_heading_text(info.get("title", ""))
        title = re.sub(r"^[^A-Za-z0-9]+\s*", "", title)
        if title:
            division = _document_profiles.match_division(
                title, profile, "canonical_title")
            if division is not None:
                if not _document_profiles.contains_division_subnumber(
                        title, division):
                    canonical[chapter_num] = title
            else:
                raw_number = str(info.get("division_number") or chapter_num)
                kind = str(info.get("division_kind") or "Chapter")
                synthesized = _document_profiles.DivisionMatch(
                    ordinal=chapter_num,
                    raw_number=raw_number,
                    kind=kind,
                    title=title,
                    matched_text=title,
                )
                if not _document_profiles.contains_division_subnumber(
                        title, synthesized):
                    canonical[chapter_num] = (
                        _document_profiles.canonical_division_title(
                            synthesized, profile))
    return canonical


def _normalize_scaffold_metadata(
        scaffold: list[dict], chapter_titles: dict[int, str]) -> list[dict]:
    """Clean scaffold titles and rebuild canonical hierarchical paths."""
    stack: dict[int, str] = {}
    normalized: list[dict] = []
    for entry in scaffold:
        title = _chunking_core.clean_heading_text(entry.get("title", ""))
        chapter_num = entry.get("chapter_num")
        if entry.get("level") == 1 and chapter_num in chapter_titles:
            title = chapter_titles[chapter_num]
        if not title:
            continue
        entry = dict(entry)
        entry["title"] = title
        level = entry.get("level", 3)
        for prior_level in list(stack):
            if prior_level >= level:
                del stack[prior_level]
        stack[level] = title
        entry["path"] = " > ".join(stack[key] for key in sorted(stack))
        normalized.append(entry)
    return normalized


# ---------------------------------------------------------------------------
# Scaffold QC — validate chunks against scaffold
# ---------------------------------------------------------------------------

def _validate_against_scaffold(
    enriched: list[dict],
    scaffold: list[dict],
    chapter_map: dict,
) -> dict:
    """Validate chunk metadata against the authoritative scaffold.

    Checks:
    1. Every scaffold chapter has ≥1 chunk assigned to it
    2. Chapter assignments match scaffold page ranges
    3. Section paths reference valid scaffold entries

    Returns::

        {
            "passed": bool,
            "missing_chapters": [int, ...],
            "mismatched_sections": [{"chunk_idx": int, "expected": str, ...}, ...],
            "coverage": float,  # fraction of scaffold entries with ≥1 chunk
            "flagged": [str, ...],  # issues for manual review
        }
    """
    # Build chapter→chunks mapping from enriched
    chunks_by_chapter: dict[int, int] = {}
    for rec in enriched:
        ch = rec["metadata"].get("chapter_num")
        if ch is not None:
            chunks_by_chapter[ch] = chunks_by_chapter.get(ch, 0) + 1

    # Check scaffold chapters
    scaffold_chapters = set(
        e["chapter_num"] for e in scaffold
        if e.get("chapter_num") is not None and e.get("level") == 1
    )
    missing_chapters = sorted(scaffold_chapters - set(chunks_by_chapter.keys()))

    # Check section coverage: what fraction of scaffold entries
    # have at least one chunk with a matching section_path?
    scaffold_paths = set(e["path"] for e in scaffold if e.get("path"))
    chunk_paths = set(r["metadata"].get("section_path", "")
                      for r in enriched)
    matched_paths = scaffold_paths & chunk_paths
    coverage = len(matched_paths) / len(scaffold_paths) if scaffold_paths else 1.0

    # Check for chunks with no chapter assignment
    unassigned = sum(1 for r in enriched
                     if r["metadata"].get("chapter_num") is None)
    unassigned_pct = unassigned / len(enriched) if enriched else 0

    # Build flagged issues
    flagged = []
    if missing_chapters:
        flagged.append(
            f"Missing chapters in output: {missing_chapters}")
    if coverage < 0.5:
        flagged.append(
            f"Low scaffold coverage: {coverage:.0%} of TOC entries matched")
    if unassigned_pct > 0.3:
        flagged.append(
            f"High unassigned rate: {unassigned}/{len(enriched)} chunks "
            f"({unassigned_pct:.0%}) have no chapter")

    # Mismatched sections: chunks whose page falls in one scaffold chapter
    # but metadata says a different chapter
    mismatched = []
    scaffold_lookup = _build_scaffold_lookup(scaffold)
    for i, rec in enumerate(enriched):
        page = rec["metadata"].get("page_start")
        if page is None:
            continue
        chunk_ch = rec["metadata"].get("chapter_num")
        scaffold_entry = scaffold_lookup.get(page)
        if scaffold_entry and chunk_ch is not None:
            expected_ch = scaffold_entry.get("chapter_num")
            if expected_ch is not None and expected_ch != chunk_ch:
                mismatched.append({
                    "chunk_idx": i,
                    "page": page,
                    "expected_chapter": expected_ch,
                    "got_chapter": chunk_ch,
                })

    if len(mismatched) > 10:
        flagged.append(
            f"{len(mismatched)} chunks have chapter mismatch vs scaffold")

    passed = len(flagged) == 0
    result = {
        "passed": passed,
        "missing_chapters": missing_chapters,
        "mismatched_sections": mismatched[:20],  # cap for readability
        "coverage": coverage,
        "unassigned_chunks": unassigned,
        "flagged": flagged,
    }

    if passed:
        log.info(f"Scaffold QC: PASSED (coverage: {coverage:.0%}, "
                 f"{len(enriched)} chunks, "
                 f"{len(chunks_by_chapter)} chapters)")
    else:
        log.warning(f"Scaffold QC: FAILED — {len(flagged)} issue(s):")
        for f in flagged:
            log.warning(f"  - {f}")

    return result


def _build_chapter_map(
        doc_path: Path, *,
        structure_profile: (
            str | _document_profiles.StructureProfile
        ) = DEFAULT_STRUCTURE_PROFILE,
) -> dict[int, dict]:
    """Extract chapter-to-page-range mapping from DoclingDocument page headers.

    Docling labels running page headers as 'page_header'. These contain chapter
    numbers and titles on every page, giving us a definitive mapping of which
    pages belong to which chapter. This is far more reliable than regex-based
    chapter detection from chunk text.

    Returns: {chapter_num: {"title": str, "min_page": int, "max_page": int}}
    """
    try:
        raw = doc_path.read_bytes()
        doc = json.loads(raw)
    except Exception:
        for enc in ("utf-8", "latin-1"):
            try:
                doc = json.loads(doc_path.read_text(encoding=enc))
                break
            except (UnicodeDecodeError, json.JSONDecodeError):
                continue
        else:
            return {}

    return _build_chapter_map_from_document(
        doc, structure_profile=structure_profile)


def _build_chapter_map_from_document(
        doc: dict, *,
        structure_profile: (
            str | _document_profiles.StructureProfile
        ) = DEFAULT_STRUCTURE_PROFILE,
) -> dict[int, dict]:
    """Build chapter ranges from one already-captured document mapping."""

    profile = _document_profiles.get_profile(structure_profile)
    texts = doc.get("texts", [])
    if not texts:
        return {}

    # --- Private Use Area (PUA) digit decoder ---
    # Some PDFs use custom font glyphs (U+F643..U+F64C) instead of ASCII
    # digits. Map each PUA codepoint to its digit value.
    _PUA_DIGIT_MAP = {chr(0xF643 + i): str(i) for i in range(10)}  # uf643=0 .. uf64c=9

    def _decode_pua_digits(s: str) -> str:
        """Replace PUA codepoints with ASCII digits.  Returns *s* unchanged
        if it contains no PUA characters."""
        if not any(0xE000 <= ord(c) <= 0xF8FF for c in s):
            return s
        return "".join(_PUA_DIGIT_MAP.get(c, c) for c in s)

    chapter_pages: dict[int, dict] = {}
    for t in texts:
        if t.get("label") != "page_header":
            continue
        text = t.get("text", "").strip()
        if len(text) < 5 or len(text) > 120:
            continue
        prov = t.get("prov", [])
        if not prov:
            continue
        page = prov[0].get("page_no")
        if page is None:
            continue

        # Decode PUA font glyphs to ASCII digits before matching
        text = _decode_pua_digits(text)

        division = _document_profiles.match_division(
            text, profile, "running_header")
        if division is None or len(division.title) < 3:
            continue
        ch_num = division.ordinal
        if ch_num not in chapter_pages:
            chapter_pages[ch_num] = {
                "title": division.title.title(),
                "division_kind": division.kind,
                "division_number": division.raw_number,
                "min_page": page,
                "max_page": page,
            }
        else:
            chapter_pages[ch_num]["min_page"] = min(
                chapter_pages[ch_num]["min_page"], page)
            chapter_pages[ch_num]["max_page"] = max(
                chapter_pages[ch_num]["max_page"], page)

    # --- Fallback: if page headers yielded nothing, scan section_header items ---
    if not chapter_pages:
        log.debug("No chapters from page_headers, trying section_header items...")
        for t in texts:
            if t.get("label") not in ("section_header", "title"):
                continue
            text = t.get("text", "").strip()
            if len(text) < 5 or len(text) > 120:
                continue
            prov = t.get("prov", [])
            if not prov:
                continue
            page = prov[0].get("page_no")
            if page is None:
                continue
            division = _document_profiles.match_division(
                text, profile, "running_header")
            if division is None or len(division.title) < 3:
                continue
            ch_num = division.ordinal
            if ch_num not in chapter_pages:
                chapter_pages[ch_num] = {
                    "title": division.title.title(),
                    "division_kind": division.kind,
                    "division_number": division.raw_number,
                    "min_page": page,
                    "max_page": page,
                }

    if chapter_pages:
        log.info(f"Chapter map: {len(chapter_pages)} chapters from page headers")
    else:
        log.warning("No chapter structure detected in document. "
                     "Chapter assignment will use regex fallback.")
    return chapter_pages


def _build_toc_hierarchy(
        doc_path: Path, *,
        structure_profile: (
            str | _document_profiles.StructureProfile
        ) = DEFAULT_STRUCTURE_PROFILE,
) -> list[dict]:
    """Parse the Table of Contents from DoclingDocument tables.

    The TOC is the authoritative source for document hierarchy. It contains
    every chapter, section, subsection, and case name with page numbers.
    The hierarchy is encoded in column positions of the TOC tables.

    Returns a sorted list of:
      {"level": 1-4, "marker": "B.", "title": "Federalism", "page": 5}
    """
    profile = _document_profiles.get_profile(structure_profile)
    try:
        raw = doc_path.read_bytes()
        doc = json.loads(raw)
    except Exception:
        for enc in ("utf-8", "latin-1"):
            try:
                doc = json.loads(doc_path.read_text(encoding=enc))
                break
            except (UnicodeDecodeError, json.JSONDecodeError):
                continue
        else:
            return []

    tables = doc.get("tables", [])
    if not tables:
        return []

    # TOC tables are in the first ~25 pages
    toc_entries = []
    for t in tables:
        prov = t.get("prov", [])
        page = prov[0].get("page_no", 999) if prov else 999
        if page > 30:
            continue

        cells = t.get("data", {}).get("table_cells", [])
        rows: dict[int, dict[int, str]] = {}
        _pua_map = {chr(0xF643 + i): str(i) for i in range(10)}
        for cell in cells:
            row_idx = cell.get("start_row_offset_idx", 0)
            col_idx = cell.get("start_col_offset_idx", 0)
            text = cell.get("text", "").strip()
            # Decode PUA font glyphs to ASCII digits
            if text and any(0xE000 <= ord(c) <= 0xF8FF for c in text):
                text = "".join(_pua_map.get(c, c) for c in text)
            if text:
                rows.setdefault(row_idx, {})[col_idx] = text

        for row_idx in sorted(rows):
            row = rows[row_idx]
            page_num = None
            title = ""

            # Last column is typically the page number
            max_col = max(row.keys()) if row else 0
            pval = row.get(max_col, "")
            if pval.isdigit():
                page_num = int(pval)

            # Hierarchy from column position
            if 0 in row:
                title = row.get(1, row.get(2, ""))
            elif 1 in row:
                title = row.get(2, row[1])
            elif 2 in row:
                title = row[2]

            if title and page_num and page_num > 0:
                marker = row.get(0, row.get(1, ""))
                normalized_title = _normalize_text(title)
                toc_entries.append({
                    "level": _document_profiles.hierarchy_level(
                        f"{marker} {normalized_title}".strip(), profile),
                    "marker": marker.strip(),
                    "title": normalized_title,
                    "page": page_num,
                })

    toc_entries.sort(key=lambda x: x["page"])
    if toc_entries:
        log.info(f"TOC hierarchy: {len(toc_entries)} entries parsed from tables")
    return toc_entries


_TOC_HIERARCHY_PROMPT = """You are analyzing a Table of Contents from this reviewed document family:
{profile_description}
Parse this TOC into a structured hierarchy. For each entry, assign a heading level:

{hierarchy_guidance}

Output ONLY a JSON array. Each entry: {{"level": N, "title": "...", "page": N}}
No explanation, no markdown, ONLY the JSON array.

Table of Contents:
{toc_text}

JSON array:"""


def _llm_parse_toc(toc_text: str, *,
                   cloud_url: str = DEFAULT_CLOUD_URL,
                   cloud_model: str = DEFAULT_CLOUD_MODEL,
                   cloud_key: str = "",
                   ollama_url: str = DEFAULT_OLLAMA_URL,
                   ollama_model: str = DEFAULT_OLLAMA_MODEL,
                   gemini_key: str = "",
                   llm_workers: int = DEFAULT_LLM_WORKERS,
                   thinking: bool = False,
                   structure_profile: (
                       str | _document_profiles.StructureProfile
                   ) = DEFAULT_STRUCTURE_PROFILE,
                   security_policy: (
                       _release_security.ReleaseSecurityPolicy | None
                   ) = None) -> list[dict]:
    """Send raw TOC text through the configured LLM provider and parse it.

    The LLM understands the textbook's structure better than regex —
    it can distinguish chapters from sections from subsections from
    case names based on context and formatting patterns.
    """
    profile = _document_profiles.get_profile(structure_profile)
    hierarchy_guidance = "\n".join([
        "Level 1: a primary division matching the selected profile.",
        *[
            f"Level {rule.level}: a title matching /{rule.pattern}/."
            for rule in profile.hierarchy_rules
        ],
    ])
    # Send TOC in chunks of ~100 lines. Output length, rather than the large
    # reviewed provider context windows, is the practical JSON bottleneck.
    lines = toc_text.strip().split("\n")
    all_entries = []

    CHUNK_SIZE = 100
    for start in range(0, len(lines), CHUNK_SIZE):
        batch = "\n".join(lines[start:start + CHUNK_SIZE])
        prompt = _TOC_HIERARCHY_PROMPT.format(
            toc_text=batch,
            profile_description=profile.document_description,
            hierarchy_guidance=hierarchy_guidance)
        result = _call_llm(
            prompt, cloud_url=cloud_url, cloud_model=cloud_model,
            cloud_key=cloud_key, ollama_url=ollama_url,
            ollama_model=ollama_model, gemini_key=gemini_key,
            llm_workers=llm_workers, thinking=thinking,
            max_tokens=4000, timeout=60, operation="toc.parse",
            security_policy=security_policy)
        if result:
            result = _THINK_TAG_RE.sub("", result).strip()
            s = result.find("[")
            e = result.rfind("]")
            if s != -1 and e != -1:
                try:
                    batch_entries = json.loads(result[s:e + 1])
                    for entry in batch_entries:
                        if isinstance(entry, dict) and "level" in entry and "title" in entry and "page" in entry:
                            all_entries.append({
                                "level": int(entry["level"]),
                                "title": str(entry["title"]).strip(),
                                "page": int(entry["page"]),
                                "marker": "",
                            })
                except (json.JSONDecodeError, ValueError):
                    pass
        log.debug(f"TOC batch {start//CHUNK_SIZE + 1}: "
                  f"{len(all_entries)} entries so far")

    if all_entries:
        log.info(f"LLM TOC parsing: {len(all_entries)} entries with explicit hierarchy")
        return all_entries

    log.warning("LLM TOC parsing failed — using column-position fallback")
    return []


def _build_section_lookup(
        toc: list[dict], chapter_map: dict[int, dict], *,
        structure_profile: (
            str | _document_profiles.StructureProfile
        ) = DEFAULT_STRUCTURE_PROFILE,
) -> dict[int, str]:
    """Build a page -> section_path lookup from the TOC hierarchy.

    For each page in the document, determines the full hierarchical section
    path (e.g., "Chapter 3 > B. Constitutional Limits > 1. The Fountainhead").
    This replaces the flat single-heading section paths from Docling.

    Returns: {page_number: "Chapter N > Section > Subsection > ..."}
    """
    profile = _document_profiles.get_profile(structure_profile)
    if not toc:
        return {}

    # Build a running heading stack that tracks the current position
    # at each hierarchy level
    page_to_path: dict[int, str] = {}
    heading_stack: dict[int, str] = {}  # level -> heading text

    for entry in toc:
        level = entry["level"]
        title = entry["title"]
        page = entry["page"]
        marker = entry["marker"]

        # Clear deeper levels when a higher level changes
        for stack_level in list(heading_stack.keys()):
            if stack_level >= level:
                del heading_stack[stack_level]

        # Set current level
        label = f"{marker} {title}".strip() if marker else title
        heading_stack[level] = label

        # Find which chapter this page belongs to
        ch_prefix = ""
        for ch_num in sorted(chapter_map):
            info = chapter_map[ch_num]
            if info["min_page"] <= page <= info["max_page"]:
                raw_number = str(info.get("division_number") or ch_num)
                kind = str(info.get("division_kind") or "Chapter")
                division = _document_profiles.DivisionMatch(
                    ordinal=ch_num, raw_number=raw_number, kind=kind,
                    title=str(info.get("title") or ""), matched_text="")
                ch_prefix = _document_profiles.canonical_division_title(
                    division, profile)
                break

        # Build full path from stack
        parts = [ch_prefix] if ch_prefix else []
        for stack_level in sorted(heading_stack):
            parts.append(heading_stack[stack_level])

        path = " > ".join(parts)
        page_to_path[page] = path

    # Forward-fill: pages between TOC entries inherit the last path
    if page_to_path:
        all_pages = sorted(page_to_path.keys())
        max_page = max(p.get("max_page", 0) for p in chapter_map.values()) if chapter_map else max(all_pages)
        last_path = ""
        for pg in range(min(all_pages), max_page + 1):
            if pg in page_to_path:
                last_path = page_to_path[pg]
            else:
                page_to_path[pg] = last_path

    return page_to_path


def _assign_chapter_by_page(page_num: int | None,
                            chapter_map: dict[int, dict]) -> tuple[int | None, str]:
    """Look up which chapter a page belongs to using the chapter map.

    Returns (chapter_num, chapter_title) or (None, "") if no match.
    """
    if page_num is None or not chapter_map:
        return None, ""
    for ch_num in sorted(chapter_map.keys()):
        info = chapter_map[ch_num]
        if info["min_page"] <= page_num <= info["max_page"]:
            return ch_num, info["title"]
    return None, ""


def _page_count(pdf_path: Path) -> int:
    """Count pages in a PDF using pypdfium2 (a docling dependency)."""
    import pypdfium2
    pdf_reader = pypdfium2.PdfDocument(str(pdf_path))
    n = len(pdf_reader)
    pdf_reader.close()
    return n


# ---------------------------------------------------------------------------
# LLM helpers — MiniMax cloud primary, Ollama fallback, Gemini last resort
# ---------------------------------------------------------------------------


_provider_value = _llm_adapters._provider_value


def _provider_token_count(source: object, name: str) -> int | None:
    """Read and validate an optional provider-native token count."""
    return _llm_adapters._provider_token_count(
        source, name, provider_value_fn=_provider_value)


_provider_error_category = _llm_adapters._provider_error_category


def _provider_call_error(exc: BaseException, *,
                         transport_attempts: int) -> ProviderCallError:
    return _llm_adapters._provider_call_error(
        exc, transport_attempts=transport_attempts,
        error_category_fn=_provider_error_category)


def _post_loopback_without_environment(url: str, **kwargs):
    """POST to a literal loopback target without ambient proxy settings."""
    session = requests.Session()
    session.trust_env = False
    kwargs.setdefault("stream", True)
    try:
        return _provider_transport.OwnedHttpResponse(
            session.post(url, **kwargs), session)
    except BaseException:
        try:
            session.close()
        except Exception:
            pass
        raise


_DEFAULT_RELEASE_SECURITY_POLICY = _release_security.ReleaseSecurityPolicy()


def _effective_security_policy(
        policy: _release_security.ReleaseSecurityPolicy | None,
) -> _release_security.ReleaseSecurityPolicy:
    """Apply fail-closed release defaults to direct Python callers too."""
    if policy is None:
        return _DEFAULT_RELEASE_SECURITY_POLICY
    if not isinstance(policy, _release_security.ReleaseSecurityPolicy):
        raise TypeError("invalid release security policy")
    return policy


def _post_cloud_with_policy(
        policy: _release_security.ReleaseSecurityPolicy,
        url: str, **kwargs):
    """POST with ambient proxy/CA/netrc state disabled unless reviewed."""
    policy = _effective_security_policy(policy)
    session = requests.Session()
    session.trust_env = policy.trust_environment_network
    kwargs.setdefault("stream", True)
    try:
        return _provider_transport.OwnedHttpResponse(
            session.post(url, **kwargs), session)
    except BaseException:
        try:
            session.close()
        except Exception:
            pass
        raise


def _require_no_cloud_redirect(response: object, feature: str) -> None:
    status_code = getattr(response, "status_code", None)
    if isinstance(status_code, int) and 300 <= status_code < 400:
        raise RuntimeError(f"{feature} endpoint returned a redirect")


def _read_provider_json_response(
        response: object, *, feature: str, max_bytes: int,
        deadline_seconds: float) -> object:
    """Validate status then consume one owned provider response safely."""
    try:
        _require_no_cloud_redirect(response, feature)
        response.raise_for_status()
    except BaseException:
        _provider_transport.close_http_response(response)
        raise
    return _provider_transport.read_bounded_json_response(
        response,
        max_bytes=max_bytes,
        deadline_seconds=deadline_seconds,
    )


def _require_endpoint_egress(
        policy: _release_security.ReleaseSecurityPolicy,
        endpoint: _endpoint_policy.ValidatedEndpoint,
        *, feature: str) -> None:
    """Allow literal loopback locally and gate every other endpoint."""
    if endpoint.is_loopback:
        return
    _release_security.require_cloud_egress(
        policy,
        feature=feature,
        custom_gateway=endpoint.provider == "custom",
    )


def _call_ollama_result(prompt: str, *, url: str = DEFAULT_OLLAMA_URL,
                        model: str = DEFAULT_OLLAMA_MODEL,
                        thinking: bool = False,
                        max_tokens: int = 256,
                        timeout: int = 30,
                        security_policy: (
                            _release_security.ReleaseSecurityPolicy | None
                        ) = None) -> ProviderResponse:
    """Return Ollama text with its native prompt/output token counts."""
    endpoint = _validate_cloud_endpoint(url)
    assert endpoint is not None
    policy = _effective_security_policy(security_policy)
    _require_endpoint_egress(
        policy, endpoint,
        feature="Ollama generation")
    return _llm_adapters._call_ollama_result(
        prompt, url=endpoint.base_url, model=model, thinking=thinking,
        max_tokens=max_tokens, timeout=timeout,
        post_fn=lambda target, **kwargs: _post_cloud_with_policy(
            policy, target, **kwargs),
        loopback_post_fn=_post_loopback_without_environment,
        validate_endpoint_fn=_validate_cloud_endpoint,
        provider_token_count_fn=_provider_token_count,
        provider_call_error_fn=_provider_call_error)


def _call_ollama(prompt: str, *, url: str = DEFAULT_OLLAMA_URL,
                 model: str = DEFAULT_OLLAMA_MODEL,
                 thinking: bool = False,
                 max_tokens: int = 256,
                 timeout: int = 30,
                 _structured: bool = False,
                 security_policy: (
                     _release_security.ReleaseSecurityPolicy | None
                 ) = None,
                 ) -> Optional[str] | ProviderResponse:
    """Call Ollama generate endpoint. Returns response text or None on failure."""
    try:
        result = _call_ollama_result(
            prompt, url=url, model=model, thinking=thinking,
            max_tokens=max_tokens, timeout=timeout,
            security_policy=security_policy)
        return result if _structured else result.text
    except ProviderCallError as exc:
        log.debug("Ollama call failed: %s", exc.category)
        if _structured:
            raise
        return None


_gemini_client_cache = None
_gemini_client_key = ""
_gemini_client_trust_environment: bool | None = None
_gemini_client_lock = _threading.Lock()


def _gemini_content_filtered(response: object) -> bool:
    return _llm_adapters._gemini_content_filtered(
        response, provider_value_fn=_provider_value)


def _load_gemini_client(
        api_key: str, *,
        security_policy: (
            _release_security.ReleaseSecurityPolicy | None) = None,
) -> tuple[object, object]:
    """Lazily create the cached Gemini client through facade-owned state."""
    global _gemini_client_cache, _gemini_client_key
    global _gemini_client_trust_environment
    policy = _effective_security_policy(security_policy)
    _release_security.require_cloud_egress(
        policy, feature="Gemini generation")
    try:
        from google import genai
        from google.genai import types
        types.HttpOptions
        types.HttpRetryOptions
    except (ImportError, AttributeError):
        raise ProviderCallError(
            "configuration_error", transport_attempts=0) from None

    try:
        with _gemini_client_lock:
            if (_gemini_client_cache is None
                    or _gemini_client_key != api_key
                    or _gemini_client_trust_environment
                    != policy.trust_environment_network):
                transport_args = {
                    "trust_env": policy.trust_environment_network,
                    "follow_redirects": False,
                    "verify": True,
                }
                new_client = genai.Client(
                    vertexai=False,
                    api_key=api_key,
                    http_options=types.HttpOptions(
                        base_url=_GEMINI_API_BASE_URL,
                        api_version="v1beta",
                        timeout=60_000,
                        retry_options=types.HttpRetryOptions(attempts=1),
                        client_args=dict(transport_args),
                        async_client_args=dict(transport_args),
                    ),
                )
                _gemini_client_cache = new_client
                _gemini_client_key = api_key
                _gemini_client_trust_environment = (
                    policy.trust_environment_network)
                # Do not close the displaced client here.  Another admitted
                # call can still hold it while performing generate_content;
                # eager close turns a concurrent policy/key rotation into a
                # spurious provider failure.  Its caller reference keeps it
                # alive through the request, after which normal object/process
                # cleanup can reclaim the transport.
            return _gemini_client_cache, types
    except Exception:
        raise ProviderCallError(
            "configuration_error", transport_attempts=0) from None


def _call_gemini_result(prompt: str, *, api_key: str = "",
                        model: str = DEFAULT_GEMINI_MODEL,
                        max_tokens: int = 256,
                        timeout: int = 30,
                        thinking_level: str | None = None,
                        security_policy: (
                            _release_security.ReleaseSecurityPolicy | None
                        ) = None) -> ProviderResponse:
    """Return Gemini text and usage with SDK retries explicitly disabled."""
    policy = _effective_security_policy(security_policy)
    _release_security.require_cloud_egress(
        policy,
        feature="Gemini generation")
    return _llm_adapters._call_gemini_result(
        prompt, api_key=api_key, model=model,
        max_tokens=max_tokens, timeout=timeout,
        thinking_level=thinking_level,
        client_loader_fn=lambda key: _load_gemini_client(
            key, security_policy=policy),
        environment_get_fn=os.environ.get,
        provider_value_fn=_provider_value,
        provider_token_count_fn=_provider_token_count,
        provider_call_error_fn=_provider_call_error,
        content_filtered_fn=_gemini_content_filtered)


def _call_gemini(prompt: str, *, api_key: str = "",
                 model: str = DEFAULT_GEMINI_MODEL,
                 max_tokens: int = 256,
                 timeout: int = 30,
                 thinking_level: str | None = None,
                 _structured: bool = False,
                 security_policy: (
                     _release_security.ReleaseSecurityPolicy | None
                 ) = None,
                 ) -> Optional[str] | ProviderResponse:
    """Call Gemini generate endpoint. Returns response text or None on failure."""
    try:
        result = _call_gemini_result(
            prompt, api_key=api_key, model=model,
            max_tokens=max_tokens, timeout=timeout,
            thinking_level=thinking_level,
            security_policy=security_policy)
        return result if _structured else result.text
    except ProviderCallError as exc:
        log.debug("Gemini call failed: %s", exc.category)
        if _structured:
            raise
        return None


# --- Adaptive rate limiting for cloud API ---

_AdaptiveThrottle = _llm_adapters._AdaptiveThrottle


# Global throttle instance — shared across all LLM calls
_api_throttle: Optional[_AdaptiveThrottle] = None
_api_throttle_lock = _threading.Lock()


def _get_throttle(max_workers: int) -> _AdaptiveThrottle:
    """Get or create the global adaptive throttle."""
    global _api_throttle
    with _api_throttle_lock:
        if _api_throttle is None or _api_throttle._max != max_workers:
            _api_throttle = _AdaptiveThrottle(
                max_workers, sleep_fn=time.sleep,
                info_fn=log.info, warning_fn=log.warning)
        return _api_throttle


_retry_after_seconds = _llm_adapters._retry_after_seconds


def _call_openai_compatible_result(
        prompt: str, *, base_url: str, model: str, api_key: str = "",
        thinking: bool = False, max_tokens: int = 256,
        max_workers: int = DEFAULT_LLM_WORKERS,
        timeout: int = 30,
        _admit_retry: Callable[[], None] | None = None,
        security_policy: (
            _release_security.ReleaseSecurityPolicy | None
        ) = None) -> ProviderResponse:
    """Return OpenAI-compatible text, native usage, and retry provenance."""
    try:
        endpoint = _validate_cloud_endpoint(base_url)
    except (TypeError, ValueError):
        raise ProviderCallError(
            "configuration_error", transport_attempts=0) from None
    assert endpoint is not None
    policy = _effective_security_policy(security_policy)
    _require_endpoint_egress(
        policy, endpoint,
        feature="OpenAI-compatible generation")
    return _llm_adapters._call_openai_compatible_result(
        prompt, base_url=endpoint.base_url, model=model, api_key=api_key,
        thinking=thinking, max_tokens=max_tokens,
        max_workers=max_workers, timeout=timeout,
        post_fn=lambda url, **kwargs: _post_cloud_with_policy(
            policy, url, **kwargs),
        loopback_post_fn=_post_loopback_without_environment,
        get_throttle_fn=_get_throttle,
        sleep_fn=time.sleep, validate_endpoint_fn=_validate_cloud_endpoint,
        provider_token_count_fn=_provider_token_count,
        provider_value_fn=_provider_value,
        provider_call_error_fn=_provider_call_error,
        retry_after_fn=_retry_after_seconds,
        admit_retry_fn=_admit_retry)


def _call_openai_compatible(prompt: str, *, base_url: str,
                            model: str, api_key: str = "",
                            thinking: bool = False,
                            max_tokens: int = 256,
                            max_workers: int = DEFAULT_LLM_WORKERS,
                            timeout: int = 30,
                            _structured: bool = False,
                            _admit_retry: Callable[[], None] | None = None,
                            security_policy: (
                                _release_security.ReleaseSecurityPolicy | None
                            ) = None,
                            ) -> Optional[str] | ProviderResponse:
    """Compatibility facade for an OpenAI-compatible chat completion."""
    try:
        result = _call_openai_compatible_result(
            prompt, base_url=base_url, model=model, api_key=api_key,
            thinking=thinking, max_tokens=max_tokens,
            max_workers=max_workers, timeout=timeout,
            _admit_retry=_admit_retry,
            security_policy=security_policy)
        return result if _structured else result.text
    except ProviderCallError as exc:
        log.debug("OpenAI-compatible call failed: %s", exc.category)
        if _structured:
            raise
        return None


_llm_endpoint_id = _llm_adapters._llm_endpoint_id


def _call_llm_result(
        prompt: str, *, ollama_url: str = DEFAULT_OLLAMA_URL,
        ollama_model: str = DEFAULT_OLLAMA_MODEL,
        gemini_key: str = "", cloud_url: str = "", cloud_model: str = "",
        cloud_key: str = "", llm_workers: int = DEFAULT_LLM_WORKERS,
        thinking: bool = False, max_tokens: int = 256,
        operation: str = "generic", prompt_version: str = "1",
        timeout: int = 30, fallback_policy: str | None = None,
        failure_policy: str | None = None, cache_mode: str | None = None,
        cache_dir: Path | str | None = None,
        security_policy: (
            _release_security.ReleaseSecurityPolicy | None
        ) = None) -> LLMResult:
    """Execute an LLM request with structured provenance and run controls."""
    # Validate every enabled caller-supplied transport before constructing a
    # cache key or consulting a cache.  The adapters repeat this check at the
    # final network boundary, but that alone would let a malformed endpoint
    # reuse a pre-existing cache entry without ever reaching the adapter.
    policy = _effective_security_policy(security_policy)
    minimax_enabled = False
    if cloud_url and cloud_key:
        cloud_endpoint = _validate_cloud_endpoint(cloud_url)
        assert cloud_endpoint is not None
        _require_endpoint_egress(
            policy, cloud_endpoint,
            feature="OpenAI-compatible generation")
        cloud_url = cloud_endpoint.base_url
        minimax_enabled = cloud_endpoint.provider == "minimax"
    if ollama_url:
        ollama_endpoint = _validate_cloud_endpoint(ollama_url)
        assert ollama_endpoint is not None
        _require_endpoint_egress(
            policy, ollama_endpoint, feature="Ollama generation")
        ollama_url = ollama_endpoint.base_url

    runtime_config = _llm_runtime.config
    effective_cloud_model = cloud_model or ollama_model
    minimax_m2_enabled = (
        minimax_enabled
        and effective_cloud_model.casefold().startswith("minimax-m2"))
    # MiniMax M2.x always reasons inside the completion-token allowance.  A
    # tiny shared operation budget can end before final content appears, so
    # bind a provider-safe floor before runtime token admission/accounting.
    effective_max_tokens = (
        max(max_tokens, 512) if minimax_m2_enabled else max_tokens)
    request = LLMRequest(
        prompt=prompt,
        operation=operation,
        prompt_version=prompt_version,
        max_tokens=effective_max_tokens,
        thinking=thinking,
        timeout=timeout,
        fallback_policy=fallback_policy or runtime_config.fallback_policy,
        failure_policy=failure_policy or runtime_config.failure_policy,
        cache_mode=cache_mode,
        cache_dir=Path(cache_dir) if cache_dir is not None else None,
    )
    providers: list[ProviderSpec] = []

    if cloud_url and cloud_key:
        def invoke_cloud(
                req: LLMRequest) -> Optional[str] | ProviderResponse:
            return _call_openai_compatible(
                req.prompt, base_url=cloud_url, model=effective_cloud_model,
                api_key=cloud_key, max_workers=llm_workers,
                thinking=req.thinking, max_tokens=req.max_tokens,
                timeout=req.timeout, _structured=True,
                _admit_retry=req.admit_transport_retry,
                security_policy=policy)

        providers.append(ProviderSpec(
            name="cloud", model=effective_cloud_model,
            endpoint_id=_llm_endpoint_id(cloud_url), invoke=invoke_cloud,
            cache_namespace_id=(
                policy.cache_namespace_id or "v1:default")))

    if ollama_url:
        def invoke_ollama(
                req: LLMRequest) -> Optional[str] | ProviderResponse:
            return _call_ollama(
                req.prompt, url=ollama_url, model=ollama_model,
                thinking=req.thinking, max_tokens=req.max_tokens,
                timeout=req.timeout, _structured=True,
                security_policy=policy)

        providers.append(ProviderSpec(
            name="ollama", model=ollama_model,
            endpoint_id=_llm_endpoint_id(ollama_url), invoke=invoke_ollama,
            cache_namespace_id=(
                policy.cache_namespace_id or "v1:default")))

    # Ambient cloud credentials do not turn a local-only execution into an
    # error or a cloud-capable chain.  An explicitly supplied key still fails
    # closed, while allow-cloud mode may discover the environment key only
    # after the network policy has been checked.
    gemini_configured = bool(gemini_key)
    if not gemini_configured and policy.network_policy == "allow-cloud":
        gemini_configured = "GEMINI_API_KEY" in os.environ
    if gemini_configured:
        _release_security.require_cloud_egress(
            policy, feature="Gemini generation")
    effective_gemini_key = (
        gemini_key or os.environ.get("GEMINI_API_KEY", "")
        if gemini_configured else ""
    )
    if effective_gemini_key:
        def invoke_gemini(
                req: LLMRequest) -> Optional[str] | ProviderResponse:
            return _call_gemini(
                req.prompt, api_key=effective_gemini_key,
                model=DEFAULT_GEMINI_MODEL, max_tokens=req.max_tokens,
                timeout=req.timeout, _structured=True,
                thinking_level=("high" if req.thinking else "minimal"),
                security_policy=policy)

        providers.append(ProviderSpec(
            name="gemini", model=DEFAULT_GEMINI_MODEL,
            endpoint_id="generativelanguage.googleapis.com/v1beta",
            invoke=invoke_gemini,
            cache_namespace_id=(
                policy.cache_namespace_id or "v1:default")))

    return _llm_runtime.execute(request, providers)


def _call_llm(prompt: str, *, ollama_url: str = DEFAULT_OLLAMA_URL,
              ollama_model: str = DEFAULT_OLLAMA_MODEL,
              gemini_key: str = "", cloud_url: str = "",
              cloud_model: str = "", cloud_key: str = "",
              llm_workers: int = DEFAULT_LLM_WORKERS,
              thinking: bool = False, max_tokens: int = 256,
              operation: str = "generic", prompt_version: str = "1",
              timeout: int = 30, fallback_policy: str | None = None,
              failure_policy: str | None = None,
              cache_mode: str | None = None,
              cache_dir: Path | str | None = None,
              security_policy: (
                  _release_security.ReleaseSecurityPolicy | None
              ) = None) -> Optional[str]:
    """Compatibility facade returning text from the structured LLM runtime."""
    result = _call_llm_result(
        prompt, ollama_url=ollama_url, ollama_model=ollama_model,
        gemini_key=gemini_key, cloud_url=cloud_url,
        cloud_model=cloud_model, cloud_key=cloud_key,
        llm_workers=llm_workers, thinking=thinking,
        max_tokens=max_tokens, operation=operation,
        prompt_version=prompt_version, timeout=timeout,
        fallback_policy=fallback_policy, failure_policy=failure_policy,
        cache_mode=cache_mode, cache_dir=cache_dir,
        security_policy=security_policy)
    return result.text or None


_CLASSIFY_PROMPT = """You are classifying chunks from a law school casebook for a legal RAG system.
Accurate classification improves retrieval: case opinions should be findable by case name,
statutory text by rule number, and pedagogical content by topic.

Classify into exactly ONE category:

case_opinion — Judicial opinion text. Key signals: judge attribution ("Justice X",
"delivered the opinion"), procedural posture ("certiorari", "affirmed", "reversed"),
legal reasoning and holdings. Includes concurrences and dissents.

notes_and_questions — Pedagogical material FOLLOWING a case or reading. Numbered
questions (1., 2.), hypotheticals, discussion prompts, "Notes and Questions" headings.
NOT the case itself, but the teaching material about it.

author_narrative — The textbook authors' explanatory prose: introductions to legal
concepts, historical context, doctrinal analysis, transitions between cases.
This is the "glue" text, not quotes from courts or statutes.

statutory_excerpt — Verbatim statute, rule, or code text. Key signals: "U.S.C.",
section symbols, "Fed. R. Civ. P.", "Rule XX", numbered subsections (a)(1)(A).
Must be the actual text of the law, not discussion about it.

table — Structured tabular data, comparison charts, jurisdiction tables.
Pipe-delimited or column-aligned content.

footnote — Numbered footnotes with citation-heavy text. Signals: superscript
numbering, "Id.", "supra", "infra", "see also", parenthetical case descriptions.

chapter_introduction — Opening overview at the start of a chapter or major section.
Sets up what will be covered. Usually under a "Chapter N" or "Introduction" heading.

Headings: {headings}

Text (first 600 chars):
{text}

Reply with ONLY the category name."""


def _llm_classify(text: str, headings: list[str] | None, *,
                  ollama_url: str = DEFAULT_OLLAMA_URL,
                  ollama_model: str = DEFAULT_OLLAMA_MODEL,
                  gemini_key: str = "",
                  cloud_url: str = "", cloud_model: str = "",
                  cloud_key: str = "",
                  llm_workers: int = DEFAULT_LLM_WORKERS,
                  thinking: bool = False,
                  security_policy: (
                      _release_security.ReleaseSecurityPolicy | None
                  ) = None) -> Optional[str]:
    """Classify a chunk using LLM. Returns label or None on failure."""
    heading_str = " > ".join(headings) if headings else "(none)"
    prompt = _CLASSIFY_PROMPT.format(headings=heading_str, text=text[:600])
    result = _call_llm(prompt, ollama_url=ollama_url, ollama_model=ollama_model,
                       gemini_key=gemini_key, cloud_url=cloud_url,
                       cloud_model=cloud_model, cloud_key=cloud_key,
                       llm_workers=llm_workers, thinking=thinking,
                       max_tokens=256, operation="chunk.classify",
                       security_policy=security_policy)
    if not result:
        return None
    # Clean thinking tags and extract the label
    result = _THINK_TAG_RE.sub("", result)
    result_lower = result.lower().strip()
    for label in _CONTENT_LABELS:
        if label in result_lower:
            return label
    return None


# --- Zero-shot classifier (BART-MNLI, no LLM call needed) ---

_zeroshot_classifier = None


def _zeroshot_classify(text: str, headings: list[str] | None) -> Optional[str]:
    """Classify using the pinned BART-MNLI zero-shot classifier.

    Runs locally on GPU. No API calls, no LLM latency.
    ~50ms per chunk on RTX 5060.
    """
    global _zeroshot_classifier
    if _zeroshot_classifier is None:
        try:
            from transformers import pipeline as hf_pipeline
            log.info(f"Loading zero-shot classifier: {DEFAULT_ZEROSHOT_MODEL}")
            model_source, verified = _model_loader_source(
                DEFAULT_ZEROSHOT_MODEL, "zero_shot_classifier")
            _zeroshot_classifier = hf_pipeline(
                "zero-shot-classification",
                model=model_source,
                **({
                    "tokenizer": model_source,
                    "model_kwargs": {
                        "local_files_only": True,
                        "use_safetensors": True,
                    },
                } if verified else {}),
                device=0,  # GPU
            )
        except Exception as e:
            log.warning(f"Zero-shot classifier unavailable: {e}")
            return None

    # Include heading context in the text
    context = f"[Section: {' > '.join(headings)}] " if headings else ""
    input_text = context + text[:512]

    result = _zeroshot_classifier(input_text, _ZS_LABELS,
                                   multi_label=False)
    top_label = result["labels"][0]
    top_score = result["scores"][0]

    # Only accept if confidence is reasonably high
    if top_score < 0.4:
        return None

    return _ZS_LABEL_MAP.get(top_label)


_CONTEXT_PROMPT = """You are summarizing chunks from a Civil Procedure law textbook for a retrieval system.
Write exactly 1-2 sentences of context for this chunk. Include: the topic area, the legal concept discussed, and any key case names.

Headings: {headings}
Chapter: {chapter}

Text (first 600 chars):
{text}

Context (1-2 sentences only):"""


def _generate_context(text: str, headings: list[str] | None,
                      chapter_title: str = "", *,
                      ollama_url: str = DEFAULT_OLLAMA_URL,
                      ollama_model: str = DEFAULT_OLLAMA_MODEL,
                      gemini_key: str = "",
                      cloud_url: str = "", cloud_model: str = "",
                      cloud_key: str = "",
                      llm_workers: int = DEFAULT_LLM_WORKERS,
                      thinking: bool = False,
                      security_policy: (
                          _release_security.ReleaseSecurityPolicy | None
                      ) = None) -> str:
    """Generate a contextual retrieval prefix for a chunk."""
    heading_str = " > ".join(headings) if headings else "(none)"
    prompt = _CONTEXT_PROMPT.format(
        headings=heading_str,
        chapter=chapter_title or "(unknown)",
        text=text[:600],
    )
    result = _call_llm(prompt, ollama_url=ollama_url, ollama_model=ollama_model,
                       gemini_key=gemini_key, cloud_url=cloud_url,
                       cloud_model=cloud_model, cloud_key=cloud_key,
                       llm_workers=llm_workers, thinking=thinking,
                       max_tokens=160, operation="chunk.contextualize",
                       security_policy=security_policy)
    if not result:
        return ""
    # Clean up: remove thinking tags that deepseek-r1 sometimes emits
    result = _THINK_TAG_RE.sub("", result).strip()
    # Cap at 2 sentences
    sentences = re.split(r"(?<=[.!?])\s+", result)
    return " ".join(sentences[:2])


# ---------------------------------------------------------------------------
# Reranker — lazy-loaded, model-keyed cache
# ---------------------------------------------------------------------------

_reranker_instances: dict[
    tuple[str, _release_security.ReleaseSecurityPolicy], object
] = {}
_reranker_lock = _threading.Lock()
_RERANKER_METADATA_CHAR_LIMIT = 1600


_metadata_text = _retrieval_core._metadata_text


def _reranker_document(document: str, metadata: dict) -> str:
    """Add bounded legal context to a candidate without changing its payload."""
    fields = (
        ("Case", "primary_case"),
        ("Section", "section_path"),
        ("Heading", "headings"),
        ("Chapter", "chapter_title"),
        ("Context", "context"),
    )
    context_parts = []
    remaining = _RERANKER_METADATA_CHAR_LIMIT
    for label, key in fields:
        value = _metadata_text(metadata.get(key))
        if not value or remaining <= 0:
            continue
        entry = f"{label}: {value}"[:remaining]
        context_parts.append(entry)
        remaining -= len(entry) + 1
    if not context_parts:
        return document
    return "\n".join(context_parts) + "\n\nText:\n" + document


def _get_reranker(
        model_name: str = DEFAULT_RERANKER_MODEL, *,
        security_policy: (
            _release_security.ReleaseSecurityPolicy | None) = None):
    """Lazy-load a local reranker within one immutable security policy."""
    if model_name.startswith(("cohere-rerank", "jina-reranker")):
        return None
    policy = _effective_security_policy(security_policy)
    cache_key = (model_name, policy)
    if cache_key not in _reranker_instances:
        with _reranker_lock:
            if cache_key in _reranker_instances:
                return _reranker_instances[cache_key]
            from FlagEmbedding import FlagReranker
            log.info(f"Loading reranker: {model_name}")
            model_source, verified = _model_loader_source(
                model_name, "reranker", security_policy=policy)
            _reranker_instances[cache_key] = FlagReranker(
                model_source, use_fp16=True,
                trust_remote_code=not verified,
            )
    return _reranker_instances[cache_key]


def _validated_reranker_rows(
        payload: object, *, document_count: int, top_k: int,
        provider: str) -> list[tuple[int, float]]:
    if not isinstance(payload, dict) or not isinstance(
            payload.get("results"), list):
        raise RuntimeError(f"{provider} returned an invalid rerank response")
    raw_results = payload["results"]
    if len(raw_results) > min(document_count, top_k):
        raise RuntimeError(f"{provider} returned too many rerank results")
    rows = []
    seen = set()
    for item in raw_results:
        if not isinstance(item, dict):
            raise RuntimeError(f"{provider} returned an invalid rerank result")
        index = item.get("index")
        score = item.get("relevance_score")
        if (
            isinstance(index, bool)
            or not isinstance(index, int)
            or not 0 <= index < document_count
            or index in seen
        ):
            raise RuntimeError(f"{provider} returned an invalid rerank index")
        if isinstance(score, bool) or not isinstance(score, (int, float)):
            raise RuntimeError(f"{provider} returned an invalid rerank score")
        numeric_score = float(score)
        if not math.isfinite(numeric_score):
            raise RuntimeError(f"{provider} returned an invalid rerank score")
        seen.add(index)
        rows.append((index, numeric_score))
    return rows


def _rerank(query: str, documents: list[str], metadatas: list[dict],
            distances: list[float], top_k: int, *,
            reranker_model: str = DEFAULT_RERANKER_MODEL,
            security_policy: (
                _release_security.ReleaseSecurityPolicy | None
            ) = None) -> tuple:
    """Rerank retrieved documents using cross-encoder or API reranker.

    Supports:
      - BAAI/bge-reranker-* → local FlagReranker (GPU)
      - cohere-rerank-*     → Cohere Rerank API (requires COHERE_API_KEY)
      - jina-reranker-*     → Jina Rerank API (requires JINA_API_KEY)

    Returns (documents, metadatas, scores) reranked and trimmed to top_k.
    """
    reranker_documents = [
        _reranker_document(document, metadata or {})
        for document, metadata in zip(documents, metadatas)
    ]
    if reranker_model.startswith(("cohere-rerank", "jina-reranker")):
        policy = _effective_security_policy(security_policy)
        _release_security.require_cloud_egress(
            policy,
            feature="cloud reranking")
    else:
        policy = _effective_security_policy(security_policy)

    # --- Cohere Rerank API ---
    if reranker_model.startswith("cohere-rerank"):
        api_key = os.environ.get("COHERE_API_KEY", "")
        if not api_key:
            raise ValueError("COHERE_API_KEY env var required for Cohere reranker")
        model_id = reranker_model.removeprefix("cohere-")
        resp = _post_cloud_with_policy(
            policy, _COHERE_RERANK_URL,
            headers={"Accept": "application/json"},
            auth=_llm_adapters._BearerAuth(api_key),
            json={"model": model_id, "query": query,
                  "documents": reranker_documents, "top_n": top_k},
            timeout=60, allow_redirects=False, stream=True,
        )
        payload = _read_provider_json_response(
            resp,
            feature="Cohere reranker",
            max_bytes=_provider_transport.RERANK_RESPONSE_MAX_BYTES,
            deadline_seconds=60,
        )
        rows = _validated_reranker_rows(
            payload, document_count=len(documents), top_k=top_k,
            provider="Cohere")
        return (
            [documents[index] for index, _ in rows],
            [metadatas[index] for index, _ in rows],
            [score for _, score in rows],
        )

    # --- Jina Rerank API ---
    if reranker_model.startswith("jina-reranker"):
        api_key = os.environ.get("JINA_API_KEY", "")
        if not api_key:
            raise ValueError("JINA_API_KEY env var required for Jina reranker")
        resp = _post_cloud_with_policy(
            policy, _JINA_RERANK_URL,
            headers={"Accept": "application/json"},
            auth=_llm_adapters._BearerAuth(api_key),
            json={"model": reranker_model, "query": query,
                  "documents": reranker_documents, "top_n": top_k},
            timeout=60,
            allow_redirects=False,
            stream=True,
        )
        payload = _read_provider_json_response(
            resp,
            feature="Jina reranker",
            max_bytes=_provider_transport.RERANK_RESPONSE_MAX_BYTES,
            deadline_seconds=60,
        )
        rows = _validated_reranker_rows(
            payload, document_count=len(documents), top_k=top_k,
            provider="Jina")
        return (
            [documents[index] for index, _ in rows],
            [metadatas[index] for index, _ in rows],
            [score for _, score in rows],
        )

    # --- Local FlagReranker (BGE, etc.) ---
    reranker = _get_reranker(
        reranker_model, security_policy=security_policy)
    pairs = [[query, document] for document in reranker_documents]
    scores = reranker.compute_score(pairs, normalize=True)
    if isinstance(scores, float):
        scores = [scores]

    ranked = sorted(zip(scores, documents, metadatas, distances),
                    key=lambda x: x[0], reverse=True)
    ranked = ranked[:top_k]

    return (
        [r[1] for r in ranked],
        [r[2] for r in ranked],
        [r[0] for r in ranked],
    )


def _normalize_text(text: str) -> str:
    """Fix common encoding artifacts from PDF extraction.

    Handles: non-breaking spaces (\\xa0), smart quotes, en/em dashes,
    ligatures (fi, fl, ff, ffi, ffl), and stray control characters.
    """
    return _chunking_core._normalize_text(
        text,
        strip_headers_footers_fn=_strip_headers_footers,
        dedup_nearby_lines_fn=_dedup_nearby_lines,
    )


def _strip_headers_footers(text: str) -> str:
    """Remove likely page headers and footers from chunk text.

    Only standalone page-number lines are removed. Without page-position
    provenance, short all-caps lines and Roman numerals are ambiguous: they are
    often legitimate legal headings (for example ``PERSONAL JURISDICTION`` or
    ``IV.``) and must be preserved.
    """
    return _chunking_core._strip_headers_footers(text)


def _dedup_nearby_lines(text: str, window: int = 5) -> str:
    """Remove lines that duplicate another line within *window* lines above."""
    return _chunking_core._dedup_nearby_lines(text, window)


# --- Front/back matter detection ---

# Pages that are structural (TOC, index, title page, copyright) not content
_STRUCTURAL_PATTERNS = _chunking_core._STRUCTURAL_PATTERNS

# TOC-like content: lines that are mostly "Topic ... page_number"
_TOC_LINE_RE = _chunking_core._TOC_LINE_RE
# Index-like content: lines that are "Term, page, page, page"
_INDEX_LINE_RE = _chunking_core._INDEX_LINE_RE


_is_structural_content = _chunking_core._is_structural_content


# --- Deduplication ---

_text_fingerprint = _chunking_core._text_fingerprint


_make_trigrams = _chunking_core._make_trigrams


def _deduplicate_chunks(chunks: list[dict],
                        threshold: float = DEDUP_THRESHOLD) -> list[dict]:
    """Deduplicate only when doing so cannot orphan source identities."""

    def source_refs(record: dict) -> set[str] | None:
        values = record.get("metadata", {}).get("source_items")
        if values is None:
            return None
        return {
            item["ref"] for item in values
            if isinstance(item, dict) and isinstance(item.get("ref"), str)
        }

    def can_deduplicate(kept: dict, candidate: dict) -> bool:
        kept_metadata = kept.get("metadata", {})
        candidate_metadata = candidate.get("metadata", {})
        if (isinstance(kept_metadata, dict)
                and _table_retrieval_core.TABLE_FRAGMENT_OCCURRENCE_FIELD
                in kept_metadata
                or isinstance(candidate_metadata, dict)
                and _table_retrieval_core.TABLE_FRAGMENT_OCCURRENCE_FIELD
                in candidate_metadata):
            return False
        kept_refs = source_refs(kept)
        candidate_refs = source_refs(candidate)
        if kept_refs is None and candidate_refs is None:
            return True
        if not kept_refs or not candidate_refs:
            return False
        # Similarity alone is not proof that two fragments from the same
        # source item carry the same proposition.  For source-lineaged data,
        # remove only byte-identical text whose identities are already fully
        # represented by the retained record.
        return (
            candidate_refs.issubset(kept_refs)
            and candidate.get("text") == kept.get("text")
        )

    return _chunking_core._deduplicate_chunks(
        chunks,
        threshold,
        text_fingerprint_fn=_text_fingerprint,
        make_trigrams_fn=_make_trigrams,
        can_deduplicate_fn=can_deduplicate,
        removed_callback=lambda removed: log.info(
            "Deduplication: removed "
            f"{removed} source-overlapping near-duplicate chunks"),
    )


# ---------------------------------------------------------------------------
# Step 0: Preprocess — strip background scan images
# ---------------------------------------------------------------------------

DEFAULT_PREPROCESSED_PATH = Path("output/preprocessed.pdf")


def _inspect_page_background_images(
        doc, page, min_dim: int
) -> _ingestion_core.PageBackgroundInspection:
    """Return rich background-image inspection state for safety planning."""
    return _ingestion_core.inspect_page_background_images(
        doc, page, min_dim, thresholds=_pdf_ingestion_thresholds())


def _page_background_images(doc, page, min_dim: int) -> list[tuple[int, int, int]]:
    """Return large images that visibly cover most of a PDF page.

    Dimensions alone cannot distinguish a scan background from a substantive
    figure. Placement checks fail closed: an image is removable only when its
    rendered rectangle covers at least 70% of the page.
    """
    inspection = _inspect_page_background_images(doc, page, min_dim)
    return list(inspection.candidates) if inspection.complete else []


def _analyze_pdf_images(pdf_path: Path, min_dim: int = 1000) -> dict:
    """Scan background images and the safety of the PDF text layer."""
    import pymupdf

    with pymupdf.open(str(pdf_path)) as doc:
        analysis = _ingestion_core.analyze_pdf_document(
            doc,
            min_dim,
            thresholds=_pdf_ingestion_thresholds(),
            page_text_is_usable_fn=_pdf_page_text_is_usable,
            page_background_inspection_fn=_inspect_page_background_images,
        )
    for issue in analysis.issues:
        if issue.stage == "text":
            log.warning(
                "Could not inspect the text layer on PDF page %s: %s",
                issue.page_number, issue.detail,
            )
        else:
            log.warning(
                "Could not inspect images on PDF page %s: %s",
                issue.page_number, issue.detail,
            )
    return analysis.stats


def preprocess_pdf(input_path: Path, output_path: Path, *,
                   min_dim: int = 1000,
                   force: bool = False,
                   analyze_only: bool = False,
                   _analysis_cache: dict | None = None) -> Optional[Path]:
    """Strip large background raster images from scanned PDFs.

    Many scanned-then-OCR'd textbooks (Paper Capture, ABBYY) embed a full-page
    raster behind the text layer. This causes docling-parse's C++ backend to
    OOM (std::bad_alloc). Stripping those images yields a text-only PDF that
    converts cleanly.

    Returns the output path if stripping was done, or None if skipped.
    """
    import pymupdf
    from tqdm import tqdm

    _require_file(input_path, "PDF file")

    # --- Analyze ---
    if _analysis_cache is not None:
        stats = _analysis_cache
    else:
        log.info(f"Scanning {input_path.name} for background images (>{min_dim}px)...")
        stats = _analyze_pdf_images(input_path, min_dim)
    ratio = stats["pages_with_large_images"] / max(stats["total_pages"], 1)

    log.info(f"  {stats['pages_with_large_images']}/{stats['total_pages']} pages "
             f"have large images ({ratio:.0%})")
    if stats["unique_dims"]:
        log.info(f"  Image dimensions: {', '.join(sorted(stats['unique_dims']))}")
    if "pages_with_usable_text" in stats:
        text_ratio = (
            stats["pages_with_usable_text"] / max(stats["total_pages"], 1)
        )
        log.info(
            f"  Usable text layer: {stats['pages_with_usable_text']}/"
            f"{stats['total_pages']} pages ({text_ratio:.0%})"
        )

    if stats.get("inspection_complete") is False:
        log.warning(
            "PDF inspection was incomplete; no background images can be "
            "stripped safely."
        )
        return None

    if analyze_only:
        if ratio > 0.5:
            usable_scan_pages = stats.get(
                "large_image_pages_with_usable_text", 0)
            scan_pages = stats.get("pages_with_large_images", 0)
            if scan_pages and usable_scan_pages == scan_pages:
                log.info("  DIAGNOSIS: PDF has removable background scans.")
                log.info("  Run 'preprocess' without --analyze to strip them.")
            elif usable_scan_pages:
                log.warning(
                    "  DIAGNOSIS: mixed PDF; only scan pages with reliable "
                    "text can be stripped. Other scan images must remain for OCR."
                )
            else:
                log.warning(
                    "  DIAGNOSIS: scan pages lack a reliable text layer; "
                    "keep their images and use OCR."
                )
        else:
            log.info("  PDF has few large images; stripping may not be needed.")
        return None

    if ratio < 0.1:
        log.info("  Few background images detected — skipping preprocessing.")
        return None

    if ("large_image_pages_with_usable_text" in stats
            and stats["large_image_pages_with_usable_text"] == 0):
        log.warning(
            "Large scan images were not stripped because their pages lack a "
            "reliable text layer. OCR must run against the original images."
        )
        return None

    if output_path.exists() and not force:
        log.info(f"  Preprocessed file already exists: {output_path}")
        log.info("  Use --force to overwrite.")
        return output_path

    # --- Strip ---
    log.info("Stripping background images...")
    doc = pymupdf.open(str(input_path))
    removed = 0
    try:
        plan = _ingestion_core.plan_background_image_removals(
            doc,
            min_dim,
            thresholds=_pdf_ingestion_thresholds(),
            page_text_is_usable_fn=_pdf_page_text_is_usable,
            page_background_inspection_fn=_inspect_page_background_images,
        )
        for issue in plan.issues:
            if issue.stage == "text":
                log.warning(
                    "Could not verify text before stripping PDF page %s: %s",
                    issue.page_number, issue.detail,
                )
            else:
                log.warning(
                    "Could not inspect images on PDF page %s: %s",
                    issue.page_number, issue.detail,
                )
        if not plan.complete:
            log.warning(
                "Image stripping was cancelled because not every page could "
                "be inspected safely."
            )
            return None

        if not plan.removable_xrefs:
            log.warning(
                "No background image can be removed without risking scan-only "
                "content; keeping the original PDF for OCR."
            )
            return None

        outcome = _ingestion_core.apply_background_image_removals(
            plan,
            progress_pages_fn=lambda pages: tqdm(
                pages, desc="Stripping images", unit="pg"),
        )
        for issue in outcome.deletion_issues:
            log.warning(
                "Could not remove background image %s safely: %s",
                issue.xref, issue.detail,
            )
        removed = outcome.removed_count

        if not removed:
            log.warning("No background images were removed; keeping original PDF.")
            return None

        _storage_policy.atomic_publish_private_file(
            output_path,
            lambda staging: doc.save(
                str(staging), garbage=4, deflate=True, clean=True),
        )
    finally:
        doc.close()

    orig_mb = input_path.stat().st_size / 1e6
    new_mb = output_path.stat().st_size / 1e6
    log.info(f"Stripped {removed} images: {orig_mb:.1f} MB -> {new_mb:.1f} MB "
             f"({100*(1 - new_mb/orig_mb):.0f}% smaller)")
    log.info(f"Preprocessed PDF -> {output_path}")

    return output_path


# ---------------------------------------------------------------------------
# Step 1: Convert PDF to DoclingDocument
# ---------------------------------------------------------------------------

def _detect_gpu() -> tuple:
    """Detect GPU and return (device_enum, batch_size, device_name).

    VRAM-tiered batch sizing:
      ≤8 GB  → 8   (RTX 5060 / 4060)
      ≤12 GB → 16  (RTX 5070 / 4070)
      ≤16 GB → 32  (RTX 5060 Ti 16GB / 4080)
      ≤24 GB → 64  (RTX 4090)
      >24 GB → 128 (RTX 5090 / A100)
    """
    try:
        import torch
        from docling.datamodel.accelerator_options import AcceleratorDevice
    except ImportError:
        return None, 4, "no-torch"

    if not torch.cuda.is_available():
        log.warning("CUDA not available — falling back to CPU")
        log.warning("  Fix: pip install torch --index-url https://download.pytorch.org/whl/cu128")
        return AcceleratorDevice.CPU, 4, "cpu"

    gpu_name = torch.cuda.get_device_name(0)
    cap = torch.cuda.get_device_capability(0)
    vram_gb = torch.cuda.get_device_properties(0).total_memory / 1024**3

    log.info(f"GPU detected: {gpu_name}")
    log.info(f"  Compute: sm_{cap[0]*10 + (cap[1]*10 if cap[1] >= 10 else cap[1])}0  "
             f"VRAM: {vram_gb:.1f} GB  CUDA: {torch.version.cuda}")

    tiers = [(8, 8), (12, 16), (16, 32), (24, 64)]
    batch_size = 128
    for threshold, bs in tiers:
        if vram_gb <= threshold:
            batch_size = bs
            break
    log.info(f"  Auto batch_size={batch_size} (override with --batch-size)")

    return AcceleratorDevice.CUDA, batch_size, gpu_name


def _conversion_parameters(*, batch_size_override: int | None,
                           backend: str, auto_preprocess: bool,
                           ocr: bool | None,
                           watermark: re.Pattern | None) -> dict:
    return {
        "batch_size_override": batch_size_override,
        "backend": backend,
        "auto_preprocess": auto_preprocess,
        "ocr": ocr,
        "watermark_pattern": watermark.pattern if watermark else None,
        "watermark_flags": watermark.flags if watermark else None,
        "model_artifact_lock_sha256": _model_artifact_lock_sha256(),
    }


def _pin_docling_layout_revision(pipeline_options) -> str | None:
    """Replace Docling's mutable layout ref with the reviewed commit hash."""
    layout_options = pipeline_options.layout_options
    model_spec = layout_options.model_spec
    pinned = _model_artifacts.pinned_model_kwargs(
        model_spec.repo_id, trust_remote_code=False)
    revision = pinned.get("revision")
    if revision is None:
        return None
    layout_options.model_spec = model_spec.model_copy(
        update={"revision": revision})
    return revision


def _configure_docling_model_artifacts(
        pipeline_options, *, include_ocr: bool,
        security_policy: (
            _release_security.ReleaseSecurityPolicy | None) = None,
) -> Path:
    """Force Docling onto verified local models and deterministic modes."""
    from docling.datamodel.pipeline_options import (
        RapidOcrOptions,
        TableFormerMode,
        TableStructureOptions,
    )

    policy = _effective_security_policy(security_policy)
    allow_download = (
        policy.model_download_policy == "allow-reviewed-sync")
    root = _model_artifacts.verified_docling_artifact_directory(
        include_ocr=include_ocr,
        allow_download=allow_download,
        authorize_download_fn=(
            lambda: _release_security.require_model_download(
                policy, feature="Docling model synchronization")
        ) if allow_download else None,
        **(_model_download_transport(policy) if allow_download else {}),
    )
    pipeline_options.artifacts_path = root
    pipeline_options.table_structure_options = TableStructureOptions(
        do_cell_matching=True,
        mode=TableFormerMode.ACCURATE,
    )
    if include_ocr:
        pipeline_options.ocr_options = RapidOcrOptions(
            backend="onnxruntime",
            lang=["english"],
            det_model_path=str(
                root / "RapidOcr/onnx/PP-OCRv6/det/PP-OCRv6_det_small.onnx"),
            cls_model_path=str(
                root
                / "RapidOcr/onnx/PP-OCRv4/cls/"
                  "ch_ppocr_mobile_v2.0_cls_mobile.onnx"),
            rec_model_path=str(
                root / "RapidOcr/onnx/PP-OCRv6/rec/PP-OCRv6_rec_small.onnx"),
            # Font rendering is not used by this conversion pipeline. Leaving
            # it unset avoids an unlicensed mutable ModelScope font download.
            font_path=None,
        )
    return root


def _converted_outputs_complete(
        pdf_path: Path, doc_output: Path, markdown_output: Path, *,
        parameters: dict,
        preprocessed_output: Path | None = None) -> bool:
    """Validate conversion evidence under the complete output-set lease."""
    doc_output = Path(doc_output)
    markdown_output = Path(markdown_output)
    preprocessed_path = preprocessed_output or doc_output.with_name(
        f"{doc_output.stem}_preprocessed.pdf")
    completion_path = _artifact_completion_path(
        doc_output, stage="conversion")
    with _conversion_output_lease(
            doc_output, markdown_output, preprocessed_path, completion_path):
        return _converted_outputs_complete_locked(
            pdf_path, doc_output, markdown_output,
            parameters=parameters,
            preprocessed_output=preprocessed_path,
        )


def _converted_outputs_complete_locked(
        pdf_path: Path, doc_output: Path, markdown_output: Path, *,
        parameters: dict,
        preprocessed_output: Path | None = None) -> bool:
    """Validate conversion evidence while its output-set lease is held."""
    preprocessed_path = preprocessed_output or doc_output.with_name(
        f"{doc_output.stem}_preprocessed.pdf")
    try:
        source_generation = _hash_file_generation(pdf_path)
    except (OSError, RuntimeError):
        return False
    manifest_path = _artifact_completion_path(
        doc_output, stage="conversion")
    try:
        document_raw, document_sha256, _ = (
            _read_index_artifact_snapshot(doc_output))
        document = json.loads(document_raw)
        binding = _load_conversion_source_binding(
            doc_output,
            document_sha256=document_sha256,
            document_size=len(document_raw),
        )
        if (binding is None
                or binding.source_name != Path(pdf_path).name
                or binding.source_sha256 != source_generation.sha256
                or binding.source_size != source_generation.size):
            return False
        outputs = {
            "docling_json": doc_output,
            "docling_markdown": markdown_output,
        }
        if binding.effective_input_kind == "preprocessed":
            if preprocessed_path.name != binding.effective_input_name:
                return False
            outputs["preprocessed_pdf"] = preprocessed_path
        v2_complete = _fixed_artifacts_complete(
            manifest_path, stage="conversion",
            source_sha256=source_generation.sha256,
            source_record_count=None, parameters=parameters,
            outputs=outputs,
            source_name=Path(pdf_path).name,
            schema_version=CONVERSION_COMPLETION_SCHEMA_VERSION)
        if not v2_complete:
            # Schema-v1 cannot prove immutable capture or a correctly derived
            # preprocessed input.  Regenerate it once under the v2 contract.
            return False
        if (not isinstance(document, dict)
                or not markdown_output.read_text(
                    encoding="utf-8").strip()):
            return False
        final_source = _hash_file_generation(
            pdf_path, expected_sha256=source_generation.sha256)
        return final_source.size == source_generation.size
    except (OSError, UnicodeError, json.JSONDecodeError, ValueError,
            RuntimeError):
        return False


def convert_pdf(pdf_path: Path, doc_output: Path, *,
                batch_size_override: int | None = None,
                backend: str = "pypdfium2",
                force: bool = False,
                watermark: Optional[re.Pattern] = None,
                auto_preprocess: bool = True,
                ocr: bool | None = None,
                preprocessed_output: Path | None = None,
                markdown_output: Path | None = None,
                security_policy: (
                    _release_security.ReleaseSecurityPolicy | None) = None,
                ) -> None:
    """Convert under path-wide leases for the complete artifact set."""
    doc_output = Path(doc_output)
    markdown_path = markdown_output or doc_output.with_name(
        f"{doc_output.stem}_docling.md")
    preprocessed_path = preprocessed_output or doc_output.with_name(
        f"{doc_output.stem}_preprocessed.pdf")
    completion_path = _artifact_completion_path(
        doc_output, stage="conversion")
    _run_telemetry.validate_distinct_output_paths({
        "source PDF": Path(pdf_path),
        "Docling JSON": doc_output,
        "Docling Markdown": markdown_path,
        "preprocessed PDF": preprocessed_path,
        "conversion completion": completion_path,
    })
    with _conversion_output_lease(
            doc_output, markdown_path, preprocessed_path, completion_path):
        _convert_pdf_locked(
            pdf_path, doc_output,
            batch_size_override=batch_size_override,
            backend=backend, force=force, watermark=watermark,
            auto_preprocess=auto_preprocess, ocr=ocr,
            preprocessed_output=preprocessed_output,
            markdown_output=markdown_output,
            security_policy=security_policy)


def _convert_pdf_locked(pdf_path: Path, doc_output: Path, *,
                batch_size_override: int | None = None,
                backend: str = "pypdfium2",
                force: bool = False,
                watermark: Optional[re.Pattern] = None,
                auto_preprocess: bool = True,
                ocr: bool | None = None,
                preprocessed_output: Path | None = None,
                markdown_output: Path | None = None,
                security_policy: (
                    _release_security.ReleaseSecurityPolicy | None) = None,
                ) -> None:
    """Convert one immutable PDF generation and bind its exact source."""
    source_pdf_path = Path(pdf_path)
    _require_file(source_pdf_path, "PDF file")
    md_path = markdown_output or doc_output.with_name(
        f"{doc_output.stem}_docling.md")
    preprocessed_path = preprocessed_output or doc_output.with_name(
        f"{doc_output.stem}_preprocessed.pdf")
    completion_parameters = _conversion_parameters(
        batch_size_override=batch_size_override, backend=backend,
        auto_preprocess=auto_preprocess, ocr=ocr, watermark=watermark)

    if (not force and _converted_outputs_complete_locked(
            source_pdf_path, doc_output, md_path,
            parameters=completion_parameters,
            preprocessed_output=preprocessed_path)):
        log.info(f"Conversion outputs already complete: {doc_output}")
        log.info("  Use --force to overwrite, or skip to the next step.")
        return

    with ExitStack() as snapshot_stack:
        source_snapshot = snapshot_stack.enter_context(
            _immutable_file_snapshot(source_pdf_path))
        original_input = ConversionInputBinding(
            kind="original", name=source_snapshot.source_name,
            sha256=source_snapshot.sha256, size=source_snapshot.size)
        effective_input = _convert_pdf_generation(
            source_snapshot.path, doc_output,
            snapshot_stack=snapshot_stack,
            original_input=original_input,
            batch_size_override=batch_size_override, backend=backend,
            force=force, watermark=watermark,
            auto_preprocess=auto_preprocess, ocr=ocr,
            preprocessed_output=preprocessed_path,
            markdown_output=markdown_output,
            security_policy=security_policy)
        if _cached_artifact_sha256(source_pdf_path) != source_snapshot.sha256:
            raise RuntimeError(
                f"PDF source changed while converting: {source_pdf_path}")

    conversion_outputs = {
        "docling_json": doc_output,
        "docling_markdown": md_path,
    }
    if effective_input.kind == "preprocessed":
        if effective_input.name != preprocessed_path.name:
            raise RuntimeError(
                "Preprocessed conversion input does not match its output path")
        published_generation = _hash_file_generation(
            preprocessed_path, expected_sha256=effective_input.sha256)
        if published_generation.size != effective_input.size:
            raise RuntimeError(
                "Preprocessed conversion output size changed before commit")
        conversion_outputs["preprocessed_pdf"] = preprocessed_path
    else:
        # This path is a declared, lease-protected pipeline output.  Remove a
        # prior derived generation when the current conversion used the
        # original PDF so callers never mistake stale sensitive bytes for a
        # member of the newly committed artifact set.
        try:
            preprocessed_path.unlink(missing_ok=True)
        except OSError as exc:
            raise RuntimeError(
                "Could not remove stale preprocessed conversion output: "
                f"{preprocessed_path}") from exc
    _write_artifact_completion(
        _artifact_completion_path(doc_output, stage="conversion"),
        stage="conversion", source_sha256=source_snapshot.sha256,
        source_name=source_pdf_path.name,
        source_record_count=None, parameters=completion_parameters,
        outputs=conversion_outputs,
        schema_version=CONVERSION_COMPLETION_SCHEMA_VERSION,
        extra_fields={
            "source": {
                "name": source_snapshot.source_name,
                "size": source_snapshot.size,
                "sha256": source_snapshot.sha256,
                "capture_policy": source_snapshot.capture_policy,
            },
            "effective_input": {
                "kind": effective_input.kind,
                "name": effective_input.name,
                "size": effective_input.size,
                "sha256": effective_input.sha256,
            },
        })
    if _cached_artifact_sha256(source_pdf_path) != source_snapshot.sha256:
        raise RuntimeError(
            f"PDF source changed while committing conversion: "
            f"{source_pdf_path}")


def _convert_pdf_generation(
                pdf_path: Path, doc_output: Path, *,
                snapshot_stack: ExitStack,
                original_input: "ConversionInputBinding",
                batch_size_override: int | None = None,
                backend: str = "pypdfium2",
                force: bool = False,
                watermark: Optional[re.Pattern] = None,
                auto_preprocess: bool = True,
                ocr: bool | None = None,
                preprocessed_output: Path | None = None,
                markdown_output: Path | None = None,
                security_policy: (
                    _release_security.ReleaseSecurityPolicy | None) = None,
                ) -> "ConversionInputBinding":
    """Convert an already-pinned PDF pathname generation."""
    import os

    md_path = markdown_output or doc_output.with_name(
        f"{doc_output.stem}_docling.md")
    effective_input = original_input

    # --- Assess text layer, then preprocess or OCR scans safely ---
    stats = None
    if auto_preprocess or ocr is None:
        try:
            stats = _analyze_pdf_images(pdf_path)
        except ImportError:
            log.debug(
                "PyMuPDF not installed — skipping text-layer analysis "
                "(pip install PyMuPDF)"
            )

    effective_ocr = bool(ocr)
    if ocr is None and stats is not None:
        effective_ocr = not _pdf_text_layer_is_usable(stats)
        if effective_ocr:
            log.warning(
                "PDF text layer is incomplete or low quality; enabling OCR."
            )
    elif ocr is False and stats is not None and not _pdf_text_layer_is_usable(stats):
        log.warning(
            "OCR was explicitly disabled even though the PDF text layer "
            "appears incomplete."
        )

    if auto_preprocess and stats is not None and not effective_ocr:
        private_cleaned_path: Path | None = None
        try:
            ratio = stats["pages_with_large_images"] / max(stats["total_pages"], 1)
            if ratio > 0.1:
                log.info(f"Detected background scans on {stats['pages_with_large_images']}"
                         f"/{stats['total_pages']} pages — auto-preprocessing")
                published_cleaned_path = preprocessed_output or doc_output.with_name(
                    f"{doc_output.stem}_preprocessed.pdf")
                # Keep owned scratch trees flat so stale cleanup never has to
                # traverse a replaceable directory component.
                private_cleaned_path = (
                    pdf_path.parent / f".rag-preprocess-{uuid4().hex}.pdf")
                cleaned = preprocess_pdf(pdf_path, private_cleaned_path,
                                         force=True, _analysis_cache=stats)
                if cleaned:
                    cleaned_snapshot = snapshot_stack.enter_context(
                        _immutable_file_snapshot(
                            cleaned,
                            snapshot_name=published_cleaned_path.name,
                        ))
                    _storage_policy.atomic_publish_private_file(
                        published_cleaned_path,
                        lambda staging: shutil.copyfile(
                            cleaned_snapshot.path, staging),
                    )
                    pdf_path = cleaned_snapshot.path
                    effective_input = ConversionInputBinding(
                        kind="preprocessed",
                        name=published_cleaned_path.name,
                        sha256=cleaned_snapshot.sha256,
                        size=cleaned_snapshot.size,
                    )
        except ImportError:
            log.debug(
                "PyMuPDF not installed — skipping auto-preprocess "
                "(pip install PyMuPDF)"
            )
        finally:
            if private_cleaned_path is not None:
                try:
                    private_cleaned_path.unlink(missing_ok=True)
                except OSError as exc:
                    _log_cleanup_error(
                        "Could not remove private preprocessed PDF %s",
                        private_cleaned_path, error=exc)
    elif (auto_preprocess and stats is not None and effective_ocr
          and stats.get("pages_with_large_images", 0)):
        log.info(
            "Keeping page images because OCR is enabled; preprocessing would "
            "remove OCR source pixels."
        )

    if backend == "pypdfium2":
        os.environ["DOCLING_PDF_BACKEND"] = "pypdfium2"
        log.info("Backend: pypdfium2")
    elif backend != "auto":
        log.warning(f"Unknown backend '{backend}', using auto")

    # Validate/read the source before importing Docling's heavyweight runtime.
    # This also keeps preprocessing and page-count failures deterministic.
    total_pages = _page_count(pdf_path)

    from docling.document_converter import DocumentConverter, PdfFormatOption
    from docling.datamodel.pipeline_options import (
        PdfPipelineOptions,
        ThreadedPdfPipelineOptions,
    )
    from docling.datamodel.accelerator_options import (
        AcceleratorDevice,
        AcceleratorOptions,
    )
    from docling.datamodel.base_models import InputFormat

    log.info(f"Converting {pdf_path.name} ({pdf_path.stat().st_size / 1e6:.1f} MB, {total_pages} pages)")

    # --- GPU detection ---
    device_enum, auto_batch, gpu_name = _detect_gpu()
    batch_size = batch_size_override or auto_batch
    use_gpu = device_enum is not None and device_enum != AcceleratorDevice.CPU

    # --- Shared pipeline options ---
    common_opts = dict(
        do_ocr=effective_ocr,
        generate_page_images=False,
        generate_picture_images=False,
        do_picture_classification=False,
    )
    log.info(f"OCR: {'enabled' if effective_ocr else 'disabled'}")

    if use_gpu:
        log.info(f"Mode: GPU ({gpu_name}), layout_batch_size={batch_size}")
        pipeline_opts = ThreadedPdfPipelineOptions(
            accelerator_options=AcceleratorOptions(device=AcceleratorDevice.CUDA),
            layout_batch_size=batch_size,
            table_batch_size=4,
            ocr_batch_size=batch_size,
            queue_max_size=8,
            **common_opts,
        )
    else:
        log.info(f"Mode: CPU ({total_pages} pages — this may take a while)")
        pipeline_opts = PdfPipelineOptions(**common_opts)

    if revision := _pin_docling_layout_revision(pipeline_opts):
        log.info(f"Docling layout revision: {revision}")
    artifacts_root = _configure_docling_model_artifacts(
        pipeline_opts, include_ocr=effective_ocr,
        security_policy=security_policy)
    log.info(f"Docling verified model artifacts: {artifacts_root}")

    converter = DocumentConverter(
        format_options={
            InputFormat.PDF: PdfFormatOption(pipeline_options=pipeline_opts),
        },
    )

    # --- Progress bar via log interception ---
    from tqdm import tqdm
    import threading

    pbar = tqdm(total=total_pages, desc="Converting", unit="pg",
                bar_format="{l_bar}{bar}| {n_fmt}/{total_fmt} pages "
                           "[{elapsed}<{remaining}, {rate_fmt}]")
    _seen = {"n": 0, "lock": threading.Lock()}

    class _ProgressHandler(logging.Handler):
        def emit(self, record):
            m = re.search(r"pages \[([^\]]+)\]", record.getMessage())
            if m:
                count = len(m.group(1).split(","))
                with _seen["lock"]:
                    _seen["n"] += count
                    pbar.update(count)

    handler = _ProgressHandler()
    handler.setLevel(logging.DEBUG)
    dl_logger = logging.getLogger("docling.pipeline.standard_pdf_pipeline")
    dl_logger.addHandler(handler)
    dl_logger.setLevel(logging.DEBUG)

    # --- Run conversion ---
    t0 = time.time()
    try:
        result = converter.convert(str(pdf_path))
    except RuntimeError as e:
        if "out of memory" in str(e).lower():
            log.error(f"CUDA out of memory! Current batch_size={batch_size}")
            log.error(f"  Retry with: --batch-size {max(1, batch_size // 2)}")
            sys.exit(1)
        raise
    elapsed = time.time() - t0

    # Ensure bar reaches 100%
    with _seen["lock"]:
        remaining = total_pages - _seen["n"]
        if remaining > 0:
            pbar.update(remaining)
    pbar.close()
    dl_logger.removeHandler(handler)

    log.info(f"Conversion complete in {elapsed:.0f}s "
             f"({total_pages / elapsed:.1f} pages/sec)")

    if use_gpu:
        import torch
        peak_mb = torch.cuda.max_memory_allocated(0) / 1024**2
        total_mb = torch.cuda.get_device_properties(0).total_memory / 1024**2
        log.info(f"Peak GPU memory: {peak_mb:.0f} MB / {total_mb:.0f} MB")

    dl_doc = result.document

    # Persist DoclingDocument
    dl_doc_json = dl_doc.model_dump_json(indent=2)
    _atomic_write_text(doc_output, dl_doc_json)
    log.info(f"DoclingDocument saved → {doc_output} ({len(dl_doc_json) / 1e6:.1f} MB)")

    # Markdown export — normalize encoding + strip watermark
    md_text = _normalize_text(strip_watermark(dl_doc.export_to_markdown(), watermark))
    _atomic_write_text(md_path, md_text)
    log.info(f"Markdown export  → {md_path}")
    return effective_input


# ---------------------------------------------------------------------------
# Step 2: Chunk + Enrich Metadata
# ---------------------------------------------------------------------------

_ZS_LABELS = [
    "judicial court opinion with legal reasoning",
    "study questions and discussion prompts",
    "textbook author explanation and commentary",
    "statutory text or legal rule",
    "data table or chart",
    "footnote with citations",
    "chapter introduction and overview",
]
_ZS_LABEL_MAP = {
    "judicial court opinion with legal reasoning": "case_opinion",
    "study questions and discussion prompts": "notes_and_questions",
    "textbook author explanation and commentary": "author_narrative",
    "statutory text or legal rule": "statutory_excerpt",
    "data table or chart": "table",
    "footnote with citations": "footnote",
    "chapter introduction and overview": "chapter_introduction",
}

_WHITESPACE_RE = _chunking_core._WHITESPACE_RE
_NEWLINES_RE = _chunking_core._NEWLINES_RE
_FP_RE = _chunking_core._FP_RE
_THINK_TAG_RE = _retrieval_core._THINK_TAG_RE
_HEADER_FOOTER_RE = _chunking_core._HEADER_FOOTER_RE

NOTES_Q_RE = _chunking_core.NOTES_Q_RE
# Matches "Chapter 3 · Title" OR "3  ·  PERSONAL JURISDICTION" (Docling heading format)
CHAPTER_RE = _chunking_core.CHAPTER_RE
# Fallback: ALL-CAPS chapter headings like "CHAPTER 5  VENUE" or "5  VENUE"
CHAPTER_CAPS_RE = re.compile(
    r"^(?:CHAPTER\s+)?(\d{1,2})\s{2,}([A-Z][A-Z\s,]{4,})$",
    re.MULTILINE,
)
SECTION_RE = re.compile(r"^([A-G])\.\s+(.+)$", re.MULTILINE)
CASE_EXTRACT_RE = _chunking_core.CASE_EXTRACT_RE


# Footnote markers: superscript-style numbering at start of lines
_FOOTNOTE_NUM_RE = _chunking_core._FOOTNOTE_NUM_RE
# Legal citation shorthand common in footnotes
_FOOTNOTE_CITE_MARKERS = _chunking_core._FOOTNOTE_CITE_MARKERS


def classify_content_type(
        text: str, headings: list[str] | None, *,
        structure_profile: (
            str | _document_profiles.StructureProfile
        ) = DEFAULT_STRUCTURE_PROFILE,
) -> str:
    """Classify through the facade's current structural-content helper."""
    profile = _document_profiles.get_profile(structure_profile)

    def structural_content(value: str, values: list[str] | None) -> bool:
        if profile.name == DEFAULT_STRUCTURE_PROFILE:
            return _is_structural_content(value, values)
        return _chunking_core._is_structural_content(
            value, values,
            structural_patterns=(
                _document_profiles.structural_heading_patterns(profile)))

    return _chunking_core.classify_content_type(
        text, headings,
        structural_content_fn=structural_content,
        chapter_heading_fn=lambda heading: (
            _document_profiles.match_division(
                heading, profile, "chunk_heading") is not None),
    )


extract_case_names = _chunking_core.extract_case_names


build_section_path = _chunking_core.build_section_path


estimate_page_range = _chunking_core.estimate_page_range


def enrich_chunk(chunk_text: str, headings: list[str] | None,
                 chunk_index: int, total_chunks: int,
                 total_pages: int,
                 doc_items: list | None = None,
                 source_file: str = "",
                 source_items: list[dict] | None = None, *,
                 structure_profile: (
                     str | _document_profiles.StructureProfile
                 ) = DEFAULT_STRUCTURE_PROFILE) -> dict:
    """Build an enriched chunk record with legal-textbook metadata."""
    profile = _document_profiles.get_profile(structure_profile)
    content_type = classify_content_type(
        chunk_text, headings, structure_profile=profile)
    case_names = extract_case_names(chunk_text)
    section_path = build_section_path(headings)

    chapter_num = None
    chapter_title = None
    for h in (headings or []):
        h_clean = _normalize_text(h)
        division = _document_profiles.match_division(
            h_clean, profile, "chunk_heading")
        if division is not None:
            chapter_num = division.ordinal
            chapter_title = division.title.title()
            break
    # Fallback: scan section_path for chapter numbers
    if chapter_num is None and headings:
        sp = build_section_path(headings)
        sp_clean = _normalize_text(sp)
        division = _document_profiles.match_division(
            sp_clean, profile, "chunk_heading")
        if division is not None:
            chapter_num = division.ordinal
            chapter_title = division.title.title()

    page_numbers = []
    if doc_items:
        for item in doc_items:
            if hasattr(item, "prov") and item.prov:
                for prov in item.prov:
                    if hasattr(prov, "page_no"):
                        page_numbers.append(prov.page_no)
    page_range = (
        f"pp.{min(page_numbers)}-{max(page_numbers)}"
        if page_numbers
        else estimate_page_range(chunk_index, total_chunks, total_pages)
    )

    cross_refs = []
    for division in _document_profiles.find_divisions(
            chunk_text, profile, "cross_reference"):
        prefix = "Ch" if division.kind.casefold() == "chapter" else division.kind
        number = (
            str(division.ordinal)
            if division.kind.casefold() == "chapter" else division.raw_number)
        cross_refs.append(
            f"{prefix}.{number}"
            + (f".{division.title}" if division.title else ""))

    content_source_map = {
        "case_opinion": "body",
        "footnote": "footnote",
        "table": "table",
        "structural": "structural",
    }
    content_source = content_source_map.get(content_type, "body")

    # Table metadata
    table_rows: int | None = None
    table_cols: int | None = None
    if content_type == "table":
        chunk_lines = [line for line in chunk_text.split("\n") if line.strip()]
        # Estimate rows: non-empty, non-separator lines
        data_lines = [
            line for line in chunk_lines
            if not re.match(r"^\s*[-|+:]+\s*$", line)
        ]
        table_rows = len(data_lines)
        # Estimate cols from pipe count or tab count
        if any("|" in line for line in data_lines):
            col_counts = [
                line.count("|") - 1 for line in data_lines
                if line.count("|") >= 2
            ]
            table_cols = max(col_counts) if col_counts else 1
        elif any("\t" in line for line in data_lines):
            col_counts = [
                line.count("\t") + 1 for line in data_lines if "\t" in line
            ]
            table_cols = max(col_counts) if col_counts else 1
        else:
            # Aligned-column estimate: count distinct aligned positions
            pos_counts: dict[int, int] = {}
            for line in data_lines:
                i = 0
                while i < len(line):
                    if line[i] != " " and (i == 0 or line[i - 1] == " "):
                        pos_counts[i] = pos_counts.get(i, 0) + 1
                    i += 1
            table_cols = sum(1 for v in pos_counts.values() if v >= 3) or 1

    metadata: dict = {
        "content_type": content_type,
        "content_source": content_source,
        "source_file": source_file,
        "section_path": section_path,
        "chapter_num": chapter_num,
        "chapter_title": chapter_title,
        "case_names": case_names,
        "primary_case": case_names[0] if case_names else None,
        "page_range": page_range,
        "page_start": min(page_numbers) if page_numbers else None,
        "page_end": max(page_numbers) if page_numbers else None,
        # dict preserves first-seen order while removing duplicates.  Avoid a
        # set here because hash randomization made exported metadata vary
        # between otherwise identical runs.
        "cross_references": list(dict.fromkeys(cross_refs)),
        "headings": headings or [],
        "chunk_index": chunk_index,
        "context": "",
    }
    if source_items is not None:
        metadata["source_lineage_schema_version"] = (
            _quality_core.SOURCE_LINEAGE_SCHEMA_VERSION)
        metadata["source_items"] = source_items
    if content_type == "table":
        metadata["table_rows"] = table_rows
        metadata["table_cols"] = table_cols

    return {
        "text": chunk_text,
        "metadata": metadata,
    }


def _chunk_heading_key(record: dict) -> tuple[str, ...]:
    headings = record.get("metadata", {}).get("headings") or []
    return tuple(
        _chunking_core.clean_heading_text(str(heading)).casefold()
        for heading in headings
        if _chunking_core.clean_heading_text(str(heading))
    )


def _merge_enriched_chunk_group(
        group: list[dict], token_counter: Callable[[str], int], *,
        structure_profile: (
            str | _document_profiles.StructureProfile
        ) = DEFAULT_STRUCTURE_PROFILE,
) -> dict:
    """Merge source-adjacent records and recompute all text-derived fields."""
    merged = {
        "text": "\n\n".join(
            record["text"].strip() for record in group if record["text"].strip()),
        "metadata": dict(group[0]["metadata"]),
    }
    metadata = merged["metadata"]
    starts = [
        record["metadata"].get("page_start") for record in group
        if record["metadata"].get("page_start") is not None
    ]
    ends = [
        record["metadata"].get("page_end") for record in group
        if record["metadata"].get("page_end") is not None
    ]
    metadata["page_start"] = min(starts) if starts else None
    metadata["page_end"] = max(ends) if ends else None
    if starts and ends:
        metadata["page_range"] = f"pp.{min(starts)}-{max(ends)}"
    headings = metadata.get("headings") or []
    metadata["section_path"] = build_section_path(headings)
    metadata["content_type"] = classify_content_type(
        merged["text"], headings, structure_profile=structure_profile)
    case_names = extract_case_names(merged["text"])
    metadata["case_names"] = case_names
    metadata["primary_case"] = case_names[0] if case_names else None
    metadata["cross_references"] = list(dict.fromkeys(
        reference
        for record in group
        for reference in record["metadata"].get("cross_references", [])
    ))
    source_items: list[dict] = []
    seen_source_items: set[str] = set()
    for record in group:
        for source_item in record["metadata"].get("source_items", []):
            key = json.dumps(
                source_item, ensure_ascii=False, sort_keys=True,
                separators=(",", ":"))
            if key not in seen_source_items:
                seen_source_items.add(key)
                source_items.append(source_item)
    if source_items:
        metadata["source_lineage_schema_version"] = (
            _quality_core.SOURCE_LINEAGE_SCHEMA_VERSION)
        metadata["source_items"] = source_items
    sources = {
        record["metadata"].get("content_source", "body") for record in group
    }
    metadata["content_source"] = (
        next(iter(sources)) if len(sources) == 1 else "mixed")
    metadata["token_count"] = int(token_counter(merged["text"]))
    return merged


def _coalesce_chunk_boundaries(
        records: list[dict], token_counter: Callable[[str], int],
        max_tokens: int, *, hard_max_tokens: int | None = None,
        structure_profile: (
            str | _document_profiles.StructureProfile
        ) = DEFAULT_STRUCTURE_PROFILE,
) -> list[dict]:
    """Repair split sentences and alternating rule/explanation layout lanes."""
    if len(records) < 2:
        return records

    def _pages_touch(left: dict, right: dict) -> bool:
        left_end = left["metadata"].get("page_end")
        right_start = right["metadata"].get("page_start")
        if left_end is None or right_start is None:
            return False
        return left_end <= right_start <= left_end + 1

    def _lane(record: dict) -> str | None:
        metadata = record.get("metadata", {})
        if (metadata.get("content_source") == "table"
                or metadata.get("content_type") == "table"):
            return None
        heading = " ".join(_chunk_heading_key(record))
        opening = record.get("text", "").lstrip()[:100]
        if (re.search(r"\brule language\b|\bstatutory text\b", heading)
                or re.match(r"rule language\s*\**(?:\s|$)", opening, re.I)):
            return "rule"
        if (re.search(r"\bauthors?['’] explanation\b", heading)
                or re.match(
                    r"authors?['’] explanation\s*\**(?:\s|$)",
                    opening,
                    re.I,
                )):
            return "explanation"
        return None

    def _pack(group: list[dict]) -> list[dict]:
        packed: list[dict] = []
        current: list[dict] = []
        for record in group:
            candidate = current + [record]
            combined_text = "\n\n".join(item["text"] for item in candidate)
            if current and token_counter(combined_text) > max_tokens:
                packed.append(_merge_enriched_chunk_group(
                    current, token_counter,
                    structure_profile=structure_profile))
                current = [record]
            else:
                current = candidate
        if current:
            packed.append(_merge_enriched_chunk_group(
                current, token_counter,
                structure_profile=structure_profile))
        return packed

    # Reconstruct alternating two-column source layout as two coherent lanes.
    lane_repaired: list[dict] = []
    index = 0
    while index < len(records):
        if _lane(records[index]) is None:
            lane_repaired.append(records[index])
            index += 1
            continue
        end = index + 1
        while (end < len(records)
               and _lane(records[end]) is not None
               and _pages_touch(records[end - 1], records[end])):
            end += 1
        run = records[index:end]
        keys = list(dict.fromkeys(
            (_lane(record), _chunk_heading_key(record)) for record in run))
        # Source columns may be extracted in either visual order. Publish the
        # complete rule lane before its explanation consistently.
        keys.sort(key=lambda item: 0 if item[0] == "rule" else 1)
        if len(run) >= 3 and len(keys) >= 2:
            for lane, key in keys:
                lane_repaired.extend(_pack([
                    record for record in run
                    if (_lane(record), _chunk_heading_key(record)) == (lane, key)
                ]))
        else:
            lane_repaired.extend(run)
        index = end

    def _edge_footnotes(text: str, *, leading: bool) -> tuple[str, list[str]]:
        lines = text.splitlines()
        footnotes: list[str] = []
        if leading:
            while lines and _FOOTNOTE_NUM_RE.match(lines[0]):
                footnotes.append(lines.pop(0))
        else:
            while lines and _FOOTNOTE_NUM_RE.match(lines[-1]):
                footnotes.insert(0, lines.pop())
        return "\n".join(lines).strip(), footnotes

    def _continues_sentence(left: str, right: str) -> bool:
        left_core = left.rstrip().rstrip('"\'\u2019\u201d)]}')
        right_core = right.lstrip()
        if not left_core or not right_core:
            return False
        if left_core.endswith((".", "?", "!")):
            return False
        return (right_core[0].islower()
                or right_core[0] in ",.;:)]}'\"’”"
                or left_core.endswith(("-", "–", "—", ",", ";", ":")))

    # Merge ordinary same-heading chunks only when the boundary is visibly a
    # sentence continuation and the configured token cap remains satisfied.
    repaired: list[dict] = []
    for record in lane_repaired:
        if not repaired:
            repaired.append(record)
            continue
        previous = repaired[-1]
        same_heading = _chunk_heading_key(previous) == _chunk_heading_key(record)
        non_table = all(
            item["metadata"].get("content_type") != "table"
            for item in (previous, record)
        )
        left_body, trailing_footnotes = _edge_footnotes(
            previous["text"], leading=False)
        right_body, leading_footnotes = _edge_footnotes(
            record["text"], leading=True)
        reordered_text = f"{left_body.rstrip()} {right_body.lstrip()}".strip()
        boundary_footnotes = trailing_footnotes + leading_footnotes
        if boundary_footnotes:
            reordered_text += "\n\n" + "\n".join(boundary_footnotes)
        combined_limit = max_tokens + max(128, max_tokens // 4)
        if hard_max_tokens is not None:
            combined_limit = min(combined_limit, hard_max_tokens)
        if (same_heading and non_table and _pages_touch(previous, record)
                and _continues_sentence(left_body, right_body)
                and token_counter(reordered_text) <= combined_limit):
            previous_copy = {
                "text": reordered_text,
                "metadata": dict(previous["metadata"]),
            }
            record_copy = {
                "text": "",
                "metadata": dict(record["metadata"]),
            }
            repaired[-1] = _merge_enriched_chunk_group(
                [previous_copy, record_copy], token_counter,
                structure_profile=structure_profile)
        else:
            repaired.append(record)
    return repaired


def _validate_chunk_structure_for_publication(
        records: list[dict], *, scaffold: list[dict],
        book_sections: dict, chapter_map: dict[int, dict],
        chapter_titles: dict[int, str],
        structure_profile: (
            str | _document_profiles.StructureProfile
        ) = DEFAULT_STRUCTURE_PROFILE,
) -> None:
    """Reject a corpus with leaked structural pages or corrupt hierarchy."""
    profile = _document_profiles.get_profile(structure_profile)
    structural_names = _document_profiles.structural_section_keys(profile)
    structural_ranges = {
        (value["start"], value["end"])
        for name in structural_names
        if (value := book_sections.get(name)) is not None
    }

    starts: dict[int, int] = {}
    for entry in scaffold:
        chapter_num = entry.get("chapter_num")
        page = entry.get("page")
        if entry.get("level") == 1 and chapter_num is not None and page:
            starts.setdefault(chapter_num, page)
    for chapter_num, info in chapter_map.items():
        page = info.get("min_page")
        if page:
            starts[chapter_num] = min(starts.get(chapter_num, page), page)

    ordered_starts = sorted((page, chapter) for chapter, page in starts.items())
    bounds: dict[int, tuple[int, int]] = {}
    first_backmatter = min(
        (start for start, _ in structural_ranges
         if not ordered_starts or start > ordered_starts[-1][0]),
        default=0,
    )
    for index, (start, chapter_num) in enumerate(ordered_starts):
        if index + 1 < len(ordered_starts):
            end = ordered_starts[index + 1][0] - 1
        else:
            observed_end = chapter_map.get(chapter_num, {}).get("max_page", start)
            end = first_backmatter - 1 if first_backmatter else observed_end
        bounds[chapter_num] = (start, end)

    issues: list[str] = []
    for index, record in enumerate(records):
        metadata = record.get("metadata", {})
        text = record.get("text", "")
        page_start = metadata.get("page_start")
        page_end = metadata.get("page_end")
        if page_start is not None and page_end is not None and any(
                start <= page_start and page_end <= end
                for start, end in structural_ranges):
            issues.append(
                f"chunk {index} retains structural pp.{page_start}-{page_end}")

        if re.search(r"(?<=[A-Za-z0-9])-\s+(?=[A-Za-z0-9])", text):
            issues.append(f"chunk {index} retains a split-hyphen artifact")
        if re.search(
                r"https?://\s|\bwww\s+\.|\bperma\.cc/\s", text, re.I):
            issues.append(f"chunk {index} retains a split URL")
        if "This and other authors' explanations draw" in text:
            issues.append(f"chunk {index} retains editorial boilerplate")
        if re.search(
                r"\b(?:clientlawyer|lawyerclient|plaintiffdefendant|"
                r"threejudge|Aconcluding)\b", text, re.I):
            issues.append(f"chunk {index} retains a known fused term")

        headings_text = " ".join(metadata.get("headings") or [])
        content_type = metadata.get("content_type")
        if re.search(
                r"(?:\s*[.\u2026·]){3,}",
                metadata.get("section_path", "")):
            issues.append(f"chunk {index} section path contains TOC leaders")
        content_source = metadata.get("content_source")
        source_is_table = content_source == "table"
        source_is_nonprose = content_source in {"table", "footnote"}
        explicit_rule_lane = (
            bool(re.search(r"\brule language\b", headings_text, re.I))
            or bool(re.match(
                r"\s*rule language\s*\**(?:\s|$)", text, re.I)))
        explicit_explanation_lane = (
            bool(re.search(
                r"\bauthors?['’] explanation\b", headings_text, re.I))
            or bool(re.match(
                r"\s*authors?['’] explanation\s*\**(?:\s|$)",
                text,
                re.I,
            )))
        if (explicit_rule_lane
                and content_type != "statutory_excerpt"
                and not source_is_nonprose):
            issues.append(f"chunk {index} misclassifies explicit rule language")
        if (explicit_explanation_lane
                and content_type != "author_narrative"
                and not source_is_nonprose):
            issues.append(f"chunk {index} misclassifies author explanation")
        if (content_type == "footnote"
                and content_source != "footnote"
                and not _FOOTNOTE_NUM_RE.match(text)
                ):
            issues.append(f"chunk {index} footnote does not open as a footnote")
        if source_is_table:
            if content_type != "table":
                issues.append(f"chunk {index} source table is not classified as table")
            if not any(line.lstrip().startswith("|") for line in text.splitlines()):
                issues.append(f"chunk {index} source table lost its Markdown header")
        for case_name in metadata.get("case_names", []):
            if (len(case_name) > 160
                    or not (" v. " in case_name
                            or case_name.startswith(("In re ", "Ex parte ")))):
                issues.append(f"chunk {index} has an invalid case entity")

        chapter_num = metadata.get("chapter_num")
        if chapter_num is None:
            continue
        expected_title = chapter_titles.get(chapter_num)
        actual_title = metadata.get("chapter_title")
        if not expected_title or actual_title != expected_title:
            issues.append(
                f"chunk {index} chapter {chapter_num} has noncanonical title")
        actual_division = (
            _document_profiles.match_division(
                actual_title, profile, "canonical_title")
            if actual_title else None
        )
        if actual_title and (
                re.search(r"(?:\s*[.\u2026·]){3,}", actual_title)
                or (actual_division is not None
                    and _document_profiles.contains_division_subnumber(
                        actual_title, actual_division))):
            issues.append(f"chunk {index} chapter title contains TOC artifacts")
        section_path = metadata.get("section_path", "")
        if expected_title and not section_path.startswith(expected_title):
            issues.append(
                f"chunk {index} section path does not start with chapter title")
        if (page_start is not None and page_end is not None
                and chapter_num in bounds):
            lower, upper = bounds[chapter_num]
            if page_start < lower or page_end > upper:
                issues.append(
                    f"chunk {index} chapter {chapter_num} lies outside "
                    f"pp.{lower}-{upper}: pp.{page_start}-{page_end}")

    if issues:
        preview = "; ".join(issues[:10])
        suffix = f"; and {len(issues) - 10} more" if len(issues) > 10 else ""
        raise RuntimeError(
            f"Structural metadata quality gate failed: {preview}{suffix}")


def _split_markdown_table_by_rows(
        markdown: str, token_counter: Callable[[str], int],
        max_tokens: int) -> list[str]:
    """Pack source table rows into independently valid Markdown tables.

    Docling's hybrid chunker sizes a flattened table representation. Restoring
    the richer source Markdown can therefore exceed the embedding limit even
    when the original raw chunk fit. Repeat the header and separator for each
    row group so every published child remains understandable and retrievable.
    A single row that cannot fit is left intact for the exact model-input gate
    to reject rather than being silently truncated or converted to prose.
    """
    markdown = markdown.strip()
    if not markdown or token_counter(markdown) <= max_tokens:
        return [markdown] if markdown else []
    table = _table_retrieval_core._parse_markdown_table(markdown)
    if table is None:
        return [markdown]

    preamble = list(table.preamble)
    header = [table.header, table.separator]
    groups: list[list[str]] = []
    current_rows: list[str] = []
    for row in table.rows:
        candidate = "\n".join(preamble + header + current_rows + [row])
        if current_rows and token_counter(candidate) > max_tokens:
            groups.append(current_rows)
            current_rows = [row]
        else:
            current_rows.append(row)
    if current_rows:
        groups.append(current_rows)
    return ["\n".join(preamble + header + rows) for rows in groups]


def _doc_item_label(item) -> str:
    """Return a stable lowercase Docling item label."""
    label = getattr(item, "label", "")
    return str(getattr(label, "value", label)).lower()


def _docling_lineage_catalog(
        dl_doc,
) -> tuple[dict[str, object], dict[str, set[str]], dict[str, list[str]]]:
    """Index Docling items and their source-level parent relationships."""
    item_by_ref: dict[str, object] = {}
    for collection_name in (
            "texts", "pictures", "tables", "key_value_items", "form_items"):
        for item in getattr(dl_doc, collection_name, []) or []:
            ref = str(getattr(item, "self_ref", ""))
            if ref:
                item_by_ref[ref] = item

    parent_refs_by_child: dict[str, set[str]] = {}
    caption_refs_by_parent: dict[str, list[str]] = {}
    for parent_ref, item in item_by_ref.items():
        direct_parent = str(getattr(
            getattr(item, "parent", None), "cref", ""))
        if direct_parent in item_by_ref:
            parent_refs_by_child.setdefault(parent_ref, set()).add(
                direct_parent)
        for relationship in ("captions", "footnotes", "children"):
            child_refs = [
                str(getattr(reference, "cref", ""))
                for reference in (getattr(item, relationship, None) or [])
            ]
            child_refs = [ref for ref in child_refs if ref in item_by_ref]
            for child_ref in child_refs:
                parent_refs_by_child.setdefault(child_ref, set()).add(
                    parent_ref)
            if relationship == "captions" and child_refs:
                caption_refs_by_parent[parent_ref] = list(dict.fromkeys(
                    child_refs))
    return item_by_ref, parent_refs_by_child, caption_refs_by_parent


def _finite_source_coordinate(value: object) -> float | None:
    try:
        coordinate = float(value)
    except (TypeError, ValueError):
        return None
    if not math.isfinite(coordinate):
        return None
    return round(coordinate, 3)


def _source_lineage_for_items(
        doc_items: list | None, *, item_by_ref: dict[str, object],
        parent_refs_by_child: dict[str, set[str]],
        caption_refs_by_parent: dict[str, list[str]],
) -> list[dict]:
    """Serialize deterministic source identities and page/geometry spans."""
    refs = [
        str(getattr(item, "self_ref", ""))
        for item in (doc_items or [])
        if getattr(item, "self_ref", "")
    ]
    for parent_ref in list(refs):
        refs.extend(caption_refs_by_parent.get(parent_ref, []))
    refs = list(dict.fromkeys(refs))

    source_items = []
    for ref in refs:
        item = item_by_ref.get(ref)
        if item is None:
            continue
        spans = []
        for provenance in getattr(item, "prov", None) or []:
            page = getattr(provenance, "page_no", None)
            if (not isinstance(page, int) or isinstance(page, bool)
                    or page < 1):
                continue
            span: dict[str, object] = {"page": page}
            bbox = getattr(provenance, "bbox", None)
            if bbox is not None:
                coordinates = [
                    _finite_source_coordinate(getattr(bbox, name, None))
                    for name in ("l", "t", "r", "b")
                ]
                if all(value is not None for value in coordinates):
                    span["bbox"] = coordinates
                    origin = str(getattr(
                        getattr(bbox, "coord_origin", ""), "value",
                        getattr(bbox, "coord_origin", "")))
                    if origin:
                        span["origin"] = origin.upper()
            spans.append(span)
        spans.sort(key=lambda value: (
            value["page"], value.get("bbox", []), value.get("origin", "")))
        source_items.append({
            "ref": ref,
            "label": _doc_item_label(item),
            "parent_refs": sorted(parent_refs_by_child.get(ref, set())),
            "spans": spans,
        })
    return source_items


@dataclass(frozen=True, slots=True)
class ConversionInputBinding:
    """Hash-bound original or derived PDF input used by conversion."""

    kind: str
    name: str
    sha256: str
    size: int


@dataclass(frozen=True, slots=True)
class ConversionSourceBinding:
    """Strict capture proof loaded from a conversion-v2 completion."""

    manifest_path: Path
    manifest_sha256: str
    schema_version: int
    capture_verified: bool
    source_name: str
    source_sha256: str
    source_size: int
    effective_input_kind: str
    effective_input_name: str
    effective_input_sha256: str
    effective_input_size: int


@dataclass(frozen=True, slots=True)
class TableRecoverySource:
    """Private PDF generation and conversion proof used for table checks."""

    pdf: _artifact_io.ImmutableFileSnapshot
    conversion: ConversionSourceBinding
    discovery: str
    source_path: Path

    def manifest_input(self) -> dict:
        return {
            "pdf": {
                "name": self.pdf.source_name,
                "size": self.pdf.size,
                "sha256": self.pdf.sha256,
                "capture_policy": self.pdf.capture_policy,
            },
            "conversion_manifest": {
                "name": self.conversion.manifest_path.name,
                "sha256": self.conversion.manifest_sha256,
                "schema_version": self.conversion.schema_version,
            },
            "discovery": self.discovery,
        }


def _strict_json_object(raw: bytes, *, description: str) -> dict:
    """Decode one UTF-8 JSON object while rejecting ambiguous syntax."""
    def reject_constant(value: str):
        raise ValueError(f"invalid JSON numeric constant: {value}")

    def reject_duplicate_keys(pairs):
        result = {}
        for key, value in pairs:
            if key in result:
                raise ValueError(f"duplicate {description} field: {key}")
            result[key] = value
        return result

    try:
        payload = json.loads(
            raw.decode("utf-8"),
            parse_constant=reject_constant,
            object_pairs_hook=reject_duplicate_keys,
        )
    except (UnicodeError, json.JSONDecodeError) as exc:
        raise ValueError(f"cannot parse {description}") from exc
    if not isinstance(payload, dict):
        raise ValueError(f"{description} must be a JSON object")
    return payload


def _valid_manifest_file_record(value: object) -> bool:
    if not isinstance(value, dict) or set(value) != {
            "name", "size", "sha256"}:
        return False
    name = value.get("name")
    size = value.get("size")
    digest = value.get("sha256")
    return (
        isinstance(name, str) and bool(name) and Path(name).name == name
        and isinstance(size, int) and not isinstance(size, bool) and size > 0
        and isinstance(digest, str)
        and re.fullmatch(r"[0-9a-f]{64}", digest) is not None
    )


def _load_conversion_source_binding(
        doc_path: Path, *, document_sha256: str,
        document_size: int) -> ConversionSourceBinding | None:
    """Load strict v2 capture proof for one already-snapshotted document.

    Legacy v1 completion files are readable migration inputs, but are returned
    as unverified and force one rebuild before they can authorize source-PDF
    table recovery or resume.
    """
    manifest_path = _artifact_completion_path(doc_path, stage="conversion")
    try:
        raw, manifest_sha256, _ = _read_index_artifact_snapshot(
            manifest_path, max_bytes=_MAX_CONVERSION_MANIFEST_BYTES)
    except FileNotFoundError:
        return None
    payload = _strict_json_object(raw, description="conversion completion")
    if payload.get("schema_version") != CONVERSION_COMPLETION_SCHEMA_VERSION:
        return None
    expected_root_fields = {
        "schema_version", "stage", "source_sha256", "source_name",
        "source_record_count", "parameters_sha256", "outputs", "source",
        "effective_input",
    }
    if set(payload) != expected_root_fields:
        raise ValueError("conversion completion has an invalid field set")
    if (payload.get("stage") != "conversion"
            or payload.get("source_record_count") is not None
            or not isinstance(payload.get("parameters_sha256"), str)
            or re.fullmatch(
                r"[0-9a-f]{64}", payload["parameters_sha256"]) is None):
        raise ValueError("conversion completion header is invalid")

    source = payload.get("source")
    if (not isinstance(source, dict)
            or set(source) != {"name", "size", "sha256", "capture_policy"}
            or source.get("capture_policy") != _CONVERSION_CAPTURE_POLICY
            or not _valid_manifest_file_record({
                key: source.get(key) for key in ("name", "size", "sha256")
            })):
        raise ValueError("conversion completion source binding is invalid")
    if (payload.get("source_name") != source["name"]
            or payload.get("source_sha256") != source["sha256"]):
        raise ValueError("conversion completion source fields disagree")

    effective_input = payload.get("effective_input")
    if (not isinstance(effective_input, dict)
            or set(effective_input) != {"kind", "name", "size", "sha256"}
            or effective_input.get("kind") not in {"original", "preprocessed"}
            or not _valid_manifest_file_record({
                key: effective_input.get(key)
                for key in ("name", "size", "sha256")
            })):
        raise ValueError(
            "conversion completion effective-input binding is invalid")
    if (effective_input["kind"] == "original"
            and any(effective_input[key] != source[key]
                    for key in ("name", "size", "sha256"))):
        raise ValueError("original effective input must equal captured source")

    records = payload.get("outputs")
    if not isinstance(records, list):
        raise ValueError("conversion completion outputs are invalid")
    expected_roles = {"docling_json", "docling_markdown"}
    if effective_input["kind"] == "preprocessed":
        expected_roles.add("preprocessed_pdf")
    if (len(records) != len(expected_roles)
            or any(not isinstance(record, dict)
                   or set(record) != {"role", "name", "size", "sha256"}
                   or not isinstance(record.get("role"), str)
                   or record["role"] not in expected_roles
                   or not _valid_manifest_file_record({
                       key: record.get(key)
                       for key in ("name", "size", "sha256")
                   }) for record in records)):
        raise ValueError("conversion completion output records are invalid")
    by_role = {record["role"]: record for record in records}
    if len(by_role) != len(expected_roles) or set(by_role) != expected_roles:
        raise ValueError("conversion completion output roles are invalid")
    document_record = by_role["docling_json"]
    if (document_record["name"] != doc_path.name
            or document_record["size"] != document_size
            or document_record["sha256"] != document_sha256):
        raise ValueError(
            "conversion completion does not bind this document generation")
    if effective_input["kind"] == "preprocessed":
        preprocessed_record = by_role["preprocessed_pdf"]
        if any(
                preprocessed_record[key] != effective_input[key]
                for key in ("name", "size", "sha256")):
            raise ValueError(
                "conversion completion does not bind its preprocessed input")

    return ConversionSourceBinding(
        manifest_path=manifest_path,
        manifest_sha256=manifest_sha256,
        schema_version=CONVERSION_COMPLETION_SCHEMA_VERSION,
        capture_verified=True,
        source_name=source["name"],
        source_sha256=source["sha256"],
        source_size=source["size"],
        effective_input_kind=effective_input["kind"],
        effective_input_name=effective_input["name"],
        effective_input_sha256=effective_input["sha256"],
        effective_input_size=effective_input["size"],
    )


def _conversion_source_identity(
        doc_path: Path, *, document_sha256: str, document_size: int,
        origin_filename: str | None = None) -> tuple[str, str] | None:
    """Compatibility view over strict capture-verified conversion lineage."""
    del origin_filename
    binding = _load_conversion_source_binding(
        doc_path,
        document_sha256=document_sha256,
        document_size=document_size,
    )
    if binding is None:
        return None
    return binding.source_name, binding.source_sha256


def _find_docling_source_pdf(
        doc_path: Path, doc_dict: dict, *,
        source_identity: tuple[str, str] | None = None) -> Path | None:
    """Find only a source PDF whose bytes match proven conversion lineage."""
    del doc_dict  # Kept in the facade signature for compatibility.
    if source_identity is None:
        return None
    filename, expected_sha256 = source_identity
    if (not filename or Path(filename).name != filename
            or re.fullmatch(r"[0-9a-f]{64}", expected_sha256) is None):
        return None

    seen: set[Path] = set()
    candidates = [doc_path.parent / filename]
    candidates.extend(parent / filename for parent in doc_path.parents[1:])
    for candidate in candidates:
        try:
            resolved = candidate.resolve(strict=True)
            if resolved in seen or not resolved.is_file():
                continue
            seen.add(resolved)
            candidate_generation = _hash_file_generation(
                resolved, expected_sha256=expected_sha256)
            if candidate_generation.sha256 == expected_sha256:
                return resolved
        except (OSError, RuntimeError):
            continue
    return None


@contextmanager
def _open_docling_source_pdf_snapshot(
        doc_path: Path, binding: ConversionSourceBinding | None, *,
        explicit_source_pdf: Path | None = None):
    """Yield the only source-PDF pathname permitted to reach PyMuPDF."""
    if binding is None:
        if explicit_source_pdf is not None:
            raise ValueError(
                "--source-pdf requires a capture-verified conversion-v2 "
                "completion manifest")
        yield None
        return

    if explicit_source_pdf is not None:
        candidate = Path(explicit_source_pdf)
        _require_file(candidate, "source PDF")
        candidate = candidate.resolve(strict=True)
        discovery = "explicit"
    else:
        candidate = _find_docling_source_pdf(
            doc_path, {},
            source_identity=(binding.source_name, binding.source_sha256),
        )
        if candidate is None:
            yield None
            return
        discovery = (
            "adjacent" if candidate.parent == Path(doc_path).parent.resolve()
            else "ancestor")

    with _immutable_file_snapshot(
            candidate, expected_sha256=binding.source_sha256) as snapshot:
        yield TableRecoverySource(
            pdf=snapshot, conversion=binding, discovery=discovery,
            source_path=candidate)


class _SourceOutputAliasError(ValueError):
    """A recovered source PDF aliases an artifact that would overwrite it."""


def _recover_bound_table_markdown(
        dl_doc, doc_path: Path, binding: ConversionSourceBinding | None, *,
        source_pdf_path: Path | None = None,
        forbidden_output_paths: dict[str, Path] | None = None,
) -> tuple[dict[str, str], dict | None]:
    """Recover table text transactionally from a verified private PDF."""
    candidate_overrides: dict[str, str] = {}
    candidate_input: dict | None = None
    try:
        with _open_docling_source_pdf_snapshot(
                doc_path, binding,
                explicit_source_pdf=source_pdf_path) as recovery_source:
            if recovery_source is not None:
                if forbidden_output_paths:
                    try:
                        _run_telemetry.validate_distinct_output_paths({
                            "recovery source PDF": recovery_source.source_path,
                            **forbidden_output_paths,
                        })
                    except ValueError as exc:
                        raise _SourceOutputAliasError(str(exc)) from exc
                candidate_overrides = _recover_incomplete_table_markdown(
                    dl_doc, recovery_source.pdf.path)
                candidate_input = recovery_source.manifest_input()
    except _SourceOutputAliasError:
        raise
    except Exception as exc:
        if source_pdf_path is not None:
            raise RuntimeError(
                f"Explicit source PDF could not be verified: "
                f"{source_pdf_path}") from exc
        log.warning(
            "Could not verify Docling tables against its bound source PDF: %s",
            exc)
        return {}, None
    # Returning only after context exit makes verification part of the commit.
    return candidate_overrides, candidate_input


def _markdown_table_cell(text: str, *, preserve_lines: bool = False) -> str:
    """Normalize extracted PDF text for one safe Markdown table cell."""
    text = text.replace("\u200b", "").replace("|", "\\|").strip()
    if preserve_lines:
        lines = [re.sub(r"[^\S\n]+", " ", line).strip()
                 for line in text.splitlines()]
        return "<br>".join(line for line in lines if line)
    paragraphs = [
        re.sub(r"\s+", " ", paragraph).strip()
        for paragraph in re.split(r"\n\s*\n", text)
    ]
    return "<br><br>".join(paragraph for paragraph in paragraphs if paragraph)


def _source_table_needs_recovery(pdf_text: str, cell_text: str) -> bool:
    """Return whether source PDF text contains a real table-cell omission.

    Token-boundary differences such as ``New York`` versus ``NewYork`` do not
    lose information and should retain Docling's richer table structure.  In
    addition to large proportional omissions, recover a table when the missing
    token text contains at least eight more alphanumeric characters than the
    apparent replacement text.  That catches short but meaningful truncations
    such as a dropped final word or clause.
    """
    from collections import Counter

    def tokens(text: str) -> Counter:
        return Counter(re.findall(
            r"[a-z0-9]+", text.replace("\u200b", "").lower()))

    pdf_tokens = tokens(pdf_text)
    cell_tokens = tokens(cell_text)
    missing_tokens = pdf_tokens - cell_tokens
    missing_count = sum(missing_tokens.values())
    if not missing_count:
        return False
    extra_tokens = cell_tokens - pdf_tokens
    missing_characters = sum(
        len(token) * count for token, count in missing_tokens.items())
    extra_characters = sum(
        len(token) * count for token, count in extra_tokens.items())
    unexplained_missing_characters = max(
        0, missing_characters - extra_characters)
    if not unexplained_missing_characters:
        return False
    missing_ratio = missing_count / max(1, sum(pdf_tokens.values()))
    return (
        (missing_count >= 4 and missing_ratio > 0.10)
        or unexplained_missing_characters >= 8
    )


def _prepend_source_table_captions(
        markdown: str, table, text_by_ref: dict[str, object]) -> str:
    """Preserve Docling caption text when a table body comes from the PDF."""
    captions = []
    for reference in (getattr(table, "captions", None) or []):
        item = text_by_ref.get(str(getattr(reference, "cref", "")))
        caption = (
            getattr(item, "text", "") or getattr(item, "orig", "")
            if item is not None else ""
        )
        if caption and caption.strip():
            captions.append(caption.strip())
    if not captions:
        return markdown
    return "\n\n".join([*captions, markdown])


def _recover_incomplete_table_markdown(
        dl_doc, pdf_path: Path) -> dict[str, str]:
    """Recover tables whose Docling cells omit text present in the source PDF.

    The recovery is deliberately narrow: compare token multisets inside each
    table's source bounding box and replace tables with either a large
    proportional omission or a smaller omission that cannot be explained by
    token fusion.  Ordinary tables continue to use Docling's richer row/column
    model.
    """
    import pymupdf
    recovered: dict[str, str] = {}
    text_by_ref = {
        str(getattr(item, "self_ref", "")): item
        for item in (getattr(dl_doc, "texts", None) or [])
        if getattr(item, "self_ref", "")
    }
    with pymupdf.open(str(pdf_path)) as pdf:
        for table in getattr(dl_doc, "tables", []) or []:
            if _doc_item_label(table) != "table" or not table.prov:
                continue
            provenance = table.prov[0]
            page_number = int(provenance.page_no)
            if not 1 <= page_number <= len(pdf):
                continue
            page = pdf[page_number - 1]
            bbox = provenance.bbox
            origin = str(getattr(bbox.coord_origin, "value", bbox.coord_origin))
            if origin.upper() == "BOTTOMLEFT":
                rectangle = pymupdf.Rect(
                    bbox.l, page.rect.height - bbox.t,
                    bbox.r, page.rect.height - bbox.b)
            else:
                rectangle = pymupdf.Rect(bbox.l, bbox.t, bbox.r, bbox.b)
            pdf_text = page.get_text("text", clip=rectangle, sort=True).strip()
            cells = list(getattr(table.data, "table_cells", []) or [])
            cell_text = " ".join(str(getattr(cell, "text", ""))
                                 for cell in cells)
            if not _source_table_needs_recovery(pdf_text, cell_text):
                continue

            first_row = sorted(
                (cell for cell in cells
                 if int(cell.start_row_offset_idx) == 0),
                key=lambda cell: int(cell.start_col_offset_idx),
            )
            is_case_layout = bool(
                first_row and re.match(
                    r"\s*case\s+\d+\s*:", first_row[0].text, re.I))
            if (int(getattr(table.data, "num_cols", 0)) == 2
                    and len(first_row) >= 2 and not is_case_layout):
                left_cells = [cell for cell in cells
                              if int(cell.start_col_offset_idx) == 0]
                right_cells = [cell for cell in cells
                               if int(cell.start_col_offset_idx) == 1]
                boundary = (
                    max(cell.bbox.r for cell in left_cells)
                    + min(cell.bbox.l for cell in right_cells)
                ) / 2
                body_top = max(cell.bbox.b for cell in first_row) + 1
                def column_text(column_rectangle) -> str:
                    blocks = page.get_text(
                        "blocks", clip=column_rectangle, sort=True)
                    return "\n\n".join(
                        str(block[4]).strip() for block in blocks
                        if str(block[4]).strip()
                    )

                left_text = column_text(pymupdf.Rect(
                    rectangle.x0, body_top, boundary, rectangle.y1))
                right_text = column_text(pymupdf.Rect(
                    boundary, body_top, rectangle.x1, rectangle.y1))
                left_header = _markdown_table_cell(first_row[0].text)
                right_header = _markdown_table_cell(first_row[1].text)
                markdown = (
                    f"| {left_header} | {right_header} |\n"
                    "|---|---|\n"
                    f"| {_markdown_table_cell(left_text)} | "
                    f"{_markdown_table_cell(right_text)} |"
                )
            else:
                markdown = (
                    "| Source table layout |\n|---|\n"
                    f"| {_markdown_table_cell(pdf_text, preserve_lines=True)} |"
                )
            recovered[str(table.self_ref)] = _prepend_source_table_captions(
                markdown, table, text_by_ref)
    return recovered


def _prepare_source_preserving_chunks(
        raw_chunks: list, dl_doc, token_counter: Callable[[str], int],
        max_tokens: int, *,
        table_markdown_overrides: dict[str, str] | None = None,
        structural_ranges: set[tuple[int, int]] | None = None,
        ) -> list[tuple[str, list | None, list | None, bool]]:
    """Separate mixed Docling tables from prose without duplicating either.

    HybridChunker serializes a table as flattened ``key = value`` prose when
    it shares a chunk with surrounding text.  Rebuild those mixed chunks from
    their source DocItems, emit each table once as Markdown, and retain every
    adjacent text item in source order.  The final boolean marks source-bound
    fragments that must survive the generic minimum-word filter.
    """
    item_by_ref, _, _ = _docling_lineage_catalog(dl_doc)

    table_markdown_by_ref: dict[str, str] = {}
    table_caption_refs: set[str] = set()
    for table_index, table in enumerate(getattr(dl_doc, "tables", []) or []):
        if _doc_item_label(table) != "table":
            continue
        try:
            markdown = table.export_to_markdown(doc=dl_doc)
        except Exception:
            continue
        if not markdown or not markdown.strip():
            continue
        ref = str(getattr(table, "self_ref", f"#/tables/{table_index}"))
        table_markdown_by_ref[ref] = (
            (table_markdown_overrides or {}).get(ref, markdown).strip())
        table_markdown_by_ref.setdefault(
            f"#/tables/{table_index}", markdown.strip())
        table_caption_refs.update(
            str(getattr(caption, "cref", ""))
            for caption in (getattr(table, "captions", []) or [])
            if getattr(caption, "cref", "")
        )

    nested_footnotes_by_parent: dict[str, list[object]] = {}
    for parent in list(getattr(dl_doc, "tables", []) or []) + list(
            getattr(dl_doc, "pictures", []) or []):
        parent_ref = str(getattr(parent, "self_ref", ""))
        footnotes = [
            item_by_ref.get(str(getattr(reference, "cref", "")))
            for reference in (getattr(parent, "footnotes", []) or [])
        ]
        footnotes = [
            footnote for footnote in footnotes
            if footnote is not None
            and "This and other authors' explanations draw" not in (
                getattr(footnote, "text", "")
                or getattr(footnote, "orig", "")
            )
        ]
        if parent_ref and footnotes:
            nested_footnotes_by_parent[parent_ref] = footnotes

    nested_footnote_refs = {
        str(getattr(footnote, "self_ref", ""))
        for footnotes in nested_footnotes_by_parent.values()
        for footnote in footnotes
    }

    prepared: list[tuple[str, list | None, list | None, bool]] = []
    emitted_tables: set[str] = set()
    emitted_nested_footnotes: set[str] = set()

    page_headers: dict[int, list[tuple[float, str]]] = {}

    def source_position(item) -> tuple[int, float] | None:
        provenance = list(getattr(item, "prov", None) or [])
        if not provenance:
            return None
        first = provenance[0]
        bbox = getattr(first, "bbox", None)
        if bbox is None:
            return None
        origin = str(getattr(bbox.coord_origin, "value", bbox.coord_origin))
        vertical = (
            -float(bbox.t) if origin.upper() == "BOTTOMLEFT"
            else float(bbox.t)
        )
        return int(first.page_no), vertical

    for item in getattr(dl_doc, "texts", []) or []:
        if _doc_item_label(item) != "section_header":
            continue
        if (position := source_position(item)) is None:
            continue
        header_text = (
            getattr(item, "text", "") or getattr(item, "orig", ""))
        if header_text and header_text.strip():
            page_headers.setdefault(position[0], []).append(
                (position[1], header_text.strip()))
    for headers in page_headers.values():
        headers.sort()

    def source_pages(item) -> list[int]:
        return [
            int(provenance.page_no)
            for provenance in (getattr(item, "prov", None) or [])
            if getattr(provenance, "page_no", None) is not None
        ]

    def fully_inside_structural_range(item) -> bool | None:
        pages = source_pages(item)
        if not pages:
            return None
        return all(
            any(start <= page <= end for start, end in structural_ranges or ())
            for page in pages
        )

    source_heading_by_ref: dict[str, str] = {}
    for ref, item in item_by_ref.items():
        if _doc_item_label(item) == "section_header":
            continue
        if (position := source_position(item)) is None:
            continue
        preceding = [
            (vertical, heading) for vertical, heading
            in page_headers.get(position[0], [])
            if vertical < position[1]
        ]
        if preceding:
            source_heading_by_ref[ref] = preceding[-1][1]

    def missing_nested_footnote_entry(
            parent_ref: str, headings: list | None):
        missing_footnotes = []
        missing_texts = []
        for footnote in nested_footnotes_by_parent.get(parent_ref, []):
            footnote_ref = str(getattr(footnote, "self_ref", ""))
            if footnote_ref in emitted_nested_footnotes:
                continue
            footnote_text = (
                getattr(footnote, "text", "")
                or getattr(footnote, "orig", "")
            )
            if not footnote_text or not footnote_text.strip():
                raise RuntimeError(
                    f"Nested footnote {footnote_ref} has no source text")
            missing_footnotes.append(footnote)
            missing_texts.append(footnote_text.strip())
            emitted_nested_footnotes.add(footnote_ref)
        if not missing_footnotes:
            return None
        return (
            "\n".join(missing_texts), headings, missing_footnotes, True,
        )

    def append_missing_nested_footnotes(
            parent_ref: str, headings: list | None) -> None:
        entry = missing_nested_footnote_entry(parent_ref, headings)
        if entry is not None:
            prepared.append(entry)

    for chunk in raw_chunks:
        items = list(
            getattr(getattr(chunk, "meta", None), "doc_items", None) or [])
        headings = getattr(getattr(chunk, "meta", None), "headings", None)
        mapped_headings = [
            source_heading_by_ref.get(
                str(getattr(item, "self_ref", "")))
            for item in items
        ]
        distinct_mapped_headings = list(dict.fromkeys(
            heading for heading in mapped_headings if heading))
        known_table_refs = {
            str(getattr(item, "self_ref", ""))
            for item in items
            if _doc_item_label(item) == "table"
            and str(getattr(item, "self_ref", "")) in table_markdown_by_ref
        }
        structural_flags = {
            flag for item in items
            if (flag := fully_inside_structural_range(item)) is not None
        }
        crosses_structural_boundary = structural_flags == {False, True}
        if ((len(distinct_mapped_headings) > 1
             or crosses_structural_boundary)
                and not known_table_refs):
            for item, mapped_heading in zip(items, mapped_headings):
                ref = str(getattr(item, "self_ref", ""))
                if ref in nested_footnote_refs:
                    continue
                source_item = item_by_ref.get(ref, item)
                source_text = (
                    getattr(source_item, "text", "")
                    or getattr(source_item, "orig", "")
                )
                item_headings = (
                    [mapped_heading] if mapped_heading else headings)
                if source_text and source_text.strip():
                    prepared.append((
                        source_text.strip(), item_headings,
                        [source_item], True,
                    ))
                append_missing_nested_footnotes(
                    str(getattr(source_item, "self_ref", "")),
                    item_headings)
            continue
        if not known_table_refs:
            has_nested_footnotes = any(
                str(getattr(item, "self_ref", "")) in nested_footnote_refs
                for item in items
            )
            if has_nested_footnotes:
                retained_items = [
                    item_by_ref.get(
                        str(getattr(item, "self_ref", "")), item)
                    for item in items
                    if str(getattr(item, "self_ref", ""))
                    not in nested_footnote_refs
                ]
                retained_text = [
                    (getattr(item, "text", "")
                     or getattr(item, "orig", "")).strip()
                    for item in retained_items
                    if (getattr(item, "text", "")
                        or getattr(item, "orig", "")).strip()
                ]
                if retained_text:
                    prepared.append((
                        "\n\n".join(retained_text), headings,
                        retained_items, True,
                    ))
            else:
                prepared.append((chunk.text, headings, items, False))
            for item in items:
                append_missing_nested_footnotes(
                    str(getattr(item, "self_ref", "")), headings)
            continue

        pending_text: list[str] = []
        pending_items: list[object] = []

        def flush_text_items() -> None:
            if not pending_text:
                return
            prepared.append((
                "\n\n".join(pending_text), headings,
                list(pending_items), True,
            ))
            pending_text.clear()
            pending_items.clear()

        for item in items:
            ref = str(getattr(item, "self_ref", ""))
            source_item = item_by_ref.get(ref, item)
            if ref in nested_footnote_refs:
                continue
            if _doc_item_label(source_item) == "table" and ref in known_table_refs:
                flush_text_items()
                if ref in emitted_tables:
                    continue
                for table_part in _split_markdown_table_by_rows(
                        table_markdown_by_ref[ref], token_counter, max_tokens):
                    prepared.append((table_part, headings, [source_item], True))
                emitted_tables.add(ref)
                append_missing_nested_footnotes(ref, headings)
                continue
            if ref in nested_footnotes_by_parent:
                flush_text_items()
                append_missing_nested_footnotes(ref, headings)
                continue
            if ref in table_caption_refs:
                # Docling includes a table's caption in export_to_markdown().
                continue
            source_text = (
                getattr(source_item, "text", "")
                or getattr(source_item, "orig", "")
            )
            if source_text and source_text.strip():
                pending_text.append(source_text.strip())
                pending_items.append(source_item)
        flush_text_items()

    def item_page(item) -> int | None:
        pages = [
            int(provenance.page_no)
            for provenance in (getattr(item, "prov", None) or [])
            if getattr(provenance, "page_no", None) is not None
        ]
        return min(pages) if pages else None

    def prepared_page(entry) -> int | None:
        pages = [
            page
            for item in (entry[2] or [])
            if (page := item_page(item)) is not None
        ]
        return min(pages) if pages else None

    orphaned_parents = [
        (parent_ref, footnotes)
        for parent_ref, footnotes in nested_footnotes_by_parent.items()
        if any(
            str(getattr(footnote, "self_ref", ""))
            not in emitted_nested_footnotes
            for footnote in footnotes
        )
    ]
    orphaned_parents.sort(key=lambda pair: min(
        (item_page(footnote) for footnote in pair[1]
         if item_page(footnote) is not None),
        default=sys.maxsize,
    ))
    for parent_ref, footnotes in orphaned_parents:
        page = min(
            (item_page(footnote) for footnote in footnotes
             if item_page(footnote) is not None),
            default=None,
        )
        if page is None:
            raise RuntimeError(
                f"Cannot place nested footnotes for {parent_ref}: "
                "missing source page")
        insert_at = 0
        for index, entry in enumerate(prepared):
            entry_page = prepared_page(entry)
            if entry_page is not None and entry_page <= page:
                insert_at = index + 1
        inherited_headings = (
            prepared[insert_at - 1][1] if insert_at else None)
        entry = missing_nested_footnote_entry(
            parent_ref, inherited_headings)
        if entry is not None:
            prepared.insert(insert_at, entry)

    # HybridChunker can omit a small source item while retaining the same
    # words in a neighboring mixed-layout chunk (for example, a one-word
    # figure caption beside wrapped prose). Attach that source identity to the
    # exact matching text; if its words are genuinely absent, publish a
    # source-positioned fragment instead of silently losing the item.
    represented_refs = {
        str(getattr(item, "self_ref", ""))
        for entry in prepared
        for item in (entry[2] or [])
        if getattr(item, "self_ref", "")
    }
    recovered_matching_items = 0
    recovered_standalone_items = 0
    eligible_labels = {
        "text", "list_item", "footnote", "caption", "code", "table",
    }
    for ref, item in item_by_ref.items():
        if ref in represented_refs or ref in table_caption_refs:
            continue
        label = _doc_item_label(item)
        if label not in eligible_labels:
            continue
        content_layer = str(getattr(item, "content_layer", "")).lower()
        if "furniture" in content_layer:
            continue
        if fully_inside_structural_range(item) is not False:
            continue
        source_text = (
            getattr(item, "text", "") or getattr(item, "orig", ""))
        source_text = source_text.strip()
        if (label != "table" and not source_text
                or re.fullmatch(
                    r"\**\s*all\s+emphasis\s+added\.?\s*\**",
                    source_text, re.I)
                or "This and other authors' explanations draw" in source_text):
            continue
        page = item_page(item)
        source_fingerprint = _text_fingerprint(source_text)
        matching_index = next((
            index for index, entry in enumerate(prepared)
            if page is not None and prepared_page(entry) == page
            and len(source_fingerprint) >= 5
            and source_fingerprint in _text_fingerprint(entry[0])
        ), None)
        if matching_index is not None:
            text, headings, items, preserve_short = prepared[matching_index]
            prepared[matching_index] = (
                text, headings, [*(items or []), item], True)
            represented_refs.add(ref)
            recovered_matching_items += 1
            continue

        if label == "table" and ref in table_markdown_by_ref:
            orphan_parts = _split_markdown_table_by_rows(
                table_markdown_by_ref[ref], token_counter, max_tokens)
        else:
            orphan_parts = [source_text] if source_text else []
        if not orphan_parts or page is None:
            continue
        insert_at = 0
        for index, entry in enumerate(prepared):
            entry_page = prepared_page(entry)
            if entry_page is not None and entry_page <= page:
                insert_at = index + 1
        inherited_headings = (
            [source_heading_by_ref[ref]]
            if ref in source_heading_by_ref else
            prepared[insert_at - 1][1] if insert_at else None)
        for part in orphan_parts:
            prepared.insert(
                insert_at, (part, inherited_headings, [item], True))
            insert_at += 1
        represented_refs.add(ref)
        recovered_standalone_items += 1

    if recovered_matching_items or recovered_standalone_items:
        log.info(
            "Recovered %s omitted source identities from matching chunks; "
            "%s required standalone source fragments",
            recovered_matching_items, recovered_standalone_items)

    return prepared


_HEADING_PROMPT = """This text belongs to the document division "{division_title}".
The current section heading is "{heading}" which lacks context.
Based on the text content, what is the full hierarchical section path?
Begin the path with the exact division title and separate levels with " > ".
Reply with ONLY the path, nothing else.

Text (first 400 chars):
{text}

Full section path:"""


def _reconstruct_heading(
        text, heading, chapter_num, chapter_title, *,
        structure_profile: (
            str | _document_profiles.StructureProfile
        ) = DEFAULT_STRUCTURE_PROFILE,
        **llm_kwargs,
):
    """Use LLM to reconstruct a full section path from a bare heading.

    Only processes headings that are < 5 characters (bare "B", "III", "2", etc.).
    Returns the reconstructed section path or None on failure.
    """
    if len(heading) >= 5:
        return None
    profile = _document_profiles.get_profile(structure_profile)
    division_title = chapter_title or (
        _document_profiles.fallback_division_title(profile, chapter_num))
    prompt = _HEADING_PROMPT.format(
        division_title=division_title,
        heading=heading,
        text=text[:400],
    )
    result = _call_llm(
        prompt, max_tokens=256, operation="heading.reconstruct", **llm_kwargs)
    if not result:
        return None
    result = _THINK_TAG_RE.sub("", result).strip()
    # The profile-native title is always available, so a bare normalized
    # ordinal is not enough to keep an LLM reconstruction in this division.
    def normalize_title(value: str) -> str:
        return re.sub(r"\s+", " ", value).strip().casefold()

    first_component = re.split(r"\s*>\s*", result, maxsplit=1)[0]
    if normalize_title(first_component) != normalize_title(division_title):
        return None
    # Cap length — should be a section path, not a paragraph
    if len(result) > 200:
        return None
    return result


def _endpoint_parameter_binding(url: str) -> dict:
    """Represent an exact endpoint without retaining credential material."""
    endpoint = _validate_cloud_endpoint(url, allow_disabled=True)
    return {
        "policy_version": _endpoint_policy.ENDPOINT_POLICY_VERSION,
        "endpoint_id": (
            endpoint.endpoint_id if endpoint is not None
            else _endpoint_policy.cloud_endpoint_identity("")
        ),
    }


def _chunk_parameters(*, embedding_model: str, max_tokens: int,
                      min_words: int, dedup_threshold: float,
                      watermark: re.Pattern | None,
                      llm_classify: bool, zeroshot_classify: bool,
                      contextualize: bool, ollama_url: str,
                      ollama_model: str, gemini_key: str,
                      cloud_url: str, cloud_model: str, cloud_key: str,
                      llm_workers: int, thinking: bool,
                       reconstruct_headings: bool, quality_score: bool,
                       llm_scaffold: bool,
                       table_children: bool = False,
                       structure_profile: (
                          str | _document_profiles.StructureProfile
                      ) = DEFAULT_STRUCTURE_PROFILE,
                       security_policy: (
                           _release_security.ReleaseSecurityPolicy | None
                       ) = None) -> dict:
    """Return credential-free parameters that determine chunking output."""
    profile = _document_profiles.get_profile(structure_profile)
    llm_generation_enabled = any((
        llm_classify,
        contextualize,
        reconstruct_headings,
        quality_score,
        llm_scaffold,
    ))
    policy = _effective_security_policy(security_policy)
    gemini_enabled = bool(
        llm_generation_enabled
        and policy.network_policy == "allow-cloud"
        and (gemini_key or "GEMINI_API_KEY" in os.environ))
    llm_config = _llm_runtime.config
    return {
        "chunking_policy_version": 23,
        "classification_prompt_version": 1,
        "embedding_model": embedding_model,
        "max_tokens": max_tokens,
        "min_words": min_words,
        "dedup_threshold": dedup_threshold,
        "watermark_pattern": watermark.pattern if watermark else None,
        "watermark_flags": watermark.flags if watermark else None,
        "llm_classify": llm_classify,
        "zeroshot_classify": zeroshot_classify,
        "zeroshot_model": (
            DEFAULT_ZEROSHOT_MODEL if zeroshot_classify else None),
        "contextualize": contextualize,
        "ollama_url": _endpoint_parameter_binding(ollama_url),
        "ollama_model": ollama_model,
        "gemini_enabled": gemini_enabled,
        "gemini_model": DEFAULT_GEMINI_MODEL if gemini_enabled else None,
        "cloud_url": _endpoint_parameter_binding(cloud_url),
        "cloud_model": cloud_model,
        "cloud_enabled": bool(
            llm_generation_enabled and cloud_url and cloud_key),
        "llm_workers": llm_workers,
        "llm_fallback_policy": llm_config.fallback_policy,
        "llm_failure_policy": llm_config.failure_policy,
        "max_llm_calls": llm_config.max_provider_calls,
        "max_llm_transport_attempts": llm_config.max_transport_attempts,
        "max_llm_reserved_tokens": llm_config.max_reserved_tokens,
        "thinking": thinking,
        "reconstruct_headings": reconstruct_headings,
        "quality_score": quality_score,
        "llm_scaffold": llm_scaffold,
        "table_children": table_children,
        "table_retrieval_schema_version": (
            _table_retrieval_core.TABLE_RETRIEVAL_SCHEMA_VERSION),
        "table_child_min_rows": (
            _table_retrieval_core.DEFAULT_TABLE_CHILD_MIN_ROWS),
        "table_child_parent_cap": (
            _table_retrieval_core.MAX_TABLE_CHILDREN_PER_PARENT),
        "table_child_corpus_cap": (
            _table_retrieval_core.MAX_TABLE_CHILDREN_PER_CORPUS),
        "structure_profile": _document_profiles.profile_provenance(profile),
        "model_artifact_lock_sha256": _model_artifact_lock_sha256(),
        "release_security": policy.provenance(),
    }


_STRUCTURAL_SECTION_NAMES = (
    _document_profiles.structural_section_keys(
        _document_profiles.get_profile(DEFAULT_STRUCTURE_PROFILE)))


def _book_structural_ranges(
        book_sections: dict, *,
        structure_profile: (
            str | _document_profiles.StructureProfile
        ) = DEFAULT_STRUCTURE_PROFILE,
) -> set[tuple[int, int]]:
    """Return the exact source-page ranges excluded from publication."""
    profile = _document_profiles.get_profile(structure_profile)
    structural_names = _document_profiles.structural_section_keys(profile)
    ranges = {
        (section["start"], section["end"])
        for name in structural_names
        if (section := book_sections.get(name)) is not None
    }
    opening_starts = [
        section["start"]
        for name in _document_profiles.toc_seed_keys(profile)
        if (section := book_sections.get(name)) is not None
    ]
    if opening_starts and min(opening_starts) > 1:
        ranges.add((1, min(opening_starts) - 1))
    return ranges


def _load_chunk_completion_inputs(
        doc_path: Path, chunks_output: Path, *,
        document_sha256: str, document_size: int,
        chunks_sha256: str, chunks_size: int, parameters: dict,
        records: list[dict], source_pdf_path: Path | None = None,
) -> dict | None:
    """Strictly validate chunk-v3 inputs against current exact artifacts."""
    manifest_path = _artifact_completion_path(
        chunks_output, stage="chunking")
    try:
        raw, _, _ = _read_index_artifact_snapshot(
            manifest_path, max_bytes=_MAX_CHUNK_COMPLETION_BYTES)
    except FileNotFoundError:
        return None
    payload = _strict_json_object(raw, description="chunk completion")
    if payload.get("schema_version") != CHUNK_COMPLETION_SCHEMA_VERSION:
        return None
    if set(payload) != {
            "schema_version", "stage", "source_sha256",
            "source_record_count", "parameters_sha256", "outputs",
            "inputs", "structure_profile",
            "structure_profile_parameters_sha256"}:
        raise ValueError("chunk completion has an invalid field set")
    if (payload.get("stage") != "chunking"
            or payload.get("source_sha256") != document_sha256
            or payload.get("source_record_count") is not None
            or payload.get("parameters_sha256")
            != _artifact_parameters_sha256(parameters)):
        raise ValueError("chunk completion header is invalid")
    profile = _document_profiles.profile_from_provenance(
        parameters.get("structure_profile"))
    if payload.get("structure_profile") != (
            _document_profiles.profile_provenance(profile)):
        raise ValueError("chunk completion structure profile is invalid")
    if payload.get("structure_profile_parameters_sha256") != (
            _structure_profile_parameters_binding(
                payload["parameters_sha256"], payload["structure_profile"])):
        raise ValueError(
            "chunk completion structure profile is detached from parameters")

    outputs = payload.get("outputs")
    if (not isinstance(outputs, list) or len(outputs) != 1
            or not isinstance(outputs[0], dict)
            or set(outputs[0]) != {"role", "name", "size", "sha256"}
            or outputs[0].get("role") != "chunks_jsonl"
            or outputs[0].get("name") != chunks_output.name
            or outputs[0].get("size") != chunks_size
            or outputs[0].get("sha256") != chunks_sha256):
        raise ValueError("chunk completion output binding is invalid")

    inputs = payload.get("inputs")
    if (not isinstance(inputs, dict)
            or set(inputs) != {
                "docling_json", "conversion_manifest", "table_recovery"}):
        raise ValueError("chunk completion inputs are invalid")
    document_input = inputs.get("docling_json")
    if (not _valid_manifest_file_record(document_input)
            or document_input != {
                "name": doc_path.name,
                "size": document_size,
                "sha256": document_sha256,
            }):
        raise ValueError("chunk completion Docling input is invalid")

    conversion_binding = _load_conversion_source_binding(
        doc_path,
        document_sha256=document_sha256,
        document_size=document_size,
    )
    conversion_input = inputs.get("conversion_manifest")
    expected_conversion_input = (
        {
            "name": conversion_binding.manifest_path.name,
            "sha256": conversion_binding.manifest_sha256,
            "schema_version": conversion_binding.schema_version,
        }
        if conversion_binding is not None else None
    )
    if conversion_input != expected_conversion_input:
        raise ValueError("chunk completion conversion input is invalid")

    table_recovery = inputs.get("table_recovery")
    if table_recovery is not None:
        if (conversion_binding is None
                or not isinstance(table_recovery, dict)
                or set(table_recovery) != {
                    "pdf", "conversion_manifest", "discovery"}
                or table_recovery.get("conversion_manifest")
                != expected_conversion_input
                or table_recovery.get("discovery")
                not in {"explicit", "adjacent", "ancestor"}):
            raise ValueError("chunk completion table recovery is invalid")
        pdf_input = table_recovery.get("pdf")
        if (not isinstance(pdf_input, dict)
                or set(pdf_input) != {
                    "name", "size", "sha256", "capture_policy"}
                or pdf_input.get("capture_policy")
                != _CONVERSION_CAPTURE_POLICY
                or not _valid_manifest_file_record({
                    key: pdf_input.get(key)
                    for key in ("name", "size", "sha256")
                })
                or pdf_input.get("size") != conversion_binding.source_size
                or pdf_input.get("sha256")
                != conversion_binding.source_sha256):
            raise ValueError("chunk completion recovery PDF is invalid")

    recovered_tables = any(
        (record.get("metadata") or {}).get("table_recovered_from_pdf")
        for record in records)
    if recovered_tables and table_recovery is None:
        raise ValueError(
            "recovered tables lack a bound source-PDF input")

    if source_pdf_path is not None:
        if conversion_binding is None or table_recovery is None:
            raise ValueError(
                "explicit source PDF was not inspected by this chunk artifact")
        source_generation = _hash_file_generation(
            source_pdf_path,
            expected_sha256=conversion_binding.source_sha256,
        )
        if (source_generation.size != conversion_binding.source_size
                or table_recovery["pdf"]["sha256"]
                != source_generation.sha256):
            raise ValueError("explicit source PDF binding changed")
    return inputs


def _chunks_complete(doc_path: Path, chunks_output: Path, *,
                     parameters: dict,
                     source_pdf_path: Path | None = None) -> bool:
    """Validate chunk completeness under the artifact-set lease."""
    with _chunk_output_lease(chunks_output):
        return _chunks_complete_locked(
            doc_path, chunks_output,
            parameters=parameters,
            source_pdf_path=source_pdf_path,
        )


def _chunks_complete_locked(
        doc_path: Path, chunks_output: Path, *, parameters: dict,
        source_pdf_path: Path | None = None) -> bool:
    """Validate a chunk artifact and its source/configuration completion."""
    try:
        document_raw, source_sha256, _ = _read_index_artifact_snapshot(
            doc_path)
        chunks_raw, chunks_sha256, _ = _read_index_artifact_snapshot(
            chunks_output)
        records = _parse_index_records_strict(chunks_raw, chunks_output)
        inputs = _load_chunk_completion_inputs(
            doc_path, chunks_output,
            document_sha256=source_sha256,
            document_size=len(document_raw),
            chunks_sha256=chunks_sha256,
            chunks_size=len(chunks_raw),
            parameters=parameters,
            records=records,
            source_pdf_path=source_pdf_path,
        )
    except (OSError, RuntimeError, ValueError):
        return False
    if inputs is not None:
        return bool(records)
    # Schema-v1 cannot attest one Docling generation or supplemental PDF
    # recovery.  It is readable for migration tooling but never "complete".
    return False


def _quality_report_complete(doc_path: Path, chunks_output: Path, *,
                             parameters: dict) -> bool:
    """Validate one quality report under its artifact-set lease."""
    with _chunk_output_lease(chunks_output):
        return _quality_report_complete_locked(
            doc_path, chunks_output, parameters=parameters)


def _quality_report_complete_locked(
        doc_path: Path, chunks_output: Path, *, parameters: dict) -> bool:
    """Validate the report against exact source, chunks, and parameters."""
    try:
        source_raw, source_sha256, _ = _read_index_artifact_snapshot(doc_path)
        if not isinstance(json.loads(source_raw), dict):
            return False
        chunks_raw, chunks_sha256, _ = _read_index_artifact_snapshot(
            chunks_output)
        records = _parse_index_records_strict(chunks_raw, chunks_output)
        input_bindings = _load_chunk_completion_inputs(
            doc_path, chunks_output,
            document_sha256=source_sha256,
            document_size=len(source_raw),
            chunks_sha256=chunks_sha256,
            chunks_size=len(chunks_raw),
            parameters=parameters,
            records=records,
        )
        if input_bindings is None:
            return False
        schema_version, report_sha256, _ = _validated_quality_report_binding(
            chunks_output,
            records,
            chunks_sha256,
            len(chunks_raw),
            source_name=Path(doc_path).name,
            source_sha256=source_sha256,
            parameters_sha256=_artifact_io._artifact_parameters_sha256(
                parameters),
            embedding_model=parameters.get("embedding_model"),
            embedding_limit=EMBEDDING_MAX_TOKENS.get(
                parameters.get("embedding_model")),
            input_bindings=input_bindings,
        )
        return (
            schema_version == _quality_core.QUALITY_REPORT_SCHEMA_VERSION
            and isinstance(report_sha256, str)
        )
    except (OSError, UnicodeError, json.JSONDecodeError, ValueError,
            RuntimeError):
        return False


def _recovered_table_refs_from_records(records: list[dict]) -> set[str]:
    refs = set()
    for record in records:
        metadata = record.get("metadata", {})
        if not metadata.get("table_recovered_from_pdf"):
            continue
        for source_item in metadata.get("source_items", []):
            if (isinstance(source_item, dict)
                    and source_item.get("label") == "table"
                    and isinstance(source_item.get("ref"), str)):
                refs.add(source_item["ref"])
    return refs


def _publish_corpus_quality_report(
        doc_path: Path, chunks_output: Path, *, parameters: dict,
        structural_ranges: set[tuple[int, int]] | None = None,
        document_snapshot: tuple[dict, str, int] | None = None,
        chunk_inputs: dict | None = None,
        structure_profile: (
            str | _document_profiles.StructureProfile | None
        ) = None,
) -> dict:
    """Publish quality evidence under the chunk artifact-set lease."""
    with _chunk_output_lease(chunks_output):
        return _publish_corpus_quality_report_locked(
            doc_path, chunks_output,
            parameters=parameters,
            structural_ranges=structural_ranges,
            document_snapshot=document_snapshot,
            chunk_inputs=chunk_inputs,
            structure_profile=structure_profile,
        )


def _publish_corpus_quality_report_locked(
        doc_path: Path, chunks_output: Path, *, parameters: dict,
        structural_ranges: set[tuple[int, int]] | None = None,
        document_snapshot: tuple[dict, str, int] | None = None,
        chunk_inputs: dict | None = None,
        structure_profile: (
            str | _document_profiles.StructureProfile | None
        ) = None,
) -> dict:
    """Build and atomically publish a report over exact artifact snapshots."""
    doc_path = Path(doc_path)
    chunks_output = Path(chunks_output)
    if document_snapshot is None:
        source_raw, source_sha256, _ = _read_index_artifact_snapshot(doc_path)
        source_size = len(source_raw)
        try:
            document = json.loads(source_raw)
        except (UnicodeError, json.JSONDecodeError) as exc:
            raise ValueError(f"Invalid Docling source JSON: {doc_path}") from exc
        if not isinstance(document, dict):
            raise ValueError(
                f"Docling source must be a JSON object: {doc_path}")
    else:
        document, source_sha256, source_size = document_snapshot
        if (not isinstance(document, dict)
                or not isinstance(source_size, int) or source_size <= 0
                or re.fullmatch(r"[0-9a-f]{64}", source_sha256) is None):
            raise ValueError("Invalid captured Docling source snapshot")

    chunks_raw, chunks_sha256, _ = _read_index_artifact_snapshot(
        chunks_output)
    records = _parse_index_records_strict(chunks_raw, chunks_output)
    validated_chunk_inputs = _load_chunk_completion_inputs(
        doc_path, chunks_output,
        document_sha256=source_sha256,
        document_size=source_size,
        chunks_sha256=chunks_sha256,
        chunks_size=len(chunks_raw),
        parameters=parameters,
        records=records,
    )
    if validated_chunk_inputs is None:
        raise ValueError(
            "Corpus quality requires a verified chunk-v3 completion")
    if chunk_inputs is not None and chunk_inputs != validated_chunk_inputs:
        raise RuntimeError(
            "Chunk inputs changed before quality report publication")
    chunk_inputs = validated_chunk_inputs
    profile = _document_profiles.profile_from_provenance(
        parameters.get("structure_profile"))
    if (structure_profile is not None
            and _document_profiles.get_profile(structure_profile) is not profile):
        raise ValueError(
            "quality-report structure profile does not match the attested "
            "chunk parameters")
    if structural_ranges is None:
        structural_ranges = _book_structural_ranges(
            _identify_book_sections(
                document, structure_profile=profile),
            structure_profile=profile)
    stable_ids = [_retrieval_core._chunk_id(record) for record in records]
    chunk_hashes = [_retrieval_core._chunk_hash(record) for record in records]
    embedding_model = str(parameters.get("embedding_model") or "")
    report = _quality_core.build_quality_report(
        records=records,
        stable_ids=stable_ids,
        chunk_hashes=chunk_hashes,
        document=document,
        structural_ranges=structural_ranges,
        recovered_table_refs=_recovered_table_refs_from_records(records),
        source_name=doc_path.name,
        source_sha256=source_sha256,
        chunks_name=chunks_output.name,
        chunks_sha256=chunks_sha256,
        chunks_size=len(chunks_raw),
        parameters_sha256=_artifact_io._artifact_parameters_sha256(
            parameters),
        embedding_model=embedding_model,
        embedding_limit=EMBEDDING_MAX_TOKENS.get(embedding_model),
        input_bindings=chunk_inputs,
    )
    report_path = _quality_core.quality_report_path(chunks_output)
    _atomic_write_json(report_path, report)
    if report["status"] != "pass":
        failed = [
            check["name"] for check in report["checks"]
            if check["status"] == "fail"
        ]
        raise RuntimeError(
            "Corpus quality gate failed: " + ", ".join(failed))

    _validated_quality_report_binding(
        chunks_output,
        records,
        chunks_sha256,
        len(chunks_raw),
        source_name=doc_path.name,
        source_sha256=source_sha256,
        parameters_sha256=_artifact_io._artifact_parameters_sha256(
            parameters),
        embedding_model=embedding_model,
        embedding_limit=EMBEDDING_MAX_TOKENS.get(embedding_model),
        input_bindings=chunk_inputs,
    )
    if (_cached_artifact_sha256(doc_path) != source_sha256
            or _cached_artifact_sha256(chunks_output) != chunks_sha256):
        raise RuntimeError(
            "Source or chunks changed while publishing the quality report")
    return report


def _load_docling_document_snapshot(
        doc_path: Path, document_type) -> tuple[object, dict, str, int]:
    """Parse the model and mapping views from one exact JSON generation."""
    raw, source_sha256, _ = _read_index_artifact_snapshot(doc_path)
    last_error: Exception | None = None
    for encoding in ("utf-8-sig", "latin-1"):
        try:
            decoded = raw.decode(encoding)
            mapping = json.loads(decoded)
            if not isinstance(mapping, dict):
                raise ValueError("DoclingDocument JSON must be an object")
        except (UnicodeDecodeError, json.JSONDecodeError, ValueError) as exc:
            last_error = exc
            continue
        try:
            document = document_type.model_validate(mapping)
            return document, mapping, source_sha256, len(raw)
        except Exception as exc:
            # Pydantic/Docling validation failures are content failures, not a
            # reason to parse the same mapping again under another encoding.
            last_error = exc
            break
    raise ValueError(f"Cannot parse DoclingDocument: {doc_path}") from last_error


def chunk_document(doc_path: Path, chunks_output: Path, *,
                   source_pdf_path: Path | None = None,
                   embedding_model: str = DEFAULT_EMBEDDING_MODEL,
                   max_tokens: int = DEFAULT_MAX_TOKENS,
                   min_words: int = MIN_CHUNK_WORDS,
                   dedup_threshold: float = DEDUP_THRESHOLD,
                   watermark: Optional[re.Pattern] = None,
                   llm_classify: bool = False,
                   zeroshot_classify: bool = False,
                   contextualize: bool = False,
                   ollama_url: str = DEFAULT_OLLAMA_URL,
                   ollama_model: str = DEFAULT_OLLAMA_MODEL,
                   gemini_key: str = "",
                   cloud_url: str = DEFAULT_CLOUD_URL,
                   cloud_model: str = DEFAULT_CLOUD_MODEL,
                   cloud_key: str = "",
                   llm_workers: int = DEFAULT_LLM_WORKERS,
                   thinking: bool = False,
                   reconstruct_headings: bool = False,
                   quality_score: bool = False,
                   llm_scaffold: bool = False,
                   table_children: bool = False,
                   structure_profile: (
                       str | _document_profiles.StructureProfile
                   ) = DEFAULT_STRUCTURE_PROFILE,
                   security_policy: (
                       _release_security.ReleaseSecurityPolicy | None
                   ) = None) -> None:
    """Build one complete chunk artifact set under a path-wide lease."""
    profile = _document_profiles.get_profile(structure_profile)
    chunks_output = Path(chunks_output)
    _run_telemetry.validate_distinct_output_paths({
        "Docling JSON": Path(doc_path),
        "source PDF": (
            Path(source_pdf_path) if source_pdf_path is not None else None),
        "chunks JSONL": chunks_output,
        "conversion completion": _artifact_completion_path(
            Path(doc_path), stage="conversion"),
        "chunk completion": _artifact_completion_path(
            chunks_output, stage="chunking"),
        "quality report": _quality_core.quality_report_path(chunks_output),
    })
    with _chunk_output_lease(chunks_output):
        _chunk_document_locked(
            doc_path, chunks_output,
            source_pdf_path=source_pdf_path,
            embedding_model=embedding_model,
            max_tokens=max_tokens,
            min_words=min_words,
            dedup_threshold=dedup_threshold,
            watermark=watermark,
            llm_classify=llm_classify,
            zeroshot_classify=zeroshot_classify,
            contextualize=contextualize,
            ollama_url=ollama_url,
            ollama_model=ollama_model,
            gemini_key=gemini_key,
            cloud_url=cloud_url,
            cloud_model=cloud_model,
            cloud_key=cloud_key,
            llm_workers=llm_workers,
            thinking=thinking,
            reconstruct_headings=reconstruct_headings,
            quality_score=quality_score,
            llm_scaffold=llm_scaffold,
            table_children=table_children,
            structure_profile=profile,
            security_policy=security_policy,
        )


def _chunk_document_locked(doc_path: Path, chunks_output: Path, *,
                   source_pdf_path: Path | None = None,
                   embedding_model: str = DEFAULT_EMBEDDING_MODEL,
                   max_tokens: int = DEFAULT_MAX_TOKENS,
                   min_words: int = MIN_CHUNK_WORDS,
                   dedup_threshold: float = DEDUP_THRESHOLD,
                   watermark: Optional[re.Pattern] = None,
                   llm_classify: bool = False,
                   zeroshot_classify: bool = False,
                   contextualize: bool = False,
                   ollama_url: str = DEFAULT_OLLAMA_URL,
                   ollama_model: str = DEFAULT_OLLAMA_MODEL,
                   gemini_key: str = "",
                   cloud_url: str = DEFAULT_CLOUD_URL,
                   cloud_model: str = DEFAULT_CLOUD_MODEL,
                   cloud_key: str = "",
                   llm_workers: int = DEFAULT_LLM_WORKERS,
                   thinking: bool = False,
                   reconstruct_headings: bool = False,
                   quality_score: bool = False,
                   llm_scaffold: bool = False,
                   table_children: bool = False,
                   structure_profile: (
                       str | _document_profiles.StructureProfile
                   ) = DEFAULT_STRUCTURE_PROFILE,
                   security_policy: (
                       _release_security.ReleaseSecurityPolicy | None
                   ) = None) -> None:
    """Load a DoclingDocument, chunk with HybridChunker, and enrich."""
    from docling_core.types import DoclingDocument
    from docling_core.transforms.chunker import HybridChunker
    from docling_core.transforms.chunker.tokenizer.huggingface import (
        HuggingFaceTokenizer,
    )
    from tqdm import tqdm

    profile = _document_profiles.get_profile(structure_profile)
    _require_file(doc_path, "DoclingDocument JSON")

    completion_parameters = _chunk_parameters(
        embedding_model=embedding_model, max_tokens=max_tokens,
        min_words=min_words, dedup_threshold=dedup_threshold,
        watermark=watermark, llm_classify=llm_classify,
        zeroshot_classify=zeroshot_classify,
        contextualize=contextualize, ollama_url=ollama_url,
        ollama_model=ollama_model, gemini_key=gemini_key,
        cloud_url=cloud_url, cloud_model=cloud_model, cloud_key=cloud_key,
        llm_workers=llm_workers, thinking=thinking,
        reconstruct_headings=reconstruct_headings,
        quality_score=quality_score, llm_scaffold=llm_scaffold,
        table_children=table_children,
        structure_profile=profile,
        security_policy=security_policy)
    requested_max_tokens = max_tokens
    reserve_tokens = _CONTEXT_TOKEN_RESERVE if contextualize else 0
    max_tokens = _effective_chunk_token_limit(
        embedding_model,
        requested_max_tokens,
        reserve_tokens=reserve_tokens,
    )
    if max_tokens != requested_max_tokens:
        model_limit = EMBEDDING_MAX_TOKENS[embedding_model]
        reserve_note = (
            f" with {reserve_tokens} tokens reserved for context"
            if reserve_tokens else ""
        )
        log.warning(
            f"Capping chunk size from {requested_max_tokens} to {max_tokens} "
            f"for {embedding_model}'s {model_limit}-token limit{reserve_note}."
        )

    log.info(f"Loading DoclingDocument from {doc_path}")
    try:
        dl_doc, doc_dict, source_sha256, source_size = (
            _load_docling_document_snapshot(doc_path, DoclingDocument))
    except (OSError, RuntimeError, ValueError):
        log.error(f"Cannot parse DoclingDocument: {doc_path}")
        log.error("  File may be corrupted. Re-run 'convert' to regenerate.")
        sys.exit(1)

    # Derive total page count from the document itself
    total_pages = 0
    if hasattr(dl_doc, "pages") and dl_doc.pages:
        total_pages = len(dl_doc.pages)
    if total_pages == 0:
        total_pages = 1  # prevent division by zero in fallback estimator

    # Structural ranges must be known before enrichment so TOC/back-matter
    # chunks cannot enter classification or inherit chapter metadata.
    chapter_map = _build_chapter_map_from_document(
        doc_dict, structure_profile=profile)
    book_sections = _identify_book_sections(
        doc_dict, structure_profile=profile)
    has_toc = any(
        book_sections.get(name) is not None
        for name in _document_profiles.toc_seed_keys(profile)
    )
    if not has_toc:
        log.error("FATAL: No Table of Contents or Contents section found.")
        log.error(
            "  The selected structure profile requires a recognized "
            "TOC/Contents section. Cannot proceed.")
        log.error(f"  Structure profile: {profile.name}")
        log.error(f"  Document: {doc_path}")
        sys.exit(1)

    structural_ranges = _book_structural_ranges(
        book_sections, structure_profile=profile)

    # Validate the selected publisher policy before loading tokenizers or
    # producing raw chunks.  A wrong profile must fail without publishing or
    # spending the bulk of a chunking run on an unrecognized hierarchy.
    llm_kwargs = dict(cloud_url=cloud_url, cloud_model=cloud_model,
                      cloud_key=cloud_key, ollama_url=ollama_url,
                      ollama_model=ollama_model, gemini_key=gemini_key,
                      llm_workers=llm_workers, thinking=thinking,
                      security_policy=security_policy)
    scaffold = _build_scaffold(
        doc_dict, book_sections, structure_profile=profile,
        use_llm=llm_scaffold, **llm_kwargs)

    # The chunker tokenizer is for token counting only — it doesn't need to
    # match the embedding model exactly. API models (voyage-*, text-embedding-*,
    # cohere-*) aren't on HuggingFace, so use a local tokenizer as fallback.
    tokenizer_model = embedding_model
    if any(embedding_model.startswith(p) for p in ("voyage-", "text-embedding-", "embed-", "cohere-", "embo-", "minimax-emb")):
        tokenizer_model = DEFAULT_EMBEDDING_MODEL_GENERAL  # stella (local)
        log.info(f"Using {tokenizer_model} tokenizer for chunking "
                 f"(embedding model {embedding_model} is API-only)")
    tokenizer_source, tokenizer_verified = _model_loader_source(
        tokenizer_model, "chunk_tokenizer",
        security_policy=security_policy)
    tokenizer = HuggingFaceTokenizer.from_pretrained(
        model_name=tokenizer_source,
        max_tokens=max_tokens,
        **({
            "local_files_only": True,
            "trust_remote_code": False,
        } if tokenizer_verified else {"trust_remote_code": True}),
    )
    chunker = HybridChunker(tokenizer=tokenizer, merge_peers=True)

    log.info(f"Chunking with model={embedding_model}, max_tokens={max_tokens}")
    raw_chunks = list(tqdm(chunker.chunk(dl_doc), desc="Chunking", unit="chunk"))
    log.info(f"HybridChunker produced {len(raw_chunks)} raw chunks")

    conversion_binding = _load_conversion_source_binding(
        doc_path,
        document_sha256=source_sha256,
        document_size=source_size,
    )
    table_markdown_overrides, table_recovery_input = (
        _recover_bound_table_markdown(
            dl_doc, doc_path, conversion_binding,
            source_pdf_path=source_pdf_path,
            forbidden_output_paths={
                "chunks JSONL": chunks_output,
                "chunk completion": _artifact_completion_path(
                    chunks_output, stage="chunking"),
                "quality report": _quality_core.quality_report_path(
                    chunks_output),
            }))
    if table_markdown_overrides:
        log.info(
            "Recovered %s incomplete tables from source PDF text",
            len(table_markdown_overrides))
    elif conversion_binding is not None and table_recovery_input is None:
        log.warning(
            "No source PDF matching conversion hash is available; "
            "skipping table-text recovery")
    if conversion_binding is None:
        log.debug(
            "Capture-verified conversion lineage is unavailable; "
            "skipping unbound table-text recovery")
    chunk_input_bindings = {
        "docling_json": {
            "name": doc_path.name,
            "size": source_size,
            "sha256": source_sha256,
        },
        "conversion_manifest": (
            {
                "name": conversion_binding.manifest_path.name,
                "sha256": conversion_binding.manifest_sha256,
                "schema_version": conversion_binding.schema_version,
            }
            if conversion_binding is not None else None
        ),
        "table_recovery": table_recovery_input,
    }
    prepared_chunks = _prepare_source_preserving_chunks(
        raw_chunks, dl_doc,
        lambda value: int(tokenizer.count_tokens(value)), max_tokens,
        table_markdown_overrides=table_markdown_overrides,
        structural_ranges=structural_ranges)
    (lineage_item_by_ref, lineage_parent_refs,
     lineage_caption_refs) = _docling_lineage_catalog(dl_doc)

    if len(prepared_chunks) != len(raw_chunks):
        log.info(
            f"Prepared {len(prepared_chunks)} source-preserving chunks from "
            f"{len(raw_chunks)} raw chunks")
    raw_token_counts = [
        int(tokenizer.count_tokens(text))
        for text, _, _, _ in prepared_chunks
    ]

    enriched = []
    filtered_structural = 0
    filtered_tiny = 0
    # Track the last-seen chapter across chunks (chapters propagate forward)
    # --- Pass 1: Parallel enrichment (regex, classification, metadata) ---
    from concurrent.futures import ThreadPoolExecutor
    import multiprocessing

    source_file = doc_path.stem
    def _fully_inside_structural_range(metadata: dict) -> bool:
        page_start = metadata.get("page_start")
        page_end = metadata.get("page_end")
        if page_start is None or page_end is None:
            return False
        return any(
            range_start <= page_start and page_end <= range_end
            for range_start, range_end in structural_ranges
        )

    def _content_source_from_items(doc_items: list | None) -> str | None:
        labels = {
            _doc_item_label(item) for item in (doc_items or [])
            if _doc_item_label(item)
        }
        if labels == {"table"}:
            return "table"
        if "footnote" in labels:
            return "footnote" if labels == {"footnote"} else "mixed"
        return "body" if labels else None

    def _enrich_one(args):
        """Pure function: clean + enrich a single chunk. No shared state."""
        (i, chunk_text, headings, doc_items, preserve_short, total_c, total_p,
         wm_pattern, src_file, token_count) = args
        clean = _normalize_text(
            strip_watermark(chunk_text, _compile_watermark(wm_pattern) if wm_pattern else None))
        content_source = _content_source_from_items(doc_items)
        substantive_labels = {
            _doc_item_label(item) for item in (doc_items or [])
        } & {"text", "list_item", "footnote", "caption", "section_header",
             }
        if (len(clean.split()) < min_words
                and content_source != "table" and not preserve_short
                and not substantive_labels):
            return None, "tiny"
        record = enrich_chunk(
            chunk_text=clean, headings=headings,
            chunk_index=i, total_chunks=total_c,
            total_pages=total_p, doc_items=doc_items,
            source_file=src_file,
            source_items=_source_lineage_for_items(
                doc_items,
                item_by_ref=lineage_item_by_ref,
                parent_refs_by_child=lineage_parent_refs,
                caption_refs_by_parent=lineage_caption_refs,
            ),
            structure_profile=profile,
        )
        if (_fully_inside_structural_range(record["metadata"])
                or record["metadata"]["content_type"] == "structural"):
            return None, "structural"
        if content_source:
            record["metadata"]["content_source"] = content_source
        if content_source == "footnote":
            record["metadata"]["content_type"] = "footnote"
        item_refs = {
            str(getattr(item, "self_ref", ""))
            for item in (doc_items or [])
        }
        if item_refs & set(table_markdown_overrides):
            record["metadata"]["table_recovered_from_pdf"] = True
        record["metadata"]["token_count"] = token_count
        return record, "ok"

    # Prepare args (avoid passing unpicklable objects)
    wm_str = watermark.pattern if watermark else ""
    enrich_args = [
        (i,
         chunk_text,
         headings,
         doc_items, preserve_short,
         len(prepared_chunks), total_pages, wm_str, source_file)
        + (raw_token_counts[i],)
        for i, (chunk_text, headings, doc_items, preserve_short)
        in enumerate(prepared_chunks)
    ]

    # Use threads (not processes) to avoid pickling issues with doc_items
    n_workers = min(multiprocessing.cpu_count(), 8, len(prepared_chunks))
    if n_workers > 1 and len(prepared_chunks) > 200:
        with ThreadPoolExecutor(max_workers=n_workers) as pool:
            results = list(tqdm(pool.map(_enrich_one, enrich_args),
                                total=len(enrich_args),
                                desc="Enriching", unit="chunk"))
    else:
        results = [_enrich_one(a) for a in tqdm(enrich_args,
                                                 desc="Enriching", unit="chunk")]

    for record, status in results:
        if status == "tiny":
            filtered_tiny += 1
        elif status == "structural":
            filtered_structural += 1
        elif record:
            enriched.append(record)

    if filtered_structural or filtered_tiny:
        log.info(f"Filtered: {filtered_structural} structural (TOC/index), "
                 f"{filtered_tiny} tiny (<{min_words} words)")

    before_coalescing = len(enriched)
    enriched = _coalesce_chunk_boundaries(
        enriched,
        lambda value: int(tokenizer.count_tokens(value)),
        max_tokens,
        hard_max_tokens=max(
            max_tokens,
            _effective_chunk_token_limit(
                embedding_model,
                EMBEDDING_MAX_TOKENS.get(embedding_model, max_tokens),
                reserve_tokens=reserve_tokens,
            ),
        ),
        structure_profile=profile,
    )
    if len(enriched) != before_coalescing:
        log.info(
            f"Boundary repair coalesced {before_coalescing - len(enriched)} "
            "split chunks")

    # --- Pass 2: Chapter assignment (team-orchestrated) ---
    # Uses the 4-tier agent team hierarchy:
    #   Director (I) → Manager (II) → QC (III) → Expert(s) (IV)
    # Scaffold from TOC is the single source of truth.

    # ── Team 1: Scaffold Construction ──
    # (already orchestrated before tokenizer loading so profile mismatches
    # fail early and cannot publish a guessed hierarchy)

    _scaffold_ch_titles = _canonical_chapter_titles(
        scaffold, chapter_map, structure_profile=profile)
    scaffold = _normalize_scaffold_metadata(scaffold, _scaffold_ch_titles)
    observed_chapter_end = max(
        (info.get("max_page", 0) for info in chapter_map.values()),
        default=max((entry.get("page", 0) for entry in scaffold), default=0),
    )
    scaffold_lookup = _build_scaffold_lookup(
        scaffold, max_page=observed_chapter_end)
    log.info(f"Scaffold: {len(scaffold)} entries, "
             f"lookup covers {len(scaffold_lookup)} pages")

    def _apply_scaffold_to_chunks(chunks, s_lookup, ch_map, ch_titles):
        """Assign chapter/section metadata from scaffold lookup."""
        assigned = 0

        def _canonical_path(path: str, chapter_num: int | None) -> str:
            chapter_title = ch_titles.get(chapter_num) if chapter_num else None
            if not chapter_title:
                return path
            parts = [part.strip() for part in path.split(" > ") if part.strip()]
            if (parts and _document_profiles.match_division(
                    parts[0], profile, "canonical_title") is not None):
                parts[0] = chapter_title
            elif not parts or parts[0] != chapter_title:
                parts.insert(0, chapter_title)
            return " > ".join(parts)

        for rec in chunks:
            page = rec["metadata"].get("page_start")
            if page is None:
                pr = rec["metadata"].get("page_range", "")
                m = re.search(r"(\d+)", pr)
                if m:
                    page = int(m.group(1))
            if page and page in s_lookup:
                entry = s_lookup[page]
                ch_num = entry.get("chapter_num")
                if ch_num is not None:
                    rec["metadata"]["chapter_num"] = ch_num
                    rec["metadata"]["chapter_title"] = ch_titles.get(
                        ch_num, entry.get("title", ""))
                    assigned += 1
                if entry.get("path"):
                    rec["metadata"]["section_path"] = _canonical_path(
                        entry["path"], ch_num)
            elif page and ch_map:
                ch_num, ch_title = _assign_chapter_by_page(page, ch_map)
                if ch_num is not None:
                    rec["metadata"]["chapter_num"] = ch_num
                    rec["metadata"]["chapter_title"] = ch_titles.get(
                        ch_num, ch_title)
                    rec["metadata"]["section_path"] = _canonical_path(
                        rec["metadata"].get("section_path", ""), ch_num)
                    assigned += 1
        return assigned

    def _remediate_chunks(chunks, s_lookup, ch_map, ch_titles, qc_result):
        """Fix mismatched and unassigned chunks identified by QC."""
        fixed = 0
        for mm in qc_result.get("mismatched_sections", []):
            idx = mm["chunk_idx"]
            pg = mm["page"]
            if pg in s_lookup:
                entry = s_lookup[pg]
                ch = entry.get("chapter_num")
                chunks[idx]["metadata"]["chapter_num"] = ch
                chunks[idx]["metadata"]["chapter_title"] = (
                    ch_titles.get(ch, entry.get("title", ""))
                    if ch is not None else entry.get("title", ""))
                if entry.get("path"):
                    chunks[idx]["metadata"]["section_path"] = entry["path"]
                fixed += 1
        for rec in chunks:
            if rec["metadata"].get("chapter_num") is None:
                pg = rec["metadata"].get("page_start")
                if pg is None:
                    pr = rec["metadata"].get("page_range", "")
                    m_pg = re.search(r"(\d+)", pr)
                    if m_pg:
                        pg = int(m_pg.group(1))
                if pg and ch_map:
                    ch, ct = _assign_chapter_by_page(pg, ch_map)
                    if ch is not None:
                        rec["metadata"]["chapter_num"] = ch
                        rec["metadata"]["chapter_title"] = ch_titles.get(ch, ct)
                        fixed += 1
        return fixed

    # ── Team 2: Chapter Assignment ──
    assign_team = _AgentTeam(
        "Chapter Assignment",
        context=f"{len(enriched)} chunks, {len(scaffold)} scaffold entries, "
                f"{len(_scaffold_ch_titles)} chapters",
        enabled=llm_scaffold,
        **llm_kwargs,
    )

    # I. Director plans
    assign_plan = assign_team.director_plan(
        "Assign correct chapter number, chapter title, and hierarchical "
        "section path to every chunk using the authoritative scaffold")

    # II. Manager decomposes
    assign_subtasks = assign_team.manager_decompose(assign_plan)

    # III. QC defines criteria
    assign_criteria = assign_team.qc_criteria(assign_subtasks)

    # IV ↔ III: Expert executes + QC validates (2-strike rule)
    qc_report: dict = {"passed": False, "issues": []}

    for attempt in range(1, 3):  # max 2 attempts
        # IV. Expert: apply scaffold to chunks
        assign_team._log(4, f"Applying scaffold (attempt {attempt}/2)")

        if attempt > 1:
            # On retry, clear assignments and re-apply
            for rec in enriched:
                rec["metadata"]["chapter_num"] = None
                rec["metadata"]["chapter_title"] = None

        assigned = _apply_scaffold_to_chunks(
            enriched, scaffold_lookup, chapter_map, _scaffold_ch_titles)
        assign_team._log(4, f"Assigned {assigned}/{len(enriched)} chunks")

        # III. QC validates
        prog_check = _validate_against_scaffold(enriched, scaffold, chapter_map)

        if not prog_check["passed"] and attempt == 1:
            # First failure: remediate before QC judgment
            fixed = _remediate_chunks(
                enriched, scaffold_lookup, chapter_map,
                _scaffold_ch_titles, prog_check)
            if fixed:
                assign_team._log(4, f"Remediated {fixed} chunks")
                prog_check = _validate_against_scaffold(
                    enriched, scaffold, chapter_map)

        summary = (f"{assigned} assigned, "
                   f"{prog_check.get('unassigned_chunks', 0)} unassigned, "
                   f"coverage {prog_check.get('coverage', 0):.0%}, "
                   f"{len(prog_check.get('mismatched_sections', []))} mismatches")

        qc_report = assign_team.qc_validate(summary, assign_criteria, prog_check)

        if qc_report["passed"]:
            break
        if attempt < 2:
            assign_team._log(3, "Strike 1 — retrying with remediation")

    if not qc_report["passed"]:
        assign_team.flagged.extend(qc_report["issues"])
        assign_team._log(3, "FLAGGED for manual review after 2 strikes")
        qc_path = chunks_output.parent / f"{chunks_output.stem}_qc_flags.json"
        prog_check["doc_path"] = str(doc_path)
        prog_check["team_audit"] = assign_team.audit
        _atomic_write_text(
            qc_path, json.dumps(prog_check, indent=2, default=str))
        log.warning(f"QC report saved to {qc_path}")

    # II. Manager reviews edge cases
    mgr_review = assign_team.manager_review(
        f"{assigned}/{len(enriched)} chunks assigned to "
        f"{len(_scaffold_ch_titles)} chapters. "
        f"Unassigned: {prog_check.get('unassigned_chunks', 0)}. "
        f"Source: {doc_path.stem}",
        qc_report)

    # I. Director acceptance test: spot-check chunk assignments
    def _director_spot_check():
        """Check a sample of chunks for correct chapter assignment."""
        sample = enriched[::max(1, len(enriched) // 10)][:10]
        issues = []
        for rec in sample:
            pg = rec["metadata"].get("page_start")
            ch = rec["metadata"].get("chapter_num")
            if ch is None and pg:
                issues.append(f"p.{pg}: no chapter assigned")
            elif pg and pg in scaffold_lookup:
                expected = scaffold_lookup[pg].get("chapter_num")
                if expected is not None and expected != ch:
                    issues.append(f"p.{pg}: chapter {ch} vs scaffold {expected}")
        return {"checked": len(sample), "issues": issues}

    assign_team.director_accept(
        f"{assigned}/{len(enriched)} assigned, QC {'PASSED' if qc_report['passed'] else 'FAILED'}",
        mgr_review,
        test_fn=_director_spot_check)

    if assign_team.flagged:
        log.warning(f"Assignment team flagged {len(assign_team.flagged)} issues")

    # --- Pass 3: Heading hierarchy reconstruction (LLM) ---
    if reconstruct_headings:
        from concurrent.futures import ThreadPoolExecutor, as_completed

        llm_kwargs = dict(ollama_url=ollama_url, ollama_model=ollama_model,
                          gemini_key=gemini_key, cloud_url=cloud_url,
                          cloud_model=cloud_model, cloud_key=cloud_key,
                          llm_workers=llm_workers, thinking=thinking,
                          security_policy=security_policy)

        # Only reconstruct low-quality headings (bare letters/numerals)
        low_quality = [r for r in enriched
                       if len(r["metadata"].get("section_path", "")) < 5
                       and r["metadata"].get("chapter_num") is not None]
        log.info(f"Reconstructing {len(low_quality)} low-quality headings...")

        is_cloud = bool(cloud_url and cloud_key)
        workers = min(llm_workers, len(low_quality)) if is_cloud else 1
        reconstructed = 0

        def _do_reconstruct(rec):
            sp = rec["metadata"].get("section_path", "")
            ch_num = rec["metadata"]["chapter_num"]
            ch_title = rec["metadata"].get("chapter_title", "")
            heading = sp if sp else "(none)"
            return _reconstruct_heading(
                rec["text"], heading, ch_num, ch_title,
                structure_profile=profile, **llm_kwargs)

        if workers > 1 and len(low_quality) > 0:
            with ThreadPoolExecutor(max_workers=workers) as pool:
                futures = {pool.submit(_do_reconstruct, rec): rec
                           for rec in low_quality}
                for future in as_completed(futures):
                    rec = futures[future]
                    try:
                        new_path = future.result()
                        if new_path:
                            rec["metadata"]["section_path"] = new_path
                            reconstructed += 1
                    except (LLMBudgetExceeded, LLMExecutionError):
                        raise
                    except Exception:
                        pass
        else:
            for rec in low_quality:
                new_path = _do_reconstruct(rec)
                if new_path:
                    rec["metadata"]["section_path"] = new_path
                    reconstructed += 1

        log.info(f"Reconstructed {reconstructed}/{len(low_quality)} headings")

    # --- Zero-shot classification (batched GPU inference) ---
    if zeroshot_classify:
        zs_upgraded = 0
        log.info(f"Zero-shot classifying {len(enriched)} chunks "
                 f"(facebook/bart-large-mnli, batched GPU)")

        # Initialize classifier once
        global _zeroshot_classifier
        if _zeroshot_classifier is None:
            try:
                from transformers import pipeline as hf_pipeline
                log.info(
                    f"Loading zero-shot classifier: {DEFAULT_ZEROSHOT_MODEL}")
                model_source, verified = _model_loader_source(
                    DEFAULT_ZEROSHOT_MODEL, "zero_shot_classifier",
                    security_policy=security_policy)
                _zeroshot_classifier = hf_pipeline(
                    "zero-shot-classification",
                    model=model_source,
                    **({
                        "tokenizer": model_source,
                        "model_kwargs": {
                            "local_files_only": True,
                            "use_safetensors": True,
                        },
                    } if verified else {}),
                    device=0,
                )
            except Exception as e:
                log.warning(f"Zero-shot classifier unavailable: {e}")

        if _zeroshot_classifier is not None:
            # Prepare all inputs at once
            inputs = []
            for rec in enriched:
                hdgs = rec["metadata"].get("headings")
                ctx = f"[Section: {' > '.join(hdgs)}] " if hdgs else ""
                inputs.append(ctx + rec["text"][:512])

            # Batched inference — 10-30x faster than one-at-a-time
            ZS_BATCH = 32
            all_results = []
            for start in tqdm(range(0, len(inputs), ZS_BATCH),
                              desc="Zero-shot classify", unit="batch",
                              total=(len(inputs) + ZS_BATCH - 1) // ZS_BATCH):
                batch = inputs[start:start + ZS_BATCH]
                batch_results = _zeroshot_classifier(
                    batch, _ZS_LABELS,
                    multi_label=False, batch_size=ZS_BATCH,
                )
                # Handle single result (not wrapped in list)
                if isinstance(batch_results, dict):
                    batch_results = [batch_results]
                all_results.extend(batch_results)

            # Apply results
            for rec, result in zip(enriched, all_results):
                top_label = result["labels"][0]
                top_score = result["scores"][0]
                if top_score >= 0.4:
                    mapped = _ZS_LABEL_MAP.get(top_label)
                    if mapped and mapped != rec["metadata"]["content_type"]:
                        rec["metadata"]["content_type"] = mapped
                        zs_upgraded += 1

        log.info(f"Zero-shot reclassified {zs_upgraded}/{len(enriched)} chunks")

    # --- LLM classification + contextual retrieval ---
    if llm_classify or contextualize:
        from concurrent.futures import ThreadPoolExecutor, as_completed
        import threading

        llm_upgraded = 0
        ctx_added = 0
        desc = []
        if llm_classify:
            if cloud_url and cloud_key:
                desc.append(f"classifying ({cloud_model or ollama_model})")
            else:
                desc.append(f"classifying ({ollama_model})")
        if contextualize:
            desc.append("contextualizing")
        provider_chain = (
            f"Cloud ({_llm_endpoint_id(cloud_url)}) -> "
            if cloud_url and cloud_key else "")

        # Parallel when using cloud API, sequential for local Ollama
        is_cloud = bool(cloud_url and cloud_key)
        workers = llm_workers if is_cloud else 1
        log.info(f"LLM pass: {' + '.join(desc)} {len(enriched)} chunks "
                 f"({provider_chain}Ollama -> Gemini -> regex fallback)"
                 f"{f' [{workers} parallel workers]' if workers > 1 else ''}")

        llm_kwargs = dict(ollama_url=ollama_url, ollama_model=ollama_model,
                          gemini_key=gemini_key, cloud_url=cloud_url,
                          cloud_model=cloud_model, cloud_key=cloud_key,
                          llm_workers=llm_workers, thinking=thinking,
                          security_policy=security_policy)
        _lock = threading.Lock()

        def _process_chunk(rec):
            """Classify and/or contextualize a single chunk."""
            text = rec["text"]
            hdgs = rec["metadata"].get("headings")
            ch_title = rec["metadata"].get("chapter_title", "")
            label = None
            ctx = None

            if llm_classify:
                label = _llm_classify(text, hdgs, **llm_kwargs)
            if contextualize:
                ctx = _generate_context(text, hdgs, ch_title, **llm_kwargs)

            return rec, label, ctx

        if workers > 1:
            # Parallel cloud API calls
            with ThreadPoolExecutor(max_workers=workers) as pool:
                futures = {pool.submit(_process_chunk, rec): rec
                           for rec in enriched}
                pbar = tqdm(total=len(enriched), desc="LLM enrichment",
                            unit="chunk")
                for future in as_completed(futures):
                    rec, label, ctx = future.result()
                    if llm_classify and label:
                        if label != rec["metadata"]["content_type"]:
                            rec["metadata"]["content_type"] = label
                            llm_upgraded += 1
                    if contextualize and ctx:
                        rec["metadata"]["context"] = ctx
                        ctx_added += 1
                    pbar.update(1)
                pbar.close()
        else:
            # Sequential (local Ollama or single-threaded)
            for rec in tqdm(enriched, desc="LLM enrichment", unit="chunk"):
                _, label, ctx = _process_chunk(rec)
                if llm_classify and label:
                    if label != rec["metadata"]["content_type"]:
                        rec["metadata"]["content_type"] = label
                        llm_upgraded += 1
                if contextualize and ctx:
                    rec["metadata"]["context"] = ctx
                    ctx_added += 1

        if llm_classify:
            log.info(f"LLM reclassified {llm_upgraded}/{len(enriched)} chunks")
        if contextualize:
            log.info(f"Context generated for {ctx_added}/{len(enriched)} chunks")

    # --- Quality scoring ---
    if quality_score:
        from concurrent.futures import ThreadPoolExecutor as _QSPool
        is_cloud = bool(cloud_url and cloud_key)
        qs_workers = llm_workers if is_cloud else 1
        log.info(f"Quality scoring {len(enriched)} chunks "
                 f"({qs_workers} workers)...")

        def _qs_one(rec):
            return rec, _score_chunk_quality(
                rec["text"], rec["metadata"]["content_type"], **llm_kwargs)

        with _QSPool(max_workers=qs_workers) as pool:
            futures = {pool.submit(_qs_one, r): r for r in enriched}
            from tqdm import tqdm as _qs_tqdm
            for future in _qs_tqdm(as_completed(futures), total=len(futures),
                                    desc="Quality scoring", unit="chunk"):
                rec, score = future.result()
                rec["metadata"]["quality_score"] = score

        score_dist: dict[int, int] = {}
        for rec in enriched:
            s = rec["metadata"].get("quality_score", 0)
            score_dist[s] = score_dist.get(s, 0) + 1
        log.info(f"Quality distribution: {dict(sorted(score_dist.items()))}")

    # Preserve repeated source-table rows that row packing renders as
    # byte-identical fragments.  The first occurrence keeps its historical ID;
    # later occurrences receive deterministic disambiguators.
    annotated_fragments = (
        _table_retrieval_core.annotate_table_fragment_occurrences(
            enriched, stable_id_fn=_chunk_id))
    if annotated_fragments:
        log.info(
            "Disambiguated %s otherwise identical table fragments",
            annotated_fragments,
        )

    # Deduplicate near-identical chunks
    enriched = _deduplicate_chunks(enriched, threshold=dedup_threshold)

    if table_children:
        primary_count = len(enriched)
        enriched = _table_retrieval_core.expand_table_records(
            enriched,
            stable_id_fn=_chunk_id,
            token_count_fn=lambda value: int(tokenizer.count_tokens(value)),
        )
        child_count = len(enriched) - primary_count
        log.info(
            "Generated %s header-propagated table retrieval children",
            child_count,
        )

    # Re-index chunk_index after filtering
    for i, rec in enumerate(enriched):
        rec["metadata"]["chunk_index"] = i

    # Stable adjacency is meaningful only after every filter, merge, and
    # deduplication decision has established the canonical published order.
    _retrieval_core._attach_retrieval_linkage(enriched)

    _validate_chunk_structure_for_publication(
        enriched,
        scaffold=scaffold,
        book_sections=book_sections,
        chapter_map=chapter_map,
        chapter_titles=_scaffold_ch_titles,
        structure_profile=profile,
    )

    # Record the final embedding payload size with the provider/model tokenizer
    # where available, including special tokens and contextual prefixes.
    if embedding_model in EMBEDDING_MAX_TOKENS:
        _validate_embedding_token_counts(
            enriched, embedding_model, recompute=True)
    else:
        final_counts, _ = _count_embedding_text_tokens(
            [_embedding_text(record) for record in enriched], embedding_model)
        for record, token_count in zip(enriched, final_counts):
            record["metadata"]["embedding_token_count"] = token_count

    # Publish a complete artifact atomically. A killed chunking run therefore
    # leaves either the previous corpus or the complete new corpus, never a
    # partially truncated JSONL file.
    if _cached_artifact_sha256(doc_path) != source_sha256:
        raise RuntimeError(
            f"Docling source changed while chunking: {doc_path}")
    _atomic_write_jsonl(chunks_output, enriched)
    _write_artifact_completion(
        _artifact_completion_path(chunks_output, stage="chunking"),
        stage="chunking", source_sha256=source_sha256,
        source_record_count=None, parameters=completion_parameters,
        outputs={"chunks_jsonl": chunks_output},
        schema_version=CHUNK_COMPLETION_SCHEMA_VERSION,
        extra_fields={
            "inputs": chunk_input_bindings,
            "structure_profile": completion_parameters["structure_profile"],
            "structure_profile_parameters_sha256": (
                _structure_profile_parameters_binding(
                    _artifact_parameters_sha256(completion_parameters),
                    completion_parameters["structure_profile"])),
        })
    quality_report = _publish_corpus_quality_report_locked(
        doc_path,
        chunks_output,
        parameters=completion_parameters,
        structural_ranges=structural_ranges,
        document_snapshot=(doc_dict, source_sha256, source_size),
        chunk_inputs=chunk_input_bindings,
        structure_profile=profile,
    )

    # Stats
    type_counts: dict[str, int] = {}
    for rec in enriched:
        ct = rec["metadata"]["content_type"]
        type_counts[ct] = type_counts.get(ct, 0) + 1

    word_counts = [len(rec["text"].split()) for rec in enriched]
    avg_wc = sum(word_counts) / len(word_counts) if word_counts else 0

    log.info(f"Wrote {len(enriched)} enriched chunks → {chunks_output}")
    log.info(
        "Corpus quality: %s → %s",
        quality_report["status"].upper(),
        _quality_core.quality_report_path(chunks_output),
    )
    log.info(f"Content types: {json.dumps(type_counts, indent=2)}")
    if word_counts:
        log.info(f"Word counts — min: {min(word_counts)}, "
                 f"max: {max(word_counts)}, avg: {avg_wc:.0f}")
    else:
        log.info("Word counts — no chunks retained")


# ---------------------------------------------------------------------------
# Step 3: Index into ChromaDB
# ---------------------------------------------------------------------------

def _prepare_chroma_batch(
        batch: list[dict],
) -> tuple[list[str], list[str], list[str], list[dict]]:
    """Prepare embedding inputs separately from stored Chroma documents.

    Context improves embeddings but is metadata, not part of the source chunk.
    Keeping it out of ``documents`` makes Chroma and Qdrant return the same raw
    text while both still embed the contextualized representation.
    """
    ids = [_chunk_id(record) for record in batch]
    documents = [record["text"] for record in batch]
    embedding_inputs = []
    metadatas = []
    numeric_fields = {
        "page_start", "page_end", "chapter_num", "table_rows", "table_cols",
    }
    for record, document, stable_id in zip(batch, documents, ids):
        embedding_inputs.append(_embedding_text(record))

        metadata = record["metadata"].copy()
        # Chroma uses this value as the document ID but does not return IDs in
        # ordinary metadata queries. Persist it in metadata as well so source
        # citations have the same stable identifier contract as Qdrant.
        metadata["stable_id"] = stable_id
        for key in ("case_names", "cross_references", "headings"):
            value = metadata.get(key, [])
            if isinstance(value, list):
                metadata[key] = "|".join(str(item) for item in value)
            elif value is None:
                metadata[key] = ""
            elif not isinstance(value, str):
                metadata[key] = str(value)
        metadata["primary_case"] = metadata.get("primary_case") or ""
        metadata["chapter_title"] = metadata.get("chapter_title") or ""
        for key, value in list(metadata.items()):
            if value is None:
                metadata[key] = -1 if key in numeric_fields else ""
            elif isinstance(value, float) and not math.isfinite(value):
                raise ValueError(
                    f"Chroma metadata field '{key}' must be finite")
            elif isinstance(value, (list, dict)):
                metadata[key] = json.dumps(
                    value, ensure_ascii=False, sort_keys=True)
            elif not isinstance(value, (str, int, float, bool)):
                metadata[key] = str(value)
        metadatas.append(metadata)
    return ids, embedding_inputs, documents, metadatas


def _chroma_exact_count(collection, collection_name: str) -> int:
    """Return Chroma's API-visible logical record count or fail closed."""
    count = collection.count()
    if (isinstance(count, bool) or not isinstance(count, int)
            or count < 0):
        raise RuntimeError(
            f"Chroma collection '{collection_name}' returned an invalid "
            "logical record count")
    return count


def _chroma_mutation_batch_size(client, *, upper_bound: int = 1000) -> int:
    """Return a conservative batch size within the client's advertised cap."""
    if (isinstance(upper_bound, bool) or not isinstance(upper_bound, int)
            or upper_bound < 1):
        raise ValueError("Chroma mutation upper_bound must be a positive integer")
    get_max_batch_size = getattr(client, "get_max_batch_size", None)
    if callable(get_max_batch_size):
        max_batch_size = get_max_batch_size()
    else:
        missing = object()
        max_batch_size = getattr(client, "max_batch_size", missing)
        if max_batch_size is missing:
            return upper_bound
    if (isinstance(max_batch_size, bool)
            or not isinstance(max_batch_size, int)
            or max_batch_size < 1):
        raise RuntimeError("Chroma client returned an invalid maximum batch size")
    return min(upper_bound, max_batch_size)


def _chroma_stable_id_rows(
        collection, collection_name: str, *,
        page_size: int = 1000) -> list[tuple[str, str | None]]:
    """Return every Chroma ID through a count-bounded, drift-safe scan."""
    if (isinstance(page_size, bool) or not isinstance(page_size, int)
            or page_size < 1):
        raise ValueError("Chroma get page_size must be a positive integer")

    count_before = _chroma_exact_count(collection, collection_name)
    rows: list[tuple[str, str | None]] = []
    seen_ids: set[str] = set()
    max_get_calls = count_before + 1
    for _ in range(max_get_calls):
        result = collection.get(
            limit=page_size,
            offset=len(rows),
            include=["metadatas"],
        )
        if not isinstance(result, dict):
            raise RuntimeError("Chroma get returned an invalid result")
        ids = result.get("ids")
        metadatas = result.get("metadatas")
        if not isinstance(ids, list) or not isinstance(metadatas, list):
            raise RuntimeError(
                "Chroma get omitted its IDs or metadata page")
        if len(ids) != len(metadatas):
            raise RuntimeError(
                "Chroma get returned misaligned IDs and metadata")
        if len(ids) > page_size:
            raise RuntimeError(
                "Chroma get returned more records than its page limit")
        if not ids:
            count_after = _chroma_exact_count(collection, collection_name)
            if len(rows) != count_before or count_after != count_before:
                raise RuntimeError(
                    f"Chroma collection '{collection_name}' changed or "
                    "returned an incomplete exact-count scan")
            return rows

        for physical_id, metadata in zip(ids, metadatas):
            if len(rows) >= count_before:
                raise RuntimeError(
                    "Chroma get exceeded its logical record count")
            if not isinstance(physical_id, str) or not physical_id.strip():
                raise RuntimeError(
                    "Chroma get returned an invalid document ID")
            if physical_id in seen_ids:
                raise RuntimeError(
                    "Chroma get repeated a document ID")
            seen_ids.add(physical_id)
            metadata = metadata if isinstance(metadata, dict) else {}
            stable_id = metadata.get("stable_id")
            if not isinstance(stable_id, str) or not stable_id.strip():
                stable_id = None
            rows.append((physical_id, stable_id))

    raise RuntimeError("Chroma get exceeded its exact-count page budget")


def _require_chroma_stable_ids(
        collection, collection_name: str, expected: set[str], *,
        page_size: int = 1000) -> set[str]:
    """Require exact Chroma document and metadata stable-ID identities."""
    rows = _chroma_stable_id_rows(
        collection, collection_name, page_size=page_size)
    physical_ids = {physical_id for physical_id, _ in rows}
    metadata_ids: dict[str, list[str]] = {}
    untracked = 0
    mismatched = 0
    for physical_id, stable_id in rows:
        if stable_id is None:
            untracked += 1
            continue
        metadata_ids.setdefault(stable_id, []).append(physical_id)
        if stable_id != physical_id:
            mismatched += 1

    missing = expected - physical_ids
    unexpected = physical_ids - expected
    duplicates = sum(
        len(ids) - 1 for ids in metadata_ids.values() if len(ids) > 1)
    if missing or unexpected or duplicates or untracked or mismatched:
        details = []
        if missing:
            details.append(f"{len(missing)} stable ID(s) not found")
        if unexpected:
            details.append(f"{len(unexpected)} unexpected stable ID(s)")
        if duplicates:
            details.append(f"{duplicates} duplicate stable-ID record(s)")
        if untracked:
            details.append(f"{untracked} record(s) without a stable ID")
        if mismatched:
            details.append(
                f"{mismatched} document ID/stable ID mismatch(es)")
        raise RuntimeError(
            f"Chroma collection '{collection_name}' does not match its "
            f"stable-ID manifest ({'; '.join(details)}). Refusing to update "
            "the manifest; run again with --full-reindex.")
    return physical_ids


def _new_vector_update_lifecycle(
        db_dir: Path, *, backend: str, collection_name: str,
        source_sha256: str, source_record_count: int,
        target_ids: set[str] | frozenset[str],
        active_update_token: str | None,
        ) -> _vector_lifecycle.VectorUpdateLifecycle:
    """Compose backend-neutral lifecycle policy with late-bound facades."""
    if backend == "chroma":
        marker_path = _chroma_update_marker_path(
            db_dir, collection_name=collection_name)

        def begin_update(owner_token: str, replace_existing: bool):
            return _begin_chroma_index_update(
                db_dir, collection_name=collection_name,
                source_sha256=source_sha256,
                source_record_count=source_record_count,
                owner_token=owner_token,
                replace_existing=replace_existing)
    elif backend == "qdrant":
        marker_path = _qdrant_update_marker_path(
            db_dir, collection_name=collection_name)

        def begin_update(owner_token: str, replace_existing: bool):
            return _begin_qdrant_index_update(
                db_dir, collection_name=collection_name,
                source_sha256=source_sha256,
                source_record_count=source_record_count,
                owner_token=owner_token,
                replace_existing=replace_existing)
    else:
        raise ValueError(f"Unsupported vector lifecycle backend: {backend}")

    def marker_owned(owner_token: str | None) -> bool:
        return _index_update_marker_owned_by(
            marker_path, owner_token, backend=backend,
            collection_name=collection_name)

    def finish_update(owner_token: str) -> None:
        _finish_index_update(
            marker_path, owner_token=owner_token, backend=backend,
            collection_name=collection_name)

    guard = _vector_lifecycle.UpdateGuard(
        backend=backend,
        collection_name=collection_name,
        marker_path=marker_path,
        active_token=active_update_token,
        marker_owned_fn=marker_owned,
        begin_update_fn=begin_update,
        finish_update_fn=finish_update,
        token_factory=lambda: uuid4().hex,
    )
    return _vector_lifecycle.VectorUpdateLifecycle(
        target_ids=target_ids, guard=guard)


def _index_chunks_chroma_impl(
        chunks_path: Path, chroma_dir: Path, *,
        collection_name: str = DEFAULT_COLLECTION,
        embedding_model: str = DEFAULT_EMBEDDING_MODEL,
        full_reindex: bool = False,
        security_policy: (
            _release_security.ReleaseSecurityPolicy | None
        ) = None,
        _active_update_token: str | None = None,
        _client_owner: _VectorClientOwner,
        _operation_tracker: _IndexOperationalTracker,
        ) -> _operation_contracts.IndexOutcome:
    """Load enriched chunks and index into a local ChromaDB collection."""
    import chromadb
    from tqdm import tqdm

    _require_file(chunks_path, "Chunks JSONL")

    log.info(f"Loading chunks from {chunks_path}")
    (records, source_sha256, _,
     quality_binding) = _load_index_snapshot_with_quality(
         chunks_path, allow_legacy_quality=False)
    quality_schema_version, quality_report_sha256 = quality_binding
    source_record_count = len(records)
    table_child_count = _table_retrieval_core.table_child_count(records)
    _validate_embedding_token_counts(
        records, embedding_model, recompute=True)
    chunk_info = [(record, _chunk_id(record), _chunk_hash(record))
                  for record in records]
    new_hashes = {chunk_id: chunk_hash
                  for _, chunk_id, chunk_hash in chunk_info}
    # Normalize every metadata payload before any incompatible collection is
    # deleted, so a malformed late batch cannot destroy a working index first.
    _prepare_chroma_batch(records)
    log.info(f"Loaded {len(records)} chunks")

    chroma_dir = _storage_policy.ensure_private_tree(chroma_dir)
    client = _client_owner.own(
        chromadb.PersistentClient(
            path=str(chroma_dir), **_chroma_settings_kwargs(chromadb)))

    try:
        collection = client.get_collection(collection_name)
        collection_exists = True
    except Exception:
        collection = None
        collection_exists = False

    embedding_dimension = _embedding_dimension(
        embedding_model, security_policy=security_policy)
    old_hashes, rebuild_collection, rebuild_reason = (
        _resolve_incremental_index_state(
            chroma_dir, backend="chroma", collection_name=collection_name,
            embedding_model=embedding_model,
            embedding_dimension=embedding_dimension,
            collection_exists=collection_exists,
            full_reindex=full_reindex,
            active_update_token=_active_update_token,
        )
    )
    collection_existed_at_start = collection_exists
    plan = _vector_lifecycle.plan_reconciliation(chunk_info, old_hashes)
    new_hashes = plan.new_hashes
    update_lifecycle = _new_vector_update_lifecycle(
        chroma_dir, backend="chroma", collection_name=collection_name,
        source_sha256=source_sha256,
        source_record_count=source_record_count,
        target_ids=plan.target_ids,
        active_update_token=_active_update_token)
    operation_tracker = _operation_tracker

    if rebuild_collection:
        log.info("Rebuilding Chroma collection '%s': %s",
                 collection_name, rebuild_reason)

    def _delete_collection() -> None:
        operation_tracker.collection_delete_calls += 1
        client.delete_collection(collection_name)

    def _create_collection():
        operation_tracker.collection_create_calls += 1
        return client.get_or_create_collection(
            name=collection_name,
            metadata={"hnsw:space": "cosine"},
        )

    def _verify_stable_ids(handle, expected):
        return _require_chroma_stable_ids(
            handle, collection_name, expected)

    def _delete_stable_ids(handle, stable_ids, _verified) -> None:
        delete_batch_size = _chroma_mutation_batch_size(client)
        for start in range(0, len(stable_ids), delete_batch_size):
            operation_tracker.record_delete_calls += 1
            handle.delete(
                ids=list(stable_ids[start:start + delete_batch_size]))

    reconciled = _vector_lifecycle.reconcile_collection(
        lifecycle=update_lifecycle,
        plan=plan,
        handle=collection,
        collection_exists=collection_exists,
        rebuild_collection=rebuild_collection,
        delete_collection_fn=_delete_collection,
        create_collection_fn=_create_collection,
        verify_stable_ids_fn=_verify_stable_ids,
        delete_stable_ids_fn=_delete_stable_ids,
    )
    collection = reconciled.handle

    def _save_manifest():
        return _save_index_manifest(
            chroma_dir, backend="chroma",
            collection_name=collection_name,
            embedding_model=embedding_model,
            embedding_dimension=embedding_dimension,
            chunk_hashes=new_hashes, source_sha256=source_sha256,
            source_record_count=source_record_count,
            table_child_count=table_child_count,
            quality_report_schema_version=quality_schema_version,
            quality_report_sha256=quality_report_sha256)

    if old_hashes:
        log.info(
            "Incremental: %d changed, %d unchanged (skipped), %d removed",
            plan.changed_count, plan.unchanged_count, plan.removed_count)

    if not plan.changed_items:
        if reconciled.receipt is None:
            raise RuntimeError(
                "Vector reconciliation produced no final verification")
        verified_ids = update_lifecycle.commit(
            reconciled.receipt,
            close_client_fn=lambda: _client_owner.close(client),
            save_manifest_fn=_save_manifest)
        operation_tracker.committed = True
        disposition = (
            "created" if not collection_existed_at_start else
            "rebuilt" if rebuild_collection else
            "updated" if plan.removed_count else
            "unchanged")
        return _operation_contracts.IndexOutcome(
            backend="chroma", disposition=disposition,
            total_records=source_record_count,
            changed_records=plan.changed_count,
            unchanged_records=plan.unchanged_count,
            removed_records=plan.removed_count,
            upserted_records=0, batch_count=0,
            physical_count=len(verified_ids), committed=True,
            operations=operation_tracker.contract())
    records = list(plan.changed_records)

    # --- Parallel embedding + pipelined upsert ---
    # For API-based embeddings (Voyage, OpenAI, Cohere), embed batches in
    # parallel threads. For local models, parallel embedding helps less but
    # we still pipeline embed with upsert.
    from concurrent.futures import ThreadPoolExecutor, as_completed
    is_api_model = any(embedding_model.startswith(p)
                       for p in ("voyage-", "text-embedding-", "embed-", "cohere-",
                                 "embo-", "minimax-emb"))
    # API models have per-batch token limits (Voyage: 120K tokens).
    # The configured default leaves ample room under API batch-token limits.
    # Local models can handle larger batches since there's no API limit.
    BATCH_SIZE = 25 if is_api_model else 100
    embed_workers = 4 if is_api_model else 1

    def _embed_batch(batch_data):
        """Embed a batch and return (ids, embeddings, documents, metadatas)."""
        ids, embedding_inputs, documents, metadatas = batch_data
        embeddings = _embed_texts(
            embedding_inputs, embedding_model,
            security_policy=security_policy)
        return ids, embeddings, documents, metadatas

    batches = _batch_index_records(
        records, embedding_model, max_records=BATCH_SIZE)
    prepared = [_prepare_chroma_batch(batch) for batch in batches]
    update_lifecycle.prepare_mutation()

    def _upsert_prepared(ids, embeddings, documents, metadatas) -> None:
        def _upsert() -> None:
            operation_tracker.upsert_calls += 1
            collection.upsert(
                ids=ids, embeddings=embeddings,
                documents=documents, metadatas=metadatas)

        update_lifecycle.mutate(_upsert)

    if embed_workers > 1:
        # Parallel embedding for API models (network I/O bound)
        log.info(f"Parallel embedding with {embed_workers} workers "
                 f"({len(batches)} batches)")
        pbar = tqdm(total=len(batches), desc="Indexing", unit="batch")
        pool = None
        parallel_error = None
        try:
            pool = ThreadPoolExecutor(max_workers=embed_workers)
            futures = {pool.submit(_embed_batch, p): i
                       for i, p in enumerate(prepared)}
            results_map = {}
            for future in as_completed(futures):
                idx = futures[future]
                results_map[idx] = future.result()
            # Upsert in order
            for i in range(len(batches)):
                ids, embeddings, documents, metadatas = results_map[i]
                _upsert_prepared(
                    ids, embeddings, documents, metadatas)
                pbar.update(1)
        except BaseException as exc:
            parallel_error = exc
            raise
        finally:
            _finish_executor_progress(
                pool, pbar, operation_name="Chroma parallel indexing",
                primary_error=parallel_error)
    else:
        # Sequential with pipelined upsert for local models
        upsert_q: queue.Queue = queue.Queue(maxsize=2)

        def _upsert_worker():
            while True:
                item = upsert_q.get()
                if item is None:
                    break
                ids, embs, docs, metas = item
                _upsert_prepared(ids, embs, docs, metas)
                pbar.update(1)
                upsert_q.task_done()

        pbar = tqdm(total=len(batches), desc="Indexing", unit="batch")
        upsert_pool = None
        upsert_future = None
        pipeline_error = None
        try:
            upsert_pool = ThreadPoolExecutor(max_workers=1)
            upsert_future = upsert_pool.submit(_upsert_worker)
            for p in prepared:
                ids, embeddings, documents, metadatas = _embed_batch(p)
                _put_unless_worker_failed(
                    upsert_q,
                    (ids, embeddings, documents, metadatas),
                    upsert_future,
                    metrics=operation_tracker.queue,
                )
        except BaseException as exc:
            pipeline_error = exc
            raise
        finally:
            _finish_queue_worker(
                upsert_q, upsert_future, upsert_pool, pbar,
                worker_name="Chroma upsert worker",
                primary_error=pipeline_error)

    verification = update_lifecycle.verify(
        plan.target_ids,
        lambda expected: _require_chroma_stable_ids(
            collection, collection_name, expected))
    verified_ids = update_lifecycle.commit(
        verification,
        close_client_fn=lambda: _client_owner.close(client),
        save_manifest_fn=_save_manifest)
    operation_tracker.committed = True

    log.info(
        "Collection '%s' → %d documents",
        collection_name,
        len(verified_ids),
    )
    log.info(f"Embedding model: {embedding_model}")
    log.info(f"Persisted to {chroma_dir}")
    disposition = (
        "created" if not collection_existed_at_start else
        "rebuilt" if rebuild_collection else
        "updated")
    return _operation_contracts.IndexOutcome(
        backend="chroma", disposition=disposition,
        total_records=source_record_count,
        changed_records=plan.changed_count,
        unchanged_records=plan.unchanged_count,
        removed_records=plan.removed_count,
        upserted_records=plan.changed_count, batch_count=len(batches),
        physical_count=len(verified_ids), committed=True,
        operations=operation_tracker.contract())


def index_chunks(chunks_path: Path, chroma_dir: Path, *,
                 collection_name: str = DEFAULT_COLLECTION,
                 embedding_model: str = DEFAULT_EMBEDDING_MODEL,
                 full_reindex: bool = False,
                 security_policy: (
                     _release_security.ReleaseSecurityPolicy | None
                 ) = None,
                 lock_timeout: float = DEFAULT_DB_LOCK_TIMEOUT,
                 _active_update_token: str | None = None,
                 _operation_observer: Callable[
                     [dict[str, int | float | bool]], None] | None = None,
                 ) -> _operation_contracts.IndexOutcome:
    """Index Chroma under a path-wide, process-safe exclusive lease."""
    operation_tracker = _IndexOperationalTracker()
    try:
        with _vector_store_lock(
                chroma_dir, backend="chroma",
                collection_name=collection_name,
                operation="Chroma indexing", timeout=lock_timeout):
            client_owner = _VectorClientOwner("Chroma")
            operation_error = None
            try:
                return _index_chunks_chroma_impl(
                    chunks_path, chroma_dir,
                    collection_name=collection_name,
                    embedding_model=embedding_model,
                    full_reindex=full_reindex,
                    security_policy=security_policy,
                    _active_update_token=_active_update_token,
                    _client_owner=client_owner,
                    _operation_tracker=operation_tracker,
                )
            except BaseException as exc:
                operation_error = exc
                raise
            finally:
                client_owner.finish(operation_error)
    except BaseException:
        _observe_failed_index_operation(
            _operation_observer, operation_tracker)
        raise


# ---------------------------------------------------------------------------
# Step 3b: Index into Qdrant
# ---------------------------------------------------------------------------

_embed_fn_cache: dict[
    tuple[str, str, _release_security.ReleaseSecurityPolicy], object
] = {}


_stable_token_hash = _retrieval_core._stable_token_hash


_LEGAL_SEARCH_ALIASES = _retrieval_core._LEGAL_SEARCH_ALIASES
_LEGAL_WORD_RE = _retrieval_core._LEGAL_WORD_RE
_LEGAL_SUBSECTION_RE = _retrieval_core._LEGAL_SUBSECTION_RE
_LEGAL_CITATION_RE = _retrieval_core._LEGAL_CITATION_RE
_LEXICAL_METADATA_FIELDS = _retrieval_core._LEXICAL_METADATA_FIELDS
_LEXICAL_METADATA_CHAR_LIMIT = _retrieval_core._LEXICAL_METADATA_CHAR_LIMIT


_normalize_legal_search_text = _retrieval_core._normalize_legal_search_text


_legal_search_tokens = _retrieval_core._legal_search_tokens


_lexical_document_text = _retrieval_core._lexical_document_text


_sparse_token_vector = _retrieval_core._sparse_token_vector


_chunk_hash = _retrieval_core._chunk_hash


_chunk_id = _retrieval_core._chunk_id


def _load_hash_index(db_dir: Path) -> dict[str, str]:
    """Load the legacy, database-wide chunk-hash sidecar.

    New indexing runs never rely on this file for incremental skipping because
    it does not identify the backend, collection, or embedding model. It is
    retained as a reader for safe migration detection and compatibility with
    older callers.
    """
    hash_file = db_dir / "chunk_hashes.json"
    if hash_file.exists():
        try:
            payload = json.loads(hash_file.read_text(encoding="utf-8"))
        except (OSError, json.JSONDecodeError):
            return {}
        if (isinstance(payload, dict)
                and all(isinstance(key, str) and isinstance(value, str)
                        for key, value in payload.items())):
            return payload
    return {}


def _save_hash_index(db_dir: Path, hashes: dict[str, str]) -> None:
    """Atomically store the legacy hash sidecar for external compatibility."""
    _atomic_write_json(db_dir / "chunk_hashes.json", hashes)


def _atomic_write_text(path: Path, content: str) -> None:
    """Publish text through the facade's current replace and cleanup hooks."""
    _artifact_io._atomic_write_text(
        path, content, replace_fn=os.replace,
        cleanup_error_fn=_log_cleanup_error)


_artifact_parameters_sha256 = _artifact_io._artifact_parameters_sha256


_artifact_completion_path = _artifact_io._artifact_completion_path


_hash_file_generation = _artifact_io.hash_file_generation


def _immutable_file_snapshot(path: Path, **kwargs):
    """Capture through the configured private scratch location."""
    if ("temporary_root" not in kwargs and "scratch_root" not in kwargs
            and (configured := os.environ.get(_SNAPSHOT_SCRATCH_ENV))):
        kwargs["scratch_root"] = Path(configured)
    kwargs.setdefault("cleanup_error_fn", _log_cleanup_error)
    return _artifact_io.immutable_file_snapshot(path, **kwargs)


def _write_artifact_completion(
        manifest_path: Path, *, stage: str, source_sha256: str,
        source_record_count: int | None, parameters: dict,
        outputs: dict[str, Path], source_name: str | None = None,
        schema_version: int = ARTIFACT_COMPLETION_SCHEMA_VERSION,
        extra_fields: dict[str, object] | None = None) -> None:
    """Commit completion through the facade's current hash/write hooks."""
    _artifact_io._write_artifact_completion(
        manifest_path, stage=stage, source_sha256=source_sha256,
        source_record_count=source_record_count, parameters=parameters,
        outputs=outputs, schema_version=schema_version,
        artifact_sha256_fn=_cached_artifact_sha256,
        atomic_write_json_fn=_atomic_write_json,
        source_name=source_name, extra_fields=extra_fields)


def _fixed_artifacts_complete(
        manifest_path: Path, *, stage: str, source_sha256: str,
        source_record_count: int | None, parameters: dict,
        outputs: dict[str, Path], source_name: str | None = None,
        schema_version: int = ARTIFACT_COMPLETION_SCHEMA_VERSION) -> bool:
    """Validate completion through the facade's current hashing policy."""
    return _artifact_io._fixed_artifacts_complete(
        manifest_path, stage=stage, source_sha256=source_sha256,
        source_record_count=source_record_count, parameters=parameters,
        outputs=outputs, schema_version=schema_version,
        artifact_sha256_fn=_cached_artifact_sha256,
        source_name=source_name)


def _atomic_write_json(path: Path, payload: object) -> None:
    """Publish JSON through the facade's current replace and cleanup hooks."""
    _artifact_io._atomic_write_json(
        path, payload, replace_fn=os.replace,
        cleanup_error_fn=_log_cleanup_error)


def _atomic_write_jsonl(path: Path, records: list[dict]) -> None:
    """Publish JSONL through the facade's current replace and cleanup hooks."""
    _artifact_io._atomic_write_jsonl(
        path, records, replace_fn=os.replace,
        cleanup_error_fn=_log_cleanup_error)


_index_manifest_path = _index_state._index_manifest_path


def _index_update_marker_path(db_dir: Path, *, backend: str,
                              collection_name: str) -> Path:
    """Return the collection-scoped marker for an unfinished index update."""
    return _index_state._index_update_marker_path(
        db_dir, backend=backend, collection_name=collection_name,
        manifest_path_fn=_index_manifest_path)


def _qdrant_update_marker_path(qdrant_dir: Path, *,
                                collection_name: str) -> Path:
    """Return the collection-scoped marker for an unfinished Qdrant update."""
    return _index_state._qdrant_update_marker_path(
        qdrant_dir, collection_name=collection_name,
        marker_path_fn=_index_update_marker_path)


def _chroma_update_marker_path(chroma_dir: Path, *,
                                collection_name: str) -> Path:
    """Return the collection-scoped marker for an unfinished Chroma update."""
    return _index_state._chroma_update_marker_path(
        chroma_dir, collection_name=collection_name,
        marker_path_fn=_index_update_marker_path)


def _begin_index_update(
        db_dir: Path, *, backend: str, collection_name: str,
        source_sha256: str, source_record_count: int,
        owner_token: str | None = None,
        replace_existing: bool = False) -> Path:
    """Durably mark one backend collection dirty before physical mutation."""
    return _index_state._begin_index_update(
        db_dir, backend=backend, collection_name=collection_name,
        source_sha256=source_sha256,
        source_record_count=source_record_count, owner_token=owner_token,
        replace_existing=replace_existing,
        manifest_schema_version=INDEX_MANIFEST_SCHEMA_VERSION,
        marker_path_fn=_index_update_marker_path,
        atomic_write_json_fn=_atomic_write_json)


def _index_update_marker_owned_by(path: Path,
                                  owner_token: str | None, *,
                                  backend: str | None = None,
                                  collection_name: str | None = None) -> bool:
    """Return whether a marker carries this run's per-update ownership token."""
    return _index_state._index_update_marker_owned_by(
        path, owner_token, backend=backend,
        collection_name=collection_name,
        manifest_schema_version=INDEX_MANIFEST_SCHEMA_VERSION)


def _begin_qdrant_index_update(
        qdrant_dir: Path, *, collection_name: str,
        source_sha256: str, source_record_count: int, owner_token: str,
        replace_existing: bool = False) -> Path:
    """Durably mark Qdrant dirty before its first physical mutation."""
    return _index_state._begin_qdrant_index_update(
        qdrant_dir, collection_name=collection_name,
        source_sha256=source_sha256,
        source_record_count=source_record_count, owner_token=owner_token,
        replace_existing=replace_existing,
        begin_index_update_fn=_begin_index_update)


def _begin_chroma_index_update(
        chroma_dir: Path, *, collection_name: str,
        source_sha256: str, source_record_count: int, owner_token: str,
        replace_existing: bool = False) -> Path:
    """Durably mark Chroma dirty before its first physical mutation."""
    return _index_state._begin_chroma_index_update(
        chroma_dir, collection_name=collection_name,
        source_sha256=source_sha256,
        source_record_count=source_record_count, owner_token=owner_token,
        replace_existing=replace_existing,
        begin_index_update_fn=_begin_index_update)


def _finish_index_update(marker_path: Path, *, owner_token: str,
                         backend: str | None = None,
                         collection_name: str | None = None) -> None:
    """Mark an update clean after its verified manifest has been committed."""
    _index_state._finish_index_update(
        marker_path, owner_token=owner_token, backend=backend,
        collection_name=collection_name,
        marker_owned_by_fn=_index_update_marker_owned_by)


def _load_index_manifest(db_dir: Path, *, backend: str,
                         collection_name: str) -> dict | None:
    """Load a collection-scoped manifest, returning ``None`` if unusable."""
    return _index_state._load_index_manifest(
        db_dir, backend=backend, collection_name=collection_name,
        manifest_path_fn=_index_manifest_path,
        warning_fn=log.warning)


def _index_manifest_mismatch(
        manifest: dict, *, backend: str, collection_name: str,
        embedding_model: str, embedding_dimension: int) -> str | None:
    """Return why *manifest* is incompatible, or ``None`` when safe to use."""
    return _index_state._index_manifest_mismatch(
        manifest, backend=backend, collection_name=collection_name,
        embedding_model=embedding_model,
        embedding_dimension=embedding_dimension,
        model_artifact_lock_sha256=_model_artifact_lock_sha256(),
        manifest_schema_version=INDEX_MANIFEST_SCHEMA_VERSION,
        quality_report_schema_version=(
            _quality_core.QUALITY_REPORT_SCHEMA_VERSION))


def _resolve_incremental_index_state(
        db_dir: Path, *, backend: str, collection_name: str,
        embedding_model: str, embedding_dimension: int,
        collection_exists: bool, full_reindex: bool,
        active_update_token: str | None = None,
) -> tuple[dict[str, str], bool, str]:
    """Resolve hashes and whether the named collection must be rebuilt.

    A legacy database-wide sidecar is deliberately never trusted for a skip:
    it cannot prove which collection or embedding model produced the vectors.
    Rebuilding only the requested collection safely migrates it to the new
    manifest without touching sibling collections or deleting the legacy file.
    """
    return _index_state._resolve_incremental_index_state(
        db_dir, backend=backend, collection_name=collection_name,
        embedding_model=embedding_model,
        embedding_dimension=embedding_dimension,
        collection_exists=collection_exists, full_reindex=full_reindex,
        active_update_token=active_update_token,
        marker_path_fn=_index_update_marker_path,
        marker_owned_by_fn=_index_update_marker_owned_by,
        load_manifest_fn=_load_index_manifest,
        manifest_mismatch_fn=_index_manifest_mismatch)


def _save_index_manifest(
        db_dir: Path, *, backend: str, collection_name: str,
        embedding_model: str, embedding_dimension: int,
        chunk_hashes: dict[str, str], source_sha256: str | None = None,
        source_record_count: int | None = None,
        quality_report_schema_version: int | None = None,
        quality_report_sha256: str | None = None,
        table_child_count: int = 0) -> Path:
    """Atomically persist versioned incremental state for one collection."""
    return _index_state._save_index_manifest(
        db_dir, backend=backend, collection_name=collection_name,
        embedding_model=embedding_model,
        embedding_dimension=embedding_dimension,
        model_artifact_lock_sha256=_model_artifact_lock_sha256(),
        chunk_hashes=chunk_hashes, source_sha256=source_sha256,
        source_record_count=source_record_count,
        quality_report_schema_version=quality_report_schema_version,
        quality_report_sha256=quality_report_sha256,
        table_child_count=table_child_count,
        manifest_schema_version=INDEX_MANIFEST_SCHEMA_VERSION,
        quality_report_policy_schema_version=(
            _quality_core.QUALITY_REPORT_SCHEMA_VERSION),
        manifest_path_fn=_index_manifest_path,
        atomic_write_json_fn=_atomic_write_json)


def _query_manifest_dimension_impl(
        db_dir: Path, *, backend: str, collection_name: str,
        embedding_model: str,
        allow_legacy: bool = True) -> int | None:
    """Validate query/index compatibility and return the indexed dimension.

    Legacy collections without a manifest remain queryable. Once a manifest
    exists, however, querying with a different model or stale schema is refused
    rather than silently comparing vectors from incompatible embedding spaces.
    """
    return _index_state._query_manifest_dimension_impl(
        db_dir, backend=backend, collection_name=collection_name,
        embedding_model=embedding_model,
        model_artifact_lock_sha256=_model_artifact_lock_sha256(),
        manifest_schema_version=INDEX_MANIFEST_SCHEMA_VERSION,
        quality_report_schema_version=(
            _quality_core.QUALITY_REPORT_SCHEMA_VERSION),
        marker_path_fn=_index_update_marker_path,
        manifest_path_fn=_index_manifest_path,
        load_manifest_fn=_load_index_manifest,
        compatible_schema_bindings=(
            _LEGACY_QUERY_SCHEMA_BINDINGS
            if allow_legacy else _CONTEXT_QUERY_SCHEMA_BINDINGS))


def _query_manifest_dimension(
        db_dir: Path, *, backend: str, collection_name: str,
        embedding_model: str,
        allow_legacy: bool = True,
        lock_timeout: float = DEFAULT_DB_LOCK_TIMEOUT) -> int | None:
    """Validate query/index compatibility under the database lease."""
    with _vector_store_lock(
            db_dir, backend=backend, collection_name=collection_name,
            operation="manifest inspection", timeout=lock_timeout):
        return _query_manifest_dimension_impl(
            db_dir, backend=backend, collection_name=collection_name,
            embedding_model=embedding_model, allow_legacy=allow_legacy)


def _indexed_table_child_count(
        db_dir: Path, *, backend: str, collection_name: str,
) -> int:
    """Return the manifested row-child count for a current index generation."""
    manifest = _load_index_manifest(
        db_dir, backend=backend, collection_name=collection_name)
    if (manifest is None
            or manifest.get("schema_version") != INDEX_MANIFEST_SCHEMA_VERSION):
        return 0
    count = manifest.get("table_child_count")
    if isinstance(count, bool) or not isinstance(count, int) or count < 0:
        raise ValueError("current index manifest has an invalid table child count")
    return count


_artifact_sha256_cache: dict[
    str, tuple[tuple[int, int, int, int, int], str]
] = {}
_artifact_sha256_cache_lock = _threading.Lock()
_ARTIFACT_SHA256_CACHE_MAX = 16
# Windows ``st_ctime`` is creation time rather than an inode change counter.
# Same-size bytes can therefore alias a restored mtime/ctime fingerprint, so a
# stat-only cache hit cannot prove content identity there.
_ARTIFACT_STAT_HASH_CACHE_SAFE = os.name != "nt"


def _cached_artifact_sha256(path: Path) -> str:
    """Hash one stable artifact snapshot, caching by strong stat identity."""
    path = Path(path)
    cache_key = os.path.normcase(str(path.resolve(strict=True)))
    if _ARTIFACT_STAT_HASH_CACHE_SAFE:
        with path.open("rb") as handle:
            before = _artifact_stat_fingerprint(os.fstat(handle.fileno()))
            with _artifact_sha256_cache_lock:
                cached = _artifact_sha256_cache.get(cache_key)
            if cached is not None and cached[0] == before:
                after = _artifact_stat_fingerprint(os.fstat(handle.fileno()))
                if after == before:
                    return cached[1]

    generation = _hash_file_generation(path)
    value = generation.sha256
    fingerprint = generation.fingerprint
    if _ARTIFACT_STAT_HASH_CACHE_SAFE:
        with _artifact_sha256_cache_lock:
            _artifact_sha256_cache[cache_key] = (fingerprint, value)
            while len(_artifact_sha256_cache) > _ARTIFACT_SHA256_CACHE_MAX:
                oldest = next(iter(_artifact_sha256_cache))
                del _artifact_sha256_cache[oldest]
    return value


def _require_hybrid_chunks_snapshot(
        chunks_path: Path, db_dir: Path, *, backend: str,
        collection_name: str) -> str | None:
    """Validate and return the manifested lexical corpus digest, if proven."""
    return _index_state._require_hybrid_chunks_snapshot(
        chunks_path, db_dir, backend=backend,
        collection_name=collection_name,
        load_manifest_fn=_load_index_manifest,
        artifact_sha256_fn=_cached_artifact_sha256,
        quality_report_path_fn=_quality_core.quality_report_path)


_validate_query_vector_dimension = (
    _index_state._validate_query_vector_dimension)


def _embedding_dimension(
        model_name: str, *,
        security_policy: (
            _release_security.ReleaseSecurityPolicy | None
        ) = None) -> int:
    """Probe and validate the configured document embedding dimension."""
    embeddings = _embed_texts(
        ["RAG index embedding-dimension probe"], model_name,
        input_type="document", security_policy=security_policy)
    if len(embeddings) != 1 or len(embeddings[0]) < 1:
        raise ValueError(
            f"Embedding model '{model_name}' returned no usable vector")
    return len(embeddings[0])


def _qdrant_exact_count(client, collection_name: str) -> int:
    """Return Qdrant's exact physical point count or fail closed."""
    result = client.count(collection_name=collection_name, exact=True)
    count = getattr(result, "count", None)
    if (isinstance(count, bool) or not isinstance(count, int)
            or count < 0):
        raise RuntimeError(
            f"Qdrant collection '{collection_name}' returned an invalid "
            "exact point count")
    return count


def _require_qdrant_update_completed(result: object, operation: str) -> None:
    """Require a waited Qdrant mutation to report completed status."""
    status = getattr(result, "status", None)
    status_value = getattr(status, "value", status)
    if type(status_value) is not str or status_value != "completed":
        raise RuntimeError(
            f"Qdrant {operation} did not report completed status")


def _qdrant_id_key(value: object) -> tuple[object, ...]:
    """Return a hashable key without collapsing distinct ID representations."""
    if type(value) is int:
        return "int", value
    if type(value) is str:
        return "str", value
    if isinstance(value, UUID):
        return "uuid.UUID", value.bytes

    descriptor = getattr(value, "DESCRIPTOR", None)
    if getattr(descriptor, "full_name", None) == "qdrant.PointId":
        try:
            variant = value.WhichOneof("point_id_options")
        except (TypeError, ValueError) as exc:
            raise RuntimeError(
                "Qdrant returned an invalid protobuf point ID") from exc
        if variant == "num":
            return "protobuf", "qdrant.PointId", "num", int(value.num)
        if variant == "uuid":
            return "protobuf", "qdrant.PointId", "uuid", str(value.uuid)
        raise RuntimeError(
            "Qdrant returned an unset or unsupported protobuf point ID")

    raise RuntimeError(
        f"Qdrant returned an unsupported point ID type: "
        f"{type(value).__name__}")


def _qdrant_stable_id_rows(
        client, collection_name: str, *,
        page_size: int = 1000) -> list[tuple[object, str | None]]:
    """Return every stable ID through a count-bounded, drift-safe scroll."""
    if (isinstance(page_size, bool) or not isinstance(page_size, int)
            or page_size < 1):
        raise ValueError("Qdrant scroll page_size must be a positive integer")

    count_before = _qdrant_exact_count(client, collection_name)
    rows = []
    offset = None
    seen_offsets = set()
    seen_point_ids = set()
    max_scroll_calls = max(1, count_before + 1)
    for _ in range(max_scroll_calls):
        points, next_offset = client.scroll(
            collection_name,
            limit=page_size,
            offset=offset,
            with_payload=["stable_id"],
            with_vectors=False,
        )
        if len(points) > page_size:
            raise RuntimeError(
                "Qdrant scroll returned more points than its page limit")
        if not points and next_offset is not None:
            raise RuntimeError(
                "Qdrant scroll returned an empty nonterminal page")
        for point in points:
            if len(rows) >= count_before:
                raise RuntimeError(
                    "Qdrant scroll exceeded its exact point count")
            point_key = _qdrant_id_key(point.id)
            if point_key in seen_point_ids:
                raise RuntimeError(
                    "Qdrant scroll repeated a physical point ID")
            seen_point_ids.add(point_key)
            payload = point.payload if isinstance(point.payload, dict) else {}
            stable_id = payload.get("stable_id")
            if not isinstance(stable_id, str) or not stable_id.strip():
                stable_id = None
            rows.append((point.id, stable_id))
        if next_offset is None:
            count_after = _qdrant_exact_count(client, collection_name)
            if len(rows) != count_before or count_after != count_before:
                raise RuntimeError(
                    f"Qdrant collection '{collection_name}' changed or "
                    "returned an incomplete exact-count scroll")
            return rows
        offset_key = _qdrant_id_key(next_offset)
        if offset_key in seen_offsets:
            raise RuntimeError(
                "Qdrant scroll repeated a continuation offset")
        seen_offsets.add(offset_key)
        offset = next_offset
    raise RuntimeError(
        "Qdrant scroll exceeded its exact-count page budget")


def _qdrant_point_ids_for_stable_ids(client, collection_name: str,
                                      stable_ids: set[str], *,
                                      page_size: int = 1000) -> list:
    """Find every Qdrant point matching the exact requested stable-ID set."""
    requested = set(stable_ids)
    if not requested:
        return []
    rows = _qdrant_stable_id_rows(
        client, collection_name, page_size=page_size)
    found = {stable_id for _, stable_id in rows if stable_id in requested}
    missing = requested - found
    if missing:
        raise RuntimeError(
            f"{len(missing)} requested Qdrant stable ID(s) not found; "
            "refusing an incomplete deletion")
    return [point_id for point_id, stable_id in rows
            if stable_id in requested]


def _require_qdrant_stable_ids(
        client, collection_name: str, expected: set[str], *,
        page_size: int = 1000) -> dict[str, list]:
    """Require one physical point for every expected stable ID and no others."""
    rows = _qdrant_stable_id_rows(
        client, collection_name, page_size=page_size)
    point_ids: dict[str, list] = {}
    untracked = 0
    for point_id, stable_id in rows:
        if stable_id is None:
            untracked += 1
            continue
        point_ids.setdefault(stable_id, []).append(point_id)

    actual = set(point_ids)
    missing = expected - actual
    unexpected = actual - expected
    duplicates = sum(
        len(ids) - 1 for ids in point_ids.values() if len(ids) > 1)
    if missing or unexpected or duplicates or untracked:
        details = []
        if missing:
            details.append(f"{len(missing)} stable ID(s) not found")
        if unexpected:
            details.append(f"{len(unexpected)} unexpected stable ID(s)")
        if duplicates:
            details.append(f"{duplicates} duplicate stable-ID point(s)")
        if untracked:
            details.append(f"{untracked} point(s) without a stable ID")
        raise RuntimeError(
            f"Qdrant collection '{collection_name}' does not match its "
            f"stable-ID manifest ({'; '.join(details)}). Refusing to update "
            "the manifest; run again with --full-reindex.")
    return point_ids


def _qdrant_point_id(stable_id: str) -> int:
    """Map a canonical hexadecimal stable ID to a portable 63-bit point ID."""
    return int(stable_id.removeprefix("chunk_"), 16) % (2**63)


def _qdrant_payload(record: dict, stable_id: str) -> dict:
    """Build payload with canonical fields protected from metadata collisions."""
    return {
        **record["metadata"],
        "text": record["text"],
        "stable_id": stable_id,
    }


def _embed_texts(
        texts: list[str], model_name: str, *,
        input_type: str = "document",
        security_policy: (
            _release_security.ReleaseSecurityPolicy | None
        ) = None) -> list[list[float]]:
    """Embed texts with a cached document- or query-role embedder."""
    policy = _effective_security_policy(security_policy)
    if model_name.startswith(_API_EMBEDDING_MODEL_PREFIXES):
        # Re-authorize before cache lookup.  The process can execute operations
        # with different policies, and ambient network configuration can change
        # after a provider closure was constructed.
        _release_security.require_cloud_egress(
            policy, feature="cloud embedding")
    cache_key = (model_name, input_type, policy)
    if cache_key not in _embed_fn_cache:
        _embed_fn_cache[cache_key] = _get_embedding_fn(
            model_name, input_type=input_type, security_policy=policy)
    return _embed_fn_cache[cache_key](texts)


def _index_chunks_qdrant_impl(
        chunks_path: Path, qdrant_dir: Path, *,
        collection_name: str = DEFAULT_COLLECTION,
        embedding_model: str = DEFAULT_EMBEDDING_MODEL,
        full_reindex: bool = False,
        security_policy: (
            _release_security.ReleaseSecurityPolicy | None
        ) = None,
        _active_update_token: str | None = None,
        _client_owner: _VectorClientOwner,
        _operation_tracker: _IndexOperationalTracker,
        ) -> _operation_contracts.IndexOutcome:
    """Load enriched chunks and index into a local Qdrant collection.

    Uses Qdrant's local mode (on-disk, no server needed) with:
    - Dense vectors from the configured embedding model
    - Sparse vectors for built-in hybrid search (no separate BM25 step)
    - Rich payload filtering on all metadata fields
    """
    from qdrant_client import QdrantClient, models
    from tqdm import tqdm

    _require_file(chunks_path, "Chunks JSONL")

    log.info(f"Loading chunks from {chunks_path}")
    (records, source_sha256, _,
     quality_binding) = _load_index_snapshot_with_quality(
         chunks_path, allow_legacy_quality=False)
    quality_schema_version, quality_report_sha256 = quality_binding
    source_record_count = len(records)
    table_child_count = _table_retrieval_core.table_child_count(records)
    _validate_embedding_token_counts(
        records, embedding_model, recompute=True)
    chunk_info = [(record, _chunk_id(record), _chunk_hash(record))
                  for record in records]
    new_hashes = {chunk_id: chunk_hash
                  for _, chunk_id, chunk_hash in chunk_info}
    qdrant_point_ids = {
        chunk_id: _qdrant_point_id(chunk_id) for chunk_id in new_hashes}
    if len(set(qdrant_point_ids.values())) != len(qdrant_point_ids):
        raise ValueError(
            "Chunks file contains colliding numeric Qdrant point IDs")
    log.info(f"Loaded {len(records)} chunks")

    qdrant_dir = _storage_policy.ensure_private_tree(qdrant_dir)
    client = _client_owner.own(
        QdrantClient(path=str(qdrant_dir)))

    dim = _embedding_dimension(
        embedding_model, security_policy=security_policy)
    collection_exists = client.collection_exists(collection_name)
    old_hashes, rebuild_collection, rebuild_reason = (
        _resolve_incremental_index_state(
            qdrant_dir, backend="qdrant", collection_name=collection_name,
            embedding_model=embedding_model,
            embedding_dimension=dim,
            collection_exists=collection_exists,
            full_reindex=full_reindex,
            active_update_token=_active_update_token,
        )
    )
    collection_existed_at_start = collection_exists
    plan = _vector_lifecycle.plan_reconciliation(chunk_info, old_hashes)
    new_hashes = plan.new_hashes
    update_lifecycle = _new_vector_update_lifecycle(
        qdrant_dir, backend="qdrant", collection_name=collection_name,
        source_sha256=source_sha256,
        source_record_count=source_record_count,
        target_ids=plan.target_ids,
        active_update_token=_active_update_token)
    operation_tracker = _operation_tracker

    if rebuild_collection:
        log.info("Rebuilding Qdrant collection '%s': %s",
                 collection_name, rebuild_reason)

    def _delete_collection() -> None:
        operation_tracker.collection_delete_calls += 1
        client.delete_collection(collection_name)

    def _create_collection():
        operation_tracker.collection_create_calls += 1
        client.create_collection(
            collection_name=collection_name,
            vectors_config=models.VectorParams(
                size=dim,
                distance=models.Distance.COSINE,
            ),
            sparse_vectors_config={
                "bm25": models.SparseVectorParams(
                    modifier=models.Modifier.IDF,
                ),
            },
        )
        return client

    def _verify_stable_ids(handle, expected):
        return _require_qdrant_stable_ids(
            handle, collection_name, expected)

    def _delete_stable_ids(handle, stable_ids, verified_point_ids) -> None:
        # Delete removals and stale versions of changed durable IDs first. A
        # post-delete identity check makes a no-op delete fail closed; the
        # final check likewise catches a no-op replacement upsert.
        points_to_delete = [
            point_id
            for stable_id in stable_ids
            for point_id in verified_point_ids[stable_id]
        ]
        if points_to_delete:
            operation_tracker.record_delete_calls += 1
            delete_result = handle.delete(
                collection_name,
                points_selector=models.PointIdsList(
                    points=points_to_delete),
                wait=True)
            _require_qdrant_update_completed(
                delete_result, "point deletion")

    reconciled = _vector_lifecycle.reconcile_collection(
        lifecycle=update_lifecycle,
        plan=plan,
        handle=client,
        collection_exists=collection_exists,
        rebuild_collection=rebuild_collection,
        delete_collection_fn=_delete_collection,
        create_collection_fn=_create_collection,
        verify_stable_ids_fn=_verify_stable_ids,
        delete_stable_ids_fn=_delete_stable_ids,
    )

    def _save_manifest():
        return _save_index_manifest(
            qdrant_dir, backend="qdrant",
            collection_name=collection_name,
            embedding_model=embedding_model,
            embedding_dimension=dim,
            chunk_hashes=new_hashes, source_sha256=source_sha256,
            source_record_count=source_record_count,
            table_child_count=table_child_count,
            quality_report_schema_version=quality_schema_version,
            quality_report_sha256=quality_report_sha256)

    if old_hashes:
        log.info(
            "Incremental: %d changed, %d unchanged (skipped), %d removed",
            plan.changed_count, plan.unchanged_count, plan.removed_count)

    if not plan.changed_items:
        if reconciled.receipt is None:
            raise RuntimeError(
                "Vector reconciliation produced no final verification")
        verified_point_ids = update_lifecycle.commit(
            reconciled.receipt,
            close_client_fn=lambda: _client_owner.close(client),
            save_manifest_fn=_save_manifest)
        operation_tracker.committed = True
        verified_count = sum(
            len(ids) for ids in verified_point_ids.values())
        disposition = (
            "created" if not collection_existed_at_start else
            "rebuilt" if rebuild_collection else
            "updated" if plan.removed_count else
            "unchanged")
        return _operation_contracts.IndexOutcome(
            backend="qdrant", disposition=disposition,
            total_records=source_record_count,
            changed_records=plan.changed_count,
            unchanged_records=plan.unchanged_count,
            removed_records=plan.removed_count,
            upserted_records=0, batch_count=0,
            physical_count=verified_count, committed=True,
            operations=operation_tracker.contract())
    records = list(plan.changed_records)

    # Pipeline: embed batch N on GPU while upserting batch N-1 to disk
    update_lifecycle.prepare_mutation()
    from concurrent.futures import ThreadPoolExecutor
    BATCH_SIZE = 64
    batches = _batch_index_records(
        records, embedding_model, max_records=BATCH_SIZE)
    pbar = tqdm(total=len(batches), desc="Indexing (Qdrant)", unit="batch")
    upsert_queue: queue.Queue = queue.Queue(maxsize=2)

    def _upsert_worker():
        """Background thread: drain upsert queue."""
        while True:
            item = upsert_queue.get()
            if item is None:
                break

            def _upsert_points() -> None:
                operation_tracker.upsert_calls += 1
                upsert_result = client.upsert(
                    collection_name=collection_name,
                    points=item,
                    wait=True)
                _require_qdrant_update_completed(
                    upsert_result, "point upsert")

            update_lifecycle.mutate(_upsert_points)
            pbar.update(1)
            upsert_queue.task_done()

    upsert_thread = None
    upsert_future = None
    pipeline_error = None
    try:
        upsert_thread = ThreadPoolExecutor(max_workers=1)
        upsert_future = upsert_thread.submit(_upsert_worker)
        for batch in batches:
            # Prepare texts
            texts = []
            for r in batch:
                texts.append(_embedding_text(r))

            # GPU: embed this batch (while previous batch upserts in background)
            dense_vectors = _embed_texts(
                texts, embedding_model, security_policy=security_policy)

            # Build points
            points = []
            for i, r in enumerate(batch):
                stable_id = _chunk_id(r)
                idx = qdrant_point_ids[stable_id]
                payload = _qdrant_payload(r, stable_id)
                lexical_text = _lexical_document_text(
                    r["text"], r.get("metadata", {}))
                sp_indices, sp_values = _sparse_token_vector(lexical_text)
                points.append(models.PointStruct(
                    id=idx,
                    vector={
                        "": dense_vectors[i],
                        "bm25": models.SparseVector(
                            indices=sp_indices, values=sp_values,
                        ),
                    },
                    payload=payload,
                ))

            # Queue for background upsert (blocks if queue full — backpressure)
            _put_unless_worker_failed(
                upsert_queue, points, upsert_future,
                metrics=operation_tracker.queue)
    except BaseException as exc:
        pipeline_error = exc
        raise
    finally:
        _finish_queue_worker(
            upsert_queue, upsert_future, upsert_thread, pbar,
            worker_name="Qdrant upsert worker",
            primary_error=pipeline_error)

    verification = update_lifecycle.verify(
        plan.target_ids,
        lambda expected: _require_qdrant_stable_ids(
            client, collection_name, expected))
    verified_point_ids = update_lifecycle.commit(
        verification,
        close_client_fn=lambda: _client_owner.close(client),
        save_manifest_fn=_save_manifest)
    operation_tracker.committed = True

    verified_count = sum(len(ids) for ids in verified_point_ids.values())
    log.info(
        f"Qdrant collection '{collection_name}' -> {verified_count} points")
    log.info(f"Embedding: {embedding_model} (dim={dim})")
    log.info("Sparse vectors: BM25 (built-in hybrid search)")
    log.info(f"Persisted to {qdrant_dir}")
    disposition = (
        "created" if not collection_existed_at_start else
        "rebuilt" if rebuild_collection else
        "updated")
    return _operation_contracts.IndexOutcome(
        backend="qdrant", disposition=disposition,
        total_records=source_record_count,
        changed_records=plan.changed_count,
        unchanged_records=plan.unchanged_count,
        removed_records=plan.removed_count,
        upserted_records=plan.changed_count, batch_count=len(batches),
        physical_count=verified_count, committed=True,
        operations=operation_tracker.contract())


def index_chunks_qdrant(chunks_path: Path, qdrant_dir: Path, *,
                        collection_name: str = DEFAULT_COLLECTION,
                        embedding_model: str = DEFAULT_EMBEDDING_MODEL,
                        full_reindex: bool = False,
                        security_policy: (
                            _release_security.ReleaseSecurityPolicy | None
                        ) = None,
                        lock_timeout: float = DEFAULT_DB_LOCK_TIMEOUT,
                        _active_update_token: str | None = None,
                        _operation_observer: Callable[
                            [dict[str, int | float | bool]], None]
                        | None = None,
                        ) -> _operation_contracts.IndexOutcome:
    """Index Qdrant under a path-wide, process-safe exclusive lease."""
    operation_tracker = _IndexOperationalTracker()
    try:
        with _vector_store_lock(
                qdrant_dir, backend="qdrant",
                collection_name=collection_name,
                operation="Qdrant indexing", timeout=lock_timeout):
            client_owner = _VectorClientOwner("Qdrant")
            operation_error = None
            try:
                return _index_chunks_qdrant_impl(
                    chunks_path, qdrant_dir,
                    collection_name=collection_name,
                    embedding_model=embedding_model,
                    full_reindex=full_reindex,
                    security_policy=security_policy,
                    _active_update_token=_active_update_token,
                    _client_owner=client_owner,
                    _operation_tracker=operation_tracker,
                )
            except BaseException as exc:
                operation_error = exc
                raise
            finally:
                client_owner.finish(operation_error)
    except BaseException:
        _observe_failed_index_operation(
            _operation_observer, operation_tracker)
        raise


def query_index_qdrant(query: str, qdrant_dir: Path, *,
                       n_results: int = 5,
                       content_type: Optional[str] = None,
                       chapter_num: Optional[int] = None,
                       collection_name: str = DEFAULT_COLLECTION,
                       embedding_model: str = DEFAULT_EMBEDDING_MODEL,
                       output_json: bool = False,
                       use_reranker: bool | None = None,
                       hybrid: bool | None = None,
                       chunks_path: Path = DEFAULT_CHUNKS_PATH,
                       reranker_model: str = DEFAULT_RERANKER_MODEL,
                       overfetch: int = RERANK_OVERFETCH,
                       rrf_k: int = DEFAULT_RRF_K,
                       dense_weight: float = DEFAULT_DENSE_RRF_WEIGHT,
                       sparse_weight: float = DEFAULT_SPARSE_RRF_WEIGHT,
                       context_window: int = 0,
                       context_max_characters: int = (
                           DEFAULT_CONTEXT_MAX_CHARACTERS),
                       context_segment_characters: int = (
                           DEFAULT_CONTEXT_SEGMENT_CHARACTERS),
                       answer: bool = False,
                       cloud_url: str = DEFAULT_CLOUD_URL,
                       cloud_model: str = DEFAULT_CLOUD_MODEL,
                       cloud_key: str = "",
                       ollama_url: str = DEFAULT_OLLAMA_URL,
                       ollama_model: str = DEFAULT_OLLAMA_MODEL,
                       gemini_key: str = "",
                       llm_workers: int = DEFAULT_LLM_WORKERS,
                       thinking: bool = False,
                       lock_timeout: float = DEFAULT_DB_LOCK_TIMEOUT,
                       security_policy: (
                           _release_security.ReleaseSecurityPolicy | None
                       ) = None) -> None:
    """Query Qdrant and preserve the legacy CLI/JSON output contract."""
    try:
        response = search_index(
            query, qdrant_dir, db_backend="qdrant", n_results=n_results,
            content_type=content_type, chapter_num=chapter_num,
            collection_name=collection_name, embedding_model=embedding_model,
            use_reranker=use_reranker, hybrid=hybrid,
            chunks_path=chunks_path,
            reranker_model=reranker_model, overfetch=overfetch, rrf_k=rrf_k,
            dense_weight=dense_weight, sparse_weight=sparse_weight,
            context_window=context_window,
            context_max_characters=context_max_characters,
            context_segment_characters=context_segment_characters,
            lock_timeout=lock_timeout,
            security_policy=security_policy,
        )
    except (FileNotFoundError, LookupError) as exc:
        log.error(str(exc))
        log.error("  Run: python rag.py index --db-backend qdrant")
        raise SystemExit(1) from exc

    llm_answer = _answer_search_results(
        query, response,
        answer=answer, cloud_url=cloud_url, cloud_model=cloud_model,
        cloud_key=cloud_key, ollama_url=ollama_url,
        ollama_model=ollama_model, gemini_key=gemini_key,
        llm_workers=llm_workers, thinking=thinking,
        security_policy=security_policy,
    )
    _write_search_output(
        query, response, output_json=output_json, llm_answer=llm_answer,
        content_type=content_type, chapter_num=chapter_num,
    )


# ---------------------------------------------------------------------------
# Step 4: Query (ChromaDB — see query_index_qdrant above for Qdrant)
# ---------------------------------------------------------------------------

_bm25_cache: dict[str, tuple] = {}  # path -> records, BM25, fingerprint, terms
_bm25_cache_lock = _threading.Lock()
_BM25_CACHE_MAX = 5  # evict oldest when exceeded


def _bm25_search(query: str, chunks_path: Path, n_results: int,
                 content_type: Optional[str] = None,
                 chapter_num: Optional[int] = None, *,
                 expected_source_sha256: str | None = None) -> tuple:
    """BM25 keyword search over chunks JSONL.

    Caches the BM25 index per file (reloads only if file changed).
    Returns (documents, metadatas, scores) sorted by BM25 score descending.
    """
    from rank_bm25 import BM25Okapi

    cache_key = str(chunks_path.resolve())
    raw, source_sha256, artifact_fingerprint = (
        _read_index_artifact_snapshot(chunks_path))
    if (expected_source_sha256 is not None
            and source_sha256 != expected_source_sha256):
        raise ValueError(
            f"BM25 chunks snapshot changed from the indexed corpus: "
            f"{chunks_path}")
    fingerprint = (
        artifact_fingerprint, source_sha256, INDEX_MANIFEST_SCHEMA_VERSION)

    with _bm25_cache_lock:
        cached = _bm25_cache.get(cache_key)
    if cached is not None and cached[2] == fingerprint:
        all_records, bm25 = cached[0], cached[1]
        corpus_terms = cached[3]
    else:
        all_records = _parse_index_records_strict(raw, chunks_path)

        corpus = [
            _legal_search_tokens(_lexical_document_text(
                rec["text"], rec.get("metadata", {}))) or ["__rag_empty__"]
            for rec in all_records
        ]
        bm25 = BM25Okapi(corpus) if corpus else None
        corpus_terms = [set(tokens) for tokens in corpus]
        with _bm25_cache_lock:
            _bm25_cache[cache_key] = (
                all_records, bm25, fingerprint, corpus_terms)
            # Evict oldest entries if cache exceeds limit
            while len(_bm25_cache) > _BM25_CACHE_MAX:
                oldest = next(iter(_bm25_cache))
                del _bm25_cache[oldest]

    if not all_records or bm25 is None:
        return [], [], []

    # Apply metadata filters post-cache
    filtered_indices = []
    for i, rec in enumerate(all_records):
        if content_type and rec["metadata"].get("content_type") != content_type:
            continue
        if chapter_num is not None and rec["metadata"].get("chapter_num") != chapter_num:
            continue
        filtered_indices.append(i)

    if not filtered_indices:
        return [], [], []

    # Score all documents, then filter
    query_tokens = _legal_search_tokens(query)
    if not query_tokens:
        return [], [], []
    all_scores = bm25.get_scores(query_tokens)

    scored = [
        (all_scores[i], all_records[i]) for i in filtered_indices
        if (math.isfinite(float(all_scores[i]))
            and bool(corpus_terms[i].intersection(query_tokens)))
    ]
    scored.sort(key=lambda x: x[0], reverse=True)
    scored = scored[:n_results]
    return (
        [s[1]["text"] for s in scored],
        [
            {
                **s[1]["metadata"],
                "stable_id": _chunk_id(s[1]),
            }
            for s in scored
        ],
        [float(s[0]) for s in scored],
    )


_reciprocal_rank_fusion = _retrieval_core._reciprocal_rank_fusion


_build_chroma_where = _retrieval_core._build_chroma_where


def _record_search_warning(warnings: list[str], message: str) -> None:
    """Record a machine-readable warning and also surface it in logs."""
    warnings.append(message)
    log.warning(message)


def _unpack_chroma_results(results: dict) -> tuple[list[str], list[dict],
                                                    list[float]]:
    """Normalize Chroma's nested single-query result shape."""
    documents = results.get("documents") or [[]]
    metadatas = results.get("metadatas") or [[]]
    distances = results.get("distances") or [[]]
    docs = list(documents[0] or [])
    metas = [dict(meta or {}) for meta in (metadatas[0] or [])]
    # Older Chroma indexes stored ``context + text`` as the document.  New
    # indexes store raw text, but normalize legacy results during migration so
    # callers receive the same payload shape from both backends.
    for index, (doc, meta) in enumerate(zip(docs, metas)):
        context = meta.get("context", "")
        prefix = f"{context}\n\n" if context else ""
        if prefix and doc.startswith(prefix):
            docs[index] = doc[len(prefix):]
    scores = [1.0 - float(distance) for distance in (distances[0] or [])]
    return docs, metas, scores


def _search_chroma_candidates_impl(
        query: str, db_dir: Path, *, n_results: int,
        content_type: str | None, chapter_num: int | None,
        collection_name: str, embedding_model: str, hybrid: bool,
        chunks_path: Path, warnings: list[str],
        rrf_k: int = DEFAULT_RRF_K,
        dense_weight: float = DEFAULT_DENSE_RRF_WEIGHT,
        sparse_weight: float = DEFAULT_SPARSE_RRF_WEIGHT,
        expected_dimension: int | None = None,
        expected_source_sha256: str | None = None,
        security_policy: (
            _release_security.ReleaseSecurityPolicy | None
        ) = None,
        _client_owner: _VectorClientOwner,
) -> tuple[list[str], list[dict], list[float], str]:
    """Retrieve Chroma candidates, falling back cleanly from BM25."""
    import chromadb

    client = _client_owner.own(
        chromadb.PersistentClient(
            path=str(db_dir), **_chroma_settings_kwargs(chromadb)))
    try:
        collection = client.get_collection(collection_name)
    except Exception as exc:
        raise LookupError(
            f"Collection '{collection_name}' not found in {db_dir}") from exc

    where = _build_chroma_where(content_type, chapter_num)
    fetch_n = n_results
    query_vector = _embed_texts(
        [query], embedding_model, input_type="query",
        security_policy=security_policy)[0]
    _validate_query_vector_dimension(
        query_vector, expected_dimension, embedding_model)
    query_kwargs = {
        "query_embeddings": [query_vector],
        "n_results": fetch_n,
        "include": ["documents", "metadatas", "distances"],
    }
    if where is not None:
        query_kwargs["where"] = where

    def vector_search():
        return collection.query(**query_kwargs)

    if not hybrid:
        return (*_unpack_chroma_results(vector_search()), "vector")

    if not chunks_path.is_file():
        _record_search_warning(
            warnings,
            f"Hybrid search requested but chunks file '{chunks_path}' is "
            "unavailable; used vector search.",
        )
        return (*_unpack_chroma_results(vector_search()), "vector")

    from concurrent.futures import ThreadPoolExecutor

    with ThreadPoolExecutor(max_workers=2) as pool:
        vector_future = pool.submit(vector_search)
        bm25_future = pool.submit(
            _bm25_search, query, chunks_path, fetch_n,
            content_type=content_type, chapter_num=chapter_num,
            expected_source_sha256=expected_source_sha256,
        )
        vector_results = vector_future.result()
        try:
            bm25_docs, bm25_metas, bm25_scores = bm25_future.result()
        except Exception as exc:
            _record_search_warning(
                warnings,
                f"Hybrid BM25 search failed ({exc}); used vector search.",
            )
            return (*_unpack_chroma_results(vector_results), "vector")

    docs, metas, scores = _unpack_chroma_results(vector_results)
    if not bm25_docs:
        _record_search_warning(
            warnings,
            "Hybrid BM25 search returned no candidates; used vector search.",
        )
        return docs, metas, scores, "vector"

    dense_ranked = list(zip(docs, metas, scores))
    sparse_ranked = list(zip(bm25_docs, bm25_metas, bm25_scores))
    merged = _reciprocal_rank_fusion(
        [dense_ranked, sparse_ranked], k=rrf_k, top_n=fetch_n,
        weights=[dense_weight, sparse_weight])
    log.debug(
        "Hybrid: %d vector + %d BM25 -> %d merged",
        len(dense_ranked), len(sparse_ranked), len(merged),
    )
    return (
        [item[0] for item in merged],
        [dict(item[1] or {}) for item in merged],
        [float(item[2]) for item in merged],
        "hybrid",
    )


def _search_chroma_candidates(
        query: str, db_dir: Path, *, n_results: int,
        content_type: str | None, chapter_num: int | None,
        collection_name: str, embedding_model: str, hybrid: bool,
        chunks_path: Path, warnings: list[str],
        rrf_k: int = DEFAULT_RRF_K,
        dense_weight: float = DEFAULT_DENSE_RRF_WEIGHT,
        sparse_weight: float = DEFAULT_SPARSE_RRF_WEIGHT,
        expected_dimension: int | None = None,
        expected_source_sha256: str | None = None,
        security_policy: (
            _release_security.ReleaseSecurityPolicy | None
        ) = None,
) -> tuple[list[str], list[dict], list[float], str]:
    """Retrieve Chroma candidates and deterministically close the client."""
    client_owner = _VectorClientOwner("Chroma")
    operation_error = None
    try:
        return _search_chroma_candidates_impl(
            query, db_dir, n_results=n_results,
            content_type=content_type, chapter_num=chapter_num,
            collection_name=collection_name,
            embedding_model=embedding_model, hybrid=hybrid,
            chunks_path=chunks_path, warnings=warnings,
            rrf_k=rrf_k, dense_weight=dense_weight,
            sparse_weight=sparse_weight,
            expected_dimension=expected_dimension,
            expected_source_sha256=expected_source_sha256,
            security_policy=security_policy,
            _client_owner=client_owner,
        )
    except BaseException as exc:
        operation_error = exc
        raise
    finally:
        client_owner.finish(operation_error)


def _search_qdrant_candidates(
        query: str, db_dir: Path, *, n_results: int,
        content_type: str | None, chapter_num: int | None,
        collection_name: str, embedding_model: str, hybrid: bool,
        warnings: list[str], expected_dimension: int | None = None,
        security_policy: (
            _release_security.ReleaseSecurityPolicy | None
        ) = None,
) -> tuple[list[str], list[dict], list[float], str]:
    """Retrieve Qdrant candidates with native dense/sparse fusion."""
    from qdrant_client import QdrantClient, models

    client = None
    operation_error = None
    try:
        client = QdrantClient(path=str(db_dir))
        if not client.collection_exists(collection_name):
            raise LookupError(
                f"Collection '{collection_name}' not found in {db_dir}")

        conditions = []
        if content_type:
            conditions.append(models.FieldCondition(
                key="content_type",
                match=models.MatchValue(value=content_type),
            ))
        if chapter_num is not None:
            conditions.append(models.FieldCondition(
                key="chapter_num",
                match=models.MatchValue(value=chapter_num),
            ))
        query_filter = models.Filter(must=conditions) if conditions else None

        fetch_n = n_results
        query_vector = _embed_texts(
            [query], embedding_model, input_type="query",
            security_policy=security_policy)[0]
        _validate_query_vector_dimension(
            query_vector, expected_dimension, embedding_model)
        effective_mode = "vector"

        if hybrid:
            sparse_indices, sparse_values = _sparse_token_vector(query)
            sparse_vector = models.SparseVector(
                indices=sparse_indices,
                values=sparse_values,
            )
            try:
                results = client.query_points(
                    collection_name=collection_name,
                    prefetch=[
                        models.Prefetch(
                            query=query_vector, using="", limit=fetch_n,
                            filter=query_filter,
                        ),
                        models.Prefetch(
                            query=sparse_vector, using="bm25", limit=fetch_n,
                            filter=query_filter,
                        ),
                    ],
                    query=models.FusionQuery(fusion=models.Fusion.RRF),
                    limit=fetch_n,
                )
                effective_mode = "hybrid"
            except Exception as exc:
                _record_search_warning(
                    warnings,
                    f"Qdrant hybrid search failed ({exc}); used vector search.",
                )
                results = client.query_points(
                    collection_name=collection_name,
                    query=query_vector, using="", limit=fetch_n,
                    query_filter=query_filter,
                )
        else:
            results = client.query_points(
                collection_name=collection_name,
                query=query_vector, using="", limit=fetch_n,
                query_filter=query_filter,
            )

        docs = []
        metas = []
        scores = []
        for point in results.points:
            payload = dict(point.payload or {})
            docs.append(str(payload.pop("text", "")))
            metas.append(payload)
            scores.append(float(point.score))
        return docs, metas, scores, effective_mode
    except BaseException as exc:
        operation_error = exc
        raise
    finally:
        _finish_vector_client(
            client, client_name="Qdrant",
            primary_error=operation_error)


def _search_hit_source_id(hit: SearchHit) -> str:
    """Resolve a source ID through the compatibility facade's chunk helper."""
    return _retrieval_core._search_hit_source_id(
        hit, chunk_id_fn=_chunk_id)


def _search_index_impl(query: str, db_dir: Path, *,
                       db_backend: str = DEFAULT_DB_BACKEND,
                       n_results: int = 5,
                       content_type: Optional[str] = None,
                       chapter_num: Optional[int] = None,
                       collection_name: str = DEFAULT_COLLECTION,
                       embedding_model: str = DEFAULT_EMBEDDING_MODEL,
                       use_reranker: bool | None = None,
                       hybrid: bool | None = None,
                       chunks_path: Path = DEFAULT_CHUNKS_PATH,
                       reranker_model: str = DEFAULT_RERANKER_MODEL,
                       overfetch: int = RERANK_OVERFETCH,
                       rrf_k: int = DEFAULT_RRF_K,
                       dense_weight: float = DEFAULT_DENSE_RRF_WEIGHT,
                       sparse_weight: float = DEFAULT_SPARSE_RRF_WEIGHT,
                       context_window: int = 0,
                       context_max_characters: int = (
                           DEFAULT_CONTEXT_MAX_CHARACTERS),
                       context_segment_characters: int = (
                           DEFAULT_CONTEXT_SEGMENT_CHARACTERS),
                       lock_timeout: float = DEFAULT_DB_LOCK_TIMEOUT,
                       security_policy: (
                           _release_security.ReleaseSecurityPolicy | None
                       ) = None,
                       ) -> SearchResponse:
    """Search either supported vector backend and return structured results.

    Hybrid and reranker requests over-fetch candidates before reducing them to
    ``n_results``.  Any recoverable hybrid or reranking failure is reflected in
    ``effective_mode``, ``reranker_applied``, and ``warnings`` rather than being
    mislabeled as a successful requested mode.
    """
    policy = _effective_security_policy(security_policy)
    if embedding_model.startswith(_API_EMBEDDING_MODEL_PREFIXES):
        _release_security.require_cloud_egress(
            policy, feature="cloud embedding")
    if (
        use_reranker is not False
        and reranker_model.startswith(("cohere-rerank", "jina-reranker"))
    ):
        _release_security.require_cloud_egress(
            policy, feature="cloud reranking")
    backend = db_backend.lower()
    if backend not in {"chroma", "qdrant"}:
        raise ValueError("db_backend must be 'chroma' or 'qdrant'")
    if not query.strip():
        raise ValueError("query must not be blank")
    if n_results < 1:
        raise ValueError("n_results must be at least 1")
    if isinstance(overfetch, bool) or not isinstance(overfetch, int) \
            or not 1 <= overfetch <= 20:
        raise ValueError("overfetch must be an integer from 1 to 20")
    if isinstance(rrf_k, bool) or not isinstance(rrf_k, int) or rrf_k < 1:
        raise ValueError("rrf_k must be a positive integer")
    if (isinstance(context_window, bool)
            or not isinstance(context_window, int)
            or not 0 <= context_window <= MAX_CONTEXT_WINDOW):
        raise ValueError(
            f"context_window must be an integer from 0 to {MAX_CONTEXT_WINDOW}")
    if (isinstance(context_max_characters, bool)
            or not isinstance(context_max_characters, int)
            or not 1 <= context_max_characters <= MAX_CONTEXT_CHARACTERS):
        raise ValueError(
            "context_max_characters must be an integer from 1 to "
            f"{MAX_CONTEXT_CHARACTERS}")
    if (isinstance(context_segment_characters, bool)
            or not isinstance(context_segment_characters, int)
            or not 1 <= context_segment_characters
            <= MAX_CONTEXT_SEGMENT_CHARACTERS):
        raise ValueError(
            "context_segment_characters must be an integer from 1 to "
            f"{MAX_CONTEXT_SEGMENT_CHARACTERS}")
    try:
        dense_weight = float(dense_weight)
        sparse_weight = float(sparse_weight)
    except (TypeError, ValueError) as exc:
        raise ValueError("fusion weights must be numeric") from exc
    if (not math.isfinite(dense_weight) or not math.isfinite(sparse_weight)
            or dense_weight < 0 or sparse_weight < 0
            or dense_weight + sparse_weight == 0):
        raise ValueError(
            "fusion weights must be finite, non-negative, and not both zero")

    db_path = Path(db_dir)
    if not db_path.is_dir():
        raise FileNotFoundError(f"{backend.title()} directory not found: {db_path}")

    requested_mode = (
        "auto" if hybrid is None else "hybrid" if hybrid else "vector")
    warnings: list[str] = []
    chunks_file = Path(chunks_path)
    context_records: list[dict] | None = None
    context_source_sha256: str | None = None
    indexed_table_children = 0
    if context_window:
        if not chunks_file.is_file():
            raise FileNotFoundError(
                f"Context assembly requires chunks JSONL: {chunks_file}")
        context_records, context_source_sha256, _, _ = (
            _load_index_snapshot_with_quality(chunks_file))
    hybrid_enabled = (
        backend == "qdrant" or chunks_file.is_file()) if hybrid is None else hybrid
    if backend == "qdrant" and hybrid_enabled and rrf_k != DEFAULT_RRF_K:
        _record_search_warning(
            warnings,
            "Custom RRF k applies only to Chroma; Qdrant used its native RRF.",
        )
    if (backend == "qdrant" and hybrid_enabled
            and (dense_weight != DEFAULT_DENSE_RRF_WEIGHT
                 or sparse_weight != DEFAULT_SPARSE_RRF_WEIGHT)):
        _record_search_warning(
            warnings,
            "Custom fusion weights apply only to Chroma; Qdrant used native RRF.",
        )
    with _vector_store_lock(
            db_path, backend=backend, collection_name=collection_name,
            operation="vector search", timeout=lock_timeout):
        db_path = _storage_policy.ensure_private_tree(db_path)
        expected_dimension = _query_manifest_dimension(
            db_path, backend=backend, collection_name=collection_name,
            embedding_model=embedding_model,
            allow_legacy=not bool(context_window))
        indexed_table_children = _indexed_table_child_count(
            db_path, backend=backend, collection_name=collection_name)
        hybrid_source_sha256 = None
        if context_records is not None:
            manifested_source_sha256 = _require_hybrid_chunks_snapshot(
                chunks_file, db_path, backend=backend,
                collection_name=collection_name)
            if (manifested_source_sha256 is None
                    or manifested_source_sha256 != context_source_sha256):
                raise ValueError(
                    "Context chunks snapshot does not match the indexed corpus")
            if backend == "chroma":
                hybrid_source_sha256 = manifested_source_sha256
        if backend == "chroma" and hybrid_enabled and chunks_file.is_file():
            if hybrid_source_sha256 is None:
                hybrid_source_sha256 = _require_hybrid_chunks_snapshot(
                    chunks_file, db_path, backend=backend,
                    collection_name=collection_name)
            if hybrid_source_sha256 is None:
                _record_search_warning(
                    warnings,
                    "Hybrid search requires a manifested chunks SHA-256; used "
                    "vector search for this legacy or incomplete index.",
                )
                hybrid_enabled = False
        fetch_n = (
            n_results * overfetch
            if (hybrid_enabled or use_reranker is not False
                or indexed_table_children) else n_results)

        if backend == "chroma":
            docs, metas, scores, effective_mode = _search_chroma_candidates(
                query, db_path, n_results=fetch_n,
                content_type=content_type, chapter_num=chapter_num,
                collection_name=collection_name,
                embedding_model=embedding_model,
                hybrid=hybrid_enabled, chunks_path=chunks_file,
                warnings=warnings, rrf_k=rrf_k,
                dense_weight=dense_weight, sparse_weight=sparse_weight,
                expected_dimension=expected_dimension,
                expected_source_sha256=hybrid_source_sha256,
                security_policy=policy,
            )
        else:
            docs, metas, scores, effective_mode = _search_qdrant_candidates(
                query, db_path, n_results=fetch_n,
                content_type=content_type, chapter_num=chapter_num,
                collection_name=collection_name,
                embedding_model=embedding_model, hybrid=hybrid_enabled,
                warnings=warnings, expected_dimension=expected_dimension,
                security_policy=policy,
            )

        if (backend == "chroma" and effective_mode == "hybrid"
                and chunks_file.is_file()):
            # Close the lexical-artifact TOCTOU window: a standalone atomic
            # replacement racing this search must be checked again after BM25
            # has consumed its exact snapshot.
            _require_hybrid_chunks_snapshot(
                chunks_file, db_path, backend=backend,
                collection_name=collection_name)

    if indexed_table_children:
        docs, metas, scores = _table_retrieval_core.collapse_table_families(
            docs, metas, scores)

    reranker_enabled = (
        effective_mode == "vector" if use_reranker is None else use_reranker)
    reranker_applied = False
    if reranker_enabled and len(docs) > 1:
        try:
            docs, metas, scores = _rerank(
                query, docs, metas, [1.0 - score for score in scores],
                n_results, reranker_model=reranker_model,
                security_policy=policy,
            )
            reranker_applied = True
        except Exception as exc:
            _record_search_warning(
                warnings,
                f"Reranker failed ({exc}); kept {effective_mode} ranking.",
            )
    elif reranker_enabled:
        _record_search_warning(
            warnings,
            "Reranker requested but fewer than two candidates were available; "
            "reranking was not applied.",
        )

    hits = []
    for doc, meta, score in zip(
            docs[:n_results], metas[:n_results], scores[:n_results]):
        hit = SearchHit(
            text=doc, metadata=dict(meta or {}), score=float(score))
        hit.source_id = _search_hit_source_id(hit)
        hits.append(hit)
    response = SearchResponse(
        hits=hits, backend=backend, requested_mode=requested_mode,
        effective_mode=effective_mode,
        reranker_applied=reranker_applied, warnings=warnings,
        candidate_depth=fetch_n,
        reranker_model=reranker_model if reranker_applied else None,
    )
    if context_records is not None:
        _retrieval_core._assemble_retrieval_context(
            response, context_records,
            context_window=context_window,
            content_type=content_type,
            chapter_num=chapter_num,
            max_characters=context_max_characters,
            segment_characters=context_segment_characters,
            source_id_fn=_search_hit_source_id,
        )
    return response


def search_index(query: str, db_dir: Path, *,
                 db_backend: str = DEFAULT_DB_BACKEND,
                 n_results: int = 5,
                 content_type: Optional[str] = None,
                 chapter_num: Optional[int] = None,
                 collection_name: str = DEFAULT_COLLECTION,
                 embedding_model: str = DEFAULT_EMBEDDING_MODEL,
                 use_reranker: bool | None = None,
                 hybrid: bool | None = None,
                 chunks_path: Path = DEFAULT_CHUNKS_PATH,
                 reranker_model: str = DEFAULT_RERANKER_MODEL,
                 overfetch: int = RERANK_OVERFETCH,
                 rrf_k: int = DEFAULT_RRF_K,
                 dense_weight: float = DEFAULT_DENSE_RRF_WEIGHT,
                 sparse_weight: float = DEFAULT_SPARSE_RRF_WEIGHT,
                 context_window: int = 0,
                 context_max_characters: int = (
                     DEFAULT_CONTEXT_MAX_CHARACTERS),
                 context_segment_characters: int = (
                     DEFAULT_CONTEXT_SEGMENT_CHARACTERS),
                 lock_timeout: float = DEFAULT_DB_LOCK_TIMEOUT,
                 security_policy: (
                     _release_security.ReleaseSecurityPolicy | None
                 ) = None,
                 ) -> SearchResponse:
    """Search a coherent local index generation under its exclusive lease."""
    backend = db_backend.lower()
    if backend not in {"chroma", "qdrant"}:
        # Preserve the public validation error without creating a lock sidecar.
        return _search_index_impl(
            query, db_dir, db_backend=db_backend, n_results=n_results,
            content_type=content_type, chapter_num=chapter_num,
            collection_name=collection_name,
            embedding_model=embedding_model, use_reranker=use_reranker,
            hybrid=hybrid, chunks_path=chunks_path,
            reranker_model=reranker_model, overfetch=overfetch, rrf_k=rrf_k,
            dense_weight=dense_weight, sparse_weight=sparse_weight,
            context_window=context_window,
            context_max_characters=context_max_characters,
            context_segment_characters=context_segment_characters,
            lock_timeout=lock_timeout,
            security_policy=security_policy)
    db_path = Path(db_dir)
    return _search_index_impl(
        query, db_path, db_backend=backend, n_results=n_results,
        content_type=content_type, chapter_num=chapter_num,
        collection_name=collection_name,
        embedding_model=embedding_model, use_reranker=use_reranker,
        hybrid=hybrid, chunks_path=chunks_path,
        reranker_model=reranker_model, overfetch=overfetch, rrf_k=rrf_k,
        dense_weight=dense_weight, sparse_weight=sparse_weight,
        context_window=context_window,
        context_max_characters=context_max_characters,
        context_segment_characters=context_segment_characters,
        lock_timeout=lock_timeout,
        security_policy=security_policy)


_ANSWER_SOURCE_LIMIT = _retrieval_core._ANSWER_SOURCE_LIMIT
_ANSWER_SOURCE_CHAR_LIMIT = _retrieval_core._ANSWER_SOURCE_CHAR_LIMIT
_INSUFFICIENT_EVIDENCE_TEXT = _retrieval_core._INSUFFICIENT_EVIDENCE_TEXT
_BRACKETED_TEXT_RE = _retrieval_core._BRACKETED_TEXT_RE
_SOURCE_CITATION_RE = _retrieval_core._SOURCE_CITATION_RE
_DIRECT_QUOTE_RE = _retrieval_core._DIRECT_QUOTE_RE


_useful_source_metadata = _retrieval_core._useful_source_metadata


_query_centered_excerpt = _retrieval_core._query_centered_excerpt


def _grounded_sources(response: SearchResponse,
                      query: str = "") -> list[GroundedSource]:
    """Build sources through the facade's dynamically replaceable resolver."""
    return _retrieval_core._grounded_sources(
        response, query, source_id_fn=_search_hit_source_id)


_grounded_answer_prompt = _retrieval_core._grounded_answer_prompt


_validate_grounded_answer = _retrieval_core._validate_grounded_answer


def _answer_search_results(query: str, response: SearchResponse, *,
                           answer: bool, cloud_url: str, cloud_model: str,
                           cloud_key: str, ollama_url: str,
                           ollama_model: str, gemini_key: str,
                           llm_workers: int = DEFAULT_LLM_WORKERS,
                           thinking: bool = False,
                           security_policy: (
                               _release_security.ReleaseSecurityPolicy | None
                           ) = None) -> GroundedAnswer | None:
    """Optionally generate a source-grounded answer from structured hits."""
    if not answer:
        return None
    sources = _grounded_sources(response, query)
    if not sources:
        return GroundedAnswer(
            text=_INSUFFICIENT_EVIDENCE_TEXT,
            citations=[], sources=[],
            warnings=["No retrieved sources were available for answer generation."],
            abstained=True,
        )

    answer_prompt = _grounded_answer_prompt(query, sources)
    llm_answer = _call_llm(
        answer_prompt, cloud_url=cloud_url, cloud_model=cloud_model,
        cloud_key=cloud_key, ollama_url=ollama_url,
        ollama_model=ollama_model, gemini_key=gemini_key,
        llm_workers=llm_workers, thinking=thinking,
        operation="query.grounded_answer",
        security_policy=security_policy,
    )
    if not llm_answer:
        return GroundedAnswer(
            text=_INSUFFICIENT_EVIDENCE_TEXT,
            citations=[], sources=sources,
            warnings=["The configured language model returned no answer."],
            abstained=True,
        )
    return _validate_grounded_answer(llm_answer, sources)


def _write_search_output(query: str, response: SearchResponse, *,
                         output_json: bool,
                         llm_answer: GroundedAnswer | str | None,
                         content_type: str | None,
                         chapter_num: int | None) -> None:
    """Render structured results using the existing CLI/JSON schema."""
    output_hits = []
    for hit in response.hits:
        output_hit = {
            "score": round(hit.score, 4),
            "search_mode": response.effective_mode,
            "reranked": response.reranker_applied,
            "text": hit.text,
            "metadata": hit.metadata,
        }
        if response.context_window:
            output_hit["source_id"] = hit.source_id
            output_hit["equivalent_sources"] = [
                {
                    "source_id": alias.source_id,
                    "metadata": alias.metadata,
                }
                for alias in hit.source_aliases
            ]
            output_hit["context"] = [
                {
                    "source_id": segment.source_id,
                    "relation": segment.relation,
                    "distance": segment.distance,
                    "text": segment.text,
                    "metadata": segment.metadata,
                    "equivalent_sources": [
                        {
                            "source_id": alias.source_id,
                            "metadata": alias.metadata,
                        }
                        for alias in segment.source_aliases
                    ],
                }
                for segment in hit.context_segments
            ]
        output_hits.append(output_hit)
    if output_json:
        payload: object = output_hits
        if llm_answer:
            if isinstance(llm_answer, GroundedAnswer):
                # Preserve the established string-valued ``answer`` and
                # ``results`` keys while adding machine-readable grounding.
                payload = {
                    "answer": llm_answer.text,
                    "citations": llm_answer.citations,
                    "sources": llm_answer.source_mapping(),
                    "answer_warnings": llm_answer.warnings,
                    "abstained": llm_answer.abstained,
                    "results": output_hits,
                }
            else:
                payload = {"answer": llm_answer, "results": output_hits}
        print(json.dumps(payload, indent=2, ensure_ascii=False))
        return

    if llm_answer:
        answer_text = (
            llm_answer.text
            if isinstance(llm_answer, GroundedAnswer)
            else llm_answer
        )
        print(f"\n{'=' * 80}")
        print("ANSWER")
        print("=" * 80)
        print(answer_text)
        if isinstance(llm_answer, GroundedAnswer):
            cited_sources = llm_answer.source_mapping(cited_only=True)
            if cited_sources:
                print("\nSources:")
                for citation_id, source in cited_sources.items():
                    meta = source["metadata"]
                    location = " | ".join(str(value) for value in (
                        meta.get("source_file"),
                        meta.get("page_range"),
                        meta.get("section_path"),
                    ) if value not in (None, ""))
                    suffix = f" — {location}" if location else ""
                    print(
                        f"  [{citation_id}] {source['source_id']}{suffix}")
            for warning in llm_answer.warnings:
                print(f"Warning: {warning}")

    print(f"\n{'=' * 80}")
    print(f"Query: {query}")
    mode = response.effective_mode
    if response.backend == "qdrant":
        mode = f"qdrant {mode}"
    if response.reranker_applied:
        mode += " + reranked"
    requested = (f"; requested={response.requested_mode}"
                 if response.requested_mode != response.effective_mode else "")
    print(f"Mode:  {mode}{requested}")
    print(f"Candidate depth: {response.candidate_depth}")
    if response.reranker_model:
        print(f"Reranker: {response.reranker_model}")
    if content_type or chapter_num is not None:
        print(f"Filters: content_type={content_type}, chapter={chapter_num}")
    score_label = ("rerank" if response.reranker_applied else
                   "rrf" if response.effective_mode == "hybrid" else
                   "similarity")
    print(f"{'=' * 80}\n")

    for index, hit in enumerate(response.hits, 1):
        meta = hit.metadata
        print(f"--- Result {index} ({score_label}: {hit.score:.3f}) ---")
        print(f"  Type:    {meta.get('content_type', '?')}")
        print(f"  Section: {meta.get('section_path', '?')}")
        print(f"  Case:    {meta.get('primary_case', 'n/a')}")
        print(f"  Pages:   {meta.get('page_range', '?')}")
        context = meta.get("context", "")
        if context:
            print(f"  Context: {context}")
        print(f"  Text:    {hit.text[:300]}...")
        for segment in hit.context_segments:
            label = f"{segment.relation} {segment.distance}"
            print(
                f"  Neighbor ({label}, {segment.source_id}): "
                f"{segment.text[:300]}...")
        print()


def query_index(query: str, chroma_dir: Path, *,
                n_results: int = 5,
                content_type: Optional[str] = None,
                chapter_num: Optional[int] = None,
                collection_name: str = DEFAULT_COLLECTION,
                embedding_model: str = DEFAULT_EMBEDDING_MODEL,
                output_json: bool = False,
                use_reranker: bool | None = None,
                hybrid: bool | None = None,
                chunks_path: Path = DEFAULT_CHUNKS_PATH,
                reranker_model: str = DEFAULT_RERANKER_MODEL,
                overfetch: int = RERANK_OVERFETCH,
                rrf_k: int = DEFAULT_RRF_K,
                dense_weight: float = DEFAULT_DENSE_RRF_WEIGHT,
                sparse_weight: float = DEFAULT_SPARSE_RRF_WEIGHT,
                context_window: int = 0,
                context_max_characters: int = (
                    DEFAULT_CONTEXT_MAX_CHARACTERS),
                context_segment_characters: int = (
                    DEFAULT_CONTEXT_SEGMENT_CHARACTERS),
                answer: bool = False,
                cloud_url: str = DEFAULT_CLOUD_URL,
                cloud_model: str = DEFAULT_CLOUD_MODEL,
                cloud_key: str = "",
                ollama_url: str = DEFAULT_OLLAMA_URL,
                ollama_model: str = DEFAULT_OLLAMA_MODEL,
                gemini_key: str = "",
                llm_workers: int = DEFAULT_LLM_WORKERS,
                thinking: bool = False,
                lock_timeout: float = DEFAULT_DB_LOCK_TIMEOUT,
                security_policy: (
                    _release_security.ReleaseSecurityPolicy | None
                ) = None) -> None:
    """Query Chroma and preserve the legacy CLI/JSON output contract."""
    try:
        response = search_index(
            query, chroma_dir, db_backend="chroma", n_results=n_results,
            content_type=content_type, chapter_num=chapter_num,
            collection_name=collection_name, embedding_model=embedding_model,
            use_reranker=use_reranker, hybrid=hybrid,
            chunks_path=chunks_path,
            reranker_model=reranker_model, overfetch=overfetch, rrf_k=rrf_k,
            dense_weight=dense_weight, sparse_weight=sparse_weight,
            context_window=context_window,
            context_max_characters=context_max_characters,
            context_segment_characters=context_segment_characters,
            lock_timeout=lock_timeout,
            security_policy=security_policy,
        )
    except (FileNotFoundError, LookupError) as exc:
        log.error(str(exc))
        log.error("  Run: python rag.py index")
        raise SystemExit(1) from exc

    llm_answer = _answer_search_results(
        query, response,
        answer=answer, cloud_url=cloud_url, cloud_model=cloud_model,
        cloud_key=cloud_key, ollama_url=ollama_url,
        ollama_model=ollama_model, gemini_key=gemini_key,
        llm_workers=llm_workers, thinking=thinking,
        security_policy=security_policy,
    )
    _write_search_output(
        query, response, output_json=output_json, llm_answer=llm_answer,
        content_type=content_type, chapter_num=chapter_num,
    )


# ---------------------------------------------------------------------------
# Step 5: Export — clean, LLM-ready markdown
# ---------------------------------------------------------------------------

def _format_chunk(rec: dict) -> list[str]:
    """Format a single chunk record as markdown lines."""
    meta = rec["metadata"]
    text = rec["text"]
    ct = meta["content_type"]
    lines: list[str] = []

    # --- Content-type HTML comment tag ---
    _CONTENT_TYPE_TAGS = {
        "case_opinion": lambda m: f"<!-- CASE: {m.get('primary_case', 'Unknown')} -->",
        "notes_and_questions": lambda m: "<!-- NOTES AND QUESTIONS -->",
        "statutory_excerpt": lambda m: "<!-- STATUTE -->",
        "footnote": lambda m: "<!-- FOOTNOTE -->",
        "table": lambda m: "<!-- TABLE -->",
        "chapter_introduction": lambda m: "<!-- CHAPTER INTRODUCTION -->",
    }
    tag_fn = _CONTENT_TYPE_TAGS.get(ct)
    if tag_fn:
        lines.append(tag_fn(meta))

    ctx = meta.get("context", "")
    if ctx:
        lines.append(f"\n*{ctx}*\n")

    if ct == "case_opinion":
        case_name = meta.get("primary_case", "Case")
        page_ref = meta.get("page_range", "")
        lines.append(f"\n##### {case_name}")
        if page_ref:
            lines.append(f"*{page_ref}*\n")
        lines.append(text)
        lines.append("")
    elif ct == "statutory_excerpt":
        lines.append(f"\n> **Statutory Text** ({meta.get('page_range', '')})")
        for sline in text.split("\n"):
            lines.append(f"> {sline}")
        lines.append("")
    elif ct == "table":
        lines.append(f"\n{text}\n")
    elif ct == "notes_and_questions":
        lines.append(f"\n**Notes and Questions** ({meta.get('page_range', '')})\n")
        lines.append(text)
        lines.append("")
    elif ct == "footnote":
        lines.append("\n<details><summary>Footnote</summary>\n")
        lines.append(text)
        lines.append("\n</details>\n")
    elif ct == "chapter_introduction":
        lines.append("\n**Chapter Introduction**\n")
        lines.append(text)
        lines.append("")
    else:
        lines.append(f"\n{text}\n")

    return lines


def _filter_chunk_records(records: list[dict], *,
                          include_types: list[str] | None = None,
                          exclude_types: list[str] | None = None,
                          chapters: list[int] | None = None) -> list[dict]:
    """Apply export filters to one already-captured chunks generation."""
    log.info(f"Loaded {len(records)} chunks for export")

    if exclude_types is None:
        exclude_types = ["structural", "empty"]

    filtered = []
    for rec in records:
        if _table_retrieval_core.is_table_child(rec.get("metadata")):
            continue
        ct = rec["metadata"]["content_type"]
        if ct in exclude_types:
            continue
        if include_types and ct not in include_types:
            continue
        if chapters and rec["metadata"].get("chapter_num") not in chapters:
            continue
        filtered.append(rec)

    log.info(f"After filtering: {len(filtered)} chunks "
             f"(excluded {len(records) - len(filtered)})")
    return filtered


def _load_and_filter_chunks(chunks_path: Path, *,
                            include_types: list[str] | None = None,
                            exclude_types: list[str] | None = None,
                            chapters: list[int] | None = None) -> list[dict]:
    """Load chunks JSONL and apply filters."""
    _require_file(chunks_path, "Chunks JSONL")
    records, _, _ = _load_index_snapshot_strict(chunks_path)
    return _filter_chunk_records(
        records, include_types=include_types,
        exclude_types=exclude_types, chapters=chapters)


def _section_heading_level(part: str, depth: int) -> str:
    """Return a markdown heading prefix for a section_path part at *depth*.

    Depth mapping:
        0 (chapter)          -> # (handled separately)
        1 (letter A-Z)       -> ##
        2 (number 1-9)       -> ###
        3 (letter a-z)       -> ####
        4+ (anything else)   -> #####
    """
    if depth <= 0:
        return "#"
    if depth == 1:
        return "##"
    if depth == 2:
        return "###"
    if depth == 3:
        return "####"
    return "#####"


def _display_division_title(ordinal: int, title: str) -> str:
    """Render a normalized division without duplicating its designation."""
    cleaned = str(title or "").strip()
    if re.match(
            r"^(?:Chapter|Part|Unit)\s+"
            r"(?:\d{1,3}|[IVXLCDM]+|[A-Za-z-]+)\b",
            cleaned, re.I):
        return cleaned
    if cleaned:
        return f"Chapter {ordinal} - {cleaned}"
    return f"Chapter {ordinal}"


def _assemble_markdown(chunks: list[dict]) -> str:
    """Assemble filtered chunks into a single structured markdown string."""
    lines: list[str] = []
    current_chapter = None
    # Track emitted headings per depth so we don't repeat them
    emitted_sections: dict[int, str] = {}

    for rec in chunks:
        meta = rec["metadata"]

        ch = meta.get("chapter_num")
        ch_title = meta.get("chapter_title", "")
        if ch and ch != current_chapter:
            current_chapter = ch
            lines.append(
                f"\n\n---\n\n# {_display_division_title(ch, ch_title)}\n")
            emitted_sections.clear()

        sp = meta.get("section_path", "")
        if sp and sp != emitted_sections.get(-1, ""):
            emitted_sections[-1] = sp  # raw path tracker
            # Scaffold, deterministic, and legacy paths use three separators.
            parts = re.split(r"\s+(?:\u2192|->|>)\s+", sp)
            # Skip the chapter part if it's repeated as parts[0]
            if (parts and (
                    parts[0].strip() == str(ch_title).strip()
                    or re.match(
                        r"^(?:Chapter|Part|Unit)\s+", parts[0], re.I))):
                parts = parts[1:]
            # Emit each new level heading that hasn't been emitted yet
            for depth_idx, part in enumerate(parts):
                part_stripped = part.strip()
                if not part_stripped:
                    continue
                prev = emitted_sections.get(depth_idx)
                if prev == part_stripped:
                    continue
                # Clear deeper levels when a parent changes
                for k in list(emitted_sections.keys()):
                    if isinstance(k, int) and k > depth_idx and k >= 0:
                        del emitted_sections[k]
                emitted_sections[depth_idx] = part_stripped
                prefix = _section_heading_level(part_stripped, depth_idx + 1)
                lines.append(f"\n{prefix} {part_stripped}\n")

        lines.extend(_format_chunk(rec))

    return "\n".join(lines)


def _export_plaintext(chunks: list[dict], export_path: Path) -> None:
    """Export chunks as a plain-text file + JSON metadata sidecar.

    Writes two files:
      <export_path>.txt             -- plain text, one chunk per block
      <export_path>.metadata.json   -- chunk metadata with char_start/char_end
                                       offsets for Anthropic citation API
    """
    txt_path = export_path.with_suffix(".txt")
    meta_path = export_path.parent / (export_path.stem + ".metadata.json")
    _storage_policy.ensure_private_directory(export_path.parent)

    text_parts: list[str] = []
    metadata_records: list[dict] = []
    char_cursor = 0

    for i, rec in enumerate(chunks):
        meta = rec["metadata"]
        text = rec["text"]

        ct = meta.get("content_type", "")
        page_start = meta.get("page_start")
        page_end = meta.get("page_end")
        primary_case = meta.get("primary_case", "")

        page_part = ""
        if page_start is not None and page_end is not None:
            page_part = f" | pp.{page_start}-{page_end}"
        elif page_start is not None:
            page_part = f" | p.{page_start}"

        case_part = f" | {primary_case}" if primary_case else ""
        delimiter = f"[CHUNK {i} | {ct}{page_part}{case_part}]"
        block = f"{delimiter}\n{text}"

        if i > 0:
            separator = "\n\n"
            char_cursor += len(separator)
            text_parts.append(separator)

        char_start = char_cursor
        char_end = char_cursor + len(block)
        char_cursor = char_end
        text_parts.append(block)

        metadata_records.append({
            "chunk_index": meta.get("chunk_index", i),
            "content_type": ct,
            "source_file": meta.get("source_file", ""),
            "page_start": page_start,
            "page_end": page_end,
            "section_path": meta.get("section_path", ""),
            "primary_case": primary_case,
            "content_source": meta.get("content_source", ""),
            "context": meta.get("context", ""),
            "chapter_num": meta.get("chapter_num"),
            "chapter_title": meta.get("chapter_title", ""),
            "case_names": meta.get("case_names", []),
            "char_start": char_start,
            "char_end": char_end,
        })

    full_text = "".join(text_parts)
    _atomic_write_text(txt_path, full_text)
    _atomic_write_text(
        meta_path,
        json.dumps(metadata_records, indent=2, ensure_ascii=False),
    )
    log.info(f"Exported {len(chunks)} chunks (plaintext) -> {txt_path} "
             f"({len(full_text) / 1e6:.1f} MB)")
    log.info(f"Metadata sidecar -> {meta_path}")


def _markdown_export_parameters(*, include_types: list[str] | None,
                                exclude_types: list[str] | None,
                                chapters: list[int] | None,
                                split_chapters: bool) -> dict:
    effective_excludes = (
        ["structural", "empty"]
        if exclude_types is None else exclude_types)
    return {
        "format": "markdown",
        "include_types": sorted(set(include_types or [])),
        "exclude_types": sorted(set(effective_excludes)),
        "chapters": sorted(set(chapters or [])),
        "split_chapters": split_chapters,
    }


def _chunks_identity(path: Path) -> tuple[str, int] | None:
    try:
        records, source_sha256, _ = _load_index_snapshot_strict(path)
        return source_sha256, len(records)
    except (OSError, UnicodeError, ValueError, RuntimeError):
        return None


def _unified_export_complete(chunks_path: Path, export_path: Path, *,
                             parameters: dict) -> bool:
    identity = _chunks_identity(chunks_path)
    if identity is None:
        return False
    source_sha256, source_count = identity
    return _fixed_artifacts_complete(
        _artifact_completion_path(export_path, stage="unified_export"),
        stage="unified_export", source_sha256=source_sha256,
        source_record_count=source_count, parameters=parameters,
        outputs={"markdown": export_path})


def _owned_chapter_filename(name: str) -> bool:
    return bool(
        name == "front_matter.md"
        or re.fullmatch(r"ch\d+(?:_[\w-]+)?\.md", name))


def _split_export_complete(chunks_path: Path, chapters_dir: Path, *,
                           parameters: dict) -> bool:
    identity = _chunks_identity(chunks_path)
    if identity is None:
        return False
    manifest_path = _artifact_completion_path(
        chapters_dir, stage="split_export")
    try:
        payload = json.loads(manifest_path.read_text(encoding="utf-8"))
        records = payload.get("outputs") if isinstance(payload, dict) else None
        if not isinstance(records, list) or not records:
            return False
        names = []
        for record in records:
            if not isinstance(record, dict):
                return False
            name = record.get("name")
            if (not isinstance(name, str) or not _owned_chapter_filename(name)
                    or record.get("role") != name):
                return False
            names.append(name)
        if len(names) != len(set(names)):
            return False
        actual_names = {
            path.name for path in chapters_dir.glob("*.md")
            if _owned_chapter_filename(path.name)
        }
        if actual_names != set(names):
            return False
        source_sha256, source_count = identity
        return _fixed_artifacts_complete(
            manifest_path, stage="split_export",
            source_sha256=source_sha256,
            source_record_count=source_count, parameters=parameters,
            outputs={name: chapters_dir / name for name in names})
    except (OSError, UnicodeError, json.JSONDecodeError):
        return False


def export_markdown(chunks_path: Path, export_path: Path, *,
                    include_types: list[str] | None = None,
                    exclude_types: list[str] | None = None,
                    chapters: list[int] | None = None,
                    split_chapters: bool = False,
                    chapters_dir: Optional[Path] = None,
                    format: str = "markdown",
                    cloud_url: str = DEFAULT_CLOUD_URL,
                    cloud_model: str = DEFAULT_CLOUD_MODEL,
                    cloud_key: str = "",
                    ollama_url: str = DEFAULT_OLLAMA_URL,
                    ollama_model: str = DEFAULT_OLLAMA_MODEL,
                    gemini_key: str = "",
                    llm_workers: int = DEFAULT_LLM_WORKERS,
                    thinking: bool = False,
                    security_policy: (
                        _release_security.ReleaseSecurityPolicy | None
                    ) = None) -> None:
    """Produce clean export file(s) optimized for LLM consumption.

    With --format plaintext: writes a .txt + .metadata.json sidecar
    (char offsets for Anthropic citation API).

    With ``split_chapters=True`` (markdown only), writes ``chNN_title.md``
    files (plus ``front_matter.md`` when needed) to ``chapters_dir`` or, by
    default, ``export_path.parent / "Chapters"``. The unified ``export_path``
    file is not written in this mode.

    Without: writes a single combined markdown file.
    """
    if split_chapters and format != "markdown":
        raise ValueError("split_chapters is supported only for markdown exports")

    records, source_sha256, _ = _load_index_snapshot_strict(chunks_path)
    source_record_count = len(records)
    filtered = _filter_chunk_records(
        records,
        include_types=include_types,
        exclude_types=exclude_types,
        chapters=chapters,
    )

    if not filtered:
        log.warning("No chunks to export after filtering.")
        return

    if format == "plaintext":
        _export_plaintext(filtered, export_path)
        return

    if format == "flashcards":
        _export_flashcards(filtered, export_path,
                           cloud_url=cloud_url, cloud_model=cloud_model,
                           cloud_key=cloud_key, ollama_url=ollama_url,
                           ollama_model=ollama_model, gemini_key=gemini_key,
                           llm_workers=llm_workers, thinking=thinking,
                           security_policy=security_policy)
        return

    if split_chapters:
        # Group chunks by chapter
        by_chapter: dict[int | None, list[dict]] = {}
        for rec in filtered:
            ch = rec["metadata"].get("chapter_num")
            by_chapter.setdefault(ch, []).append(rec)

        # Use Chapters/ subdirectory under the export path's parent
        # e.g., output/Constitutional Law/Chapters/
        out_dir = chapters_dir if chapters_dir else (
            export_path.parent / "Chapters")
        _storage_policy.ensure_private_directory(out_dir)

        # Build chapter index for cross-references
        chapter_index: dict[int, str] = {}  # ch_num -> filename
        for ch_num in sorted(by_chapter.keys(), key=lambda x: (x is None, x)):
            ch_chunks = by_chapter[ch_num]
            ch_title = ""
            for rec in ch_chunks:
                t = rec["metadata"].get("chapter_title", "")
                if t:
                    ch_title = t
                    break
            if ch_num is not None:
                safe_title = re.sub(r"[^\w\s-]", "", ch_title).strip()
                safe_title = re.sub(r"\s+", "_", safe_title)[:40]
                filename = f"ch{ch_num:02d}_{safe_title}.md" if safe_title else f"ch{ch_num:02d}.md"
                chapter_index[ch_num] = filename
            else:
                chapter_index[-1] = "front_matter.md"

        total_files = 0
        total_words = 0
        published_files = {}
        for ch_num in sorted(by_chapter.keys(), key=lambda x: (x is None, x)):
            ch_chunks = by_chapter[ch_num]
            ch_title = ""
            for rec in ch_chunks:
                t = rec["metadata"].get("chapter_title", "")
                if t:
                    ch_title = t
                    break

            if ch_num is not None:
                filename = chapter_index[ch_num]
            else:
                filename = "front_matter.md"

            md = _assemble_markdown(ch_chunks)

            # Add cross-reference footer linking to other chapters
            if ch_num is not None:
                # Collect cross-refs found in this chapter's chunks
                xref_chapters = set()
                for rec in ch_chunks:
                    for xref in rec["metadata"].get("cross_references", []):
                        # Normalize built-in profile displays (``Ch.12`` or
                        # ``Part.IV``) back to the integer division contract.
                        m = re.match(
                            r"(?:Ch|Chapter|Part|Unit)\."
                            r"([A-Za-z0-9-]+)", xref, re.I)
                        if m:
                            ref_ch = _document_profiles.parse_division_ordinal(
                                m.group(1), maximum=3999)
                            if ref_ch is None:
                                continue
                            if ref_ch != ch_num and ref_ch in chapter_index:
                                xref_chapters.add(ref_ch)

                if xref_chapters:
                    md += "\n\n---\n\n**Cross-References:**\n"
                    for ref_ch in sorted(xref_chapters):
                        ref_file = chapter_index[ref_ch]
                        ref_title = ""
                        if ref_ch in by_chapter:
                            for rec in by_chapter[ref_ch]:
                                t = rec["metadata"].get("chapter_title", "")
                                if t:
                                    ref_title = t
                                    break
                        label = _display_division_title(ref_ch, ref_title)
                        md += f"- [{label}]({ref_file})\n"

            filepath = out_dir / filename
            _atomic_write_text(filepath, md)
            published_files[filename] = filepath
            wc = len(md.split())
            total_words += wc
            total_files += 1
            log.info(f"  {filename} ({len(ch_chunks)} chunks, {wc:,} words)")

        for prior_path in out_dir.glob("*.md"):
            if (_owned_chapter_filename(prior_path.name)
                    and prior_path.name not in published_files):
                prior_path.unlink()
        parameters = _markdown_export_parameters(
            include_types=include_types, exclude_types=exclude_types,
            chapters=chapters, split_chapters=True)
        _write_artifact_completion(
            _artifact_completion_path(out_dir, stage="split_export"),
            stage="split_export", source_sha256=source_sha256,
            source_record_count=source_record_count, parameters=parameters,
            outputs=published_files)
        log.info(f"Exported {total_files} chapter files -> {out_dir}/ "
                 f"({total_words:,} words total)")
        log.info(f"Upload the entire {out_dir.parent}/ folder to Claude Projects.")
    else:
        # Single combined file
        output = _assemble_markdown(filtered)
        _atomic_write_text(export_path, output)
        parameters = _markdown_export_parameters(
            include_types=include_types, exclude_types=exclude_types,
            chapters=chapters, split_chapters=False)
        _write_artifact_completion(
            _artifact_completion_path(export_path, stage="unified_export"),
            stage="unified_export", source_sha256=source_sha256,
            source_record_count=source_record_count, parameters=parameters,
            outputs={"markdown": export_path})
        word_count = len(output.split())
        log.info(f"Exported {len(filtered)} chunks -> {export_path} "
                 f"({len(output) / 1e6:.1f} MB, {word_count:,} words)")


# ---------------------------------------------------------------------------
# Step 6: Question Extraction
# ---------------------------------------------------------------------------

# Splits on numbered questions: "1.", "2.", "(a)", "(b)", etc.
_QUESTION_SPLIT_RE = re.compile(r"(?:^|\n)\s*(?:\d{1,2}\.\s+|\([a-z]\)\s+)")


def extract_questions(chunks_path: Path, output_path: Path) -> None:
    """Extract individual questions from Notes & Questions chunks.

    Produces a JSONL file of question/context pairs suitable for
    flashcard generation, fine-tuning, or evaluation harnesses.
    """
    _require_file(chunks_path, "Chunks JSONL")
    all_chunks, _, _ = _load_index_snapshot_strict(chunks_path)
    all_chunks = _table_retrieval_core.canonical_records(all_chunks)
    chunk_by_idx = {r["metadata"]["chunk_index"]: r for r in all_chunks}

    # Filter to notes_and_questions
    records = [r for r in all_chunks
               if r["metadata"]["content_type"] == "notes_and_questions"]

    if not records:
        log.warning("No notes_and_questions chunks found.")
        return

    questions = []
    for rec in records:
        text = rec["text"]
        meta = rec["metadata"]
        idx = meta["chunk_index"]

        # Split into individual questions
        parts = _QUESTION_SPLIT_RE.split(text)
        # First part is usually preamble (heading text), skip if short
        for part in parts:
            part = part.strip()
            if len(part) < 30:
                continue
            # Only keep parts that look like questions (end with ?)
            # or are substantive discussion prompts
            if "?" in part or len(part) > 80:
                # Walk backward to find the nearest non-N&Q context chunk
                context_chunk = None
                for walk_idx in range(idx - 1, max(idx - 10, -1), -1):
                    candidate = chunk_by_idx.get(walk_idx)
                    if candidate and candidate["metadata"]["content_type"] != "notes_and_questions":
                        context_chunk = candidate
                        break
                context_text = context_chunk["text"][:500] if context_chunk else ""
                context_case = (context_chunk["metadata"].get("primary_case", "")
                                if context_chunk else "")

                questions.append({
                    "question": part,
                    "source_chunk_index": idx,
                    "context_chunk_index": context_chunk["metadata"]["chunk_index"] if context_chunk else None,
                    "context_text": context_text,
                    "context_case": context_case,
                    "chapter_num": meta.get("chapter_num"),
                    "chapter_title": meta.get("chapter_title", ""),
                    "section_path": meta.get("section_path", ""),
                })

    _atomic_write_jsonl(output_path, questions)

    log.info(f"Extracted {len(questions)} questions from "
             f"{len(records)} N&Q chunks -> {output_path}")


# ---------------------------------------------------------------------------
# Step 6b: Exam Question Generation (via LLM)
# ---------------------------------------------------------------------------

_EXAM_PROMPT = """You are a law school professor. Based on these passages from a Civil Procedure textbook,
generate 5 exam-style questions. Include:
- 2 issue-spotter hypotheticals (short fact patterns requiring analysis)
- 2 doctrinal questions (testing understanding of rules and standards)
- 1 policy question (asking about rationale behind the doctrine)

For each question, provide a brief suggested answer outline (2-3 bullet points).

Format each question as:
[TYPE] (issue_spotter / doctrinal / policy)
Q: <question text>
A:
- <bullet 1>
- <bullet 2>
- <bullet 3>
---

Chapter: {chapter}
Topic: {topic}

Passages:
{passages}

Questions:"""


def generate_exam_questions(chunks_path: Path, output_path: Path, *,
                            cloud_url: str = DEFAULT_CLOUD_URL,
                            cloud_model: str = DEFAULT_CLOUD_MODEL,
                            cloud_key: str = "",
                            ollama_url: str = DEFAULT_OLLAMA_URL,
                            ollama_model: str = DEFAULT_OLLAMA_MODEL,
                            gemini_key: str = "",
                            llm_workers: int = DEFAULT_LLM_WORKERS,
                            thinking: bool = False,
                            security_policy: (
                                _release_security.ReleaseSecurityPolicy | None
                            ) = None) -> None:
    """Generate exam-style questions from chapter chunks via LLM.

    Groups chunks by chapter, selects representative passages (mix of
    case_opinion + author_narrative), sends to LLM with the exam prompt,
    and writes structured JSONL output.
    """
    from concurrent.futures import ThreadPoolExecutor, as_completed

    _require_file(chunks_path, "Chunks JSONL")
    all_chunks, _, _ = _load_index_snapshot_strict(chunks_path)
    all_chunks = _table_retrieval_core.canonical_records(all_chunks)

    by_chapter: dict[int, list[dict]] = {}
    for rec in all_chunks:
        ch = rec["metadata"].get("chapter_num")
        if ch is not None:
            by_chapter.setdefault(ch, []).append(rec)

    if not by_chapter:
        log.warning("No chapters found in chunks. Cannot generate questions.")
        return

    log.info(f"Generating exam questions for {len(by_chapter)} chapters")

    llm_kwargs = dict(
        ollama_url=ollama_url, ollama_model=ollama_model,
        gemini_key=gemini_key, cloud_url=cloud_url,
        cloud_model=cloud_model, cloud_key=cloud_key,
        llm_workers=llm_workers, thinking=thinking,
        security_policy=security_policy,
    )

    def _generate_for_chapter(ch_num, ch_chunks):
        preferred_types = ("case_opinion", "author_narrative")
        preferred = [c for c in ch_chunks
                     if c["metadata"].get("content_type") in preferred_types]
        other = [c for c in ch_chunks
                 if c["metadata"].get("content_type") not in preferred_types]
        selected = preferred[:7] + other[:3]
        if len(selected) < 10 and len(preferred) > 7:
            selected = preferred[:10]
        selected = selected[:10]
        if not selected:
            return []

        ch_title = selected[0]["metadata"].get("chapter_title", f"Chapter {ch_num}")
        topic = selected[0]["metadata"].get("section_path", ch_title)
        passages = "\n\n---\n\n".join(c["text"][:600] for c in selected)
        prompt = _EXAM_PROMPT.format(
            chapter=f"Chapter {ch_num}: {ch_title}",
            topic=topic, passages=passages)

        result = _call_llm(
            prompt, operation="study.exam_questions", **llm_kwargs)
        if not result:
            log.warning(f"LLM returned no response for Chapter {ch_num}")
            return []
        result = _THINK_TAG_RE.sub("", result).strip()

        questions = []
        raw_questions = re.split(r"\n---\s*\n", result)
        for raw_q in raw_questions:
            raw_q = raw_q.strip()
            if not raw_q:
                continue
            q_type = "unknown"
            type_match = re.search(
                r"\[(issue_spotter|doctrinal|policy)\]", raw_q, re.IGNORECASE)
            if type_match:
                q_type = type_match.group(1).lower()
            q_match = re.search(r"Q:\s*(.+?)(?=\nA:|\Z)", raw_q, re.DOTALL)
            question_text = q_match.group(1).strip() if q_match else raw_q
            a_match = re.search(r"A:\s*(.+)", raw_q, re.DOTALL)
            answer_text = a_match.group(1).strip() if a_match else ""
            if len(question_text) < 20:
                continue
            questions.append({
                "question": question_text,
                "question_type": q_type,
                "chapter_num": ch_num,
                "chapter_title": ch_title,
                "suggested_answer": answer_text,
                "source_chunks": [c["metadata"]["chunk_index"] for c in selected],
            })
        return questions

    all_questions = []
    is_cloud = bool(cloud_url and cloud_key)
    workers = min(llm_workers, len(by_chapter)) if is_cloud else 1

    if workers > 1:
        with ThreadPoolExecutor(max_workers=workers) as pool:
            futures = {
                pool.submit(_generate_for_chapter, ch_num, ch_chunks): ch_num
                for ch_num, ch_chunks in sorted(by_chapter.items())
            }
            for future in as_completed(futures):
                ch_num = futures[future]
                try:
                    qs = future.result()
                    all_questions.extend(qs)
                    log.info(f"  Chapter {ch_num}: {len(qs)} questions")
                except (LLMBudgetExceeded, LLMExecutionError):
                    raise
                except Exception as e:
                    log.warning(f"  Chapter {ch_num} failed: {e}")
    else:
        for ch_num, ch_chunks in sorted(by_chapter.items()):
            qs = _generate_for_chapter(ch_num, ch_chunks)
            all_questions.extend(qs)
            log.info(f"  Chapter {ch_num}: {len(qs)} questions")

    _atomic_write_jsonl(output_path, all_questions)

    log.info(f"Generated {len(all_questions)} exam questions from "
             f"{len(by_chapter)} chapters -> {output_path}")


# ---------------------------------------------------------------------------
# Step 7: Citation Graph
# ---------------------------------------------------------------------------

# Case citation: "International Shoe Co. v. Washington, 326 U.S. 310 (1945)"
_FULL_CITE_RE = re.compile(
    r"((?:(?:In re|Ex parte)\s+)?[A-Z][A-Za-z\'\-\.]+(?:\s+[A-Za-z\'\-\.]+)*"
    r"\s+v\.\s+"
    r"[A-Z][A-Za-z\'\-\.]+(?:\s+[A-Za-z\'\-\.,]+)*)"
    r"(?:,\s*(\d{1,3}\s+[A-Z][A-Za-z\.\s]+\s+\d{1,4}))?"  # reporter cite
    r"(?:\s*\((\d{4})\))?"  # year
)
# Short-form: "Shoe, supra", "Burger King, supra at 462"
_SUPRA_RE = re.compile(r"(\b[A-Z][A-Za-z\'\-\.]+(?:\s+[A-Z][A-Za-z\'\-\.]+)?),?\s+supra")
# Statute: "28 U.S.C. § 1332", "Fed. R. Civ. P. 12"
_STATUTE_RE = re.compile(
    r"(\d{1,2}\s+U\.S\.C\.\s*(?:§+\s*)?\d{1,5}(?:\([a-z]\))?|"
    r"Fed\.\s*R\.\s*Civ\.\s*P\.\s*\d{1,3}(?:\([a-z]\))?|"
    r"Rule\s+\d{1,3}(?:\([a-z]\))?)"
)


def build_citation_graph(chunks_path: Path, output_path: Path) -> None:
    """Parse cross-references from all chunks into a citation graph.

    Output JSON with nodes (chunks, cases, statutes) and edges (citations).
    """
    records = _load_and_filter_chunks(chunks_path)

    nodes = {}  # id -> {type, label, ...}
    edges = []  # {source, target, type}

    # Add all chunks as nodes (using stable content-hash IDs)
    for rec in records:
        cid = _chunk_id(rec)
        idx = rec["metadata"]["chunk_index"]
        nodes[cid] = {
            "id": cid,
            "type": "chunk",
            "label": rec["metadata"].get("section_path", f"Chunk {idx}")[:80],
            "chapter": rec["metadata"].get("chapter_num"),
            "content_type": rec["metadata"]["content_type"],
        }

    all_cases = {}   # normalized name -> first chunk seen
    all_statutes = {}  # statute text -> first chunk seen

    for rec in records:
        text = rec["text"]
        chunk_id = _chunk_id(rec)

        # Full case citations
        for m in _FULL_CITE_RE.finditer(text):
            case_name = m.group(1).strip().rstrip(".,;")
            if len(case_name) < 8:
                continue
            case_id = re.sub(r"[^a-z0-9]", "_", case_name.lower())

            if case_id not in nodes:
                reporter = m.group(2).strip() if m.group(2) else ""
                year = m.group(3) if m.group(3) else ""
                nodes[case_id] = {
                    "id": case_id,
                    "type": "case",
                    "label": case_name,
                    "reporter": reporter,
                    "year": year,
                }
                all_cases[case_id] = idx

            edges.append({
                "source": chunk_id,
                "target": case_id,
                "type": "cites_case",
            })

        # Supra references (short-form)
        for m in _SUPRA_RE.finditer(text):
            short_name = m.group(1).strip()
            # Try to match to a known case
            short_id = re.sub(r"[^a-z0-9]", "_", short_name.lower())
            matched = None
            for cid in all_cases:
                if short_id in cid:
                    matched = cid
                    break
            if matched:
                edges.append({
                    "source": chunk_id,
                    "target": matched,
                    "type": "cites_supra",
                })

        # Statute citations
        for m in _STATUTE_RE.finditer(text):
            statute = m.group(1).strip()
            stat_id = re.sub(r"[^a-z0-9]", "_", statute.lower())

            if stat_id not in nodes:
                nodes[stat_id] = {
                    "id": stat_id,
                    "type": "statute",
                    "label": statute,
                }
                all_statutes[stat_id] = idx

            edges.append({
                "source": chunk_id,
                "target": stat_id,
                "type": "cites_statute",
            })

        # Cross-chapter references (from existing metadata)
        for xref in rec["metadata"].get("cross_references", []):
            if xref not in nodes:
                nodes[xref] = {"id": xref, "type": "chapter_ref", "label": xref}
            edges.append({
                "source": chunk_id,
                "target": xref,
                "type": "cross_reference",
            })

    # Deduplicate edges
    seen_edges = set()
    unique_edges = []
    for e in edges:
        key = (e["source"], e["target"], e["type"])
        if key not in seen_edges:
            seen_edges.add(key)
            unique_edges.append(e)

    graph = {
        "nodes": list(nodes.values()),
        "edges": unique_edges,
        "stats": {
            "total_nodes": len(nodes),
            "chunk_nodes": sum(1 for n in nodes.values() if n["type"] == "chunk"),
            "case_nodes": sum(1 for n in nodes.values() if n["type"] == "case"),
            "statute_nodes": sum(1 for n in nodes.values() if n["type"] == "statute"),
            "total_edges": len(unique_edges),
        },
    }

    _atomic_write_text(
        output_path, json.dumps(graph, indent=2, ensure_ascii=False))

    s = graph["stats"]
    log.info(f"Citation graph -> {output_path}")
    log.info(f"  {s['chunk_nodes']} chunks, {s['case_nodes']} cases, "
             f"{s['statute_nodes']} statutes, {s['total_edges']} edges")


# ---------------------------------------------------------------------------
# Step 8b: Case Brief Generation
# ---------------------------------------------------------------------------

_BRIEF_PROMPT = """Generate a case brief for this judicial opinion from a law textbook.
Format your response as:
FACTS: [key facts in 2-3 sentences]
ISSUE: [the legal question presented]
HOLDING: [the court's decision]
REASONING: [the court's legal reasoning in 2-3 sentences]

Opinion text:
{text}

Case brief:"""


def generate_briefs(chunks_path: Path, output_path: Path, *,
                    cloud_url: str = DEFAULT_CLOUD_URL,
                    cloud_model: str = DEFAULT_CLOUD_MODEL,
                    cloud_key: str = "",
                    ollama_url: str = DEFAULT_OLLAMA_URL,
                    ollama_model: str = DEFAULT_OLLAMA_MODEL,
                    gemini_key: str = "",
                    llm_workers: int = DEFAULT_LLM_WORKERS,
                    thinking: bool = False,
                    security_policy: (
                        _release_security.ReleaseSecurityPolicy | None
                    ) = None) -> None:
    """Generate case briefs for all case_opinion chunks via LLM."""
    from concurrent.futures import ThreadPoolExecutor, as_completed
    from tqdm import tqdm

    _require_file(chunks_path, "Chunks JSONL")
    records, _, _ = _load_index_snapshot_strict(chunks_path)
    records = _table_retrieval_core.canonical_records(records)

    # Filter to case opinions only
    case_chunks = [r for r in records
                   if r.get("metadata", {}).get("content_type") == "case_opinion"]

    if not case_chunks:
        log.warning("No case_opinion chunks found — nothing to brief.")
        return

    log.info(f"Generating briefs for {len(case_chunks)} case opinion chunks...")

    llm_kwargs = dict(ollama_url=ollama_url, ollama_model=ollama_model,
                      gemini_key=gemini_key, cloud_url=cloud_url,
                      cloud_model=cloud_model, cloud_key=cloud_key,
                      llm_workers=llm_workers, thinking=thinking,
                      security_policy=security_policy)

    def _parse_brief(response: str) -> dict:
        """Parse a brief response into structured fields."""
        fields = {"facts": "", "issue": "", "holding": "", "reasoning": ""}
        if not response:
            return fields
        # Clean think tags
        response = _THINK_TAG_RE.sub("", response).strip()
        current_key = None
        current_lines: list[str] = []
        for line in response.split("\n"):
            stripped = line.strip()
            matched = False
            for key in fields:
                if stripped.upper().startswith(key.upper() + ":"):
                    if current_key:
                        fields[current_key] = " ".join(current_lines).strip()
                    current_key = key
                    current_lines = [stripped[len(key) + 1:].strip()]
                    matched = True
                    break
            if not matched and current_key:
                current_lines.append(stripped)
        if current_key:
            fields[current_key] = " ".join(current_lines).strip()
        return fields

    def _brief_one(rec: dict) -> Optional[dict]:
        text = rec["text"]
        meta = rec["metadata"]
        prompt = _BRIEF_PROMPT.format(text=text[:3000])
        result = _call_llm(prompt, operation="study.case_brief", **llm_kwargs)
        if not result:
            return None
        parsed = _parse_brief(result)
        return {
            "case_name": meta.get("primary_case", "Unknown Case"),
            "facts": parsed["facts"],
            "issue": parsed["issue"],
            "holding": parsed["holding"],
            "reasoning": parsed["reasoning"],
            "page_range": meta.get("page_range", ""),
            "chapter_num": meta.get("chapter_num"),
        }

    briefs: list[dict] = []

    with ThreadPoolExecutor(max_workers=llm_workers) as pool:
        futures = {pool.submit(_brief_one, rec): i
                   for i, rec in enumerate(case_chunks)}
        for future in tqdm(as_completed(futures), total=len(futures),
                           desc="Briefing cases"):
            result = future.result()
            if result:
                briefs.append(result)

    # Sort by chapter then page for stable output
    briefs.sort(key=lambda b: (b.get("chapter_num") or 0, b.get("page_range", "")))

    _atomic_write_jsonl(output_path, briefs)

    log.info(f"Generated {len(briefs)} case briefs -> {output_path}")


# ---------------------------------------------------------------------------
# Step 7c: Chunk Quality Scoring
# ---------------------------------------------------------------------------

_QUALITY_PROMPT = """Rate this text from a law textbook for its usefulness in answering law school exam questions.
Score 1-5:
1 = Procedural noise, boilerplate, or formatting artifacts
2 = Background context with minimal legal content
3 = Useful explanatory text about legal concepts
4 = Important doctrinal analysis, key rules, or significant case discussion
5 = Essential doctrine: landmark holdings, foundational rules, or critical analysis

Text (first 500 chars):
{text}

Content type: {content_type}

Reply with ONLY the number (1-5):"""


def _score_chunk_quality(text: str, content_type: str, **llm_kwargs) -> int:
    """Rate a chunk's usefulness via LLM. Returns 1-5 (default 3 on failure)."""
    prompt = _QUALITY_PROMPT.format(text=text[:500], content_type=content_type)
    result = _call_llm(prompt, operation="chunk.quality", **llm_kwargs)
    if result:
        result = _THINK_TAG_RE.sub("", result).strip()
        for ch in result:
            if ch.isdigit() and 1 <= int(ch) <= 5:
                return int(ch)
    return 3  # default: useful


# ---------------------------------------------------------------------------
# Step 7d: Flashcard Export
# ---------------------------------------------------------------------------

_FLASHCARD_PROMPT = """Create a study flashcard from this law textbook passage.
Front: A clear question testing understanding of the legal concept.
Back: A concise answer (2-3 sentences) with the key rule or holding.

Passage ({content_type}, {page_range}):
{text}

Format your response exactly as:
Q: [question]
A: [answer]"""


def _export_flashcards(chunks: list[dict], export_path: Path, *,
                       cloud_url: str = DEFAULT_CLOUD_URL,
                       cloud_model: str = DEFAULT_CLOUD_MODEL,
                       cloud_key: str = "",
                       ollama_url: str = DEFAULT_OLLAMA_URL,
                       ollama_model: str = DEFAULT_OLLAMA_MODEL,
                       gemini_key: str = "",
                       llm_workers: int = DEFAULT_LLM_WORKERS,
                       thinking: bool = False,
                       security_policy: (
                           _release_security.ReleaseSecurityPolicy | None
                       ) = None) -> None:
    """Generate Anki-compatible flashcards from chunks via LLM."""
    from concurrent.futures import ThreadPoolExecutor, as_completed
    from tqdm import tqdm

    is_cloud = bool(cloud_url and cloud_key)
    workers = llm_workers if is_cloud else 1
    llm_kwargs = dict(ollama_url=ollama_url, ollama_model=ollama_model,
                      gemini_key=gemini_key, cloud_url=cloud_url,
                      cloud_model=cloud_model, cloud_key=cloud_key,
                      llm_workers=llm_workers, thinking=thinking,
                      security_policy=security_policy)

    def _generate_card(rec):
        meta = rec["metadata"]
        prompt = _FLASHCARD_PROMPT.format(
            text=rec["text"][:800],
            content_type=meta.get("content_type", ""),
            page_range=meta.get("page_range", ""),
        )
        result = _call_llm(prompt, operation="study.flashcard", **llm_kwargs)
        if not result:
            return None
        result = _THINK_TAG_RE.sub("", result).strip()
        # Parse Q: / A: format
        q, a = "", ""
        for line in result.split("\n"):
            line = line.strip()
            if line.startswith("Q:"):
                q = line[2:].strip()
            elif line.startswith("A:"):
                a = line[2:].strip()
        if not q or not a:
            return None
        # Build tags from metadata
        tags = []
        ch = meta.get("chapter_num")
        if ch is not None:
            tags.append(f"ch{ch}")
        ct = meta.get("content_type", "")
        if ct:
            tags.append(ct)
        case = meta.get("primary_case", "")
        if case:
            tags.append(case.replace(" ", "_")[:30])
        return {"front": q, "back": a, "tags": tags, "metadata": {
            "chunk_index": meta.get("chunk_index"),
            "page_range": meta.get("page_range", ""),
            "chapter_num": ch,
            "content_type": ct,
        }}

    log.info(f"Generating flashcards from {len(chunks)} chunks "
             f"({workers} workers)...")
    cards = []
    with ThreadPoolExecutor(max_workers=workers) as pool:
        futures = {pool.submit(_generate_card, rec): rec for rec in chunks}
        for future in tqdm(as_completed(futures), total=len(futures),
                           desc="Flashcards", unit="card"):
            card = future.result()
            if card:
                cards.append(card)

    if not cards:
        log.warning("No flashcards generated.")
        return

    # Write TSV (Anki import format)
    tsv_path = export_path.with_suffix(".tsv")
    tsv_lines = []
    for card in cards:
        tags_str = " ".join(card["tags"])
        tsv_lines.append(
            f"{card['front']}\t{card['back']}\t{tags_str}\n")
    _atomic_write_text(tsv_path, "".join(tsv_lines))

    # Write JSON sidecar with full metadata
    json_path = export_path.with_suffix(".flashcards.json")
    _atomic_write_text(
        json_path, json.dumps(cards, indent=2, ensure_ascii=False))

    log.info(f"Generated {len(cards)} flashcards")
    log.info(f"  Anki TSV:  {tsv_path}")
    log.info(f"  Full JSON: {json_path}")


# ---------------------------------------------------------------------------
# Step 8: RAPTOR — Recursive Abstractive Processing for Tree-Organized Retrieval
# ---------------------------------------------------------------------------

_RAPTOR_SUMMARY_PROMPT = """Summarize these {n} passages from a law school textbook.
Focus on: the legal concepts discussed, key case names and holdings, doctrinal
rules, and statutory provisions. Write 2-3 concise sentences.

Passages:
{texts}

Summary (2-3 sentences):"""


def _cluster_embeddings(embeddings: list[list[float]], k: int) -> list[list[int]]:
    """K-means clustering on embeddings. Returns list of index groups."""
    import numpy as np

    embs = np.array(embeddings)
    n = len(embs)
    if n == 0:
        return []
    k = min(k, n)
    if k <= 1:
        return [list(range(n))]

    # Simple k-means (sklearn-free)
    rng = np.random.default_rng(42)
    # Initialize centroids with k-means++
    centroids = [embs[rng.integers(n)]]
    for _ in range(k - 1):
        dists = np.min([np.sum((embs - c) ** 2, axis=1) for c in centroids], axis=0)
        total_distance = dists.sum()
        if not np.isfinite(total_distance) or total_distance <= 0:
            # All remaining points coincide with an existing centroid. Any
            # point is a valid deterministic fallback; k-means will collapse
            # duplicate centroids into a single non-empty cluster below.
            centroids.append(embs[len(centroids) % n])
        else:
            probs = dists / total_distance
            centroids.append(embs[rng.choice(n, p=probs)])
    centroids = np.array(centroids)

    for _ in range(20):  # max iterations
        # Assign points to nearest centroid
        dists = np.array([np.sum((embs - c) ** 2, axis=1) for c in centroids])
        labels = np.argmin(dists, axis=0)
        # Update centroids
        new_centroids = np.array([
            embs[labels == i].mean(axis=0) if (labels == i).any() else centroids[i]
            for i in range(k)
        ])
        if np.allclose(centroids, new_centroids, atol=1e-6):
            break
        centroids = new_centroids

    clusters: list[list[int]] = [[] for _ in range(k)]
    for idx, label in enumerate(labels):
        clusters[label].append(idx)
    return [c for c in clusters if c]  # remove empty clusters


def _raptor_parameters(*, embedding_model: str, cloud_url: str,
                       cloud_model: str, cloud_key: str,
                       ollama_url: str, ollama_model: str,
                       gemini_key: str, thinking: bool,
                       security_policy: (
                           _release_security.ReleaseSecurityPolicy | None
                       ) = None) -> dict:
    policy = _effective_security_policy(security_policy)
    gemini_configured = bool(
        policy.network_policy == "allow-cloud"
        and (gemini_key or "GEMINI_API_KEY" in os.environ))
    return {
        "embedding_model": embedding_model,
        "cloud_url": _endpoint_parameter_binding(cloud_url),
        "cloud_model": cloud_model,
        "cloud_configured": bool(cloud_url and cloud_key),
        "ollama_url": _endpoint_parameter_binding(ollama_url),
        "ollama_model": ollama_model,
        "gemini_configured": gemini_configured,
        "thinking": thinking,
        "prompt_version": 1,
        "release_security": policy.provenance(),
    }


def _raptor_output_complete(chunks_path: Path, output_path: Path, *,
                            parameters: dict) -> bool:
    try:
        source_records, source_sha256, _ = _load_index_snapshot_strict(
            chunks_path)
    except (OSError, UnicodeError, ValueError, RuntimeError):
        return False
    source_count = len(
        _table_retrieval_core.canonical_records(source_records))
    try:
        tree = json.loads(output_path.read_text(encoding="utf-8"))
        if not isinstance(tree, dict):
            return False
        expected = {
            "schema_version": ARTIFACT_COMPLETION_SCHEMA_VERSION,
            "source_sha256": source_sha256,
            "source_record_count": source_count,
            "parameters_sha256": _artifact_parameters_sha256(parameters),
        }
        if any(tree.get(key) != value for key, value in expected.items()):
            return False
        levels = tree.get("levels")
        nodes = tree.get("nodes")
        stats = tree.get("stats")
        if levels not in {2, 3} or not isinstance(nodes, list) or not nodes:
            return False
        if not isinstance(stats, dict):
            return False
        node_ids = []
        level_counts = {0: 0, 1: 0, 2: 0}
        for node in nodes:
            if not isinstance(node, dict):
                return False
            node_id = node.get("node_id")
            level = node.get("level")
            if (not isinstance(node_id, str) or not node_id
                    or level not in level_counts
                    or not isinstance(node.get("children"), list)):
                return False
            node_ids.append(node_id)
            level_counts[level] += 1
        if len(node_ids) != len(set(node_ids)):
            return False
        known_ids = set(node_ids)
        node_levels = {node["node_id"]: node["level"] for node in nodes}
        if any(child not in known_ids for node in nodes
               for child in node["children"]):
            return False
        if any(
                (node["level"] == 0 and node["children"])
                or any(node_levels[child] != node["level"] - 1
                       for child in node["children"])
                for node in nodes):
            return False
        if ((levels == 2 and level_counts[2] != 0)
                or (levels == 3 and level_counts[2] == 0)):
            return False
        return (
            level_counts[0] == source_count
            and stats.get("level_0") == level_counts[0]
            and stats.get("level_1") == level_counts[1]
            and stats.get("level_2") == level_counts[2]
            and stats.get("total") == len(nodes)
        )
    except (OSError, UnicodeError, json.JSONDecodeError, TypeError):
        return False


def build_raptor_tree(chunks_path: Path, output_path: Path, *,
                      embedding_model: str = DEFAULT_EMBEDDING_MODEL,
                      cloud_url: str = DEFAULT_CLOUD_URL,
                      cloud_model: str = DEFAULT_CLOUD_MODEL,
                      cloud_key: str = "",
                      ollama_url: str = DEFAULT_OLLAMA_URL,
                      ollama_model: str = DEFAULT_OLLAMA_MODEL,
                      gemini_key: str = "",
                      llm_workers: int = DEFAULT_LLM_WORKERS,
                      thinking: bool = False,
                      security_policy: (
                          _release_security.ReleaseSecurityPolicy | None
                      ) = None) -> None:
    """Build a 3-level RAPTOR tree over chunks.

    Level 0: Raw chunks (from JSONL)
    Level 1: Section summaries (clusters of ~5-10 chunks)
    Level 2: Chapter summaries (clusters of Level 1 nodes)

    Output is a JSON file with all tree nodes. Indexing those summaries is a
    separate concern; this command does not mutate an existing vector index.
    """
    from concurrent.futures import ThreadPoolExecutor, as_completed
    from tqdm import tqdm

    records, source_sha256, _ = _load_index_snapshot_strict(chunks_path)
    records = _table_retrieval_core.canonical_records(records)
    source_record_count = len(records)
    parameters = _raptor_parameters(
        embedding_model=embedding_model, cloud_url=cloud_url,
        cloud_model=cloud_model, cloud_key=cloud_key,
        ollama_url=ollama_url, ollama_model=ollama_model,
        gemini_key=gemini_key, thinking=thinking,
        security_policy=security_policy)
    log.info(f"RAPTOR: building tree over {len(records)} chunks")

    if not records:
        log.warning("No chunks to build RAPTOR tree from.")
        return

    is_cloud = bool(cloud_url and cloud_key)
    llm_kwargs = dict(ollama_url=ollama_url, ollama_model=ollama_model,
                      gemini_key=gemini_key, cloud_url=cloud_url,
                      cloud_model=cloud_model, cloud_key=cloud_key,
                      llm_workers=llm_workers, thinking=thinking,
                      security_policy=security_policy)

    def _summarize(texts: list[str]) -> str:
        """Summarize a cluster of texts via LLM."""
        combined = "\n\n---\n\n".join(t[:500] for t in texts)  # cap per-text
        prompt = _RAPTOR_SUMMARY_PROMPT.format(n=len(texts), texts=combined[:3000])
        result = _call_llm(prompt, operation="raptor.summarize", **llm_kwargs)
        if result:
            result = _THINK_TAG_RE.sub("", result).strip()
            sentences = re.split(r"(?<=[.!?])\s+", result)
            return " ".join(sentences[:3])
        return ""

    # --- Level 0: Raw chunks (already have embeddings implicitly) ---
    log.info("RAPTOR Level 0: Embedding chunks...")
    texts_l0 = [r["text"] for r in records]
    try:
        embeddings_l0 = _embed_texts(
            texts_l0, embedding_model, security_policy=security_policy)
    except Exception as e:
        log.error(f"RAPTOR embedding failed: {e}")
        log.error("  Check --embedding-model and API keys.")
        raise RuntimeError("RAPTOR embedding failed") from e

    tree_nodes = []
    # Add level 0 nodes
    for i, r in enumerate(records):
        tree_nodes.append({
            "text": r["text"],
            "level": 0,
            "is_summary": False,
            "node_id": f"L0_{i}",
            "children": [],
            "metadata": r["metadata"],
        })

    # --- Level 1: Section summaries (cluster into ~5-10 chunks each) ---
    # Target ~60-80 clusters for a textbook with ~275 chunks
    n_clusters_l1 = max(5, len(records) // 5)
    log.info(f"RAPTOR Level 1: Clustering {len(records)} chunks "
             f"into ~{n_clusters_l1} sections...")
    clusters_l1 = _cluster_embeddings(embeddings_l0, n_clusters_l1)
    log.info(f"  Formed {len(clusters_l1)} clusters")

    # Summarize each cluster (parallel for cloud, sequential for local)
    log.info(f"RAPTOR Level 1: Summarizing {len(clusters_l1)} sections...")
    texts_l1 = []
    metas_l1 = []
    workers = llm_workers if is_cloud else 1

    def _summarize_cluster(cluster_indices):
        cluster_texts = [texts_l0[i] for i in cluster_indices]
        summary = _summarize(cluster_texts)
        # Inherit chapter from majority of cluster members
        ch_counts: dict = {}
        for i in cluster_indices:
            ch = records[i]["metadata"].get("chapter_num")
            if ch is not None:
                ch_counts[ch] = ch_counts.get(ch, 0) + 1
        majority_ch = max(ch_counts, key=ch_counts.get) if ch_counts else None
        ch_title = ""
        if majority_ch is not None:
            for i in cluster_indices:
                t = records[i]["metadata"].get("chapter_title", "")
                if t and records[i]["metadata"].get("chapter_num") == majority_ch:
                    ch_title = t
                    break
        return summary, cluster_indices, majority_ch, ch_title

    with ThreadPoolExecutor(max_workers=workers) as pool:
        futures = [pool.submit(_summarize_cluster, c) for c in clusters_l1]
        for future in tqdm(as_completed(futures), total=len(futures),
                           desc="Level 1 summaries", unit="cluster"):
            summary, indices, ch_num, ch_title = future.result()
            if summary:
                node_id = f"L1_{len(texts_l1)}"
                texts_l1.append(summary)
                metas_l1.append({
                    "chapter_num": ch_num,
                    "chapter_title": ch_title,
                    "content_type": "raptor_summary",
                    "content_source": "summary",
                })
                tree_nodes.append({
                    "text": summary,
                    "level": 1,
                    "is_summary": True,
                    "node_id": node_id,
                    "children": [f"L0_{i}" for i in indices],
                    "metadata": metas_l1[-1],
                })

    log.info(f"  Generated {len(texts_l1)} section summaries")

    if not texts_l1:
        raise RuntimeError(
            "RAPTOR produced no summaries; check LLM connectivity")

    # --- Level 2: Chapter summaries (cluster Level 1 nodes) ---
    log.info("RAPTOR Level 2: Embedding section summaries...")
    try:
        embeddings_l1 = _embed_texts(
            texts_l1, embedding_model, security_policy=security_policy)
    except Exception as e:
        log.error(f"RAPTOR Level 2 embedding failed: {e}")
        log.error("  Saving partial tree (Level 0 + Level 1 only).")
        # Save what we have
        tree = {
                "schema_version": ARTIFACT_COMPLETION_SCHEMA_VERSION,
                "source_sha256": source_sha256,
                "source_record_count": source_record_count,
                "parameters_sha256": _artifact_parameters_sha256(parameters),
                "levels": 2, "nodes": tree_nodes,
                "stats": {"level_0": sum(1 for n in tree_nodes if n["level"] == 0),
                           "level_1": sum(1 for n in tree_nodes if n["level"] == 1),
                           "level_2": 0, "total": len(tree_nodes)}}
        _atomic_write_json(output_path, tree)
        return

    # Target ~15 chapter-level clusters
    n_clusters_l2 = max(3, len(texts_l1) // 4)
    log.info(f"RAPTOR Level 2: Clustering {len(texts_l1)} sections "
             f"into ~{n_clusters_l2} chapters...")
    clusters_l2 = _cluster_embeddings(embeddings_l1, n_clusters_l2)

    log.info(f"RAPTOR Level 2: Summarizing {len(clusters_l2)} chapter overviews...")
    texts_l2 = []

    with ThreadPoolExecutor(max_workers=workers) as pool:
        def _summarize_l2(cluster_indices):
            cluster_texts = [texts_l1[i] for i in cluster_indices]
            summary = _summarize(cluster_texts)
            ch_counts: dict = {}
            for i in cluster_indices:
                ch = metas_l1[i].get("chapter_num")
                if ch is not None:
                    ch_counts[ch] = ch_counts.get(ch, 0) + 1
            majority_ch = max(ch_counts, key=ch_counts.get) if ch_counts else None
            ch_title = metas_l1[cluster_indices[0]].get("chapter_title", "") if cluster_indices else ""
            return summary, cluster_indices, majority_ch, ch_title

        futures = [pool.submit(_summarize_l2, c) for c in clusters_l2]
        for future in tqdm(as_completed(futures), total=len(futures),
                           desc="Level 2 summaries", unit="cluster"):
            summary, indices, ch_num, ch_title = future.result()
            if summary:
                node_id = f"L2_{len(texts_l2)}"
                texts_l2.append(summary)
                tree_nodes.append({
                    "text": summary,
                    "level": 2,
                    "is_summary": True,
                    "node_id": node_id,
                    "children": [f"L1_{i}" for i in indices],
                    "metadata": {
                        "chapter_num": ch_num,
                        "chapter_title": ch_title,
                        "content_type": "raptor_summary",
                        "content_source": "summary",
                    },
                })

    # --- Save tree ---
    _storage_policy.ensure_private_directory(output_path.parent)
    tree = {
        "schema_version": ARTIFACT_COMPLETION_SCHEMA_VERSION,
        "source_sha256": source_sha256,
        "source_record_count": source_record_count,
        "parameters_sha256": _artifact_parameters_sha256(parameters),
        "levels": 3,
        "nodes": tree_nodes,
        "stats": {
            "level_0": sum(1 for n in tree_nodes if n["level"] == 0),
            "level_1": sum(1 for n in tree_nodes if n["level"] == 1),
            "level_2": sum(1 for n in tree_nodes if n["level"] == 2),
            "total": len(tree_nodes),
        },
    }
    _atomic_write_json(output_path, tree)

    s = tree["stats"]
    log.info(f"RAPTOR tree -> {output_path}")
    log.info(f"  Level 0 (chunks):   {s['level_0']}")
    log.info(f"  Level 1 (sections): {s['level_1']}")
    log.info(f"  Level 2 (chapters): {s['level_2']}")
    log.info(f"  Total nodes:        {s['total']}")


# ---------------------------------------------------------------------------
# Step 9: Info
# ---------------------------------------------------------------------------

def show_info(chroma_dir: Path, collection_name: str = DEFAULT_COLLECTION, *,
              db_backend: str = DEFAULT_DB_BACKEND,
              lock_timeout: float = DEFAULT_DB_LOCK_TIMEOUT) -> None:
    """Show status of pipeline output artifacts.

    Scans the output directory for all pipeline artifacts, not just default paths.

    ``chroma_dir`` retains the original public keyword for compatibility; when
    ``db_backend='qdrant'`` it identifies the selected Qdrant directory instead.
    """
    db_dir = Path(chroma_dir)
    print(f"\n{'='*60}")
    print(" Pipeline Output Status")
    print(f"{'='*60}\n")

    out = OUTPUT_DIR
    if not out.exists():
        print("  No output directory found.")
        return

    # Scan for all pipeline outputs in the output directory
    def _show_file(label: str, path: Path):
        try:
            try:
                display_name = str(path.relative_to(out))
            except ValueError:
                display_name = str(path)
            if path.is_dir():
                size = sum(
                    f.stat().st_size
                    for f in path.rglob("*") if f.is_file())
                print(
                    f"  [OK] {label:20s} {display_name:40s} "
                    f"({size / 1e6:.1f} MB)")
            else:
                stat_result = path.stat()
                size = stat_result.st_size
                mtime = time.strftime(
                    "%Y-%m-%d %H:%M",
                    time.localtime(stat_result.st_mtime))
                print(f"  [OK] {label:20s} {display_name:40s} "
                      f"({size / 1e6:.1f} MB, {mtime})")
        except OSError as exc:
            print(f"  [BUSY] {label:20s} {path} (status unavailable: {exc})")

    # Group by file type. Current runs live under output/<book>/ while older
    # runs may still use the output root, so scan recursively.
    jsons = sorted(
        p for p in out.rglob("*.json")
        if (p.parent == out or p.stem == p.parent.name)
        and not p.name.endswith(("_raptor.json", ".metadata.json"))
        and "citation" not in p.stem.lower()
    )
    chunks = sorted(out.rglob("*_chunks.jsonl"))
    quality_reports = sorted(out.rglob("*_chunks.quality.json"))
    questions_jsonls = sorted(out.rglob("*questions*.jsonl"))
    citations_jsons = sorted(out.rglob("*citations*.json"))
    markdowns = sorted(out.rglob("*.md"))
    chroma_dirs = sorted(
        p for p in out.rglob("*")
        if p.is_dir() and (p.name.endswith("_chroma") or p.name == "chroma_db")
    )
    qdrant_dirs = sorted(
        p for p in out.rglob("*")
        if p.is_dir() and (p.name.endswith("_qdrant") or p.name == "qdrant_db")
    )
    chapter_dirs = sorted(
        p for p in out.rglob("*")
        if p.is_dir() and (p.name == "Chapters" or p.name.endswith("_chapters"))
    )

    if jsons:
        print("  DoclingDocuments:")
        for p in jsons:
            # Skip sidecar files (chunk hashes)
            if p.name in ("chunk_hashes.json",):
                continue
            _show_file("", p)

    if chunks:
        print("  Chunk files:")
        for p in chunks:
            _show_file("", p)

    if quality_reports:
        print("  Corpus quality reports:")
        for report_path in quality_reports:
            chunks_path = report_path.with_name(
                report_path.name.removesuffix(".quality.json") + ".jsonl")
            try:
                records, _, _ = _load_index_snapshot_strict(chunks_path)
                payload = json.loads(report_path.read_text(encoding="utf-8"))
                label = (
                    f"PASS v{payload.get('schema_version')} "
                    f"({len(records)} chunks)"
                )
            except (OSError, UnicodeError, json.JSONDecodeError, ValueError,
                    RuntimeError):
                label = "FAIL"
            _show_file(label, report_path)

    if questions_jsonls:
        print("  Questions files:")
        for p in questions_jsonls:
            _show_file("", p)

    if citations_jsons:
        print("  Citations files:")
        for p in citations_jsons:
            _show_file("", p)

    if markdowns:
        print("  Markdown exports:")
        for p in markdowns:
            _show_file("", p)

    if chapter_dirs:
        print("  Chapter splits:")
        for d in chapter_dirs:
            n_files = len(list(d.glob("*.md")))
            _show_file(f"({n_files} files)", d)

    if chroma_dirs:
        print("  ChromaDB indexes:")
        for d in chroma_dirs:
            _show_file("", d)

    # RAPTOR trees
    raptor_files = sorted(out.rglob("*_raptor.json"))
    if raptor_files:
        print("  RAPTOR trees:")
        for p in raptor_files:
            try:
                tree = json.loads(p.read_text(encoding="utf-8"))
                stats = tree.get("stats", {})
                label = (f"L0:{stats.get('level_0', '?')} "
                         f"L1:{stats.get('level_1', '?')} "
                         f"L2:{stats.get('level_2', '?')}")
                _show_file(label, p)
            except Exception:
                _show_file("", p)

    if qdrant_dirs:
        print("  Qdrant indexes:")
        for d in qdrant_dirs:
            _show_file("", d)

    if not any([jsons, chunks, questions_jsonls, citations_jsons,
                markdowns, chroma_dirs, qdrant_dirs, raptor_files]):
        print("  No pipeline output found.")

    # Chunk stats for each chunks file
    for cp in chunks:
        records = _load_jsonl(cp)
        if not records:
            continue
        print(f"\n--- {cp.name} ({len(records)} chunks) ---")
        type_counts: dict[str, int] = {}
        ch_counts: dict[int, int] = {}
        for rec in records:
            ct = rec["metadata"]["content_type"]
            type_counts[ct] = type_counts.get(ct, 0) + 1
            ch = rec["metadata"].get("chapter_num")
            if ch is not None:
                ch_counts[ch] = ch_counts.get(ch, 0) + 1
        print("  Content types:")
        for ct, n in sorted(type_counts.items(), key=lambda x: -x[1]):
            print(f"    {ct:25s} {n:5d}  ({100*n/len(records):.0f}%)")
        if ch_counts:
            print(f"  Chapters ({len(ch_counts)} detected):")
            for ch in sorted(ch_counts):
                print(f"    Ch {ch:2d}: {ch_counts[ch]:4d} chunks")

    # Exact selected-backend stats are protected by the vector-store lease.
    if db_dir.exists():
        label = "Qdrant" if db_backend == "qdrant" else "ChromaDB"
        unit = "Points" if db_backend == "qdrant" else "Documents"
        try:
            count = _index_collection_count(
                db_dir, collection_name, db_backend=db_backend,
                lock_timeout=lock_timeout)
            print(f"\n--- {label} ---")
            print(f"  Collection: {collection_name}")
            print(f"  {unit}:  {count}")
        except LookupError:
            print(f"\n--- {label} ---")
            print(f"  Collection '{collection_name}' not found")
        except Exception as exc:
            print(f"\n--- {label} ---")
            print(f"  Collection status unavailable: {exc}")

    print()


# ---------------------------------------------------------------------------
# Pipeline orchestration
# ---------------------------------------------------------------------------

class _PipelineStageError(RuntimeError):
    """Wrap a pipeline failure with the stage that raised it."""

    def __init__(self, stage: str, cause: BaseException):
        self.stage = stage
        self.cause = cause
        super().__init__(f"{stage}: {cause}")


def _background_pipeline_plan(
        context: _job_runtime.JobWorkerContext | None,
        pdf_path: Path, item_index: int, *, fallback_resume: bool,
        fallback_exact_run_name: str | None = None,
        ) -> tuple[
            bool, str | None, Callable[[str], None] | None,
            _job_runtime.PipelineRunBinding | None]:
    """Resolve one attempt's exact run without inferring another job's run."""
    if context is None:
        return (
            fallback_resume, fallback_exact_run_name, None, None)
    execution = context.execution
    binding = context.store.get_pipeline_binding(
        execution.job_id, item_index=item_index, input_path=pdf_path)

    def bind(run_name: str) -> None:
        context.store.bind_pipeline_run(
            execution.job_id,
            attempt_token=execution.attempt_token,
            item_index=item_index,
            input_path=pdf_path,
            run_name=run_name,
        )

    if binding is None:
        # A prior attempt may have failed before allocation became durable.
        # Allocate afresh instead of falling back to an unrelated latest run.
        return False, None, bind, None
    return True, binding.run_name, bind, binding


def _mark_background_pipeline_binding(
        context: _job_runtime.JobWorkerContext | None,
        pdf_path: Path, item_index: int, status: str, *,
        primary_error: BaseException | None = None) -> None:
    if context is None:
        return
    try:
        binding = context.store.get_pipeline_binding(
            context.execution.job_id,
            item_index=item_index,
            input_path=pdf_path)
        if binding is not None:
            context.store.mark_pipeline_binding(
                context.execution.job_id,
                attempt_token=context.execution.attempt_token,
                item_index=item_index,
                input_path=pdf_path,
                status=status,
            )
    except BaseException as marker_error:
        if primary_error is None:
            raise
        _log_cleanup_error(
            "Background run-binding update failed while preserving the "
            "pipeline error",
            error=marker_error,
        )


def _resolve_cloud_endpoint(args) -> tuple[str, str]:
    """Resolve URL/model shortcuts for the configured cloud provider."""
    return _cli_policy._resolve_cloud_endpoint(
        args,
        defaults=_provider_cli_defaults(),
        validate_cloud_endpoint_fn=_validate_cloud_endpoint,
    )


def _cloud_endpoint_arg(value: str) -> str:
    """Argparse type that canonicalizes URLs without echoing rejected text."""
    try:
        endpoint = _validate_cloud_endpoint(value, allow_disabled=True)
    except (TypeError, ValueError) as exc:
        raise argparse.ArgumentTypeError(str(exc)) from None
    return endpoint.base_url if endpoint is not None else ""


def _resolve_cloud_key(args, *, cloud_url: str = "",
                       cloud_model: str = "") -> str:
    """Resolve a key without sending one provider's secret to another host."""
    return _cli_policy._resolve_cloud_key(
        args,
        cloud_url=cloud_url,
        cloud_model=cloud_model,
        resolve_cloud_endpoint_fn=_resolve_cloud_endpoint,
        validate_cloud_endpoint_fn=_validate_cloud_endpoint,
        environment_get_fn=os.environ.get,
    )


def _llm_kwargs_from_args(
        args, *, include_workers: bool = False,
        resolve_credentials: bool = True) -> dict:
    """Collect provider options shared by LLM-backed operations."""
    return _cli_policy._llm_kwargs_from_args(
        args,
        include_workers=include_workers,
        defaults=_provider_cli_defaults(),
        resolve_cloud_endpoint_fn=_resolve_cloud_endpoint,
        resolve_cloud_key_fn=_resolve_cloud_key,
        resolve_credentials=resolve_credentials,
    )


_namespace_uses_llm = _cli_policy._namespace_uses_llm


def _configure_llm_runtime_from_args(args) -> None:
    """Reset run-scoped LLM controls from a parsed CLI namespace."""
    defaults = LLMRuntimeConfig()
    values = _cli_policy._llm_runtime_config_values_from_args(
        args, default_cache_dir=defaults.cache_dir)
    _llm_runtime.configure(LLMRuntimeConfig(**values))


def _index_chunks_for_backend(chunks_path: Path, db_dir: Path, *,
                              db_backend: str, collection_name: str,
                              embedding_model: str,
                              full_reindex: bool = False,
                              security_policy: (
                                  _release_security.ReleaseSecurityPolicy | None
                              ) = None,
                              lock_timeout: float = (
                                  DEFAULT_DB_LOCK_TIMEOUT),
                              _active_update_token: str | None = None,
                              _operation_observer: Callable[
                                  [dict[str, int | float | bool]], None]
                              | None = None,
                              ) -> _operation_contracts.IndexOutcome:
    """Dispatch indexing to the configured storage backend."""
    observer_kwargs = (
        {"_operation_observer": _operation_observer}
        if _operation_observer is not None else {})
    if db_backend == "qdrant":
        return index_chunks_qdrant(
            chunks_path, db_dir,
            collection_name=collection_name,
            embedding_model=embedding_model,
            full_reindex=full_reindex,
            security_policy=security_policy,
            lock_timeout=lock_timeout,
            _active_update_token=_active_update_token,
            **observer_kwargs,
        )
    else:
        return index_chunks(
            chunks_path, db_dir,
            collection_name=collection_name,
            embedding_model=embedding_model,
            full_reindex=full_reindex,
            security_policy=security_policy,
            lock_timeout=lock_timeout,
            _active_update_token=_active_update_token,
            **observer_kwargs,
        )


def _query_index_for_backend(query_text: str, db_dir: Path, *,
                             db_backend: str, **kwargs) -> None:
    """Dispatch a CLI query while preserving the backend-specific wrappers."""
    if db_backend == "qdrant":
        query_index_qdrant(query_text, db_dir, **kwargs)
    else:
        query_index(query_text, db_dir, **kwargs)


def _run_pipeline_stages(pdf_path: Path, paths: PipelinePaths, args, *,
                         resume: bool, watermark: re.Pattern | None,
                         telemetry: _run_telemetry.RunTelemetry | None = None,
                         stage_scope: str | None = None) -> dict:
    """Run one PDF through the shared full/batch stage sequence.

    The CLI namespace is accepted at this boundary so the individual pipeline
    functions remain independent of argument parsing. A structured result is
    returned for both the single-run summary and batch reporting.
    """
    db_backend = getattr(args, "db_backend", DEFAULT_DB_BACKEND)
    collection = getattr(args, "collection", None) or paths["collection"]
    db_dir = paths["qdrant"] if db_backend == "qdrant" else paths["chroma"]
    quality_report_path = paths.get(
        "quality_report", _quality_core.quality_report_path(paths["chunks"]))
    llm_kwargs = _llm_kwargs_from_args(
        args,
        include_workers=True,
        resolve_credentials=_cli_policy._pipeline_features_use_llm(args),
    )
    llm_kwargs.setdefault(
        "security_policy",
        _effective_security_policy(
            getattr(args, "_release_security_policy", None)),
    )
    structure_profile = _document_profiles.get_profile(
        getattr(args, "structure_profile", DEFAULT_STRUCTURE_PROFILE))

    def observed_stage(name: str) -> str:
        return f"{stage_scope}.{name}" if stage_scope else name

    def stage_started(name: str, *, metrics: dict | None = None) -> None:
        if telemetry is not None:
            telemetry.stage_started(observed_stage(name), metrics=metrics)

    def stage_finished(name: str, *, status: str = "completed",
                       metrics: dict | None = None) -> None:
        if telemetry is not None:
            telemetry.stage_finished(
                observed_stage(name), status=status, metrics=metrics)

    current_stage = "convert"
    index_attempt_metrics: dict[str, int | float | bool] = {}
    try:
        stage_started(current_stage)
        conversion_parameters = _conversion_parameters(
            batch_size_override=args.batch_size, backend=args.backend,
            auto_preprocess=not args.no_preprocess,
            ocr=getattr(args, "ocr", None), watermark=watermark)
        if resume and _converted_outputs_complete(
                pdf_path, paths["doc"], paths["converted_markdown"],
                parameters=conversion_parameters,
                preprocessed_output=paths["preprocessed"]):
            log.info(f"  [SKIP] convert (output exists: {paths['doc']})")
            stage_finished(current_stage, status="skipped")
        else:
            convert_pdf(
                pdf_path,
                paths["doc"],
                batch_size_override=args.batch_size,
                backend=args.backend,
                force=args.force,
                watermark=watermark,
                auto_preprocess=not args.no_preprocess,
                ocr=getattr(args, "ocr", None),
                preprocessed_output=paths["preprocessed"],
                markdown_output=paths["converted_markdown"],
                security_policy=llm_kwargs["security_policy"],
            )
            if not _converted_outputs_complete(
                    pdf_path, paths["doc"], paths["converted_markdown"],
                    parameters=conversion_parameters,
                    preprocessed_output=paths["preprocessed"]):
                raise RuntimeError(
                    "Conversion did not publish a complete artifact set")
            log.info(f"  [DONE] convert -> {paths['doc']}")
            stage_finished(current_stage)

        current_stage = "chunk_index_lease"
        lock_timeout = getattr(
            args, "db_lock_timeout", DEFAULT_DB_LOCK_TIMEOUT)
        stage_started(current_stage)
        with _vector_store_lock(
                db_dir, backend=db_backend, collection_name=collection,
                operation="pipeline chunk/index transition",
                timeout=lock_timeout):
            stage_finished(current_stage)
            current_stage = "chunk"
            stage_started(current_stage)
            chunk_parameters = _chunk_parameters(
                embedding_model=args.embedding_model,
                max_tokens=args.max_tokens, min_words=args.min_words,
                dedup_threshold=args.dedup_threshold, watermark=watermark,
                llm_classify=args.llm_classify,
                zeroshot_classify=args.zeroshot_classify,
                contextualize=args.contextualize,
                reconstruct_headings=args.reconstruct_headings,
                quality_score=args.quality_score,
                llm_scaffold=getattr(args, "llm_scaffold", False),
                table_children=getattr(args, "table_children", False),
                structure_profile=structure_profile,
                **llm_kwargs)
            chunk_count = (
                _chunk_record_count(paths["chunks"])
                if _chunks_complete(
                    paths["doc"], paths["chunks"],
                    parameters=chunk_parameters,
                    source_pdf_path=pdf_path)
                else None
            )
            active_update_token = None
            if resume and chunk_count is not None:
                chunk_was_skipped = True
                log.info(
                    f"  [SKIP] chunk (verified complete: {paths['chunks']})")
                stage_finished(
                    current_stage, status="skipped",
                    metrics={"records": chunk_count})
            else:
                chunk_was_skipped = False
                marker_path = _index_update_marker_path(
                    db_dir, backend=db_backend,
                    collection_name=collection)
                if not marker_path.exists():
                    active_update_token = uuid4().hex
                    _begin_index_update(
                        db_dir, backend=db_backend,
                        collection_name=collection,
                        source_sha256="pending-chunk-publication",
                        source_record_count=0,
                        owner_token=active_update_token)
                chunk_document(
                    paths["doc"],
                    paths["chunks"],
                    source_pdf_path=pdf_path,
                    embedding_model=args.embedding_model,
                    max_tokens=args.max_tokens,
                    min_words=args.min_words,
                    dedup_threshold=args.dedup_threshold,
                    watermark=watermark,
                    llm_classify=args.llm_classify,
                    zeroshot_classify=args.zeroshot_classify,
                    contextualize=args.contextualize,
                    reconstruct_headings=args.reconstruct_headings,
                    quality_score=args.quality_score,
                    llm_scaffold=getattr(args, "llm_scaffold", False),
                    table_children=getattr(args, "table_children", False),
                    structure_profile=structure_profile,
                    **llm_kwargs,
                )
                if not _chunks_complete(
                        paths["doc"], paths["chunks"],
                        parameters=chunk_parameters,
                        source_pdf_path=pdf_path):
                    raise RuntimeError(
                        "Chunking did not publish a complete artifact set")
                log.info(f"  [DONE] chunk -> {paths['chunks']}")
                chunk_count = _chunk_record_count(paths["chunks"])
                stage_finished(
                    current_stage, metrics={"records": chunk_count})

            current_stage = "quality"
            stage_started(current_stage, metrics={"records": chunk_count})
            if _quality_report_complete(
                    paths["doc"], paths["chunks"],
                    parameters=chunk_parameters):
                log.info(
                    "  [CHECK] corpus quality PASS -> %s",
                    quality_report_path,
                )
                stage_finished(
                    current_stage,
                    status="skipped" if chunk_was_skipped else "completed",
                    metrics={"records": chunk_count, "passed": True},
                )
            else:
                report = _publish_corpus_quality_report(
                    paths["doc"], paths["chunks"],
                    parameters=chunk_parameters,
                    structure_profile=structure_profile)
                if not _quality_report_complete(
                        paths["doc"], paths["chunks"],
                        parameters=chunk_parameters):
                    raise RuntimeError(
                        "Corpus quality report publication did not verify")
                log.info(
                    "  [DONE] quality %s -> %s",
                    report["status"].upper(), quality_report_path,
                )
                stage_finished(
                    current_stage,
                    metrics={"records": chunk_count, "passed": True},
                )

            current_stage = "index"
            stage_started(current_stage, metrics={"records": chunk_count})
            if resume:
                log.info(
                    "  [CHECK] index manifest, model, dimension, and chunk "
                    "hashes")
            index_outcome = _index_chunks_for_backend(
                paths["chunks"],
                db_dir,
                db_backend=db_backend,
                collection_name=collection,
                embedding_model=args.embedding_model,
                full_reindex=getattr(args, "full_reindex", False),
                security_policy=llm_kwargs["security_policy"],
                lock_timeout=lock_timeout,
                _active_update_token=active_update_token,
                _operation_observer=index_attempt_metrics.update,
            )
            log.info(f"  [DONE] index -> {db_dir}")
            stage_finished(
                current_stage, metrics=index_outcome.telemetry_metrics())

        current_stage = "export"
        stage_started(current_stage)
        unified_parameters = _markdown_export_parameters(
            include_types=None, exclude_types=None, chapters=None,
            split_chapters=False)
        if resume and _unified_export_complete(
                paths["chunks"], paths["export"],
                parameters=unified_parameters):
            log.info(f"  [SKIP] unified export (output exists: {paths['export']})")
            stage_finished(current_stage, status="skipped")
        else:
            export_markdown(paths["chunks"], paths["export"], **llm_kwargs)
            if not _unified_export_complete(
                    paths["chunks"], paths["export"],
                    parameters=unified_parameters):
                raise RuntimeError(
                    "Unified export did not publish a complete artifact set")
            log.info(f"  [DONE] unified export -> {paths['export']}")
            stage_finished(current_stage)

        if getattr(args, "split_chapters", False):
            current_stage = "chapter_export"
            stage_started(current_stage)
            split_parameters = _markdown_export_parameters(
                include_types=None, exclude_types=None, chapters=None,
                split_chapters=True)
            if resume and _split_export_complete(
                    paths["chunks"], paths["chapters_dir"],
                    parameters=split_parameters):
                log.info(
                    f"  [SKIP] chapter export (output exists: {paths['chapters_dir']})")
                stage_finished(current_stage, status="skipped")
            else:
                export_markdown(
                    paths["chunks"],
                    paths["export"],
                    split_chapters=True,
                    chapters_dir=paths["chapters_dir"],
                    **llm_kwargs,
                )
                if not _split_export_complete(
                        paths["chunks"], paths["chapters_dir"],
                        parameters=split_parameters):
                    raise RuntimeError(
                        "Chapter export did not publish a complete artifact set")
                log.info(f"  [DONE] chapter export -> {paths['chapters_dir']}")
                stage_finished(current_stage)

        if getattr(args, "raptor", False):
            current_stage = "raptor"
            stage_started(current_stage)
            raptor_out = paths["chunks"].with_name(
                paths["chunks"].stem.replace("_chunks", "") + "_raptor.json")
            raptor_parameters = _raptor_parameters(
                embedding_model=args.embedding_model,
                cloud_url=llm_kwargs["cloud_url"],
                cloud_model=llm_kwargs["cloud_model"],
                cloud_key=llm_kwargs["cloud_key"],
                ollama_url=llm_kwargs["ollama_url"],
                ollama_model=llm_kwargs["ollama_model"],
                gemini_key=llm_kwargs["gemini_key"],
                thinking=llm_kwargs["thinking"],
                security_policy=llm_kwargs["security_policy"])
            if resume and _raptor_output_complete(
                    paths["chunks"], raptor_out,
                    parameters=raptor_parameters):
                log.info(f"  [SKIP] raptor (output exists: {raptor_out})")
                stage_finished(current_stage, status="skipped")
            else:
                build_raptor_tree(
                    paths["chunks"],
                    raptor_out,
                    embedding_model=args.embedding_model,
                    **llm_kwargs,
                )
                if not _raptor_output_complete(
                        paths["chunks"], raptor_out,
                        parameters=raptor_parameters):
                    raise RuntimeError(
                        "RAPTOR did not publish a complete tree")
                log.info(f"  [DONE] raptor -> {raptor_out}")
                stage_finished(current_stage)

    except KeyboardInterrupt as exc:
        if telemetry is not None:
            telemetry.stage_cancelled(
                observed_stage(current_stage), exc,
                metrics=(index_attempt_metrics
                         if current_stage == "index" else None))
        raise
    except SystemExit as exc:
        if telemetry is not None:
            if exc.code == 130:
                telemetry.stage_cancelled(
                    observed_stage(current_stage), exc,
                    metrics=(index_attempt_metrics
                             if current_stage == "index" else None))
            else:
                telemetry.stage_failed(
                    observed_stage(current_stage), exc,
                    metrics=(index_attempt_metrics
                             if current_stage == "index" else None))
        if exc.code == 130:
            raise
        raise _PipelineStageError(current_stage, exc) from exc
    except Exception as exc:
        if telemetry is not None:
            telemetry.stage_failed(
                observed_stage(current_stage), exc,
                metrics=(index_attempt_metrics
                         if current_stage == "index" else None))
        raise _PipelineStageError(current_stage, exc) from exc

    return {
        "paths": paths,
        "collection": collection,
        "db_dir": db_dir,
        "db_backend": db_backend,
        "index_outcome": index_outcome,
    }


def _run_pipeline_job(pdf_path: Path, args, *, resume: bool,
                      watermark: re.Pattern | None,
                      announce: bool = False,
                      telemetry: _run_telemetry.RunTelemetry | None = None,
                      stage_scope: str | None = None,
                      exact_run_name: str | None = None,
                      on_run_allocated: Callable[[str], None]
                      | None = None) -> tuple[PipelinePaths, dict]:
    """Allocate and execute one run while holding its PDF-stem job lease."""
    if exact_run_name is not None and not resume:
        raise ValueError("an exact run binding requires resume mode")
    timeout = getattr(args, "db_lock_timeout", DEFAULT_DB_LOCK_TIMEOUT)
    try:
        with _pipeline_job_lock(pdf_path, timeout=timeout):
            paths = (
                _derive_output_paths_exact(pdf_path, exact_run_name)
                if exact_run_name is not None
                else _derive_output_paths_existing(pdf_path)
                if resume
                else _derive_output_paths(pdf_path)
            )
            collection = (
                getattr(args, "collection", None) or paths["collection"])
            run_root = paths["doc"].parent
            manifest_candidate = run_root / _retention.RUN_MANIFEST_NAME
            legacy_unowned = (
                resume and run_root.is_dir()
                and not manifest_candidate.exists()
                and any(run_root.iterdir()))
            if legacy_unowned:
                _storage_policy.harden_private_tree(run_root)
                manifest_path = None
                log.warning(
                    "Resuming a legacy run without an ownership manifest; "
                    "storage retention will not infer ownership for %s",
                    run_root)
            else:
                manifest_path = _retention.ensure_pipeline_run_manifest(
                    OUTPUT_DIR,
                    run_root,
                    job_scope=Path(pdf_path).stem,
                    owned_siblings=[paths["preprocessed"]],
                    vector_stores=[
                        {
                            "backend": "chroma",
                            "collection": collection,
                            "path": paths["chroma"],
                        },
                        {
                            "backend": "qdrant",
                            "collection": collection,
                            "path": paths["qdrant"],
                        },
                    ],
                )
            if announce:
                log.info("=== FULL PIPELINE ===")
                log.info(f"Output prefix: {paths['doc'].stem}")
                if resume:
                    log.info("  (--resume mode: skipping completed stages)")
            try:
                if on_run_allocated is not None:
                    # This callback runs after the ownership manifest is
                    # durable and while allocation remains serialized.
                    on_run_allocated(run_root.name)
                run = _run_pipeline_stages(
                    pdf_path, paths, args, resume=resume,
                    watermark=watermark, telemetry=telemetry,
                    stage_scope=stage_scope)
            except BaseException as exc:
                state = (
                    "cancelled"
                    if (isinstance(exc, KeyboardInterrupt)
                        or isinstance(exc, SystemExit) and exc.code == 130)
                    else "failed")
                if manifest_path is not None:
                    try:
                        _retention.mark_pipeline_run_state(
                            manifest_path, state)
                    except BaseException as marker_error:
                        _log_cleanup_error(
                            "Pipeline ownership-state update failed for %s",
                            manifest_path, error=marker_error)
                raise
            if manifest_path is not None:
                _retention.mark_pipeline_run_state(manifest_path, "complete")
            return paths, run
    except VectorStoreBusyError as exc:
        if telemetry is not None:
            stage = (
                f"{stage_scope}.concurrency" if stage_scope
                else "concurrency")
            telemetry.stage_failed(stage, exc)
        raise _PipelineStageError("concurrency", exc) from exc


# ---------------------------------------------------------------------------
# CLI
# ---------------------------------------------------------------------------

def _normalize_operation_timeout(timeout: float) -> float:
    """Validate a finite positive deadline accepted by process waiting APIs."""
    return _cli_policy._normalize_operation_timeout(
        timeout, timeout_max=_threading.TIMEOUT_MAX)


def _cli_operation_timeout(argv: list[str], operation: str) -> float:
    """Read the last CLI deadline without replacing argparse validation."""
    return _cli_policy._cli_operation_timeout(
        argv,
        operation,
        operation_timeouts=DEFAULT_OPERATION_TIMEOUTS,
        normalize_timeout_fn=_normalize_operation_timeout,
    )


_WindowsKillJob = _process_supervision._WindowsKillJob
_PosixSupervisedStartGate = _process_supervision._PosixSupervisedStartGate
_WindowsSupervisedStartGate = _process_supervision._WindowsSupervisedStartGate
_SupervisorSignal = _process_supervision._SupervisorSignal
_SupervisorCleanupError = _process_supervision._SupervisorCleanupError


def _new_supervised_start_gate():
    """Build a platform gate through facade-replaceable class globals."""
    return (_WindowsSupervisedStartGate()
            if os.name == "nt" else _PosixSupervisedStartGate())


def _supervision_config() -> _process_supervision.SupervisionConfig:
    """Snapshot facade-owned supervision constants for one operation."""
    return _process_supervision.SupervisionConfig(
        supervised_child_env=_SUPERVISED_CHILD_ENV,
        run_id_env=_RUN_ID_ENV,
        terminate_grace=_SUPERVISED_TERMINATE_GRACE,
        poll_interval=_SUPERVISED_POLL_INTERVAL,
        start_gate_timeout=_SUPERVISED_START_GATE_TIMEOUT,
    )


def _terminate_supervised_process(process, *, kill_job=None) -> bool:
    """Terminate a supervised tree through the extracted runtime core."""
    return _process_supervision._terminate_supervised_process(
        process,
        kill_job=kill_job,
        terminate_grace=_SUPERVISED_TERMINATE_GRACE,
    )


def _supervised_telemetry_requested(
        run_id: str | None, run_events: Path | None,
        run_report: Path | None) -> bool:
    return run_id is not None and (
        run_events is not None or run_report is not None)


def _finalize_supervised_run_telemetry(
        operation: str, *, run_id: str | None,
        run_events: Path | None, run_report: Path | None,
        status: str, exc: BaseException) -> None:
    if not _supervised_telemetry_requested(
            run_id, run_events, run_report):
        return
    try:
        _run_telemetry.finalize_interrupted_run(
            operation, run_id=run_id, status=status, exc=exc,
            events_path=run_events, report_path=run_report)
    except Exception as telemetry_exc:
        print(
            "Could not finalize supervised run telemetry "
            f"({type(telemetry_exc).__name__}).",
            file=sys.stderr,
        )


def _start_supervised_run_telemetry(
        operation: str, *, run_id: str | None,
        run_events: Path | None, run_report: Path | None) -> None:
    if _supervised_telemetry_requested(run_id, run_events, run_report):
        _run_telemetry.RunTelemetry(
            operation, run_id=run_id, events_path=run_events,
            report_path=run_report).start()


def _run_cli_with_deadline(script_path: Path, argv: list[str], *,
                           operation: str, timeout: float,
                           working_directory: Path | None = None,
                           environment_overrides: dict[str, str | None]
                           | None = None,
                           run_id: str | None = None,
                           run_events: Path | None = None,
                           run_report: Path | None = None,
                           cancel_requested: Callable[[], bool]
                           | None = None,
                           on_child_started: Callable[[Any], None]
                           | None = None,
                           heartbeat: Callable[[Any], None] | None = None,
                           stdout_target: Any = None,
                           stderr_target: Any = None) -> int:
    """Run one CLI operation through the extracted supervision core.

    Mutable collaborators are resolved from this facade for every call so the
    established monkeypatch and consumer seams remain compatible.
    """
    return _process_supervision._run_cli_with_deadline(
        script_path,
        argv,
        operation=operation,
        timeout=timeout,
        config=_supervision_config(),
        normalize_timeout_fn=_normalize_operation_timeout,
        telemetry_start_fn=_start_supervised_run_telemetry,
        telemetry_finalize_fn=_finalize_supervised_run_telemetry,
        kill_job_factory=_WindowsKillJob,
        start_gate_factory=_new_supervised_start_gate,
        terminate_fn=_terminate_supervised_process,
        supervisor_signal_type=_SupervisorSignal,
        cleanup_error_type=_SupervisorCleanupError,
        working_directory=working_directory,
        environment_overrides=environment_overrides,
        run_id=run_id,
        run_events=run_events,
        run_report=run_report,
        cancel_requested=cancel_requested,
        on_child_started=on_child_started,
        heartbeat=heartbeat,
        stdout_target=stdout_target,
        stderr_target=stderr_target,
    )


_rag_cli_command = _cli_policy._rag_cli_command
_cli_run_telemetry_options = _cli_policy._cli_run_telemetry_options


def _run_rag_entrypoint(
        argv: list[str] | None = None, *,
        environment_overrides: dict[str, str | None] | None = None) -> int:
    """Run the CLI through the generic extracted entrypoint policy."""
    return _process_supervision._run_supervised_entrypoint(
        argv,
        environment_overrides=environment_overrides,
        config=_supervision_config(),
        script_path=Path(__file__),
        command_resolver=_rag_cli_command,
        operation_timeouts=DEFAULT_OPERATION_TIMEOUTS,
        telemetry_options_fn=_cli_run_telemetry_options,
        timeout_fn=_cli_operation_timeout,
        run_id_factory=_run_telemetry.new_run_id,
        menu_fn=interactive_menu,
        main_fn=main,
        supervisor_fn=_run_cli_with_deadline,
    )


def _render_storage_outcome(payload: dict) -> None:
    if payload.get("mode") == "dry_run":
        print(
            f"DRY RUN: {payload['action']} found "
            f"{payload['candidate_count']} owned candidate(s), "
            f"{payload['total_bytes']} byte(s).")
        for candidate in payload["candidates"]:
            print(
                f"  {candidate['relative_path']} "
                f"({candidate['size_bytes']} bytes, "
                f"{candidate['age_days']:.1f} days old)")
        print("No data was deleted. Re-run with --apply to execute this plan.")
        return
    print(
        f"APPLIED: {payload['action']} deleted "
        f"{payload['deleted_count']} owned candidate(s), "
        f"{payload['deleted_bytes']} byte(s).")


def _apply_pipeline_deletion_with_leases(
        args, plan: _retention.RetentionPlan) -> dict:
    context = plan.context
    job_scope = context["job_scope"]
    with _pipeline_job_lock(
            Path(job_scope), timeout=args.db_lock_timeout,
            output_root=plan.root):
        with ExitStack() as leases:
            stores = sorted(
                context["vector_stores"],
                key=lambda record: (record["path"], record["backend"]))
            for record in stores:
                db_path = plan.root / Path(record["path"])
                leases.enter_context(_vector_store_lock(
                    db_path,
                    backend=record["backend"],
                    collection_name=record["collection"],
                    operation="pipeline run retention",
                    timeout=args.db_lock_timeout,
                ))
            fresh_plan = _retention.plan_pipeline_run_deletion(
                plan.root, context["run_name"])
            if fresh_plan.context["ownership_token"] != (
                    context["ownership_token"]):
                raise _retention.RetentionError(
                    "pipeline ownership changed while acquiring leases")
            return _retention.apply_retention_plan(fresh_plan)


def _run_storage_command(args) -> dict[str, int]:
    cache_root = (
        args.llm_cache_dir
        if args.llm_cache_dir is not None
        else LLMRuntimeConfig().cache_dir)
    if args.delete_run is not None:
        plan = _retention.plan_pipeline_run_deletion(
            args.output_root, args.delete_run)
        payload = (
            _apply_pipeline_deletion_with_leases(args, plan)
            if args.apply else plan.as_dict())
        outcomes = [payload]
    elif args.prune_llm_cache:
        plan = _retention.plan_llm_cache_prune(
            cache_root, older_than_days=args.older_than_days,
            max_total_bytes=args.max_cache_bytes)
        payload = (
            _retention.apply_retention_plan(plan)
            if args.apply else plan.as_dict())
        outcomes = [payload]
    elif args.prune_ui_exports:
        plan = _retention.plan_ui_export_prune(
            args.output_root, older_than_days=args.older_than_days)
        payload = (
            _retention.apply_retention_plan(plan)
            if args.apply else plan.as_dict())
        outcomes = [payload]
    elif getattr(args, "prune_snapshot_scratch", False):
        min_age_seconds = args.older_than_days * 24 * 60 * 60
        planned = _artifact_io.cleanup_stale_snapshot_directories(
            getattr(args, "snapshot_scratch_root", None),
            min_age_seconds=min_age_seconds,
            apply=False,
        )
        if args.apply:
            removed = _artifact_io.cleanup_stale_snapshot_directories(
                getattr(args, "snapshot_scratch_root", None),
                min_age_seconds=min_age_seconds,
                apply=True,
            )
            payload = {
                "schema_version": 1,
                "action": "prune_snapshot_scratch",
                "mode": "applied",
                "deleted_count": len(removed),
                "deleted_bytes": 0,
            }
        else:
            payload = {
                "schema_version": 1,
                "action": "prune_snapshot_scratch",
                "mode": "dry_run",
                "apply_required": True,
                "root": str(_artifact_io._snapshot_scratch_root_path(
                    getattr(args, "snapshot_scratch_root", None))),
                "candidate_count": len(planned),
                "total_bytes": 0,
                "candidates": [{
                    "relative_path": path.name,
                    "size_bytes": 0,
                    "age_days": args.older_than_days,
                } for path in planned],
            }
        outcomes = [payload]
    else:
        roots = [Path(args.output_root), Path(cache_root)]
        unique_roots = []
        observed = set()
        for root in roots:
            identity = os.path.normcase(str(root.resolve(strict=False)))
            if identity not in observed:
                observed.add(identity)
                unique_roots.append(root)
        outcomes = []
        for root in unique_roots:
            plan = _retention.plan_quarantine_purge(
                root, older_than_days=args.older_than_days)
            outcomes.append(
                _retention.apply_quarantine_purge(plan)
                if args.apply else plan.as_dict())
    output_payload: dict | list[dict] = (
        outcomes[0] if len(outcomes) == 1 else outcomes)
    if args.output_json:
        print(json.dumps(output_payload, indent=2, ensure_ascii=False))
    else:
        for outcome in outcomes:
            _render_storage_outcome(outcome)
    return {
        "candidates": sum(
            int(outcome.get("candidate_count", outcome.get(
                "deleted_count", 0))) for outcome in outcomes),
        "bytes": sum(
            int(outcome.get("total_bytes", outcome.get(
                "deleted_bytes", 0))) for outcome in outcomes),
        "applied": bool(args.apply),
    }


def _render_job_summaries(summaries: list[_job_runtime.JobSummary]) -> None:
    if not summaries:
        print("No background jobs.")
        return
    print(f"{'JOB ID':32}  {'COMMAND':20}  {'STATUS':16}  ATTEMPT")
    for summary in summaries:
        print(
            f"{summary.job_id:32}  {summary.command:20}  "
            f"{summary.status:16}  {summary.attempt_number}")


def _background_submit_tokens(tokens: list[str]) -> tuple[str, list[str]]:
    tokens = list(tokens)
    if tokens and tokens[0] == "--":
        tokens.pop(0)
    if not tokens:
        raise _job_runtime.JobValidationError(
            "jobs submit requires '-- COMMAND [ARG ...]'")
    command, command_arguments = tokens[0], tokens[1:]
    if command in {"full", "batch"} and any(
            token == "--resume" or token.startswith("--resume=")
            for token in command_arguments):
        raise _job_runtime.JobValidationError(
            "initial background submissions cannot infer an existing run; "
            "submit a fresh job and use 'jobs resume' after interruption")
    return command, command_arguments


_BACKGROUND_SECURITY_VALUE_OPTIONS = {
    "--security-profile": "security_profile",
    "--network-policy": "network_policy",
    "--model-download-policy": "model_download_policy",
    "--llm-cache-namespace": "llm_cache_namespace",
    "--release-cache-namespace-id": "release_cache_namespace_id",
    "--release-security-policy-version": (
        "release_security_policy_version"),
}


def _canonical_background_security_argv(
        command: str, arguments: list[str]) -> list[str]:
    """Replace submitted policy flags with one immutable canonical receipt."""
    if command not in _job_runtime.ALLOWED_JOB_COMMANDS:
        return list(arguments)
    try:
        terminator_index = arguments.index("--")
    except ValueError:
        policy_arguments = list(arguments)
        positional_suffix: list[str] = []
    else:
        policy_arguments = list(arguments[:terminator_index])
        positional_suffix = list(arguments[terminator_index:])
    values: dict[str, object] = {
        "security_profile": "release",
        "network_policy": "local-only",
        "model_download_policy": "cache-only",
        "llm_cache_namespace": "",
        "release_cache_namespace_id": None,
        "release_security_policy_version": (
            _release_security.RELEASE_SECURITY_POLICY_VERSION),
        "trust_environment_network": False,
    }
    cleaned: list[str] = []
    index = 0
    while index < len(policy_arguments):
        token = policy_arguments[index]
        option, separator, inline_value = token.partition("=")
        attr = _BACKGROUND_SECURITY_VALUE_OPTIONS.get(option)
        if attr is not None:
            if separator:
                value = inline_value
            else:
                index += 1
                if index >= len(policy_arguments):
                    raise _job_runtime.JobValidationError(
                        f"{option} requires a value")
                value = policy_arguments[index]
            if attr == "release_security_policy_version":
                try:
                    values[attr] = int(value)
                except ValueError as exc:
                    raise _job_runtime.JobValidationError(
                        "invalid release-security policy version") from exc
            else:
                values[attr] = value
        elif option == "--trust-environment-network":
            if separator:
                raise _job_runtime.JobValidationError(
                    "--trust-environment-network accepts no value")
            values["trust_environment_network"] = True
        else:
            cleaned.append(token)
        index += 1
    policy = _cli_policy._release_security_policy_from_args(
        argparse.Namespace(**values))
    cleaned.extend([
        "--release-security-policy-version", str(policy.schema_version),
        "--security-profile", policy.profile,
        "--network-policy", policy.network_policy,
        "--model-download-policy", policy.model_download_policy,
    ])
    if policy.cache_namespace_id is not None:
        cleaned.extend([
            "--release-cache-namespace-id", policy.cache_namespace_id])
    if policy.trust_environment_network:
        cleaned.append("--trust-environment-network")
    return cleaned + positional_suffix


def _run_jobs_command(args) -> dict[str, int | bool]:
    import job_manager as _job_manager

    store = _job_runtime.JobStore(args.job_root)
    action = args.job_action
    payload: dict | list[dict]
    summaries: list[_job_runtime.JobSummary]
    if action == "submit":
        command, command_arguments = _background_submit_tokens(
            args.job_command)
        command_arguments = _canonical_background_security_argv(
            command, command_arguments)
        submitted = store.submit_job(
            command, command_arguments,
            timeout_seconds=args.timeout,
            working_directory=Path.cwd(), output_root=OUTPUT_DIR)
        try:
            launch = _job_manager.launch_detached(
                store, submitted.job_id,
                ready_timeout=args.ready_timeout)
        except BaseException as launch_error:
            try:
                execution = store.load_execution(submitted.job_id)
                store.transition_job(
                    submitted.job_id, "failed",
                    attempt_token=execution.attempt_token,
                    expected_revision=execution.revision,
                    lease_timeout=0)
            except BaseException as marker_error:
                _log_cleanup_error(
                    "Background launch-state update failed while preserving "
                    "the launch error",
                    error=marker_error)
            raise launch_error
        summary = store.get_job(submitted.job_id)
        payload = {"job": summary.as_dict(), "launch": launch.as_dict()}
        summaries = [summary]
    elif action == "list":
        summaries = _job_manager.reconcile_all_jobs(store)
        payload = [summary.as_dict() for summary in summaries]
    elif action == "status":
        summary = _job_manager.reconcile_job(store, args.job_id)
        summaries = [summary]
        payload = summary.as_dict()
    elif action == "cancel":
        store.request_cancel(args.job_id)
        deadline = time.monotonic() + args.wait_timeout
        summary = _job_manager.reconcile_job(store, args.job_id)
        while args.wait and not summary.terminal:
            summary = _job_manager.reconcile_job(store, args.job_id)
            if summary.terminal or time.monotonic() >= deadline:
                break
            time.sleep(0.1)
        summaries = [summary]
        payload = summary.as_dict()
    elif action == "resume":
        current = _job_manager.reconcile_job(store, args.job_id)
        resumed = store.prepare_resume(
            args.job_id, expected_revision=current.revision)
        try:
            launch = _job_manager.launch_detached(
                store, args.job_id, ready_timeout=args.ready_timeout)
        except BaseException as launch_error:
            try:
                execution = store.load_execution(args.job_id)
                store.transition_job(
                    args.job_id, "failed",
                    attempt_token=execution.attempt_token,
                    expected_revision=execution.revision,
                    lease_timeout=0)
            except BaseException as marker_error:
                _log_cleanup_error(
                    "Background resume-state update failed while preserving "
                    "the launch error",
                    error=marker_error)
            raise launch_error
        summary = store.get_job(args.job_id)
        payload = {
            "job": summary.as_dict(),
            "launch": launch.as_dict(),
            "resumed_from_revision": resumed.revision - 1,
        }
        summaries = [summary]
    elif action == "delete":
        if args.apply:
            store.prepare_delete(args.job_id)
        plan = _retention.plan_background_job_deletion(
            store.root, args.job_id)
        payload = (
            _retention.apply_retention_plan(plan)
            if args.apply else plan.as_dict())
        if args.job_json:
            print(json.dumps(payload, indent=2, ensure_ascii=False))
        else:
            _render_storage_outcome(payload)
        return {"jobs": 1, "terminal": 1, "applied": bool(args.apply)}
    else:
        raise _job_runtime.JobValidationError(
            "unknown background job action")

    if args.job_json:
        print(json.dumps(payload, indent=2, ensure_ascii=False))
    else:
        _render_job_summaries(summaries)
    return {
        "jobs": len(summaries),
        "terminal": sum(summary.terminal for summary in summaries),
        "applied": action in {"submit", "cancel", "resume"},
    }


CONTENT_TYPES = [
    "case_opinion", "notes_and_questions", "author_narrative",
    "statutory_excerpt", "chapter_introduction", "table",
    "footnote", "structural", "empty",
]


class _StrictArgumentParser(argparse.ArgumentParser):
    """Reject abbreviations and never reproduce submitted values in errors."""

    def __init__(self, *args, **kwargs):
        kwargs.setdefault("allow_abbrev", False)
        super().__init__(*args, **kwargs)

    def error(self, message):
        del message
        super().error(
            "invalid command line; argument values were omitted (use --help)")


def main(argv: list[str] | None = None):
    # Ensure print() handles non-ASCII (case names, Unicode dashes) on Windows
    if sys.stdout and hasattr(sys.stdout, "reconfigure"):
        try:
            sys.stdout.reconfigure(encoding="utf-8", errors="replace")
        except Exception:
            pass

    parser = _StrictArgumentParser(
        description="RAG Pipeline — Docling + HybridChunker + ChromaDB/Qdrant",
        formatter_class=argparse.RawDescriptionHelpFormatter,
    )
    parser.add_argument("-v", "--verbose", action="store_true",
                        help="Show DEBUG-level output")
    parser.add_argument("--quiet", action="store_true",
                        help="Only show warnings and errors")
    sub = parser.add_subparsers(dest="command")

    # --- Shared flag definitions ---
    def add_release_security_flags(p):
        if getattr(p, "_release_security_flags_added", False):
            return
        p._release_security_flags_added = True
        p.add_argument(
            "--security-profile", choices=["release", "development"],
            default="release",
            help=("Trust defaults (release is fail-closed; development "
                  "retains explicitly selected unsafe conveniences)"))
        p.add_argument(
            "--release-security-policy-version", type=int,
            default=_release_security.RELEASE_SECURITY_POLICY_VERSION,
            help=argparse.SUPPRESS)
        p.add_argument(
            "--network-policy", choices=["local-only", "allow-cloud"],
            default="local-only",
            help=("Private-data egress policy (default: local-only; cloud "
                  "providers require explicit allow-cloud)"))
        p.add_argument(
            "--model-download-policy",
            choices=["cache-only", "allow-reviewed-sync"],
            default="cache-only",
            help=("Reviewed model artifact synchronization policy "
                  "(default: cache-only)"))
        p.add_argument(
            "--llm-cache-namespace", default="",
            help=("Nonsecret trust/tenant label required for custom cloud "
                  "gateways in release mode; only its hash is reported"))
        p.add_argument(
            "--release-cache-namespace-id", default=None,
            help=argparse.SUPPRESS)
        p.add_argument(
            "--trust-environment-network", action="store_true",
            help=("Allow reviewed proxy and custom-CA environment overrides "
                  "for release cloud transports"))

    def add_embedding_flags(p):
        add_release_security_flags(p)
        p.add_argument("--embedding-model", type=str,
                        default=DEFAULT_EMBEDDING_MODEL,
                        help=f"Embedding model (default: {DEFAULT_EMBEDDING_MODEL}). "
                             f"Legal: {DEFAULT_EMBEDDING_MODEL_LEGAL} (paid, VOYAGE_API_KEY). "
                             f"General: {DEFAULT_EMBEDDING_MODEL_GENERAL} (free, local GPU)")

    def add_collection_flag(p, *, derive_from_run: bool = False):
        default = None if derive_from_run else DEFAULT_COLLECTION
        default_help = ("derived from the run name" if derive_from_run
                        else DEFAULT_COLLECTION)
        p.add_argument("--collection", type=str, default=default,
                       help=f"Collection name (default: {default_help})")

    def add_db_backend_flag(p):
        p.add_argument("--db-backend", type=str, default=DEFAULT_DB_BACKEND,
                        choices=["chroma", "qdrant"],
                        help=f"Vector DB backend (default: {DEFAULT_DB_BACKEND})")

    def add_structure_profile_flag(p):
        p.add_argument(
            "--structure-profile",
            choices=_document_profiles.profile_names(),
            default=DEFAULT_STRUCTURE_PROFILE,
            help=("Reviewed document-layout policy (default: "
                  f"{DEFAULT_STRUCTURE_PROFILE})"),
        )

    def add_db_lock_flag(p):
        p.add_argument(
            "--db-lock-timeout", type=float,
            default=DEFAULT_DB_LOCK_TIMEOUT,
            help=("Seconds to wait for exclusive local vector-store access "
                  f"(default: {DEFAULT_DB_LOCK_TIMEOUT:g})"))

    def add_operation_timeout_flag(p, command: str):
        default = DEFAULT_OPERATION_TIMEOUTS[command]
        p.add_argument(
            "--operation-timeout", type=float, default=default,
            help=("Maximum wall-clock seconds for the isolated command worker "
                  f"(default: {default:g})"))

    def add_run_telemetry_flags(p):
        p.add_argument(
            "--run-id", default=None,
            help="Opaque correlation ID (default: generated UUID)")
        p.add_argument(
            "--run-events", type=Path, default=None,
            help="Write content-free stage events as private JSONL")
        p.add_argument(
            "--run-report", type=Path, default=None,
            help="Write a content-free aggregate run report as private JSON")

    def add_watermark_flag(p):
        p.add_argument("--watermark", type=str, default=DEFAULT_WATERMARK,
                        help="Watermark regex to strip (empty string to disable)")

    def add_ocr_flag(p):
        p.add_argument(
            "--ocr",
            action=argparse.BooleanOptionalAction,
            default=None,
            help="Enable/disable OCR (default: detect from PDF text quality)",
        )

    def add_chunk_llm_flags(p):
        p.add_argument("--llm-classify", action="store_true",
                        help="Use LLM for content classification")
        p.add_argument("--zeroshot-classify", action="store_true",
                        help="Use BART zero-shot classifier (local GPU, fast)")
        p.add_argument("--contextualize", action="store_true",
                        help="Generate contextual retrieval prefixes via LLM")

    def add_table_retrieval_flag(p):
        p.add_argument(
            "--table-children", action="store_true",
            help=("Index one caption/header-propagated retrieval child per row "
                  "for eligible large Markdown tables"),
        )

    def add_llm_provider_flags(p):
        add_release_security_flags(p)
        p.add_argument("--ollama-url", type=_cloud_endpoint_arg,
                        default=DEFAULT_OLLAMA_URL,
                        help=f"Ollama API URL (default: {DEFAULT_OLLAMA_URL})")
        p.add_argument("--ollama-model", type=str, default=DEFAULT_OLLAMA_MODEL,
                        help=f"Ollama model (default: {DEFAULT_OLLAMA_MODEL})")
        p.add_argument("--cloud-url", "--llm-url", dest="cloud_url",
                        type=_cloud_endpoint_arg, default=DEFAULT_CLOUD_URL,
                        help=f"OpenAI-compatible API URL (default: {DEFAULT_CLOUD_URL})")
        p.add_argument("--cloud-model", "--llm-model", dest="cloud_model",
                        type=str, default=DEFAULT_CLOUD_MODEL,
                        help=f"Cloud model name (default: {DEFAULT_CLOUD_MODEL})")
        p.add_argument("--cloud-key", "--api-key", dest="cloud_key",
                        type=str, default="",
                        help="Hand-entered API key (prefer a provider env var)")
        p.add_argument("--llm-workers", type=int, default=DEFAULT_LLM_WORKERS,
                        help=f"Parallel workers for cloud LLM calls (default: {DEFAULT_LLM_WORKERS})")
        p.add_argument("--gemini-key", type=str, default="",
                        help="Gemini API key (or set GEMINI_API_KEY env var)")
        p.add_argument(
            "--thinking", action=argparse.BooleanOptionalAction, default=False,
            help=("Use provider reasoning mode (DeepSeek, MiniMax M3, Gemini, "
                  "or Ollama); --no-thinking selects its minimal/disabled mode"))
        p.add_argument(
            "--llm-cache-mode", default=None,
            choices=["readwrite", "readonly", "refresh", "off"],
            help=("LLM response cache policy (release default: off; "
                  "development default: readwrite; refresh bypasses reads "
                  "and replaces successful entries)"))
        p.add_argument(
            "--llm-cache-dir", type=Path, default=None,
            help="Persistent LLM response cache directory")
        p.add_argument(
            "--llm-events", type=Path, default=None,
            help="Append prompt-free per-request LLM events as JSONL")
        p.add_argument(
            "--llm-report", type=Path, default=None,
            help="Write a prompt-free aggregate LLM run report as JSON")
        p.add_argument(
            "--llm-fallback", default="ordered", choices=["ordered", "none"],
            help="Try configured providers in order, or only the first")
        p.add_argument(
            "--llm-failure-policy", default="best-effort",
            choices=["best-effort", "strict"],
            help="Continue on unavailable LLM output, or fail the operation")
        p.add_argument(
            "--max-llm-calls", type=int, default=None,
            help="Hard cap on logical provider dispatches for this run")
        p.add_argument(
            "--max-llm-transport-attempts", type=int, default=None,
            help=("Hard cap on physical provider transport admissions, "
                  "including retries"))
        p.add_argument(
            "--max-llm-reserved-tokens", type=int, default=None,
            help=("Hard cap on estimated prompt plus maximum-output tokens "
                  "reserved across provider dispatches"))

    # preprocess
    p_pre = sub.add_parser("preprocess",
                           help="Strip background scan images from PDF")
    p_pre.add_argument("--pdf", type=Path, required=True)
    p_pre.add_argument("-o", "--out", type=Path, default=DEFAULT_PREPROCESSED_PATH)
    p_pre.add_argument("--min-dim", type=int, default=1000,
                        help="Min pixel dimension to strip (default: 1000)")
    p_pre.add_argument("--analyze", action="store_true",
                        help="Analyze only, don't modify")
    p_pre.add_argument("--force", action="store_true",
                        help="Overwrite existing output")

    # convert
    p_conv = sub.add_parser("convert", help="PDF to DoclingDocument")
    p_conv.add_argument("--pdf", type=Path, required=True)
    p_conv.add_argument("--out", type=Path, default=DEFAULT_DOC_PATH)
    p_conv.add_argument("--batch-size", type=int, default=None,
                        help="Override GPU layout_batch_size (auto by VRAM)")
    p_conv.add_argument("--backend", default="pypdfium2",
                        choices=["pypdfium2", "auto"],
                        help="PDF backend ('pypdfium2' avoids std::bad_alloc)")
    p_conv.add_argument("--force", action="store_true",
                        help="Overwrite existing output")
    p_conv.add_argument("--no-preprocess", action="store_true",
                        help="Skip auto-detection of background scan images")
    add_watermark_flag(p_conv)
    add_ocr_flag(p_conv)

    # chunk
    p_chunk = sub.add_parser("chunk", help="DoclingDocument to enriched chunks")
    p_chunk.add_argument("--doc", type=Path, default=DEFAULT_DOC_PATH)
    p_chunk.add_argument("--out", type=Path, default=DEFAULT_CHUNKS_PATH)
    p_chunk.add_argument(
        "--source-pdf", type=Path, default=None,
        help=("Exact original PDF for hash-verified table recovery; requires "
              "a conversion-v2 completion manifest"))
    p_chunk.add_argument("--max-tokens", type=int, default=DEFAULT_MAX_TOKENS,
                         help=f"Max tokens per chunk (default: {DEFAULT_MAX_TOKENS})")
    p_chunk.add_argument("--min-words", type=int, default=MIN_CHUNK_WORDS,
                         help=f"Min words to keep a chunk (default: {MIN_CHUNK_WORDS})")
    p_chunk.add_argument("--dedup-threshold", type=float, default=DEDUP_THRESHOLD,
                         help=f"Dedup Jaccard threshold 0-1 (default: {DEDUP_THRESHOLD})")
    add_embedding_flags(p_chunk)
    add_structure_profile_flag(p_chunk)
    add_watermark_flag(p_chunk)
    add_chunk_llm_flags(p_chunk)
    add_table_retrieval_flag(p_chunk)
    add_llm_provider_flags(p_chunk)
    p_chunk.add_argument("--reconstruct-headings", action="store_true",
                         help="Use LLM to reconstruct low-quality section headings")
    p_chunk.add_argument("--quality-score", action="store_true",
                         help="Use LLM to score chunk quality (1-5)")
    p_chunk.add_argument(
        "--llm-scaffold", action="store_true",
        help="Use LLM review while building and validating the TOC scaffold")

    # index
    p_idx = sub.add_parser("index", help="Chunks to vector DB")
    p_idx.add_argument("--chunks", type=Path, default=DEFAULT_CHUNKS_PATH)
    p_idx.add_argument("--db", type=Path, default=None,
                        help="DB directory (auto-set per backend)")
    add_collection_flag(p_idx)
    add_embedding_flags(p_idx)
    add_db_backend_flag(p_idx)
    add_db_lock_flag(p_idx)
    add_operation_timeout_flag(p_idx, "index")
    p_idx.add_argument("--full-reindex", action="store_true",
                        help="Force complete rebuild (skip incremental)")

    # extract-questions
    p_eq = sub.add_parser("extract-questions",
                          help="Extract Q&A pairs from Notes & Questions")
    p_eq.add_argument("--chunks", type=Path, default=DEFAULT_CHUNKS_PATH)
    p_eq.add_argument("-o", "--out", type=Path, default=Path("output/questions.jsonl"))

    # generate-questions (LLM-generated exam questions — different from extract-questions)
    p_genq = sub.add_parser("generate-questions",
                             help="Generate exam questions via LLM")
    p_genq.add_argument("--chunks", type=Path, default=DEFAULT_CHUNKS_PATH)
    p_genq.add_argument("-o", "--out", type=Path,
                         default=Path("output/exam_questions.jsonl"))
    add_llm_provider_flags(p_genq)

    # citations
    p_cg = sub.add_parser("citations",
                          help="Build citation graph from chunk cross-references")
    p_cg.add_argument("--chunks", type=Path, default=DEFAULT_CHUNKS_PATH)
    p_cg.add_argument("-o", "--out", type=Path, default=Path("output/citations.json"))

    # raptor
    p_rap = sub.add_parser("raptor",
                           help="Build RAPTOR tree (recursive summaries)")
    p_rap.add_argument("--chunks", type=Path, default=DEFAULT_CHUNKS_PATH)
    p_rap.add_argument("-o", "--out", type=Path, default=None,
                       help="Output path (default: {chunks}_raptor.json)")
    add_embedding_flags(p_rap)
    add_llm_provider_flags(p_rap)

    # brief
    p_brief = sub.add_parser("brief", help="Generate case briefs via LLM")
    p_brief.add_argument("--chunks", type=Path, default=DEFAULT_CHUNKS_PATH)
    p_brief.add_argument("-o", "--out", type=Path, default=Path("output/briefs.jsonl"))
    add_llm_provider_flags(p_brief)

    # query
    p_q = sub.add_parser("query", help="Query the index")
    p_q.add_argument("query_text", type=str, help="Search query")
    p_q.add_argument("--db", type=Path, default=None,
                     help="DB directory (auto-set per backend)")
    p_q.add_argument("-n", type=int, default=5, help="Number of results")
    p_q.add_argument("--type", type=str, default=None, choices=CONTENT_TYPES)
    p_q.add_argument("--chapter", type=int, default=None)
    p_q.add_argument("--json", action="store_true", dest="output_json",
                     help="Output results as JSON")
    reranker_mode = p_q.add_mutually_exclusive_group()
    reranker_mode.add_argument(
        "--rerank", dest="reranker", action="store_const", const=True,
        default=None,
        help="Force cross-encoder reranking for every retrieval mode")
    reranker_mode.add_argument(
        "--no-rerank", dest="reranker", action="store_const", const=False,
        help="Disable cross-encoder reranking (auto reranks vector fallback only)")
    retrieval_mode = p_q.add_mutually_exclusive_group()
    retrieval_mode.add_argument(
        "--hybrid", dest="hybrid", action="store_const", const=True,
        default=None,
        help="Force BM25 + vector hybrid search (the default auto mode uses "
             "hybrid whenever its lexical artifact is available)")
    retrieval_mode.add_argument(
        "--vector-only", dest="hybrid", action="store_const", const=False,
        help="Disable lexical retrieval and use vector search only")
    p_q.add_argument("--chunks", type=Path, default=DEFAULT_CHUNKS_PATH,
                     help="Exact chunks JSONL for hybrid/context retrieval")
    p_q.add_argument(
        "--context-window", type=int, default=0,
        choices=range(MAX_CONTEXT_WINDOW + 1), metavar="N",
        help="Attach up to N preceding/following chunks per ranked hit "
             f"(default: 0; maximum: {MAX_CONTEXT_WINDOW})")
    p_q.add_argument(
        "--context-max-characters", type=int,
        default=DEFAULT_CONTEXT_MAX_CHARACTERS,
        help="Total supplementary context character budget (default: "
             f"{DEFAULT_CONTEXT_MAX_CHARACTERS})")
    p_q.add_argument(
        "--context-segment-characters", type=int,
        default=DEFAULT_CONTEXT_SEGMENT_CHARACTERS,
        help="Maximum characters retained from each neighbor (default: "
             f"{DEFAULT_CONTEXT_SEGMENT_CHARACTERS})")
    p_q.add_argument(
        "--reranker-model", default=DEFAULT_RERANKER_MODEL,
        help=f"Cross-encoder/API reranker (default: {DEFAULT_RERANKER_MODEL})")
    p_q.add_argument(
        "--overfetch", type=int, default=RERANK_OVERFETCH,
        help=f"Candidate-pool multiplier for hybrid/reranking (default: "
             f"{RERANK_OVERFETCH}; range: 1-20)")
    p_q.add_argument(
        "--rrf-k", type=int, default=DEFAULT_RRF_K,
        help=f"RRF rank constant for Chroma hybrid search (default: "
             f"{DEFAULT_RRF_K})")
    p_q.add_argument(
        "--dense-weight", type=float, default=DEFAULT_DENSE_RRF_WEIGHT,
        help=f"Dense-list weight for Chroma RRF (default: "
             f"{DEFAULT_DENSE_RRF_WEIGHT})")
    p_q.add_argument(
        "--sparse-weight", type=float, default=DEFAULT_SPARSE_RRF_WEIGHT,
        help=f"Lexical-list weight for Chroma RRF (default: "
             f"{DEFAULT_SPARSE_RRF_WEIGHT})")
    p_q.add_argument("--answer", action="store_true",
                     help="Generate LLM answer from retrieved chunks")
    add_collection_flag(p_q)
    add_embedding_flags(p_q)
    add_db_backend_flag(p_q)
    add_db_lock_flag(p_q)
    add_operation_timeout_flag(p_q, "query")
    add_llm_provider_flags(p_q)

    # export
    p_exp = sub.add_parser("export", help="Chunks to an LLM-ready export")
    p_exp.add_argument("--chunks", type=Path, default=DEFAULT_CHUNKS_PATH)
    p_exp.add_argument("-o", "--out", type=Path, default=DEFAULT_EXPORT_PATH)
    p_exp.add_argument("--include-types", type=str, nargs="+", default=None,
                        choices=CONTENT_TYPES, metavar="TYPE",
                        help="Only include these content types")
    p_exp.add_argument("--exclude-types", type=str, nargs="+",
                        default=["structural", "empty"],
                        choices=CONTENT_TYPES, metavar="TYPE",
                        help="Exclude these content types (default: structural, empty)")
    p_exp.add_argument("--chapters", type=int, nargs="+", default=None,
                        metavar="N", help="Only include these chapter numbers")
    p_exp.add_argument("--split-chapters", action="store_true",
                        help="Markdown only: write chapter files to OUT's "
                             "sibling Chapters/ directory instead of OUT")
    p_exp.add_argument("--format", type=str, default="markdown",
                        choices=["markdown", "plaintext", "flashcards"],
                        help="Export format: markdown, plaintext, or flashcards (Anki TSV)")
    add_llm_provider_flags(p_exp)

    # info
    p_info = sub.add_parser("info", help="Inspect output artifacts")
    p_info.add_argument("--db", type=Path, default=None)
    add_collection_flag(p_info)
    add_db_backend_flag(p_info)
    add_db_lock_flag(p_info)
    add_operation_timeout_flag(p_info, "info")

    # storage lifecycle and retention
    p_storage = sub.add_parser(
        "storage",
        help="Plan or apply ownership-checked private-data retention")
    storage_action = p_storage.add_mutually_exclusive_group(required=True)
    storage_action.add_argument(
        "--delete-run", metavar="RUN_NAME",
        help="Delete one manifest-owned pipeline run")
    storage_action.add_argument(
        "--prune-llm-cache", action="store_true",
        help="Prune validated LLM response-cache entries by age")
    storage_action.add_argument(
        "--prune-ui-exports", action="store_true",
        help="Prune manifest-owned UI export directories by age")
    storage_action.add_argument(
        "--purge-quarantine", action="store_true",
        help="Purge validated retention quarantine directories by age")
    storage_action.add_argument(
        "--prune-snapshot-scratch", action="store_true",
        help="Prune marker-owned scratch trees whose process is gone")
    p_storage.add_argument(
        "--output-root", type=Path, default=OUTPUT_DIR,
        help=f"Pipeline output root (default: {OUTPUT_DIR})")
    p_storage.add_argument(
        "--llm-cache-dir", type=Path, default=None,
        help="LLM response-cache root (default: runtime cache directory)")
    p_storage.add_argument(
        "--snapshot-scratch-root", type=Path, default=None,
        help="Base directory containing the private snapshot scratch root")
    p_storage.add_argument(
        "--older-than-days", type=float, default=30.0,
        help="Minimum age for prune/purge candidates (default: 30)")
    p_storage.add_argument(
        "--max-cache-bytes", type=int,
        default=_retention.DEFAULT_LLM_CACHE_MAX_BYTES,
        help="Keep validated LLM cache entries within this total size")
    p_storage.add_argument(
        "--apply", action="store_true",
        help="Execute the displayed retention plan (default: dry run)")
    p_storage.add_argument(
        "--json", action="store_true", dest="output_json",
        help="Emit the retention plan or result as JSON")
    add_db_lock_flag(p_storage)
    add_operation_timeout_flag(p_storage, "storage")

    # durable background jobs
    p_jobs = sub.add_parser(
        "jobs", help="Submit and manage durable background operations")
    job_actions = p_jobs.add_subparsers(
        dest="job_action", required=True)

    def add_job_store_flags(job_parser):
        job_parser.add_argument(
            "--job-root", type=Path,
            default=_job_runtime.DEFAULT_JOB_ROOT,
            help=("Private durable job root (default: "
                  f"{_job_runtime.DEFAULT_JOB_ROOT})"))
        job_parser.add_argument(
            "--json", action="store_true", dest="job_json",
            help="Emit a redacted JSON response")

    p_job_submit = job_actions.add_parser(
        "submit", help="Persist and launch one allowed operation")
    add_job_store_flags(p_job_submit)
    p_job_submit.add_argument(
        "--timeout", type=float, default=None,
        help="Manager wall-clock deadline in seconds")
    p_job_submit.add_argument(
        "--ready-timeout", type=float, default=10.0,
        help="Seconds to wait for the detached-manager handshake")
    p_job_submit.add_argument(
        "job_command", nargs=argparse.REMAINDER,
        help="Command after '--', for example: -- full --pdf Book.pdf")

    p_job_list = job_actions.add_parser(
        "list", help="List redacted durable job summaries")
    add_job_store_flags(p_job_list)

    p_job_status = job_actions.add_parser(
        "status", help="Reconcile and show one durable job")
    add_job_store_flags(p_job_status)
    p_job_status.add_argument("job_id")

    p_job_cancel = job_actions.add_parser(
        "cancel", help="Request attempt-bound process-tree cancellation")
    add_job_store_flags(p_job_cancel)
    p_job_cancel.add_argument("job_id")
    p_job_cancel.add_argument(
        "--wait", action="store_true",
        help="Wait briefly for a terminal state")
    p_job_cancel.add_argument(
        "--wait-timeout", type=float, default=30.0,
        help="Maximum cancellation wait in seconds (default: 30)")

    p_job_resume = job_actions.add_parser(
        "resume", help="Launch a new explicit attempt from a terminal job")
    add_job_store_flags(p_job_resume)
    p_job_resume.add_argument("job_id")
    p_job_resume.add_argument(
        "--ready-timeout", type=float, default=10.0,
        help="Seconds to wait for the detached-manager handshake")

    p_job_delete = job_actions.add_parser(
        "delete", help="Plan or apply deletion of one safely terminal job")
    add_job_store_flags(p_job_delete)
    p_job_delete.add_argument("job_id", help="32-character job ID")
    p_job_delete.add_argument(
        "--apply", action="store_true",
        help="Execute the displayed deletion plan (default: dry run)")

    # full pipeline
    p_full = sub.add_parser("full", help="End-to-end: PDF to queryable index")
    p_full.add_argument("--pdf", type=Path, required=True)
    p_full.add_argument("--batch-size", type=int, default=None)
    p_full.add_argument("--backend", default="pypdfium2",
                        choices=["pypdfium2", "auto"])
    p_full.add_argument("--force", action="store_true")
    p_full.add_argument("--no-preprocess", action="store_true",
                        help="Skip auto-detection of background scan images")
    p_full.add_argument("--max-tokens", type=int, default=DEFAULT_MAX_TOKENS)
    p_full.add_argument("--min-words", type=int, default=MIN_CHUNK_WORDS)
    p_full.add_argument("--dedup-threshold", type=float, default=DEDUP_THRESHOLD)
    p_full.add_argument("--split-chapters", action="store_true",
                        help="Also export split chapter files")
    add_collection_flag(p_full, derive_from_run=True)
    add_embedding_flags(p_full)
    add_structure_profile_flag(p_full)
    add_watermark_flag(p_full)
    add_ocr_flag(p_full)
    add_chunk_llm_flags(p_full)
    add_table_retrieval_flag(p_full)
    add_llm_provider_flags(p_full)
    add_db_backend_flag(p_full)
    add_db_lock_flag(p_full)
    add_operation_timeout_flag(p_full, "full")
    p_full.add_argument("--full-reindex", action="store_true",
                        help="Force full re-index, ignoring existing index state")
    p_full.add_argument("--raptor", action="store_true",
                        help="Build a RAPTOR summary tree")
    p_full.add_argument("--quality-score", action="store_true",
                        help="Use LLM to score chunk quality (1-5)")
    p_full.add_argument("--reconstruct-headings", action="store_true",
                        help="Use LLM to reconstruct low-quality section headings")
    p_full.add_argument(
        "--llm-scaffold", action="store_true",
        help="Use LLM review while building and validating the TOC scaffold")
    p_full.add_argument("--resume", action="store_true",
                        help="Skip already-completed stages (resume a failed run)")
    p_full.add_argument(
        "--resume-run", dest="exact_run_name", default=None,
        help=argparse.SUPPRESS)

    # batch (multiple PDFs)
    p_batch = sub.add_parser("batch",
                             help="Process multiple PDFs end-to-end")
    p_batch.add_argument("pdfs", type=Path, nargs="+",
                         help="PDF files to process")
    p_batch.add_argument("--batch-size", type=int, default=None)
    p_batch.add_argument("--backend", default="pypdfium2",
                         choices=["pypdfium2", "auto"])
    p_batch.add_argument("--force", action="store_true")
    p_batch.add_argument("--no-preprocess", action="store_true")
    p_batch.add_argument("--max-tokens", type=int, default=DEFAULT_MAX_TOKENS)
    p_batch.add_argument("--min-words", type=int, default=MIN_CHUNK_WORDS)
    p_batch.add_argument("--dedup-threshold", type=float, default=DEDUP_THRESHOLD)
    p_batch.add_argument("--split-chapters", action="store_true",
                         help="Also export split chapter files")
    add_collection_flag(p_batch, derive_from_run=True)
    add_embedding_flags(p_batch)
    add_structure_profile_flag(p_batch)
    add_watermark_flag(p_batch)
    add_ocr_flag(p_batch)
    add_chunk_llm_flags(p_batch)
    add_table_retrieval_flag(p_batch)
    add_llm_provider_flags(p_batch)
    add_db_backend_flag(p_batch)
    add_db_lock_flag(p_batch)
    add_operation_timeout_flag(p_batch, "batch")
    p_batch.add_argument("--full-reindex", action="store_true",
                         help="Force full re-index, ignoring existing index state")
    p_batch.add_argument("--raptor", action="store_true",
                         help="Build a RAPTOR summary tree")
    p_batch.add_argument("--quality-score", action="store_true",
                         help="Use LLM to score chunk quality (1-5)")
    p_batch.add_argument("--reconstruct-headings", action="store_true",
                         help="Use LLM to reconstruct low-quality section headings")
    p_batch.add_argument(
        "--llm-scaffold", action="store_true",
        help="Use LLM review while building and validating the TOC scaffold")
    p_batch.add_argument("--resume", action="store_true",
                         help="Skip already-completed stages per PDF (resume failed batch)")

    for command_parser in (
            p_pre, p_conv, p_chunk, p_idx, p_eq, p_genq, p_cg, p_rap,
            p_brief, p_q, p_exp, p_info, p_storage, p_full, p_batch):
        add_release_security_flags(command_parser)
        add_run_telemetry_flags(command_parser)

    parse_argv = list(sys.argv[1:] if argv is None else argv)
    if _cli_policy._has_ambiguous_sensitive_option(parse_argv):
        parser.error(
            "endpoint and credential options must be written in full and "
            "with exact case")
    args = parser.parse_args(parse_argv)
    try:
        security_policy = _cli_policy._release_security_policy_from_args(args)
        if (
            not security_policy.allow_inline_secrets
            and _cli_policy._argv_has_inline_secret(parse_argv)
        ):
            raise _release_security.ReleaseSecurityError(
                "release mode does not accept credential values in argv; "
                "use provider environment variables or the interactive "
                "hidden prompt"
            )
        args._release_security_policy = security_policy
        if (
            hasattr(args, "llm_cache_mode")
            and args.llm_cache_mode is None
        ):
            args.llm_cache_mode = security_policy.default_llm_cache_mode
    except (TypeError, ValueError) as exc:
        parser.error(str(exc))

    # --- Configure logging ---
    level = logging.WARNING if args.quiet else (
        logging.DEBUG if args.verbose else logging.INFO
    )
    logging.basicConfig(
        level=level,
        format="%(asctime)s [%(levelname)s] %(message)s",
        datefmt="%H:%M:%S",
        force=True,
    )
    previous_llm_config = _llm_runtime.config
    cli_run_id = getattr(args, "run_id", None)
    try:
        _run_telemetry.validate_distinct_output_paths({
            "--run-events": getattr(args, "run_events", None),
            "--run-report": getattr(args, "run_report", None),
            "--llm-events": getattr(args, "llm_events", None),
            "--llm-report": getattr(args, "llm_report", None),
        })
        run_telemetry = _run_telemetry.RunTelemetry(
            args.command or "cli",
            run_id=(cli_run_id if cli_run_id is not None
                    else os.environ.get(_RUN_ID_ENV)),
            events_path=getattr(args, "run_events", None),
            report_path=getattr(args, "run_report", None),
        )
    except (TypeError, ValueError) as exc:
        parser.error(str(exc))
    args.run_id = run_telemetry.run_id
    run_telemetry.start()
    if run_telemetry.enabled:
        log.info("Run ID: %s", run_telemetry.run_id)

    wm = None
    db_backend = getattr(args, "db_backend", DEFAULT_DB_BACKEND)
    full_reindex = getattr(args, "full_reindex", False)
    observed_command_stage = (
        args.command is not None and args.command not in {"full", "batch"})
    operation_metrics: dict[str, int | float | bool | None] = {}
    run_completion_status: str | None = None
    llm_runtime_configured = False
    worker_job_context = None

    try:
        # --- Compile watermark once ---
        if hasattr(args, "watermark"):
            wm = _compile_watermark(args.watermark)

        # --- Resolve DB path based on backend ---
        if hasattr(args, "db") and args.db is None:
            args.db = (
                DEFAULT_QDRANT_DIR if db_backend == "qdrant"
                else DEFAULT_CHROMA_DIR)

        if hasattr(args, "db_lock_timeout"):
            try:
                args.db_lock_timeout = _normalize_db_lock_timeout(
                    args.db_lock_timeout)
            except ValueError as exc:
                parser.error(str(exc))
        if hasattr(args, "operation_timeout"):
            try:
                args.operation_timeout = _normalize_operation_timeout(
                    args.operation_timeout)
            except ValueError as exc:
                parser.error(str(exc))
        if args.command in _job_runtime.ALLOWED_JOB_COMMANDS:
            worker_job_context = _job_runtime.load_worker_context(
                args.command)
        llm_kwargs = (
            _llm_kwargs_from_args(args, include_workers=True)
            if _namespace_uses_llm(args) else {}
        )
        if args.command in {"chunk", "query"}:
            # These operations can use embeddings/rerankers even when no LLM
            # generation feature is selected.  Carry the same immutable
            # policy to those boundaries and to chunk provenance.
            llm_kwargs.setdefault("security_policy", security_policy)
        _configure_llm_runtime_from_args(args)
        llm_runtime_configured = True

        if observed_command_stage:
            run_telemetry.stage_started(args.command)
        # Validate API keys early (before expensive processing)
        if hasattr(args, "embedding_model"):
            if args.embedding_model.startswith(
                    _API_EMBEDDING_MODEL_PREFIXES):
                _release_security.require_cloud_egress(
                    security_policy, feature="cloud embedding")
            _validate_api_key(args.embedding_model)
        if (
            getattr(args, "reranker", False) is not False
            and getattr(args, "reranker_model", "").startswith(
                ("cohere-rerank", "jina-reranker"))
        ):
            _release_security.require_cloud_egress(
                security_policy, feature="cloud reranking")

        if args.command == "preprocess":
            preprocess_pdf(args.pdf, args.out,
                           min_dim=args.min_dim,
                           force=args.force,
                           analyze_only=args.analyze)

        elif args.command == "convert":
            convert_pdf(args.pdf, args.out,
                        batch_size_override=args.batch_size,
                        backend=args.backend,
                        force=args.force,
                        watermark=wm,
                        auto_preprocess=not args.no_preprocess,
                        ocr=getattr(args, "ocr", None),
                        security_policy=security_policy)

        elif args.command == "chunk":
            chunk_document(args.doc, args.out,
                           source_pdf_path=args.source_pdf,
                           embedding_model=args.embedding_model,
                           max_tokens=args.max_tokens,
                           min_words=args.min_words,
                           dedup_threshold=args.dedup_threshold,
                           watermark=wm,
                           llm_classify=args.llm_classify,
                           zeroshot_classify=args.zeroshot_classify,
                           contextualize=args.contextualize,
                           reconstruct_headings=args.reconstruct_headings,
                           quality_score=args.quality_score,
                           llm_scaffold=args.llm_scaffold,
                           table_children=args.table_children,
                           structure_profile=args.structure_profile,
                           **llm_kwargs)

        elif args.command == "index":
            index_outcome = _index_chunks_for_backend(
                args.chunks,
                args.db,
                db_backend=db_backend,
                collection_name=args.collection,
                embedding_model=args.embedding_model,
                full_reindex=full_reindex,
                security_policy=security_policy,
                lock_timeout=args.db_lock_timeout,
                _operation_observer=operation_metrics.update,
            )
            operation_metrics.update(index_outcome.telemetry_metrics())

        elif args.command == "extract-questions":
            extract_questions(args.chunks, args.out)

        elif args.command == "generate-questions":
            generate_exam_questions(
                args.chunks, args.out, **llm_kwargs)

        elif args.command == "citations":
            build_citation_graph(args.chunks, args.out)

        elif args.command == "raptor":
            raptor_out = args.out or args.chunks.with_name(
                args.chunks.stem.replace("_chunks", "") + "_raptor.json")
            build_raptor_tree(
                args.chunks, raptor_out,
                embedding_model=args.embedding_model,
                **llm_kwargs)

        elif args.command == "brief":
            generate_briefs(args.chunks, args.out, **llm_kwargs)

        elif args.command == "query":
            _query_index_for_backend(args.query_text, args.db,
                      db_backend=db_backend,
                      n_results=args.n,
                      content_type=args.type,
                      chapter_num=args.chapter,
                      collection_name=args.collection,
                      embedding_model=args.embedding_model,
                      output_json=args.output_json,
                      use_reranker=args.reranker,
                      hybrid=args.hybrid,
                      chunks_path=args.chunks,
                      reranker_model=args.reranker_model,
                      overfetch=args.overfetch,
                      rrf_k=args.rrf_k,
                      dense_weight=args.dense_weight,
                      sparse_weight=args.sparse_weight,
                      context_window=args.context_window,
                      context_max_characters=args.context_max_characters,
                      context_segment_characters=(
                          args.context_segment_characters),
                      lock_timeout=args.db_lock_timeout,
                      answer=args.answer,
                      **llm_kwargs)

        elif args.command == "export":
            export_markdown(args.chunks, args.out,
                            include_types=args.include_types,
                            exclude_types=args.exclude_types,
                            chapters=args.chapters,
                            split_chapters=args.split_chapters,
                            format=args.format,
                            **llm_kwargs)

        elif args.command == "info":
            show_info(
                args.db, args.collection,
                db_backend=db_backend,
                lock_timeout=args.db_lock_timeout)

        elif args.command == "storage":
            operation_metrics.update(_run_storage_command(args))

        elif args.command == "jobs":
            import job_manager as _job_manager

            if hasattr(args, "wait_timeout"):
                try:
                    args.wait_timeout = _normalize_operation_timeout(
                        args.wait_timeout)
                except ValueError as exc:
                    parser.error(str(exc))
            try:
                operation_metrics.update(_run_jobs_command(args))
            except _job_manager.JobManagerError as exc:
                raise _job_runtime.JobRuntimeError(str(exc)) from exc

        elif args.command == "full":
            resume = getattr(args, "resume", False)
            (effective_resume, exact_run_name, allocation_callback,
             _binding) = _background_pipeline_plan(
                worker_job_context, args.pdf, 1,
                fallback_resume=resume,
                fallback_exact_run_name=getattr(
                    args, "exact_run_name", None),
            )
            try:
                paths, run = _run_pipeline_job(
                    args.pdf, args, resume=effective_resume, watermark=wm,
                    announce=True, telemetry=run_telemetry,
                    exact_run_name=exact_run_name,
                    on_run_allocated=allocation_callback)
                _mark_background_pipeline_binding(
                    worker_job_context, args.pdf, 1, "complete")
            except BaseException as exc:
                _mark_background_pipeline_binding(
                    worker_job_context, args.pdf, 1, "failed",
                    primary_error=exc)
                if not isinstance(exc, _PipelineStageError):
                    raise
                log.error(f"Pipeline failed at stage '{exc.stage}': {exc.cause}")
                resume_cmd = _build_resume_cmd(args.pdf, args)
                log.error("Resume from where it failed with:")
                log.error(f"  {resume_cmd}")
                sys.exit(1)

            collection = run["collection"]
            db_dir = run["db_dir"]
            log.info("=== PIPELINE COMPLETE ===")
            log.info(f"  DoclingDocument: {paths['doc']}")
            log.info(f"  Docling Markdown:{paths['converted_markdown']}")
            log.info(f"  Chunks JSONL:    {paths['chunks']}")
            log.info(f"  Quality report:  {paths['quality_report']}")
            log.info(f"  Unified MD:      {paths['export']}")
            if args.split_chapters:
                log.info(f"  Chapters:        {paths['chapters_dir']}/")
            log.info(f"  Vector DB:       {db_dir}")
            log.info(f"  Collection:      {collection}")
            log.info(f'Query: python rag.py query "search terms" '
                     f'--chunks "{paths["chunks"]}" --db "{db_dir}" '
                     f'--collection {collection} --db-backend {db_backend} '
                     f'--embedding-model {args.embedding_model}')

        elif args.command == "batch":
            resume = getattr(args, "resume", False)
            results = []
            total = len(args.pdfs)
            run_telemetry.stage_started(
                "batch", metrics={"total_items": total})
            for idx, pdf in enumerate(args.pdfs, 1):
                log.info(f"\n{'='*60}")
                log.info(f"  BATCH [{idx}/{total}]: {pdf.name}")
                log.info(f"{'='*60}")

                (item_resume, exact_run_name, allocation_callback,
                 _binding) = _background_pipeline_plan(
                    worker_job_context, pdf, idx,
                    fallback_resume=resume)
                if not pdf.exists():
                    log.error(f"PDF not found: {pdf}")
                    missing_stage = f"item_{idx}.input"
                    run_telemetry.stage_started(missing_stage)
                    run_telemetry.stage_failed(
                        missing_stage, FileNotFoundError(pdf.name))
                    results.append({"pdf": str(pdf), "status": "SKIPPED",
                                    "reason": "file not found"})
                    continue

                t0 = time.time()
                try:
                    paths, run = _run_pipeline_job(
                        pdf, args, resume=item_resume, watermark=wm,
                        telemetry=run_telemetry, stage_scope=f"item_{idx}",
                        exact_run_name=exact_run_name,
                        on_run_allocated=allocation_callback)
                    _mark_background_pipeline_binding(
                        worker_job_context, pdf, idx, "complete")

                    elapsed = time.time() - t0
                    results.append({
                        "pdf": str(pdf),
                        "status": "OK",
                        "time": f"{elapsed:.0f}s",
                        "output": str(paths["export"]),
                        "collection": run["collection"],
                        "db": str(run["db_dir"]),
                    })

                except BaseException as exc:
                    _mark_background_pipeline_binding(
                        worker_job_context, pdf, idx, "failed",
                        primary_error=exc)
                    if not isinstance(exc, _PipelineStageError):
                        raise
                    elapsed = time.time() - t0
                    log.error(
                        f"Failed on {pdf.name} at stage '{exc.stage}': {exc.cause}")
                    resume_cmd = _build_resume_cmd(pdf, args)
                    log.error("  Resume this PDF with:")
                    log.error(f"    {resume_cmd}")
                    results.append({
                        "pdf": str(pdf),
                        "status": "FAILED",
                        "reason": f"{exc.stage}: {exc.cause}"[:100],
                        "time": f"{elapsed:.0f}s",
                    })
                    if isinstance(exc.cause, LLMBudgetExceeded):
                        log.error(
                            "LLM budget exhausted; stopping the remaining batch")
                        break

            # Summary
            log.info(f"\n{'='*60}")
            log.info(f"  BATCH COMPLETE: {total} PDFs")
            log.info(f"{'='*60}")
            ok = sum(1 for r in results if r["status"] == "OK")
            failed = sum(1 for r in results if r["status"] == "FAILED")
            skipped = sum(1 for r in results if r["status"] == "SKIPPED")
            remaining = total - len(results)
            log.info(f"  OK: {ok}  Failed: {failed}  Skipped: {skipped}")
            for r in results:
                status = r["status"]
                name = Path(r["pdf"]).name
                if status == "OK":
                    log.info(f"  [{status}] {name} ({r['time']}) "
                             f"-> {r['output']}")
                elif status == "FAILED":
                    log.info(f"  [{status}] {name} — {r.get('reason', '')}")
                    log.info(f"           Resume: {_build_resume_cmd(Path(r['pdf']), args)}")
                else:
                    log.info(f"  [{status}] {name} — {r.get('reason', '')}")
            run_telemetry.stage_finished("batch", metrics={
                "total_items": total,
                "processed_items": len(results),
                "succeeded_items": ok,
                "failed_items": failed,
                "skipped_items": skipped,
                "remaining_items": remaining,
            })
            if failed or skipped or remaining:
                run_completion_status = "partial"

        else:
            if args.command is None:
                parser.print_help()
            else:
                log.error(f"Unknown command: {args.command}")
                sys.exit(1)

        if observed_command_stage:
            run_telemetry.stage_finished(
                args.command, metrics=operation_metrics)

    except VectorStoreBusyError as exc:
        if observed_command_stage:
            run_telemetry.stage_failed(
                args.command, exc, metrics=operation_metrics or None)
        run_telemetry.terminate_active_stages("failed", exc)
        log.error(str(exc))
        sys.exit(1)
    except SystemExit as exc:
        cancelled = exc.code == 130
        if observed_command_stage:
            if cancelled:
                run_telemetry.stage_cancelled(
                    args.command, exc, metrics=operation_metrics or None)
            else:
                run_telemetry.stage_failed(
                    args.command, exc, metrics=operation_metrics or None)
        run_telemetry.terminate_active_stages(
            "cancelled" if cancelled else "failed", exc)
        raise
    except KeyboardInterrupt as exc:
        if observed_command_stage:
            run_telemetry.stage_cancelled(
                args.command, exc, metrics=operation_metrics or None)
        run_telemetry.terminate_active_stages("cancelled", exc)
        print("\nInterrupted.", file=sys.stderr)
        sys.exit(130)
    except (_job_runtime.JobRuntimeError,
            _retention.RetentionError,
            _storage_policy.StoragePolicyError) as exc:
        if observed_command_stage:
            run_telemetry.stage_failed(
                args.command, exc, metrics=operation_metrics or None)
        run_telemetry.terminate_active_stages("failed", exc)
        log.error(str(exc))
        sys.exit(1)
    except ImportError as e:
        if observed_command_stage:
            run_telemetry.stage_failed(
                args.command, e, metrics=operation_metrics or None)
        run_telemetry.terminate_active_stages("failed", e)
        mod = str(e).split("'")[1] if "'" in str(e) else str(e)
        log.error(f"Missing dependency: {mod}")
        log.error(f"  pip install {mod}")
        sys.exit(1)
    except Exception as exc:
        if observed_command_stage:
            run_telemetry.stage_failed(
                args.command, exc, metrics=operation_metrics or None)
        run_telemetry.terminate_active_stages("failed", exc)
        raise
    finally:
        run_exception = sys.exc_info()[1]
        try:
            if llm_runtime_configured:
                report_path = _llm_runtime.write_report()
                if report_path is not None:
                    log.info(f"LLM run report -> {report_path}")
        except Exception as exc:
            log.warning(f"Could not write LLM run report: {exc}")
        finally:
            try:
                if llm_runtime_configured:
                    llm_payload = _llm_runtime.report_payload()
                    counts = llm_payload["counts"]
                    if counts.get("requests", 0):
                        latency = llm_payload["latency_ms"]
                        run_telemetry.stage_observation("llm", metrics={
                            "requests": counts["requests"],
                            "succeeded": counts["succeeded"],
                            "failed": counts["failed"],
                            "provider_calls": counts["provider_calls"],
                            "transport_attempts": counts[
                                "transport_attempts"],
                            "transport_retries": counts[
                                "transport_retries"],
                            "exact_prompt_tokens": counts[
                                "exact_prompt_tokens"],
                            "exact_completion_tokens": counts[
                                "exact_completion_tokens"],
                            "estimated_prompt_tokens": counts[
                                "estimated_prompt_tokens"],
                            "estimated_completion_tokens": counts[
                                "estimated_completion_tokens"],
                            "latency_p50_ms": latency["p50"],
                            "latency_p95_ms": latency["p95"],
                        })
                if run_exception is None and run_completion_status is not None:
                    run_telemetry.finish(run_completion_status)
                else:
                    run_telemetry.finish_from_exception(run_exception)
                if run_telemetry.report_path is not None:
                    log.info(
                        "Run report -> %s", run_telemetry.report_path)
            except Exception as exc:
                log.warning("Could not finalize run telemetry: %s", exc)
                if run_exception is None:
                    raise
            finally:
                _llm_runtime.configure(previous_llm_config)


def _menu_choose(prompt: str, options: list[tuple[str, str]],
                  allow_skip: bool = False) -> str | None:
    """Display a numbered menu and return the chosen value."""
    print(f"\n  {prompt}")
    print("  " + "-" * 50)
    for i, (value, label) in enumerate(options, 1):
        print(f"  {i}. {label}")
    if allow_skip:
        print("  0. Skip")
    print()
    while True:
        try:
            raw = input("  Enter choice: ").strip()
            if not raw:
                continue
            n = int(raw)
            if allow_skip and n == 0:
                return None
            if 1 <= n <= len(options):
                return options[n - 1][0]
            print(f"  Please enter 1-{len(options)}" +
                  (" or 0 to skip" if allow_skip else ""))
        except KeyboardInterrupt:
            print("\n  Cancelled.")
            sys.exit(0)
        except (ValueError, EOFError):
            print("  Enter a number.")


def _menu_yesno(prompt: str, default: bool = False) -> bool:
    """Ask a yes/no question."""
    suffix = " [Y/n]: " if default else " [y/N]: "
    try:
        raw = input(f"  {prompt}{suffix}").strip().lower()
    except EOFError:
        return default
    if not raw:
        return default
    return raw.startswith("y")


def _menu_file(prompt: str, extension: str = "", default: Path | None = None,
               allow_empty: bool = False) -> str:
    """Prompt for a file path with validation and directory listing on failure.

    Loops until a valid file is provided, showing matching files in the
    directory when the path is wrong. Returns the validated path string,
    or empty string if allow_empty and user presses Enter.
    """
    while True:
        suffix = f" (Enter for {default.name})" if default else ""
        try:
            raw = input(f"\n  {prompt}{suffix}: ").strip().strip('"')
        except (KeyboardInterrupt, EOFError):
            print("\n  Cancelled.")
            sys.exit(0)

        if not raw:
            if allow_empty and default:
                if default.exists():
                    return str(default)
                # Default doesn't exist — show what's available
                print(f"  Default not found: {default}")
            elif allow_empty:
                return ""
            else:
                print("  A file path is required.")
                continue

        path = Path(raw)
        if path.is_file():
            return str(path)
        if path.is_dir():
            print(f"  That is a directory, not a file: {path}")

        # File not found — help the user
        print(f"  File not found: {path}")

        # Show matching files in the directory
        parent = path.parent if path.parent.exists() else Path(".")
        ext = extension or path.suffix or ""

        if ext:
            matches = sorted(parent.glob(f"*{ext}"))
        else:
            matches = sorted(f for f in parent.iterdir() if f.is_file())

        if matches:
            print(f"\n  Files in {parent}/:")
            for i, m in enumerate(matches[:15], 1):
                size = m.stat().st_size / 1e6
                print(f"    {i:2d}. {m.name}  ({size:.1f} MB)")
            if len(matches) > 15:
                print(f"    ... and {len(matches) - 15} more")

            # Let user pick from the list
            print()
            try:
                pick = input("  Enter number to select, or retype path: ").strip()
            except (KeyboardInterrupt, EOFError):
                print("\n  Cancelled.")
                sys.exit(0)

            if pick.isdigit() and 1 <= int(pick) <= len(matches):
                return str(matches[int(pick) - 1])
            elif pick:
                # They retyped a path — loop back
                path = Path(pick.strip('"'))
                if path.is_file():
                    return str(path)
                print(f"  Still not found: {path}")
        else:
            print(f"  No {ext or 'files'} found in {parent}/")

        print("  Please try again.")


_menu_args_use_llm = _cli_policy._menu_args_use_llm


def _menu_cloud_consent_args(
        feature: str, *, already_allowed: bool = False,
        ) -> list[str] | None:
    """Return explicit cloud consent flags, or ``None`` when declined."""
    if already_allowed:
        return []
    if not _menu_yesno(
            f"Allow this operation to send {feature} to a cloud provider?",
            default=False):
        print("  Cloud egress was not enabled. Operation cancelled.")
        return None
    return ["--network-policy", "allow-cloud"]


def _menu_cache_namespace_args() -> list[str]:
    """Collect a validated nonsecret trust/tenant label for a custom host."""
    while True:
        namespace = input(
            "  Nonsecret custom-gateway trust/tenant label: ").strip()
        try:
            _release_security.cache_namespace_identity(namespace)
        except (TypeError, ValueError) as exc:
            print(f"  Invalid label: {exc}")
            continue
        return ["--llm-cache-namespace", namespace]


def _menu_llm_provider_args(*, cloud_already_allowed: bool = False,
                            ) -> list[str] | None:
    """Collect provider settings, including a securely typed API key."""
    provider = _menu_choose("LLM provider:", [
        ("automatic", "Automatic fallback chain (environment/defaults)"),
        ("deepseek-v4-pro", "DeepSeek V4 Pro API"),
        ("deepseek-v4-flash", "DeepSeek V4 Flash API"),
        ("minimax", "MiniMax OpenAI-compatible API"),
        ("ollama", "Local Ollama"),
        ("gemini", "Google Gemini fallback"),
        ("custom", "Custom OpenAI-compatible API"),
    ])
    provider_args: list[str] = []

    if provider == "automatic":
        if cloud_already_allowed or _menu_yesno(
                "Allow automatic fallback to reviewed cloud providers?",
                default=False):
            if not cloud_already_allowed:
                provider_args.extend(["--network-policy", "allow-cloud"])
        else:
            print("  Automatic fallback will remain local-only.")
        if _menu_yesno(
                "Enable thinking when the selected provider supports it?",
                default=False):
            provider_args.append("--thinking")
    elif provider.startswith("deepseek-"):
        consent = _menu_cloud_consent_args(
            "private prompts", already_allowed=cloud_already_allowed)
        if consent is None:
            return None
        provider_args.extend(consent)
        provider_args.extend([
            "--llm-url", DEFAULT_DEEPSEEK_URL,
            "--llm-model", provider,
        ])
        key = getpass(
            "  DeepSeek API key (hidden; Enter for DEEPSEEK_API_KEY): "
        ).strip()
        if key:
            provider_args.extend(["--api-key", key])
        provider_args.append(
            "--thinking"
            if _menu_yesno("Enable DeepSeek thinking mode?", default=False)
            else "--no-thinking"
        )
    elif provider == "minimax":
        consent = _menu_cloud_consent_args(
            "private prompts", already_allowed=cloud_already_allowed)
        if consent is None:
            return None
        provider_args.extend(consent)
        key = getpass(
            "  MiniMax API key (hidden; Enter for MINIMAX_API_KEY): "
        ).strip()
        if key:
            provider_args.extend(["--api-key", key])
    elif provider == "ollama":
        url = input(
            f"  Ollama URL (Enter for {DEFAULT_OLLAMA_URL}): "
        ).strip() or DEFAULT_OLLAMA_URL
        endpoint = _validate_cloud_endpoint(url)
        assert endpoint is not None
        url = endpoint.base_url
        if not endpoint.is_loopback:
            consent = _menu_cloud_consent_args(
                "private prompts", already_allowed=cloud_already_allowed)
            if consent is None:
                return None
            provider_args.extend(consent)
            provider_args.extend(_menu_cache_namespace_args())
        model = input(
            f"  Ollama model (Enter for {DEFAULT_OLLAMA_MODEL}): "
        ).strip() or DEFAULT_OLLAMA_MODEL
        provider_args.extend([
            "--cloud-url", "", "--ollama-url", url,
            "--ollama-model", model,
        ])
        provider_args.append(
            "--thinking"
            if _menu_yesno("Enable model thinking?", default=False)
            else "--no-thinking"
        )
    elif provider == "gemini":
        consent = _menu_cloud_consent_args(
            "private prompts", already_allowed=cloud_already_allowed)
        if consent is None:
            return None
        provider_args.extend(consent)
        key = getpass(
            "  Gemini API key (hidden; Enter for GEMINI_API_KEY): "
        ).strip()
        provider_args.extend(["--cloud-url", "", "--ollama-url", ""])
        if key:
            provider_args.extend(["--gemini-key", key])
    elif provider == "custom":
        consent = _menu_cloud_consent_args(
            "private prompts", already_allowed=cloud_already_allowed)
        if consent is None:
            return None
        provider_args.extend(consent)
        url = input("  OpenAI-compatible API URL: ").strip()
        model = input("  Model name: ").strip()
        if not url or not model:
            raise ValueError("Custom provider requires both an API URL and model")
        endpoint = _validate_cloud_endpoint(url)
        assert endpoint is not None
        url = endpoint.base_url
        provider_args.extend(_menu_cache_namespace_args())
        key = getpass(
            "  API key (hidden; Enter for CLOUD_API_KEY): "
        ).strip()
        provider_args.extend(["--llm-url", url, "--llm-model", model])
        if key:
            provider_args.extend(["--api-key", key])
    return provider_args


_redact_cli_secrets = _cli_policy._redact_cli_secrets


def _menu_secrets_to_environment(
        args: list[str]) -> tuple[list[str], dict[str, str]]:
    """Remove hidden-prompt secrets from argv and scope them to the child."""
    return _cli_policy._menu_secrets_to_environment(
        args,
        default_cloud_url=DEFAULT_CLOUD_URL,
        is_deepseek_cloud_fn=_is_deepseek_cloud,
        is_minimax_cloud_fn=_is_minimax_cloud,
    )


def _menu_structure_profile() -> str:
    """Choose one reviewed immutable document-layout policy."""
    return _menu_choose("Document structure profile:", [
        (name, _document_profiles.get_profile(name).document_description)
        for name in _document_profiles.profile_names()
    ], default=DEFAULT_STRUCTURE_PROFILE)


def interactive_menu():
    """User-friendly guided pipeline menu."""
    print("\n" + "=" * 60)
    print("  RAG Pipeline — Interactive Mode")
    print("=" * 60)

    # Step 1: What do you want to do?
    action = _menu_choose("What would you like to do?", [
        ("full", "Process a new PDF (full pipeline)"),
        ("batch", "Process multiple PDFs"),
        ("preprocess", "Strip background scans from a PDF"),
        ("convert", "Convert PDF to DoclingDocument only"),
        ("chunk", "Re-chunk an existing DoclingDocument"),
        ("index", "Index existing chunks into a vector DB"),
        ("query", "Search the index"),
        ("export", "Export chunks to markdown / plaintext"),
        ("raptor", "Build RAPTOR tree (recursive summaries)"),
        ("brief", "Generate case briefs from case opinions"),
        ("info", "View pipeline status"),
        ("extract-questions", "Extract Q&A pairs from chunks"),
        ("generate-questions", "Generate exam questions via LLM"),
        ("citations", "Build citation graph"),
    ])

    args = []

    if action == "full":
        # PDF path
        pdf = _menu_file("Path to PDF file", extension=".pdf")
        if not pdf:
            return
        args = ["full", "--pdf", pdf]

        # Force overwrite?
        if _menu_yesno("Overwrite existing output?", default=False):
            args.append("--force")

        # Embedding model
        emb = _menu_choose("Embedding model:", [
            (DEFAULT_EMBEDDING_MODEL_LEGAL, "Voyage Law 2 — best for legal text (paid, needs VOYAGE_API_KEY)"),
            (DEFAULT_EMBEDDING_MODEL_GENERAL, "Nomic Embed v2 MoE — best free option (local GPU)"),
            ("text-embedding-3-large", "OpenAI text-embedding-3-large (paid, needs OPENAI_API_KEY)"),
        ])
        args.extend(["--embedding-model", emb])
        args.extend(["--structure-profile", _menu_structure_profile()])

        # Chunk size
        token_choices = (
            [
                (str(DEFAULT_MAX_TOKENS),
                 f"{DEFAULT_MAX_TOKENS} — default; fits Nomic's 512-token input"),
                ("256", "256 — smaller, more precise chunks"),
                ("506", "506 — maximum safe raw Nomic chunk"),
            ]
            if emb == DEFAULT_EMBEDDING_MODEL_GENERAL else
            [
                (str(DEFAULT_MAX_TOKENS),
                 f"{DEFAULT_MAX_TOKENS} — default retrieval chunks"),
                ("1024", "1024 — larger sections"),
                ("4096", "4096 — large sections"),
                ("16000", "16000 — Voyage model maximum"),
            ]
        )
        tokens = _menu_choose("Chunk size (tokens):", token_choices)
        args.extend(["--max-tokens", tokens])

        # Vector DB backend
        backend = _menu_choose("Vector database:", [
            ("chroma", "ChromaDB — simple local storage (default)"),
            ("qdrant", "Qdrant — faster hybrid search, native sparse vectors"),
        ])
        args.extend(["--db-backend", backend])

        # LLM classification?
        if _menu_yesno("Use LLM for content classification? (improves accuracy)", default=False):
            args.append("--llm-classify")

        # Contextual retrieval?
        if _menu_yesno("Generate contextual summaries per chunk? (improves retrieval)", default=False):
            args.append("--contextualize")

        if _menu_yesno(
                "Create row-level retrieval children for large tables?",
                default=False):
            args.append("--table-children")

        if _menu_yesno("Use LLM review for TOC scaffold construction?", default=False):
            args.append("--llm-scaffold")

        if _menu_yesno("Export separate chapter files?", default=False):
            args.append("--split-chapters")

        # RAPTOR?
        if _menu_yesno("Build RAPTOR tree? (multi-level retrieval)", default=False):
            args.append("--raptor")

    elif action == "preprocess":
        pdf = _menu_file("Path to PDF file", extension=".pdf")
        if not pdf:
            return
        args = ["preprocess", "--pdf", pdf]
        if _menu_yesno("Analyze only (don't modify)?", default=True):
            args.append("--analyze")

    elif action == "convert":
        pdf = _menu_file("Path to PDF file", extension=".pdf")
        if not pdf:
            return
        args = ["convert", "--pdf", pdf]
        if _menu_yesno("Overwrite existing output?", default=False):
            args.append("--force")

    elif action == "batch":
        print("\n  Add PDF files (empty line to finish):")
        pdfs = []
        while True:
            p = _menu_file(f"PDF {len(pdfs)+1}", extension=".pdf", allow_empty=True)
            if not p:
                break
            pdfs.append(p)
        if not pdfs:
            print("  No PDFs specified. Exiting.")
            return
        args = ["batch"] + pdfs

        if _menu_yesno("Overwrite existing output?", default=False):
            args.append("--force")

        emb = _menu_choose("Embedding model:", [
            (DEFAULT_EMBEDDING_MODEL_LEGAL, "Voyage Law 2 (paid)"),
            (DEFAULT_EMBEDDING_MODEL_GENERAL, "Nomic Embed v2 MoE (free, local)"),
        ])
        args.extend(["--embedding-model", emb])
        args.extend(["--structure-profile", _menu_structure_profile()])

        backend = _menu_choose("Vector database:", [
            ("chroma", "ChromaDB (default)"),
            ("qdrant", "Qdrant (faster hybrid)"),
        ])
        args.extend(["--db-backend", backend])

        if _menu_yesno("Use LLM classification?", default=False):
            args.append("--llm-classify")
        if _menu_yesno("Generate contextual summaries?", default=False):
            args.append("--contextualize")
        if _menu_yesno(
                "Create row-level retrieval children for large tables?",
                default=False):
            args.append("--table-children")
        if _menu_yesno("Use LLM review for TOC scaffolds?", default=False):
            args.append("--llm-scaffold")
        if _menu_yesno("Export separate chapter files?", default=False):
            args.append("--split-chapters")
        if _menu_yesno("Build RAPTOR trees?", default=False):
            args.append("--raptor")

    elif action == "query":
        query = input("\n  Search query: ").strip()
        if not query:
            print("  No query. Exiting.")
            return
        args = ["query", query]

        n = _menu_choose("Number of results:", [
            ("3", "3 results"), ("5", "5 results (default)"),
            ("10", "10 results"), ("20", "20 results"),
        ])
        args.extend(["-n", n])

        retrieval_mode = _menu_choose("Retrieval mode:", [
            ("auto", "Auto — hybrid when available (recommended)"),
            ("hybrid", "Hybrid — require BM25 + vector retrieval"),
            ("vector", "Vector only — disable lexical retrieval"),
        ])
        if retrieval_mode == "hybrid":
            args.append("--hybrid")
        elif retrieval_mode == "vector":
            args.append("--vector-only")

        reranker_mode = _menu_choose("Reranker mode:", [
            ("auto", "Auto — rerank vector fallback, preserve hybrid order"),
            ("on", "Always rerank — slower, may improve vector-only search"),
            ("off", "Never rerank — lowest latency"),
        ])
        if reranker_mode == "on":
            args.append("--rerank")
        elif reranker_mode == "off":
            args.append("--no-rerank")

        context_window = _menu_choose("Neighbor context:", [
            ("0", "Off — ranked chunks only (default)"),
            ("1", "One preceding/following chunk"),
            ("2", "Two preceding/following chunks"),
        ])
        if context_window != "0":
            args.extend(["--context-window", context_window])

        if _menu_yesno("Generate answer from results?", default=False):
            args.append("--answer")

        if _menu_yesno("Output as JSON?", default=False):
            args.append("--json")

        # Content type filter
        ct_filter = _menu_choose("Filter by content type?", [
            ("none", "No filter (all types)"),
            ("case_opinion", "Case opinions only"),
            ("notes_and_questions", "Notes & Questions only"),
            ("author_narrative", "Author narrative only"),
            ("statutory_excerpt", "Statutory excerpts only"),
        ])
        if ct_filter != "none":
            args.extend(["--type", ct_filter])

        # Chapter filter
        ch_input = input("  Filter by chapter number (Enter to skip): ").strip()
        if ch_input.isdigit():
            args.extend(["--chapter", ch_input])

        # DB backend
        backend = _menu_choose("Vector database:", [
            ("chroma", "ChromaDB (default)"),
            ("qdrant", "Qdrant"),
        ])
        args.extend(["--db-backend", backend])

    elif action == "export":
        args = ["export"]

        chunks = _menu_file("Chunks JSONL path", extension=".jsonl",
                            default=DEFAULT_CHUNKS_PATH, allow_empty=True)
        if chunks:
            args.extend(["--chunks", chunks])

        fmt = _menu_choose("Export format:", [
            ("markdown", "Markdown — readable, with headings and formatting"),
            ("plaintext", "Plain text + JSON metadata — for Anthropic citations API"),
            ("flashcards", "Flashcards — Anki-compatible TSV for study review"),
        ])
        args.extend(["--format", fmt])

        if fmt == "markdown" and _menu_yesno(
                "Split into separate chapter files?", default=True):
            args.append("--split-chapters")

    elif action == "index":
        args = ["index"]
        chunks = _menu_file("Chunks JSONL path", extension=".jsonl",
                            default=DEFAULT_CHUNKS_PATH, allow_empty=True)
        if chunks:
            args.extend(["--chunks", chunks])

        backend = _menu_choose("Vector database:", [
            ("chroma", "ChromaDB (default)"),
            ("qdrant", "Qdrant (faster hybrid)"),
        ])
        args.extend(["--db-backend", backend])

        emb = _menu_choose("Embedding model:", [
            (DEFAULT_EMBEDDING_MODEL_LEGAL, "Voyage Law 2 (paid)"),
            (DEFAULT_EMBEDDING_MODEL_GENERAL, "Nomic Embed v2 MoE (free)"),
        ])
        args.extend(["--embedding-model", emb])

    elif action == "raptor":
        args = ["raptor"]
        chunks = _menu_file("Chunks JSONL path", extension=".jsonl",
                            default=DEFAULT_CHUNKS_PATH, allow_empty=True)
        if chunks:
            args.extend(["--chunks", chunks])

    elif action == "brief":
        args = ["brief"]
        chunks = _menu_file("Chunks JSONL path", extension=".jsonl",
                            default=DEFAULT_CHUNKS_PATH, allow_empty=True)
        if chunks:
            args.extend(["--chunks", chunks])
        out = input("  Output path (Enter for default output/briefs.jsonl): ").strip().strip('"')
        if out:
            args.extend(["-o", out])

    elif action == "chunk":
        args = ["chunk"]
        doc = _menu_file("DoclingDocument JSON path", extension=".json",
                         default=DEFAULT_DOC_PATH, allow_empty=True)
        if doc:
            args.extend(["--doc", doc])

        tokens = _menu_choose("Chunk size:", [
            (str(DEFAULT_MAX_TOKENS),
             f"{DEFAULT_MAX_TOKENS} tokens (default; fits Nomic)"),
            ("256", "256 tokens (smaller, more precise)"),
            ("506", "506 tokens (maximum safe raw Nomic chunk)"),
        ])
        args.extend(["--max-tokens", tokens])
        args.extend(["--structure-profile", _menu_structure_profile()])

        if _menu_yesno("Classify chunks with an LLM?", default=False):
            args.append("--llm-classify")
        if _menu_yesno("Generate contextual retrieval prefixes?", default=False):
            args.append("--contextualize")
        if _menu_yesno(
                "Create row-level retrieval children for large tables?",
                default=False):
            args.append("--table-children")
        if _menu_yesno("Extract an LLM document scaffold?", default=False):
            args.append("--llm-scaffold")
        if _menu_yesno("Reconstruct missing headings with an LLM?", default=False):
            args.append("--reconstruct-headings")
        if _menu_yesno("Score chunk quality with an LLM?", default=False):
            args.append("--quality-score")

    elif action in ("info", "extract-questions", "citations"):
        args = [action]
        if action in ("extract-questions", "citations"):
            chunks = _menu_file("Chunks JSONL path", extension=".jsonl",
                                default=DEFAULT_CHUNKS_PATH, allow_empty=True)
            if chunks:
                args.extend(["--chunks", chunks])

    elif action == "generate-questions":
        args = ["generate-questions"]
        chunks = _menu_file("Chunks JSONL path", extension=".jsonl",
                            default=DEFAULT_CHUNKS_PATH, allow_empty=True)
        if chunks:
            args.extend(["--chunks", chunks])

    cloud_allowed = False
    for index, token in enumerate(args[:-1]):
        if (
            token == "--embedding-model"
            and args[index + 1].startswith(_API_EMBEDDING_MODEL_PREFIXES)
        ):
            consent = _menu_cloud_consent_args("private embedding text")
            if consent is None:
                return
            args.extend(consent)
            cloud_allowed = True
            break

    if _menu_args_use_llm(args):
        provider_args = _menu_llm_provider_args(
            cloud_already_allowed=cloud_allowed)
        if provider_args is None:
            return
        args.extend(provider_args)

    # Show the generated command
    display_args = _redact_cli_secrets(args)
    cmd = f"python rag.py {' '.join(display_args)}"
    print(f"\n  {'='*50}")
    print(f"  Running: {cmd}")
    print(f"  {'='*50}\n")

    safe_args, secret_environment = _menu_secrets_to_environment(args)

    # Run it
    original_argv = sys.argv
    try:
        sys.argv = ["rag.py"] + safe_args
        exit_code = _run_rag_entrypoint(
            safe_args, environment_overrides=secret_environment)
        if exit_code:
            print(f"  Command exited with status {exit_code}.", file=sys.stderr)
    finally:
        sys.argv = original_argv


if __name__ == "__main__":
    try:
        sys.exit(_run_rag_entrypoint())
    except KeyboardInterrupt:
        print("\nCancelled.", file=sys.stderr)
        sys.exit(130)
    except ImportError as e:
        mod = str(e).split("'")[1] if "'" in str(e) else str(e)
        print(f"Missing dependency: {mod}", file=sys.stderr)
        print(f"  pip install {mod}", file=sys.stderr)
        sys.exit(1)
    except Exception as e:
        print(f"\nError: {e}", file=sys.stderr)
        print("  Run with -v for details.", file=sys.stderr)
        sys.exit(1)
