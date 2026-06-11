"""Signed-URL minting Cloud Function (sprint-13.5 Stage 1 / job-0251 + job-0251b).

A Cloud Functions gen2, HTTPS-triggered, AUTHENTICATED function that mints a
short-lived GCS V4 signed URL for a layer object, but ONLY after verifying that
the caller (a Firebase-authenticated user) owns the Case the layer belongs to.

Why this exists (sprint-13.5 manifest §3.8 + NFR-S-1): in production the client
must never receive a raw ``gs://`` object path or a public Cloud Run URL. Every
``LayerURI`` is served via a short-lived signed URL minted here. The function is
the single trust boundary between "an authenticated user" and "a GCS object":

  1. Firebase ID token (Authorization: Bearer <token>) is verified server-side.
     The ``uid`` from the *verified* token is the only identity we trust — the
     request body's ``user_id`` carries that same Firebase uid and MUST equal
     it, or we reject (never trust body).
  2. The verified Firebase uid is resolved to the INTERNAL ``users._id`` ULID
     via a ``users``-collection lookup on ``{"firebase_uid": <verified uid>}``
     (sprint-13.5 Decision 10; SRS H.2:42 / H.5:124: the canonical owner
     identity everywhere is the internal ULID minted by the agent's
     ``auth_handshake._resolve_or_provision_user`` — the Firebase uid lives
     only in ``users.firebase_uid``, never in Case owner fields). Fail-closed:
     no users doc for the verified uid → 403 (a Firebase user who has never
     connected to the agent owns nothing); lookup error → 503. The check NEVER
     falls through to a raw Firebase-uid comparison.
  3. Ownership of ``case_id`` is checked against the RESOLVED internal ULID
     (the ``projects`` collection — Case <-> projects 1:1 per FR-MP-5).
  4. Only then do we mint a GCS V4 signed URL for the layer object, with TTL
     clamped to [900, 3600] seconds.

So the documented chain is: token uid == body.user_id → resolve to internal
ULID → ownership check → mint.

Persistence seam note (manifest binding): the agent service reads Cases through
``grace2_agent.persistence.Persistence`` wrapping ``MCPSurfaceTranslator`` over a
stdio ``mongodb-mcp-server`` subprocess. That translator is the *stable surface*
for the long-lived agent process. For THIS function we deliberately do NOT spawn
an MCP stdio sidecar:

  - The function is a short-lived, synchronous HTTP handler. Spawning a Node
    ``mongodb-mcp-server`` subprocess (and tearing it down — see MCP-1 PDEATHSIG
    work) on every cold request would dominate latency and re-introduce the
    sidecar-leak class of bug this sprint is hardening away.
  - The reads are two single-document ``find_one`` calls (users doc by
    ``firebase_uid``, Case doc by ``_id``) — direct PyMongo reads against the
    Atlas URI (from Secret Manager) are the minimal, auditable surface. We
    REUSE the translator's logical contracts exactly: the users lookup mirrors
    ``Persistence.get_user_by_firebase_uid`` (collection ``users``, filter
    ``{"firebase_uid": uid}``, ``_id`` authoritative with ``user_id`` key
    fallback); the Case read uses the same collection name (``projects``), the
    same primary key (``_id`` == ``case_id``), and the same user-link semantics
    (``user_id`` OR ``owner_user_id``) that ``Persistence.list_cases_for_user``
    filters on — compared against the RESOLVED internal ULID, never the raw
    Firebase uid.

  The single behavioral DIFFERENCE, on purpose: this function does NOT honor the
  pre-Auth ``{"user_id": {"$exists": False}}`` backward-compat clause. job-0252
  migrates pre-Auth cases to ``MIGRATION_ANON_UID``; a signing endpoint is a
  hard security boundary and must fail closed — an un-owned (orphan) Case is NOT
  mintable by an arbitrary signed-in user. Likewise ``MIGRATION_ANON_UID``-owned
  Cases are not mintable by ANY Firebase user, by design: no users doc maps a
  ``firebase_uid`` to the migration sentinel, so uid resolution can never
  produce it.

Signing approach (manifest HARD CONSTRAINT): we NEVER create or download a
service-account key. The V4 signature is produced via IAM ``signBlob`` using the
function's attached runtime service-account credentials. ``google-auth``'s
``Credentials.signer`` (an ``iam.Signer`` for the impersonated/attached SA) lets
``blob.generate_signed_url(..., credentials=..., service_account_email=...)``
sign without any key material. The runtime SA needs
``roles/iam.serviceAccountTokenCreator`` on itself for ``signBlob`` — provisioned
in ``signed_urls.tf`` (and called out as an UNBLOCK item if the grant must be
applied manually).

Testability: every heavy dependency (firebase_admin, google.cloud.storage,
pymongo, google.auth) is imported *inside* the function that needs it and is
injectable via the module-level ``_DEPS`` override hook. The unit tests run on a
plain Python install with no GCP SDKs by injecting fakes — see
``test_mint_signed_url.py``.
"""

