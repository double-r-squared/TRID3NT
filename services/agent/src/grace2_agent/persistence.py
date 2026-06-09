"""Thin typed wrapper around MongoDB Atlas MCP server CRUD operations (FR-AS-4).

Pattern: agent code calls ``Persistence.upsert_case(case_dataclass)`` — this
module calls the MongoDB MCP server's ``insert-one`` / ``update-one`` /
``find-one`` / ``find`` tools and serializes/deserializes through the
``grace2_contracts`` ``GraceModel`` types (NEVER raw dicts at the call site).

This is the **LLM-facing DB path** per FR-AS-4 and Decision F. Worker-side
direct-driver writes (``engine``'s solver result inserts, see FR-MP-3) are a
separate seam that does NOT route through this module.

Job-0115 scope (sprint-12-mega Wave 1.5):
- ``CaseSummary`` round-trip: get / upsert / list / archive / delete
- ``CaseChatMessage`` append + ``CaseSessionState`` hydration
- ``User`` round-trip: ``get_user_by_firebase_uid`` / ``upsert_user``
- ``SecretRecord`` round-trip (vault-ref-only — Decision F): list / upsert /
  revoke
- ``append_audit`` — fire-and-forget audit log line

Containment discipline (per agent.md):
- This module does NOT open a direct PyMongo driver. Every storage call goes
  through ``mcp_client.call_tool("<mcp-method>", args)`` so the agent has a
  single LLM-facing DB seam.
- The MCP server is consumed verbatim (``mongodb-mcp-server`` npm package);
  we don't wrap it, we delegate to it. The agent code that calls this module
  passes typed ``GraceModel`` instances in and gets typed instances out — the
  ``dict``-shape MCP transport is contained here.
- The session-record write carveout (Appendix D.6, FR-AS-8) is implemented at
  the confirmation-hook layer (``server.CONFIRMATION_TRIGGERS``), not here.
  Persistence is the I/O substrate; the hook policy is per-call.

Invariants this module is responsible for:
- **Decision F (wire isolation).** ``SecretRecord`` serialization NEVER carries
  a raw key value. ``key_value`` only ever appears on the ``secret-add``
  *envelope* (cleared at the server boundary before persistence); the
  ``SecretRecord`` shape itself is vault-ref-only and is what this module
  upserts. The redaction back-stop is at the schema layer (``__repr_args__``
  on ``SecretAddEnvelopePayload``); persistence simply never receives a
  ``SecretAddEnvelopePayload`` — only ``SecretRecord``s.
- **9. No cost theater.** No quota / cost / spend fields on any record.
- **session-record carveout.** A ``sessions``-collection update (the agent's
  own session record) is NOT a confirmable write; a ``runs``-collection
  insert IS (Decision F + FR-AS-8). This module exposes both seams; the
  caller (``server.py``) is responsible for confirmation routing.
"""

from __future__ import annotations

import logging
from typing import Any, Protocol

from grace2_contracts import new_ulid, now_utc
from grace2_contracts.case import (
    CaseChatMessage,
    CaseSessionState,
    CaseSummary,
)
from grace2_contracts.secrets import SecretRecord
from grace2_contracts.user import User

logger = logging.getLogger("grace2_agent.persistence")

# MongoDB Atlas database used for all Case/User/Secret persistence at v0.1.
# Override via env var ``GRACE2_MONGO_DB`` for staging / test isolation; the
# production deploy pins the database name via Secret Manager.
import os

DEFAULT_DATABASE = os.environ.get("GRACE2_MONGO_DB", "grace2_dev")

# Collection names — pinned by Appendix D nomenclature (D.2 ``projects`` for
# Cases, D.6 ``sessions`` for chat history, D.13 ``users`` for the
# forward-looking Auth track stub, D.14 ``secrets`` for §F.3 per-Case keys,
# D.15 ``audit_log`` for the fire-and-forget audit stream).
CASES_COLLECTION = "projects"  # FR-MP-5/-6: Case <-> projects 1:1
CHAT_COLLECTION = "case_chat_messages"  # per-turn message log (FR-MP-6)
SESSIONS_COLLECTION = "sessions"  # D.6 — agent's own session records
USERS_COLLECTION = "users"  # D.13 (Auth/Users track stub)
SECRETS_COLLECTION = "secrets"  # §F.3 per-Case secrets
AUDIT_COLLECTION = "audit_log"  # fire-and-forget audit stream


