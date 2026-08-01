"""Versioned release defaults for private-data and local-principal trust.

This module is deliberately standard-library only.  Command parsing, provider
composition, background jobs, and the web UI can therefore share one policy
without importing a transport SDK or the monolithic pipeline facade.
"""

from __future__ import annotations

from dataclasses import dataclass
import hashlib
import os
import re
from typing import Literal, Mapping


RELEASE_SECURITY_POLICY_VERSION = 1

SecurityProfile = Literal["release", "development"]
NetworkPolicy = Literal["local-only", "allow-cloud"]
ModelDownloadPolicy = Literal["cache-only", "allow-reviewed-sync"]

_CACHE_NAMESPACE_RE = re.compile(r"[A-Za-z0-9][A-Za-z0-9._-]{0,63}")
_NETWORK_OVERRIDE_VARIABLES = (
    "HTTPS_PROXY",
    "https_proxy",
    "HTTP_PROXY",
    "http_proxy",
    "ALL_PROXY",
    "all_proxy",
    "REQUESTS_CA_BUNDLE",
    "CURL_CA_BUNDLE",
    "SSL_CERT_FILE",
    "SSL_CERT_DIR",
    # TLS key logging exports the session secrets protecting private corpus
    # text and provider credentials. urllib3 and httpx read it straight from
    # the process environment when they build an SSL context, so unlike the
    # proxy and CA variables above it is not neutralized by a session-level
    # ``trust_env = False``. It must therefore fail closed on policy alone.
    "SSLKEYLOGFILE",
    # SDK/model-hub endpoint overrides are trust decisions too. Runtime
    # provider adapters pin their official origins; the owned model-sync
    # transport accepts a reviewed HF endpoint only under explicit trust.
    "HF_ENDPOINT",
    "OPENAI_BASE_URL",
    "GOOGLE_GEMINI_BASE_URL",
    "GOOGLE_VERTEX_BASE_URL",
    "GEMINI_NEXT_GEN_API_BASE_URL",
    "GOOGLE_GENAI_USE_VERTEXAI",
    "GOOGLE_GENAI_USE_ENTERPRISE",
    "CO_API_URL",
)


class ReleaseSecurityError(ValueError):
    """A release trust boundary was not explicitly satisfied."""


def cache_namespace_identity(namespace: str) -> str:
    """Return an opaque identity for a caller-declared trust/tenant domain.

    The raw namespace is intentionally excluded from cache records, telemetry,
    and resume provenance.  A short conservative syntax also keeps URLs,
    whitespace-delimited secrets, and shell fragments out of the input.
    """
    if not isinstance(namespace, str):
        raise TypeError("cache namespace must be text")
    if _CACHE_NAMESPACE_RE.fullmatch(namespace) is None:
        raise ReleaseSecurityError(
            "cache namespace must be 1-64 ASCII letters, digits, dots, "
            "underscores, or hyphens"
        )
    digest = hashlib.sha256(namespace.encode("ascii")).hexdigest()
    return f"v{RELEASE_SECURITY_POLICY_VERSION}:sha256:{digest}"


