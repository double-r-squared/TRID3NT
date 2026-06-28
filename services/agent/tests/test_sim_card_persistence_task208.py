"""task-208 — SIM/compute-card DURABILITY: the Batch-bound SIM card replays
like every other tool card across a WS reconnect / Case reopen.

The two-card sim observability (task-149) mints a "Dispatch" tool card
(``add_step`` -> ``mark_complete``, ALREADY persisted as a ``role="tool"`` row)
and a "Sim" compute card (``add_compute_step``, ``role="compute"``) bound to the
AWS Batch jobId. The Dispatch card replayed on reopen; the SIM card was NEVER
persisted -- it lived only on the wire -- so a WS reconnect / Case reopen
replayed an EMPTY pipeline and the user's green/red solve card vanished
(task #208: "tool-card flicker on refresh/reconnect, sim card non-durable").

These tests drive the REAL seams (no Bedrock, no Batch, no Playwright):

  PART 2 (persist):
    (a) a COMPLETE solve persists a ``role="tool"`` ``CaseChatMessage`` carrying
        a ``ToolCardRecord(state="complete")`` with the compute step's label +
        duration, and it round-trips through ``get_session_state``;
    (b) a FAILED solve persists ``state="failed"`` (the honesty floor: a solve
        failure SURFACES across a socket cycle);
    (c) a CANCELLED solve persists NOTHING (Invariant 8 -- no replay row);
    (d) the Dispatch card is NOT double-persisted by this path (only the
        ``role="compute"`` SIM card is the new write);
    (e) ``route_sim_terminal`` with NO persist hook (verify/CI/direct call)
        still drives the live card terminal -- it just writes no row.

  PART 1 (reconnect carries it):
    (f) a bare WS reconnect replays the persisted SIM tool-card row in the
        resume ``session-state`` payload's ``chat_history`` (so the green/red
        card re-renders without a case-open) -- the end-to-end durability proof.
"""

from __future__ import annotations

import json

import pytest

from grace2_agent import server
from grace2_agent.pipeline_emitter import route_sim_terminal
from grace2_agent.persistence import make_file_persistence
from grace2_contracts.case import CaseCommandEnvelopePayload
from grace2_contracts.common import new_ulid


class FakeWS:
    """Minimal ServerConnection stand-in: records every envelope sent."""

    def __init__(self) -> None:
        self.sent: list[str] = []
        self.closed = False

    async def send(self, text: str) -> None:
        if self.closed:
            raise ConnectionError("socket closed")
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
    server._SESSION_ACTIVE_CASE.clear()
    server._SESSION_LIVE_TURNS.clear()
    yield
    server._SESSION_ACTIVE_CASE.clear()
    server._SESSION_LIVE_TURNS.clear()


async def _create_case(ws, state, title="Sim Card Case") -> str:
    cmd = CaseCommandEnvelopePayload(command="create", args={"title": title})
    await server._handle_case_command(ws, state, cmd)
    case_id = state.active_case_id
    assert case_id, "create must bind the active case"
    return case_id


class _RunResult:
    """Minimal RunResult stand-in (the wait_for_completion return shape)."""

    def __init__(self, status: str, error_code=None, error_message=None) -> None:
        self.status = status
        self.error_code = error_code
        self.error_message = error_message


async def _mint_sim_card(state) -> str:
    """Mint the SIM (role=compute) card on the session emitter; return its id."""
    sim_id = await state.emitter.add_compute_step(
        name="sfincs solve",
        tool_name="sfincs:solve",
        batch_job_id="job-abc123",
        batch_status="SUBMITTED",
    )
    return sim_id


# --------------------------------------------------------------------------- #
# (a) a COMPLETE solve persists a replayable role="tool" SIM card row
# --------------------------------------------------------------------------- #


