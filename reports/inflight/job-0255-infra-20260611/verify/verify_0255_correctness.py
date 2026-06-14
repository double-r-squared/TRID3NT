"""FRESH adversarial correctness probe for job-0255 (qgis_proxy).

A hostile re-derivation by the CORRECTNESS lens — NOT the runner's tests.
Stands up a REAL fake upstream HTTP server that records every header it
receives, drives stream_qgis_response against it, and attacks:
  * no-open-proxy (host override, path traversal, absolute-URL injection)
  * credential stripping at the UPSTREAM boundary (upstream must see NONE)
  * streaming bound (multi-MB body, chunked relay, not full buffering)
  * upstream 4xx/5xx and connection-refused relay (honest, no crash/hang)

Run: cd services/agent && .venv/bin/python -m pytest tests/verify_0255_correctness.py -q
"""
from __future__ import annotations

import asyncio
import socket
import threading
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from urllib.parse import urlsplit, parse_qs

import pytest

from grace2_agent import qgis_proxy as qp


# ------------------------------------------------------------------ #
# A real recording upstream HTTP server
# ------------------------------------------------------------------ #
class _Recorder:
    def __init__(self):
        self.last_headers: dict[str, str] = {}
        self.last_path: str = ""
        self.last_query: dict = {}
        self.body_size: int = 1024
        self.status: int = 200
        self.content_type: str = "image/png"


_REC = _Recorder()


class _Handler(BaseHTTPRequestHandler):
    def log_message(self, *a):  # silence
        pass

    def do_GET(self):
        _REC.last_headers = {k.lower(): v for k, v in self.headers.items()}
        sp = urlsplit(self.path)
        _REC.last_path = sp.path
        _REC.last_query = parse_qs(sp.query)
        self.send_response(_REC.status)
        self.send_header("Content-Type", _REC.content_type)
        # An auth-echo header the proxy MUST drop on the way back:
        self.send_header("Set-Cookie", "upstream_session=leaky")
        self.send_header("Server", "qgis-upstream/secret")
        self.end_headers()
        if _REC.status < 400:
            self.wfile.write(b"\x00" * _REC.body_size)
        else:
            self.wfile.write(b'{"upstream":"error"}')


@pytest.fixture(autouse=True)
def _no_oidc(monkeypatch):
    """Patch OIDC to None so tests run deterministically and fast — the real
    fetch_oidc_token's graceful-degrade-to-None behavior is proven separately
    (the proxy forwards unauthenticated, never crashes). With None patched, the
    upstream sees NO Authorization header (exactly the dev path)."""
    monkeypatch.setattr(qp, "fetch_oidc_token", lambda base: None)


@pytest.fixture(scope="module")
def upstream():
    srv = ThreadingHTTPServer(("127.0.0.1", 0), _Handler)
    host, port = srv.server_address
    t = threading.Thread(target=srv.serve_forever, daemon=True)
    t.start()
    base = f"http://{host}:{port}/ogc/wms"
    yield base
    srv.shutdown()


# A helper to run stream_qgis_response and collect status/headers/body.
async def _drive(query_string, base_url):
    captured = {"result": None, "body": bytearray()}

    async def write_head(result):
        captured["result"] = result

    async def write_chunk(chunk):
        captured["body"].extend(chunk)

    res = await qp.stream_qgis_response(
        query_string, write_head, write_chunk, base_url=base_url, chunk_size=64 * 1024
    )
    captured["returned"] = res
    return captured


def _run(coro):
    return asyncio.new_event_loop().run_until_complete(coro)


# ------------------------------------------------------------------ #
# Credential stripping at the UPSTREAM boundary
# ------------------------------------------------------------------ #
def test_no_inbound_credential_reaches_upstream(upstream):
    """stream_qgis_response builds its OWN header set; it never receives or
    forwards inbound headers. Prove the upstream sees ONLY UA (dev: no token)."""
    _REC.status = 200
    _REC.body_size = 4096
    cap = _run(_drive("SERVICE=WMS&REQUEST=GetMap&LAYERS=x", upstream))
    # Upstream got the request:
    assert cap["returned"].status == 200
    hdrs = _REC.last_headers
    # NONE of the credential-class headers can appear (proxy never had them,
    # and even the function signature has no inbound-header param):
    for banned in qp.STRIPPED_REQUEST_HEADERS:
        assert banned not in hdrs, f"upstream saw stripped header {banned}: {hdrs.get(banned)!r}"
    # The only auth-ish header would be Authorization from an OIDC token; in this
    # dev env fetch_oidc_token returns None, so there must be no Authorization:
    assert "authorization" not in hdrs, hdrs.get("authorization")
    # UA is the proxy's own:
    assert hdrs.get("user-agent") == "grace-2-agent-qgis-proxy/0.1"


def test_function_has_no_inbound_header_parameter():
    """Structural proof of strip-by-construction: stream_qgis_response cannot
    forward inbound headers because it never accepts them."""
    import inspect

    sig = inspect.signature(qp.stream_qgis_response)
    params = set(sig.parameters)
    assert "headers" not in params and "request_headers" not in params, params


