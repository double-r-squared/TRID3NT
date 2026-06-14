"""Adversarial CORRECTNESS-lens probes for job-0252b (panel re-verify).

Fresh tests — independent of the job's own suite. The job tested with an
in-memory MockMCPClient; these drive the REAL ``authenticate_token`` (and the
real server gate ``_handle_auth_token``) against a REAL ``FileMCPClient``
file-backed Persistence, counting every MCP call and inspecting the on-disk
``users.json`` before/after:

  B1. gate ON + empty token (no hint)        -> zero MCP calls, no users.json
  B2. gate ON + empty token + REAL anon hint -> zero MCP calls after seed,
      users.json byte-identical (no sticky read, no write)
  B3. gate ON + forged token (verify fails)  -> zero MCP calls, no users.json
  B4. gate ON + claims missing uid           -> zero MCP calls, no users.json
  B5. gate OFF + forged token                -> anonymous row IS persisted to
      users.json (live-demo pin)
  B6. gate ON + VALID token                  -> real user resolved + persisted
      (firebase_uid set, is_anonymous False)
  B7. gate ON + forged token through the REAL server gate _handle_auth_token
      with file persistence -> 4401 close, users.json never created

Run:
  cd /home/nate/Documents/GRACE-2/services/agent && .venv/bin/python -m pytest \
      ../../reports/inflight/job-0252b-agent-20260611/verify/test_gate_ordering_probe_panel.py -q
"""

from __future__ import annotations

import json
from pathlib import Path

import pytest

from grace2_agent.auth import AUTH_REQUIRED_ENV
from grace2_agent.auth_handshake import authenticate_token, set_verify_hook
from grace2_agent.persistence import (
    DEFAULT_DATABASE,
    USERS_COLLECTION,
    FileMCPClient,
    Persistence,
)
from grace2_contracts.auth import AuthTokenEnvelope
from grace2_contracts.common import new_ulid, now_utc
from grace2_contracts.user import User


class CountingFileMCPClient(FileMCPClient):
    """Real FileMCPClient with a call recorder — every MCP verb is logged."""

    def __init__(self, base_dir: Path) -> None:
        super().__init__(base_dir=base_dir)
        self.calls: list[tuple[str, dict]] = []

    async def call_tool(self, name, arguments=None):
        self.calls.append((name, dict(arguments or {})))
        return await super().call_tool(name, arguments)


def _users_json(base: Path) -> Path:
    return base / DEFAULT_DATABASE / f"{USERS_COLLECTION}.json"


@pytest.fixture(autouse=True)
def _restore_hook():
    yield
    set_verify_hook(None)


@pytest.fixture()
def file_p(tmp_path):
    client = CountingFileMCPClient(base_dir=tmp_path)
    return tmp_path, client, Persistence(client)


# --------------------------------------------------------------------------- #
# B1. gate ON + empty token, no hint -> zero MCP traffic, no users.json file.
# --------------------------------------------------------------------------- #


@pytest.mark.asyncio
async def test_b1_gate_on_empty_token_zero_mcp_no_file(file_p, monkeypatch):
    base, client, p = file_p
    monkeypatch.setenv(AUTH_REQUIRED_ENV, "true")

    res = await authenticate_token(AuthTokenEnvelope(token=""), p)

    assert res.is_anonymous is True
    assert res.firebase_uid is None
    assert client.calls == [], f"MCP traffic on rejected path: {client.calls}"
    assert not _users_json(base).exists(), "users.json was created on rejected path"


@pytest.mark.asyncio
async def test_b1b_gate_on_no_envelope_zero_mcp(file_p, monkeypatch):
    base, client, p = file_p
    monkeypatch.setenv(AUTH_REQUIRED_ENV, "true")

    res = await authenticate_token(None, p)

    assert res.is_anonymous is True
    assert client.calls == []
    assert not _users_json(base).exists()


# --------------------------------------------------------------------------- #
# B2. gate ON + empty token + REAL reusable anon hint -> not even the sticky
#     read; users.json byte-identical after.
# --------------------------------------------------------------------------- #


@pytest.mark.asyncio
async def test_b2_gate_on_anon_hint_no_read_no_write(file_p, monkeypatch):
    base, client, p = file_p
    seeded = User(
        user_id=new_ulid(),
        firebase_uid=None,
        email=None,
        display_name=None,
        created_at=now_utc(),
        is_active=True,
        prefs={},
        is_anonymous=True,
    )
    await p.upsert_user(seeded)
    bytes_before = _users_json(base).read_bytes()
    calls_after_seed = len(client.calls)

    monkeypatch.setenv(AUTH_REQUIRED_ENV, "true")
    res = await authenticate_token(
        AuthTokenEnvelope(token="", anonymous_user_id=seeded.user_id), p
    )

    assert res.is_anonymous is True
    # The sticky hint was NOT rebound (fresh in-memory ULID instead).
    assert res.user.user_id != seeded.user_id
    assert client.calls[calls_after_seed:] == [], (
        f"MCP traffic on rejected path: {client.calls[calls_after_seed:]}"
    )
    assert _users_json(base).read_bytes() == bytes_before, "users.json mutated"


# --------------------------------------------------------------------------- #
# B3 / B4. gate ON + forged token / uid-less claims -> zero MCP, no file.
# --------------------------------------------------------------------------- #


