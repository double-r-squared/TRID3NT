"""Unit tests for the idle-check Lambda (bulletproof auto-stop logic).

boto3 + urllib are fully mocked -- NO live AWS, NO network. We import the
handler module fresh per test with the required env vars set and the boto3
clients patched, then exercise each guard + the streak state machine.

The CRITICAL property under test: the box is stopped ONLY when EVERY guard
passes for IDLE_THRESHOLD_CHECKS consecutive ticks. Any busy signal (the agent
``busy`` flag = detached in-flight turn or in-flight solve, an in-flight Batch
solve, unreachable health, or a non-running instance) must RESET the streak and
leave the box up.

STAGE 3 (sleep/wake): a merely-open IDLE viewer connection
(``active_connections > 0`` with ``busy == false`` and no Batch) is NO LONGER a
busy signal -- it counts as IDLE and is eligible to advance the streak, so an
idle viewer no longer pins the box forever. ``active_connections`` is still
reported in the decision for observability.
"""

from __future__ import annotations

import importlib.util
import json
import sys
from pathlib import Path
from unittest import mock

import pytest

_HERE = Path(__file__).resolve().parent
_IDLE_HANDLER = _HERE.parent / "idle_check" / "handler.py"


@pytest.fixture
def env(monkeypatch):
    monkeypatch.setenv("AWS_REGION", "us-west-2")
    monkeypatch.setenv("AGENT_INSTANCE_ID", "i-0251879a278df797f")
    monkeypatch.setenv("HEALTH_URL", "https://edge.example/api/health")
    monkeypatch.setenv("STATE_TABLE", "grace2-autostop-state")
    monkeypatch.setenv("IDLE_THRESHOLD_CHECKS", "3")
    monkeypatch.setenv("BATCH_QUEUES", "grace2-solvers")
    monkeypatch.setenv("DRY_RUN", "false")


class _Streak:
    """In-memory stand-in for the DynamoDB streak store."""

    def __init__(self, start: int = 0):
        self.value = start


def _load_handler(env_unused):
    """Import the handler module with boto3 clients replaced by mocks.

    Returns ``(module, ec2, ddb, batch, streak)`` where the mocks are wired so
    DynamoDB get/put read/write the shared ``streak`` object.
    """
    ec2 = mock.MagicMock(name="ec2")
    ddb = mock.MagicMock(name="ddb")
    batch = mock.MagicMock(name="batch")
    streak = _Streak()

    def _get_item(**kwargs):
        return {"Item": {"instance_id": {"S": "i"}, "idle_streak": {"N": str(streak.value)}}}

    def _put_item(**kwargs):
        streak.value = int(kwargs["Item"]["idle_streak"]["N"])
        return {}

    ddb.get_item.side_effect = _get_item
    ddb.put_item.side_effect = _put_item

    def _client(name, **kwargs):
        return {"ec2": ec2, "dynamodb": ddb, "batch": batch}[name]

    # Load the idle-check handler under a UNIQUE module name from its file path so
    # it never collides with the wake handler (both files are named handler.py).
    # The boto3 clients are constructed at module import, so patch boto3 first.
    spec = importlib.util.spec_from_file_location("idle_check_handler_under_test", _IDLE_HANDLER)
    module = importlib.util.module_from_spec(spec)
    with mock.patch("boto3.client", side_effect=_client):
        spec.loader.exec_module(module)
    return module, ec2, ddb, batch, streak


def _set_state(ec2, name: str):
    ec2.describe_instances.return_value = {
        "Reservations": [
            {"Instances": [{"InstanceId": "i-0251879a278df797f", "State": {"Name": name}}]}
        ]
    }


def _set_health(monkeypatch, module, *, busy: bool, active: int, reachable: bool = True):
    monkeypatch.setattr(
        module,
        "_probe_health",
        lambda: {"reachable": reachable, "busy": busy, "active_connections": active},
    )


