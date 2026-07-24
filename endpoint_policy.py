"""Fail-closed URL policy for LLM endpoints.

The validator is deliberately standard-library only so command interpretation,
transport adapters, and reporting can share one definition of a safe endpoint.
It never includes rejected URL text in an exception or endpoint identity.
"""

from __future__ import annotations

import hashlib
import ipaddress
import re
from dataclasses import dataclass
from urllib.parse import urlsplit


_DNS_LABEL_RE = re.compile(r"[A-Za-z0-9](?:[A-Za-z0-9-]{0,61}[A-Za-z0-9])?")
_PATH_SEGMENT_RE = re.compile(r"[A-Za-z0-9_~-](?:[A-Za-z0-9._~-]{0,126}[A-Za-z0-9_~-])?")
_OFFICIAL_PATHS = {
    "api.deepseek.com": ("deepseek", frozenset({"", "/v1"})),
    "api.minimax.io": ("minimax", frozenset({"/v1"})),
}
ENDPOINT_POLICY_VERSION = 1


@dataclass(frozen=True, slots=True)
class ValidatedEndpoint:
    """Canonical network target plus a secret-safe reporting identity."""

    base_url: str
    endpoint_id: str
    provider: str
    is_loopback: bool


def _opaque_id(kind: str, value: str) -> str:
    digest = hashlib.sha256(
        value.encode("utf-8", errors="surrogatepass")).hexdigest()
    return f"{kind}:sha256:{digest}"


def _validated_hostname(hostname: str) -> tuple[str, bool]:
    if not hostname or hostname.endswith(".") or "%" in hostname:
        raise ValueError("cloud endpoint hostname is not canonical")
    if not hostname.isascii():
        raise ValueError("cloud endpoint hostname must be ASCII")

    normalized = hostname.casefold()
    try:
        address = ipaddress.ip_address(normalized)
    except ValueError:
        if len(normalized) > 253:
            raise ValueError("cloud endpoint hostname is too long") from None
        labels = normalized.split(".")
        if normalized == "localhost" or normalized.endswith(".localhost"):
            raise ValueError(
                "cloud endpoint loopback names are not permitted") from None
        numeric_labels = all(
            re.fullmatch(r"(?:0x[0-9a-f]+|[0-9]+)", label) is not None
            for label in labels
        )
        if any(
            not label
            or label.startswith("xn--")
            or _DNS_LABEL_RE.fullmatch(label) is None
            for label in labels
        ) or numeric_labels:
            raise ValueError("cloud endpoint hostname is not canonical") from None
        return normalized, False

    canonical = address.compressed.casefold()
    if normalized != canonical:
        raise ValueError("cloud endpoint IP literal is not canonical")
    if isinstance(address, ipaddress.IPv6Address) and address.ipv4_mapped:
        raise ValueError("cloud endpoint IP literal must not be IPv4-mapped IPv6")
    if address.is_unspecified or address.is_multicast:
        raise ValueError("cloud endpoint IP literal is not a usable target")
    return canonical, address.is_loopback


def _validated_port(
        authority: str, *, ipv6: bool,
        parsed_port: int | None) -> int | None:
    host_authority = authority
    if ipv6:
        closing = authority.find("]")
        if closing < 0:
            raise ValueError("cloud endpoint IPv6 literal must be bracketed")
        suffix = authority[closing + 1:]
        if suffix and (
            not suffix.startswith(":") or not suffix[1:].isdigit()
        ):
            raise ValueError("cloud endpoint port is malformed")
    elif ":" in host_authority:
        if host_authority.count(":") != 1:
            raise ValueError("cloud endpoint IPv6 literal must be bracketed")
        port_text = host_authority.rsplit(":", 1)[1]
        if not port_text.isdigit():
            raise ValueError("cloud endpoint port is malformed")
    if parsed_port is not None and not 1 <= parsed_port <= 65535:
        raise ValueError("cloud endpoint port is outside 1-65535")
    return parsed_port


