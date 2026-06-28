"""Unit tests for the demo-token Lambda (code-gate public-demo sign-in).

boto3 is fully mocked -- NO live AWS, NO network. We import the handler module
fresh per test from its file path with the required env vars set and the boto3
``ssm`` + ``cognito-idp`` clients patched, then exercise the code gate + the
EPHEMERAL-user mint flow.

CRITICAL properties under test:
  - A WRONG code returns 403 and makes NO ``admin_create_user`` call (the code
    gate is the only thing between the public and a real token; no oracle).
  - A CORRECT code MINTS a fresh user: ``admin_create_user`` ->
    ``admin_set_user_password(Permanent=True)`` -> ``admin_initiate_auth`` IN
    ORDER, and returns 200 with the Cognito token set passed through.
  - TWO successive valid calls mint DISTINCT usernames (per-judge isolation),
    both carrying the cleanup PREFIX (no shared identity).
  - A Cognito ClientError fails CLOSED (500), never a 200.
  - An OPTIONS preflight returns a CORS 200 without touching SSM/Cognito.
"""

from __future__ import annotations

import importlib.util
import json
from pathlib import Path
from unittest import mock

import pytest
from botocore.exceptions import ClientError

_HERE = Path(__file__).resolve().parent
_DEMO_HANDLER = _HERE.parent / "demo_token" / "handler.py"

_GOOD_CODE = "let-me-in-2026"
_PREFIX = "trid3nt-judge-"
_EMAIL_DOMAIN = "demo.trident.invalid"

_SSM_VALUES = {
    "/grace2/demo-access-code": _GOOD_CODE,
}


@pytest.fixture
def env(monkeypatch):
    monkeypatch.setenv("AWS_REGION", "us-west-2")
    monkeypatch.setenv("COGNITO_USER_POOL_ID", "us-west-2_mIpKrr727")
    monkeypatch.setenv("GRACE2_COGNITO_CLIENT_ID", "43ovkrtt97oh6gsnl006aecera")
    monkeypatch.setenv("SSM_CODE_PARAM", "/grace2/demo-access-code")
    monkeypatch.setenv("DEMO_USER_PREFIX", _PREFIX)
    monkeypatch.setenv("DEMO_EMAIL_DOMAIN", _EMAIL_DOMAIN)


def _load_handler(env_unused):
    """Import the demo-token handler with boto3 clients replaced by mocks.

    Returns ``(module, ssm, cognito)``. The boto3 clients are constructed at
    module import, so patch boto3.client first. Loaded under a UNIQUE module
    name so it never collides with the other handler.py files.

    The cognito mock's ``admin_initiate_auth`` defaults to a good token result
    so the happy path works out of the box; tests override as needed.
    """
    ssm = mock.MagicMock(name="ssm")
    cognito = mock.MagicMock(name="cognito")

    def _get_parameter(Name, WithDecryption=False):  # noqa: N803
        assert WithDecryption is True
        return {"Parameter": {"Value": _SSM_VALUES[Name]}}

    ssm.get_parameter.side_effect = _get_parameter
    cognito.admin_initiate_auth.return_value = _ok_auth_result()

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
# Wrong / missing / malformed code -> 403, NO Cognito call (no oracle).
# --------------------------------------------------------------------------- #


def test_wrong_code_403_no_cognito_call(env):
    module, ssm, cognito = _load_handler(env)
    out = module.handler(_post_event("nope-wrong"), None)
    assert out["statusCode"] == 403
    body = json.loads(out["body"])
    assert "error" in body
    # The single most important property: NO Cognito work on a bad code -- not
    # even user creation (no oracle, no resource churn).
    cognito.admin_create_user.assert_not_called()
    cognito.admin_set_user_password.assert_not_called()
    cognito.admin_initiate_auth.assert_not_called()
    # Only the cheap access-code read happened (constant-time compare input).
    ssm.get_parameter.assert_called_once_with(
        Name="/grace2/demo-access-code", WithDecryption=True
    )
    _assert_cors(out)


def test_missing_code_403_no_cognito_call(env):
    module, ssm, cognito = _load_handler(env)
    out = module.handler(_post_event(None, raw_body=json.dumps({})), None)
    assert out["statusCode"] == 403
    cognito.admin_create_user.assert_not_called()
    cognito.admin_initiate_auth.assert_not_called()
    _assert_cors(out)


def test_malformed_body_403(env):
    module, ssm, cognito = _load_handler(env)
    out = module.handler(_post_event(None, raw_body="{not-json"), None)
    assert out["statusCode"] == 403
    cognito.admin_create_user.assert_not_called()
    cognito.admin_initiate_auth.assert_not_called()
    _assert_cors(out)


# --------------------------------------------------------------------------- #
# Correct code -> mint ephemeral user, in order, 200 token passthrough.
# --------------------------------------------------------------------------- #


