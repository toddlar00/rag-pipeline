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

import endpoint_policy
import release_security


EndpointPredicateFn = Callable[..., bool]
EndpointResolverFn = Callable[[object], tuple[str, str]]
CloudKeyResolverFn = Callable[..., str]
CloudEndpointValidatorFn = Callable[
    ..., endpoint_policy.ValidatedEndpoint | None]
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
    structure_profile: str
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
    security_profile: str = "release"
    network_policy: str = "local-only"
    model_download_policy: str = "cache-only"
    markdown_validation: str = "auto"


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
    security_policy: release_security.ReleaseSecurityPolicy


class LLMRuntimeConfigValues(TypedDict):
    """Values used by the facade to construct its runtime configuration."""

    cache_mode: str
    cache_dir: Path | str
    events_path: Path | str | None
    report_path: Path | str | None
    run_id: str | None
    max_provider_calls: int | None
    max_transport_attempts: int | None
    max_reserved_tokens: int | None
    fallback_policy: str
    failure_policy: str


_SECRET_CLI_FLAGS = frozenset({
    "--cloud-key", "--api-key", "--gemini-key",
})
_ENDPOINT_CLI_FLAGS = frozenset({
    "--cloud-url", "--llm-url", "--ollama-url",
})
_SENSITIVE_CLI_FLAGS = _SECRET_CLI_FLAGS | _ENDPOINT_CLI_FLAGS


