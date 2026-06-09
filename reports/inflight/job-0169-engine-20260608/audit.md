# Audit: Multi-turn function_call → function_response loop

**Job ID:** job-0169-engine-20260608, **Sprint:** sprint-12-mega Wave 4.8, **Specialist:** agent (Opus)

## Why (CRITICAL BLOCKER)

Every multi-tool natural-language prompt fails because Gemini stops after first tool call. User testing 2026-06-08:
- "Show me weather alerts across America" → only first call dispatched, no follow-up
- "Show me protected areas in Fort Myers" → geocode_location fired, then nothing
- Reported by OQ-0154-FUNCTION-RESPONSE-FEEDBACK (Opus diagnostic, ack'd as queued for Wave 5; now Wave 4.8 priority)

## Scope

Update `services/agent/src/grace2_agent/adapter.py` and `server.py`:
```
async loop:
  response = gemini.generate_content_stream(contents, tools=tool_decls)
  for chunk in response:
    if chunk has function_call:
      result = await dispatch_tool(name, args)  # via _invoke_tool_via_emitter
      contents.append({"role":"model", "parts":[{"function_call":{...}}]})
      contents.append({"role":"function", "parts":[{"function_response":{"name":name,"response":result_summary}}]})
    elif chunk has text:
      stream text to client
  if no function_call this iteration: break
```

The function_response should be a SUMMARY (LayerURI metadata, key metrics, error code) — not the full tool result (which can be huge).

## Verify

Live: "Show me protected areas in Fort Myers" → Gemini calls geocode → gets bbox → calls fetch_wdpa with bbox → layer published. End-to-end works.

Also enables prose narration: after run_model_flood_scenario returns, Gemini reads the metrics and narrates "Modeled Fort Myers with 100-year storm, max depth 1.2m, mean 0.4m".

## File ownership
- `services/agent/src/grace2_agent/adapter.py`
- `services/agent/src/grace2_agent/server.py` (_stream_gemini_reply, _invoke_tool_via_emitter)
- Tests
- `reports/inflight/job-0169-engine-20260608/`

## FROZEN
Tool files. Codified lessons. Single commit prefix `job-0169:`.
