# Report: QGIS Server Cloud Run CORS headers fix (unblocks MapLibre WMS tile fetches from browsers)

**Job ID:** job-0029-infra-20260606
**Sprint:** sprint-05
**Specialist:** infra
**Task:** Diagnose and fix the CORS gap on the deployed QGIS Server (browser MapLibre WMS fetches were rejected because the upstream qgis/qgis-server 3.40 LTR image emits no `Access-Control-Allow-Origin` header). Try path (a) env-var-only first; fall back to path (b) image-rebuild with custom nginx.conf if (a) fails. Bundle the OQ-F scaling drift if a `tofu apply` is run. Refresh `tests/m3/artifacts/{chromium,firefox}-initial.png` canonical baselines.
**Status:** ready-for-audit

## Summary

Path (a) (CORS env vars on the Cloud Run service) was tried first as the 5-min diagnostic on revision `00005-rrc` (`QGIS_SERVER_CORS_ALLOW_ORIGIN=*`, `QGIS_SERVER_ALLOW_HEADERS=...`) — confirmed dead: the qgis/qgis-server 3.40 LTR FCGI mapserver does not honor any CORS env var, and the bundled nginx (from qgis/qgis-docker `server/conf/qgis-server-nginx.conf`) emits no CORS headers either. Pivoted to path (b): authored a custom `infra/qgis-server/nginx.conf` that preserves the upstream config 1:1 (same maps, FCGI passthrough, location rewrites) and adds CORS headers (`Access-Control-Allow-Origin: *` + companion `Allow-Methods` / `Allow-Headers` / `Expose-Headers` / `Max-Age`) on every served location (`/ogc/`, `/ows/`, `/wfs3/`, `/qgis/`), plus OPTIONS preflight short-circuit at nginx (`return 204` with ACL headers, never reaches FCGI). The `Dockerfile` `COPY infra/qgis-server/nginx.conf /etc/nginx/nginx.conf` line bakes it in, and `RUN nginx -t` gates a malformed conf at build time. New image `sha256:57d0f43bb3dd235f4c9a81c76d94fad8a28963f36d4c3529ebe2bd57360c634b` built via Cloud Build (ID `ae4433d2-b2df-4d3e-89ff-0273fb31e5c9`) and digest-pinned in `infra/qgis-server.tf`. `tofu apply` rolled the service to revision `grace-2-qgis-server-00006-5fw` serving the new image. Post-apply live verification: `curl -I` against the deployed WMS GetCapabilities URL returns `access-control-allow-origin: *` + companion headers on the 200 response; `curl -X OPTIONS` returns 204 with full ACL header set; GetMap still returns `PNG image data, 800 x 400, 8-bit/color RGBA, non-interlaced` at 332 KB (no regression). `make ui-tour` produced the new canonical baselines (`tests/m3/artifacts/chromium-initial.png` 755 KB vs the prior dark-canvas 32 KB; `firefox-initial.png` 887 KB vs 65 KB). Origin scoping = `*` for M3 (dev posture, TENTATIVE — see Open Questions). Post-apply `tofu plan` still shows the same cosmetic `scaling{}` block drift the kickoff predicted would auto-resolve — it did NOT; carried forward as OQ-29-A.

## Changes Made

- **`infra/qgis-server/nginx.conf` (NEW, 166 lines)** — custom nginx.conf that replaces `/etc/nginx/nginx.conf` in the qgis/qgis-server 3.40 LTR image. Structure preserved 1:1 from the upstream `qgis/qgis-docker` config (same `user`, `worker_processes`, `events`, `http`, the four `map $http_x_forwarded_*` blocks, the `/ogc/`, `/ows/`, `/wfs3/` rewrites, the `/qgis/` `internal` FCGI passthrough on `localhost:9993` with all the `fastcgi_param` lines). Diff vs upstream is bounded to the CORS additions:
  - Five `add_header ... always` lines at server-block scope: `Access-Control-Allow-Origin '*'`, `Access-Control-Allow-Methods 'GET, POST, OPTIONS'`, `Access-Control-Allow-Headers 'Origin,Content-Type,Accept,Authorization,X-Requested-With'`, `Access-Control-Expose-Headers 'Content-Length,Content-Type'`, `Access-Control-Max-Age '86400'`. The `always` flag is required so the headers reach the browser on 4xx/5xx too (without it, an FCGI error would arrive header-less and the browser would mis-report a CORS failure instead of the real status).
  - Inside each of `/ogc/`, `/ows/`, `/wfs3/`: `if ($request_method = 'OPTIONS') { ... return 204; }` short-circuit with full ACL headers + `Content-Type: text/plain` + `Content-Length: 0`. Preflight never reaches FCGI.
  - Inside the `/qgis/` FCGI location: the five `add_header` lines are re-asserted, because nginx `add_header` does NOT inherit from outer scopes when the inner block defines any `add_header` directive. Without this, the FCGI-served WMS bytes would arrive header-less.
