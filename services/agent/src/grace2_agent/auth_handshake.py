"""Firebase Auth verification + Persistence wiring for WS connect (job-0122).

Implements the agent-side of the H.5 session-validation handshake:

1. On WebSocket connect, the client sends an ``auth-token`` envelope carrying
   its Firebase ID token (Appendix H.5).
2. This module verifies the token via the Firebase Admin SDK
   (``firebase_admin.auth.verify_id_token``), resolves the Firebase ``uid`` to
   the corresponding ``UserDocument._id`` via the FR-MP-1 Persistence
   interface, and auto-provisions the user record on first authenticated
   connect (H.5 step 3).
3. If verification fails OR no token arrives within 5 seconds, this module
   provisions an **ephemeral anonymous user** (H.3 anonymous-fallback path)
   — anonymous users have a stable ``UserDocument._id`` ULID, no
   ``firebase_uid``, and ``is_active=True``. Cases they create flow through
   the normal ``owner_user_id`` ownership rule (H.2).

The module is **transport-agnostic** — it does not touch the WebSocket
itself; ``server.py`` reads / writes envelopes and calls the functions here
for the verification + provisioning logic. This keeps the handshake testable
without standing up a real socket and makes mocking trivial.

Firebase Admin SDK initialization happens once at agent startup (``main.py``
via ``init_firebase_admin``). Re-initialization is idempotent —
``firebase_admin.initialize_app`` raises ``ValueError`` on second call, which
we swallow so test contexts can call ``init_firebase_admin`` repeatedly.

Invariants this module is responsible for:

- **Decision F (wire isolation).** The raw token never persists. It is
  consumed by ``verify_id_token`` and discarded; only ``firebase_uid`` /
  ``user_id`` / ``tier`` survive past this module.
- **Invariant 9 (no cost theater).** No cost / quota / spend surfaces.
- **MCP canonical persistence (job-0115).** All ``UserDocument`` CRUD goes
  through ``Persistence.get_user_by_firebase_uid`` /
  ``Persistence.upsert_user``. No direct PyMongo driver.

SRS references:

- Appendix H.5 — session validation (this is the agent-side implementation).
- Appendix H.3 — anonymous fallback (5-second token-arrival timeout).
- Appendix H.4 — tier claim resolution (``free`` default).
- FR-AS-5 — WebSocket server speaks Appendix A (the handshake is now part of
  A.5 Connection Lifecycle).
"""

from __future__ import annotations

import logging
import os
from dataclasses import dataclass
from typing import Any, Awaitable, Callable

from grace2_contracts.auth import AuthAckEnvelope, AuthTokenEnvelope, TierClaim
from grace2_contracts.common import new_ulid, now_utc
from grace2_contracts.user import User

from .persistence import Persistence

logger = logging.getLogger("grace2_agent.auth_handshake")

#: Default time the agent waits for ``auth-token`` before falling through to
#: the anonymous-fallback path (H.3). Override via env for ops flexibility.
DEFAULT_AUTH_TOKEN_TIMEOUT_S: float = float(
    os.environ.get("GRACE2_AUTH_TOKEN_TIMEOUT_S", "5.0")
)

#: Firebase Admin SDK init flag — flips True after a successful initialize_app.
_FIREBASE_INITIALIZED: bool = False


def init_firebase_admin() -> bool:
    """Initialize the Firebase Admin SDK using GCP ADC.

    Returns True if Firebase Admin is available + initialized, False otherwise.

    Idempotent: the second call is a no-op. If ``firebase_admin`` is not
    installed (e.g. CI without the dep), this returns False without raising —
    the handshake then falls through to the anonymous-fallback path for every
    connect.

    Per Appendix H.1 + Decision E, GRACE-2 uses GCP Application Default
    Credentials — no separate service-account JSON in v0.1.
    """
    global _FIREBASE_INITIALIZED
    if _FIREBASE_INITIALIZED:
        return True
    try:
        import firebase_admin
        from firebase_admin import credentials

        # ADC: firebase_admin picks up GOOGLE_APPLICATION_CREDENTIALS env, or
        # the gcloud ADC file, or the Cloud Run service-account identity.
        try:
            firebase_admin.initialize_app(credentials.ApplicationDefault())
        except ValueError as exc:
            # Already initialized (test context, double-import); treat as
            # success.
            if "already exists" in str(exc).lower() or "default app" in str(exc).lower():
                logger.debug("firebase_admin already initialized; skipping")
            else:
                raise
        _FIREBASE_INITIALIZED = True
        logger.info("firebase_admin initialized via ADC")
        return True
    except ImportError:
        logger.info(
            "firebase_admin not installed; anonymous-fallback only "
            "(install with: pip install firebase-admin)"
        )
        return False
    except Exception as exc:  # noqa: BLE001 — startup must not abort
        logger.warning("firebase_admin init failed (%s); anonymous-fallback only", exc)
        return False


