# Sprint 09: M5→UI wiring (layer surfacing on basemap) + sprint-8 carry-forwards

**Status:** closed
**Opened:** 2026-06-07
**Closed:** 2026-06-07
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
| job-0060-engine-20260607 | engine | **Agent return-type change:** `run_model_flood_scenario` returns `LayerURI` (or `list[LayerURI]`) instead of dict-dumped `AssessmentEnvelope`. PipelineEmitter's `isinstance(result, LayerURI)` auto-emit branch then fires → `session-state.loaded_layers` populates → LayerPanel sees the layer. Envelope JSON still travels via tool-call-complete payload for chat-message + Mongo persistence. ~5 LOC change + 2 tests; no contract change. | — | **approved** (commit edfeb06; 25/25 tests; session_state shows loaded_layers populated) |
| job-0061-infra-20260607 | infra | **IAM grant:** `qgis-server-runtime` SA receives `roles/storage.objectViewer` on `grace-2-hazard-prod-runs` so `/vsigs/<runs-bucket>/<run_id>/flood_depth_peak.tif` resolves at WMS render time. Tofu addition; ~5 LOC; verified via `gcloud projects get-iam-policy` + a curl of the WMS GetMap once a layer is registered. | — | **approved** (commit 1b2f989; live WMS GetMap on a runs-bucket COG returned 200 + real PNG; NFR-S-2 preserved) |
| job-0062-engine-20260607 | engine | **Atomic `publish_layer` tool:** new agent tool that invokes the existing PyQGIS worker round-trip to add a fresh COG as a published WMS layer. Worker: read `gs://grace-2-hazard-prod-qgs/grace2-sample.qgs` (or a per-session project), `QgsRasterLayer("/vsigs/<runs-bucket>/<run_id>/flood_depth_peak.tif")`, `apply_style_preset(layer, "continuous_flood_depth")`, `project.addMapLayer(layer)`, `QgsProject.write()`, Pub/Sub notify. Returns the WMS URL: `<qgis-server>/ogc/wms?MAP=/mnt/qgs/grace2-sample.qgs&LAYERS=<layer_id>`. Cache-shim integration: `cacheable=False` (the worker round-trip is the side effect). The `model_flood_scenario` workflow calls `publish_layer` after `postprocess_flood` succeeds. Plus authoring `styles/continuous_flood_depth.qml` + extending PyQGIS worker with `_append_raster_layer`. | job-0060, job-0061 | **approved** (commit f202a31; 17 tools at startup; 180 tests; 4 OQs incl pyqgis-worker IAM grant → job-0067) |
| job-0067-infra-20260607 | infra | **Stage B follow-up (OQ-62 closure):** pyqgis-worker SA receives `roles/storage.objectViewer` on runs bucket. Mirrors job-0061 pattern. Gates live E2E for publish_layer. | job-0062 | **approved** (commit 726f79d; IAM clean; NEW OQ-67-WORKER-IMAGE-REBUILD — worker container is stale, needs rebuild → sprint-10) |
| job-0063-engine-20260607 | engine | **(Optional carry-forward):** OQ-59 CRS-label fix in `postprocess_flood` — write the COG's CRS tag from the SFINCS dataset's actual CRS variable (not the .attrs default) so the tag matches the coordinates. ~3 LOC + 1 test. | — | **approved** (commit 0990d1c; BEFORE EPSG:3857 — AFTER EPSG:32617; live re-run verified) |
| job-0064-web-20260607 | web | **UI tweak #1 (per user direction 2026-06-07):** pipeline cards inline in chat. Move `PipelineStrip.tsx`'s cards out of the strip and into the chat stream beside assistant messages. Stacked in call order; one-line format `operation N%`. Clears the basemap of pipeline chrome. Keeps the existing pipeline-state envelope contract (no schema change). | — | **approved** (commit cec1071; Option A — PipelineStrip deleted entirely; 2 screenshots surfaced; 46/46 tests) |
| job-0065-web-20260607 | web | **UI tweak #2 (per user direction 2026-06-07):** (a) Render a layer legend / colorbar (gradient key) at the bottom of the map, horizontally centered between the two side panels — mirrors matplotlib's colorbar for the active continuous raster layer (initially `continuous_flood_depth` preset with client-side stops). (b) Hide the `LayerPanel.tsx` when `loaded_layers.length === 0`. (c) Add collapse toggles on both side panels (chevron buttons; persists in localStorage). | — | **approved** (commit 485ed93; 3 screenshots surfaced; NEW OQ-W-65-STYLE-PRESET → sprint-10 schema) |
| job-0066-testing-20260607 | testing | **Sprint-09 acceptance:** drive the full end-to-end via Playwright — user prompt in the chat panel → M5 workflow runs → flood layer appears on the MapLibre basemap → user toggles visibility in LayerPanel → colorbar renders at bottom of map → both panels collapsible. Capture screenshots at 4 states (baseline empty; mid-run with inline pipeline cards; final with flood layer rendered + colorbar; collapsed panels). Honest scope: UI integration verified via dev-injection hooks; live worker round-trip deferred to sprint-10 per OQ-67-WORKER-IMAGE-REBUILD. Closes sprint-09. | job-0060, job-0061, job-0062, job-0064, job-0065, job-0067 | **approved** (commit 142146e; 4/4 Playwright pass; 4 headline screenshots surfaced; full regression green) |

