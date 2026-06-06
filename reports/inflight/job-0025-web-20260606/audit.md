# Audit: Basemap pivot to QGIS Server WMS + LayerPanel.tsx + App.tsx layout shell

**Job ID:** job-0025-web-20260606, **Sprint:** sprint-05, **Auditor:** Development Orchestrator, **Status:** assigned

## Task Assignment

**Specialist:** web

**Prerequisites:**
- job-0016 (M1 web stub: React 18 + Vite 5 + TS strict + MapLibre 4.7 + OSM-direct CONUS basemap + chat panel; 2D camera lock already enforced; hand-mirror contracts.ts with ~6 M1 payloads)
- job-0024 (M2 QGIS Server deployed at `https://grace-2-qgis-server-425352658356.us-central1.run.app`, image `@sha256:a703476`, `.qgs` mounted at `/mnt/qgs/grace2-sample.qgs`, layer `basemap-osm-conus` verified)

**SRS references:** §7 M3; FR-WC-1, FR-WC-2, FR-WC-3, FR-WC-4 (Layer panel: visibility / opacity / drag-and-drop reorder — drag-and-drop is in v0.1 scope per the SRS, not deferred); FR-DT-1, FR-DT-2, FR-DT-3, FR-DT-5; Decision B, Decision C, Decision I; FR-QS-2 (`.qgs` via `/mnt/qgs/` per amendment landed in job-0024); Appendix A `session-state` (envelope carrying `loaded_layers`, `map_view`, `current_pipeline`); Appendix A `map-command` (load-layer / remove-layer / set-layer-visibility / set-layer-opacity / set-layer-order / zoom-to / set-temporal-config / start-animation / stop-animation / invalidate-tiles); Appendix D.2 `ProjectLayerSummary`; Appendix D.6 `MapView`.

### Environment
Linux Debian dev host and prod (Cloud Run linux/amd64). The web client runs in Chromium + Firefox-ESR for spot-checks (NFR-PO-1, FR-WC-1; Safari deferred per job-0016, no macOS substrate). Consume the live M2 substrate per `PROJECT_STATE.md`: QGIS Server WMS at `https://grace-2-qgis-server-425352658356.us-central1.run.app/ogc/wms?MAP=/mnt/qgs/grace2-sample.qgs`, layer `basemap-osm-conus`, image pinned `@sha256:a703476`, serving EPSG:3857 + EPSG:4326. No mocks for the basemap path — real WMS GetMap PNG responses.

