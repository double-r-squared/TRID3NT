"""research_mode A1 seam (FR-WC-15 / orchestrator-pinned toggle carrier).

In v0.1 there is only one pipeline strategy; the carrier is pinned now so no
second path gets invented. These tests assert the field validates, is
forward-compatible, and round-trips through the live agent without disrupting
the user-message -> agent-message-chunk -> done shape.
"""

from __future__ import annotations

import json

import pytest

from grace2_contracts.ws import UserMessagePayload

from ._ws_helpers import collect_until_done, open_session, send_user_message


def test_research_mode_default_is_research() -> None:
    """Default value matches the orchestrator-pinned seam (Decision G)."""
    p = UserMessagePayload(text="hi")
    assert p.research_mode == "research"


def test_research_mode_accepts_deep_research() -> None:
    """The Literal accepts ``deep_research`` (FR-WC-15 / FR-HEP-4)."""
    p = UserMessagePayload(text="hi", research_mode="deep_research")
    assert p.research_mode == "deep_research"


def test_research_mode_rejects_unknown_value() -> None:
    """Closed Literal — typos must be caught at the wire boundary."""
    from pydantic import ValidationError

    with pytest.raises(ValidationError):
        UserMessagePayload(text="hi", research_mode="bogus")


async def test_research_mode_research_value_round_trips_through_agent(
    agent_subprocess: str,
) -> None:
    """The agent accepts research_mode=research, yields chunks, hits done."""
    ws, sid = await open_session(agent_subprocess)
    try:
        await send_user_message(ws, sid, "hi", research_mode="research")
        frames = await collect_until_done(ws, timeout=10.0)
        chunks = [
            f for f in frames if f["type"] == "agent-message-chunk"
        ]
        assert any(f["payload"].get("done") for f in chunks)
    finally:
        await ws.close()


async def test_research_mode_deep_research_value_round_trips_through_agent(
    agent_subprocess: str,
) -> None:
    """deep_research is accepted by the agent (v0.1 falls back to research mode,
    but the carrier must travel through unmodified — Decision G seam)."""
    ws, sid = await open_session(agent_subprocess)
    try:
        await send_user_message(ws, sid, "hi", research_mode="deep_research")
        frames = await collect_until_done(ws, timeout=10.0)
        assert any(
            f["type"] == "agent-message-chunk" and f["payload"].get("done")
            for f in frames
        )
    finally:
        await ws.close()
