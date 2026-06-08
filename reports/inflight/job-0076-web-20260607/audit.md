# Audit: Map.tsx WMS overlay non-render diagnosis + fix + dark-theme toggle

**Job ID:** job-0076-web-20260607, **Sprint:** sprint-10 (the actual headline unblock), **Auditor:** Development Orchestrator, **Status:** assigned

## Task Assignment

**Specialist:** web (Opus per user direction — investigation + fix + bundled enhancement)

**Prerequisites (ALL APPROVED):**
- job-0068 (commit e5398b8): Map.tsx WMS source wiring + LayerURI bbox + zoom-to fitBounds + the `useRef<Set<string>>` source-ID tracker
- job-0070 + job-0074 + job-0075 — each produced a "headline" screenshot the user accepted as wired correctly but which **upon honest re-examination contained ZERO flood overlay pixels on the map area** (job-0075 agent confirmed the map-area pixel diff between 0074 and 0075 was 0.03%, "all in the UI layer-name text" — meaning the map area is byte-identical, no overlay was ever rendering)
- job-0072 (commit 876798b): ws.ts map-command routing + onMapCommand callback added to WsHandlers
- job-0074 (commit 344de30): App.tsx onMapCommand wiring closure

**SRS references** (narrow file loading only):
- None — this is a debugging + UX job, no SRS amendment.

**Required reads:**
- `web/src/Map.tsx` lines 1–250 — the full file; particularly `buildWmsTileUrl`, the `useRef<Set<string>>` tracker, and the `addSource`/`addLayer` calls in the session-state useEffect
- `web/src/App.tsx` — where the dark-theme toggle lands (mount + state)
- `reports/complete/job-0068-web-20260607/report.md` — the original wiring claim
- `reports/complete/job-0075-engine-20260607/report.md` — the honest finding "map area byte-identical between job-0074 and job-0075 screenshots"
- `reports/complete/job-0074-engine-20260607/evidence/server_side_fort_myers_FINAL.png` AND `reports/complete/job-0075-engine-20260607/evidence/wms_full_0075.png` — both prove WMS GetMap works server-side; if you curl the same URL the user's MapLibre would compose into a tile-request, you get real flood pixels

### Why this job exists

Since at least job-0066, every screenshot the orchestrator surfaced to the user has shown LayerPanel + LayerLegend + basemap with **zero flood overlay pixels on the map area**. The structural code path (Map.tsx adds source + layer; QGIS Server serves tiles; MapLibre composites) is INTENDED correct, but produces no visible raster. Multiple Sonnet agents reported "screenshot captured successfully" because they checked UI chrome (LayerPanel populated, LayerLegend visible) rather than pixel-level evidence on the map canvas. The orchestrator trusted those claims. The user caught the actual gap.

This job: diagnose, fix, and bundle a dark-theme toggle that will make verification trivially obvious in future (a blue flood overlay on a dark basemap is visually unambiguous; on the current light OSM basemap it can be confused with water features in the basemap).

### Part 1 — Diagnose the WMS overlay non-render (Opus-grade investigation)

Read `web/src/Map.tsx` completely. Focus on:

- `buildWmsTileUrl(wms_url)` — does it return a tile-URL template suitable for MapLibre? MapLibre's raster source `tiles:` array expects a URL where `{bbox-epsg-3857}` (or `{x}`, `{y}`, `{z}`) will be substituted per-tile. For a WMS source to work, the URL needs MORE than just `&BBOX={bbox-epsg-3857}` appended — verify whether `WIDTH={width}`, `HEIGHT={height}`, `SRS=EPSG:3857` (or `CRS=EPSG:3857` for WMS 1.3.0), `FORMAT=image/png`, `TRANSPARENT=true`, and `VERSION` are all present in what `buildWmsTileUrl` produces. The basemap source at `Map.tsx:40-90` (job-0025) is a working comparison.
- `addSource` call — does it use `type: "raster"`, `tileSize: 256`, and any `bounds: [...]` (which would cull tiles outside the Fort Myers bbox)? Are minzoom/maxzoom set?
- `addLayer` call — does it use `type: "raster"`, the correct `source: ...`, and any `paint: {"raster-opacity": ...}`? Is it added BEFORE or AFTER the basemap layer? MapLibre paints layers in the order they were added; if the basemap is added LAST, it covers the flood.