from __future__ import annotations

import json
import logging
import os
from dataclasses import dataclass
from typing import Any, Callable, Optional

logger = logging.getLogger("grace2.signed_urls")

# --------------------------------------------------------------------------- #
# Configuration (env-driven; all overridable for tests)
# --------------------------------------------------------------------------- #

#: TTL clamp window, in seconds. Manifest job-0251: "15-minute minimum TTL,
#: 60-minute maximum TTL". SRS §F.1/§3.8 short-lived-URL posture.
MIN_TTL_SECONDS = 900
MAX_TTL_SECONDS = 3600
DEFAULT_TTL_SECONDS = 3600

#: MongoDB database + collections — pinned to the same nomenclature the agent's
#: Persistence layer uses (persistence.py: CASES_COLLECTION = "projects",
#: Case primary key is the ``_id`` == ``case_id``; USERS_COLLECTION = "users",
#: D.13, user lookup key is ``firebase_uid``, internal id is the ``_id`` ULID).
DEFAULT_DATABASE = os.environ.get("GRACE2_MONGO_DB", "grace2_dev")
CASES_COLLECTION = "projects"
USERS_COLLECTION = "users"

#: Secret Manager resource for the Atlas SRV connection string. Production sets
#: ``GRACE2_MONGO_SRV_SECRET`` to the full resource name; defaults to the same
#: dev secret the agent's mcp.py pins so local plan/validate stay coherent.
SRV_SECRET_RESOURCE = os.environ.get(
    "GRACE2_MONGO_SRV_SECRET",
    "projects/425352658356/secrets/mongodb-srv-dev/versions/latest",
)

#: Buckets a layer object is allowed to live in. A ``gs://`` URI pointing at any
#: other bucket is rejected (defense-in-depth: the runtime SA only has read on
#: these, but we fail fast + legibly rather than mint a doomed URL).
def _allowed_buckets() -> set[str]:
    raw = os.environ.get("GRACE2_SIGNED_URL_BUCKETS", "")
    return {b.strip() for b in raw.split(",") if b.strip()}


# --------------------------------------------------------------------------- #
# Errors → HTTP status mapping
# --------------------------------------------------------------------------- #


class SignedUrlError(Exception):
    """Base class — carries an HTTP status + a client-safe message."""

    status = 500

    def __init__(self, message: str) -> None:
        super().__init__(message)
        self.message = message


class BadRequest(SignedUrlError):
    status = 400


class Unauthorized(SignedUrlError):
    status = 401


class Forbidden(SignedUrlError):
    status = 403


class NotFound(SignedUrlError):
    status = 404


class ServiceUnavailable(SignedUrlError):
    """A backing store we need for an auth decision is unreachable.

    Used by the owner-identity resolution (job-0251b): when the users-collection
    lookup itself FAILS (as opposed to finding no user), we must fail closed
    with a retryable 503 — never fall through to a raw Firebase-uid comparison.
    """

    status = 503


# --------------------------------------------------------------------------- #
# Dependency injection seam (so unit tests need no GCP SDKs)
# --------------------------------------------------------------------------- #


@dataclass
class _Deps:
    """Pluggable backends. Production lazily builds the real ones; tests inject.

    Each attribute is ``None`` until first use, at which point ``_resolve``
    constructs the real implementation. Tests set these directly to fakes
    BEFORE calling ``mint_signed_url`` so the real SDKs are never imported.
    """

    #: ``(id_token: str) -> dict`` — verify a Firebase ID token, return claims.
    verify_id_token: Optional[Callable[[str], dict[str, Any]]] = None
    #: ``(firebase_uid: str) -> dict | None`` — read a users doc from Atlas by
    #: ``{"firebase_uid": ...}`` (owner-identity resolution, job-0251b).
    fetch_user_doc: Optional[Callable[[str], Optional[dict[str, Any]]]] = None
    #: ``(case_id: str) -> dict | None`` — read a Case doc from Atlas.
    fetch_case_doc: Optional[Callable[[str], Optional[dict[str, Any]]]] = None
    #: ``(bucket, obj, ttl, method) -> str`` — mint a V4 signed URL.
    sign_url: Optional[Callable[[str, str, int, str], str]] = None


