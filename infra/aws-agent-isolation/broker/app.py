"""GRACE-2 session broker -- the connection-time control flow.

This is the thin always-on tier that turns the shared single-box agent into
Fargate-per-session isolation (reports/design/agent_isolation_spike.md). It owns
NO session state (all state is in DynamoDB); it just routes each WSS connection to
the right per-session agent task.

PER NEW WSS CONNECTION (the concrete flow -- routing.py does the heavy lifting):

  1. Extract the Cognito ID token + session_id PRE-UPGRADE (see _extract_identity).
  2. cognito_verify(token) -> claims{uid=sub}     (cognito_verify.py, zero-drift)
  3. resolve_user_ulid(sub) -> internal ULID      (users firebase_uid-index GSI)
  4. resolve_or_provision(user_ulid, session_id)  (HIT -> task; MISS -> RunTask +
     wait :8766 health + write route)             (routing.py)
  5. bidirectionally proxy the WSS frames task <-> client (proxy.py).

The HTTP control flow + the route decision are CONCRETE here, and the raw WS
byte-proxy in proxy.py + the runnable server entry in server.py are complete.

PRE-UPGRADE IDENTITY (the one net-new client-coupling -- spike section 9.2):
  Today the Cognito ID token + session_id ride IN-BAND as the post-connect
  ``auth-token`` / ``session-resume`` envelopes (ws.ts chose this over the
  subprotocol because chrome rejects an oversize subprotocol header for a long
  JWT -- OQ-0123). For ROUTING the broker needs them BEFORE the upgrade. The
  options, in preference order (decided in the canary, not here):
    (a) a short-lived connect query token (?st=<jwt|exchange-code>&sid=<session>)
        -- simplest for the browser; keep the token short-lived/single-use.
    (b) the ``Sec-WebSocket-Protocol`` ``base64UrlBearerAuthorization`` subprotocol
        (the same surface AgentCore-LATER would need -- so the work is not wasted).
  _extract_identity below reads BOTH a query param and a subprotocol so either
  client change works; it is the single seam to adapt when the client lands the
  pre-upgrade carrier. The agent's in-band ``_ensure_auth_handshake`` stays the
  SECOND, authoritative check inside the task -- the broker's verify is only for
  routing.

The server uses the ``websockets`` asyncio API (the same surface the agent serves
on) so the byte-proxy reuses its frame APIs; the upgrade/serve wiring lives in
server.py. The route-decision functions here are fully unit-tested (tests/) with
mocked AWS + a fake verifier; the proxy is tested with duck-typed fakes.
"""

from __future__ import annotations

import asyncio
import logging
import os
import threading
from collections import defaultdict
from typing import Optional, Tuple
from urllib.parse import parse_qs, urlsplit

from .cognito_verify import cognito_verify
from .routing import (
    RoutingConfig,
    Route,
    provision_user,
    resolve_or_provision,
    resolve_user_ulid,
)

logger = logging.getLogger("grace2.broker.app")


# --------------------------------------------------------------------------- #
# Config from env (set by the broker task definition -- see broker.tf).
# --------------------------------------------------------------------------- #
def load_config() -> RoutingConfig:
    def _csv(name: str) -> list[str]:
        return [s.strip() for s in os.environ.get(name, "").split(",") if s.strip()]

    return RoutingConfig(
        routes_table=os.environ.get("ROUTES_TABLE", "grace2_session_routes"),
        users_table=os.environ.get("USERS_TABLE", "trid3nt_users"),
        users_firebase_uid_index=os.environ.get("USERS_FIREBASE_UID_INDEX", "firebase_uid-index"),
        ecs_cluster=os.environ.get("ECS_CLUSTER", "grace2-agents"),
        agent_task_definition=os.environ.get("AGENT_TASK_DEFINITION", "grace2-agent-session"),
        agent_container_name=os.environ.get("AGENT_CONTAINER_NAME", "agent"),
        agent_ws_port=int(os.environ.get("AGENT_WS_PORT", "8765")),
        agent_health_port=int(os.environ.get("AGENT_HEALTH_PORT", "8766")),
        task_subnets=_csv("TASK_SUBNETS"),
        task_security_groups=_csv("TASK_SECURITY_GROUPS"),
        route_ttl_seconds=int(os.environ.get("ROUTE_TTL_SECONDS", "86400")),
    )


# --------------------------------------------------------------------------- #
# Per-(user, session) provisioning lock so a tab's two near-simultaneous sockets
# do not double-RunTask. The SECOND socket waits, re-reads, and HITs the row the
# first wrote -> both land on the SAME task (the convergence the agent needs).
# --------------------------------------------------------------------------- #
_provision_locks: "defaultdict[Tuple[str, str], threading.Lock]" = defaultdict(threading.Lock)
_provision_locks_guard = threading.Lock()


def _lock_for(user_ulid: str, session_id: str) -> threading.Lock:
    key = (user_ulid, session_id)
    with _provision_locks_guard:
        return _provision_locks[key]


