"""View-signer Lambda for GRACE-2 "view a Case with the agent box OFF".

Fronted by the EXISTING wake API Gateway HTTP API (a second route,
``GET /case-view-url``). The web client calls it to obtain a PRE-SIGNED S3 GET
URL for the materialized Case-view snapshot the agent writes to the durable runs
bucket at ``s3://<RUNS_BUCKET>/case-views/{case_id}.json``. With a valid URL the
browser fetches that snapshot directly from S3 (private bucket, pre-signed) and
renders the Case with the agent EC2 box asleep — pen (agent) off, paper (case)
still readable.

AUTH TIERING (the only thing this Lambda decides):
  - SIGNED-IN: an ``Authorization: Bearer <Cognito ID token>`` that verifies
    (RS256/JWKS by kid, iss = the pool issuer, aud = the app client id,
    token_use == "id", exp valid) AND — when the snapshot carries an ``owner``
    that the agent stamped in — matches that owner. A valid signed-in owner gets
    a LONG-lived URL (SIGNED_TTL, default 12h = 43200s), re-issuable on demand =
    effectively unlimited.
  - ANONYMOUS: no token, an invalid/expired token, or no Cognito pool configured
    (the demo default — GRACE2_COGNITO_USER_POOL_ID unset). Gets a SHORT-lived
    URL (ANON_TTL, default 15min = 900s). This is the public-demo path.

The bucket stays PRIVATE — the only way to read the snapshot is a URL this
Lambda signs. The Lambda's IAM role can s3:GetObject ONLY on
``case-views/*`` of the runs bucket (no list, no put, no other prefix, no
DynamoDB, no EC2).

Never 500 on a missing snapshot: a HEAD that 404s returns a typed 404
``{"error": ...}`` so the client can show "this case has no shared view yet"
instead of a server error. The owner check reads the snapshot's S3 OBJECT
METADATA (``owner-user-id``) the agent stamps on ``put_object`` — a single
``head_object`` gives existence AND owner without downloading the body (the body
strips the owner-link fields, so it could never carry the owner). Absent owner
⇒ no owner-gate (the snapshot is treated as shareable, still signed-in vs anon
TTL).

Cognito verification is ported from
``services/agent/src/grace2_agent/auth_handshake.py`` (cognito_verify + JWKS
helpers) so the two stay byte-compatible: same issuer/aud/token_use/exp rules,
same fail-closed-to-None posture. The agent module is NOT importable from the
Lambda (different deploy unit), so the logic is duplicated here deliberately;
keep them in sync.

Deps beyond the Lambda runtime's boto3: PyJWT[crypto] (RS256 verify) + requests
(JWKS fetch). They are pip-installed into this directory at package time by the
OpenTofu ``null_resource`` + ``archive_file`` in main.tf (see RUNBOOK).
"""

from __future__ import annotations

import json
import logging
import os
import threading
import urllib.parse
from typing import Any

import boto3
from botocore.config import Config as BotoConfig
from botocore.exceptions import ClientError

logger = logging.getLogger()
logger.setLevel(logging.INFO)

# --------------------------------------------------------------------------- #
# Config (env-driven; read at module load — Lambda env is fixed per deploy).
# --------------------------------------------------------------------------- #

REGION = os.environ.get("AWS_REGION", "us-west-2")
RUNS_BUCKET = os.environ["RUNS_BUCKET"]
VIEW_PREFIX = "case-views"

#: Long expiry for a verified signed-in owner (seconds). Default 12h.
SIGNED_TTL = int(os.environ.get("SIGNED_TTL", "43200"))
#: Short expiry for the anonymous/demo path (seconds). Default 15min.
ANON_TTL = int(os.environ.get("ANON_TTL", "900"))

# Cognito — mirrors auth_handshake.py env names. UNSET pool ⇒ verifier returns
# None for every token (anonymous fallback; the live demo is unaffected).
COGNITO_POOL_ENV = "GRACE2_COGNITO_USER_POOL_ID"
COGNITO_CLIENT_ENV = "GRACE2_COGNITO_CLIENT_ID"

#: Clock-skew leeway (seconds) for exp validation. Matches auth_handshake.
_JWT_LEEWAY_S = 60
#: HTTPS timeout (seconds) for the public JWKS fetch.
_JWKS_FETCH_TIMEOUT_S = 5.0

# Force SigV4 + the regional endpoint so the pre-signed URL is valid in
# us-west-2 (S3 SigV2 pre-signed URLs are deprecated; some regions reject them).
_s3 = boto3.client(
    "s3",
    region_name=REGION,
    config=BotoConfig(signature_version="s3v4", s3={"addressing_style": "virtual"}),
)

_CORS_HEADERS = {
    "Content-Type": "application/json",
    "Access-Control-Allow-Origin": "*",
    "Access-Control-Allow-Methods": "GET, OPTIONS",
    "Access-Control-Allow-Headers": "Content-Type, Authorization",
    # Each call mints a fresh time-boxed URL; never cache the response.
    "Cache-Control": "no-store",
}


