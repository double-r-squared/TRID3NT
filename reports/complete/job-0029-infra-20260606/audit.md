# Audit: QGIS Server Cloud Run CORS headers fix (unblocks MapLibre WMS tile fetches from browsers)

**Job ID:** job-0029-infra-20260606
**Sprint:** sprint-05
**Auditor:** Development Orchestrator
**Status:** approved

## Task Assignment

**Specialist:** infra
**Prerequisites:** job-0018 + job-0024 (QGIS Server Cloud Run service + image digest pin pattern); job-0027 audit OQ-W-27-1 (CORS gap diagnosis).
**SRS references:** FR-WC-2 (browser-rendered MapLibre + QGIS Server tiles), FR-DT-2/5 (Tier B served via QGIS Server WMS to the client), FR-QS-1 (QGIS Server on Cloud Run), NFR-S-1 (TLS termination), Invariant 4 (Rendering through QGIS Server), Invariant 5 (Tier separation — no public bucket; QGIS Server is the canonical Tier B path).

### Environment

Dev + prod substrate Linux (Debian 13). All container builds `linux/amd64`-only. Consume live cloud substrate from `PROJECT_STATE.md`: QGIS Server URL `https://grace-2-qgis-server-425352658356.us-central1.run.app`, AR repo `grace-2-containers`, image `@sha256:a703476…` (job-0024 baseline). `python3-venv` unavailable; use `virtualenv` if needed.

### Background

Job-0027's headless-browser capture transcripts show the page loads cleanly but MapLibre WMS tile fetches against the deployed QGIS Server return without an `Access-Control-Allow-Origin` header, triggering Chromium + Firefox CORS rejection. Result: page renders the UI shell but the map canvas stays dark. This blocks job-0025's live visual evidence step and job-0028's tile-rendering test.

### Scope

1. **Diagnose the CORS gap and pick a fix:**
   - **Path (a) — QGIS Server config:** add `<CORSExceptions>` block to the project's QGIS Server `.qgs` metadata, or set the `QGIS_SERVER_ALLOW_HEADERS` / `QGIS_SERVER_CORS_ORIGINS` env vars in the Cloud Run service. Check QGIS Server 3.40 docs for the canonical env var name.
   - **Path (b) — Cloud Run service-level CORS:** Cloud Run has a built-in response-header-injection mechanism via `ingress` config or via a small reverse-proxy sidecar that injects CORS headers on responses. Heavier than (a).
   - **Path (c) — Cloud Load Balancer / Cloud Armor:** add a header policy in front of Cloud Run. Heaviest. Reserve for production hardening.
   - **Try path (a) first** as 5-min diagnostic (env var change in `infra/qgis-server.tf` + Cloud Run revision deploy without image rebuild). If QGIS Server 3.40 honors a CORS env var, that's the cleanest fix.
   - If (a) doesn't work: fall back to (b) with explicit nginx-side header injection in the QGIS Server Dockerfile (requires image rebuild).

2. **Origin scoping decision (surface as TENTATIVE):**
   - **Permissive `Access-Control-Allow-Origin: *`** — fine for dev / pre-MVP since the QGIS Server response payload is map tiles (not credentialed user data); no cookie/auth headers in the request flow. Recommend this for M3.
   - **Scoped to `http://localhost:5173` + future deployed web origin** — more locked-down; the right posture once production hosting lands.
   - For M3 the recommended default is `*`; revisit at M9/M10 production hosting.

3. **Bundle small fixes (cosmetic scaling drift OQ-F carry-forward):** if the path involves a `tofu apply` against the Cloud Run service, the OQ-F scaling-block null normalization will auto-resolve. Document.

4. **Live re-verify:**
   - `curl -I "https://grace-2-qgis-server-425352658356.us-central1.run.app/ogc/wms?MAP=/mnt/qgs/grace2-sample.qgs&SERVICE=WMS&REQUEST=GetCapabilities"` shows `access-control-allow-origin: *` (or scoped value).
   - Re-run `make ui-tour` against the redeployed service — `chromium-initial.png` should now show actual basemap tiles, not dark canvas.
   - Commit the **updated** canonical baselines to `tests/m3/artifacts/{chromium,firefox}-initial.png` (replacing the dark-canvas versions).

5. **Update `tofu plan`** must remain clean post-apply.

### File ownership (exclusive)

