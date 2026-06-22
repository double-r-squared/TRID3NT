"""Per-task idle reaper for the GRACE-2 Fargate-per-session agent tasks.

This GENERALIZES the single-box autostop idle_check
(infra/aws-autostop/lambda/idle_check/handler.py) from one EC2 box to N
per-session Fargate tasks: ``ec2:StopInstances`` becomes ``ecs:StopTask``, and
the single idle-streak item becomes one item PER session_id. The busy logic, the
G3 Batch guard, the Stage-3 "idle-open tab is NOT busy" rule, and the streak/
threshold machinery are PORTED UNCHANGED in shape -- only the target verb and the
per-task fan-out differ.

Runs on an EventBridge schedule (default every 5 minutes). For EACH live route in
``grace2_session_routes`` it polls that task's ``GET /api/health`` and StopTasks
(+ deletes the route row) ONLY when every guard passes -- bulletproof by
construction (any failed guard => leave the task up):

  G1. The task is RUNNING (never act on a task already stopping/stopped/pending).
  G2. The health probe SUCCEEDS and reports the task NOT busy (``busy == false``).
      STAGE 3: a merely-open idle viewer connection does NOT count as busy
      (``active_connections`` is logged only). A running turn/solve pins the task
      because the agent's ``busy`` ORs detached in-flight turns + in-flight solver
      dispatches (both survive a socket drop). A probe failure / malformed body /
      ``busy == true`` all RESET that session's idle streak (fail-safe busy).
  G3. No AWS Batch solve is in flight on the configured queue(s). Heavy compute
      (SFINCS/MODFLOW/SWMM) runs on Batch and the per-session task stages/polls
      it; stopping the task mid-solve would orphan the run. ANY SUBMITTED..RUNNING
      job keeps the task up. (Conservative: the Batch guard is GLOBAL, not yet
      attributed to a session_id -- so a single in-flight solve keeps ALL idle
      tasks up. This is the SAFE direction. TODO(canary): tag Batch jobs with the
      owning session_id and gate per-session so an unrelated session's solve does
      not pin every task.)
  G4. The session's task has been idle for ``IDLE_THRESHOLD_CHECKS`` CONSECUTIVE
      polls. The per-session streak counter lives in the routes table item (or a
      sibling state table) so it survives Lambda cold starts; it RESETS on any
      busy signal and only triggers a StopTask when it REACHES the threshold.

This module has NO third-party deps beyond boto3 + urllib (both in the Lambda
runtime). It is unit-tested in tests/test_task_reaper.py with boto3 / urllib
fully mocked -- no live AWS or network calls.

NETWORKING NOTE (the one real difference from the EC2 reaper): the per-session
agent task has NO public IP; its ``/api/health`` is reachable only on its private
ENI IP inside the VPC. So the reaper Lambda MUST run IN THE VPC (the task subnets
+ a SG allowed to reach the agent SG on 8766) to probe health. reaper.tf wires the
Lambda's vpc_config accordingly. (Alternatively the task could SELF-report idle
to the routes table and the reaper would skip the probe -- a documented LATER
option to drop the VPC requirement.)
"""

from __future__ import annotations

import json
import logging
import os
import time
import urllib.error
import urllib.request

import boto3

logger = logging.getLogger()
logger.setLevel(logging.INFO)

# --------------------------------------------------------------------------- #
# Configuration (all from environment -- set by the tofu root)
# --------------------------------------------------------------------------- #

REGION = os.environ.get("AWS_REGION", "us-west-2")
CLUSTER = os.environ["ECS_CLUSTER"]
ROUTES_TABLE = os.environ["ROUTES_TABLE"]
#: Port the per-session agent task serves /api/health on (matches the task def).
HEALTH_PORT = int(os.environ.get("AGENT_HEALTH_PORT", "8766"))
#: Consecutive idle polls required before a StopTask. With a 5-minute schedule, 3
#: checks ~= 15 minutes of confirmed idle (matches the single-box autostop).
IDLE_THRESHOLD_CHECKS = int(os.environ.get("IDLE_THRESHOLD_CHECKS", "3"))
#: Comma-separated Batch job-queue names to check for in-flight solves (G3).
BATCH_QUEUES = [
    q.strip() for q in os.environ.get("BATCH_QUEUES", "grace2-solvers").split(",") if q.strip()
]
#: HTTP timeout (seconds) for each per-task health probe.
HEALTH_TIMEOUT_S = float(os.environ.get("HEALTH_TIMEOUT_S", "5"))
#: TTL refresh (seconds) stamped onto a route row each live tick so an active
#: session's row never expires out from under it.
ROUTE_TTL_SECONDS = int(os.environ.get("ROUTE_TTL_SECONDS", "86400"))
#: Set DRY_RUN=true to log the StopTask decision WITHOUT calling StopTask.
DRY_RUN = os.environ.get("DRY_RUN", "false").lower() in ("1", "true", "yes")

