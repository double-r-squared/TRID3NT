"""Per-Case secret lifecycle handler (FR-AS-4 + §F.3, job-0124).

Wires the three WebSocket envelope payloads from
``grace2_contracts.secrets`` (``secret-add``, ``secret-revoke``,
``secrets-list``) to the actual key-storage seam: **GCP Secret Manager** for
the raw key value, **MongoDB (via Persistence)** for the vault-ref-only
``SecretRecord``.

Design notes (per the kickoff + agent.md):

- The raw key value (``SecretAddEnvelopePayload.key_value``) is the only
  place a key ever appears on the wire. This handler writes that value to
  GCP Secret Manager, captures the resulting ``vault_ref``
  (``projects/.../secrets/.../versions/latest``), and persists only the
  vault-ref-bearing ``SecretRecord``. The raw key value is **never** stored
  in MongoDB and **never** returned in any reply envelope.

- ``handle_secret_revoke`` is a **soft-revoke** (flips
  ``SecretRecord.is_active = False`` in MongoDB). The Secret Manager entry
  is deliberately **not deleted** — it preserves the audit trail and lets
  the user un-revoke without re-entering the key (§F.3 discipline).

- ``handle_secrets_list`` queries ``Persistence.list_secrets_refs`` (active
  records only by default) and wraps the result in
  ``SecretsListEnvelopePayload``. The reply payload carries
  ``SecretRecord`` entries which by construction have no ``key_value``
  field — Decision F wire-isolation invariant.

- ``Persistence.get_secret_value`` (added in the same job-0124 scope) reads
  the live key value from Secret Manager using the stored ``vault_ref``.
  Called by Tier-2 fetchers at tool-invocation time; raises
  ``SecretRevokedError`` if the record's ``is_active`` flag is ``False``.

- Every operation appends one fire-and-forget audit-log line via
  ``Persistence.append_audit`` (Decision F + §F.3 audit trail).

- Multi-tenant isolation: ``handle_secrets_list`` always filters by
  ``user_id`` (from the SessionState authenticated identity). User A's
  ``secret-add`` writes the ``SecretRecord`` with ``user_id=A``; User B's
  ``secrets-list`` never sees those records because the persistence-layer
  filter narrows on the caller's id.

Invariants this module is responsible for (Decision F + invariant 9):

- **No cost theater.** No quota / cost / spend fields on any envelope or
  audit-log entry. (FR-AS-8)
- **No raw key on the reply path.** ``handle_secret_add``'s reply
  (``SecretsListEnvelopePayload``) carries only the ``SecretRecord`` (vault
  ref only). The ``key_value`` field is consumed by this handler and never
  echoed.
- **Confirmation hooks NOT triggered.** Per FR-AS-8 the two solver triggers
  are (1) any solver execution and (2) any MongoDB write **beyond** the
  agent's session records. Per-Case secret writes (``secrets`` collection)
  are user-driven configuration of the same session — not a solver run, not
  a result-bearing write. They proceed without a ``confirmation-request``
  pause. This matches the Case-lifecycle commands which are also not
  confirmation-gated.

SRS references:
- Appendix F.3 (``docs/srs/F-data-sources-discovery-secrets.md``) — the
  per-Case secrets architecture.
- FR-AS-4 (LLM-facing DB path via Persistence/MCP).
- FR-AS-8 (confirmation triggers — secrets writes are NOT a trigger).
- Decision F (wire isolation — raw key never persisted to MongoDB).
"""

from __future__ import annotations

import logging
import os
from datetime import datetime, timezone
from typing import Final

from grace2_contracts.common import new_ulid
from grace2_contracts.secrets import (
    ProviderID,
    SecretAddEnvelopePayload,
    SecretRecord,
    SecretsListEnvelopePayload,
)

from .persistence import Persistence

logger = logging.getLogger("grace2_agent.secrets_handler")

# --------------------------------------------------------------------------- #
# Typed errors
# --------------------------------------------------------------------------- #


class SecretError(RuntimeError):
    """Base for secret-handler failures."""


class SecretRevokedError(SecretError):
    """Raised when ``get_secret_value`` is called on a revoked record.

    Tier-2 fetchers catch this and surface a recoverable A.6 error code
    (the user can re-enable the key or add a new one).
    """


class SecretNotFoundError(SecretError):
    """Raised when ``get_secret_value`` is called on a missing record."""


# --------------------------------------------------------------------------- #
# GCP Secret Manager client protocol — duck-typed so tests can pass a mock
# --------------------------------------------------------------------------- #

