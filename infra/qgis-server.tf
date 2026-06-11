# cloudrun.tf — QGIS Server Cloud Run service (sprint-04 / job-0018 / FR-QS-1).
#
# Provisions:
#   1. The dedicated runtime service account `grace-2-qgis-server`.
#   2. An Artifact Registry Docker repo `grace-2-containers` in us-central1 to
#      host the QGIS Server image (and future agent/worker images).
#   3. The Cloud Run v2 service `grace-2-qgis-server` running the image built
#      from infra/qgis-server/Dockerfile.
#
# IAM scoping (Invariant 5 — Tier separation; NFR-S-2 service-account-scoped):
#   - The SA gets `roles/storage.objectViewer` on the THREE specific buckets
#     (-qgs, -cog, -fgb) — NOT at project level. Bindings live in
#     infra/qgis-server/buckets.tf so all bucket-IAM is co-located.
#   - The SA is NOT granted any Pub/Sub or Mongo roles; QGIS Server reads,
#     it does not publish events.
#
# Cloud Run config:
#   - `--min-instances=0` (NFR-C-2). First-tile latency NFR-P-3 (<1s p95)
#     lands at M3 when the web client first consumes tiles; if cold start
#     starves it, this becomes `min=1` then.
#   - Request-rate autoscaling (FR-QS-1) — Cloud Run's default autoscaler.
#   - `--cpu=2 --memory=2Gi` baseline; bumped only with a latency NFR.
#   - Public ingress (`INGRESS_TRAFFIC_ALL`): the service is reachable on the
#     internet, but invocation is GATED by IAM (job-0255 invoker-only flip).
#     ingress=ALL + invoker-only is the standard Cloud Run private-service
#     posture: TCP reaches the front door, IAM rejects un-authed callers (403).
#   - Invoker binding (job-0255): roles/run.invoker is granted ONLY to the
#     agent-runtime SA — NOT allUsers. Tier B reaches the browser via the
#     agent's /qgis-proxy (which holds the invoker grant and attaches an OIDC
#     token), so a direct unauthenticated WMS GET to this URL returns 403.
#     The buckets stay private; QGIS Server is still the only render path.
#   - Stateless and replaceable (NFR-R-4): no volumes, no sticky sessions,
#     `.qgs` lives in GCS, no per-instance disk writes.
#
# Image source-of-truth:
#   The `image` arg below references the Artifact Registry image BY DIGEST,
#   not by the `:latest` tag (revision round 1 — reviewer finding: `:latest`
#   means a silent AR push would deploy without TF visibility because
#   `tofu plan` cannot detect drift between a resolved digest and a floating
#   tag). Digest-pin makes the deployed bits an explicit TF input.
#
#   Bump-on-build workflow:
#     1. `make qgis-server-build` (Cloud Build push) emits the new digest
#        on the last line of stdout, e.g.
#          us-central1-docker.pkg.dev/.../grace-2-qgis-server@sha256:<NEW>
#     2. Update the digest on the `image = ...` line below to <NEW>.
#     3. `tofu apply` rolls Cloud Run to the new revision.
#     4. `tofu plan` after must return "No changes" — proving the deployed
#        bits match what's in code.
#   This is the cleaner half of the OQ-H decision (digest-pin for prod);
#   the floating-tag alternative is documented in OQ-H of the report.

# --- Artifact Registry Docker repo ---------------------------------------
# Hosts the QGIS Server image (and forthcoming agent/worker images — repo is
# reused across containers to keep the registry surface tiny). Pinned to
# us-central1 to co-locate with Cloud Run pulls (zero network hop, zero
# data-egress).

resource "google_artifact_registry_repository" "containers" {
  project       = google_project.grace2.project_id
  location      = var.gcp_region
  repository_id = "grace-2-containers"
  format        = "DOCKER"
  description   = "GRACE-2 container images: QGIS Server, PyQGIS workers, agent (later)."

  labels = merge(local.common_labels, {
    component = "qgis-server"
  })

  depends_on = [google_project_service.enabled]
}

# --- Service account for QGIS Server -------------------------------------

resource "google_service_account" "qgis_server" {
  project      = google_project.grace2.project_id
  account_id   = "grace-2-qgis-server"
  display_name = "GRACE-2 QGIS Server runtime"
  description  = "Cloud Run identity for QGIS Server. Granted read-only access to the .qgs/COG/FGB buckets (see qgis-server/buckets.tf). No Pub/Sub or Mongo roles — server renders, it does not write or notify."

  depends_on = [google_project_service.enabled]
}

