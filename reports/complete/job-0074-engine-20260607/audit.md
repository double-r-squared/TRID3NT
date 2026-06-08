# Audit: worker bug fixes + rebuild + App.tsx wiring + Fort Myers re-screenshot (Stage 2 — the live verification)

**Job ID:** job-0074-engine-20260607, **Sprint:** sprint-10 Stage 2 (the live verification gate), **Auditor:** Development Orchestrator, **Status:** assigned

## Task Assignment

**Specialist:** engine (cross-specialty: also touches infra worker.tf + 1 line in web/App.tsx)

**Prerequisites (ALL APPROVED):**
- **job-0071 (commit 0cbfa43):** postprocess_flood rotation fix + transparency belt+suspenders (data + QML) + CRS_TAG_MISMATCH guard + publish_layer dispatch fix
- **job-0072 (commit 876798b):** ProjectLayerSummary D.2 amendment + ws.ts map-command routing; surfaced OQ-72-APP-MAPCMD-WIRING (1-line App.tsx wire-up to close production routing)
- **job-0073 (commit 2ed3efa):** Cloud Run scaling drift reconciled — untargeted tofu plan now clean
- jobs 0069 + 0070 — established worker rebuild rhythm (Cloud Build → digest pin → targeted tofu apply); existing flood COG at `gs://grace-2-hazard-prod-runs/01KTJKTAPX4V7GW0AS3C8BDYHK/flood_depth_peak.tif` from job-0070 (correct EPSG:32617 tag)

**SRS references:** none beyond what's in force.

**Required reads:**
- `services/workers/pyqgis/worker.py` lines 800–900 (find `_build_wms_url` near line 825; find `_append_raster_layer` near line 430)
- `reports/complete/job-0069-infra-20260607/report.md` — the worker rebuild rhythm + surfaced OQ-69 bugs
- `reports/complete/job-0070-engine-20260607/report.md` — the proven publish-raster invocation pattern
- `reports/complete/job-0072-schema-20260607/report.md` — the 1-line App.tsx wiring closeout
- `infra/worker.tf` — current image digest pin (sha256:94dad2bc from job-0069)

### Why this job exists

Stage 1 landed 4 code-level fixes that need to ship to the deployed worker container before the user-facing screenshot reflects them. Two additional worker.py bugs from job-0069's evidence remain (DOUBLE-MNT prefix + EPSG:4326 empty tile) and need to land in the same rebuild cycle. Plus there's a 1-line App.tsx wiring closeout from job-0072 that completes the production map-command routing. Bundling all of these into one job avoids running multiple expensive Cloud Build + tofu apply cycles.

This is THE final corrected screenshot of sprint-10 — flood layer rendered correctly oriented + transparency-only-where-flooded + at Fort Myers + with production-routing wiring complete.

### Scope — 5 parts in one commit

#### Part 1 — Worker.py bug fixes

**Bug 1: OQ-69-WMS-URL-DOUBLE-MNT-PREFIX** (`services/workers/pyqgis/worker.py:825-829`):

Read the `_build_wms_url` function. When `read_path` is already a local-mode path (starts with `/mnt/qgs/`), the current code passes `read_path.lstrip("/")` (= `mnt/qgs/grace2-sample.qgs`) to a path constructor that prepends `/mnt/qgs/` again — producing `MAP=/mnt/qgs/mnt/qgs/grace2-sample.qgs`. 

Fix: detect when `read_path.startswith("/mnt/qgs/")` and use just `Path(read_path).name` (the basename) instead, or pass the path as-is without prefix re-prepending. The fix should preserve behavior for the gs:// and /vsigs/ source paths (which DON'T start with `/mnt/qgs/`).

**Bug 2: OQ-69-WMS-LAYER-EPSG4326-EMPTY** (`services/workers/pyqgis/worker.py` `_append_raster_layer` near line 430):