# --------------------------------------------------------------------------- #
# MCP client protocol — duck-typed so tests can pass a mock
# --------------------------------------------------------------------------- #


class MCPClientProtocol(Protocol):
    """Minimal MCP client surface this module depends on.

    Matches ``grace2_agent.mcp.MCPClient.call_tool`` so the live client (the
    stdio-launched ``mongodb-mcp-server`` subprocess) drops in without
    adaptation. Tests pass a mock implementing this single method.
    """

    async def call_tool(
        self, name: str, arguments: dict[str, Any] | None = None
    ) -> dict[str, Any]:
        ...


# --------------------------------------------------------------------------- #
# Persistence wrapper
# --------------------------------------------------------------------------- #


def _unwrap_mcp_result(raw: dict[str, Any]) -> Any:
    """Extract the structured payload from an MCP ``tools/call`` result.

    The MCP protocol returns results in a ``content`` array. ``mongodb-mcp-server``
    populates the first entry's ``text`` field with a JSON string for document
    operations. Best-effort: if the shape doesn't match we surface ``None`` so
    callers can branch on "no document" vs "raw dict already parsed".
    """
    if not isinstance(raw, dict):
        return raw
    # Direct dict already — e.g., when the mock test client returns a dict.
    if "content" not in raw and "document" not in raw and "documents" not in raw:
        return raw
    # mongodb-mcp-server: content[0].text is a JSON string
    content = raw.get("content")
    if isinstance(content, list) and content:
        first = content[0]
        if isinstance(first, dict) and isinstance(first.get("text"), str):
            import json as _json

            try:
                return _json.loads(first["text"])
            except _json.JSONDecodeError:
                return first["text"]
    # Some MCP variants emit ``document`` / ``documents`` directly.
    if "document" in raw:
        return raw["document"]
    if "documents" in raw:
        return raw["documents"]
    return raw