### Scope
1. Edit `web/src/Map.tsx`: replace the OSM-direct raster source with a MapLibre `rasterSource` whose `tiles` template is `${VITE_GRACE2_WMS_URL}?SERVICE=WMS&VERSION=1.3.0&REQUEST=GetMap&LAYERS=basemap-osm-conus&CRS=EPSG:3857&FORMAT=image/png&TRANSPARENT=true&BBOX={bbox-epsg-3857}&WIDTH=256&HEIGHT=256`, with `VITE_GRACE2_WMS_URL` defaulting to `https://grace-2-qgis-server-425352658356.us-central1.run.app/ogc/wms?MAP=/mnt/qgs/grace2-sample.qgs`. `tileSize: 256`.
2. Keep the OSM-direct raster source committed as an inactive fallback layer (`visibility: 'none'`) to demonstrate FR-DT-1 Tier A swappability without churn.
3. Preserve the 2D camera lock verbatim from M1: `maxPitch: 0`, `dragRotate` disabled, `pitchWithRotate: false`, `touchPitch` disabled, `NavigationControl({ showCompass: false })` (Decision I, FR-WC-3).
4. Preserve the initial CONUS view (FR-DT-3).
5. Create `web/src/LayerPanel.tsx`: renders the current layer list with z-order (top of list = top of stack). Initial state seeded from `loaded_layers` (`list[ProjectLayerSummary]` per Appendix D.2) on the `session-state` envelope on connect/reconnect; live updates from incoming `map-command` envelopes (load-layer / remove-layer / set-layer-visibility / set-layer-opacity / set-layer-order). Controls per row: visibility checkbox, 0..1 opacity slider, **drag-and-drop reorder** (mandatory — FR-WC-4 explicitly puts drag-and-drop in v0.1 scope), and up/down keyboard controls **in addition** for a11y/test-automation, name + attribution from `ProjectLayerSummary`. User-side control clicks emit an intent payload onto a local event bus and `console.debug` only — the agent does NOT consume client → agent layer intents in M3 (M4 work). No local persistence of layer identity/visibility/opacity/order — only UI-chrome state (panel collapsed) may live locally. Drag-and-drop library choice (`@dnd-kit/sortable` vs hand-rolled HTML5 DnD vs `react-dnd`) surfaced as Open Question; drag-and-drop AS the FR-WC-4 acceptance is mandatory either way.
6. Extend `web/src/contracts.ts` (additive, hand-mirror per job-0016 OQ-W-1 resolution; promotion to codegen at ~20 payloads). Count target: M1 mirror currently has ~6 payloads; this job adds ~6–8 new type definitions (`ProjectLayerSummary` from Appendix D.2; `MapView` from Appendix D.6; `SessionStatePayload` covering `chat_history`/`loaded_layers`/`pipeline_history`/`current_pipeline`/`map_view`; `MapCommandPayload` discriminated union; the five active M3 `map-command` sub-discriminants — load-layer / remove-layer / set-layer-visibility / set-layer-opacity / set-layer-order). Total projected ~12–14, still below the ~20 codegen trigger from OQ-W-1. The other five `map-command` discriminants (zoom-to / set-temporal-config / start-animation / stop-animation / invalidate-tiles) are deferred to M4–M5 and explicitly NOT mirrored here. If the realized total exceeds 18, surface as refined OQ-W-1 (codegen promotion may need to land sooner than the ~20 trigger).
7. Edit `web/src/App.tsx` layout glue only: mount `LayerPanel` as a docked, collapsible right-side panel above the map; route `session-state` and `map-command` envelopes from `ws.ts` to the panel via a small `useReducer` store (no new state library). Publish the App.tsx layout shape (panel slots, reducer-store API for new envelope subscriptions, named export points for downstream component mounts) clearly in the job report so job-0026 can consume it without rework. Do not edit `Chat.tsx` or `ws.ts` logic beyond the minimal exposure of subscriptions for the new panel.
8. Confirm zero `gs://` URLs anywhere in compiled client output (FR-DT-5, Invariant 5 Tier separation).

### File ownership (exclusive)
- `web/src/Map.tsx`
- `web/src/LayerPanel.tsx` (NEW)
- `web/src/App.tsx` (layout glue only — no `Chat.tsx` or `ws.ts` edits beyond subscription exposure)
- `web/src/contracts.ts` — session/map surface: `ProjectLayerSummary`, `MapView`, `SessionStatePayload`, `MapCommandPayload` + the five M3-active sub-discriminants. Do not touch any pipeline types (reserved for job-0026).
- `web/README.md` (additive note on `VITE_GRACE2_WMS_URL` env var only — do not add Playwright sections; that is job-0027)

### FROZEN — no edits in this job
- `packages/contracts/**` (any new shape needed → schema consumer-pushback Open Question, NOT in-place edits per AGENTS.md "Architecture / Schema Consumer Pushback")
- `services/agent/**` (no agent code lands; agent emission of session-state with populated loaded_layers + map-command + pipeline-state is M4)
- `services/workers/**` (M2 owned)
- `infra/**` (M2 owned; QGIS Server stays at `@sha256:a703476`)
- `docs/SRS_v0.3.md` (user-owned)
- `styles/**` (engine-owned)
- `reports/complete/**` (immutable per AGENTS.md "Completed Job Immutability")
- The other parallel job-0027's exclusive paths: `web/playwright.config.ts`, `tools/screenshot.mjs`, root `Makefile`, the Playwright section of `web/README.md`
- Job-0026's reserved files: `web/src/PipelineStrip.tsx`, the pipeline section of `web/src/contracts.ts`
- `web/src/Chat.tsx` (M1-owned by job-0016; do not touch)
- `web/src/ws.ts` logic (only additive subscription exposure permitted; no protocol-logic edits)