# --------------------------------------------------------------------------- #
# Per-sub first-connect-provisioning lock. A brand-new sub has NO ULID yet, so the
# per-(user_ulid, session_id) lock above cannot serialize its dual sockets (both
# resolve None). This second lock keys on the sub so a tab's App + Chat sockets do
# not both mint a users row -- the first creates it, the second waits, re-reads,
# and reuses it (the same convergence pattern, one level earlier).
# --------------------------------------------------------------------------- #
_user_provision_locks: "defaultdict[str, threading.Lock]" = defaultdict(threading.Lock)
_user_provision_locks_guard = threading.Lock()


def _user_lock_for(sub: str) -> threading.Lock:
    with _user_provision_locks_guard:
        return _user_provision_locks[sub]


# --------------------------------------------------------------------------- #
# Pre-upgrade identity extraction (the single client-coupling seam).
# --------------------------------------------------------------------------- #
def _extract_identity(
    request_uri: str, subprotocols: Optional[list[str]] = None
) -> Tuple[Optional[str], Optional[str]]:
    """Return (token, session_id) from the connect, or (None, None).

    Reads BOTH a query param and a subprotocol so EITHER client change works:
      - query:        ?st=<token>&sid=<session_id>
      - subprotocol:  ["grace2.session.<session_id>",
                       "base64UrlBearerAuthorization.<token>"]
    The session_id is REQUIRED for routing (it is the SK of the route). The token
    may be absent -> anonymous fallback (the broker keys the route on the
    client-replayed anonymous ULID, exactly like the agent's sticky-anon path;
    that anon-ULID carrier is a documented TODO -- the canary uses the
    authenticated path first).
    """
    token: Optional[str] = None
    session_id: Optional[str] = None

    # Query params.
    try:
        qs = parse_qs(urlsplit(request_uri).query)
        if qs.get("st"):
            token = qs["st"][0]
        if qs.get("sid"):
            session_id = qs["sid"][0]
    except Exception:  # noqa: BLE001 - a malformed URI yields no identity
        pass

    # Subprotocols (override query if present -- the subprotocol is the more
    # tamper-resistant carrier).
    for proto in subprotocols or []:
        if proto.startswith("base64UrlBearerAuthorization."):
            token = proto.split(".", 1)[1] or token
        elif proto.startswith("grace2.session."):
            session_id = proto.split("grace2.session.", 1)[1] or session_id

    return token, session_id


# --------------------------------------------------------------------------- #
# The connection decision: identity -> ULID -> route. Returns a Route to proxy to
# or None to reject. AWS clients are injected so this is unit-testable.
# --------------------------------------------------------------------------- #
def decide_route(
    ddb_resource,
    ecs_client,
    cfg: RoutingConfig,
    *,
    request_uri: str,
    subprotocols: Optional[list[str]],
    health_probe,
    verify=cognito_verify,
) -> Optional[Route]:
    """Run steps 1-4. Returns the Route to proxy to, or None to reject the
    connect (caller closes with an appropriate WS close code)."""
    token, session_id = _extract_identity(request_uri, subprotocols)
    if not session_id:
        logger.info("connect rejected: no session_id (routing key) present")
        return None

    claims = verify(token) if token else None
    if claims is None:
        # Authenticated path required for the canary. Anonymous-ULID routing is a
        # documented TODO (key on the client-replayed anon ULID like the agent).
        logger.info("connect rejected: token did not verify (anonymous routing is a TODO)")
        return None

    sub = claims.get("uid")
    user_ulid = resolve_user_ulid(ddb_resource, cfg, sub)
    if not user_ulid:
        # First-connect provisioning. A brand-new verified sub (e.g. a code-gate
        # demo user minted by the demo-token Lambda) has a Cognito identity but no
        # users row yet -- the agent normally creates it IN-BAND on first connect,
        # but the broker resolves sub->ULID BEFORE the agent task is ever reached,
        # so it used to reject here (chicken-and-egg). Mint the row now, mirroring
        # auth_handshake._resolve_or_provision_user; the agent's in-band handshake
        # then FINDS this row instead of forking a second identity. Serialized
        # per-sub so a tab's two sockets do not both create (the second re-reads
        # under the lock and reuses the first's row).
        user_lock = _user_lock_for(sub)
        with user_lock:
            user_ulid = resolve_user_ulid(ddb_resource, cfg, sub)  # re-read under lock
            if not user_ulid:
                user_ulid = provision_user(
                    ddb_resource,
                    cfg,
                    sub,
                    email=claims.get("email"),
                    display_name=claims.get("name"),
                )
        if not user_ulid:
            logger.info("connect rejected: first-connect user provisioning failed for sub")
            return None

    lock = _lock_for(user_ulid, session_id)
    with lock:
        return resolve_or_provision(
            ddb_resource, ecs_client, cfg, user_ulid, session_id, health_probe=health_probe
        )


