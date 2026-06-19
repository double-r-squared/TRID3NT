"""Unit tests for the wake Lambda. boto3 + the Cognito verifier + the health
probe are mocked -- NO live AWS, NO network.

Covers BOTH side-effect paths the handler exposes:

  * the WAKE path (POST default / action=="wake") -> StartInstances on a stopped
    box, and the GET/no-method report-only contract (never mutates);
  * the SLEEP path (POST action=="stop") -> Cognito-gated + not-busy guarded
    StopInstances. A stop must be REFUSED (401) without a verified token, and
    REFUSED (409) when the agent /api/health says busy (or is unreachable ->
    fail-safe busy), and is a no-op on a non-running box.

The verifier (``cognito_verify``) and the health probe (``_probe_health``) are
patched per test the same way the idle-check tests patch ``_probe_health`` --
the real JWKS/RS256 verify and the real urllib probe are exercised by their own
suites; here we only need to drive the handler's branches.
"""

from __future__ import annotations

import importlib.util
import json
from pathlib import Path
from unittest import mock

import pytest

_HERE = Path(__file__).resolve().parent
_WAKE_HANDLER = _HERE.parent / "handler.py"

_INSTANCE = "i-0251879a278df797f"


@pytest.fixture
def env(monkeypatch):
    monkeypatch.setenv("AWS_REGION", "us-west-2")
    monkeypatch.setenv("AGENT_INSTANCE_ID", _INSTANCE)
    monkeypatch.setenv("HEALTH_URL", "https://edge.example/api/health")


def _load(env_unused):
    """Import the wake handler fresh with boto3 replaced by a mock EC2 client.

    The boto3 client is constructed at module import, so patch boto3 first.
    Returns ``(module, ec2)``.
    """
    ec2 = mock.MagicMock(name="ec2")
    spec = importlib.util.spec_from_file_location("wake_handler_under_test", _WAKE_HANDLER)
    module = importlib.util.module_from_spec(spec)
    with mock.patch("boto3.client", return_value=ec2):
        spec.loader.exec_module(module)
    return module, ec2


def _set_state(ec2, name: str):
    ec2.describe_instances.return_value = {
        "Reservations": [
            {"Instances": [{"InstanceId": _INSTANCE, "State": {"Name": name}}]}
        ]
    }


def _body(resp):
    return json.loads(resp["body"])


def _set_verify(monkeypatch, module, claims):
    """Patch the module's Cognito verifier to return ``claims`` for any token."""
    monkeypatch.setattr(module, "cognito_verify", lambda token: claims)


def _set_health(monkeypatch, module, *, busy: bool, active: int = 0, reachable: bool = True):
    """Patch the module's health probe (mirrors the idle-check test helper)."""
    monkeypatch.setattr(
        module,
        "_probe_health",
        lambda: {"reachable": reachable, "busy": busy, "active_connections": active},
    )


def _post(action=None, *, token=None):
    """Build an API Gateway payload-2.0 POST event with an optional action body
    and an optional bearer Authorization header."""
    event: dict = {"requestContext": {"http": {"method": "POST"}}}
    if action is not None:
        event["body"] = json.dumps({"action": action})
    if token is not None:
        event["headers"] = {"authorization": f"Bearer {token}"}
    return event


# --------------------------------------------------------------------------- #
# WAKE path (back-compat) -- POST default / action=="wake" -> StartInstances.
# --------------------------------------------------------------------------- #


def test_post_starts_stopped_instance(env):
    """POST on a stopped box is the user-tap wake -> StartInstances."""
    module, ec2 = _load(env)
    _set_state(ec2, "stopped")
    resp = module.handler({"requestContext": {"http": {"method": "POST"}}}, None)
    assert resp["statusCode"] == 202
    body = _body(resp)
    assert body["state"] == "starting"
    assert body["started"] is True
    ec2.start_instances.assert_called_once_with(InstanceIds=[_INSTANCE])
    # CORS open so the browser can call it pre-session.
    assert resp["headers"]["Access-Control-Allow-Origin"] == "*"
    assert resp["headers"]["Cache-Control"] == "no-store"


def test_post_no_action_still_wakes(env):
    """Back-compat: POST with NO action body still drives the WAKE path
    (StartInstances on a stopped box, never StopInstances)."""
    module, ec2 = _load(env)
    _set_state(ec2, "stopped")
    resp = module.handler(_post(), None)  # no body at all
    assert resp["statusCode"] == 202
    assert _body(resp)["started"] is True
    ec2.start_instances.assert_called_once_with(InstanceIds=[_INSTANCE])
    ec2.stop_instances.assert_not_called()