def _no_batch(batch):
    batch.list_jobs.return_value = {"jobSummaryList": []}


# --------------------------------------------------------------------------- #


def test_noop_when_instance_not_running(env, monkeypatch):
    module, ec2, ddb, batch, streak = _load_handler(env)
    _set_state(ec2, "stopped")
    streak.value = 2  # had a streak going
    out = module.handler({}, None)
    assert out["action"] == "noop"
    assert "instance_state" in out["reason"]
    assert streak.value == 0  # reset
    ec2.stop_instances.assert_not_called()


def test_open_idle_connection_counts_as_idle_advances_streak(env, monkeypatch):
    # STAGE 3 (sleep/wake): an OPEN but IDLE viewer connection
    # (active_connections > 0, busy == false, no Batch) is NO LONGER busy. The
    # poll must count as idle and ADVANCE the streak (not reset it), so an idle
    # viewer no longer pins the box. active_connections is still reported.
    module, ec2, ddb, batch, streak = _load_handler(env)
    _set_state(ec2, "running")
    _no_batch(batch)
    _set_health(monkeypatch, module, busy=False, active=1)  # a tab is open, idle
    streak.value = 1
    out = module.handler({}, None)
    assert out["action"] == "noop"
    assert out["reason"] == "idle_below_threshold"
    assert out["idle_streak"] == 2  # advanced, NOT reset
    assert streak.value == 2
    ec2.stop_instances.assert_not_called()


def test_open_idle_connection_does_not_block_stop_at_threshold(env, monkeypatch):
    # STAGE 3: an idle viewer with the connection open must NOT prevent the box
    # from stopping once the consecutive-idle threshold is reached.
    module, ec2, ddb, batch, streak = _load_handler(env)
    _set_state(ec2, "running")
    _no_batch(batch)
    _set_health(monkeypatch, module, busy=False, active=3)  # idle viewer, open tab
    streak.value = 2  # threshold is 3 (env fixture); this tick is the 3rd, idle
    out = module.handler({}, None)
    assert out["action"] == "stop"
    assert out["idle_streak"] == 3
    ec2.stop_instances.assert_called_once_with(InstanceIds=["i-0251879a278df797f"])
    assert streak.value == 0


def test_busy_flag_true_resets_streak(env, monkeypatch):
    module, ec2, ddb, batch, streak = _load_handler(env)
    _set_state(ec2, "running")
    _no_batch(batch)
    _set_health(monkeypatch, module, busy=True, active=0)  # detached solve
    streak.value = 2
    out = module.handler({}, None)
    assert out["action"] == "noop"
    assert streak.value == 0
    ec2.stop_instances.assert_not_called()


def test_batch_solve_in_flight_blocks_stop(env, monkeypatch):
    module, ec2, ddb, batch, streak = _load_handler(env)
    _set_state(ec2, "running")
    _set_health(monkeypatch, module, busy=False, active=0)  # agent idle...
    # ...but a Batch solve is RUNNING.
    batch.list_jobs.side_effect = lambda **kw: (
        {"jobSummaryList": [{"jobId": "j1"}]} if kw.get("jobStatus") == "RUNNING" else {"jobSummaryList": []}
    )
    streak.value = 2
    out = module.handler({}, None)
    assert out["action"] == "noop"
    assert out["batch_in_flight"] is True
    assert streak.value == 0
    ec2.stop_instances.assert_not_called()


def test_unreachable_health_is_failsafe_busy(env, monkeypatch):
    module, ec2, ddb, batch, streak = _load_handler(env)
    _set_state(ec2, "running")
    _no_batch(batch)
    _set_health(monkeypatch, module, busy=True, active=-1, reachable=False)
    streak.value = 2
    out = module.handler({}, None)
    assert out["action"] == "noop"
    assert streak.value == 0
    ec2.stop_instances.assert_not_called()


