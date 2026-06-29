"""GRACE-2 session broker -- the runnable server entry.

This is the process the broker container runs (``python -m broker.server``). It:

  1. Serves a single port (``BROKER_PORT``, default 8080 -- the ALB target) with
     the ``websockets`` asyncio server, the SAME library/surface the agent uses.
  2. Answers ``GET /healthz`` (HTTP, no WS upgrade) for the ALB target-group +
     the ECS container health check -- a liveness, NOT a per-session probe.
  3. Upgrades every other connection to WS and hands it to
     ``app.handle_connection``, which verifies Cognito, resolves the route
     (HIT -> reuse the task; MISS -> RunTask + wait :8766 health + write the
     route), then byte-proxies the frames to the per-session agent task.

State lives entirely in DynamoDB (the routes table); this process is stateless,
so it scales horizontally behind the ALB and a dropped broker re-resolves the
SAME agent task on reconnect.

Keepalive: the broker is the WS SERVER to the client, so it pings the CLIENT leg
at 20s/20s -- mirroring the agent listener it replaces -- to reap a truly-dead
peer. The TASK leg (open_upstream) sets ping_interval=None so the broker adds no
keepalive there (the agent + client own it). ``max_size=None`` on both legs: no
frame-size cap (the agent envelopes can be large; there is no AgentCore 64KB
ceiling on this hop).

The boto3 clients are created ONCE here and injected into every handler; the
route-decision modules never create a module-level client (so the unit tests
never touch live AWS).
"""

from __future__ import annotations

import asyncio
import json
import logging
import os
import urllib.request
from http import HTTPStatus
from urllib.parse import urlsplit

from .app import handle_connection, healthz, load_config

logger = logging.getLogger("grace2.broker.server")

#: Bound on the synchronous /api/health probe of a freshly provisioned task.
_HEALTH_PROBE_TIMEOUT_S = 4.0


def make_health_probe(region: str):
    """Build the ``health_probe(private_ip, port) -> bool`` the provision path polls.

    Green == HTTP 200 AND the agent's busy contract is present
    (``{"ok", "active_connections", "busy"}``) -- the SAME contract the single-box
    autostop idle-check reads, so a task is only routed to once its agent loop is
    actually serving. SYNC by design: it is only ever called from
    ``provision_task``, which app.handle_connection runs OFF the event loop via
    ``asyncio.to_thread`` (so this never blocks the broker loop).
    """

    def health_probe(private_ip: str, port: int) -> bool:
        url = f"http://{private_ip}:{port}/api/health"
        try:
            with urllib.request.urlopen(url, timeout=_HEALTH_PROBE_TIMEOUT_S) as resp:
                if resp.status != 200:
                    return False
                data = json.loads(resp.read() or b"{}")
        except Exception as exc:  # noqa: BLE001 - not-yet-up is the normal case
            logger.debug("health probe %s not ready: %s", url, type(exc).__name__)
            return False
        return (
            bool(data.get("ok"))
            and "active_connections" in data
            and "busy" in data
        )

    return health_probe


def _process_request(connection, request):
    """websockets asyncio ``process_request`` hook: short-circuit ``GET /healthz``
    with a 200 HTTP response (no WS upgrade); return None for everything else so
    the WS handshake proceeds."""
    path = urlsplit(request.path).path
    if path == "/healthz":
        body = json.dumps(healthz()) + "\n"
        return connection.respond(HTTPStatus.OK, body)
    return None


async def _amain() -> None:
    from websockets.asyncio.server import serve

    cfg = load_config()
    region = (
        os.environ.get("AWS_REGION")
        or os.environ.get("GRACE2_AWS_REGION")
        or "us-west-2"
    )
    port = int(os.environ.get("BROKER_PORT", "8080"))

    import boto3  # local import: the unit tests never import this module

    ddb_resource = boto3.resource("dynamodb", region_name=region)
    ecs_client = boto3.client("ecs", region_name=region)
    health_probe = make_health_probe(region)

    async def _handler(connection) -> None:
        await handle_connection(connection, ddb_resource, ecs_client, cfg, health_probe)

    async with serve(
        _handler,
        "0.0.0.0",
        port,
        process_request=_process_request,
        ping_interval=20,   # broker pings the CLIENT leg (it is the server here)
        ping_timeout=20,
        max_size=None,      # no frame-size cap (large agent envelopes)
    ):
        logger.info(
            "grace2 session broker listening on :%d (cluster=%s task-def=%s routes=%s)",
            port, cfg.ecs_cluster, cfg.agent_task_definition, cfg.routes_table,
        )
        await asyncio.Future()  # serve forever


def main() -> None:
    logging.basicConfig(
        level=os.environ.get("GRACE2_LOG_LEVEL", "INFO"),
        format="%(asctime)s %(levelname)s %(name)s %(message)s",
    )
    asyncio.run(_amain())


if __name__ == "__main__":  # pragma: no cover
    main()
