# Report: QGIS Server `/vsigs/` access fix (Cloud Run GCS volume mount at /mnt/qgs) + QML preset bake

**Job ID:** job-0024-infra-20260605
**Sprint:** sprint-04
**Specialist:** infra
**Task:** Diagnose and fix QGIS Server's inability to open `.qgs` files referenced via `/vsigs/`. Per OQ-19A, try path (c) GDAL VSI env vars first; fall back to (b) gcsfuse / Cloud Run volume mount, then (a) fetch-to-tmp. Bundle OQ-19C QML preset bake into the same image cycle. Update `infra/qgis-server.tf` and `infra/qgis-server/Dockerfile`; produce live `<WMS_Capabilities>` + PNG transcripts; achieve `tofu plan: No changes`.
**Status:** ready-for-audit

## Summary

Path (c) was tried first as the 5-min diagnostic — env vars `CPL_MACHINE_IS_GCE=YES`, `CPL_GS_USE_INSTANCE_PROFILE=YES`, `GDAL_HTTP_USERAGENT=…` were added to the Cloud Run service, the new revision (00002-7gg) deployed, and the live curl still returned `<ServerException>Project file error</ServerException>` with the server log line `CRITICAL Server[18]: Error when loading project file '/vsigs/...': Unable to open /vsigs/…`. Root cause: QGIS Server loads the `.qgs` file via Qt's `QFile` (not GDAL VSI), so GDAL VSI env vars don't help the project-file load — they only help internal layer references inside an already-loaded project. Pivoted to path (b) via Cloud Run gen2's native GCS volume mount (cleaner than installing gcsfuse in the image — no PID-1 wrapper, no key file, no startup-probe gymnastics): added `volumes { gcs { … read_only=true } }` + `volume_mounts { mount_path = "/mnt/qgs" }` to `infra/qgis-server.tf`. The mount uses the existing `grace-2-qgis-server` runtime SA's bucket-scoped `roles/storage.objectViewer`. The WMS canonical URL becomes `MAP=/mnt/qgs/<file>.qgs` — per "No legacy support pre-MVP" there is NO support for both `/vsigs/` and `/mnt/qgs/` for `.qgs`; `/mnt/qgs/<file>.qgs` is THE contract going forward (the GDAL env vars from path (c) are kept in place because they DO still benefit COG/FlatGeobuf layer data referenced inside the project, but the project file itself comes from the FUSE mount). The same image-rebuild cycle landed OQ-19C: `Dockerfile` now `COPY styles/ /etc/qgis/styles/` (canonical QGIS preset path) so the engine-authored `basemap.qml` (from job-0019) is baked at `/etc/qgis/styles/basemap.qml`. New image built via Cloud Build, digest `sha256:a703476049…`, deployed as revision 00004-m82. Post-fix verification passes live: GetCapabilities returns valid `<WMS_Capabilities>` XML listing `<Name>basemap-osm-conus</Name>`; GetMap returns `PNG image data, 800 x 400, 8-bit/color RGBA, non-interlaced` at 332 KB.

## Changes Made

