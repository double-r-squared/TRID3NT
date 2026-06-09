"""Test sticky anonymous user_id reuse (job-0172 Part C).

The H.3 anonymous-fallback path used to mint a fresh ULID on every connect,
so a browser refresh orphaned the user's Cases. The fix: the client persists
its assigned ``user_id`` in localStorage and replays it via
``AuthTokenEnvelope.anonymous_user_id``; the agent looks it up and re-binds
the same User record when ``is_anonymous=True``.

These tests exercise the agent-side logic in isolation (the web persistence
is verified separately in the web test suite).
"""

from __future__ import annotations

import asyncio
from typing import Any

import pytest

from grace2_agent.auth_handshake import authenticate_token
from grace2_agent.persistence import Persistence
from grace2_contracts.auth import AuthTokenEnvelope
from grace2_contracts.common import new_ulid, now_utc
from grace2_contracts.user import User


class FakeMCPClient:
    """In-memory MCP client that round-trips users/cases for tests."""

    def __init__(self) -> None:
        self.users: dict[str, dict] = {}

    async def call_tool(
        self, name: str, arguments: dict[str, Any] | None = None
    ) -> dict[str, Any]:
        args = arguments or {}
        coll = args.get("collection")
        if coll != "users":
            return {"document": None}
        if name == "find-one":
            filt = args.get("filter", {})
            uid = filt.get("_id")
            if uid and uid in self.users:
                return {"document": self.users[uid]}
            return {"document": None}
        if name == "update-one":
            filt = args.get("filter", {})
            update = args.get("update", {}).get("$set", {})
            uid = filt.get("_id")
            if uid is None:
                return {"matchedCount": 0, "modifiedCount": 0}
            if uid in self.users:
                self.users[uid].update(update)
            elif args.get("upsert"):
                self.users[uid] = dict(update)
            return {"matchedCount": 1, "modifiedCount": 1}
        return {}


@pytest.mark.asyncio
async def test_anonymous_reuse_rebinds_same_user_on_reconnect() -> None:
    """An ``anonymous_user_id`` hint re-binds the prior anonymous User."""
    client = FakeMCPClient()
    p = Persistence(client)

    # First connect — no hint, no token → mint a fresh anonymous user.
    first = await authenticate_token(AuthTokenEnvelope(token=""), p)
    assert first.is_anonymous
    assert first.user.is_anonymous is True
    assert first.user.user_id in client.users  # persisted

    # Second connect with the hint — must re-bind the SAME user_id.
    hint = first.user.user_id
    second = await authenticate_token(
        AuthTokenEnvelope(token="", anonymous_user_id=hint), p
    )
    assert second.is_anonymous
    assert second.user.user_id == hint
    # Same User document — not a fresh ULID.
    assert second.user.user_id == first.user.user_id


@pytest.mark.asyncio
async def test_anonymous_reuse_rejects_non_anonymous_record() -> None:
    """A hint pointing at a Firebase-verified User must NOT re-bind."""
    client = FakeMCPClient()
    p = Persistence(client)

    # Pre-seed a Firebase-verified User (is_anonymous=False).
    verified_id = new_ulid()
    verified = User(
        user_id=verified_id,
        firebase_uid="firebase-uid-001",
        created_at=now_utc(),
        is_anonymous=False,
    )
    await p.upsert_user(verified)

    # Client replays that id as an anonymous hint — agent MUST reject the
    # rebind (no JWT was presented) and mint a fresh anonymous user.
    result = await authenticate_token(
        AuthTokenEnvelope(token="", anonymous_user_id=verified_id), p
    )
    assert result.is_anonymous
    assert result.user.user_id != verified_id
    assert result.user.is_anonymous is True


@pytest.mark.asyncio
async def test_anonymous_reuse_missing_hint_mints_fresh() -> None:
    """A hint pointing at a missing User mints a fresh anonymous User."""
    client = FakeMCPClient()
    p = Persistence(client)

    fake_id = new_ulid()
    result = await authenticate_token(
        AuthTokenEnvelope(token="", anonymous_user_id=fake_id), p
    )
    assert result.is_anonymous
    assert result.user.user_id != fake_id
    assert result.user.is_anonymous is True


@pytest.mark.asyncio
async def test_anonymous_hint_ignored_when_persistence_absent() -> None:
    """No Persistence → fall through to fresh in-memory anonymous User."""
    hint = new_ulid()
    result = await authenticate_token(
        AuthTokenEnvelope(token="", anonymous_user_id=hint), persistence=None
    )
    assert result.is_anonymous
    # Without persistence we cannot look up the hint; result is a fresh user.
    assert result.user.user_id != hint
    assert result.user.is_anonymous is True
