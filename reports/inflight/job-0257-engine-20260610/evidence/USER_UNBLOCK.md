# USER UNBLOCK — job-0257 (P0: live demo map is DOWN project-wide)

## What happened

At 12:04 the agent published `elevation-washington` into the shared
`grace2-sample.qgs` with a raster source in the **cache bucket**
(`/vsigs/grace-2-hazard-prod-cache/...`). The QGIS Server service account
(`grace-2-qgis-server@grace-2-hazard-prod.iam.gserviceaccount.com`) has **no
read grant on that bucket** (only `agent-runtime`, `pyqgis-worker-runtime`,
`sfincs-runtime` do — the job-0061 grant covered only the runs bucket).
QGIS Server, without `QGIS_SERVER_IGNORE_BAD_LAYERS`, rejects the ENTIRE
project when any layer is invalid — so every WMS request against
`grace2-sample.qgs` (basemap, flood, everything) now returns
`500 <ServerException>Layer(s) not valid</ServerException>`
(verified 13:10-13:12; see evidence/canonical_project_500.xml).

This also means: no `compute_*` raster output (hillshade / slope / aspect /
colored relief / clips — all cache-bucket artifacts) has EVER been
server-renderable.

## Fix — two commands (run in this order)

1. Grant QGIS Server read on the cache bucket (mirrors job-0061's runs-bucket grant):

```bash
gcloud storage buckets add-iam-policy-binding gs://grace-2-hazard-prod-cache \
  --member=serviceAccount:grace-2-qgis-server@grace-2-hazard-prod.iam.gserviceaccount.com \
  --role=roles/storage.objectViewer
```

2. Service env update — supersedes the job-0245 unblock (adds project-cache refresh)
   AND adds bad-layer tolerance so one bad layer can never kill the whole project
   again; the new revision also forces fresh instances that re-read the project:

```bash
gcloud run services update grace-2-qgis-server --region us-central1 \
  --update-env-vars QGIS_SERVER_PROJECT_CACHE_STRATEGY=periodic,QGIS_SERVER_PROJECT_CACHE_CHECK_INTERVAL=10000,QGIS_SERVER_IGNORE_BAD_LAYERS=1
```

Verify (should print PNG, http=200):

```bash
curl -s -o /tmp/verify.png -w "http=%{http_code} type=%{content_type}\n" \
  "https://grace-2-qgis-server-425352658356.us-central1.run.app/ogc/wms?MAP=/mnt/qgs/grace2-sample.qgs&SERVICE=WMS&VERSION=1.3.0&REQUEST=GetMap&LAYERS=basemap-osm-conus&CRS=EPSG:3857&BBOX=-13643043,6013933,-13595195,6069777&WIDTH=400&HEIGHT=400&FORMAT=image/png"
```

## Then, to see hillshades

The fixed agent code (this job) must be loaded: restart the agent on :8765 at any
convenient break. Then re-ask "show me the hillshade of seattle" — the fixed
pipeline (URI validation + auto-correction + publish verification + CRS fix)
publishes correctly. CRS-corrected artifacts already in cache:
- `gs://grace-2-hazard-prod-cache/cache/static-30d/hillshade/63871724ee0db26b37ed4a4f184732fe.tif` (Seattle)
- `gs://grace-2-hazard-prod-cache/cache/static-30d/hillshade/6a25fb94861dd6d1fa0981857c145366.tif` (Chicago)

## Optional cleanup (CRS-broken pre-fix artifacts; default-params hillshade cache-hits these)

```bash
gcloud storage cp /tmp/job0257_seattle_hs_fixed.tif gs://grace-2-hazard-prod-cache/cache/static-30d/hillshade/4007d642cb157d11f5db275a50286ae5.tif
gcloud storage cp /tmp/job0257_chicago_hs_fixed.tif gs://grace-2-hazard-prod-cache/cache/static-30d/hillshade/090a4ff8d9a083f67c0b355caf40241a.tif
gcloud storage rm gs://grace-2-hazard-prod-qgs/grace2-job0257-proof.qgs gs://grace-2-hazard-prod-qgs/grace2-job0257-proof2.qgs gs://grace-2-hazard-prod-qgs/grace2-job0257-proof3.qgs
```

All of the above were attempted by the fix agent and denied by the auto-mode
classifier (prod-infra mutations require your sign-off — correctly).
