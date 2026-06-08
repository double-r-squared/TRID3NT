# Audit: Mode 2 `.gov`/`.edu` offer-to-add modal

**Job ID:** job-0126-web-20260608, **Sprint:** sprint-12-mega Wave 2, **Specialist:** web

**Required reads:**
- `services/agent/src/grace2_agent/mode2_classifier.py` (Wave 1)
- `services/agent/src/grace2_agent/server.py` (envelope emission site)
- `web/src/ws.ts` + `web/src/App.tsx`

### Scope

Add the offer-to-add modal that consumes the Mode 2 candidate envelopes emitted by Wave 1's classifier.

1. **NEW `web/src/components/Mode2OfferModal.tsx`**:
   - Triggered when `mode2-candidate` envelope arrives
   - Shows: domain, snippet, detected patterns (chips), suggested_tool_kind, confidence
   - User actions: "Add to Mode 2 catalog" / "Maybe later" / "Don't ask again for this domain"
   - "Add" submits a new envelope `mode2-add-confirmed` (define in Wave 1.5 ws.py registry if not present — surface as OQ if missing)
2. **Auto-dismiss**: confidence <0.7 → silent toast notification instead of modal (less disruptive)
3. **Audit log entry**: every modal display + user action logged client-side + emitted to server for audit_log
4. **Style**: match existing modal patterns (subtle backdrop, rounded panel, dark-theme aware)

**Tests** (Vitest):
- Renders modal when high-confidence envelope arrives
- Renders toast for low-confidence envelopes
- "Add" button emits mode2-add-confirmed envelope
- "Don't ask again" adds domain to local-storage suppression list
- Audit event emitted on each user action

**Live verification** (Playwright):
- Inject a synthetic mode2-candidate envelope; verify modal renders with correct snippet + patterns
- Click "Add"; verify envelope captured

### File ownership (exclusive)

- `web/src/components/Mode2OfferModal.tsx` (NEW)
- `web/src/Mode2OfferModal.test.tsx` (NEW)
- `web/src/ws.ts` — listen for mode2-candidate (~15 lines)
- `web/src/App.tsx` — mount modal (~10 lines)
- `web/src/lib/mode2_suppression.ts` (NEW small — local-storage helper)
- `reports/inflight/job-0126-web-20260608/`


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