_DEPS = _Deps()


# --------------------------------------------------------------------------- #
# Production backend builders (lazy — imported only when actually called)
# --------------------------------------------------------------------------- #


def _prod_verify_id_token(id_token: str) -> dict[str, Any]:
    """Verify a Firebase ID token via firebase_admin. Returns decoded claims."""
    import firebase_admin  # type: ignore
    from firebase_admin import auth as fb_auth  # type: ignore

    if not firebase_admin._apps:  # pragma: no cover - one-time init
        firebase_admin.initialize_app()
    # check_revoked=True so a signed-out / disabled user can't reuse a token.
    return fb_auth.verify_id_token(id_token, check_revoked=True)


def _mongo_find_one(
    collection: str, filter_: dict[str, Any]
) -> Optional[dict[str, Any]]:
    """One-shot single-document Atlas read — the function's ONLY Mongo seam.

    Documented divergence from the MCP/translator path (see module docstring):
    a short-lived function reads single documents directly rather than spawning
    a Node MCP sidecar. Both production fetchers (users doc for owner-identity
    resolution, Case doc for the ownership check) go through this one path: the
    same Secret Manager SRV secret, the same database, the same timeouts.
    """
    from google.cloud import secretmanager  # type: ignore
    from pymongo import MongoClient  # type: ignore

    sm = secretmanager.SecretManagerServiceClient()
    srv = sm.access_secret_version(
        request={"name": SRV_SECRET_RESOURCE}
    ).payload.data.decode("utf-8")
    # Short connect/selection timeouts so a misconfigured URI fails the request
    # fast rather than hanging the function for the default 30s.
    client = MongoClient(srv, serverSelectionTimeoutMS=5000, connectTimeoutMS=5000)
    try:
        return client[DEFAULT_DATABASE][collection].find_one(filter_)
    finally:
        client.close()


def _prod_fetch_user_doc(firebase_uid: str) -> Optional[dict[str, Any]]:
    """Read one users document from Atlas by ``firebase_uid`` (job-0251b).

    Mirrors ``Persistence.get_user_by_firebase_uid`` (persistence.py): same
    collection (``users``), same filter (``{"firebase_uid": <uid>}``). The
    internal owner identity is extracted by ``resolve_internal_user_id``.
    """
    return _mongo_find_one(USERS_COLLECTION, {"firebase_uid": firebase_uid})


def _prod_fetch_case_doc(case_id: str) -> Optional[dict[str, Any]]:
    """Read one Case document from Atlas by ``_id`` using a direct PyMongo read.

    Same collection + key + user-link contract as the agent's Persistence layer
    (see module docstring).
    """
    return _mongo_find_one(CASES_COLLECTION, {"_id": case_id})


def _prod_sign_url(bucket: str, obj: str, ttl_seconds: int, method: str) -> str:
    """Mint a V4 signed URL via IAM signBlob (NO key file is ever created).

    Uses the function's attached runtime SA: ``google.auth.default`` yields
    credentials whose ``.signer`` is an ``iam.Signer`` (signs through the
    IAM Credentials API ``signBlob``). ``generate_signed_url`` takes those
    credentials + the SA email and produces a V4 signature without any private
    key material on disk.
    """
    import google.auth  # type: ignore
    from google.cloud import storage  # type: ignore

    creds, _project = google.auth.default(
        scopes=["https://www.googleapis.com/auth/cloud-platform"]
    )
    # The runtime SA email — needed for the V4 signing subject. On Cloud
    # Functions gen2 the attached SA email is exposed via the metadata server;
    # google.auth surfaces it as ``service_account_email`` on the credentials.
    sa_email = getattr(creds, "service_account_email", None) or os.environ.get(
        "GRACE2_SIGNER_SA_EMAIL"
    )
    if not sa_email:  # pragma: no cover - misconfig guard
        raise SignedUrlError(
            "signer service-account email unavailable (no metadata SA + "
            "GRACE2_SIGNER_SA_EMAIL unset)"
        )

    from datetime import timedelta

    storage_client = storage.Client()
    blob = storage_client.bucket(bucket).blob(obj)
    return blob.generate_signed_url(
        version="v4",
        expiration=timedelta(seconds=ttl_seconds),
        method=method,
        credentials=creds,
        service_account_email=sa_email,
    )


