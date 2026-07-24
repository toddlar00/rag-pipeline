import subprocess
import sys

import pytest

import release_security


def test_release_security_is_a_standard_library_only_leaf_module():
    result = subprocess.run(
        [
            sys.executable,
            "-c",
            (
                "import sys; import release_security; "
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


def test_release_defaults_are_local_nonpersistent_and_value_free():
    policy = release_security.ReleaseSecurityPolicy()

    assert policy.network_policy == "local-only"
    assert policy.allow_inline_secrets is False
    assert policy.default_llm_cache_mode == "off"
    assert policy.provenance() == {
        "schema_version": release_security.RELEASE_SECURITY_POLICY_VERSION,
        "profile": "release",
        "network_policy": "local-only",
        "model_download_policy": "cache-only",
        "auxiliary_telemetry": "disabled",
        "cache_namespace_id": None,
        "trust_environment_network": False,
        "ui_boundary": "disabled",
    }


def test_namespace_is_opaque_stable_and_domain_separating():
    first = release_security.cache_namespace_identity("client-a.production")
    same = release_security.cache_namespace_identity("client-a.production")
    second = release_security.cache_namespace_identity("client-b.production")

    assert first == same
    assert first != second
    assert first.startswith("v1:sha256:")
    assert "client-a" not in first


def test_value_free_provenance_round_trips_exact_policy():
    policy = release_security.ReleaseSecurityPolicy.from_values(
        profile="development", network_policy="allow-cloud",
        cache_namespace="client-a", trust_environment_network=True,
        trusted_single_user_ui=True,
    )

    assert release_security.ReleaseSecurityPolicy.from_provenance(
        policy.provenance()) == policy


@pytest.mark.parametrize("mutator", [
    lambda receipt: receipt.update({"schema_version": True}),
    lambda receipt: receipt.update({"schema_version": 2}),
    lambda receipt: receipt.update({"profile": "release-candidate"}),
    lambda receipt: receipt.update({"extra": False}),
    lambda receipt: receipt.pop("network_policy"),
])
def test_policy_provenance_rejects_tampering(mutator):
    receipt = release_security.ReleaseSecurityPolicy().provenance()
    mutator(receipt)

    with pytest.raises(
            (TypeError, release_security.ReleaseSecurityError)):
        release_security.ReleaseSecurityPolicy.from_provenance(receipt)


@pytest.mark.parametrize(
    "namespace",
    ["", "contains space", "https://tenant.example", "tenant/key", "x" * 65],
)
def test_namespace_rejects_ambiguous_or_structured_values(namespace):
    with pytest.raises(release_security.ReleaseSecurityError):
        release_security.cache_namespace_identity(namespace)


def test_local_only_blocks_before_inspecting_network_environment():
    class ExplodingEnvironment(dict):
        def get(self, *_args, **_kwargs):
            raise AssertionError("blocked policy must not inspect environment")

    with pytest.raises(
        release_security.ReleaseSecurityError,
        match="network-policy allow-cloud",
    ):
        release_security.require_cloud_egress(
            release_security.ReleaseSecurityPolicy(),
            feature="embedding",
            environment=ExplodingEnvironment(),
        )


def test_release_custom_gateway_needs_opaque_namespace():
    policy = release_security.ReleaseSecurityPolicy(
        network_policy="allow-cloud",
        trust_environment_network=True,
    )

    with pytest.raises(
        release_security.ReleaseSecurityError,
        match="llm-cache-namespace",
    ):
        release_security.require_cloud_egress(
            policy, feature="LLM generation", custom_gateway=True,
        )

    namespaced = release_security.ReleaseSecurityPolicy.from_values(
        network_policy="allow-cloud",
        cache_namespace="tenant-a",
        trust_environment_network=True,
    )
    release_security.require_cloud_egress(
        namespaced, feature="LLM generation", custom_gateway=True,
    )


def test_release_cloud_rejects_ambient_proxy_or_custom_ca_by_name_only():
    policy = release_security.ReleaseSecurityPolicy(
        network_policy="allow-cloud")
    environment = {
        "HTTPS_PROXY": "PROXY_SECRET_CANARY",
        "SSL_CERT_FILE": "CERT_PATH_CANARY",
    }

    with pytest.raises(release_security.ReleaseSecurityError) as error:
        release_security.require_cloud_egress(
            policy, feature="reranking", environment=environment,
        )

    message = str(error.value)
    assert "HTTPS_PROXY" in message
    assert "SSL_CERT_FILE" in message
    assert "PROXY_SECRET_CANARY" not in message
    assert "CERT_PATH_CANARY" not in message


def test_development_still_needs_explicit_cloud_consent():
    policy = release_security.ReleaseSecurityPolicy(profile="development")

    assert policy.allow_inline_secrets is True
    assert policy.default_llm_cache_mode == "readwrite"
    with pytest.raises(release_security.ReleaseSecurityError):
        release_security.require_cloud_egress(
            policy, feature="embedding", environment={"HTTPS_PROXY": "proxy"},
        )


def test_model_download_is_separate_explicit_consent():
    with pytest.raises(
            release_security.ReleaseSecurityError,
            match="model-download-policy"):
        release_security.require_model_download(
            release_security.ReleaseSecurityPolicy(),
            feature="model synchronization",
            environment={},
        )

    approved = release_security.ReleaseSecurityPolicy(
        model_download_policy="allow-reviewed-sync")
    release_security.require_model_download(
        approved, feature="model synchronization", environment={})
    with pytest.raises(
            release_security.ReleaseSecurityError,
            match="HTTPS_PROXY"):
        release_security.require_model_download(
            approved, feature="model synchronization",
            environment={"HTTPS_PROXY": "secret-proxy"},
        )


def test_ui_requires_explicit_trusted_single_user_opt_in():
    with pytest.raises(release_security.ReleaseSecurityError):
        release_security.require_trusted_ui(
            release_security.ReleaseSecurityPolicy())

    release_security.require_trusted_ui(
        release_security.ReleaseSecurityPolicy(trusted_single_user_ui=True))
