"""Shared helpers for protocol-conformance tests.

Every helper here drives the real WebSocket transport against the live agent
subprocess. The only mock in the stack is the Gemini adapter (see
``tests/_agent_runner.py``). Envelope construction goes through
``grace2_contracts.ws.Envelope`` so we test the same shapes the agent and web
client consume.
"""

from __future__ import annotations

import asyncio
import json
from typing import Any

import websockets

from grace2_contracts import new_ulid
from grace2_contracts.ws import (
    CancelPayload,
    Envelope,
    SessionResumePayload,
    UserMessagePayload,
)


def serialize(message_type: str, session_id: str, payload) -> str:
    env = Envelope(type=message_type, session_id=session_id, payload=payload)
    return env.model_dump_json()


async def open_session(url: str, session_id: str | None = None) -> tuple[
    websockets.ClientConnection, str
]:
    """Connect, send ``session-resume``, wait for ``session-state``."""
    sid = session_id or new_ulid()
    ws = await websockets.connect(url)
    await ws.send(serialize("session-resume", sid, SessionResumePayload()))
    # Drain until session-state
    frame = await asyncio.wait_for(ws.recv(), timeout=5.0)
    parsed = json.loads(frame)
    assert parsed["type"] == "session-state", f"expected session-state, got {parsed}"
    return ws, sid


async def send_user_message(
    ws, sid: str, text: str, research_mode: str = "research"
) -> None:
    await ws.send(
        serialize(
            "user-message",
            sid,
            UserMessagePayload(text=text, research_mode=research_mode),
        )
    )


async def send_cancel(ws, sid: str, reason: str = "user-requested") -> None:
    await ws.send(serialize("cancel", sid, CancelPayload(reason=reason)))


async def collect_until_done(ws, timeout: float = 15.0) -> list[dict[str, Any]]:
    """Read frames until a terminal ``done=True`` chunk arrives, or timeout."""
    frames: list[dict[str, Any]] = []
    deadline_loop = asyncio.get_event_loop().time() + timeout
    while True:
        remaining = deadline_loop - asyncio.get_event_loop().time()
        if remaining <= 0:
            raise TimeoutError("collect_until_done timed out")
        raw = await asyncio.wait_for(ws.recv(), timeout=remaining)
        parsed = json.loads(raw)
        frames.append(parsed)
        if parsed["type"] == "agent-message-chunk" and parsed["payload"].get("done"):
            # Drain any trailing pipeline-state(complete) before returning.
            try:
                trailing = await asyncio.wait_for(ws.recv(), timeout=0.5)
                frames.append(json.loads(trailing))
            except asyncio.TimeoutError:
                pass
            return frames


async def collect_until_cancelled(ws, timeout: float = 30.0) -> list[dict[str, Any]]:
    """Read frames until a ``pipeline-state`` with cancelled step arrives."""
    frames: list[dict[str, Any]] = []
    deadline_loop = asyncio.get_event_loop().time() + timeout
    while True:
        remaining = deadline_loop - asyncio.get_event_loop().time()
        if remaining <= 0:
            raise TimeoutError("collect_until_cancelled timed out")
        raw = await asyncio.wait_for(ws.recv(), timeout=remaining)
        parsed = json.loads(raw)
        frames.append(parsed)
        if parsed["type"] == "pipeline-state":
            states = [s.get("state") for s in parsed["payload"].get("steps", [])]
            if "cancelled" in states:
                return frames
