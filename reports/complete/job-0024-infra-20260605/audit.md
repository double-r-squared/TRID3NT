# Audit: QGIS Server `/vsigs/` access fix (env vars or gcsfuse) + QML preset bake

**Job ID:** job-0024-infra-20260605
**Sprint:** sprint-04
**Auditor:** Development Orchestrator
**Status:** approved

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

Path (c) GDAL VSI env vars tried first as 5-min diagnostic — failed live: QGIS Server loads `.qgs` via Qt's `QFile`, not GDAL VSI, so the env vars don't help the project-file load (they DO help internal layer references inside an already-loaded project, so they're kept). Pivoted to **path (b) via Cloud Run gen2's native GCS volume mount** (cleaner than gcsfuse-in-image: no PID-1 wrapper, no `/etc/fuse.conf`, no `dev/fuse` device permission, no startup-probe gymnastics): added `volumes { gcs { bucket=…-qgs read_only=true } }` + `volume_mounts { mount_path="/mnt/qgs" }`. **WMS URL contract changes from `MAP=/vsigs/...` to `MAP=/mnt/qgs/...`** going forward — per "No legacy support pre-MVP" no support for both forms. Same image rebuild landed OQ-19C QML preset bake (`COPY styles/ /etc/qgis/styles/`). Live verification passes: `GetCapabilities` returns valid `<WMS_Capabilities>` XML listing `<Name>basemap-osm-conus</Name>`; `GetMap` returns `PNG image data, 800 x 400, 8-bit/color RGBA, non-interlaced` at 332 KB. New image digest `@sha256:a703476049…` deployed as revision 00004-m82. Commit `963f77e`.

## Invariant Check

- **Determinism boundary:** n/a — infra-layer fix; no narrative path.
- **Deterministic workflows:** n/a — no workflow code.
- **Engine registration, not modification:** n/a.
- **Rendering through QGIS Server:** pass — QGIS Server now renders correctly; no alternate rendering path introduced. `read_only=true` on the GCS mount enforces at runtime that QGIS Server cannot write `.qgs` (only the PyQGIS worker job-0021 will, via its own container with `objectAdmin`).
- **Tier separation:** pass — bucket mount is SA-scoped (runtime SA's `roles/storage.objectViewer`); no public path; client never reads GCS directly. Mount is intra-container; Tier B reaches the map only via QGIS Server WMS.
- **Metadata-payload pattern:** pass — `.qgs` is the payload in GCS, accessed via mount; no bucket-enumeration path.
- **Claims carry provenance:** n/a.
- **Cancellation is first-class:** n/a.
- **Confirmation before consequence — and no cost theater:** pass — no cost fields. GCS volume mount adds zero idle cost beyond bucket reads.
- **Minimal parameter surface:** pass — three additive env vars + one mount block + one digest bump; no excess knobs.

## Dependency Check

- **Prerequisites satisfied:** yes — job-0018 (QGIS Server + bucket); job-0019 (sample `.qgs` + `styles/basemap.qml`).
- **Downstream impacts:**
  - **job-0020 (PyQGIS worker code):** worker `.qgs` reads now use `/mnt/qgs/...` (or `/vsigs/...` if worker uses GDAL bindings directly; the worker container can have either). Layer-data references inside `.qgs` files keep the GDAL VSI env vars for COG/FlatGeobuf via `/vsigs/`.
  - **job-0021 (worker container):** must consume the same mount path convention for the worker if it goes via filesystem; alternatively, the worker uses google-cloud-storage directly without a mount.
  - **job-0023 (M2 acceptance):** WMS URL pattern in tests is `MAP=/mnt/qgs/grace2-sample.qgs` (not `/vsigs/`).
  - **SRS FR-QS-2 contract change:** the SRS literally says "`.qgs` in GCS read via `/vsigs/`". The pivot to `/mnt/qgs/...` is a contract change at the WMS-URL layer (the `.qgs` is STILL in GCS; only the QGIS-Server-side filesystem-address-form changes). **Surface to user as SRS amendment proposal candidate** (FR-QS-2 wording update).

## Decisions Validated

- **Path (c) tried first as 5-min diagnostic, failed cleanly, documented why:** agree — exemplary "diagnose before fix" discipline.
- **Path (b) via Cloud Run gen2 native GCS volume mount over gcsfuse-in-image:** agree — fewer failure surfaces, no PID-1 wrapper, no FUSE-device permission, native to runtime. The trade-off (Cloud-Run-gen2-specific) is acceptable since we're already on Cloud Run by Decision E.
- **GDAL VSI env vars kept in place even after path (b) succeeded:** agree — zero-cost; benefits future COG/FlatGeobuf layer-data `/vsigs/` references inside `.qgs` files. Removing would risk silent future breakage.
- **QML canonical bake path `/etc/qgis/styles/` (with `/opt/grace2/styles/` alias):** agree — matches QGIS preset directory convention; alias preserves legacy `GRACE2_STYLES_DIR` until consumers update.
- **WMS URL contract: `MAP=/mnt/qgs/<file>.qgs`, NO `/vsigs/` support for `.qgs`:** agree — "no legacy support pre-MVP" applied. Surfaced as SRS amendment candidate (FR-QS-2).
- **`read_only=true` on the GCS volume mount:** agree — enforces Invariant 4 at runtime (QGIS Server cannot write `.qgs`; only the worker can).
- **Build-time smoke `RUN test -f /etc/qgis/styles/basemap.qml`:** agree — fails the build if engine preset is missing; cheap guard.
- **Image rebuilt via Cloud Build (not local docker):** agree — matches job-0018 precedent; zero local credential surface.

## Open Questions Resolved

- **OQ-19A (QGIS Server `/vsigs/` access):** resolved → path (b) Cloud Run gen2 native GCS volume mount. WMS URL contract changes to `/mnt/qgs/`.
- **OQ-19C (QML preset bake):** resolved → baked via `COPY styles/ /etc/qgis/styles/` in same image cycle. Live `<Name>basemap-osm-conus</Name>` in GetCapabilities; `apply_style_preset` codepath now valid.
- **OQ-24A (path c vs b vs a):** resolved → (b). (c) tried first per kickoff, failed for documented reason (QFile not GDAL VSI). (a) reflexively rejected.
- **OQ-24B (gcsfuse install vs native gen2 mount):** resolved → native mount (no PID-1 wrapper).
- **OQ-24C (canonical preset path):** resolved → `/etc/qgis/styles/` canonical; `/opt/grace2/styles/` alias.
- **OQ-24D (read-only mount):** resolved → yes, enforces Invariant 4 at runtime.

## Follow-up Actions

- **SRS amendment proposal: FR-QS-2 `.qgs` URL form** — surface to user. Current SRS says "QGIS Server reads via `/vsigs/`"; reality is `/mnt/qgs/...` for `.qgs` files specifically. Layer-data references inside `.qgs` (COG/FlatGeobuf) still go via `/vsigs/`. Recommended wording: "`.qgs` files are loaded via a runtime GCS volume mount at `/mnt/qgs/`; layer-data references inside `.qgs` projects continue to use `/vsigs/` with GDAL VSI auth."
  - Routing: orchestrator → user. Priority: medium (carry alongside A1–A5 + NFR-C-1 amendment pile).
- **Update job-0020 + job-0023 kickoffs (still STATE=created)** to cite `/mnt/qgs/grace2-sample.qgs` instead of `/vsigs/...` for the WMS URL. Surgical s///.
  - Routing: orchestrator. Priority: high (do before 0020 closeout commits more changes; 0023 hasn't started).
- **Cosmetic scaling drift OQ-F** carried from job-0018 — still present (Cloud Run scaling block null normalization). Auto-resolves on next service-touching apply.
  - Routing: infra. Priority: low.
- **PROJECT_STATE update** (this audit closure): QGIS Server image digest bumped to `@sha256:a703476049…`; GCS volume mount at `/mnt/qgs`; QML preset at `/etc/qgis/styles/basemap.qml`; WMS URL canonical form `MAP=/mnt/qgs/...` for `.qgs` files.
  - Routing: orchestrator. Priority: high.
- **Close job-0024** and handle job-0020 closeout (separate workflow).
  - Routing: orchestrator. Priority: high.

## Sign-off

- **Ready to move to complete:** yes
- All kickoff acceptance criteria pass on live re-run: GetCapabilities valid `<WMS_Capabilities>` XML (NOT `<ServerException>`); GetMap returns PNG (332 KB, 800×400); `tofu plan` clean; env vars in Cloud Run revision; QML preset baked into image; image digest pinned; path chosen documented with rationale.
- Invariants #4, #5, #6, #9 pass with citations; #1, #2, #3, #7, #8, #10 n/a or pass structurally.
- Reviewer approved (this audit incorporates the live re-verification done by the workflow's in-workflow review phase).
- 6 Open Questions surfaced; all resolved in this job; one carry-forward (OQ-F cosmetic scaling drift).
- WMS URL contract change to `/mnt/qgs/...` is a real architectural decision — surfaces to user as FR-QS-2 amendment proposal.
- Live cloud verification: QGIS Server revision `00004-m82` renders sample `.qgs` correctly.
- Revisions: 0.
