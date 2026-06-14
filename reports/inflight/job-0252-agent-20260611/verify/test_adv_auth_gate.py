"""ADVERSARIAL verifier tests for job-0252 (panel, CORRECTNESS lens).

Goal: REFUTE the AUTH_REQUIRED gate. Find ANY path that reaches a bound
session / a user-scoped dispatch without a valid Firebase token when the
gate is engaged. Drive the REAL handler loop end-to-end (not just the
helper functions in isolation) so any seam where dispatch happens BEFORE
the gate is caught.

No Gemini/Vertex, no live Firebase, no live Mongo, no live process restart.
Verify hook is mocked; persistence is in-memory MockMCPClient or the real
FileMCPClient against a tmp dir.
"""

from __future__ import annotations

import json
import tempfile
from pathlib import Path
from typing import Any

import pytest

from grace2_agent import auth as agent_auth
from grace2_agent.auth import (
    AUTH_CLOSE_CODE,
    AUTH_FAILED_ERROR_CODE,
    AUTH_REQUIRED_ENV,
    MIGRATION_ANON_UID,
)
from grace2_agent.auth_handshake import set_verify_hook
from grace2_agent.persistence import (
    CASES_COLLECTION,
    FileMCPClient,
    Persistence,
)
from grace2_contracts.case import CaseSummary
from grace2_contracts.common import new_ulid, now_utc

from grace2_agent import server as srv


# --------------------------------------------------------------------------- #
# Mock MCP client (mirrors the one in the job's own test file)
# --------------------------------------------------------------------------- #


class MockMCPClient:
    def __init__(self) -> None:
        self._store: dict[str, dict[str, dict]] = {}
        self.calls: list[tuple[str, dict]] = []

    async def call_tool(self, name, arguments=None):
        args = dict(arguments or {})
        self.calls.append((name, args))
        coll = args.get("collection") or "_default"
        store = self._store.setdefault(coll, {})
        if name == "insert-one":
            doc = args["document"]
            store[doc["_id"]] = doc
            return {"insertedId": doc["_id"]}
        if name == "update-one":
            filt = args.get("filter", {})
            set_ = args.get("update", {}).get("$set", {})
            upsert = args.get("upsert", False)
            target_id = filt.get("_id")
            if target_id and target_id in store:
                store[target_id].update(set_)
            elif upsert and target_id:
                store[target_id] = {**set_, "_id": target_id}
            return {"matchedCount": 1, "modifiedCount": 1}
        if name == "update-many":
            filt = args.get("filter", {})
            set_ = args.get("update", {}).get("$set", {})
            modified = 0
            for doc in store.values():
                if self._matches(doc, filt):
                    doc.update(set_)
                    modified += 1
            return {"matchedCount": modified, "modifiedCount": modified}
        if name == "find-one":
            filt = args.get("filter", {})
            for doc in store.values():
                if self._matches(doc, filt):
                    return {"document": doc}
            return {"document": None}
        if name == "find":
            filt = args.get("filter", {})
            return {"documents": [d for d in store.values() if self._matches(d, filt)]}
        raise NotImplementedError(name)

    @staticmethod
    def _matches(doc: dict, filt: dict) -> bool:
        for k, v in filt.items():
            if k == "$or":
                if not any(MockMCPClient._matches(doc, s) for s in v):
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
                if doc.get(k) in v["$nin"]:
                    return False
                continue
            if doc.get(k) != v:
                return False
        return True


class _RecordingWS:
    """WS stand-in that records sent envelopes + close, and replays a
    scripted list of inbound raw messages as an async iterator (drives the
    real handler loop)."""

    def __init__(self, inbound: list[dict]) -> None:
        self._inbound = [json.dumps(m) for m in inbound]
        self.sent: list[dict] = []
        self.closed_with: tuple[int, str] | None = None

    async def send(self, raw: str) -> None:
        self.sent.append(json.loads(raw))

    async def close(self, code: int = 1000, reason: str = "") -> None:
        self.closed_with = (code, reason)

    def __aiter__(self):
        self._it = iter(self._inbound)
        return self

    async def __anext__(self):
        try:
            return next(self._it)
        except StopIteration:
            raise StopAsyncIteration


def _fresh_case(title: str = "c") -> CaseSummary:
    return CaseSummary(
        case_id=new_ulid(),
        title=title,
        created_at=now_utc(),
        updated_at=now_utc(),
        status="active",
    )