def _verify_id_token_sync(token: str) -> dict[str, Any] | None:
    """Call ``firebase_admin.auth.verify_id_token`` defensively.

    Returns the decoded claims dict on success, ``None`` on any failure
    (invalid / expired / revoked token, or firebase_admin not installed).
    Logs the failure reason at ``INFO`` — verification failures are an
    expected path (anonymous fallback), not an error.
    """
    if not _FIREBASE_INITIALIZED:
        return None
    try:
        from firebase_admin import auth as fb_auth

        # check_revoked=True per Appendix H.5 — also catches account-deletion
        # tombstoning.
        return fb_auth.verify_id_token(token, check_revoked=True)
    except Exception as exc:  # noqa: BLE001 — verification failure is normal
        logger.info("verify_id_token failed: %s", type(exc).__name__)
        return None


# --------------------------------------------------------------------------- #
# Public surface
# --------------------------------------------------------------------------- #


#: Hook for tests: replace ``_verify_id_token_sync`` with a fake. The
#: signature is ``(token: str) -> dict | None``. Tests set this to a lambda
#: that returns a fixed claims dict; production keeps the default.
_verify_id_token_hook: Callable[[str], dict[str, Any] | None] = _verify_id_token_sync


def set_verify_hook(hook: Callable[[str], dict[str, Any] | None]) -> None:
    """Replace the token-verification hook (test seam).

    The hook signature matches the synchronous Firebase Admin call. Pass
    ``None`` to restore the default (real Firebase verification).
    """
    global _verify_id_token_hook
    _verify_id_token_hook = hook or _verify_id_token_sync


@dataclass
class AuthResult:
    """Outcome of the connect handshake.

    Fields:
    - ``user`` — the resolved ``User`` (always populated; anonymous users get
      a fresh ephemeral User with ``firebase_uid=None``).
    - ``firebase_uid`` — the verified Firebase UID, or ``None`` on anonymous
      fallback.
    - ``is_anonymous`` — True if the user is anonymous (no Firebase
      verification).
    - ``tier`` — the H.4 tier capability claim. Default ``"free"``.
    """

    user: User
    firebase_uid: str | None
    is_anonymous: bool
    tier: TierClaim


