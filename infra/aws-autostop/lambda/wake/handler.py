"""Wake/Sleep Lambda for the GRACE-2 always-on agent EC2 box.

Fronted by an API Gateway HTTP API endpoint (``ANY /wake``). The web client
calls it when the WebSocket is down (asleep-detection + the explicit "Wake up
agent" rectangle) to inspect or bring the auto-stopped box back, and ALSO calls
it from the explicit "sleep" control to STOP the box on demand (the user-driven
counterpart to the idle-check auto-stop).

HTTP method drives the side effect (NATE Stage-2 contract -- detection must
NEVER mutate the box; only an explicit user action wakes/stops it):
  - ``GET`` (or ``HEAD`` / any non-POST method) -> REPORT-ONLY. Describe the
    instance and return its live state with ``started:false``. NEVER calls
    StartInstances/StopInstances, even when the box is ``stopped``/``running``.
    This is the asleep-probe the web GETs on WS connect-fail to decide whether
    to show the Wake UI.
  - ``POST`` -> MUTATE, selected by the JSON body's ``action`` field:
      * absent ``action`` or ``action == "wake"`` -> WAKE (back-compat). If the
        instance is ``stopped`` -> call StartInstances and return
        ``{"state":"starting","started":true}``. If already ``running`` -> no-op
        ``{"state":"running","started":false}``. Any transitional state
        (``pending`` / ``stopping`` / ``shutting-down``) -> no StartInstances
        call (it would error or be wasted); return the live state. UNAUTHENTICATED
        and CORS-open -- the wake action is low-risk (can only START one specific,
        hard-coded instance) and the web must call it before a session exists.
      * ``action == "stop"`` -> SLEEP. REQUIRES a valid Cognito ID token
        (Authorization: Bearer ...); anonymous/invalid -> 401. BEFORE stopping,
        polls the agent's ``GET /api/health`` (port 8766) exactly as the
        idle-check Lambda does: if the box is ``busy`` (a running turn/solve, or
        an unreadable/timed-out probe -- fail-safe) -> 409, NO StopInstances. On
        a not-busy ``running`` box -> StopInstances and return
        ``{"state":"stopping","stopped":true}``. On a non-``running`` box the
        stop is a no-op (report-only).
  - ``OPTIONS`` -> CORS preflight, no instance describe.

StartInstances is reachable ONLY from a POST wake on a ``stopped`` box.
StopInstances is reachable ONLY from a POST ``action == "stop"`` by an
AUTHENTICATED caller on a not-busy ``running`` box -- the user-tap sleep path.

The wake (StartInstances) path stays UNAUTHENTICATED + CORS-open by design (see
above). The stop (StopInstances) path is gated behind a valid Cognito token AND
the not-busy health guard, so a stray/anonymous call can never put the box to
sleep mid-turn.

Cognito verification is ported from
``services/agent/src/grace2_agent/auth_handshake.py`` (cognito_verify + JWKS
helpers), kept byte-compatible with the copy in the view-signer Lambda. The
agent module is NOT importable from the Lambda (different deploy unit), so the
logic is duplicated here deliberately; keep them in sync. The health probe is
ported from ``lambda/idle_check/handler.py`` (``_probe_health``) so the stop
guard uses the SAME busy signal the auto-stop uses.

Deps beyond the Lambda runtime's boto3 + urllib: PyJWT[crypto] (RS256 verify) +
requests (JWKS fetch). They are pip-installed into this directory at package time
by the OpenTofu ``null_resource`` + ``archive_file`` in main.tf (mirrors the
view-signer packaging). Unit-tested in ``tests/test_wake.py`` with boto3 /
urllib mocked.
"""

from __future__ import annotations

import json
import logging
import os
import threading
import urllib.error
import urllib.request
from typing import Any

import boto3

logger = logging.getLogger()
logger.setLevel(logging.INFO)

REGION = os.environ.get("AWS_REGION", "us-west-2")
INSTANCE_ID = os.environ["AGENT_INSTANCE_ID"]
#: Agent /api/health URL (catalog HTTP listener, port 8766). Same value the
#: idle-check Lambda polls -- the stop guard reuses the auto-stop busy signal.
HEALTH_URL = os.environ.get("HEALTH_URL", "")
#: HTTP timeout (seconds) for the health probe. Short -- a slow/hung agent must
#: not stall the Lambda; a timeout counts as "busy" (fail-safe) so a stop is
#: refused rather than racing an in-flight turn.
HEALTH_TIMEOUT_S = float(os.environ.get("HEALTH_TIMEOUT_S", "5"))