@pytest.mark.asyncio
async def test_complete_sim_card_persists_and_replays(file_persistence) -> None:
    ws = FakeWS()
    state = server.SessionState(session_id=new_ulid())
    server._ensure_emitter(ws, state)  # wires the _tool_card_persist hook
    case_id = await _create_case(ws, state)

    sim_id = await _mint_sim_card(state)
    await route_sim_terminal(
        state.emitter, sim_id, run_result=_RunResult("complete")
    )

    session_state = await file_persistence.get_session_state(case_id)
    tool_rows = [m for m in session_state.chat_history if m.role == "tool"]
    assert len(tool_rows) == 1, (
        "a complete SIM compute card must persist exactly one replayable "
        "role='tool' chat_history row"
    )
    card = tool_rows[0].tool_card
    assert card is not None
    assert card.tool_name == "sfincs:solve"
    assert card.label == "sfincs solve"
    assert card.state == "complete"
    # Duration is the authoritative emitter stamp (>= 0; deterministic).
    assert card.duration_ms is not None and card.duration_ms >= 0
    # The content JSON twin matches the typed record (non-contract consumers).
    twin = json.loads(tool_rows[0].content)
    assert twin["state"] == "complete"
    assert twin["tool_name"] == "sfincs:solve"


# --------------------------------------------------------------------------- #
# (b) a FAILED solve persists state="failed" (honesty floor)
# --------------------------------------------------------------------------- #


@pytest.mark.asyncio
async def test_failed_sim_card_persists_failed(file_persistence) -> None:
    ws = FakeWS()
    state = server.SessionState(session_id=new_ulid())
    server._ensure_emitter(ws, state)
    case_id = await _create_case(ws, state)

    sim_id = await _mint_sim_card(state)
    await route_sim_terminal(
        state.emitter,
        sim_id,
        run_result=_RunResult(
            "failed", error_code="SOLVER_TIMEOUT", error_message="ran out of budget"
        ),
    )

    session_state = await file_persistence.get_session_state(case_id)
    tool_rows = [m for m in session_state.chat_history if m.role == "tool"]
    assert len(tool_rows) == 1
    card = tool_rows[0].tool_card
    assert card is not None
    assert card.state == "failed", (
        "a terminal solve FAILURE must persist a RED card so it surfaces across "
        "a socket cycle (not a forever-spinning card)"
    )


# --------------------------------------------------------------------------- #
# (c) a CANCELLED solve persists NOTHING (Invariant 8)
# --------------------------------------------------------------------------- #


@pytest.mark.asyncio
async def test_cancelled_sim_card_persists_nothing(file_persistence) -> None:
    ws = FakeWS()
    state = server.SessionState(session_id=new_ulid())
    server._ensure_emitter(ws, state)
    case_id = await _create_case(ws, state)

    sim_id = await _mint_sim_card(state)
    # cancel: run_result is None -> mark_cancelled, NO persist
    await route_sim_terminal(state.emitter, sim_id, run_result=None)

    session_state = await file_persistence.get_session_state(case_id)
    tool_rows = [m for m in session_state.chat_history if m.role == "tool"]
    assert tool_rows == [], (
        "a cancelled SIM card persists nothing (Invariant 8): a cancel is not a "
        "completion and must leave no replay row"
    )


# --------------------------------------------------------------------------- #
# (d) the Dispatch card is NOT double-persisted by THIS path
# --------------------------------------------------------------------------- #


@pytest.mark.asyncio
async def test_only_compute_card_persisted_not_dispatch(file_persistence) -> None:
    """``route_sim_terminal``'s persist writes the role='compute' SIM card ONLY.

    The Dispatch (Card 1) tool card is persisted on the on-box tool path (it is
    a plain ``add_step`` -> ``mark_complete`` minted by the composer); THIS new
    write must add EXACTLY ONE row for the SIM card -- never a second Dispatch
    row -- so a re-open shows one solve card, not a duplicate.
    """
    ws = FakeWS()
    state = server.SessionState(session_id=new_ulid())
    server._ensure_emitter(ws, state)
    case_id = await _create_case(ws, state)

    sim_id = await _mint_sim_card(state)
    await route_sim_terminal(
        state.emitter, sim_id, run_result=_RunResult("complete")
    )

    session_state = await file_persistence.get_session_state(case_id)
    tool_rows = [m for m in session_state.chat_history if m.role == "tool"]
    assert len(tool_rows) == 1
    assert tool_rows[0].tool_card is not None
    assert tool_rows[0].tool_card.tool_name == "sfincs:solve"


