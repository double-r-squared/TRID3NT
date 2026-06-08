# Audit: Case UX agent backend — per-Case `.qgs` lazy-init + Persistence wiring

**Job ID:** job-0121-agent-20260608, **Sprint:** sprint-12-mega Wave 2, **Specialist:** agent

**Required reads:**
- `services/agent/src/grace2_agent/persistence.py` (Wave 1.5 — Persistence module)
- `packages/contracts/src/grace2_contracts/case.py` (Wave 1 — Case envelopes)
- `services/agent/src/grace2_agent/server.py` (existing M1 substrate)
- `services/agent/src/grace2_agent/main.py` (entry point)
- OQ-62-QGS-MUTATION-CONFLICT (per-Case .qgs isolation question)

### Scope

Wire the Case UX backend behind the existing WebSocket server. Key responsibilities:

1. **Case lifecycle handlers** (server.py additions):
   - `case-command(create)` → `Persistence.upsert_case(new_case)` → emit `case-open` with empty session_state
   - `case-command(select)` → `Persistence.get_session_state(case_id)` → emit `case-open` with full rehydration
   - `case-command(rename)` → `Persistence.upsert_case(updated)` → emit `case-list` updated
   - `case-command(archive)` → `Persistence.archive_case(case_id)` → emit `case-list` updated
   - `case-command(delete)` → `Persistence.delete_case(case_id)` → emit `case-list` updated (memory rule: user-confirmation gate handled by web UI before this fires)

2. **Per-Case `.qgs` lazy-init** (resolves OQ-62-QGS-MUTATION-CONFLICT):
   - When the FIRST raster/vector layer publishes for a Case (publish_layer atomic tool call inside an in-Case context):
     - Compute case-scoped path: `gs://grace-2-qgis-projects/{case_id}.qgs`
     - Copy the template `.qgs` to that path
     - Update `Case.qgs_project_uri` in MongoDB via Persistence
   - Subsequent publishes route to the case-specific `.qgs`, NOT the shared one
   - The publish_layer atomic tool needs to accept an optional `case_id` parameter to know which `.qgs` to mutate; sprint-12 work assumes single-tenant for now (existing tests pass), Case isolation kicks in only when case_id is set

3. **Chat persistence**:
   - Every user message + agent reply appended to `sessions` collection via `Persistence.append_chat_message(CaseChatMessage(...))`
   - Pipeline emissions and layer additions captured in the same message record (`layer_emissions`, `pipeline_id` fields)
   - On case-open: full chat history rehydrated from MongoDB and emitted in `case-open` envelope (chat-replay default per user 2026-06-08)

4. **Active-case context**:
   - Server tracks per-connection `active_case_id` (None for fresh sessions)
   - When set, all tool invocations carry the case context for `.qgs` routing
   - `case-command(create|select)` updates this context

**Tests** (≥10 unit + 1 integration):
- Case lifecycle handlers: create/select/rename/archive/delete each produce correct envelopes
- Lazy-init: first publish in a Case copies template `.qgs`; second publish doesn't re-copy
- Chat persistence: messages append to MongoDB; rehydration loads correct order
- Active-case context: tool calls in-Case have case_id passed; out-of-case do not
- Integration: e2e simulation of (create Case → publish layer → message exchange → select different Case → verify isolation)

### File ownership (exclusive)

- `services/agent/src/grace2_agent/server.py` — add case-command handlers + active-case context (~150 lines)
- `services/agent/src/grace2_agent/case_lifecycle.py` (NEW — extract lazy-init logic into a focused module)
- `services/agent/src/grace2_agent/tools/publish_layer.py` — ADD optional `case_id` parameter (additive; default None preserves existing behavior)
- `services/agent/tests/test_case_lifecycle.py` (NEW)
- `services/agent/tests/test_server_case_handlers.py` (NEW)
- `reports/inflight/job-0121-agent-20260608/`


### FROZEN

All files outside the explicit file-ownership list. Especially: every sibling Wave 2 job's exclusive files; `reports/complete/**`; `docs/SRS_v0.3.md` monolith (regenerated only); all Wave 1/1.5 atomic tool files (additive use only — don't modify their signatures).

### Concurrency note (Wave 2 fan-out — 16 parallel)

Same idempotent-append pattern + `git pull --rebase` pre-commit mitigation as Wave 1.5. Files all land correctly in HEAD; only commit-message labels may drift. Use marker commits if your changes get swept into a sibling's commit hash.

### Codified lessons (do NOT violate)

1. **Geographic-correctness gate (job-0086)**: verify against real geography, not URL/render consistency.
2. **Kickoff-front-loaded design**: orchestrator did the design — execute, don't redesign. Surface OQs in your report rather than expanding scope.
3. **MongoDB MCP canonical persistence (job-0115 foundation)**: ALL CRUD goes through `Persistence.*`. Do NOT design custom collection wrappers. If your job needs a new method on Persistence, ADD it (additive) rather than bypassing.

### Acceptance criteria

- [ ] All deliverables landed per scope
- [ ] ≥4 unit tests + ≥1 live test (env-guarded if external)
- [ ] Geographic-correctness / behavioral-correctness verified
- [ ] No FROZEN edits; single commit prefix `<job-id>:`; co-author line
- [ ] Returns commit SHA + outcome + 1-paragraph headline + evidence + OQs

