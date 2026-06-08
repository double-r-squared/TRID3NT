# Audit: regenerate flood COG with correct CRS + republish + headline screenshot (OQ-69-COG-CRS-MISTAG closure)

**Job ID:** job-0070-engine-20260607, **Sprint:** sprint-10 (tight follow-up to job-0069 — the headline-screenshot unblocker), **Auditor:** Development Orchestrator, **Status:** assigned

## Task Assignment

**Specialist:** engine

**Prerequisites (ALL APPROVED):**
- **job-0063 (commit 0990d1c):** CRS-label fix in `postprocess_flood._read_crs_from_dataset` — fresh M5 runs now write the correct EPSG:32617 tag.
- **job-0069 (commit 0aa6d46):** PyQGIS worker image rebuild + live publish-raster + dev-UI screenshot. **Surfaced OQ-69-COG-CRS-MISTAG (HIGH).** The job-0066 COG predates job-0063 — has EPSG:3857 metadata but UTM-17N coordinates; QGIS Server publishes it off N Africa.
- **job-0068 (commit e5398b8):** Map.tsx WMS source wiring + dev-injection seam.

**SRS references:** none beyond what's already in force.

**Required reads:**
- `reports/complete/job-0069-infra-20260607/report.md` — the diagnosis + the live invocation pattern that worked
- `reports/complete/job-0063-engine-20260607/report.md` — the CRS fix that ensures fresh COGs are written correctly
- `reports/complete/job-0058-engine-20260607/evidence/smoke_demo.py` — the harness you'll re-run

### Why this job exists

Job-0069 demonstrated the live M5-to-UI pipeline works end-to-end. The remaining gap to a real Fort-Myers-with-flood headline screenshot is a stale COG. The job-0066 flood COG at `gs://grace-2-hazard-prod-runs/01KTJ3PP1JMF96WR4CCZZ4JRYS/flood_depth_peak.tif` predates job-0063's CRS fix — it carries the wrong metadata tag. Today's `postprocess_flood` writes the right tag. Generating a fresh COG via the same M5 chain unblocks the user's headline.