# --------------------------------------------------------------------------- #
# Per-connection handler (the route decision is concrete; proxy.py is the
# completed byte-proxy). The runnable server entry lives in server.py.
# --------------------------------------------------------------------------- #
def _connection_path(client_ws) -> str:
    """Pull the connect request path from EITHER the test-mock surface (.path) or
    the websockets asyncio ServerConnection surface (.request.path)."""
    path = getattr(client_ws, "path", None)
    if path:
        return path
    req = getattr(client_ws, "request", None)
    return getattr(req, "path", "") if req is not None else ""


def _connection_subprotocols(client_ws) -> list[str]:
    """Requested subprotocols from EITHER an explicit attribute (test mock) or the
    ``Sec-WebSocket-Protocol`` request header (the asyncio ServerConnection)."""
    explicit = getattr(client_ws, "requested_subprotocols", None)
    if explicit:
        return list(explicit)
    req = getattr(client_ws, "request", None)
    headers = getattr(req, "headers", None) if req is not None else None
    if headers is None:
        return []
    try:
        raw = headers.get("Sec-WebSocket-Protocol", "") or ""
    except Exception:  # noqa: BLE001 - non-dict header surface
        return []
    return [p.strip() for p in raw.split(",") if p.strip()]


async def handle_connection(client_ws, ddb_resource, ecs_client, cfg: RoutingConfig, health_probe):
    """Per-connection coroutine: decide the route, then proxy frames.

    ``client_ws`` is a ``websockets`` asyncio ServerConnection (it exposes
    ``.request.path`` / ``.request.headers`` and async iteration of frames).

    LOOP SAFETY: ``decide_route`` does sync boto3 (DynamoDB read, ecs:RunTask /
    DescribeTasks) + a sync health probe + ``time.sleep`` polling on a MISS -- all
    blocking. We run it via ``asyncio.to_thread`` so the always-on broker's event
    loop is never stalled while a task is provisioned (the per-(user,session)
    in-process lock in ``decide_route`` is a ``threading.Lock`` precisely because
    it runs in the thread pool). The proxy itself is fully async.
    """
    from .proxy import (  # local import: optional dep at test time
        client_provision_keepalive,
        open_upstream,
        proxy_frames,
    )

    request_uri = _connection_path(client_ws)
    subprotocols = _connection_subprotocols(client_ws)

    # COLD-PROVISION KEEPALIVE: a route MISS provisions a COLD Fargate agent
    # (~40-48s) during which the broker would otherwise send the client ZERO data
    # frames -- so the web client's 10s DATA-frame pong-deadline fires (~35s) and
    # it force-reconnects mid-provision, re-entering the wait (the broker-only
    # reconnect churn). Emit a heartbeat DATA frame on the client leg every ~8s
    # for the WHOLE resolve+dial window, then cancel it the instant the proxy
    # takes over (the agent's own 12s heartbeat is the sole keepalive thereafter).
    # See proxy.HEARTBEAT_INTERVAL_SECONDS for the full rationale.
    keepalive_task = asyncio.create_task(client_provision_keepalive(client_ws))

    async def _stop_keepalive() -> None:
        keepalive_task.cancel()
        try:
            await keepalive_task
        except (asyncio.CancelledError, Exception):  # noqa: BLE001
            pass

    try:
        route = await asyncio.to_thread(
            decide_route,
            ddb_resource,
            ecs_client,
            cfg,
            request_uri=request_uri,
            subprotocols=subprotocols,
            health_probe=health_probe,
        )
        if route is None:
            # Reject the connect. 4401 (app-defined) signals "unauthorized/
            # unroutable" so ws.ts can distinguish it from a transient drop; it
            # still triggers the client's reconnect/backoff.
            await client_ws.close(code=4401, reason="unauthorized or unroutable")
            return

        # Connect to the per-session agent task. On a failed upstream dial, close
        # the client so it retries. The keepalive stays armed through the dial.
        try:
            upstream = await open_upstream(route.private_ip, route.port)
        except Exception as exc:  # noqa: BLE001 - task unreachable -> client retries
            logger.warning(
                "upstream connect to %s:%d failed (%s); closing client for retry",
                route.private_ip, route.port, type(exc).__name__,
            )
            await client_ws.close(code=1013, reason="agent task not ready")
            return
    finally:
        # Hand off liveness to the proxied legs (the agent's 12s heartbeat now
        # flows through proxy_frames) -- stop the broker's provisioning heartbeat
        # on EVERY path (route None, dial failure, or success) so it never races
        # the relay or double-sends.
        await _stop_keepalive()

    # Pump frames both ways until either side closes. The 12s server-push
    # heartbeat keeps the connection never-idle (open_upstream sets
    # ping_interval=None / no read deadline -> no broker-side idle timeout).
    await proxy_frames(client_ws, upstream)


def healthz() -> dict:
    """Liveness for the ALB target-group health check (NOT a per-session probe)."""
    return {"ok": True, "service": "grace2-session-broker"}
