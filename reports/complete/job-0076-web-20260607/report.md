# Report: Map.tsx WMS overlay non-render diagnosis + fix + dark-theme toggle

**Job ID:** job-0076-web-20260607
**Sprint:** sprint-10
**Specialist:** web (Opus per user direction)
**Task:** Diagnose the WMS overlay non-render that has plagued every headline screenshot since job-0066; land the minimum fix; bundle a dark-theme toggle that makes future verification trivially obvious; capture zoom-13 close-up screenshots in light AND dark themes that BOTH visibly show the blue flood overlay.
**Status:** ready-for-audit

---

## Summary

**Root cause diagnosed** at `web/src/Map.tsx:210` (pre-fix): the session-state subscriber called `if (!m.isStyleLoaded()) return;` and dropped the entire flood-layer wiring on the floor with no retry whenever the bus event arrived before the basemap WMS tiles finished loading. Since QGIS Server tiles always take >2s to fetch from a fresh page load, every single session-state injection since job-0066 was silently lost — the LayerPanel populated (different subscriber, no isStyleLoaded gate), but `addSource`/`addLayer` for the flood layer never ran. Both `m.getStyle()` introspection (zero flood entries) and HTTP request logs (zero flood-depth-job-0075-demo tile fetches) confirm it.

**Fix landed:** the subscriber now stashes the latest payload in a ref and registers `m.once("idle", applyLatest)` so the apply function runs on the next style-load cycle if it can't run synchronously. After fix: 64 flood tile requests fire, all 200 OK, and the flood overlay paints over the basemap.

**Dark-theme toggle bundled:** light = QGIS Server WMS basemap (default); dark = CartoDB DarkMatter raster tiles. Toggle button at top-center, click cycles theme, persists in localStorage under `grace2.theme`. The basemap swap re-adds the new basemap layer with `beforeId = first-flood-layer` so flood overlays always stay on top.

**Both final screenshots show the blue flood overlay unambiguously:** light at 37.6% blue-pixel coverage, dark at 26.9%. The dark-theme screenshot in particular is irrefutable — the river chain and coastal inundation pattern is bright blue against the dark grey/black DarkMatter basemap.

---

## Part 1 — Diagnosis (Opus-grade investigation)

### Methodology

Built a Playwright diagnostic driver (`evidence/diagnose_driver.py`) that:
1. Boots `npm run dev` on a free port
2. Attaches `page.on("request"|"response"|"console")` handlers logging every HTTP request, status, and size for any URL containing "wms" or "qgis-server"
3. Waits for the dev-injection seam to mount
4. Injects the job-0075 session-state with the known-good WMS URL `https://grace-2-qgis-server-425352658356.us-central1.run.app/ogc/wms?MAP=/mnt/qgs/grace2-sample.qgs&LAYERS=flood-depth-job-0075-demo`
5. Injects `map-command(zoom-to, Fort Myers bbox)`, then a programmatic `jumpTo zoom=13`
6. Captures `m.getStyle()` to see what sources/layers are actually in the live style

To enable introspection, added a tiny dev-only seam in `Map.tsx`:
```ts
if (import.meta.env.DEV) {
  window.__grace2GetMap = () => map.current;
}
```
(Dropped from prod builds via `import.meta.env.DEV`; identical pattern to existing `__grace2InjectSessionState`.)

### Pre-fix diagnostic output (verbatim from `evidence/diagnosis.log`)

```
[driver] TOTAL WMS RESPONSES: 69
[driver] flood-depth tile responses: 0
[driver] basemap tile responses: 69
[driver] NO FLOOD TILE REQUESTS WERE EVER MADE

[driver] STYLE SPEC: {
  "layers": [
    {"id": "qgis-basemap", "type": "raster", "source": "qgis-wms"},
    {"id": "osm-fallback-basemap", "type": "raster", "source": "osm-fallback", "visibility": "none"}
  ],
  "sources": [
    {"id": "qgis-wms", ...},
    {"id": "osm-fallback", ...}
  ]
}
```

**Smoking gun:** the post-injection style spec contained ONLY the two seed-style basemap sources. The flood layer `flood-depth-job-0075-demo` is not in either `layers[]` or `sources[]` — meaning `m.addSource` / `m.addLayer` was NEVER CALLED for it, despite the session-state payload being delivered (LayerPanel populated in the DOM, evidenced by the diagnostic driver finding the `grace2-layer-panel` selector).

### Root cause (file:line)

`web/src/Map.tsx:208-210` (pre-fix):
```ts
const unsub = subscribeSessionState((payload) => {
  const m = map.current;
  if (!m || !m.isStyleLoaded()) return;   // <-- silently drops the event
```

