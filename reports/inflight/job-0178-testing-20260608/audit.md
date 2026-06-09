# Audit: Wave 4.9 LIVE Playwright verify (no inject seams)

**Job ID:** job-0178-testing-20260608, **Specialist:** web (Opus, gated)

## Scope

After Stage A:
1. Kill existing agent (find PID); setsid + nohup launch with full env
2. Open browser at localhost:5177, accept anonymous, create Case
3. Send REAL prompts via chat input (NO inject seams per `feedback_playwright_must_drive_live_agent`):
   - "Show me radar over America" (raster — already passes; control)
   - "Show me weather alerts across America" (vector polygon — was the bug)
   - "Show me protected areas in Big Cypress" (vector polygon + multi-tool chain)
   - "Show me roads near Fort Myers" (vector linestring)
4. Verify each layer ACTUALLY rendered on the map (pixel-level — count non-basemap-colored pixels in the overlay area)
5. Verify chat scroll reads as INTERLEAVED text + tool cards in arrival order (no separate strip)
6. Force a recoverable failure (e.g. invalid prompt) — verify retry attempt fires
7. Capture screenshots — be HONEST about pass/fail per scenario

## File ownership
- `reports/inflight/job-0178-testing-20260608/`

## FROZEN
Single commit prefix `job-0178:`. Codified lessons.

## Live-drive enforcement

Forbidden: `__grace2Inject*` seams of any kind. Must drive via the real chat input.