def _response(status: int, body: dict) -> dict:
    """Build an API Gateway (payload format 2.0) proxy response."""
    return {
        "statusCode": status,
        "headers": _CORS_HEADERS,
        "body": json.dumps(body, separators=(",", ":")),
    }


# --------------------------------------------------------------------------- #
# Cognito verification (ported from auth_handshake.py — keep in sync).
# --------------------------------------------------------------------------- #

_jwks_cache: dict[str, dict[str, dict[str, Any]]] = {}
_jwks_lock = threading.Lock()


def _cognito_region() -> str:
    return (
        os.environ.get("GRACE2_AWS_REGION")
        or os.environ.get("AWS_REGION")
        or "us-west-2"
    )


def _cognito_issuer(region: str, pool_id: str) -> str:
    return f"https://cognito-idp.{region}.amazonaws.com/{pool_id}"


def _fetch_jwks(issuer: str) -> dict[str, dict[str, Any]]:
    import requests  # packaged dep

    url = f"{issuer}/.well-known/jwks.json"
    resp = requests.get(url, timeout=_JWKS_FETCH_TIMEOUT_S)
    resp.raise_for_status()
    keys = resp.json().get("keys", [])
    return {k["kid"]: k for k in keys if "kid" in k}


def _get_jwk(issuer: str, kid: str, *, allow_refetch: bool = True) -> dict[str, Any] | None:
    with _jwks_lock:
        cached = _jwks_cache.get(issuer)
    if cached is not None and kid in cached:
        return cached[kid]
    if not allow_refetch:
        return cached.get(kid) if cached else None
    try:
        fresh = _fetch_jwks(issuer)
    except Exception as exc:  # noqa: BLE001 — network/parse failure is normal
        logger.info("JWKS fetch failed for %s: %s", issuer, type(exc).__name__)
        return cached.get(kid) if cached else None
    with _jwks_lock:
        _jwks_cache[issuer] = fresh
    return fresh.get(kid)


def cognito_verify(token: str) -> dict[str, Any] | None:
    """Verify a Cognito ID token. Returns claims dict on success, None on any
    failure (invalid/expired/wrong-aud, or no pool configured ⇒ anonymous)."""
    pool_id = os.environ.get(COGNITO_POOL_ENV, "").strip()
    if not pool_id:
        # Master gate: no pool configured → anonymous fallback.
        return None
    client_id = os.environ.get(COGNITO_CLIENT_ENV, "").strip()
    region = _cognito_region()
    issuer = _cognito_issuer(region, pool_id)

    try:
        import jwt  # PyJWT[crypto] — packaged dep
        from jwt.algorithms import RSAAlgorithm

        header = jwt.get_unverified_header(token)
        kid = header.get("kid")
        if not kid:
            logger.info("Cognito verify: token header missing 'kid'")
            return None
        jwk = _get_jwk(issuer, kid)
        if jwk is None:
            logger.info("Cognito verify: no JWK for kid=%s", kid)
            return None

        public_key = RSAAlgorithm.from_jwk(jwk)

        decode_kwargs: dict[str, Any] = dict(
            algorithms=["RS256"],
            issuer=issuer,
            leeway=_JWT_LEEWAY_S,
            options={
                "require": ["exp", "iss", "sub"],
                "verify_aud": False,  # validated explicitly below
            },
        )
        claims = jwt.decode(token, public_key, **decode_kwargs)
    except Exception as exc:  # noqa: BLE001 — verification failure is normal
        logger.info("Cognito verify failed: %s", type(exc).__name__)
        return None

    if claims.get("token_use") != "id":
        logger.info("Cognito verify: token_use=%r (expected 'id')", claims.get("token_use"))
        return None

    if not client_id or claims.get("aud") != client_id:
        logger.info("Cognito verify: aud mismatch")
        return None

    sub = claims.get("sub")
    if not sub:
        logger.info("Cognito verify: claims missing 'sub'")
        return None

    return {
        "uid": sub,
        "email": claims.get("email"),
        "name": claims.get("name") or claims.get("cognito:username"),
        "tier": claims.get("custom:tier", "free"),
    }


# --------------------------------------------------------------------------- #
# Request helpers.
# --------------------------------------------------------------------------- #


def _extract_bearer(event: dict) -> str | None:
    """Pull the bearer token from the Authorization header (case-insensitive).

    API Gateway payload 2.0 lower-cases header keys, but be defensive.
    """
    headers = event.get("headers") or {}
    raw = None
    for k, v in headers.items():
        if k.lower() == "authorization":
            raw = v
            break
    if not raw:
        return None
    parts = raw.split(None, 1)
    if len(parts) == 2 and parts[0].lower() == "bearer":
        return parts[1].strip()
    # Tolerate a bare token (no scheme).
    return raw.strip() or None


