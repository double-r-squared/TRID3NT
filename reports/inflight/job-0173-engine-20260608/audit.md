# Audit: Small UX cleanup bundle — Thinking label + chat unstick + map unlock + remove nudge buttons

**Job ID:** job-0173-engine-20260608, **Specialist:** web (Opus — multi-deliverable)

## Scope

### Part 1 — Rename `llm_generation` → `Thinking…`
In the pipeline card label-mapping (see memory `feedback_pipeline_card_humanized_labels`), the Gemini thinking step should display as "Thinking…" not "llm_generation".

### Part 2 — Chat input force-idle on error
When `error` envelope arrives (Gemini failure, agent crash + reconnect, dispatch TypeError, etc.), force-transition `ChatInput` state to `idle` (blue up-arrow). Currently the input stays in `in-flight` (grey stop-square) so user can't send a new prompt.

### Part 3 — Map pan unlock
After a flood/raster layer renders, user can't pan/drag the map. Diagnose: invisible overlay capturing pointer events? Modal stuck open? MapLibre `interactive: false`? Find + fix.

### Part 4 — Remove redundant ▲/▼ nudge buttons in LayerPanel
`web/src/LayerPanel.tsx` has up/down z-order arrows on each layer. **Redundant** with the existing @dnd-kit drag-and-drop reorder. Delete the buttons + handlers + tests. Keep drag-and-drop.

## Verify
Live + Playwright covering each part.

## File ownership
- `web/src/components/ChatInput.tsx` (force-idle on error)
- `web/src/Map.tsx` (pan unlock)
- `web/src/LayerPanel.tsx` (remove nudge buttons)
- `web/src/components/PipelineCard.tsx` (Thinking label)
- Tests
- `reports/inflight/job-0173-engine-20260608/`

## FROZEN
Single commit prefix `job-0173:`.
