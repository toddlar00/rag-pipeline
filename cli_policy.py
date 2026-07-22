"""Pure command-line interpretation and serialization policy.

The module is intentionally limited to Python's standard library.  Callers
inject product defaults, environment access, endpoint predicates, and runtime
mutation so parsing policy remains independent from the executable facade.
"""

from __future__ import annotations

import math
from collections.abc import Callable, Mapping
from dataclasses import dataclass
from pathlib import Path
from typing import TypedDict


EndpointPredicateFn = Callable[..., bool]
EndpointResolverFn = Callable[[object], tuple[str, str]]
CloudKeyResolverFn = Callable[..., str]
EnvironmentGetFn = Callable[[str, str], str]
NormalizeTimeoutFn = Callable[[object], float]


@dataclass(frozen=True)
class ProviderCliDefaults:
    """Provider values used when a parsed namespace omits an option."""

    cloud_url: str
    cloud_model: str
    deepseek_url: str
    deepseek_model: str
    ollama_url: str
    ollama_model: str
    llm_workers: int


@dataclass(frozen=True)
class ResumeCommandDefaults:
    """Current product defaults needed to serialize a resume command."""

    executable: str
    script_name: str
    embedding_model: str
    db_backend: str
    conversion_backend: str
    max_tokens: int
    min_words: int
    dedup_threshold: float
    db_lock_timeout: float
    full_operation_timeout: float
    llm_cache_mode: str
    llm_fallback: str
    llm_failure_policy: str
    provider: ProviderCliDefaults


class LLMCallOptions(TypedDict, total=False):
    """Provider call options derived from an argparse-like namespace."""

    cloud_url: str
    cloud_model: str
    cloud_key: str
    ollama_url: str
    ollama_model: str
    gemini_key: str
    thinking: bool
    llm_workers: int


class LLMRuntimeConfigValues(TypedDict):
    """Values used by the facade to construct its runtime configuration."""

    cache_mode: str
    cache_dir: Path | str
    events_path: Path | str | None
    report_path: Path | str | None
    run_id: str | None
    max_provider_calls: int | None
    max_reserved_tokens: int | None
    fallback_policy: str
    failure_policy: str


_SECRET_CLI_FLAGS = frozenset({
    "--cloud-key", "--api-key", "--gemini-key",
})


def _build_resume_cmd(
        pdf: Path, args: object, extra_flags: str = "", *,
        defaults: ResumeCommandDefaults) -> str:
    """Build a shell command string to resume a failed ``full`` pipeline."""
    parts = [
        defaults.executable, defaults.script_name, "full",
        "--pdf", f'"{pdf}"', "--resume",
    ]
    boolean_flags = {
        "force": "--force",
        "raptor": "--raptor",
        "no_preprocess": "--no-preprocess",
        "split_chapters": "--split-chapters",
        "full_reindex": "--full-reindex",
        "llm_classify": "--llm-classify",
        "zeroshot_classify": "--zeroshot-classify",
        "contextualize": "--contextualize",
        "reconstruct_headings": "--reconstruct-headings",
        "quality_score": "--quality-score",
        "llm_scaffold": "--llm-scaffold",
        "thinking": "--thinking",
    }
    for attr, flag in boolean_flags.items():
        if getattr(args, attr, False):
            parts.append(flag)

    ocr = getattr(args, "ocr", None)
    if ocr is True:
        parts.append("--ocr")
    elif ocr is False:
        parts.append("--no-ocr")

    embedding_model = getattr(
        args, "embedding_model", defaults.embedding_model)
    if embedding_model != defaults.embedding_model:
        parts.extend(["--embedding-model", embedding_model])
    collection = getattr(args, "collection", None)
    if collection:
        parts.extend(["--collection", collection])
    db_backend = getattr(args, "db_backend", defaults.db_backend)
    if db_backend != defaults.db_backend:
        parts.extend(["--db-backend", db_backend])
    conversion_backend = getattr(
        args, "backend", defaults.conversion_backend)
    if conversion_backend != defaults.conversion_backend:
        parts.extend(["--backend", conversion_backend])
    value_flags = (
        ("batch_size", None, "--batch-size"),
        ("max_tokens", defaults.max_tokens, "--max-tokens"),
        ("min_words", defaults.min_words, "--min-words"),
        ("dedup_threshold", defaults.dedup_threshold, "--dedup-threshold"),
        ("db_lock_timeout", defaults.db_lock_timeout, "--db-lock-timeout"),
        ("operation_timeout", defaults.full_operation_timeout,
         "--operation-timeout"),
        ("llm_workers", defaults.provider.llm_workers, "--llm-workers"),
        ("ollama_url", defaults.provider.ollama_url, "--ollama-url"),
        ("ollama_model", defaults.provider.ollama_model, "--ollama-model"),
        ("cloud_url", defaults.provider.cloud_url, "--cloud-url"),
        ("cloud_model", defaults.provider.cloud_model, "--cloud-model"),
        ("llm_cache_mode", defaults.llm_cache_mode, "--llm-cache-mode"),
        ("llm_cache_dir", None, "--llm-cache-dir"),
        ("llm_events", None, "--llm-events"),
        ("llm_report", None, "--llm-report"),
        ("llm_fallback", defaults.llm_fallback, "--llm-fallback"),
        ("llm_failure_policy", defaults.llm_failure_policy,
         "--llm-failure-policy"),
        ("max_llm_calls", None, "--max-llm-calls"),
        ("max_llm_reserved_tokens", None,
         "--max-llm-reserved-tokens"),
    )
    for attr, default, flag in value_flags:
        value = getattr(args, attr, default)
        if value is not None and value != default:
            parts.extend([
                flag, f'"{value}"' if " " in str(value) else str(value)])
    if extra_flags:
        parts.append(extra_flags)
    return " ".join(parts)