def _query_case_id(event: dict) -> str | None:
    qs = event.get("queryStringParameters") or {}
    cid = (qs.get("case_id") or "").strip()
    return cid or None


def _snapshot_owner(key: str) -> tuple[bool, str | None]:
    """Read the snapshot's stamped owner from S3 OBJECT METADATA (cheap HEAD).

    Adversarial-review fix: the snapshot BODY strips the owner-link fields, so
    the old full-``get_object`` probe could NEVER owner-match (and wastefully
    downloaded the whole snapshot). The agent now carries the owner in S3 object
    metadata (``put_object(Metadata={"owner-user-id": <owner>})``). A
    ``head_object`` gives us BOTH existence AND the owner without downloading the
    body. boto3 lowercases user-metadata keys, so read ``owner-user-id``.

    Returns ``(exists, owner)``:
      - ``exists`` False ⇒ the object is missing (caller returns 404).
      - ``owner`` is the agent-stamped owner id when present, else None
        (no owner-gate; snapshot treated as shareable).

    On any HEAD error other than a clean 404, returns ``(True, None)``
    (fail-open on the OWNER check only — never block signing on a metadata
    hiccup; TTL tiering still applies).
    """
    try:
        resp = _s3.head_object(Bucket=RUNS_BUCKET, Key=key)
    except ClientError as exc:
        error = exc.response.get("Error", {})
        code = error.get("Code", "")
        status = exc.response.get("ResponseMetadata", {}).get("HTTPStatusCode")
        if code in ("NoSuchKey", "404", "NotFound") or status == 404:
            return (False, None)
        # GetObject-only role (no s3:ListBucket): S3 returns 403/AccessDenied
        # for a MISSING key under case-views/* (it hides existence without
        # ListBucket). Since this role DOES hold GetObject on the prefix, a 403
        # here means the snapshot does not exist -> treat as a clean miss (404),
        # NOT a transient error to fail-open on. Otherwise we mint a useless
        # pre-signed URL for a nonexistent object.
        if code in ("403", "AccessDenied", "Forbidden") or status == 403:
            return (False, None)
        logger.info("head_object error (%s) for %s; fail-open on owner", code, key)
        return (True, None)
    except Exception:  # noqa: BLE001
        logger.exception("Unexpected head_object failure for %s; fail-open on owner", key)
        return (True, None)

    # boto3 returns user-defined metadata under ``Metadata`` with lowercased
    # keys (S3 normalizes ``x-amz-meta-*``). Absent ⇒ no owner-gate.
    metadata = resp.get("Metadata") or {}
    owner = metadata.get("owner-user-id")
    return (True, owner or None)


def _presign(key: str, ttl: int) -> str:
    return _s3.generate_presigned_url(
        "get_object",
        Params={"Bucket": RUNS_BUCKET, "Key": key},
        ExpiresIn=ttl,
    )


# --------------------------------------------------------------------------- #
# Entrypoint.
# --------------------------------------------------------------------------- #


def handler(event, context):  # noqa: ANN001, ARG001
    """API Gateway HTTP entrypoint. Issues a pre-signed S3 GET URL for a Case
    view snapshot, tiered by Cognito sign-in (12h owner / 15min anon)."""
    if not isinstance(event, dict):
        event = {}

    method = event.get("requestContext", {}).get("http", {}).get("method", "")
    if method == "OPTIONS":
        return _response(200, {"ok": True})

    case_id = _query_case_id(event)
    if not case_id:
        return _response(400, {"error": "missing required query param 'case_id'"})

    # Defensive: case_id is a path component — reject traversal / separators so a
    # crafted id can never escape the case-views/ prefix.
    if "/" in case_id or ".." in case_id or "\\" in case_id:
        return _response(400, {"error": "invalid case_id"})

    key = f"{VIEW_PREFIX}/{case_id}.json"

    # Resolve sign-in tier.
    token = _extract_bearer(event)
    claims = cognito_verify(token) if token else None
    signed_in = claims is not None

    # Existence + owner check.
    exists, owner = _snapshot_owner(key)
    if not exists:
        return _response(
            404,
            {"error": "no shared view for this case", "case_id": case_id},
        )

    mode = "anon"
    ttl = ANON_TTL
    if signed_in:
        # When the snapshot carries an owner, require a match to grant the
        # signed-in (long) tier. A signed-in non-owner falls back to the anon
        # TTL rather than being denied (the snapshot is still readable; we just
        # don't hand a 12h URL to a non-owner).
        if owner is None or claims.get("uid") == owner:
            mode = "signed"
            ttl = SIGNED_TTL
        else:
            logger.info(
                "Signed-in non-owner for case %s (uid != owner); anon TTL", case_id
            )

    try:
        url = _presign(key, ttl)
    except Exception as exc:  # noqa: BLE001
        logger.exception("generate_presigned_url failed for %s", key)
        return _response(500, {"error": f"could not sign url: {exc}"})

    return _response(200, {"url": url, "expires_in": ttl, "mode": mode})