def _validated_path(path: str) -> str:
    if path in {"", "/"}:
        return ""
    if len(path) > 512 or not path.startswith("/") or "//" in path:
        raise ValueError("cloud endpoint base path is not canonical")
    normalized = path[:-1] if path.endswith("/") else path
    segments = normalized[1:].split("/")
    if any(
        segment in {"", ".", ".."}
        or _PATH_SEGMENT_RE.fullmatch(segment) is None
        for segment in segments
    ):
        raise ValueError("cloud endpoint base path is not canonical")
    return normalized


def validate_cloud_endpoint(
        url: str, *, allow_disabled: bool = False,
) -> ValidatedEndpoint | None:
    """Validate and canonicalize one OpenAI-compatible API base URL.

    Public/custom endpoints require HTTPS. Plain HTTP is accepted only for a
    canonical literal loopback address, avoiding DNS rebinding through names
    such as ``localhost``. Official provider identity additionally binds the
    scheme, default port, and reviewed base path.
    """
    if not isinstance(url, str):
        raise TypeError("cloud endpoint must be text")
    if url == "" and allow_disabled:
        return None
    if (
        not url
        or url != url.strip()
        or not url.isascii()
        or any(ord(char) < 33 or ord(char) == 127 for char in url)
        or "\\" in url
        or "?" in url
        or "#" in url
    ):
        raise ValueError("cloud endpoint contains disallowed URL syntax")

    try:
        parsed = urlsplit(url)
        hostname = parsed.hostname
        port = parsed.port
    except ValueError:
        raise ValueError("cloud endpoint is malformed") from None

    scheme = parsed.scheme.casefold()
    if scheme not in {"http", "https"} or not parsed.netloc or hostname is None:
        raise ValueError("cloud endpoint must be an absolute HTTP(S) URL")
    if (
        parsed.username is not None
        or parsed.password is not None
        or "@" in parsed.netloc
    ):
        raise ValueError("cloud endpoint must not contain user information")
    if parsed.query or parsed.fragment:
        raise ValueError("cloud endpoint must not contain a query or fragment")

    normalized_host, is_loopback = _validated_hostname(hostname)
    is_ipv6 = ":" in normalized_host
    port = _validated_port(parsed.netloc, ipv6=is_ipv6, parsed_port=port)
    path = _validated_path(parsed.path)

    official = _OFFICIAL_PATHS.get(normalized_host)
    if official is not None:
        provider, allowed_paths = official
        if scheme != "https" or port not in {None, 443}:
            raise ValueError("official cloud endpoints require HTTPS on port 443")
        if path not in allowed_paths:
            raise ValueError("official cloud endpoint base path is not supported")
    else:
        if any(
            normalized_host.startswith(f"{official_host}.")
            or normalized_host.endswith(f".{official_host}")
            for official_host in _OFFICIAL_PATHS
        ):
            raise ValueError(
                "cloud endpoint hostname is confusingly similar to an "
                "official provider")
        provider = "loopback" if is_loopback else "custom"
        if scheme == "http" and not is_loopback:
            raise ValueError("non-loopback cloud endpoints require HTTPS")

    default_port = 443 if scheme == "https" else 80
    display_host = f"[{normalized_host}]" if is_ipv6 else normalized_host
    port_suffix = f":{port}" if port is not None and port != default_port else ""
    base_url = f"{scheme}://{display_host}{port_suffix}{path}"
    endpoint_id = (
        f"v{ENDPOINT_POLICY_VERSION}:{base_url}"
        if provider in {"deepseek", "minimax"}
        else f"v{ENDPOINT_POLICY_VERSION}:{_opaque_id(provider, base_url)}"
    )
    return ValidatedEndpoint(
        base_url=base_url,
        endpoint_id=endpoint_id,
        provider=provider,
        is_loopback=is_loopback,
    )


def cloud_endpoint_identity(url: object) -> str:
    """Return a stable identity without ever echoing rejected URL material."""
    if url == "":
        return f"v{ENDPOINT_POLICY_VERSION}:disabled"
    if not isinstance(url, str):
        return (
            f"v{ENDPOINT_POLICY_VERSION}:"
            f"{_opaque_id('invalid-type', type(url).__name__)}"
        )
    try:
        endpoint = validate_cloud_endpoint(url)
    except (TypeError, ValueError):
        return f"v{ENDPOINT_POLICY_VERSION}:{_opaque_id('invalid', url)}"
    assert endpoint is not None
    return endpoint.endpoint_id
