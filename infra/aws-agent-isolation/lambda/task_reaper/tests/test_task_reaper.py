"""Unit tests for the per-task idle reaper (generalized idle_check).

boto3 + urllib fully mocked -- no live AWS / network. Covers: busy resets the
streak, idle advances + stops at threshold, a non-RUNNING task drops its route,
the G3 Batch guard keeps tasks up, DRY_RUN logs but does not StopTask.

Run: python -m pytest infra/aws-agent-isolation/lambda/task_reaper/tests/test_task_reaper.py
"""

from __future__ import annotations

import importlib
import os
import sys
from pathlib import Path

import pytest

# Make the handler importable as a top-level module (Lambda layout).
_HANDLER_DIR = str(Path(__file__).resolve().parents[1])
if _HANDLER_DIR not in sys.path:
    sys.path.insert(0, _HANDLER_DIR)


@pytest.fixture
def reaper(monkeypatch):
    """Import handler with required env set + boto3 clients patched to fakes."""
    monkeypatch.setenv("AWS_REGION", "us-west-2")
    monkeypatch.setenv("ECS_CLUSTER", "grace2-agents")
    monkeypatch.setenv("ROUTES_TABLE", "grace2_session_routes")
    monkeypatch.setenv("IDLE_THRESHOLD_CHECKS", "3")
    monkeypatch.setenv("BATCH_QUEUES", "grace2-solvers")
    monkeypatch.setenv("DRY_RUN", "false")

    import handler as h  # noqa

    importlib.reload(h)
    return h


# --------------------------------------------------------------------------- #
# Fakes
# --------------------------------------------------------------------------- #
class FakeECS:
    def __init__(self, state="RUNNING"):
        self.state = state
        self.stopped: list[str] = []

    def describe_tasks(self, cluster, tasks):
        return {"tasks": [{"lastStatus": self.state}]}

    def stop_task(self, cluster, task, reason):
        self.stopped.append(task)


class FakeDDB:
    def __init__(self, routes):
        self._routes = routes
        self.updates: list[tuple] = []
        self.deletes: list[tuple] = []

    def scan(self, **kwargs):
        return {"Items": self._routes}

    def update_item(self, TableName, Key, UpdateExpression, ExpressionAttributeValues):
        self.updates.append((Key["session_id"]["S"], ExpressionAttributeValues[":s"]["N"]))

    def delete_item(self, TableName, Key):
        self.deletes.append(Key["session_id"]["S"])


class FakeBatch:
    def __init__(self, in_flight=False):
        self.in_flight = in_flight

    def list_jobs(self, jobQueue, jobStatus, maxResults):
        if self.in_flight and jobStatus == "RUNNING":
            return {"jobSummaryList": [{"jobId": "j1"}]}
        return {"jobSummaryList": []}


def _route(session_id, *, ip="10.0.0.1", streak=0, task="arn:task/x"):
    return {
        "user_ulid": {"S": "U1"},
        "session_id": {"S": session_id},
        "task_arn": {"S": task},
        "private_ip": {"S": ip},
        "idle_streak": {"N": str(streak)},
    }


def _wire(reaper, *, routes, ecs, ddb, batch, health):
    reaper._ecs = ecs
    reaper._ddb = ddb
    reaper._batch = batch
    reaper._probe_health = lambda ip: health  # patch the probe


# --------------------------------------------------------------------------- #
# Tests
# --------------------------------------------------------------------------- #
def test_busy_resets_streak(reaper):
    ddb = FakeDDB([_route("S1", streak=2)])
    _wire(reaper, routes=ddb._routes, ecs=FakeECS(), ddb=ddb,
          batch=FakeBatch(False),
          health={"reachable": True, "busy": True, "active_connections": 1})
    out = reaper.handler({}, None)
    assert out["stopped"] == 0
    # streak written back to 0
    assert ("S1", "0") in ddb.updates


def test_idle_advances_and_stops_at_threshold(reaper):
    ddb = FakeDDB([_route("S1", streak=2)])  # one below threshold of 3
    ecs = FakeECS(state="RUNNING")
    _wire(reaper, routes=ddb._routes, ecs=ecs, ddb=ddb,
          batch=FakeBatch(False),
          health={"reachable": True, "busy": False, "active_connections": 0})
    out = reaper.handler({}, None)
    assert out["stopped"] == 1
    assert ecs.stopped == ["arn:task/x"]
    assert "S1" in ddb.deletes  # route dropped after stop


def test_idle_below_threshold_does_not_stop(reaper):
    ddb = FakeDDB([_route("S1", streak=0)])
    ecs = FakeECS(state="RUNNING")
    _wire(reaper, routes=ddb._routes, ecs=ecs, ddb=ddb,
          batch=FakeBatch(False),
          health={"reachable": True, "busy": False, "active_connections": 0})
    out = reaper.handler({}, None)
    assert out["stopped"] == 0
    assert ecs.stopped == []
    assert ("S1", "1") in ddb.updates  # streak advanced to 1


def test_batch_in_flight_keeps_task_up(reaper):
    ddb = FakeDDB([_route("S1", streak=2)])
    ecs = FakeECS(state="RUNNING")
    _wire(reaper, routes=ddb._routes, ecs=ecs, ddb=ddb,
          batch=FakeBatch(in_flight=True),  # G3: a solve is running
          health={"reachable": True, "busy": False, "active_connections": 0})
    out = reaper.handler({}, None)
    assert out["batch_in_flight"] is True
    assert out["stopped"] == 0
    assert ecs.stopped == []
    assert ("S1", "0") in ddb.updates  # streak reset by the Batch guard


def test_non_running_task_drops_route(reaper):
    ddb = FakeDDB([_route("S1")])
    ecs = FakeECS(state="STOPPED")
    _wire(reaper, routes=ddb._routes, ecs=ecs, ddb=ddb,
          batch=FakeBatch(False),
          health={"reachable": True, "busy": False, "active_connections": 0})
    out = reaper.handler({}, None)
    assert out["stopped"] == 0
    assert "S1" in ddb.deletes  # route dropped (task gone)


def test_dry_run_does_not_stop(reaper, monkeypatch):
    monkeypatch.setenv("DRY_RUN", "true")
    import handler as h
    importlib.reload(h)
    ddb = FakeDDB([_route("S1", streak=2)])
    ecs = FakeECS(state="RUNNING")
    _wire(h, routes=ddb._routes, ecs=ecs, ddb=ddb,
          batch=FakeBatch(False),
          health={"reachable": True, "busy": False, "active_connections": 0})
    out = h.handler({}, None)
    assert out["stopped"] == 1          # decision counted
    assert ecs.stopped == []            # but no real StopTask
    assert out["decisions"][0]["action"] == "stop_dryrun"


def test_unreadable_health_is_busy(reaper):
    ddb = FakeDDB([_route("S1", streak=2)])
    ecs = FakeECS(state="RUNNING")
    _wire(reaper, routes=ddb._routes, ecs=ecs, ddb=ddb,
          batch=FakeBatch(False),
          health={"reachable": False, "busy": True, "active_connections": -1})
    out = reaper.handler({}, None)
    assert out["stopped"] == 0          # fail-safe busy -> no stop
    assert ("S1", "0") in ddb.updates


if __name__ == "__main__":  # pragma: no cover
    raise SystemExit(pytest.main([__file__, "-q"]))
