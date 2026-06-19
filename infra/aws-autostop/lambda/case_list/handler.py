"""Cases-LIST Lambda for GRACE-2 "browse your Cases with the agent box OFF".

Fronted by the EXISTING wake API Gateway HTTP API (a third route,
``GET /case-list``, alongside ``POST /wake`` and ``GET /case-view-url``). The web
client calls it on the cold-open landing path so the left rail renders the
signed-in user's Cases WITHOUT the agent EC2 box being awake -- pen (agent) off,
paper (case list) still readable.

AUTH CONTRACT (the only thing this Lambda decides):
  - SIGNED-IN ONLY. A valid ``Authorization: Bearer <Cognito ID token>`` that
    verifies (RS256/JWKS by kid, iss = the pool issuer, aud = the app client id,
    token_use == "id", exp valid) yields the verified ``uid``. We list ONLY that
    uid's own Cases -- never another user's.
  - NO TOKEN / INVALID TOKEN / NO POOL CONFIGURED / TABLE UNSET -> HTTP **200**
    with an EMPTY case list. NEVER 401, NEVER 403, and NEVER another user's
    Cases. The empty-list-on-anon posture keeps the cold-open path a clean
    no-surprises read: an unauthenticated browser simply sees zero Cases (it
    falls back to the live WS list once the agent wakes).

Owner scoping mirrors ``Persistence.list_cases_for_user``
(services/agent/src/grace2_agent/persistence.py): query BOTH the
``user_id-index`` and ``owner_user_id-index`` GSIs for the verified uid, union
by ``_id``, exclude tombstones (``status in (deleted, archived)``), and marshal
each doc into a ``CaseSummary`` (rename ``_id -> case_id``, drop the user-link
fields, coerce DynamoDB ``Decimal`` back to int/float). The result matches the
``CaseListEnvelopePayload`` contract (packages/contracts case.py): ``{
"envelope_type": "case-list", "cases": [...] }``.

The Lambda's IAM role can ONLY ``dynamodb:Query`` + ``dynamodb:GetItem`` on the
cases table ARN and its ``/index/*`` GSIs -- no PutItem, no other table, no S3,
no EC2.

Cognito verification is ported VERBATIM from the view-signer + wake handlers
(``cognito_verify`` + the JWKS helpers) so all three stay byte-compatible: same
issuer/aud/token_use/exp rules, same fail-closed-to-None posture. The agent
module is NOT importable from the Lambda (different deploy unit), so the logic is
duplicated here deliberately; keep the three copies in sync.

Deps beyond the Lambda runtime's boto3: PyJWT[crypto] (RS256 verify) + requests
(JWKS fetch). They are pip-installed into this directory at package time by the
OpenTofu ``null_resource`` + ``archive_file`` in main.tf (mirrors view_sign).
"""

from __future__ import annotations

import json
import logging
import os
import threading
from decimal import Decimal
from typing import Any

import boto3
from botocore.config import Config as BotoConfig

logger = logging.getLogger()
logger.setLevel(logging.INFO)

# --------------------------------------------------------------------------- #
# Config (env-driven; read at module load -- Lambda env is fixed per deploy).
# --------------------------------------------------------------------------- #

REGION = os.environ.get("AWS_REGION", "us-west-2")

#: DynamoDB cases table (mirrors GRACE2_DYNAMO_TABLE_PREFIX + "cases" on the
#: agent side; defaults to the live table name). UNSET/empty -> the handler short
#: -circuits to an empty list (never errors), so the cold-open path stays safe in
#: a deployment that has not wired the table.
CASES_TABLE = os.environ.get("CASES_TABLE", "grace2_cases").strip()

#: GSI names on the cases table -- owner-scoped listing avoids a full Scan. These
#: match the live table (verified) AND ``_TABLE_GSIS`` in dynamo_backend.py.
USER_ID_INDEX = "user_id-index"
OWNER_USER_ID_INDEX = "owner_user_id-index"

#: Tombstone statuses excluded from the wire (mirrors list_cases_for_user).
_TOMBSTONE_STATUSES = {"deleted", "archived"}

#: CaseSummary fields the wire envelope carries (mirrors
#: ``CaseSummary.model_fields`` in packages/contracts case.py). Storage-only
#: fields (``user_id`` / ``owner_user_id`` / ``_id`` / ``deleted_at`` / etc.) are
#: dropped during marshal so the payload matches the contract exactly.
_CASE_SUMMARY_FIELDS = {
    "schema_version",
    "case_id",
    "title",
    "created_at",
    "updated_at",
    "status",
    "bbox",
    "primary_hazard",
    "layer_summary",
    "loaded_layer_summaries",
    "qgs_project_uri",
}