def test_correct_code_mints_user_in_order_200(env):
    module, ssm, cognito = _load_handler(env)

    # Track call ordering across the three Cognito admin calls.
    parent = mock.MagicMock()
    parent.attach_mock(cognito.admin_create_user, "create")
    parent.attach_mock(cognito.admin_set_user_password, "setpw")
    parent.attach_mock(cognito.admin_initiate_auth, "auth")

    out = module.handler(_post_event(_GOOD_CODE), None)
    assert out["statusCode"] == 200
    body = json.loads(out["body"])
    assert body["id_token"] == "ID.TOKEN.VALUE"
    assert body["access_token"] == "ACCESS.TOKEN.VALUE"
    assert body["refresh_token"] == "REFRESH.TOKEN.VALUE"
    assert body["expires_in"] == 3600

    # All three were called exactly once, in create -> setpw -> auth order.
    cognito.admin_create_user.assert_called_once()
    cognito.admin_set_user_password.assert_called_once()
    cognito.admin_initiate_auth.assert_called_once()
    order = [c[0] for c in parent.mock_calls]
    assert order == ["create", "setpw", "auth"]

    # admin_create_user: SUPPRESS + email attrs + prefixed email-form username.
    cargs = cognito.admin_create_user.call_args.kwargs
    assert cargs["UserPoolId"] == "us-west-2_mIpKrr727"
    assert cargs["MessageAction"] == "SUPPRESS"
    uname = cargs["Username"]
    assert uname.startswith(_PREFIX)
    assert uname.endswith("@" + _EMAIL_DOMAIN)
    attrs = {a["Name"]: a["Value"] for a in cargs["UserAttributes"]}
    assert attrs["email"] == uname
    assert attrs["email_verified"] == "true"

    # admin_set_user_password: Permanent=True (confirms + no challenge), same
    # user, same throwaway password the auth then uses.
    pwargs = cognito.admin_set_user_password.call_args.kwargs
    assert pwargs["UserPoolId"] == "us-west-2_mIpKrr727"
    assert pwargs["Username"] == uname
    assert pwargs["Permanent"] is True
    minted_pw = pwargs["Password"]

    # admin_initiate_auth: right flow, no SECRET_HASH (public client), and the
    # SAME username + the SAME just-set password.
    aargs = cognito.admin_initiate_auth.call_args.kwargs
    assert aargs["UserPoolId"] == "us-west-2_mIpKrr727"
    assert aargs["ClientId"] == "43ovkrtt97oh6gsnl006aecera"
    assert aargs["AuthFlow"] == "ADMIN_USER_PASSWORD_AUTH"
    assert aargs["AuthParameters"]["USERNAME"] == uname
    assert aargs["AuthParameters"]["PASSWORD"] == minted_pw
    assert "SECRET_HASH" not in aargs["AuthParameters"]
    _assert_cors(out)


def test_two_calls_mint_distinct_prefixed_usernames(env):
    """Per-judge isolation: successive valid entries get DISTINCT identities,
    both carrying the cleanup PREFIX (no shared account)."""
    module, _ssm, cognito = _load_handler(env)

    out1 = module.handler(_post_event(_GOOD_CODE), None)
    out2 = module.handler(_post_event(_GOOD_CODE), None)
    assert out1["statusCode"] == 200
    assert out2["statusCode"] == 200

    names = [c.kwargs["Username"] for c in cognito.admin_create_user.call_args_list]
    assert len(names) == 2
    u1, u2 = names
    assert u1 != u2  # distinct subs -> isolated cases
    assert u1.startswith(_PREFIX) and u2.startswith(_PREFIX)
    assert u1.endswith("@" + _EMAIL_DOMAIN) and u2.endswith("@" + _EMAIL_DOMAIN)


# --------------------------------------------------------------------------- #
# Fail closed: any Cognito error -> 500, never a 200.
# --------------------------------------------------------------------------- #


def _client_error(op):
    return ClientError(
        {"Error": {"Code": "InternalErrorException", "Message": "boom"}}, op
    )


def test_create_user_error_fails_closed_500(env):
    module, _ssm, cognito = _load_handler(env)
    cognito.admin_create_user.side_effect = _client_error("AdminCreateUser")

    out = module.handler(_post_event(_GOOD_CODE), None)
    assert out["statusCode"] == 500
    assert out["statusCode"] != 200
    # Mint failed before setpw/auth.
    cognito.admin_set_user_password.assert_not_called()
    cognito.admin_initiate_auth.assert_not_called()
    _assert_cors(out)


def test_initiate_auth_error_fails_closed_500(env):
    module, _ssm, cognito = _load_handler(env)
    cognito.admin_initiate_auth.side_effect = _client_error("AdminInitiateAuth")

    out = module.handler(_post_event(_GOOD_CODE), None)
    assert out["statusCode"] == 500
    # The user was created + password set, but auth blew up -> fail closed.
    cognito.admin_create_user.assert_called_once()
    cognito.admin_set_user_password.assert_called_once()
    _assert_cors(out)


# --------------------------------------------------------------------------- #
# OPTIONS preflight -> CORS 200, no SSM/Cognito.
# --------------------------------------------------------------------------- #


def test_options_preflight_cors_200(env):
    module, ssm, cognito = _load_handler(env)
    event = {"requestContext": {"http": {"method": "OPTIONS"}}}
    out = module.handler(event, None)
    assert out["statusCode"] == 200
    _assert_cors(out)
    # Preflight never touches SSM/Cognito.
    ssm.get_parameter.assert_not_called()
    cognito.admin_create_user.assert_not_called()
    cognito.admin_initiate_auth.assert_not_called()
