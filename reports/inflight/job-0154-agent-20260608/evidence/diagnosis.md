# Diagnosis: "I can't model this scenario" refusal

**Job:** job-0154-agent-20260608  
**Date:** 2026-06-08

## Root Cause (confirmed): A + B combined

### Root Cause A — No system prompt  
`adapter.py:stream_reply()` called `generate_content_stream()` with only `temperature=0.7` in the config. No `systemInstruction` was passed. Gemini had no guidance that it was a hazard-modeling assistant, and no direction to call tools. A natural-language flood-modeling request prompted free-text generation with a prose refusal.

### Root Cause B — No tool declarations  
`GenerateContentConfig.tools` was `None`. Gemini received the user's message with zero knowledge of any tools in the registry. `run_model_flood_scenario` existed in `TOOL_REGISTRY` and was registered correctly, but Gemini was never told about it. The LLM simply could not call it — it had no function catalog.

### Root Cause C (secondary) — Tool docstring lacked demo-phrase coverage  
`run_model_flood_scenario`'s "Use this when:" section described the tool in technical SRS terms but did not include the exact phrases a user types: "100-year design storm", "return period", "flood depth". Gemini uses the docstring as its routing signal; missing phrases reduce match confidence.

## What was NOT the root cause

- **Root Cause D (Gemini config):** `GRACE2_GEMINI_MODEL` + ADC credentials were set correctly in the demo env; the Gemini call itself succeeded and returned text.
- **Root Cause E (geocoding):** The geocoder was never reached because the tool dispatch never happened.

## Fix

Three files changed:

### 1. `services/agent/src/grace2_agent/adapter.py`

Added:
- `TextDeltaEvent` / `FunctionCallEvent` dataclasses — typed stream events.
- `SYSTEM_PROMPT` — focused system prompt telling Gemini it is a hazard-modeling assistant with specific rules: call `run_model_flood_scenario` for flood requests, never fabricate numbers.
- `build_tool_declarations()` — builds `FunctionDeclaration` objects from `TOOL_REGISTRY` using `from_callable_with_api_option("VERTEX_AI")`, with a docstring-based fallback for tools whose signatures have complex types (`tuple[float,...]`, pydantic models).
- `stream_events()` — new streaming function that:
  - passes `systemInstruction` + `tools` list to `generate_content_stream`
  - demultiplexes each chunk into `TextDeltaEvent` (text parts) or `FunctionCallEvent` (function_call parts)
  - disables `automaticFunctionCalling` (we dispatch manually through the registry)
- `stream_reply()` retained as a text-only shim that delegates to `stream_events`.

### 2. `services/agent/src/grace2_agent/server.py`

Updated `_stream_gemini_reply` to:
- Import and use `stream_events`, `FunctionCallEvent`, `TextDeltaEvent`, `SYSTEM_PROMPT`, `build_tool_declarations`
- Call `build_tool_declarations(TOOL_REGISTRY)` before the stream
- Iterate `stream_events(...)` instead of `stream_reply(...)`
- On `FunctionCallEvent`: log it and dispatch via `_invoke_tool_via_emitter(websocket, state, event.name, event.args)` — the existing registry + emitter path handles pipeline-state, session-state, and layer emission unchanged
- On `TextDeltaEvent`: wrap in `agent-message-chunk` as before

### 3. `services/agent/src/grace2_agent/workflows/model_flood_scenario.py`

Updated `run_model_flood_scenario` docstring "Use this when:" to include concrete user phrases:
- "model a flood scenario" / "simulate flood inundation" / "compute peak flood depth"
- "run a flood simulation" / "estimate flood extent"
- "100-year storm" / "25-year design storm" / "return period" / "ARI" / "flood risk"
- Named-location examples: Fort Myers, Houston, New Orleans

## Evidence

- `gemini_call_payload.json` — full payload shape (system prompt + tool catalog + user message) verified by running `build_tool_declarations(TOOL_REGISTRY)` in Python; 53 tools in catalog, `run_model_flood_scenario` confirmed present
- `gemini_response.json` — expected Gemini response shape (function_call part); verified by `test_stream_events_yields_function_call_event` which mocks exactly this shape
- `services/agent/tests/test_agent_routing.py` — 6 tests, all passing:
  1. `test_run_model_flood_scenario_in_registry` — confirms tool is registered
  2. `test_build_tool_declarations_includes_flood_workflow` — confirms declaration builder surfaces it
  3. `test_stream_events_yields_function_call_event` — confirms `FunctionCallEvent` is produced from a mocked Gemini function_call chunk
  4. `test_stream_events_yields_text_delta_event` — confirms `TextDeltaEvent` still works
  5. `test_system_prompt_mentions_flood_routing` — confirms `SYSTEM_PROMPT` names `run_model_flood_scenario`
  6. `test_run_model_flood_scenario_docstring_covers_user_intent` — confirms "100-year" is in docstring

## OQs surfaced

### OQ-0154-DECL-FALLBACK
Many tools with complex signatures (`tuple[float, float, float, float] | None`, pydantic models as params or return types) fall back to description-only declarations (no parameter schema). Gemini receives the docstring text but no machine-readable argument schema. This means Gemini must infer argument names from the "Params:" section of the docstring rather than a structured schema. For `run_model_flood_scenario` this works well (`location_query`, `return_period_yr` are clearly described). For tools with more complex arg patterns it may produce wrong arg names.

**TENTATIVE resolution:** A follow-up job should add a `@simple_schema` decorator or hand-authored `schema: dict` on affected tool functions so `from_callable` succeeds without fallback. Priority: medium. Does not block the immediate demo fix.

### OQ-0154-MULTI-TURN-FUNCTION-CALL
The current fix is single-shot: Gemini emits one `FunctionCallEvent` per turn, the tool is dispatched, and the result is NOT fed back to Gemini as a `FunctionResponse`. In a multi-turn design, Gemini would receive the tool result and generate a narration turn. This means Gemini cannot chain tools or narrate the result in the same turn.

**TENTATIVE resolution:** For the Fort Myers demo this is acceptable — the `PipelineEmitter` emits `pipeline-state` + `session-state` as side effects, and the user sees the flood layer appear on the map. Narration of tool results requires a follow-up job that adds the function-response turn to `contents` and calls `generate_content` again after dispatch. Priority: medium.

### OQ-0154-SYSTEM-PROMPT-TUNING
`SYSTEM_PROMPT` is a v0.1 starting point. It may need iteration based on real Gemini behavior (hallucination patterns, over-calling, under-calling). Recommend a quick live test once credentials are available.