def _resolve() -> _Deps:
    """Fill in any unset deps with the production builders (lazy)."""
    if _DEPS.verify_id_token is None:
        _DEPS.verify_id_token = _prod_verify_id_token
    if _DEPS.fetch_user_doc is None:
        _DEPS.fetch_user_doc = _prod_fetch_user_doc
    if _DEPS.fetch_case_doc is None:
        _DEPS.fetch_case_doc = _prod_fetch_case_doc
    if _DEPS.sign_url is None:
        _DEPS.sign_url = _prod_sign_url
    return _DEPS


# --------------------------------------------------------------------------- #
# Pure helpers (no I/O — directly unit-tested)
# --------------------------------------------------------------------------- #


def clamp_ttl(ttl_seconds: int) -> int:
    """Clamp a requested TTL into the [MIN, MAX] window.

    Non-int / non-positive inputs fall back to the default rather than raising:
    the contract is "give me a usable short-lived URL", and a clamp is the
    least-surprising behavior. The bounds themselves are the security property.
    """
    try:
        ttl = int(ttl_seconds)
    except (TypeError, ValueError, OverflowError):
        # OverflowError: int(float("inf")) / int(1e400) — job-0251b panel nit.
        # Without it an infinite ttl_seconds 500'd instead of falling back.
        return DEFAULT_TTL_SECONDS
    if ttl < MIN_TTL_SECONDS:
        return MIN_TTL_SECONDS
    if ttl > MAX_TTL_SECONDS:
        return MAX_TTL_SECONDS
    return ttl


def parse_layer_uri(layer_uri: str) -> tuple[str, str]:
    """Split a ``gs://bucket/path/to/object`` URI into ``(bucket, object)``.

    Raises ``BadRequest`` for anything that is not a well-formed ``gs://`` URI
    with a non-empty object path. Also rejects buckets outside the configured
    allowlist when one is set (defense-in-depth — the SA can't read other
    buckets anyway, but we fail fast + legibly).
    """
    if not isinstance(layer_uri, str) or not layer_uri.startswith("gs://"):
        raise BadRequest(
            f"layer_uri must be a gs:// URI, got {layer_uri!r}"
        )
    rest = layer_uri[len("gs://") :]
    if "/" not in rest:
        raise BadRequest(
            f"layer_uri must include an object path: {layer_uri!r}"
        )
    bucket, _, obj = rest.partition("/")
    if not bucket or not obj:
        raise BadRequest(f"layer_uri has empty bucket or object: {layer_uri!r}")
    # Reject path traversal / encoded shenanigans that could escape the object.
    if ".." in obj.split("/"):
        raise BadRequest(f"layer_uri object path may not contain '..': {obj!r}")
    allowed = _allowed_buckets()
    if allowed and bucket not in allowed:
        raise Forbidden(
            f"bucket {bucket!r} is not an allowed layer bucket"
        )
    return bucket, obj


def resolve_internal_user_id(
    user_doc: Optional[dict[str, Any]],
) -> Optional[str]:
    """Extract the canonical internal owner identity from a users document.

    Decision 10 (sprint-13-5-decisions.md; SRS H.2:42 / H.5:124): the canonical
    owner identity is the internal ``users._id`` ULID, NOT the Firebase uid.
    Mirrors the normalization in ``Persistence.get_user_by_firebase_uid``:
    ``_id`` is authoritative, with the ``user_id`` key as fallback (the agent's
    ``upsert_user`` writes both, equal).

    Returns ``None`` (caller fails closed → 403) when the doc is missing,
    malformed, or carries no usable string id.
    """
    if not isinstance(user_doc, dict) or not user_doc:
        return None
    internal = user_doc.get("_id") or user_doc.get("user_id")
    if isinstance(internal, str) and internal:
        return internal
    return None


def case_owned_by(case_doc: Optional[dict[str, Any]], user_id: str) -> bool:
    """Return True iff ``user_id`` owns the Case document.

    ``user_id`` here is the RESOLVED internal ``users._id`` ULID (Decision 10)
    — callers must run ``resolve_internal_user_id`` first; the raw Firebase uid
    never reaches this comparison.

    Mirrors the user-link semantics of ``Persistence.list_cases_for_user``
    (``user_id`` OR ``owner_user_id``) but DELIBERATELY OMITS the pre-Auth
    ``$exists:False`` backward-compat clause: a signing boundary fails closed,
    so an orphan (un-owned) Case is not mintable by an arbitrary user. job-0252
    migrates pre-Auth cases to MIGRATION_ANON_UID so they carry a real owner —
    and the sentinel has no users doc, so no token can ever resolve to it.
    """
    if not case_doc:
        return False
    return case_doc.get("user_id") == user_id or (
        case_doc.get("owner_user_id") == user_id
    )


