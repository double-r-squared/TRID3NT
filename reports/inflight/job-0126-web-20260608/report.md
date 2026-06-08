# Report: Mode 2 `.gov`/`.edu` offer-to-add modal

**Job ID:** job-0126-web-20260608
**Sprint:** sprint-12-mega Wave 2
**Specialist:** web
**Task:** Add the offer-to-add modal that consumes the Mode 2 candidate envelopes emitted by Wave 1's classifier.
**Status:** ready-for-audit

## Summary

Landed the Mode 2 offer-to-add UI surface end to end: a new Mode2OfferModal component that renders a backdrop modal for high-confidence (>=0.7) mode2-candidate envelopes and a corner toast for low-confidence ones, with localStorage-backed "Don't ask again for this domain" suppression, plus ws.ts routing for inbound mode2-candidate envelopes and outbound mode2-add-confirmed + mode2-audit-event envelopes. The Wave 1 classifier (services/agent/src/grace2_agent/mode2_classifier.py) is now consumable client-side; the agent emitter at server.py:993 already produces the wire shape this surface reads.

## Changes Made

- File: web/src/lib/mode2_suppression.ts (NEW)
  - TS mirrors of the Wave 1 Mode2Candidate / Mode2CandidateEnvelope shapes (placed here rather than in contracts.ts because the canonical pydantic envelope is not yet registered — see OQ-0126-MODE2-ADD-CONFIRMED-SCHEMA).
  - Outbound Mode2AddConfirmedPayload + Mode2AuditEventPayload wire shapes.
  - localStorage-backed suppression helpers (isSuppressed, suppressDomain, unsuppressDomain, listSuppressed, clearSuppressions) with case-insensitive lowercase normalization. Privacy-mode-safe.

- File: web/src/components/Mode2OfferModal.tsx (NEW)
  - Confidence-routed surface: >=0.7 -> backdrop modal with snippet, pattern chips, suggested-kind chip, accent-by-TLD colored left border. <0.7 -> low-friction toast in the bottom-left with a 5s self-dismiss + an Add-to-catalog quick action.
  - Three primary modal actions emit Mode2OfferAction to the parent (add | dismiss | suppress). Suppress also writes to the localStorage list before bubbling up so a re-emit on the same domain is silently filtered.
  - Dedupes by candidate_id so a duplicate emission doesn't double-render.
  - Dark-theme aware via the existing rgba(20,20,25,0.96) panel palette.

- File: web/src/Mode2OfferModal.test.tsx (NEW, 12 tests)
  - Modal/toast routing for both confidence bands; Add emits and dismisses; Don't-ask-again adds to suppression list and silences re-emits; low-confidence emit on suppressed domain doesn't toast; Maybe-later dismisses without suppressing; toast Add path; suppression-helper unit tests (case-insensitive, idempotent, clearSuppressions); GraceWs send-path tests for mode2-add-confirmed + mode2-audit-event + onMode2Candidate dispatch.

- File: web/src/ws.ts (~30 lines additive)
  - Imports from ./lib/mode2_suppression; new optional onMode2Candidate handler; dispatch case in handleMessage; new sendMode2AddConfirmed and sendMode2AuditEvent methods.

- File: web/src/App.tsx (~30 lines additive, coexists with sibling job-0125 SecretsPanel)
  - Mounts <Mode2OfferModal .../> at the root with a stable subscribeMode2Candidate bus.
  - handleMode2Action bridges modal actions to wsRef.current.sendMode2AddConfirmed + sendMode2AuditEvent.
  - GraceWs handler onMode2Candidate routes inbound envelopes to the modal.
  - Dev-only seam window.__grace2InjectMode2Candidate so Playwright + browser-console verification needs no live agent.

- File: web/tools/screenshot_mode2_modal.mjs (NEW, evidence harness)
  - Drives Chromium against the dev server, injects three candidate envelopes (high-confidence modal, low-confidence toast, suppressed-domain re-emit), captures two screenshots, and asserts 7 verification predicates exit clean.

## Decisions Made

- Decision: Types live in lib/mode2_suppression.ts, not in contracts.ts.
  - Rationale: contracts.ts is the mirror of Appendix A and is FROZEN for this job. The Wave 1 classifier emits a Python dataclass envelope that has NOT been promoted to a pydantic model in packages/contracts; OQ-0126-MODE2-ADD-CONFIRMED-SCHEMA tracks the follow-up to relocate.
  - Alternatives: inline in component (rejected — ws.ts + App.tsx also need the types); separate mode2_types.ts (rejected — kickoff names only lib/mode2_suppression.ts).

- Decision: One full modal at a time; toasts queue independently.
  - Rationale: A high-confidence interruption shouldn't be blocked by stale toasts. Duplicate candidate_id is deduped by both surfaces.

