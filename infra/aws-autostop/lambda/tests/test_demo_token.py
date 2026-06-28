"""Unit tests for the demo-token Lambda (code-gate public-demo sign-in).

boto3 is fully mocked -- NO live AWS, NO network. We import the handler module
fresh per test from its file path with the required env vars set and the boto3
``ssm`` + ``cognito-idp`` clients patched, then exercise the code gate.

CRITICAL properties under test:
  - A WRONG code returns 403 and makes NO ``admin_initiate_auth`` call (the code
    gate is the only thing between the public and a real token).
  - A CORRECT code returns 200 with the Cognito token set passed through.
  - An OPTIONS preflight returns a CORS 200 without touching SSM/Cognito.
"""

from __future__ import annotations

import importlib.util
import json
from pathlib import Path
from unittest import mock

import pytest

_HERE = Path(__file__).resolve().parent
_DEMO_HANDLER = _HERE.parent / "demo_token" / "handler.py"

_GOOD_CODE = "let-me-in-2026"
_DEMO_PW = "sup3r-s3cret-demo-pw"

_SSM_VALUES = {
    "/grace2/demo-access-code": _GOOD_CODE,
    "/grace2/demo-user-password": _DEMO_PW,
}


@pytest.fixture
def env(monkeypatch):
    monkeypatch.setenv("AWS_REGION", "us-west-2")
    monkeypatch.setenv("COGNITO_USER_POOL_ID", "us-west-2_mIpKrr727")
    monkeypatch.setenv("GRACE2_COGNITO_CLIENT_ID", "43ovkrtt97oh6gsnl006aecera")
    monkeypatch.setenv("DEMO_USERNAME", "grace2-demo@example.com")
    monkeypatch.setenv("SSM_CODE_PARAM", "/grace2/demo-access-code")
    monkeypatch.setenv("SSM_PW_PARAM", "/grace2/demo-user-password")


def _load_handler(env_unused):
    """Import the demo-token handler with boto3 clients replaced by mocks.

    Returns ``(module, ssm, cognito)``. The boto3 clients are constructed at
    module import, so patch boto3.client first. Loaded under a UNIQUE module
    name so it never collides with the other handler.py files.
    """
    ssm = mock.MagicMock(name="ssm")
    cognito = mock.MagicMock(name="cognito")

    def _get_parameter(Name, WithDecryption=False):  # noqa: N803
        assert WithDecryption is True
        return {"Parameter": {"Value": _SSM_VALUES[Name]}}

    ssm.get_parameter.side_effect = _get_parameter

    def _client(name, **kwargs):
        return {"ssm": ssm, "cognito-idp": cognito}[name]

    spec = importlib.util.spec_from_file_location(
        "demo_token_handler_under_test", _DEMO_HANDLER
    )
    module = importlib.util.module_from_spec(spec)
    with mock.patch("boto3.client", side_effect=_client):
        spec.loader.exec_module(module)
    return module, ssm, cognito


def _post_event(code, *, raw_body=None):
    """Build an API Gateway payload-2.0 POST event with a JSON body."""
    body = raw_body if raw_body is not None else json.dumps({"code": code})
    return {
        "requestContext": {"http": {"method": "POST"}},
        "body": body,
    }


def _ok_auth_result():
    return {
        "AuthenticationResult": {
            "IdToken": "ID.TOKEN.VALUE",
            "AccessToken": "ACCESS.TOKEN.VALUE",
            "RefreshToken": "REFRESH.TOKEN.VALUE",
            "ExpiresIn": 3600,
        }
    }


# --------------------------------------------------------------------------- #
# CORS on every response.
# --------------------------------------------------------------------------- #


def _assert_cors(resp):
    h = resp["headers"]
    assert h["Access-Control-Allow-Origin"] == "*"
    assert "POST" in h["Access-Control-Allow-Methods"]
    assert "OPTIONS" in h["Access-Control-Allow-Methods"]
    assert "content-type" in h["Access-Control-Allow-Headers"].lower()


# --------------------------------------------------------------------------- #


def test_wrong_code_403_no_cognito_call(env):
    module, ssm, cognito = _load_handler(env)
    out = module.handler(_post_event("nope-wrong"), None)
    assert out["statusCode"] == 403
    body = json.loads(out["body"])
    assert "error" in body
    # The single most important property: NO Cognito call on a bad code.
    cognito.admin_initiate_auth.assert_not_called()
    # The stored code was read (to compare), but the password was NOT.
    ssm.get_parameter.assert_called_once_with(
        Name="/grace2/demo-access-code", WithDecryption=True
    )
    _assert_cors(out)


def test_correct_code_200_token_passthrough(env):
    module, ssm, cognito = _load_handler(env)
    cognito.admin_initiate_auth.return_value = _ok_auth_result()

    out = module.handler(_post_event(_GOOD_CODE), None)
    assert out["statusCode"] == 200
    body = json.loads(out["body"])
    assert body["id_token"] == "ID.TOKEN.VALUE"
    assert body["access_token"] == "ACCESS.TOKEN.VALUE"
    assert body["refresh_token"] == "REFRESH.TOKEN.VALUE"
    assert body["expires_in"] == 3600

    # Cognito was called with the right flow + no SECRET_HASH (public client).
    cognito.admin_initiate_auth.assert_called_once()
    kwargs = cognito.admin_initiate_auth.call_args.kwargs
    assert kwargs["UserPoolId"] == "us-west-2_mIpKrr727"
    assert kwargs["ClientId"] == "43ovkrtt97oh6gsnl006aecera"
    assert kwargs["AuthFlow"] == "ADMIN_USER_PASSWORD_AUTH"
    assert kwargs["AuthParameters"]["USERNAME"] == "grace2-demo@example.com"
    assert kwargs["AuthParameters"]["PASSWORD"] == _DEMO_PW
    assert "SECRET_HASH" not in kwargs["AuthParameters"]
    _assert_cors(out)


def test_options_preflight_cors_200(env):
    module, ssm, cognito = _load_handler(env)
    event = {"requestContext": {"http": {"method": "OPTIONS"}}}
    out = module.handler(event, None)
    assert out["statusCode"] == 200
    _assert_cors(out)
    # Preflight never touches SSM/Cognito.
    ssm.get_parameter.assert_not_called()
    cognito.admin_initiate_auth.assert_not_called()


def test_missing_code_403_no_cognito_call(env):
    module, ssm, cognito = _load_handler(env)
    out = module.handler(_post_event(None, raw_body=json.dumps({})), None)
    assert out["statusCode"] == 403
    cognito.admin_initiate_auth.assert_not_called()
    _assert_cors(out)


def test_malformed_body_403(env):
    module, ssm, cognito = _load_handler(env)
    out = module.handler(_post_event(None, raw_body="{not-json"), None)
    assert out["statusCode"] == 403
    cognito.admin_initiate_auth.assert_not_called()
    _assert_cors(out)
