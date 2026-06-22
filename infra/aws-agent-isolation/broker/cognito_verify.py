"""Cognito ID-token verification for the broker -- REUSES the agent's exact
``auth_handshake.cognito_verify`` so it CANNOT drift.

THE ANTI-DRIFT DISCIPLINE (kickoff: "import or vendor the exact logic so it
cannot drift"):

  1. PRIMARY (import, zero drift): if the ``grace2_agent`` package is importable
     (the broker image installs it -- see broker/Dockerfile + broker/README.md),
     we import ``grace2_agent.auth_handshake.cognito_verify`` DIRECTLY and call
     it. There is then literally ONE implementation in the running process; a
     change to the agent's verifier is a change to the broker's verifier by
     construction. This is the path the broker image takes.

  2. FALLBACK (vendored, drift-GUARDED): if the agent package is not importable
     (e.g. a minimal broker image, or a unit-test env without the agent on the
     path), we use a VENDORED transcription below -- byte-faithful to the agent's
     logic (RS256 vs pool JWKS, iss/aud/token_use=="id"/exp, sub->uid). The
     drift-guard test ``tests/test_cognito_verify_no_drift.py`` imports BOTH the
     real agent function and this vendored one and asserts they agree on the same
     tokens (mocked JWKS) so the fallback can never silently diverge.

Either way the broker verifies with the SAME pool JWKS, the SAME claims rules,
and returns the SAME ``{"uid": sub, ...}`` shape the agent's in-band handshake
expects -- so the broker's verify is a faithful PRE-ROUTING check and the agent's
in-band ``_ensure_auth_handshake`` remains the second, authoritative gate.

Env gate (identical to the agent): GRACE2_COGNITO_USER_POOL_ID unset => every
token verifies to None (anonymous fallback), GRACE2_COGNITO_CLIENT_ID is the
required ``aud``, GRACE2_AWS_REGION/AWS_REGION select the issuer/JWKS region.
"""

from __future__ import annotations

import logging
import os
import threading
from typing import Any

logger = logging.getLogger("grace2.broker.cognito")

# Env names -- IDENTICAL to auth_handshake (so the same task env drives both).
COGNITO_POOL_ENV = "GRACE2_COGNITO_USER_POOL_ID"
COGNITO_CLIENT_ENV = "GRACE2_COGNITO_CLIENT_ID"
COGNITO_REGION_ENV = "GRACE2_AWS_REGION"
_JWT_LEEWAY_S = 60
_JWKS_FETCH_TIMEOUT_S = 5.0


# --------------------------------------------------------------------------- #
# PRIMARY: import the agent's real verifier if available. Resolved once at import.
# --------------------------------------------------------------------------- #
_real_cognito_verify = None
try:  # pragma: no cover - exercised in the broker image, mocked out in tests
    from grace2_agent.auth_handshake import cognito_verify as _real_cognito_verify  # type: ignore
    logger.info("broker cognito_verify: using grace2_agent.auth_handshake (zero-drift import)")
except Exception:  # noqa: BLE001 - agent package not on the path -> vendored fallback
    _real_cognito_verify = None
    logger.info("broker cognito_verify: grace2_agent not importable; using vendored fallback")


# --------------------------------------------------------------------------- #
# FALLBACK: vendored transcription (drift-guarded by the test). Byte-faithful to
# auth_handshake.cognito_verify. Only used when the import above failed.
# --------------------------------------------------------------------------- #
_jwks_cache: dict[str, dict[str, dict[str, Any]]] = {}
_jwks_lock = threading.Lock()


def _cognito_region() -> str:
    return os.environ.get(COGNITO_REGION_ENV) or os.environ.get("AWS_REGION") or "us-west-2"


def _cognito_issuer(region: str, pool_id: str) -> str:
    return f"https://cognito-idp.{region}.amazonaws.com/{pool_id}"


def _fetch_jwks(issuer: str) -> dict[str, dict[str, Any]]:
    import requests  # local import keeps module import cheap

    url = f"{issuer}/.well-known/jwks.json"
    resp = requests.get(url, timeout=_JWKS_FETCH_TIMEOUT_S)
    resp.raise_for_status()
    keys = resp.json().get("keys", [])
    return {k["kid"]: k for k in keys if "kid" in k}


def _get_jwk(issuer: str, kid: str) -> dict[str, Any] | None:
    with _jwks_lock:
        cached = _jwks_cache.get(issuer)
    if cached is not None and kid in cached:
        return cached[kid]
    try:
        fresh = _fetch_jwks(issuer)
    except Exception as exc:  # noqa: BLE001 - network/parse failure is normal
        logger.info("JWKS fetch failed for %s: %s", issuer, type(exc).__name__)
        return cached.get(kid) if cached else None
    with _jwks_lock:
        _jwks_cache[issuer] = fresh
    return fresh.get(kid)


def _vendored_cognito_verify(token: str) -> dict[str, Any] | None:
    """Byte-faithful transcription of auth_handshake.cognito_verify."""
    pool_id = os.environ.get(COGNITO_POOL_ENV, "").strip()
    if not pool_id:
        return None
    client_id = os.environ.get(COGNITO_CLIENT_ENV, "").strip()
    region = _cognito_region()
    issuer = _cognito_issuer(region, pool_id)

    try:
        import jwt  # PyJWT[crypto]
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
            options={"require": ["exp", "iss", "sub"], "verify_aud": False},
        )
        claims = jwt.decode(token, public_key, **decode_kwargs)
    except Exception as exc:  # noqa: BLE001 - verification failure is normal
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


def cognito_verify(token: str) -> dict[str, Any] | None:
    """Verify a Cognito ID token -> {"uid": sub, ...} or None.

    Delegates to the agent's REAL ``cognito_verify`` when the ``grace2_agent``
    package is importable (the broker image path -- zero drift), else to the
    drift-guarded vendored transcription.
    """
    if _real_cognito_verify is not None:
        return _real_cognito_verify(token)
    return _vendored_cognito_verify(token)
