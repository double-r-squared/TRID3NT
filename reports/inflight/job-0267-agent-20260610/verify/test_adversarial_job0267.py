"""Adversarial verification probes for job-0267 (full-stream persistence).

Run with the agent venv:
    /home/nate/Documents/GRACE-2/services/agent/.venv/bin/python -m pytest \
        reports/inflight/job-0267-agent-20260610/verify/test_adversarial_job0267.py -v

Attack vectors (per the verifier kickoff):

  A. Stream cross-contamination — a stream dispatched while Case A was active
     persists its narration into Case B when the user opens Case B mid-stream
     (the web client sends NO cancel on case select; case-command runs inline
     in the message loop while the turn runs as a background task).
  B. Same contamination for the tool-card row (long-running tool + mid-flight
     case switch).
  C. ULID tiebreak monotonicity — the sort docstring claims the ULID
     message_id "breaks ties in write order"; python-ulid ULIDs are only
     write-ordered within a tie if monotonic.
  D. Auto-create -> flip-into-case hand-off: a prompt from the Cases root
     must land user + tool + agent rows in the auto-created Case.
  E. Deleted-ghost defense-in-depth: a backend that silently IGNORES $nin
     must still produce a tombstone-free list (the Python guard).

Probes A and B are EXPECTED-BUG probes: they assert the contaminated
behavior so a pass == bug demonstrated. If a future job fixes turn/case
binding they will fail loudly and should be inverted.
"""

from __future__ import annotations

import asyncio

import pytest

from grace2_agent import server
from grace2_agent import tools as agent_tools
from grace2_agent.persistence import FileMCPClient, make_file_persistence
from grace2_agent.tools import RegisteredTool
from grace2_contracts.case import CaseCommandEnvelopePayload
from grace2_contracts.common import new_ulid
from grace2_contracts.tool_registry import AtomicToolMetadata


class FakeWS:
    def __init__(self) -> None:
        self.sent: list[str] = []

    async def send(self, text: str) -> None:
        self.sent.append(text)


@pytest.fixture()
def file_persistence(tmp_path):
    p = make_file_persistence(base_dir=tmp_path)
    server.set_persistence(p)
    try:
        yield p
    finally:
        server.set_persistence(None)


@pytest.fixture(autouse=True)
def _clean_session_registry():
    server._SESSION_ACTIVE_CASE.clear()
    yield
    server._SESSION_ACTIVE_CASE.clear()


async def _create_case(ws, state, title) -> str:
    cmd = CaseCommandEnvelopePayload(command="create", args={"title": title})
    await server._handle_case_command(ws, state, cmd)
    case_id = state.active_case_id
    assert case_id
    return case_id


# --------------------------------------------------------------------------- #
# A. Narration cross-contamination on mid-stream case switch
# --------------------------------------------------------------------------- #


