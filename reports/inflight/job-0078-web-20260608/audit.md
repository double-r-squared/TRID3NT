# Audit: OQ-76-MAP-ALIGNMENT — flood overlay alignment + rotation + zoom mismatch

**Job ID:** job-0078-web-20260608, **Sprint:** sprint-11 Stage 1 (parallel with hillshade), **Auditor:** Development Orchestrator, **Status:** assigned

## Task Assignment

**Specialist:** web

**Prerequisites (ALL APPROVED):**
- job-0076 (commit 96e0060): Map.tsx WMS overlay non-render fixed via idle-retry pattern; light + dark themes confirmed rendering blue flood pixels (37.6% / 26.9% bluish)
- job-0075's COG at `gs://grace-2-hazard-prod-runs/01KTJX71NKGDMXB9TN0DV75JWK/flood_depth_peak.tif` (EPSG:32617 verified) + WMS URL `https://grace-2-qgis-server-.../ogc/wms?MAP=/mnt/qgs/grace2-sample.qgs&LAYERS=flood-depth-job-0075-demo`
- job-0072 (commit 876798b): ws.ts map-command routing + onMapCommand callback

**SRS references:** None — debugging job.

**Required reads:**
- `web/src/Map.tsx` — full file; particularly `buildWmsTileUrl`, the `useRef<Set<string>>` source-id tracker, addSource/addLayer calls, the idle-retry pattern from job-0076, the theme-swap logic for dark mode
- `reports/complete/job-0076-web-20260607/report.md` — the root cause diagnosis + idle-retry fix details
- `reports/complete/job-0076-web-20260607/evidence/headline_light_FINAL.png` AND `headline_dark_FINAL.png` — both show the misalignment/rotation/zoom issue the user flagged 2026-06-08

### Why this job exists

User direction 2026-06-08 after seeing job-0076's screenshots: *"the alignment to the map is off and rotation is also off and maybe zoom too by the looks of it"*. The blue flood overlay now visibly renders (job-0076 fixed the silent-bail) but doesn't geographically line up with the basemap features at zoom-13 — the overlay appears to be:
- Offset horizontally/vertically from where Fort Myers actually is on the basemap
- Possibly rotated relative to north-up
- Possibly at the wrong zoom-level scale (tiles being requested at a different effective zoom than what's visible)

The server-side WMS GetMap returns a correctly-georeferenced PNG (proven by `reports/complete/job-0075-engine-20260607/evidence/wms_full_0075.png` — that tile when curled directly shows the Caloosahatchee River inundation pattern in the right place). So the bug is **client-side**: how MapLibre constructs per-tile WMS requests, OR how it composites the response onto the basemap canvas.

### Scope — 3-stage investigation + fix

#### Part 1 — Diagnose precisely

Boot `cd web && npm run dev`. Drive the dev UI via Playwright with `page.on("request", ...)` instrumentation logging EVERY WMS tile request URL.

For a session-state injection with the job-0075 WMS URL at zoom-13:
- **Log each WMS request URL** that MapLibre fires for the flood layer
- **Log the basemap WMS URL** for the same tile (for comparison; the basemap clearly renders in the right place)
- **Compare BBOX values** between flood-layer tile requests and basemap tile requests at the same MapLibre tile coordinate — they should be IDENTICAL if both are projected through the same EPSG:3857 grid. If they differ, that's the alignment bug.
- **Inspect the WMS response** for one or two tiles — what's the actual geographic extent of the returned image? Compare to expected.

Likely root causes ranked:
1. **WMS URL missing CRS for the response interpretation** — if the tile URL says `SRS=EPSG:3857&BBOX=<3857-coords>` but the server reprojects from the layer's native EPSG:32617 to EPSG:3857 with an off-by-one offset
2. **MapLibre `bounds: [...]` on the source mismatched** — if `bounds` is set to gs://...32617 coords but MapLibre interprets them as EPSG:4326 degrees, the source is constrained to the wrong geographic region
3. **Tile pixel grid offset** — `tileSize: 256` vs basemap source `tileSize` (verify they match); MapLibre composites tiles assuming pixel-coordinate alignment
4. **WMS VERSION mismatch** — WMS 1.3.0 axis-order is (lat, lon) for EPSG:4326 but (x, y) for EPSG:3857; WMS 1.1.1 is (lon, lat). If buildWmsTileUrl uses VERSION=1.3.0 but the basemap uses 1.1.1, there's an axis-order asymmetry
5. **Geographic reprojection edge case** — the flood layer in EPSG:32617 (UTM 17N) reprojected to EPSG:3857 (Web Mercator) at a small extent introduces sub-meter scale errors that look like rotation at zoom-13

Capture diagnosis as `evidence/diagnosis.md` with: (a) the actual flood-layer WMS URL MapLibre fires for one tile; (b) the basemap WMS URL for the same tile; (c) any URL parameter difference; (d) curl evidence comparing the two responses.

#### Part 2 — Land the minimum fix

Based on diagnosis, fix it. Likely candidates:
- Force WMS VERSION=1.1.1 (or 1.3.0) consistently across flood + basemap if axis-order is the issue
- Remove or correctly-format the `bounds:` constraint on the raster source if it's mis-set
- Match `tileSize` between flood + basemap sources
- Set `SRS` vs `CRS` parameter correctly per WMS version

Tests: Vitest with happy-dom + intercepted fetch; assert per-tile WMS URL params + ordering match the working basemap pattern.

#### Part 3 — Verify with side-by-side screenshots

Playwright dev-injection screenshot at zoom-13, BOTH light and dark themes. Save to `evidence/`:
- `aligned_light.png` — flood overlay aligned with basemap features
- `aligned_dark.png` — same, dark theme

**Visually verify** the Caloosahatchee River inundation pattern in the screenshot matches the position of the river on the basemap. If not aligned, document precisely what's still off and route as OQ-78-* — don't claim success without confirming.

### File ownership (exclusive)

- `web/src/Map.tsx` — diagnosis + fix
- `web/src/Map.test.tsx` — additive tests
- `reports/inflight/job-0078-web-20260608/`

### FROZEN

- `web/src/App.tsx`, `web/src/LayerPanel.tsx`, `web/src/Chat.tsx`, `web/src/PipelineCard.tsx`, `web/src/components/LayerLegend.tsx`, `web/src/ws.ts`, `web/src/contracts.ts`, `web/src/lib/style-presets.ts`
- All services/, packages/, infra/, docs/, styles/
- `reports/complete/**`

### Acceptance criteria

- [ ] Diagnosis cited precisely with file:line + concrete URL comparison evidence
- [ ] Fix lands; tests stay 72+/72+; ≥1 new regression test
- [ ] **Two zoom-13 screenshots captured** (light + dark) — flood overlay visibly aligned with basemap features (Caloosahatchee River inundation matches the river on basemap)
- [ ] **No edits to FROZEN paths**
- [ ] Single commit

### Honest disclosure

If diagnosis reveals the issue is server-side (QGIS Server reprojection settings) rather than client-side, surface as OQ-78-* and route to infra. Don't try to hack a client-side workaround for a server-side bug — the user's been bitten by silent failures enough this sprint.
