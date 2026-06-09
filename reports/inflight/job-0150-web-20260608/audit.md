# Audit: PayloadWarning visual restyle (apply the polish)

**Job ID:** job-0150-web-20260608, **Sprint:** sprint-12-mega Wave 4.5, **Specialist:** web (Sonnet small)

**Required reads:**
- `web/src/components/PayloadWarningInline.tsx` (job-0145)
- `web/src/components/InlineChatCard.tsx` (job-0145 primitive)
- `reports/inflight/job-0148-testing-20260608/evidence/09_payload_warning_card.png`

### Why

Wave 4 verification: 09_payload_warning_card.png computed style shows `shadow='none' radius=0px` despite job-0145 kickoff requiring drop shadow + rounded corners. The restyle didn't actually apply at runtime.

### Scope

Diagnose why InlineChatCard's drop shadow + rounded corners aren't applying to PayloadWarning:
- CSS class missing? Module ID conflict?
- Are the styles inline or in a CSS module that didn't get bundled?

Fix so computed style at runtime shows:
- `box-shadow: 0 2px 12px rgba(0,0,0,0.15)` (or similar polished value)
- `border-radius: 12-16px`
- Subtle semi-transparent background distinguishing it from plain chat bg

**Tests**: extend PayloadWarningInline.test.tsx — assert computed style box-shadow non-empty AND border-radius >= 8px

**Live verification**: Playwright dev-injection of payload warning → screenshot AND DOM inspection confirming shadow + radius

### File ownership (exclusive)

- `web/src/components/PayloadWarningInline.tsx` OR `web/src/components/InlineChatCard.tsx` (whichever needs the fix)
- `web/src/PayloadWarningInline.test.tsx` — extend
- `reports/inflight/job-0150-web-20260608/`


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

