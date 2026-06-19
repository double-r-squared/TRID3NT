"""Wake Lambda for the GRACE-2 always-on agent EC2 box.

Fronted by an API Gateway HTTP API endpoint (``ANY /wake``). The web client
calls it when the WebSocket is down (asleep-detection + the explicit "Wake up
agent" rectangle) to inspect or bring the auto-stopped box back.

HTTP method drives the side effect (NATE Stage-2 contract -- detection must
NEVER wake the box; only an explicit user tap wakes it):
  - ``GET`` (or ``HEAD`` / any non-POST method) -> REPORT-ONLY. Describe the
    instance and return its live state with ``started:false``. NEVER calls
    StartInstances, even when the box is ``stopped``. This is the asleep-probe
    the web GETs on WS connect-fail to decide whether to show the Wake UI.
  - ``POST`` -> WAKE. If the instance is ``stopped`` -> call StartInstances and
    return ``{"state":"starting","started":true}``. If already ``running`` ->
    no-op ``{"state":"running","started":false}``. Any transitional state
    (``pending`` / ``stopping`` / ``shutting-down``) -> no StartInstances call
    (it would error or be wasted); return the live state.
  - ``OPTIONS`` -> CORS preflight, no instance describe.

StartInstances is therefore reachable ONLY from POST on a ``stopped`` box --
the normal user-tap wake path.

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
    """API Gateway HTTP entrypoint. GET reports state; POST wakes if stopped."""
    # API Gateway HTTP API (payload format 2.0): the verb lives at
    # event.requestContext.http.method. Default to "" (treated as report-only).
    method = (
        (event or {}).get("requestContext", {}).get("http", {}).get("method", "")
        if isinstance(event, dict)
        else ""
    )

    # Preflight: API Gateway can route OPTIONS here when the route is ANY.
    if method == "OPTIONS":
        return _response(200, {"ok": True})

    state = _instance_state()

    # WAKE is the side-effecting path and is gated behind POST ONLY. A POST on a
    # ``stopped`` box is the user-tap wake. Detection (GET) must never reach here.
    if method == "POST" and state == "stopped":
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

    # Report-only for everything else:
    #   - GET / HEAD / unknown method (asleep-detection probe): NEVER start, even
    #     when ``stopped`` -- just report the live state so the web can decide to
    #     show the Wake UI.
    #   - POST on a ``running`` box: no-op (the WS will connect on its own).
    #   - POST on a transitional box (pending / stopping / shutting-down): no
    #     StartInstances call (it would error on a stopping box).
    # ``started`` is always False on this path.
    return _response(200, {"state": state, "started": False, "instance_id": INSTANCE_ID})
