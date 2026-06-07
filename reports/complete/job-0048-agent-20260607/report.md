# Report: FR-FR-3 MAX_TURNS_PER_SESSION cap (small)

**Job ID:** job-0048-agent-20260607
**Sprint:** sprint-08
**Specialist:** agent
**Task:** Pin MAX_TURNS_PER_SESSION=25 env-var-overridable; session turn counter; on 26th turn emit session-state.status="max_turns_reached" + closing message + refuse further tool calls; new session starts fresh. >=3 tests.
**Status:** ready-for-audit

## Summary

Landed FR-FR-3. MAX_TURNS_PER_SESSION=25 constant in main.py (GRACE2_MAX_TURNS_PER_SESSION env-var override). turn_count field added to SessionState in server.py; increments on every user-message before dispatch. When turn_count > MAX_TURNS_PER_SESSION, _handle_max_turns_reached emits session-state(status="max_turns_reached") + closing agent-message-chunk, then skips dispatch. SessionStateStatus = Literal["active","max_turns_reached"] + status field added to SessionStatePayload in grace2_contracts/ws.py (additive, default "active"). 11 new tests; full agent suite 130/130 green.

## Changes Made

- packages/contracts/src/grace2_contracts/ws.py (EDIT additive)
  - Added SessionStateStatus = Literal["active", "max_turns_reached"] with FR-FR-3 docstring
  - Added status: SessionStateStatus = "active" field to SessionStatePayload
  - Added "SessionStateStatus" to __all__

- services/agent/src/grace2_agent/main.py (EDIT additive)
  - Added MAX_TURNS_PER_SESSION: int = int(os.environ.get("GRACE2_MAX_TURNS_PER_SESSION", "25")) with FR-FR-3 / OQ-FR-1 inline docs

- services/agent/src/grace2_agent/server.py (EDIT minimal additive)
  - Added from .main import MAX_TURNS_PER_SESSION import
  - Added turn_count: int = 0 field to SessionState dataclass
  - Added _handle_max_turns_reached(websocket, state) async helper: emits session-state(status="max_turns_reached") + closing agent-message-chunk (delta + terminal done=True)
  - Extended user-message dispatch: increments state.turn_count before dispatch; if MAX_TURNS_PER_SESSION > 0 and turn_count > MAX_TURNS_PER_SESSION, calls _handle_max_turns_reached and continues (skipping Gemini + /invoke)

- services/agent/tests/test_max_turns_cap.py (NEW 11 tests)
  - test_turn_counter_starts_at_zero
  - test_turn_counter_increments_on_each_dispatch
  - test_cap_fires_and_emits_max_turns_reached
  - test_cap_fires_and_emits_closing_agent_message
  - test_cap_refuses_further_tool_calls_after_hitting_limit
  - test_new_session_starts_fresh_counter
  - test_multiple_sessions_have_independent_counters
  - test_max_turns_env_var_default
  - test_session_state_payload_active_status_default
  - test_session_state_payload_max_turns_reached_status
  - test_session_state_payload_rejects_unknown_status

## Decisions Made

- Decision: turn counter is per-SessionState (per-connection). Rationale: ADK internal counter is opaque; we need our own to emit the envelope per kickoff.
- Decision: cap check is turn_count > MAX_TURNS_PER_SESSION (strictly greater). Rationale: FR-FR-3 says "25+1th turn"; at turn 26 counter is 26 and 26 > 25 fires.
- Decision: MAX_TURNS_PER_SESSION=0 disables cap. Rationale: ops flexibility for demos. Surfaced as OQ-48-ZERO-DISABLES-CAP.
- Decision: status field added to SessionStatePayload as additive, no pushback to schema. Rationale: kickoff explicitly assigns contracts to this job for this exact additive extension. No version bump needed per pre-MVP additive rule.
- Decision: _handle_max_turns_reached does NOT emit pipeline-state. Rationale: no pipeline is being created or cancelled; a pipeline-state with no pipeline would be semantically incorrect.

## Invariants Touched

- Invariant 9 (No cost theater): preserves. Cap-hit path bypasses dispatch entirely; no cost fields.
- Invariant 8 (Cancellation is first-class): preserves. Turn increment and cap check happen before the new inflight_task is created; cancel on prior tasks propagates through existing chain untouched.
- Invariant 1 (Determinism boundary): preserves. Closing message text is a static template; no LLM generation.

## Open Questions

- OQ-48-ZERO-DISABLES-CAP (TENTATIVE: zero disables cap). Useful for long demos; alternative is hard error at startup. Routes to: nobody now.
- OQ-48-SCHEMA-ADDITIVE (TENTATIVE: additive, no pushback). Surface if schema specialist finds issue. Routes to: schema.
- OQ-48-FR-FR-3-CLIENT-RENDERING. Web has no handler for status="max_turns_reached" yet; follow-up web job needed for banner/indicator. Routes to: web.
- OQ-FR-1 carry-forward (TENTATIVE: 25 turns). Default not empirically derived; revisit after production usage. Routes to: orchestrator.

## Dependencies and Impacts

- Depends on: job-0015 (SessionState + server.py base), job-0032 (main.py pattern), job-0035 (PipelineEmitter + _ensure_emitter)
- Affects: web (OQ-48-FR-FR-3-CLIENT-RENDERING — needs to render max_turns_reached); schema (SessionStatePayload now has status field; all additive)

## Verification

- Tests run:
  - services/agent/tests/test_max_turns_cap.py: 11 passed in 0.75s
  - Full agent suite: .venv-agent/bin/python -m pytest services/agent/tests/ -q: 130 passed, 4 warnings in 1.36s (0 regressions)
  - Contracts no-regression: 127 passed (2 pre-existing catalog failures from job-0045 concurrent sprint-08 job; not caused by this job)

- Live E2E evidence: QUALIFIED. _handle_max_turns_reached tested via FakeWebSocket harness exercising real server.py + grace2_contracts.ws Pydantic machinery (same pattern as job-0035 evidence harness). Outer _make_handler integration test would require running agent + Gemini credentials; not available. Marking qualified with this reason.

- FROZEN-paths check: edits scoped to packages/contracts/src/grace2_contracts/ws.py, services/agent/src/grace2_agent/main.py, services/agent/src/grace2_agent/server.py, services/agent/tests/test_max_turns_cap.py, reports/inflight/job-0048-agent-20260607/. NO edits to tools/**, workflows/**, pipeline_emitter.py, mcp.py, services/workers/**, infra/**, web/**, docs/srs/**, styles/**, reports/complete/**.

- Results: pass (qualified on live E2E per above). All 6 acceptance criteria satisfied.