**Diagnostic priority list (likely culprits in order):**

1. **Missing WMS GetMap params in tile URL** — the WMS endpoint returns an XML error or empty for malformed requests; the tile request silently fails and MapLibre shows nothing
2. **Z-order:** flood layer added BEFORE the basemap (so basemap covers it)
3. **Tile request response is empty** — server-side returns nothing for the per-tile bbox (test by issuing one of the requests MapLibre would issue, e.g., zoom=11 tile at Fort Myers in EPSG:3857)
4. **CORS rejection on the per-tile request** — QGIS Server is CORS-fixed (job-0029) but maybe only for certain methods/origins
5. **MapLibre raster source bounds constraint** — if `bounds: [...]` is in the source spec and is wrong, tiles outside the bounds are never requested
6. **Multi-tile composite issue** — `tileSize: 256` requests 256×256 tiles; the Fort Myers extent is ~20km × 20km which at zoom-11 spans only ~1 tile diagonally; MapLibre may not request neighboring tiles correctly

**Diagnostic methodology:**

- Boot `cd web && npm run dev`, open the app in a headless Chromium via Playwright with `page.on("request", ...)` and `page.on("response", ...)` handlers logging EVERY HTTP request and response status
- Inject the existing dev-injection seam with a known-good WMS URL from job-0075 (`https://grace-2-qgis-server-.../ogc/wms?MAP=/mnt/qgs/grace2-sample.qgs&LAYERS=flood-depth-job-0075-demo`)
- Inject zoom-to(Fort Myers bbox)
- Wait for tile requests to fire
- For each WMS tile request: log the FULL URL + response status + response size
- If the requests are 200 OK + non-trivial bytes but the canvas still doesn't show the layer → MapLibre z-order or compositing issue
- If the requests are 400/404/error → WMS URL is malformed; show the diff between what MapLibre sent and what the WMS endpoint needs
- If no requests fire at all → MapLibre is culling the layer before the request (bounds, zoom range, source-spec issue)

Document the diagnosis precisely in the report.

### Part 2 — Fix the overlay render

Based on the diagnosis, land the minimum fix. Most likely shape:

- Expand `buildWmsTileUrl` to append `&WIDTH=256&HEIGHT=256&SRS=EPSG:3857&FORMAT=image/png&TRANSPARENT=true&VERSION=1.1.1` (or appropriate WMS version) before `&BBOX={bbox-epsg-3857}`
- Fix layer z-order: ensure flood layer is added AFTER basemap; pass an explicit "before-id" in `addLayer({...}, beforeId)` if MapLibre requires it
- If a CORS issue, surface as OQ-76-* and route to infra (CORS at QGIS Server) rather than working around it in the client

**Tests:**
- Unit/integration test: drive a synthetic session-state injection in Vitest + happy-dom; intercept fetch; assert WMS tile requests with the correct params
- ≥1 regression test that asserts `buildWmsTileUrl` produces a URL with ALL required WMS GetMap params

### Part 3 — Dark-theme toggle (BUNDLED per user direction)

Add a light/dark basemap toggle.

