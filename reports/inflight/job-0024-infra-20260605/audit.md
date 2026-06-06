# Audit: QGIS Server `/vsigs/` access fix (env vars or gcsfuse) + QML preset bake

**Job ID:** job-0024-infra-20260605
**Sprint:** sprint-04
**Auditor:** Development Orchestrator
**Status:** assigned

## Task Assignment

**Specialist:** infra
**Prerequisites:** job-0018 (QGIS Server Cloud Run service + GCS buckets; image digest `@sha256:7d8a338…`); job-0019 (sample `.qgs` in `gs://grace-2-hazard-prod-qgs/grace2-sample.qgs`; `styles/basemap.qml` preset stub committed to source). Read the job-0019 audit OQ-19A diagnosis and the QGIS Server log line `CRITICAL Server[18]: Unable to open /vsigs/grace-2-hazard-prod-qgs/grace2-sample.qgs`.
**SRS references:** FR-QS-1 (QGIS Server), FR-QS-2 (`.qgs` in GCS read via `/vsigs/`), FR-QS-5 (QML preset library baked into the container), NFR-S-2/S-5 (SA-scoped reads, no public buckets), NFR-R-4 (QGIS Server stateless + replaceable), NFR-PO-3 (IaC), Decision B/C (QGIS Server as rendering backend), Invariant 4 (Rendering through QGIS Server), Invariant 5 (Tier separation).

### Environment

Dev + prod substrate Linux (Debian 13 trixie, x86_64). All container builds `linux/amd64`-only. Consume the live cloud substrate from PROJECT_STATE (QGIS Server URL `https://grace-2-qgis-server-425352658356.us-central1.run.app`, AR repo `grace-2-containers`, GCS bucket `grace-2-hazard-prod-qgs` with the sample `.qgs` already uploaded). Toolchain already installed (gcloud, tofu, atlas, gh). `python3-venv` unavailable on Debian 13; use `virtualenv` if a helper venv is needed.

### Scope

Diagnose and fix the QGIS Server container's inability to open `.qgs` from `/vsigs/`. Per OQ-19A, three candidate paths exist; **try (c) first as a 5-minute diagnostic**, fall back to (b) if (c) does not work, fall back to (a) if both fail.