@pytest.mark.asyncio
async def test_A_narration_paints_into_other_case_on_midstream_switch(
    file_persistence,
) -> None:
    """EXPECTED-BUG probe: Case A's narration row lands in Case B.

    Reproduces the server-reachable sequence: user-message dispatches the
    turn as a background task (server.py:3754 asyncio.create_task); a
    case-command(select) arrives mid-stream and is handled inline WITHOUT
    cancelling state.inflight_task (server.py:1700-1710 / _emit_case_open);
    the turn's finally-block persist reads state.active_case_id at WRITE
    time (server.py _persist_chat_turn), which now names Case B.
    """
    ws = FakeWS()
    state = server.SessionState(session_id=new_ulid())
    case_a = await _create_case(ws, state, "Case A")
    await server._persist_chat_turn(state, role="user", content="flood in A")

    release = asyncio.Event()

    async def slow_stream(websocket, st, settings, user_text, research_mode):
        st.current_turn_narration = []
        st.current_turn_narration.append("Case A flood narration.")
        await release.wait()
        st.chat_history.append({"role": "user", "text": user_text})

    orig = server._stream_gemini_reply
    server._stream_gemini_reply = slow_stream
    try:
        task = asyncio.create_task(
            server._dispatch_gemini_and_persist(ws, state, None, "flood in A", "off")
        )
        await asyncio.sleep(0.05)  # stream is now mid-flight, accumulator full

        # User clicks Case B in the left rail -> case-command(select).
        case_b = await _create_case(ws, state, "Case B")
        sel = CaseCommandEnvelopePayload(command="select", case_id=case_b)
        await server._handle_case_command(ws, state, sel)

        release.set()
        await task
    finally:
        server._stream_gemini_reply = orig

    chat_a = (await file_persistence.get_session_state(case_a)).chat_history
    chat_b = (await file_persistence.get_session_state(case_b)).chat_history

    roles_a = [m.role for m in chat_a]
    contents_b = [(m.role, m.content) for m in chat_b]

    # The user's turn stayed in Case A; the narration it produced did NOT.
    assert roles_a == ["user"], f"Case A lost its narration: {roles_a}"
    assert ("agent", "Case A flood narration.") in contents_b, (
        f"expected Case A narration painted into Case B, got {contents_b}"
    )


# --------------------------------------------------------------------------- #
# B. Tool-card cross-contamination on mid-dispatch case switch
# --------------------------------------------------------------------------- #


@pytest.mark.asyncio
async def test_B_tool_card_paints_into_other_case_on_middispatch_switch(
    file_persistence,
) -> None:
    """EXPECTED-BUG probe: Case A's tool card lands in Case B.

    Long-running tool (run_model_flood_scenario-class) dispatched under
    Case A; user opens Case B while it runs; the terminal tool-card persist
    (_invoke_tool_via_emitter finally -> _persist_tool_card ->
    _persist_chat_turn) reads state.active_case_id at write time.
    """
    name = "job0267_verify_slow_tool"
    gate = asyncio.Event()

    async def _fn() -> dict:
        await gate.wait()
        return {"ok": True}

    meta = AtomicToolMetadata(name=name, ttl_class="live-no-cache", cacheable=False)
    agent_tools.TOOL_REGISTRY[name] = RegisteredTool(
        metadata=meta, fn=_fn, module=__name__
    )
    try:
        ws = FakeWS()
        state = server.SessionState(session_id=new_ulid())
        case_a = await _create_case(ws, state, "Case A")

        task = asyncio.create_task(
            server._invoke_tool_via_emitter(ws, state, name, {})
        )
        await asyncio.sleep(0.05)  # dispatch in flight under Case A

        case_b = await _create_case(ws, state, "Case B")
        sel = CaseCommandEnvelopePayload(command="select", case_id=case_b)
        await server._handle_case_command(ws, state, sel)

        gate.set()
        await task
    finally:
        agent_tools.TOOL_REGISTRY.pop(name, None)

    tools_a = [
        m for m in (await file_persistence.get_session_state(case_a)).chat_history
        if m.role == "tool"
    ]
    tools_b = [
        m for m in (await file_persistence.get_session_state(case_b)).chat_history
        if m.role == "tool"
    ]
    assert tools_a == [], "tool card unexpectedly (correctly!) stayed in Case A"
    assert len(tools_b) == 1 and tools_b[0].tool_card.tool_name == name, (
        f"expected Case A tool card painted into Case B, got {tools_b}"
    )


# --------------------------------------------------------------------------- #
# C. ULID tiebreak monotonicity (sort-docstring claim)
# --------------------------------------------------------------------------- #


def test_C_ulid_tiebreak_is_not_write_ordered_within_same_ms() -> None:
    """The get_session_state sort comment claims the ULID message_id breaks
    created_at ties "in write order". python-ulid randomness within one ms
    is NOT monotonic, so this only holds because created_at carries
    microseconds (ties are practically impossible). Document the fact."""
    ulids = [new_ulid() for _ in range(5000)]
    is_sorted = all(a <= b for a, b in zip(ulids, ulids[1:]))
    # EXPECTED-FACT probe: not monotonic. If this starts failing, the ULID
    # lib became monotonic and the docstring becomes accurate.
    assert not is_sorted, "ULIDs unexpectedly monotonic — docstring now accurate"


