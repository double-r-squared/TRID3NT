# Audit: FR-FR-3 MAX_TURNS_PER_SESSION cap (small)

**Job ID:** job-0048-agent-20260607, **Sprint:** sprint-08, **Auditor:** Development Orchestrator, **Status:** assigned

## Task Assignment

**Specialist:** agent

**Prerequisites:**
- v0.3.19 §3.10 FR-FR-3 (this job IS the §3.10 implementation)
- job-0030 (PipelineStepSummary + session-state shape patterns)

**SRS references** (narrow files only):
- `docs/srs/03-functional-requirements.md` — §3.10 FR-FR-3 (the binding contract)
- `docs/srs/A-websocket-protocol.md` — session-state envelope shape
- DO NOT load `docs/SRS_v0.3.md` monolith

### Scope (deliberately small — "cheap insurance" job)

1. **Pin `MAX_TURNS_PER_SESSION = 25`** (TENTATIVE default per OQ-FR-1) as a config constant in `services/agent/src/grace2_agent/main.py` (or wherever the ADK app is initialized). Make it env-var overridable via `GRACE2_MAX_TURNS_PER_SESSION` for ops flexibility.
2. **Session turn counter** — track turns at the agent service level (not delegated to ADK's internal counter — we want our own counter so we can emit our envelope). Increment on each user-message or tool-call cycle.
3. **On 25+1th turn**:
   - Emit a final `session-state` envelope with `status="max_turns_reached"` (NEW enum value — extend the existing session-state status enum)
   - Send a closing `agent-message-chunk` summarizing what's been done in this session
   - Refuse any further tool-call dispatches; subsequent user-messages get a "session has reached its turn limit; start a new session to continue" narration
4. **Session reset path**: a new WebSocket connection with a fresh session_id starts a new counter at 0. Don't auto-extend; user owns the decision.
5. **Tests** in `services/agent/tests/test_max_turns_cap.py` (NEW): 3 tests minimum — turn counter increments correctly; cap fires at 26th turn; new session starts fresh counter.

### File ownership (exclusive)
- `services/agent/src/grace2_agent/main.py` (or equivalent — extend with the cap + counter)
- `services/agent/src/grace2_agent/server.py` (extend with the per-turn increment + emission on cap hit) — minimal additive edit only, do NOT refactor the WS handling
- `packages/contracts/src/grace2_contracts/` — only if extending the session-state status enum (additive Literal value); routed to schema if needed
- `services/agent/tests/test_max_turns_cap.py` (NEW)
- `reports/inflight/job-0048-agent-20260607/`

### FROZEN
- `services/agent/src/grace2_agent/tools/**` (all M4/M5 tools)
- `services/agent/src/grace2_agent/workflows/**`
- `services/agent/src/grace2_agent/pipeline_emitter.py` (consume; don't modify)
- `services/agent/src/grace2_agent/mcp.py`
- `services/workers/**`, `infra/**`, `web/**`, `docs/srs/**`, `docs/SRS_v0.3.md`, `styles/**`, `reports/complete/**`
- Stage A concurrent jobs

### Acceptance criteria
- [ ] `MAX_TURNS_PER_SESSION` env-var-overridable constant pinned at 25
- [ ] Session turn counter increments correctly
- [ ] Cap fires; `session-state.status="max_turns_reached"` emitted; further tools refused
- [ ] New session starts fresh counter
- [ ] ≥3 new tests + agent suite still green
- [ ] No edits to FROZEN paths