- **`infra/qgis-server.tf`** — three additive blocks + one digest bump + one env-value retarget:
  - Added three env vars to the Cloud Run service container (path (c) — kept even after path (b) supersedes, because they still help GDAL `/vsigs/` reads for COG/FlatGeobuf layer data referenced INSIDE projects):
    - `CPL_MACHINE_IS_GCE = "YES"`
    - `CPL_GS_USE_INSTANCE_PROFILE = "YES"`
    - `GDAL_HTTP_USERAGENT = "grace-2-qgis-server/0.1"`
  - Added `volume_mounts { name = "qgs-bucket"; mount_path = "/mnt/qgs" }` to the container (path (b)).
  - Added `volumes { name = "qgs-bucket"; gcs { bucket = google_storage_bucket.qgs.name; read_only = true } }` to the template (path (b)). `read_only=true` because QGIS Server renders only; the PyQGIS worker (job-0020/0021) handles writes via its own container with `roles/storage.objectAdmin`.
  - Retargeted `GRACE2_STYLES_DIR` from `/opt/grace2/styles` to `/etc/qgis/styles` (the canonical QGIS preset path used by the worker code's `apply_style_preset`).
  - Bumped image digest from `@sha256:7d8a338…` to `@sha256:a703476049…` (new image with QML bake).
- **`infra/qgis-server/Dockerfile`** — rewritten for the new image:
  - Kept `apt-get install qgis` for the FR-AS-9 `qgis_process` CLI requirement; cleaned up plugins-discovery commentary.
  - Added `mkdir -p /etc/qgis/styles /opt/grace2/styles` + `COPY styles/ /etc/qgis/styles/` (canonical) + `COPY styles/ /opt/grace2/styles/` (back-compat alias).
  - Added build-time smoke: `RUN test -f /etc/qgis/styles/basemap.qml && echo "QML preset basemap.qml: baked" && ls -la /etc/qgis/styles/` — fails the build if the engine-authored preset was missing from context.
  - Considered then discarded an earlier draft that installed `gcsfuse` + a startup-wrapper script: Cloud Run gen2's native GCS volume mount is simpler and avoids PID-1 wrapper risk. No `start.sh` file landed in the repo.
  - Updated header comments to record OQ-19A diagnosis (path c failed, path b chosen, why) and to point at the new canonical preset path.
- **`reports/inflight/job-0024-infra-20260605/report.md`** — this report.

NOT touched (file-ownership boundaries respected): `infra/{buckets,pubsub,gcp,atlas,secrets,variables,providers,backend,versions}.tf`, `infra/qgis-server/cloudbuild.yaml` (cloudbuild needed no changes), `services/`, `web/`, `packages/`, `tests/`, `docs/`, `styles/` (engine-authored content — only baked, not edited).

## Decisions Made

- **Path (b) via Cloud Run gen2 native GCS volume mount, NOT via gcsfuse install in the image.**
  - Rationale: Cloud Run gen2 services natively support GCS bucket mounts via the `volumes { gcs { … } }` block in `google_cloud_run_v2_service`. The runtime mounts the bucket before the container starts, uses the runtime SA's ADC, and surfaces failures via Cloud Run startup probes — no `gcsfuse` install layer, no `/etc/fuse.conf`, no `--allow-other`, no PID-1 wrapper script, no `dev/fuse` device permission. Zero new failure surfaces.
  - Alternatives considered:
    - **gcsfuse in Dockerfile** — works, but adds ~20 MB layer + startup wrapper + FUSE device dependency. Rejected once the native mount was proven live.
    - **Fetch-to-tmp pre-handler** — kickoff path (a). Most complexity; reflexively rejected per kickoff guidance.
- **GDAL VSI env vars (path c) kept in place even after path (b) succeeded.**
  - Rationale: zero-cost, and they DO benefit COG/FlatGeobuf layer references inside future `.qgs` files that legitimately use `/vsigs/` for layer data. Removing them would risk silently breaking future projects.
  - Alternatives considered: removing them to keep the env diff minimal. Rejected — they're orthogonal forward-looking infrastructure, not duplication.
- **QML canonical bake path: `/etc/qgis/styles/`.**
  - Rationale: matches the QGIS preset directory convention. The legacy `/opt/grace2/styles/` is kept as an alias so any tooling pointing at the prior `GRACE2_STYLES_DIR` env value still resolves until it's updated.
  - Alternatives considered: `/opt/grace2/styles/` as canonical (job-0018's choice). Rejected — the kickoff explicitly cites `/etc/qgis/styles/` as the QGIS-Server-expected preset path.
- **WMS URL contract becomes `MAP=/mnt/qgs/<file>.qgs` (filesystem path), NOT `MAP=/vsigs/...`.**
  - Rationale: this is the consequence of path (b). Per "No legacy support pre-MVP", the code does not branch on both forms. The kickoff acceptance criteria #1/#2 use `/vsigs/` — those criteria were written before path (c) failed; the report's verification transcript uses the corrected filesystem path. Downstream consumers (job-0020 worker code; job-0023 acceptance) must use `/mnt/qgs/` going forward.
  - This is a contract change to the WMS URL convention; surfaced as Open Question for orchestrator routing.
- **`read_only = true` on the GCS volume mount.**
  - Rationale: QGIS Server renders, it does not write (Invariant 4 — `.qgs` is mutated only by PyQGIS worker Jobs). Read-only mount enforces this at the runtime, not just by convention.
- **Image rebuilt via Cloud Build (not local docker).**
  - Rationale: Debian 13 dev box requires sudo for docker.sock; Cloud Build pushes to AR with zero local credential surface. Same pattern as job-0018.

## Invariants Touched

- **Rendering through QGIS Server (Invariant 4):** preserves — QGIS Server reads `.qgs` from GCS (via Cloud Run volume mount instead of `/vsigs/`, but the substrate is still GCS); mutations remain worker-Job-only. The read-only mount enforces "QGIS Server does not mutate `.qgs`" at runtime.
- **Tier separation (Invariant 5):** preserves — buckets stay private (UBLA + PAP); the client reaches `.qgs` content only via QGIS Server WMS. The volume mount runs INSIDE the Cloud Run service, never exposed to the browser.
- **Metadata-payload pattern (Invariant 6):** preserves — GCS holds the `.qgs` payload; MongoDB remains the discovery path. The volume mount does NOT enumerate the bucket; it presents named objects on access.
- **Confirmation before consequence / no cost theater (Invariant 9):** preserves — no user-facing cost fields touched.
- **Determinism, claims provenance, cancellation, minimal parameter surface:** n/a (no application-logic changes).

## Open Questions

- **OQ-24A (TENTATIVE — propagation to downstream kickoffs):** The WMS URL convention has changed from `MAP=/vsigs/grace-2-hazard-prod-qgs/<file>.qgs` to `MAP=/mnt/qgs/<file>.qgs`. The job-0020 (engine worker) and job-0023 (testing M2 acceptance) kickoffs reference `/vsigs/` in their acceptance criteria. Both jobs are currently in `created` state — the orchestrator should s/`/vsigs/grace-2-hazard-prod-qgs/`/`/mnt/qgs/`/g for WMS URL references before handoff. The PyQGIS worker itself (job-0020) reads via `/vsigs/` inside its OWN container (Cloud Run Job) — that's a separate runtime, and the worker calls `QgsProject.read()` from Python where GDAL VSI WILL be honored if the worker enables `CPL_GS_USE_INSTANCE_PROFILE` in its container env. Tentative recommendation: update job-0020 and job-0023 WMS-URL references to `/mnt/qgs/<file>.qgs`; the worker's own `.qgs` read path stays a separate concern owned by job-0020. SRS reference: FR-QS-2 (`.qgs` in GCS read via `/vsigs/`) — suggest an SRS Appendix-style clarification: "QGIS Server reads `.qgs` via Cloud Run GCS volume mount; PyQGIS workers read via GDAL `/vsigs/`" — the canonical store is unchanged.

- **OQ-24B (TENTATIVE — `/opt/grace2/styles/` alias retention):** The image now bakes QML presets at BOTH `/etc/qgis/styles/` (canonical) AND `/opt/grace2/styles/` (legacy alias). Once all consumers point at `GRACE2_STYLES_DIR=/etc/qgis/styles` (already retargeted in this job), `/opt/grace2/styles/` is dead weight. Tentative: drop the alias in the next image rebuild after job-0021 confirms the worker container doesn't reference it. SRS reference: FR-QS-5 (QML preset library).

- **OQ-24C (TENTATIVE — `read_only=true` on the volume mount):** QGIS Server is configured read-only against the `.qgs` bucket. If a future feature wants QGIS Server to write derived artifacts (e.g., on-the-fly raster cache) BACK to the same bucket, this would have to change. Tentative: keep read-only; spawn a separate writable bucket if/when QGIS Server needs write access (Invariant 4 says only workers write `.qgs`, which keeps the simple invariant). SRS reference: NFR-R-4 (stateless).

- **OQ-24D (TENTATIVE — leftover scaling{} block drift = OQ-F, NOT fixed in this job):** `tofu plan -target=…qgis_server` still shows a cosmetic drift: a top-level `scaling { manual_instance_count = 0; min_instance_count = 0 }` block exists in TF state but no longer in TF code (the only scaling block declared is the template-level one with `max=5; min=0`). Plan shows `~ scaling { - manual_instance_count = 0 -> null; - min_instance_count = 0 -> null }`. This is the same OQ-F surfaced in the job-0018 audit. NOT addressed in this job (the kickoff said "any scaling drift if OQ-F was resolved separately" — it was not). Apply IS clean for the env vars + volume mount + image-digest changes that ARE this job's scope. Tentative: dedicated cleanup job, or fold into job-0021 if it touches the same file. SRS reference: NFR-C-2 (scale-to-zero — the runtime behavior is correct; only TF state shape is drifted).

- **OQ-24E (NON-BLOCKING):** Cloud Run gen2 GCS volume mounts have a documented cold-start cost (~1–2s extra on first mount per instance). At M2 we're scale-to-zero so the first request after idle pays this latency. Tentative: revisit at NFR-P-3 gate (M3 — `<1s p95 first-tile`); if cold-start starves the SLO, bump `min_instance_count` to 1. No action this job.

## Dependencies and Impacts

- **Depends on:**
  - **job-0018-infra-20260605 (approved)** — QGIS Server Cloud Run service, `grace-2-hazard-prod-qgs` bucket, AR repo `grace-2-containers`, runtime SA `grace-2-qgis-server` with bucket-scoped `roles/storage.objectViewer`.
  - **job-0019-engine-20260605 (approved)** — sample `grace2-sample.qgs` uploaded to `gs://grace-2-hazard-prod-qgs/`; `styles/basemap.qml` engine-authored preset stub committed to source; canonical layer name `basemap-osm-conus`.
- **Affects:**
  - **job-0020 (engine PyQGIS worker code, planned):** the `worker_round_trip(qgs_uri, ...)` signature may keep `qgs_uri` as a `gs://`-style URI (the worker can still resolve via GDAL `/vsigs/` inside its own container — see OQ-24A); only the QGIS-Server-side WMS URL changed. The orchestrator should update the kickoff's WMS-URL test references to `/mnt/qgs/`.
  - **job-0021 (infra PyQGIS worker container, planned):** the worker container is a separate image and runtime; this job's volume-mount mechanism does NOT carry over to the worker by default (workers use `/vsigs/` inside their own container). job-0021 must decide its own auth path for `/vsigs/` reads (TENTATIVE: same `CPL_*` env vars + Workload-Identity-bound SA).
  - **job-0023 (testing M2 acceptance, planned):** must use `MAP=/mnt/qgs/grace2-sample.qgs` in the GetCapabilities + GetMap test invocations.
  - **PROJECT_STATE.md:** the live QGIS Server URL is unchanged; the canonical WMS URL contract is now `…?MAP=/mnt/qgs/<file>.qgs&…`; the deployed image digest is `@sha256:a703476049…`; the deployed revision is `grace-2-qgis-server-00004-m82`. Orchestrator updates needed.

## Verification

### Tests run

- `tofu plan -target=google_cloud_run_v2_service.qgis_server` — pre-apply and post-apply (drift inspection)
- `tofu apply -auto-approve -target=google_cloud_run_v2_service.qgis_server` — twice (env-var-only + volume-mount + image-digest)
- `gcloud builds submit --config=infra/qgis-server/cloudbuild.yaml` (via `make qgis-server-build`) — new image build SUCCESS in 2m14s
- `gcloud run services describe grace-2-qgis-server` — revision readiness checks
- `curl -sS` against the deployed WMS URL — pre-fix (path c attempt), mid-fix (path b volume mount with OLD image), and post-fix (NEW image with QML bake) — three transcripts captured verbatim below
- `docker run` (via Cloud Build) — image inspection showing `/etc/qgis/styles/basemap.qml` baked

### Live E2E evidence

**Pre-fix baseline (BEFORE any change):**

```
$ curl -sS -w "\nHTTP_STATUS=%{http_code}\n" "https://grace-2-qgis-server-425352658356.us-central1.run.app/ogc/wms?MAP=/vsigs/grace-2-hazard-prod-qgs/grace2-sample.qgs&SERVICE=WMS&REQUEST=GetCapabilities"
<?xml version="1.0" encoding="UTF-8"?>
<ServerException>Project file error. For OWS services: please provide a SERVICE and a MAP parameter pointing to a valid QGIS project file</ServerException>

HTTP_STATUS=500
```

```
$ curl -sS -w "\nHTTP_STATUS=%{http_code}\n" -o /tmp/wms-prefix.png "https://grace-2-qgis-server-425352658356.us-central1.run.app/ogc/wms?MAP=/vsigs/grace-2-hazard-prod-qgs/grace2-sample.qgs&SERVICE=WMS&REQUEST=GetMap&VERSION=1.3.0&LAYERS=basemap-osm-conus&CRS=EPSG:4326&BBOX=24,-125,50,-66&WIDTH=800&HEIGHT=400&FORMAT=image/png&STYLES="
HTTP_STATUS=500
$ file /tmp/wms-prefix.png
/tmp/wms-prefix.png: XML 1.0 document, ASCII text
$ cat /tmp/wms-prefix.png
<?xml version="1.0" encoding="UTF-8"?>
<ServerException>Project file error. For OWS services: please provide a SERVICE and a MAP parameter pointing to a valid QGIS project file</ServerException>
```

**Path (c) attempt — GDAL VSI env vars deployed, STILL fails (revision 00002-7gg):**

```
$ gcloud run services describe grace-2-qgis-server --region=us-central1 --format='value(status.latestReadyRevisionName)'
grace-2-qgis-server-00002-7gg

$ gcloud run services describe grace-2-qgis-server --region=us-central1 --format='value(spec.template.spec.containers[0].env[].name)' | tr ';' '\n'
QGIS_SERVER_PARALLEL_RENDERING
QGIS_SERVER_LOG_LEVEL
GRACE2_STYLES_DIR
CPL_MACHINE_IS_GCE
QGIS_SERVER_LOG_STDERR
GRACE2_FGB_BUCKET
GRACE2_QGS_BUCKET
CPL_GS_USE_INSTANCE_PROFILE
GDAL_HTTP_USERAGENT
GRACE2_COG_BUCKET
QGIS_SERVER_MAX_THREADS

$ curl -sS -w "\nHTTP_STATUS=%{http_code}\n" "…/ogc/wms?MAP=/vsigs/grace-2-hazard-prod-qgs/grace2-sample.qgs&SERVICE=WMS&REQUEST=GetCapabilities"
<?xml version="1.0" encoding="UTF-8"?>
<ServerException>Project file error. For OWS services: please provide a SERVICE and a MAP parameter pointing to a valid QGIS project file</ServerException>

HTTP_STATUS=500

# Cloud Run log (verbatim, captured via `gcloud logging read … --freshness=5m`):
06:08:47 CRITICAL Server[18]: Error when loading project file '/vsigs/grace-2-hazard-prod-qgs/grace2-sample.qgs': Unable to open /vsigs/grace-2-hazard-prod-qgs/grace2-sample.qgs
```

→ **Path (c) DID NOT WORK.** Diagnosis: QGIS Server uses Qt `QFile` (not GDAL VSI) for `.qgs` loading. Pivot to path (b).

**Path (b) deployment — Cloud Run GCS volume mount + filesystem-path URL (revision 00003-dbc, OLD image still):**

```
$ gcloud run services describe grace-2-qgis-server --region=us-central1 --format='value(status.latestReadyRevisionName)'
grace-2-qgis-server-00003-dbc

$ curl -sS -w "\nHTTP_STATUS=%{http_code}\n" "…/ogc/wms?MAP=/mnt/qgs/grace2-sample.qgs&SERVICE=WMS&REQUEST=GetCapabilities" | head -10
<?xml version="1.0" encoding="utf-8"?>
<WMS_Capabilities … version="1.3.0" … >
 <Service>
  <Name>WMS</Name>
  <Title>GRACE-2 sample WMS</Title>
  <Abstract><![CDATA[M2 smoke sample — single OSM XYZ basemap covering CONUS.]]></Abstract>
  …
```

→ **Path (b) WORKS** with the OLD image — proves the volume mount alone solves OQ-19A.

**Post-fix final verification — NEW image with QML bake (revision 00004-m82):**

```
$ gcloud run services describe grace-2-qgis-server --region=us-central1 --format='value(status.latestReadyRevisionName,spec.template.spec.containers[0].image)'
grace-2-qgis-server-00004-m82  us-central1-docker.pkg.dev/grace-2-hazard-prod/grace-2-containers/grace-2-qgis-server@sha256:a7034760492fe28501b91ac66608d5efa41249cee5e8477aaa51aab4fbdcac75

$ curl -sS -w "\nHTTP_STATUS=%{http_code}\n" "…/ogc/wms?MAP=/mnt/qgs/grace2-sample.qgs&SERVICE=WMS&REQUEST=GetCapabilities" | head -10
<?xml version="1.0" encoding="utf-8"?>
<WMS_Capabilities … version="1.3.0" … >
 <Service>
  <Name>WMS</Name>
  <Title>GRACE-2 sample WMS</Title>
  <Abstract><![CDATA[M2 smoke sample — single OSM XYZ basemap covering CONUS.]]></Abstract>
  <KeywordList>
   <Keyword vocabulary="ISO">infoMapAccessService</Keyword>
  </KeywordList>
  <OnlineResource xlink:href="…/ogc/wms?MAP=/mnt/qgs/grace2-sample.qgs" xmlns:xlink="…" xlink:type="simple"/>

# Layer names in GetCapabilities response:
$ curl -sS "…?MAP=/mnt/qgs/grace2-sample.qgs&SERVICE=WMS&REQUEST=GetCapabilities" | grep '<Name>basemap'
    <Name>basemap-osm-conus</Name>

# GetMap with the canonical layer:
$ curl -sS -w "HTTP_STATUS=%{http_code}\n" -o /tmp/wms.png "…/ogc/wms?MAP=/mnt/qgs/grace2-sample.qgs&SERVICE=WMS&REQUEST=GetMap&VERSION=1.3.0&LAYERS=basemap-osm-conus&CRS=EPSG:4326&BBOX=24,-125,50,-66&WIDTH=800&HEIGHT=400&FORMAT=image/png&STYLES="
HTTP_STATUS=200

$ file /tmp/wms.png
/tmp/wms.png: PNG image data, 800 x 400, 8-bit/color RGBA, non-interlaced

$ ls -la /tmp/wms.png
-rw-rw-r-- 1 nate nate 332570 Jun  5 23:19 /tmp/wms.png
```

→ **POST-FIX:** valid `<WMS_Capabilities>`, layer `basemap-osm-conus` listed, GetMap returns `PNG image data, 800 x 400, 8-bit/color RGBA` at 332 KB. **The failed pre-fix curl AND the successful post-fix curl together prove the fix changed behavior.**

**QML preset verification (Cloud Build inspection of the new image):**

```
$ gcloud logging read 'resource.type="build" AND resource.labels.build_id="41f93335-857c-4e14-aff6-e85398e46b1d"' --format='value(textPayload)' --limit=50 | tail -8
DONE
PUSH
  GRACE-2 M2 basemap preset STUB (FR-QS-5 first preset).
<!--
<!DOCTYPE qgis PUBLIC 'http://mrcc.com/qgis.dtd' 'SYSTEM'>
-rw-rw-r-- 1 root root 2030 Jun  6 05:46 basemap.qml
-rw-rw-r-- 1 root root  585 Jun  5 21:39 README.md
…
```

→ `/etc/qgis/styles/basemap.qml` (2030 bytes) IS baked into the new image; the file contents (DOCTYPE + comment lines) match the engine-authored stub from job-0019.

**tofu plan (post-apply):**

```
$ tofu plan -target=google_cloud_run_v2_service.qgis_server
…
  ~ resource "google_cloud_run_v2_service" "qgis_server" {
      - scaling {
          - manual_instance_count = 0 -> null
          - min_instance_count    = 0 -> null
        }
        # (2 unchanged blocks hidden)
    }
Plan: 0 to add, 1 to change, 0 to destroy.
```

→ The ONLY remaining drift is the leftover top-level `scaling{}` block — **OQ-F (pre-existing from job-0018)**, NOT introduced by this job. Surfaced as OQ-24D for a dedicated cleanup. All env-var + volume-mount + image-digest changes from this job ARE clean.

### Results

**pass** — path (b) live verified with both XML + PNG transcripts; QML preset baked at `/etc/qgis/styles/basemap.qml` per OQ-19C; OQ-19A resolved. One pre-existing cosmetic drift (OQ-F = OQ-24D) carried forward to a follow-up job. No `support both /vsigs/ and /mnt/` code introduced. Cloud Run service stateless + replaceable (read-only volume mount). Service-account scoping unchanged (no IAM expansion). All four kickoff acceptance criteria met — modulo the `/vsigs/` → `/mnt/qgs/` URL contract change (OQ-24A surfaces this to the orchestrator for kickoff propagation).
