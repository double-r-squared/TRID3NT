"""Unit tests for the broker connection decision (identity -> ULID -> route).

Covers _extract_identity (query + subprotocol) and decide_route's gating:
missing session_id -> reject; bad token -> reject; unknown sub -> reject; the
happy path -> resolve_or_provision is reached with the resolved ULID.

All AWS + the verifier are injected/mocked.
"""

from __future__ import annotations

import sys
from pathlib import Path

import pytest

_BROKER_PARENT = str(Path(__file__).resolve().parents[2])
if _BROKER_PARENT not in sys.path:
    sys.path.insert(0, _BROKER_PARENT)

from broker.app import _extract_identity, decide_route  # noqa: E402
from broker.routing import RoutingConfig, build_user_item, provision_user  # noqa: E402
from broker.tests.test_routing import FakeDDB, FakeECS, FakeTable  # noqa: E402


# --------------------------------------------------------------------------- #
# Fakes for the first-connect provisioning path.
# --------------------------------------------------------------------------- #
class StatefulUsersTable:
    """A users table whose GSI query reflects rows written by put_item, so the
    first-connect provision -> re-resolve flow (and the dual-socket convergence)
    behaves like real DynamoDB: the second socket's re-read HITs the first's row."""

    def __init__(self):
        self.rows: list[dict] = []
        self.put_items: list[dict] = []

    def query(self, **kwargs):
        return {"Items": list(self.rows)}

    def get_item(self, **kwargs):  # not used for the users table here
        return {}

    def put_item(self, **kwargs):
        item = kwargs.get("Item")
        self.put_items.append(item)
        # Mirror the new row into the GSI view (ConditionExpression kwarg ignored).
        self.rows.append({"_id": item["_id"], "firebase_uid": item["firebase_uid"]})


class RaceUsersTable(FakeTable):
    """Simulates a concurrent create: put_item fails (conditional-check / a second
    broker won) but the GSI query already resolves the WINNER's ULID -> the loser
    must reuse it (no forked identity)."""

    def __init__(self, *, winner_ulid: str, sub: str):
        super().__init__(query_result={"Items": [{"_id": winner_ulid, "firebase_uid": sub}]})
        self.put_attempts = 0

    def put_item(self, **kwargs):
        self.put_attempts += 1
        raise RuntimeError("ConditionalCheckFailedException")


def _live_route_item() -> dict:
    return {
        "Item": {
            "user_ulid": "ignored-by-resolve_route",
            "session_id": "S1",
            "task_arn": "arn:task/live",
            "private_ip": "10.0.1.2",
            "port": 8765,
        }
    }


def _cfg() -> RoutingConfig:
    return RoutingConfig(
        routes_table="grace2_session_routes",
        users_table="grace2_users",
        users_firebase_uid_index="firebase_uid-index",
        ecs_cluster="grace2-agents",
        agent_task_definition="grace2-agent-session",
        agent_container_name="agent",
        agent_ws_port=8765,
        agent_health_port=8766,
        task_subnets=["subnet-a"],
        task_security_groups=["sg-agent"],
        provision_timeout_s=1.0,
        provision_poll_interval_s=0.0,
    )


# --------------------------------------------------------------------------- #
# _extract_identity
# --------------------------------------------------------------------------- #
def test_extract_identity_from_query():
    token, sid = _extract_identity("/ws?st=TOK123&sid=SESS9", subprotocols=None)
    assert token == "TOK123"
    assert sid == "SESS9"


def test_extract_identity_from_subprotocol_overrides_query():
    token, sid = _extract_identity(
        "/ws?st=Q&sid=Q",
        subprotocols=["grace2.session.SUB_SID", "base64UrlBearerAuthorization.SUB_TOK"],
    )
    assert token == "SUB_TOK"
    assert sid == "SUB_SID"


def test_extract_identity_missing():
    token, sid = _extract_identity("/ws", subprotocols=None)
    assert token is None
    assert sid is None