**Path (c) — GDAL VSI env vars (try first):**
1. Add to `infra/qgis-server/Dockerfile` or set as Cloud Run service env vars:
   - `CPL_MACHINE_IS_GCE=YES` (tells GDAL it's on GCE/Cloud Run for metadata-server auth flow)
   - `CPL_GS_USE_INSTANCE_PROFILE=YES` (use the runtime SA for `/vsigs/` reads)
   - Optionally: `GDAL_HTTP_USERAGENT=grace-2-qgis-server/0.1` for diagnosability
2. Decide: env vars in Cloud Run service config (in `infra/qgis-server.tf`) or baked in Dockerfile? Recommend Cloud Run env (changeable without image rebuild). Surface as TENTATIVE.
3. Apply via `tofu apply`. Verify Cloud Run revision picks up the env. New revision deploys automatically.
4. Re-curl GetCapabilities + GetMap against the deployed URL. If now returns valid `<WMS_Capabilities>` XML and PNG bytes, fix is complete.

**Path (b) — gcsfuse FUSE mount (fall back if (c) fails):**
1. Add `gcsfuse` install to `infra/qgis-server/Dockerfile` (apt + gpg key).
2. Add a startup script that mounts `gs://grace-2-hazard-prod-qgs` at `/mnt/qgs` before `qgis-server` starts (use Cloud Run's startup probe + a wrapper entrypoint).
3. Rewrite the WMS URL to use `MAP=/mnt/qgs/grace2-sample.qgs` (filesystem path) instead of `MAP=/vsigs/...`.
4. Surface the implications: gcsfuse adds latency and a startup dependency; document as a known trade-off.
5. Re-deploy; verify GetCapabilities + GetMap.

**Path (a) — fetch-to-tmp pre-handler (last resort):**
1. A small reverse-proxy in the container fetches the .qgs to `/tmp` on first request, then forwards to qgis-server with the local path. Most complexity; document and reject unless (b) and (c) both fail.

**QML preset bake (per OQ-19C, bundled into this job's image rebuild):**
- Update `infra/qgis-server/Dockerfile` to `COPY styles/basemap.qml /etc/qgis/styles/basemap.qml` (or the QGIS-Server-expected preset path; verify against image docs).
- Bake during the same image rebuild that lands path (b) or (c).

**Image rebuild + digest pin:**
- `make qgis-server-build && make qgis-server-push` (or equivalent) — capture the new digest from AR.
- Update `infra/qgis-server.tf` image arg from the prior digest to the NEW digest.
- `tofu plan` should show only the image-arg change (no scaling drift if OQ-F was resolved separately).
- `tofu apply` — Cloud Run deploys new revision.

### File ownership (exclusive)

- `infra/qgis-server.tf` (env vars + new image digest pin) — additive edits
- `infra/qgis-server/Dockerfile` — path (b) or (c) deps + QML preset COPY
- `infra/qgis-server/cloudbuild.yaml` — only if cloudbuild changes are needed (likely unchanged)
- Own report dir `reports/inflight/job-0024-infra-20260605/`

NOT touched: `infra/{buckets,pubsub,gcp,atlas,secrets,variables,providers,backend,versions}.tf` (other infra ownership); `services/`, `web/`, `packages/`, `tests/`, `docs/`, `styles/` (read-only here — `styles/basemap.qml` is engine-authored from job-0019, baked into image but not edited).

### Cross-cutting principles in force

- *Live E2E validation required* — must demonstrate live GetCapabilities + GetMap returning valid XML + PNG against the deployed service.
- *Diagnose before fix* — try path (c) as 5-min diagnostic before committing to a heavier path.
- *Surface uncertainty* — document path choice rationale; if (c) works, surface why we expected it might not.
- *Bundle small fixes; scan for all instances* — the QML bake (OQ-19C) and the env var fix (OQ-19A) and the cosmetic scaling drift (OQ-F) can all land in this single image+IaC cycle if they're touching the same files.
- *No legacy support pre-MVP* — `/vsigs/` is the canonical path per FR-QS-2; do NOT introduce a "support both" code path.

### Acceptance criteria (reviewer re-runs)

- `curl -s "https://grace-2-qgis-server-425352658356.us-central1.run.app/ogc/wms?MAP=/vsigs/grace-2-hazard-prod-qgs/grace2-sample.qgs&SERVICE=WMS&REQUEST=GetCapabilities" | head -20` returns valid `<WMS_Capabilities>` XML (NOT `<ServerException>`).
- `curl -s "<URL>?MAP=...&SERVICE=WMS&REQUEST=GetMap&VERSION=1.3.0&LAYERS=basemap-osm-conus&CRS=EPSG:4326&BBOX=24,-125,50,-66&WIDTH=800&HEIGHT=400&FORMAT=image/png&STYLES=" -o /tmp/wms.png && file /tmp/wms.png` returns `PNG image data` (NOT `XML 1.0 document`).
- `tofu plan` post-apply: `No changes` (only the image-arg digest update from before this job, and any env-var additions, should appear in the apply diff).
- `gcloud run services describe grace-2-qgis-server --region=us-central1 --format='value(spec.template.spec.containers[0].env)'` shows the new GDAL VSI env vars (if path (c)).
- Verbatim transcripts of attempts at path (c) and (b) if (c) failed; rationale for the path chosen.
- Report cites OQ-19A as resolved; OQ-19C QML bake confirmed (the new image contains `/etc/qgis/styles/basemap.qml` per `docker run --rm <new-image> ls /etc/qgis/styles/`).

Surface contestable choices as Open Questions with TENTATIVE tags.

## Assessment

## Invariant Check

## Dependency Check

## Decisions Validated

## Open Questions Resolved

## Follow-up Actions

## Sign-off