# Cognito -- mirrors auth_handshake.py / view_sign env names. UNSET pool =>
# verifier returns None for every token. NOTE: with no pool configured the stop
# action is UNAUTHENTICATED-blocked (cognito_verify returns None -> 401), so the
# stop control is inert until a pool is wired -- the wake path is unaffected.
COGNITO_POOL_ENV = "GRACE2_COGNITO_USER_POOL_ID"
COGNITO_CLIENT_ENV = "GRACE2_COGNITO_CLIENT_ID"

#: Clock-skew leeway (seconds) for exp validation. Matches auth_handshake.
_JWT_LEEWAY_S = 60
#: HTTPS timeout (seconds) for the public JWKS fetch.
_JWKS_FETCH_TIMEOUT_S = 5.0

_ec2 = boto3.client("ec2", region_name=REGION)

_CORS_HEADERS = {
    "Content-Type": "application/json",
    "Access-Control-Allow-Origin": "*",
    "Access-Control-Allow-Methods": "GET, POST, OPTIONS",
    # `authorization` so the signed-in browser can send the Cognito ID token on
    # the stop action (the wake action never needs it -- harmless there).
    "Access-Control-Allow-Headers": "Content-Type, Authorization",
    # The wake state can change second-to-second; never let a CDN/browser cache it.
    "Cache-Control": "no-store",
}


def _response(status: int, body: dict) -> dict:
    """Build an API Gateway (payload format 2.0) proxy response."""
    return {
        "statusCode": status,
        "headers": _CORS_HEADERS,
        "body": json.dumps(body, separators=(",", ":")),
    }


def _instance_state() -> str:
    """Return the EC2 instance state name, or ``"unknown"`` on API error."""
    try:
        resp = _ec2.describe_instances(InstanceIds=[INSTANCE_ID])
        for res in resp.get("Reservations", []):
            for inst in res.get("Instances", []):
                if inst.get("InstanceId") == INSTANCE_ID:
                    return inst.get("State", {}).get("Name", "unknown")
    except Exception:  # noqa: BLE001
        logger.exception("describe_instances failed")
    return "unknown"


# --------------------------------------------------------------------------- #
# Health probe (ported from lambda/idle_check/handler.py -- keep in sync).
# The stop guard reuses the SAME busy signal the auto-stop uses.
# --------------------------------------------------------------------------- #


def _probe_health() -> dict:
    """Poll ``GET /api/health`` and return a normalised liveness dict.

    Returns ``{"reachable": bool, "busy": bool, "active_connections": int}``.
    A failed/timed-out/malformed probe yields ``reachable=False`` with
    ``busy=True`` (fail-safe -- a box we cannot read is treated as busy so it is
    never stopped on a transient blip). An UNSET HEALTH_URL also yields busy
    (fail-safe: never stop a box we cannot confirm idle).
    """
    if not HEALTH_URL:
        logger.warning("HEALTH_URL unset; treating box as busy (cannot confirm idle)")
        return {"reachable": False, "busy": True, "active_connections": -1}
    try:
        req = urllib.request.Request(HEALTH_URL, headers={"User-Agent": "grace2-autostop"})
        with urllib.request.urlopen(req, timeout=HEALTH_TIMEOUT_S) as resp:  # noqa: S310
            raw = resp.read().decode("utf-8")
        body = json.loads(raw)
    except (urllib.error.URLError, TimeoutError, ValueError, OSError) as exc:
        logger.warning("health probe failed (%s); treating box as busy", exc)
        return {"reachable": False, "busy": True, "active_connections": -1}

    # The agent's /api/health returns {"ok":bool,"active_connections":int,"busy":bool}.
    # Any missing/odd field -> fail-safe busy. A box that responds but omits the
    # autostop fields (older build) is treated as busy so we never stop it blind.
    active = body.get("active_connections")
    busy = body.get("busy")
    if not isinstance(active, int) or not isinstance(busy, bool):
        logger.warning("health body missing autostop fields: %r; treating as busy", body)
        return {"reachable": True, "busy": True, "active_connections": -1}
    return {"reachable": True, "busy": busy, "active_connections": active}


# --------------------------------------------------------------------------- #
# Cognito verification (ported from auth_handshake.py / view_sign -- keep in
# sync). Required for the stop action; anonymous/invalid -> None -> 401.
# --------------------------------------------------------------------------- #