def _settings():
    from grace2_agent.adapter import GeminiSettings

    return GeminiSettings(
        model="gemini-x", project="p", location="us", use_vertex=False
    )


@pytest.fixture(autouse=True)
def _restore():
    yield
    set_verify_hook(None)
    srv.set_persistence(None)


@pytest.fixture()
def _required(monkeypatch):
    monkeypatch.setenv(AUTH_REQUIRED_ENV, "true")
    return monkeypatch


# =========================================================================== #
# ATTACK 1: send a user-message as the FIRST envelope (no auth-token) under
# the gate. Must be rejected BEFORE any dispatch. Drives the REAL loop.
# =========================================================================== #


@pytest.mark.asyncio
async def test_first_user_message_rejected_under_gate(_required):
    srv.set_persistence(Persistence(MockMCPClient()))
    sid = new_ulid()
    ws = _RecordingWS(
        [{"type": "user-message", "session_id": sid, "payload": {"text": "hi"}}]
    )
    handler = srv._make_handler(_settings())
    await handler(ws)  # type: ignore[arg-type]

    # rejected + closed 4401; AUTH_FAILED on the wire; no chat/turn artifacts.
    assert ws.closed_with is not None and ws.closed_with[0] == 4401
    errors = [e for e in ws.sent if e.get("type") == "error"]
    assert errors and errors[0]["payload"]["error_code"] == "AUTH_FAILED"
    # No turn ever dispatched: no pipeline-state / agent-message leaked.
    leaked = [e for e in ws.sent if e.get("type") in ("pipeline-state", "agent-message", "session-state")]
    assert leaked == [], f"dispatch leaked before auth: {leaked}"


# =========================================================================== #
# ATTACK 2: session-resume as the FIRST envelope under the gate. This is the
# scariest path: _handle_session_resume emits the case-list (a user-scoped
# read). Must be rejected before reaching it.
# =========================================================================== #


@pytest.mark.asyncio
async def test_first_session_resume_rejected_under_gate(_required):
    mock = MockMCPClient()
    # Seed a case so, if the gate failed, a case-list would leak it.
    p = Persistence(mock)
    await p.upsert_case(_fresh_case("secret"), owner_user_id=MIGRATION_ANON_UID)
    srv.set_persistence(p)

    sid = new_ulid()
    ws = _RecordingWS(
        [{"type": "session-resume", "session_id": sid, "payload": {}}]
    )
    handler = srv._make_handler(_settings())
    await handler(ws)  # type: ignore[arg-type]

    assert ws.closed_with is not None and ws.closed_with[0] == 4401
    # CRITICAL: no case-list / session-state ever reached the wire.
    assert not any(e.get("type") == "case-list" for e in ws.sent), "case-list LEAKED pre-auth"
    assert not any(e.get("type") == "session-state" for e in ws.sent)


# =========================================================================== #
# ATTACK 3: race — auth-token arrives, but the verify hook fails (forged),
# THEN the client tries to push a user-message in the same connection. The
# first auth-token reject must terminate the loop so the second envelope is
# never processed.
# =========================================================================== #


@pytest.mark.asyncio
async def test_forged_auth_then_user_message_never_dispatched(_required):
    srv.set_persistence(Persistence(MockMCPClient()))
    set_verify_hook(lambda t: None)  # forged
    sid = new_ulid()
    ws = _RecordingWS(
        [
            {"type": "auth-token", "session_id": sid, "payload": {"token": "x.y.z"}},
            {"type": "user-message", "session_id": sid, "payload": {"text": "pwned"}},
        ]
    )
    handler = srv._make_handler(_settings())
    await handler(ws)  # type: ignore[arg-type]

    assert ws.closed_with is not None and ws.closed_with[0] == 4401
    # Exactly one auth error, no second-envelope processing.
    assert not any(e.get("type") in ("pipeline-state", "agent-message") for e in ws.sent)
    assert not any(e.get("type") == "auth-ack" for e in ws.sent)


# =========================================================================== #
# ATTACK 4: empty-token + sticky-anonymous hint under the gate. The reuse
# path returns is_anonymous=True; the gate must still reject (no sticky-
# anonymous bypass of the prod sign-in requirement).
# =========================================================================== #