- Decision: "Maybe later" does NOT suppress; "Don't ask again" does.
  - Rationale: Kickoff §1 names the three actions distinctly.

- Decision: Confidence threshold = 0.7, prop-overridable.
  - Rationale: Kickoff §2 names 0.7 explicitly; the prop lets a future settings UI tighten/loosen without re-shipping.

- Decision: Audit-event lifecycle = on every user action (add | dismiss | suppress), with surface tag (modal | toast). The Wave 1 classifier already writes per-emission lines to ~/.grace2/mode2_audit.log server-side, so the union of server display log + client action audit reconstructs the full lifecycle.
  - Rationale: A passive toast a user never sees shouldn't pollute the audit log with a synthetic display event. Hook for strict literal "every display too" is left in place — see OQ-0126-AUDIT-ON-DISPLAY.

## Invariants Touched

- 1. Determinism boundary: preserves. Every number rendered (confidence pct, pattern names, domain, snippet) is verbatim from the envelope. Math.round(confidence*100) is presentational formatting only.
- 4/5. Rendering / Tier separation: preserves. Modal is chat-side; no map/tile state touched.
- 8. Cancellation: preserves. Both surfaces are dismissible without consequence; loaded layers untouched.
- 9. Confirmation before consequence: preserves. Add-to-catalog is the only path that emits a confirmed envelope; the heavier offer-catalog-addition flow's confirmation gate (sprint-08) still binds.

## Open Questions

- OQ-0126-MODE2-ADD-CONFIRMED-SCHEMA — mode2-add-confirmed is not registered in packages/contracts/.../ws.py. TENTATIVE shape mirrors candidate_id + url + domain + suggested_tool_kind so the server can correlate to ~/.grace2/mode2_audit.log and hand off to offer-catalog-addition. Routing: schema — promote Mode2CandidatePayload + Mode2AddConfirmedPayload + Mode2AuditEventPayload to pydantic models, then migrate the TS types into contracts.ts.

- OQ-0126-AUDIT-ON-DISPLAY — my audit lifecycle fires on user action only. The Wave 1 server already maintains ~/.grace2/mode2_audit.log (1 line per emission). If strict literal "every modal display + user action" is required, the hook is at the subscription effect. Routing: agent.

- OQ-0126-AUDIT-PERSISTENCE — mode2-audit-event is fire-and-forget; server-side persistence is not yet wired. Routing: agent — pick the JSONL target file.

- OQ-0126-CASE-ID-WIRING — like SecretsPanel (job-0125), no case_id is attached to mode2-add-confirmed because no currentCaseId selector exists yet. Routing: web (self) — wire into the next case-context job.

## Dependencies and Impacts

- Depends on: job-0101 (Wave 1 Mode 2 classifier + server.py emission), job-0125 (Wave 2 SecretsPanel App.tsx — coexists without conflict).
- Affects:
  - schema — promote mode2-candidate / mode2-add-confirmed / mode2-audit-event to canonical pydantic models.
  - agent — consume mode2-add-confirmed (hand off to offer-catalog-addition) and mode2-audit-event (server-side persistence).

## Verification

- Tests run: Full Vitest suite (cd web && npx vitest run): 109/109 passed, 10 test files, including 12 new Mode2OfferModal + suppression + ws.ts send-path tests.
- Type check: npx tsc --noEmit clean for all my new files and edits. The 4 pre-existing errors in ws.test.tsx are unrelated to this job.
- Live E2E evidence (Playwright + real Chromium against the dev server on http://localhost:5173):
  - reports/inflight/job-0126-web-20260608/evidence/mode2_modal_high_confidence.png — high-confidence modal renders with .gov TLD badge, water.weather.gov domain, pattern chips (openapi-spec-link, data-download-link, tabular-data), API endpoint kind chip, 85% confidence, snippet excerpt, three action buttons.
  - reports/inflight/job-0126-web-20260608/evidence/mode2_toast_low_confidence.png — low-confidence (0.55) .edu toast renders in the bottom-left without a backdrop modal.
  - Verified predicates (all PASS):
    1. High-confidence envelope renders as MODAL (not toast).
    2. Low-confidence envelope renders as TOAST (not modal).
    3. Click Add -> dismisses modal AND emits mode2-add-confirmed envelope with candidate_id=01HFAKEMODALDEMO00000000001, domain=water.weather.gov, suggested_tool_kind=endpoint.
    4. Click Add also emits mode2-audit-event with action=add, surface=modal.
    5. Click "Don't ask again" -> writes domain to grace2.mode2_suppressed_domains localStorage list (["nws.noaa.gov"]).
    6. A re-emitted candidate on the suppressed domain does NOT surface (no modal, no toast).
    7. Suppress action emits mode2-audit-event with action=suppress, surface=modal, domain=nws.noaa.gov.
- Results: pass
