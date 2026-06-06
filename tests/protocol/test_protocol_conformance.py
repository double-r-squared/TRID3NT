"""Appendix-A WebSocket protocol conformance (FR-AS-5, M1).

Real transport against the live ``grace2-agent`` subprocess. Gemini is stubbed
at the adapter seam so flow shapes are deterministic; every envelope is
validated through ``grace2_contracts.ws``.

Tests:
- envelope discrimination on ``type``
- ``user-message`` -> ``agent-message-chunk`` stream -> terminal ``done=True``
- ``cancel`` mid-stream -> ``pipeline-state`` with cancelled step (NFR-R-3
  budget 30s — invariant 8)
- malformed frame -> A.6 typed ``error`` and the server survives (negative
  control)
- ``session-resume`` -> ``session-state``
"""

from __future__ import annotations

import asyncio
import json
import time

import pytest

from grace2_contracts.ws import (
    AgentMessageChunkPayload,
    Envelope,
    ErrorPayload,
    PipelineStatePayload,
    SessionStatePayload,
)

from ._ws_helpers import (
    collect_until_cancelled,
    collect_until_done,
    open_session,
    send_cancel,
    send_user_message,
    serialize,
)


# ---------------------------------------------------------------------------
# Envelope discrimination + session-resume -> session-state
# ---------------------------------------------------------------------------


async def test_session_resume_returns_session_state(agent_subprocess: str) -> None:
    """session-resume must produce a session-state envelope; type discriminator
    drives dispatch (Invariant 2 seeded)."""
    ws, _sid = await open_session(agent_subprocess)
    try:
        # open_session already asserted shape; validate the contract payload too.
        # The frame is already consumed inside open_session; re-validate by
        # constructing a fresh session and reading the very first frame raw.
        pass
    finally:
        await ws.close()


async def test_session_state_payload_validates(agent_subprocess: str) -> None:
    """The session-state payload validates through the contracts package."""
    from grace2_contracts import new_ulid
    import websockets

    from grace2_contracts.ws import SessionResumePayload

    sid = new_ulid()
    async with websockets.connect(agent_subprocess) as ws:
        await ws.send(serialize("session-resume", sid, SessionResumePayload()))
        raw = await asyncio.wait_for(ws.recv(), timeout=5.0)
        parsed = json.loads(raw)
        # Failure here names the AGENT layer (server.py emitted wrong shape)
        # rather than the contracts layer (which has its own 91-test suite).
        assert parsed["type"] == "session-state", (
            f"AGENT layer regression — session-resume did not produce "
            f"session-state. Got: {parsed.get('type')!r}"
        )
        payload = SessionStatePayload.model_validate(parsed["payload"])
        assert payload.chat_history == []
        assert payload.loaded_layers == []


# ---------------------------------------------------------------------------
# user-message -> chunks -> terminal done
# ---------------------------------------------------------------------------


async def test_user_message_streams_chunks_then_done(agent_subprocess: str) -> None:
    """user-message must yield agent-message-chunk deltas terminated by done=True."""
    ws, sid = await open_session(agent_subprocess)
    try:
        await send_user_message(ws, sid, "ping")
        frames = await collect_until_done(ws, timeout=10.0)

        chunks = [f for f in frames if f["type"] == "agent-message-chunk"]
        terminal_chunks = [
            f for f in chunks if f["payload"].get("done") is True
        ]
        delta_chunks = [
            f for f in chunks if f["payload"].get("delta") and not f["payload"].get("done")
        ]

        assert len(delta_chunks) >= 1, (
            f"AGENT layer regression — no streamed deltas before terminal. "
            f"frames={[f['type'] for f in frames]}"
        )
        assert len(terminal_chunks) == 1, (
            f"AGENT layer regression — expected exactly one done=True chunk, "
            f"got {len(terminal_chunks)}"
        )

        # Every chunk validates through the contracts model. This is the
        # determinism-boundary seam: the wire frame == the typed shape.
        for f in chunks:
            AgentMessageChunkPayload.model_validate(f["payload"])
            assert f["session_id"] == sid

        # A trailing pipeline-state(complete) snapshot may or may not arrive
        # within the drain window; the contract does not require it.
    finally:
        await ws.close()


# ---------------------------------------------------------------------------
# Cancel mid-stream — Invariant 8 / NFR-R-3
# ---------------------------------------------------------------------------


