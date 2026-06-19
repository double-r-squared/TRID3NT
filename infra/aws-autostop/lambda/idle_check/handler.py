"""Idle-check Lambda for the GRACE-2 always-on agent EC2 box.

Runs on an EventBridge schedule (default every 5 minutes). Polls the agent's
``GET /api/health`` endpoint and STOPS the agent EC2 instance ONLY when EVERY
one of these guards passes -- the auto-stop logic is bulletproof by construction
(any failed guard => do nothing, leave the box up):

  G1. The instance is in the ``running`` state (never act on a box that is
      already stopped / stopping / pending).
  G2. The health probe SUCCEEDS and reports the box is NOT busy:
      ``busy == false``. STAGE 3 (sleep/wake): a merely-open IDLE viewer
      connection NO LONGER counts as busy -- ``active_connections`` is logged
      for observability but does NOT gate the stop decision, so a user just
      LOOKING at a painted case lets the box auto-stop (then sees the cold case
      + a Wake button on return). A running turn/solve still pins the box: the
      agent's ``busy`` flag already ORs detached in-flight turns + in-flight
      solver dispatches (both of which SURVIVE a socket drop), so a long turn
      whose socket dropped keeps ``busy == true`` here even at zero connections.
      A probe failure, a malformed body, OR ``busy == true`` all count as
      "busy" -> the idle streak RESETS to zero (fail-safe: a box we cannot
      confirm idle is treated as busy).
  G3. No AWS Batch solve is in flight on the solver queue(s) -- heavy compute
      (SFINCS / MODFLOW) runs on Batch and the agent stages/polls it; stopping
      the agent mid-solve would orphan the run. Any SUBMITTED..RUNNING job on a
      configured queue counts as busy and RESETS the streak.
  G4. The box has been confirmed idle for ``IDLE_THRESHOLD_CHECKS`` CONSECUTIVE
      polls (configurable). The streak counter lives in a DynamoDB single-item
      store so it survives Lambda cold starts and concurrent-safe conditional
      writes; it RESETS to zero on any busy signal and only triggers a stop when
      it REACHES the threshold.

Idempotency:
  - StopInstances is a no-op on an already-stopped instance, but we gate on G1
    so we never even call it then.
  - The streak counter is stored once per instance id; a second invocation in
    the same minute (EventBridge at-least-once) re-reads + re-writes the same
    item -- the worst case is the streak advancing by the number of duplicate
    invocations, which only makes auto-stop SLOWER, never wrongly faster.
  - After issuing a stop we RESET the streak to zero so a wake (which transitions
    the box back to running) starts a fresh idle countdown.

This module has NO third-party dependencies beyond boto3 + urllib (both in the
Lambda Python runtime). It is unit-tested in ``tests/test_idle_check.py`` with
boto3 / urllib fully mocked -- no live AWS or network calls.
"""

from __future__ import annotations

import json
import logging
import os
import urllib.error
import urllib.request

import boto3

logger = logging.getLogger()
logger.setLevel(logging.INFO)

# --------------------------------------------------------------------------- #
# Configuration (all from environment -- set by the tofu root)
# --------------------------------------------------------------------------- #

REGION = os.environ.get("AWS_REGION", "us-west-2")
INSTANCE_ID = os.environ["AGENT_INSTANCE_ID"]
HEALTH_URL = os.environ["HEALTH_URL"]
STATE_TABLE = os.environ["STATE_TABLE"]
#: Consecutive idle polls required before a stop. With a 5-minute schedule, 3
#: checks ~= 15 minutes of confirmed idle before the box is stopped.
IDLE_THRESHOLD_CHECKS = int(os.environ.get("IDLE_THRESHOLD_CHECKS", "3"))
#: Comma-separated Batch job-queue names to check for in-flight solves. Empty
#: disables the Batch guard (only safe if Batch is not used). Default targets
#: the SFINCS/MODFLOW solver queue created by infra/aws-batch.
BATCH_QUEUES = [
    q.strip() for q in os.environ.get("BATCH_QUEUES", "grace2-solvers").split(",") if q.strip()
]
#: HTTP timeout (seconds) for the health probe. Short -- a slow/hung agent must
#: not stall the Lambda; a timeout counts as "busy" (fail-safe) and resets.
HEALTH_TIMEOUT_S = float(os.environ.get("HEALTH_TIMEOUT_S", "5"))
#: Set DRY_RUN=true to log the stop decision WITHOUT calling StopInstances --
#: lets the orchestrator validate behaviour against the live box before arming.
DRY_RUN = os.environ.get("DRY_RUN", "false").lower() in ("1", "true", "yes")