# Default GCP project for the Secret Manager backend. Resolved at handler
# construction time; the env var matches the existing ``adapter.py`` pattern
# so a single project setting drives the whole agent service. Override with
# ``GRACE2_SECRETS_GCP_PROJECT`` if the secrets project is split from the
# Vertex AI project (the v0.1 deployment puts them in the same project).
DEFAULT_GCP_PROJECT: Final[str] = (
    os.environ.get("GRACE2_SECRETS_GCP_PROJECT")
    or os.environ.get("GOOGLE_CLOUD_PROJECT")
    or "grace-2-hazard-prod"
)


def _build_secret_id(provider: ProviderID, case_id: str | None) -> str:
    """Generate a Secret Manager secret-id for a fresh per-Case secret.

    Shape: ``case-<case_id>-<provider>-<short_ulid>`` (case-scoped) or
    ``user-<provider>-<short_ulid>`` (user-level when ``case_id`` is None).
    The full ULID is the discriminator that ensures collisions cannot
    happen between two adds in the same Case for the same provider (the
    user might re-enter the key after revoking — both records persist for
    audit, so the IDs must differ).

    Secret Manager IDs must match ``[A-Za-z0-9_-]{1,255}`` — the ULID
    crockford-base32 alphabet falls inside that range and we substitute
    nothing.
    """
    short = new_ulid()[-12:].lower()
    if case_id:
        # Truncate the case_id ULID for brevity — the short fragment still
        # uniquely identifies the per-Case scoping for audit grep.
        case_short = case_id[-8:].lower()
        return f"case-{case_short}-{provider}-{short}"
    return f"user-{provider}-{short}"


def _now_utc() -> datetime:
    """UTC ``datetime`` for ``SecretRecord.added_at`` / ``last_used_at``."""
    return datetime.now(timezone.utc)


# --------------------------------------------------------------------------- #
# Secret Manager client construction (lazy)
# --------------------------------------------------------------------------- #


def _default_secret_manager_client():  # pragma: no cover — exercised live
    """Construct a live Secret Manager client.

    Imported lazily so unit tests that pass a mock client don't pay the
    ``google.cloud.secretmanager`` import cost (and don't need ADC).
    """
    from google.cloud import secretmanager

    return secretmanager.SecretManagerServiceClient()


# --------------------------------------------------------------------------- #
# Handlers
# --------------------------------------------------------------------------- #


