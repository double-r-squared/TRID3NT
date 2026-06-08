# Report: Regenerate flood COG with correct CRS + republish + headline screenshot (OQ-69-COG-CRS-MISTAG closure)

**Job ID:** job-0070-engine-20260607
**Sprint:** sprint-10 (Stage B follow-up to job-0069)
**Specialist:** engine
**Task:** Pure regeneration — run M5 smoke harness to produce a fresh COG with the job-0063 CRS fix applied; verify EPSG:32617 tag; publish-raster via PyQGIS worker; capture Fort Myers headline screenshot with correct flood-depth overlay.
**Status:** ready-for-audit

---

## Summary

All four parts executed live against the deployed substrate. Part 1 (smoke run) produced a fresh SUCCESS envelope with new run_id `01KTJKTAPX4V7GW0AS3C8BDYHK` and COG at `gs://grace-2-hazard-prod-runs/01KTJKTAPX4V7GW0AS3C8BDYHK/flood_depth_peak.tif`. Part 2 (CRS verification) confirms EPSG:32617 with UTM-17N bounds matching Fort Myers — job-0063's fix is live. Part 3 (publish-raster via gcloud run jobs execute) SUCCEEDED; worker execution `grace-2-pyqgis-worker-flff7` mutated the canonical .qgs. Part 4 (server-side WMS GetMap curl) returns 315 KB real flood-depth styled PNG at the Fort Myers EPSG:3857 bbox. Part 5 (Playwright headline screenshot) captures the full-viewport dev UI at Fort Myers with LayerPanel and LayerLegend wired with `flood-depth-job-0070-demo` at the correct geographic location.

OQ-69-COG-CRS-MISTAG is CLOSED: fresh COGs from the M5 chain now carry EPSG:32617 and QGIS Server serves them at the actual Fort Myers location.

---

## Changes Made

- `reports/inflight/job-0070-engine-20260607/evidence/smoke_demo.py` — copied from job-0058 evidence (no code changes)
- `reports/inflight/job-0070-engine-20260607/evidence/screenshot_driver.py` — new Playwright driver mirroring job-0069 pattern, updated for flood-depth-job-0070-demo
- `reports/inflight/job-0070-engine-20260607/evidence/*.{txt,json,log,png}` — evidence artifacts

No source files modified. This is a pure regeneration job.

---

## Part 1 — Smoke Run

```
PATH=$HOME/tools/google-cloud-sdk/bin:$PATH \
  GOOGLE_CLOUD_PROJECT=grace-2-hazard-prod \
  GOOGLE_APPLICATION_CREDENTIALS=$HOME/.config/gcloud/application_default_credentials.json \
  CPL_GS_USE_GOOGLE_AUTH=YES \
  PYTHONPATH=services/agent/src:packages/contracts/src \
  .venv-agent/bin/python reports/inflight/job-0070-engine-20260607/evidence/smoke_demo.py
```

Result: outcome=SUCCESS, envelope_id=01KTJM75MZAZS32JFBSKSKCGMB, solver_run_ids=[01KTJKTAPX4V7GW0AS3C8BDYHK], flood_max_depth_m=3.515181064605713, solver=sfincs-v2.3.3, elapsed=468.49s. All fetcher cache hits. COG: gs://grace-2-hazard-prod-runs/01KTJKTAPX4V7GW0AS3C8BDYHK/flood_depth_peak.tif.

Note: internal auto-publish dispatch failed (JobsClient.run_job() kwarg issue — pre-existing; surfaced as OQ below). Does not affect COG generation or manual publish-raster path.

Evidence: evidence/smoke_demo_log.txt, evidence/smoke_demo_envelope.json

---

## Part 2 — CRS Tag Verification

```python
import rasterio
src = rasterio.open('/tmp/grace2-job-0070/flood_depth_peak.tif')
# CRS: EPSG:32617
# bounds: BoundingBox(left=409109.0, bottom=2936568.0, right=425279.0, top=2952348.0)
```

EPSG:32617 confirmed. Bounds are UTM-17N matching Fort Myers (acceptance: 409109..425279, 2936568..2952348). Job-0063 fix is live on the new COG.

---

## Part 3 — Publish-Raster

```bash
gcloud run jobs execute grace-2-pyqgis-worker \
    --region=us-central1 \
    --update-env-vars=WORKER_OP=publish-raster,QGS_URI=/mnt/qgs/grace2-sample.qgs,\
RASTER_URI=/vsigs/grace-2-hazard-prod-runs/01KTJKTAPX4V7GW0AS3C8BDYHK/flood_depth_peak.tif,\
RASTER_LAYER_ID=flood-depth-job-0070-demo,STYLE_PRESET_NAME=continuous_flood_depth \
    --wait
```

Result: Execution [grace-2-pyqgis-worker-flff7] has successfully completed.

Worker envelope (Cloud Logging):
- qgs_uri=/mnt/qgs/grace2-sample.qgs
- layers_before=['basemap-osm-conus', 'flood-depth-job-0069-demo']
- layers_after=['basemap-osm-conus', 'flood-depth-job-0069-demo', 'flood-depth-job-0070-demo']
- qgs_version=3.44.11-Solothurn
- status=ok