# --------------------------------------------------------------------------- #
# (e) no persist hook (verify/CI/direct call) -> live card still terminal,
#     no crash, no row written
# --------------------------------------------------------------------------- #


@pytest.mark.asyncio
async def test_no_persist_hook_still_marks_terminal(file_persistence) -> None:
    from grace2_agent.pipeline_emitter import PipelineEmitter

    frames: list[str] = []

    async def _sink(text: str) -> None:
        frames.append(text)

    # Emitter built WITHOUT a tool_card_persist hook (the direct/verify path).
    emitter = PipelineEmitter(session_id=new_ulid(), sink=_sink)
    sim_id = await emitter.add_compute_step(
        name="swmm solve", tool_name="swmm:solve", batch_job_id="j"
    )
    await route_sim_terminal(emitter, sim_id, run_result=_RunResult("complete"))

    # The live card still reached its terminal state on the wire.
    last = json.loads(frames[-1])
    assert last["type"] == "pipeline-state"
    assert last["payload"]["steps"][-1]["state"] == "complete"


# --------------------------------------------------------------------------- #
# (f) PART 1 END-TO-END: a bare WS reconnect replays the persisted SIM card in
#     the resume session-state payload's chat_history.
# --------------------------------------------------------------------------- #


def _session_states(ws: FakeWS) -> list[dict]:
    out: list[dict] = []
    for raw in ws.sent:
        try:
            env = json.loads(raw)
        except Exception:  # noqa: BLE001
            continue
        if env.get("type") == "session-state":
            out.append(env)
    return out


@pytest.mark.asyncio
async def test_reconnect_replays_persisted_sim_card(file_persistence) -> None:
    """The durability keystone: a SIM card persisted on solve-complete replays
    in the bare-reconnect session-state's ``chat_history`` (PART 1 carries what
    PART 2 persisted), so the green solve card re-renders with NO case-open.
    """
    # Connection 1: create the case + complete a solve (persists the SIM card).
    ws1 = FakeWS()
    state1 = server.SessionState(session_id=new_ulid())
    server._ensure_emitter(ws1, state1)
    case_id = await _create_case(ws1, state1)
    sim_id = await _mint_sim_card(state1)
    await route_sim_terminal(
        state1.emitter, sim_id, run_result=_RunResult("complete")
    )

    # Connection 2 (a fresh socket / same session): a BARE reconnect. The
    # session's active Case survives via _SESSION_ACTIVE_CASE; the resume
    # replays the persisted chat_history into the emitter so the single
    # session-state carries the SIM tool-card row.
    server._set_session_active_case(state1.session_id, case_id)
    ws2 = FakeWS()
    state2 = server.SessionState(session_id=state1.session_id)
    server._ensure_emitter(ws2, state2)

    await server._handle_session_resume(ws2, state2)

    states = _session_states(ws2)
    assert len(states) == 1, "bare resume emits exactly one session-state"
    chat = states[0]["payload"]["chat_history"]
    tool_msgs = [m for m in chat if m["role"] == "tool"]
    assert len(tool_msgs) == 1, (
        "the reconnect session-state must carry the persisted SIM tool-card row "
        "so the green solve card re-renders without a case-open"
    )
    assert tool_msgs[0]["tool_card"]["state"] == "complete"
    assert tool_msgs[0]["tool_card"]["tool_name"] == "sfincs:solve"
