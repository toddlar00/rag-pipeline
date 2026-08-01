import gzip
import json
import subprocess
import sys
import threading
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer

import pytest
import requests

import provider_transport


class _StreamingResponse:
    status_code = 200

    def __init__(self, body: bytes, *, headers=None, chunks=None,
                 stream_error=None):
        self.body = body
        self.headers = (
            {
                "Content-Type": "application/json",
                "Content-Length": str(len(body)),
            }
            if headers is None else headers
        )
        self.chunks = chunks
        self.stream_error = stream_error
        self.iterated = False
        self.close_calls = 0

    def iter_content(self, *, chunk_size):
        self.iterated = True
        if self.stream_error is not None:
            raise self.stream_error
        chunks = self.chunks if self.chunks is not None else [self.body]
        for chunk in chunks:
            yield chunk

    def close(self):
        self.close_calls += 1


def test_provider_transport_is_a_stdlib_only_leaf():
    result = subprocess.run(
        [
            sys.executable,
            "-c",
            (
                "import sys; import provider_transport; "
                "forbidden = {'rag', 'llm_adapters', 'llm_runtime', "
                "'requests', 'httpx', 'google', 'google.genai'}; "
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


@pytest.mark.parametrize("content_type", [
    "application/json",
    "application/json; charset=utf-8",
    "Application/Problem+JSON; Charset=\"UTF8\"",
])
def test_bounded_reader_accepts_reviewed_json_media_types(content_type):
    body = b'{"ok":true}'
    response = _StreamingResponse(body, headers={
        "Content-Type": content_type,
    })

    assert provider_transport.read_bounded_json_response(
        response, max_bytes=1024) == {"ok": True}
    assert response.close_calls == 1


@pytest.mark.parametrize("headers", [
    {},
    {"Content-Type": "text/html"},
    {"Content-Type": "application/json, text/plain"},
    {"Content-Type": "application/json; charset=utf-16"},
])
def test_bounded_reader_rejects_unreviewed_mime_before_iteration(headers):
    response = _StreamingResponse(b'{"secret":"BODY_CANARY"}', headers=headers)

    with pytest.raises(provider_transport.ProviderResponseRejected) as error:
        provider_transport.read_bounded_json_response(
            response, max_bytes=1024)

    assert response.iterated is False
    assert response.close_calls == 1
    assert "BODY_CANARY" not in str(error.value)


@pytest.mark.parametrize("length", ["", "-1", "+1", "1.0", "1, 1"])
def test_bounded_reader_rejects_malformed_length_before_iteration(length):
    response = _StreamingResponse(b"{}", headers={
        "Content-Type": "application/json",
        "Content-Length": length,
    })

    with pytest.raises(provider_transport.ProviderResponseRejected):
        provider_transport.read_bounded_json_response(response, max_bytes=8)

    assert response.iterated is False
    assert response.close_calls == 1


def test_bounded_reader_rejects_oversized_declared_length_before_iteration():
    response = _StreamingResponse(b"{}", headers={
        "Content-Type": "application/json",
        "Content-Length": "9",
    })

    with pytest.raises(provider_transport.ProviderResponseRejected):
        provider_transport.read_bounded_json_response(response, max_bytes=8)

    assert response.iterated is False
    assert response.close_calls == 1


def test_bounded_reader_rejects_ambiguous_http_framing():
    response = _StreamingResponse(b"{}", headers={
        "Content-Type": "application/json",
        "Content-Length": "2",
        "Transfer-Encoding": "chunked",
    })

    with pytest.raises(provider_transport.ProviderResponseRejected):
        provider_transport.read_bounded_json_response(response, max_bytes=8)

    assert response.iterated is False


@pytest.mark.parametrize("declared", ["1", "3"])
def test_bounded_reader_rejects_dishonest_unencoded_length(declared):
    response = _StreamingResponse(b"{}", headers={
        "Content-Type": "application/json",
        "Content-Length": declared,
    })

    with pytest.raises(provider_transport.ProviderResponseRejected):
        provider_transport.read_bounded_json_response(response, max_bytes=8)

    assert response.close_calls == 1


def test_chunked_response_is_bounded_without_content_length():
    response = _StreamingResponse(b"", headers={
        "Content-Type": "application/json",
        "Transfer-Encoding": "chunked",
    }, chunks=[b'{"x":"', b"A" * 16, b'"}'])

    with pytest.raises(provider_transport.ProviderResponseRejected):
        provider_transport.read_bounded_json_response(response, max_bytes=12)

    assert response.close_calls == 1


@pytest.mark.parametrize("body", [
    b'{"value":',
    b'\xff',
    b'{"same":1,"same":2}',
    b'{"value":NaN}',
    b'{"value":Infinity}',
    (b"[" * 65) + b"0" + (b"]" * 65),
])
def test_bounded_reader_rejects_malformed_or_hostile_json(body):
    response = _StreamingResponse(body)

    with pytest.raises(provider_transport.ProviderResponseRejected) as error:
        provider_transport.read_bounded_json_response(
            response, max_bytes=len(body) + 1)

    assert error.value.__cause__ is None
    assert response.close_calls == 1


def test_json_nesting_scanner_ignores_delimiters_inside_strings():
    body = json.dumps({"text": "[" * 100 + "}" * 100}).encode("utf-8")
    response = _StreamingResponse(body)

    assert provider_transport.read_bounded_json_response(
        response, max_bytes=2048) == {
            "text": "[" * 100 + "}" * 100,
        }


def test_stream_deadline_is_checked_between_chunks():
    response = _StreamingResponse(b"{}", chunks=[b"{}"])
    ticks = iter([10.0, 12.1])

    with pytest.raises(
            provider_transport.ProviderResponseDeadlineExceeded):
        provider_transport.read_bounded_json_response(
            response, max_bytes=8, deadline_seconds=2,
            monotonic_fn=lambda: next(ticks))

    assert response.close_calls == 1


@pytest.mark.parametrize("error", [
    requests.exceptions.ReadTimeout("SECRET_TRANSPORT_CANARY"),
    requests.exceptions.ConnectionError("SECRET_TRANSPORT_CANARY"),
])
def test_stream_errors_are_recategorized_without_secret_exception_text(error):
    response = _StreamingResponse(b"", stream_error=error)

    with pytest.raises((
            provider_transport.ProviderResponseReadTimeout,
            provider_transport.ProviderResponseReadError,
    )) as raised:
        provider_transport.read_bounded_json_response(response, max_bytes=8)

    assert "SECRET_TRANSPORT_CANARY" not in str(raised.value)
    assert raised.value.__cause__ is None
    assert response.close_calls == 1


def test_preflight_rejection_never_invokes_json_parser(monkeypatch):
    response = _StreamingResponse(b'{"secret":"BODY_CANARY"}', headers={
        "Content-Type": "text/plain",
    })
    monkeypatch.setattr(
        provider_transport.json, "loads",
        lambda *_args, **_kwargs: pytest.fail("parser must not run"),
    )

    with pytest.raises(provider_transport.ProviderResponseRejected):
        provider_transport.read_bounded_json_response(response, max_bytes=1024)


def test_owned_response_closes_response_and_session_exactly_once():
    class Owner:
        def __init__(self):
            self.close_calls = 0

        def close(self):
            self.close_calls += 1

    response = _StreamingResponse(b"{}")
    owner = Owner()
    owned = provider_transport.OwnedHttpResponse(response, owner)

    owned.close()
    owned.close()

    assert response.close_calls == 1
    assert owner.close_calls == 1


def test_decoded_gzip_body_cannot_expand_past_stream_ceiling():
    decoded = json.dumps({"value": "A" * 4096}).encode("utf-8")
    compressed = gzip.compress(decoded)

    class Handler(BaseHTTPRequestHandler):
        def do_POST(self):
            length = int(self.headers.get("Content-Length", "0"))
            self.rfile.read(length)
            self.send_response(200)
            self.send_header("Content-Type", "application/json")
            self.send_header("Content-Encoding", "gzip")
            self.send_header("Content-Length", str(len(compressed)))
            self.end_headers()
            self.wfile.write(compressed)

        def log_message(self, *_args):
            return None

    server = ThreadingHTTPServer(("127.0.0.1", 0), Handler)
    thread = threading.Thread(target=server.serve_forever, daemon=True)
    thread.start()
    session = requests.Session()
    session.trust_env = False
    try:
        response = session.post(
            f"http://127.0.0.1:{server.server_port}/",
            data=b"{}", stream=True, timeout=(2, 2),
        )
        owned = provider_transport.OwnedHttpResponse(response, session)

        assert len(compressed) < 512
        with pytest.raises(provider_transport.ProviderResponseRejected):
            provider_transport.read_bounded_json_response(
                owned, max_bytes=512, deadline_seconds=2)
    finally:
        server.shutdown()
        server.server_close()
        thread.join(timeout=2)