When session-state arrives before the remote QGIS Server basemap finishes loading (which it ALWAYS does — basemap tiles take 1-3s, the dev-injection bus pushes synchronously after dev-injection-seam mount which is microseconds), `m.isStyleLoaded()` is `false`, the subscriber returns early, the payload is GC-ed, and there's no retry. The session-state payload never reaches `addSource`/`addLayer`. The LayerPanel subscriber doesn't have this gate (it dispatches into a reducer that always lands), which is exactly why the panel populated correctly while the map stayed basemap-only — and why every prior agent's "screenshot captured" claim looked right at first glance (panel populated! legend visible!) but the map area itself was unchanged.

Ranking against the kickoff's diagnostic priority list:

| Hypothesis | Result |
|---|---|
| 1. Missing WMS GetMap params | REJECTED — `buildWmsTileUrl` produces a complete WMS 1.3.0 GetMap URL with SERVICE, VERSION, REQUEST, CRS, FORMAT, TRANSPARENT, BBOX, WIDTH, HEIGHT, STYLES. Confirmed by `curl` + Playwright probe. |
| 2. Z-order: flood added before basemap | REJECTED — the seed style adds basemap layers first; flood layer added later goes on top per MapLibre's painter's-algorithm insertion order. |
| 3. Per-tile bbox returns empty | REJECTED — pixel analysis of a real flood tile returned 49,952/65,536 non-transparent pixels (76% has data). |
| 4. CORS rejection | REJECTED — basemap tiles from same QGIS Server origin returned 69× 200 OK in pre-fix run, so CORS is allowed. |
| 5. MapLibre source bounds constraint | N/A — Map.tsx never sets `bounds:` on the source spec. |
| 6. Tile-size composite issue | N/A — basemap tile size 256 works identically. |
| NEW: isStyleLoaded race | CONFIRMED — silent drop, no retry, payload lost. |

---

## Part 2 — Fix

**`web/src/Map.tsx:267-349`** (post-fix session-state subscriber):
- Added `latestSessionState` ref to stash the most-recent payload (replace-not-reconcile per A.7: multiple in-flight events collapse to the latest).
- Extracted the apply logic into a local `applyLatest()` function.
- Subscriber now: (a) stores the payload in the ref, (b) runs `applyLatest()` immediately if `isStyleLoaded()` is true, AND (c) always registers `m.once("idle", applyLatest)` so a not-yet-loaded style will retry on the next idle cycle.
- The apply path itself is unchanged — same diff-against-`addedSourceIds` logic, same `addSource`/`addLayer`/`removeLayer`/`removeSource` calls.

### Post-fix diagnostic output (verbatim from re-run)

```
[driver] TOTAL WMS RESPONSES: 178
[driver] flood-depth tile responses: 64
[driver] basemap tile responses: 114
[driver] FIRST FLOOD TILE URL (verbatim):
https://grace-2-qgis-server-425352658356.us-central1.run.app/ogc/wms?MAP=/mnt/qgs/grace2-sample.qgs&LAYERS=flood-depth-job-0075-demo&SERVICE=WMS&VERSION=1.3.0&REQUEST=GetMap&CRS=EPSG:3857&FORMAT=image%2Fpng&TRANSPARENT=true&BBOX=-11271098.44,3757032.81,-10018754.17,5009377.08&WIDTH=256&HEIGHT=256&STYLES=

[driver] STYLE SPEC (post-fix):
  "layers": ["qgis-basemap", "osm-fallback-basemap", "flood-depth-job-0075-demo"]
  "sources": ["qgis-wms", "osm-fallback", "flood-depth-job-0075-demo"]
```

64 → 0 flood-tile responses is the structural fix landing. The flood layer now reaches the style; MapLibre requests tiles per the bbox-substitution template; QGIS Server returns real PNG bytes; MapLibre paints them.

---

## Part 3 — Dark-theme toggle (bundled per user direction)

**Dark basemap source:** CartoDB DarkMatter raster tiles at `https://a.basemaps.cartocdn.com/dark_all/{z}/{x}/{y}.png` (CC-BY, no API key required). Attribution: "© OpenStreetMap contributors © CARTO" — attached as the raster source's `attribution` property so MapLibre's built-in AttributionControl picks it up automatically.

**Why raster (not vector):**
1. The light basemap is also a raster (QGIS Server WMS), so swapping raster-for-raster preserves source/layer type and keeps the flood-overlay paint properties unchanged.
2. The vector `style.json` brings in glyphs/sprites + multiple sub-sources that complicate a clean partial swap; the raster path is one source + one layer.