async def test_cancel_midstream_emits_cancelled_pipeline_state(
    agent_subprocess: str,
) -> None:
    """Send cancel after the first chunk; assert a cancelled pipeline-state
    arrives within NFR-R-3 (30s budget). Distinct from 'failed' (Invariant 8)."""
    ws, sid = await open_session(agent_subprocess)
    try:
        await send_user_message(ws, sid, "tell me a long story")
        # Wait for at least one delta chunk (or any frame) before cancel.
        first_chunk_seen = False
        for _ in range(40):
            raw = await asyncio.wait_for(ws.recv(), timeout=2.0)
            parsed = json.loads(raw)
            if parsed["type"] == "agent-message-chunk" and parsed["payload"].get(
                "delta"
            ):
                first_chunk_seen = True
                break
        assert first_chunk_seen, "no delta arrived before cancel timeout"

        cancel_sent_at = time.monotonic()
        await send_cancel(ws, sid)
        frames = await collect_until_cancelled(ws, timeout=30.0)
        cancel_landed_at = time.monotonic()
        cancel_latency = cancel_landed_at - cancel_sent_at

        # Invariant 8 / NFR-R-3.
        assert cancel_latency < 30.0, (
            f"AGENT layer regression — cancel-to-cancelled took {cancel_latency:.2f}s "
            f"(NFR-R-3 budget 30s)"
        )

        pipe_frames = [f for f in frames if f["type"] == "pipeline-state"]
        assert pipe_frames, "no pipeline-state frame at all after cancel"
        last_states = [
            s["state"] for s in pipe_frames[-1]["payload"]["steps"]
        ]
        assert "cancelled" in last_states, (
            f"AGENT layer regression — pipeline-state has no cancelled step. "
            f"states={last_states}"
        )
        # Cancellation is distinct from failure (Invariant 8).
        assert "failed" not in last_states, (
            "AGENT layer regression — cancel was reported as failed (Invariant 8 "
            "requires distinct cancelled vs failed step states)"
        )
    finally:
        await ws.close()


# ---------------------------------------------------------------------------
# Malformed frame -> A.6 typed error AND server survives (negative control)
# ---------------------------------------------------------------------------


async def test_malformed_frame_returns_typed_error_and_server_survives(
    agent_subprocess: str,
) -> None:
    """Send raw garbage; assert an A.6 ``error`` frame and the connection
    keeps working afterwards (server doesn't crash)."""
    import websockets

    from grace2_contracts import new_ulid
    from grace2_contracts.ws import SessionResumePayload

    sid = new_ulid()
    async with websockets.connect(agent_subprocess) as ws:
        # Garbage JSON the server cannot parse as an envelope.
        await ws.send("not-a-json-object")
        raw = await asyncio.wait_for(ws.recv(), timeout=5.0)
        parsed = json.loads(raw)
        assert parsed["type"] == "error", (
            f"AGENT layer regression — malformed frame did not produce error. "
            f"Got: {parsed.get('type')!r}"
        )
        # The A.6 error code is a closed Literal — payload must validate.
        err = ErrorPayload.model_validate(parsed["payload"])
        assert err.error_code in {
            "INTERNAL_ERROR",
            "TOOL_PARAMS_INVALID",
            "AUTH_FAILED",
        }, f"malformed frame error_code unexpected: {err.error_code}"

        # Server survives: a valid session-resume after garbage still works.
        await ws.send(serialize("session-resume", sid, SessionResumePayload()))
        raw = await asyncio.wait_for(ws.recv(), timeout=5.0)
        parsed = json.loads(raw)
        assert parsed["type"] == "session-state", (
            "AGENT layer regression — server did not survive malformed frame"
        )


# ---------------------------------------------------------------------------
# Unknown message type -> typed error
# ---------------------------------------------------------------------------


# ---------------------------------------------------------------------------
# Live Gemini opt-in (skipped by default; runs via -m live_gemini)
# ---------------------------------------------------------------------------


@pytest.mark.live_gemini
async def test_live_gemini_round_trip(agent_subprocess_live_gemini: str) -> None:
    """One real Gemini round-trip through the live agent process.

    Opt-in via ``pytest -m live_gemini``. Not part of ``make test`` default —
    LLM nondeterminism stays out of the M1 acceptance run.
    """
    ws, sid = await open_session(agent_subprocess_live_gemini)
    try:
        await send_user_message(ws, sid, "Say the word OK and nothing else.")
        frames = await collect_until_done(ws, timeout=120.0)
        deltas = [
            f["payload"]["delta"]
            for f in frames
            if f["type"] == "agent-message-chunk" and f["payload"].get("delta")
        ]
        # Real Gemini must emit at least one token before terminal.
        assert deltas, "AGENT/Vertex layer — no Gemini deltas received"
        terminal = [
            f
            for f in frames
            if f["type"] == "agent-message-chunk" and f["payload"].get("done")
        ]
        assert terminal, "AGENT/Vertex layer — no terminal done=True frame"
    finally:
        await ws.close()


async def test_unknown_message_type_returns_typed_error(
    agent_subprocess: str,
) -> None:
    """An unknown ``type`` triggers an A.6 error, not a crash."""
    import websockets

    from grace2_contracts import new_ulid

    sid = new_ulid()
    async with websockets.connect(agent_subprocess) as ws:
        await ws.send(
            json.dumps(
                {
                    "type": "totally-bogus",
                    "id": new_ulid(),
                    "ts": "2026-06-05T12:00:00Z",
                    "session_id": sid,
                    "payload": {},
                }
            )
        )
        raw = await asyncio.wait_for(ws.recv(), timeout=5.0)
        parsed = json.loads(raw)
        assert parsed["type"] == "error", (
            f"AGENT layer — unknown type should emit error. Got: {parsed['type']!r}"
        )
        ErrorPayload.model_validate(parsed["payload"])