# --------------------------------------------------------------------------- #
# decide_route gating
# --------------------------------------------------------------------------- #
def test_decide_route_rejects_without_session_id():
    ddb = FakeDDB({})
    ecs = FakeECS()
    r = decide_route(
        ddb, ecs, _cfg(),
        request_uri="/ws?st=TOK",  # no sid
        subprotocols=None,
        health_probe=lambda ip, p: True,
        verify=lambda t: {"uid": "sub-abc"},
    )
    assert r is None


def test_decide_route_rejects_bad_token():
    ddb = FakeDDB({})
    ecs = FakeECS()
    r = decide_route(
        ddb, ecs, _cfg(),
        request_uri="/ws?st=BAD&sid=S1",
        subprotocols=None,
        health_probe=lambda ip, p: True,
        verify=lambda t: None,  # verification fails
    )
    assert r is None


def test_decide_route_rejects_when_provisioning_fails():
    """A verified sub with no ULID is now first-connect-provisioned; the connect is
    rejected ONLY if that provisioning write truly fails (and the sub stays
    unresolved). Here put_item raises and the GSI still resolves nothing -> reject."""
    class FailUsersTable(FakeTable):
        def put_item(self, **kwargs):
            raise RuntimeError("AccessDenied")

    users = FailUsersTable(query_result={"Items": []})  # never resolves
    ddb = FakeDDB({"grace2_users": users})
    ecs = FakeECS()
    r = decide_route(
        ddb, ecs, _cfg(),
        request_uri="/ws?st=TOK&sid=S1",
        subprotocols=None,
        health_probe=lambda ip, p: True,
        verify=lambda t: {"uid": "sub-unknown"},
    )
    assert r is None
    assert ecs.run_task_calls == []  # never reached provisioning a task


def test_decide_route_happy_path_hits_existing_route():
    users = FakeTable(query_result={"Items": [{"_id": "ULID-U1", "firebase_uid": "sub-abc"}]})
    routes = FakeTable(
        get_item_result={
            "Item": {
                "user_ulid": "ULID-U1",
                "session_id": "S1",
                "task_arn": "arn:task/live",
                "private_ip": "10.0.1.2",
                "port": 8765,
            }
        }
    )
    ddb = FakeDDB({"grace2_users": users, "grace2_session_routes": routes})
    ecs = FakeECS()  # must NOT run_task on a HIT

    r = decide_route(
        ddb, ecs, _cfg(),
        request_uri="/ws?st=TOK&sid=S1",
        subprotocols=None,
        health_probe=lambda ip, p: True,
        verify=lambda t: {"uid": "sub-abc"},
    )
    assert r is not None
    assert r.user_ulid == "ULID-U1"
    assert r.task_arn == "arn:task/live"
    assert ecs.run_task_calls == []  # HIT -> no provision


def test_decide_route_dual_socket_same_session_same_task():
    """Both of a tab's sockets carry the SAME session_id -> the SAME route (the
    convergence the agent depends on)."""
    users = FakeTable(query_result={"Items": [{"_id": "ULID-U1", "firebase_uid": "sub-abc"}]})
    routes = FakeTable(
        get_item_result={
            "Item": {
                "user_ulid": "ULID-U1",
                "session_id": "SHARED",
                "task_arn": "arn:task/shared",
                "private_ip": "10.0.1.7",
                "port": 8765,
            }
        }
    )
    ddb = FakeDDB({"grace2_users": users, "grace2_session_routes": routes})
    ecs = FakeECS()

    args = dict(
        request_uri="/ws?st=TOK&sid=SHARED",
        subprotocols=None,
        health_probe=lambda ip, p: True,
        verify=lambda t: {"uid": "sub-abc"},
    )
    r_app = decide_route(ddb, ecs, _cfg(), **args)
    r_chat = decide_route(ddb, ecs, _cfg(), **args)
    assert r_app.task_arn == r_chat.task_arn == "arn:task/shared"