def test_post_action_wake_still_wakes(env):
    """Back-compat: an explicit action=="wake" body drives StartInstances."""
    module, ec2 = _load(env)
    _set_state(ec2, "stopped")
    resp = module.handler(_post("wake"), None)
    assert resp["statusCode"] == 202
    assert _body(resp)["started"] is True
    ec2.start_instances.assert_called_once_with(InstanceIds=[_INSTANCE])
    ec2.stop_instances.assert_not_called()


def test_get_stopped_does_not_start(env):
    """Asleep-detection contract: GET on a STOPPED box REPORTS, never wakes."""
    module, ec2 = _load(env)
    _set_state(ec2, "stopped")
    resp = module.handler({"requestContext": {"http": {"method": "GET"}}}, None)
    assert resp["statusCode"] == 200
    body = _body(resp)
    assert body["state"] == "stopped"
    assert body["started"] is False
    # The probe must have NO side effect -- this is the whole point of the split.
    ec2.start_instances.assert_not_called()


def test_default_method_stopped_does_not_start(env):
    """A blind probe with no method must NOT wake a stopped box (report-only)."""
    module, ec2 = _load(env)
    _set_state(ec2, "stopped")
    resp = module.handler({}, None)
    assert resp["statusCode"] == 200
    body = _body(resp)
    assert body["state"] == "stopped"
    assert body["started"] is False
    ec2.start_instances.assert_not_called()


def test_get_noop_when_running(env):
    module, ec2 = _load(env)
    _set_state(ec2, "running")
    resp = module.handler({"requestContext": {"http": {"method": "GET"}}}, None)
    assert resp["statusCode"] == 200
    body = _body(resp)
    assert body["state"] == "running"
    assert body["started"] is False
    ec2.start_instances.assert_not_called()


def test_post_noop_when_running(env):
    """POST on a running box no-ops (the WS connects on its own)."""
    module, ec2 = _load(env)
    _set_state(ec2, "running")
    resp = module.handler({"requestContext": {"http": {"method": "POST"}}}, None)
    assert resp["statusCode"] == 200
    body = _body(resp)
    assert body["state"] == "running"
    assert body["started"] is False
    ec2.start_instances.assert_not_called()


@pytest.mark.parametrize("state", ["pending", "stopping", "shutting-down", "unknown"])
def test_wake_noop_on_transitional_states(env, state):
    module, ec2 = _load(env)
    _set_state(ec2, state)
    resp = module.handler({}, None)
    assert resp["statusCode"] == 200
    body = _body(resp)
    assert body["state"] == state
    assert body["started"] is False
    # Never call StartInstances on a non-stopped box (would error on stopping).
    ec2.start_instances.assert_not_called()


def test_wake_options_preflight(env):
    module, ec2 = _load(env)
    resp = module.handler({"requestContext": {"http": {"method": "OPTIONS"}}}, None)
    assert resp["statusCode"] == 200
    assert resp["headers"]["Access-Control-Allow-Methods"].startswith("GET, POST")
    ec2.describe_instances.assert_not_called()


def test_wake_start_failure_returns_500(env):
    module, ec2 = _load(env)
    _set_state(ec2, "stopped")
    ec2.start_instances.side_effect = RuntimeError("throttled")
    resp = module.handler({"requestContext": {"http": {"method": "POST"}}}, None)
    assert resp["statusCode"] == 500
    body = _body(resp)
    assert body["started"] is False
    assert "error" in body


# --------------------------------------------------------------------------- #
# SLEEP path -- POST action=="stop". Cognito-gated + not-busy guarded.
# --------------------------------------------------------------------------- #


def test_stop_no_token_is_401_and_no_stop(env, monkeypatch):
    """A stop with NO Authorization header -> 401, StopInstances NOT called.

    No token means the handler never even reaches the verifier, but patch it to
    None for good measure so the test is independent of pool config.
    """
    module, ec2 = _load(env)
    _set_state(ec2, "running")
    _set_verify(monkeypatch, module, None)
    # Health is idle -- proves the 401 is purely the auth gate, not the busy guard.
    _set_health(monkeypatch, module, busy=False)
    resp = module.handler(_post("stop"), None)  # no token
    assert resp["statusCode"] == 401
    body = _body(resp)
    assert "error" in body
    assert body["instance_id"] == _INSTANCE
    ec2.stop_instances.assert_not_called()


