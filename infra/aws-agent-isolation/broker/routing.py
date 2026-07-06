"""Route-resolve + provision-on-miss + sub->ULID for the GRACE-2 session broker.

This is the CONCRETE control flow the spike (sections 4.1-4.3) specifies, with the
raw WS byte-proxy left as a documented skeleton in proxy.py. Per new WSS
connection the broker (in app.py) calls, in order:

  1. cognito_verify(token) -> claims{uid=sub}        (cognito_verify.py, zero-drift)
  2. resolve_user_ulid(sub) -> internal ULID         (users firebase_uid-index GSI)
  3. resolve_route(user_ulid, session_id)            (ConsistentRead grace2_session_routes)
        HIT  -> proxy to the existing task
        MISS -> provision_task(...) -> wait_health -> write_route -> proxy

Decision 10: the OWNER id is the internal ULID, NOT the Cognito sub. Both of a
tab's dual sockets carry the SAME localStorage session_id, so the second socket's
resolve_route HITS the just-written row and lands on the SAME task -- preserving
the agent's in-process dual-socket convergence, now scoped to ONE task.

sub->ULID MIRRORS the canonical resolver in the case_list Lambda
(infra/aws-autostop/lambda/case_list/handler.py::_resolve_internal_uid) and
Persistence.get_user_by_firebase_uid: query the users table firebase_uid-index
GSI; the matched item's ``_id`` is the ULID. Fail closed to None.

All AWS clients are injected (the unit tests pass mocks); no module-level boto3
client is created at import so the tests never touch live AWS.
"""

from __future__ import annotations

import logging
import time
from dataclasses import dataclass
from typing import Any, Callable, Optional

logger = logging.getLogger("grace2.broker.routing")


# --------------------------------------------------------------------------- #
# Config bag (env -> the broker passes one of these around).
# --------------------------------------------------------------------------- #
@dataclass
class RoutingConfig:
    routes_table: str
    users_table: str
    users_firebase_uid_index: str
    ecs_cluster: str
    agent_task_definition: str
    agent_container_name: str
    agent_ws_port: int
    agent_health_port: int
    task_subnets: list[str]
    task_security_groups: list[str]
    route_ttl_seconds: int = 86400
    #: How long provision-on-miss polls :8766 health before giving up.
    provision_timeout_s: float = 90.0
    provision_poll_interval_s: float = 2.0


@dataclass
class Route:
    """A resolved session route -> the task to proxy to."""
    user_ulid: str
    session_id: str
    task_arn: str
    private_ip: str
    port: int
    state: str = "RUNNING"


# --------------------------------------------------------------------------- #
# sub -> internal ULID (mirror of case_list _resolve_internal_uid).
# --------------------------------------------------------------------------- #
def resolve_user_ulid(ddb_resource, cfg: RoutingConfig, sub: str) -> Optional[str]:
    """Resolve a Cognito sub to the internal ULID via the users firebase_uid GSI.

    Returns the ULID (users._id) on a hit, else None. NEVER raises: a missing
    table, a GSI error, or no record all fail closed to None (the caller then
    rejects the connect rather than mis-route). Mirrors the case_list Lambda.

    ``ddb_resource`` is a boto3 DynamoDB *resource* (so .Table().query works);
    the tests inject a fake with the same surface.
    """
    if not sub:
        return None
    try:
        from boto3.dynamodb.conditions import Key
    except Exception:  # pragma: no cover - boto3 always present at runtime
        Key = None  # type: ignore

    try:
        table = ddb_resource.Table(cfg.users_table)
        if Key is not None:
            resp = table.query(
                IndexName=cfg.users_firebase_uid_index,
                KeyConditionExpression=Key("firebase_uid").eq(sub),
                Limit=1,
            )
        else:  # pragma: no cover - test path passes a fake that ignores this
            resp = table.query(IndexName=cfg.users_firebase_uid_index, Limit=1)
        items = resp.get("Items") or []
    except Exception as exc:  # noqa: BLE001 - fail closed to None
        logger.info("users-table resolve failed (%s); no internal id", type(exc).__name__)
        return None
    if not items:
        return None
    internal = items[0].get("_id")
    if not isinstance(internal, str) or not internal:
        return None
    return internal