async def authenticate_token(
    token_envelope: AuthTokenEnvelope | None,
    persistence: Persistence | None,
) -> AuthResult:
    """Resolve an ``AuthTokenEnvelope`` to a concrete ``User`` (H.5 + H.3).

    Branches:

    1. **Valid token + verification succeeds.** Resolve ``firebase_uid`` to
       a ``UserDocument`` via ``Persistence.get_user_by_firebase_uid``. If
       no user exists, auto-provision via ``Persistence.upsert_user`` (H.5
       step 3). Tier defaults to ``"free"`` if no ``tier`` claim is present
       on the JWT.

    2. **Missing / empty / invalid token, anonymous hint provided.** When
       the envelope carries ``anonymous_user_id`` and the lookup finds an
       existing ``UserDocument`` with ``is_anonymous=True``, re-bind the
       same User (job-0172 Part C). This is the sticky anonymous path that
       prevents page-refresh from minting a fresh user every reconnect —
       the persisted Cases stay reachable across browser reloads.

    3. **Missing / empty / invalid token, no usable hint.** Provision an
       ephemeral anonymous user with a fresh ULID, ``firebase_uid=None``,
       ``is_active=True``, ``is_anonymous=True``. If persistence is
       provisioned, the user is upserted; otherwise the anonymous user
       stays in-memory for the session (M1 substrate path).

    Always returns an ``AuthResult`` — verification failure is a path, not
    an exception.

    **job-0252b — gate-ordering hygiene.** When the ``AUTH_REQUIRED`` gate
    is engaged (``grace2_agent.auth.auth_required()``), every anonymous
    *resolution* on this function's failure paths returns an **unprovisioned**
    anonymous ``AuthResult`` — NO write to the users collection (and no
    sticky-reuse read). The server gate (``server._handle_auth_token`` /
    ``_ensure_auth_handshake``) inspects ``result.is_anonymous`` and rejects
    the socket (A.5 4401 + A.6 ``AUTH_FAILED``) WITHOUT ever persisting a
    junk anonymous row. Provisioning a row only to reject the connection a
    moment later is unbounded junk-row growth + write amplification under
    hostile/bot load. When the gate is OFF (dev/demo), behavior is byte-
    identical to before this change: anonymous provisioning, sticky-anon
    reuse, and the auth-ack all run exactly as the Wave 2 handshake did.
    """
    # When the production sign-in gate is engaged, an anonymous resolution is
    # destined for rejection by the server gate — so do NOT provision/persist
    # (or even read for sticky-reuse). We hand back an unprovisioned anonymous
    # AuthResult; the server reads is_anonymous and closes 4401. Read at call
    # time per ``auth.auth_required`` so dev (no env) is untouched.
    from .auth import auth_required  # local import: avoid an import cycle.

    gate_on = auth_required()

    # 1. Anonymous fallback: no envelope, empty token, or no firebase_admin.
    token_str = (token_envelope.token if token_envelope else "").strip()
    if not token_str:
        # Gate ON: short-circuit BEFORE the sticky-reuse read and BEFORE any
        # provisioning write — zero collection access on the rejected path.
        if gate_on:
            return await _anonymous_result_no_persist()
        anon_hint = (
            token_envelope.anonymous_user_id if token_envelope else None
        )
        if anon_hint and persistence is not None:
            existing = await _try_reuse_anonymous_user(persistence, anon_hint)
            if existing is not None:
                logger.info(
                    "anonymous reuse: rebound user_id=%s (sticky)", existing.user_id
                )
                return AuthResult(
                    user=existing,
                    firebase_uid=None,
                    is_anonymous=True,
                    tier="free",
                )
        return await _provision_anonymous_user(persistence)

    # 2. Verify the token.
    claims = _verify_id_token_hook(token_str)
    if not claims:
        logger.info("auth-token verification failed; falling back to anonymous")
        if gate_on:
            return await _anonymous_result_no_persist()
        return await _provision_anonymous_user(persistence)

    firebase_uid = claims.get("uid")
    if not firebase_uid:
        # JWT decoded but uid missing — treat as anonymous.
        logger.warning("verified claims missing 'uid' field; anonymous fallback")
        if gate_on:
            return await _anonymous_result_no_persist()
        return await _provision_anonymous_user(persistence)

    # H.4 tier resolution. Default "free" when no claim is present.
    tier_claim = claims.get("tier", "free")
    if tier_claim not in ("free", "pro", "enterprise"):
        # Unknown claim — fall back to free (H.4 v0.1 default).
        logger.warning(
            "unknown tier claim %r; defaulting to 'free'", tier_claim
        )
        tier_claim = "free"

    # 3. Resolve to a UserDocument via Persistence (job-0115 substrate).
    user = await _resolve_or_provision_user(
        persistence,
        firebase_uid=firebase_uid,
        email=claims.get("email"),
        display_name=claims.get("name") or claims.get("display_name"),
    )

    return AuthResult(
        user=user,
        firebase_uid=firebase_uid,
        is_anonymous=False,
        tier=tier_claim,
    )


async def _provision_anonymous_user(persistence: Persistence | None) -> AuthResult:
    """Provision an ephemeral anonymous ``User`` per H.3 fallback.

    The anonymous User has:
    - a fresh ULID ``user_id``,
    - ``firebase_uid=None`` (no Firebase binding),
    - ``email=None`` / ``display_name=None``,
    - ``is_active=True`` (Cases CAN be created; web prompts upgrade at save),
    - default ``prefs={}``.

    If persistence is provisioned, the user is upserted so the
    ``owner_user_id`` cascade rule (H.2) has a stable target. If
    persistence is unbound (no MCP env), the anonymous user lives in-memory
    only — the M1 substrate path keeps working.

    Returns ``AuthResult`` with ``is_anonymous=True``, ``tier="free"``.
    """
    user = User(
        user_id=new_ulid(),
        firebase_uid=None,
        email=None,
        display_name=None,
        created_at=now_utc(),
        is_active=True,
        prefs={},
        is_anonymous=True,  # job-0172 Part C: pin the H.3 fallback as anonymous.
    )
    if persistence is not None:
        try:
            await persistence.upsert_user(user)
        except Exception as exc:  # noqa: BLE001 — best-effort
            logger.warning(
                "anonymous user upsert failed (continuing in-memory): %s", exc
            )
    return AuthResult(
        user=user,
        firebase_uid=None,
        is_anonymous=True,
        tier="free",
    )


async def _anonymous_result_no_persist() -> AuthResult:
    """Build an anonymous ``AuthResult`` WITHOUT touching any collection.

    job-0252b: the ``AUTH_REQUIRED`` gate-rejected path. The server inspects
    ``is_anonymous`` and closes the socket (A.5 4401 + A.6 ``AUTH_FAILED``)
    immediately — there is no point provisioning a users row that is never
    bound to a session, and doing so amplifies writes / grows junk rows under
    hostile connection load. So the ephemeral User stays purely in-memory: a
    fresh ULID, ``firebase_uid=None``, never persisted.

    This is exactly ``_provision_anonymous_user(None)`` (the unbound-
    persistence branch), but expressed as its own intent-named helper so the
    "no write on the rejected path" property is explicit at the call sites
    and cannot regress to passing a live ``persistence`` by accident.

    The function is ``async`` to keep the call sites uniform with
    ``_provision_anonymous_user`` even though it never awaits.
    """
    return await _provision_anonymous_user(None)