# --------------------------------------------------------------------------- #
# D. Auto-create -> flip-into-case hand-off
# --------------------------------------------------------------------------- #


@pytest.mark.asyncio
async def test_D_auto_created_case_receives_full_stream(file_persistence) -> None:
    """A prompt from the Cases root auto-creates a Case BEFORE dispatch, so
    user + tool + agent rows all land in it (the hand-off the verifier
    kickoff names)."""
    name = "job0267_verify_root_tool"

    async def _fn() -> dict:
        return {"rows": 1}

    meta = AtomicToolMetadata(name=name, ttl_class="live-no-cache", cacheable=False)
    agent_tools.TOOL_REGISTRY[name] = RegisteredTool(
        metadata=meta, fn=_fn, module=__name__
    )
    try:
        ws = FakeWS()
        state = server.SessionState(session_id=new_ulid())
        assert state.active_case_id is None  # Cases root

        directive = await server._prepare_user_turn(
            ws, state, "model the flood in fort myers"
        )
        assert directive is None
        auto_case = state.active_case_id
        assert auto_case, "auto-create did not bind a Case"

        async def fake_stream(websocket, st, settings, user_text, research_mode):
            st.current_turn_narration = []
            st.current_turn_narration.append("Working. ")
            await server._invoke_tool_via_emitter(websocket, st, name, {})
            st.current_turn_narration.append("Done.")
            st.chat_history.append({"role": "user", "text": user_text})

        orig = server._stream_gemini_reply
        server._stream_gemini_reply = fake_stream
        try:
            await server._dispatch_gemini_and_persist(
                ws, state, None, "model the flood in fort myers", "off"
            )
        finally:
            server._stream_gemini_reply = orig
    finally:
        agent_tools.TOOL_REGISTRY.pop(name, None)

    rows = (await file_persistence.get_session_state(auto_case)).chat_history
    assert [m.role for m in rows] == ["user", "tool", "agent"]
    assert rows[0].content == "model the flood in fort myers"
    assert rows[2].content == "Working. Done."


# --------------------------------------------------------------------------- #
# E. Python guard catches a backend that ignores $nin
# --------------------------------------------------------------------------- #


@pytest.mark.asyncio
async def test_E_python_guard_filters_tombstones_when_backend_ignores_nin(
    tmp_path, monkeypatch,
) -> None:
    from grace2_contracts.case import CaseSummary
    from grace2_contracts.common import now_utc

    p = make_file_persistence(base_dir=tmp_path)
    server.set_persistence(p)
    try:
        live = CaseSummary(
            case_id=new_ulid(), title="live",
            created_at=now_utc(), updated_at=now_utc(),
        )
        ghost = CaseSummary(
            case_id=new_ulid(), title="ghost",
            created_at=now_utc(), updated_at=now_utc(),
        )
        await p.upsert_case(live)
        await p.upsert_case(ghost)
        await p.delete_case(ghost.case_id)

        # Lobotomize the file backend's query matcher: strip every $nin
        # clause, simulating an MCP backend whose dialect ignores it.
        orig_matches = FileMCPClient._matches  # plain function in py3.10+

        def nin_blind(doc, filt):
            stripped = {
                k: v
                for k, v in filt.items()
                if not (isinstance(v, dict) and "$nin" in v)
            }
            return orig_matches(doc, stripped)

        monkeypatch.setattr(FileMCPClient, "_matches", staticmethod(nin_blind))

        listed = await p.list_cases_for_user("anyone")
        assert [c.title for c in listed] == ["live"], (
            "Python guard failed: tombstone survived a $nin-blind backend"
        )
    finally:
        server.set_persistence(None)