@pytest.mark.asyncio
async def test_sticky_anonymous_hint_rejected_under_gate(_required):
    srv.set_persistence(Persistence(MockMCPClient()))
    sid = new_ulid()
    ws = _RecordingWS(
        [
            {
                "type": "auth-token",
                "session_id": sid,
                "payload": {"token": "", "anonymous_user_id": new_ulid()},
            }
        ]
    )
    handler = srv._make_handler(_settings())
    await handler(ws)  # type: ignore[arg-type]
    assert ws.closed_with is not None and ws.closed_with[0] == 4401
    assert not any(e.get("type") == "auth-ack" for e in ws.sent)


# =========================================================================== #
# ATTACK 5: token with a "uid"-less claims dict (decoded but no uid) under
# the gate. authenticate_token treats this as anonymous → must reject.
# =========================================================================== #


@pytest.mark.asyncio
async def test_uidless_claims_rejected_under_gate(_required):
    srv.set_persistence(Persistence(MockMCPClient()))
    set_verify_hook(lambda t: {"email": "x@y.z"})  # claims present but NO uid
    sid = new_ulid()
    ws = _RecordingWS(
        [{"type": "auth-token", "session_id": sid, "payload": {"token": "a.b.c"}}]
    )
    handler = srv._make_handler(_settings())
    await handler(ws)  # type: ignore[arg-type]
    assert ws.closed_with is not None and ws.closed_with[0] == 4401
    assert not any(e.get("type") == "auth-ack" for e in ws.sent)


# =========================================================================== #
# ATTACK 6: VALID token then a real user-message under the gate — must NOT
# be rejected (positive control: the gate doesn't break legit auth).
# We stop the loop after the auth-ack by sending only the auth-token so we
# never reach Gemini. A valid bind => no close.
# =========================================================================== #


@pytest.mark.asyncio
async def test_valid_token_not_rejected_under_gate(_required):
    srv.set_persistence(Persistence(MockMCPClient()))
    set_verify_hook(lambda t: {"uid": "real-uid-42", "email": "n@x.z"})
    sid = new_ulid()
    ws = _RecordingWS(
        [{"type": "auth-token", "session_id": sid, "payload": {"token": "valid"}}]
    )
    handler = srv._make_handler(_settings())
    await handler(ws)  # type: ignore[arg-type]
    assert ws.closed_with is None  # not rejected
    assert any(e.get("type") == "auth-ack" for e in ws.sent)


# =========================================================================== #
# ATTACK 7: malformed first envelope under the gate. The handler's generic
# JSON guard fires BEFORE auth. Make sure this does not become a dispatch
# bypass: the malformed envelope is errored, and the NEXT real envelope is
# still gated.
# =========================================================================== #


@pytest.mark.asyncio
async def test_malformed_then_real_envelope_still_gated(_required):
    srv.set_persistence(Persistence(MockMCPClient()))
    sid = new_ulid()

    class _MalformedFirstWS(_RecordingWS):
        def __aiter__(self):
            self._raws = iter(
                [
                    "{ this is : not json",
                    json.dumps(
                        {"type": "user-message", "session_id": sid, "payload": {"text": "x"}}
                    ),
                ]
            )
            return self

        async def __anext__(self):
            try:
                return next(self._raws)
            except StopIteration:
                raise StopAsyncIteration

    ws = _MalformedFirstWS([])
    handler = srv._make_handler(_settings())
    await handler(ws)  # type: ignore[arg-type]
    # The real envelope after the malformed one must still be rejected.
    assert ws.closed_with is not None and ws.closed_with[0] == 4401
    assert not any(e.get("type") in ("pipeline-state", "agent-message") for e in ws.sent)


# =========================================================================== #
# ATTACK 8: gate OFF (default) — the SAME first user-message must NOT be
# rejected (regression guard for the live dev agent that has no env set).
# =========================================================================== #


@pytest.mark.asyncio
async def test_gate_off_preserves_anonymous(monkeypatch):
    monkeypatch.delenv(AUTH_REQUIRED_ENV, raising=False)
    srv.set_persistence(Persistence(MockMCPClient()))
    sid = new_ulid()
    ws = _RecordingWS(
        [{"type": "session-resume", "session_id": sid, "payload": {}}]
    )
    handler = srv._make_handler(_settings())
    await handler(ws)  # type: ignore[arg-type]
    # No reject; anonymous fallback bound; session-state emitted.
    assert ws.closed_with is None
    assert any(e.get("type") == "session-state" for e in ws.sent) or any(
        e.get("type") == "case-list" for e in ws.sent
    )


