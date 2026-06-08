# Report: UI correction — panels overlay above full-viewport map + hamburger collapse + raster wiring + zoom-to

**Job ID:** job-0068-web-20260607
**Sprint:** sprint-10
**Specialist:** web
**Task:** Revert flex-row split-pane layout to overlay panels + add Map.tsx WMS source wiring from session-state.loaded_layers + emit_map_command(zoom-to) after add_loaded_layer + hamburger collapse pattern + conditional LayerPanel mount on layers.length > 0
**Status:** ready-for-audit

## Summary

Implemented all 5 changes from the kickoff in tackle order (4->5->3->1->2). The web client now renders a full-viewport map with panels as position:absolute overlays. LayerPanel conditionally mounts only when layers are present. Map.tsx subscribes to session-state and wires WMS raster sources via MapLibre addSource/addLayer (replace-not-reconcile per A.7). Zoom-to fires automatically when a LayerURI carries a bbox. Hamburgers appear on the same side as their panel when collapsed. All 3 test suites remain green with 14 new tests added.

## Changes Made

### Change 4: web/src/Map.tsx

- Added MapViewProps interface with subscribeSessionState and subscribeMapCommand props
- Added useRef<Set<string>>(new Set()) for addedSourceIds (survives effect re-runs per A.7)
- Added useEffect subscribing to session-state: diffs loaded_layers against ref, adds raster sources+layers, removes gone ones, updates opacity/visibility (replace-not-reconcile)
- Added useEffect subscribing to map-command: handles zoom-to with map.fitBounds; other verbs get console.warn
- Added buildWmsTileUrl() helper with {bbox-epsg-3857} placeholder
- Moved NavigationControl from "top-right" to "bottom-right" to avoid Chat panel overlap
- Added local WireLayerSummary (agent emits uri; contracts.ts has source_url), ZoomToCommand, WireMapCommand types

### Change 5 (contracts): packages/contracts/src/grace2_contracts/execution.py

- Added bbox: tuple[float,float,float,float] | None = None to LayerURI

### Change 5 (emitter): services/agent/src/grace2_agent/pipeline_emitter.py

- Added MapCommandPayload import
- Added emit_map_command(command, args) method
- Extended add_loaded_layer: after emit_session_state(), if layer.bbox is not None, calls emit_map_command("zoom-to", {"bbox": list(layer.bbox)})

### Change 5 (workflow): services/agent/src/grace2_agent/workflows/model_flood_scenario.py

- Added bbox=resolved_bbox to LayerURI construction in the publish_layer success path

### Change 3: web/src/App.tsx (conditional mount)

- Added useEffect subscribing to bus.subscribeSessionState to lift layers state at App level
- Gated LayerPanel mount: showLeftPanel = layers.length > 0 && !leftCollapsed
- showLayersHamburger = layers.length > 0 && leftCollapsed

### Change 1: web/src/App.tsx (overlay layout)

- Removed flex-row layout; replaced with position:fixed; inset:0 container
- Removed COLLAPSED_WIDTH constant and side panel slot divs
- MapView first in DOM (underneath), LayerPanel and Chat as overlays
- LayerLegend directly in fixed container

### Change 2: web/src/App.tsx (hamburger)

- Deleted chevronBtnStyle and in-slot chevrons
- Added hamburgerStyle with z-index 30 (above panels z=20, legend z=10)
- Layers hamburger: top:12, left:12 (TL, same side as LayerPanel)
- Chat hamburger: top:12, right:12 (TR, same side as Chat)

### Change 2 (panels): web/src/LayerPanel.tsx + web/src/Chat.tsx

- Added onClose? prop + x close button in each panel header (data-testid: grace2-layer-panel-close, grace2-chat-close)

### New: web/src/Map.test.tsx

- 5 tests: addSource/addLayer on session-state, removeLayer/removeSource (A.7), setPaintProperty on opacity, fitBounds on zoom-to, console.warn on unknown verb

### Extended: web/src/App.test.tsx

- 9 new tests: conditional mount, hamburger collapse/expand cycle, chat persistence

## Open Questions

- OQ-0068-URI (non-blocking): contracts.ts uses source_url; Python model uses uri. Map.tsx reads uri via WireLayerSummary cast. Fix in job-0070.
- OQ-0068-MAPCMD-WS (non-blocking): GraceWs (frozen) does not dispatch map-command to handler; falls through to console.debug. Bus pushMapCommand only reachable via dev-injection in this job. Production path deferred.
- OQ-0068-ZIDX (non-blocking): opacity/z_index not formally in ProjectLayerSummary Python contract. Map.tsx uses layer.opacity ?? 1 fallback per kickoff. Cleanup in job-0070.

## Dependencies and Impacts

- Depends on: job-0064, job-0065, job-0066, job-0062
- Affects: job-0070 (schema cleanup)

## Verification

Tests run:
- web: 60 passed (46 + 14 new)
- contracts: 142 passed
- agent: 180 passed, 1 skipped
- tsc --noEmit: 0 errors

Live E2E evidence (5 screenshots, all assertions passed):
1. baseline.png: layer-panel:0, layers-hamburger:0, chat:1
2. layer_arrival.png: layer-panel:1, legend:1 (map zoomed to Fort Myers)
3. collapsed_via_close.png: layer-panel:0, layers-hamburger:1
4. expanded_via_hamburger.png: layer-panel:1, layers-hamburger:0
5. persistence_after_reload.png: layers-hamburger:1, chat-hamburger:1 after reload

Results: pass