class Persistence:
    """Typed wrapper around the MongoDB Atlas MCP server.

    Construct with a live ``MCPClient`` (or any object implementing the
    ``MCPClientProtocol``). All methods are ``async`` — the underlying MCP
    transport is async stdio.
    """

    def __init__(
        self,
        mcp_client: MCPClientProtocol,
        *,
        database: str = DEFAULT_DATABASE,
    ) -> None:
        self._mcp = mcp_client
        self._db = database

    # ----- Cases (FR-MP-6) ------------------------------------------------- #

    async def get_case(self, case_id: str) -> CaseSummary | None:
        """Find one Case by id. Returns ``None`` if not found.

        Forward-compat: drops any field the ``ProjectDocument`` schema (D.2)
        carries that ``CaseSummary`` doesn't denormalize (e.g. ``deleted_at``,
        ``owner_user_id``, etc.). The Case envelope is a UI denormalization
        of the storage shape — extra storage fields are expected and ignored.
        """
        raw = await self._mcp.call_tool(
            "find-one",
            {
                "database": self._db,
                "collection": CASES_COLLECTION,
                "filter": {"_id": case_id},
            },
        )
        doc = _unwrap_mcp_result(raw)
        if not doc or not isinstance(doc, dict):
            return None
        return self._doc_to_case_summary(doc)

    @staticmethod
    def _doc_to_case_summary(doc: dict) -> CaseSummary:
        """Normalize a stored projects document into a ``CaseSummary``.

        Strips ``_id`` (rewires to ``case_id``), drops user-link fields the
        schema doesn't know, and drops any other storage-only fields the
        denormalized envelope doesn't carry.
        """
        allowed = set(CaseSummary.model_fields.keys())
        normalized: dict[str, object] = {}
        for k, v in doc.items():
            if k == "_id":
                continue
            if k in {"user_id", "owner_user_id"}:
                continue
            if k not in allowed:
                continue
            normalized[k] = v
        if "case_id" not in normalized and "_id" in doc:
            normalized["case_id"] = doc["_id"]
        return CaseSummary.model_validate(normalized)

    async def upsert_case(self, case: CaseSummary) -> CaseSummary:
        """Insert or update a Case. Returns the persisted ``CaseSummary``.

        Uses MCP ``update-one`` with ``upsert=True`` so a fresh Case lands and
        an existing one is overwritten in a single round-trip.
        """
        body = case.model_dump(mode="json")
        body["_id"] = case.case_id  # MongoDB primary key (FR-MP-5)
        await self._mcp.call_tool(
            "update-one",
            {
                "database": self._db,
                "collection": CASES_COLLECTION,
                "filter": {"_id": case.case_id},
                "update": {"$set": body},
                "upsert": True,
            },
        )
        return case

    async def list_cases_for_user(self, user_id: str) -> list[CaseSummary]:
        """List Cases (filtered by ``user_id`` once the Auth track lands).

        v0.1 Auth-stub note: the ``projects`` collection schema does not
        currently carry a ``user_id`` field (FR-MP-5 was specified pre-Auth).
        We pass the filter anyway — once the Auth/Users track adds the field
        the query starts narrowing; until then it returns the full Case list
        for the deployment. Surfaced as OQ-0115-CASE-USER-LINK.
        """
        # Backward-compat: include records that pre-date the Auth track (no
        # user_id field at all) so the Wave 1.5 stub returns a useful list.
        raw = await self._mcp.call_tool(
            "find",
            {
                "database": self._db,
                "collection": CASES_COLLECTION,
                "filter": {
                    "$or": [
                        {"user_id": user_id},
                        {"owner_user_id": user_id},
                        {"user_id": {"$exists": False}},
                    ],
                },
            },
        )
        docs = _unwrap_mcp_result(raw)
        # If the MCP server returned no filter match, ``docs`` may be empty
        # list or None. Be tolerant.
        if not docs:
            return []
        if isinstance(docs, dict):
            docs = [docs]
        cases: list[CaseSummary] = []
        for d in docs:
            if not isinstance(d, dict):
                continue
            try:
                cases.append(self._doc_to_case_summary(d))
            except Exception:  # noqa: BLE001 — skip malformed docs
                logger.warning("skipping malformed Case doc: %s", d)
                continue
        return cases

    async def archive_case(self, case_id: str) -> None:
        """Soft-archive a Case (sets ``status="archived"``).

        Preserves the document for un-archive; ``delete_case`` is the hard
        path. Mirrors ``CaseStatus`` Literal in ``grace2_contracts.case``.
        """
        await self._mcp.call_tool(
            "update-one",
            {
                "database": self._db,
                "collection": CASES_COLLECTION,
                "filter": {"_id": case_id},
                "update": {
                    "$set": {
                        "status": "archived",
                        "updated_at": now_utc().isoformat().replace("+00:00", "Z"),
                    }
                },
            },
        )

    async def delete_case(self, case_id: str) -> None:
        """Soft-delete a Case (sets ``status="deleted"``).

        v0.1 stance: soft-delete only. A future job lands a curator-tooled
        hard delete; data-retention rules (D.2 ``deleted_at``) point this way
        anyway. Status mirrors the ``CaseStatus`` Literal tombstone value.
        """
        await self._mcp.call_tool(
            "update-one",
            {
                "database": self._db,
                "collection": CASES_COLLECTION,
                "filter": {"_id": case_id},
                "update": {
                    "$set": {
                        "status": "deleted",
                        "deleted_at": now_utc().isoformat().replace("+00:00", "Z"),
                    }
                },
            },
        )

    # ----- Chat history + session state (FR-MP-6 rehydration) ------------- #

    async def append_chat_message(self, msg: CaseChatMessage) -> None:
        """Append one persisted chat exchange to a Case's history.

        Per FR-AS-8 the chat-message collection is the agent's own session
        record (it is per-turn replay material, not a solver result), so this
        write is NOT a confirmation trigger — the caller does not need to
        gate it. The carveout is enforced at the confirmation-hook layer.
        """
        body = msg.model_dump(mode="json")
        body["_id"] = msg.message_id
        await self._mcp.call_tool(
            "insert-one",
            {
                "database": self._db,
                "collection": CHAT_COLLECTION,
                "document": body,
            },
        )

    async def get_session_state(self, case_id: str) -> CaseSessionState:
        """Hydrate the rehydration envelope for a Case (FR-MP-6 resume).

        Joins the Case header (``CaseSummary``) with its ordered chat history
        from ``CHAT_COLLECTION``. ``loaded_layers`` / ``pipeline_history`` /
        ``current_pipeline`` are passed through as dicts — collections.py
        owns the concrete shapes (matches the ``SessionStatePayload`` pattern
        already in ws.py).
        """
        case = await self.get_case(case_id)
        if case is None:
            # Surface a minimal placeholder so the caller can decide how to
            # handle "Case not found" without raising through the MCP layer.
            return CaseSessionState(
                case=CaseSummary(
                    case_id=case_id,
                    title="(missing)",
                    created_at=now_utc(),
                    updated_at=now_utc(),
                    status="deleted",
                ),
            )
        # Chat history, oldest-first
        raw = await self._mcp.call_tool(
            "find",
            {
                "database": self._db,
                "collection": CHAT_COLLECTION,
                "filter": {"case_id": case_id},
                "sort": {"created_at": 1},
            },
        )
        docs = _unwrap_mcp_result(raw) or []
        if isinstance(docs, dict):
            docs = [docs]
        chat: list[CaseChatMessage] = []
        for d in docs:
            if not isinstance(d, dict):
                continue
            normalized = {k: v for k, v in d.items() if k != "_id"}
            try:
                chat.append(CaseChatMessage.model_validate(normalized))
            except Exception:  # noqa: BLE001
                logger.warning("skipping malformed CaseChatMessage doc: %s", d)
                continue
        return CaseSessionState(case=case, chat_history=chat)

    # ----- Users (Auth/Users track stub) ----------------------------------- #

    async def get_user_by_firebase_uid(self, uid: str) -> User | None:
        """Find a user by Firebase / Identity Platform UID."""
        raw = await self._mcp.call_tool(
            "find-one",
            {
                "database": self._db,
                "collection": USERS_COLLECTION,
                "filter": {"firebase_uid": uid},
            },
        )
        doc = _unwrap_mcp_result(raw)
        if not doc or not isinstance(doc, dict):
            return None
        normalized = {k: v for k, v in doc.items() if k != "_id"}
        if "user_id" not in normalized and "_id" in doc:
            normalized["user_id"] = doc["_id"]
        try:
            return User.model_validate(normalized)
        except Exception:  # noqa: BLE001
            logger.warning("malformed user doc for firebase_uid=%s", uid)
            return None

    async def upsert_user(self, user: User) -> User:
        """Insert or update a user record."""
        body = user.model_dump(mode="json")
        body["_id"] = user.user_id
        await self._mcp.call_tool(
            "update-one",
            {
                "database": self._db,
                "collection": USERS_COLLECTION,
                "filter": {"_id": user.user_id},
                "update": {"$set": body},
                "upsert": True,
            },
        )
        return user

    # ----- Per-Case secrets (§F.3) ----------------------------------------- #

    async def list_secrets_refs(
        self,
        user_id: str,
        case_id: str | None = None,
    ) -> list[SecretRecord]:
        """List active secret records.

        Filters on ``is_active=True`` (revoked records are still in the
        collection for audit but excluded from the listing). If ``case_id`` is
        provided the filter narrows to per-Case records; otherwise returns
        every active record for the user.

        Decision F: the result NEVER includes the raw key value — only the
        ``vault_ref``. The schema enforces this at construct time.
        """
        filt: dict[str, Any] = {"is_active": True}
        if case_id is not None:
            filt["case_id"] = case_id
        # user_id linking is a forward-compat field once Auth lands; the
        # secrets collection schema in §F.3 already anticipates it.
        if user_id:
            filt["$or"] = [
                {"user_id": user_id},
                {"owner_user_id": user_id},
                {"user_id": {"$exists": False}},  # backward-compat to pre-Auth records
            ]
        raw = await self._mcp.call_tool(
            "find",
            {
                "database": self._db,
                "collection": SECRETS_COLLECTION,
                "filter": filt,
            },
        )
        docs = _unwrap_mcp_result(raw) or []
        if isinstance(docs, dict):
            docs = [docs]
        out: list[SecretRecord] = []
        for d in docs:
            if not isinstance(d, dict):
                continue
            normalized = {k: v for k, v in d.items() if k != "_id"}
            if "secret_id" not in normalized and "_id" in d:
                normalized["secret_id"] = d["_id"]
            normalized.pop("user_id", None)
            normalized.pop("owner_user_id", None)
            # Defensive: even though the schema rejects key_value, scrub
            # anything that looks like one before validation. This is the
            # "fail closed" backstop if a malformed write ever leaked.
            for k in list(normalized):
                if "key" in k and "value" in k.lower():
                    normalized.pop(k)
            try:
                out.append(SecretRecord.model_validate(normalized))
            except Exception:  # noqa: BLE001
                logger.warning("skipping malformed SecretRecord doc")
                continue
        return out

    async def upsert_secret_ref(self, sec: SecretRecord) -> SecretRecord:
        """Insert or update a vault-ref-only secret record.

        Decision F backstop: this method takes a ``SecretRecord`` (which has
        no ``key_value`` field at all). The agent service is responsible for
        writing the raw key value to the vault BEFORE calling this method
        and clearing the value from the in-memory envelope. The schema-side
        contract ensures the persistence layer cannot accidentally accept a
        raw key value.
        """
        body = sec.model_dump(mode="json")
        body["_id"] = sec.secret_id
        # Belt-and-braces: assert no key_value sneaked in via aliasing.
        for k in list(body):
            if "key" in k and "value" in k.lower():
                raise ValueError(
                    f"persistence refuses to write a key_value-shaped field "
                    f"({k!r}) — vault-ref only (Decision F)"
                )
        await self._mcp.call_tool(
            "update-one",
            {
                "database": self._db,
                "collection": SECRETS_COLLECTION,
                "filter": {"_id": sec.secret_id},
                "update": {"$set": body},
                "upsert": True,
            },
        )
        return sec

    async def revoke_secret(self, secret_id: str) -> None:
        """Soft-revoke a secret (sets ``is_active=False``).

        The vault entry is NOT deleted — preserves audit trail and lets the
        user un-revoke without re-entering the key. Mirrors §F.3 discipline.
        """
        await self._mcp.call_tool(
            "update-one",
            {
                "database": self._db,
                "collection": SECRETS_COLLECTION,
                "filter": {"_id": secret_id},
                "update": {"$set": {"is_active": False}},
            },
        )

    async def get_secret_value(
        self,
        secret_ref: "SecretRecord",
        *,
        secret_manager_client=None,
    ) -> str:
        """Read the live key value from GCP Secret Manager (job-0124).

        Called by Tier-2 fetchers (eBird / OpenWeatherMap / etc.) at
        tool-invocation time to materialize the raw key for the outbound
        HTTP request. The handler never logs the returned value.

        Fail-closed semantics:

        - If the record's ``is_active`` flag is ``False`` (soft-revoked),
          we raise ``SecretRevokedError`` BEFORE touching Secret Manager
          so a revoked secret never resurrects via stale cache.
        - If the Secret Manager fetch raises (missing version, permission
          denied, etc.) we surface the original exception — Tier-2
          fetchers wrap this in a tool-level error envelope.

        Args:
            secret_ref: the persisted ``SecretRecord`` (vault-ref only —
                we read ``secret_ref.vault_ref`` to construct the GCP
                ``access_secret_version`` request name).
            secret_manager_client: optional pre-constructed client (tests
                pass a mock; production lazy-constructs a live client).

        Returns:
            The raw key value as a string. **Caller MUST NOT log this.**

        Raises:
            SecretRevokedError: when ``secret_ref.is_active is False``.
        """
        # Local import — avoids a circular dependency between persistence
        # and secrets_handler (which imports Persistence).
        from .secrets_handler import SecretRevokedError

        if not secret_ref.is_active:
            raise SecretRevokedError(
                f"secret {secret_ref.secret_id!r} has been revoked "
                f"(provider={secret_ref.provider})"
            )

        # The stored vault_ref is the resource name (no scheme prefix).
        # Tolerate the legacy ``gcp-sm://`` shape used in some test
        # fixtures by stripping it before the SDK call.
        name = secret_ref.vault_ref
        if name.startswith("gcp-sm://"):
            name = name[len("gcp-sm://") :]

        client = secret_manager_client
        if client is None:
            from google.cloud import secretmanager  # local — production only

            client = secretmanager.SecretManagerServiceClient()

        response = client.access_secret_version(request={"name": name})
        # The live SDK returns a ``SecretPayload`` proto with a ``data``
        # bytes field. Mock clients used in tests return the same shape.
        data = getattr(response, "payload", None)
        raw = getattr(data, "data", None) if data is not None else None
        if raw is None:
            # Some mocks/proto variants stuff the bytes directly on the
            # response. Try a fallback before failing.
            raw = getattr(response, "data", None)
        if raw is None:
            raise RuntimeError(
                "Secret Manager access_secret_version returned no payload data"
            )
        if isinstance(raw, bytes):
            return raw.decode("utf-8")
        return str(raw)

    # ----- Audit log -------------------------------------------------------- #

    async def append_audit(self, event_type: str, payload: dict) -> None:
        """Append one fire-and-forget audit event.

        Used by Decision M (claim provenance) and §F.3 catalog-amendment
        audit. Best-effort: callers should NOT block their happy path on
        this — wrap in ``try/except`` at the call site if the audit write
        failing would otherwise abort the user's action.
        """
        body = {
            "_id": new_ulid(),
            "event_type": event_type,
            "ts": now_utc().isoformat().replace("+00:00", "Z"),
            "payload": payload,
        }
        await self._mcp.call_tool(
            "insert-one",
            {
                "database": self._db,
                "collection": AUDIT_COLLECTION,
                "document": body,
            },
        )