async def _try_reuse_anonymous_user(
    persistence: Persistence,
    anonymous_user_id: str,
) -> User | None:
    """Look up the User by ULID and reuse iff it's an anonymous record.

    job-0172 Part C: the sticky-anonymous path. Returns the existing User
    only when (a) a record exists for ``anonymous_user_id`` and (b) that
    record is marked ``is_anonymous=True``. Returns ``None`` to fall
    through to fresh-user provisioning when either condition fails.

    Why the is_anonymous gate: an attacker could fish a known authenticated
    User id from a log and replay it; we MUST NOT re-bind a Firebase-verified
    User without the actual JWT. Anonymous Users have no credential, so
    re-binding them is the entire point — the id IS the only identifier
    they ever had.
    """
    try:
        existing = await persistence.get_user_by_id(anonymous_user_id)
    except Exception as exc:  # noqa: BLE001 — best-effort: fall back to fresh
        logger.warning(
            "anonymous reuse: get_user_by_id(%s) failed (%s); minting fresh",
            anonymous_user_id,
            exc,
        )
        return None
    if existing is None:
        logger.info(
            "anonymous reuse: hint %s not found; minting fresh", anonymous_user_id
        )
        return None
    if not existing.is_anonymous:
        # Forbid re-binding to a Firebase-verified record without the JWT.
        logger.warning(
            "anonymous reuse: hint %s belongs to a non-anonymous user; "
            "rejecting (minting fresh anonymous)",
            anonymous_user_id,
        )
        return None
    if not existing.is_active:
        logger.info(
            "anonymous reuse: hint %s is_active=False; minting fresh",
            anonymous_user_id,
        )
        return None
    return existing


async def _resolve_or_provision_user(
    persistence: Persistence | None,
    *,
    firebase_uid: str,
    email: str | None,
    display_name: str | None,
) -> User:
    """Look up the User by ``firebase_uid``, auto-create on first connect.

    H.5 step 3: if no ``UserDocument`` exists for the ``uid``, the resolver
    creates one with default fields. Idempotent — a second connect with the
    same uid returns the existing User.

    If persistence is unbound (no MCP env), returns a fresh in-memory User
    so the session-bind path keeps working — the M1 substrate fallback.
    """
    if persistence is None:
        # No persistence — keep an in-memory User so server.py can still
        # bind a session. This is the local-dev / CI path.
        return User(
            user_id=new_ulid(),
            firebase_uid=firebase_uid,
            email=email,
            display_name=display_name,
            created_at=now_utc(),
            is_active=True,
            prefs={},
        )

    existing = await persistence.get_user_by_firebase_uid(firebase_uid)
    if existing is not None:
        return existing

    # First-login auto-provision.
    new_user = User(
        user_id=new_ulid(),
        firebase_uid=firebase_uid,
        email=email,
        display_name=display_name,
        created_at=now_utc(),
        is_active=True,
        prefs={},
    )
    try:
        await persistence.upsert_user(new_user)
        logger.info(
            "auto-provisioned user user_id=%s firebase_uid=%s",
            new_user.user_id,
            firebase_uid,
        )
    except Exception as exc:  # noqa: BLE001
        logger.warning("upsert_user failed (continuing): %s", exc)
    return new_user


def build_auth_ack(result: AuthResult) -> AuthAckEnvelope:
    """Construct the ``auth-ack`` envelope payload for a resolved AuthResult.

    Mirrors only the fields the H.5 ack surfaces — never the raw token
    (Decision F wire isolation). The web client uses this to drive tier-
    gated UI and the anonymous-upgrade prompt.
    """
    return AuthAckEnvelope(
        user_id=result.user.user_id,
        firebase_uid=result.firebase_uid,
        is_anonymous=result.is_anonymous,
        tier=result.tier,
    )


# --------------------------------------------------------------------------- #
# Timeout helper — public so server.py can use the same default constant.
# --------------------------------------------------------------------------- #


def get_auth_token_timeout_s(default: float | None = None) -> float:
    """Return the configured auth-token-arrival timeout (seconds).

    Used by the server connect-handler to bound how long it waits for the
    client's first ``auth-token`` envelope before flipping into the
    anonymous-fallback path. Tests can stub by setting the env var, or pass
    a tighter ``default`` to short-circuit.
    """
    if default is not None:
        return default
    return DEFAULT_AUTH_TOKEN_TIMEOUT_S


__all__ = [
    "AuthResult",
    "DEFAULT_AUTH_TOKEN_TIMEOUT_S",
    "authenticate_token",
    "build_auth_ack",
    "get_auth_token_timeout_s",
    "init_firebase_admin",
    "set_verify_hook",
]