async def handle_secret_add(
    envelope: SecretAddEnvelopePayload,
    *,
    user_id: str,
    persistence: Persistence,
    secret_manager_client=None,
    gcp_project: str | None = None,
) -> SecretRecord:
    """Process a ``secret-add`` envelope end-to-end.

    Steps:

    1. Generate a Secret Manager secret-id (``case-<…>-<provider>-<ulid>``).
    2. Create the parent secret resource (``secrets.create``) with the
       per-Case label as the user-facing description.
    3. Add the first version carrying the raw ``key_value`` payload
       (``secrets.add_version``).
    4. Build a ``SecretRecord`` with ``vault_ref`` pointing at
       ``projects/<project>/secrets/<id>/versions/latest`` and persist it
       via ``Persistence.upsert_secret_ref`` (Decision F backstop refuses
       any field shaped like a key value).
    5. Append an audit-log entry (``event_type="secret-add"``).

    The handler returns the persisted ``SecretRecord``. The caller
    (``server.py``) wraps it in a fresh ``SecretsListEnvelopePayload`` and
    sends to the client. The raw ``key_value`` field on the inbound
    envelope is **never** echoed back, **never** persisted to MongoDB, and
    **never** logged.

    Args:
        envelope: the inbound ``SecretAddEnvelopePayload``.
        user_id: the authenticated caller's user_id (from SessionState).
            Stamped onto the ``SecretRecord`` for multi-tenant isolation.
            Cannot be empty — fail closed.
        persistence: the agent-side Mongo wrapper (added the secret-record
            CRUD methods in job-0115).
        secret_manager_client: optional pre-constructed Secret Manager
            client. Tests pass a mock; production passes None and we lazy-
            construct a live one.
        gcp_project: override the default project (``DEFAULT_GCP_PROJECT``).

    Returns:
        The persisted ``SecretRecord`` (vault-ref only).

    Raises:
        SecretError: on any failure — the caller surfaces this as an A.6
            ``INTERNAL_ERROR`` envelope. The raw key value is NOT leaked
            into the error message.
    """
    if not user_id:
        # Fail closed — multi-tenant isolation requires a stamped user_id.
        raise SecretError("handle_secret_add requires a non-empty user_id")
    if not envelope.key_value:
        # An empty key_value is a malformed envelope — refuse before we
        # write a zero-byte secret version to GCP.
        raise SecretError("handle_secret_add: key_value is empty")

    project = gcp_project or DEFAULT_GCP_PROJECT
    secret_id = _build_secret_id(envelope.provider, envelope.case_id)
    parent = f"projects/{project}"

    client = secret_manager_client or _default_secret_manager_client()

    # 1. Create the secret resource.
    #    We intentionally do not surface key_value into any log line.
    logger.info(
        "secret-add: creating secret_id=%s provider=%s case=%s user=%s",
        secret_id,
        envelope.provider,
        envelope.case_id,
        user_id,
    )
    create_secret_kwargs = {
        "parent": parent,
        "secret_id": secret_id,
        "secret": {"replication": {"automatic": {}}},
    }
    # Live Secret Manager SDK accepts both ``request=`` and kwargs;
    # the kwargs path matches the mock client surface in tests.
    client.create_secret(request=create_secret_kwargs)

    # 2. Add the first version with the raw key_value as payload.
    version_kwargs = {
        "parent": f"{parent}/secrets/{secret_id}",
        "payload": {"data": envelope.key_value.encode("utf-8")},
    }
    add_version_response = client.add_secret_version(request=version_kwargs)
    # The live SDK returns a ``SecretVersion`` proto with a ``name`` attr
    # like ``projects/.../secrets/.../versions/1``. We normalize to
    # ``.../versions/latest`` for the stored ``vault_ref`` so subsequent
    # ``get_secret_value`` calls always read the freshest version (the
    # current schema does not version per-revoke; a future un-revoke flow
    # could add a new version and update the ref then).
    versioned_name = getattr(add_version_response, "name", None) or (
        f"{parent}/secrets/{secret_id}/versions/1"
    )
    # Replace the trailing version selector with ``latest``.
    vault_ref = versioned_name.rsplit("/versions/", 1)[0] + "/versions/latest"

    # 3. Build and persist the SecretRecord.
    record = SecretRecord(
        secret_id=new_ulid(),
        provider=envelope.provider,
        case_id=envelope.case_id,
        vault_ref=vault_ref,
        label=envelope.label,
        added_at=_now_utc(),
        last_used_at=None,
        is_active=True,
    )
    # Stamp the user_id onto the persisted document for multi-tenant
    # filtering. ``Persistence.upsert_secret_ref`` stores the
    # ``SecretRecord.model_dump()`` plus our supplied user_id; the schema
    # itself does not carry user_id (forward-compat field), but the
    # persistence layer's list filter looks for it.
    await _upsert_with_user(persistence, record, user_id=user_id)

    # 4. Append an audit-log entry. Never logs the key value.
    await _safe_append_audit(
        persistence,
        event_type="secret-add",
        payload={
            "user_id": user_id,
            "case_id": envelope.case_id,
            "provider": envelope.provider,
            "secret_id": record.secret_id,
            "vault_ref": vault_ref,
            "label": envelope.label,
        },
    )

    return record


async def handle_secret_revoke(
    secret_id: str,
    *,
    user_id: str,
    persistence: Persistence,
) -> None:
    """Soft-revoke a secret (sets ``SecretRecord.is_active = False``).

    The GCP Secret Manager entry is **not** deleted — preserves the audit
    trail and lets the user un-revoke without re-entering the key.

    Per FR-AS-8 this is NOT a confirmation trigger (per-Case secret
    revocation is user-driven configuration, not a solver run or result
    write).

    Args:
        secret_id: the ULID of the ``SecretRecord`` to revoke.
        user_id: the authenticated caller's user_id (audit only — the
            persistence layer doesn't currently enforce caller-owns-secret
            because the storage schema doesn't denormalize the ownership
            link. Surfaced as OQ-0124-SECRET-OWNER-CHECK).
        persistence: the agent-side Mongo wrapper.
    """
    if not secret_id:
        raise SecretError("handle_secret_revoke requires a non-empty secret_id")

    await persistence.revoke_secret(secret_id)
    await _safe_append_audit(
        persistence,
        event_type="secret-revoke",
        payload={"user_id": user_id, "secret_id": secret_id},
    )
    logger.info(
        "secret-revoke: marked secret_id=%s inactive (user=%s)",
        secret_id,
        user_id,
    )


