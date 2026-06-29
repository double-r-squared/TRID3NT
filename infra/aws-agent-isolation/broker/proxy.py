"""Bidirectional WSS byte-proxy: client <-> per-session agent task.

COMPLETE (the route-decision lives in routing.py/app.py; this is the raw frame
relay the broker runs once a route is resolved). It honors every clause of the
spike's "preserve the streaming model" gate (section 2):

  - TRANSPARENT, FRAME-FAITHFUL relay. Each ``async for message in src`` yields a
    whole WS frame (str for text, bytes for binary) and we forward it verbatim
    with ``await dst.send(message)``. We do NOT parse, coalesce, or re-chunk -- the
    agent envelopes (agent-message-chunk, pipeline cards, confirmation envelopes,
    layer replay) and the client frames (user-message / auth-token / session-
    resume) cross byte-identical and in order. There is NO AgentCore 64KB cap on
    this hop, so ``max_size=None`` (no frame ceiling) and no chunking.

  - NO BROKER-IMPOSED IDLE TIMEOUT. The agent's 12s server-push DATA heartbeat
    keeps the connection never-idle; the proxy must let an idle-but-alive socket
    live indefinitely. ``open_upstream`` sets ``ping_interval=None`` so the broker
    adds NO keepalive of its own on the task leg (the agent + client own it), and
    the relay imposes no read deadline -- ``async for`` blocks forever until a
    real frame or a real close.

  - CLOSE PROPAGATION (full duplex teardown). Two pumps run under
    ``asyncio.gather``; when EITHER pump's source ends (close OR transport drop),
    its ``finally`` closes the OTHER leg with a propagated, sanitized close code.
    Closing one leg ends the other pump's ``async for`` (its source is now the
    just-closed peer), which closes the first leg back -- so a client close OR a
    task-side crash tears down BOTH directions and the client's ws.ts capped-
    backoff fires. A detached in-flight turn survives on the task
    (_SESSION_LIVE_TURNS) until the client redials the SAME session_id and the
    broker re-resolves the SAME task.

  - BACKPRESSURE WITHOUT A QUEUE. We ``await dst.send(...)`` before reading the
    next frame from ``src``, so there is no broker-side buffer to balloon: a slow
    destination throttles its source via TCP. No unbounded in-flight queue can
    form by construction.

  - EVENT-LOOP SAFE. Everything here is ``async`` (websockets asyncio API on both
    legs); no sync boto3 / file / compute runs on the loop. (The blocking
    provision path in routing.py is run OFF the loop via ``asyncio.to_thread`` in
    app.handle_connection.)

Library: the SAME ``websockets`` asyncio API the agent uses
(``websockets.asyncio``), so the frame surface matches exactly. The import is
LOCAL to ``open_upstream`` so this module stays importable (and unit-testable with
duck-typed fakes) in an env without ``websockets`` installed.
"""

from __future__ import annotations

import asyncio
import json
import logging
import os
import secrets
import time
from datetime import datetime, timezone
from typing import Optional

logger = logging.getLogger("grace2.broker.proxy")

#: The agent serves its WS on any path; ``/ws`` mirrors the public client URL.
AGENT_WS_PATH = "/ws"

