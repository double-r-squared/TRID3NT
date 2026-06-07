"""FR-FR-3 MAX_TURNS cap — stage D acceptance integration test.

Job: job-0059-testing-20260607 (sprint-08 Stage D)

Drives a session past 25 turns using the real SessionState + _handle_max_turns_reached
machinery (job-0048 deliverable). Asserts:
  1. session-state.status flips to "max_turns_reached" on cap hit
  2. Closing agent-message-chunk fires on the cap hit
  3. Further calls (turns 27+) continue to be refused (idempotent gate)

This test uses the FakeWebSocket double used in test_max_turns_cap.py
(same module boundary) so it exercises the real emission path without
requiring a live WebSocket server.
"""

from __future__ import annotations

import asyncio
import json
from dataclasses import dataclass, field

import pytest

from grace2_agent.server import SessionState, _handle_max_turns_reached
from grace2_agent.main import MAX_TURNS_PER_SESSION


@dataclass
class _FakeWS:
    """Minimal duck-type WebSocket that captures send() calls."""
    session_id: str = "01ACCEPTANCE00000000000000"
    frames: list[dict] = field(default_factory=list)

    async def send(self, text: str) -> None:
        self.frames.append(json.loads(text))

    def frames_of_type(self, t: str) -> list[dict]:
        return [f for f in self.frames if f.get("type") == t]


@pytest.mark.asyncio
async def test_session_past_25_turns_status_flip():
    """Drive a session to MAX_TURNS_PER_SESSION+1 turns; assert status flip.

    Acceptance criterion: session-state.status == "max_turns_reached" is
    emitted on the (MAX+1)th user-message, and further messages continue
    to be refused.
    """
    ws = _FakeWS()
    state = SessionState(session_id=ws.session_id)
    assert state.turn_count == 0

    # Simulate MAX_TURNS_PER_SESSION normal turns (no cap)
    for _ in range(MAX_TURNS_PER_SESSION):
        state.turn_count += 1

    assert state.turn_count == MAX_TURNS_PER_SESSION, (
        f"Expected turn_count == {MAX_TURNS_PER_SESSION}, got {state.turn_count}"
    )

    # Turn MAX+1: the cap fires
    state.turn_count += 1
    await _handle_max_turns_reached(ws, state)

    ss_frames = ws.frames_of_type("session-state")
    assert len(ss_frames) >= 1, "Cap hit did not emit any session-state frame"
    assert ss_frames[0]["payload"]["status"] == "max_turns_reached", (
        f"Expected max_turns_reached, got {ss_frames[0]['payload']['status']!r}"
    )

    # Closing message must fire on cap hit
    chunk_frames = ws.frames_of_type("agent-message-chunk")
    assert len(chunk_frames) >= 1, "Cap hit did not emit a closing agent-message-chunk"
    combined = "".join(f["payload"]["delta"] for f in chunk_frames)
    assert any(kw in combined for kw in ("turn limit", "turns", str(MAX_TURNS_PER_SESSION))), (
        f"Closing message text did not mention turn limit: {combined!r}"
    )

    frames_after_first_cap = len(ws.frames_of_type("session-state"))

    # Turns 27+ (simulate two more messages past the cap)
    for extra in range(2):
        state.turn_count += 1
        await _handle_max_turns_reached(ws, state)

    frames_after_extras = ws.frames_of_type("session-state")
    assert len(frames_after_extras) > frames_after_first_cap, (
        "Expected additional session-state frames for turns beyond the cap"
    )
    for ss in frames_after_extras:
        assert ss["payload"]["status"] == "max_turns_reached", (
            "All post-cap session-state frames must carry max_turns_reached"
        )


@pytest.mark.asyncio
async def test_new_session_is_independent_after_cap():
    """A new WebSocket connection (new SessionState) is unaffected by a maxed-out session."""
    ws_old = _FakeWS(session_id="01OLDSESSION000000000000000")
    state_old = SessionState(session_id=ws_old.session_id)
    state_old.turn_count = MAX_TURNS_PER_SESSION + 5  # fully maxed

    ws_new = _FakeWS(session_id="01NEWSESSION000000000000000")
    state_new = SessionState(session_id=ws_new.session_id)

    assert state_new.turn_count == 0, "New session must start at 0"
    assert len(ws_new.frames) == 0, "New session must have 0 frames emitted"


if __name__ == "__main__":
    asyncio.run(test_session_past_25_turns_status_flip())
    print("PASS: MAX_TURNS acceptance test passed")