def extract_bearer_token(headers: Any) -> str:
    """Pull a Firebase ID token out of the ``Authorization: Bearer`` header.

    Accepts any mapping-like headers object (Flask request.headers, a plain
    dict, etc.). Case-insensitive header lookup. Raises ``Unauthorized`` when
    absent or malformed.
    """
    auth = None
    # Flask's headers support .get; plain dicts too. Fall back to a manual,
    # case-insensitive scan for raw dicts that used a different case.
    getter = getattr(headers, "get", None)
    if callable(getter):
        auth = getter("Authorization") or getter("authorization")
    if auth is None and hasattr(headers, "items"):
        for k, v in headers.items():
            if str(k).lower() == "authorization":
                auth = v
                break
    if not auth or not isinstance(auth, str):
        raise Unauthorized("missing Authorization header")
    parts = auth.split(None, 1)
    if len(parts) != 2 or parts[0].lower() != "bearer" or not parts[1].strip():
        raise Unauthorized("Authorization header must be 'Bearer <id-token>'")
    return parts[1].strip()


# --------------------------------------------------------------------------- #
# Core: the testable, I/O-via-deps entry point
# --------------------------------------------------------------------------- #


def mint_signed_url(
    layer_uri: str,
    user_id: str,
    case_id: str,
    ttl_seconds: int = DEFAULT_TTL_SECONDS,
    *,
    verified_uid: Optional[str] = None,
    deps: Optional[_Deps] = None,
) -> dict[str, Any]:
    """Mint a GCS V4 signed URL for ``layer_uri`` after ownership checks.

    This is the unit-tested core. The HTTP wrapper (``handle_request``) verifies
    the Firebase token and passes the verified uid as ``verified_uid``; callers
    that already hold a trusted uid (or tests) may pass it directly.

    The chain (Decision 10): token uid == body.user_id → resolve to the
    internal ``users._id`` ULID → ownership check → mint.

    Args:
        layer_uri: ``gs://bucket/object`` path of the layer object.
        user_id: the FIREBASE uid claimed in the request body. It is matched
            against ``verified_uid`` and then resolved internally to the
            canonical ``users._id`` ULID — it is never compared against Case
            owner fields directly.
        case_id: the Case the layer belongs to.
        ttl_seconds: requested TTL; clamped to [900, 3600].
        verified_uid: the uid proven by a verified Firebase token. When given it
            MUST equal ``user_id`` (never trust the body) — else ``Forbidden``.
        deps: dependency overrides (tests). Defaults to the module singleton.

    Returns:
        ``{"signed_url": str, "expires_in": int, "bucket": str, "object": str}``.

    Raises:
        BadRequest / Unauthorized / Forbidden / NotFound / ServiceUnavailable on
        validation/auth/identity-resolution/ownership failures (each carries an
        HTTP ``status``).
    """
    d = deps or _resolve()

    if not isinstance(user_id, str) or not user_id:
        raise BadRequest("user_id is required")
    if not isinstance(case_id, str) or not case_id:
        raise BadRequest("case_id is required")

    # 1. Trust boundary: the verified token uid is authoritative; the body's
    #    user_id must match it. NEVER trust the body's user_id on its own.
    if verified_uid is not None and verified_uid != user_id:
        raise Forbidden(
            "token uid does not match body user_id (never trust the body)"
        )

    # 2. Validate + split the layer URI before any DB I/O (cheap fail-fast).
    bucket, obj = parse_layer_uri(layer_uri)

    # 3. Owner-identity resolution (job-0251b / Decision 10): the verified
    #    Firebase uid is resolved to the internal users._id ULID via the users
    #    collection. Fail-closed at every branch — a lookup ERROR is a 503 and
    #    a missing/malformed users doc is a 403; under no circumstance does the
    #    raw Firebase uid fall through into the ownership comparison.
    firebase_uid = verified_uid if verified_uid is not None else user_id
    try:
        user_doc = d.fetch_user_doc(firebase_uid)  # type: ignore[misc]
    except SignedUrlError:
        raise
    except Exception as exc:  # noqa: BLE001 — any lookup failure fails closed
        logger.warning(
            "users-collection lookup failed for firebase_uid=%r: %s",
            firebase_uid,
            exc,
        )
        raise ServiceUnavailable("owner identity lookup unavailable") from exc
    internal_user_id = resolve_internal_user_id(user_doc)
    if internal_user_id is None:
        # A Firebase user who has never connected to the agent (no users doc)
        # owns nothing — fail closed.
        raise Forbidden(
            "no provisioned user for the verified identity"
        )

    # 4. Ownership: the RESOLVED internal ULID must own case_id.
    case_doc = d.fetch_case_doc(case_id)  # type: ignore[misc]
    if case_doc is None:
        raise NotFound(f"case {case_id!r} not found")
    if not case_owned_by(case_doc, internal_user_id):
        # 403 not 404: the Case exists; the user just doesn't own it. (We do not
        # leak object existence beyond "you don't own this case".)
        raise Forbidden(f"user {user_id!r} does not own case {case_id!r}")

    # 5. Clamp TTL + mint.
    ttl = clamp_ttl(ttl_seconds)
    signed = d.sign_url(bucket, obj, ttl, "GET")  # type: ignore[misc]
    return {
        "signed_url": signed,
        "expires_in": ttl,
        "bucket": bucket,
        "object": obj,
    }


