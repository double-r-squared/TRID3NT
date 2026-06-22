"""Bidirectional WSS byte-proxy: client <-> per-session agent task.

SKELETON (the spike scopes the raw byte-proxy plumbing as a documented
skeleton/TODO; the route-decision in routing.py/app.py is the concrete part).

CONTRACT the real implementation must honor (all load-bearing, from the spike's
"preserve the streaming model" gate, section 2):

  - TRANSPARENT, FRAME-FAITHFUL relay. Forward every WS frame verbatim in BOTH
    directions: client->task and task->client. Text AND binary. Do NOT parse,
    buffer-coalesce, or re-chunk frames -- the agent's envelopes (agent-message-
    chunk, pipeline cards, confirmation envelopes, layer replay) and the client's
    user-message/auth-token/session-resume must arrive byte-identical and in
    order. (Unlike AgentCore-LATER, there is NO 64KB frame cap here, so no
    chunking is needed.)

  - NO BROKER-IMPOSED IDLE TIMEOUT. The agent pushes a 12s server-push DATA
    heartbeat to defeat the browser's ~30s control-frame-blindness reconnect
    storm. The proxy must let an idle-but-alive connection live indefinitely (the
    ALB idle timeout is 4000s; the heartbeat keeps it never-idle). Set the
    upstream/downstream read timeouts to None / >> 12s.

  - PASS PING/PONG THROUGH (or keep BOTH legs alive). The agent runs
    ping_interval=20/ping_timeout=20 on its own listener; let control frames flow
    so the agent's keepalive and the client's reach each other, OR run an
    independent keepalive on each leg. Either way a dropped PONG on one leg must
    not silently wedge the other.

  - CLOSE PROPAGATION. When EITHER side closes (or errors), close the OTHER with
    a sane code so the client's reconnect logic (ws.ts capped-backoff) fires and
    a detached in-flight turn survives on the task (_SESSION_LIVE_TURNS) until the
    client redials the SAME session_id and the broker re-resolves the SAME task.

  - BACKPRESSURE. Do not let a slow client balloon broker memory: bound the
    in-flight queue per direction; on overflow, close (the client reconnects).

IMPLEMENTATION SKETCH (websockets library, same as the agent):

    import asyncio
    import websockets

    async def open_upstream(private_ip: str, port: int):
        # ws:// inside the VPC (TLS terminates at the ALB; the broker->task hop is
        # private). ping_interval=None so the proxy adds no keepalive of its own
        # (the agent + client provide it); max_size=None so no frame-size cap.
        uri = f"ws://{private_ip}:{port}/ws"   # TODO: confirm the agent WS path
        return await websockets.connect(
            uri, ping_interval=None, max_size=None, open_timeout=10, close_timeout=10,
        )

    async def proxy_frames(client_ws, upstream_ws):
        async def pump(src, dst):
            try:
                async for message in src:          # text or bytes, verbatim
                    await dst.send(message)
            except websockets.ConnectionClosed:
                pass
            finally:
                await dst.close()
        await asyncio.gather(
            pump(client_ws, upstream_ws),
            pump(upstream_ws, client_ws),
        )

The functions below are stubs that raise NotImplementedError so the import wiring
is real (app.py imports them) while the plumbing is explicitly pending the canary.
"""

from __future__ import annotations

import logging

logger = logging.getLogger("grace2.broker.proxy")


async def open_upstream(private_ip: str, port: int):
    """Open a WS to the per-session agent task. SKELETON -- see the sketch above.

    Real impl: ``websockets.connect(f"ws://{private_ip}:{port}/ws",
    ping_interval=None, max_size=None, ...)``. ping_interval=None and
    max_size=None are load-bearing (no broker keepalive override, no frame cap).
    """
    raise NotImplementedError(
        "broker WS upstream connect is a documented skeleton (proxy.py) -- "
        "wire websockets.connect per the sketch during the canary"
    )


async def proxy_frames(client_ws, upstream_ws) -> None:
    """Pump frames both directions until either side closes. SKELETON.

    Real impl: two ``async for message in src: await dst.send(message)`` pumps
    under ``asyncio.gather``, frame-faithful, no idle timeout, close-propagating.
    """
    raise NotImplementedError(
        "broker bidirectional frame proxy is a documented skeleton (proxy.py) -- "
        "wire the two-pump gather per the sketch during the canary"
    )