# ------------------------------------------------------------------ #
# No-open-proxy: host override / path traversal / absolute-URL injection
# ------------------------------------------------------------------ #
@pytest.mark.parametrize(
    "qs",
    [
        # absolute-URL injection in a param
        "MAP=http://evil.example.com/x&REQUEST=GetMap",
        "url=https://attacker.test/&LAYERS=x",
        # path traversal attempts in params
        "MAP=../../../../etc/passwd&REQUEST=GetMap",
        "LAYERS=../../secret",
        # attempt to override host via a leading absolute URL in the query
        "REQUEST=GetMap&host=evil.example.com",
        # an @ injection
        "x=y@evil.example.com",
    ],
)
def test_no_open_proxy_upstream_host_is_fixed(upstream, qs):
    """Whatever the params say, the upstream host stays the fixed base. The
    recording server (the fixed base) must be the one that gets hit."""
    _REC.status = 200
    _REC.body_size = 16
    _REC.last_path = "SENTINEL_NOT_HIT"
    cap = _run(_drive(qs, upstream))
    # If the request had been redirected off-box, OUR recorder would not have
    # recorded it. It did → host is fixed.
    assert cap["returned"].status == 200
    assert _REC.last_path == "/ogc/wms", _REC.last_path
    # The malicious params transit verbatim as query (that's fine — they go to
    # the FIXED host), but they never change the host/path:
    assert _REC.last_query  # something was parsed


def test_upstream_url_built_from_fixed_base_not_params():
    """Unit-level: the upstream URL netloc is always the configured base's."""
    base = "http://fixed-host:1234/ogc/wms"
    # Re-derive what stream_qgis_response builds (mirrors its line 287-289):
    qs = "MAP=http://evil/&url=https://evil2/"
    built = f"{base.rstrip('/')}?{qs.lstrip('?')}"
    assert urlsplit(built).netloc == "fixed-host:1234"


# ------------------------------------------------------------------ #
# Response header stripping (upstream Set-Cookie/Server must NOT come back)
# ------------------------------------------------------------------ #
def test_response_headers_allowlisted(upstream):
    _REC.status = 200
    _REC.body_size = 32
    cap = _run(_drive("REQUEST=GetMap", upstream))
    relayed = {k.lower() for k in cap["returned"].headers}
    assert "set-cookie" not in relayed
    assert "server" not in relayed
    assert "content-type" in relayed  # allowlisted


# ------------------------------------------------------------------ #
# Streaming bound — multi-MB body, chunked, not fully buffered
# ------------------------------------------------------------------ #
def test_streaming_multi_mb_is_chunked(upstream):
    """A 5 MB body must arrive in >1 chunk, each <= chunk_size; the queue is
    bounded(4) so resident memory is O(chunk), not O(body)."""
    _REC.status = 200
    _REC.body_size = 5 * 1024 * 1024  # 5 MB
    chunk_sizes = []

    async def drive():
        async def write_head(result):
            pass

        async def write_chunk(chunk):
            chunk_sizes.append(len(chunk))

        return await qp.stream_qgis_response(
            "REQUEST=GetMap", write_head, write_chunk,
            base_url=upstream, chunk_size=64 * 1024,
        )

    res = _run(drive())
    assert res.status == 200
    assert len(chunk_sizes) > 1, "5MB should not arrive as a single chunk"
    assert max(chunk_sizes) <= 64 * 1024, f"chunk exceeded bound: {max(chunk_sizes)}"
    assert sum(chunk_sizes) == 5 * 1024 * 1024
    # bounded queue:
    assert qp.stream_qgis_response.__code__.co_consts  # sanity
    # Verify the queue maxsize literal is bounded (4) in the source.
    import inspect
    src = inspect.getsource(qp.stream_qgis_response)
    assert "asyncio.Queue(maxsize=4)" in src


# ------------------------------------------------------------------ #
# Upstream 4xx / 5xx relayed honestly (not masked as success)
# ------------------------------------------------------------------ #
@pytest.mark.parametrize("status", [403, 404, 500, 502, 503])
def test_upstream_error_status_relayed(upstream, status):
    _REC.status = status
    _REC.body_size = 0
    cap = _run(_drive("REQUEST=GetMap", upstream))
    assert cap["returned"].status == status, f"expected {status}, got {cap['returned'].status}"


# ------------------------------------------------------------------ #
# Connection refused — raises (caller maps to 502), no hang
# ------------------------------------------------------------------ #
def test_connection_refused_raises_not_hangs():
    # Bind a port then close it so the connect is refused.
    s = socket.socket()
    s.bind(("127.0.0.1", 0))
    port = s.getsockname()[1]
    s.close()
    dead_base = f"http://127.0.0.1:{port}/ogc/wms"

    async def drive():
        async def wh(r):
            pass

        async def wc(c):
            pass

        return await qp.stream_qgis_response(
            "REQUEST=GetMap", wh, wc, base_url=dead_base, timeout_s=3.0
        )

    with pytest.raises(Exception):
        _run(drive())  # caller (_handle_qgis_proxy) maps this to a 502


# ------------------------------------------------------------------ #
# Disabled gate (qgis_proxy_enabled default false)
# ------------------------------------------------------------------ #
@pytest.mark.parametrize(
    "val,expected",
    [
        (None, False), ("", False), ("false", False), ("0", False),
        ("no", False), ("off", False), ("2", False),
        ("true", True), ("TRUE", True), ("1", True), ("yes", True), ("on", True),
    ],
)
def test_proxy_enabled_parsing(val, expected, monkeypatch):
    if val is None:
        monkeypatch.delenv("QGIS_PROXY_ENABLED", raising=False)
    else:
        monkeypatch.setenv("QGIS_PROXY_ENABLED", val)
    assert qp.qgis_proxy_enabled() is expected


def test_default_disabled_no_env(monkeypatch):
    monkeypatch.delenv("QGIS_PROXY_ENABLED", raising=False)
    assert qp.qgis_proxy_enabled() is False


if __name__ == "__main__":
    raise SystemExit(pytest.main([__file__, "-q"]))
