# Live mobile/desktop feedback - 2026-06-25 (NATE driving)

Accumulated per the accumulate-batch norm. HELD for go except the reliability cluster
(runaway guard / hard-shutdown) which NATE green-lit ("make sure this doesn't happen again").

## RELIABILITY (GO NOW - task #186)
- Runaway agent: Nova Lite ran away on ONE prompt, pinned + wedged the box (SSM Undeliverable, restart Failed); only ec2 stop unstuck it. Need: per-turn tool-call/step cap + loop watchdog (abort runaway turns, typed error) + Nova-Lite-tighter cap + max wall-clock.
- Agent BUSY-lock: agent claims busy, cannot be put to sleep even when idle; blocks starting new cases. Need a HARD shutdown / force-sleep override.
- Nova Lite: disconnects after one query, stuck "connecting", layers don't compute. (same runaway/instability cluster)

## DELEGATED TO TOOLS SESSION (reports/inflight/tools-backlog/)
- GOES identifier: agent uses "goes18" instead of "goes-18"; the EXACT identifier must be explicit in the tool description. Principle: tool description carries ALL info needed for selection, NO extraneous info.
- "Fetch Goes Archive Animation" label is false/inconsistent -> generic "Fetched GOES file"; capitalize "GOES".

## UI/UX - scrubber & overlays (HELD)
- Scrubber when agent box DISCONNECTED: snap to the BOTTOM of the bbox.
- When covered by wake-up button or chat panel: snap ABOVE those elements.
- Scrubber<->chat panel padding too tight; add padding.
- Mobile: if user drags chat panel up and it intersects the scrubber, scrubber must snap to avoid overlap.
- Mobile: the "Drawn AOI" button renders OVER the settings button (inaccessible) -> put it UNDER settings.
- Desktop: bottom of chat history too close to the text input form; some text/buttons hidden behind the form.

## Agent behavior & cards (HELD)
- Tool cards don't appear immediately after a tool call; only after a refresh.
- Python sandbox cards: fold inline after completion/approval (currently stuck un-collapsible at bottom); FAILED runs must show RED; acceptance gates must fold inline on acceptance (desktop + mobile); folded cards reuse existing dims + accent color, with a GLOW while running (health pings later, maybe).
- Humanized status: "Layer statistics ready" -> "Code completed" for sandbox steps.

## Rendering & data (HELD)
- GOES imagery not always displayed, esp. during ANIMATION runs (single-frame test works).
- Low output image resolution.
- Layer DELETION bug: deleting one layer momentarily drops many others (refresh restores).
- True-color raster deletion deletes BOTH rasters (recurring).
- 3D terrain too aggressive -> layers extremely pixelated (~9 px).
- Sentinel-2 guardrail 0.5 deg^2 too strict for ~0.77 deg^2 AOI; no resolution gate offered. Suggestion: loosen limits + adopt deck.gl for unified rendering + better large-file/3D.
- Vercel build.chunkSizeWarningLimit warning - adjust.

## Connectivity & session (HELD)
- Mobile REFRESH navigates to the cases LIST instead of staying in the current case.

## Tooling integration (HELD)
- Draw + select tools awaiting UI integration; reuse existing components where similar.