# Cognito -- mirrors auth_handshake.py / view_sign / wake env names. UNSET pool ->
# the verifier returns None for every token (anonymous fallback -> empty list).
COGNITO_POOL_ENV = "GRACE2_COGNITO_USER_POOL_ID"
COGNITO_CLIENT_ENV = "GRACE2_COGNITO_CLIENT_ID"

#: Clock-skew leeway (seconds) for exp validation. Matches the other handlers.
_JWT_LEEWAY_S = 60
#: HTTPS timeout (seconds) for the public JWKS fetch.
_JWKS_FETCH_TIMEOUT_S = 5.0

# Bound the DynamoDB calls so a stalled table never pins the Lambda to its 15s
# timeout -- fail fast to an empty list instead (the cold-open path is best
# -effort; the live WS list is the source of truth once the agent wakes).
_ddb = boto3.resource(
    "dynamodb",
    region_name=REGION,
    config=BotoConfig(
        connect_timeout=2,
        read_timeout=3,
        retries={"max_attempts": 2, "mode": "standard"},
    ),
)

_CORS_HEADERS = {
    "Content-Type": "application/json",
    "Access-Control-Allow-Origin": "*",
    "Access-Control-Allow-Methods": "GET, OPTIONS",
    "Access-Control-Allow-Headers": "Content-Type, Authorization",
    # The list reflects live persistence; never cache a stale view.
    "Cache-Control": "no-store",
}


def _response(status: int, body: dict) -> dict:
    """Build an API Gateway (payload format 2.0) proxy response."""
    return {
        "statusCode": status,
        "headers": _CORS_HEADERS,
        "body": json.dumps(body, separators=(",", ":")),
    }


def _empty_list() -> dict:
    """The signed-out / unset-table / error fallback: 200 with zero Cases.

    Per the auth contract this is NEVER a 401/403 -- an unauthenticated browser
    simply sees an empty rail and falls back to the live WS list on wake.
    """
    return _response(200, {"envelope_type": "case-list", "cases": []})


# --------------------------------------------------------------------------- #
# Cognito verification (ported VERBATIM from view_sign/wake -- keep in sync).
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
    except Exception as exc:  # noqa: BLE001 -- network/parse failure is normal
        logger.info("JWKS fetch failed for %s: %s", issuer, type(exc).__name__)
        return cached.get(kid) if cached else None
    with _jwks_lock:
        _jwks_cache[issuer] = fresh
    return fresh.get(kid)


def cognito_verify(token: str) -> dict[str, Any] | None:
    """Verify a Cognito ID token. Returns claims dict on success, None on any
    failure (invalid/expired/wrong-aud, or no pool configured -> anonymous)."""
    pool_id = os.environ.get(COGNITO_POOL_ENV, "").strip()
    if not pool_id:
        # Master gate: no pool configured -> anonymous fallback.
        return None
    client_id = os.environ.get(COGNITO_CLIENT_ENV, "").strip()
    region = _cognito_region()
    issuer = _cognito_issuer(region, pool_id)

    try:
        import jwt  # PyJWT[crypto] -- packaged dep
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
    except Exception as exc:  # noqa: BLE001 -- verification failure is normal
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


# --------------------------------------------------------------------------- #
# DynamoDB read + marshal.
# --------------------------------------------------------------------------- #


def _from_ddb(value: Any) -> Any:
    """Recursively coerce a DynamoDB-resource value back to JSON-shaped form.

    Ported from dynamo_backend._from_ddb: integral Decimal -> int, fractional
    Decimal -> float, String Set -> list, recursing dict/list. The boto3
    resource API returns numbers as ``Decimal``, which ``json.dumps`` cannot
    serialize, so this MUST run before building the response body.
    """
    if isinstance(value, Decimal):
        if value == value.to_integral_value():
            return int(value)
        return float(value)
    if isinstance(value, dict):
        return {k: _from_ddb(v) for k, v in value.items()}
    if isinstance(value, (list, tuple)):
        return [_from_ddb(v) for v in value]
    if isinstance(value, (set, frozenset)):
        return [_from_ddb(v) for v in value]
    return value