#: COLD-PROVISION KEEPALIVE (broker-originated client-leg heartbeat).
#:
#: A first-connect for a session is a route MISS: the broker RunTask-provisions a
#: per-session Fargate agent, which is a COLD start -- measured ~40-48s wall-clock
#: before the upstream agent leg is connected and its own 12s data-heartbeat
#: begins flowing to the client. During that whole window the broker sends the
#: client ZERO application DATA frames (the handler is blocked in
#: ``decide_route`` -> ``to_thread`` provisioning, then in ``open_upstream``).
#:
#: The web client (web/src/ws.ts) runs an app-level watchdog: a ``session-resume``
#: ping every KEEPALIVE_INTERVAL_MS (25s) that arms a pong-deadline of
#: KEEPALIVE_PONG_TIMEOUT_MS (10s) cleared ONLY by an inbound *DATA* frame
#: (browsers hide WS PING control frames, so the broker's protocol-level pings do
#: NOT reset it -- the SAME reason the agent sends a data-heartbeat at all). With
#: the first DATA frame ~48s out but the deadline firing at ~35s, the real browser
#: force-reconnects MID-PROVISION, and each redial re-enters the provisioning wait
#: -> the reconnect churn observed only through the broker (never on the single box,
#: where the first frame lands in <1s).
#:
#: Fix: while the route is being resolved/provisioned AND while the upstream is
#: being dialed, the broker emits a lightweight ``heartbeat`` DATA frame on the
#: client leg every HEARTBEAT_INTERVAL_SECONDS -- BYTE-COMPATIBLE with the agent's
#: own heartbeat (services/agent/src/grace2_agent/server.py ``_heartbeat_loop``),
#: which ws.ts already treats as a no-op proof-of-life frame (its dispatch routes
#: an unknown ``heartbeat`` type to a no-op default and ``noteInboundActivity``
#: clears the pong deadline for EVERY inbound frame). The task cancels the instant
#: the proxy takes over, so once the agent's own heartbeat flows there is no
#: double-heartbeat. Default 8s is comfortably under the client's 10s deadline.
HEARTBEAT_INTERVAL_SECONDS: float = float(
    os.environ.get("BROKER_PROVISION_HEARTBEAT_SECONDS", "8.0")
)

#: Placeholder session_id on the broker's provisioning heartbeat. The client
#: routes a liveness frame by transport, not session, so a zeros-ULID is fine
#: (mirrors the agent's pre-handshake heartbeat session_id). 26 chars (ULID len).
_HEARTBEAT_PLACEHOLDER_SID = "00000000000000000000000000"

_CROCKFORD32 = "0123456789ABCDEFGHJKMNPQRSTVWXYZ"

#: RFC6455 close codes a peer must NOT *send* in a Close frame. If an inbound leg
#: ended with one of these (or none), we substitute a sane sendable code when
#: propagating to the other leg so ``close()`` does not itself raise.
_NON_SENDABLE_CLOSE_CODES = {1004, 1005, 1006, 1015}

#: websockets caps a close reason at 123 UTF-8 bytes; keep margin.
_MAX_CLOSE_REASON = 120


def _sanitize_close_code(code: Optional[int]) -> int:
    """Map a source-leg close code to a code we may legally SEND on the other leg.

    An abnormal/empty upstream close (e.g. a task crash -> 1006, or 1005 no-status)
    still has to surface to the client as a clean "going away" so the client's
    reconnect/backoff fires and re-resolves the SAME session_id. A normal/sendable
    code passes through unchanged.
    """
    if code is None or code in _NON_SENDABLE_CLOSE_CODES:
        return 1001  # going away
    return code


def _ulid() -> str:
    """A time-ordered 26-char Crockford-base32 ULID for the heartbeat id field.

    The client never validates this id (it reads only ``type`` / ``payload``),
    but a well-formed ULID keeps the frame indistinguishable from the agent's own
    heartbeat. 48-bit ms timestamp (10 chars) + 80 bits randomness (16 chars).
    """
    ts = int(time.time() * 1000)
    ts_chars = []
    for _ in range(10):
        ts_chars.append(_CROCKFORD32[ts & 0x1F])
        ts >>= 5
    rand = "".join(_CROCKFORD32[secrets.randbelow(32)] for _ in range(16))
    return "".join(reversed(ts_chars)) + rand


def _heartbeat_frame() -> str:
    """Build one ``heartbeat`` DATA frame byte-compatible with the agent's
    ``_heartbeat_loop`` so the web client's pong-deadline watchdog is satisfied."""
    now = datetime.now(timezone.utc).isoformat().replace("+00:00", "Z")
    return json.dumps(
        {
            "type": "heartbeat",
            "id": _ulid(),
            "ts": now,
            "session_id": _HEARTBEAT_PLACEHOLDER_SID,
            "case_id": None,
            "payload": {"ts": now},
        }
    )


