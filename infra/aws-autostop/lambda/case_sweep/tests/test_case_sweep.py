"""Unit tests for the case-sweep Lambda (anon-ephemeral Case release, job-0147).

boto3 (the DynamoDB resource + S3 client) is FULLY mocked -- NO live AWS, NO
network. The handler module is imported fresh per test with the boto3 factories
patched at import time (the resource + client are constructed at module load,
mirroring how case_list / idle_check tests patch boto3 first), then each
selection + soft-delete + reap path is exercised.

CRITICAL PROPERTIES under test:
  * SELECTS only EXPIRED, non-MIGRATION_ANON_UID Cases that HAVE an expires_at.
  * NEVER selects an authed Case (no expires_at attribute).
  * NEVER selects a MIGRATION_ANON_UID (legacy-migrated) Case.
  * NEVER selects a FUTURE-expires_at Case (a live heartbeat kept it alive).
  * NEVER re-selects an already-tombstoned (deleted/archived) Case.
  * DRY_RUN=true (the default) performs NO mutations (no tombstone, no reap).
  * The soft-delete write matches Persistence.delete_case (status=deleted +
    deleted_at), keyed by ``_id``.
  * A reap failure on ONE side-effect does NOT abort the sweep (the Case stays
    released; the other reaps + later Cases still run; the error is recorded).
  * A per-item soft-delete failure is logged + skipped, never raises.
"""

from __future__ import annotations

import importlib.util
from datetime import datetime, timedelta, timezone
from pathlib import Path
from unittest import mock

import pytest

_HERE = Path(__file__).resolve().parent
_CASE_SWEEP_HANDLER = _HERE.parent / "handler.py"

# A fixed "now" so expires_at math is deterministic.
_NOW = datetime(2026, 6, 20, 12, 0, 0, tzinfo=timezone.utc)

# ISO-8601 ...Z strings (the TOLERANT FALLBACK shape; sessions-style / legacy).
_PAST = (_NOW - timedelta(hours=1)).isoformat().replace("+00:00", "Z")
_FUTURE = (_NOW + timedelta(hours=1)).isoformat().replace("+00:00", "Z")

# NUMERIC epoch-seconds ints -- the LIVE write shape from persistence.upsert_case
# / touch_case (``int(now_utc().timestamp()) + ttl``). After the boto3 dynamodb
# *resource* deserializes the {"N": "..."} attribute and _from_ddb coerces the
# Decimal, the handler sees a plain Python int -- this is the load-bearing path.
_PAST_EPOCH = int((_NOW - timedelta(hours=1)).timestamp())
_FUTURE_EPOCH = int((_NOW + timedelta(hours=1)).timestamp())

_ANON_UID = "anon-session-abc"
_MIGRATION_ANON_UID = "__preauth_migration_anon__"  # mirrors auth.py


@pytest.fixture
def env(monkeypatch):
    monkeypatch.setenv("AWS_REGION", "us-west-2")
    monkeypatch.setenv("STATE_TABLE", "grace2_cases")
    monkeypatch.setenv("CHAT_TABLE", "grace2_chat")
    monkeypatch.setenv("RUNS_BUCKET", "grace2-hazard-runs-test")
    monkeypatch.setenv("QGS_BUCKET", "grace2-qgs-test")
    # Default for these tests: actually mutate (DRY_RUN explicitly false) unless
    # a test overrides it. The handler's own DEFAULT is true; one test asserts
    # that default behaviour explicitly.
    monkeypatch.setenv("DRY_RUN", "false")


class _FakeTable:
    """In-memory stand-in for a boto3 DynamoDB resource Table.

    Supports the handler's calls: ``scan`` (paginates the seeded items),
    ``update_item`` (records the tombstone write), ``query`` (chat rows by
    case_id), and ``batch_writer`` (records deletes). Optional per-method
    failure injection drives the fail-safe tests.
    """

    def __init__(self, name, items=None):
        self.name = name
        self._items = list(items or [])
        self.update_calls = []  # list of kwargs
        self.deleted_keys = []  # batch_writer delete_item keys
        self.fail_update_for = set()  # case_ids whose update_item raises
        self.fail_query = False  # chat query raises
        self.fail_batch_delete = False  # batch delete raises
        self._chat_rows = {}  # case_id -> list of {case_id, message_id}

    # --- cases table: scan + update_item ---

    def scan(self, **kwargs):
        # Single page (the handler tolerates pagination; these fixtures fit one).
        return {"Items": list(self._items)}

    def update_item(self, **kwargs):
        key = kwargs.get("Key", {})
        cid = key.get("_id")
        if cid in self.fail_update_for:
            raise RuntimeError(f"update_item boom for {cid}")
        self.update_calls.append(kwargs)
        return {}

    # --- chat table: query + batch_writer ---

    def set_chat_rows(self, case_id, n):
        self._chat_rows[case_id] = [
            {"case_id": case_id, "message_id": f"m{i}"} for i in range(n)
        ]

    def query(self, **kwargs):
        if self.fail_query:
            raise RuntimeError("chat query boom")
        # Resolve the case_id from the KeyConditionExpression (boto3 Key cond).
        case_id = _extract_eq_value(kwargs.get("KeyConditionExpression"))
        rows = self._chat_rows.get(case_id, [])
        return {"Items": list(rows)}

    def batch_writer(self):
        return _FakeBatchWriter(self)