# --------------------------------------------------------------------------- #
# Local-dev file-backed MCP client (job-0161, Wave 4.6)
# --------------------------------------------------------------------------- #
#
# The MongoDB Atlas MCP server is the production LLM-facing DB seam (FR-AS-4).
# For LOCAL DEV without Atlas/MCP, this file-backed shim satisfies the same
# ``MCPClientProtocol`` surface so the ``Persistence`` class above doesn't
# need to know which substrate it is talking to. The Persistence singleton
# can therefore be bound at startup regardless of whether MCP is provisioned,
# so the Case-create / select / archive / delete UI surface works on a fresh
# clone without Atlas credentials.
#
# Storage layout:
#   ``~/.grace2/dev_persistence/<database>/<collection>.json``
#   one JSON file per collection — a dict mapping ``_id`` → document
#
# Atomicity:
#   - per-collection ``asyncio.Lock`` serializes concurrent calls
#   - writes go to a sibling ``<collection>.json.tmp`` then ``os.replace``
#     (POSIX-atomic rename on the same filesystem)
#
# Scope (matches the subset of MCP tools Persistence actually invokes):
#   ``insert-one`` / ``update-one`` (with ``$set`` + optional ``upsert``) /
#   ``find-one`` / ``find`` (with optional sort by single key, ±1 direction).
#
# This is NOT a Mongo emulator — it's just enough query semantics to round-trip
# the Persistence layer's calls. When real MCP lands the Persistence singleton
# is constructed with the live ``MCPClient`` instead, and this file-backed
# shim is never instantiated.

