# worker.tf — PyQGIS worker Cloud Run Job (sprint-04 / job-0021 / FR-QS-6).
#
# Provisions the runtime for the canonical PyQGIS worker round-trip:
#   1. Service account `pyqgis-worker-runtime` (Cloud Run Job identity).
#   2. IAM bindings — bucket-scoped `objectAdmin` on `grace-2-hazard-prod-qgs`
#      and topic-scoped `publisher` on `grace-2-worker-events`. Both bound
#      AT THE RESOURCE LEVEL — no project-wide grants (Invariant 5 + NFR-S-2
#      + NFR-S-5 service-account scoping).
#   3. Cloud Run v2 Job `grace-2-pyqgis-worker` that mounts the -qgs bucket
#      WRITABLE at /mnt/qgs (replicates job-0024's QGIS Server mount pattern
#      but with `read_only=false` — the worker is the only sanctioned
#      `.qgs` writer, Invariant 4).
#
# Image source-of-truth (revision-r1 lesson from job-0018 carried forward):
#   the Cloud Run Job pins the container image BY DIGEST below, not by the
#   `:latest` tag, so `tofu plan` detects when a newer image has been pushed
#   without an IaC change.
#
#   Bump-on-build workflow:
#     1. `make worker-build` (Cloud Build push) emits the new digest at
#        AR. Read it via:
#          gcloud artifacts docker images list \
#            us-central1-docker.pkg.dev/grace-2-hazard-prod/grace-2-containers \
#            --include-tags | grep pyqgis-worker
#     2. Update the digest on the `image = ...` line below.
#     3. `tofu apply` rolls the Job to the new image.
#     4. `tofu plan` after must return "No changes".
#
# Scale-to-zero: Cloud Run Jobs are inherently scale-to-zero — they run
# only when `gcloud run jobs execute` is invoked. `parallelism = 1` +
# `max_retries = 0` keeps the M2 smoke pattern deterministic; M3+ may bump
# parallelism for batch sweeps.

# --- Service account ------------------------------------------------------
#
# Dedicated runtime SA for the PyQGIS worker. The SA exists ONLY for this
# Job; no other resource binds to it. No keys are minted (Cloud Run uses
# the runtime-attached identity / Workload Identity at the metadata-server
# level; no JSON keys ever leave the project).

resource "google_service_account" "pyqgis_worker" {
  project      = google_project.grace2.project_id
  account_id   = "pyqgis-worker-runtime"
  display_name = "GRACE-2 PyQGIS worker Cloud Run Job runtime"
  description  = "Cloud Run Job identity for the PyQGIS worker. objectAdmin on the -qgs bucket + pubsub.publisher on grace-2-worker-events. No project-wide roles."

  depends_on = [google_project_service.enabled]
}

# --- IAM: bucket-scoped objectAdmin on the .qgs bucket --------------------
#
# Bound at bucket scope — `objectAdmin` here grants GET / LIST / CREATE /
# UPDATE / DELETE on objects in this single bucket. Project-level roles
# (storage.admin / storage.objectAdmin) are NEVER used (NFR-S-2 + NFR-S-5 +
# Invariant 6 "no bucket enumeration"). The worker writes ONLY `.qgs` files
# under this bucket; COG/FGB writes (M5+) will require a separate binding
# when that solver-job lands.

resource "google_storage_bucket_iam_member" "pyqgis_worker_qgs_admin" {
  bucket = google_storage_bucket.qgs.name
  role   = "roles/storage.objectAdmin"
  member = "serviceAccount:${google_service_account.pyqgis_worker.email}"
}

# --- IAM: topic-scoped publisher on grace-2-worker-events ----------------
#
# Bound at topic scope (not project) — `pubsub.publisher` grants only
# `pubsub.topics.publish` on THIS topic. The worker reads no topics, does
# not create subscriptions, and is not a topic admin (no
# pubsub.admin / editor).

resource "google_pubsub_topic_iam_member" "pyqgis_worker_publisher" {
  project = google_pubsub_topic.worker_events.project
  topic   = google_pubsub_topic.worker_events.name
  role    = "roles/pubsub.publisher"
  member  = "serviceAccount:${google_service_account.pyqgis_worker.email}"
}

# --- Cloud Run v2 Job -----------------------------------------------------