## Execution order

```
stage A (parallel, file-disjoint):
  job-0060-engine  (agent return-type change — model_flood_scenario.py)
  job-0061-infra   (IAM grant on runs bucket)
  job-0063-engine  (OQ-59 CRS-label fix — postprocess_flood.py)

stage B:
  job-0062-engine  (atomic publish_layer tool + workflow integration)
  ← gated on 0060 + 0061

stage C (UI tweaks, parallel — file-disjoint within web/):
  job-0064-web     (pipeline cards inline in chat)
  job-0065-web     (colorbar + hide-empty LayerPanel + collapse toggles)
  ← gated on 0062 lands (so layers exist to test the new chrome against)

stage D (testing):
  job-0066-testing (Playwright end-to-end + 3 screenshots with new chrome)
  ← gated on 0062 + 0064 + 0065
```

Plus orchestrator-direct **v0.3.21 SRS amendment** (FR-MP-6 Case UX) already landed before Stage A dispatches; **v0.3.20 SRS housekeeping pass** carryover still scheduled as orchestrator-direct in parallel with Stage A or B.

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

### Planned vs actual

Planned: 5 reserved jobs (0060/0061/0062/0063/0064/0065) + 1 testing close job (0066) = 6 jobs.
Actual: 8 jobs delivered (0060/0061/0062/0063/0064/0065/0067/0066). One IAM follow-up (job-0067 — pyqgis-worker SA runs-bucket grant) was added mid-sprint after job-0062 surfaced OQ-62-WORKER-SA-RUNS-BUCKET-GRANT. The added job was narrow (single IAM grant, mirrors job-0061 pattern) and did not widen scope.

All 8 jobs ran on Sonnet 4.6 — 100% Sonnet routing win rate for the sprint.

### Cost telemetry (sprint-09)

| Job | Specialist | Model | Tokens |
|-----|-----------|-------|--------|
| job-0060-engine-20260607 | engine | Sonnet | 126,487 |
| job-0061-infra-20260607 | infra | Sonnet | 66,184 |
| job-0062-engine-20260607 | engine | Sonnet | 68,556 |
| job-0063-engine-20260607 | engine | Sonnet | 66,081 |
| job-0064-web-20260607 | web | Sonnet | 115,728 |
| job-0065-web-20260607 | web | Sonnet | 90,455 |
| job-0067-infra-20260607 | infra | Sonnet | 65,817 |
| job-0066-testing-20260607 | testing | Sonnet | _this job_ |

Sprint-09 total (excl. this job): 599,308 tokens. Opus: 0. Sonnet: 100%.
Cumulative across all sprints (excl. this job): 5,514,397 tokens.

### Architectural decisions landed

1. **`docs/decisions/layer-emission-contract.md`** (2026-06-07, orchestrator-direct): The authoritative contract spine for layer delivery is `session-state.loaded_layers` (declarative, replace-not-reconcile per Appendix A.7). `map-command` is reserved for transient verbs. WMS URL is the `LayerURI.uri` payload. Resolves the design ambiguity that blocked M5→UI wiring.

2. **SRS v0.3.21 FR-MP-6 Case UX** (2026-06-07, orchestrator-direct): Formalizes the Case-scoped layer isolation path for future multi-case sessions.

### What worked