def _build_resume_cmd(
        pdf: Path, args: object, extra_flags: str = "", *,
        defaults: ResumeCommandDefaults) -> str:
    """Build a shell command string to resume a failed ``full`` pipeline."""
    parts = [
        defaults.executable, defaults.script_name, "full",
        "--pdf", f'"{pdf}"', "--resume",
        "--structure-profile",
        str(getattr(args, "structure_profile", defaults.structure_profile)),
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
        "table_children": "--table-children",
        "thinking": "--thinking",
        "trust_environment_network": "--trust-environment-network",
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
    # Chunk size is an artifact-defining choice. Preserve it even when it
    # equals today's default so a resume remains stable after future upgrades.
    max_tokens = getattr(args, "max_tokens", None)
    if max_tokens is not None:
        parts.extend(["--max-tokens", str(max_tokens)])
    value_flags = (
        ("batch_size", None, "--batch-size"),
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
        ("max_llm_transport_attempts", None,
         "--max-llm-transport-attempts"),
        ("max_llm_reserved_tokens", None,
         "--max-llm-reserved-tokens"),
        ("security_profile", defaults.security_profile,
         "--security-profile"),
        ("network_policy", defaults.network_policy, "--network-policy"),
        ("model_download_policy", defaults.model_download_policy,
         "--model-download-policy"),
        ("markdown_validation", defaults.markdown_validation,
         "--markdown-validation"),
    )
    for attr, default, flag in value_flags:
        value = getattr(args, attr, default)
        if value is not None and value != default:
            if attr in {"ollama_url", "cloud_url"}:
                endpoint = endpoint_policy.validate_cloud_endpoint(
                    value, allow_disabled=True)
                value = endpoint.base_url if endpoint is not None else ""
            parts.extend([
                flag, f'"{value}"' if " " in str(value) else str(value)])
    policy = getattr(args, "_release_security_policy", None)
    if isinstance(policy, release_security.ReleaseSecurityPolicy):
        parts.extend([
            "--release-security-policy-version",
            str(policy.schema_version),
        ])
        if policy.cache_namespace_id is not None:
            parts.extend([
                "--release-cache-namespace-id",
                policy.cache_namespace_id,
            ])
    if extra_flags:
        parts.append(extra_flags)
    return " ".join(parts)


def _resolve_cloud_endpoint(
        args: object, *, defaults: ProviderCliDefaults,
        validate_cloud_endpoint_fn: CloudEndpointValidatorFn = (
            endpoint_policy.validate_cloud_endpoint)) -> tuple[str, str]:
    """Resolve URL/model shortcuts for the configured cloud provider."""
    cloud_url = getattr(args, "cloud_url", defaults.cloud_url)
    cloud_model = getattr(args, "cloud_model", defaults.cloud_model)
    endpoint = validate_cloud_endpoint_fn(
        cloud_url, allow_disabled=True)
    if endpoint is not None:
        cloud_url = endpoint.base_url
    default_endpoint = validate_cloud_endpoint_fn(
        defaults.cloud_url, allow_disabled=True)
    canonical_default_url = (
        default_endpoint.base_url
        if default_endpoint is not None else defaults.cloud_url)
    if (cloud_model or "").lower().startswith("deepseek-"):
        if cloud_url == canonical_default_url:
            cloud_url = defaults.deepseek_url
            endpoint = validate_cloud_endpoint_fn(
                cloud_url, allow_disabled=True)
            if endpoint is not None:
                cloud_url = endpoint.base_url
    if endpoint is not None and endpoint.provider == "deepseek":
        if cloud_model == defaults.cloud_model:
            cloud_model = defaults.deepseek_model
    return cloud_url, cloud_model


def _resolve_cloud_key(
        args: object, *, cloud_url: str = "", cloud_model: str = "",
        resolve_cloud_endpoint_fn: EndpointResolverFn,
        validate_cloud_endpoint_fn: CloudEndpointValidatorFn = (
            endpoint_policy.validate_cloud_endpoint),
        environment_get_fn: EnvironmentGetFn) -> str:
    """Resolve a key without sending one provider's secret to another host."""
    if not cloud_url and not cloud_model:
        cloud_url, cloud_model = resolve_cloud_endpoint_fn(args)
    endpoint = validate_cloud_endpoint_fn(
        cloud_url, allow_disabled=True)
    if endpoint is None:
        return ""

    explicit_key = getattr(args, "cloud_key", "")
    if explicit_key:
        return explicit_key
    if endpoint.provider == "loopback":
        return ""
    if endpoint.provider == "deepseek":
        return (
            environment_get_fn("DEEPSEEK_API_KEY", "")
            or environment_get_fn("CLOUD_API_KEY", "")
        )
    if endpoint.provider == "minimax":
        return (
            environment_get_fn("MINIMAX_API_KEY", "")
            or environment_get_fn("CLOUD_API_KEY", "")
        )
    return environment_get_fn("CLOUD_API_KEY", "")


def _llm_kwargs_from_args(
        args: object, *, include_workers: bool,
        defaults: ProviderCliDefaults,
        resolve_cloud_endpoint_fn: EndpointResolverFn,
        resolve_cloud_key_fn: CloudKeyResolverFn,
        resolve_credentials: bool = True) -> LLMCallOptions:
    """Collect provider options shared by LLM-backed operations."""
    cloud_url, cloud_model = resolve_cloud_endpoint_fn(args)
    ollama_endpoint = endpoint_policy.validate_cloud_endpoint(
        getattr(args, "ollama_url", defaults.ollama_url),
        allow_disabled=True,
    )
    policy = getattr(args, "_release_security_policy", None)
    cloud_credentials_enabled = resolve_credentials
    if policy is not None:
        if not isinstance(policy, release_security.ReleaseSecurityPolicy):
            raise TypeError("invalid release security policy")
        cloud_credentials_enabled = (
            resolve_credentials and policy.network_policy == "allow-cloud")
        if cloud_credentials_enabled:
            cloud_endpoint = endpoint_policy.validate_cloud_endpoint(
                cloud_url, allow_disabled=True)
            release_security.require_cloud_egress(
                policy,
                feature="LLM generation",
                custom_gateway=(
                    cloud_endpoint is not None
                    and cloud_endpoint.provider == "custom"),
            )
    kwargs: LLMCallOptions = {
        "cloud_url": cloud_url,
        "cloud_model": cloud_model,
        "cloud_key": (
            resolve_cloud_key_fn(
                args, cloud_url=cloud_url, cloud_model=cloud_model)
            if cloud_credentials_enabled else ""),
        "ollama_url": (
            ollama_endpoint.base_url if ollama_endpoint is not None else ""),
        "ollama_model": getattr(args, "ollama_model", defaults.ollama_model),
        "gemini_key": (
            getattr(args, "gemini_key", "")
            if cloud_credentials_enabled else ""),
        "thinking": getattr(args, "thinking", False),
    }
    if include_workers:
        kwargs["llm_workers"] = getattr(
            args, "llm_workers", defaults.llm_workers)
    if policy is not None:
        kwargs["security_policy"] = policy
    return kwargs


def _release_security_policy_from_args(
        args: object) -> release_security.ReleaseSecurityPolicy:
    """Construct the one immutable policy shared by the parsed operation."""
    namespace = getattr(args, "llm_cache_namespace", "")
    namespace_id = getattr(args, "release_cache_namespace_id", None)
    if namespace and namespace_id:
        raise release_security.ReleaseSecurityError(
            "cache namespace and canonical namespace identity conflict")
    if namespace_id:
        return release_security.ReleaseSecurityPolicy(
            profile=getattr(args, "security_profile", "release"),
            network_policy=getattr(args, "network_policy", "local-only"),
            model_download_policy=getattr(
                args, "model_download_policy", "cache-only"),
            cache_namespace_id=namespace_id,
            trust_environment_network=getattr(
                args, "trust_environment_network", False),
            trusted_single_user_ui=getattr(args, "trust_local_user", False),
            schema_version=getattr(
                args, "release_security_policy_version",
                release_security.RELEASE_SECURITY_POLICY_VERSION),
        )
    return release_security.ReleaseSecurityPolicy.from_values(
        profile=getattr(args, "security_profile", "release"),
        network_policy=getattr(args, "network_policy", "local-only"),
        model_download_policy=getattr(
            args, "model_download_policy", "cache-only"),
        cache_namespace=namespace,
        trust_environment_network=getattr(
            args, "trust_environment_network", False),
        trusted_single_user_ui=getattr(args, "trust_local_user", False),
        schema_version=getattr(
            args, "release_security_policy_version",
            release_security.RELEASE_SECURITY_POLICY_VERSION),
    )


def _argv_has_inline_secret(args: list[str]) -> bool:
    """Return whether exact CLI syntax carries a credential value in argv."""
    return any(
        isinstance(token, str)
        and token.partition("=")[0] in _SECRET_CLI_FLAGS
        for token in args
    )


def _option_spelling_conflicts(
        option: str, candidates: frozenset[str]) -> bool:
    """Return whether a long option is a case/shape/prefix near miss."""
    if not isinstance(option, str) or option in candidates:
        return False
    normalized = option.casefold().replace("_", "-")
    if len(normalized) <= 2:
        return False
    return any(
        candidate.startswith(normalized) or normalized.startswith(candidate)
        for candidate in candidates
    )


def _has_ambiguous_sensitive_option(args: list[str]) -> bool:
    """Reject sensitive option near-misses consistently across CLI scopes."""
    for token in args:
        if not isinstance(token, str):
            continue
        option = token.partition("=")[0]
        if _option_spelling_conflicts(option, _SENSITIVE_CLI_FLAGS):
            return True
    return False


def _namespace_uses_llm(args: object) -> bool:
    """Return whether a parsed command can actually invoke an LLM."""
    command = getattr(args, "command", None)
    if command in {"generate-questions", "raptor", "brief"}:
        return True
    if command == "query":
        return bool(getattr(args, "answer", False))
    if command == "export":
        return getattr(args, "format", "markdown") == "flashcards"
    if command in {"chunk", "full", "batch"}:
        return _pipeline_features_use_llm(args)
    return False


def _pipeline_features_use_llm(args: object) -> bool:
    """Return whether chunk/full pipeline features need provider secrets."""
    return any(bool(getattr(args, field, False)) for field in (
        "llm_classify",
        "contextualize",
        "reconstruct_headings",
        "quality_score",
        "llm_scaffold",
        "raptor",
    ))


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
        "max_transport_attempts": getattr(
            args, "max_llm_transport_attempts", None),
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
    for index, value in enumerate(redacted):
        option, separator, _inline_value = value.partition("=")
        if separator and option in _SECRET_CLI_FLAGS:
            redacted[index] = f"{option}=<redacted>"
            continue
        if index + 1 >= len(redacted):
            continue
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
    for index, value in enumerate(args):
        option, separator, inline_value = value.partition("=")
        if separator and option in {"--cloud-url", "--llm-url"}:
            cloud_url = inline_value
        elif (value in {"--cloud-url", "--llm-url"}
              and index + 1 < len(args)):
            cloud_url = args[index + 1]
    endpoint = endpoint_policy.validate_cloud_endpoint(
        cloud_url, allow_disabled=True)
    cloud_url = endpoint.base_url if endpoint is not None else ""

    safe_args = []
    environment = {}
    index = 0
    while index < len(args):
        flag = args[index]
        option, separator, inline_secret = flag.partition("=")
        if option in _SECRET_CLI_FLAGS:
            if separator:
                secret = inline_secret
                consumed = 1
            elif index + 1 >= len(args):
                safe_args.append(flag)
                index += 1
                continue
            else:
                secret = args[index + 1]
                consumed = 2
            if option == "--gemini-key":
                environment["GEMINI_API_KEY"] = secret
            elif is_deepseek_cloud_fn(cloud_url):
                environment["DEEPSEEK_API_KEY"] = secret
            elif is_minimax_cloud_fn(cloud_url):
                environment["MINIMAX_API_KEY"] = secret
            else:
                environment["CLOUD_API_KEY"] = secret
            index += consumed
            continue
        safe_args.append(flag)
        index += 1
    return safe_args, environment
