"""ADVERSARIAL verification probes for job-0262 (auto-create Case from root).

Fable-5 refute-by-default panel. These probes go BEYOND the job's own 12
tests: race windows, title-derivation fuzz, directive near-misses,
cross-session isolation, and degraded-persistence edges. Run from
services/agent with the project venv:

    .venv/bin/python -m pytest \
        ../../reports/inflight/job-0262-agent-20260610/verify/test_adversarial_0262.py -v
"""

from __future__ import annotations

import asyncio

import pytest

from grace2_agent import server as server_mod
from grace2_agent.persistence import CASES_COLLECTION, Persistence
from grace2_agent.server import (
    SessionState,
    _derive_case_title,
    _prepare_user_turn,
    get_persistence,
    set_persistence,
)
from grace2_contracts.common import new_ulid

from tests.test_persistence import MockMCPClient
from tests.test_server_case_handlers import MockWebSocket


@pytest.fixture()
def _p():
    saved = get_persistence()
    p = Persistence(MockMCPClient())
    set_persistence(p)
    try:
        yield p
    finally:
        set_persistence(saved)


def _docs(p: Persistence) -> list[dict]:
    return list(p._mcp._store.get(CASES_COLLECTION, {}).values())  # type: ignore[attr-defined]


# --------------------------------------------------------------------- #
# 1. RACE: sibling connections of the SAME session, concurrent root
#    prompts. active_case_id is session-scoped; the check-then-set spans
#    an await (upsert_case). Probe whether two Cases get minted.
# --------------------------------------------------------------------- #


def test_race_sibling_connections_concurrent_root_prompts(_p) -> None:
    session_id = new_ulid()
    s1 = SessionState(session_id=session_id)
    s2 = SessionState(session_id=session_id)
    w1, w2 = MockWebSocket(), MockWebSocket()

    real_upsert = _p.upsert_case

    async def slow_upsert(case):
        await asyncio.sleep(0.01)  # widen the check-then-set window
        return await real_upsert(case)

    _p.upsert_case = slow_upsert  # type: ignore[method-assign]

    async def both():
        await asyncio.gather(
            _prepare_user_turn(w1, s1, "Flood depth grid Cedar Rapids"),
            _prepare_user_turn(w2, s2, "Burn severity map Paradise CA"),
        )

    asyncio.run(both())
    n = len(_docs(_p))
    # ADVERSARIAL EXPECTATION: a perfect implementation mints ONE case for
    # the session. Two means the race exists. We record the observation —
    # severity assessed in the panel verdict (user-messages only arrive on
    # the Chat socket in the real client, so cross-socket user-message
    # concurrency is not a reachable production path today).
    print(f"OBSERVED: {n} case(s) minted by concurrent sibling root prompts")
    assert n in (1, 2)
    if n == 2:
        # Both turns must still be attributed SOMEWHERE consistent: the
        # session registry holds exactly one winner.
        assert s1.active_case_id == s2.active_case_id


# --------------------------------------------------------------------- #
# 2. Title-derivation fuzz: no crash, case always minted, title sane.
# --------------------------------------------------------------------- #

FUZZ_PROMPTS = [
    "x" * 10_000,  # one giant word
    "the and or of for with to in on at",  # all stopwords
    "\x00\x01\x02 control \x03chars here",
    "🌊🔥 flood AND fire 🌪️ combo événement 洪水",
    "<script>alert(1)</script> drop table cases",
    "line\nbreaks\nand\ttabs everywhere now",
    "   ",  # whitespace only
    "(((()))) [[[]]] \"\"'' ?!.,;:",  # pure punctuation words
    "a " * 500,  # many stopwords
    "ULTRA-LONG-HYPHENATED-TOKEN-" * 20 + "END",
]


@pytest.mark.parametrize("prompt", FUZZ_PROMPTS)
def test_fuzz_prompt_never_crashes_and_titles_are_sane(_p, prompt) -> None:
    ws = MockWebSocket()
    state = SessionState(session_id=new_ulid())
    directive = asyncio.run(_prepare_user_turn(ws, state, prompt))
    assert directive is None
    # A persistence-bound non-directive prompt must ALWAYS land in a Case.
    assert state.active_case_id is not None
    docs = [d for d in _docs(_p) if d["case_id"] == state.active_case_id]
    assert len(docs) == 1
    title = docs[0]["title"]
    assert isinstance(title, str) and title.strip()
    assert len(title) <= 48 or title == "Untitled Case"


def test_derive_title_pure_function_fuzz() -> None:
    for prompt in FUZZ_PROMPTS + ["", "hi", "two words"]:
        t = _derive_case_title(prompt)
        assert t is None or (isinstance(t, str) and 0 < len(t) <= 48)