This is also a chance to re-verify the full M5 chain end-to-end (a 4th live SFINCS run; should reproduce the same shape per job-0059's reproducibility evidence).

### Scope

#### Part 1 — Regenerate the flood COG (the actual fix for the CRS mistag)

Copy `reports/complete/job-0058-engine-20260607/evidence/smoke_demo.py` to `reports/inflight/job-0070-engine-20260607/evidence/smoke_demo.py` and run it:

```
PATH=$HOME/tools/google-cloud-sdk/bin:$PATH \
  GOOGLE_CLOUD_PROJECT=grace-2-hazard-prod \
  GOOGLE_APPLICATION_CREDENTIALS=$HOME/.config/gcloud/application_default_credentials.json \
  CPL_GS_USE_GOOGLE_AUTH=YES \
  PYTHONPATH=services/agent/src:packages/contracts/src \
  .venv-agent/bin/python reports/inflight/job-0070-engine-20260607/evidence/smoke_demo.py
```

Capture: full log to `evidence/smoke_demo_log.txt`, envelope JSON to `evidence/smoke_demo_envelope.json`. Expected: another reproducible SUCCESS — `AssessmentEnvelope.outcome=SUCCESS`, `flood_max_depth_m≈3.5`, new run_id, new COG GCS URI.

**Confirm the CRS tag is now correct.** Download the new COG and verify with rasterio:
```
PATH=$HOME/tools/google-cloud-sdk/bin:$PATH gsutil cp gs://grace-2-hazard-prod-runs/<NEW_RUN_ID>/flood_depth_peak.tif /tmp/grace2-job-0070/
.venv-agent/bin/python -c "import rasterio; src = rasterio.open('/tmp/grace2-job-0070/flood_depth_peak.tif'); print('CRS:', src.crs, 'bounds:', src.bounds)"
```
Expected: `CRS: EPSG:32617` (NOT EPSG:3857). `bounds:` should be UTM-17N values around 409109..425279, 2936568..2952348 (matching the Fort Myers area).

#### Part 2 — Trigger publish-raster on the fresh COG

Invoke the (now-rebuilt-by-0069) PyQGIS worker against the NEW COG:

```
PATH=$HOME/tools/google-cloud-sdk/bin:$PATH gcloud run jobs execute grace-2-pyqgis-worker \
    --region=us-central1 \
    --update-env-vars=OP=publish-raster,QGS_URI=gs://grace-2-hazard-prod-qgs/grace2-sample.qgs,RASTER_URI=/vsigs/grace-2-hazard-prod-runs/<NEW_RUN_ID>/flood_depth_peak.tif,RASTER_LAYER_ID=flood-depth-job-0070-demo,STYLE_PRESET_NAME=continuous_flood_depth \
    --wait
```

Expected: Cloud Run Job SUCCEEDED. Pub/Sub completion envelope includes the new WMS URL: `https://grace-2-qgis-server-.../ogc/wms?MAP=/mnt/qgs/grace2-sample.qgs&LAYERS=flood-depth-job-0070-demo`.

Capture the execution log + WMS URL.

#### Part 3 — Curl-verify the WMS GetMap renders at Fort Myers EPSG:3857 bbox

```
curl -o /tmp/grace2-job-0070/server_side_fort_myers_tile.png \
    "https://grace-2-qgis-server-425352658356.us-central1.run.app/ogc/wms?MAP=/mnt/qgs/grace2-sample.qgs&SERVICE=WMS&VERSION=1.3.0&REQUEST=GetMap&LAYERS=flood-depth-job-0070-demo&BBOX=-9120000,3070000,-9100000,3090000&CRS=EPSG:3857&WIDTH=512&HEIGHT=512&FORMAT=image/png&TRANSPARENT=true"
file /tmp/grace2-job-0070/server_side_fort_myers_tile.png
```

(BBOX above is roughly Fort Myers in Web Mercator. Adjust if needed via online EPSG:4326→EPSG:3857 converter for the actual Fort Myers bbox `[-81.91, 26.55, -81.75, 26.69]`.)

Expected: a real flood-depth styled PNG (not a 1.1 KB transparent empty tile per OQ-69-WMS-LAYER-EPSG4326-EMPTY which was the 4326 issue — 3857 should work fine per job-0069's evidence). Save to `evidence/server_side_fort_myers_tile.png`.

#### Part 4 — Capture the headline Fort Myers screenshot

Drive the dev UI via the existing Playwright + dev-injection pattern (mirror job-0069's Part 4 exactly):

```
cd web && npm run dev &   # start dev server in background
# Use a Playwright script (look at how job-0069 did it; tests/m6/playwright/*.py exist)
# Inject session-state with loaded_layers carrying the new WMS URL + bbox=[-81.91,26.55,-81.75,26.69]
# Inject map-command(zoom-to, {bbox: [-81.91, 26.55, -81.75, 26.69]})
# Wait 5 seconds for the WMS tiles to load
# Screenshot to evidence/headline_fort_myers_with_flood.png
```

Expected: full-viewport map at Fort Myers, LayerPanel left ("flood-depth-job-0070-demo"), LayerLegend bottom-center, **and a distinct blue flood-depth overlay** — geographically aligned to Fort Myers this time (not off N Africa).

**This is THE headline.**

### File ownership (exclusive)

- `reports/inflight/job-0070-engine-20260607/`

### FROZEN

- ALL source files. **This is a regeneration job**, not a code-change job. The CRS fix already shipped (job-0063); we're just running the chain to produce a fresh COG with the fix applied. The two medium-priority job-0069 OQs (OQ-69-WMS-URL-DOUBLE-MNT-PREFIX, OQ-69-WMS-LAYER-EPSG4326-EMPTY) are deferred to a separate sprint-10 cleanup pass — don't touch worker.py here.
- `reports/complete/**`

### Acceptance criteria

- [ ] **Fresh M5 chain run** — AssessmentEnvelope.outcome=SUCCESS; new COG at `gs://grace-2-hazard-prod-runs/<NEW_RUN_ID>/flood_depth_peak.tif`
- [ ] **CRS tag verified correct** — rasterio reports `EPSG:32617`; bounds are UTM-17N values matching Fort Myers
- [ ] **publish-raster live succeeded** on the new COG — new WMS URL captured
- [ ] **Server-side WMS GetMap returns real raster at Fort Myers EPSG:3857 bbox** (not transparent empty)
- [ ] **Headline screenshot captured** — full-viewport map at Fort Myers + LayerPanel left + LayerLegend bottom + distinct blue flood overlay over Fort Myers coastal/lowland areas
- [ ] **No edits to FROZEN paths** (no source-file changes; this is regeneration only)
- [ ] **Single commit**

### Honest disclosure

If the chain produces a SUCCESS COG but the new CRS tag is somehow STILL wrong (job-0063 should have made this impossible — but verify), surface as OQ-70-* and route. If the publish-raster live fails for a new reason, similarly honest disclose. The user wants the headline; if we can't get there cleanly, honest pause is better than fabricated success.