# =========================================================================== #
# MIGRATION ATTACK 9: run the migration TWICE against the REAL FileMCPClient
# (not the mock). Idempotent? Does it touch cases that already have owners?
# =========================================================================== #


@pytest.mark.asyncio
async def test_migration_idempotent_on_file_persistence():
    with tempfile.TemporaryDirectory() as d:
        client = FileMCPClient(base_dir=Path(d))
        p = Persistence(client)

        # Two orphans, one already owned by a real uid.
        orphan_a = _fresh_case("orphan A")
        orphan_b = _fresh_case("orphan B")
        owned = _fresh_case("owned")
        await p.upsert_case(orphan_a)
        await p.upsert_case(orphan_b)
        await p.upsert_case(owned, owner_user_id="real-owner-uid")

        first = await p.migrate_preauth_cases(MIGRATION_ANON_UID)
        assert first == 2, f"first run should stamp 2 orphans, got {first}"

        # Read the raw store from disk and assert ownership.
        store_path = client._collection_path(p._db, CASES_COLLECTION)
        store = json.loads(store_path.read_text())
        assert store[orphan_a.case_id]["user_id"] == MIGRATION_ANON_UID
        assert store[orphan_b.case_id]["user_id"] == MIGRATION_ANON_UID
        # The owned case is UNTOUCHED.
        assert store[owned.case_id]["user_id"] == "real-owner-uid"

        # SECOND run: idempotent, matches nothing.
        second = await p.migrate_preauth_cases(MIGRATION_ANON_UID)
        assert second == 0, f"second run must be a no-op, got {second}"

        # Owner of the real case STILL untouched after the second run.
        store2 = json.loads(store_path.read_text())
        assert store2[owned.case_id]["user_id"] == "real-owner-uid"
        assert store2[orphan_a.case_id]["user_id"] == MIGRATION_ANON_UID


# =========================================================================== #
# MIGRATION ATTACK 10: after migration on the file substrate, an orphan case
# is visible ONLY to MIGRATION_ANON_UID — the $exists:false leak is truly
# gone (visible to no arbitrary user before, exactly the migration owner
# after).
# =========================================================================== #


@pytest.mark.asyncio
async def test_migration_visibility_scoping_on_file_persistence():
    with tempfile.TemporaryDirectory() as d:
        p = Persistence(FileMCPClient(base_dir=Path(d)))
        orphan = _fresh_case("orphan")
        await p.upsert_case(orphan)

        # Before: invisible to everyone (leak clause gone).
        assert await p.list_cases_for_user(new_ulid()) == []
        assert await p.list_cases_for_user(MIGRATION_ANON_UID) == []

        await p.migrate_preauth_cases(MIGRATION_ANON_UID)

        listed = await p.list_cases_for_user(MIGRATION_ANON_UID)
        assert [c.case_id for c in listed] == [orphan.case_id]
        assert await p.list_cases_for_user(new_ulid()) == []


# =========================================================================== #
# MIGRATION ATTACK 11: a freshly created Case under the gate (owner = real
# uid) must NOT be swept by a subsequent migration (it already has user_id).
# This exercises the upsert_case owner-stamp + idempotency interplay.
# =========================================================================== #


@pytest.mark.asyncio
async def test_owned_case_not_reclaimed_by_migration():
    with tempfile.TemporaryDirectory() as d:
        client = FileMCPClient(base_dir=Path(d))
        p = Persistence(client)
        owned = _fresh_case("legit")
        await p.upsert_case(owned, owner_user_id="user-A")

        n = await p.migrate_preauth_cases(MIGRATION_ANON_UID)
        assert n == 0  # nothing to stamp

        store = json.loads(
            client._collection_path(p._db, CASES_COLLECTION).read_text()
        )
        assert store[owned.case_id]["user_id"] == "user-A"
        # And user-A can still see it; MIGRATION_ANON_UID cannot.
        assert [c.case_id for c in await p.list_cases_for_user("user-A")] == [owned.case_id]
        assert await p.list_cases_for_user(MIGRATION_ANON_UID) == []