import asyncio as _asyncio
import json as _json_for_file
import os as _os_for_file
from pathlib import Path as _Path

DEV_PERSISTENCE_DIR_ENV = "GRACE2_DEV_PERSISTENCE_DIR"
DEV_PERSISTENCE_ENABLED_ENV = "GRACE2_DEV_PERSISTENCE"


def _default_dev_persistence_dir() -> _Path:
    """Resolve the on-disk directory for the file-backed dev substrate.

    Override via ``GRACE2_DEV_PERSISTENCE_DIR`` (used by tests + CI to point
    at a tmpdir). Default is ``~/.grace2/dev_persistence/`` per the job-0161
    kickoff so a fresh clone gets a stable, user-scoped location.
    """
    override = _os_for_file.environ.get(DEV_PERSISTENCE_DIR_ENV)
    if override:
        return _Path(override).expanduser()
    return _Path.home() / ".grace2" / "dev_persistence"


class FileMCPClient:
    """File-backed shim that satisfies :class:`MCPClientProtocol`.

    Implements the four MCP tool methods the :class:`Persistence` wrapper
    actually invokes (``insert-one``, ``update-one``, ``find-one``, ``find``)
    against a per-collection JSON file in ``base_dir / database / coll.json``.

    The return shape mirrors what ``Persistence._unwrap_mcp_result`` expects:
    we return a plain dict for single-document operations and a
    ``{"documents": [...]}`` envelope for list operations. This keeps the
    Persistence layer agnostic of substrate — the same code paths that
    deserialize MCP-server JSON responses deserialize our file payloads.
    """

    def __init__(self, base_dir: _Path | None = None) -> None:
        self._base_dir = base_dir or _default_dev_persistence_dir()
        self._base_dir.mkdir(parents=True, exist_ok=True)
        # collection-path -> asyncio.Lock, lazily allocated. Per-collection
        # rather than global so reads from one collection don't block another.
        self._locks: dict[str, _asyncio.Lock] = {}

    # ------------------------------------------------------------------ #
    # Storage helpers
    # ------------------------------------------------------------------ #

    def _collection_path(self, database: str, collection: str) -> _Path:
        db_dir = self._base_dir / database
        db_dir.mkdir(parents=True, exist_ok=True)
        return db_dir / f"{collection}.json"

    def _lock_for(self, path: _Path) -> _asyncio.Lock:
        key = str(path)
        lock = self._locks.get(key)
        if lock is None:
            lock = _asyncio.Lock()
            self._locks[key] = lock
        return lock

    @staticmethod
    def _read_store(path: _Path) -> dict[str, dict]:
        if not path.exists():
            return {}
        try:
            with path.open("r", encoding="utf-8") as fh:
                data = _json_for_file.load(fh)
        except (_json_for_file.JSONDecodeError, OSError) as exc:
            logger.warning(
                "FilePersistence: failed to read %s (%s); treating as empty",
                path,
                exc,
            )
            return {}
        if not isinstance(data, dict):
            return {}
        return data

    @staticmethod
    def _atomic_write(path: _Path, store: dict[str, dict]) -> None:
        """Atomic JSON write: tmp file + os.replace (POSIX-atomic rename)."""
        tmp = path.with_suffix(path.suffix + ".tmp")
        with tmp.open("w", encoding="utf-8") as fh:
            _json_for_file.dump(store, fh, indent=2, sort_keys=True)
            fh.flush()
            try:
                _os_for_file.fsync(fh.fileno())
            except OSError:
                # fsync isn't available on every filesystem; the os.replace
                # below is still atomic on POSIX so we don't escalate.
                pass
        _os_for_file.replace(tmp, path)

    # ------------------------------------------------------------------ #
    # Query matcher — same subset MockMCPClient supports in tests
    # ------------------------------------------------------------------ #

    @staticmethod
    def _matches(doc: dict, filt: dict) -> bool:
        """Tiny query matcher: equality, ``$or``, ``$exists``."""
        for k, v in filt.items():
            if k == "$or":
                if not any(FileMCPClient._matches(doc, sub) for sub in v):
                    return False
                continue
            if isinstance(v, dict) and "$exists" in v:
                present = k in doc
                if v["$exists"] is False and present:
                    return False
                if v["$exists"] is True and not present:
                    return False
                continue
            if doc.get(k) != v:
                return False
        return True

    # ------------------------------------------------------------------ #
    # MCP tool surface
    # ------------------------------------------------------------------ #

    async def call_tool(
        self, name: str, arguments: dict[str, Any] | None = None
    ) -> dict[str, Any]:
        args = dict(arguments or {})
        database = args.get("database", DEFAULT_DATABASE)
        collection = args.get("collection")
        if not collection:
            raise ValueError(
                f"FileMCPClient: tool {name!r} requires a 'collection' argument"
            )
        path = self._collection_path(database, collection)
        lock = self._lock_for(path)

        if name == "insert-one":
            async with lock:
                store = self._read_store(path)
                doc = args["document"]
                doc_id = doc.get("_id")
                if doc_id is None:
                    raise ValueError(
                        "FileMCPClient insert-one: document missing '_id'"
                    )
                store[doc_id] = doc
                self._atomic_write(path, store)
                return {"insertedId": doc_id}

        if name == "update-one":
            async with lock:
                store = self._read_store(path)
                filt = args.get("filter", {})
                update = args.get("update", {})
                set_ = update.get("$set", {})
                upsert = bool(args.get("upsert", False))
                target_id = filt.get("_id")
                matched = 0
                modified = 0
                if target_id and target_id in store:
                    store[target_id].update(set_)
                    matched = 1
                    modified = 1
                elif upsert and target_id:
                    store[target_id] = {**set_, "_id": target_id}
                    matched = 1
                    modified = 1
                else:
                    # Update by non-_id filter (e.g. firebase_uid). First match wins.
                    for doc in store.values():
                        if self._matches(doc, filt):
                            doc.update(set_)
                            matched = 1
                            modified = 1
                            break
                self._atomic_write(path, store)
                return {"matchedCount": matched, "modifiedCount": modified}

        if name == "find-one":
            async with lock:
                store = self._read_store(path)
                filt = args.get("filter", {})
                for doc in store.values():
                    if self._matches(doc, filt):
                        return {"document": doc}
                return {"document": None}

        if name == "find":
            async with lock:
                store = self._read_store(path)
                filt = args.get("filter", {})
                sort = args.get("sort", {})
                results = [d for d in store.values() if self._matches(d, filt)]
                if sort:
                    key = next(iter(sort.keys()))
                    direction = sort[key]
                    results.sort(
                        key=lambda d: d.get(key, ""),
                        reverse=(direction == -1),
                    )
                return {"documents": results}

        raise NotImplementedError(
            f"FileMCPClient: unsupported MCP tool {name!r} "
            f"(supports insert-one / update-one / find-one / find)"
        )


