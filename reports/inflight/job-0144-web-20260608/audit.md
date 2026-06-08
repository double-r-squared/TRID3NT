# Audit: Chat input redesign — Claude Code-style merged send/stop button + dynamic textarea

**Job ID:** job-0144-web-20260608, **Sprint:** sprint-12-mega Wave 4, **Specialist:** web (Opus — interaction-heavy)

**Required reads:**
- `web/src/components/Chat.tsx` (existing input + send/cancel)
- `web/src/ws.ts` (cancel envelope wiring)
- Reference: Claude Code chat input pattern (up-arrow → stop-square → up-arrow state cycle)

### Scope

Replace the existing send + separate cancel button + static textarea with a SINGLE polished chat input control inspired by Claude Code.

#### Part 1 — Merged send/stop button

One square button positioned **bottom-right INSIDE the input wrapper**:
- **Idle state** (no message in flight): blue background, up-arrow icon (↑), enabled when textarea has non-whitespace content
- **In-flight state** (message dispatched, response not yet complete): grey background, stop-square icon (■), clicking emits cancel envelope
- **Returning-to-idle** (cancel/response complete): smoothly transitions back to blue up-arrow

State source: derive from existing pipeline state (current_pipeline.status: 'idle'|'running'|'complete'|'cancelled').

#### Part 2 — Dynamic textarea

Single content-editable / textarea control that grows with content:
- Min height: 1 line of text + padding (~48px)
- Max height: ~40% of viewport height (then scrolls internally)
- Auto-expands as user types more lines
- Multi-line: enter inserts newline; Cmd+Enter / Ctrl+Enter submits

#### Part 3 — Outer wrapper styling

The textarea + send button live INSIDE a single rounded container:
- Subtle drop shadow (e.g. `box-shadow: 0 2px 12px rgba(0,0,0,0.15)`)
- Rounded corners (~12-16px)
- Dark-theme aware background (slightly elevated above chat background)
- Border subtle (or none — let the shadow do the work)

#### Part 4 — Overlay positioning

The chat input wrapper is positioned as an OVERLAY at the bottom of the chat panel:
- Floats over the bottom of the chat message scroll
- The chat message scroll has bottom-padding equal to input height so messages don't get hidden behind input
- When input grows (long message), chat scroll shifts up (or the input simply overlays; pick which feels best)
- The wrapper does NOT displace chat content — it floats over it

#### Part 5 — Send/cancel envelope wiring

- On idle-state submit: emit `agent-prompt(text)` envelope (existing path) — clear input + transition to in-flight
- On in-flight stop click: emit existing `cancel` envelope (whatever the current cancel button uses)
- On pipeline complete or cancelled: transition back to idle

#### Part 6 — No separate displayed cancel button

The existing separate cancel button is DELETED. Cancel is exclusively the in-flight stop-square.

**Tests** (Vitest):
- Idle state shows up-arrow disabled when empty
- Idle state shows up-arrow enabled when text present
- Submit transitions to in-flight; up-arrow becomes stop-square
- Cancel click emits cancel envelope + returns to idle
- Pipeline-complete returns to idle automatically
- Multi-line typing expands textarea height
- Cmd+Enter submits; Enter alone inserts newline
- Drop shadow + rounded corner styles applied (snapshot test or class assertions)

**Live verification** (Playwright):
- 4 screenshots: idle empty, idle with text, in-flight stop-square, post-cancel idle
- Verify text overflow with a 500-char message → textarea grows visibly + chat content not hidden
- Verify the wrapper has visible drop shadow (DOM inspection: computed box-shadow non-empty)

### File ownership (exclusive)

- `web/src/components/Chat.tsx` — significant refactor (~150 lines of changes)
- `web/src/components/ChatInput.tsx` (NEW — extract input wrapper into focused component)
- `web/src/Chat.test.tsx` + `web/src/ChatInput.test.tsx` (new)
- `web/src/styles/chat-input.module.css` (NEW — if using CSS modules) OR inline styles in component
- `web/src/ws.ts` — cancel envelope emitter helper (additive ~20 lines)
- `reports/inflight/job-0144-web-20260608/`


### FROZEN

All files outside the explicit file-ownership list. Especially: every sibling Wave 4 job's exclusive files; `reports/complete/**`.

### Codified lessons (do NOT violate)

1. **Geographic-correctness gate (job-0086)**: pixel-level evidence required.
2. **Kickoff-front-loaded design**: execute scope, surface OQs, don't redesign.
3. **MongoDB MCP persistence (job-0115)**: use Persistence.* — no custom CRUD.
4. **Concurrent web jobs**: App.tsx will be touched by multiple Wave 4 jobs. Pre-commit `git pull --rebase` before commit. Idempotent-append discipline; if conflict, re-apply your specific changes.

### Acceptance criteria

- [ ] All deliverables landed per scope
- [ ] Live Playwright verification per kickoff (screenshots of NEW visual state vs old)
- [ ] No FROZEN edits; single commit prefix `<job-id>:`; co-author line
- [ ] Returns commit SHA + outcome + 1-paragraph headline + evidence + OQs