# --------------------------------------------------------------------------- #
# First-connect user provisioning -- mint the users row a brand-new verified sub
# has no row for yet (e.g. a code-gate demo user). The agent normally creates this
# row IN-BAND on first connect (auth_handshake._resolve_or_provision_user ->
# Persistence.upsert_user), but the broker resolves sub->ULID BEFORE the agent is
# ever reached, so on a true first connect resolve_user_ulid returns None and the
# connect was rejected (chicken-and-egg). We mint the row here instead, mirroring
# the agent's user shape EXACTLY so the broker- and agent-created rows never drift.
#
# ZERO-DRIFT (same discipline as cognito_verify.py): PREFER the agent's real User
# contract + ULID/timestamp helpers (the broker image installs grace2_agent +
# grace2_contracts), so a broker-minted row is byte-identical to an agent-minted
# one. Fall back to a vendored builder only when those are not importable (a
# minimal image or the unit-test env).
# --------------------------------------------------------------------------- #
try:  # pragma: no cover - exercised in the broker image; vendored path in tests
    from grace2_contracts import new_ulid as _new_ulid, now_utc as _now_utc
    from grace2_contracts.user import User as _User
except Exception:  # noqa: BLE001 - contracts not on the path -> vendored fallback
    _new_ulid = None  # type: ignore
    _now_utc = None  # type: ignore
    _User = None  # type: ignore


def _vendored_new_ulid() -> str:
    """A syntactically valid 26-char Crockford-base32 ULID (matches new_ulid())."""
    try:
        from ulid import ULID  # python-ulid, the same lib grace2_contracts uses

        return str(ULID())
    except Exception:  # noqa: BLE001 - last-resort pure-stdlib generator
        import os

        crock = "0123456789ABCDEFGHJKMNPQRSTVWXYZ"
        n = int.from_bytes(os.urandom(16), "big")
        chars = []
        for _ in range(26):
            chars.append(crock[n & 0x1F])
            n >>= 5
        return "".join(reversed(chars))[-26:]


def _vendored_now_z() -> str:
    """ISO-8601 UTC with a ``Z`` suffix (matches the contract's UTCDatetime)."""
    from datetime import datetime, timezone

    return datetime.now(timezone.utc).isoformat().replace("+00:00", "Z")


def build_user_item(
    sub: str,
    *,
    email: Optional[str] = None,
    display_name: Optional[str] = None,
    ulid: Optional[str] = None,
) -> dict:
    """Build the DynamoDB users-row item for a first-connect sub.

    Mirrors auth_handshake._resolve_or_provision_user + Persistence.upsert_user
    EXACTLY: the item is ``User.model_dump(mode="json")`` with ``_id`` set to the
    user_id (the dynamo_backend stores the model dump under PK ``_id``). The
    vendored fallback reproduces the identical key set + value types.
    """
    if _User is not None and _new_ulid is not None and _now_utc is not None:
        user = _User(
            user_id=ulid or _new_ulid(),
            firebase_uid=sub,
            email=email,
            display_name=display_name,
            created_at=_now_utc(),
            is_active=True,
            prefs={},
        )
        body = user.model_dump(mode="json")
        body["_id"] = user.user_id
        return body
    uid = ulid or _vendored_new_ulid()
    return {
        "schema_version": "v1",
        "user_id": uid,
        "firebase_uid": sub,
        "email": email,
        "display_name": display_name,
        "created_at": _vendored_now_z(),
        "is_active": True,
        "prefs": {},
        "is_anonymous": False,
        "_id": uid,
    }


def provision_user(
    ddb_resource,
    cfg: RoutingConfig,
    sub: str,
    *,
    email: Optional[str] = None,
    display_name: Optional[str] = None,
) -> Optional[str]:
    """Create the users row for a verified ``sub`` with no internal ULID yet and
    return the new internal ULID (users._id). None if creation truly failed.

    Race/idempotency: the write is a conditional PutItem
    (``attribute_not_exists(_id)``) so a ULID collision can never clobber an
    existing row; on ANY write failure -- a conditional-check fail, a concurrent
    create by a second broker, or a transient error -- we re-read the GSI and
    adopt whatever ULID is now resolvable, so the loser of a race REUSES the
    winner's row instead of forking a second identity. app.py additionally
    serializes provisioning per-sub in-process, which fully covers the common
    single-broker dual-socket (App + Chat) case.
    """
    if not sub:
        return None
    item = build_user_item(sub, email=email, display_name=display_name)
    try:
        from boto3.dynamodb.conditions import Attr
    except Exception:  # pragma: no cover - boto3 always present at runtime
        Attr = None  # type: ignore
    try:
        table = ddb_resource.Table(cfg.users_table)
        if Attr is not None:
            table.put_item(Item=item, ConditionExpression=Attr("_id").not_exists())
        else:  # pragma: no cover - test path
            table.put_item(Item=item)
        logger.info("first-connect provisioned user _id=%s for a new firebase_uid", item["_id"])
        return item["_id"]
    except Exception as exc:  # noqa: BLE001 - collision/race/perm -> re-resolve
        logger.info("provision_user put failed (%s); re-resolving sub", type(exc).__name__)
        existing = resolve_user_ulid(ddb_resource, cfg, sub)
        if existing:
            return existing
        logger.warning("provision_user failed and sub still has no internal ULID")
        return None