def test_idle_streak_advances_below_threshold(env, monkeypatch):
    module, ec2, ddb, batch, streak = _load_handler(env)
    _set_state(ec2, "running")
    _no_batch(batch)
    _set_health(monkeypatch, module, busy=False, active=0)
    streak.value = 0
    out = module.handler({}, None)
    assert out["action"] == "noop"
    assert out["reason"] == "idle_below_threshold"
    assert out["idle_streak"] == 1
    assert streak.value == 1
    ec2.stop_instances.assert_not_called()


def test_stop_only_after_threshold_consecutive_idles(env, monkeypatch):
    module, ec2, ddb, batch, streak = _load_handler(env)
    _set_state(ec2, "running")
    _no_batch(batch)
    _set_health(monkeypatch, module, busy=False, active=0)
    # threshold = 3. Ticks 1,2 advance; tick 3 stops.
    out1 = module.handler({}, None)
    assert out1["idle_streak"] == 1 and out1["action"] == "noop"
    out2 = module.handler({}, None)
    assert out2["idle_streak"] == 2 and out2["action"] == "noop"
    ec2.stop_instances.assert_not_called()
    out3 = module.handler({}, None)
    assert out3["action"] == "stop"
    assert out3["idle_streak"] == 3
    ec2.stop_instances.assert_called_once_with(InstanceIds=["i-0251879a278df797f"])
    # Streak reset after stop so a wake starts a fresh countdown.
    assert streak.value == 0


def test_busy_midway_resets_then_requires_full_threshold_again(env, monkeypatch):
    module, ec2, ddb, batch, streak = _load_handler(env)
    _set_state(ec2, "running")
    _no_batch(batch)
    # Two idle ticks...
    _set_health(monkeypatch, module, busy=False, active=0)
    module.handler({}, None)
    module.handler({}, None)
    assert streak.value == 2
    # ...then a real busy signal arrives (agent busy flag true = an in-flight
    # turn/solve that survived a socket drop) -> reset. STAGE 3: it is the busy
    # FLAG, not the connection count, that resets the streak.
    _set_health(monkeypatch, module, busy=True, active=0)
    module.handler({}, None)
    assert streak.value == 0
    ec2.stop_instances.assert_not_called()


def test_dry_run_does_not_stop(env, monkeypatch):
    monkeypatch.setenv("DRY_RUN", "true")
    module, ec2, ddb, batch, streak = _load_handler(env)
    _set_state(ec2, "running")
    _no_batch(batch)
    _set_health(monkeypatch, module, busy=False, active=0)
    streak.value = 2
    out = module.handler({}, None)
    assert out["action"] == "stop_dryrun"
    ec2.stop_instances.assert_not_called()


def test_probe_health_parses_real_body(env, monkeypatch):
    """The REAL _probe_health must accept the agent's exact health body shape."""
    module, ec2, ddb, batch, streak = _load_handler(env)

    class _Resp:
        def __init__(self, payload):
            self._payload = payload

        def read(self):
            return json.dumps(self._payload).encode("utf-8")

        def __enter__(self):
            return self

        def __exit__(self, *a):
            return False

    with mock.patch.object(
        module.urllib.request,
        "urlopen",
        return_value=_Resp({"ok": True, "active_connections": 0, "busy": False}),
    ):
        out = module._probe_health()
    assert out == {"reachable": True, "busy": False, "active_connections": 0}


def test_probe_health_old_body_is_failsafe_busy(env, monkeypatch):
    """An older agent build whose /api/health lacks the autostop fields -> busy."""
    module, ec2, ddb, batch, streak = _load_handler(env)

    class _Resp:
        def read(self):
            return json.dumps({"ok": True}).encode("utf-8")

        def __enter__(self):
            return self

        def __exit__(self, *a):
            return False

    with mock.patch.object(module.urllib.request, "urlopen", return_value=_Resp()):
        out = module._probe_health()
    assert out["busy"] is True
    assert out["reachable"] is True