When QGIS Server publishes the layer via WMS, it only advertises the layer's native CRS. Requests in EPSG:4326 return transparent empty tiles. The fix is to declare additional supported CRSes on the layer at publish time. In PyQGIS:

```python
# After project.addMapLayer(layer):
layer.setCrs(QgsCoordinateReferenceSystem(layer.crs().authid()))  # ensure native CRS set
# Then on the project's WMS capabilities config:
project.writeEntry("WMSCrsList", "/", ["EPSG:3857", "EPSG:4326", "EPSG:32617"])
# Or per-layer via the project's wms_crs property — verify the API actually exists
```

Note: the exact PyQGIS API for adding CRSes to a layer's WMS capabilities varies by QGIS version. Investigate via:
```
python -c "from qgis.core import QgsProject; help(QgsProject.instance().writeEntry)"
```
Verify the right key/value combination. If the API doesn't cleanly support this, fall back to "QGIS Server's auto-reprojection on demand" — i.e. accept EPSG:3857 as the only working CRS (production MapLibre uses 3857 so this is non-blocking) and document the OQ as carry-forward rather than forcing a fix.

#### Part 2 — App.tsx 1-line wiring (OQ-72-APP-MAPCMD-WIRING closure)

Find the existing `WsHandlers` props wiring in `web/src/App.tsx`. Add the `onMapCommand` callback:

```typescript
// Existing pattern (illustrative):
// const wsHandlers: WsHandlers = {
//   onSessionState: (p) => bus.pushSessionState(p),
//   onPipelineState: (p) => bus.pushPipelineState(p),
// };

// Add this line:
//   onMapCommand: (p) => bus.pushMapCommand(p),
```

