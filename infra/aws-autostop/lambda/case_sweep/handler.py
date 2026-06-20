"""Case-sweep Lambda for GRACE-2 ANON EPHEMERAL Case release (job-0147).

This is the PRIMARY release mechanism for anonymous, ephemeral Cases. An anon
Case carries an ``expires_at`` heartbeat horizon (D.6 TTL driver, written by
``persistence.touch_session`` / the case heartbeat): every interaction pushes
``expires_at`` further out, so a Case that stops being touched eventually falls
behind ``now`` and becomes eligible for release. DynamoDB-native TTL on the
``expires_at`` attribute is a cheap BACKSTOP NATE provisions separately; this
sweep is the deterministic, observable, side-effect-reaping primary path.

Runs on an EventBridge schedule (e.g. every 15 minutes). For every EXPIRED anon
Case it:

  1. SELECTS only items where ALL of these hold (fail-safe by construction --
     any case that does not match every clause is left ALONE):
       S1. ``expires_at`` attribute is PRESENT. An authed/legacy-migrated Case
           that carries no ``expires_at`` is NEVER touched (authed Cases live
           forever; this sweep only reaps the ephemeral anon ones).
       S2. ``expires_at`` < now (the heartbeat horizon has lapsed). A Case a
           live viewer kept warm has a FUTURE ``expires_at`` and is skipped.
       S3. the owner (``user_id``, falling back to ``owner_user_id``) is NOT
           ``MIGRATION_ANON_UID`` -- the pre-Auth migration sentinel. Those are
           legacy authed-equivalent Cases and must NEVER be swept.
       S4. the Case is not ALREADY a tombstone (``status in {deleted,
           archived}``) -- already released, nothing to do.

  2. SOFT-DELETES each selected Case with the SAME tombstone write
     ``Persistence.delete_case`` performs: ``status="deleted"`` +
     ``deleted_at=<now ISO-8601 Z>``. NEVER a hard delete -- a future curator
     tool owns hard deletion; data-retention rules (D.2 ``deleted_at``) point
     this way anyway.

  3. BEST-EFFORT reaps the orphaned side-effects of the released Case, EACH
     guarded in its own try/except so a reap failure can never abort the sweep:
       - ``grace2_chat`` rows for that ``case_id`` (PK ``case_id`` + SK
         ``message_id``): query + batch delete.
       - the S3 case-view snapshot ``case-views/{case_id}.json`` in the runs
         bucket (the cold-view materialization the view-signer presigns).
       - the per-Case ``.qgs`` object (only when a QGS bucket is configured;
         GCP is being decommissioned, so this reap is optional).

  3. Returns a JSON-serializable decision ``{scanned, released, errors}``.

FAIL-SAFE: any per-item error is logged and SKIPPED -- the handler NEVER raises.
A single malformed/erroring Case must not stall the whole sweep.

DRY_RUN: defaults to ``true`` (mirrors idle_check's DRY_RUN discipline) so the
first deploy LOGS every release decision WITHOUT mutating anything -- the
orchestrator validates the selection against the live table before arming. Flip
``DRY_RUN=false`` to actually tombstone + reap.

This module is STANDALONE: it talks to DynamoDB + S3 directly via boto3 and does
NOT import the agent package (a different deploy unit). The soft-delete write
shape, the table/key shapes (``grace2_`` prefix, ``_id`` PK on cases, ``case_id``
PK + ``message_id`` SK on chat), the ``MIGRATION_ANON_UID`` sentinel, and the
``case-views/{case_id}.json`` snapshot path are REPLICATED here verbatim from the
agent side -- keep them in sync.

No third-party deps beyond boto3 (in the Lambda runtime). Unit-tested in
``tests/test_case_sweep.py`` with boto3 fully mocked -- no live AWS.
"""

from __future__ import annotations

import logging
import os
from datetime import datetime, timezone
from decimal import Decimal
from typing import Any

import boto3
from botocore.config import Config as BotoConfig

logger = logging.getLogger()
logger.setLevel(logging.INFO)

# --------------------------------------------------------------------------- #
# Configuration (all from environment -- set by the tofu root).
# --------------------------------------------------------------------------- #