def is_dev_persistence_enabled() -> bool:
    """Resolve whether the file-backed dev substrate should engage.

    Order:
    - explicit ``GRACE2_DEV_PERSISTENCE=0`` disables (escape hatch for CI
      that wants the M1 None-Persistence path even on a dev box);
    - explicit ``GRACE2_DEV_PERSISTENCE=1`` enables;
    - if neither is set AND MongoDB MCP is not provisioned (no
      ``GRACE2_MONGO_MCP_STDIO=1`` nor ``GRACE2_MONGO_MCP_URL``), default ON
      so a fresh local clone gets working Case persistence with zero config.

    The MCP-provisioned check is a string read (we don't try to start the
    sidecar here); ``init_persistence_from_env`` in ``server.py`` is the
    single place that actually decides between FilePersistence and the live
    MCP-backed Persistence, and it owns the precedence (real MCP wins).
    """
    raw = _os_for_file.environ.get(DEV_PERSISTENCE_ENABLED_ENV)
    if raw is not None:
        return raw.strip().lower() in {"1", "true", "yes", "on"}
    mcp_stdio = _os_for_file.environ.get("GRACE2_MONGO_MCP_STDIO") == "1"
    mcp_url = bool(_os_for_file.environ.get("GRACE2_MONGO_MCP_URL"))
    return not (mcp_stdio or mcp_url)


def make_file_persistence(base_dir: _Path | None = None) -> Persistence:
    """Construct a ``Persistence`` backed by the file-backed MCP shim.

    Convenience for ``server.init_persistence_from_env`` and tests — wraps
    the substrate selection so the call site stays a one-liner.
    """
    return Persistence(FileMCPClient(base_dir=base_dir))


__all__ = [
    "Persistence",
    "MCPClientProtocol",
    "FileMCPClient",
    "make_file_persistence",
    "is_dev_persistence_enabled",
    "DEFAULT_DATABASE",
    "DEV_PERSISTENCE_DIR_ENV",
    "DEV_PERSISTENCE_ENABLED_ENV",
    "CASES_COLLECTION",
    "CHAT_COLLECTION",
    "SESSIONS_COLLECTION",
    "USERS_COLLECTION",
    "SECRETS_COLLECTION",
    "AUDIT_COLLECTION",
]