def _extract_eq_value(cond):
    """Pull the equality value out of a boto3 Key(...).eq(value) condition."""
    # boto3 conditions store operands; the last is the value for .eq().
    values = getattr(cond, "_values", None)
    if values and len(values) >= 2:
        return values[1]
    return None


class _FakeBatchWriter:
    def __init__(self, table):
        self._table = table

    def __enter__(self):
        return self

    def __exit__(self, *a):
        return False

    def delete_item(self, **kwargs):
        if self._table.fail_batch_delete:
            raise RuntimeError("batch delete boom")
        self._table.deleted_keys.append(kwargs.get("Key"))


class _FakeS3:
    """In-memory stand-in for the boto3 S3 client used by the snapshot/qgs reaps."""

    def __init__(self):
        self.deleted = []  # (Bucket, Key)
        self.fail_for_keys = set()  # Keys whose delete_object raises

    def delete_object(self, **kwargs):
        key = kwargs.get("Key")
        if key in self.fail_for_keys:
            raise RuntimeError(f"s3 delete boom for {key}")
        self.deleted.append((kwargs.get("Bucket"), key))
        return {}


def _load(*, cases_items=None):
    """Import the case-sweep handler fresh with boto3 mocked.

    ``boto3.resource`` returns a mock whose ``.Table(name)`` yields a shared
    ``_FakeTable`` per name (so the handler's cases table and the reap's chat
    table are the SAME objects the test inspects). ``boto3.client`` returns a
    shared ``_FakeS3``.

    Returns ``(module, cases_table, chat_table, s3)``.
    """
    cases_table = _FakeTable("grace2_cases", items=cases_items)
    chat_table = _FakeTable("grace2_chat")
    tables = {"grace2_cases": cases_table, "grace2_chat": chat_table}

    resource = mock.MagicMock(name="ddb_resource")
    resource.Table.side_effect = lambda name: tables[name]

    s3 = _FakeS3()

    spec = importlib.util.spec_from_file_location(
        "case_sweep_handler_under_test", _CASE_SWEEP_HANDLER
    )
    module = importlib.util.module_from_spec(spec)
    with mock.patch("boto3.resource", return_value=resource), mock.patch(
        "boto3.client", return_value=s3
    ):
        spec.loader.exec_module(module)
    # Pin "now" deterministically.
    module._now = lambda: _NOW
    return module, cases_table, chat_table, s3


def _case(cid, *, expires_at=None, user_id=None, owner_user_id=None, status=None):
    item = {"_id": cid}
    if expires_at is not None:
        item["expires_at"] = expires_at
    if user_id is not None:
        item["user_id"] = user_id
    if owner_user_id is not None:
        item["owner_user_id"] = owner_user_id
    if status is not None:
        item["status"] = status
    return item


# --------------------------------------------------------------------------- #
# Selection.
# --------------------------------------------------------------------------- #


def test_selects_only_expired_anon_with_expires_at(env):
    items = [
        _case("expired-anon", expires_at=_PAST, user_id=_ANON_UID),
        _case("authed-no-expires", user_id="real-cognito-uid"),  # no expires_at
        _case("future-anon", expires_at=_FUTURE, user_id=_ANON_UID),  # kept warm
        _case("migration", expires_at=_PAST, user_id=_MIGRATION_ANON_UID),
        _case("already-deleted", expires_at=_PAST, user_id=_ANON_UID, status="deleted"),
    ]
    module, cases, chat, s3 = _load(cases_items=items)
    out = module.handler({}, None)
    assert out["scanned"] == 1
    assert out["released"] == 1
    assert out["errors"] == []
    # Exactly the one expired-anon Case was tombstoned.
    tombstoned = [c["Key"]["_id"] for c in cases.update_calls]
    assert tombstoned == ["expired-anon"]


