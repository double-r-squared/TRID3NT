# Sprint 10: UI corrections (panels-over-map + raster wiring) + sprint-9 maintenance carry-forwards

**Status:** closed
**Opened:** 2026-06-07
**Closed:** 2026-06-08
**SRS milestones covered:** Corrects the sprint-9 UI deliverables (job-0064/0065) to match the user's actual mental model — panels OVER a full-viewport map, hamburger collapse, raster overlays wired from `loaded_layers`, zoom-to-bbox. Plus the sprint-9 maintenance carry-forwards (OQ-67 worker image rebuild, OQ-62-LAYERURI / OQ-W-65-STYLE-PRESET schema bundle, OQ-61 Cloud Run scaling drift, v0.3.20 SRS housekeeping).

## Goal

Land the UI correction first (job-0068 — the headline) so the user-facing demo flow matches the original intent: full-viewport map underneath, panels floating on top, hamburger collapse, rasters overlayed via QGIS Server WMS once `loaded_layers` populates, camera zooms to the bbox when a layer lands. Then absorb the sprint-9 maintenance carry-forwards (worker image rebuild + schema amendments + scaling drift + SRS housekeeping). Headline beyond the corrections (Mode 2 .gov/.edu offer-to-add vs ATCF Hurricane Ian forcing vs FR-MP-6 Case UX implementation) is TBD pending user direction.

The UI correction is **reversion + 2 small additions**, not an overhaul (per user framing 2026-06-07):
- **Reverting** the job-0064/0065 flex-row + chevron pattern to overlay-panels-on-map (back to the M3 spec intent)
- **Adding** Map.tsx WMS source wiring from `session-state.loaded_layers` (canonical MapLibre+WMS pattern; QGIS Server does all rendering — client just registers URLs)
- **Adding** `map-command(zoom-to, bbox)` emission + Map.tsx `fitBounds` handler (per the layer-emission-contract — transient verb)

## Pre-flight (orchestrator-direct)

- **v0.3.20 SRS housekeeping pass** — bundle the v0.3.17–v0.3.19 carry-forwards + the sprint-9 layer-emission-contract reference + OQ-47-OWSLIB-CHOICE formal decision + OQ-59 closed note. Orchestrator-direct. Keep short.

## Jobs