_jwks_cache: dict[str, dict[str, dict[str, Any]]] = {}
_jwks_lock = threading.Lock()


def _cognito_region() -> str:
    return (
        os.environ.get("GRACE2_AWS_REGION")
        or os.environ.get("AWS_REGION")
        or "us-west-2"
    )


def _cognito_issuer(region: str, pool_id: str) -> str:
    return f"https://cognito-idp.{region}.amazonaws.com/{pool_id}"


def _fetch_jwks(issuer: str) -> dict[str, dict[str, Any]]:
    import requests  # packaged dep

    url = f"{issuer}/.well-known/jwks.json"
    resp = requests.get(url, timeout=_JWKS_FETCH_TIMEOUT_S)
    resp.raise_for_status()
    keys = resp.json().get("keys", [])
    return {k["kid"]: k for k in keys if "kid" in k}


def _get_jwk(issuer: str, kid: str, *, allow_refetch: bool = True) -> dict[str, Any] | None:
    with _jwks_lock:
        cached = _jwks_cache.get(issuer)
    if cached is not None and kid in cached:
        return cached[kid]
    if not allow_refetch:
        return cached.get(kid) if cached else None
    try:
        fresh = _fetch_jwks(issuer)
    except Exception as exc:  # noqa: BLE001 -- network/parse failure is normal
        logger.info("JWKS fetch failed for %s: %s", issuer, type(exc).__name__)
        return cached.get(kid) if cached else None
    with _jwks_lock:
        _jwks_cache[issuer] = fresh
    return fresh.get(kid)


def cognito_verify(token: str) -> dict[str, Any] | None:
    """Verify a Cognito ID token. Returns claims dict on success, None on any
    failure (invalid/expired/wrong-aud, or no pool configured => anonymous)."""
    pool_id = os.environ.get(COGNITO_POOL_ENV, "").strip()
    if not pool_id:
        # Master gate: no pool configured -> anonymous fallback (None).
        return None
    client_id = os.environ.get(COGNITO_CLIENT_ENV, "").strip()
    region = _cognito_region()
    issuer = _cognito_issuer(region, pool_id)

    try:
        import jwt  # PyJWT[crypto] -- packaged dep
        from jwt.algorithms import RSAAlgorithm

        header = jwt.get_unverified_header(token)
        kid = header.get("kid")
        if not kid:
            logger.info("Cognito verify: token header missing 'kid'")
            return None
        jwk = _get_jwk(issuer, kid)
        if jwk is None:
            logger.info("Cognito verify: no JWK for kid=%s", kid)
            return None

        public_key = RSAAlgorithm.from_jwk(jwk)

        decode_kwargs: dict[str, Any] = dict(
            algorithms=["RS256"],
            issuer=issuer,
            leeway=_JWT_LEEWAY_S,
            options={
                "require": ["exp", "iss", "sub"],
                "verify_aud": False,  # validated explicitly below
            },
        )
        claims = jwt.decode(token, public_key, **decode_kwargs)
    except Exception as exc:  # noqa: BLE001 -- verification failure is normal
        logger.info("Cognito verify failed: %s", type(exc).__name__)
        return None

    if claims.get("token_use") != "id":
        logger.info("Cognito verify: token_use=%r (expected 'id')", claims.get("token_use"))
        return None

    if not client_id or claims.get("aud") != client_id:
        logger.info("Cognito verify: aud mismatch")
        return None

    sub = claims.get("sub")
    if not sub:
        logger.info("Cognito verify: claims missing 'sub'")
        return None

    return {
        "uid": sub,
        "email": claims.get("email"),
        "name": claims.get("name") or claims.get("cognito:username"),
        "tier": claims.get("custom:tier", "free"),
    }


# --------------------------------------------------------------------------- #
# Request helpers.
# --------------------------------------------------------------------------- #


def _extract_bearer(event: dict) -> str | None:
    """Pull the bearer token from the Authorization header (case-insensitive).

    API Gateway payload 2.0 lower-cases header keys, but be defensive.
    """
    headers = event.get("headers") or {}
    raw = None
    for k, v in headers.items():
        if k.lower() == "authorization":
            raw = v
            break
    if not raw:
        return None
    parts = raw.split(None, 1)
    if len(parts) == 2 and parts[0].lower() == "bearer":
        return parts[1].strip()
    # Tolerate a bare token (no scheme).
    return raw.strip() or None


