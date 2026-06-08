# Audit: Payload-warning dev injection seam + small follow-ups

**Job ID:** job-0140-web-20260608, **Sprint:** sprint-12-mega Wave 3.5, **Specialist:** web (SMALL Sonnet-tier)

**Required reads:**
- `web/src/App.tsx:365-384` (existing `__grace2Inject*` dev seams)
- `web/src/components/PayloadWarningInline.tsx` (Wave 2 job-0127)

### Why

OQ-PAY-NO-PAYLOAD-WARNING-SEAM from Playwright capture agent: `PayloadWarningInline` lacks a dev injection seam analogous to `__grace2InjectSecretsList` / `__grace2InjectMode2Candidate`. ~3 lines to add.

### Scope

1. ADD `window.__grace2InjectPayloadWarning(payload)` dev seam in `App.tsx` (near the other inject seams ~line 376) — accepts a `PayloadWarningEnvelopePayload` shape, sets the local state that renders `PayloadWarningInline`
2. Update test to verify the seam works
3. While here, also surface OQ-PAY-NO-EVIDENCE-MRMS: leave alone for now (engine job-0141 handles it)

**Tests** (Vitest):
- Calling `__grace2InjectPayloadWarning(payload)` renders PayloadWarningInline with payload data
- Inline component shows estimated_mb, threshold, recommendation, 3 action buttons
- Clicking "Proceed" emits tool-payload-confirmation envelope

**Live verification** (Playwright — quick): inject a fake warning + screenshot the inline card.

### File ownership (exclusive)

- `web/src/App.tsx` — add seam (~10 lines)
- `web/src/App.test.tsx` — extend
- `reports/inflight/job-0140-web-20260608/`


### FROZEN

All files outside the explicit file-ownership list. Especially: every sibling Wave 3/3.5 job's exclusive files; `reports/complete/**`.

### Codified lessons (do NOT violate)

1. Geographic-correctness gate (job-0086): pixel-level evidence required.
2. Kickoff-front-loaded design: execute scope, surface OQs, don't redesign.
3. MongoDB MCP persistence (job-0115): use Persistence.* — no custom CRUD.

### Acceptance criteria

- [ ] Deliverables landed per scope
- [ ] Live verification per kickoff
- [ ] No FROZEN edits; single commit prefix `<job-id>:`; co-author line
- [ ] Returns commit SHA + outcome + 1-paragraph headline + evidence + OQs