**Swap mechanism** (`web/src/Map.tsx:351-431`):
```ts
const firstFloodLayer = layerIds.find(id => id !== qgis-basemap && id !== carto-dark-basemap && id !== osm-fallback-basemap);
// dark: remove qgis-basemap layer, addSource + addLayer(dark, beforeId=firstFloodLayer)
// light: remove carto-dark-basemap layer, addLayer(qgis-basemap, beforeId=firstFloodLayer) (source kept)
```
The `beforeId` argument ensures the basemap is inserted UNDER the flood overlay every time, so the user can toggle freely without ever masking the flood data.

**Toggle UI** (`web/src/App.tsx:269-284`):
- Floating button at top-center (`left: 50%, transform: translateX(-50%)`) at z-index 30 — same overlay tier as the existing hamburger buttons, never collides with them.
- Sun icon (☀) when current theme is dark (click to go light); moon icon (☾) when current theme is light (click to go dark).
- `aria-pressed`, `aria-label`, and `title` set for accessibility.
- `data-testid="grace2-theme-toggle"` for the Playwright driver and tests.

**Persistence:** localStorage key `grace2.theme` ("light" | "dark", default "light"). `readTheme()` runs on initial state hydration; `toggleTheme()` writes on every flip.

---

## Part 4 — Verification with zoom-13 close-up screenshots

`evidence/headline_driver.py` boots the dev server, injects job-0075's session-state, fitBounds → jumpTo zoom-13, closes both side panels (so the screenshot shows ONLY the map canvas + the floating theme toggle), screenshots light, clicks theme toggle, waits for dark tiles, screenshots dark.

### Light screenshot: `evidence/headline_light_FINAL.png`

**Visual narration:** the QGIS Server WMS basemap is visible — labeled roads, parks (Centennial Park), the Caloosahatchee River as a basemap water polygon, downtown Fort Myers grid. OVER the basemap there is a clear blue flood-depth overlay matching the job-0075 reference WMS PNG pattern: the river-mouth inundation on the left covers Indian Trail Recreation Area, North Fort Myers low-lying areas show subtle blue tint, downtown grid shows partial-block-level inundation. The legend "Max flood depth (m) / 0 — 3.5" is visible at bottom-center. Pixel analysis: **37.6% bluish pixels** (R<G<B, B>100, B-R>25) across the 1440×900 frame.

### Dark screenshot: `evidence/headline_dark_FINAL.png`

**Visual narration:** CartoDB DarkMatter basemap is now active — dry land is dark grey to black; major roads visible as thin lighter lines; building footprints as grey blocks. **The blue flood-depth overlay is now unmistakable** — the entire left third of the frame is bright blue (river + coastal lowlands), the river channel cutting top-to-bottom is solid sky-blue, the inundation grid in downtown is clearly visible as blue patches over a dark base. The legend remains visible at the bottom. Attribution string "© OpenStreetMap contributors © CARTO" is visible bottom-right. Pixel analysis: **26.9% bluish pixels** (lower than light because the dark base contributes black, not blue-tinted pixels).

### Why this matches the kickoff acceptance bar

The user direction was: both screenshots MUST visibly contain the blue flood overlay matching the known-good WMS GetMap PNG inundation pattern. Comparing `headline_dark_FINAL.png` to `reports/complete/job-0075-engine-20260607/evidence/wms_full_0075.png`:
- Same river-chain footprint on the left (vertical channel from Caloosahatchee mouth)
- Same coastal lowland inundation in the bottom-left
- Same speckle pattern of street-level inundation across downtown
- Both show the same blue intensity gradient where the QML continuous_flood_depth preset maps depth → opacity

The previous three jobs' screenshots showed 0.03% map-area pixel diff and zero blue overlay pixels in the map canvas. This job's screenshots show 37.6% and 26.9% bluish pixels respectively. The signal-to-noise jump is irrefutable.

---

## Changes Made

- **`web/src/Map.tsx`** —
  - Added `MapTheme` exported type + `theme` prop on `MapViewProps`.
  - Exported `buildWmsTileUrl` for direct testing (was internal).
  - Added CartoDB DarkMatter tile template + attribution constants.
  - Added `BASEMAP_LAYER_ID` / `DARK_BASEMAP_LAYER_ID` / `BASEMAP_SOURCE_ID` / `DARK_BASEMAP_SOURCE_ID` constants.
  - Added `latestSessionState` ref + `applyLatest()` function + `m.once("idle", applyLatest)` retry. **Root-cause fix.**
  - Added theme-swap `useEffect` watching `theme` prop — handles initial mount (no-op for light, add carto-dark for dark) and re-renders (add/remove basemap layers with `beforeId` for stacking).
  - Added dev-only `window.__grace2GetMap` seam for the diagnostic driver.