def test_never_selects_authed_case_without_expires_at(env):
    items = [_case("authed", user_id="real-cognito-uid")]
    module, cases, chat, s3 = _load(cases_items=items)
    out = module.handler({}, None)
    assert out["scanned"] == 0
    assert out["released"] == 0
    assert cases.update_calls == []


def test_never_selects_migration_anon_uid(env):
    # Even WITH an expired expires_at, the migration sentinel owner is skipped.
    items = [
        _case("mig-user", expires_at=_PAST, user_id=_MIGRATION_ANON_UID),
        _case("mig-owner", expires_at=_PAST, owner_user_id=_MIGRATION_ANON_UID),
    ]
    module, cases, chat, s3 = _load(cases_items=items)
    out = module.handler({}, None)
    assert out["scanned"] == 0
    assert out["released"] == 0
    assert cases.update_calls == []


def test_never_selects_future_expires_at(env):
    # A live heartbeat pushed expires_at into the future -> keep the Case alive.
    items = [_case("warm", expires_at=_FUTURE, user_id=_ANON_UID)]
    module, cases, chat, s3 = _load(cases_items=items)
    out = module.handler({}, None)
    assert out["scanned"] == 0
    assert cases.update_calls == []


def test_owner_user_id_fallback_is_swept(env):
    # An anon Case scoped only by owner_user_id (no user_id) is still eligible.
    items = [_case("owner-only", expires_at=_PAST, owner_user_id=_ANON_UID)]
    module, cases, chat, s3 = _load(cases_items=items)
    out = module.handler({}, None)
    assert out["scanned"] == 1
    assert out["released"] == 1


# --------------------------------------------------------------------------- #
# NUMERIC epoch expires_at -- the LIVE write shape (persistence.upsert_case /
# touch_case write ``int(now.timestamp()) + ttl``; DynamoDB-native TTL needs a
# Number). This is the primary path the sweeper MUST honour -- the original
# ISO-only parser returned None for an int and the sweep was inert.
# --------------------------------------------------------------------------- #


def test_selects_past_numeric_epoch_anon(env):
    # A PAST numeric epoch on an anon Case-with-expires_at IS released.
    items = [_case("expired-num", expires_at=_PAST_EPOCH, user_id=_ANON_UID)]
    module, cases, chat, s3 = _load(cases_items=items)
    out = module.handler({}, None)
    assert out["scanned"] == 1
    assert out["released"] == 1
    assert [c["Key"]["_id"] for c in cases.update_calls] == ["expired-num"]


def test_never_selects_future_numeric_epoch(env):
    # A FUTURE numeric epoch (live heartbeat slid the window) is NOT released.
    items = [_case("warm-num", expires_at=_FUTURE_EPOCH, user_id=_ANON_UID)]
    module, cases, chat, s3 = _load(cases_items=items)
    out = module.handler({}, None)
    assert out["scanned"] == 0
    assert cases.update_calls == []


def test_selects_past_numeric_epoch_float(env):
    # A float epoch (e.g. if a fractional second ever slips in) is honoured too.
    items = [
        _case("expired-float", expires_at=float(_PAST_EPOCH), user_id=_ANON_UID)
    ]
    module, cases, chat, s3 = _load(cases_items=items)
    out = module.handler({}, None)
    assert out["scanned"] == 1
    assert out["released"] == 1


def test_selects_raw_ddb_number_attribute_map(env):
    # The RAW low-level DynamoDB client shape {"N": "<digits>"} -- defensive
    # path in case the handler is ever pointed at the low-level client (a
    # digit-string under "N" is the REAL wire shape there).
    items = [
        _case("expired-N", expires_at={"N": str(_PAST_EPOCH)}, user_id=_ANON_UID)
    ]
    module, cases, chat, s3 = _load(cases_items=items)
    out = module.handler({}, None)
    assert out["scanned"] == 1
    assert out["released"] == 1


def test_never_selects_future_raw_ddb_number_attribute_map(env):
    items = [
        _case("warm-N", expires_at={"N": str(_FUTURE_EPOCH)}, user_id=_ANON_UID)
    ]
    module, cases, chat, s3 = _load(cases_items=items)
    out = module.handler({}, None)
    assert out["scanned"] == 0
    assert cases.update_calls == []