# --------------------------------------------------------------------------- #
# HTTP entry point (Cloud Functions gen2 / functions-framework)
# --------------------------------------------------------------------------- #


def handle_request(request: Any) -> tuple[str, int, dict[str, str]]:
    """HTTPS entry point. Verifies the Firebase token, then mints.

    Registered as the function target (``functions-framework --target=...``).
    Expects a JSON body ``{layer_uri, user_id, case_id, ttl_seconds?}`` and an
    ``Authorization: Bearer <firebase-id-token>`` header. ``body.user_id``
    carries the FIREBASE uid and must equal the verified token uid; resolution
    to the internal ``users._id`` ULID happens internally (Decision 10) — the
    body never carries the internal id. Cloud Run / Functions gen2 with
    ``--no-allow-unauthenticated`` is the OUTER gate (IAM invoker); the
    Firebase token check here is the application-level identity gate.

    Returns a ``(body, status, headers)`` triple (Flask-compatible).
    """
    headers = {"Content-Type": "application/json"}
    try:
        if getattr(request, "method", "POST") not in ("POST",):
            raise BadRequest("only POST is supported")

        d = _resolve()
        token = extract_bearer_token(getattr(request, "headers", {}))
        try:
            claims = d.verify_id_token(token)  # type: ignore[misc]
        except SignedUrlError:
            raise
        except Exception as exc:  # noqa: BLE001 — any verify failure is a 401
            logger.warning("firebase token verification failed: %s", exc)
            raise Unauthorized("invalid or expired Firebase ID token") from exc
        verified_uid = claims.get("uid") or claims.get("sub")
        if not verified_uid:
            raise Unauthorized("verified token carries no uid")

        body = _read_json_body(request)
        result = mint_signed_url(
            layer_uri=body.get("layer_uri"),
            user_id=body.get("user_id"),
            case_id=body.get("case_id"),
            ttl_seconds=body.get("ttl_seconds", DEFAULT_TTL_SECONDS),
            verified_uid=verified_uid,
            deps=d,
        )
        return json.dumps(result), 200, headers
    except SignedUrlError as exc:
        return (
            json.dumps({"error": exc.message}),
            exc.status,
            headers,
        )
    except Exception as exc:  # noqa: BLE001 — never leak a stack trace
        logger.exception("unexpected error minting signed URL")
        return (
            json.dumps({"error": "internal error"}),
            500,
            headers,
        )


def _read_json_body(request: Any) -> dict[str, Any]:
    """Best-effort JSON body extraction from a Flask-like request."""
    # Flask request: get_json(silent=True). Fall back to raw .data / .body.
    getter = getattr(request, "get_json", None)
    if callable(getter):
        data = getter(silent=True)
        if isinstance(data, dict):
            return data
    raw = getattr(request, "data", None) or getattr(request, "body", None)
    if isinstance(raw, (bytes, bytearray)):
        raw = raw.decode("utf-8")
    if isinstance(raw, str) and raw.strip():
        try:
            data = json.loads(raw)
        except json.JSONDecodeError as exc:
            raise BadRequest("request body is not valid JSON") from exc
        if isinstance(data, dict):
            return data
    raise BadRequest("request body must be a JSON object")


# functions-framework looks for a callable named in --target. We expose both
# the conventional name and the descriptive one.
mint_signed_url_http = handle_request