# --------------------------------------------------------------------------- #
# Route resolve (ConsistentRead the routes table).
# --------------------------------------------------------------------------- #
def resolve_route(
    ddb_resource, cfg: RoutingConfig, user_ulid: str, session_id: str
) -> Optional[Route]:
    """ConsistentRead grace2_session_routes(user_ulid, session_id).

    Returns a Route on a HIT (and the row's task is presumed live -- the reaper
    deletes the row on StopTask, so a present row means a provisioned task),
    None on a MISS or any read error (caller then provisions).
    """
    try:
        table = ddb_resource.Table(cfg.routes_table)
        resp = table.get_item(
            Key={"user_ulid": user_ulid, "session_id": session_id},
            ConsistentRead=True,
        )
        item = resp.get("Item")
    except Exception as exc:  # noqa: BLE001 - treat a read error as a miss
        logger.info("route read failed (%s); treating as miss", type(exc).__name__)
        return None
    if not item:
        return None
    task_arn = item.get("task_arn")
    private_ip = item.get("private_ip")
    if not task_arn or not private_ip:
        # Half-written / corrupt row -> treat as a miss and reprovision.
        logger.info("route row incomplete for %s/%s; treating as miss", user_ulid, session_id)
        return None
    return Route(
        user_ulid=user_ulid,
        session_id=session_id,
        task_arn=task_arn,
        private_ip=private_ip,
        port=int(item.get("port", cfg.agent_ws_port)),
        state=item.get("state", "RUNNING"),
    )


# --------------------------------------------------------------------------- #
# Provision-on-miss: RunTask -> wait :8766 health-green -> write the route row.
# --------------------------------------------------------------------------- #
def provision_task(
    ecs_client,
    ddb_resource,
    cfg: RoutingConfig,
    user_ulid: str,
    session_id: str,
    *,
    health_probe: Callable[[str, int], bool],
    sleep: Callable[[float], None] = time.sleep,
    now: Callable[[], float] = time.time,
) -> Optional[Route]:
    """ecs:RunTask a per-session agent task, wait for RUNNING + a health-green
    :8766 probe, write the route row, and return the Route. None on failure.

    Idempotency note: two near-simultaneous sockets of one tab could both miss
    and both RunTask. The cheap guard (and the one app.py uses) is to re-read the
    route after acquiring a per-(user,session) in-process lock; the SECOND caller
    then HITs the row the first wrote. A belt-level guard is a conditional PutItem
    (attribute_not_exists(task_arn)) so only one row wins; the loser StopTasks its
    extra task. The conditional-write guard is a documented TODO -- the
    in-process lock in app.py covers the common dual-socket case.

    ``health_probe(private_ip, port)`` returns True when GET
    http://private_ip:port/api/health is green; the tests inject a fake.
    """
    task_arn = _run_task(ecs_client, cfg, user_ulid, session_id)
    if not task_arn:
        return None

    private_ip = _wait_running_ip(ecs_client, cfg, task_arn, sleep=sleep, now=now)
    if not private_ip:
        logger.warning("task %s never reported a private IP; abandoning", task_arn)
        _safe_stop(ecs_client, cfg, task_arn, "no private ip")
        return None

    deadline = now() + cfg.provision_timeout_s
    while now() < deadline:
        if health_probe(private_ip, cfg.agent_health_port):
            route = Route(
                user_ulid=user_ulid,
                session_id=session_id,
                task_arn=task_arn,
                private_ip=private_ip,
                port=cfg.agent_ws_port,
            )
            _write_route(ddb_resource, cfg, route, now=now)
            logger.info("provisioned %s for %s/%s", task_arn, user_ulid, session_id)
            return route
        sleep(cfg.provision_poll_interval_s)

    logger.warning("task %s never went health-green within %ss", task_arn, cfg.provision_timeout_s)
    _safe_stop(ecs_client, cfg, task_arn, "health never green")
    return None