def _query_gsi(table, index_name: str, attr: str, value: str) -> list[dict]:
    """Query one GSI by a single equality, paginating, JSON-shaped out.

    Mirrors dynamo_backend._query_gsi. On ANY error returns an empty list -- the
    cold-open path must never surface a 500; a partial/failed read degrades to
    fewer (or zero) Cases, and the live WS list reconciles on wake.
    """
    from boto3.dynamodb.conditions import Key

    items: list[dict] = []
    kwargs: dict[str, Any] = {
        "IndexName": index_name,
        "KeyConditionExpression": Key(attr).eq(value),
    }
    try:
        while True:
            resp = table.query(**kwargs)
            items.extend(_from_ddb(it) for it in resp.get("Items", []))
            lek = resp.get("LastEvaluatedKey")
            if not lek:
                break
            kwargs["ExclusiveStartKey"] = lek
    except Exception as exc:  # noqa: BLE001 -- degrade to a partial/empty read
        logger.info("GSI query failed (%s) on %s: %s", index_name, attr, type(exc).__name__)
    return items


def _doc_to_case_summary(doc: dict) -> dict:
    """Marshal a stored cases document into a CaseSummary-shaped dict.

    Mirrors ``Persistence._doc_to_case_summary``: rename ``_id -> case_id``,
    drop the user-link fields (``user_id`` / ``owner_user_id``) and any other
    storage-only field the contract envelope doesn't carry, keeping only the
    ``CaseSummary`` field set. ``_from_ddb`` has already coerced Decimals at the
    query boundary, so values are JSON-ready scalars/containers here.
    """
    normalized: dict[str, Any] = {}
    for k, v in doc.items():
        if k == "_id":
            continue
        if k in {"user_id", "owner_user_id"}:
            continue
        if k not in _CASE_SUMMARY_FIELDS:
            continue
        normalized[k] = v
    if "case_id" not in normalized and "_id" in doc:
        normalized["case_id"] = doc["_id"]
    return normalized


def _list_cases_for_uid(uid: str) -> list[dict]:
    """Owner-scoped Case list for a verified uid (mirrors list_cases_for_user).

    Query BOTH GSIs for the uid, union by ``_id`` (a doc projected into both
    indexes appears once), exclude tombstones, marshal each survivor. Returns
    marshaled CaseSummary dicts.
    """
    table = _ddb.Table(CASES_TABLE)

    merged: dict[Any, dict] = {}
    for index_name, attr in (
        (USER_ID_INDEX, "user_id"),
        (OWNER_USER_ID_INDEX, "owner_user_id"),
    ):
        for doc in _query_gsi(table, index_name, attr, uid):
            did = doc.get("_id")
            merged[did if did is not None else id(doc)] = doc

    cases: list[dict] = []
    for doc in merged.values():
        # job-0267 parity: tombstones never reach the wire. A doc with no
        # ``status`` field is live by definition (CaseSummary.status defaults to
        # "active"), so only an explicit deleted/archived is excluded.
        if doc.get("status") in _TOMBSTONE_STATUSES:
            continue
        cases.append(_doc_to_case_summary(doc))
    return cases


# --------------------------------------------------------------------------- #
# Entrypoint.
# --------------------------------------------------------------------------- #


def handler(event, context):  # noqa: ANN001, ARG001
    """API Gateway HTTP entrypoint. Returns the signed-in user's Case list for
    the cold-open path; anonymous / unset-table / error -> 200 empty list."""
    if not isinstance(event, dict):
        event = {}

    method = event.get("requestContext", {}).get("http", {}).get("method", "")
    if method == "OPTIONS":
        return _response(200, {"ok": True})

    # Unset/empty table -> the cold-open path is not wired in this deployment.
    # Return an empty list rather than erroring (anonymous-safe contract).
    if not CASES_TABLE:
        return _empty_list()

    # Resolve sign-in. No/invalid token (or no pool) -> empty list, NEVER 401.
    token = _extract_bearer(event)
    claims = cognito_verify(token) if token else None
    if claims is None:
        return _empty_list()

    uid = claims.get("uid")
    if not uid:
        return _empty_list()

    try:
        cases = _list_cases_for_uid(uid)
    except Exception:  # noqa: BLE001 -- never 500 the cold-open list path
        logger.exception("case-list failed for uid; returning empty list")
        return _empty_list()

    return _response(200, {"envelope_type": "case-list", "cases": cases})
