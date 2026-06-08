# Audit: UI correction — panels overlay above full-viewport map + hamburger collapse + raster wiring + zoom-to

**Job ID:** job-0068-web-20260607, **Sprint:** sprint-10 (headline opener), **Auditor:** Development Orchestrator, **Status:** assigned

## Task Assignment

**Specialist:** web (cross-file: also touches one agent-side emit + one tiny contracts additive)

**Prerequisites (ALL APPROVED):**
- job-0064-web (commit cec1071): chat-inline pipeline cards; PipelineStrip deleted
- job-0065-web (commit 485ed93): LayerLegend + collapse toggles + hide-when-empty + style-presets registry
- job-0066-testing (commit 142146e): Playwright fixtures + dev-injection seams verified
- job-0062-engine (commit f202a31): publish_layer + LayerURI returns with style_preset
- `docs/decisions/layer-emission-contract.md`: session-state.loaded_layers canonical; map-command for transient verbs (including `zoom-to`)
- This kickoff is informed by the **2026-06-07 workflow synthesis** (wtqemo22e) — the 5-change fix-plan + the user's clarifications (hamburger same side as panel; not an overhaul, "going back to map underneath panels/collapse icon on top").

**SRS references** (narrow file loading only):
- `docs/srs/A-websocket-protocol.md` A.7 (replace-not-reconcile) + the MapCommand Literal at `packages/contracts/src/grace2_contracts/ws.py:406-417`
- `docs/srs/03-functional-requirements.md` FR-WC (web client UX)
- DO NOT load `docs/SRS_v0.3.md` monolith.