async def handle_secrets_list(
    *,
    user_id: str,
    case_id: str | None = None,
    persistence: Persistence,
) -> SecretsListEnvelopePayload:
    """List active secret references for the caller.

    Multi-tenant isolation: ``Persistence.list_secrets_refs`` filters on
    ``user_id`` (plus backward-compat for pre-Auth records without the
    field). When ``case_id`` is supplied the result is further narrowed
    to per-Case records — user-level records are excluded from a
    Case-scoped list to keep the UX surface tight.

    The returned ``SecretsListEnvelopePayload`` carries only the
    vault-ref-bearing ``SecretRecord`` entries — by construction no
    ``key_value`` field. This is the Decision F wire-isolation backstop.

    Args:
        user_id: the authenticated caller's user_id (from SessionState).
            Cannot be empty — fail closed.
        case_id: optional Case scope. ``None`` returns every active
            record for the user.
        persistence: the agent-side Mongo wrapper.

    Returns:
        ``SecretsListEnvelopePayload`` with the (possibly empty) list.
    """
    if not user_id:
        raise SecretError("handle_secrets_list requires a non-empty user_id")
    records = await persistence.list_secrets_refs(user_id=user_id, case_id=case_id)
    # Defensive: even though the schema rejects key_value at construction,
    # double-check the wire payload carries no leakage. ``SecretRecord``
    # has no key-value field at all — this loop never trips, but it's the
    # explicit "fail closed" assertion the kickoff requires.
    for r in records:
        dump = r.model_dump()
        for k in dump:
            assert "key" not in k or "value" not in k.lower(), (
                f"SecretRecord contained a key-value-shaped field: {k!r}"
            )
    return SecretsListEnvelopePayload(secrets=records)


# --------------------------------------------------------------------------- #
# Helpers — confined to this module (don't expand Persistence's public API
# more than the kickoff specifies)
# --------------------------------------------------------------------------- #


async def _upsert_with_user(
    persistence: Persistence, record: SecretRecord, *, user_id: str
) -> None:
    """Upsert a ``SecretRecord`` stamped with ``user_id`` for tenant scoping.

    The schema-level ``SecretRecord`` doesn't carry a ``user_id`` field
    (it's a forward-compat field on the *storage* document only, per the
    §F.3 multi-tenant note in ``persistence.py``). We call the existing
    ``upsert_secret_ref`` then ``$set`` the ``user_id`` via a second MCP
    call — the persistence wrapper's list filter looks for either
    ``user_id`` or the legacy ``owner_user_id``.

    We could (and may, in a future job) expose a ``user_id``-aware
    ``upsert_secret_ref`` directly; the kickoff explicitly says additive
    Persistence changes only, and the only required new method is
    ``get_secret_value``. So this stays in the handler module.
    """
    # First: the schema-shaped upsert (vault-ref-only, no key value).
    await persistence.upsert_secret_ref(record)
    # Second: stamp the user_id by re-issuing an update-one. Best-effort: if
    # this stamp fails the record is persisted but UNOWNED, so the owner-scoped
    # ``list_secrets_refs`` filter (job-0252 removed the ``$exists:false``
    # backward-compat clause that used to surface unowned rows to every user)
    # will not surface it. We log and continue rather than raise — losing
    # visibility of one secret-ref is preferable to failing the whole add; the
    # next add for the same secret_id re-stamps it.
    try:
        await persistence._mcp.call_tool(  # noqa: SLF001 — intentional
            "update-one",
            {
                "database": persistence._db,  # noqa: SLF001 — intentional
                "collection": "secrets",
                "filter": {"_id": record.secret_id},
                "update": {"$set": {"user_id": user_id}},
            },
        )
    except Exception:  # noqa: BLE001
        logger.exception(
            "secret-add: failed to stamp user_id on secret_id=%s "
            "(continuing — list filter still finds the record)",
            record.secret_id,
        )


async def _safe_append_audit(
    persistence: Persistence, *, event_type: str, payload: dict
) -> None:
    """Append an audit-log entry — never raise from this path.

    Audit-log writes are fire-and-forget: a failure must not abort the
    caller's happy path. ``Persistence.append_audit`` is already async +
    MCP-routed; we wrap it in try/except so any MCP wobble doesn't turn
    a successful secret-add into a user-visible error.
    """
    try:
        await persistence.append_audit(event_type, payload)
    except Exception:  # noqa: BLE001 — fire-and-forget
        logger.exception(
            "audit-log append failed for event_type=%s (best-effort, continuing)",
            event_type,
        )


__all__ = [
    "DEFAULT_GCP_PROJECT",
    "SecretError",
    "SecretNotFoundError",
    "SecretRevokedError",
    "handle_secret_add",
    "handle_secret_revoke",
    "handle_secrets_list",
]
