# Report: PyQGIS worker image rebuild + live publish_layer + real flood-raster UI screenshot (OQ-67 closure)

**Job ID:** job-0069-infra-20260607
**Sprint:** sprint-10 Stage B
**Specialist:** infra
**Task:** Rebuild the PyQGIS worker Cloud Run Job image to include job-0062's `--op publish-raster` CLI + `_append_raster_layer`. Trigger live publish-raster against the job-0066 flood COG; verify the mutated `.qgs` renders through QGIS Server WMS; capture headline UI screenshot showing the real flood-depth raster on MapLibre.
**Status:** ready-for-audit

---

## Summary

All four parts executed live against the deployed substrate. Part 1 (image rebuild + targeted apply) lands cleanly: new digest `sha256:94dad2bc...` pinned in `infra/worker.tf`, Cloud Run Job now serves the rebuilt image. Part 2 (live publish-raster) succeeds — Cloud Run execution `grace-2-pyqgis-worker-7zhvn` SUCCEEDED, the canonical `gs://grace-2-hazard-prod-qgs/grace2-sample.qgs` was mutated (generation `1780726507595598` → `1780887378642723`; size 28308 → 43278 bytes; `layers_after=['basemap-osm-conus', 'flood-depth-job-0069-demo']`); WMS URL emitted. Part 3 (server-side WMS GetMap curl) returns a real 536 KB flood-depth-styled PNG when requested in the layer's native EPSG:3857 frame. Part 4 (UI headline screenshot via Playwright + dev-injection seam) captures the full pipeline working end-to-end — MapLibre fetches the real WMS tile and renders the SFINCS flood-depth raster styled by QGIS Server's `continuous_flood_depth.qml`.

The work surfaces three Open Questions in the engine domain (none block infra acceptance): OQ-69-WMS-URL-DOUBLE-MNT-PREFIX (worker `_build_wms_url` doubles `/mnt/qgs/` when QGS_URI is already a local mount path); OQ-69-COG-CRS-MISTAG (job-0066's SFINCS postprocess tagged the COG as EPSG:3857 but populated it with UTM 17N coordinates, so the flood raster renders at the COG's claimed location off N Africa rather than the actual Fort Myers location); OQ-69-WMS-LAYER-EPSG4326-EMPTY (the published WMS layer returns transparent tiles when requested in EPSG:4326 — only the native EPSG:3857 frame returns real raster). All three are downstream of `services/workers/pyqgis/worker.py` and the SFINCS postprocess — engine territory, surfaced for sprint-10's next infra-or-engine gating decision.

---

## Changes Made

- **`infra/worker.tf`** (single line — image digest pin only)
  - Updated `image = "...grace-2-pyqgis-worker@sha256:fffd7e0f..."` to `sha256:94dad2bc8964c426d962bcad05b01f917e0e05408fa87e3557e47a2844a19119`. Reason: the prior digest predates job-0062's `--op publish-raster` CLI + `_append_raster_layer` function. The rebuilt image (Cloud Build `b67ecb30-b2f7-4e7c-a17c-9a6084e7dabf`, 2m6s, 962 MB) bakes the current HEAD of `services/workers/pyqgis/` and `styles/` (incl. `continuous_flood_depth.qml`).

No new files created. Makefile already had `worker-build` and `worker-deploy` targets from job-0021. `infra/worker/cloudbuild.yaml` + `infra/worker/Dockerfile` already correct and required no changes — they reference repo paths and pick up current HEAD automatically.

---

## Part 1 — Worker image rebuild evidence

### Cloud Build

```
$ PATH=$HOME/tools/google-cloud-sdk/bin:$PATH make worker-build
Building us-central1-docker.pkg.dev/grace-2-hazard-prod/grace-2-containers/grace-2-pyqgis-worker:latest via Cloud Build (linux/amd64)...
...
ID                                    CREATE_TIME                DURATION  STATUS
b67ecb30-b2f7-4e7c-a17c-9a6084e7dabf  2026-06-08T02:48:39+00:00  2M6S      SUCCESS
```

Full log: `evidence/worker_build.log` (gcloud submit + Cloud Build tail).

### New image digest

```
sha256:94dad2bc8964c426d962bcad05b01f917e0e05408fa87e3557e47a2844a19119

Created: 2026-06-08T02:51:36 UTC
Size: 962,467,392 bytes (~918 MiB)
Tags: latest
```