REGION = os.environ.get("AWS_REGION", "us-west-2")

#: DynamoDB cases table (mirrors GRACE2_DYNAMO_TABLE_PREFIX + "cases" on the
#: agent side; PK ``_id`` == case_id). Defaults to the live table name.
STATE_TABLE = os.environ.get("STATE_TABLE", "grace2_cases").strip()

#: DynamoDB chat table (PK ``case_id`` + SK ``message_id``). Empty disables the
#: chat reap (the soft-delete + snapshot reap still run).
CHAT_TABLE = os.environ.get("CHAT_TABLE", "grace2_chat").strip()

#: Durable runs bucket holding the cold-view snapshot
#: ``case-views/{case_id}.json``. Empty disables the snapshot reap.
RUNS_BUCKET = os.environ.get("RUNS_BUCKET", "").strip()
#: Prefix under the runs bucket for the snapshot (mirrors persistence
#: CASE_VIEWS_PREFIX / the view-signer VIEW_PREFIX).
VIEW_PREFIX = "case-views"

#: Per-Case ``.qgs`` bucket. Empty disables the .qgs reap (GCP is being
#: decommissioned, so this side-effect may not exist on AWS yet -- optional).
QGS_BUCKET = os.environ.get("QGS_BUCKET", "").strip()

#: Synthetic owner UID stamped on every pre-Auth (legacy-migrated) Case.
#: REPLICATED from services/agent/src/grace2_agent/auth.py MIGRATION_ANON_UID --
#: a Case owned by this sentinel is legacy-authed-equivalent and is NEVER swept,
#: even if it somehow carried an expires_at. Keep in sync with auth.py.
MIGRATION_ANON_UID = "__preauth_migration_anon__"

#: Tombstone statuses -- a Case already in one of these is already released.
#: REPLICATED from persistence.list_cases_for_user / CaseStatus.
_TOMBSTONE_STATUSES = {"deleted", "archived"}

#: Set DRY_RUN=true (the DEFAULT) to LOG every release decision WITHOUT
#: mutating DynamoDB or S3 -- mirrors idle_check's DRY_RUN discipline so the
#: first deploy can be validated against the live table before arming.
DRY_RUN = os.environ.get("DRY_RUN", "true").lower() in ("1", "true", "yes")

#: Cap the per-invocation DynamoDB calls so a stalled table can never pin the
#: Lambda to its timeout. The next scheduled tick picks up any remainder.
_ddb = boto3.resource(
    "dynamodb",
    region_name=REGION,
    config=BotoConfig(
        connect_timeout=3,
        read_timeout=5,
        retries={"max_attempts": 3, "mode": "standard"},
    ),
)
_s3 = boto3.client("s3", region_name=REGION)


# --------------------------------------------------------------------------- #
# Time + value helpers.
# --------------------------------------------------------------------------- #


def _now() -> datetime:
    """Current UTC time (overridable in tests via monkeypatch on this symbol)."""
    return datetime.now(timezone.utc)


def _iso_z(dt: datetime) -> str:
    """ISO-8601 with a trailing ``Z`` -- the EXACT shape the agent writes for
    ``deleted_at`` / ``expires_at`` (``isoformat().replace("+00:00", "Z")``)."""
    if dt.tzinfo is None:
        dt = dt.replace(tzinfo=timezone.utc)
    return dt.astimezone(timezone.utc).isoformat().replace("+00:00", "Z")