def _resolve_cloud_endpoint(
        args: object, *, defaults: ProviderCliDefaults,
        is_deepseek_cloud_fn: EndpointPredicateFn) -> tuple[str, str]:
    """Resolve URL/model shortcuts for the configured cloud provider."""
    cloud_url = getattr(args, "cloud_url", defaults.cloud_url)
    cloud_model = getattr(args, "cloud_model", defaults.cloud_model)
    if (cloud_model or "").lower().startswith("deepseek-"):
        if cloud_url == defaults.cloud_url:
            cloud_url = defaults.deepseek_url
    elif is_deepseek_cloud_fn(cloud_url):
        if cloud_model == defaults.cloud_model:
            cloud_model = defaults.deepseek_model
    return cloud_url, cloud_model


def _resolve_cloud_key(
        args: object, *, cloud_url: str = "", cloud_model: str = "",
        resolve_cloud_endpoint_fn: EndpointResolverFn,
        is_deepseek_cloud_fn: EndpointPredicateFn,
        is_minimax_cloud_fn: EndpointPredicateFn,
        environment_get_fn: EnvironmentGetFn) -> str:
    """Resolve a key without sending one provider's secret to another host."""
    explicit_key = getattr(args, "cloud_key", "")
    if explicit_key:
        return explicit_key

    if not cloud_url and not cloud_model:
        cloud_url, cloud_model = resolve_cloud_endpoint_fn(args)
    if is_deepseek_cloud_fn(cloud_url, cloud_model):
        return (
            environment_get_fn("DEEPSEEK_API_KEY", "")
            or environment_get_fn("CLOUD_API_KEY", "")
        )
    if is_minimax_cloud_fn(cloud_url):
        return (
            environment_get_fn("MINIMAX_API_KEY", "")
            or environment_get_fn("CLOUD_API_KEY", "")
        )
    return environment_get_fn("CLOUD_API_KEY", "")


def _llm_kwargs_from_args(
        args: object, *, include_workers: bool,
        defaults: ProviderCliDefaults,
        resolve_cloud_endpoint_fn: EndpointResolverFn,
        resolve_cloud_key_fn: CloudKeyResolverFn) -> LLMCallOptions:
    """Collect provider options shared by LLM-backed operations."""
    cloud_url, cloud_model = resolve_cloud_endpoint_fn(args)
    kwargs: LLMCallOptions = {
        "cloud_url": cloud_url,
        "cloud_model": cloud_model,
        "cloud_key": resolve_cloud_key_fn(
            args, cloud_url=cloud_url, cloud_model=cloud_model),
        "ollama_url": getattr(args, "ollama_url", defaults.ollama_url),
        "ollama_model": getattr(args, "ollama_model", defaults.ollama_model),
        "gemini_key": getattr(args, "gemini_key", ""),
        "thinking": getattr(args, "thinking", False),
    }
    if include_workers:
        kwargs["llm_workers"] = getattr(
            args, "llm_workers", defaults.llm_workers)
    return kwargs


def _llm_runtime_config_values_from_args(
        args: object, *,
        default_cache_dir: Path | str) -> LLMRuntimeConfigValues:
    """Map a parsed namespace to provider-neutral runtime config values."""
    cache_dir = getattr(args, "llm_cache_dir", None)
    return {
        "cache_mode": getattr(args, "llm_cache_mode", "off"),
        "cache_dir": cache_dir or default_cache_dir,
        "events_path": getattr(args, "llm_events", None),
        "report_path": getattr(args, "llm_report", None),
        "run_id": getattr(args, "run_id", None),
        "max_provider_calls": getattr(args, "max_llm_calls", None),
        "max_reserved_tokens": getattr(
            args, "max_llm_reserved_tokens", None),
        "fallback_policy": getattr(args, "llm_fallback", "ordered"),
        "failure_policy": getattr(
            args, "llm_failure_policy", "best-effort"),
    }


