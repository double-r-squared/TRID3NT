"""Demo-token Lambda for GRACE-2 "code-gate" public-demo sign-in.

Fronted by the EXISTING wake API Gateway HTTP API (a new route,
``POST /demo-token``). The web client's access-code surface POSTs a single
shared demo CODE; on a match this Lambda exchanges the (server-held) demo user's
password for a real Cognito token set and hands it back so the browser signs in
as the demo user -- WITHOUT ever shipping the demo password to the client.

FLOW (the only thing this Lambda decides):
  1. Parse the JSON body ``{"code": "<submitted>"}``.
  2. ``ssm:GetParameter`` the stored access code (SecureString, decrypted) and
     ``hmac.compare_digest`` it against the submitted code -- a constant-time
     compare so a wrong code leaks no timing signal. On a MISMATCH return 403
     with a generic body and make NO Cognito call (the code gate is the only
     thing standing between the public and a real token; never probe Cognito on
     a bad code).
  3. On a MATCH: ``ssm:GetParameter`` the demo user's password (SecureString,
     decrypted) and call ``cognito-idp:AdminInitiateAuth`` with
     ``ADMIN_USER_PASSWORD_AUTH`` for the demo user. The public app client has
     NO client secret (ClientSecret null), so there is NO SECRET_HASH to send.
  4. Return 200 ``{id_token, access_token, refresh_token, expires_in}`` lifted
     from ``AuthenticationResult``.

The demo password NEVER leaves the server: the client only ever holds the
short shared code, and only a correct code yields a token set. The Lambda's IAM
role can ``cognito-idp:AdminInitiateAuth`` on the one pool, ``ssm:GetParameter``
ONLY on ``/grace2/demo-*`` parameters (+ ``kms:Decrypt`` for the SecureString
CMK), and CloudWatch logs on its own group -- nothing else.

CORS mirrors the wake-API config (allow_origins ["*"], methods POST/OPTIONS,
header content-type) on ALL of 200/403/500, and the OPTIONS preflight is handled
directly. Response shape mirrors the view_sign handler (API Gateway payload
format 2.0 proxy response). No third-party deps -- boto3 only (Lambda runtime).
"""

from __future__ import annotations

import hmac
import json
import logging
import os
from typing import Any

import boto3
from botocore.exceptions import ClientError

logger = logging.getLogger()
logger.setLevel(logging.INFO)

# --------------------------------------------------------------------------- #
# Config (env-driven; read at module load -- Lambda env is fixed per deploy).
# --------------------------------------------------------------------------- #

REGION = os.environ.get("AWS_REGION", "us-west-2")

#: Cognito user pool + public app client. The client has NO secret -> no
#: SECRET_HASH in AuthParameters.
COGNITO_USER_POOL_ID = os.environ["COGNITO_USER_POOL_ID"]
GRACE2_COGNITO_CLIENT_ID = os.environ["GRACE2_COGNITO_CLIENT_ID"]

#: The demo user whose credentials the code unlocks (grace2-demo@example.com).
DEMO_USERNAME = os.environ["DEMO_USERNAME"]

#: SSM SecureString parameter names. The VALUES are set out-of-band by NATE at
#: cutover (NOT created in Terraform); this Lambda only reads them by name.
SSM_CODE_PARAM = os.environ["SSM_CODE_PARAM"]  # /grace2/demo-access-code
SSM_PW_PARAM = os.environ["SSM_PW_PARAM"]  # /grace2/demo-user-password

_ssm = boto3.client("ssm", region_name=REGION)
_cognito = boto3.client("cognito-idp", region_name=REGION)

# CORS mirrors the wake API (allow_origins ["*"], methods POST/OPTIONS, header
# content-type). Applied on EVERY response (200/403/500 + the OPTIONS preflight).
_CORS_HEADERS = {
    "Content-Type": "application/json",
    "Access-Control-Allow-Origin": "*",
    "Access-Control-Allow-Methods": "POST, OPTIONS",
    "Access-Control-Allow-Headers": "content-type",
    # A token set is minted per call; never cache the response.
    "Cache-Control": "no-store",
}


def _response(status: int, body: dict) -> dict:
    """Build an API Gateway (payload format 2.0) proxy response."""
    return {
        "statusCode": status,
        "headers": _CORS_HEADERS,
        "body": json.dumps(body, separators=(",", ":")),
    }


