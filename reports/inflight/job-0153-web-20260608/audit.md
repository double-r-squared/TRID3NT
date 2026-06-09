# Audit: Chat polish bundle — markdown + scroll-to-bottom arrow + placeholder + Enter behavior + layout fix

**Job ID:** job-0153-web-20260608, **Sprint:** sprint-12-mega Wave 4.5, **Specialist:** web (Opus — significant feature work)

**Required reads:**
- `web/src/components/Chat.tsx` (job-0144 refactor)
- `web/src/components/ChatInput.tsx` (job-0144 NEW)
- User direction 2026-06-08 (test report)

### Why

User tested the localhost demo and surfaced 5 chat improvements:
1. Inline chat card at bottom is covered by chat input overlay — content hidden
2. No "scroll to bottom" affordance when scrolled up
3. LLM text renders with raw markdown chars (no renderer); also in a border that doesn't fit the chat panel
4. User chat bubble styling needed: grey box with white text
5. Placeholder text too long
6. Enter to send (currently Cmd+Enter)

### Scope — 6 deliverables in one bundled job

#### Part 1 — Markdown rendering for LLM messages

Add a markdown renderer for `agent` role messages:
- Use `react-markdown` (add to web/package.json deps) + `remark-gfm` for tables/strikethrough
- LLM message renders rendered markdown (headings, bold, italic, lists, code blocks, links)
- Style: transparent background (no border, no box) — content lives directly in the chat panel
- Reasonable margin/spacing between markdown elements

#### Part 2 — User message styling

User messages in chat appear as:
- Grey rounded bubble (subtle, e.g. `rgba(255,255,255,0.08)` bg in dark theme)
- White text
- Right-aligned (matches Claude Code convention — user on right, agent on left/unaligned)
- Bounded box ≤80% chat width

#### Part 3 — Scroll-to-bottom affordance

- When user is scrolled up (not at bottom): show floating down-arrow button centered ABOVE chat input wrapper
- Arrow: transparent background, ~32px circle, subtle white border, down-pointing chevron icon
- Click: smooth-scroll to bottom of chat
- Auto-hide when scrolled within ~50px of bottom (use IntersectionObserver or scroll handler)
- Smooth fade-in/fade-out (~200ms)

#### Part 4 — Chat content NOT hidden by input overlay

Ensure bottom chat content is fully visible when scrolled to bottom:
- chat scroll container has bottom-padding = chat input height + 16px gap
- When input grows (multi-line), bottom-padding grows correspondingly
- Inline cards (PayloadWarning, SourceSuggestion) at chat bottom should be fully readable, NOT clipped by input

#### Part 5 — Placeholder text

Replace existing placeholder with short, informative text:
- `"Reply to GRACE-2"` — recommended (mirrors Claude's pattern)
- OR `"Ask GRACE-2..."` — alternative
- Either works; pick the cleaner one

#### Part 6 — Enter key behavior (FLIP from job-0144)

Change ChatInput key handling:
- **Enter alone** → SUBMIT (clear text, send)
- **Shift+Enter** → newline (multi-line input)
- This matches user expectation + Claude Code convention
- Resolves OQ-0144-CMD-ENTER-VS-PLAIN-ENTER-DEFAULT

**Tests** (Vitest):
- Markdown renders: H1, bold, code blocks, links, lists all visible
- User bubble: grey bg + white text + right-align (snapshot)
- Agent message: no border, transparent bg
- Scroll-to-bottom arrow hidden when at bottom; visible when scrolled up
- Scroll handler smooth-scrolls on click
- Chat content fully visible at bottom (no overlap with input)
- Placeholder text = "Reply to GRACE-2"
- Enter submits; Shift+Enter inserts newline
- Empty input + Enter does NOT submit

**Live verification** (Playwright, 5 screenshots):
- `01_chat_with_markdown.png` — agent message with `# heading
**bold** + [link](...) + \`\`\`code\`\`\`` rendered as actual markdown
- `02_user_message_bubble.png` — user message right-aligned with grey bubble + white text
- `03_scroll_arrow_visible.png` — scrolled up; down-arrow centered above chat input
- `04_scroll_arrow_hidden.png` — at bottom; arrow gone
- `05_enter_submits.png` — Enter sends, Shift+Enter newlines (state captured at multi-step input)

### File ownership (exclusive)

- `web/src/components/Chat.tsx` — markdown wiring, scroll handler, content padding (~200 lines changes)
- `web/src/components/ChatInput.tsx` — placeholder + Enter behavior flip (~30 lines)
- `web/src/components/ScrollToBottom.tsx` (NEW)
- `web/src/components/UserBubble.tsx` (NEW small component for user message styling)
- `web/src/components/AgentMessage.tsx` (NEW — markdown renderer wrapper)
- `web/package.json` — add `react-markdown` + `remark-gfm` deps
- `web/src/Chat.test.tsx` + `web/src/ChatInput.test.tsx` + new test files
- `reports/inflight/job-0153-web-20260608/`


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