#: Batch statuses that count as "in flight" (anything not terminal).
_BATCH_ACTIVE_STATUSES = ("SUBMITTED", "PENDING", "RUNNABLE", "STARTING", "RUNNING")

_ecs = boto3.client("ecs", region_name=REGION)
_ddb = boto3.client("dynamodb", region_name=REGION)
_batch = boto3.client("batch", region_name=REGION)


# --------------------------------------------------------------------------- #
# Route enumeration (the per-session fan-out -- the new part vs the EC2 reaper)
# --------------------------------------------------------------------------- #


def _scan_routes() -> list[dict]:
    """Return all live route rows as plain dicts.

    Each row: {user_ulid, session_id, task_arn, private_ip, idle_streak,
    state, ...}. A small table (one row per ACTIVE session); a Scan is fine and
    cheaper than maintaining a GSI. On any error returns [] (no action this tick).
    """
    rows: list[dict] = []
    try:
        kwargs: dict = {"TableName": ROUTES_TABLE, "ConsistentRead": True}
        while True:
            resp = _ddb.scan(**kwargs)
            for item in resp.get("Items", []):
                rows.append(_unwrap(item))
            lek = resp.get("LastEvaluatedKey")
            if not lek:
                break
            kwargs["ExclusiveStartKey"] = lek
    except Exception:  # noqa: BLE001 -- never raise; skip this tick
        logger.exception("scan routes failed; no reap this tick")
    return rows


def _unwrap(item: dict) -> dict:
    """Shallow-unwrap a DynamoDB low-level item to {attr: python} for the fields
    the reaper reads (all S or N). Unknown types are passed through as-is."""
    out: dict = {}
    for k, v in item.items():
        if "S" in v:
            out[k] = v["S"]
        elif "N" in v:
            out[k] = int(v["N"])
        else:
            out[k] = v
    return out


# --------------------------------------------------------------------------- #
# Guards (ported from idle_check, retargeted EC2 -> ECS task)
# --------------------------------------------------------------------------- #


def _task_state(task_arn: str) -> str:
    """Return the task's lastStatus (e.g. ``RUNNING``), or ``unknown`` on error.

    A non-RUNNING task is never stopped (it is already gone/going), so an error
    is fail-safe.
    """
    try:
        resp = _ecs.describe_tasks(cluster=CLUSTER, tasks=[task_arn])
        tasks = resp.get("tasks", [])
        if tasks:
            return tasks[0].get("lastStatus", "unknown")
    except Exception:  # noqa: BLE001
        logger.exception("describe_tasks failed for %s; state unknown", task_arn)
    return "unknown"


def _probe_health(private_ip: str) -> dict:
    """Poll ``http://<private_ip>:<HEALTH_PORT>/api/health``.

    Returns {"reachable": bool, "busy": bool, "active_connections": int}. A
    failed/timed-out/malformed probe yields reachable=False, busy=True (fail-safe
    -- a task we cannot read is treated as busy so it is never stopped on a blip).
    Identical contract to the EC2 reaper's _probe_health.
    """
    url = f"http://{private_ip}:{HEALTH_PORT}/api/health"
    try:
        req = urllib.request.Request(url, headers={"User-Agent": "grace2-task-reaper"})
        with urllib.request.urlopen(req, timeout=HEALTH_TIMEOUT_S) as resp:  # noqa: S310
            body = json.loads(resp.read().decode("utf-8"))
    except (urllib.error.URLError, TimeoutError, ValueError, OSError) as exc:
        logger.warning("health probe failed for %s (%s); treating busy", url, exc)
        return {"reachable": False, "busy": True, "active_connections": -1}

    active = body.get("active_connections")
    busy = body.get("busy")
    if not isinstance(active, int) or not isinstance(busy, bool):
        logger.warning("health body missing autostop fields: %r; treating busy", body)
        return {"reachable": True, "busy": True, "active_connections": -1}
    return {"reachable": True, "busy": busy, "active_connections": active}


