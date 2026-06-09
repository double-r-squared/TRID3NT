# Audit: Agent diagnostic — "I can't model this scenario" investigation

**Job ID:** job-0154-agent-20260608, **Sprint:** sprint-12-mega Wave 4.5, **Specialist:** agent (Sonnet diagnostic)

**Required reads:**
- `services/agent/src/grace2_agent/server.py` (system prompt + tool catalog routing)
- `services/agent/src/grace2_agent/workflows/model_flood_scenario.py` (target workflow)
- `services/agent/src/grace2_agent/tools/__init__.py` (TOOL_REGISTRY)
- `services/agent/src/grace2_agent/main.py` (entry + ADK wiring)

### Why

User tested localhost demo with prompt `"Model peak flood depth from a 100-year design storm in Fort Myers, FL"`. Agent responded "I can't model that scenario" (or similar refusal). User cancelled successfully (confirms chat input works).

This is a critical bug — without successful tool dispatch, no demo works end-to-end.

### Scope — DIAGNOSTIC + FIX

#### Part 1 — Investigate

Read agent code + system prompt + tool registration + ADK call path. Trace what happens when user sends a Fort Myers flood prompt:
1. Server receives prompt envelope
2. Agent passes to Gemini with available tools
3. Gemini decides whether to call `run_model_flood_scenario` or decline
4. If decline: what does the system prompt look like? Does it tell Gemini about the workflow? Does it explicitly LIMIT what Gemini can do?

Likely root causes (rank in your investigation):
- A. **System prompt is too restrictive** — tells Gemini not to model things or only respond when given exact bbox/parameters
- B. **Workflow not in catalog** — `run_model_flood_scenario` exists but isn't surfaced to Gemini's tool list
- C. **Tool description ambiguity** — `run_model_flood_scenario` description doesn't mention Fort Myers / flood / hurricane / 100-year storm in a way Gemini matches
- D. **Gemini missing API key / config** — if Gemini isn't loaded, agent might fall back to a "can't do that" path
- E. **Geocoding fails silently** — Nominatim call for "Fort Myers, FL" fails → workflow exits early with refusal

#### Part 2 — Fix

Based on investigation:
- If A: amend system prompt to invite Gemini to dispatch model_flood_scenario when user asks about flooding
- If B: register the workflow as a Gemini-callable tool
- If C: improve docstring with example phrases
- If D: log + surface the config issue
- If E: better error handling + present to user

Land the most-conservative fix that resolves the immediate user-facing refusal. Surface other findings as OQ-0154-*.

#### Part 3 — Live verification

- Run a real Gemini call with the prompt "Model peak flood depth from a 100-year design storm in Fort Myers, FL"
- Confirm Gemini calls `run_model_flood_scenario(location_query="Fort Myers, FL", forcing="atlas14_100yr")`
- Workflow dispatches (even if SFINCS Cloud Run job times out — the key is Gemini actually CALLS the tool, not refuses)

**Tests**:
- Unit: any test you add for the fix
- Integration: mocked Gemini that confirms tool catalog includes run_model_flood_scenario

**Live verification**:
- Capture the full Gemini call payload (tool catalog + system prompt + user prompt) to `evidence/gemini_call_payload.json`
- Capture the Gemini response (tool call args) to `evidence/gemini_response.json`
- Document the root cause + fix in `evidence/diagnosis.md`

### File ownership (exclusive)

- `services/agent/src/grace2_agent/server.py` — system prompt or routing fix
- `services/agent/src/grace2_agent/workflows/model_flood_scenario.py` — docstring may need clarification
- `services/agent/src/grace2_agent/tools/__init__.py` — registration may need fix
- Any other agent-side file the fix touches
- `services/agent/tests/test_agent_routing.py` (NEW or extend)
- `reports/inflight/job-0154-agent-20260608/`


### FROZEN

All files outside the explicit file-ownership list. Sibling Wave 4.5 files; `reports/complete/**`.

### Codified lessons (do NOT violate)

1. Geographic-correctness gate (job-0086): pixel-level evidence.
2. Kickoff-front-loaded design: execute scope, surface OQs.
3. UX language discipline: no internal terms ("Mode 1/2", "Tier", "OQ-*") in user-facing surfaces.
4. Pre-commit: `git pull --rebase` before commit.

### Acceptance criteria

- [ ] Deliverables landed per scope
- [ ] Live verification per kickoff
- [ ] No FROZEN edits; single commit prefix; co-author line
- [ ] Returns commit SHA + outcome + headline + evidence + OQs

