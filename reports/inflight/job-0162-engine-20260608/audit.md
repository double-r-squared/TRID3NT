# Audit: Web UI bundle — collapse preserves chat + duplicate cards + pipeline-card visual states

**Job ID:** job-0162-engine-20260608, **Sprint:** sprint-12-mega Wave 4.6, **Specialist:** web (Opus)

**Required reads:**
- `web/src/components/Chat.tsx`
- `web/src/components/PipelineCard.tsx` (or wherever pipeline cards render)
- Memory: `feedback_pipeline_card_visual_states` (codified spec)
- `web/src/App.tsx` (X close button — currently erases chat content)

### Why

User-reported (2026-06-08):
- **X button erases all chat** when collapsing the panel — should preserve. Change icon from X to a less destructive symbol (chevron-right or similar)
- **Duplicate pipeline cards**: stale blue card at start AND green completed card at end. Should be ONE card transitioning states
- **Pipeline-card visual states**: implement the spec from `feedback_pipeline_card_visual_states.md` — grey idle → rainbow gradient running + spinner → green success / red fail; drop borderlines, "completed" text, blue left-edge accent

### Scope

#### Part 1 — Collapse preserves chat
- X button: rename to chevron icon (visual idiom: panel collapse, not close)
- Clicking does NOT clear chat state; just hides the panel
- Re-expanding restores prior chat history visible

#### Part 2 — Single transitioning pipeline card per tool dispatch
- Render by `event_id` or `step_id`; updates merge into existing card
- No duplicate at start + end

#### Part 3 — Visual states per memory spec
- Pending: greyed bg + greyed text + no right-side indicator
- Running: normal bg + rainbow gradient animated text + spinner
- Success: full-card green tint
- Failure: full-card red tint
- Drop: blue left-edge, checkmark, "...", "completed" text, borderlines
- Vertical padding between stacked cards (12-16px)
- Respect `prefers-reduced-motion`

#### Part 4 — Verify

Playwright dev-injection covering each card state + collapse behavior. 4 screenshots in evidence/.

### File ownership

- `web/src/components/Chat.tsx`
- `web/src/components/PipelineCard.tsx` (or analogous)
- `web/src/App.tsx` (X icon change)
- Tests
- `reports/inflight/job-0162-engine-20260608/`


### FROZEN

All files outside the explicit file-ownership list. Especially: every sibling Wave 4.6 job's exclusive files; `reports/complete/**`.

### Codified lessons (do NOT violate)

1. Geographic-correctness gate (job-0086): pixel-level evidence required.
2. Kickoff-front-loaded design: execute scope, surface OQs.
3. UX language discipline: no internal terms in user-facing surfaces.
4. Pre-commit: `git pull --rebase` before commit.

### Acceptance criteria

- [ ] Deliverables landed per scope
- [ ] Live verification per kickoff
- [ ] No FROZEN edits; single commit prefix; co-author line
- [ ] Returns commit SHA + outcome + headline + evidence + OQs

