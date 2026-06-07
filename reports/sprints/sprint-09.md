# Sprint 09: M5→UI wiring (layer surfacing on basemap) + sprint-8 carry-forwards

**Status:** planned
**Opened:** 2026-06-07
**Closed:** —
**SRS milestones covered:** Closes the gap between sprint-8's PRODUCTION M5 SUCCESS (real flood-depth COG produced) and the M3 web client (the COG appears as a layer over the basemap and is browsable in the LayerPanel). Plus sprint-8 carry-forwards (v0.3.20 SRS housekeeping is sprint-9 opener; OQ-59 CRS-label fix; OQ-49 agent Dockerfile/deploy).

## Goal

Make the production M5 flood-depth COG that sprint-8 produced **show up in the web UI over the basemap** so an AFK user typing "model Hurricane Ian at Fort Myers" sees the rendered flood layer on the MapLibre canvas. Per `docs/decisions/layer-emission-contract.md` (2026-06-07), the contract spine is `session-state.loaded_layers` (declarative, replace-not-reconcile per A.7); `map-command` is reserved for transient verbs.

The substrate is already substantial:
- PyQGIS worker exists (`services/workers/pyqgis/worker.py`) that mutates `.qgs` projects and writes back to GCS — runtime layer publication is supported, not baked-only.
- QGIS Server with CORS-fixed nginx serves WMS from the `-qgs` bucket via `/mnt/qgs/` Cloud Run gen2 mount, with `/vsigs/` GDAL auth pre-wired for layers inside the `.qgs` referencing GCS COGs.
- Web client `LayerPanel.tsx` already consumes `session-state` and reconciles the loaded-layers list (M3 substrate).
- `PipelineEmitter.add_loaded_layer` (`services/agent/src/grace2_agent/pipeline_emitter.py:413-440`) already auto-emits `session-state` when a tool returns a `LayerURI` (line 517 check).

What's missing is exactly **three small jobs** — the gap the layer-emission-contract decision identified.

## Pre-flight (orchestrator-direct, lands in parallel with Stage A)

- **v0.3.20 SRS housekeeping pass** — bundle the v0.3.17–v0.3.19 carry-forwards (carryover from sprint-8 pre-flight that we never landed because the sprint exceeded scope). Likely additions: layer-emission-contract reference in §2.1 Decisions or §A.7 (Appendix A); OQ-47-OWSLIB-CHOICE formal decision; OQ-59 surfaced. Keep amendment short per user direction.

## Jobs

| Job ID | Specialist | Task | Depends on | Status |
|---|---|---|---|---|
| job-0060-engine-20260608 | engine | **Agent return-type change:** `run_model_flood_scenario` returns `LayerURI` (or `list[LayerURI]`) instead of dict-dumped `AssessmentEnvelope`. PipelineEmitter's `isinstance(result, LayerURI)` auto-emit branch then fires → `session-state.loaded_layers` populates → LayerPanel sees the layer. Envelope JSON still travels via tool-call-complete payload for chat-message + Mongo persistence. ~5 LOC change + 2 tests; no contract change. | — | planned |
| job-0061-infra-20260608 | infra | **IAM grant:** `qgis-server-runtime` SA receives `roles/storage.objectViewer` on `grace-2-hazard-prod-runs` so `/vsigs/<runs-bucket>/<run_id>/flood_depth_peak.tif` resolves at WMS render time. Tofu addition; ~5 LOC; verified via `gcloud projects get-iam-policy` + a curl of the WMS GetMap once a layer is registered. | — | planned |
| job-0062-engine-20260608 | engine | **Atomic `publish_layer` tool:** new agent tool that invokes the existing PyQGIS worker round-trip to add a fresh COG as a published WMS layer. Worker: read `gs://grace-2-hazard-prod-qgs/grace2-sample.qgs` (or a per-session project), `QgsRasterLayer("/vsigs/<runs-bucket>/<run_id>/flood_depth_peak.tif")`, `apply_style_preset(layer, "continuous_flood_depth")`, `project.addMapLayer(layer)`, `QgsProject.write()`, Pub/Sub notify. Returns the WMS URL: `<qgis-server>/ogc/wms?MAP=/mnt/qgs/grace2-sample.qgs&LAYERS=<layer_id>`. Cache-shim integration: `cacheable=False` (the worker round-trip is the side effect). The `model_flood_scenario` workflow calls `publish_layer` after `postprocess_flood` succeeds. | job-0060, job-0061 | planned |
| job-0063-engine-20260608 | engine | **(Optional carry-forward):** OQ-59 CRS-label fix in `postprocess_flood` — write the COG's CRS tag from the SFINCS dataset's actual CRS variable (not the .attrs default) so the tag matches the coordinates. ~3 LOC + 1 test. | — | planned |
| job-0064-testing-20260608 | testing | **Sprint-09 acceptance:** drive the full end-to-end via Playwright — user prompt in the chat panel → M5 workflow runs → flood layer appears on the MapLibre basemap → user toggles visibility in LayerPanel. Capture screenshots at 3 states (baseline empty map; mid-run with pipeline strip active; final with flood layer rendered over basemap). The third screenshot is the actual headline deliverable: WEB-UI-RENDERED flood layer, not an orchestrator-direct PNG. Closes sprint-09. | job-0060, job-0061, job-0062 | planned |

