"""job-0267 — FULL-STREAM persistence: narration + tool cards replay on reopen.

User-verified bug: reopening a Case replayed ONLY the user's own messages.
Two root causes:

1. ``_dispatch_gemini_and_persist`` persisted the agent turn with
   ``content=""`` — the streamed deltas were never accumulated, so the web
   replay (rightly) rendered nothing for agent turns.
2. Tool dispatches persisted NO replayable record at all — the inline tool
   cards (``feedback_chat_tool_interleave``) were wire-only ``pipeline-state``
   envelopes, lost the moment the socket closed.

This suite drives the REAL server seams (no Gemini, no Playwright) against
both the file-backed dev substrate and the MockMCPClient:

- agent narration accumulates across stream iterations and persists as a
  ``role="agent"`` ``CaseChatMessage`` with the real text;
- every terminal tool dispatch persists a ``role="tool"`` row carrying a
  typed ``ToolCardRecord`` (state, started_at, duration_ms from the
  authoritative job-0264 emitter stamp, label);
- failed dispatches persist ``state="failed"``; cancelled dispatches persist
  nothing (Invariant 8);
- ``get_session_state`` returns the FULL stream ordered by ``created_at``
  (user -> tool -> agent), ULID tiebreak, regardless of backend sort;
- ``list_cases_for_user`` excludes ``deleted`` AND ``archived`` Cases
  SERVER-side (the user saw a deleted ghost in the left rail);
- the user-turn persist path is byte-shape unchanged;
- Gemini-free E2E: one full simulated turn (user msg -> tool dispatch ->
  narration) through ``_prepare_user_turn`` + ``_invoke_tool_via_emitter`` +
  ``_dispatch_gemini_and_persist`` against file persistence, then the
  rehydration envelope replays the complete ordered stream.
"""

from __future__ import annotations

import asyncio
import json

import pytest

from grace2_agent import server
from grace2_agent import tools as agent_tools
from grace2_agent.persistence import make_file_persistence
from grace2_agent.tools import RegisteredTool
from grace2_contracts.case import CaseCommandEnvelopePayload, CaseSummary
from grace2_contracts.common import new_ulid, now_utc
from grace2_contracts.tool_registry import AtomicToolMetadata

FAKE_TOOL = "job0267_fake_tool"
FAILING_TOOL = "job0267_failing_tool"


class FakeWS:
    """Minimal ServerConnection stand-in: records every envelope sent."""

    def __init__(self) -> None:
        self.sent: list[str] = []

    async def send(self, text: str) -> None:
        self.sent.append(text)


@pytest.fixture()
def file_persistence(tmp_path):
    """Bind REAL file-backed persistence (tmpdir) as the server singleton."""
    p = make_file_persistence(base_dir=tmp_path)
    server.set_persistence(p)
    try:
        yield p
    finally:
        server.set_persistence(None)


@pytest.fixture(autouse=True)
def _clean_session_registry():
    """Keep the session-scoped Case registry hermetic per test."""
    server._SESSION_ACTIVE_CASE.clear()
    yield
    server._SESSION_ACTIVE_CASE.clear()


@pytest.fixture()
def fake_tool():
    """Register a trivial registry tool; deregister on teardown."""

    async def _fn() -> dict:
        return {"status": "ok", "rows": 3}

    meta = AtomicToolMetadata(
        name=FAKE_TOOL, ttl_class="live-no-cache", cacheable=False
    )
    agent_tools.TOOL_REGISTRY[FAKE_TOOL] = RegisteredTool(
        metadata=meta, fn=_fn, module=__name__
    )
    try:
        yield FAKE_TOOL
    finally:
        agent_tools.TOOL_REGISTRY.pop(FAKE_TOOL, None)


@pytest.fixture()
def failing_tool():
    """Register a registry tool that always raises; deregister on teardown."""

    async def _fn() -> dict:
        raise RuntimeError("upstream exploded")

    meta = AtomicToolMetadata(
        name=FAILING_TOOL, ttl_class="live-no-cache", cacheable=False
    )
    agent_tools.TOOL_REGISTRY[FAILING_TOOL] = RegisteredTool(
        metadata=meta, fn=_fn, module=__name__
    )
    try:
        yield FAILING_TOOL
    finally:
        agent_tools.TOOL_REGISTRY.pop(FAILING_TOOL, None)


async def _create_case(ws, state, title="Full Stream Case") -> str:
    cmd = CaseCommandEnvelopePayload(command="create", args={"title": title})
    await server._handle_case_command(ws, state, cmd)
    case_id = state.active_case_id
    assert case_id, "create must bind the active case"
    return case_id


# --------------------------------------------------------------------------- #
# 1. Agent narration persists with the REAL accumulated text
# --------------------------------------------------------------------------- #