def _parse_expires_at(value: Any) -> datetime | None:
    """Parse an ``expires_at`` value into an aware UTC datetime, or None if
    absent/unparseable.

    The LIVE write shape is a NUMERIC epoch-seconds attribute (see
    ``persistence.upsert_case`` / ``touch_case``: ``int(now.timestamp()) + ttl``)
    -- DynamoDB-native TTL only honours a Number attribute. So the PRIMARY,
    load-bearing path here is a numeric epoch, NOT the ISO-8601 string the
    sessions collection uses.

    Accepted shapes (in priority order):

      1. A native number (``int`` / ``float`` / ``Decimal``) -- the canonical
         write shape AFTER the boto3 dynamodb *resource* deserializes it. The
         handler reads items via ``boto3.resource(...).Table.scan`` and runs each
         through ``_from_ddb``, which turns a DynamoDB ``{"N": "..."}`` into a
         Python ``int``/``float`` (a ``Decimal`` is also tolerated directly here
         in case the value is parsed before ``_from_ddb``). Treated as
         epoch-seconds-since-1970-UTC.
      2. A ``{"N": "..."}`` low-level DynamoDB attribute map -- the RAW shape a
         boto3 *client* (not resource) would return. Defensive: if this handler
         is ever pointed at the low-level client, a digit-string under ``"N"`` is
         the real shape, so coerce it to epoch-seconds.
      3. A digit / float STRING (e.g. ``"1781001600"`` or ``"1781001600.0"``) --
         coerced to epoch-seconds. Load-bearing if a number ever arrives as a
         bare string.
      4. An ISO-8601 STRING (the agent's ``...Z`` shape) -- tolerant FALLBACK
         only, retained for legacy/sessions-style writes. Trailing ``Z`` is
         normalised (Python <3.11 ``fromisoformat`` rejects it); a naive
         timestamp is assumed UTC.

    Anything else (None / garbage / empty) -> None, which the caller treats as
    "no usable expires_at", i.e. NOT eligible for release -- fail-safe.
    """
    # 2. Low-level DynamoDB attribute map {"N": "<digits>"} (raw client shape).
    if isinstance(value, dict):
        n = value.get("N")
        if n is not None:
            return _parse_expires_at(n)
        return None

    # 1. Native numeric epoch-seconds (the canonical resource/_from_ddb shape).
    #    bool is an int subclass -- reject it explicitly (a boolean is garbage).
    if isinstance(value, bool):
        return None
    if isinstance(value, (int, float, Decimal)):
        try:
            return datetime.fromtimestamp(float(value), tz=timezone.utc)
        except (ValueError, OverflowError, OSError):
            return None

    if not isinstance(value, str) or not value.strip():
        return None
    raw = value.strip()

    # 3. Digit / float string -> epoch-seconds.
    try:
        return datetime.fromtimestamp(float(raw), tz=timezone.utc)
    except (ValueError, OverflowError, OSError):
        pass

    # 4. ISO-8601 string (tolerant fallback for legacy / sessions-style writes).
    if raw.endswith("Z"):
        raw = raw[:-1] + "+00:00"
    try:
        dt = datetime.fromisoformat(raw)
    except ValueError:
        return None
    if dt.tzinfo is None:
        dt = dt.replace(tzinfo=timezone.utc)
    return dt.astimezone(timezone.utc)


def _from_ddb(value: Any) -> Any:
    """Coerce a boto3 DynamoDB-resource value to JSON-shaped form (Decimal ->
    int/float, set -> list, recursing) so the decision dict serializes.

    Mirrors dynamo_backend._from_ddb / case_list._from_ddb.
    """
    if isinstance(value, Decimal):
        if value == value.to_integral_value():
            return int(value)
        return float(value)
    if isinstance(value, dict):
        return {k: _from_ddb(v) for k, v in value.items()}
    if isinstance(value, (list, tuple)):
        return [_from_ddb(v) for v in value]
    if isinstance(value, (set, frozenset)):
        return [_from_ddb(v) for v in value]
    return value


def _case_owner(item: dict) -> str | None:
    """The Case's owner uid: ``user_id`` first, ``owner_user_id`` fallback
    (mirrors the agent's ``$or:[{user_id},{owner_user_id}]`` owner scoping)."""
    for key in ("user_id", "owner_user_id"):
        v = item.get(key)
        if isinstance(v, str) and v:
            return v
    return None


# --------------------------------------------------------------------------- #
# Selection.
# --------------------------------------------------------------------------- #


