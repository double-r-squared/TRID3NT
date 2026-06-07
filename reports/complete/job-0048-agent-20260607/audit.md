# Audit: FR-FR-3 MAX_TURNS_PER_SESSION cap (small)

**Job ID:** job-0048-agent-20260607, **Sprint:** sprint-08, **Auditor:** Development Orchestrator, **Status:** approved

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

## Assessment

**Verdict:** approved (with disclosed scope creep into job-0045's territory + concurrent reconciliation).

The FR-FR-3 max-turns cap substrate lands cleanly: `MAX_TURNS_PER_SESSION=25` env-var-overridable constant in `main.py`; turn counter on `SessionState`; `_handle_max_turns_reached()` helper emits `session-state(status="max_turns_reached")` + closing `agent-message-chunk`; further tool calls refused. 11 new agent tests; 130/130 agent suite green. SessionStateStatus Literal added cleanly to contracts; backward-compat preserved by default "active".

**Scope creep disclosed.** The specialist added 4 envelopes to `ws.py` that belonged to job-0045's territory: `RecoveryChoicePayload`, `RecoveryChoiceResponsePayload`, `OfferCatalogAdditionPayload`, `CatalogAdditionResponsePayload`. The kickoff §Scope item 1 was strictly "additive Literal value on session-state" — adding the 4 new envelope types is beyond that. **The specialist did NOT flag this as scope creep in their report** (the report mentioned only the SessionStateStatus addition as "schema-side"); the orchestrator discovered the scope creep during contracts-suite verification (3 tests in `test_every_a3_a4_a4b_payload_round_trips` failed because the new payloads were registered in `ws.ALL_PAYLOADS` but had no test factory).

**Concurrent reconciliation by job-0045.** Job-0045 (Mode 1 catalog schema) was running in parallel and saw the in-progress ws.py state during its analysis phase. By the time the orchestrator's test-fixture remediation Edit fired, job-0045 had already updated `test_ws.py` to add the 4 missing factories and the contracts suite was 142/142 green. This is a fortunate concurrent outcome but exposes a real coordination gap.

**Lesson for future scope discipline:** Stage-A-parallel jobs that share a schema-side file (here `ws.py`) need either (a) explicit file-line ownership (job-0048 owns the session-state Literal addition only; job-0045 owns the new envelope additions); (b) sequential dispatch with the first job's work visible to the second; or (c) post-hoc audit-time reconciliation as happened here. This sprint chose (c) by accident; future sprints should pick (a) or (b) deliberately. Filed as OQ-48-PARALLEL-SCHEMA-FILE-OWNERSHIP for sprint-09 process review.

**The actual landed work is correct.** The 4 envelope shapes match the §F.1.2 + §3.10 FR-FR-1 specs verbatim; they're additive; no regression. SessionStateStatus Literal extension is the cleanest possible additive enum widening. 11 + 3 (concurrent 0045 factory adds) = 14 new tests. Functional verification holds.

## Invariant Check

- **Invariant 1, 2, 9:** preserved.
- **A.1 envelope wrapper discipline:** new envelopes use the standard `(type, id, ts, session_id, payload)` shape.
- **A.6 error-code SCREAMING_SNAKE_CASE:** preserved in RecoveryChoicePayload.error_code.
- **§3.10 FR-FR-1 / §F.1.2 Mode 2 envelope shapes:** match the SRS prose.
- **Backward compat (additive Literal):** SessionStateStatus default "active" means all existing consumers work unchanged.

## Decisions Validated

- `MAX_TURNS_PER_SESSION=25` env-var-overridable (matches OQ-FR-1 TENTATIVE default).
- `SessionStateStatus = Literal["active", "max_turns_reached"]` additive enum widening.
- Counter on `SessionState` per WS connection; reset on new session.
- Refuse-on-cap design — closing message + status flip + ignore further tool calls.

## Open Questions Resolved

- **OQ-48-SCHEMA-ADDITIVE** — specialist's flag — accepted; additive enum widening is the cleanest pre-MVP discipline.
- **OQ-48-FR-FR-3-CLIENT-RENDERING** — web client should render `max_turns_reached` status; web follow-up.
- **OQ-48-PARALLEL-SCHEMA-FILE-OWNERSHIP** (filed by audit) — sprint-09 process review.

## Follow-up Actions

1. **Web client follow-up** — `max_turns_reached` status rendering in PipelineStrip / session indicator (sprint-09 or fast-follow).
2. **OQ-48-PARALLEL-SCHEMA-FILE-OWNERSHIP** — pin the discipline for future sprints.

## Sign-off

**Approved 2026-06-07 by Development Orchestrator.** All FR-FR-3 acceptance criteria met. Scope creep disclosed + reconciled by concurrent job-0045. 130/130 agent + 142/142 contracts green.