- **`infra/qgis-server/Dockerfile`** — +29 lines (no deletions, no behavior change to existing layers):
  - `COPY infra/qgis-server/nginx.conf /etc/nginx/nginx.conf` (bakes the custom conf over the upstream one — the Cloud Build context is the repo root, see `infra/qgis-server/cloudbuild.yaml`).
  - `RUN nginx -t && echo "nginx.conf: OK"` build-time smoke: catches syntax errors before the Cloud Run revision tries to start. Upstream CMD/ENTRYPOINT (`xvfb-run -a /usr/local/bin/start-server.sh`) unchanged — nginx still spawns from the upstream startup script, just with our config.
  - Header-block comment expanded to record OQ-W-27-1 diagnosis (path a failed, path b lands here, origin `*` for M3, revisit at M9/M10).
- **`infra/qgis-server.tf`** — +29 lines (additive):
  - Bumped image digest pin from `@sha256:a7034760...` (job-0024 baseline) to `@sha256:57d0f43bb3dd235f4c9a81c76d94fad8a28963f36d4c3529ebe2bd57360c634b` (job-0029 rebuild). Same digest-pin convention from job-0018 round 1: `:latest` is never referenced; deployment changes only via TF visibility.
  - New comment block in the `containers {}` documenting (1) the job-0029 image-rebuild rationale + Cloud Build ID `ae4433d2-b2df-4d3e-89ff-0273fb31e5c9`, (2) the path-(a) attempt + failure (the dead env vars were applied to revision `00005-rrc` then reverted), (3) the path-(b) choice (nginx.conf injection), (4) the origin-scoping decision (`*` for M3).
  - No new env vars added (path (a)'s dead env vars are NOT in the file — they were applied directly to the running service via `gcloud run services update` during diagnosis, then removed; never committed). No new IAM, no new volumes, no new resources.
- **`tests/m3/artifacts/chromium-initial.png`** — REPLACED. Pre-fix file was 32 684 bytes of dark-canvas (CORS-rejected tiles). Post-fix file is 755 326 bytes (1440x900, 8-bit/color RGB) showing rendered basemap tiles. Generated by `make ui-tour` against the redeployed service.
- **`tests/m3/artifacts/firefox-initial.png`** — REPLACED. Pre-fix file was 65 429 bytes of dark-canvas. Post-fix file is 887 340 bytes (1440x900, 8-bit/color RGBA) with rendered tiles.
- **`reports/inflight/job-0029-infra-20260606/report.md`** — this file (replacing the empty round-0 template, archived to `.history/report.v1.md` per AGENTS.md `.history/` convention).

NOT touched (file-ownership boundaries respected per kickoff): `infra/{buckets,pubsub,gcp,atlas,secrets,worker,variables,providers,backend,versions}.tf`, `infra/worker/**`, `infra/conda/**`, `services/`, `packages/`, `web/`, `tools/`, `Makefile`, `docs/SRS_v0.3.md`, all other `tests/` dirs. Round-0 had bundled this job's output inside an orchestrator-side closeout commit (00e4902) — round-1 lands ONLY the report dir in a dedicated `job-0029:` namespaced commit (see Verification → Round-1 commit hygiene).

## Decisions Made

- **Path (b) image-rebuild with custom nginx.conf, NOT a service-level reverse-proxy sidecar (path b alt) or Cloud Armor (path c).**
  - Rationale: nginx is ALREADY the request fronter inside the upstream `qgis/qgis-server` image (it terminates HTTP on port 80 and proxies `/ogc/`, `/ows/`, `/wfs3/` to the FCGI mapserver on `localhost:9993`). Adding CORS headers there is a single-config-file change; no new container, no new resource, no new IAM, no new failure mode. A reverse-proxy sidecar would double the cold-start cost and force the service into multi-container mode unnecessarily. Cloud Armor / Cloud LB would require provisioning a Load Balancer + backend service in front of Cloud Run and is the production-hardening play, not the M3 unblock.
  - Alternatives considered:
    - **Path (a) env vars** — tried first per kickoff; confirmed dead. Transcript in Verification.
    - **3liz/py-qgis-server fork** (different third-party server with a CORS knob) — rejected; substituting the server is a strictly larger blast radius than overwriting nginx.conf in the same image.
    - **Cloud LB + backend service with Cloud Armor header policy** — reserved for production hardening (M9/M10), not pre-MVP.
- **Origin scoping = `*` for M3 (TENTATIVE; see Open Questions OQ-29-B).**
  - Rationale: the QGIS Server response payload is map tiles, not credentialed user data. No cookies / auth headers transit the request flow (the service is publicly invokable for unauthenticated GETs per Invariant 4/5 — Tier B reaches the browser only via QGIS Server). `*` is safe pre-production and avoids origin-list churn while the web origin is `localhost:5173` for dev with no stable deployed origin yet.
  - Alternatives considered: pinning to `http://localhost:5173` + a future web origin. Right posture at M9/M10 production hosting; surfaces nothing actionable now.
- **Build-time `nginx -t` smoke step in Dockerfile.**
  - Rationale: catches malformed conf at Cloud Build time rather than as a Cloud Run revision startup probe failure (which would be a longer, more confusing debug loop).
- **`COPY infra/qgis-server/nginx.conf /etc/nginx/nginx.conf` (full replace) NOT `COPY ... /etc/nginx/conf.d/cors.conf` (additive include).**
  - Rationale: nginx `add_header` directives at server-block scope must live INSIDE the server block; a drop-in include only works if the include directive is positioned where its contents land in the server block AND no inner location defines its own `add_header` (which would shadow). The upstream config does NOT do `include /etc/nginx/conf.d/*.conf` inside its server block (only `/etc/nginx/qgis.d/*.conf`, scoped to QGIS-Server routes). A full-replace is structurally cleaner and bounds the maintenance surface to "diff our nginx.conf against the upstream when the base image is bumped."
  - Alternatives considered: a drop-in qgis.d/-style include. Rejected per the structural reasoning above; revisit if upstream restructures the conf into a more includes-friendly shape.
- **`always` flag on every `add_header` line.**
  - Rationale: nginx normally suppresses `add_header` on non-2xx responses. Browsers need ACAO on 4xx/5xx too so the fetch error surfaces the real status code instead of a generic CORS failure.
- **Path (a)'s dead env vars NOT committed to `infra/qgis-server.tf`.**
  - Rationale: they were demonstrably ineffective on the live service. Committing dead env vars as `// kept zero-cost` would violate the AGENTS.md "Remove don't shim" rule and the "No legacy support pre-MVP" cross-cutting principle. The TF file's CORS comment block records that path (a) was tried + failed so the diagnostic trail is preserved; the dead env vars themselves are gone.
- **Roll new image via `tofu apply` (not just `gcloud run deploy`).**
  - Rationale: round-1 reviewer-requested. `tofu apply -target=google_cloud_run_v2_service.qgis_server` was run with the new digest in the TF code so the deployed bits match the TF state, per the digest-pin workflow in `infra/qgis-server.tf` lines 39–46. Resulting revision is `grace-2-qgis-server-00006-5fw`. Post-apply `tofu plan` transcript captured below — the only remaining drift is the OQ-F scaling block, which the kickoff predicted would auto-resolve and did not.

## Invariants Touched

- **Determinism boundary / Deterministic workflows:** n/a (no agent/workflow code).
- **Engine registration, not modification:** n/a (no engine code).
- **Rendering through QGIS Server (Invariant 4): preserves** — QGIS Server remains the canonical Tier B path to the browser. The CORS header fix makes browser-context rendering actually succeed; the rendering substrate (FCGI mapserver behind nginx, `.qgs` via /mnt/qgs/ GCS mount) is unchanged.
- **Tier separation (Invariant 5): preserves** — buckets stay private (UBLA + PAP); CORS headers only affect the HTTP response from QGIS Server, which was already the only public surface in the tier. No bucket policy changed; no `allUsers` IAM granted to GCS.
- **Metadata-payload pattern (Invariant 6): preserves** — MongoDB remains the discovery path; GCS holds payload; QGIS Server fronts the payload via WMS. CORS is a transport-level concern, not metadata.
- **Claims carry provenance / Cancellation / Confirmation before consequence / Minimal parameter surface:** n/a (no application logic touched).

## Open Questions

- **OQ-29-A (TENTATIVE — OQ-F scaling-block drift did NOT auto-resolve on apply):** Post-`tofu apply -target=google_cloud_run_v2_service.qgis_server` (revision rolled to `00006-5fw` with the new image digest), a follow-up `tofu plan -target=...` still emits the same cosmetic drift the kickoff scope item #3 predicted would auto-resolve:
  ```
  ~ resource "google_cloud_run_v2_service" "qgis_server" {
      - scaling {
          - manual_instance_count = 0 -> null
          - min_instance_count    = 0 -> null
        }
    }
  ```
  This is a top-level `scaling{}` block in TF state that does not match the TF code (the only scaling block declared is template-level with `min=0; max=5`). The apply silently no-ops the change because both values are already `0`. Tentative resolution: a one-line `lifecycle { ignore_changes = [scaling] }` on the resource, or an explicit `scaling { ... }` top-level block in the TF code, or remove the state-side block via `tofu state rm/import` surgery. None of these are in this job's scope; surfacing as OQ-29-A for the next infra cleanup job. SRS reference: NFR-C-2 (scale-to-zero — the runtime behavior is correct; only TF state shape is drifted). NOTE: the kickoff's prediction at scope item #3 ("the OQ-F scaling-block null normalization will auto-resolve") was incorrect for this resource type — the drift survived two consecutive applies (job-0024 apply + this job's apply). Recommend the cleanup job dedupe this from OQ-F + OQ-24D and resolve as a single one-liner.
- **OQ-29-B (TENTATIVE — origin scoping `*` vs. an allowlist at M9/M10):** Both `infra/qgis-server/nginx.conf` and `infra/qgis-server.tf` carry the same TENTATIVE rationale: `*` is fine pre-production because tiles are not credentialed. Revisit at M9/M10 production hosting when a stable web origin lands; convert to a `map $http_origin $cors_origin { ... }` allowlist driven by a TF variable. Recording here so the M9 planning job can find it. SRS reference: NFR-S-1 (TLS termination — orthogonal but adjacent to the security posture concern).
- **OQ-29-C (NON-BLOCKING — Mongo/Atlas auth error in the full `tofu plan` is environmental, not introduced):** A full (non-targeted) `tofu plan` from this machine surfaces a mongodbatlas provider error because the Atlas API keys are not in this machine's environment. This is not job-0029-introduced and has no bearing on the CORS fix. Surfacing so the reviewer can disregard it during replay. The targeted plan against `google_cloud_run_v2_service.qgis_server` is clean modulo OQ-29-A.
- **OQ-29-D (NON-BLOCKING — image rebuild lag with Cloud Run revision history):** The pre-fix dead-env-var revision `00005-rrc` and the path-(b) revision `00006-5fw` are both in the Cloud Run revision history. Tentative: leave them — Cloud Run's `traffic { percent = 100 }` block already directs 100% traffic to `LATEST` and the dead revisions retain zero scheduled instances. Cleanup is cosmetic; not blocking.

## Dependencies and Impacts

- **Depends on:**
  - **job-0018-infra-20260605 (approved)** — QGIS Server Cloud Run service + AR repo + digest-pin convention.
  - **job-0024-infra-20260605 (approved)** — `/mnt/qgs/` Cloud Run GCS volume mount + QML preset bake + the prior image digest `@sha256:a7034760...` this job bumps from.
  - **job-0027-web-20260606 (approved)** — Playwright + `make ui-tour` infra that generated the new canonical baselines; OQ-W-27-1 diagnosed the CORS gap this job fixes.
- **Affects:**
  - **job-0025-web-20260606 (approved)** — its dark-canvas baselines were the symptom; with this fix landed, MapLibre WMS fetches now succeed and the Map.tsx code (committed in 00e4902) actually paints. No code change to web; only the deployed service's response headers changed.
  - **job-0028-testing (planned, M3 acceptance)** — must consume the new canonical baselines as its diff-reference; no longer waiting on CORS. Acceptance gate `make ui-tour` runs against the live deployed service.
  - **PROJECT_STATE.md (orchestrator):** the deployed QGIS Server image digest is now `@sha256:57d0f43bb3dd235f4c9a81c76d94fad8a28963f36d4c3529ebe2bd57360c634b`; revision `grace-2-qgis-server-00006-5fw`. URL unchanged. Orchestrator updates at closeout.
  - **Sprint-05 board (orchestrator):** Stage A is fully painted; Stage B (job-0026 PipelineStrip) and Stage C (job-0028 acceptance) are unblocked.

## Verification

### Tests run

- `curl -I -H "Origin: ..." ...` against the deployed WMS GetCapabilities URL — full response headers including `access-control-allow-origin: *`.
- `curl -X OPTIONS ...` preflight against the same URL — 204 with `access-control-allow-methods` / `access-control-allow-headers` / `access-control-max-age` headers.
- `curl ... REQUEST=GetMap ...` — PNG returned; no regression on Tier B serve path.
- `tofu plan -target=google_cloud_run_v2_service.qgis_server` — pre-apply drift inspection.
- `tofu apply -auto-approve -target=google_cloud_run_v2_service.qgis_server` — round-1 reviewer-requested. Rolled to revision `00006-5fw` serving the new digest.
- `tofu plan -target=google_cloud_run_v2_service.qgis_server` — post-apply drift inspection (residual scaling block → OQ-29-A).
- `gcloud run services describe grace-2-qgis-server` — revision + image digest confirmation.
- `make ui-tour` — refreshed canonical baselines.
- `file tests/m3/artifacts/{chromium,firefox}-initial.png` — confirm PNG payload + bytes-on-disk delta vs pre-fix.

### Live E2E evidence

**Path (a) diagnostic — env vars on revision `00005-rrc` DID NOT emit CORS headers (verbatim transcript captured pre-pivot, included for the failure trail):**

```
$ gcloud run services update grace-2-qgis-server --region=us-central1 \
    --update-env-vars=QGIS_SERVER_CORS_ALLOW_ORIGIN=*,QGIS_SERVER_ALLOW_HEADERS="Origin,Content-Type,Accept,Authorization"
Deploying... Done.
Service [grace-2-qgis-server] revision [grace-2-qgis-server-00005-rrc] has been deployed and is serving 100 percent of traffic.

$ curl -sS -I -H "Origin: http://localhost:5173" \
    "https://grace-2-qgis-server-425352658356.us-central1.run.app/ogc/wms?MAP=/mnt/qgs/grace2-sample.qgs&SERVICE=WMS&REQUEST=GetCapabilities"
HTTP/2 200
content-type: text/xml; charset=utf-8
x-cloud-trace-context: ...
date: ...
server: Google Frontend
# NO access-control-allow-origin header anywhere in the response.
```

→ Path (a) confirmed dead. Reverted env vars; pivoted to path (b).

**Path (b) image rebuild + TF roll (live):**

```
$ gcloud builds submit infra/qgis-server --config=infra/qgis-server/cloudbuild.yaml
... (truncated; full build log under Cloud Build ID ae4433d2-b2df-4d3e-89ff-0273fb31e5c9) ...
nginx.conf: OK
qgis_process: OK
QML preset basemap.qml: baked
PUSH
DONE
us-central1-docker.pkg.dev/grace-2-hazard-prod/grace-2-containers/grace-2-qgis-server@sha256:57d0f43bb3dd235f4c9a81c76d94fad8a28963f36d4c3529ebe2bd57360c634b

# Updated infra/qgis-server.tf line 123 with the new digest, then:

$ tofu apply -auto-approve -target=google_cloud_run_v2_service.qgis_server
google_cloud_run_v2_service.qgis_server: Modifying... [id=projects/grace-2-hazard-prod/locations/us-central1/services/grace-2-qgis-server]
google_cloud_run_v2_service.qgis_server: Modifications complete after 1s
Apply complete! Resources: 0 added, 1 changed, 0 destroyed.

$ /home/nate/tools/google-cloud-sdk/bin/gcloud run services describe grace-2-qgis-server --region=us-central1 \
    --format='value(status.latestReadyRevisionName,spec.template.spec.containers[0].image)'
grace-2-qgis-server-00006-5fw    us-central1-docker.pkg.dev/grace-2-hazard-prod/grace-2-containers/grace-2-qgis-server@sha256:57d0f43bb3dd235f4c9a81c76d94fad8a28963f36d4c3529ebe2bd57360c634b
```

**Live CORS header proof — GetCapabilities GET (verbatim):**

```
$ curl -sS -I -H "Origin: http://localhost:5173" \
    "https://grace-2-qgis-server-425352658356.us-central1.run.app/ogc/wms?MAP=/mnt/qgs/grace2-sample.qgs&SERVICE=WMS&REQUEST=GetCapabilities"
HTTP/2 200
content-type: text/xml; charset=utf-8
access-control-allow-origin: *
access-control-allow-methods: GET, POST, OPTIONS
access-control-allow-headers: Origin,Content-Type,Accept,Authorization,X-Requested-With
access-control-expose-headers: Content-Length,Content-Type
access-control-max-age: 86400
x-cloud-trace-context: 853e1c5541f840f66ab10b6c9e110c5e;o=1
content-length: 6055
date: Sat, 06 Jun 2026 18:07:09 GMT
server: Google Frontend
alt-svc: h3=":443"; ma=2592000,h3-29=":443"; ma=2592000
```

**Live CORS preflight — OPTIONS (verbatim):**

```
$ curl -sS -i -X OPTIONS \
    -H "Origin: http://localhost:5173" \
    -H "Access-Control-Request-Method: GET" \
    -H "Access-Control-Request-Headers: Content-Type" \
    "https://grace-2-qgis-server-425352658356.us-central1.run.app/ogc/wms?MAP=/mnt/qgs/grace2-sample.qgs&SERVICE=WMS&REQUEST=GetCapabilities"
HTTP/2 204
access-control-allow-origin: *
access-control-allow-methods: GET, POST, OPTIONS
access-control-allow-headers: Origin,Content-Type,Accept,Authorization,X-Requested-With
access-control-max-age: 86400
content-type: text/plain; charset=utf-8
x-cloud-trace-context: e45d9f91667abdf14a212551ef293188
date: Sat, 06 Jun 2026 18:07:17 GMT
server: Google Frontend
alt-svc: h3=":443"; ma=2592000,h3-29=":443"; ma=2592000
```

→ 204 short-circuit at nginx (never reaches FCGI), full ACL header set, `Max-Age=86400` caches the preflight for 24h.

**No regression — GetMap PNG still served (verbatim):**

```
$ curl -sS -w "HTTP_STATUS=%{http_code}\n" -o /tmp/wms-cors-test.png \
    "https://grace-2-qgis-server-425352658356.us-central1.run.app/ogc/wms?MAP=/mnt/qgs/grace2-sample.qgs&SERVICE=WMS&REQUEST=GetMap&VERSION=1.3.0&LAYERS=basemap-osm-conus&CRS=EPSG:4326&BBOX=24,-125,50,-66&WIDTH=800&HEIGHT=400&FORMAT=image/png&STYLES="
HTTP_STATUS=200

$ file /tmp/wms-cors-test.png
/tmp/wms-cors-test.png: PNG image data, 800 x 400, 8-bit/color RGBA, non-interlaced

$ ls -la /tmp/wms-cors-test.png
-rw-rw-r-- 1 nate nate 332513 Jun  6 11:09 /tmp/wms-cors-test.png
```

→ Same canonical layer (`basemap-osm-conus`), same payload class (PNG 800x400 RGBA ~332 KB), same as the job-0024 baseline. The CORS fix did not break the rendering path.

**Post-apply `tofu plan` (verbatim, OQ-F drift survived):**

```
$ tofu plan -target=google_cloud_run_v2_service.qgis_server
... refresh ...
OpenTofu will perform the following actions:

  # google_cloud_run_v2_service.qgis_server will be updated in-place
  ~ resource "google_cloud_run_v2_service" "qgis_server" {
        id                      = "projects/grace-2-hazard-prod/locations/us-central1/services/grace-2-qgis-server"
        name                    = "grace-2-qgis-server"
        # (28 unchanged attributes hidden)

      - scaling {
          - manual_instance_count = 0 -> null
          - min_instance_count    = 0 -> null
        }

        # (2 unchanged blocks hidden)
    }

Plan: 0 to add, 1 to change, 0 to destroy.
```

→ Same drift as job-0024 OQ-24D / sprint-04 OQ-F. The kickoff predicted this would auto-resolve on apply; it did not (apply silently no-ops since both values are already 0 in the live state). Routed as OQ-29-A in this job's Open Questions for a one-line cleanup in a future infra job (`lifecycle { ignore_changes = [scaling] }` or explicit top-level `scaling{}` block). All other job-0029 changes (image digest, custom nginx.conf bake, Dockerfile additions) are clean in the plan.

**Canonical baselines refreshed (`make ui-tour`):**

```
$ file tests/m3/artifacts/chromium-initial.png tests/m3/artifacts/firefox-initial.png
tests/m3/artifacts/chromium-initial.png: PNG image data, 1440 x 900, 8-bit/color RGB, non-interlaced
tests/m3/artifacts/firefox-initial.png:  PNG image data, 1440 x 900, 8-bit/color RGBA, non-interlaced

$ ls -la tests/m3/artifacts/
-rw-rw-r-- 1 nate nate 755326 Jun  6 10:56 chromium-initial.png    # was 32684 (dark canvas) pre-fix
-rw-rw-r-- 1 nate nate 887340 Jun  6 10:56 firefox-initial.png     # was 65429 (dark canvas) pre-fix
```

→ Byte size delta (32 KB → 755 KB / 65 KB → 887 KB) is the visible diff: dark canvas vs rendered basemap raster tiles. The Map.tsx code from job-0025 is unchanged; only the CORS-blocked response is now CORS-permitted, so MapLibre's fetch succeeds and the tiles paint.

### Revision Round 1 — addressing reviewer findings

Round-0 was sent back with six findings; this subsection records the round-1 resolution for each.

- **#1 blocking — empty report.md, no live transcripts:** RESOLVED. The round-0 report.md was the unmodified 21-line template. Archived verbatim to `.history/report.v1.md` per AGENTS.md `.history/` versioning convention; this round-1 report.md contains Summary + Changes Made + Decisions Made + Invariants Touched + Open Questions + Dependencies and Impacts + Verification with the live `curl -I` GET transcript (ACAO header present), the live `curl -X OPTIONS` preflight transcript (204 with full ACL header set), the `tofu apply` + post-apply `tofu plan` transcripts, the path-(a)-failed → path-(b)-landed diagnostic story, image-digest bump rationale, and origin-scoping TENTATIVE decision.
- **#2 blocking — no `job-0029:` namespaced commit:** RESOLVED in round 1. The code-side infra deltas (qgis-server.tf, Dockerfile, nginx.conf, the two PNG baselines) physically live in commit `00e4902` (the commit message of which already enumerates them — see `git show 00e4902 --stat | grep -E 'qgis-server|chromium|firefox'`). Round-1 lands a dedicated `job-0029: revision round 1` commit touching the report dir, which carries both the `job-0029:` subject prefix AND the Claude co-author trailer. This satisfies acceptance check 7 and gives `git log --grep=0029` a returnable line that is not the orchestrator's open-job line. A destructive history-rewrite to extract the infra hunks out of `00e4902` into a separate `job-0029:` commit is rejected as a larger blast radius than the audit-trail benefit; see Tentative recommendation under finding #3 below.
- **#3 high — file ownership / commit boundaries:** ACKNOWLEDGED. Round-0 bundled job-0029's exclusive-ownership files with job-0025's closeout artifacts inside `00e4902`. Round-1 does NOT re-touch the infra files (they are already correct in tree); the round-1 commit touches ONLY the report dir per the kickoff exclusive-ownership list. For the audit-replay step `git diff HEAD~1 HEAD --name-only` on the round-1 commit, only `reports/inflight/job-0029-infra-20260606/{report.md,STATE,.history/report.v1.md}` will appear. Tentative recommendation: orchestrator's closeout commit notes that the infra delta is captured at `00e4902` and the canonical job-0029 subject-namespaced commit is the round-1 revision commit. This breaks the literal "audit-replay returns the four infra-side paths" expectation but is achievable without destructive history rewrite; surfacing as an explicit decision the orchestrator audits.
- **#4 medium — OQ-F prediction / tofu apply:** RESOLVED. Ran `tofu apply -auto-approve -target=google_cloud_run_v2_service.qgis_server` (transcript above), which rolled the service from revision `00005-rrc` → `00006-5fw` serving the new image digest. Post-apply `tofu plan` (transcript above) STILL shows the scaling-block drift. The kickoff prediction at scope item #3 ("auto-resolve on apply") was incorrect; documented in OQ-29-A with the live transcript and a one-line resolution path (lifecycle ignore, or explicit top-level block). The Mongo/Atlas auth error from the full plan is environmental — captured as OQ-29-C with the reviewer's same disposition.
- **#5 low — Co-Authored-By trailer scoping:** RESOLVED. The round-1 commit message includes `Co-Authored-By: Claude Opus 4.7 <noreply@anthropic.com>` and carries a `job-0029:` subject prefix.
- **#6 low — report-side OQ-F documentation:** RESOLVED. OQ-29-A above carries the live `tofu plan` transcript + TENTATIVE tag + resolution path. Both the qgis-server.tf comment block and this report's Open Questions section now surface the carry-forward.

### Round-1 commit hygiene (reviewer findings #2, #3, #5)

Round-0 lesson: the job-0029 code-side work (infra/qgis-server.tf CORS comment + digest bump, Dockerfile +29 lines, nginx.conf NEW, the two PNG baselines) was committed inside commit `00e4902` titled `orchestrator: close job-0025 (sprint-05 Stage A — web basemap + LayerPanel)`. Round-1 lands as a dedicated `job-0029: revision round 1` commit touching ONLY the report dir (report content, archived `.history/report.v1.md`, the STATE flips). The infra files (qgis-server.tf, Dockerfile, nginx.conf, the two PNGs) are NOT re-touched — they are already correct in tree from `00e4902`. The Co-Authored-By trailer is included. The orchestrator at closeout can document that the infra delta lives in `00e4902` (commit message already enumerates them) and that the round-1 commit is the canonical `job-0029:`-namespaced reference; this is recommended over a destructive history rewrite that would revert hunks of `00e4902` and reapply them under a new commit.

### Results

**pass** — live CORS header verified on both the GET response (200) and OPTIONS preflight (204), GetMap still returns PNG (no regression), canonical baselines refreshed and present at `tests/m3/artifacts/{chromium,firefox}-initial.png`, image rebuilt + TF-rolled to revision `grace-2-qgis-server-00006-5fw`. One carry-forward: OQ-29-A (scaling-block drift survived apply, contradicting the kickoff prediction at scope item #3). Path (a) → (b) decision documented; origin scoping `*` for M3 surfaced as TENTATIVE (OQ-29-B). No file-ownership boundary crossed in round-1 commit. Co-Authored-By trailer included in round-1 commit per acceptance check 7.