def _is_expired_anon(item: dict, now: datetime) -> bool:
    """True iff this Case is an EXPIRED anon Case eligible for release.

    Every clause must hold (S1-S4 in the module docstring). Any failure ->
    False (leave the Case ALONE). This is the single safety chokepoint.
    """
    # S1: expires_at MUST be present + parseable. Authed/legacy Cases carry no
    # expires_at and are skipped here (the most important guard). The live shape
    # is a NUMERIC epoch (DynamoDB-native TTL); _parse_expires_at handles that
    # primary path plus the ISO-string fallback.
    expires_at = _parse_expires_at(item.get("expires_at"))
    if expires_at is None:
        return False
    # S2: the heartbeat horizon must have LAPSED. A live-kept-warm Case has a
    # future expires_at and is skipped.
    if expires_at >= now:
        return False
    # S3: never sweep the pre-Auth migration sentinel owner (legacy authed).
    if _case_owner(item) == MIGRATION_ANON_UID:
        return False
    # S4: already a tombstone -> already released, skip.
    if item.get("status") in _TOMBSTONE_STATUSES:
        return False
    return True


def _scan_expired(table, now: datetime) -> list[dict]:
    """Scan the cases table and return the EXPIRED-ANON items only.

    A full Scan is acceptable: the sweep is infrequent and the eligible set is
    the anon-ephemeral tail. Pagination is honored. Server-side FilterExpression
    is NOT used (the expires_at < now comparison + the owner-fallback + the
    present-attribute test are clearer + safer evaluated client-side via the
    single ``_is_expired_anon`` chokepoint), but the projection is left full so
    the tombstone write + side-effect reaps have the keys they need.

    On a scan error the partial set gathered so far is returned (the next tick
    retries the remainder) -- the sweep never raises out of the scan.
    """
    expired: list[dict] = []
    kwargs: dict[str, Any] = {}
    try:
        while True:
            resp = table.scan(**kwargs)
            for raw in resp.get("Items", []):
                item = _from_ddb(raw)
                if _is_expired_anon(item, now):
                    expired.append(item)
            lek = resp.get("LastEvaluatedKey")
            if not lek:
                break
            kwargs["ExclusiveStartKey"] = lek
    except Exception:  # noqa: BLE001 -- degrade to the partial set, never raise
        logger.exception("cases scan failed; sweeping the partial set gathered")
    return expired


# --------------------------------------------------------------------------- #
# Soft-delete (the SAME tombstone write Persistence.delete_case performs).
# --------------------------------------------------------------------------- #


def _soft_delete(table, case_id: str, now: datetime) -> None:
    """Tombstone one Case: ``status="deleted"`` + ``deleted_at=<now ISO-Z>``.

    Byte-for-byte the agent's ``Persistence.delete_case`` ``$set`` (soft-delete
    only; NEVER a hard delete). Raises on failure so the caller counts it as a
    per-item error (the Case is NOT counted released).
    """
    table.update_item(
        Key={"_id": case_id},
        UpdateExpression="SET #s = :deleted, deleted_at = :ts",
        ExpressionAttributeNames={"#s": "status"},
        ExpressionAttributeValues={
            ":deleted": "deleted",
            ":ts": _iso_z(now),
        },
    )


# --------------------------------------------------------------------------- #
# Best-effort side-effect reaps. EACH guarded -- a failure NEVER aborts the
# sweep (it is logged + recorded as an error and the sweep continues).
# --------------------------------------------------------------------------- #


def _reap_chat(case_id: str) -> None:
    """Delete the ``grace2_chat`` rows for a released Case (PK ``case_id`` + SK
    ``message_id``). Query by partition key, batch-delete the survivors.

    Best-effort: any failure is allowed to propagate to the caller's per-reap
    try/except (recorded as an error, sweep continues).
    """
    from boto3.dynamodb.conditions import Key

    table = _ddb.Table(CHAT_TABLE)
    keys: list[dict] = []
    kwargs: dict[str, Any] = {
        "KeyConditionExpression": Key("case_id").eq(case_id),
        "ProjectionExpression": "case_id, message_id",
    }
    while True:
        resp = table.query(**kwargs)
        for it in resp.get("Items", []):
            mid = it.get("message_id")
            if mid is not None:
                keys.append({"case_id": case_id, "message_id": mid})
        lek = resp.get("LastEvaluatedKey")
        if not lek:
            break
        kwargs["ExclusiveStartKey"] = lek

    if not keys:
        return
    with table.batch_writer() as batch:
        for key in keys:
            batch.delete_item(Key=key)


