# Report: worker bug fixes + rebuild + App.tsx wiring + Fort Myers re-screenshot (Stage 2)

**Job ID:** job-0074-engine-20260607
**Sprint:** sprint-10 Stage 2 (live verification gate)
**Specialist:** engine (cross-specialty: infra worker.tf + 1 line in web/App.tsx)
**Task:** 5-part job: (1) worker.py bug fixes (DOUBLE-MNT-PREFIX + EPSG:4326 WMSCrsList), (2) App.tsx 1-line OQ-72 wiring, (3) worker rebuild + tofu apply, (4) re-publish-raster on job-0070 COG, (5) live verification + headline screenshot.
**Status:** ready-for-audit

---

## Summary

All 5 parts executed live against the deployed substrate. Part 1 (two worker.py bug fixes + 3 new unit tests) lands cleanly: DOUBLE-MNT-PREFIX fixed (confirmed by live worker log showing single-prefix URL natively), WMSCrsList fix implemented and confirmed by live log showing EPSG:4326/3857/32617 written to project, EPSG:4326 GetMap now returns 552 KB real tiles (vs 1117-byte transparent placeholder). Part 2 (App.tsx OQ-72-APP-MAPCMD-WIRING closeout) adds 1 line; 63/63 web tests pass. Part 3 (rebuild + tofu apply): new digest sha256:56df8f4c... pinned, Cloud Run Job serving new image. Part 4 (publish-raster): execution grace-2-pyqgis-worker-llphx SUCCEEDED, .qgs now has 4 layers, single-prefix WMS URL emitted natively by the fixed worker. Part 5 (screenshot): server-side WMS GetMap returns 333 KB real flood-depth styled PNG at Fort Myers EPSG:3857; headline screenshot evidence/headline_fort_myers_FINAL.png captured (1440x900, 1.2 MB).

OQ-69-WMS-URL-DOUBLE-MNT-PREFIX: CLOSED. OQ-72-APP-MAPCMD-WIRING: CLOSED. OQ-69-WMS-LAYER-EPSG4326-EMPTY: CLOSED (WMSCrsList written; EPSG:4326 now returns real tiles).

---

## Changes Made

- **services/workers/pyqgis/worker.py**
  - DOUBLE-MNT-PREFIX fix: changed `_build_wms_url(read_path.lstrip("/"), layer_id)` to `_build_wms_url(Path(read_path).name, layer_id)`. Path.name gives the basename "grace2-sample.qgs" so _build_wms_url produces MAP=/mnt/qgs/grace2-sample.qgs (not the doubled MAP=/mnt/qgs/mnt/qgs/...).
  - WMSCrsList fix: added `project.writeEntry("WMSCrsList", "/", ["EPSG:4326", "EPSG:3857", "EPSG:32617"])` after `project.addMapLayer(layer)` in `_append_raster_layer`, wrapped in a non-fatal try/except.

- **services/workers/pyqgis/tests/test_worker_raster.py**
  - Test 8 (test_publish_raster_local_mode_no_double_mnt_prefix): regression guard for DOUBLE-MNT fix.
  - Test 9 (test_publish_raster_local_mode_non_mnt_path_uses_basename): non-/mnt/qgs/ local dev path produces basename MAP param.
  - Test 10 (test_append_raster_layer_writes_wms_crs_list): asserts writeEntry called with WMSCrsList including EPSG:4326.

- **web/src/App.tsx**
  - Added `onMapCommand: (p) => bus.pushMapCommand(p),` to the GraceWs constructor. Closes OQ-72-APP-MAPCMD-WIRING.

- **infra/worker.tf**
  - Updated image pin from sha256:94dad2bc... (job-0069) to sha256:56df8f4c719c66432e207ad696efea289674a24b3704db907d927f96775cc4b4.

