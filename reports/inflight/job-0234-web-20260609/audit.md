# Audit: Sandbox result card + user-confirm gate web

**Job ID:** job-0234-web-20260609
**Sprint:** sprint-13 (Stage 2)
**Auditor:** Development Orchestrator
**Status:** assigned

## Task Assignment

(Kickoff frozen — verbatim from orchestrator prompt)

You are the web specialist. Job job-0234-web-20260609 — sandbox result card + user-confirm gate web (sprint-13 Stage 2).

### Scope (manifest job-0234)
1. web/src/components/SandboxCard.tsx (NEW): chat-inline card for a code-exec lifecycle: (a) REQUEST state — code block (monospace, scrollable, syntax-dim), rationale line, Proceed/Cancel buttons (PayloadWarning pattern: Cancel rightmost per the established button-order memory); (b) RUNNING state — same ephemeral treatment as other in-flight cards; (c) RESULT state — status chip (ok green / error red / timeout amber / blocked red), stdout tail collapsible, result rendering (scalar inline; dict as pretty JSON capped; chart results hand off to the existing chart-emission flow), truncated=true marker visible, Save button (download result JSON).
2. ws.ts: add code-exec-request + code-exec-result to the envelope union + SESSION_SCOPED_TYPES; onCodeExecRequest/onCodeExecResult handlers; App.tsx state + Chat interleave hook (same pattern as tool cards / impact-envelope / chart-emission — read how those interleave).
3. Confirm wiring: Proceed/Cancel sends the SAME confirmation reply envelope the PayloadWarning flow sends (read PayloadWarning.tsx + its ws reply shape; reuse, do not invent a new reply type).
4. Tests (vitest): request card renders code + gate buttons; Proceed emits the confirm reply; Cancel emits cancel; result states render per status; truncated marker shown; malformed payload dropped with console.warn.
5. Playwright screenshot (UI-only, dev seam PERMITTED per bundle-UI-verification memory): inject a request card + an ok result + a blocked result via a __grace2InjectCodeExec dev seam you add; capture chat showing the gate + results. Save to reports/inflight/<job-id>/evidence/. Do NOT drive Gemini — live verification is job-0238 (Stage 3).

### File ownership
web/src/components/SandboxCard.tsx + tests, ws.ts + App.tsx + chat-interleave surgical wiring, dev seam. NOTHING in services/.

## Assessment
(filled at audit)

## Invariant Check
(filled at audit)

## Dependency Check
- Prerequisites: job-0232 (sandbox substrate), job-0233 (sandbox contracts + confirm gate seam) — both ready-for-audit.
- Downstream: job-0238 (Python sandbox acceptance Playwright — Stage 3).

## Decisions Validated
(filled at audit)

## Open Questions Resolved
(filled at audit)

## Follow-up Actions
(filled at audit)

## Sign-off
- Ready to move to complete: pending audit
