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
    CaseOpenEnvelopePayload,
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

# Lane A1 (pen=agent / paper=case): the durable runs bucket holds the
# materialized case-view SNAPSHOT (``case-views/{case_id}.json``) that the
# view-without-agent path serves via a pre-signed S3 GET (the agent box may be
# asleep). The bucket already holds solver decks/results and the agent already
# has S3 write creds to it (GRACE2_RUNS_BUCKET / the EC2 instance role). Mirror
# the resolution used in ``tools/solver.py`` so a single env var moves both.
CASE_VIEWS_BUCKET = os.environ.get(
    "GRACE2_RUNS_BUCKET", "grace2-hazard-runs-226996537797"
)
#: Object-key prefix for materialized case-view snapshots (PRIVATE objects).
CASE_VIEWS_PREFIX = "case-views"


def case_view_snapshot_key(case_id: str) -> str:
    """Return the S3 object key for a Case's materialized view snapshot.

    Single seam so the writer (here) and the signer (infra lane's Lambda) name
    the object identically: ``case-views/{case_id}.json``.
    """
    return f"{CASE_VIEWS_PREFIX}/{case_id}.json"

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
# Live-server surface translation (job-0203 / Wave 4.11 M4)
# --------------------------------------------------------------------------- #
#
# FINDING (2026-06-09, live protocol smoke against mongodb-mcp-server@latest):
# the real npm server does NOT expose ``find-one`` / ``insert-one`` /
# ``update-one`` at all. Its actual document surface is ``find`` /
# ``insert-many`` / ``update-many`` (+ ``delete-many``, ``count``, ...), and
# ``find`` results come back as EJSON wrapped in
# ``<untrusted-user-data-{uuid}>`` tags in the SECOND content entry — the
# first is a human-readable "Found N documents" banner. Every Persistence
# call written against the logical surface would have failed on first
# contact with production.
#
# Resolution: the logical surface (``find-one``/``insert-one``/
# ``update-one``/``find``) is OUR seam contract (``MCPClientProtocol``) —
# ``FileMCPClient``, every test mock, and every call site speak it. This
# translator is the single boundary that adapts the logical surface to the
# real server's tool names and response shape. When MongoDB renames tools
# again, this class is the only thing that changes.
#
# ``server.init_persistence_from_env`` wraps the live ``MCPClient`` in this
# translator before handing it to ``Persistence``.


def _ejson_normalize(value: Any) -> Any:
    """Collapse the EJSON extended-type wrappers we can encounter.

    GRACE-2 documents store string ULIDs and ISO-8601 strings, so most
    round-trips are plain JSON. Mongo may still emit ``{"$date": ...}`` /
    ``{"$oid": ...}`` / ``{"$numberLong": ...}`` for fields written by
    other paths — collapse them to their plain value so Pydantic
    validation sees normal scalars.
    """
    if isinstance(value, dict):
        if len(value) == 1:
            ((k, v),) = value.items()
            if k == "$oid":
                return v
            if k == "$numberLong" or k == "$numberInt" or k == "$numberDouble":
                try:
                    return float(v) if "." in str(v) else int(v)
                except (TypeError, ValueError):
                    return v
            if k == "$date":
                # {"$date": "ISO"} or {"$date": {"$numberLong": "ms"}}
                if isinstance(v, dict) and "$numberLong" in v:
                    return v["$numberLong"]
                return v
        return {k: _ejson_normalize(v) for k, v in value.items()}
    if isinstance(value, list):
        return [_ejson_normalize(v) for v in value]
    return value


import re as _re

# The warning prose MENTIONS both tags inline ("between the <tag> and
# </tag> tags may lead to...") BEFORE the actual payload block — a lazy
# match from the first mention captures the prose word "and" instead of
# the payload. The real block is newline-delimited (``<tag>\npayload\n</tag>``
# per formatUntrustedData), so the mandatory ``\n`` on both sides skips the
# prose mentions. Verified against a live mongod round-trip (evidence/).
_UNTRUSTED_RE = _re.compile(
    r"<untrusted-user-data-([0-9a-fA-F-]+)>\n(.*?)\n</untrusted-user-data-\1>",
    _re.DOTALL,
)


def _extract_untrusted_payload(raw: dict[str, Any]) -> Any | None:
    """Pull the EJSON document payload out of a real-server tool result.

    Returns the parsed (and EJSON-normalized) payload, or ``None`` when no
    untrusted-data block is present (e.g. "Found 0 documents" responses).
    """
    content = raw.get("content")
    if not isinstance(content, list):
        return None
    for entry in content:
        if not isinstance(entry, dict):
            continue
        text = entry.get("text")
        if not isinstance(text, str):
            continue
        m = _UNTRUSTED_RE.search(text)
        if not m:
            continue
        import json as _json

        try:
            return _ejson_normalize(_json.loads(m.group(2)))
        except _json.JSONDecodeError:
            logger.warning("untrusted-data block was not valid EJSON")
            return None
    return None