#: AWS Batch job statuses that count as "in flight" -- anything not yet
#: terminal (SUCCEEDED / FAILED).
_BATCH_ACTIVE_STATUSES = (
    "SUBMITTED",
    "PENDING",
    "RUNNABLE",
    "STARTING",
    "RUNNING",
)

_ec2 = boto3.client("ec2", region_name=REGION)
_ddb = boto3.client("dynamodb", region_name=REGION)
_batch = boto3.client("batch", region_name=REGION)


# --------------------------------------------------------------------------- #
# Guards
# --------------------------------------------------------------------------- #


def _instance_state() -> str:
    """Return the current EC2 instance state name (e.g. ``running``).

    On any API error returns ``"unknown"`` -- the caller treats a non-``running``
    state as "do not stop", so an error is fail-safe (no action).
    """
    try:
        resp = _ec2.describe_instances(InstanceIds=[INSTANCE_ID])
        reservations = resp.get("Reservations", [])
        for res in reservations:
            for inst in res.get("Instances", []):
                if inst.get("InstanceId") == INSTANCE_ID:
                    return inst.get("State", {}).get("Name", "unknown")
    except Exception:  # noqa: BLE001 -- never raise from a guard
        logger.exception("describe_instances failed; treating state as unknown")
    return "unknown"


def _probe_health() -> dict:
    """Poll ``GET /api/health`` and return a normalised liveness dict.

    Returns ``{"reachable": bool, "busy": bool, "active_connections": int}``.
    A failed/timed-out/malformed probe yields ``reachable=False`` with
    ``busy=True`` (fail-safe -- a box we cannot read is treated as busy so it is
    never stopped on a transient blip).
    """
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


def _batch_solve_in_flight() -> bool:
    """True if ANY configured Batch queue has a non-terminal job.

    On any Batch API error returns True (fail-safe busy) so a transient Batch
    outage can never let the box be stopped while a solve might be running.
    """
    if not BATCH_QUEUES:
        return False
    try:
        for queue in BATCH_QUEUES:
            for status in _BATCH_ACTIVE_STATUSES:
                resp = _batch.list_jobs(jobQueue=queue, jobStatus=status, maxResults=1)
                if resp.get("jobSummaryList"):
                    logger.info("Batch job in flight on %s status=%s", queue, status)
                    return True
        return False
    except Exception:  # noqa: BLE001
        logger.exception("list_jobs failed; treating Batch as in-flight (busy)")
        return True


# --------------------------------------------------------------------------- #
# Idle-streak store (DynamoDB single item per instance)
# --------------------------------------------------------------------------- #


def _read_streak() -> int:
    """Read the current consecutive-idle streak from DynamoDB (0 if absent)."""
    try:
        resp = _ddb.get_item(
            TableName=STATE_TABLE,
            Key={"instance_id": {"S": INSTANCE_ID}},
            ConsistentRead=True,
        )
        item = resp.get("Item")
        if item and "idle_streak" in item:
            return int(item["idle_streak"]["N"])
    except Exception:  # noqa: BLE001
        logger.exception("read streak failed; treating as 0")
    return 0


