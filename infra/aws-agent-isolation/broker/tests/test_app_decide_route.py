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
from broker.routing import RoutingConfig  # noqa: E402
from broker.tests.test_routing import FakeDDB, FakeECS, FakeTable  # noqa: E402


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


def test_decide_route_rejects_unknown_sub():
    users = FakeTable(query_result={"Items": []})  # sub resolves to no ULID
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


if __name__ == "__main__":  # pragma: no cover
    raise SystemExit(pytest.main([__file__, "-q"]))
