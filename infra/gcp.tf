# gcp.tf — GCP project bootstrap (project, APIs, GCS, service account).
#
# Live state alignment:
#   - The project was created via `gcloud projects create` in job-0014 and
#     IS THEN IMPORTED into this configuration. No `tofu apply` ever creates
#     the project itself.
#   - The billing-link is also a manual gcloud step; `google_billing_account`
#     here is data-only.
#
# Labels (NFR-C-1 idle-cost breakdown):
#   - project = grace-2
#   - env     = var.env  (dev | prod)
#   - sprint  = var.sprint
# Every resource gets these three labels so the cost report is mechanical.

locals {
  common_labels = {
    project = "grace-2"
    env     = var.env
    sprint  = var.sprint
  }

  enabled_apis = [
    "cloudresourcemanager.googleapis.com",
    "serviceusage.googleapis.com",
    "iam.googleapis.com",
    "iamcredentials.googleapis.com",
    "run.googleapis.com",
    "workflows.googleapis.com",
    "storage.googleapis.com",
    "aiplatform.googleapis.com",
    "secretmanager.googleapis.com",
    "artifactregistry.googleapis.com",
    "logging.googleapis.com",
    "monitoring.googleapis.com",
    # sprint-04 / job-0018 additions:
    "pubsub.googleapis.com",     # FR-QS-6 step 5 worker-events topic.
    "cloudbuild.googleapis.com", # Cloud Build path for QGIS Server image
    # (avoids local docker sudo on dev box).
    # sprint-13.5 / job-0250 additions (production auth substrate):
    # Identity Platform (Firebase Auth backend) — sign-in providers + ID tokens.
    "identitytoolkit.googleapis.com",
    # Firebase management surface (project enrollment / web-app registration are
    # console/google-beta — see infra/firebase/README.md).
    "firebase.googleapis.com",
    # Native Firestore (custom-claims / tier store; SRS §F.1 tier gating).
    "firestore.googleapis.com",
    # Firestore security-rules ruleset + release.
    "firebaserules.googleapis.com",
  ]
}

# --- Project resource (imported; never created by tofu) ------------------

resource "google_project" "grace2" {
  project_id      = var.gcp_project_id
  name            = "GRACE-2 Hazard"
  billing_account = var.gcp_billing_account

  labels = local.common_labels

  # Prevent accidental destroy; the project is the root of everything.
  lifecycle {
    prevent_destroy = true
  }
}

# --- APIs ----------------------------------------------------------------

resource "google_project_service" "enabled" {
  for_each = toset(local.enabled_apis)

  project = google_project.grace2.project_id
  service = each.value

  # Don't disable on destroy (keeps the project usable if tofu state is lost).
  disable_on_destroy = false
}

# --- Service account (minimal; expanded as Cloud Run etc. land) ----------

resource "google_service_account" "agent_runtime" {
  project      = google_project.grace2.project_id
  account_id   = "agent-runtime"
  display_name = "GRACE-2 agent service runtime"
  description  = "Cloud Run identity for the agent service. Roles bound as deploys land in later jobs (Workload Identity Federation comes when CI lands)."

  depends_on = [google_project_service.enabled]
}

# Grant the runtime SA permission to read secrets it needs. The specific
# secret bindings come per-secret (see secrets.tf) — this is the
# project-level role-binding scaffold the agent service will use.
resource "google_project_iam_member" "agent_runtime_secret_accessor" {
  project = google_project.grace2.project_id
  role    = "roles/secretmanager.secretAccessor"
  member  = "serviceAccount:${google_service_account.agent_runtime.email}"
}

# --- Artifact bucket (general use; per-component buckets land later) -----

resource "google_storage_bucket" "artifacts" {
  project  = google_project.grace2.project_id
  name     = "${google_project.grace2.project_id}-artifacts"
  location = var.gcp_region

  uniform_bucket_level_access = true
  public_access_prevention    = "enforced"

  versioning {
    enabled = true
  }

  # Lifecycle: noncurrent versions older than 90d are deleted.
  lifecycle_rule {
    action {
      type = "Delete"
    }
    condition {
      days_since_noncurrent_time = 90
      with_state                 = "ARCHIVED"
    }
  }

  labels = local.common_labels

  depends_on = [google_project_service.enabled]
}