def _run_task(ecs_client, cfg: RoutingConfig, user_ulid: str, session_id: str) -> Optional[str]:
    try:
        resp = ecs_client.run_task(
            cluster=cfg.ecs_cluster,
            taskDefinition=cfg.agent_task_definition,
            launchType="FARGATE",
            count=1,
            networkConfiguration={
                "awsvpcConfiguration": {
                    "subnets": cfg.task_subnets,
                    "securityGroups": cfg.task_security_groups,
                    "assignPublicIp": "ENABLED",
                }
            },
            # Tag the task with the owning session so the reaper / ops can
            # attribute it; also lets the per-session Batch guard correlate.
            tags=[
                {"key": "grace2:user_ulid", "value": user_ulid},
                {"key": "grace2:session_id", "value": session_id},
            ],
            propagateTags="TASK_DEFINITION",
            # Phase-1 scale-to-zero (design 2.3): inject the route identity as
            # env vars so the agent can write its own heartbeat to the route row
            # without having to discover these from ECS task metadata at runtime.
            # GRACE2_ROUTE_HEARTBEAT_SECONDS arms the writer (0 = disabled by
            # default, non-zero value comes from the task definition baseline and
            # is preserved here; the override ONLY sets the identity vars).
            overrides={
                "containerOverrides": [
                    {
                        "name": cfg.agent_container_name,
                        "environment": [
                            {"name": "GRACE2_ROUTE_USER_ULID", "value": user_ulid},
                            {"name": "GRACE2_ROUTE_SESSION_ID", "value": session_id},
                        ],
                    }
                ]
            },
        )
        tasks = resp.get("tasks") or []
        failures = resp.get("failures") or []
        if not tasks:
            logger.warning("RunTask returned no task (failures=%r)", failures)
            return None
        return tasks[0].get("taskArn")
    except Exception as exc:  # noqa: BLE001
        logger.exception("RunTask failed for %s/%s: %s", user_ulid, session_id, exc)
        return None


def _wait_running_ip(
    ecs_client,
    cfg: RoutingConfig,
    task_arn: str,
    *,
    sleep: Callable[[float], None],
    now: Callable[[], float],
) -> Optional[str]:
    """Poll DescribeTasks until the task is RUNNING and return its ENI private IP."""
    deadline = now() + cfg.provision_timeout_s
    while now() < deadline:
        try:
            resp = ecs_client.describe_tasks(cluster=cfg.ecs_cluster, tasks=[task_arn])
            tasks = resp.get("tasks") or []
        except Exception as exc:  # noqa: BLE001
            logger.info("describe_tasks failed (%s); retrying", type(exc).__name__)
            tasks = []
        if tasks:
            t = tasks[0]
            status = t.get("lastStatus")
            if status in ("STOPPED", "DEPROVISIONING"):
                logger.warning("task %s went %s during provision", task_arn, status)
                return None
            if status == "RUNNING":
                ip = _extract_private_ip(t)
                if ip:
                    return ip
        sleep(cfg.provision_poll_interval_s)
    return None


def _extract_private_ip(task: dict) -> Optional[str]:
    """Pull the awsvpc ENI private IPv4 from a DescribeTasks task."""
    for att in task.get("attachments", []):
        if att.get("type") == "ElasticNetworkInterface":
            for detail in att.get("details", []):
                if detail.get("name") == "privateIPv4Address":
                    return detail.get("value")
    return None


def _write_route(ddb_resource, cfg: RoutingConfig, route: Route, *, now: Callable[[], float]) -> None:
    try:
        table = ddb_resource.Table(cfg.routes_table)
        table.put_item(
            Item={
                "user_ulid": route.user_ulid,
                "session_id": route.session_id,
                "task_arn": route.task_arn,
                "private_ip": route.private_ip,
                "port": route.port,
                "state": route.state,
                "idle_streak": 0,
                "last_seen": int(now()),
                "expires_at": int(now()) + cfg.route_ttl_seconds,
            }
        )
    except Exception as exc:  # noqa: BLE001 - a failed write means a reconnect re-provisions
        logger.exception("write route failed for %s/%s: %s", route.user_ulid, route.session_id, exc)


def _safe_stop(ecs_client, cfg: RoutingConfig, task_arn: str, reason: str) -> None:
    try:
        ecs_client.stop_task(cluster=cfg.ecs_cluster, task=task_arn, reason=reason)
    except Exception:  # noqa: BLE001
        logger.exception("StopTask cleanup failed for %s", task_arn)


# --------------------------------------------------------------------------- #
# The single decision the app calls: resolve-or-provision.
# --------------------------------------------------------------------------- #
def resolve_or_provision(
    ddb_resource,
    ecs_client,
    cfg: RoutingConfig,
    user_ulid: str,
    session_id: str,
    *,
    health_probe: Callable[[str, int], bool],
) -> Optional[Route]:
    """The end-to-end route decision: HIT -> return; MISS -> provision + return.

    app.py wraps this in a per-(user_ulid, session_id) in-process lock so a tab's
    two near-simultaneous sockets do not double-provision (the second re-reads
    and HITs).
    """
    existing = resolve_route(ddb_resource, cfg, user_ulid, session_id)
    if existing is not None:
        logger.info("route HIT %s/%s -> %s", user_ulid, session_id, existing.task_arn)
        return existing
    logger.info("route MISS %s/%s -> provisioning", user_ulid, session_id)
    return provision_task(
        ecs_client,
        ddb_resource,
        cfg,
        user_ulid,
        session_id,
        health_probe=health_probe,
    )
