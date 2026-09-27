from __future__ import annotations

from dataclasses import FrozenInstanceError
import hashlib
import json

import pytest

pytest.importorskip("fastapi")
from fastapi.testclient import TestClient

import service_api
import service_contracts
import service_evidence_http
import service_runtime
from tests.test_service_evidence_runtime import config_for, empty_result


READER = "r" * 48
ADMIN = "a" * 48
ROUTE = "/evidence/v1/corpora/book/search"
AUTH = {"Authorization": "Bearer " + READER, "X-Request-ID": "request-1"}


def _client(tmp_path, *, enabled=True, runner=None, enforce_peer=False):
    import service_evidence_contracts

    runtime = service_runtime.RagApplicationService(
        {"book": config_for(tmp_path)}, working_directory=tmp_path,
        output_root=tmp_path / "output",
        evidence_configs={"book": service_evidence_contracts.EvidenceCorpusConfig("book")},
        evidence_runner=runner or (lambda *a, **k: empty_result()),
        search_runner=lambda c, r, i: empty_result(c.corpus_id, i, r.mode)["search"])
    app = service_api.create_app(
        runtime, service_api.ServiceCredentials(READER, ADMIN),
        evidence_enabled=enabled, enforce_peer_loopback=enforce_peer,
        reconcile_interval_seconds=3600)
    return TestClient(app, base_url="http://127.0.0.1"), runtime


def test_opt_in_uses_same_lifecycle_credentials_headers_and_v1_schema(tmp_path):
    client, runtime = _client(tmp_path)
    with client:
        assert runtime.started
        for token in (READER, ADMIN):
            response = client.post(ROUTE, headers={**AUTH, "Authorization": "Bearer " + token},
                                   json={"query": "private", "mode": "vector"})
            assert response.status_code == 200 and response.json() == empty_result()
            assert response.headers["cache-control"] == "no-store"
            assert response.headers["x-content-type-options"] == "nosniff"
            assert response.headers["x-request-id"] == "request-1"
        schema = client.get("/v1/openapi.json", headers=AUTH)
        encoded = (json.dumps(schema.json(), ensure_ascii=False, allow_nan=False,
                              sort_keys=True, separators=(",", ":")) + "\n").encode()
        assert hashlib.sha256(encoded).hexdigest() == "169b09c0a820c72483ff74da3e8a48175371b1c6d5ee9dbb4371f5d1953b02ce"
        ordinary = client.post("/v1/corpora/book/search", headers=AUTH,
                               json={"query": "private", "mode": "vector"})
        assert ordinary.status_code == 200 and ordinary.json() == empty_result()["search"]
    assert not runtime.started


def test_evidence_route_absent_by_default(tmp_path):
    client, runtime = _client(tmp_path, enabled=False)
    assert all("evidence" not in getattr(route, "path", "") for route in client.app.routes)
    with client:
        assert client.post(ROUTE, headers=AUTH, json={"query": "private"}).status_code == 404


@pytest.mark.parametrize("headers,body,status", [
    ({}, '{"query":"private"}', 401),
    ({"Authorization": "Bearer " + "x" * 48}, '{"query":"private"}', 401),
    ({**AUTH, "Content-Type": "text/plain"}, '{"query":"private"}', 415),
    (AUTH, '{"query":"private","query":"other"}', 400),
    (AUTH, '{"query":"private","filename":"C:/secret"}', 400),
    (AUTH, '{"query":"private","limit":true}', 400),
    (AUTH, " " * (service_contracts.MAX_REQUEST_BYTES + 1), 413),
], ids=["missing-auth", "wrong-auth", "media-type", "duplicate-key", "path-field",
        "boolean-limit", "oversized-body"])
def test_route_reuses_auth_and_strict_bounded_body(tmp_path, headers, body, status):
    client, runtime = _client(tmp_path, runner=lambda *a, **k: pytest.fail("must not execute"))
    with client:
        response = client.post(ROUTE, headers={"Content-Type": "application/json", **headers}, content=body)
        assert response.status_code == status
        assert "private" not in response.text and "C:/secret" not in response.text
        assert response.headers["cache-control"] == "no-store"
        assert runtime.healthy


@pytest.mark.parametrize("route,headers,status", [
    (ROUTE + "?filename=private", AUTH, 400),
    (ROUTE.replace("book", "outside"), AUTH, 404),
    (ROUTE, {**AUTH, "Host": "attacker.invalid"}, 400),
])
def test_no_query_authority_no_extra_corpora_no_host_rebinding(tmp_path, route, headers, status):
    client, runtime = _client(tmp_path)
    with client:
        response = client.post(route, headers=headers, json={"query": "private"})
        assert response.status_code == status
        assert "private" not in response.text and "attacker" not in response.text


def test_peer_boundary_applies_to_new_route(tmp_path):
    client, runtime = _client(tmp_path, enforce_peer=True)
    with client:
        assert client.post(ROUTE, headers=AUTH, json={"query": "private"}).status_code == 400


def test_invalid_runner_result_is_redacted_and_unhealthy(tmp_path):
    client, runtime = _client(tmp_path, runner=lambda *a, **k: {"secret": "private"})
    with client:
        response = client.post(ROUTE, headers=AUTH, json={"query": "private"})
        assert response.status_code == 503
        assert "private" not in response.text and not runtime.healthy


def test_binding_is_frozen_and_duplicate_mount_refused(tmp_path):
    import service_http

    binding = service_evidence_http.EvidenceHttpBinding(service_http.require_reader,
                                                        service_http._bounded_json_body)
    with pytest.raises(FrozenInstanceError):
        binding.require_reader = lambda: None
    with pytest.raises(TypeError):
        service_evidence_http.EvidenceHttpBinding(None, lambda: None)
    client, runtime = _client(tmp_path)
    with pytest.raises(ValueError, match="already installed"):
        service_evidence_http.install_evidence_routes(client.app, runtime, binding=binding)