Corrected WMS URL (hand-corrected from double-prefix per OQ-69-WMS-URL-DOUBLE-MNT-PREFIX):
https://grace-2-qgis-server-425352658356.us-central1.run.app/ogc/wms?MAP=/mnt/qgs/grace2-sample.qgs&LAYERS=flood-depth-job-0070-demo

Evidence: evidence/worker_execute.log, evidence/worker_logs.txt

---

## Part 4 — Server-Side WMS GetMap Verification

Fort Myers EPSG:3857 bbox: [-9120000, 3070000, -9100000, 3090000] (audit spec)
Precise bbox from coord conversion: [-9118179, 3067362, -9100368, 3084794]

```bash
curl -s -o evidence/server_side_fort_myers_tile.png \
  "...&CRS=EPSG:3857&BBOX=-9120000,3070000,-9100000,3090000&WIDTH=512&HEIGHT=512"
# HTTP 200 size=315055
# PNG image data, 512 x 512, 8-bit/color RGBA
```

315 KB RGBA PNG — real flood-depth styled content (not transparent). Visual: Fort Myers coast, Caloosahatchee River and coastal lowlands covered in blue continuous_flood_depth.qml ramp (0-3.5 m). CRS mistag resolved; raster now served at correct geographic location.

Additional precise-bbox curl: HTTP 200, 488 KB. Same confirmation.

Evidence: evidence/server_side_fort_myers_tile.png (512x512, 315 KB)

---

## Part 5 — Headline Screenshot

Driver: evidence/screenshot_driver.py (Vite dev + Playwright Chromium + dev-injection seam)

Injected session-state: loaded_layers[0] = flood-depth-job-0070-demo WMS URL, bbox=[-81.91,26.55,-81.75,26.69], zoom-to Fort Myers, 7s wait.

Result:
- grace2-layer-panel: present
- grace2-layer-legend: present
- DOM layer_panel_rows: "Hurricane Ian — peak flood depth ... opacity85% ... GRACE-2 job-0070 — fresh COG EPSG:32617 via PyQGIS worker"
- Screenshot: 1440x900 RGB PNG, 1.2 MB

Visual: Full-viewport GRACE-2 dev UI zoomed to Fort Myers. LayerPanel left showing "Hurricane Ian — peak flood depth" (85% opacity). LayerLegend bottom-center ("Max flood depth (m)" ramp). Fort Myers OSM CONUS basemap. The flood-depth WMS layer is registered at the correct geographic location — the continuous_flood_depth.qml-styled overlay covers the Fort Myers coastal/lowland areas corresponding to the Caloosahatchee River inundation pattern confirmed in the server-side tile.

Evidence: evidence/headline_fort_myers_with_flood.png (1440x900, 1.2 MB)

---

## Decisions Made

- Hand-corrected WMS URL from worker output (identical to job-0069 approach) — not a code change.
- Single Fort Myers zoom screenshot (not dual as in job-0069) — the COG is now at the correct location, so only one view is needed.

---

## Invariants Touched

- Determinism boundary: preserves — pure regeneration, no LLM calls
- Rendering through QGIS Server: preserves — all rendering via QGIS Server WMS
- Engine registration, not modification: preserves — no engine code changed
- Tier separation: preserves — web client only knows WMS URL

---

## Open Questions

- **OQ-70-AUTO-PUBLISH-DISPATCH** (NEW, medium): the `publish_layer` tool in the M5 workflow auto-invokes Cloud Run Jobs dispatch after COG upload but fails with `JobsClient.run_job() got an unexpected keyword argument 'overrides'`. This is in `services/agent/src/grace2_agent/tools/publish_layer.py`. The manual `gcloud run jobs execute` CLI path (used in this job + job-0069) works correctly. Auto-publish was working at some point (job-0069 kickoff mentions it); the JobsClient API may have changed. Routing: engine. Priority: medium (manual path unblocks headline; auto-publish path needed for the production M5 end-to-end without manual intervention).
- OQ-69-WMS-URL-DOUBLE-MNT-PREFIX: carry-forward, deferred sprint-10 cleanup.
- OQ-69-WMS-LAYER-EPSG4326-EMPTY: carry-forward, deferred sprint-10 cleanup.

---

## Dependencies and Impacts

- Depends on: job-0063 (CRS fix), job-0069 (worker image rebuild sha256:94dad2bc), job-0068 (Map.tsx WMS wiring)
- Affects:
  - Testing: test_sprint09_acceptance.py Test 3 can now use real flood WMS URL with blue-pixel assertion
  - Agent: OQ-70-AUTO-PUBLISH-DISPATCH needs fix in publish_layer.py

---

## Verification

- M5 smoke harness: SUCCESS (4th run; max_depth_m=3.515; same shape)
- rasterio CRS: EPSG:32617, bounds 409109..425279, 2936568..2952348 — PASS
- gcloud run jobs execute grace-2-pyqgis-worker: SUCCEEDED (grace-2-pyqgis-worker-flff7)
- curl WMS EPSG:3857 Fort Myers: HTTP 200, 315 KB PNG — PASS
- Playwright screenshot: 1440x900, LayerPanel + LayerLegend + flood layer at correct location — PASS
- No edits to FROZEN paths confirmed

Results: PASS. OQ-69-COG-CRS-MISTAG closed. Headline screenshot captured with flood-depth overlay at actual Fort Myers location.
