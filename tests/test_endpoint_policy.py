import subprocess
import sys

import pytest

import endpoint_policy


def test_endpoint_policy_is_a_standard_library_only_leaf_module():
    result = subprocess.run(
        [
            sys.executable,
            "-c",
            (
                "import sys; import endpoint_policy; "
                "forbidden = {'rag', 'cli_policy', 'llm_adapters', "
                "'llm_runtime', 'requests', 'google', 'torch'}; "
                "loaded = forbidden.intersection(sys.modules); "
                "assert not loaded, sorted(loaded)"
            ),
        ],
        capture_output=True,
        check=False,
        text=True,
        timeout=30,
    )

    assert result.returncode == 0, result.stderr


@pytest.mark.parametrize(
    ("url", "base_url", "provider", "loopback"),
    [
        (
            "HTTPS://API.DEEPSEEK.COM:443/v1/",
            "https://api.deepseek.com/v1",
            "deepseek",
            False,
        ),
        (
            "https://api.deepseek.com",
            "https://api.deepseek.com",
            "deepseek",
            False,
        ),
        (
            "https://api.minimax.io/v1",
            "https://api.minimax.io/v1",
            "minimax",
            False,
        ),
        (
            "https://Gateway.Example:8443/openai/v1/",
            "https://gateway.example:8443/openai/v1",
            "custom",
            False,
        ),
        (
            "http://127.0.0.1:9000/v1",
            "http://127.0.0.1:9000/v1",
            "loopback",
            True,
        ),
        (
            "http://127.0.0.2:9000/v1",
            "http://127.0.0.2:9000/v1",
            "loopback",
            True,
        ),
        (
            "http://[::1]:9000/v1",
            "http://[::1]:9000/v1",
            "loopback",
            True,
        ),
    ],
)
def test_cloud_endpoint_contract_canonicalizes_safe_targets(
        url, base_url, provider, loopback):
    endpoint = endpoint_policy.validate_cloud_endpoint(url)

    assert endpoint is not None
    assert endpoint.base_url == base_url
    assert endpoint.provider == provider
    assert endpoint.is_loopback is loopback
    if provider in {"custom", "loopback"}:
        assert endpoint.endpoint_id.startswith(f"v1:{provider}:sha256:")
        assert base_url not in endpoint.endpoint_id
    else:
        assert endpoint.endpoint_id == f"v1:{base_url}"


@pytest.mark.parametrize("url", [
    "http://api.deepseek.com",
    "http://api.minimax.io/v1",
    "https://api.deepseek.com:444/v1",
    "https://api.deepseek.com/v2",
    "https://api.minimax.io",
    "https://user:password@api.deepseek.com/v1",
    "https://api.deepseek.com/v1?token=secret",
    "https://api.deepseek.com/v1#fragment",
    "https://api.deepseek.com./v1",
    "https://xn--bcher-kva.example/v1",
    "https://bücher.example/v1",
    "//api.deepseek.com/v1",
    "https:////api.deepseek.com/v1",
    "http://localhost:11434",
    "https://localhost/v1",
    "https://foo.localhost/v1",
    "http://gateway.example/v1",
    "http://2130706433:11434",
    "https://2130706433/v1",
    "https://0x7f000001/v1",
    "http://127.1:11434",
    "https://127.1/v1",
    "http://127.0.0.1.attacker.test/v1",
    "http://0.0.0.0:9000/v1",
    "https://0.0.0.0/v1",
    "https://224.0.0.1/v1",
    "https://[::]/v1",
    "https://[ff02::1]/v1",
    "http://[::ffff:127.0.0.1]:11434",
    "http://[::1%25eth0]:11434",
    "https://api.deepseek.com.attacker.invalid/v1",
    "https://evil.api.minimax.io/v1",
    "https://gateway.example:0/v1",
    "https://gateway.example:65536/v1",
    "https://gateway.example:/v1",
    "https://gateway.example:invalid/v1",
    " https://gateway.example/v1",
    "https://gateway.example/v1\t",
    "https://gateway.example/v1\n",
    "https://gateway.example/v1\x00",
    "https://gateway.example\\attacker/v1",
    "https://gateway.example/a/../v1",
    "https://gateway.example/%2e%2e/v1",
    "https://gateway.example/v1%2fadmin",
    "https://gateway.example/v1%5cadmin",
    "ftp://gateway.example/v1",
    "file://gateway.example/v1",
    "gateway.example/v1",
    "https://::1/v1",
    "",
])
def test_cloud_endpoint_contract_rejects_ambiguous_or_unsafe_targets(url):
    with pytest.raises((TypeError, ValueError)) as error:
        endpoint_policy.validate_cloud_endpoint(url)

    if url:
        assert url not in str(error.value)


def test_disabled_endpoint_is_explicit_and_distinct_from_invalid_input():
    assert endpoint_policy.validate_cloud_endpoint(
        "", allow_disabled=True) is None
    assert endpoint_policy.cloud_endpoint_identity("") == "v1:disabled"


def test_endpoint_identity_never_echoes_credentials_or_malformed_urls():
    canary = "URL_SECRET_CANARY_7fa2"
    unsafe = f"https://user:{canary}@gateway.example/v1?token={canary}"

    identity = endpoint_policy.cloud_endpoint_identity(unsafe)

    assert identity.startswith("v1:invalid:sha256:")
    assert canary not in identity
    assert unsafe not in identity


def test_custom_endpoint_identity_hides_internal_origin_and_base_path():
    endpoint = "https://private-gateway.internal:8443/tenant/v1"

    identity = endpoint_policy.cloud_endpoint_identity(endpoint)

    assert identity.startswith("v1:custom:sha256:")
    assert "private-gateway" not in identity
    assert "tenant" not in identity


def test_canonical_equivalents_share_identity_but_base_paths_do_not():
    first = endpoint_policy.cloud_endpoint_identity(
        "HTTPS://Gateway.Example:443/openai/v1/")
    equivalent = endpoint_policy.cloud_endpoint_identity(
        "https://gateway.example/openai/v1")
    different_path = endpoint_policy.cloud_endpoint_identity(
        "https://gateway.example/openai/v2")

    assert first == equivalent
    assert first != different_path
    assert first.startswith(
        f"v{endpoint_policy.ENDPOINT_POLICY_VERSION}:custom:sha256:")