@pytest.mark.asyncio
async def test_agent_narration_persists_and_replays(file_persistence) -> None:
    """The terminal agent row carries the accumulated stream text — the exact
    regression the user verified (only their own messages replayed)."""
    ws = FakeWS()
    state = server.SessionState(session_id=new_ulid())
    case_id = await _create_case(ws, state)
    await server._persist_chat_turn(state, role="user", content="hi agent")

    async def fake_stream(websocket, st, settings, user_text, research_mode):
        # Mirrors _stream_gemini_reply: reset, accumulate deltas across
        # iterations, terminal chat_history append on clean completion.
        st.current_turn_narration = []
        st.current_turn_narration.append("I fetched the DEM ")
        st.current_turn_narration.append("and added it to the map.")
        st.chat_history.append({"role": "user", "text": user_text})

    orig = server._stream_gemini_reply
    server._stream_gemini_reply = fake_stream
    try:
        await server._dispatch_gemini_and_persist(ws, state, None, "hi agent", "off")
    finally:
        server._stream_gemini_reply = orig

    session_state = await file_persistence.get_session_state(case_id)
    roles = [m.role for m in session_state.chat_history]
    assert roles == ["user", "agent"]
    agent_row = session_state.chat_history[1]
    assert agent_row.content == "I fetched the DEM and added it to the map."
    assert agent_row.tool_card is None


@pytest.mark.asyncio
async def test_agent_narration_persists_even_when_stream_dies(
    file_persistence,
) -> None:
    """Best-effort on error: whatever narration accumulated before the stream
    raised is still persisted (the finally-block path)."""
    ws = FakeWS()
    state = server.SessionState(session_id=new_ulid())
    case_id = await _create_case(ws, state)

    async def dying_stream(websocket, st, settings, user_text, research_mode):
        st.current_turn_narration = []
        st.current_turn_narration.append("Partial narration before the crash")
        raise RuntimeError("LLM_UNAVAILABLE")

    orig = server._stream_gemini_reply
    server._stream_gemini_reply = dying_stream
    try:
        with pytest.raises(RuntimeError):
            await server._dispatch_gemini_and_persist(ws, state, None, "x", "off")
    finally:
        server._stream_gemini_reply = orig

    session_state = await file_persistence.get_session_state(case_id)
    assert [m.role for m in session_state.chat_history] == ["agent"]
    assert (
        session_state.chat_history[0].content
        == "Partial narration before the crash"
    )


@pytest.mark.asyncio
async def test_no_agent_row_when_stream_dies_with_nothing_said(
    file_persistence,
) -> None:
    """No narration + no terminal completion = no phantom agent row."""
    ws = FakeWS()
    state = server.SessionState(session_id=new_ulid())
    case_id = await _create_case(ws, state)

    async def instant_death(websocket, st, settings, user_text, research_mode):
        st.current_turn_narration = []
        raise RuntimeError("died before the first token")

    orig = server._stream_gemini_reply
    server._stream_gemini_reply = instant_death
    try:
        with pytest.raises(RuntimeError):
            await server._dispatch_gemini_and_persist(ws, state, None, "x", "off")
    finally:
        server._stream_gemini_reply = orig

    session_state = await file_persistence.get_session_state(case_id)
    assert session_state.chat_history == []


# --------------------------------------------------------------------------- #
# 2. Tool-card rows persist with duration + label
# --------------------------------------------------------------------------- #


@pytest.mark.asyncio
async def test_tool_card_persists_with_duration(
    file_persistence, fake_tool
) -> None:
    ws = FakeWS()
    state = server.SessionState(session_id=new_ulid())
    case_id = await _create_case(ws, state)

    result = await server._invoke_tool_via_emitter(ws, state, FAKE_TOOL, {})
    assert result == {"status": "ok", "rows": 3}

    session_state = await file_persistence.get_session_state(case_id)
    tool_rows = [m for m in session_state.chat_history if m.role == "tool"]
    assert len(tool_rows) == 1
    row = tool_rows[0]
    card = row.tool_card
    assert card is not None
    assert card.tool_name == FAKE_TOOL
    assert card.state == "complete"
    assert card.label == FAKE_TOOL  # registry display name
    assert card.started_at is not None
    assert card.duration_ms is not None and card.duration_ms >= 0
    # content is the JSON twin of the typed record.
    assert json.loads(row.content)["tool_name"] == FAKE_TOOL
    # pipeline link + no duplicated layer attribution on tool rows.
    assert row.pipeline_id is not None
    assert row.layer_emissions == []


