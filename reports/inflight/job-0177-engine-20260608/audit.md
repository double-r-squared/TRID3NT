# Audit: Tool retry on failure — feed errors back to agent loop

**Job ID:** job-0177-engine-20260608, **Specialist:** agent (Opus)

## Why

Per memory `feedback_tool_retry_on_failure`: when a tool fails, the agent should see the error and decide whether to retry (with corrected args or different tool) or narrate failure honestly. Currently the agent just stops on first failure.

## Scope

Extend job-0169's multi-turn loop (`stream_events_with_contents`):
- `_invoke_tool_via_emitter` already catches exceptions
- When an exception is caught, build a function_response with error payload:
  ```
  {status: "error", error_code: "...", message: "...", retryable: true|false}
  ```
- Append as a function_response Content to the running contents list
- Loop continues — Gemini reads the error and decides next step
- MAX_TURN_ITERATIONS=8 already caps runaway retry

## UI visibility — DEFERRED for v0.1 per user direction

Per memory: don't add explicit "Retry N of M" markers in the UI for v0.1. Each retry attempt just creates a new tool card (one becomes red, next one tries again). The CHAIN of cards itself visually shows the retry.

## Verify

LIVE: send a prompt that should trigger a recoverable failure (e.g. invented but plausible tool call). Verify:
1. First tool dispatch fails → card turns red
2. Agent automatically retries → new tool card appears
3. Either retry succeeds (green) or agent narrates honest failure

Edge case: agent's automatic retry shouldn't loop forever — MAX_TURN_ITERATIONS caps at 8.

## File ownership
- `services/agent/src/grace2_agent/server.py` (function_response with error)
- `services/agent/src/grace2_agent/adapter.py` (no changes if already in loop)
- Tests
- `reports/inflight/job-0177-engine-20260608/`

## FROZEN
Single commit prefix `job-0177:`. Codified lessons.