def _reap_snapshot(case_id: str) -> None:
    """Delete the S3 cold-view snapshot ``case-views/{case_id}.json``.

    Best-effort: a missing object (delete_object is idempotent) is fine; any
    other failure propagates to the per-reap try/except.
    """
    if not RUNS_BUCKET:
        return
    key = f"{VIEW_PREFIX}/{case_id}.json"
    _s3.delete_object(Bucket=RUNS_BUCKET, Key=key)


def _reap_qgs(case_id: str) -> None:
    """Delete the per-Case ``.qgs`` object ``{case_id}.qgs`` from the QGS bucket.

    Only runs when a QGS bucket is configured (GCP is being decommissioned, so
    this side-effect may not exist on AWS yet). Best-effort.
    """
    if not QGS_BUCKET:
        return
    _s3.delete_object(Bucket=QGS_BUCKET, Key=f"{case_id}.qgs")


_REAPS = (
    ("chat", _reap_chat),
    ("snapshot", _reap_snapshot),
    ("qgs", _reap_qgs),
)


def _reap_side_effects(case_id: str) -> list[str]:
    """Run every side-effect reap for a released Case, each independently
    guarded. Returns a list of human-readable error strings (empty == clean).

    A reap failure NEVER aborts the sweep -- it is logged + collected. The chat
    reap and the snapshot reap are independent: one failing must not skip the
    other.
    """
    errors: list[str] = []
    for name, fn in _REAPS:
        try:
            fn(case_id)
        except Exception as exc:  # noqa: BLE001 -- best-effort; record + continue
            logger.exception("reap %s failed for case %s", name, case_id)
            errors.append(f"{case_id}:{name}:{type(exc).__name__}")
    return errors


# --------------------------------------------------------------------------- #
# Handler.
# --------------------------------------------------------------------------- #


def handler(event, context):  # noqa: ANN001, ARG001
    """EventBridge-scheduled anon-ephemeral Case sweep.

    Returns a JSON-serializable decision::

        {"scanned": int, "released": int, "errors": [str, ...], "dry_run": bool}

    ``scanned`` is the count of EXPIRED-ANON Cases selected this tick;
    ``released`` is how many were actually tombstoned (== scanned in a clean
    run; 0 under DRY_RUN); ``errors`` collects per-item / per-reap failures
    (each logged + skipped). The handler NEVER raises.
    """
    now = _now()

    if not STATE_TABLE:
        decision = {"scanned": 0, "released": 0, "errors": [], "dry_run": DRY_RUN,
                    "reason": "STATE_TABLE unset"}
        logger.info("case-sweep: %s", decision)
        return decision

    table = _ddb.Table(STATE_TABLE)
    expired = _scan_expired(table, now)

    errors: list[str] = []
    released = 0

    for item in expired:
        case_id = item.get("_id")
        if not isinstance(case_id, str) or not case_id:
            errors.append("<missing-_id>:select")
            continue

        if DRY_RUN:
            logger.info(
                "case-sweep DRY_RUN: would release case=%s owner=%s expires_at=%s",
                case_id, _case_owner(item), item.get("expires_at"),
            )
            # Count it as "scanned" but NOT released; mutate nothing (no
            # tombstone, no reap) -- mirrors idle_check's DRY_RUN.
            continue

        # 1. Soft-delete (tombstone). On failure the Case is NOT counted
        #    released and its side-effects are NOT reaped (a Case we could not
        #    tombstone is still live; reaping its chat/snapshot would orphan it).
        try:
            _soft_delete(table, case_id, now)
        except Exception as exc:  # noqa: BLE001 -- per-item; log + skip, never raise
            logger.exception("soft-delete failed for case %s; skipping", case_id)
            errors.append(f"{case_id}:soft_delete:{type(exc).__name__}")
            continue

        released += 1

        # 2. Best-effort side-effect reaps (each independently guarded). A reap
        #    failure does NOT un-count the release (the Case IS tombstoned) and
        #    does NOT stop the sweep.
        errors.extend(_reap_side_effects(case_id))

    decision = {
        "scanned": len(expired),
        "released": released,
        "errors": errors,
        "dry_run": DRY_RUN,
    }
    logger.info("case-sweep: %s", decision)
    return decision