@pytest.mark.asyncio
async def test_b3_gate_on_forged_token_zero_mcp_no_file(file_p, monkeypatch):
    base, client, p = file_p
    monkeypatch.setenv(AUTH_REQUIRED_ENV, "true")
    set_verify_hook(lambda token: None)  # forged/expired

    res = await authenticate_token(AuthTokenEnvelope(token="forged.jwt"), p)

    assert res.is_anonymous is True
    assert client.calls == []
    assert not _users_json(base).exists()


@pytest.mark.asyncio
async def test_b4_gate_on_claims_missing_uid_zero_mcp_no_file(file_p, monkeypatch):
    base, client, p = file_p
    monkeypatch.setenv(AUTH_REQUIRED_ENV, "true")
    set_verify_hook(lambda token: {"email": "x@example.com"})  # no uid

    res = await authenticate_token(AuthTokenEnvelope(token="decoded.but.no.uid"), p)

    assert res.is_anonymous is True
    assert client.calls == []
    assert not _users_json(base).exists()


# --------------------------------------------------------------------------- #
# B5. gate OFF (env unset) + forged token -> anonymous row persisted to disk.
#     The live-demo pin: dev agent behavior unchanged.
# --------------------------------------------------------------------------- #


@pytest.mark.asyncio
async def test_b5_gate_off_forged_token_persists_anonymous_row(file_p, monkeypatch):
    base, client, p = file_p
    monkeypatch.delenv(AUTH_REQUIRED_ENV, raising=False)
    set_verify_hook(lambda token: None)  # forged

    res = await authenticate_token(AuthTokenEnvelope(token="forged.jwt"), p)

    assert res.is_anonymous is True
    writes = [n for (n, a) in client.calls if not n.startswith("find")]
    assert writes, "gate-OFF forged token must still persist the anonymous user"
    store = json.loads(_users_json(base).read_text())
    assert res.user.user_id in store
    assert store[res.user.user_id]["is_anonymous"] is True
    assert store[res.user.user_id].get("firebase_uid") is None


@pytest.mark.asyncio
async def test_b5b_gate_off_sticky_hint_still_reuses(file_p, monkeypatch):
    """Gate OFF: sticky-anon reuse path unchanged (reads + rebinds)."""
    base, client, p = file_p
    monkeypatch.delenv(AUTH_REQUIRED_ENV, raising=False)
    seeded = User(
        user_id=new_ulid(),
        firebase_uid=None,
        email=None,
        display_name=None,
        created_at=now_utc(),
        is_active=True,
        prefs={},
        is_anonymous=True,
    )
    await p.upsert_user(seeded)

    res = await authenticate_token(
        AuthTokenEnvelope(token="", anonymous_user_id=seeded.user_id), p
    )

    assert res.is_anonymous is True
    assert res.user.user_id == seeded.user_id, "sticky reuse regressed gate-OFF"


# --------------------------------------------------------------------------- #
# B6. gate ON + VALID token -> real user resolved/persisted to users.json.
# --------------------------------------------------------------------------- #


@pytest.mark.asyncio
async def test_b6_gate_on_valid_token_resolves_and_persists(file_p, monkeypatch):
    base, client, p = file_p
    monkeypatch.setenv(AUTH_REQUIRED_ENV, "true")
    set_verify_hook(lambda token: {"uid": "fb-panel-uid", "email": "p@example.com"})

    res = await authenticate_token(AuthTokenEnvelope(token="eyJ.valid.jwt"), p)

    assert res.is_anonymous is False
    assert res.firebase_uid == "fb-panel-uid"
    store = json.loads(_users_json(base).read_text())
    assert res.user.user_id in store
    assert store[res.user.user_id]["firebase_uid"] == "fb-panel-uid"


# --------------------------------------------------------------------------- #
# B7. The REAL server gate with file persistence: forged token -> 4401 close,
#     users.json never created on disk.
# --------------------------------------------------------------------------- #


class _WS:
    def __init__(self) -> None:
        self.sent: list[dict] = []
        self.closed_with: tuple[int, str] | None = None

    async def send(self, raw: str) -> None:
        self.sent.append(json.loads(raw))

    async def close(self, code: int = 1000, reason: str = "") -> None:
        self.closed_with = (code, reason)


@pytest.mark.asyncio
async def test_b7_real_server_gate_forged_token_file_persistence(
    file_p, monkeypatch
):
    from grace2_agent.server import SessionState, _handle_auth_token, set_persistence

    base, client, p = file_p
    set_persistence(p)
    try:
        monkeypatch.setenv(AUTH_REQUIRED_ENV, "true")
        set_verify_hook(lambda token: None)

        state = SessionState(session_id=new_ulid())
        ws = _WS()
        ok = await _handle_auth_token(
            ws, state, {"token": "forged.jwt.value", "anonymous": False}
        )

        assert ok is False
        assert state.authenticated_user_id is None
        assert ws.closed_with is not None and ws.closed_with[0] == 4401
        assert any(
            e["type"] == "error" and e["payload"]["error_code"] == "AUTH_FAILED"
            for e in ws.sent
        )
        assert client.calls == [], f"MCP traffic on rejected path: {client.calls}"
        assert not _users_json(base).exists(), "users.json created on rejected path"
    finally:
        set_persistence(None)