@pytest.mark.asyncio
async def test_tool_card_failed_state_persists_and_raises(
    file_persistence, failing_tool
) -> None:
    ws = FakeWS()
    state = server.SessionState(session_id=new_ulid())
    case_id = await _create_case(ws, state)

    with pytest.raises(RuntimeError, match="upstream exploded"):
        await server._invoke_tool_via_emitter(ws, state, FAILING_TOOL, {})

    session_state = await file_persistence.get_session_state(case_id)
    tool_rows = [m for m in session_state.chat_history if m.role == "tool"]
    assert len(tool_rows) == 1
    card = tool_rows[0].tool_card
    assert card is not None
    assert card.state == "failed"
    assert card.tool_name == FAILING_TOOL
    assert card.duration_ms is not None and card.duration_ms >= 0


@pytest.mark.asyncio
async def test_cancelled_dispatch_persists_no_tool_card(file_persistence) -> None:
    """Invariant 8: cancellation is not a replayable outcome — no card row."""
    name = "job0267_cancelling_tool"

    async def _fn() -> dict:
        raise asyncio.CancelledError()

    meta = AtomicToolMetadata(name=name, ttl_class="live-no-cache", cacheable=False)
    agent_tools.TOOL_REGISTRY[name] = RegisteredTool(
        metadata=meta, fn=_fn, module=__name__
    )
    try:
        ws = FakeWS()
        state = server.SessionState(session_id=new_ulid())
        case_id = await _create_case(ws, state)
        with pytest.raises(asyncio.CancelledError):
            await server._invoke_tool_via_emitter(ws, state, name, {})
        session_state = await file_persistence.get_session_state(case_id)
        assert [m for m in session_state.chat_history if m.role == "tool"] == []
    finally:
        agent_tools.TOOL_REGISTRY.pop(name, None)


@pytest.mark.asyncio
async def test_no_tool_card_write_without_active_case(
    file_persistence, fake_tool, tmp_path
) -> None:
    """No active Case -> dispatch succeeds, nothing lands in the chat store."""
    ws = FakeWS()
    state = server.SessionState(session_id=new_ulid())
    assert state.active_case_id is None
    await server._invoke_tool_via_emitter(ws, state, FAKE_TOOL, {})
    chat_file = tmp_path / "grace2_dev" / "case_chat_messages.json"
    assert (not chat_file.exists()) or chat_file.read_text().strip() in ("{}", "")


# --------------------------------------------------------------------------- #
# 3. Ordering: the rehydrated stream interleaves by created_at
# --------------------------------------------------------------------------- #


@pytest.mark.asyncio
async def test_rehydrated_stream_orders_by_created_at(file_persistence) -> None:
    """Rows written out of order come back interleaved by created_at (ULID
    message_id breaks exact-timestamp ties in write order)."""
    ws = FakeWS()
    state = server.SessionState(session_id=new_ulid())
    case_id = await _create_case(ws, state)

    base = now_utc()
    from datetime import timedelta

    from grace2_contracts.case import CaseChatMessage, ToolCardRecord

    def _row(role, content, offset_s, card=None):
        return CaseChatMessage(
            message_id=new_ulid(),
            case_id=case_id,
            role=role,
            content=content,
            tool_card=card,
            created_at=base + timedelta(seconds=offset_s),
        )

    card = ToolCardRecord(tool_name="t", state="complete", duration_ms=5)
    # Deliberately INSERT out of chronological order.
    await file_persistence.append_chat_message(_row("agent", "done", 10))
    await file_persistence.append_chat_message(_row("user", "go", 0))
    await file_persistence.append_chat_message(
        _row("tool", card.model_dump_json(), 5, card=card)
    )

    session_state = await file_persistence.get_session_state(case_id)
    assert [m.role for m in session_state.chat_history] == ["user", "tool", "agent"]


# --------------------------------------------------------------------------- #
# 4. Server-side case-list hardening (deleted ghost)
# --------------------------------------------------------------------------- #


@pytest.mark.asyncio
async def test_deleted_and_archived_cases_excluded_server_side(
    file_persistence,
) -> None:
    live = CaseSummary(
        case_id=new_ulid(), title="live", created_at=now_utc(), updated_at=now_utc()
    )
    ghost = CaseSummary(
        case_id=new_ulid(), title="ghost", created_at=now_utc(), updated_at=now_utc()
    )
    shelf = CaseSummary(
        case_id=new_ulid(), title="shelf", created_at=now_utc(), updated_at=now_utc()
    )
    for c in (live, ghost, shelf):
        await file_persistence.upsert_case(c)
    await file_persistence.delete_case(ghost.case_id)
    await file_persistence.archive_case(shelf.case_id)

    listed = await file_persistence.list_cases_for_user("anyone")
    titles = {c.title for c in listed}
    assert titles == {"live"}


