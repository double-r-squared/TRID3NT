# Audit: UX cleanup — pipeline failure terminates + font consistency + dup llm_generation card

**Job ID:** job-0166-web-20260608, **Sprint:** sprint-12-mega Wave 4.7, **Specialist:** web (Opus)

## Why
Three user-reported bugs after Wave 4.6:
1. Pipeline-card failure doesn't terminate animation (rainbow keeps spinning on LLM_UNAVAILABLE / tool TypeError) — should transition to RED no-animation
2. Cases area renders in serif (Times-New-Roman-like); rest of app is sans-serif
3. Duplicate llm_generation cards (stale blue + green completed); should be ONE transitioning card

## Scope

### Part 1 — Pipeline card failure transition
When an `error` envelope arrives: find the most-recent `running` card → force-transition to `failure` (red bg, no animation, no spinner). Prefer matching `tool_name` if present; else most-recent.

### Part 2 — Font consistency
Audit `font-family` declarations in `web/src/**/*.{tsx,css,module.css}`. Find existing sans-serif convention. Enforce across CasesPanel, CaseView, ConfirmationDialog. OR add global body font in shared CSS file.

### Part 3 — Single transitioning llm_generation card
Render by event_id; incoming pipeline-state updates merge into existing card.

## Verify
Live test (agent PID 1833028, port 8765). 3 screenshots in evidence/:
- Failed pipeline card (red, no animation)
- Font consistency (chat + cases same font)
- Single llm_generation transitioning card

## File ownership
- `web/src/components/PipelineCard.tsx` (or analogous)
- `web/src/components/CasesPanel.tsx`, `CaseView.tsx`, `ConfirmationDialog.tsx`
- Possibly NEW `web/src/styles/global.css`
- Tests
- `reports/inflight/job-0166-web-20260608/`

## FROZEN
All other web files. Single commit prefix `job-0166:`.
