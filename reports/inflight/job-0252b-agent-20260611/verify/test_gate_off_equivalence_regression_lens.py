"""REGRESSION-lens panel probe (sprint-13.5 Stage 1 re-panel, job-0252b).

Proves the gate-OFF byte-identity claim BEHAVIORALLY: with AUTH_REQUIRED
absent from the environment, the post-0252b ``authenticate_token`` (commit
80f326c, current tree) and the pre-0252b version (80f326c^, dumped verbatim
to ``_auth_handshake_pre_0252b.py``) produce IDENTICAL persistence traffic
(tool-call sequence, collections touched, persisted-row shapes) and identical
AuthResult fields on every gate-relevant path:

  A. empty token, no hint        -> anonymous provision + persist
  B. forged token (verify fails) -> anonymous provision + persist
  C. claims missing uid          -> anonymous provision + persist
  D. empty token + sticky hint   -> reuse the existing anonymous user

ULIDs are nondeterministic per call, so traces are normalized (every ULID
replaced by a positional placeholder) before comparison.
"""

from __future__ import annotations

import importlib.util
import os
import sys
from pathlib import Path

import pytest

from grace2_agent.persistence import Persistence
from grace2_contracts.auth import AuthTokenEnvelope

HERE = Path(__file__).resolve().parent
OLD_PATH = HERE / "_auth_handshake_pre_0252b.py"


def _load_old_module():
    """Import the pre-0252b module under the grace2_agent package context so
    its relative imports (.auth/.persistence/...) resolve against the real
    installed package."""
    spec = importlib.util.spec_from_file_location(
        "grace2_agent._auth_handshake_pre_0252b_probe", OLD_PATH
    )
    mod = importlib.util.module_from_spec(spec)
    mod.__package__ = "grace2_agent"
    sys.modules[spec.name] = mod
    spec.loader.exec_module(mod)
    return mod


class RecordingMCPClient:
    """In-memory MCP mock (mirror of tests/test_auth_handshake.py)."""

    def __init__(self) -> None:
        self._store: dict[str, dict[str, dict]] = {}
        self.calls: list[tuple[str, str]] = []  # (tool, collection)

    async def call_tool(self, name, arguments=None):
        args = dict(arguments or {})
        self.calls.append((name, args.get("collection") or "_default"))
        store = self._store.setdefault(args.get("collection") or "_default", {})
        if name == "insert-one":
            doc = args["document"]
            store[doc["_id"]] = doc
            return {"insertedId": doc["_id"]}
        if name == "update-one":
            filt = args.get("filter", {})
            set_ = args.get("update", {}).get("$set", {})
            tid = filt.get("_id")
            if tid and tid in store:
                store[tid].update(set_)
            elif args.get("upsert") and tid:
                store[tid] = {**set_, "_id": tid}
            return {"matchedCount": 1, "modifiedCount": 1}
        if name == "find-one":
            filt = args.get("filter", {})
            for doc in store.values():
                if all(doc.get(k) == v for k, v in filt.items()):
                    return {"document": doc}
            return {"document": None}
        if name == "find":
            filt = args.get("filter", {})
            return {
                "documents": [
                    d
                    for d in store.values()
                    if all(d.get(k) == v for k, v in filt.items())
                ]
            }
        raise RuntimeError(f"unhandled tool {name}")


def _normalize_doc(doc: dict, ulid_map: dict) -> tuple:
    out = []
    for k in sorted(doc):
        v = doc[k]
        if isinstance(v, str) and len(v) == 26 and v.isalnum():
            v = ulid_map.setdefault(v, f"<ULID-{len(ulid_map)}>")
        elif k in ("created_at", "updated_at", "last_seen_at"):
            v = "<ts>"
        out.append((k, v))
    return tuple(out)


async def _drive(module) -> list:
    """Run scenarios A-D against the given auth_handshake module; return a
    normalized trace of persistence traffic + result fields."""
    assert "AUTH_REQUIRED" not in os.environ, "probe requires gate-OFF env"
    trace = []
    module.set_verify_hook(lambda tok: None)  # default: verification fails

    # A: empty token, no hint
    mcp = RecordingMCPClient()
    res = await module.authenticate_token(
        AuthTokenEnvelope(token=""), Persistence(mcp)
    )
    ulids: dict = {}
    trace.append(
        (
            "A",
            tuple(mcp.calls),
            res.is_anonymous,
            res.firebase_uid,
            res.tier,
            sorted(
                _normalize_doc(d, ulids)
                for coll in mcp._store.values()
                for d in coll.values()
            ),
        )
    )

    # B: forged token -> verify hook returns None
    mcp = RecordingMCPClient()
    res = await module.authenticate_token(
        AuthTokenEnvelope(token="forged.jwt.token"), Persistence(mcp)
    )
    ulids = {}
    trace.append(
        (
            "B",
            tuple(mcp.calls),
            res.is_anonymous,
            res.firebase_uid,
            res.tier,
            sorted(
                _normalize_doc(d, ulids)
                for coll in mcp._store.values()
                for d in coll.values()
            ),
        )
    )

    # C: claims decode but uid missing
    module.set_verify_hook(lambda tok: {"email": "x@y.z"})
    mcp = RecordingMCPClient()
    res = await module.authenticate_token(
        AuthTokenEnvelope(token="uidless.jwt.token"), Persistence(mcp)
    )
    ulids = {}
    trace.append(
        (
            "C",
            tuple(mcp.calls),
            res.is_anonymous,
            res.firebase_uid,
            res.tier,
            sorted(
                _normalize_doc(d, ulids)
                for coll in mcp._store.values()
                for d in coll.values()
            ),
        )
    )

    # D: sticky reuse — provision an anonymous user via the module's own
    # path, then reconnect with the hint; must re-bind the SAME user.
    module.set_verify_hook(lambda tok: None)
    mcp = RecordingMCPClient()
    p = Persistence(mcp)
    first = await module.authenticate_token(AuthTokenEnvelope(token=""), p)
    seed_calls = len(mcp.calls)
    second = await module.authenticate_token(
        AuthTokenEnvelope(token="", anonymous_user_id=first.user.user_id), p
    )
    trace.append(
        (
            "D",
            tuple(mcp.calls[seed_calls:]),  # traffic of the reuse turn only
            second.is_anonymous,
            second.firebase_uid,
            second.user.user_id == first.user.user_id,  # sticky re-bind
        )
    )
    module.set_verify_hook(None)  # restore default hook
    return trace


@pytest.mark.asyncio
async def test_gate_off_old_vs_new_behaviorally_identical():
    import grace2_agent.auth_handshake as new_mod

    old_mod = _load_old_module()
    old_trace = await _drive(old_mod)
    new_trace = await _drive(new_mod)
    assert old_trace == new_trace, (
        f"gate-OFF divergence:\nOLD={old_trace}\nNEW={new_trace}"
    )
    # And the gate-OFF path really does persist + reuse (not vacuous):
    for label, calls, is_anon, fb_uid, *rest in new_trace[:3]:
        assert is_anon is True and fb_uid is None
        assert any(t in ("insert-one", "update-one") for t, _ in calls), (
            f"path {label}: expected a users write when gate is OFF"
        )
    d = new_trace[3]
    assert d[2] is True and d[4] is True, "sticky reuse must re-bind same user"
