# Sprint 10: UI corrections (panels-over-map + raster wiring) + sprint-9 maintenance carry-forwards

**Status:** planned
**Opened:** 2026-06-07
**Closed:** —
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
| job-0069-infra-20260607 | infra (Opus) | **Worker image rebuild + live publish_layer + real flood-raster UI screenshot (OQ-67 + HEADLINE).** Cloud Build → digest pin → targeted tofu apply (Part 1); live `publish-raster` round-trip against existing job-0066 COG (Part 2); curl-verify server-side WMS GetMap returns real styled PNG (Part 3); drive dev UI with the real WMS URL → headline screenshot of distinct blue flood overlay rendered server-side by QGIS Server (Part 4). | job-0068 | assigned |
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

_Filled at close._
