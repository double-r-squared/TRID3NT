"""Unit tests for the wake Lambda. boto3 mocked -- NO live AWS, NO network."""

from __future__ import annotations

import importlib.util
import json
from pathlib import Path
from unittest import mock

import pytest

_HERE = Path(__file__).resolve().parent
_WAKE_HANDLER = _HERE.parent / "wake" / "handler.py"


@pytest.fixture
def env(monkeypatch):
    monkeypatch.setenv("AWS_REGION", "us-west-2")
    monkeypatch.setenv("AGENT_INSTANCE_ID", "i-0251879a278df797f")


def _load(env_unused):
    ec2 = mock.MagicMock(name="ec2")
    spec = importlib.util.spec_from_file_location("wake_handler_under_test", _WAKE_HANDLER)
    module = importlib.util.module_from_spec(spec)
    with mock.patch("boto3.client", return_value=ec2):
        spec.loader.exec_module(module)
    return module, ec2


def _set_state(ec2, name: str):
    ec2.describe_instances.return_value = {
        "Reservations": [
            {"Instances": [{"InstanceId": "i-0251879a278df797f", "State": {"Name": name}}]}
        ]
    }


def _body(resp):
    return json.loads(resp["body"])


def test_wake_starts_stopped_instance(env):
    module, ec2 = _load(env)
    _set_state(ec2, "stopped")
    resp = module.handler({"requestContext": {"http": {"method": "POST"}}}, None)
    assert resp["statusCode"] == 202
    body = _body(resp)
    assert body["state"] == "starting"
    assert body["started"] is True
    ec2.start_instances.assert_called_once_with(InstanceIds=["i-0251879a278df797f"])
    # CORS open so the browser can call it pre-session.
    assert resp["headers"]["Access-Control-Allow-Origin"] == "*"
    assert resp["headers"]["Cache-Control"] == "no-store"


def test_wake_noop_when_running(env):
    module, ec2 = _load(env)
    _set_state(ec2, "running")
    resp = module.handler({"requestContext": {"http": {"method": "GET"}}}, None)
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
