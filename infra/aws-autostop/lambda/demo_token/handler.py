"""Demo-token Lambda for GRACE-2 "code-gate" public-demo sign-in.

Fronted by the EXISTING wake API Gateway HTTP API (a new route,
``POST /demo-token``). The web client's access-code surface POSTs a single
shared demo CODE; on a match this Lambda MINTS A FRESH EPHEMERAL Cognito user
(one per code-entry, uncapped per-judge isolation) and hands back that user's
token set so the browser signs in as a private throwaway identity -- WITHOUT any
shared account and WITHOUT any password ever reaching the client.

WHY EPHEMERAL: a shared demo account crosses every judge's cases onto one
Cognito sub. By minting a distinct user per code-entry, each judge gets a unique
sub and the EXISTING per-user case scoping isolates them automatically -- no web
or agent change needed.

FLOW (the only thing this Lambda decides):
  1. Parse the JSON body ``{"code": "<submitted>"}``.
  2. ``ssm:GetParameter`` the stored access code (SecureString, decrypted) and
     ``hmac.compare_digest`` it against the submitted code -- a constant-time
     compare so a wrong code leaks no timing signal. On a MISMATCH return 403
     with a generic body and make NO Cognito call (the code gate is the only
     thing standing between the public and a real token; never probe Cognito on
     a bad code -- no create, no auth, only the cheap SSM code read).
  3. On a MATCH, mint a fresh ephemeral user and auth as it:
       a. Build an email-format username (the pool uses email as the username
          attribute) with an OBVIOUS cleanup PREFIX so the synthetic judges are
          trivially greppable/sweepable:
          ``{DEMO_USER_PREFIX}{uuid4().hex}@{DEMO_EMAIL_DOMAIN}``.
       b. Generate a strong random throwaway password satisfying the pool policy.
       c. ``admin_create_user`` with ``MessageAction="SUPPRESS"`` (the synthetic
          .invalid domain never delivers; suppress avoids a bounced email).
       d. ``admin_set_user_password`` ``Permanent=True`` -- confirms the user AND
          sets the password in one call (no FORCE_CHANGE_PASSWORD challenge).
       e. ``admin_initiate_auth`` ``ADMIN_USER_PASSWORD_AUTH`` for that user. The
          public app client has NO client secret (ClientSecret null), so there
          is NO SECRET_HASH to send.
  4. Return 200 ``{id_token, access_token, refresh_token, expires_in}`` lifted
     from ``AuthenticationResult``.

The throwaway password is NEVER stored anywhere: the client re-auths via the
returned refresh token, not the password, so the password lives only for the
duration of this invocation. The Lambda's IAM role can
``cognito-idp:AdminCreateUser`` / ``AdminSetUserPassword`` / ``AdminInitiateAuth``
on the one pool, ``ssm:GetParameter`` on ``/grace2/demo-access-code`` (+
``kms:Decrypt`` for the SecureString CMK), and CloudWatch logs on its own group
-- nothing else.

FAIL CLOSED: any error in the create/setpw/auth path returns 500 (never a 200);
a wrong/missing/malformed code returns 403 (no oracle). CORS mirrors the wake-API
config (allow_origins ["*"], methods POST/OPTIONS, header content-type) on ALL of
200/403/500, and the OPTIONS preflight is handled directly. Response shape mirrors
the view_sign handler (API Gateway payload format 2.0 proxy response). No
third-party deps -- boto3 only (Lambda runtime).
"""

from __future__ import annotations

import hmac
import json
import logging
import os
import secrets
from typing import Any
from uuid import uuid4

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

#: SSM SecureString parameter name for the shared access code. The VALUE is set
#: out-of-band by NATE at cutover (NOT created in Terraform); this Lambda only
#: reads it by name. There is NO shared user password anymore -- each code-entry
#: mints its own throwaway credential.
SSM_CODE_PARAM = os.environ["SSM_CODE_PARAM"]  # /grace2/demo-access-code

#: Ephemeral-user naming. PREFIX is an OBVIOUS cleanup marker so a sweep can
#: find/disable every synthetic judge; EMAIL_DOMAIN is a non-deliverable
#: synthetic domain (the pool keys on email, so the username must be email-form).
DEMO_USER_PREFIX = os.environ.get("DEMO_USER_PREFIX", "trid3nt-judge-")
DEMO_EMAIL_DOMAIN = os.environ.get("DEMO_EMAIL_DOMAIN", "demo.trident.invalid")

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


def _new_username() -> str:
    """Mint a fresh email-format username carrying the cleanup PREFIX.

    The pool keys on the email attribute, so the username must be email-shaped.
    uuid4 hex gives collision-free uniqueness per code-entry; the synthetic
    .invalid domain never delivers mail.
    """
    return f"{DEMO_USER_PREFIX}{uuid4().hex}@{DEMO_EMAIL_DOMAIN}"


def _new_password() -> str:
    """Generate a strong throwaway password satisfying the pool policy.

    Cognito's default policy needs >=8 chars with upper + lower + number (and
    here also a symbol). ``token_urlsafe`` supplies entropy + mixed case +
    digits; the ``"Aa1!"`` suffix GUARANTEES every required class is present so
    a token that happens to lack (say) an uppercase char still passes policy.
    The value is never stored -- it lives only for this invocation.
    """
    return secrets.token_urlsafe(18) + "Aa1!"


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
    FRESH ephemeral Cognito user's token set. Wrong code -> 403 (no Cognito
    call); correct code -> mint user + 200 with the token passthrough."""
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
        # Code mismatch -- generic 403, NO Cognito call (no create, no auth).
        logger.info("demo-token: access code mismatch")
        return _response(403, {"error": "invalid access code"})

    # Code OK -> mint a fresh ephemeral user, set its password, and auth as it.
    # Any failure here is a SERVER problem (the code already matched), so fail
    # closed with a generic 500 -- never a 200, never leak the cause.
    username = _new_username()
    password = _new_password()

    try:
        _cognito.admin_create_user(
            UserPoolId=COGNITO_USER_POOL_ID,
            Username=username,
            # Synthetic .invalid domain never delivers; suppress the welcome
            # email so Cognito does not attempt (and bounce) a send.
            MessageAction="SUPPRESS",
            UserAttributes=[
                {"Name": "email", "Value": username},
                {"Name": "email_verified", "Value": "true"},
            ],
        )

        _cognito.admin_set_user_password(
            UserPoolId=COGNITO_USER_POOL_ID,
            Username=username,
            Password=password,
            # Permanent=True confirms the user AND skips the
            # FORCE_CHANGE_PASSWORD challenge so the auth below returns tokens.
            Permanent=True,
        )

        auth = _cognito.admin_initiate_auth(
            UserPoolId=COGNITO_USER_POOL_ID,
            ClientId=GRACE2_COGNITO_CLIENT_ID,
            AuthFlow="ADMIN_USER_PASSWORD_AUTH",
            AuthParameters={
                "USERNAME": username,
                "PASSWORD": password,
            },
        )
    except ClientError as exc:
        logger.exception("ephemeral-user mint/auth failed: %s", exc)
        return _response(500, {"error": "demo sign-in unavailable"})
    except Exception as exc:  # noqa: BLE001
        logger.exception("Unexpected ephemeral-user mint/auth error: %s", exc)
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