**Required reads (in order — these are tight specific reads, not whole-file):**
1. `web/src/App.tsx` lines 1–250 — current flex-row + chevron pattern
2. `web/src/Map.tsx` lines 1–155 — current MapLibre setup (basemap only; NO source-from-loaded_layers subscription)
3. `web/src/LayerPanel.tsx` lines 195–290 — hide-when-empty + initialLayers + onLayersChange prop
4. `web/src/Chat.tsx` lines 250–270 — existing self-positioning + cancel button location
5. `web/src/components/LayerLegend.tsx` — current bottom-center absolute positioning
6. `packages/contracts/src/grace2_contracts/ws.py:342-433` — LoadLayerArgs + MapCommand
7. `packages/contracts/src/grace2_contracts/execution.py` — LayerURI shape (you'll add `bbox`)
8. `services/agent/src/grace2_agent/pipeline_emitter.py:413-440 + 517` — add_loaded_layer + auto-emit gate
9. `services/agent/src/grace2_agent/workflows/model_flood_scenario.py` — where the LayerURI is constructed (to set the bbox field)

### Why this job exists (binding context)

The sprint-9 deliverables (job-0064 + 0065) shipped a flex-row split-pane layout where the map is sandwiched between width-taking side panels. The user's actual mental model is different and we are correcting back to it: a full-viewport MapLibre map underneath, with panels as `position:absolute` overlays floating on top; collapsed = panel fully hidden + a hamburger icon appears on the SAME side as the panel (Layers TL, Chat TR); LayerPanel mounts only when `loaded_layers` is non-empty; raster overlays appear on the map automatically when `loaded_layers` populates; camera zooms to the layer's bbox when a layer lands.

**Architectural anchor**: per Invariant 4 + the layer-emission-contract.md decision, the client never styles map data. QGIS Server does all rendering. Map.tsx merely registers WMS URLs as MapLibre raster sources — same pattern it already uses for the basemap (`Map.tsx:40`). The "raster wiring" is small and canonical, not custom rendering.

### Scope — 5 concrete changes (in tackle order)

**Order: 4 → 5 → 3 → 1 → 2** (most fundamental → cosmetic). Reasoning: Map raster wiring (#4) is the prerequisite for visible raster overlay; zoom emission (#5) completes the load→camera contract; conditional mount (#3) removes the empty-tab bug; layout (#1) removes the flex seam; hamburger (#2) is final polish.

#### 4. `web/src/Map.tsx` — WMS source from `loaded_layers` (NEW — the most fundamental gap)

Today Map.tsx has zero subscription to session-state and zero `addSource`/`addLayer` calls beyond the basemap. Add:

- Accept new props (the bus pattern from App.tsx): `subscribeSessionState` + `subscribeMapCommand`
- A `useRef<Set<string>>(new Set())` of added source IDs (must survive effect re-runs, NOT closure-local)
- A `useEffect` subscribing to session-state. On each update, diff `loaded_layers` against the ref:
  - For each NEW layer (id not in ref): `map.addSource(layer_id, {type: "raster", tiles: [<wms_url with BBOX={bbox-epsg-3857}&WIDTH=256&HEIGHT=256&SRS=EPSG:3857&FORMAT=image/png>], tileSize: 256})` + `map.addLayer({id: layer_id, type: "raster", source: layer_id, paint: {"raster-opacity": layer.opacity ?? 1}})`; add to ref
  - For each layer in ref but NOT in current loaded_layers: `map.removeLayer + map.removeSource`; delete from ref (replace-not-reconcile per A.7)
  - For each layer in BOTH: update opacity / visibility if changed (`map.setPaintProperty(id, "raster-opacity", ...)` + `map.setLayoutProperty(id, "visibility", visible ? "visible" : "none")`)
- The WMS URL from `loaded_layers[].uri` is the canonical-form QGIS Server URL ending in `&LAYERS=<layer-id>` (per job-0062's publish_layer output); the BBOX placeholder is MapLibre's `{bbox-epsg-3857}` substitution
- ProjectLayerSummary contract lacks `opacity` / `z_index` formally — fixture carries them but contract doesn't; use `?? 1` fallback (the schema cleanup is job-0070)

#### 5. Zoom-to-bbox: agent emit + Map.tsx handler

**Agent side (small surface in pipeline_emitter):**

- Add optional `bbox: tuple[float, float, float, float] | None = None` to `LayerURI` in `packages/contracts/src/grace2_contracts/execution.py` (pydantic; tuple of 4 floats; null default). JSON Schema re-export.
- In `pipeline_emitter.py:413-440 add_loaded_layer`: after `emit_session_state()`, if the LayerURI carries a non-null bbox, also call `emit_map_command("zoom-to", {bbox: [minLon, minLat, maxLon, maxLat]})`. The emit_map_command method may not exist yet — add it mirroring emit_session_state. Use the `MapCommandPayload` shape from `ws.py:420-433` (command="zoom-to", args is a dict with bbox).
- In `services/agent/src/grace2_agent/workflows/model_flood_scenario.py`: when constructing the final LayerURI (after publish_layer wms_url substitution), populate `bbox` from the workflow's bbox parameter (the Fort Myers bbox `[-81.91, 26.55, -81.75, 26.69]`).

**Client side (Map.tsx):**

- Subscribe to `subscribeMapCommand` in a useEffect
- On `case "zoom-to"`: `map.fitBounds([[minLon, minLat], [maxLon, maxLat]], {padding: 40, duration: 1200})`
- Other MapCommand verbs (set-layer-visibility, set-layer-opacity, set-layer-order, set-temporal-config, start-animation, stop-animation, invalidate-tiles) → for now, route to a no-op handler with a `console.warn("MapCommand <verb> not yet implemented")`. Per the layer-emission-contract, these are mostly deferred (LayerPanel handles layer-CRUD via session-state) but `zoom-to` and `invalidate-tiles` and the animation verbs WILL be needed eventually.

#### 3. `web/src/App.tsx` — conditional mount of left slot

Today (per job-0065): the App-level wrapper for the left slot mounts unconditionally; LayerPanel returns null when empty; the wrapper's chevron + background still render → user sees the "empty white tab with an arrow" bug.

Change:
- Lift `layers` state to App.tsx (job-0065 partially did this with `onLayersChange` callback at `App.tsx:198`; drop the callback in favor of App owning a `bus.subscribeSessionState` effect that updates `layers` directly).
- Gate the entire left panel container render: `{layers.length > 0 ? <LayerPanel initialLayers={layers}/> : null}`.
- Pass `initialLayers={layers}` (already supported at `LayerPanel.tsx:199–204`) so the panel rehydrates on remount.
- Right slot (Chat) stays unconditional — chat is the only way to *request* layers, so it must always be reachable.

#### 1. `web/src/App.tsx` — flex-row → overlay (the layout reversion)

Today: three siblings in a flex-row (left slot + map + right slot) share viewport width.

Change to overlay pattern:
- Outer wrapper: `<div style={{position: "fixed", inset: 0}}>` (full viewport)
- Inside the wrapper, in DOM order: `<Map />` first (full-bleed: `width: 100%, height: 100%`), then `<LayerPanel />` (or null per #3), then `<Chat />`, then `<LayerLegend />` (already self-positioned at LayerLegend.tsx:78)
- Each panel renders `position: absolute; top: 0; bottom: 0; left: 0` (LayerPanel) or `right: 0` (Chat); fixed width (e.g., 320px each)
- Delete `COLLAPSED_WIDTH` constant at `App.tsx:58` (no more 28px strip)
- Z-index: legend 10, panels 20, hamburgers 30 (per workflow synthesis recommendation)

#### 2. `web/src/App.tsx` — chevron → hamburger (final polish)

Today: chevron buttons live on the inward edge of each panel slot; when collapsed, the chevron stays visible on a 28px strip.

Change:
- Delete the in-slot chevron buttons
- Add in-panel `×` close buttons in `LayerPanel` header + `Chat` header (so the user can close from inside the panel)
- When `leftCollapsed === true` (or `layers.length === 0` per #3): render a `<button class="hamburger" aria-label="Show layers" style={{position: "absolute", top: 12, left: 12, ...}}>` as a sibling of the map (NOT inside the panel slot). Click → `setLeftCollapsed(false)`. Hamburger disappears when panel is open. Hit target ≥ 40×40. `aria-expanded`, `aria-controls`, focus-visible ring, focus management.
- Symmetric for `rightCollapsed`: hamburger at `top: 12, right: 12` (Chat icon).
- Same-side-as-panel discipline per user direction 2026-06-07.
- localStorage keys at `App.tsx:46-47` unchanged (`grace2.leftPanelCollapsed`, `grace2.rightPanelCollapsed`).
- Note: if `layers.length === 0` AND `leftCollapsed === false`, the LEFT side has NEITHER the panel NOR the hamburger (because there are no layers to display) — the hamburger is layers-specific. This is per the user direction "when no layers are loaded we should hide the layers panel until something is loaded in". For the Chat side, the panel + hamburger pattern works as designed (chat is always available).

### Live verification (dev-injection)

Use the existing `window.__grace2InjectSessionState` + `window.__grace2InjectPipelineState` + `window.__grace2InjectMapCommand` hooks (registered by Chat.tsx + App.tsx at lines 139-150). Drive sequences:

1. **Baseline**: app mounted, no injection. Map full-viewport. Chat panel visible (right side). LayerPanel NOT mounted (no layers). Layers hamburger NOT visible (no layers). Just map + chat overlay + the LayerLegend hidden too. Screenshot.

2. **Layer-arrival**: inject session-state with `loaded_layers: [{layer_id: "flood-demo", uri: "<existing QGIS Server WMS URL — use basemap-osm-conus as substitute per job-0066 pattern since OQ-67 worker rebuild is pending>", style_preset: "continuous_flood_depth", visible: true, role: "primary", bbox: [-81.91, 26.55, -81.75, 26.69]}]`. Then inject `map-command(zoom-to, {bbox: [-81.91, 26.55, -81.75, 26.69]})`. Assert: LayerPanel appears on the left; raster source registered in MapLibre; map flies to the bbox; LayerLegend appears bottom-center showing "Max flood depth (m)" + 0/3.5 m ticks. Screenshot.

3. **Collapse via close button**: click `×` in LayerPanel header → LayerPanel disappears + Layers hamburger appears top-left. Screenshot.

4. **Re-expand via hamburger**: click Layers hamburger → LayerPanel re-appears + hamburger disappears. Screenshot.

5. **Reload persistence**: collapse both panels, reload, assert collapse states restored from localStorage.

Capture screenshots to `reports/inflight/job-0068-web-20260607/evidence/{baseline.png, layer_arrival.png, collapsed_via_close.png, expanded_via_hamburger.png, persistence_after_reload.png}`.

### File ownership (exclusive)

- `web/src/App.tsx` — layout overhaul + hamburger pattern + state-lifting for layers
- `web/src/Map.tsx` — subscriptions + WMS source wiring + fitBounds handler
- `web/src/LayerPanel.tsx` — small: add `×` close button in header (the panel itself already self-positions absolute)
- `web/src/Chat.tsx` — small: add `×` close button in header
- `web/src/*.test.tsx` — update/add tests for the new layout + hamburger + Map.tsx subscriptions
- `packages/contracts/src/grace2_contracts/execution.py` — additive: `LayerURI.bbox: tuple[float, float, float, float] | None = None`; re-export JSON Schema
- `services/agent/src/grace2_agent/pipeline_emitter.py:413-440` — emit_map_command method + zoom-to emission after add_loaded_layer
- `services/agent/src/grace2_agent/workflows/model_flood_scenario.py` — populate LayerURI.bbox from workflow bbox at construction
- `reports/inflight/job-0068-web-20260607/`

### FROZEN

- `web/src/PipelineStrip.tsx` (deleted by job-0064; don't resurrect)
- `web/src/components/LayerLegend.tsx` (job-0065 is correct; don't touch)
- `web/src/lib/style-presets.ts` (job-0065)
- `web/src/components/PipelineCard.tsx` (job-0064)
- `web/src/ws.ts`, `web/src/contracts.ts` (generated/wire layer; the LayerURI.bbox addition flows from execution.py via JSON Schema re-export)
- All other services/, infra/, docs/srs/, etc.
- `reports/complete/**`

### Acceptance criteria

- [ ] **Map full-viewport** underneath; panels render as `position: absolute` overlays above it
- [ ] **Hamburger collapse on same side as panel** (Layers TL, Chat TR); same-side per user direction
- [ ] **Left panel conditionally mounts** on `layers.length > 0` (no empty-tab bug)
- [ ] **Map.tsx WMS source wiring** — subscribes to session-state, diffs against ref, addSource/addLayer/removeSource per A.7 replace-not-reconcile
- [ ] **LayerURI.bbox** added to contracts; JSON Schema re-exported idempotently
- [ ] **pipeline_emitter.emit_map_command("zoom-to", {bbox})** fires after `add_loaded_layer` when bbox present
- [ ] **Map.tsx fitBounds handler** on `case "zoom-to"`; smooth fly-to with padding+duration
- [ ] **model_flood_scenario populates LayerURI.bbox** at construction
- [ ] **Live dev-injection screenshots** — 5 evidence PNGs documenting the corrected flow
- [ ] **Tests** — agent + contracts + web suites all green; ≥3 new web tests covering Map.tsx subscription + hamburger toggle + conditional mount
- [ ] **No edits to FROZEN paths**
- [ ] **Single commit**