def _normalize_operation_timeout(
        timeout: object, *, timeout_max: float) -> float:
    """Validate a finite positive deadline accepted by process waiting APIs."""
    if isinstance(timeout, bool):
        raise ValueError(
            "operation timeout must be a finite positive number")
    try:
        value = float(timeout)
    except (TypeError, ValueError) as exc:
        raise ValueError(
            "operation timeout must be a finite positive number") from exc
    if (not math.isfinite(value) or value <= 0
            or value > timeout_max):
        raise ValueError(
            "operation timeout must be a finite positive number no greater "
            f"than {timeout_max:g} seconds")
    return value


def _cli_operation_timeout(
        argv: list[str], operation: str, *,
        operation_timeouts: Mapping[str, float],
        normalize_timeout_fn: NormalizeTimeoutFn) -> float:
    """Read the last CLI deadline without replacing argparse validation."""
    default = operation_timeouts[operation]
    raw_value = None
    if operation == "evaluation":
        command_index = -1
    else:
        try:
            command_index = argv.index(operation)
        except ValueError:
            return default
    for index in range(command_index + 1, len(argv)):
        token = argv[index]
        if token == "--":
            break
        if token == "--operation-timeout":
            raw_value = argv[index + 1] if index + 1 < len(argv) else None
        elif token.startswith("--operation-timeout="):
            raw_value = token.split("=", 1)[1]
    if raw_value is None:
        return default
    try:
        return normalize_timeout_fn(raw_value)
    except ValueError:
        return default


def _cli_run_telemetry_options(argv: list[str], operation: str) -> dict:
    """Read the last run correlation/output options after a subcommand."""
    try:
        command_index = argv.index(operation)
    except ValueError:
        return {"run_id": None, "events_path": None, "report_path": None}
    flags = {
        "--run-id": "run_id",
        "--run-events": "events_path",
        "--run-report": "report_path",
    }
    values = {name: None for name in flags.values()}
    index = command_index + 1
    while index < len(argv):
        token = argv[index]
        if token == "--":
            break
        matched = False
        for flag, name in flags.items():
            if token == flag:
                candidate = argv[index + 1] if index + 1 < len(argv) else None
                if candidate is None or candidate == "--" or candidate.startswith("-"):
                    values[name] = None
                    index += 1
                else:
                    values[name] = candidate
                    index += 2
                matched = True
                break
            if token.startswith(flag + "="):
                values[name] = token.split("=", 1)[1]
                index += 1
                matched = True
                break
        if not matched:
            index += 1
    return values


def _rag_cli_command(argv: list[str]) -> str | None:
    """Return the argparse subcommand after global flag-only options."""
    for token in argv:
        if token in {"-v", "--verbose", "--quiet", "--"}:
            continue
        if not token.startswith("-"):
            return token
    return None


def _menu_args_use_llm(args: list[str]) -> bool:
    """Return whether an interactive-menu command will invoke generation."""
    if not args:
        return False
    action = args[0]
    if action in {"raptor", "brief", "generate-questions"}:
        return True
    if action in {"chunk", "full", "batch"}:
        feature_flags = {
            "--llm-classify", "--contextualize", "--reconstruct-headings",
            "--quality-score", "--llm-scaffold", "--raptor",
        }
        return any(flag in args for flag in feature_flags)
    if action == "query":
        return "--answer" in args
    if action == "export":
        return "--format" in args and "flashcards" in args
    return False


def _redact_cli_secrets(args: list[str]) -> list[str]:
    """Return a display-safe CLI argument list."""
    redacted = list(args)
    for index, value in enumerate(redacted[:-1]):
        if value in _SECRET_CLI_FLAGS:
            redacted[index + 1] = "<redacted>"
    return redacted


def _menu_secrets_to_environment(
        args: list[str], *, default_cloud_url: str,
        is_deepseek_cloud_fn: EndpointPredicateFn,
        is_minimax_cloud_fn: EndpointPredicateFn,
) -> tuple[list[str], dict[str, str]]:
    """Remove hidden-prompt secrets from argv and scope them to the child."""
    cloud_url = default_cloud_url
    for index, value in enumerate(args[:-1]):
        if value in {"--cloud-url", "--llm-url"}:
            cloud_url = args[index + 1]

    safe_args = []
    environment = {}
    index = 0
    while index < len(args):
        flag = args[index]
        if flag in _SECRET_CLI_FLAGS:
            if index + 1 >= len(args):
                safe_args.append(flag)
                index += 1
                continue
            secret = args[index + 1]
            if flag == "--gemini-key":
                environment["GEMINI_API_KEY"] = secret
            elif is_deepseek_cloud_fn(cloud_url):
                environment["DEEPSEEK_API_KEY"] = secret
            elif is_minimax_cloud_fn(cloud_url):
                environment["MINIMAX_API_KEY"] = secret
            else:
                environment["CLOUD_API_KEY"] = secret
            index += 2
            continue
        safe_args.append(flag)
        index += 1
    return safe_args, environment
