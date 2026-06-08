# Audit: In-chat cards — payload warnings + source suggestions (Claude Code-styled, drop "Mode 2")

**Job ID:** job-0145-web-20260608, **Sprint:** sprint-12-mega Wave 4, **Specialist:** web (Opus)

**Required reads:**
- `web/src/components/PayloadWarningInline.tsx` (Wave 2 — existing inline card)
- `web/src/components/Mode2OfferModal.tsx` (Wave 2 — current modal, will be REPLACED)
- Memory: `feedback_large_payload_chat_warning`

### Why

User feedback: payload warnings + source-detection notifications currently lack polish; the "Mode 2" terminology is internal jargon that shouldn't surface to the user. Replace with Claude Code-style inline chat cards.

### Scope

#### Part 1 — PayloadWarning inline card polish

Restyle `PayloadWarningInline` to look like Claude Code's inline informational cards:
- Subtle background (semi-transparent over chat bg)
- Rounded corners + drop shadow
- Icon at left (warning triangle or similar)
- Title + body text + action row
- Action buttons: text-only or filled, dark-theme aware
- NO modal overlay — it sits IN the chat scroll as a message card
- Width matches chat message width (not full chat panel)

#### Part 2 — Source suggestion card replaces Mode 2 modal

DELETE `Mode2OfferModal.tsx` (and test). REPLACE with inline chat card `SourceSuggestionInline`:
- Triggered by the SAME `mode2-candidate` envelope (server-side name is internal — UI just renames)
- User-facing label: "Found a useful data source you might want to add"
- Same styling as PayloadWarning card (consistent visual language)
- Snippet of detected page + domain + 2-3 detected capabilities (translated from `detected_patterns` field — e.g. `json-ld` → "Has machine-readable metadata"; `data-download-link` → "Offers data downloads"; etc.)
- Actions: "Add data source" / "Maybe later" / "Don't suggest this domain again"
- "Don't suggest again" stored in local-storage as before
- Suggestion confidence shown as a subtle "70% match" or similar, NOT as a raw decimal

**Critical**: no surface text contains "Mode 2", "Mode 1", "Tier 1", "Tier 2", "OQ-*", or any internal term. The agent's emission is what it is; the UI translates to user-friendly language.

#### Part 3 — Inline card pattern reuse

Extract a common `InlineChatCard` primitive that PayloadWarning + SourceSuggestion + any future agent-emitted inline informational cards can use:
- `<InlineChatCard variant="warning"|"info"|"success" icon={...} title="..." body="..." actions={[...]} />`
- Consistent styling across all cards
- A11y: appropriate ARIA roles

**Tests** (Vitest):
- PayloadWarningInline renders with new visual identity
- SourceSuggestionInline renders from mode2-candidate envelope, no "Mode 2" text in output
- Detected patterns translated to user-friendly phrases
- Confidence shown as percentage
- "Don't suggest again" persists per-domain
- InlineChatCard primitive renders 3 variants
- a11y: cards have appropriate roles

**Live verification** (Playwright):
- Inject payload warning → polished card in chat
- Inject mode2-candidate → SourceSuggestionInline appears, no internal jargon visible
- 3 screenshots: payload warning card, source suggestion card, primitive variants

### File ownership (exclusive)

- `web/src/components/PayloadWarningInline.tsx` — restyle (substantial)
- `web/src/components/SourceSuggestionInline.tsx` (NEW — replaces Mode2OfferModal)
- `web/src/components/InlineChatCard.tsx` (NEW — common primitive)
- `web/src/components/Mode2OfferModal.tsx` — DELETE
- `web/src/Mode2OfferModal.test.tsx` — DELETE
- `web/src/InlineChatCard.test.tsx` + `web/src/SourceSuggestionInline.test.tsx` (new)
- `web/src/PayloadWarningInline.test.tsx` — update for restyle
- `web/src/App.tsx` — replace Mode2OfferModal mount with SourceSuggestionInline in chat
- `web/src/lib/mode2_suppression.ts` — rename to `source_suggestion_suppression.ts` (still per-domain)
- `reports/inflight/job-0145-web-20260608/`


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