(Adjust to the actual structure in App.tsx; job-0072's report says `WsHandlers` accepts an optional `onMapCommand` callback that mirrors the existing dev-injection seam.)

Verify with `cd web && npm run test` — existing 63 tests stay green; no new tests needed for a 1-liner like this (job-0072 already tested the ws.ts dispatch side).

#### Part 3 — Worker rebuild + redeploy

Mirror job-0069's rhythm exactly:

1. `make worker-build` (from repo root) — Cloud Build push to Artifact Registry; last line of stdout has the new digest
2. Capture digest to `evidence/worker_image_digest.txt`
3. Update `image =` pin in `infra/worker.tf`
4. Targeted apply: `cd infra && $HOME/tools/opentofu/tofu apply -target=google_cloud_run_v2_job.pyqgis_worker -auto-approve` (the OQ-61 untargeted-plan drift is now resolved per job-0073, so untargeted apply should also be clean — but targeted is still safer for an image-only change)
5. Verify: `gcloud run jobs describe grace-2-pyqgis-worker --region=us-central1 --format='value(template.containers.image)'` should show your new digest

#### Part 4 — Re-publish on the job-0070 COG

The COG already lives at `gs://grace-2-hazard-prod-runs/01KTJKTAPX4V7GW0AS3C8BDYHK/flood_depth_peak.tif` (correct EPSG:32617 tag from job-0070). Don't regenerate it — re-use.

Trigger publish-raster with a NEW layer_id so it doesn't conflict with the existing `flood-depth-job-0070-demo`:

```
PATH=$HOME/tools/google-cloud-sdk/bin:$PATH gcloud run jobs execute grace-2-pyqgis-worker \
    --region=us-central1 \
    --update-env-vars=OP=publish-raster,QGS_URI=gs://grace-2-hazard-prod-qgs/grace2-sample.qgs,RASTER_URI=/vsigs/grace-2-hazard-prod-runs/01KTJKTAPX4V7GW0AS3C8BDYHK/flood_depth_peak.tif,RASTER_LAYER_ID=flood-depth-job-0074-demo,STYLE_PRESET_NAME=continuous_flood_depth \
    --wait
```

Expected: SUCCEEDED + .qgs mutated (now 4 layers). WMS URL: `https://grace-2-qgis-server-.../ogc/wms?MAP=/mnt/qgs/grace2-sample.qgs&LAYERS=flood-depth-job-0074-demo`.

#### Part 5 — Live verification + headline re-screenshot

Curl the WMS URL at Fort Myers EPSG:3857 bbox (`[-9120000, 3070000, -9100000, 3090000]`). Expected: real flood-depth styled PNG showing:
- (a) Correct orientation (no 90° rotation — rivers running E/W look E/W)
- (b) Transparency where dry — only inundation visible; background is transparent (not faint blue tint)
- (c) Real flood-depth color ramp from job-0071's QML edit

If the curl looks right, proceed to UI screenshot. If it looks wrong, diagnose before screenshotting (capture the broken curl as `server_side_FAILED.png` + diagnose root cause).

Drive the dev UI via the existing Playwright + dev-injection seam (mirror job-0070 exactly):

```
cd web && npm run dev &
# Use Playwright (look at job-0069/0070 screenshot_driver.py) to:
# - Load the app
# - Inject session-state with loaded_layers: [{layer_id: "flood-depth-job-0074-demo", uri: "<the new WMS URL>", style_preset: "continuous_flood_depth", visible: true, role: "primary", bbox: [-81.91, 26.55, -81.75, 26.69], opacity: 0.9}]
# - Inject map-command(zoom-to, {bbox: [-81.91, 26.55, -81.75, 26.69]})
# - Wait 5 seconds for tiles to load
# - Screenshot to evidence/headline_fort_myers_FINAL.png
```

**What the FINAL screenshot should show:**
- Map full-viewport at Fort Myers
- LayerPanel left, with the new layer entry
- LayerLegend bottom-center
- **Flood overlay correctly oriented + only over actually-flooded areas (Caloosahatchee River inundation + coastal lowlands)** — basemap visible through dry land between

### File ownership (exclusive)

- `services/workers/pyqgis/worker.py` (the 2 bug fixes)
- `services/workers/pyqgis/tests/` (additive tests for the 2 fixes)
- `web/src/App.tsx` (1 line for OQ-72 closure)
- `infra/worker.tf` (image digest line only)
- `reports/inflight/job-0074-engine-20260607/`

### FROZEN
- `services/agent/src/**` (job-0071 territory; just landed)
- `packages/contracts/**` (job-0072 territory)
- `web/src/ws.ts`, `web/src/contracts.ts` (job-0072 territory)
- `web/src/Map.tsx`, `web/src/LayerPanel.tsx`, `web/src/Chat.tsx`, `web/src/components/*`, `web/src/lib/*` (no UI changes here — just the 1-line App.tsx wiring)
- All other infra/, services/, docs/srs/, styles/, packages/
- `reports/complete/**`

### Acceptance criteria

- [ ] 2 worker.py bugs fixed (DOUBLE-MNT + EPSG:4326); existing tests still pass; ≥1 new regression test per bug
- [ ] App.tsx 1-line OQ-72 wiring closeout landed; web tests stay 63+/63+
- [ ] Worker rebuilt + digest pinned + tofu apply clean
- [ ] Live publish-raster on job-0070 COG SUCCEEDED; .qgs has 4 layers; new WMS URL captured
- [ ] Server-side WMS GetMap returns real flood-depth styled PNG at Fort Myers EPSG:3857 bbox
- [ ] **Headline re-screenshot captured** — visually shows: correct orientation + transparency-only-where-flooded + Fort Myers location
- [ ] No edits to FROZEN paths
- [ ] Single commit

### Honest disclosure note

If any part fails (e.g., EPSG:4326 fix isn't cleanly achievable via PyQGIS API), honestly disclose — surface as OQ-74-* and continue with the other parts. Production MapLibre uses EPSG:3857 so the EPSG:4326 fix is non-blocking; the screenshot just needs the orientation + transparency + location to be right.
