# Audit: UI tweak #1 — pipeline cards inline in chat

**Job ID:** job-0064-web-20260607, **Sprint:** sprint-09 (Stage C UI tweaks; gates Stage D Playwright), **Auditor:** Development Orchestrator, **Status:** assigned

## Task Assignment

**Specialist:** web

**Prerequisites:**
- job-0026 (APPROVED): `PipelineStrip.tsx` original; the bus pattern for pipeline-state events.
- job-0035 (APPROVED): real PipelineEmitter + replace-not-reconcile per A.7 — the source of pipeline-state envelopes.

**SRS references** (narrow file loading only):
- `docs/srs/A-websocket-protocol.md` A.7 (replace-not-reconcile semantics for pipeline-state)
- `docs/srs/03-functional-requirements.md` FR-WC (Web Client requirements — for chat UX)
- DO NOT load `docs/SRS_v0.3.md` monolith.

**Required reads:**
- `web/src/PipelineStrip.tsx` — current strip implementation (what you're moving cards out of)
- `web/src/Chat.tsx` — where the cards will land
- `web/src/App.tsx` — the LayerPanelBus pattern that routes envelopes (you'll subscribe to pipeline-state from Chat instead of the strip)

### Why this job exists

User direction 2026-06-07: "could we put the pipeline cards in the chat since the agent is performing the operations? I would like to have them stacked in the order in which they are called, I want them to also be one line preferable so it's operation then %, this also clears up our base map so we don't clutter the UI too much".

The pipeline cards belong in the chat stream alongside the agent messages because the agent is the one performing the operations. The basemap stays clean.

### Scope

1. **`web/src/Chat.tsx`**:
   - Add a new visual element: inline pipeline cards rendered in the chat stream, stacked in call order (sorted by `step.started_at` or by emission order).
   - One-line format: `<step.name> <progress %>%` (e.g., `"fetch_dem 100%"`, `"build_sfincs_model 47%"`).
   - Cards should look subtle — small, low-contrast — so they don't overwhelm the chat text. Maybe a left border + a progress-fill background gradient, or a simple percentage badge next to the operation name.
   - Render position: at the END of the assistant's message block currently being streamed, or in a dedicated "tool calls in flight" group within the conversation. Pick the cleaner of the two (probably the dedicated group at the bottom of the conversation, until the run completes, then it scrolls into history).
   - On completion: cards stay in the history (so the user can scroll back and see what the agent did) but transition to a "done" visual state (no percentage, just the operation name + a check or similar).

2. **`web/src/PipelineStrip.tsx`**:
   - The strip component is now dead chrome. **Two options:**
     - **Option A (cleaner):** delete the strip entirely; pipeline-state envelopes route to Chat instead. Remove from `App.tsx` mount.
     - **Option B (safer):** keep the strip as a hidden component or reduce it to a minimal cancel-button substrate that still consumes session-state for the cancel UX (PipelineStrip's prior role per its docstring). Document the choice.
   - Per the user direction (clear the basemap), Option A is preferred. If the cancel button logic is tightly coupled inside the strip, refactor it into Chat or into a minimal top bar.

3. **`web/src/App.tsx`**:
   - Update the LayerPanelBus wiring: pipeline-state subscribers move from PipelineStrip to Chat.
   - If PipelineStrip is deleted, remove the mount.
   - If a cancel button needs a new home, dock it where it stays accessible (small top-right corner or in the chat input area).

4. **Tests**:
   - Update existing PipelineStrip tests (or replace them if PipelineStrip is removed).
   - New Chat test: pipeline-state envelope arrives → inline card appears in the chat stream with the correct operation name + progress.
   - Multiple steps → multiple cards stacked in call order.
   - Step completion: card transitions to "done" state.

5. **Verification**:
   - `npm run test` (or whatever the web test runner is — check `web/package.json`)
   - `npm run dev` and manually scroll through the chat with a mocked pipeline-state stream — confirm the cards stack correctly, are one-line, and look unobtrusive
   - Capture a screenshot at `reports/inflight/job-0064-web-20260607/evidence/chat_with_pipeline_cards.png`

### File ownership (exclusive)
- `web/src/Chat.tsx` — inline pipeline card rendering
- `web/src/PipelineStrip.tsx` — delete or reduce per Option A/B
- `web/src/App.tsx` — bus wiring update + mount adjustment
- `web/src/components/PipelineCard.tsx` (NEW, if extracted) — the visual card itself
- `web/src/PipelineStrip.test.tsx` or similar — update/replace tests
- `web/src/Chat.test.tsx` or similar — new tests
- `reports/inflight/job-0064-web-20260607/`

### FROZEN
- `web/src/Map.tsx` (concurrent job-0065 owns)
- `web/src/LayerPanel.tsx` (concurrent job-0065 owns)
- `web/src/ws.ts` — WS layer; no contract changes
- `web/src/contracts.ts` — generated; don't edit
- `web/src/main.tsx`
- All non-web/, all services/, all packages/, all infra/, all docs/

### Acceptance criteria
- [ ] Pipeline cards render inline in chat stream, stacked in call order
- [ ] One-line format per card: `<operation> <pct>%`
- [ ] Cards transition to "done" state on completion
- [ ] PipelineStrip removed (or reduced to cancel-button substrate per choice; document)
- [ ] Cancel button still accessible
- [ ] Web tests pass
- [ ] Screenshot captured showing the new chat-with-cards layout
- [ ] No edits to FROZEN paths
- [ ] Single commit
