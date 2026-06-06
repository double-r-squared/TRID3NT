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
#   - Public ingress (`INGRESS_TRAFFIC_ALL`): GetCapabilities + tile GET must
#     reach the browser; auth gating is the agent's contract (Tier B reaches
#     the client only via QGIS Server or agent GeoJSON).
#   - `allUsers: roles/run.invoker` binding makes the WMS public-readable.
#     This is the SRS-intended posture (Tier B served via QGIS Server,
#     Invariant 4/5). The buckets stay private; QGIS Server is the only path.
#   - Stateless and replaceable (NFR-R-4): no volumes, no sticky sessions,
#     `.qgs` lives in GCS, no per-instance disk writes.
#
# Image source-of-truth:
#   The `image` arg below references the Artifact Registry tag the Makefile
#   `qgis-server-push` target writes. The post-deploy `tofu plan` step in the
#   report verifies "No changes" — meaning the deployed image digest matches
#   what's tagged in the repo (Cloud Run resolves the tag → digest at deploy).

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

  template {
    service_account = google_service_account.qgis_server.email

    # Scale-to-zero (NFR-C-2). Bump min when NFR-P-3 (<1s p95 first-tile)
    # is gated at M3.
    scaling {
      min_instance_count = 0
      max_instance_count = 5
    }

    containers {
      # The Makefile `qgis-server-push` target writes :latest. The tag
      # resolves to a digest at deploy; the post-deploy `tofu plan` step
      # detects drift if the deployed image changes outside this config.
      image = "${var.gcp_region}-docker.pkg.dev/${google_project.grace2.project_id}/${google_artifact_registry_repository.containers.repository_id}/grace-2-qgis-server:latest"

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
        name  = "GRACE2_STYLES_DIR"
        value = "/opt/grace2/styles"
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

# --- Public-invoker binding (Tier B served via QGIS Server, Invariant 4/5) ----
# Allows unauthenticated GETs against /ogc/* — this is the SRS posture: the
# QGIS Server is the only path Tier B reaches the browser, so the WMS surface
# must be publicly reachable. The buckets behind it remain private (PAP).

resource "google_cloud_run_v2_service_iam_member" "qgis_server_public_invoker" {
  project  = google_project.grace2.project_id
  location = google_cloud_run_v2_service.qgis_server.location
  name     = google_cloud_run_v2_service.qgis_server.name
  role     = "roles/run.invoker"
  member   = "allUsers"
}