- `infra/qgis-server.tf` (additive env vars + possibly new image digest pin)
- `infra/qgis-server/Dockerfile` (only if path (b) is needed — reverse-proxy sidecar)
- `tests/m3/artifacts/chromium-initial.png` + `firefox-initial.png` (updated baselines after fix verified — exception to the engine/web ownership rule since these are downstream artifacts of the infra fix)
- Own report dir

NOT touched: `infra/{buckets,pubsub,gcp,atlas,secrets,worker,variables,providers,backend,versions}.tf`, `infra/worker/**`, `infra/conda/**`, `services/`, `packages/`, `web/`, `tools/`, `Makefile`, `docs/SRS_v0.3.md`.

### Cross-cutting principles in force

*Live E2E validation required* — must demonstrate live curl with CORS header + headless-browser screenshot showing actual basemap tiles. *Diagnose before fix* — try path (a) env var first. *Surface uncertainty* — origin scoping choice documented as TENTATIVE. *Bundle small fixes* — OQ-F scaling drift auto-resolves on apply. *No legacy support pre-MVP* — single CORS path, no support-both branching.

### Acceptance criteria (reviewer re-runs)

- `curl -I` against deployed QGIS Server WMS endpoint shows `access-control-allow-origin: *` (or scoped value documented as the M3 default).
- `make ui-tour` produces canonical baselines that now show actual basemap raster tiles (not dark canvas); diff against the pre-fix baselines (job-0027 audit's artifact) makes the change visible.
- `tofu plan` clean post-apply (or only the documented OQ-F carry-forward).
- Path chosen documented with rationale; if (a) failed and (b) was used, the failure mode is captured.
- New canonical baselines committed to `tests/m3/artifacts/{chromium,firefox}-initial.png`.
- No regression: GetCapabilities still returns valid `<WMS_Capabilities>` XML; GetMap still returns PNG.

Surface contestable choices as Open Questions with TENTATIVE tags.

## Assessment

Path (a) CORS env vars tried first as 5-min diagnostic — confirmed dead (qgis/qgis-server 3.40 LTR FCGI doesn't honor any CORS env var; bundled nginx emits no CORS either). Pivoted to **path (b) image rebuild with custom `infra/qgis-server/nginx.conf`** (166 lines, preserves upstream config 1:1, adds 5 CORS `add_header always` lines at server scope + OPTIONS preflight short-circuit + per-location re-assertion in `/qgis/` FCGI block). New image `@sha256:57d0f43b…` built via Cloud Build `ae4433d2`, deployed as revision `grace-2-qgis-server-00006-5fw`. Live verification: `curl -I` against WMS GetCapabilities returns `access-control-allow-origin: *` + companion ACL headers; `curl -X OPTIONS` returns 204 + full ACL set; GetMap still returns 332 KB PNG (no regression). Canonical baselines RE-CAPTURED: `tests/m3/artifacts/chromium-initial.png` 32 684 → 755 326 bytes (tiles render); `firefox-initial.png` 65 429 → 887 340 bytes (tiles render). Cosmetic OQ-F scaling drift persists (carried as OQ-29-A). Work was swept into commit `00e4902` alongside the orchestrator-side close of job-0025 (file-ownership smell — see Follow-ups).

## Invariant Check

- **Determinism boundary:** n/a — infra-layer fix.
- **Deterministic workflows:** n/a.
- **Engine registration, not modification:** n/a.
- **Rendering through QGIS Server:** pass — CORS fix preserves the rendering substrate; no second rendering path created. New image baked with `nginx -t` build-time smoke catches malformed config before deploy.
- **Tier separation:** pass — no public bucket created; `Access-Control-Allow-Origin: *` is on the QGIS Server response only (which is map tiles, not credentialed user data; no cookie/auth headers in the request flow).
- **Metadata-payload pattern:** n/a — bucket access unchanged.
- **Claims carry provenance:** n/a.
- **Cancellation is first-class:** n/a.
- **Confirmation before consequence — and no cost theater:** pass — zero cost fields; nginx adds zero idle cost.
- **Minimal parameter surface:** pass — single config file change; no new TF variables.

## Dependency Check

- **Prerequisites satisfied:** yes — job-0018 (image base), job-0024 (Cloud Run gen2 GCS mount + image digest-pin pattern), job-0027 (OQ-W-27-1 diagnosis).
- **Downstream impacts:**
  - **job-0025 (web basemap pivot, approved):** visual evidence step previously qualified due to CORS — now satisfied retroactively. The post-fix baselines replace the dark-canvas versions.
  - **job-0026 (PipelineStrip, in flight):** Playwright captures now show real basemap tiles behind the pipeline strip.
  - **job-0028 (M3 acceptance):** tile-rendering test now passes against the deployed substrate.
  - **Future production hardening:** origin scoping `*` is dev-only; tighten to deployed-web-origin allow-list at M9/M10 (OQ-29-B).

## Decisions Validated

- **Path (b) via custom nginx.conf (NOT path-b sidecar or path-c Cloud Armor):** agree — nginx is already the request fronter in the upstream image; adding CORS there is single-config-file change with no new container, no new resource, no new failure mode. Sidecar would double cold-start; Cloud Armor is production-hardening, not M3 unblock.
- **Path (a) attempt documented (env vars on revision `00005-rrc`, confirmed dead, reverted):** agree — exemplary diagnose-before-fix discipline; the failure transcript is the proof that env-var-only doesn't work for this image.
- **`add_header ... always` flag:** agree — ensures CORS headers reach browser on 4xx/5xx too; without it, FCGI errors arrive header-less and browser mis-reports as CORS failure instead of real status.
- **Per-location re-assertion in `/qgis/` FCGI block:** agree — nginx `add_header` does NOT inherit from outer scopes when inner block defines any `add_header`; re-asserting is necessary for FCGI-served WMS bytes to carry headers.
- **OPTIONS preflight short-circuit at nginx (`return 204`):** agree — never reaches FCGI; faster + reliable.
- **Origin scoping `*` for M3:** agree (TENTATIVE per OQ-29-B) — dev posture acceptable since QGIS Server response is map tiles (no credentialed user data); tighten at M9/M10 production hosting.
- **`RUN nginx -t` build-time smoke:** agree — catches malformed conf before runtime; cheap guard.
- **No new TF variables / no new env vars committed:** agree — the dead path-(a) env vars were applied via `gcloud run services update` during diagnosis then removed; never committed.

## Open Questions Resolved

- **OQ-W-27-1 (CORS gap surfaced by job-0027):** CLOSED → path-(b) custom nginx.conf injection.
- **OQ-29-A (cosmetic scaling drift persists):** carry-forward; auto-resolves on next service-touching apply that involves the scaling block.
- **OQ-29-B (origin scoping `*` vs deployed-origin allow-list):** TENTATIVE accept for M3; revisit at M9/M10 production hosting.
- **OQ-29-C (path-a env var failure mode):** documented in report Verification; future infra ops should not retry path-a unless QGIS Server image upstream adds CORS env-var support.

## Follow-up Actions

- **File-ownership cleanup note:** the job-0029 specialist work (infra/qgis-server/{Dockerfile,nginx.conf}, infra/qgis-server.tf, tests/m3/artifacts/{chromium,firefox}-initial.png) was swept into commit `00e4902` (orchestrator-side close of job-0025) rather than a dedicated `job-0029:` namespaced commit. Functionally correct, file-ownership smell only. The job-0029 report.md is the artifact-of-record for what landed.
  - Routing: orchestrator (note for future closeout discipline). Priority: low (process improvement).
- **Origin scoping tightening at M9/M10** (OQ-29-B): allow-list the deployed web origin instead of `*` for production hardening.
  - Routing: infra. Priority: future-sprint.
- **PROJECT_STATE update** (this audit closure): CORS fix landed; image bumped to `@sha256:57d0f43b…`; revision `00006-5fw` serving; canonical baselines updated.
  - Routing: orchestrator. Priority: high.
- **Close job-0029. Sprint-05 now has 3 of 6 jobs closed; 0026 closeout pending.** Routing: orchestrator. Priority: high.

## Sign-off

- **Ready to move to complete:** yes
- All kickoff acceptance criteria pass on live re-run: CORS header present on responses; preflight OPTIONS returns 204; GetMap returns PNG (no regression); canonical baselines updated showing real basemap tiles; path chosen (b) documented with rationale; path (a) failure mode documented.
- Invariants #4 + #5 preserved; #9 pass; rest n/a.
- 3 Open Questions surfaced; OQ-W-27-1 closed; OQ-29-A carry-forward; OQ-29-B deferred to production hardening.
- File-ownership note for future closeout discipline (not blocking).
- Live cloud substrate end-to-end verified: browser → QGIS Server → /mnt/qgs/grace2-sample.qgs → basemap-osm-conus → PNG tiles painted.
- Workflow failed at StructuredOutput step (transient harness issue); orchestrator authored this audit from disk + report contents.
- Revisions: 0.