- **reports/inflight/job-0074-engine-20260607/evidence/** — all evidence artifacts.

---

## Part 1 — Worker Bug Fix Evidence

### DOUBLE-MNT-PREFIX fix

Before: `_build_wms_url(read_path.lstrip("/"), layer_id)` → MAP=/mnt/qgs/mnt/qgs/grace2-sample.qgs
After: `_build_wms_url(Path(read_path).name, layer_id)` → MAP=/mnt/qgs/grace2-sample.qgs

Live confirmation (Part 4 worker log):
```
wms_url=https://grace-2-qgis-server-425352658356.us-central1.run.app/ogc/wms?MAP=/mnt/qgs/grace2-sample.qgs&LAYERS=flood-depth-job-0074-demo
```
Single prefix confirmed, no hand-correction needed.

### WMSCrsList fix

```python
project.writeEntry("WMSCrsList", "/", ["EPSG:4326", "EPSG:3857", "EPSG:32617"])
```

Live confirmation (Part 4 worker log):
```
_append_raster_layer: wrote WMSCrsList [EPSG:4326, EPSG:3857, EPSG:32617] to project for layer 'flood-depth-job-0074-demo'
```

EPSG:4326 GetMap (Part 5): HTTP 200, 552005 bytes (was 1117 bytes transparent before fix).

### Test counts

```
.venv-agent/bin/python -m pytest services/workers/pyqgis/tests/test_worker_raster.py -v
13 passed in 0.07s  (baseline 10; +3 new tests)
```

---

## Part 2 — App.tsx wiring

Added 1 line between `onSessionState` and `onError`:
```typescript
onMapCommand: (p) => bus.pushMapCommand(p),
```

Web tests: 63/63 passed.

Note: `npx tsc --noEmit` shows 3 pre-existing errors in ws.test.tsx (from job-0072, in a FROZEN file). Confirmed these exist before and after my change by git stash test.

---

## Part 3 — Worker Rebuild

Cloud Build: caa0a59f, 1m59s, SUCCESS.
New digest: sha256:56df8f4c719c66432e207ad696efea289674a24b3704db907d927f96775cc4b4
Tofu plan: 0 to add, 1 to change, 0 to destroy.
Tofu apply: 0 added, 1 changed, 0 destroyed.
Serving image verified via gcloud run jobs describe.

---

## Part 4 — Re-publish-raster

Command (using WORKER_OP, not OP — see OQ below):
```
gcloud run jobs execute grace-2-pyqgis-worker \
    --update-env-vars=WORKER_OP=publish-raster,QGS_URI=/mnt/qgs/grace2-sample.qgs,...
    --wait
Execution [grace-2-pyqgis-worker-llphx] has successfully completed.
```

Worker envelope:
- layers_before: [basemap-osm-conus, flood-depth-job-0069-demo, flood-depth-job-0070-demo]
- layers_after: [basemap-osm-conus, flood-depth-job-0069-demo, flood-depth-job-0070-demo, flood-depth-job-0074-demo]
- status: ok
- wms_url: https://grace-2-qgis-server-425352658356.us-central1.run.app/ogc/wms?MAP=/mnt/qgs/grace2-sample.qgs&LAYERS=flood-depth-job-0074-demo

New WMS URL: https://grace-2-qgis-server-425352658356.us-central1.run.app/ogc/wms?MAP=/mnt/qgs/grace2-sample.qgs&LAYERS=flood-depth-job-0074-demo

---

## Part 5 — Verification + Headline Screenshot

### Server-side WMS GetMap

```
HTTP 200 content-type=image/png size=333048 bytes
PNG image data, 512x512, 8-bit/color RGBA, non-interlaced
```

File: evidence/server_side_fort_myers_FINAL.png (333 KB, real flood-depth styled content).

EPSG:4326 also confirmed working: HTTP 200, 552 KB.

### Headline screenshot

evidence/headline_fort_myers_FINAL.png (1440x900, 1.2 MB).

DOM state:
- layer_panel_rows: "Hurricane Ian - peak flood depth, opacity 90%, GRACE-2 job-0074"
- legend_present: true
- legend_text: "Max flood depth (m) 0 m 3.5 m"

### Visual narration vs job-0070

job-0070 headline: Fort Myers basemap, LayerPanel + LayerLegend wired with flood-depth-job-0070-demo at 85% opacity. Worker had emitted a double-prefix WMS URL that required hand-correction before injection into the dev seam. Production App.tsx map-command routing was incomplete (OQ-72 open).

job-0074 FINAL headline: Same visual composition (Fort Myers OSM CONUS basemap, LayerPanel left, LayerLegend bottom-center). Key structural differences:
1. WMS URL from worker envelope requires no hand-correction — DOUBLE-MNT-PREFIX eliminated end-to-end.
2. App.tsx onMapCommand routing is now production-complete — the zoom-to Fort Myers traversed the full code path (ws.ts dispatch → bus.pushMapCommand → MapView subscribeMapCommand → flyTo), not just the dev-injection shortcut.
3. EPSG:4326 GetMap now returns real tiles (552 KB) — WMSCrsList written at publish time.

The flood overlay pixel distribution in the headless screenshot is within 3 pixels of job-0070's accepted screenshot (map area analyzed: 18.67% light blue, 0.33% dark blue in both) — same COG, same QGIS Server endpoint, same geographic location. The server-side curl (333 KB RGBA PNG) is the authoritative confirmation that the raster content is correct: flood-depth styled inundation over the Caloosahatchee River and coastal lowlands, QGIS Server rendering it correctly with the continuous_flood_depth.qml ramp.

---

## Decisions Made

- **Path(read_path).name for basename**: simpler and correct for both /mnt/qgs/ and /tmp/ paths. No edge cases.
- **Non-fatal writeEntry wrapper**: MapLibre uses EPSG:3857; WMSCrsList is a nice-to-have. If the API ever changes, the round-trip should continue.
- **WORKER_OP not OP**: kickoff shows OP= but __main__.py reads WORKER_OP. Code is authoritative; kickoff has a documentation error.

---

## Invariants Touched

- Rendering through QGIS Server (Invariant 4): preserves.
- Tier separation (Invariant 5): preserves.
- Determinism boundary (Invariant 1): preserves.
- Metadata-payload pattern (Invariant 6): preserves.

---

## Open Questions

- **OQ-74-KICKOFF-WORKER-OP-MISMATCH (new, low):** kickoff shows OP=publish-raster but __main__.py reads WORKER_OP. Documentation error in kickoff; code is correct. No action needed.
- **OQ-74-TSC-WS-TEST-ERRORS (new, low):** ws.test.tsx has 3 pre-existing tsc errors from job-0072 in a FROZEN file. Tests run fine. Routing: schema or web specialist.
- OQ-69-WMS-URL-DOUBLE-MNT-PREFIX: CLOSED.
- OQ-69-WMS-LAYER-EPSG4326-EMPTY: CLOSED.
- OQ-72-APP-MAPCMD-WIRING: CLOSED.
- OQ-70-AUTO-PUBLISH-DISPATCH: carry-forward (auto-publish JobsClient kwarg issue; manual gcloud path works).

---

## Dependencies and Impacts

- Depends on: job-0069, job-0070, job-0071, job-0072, job-0073.
- Affects: Testing specialist (acceptance tests can now use EPSG:4326 + EPSG:3857 assertions against job-0074-demo WMS URL).

---

## Verification

- Worker unit tests: 13/13 passed
- Web tests: 63/63 passed
- Cloud Build: SUCCESS (caa0a59f, 1m59s)
- Tofu plan: 0 to add, 1 to change, 0 to destroy
- Tofu apply: 0 added, 1 changed, 0 destroyed
- gcloud describe: sha256:56df8f4c... confirmed serving
- gcloud execute: grace-2-pyqgis-worker-llphx SUCCEEDED, status=ok, layers_after=4
- curl EPSG:3857: HTTP 200, 333 KB PNG
- curl EPSG:4326: HTTP 200, 552 KB PNG (fix confirmed)
- Playwright screenshot: 1440x900, LayerPanel + LayerLegend confirmed
- No edits to FROZEN paths confirmed

Results: PASS.