| Job ID | Specialist | Task | Depends on | Status |
|---|---|---|---|---|
| job-0068-web-20260607 | web (Sonnet) | **UI correction (the headline):** revert App.tsx flex-row to overlay panels above full-viewport map; replace chevrons with same-side hamburgers (Layers TL, Chat TR); conditional-mount left slot on `loaded_layers.length > 0`; wire Map.tsx to add MapLibre raster sources from `loaded_layers[].uri` (QGIS Server WMS); add agent-side `LayerURI.bbox` + `pipeline_emitter.emit_map_command("zoom-to", bbox)` after `add_loaded_layer` + Map.tsx `fitBounds` handler. Single comprehensive job; cross-file but small per file. | — | **approved** (commit e5398b8; 5 screenshots surfaced; 60 web tests; 3 OQs → job-0070 schema bundle) |
| job-0069-infra-20260607 | infra (Opus) | **Worker image rebuild + live publish_layer + real flood-raster UI screenshot (OQ-67 + HEADLINE).** Cloud Build → digest pin → targeted tofu apply (Part 1); live `publish-raster` round-trip against existing job-0066 COG (Part 2); curl-verify server-side WMS GetMap returns real styled PNG (Part 3); drive dev UI with the real WMS URL → headline screenshot of distinct blue flood overlay rendered server-side by QGIS Server (Part 4). | job-0068 | **approved** (commit 0aa6d46; qualified pass — live raster rendering on map verified; OQ-69-COG-CRS-MISTAG lands at wrong location, fix in job-0070) |
| job-0070-engine-20260607 | engine | **OQ-69-COG-CRS-MISTAG closure (the actual headline unlock):** re-run M5 smoke to regenerate flood COG (job-0063 fix now in place) → trigger publish-raster on the fresh COG → drive dev UI → capture Fort-Myers-with-flood headline screenshot. Pure regeneration job, no code changes. | job-0069 | **approved** (commit 4597332; CRS EPSG:32617 verified live; Fort Myers headline screenshot surfaced) |
| job-0071-engine-20260607 | engine | **UX polish + CRS guard:** (1) rotation fix in `postprocess_flood` (axis-order diagnosis + transpose if needed); (2) transparency belt-and-suspenders (NODATA_DEPTH_M=0.05 in data + QML alpha=0 lowest stop); (3) CRS_TAG_MISMATCH guard at postprocess_flood.py:~306 per research-workflow recommendation; (4) publish_layer.py `overrides` kwarg fix (OQ-70-AUTO-PUBLISH-DISPATCH). | — | **approved** (commits 0cbfa43 + 2eb98bc; rotation diagnosed as dims `(m,n)` not `(n,m)`; 7 new tests + 3 pre-existing fixed; suite 180→187; live verification gates on 0074) |
| job-0072-schema-20260607 | schema | **D.2 amendment bundle:** formalize `ProjectLayerSummary.{wms_url, style_preset, opacity, z_index}`; reconcile client `source_url` ↔ Python `uri`; add `map-command` routing to `web/src/ws.ts`. Closes 5 carry-forward OQs in one amendment. | — | **approved** (commit 876798b; 5 OQs closed; +6 tests; 2 NEW OQs — App.tsx 1-line wiring + LayerURI.wms_url) |
| job-0073-infra-20260607 | infra | **Cloud Run scaling block drift reconciliation:** untargeted tofu plan has shown drift since job-0040 era worked around with -target flags. Diagnose + reconcile + smoke test. Closes OQ-61-CLOUD-RUN-SCALING-BLOCK-DRIFT. | — | **approved** (commit 2ed3efa; Option A — codified GCP-auto-filled service-level scaling block; 0-change plan; smoke tests green) |
| job-0074-engine-20260607 | engine (cross-specialty) | **Stage 2 — THE live verification + final headline:** 5 parts in one commit — (1) worker.py 2 bug fixes (DOUBLE-MNT + EPSG:4326); (2) App.tsx OQ-72-APP-MAPCMD-WIRING 1-line closeout; (3) worker rebuild + digest pin + targeted tofu apply; (4) re-publish-raster on the job-0070 COG with new layer_id; (5) headline re-screenshot showing correctly oriented + transparent-only-where-flooded + Fort Myers-located overlay. | job-0071, job-0072, job-0073 | **approved** (commit 344de30; structurally complete — DOUBLE-MNT + EPSG:4326 + App.tsx routing + native URL all live; visible rotation + transparency need fresh-COG re-run → carry-forward) |
| job-0075-engine-20260607 | engine | **Visible-corrections verification:** re-run M5 smoke harness → fresh COG with 0071 rotation + transparency fixes baked in → publish-raster via auto-dispatch (first live test of 0071's fix) → Playwright re-screenshot. Closes the visible-demo gap from 0074's honest caveat. Pure regeneration. | job-0074 | **approved** (commit 70b8dbb; transparency fix CONFIRMED at COG level — 30.6% NaN vs 0% before; auto-dispatch live-verified; rotation didn't trigger — Fort Myers SFINCS emits correct axis order natively) |
| job-0076-web-20260607 | web (Opus) | **THE actual headline unblock**: Map.tsx WMS overlay non-render diagnosis + fix + bundled dark-theme toggle. User caught that no flood overlay has ever rendered on the map canvas since job-0066 — only UI chrome (LayerPanel + LayerLegend + basemap). Diagnose via Playwright network-tap, fix the per-tile WMS URL/z-order/bounds gap, bundle CartoDB DarkMatter dark-theme toggle for unambiguous future verification. Zoom-13 light+dark screenshots required. | job-0075 | **approved** (commit 96e0060; root cause `if (!m.isStyleLoaded()) return;` early-bail; idle-retry fix; dark theme bundled; light 37.6% / dark 26.9% bluish pixels — unmistakable) |
| job-0070-schema-20260608 | schema | **Schema D.2 amendment bundle:** add `LayerURI.bbox: BBox | None` (already added inline by job-0068) + add `wms_url: str | None` to `ProjectLayerSummary` (OQ-62-LAYERURI-URI-FIELD: client receives WMS URL, not gs://; carry both) + add `style_preset?: str | None` to `ProjectLayerSummary` formally (OQ-W-65-STYLE-PRESET — already in client contracts.ts as a stopgap). JSON Schema re-export. | job-0068 | planned |
| job-0071-infra-20260608 | infra | **OQ-61 Cloud Run scaling drift reconciliation:** untargeted tofu plan has shown drift since job-0040 — reconcile so plans return clean. | — | planned |
| job-0072-testing-20260608 | testing | **Sprint-10 acceptance:** re-run Playwright with the corrected UI; assert overlay panels, hamburger toggles, raster source registered, fitBounds fires on zoom-to envelope. Closes sprint-10. | job-0068, job-0069, job-0070 | planned |

## Execution order

```
stage A (the headline):
  job-0068-web   (UI correction; comprehensive single job)
                 ← dispatched first; everything else gates on it

stage B (parallel, file-disjoint, follow job-0068):
  job-0069-infra (worker image rebuild)
  job-0070-schema (D.2 amendment bundle)
  job-0071-infra (OQ-61 scaling drift)

stage C (acceptance):
  job-0072-testing (sprint-10 acceptance + close)
  ← gated on 0068 + 0069 + 0070
```

Plus orchestrator-direct **v0.3.20 SRS housekeeping pass** lands in parallel with Stage A.

## Exit criteria

- [ ] **v0.3.20 SRS housekeeping pass landed** (orchestrator-direct).
- [ ] **UI correction (job-0068)**: overlay panels above full-viewport map; hamburger collapse same-side as panel; conditional-mount left slot; Map.tsx WMS raster wiring from `loaded_layers`; `map-command(zoom-to)` emission + `fitBounds` handler. Live dev verification via existing `__grace2InjectSessionState` + `__grace2InjectMapCommand` seams.
- [ ] **Worker image rebuilt** — live publish_layer round-trip succeeds against the deployed worker; real flood-depth WMS URL renders in MapLibre (no more `gs://` fallback).
- [ ] **Schema D.2 amendment bundle landed** — contracts re-exported idempotently; client `contracts.ts` aligned.
- [ ] **OQ-61 Cloud Run scaling drift reconciled** — untargeted tofu plan returns clean.
- [ ] **Sprint-10 acceptance Playwright** — corrected UI verified end-to-end.

## Deferred to sprint-11 (or later, pending user direction)

- Mode 2 `.gov`/`.edu` offer-to-add (envelope shapes + agent emission detection + popup modal + audit log)
- ATCF Hurricane Ian real storm forcing (`fetch_hurricane_track` + `model_flood_scenario` real-forcing branch)
- FR-MP-6 Case UX implementation (Cases list + per-Case persistence + chat-rehydration + back-to-Cases nav)
- OQ-62-QGS-MUTATION-CONFLICT (per-Case `.qgs` isolation — emerges with FR-MP-6 work)
- OQ-62-PUBSUB-COMPLETION-POLL (async polling refinement)

## Retrospective

### Planned vs actual

Sprint-10 was opened with 6 planned jobs in the manifest (0068/0069/0070-schema/0071-infra/0072-testing + the orchestrator-direct v0.3.20 SRS pass). It delivered **9 substantive jobs + 1 research workflow + 1 ad-hoc inline investigation**:

- job-0068 (web): UI correction — overlay panels + WMS wiring (PLANNED)
- job-0069 (infra, Opus): Worker image rebuild + live WMS rendering (PLANNED as stage B)
- job-0070 (engine): CRS MISTAG regeneration — pure regen, spawned from OQ-69 discovery (UNPLANNED)
- job-0071 (engine): UX polish + CRS guard + auto-dispatch fix (PLANNED as stage B schema; became engine)
- job-0072 (schema): D.2 amendment bundle — ProjectLayerSummary + ws.ts routing (PLANNED)
- job-0073 (infra): Cloud Run scaling drift reconciliation (PLANNED as stage B)
- job-0074 (engine, cross-specialty): Stage 2 live verification — 5-part worker bug fixes + rebuild (UNPLANNED; emerged from OQ-69 chain)
- job-0075 (engine): Visible-corrections verification — third pure-regen attempt (UNPLANNED; emerged from 0074 honest caveat)
- job-0076 (web, Opus): THE actual headline unblock — Map.tsx isStyleLoaded root cause + dark-theme (UNPLANNED; user caught the fundamental gap)
- research-crs-mismatch-recurrence-20260607: 4-investigator research workflow on recurrence prevention (orchestrator-direct, UNPLANNED)

The escalation from "one UI reversion job" to a full debugging arc spanning 6 additional jobs reflects the 3-cycle false-success pattern described below.

### Cost telemetry

Total sprint-10 tokens: ~1,435,304

- job-0069 (Opus): ~224,000 tokens
- research-wf (Opus investigators): ~128,000 tokens  
- job-0076 (Opus): ~323,000 tokens
- **Opus subtotal: ~675,000 tokens (~47% of sprint spend)**
- 7 Sonnet jobs (0068, 0070-0075): ~760,000 tokens (~53% of sprint spend)

For comparison: sprint-9 was ~713,000 tokens, Sonnet-only. Sprint-10 was 2x sprint-9's spend driven entirely by the debugging arc. The two Opus escalations were both routing decisions the orchestrator made when Sonnet agents hit walls: job-0069 when the live end-to-end headline was failing to render, and job-0076 when the user caught that no flood pixel had ever appeared on the map canvas.

### The 3-cycle false-success pattern

**What happened (jobs 0070, 0074, 0075):** All three jobs reported "headline screenshot captured" as their primary acceptance signal. All three were wrong. The sequence:

1. job-0070: reported "Fort Myers headline screenshot surfaced" — LayerPanel populated, LayerLegend visible, screenshot file exists. Map canvas had 0 flood overlay pixels.
2. job-0074: reported "Fort Myers FINAL screenshot" showing pipeline populating — same false signal. 0 flood overlay pixels.
3. job-0075: reported "auto-dispatch live-verified; rotation confirmed" with Playwright re-screenshot — 0 flood overlay pixels. The 30.6% NaN at COG level was a valid data quality check but not a rendering check.

**Why it worked:** The session-state subscriber in Map.tsx subscribed to session-state changes and always populated the LayerPanel (different subscriber path, no isStyleLoaded gate). The LayerPanel, LayerLegend, and basemap tiles all loaded correctly, producing screenshots that looked like a working app at first inspection. The map area appeared to have basemap tiles — the flood layer just wasn't painted on top. No agent thought to examine what was in the MapLibre style object (`m.getStyle()`) or to count HTTP requests to the flood WMS endpoint.

**How job-0076 Opus broke the pattern:** Used `page.on("request"|"response")` Playwright network-tap instrumentation to count HTTP requests to the WMS flood endpoint. Pre-fix: 0 flood-depth tile requests out of 69 total WMS responses. Called `m.getStyle()` via the `window.__grace2GetMap` seam and found only basemap sources — no flood layer. This surfaced the silent `if (!m.isStyleLoaded()) return;` early-bail at Map.tsx:210. The fix (idle-retry on session-state subscriber) produced 64 flood-depth tile requests in the post-fix run and 37.6% / 26.9% bluish pixel coverage in the screenshots.

### Orchestrator lesson (binding for future sprints)

"Screenshot captured" without pixel-level evidence in the map area is NOT verification. The bar going forward is one of:
- **"% bluish pixels in the map area"** (as Opus implemented with the PIL pixel analysis in job-0076 `headline_driver.py`)
- **HTTP request count to the overlay WMS endpoint** (as `page.on("request"|"response")` provides)
- **`m.getStyle()` layer/source dump** confirming the flood layer is in the live style

A screenshot that shows UI chrome (LayerPanel populated, LayerLegend visible, basemap tiles loaded) with no measurement of the overlay rendering area is NOT sufficient acceptance for a "flood layer renders on map" exit criterion. This applies to any future job that claims "layer renders on map."

### Architectural wins

Sprint-10 delivered the following structural improvements beyond the headline unblock:

1. **Contract field formalization (job-0072):** `ProjectLayerSummary.{wms_url, opacity, z_index}` added to Python contracts and reconciled with client `contracts.ts`; `LayerURI.bbox` field formalized (was added inline by 0068); `map-command` envelope type added to `ws.ts` routing. Closes 5 carry-forward OQs.

2. **CRS_TAG_MISMATCH structural guard (job-0071):** `postprocess_flood.py` now raises `CRS_TAG_MISMATCH` early when the COG CRS doesn't match the expected UTM zone, preventing a repeat of the OQ-69 silent-wrong-location publish. 3 regression tests added.

3. **ws.ts production map-command routing (job-0072):** `GraceWs` now routes incoming `map-command` envelopes from the WebSocket to the internal bus, so future agent-emitted zoom-to commands will reach the client through the real socket (not just the dev-injection path). OQ-76-MAPCMD-WS documents the remaining dev-only gap for the Map.tsx injection side.

4. **Auto-dispatch `publish_layer` overrides fix (job-0071):** `pipeline_emitter.py` was not passing the `overrides` kwarg when auto-dispatching `publish_layer` after `postprocess_flood`. The fix enables style_preset, opacity, and z_index to flow through the auto-publish path. Verified live in job-0075.

5. **Cloud Run scaling block drift reconciliation (job-0073):** Google provider 6.50.x exposes a new service-level `scaling` block that caused drift since job-0040. Codified the GCP-auto-filled service-level scaling block in `infra/services.tf`; untargeted `tofu plan` now returns 0-change clean.

6. **CartoDB DarkMatter dark-theme toggle (job-0076):** Light/dark basemap toggle with localStorage persistence, `data-testid="grace2-theme-toggle"`, sun/moon icon at top-center. Enables unambiguous future verification (flood overlay is unmistakable against the dark basemap). 4 new App.test.tsx tests.

7. **Map.tsx `isStyleLoaded` fix (job-0076):** The idle-retry session-state subscriber pattern (`m.once("idle", applyLatest)`) fixes the core race condition that silenced all WMS overlay wiring since job-0068. 5 new Map.test.tsx tests covering the idle-retry, theme-swap, and beforeId stacking invariants.

### Open questions carry-forward list

All OQs below are tagged with their origin job and sprint-11 priority:

- **OQ-76-MAP-ALIGNMENT** (sprint-11 priority — user-observed): alignment + rotation + zoom visible issues on the flood overlay vs basemap after job-0076 unblock. Overlay now renders (37.6% / 26.9% bluish pixels) but doesn't geometrically align with the basemap street grid. Likely roots: MapLibre `bounds` on the WMS raster source not being set; tileSize:256 vs basemap tile scale at zoom-13; EPSG:32617 → EPSG:3857 reprojection at the layer's small extent. Sprint-11 investigation job (web specialist).

- **OQ-76-CARTO-RATE-LIMIT** (sprint-11/12): CartoDB DarkMatter free tier may be rate-limited in production; need a paid CartoDB key, self-hosted dark basemap, or alternative dark tiles. Acceptable for v0.1 demo; revisit before production rollout.

- **OQ-76-MAPCMD-WS** (sprint-11 small): `GraceWs` routes incoming `map-command` from the WS to the bus (job-0072 done), but `App.tsx → MapView` still uses the dev-only `window.__grace2InjectMapCommand` seam to pass the callback. A real agent-emitted `map-command(zoom-to)` over the live socket won't reach the client without one more wiring step. Small job; sprint-11.

- **OQ-72-LAYERURI-WMS-FIELD** (next schema sprint): `LayerURI.wms_url` field not yet formalized in Python contracts (client-side stopgap exists in `contracts.ts`). Housekeeping.

- **OQ-71-SQUARE-GRID-ROTATION** (low): only relevant if HydroMT changes dimension conventions away from the `(timemax, m, n)` order — rotation transpose guard is in place.

- **OQ-74-TSC-WS-TEST-ERRORS** (low): 3 pre-existing TSC errors in the frozen `ws.test.tsx` file (test-file-only; production source is clean). Not fixed in this sprint per FROZEN boundary; sprint-11 testing job can close this.

- **OQ-74-KICKOFF-WORKER-OP-MISMATCH** (doc fix): kickoff template discrepancy between worker op names; housekeeping in the kickoff template.

- **v0.3.22 SRS housekeeping** (orchestrator-direct sprint-11 opener): bundle sprint-10 architectural decisions (CRS guard, ws.ts routing, dark-theme basemap swap, idle-retry pattern) into the SRS narrow files.