@dataclass(frozen=True, slots=True)
class ReleaseSecurityPolicy:
    """Immutable controls shared by every release-facing execution surface."""

    profile: SecurityProfile = "release"
    network_policy: NetworkPolicy = "local-only"
    model_download_policy: ModelDownloadPolicy = "cache-only"
    cache_namespace_id: str | None = None
    trust_environment_network: bool = False
    trusted_single_user_ui: bool = False
    schema_version: int = RELEASE_SECURITY_POLICY_VERSION

    def __post_init__(self) -> None:
        if (isinstance(self.schema_version, bool)
                or not isinstance(self.schema_version, int)
                or self.schema_version != RELEASE_SECURITY_POLICY_VERSION):
            raise ReleaseSecurityError(
                "unsupported release-security policy version"
            )
        if (not isinstance(self.profile, str)
                or self.profile not in {"release", "development"}):
            raise ReleaseSecurityError("invalid security profile")
        if (not isinstance(self.network_policy, str)
                or self.network_policy not in {"local-only", "allow-cloud"}):
            raise ReleaseSecurityError("invalid network policy")
        if (
            not isinstance(self.model_download_policy, str)
            or self.model_download_policy not in {
                "cache-only", "allow-reviewed-sync"}
        ):
            raise ReleaseSecurityError("invalid model download policy")
        if (self.cache_namespace_id is not None
                and (not isinstance(self.cache_namespace_id, str)
                     or re.fullmatch(
            rf"v{RELEASE_SECURITY_POLICY_VERSION}:sha256:[0-9a-f]{{64}}",
            self.cache_namespace_id,
        ) is None)):
            raise ReleaseSecurityError("invalid cache namespace identity")
        for name, value in (
            ("trust_environment_network", self.trust_environment_network),
            ("trusted_single_user_ui", self.trusted_single_user_ui),
        ):
            if not isinstance(value, bool):
                raise TypeError(f"{name} must be boolean")

    @classmethod
    def from_values(
        cls,
        *,
        profile: str = "release",
        network_policy: str = "local-only",
        model_download_policy: str = "cache-only",
        cache_namespace: str = "",
        trust_environment_network: bool = False,
        trusted_single_user_ui: bool = False,
        schema_version: int = RELEASE_SECURITY_POLICY_VERSION,
    ) -> "ReleaseSecurityPolicy":
        namespace_id = (
            cache_namespace_identity(cache_namespace)
            if cache_namespace else None
        )
        return cls(
            profile=profile,  # type: ignore[arg-type]
            network_policy=network_policy,  # type: ignore[arg-type]
            model_download_policy=(
                model_download_policy),  # type: ignore[arg-type]
            cache_namespace_id=namespace_id,
            trust_environment_network=trust_environment_network,
            trusted_single_user_ui=trusted_single_user_ui,
            schema_version=schema_version,
        )

    @property
    def allow_inline_secrets(self) -> bool:
        """Inline argv secrets are confined to the explicit dev escape hatch."""
        return self.profile == "development"

    @property
    def default_llm_cache_mode(self) -> str:
        """Private model output is non-persistent by release default."""
        return "off" if self.profile == "release" else "readwrite"

    def provenance(self) -> dict[str, object]:
        """Return the value-free policy receipt safe for reports/manifests."""
        return {
            "schema_version": self.schema_version,
            "profile": self.profile,
            "network_policy": self.network_policy,
            "model_download_policy": self.model_download_policy,
            "auxiliary_telemetry": "disabled",
            "cache_namespace_id": self.cache_namespace_id,
            "trust_environment_network": self.trust_environment_network,
            "ui_boundary": (
                "trusted-single-user"
                if self.trusted_single_user_ui else "disabled"
            ),
        }

    @classmethod
    def from_provenance(
        cls, receipt: Mapping[str, object],
    ) -> "ReleaseSecurityPolicy":
        """Reconstruct a policy from its strict, value-free process receipt."""
        if not isinstance(receipt, Mapping):
            raise TypeError("release-security provenance must be a mapping")
        expected = {
            "schema_version", "profile", "network_policy",
            "model_download_policy", "auxiliary_telemetry",
            "cache_namespace_id", "trust_environment_network",
            "ui_boundary",
        }
        if set(receipt) != expected:
            raise ReleaseSecurityError(
                "release-security provenance fields differ from policy"
            )
        ui_boundary = receipt["ui_boundary"]
        if ui_boundary not in {"disabled", "trusted-single-user"}:
            raise ReleaseSecurityError("invalid UI trust boundary")
        if receipt["auxiliary_telemetry"] != "disabled":
            raise ReleaseSecurityError("auxiliary telemetry must be disabled")
        return cls(
            schema_version=receipt["schema_version"],  # type: ignore[arg-type]
            profile=receipt["profile"],  # type: ignore[arg-type]
            network_policy=receipt["network_policy"],  # type: ignore[arg-type]
            model_download_policy=(
                receipt["model_download_policy"]),  # type: ignore[arg-type]
            cache_namespace_id=receipt["cache_namespace_id"],  # type: ignore[arg-type]
            trust_environment_network=receipt[
                "trust_environment_network"],  # type: ignore[arg-type]
            trusted_single_user_ui=(ui_boundary == "trusted-single-user"),
        )


def environment_network_overrides(
    environment: Mapping[str, str] | None = None,
) -> tuple[str, ...]:
    """List names whose values are non-empty without returning those values."""
    source = os.environ if environment is None else environment
    return tuple(name for name in _NETWORK_OVERRIDE_VARIABLES if source.get(name))


def require_cloud_egress(
    policy: ReleaseSecurityPolicy,
    *,
    feature: str,
    custom_gateway: bool = False,
    environment: Mapping[str, str] | None = None,
) -> None:
    """Fail before credential lookup or transport construction unless trusted."""
    if not isinstance(policy, ReleaseSecurityPolicy):
        raise TypeError("release security policy is required")
    if policy.network_policy != "allow-cloud":
        raise ReleaseSecurityError(
            f"{feature} requires explicit --network-policy allow-cloud"
        )
    if custom_gateway and policy.profile == "release":
        if policy.cache_namespace_id is None:
            raise ReleaseSecurityError(
                "release custom gateways require --llm-cache-namespace"
            )
    overrides = environment_network_overrides(environment)
    if (
        policy.profile == "release"
        and overrides
        and not policy.trust_environment_network
    ):
        names = ", ".join(overrides)
        raise ReleaseSecurityError(
            "cloud transport environment is overridden by "
            f"{names}; pass --trust-environment-network only after review"
        )


def require_model_download(
    policy: ReleaseSecurityPolicy,
    *,
    feature: str,
    environment: Mapping[str, str] | None = None,
) -> None:
    """Authorize an explicit synchronization of reviewed public model bytes."""
    if not isinstance(policy, ReleaseSecurityPolicy):
        raise TypeError("release security policy is required")
    if policy.model_download_policy != "allow-reviewed-sync":
        raise ReleaseSecurityError(
            f"{feature} requires explicit --model-download-policy "
            "allow-reviewed-sync"
        )
    overrides = environment_network_overrides(environment)
    if (
        policy.profile == "release"
        and overrides
        and not policy.trust_environment_network
    ):
        names = ", ".join(overrides)
        raise ReleaseSecurityError(
            "model synchronization environment is overridden by "
            f"{names}; pass --trust-environment-network only after review"
        )


def require_trusted_ui(policy: ReleaseSecurityPolicy) -> None:
    """Enforce the supported unauthenticated UI principal boundary."""
    if not isinstance(policy, ReleaseSecurityPolicy):
        raise TypeError("release security policy is required")
    if not policy.trusted_single_user_ui:
        raise ReleaseSecurityError(
            "the unauthenticated UI requires --trust-local-user and is only "
            "supported in a trusted single-user OS session"
        )
