"""Unit tests for the broker route-resolve + sub->ULID + provision-on-miss.

All AWS is mocked -- no live calls. Covers the three decisions the kickoff names:
the route-resolve, the sub->ULID, and the provision-on-miss decision.

Run: python -m pytest infra/aws-agent-isolation/broker/tests/test_routing.py
"""

from __future__ import annotations

import sys
from pathlib import Path

import pytest

# Make the broker package importable as ``broker`` (repo-root-relative).
_BROKER_PARENT = str(Path(__file__).resolve().parents[2])
if _BROKER_PARENT not in sys.path:
    sys.path.insert(0, _BROKER_PARENT)

from broker.routing import (  # noqa: E402
    RoutingConfig,
    Route,
    provision_task,
    resolve_or_provision,
    resolve_route,
    resolve_user_ulid,
)


# --------------------------------------------------------------------------- #
# Fakes
# --------------------------------------------------------------------------- #
class FakeTable:
    def __init__(self, *, get_item_result=None, query_result=None):
        self._get_item_result = get_item_result or {}
        self._query_result = query_result or {"Items": []}
        self.put_items: list[dict] = []

    def get_item(self, **kwargs):
        return self._get_item_result

    def query(self, **kwargs):
        return self._query_result

    def put_item(self, **kwargs):
        self.put_items.append(kwargs.get("Item"))


class FakeDDB:
    def __init__(self, tables: dict[str, FakeTable]):
        self._tables = tables

    def Table(self, name):  # noqa: N802 - boto3 surface
        return self._tables[name]


class FakeECS:
    def __init__(self, *, run_task_resp=None, describe_sequence=None):
        self._run_task_resp = run_task_resp
        self._describe_sequence = list(describe_sequence or [])
        self.run_task_calls: list[dict] = []
        self.stopped: list[str] = []

    def run_task(self, **kwargs):
        self.run_task_calls.append(kwargs)
        return self._run_task_resp

    def describe_tasks(self, **kwargs):
        if self._describe_sequence:
            return self._describe_sequence.pop(0)
        return {"tasks": []}

    def stop_task(self, **kwargs):
        self.stopped.append(kwargs.get("task"))


def _cfg(**over) -> RoutingConfig:
    base = dict(
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
        provision_timeout_s=10.0,
        provision_poll_interval_s=0.0,
    )
    base.update(over)
    return RoutingConfig(**base)


# --------------------------------------------------------------------------- #
# sub -> ULID
# --------------------------------------------------------------------------- #
def test_resolve_user_ulid_hit():
    users = FakeTable(query_result={"Items": [{"_id": "ULID123", "firebase_uid": "sub-abc"}]})
    ddb = FakeDDB({"grace2_users": users})
    assert resolve_user_ulid(ddb, _cfg(), "sub-abc") == "ULID123"


def test_resolve_user_ulid_no_record_fails_closed():
    users = FakeTable(query_result={"Items": []})
    ddb = FakeDDB({"grace2_users": users})
    assert resolve_user_ulid(ddb, _cfg(), "sub-missing") is None


def test_resolve_user_ulid_empty_sub():
    ddb = FakeDDB({"grace2_users": FakeTable()})
    assert resolve_user_ulid(ddb, _cfg(), "") is None


def test_resolve_user_ulid_query_error_fails_closed():
    class Boom(FakeTable):
        def query(self, **kwargs):
            raise RuntimeError("ddb down")

    ddb = FakeDDB({"grace2_users": Boom()})
    assert resolve_user_ulid(ddb, _cfg(), "sub-abc") is None


# --------------------------------------------------------------------------- #
# route resolve
# --------------------------------------------------------------------------- #
def test_resolve_route_hit():
    routes = FakeTable(
        get_item_result={
            "Item": {
                "user_ulid": "U1",
                "session_id": "S1",
                "task_arn": "arn:task/abc",
                "private_ip": "10.0.1.5",
                "port": 8765,
                "state": "RUNNING",
            }
        }
    )
    ddb = FakeDDB({"grace2_session_routes": routes})
    r = resolve_route(ddb, _cfg(), "U1", "S1")
    assert r is not None
    assert r.task_arn == "arn:task/abc"
    assert r.private_ip == "10.0.1.5"
    assert r.port == 8765


def test_resolve_route_miss():
    routes = FakeTable(get_item_result={})  # no Item
    ddb = FakeDDB({"grace2_session_routes": routes})
    assert resolve_route(ddb, _cfg(), "U1", "S-missing") is None


