# Audit: Layer rendering investigation + fix — single-tool layers not appearing on map

**Job ID:** job-0171-engine-20260608, **Specialist:** web (Opus)

## Why

Even single-tool single-shot dispatches that succeed server-side don't render on the map. User reports:
- "Show me radar over America" → layer in LayerPanel but NOT on map
- "Show me weather alerts across America" → same
- Even Fort Myers flood layer didn't render even though publish_layer succeeded server-side

job-0159 (Wave 4.6) added SESSION_HUB fan-out in ws.ts and claimed 325/325 tests pass + Playwright verify. BUT user keeps reporting same symptom in live testing.

## Scope

**Diagnose**: trace why the layer envelope reaches LayerPanel but doesn't reach Map.tsx's raster/vector source registration. Hypotheses:
A. SESSION_HUB fan-out works for `session-state` but not for the actual `add-layer` envelope
B. Map.tsx's session-state subscription doesn't re-fire raster/vector add when layers update
C. The raster path in Map.tsx (WMS source) only triggers on initial mount, not on incremental layer adds
D. Vector vs raster code path divergence — Wave 3.5 job-0139 added vector but maybe regressed raster
E. CartoDB DarkMatter basemap mounted AFTER session-state arrived, clobbering the layers

Capture diagnosis in `evidence/diagnosis.md` with file:line citations.

**Fix**: based on diagnosis, restore reliable layer rendering for BOTH raster (WMS) and vector (FlatGeobuf/GeoJSON) LayerURIs.

## Verify

Live: send "Show me radar over America" with restarted agent → NEXRAD WMS layer appears on map (transparent reflectivity overlay).

Also: "Show me weather alerts across America" → polygon overlay appears.

Also: rerun Fort Myers flood → flood depth raster renders on map post-publish.

## File ownership
- `web/src/Map.tsx`
- `web/src/ws.ts` (SESSION_HUB if changes needed)
- `web/src/App.tsx` (loaded_layers subscription)
- Tests
- `reports/inflight/job-0171-engine-20260608/`

## FROZEN
Single commit prefix `job-0171:`.
