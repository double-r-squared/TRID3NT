# Audit: Vector polygon rendering bug — weather alerts no-render

**Job ID:** job-0175-engine-20260608, **Specialist:** web (Opus)

## Why (OPEN BUG from Wave 4.8 live verify)

Wave 4.8 job-0174 live test: "Show me weather alerts across America" → 240s elapsed, agent narrated success, BUT `settled_with_overlay: false, new_layers: []`. Layer NEVER reached the map. Same pattern for protected areas in Fort Myers (test 4) — agent narrated success but vector polygon never appeared.

NEXRAD raster DID render (test 2 PASS). So **raster path works; vector polygon path is broken.**

## Scope

**Diagnose**: trace Map.tsx vector LayerURI handling. Hypotheses:
A. `fetch_nws_alerts_conus` returns FlatGeobuf URI that Map.tsx can't fetch (CORS? auth? format?)
B. SESSION_HUB fan-out delivers session-state to App.tsx but Map.tsx's vector branch doesn't fire on incremental adds
C. `clip_vector_to_polygon` / `clip_raster_to_polygon` are part of the chain and one of them silently fails
D. FlatGeobuf parsing (job-0139 added) blocks on a network error invisibly
E. Style-preset registry lookup fails for `nws_alerts_conus` preset and aborts the addLayer

Capture diagnosis in evidence/diagnosis.md with file:line citations.

**Fix**: based on root cause, make vector polygon layers render reliably when emitted by ANY fetcher (WDPA, NWS alerts, MTBS, NIFC, OSM roads, GBIF, iNat, etc.).

## Verify

LIVE (no inject seams per `feedback_playwright_must_drive_live_agent`):
1. Restart agent
2. "Show me weather alerts across America" → polygons RENDER on map within 30s
3. "Show me protected areas in Big Cypress" → polygons render
4. "Show me roads near Fort Myers" → linestrings render

Capture screenshots showing actual map pixels (not just LayerPanel entries).

## File ownership
- `web/src/Map.tsx`
- `web/src/lib/vector_rendering.ts`
- `services/agent/src/grace2_agent/tools/fetch_nws_*.py` (if data issue)
- Tests
- `reports/inflight/job-0175-engine-20260608/`

## FROZEN
Single commit prefix `job-0175:`. Codified lessons.