def _write_streak(value: int) -> None:
    """Persist the consecutive-idle streak. Best-effort (logs on failure)."""
    try:
        _ddb.put_item(
            TableName=STATE_TABLE,
            Item={
                "instance_id": {"S": INSTANCE_ID},
                "idle_streak": {"N": str(max(0, value))},
            },
        )
    except Exception:  # noqa: BLE001
        logger.exception("write streak failed (streak not persisted this tick)")


# --------------------------------------------------------------------------- #
# Stop action
# --------------------------------------------------------------------------- #


def _stop_instance() -> None:
    if DRY_RUN:
        logger.info("DRY_RUN: would StopInstances(%s) -- skipping", INSTANCE_ID)
        return
    try:
        _ec2.stop_instances(InstanceIds=[INSTANCE_ID])
        logger.info("StopInstances issued for %s (idle threshold reached)", INSTANCE_ID)
    except Exception:  # noqa: BLE001
        logger.exception("StopInstances failed for %s", INSTANCE_ID)


# --------------------------------------------------------------------------- #
# Handler
# --------------------------------------------------------------------------- #


def handler(event, context):  # noqa: ANN001, ARG001
    """EventBridge-scheduled idle check. Returns a JSON-serialisable decision."""
    state = _instance_state()
    if state != "running":
        # G1 failed. Reset the streak so a freshly-woken box starts its idle
        # countdown from scratch, and take no action.
        _write_streak(0)
        decision = {"action": "noop", "reason": f"instance_state={state}", "idle_streak": 0}
        logger.info("idle-check: %s", decision)
        return decision

    health = _probe_health()
    batch_busy = _batch_solve_in_flight()
    # STAGE 3 (sleep/wake): the open-connection term is INTENTIONALLY dropped --
    # an idle-but-open viewer no longer keeps the box up. ``health["busy"]``
    # already reflects any in-flight turn/solve (which survive a socket drop on
    # the agent), and ``batch_busy`` covers heavy Batch compute; either keeps
    # the box up. ``active_connections`` stays REPORTED in the decision below for
    # observability but no longer gates the stop.
    busy = health["busy"] or batch_busy

    if busy:
        # G2/G3 failed -> reset the streak. Bulletproof: ANY busy signal (the
        # agent busy flag = detached in-flight turn or in-flight solve, an
        # in-flight Batch solve, or an unreadable health probe) zeroes the
        # countdown so the next stop is a full threshold away. An idle open tab
        # alone is NOT a busy signal (Stage 3).
        _write_streak(0)
        decision = {
            "action": "noop",
            "reason": "busy",
            "reachable": health["reachable"],
            "active_connections": health["active_connections"],
            "health_busy": health["busy"],
            "batch_in_flight": batch_busy,
            "idle_streak": 0,
        }
        logger.info("idle-check: %s", decision)
        return decision

    # Confirmed idle this tick -> advance the streak.
    streak = _read_streak() + 1
    if streak >= IDLE_THRESHOLD_CHECKS:
        # G4 reached -> stop, then reset so a wake starts a fresh countdown.
        _stop_instance()
        _write_streak(0)
        decision = {
            "action": "stop" if not DRY_RUN else "stop_dryrun",
            "reason": "idle_threshold_reached",
            # Reported for observability (STAGE 3): an idle viewer may have a tab
            # open here; the open connection no longer gates the stop.
            "active_connections": health["active_connections"],
            "idle_streak": streak,
            "threshold": IDLE_THRESHOLD_CHECKS,
        }
        logger.info("idle-check: %s", decision)
        return decision

    _write_streak(streak)
    decision = {
        "action": "noop",
        "reason": "idle_below_threshold",
        # Reported for observability (STAGE 3); does not gate the stop decision.
        "active_connections": health["active_connections"],
        "idle_streak": streak,
        "threshold": IDLE_THRESHOLD_CHECKS,
    }
    logger.info("idle-check: %s", decision)
    return decision
