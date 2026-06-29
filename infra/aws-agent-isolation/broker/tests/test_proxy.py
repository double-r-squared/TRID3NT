"""Unit tests for the broker WS byte-proxy (proxy.py).

Covers the load-bearing proxy behavior WITHOUT a live ``websockets`` install:
  - frame-faithful DUPLEX relay (client->task AND task->client, verbatim, ordered)
  - close propagation when the CLIENT closes (-> the task leg is closed)
  - TASK-DROP close propagation (an abnormal 1006 upstream close surfaces to the
    client as a sane sendable code so ws.ts reconnect fires)
  - the close-code sanitizer (non-sendable 1004/1005/1006/1015/None -> 1001)
  - handle_connection rejects an unroutable connect with 4401 (no proxy).

A duck-typed ``FakeWS`` stands in for a websockets connection: it is async-
iterable (yields its scripted frames then signals close), and exposes
``send`` / ``close`` / ``close_code`` / ``close_reason`` -- the only surface the
proxy touches. ``proxy.py`` imports ``websockets`` only inside ``open_upstream``,
so the module imports cleanly here with no dependency.
"""

from __future__ import annotations

import asyncio
import sys
from pathlib import Path

import pytest

_BROKER_PARENT = str(Path(__file__).resolve().parents[2])
if _BROKER_PARENT not in sys.path:
    sys.path.insert(0, _BROKER_PARENT)

from broker.proxy import _sanitize_close_code, proxy_frames  # noqa: E402


_EOF = object()


class FakeClosed(Exception):
    """Stand-in for websockets.ConnectionClosed (raised on send-after-close)."""


class FakeWS:
    """A minimal duck-typed websockets connection.

    ``script`` frames are pre-queued and ALWAYS drained (in order) before the EOF
    sentinel, so a relay forwards every scripted frame regardless of when the
    other pump closes this leg -- the close() just appends a (never-reached) EOF.
    """

    def __init__(self, name="", script=None, auto_eof=True, end_code=1000):
        self.name = name
        self._queue: asyncio.Queue = asyncio.Queue()
        self.sent: list = []
        self.closed = False
        self.close_code = None
        self.close_reason = ""
        self._end_code = end_code
        for frame in script or []:
            self._queue.put_nowait(frame)
        if auto_eof:
            self._queue.put_nowait(_EOF)

    def __aiter__(self):
        return self

    async def __anext__(self):
        item = await self._queue.get()
        if item is _EOF:
            if self.close_code is None:
                self.close_code = self._end_code
            raise StopAsyncIteration
        return item

    async def send(self, message):
        if self.closed:
            raise FakeClosed("closed")
        self.sent.append(message)

    async def close(self, code=1000, reason=""):
        self.closed = True
        if self.close_code is None:
            self.close_code = code
        self.close_reason = reason
        self._queue.put_nowait(_EOF)  # unblock a pending __anext__


# --------------------------------------------------------------------------- #
# close-code sanitizer
# --------------------------------------------------------------------------- #
@pytest.mark.parametrize(
    "code,expected",
    [
        (1000, 1000),   # normal -> passes through
        (1001, 1001),
        (4401, 4401),   # app-defined -> sendable
        (None, 1001),   # no close frame -> going away
        (1006, 1001),   # abnormal (crash) -> not sendable -> going away
        (1005, 1001),   # no status -> not sendable
        (1015, 1001),   # TLS -> not sendable
    ],
)
def test_sanitize_close_code(code, expected):
    assert _sanitize_close_code(code) == expected


# --------------------------------------------------------------------------- #
# duplex relay
# --------------------------------------------------------------------------- #
def test_proxy_duplex_relay_is_frame_faithful():
    client = FakeWS("client", script=["a", b"b", "c"], end_code=1000)
    task = FakeWS("task", script=[b"x", "y"], end_code=1000)

    asyncio.run(proxy_frames(client, task))

    # client frames arrived at the task verbatim + in order; task frames at client.
    assert task.sent == ["a", b"b", "c"]
    assert client.sent == [b"x", "y"]
    # both legs torn down.
    assert client.closed and task.closed


def test_proxy_client_close_propagates_to_task():
    # Client sends two frames then closes normally; the task leg blocks until the
    # proxy closes it (auto_eof=False == "stays open until closed").
    client = FakeWS("client", script=["m1", "m2"], end_code=1000)
    task = FakeWS("task", script=[], auto_eof=False)

    asyncio.run(proxy_frames(client, task))

    assert task.sent == ["m1", "m2"]
    assert task.closed
    assert task.close_code == 1000  # the client's normal close propagated


def test_proxy_task_drop_propagates_sane_code_to_client():
    # Task drops abnormally (1006) after one frame; the client leg must be closed
    # with a SENDABLE code (1001) so ws.ts capped-backoff reconnects.
    task = FakeWS("task", script=["last-frame"], end_code=1006)
    client = FakeWS("client", script=[], auto_eof=False)

    asyncio.run(proxy_frames(client, task))

    assert client.sent == ["last-frame"]
    assert client.closed
    assert client.close_code == 1001  # sanitized from the upstream 1006 drop


def test_proxy_never_sends_after_close():
    # A leg that closes first must not receive sends afterward (no FakeClosed leak
    # out of proxy_frames -- it swallows ConnectionClosed-class errors).
    client = FakeWS("client", script=["only"], end_code=1000)
    task = FakeWS("task", script=[], auto_eof=False)
    # Should complete without raising.
    asyncio.run(proxy_frames(client, task))
    assert task.sent == ["only"]


# --------------------------------------------------------------------------- #
# handle_connection: unroutable connect -> 4401, no proxy
# --------------------------------------------------------------------------- #
def test_handle_connection_rejects_unroutable_with_4401():
    from broker.app import handle_connection
    from broker.routing import RoutingConfig

    cfg = RoutingConfig(
        routes_table="grace2_session_routes",
        users_table="grace2_users",
        users_firebase_uid_index="firebase_uid-index",
        ecs_cluster="grace2-agents",
        agent_task_definition="grace2-agent-session",
        agent_container_name="agent",
        agent_ws_port=8765,
        agent_health_port=8766,
        task_subnets=["subnet-a"],
        task_security_groups=["sg-agent"],
    )

    class RejectWS(FakeWS):
        def __init__(self):
            super().__init__("client", script=[], auto_eof=False)
            self.path = "/ws"  # no st/sid -> decide_route returns None

    ws = RejectWS()
    # ddb/ecs unused on the no-session_id reject path; pass simple stand-ins.
    asyncio.run(handle_connection(ws, ddb_resource=None, ecs_client=None, cfg=cfg, health_probe=lambda ip, p: True))
    assert ws.closed
    assert ws.close_code == 4401


if __name__ == "__main__":  # pragma: no cover
    raise SystemExit(pytest.main([__file__, "-q"]))