# --------------------------------------------------------------------------- #
# First-connect provisioning (the core fix)
# --------------------------------------------------------------------------- #
def test_decide_route_first_connect_provisions_user_and_proceeds():
    """A verified sub with NO internal ULID -> mint the users row, then route
    (instead of the old 4401 reject)."""
    users = StatefulUsersTable()
    routes = FakeTable(get_item_result=_live_route_item())
    ddb = FakeDDB({"grace2_users": users, "grace2_session_routes": routes})
    ecs = FakeECS()  # route HIT after provisioning -> no RunTask

    r = decide_route(
        ddb, ecs, _cfg(),
        request_uri="/ws?st=TOK&sid=S1",
        subprotocols=None,
        health_probe=lambda ip, p: True,
        verify=lambda t: {"uid": "sub-new", "email": "demo@example.com", "name": "Demo"},
    )
    assert r is not None
    assert r.task_arn == "arn:task/live"
    # Exactly one users row was minted, with the agent-mirrored shape.
    assert len(users.put_items) == 1
    row = users.put_items[0]
    assert row["firebase_uid"] == "sub-new"
    assert row["_id"] == row["user_id"]
    assert len(row["_id"]) == 26  # ULID
    assert row["schema_version"] == "v1"
    assert row["is_active"] is True
    assert row["is_anonymous"] is False
    assert row["email"] == "demo@example.com"
    assert row["display_name"] == "Demo"
    assert row["prefs"] == {}
    assert "created_at" in row and row["created_at"].endswith("Z")
    assert ecs.run_task_calls == []  # HIT -> no provision


def test_decide_route_dual_socket_new_sub_provisions_once():
    """Both of a tab's sockets carry the SAME brand-new sub -> the users row is
    minted ONCE; the second socket re-reads under the per-sub lock and reuses it
    (no forked identity)."""
    users = StatefulUsersTable()
    routes = FakeTable(get_item_result=_live_route_item())
    ddb = FakeDDB({"grace2_users": users, "grace2_session_routes": routes})
    ecs = FakeECS()

    args = dict(
        request_uri="/ws?st=TOK&sid=S1",
        subprotocols=None,
        health_probe=lambda ip, p: True,
        verify=lambda t: {"uid": "sub-new"},
    )
    r1 = decide_route(ddb, ecs, _cfg(), **args)
    r2 = decide_route(ddb, ecs, _cfg(), **args)
    assert r1 is not None and r2 is not None
    assert len(users.put_items) == 1  # provisioned exactly once
    assert users.rows[0]["_id"] == users.put_items[0]["_id"]


def test_provision_user_race_reuses_winner_row():
    """When the conditional PutItem fails (a concurrent broker won), provision_user
    re-resolves the GSI and adopts the WINNER's ULID rather than forking."""
    users = RaceUsersTable(winner_ulid="ULID-WINNER", sub="sub-new")
    ddb = FakeDDB({"grace2_users": users})
    out = provision_user(ddb, _cfg(), "sub-new", email=None, display_name=None)
    assert out == "ULID-WINNER"
    assert users.put_attempts == 1  # tried to write, lost, reused the winner


def test_provision_user_empty_sub_returns_none():
    ddb = FakeDDB({"grace2_users": StatefulUsersTable()})
    assert provision_user(ddb, _cfg(), "") is None


def test_build_user_item_shape_is_agent_mirrored():
    item = build_user_item("sub-xyz", email="a@b.c", display_name=None)
    assert set(item) == {
        "schema_version", "user_id", "firebase_uid", "email",
        "display_name", "created_at", "is_active", "prefs",
        "is_anonymous", "_id",
    }
    assert item["_id"] == item["user_id"]
    assert item["firebase_uid"] == "sub-xyz"
    assert item["is_anonymous"] is False


if __name__ == "__main__":  # pragma: no cover
    raise SystemExit(pytest.main([__file__, "-q"]))
