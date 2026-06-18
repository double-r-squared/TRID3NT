"""Wake Lambda for the GRACE-2 always-on agent EC2 box.

Fronted by an API Gateway HTTP API endpoint (``POST/GET /wake``). The web client
calls it when the WebSocket is down (wake-on-load / reconnect-retry, plus the
explicit "Wake up agent" rectangle) to bring the auto-stopped box back.

Behaviour:
  - If the instance is ``stopped`` -> call StartInstances, return
    ``{"state":"starting","started":true}``.
  - If the instance is already ``running`` -> no-op, return
    ``{"state":"running","started":false}`` (the WS will connect on its own).
  - Any transitional state (``pending`` / ``stopping`` / ``shutting-down``) ->
    no StartInstances call (it would error or be wasted); return the live state
    so the client keeps polling/retrying the WS.
  - StartInstances is idempotent-friendly: calling it on a ``stopping`` instance
    is avoided here; calling it on a ``stopped`` instance is the normal path.

The endpoint is intentionally UNAUTHENTICATED and CORS-open: the wake action is
low-risk (it can only START one specific, hard-coded instance -- never stop,
terminate, or touch any other resource) and the web app must be able to call it
from any origin before a session exists. Abuse ceiling = the instance is started
(then the idle-check Lambda stops it again); there is no data exposure.

No third-party deps beyond boto3 (in the Lambda runtime). Unit-tested in
``tests/test_wake.py`` with boto3 mocked.
"""

from __future__ import annotations

import json
import logging
import os

import boto3

logger = logging.getLogger()
logger.setLevel(logging.INFO)

REGION = os.environ.get("AWS_REGION", "us-west-2")
INSTANCE_ID = os.environ["AGENT_INSTANCE_ID"]

_ec2 = boto3.client("ec2", region_name=REGION)

_CORS_HEADERS = {
    "Content-Type": "application/json",
    "Access-Control-Allow-Origin": "*",
    "Access-Control-Allow-Methods": "GET, POST, OPTIONS",
    "Access-Control-Allow-Headers": "Content-Type",
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


def handler(event, context):  # noqa: ANN001, ARG001
    """API Gateway HTTP entrypoint. Wakes the agent box if it is stopped."""
    # Preflight: API Gateway can route OPTIONS here when the route is ANY.
    method = (
        (event or {}).get("requestContext", {}).get("http", {}).get("method", "")
        if isinstance(event, dict)
        else ""
    )
    if method == "OPTIONS":
        return _response(200, {"ok": True})

    state = _instance_state()

    if state == "running":
        return _response(200, {"state": "running", "started": False, "instance_id": INSTANCE_ID})

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
                500, {"state": state, "started": False, "error": str(exc), "instance_id": INSTANCE_ID}
            )

    # Transitional (pending / stopping / shutting-down) or unknown: do NOT call
    # StartInstances (it would error on a stopping box). Report the live state so
    # the client keeps retrying the WS / re-polling wake.
    return _response(200, {"state": state, "started": False, "instance_id": INSTANCE_ID})