# --- Cloud Run service ---------------------------------------------------

resource "google_cloud_run_v2_service" "qgis_server" {
  project  = google_project.grace2.project_id
  name     = "grace-2-qgis-server"
  location = var.gcp_region

  # Public ingress — WMS GetCapabilities / tile GETs must reach the browser.
  ingress = "INGRESS_TRAFFIC_ALL"

  labels = merge(local.common_labels, {
    component = "qgis-server"
  })

  # Service-level scaling block (job-0073 reconciliation — Option A: codify live state).
  #
  # The Google provider ~6.x schema exposes TWO scaling blocks:
  #   1. Service-level  scaling {} — controls whole-service mode (AUTOMATIC/MANUAL).
  #      Attributes: manual_instance_count, min_instance_count, scaling_mode.
  #   2. Template-level scaling {} (inside template {}) — controls per-revision limits.
  #      Attributes: min_instance_count, max_instance_count.
  #
  # GCP API auto-fills the service-level block with min_instance_count=0 and
  # manual_instance_count=0 even for AUTOMATIC-mode services (scaling_mode omitted =
  # AUTOMATIC). Without this block in code, `tofu plan` continuously detected those
  # GCP-API-auto-filled values as drift and proposed to null them out
  # (OQ-61 / OQ-67 / OQ-69 carry-forward).
  #
  # Codifying the auto-filled values here is correct: the service IS in automatic
  # mode, min_instance_count=0 preserves NFR-C-2 scale-to-zero, and
  # manual_instance_count=0 is the GCP default for automatic-mode services.
  # No runtime behavior changes — only the IaC now matches what GCP returns.
  scaling {
    min_instance_count    = 0
    manual_instance_count = 0
  }

  template {
    service_account = google_service_account.qgis_server.email

    # Scale-to-zero (NFR-C-2). Bump min when NFR-P-3 (<1s p95 first-tile)
    # is gated at M3.
    scaling {
      min_instance_count = 0
      max_instance_count = 5
    }

    containers {
      # Digest-pinned (revision round 1, job-0018). The Makefile
      # `qgis-server-build` target pushes to the `:latest` tag in AR; the
      # last line of its output is the resolved digest. Bump the digest
      # below explicitly per the workflow described above. As of
      # 2026-06-05 the AR `:latest` tag resolves to this digest AND the
      # currently-running Cloud Run revision (grace-2-qgis-server-00001-klb)
      # is serving this digest — verified by
      # `gcloud run revisions describe ... --format=json` reading
      # `status.imageDigest`.
      # job-0024 rebuild: new image bakes /etc/qgis/styles/basemap.qml (the
      # engine-authored preset from job-0019) — verified at build time by the
      # Dockerfile's `test -f /etc/qgis/styles/basemap.qml` smoke step.
      # job-0029 rebuild: image bakes infra/qgis-server/nginx.conf over
      # /etc/nginx/nginx.conf with CORS headers on every served route +
      # OPTIONS preflight short-circuit at nginx. Cloud Build ID
      # ae4433d2-b2df-4d3e-89ff-0273fb31e5c9. Build-time `nginx -t` smoke
      # gates a malformed conf.
      image = "${var.gcp_region}-docker.pkg.dev/${google_project.grace2.project_id}/${google_artifact_registry_repository.containers.repository_id}/grace-2-qgis-server@sha256:57d0f43bb3dd235f4c9a81c76d94fad8a28963f36d4c3529ebe2bd57360c634b"

      ports {
        container_port = 80
      }

      resources {
        limits = {
          cpu    = "2"
          memory = "2Gi"
        }
        cpu_idle          = true
        startup_cpu_boost = true
      }

      env {
        name  = "QGIS_SERVER_LOG_LEVEL"
        value = "0"
      }
      env {
        name  = "QGIS_SERVER_LOG_STDERR"
        value = "true"
      }
      env {
        name  = "QGIS_SERVER_PARALLEL_RENDERING"
        value = "true"
      }
      env {
        name  = "QGIS_SERVER_MAX_THREADS"
        value = "2"
      }
      env {
        # job-0245 (OQ-0245-QGIS-PROJECT-CACHE): re-parse the gcsfuse-mounted
        # .qgs periodically so freshly published layers become visible without
        # a cold start (LayerNotDefined otherwise).
        name  = "QGIS_SERVER_PROJECT_CACHE_STRATEGY"
        value = "periodic"
      }
      env {
        name  = "QGIS_SERVER_PROJECT_CACHE_CHECK_INTERVAL"
        value = "10000"
      }
      env {
        # Canonical preset path baked at /etc/qgis/styles/ by the Dockerfile
        # (job-0024 rebuild). /opt/grace2/styles/ kept as a back-compat alias
        # in the image but the env points to the canonical path.
        name  = "GRACE2_STYLES_DIR"
        value = "/etc/qgis/styles"
      }
      env {
        name  = "GRACE2_QGS_BUCKET"
        value = google_storage_bucket.qgs.name
      }
      env {
        name  = "GRACE2_COG_BUCKET"
        value = google_storage_bucket.cog.name
      }
      env {
        name  = "GRACE2_FGB_BUCKET"
        value = google_storage_bucket.fgb.name
      }

      # --- GDAL /vsigs/ auth (job-0024 / OQ-19A) ---------------------------
      # Without these, GDAL's /vsigs/ driver does not pick up the Cloud Run
      # instance's ADC / metadata-server credentials, and `QgsProject.read()`
      # on `/vsigs/<bucket>/<path>.qgs` fails with "Unable to open …".
      #   - CPL_MACHINE_IS_GCE=YES        → forces GDAL to treat the runtime
      #                                     as a GCE-class host so it queries
      #                                     the metadata server for tokens.
      #   - CPL_GS_USE_INSTANCE_PROFILE=YES → use the attached runtime SA
      #                                     (the qgis-server SA with bucket-
      #                                     scoped objectViewer) for /vsigs/
      #                                     reads. No service-account-key
      #                                     file, no Workload Identity dance.
      #   - GDAL_HTTP_USERAGENT           → diagnosability in upstream GCS
      #                                     audit logs.
      # Decision rationale (TENTATIVE → confirmed live): env-on-service vs
      # baking into the Dockerfile. Picked env-on-service: changeable without
      # an image rebuild, visible in `gcloud run services describe`, drift
      # surfaces in `tofu plan`. Dockerfile would re-bake on every tweak.
      env {
        name  = "CPL_MACHINE_IS_GCE"
        value = "YES"
      }
      env {
        name  = "CPL_GS_USE_INSTANCE_PROFILE"
        value = "YES"
      }
      env {
        name  = "GDAL_HTTP_USERAGENT"
        value = "grace-2-qgis-server/0.1"
      }

      # --- CORS — landed via path (b) image rebuild (job-0029) -------------
      # PATH (a) (CORS env vars) was tried first as 5-min diagnostic on
      # revision 00005-rrc: env vars `QGIS_SERVER_CORS_ALLOW_ORIGIN=*` and
      # `QGIS_SERVER_ALLOW_HEADERS=Origin,Content-Type,Accept,Authorization`
      # added, applied, new revision served — `curl -I -H "Origin: ..."`
      # response STILL had no `access-control-allow-origin` header. Root
      # cause: the official `qgis/qgis-server` 3.40 LTR image's bundled
      # nginx (from qgis/qgis-docker server/conf/qgis-server-nginx.conf)
      # does NOT emit CORS headers and consults NO env var to do so; the
      # FCGI mapserver behind it also has no CORS knob. (The 3liz/
      # py-qgis-server fork has a CORS option, but that is a different
      # third-party server we are not running.) The dead env vars were
      # then reverted and PATH (b) was pursued: a custom nginx.conf is
      # baked into the image that injects CORS headers on every response
      # at every served location.
      #
      # The new image digest is pinned on the `image = ...` line below.
      # Origin scoping is `*` for M3 (dev posture): the QGIS Server response
      # payload is map tiles, not credentialed user data, no cookies/auth
      # transit, so origin-wildcard is safe. Revisit at M9/M10 production
      # hosting when a stable web-origin lands.

      # --- .qgs bucket FUSE mount (job-0024 / OQ-19A path b) ---------------
      # PATH (c) (GDAL VSI env vars above) was tried first and FAILED — QGIS
      # Server's QgsProject::read() uses Qt file APIs, not GDAL VSI, to load
      # the .qgs itself. Live evidence in report: server log line
      # `CRITICAL Server[18]: Error when loading project file '/vsigs/...':
      # Unable to open /vsigs/...`. Env vars above are kept zero-cost because
      # they DO help layer references inside the project that DO transit GDAL.
      #
      # PATH (b) — Cloud Run gen2 native GCS volume mount. The runtime mounts
      # the qgs bucket at /mnt/qgs via Cloud Run's gcsfuse plumbing, using the
      # qgis-server runtime SA (bucket-scoped roles/storage.objectViewer). No
      # gcsfuse install in the image, no startup-wrapper PID-1 gymnastics, no
      # service-account-key file. The WMS canonical URL becomes
      # MAP=/mnt/qgs/<file>.qgs (filesystem path). Per "No legacy support
      # pre-MVP", the codebase does NOT support both /vsigs/ and /mnt/ for
      # .qgs — /mnt/qgs/<file>.qgs is the canonical contract.
      volume_mounts {
        name       = "qgs-bucket"
        mount_path = "/mnt/qgs"
      }
    }

    # Volume declaration for the qgs-bucket FUSE mount referenced above.
    # `read_only = true`: QGIS Server is the renderer, not a writer. The
    # PyQGIS worker (job-0020 / job-0021 Cloud Run Job) writes back via its
    # own container with roles/storage.objectAdmin — not via this service.
    volumes {
      name = "qgs-bucket"
      gcs {
        bucket    = google_storage_bucket.qgs.name
        read_only = true
      }
    }

    timeout = "60s"
  }

  traffic {
    type    = "TRAFFIC_TARGET_ALLOCATION_TYPE_LATEST"
    percent = 100
  }

  # The image is built+pushed by `make qgis-server-build && make qgis-server-push`.
  # tofu doesn't manage the image build itself; it only references the tag.
  # Lifecycle ignore for `template[0].containers[0].image` would silence
  # legitimate digest rolls — left active so drift is visible in `tofu plan`.

  depends_on = [
    google_project_service.enabled,
    google_artifact_registry_repository.containers,
  ]
}