async def client_provision_keepalive(
    client_ws, *, interval: float = HEARTBEAT_INTERVAL_SECONDS
) -> None:
    """Emit a ``heartbeat`` DATA frame on the CLIENT leg every ``interval`` seconds.

    Run as a background task ONLY for the cold-provision window -- from before the
    blocking route resolution until the upstream proxy takes over -- so the web
    client's 10s DATA-frame pong-deadline never fires before the agent task is
    reachable (see HEARTBEAT_INTERVAL_SECONDS for the full rationale). It is
    cancelled the instant ``proxy_frames`` begins, so the agent's own 12s
    heartbeat is the sole keepalive once frames flow (no double-heartbeat).

    A per-send failure is swallowed (the socket may be closing); a real close ends
    the owning handler which cancels this task. Cancellation propagates cleanly.
    """
    while True:
        await asyncio.sleep(interval)
        try:
            await client_ws.send(_heartbeat_frame())
        except asyncio.CancelledError:
            raise
        except Exception as exc:  # noqa: BLE001 - transport liveness; never crash
            logger.debug(
                "provision keepalive send ended: %s", type(exc).__name__
            )
            return


async def open_upstream(private_ip: str, port: int, *, path: str = AGENT_WS_PATH):
    """Open a WS to the per-session agent task's private IP:port.

    ``ping_interval=None`` (the broker adds NO keepalive -- the agent listener pings
    and the client app-keepalive cover liveness) and ``max_size=None`` (no frame
    cap) are load-bearing per the contract above. ``open_timeout`` bounds the dial
    so a wedged task fails the connect instead of hanging the client coroutine.
    """
    from websockets.asyncio.client import connect  # local: optional dep at test time

    uri = f"ws://{private_ip}:{port}{path}"
    logger.info("broker upstream connect -> %s", uri)
    return await connect(
        uri,
        ping_interval=None,
        max_size=None,
        open_timeout=10,
        close_timeout=10,
    )


async def _safe_close(ws, code: int, reason: str) -> None:
    """Close a leg, swallowing the already-closing/closed case (close is the
    teardown signal, not an assertion)."""
    try:
        await ws.close(code=code, reason=(reason or "")[:_MAX_CLOSE_REASON])
    except Exception as exc:  # noqa: BLE001 - already closed / half-closed is fine
        logger.debug("close on already-closing leg ignored: %s", type(exc).__name__)


async def proxy_frames(client_ws, upstream_ws) -> None:
    """Pump frames both directions until either side closes, then tear down both.

    Frame-faithful, no idle timeout, close-propagating, inherently backpressured
    (see the module contract). Returns when BOTH pumps have finished, i.e. both
    legs are closed.
    """

    async def pump(src, dst, tag: str) -> None:
        try:
            async for message in src:
                # await-per-frame == backpressure; verbatim == frame-faithful.
                await dst.send(message)
        except Exception as exc:  # noqa: BLE001 - ConnectionClosed / transport drop
            logger.debug("proxy pump %s ended: %s", tag, type(exc).__name__)
        finally:
            # Propagate THIS source's close onto the other leg so the teardown
            # cascades both ways (a task-side drop closes the client and vice
            # versa). Sanitize because a crash yields a non-sendable 1006/None.
            code = _sanitize_close_code(getattr(src, "close_code", None))
            reason = getattr(src, "close_reason", "") or ""
            await _safe_close(dst, code, reason)

    await asyncio.gather(
        pump(client_ws, upstream_ws, "client->task"),
        pump(upstream_ws, client_ws, "task->client"),
        return_exceptions=True,
    )
    logger.info("proxy session torn down (both legs closed)")
