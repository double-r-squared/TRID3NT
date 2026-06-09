# Report: Agent diagnostic — "I can't model this scenario" investigation and fix

**Job ID:** job-0154-agent-20260608
**Sprint:** sprint-12-mega Wave 4.5
**Specialist:** agent
**Task:** Diagnose and fix agent refusal on "Model peak flood depth from a 100-year design storm in Fort Myers, FL". Verify Gemini calls run_model_flood_scenario instead of refusing.
**Status:** ready-for-audit

## Summary

Root cause confirmed as two combined failures: (A) no system prompt was passed to Gemini, and (B) no tool declarations were passed. `adapter.py:stream_reply()` called `generate_content_stream()` with only `temperature=0.7` — Gemini received a raw user message with no function catalog and no guidance, so it produced a prose refusal. The fix adds `stream_events()` to the adapter (typed event stream: `TextDeltaEvent` | `FunctionCallEvent`), a focused `SYSTEM_PROMPT`, a `build_tool_declarations()` builder, and updates `_stream_gemini_reply` in `server.py` to dispatch `FunctionCallEvent` through `_invoke_tool_via_emitter`. The `run_model_flood_scenario` docstring "Use this when:" section was also improved to include demo-relevant phrases. Full test suite: 1050 passed, 49 skipped, 0 failures.

## Changes Made

- **File:** `services/agent/src/grace2_agent/adapter.py`
  - Added `TextDeltaEvent` and `FunctionCallEvent` dataclasses as typed stream events.
  - Added `SYSTEM_PROMPT` — focused system instruction telling Gemini it is a hazard-modeling assistant, naming `run_model_flood_scenario` explicitly as the tool for flood modeling requests.
  - Added `build_tool_declarations(tool_registry)` — builds `FunctionDeclaration` objects from `TOOL_REGISTRY` using `FunctionDeclaration.from_callable_with_api_option("VERTEX_AI")`; falls back to docstring-only declarations (up to 1000 chars) for tools with complex signatures (most tools hit the fallback due to `tuple[float, float, float, float] | None` and pydantic model params — see OQ-0154-DECL-FALLBACK).
  - Added `stream_events()` — new streaming coroutine that passes `systemInstruction` + `tools` to `generate_content_stream`, demultiplexes chunk parts into `TextDeltaEvent` / `FunctionCallEvent`, and disables `automaticFunctionCalling`.
  - `stream_reply()` retained as a text-only shim for backward compatibility.

- **File:** `services/agent/src/grace2_agent/server.py`
  - Updated imports to include `FunctionCallEvent`, `TextDeltaEvent`, `SYSTEM_PROMPT`, `build_tool_declarations`, `stream_events`.
  - Updated `_stream_gemini_reply` to call `build_tool_declarations(TOOL_REGISTRY)` before the stream and iterate `stream_events(...)` instead of `stream_reply(...)`.
  - `TextDeltaEvent` → `agent-message-chunk` (unchanged behavior).
  - `FunctionCallEvent` → logged + dispatched via `_invoke_tool_via_emitter(websocket, state, event.name, event.args)` (existing registry + emitter path).

- **File:** `services/agent/src/grace2_agent/workflows/model_flood_scenario.py`
  - Updated `run_model_flood_scenario` docstring "Use this when:" to include natural-language phrases: "model a flood scenario", "100-year storm", "25-year design storm", "return period", "ARI", "flood risk", with named-location examples.

- **File:** `services/agent/tests/test_agent_routing.py` (NEW)
  - 6 tests confirming the full dispatch path: registry, declaration builder, stream_events FunctionCallEvent, stream_events TextDeltaEvent, SYSTEM_PROMPT content, docstring "100-year" coverage.

## Decisions Made

- **Decision:** Use docstring-based fallback (1000 chars) for tools whose signatures can't be auto-parsed.
  - **Rationale:** 42 of 53 tools fail `from_callable_with_api_option` due to `tuple` and pydantic params; hand-crafting schemas for all is out of scope for a diagnostic fix. Tracked as OQ-0154-DECL-FALLBACK.
  - **Alternatives considered:** (1) Hand-craft `FunctionDeclaration` for `run_model_flood_scenario` only — too narrow; (2) Add `@simple_schema` project-wide — correct long-term, out of scope here.

- **Decision:** Single-shot function call (no function-response turn fed back to Gemini).
  - **Rationale:** The `PipelineEmitter` handles pipeline-state + session-state side effects; the user sees the flood layer appear on the map. Multi-turn is a follow-up job.

## Invariants Touched

- Invariant 2 (Deterministic workflows): preserves — LLM's FunctionCallEvent.name IS the classification; no pre-pass.
- Invariant 1 (Determinism boundary): preserves — no narrated numbers from Gemini in this dispatch path.
- Invariant 8 (Cancellation): preserves — CancelledError propagates unchanged.
- Invariant 9 (Confirmation before consequence): preserves — _invoke_tool_via_emitter path unchanged.
- Invariant 10 (Minimal parameter surface): preserves — run_model_flood_scenario accepts intent + irreducible inputs only.

## Open Questions

- **OQ-0154-DECL-FALLBACK:** 42/53 tools fall back to docstring-only declarations (no parameter schema). Follow-up job should add `@simple_schema` or hand-authored `schema: dict` on affected tools. **TENTATIVE: defer to follow-up job. Priority: medium.**

- **OQ-0154-MULTI-TURN-FUNCTION-CALL:** Tool results are not fed back to Gemini as `FunctionResponse` turns. Full multi-turn requires follow-up job. **TENTATIVE: defer. Priority: medium.**

- **OQ-0154-SYSTEM-PROMPT-TUNING:** SYSTEM_PROMPT is v0.1; needs live tuning once credentials available. **Priority: low.**

## Dependencies and Impacts

- Depends on: job-0042 (model_flood_scenario registration), job-0035 (PipelineEmitter), job-0032 (TOOL_REGISTRY)
- Affects: all `user-message` dispatches now use `stream_events` instead of `stream_reply`; text-only prompts still work via `TextDeltaEvent`; `/invoke` directive path is unaffected.

## Verification

- Tests run: `test_agent_routing.py` — 6/6 pass; full suite — 1050 passed, 49 skipped, 0 failures
- Live E2E evidence: **qualified** — Vertex AI credentials unavailable in CI. Mocked evidence in `evidence/gemini_response.json` demonstrates the dispatch path; `test_stream_events_yields_function_call_event` replays the exact Gemini function_call response shape and confirms `FunctionCallEvent(name="run_model_flood_scenario", args={"location_query": "Fort Myers, FL", "return_period_yr": 100})` is produced and routed correctly.
- Evidence files: `evidence/gemini_call_payload.json`, `evidence/gemini_response.json`, `evidence/diagnosis.md`
- Results: pass (qualified)
