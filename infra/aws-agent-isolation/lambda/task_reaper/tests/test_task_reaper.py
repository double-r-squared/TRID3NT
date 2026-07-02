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
    # LEAK FIX: dry-run must NOT delete the route (deleting it while the task
    # keeps running is exactly how the old code manufactured orphans).
    assert "S1" not in ddb.deletes


def test_unreadable_health_is_busy(reaper):
    ddb = FakeDDB([_route("S1", streak=2)])
    ecs = FakeECS(state="RUNNING")
    _wire(reaper, routes=ddb._routes, ecs=ecs, ddb=ddb,
          batch=FakeBatch(False),
          health={"reachable": False, "busy": True, "active_connections": -1})
    out = reaper.handler({}, None)
    assert out["stopped"] == 0          # fail-safe busy -> no stop
    assert ("S1", "0") in ddb.updates


# --------------------------------------------------------------------------- #
# Orphan + max-age reaping (the outage fix)
# --------------------------------------------------------------------------- #
def _dec(reaper, **kw):
    """Call the pure decision with sane thresholds unless overridden."""
    kw.setdefault("orphan_grace_seconds", 600)
    kw.setdefault("max_age_seconds", 5400)
    kw.setdefault("batch_busy", False)
    kw.setdefault("health_busy", False)
    return reaper._orphan_maxage_decision(**kw)


def test_orphan_older_than_grace_is_stopped(reaper):
    action, reason = _dec(reaper, age_seconds=900, has_live_route=False)
    assert (action, reason) == ("stop", "orphan")


def test_no_route_within_grace_is_kept(reaper):
    # mid-provision: RUNNING but the broker has not written its route yet.
    action, reason = _dec(reaper, age_seconds=120, has_live_route=False)
    assert (action, reason) == ("keep", "orphan_within_grace")


def test_orphan_kept_while_batch_in_flight(reaper):
    action, reason = _dec(reaper, age_seconds=900, has_live_route=False, batch_busy=True)
    assert (action, reason) == ("keep", "orphan_batch_in_flight")


def test_orphan_kept_when_health_busy(reaper):
    action, reason = _dec(reaper, age_seconds=900, has_live_route=False, health_busy=True)
    assert (action, reason) == ("keep", "orphan_busy")


def test_over_max_age_no_route_stops(reaper):
    action, reason = _dec(reaper, age_seconds=6000, has_live_route=False)
    assert (action, reason) == ("stop", "max_age")


def test_over_max_age_route_backed_but_busy_is_spared(reaper):
    # SAFETY: never kill a live-route task that is busy mid-work, even at max-age.
    action, reason = _dec(reaper, age_seconds=6000, has_live_route=True, health_busy=True)
    assert (action, reason) == ("keep", "max_age_but_route_busy")


def test_over_max_age_route_backed_idle_stops(reaper):
    action, reason = _dec(reaper, age_seconds=6000, has_live_route=True, health_busy=False)
    assert (action, reason) == ("stop", "max_age")


def test_route_backed_and_young_is_kept(reaper):
    action, reason = _dec(reaper, age_seconds=300, has_live_route=True)
    assert (action, reason) == ("keep", "route_backed")


class FakeECSList:
    """ECS fake supporting the orphan pass (list_tasks + describe_tasks) + stop."""

    def __init__(self, tasks):
        self._tasks = {t["taskArn"]: t for t in tasks}
        self.stopped: list[str] = []

    def list_tasks(self, cluster, family, desiredStatus, maxResults, nextToken=None):
        return {"taskArns": list(self._tasks)}

    def describe_tasks(self, cluster, tasks):
        return {"tasks": [self._tasks[a] for a in tasks if a in self._tasks]}

    def stop_task(self, cluster, task, reason):
        self.stopped.append(task)


def _task(arn, *, age_s, ip="10.0.0.9"):
    from datetime import datetime, timedelta, timezone

    return {
        "taskArn": arn,
        "lastStatus": "RUNNING",
        "startedAt": datetime.now(timezone.utc) - timedelta(seconds=age_s),
        "attachments": [
            {
                "type": "ElasticNetworkInterface",
                "details": [{"name": "privateIPv4Address", "value": ip}],
            }
        ],
    }


def test_reap_orphans_stops_leaked_task(reaper):
    import time as _t

    reaper.ORPHAN_GRACE_SECONDS = 600
    reaper.MAX_AGE_SECONDS = 5400
    orphan = _task("arn:task/orphan", age_s=900)
    backed = _task("arn:task/backed", age_s=300)
    ecs = FakeECSList([orphan, backed])
    reaper._ecs = ecs
    reaper._probe_health = lambda ip: {"reachable": True, "busy": False, "active_connections": 0}
    route_map = {"arn:task/backed": ("U1", "S1")}

    decisions = reaper._reap_orphans_and_max_age(
        [orphan, backed], route_map, batch_busy=False, now=_t.time()
    )
    assert ecs.stopped == ["arn:task/orphan"]  # only the leaked orphan
    by_id = {d["task_id"]: d for d in decisions}
    assert by_id["orphan"]["action"] == "orphan"
    assert by_id["backed"]["action"] == "keep"


def test_reap_max_age_stops_old_route_backed_idle(reaper):
    import time as _t

    reaper.ORPHAN_GRACE_SECONDS = 600
    reaper.MAX_AGE_SECONDS = 5400
    old = _task("arn:task/old", age_s=6000)
    ecs = FakeECSList([old])
    reaper._ecs = ecs
    reaper._probe_health = lambda ip: {"reachable": True, "busy": False, "active_connections": 0}
    route_map = {"arn:task/old": ("U1", "S1")}

    decisions = reaper._reap_orphans_and_max_age(
        [old], route_map, batch_busy=False, now=_t.time()
    )
    assert ecs.stopped == ["arn:task/old"]
    assert decisions[0]["action"] == "max_age"


def test_reap_spares_busy_task_at_max_age(reaper):
    import time as _t

    reaper.ORPHAN_GRACE_SECONDS = 600
    reaper.MAX_AGE_SECONDS = 5400
    old = _task("arn:task/busy", age_s=6000)
    ecs = FakeECSList([old])
    reaper._ecs = ecs
    reaper._probe_health = lambda ip: {"reachable": True, "busy": True, "active_connections": 1}
    route_map = {"arn:task/busy": ("U1", "S1")}

    decisions = reaper._reap_orphans_and_max_age(
        [old], route_map, batch_busy=False, now=_t.time()
    )
    assert ecs.stopped == []  # live-route + busy is spared even past max-age
    assert decisions[0]["action"] == "keep"


if __name__ == "__main__":  # pragma: no cover
    raise SystemExit(pytest.main([__file__, "-q"]))