resource "google_cloud_run_v2_job" "pyqgis_worker" {
  project  = google_project.grace2.project_id
  name     = "grace-2-pyqgis-worker"
  location = var.gcp_region

  labels = merge(local.common_labels, {
    component = "pyqgis-worker"
    sprint    = "04"
  })

  template {
    labels = merge(local.common_labels, {
      component = "pyqgis-worker"
      sprint    = "04"
    })

    # `parallelism=1 task_count=1 max_retries=0`: M2 is a smoke pattern,
    # not a production sweep. NFR-C-2 (scale-to-zero) is automatic for
    # Cloud Run Jobs (they exist only at execute-time). `max_retries=0`
    # surfaces failures immediately rather than masking them with a
    # silent retry.
    parallelism = 1
    task_count  = 1

    template {
      service_account = google_service_account.pyqgis_worker.email

      # 15-minute task timeout — fits the M2 round-trip (a few seconds in
      # practice) with generous headroom for cold image pull + first-time
      # PyQGIS initialization. M5+ solver jobs will need much longer; this
      # value is sized for the FR-QS-6 smoke only.
      timeout     = "900s"
      max_retries = 0

      containers {
        # Digest-pinned (job-0018 r1 discipline). Bump per the workflow
        # in the file header when `make worker-build` produces a new digest.
        # Two builds during job-0021 closeout — the first (cce28c2f...) SEGV'd
        # in the first live exec because the worker hit Qt without an X
        # display; the second (7649cfde...) adds `QT_QPA_PLATFORM=offscreen`
        # to the Dockerfile and is the one pinned below. `:latest` AR tag
        # points to the same digest.
        image = "${var.gcp_region}-docker.pkg.dev/${google_project.grace2.project_id}/${google_artifact_registry_repository.containers.repository_id}/grace-2-pyqgis-worker@sha256:fffd7e0f41aa255c80ff288e19cf950e5953e05cc79cc67524dbc9c7edbcacd9"

        resources {
          limits = {
            cpu    = "2"
            memory = "2Gi"
          }
        }

        # --- worker env -------------------------------------------------
        # `QGS_URI` + `LAYER_TO_ADD` are read by services/workers/pyqgis/__main__.py
        # as env-var fallbacks when CLI args aren't supplied — letting an
        # invocation either set them via `--args` (preferred) or pre-bake
        # them on the Job template (handy for fixed-input smoke runs).
        # Empty default keeps the IaC declarative; the actual values come
        # from `gcloud run jobs execute --args` at invocation time.
        env {
          name  = "QGS_URI"
          value = ""
        }
        env {
          name  = "LAYER_TO_ADD"
          value = ""
        }

        # GCP project + Pub/Sub topic for the completion publish.
        # services/workers/pyqgis/worker.py reads both via os.environ.
        env {
          name  = "GCP_PROJECT"
          value = google_project.grace2.project_id
        }
        env {
          name  = "GOOGLE_CLOUD_PROJECT"
          value = google_project.grace2.project_id
        }
        env {
          name  = "PUBSUB_TOPIC"
          value = google_pubsub_topic.worker_events.name
        }

        # --- GDAL /vsigs/ auth (mirrors infra/qgis-server.tf) -----------
        # Without these, GDAL's /vsigs/ driver does not pick up the Cloud
        # Run instance's metadata-server creds for layer-data references
        # inside `.qgs` projects (COG/FlatGeobuf reads). The worker
        # primarily uses google-cloud-storage for the .qgs round-trip
        # itself (because `QgsProject.read()` uses Qt I/O, not GDAL VSI —
        # see job-0020 report), but layer references inside the `.qgs`
        # DO transit GDAL.
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
          value = "grace-2-pyqgis-worker/0.1"
        }

        # --- writable .qgs bucket mount (mirror of job-0024 pattern) ----
        # job-0024 mounted the same bucket READ-ONLY for QGIS Server.
        # This Job mounts it WRITABLE — the worker IS the only sanctioned
        # `.qgs` writer (Invariant 4). The runtime SA's objectAdmin grant
        # (above) is what actually authorizes the writes; the mount
        # `read_only=false` flag just removes the runtime guard so the SA
        # capability can be exercised.
        #
        # The worker code (services/workers/pyqgis/worker.py) accepts
        # /mnt/qgs/ paths natively via the local-path branch in
        # _parse_qgs_uri (job-0020 audit confirmed). Per "No legacy
        # support pre-MVP", /mnt/qgs/ is the canonical contract; the
        # worker also has a google-cloud-storage SDK download path so the
        # mount is not strictly required, but mounting at the container
        # layer keeps the worker code's local-path branch live.
        volume_mounts {
          name       = "qgs-bucket"
          mount_path = "/mnt/qgs"
        }
      }

      volumes {
        name = "qgs-bucket"
        gcs {
          bucket    = google_storage_bucket.qgs.name
          read_only = false
        }
      }
    }
  }

  # Cloud Run Jobs don't have a `traffic` block; the only deployed surface
  # is the Job spec itself. Executions are kicked via gcloud / API / a
  # future Cloud Workflows definition.

  depends_on = [
    google_project_service.enabled,
    google_artifact_registry_repository.containers,
    google_storage_bucket.qgs,
    google_pubsub_topic.worker_events,
    google_storage_bucket_iam_member.pyqgis_worker_qgs_admin,
    google_pubsub_topic_iam_member.pyqgis_worker_publisher,
  ]
}
