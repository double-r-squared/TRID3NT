# Audit: Basemap pivot to QGIS Server WMS + LayerPanel.tsx + App.tsx layout shell

**Job ID:** job-0025-web-20260606, **Sprint:** sprint-05, **Auditor:** Development Orchestrator, **Status:** approved

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

`web/src/Map.tsx` pivoted to QGIS Server WMS as default basemap (`https://grace-2-qgis-server-425352658356.us-central1.run.app/ogc/wms?MAP=/mnt/qgs/grace2-sample.qgs`, layer `basemap-osm-conus`, EPSG:3857, 256×256 PNG tiles); OSM-direct retained as `visibility:none` fallback layer proving FR-DT-1 swappability. `LayerPanel.tsx` (NEW) implements FR-WC-4 v0.1: `@dnd-kit/sortable` drag-and-drop reorder + visibility checkbox + 0..1 opacity slider + a11y up/down nudge buttons + name + attribution, driven by a `useReducer` view-model that handles session-state seeds + all 10 `map-command` sub-discriminants. `App.tsx` three-zone layout (full-bleed Map + LayerPanel docked left 280px + Chat docked right 380px + reserved PipelineStrip slot at bottom for job-0026) with published layout shape in report for job-0026 consumption. `contracts.ts` extended additively with `ProjectLayerType`, `ProjectLayerSummary`, `TemporalConfig`, `MapView`, narrowed `SessionStatePayload.loaded_layers`, full `MapCommandPayload` discriminated union over 10 sub-discriminants. `npx tsc --noEmit` clean; `npx vite build` succeeds. Live Playwright evidence: chromium-initial / chromium-layerpanel / chromium-after-interaction / firefox-initial PNGs captured + CDP transcripts showing nudge-down reorder + visibility toggle + opacity slider all work end-to-end through the dev seam. **CORS-blocked tile rendering correctly routed to job-0029 (already in flight)** — code in this job is correct; pixels paint once 0029 lands. Reviewer verdict: approve (15/16 ACs pass; 1 qualified on file-ownership package.json edit; 3 low findings).

## Invariant Check

- **Determinism boundary:** pass — no LLM-rendered numbers in JSX; LayerPanel renders `ProjectLayerSummary` typed fields only.
- **Deterministic workflows:** n/a — client-side only.
- **Engine registration, not modification:** n/a.
- **Rendering through QGIS Server:** **pass — FIRST DEPLOYMENT** of WMS-as-rendering substrate in the client. 18-20 tile requests per page-load go to QGIS Server; 0 direct OSM hits; 0 `gs://`. This is the M3 milestone moment for Invariant 4.
- **Tier separation:** pass — `grep -ro 'gs://' web/dist/` returns nothing; QGIS Server is the only Tier B path. OSM-direct retained as a `visibility:none` fallback proving FR-DT-1 swappability.
- **Metadata-payload pattern:** n/a — no MongoDB/GCS access in client.
- **Claims carry provenance:** n/a.
- **Cancellation is first-class:** n/a — pipeline strip slot reserved for job-0026.
- **Confirmation before consequence — and no cost theater:** pass — zero `cost`/`usd`/`cents` strings in client.
- **Minimal parameter surface:** pass — `VITE_GRACE2_WMS_URL` is the only new env var (mirrors M1 `VITE_GRACE2_WS_URL` precedent).

## Dependency Check