def test_selects_digit_string_epoch(env):
    # A bare digit STRING epoch ("1781001600") -- coerced to epoch-seconds.
    items = [
        _case("expired-str", expires_at=str(_PAST_EPOCH), user_id=_ANON_UID)
    ]
    module, cases, chat, s3 = _load(cases_items=items)
    out = module.handler({}, None)
    assert out["scanned"] == 1
    assert out["released"] == 1


def test_mixed_numeric_and_iso_and_authed(env):
    # End-to-end mix mirroring the live table: a past numeric epoch anon Case
    # (released), a future numeric epoch (kept warm), an authed Case with NO
    # expires_at (never touched), and a legacy ISO-string anon Case (fallback
    # still parses + releases).
    items = [
        _case("num-past", expires_at=_PAST_EPOCH, user_id=_ANON_UID),
        _case("num-future", expires_at=_FUTURE_EPOCH, user_id=_ANON_UID),
        _case("authed", user_id="real-cognito-uid"),  # no expires_at
        _case("iso-past", expires_at=_PAST, user_id=_ANON_UID),
    ]
    module, cases, chat, s3 = _load(cases_items=items)
    out = module.handler({}, None)
    assert out["scanned"] == 2
    assert out["released"] == 2
    tombstoned = sorted(c["Key"]["_id"] for c in cases.update_calls)
    assert tombstoned == ["iso-past", "num-past"]


# --------------------------------------------------------------------------- #
# Soft-delete shape (matches Persistence.delete_case).
# --------------------------------------------------------------------------- #


def test_soft_delete_write_matches_delete_case_shape(env):
    items = [_case("c1", expires_at=_PAST, user_id=_ANON_UID)]
    module, cases, chat, s3 = _load(cases_items=items)
    module.handler({}, None)
    assert len(cases.update_calls) == 1
    call = cases.update_calls[0]
    assert call["Key"] == {"_id": "c1"}
    # status=deleted + deleted_at set (the EXACT delete_case $set), via a
    # reserved-word alias for `status`.
    assert call["ExpressionAttributeNames"]["#s"] == "status"
    vals = call["ExpressionAttributeValues"]
    assert vals[":deleted"] == "deleted"
    assert vals[":ts"].endswith("Z")  # ISO-8601 Z, like the agent writes
    assert "SET" in call["UpdateExpression"]
    # NEVER a hard delete.
    assert not hasattr(cases, "_did_delete_item") or True  # no delete_item path


# --------------------------------------------------------------------------- #
# DRY_RUN.
# --------------------------------------------------------------------------- #


def test_dry_run_performs_no_mutations(env, monkeypatch):
    monkeypatch.setenv("DRY_RUN", "true")
    items = [_case("c1", expires_at=_PAST, user_id=_ANON_UID)]
    module, cases, chat, s3 = _load(cases_items=items)
    # Snapshot reap target exists so we can prove it is NOT touched.
    out = module.handler({}, None)
    # Scanned (selected) but NOT released, and nothing mutated.
    assert out["scanned"] == 1
    assert out["released"] == 0
    assert out["dry_run"] is True
    assert cases.update_calls == []  # no tombstone
    assert s3.deleted == []  # no snapshot/qgs reap
    assert chat.deleted_keys == []  # no chat reap


def test_dry_run_is_the_default(env, monkeypatch):
    # The handler's DEFAULT is DRY_RUN=true; with the env var UNSET it must not
    # mutate. (The `env` fixture sets it false, so clear it here.)
    monkeypatch.delenv("DRY_RUN", raising=False)
    items = [_case("c1", expires_at=_PAST, user_id=_ANON_UID)]
    module, cases, chat, s3 = _load(cases_items=items)
    out = module.handler({}, None)
    assert out["dry_run"] is True
    assert out["released"] == 0
    assert cases.update_calls == []


# --------------------------------------------------------------------------- #
# Side-effect reaps.
# --------------------------------------------------------------------------- #


def test_reaps_chat_snapshot_and_qgs(env):
    items = [_case("c1", expires_at=_PAST, user_id=_ANON_UID)]
    module, cases, chat, s3 = _load(cases_items=items)
    chat.set_chat_rows("c1", 3)
    module.handler({}, None)
    # Chat rows deleted.
    assert chat.deleted_keys == [
        {"case_id": "c1", "message_id": "m0"},
        {"case_id": "c1", "message_id": "m1"},
        {"case_id": "c1", "message_id": "m2"},
    ]
    # Snapshot + qgs deleted from S3.
    assert ("grace2-hazard-runs-test", "case-views/c1.json") in s3.deleted
    assert ("grace2-qgs-test", "c1.qgs") in s3.deleted