# --- Invoker binding: agent-runtime ONLY (job-0255, sprint-13.5 Stage 2) -------
# WAS: `allUsers` → roles/run.invoker (public WMS — the M3-era posture).
# NOW: invoker-only. The agent service's runtime SA is the SINGLE principal
# granted roles/run.invoker on QGIS Server. Tier B still reaches the browser
# only through QGIS Server (Invariant 4/5), but the hop is now
#   browser → agent /qgis-proxy (attaches OIDC) → QGIS Server
# instead of the browser hitting QGIS Server directly. The agent proxy
# (services/workers/qgis_proxy.py, mounted on the agent's :8766 HTTP listener)
# strips ALL inbound user credentials before forwarding, so QGIS Server never
# sees a user identity (no UID leak — manifest job-0255 correctness lens).
#
# Why a single binding, not allUsers + agent SA: the whole point of the flip
# is that a DIRECT unauthenticated WMS request to the QGIS Cloud Run URL now
# returns 403 (manifest correctness lens). Keeping allUsers would defeat that.
#
# SEQUENCING (loud): applying this binding flips dev rendering OFF until the
# proxy path is live end-to-end — the dev demo currently RENDERS via the
# public QGIS URL. The user must apply ONLY after the proxy is verified
# (USER_UNBLOCK 0255-A/B). `tofu apply` is a USER step (never an agent step).
#
# Other accessors inventoried (none need a grant here):
#   - The PyQGIS worker (job-0021 Cloud Run JOB) and SFINCS/MODFLOW jobs write
#     `.qgs`/layer data to GCS via their own runtime SAs; they do NOT invoke
#     the QGIS Server SERVICE (rendering ≠ writing — Invariant 4). No binding.
#   - The qgis-server runtime SA (google_service_account.qgis_server) is the
#     service's OWN identity (used for /vsigs/ + the /mnt/qgs gcsfuse mount via
#     bucket-scoped objectViewer); it is the callee, not a caller. No binding.
#   - The web client never calls QGIS Server directly post-flip — it calls the
#     agent proxy, which holds the only invoker grant. No human/SA binding.
#
# TODO(job-0257): prod agent service deploy threads QGIS_SERVER_URL +
# QGIS_PROXY_ENABLED=true so the proxy path serves prod tiles.

resource "google_cloud_run_v2_service_iam_member" "qgis_server_agent_invoker" {
  project  = google_project.grace2.project_id
  location = google_cloud_run_v2_service.qgis_server.location
  name     = google_cloud_run_v2_service.qgis_server.name
  role     = "roles/run.invoker"
  member   = "serviceAccount:${google_service_account.agent_runtime.email}"
}
