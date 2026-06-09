# Audit: Inline tool cards in chat scroll (interleave pattern)

**Job ID:** job-0176-engine-20260608, **Specialist:** web (Opus)

## Why

Per memory `feedback_chat_tool_interleave`: user wants tool usage cards INLINE in chat scroll alongside agent text bubbles, not in a separate strip.

Pattern:
```
[user]    "Show me protected areas in Fort Myers"
[agent]   "I'm locating the area..."
[tool]    Locating area [Nominatim] (0:01) ✓
[agent]   "Now fetching protected areas..."
[tool]    Fetching protected areas [WDPA] (0:08) ✓
[agent]   "I've added 2 protected areas (Everglades NP, Big Cypress NP)."
```

## Scope

Refactor pipeline cards from separate region into the chat scroll:
- New unified chat message stream: each `pipeline-state` event becomes a `kind="tool"` chat message at its arrival time; `agent-message-chunk` events become `kind="agent"` text bubbles
- Tool cards reuse visual states from `feedback_pipeline_card_visual_states` (grey/rainbow+spinner/green/red)
- Use humanized labels from `feedback_pipeline_card_humanized_labels` ("Fetching protected areas [WDPA] (0:08) ✓" etc.)
- Old separate pipeline strip region — delete

## Verify

LIVE: "Show me weather alerts across America" → chat scroll reads top-to-bottom in correct order: user prompt → agent intro text → tool card → agent narration text.

Screenshot showing the interleaved scroll.

## File ownership
- `web/src/components/Chat.tsx`
- `web/src/components/PipelineCard.tsx` (or analogous)
- Tests
- `reports/inflight/job-0176-engine-20260608/`

## FROZEN
Single commit prefix `job-0176:`. Codified lessons.