def _get_secure_param(name: str) -> str:
    """Fetch a decrypted SecureString SSM parameter value."""
    resp = _ssm.get_parameter(Name=name, WithDecryption=True)
    return resp["Parameter"]["Value"]


def _parse_code(event: dict) -> str | None:
    """Pull the submitted access code from the JSON request body."""
    raw = event.get("body")
    if raw is None:
        return None
    # API Gateway may base64-encode the body; decode if flagged.
    if event.get("isBase64Encoded"):
        import base64

        try:
            raw = base64.b64decode(raw).decode("utf-8")
        except Exception:  # noqa: BLE001
            return None
    try:
        payload = json.loads(raw)
    except (json.JSONDecodeError, TypeError):
        return None
    if not isinstance(payload, dict):
        return None
    code = payload.get("code")
    if not isinstance(code, str):
        return None
    return code


def _method(event: dict) -> str:
    return (
        event.get("requestContext", {})
        .get("http", {})
        .get("method", "")
    )


# --------------------------------------------------------------------------- #
# Entrypoint.
# --------------------------------------------------------------------------- #


def handler(event, context):  # noqa: ANN001, ARG001
    """API Gateway HTTP entrypoint. Exchanges a correct shared demo CODE for a
    real Cognito token set for the demo user. Wrong code -> 403 (no Cognito
    call); correct code -> 200 with the token passthrough."""
    if not isinstance(event, dict):
        event = {}

    if _method(event) == "OPTIONS":
        return _response(200, {"ok": True})

    submitted = _parse_code(event)
    if not submitted:
        # No/empty/malformed code -- treat as a denied gate (generic body).
        return _response(403, {"error": "invalid access code"})

    # Constant-time compare against the stored code. On ANY SSM failure fail
    # closed (500) rather than leaking whether the code matched.
    try:
        stored = _get_secure_param(SSM_CODE_PARAM)
    except ClientError as exc:
        logger.exception("SSM get of access code failed: %s", exc)
        return _response(500, {"error": "demo sign-in unavailable"})
    except Exception as exc:  # noqa: BLE001
        logger.exception("Unexpected SSM error reading access code: %s", exc)
        return _response(500, {"error": "demo sign-in unavailable"})

    if not hmac.compare_digest(submitted, stored):
        # Code mismatch -- generic 403, NO Cognito call.
        logger.info("demo-token: access code mismatch")
        return _response(403, {"error": "invalid access code"})

    # Code OK -> fetch the demo password + exchange it for a token set.
    try:
        password = _get_secure_param(SSM_PW_PARAM)
    except ClientError as exc:
        logger.exception("SSM get of demo password failed: %s", exc)
        return _response(500, {"error": "demo sign-in unavailable"})
    except Exception as exc:  # noqa: BLE001
        logger.exception("Unexpected SSM error reading demo password: %s", exc)
        return _response(500, {"error": "demo sign-in unavailable"})

    try:
        auth = _cognito.admin_initiate_auth(
            UserPoolId=COGNITO_USER_POOL_ID,
            ClientId=GRACE2_COGNITO_CLIENT_ID,
            AuthFlow="ADMIN_USER_PASSWORD_AUTH",
            AuthParameters={
                "USERNAME": DEMO_USERNAME,
                "PASSWORD": password,
            },
        )
    except ClientError as exc:
        # A NotAuthorized/UserNotFound here is a server-config problem (the demo
        # user's password drifted, etc.), not a client error -- the code already
        # matched. Surface a generic 500.
        logger.exception("admin_initiate_auth failed: %s", exc)
        return _response(500, {"error": "demo sign-in unavailable"})
    except Exception as exc:  # noqa: BLE001
        logger.exception("Unexpected admin_initiate_auth error: %s", exc)
        return _response(500, {"error": "demo sign-in unavailable"})

    result: dict[str, Any] = auth.get("AuthenticationResult") or {}
    # ADMIN_USER_PASSWORD_AUTH returns tokens directly (no challenge for a
    # confirmed user with a permanent password).
    if not result.get("IdToken"):
        logger.error(
            "admin_initiate_auth returned no AuthenticationResult/IdToken "
            "(challenge=%r)",
            auth.get("ChallengeName"),
        )
        return _response(500, {"error": "demo sign-in unavailable"})

    return _response(
        200,
        {
            "id_token": result.get("IdToken"),
            "access_token": result.get("AccessToken"),
            "refresh_token": result.get("RefreshToken"),
            "expires_in": result.get("ExpiresIn"),
        },
    )