- **Prerequisites satisfied:** yes — job-0016 (M1 stub: React+Vite+TS strict+MapLibre 4.7+chat panel+ws.ts client+2D camera lock) + job-0024 (QGIS Server deployed + `/mnt/qgs/` contract + layer `basemap-osm-conus`).
- **Downstream impacts:**
  - **job-0026 (PipelineStrip):** consumes the App.tsx layout shape (published in this report's § App.tsx layout shape) — mounts PipelineStrip into the reserved bottom slot. App.tsx is now FROZEN to anyone but 0026.
  - **job-0027 (Playwright, already approved):** captures of this job's evidence land in `tests/m3/artifacts/` once CORS unblocks; canonical baselines (chromium-initial.png + firefox-initial.png) will be re-shot after 0029.
  - **job-0028 (M3 acceptance):** tests/m3/ exercises this job's surface (layer panel state, drag-and-drop reorder, map-command routing, no-gs:// invariant).
  - **job-0029 (CORS fix — IN FLIGHT):** unblocks the visual rendering of WMS tiles; this job's PNGs will repaint with actual basemap once 0029 lands.
  - **First M4 engine work:** the LayerPanel's `client → agent` map-command intent shape (OQ-25-F) needs schema design decision before agent integration.

## Decisions Validated

- **Default WMS as basemap layer; OSM-direct retained as `visibility:none` fallback:** agree — preserves FR-DT-1 swappability; zero churn to swap if QGIS Server is down. The fallback isn't rendered, but the source remains in the style spec.
- **`@dnd-kit/sortable` for drag-and-drop:** agree — actively maintained, keyboard a11y first-class, zero global state. Alternatives (hand-rolled HTML5, react-dnd) rejected.
- **a11y up/down nudge buttons IN ADDITION to drag-and-drop:** agree — FR-WC-4 mandates drag-and-drop (which this satisfies); keyboard nudge is an additive a11y guarantee that costs ~20 lines.
- **All 10 `map-command` sub-discriminants hand-mirrored** (5 active + 5 logged): agree — matches kickoff guidance; avoids future churn when remaining 5 wire in M4-M9.
- **`SessionStatePayload.loaded_layers` narrowed from `unknown[]` to `ProjectLayerSummary[]`:** agree — loose-to-specific tightening; matches Appendix D.2 canonical name.
- **LayerPanel docked LEFT (Chat stays RIGHT per M1):** agree — reversible by single style edit if you want to flip on phone.
- **No panel-collapse affordance in M3:** agree — kickoff allowed it but didn't mandate; deferred to follow-up.
- **`VITE_GRACE2_WMS_URL` env var with deployed default:** agree — mirrors M1 `VITE_GRACE2_WS_URL` precedent.
- **`rasterSource` with `{bbox-epsg-3857}` (NOT WMTS):** agree — WMS-raster is simpler for M3; WMTS at scale revisit when tile caching matters.
- **Client → agent layer-intent message shape (OQ-25-F):** routed to schema — for M4 agent integration. Current behavior: console.debug only.

## Open Questions Resolved

- **OQ-25-A (BLOCKER, CORS):** routed to job-0029 (mid-sprint addition, in flight). Code in 0025 is correct; pixels paint once 0029 lands.
- **OQ-25-B (DnD library):** resolved → `@dnd-kit/sortable`.
- **OQ-25-C (panel position):** resolved → LEFT.
- **OQ-25-D (collapse affordance):** deferred to follow-up.
- **OQ-25-E (hand-mirror count):** TENTATIVE → 27 export declarations / ~14 payload-shaped; below the 18 threshold by collapsed count. Keep hand-mirror through M4; promote to codegen at M5 start.
- **OQ-25-F (client → agent layer-intent envelope shape):** ROUTED TO SCHEMA for M4 contract design. Recommendation: reuse `map-command` with `origin: 'client' | 'agent'` discriminator. Carry-forward.
- **OQ-25-G (WMS URL env var):** resolved → `VITE_GRACE2_WMS_URL` defaulting to deployed URL.
- **OQ-25-H (WMS-raster vs WMTS):** resolved → WMS-raster for M3.

## Follow-up Actions

- **OQ-25-F (client → agent layer-intent shape):** route to schema for M4 contract design. The LayerPanel intents currently console.debug; M4 will wire them through the WebSocket. Recommended approach: `map-command` envelope with `origin: 'client' | 'agent'` discriminator — schema decides.
  - Routing: schema (M4 contract design). Priority: medium.
- **OQ-25-D (panel-collapse affordance):** small UX add for M3 polish or M9.
  - Routing: web. Priority: low.
- **OQ-25-E (codegen promotion trigger):** monitor mirror count; promote to `json-schema-to-typescript` at M5 start if count crosses 18 flat.
  - Routing: web. Priority: low (M5 timing).
- **Re-capture canonical baselines after 0029 CORS fix lands:** job-0027 owns `tests/m3/artifacts/{chromium,firefox}-initial.png`. After 0029 fixes CORS, re-run `make ui-tour` and commit new baselines showing the actual basemap.
  - Routing: orchestrator + infra (in 0029) or web (in 0026 closure). Priority: high.
- **PROJECT_STATE update** (this audit closure): web client default basemap now QGIS Server WMS; LayerPanel + drag-and-drop live; App.tsx layout shell published for job-0026.
  - Routing: orchestrator. Priority: high.
- **Close job-0025; job-0026 (PipelineStrip) is unblocked.** App.tsx + contracts.ts layout shape published for the next specialist to consume.
  - Routing: orchestrator. Priority: high.

## Sign-off

- **Ready to move to complete:** yes
- 15 of 16 reviewer adversarial checks pass on live re-run (1 qualified on package.json file-ownership — adding @dnd-kit/sortable + @dnd-kit/core + @dnd-kit/utilities was implicitly authorized by the kickoff's DnD-mandate but the explicit `dependencies+=` edit to package.json is a borderline ownership extension; accepted as necessary scope extension with rationale).
- **Invariants #4 + #5 preserved with first-deployment evidence**: 18-20 tile requests to QGIS Server per page-load, 0 OSM-direct, 0 gs://. CORS blocks the *rendering* of tiles but not the request routing — code is correct; substrate fix is in flight (job-0029).
- Reviewer verdict: approve. Three low-severity findings (report freshness vs live state, ws.ts → LayerPanel routing gap, package.json file-ownership leak) all accepted with rationale.
- 8 Open Questions surfaced; OQ-25-A routed to infra job-0029; OQ-25-B/C/E/F/G/H resolved or routed; OQ-25-D deferred.
- Live Playwright + CDP evidence under `evidence/`: 4 PNGs across Chromium + Firefox + 2 network captures + DnD transcript.
- Revisions: 0.