- **Dark source:** **CartoDB DarkMatter** vector tiles (free, CC-BY attribution required, no API key needed at low volume). URL: `https://{a-c}.basemaps.cartocdn.com/dark_all/{z}/{x}/{y}.png` (raster) OR vector style at `https://basemaps.cartocdn.com/gl/dark-matter-gl-style/style.json`. Prefer the vector style — sharper at all zooms.
- **Light source:** keep the current QGIS Server WMS basemap (`MAP=/mnt/qgs/grace2-sample.qgs&LAYERS=basemap-osm-conus`)
- **Toggle UI:** small button or icon in a top-right (or top-center) overlay over the map area. Click cycles light↔dark. Persist in localStorage under `grace2.theme` ("light" | "dark"; default "light").
- **Implementation:** when the toggle changes, swap the basemap source via `map.removeLayer("qgis-wms-basemap-layer-id")` + `map.removeSource("qgis-wms")` + `map.addSource("qgis-wms", {... new source ...})` + `map.addLayer({...new layer...}, "first-flood-layer-id")` — IMPORTANT: ensure the flood layer(s) end up ABOVE the basemap after the swap. Or use `setStyle()` if MapLibre supports a partial style swap cleanly.
- Attribution: CartoDB requires "© OpenStreetMap contributors © CARTO" in a corner. Add a small attribution control if MapLibre doesn't auto-pull it from the style.json.

### Part 4 — Verify with zoom-13 close-up screenshot

Once Parts 1+2+3 land:

1. Boot dev server
2. Inject session-state with the job-0075 WMS URL + Fort Myers bbox + opacity:0.9
3. Inject map-command(zoom-to, bbox)
4. **Programmatically zoom the map to zoom=13** (closer than zoom-11 default fitBounds) so the inundation pattern is visually large enough to see clearly
5. Screenshot light-mode result → `evidence/headline_light_FINAL.png`
6. Toggle to dark theme (click the toggle programmatically or via injected localStorage)
7. Wait for re-render
8. Screenshot dark-mode result → `evidence/headline_dark_FINAL.png`

**What both screenshots MUST show:** the blue flood-depth overlay clearly visible over the basemap at Fort Myers, NOT washed out, NOT identical to the basemap-only view. Use the WMS GetMap PNG from job-0075's evidence as a sanity reference for what the inundation pattern should look like (Caloosahatchee River + coastal lowlands).

If you can't get the overlay to appear, **DO NOT pretend success**. Document precisely what was diagnosed, what was tried, and what's blocking — surface as OQ-76-* and route accordingly.

### File ownership (exclusive)

- `web/src/Map.tsx` — diagnosis + fix + dark-theme source swap
- `web/src/App.tsx` — theme-toggle UI + state + localStorage
- `web/src/lib/style-presets.ts` — if needed for theme-specific style overrides (probably not)
- `web/src/Map.test.tsx` + `web/src/*.test.tsx` — tests
- `reports/inflight/job-0076-web-20260607/`

### FROZEN

- `web/src/ws.ts`, `web/src/contracts.ts` (job-0072)
- `web/src/LayerPanel.tsx`, `web/src/Chat.tsx`, `web/src/PipelineCard.tsx`, `web/src/components/LayerLegend.tsx`, `web/src/lib/style-presets.ts` (except if minor add needed)
- `services/`, `packages/`, `infra/`, `docs/`, `styles/`
- `reports/complete/**`

### Acceptance criteria

- [ ] **Diagnosis** documented precisely (what was wrong; cite file:line + the actual WMS URL MapLibre was sending vs what works)
- [ ] **Fix** lands; tile requests now return real flood pixels
- [ ] **Dark-theme toggle** wired with CartoDB DarkMatter (or equivalent); persists in localStorage; attribution added
- [ ] **Two zoom-13 screenshots captured** — light + dark — BOTH visibly showing the blue flood overlay over the basemap with the Caloosahatchee River + coastal inundation pattern matching the WMS GetMap tile
- [ ] Existing web test suite stays green (≥63); ≥2 new tests
- [ ] No edits to FROZEN paths
- [ ] Single commit

### Honest-disclosure note

The user direction 2026-06-07 was explicit: **don't claim success without pixel-level evidence on the map area**. If diagnosis reveals the issue is deeper than a Map.tsx fix (e.g., requires CORS infra change, server-side WMS endpoint change, or contract amendment), STOP and surface as OQ-76-* with full diagnosis — don't try to push a workaround. Opus was selected specifically because cascading silent failures here have caused the orchestrator to mislead the user three times already.
