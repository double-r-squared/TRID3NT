# Audit: Tool payload-warning system (schema + agent + web cross-cut)

**Job ID:** job-0127-schema-20260608, **Sprint:** sprint-12-mega Wave 2, **Specialist:** schema (lead — cross-cuts agent + web)

**Required reads:**
- Memory: `feedback_large_payload_chat_warning`
- Wave 1.5 job-0114 (AtomicToolMetadata.estimate_payload_mb)
- `services/agent/src/grace2_agent/server.py` (where tool calls dispatch)

### Scope

Implement the chat payload-warning system end-to-end.

**Part A — envelopes** (this is the schema-lead piece):
NEW file `packages/contracts/src/grace2_contracts/payload_warning.py`:
```python
class PayloadWarningEnvelopePayload(GraceModel):
    envelope_type: Literal["tool-payload-warning"] = "tool-payload-warning"
    warning_id: ULIDStr
    tool_name: str
    tool_args: dict
    estimated_mb: float
    threshold_mb: float
    recommendation: str  # e.g. "Consider narrowing bbox" or "Filter to fewer features"
    alternative_args: dict | None = None  # what the agent suggests narrowing

class PayloadConfirmationEnvelopePayload(GraceModel):
    envelope_type: Literal["tool-payload-confirmation"] = "tool-payload-confirmation"
    warning_id: ULIDStr
    decision: Literal["proceed", "cancel", "narrow_scope"]
    revised_args: dict | None = None  # if decision=narrow_scope
```
Register in ws.py CLIENT_TO_AGENT_PAYLOADS / AGENT_TO_CLIENT_PAYLOADS / ALL_PAYLOADS.

**Part B — agent main-loop integration** (also in this job):
Modify `services/agent/src/grace2_agent/server.py` `_invoke_tool_via_emitter`:
- Before tool dispatch, call `tool_metadata.estimate_payload_mb(**args)` (if metadata has the estimator)
- If >25MB threshold (configurable via env GRACE2_PAYLOAD_WARNING_MB, default 25): emit `tool-payload-warning` envelope, pause coroutine awaiting `tool-payload-confirmation` envelope from client (matching warning_id), then proceed/cancel/narrow based on response
- If >250MB (configurable hard cap): hard-block without confirmation override OR require explicit narrow_scope decision
- Append `audit_log` event for every warning + decision

**Part C — web UI consumption** (also this job — small):
- `web/src/ws.ts`: listen for tool-payload-warning; emit tool-payload-confirmation
- `web/src/components/PayloadWarningInline.tsx` (NEW): inline chat card showing warning + [Proceed] [Cancel] [Narrow scope] buttons
- "Narrow scope" opens a small clarifier dialog editing the most likely arg (bbox, max_records, etc.)

**Tests** (≥10 unit across all 3 parts + 1 integration):
- Envelope contracts round-trip
- Agent main-loop: small payload (1MB) → no warning, direct dispatch
- Medium payload (50MB) → warning emitted, agent pauses
- Confirmation:proceed → dispatch fires after delay
- Confirmation:cancel → dispatch skipped, error returned to chat
- Confirmation:narrow_scope → revised_args used in dispatch
- Hard cap (>250MB) → no proceed option, only cancel/narrow
- Audit log captures decisions
- PayloadWarningInline renders correctly
- Integration: end-to-end via Playwright

### File ownership (exclusive)

- `packages/contracts/src/grace2_contracts/payload_warning.py` (NEW)
- `packages/contracts/src/grace2_contracts/__init__.py` — export
- `packages/contracts/src/grace2_contracts/ws.py` — register payloads
- `packages/contracts/tests/test_payload_warning.py` (NEW)
- `services/agent/src/grace2_agent/server.py` — _invoke_tool_via_emitter extension (~80 lines)
- `services/agent/tests/test_payload_warning_flow.py` (NEW)
- `web/src/ws.ts` — listener (~20 lines)
- `web/src/components/PayloadWarningInline.tsx` (NEW)
- `web/src/PayloadWarningInline.test.tsx` (NEW)
- `web/src/App.tsx` — render PayloadWarningInline in chat (~15 lines)
- `reports/inflight/job-0127-schema-20260608/`


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