class MCPSurfaceTranslator:
    """Adapt the logical MCP surface to the real ``mongodb-mcp-server``.

    Implements :class:`MCPClientProtocol`. Wraps a raw client (the live
    stdio :class:`grace2_agent.mcp.MCPClient`) whose tool names are the
    REAL server surface, and translates:

    - ``find-one``   → ``find`` with ``limit=1`` → ``{"document": doc|None}``
    - ``find``       → ``find`` with an explicit generous limit (the real
      server DEFAULTS TO limit=10 — unbounded logical reads like chat
      history would silently truncate) → ``{"documents": [...]}``
    - ``insert-one`` → ``insert-many`` with ``documents=[doc]``
    - ``update-one`` → ``update-many`` (every GRACE-2 update filters on a
      unique key, so the semantics coincide)

    Any other tool name passes through untouched.
    """

    #: Explicit limit injected when the logical ``find`` has none. The
    #: real server also caps responses at ``responseBytesLimit`` (1 MiB
    #: default) — we raise it for chat-history reads; documents beyond
    #: either cap surface as OQ-0203-FIND-PAGINATION.
    DEFAULT_FIND_LIMIT = 1000
    RESPONSE_BYTES_LIMIT = 8 * 1024 * 1024

    def __init__(self, raw_client: MCPClientProtocol) -> None:
        self._raw = raw_client

    async def call_tool(
        self, name: str, arguments: dict[str, Any] | None = None
    ) -> dict[str, Any]:
        args = dict(arguments or {})

        if name == "find-one":
            real = {
                "database": args["database"],
                "collection": args["collection"],
                "filter": args.get("filter", {}),
                "limit": 1,
            }
            raw = await self._raw.call_tool("find", real)
            docs = _extract_untrusted_payload(raw)
            doc = docs[0] if isinstance(docs, list) and docs else None
            return {"document": doc}

        if name == "find":
            real = {
                "database": args["database"],
                "collection": args["collection"],
                "filter": args.get("filter", {}),
                "limit": args.get("limit", self.DEFAULT_FIND_LIMIT),
                "responseBytesLimit": self.RESPONSE_BYTES_LIMIT,
            }
            if args.get("sort"):
                real["sort"] = args["sort"]
            raw = await self._raw.call_tool("find", real)
            docs = _extract_untrusted_payload(raw)
            if docs is None:
                docs = []
            if isinstance(docs, dict):
                docs = [docs]
            return {"documents": docs}

        if name == "insert-one":
            raw = await self._raw.call_tool(
                "insert-many",
                {
                    "database": args["database"],
                    "collection": args["collection"],
                    "documents": [args["document"]],
                },
            )
            return raw if isinstance(raw, dict) else {}

        if name == "update-one":
            real = {
                "database": args["database"],
                "collection": args["collection"],
                "filter": args.get("filter", {}),
                "update": args.get("update", {}),
            }
            if args.get("upsert"):
                real["upsert"] = True
            raw = await self._raw.call_tool("update-many", real)
            return raw if isinstance(raw, dict) else {}

        return await self._raw.call_tool(name, args)


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

    async def upsert_case(
        self, case: CaseSummary, *, owner_user_id: str | None = None
    ) -> CaseSummary:
        """Insert or update a Case. Returns the persisted ``CaseSummary``.

        Uses MCP ``update-one`` with ``upsert=True`` so a fresh Case lands and
        an existing one is overwritten in a single round-trip.

        job-0252 (sprint-13.5, OQ-0115-CASE-USER-LINK): when ``owner_user_id``
        is provided, it is stamped onto the document's ``user_id`` field so the
        Case belongs to its creator. ``CaseSummary`` itself carries no owner
        field (it is a UI denormalization), so ownership lives only at the
        storage layer — the read path (``_doc_to_case_summary``) deliberately
        drops it. Without this, every newly-created Case would lack a
        ``user_id`` and become invisible to ``list_cases_for_user`` now that
        the ``$exists:false`` leak clause is gone. ``owner_user_id=None``
        (the legacy / dev call shape) writes no owner — those Cases are then
        swept by the one-time ``migrate_preauth_cases`` startup step.

        The owner is written under ``$set``, so re-upserting an existing Case
        with a fresh ``owner_user_id`` updates it; passing ``None`` never
        clears an already-stamped owner (the ``user_id`` key is simply absent
        from the ``$set``).
        """
        body = case.model_dump(mode="json")
        body["_id"] = case.case_id  # MongoDB primary key (FR-MP-5)
        if owner_user_id:
            body["user_id"] = owner_user_id
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

    async def migrate_preauth_cases(self, anon_uid: str) -> int:
        """One-time, idempotent: stamp pre-Auth Cases with ``anon_uid``.

        OQ-0115-CASE-USER-LINK (job-0252, sprint-13.5): Cases written before
        the Auth track carry no ``user_id`` field. The old
        ``{"user_id": {"$exists": False}}`` clause in ``list_cases_for_user``
        leaked every such Case to every signed-in user. This migration
        assigns ``user_id = anon_uid`` (the ``MIGRATION_ANON_UID`` sentinel)
        to every Case that lacks a ``user_id``, so a pre-Auth Case belongs to
        one synthetic owner instead of leaking.

        **Idempotent** by construction: the filter is
        ``{"user_id": {"$exists": False}}``, so a second run matches nothing
        (every Case now has a ``user_id``). Re-running is a safe no-op.

        **Non-corrupting**: a single ``$set`` of one field via the logical
        ``update-one`` surface (translated to ``update-many`` by the
        :class:`MCPSurfaceTranslator` so ALL matching orphans are stamped in
        one round-trip — ``update-one`` semantics would only touch one doc).
        No other field is read, written, or removed; sessions and chat
        histories are untouched (this method only ever writes the ``projects``
        collection).

        Returns the modified count when the backend reports one, else ``0``.
        Best-effort on count parsing — the migration's success is the absence
        of orphans on the next run, not the returned integer.
        """
        raw = await self._mcp.call_tool(
            "update-many",
            {
                "database": self._db,
                "collection": CASES_COLLECTION,
                "filter": {"user_id": {"$exists": False}},
                "update": {"$set": {"user_id": anon_uid}},
            },
        )
        # Best-effort: surface the modified count for the startup log. The
        # real server returns a text/EJSON blob; the mock/file backends return
        # a plain dict. Tolerate every shape.
        modified = 0
        payload = _unwrap_mcp_result(raw) if isinstance(raw, dict) else raw
        if isinstance(payload, dict):
            for k in ("modifiedCount", "modified_count", "nModified"):
                v = payload.get(k)
                if isinstance(v, int):
                    modified = v
                    break
        logger.info(
            "pre-Auth case migration: stamped %s orphan case(s) with user_id=%s",
            modified,
            anon_uid,
        )
        return modified

    async def list_cases_for_user(self, user_id: str) -> list[CaseSummary]:
        """List the user's LIVE Cases (``status="active"`` only).

        v0.1 Auth-stub note: the ``projects`` collection schema does not
        currently carry a ``user_id`` field (FR-MP-5 was specified pre-Auth).
        We pass the filter anyway — once the Auth/Users track adds the field
        the query starts narrowing; until then it returns the full Case list
        for the deployment. Surfaced as OQ-0115-CASE-USER-LINK.

        job-0267 (server-side case-list hardening): soft-deleted and archived
        Cases are excluded HERE, in the query AND a post-validation guard —
        the user saw a deleted ghost in the left rail because exclusion was
        previously a client-side concern. The ``$nin`` filter still matches
        docs with no ``status`` field at all (pre-status records are live by
        definition: ``CaseSummary.status`` defaults to ``"active"``); the
        Python guard is the belt-and-suspenders for MCP backends whose filter
        dialect quietly ignores the operator.

        job-0252 (sprint-13.5, OQ-0115-CASE-USER-LINK): the
        ``{"user_id": {"$exists": False}}`` backward-compat clause is GONE.
        It used to leak every pre-Auth Case (no ``user_id``) to every
        signed-in user. The one-time startup migration
        (``migrate_preauth_cases``) now stamps those orphan Cases with
        ``MIGRATION_ANON_UID``, so a Case is visible only to its owner.
        """
        raw = await self._mcp.call_tool(
            "find",
            {
                "database": self._db,
                "collection": CASES_COLLECTION,
                "filter": {
                    "$or": [
                        {"user_id": user_id},
                        {"owner_user_id": user_id},
                    ],
                    # job-0267: tombstones never reach the wire.
                    "status": {"$nin": ["deleted", "archived"]},
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
                case = self._doc_to_case_summary(d)
            except Exception:  # noqa: BLE001 — skip malformed docs
                logger.warning("skipping malformed Case doc: %s", d)
                continue
            if case.status in ("deleted", "archived"):
                # job-0267 guard: backend ignored/mangled the $nin filter.
                continue
            cases.append(case)
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
        # job-0267: deterministic replay order regardless of backend sort
        # support — the full stream (user turns, tool cards, agent narration)
        # interleaves by ``created_at``; ULID ``message_id`` breaks ties in
        # write order. Python's sort is stable, so backends that already
        # honored the ``created_at`` sort are untouched.
        chat.sort(key=lambda m: (m.created_at, m.message_id))
        # job-0172 Part B: hydrate ``loaded_layers`` from the persisted
        # ``Case.loaded_layer_summaries`` so a Case re-open repopulates the
        # LayerPanel deterministically. The PipelineEmitter holds these in
        # memory per-connection; without this hydration step a browser
        # refresh (new WS, new emitter) shows an empty LayerPanel even
        # though the layers are still published on the per-Case ``.qgs``.
        loaded_layers = list(case.loaded_layer_summaries)
        # job-0294b (sprint-14-aws): hydrate persisted charts so a Case re-open
        # replays them WITHOUT a re-run. job-0230 ``$push``es SessionChartRecords
        # onto the ``sessions`` doc (keyed by case_id == sessions._id) but the
        # read side was never wired. Pull the array, unwrap each record's
        # ``payload`` (the ChartEmissionPayload the client rehydrates), in
        # emitted_at order. Best-effort: a missing/odd doc yields no charts.
        charts: list[dict] = []
        try:
            sraw = await self._mcp.call_tool(
                "find-one",
                {
                    "database": self._db,
                    "collection": SESSIONS_COLLECTION,
                    "filter": {"_id": case_id},
                },
            )
            sdoc = _unwrap_mcp_result(sraw)
            if isinstance(sdoc, dict) and isinstance(sdoc.get("charts"), list):
                records = [r for r in sdoc["charts"] if isinstance(r, dict)]
                records.sort(key=lambda r: r.get("emitted_at") or "")
                for r in records:
                    payload = r.get("payload")
                    if isinstance(payload, dict):
                        charts.append(payload)
        except Exception:  # noqa: BLE001 — chart replay is best-effort
            logger.warning("get_session_state: chart hydration failed case=%s", case_id)
        return CaseSessionState(
            case=case, chat_history=chat, loaded_layers=loaded_layers, charts=charts,
        )

    # ----- Materialized case-view snapshot (Lane A1: view-without-agent) ---- #

    async def build_case_view_snapshot(
        self,
        case_id: str,
        *,
        inline_geojson_by_layer_id: dict[str, Any] | None = None,
        density_meta_by_layer_id: dict[str, Any] | None = None,
    ) -> dict[str, Any]:
        """Assemble the materialized case-view snapshot dict (no I/O).

        The snapshot is the EXACT payload ``server._emit_case_open`` ships on the
        wire — ``CaseOpenEnvelopePayload(session_state=get_session_state(...))``
        serialized with ``model_dump(mode="json")`` — so the web's existing
        ``useCases.onCaseOpen`` + ``App.tsx`` synthesize path renders it
        verbatim from S3 with the agent box OFF.

        The ONE addition is the inline vector GeoJSON: persisted vector layers
        carry no inline GeoJSON (the side-table is in-memory on the live
        emitter; ``server.reinline_vector_layers`` repopulates it only for an
        OPEN socket). For a true cold view we MERGE that GeoJSON (and any
        dense-vector ``vector_density`` tag) onto the matching ``loaded_layers``
        entries here — byte-for-byte the same merge ``emit_session_state``
        performs on the live wire (additive ``inline_geojson`` / density fields).

        Pure: builds and returns the dict; ``write_case_view_snapshot`` does the
        S3 put. Split so the contract test can assert the shape without S3.
        """
        session_state = await self.get_session_state(case_id)
        payload = CaseOpenEnvelopePayload(session_state=session_state)
        snapshot = payload.model_dump(mode="json")
        inline = inline_geojson_by_layer_id or {}
        density = density_meta_by_layer_id or {}
        if not inline and not density:
            return snapshot
        # Merge inline GeoJSON / density tags into loaded_layers, mirroring
        # PipelineEmitter.emit_session_state EXACTLY (same field names, same
        # best-effort density-tag handling) so a cold view paints vectors and a
        # warm case-open are indistinguishable to the client.
        ss = snapshot.get("session_state")
        if isinstance(ss, dict):
            layers = ss.get("loaded_layers")
            if isinstance(layers, list):
                for layer in layers:
                    if not isinstance(layer, dict):
                        continue
                    lid = layer.get("layer_id")
                    geojson_obj = inline.get(lid)
                    if geojson_obj is not None:
                        layer["inline_geojson"] = geojson_obj
                    meta = density.get(lid)
                    if meta is not None:
                        try:
                            layer.update(meta.as_wire_tag())
                        except Exception:  # noqa: BLE001 — match live merge
                            pass
        return snapshot

    async def _resolve_case_owner(self, case_id: str) -> str | None:
        """Resolve a Case's owner from the RAW ``projects`` doc (best-effort).

        Reads ``owner_user_id`` (preferred) or ``user_id`` straight off the
        stored document — the same owner-link fields ``list_cases_for_user``
        filters on — BEFORE the owner-stripping ``_doc_to_case_summary`` runs.
        Those fields are deliberately dropped from the ``CaseSummary`` envelope
        (and therefore from the snapshot BODY), so the snapshot writer must read
        them from the raw doc here to carry the owner in S3 OBJECT METADATA.

        Returns ``None`` when the Case is missing or carries no owner link
        (the legacy / pre-Auth shape). Best-effort: any read hiccup yields
        ``None`` so a snapshot write is never blocked on the owner probe.
        """
        try:
            raw = await self._mcp.call_tool(
                "find-one",
                {
                    "database": self._db,
                    "collection": CASES_COLLECTION,
                    "filter": {"_id": case_id},
                },
            )
            doc = _unwrap_mcp_result(raw)
            if not isinstance(doc, dict):
                return None
            owner = doc.get("owner_user_id") or doc.get("user_id")
            return owner if isinstance(owner, str) and owner else None
        except Exception:  # noqa: BLE001 — owner probe is best-effort
            logger.warning(
                "case-view-snapshot owner probe failed case=%s", case_id
            )
            return None

    async def write_case_view_snapshot(
        self,
        case_id: str,
        *,
        inline_geojson_by_layer_id: dict[str, Any] | None = None,
        density_meta_by_layer_id: dict[str, Any] | None = None,
        s3_put: Any = None,
    ) -> bool:
        """Materialize the case-view snapshot to S3 (view-without-agent path).

        Writes ``s3://{CASE_VIEWS_BUCKET}/case-views/{case_id}.json`` (PRIVATE;
        ``content-type: application/json``) so the signer Lambda (infra lane) can
        hand out a pre-signed GET and a user can VIEW a Case with the agent box
        asleep. Called on every Case MUTATION (layer publish, per-turn persist,
        case create/rename) — idempotent, last-write-wins.

        Owner-gate carrier (adversarial-review fix): the snapshot BODY strips the
        owner-link fields (``_doc_to_case_summary`` drops ``user_id`` /
        ``owner_user_id``), so the signer could never owner-match off the body.
        We resolve the owner from the RAW ``projects`` doc and carry it in S3
        OBJECT METADATA (``owner-user-id``) — NEVER in the JSON body. The signer
        reads it cheaply via ``head_object`` (no full download). The metadata key
        is set ONLY when the Case has an owner; the BODY is byte-identical with
        or without an owner.

        Best-effort by contract: wrapped in ``try/except`` and returns ``False``
        on any failure so a snapshot hiccup NEVER breaks the user's turn (the
        same discipline as ``touch_session`` / chart persistence). Returns
        ``True`` on a successful put.

        ``s3_put`` injects a callable
        ``(bucket, key, body_bytes, metadata) -> None`` for tests (a fake S3
        capture; ``metadata`` is ``{"owner-user-id": <owner>}`` or ``{}`` when
        the Case has no owner); production lazily constructs a boto3 S3 client
        whose creds boto3 resolves from the EC2 instance role (same chain as
        ``case_lifecycle.default_gcs_copy`` / the dense-vector reader).
        """
        import json

        try:
            snapshot = await self.build_case_view_snapshot(
                case_id,
                inline_geojson_by_layer_id=inline_geojson_by_layer_id,
                density_meta_by_layer_id=density_meta_by_layer_id,
            )
            body = json.dumps(snapshot, separators=(",", ":")).encode("utf-8")
            key = case_view_snapshot_key(case_id)
            # Owner lives ONLY in object metadata, never in the body. S3
            # lowercases metadata keys — use the lowercase key directly so the
            # signer's ``resp["Metadata"].get("owner-user-id")`` matches.
            owner = await self._resolve_case_owner(case_id)
            metadata: dict[str, str] = (
                {"owner-user-id": owner} if owner else {}
            )
            if s3_put is not None:
                _maybe = s3_put(CASE_VIEWS_BUCKET, key, body, metadata)
                # Allow either a sync or async injected put.
                if hasattr(_maybe, "__await__"):
                    await _maybe
            else:
                await self._default_s3_put_case_view(key, body, metadata)
            logger.debug(
                "case-view-snapshot wrote s3://%s/%s bytes=%d owner=%s",
                CASE_VIEWS_BUCKET,
                key,
                len(body),
                owner or "(none)",
            )
            return True
        except Exception:  # noqa: BLE001 — never break a turn
            logger.warning(
                "case-view-snapshot write failed case=%s bucket=%s",
                case_id,
                CASE_VIEWS_BUCKET,
            )
            return False

    @staticmethod
    async def _default_s3_put_case_view(
        key: str, body: bytes, metadata: dict[str, str] | None = None
    ) -> None:
        """Production S3 put for the case-view snapshot.

        Runs the synchronous boto3 ``put_object`` in a worker thread so the
        async turn loop is never blocked (the same off-thread discipline the
        DynamoDB backend uses). boto3 resolves creds + region from the standard
        chain (env / ~/.aws / EC2 instance role — job-0289 lesson).

        ``metadata`` is the S3 OBJECT METADATA dict (the owner-gate carrier:
        ``{"owner-user-id": <owner>}`` or ``{}`` / ``None`` when the Case has no
        owner). Passed through to boto3 ``put_object(Metadata=...)`` ONLY when
        non-empty so an owner-less snapshot stamps no metadata. The owner is
        carried here, NOT in the JSON body, so the body stays byte-identical.
        """
        import asyncio

        meta = dict(metadata or {})

        def _put() -> None:
            import boto3

            s3 = boto3.client(
                "s3", region_name=os.environ.get("AWS_REGION", "us-west-2")
            )
            kwargs: dict[str, Any] = dict(
                Bucket=CASE_VIEWS_BUCKET,
                Key=key,
                Body=body,
                ContentType="application/json",
            )
            if meta:
                kwargs["Metadata"] = meta
            s3.put_object(**kwargs)

        await asyncio.to_thread(_put)

    # ----- Session records (D.6 ``sessions`` collection) ------------------- #
    #
    # job-0203 (Wave 4.11 M4): the agent's own session record goes live. The
    # ``sessions`` document is the TTL-cleaned activity header (D.6 +
    # ``SESSIONS_TTL``): who/when, which Cases were touched, and — since
    # job-0230 — the append-only ``charts`` array that chart-emission
    # ``$push``es onto. Chat content canonically lives in
    # ``case_chat_messages`` (FR-MP-6); ``SessionDocument.chat_history``
    # stays empty at v0.1 so the two stores never diverge.

    async def upsert_session_record(self, doc: "SessionDocument") -> None:
        """Insert or fully overwrite a session record.

        ``$set`` of the full document body — storage-only extras a previous
        ``$push`` added (e.g. ``charts``) survive because ``$set`` of named
        fields does not remove unnamed ones.
        """
        body = doc.model_dump(mode="json", by_alias=True)
        session_id = body.pop("_id")
        await self._mcp.call_tool(
            "update-one",
            {
                "database": self._db,
                "collection": SESSIONS_COLLECTION,
                "filter": {"_id": session_id},
                "update": {"$set": body},
                "upsert": True,
            },
        )

    async def touch_session(
        self,
        session_id: str,
        *,
        client_fingerprint: str | None = None,
        case_id: str | None = None,
        ttl_seconds: int | None = None,
    ) -> None:
        """Activity heartbeat for a session — one upsert round-trip.

        - ``$set`` ``last_active_at`` + ``expires_at`` (TTL driver, D.6) so
          every interaction pushes cleanup 30 days out (``SESSIONS_TTL``).
        - ``$setOnInsert`` the immutable header (``schema_version``,
          ``created_at``) so the first touch creates a well-formed record
          and later touches never rewrite history.
        - ``$addToSet`` the active Case into ``project_ids`` when given —
          deduped, so per-turn touches stay idempotent.

        Fire-and-forget discipline at call sites (same as telemetry M3 and
        chart persistence job-0230): callers wrap in ``try/except`` or a
        task; a persistence hiccup never takes down the user's turn.
        """
        from grace2_contracts.collections import SESSIONS_TTL

        now = now_utc()
        ttl = ttl_seconds if ttl_seconds is not None else SESSIONS_TTL["expire_after_seconds"]
        from datetime import timedelta

        iso_now = now.isoformat().replace("+00:00", "Z")
        iso_exp = (now + timedelta(seconds=ttl)).isoformat().replace("+00:00", "Z")
        set_fields: dict[str, Any] = {
            "last_active_at": iso_now,
            "expires_at": iso_exp,
        }
        if client_fingerprint is not None:
            set_fields["client_fingerprint"] = client_fingerprint
        update: dict[str, Any] = {
            "$set": set_fields,
            "$setOnInsert": {
                "schema_version": "v1",
                "created_at": iso_now,
            },
        }
        if case_id is not None:
            update["$addToSet"] = {"project_ids": case_id}
        await self._mcp.call_tool(
            "update-one",
            {
                "database": self._db,
                "collection": SESSIONS_COLLECTION,
                "filter": {"_id": session_id},
                "update": update,
                "upsert": True,
            },
        )
        # Header repair: a session doc created by an earlier bare ``$push``
        # (chart-emission upserts before any touch — job-0230 ordering) has
        # no ``created_at``/``schema_version``, and ``$setOnInsert`` above
        # can never backfill an EXISTING doc (real Mongo semantics too).
        # Detect and repair once; ``created_at=now`` is the best available
        # approximation for a doc whose true start was never recorded.
        raw = await self._mcp.call_tool(
            "find-one",
            {
                "database": self._db,
                "collection": SESSIONS_COLLECTION,
                "filter": {"_id": session_id},
            },
        )
        doc = _unwrap_mcp_result(raw)
        if isinstance(doc, dict) and (
            "created_at" not in doc or "schema_version" not in doc
        ):
            repair: dict[str, Any] = {}
            if "created_at" not in doc:
                repair["created_at"] = iso_now
            if "schema_version" not in doc:
                repair["schema_version"] = "v1"
            await self._mcp.call_tool(
                "update-one",
                {
                    "database": self._db,
                    "collection": SESSIONS_COLLECTION,
                    "filter": {"_id": session_id},
                    "update": {"$set": repair},
                },
            )

    async def set_session_active_case(
        self, session_id: str, case_id: str | None
    ) -> None:
        """Persist the session's active-Case pointer (job-CASE-AUTHORITY).

        Writes a storage-only ``last_active_case_id`` field onto the session
        record so the active-Case pointer survives an EC2 auto-stop/restart
        (the in-memory ``_SESSION_ACTIVE_CASE`` dict in server.py is wiped on
        process death). ``SessionDocument`` deliberately does NOT carry this
        field — it is storage-only, exactly like the job-0230 ``charts`` array;
        ``get_session_record`` drops unknown fields before validation, so the
        contract model stays narrow while the storage doc accretes.

        The client-stamped ``case_id`` on ``session-resume`` /
        ``user-message`` remains the REAL authority for turn-binding + replay;
        this persisted pointer is only the cold-start cache so a reconnecting
        client that sends a bare resume (older client, no stamp) still lands on
        the Case it last worked in instead of None.

        ``$set`` (with ``upsert``) so the pointer lands even if no prior
        ``touch_session`` created the doc; ``$setOnInsert`` mirrors
        ``touch_session`` so a doc created HERE first is still well-formed.
        ``case_id=None`` clears the pointer (an explicit Case exit).
        Fire-and-forget at call sites: a persistence hiccup must never take
        down the user's turn.
        """
        now = now_utc()
        iso_now = now.isoformat().replace("+00:00", "Z")
        await self._mcp.call_tool(
            "update-one",
            {
                "database": self._db,
                "collection": SESSIONS_COLLECTION,
                "filter": {"_id": session_id},
                "update": {
                    "$set": {"last_active_case_id": case_id},
                    "$setOnInsert": {
                        "schema_version": "v1",
                        "created_at": iso_now,
                    },
                },
                "upsert": True,
            },
        )

    async def get_session_active_case(self, session_id: str) -> str | None:
        """Read back the persisted active-Case pointer (job-CASE-AUTHORITY).

        Returns the ``last_active_case_id`` written by
        ``set_session_active_case``, or ``None`` when the session has no
        record / no persisted pointer (a fresh session, or one that never
        bound a Case). Used by server.py to reload the in-memory pointer when a
        fresh ``SessionState`` is built after an EC2 restart, so the cold-start
        cache survives process death. Best-effort: any malformed shape yields
        ``None``.
        """
        raw = await self._mcp.call_tool(
            "find-one",
            {
                "database": self._db,
                "collection": SESSIONS_COLLECTION,
                "filter": {"_id": session_id},
            },
        )
        doc = _unwrap_mcp_result(raw)
        if not isinstance(doc, dict):
            return None
        value = doc.get("last_active_case_id")
        return value if isinstance(value, str) else None

    async def get_session_record(self, session_id: str) -> "SessionDocument | None":
        """Read one session record back as a typed ``SessionDocument``.

        Tolerant normalization (same discipline as ``_doc_to_case_summary``):
        storage-only extras — notably the job-0230 ``charts`` array — are
        dropped before validation so the contract model stays narrow while
        the storage document accretes.
        """
        from grace2_contracts.collections import SessionDocument

        raw = await self._mcp.call_tool(
            "find-one",
            {
                "database": self._db,
                "collection": SESSIONS_COLLECTION,
                "filter": {"_id": session_id},
            },
        )
        doc = _unwrap_mcp_result(raw)
        if not doc or not isinstance(doc, dict):
            return None
        allowed = set(SessionDocument.model_fields.keys())
        # ``id`` is aliased to ``_id`` — keep the alias key, drop the rest.
        normalized = {
            k: v for k, v in doc.items() if k in allowed or k == "_id"
        }
        try:
            return SessionDocument.model_validate(normalized)
        except Exception:  # noqa: BLE001
            logger.warning("malformed session doc for session_id=%s", session_id)
            return None

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

    async def get_user_by_id(self, user_id: str) -> User | None:
        """Find a user by ULID. Returns ``None`` if not found.

        job-0172 Part C: the anonymous-fallback path needs an id-based lookup
        so a reconnecting browser can re-bind to the same ephemeral User via
        the ``AuthTokenEnvelope.anonymous_user_id`` hint. Mirrors the shape
        of ``get_user_by_firebase_uid`` so the call site stays symmetric.
        """
        raw = await self._mcp.call_tool(
            "find-one",
            {
                "database": self._db,
                "collection": USERS_COLLECTION,
                "filter": {"_id": user_id},
            },
        )
        doc = _unwrap_mcp_result(raw)
        if not doc or not isinstance(doc, dict):
            return None
        normalized = {k: v for k, v in doc.items() if k != "_id"}
        if "user_id" not in normalized:
            normalized["user_id"] = user_id
        # Forward-compat: drop fields the v0.1 schema doesn't carry so a
        # future User schema bump doesn't break the existing record.
        allowed = set(User.model_fields.keys())
        normalized = {k: v for k, v in normalized.items() if k in allowed}
        try:
            return User.model_validate(normalized)
        except Exception:  # noqa: BLE001
            logger.warning("malformed user doc for user_id=%s", user_id)
            return None

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
        # user_id linking is enforced once Auth lands. job-0252
        # (sprint-13.5): the ``{"user_id": {"$exists": False}}`` backward-
        # compat clause is GONE — it leaked pre-Auth secret records to every
        # user. A secret record belongs only to its owner.
        if user_id:
            filt["$or"] = [
                {"user_id": user_id},
                {"owner_user_id": user_id},
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
        ssm_client=None,
    ) -> str:
        """Read the live key value from the vault backend (job-0124 + AWS fix).

        Called by Tier-2 fetchers (FIRMS / eBird / ERA5 / etc.) at
        tool-invocation time to materialize the raw key for the outbound
        HTTP request — including the credential-card RETRY path. The caller
        never logs the returned value.

        Backend routing is by the ``vault_ref`` *scheme* (not the env), so a
        key written under one backend still resolves even if the env later
        flips — the env only chooses the WRITE backend:

        - ``aws-ssm://<param-name>`` → AWS SSM ``get_parameter`` with
          ``WithDecryption=True`` (KMS-decrypted SecureString). This is the
          AWS-stack path (the prod EC2 box has no GCP ADC — the GCP path was
          the demo blocker NATE hit 2026-06-17).
        - ``gcp-sm://…`` or a bare ``projects/…/versions/…`` resource name →
          GCP Secret Manager ``access_secret_version`` (the default path).

        Fail-closed semantics:

        - If the record's ``is_active`` flag is ``False`` (soft-revoked),
          we raise ``SecretRevokedError`` BEFORE touching any vault so a
          revoked secret never resurrects via stale cache.
        - If the vault fetch raises (missing version, permission denied,
          etc.) we surface the original exception — Tier-2 fetchers wrap
          this in a tool-level error envelope.

        Args:
            secret_ref: the persisted ``SecretRecord`` (vault-ref only).
            secret_manager_client: optional pre-constructed GCP client (tests
                pass a mock; production lazy-constructs a live client).
            ssm_client: optional pre-constructed AWS SSM client (tests pass a
                mock; production lazy-constructs a live boto3 client).

        Returns:
            The raw key value as a string. **Caller MUST NOT log this.**

        Raises:
            SecretRevokedError: when ``secret_ref.is_active is False``.
        """
        # Local import — avoids a circular dependency between persistence
        # and secrets_handler (which imports Persistence).
        from .secrets_handler import (
            AWS_SSM_VAULT_SCHEME,
            GCP_SM_VAULT_SCHEME,
            SecretRevokedError,
            _default_ssm_client,
        )

        if not secret_ref.is_active:
            raise SecretRevokedError(
                f"secret {secret_ref.secret_id!r} has been revoked "
                f"(provider={secret_ref.provider})"
            )

        ref = secret_ref.vault_ref

        # AWS SSM Parameter Store SecureString path (AWS prod stack).
        if ref.startswith(AWS_SSM_VAULT_SCHEME):
            param_name = ref[len(AWS_SSM_VAULT_SCHEME) :]
            client = ssm_client or _default_ssm_client()
            response = client.get_parameter(
                Name=param_name, WithDecryption=True
            )
            # boto3 returns {"Parameter": {"Value": "...", ...}}; mock clients
            # used in tests return the same shape.
            param = response.get("Parameter") if isinstance(response, dict) else None
            value = param.get("Value") if isinstance(param, dict) else None
            if value is None:
                raise RuntimeError(
                    "SSM get_parameter returned no Parameter.Value"
                )
            return str(value)

        # GCP Secret Manager path (default). The stored vault_ref is the
        # resource name (no scheme prefix); tolerate the legacy ``gcp-sm://``
        # shape by stripping it before the SDK call.
        name = ref
        if name.startswith(GCP_SM_VAULT_SCHEME):
            name = name[len(GCP_SM_VAULT_SCHEME) :]

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
        """Tiny query matcher: equality, ``$or``, ``$exists``, ``$nin``."""
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
            if isinstance(v, dict) and "$nin" in v:
                # Mongo-faithful: a MISSING field matches $nin (the doc's
                # value, None, is "not in" the exclusion list unless None is
                # listed). job-0267 uses this for the case-list status filter
                # so pre-status Case docs stay listed.
                if doc.get(k) in v["$nin"]:
                    return False
                continue
            if doc.get(k) != v:
                return False
        return True

    # ------------------------------------------------------------------ #
    # Update-operator application (job-0203 / M4)
    # ------------------------------------------------------------------ #

    @staticmethod
    def _apply_update(doc: dict, update: dict, *, inserting: bool) -> None:
        """Apply a Mongo update document in-place, Mongo-faithful semantics.

        Supported operators (the set Persistence + chart-emission actually
        send): ``$set``, ``$setOnInsert`` (applied ONLY when ``inserting``),
        ``$push`` (appends; creates the array if missing), ``$addToSet``
        (appends iff not already present — dict values compared by equality).

        Before job-0203 only ``$set`` was honored, which silently DROPPED the
        job-0230 chart ``$push`` on the dev substrate (the upsert created a
        bare ``{_id}`` doc and the chart vanished). Unknown operators now
        raise so the next gap fails loudly instead.
        """
        for op, fields in update.items():
            if op == "$set":
                doc.update(fields)
            elif op == "$setOnInsert":
                if inserting:
                    for k, v in fields.items():
                        doc.setdefault(k, v)
            elif op == "$push":
                for k, v in fields.items():
                    arr = doc.get(k)
                    if not isinstance(arr, list):
                        arr = []
                        doc[k] = arr
                    arr.append(v)
            elif op == "$addToSet":
                for k, v in fields.items():
                    arr = doc.get(k)
                    if not isinstance(arr, list):
                        arr = []
                        doc[k] = arr
                    if v not in arr:
                        arr.append(v)
            else:
                raise NotImplementedError(
                    f"FileMCPClient update-one: unsupported operator {op!r} "
                    f"(supports $set / $setOnInsert / $push / $addToSet)"
                )

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
                upsert = bool(args.get("upsert", False))
                target_id = filt.get("_id")
                matched = 0
                modified = 0
                if target_id and target_id in store:
                    self._apply_update(store[target_id], update, inserting=False)
                    matched = 1
                    modified = 1
                elif upsert and target_id:
                    fresh: dict[str, Any] = {"_id": target_id}
                    self._apply_update(fresh, update, inserting=True)
                    store[target_id] = fresh
                    matched = 1
                    modified = 1
                else:
                    # Update by non-_id filter (e.g. firebase_uid). First match wins.
                    for doc in store.values():
                        if self._matches(doc, filt):
                            self._apply_update(doc, update, inserting=False)
                            matched = 1
                            modified = 1
                            break
                self._atomic_write(path, store)
                return {"matchedCount": matched, "modifiedCount": modified}

        if name == "update-many":
            # job-0252 (sprint-13.5): the pre-Auth case migration uses the
            # real-server ``update-many`` surface directly (the translator
            # passes it through). On the dev/file substrate there is no
            # translator, so we honor it here: apply the update to EVERY
            # matching doc. No upsert (the migration never upserts).
            async with lock:
                store = self._read_store(path)
                filt = args.get("filter", {})
                update = args.get("update", {})
                matched = 0
                modified = 0
                for doc in store.values():
                    if self._matches(doc, filt):
                        self._apply_update(doc, update, inserting=False)
                        matched += 1
                        modified += 1
                if modified:
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
            f"(supports insert-one / update-one / update-many / find-one / find)"
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


# --------------------------------------------------------------------------- #
# Backend selection (sprint-14-aws — additive, default 'file')
# --------------------------------------------------------------------------- #
#
# The AWS migration adds a DynamoDB backend (``dynamo_backend.DynamoMCPClient``)
# behind this same ``MCPClientProtocol`` seam. Selection is a NEW env
# ``GRACE2_PERSISTENCE_BACKEND`` (values: ``file`` | ``dynamodb``), default
# ``file`` so the CURRENT AWS-live runtime (file-backed) is UNCHANGED until the
# orchestrator flips the env on the EC2 agent service. This block is purely
# additive: it does not alter ``Persistence`` / ``FileMCPClient`` /
# ``MCPSurfaceTranslator`` semantics, and the actual selection CALL lives in
# ``main._maybe_bind_dev_persistence`` / ``server.init_persistence_from_env``
# (NOT this file — see the job's crossTrackChanges).

#: Env that selects the persistence backend. Re-exported from dynamo_backend so
#: there is a single name; mirrored here for callers that only import
#: persistence. Default keeps current (file) behavior.
PERSISTENCE_BACKEND_ENV = "GRACE2_PERSISTENCE_BACKEND"
PERSISTENCE_BACKEND_FILE = "file"
PERSISTENCE_BACKEND_DYNAMODB = "dynamodb"


def resolve_persistence_backend() -> str:
    """Resolve the configured persistence backend name.

    Returns ``"dynamodb"`` only when ``GRACE2_PERSISTENCE_BACKEND`` is set to
    ``dynamodb`` (case-insensitive); every other value — including unset —
    resolves to ``"file"`` so the demo stays file-backed by default.
    """
    raw = (_os_for_file.environ.get(PERSISTENCE_BACKEND_ENV) or "").strip().lower()
    if raw == PERSISTENCE_BACKEND_DYNAMODB:
        return PERSISTENCE_BACKEND_DYNAMODB
    return PERSISTENCE_BACKEND_FILE


def make_dynamo_persistence(
    *, table_prefix: str | None = None, resource: Any = None
) -> Persistence:
    """Construct a ``Persistence`` backed by the DynamoDB MCP shim.

    Thin re-export of ``dynamo_backend.make_dynamo_persistence`` (imported
    lazily so the file/Mongo paths never import boto3-resource machinery).
    """
    from .dynamo_backend import make_dynamo_persistence as _make

    return _make(table_prefix=table_prefix, resource=resource)


def make_persistence_for_backend(
    *, base_dir: _Path | None = None
) -> Persistence:
    """Build the ``Persistence`` for the env-selected backend.

    Default (``file``) returns ``make_file_persistence``; ``dynamodb`` returns
    the DynamoDB-backed ``Persistence``. The selection CALL sites
    (``main._maybe_bind_dev_persistence`` / ``server.init_persistence_from_env``)
    use this so the env is honored consistently across both binding paths.
    """
    if resolve_persistence_backend() == PERSISTENCE_BACKEND_DYNAMODB:
        return make_dynamo_persistence()
    return make_file_persistence(base_dir=base_dir)


__all__ = [
    "Persistence",
    "MCPClientProtocol",
    "MCPSurfaceTranslator",
    "FileMCPClient",
    "make_file_persistence",
    "make_dynamo_persistence",
    "make_persistence_for_backend",
    "resolve_persistence_backend",
    "is_dev_persistence_enabled",
    "DEFAULT_DATABASE",
    "DEV_PERSISTENCE_DIR_ENV",
    "DEV_PERSISTENCE_ENABLED_ENV",
    "PERSISTENCE_BACKEND_ENV",
    "PERSISTENCE_BACKEND_FILE",
    "PERSISTENCE_BACKEND_DYNAMODB",
    "CASES_COLLECTION",
    "CHAT_COLLECTION",
    "SESSIONS_COLLECTION",
    "USERS_COLLECTION",
    "SECRETS_COLLECTION",
    "AUDIT_COLLECTION",
    "CASE_VIEWS_BUCKET",
    "CASE_VIEWS_PREFIX",
    "case_view_snapshot_key",
]