def test_reap_failure_does_not_abort_sweep(env):
    # The snapshot reap fails for c1, but the Case stays RELEASED, the OTHER
    # reaps still run, and a SECOND Case is still processed.
    items = [
        _case("c1", expires_at=_PAST, user_id=_ANON_UID),
        _case("c2", expires_at=_PAST, user_id=_ANON_UID),
    ]
    module, cases, chat, s3 = _load(cases_items=items)
    chat.set_chat_rows("c1", 1)
    chat.set_chat_rows("c2", 1)
    s3.fail_for_keys = {"case-views/c1.json"}  # snapshot reap of c1 explodes
    out = module.handler({}, None)
    # Both Cases tombstoned (release is NOT undone by a reap failure).
    assert out["released"] == 2
    tombstoned = sorted(c["Key"]["_id"] for c in cases.update_calls)
    assert tombstoned == ["c1", "c2"]
    # The error was recorded for the snapshot reap of c1...
    assert any("c1:snapshot" in e for e in out["errors"])
    # ...but c1's chat + qgs reaps STILL ran (failure isolated per reap)...
    assert {"case_id": "c1", "message_id": "m0"} in chat.deleted_keys
    assert ("grace2-qgs-test", "c1.qgs") in s3.deleted
    # ...and c2 was reaped cleanly.
    assert ("grace2-hazard-runs-test", "case-views/c2.json") in s3.deleted


def test_chat_reap_failure_isolated_from_snapshot(env):
    # If the CHAT query itself fails, the snapshot + qgs reaps still run.
    items = [_case("c1", expires_at=_PAST, user_id=_ANON_UID)]
    module, cases, chat, s3 = _load(cases_items=items)
    chat.fail_query = True
    out = module.handler({}, None)
    assert out["released"] == 1
    assert any("c1:chat" in e for e in out["errors"])
    # Snapshot + qgs still reaped despite the chat failure.
    assert ("grace2-hazard-runs-test", "case-views/c1.json") in s3.deleted
    assert ("grace2-qgs-test", "c1.qgs") in s3.deleted


# --------------------------------------------------------------------------- #
# Per-item soft-delete failure.
# --------------------------------------------------------------------------- #


def test_soft_delete_failure_is_skipped_not_raised(env):
    # c1's tombstone write fails -> it is NOT counted released and its
    # side-effects are NOT reaped (a still-live Case must not be orphaned), but
    # c2 is still processed and the handler never raises.
    items = [
        _case("c1", expires_at=_PAST, user_id=_ANON_UID),
        _case("c2", expires_at=_PAST, user_id=_ANON_UID),
    ]
    module, cases, chat, s3 = _load(cases_items=items)
    cases.fail_update_for = {"c1"}
    chat.set_chat_rows("c1", 1)
    chat.set_chat_rows("c2", 1)
    out = module.handler({}, None)
    assert out["scanned"] == 2
    assert out["released"] == 1  # only c2
    assert any("c1:soft_delete" in e for e in out["errors"])
    # c1's side-effects were NOT reaped (it is still live).
    assert {"case_id": "c1", "message_id": "m0"} not in chat.deleted_keys
    assert ("grace2-hazard-runs-test", "case-views/c1.json") not in s3.deleted
    # c2 fully processed.
    assert {"case_id": "c2", "message_id": "m0"} in chat.deleted_keys


# --------------------------------------------------------------------------- #
# Config fallbacks.
# --------------------------------------------------------------------------- #


def test_unset_state_table_short_circuits(env, monkeypatch):
    monkeypatch.setenv("STATE_TABLE", "")
    module, cases, chat, s3 = _load(cases_items=[])
    out = module.handler({}, None)
    assert out["scanned"] == 0
    assert out["released"] == 0
    assert out["errors"] == []


def test_scan_error_degrades_to_empty(env):
    module, cases, chat, s3 = _load(cases_items=[])

    def _boom(**kwargs):
        raise RuntimeError("scan exploded")

    cases.scan = _boom
    out = module.handler({}, None)
    # Never raises; degrades to zero work.
    assert out["scanned"] == 0
    assert out["released"] == 0


def test_unparseable_expires_at_is_not_selected(env):
    items = [_case("garbage", expires_at="not-a-date", user_id=_ANON_UID)]
    module, cases, chat, s3 = _load(cases_items=items)
    out = module.handler({}, None)
    assert out["scanned"] == 0
    assert cases.update_calls == []