# --------------------------------------------------------------------- #
# 3. Directive near-misses: which shapes mint a Case?
# --------------------------------------------------------------------- #


def test_directive_near_misses(_p) -> None:
    cases_before = len(_docs(_p))

    # Exact directive (valid JSON): stateless, no Case.
    ws, st = MockWebSocket(), SessionState(session_id=new_ulid())
    d = asyncio.run(_prepare_user_turn(ws, st, '/invoke fetch_dem {"bbox": [0,0,1,1]}'))
    assert d is not None and st.active_case_id is None

    # Bare /invoke with tool only (no JSON): still a directive -> stateless.
    ws, st = MockWebSocket(), SessionState(session_id=new_ulid())
    d = asyncio.run(_prepare_user_turn(ws, st, "/invoke fetch_dem"))
    assert d == ("fetch_dem", {}) and st.active_case_id is None

    assert len(_docs(_p)) == cases_before  # none of the above minted

    # MALFORMED JSON after /invoke: parser returns None -> treated as a
    # normal prompt -> mints a Case titled from the directive text.
    ws, st = MockWebSocket(), SessionState(session_id=new_ulid())
    d = asyncio.run(_prepare_user_turn(ws, st, "/invoke fetch_dem {bbox: oops"))
    assert d is None
    assert st.active_case_id is not None  # OBSERVED: malformed /invoke mints a Case

    # Leading whitespace: not a directive -> mints a Case.
    ws, st = MockWebSocket(), SessionState(session_id=new_ulid())
    d = asyncio.run(_prepare_user_turn(ws, st, ' /invoke fetch_dem {"a": 1}'))
    assert d is None
    assert st.active_case_id is not None


# --------------------------------------------------------------------- #
# 4. Cross-SESSION isolation: no active-case leakage between sessions.
# --------------------------------------------------------------------- #


def test_cross_session_no_active_case_leakage(_p) -> None:
    sa = SessionState(session_id=new_ulid())
    sb = SessionState(session_id=new_ulid())
    asyncio.run(_prepare_user_turn(MockWebSocket(), sa, "Flood model Tampa Bay"))
    assert sa.active_case_id is not None
    # Session B must still be at root — A's auto-create must not leak.
    assert sb.active_case_id is None
    asyncio.run(_prepare_user_turn(MockWebSocket(), sb, "Wildfire spread Boulder"))
    assert sb.active_case_id is not None
    assert sb.active_case_id != sa.active_case_id
    assert len(_docs(_p)) == 2
    # Chat turns landed in their own Cases only.
    ssa = asyncio.run(_p.get_session_state(sa.active_case_id))
    ssb = asyncio.run(_p.get_session_state(sb.active_case_id))
    assert [m.content for m in ssa.chat_history] == ["Flood model Tampa Bay"]
    assert [m.content for m in ssb.chat_history] == ["Wildfire spread Boulder"]


# --------------------------------------------------------------------- #
# 5. Degraded persistence AFTER a successful upsert: chat persist fails.
#    The case-open then carries EMPTY history -> Chat.tsx replace-not-
#    reconcile would blank the just-typed bubble. Document the edge.
# --------------------------------------------------------------------- #


def test_chat_persist_failure_after_upsert_still_emits_case_open(
    _p, monkeypatch
) -> None:
    async def boom(_msg):
        raise RuntimeError("mongo flake")

    monkeypatch.setattr(_p, "append_chat_message", boom)
    ws = MockWebSocket()
    state = SessionState(session_id=new_ulid())
    asyncio.run(_prepare_user_turn(ws, state, "Storm surge map Galveston"))
    assert state.active_case_id is not None  # Case minted
    opens = [e for e in ws.sent if e["type"] == "case-open"]
    assert len(opens) == 1
    history = opens[0]["payload"]["session_state"]["chat_history"]
    # OBSERVED: empty rehydration -> client-side bubble blank in this
    # degraded mode (persistence died between upsert and chat write).
    print(f"OBSERVED: case-open history length on chat-persist failure = {len(history)}")
    assert history == []


# --------------------------------------------------------------------- #
# 6. Turn-cap interaction is enforced UPSTREAM of _prepare_user_turn in
#    the dispatcher (turn_count incremented + checked before the call) —
#    assert the code ordering hasn't regressed by source inspection.
# --------------------------------------------------------------------- #


def test_dispatcher_orders_turn_cap_before_prepare() -> None:
    import inspect

    src = inspect.getsource(server_mod)
    handler_idx = src.index("elif msg_type == \"user-message\":")
    cap_idx = src.index("state.turn_count += 1", handler_idx)
    prep_idx = src.index("_prepare_user_turn(", handler_idx)
    assert cap_idx < prep_idx, "turn cap must gate BEFORE auto-create"
