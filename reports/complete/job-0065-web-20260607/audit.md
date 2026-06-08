# Audit: UI tweak #2 — flood-depth colorbar + hide-empty LayerPanel + collapse toggles

**Job ID:** job-0065-web-20260607, **Sprint:** sprint-09 (Stage C UI tweaks; gates Stage D Playwright), **Auditor:** Development Orchestrator, **Status:** assigned

## Task Assignment

**Specialist:** web

**Prerequisites:**
- job-0025 (APPROVED): MapLibre basemap + LayerPanel + App shell.
- job-0062 (APPROVED): publish_layer + `styles/continuous_flood_depth.qml` continuous Blues ramp 0–3.5 m.
- `docs/decisions/layer-emission-contract.md` (ADOPTED): session-state.loaded_layers is canonical; client renders from it.

**SRS references** (narrow file loading only):
- `docs/srs/03-functional-requirements.md` FR-WC (Web Client requirements) + FR-MP-6 (Case UX flow — informs the panel collapse + hide-when-empty discipline)
- DO NOT load `docs/SRS_v0.3.md` monolith.

**Required reads:**
- `web/src/LayerPanel.tsx` — current layer panel
- `web/src/Map.tsx` — MapLibre container; you'll dock the colorbar overlay relative to this
- `web/src/App.tsx` — three-pane shell where the panels and map live; you'll add collapse toggles
- `styles/continuous_flood_depth.qml` (from job-0062) — the QML you're mirroring with the client-side colorbar stops

### Why this job exists

User direction 2026-06-07:
- "render the map raster and also the key (gradient bar) like in the matplotlib if possible, we would put this bar at the bottom of the screen in between both panels centered on the map horizontally"
- "when no layers are loaded we should hide the layers panel until something is loaded in"
- "have a collapse button for both panels so the user can see the map better"

Three tweaks bundled because they're file-disjoint from job-0064's chat work and tightly cohesive in the map/panels chrome.

### Scope

1. **`web/src/components/LayerLegend.tsx` (NEW)** — colorbar component:
   - Horizontal gradient bar docked at the bottom of the map area (between the two side panels)
   - Mirrors the QML's color ramp: Blues 0→3.5 m (light-blue → dark-blue) with nodata transparent
   - Min label "0 m" at left; max label "3.5 m" at right; midpoint optional
   - Title above the bar: the layer's `style_preset` mapped to a human label (e.g., `continuous_flood_depth` → "Max flood depth (m)")
   - Client-side preset registry: a small `web/src/lib/style-presets.ts` (NEW) that has the gradient stops for the known presets baked in. For now just `continuous_flood_depth`. If a layer has an unknown preset, the legend hides.
   - Render position: absolute-positioned over the map, bottom-center, narrow margin from the bottom edge, semi-transparent background so the map shows through subtly.
   - Multiple layers loaded: render the legend for the topmost continuous-raster layer only (per the layer order in session-state). If no continuous-raster layers are loaded, the legend hides.

2. **`web/src/LayerPanel.tsx`** — hide-when-empty:
   - When `loaded_layers.length === 0`, the panel is hidden entirely (return null from render).
   - When at least one layer is loaded, the panel renders as it does today.
   - Test: layers list goes 0 → 1 → 0 → panel shows up and disappears accordingly.

3. **`web/src/App.tsx`** — collapse toggles on both side panels:
   - Each side panel has a small chevron button on its inward edge (left panel: chevron on right edge points left when expanded, right when collapsed; right panel: mirror).
   - Click toggles a collapsed state. When collapsed, the panel is reduced to a thin strip (with the chevron still clickable to re-expand) and the map area expands accordingly.
   - Persistence: collapse state persists in `localStorage` so reloads remember. Use keys like `grace2.leftPanelCollapsed` and `grace2.rightPanelCollapsed`.
   - The Layer Legend (from item 1) repositions to stay centered horizontally over the (now-larger) map area; CSS flex / grid should handle this automatically if the legend is positioned relative to the map container.

4. **Tests**:
   - Unit: `LayerLegend` renders when a continuous-raster layer is loaded with a known preset; hides when no matching layer.
   - Unit: `LayerPanel` returns null when `loaded_layers.length === 0`.
   - Unit: Collapse toggle updates state + localStorage; re-mount reads from localStorage.
   - Integration: drive a session-state envelope through the bus; assert the layer panel appears, the legend renders.

5. **Verification**:
   - `npm run test` (whatever the web test runner is)
   - `npm run dev` + manually load a session-state with one continuous-raster layer → screenshot the chrome
   - `reports/inflight/job-0065-web-20260607/evidence/map_with_legend.png` showing colorbar
   - `reports/inflight/job-0065-web-20260607/evidence/panels_collapsed.png` showing both panels collapsed
   - `reports/inflight/job-0065-web-20260607/evidence/empty_layers_hidden.png` showing the panel hidden when no layers

### File ownership (exclusive)
- `web/src/components/LayerLegend.tsx` (NEW)
- `web/src/lib/style-presets.ts` (NEW)
- `web/src/LayerPanel.tsx` — hide-when-empty conditional only
- `web/src/Map.tsx` — small additions to host the legend overlay (or via App.tsx layout — whichever is cleaner)
- `web/src/App.tsx` — collapse toggles + localStorage
- `web/src/*.test.tsx` — new + updated tests
- `reports/inflight/job-0065-web-20260607/`

### FROZEN
- `web/src/Chat.tsx` (concurrent job-0064 owns)
- `web/src/PipelineStrip.tsx` (concurrent job-0064 owns)
- `web/src/ws.ts` — WS layer
- `web/src/contracts.ts` — generated
- `web/src/main.tsx`
- All non-web/, all services/, all packages/, all infra/, all docs/

### Acceptance criteria
- [ ] `LayerLegend` colorbar renders when a layer with the `continuous_flood_depth` preset is loaded
- [ ] Legend stays bottom-centered over the map area (responds to panel collapse state)
- [ ] `LayerPanel` hides when `loaded_layers.length === 0`; shows when ≥1 layer
- [ ] Both side panels have collapse toggles; state persists in localStorage
- [ ] Web tests pass
- [ ] Screenshots captured (legend, collapsed panels, empty hidden)
- [ ] No edits to FROZEN paths
- [ ] Single commit
