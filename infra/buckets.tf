# buckets.tf — GCS buckets for canonical .qgs / COG / FGB payloads.
#
# Three buckets, each holding a distinct payload class (FR-MP-3 source-of-truth):
#   - grace-2-hazard-prod-qgs:  canonical .qgs project files (FR-MP-1, FR-QS-2)
#   - grace-2-hazard-prod-cog:  raster outputs as Cloud-Optimized GeoTIFF (FR-QS-3)
#   - grace-2-hazard-prod-fgb:  vector outputs as FlatGeobuf       (FR-QS-3)
#
# Invariant compliance:
#   - Invariant 5 (Tier separation): public-access-prevention = enforced,
#     uniform BLA, no public IAM. Client never reads these directly; QGIS
#     Server reads on the client's behalf. The agent serves GeoJSON; vector
#     payloads in -fgb are read by QGIS Server / worker jobs, not the browser.
#   - Invariant 6 (Metadata-payload pattern): MongoDB holds discovery indices;
#     no bucket enumeration is exposed. The QGIS Server SA has objectViewer
#     (GET by key) — NOT objectAdmin or storage.admin (which would allow LIST
#     / discovery via the bucket).
#   - NFR-S-5 (no public buckets except shared snapshot assets — none here).
#
# Labels (NFR-C-1 idle-cost breakdown) include `sprint=04` and `component=qgis-server`
# to itemize the M2 substrate cost separately from the M1 artifact bucket.
# (The M1 artifact bucket stays at `sprint=03` — that's correct historical
# attribution.)
#
# Lifecycle: 90-day noncurrent cleanup (parallel to artifact bucket). No
# archival tiering yet — payload corpus is too small to warrant; revisit at
# M9/M10 when production data lands.

locals {
  qgis_server_bucket_labels = merge(local.common_labels, {
    component = "qgis-server"
    # Override the global `sprint` for this M2 substrate. The artifact bucket
    # in gcp.tf keeps its `sprint=03` label (correct historical attribution).
    sprint = "04"
  })
}

# --- .qgs canonical bucket -----------------------------------------------

resource "google_storage_bucket" "qgs" {
  project  = google_project.grace2.project_id
  name     = "${google_project.grace2.project_id}-qgs"
  location = var.gcp_region

  uniform_bucket_level_access = true
  public_access_prevention    = "enforced"

  versioning {
    enabled = true
  }

  lifecycle_rule {
    action {
      type = "Delete"
    }
    condition {
      days_since_noncurrent_time = 90
      with_state                 = "ARCHIVED"
    }
  }

  labels = local.qgis_server_bucket_labels

  depends_on = [google_project_service.enabled]
}

# --- COG raster output bucket --------------------------------------------

resource "google_storage_bucket" "cog" {
  project  = google_project.grace2.project_id
  name     = "${google_project.grace2.project_id}-cog"
  location = var.gcp_region

  uniform_bucket_level_access = true
  public_access_prevention    = "enforced"

  versioning {
    enabled = true
  }

  lifecycle_rule {
    action {
      type = "Delete"
    }
    condition {
      days_since_noncurrent_time = 90
      with_state                 = "ARCHIVED"
    }
  }

  labels = local.qgis_server_bucket_labels

  depends_on = [google_project_service.enabled]
}

# --- FlatGeobuf vector output bucket -------------------------------------

resource "google_storage_bucket" "fgb" {
  project  = google_project.grace2.project_id
  name     = "${google_project.grace2.project_id}-fgb"
  location = var.gcp_region

  uniform_bucket_level_access = true
  public_access_prevention    = "enforced"

  versioning {
    enabled = true
  }

  lifecycle_rule {
    action {
      type = "Delete"
    }
    condition {
      days_since_noncurrent_time = 90
      with_state                 = "ARCHIVED"
    }
  }

  labels = local.qgis_server_bucket_labels

  depends_on = [google_project_service.enabled]
}

# --- IAM: QGIS Server SA gets objectViewer on all three buckets ----------
# Read-only; binds at bucket scope (not project) so the SA cannot enumerate
# or read OTHER buckets in the project (artifact bucket, state bucket).
# `objectViewer` grants storage.objects.get + storage.objects.list scoped to
# the bucket; .list on the bucket itself is needed for /vsigs/ to resolve
# wildcards, NOT for "discovery" of unknown objects (Invariant 6: the client
# never calls into this binding — only QGIS Server, which already knows the
# object key from the WMS MAP= param).

resource "google_storage_bucket_iam_member" "qgs_server_qgs_viewer" {
  bucket = google_storage_bucket.qgs.name
  role   = "roles/storage.objectViewer"
  member = "serviceAccount:${google_service_account.qgis_server.email}"
}

resource "google_storage_bucket_iam_member" "qgs_server_cog_viewer" {
  bucket = google_storage_bucket.cog.name
  role   = "roles/storage.objectViewer"
  member = "serviceAccount:${google_service_account.qgis_server.email}"
}

resource "google_storage_bucket_iam_member" "qgs_server_fgb_viewer" {
  bucket = google_storage_bucket.fgb.name
  role   = "roles/storage.objectViewer"
  member = "serviceAccount:${google_service_account.qgis_server.email}"
}

# --- Worker SA bucket binding deferred to job-0021 ---
# The PyQGIS worker SA (`grace-2-pyqgis-worker`) is created in job-0021 along
# with the worker container itself. Its bucket binding (objectAdmin on -qgs)
# lands there too — declaring it here would force-create the SA out-of-order.
# Kickoff TENTATIVE was "declare here for single source of truth"; revised:
# declare with the SA in 0021 (the SA + its binding are one atomic unit).
# This is the right cut for a clean job-0021 plan.