## Execution order

```
stage A (parallel, file-disjoint):
  job-0060-engine  (agent return-type change — model_flood_scenario.py)
  job-0061-infra   (IAM grant on runs bucket)
  job-0063-engine  (OQ-59 CRS-label fix — postprocess_flood.py)

stage B:
  job-0062-engine  (atomic publish_layer tool + workflow integration)
  ← gated on 0060 + 0061

stage C (testing):
  job-0064-testing (Playwright end-to-end + 3 screenshots)
  ← gated on 0062 (which is gated on 0060 + 0061)
```

Plus orchestrator-direct **v0.3.20 SRS housekeeping pass** lands in parallel with Stage A.

## Exit criteria

- [ ] **v0.3.20 SRS housekeeping landed** (orchestrator-direct).
- [ ] **`run_model_flood_scenario` returns `LayerURI`** — PipelineEmitter auto-emit fires; `session-state.loaded_layers` populates after M5 success.
- [ ] **QGIS Server SA reads the runs bucket** — `gcloud` policy check + live WMS GetMap verification on a published layer.
- [ ] **`publish_layer` atomic tool registered** — registry shows 17+ tools at startup (16 baseline + `publish_layer`); cache-shim integration verified (cacheable=False).
- [ ] **`model_flood_scenario` integrates `publish_layer`** — workflow calls it after `postprocess_flood` succeeds; the returned WMS URL lands in `ProjectLayerSummary.uri` inside `session-state.loaded_layers`.
- [ ] **End-to-end Playwright run** — user prompt → flood layer rendered on basemap; LayerPanel shows the layer with togglable visibility/opacity; final screenshot captured.
- [ ] **All sprint-8 tests + new sprint-9 tests pass** — agent + contracts + web suites green.
- [ ] **(Optional)** OQ-59 CRS-label fix landed (engine carry-forward).

## Deferred to sprint-10 (or later)

- ATCF Hurricane Ian real storm forcing (`fetch_hurricane_track` + `model_flood_scenario` real-forcing branch).
- Mode 2 `.gov`/`.edu` offer-to-add (envelope shapes + agent emission detection + web popup modal + audit log).
- `map-command(zoom-to)` polish — camera fly-to-bbox after a layer lands. Small follow-up once sprint-9 ships.
- Multi-layer style picker / LayerPanel filter chrome (M3 polish).
- DEFERRED-DEAD cleanup: `LayerPanel.tsx` `case "load-layer"` / `case "remove-layer"` / `case "set-layer-*"` handlers (kept until sprint-10 cleanup so migration is clean).

## Retrospective

_Filled at close._