def test_stop_invalid_token_is_401_and_no_stop(env, monkeypatch):
    """A stop with an invalid / no-pool token (verify -> None) -> 401, no stop."""
    module, ec2 = _load(env)
    _set_state(ec2, "running")
    _set_verify(monkeypatch, module, None)  # verifier rejects the token
    _set_health(monkeypatch, module, busy=False)
    resp = module.handler(_post("stop", token="bogus.jwt.token"), None)
    assert resp["statusCode"] == 401
    assert "error" in _body(resp)
    ec2.stop_instances.assert_not_called()
    # An invalid token must short-circuit BEFORE the instance is even described.
    ec2.describe_instances.assert_not_called()


def test_stop_busy_health_is_409_and_no_stop(env, monkeypatch):
    """Valid token but the agent /api/health says BUSY -> 409, StopInstances NOT
    called (never sleep a box mid-turn)."""
    module, ec2 = _load(env)
    _set_state(ec2, "running")
    _set_verify(monkeypatch, module, {"uid": "u1"})
    _set_health(monkeypatch, module, busy=True, active=1)
    resp = module.handler(_post("stop", token="good.jwt.token"), None)
    assert resp["statusCode"] == 409
    body = _body(resp)
    assert "error" in body
    assert body["stopped"] is False
    assert body["state"] == "running"
    ec2.stop_instances.assert_not_called()


def test_stop_unreachable_health_is_failsafe_409(env, monkeypatch):
    """Valid token but the health probe is UNREACHABLE -> fail-safe busy -> 409,
    StopInstances NOT called."""
    module, ec2 = _load(env)
    _set_state(ec2, "running")
    _set_verify(monkeypatch, module, {"uid": "u1"})
    # Unreachable probe reports busy=True (the handler's fail-safe contract).
    _set_health(monkeypatch, module, busy=True, active=-1, reachable=False)
    resp = module.handler(_post("stop", token="good.jwt.token"), None)
    assert resp["statusCode"] == 409
    body = _body(resp)
    assert body["stopped"] is False
    assert body["reachable"] is False
    ec2.stop_instances.assert_not_called()


def test_stop_valid_idle_running_is_200_and_stops(env, monkeypatch):
    """Valid token + idle health + running box -> 200 and StopInstances called
    with the scoped instance id."""
    module, ec2 = _load(env)
    _set_state(ec2, "running")
    _set_verify(monkeypatch, module, {"uid": "u1"})
    _set_health(monkeypatch, module, busy=False, active=0)
    resp = module.handler(_post("stop", token="good.jwt.token"), None)
    assert resp["statusCode"] == 200
    body = _body(resp)
    assert body["state"] == "stopping"
    assert body["stopped"] is True
    ec2.stop_instances.assert_called_once_with(InstanceIds=[_INSTANCE])
    # The wake action must never have fired on the stop path.
    ec2.start_instances.assert_not_called()


def test_stop_already_stopped_is_200_noop(env, monkeypatch):
    """Valid token but the box is already stopped -> 200 no-op: NO health probe,
    NO StopInstances."""
    module, ec2 = _load(env)
    _set_state(ec2, "stopped")
    _set_verify(monkeypatch, module, {"uid": "u1"})
    # Make the probe blow up if it is ever called -- a stopped box must short
    # the busy guard before probing.
    probe = mock.Mock(side_effect=AssertionError("_probe_health must not run on a stopped box"))
    monkeypatch.setattr(module, "_probe_health", probe)
    resp = module.handler(_post("stop", token="good.jwt.token"), None)
    assert resp["statusCode"] == 200
    body = _body(resp)
    assert body["state"] == "stopped"
    assert body["stopped"] is False
    probe.assert_not_called()
    ec2.stop_instances.assert_not_called()


def test_stop_failure_returns_500(env, monkeypatch):
    """A StopInstances API error on the (authorized, idle, running) stop path
    surfaces as 500 with stopped:false."""
    module, ec2 = _load(env)
    _set_state(ec2, "running")
    _set_verify(monkeypatch, module, {"uid": "u1"})
    _set_health(monkeypatch, module, busy=False, active=0)
    ec2.stop_instances.side_effect = RuntimeError("throttled")
    resp = module.handler(_post("stop", token="good.jwt.token"), None)
    assert resp["statusCode"] == 500
    body = _body(resp)
    assert body["stopped"] is False
    assert "error" in body
    ec2.stop_instances.assert_called_once_with(InstanceIds=[_INSTANCE])
