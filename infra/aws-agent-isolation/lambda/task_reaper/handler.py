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
  G2. The health check SUCCEEDS and reports the task NOT busy (``busy == false``).
      PROBE mode: GET /api/health on the private ENI IP (requires VPC attachment).
      HEARTBEAT mode: read hb_last_seen/hb_busy from the route row (no VPC probe).
      BOTH mode: run probe AND heartbeat; LOG agreement/disagreement; ACT on probe
      result (safe parallel-validation mode).
      STAGE 3: a merely-open idle viewer connection does NOT count as busy
      (``active_connections`` is logged only). A running turn/solve pins the task
      because the agent's ``busy`` ORs detached in-flight turns + in-flight solver
      dispatches (both survive a socket drop). A probe failure / malformed body /
      ``busy == true`` all RESET that session's idle streak (fail-safe busy).
  G3. No AWS Batch solve is in flight.
      PROBE/BOTH mode: GLOBAL guard (any in-flight solve on any queue keeps all
      idle tasks up -- conservative / safe direction).
      HEARTBEAT mode: PER-SESSION guard using hb_inflight_batch from the route row
      (an unrelated user's solve no longer pins every idle task).
  G4. The session's task has been idle for ``IDLE_THRESHOLD_CHECKS`` CONSECUTIVE
      polls. The per-session streak counter lives in the routes table item (or a
      sibling state table) so it survives Lambda cold starts; it RESETS on any
      busy signal and only triggers a StopTask when it REACHES the threshold.

This module has NO third-party deps beyond boto3 + urllib (both in the Lambda
runtime). It is unit-tested in tests/test_task_reaper.py with boto3 / urllib
fully mocked -- no live AWS or network calls.

HEALTH MODES (REAPER_HEALTH_MODE env, default "probe"):
  probe     - today's behavior (VPC-attached Lambda, HTTP probe). Fully backward
              compatible; no change to any existing deployment.
  heartbeat - the agent writes hb_last_seen/hb_busy/hb_inflight_batch to its
              route row every ~60s (GRACE2_ROUTE_HEARTBEAT_SECONDS). The reaper
              reads ONLY DynamoDB -- no VPC attachment needed, no ECS/Batch
              interface endpoints needed. G2 becomes: hb_last_seen older than
              HEARTBEAT_STALE_SECONDS (default 180) => treat as not-responding
              (fail-safe busy); else busy = hb_busy OR hb_inflight_batch > 0.
              G3 becomes per-session (hb_inflight_batch only, no ListJobs).
  both      - run probe AND heartbeat; log agreement/disagreement per route;
              ACT on probe result (safe migration/validation mode). Use this
              to validate heartbeat correctness against probe ground-truth before
              switching to heartbeat mode and deleting the VPC configuration.

NETWORKING NOTE: in probe/both mode the reaper must run inside the VPC (the task
subnets + a SG allowed to reach the agent SG on 8766). reaper.tf wires the
Lambda's vpc_config accordingly. In heartbeat-only mode the Lambda needs NO VPC
attachment -- the operator removes the vpc_config block and the ECS + Batch
interface endpoints (see the TODO markers in reaper.tf and vpc_endpoints.tf).
PASS 2 (orphan/max-age enumeration via ECS ListTasks/DescribeTasks) works fine
from a non-VPC Lambda because it uses ECS control-plane APIs, not private-IP
probes.
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

# --------------------------------------------------------------------------- #
# Phase-1 scale-to-zero: HEALTH MODE (REAPER_HEALTH_MODE env).
#
# "probe"     (default) -- HTTP-probe :8766; requires VPC attachment + interface
#             endpoints. Fully backward compatible.
# "heartbeat" -- read hb_* fields from the route row written by the agent every
#             GRACE2_ROUTE_HEARTBEAT_SECONDS. No VPC attachment required.
# "both"      -- run both; log agreement/disagreement; act on probe result
#             (safe parallel validation before cutting over to heartbeat).
# --------------------------------------------------------------------------- #
HEALTH_MODE = os.environ.get("REAPER_HEALTH_MODE", "probe").lower().strip()
if HEALTH_MODE not in ("probe", "heartbeat", "both"):
    logger.warning(
        "REAPER_HEALTH_MODE=%r not recognised; defaulting to 'probe'", HEALTH_MODE
    )
    HEALTH_MODE = "probe"

#: A heartbeat is considered STALE (=> treat as not-responding, fail-safe busy)
#: when hb_last_seen is older than this many seconds. Should be at least
#: 2-3x the agent's GRACE2_ROUTE_HEARTBEAT_SECONDS (default 60s) to tolerate
#: a missed write without triggering a false-busy. Default 180s = 3 missed beats.
HEARTBEAT_STALE_SECONDS = int(os.environ.get("HEARTBEAT_STALE_SECONDS", "180"))

# --------------------------------------------------------------------------- #
# ORPHAN + MAX-AGE reaping (the outage fix).
#
# The route-correlated pass above only ever ACTS on tasks it can find via a live
# route row. A RUNNING task whose route row vanished (TTL expiry, a dry-run
# _delete_route, a half-written broker route, a manual delete) becomes an ORPHAN
# the route pass can never see -> it runs forever -> vCPU-quota exhaustion. The
# fix: enumerate RUNNING grace2-agent-session tasks DIRECTLY (ecs:ListTasks) and
# stop any task that is (a) not backed by a live route AND older than a small
# provision grace window (so a task mid-provision, before its route is written,
# is never killed), OR (b) older than a hard MAX AGE regardless of route (a
# session should never run that long). Both passes respect DRY_RUN.
# --------------------------------------------------------------------------- #

#: The Fargate task-def family for the per-session agent tasks (ecs.tf). Used to
#: scope ListTasks so the broker service task (same cluster) is NEVER enumerated.
TASK_FAMILY = os.environ.get("AGENT_TASK_FAMILY", "grace2-agent-session")
#: A running task with NO live route is only reaped once it is older than this
#: (seconds). Protects a task that is mid-provision -- RunTask has fired and the
#: task is RUNNING but the broker has not yet health-polled + written its route.
#: The broker provision path bounds at ~90s, so 600s is a wide safety margin.
ORPHAN_GRACE_SECONDS = int(os.environ.get("ORPHAN_GRACE_SECONDS", "600"))
#: A running task older than this (seconds) is reaped regardless of route -- the
#: ultimate backstop. A legitimate solve is Batch-side (wait_for_completion caps
#: at 1800s), so no genuine session should keep a task alive for 90 min. A
#: route-backed task that is INDIVIDUALLY busy right now (its own /api/health
#: reports busy) is spared for this tick (see _orphan_maxage_decision).
MAX_AGE_SECONDS = int(os.environ.get("MAX_AGE_SECONDS", "5400"))

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

    Conservative + GLOBAL (used in probe/both modes): any in-flight solve keeps
    every idle task up. Fail-safe busy on any Batch API error.
    In heartbeat mode, the per-session hb_inflight_batch field is used instead
    (see _heartbeat_busy) -- ListJobs is NOT called.
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


def _heartbeat_busy(route: dict) -> dict:
    """Evaluate G2+G3 for ONE session using the route row's hb_* heartbeat fields.

    Returns a dict with the same shape as ``_probe_health`` plus extra fields
    so the caller can log/compare:
      {
        "reachable": bool,   # True if hb_last_seen is fresh enough
        "busy": bool,        # hb_busy OR hb_inflight_batch > 0 (or stale)
        "active_connections": int,
        "hb_last_seen": int,  # epoch seconds (0 if absent)
        "hb_inflight_batch": int,
        "stale": bool,        # True when hb_last_seen is too old
      }

    Fail-safe: if hb_last_seen is absent OR older than HEARTBEAT_STALE_SECONDS
    the result is busy=True (treat missing/stale heartbeat as not-responding,
    the same contract as a failed HTTP probe).
    """
    now_epoch = int(time.time())
    hb_last_seen = int(route.get("hb_last_seen", 0))
    hb_busy_flag = bool(route.get("hb_busy", True))
    hb_active = int(route.get("hb_active_connections", 0))
    hb_inflight = int(route.get("hb_inflight_batch", 0))

    stale = (now_epoch - hb_last_seen) >= HEARTBEAT_STALE_SECONDS
    if stale:
        logger.warning(
            "heartbeat stale for session %s: hb_last_seen=%d age=%ds >= threshold=%d; busy",
            route.get("session_id", "?"),
            hb_last_seen,
            now_epoch - hb_last_seen,
            HEARTBEAT_STALE_SECONDS,
        )
    busy = stale or hb_busy_flag or hb_inflight > 0
    return {
        "reachable": not stale,
        "busy": busy,
        "active_connections": hb_active,
        "hb_last_seen": hb_last_seen,
        "hb_inflight_batch": hb_inflight,
        "stale": stale,
    }


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

    Behaviour varies by HEALTH_MODE:
      probe     - original behaviour (HTTP probe + global Batch guard).
      heartbeat - read hb_* from the route row; per-session Batch guard from
                  hb_inflight_batch; no HTTP probe; ``batch_busy`` is IGNORED.
      both      - run probe AND heartbeat; log agreement/disagreement; act on
                  probe result so the migration is safe.
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

    # G2/G3: resolve busy according to the active health mode.
    hb = _heartbeat_busy(route)  # always computed; used in heartbeat/both modes

    if HEALTH_MODE == "heartbeat":
        # Heartbeat mode: no HTTP probe, no global Batch ListJobs.
        # hb_inflight_batch provides the per-session Batch guard.
        busy = hb["busy"]
        health = hb  # unify the shape for the response dict below
    elif HEALTH_MODE == "both":
        # Both mode: run the HTTP probe AND heartbeat; log agreement;
        # act on the probe result (safe migration/validation).
        health = _probe_health(private_ip) if private_ip else {
            "reachable": False, "busy": True, "active_connections": -1
        }
        probe_busy = health["busy"] or batch_busy
        hb_busy_result = hb["busy"]
        agree = probe_busy == hb_busy_result
        if not agree:
            logger.warning(
                "health-mode=both DISAGREE for session %s: probe_busy=%s hb_busy=%s "
                "hb_stale=%s hb_inflight_batch=%d",
                session_id,
                probe_busy,
                hb_busy_result,
                hb.get("stale"),
                hb.get("hb_inflight_batch", 0),
            )
        else:
            logger.info(
                "health-mode=both AGREE for session %s: busy=%s",
                session_id,
                probe_busy,
            )
        busy = probe_busy  # act on probe (the source of truth in both mode)
    else:
        # probe mode (default): original behavior unchanged.
        health = _probe_health(private_ip) if private_ip else {
            "reachable": False, "busy": True, "active_connections": -1
        }
        busy = health["busy"] or batch_busy

    if busy:
        _write_streak(user_ulid, session_id, 0)
        result: dict = {
            "session_id": session_id,
            "action": "noop",
            "reason": "busy",
            "reachable": health.get("reachable", True),
            "active_connections": health.get("active_connections", -1),
            "health_busy": health.get("busy"),
            "batch_in_flight": batch_busy if HEALTH_MODE != "heartbeat" else hb.get("hb_inflight_batch", 0) > 0,
            "idle_streak": 0,
            "health_mode": HEALTH_MODE,
        }
        return result

    # Confirmed idle -> advance this session's streak.
    streak = int(route.get("idle_streak", 0)) + 1
    if streak >= IDLE_THRESHOLD_CHECKS:
        _stop_task(task_arn, "grace2 idle threshold reached")
        # DRY_RUN must be side-effect-free: deleting the route while the task
        # keeps running (StopTask is a no-op under dry-run) would MANUFACTURE an
        # orphan (running task, no route) -- exactly the leak this reaper now
        # guards against. Only drop the route when a real StopTask was issued.
        if not DRY_RUN:
            _delete_route(user_ulid, session_id)
        return {
            "session_id": session_id,
            "action": "stop" if not DRY_RUN else "stop_dryrun",
            "reason": "idle_threshold_reached",
            "active_connections": health.get("active_connections", -1),
            "idle_streak": streak,
            "threshold": IDLE_THRESHOLD_CHECKS,
            "health_mode": HEALTH_MODE,
        }

    _write_streak(user_ulid, session_id, streak)
    return {
        "session_id": session_id,
        "action": "noop",
        "reason": "idle_below_threshold",
        "active_connections": health.get("active_connections", -1),
        "idle_streak": streak,
        "threshold": IDLE_THRESHOLD_CHECKS,
        "health_mode": HEALTH_MODE,
    }


# --------------------------------------------------------------------------- #
# Orphan + max-age: enumerate RUNNING agent-session tasks directly and reap the
# ones no live route backs (past the provision grace) or that are simply too old.
# --------------------------------------------------------------------------- #


def _list_running_task_arns() -> list[str]:
    """All RUNNING task ARNs for the agent-session family in the cluster.

    Scoped to ``AGENT_TASK_FAMILY`` so the always-on broker service task (same
    cluster) is NEVER returned. On any error returns [] (no orphan reap this
    tick -- fail-safe: never act on an incomplete listing)."""
    arns: list[str] = []
    try:
        token: str | None = None
        while True:
            kwargs: dict = {
                "cluster": CLUSTER,
                "family": TASK_FAMILY,
                "desiredStatus": "RUNNING",
                "maxResults": 100,
            }
            if token:
                kwargs["nextToken"] = token
            resp = _ecs.list_tasks(**kwargs)
            arns.extend(resp.get("taskArns", []))
            token = resp.get("nextToken")
            if not token:
                break
    except Exception:  # noqa: BLE001 -- never raise; skip orphan reap this tick
        logger.exception("list_tasks failed; no orphan/max-age reap this tick")
        return []
    return arns


def _describe_running_tasks(arns: list[str]) -> list[dict]:
    """DescribeTasks in <=100 batches; return the RUNNING task dicts. Fail-safe []."""
    out: list[dict] = []
    for i in range(0, len(arns), 100):
        batch = arns[i : i + 100]
        try:
            resp = _ecs.describe_tasks(cluster=CLUSTER, tasks=batch)
        except Exception:  # noqa: BLE001
            logger.exception("describe_tasks (orphan pass) failed for a batch")
            continue
        for t in resp.get("tasks", []):
            if t.get("lastStatus") == "RUNNING":
                out.append(t)
    return out


def _task_age_seconds(task: dict, now: float) -> float:
    """Age (seconds) of a task from its startedAt (fallback createdAt).

    boto3 returns tz-aware datetimes; ``.timestamp()`` yields epoch seconds. If
    neither field is present (a just-provisioned task not yet timestamped) the age
    is 0.0 -- the safe direction (treated as young, never reaped)."""
    ts = task.get("startedAt") or task.get("createdAt")
    if ts is None:
        return 0.0
    try:
        started = ts.timestamp()  # datetime -> epoch seconds
    except AttributeError:
        try:
            started = float(ts)  # already-epoch (test convenience)
        except (TypeError, ValueError):
            return 0.0
    return max(0.0, now - started)


def _orphan_maxage_decision(
    *,
    age_seconds: float,
    has_live_route: bool,
    health_busy: bool,
    batch_busy: bool,
    orphan_grace_seconds: int,
    max_age_seconds: int,
) -> tuple[str, str]:
    """Pure decision for ONE running task: return ("stop"|"keep", reason).

    Order matters:
      1. MAX-AGE backstop: past max_age -> stop, UNLESS the task is route-backed
         and INDIVIDUALLY busy right now (a live client mid-work) -- then keep
         this tick (SAFETY: never kill a live-route + busy task). A no-route task
         past max-age is always stopped (health is ignored -- it is a leak).
      2. ORPHAN: no live route. Younger than the provision grace -> keep (it may
         be mid-provision before its route is written). A global Batch solve in
         flight -> keep (conservative). Its own health busy -> keep. Else stop.
      3. Route-backed and young -> keep (the route-correlated pass owns it).
    """
    if age_seconds >= max_age_seconds:
        if has_live_route and health_busy:
            return "keep", "max_age_but_route_busy"
        return "stop", "max_age"
    if not has_live_route:
        if age_seconds < orphan_grace_seconds:
            return "keep", "orphan_within_grace"
        if batch_busy:
            return "keep", "orphan_batch_in_flight"
        if health_busy:
            return "keep", "orphan_busy"
        return "stop", "orphan"
    return "keep", "route_backed"


def _reap_orphans_and_max_age(
    running_tasks: list[dict],
    route_task_map: dict[str, tuple[str, str]],
    batch_busy: bool,
    now: float,
) -> list[dict]:
    """Evaluate every RUNNING agent-session task for orphan / max-age reaping.

    ``route_task_map`` maps task_arn -> (user_ulid, session_id) for every task
    backed by a live route row (from the scan). A task NOT in the map is an
    orphan candidate. Emits one decision dict per task."""
    decisions: list[dict] = []
    for task in running_tasks:
        arn = task.get("taskArn", "")
        age = _task_age_seconds(task, now)
        has_route = arn in route_task_map
        short_id = arn.rsplit("/", 1)[-1] if arn else ""

        # Probe health ONLY when the outcome could be a stop we must protect:
        #   - a route-backed task at/over max-age (spare it if busy), or
        #   - an orphan past the grace with no in-flight Batch solve.
        need_probe = (age >= MAX_AGE_SECONDS and has_route) or (
            not has_route and age >= ORPHAN_GRACE_SECONDS and not batch_busy
        )
        health_busy = False
        if need_probe:
            ip = _extract_private_ip(task)
            # Unreadable / no IP -> busy (fail-safe): a route-backed task is then
            # spared at max-age; an orphan is only spared until max-age forces it.
            health_busy = _probe_health(ip)["busy"] if ip else True

        action, reason = _orphan_maxage_decision(
            age_seconds=age,
            has_live_route=has_route,
            health_busy=health_busy,
            batch_busy=batch_busy,
            orphan_grace_seconds=ORPHAN_GRACE_SECONDS,
            max_age_seconds=MAX_AGE_SECONDS,
        )

        decision = {
            "task_id": short_id,
            "age_seconds": int(age),
            "has_route": has_route,
            "action": action,
            "reason": reason,
        }
        if action == "stop":
            _stop_task(arn, f"grace2 reaper: {reason}")
            decision["action"] = reason if not DRY_RUN else f"{reason}_dryrun"
            # Drop any live route pinned to this task so a reconnect reprovisions.
            # (Only on a REAL stop -- dry-run stays side-effect-free.)
            if not DRY_RUN and has_route:
                u, s = route_task_map[arn]
                _delete_route(u, s)
        decisions.append(decision)
    return decisions


def _extract_private_ip(task: dict) -> str:
    """Pull the awsvpc ENI private IPv4 from a DescribeTasks task ("" if none)."""
    for att in task.get("attachments", []):
        if att.get("type") == "ElasticNetworkInterface":
            for detail in att.get("details", []):
                if detail.get("name") == "privateIPv4Address":
                    return detail.get("value") or ""
    return ""


# --------------------------------------------------------------------------- #
# Handler
# --------------------------------------------------------------------------- #


def handler(event, context):  # noqa: ANN001, ARG001
    """EventBridge-scheduled per-task idle reaper. Returns the per-session
    decisions (JSON-serialisable)."""
    routes = _scan_routes()
    # G3 global Batch guard: computed ONCE per tick in probe/both modes.
    # In heartbeat mode the per-session hb_inflight_batch field is used instead
    # (no ListJobs call -> no Batch interface endpoint needed).
    batch_busy = _batch_solve_in_flight() if HEALTH_MODE != "heartbeat" else False

    # Map task_arn -> (user_ulid, session_id) for every LIVE route BEFORE the
    # route-correlated pass mutates rows, so the orphan pass classifies against a
    # stable snapshot (a task the route pass stops still counts as route-backed
    # here, so it is never double-stopped by the orphan pass).
    route_task_map: dict[str, tuple[str, str]] = {}
    for r in routes:
        arn = r.get("task_arn")
        if arn:
            route_task_map[arn] = (r.get("user_ulid", ""), r.get("session_id", ""))

    # PASS 1: the existing route-correlated idle reaper.
    decisions = [_reap_one(r, batch_busy) for r in routes]

    # PASS 2: orphan + max-age reaping over ALL running agent-session tasks --
    # catches tasks no live route backs (the leak) and any task that is simply
    # too old, neither of which PASS 1 can ever see.
    now = time.time()
    running_tasks = _describe_running_tasks(_list_running_task_arns())
    orphan_decisions = _reap_orphans_and_max_age(
        running_tasks, route_task_map, batch_busy, now
    )

    orphans_found = sum(1 for d in orphan_decisions if not d["has_route"])
    orphans_stopped = sum(
        1 for d in orphan_decisions if str(d["action"]).startswith("orphan")
    )
    max_age_stopped = sum(
        1 for d in orphan_decisions if str(d["action"]).startswith("max_age")
    )
    summary = {
        "routes_seen": len(routes),
        "running_tasks_seen": len(running_tasks),
        "batch_in_flight": batch_busy,
        "stopped": sum(1 for d in decisions if d.get("action") in ("stop", "stop_dryrun")),
        "orphans_found": orphans_found,
        "orphans_stopped": orphans_stopped,
        "max_age_stopped": max_age_stopped,
        "dry_run": DRY_RUN,
        "health_mode": HEALTH_MODE,
        "decisions": decisions,
        "orphan_decisions": orphan_decisions,
    }
    logger.info("task-reaper: %s", summary)
    return summary
