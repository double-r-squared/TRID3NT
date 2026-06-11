"""job-0270 ADVERSARIAL VERIFICATION — does FIX A (validator auto-widen)
bypass any post-validation safety gate?

Attack thesis: pre-0270, a registry-valid tool outside the hot set was
REJECTED at validation, so the solver-confirm / payload-warning / code-exec
gates in ``_invoke_tool_via_emitter`` were never reachable for it without an
explicit catalog detour. Post-0270 the validator lets such a call through
directly — if any gate were keyed off allowed-set membership (rather than
tool name) or were skipped on this path, auto-widen would have created an
unconfirmed-solver / unwarned-payload hole.

These tests drive the REAL ``_stream_gemini_reply`` + REAL
``_invoke_tool_via_emitter`` (only the gate internals are replaced with
recorders so nothing blocks on user confirmation futures, and no live GCP
dispatch happens) and prove each gate still fires for an auto-widened tool.

Run:
    cd services/agent && .venv/bin/python -m pytest \
        ../../reports/inflight/job-0270-agent-20260610/verify/test_gates_after_autowiden_0270.py -v
"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any
from unittest.mock import AsyncMock, MagicMock, patch

import pytest

from grace2_agent.adapter import GeminiSettings
from grace2_contracts import new_ulid


@pytest.fixture(scope="module", autouse=True)
def _populate_registry() -> None:
    from grace2_agent.main import _import_tools_registry

    _import_tools_registry()


def _make_fake_chunk_with_function_call(name: str, args: dict, call_id: str = "c1"):
    fn_call = MagicMock()
    fn_call.name = name
    fn_call.id = call_id
    fn_call.args = args
    fake_part = MagicMock()
    fake_part.function_call = fn_call
    fake_part.text = None
    fake_content = MagicMock()
    fake_content.parts = [fake_part]
    fake_candidate = MagicMock()
    fake_candidate.content = fake_content
    fake_chunk = MagicMock()
    fake_chunk.candidates = [fake_candidate]
    fake_chunk.text = None
    return fake_chunk


def _make_fake_chunk_with_text(text: str):
    fake_part = MagicMock()
    fake_part.function_call = None
    fake_part.text = text
    fake_content = MagicMock()
    fake_content.parts = [fake_part]
    fake_candidate = MagicMock()
    fake_candidate.content = fake_content
    fake_chunk = MagicMock()
    fake_chunk.candidates = [fake_candidate]
    fake_chunk.text = None
    return fake_chunk


@dataclass
class _FakeSocket:
    sent: list[str] = field(default_factory=list)

    async def send(self, msg: str) -> None:  # noqa: D401 — protocol shim
        self.sent.append(msg)


def _function_response_payloads(contents_per_turn: list[list[Any]]) -> list[tuple[str, dict]]:
    out: list[tuple[str, dict]] = []
    for contents in contents_per_turn:
        for content in contents:
            for part in content.parts:
                fr = getattr(part, "function_response", None)
                if fr is not None and not isinstance(fr, MagicMock):
                    out.append((fr.name, dict(fr.response)))
    return out


async def _drive_loop_real_invoke(
    turn_chunks: list[list[Any]],
    extra_patches: list,
    state_setup=None,
) -> tuple[list[list[Any]], "_FakeSocket", Any]:
    """Run ``_stream_gemini_reply`` with the REAL ``_invoke_tool_via_emitter``.

    Only ``build_client`` / ``build_tool_declarations`` are faked (no Gemini)
    plus whatever gate recorders the caller passes in ``extra_patches``.
    """
    from grace2_agent import server as agent_server
    from grace2_agent.server import SessionState

    turn_responses = iter([iter(chunks) for chunks in turn_chunks])
    contents_per_turn: list[list[Any]] = []

    def _capture_and_stream(**kwargs):
        contents_per_turn.append(list(kwargs["contents"]))
        return next(turn_responses)

    fake_client = MagicMock()
    fake_client.models.generate_content_stream.side_effect = _capture_and_stream

    sock = _FakeSocket()
    state = SessionState(session_id=new_ulid())
    if state_setup is not None:
        state_setup(state)
    settings = GeminiSettings(
        model="gemini-2.5-pro", project="test", location="us-central1", use_vertex=True
    )

    patches = [
        patch.object(agent_server, "build_client", return_value=fake_client),
        patch.object(agent_server, "build_tool_declarations", return_value=[]),
        *extra_patches,
    ]
    try:
        for p in patches:
            p.start()
        await agent_server._stream_gemini_reply(
            sock, state, settings, "verification probe", "research"
        )
    finally:
        for p in patches:
            p.stop()
    return contents_per_turn, sock, state


# ---------------------------------------------------------------------------
# 1. Solver-confirm gate must fire for an auto-widened solver composer
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_solver_confirm_gate_fires_for_auto_widened_solver() -> None:
    """``run_model_groundwater_contamination_scenario`` is a SOLVER_CONFIRM
    tool OUTSIDE the hot set. Post-0270 the validator auto-widens it — the
    consequence gate (FR-AS-8 / Invariant 9) must STILL intercept the
    dispatch before the solver body runs."""
    from grace2_agent import server as agent_server

    tool = "run_model_groundwater_contamination_scenario"
    assert tool in agent_server.SOLVER_CONFIRM_TOOLS
    from grace2_agent.categories import HOT_SET_TOOLS

    assert tool not in HOT_SET_TOOLS, "precondition: must require auto-widen"

    gate = AsyncMock(return_value=(False, {}))  # user declines
    body = MagicMock(side_effect=AssertionError("solver body must never run"))

    import dataclasses

    from grace2_agent.tools import TOOL_REGISTRY

    contents_per_turn, _sock, state = await _drive_loop_real_invoke(
        [
            [_make_fake_chunk_with_function_call(tool, {"bbox": [0, 0, 1, 1]})],
            [_make_fake_chunk_with_text("Cancelled.")],
        ],
        extra_patches=[
            patch.object(agent_server, "_gate_on_solver_confirm", gate),
            patch.dict(
                TOOL_REGISTRY,
                {tool: dataclasses.replace(TOOL_REGISTRY[tool], fn=body)},
            ),
        ],
    )

    # The gate WAS consulted, exactly once, for this tool.
    assert gate.await_count == 1
    assert gate.await_args.args[2] == tool
    # The solver body never ran.
    body.assert_not_called()
    # The validator did auto-widen (validation passed without a detour)...
    assert tool in state.allowed_tool_set.as_frozenset()
    # ...and Gemini saw the structured cancellation, not an allowed-set bounce.
    payloads = _function_response_payloads(contents_per_turn)
    assert payloads
    _name, payload = payloads[0]
    assert payload.get("status") == "error"
    assert payload.get("error_code") == "SOLVER_CONFIRMATION_CANCELLED"


# ---------------------------------------------------------------------------
# 2. Payload-warning gate must be consulted for an auto-widened tool
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_payload_warning_gate_consulted_for_auto_widened_tool() -> None:
    """``compute_colored_relief`` (outside the hot set) auto-widens; the
    payload-warning gate inside ``_invoke_tool_via_emitter`` must still be
    on its dispatch path. Recorder declines the dispatch."""
    from grace2_agent import server as agent_server

    tool = "compute_colored_relief"
    gate = AsyncMock(return_value=(False, {}))

    import dataclasses

    from grace2_agent.tools import TOOL_REGISTRY

    body = MagicMock(side_effect=AssertionError("tool body must never run"))

    contents_per_turn, _sock, state = await _drive_loop_real_invoke(
        [
            [_make_fake_chunk_with_function_call(tool, {"dem_uri": "gs://x/dem.tif"})],
            [_make_fake_chunk_with_text("Cancelled.")],
        ],
        extra_patches=[
            patch.object(agent_server, "_maybe_gate_on_payload_warning", gate),
            patch.dict(
                TOOL_REGISTRY,
                {tool: dataclasses.replace(TOOL_REGISTRY[tool], fn=body)},
            ),
        ],
    )

    assert gate.await_count == 1
    assert gate.await_args.args[2] == tool
    body.assert_not_called()
    assert tool in state.allowed_tool_set.as_frozenset()
    payloads = _function_response_payloads(contents_per_turn)
    assert payloads
    _name, payload = payloads[0]
    assert payload.get("status") == "error"
    assert payload.get("error_code") == "PAYLOAD_WARNING_CANCELLED"


# ---------------------------------------------------------------------------
# 3. Circuit breaker still precedes validation (auto-widen cannot revive a
#    tripped tool)
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_circuit_breaker_precedes_auto_widen() -> None:
    """A tripped breaker short-circuits BEFORE allowed-set validation — the
    auto-widen path must not give a repeatedly failing non-hot-set tool a
    fresh dispatch."""
    import dataclasses

    from grace2_agent.tools import TOOL_REGISTRY

    tool = "compute_colored_relief"
    body = MagicMock(side_effect=AssertionError("tripped tool must never run"))

    def _trip(state) -> None:
        for _ in range(state.circuit_breaker.threshold):
            state.circuit_breaker.record_failure(tool)
        assert state.circuit_breaker.is_tripped(tool)

    contents_per_turn, _sock, state = await _drive_loop_real_invoke(
        [
            [_make_fake_chunk_with_function_call(tool, {"dem_uri": "gs://x/dem.tif"})],
            [_make_fake_chunk_with_text("Breaker open.")],
        ],
        extra_patches=[
            patch.dict(
                TOOL_REGISTRY,
                {tool: dataclasses.replace(TOOL_REGISTRY[tool], fn=body)},
            )
        ],
        state_setup=_trip,
    )

    body.assert_not_called()
    payloads = _function_response_payloads(contents_per_turn)
    assert payloads
    _name, payload = payloads[0]
    assert payload.get("status") == "error"
    assert payload.get("error_code") == "CIRCUIT_BREAKER_TRIPPED"
    # Breaker fired BEFORE validate_function_call → no auto-widen happened.
    assert tool not in state.allowed_tool_set.explicit_tools


# ---------------------------------------------------------------------------
# 4. Hallucination guard + monotonicity (direct unit probes)
# ---------------------------------------------------------------------------


def test_non_registry_name_still_raises_and_does_not_pollute() -> None:
    from grace2_agent.categories import (
        AllowedToolSet,
        OutOfAllowedSetError,
        validate_function_call,
    )

    allowed = AllowedToolSet()
    with pytest.raises(OutOfAllowedSetError):
        validate_function_call("totally_fake_tool_xyz_0270", allowed)
    assert "totally_fake_tool_xyz_0270" not in allowed.as_frozenset()
    assert "totally_fake_tool_xyz_0270" not in allowed.explicit_tools


def test_auto_widen_is_monotonic_and_idempotent() -> None:
    from grace2_agent.categories import AllowedToolSet, validate_function_call

    allowed = AllowedToolSet()
    assert "compute_colored_relief" not in allowed.as_frozenset()
    validate_function_call("compute_colored_relief", allowed)  # widen
    snap1 = allowed.as_frozenset()
    assert "compute_colored_relief" in snap1
    validate_function_call("compute_colored_relief", allowed)  # member fast-path
    snap2 = allowed.as_frozenset()
    assert snap1 == snap2  # no churn, still present — monotonic for session