@pytest.mark.asyncio
async def test_emitted_case_list_envelope_excludes_tombstones(
    file_persistence,
) -> None:
    """The actual ``case-list`` wire emission carries no deleted/archived Case."""
    ws = FakeWS()
    state = server.SessionState(session_id=new_ulid())
    case_id = await _create_case(ws, state)
    ghost = CaseSummary(
        case_id=new_ulid(), title="ghost", created_at=now_utc(), updated_at=now_utc()
    )
    await file_persistence.upsert_case(ghost)
    await file_persistence.delete_case(ghost.case_id)

    ws.sent.clear()
    await server._emit_case_list(ws, state)
    envelopes = [json.loads(t) for t in ws.sent]
    case_lists = [e for e in envelopes if e["type"] == "case-list"]
    assert len(case_lists) == 1
    listed_ids = [c["case_id"] for c in case_lists[0]["payload"]["cases"]]
    assert case_id in listed_ids
    assert ghost.case_id not in listed_ids


@pytest.mark.asyncio
async def test_pre_status_case_docs_stay_listed(file_persistence) -> None:
    """Backward-compat: docs that pre-date the status field are live."""
    legacy_id = new_ulid()
    # Write a raw doc with NO status key at all (pre-CaseStatus record).
    await file_persistence._mcp.call_tool(
        "insert-one",
        {
            "database": file_persistence._db,
            "collection": "projects",
            "document": {
                "_id": legacy_id,
                "schema_version": "v1",
                "case_id": legacy_id,
                "title": "legacy",
                "created_at": now_utc().isoformat().replace("+00:00", "Z"),
                "updated_at": now_utc().isoformat().replace("+00:00", "Z"),
            },
        },
    )
    listed = await file_persistence.list_cases_for_user("anyone")
    assert [c.title for c in listed] == ["legacy"]


# --------------------------------------------------------------------------- #
# 5. User-turn path unchanged
# --------------------------------------------------------------------------- #


@pytest.mark.asyncio
async def test_user_turn_shape_unchanged(file_persistence) -> None:
    ws = FakeWS()
    state = server.SessionState(session_id=new_ulid())
    case_id = await _create_case(ws, state)
    state.current_turn_layer_ids = ["L-1"]
    await server._persist_chat_turn(state, role="user", content="model the flood")

    session_state = await file_persistence.get_session_state(case_id)
    assert len(session_state.chat_history) == 1
    row = session_state.chat_history[0]
    assert row.role == "user"
    assert row.content == "model the flood"
    assert row.layer_emissions == ["L-1"]  # accumulator default preserved
    assert row.tool_card is None
    assert row.pipeline_id is None


# --------------------------------------------------------------------------- #
# 6. Gemini-free E2E: full turn -> complete ordered stream on reopen
# --------------------------------------------------------------------------- #


@pytest.mark.asyncio
async def test_e2e_full_turn_replays_complete_stream(
    file_persistence, fake_tool
) -> None:
    """One simulated turn through the REAL seams: ``_prepare_user_turn``
    (user persist) -> fake Gemini stream that narrates, dispatches a real
    registry tool via ``_invoke_tool_via_emitter``, narrates again ->
    ``_dispatch_gemini_and_persist`` terminal persist. The rehydration
    envelope must replay user -> tool -> agent, in order, with content."""
    ws = FakeWS()
    state = server.SessionState(session_id=new_ulid())
    case_id = await _create_case(ws, state)

    async def fake_stream(websocket, st, settings, user_text, research_mode):
        st.current_turn_narration = []
        st.current_turn_narration.append("I'm fetching the data now. ")
        await server._invoke_tool_via_emitter(websocket, st, FAKE_TOOL, {})
        st.current_turn_narration.append("Done — 3 rows fetched.")
        st.chat_history.append({"role": "user", "text": user_text})

    directive = await server._prepare_user_turn(ws, state, "fetch the data")
    assert directive is None  # Gemini path

    orig = server._stream_gemini_reply
    server._stream_gemini_reply = fake_stream
    try:
        await server._dispatch_gemini_and_persist(
            ws, state, None, "fetch the data", "off"
        )
    finally:
        server._stream_gemini_reply = orig

    # Fresh "browser": reopen the Case and replay the full stream.
    session_state = await file_persistence.get_session_state(case_id)
    rows = session_state.chat_history
    assert [m.role for m in rows] == ["user", "tool", "agent"]
    assert rows[0].content == "fetch the data"
    assert rows[1].tool_card is not None
    assert rows[1].tool_card.tool_name == FAKE_TOOL
    assert rows[1].tool_card.state == "complete"
    assert rows[1].tool_card.duration_ms is not None
    assert rows[2].content == "I'm fetching the data now. Done — 3 rows fetched."
    # created_at strictly non-decreasing — the web interleave key.
    stamps = [m.created_at for m in rows]
    assert stamps == sorted(stamps)
