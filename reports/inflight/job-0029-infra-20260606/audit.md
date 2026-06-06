# Audit: QGIS Server Cloud Run CORS headers fix (unblocks MapLibre WMS tile fetches from browsers)

**Job ID:** job-0029-infra-20260606
**Sprint:** sprint-05
**Auditor:** Development Orchestrator
**Status:** assigned

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

## Invariant Check

## Dependency Check

## Decisions Validated

## Open Questions Resolved

## Follow-up Actions

## Sign-off