(File: `evidence/worker_image_digest.txt`. Previous stale digest: `sha256:fffd7e0f...` from job-0021.)

### `infra/worker.tf` pin

Single-line edit on `image =`. No other modifications to the file.

### Targeted Tofu plan

```
$ cd infra && tofu plan -target=google_cloud_run_v2_job.pyqgis_worker

  # google_cloud_run_v2_job.pyqgis_worker will be updated in-place
  ~ resource "google_cloud_run_v2_job" "pyqgis_worker" {
        ...
      ~ template {
          ~ template {
              ~ containers {
                  ~ image = "...grace-2-pyqgis-worker@sha256:fffd7e0f..." -> "...grace-2-pyqgis-worker@sha256:94dad2bc..."
              }
          }
      }
    }

Plan: 0 to add, 1 to change, 0 to destroy.
```

Untargeted plan still surfaces the OQ-61 Cloud Run scaling drift (job-0071 scope) — `-target` flag excludes it cleanly per kickoff direction. Full log: `evidence/tofu_plan.log`.

### Targeted Tofu apply

```
$ tofu apply -target=google_cloud_run_v2_job.pyqgis_worker -auto-approve
...
Apply complete! Resources: 0 added, 1 changed, 0 destroyed.
```

Full log: `evidence/tofu_apply.log`.

### Serving-image verification

```
$ gcloud run jobs describe grace-2-pyqgis-worker --region=us-central1 --format=json | jq -r '.spec.template.spec.template.spec.containers[0].image'
us-central1-docker.pkg.dev/grace-2-hazard-prod/grace-2-containers/grace-2-pyqgis-worker@sha256:94dad2bc8964c426d962bcad05b01f917e0e05408fa87e3557e47a2844a19119
```

(File: `evidence/serving_image.txt`.) New digest is live on the Cloud Run Job.

---

## Part 2 — Live publish-raster execution evidence

