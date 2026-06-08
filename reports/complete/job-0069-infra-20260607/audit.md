# Audit: PyQGIS worker image rebuild + live publish_layer + real flood-raster UI screenshot (OQ-67 closure)

**Job ID:** job-0069-infra-20260607, **Sprint:** sprint-10 Stage B (gated on job-0068 — now approved), **Auditor:** Development Orchestrator, **Status:** assigned

## Task Assignment

**Specialist:** infra (with end-to-end live verification)

**Prerequisites (ALL APPROVED):**
- **job-0062 (commit f202a31):** PyQGIS worker raster path (`_append_raster_layer` + `--op publish-raster` CLI discriminator). Code is in the repo; not yet baked into the deployed image.
- **job-0067 (commit 726f79d):** pyqgis-worker SA reads runs bucket — IAM grant is live; uncovered OQ-67-WORKER-IMAGE-REBUILD (deployed image sha256:fffd7e0f from job-0021 predates job-0062's CLI).
- **job-0068 (commit e5398b8):** UI correction landed; Map.tsx now subscribes to session-state + addSource/addLayer per loaded_layers; dev-injection seam verified working with substitute WMS URL.
- jobs 0024, 0029 (QGIS Server rebuild precedents): mirror the same Cloud Build → digest pin → targeted tofu apply workflow.

**SRS references** (narrow file loading only):
- `docs/srs/03-functional-requirements.md` FR-QS-6 (PyQGIS worker round-trip) + FR-MP-3 (`.qgs` GCS is source of truth)
- DO NOT load `docs/SRS_v0.3.md` monolith.

**Required reads:**
- `reports/complete/job-0067-infra-20260607/report.md` — surfaced OQ-67; documents the worker smoke argparse exit 2 evidence
- `reports/complete/job-0062-engine-20260607/report.md` — the worker CLI + raster path that needs to ship
- `infra/worker.tf` lines 1–80 — current Cloud Run Job config + image pin
- `services/workers/pyqgis/Dockerfile` (or `infra/worker/Dockerfile` / similar — locate via `find services/workers/pyqgis -name Dockerfile`)
- The Makefile target for worker-build (`grep -rn "worker-build" Makefile`)
- `infra/qgis-server.tf` lines 78–100 + jobs 0024/0029 reports for the exact rebuild → pin → apply rhythm

### Why this job exists

Sprint-9's `publish_layer` atomic tool is mechanically complete in the repo but cannot run live: the deployed PyQGIS worker container image was last built before job-0062 added the `--op publish-raster` CLI flag + `_append_raster_layer` function. job-0067's live smoke exited 2 on argparse because the deployed image doesn't recognize the new args.

Until this rebuilds: the user sees the basemap URL substituted in `layer_arrival.png` (per job-0068's screenshot) rather than a distinct flood raster. The whole M5-to-UI demo loop is one image build away from working end-to-end. **This is the unlock for the real headline screenshot.**

### Scope

#### Part 1 — Worker image rebuild (the mechanical infra piece)

Mirror jobs 0024 / 0029 exactly:

1. **Cloud Build the worker image.** Find and run the Makefile target (`grep -n worker-build Makefile`). If the target is `make worker-build`, run it from the repo root. It submits a Cloud Build that pushes the image to Artifact Registry and prints the new digest on the last line of stdout.
   - If the Makefile target doesn't exist, document it — there should be a `services/workers/pyqgis/cloudbuild.yaml` or similar. If absent, you may need to author one mirroring `services/workers/sfincs/` or `infra/qgis-server/cloudbuild.yaml`. **Constraint:** keep the build minimal — same FROM base, same PIP install of repo Python, no new system deps.

2. **Capture the new image digest.** Save full digest to `evidence/worker_image_digest.txt`.

3. **Update the `image =` pin in `infra/worker.tf`.** Mirror the QGIS Server pattern (digest-pinned, not `:latest` tag).

4. **Targeted Tofu apply:**
   ```
   cd infra
   $HOME/tools/opentofu/tofu plan -target=google_cloud_run_v2_job.pyqgis_worker
   $HOME/tools/opentofu/tofu apply -target=google_cloud_run_v2_job.pyqgis_worker -auto-approve
   ```
   The Cloud Run scaling drift from OQ-61 will still surface in the untargeted plan — that's job-0071's scope, not this one; the `-target` flag excludes it. Document it as noise.

5. **Verify the new image is serving.** `gcloud run jobs describe grace-2-pyqgis-worker --region=us-central1 --format='value(template.containers.image)'` should show your new digest.

#### Part 2 — Live publish_layer round-trip (the live verification)

Trigger the worker against a real existing COG (e.g., the flood-depth COG from job-0066's run at `gs://grace-2-hazard-prod-runs/01KTJ3PP1JMF96WR4CCZZ4JRYS/flood_depth_peak.tif`):

```
PATH=$HOME/tools/google-cloud-sdk/bin:$PATH gcloud run jobs execute grace-2-pyqgis-worker \
    --region=us-central1 \
    --update-env-vars=OP=publish-raster,QGS_URI=gs://grace-2-hazard-prod-qgs/grace2-sample.qgs,RASTER_URI=/vsigs/grace-2-hazard-prod-runs/01KTJ3PP1JMF96WR4CCZZ4JRYS/flood_depth_peak.tif,RASTER_LAYER_ID=flood-depth-job-0069-demo,STYLE_PRESET_NAME=continuous_flood_depth \
    --wait
```

(Check `services/workers/pyqgis/__main__.py` for the actual env var names — the kickoff above assumes the names from the job-0062 report; verify and adjust if they differ.)

Expected outcome:
- Cloud Run Job execution status = SUCCEEDED
- Worker mutates `gs://grace-2-hazard-prod-qgs/grace2-sample.qgs` to add the new raster layer with the `continuous_flood_depth.qml` style
- Pub/Sub completion envelope includes the resulting WMS URL: `https://grace-2-qgis-server-.../ogc/wms?MAP=/mnt/qgs/grace2-sample.qgs&LAYERS=flood-depth-job-0069-demo`

Capture: execution log (stderr/stdout), the resulting WMS URL, the gcs object stat of the mutated `.qgs` (showing new generation/etag).

#### Part 3 — Real raster WMS verification (server-side)

Hit the WMS GetMap directly via curl to confirm QGIS Server serves the flood layer with style applied:

```
curl -o /tmp/flood_layer_test_tile.png \
    "https://grace-2-qgis-server-425352658356.us-central1.run.app/ogc/wms?MAP=/mnt/qgs/grace2-sample.qgs&SERVICE=WMS&VERSION=1.3.0&REQUEST=GetMap&LAYERS=flood-depth-job-0069-demo&BBOX=-81.91,26.55,-81.75,26.69&CRS=EPSG:4326&WIDTH=512&HEIGHT=512&FORMAT=image/png&TRANSPARENT=true"
file /tmp/flood_layer_test_tile.png
identify /tmp/flood_layer_test_tile.png  # if ImageMagick available
```

Expected: 512×512 PNG with transparent background + blue gradient where flooded. Save to `evidence/server_side_wms_tile.png`.

#### Part 4 — Real flood raster appears in the web UI (the headline screenshot)

This is the deliverable the user is waiting for. Use the existing dev-injection seam (per job-0066/0068 pattern):

1. Start the dev server: `cd web && npm run dev` (or `npx vite`)
2. Use Playwright (existing tooling per job-0027/0066) to:
   - Load the app
   - Inject session-state with `loaded_layers: [{layer_id: "flood-depth-job-0069-demo", uri: "<the real WMS URL from Part 2>", style_preset: "continuous_flood_depth", visible: true, role: "primary", bbox: [-81.91, 26.55, -81.75, 26.69]}]`
   - Inject map-command(zoom-to, {bbox: [-81.91, 26.55, -81.75, 26.69]})
   - Wait for the map to render the new raster tile (give it 3–5 seconds)
3. Screenshot the result. Save to `evidence/real_flood_raster_on_map.png` — **THIS IS THE HEADLINE SCREENSHOT**.

What the screenshot should show: full-viewport map at Fort Myers, LayerPanel left ("flood-depth-job-0069-demo"), LayerLegend bottom-center ("Max flood depth (m)" 0–3.5 m), **and a distinct blue flood-depth overlay** rendered server-side by QGIS Server using the `continuous_flood_depth.qml` ramp — visually distinct from the basemap underneath.

If the raster doesn't appear in the screenshot, debug by:
- Checking browser console for MapLibre errors
- Hitting the WMS URL by hand from curl (Part 3 should have validated this)
- Inspecting the MapLibre layer order via `map.getStyle().layers`

### File ownership (exclusive)
- `infra/worker.tf` — only the image digest line
- `services/workers/pyqgis/cloudbuild.yaml` (NEW only if it doesn't exist; otherwise frozen)
- `Makefile` — only if you need to add a `worker-build` target (do NOT touch other targets)
- `reports/inflight/job-0069-infra-20260607/`

### FROZEN
- `services/workers/pyqgis/worker.py`, `__main__.py`, `types.py` — job-0062 substrate; don't modify (you're rebuilding what's there, not changing it)
- `services/workers/pyqgis/Dockerfile` (if it exists and references only repo code; check before touching — if it needs no edits to bake new code, don't touch it)
- All other infra/, services/, web/, packages/, docs/, reports/complete/

### Acceptance criteria

- [ ] **Worker image rebuilt** — Cloud Build succeeded; new digest captured
- [ ] **`infra/worker.tf` pinned to new digest**; targeted tofu plan shows 1-change-0-add-0-destroy on the job; apply clean
- [ ] **Live publish-raster execution** — Cloud Run Job SUCCEEDED; .qgs mutated; WMS URL captured
- [ ] **Server-side WMS GetMap** returns a real flood-depth styled PNG (not an XML error envelope)
- [ ] **Real flood raster appears in the dev UI screenshot** — distinct blue overlay over Fort Myers, visually different from the basemap
- [ ] **No edits to FROZEN paths**
- [ ] **Single commit**

### Honest outcome disclosure

If Part 2 (live publish-raster) fails for a NEW class of reason (e.g., QGIS Python plugin missing, GDAL config, .qgs schema rejection), don't push through — document the new failure class as a fresh OQ-69-* and route accordingly. The infra rebuild itself is the cleanest win; the live publish + screenshot are stretch goals that depend on the worker code being correct (job-0062 verified the code paths in unit tests but not against the real .qgs).