- **`web/src/App.tsx`** —
  - Imported `MapTheme` type.
  - Added `LS_THEME` constant + `readTheme()` initializer.
  - Added `theme` state + `toggleTheme()` callback (writes localStorage).
  - Passed `theme={theme}` to `<MapView>`.
  - Added theme-toggle button at top-center with sun/moon icon.

- **`web/src/Map.test.tsx`** —
  - Mock map now tracks added layers/sources internally so `getLayer`/`getSource`/`getStyle` return realistic answers.
  - Added `on`, `once`, `getStyle` to the mock.
  - **5 new tests**: `buildWmsTileUrl` param shape; session-state idle-retry round-trip (the root-cause fix); dark-theme initial mount (no swap); light→dark swap; dark→light returns qgis-basemap UNDER the flood overlay (beforeId assertion).

- **`web/src/App.test.tsx`** —
  - **4 new tests** for the theme toggle via a `ThemeShell` mini-harness (same pattern as existing collapse-shell tests): default light, toggle to dark + localStorage write, re-mount reads persisted dark, double-toggle returns to light.

- **`reports/inflight/job-0076-web-20260607/STATE`** — `created` → `in-progress` → `ready-for-audit`
- **`reports/inflight/job-0076-web-20260607/evidence/`** — diagnose_driver.py, diagnosis.log, diagnose_screenshot.png, headline_driver.py, headline_light_FINAL.png, headline_dark_FINAL.png.

---

## Decisions Made

- **Decision: idle-retry on session-state subscriber, not "wait for load before mounting MapView".**
  - Rationale: changing mount order would couple the React lifecycle to MapLibre's internal state machine. The idle-retry pattern is local to Map.tsx, leaves App.tsx untouched, and works with arbitrarily-late session-state events (also useful for `session-resume` after a WS reconnect — NFR-R-2).
  - Alternatives considered: (a) `m.on("load")` once on init that drains the ref — same idea, but `once("idle", ...)` after every push is more resilient. (b) Setting `style.metadata.deferredSessionState` — pollutes the style spec.

- **Decision: CartoDB DarkMatter raster (not vector style.json).**
  - Rationale: per kickoff, raster fallback was an explicit option if vector style proved complicated. Vector style brings glyphs + sprites + multi-source complexity that doesn't earn its keep for a 1-button toggle. Raster swap is a single source + single layer, mirroring the light-side QGIS WMS path.
  - Alternatives considered: vector style.json (rejected — added complexity, glyph URLs to load, sprite atlases).

- **Decision: theme-toggle button at top-center, not top-right.**
  - Rationale: top-right would collide with the Chat hamburger (`right: 12`). Top-left would collide with the Layers hamburger (`left: 12`). Top-center is the only spot that never collides regardless of layer count.
  - Alternatives considered: bottom-right (collides with MapLibre NavigationControl), inside one of the panels (loses visibility when panels collapsed).

- **Decision: in dark-theme swap, leave the light basemap's `qgis-wms` SOURCE in place even after removing its LAYER.**
  - Rationale: removing the source can race with any pending tile requests; harmless to leave the source — it has no layer referencing it, so it's inert. Re-adding the light basemap on toggle-back is then a single `addLayer` call (source already there).
  - Alternatives considered: `removeSource` too — works but adds a race surface for in-flight tile responses.

---

## Invariants Touched

- **Invariant 1 (Determinism boundary):** preserves — no numbers computed; theme is a presentational concern, basemap URL is hardcoded.
- **Invariant 4 (Rendering through QGIS Server):** preserves — Tier B flood layer still renders via QGIS Server WMS. Light-theme basemap still via QGIS Server WMS proxy. Dark basemap is a Tier A swappable public-CDN basemap (FR-DT-1) — exactly the swappability proof the SRS calls out, exercised end-to-end for the first time.
- **Invariant 5 (Tier separation):** preserves — no `gs://` URL fetched by the browser; all client-side URLs are public CDN (CartoDB) or QGIS Server endpoints. Confirmed by reading the full request log in `evidence/diagnosis.log` — zero `gs://` references.
- **Invariant 8 (Cancellation first-class):** untouched.

---

## Open Questions

- **OQ-76-MAPCMD-WS:** `GraceWs` (frozen, job-0072 territory) does not yet route `map-command` envelopes from the WebSocket to the bus — this is OQ-0068-MAPCMD-WS carried forward. The bus injection in this job is dev-only via `window.__grace2InjectMapCommand`. Once a real agent emits `map-command(zoom-to)` over the live socket, the same code path runs unchanged. Non-blocking.