def _batch_solve_in_flight() -> bool:
    """True if ANY configured Batch queue has a non-terminal job (G3).

    Conservative + GLOBAL (not yet per-session): any in-flight solve keeps every
    idle task up. Fail-safe busy on any Batch API error. TODO(canary): attribute
    jobs to session_id and gate per-session.
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
# Per-session streak store (in the route row itself)
# --------------------------------------------------------------------------- #


def _write_streak(user_ulid: str, session_id: str, value: int) -> None:
    """Update a route row's idle_streak (and refresh its TTL). Best-effort."""
    try:
        _ddb.update_item(
            TableName=ROUTES_TABLE,
            Key={"user_ulid": {"S": user_ulid}, "session_id": {"S": session_id}},
            UpdateExpression="SET idle_streak = :s, expires_at = :e",
            ExpressionAttributeValues={
                ":s": {"N": str(max(0, value))},
                ":e": {"N": str(int(time.time()) + ROUTE_TTL_SECONDS)},
            },
        )
    except Exception:  # noqa: BLE001
        logger.exception("write streak failed for %s/%s", user_ulid, session_id)


def _delete_route(user_ulid: str, session_id: str) -> None:
    """Delete a route row after a StopTask so a reconnect re-provisions."""
    try:
        _ddb.delete_item(
            TableName=ROUTES_TABLE,
            Key={"user_ulid": {"S": user_ulid}, "session_id": {"S": session_id}},
        )
    except Exception:  # noqa: BLE001
        logger.exception("delete route failed for %s/%s", user_ulid, session_id)


def _stop_task(task_arn: str, reason: str) -> None:
    if DRY_RUN:
        logger.info("DRY_RUN: would StopTask(%s) reason=%s -- skipping", task_arn, reason)
        return
    try:
        _ecs.stop_task(cluster=CLUSTER, task=task_arn, reason=reason)
        logger.info("StopTask issued for %s (%s)", task_arn, reason)
    except Exception:  # noqa: BLE001
        logger.exception("StopTask failed for %s", task_arn)


# --------------------------------------------------------------------------- #
# Per-session decision (the ported idle_check core, run once per route)
# --------------------------------------------------------------------------- #


def _reap_one(route: dict, batch_busy: bool) -> dict:
    """Evaluate ONE session's route and return its decision dict.

    Mirrors the EC2 idle_check handler body, retargeted to a single task.
    ``batch_busy`` is computed ONCE per tick (global) and passed in.
    """
    user_ulid = route.get("user_ulid", "")
    session_id = route.get("session_id", "")
    task_arn = route.get("task_arn", "")
    private_ip = route.get("private_ip", "")

    # G1: task must be RUNNING.
    state = _task_state(task_arn) if task_arn else "unknown"
    if state != "RUNNING":
        # Task is gone/going -> drop the route so a reconnect re-provisions, and
        # take no StopTask.
        _delete_route(user_ulid, session_id)
        return {"session_id": session_id, "action": "route_dropped", "reason": f"task_state={state}"}

    # G2/G3: busy = health busy OR an in-flight Batch solve.
    health = _probe_health(private_ip) if private_ip else {"reachable": False, "busy": True, "active_connections": -1}
    busy = health["busy"] or batch_busy

    if busy:
        _write_streak(user_ulid, session_id, 0)
        return {
            "session_id": session_id,
            "action": "noop",
            "reason": "busy",
            "reachable": health["reachable"],
            "active_connections": health["active_connections"],
            "health_busy": health["busy"],
            "batch_in_flight": batch_busy,
            "idle_streak": 0,
        }

    # Confirmed idle -> advance this session's streak.
    streak = int(route.get("idle_streak", 0)) + 1
    if streak >= IDLE_THRESHOLD_CHECKS:
        _stop_task(task_arn, "grace2 idle threshold reached")
        _delete_route(user_ulid, session_id)
        return {
            "session_id": session_id,
            "action": "stop" if not DRY_RUN else "stop_dryrun",
            "reason": "idle_threshold_reached",
            "active_connections": health["active_connections"],
            "idle_streak": streak,
            "threshold": IDLE_THRESHOLD_CHECKS,
        }

    _write_streak(user_ulid, session_id, streak)
    return {
        "session_id": session_id,
        "action": "noop",
        "reason": "idle_below_threshold",
        "active_connections": health["active_connections"],
        "idle_streak": streak,
        "threshold": IDLE_THRESHOLD_CHECKS,
    }


# --------------------------------------------------------------------------- #
# Handler
# --------------------------------------------------------------------------- #


def handler(event, context):  # noqa: ANN001, ARG001
    """EventBridge-scheduled per-task idle reaper. Returns the per-session
    decisions (JSON-serialisable)."""
    routes = _scan_routes()
    # G3 computed ONCE per tick (global Batch guard -- the conservative direction).
    batch_busy = _batch_solve_in_flight()

    decisions = [_reap_one(r, batch_busy) for r in routes]
    summary = {
        "routes_seen": len(routes),
        "batch_in_flight": batch_busy,
        "stopped": sum(1 for d in decisions if d.get("action") in ("stop", "stop_dryrun")),
        "decisions": decisions,
    }
    logger.info("task-reaper: %s", summary)
    return summary