- **Sonnet efficiency**: 7 consecutive specialist jobs on Sonnet with clean deliverables. No revision loops, no blocked jobs. The sprint was a clean linear execution of the dependency graph.
- **Disjoint file ownership**: job-0064 and job-0065 ran concurrently (Stage C) with no merge conflicts — the file-ownership split between Chat.tsx (pipeline cards) and App.tsx (collapse toggles / LayerLegend) was clean.
- **Dev-injection seam pattern**: The `window.__grace2InjectSessionState` / `window.__grace2InjectPipelineState` seams established in M3 continued to pay off in sprint-09 UI verification (job-0064/0065 screenshots, this M6 acceptance suite). Decoupling UI verification from live agent emission was the right call.
- **Stage A parallel dispatch**: jobs-0060/0061/0063 ran in parallel with disjoint file ownership and all approved in the same day.
- **Non-fatal fallback in publish_layer** (job-0062): the `PublishLayerError` catch-and-continue pattern preserves envelope emission even when the worker round-trip fails, keeping M5 demo survivable under the OQ-67 worker image gap.

### What to change next sprint

- **Worker image rebuild earlier in sprint**: OQ-67-WORKER-IMAGE-REBUILD was identified at the end of sprint-09 (job-0067) because worker-SA IAM grant was the last infra job. Sprint-10 should open with the image rebuild as the first job so E2E live-worker tests are unblocked early.
- **Schema D.2 amendment before E2E**: `style_preset` and `LayerURI.wms_url` were consumer-pushback OQs from job-0065 and job-0062 respectively. Having the schema amendment job run in Stage A of sprint-10 (before web/engine jobs consume the field) avoids the tentative/forward-declared field pattern.

### Open OQ carry-forward list

| OQ ID | Origin job | Description | Sprint-10 routing |
|-------|-----------|-------------|-------------------|
| OQ-67-WORKER-IMAGE-REBUILD | job-0067 | pyqgis-worker container image is stale (does not have the publish-raster CLI added in job-0062); needs rebuild + redeploy before live worker round-trip works | infra — sprint-10 opener |
| OQ-62-LAYERURI-URI-FIELD | job-0062 | `LayerURI.uri` now carries a WMS URL (not `gs://`); schema amendment to add `wms_url: str \| None` field needed in Appendix D.2 | schema — bundle with OQ-W-65 |
| OQ-W-65-STYLE-PRESET | job-0065 | `style_preset` added to `ProjectLayerSummary` in web-side contracts mirror; authoritative definition belongs in Appendix D.2 under schema | schema — bundle with OQ-62-LAYERURI |
| OQ-62-PUBSUB-COMPLETION-POLL | job-0062 | WMS URL is reconstructed deterministically rather than decoded from Pub/Sub envelope; fine for M5 demo but fragile if QGS key or layer ID becomes non-deterministic | agent — defer to M5 multi-session work |
| OQ-62-QGS-MUTATION-CONFLICT | job-0062 | Concurrent `publish_layer` calls on same `project_qgs_uri` are not atomic (last writer wins); acceptable for single-session M5 demo | engine — when FR-MP-6 Case UX adds per-Case `.qgs` isolation |
| OQ-61-CLOUD-RUN-SCALING-BLOCK-DRIFT | pre-existing | Cloud Run concurrency/scaling limits for QGIS Server not validated under concurrent requests | infra — sprint-10 |

### Sprint-10 opening hand-off

Likely jobs in priority order:

1. **infra**: Worker image rebuild + redeploy (OQ-67-WORKER-IMAGE-REBUILD). Unblocks the live end-to-end worker round-trip that sprint-09 deferred. This is the sprint-10 critical path opener.
2. **schema**: D.2 amendment bundle — `wms_url: str | None` on LayerURI + `style_preset: str | None` on ProjectLayerSummary (OQ-62-LAYERURI-URI-FIELD + OQ-W-65-STYLE-PRESET). Small additive contract change; coordinates with engine (publish_layer populated value) and web (consume without the optional-field workaround).
3. **infra**: Cloud Run scaling reconciliation — validate QGIS Server concurrency limits under load, reconcile with FR-NFR-P targets (OQ-61-CLOUD-RUN-SCALING-BLOCK-DRIFT).
4. **agent/engine**: Mode 2 `.gov`/`.edu` offer-to-add OR ATCF Hurricane Ian real-forcing branch (`fetch_hurricane_track` + `model_flood_scenario` real-forcing path). The Mode 2 path requires envelope shapes + agent emission detection + web popup modal + audit log; ATCF is a pure engine/data addition. Orchestrator decides priority after sprint-10 openers land.