def _request_action(event: dict) -> str:
    """Return the requested POST action: ``"stop"`` or ``"wake"`` (default).

    Reads the JSON body's ``action`` field. An absent/empty/unparseable body or a
    missing ``action`` defaults to ``"wake"`` (back-compat with the original wake
    contract). Anything other than ``"stop"`` is treated as ``"wake"``.
    """
    raw = event.get("body")
    if not raw:
        return "wake"
    try:
        parsed = json.loads(raw)
    except (ValueError, TypeError):
        return "wake"
    if not isinstance(parsed, dict):
        return "wake"
    action = parsed.get("action")
    if isinstance(action, str) and action.strip().lower() == "stop":
        return "stop"
    return "wake"


# --------------------------------------------------------------------------- #
# Handler.
# --------------------------------------------------------------------------- #


def handler(event, context):  # noqa: ANN001, ARG001
    """API Gateway HTTP entrypoint. GET reports state; POST wakes (default) or
    stops (action=="stop", Cognito-gated + not-busy)."""
    if not isinstance(event, dict):
        event = {}

    # API Gateway HTTP API (payload format 2.0): the verb lives at
    # event.requestContext.http.method. Default to "" (treated as report-only).
    method = event.get("requestContext", {}).get("http", {}).get("method", "")

    # Preflight: API Gateway can route OPTIONS here when the route is ANY.
    if method == "OPTIONS":
        return _response(200, {"ok": True})

    if method == "POST":
        action = _request_action(event)

        if action == "stop":
            # SLEEP path: AUTHENTICATED + not-busy only.
            token = _extract_bearer(event)
            claims = cognito_verify(token) if token else None
            if claims is None:
                # Anonymous / invalid / no-pool -> 401. Never stop without a
                # verified token (a stray call must not sleep the box).
                return _response(
                    401,
                    {
                        "error": "stop requires a valid Cognito token",
                        "instance_id": INSTANCE_ID,
                    },
                )

            state = _instance_state()
            if state != "running":
                # Nothing to stop: report-only (no StopInstances on a box that is
                # already stopped / stopping / pending).
                return _response(
                    200, {"state": state, "stopped": False, "instance_id": INSTANCE_ID}
                )

            # Busy guard: poll /api/health the SAME way idle-check does. A busy
            # box (running turn/solve) or an unreadable probe (fail-safe busy) ->
            # 409, NO StopInstances.
            health = _probe_health()
            if health["busy"]:
                return _response(
                    409,
                    {
                        "error": "agent is busy; not stopping",
                        "state": state,
                        "stopped": False,
                        "reachable": health["reachable"],
                        "active_connections": health["active_connections"],
                        "instance_id": INSTANCE_ID,
                    },
                )

            try:
                _ec2.stop_instances(InstanceIds=[INSTANCE_ID])
                logger.info("StopInstances issued for %s (user sleep request)", INSTANCE_ID)
                return _response(
                    200,
                    {"state": "stopping", "stopped": True, "instance_id": INSTANCE_ID},
                )
            except Exception as exc:  # noqa: BLE001
                logger.exception("StopInstances failed for %s", INSTANCE_ID)
                return _response(
                    500,
                    {
                        "state": state,
                        "stopped": False,
                        "error": str(exc),
                        "instance_id": INSTANCE_ID,
                    },
                )

        # WAKE path (action absent or "wake"): UNAUTHENTICATED, back-compat.
        state = _instance_state()
        if state == "stopped":
            try:
                _ec2.start_instances(InstanceIds=[INSTANCE_ID])
                logger.info("StartInstances issued for %s (wake request)", INSTANCE_ID)
                return _response(
                    202, {"state": "starting", "started": True, "instance_id": INSTANCE_ID}
                )
            except Exception as exc:  # noqa: BLE001
                logger.exception("StartInstances failed for %s", INSTANCE_ID)
                return _response(
                    500,
                    {"state": state, "started": False, "error": str(exc), "instance_id": INSTANCE_ID},
                )
        # POST wake on a running/transitional box: no-op, report the live state.
        return _response(200, {"state": state, "started": False, "instance_id": INSTANCE_ID})

    # Report-only for everything else:
    #   - GET / HEAD / unknown method (asleep-detection probe): NEVER start/stop,
    #     even when ``stopped``/``running`` -- just report the live state so the
    #     web can decide to show the Wake UI.
    # ``started`` is always False on this path.
    state = _instance_state()
    return _response(200, {"state": state, "started": False, "instance_id": INSTANCE_ID})