def test_resolve_route_incomplete_row_is_miss():
    routes = FakeTable(get_item_result={"Item": {"user_ulid": "U1", "session_id": "S1"}})
    ddb = FakeDDB({"grace2_session_routes": routes})
    assert resolve_route(ddb, _cfg(), "U1", "S1") is None


# --------------------------------------------------------------------------- #
# provision-on-miss decision
# --------------------------------------------------------------------------- #
def test_provision_task_success_writes_route():
    routes = FakeTable()
    ddb = FakeDDB({"grace2_session_routes": routes})
    ecs = FakeECS(
        run_task_resp={"tasks": [{"taskArn": "arn:task/new"}], "failures": []},
        describe_sequence=[
            {
                "tasks": [
                    {
                        "lastStatus": "RUNNING",
                        "attachments": [
                            {
                                "type": "ElasticNetworkInterface",
                                "details": [{"name": "privateIPv4Address", "value": "10.0.2.9"}],
                            }
                        ],
                    }
                ]
            }
        ],
    )

    health_calls: list[tuple] = []

    def health_probe(ip, port):
        health_calls.append((ip, port))
        return True  # green on first probe

    route = provision_task(
        ecs, ddb, _cfg(), "U1", "S1",
        health_probe=health_probe,
        sleep=lambda _s: None,
        now=_fake_clock(),
    )
    assert route is not None
    assert route.task_arn == "arn:task/new"
    assert route.private_ip == "10.0.2.9"
    assert health_calls == [("10.0.2.9", 8766)]
    assert routes.put_items and routes.put_items[0]["task_arn"] == "arn:task/new"
    assert routes.put_items[0]["idle_streak"] == 0


def test_provision_task_run_task_failure_returns_none():
    ddb = FakeDDB({"grace2_session_routes": FakeTable()})
    ecs = FakeECS(run_task_resp={"tasks": [], "failures": [{"reason": "CAPACITY"}]})
    route = provision_task(
        ecs, ddb, _cfg(), "U1", "S1",
        health_probe=lambda ip, port: True,
        sleep=lambda _s: None,
        now=_fake_clock(),
    )
    assert route is None


def test_provision_task_health_never_green_stops_task():
    ddb = FakeDDB({"grace2_session_routes": FakeTable()})
    ecs = FakeECS(
        run_task_resp={"tasks": [{"taskArn": "arn:task/stuck"}]},
        describe_sequence=[
            {
                "tasks": [
                    {
                        "lastStatus": "RUNNING",
                        "attachments": [
                            {
                                "type": "ElasticNetworkInterface",
                                "details": [{"name": "privateIPv4Address", "value": "10.0.2.9"}],
                            }
                        ],
                    }
                ]
            }
        ],
    )
    # now() advances past the deadline so the health loop exits unsatisfied.
    route = provision_task(
        ecs, ddb, _cfg(provision_timeout_s=1.0), "U1", "S1",
        health_probe=lambda ip, port: False,  # never green
        sleep=lambda _s: None,
        now=_fake_clock(step=0.6),
    )
    assert route is None
    assert ecs.stopped == ["arn:task/stuck"]  # cleaned up the stuck task


# --------------------------------------------------------------------------- #
# resolve_or_provision: HIT short-circuits (no RunTask)
# --------------------------------------------------------------------------- #
def test_resolve_or_provision_hit_does_not_run_task():
    routes = FakeTable(
        get_item_result={
            "Item": {
                "user_ulid": "U1",
                "session_id": "S1",
                "task_arn": "arn:task/existing",
                "private_ip": "10.0.1.1",
                "port": 8765,
            }
        }
    )
    ddb = FakeDDB({"grace2_session_routes": routes})
    ecs = FakeECS(run_task_resp={"tasks": [{"taskArn": "SHOULD_NOT_RUN"}]})
    r = resolve_or_provision(ddb, ecs, _cfg(), "U1", "S1", health_probe=lambda ip, p: True)
    assert r is not None
    assert r.task_arn == "arn:task/existing"
    assert ecs.run_task_calls == []  # HIT -> no provision


# --------------------------------------------------------------------------- #
# helpers
# --------------------------------------------------------------------------- #
def _fake_clock(step: float = 0.0):
    """A monotonic clock that advances by ``step`` each call (0 = frozen)."""
    state = {"t": 1000.0}

    def now() -> float:
        t = state["t"]
        state["t"] += step
        return t

    return now


if __name__ == "__main__":  # pragma: no cover
    raise SystemExit(pytest.main([__file__, "-q"]))