### Cross-cutting principles in force (cited by NUMBER+name from agents/orchestrator.md)
- **Invariant 5 (Tier separation)** — client never reads GCS directly; basemap pivot validated against the real deployed QGIS Server, not a mock or stub. Live E2E required.
- ***Diagnose before fix* (cross-cutting principle)** — if WMS tiles render blank or mis-projected, capture the failing GetMap request and response before changing tile-template structure.
- **Surface uncertainty as Open Questions** — TENTATIVE choices below surface as Open Questions in the job report.
- **No legacy support pre-MVP** — the OSM-direct fallback stays committed as the FR-DT-1 swappability proof; do not build a runtime feature-flag system for it.
- **Remove don't shim** — when replacing the OSM source, delete the M1 wiring rather than wrapping it behind a conditional.
- **Bundle small fixes** — if Map.tsx needs a tiny `contracts.ts` import path adjustment to ship the new types, fix it in this job (bounded by the FROZEN list above).
- **Schema Consumer Pushback** — never invent fields client-side; any Appendix D.2/D.6 gap surfaces as a named Open Question with the exact missing field, routed through schema not in-place.

### Acceptance criteria (reviewer re-runs)
- [ ] `npm run dev` in `web/` opens the app and the visible basemap is sourced from `grace-2-qgis-server-425352658356.us-central1.run.app/ogc/wms?MAP=/mnt/qgs/grace2-sample.qgs` (browser DevTools Network panel confirmation, evidence committed).
- [ ] Zero `gs://` URLs in any client request observed (FR-DT-5, Invariant 5 Tier separation).
- [ ] Camera is locked in 2D: rotation/pitch/bearing controls absent or no-op (Decision I, FR-WC-3).
- [ ] Initial view = CONUS (FR-DT-3 preserved from M1).
- [ ] LayerPanel renders 2–3 rows when fed a sample `session-state` envelope (DOM inspection or React DevTools); visibility checkbox + opacity slider + **drag-and-drop reorder handle** + name + attribution present per row (FR-WC-4); up/down keyboard controls also present for a11y.
- [ ] Drag-and-drop reordering actually changes the rendered z-order (manual or component-level test, evidence committed).
- [ ] LayerPanel applies incoming `map-command` (set-layer-visibility, set-layer-opacity, set-layer-order) to its view-model (component-level test or manual injection).
- [ ] `web/src/contracts.ts` aggregate hand-mirror payload count is ~12–14 (under the ~20 codegen trigger from OQ-W-1); if it exceeds 18, refined OQ-W-1 surfaced in the report.
- [ ] OSM-direct fallback layer exists in the style spec with `visibility: 'none'` (FR-DT-1 swappability demonstration).
- [ ] M1's 23 acceptance tests and 91 contracts tests still pass (no regression).
- [ ] No edits to `web/src/Chat.tsx` or substantive edits to `web/src/ws.ts` beyond exposing subscriptions; no edits to any FROZEN path listed above.
- [ ] Job report publishes the App.tsx layout shape (panel slots, reducer-store API, new envelope subscription seams) so job-0026 can mount the PipelineStrip without rework.

Surface contestable choices as Open Questions with TENTATIVE tags — at minimum: env-var-vs-hardcoded WMS URL, `rasterSource` vs WMTS source, drag-and-drop library choice (`@dnd-kit/sortable` vs hand-rolled HTML5 DnD vs `react-dnd`), layer-panel docking side, hillshade overlay deferral to M9, simulated WS server for component verification, client → agent layer-intent message shape for M4, refined OQ-W-1 if mirror count > 18.

## Assessment

## Invariant Check

## Dependency Check

## Decisions Validated

## Open Questions Resolved

## Follow-up Actions

## Sign-off