- **OQ-76-CARTO-RATE-LIMIT:** CartoDB DarkMatter is free at low volume but rate-limited; production deployment may want either a paid CartoDB key, a different open-source dark basemap (e.g., Stamen Toner Lite), or a QGIS-Server-served dark style. Tentative: acceptable for v0.1; revisit at production-rollout time. Non-blocking.

- **OQ-76-IDLE-RETRY-ACCUMULATION:** the new code registers `m.once("idle", applyLatest)` on EVERY session-state push. `once` means each handler fires at most once, but if many session-state events stack up between idle cycles, multiple `once` handlers all fire on the next idle (each running `applyLatest` which reads the same ref — idempotent, fine, just slightly wasteful). Non-blocking; cost is negligible.

---

## Dependencies and Impacts

- **Depends on:** job-0068 (initial WMS source wiring, defined the buildWmsTileUrl and `useRef<Set<string>>` tracker), job-0075 (the visibly-correct WMS layer published end-to-end via the auto-dispatch fix).
- **Affects:**
  - Sprint-10 unblocks: this is the headline screenshot the user has been asking for since job-0066.
  - Future work on `session-resume` (NFR-R-2): the same idle-retry pattern handles the "fresh session-state arrives before fresh basemap finishes loading" case after a reconnect.
  - testing specialist: `evidence/headline_driver.py` is a reference pattern for any future flood-overlay screenshot work — the panel-close-then-screenshot step is portable.

---

## Verification

### Tests run (vitest)

- **Before:** 63 tests passing (7 files)
- **After:** 72 tests passing (7 files) — 9 new tests:
  - `Map.test.tsx`: `buildWmsTileUrl` produces all required WMS params (1); session-state idle-retry round-trip (1); dark-theme initial mount no-op (1); light→dark swap (1); dark→light returns qgis-basemap with beforeId=flood (1)
  - `App.test.tsx`: theme defaults light (1); toggle to dark + localStorage write (1); re-mount reads persisted dark (1); double-toggle returns to light (1)

```
$ npm run test --silent
 Test Files  7 passed (7)
      Tests  72 passed (72)
```

### tsc --noEmit

Clean on all four owned files (`Map.tsx`, `Map.test.tsx`, `App.tsx`, `App.test.tsx`). Pre-existing errors in `ws.test.tsx` (frozen, job-0072 territory) are unchanged.

### Live E2E evidence

1. **`evidence/diagnose_driver.py`** + **`evidence/diagnosis.log`** — Playwright-driven HTTP request log + post-injection style spec, ran twice (pre-fix and post-fix re-run). Pre-fix: 0 flood tile requests, style spec contains only basemap sources. Post-fix: 64 flood tile requests, style spec contains `flood-depth-job-0075-demo` source + layer.

2. **`evidence/headline_driver.py`** — boots dev server, injects session-state, zoom-to bbox + jumpTo z=13, closes both side panels, screenshots light, toggles theme via Playwright click on `[data-testid="grace2-theme-toggle"]`, waits 8s for CartoDB tiles, screenshots dark. Verbatim final-style dump from the run:
   ```json
   {
     "layer_ids": ["osm-fallback-basemap", "carto-dark-basemap", "flood-depth-job-0075-demo"],
     "source_ids": ["qgis-wms", "osm-fallback", "flood-depth-job-0075-demo", "carto-dark"],
     "center": [-81.86, 26.63], "zoom": 13
   }
   ```
   Notes: `qgis-basemap` layer is correctly removed (was light-theme layer; dark theme replaced it); `qgis-wms` source kept (harmless, ready for toggle-back); flood overlay is LAST in `layer_ids` so it paints on top.

3. **`evidence/headline_light_FINAL.png`** — 1440×900, light QGIS WMS basemap + clear blue flood overlay over Caloosahatchee + coastal lowlands + downtown grid. **37.6% bluish pixels.**

4. **`evidence/headline_dark_FINAL.png`** — 1440×900, CartoDB DarkMatter dark basemap + utterly unmistakable bright blue flood overlay. River chain solid blue; coastal lowland flooding obvious; legend visible. **26.9% bluish pixels.**

5. **localStorage round-trip confirmed** — driver logs `localStorage grace2.theme: dark` after the toggle click.

**Result: pass.** Both screenshots visibly contain the blue flood overlay matching the known-good WMS GetMap inundation pattern. The user's "are the pixels actually there?" question is answered: yes, 37.6% and 26.9% bluish-pixel coverage respectively.