Confirmed env-var names in `services/workers/pyqgis/__main__.py` before execution: `WORKER_OP`, `QGS_URI`, `RASTER_URI`, `RASTER_LAYER_ID`, `STYLE_PRESET_NAME` (not the kickoff's `OP=...`).

Used the existing job-0066 flood COG and the canonical sample `.qgs`:

```
$ gcloud run jobs execute grace-2-pyqgis-worker \
    --region=us-central1 \
    --update-env-vars=WORKER_OP=publish-raster,QGS_URI=/mnt/qgs/grace2-sample.qgs,RASTER_URI=/vsigs/grace-2-hazard-prod-runs/01KTJ3PP1JMF96WR4CCZZ4JRYS/flood_depth_peak.tif,RASTER_LAYER_ID=flood-depth-job-0069-demo,STYLE_PRESET_NAME=continuous_flood_depth \
    --wait
Creating execution...
Provisioning resources...done
Starting execution...done
Running execution...done
Done.
Execution [grace-2-pyqgis-worker-7zhvn] has successfully completed.
```

(File: `evidence/worker_execute.log`.)

### Worker stdout envelope (excerpt from Cloud Logging)

```json
{
  "qgs_uri": "/mnt/qgs/grace2-sample.qgs",
  "layers_before": ["basemap-osm-conus"],
  "layers_after": ["basemap-osm-conus", "flood-depth-job-0069-demo"],
  "notify_message_id": "19965294712983932",
  "status": "ok",
  "error": null,
  "qgs_version": "3.44.11-Solothurn",
  "ts": "2026-06-08T02:56:18.880Z",
  "wms_url": "https://grace-2-qgis-server-425352658356.us-central1.run.app/ogc/wms?MAP=/mnt/qgs/mnt/qgs/grace2-sample.qgs&LAYERS=flood-depth-job-0069-demo"
}
```

Full log: `evidence/worker_logs.txt`. Note the `/mnt/qgs/mnt/qgs/` double prefix in the emitted `wms_url` — see OQ-69-WMS-URL-DOUBLE-MNT-PREFIX below.

### `.qgs` mutation confirmed

```
$ gcloud storage objects describe gs://grace-2-hazard-prod-qgs/grace2-sample.qgs \
      --format="value(generation,etag,size,updated)"
1780887378642723        CKOO27zS9pQDEAE=        43278
```

(File: `evidence/qgs_after_mutation.txt`.)

| | Before | After |
|---|---|---|
| Generation | `1780726507595598` | `1780887378642723` |
| Size | 28308 | 43278 (+ ~15 KB raster layer XML + style ref) |

### Real WMS URL (corrected)

```
https://grace-2-qgis-server-425352658356.us-central1.run.app/ogc/wms?MAP=/mnt/qgs/grace2-sample.qgs&LAYERS=flood-depth-job-0069-demo
```

Note: this is the **corrected** URL (single-prefix `/mnt/qgs/`). The worker emitted a doubled-prefix variant — OQ surfaced below.

---

## Part 3 — Server-side WMS GetMap verification

```
$ curl -s -o evidence/server_side_wms_tile.png -w "HTTP %{http_code} content-type=%{content_type} size=%{size_download}\n" \
    "${REAL_WMS}&SERVICE=WMS&VERSION=1.3.0&REQUEST=GetMap&CRS=EPSG:3857&FORMAT=image/png&TRANSPARENT=true&BBOX=409109,2936568,425279,2952348&WIDTH=512&HEIGHT=512"
HTTP 200 content-type=image/png size=536488 bytes

$ file evidence/server_side_wms_tile.png
evidence/server_side_wms_tile.png: PNG image data, 512 x 512, 8-bit/color RGBA, non-interlaced
```

Tile dimensions: 512×512 RGBA. Size 536 KB confirms real rendered content (not transparent placeholder). Visual: Fort-Myers-shaped river and coastline with the Blues `continuous_flood_depth.qml` ramp applied 0–3.5 m — the flooded coastal areas are darker blue, high terrain shows through.

Three GetMap variants probed (full log: `evidence/wms_curl.log`):

| CRS variant | BBOX | Result |
|---|---|---|
| WMS 1.3.0 EPSG:4326 lat/lon | 26.55,-81.91,26.69,-81.75 | HTTP 200, 1117 bytes (transparent — see OQ-69-WMS-LAYER-EPSG4326-EMPTY) |
| WMS 1.1.1 EPSG:4326 lon/lat | -81.91,26.55,-81.75,26.69 | HTTP 200, 1117 bytes (same) |
| WMS 1.3.0 EPSG:3857 native | 409109,2936568,425279,2952348 | HTTP 200, **536,488 bytes — real flood-depth styled PNG** |

Headline tile saved to `evidence/server_side_wms_tile.png`.

---

## Part 4 — Real flood raster in the dev UI (headline screenshot)

Driver: `evidence/screenshot_driver.py` (standalone Playwright Python script — vite dev server on free port, Chromium headless, dev-injection seam, two screenshots captured).

The driver injects `session-state` with `loaded_layers[0]` pointing at the real WMS URL (single-prefix correction) plus `style_preset=continuous_flood_depth`, then injects two successive `map-command(zoom-to, ...)` envelopes:

1. **Fort Myers bbox** (`[-81.91, 26.55, -81.75, 26.69]`) — the geographic location the user expects the COG to cover.
2. **COG's claimed bbox** (`[3.68, 25.50, 3.82, 25.62]`) — where QGIS Server's WMS-published bbox metadata says the raster lives (off N Africa per the EPSG:3857-mistagged metadata).

### Headline screenshot — `evidence/real_flood_raster_on_map.png`

What it shows: full-viewport map, LayerPanel left ("Hurricane Ian — peak flood depth", opacity 85%, "REAL flood raster via PyQGIS worker" attribution), LayerLegend bottom-center ("Max flood depth (m)" 0–3.5 m), basemap absent (because qgis_basemap LAYERS=basemap-osm-conus is US-only and this view is over the COG's claimed Africa location), and a **visible blue striped flood-depth overlay** in the lower-center of the viewport — the Caloosahatchee River + coastal inundation pattern from the SFINCS Hurricane Ian output, styled by `continuous_flood_depth.qml`.

This is the live demonstration of the full M5-to-UI pipeline: PyQGIS worker → mutated `.qgs` → QGIS Server WMS GetMap → MapLibre raster source → tile arrives in the viewport.

### Honest-disclosure screenshot — `evidence/real_flood_raster_on_map_ft_myers.png`

Same session-state and pipeline, zoomed to Fort Myers instead. Basemap renders correctly (OSM CONUS), LayerPanel + LayerLegend wired identically, but **no flood overlay** — because the COG's metadata says it lives off N Africa, MapLibre never requests tiles in the Fort Myers Mercator range that the WMS layer can satisfy. This is the upstream OQ-69-COG-CRS-MISTAG surface — the infra rebuild + worker round-trip path is clean; the missing overlay is engine-side.

### Server-side WMS tile (Part 3) — `evidence/server_side_wms_tile.png`

512×512 direct GetMap. Shows the real Fort Myers flood map rendered by QGIS Server when requested in the layer's native CRS. Confirms the rendering pipeline is end-to-end correct; the only thing keeping it from showing up at Fort Myers in MapLibre is the CRS mistag in the COG.

### Bonus tile — `evidence/wms_tile_at_native_extent.png`

256×256 MapLibre-style WMS tile request at the COG's native bbox. 147 KB. Real flood-depth styled tile showing river + basemap underneath. Proves QGIS Server publishes the COG's basemap-overlaid composition correctly; the MapLibre dev-injection screenshot is fetching from this same code path at the same coordinates.

### Narration — what changed vs job-0068's substitute-URL screenshot

- **Job-0068's `layer_arrival.png`**: LayerPanel + LayerLegend wired with a placeholder layer pointing at the `basemap-osm-conus` WMS (the M3 basemap). Visually: just the basemap, with the UI shell asserting layers are wired correctly.
- **Job-0069's `real_flood_raster_on_map.png`**: same UI shell, same LayerPanel + LayerLegend, but the layer's `uri` is now the REAL WMS URL for the rebuilt-image-mutated `.qgs`, returning the actual SFINCS Hurricane Ian flood-depth raster styled by `continuous_flood_depth.qml`. The viewport shows the real flood pattern.

Acceptance per kickoff: "**a distinct blue flood-depth overlay** rendered server-side by QGIS Server using the `continuous_flood_depth.qml` ramp — visually distinct from the basemap underneath." Met — qualified by the COG CRS mistag forcing the view to the COG's claimed (vs intended) geographic location.

---

## Decisions Made

- **Correct WMS URL by hand to single-prefix `/mnt/qgs/`** rather than wait for the worker fix. The worker emits `MAP=/mnt/qgs/mnt/qgs/grace2-sample.qgs` because `_build_wms_url(read_path.lstrip("/"), ...)` in the local-mode branch passes `mnt/qgs/grace2-sample.qgs` to `_build_wms_url` which then prepends `/mnt/qgs/`. The corrected single-prefix path works. Surfaced as OQ-69-WMS-URL-DOUBLE-MNT-PREFIX. Choice: continue with the corrected URL for Parts 3+4 so the live pipeline demonstration is unblocked.
- **Demonstrate the pipeline at the COG's claimed coordinates** rather than at Fort Myers. The job-0066 SFINCS postprocess wrote UTM 17N coordinates into the COG with EPSG:3857 metadata, so the WMS layer publishes its bbox metadata as "off N Africa". Choice: capture both views (Fort Myers honest-view + COG-claimed bbox real-overlay-view) so the headline shows the pipeline working AND the honest record shows the upstream OQ. Alternative considered: regenerate the COG with correct metadata — out of scope (`services/workers/sfincs/` is engine-owned and FROZEN for this job).
- **No new Makefile target.** The existing `worker-build` target already exists from job-0021. No edit to Makefile.
- **No new cloudbuild.yaml.** `infra/worker/cloudbuild.yaml` exists and works. No edit.

---

## Invariants Touched

- **Rendering through QGIS Server (Invariant 4):** preserves — the rebuilt worker still only mutates the canonical `.qgs`; rendering still happens only through QGIS Server WMS. The end-to-end pipeline working live is the strongest possible confirmation of this invariant.
- **Tier separation (Invariant 5):** preserves — the web client only knows the WMS URL; never touches GCS. The Map.tsx `addSource/addLayer` path goes through QGIS Server.
- **Metadata-payload pattern (Invariant 6):** preserves — buckets are not enumerated; the WMS URL came directly from the worker round-trip envelope.
- **Cancellation is first-class (Invariant 8):** not touched (no run-cancel surface in this job).
- **Confirmation before consequence (Invariant 9):** not touched (no cost-rendering surfaces added).

---

## Open Questions

- **OQ-69-WMS-URL-DOUBLE-MNT-PREFIX** (engine — `services/workers/pyqgis/worker.py`): in `publish_raster_round_trip`'s local-mode branch (lines 825–829), the worker computes `wms_url = _build_wms_url(read_path.lstrip("/"), layer_id)` where `read_path = "/mnt/qgs/grace2-sample.qgs"`. The `read_path.lstrip("/")` strips only the leading slash → `mnt/qgs/grace2-sample.qgs`, then `_build_wms_url` (line 395) prepends `/mnt/qgs/` → `MAP=/mnt/qgs/mnt/qgs/grace2-sample.qgs`. This is invalid for QGIS Server. Suggested fix: detect when `read_path` already starts with `/mnt/qgs/` and use just the basename, OR have the local-mode branch reconstruct the gs-style key via a different path. TENTATIVE priority: medium (the WMS URL is the bridge between worker and downstream consumers; agent/web specialists will hit this when wiring the `publish_layer` tool's actual output). Routing: engine.

- **OQ-69-COG-CRS-MISTAG** (engine — `services/workers/sfincs/postprocess_flood.py` or HydroMT chain): the flood COG at `gs://grace-2-hazard-prod-runs/01KTJ3PP1JMF96WR4CCZZ4JRYS/flood_depth_peak.tif` carries `EPSG:3857` (Web Mercator) in its GeoTIFF metadata, but the coordinate values (409109, 2936568, 425279, 2952348) are actually UTM 17N (which would place the layer at Fort Myers in EPSG:32617). EPSG:3857 with those values places it off the coast of N Africa. Consequence: MapLibre at Fort Myers cannot fetch tiles that QGIS Server will satisfy from this layer, because the publish bbox is wrong. Suggested fix: regenerate the COG with `gdalwarp -t_srs EPSG:3857 -overwrite ...` (true Web Mercator reprojection) OR `gdal_edit.py -a_srs EPSG:32617` (fix the metadata tag to UTM 17N). TENTATIVE priority: HIGH (blocks the headline Fort Myers screenshot the user has been waiting for). Routing: engine (job-0066 specialist or follow-up).

- **OQ-69-WMS-LAYER-EPSG4326-EMPTY** (engine — `_append_raster_layer`'s `crs` parameter): the published WMS layer returns 1117-byte transparent PNGs when requested in `CRS=EPSG:4326` (or 1.1.1 `SRS=EPSG:4326`), even with valid bbox values inside the layer's geographic extent. Only the layer's native `EPSG:3857` (as tagged) returns real raster. Likely cause: `_append_raster_layer` adds the layer with `crs=EPSG:3857` and QGIS Server doesn't re-project on the fly without `WMSCrsList` or similar in the project. Suggested fix: bake EPSG:4326 (and EPSG:3857, etc.) into the WMS-server-side CRS list in `_append_raster_layer`. TENTATIVE priority: medium (workaround is to use the native CRS in client requests; MapLibre's `{bbox-epsg-3857}` substitution already does that). Routing: engine.

- **Untargeted `tofu plan` drift** (carry-forward OQ-61, not this job's scope): the project-wide `tofu plan` still surfaces Cloud Run scaling drift on multiple services. The `-target` flag isolates this job cleanly. No new action.

---

## Dependencies and Impacts

- Depends on: job-0062 (engine — publish_layer + `--op publish-raster` CLI), job-0067 (infra — pyqgis-worker SA read on runs bucket, surfaced OQ-67-WORKER-IMAGE-REBUILD), job-0066 (engine — provided the live SFINCS Hurricane Ian flood COG), job-0024/0029 (infra — rebuild rhythm precedent), job-0068 (web — Map.tsx subscribes session-state with addSource/addLayer).
- Affects:
  - Engine specialist: 3 OQs above (worker WMS URL bug + COG CRS mistag + WMS EPSG:4326 publishing). The CRS mistag is the highest-priority gate to the Fort Myers headline screenshot.
  - Agent specialist: when `publish_layer` is called live and returns the wire envelope's `wms_url` for `LayerURI.uri` substitution, the doubled `/mnt/qgs/` prefix will land in session-state. Once OQ-69-WMS-URL-DOUBLE-MNT-PREFIX is fixed in worker, agent wiring will be correct without change.
  - Web specialist: no action; Map.tsx wiring already works as Part 4 demonstrates.
  - Schema specialist: none for this job.
  - Testing specialist: the existing `tests/m6/playwright/test_sprint09_acceptance.py` Test 3 uses the substitute basemap URL; once OQ-69-COG-CRS-MISTAG is fixed in engine, the fixture can be updated to use the real flood WMS URL, and the test can include a "non-trivial blue pixel count" assertion against the real overlay.

---

## Verification

- **Tests run:**
  - `make worker-build` — SUCCESS (2m6s, new digest `sha256:94dad2bc...`)
  - `tofu plan -target=google_cloud_run_v2_job.pyqgis_worker` — clean (0/1/0)
  - `tofu apply -target=google_cloud_run_v2_job.pyqgis_worker -auto-approve` — clean (0/1/0)
  - `gcloud run jobs describe` — confirms new digest serving
  - `gcloud run jobs execute grace-2-pyqgis-worker --wait` — execution `grace-2-pyqgis-worker-7zhvn` SUCCEEDED
  - `gcloud storage objects describe gs://...grace2-sample.qgs` — generation updated (1780726507595598 → 1780887378642723), size grew 28308 → 43278
  - `curl ${REAL_WMS}&...&CRS=EPSG:3857&BBOX=...` — HTTP 200, 536 KB PNG (real flood raster)
  - `evidence/screenshot_driver.py` — Vite dev server + Playwright Chromium + dev-injection seam → both screenshots captured

- **Live E2E evidence (canonical paths):**
  - `evidence/worker_build.log` — Cloud Build success transcript
  - `evidence/worker_image_digest.txt` — new digest
  - `evidence/tofu_plan.log` + `evidence/tofu_apply.log` — IaC transcripts
  - `evidence/serving_image.txt` — gcloud-describe confirmation
  - `evidence/worker_execute.log` — `gcloud run jobs execute --wait` transcript
  - `evidence/worker_logs.txt` — Cloud Logging excerpt with worker stdout envelope
  - `evidence/qgs_after_mutation.txt` — new generation/etag/size
  - `evidence/wms_curl.log` — three GetMap probes
  - `evidence/server_side_wms_tile.png` — 512×512 real flood-depth styled PNG (Part 3 headline)
  - `evidence/wms_tile_at_native_extent.png` — 256×256 MapLibre-style tile with basemap underneath
  - `evidence/real_flood_raster_on_map.png` — **Part 4 headline screenshot** (full UI with real flood overlay at COG's claimed coords)
  - `evidence/real_flood_raster_on_map_ft_myers.png` — Fort Myers honest-view (basemap only; flood missing due to COG CRS mistag)
  - `evidence/screenshot_driver.py` — Playwright driver script

- **Results: qualified pass.**
  - **Part 1 (image rebuild + tofu apply): pass.** New digest live on Cloud Run Job, tofu plan/apply clean, no drift introduced.
  - **Part 2 (live publish-raster): pass.** Cloud Run execution SUCCEEDED, `.qgs` mutated, raster layer registered.
  - **Part 3 (server-side WMS): pass.** Real 536 KB flood-depth styled PNG returned when requested in the layer's native CRS.
  - **Part 4 (headline UI screenshot): qualified pass.** Real flood overlay renders in MapLibre via the QGIS Server WMS pipeline (visible in `real_flood_raster_on_map.png`). The overlay shows at the COG's claimed-but-mistagged Web Mercator location, not at Fort Myers — qualified by upstream OQ-69-COG-CRS-MISTAG. Headline file path is `evidence/real_flood_raster_on_map.png` per kickoff; the Fort-Myers-honest-view companion is `evidence/real_flood_raster_on_map_ft_myers.png`.

- **No edits to FROZEN paths confirmed.** Only modified `infra/worker.tf` (single image digest line) + created files under `reports/inflight/job-0069-infra-20260607/`. `services/workers/pyqgis/{worker.py,__main__.py,types.py}` untouched. `services/workers/pyqgis/Dockerfile` untouched. All other infra/, services/, web/, packages/, docs/, reports/complete/ untouched.
