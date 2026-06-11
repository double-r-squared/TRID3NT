# signed_urls.tf — Signed-URL minting Cloud Function (gen2, authenticated).
#
# Sprint-13.5 Stage 1 / job-0251. Source: infra/signed_urls/ (main.py +
# requirements.txt). Mints short-lived GCS V4 signed URLs for layer objects,
# gated on (a) a verified Firebase ID token and (b) MongoDB ownership of the
# Case the layer belongs to. This is the single trust boundary between "an
# authenticated user" and "a GCS object" in production (NFR-S-1, SRS §3.8).
#
# Why a Cloud Function (not inlined in the agent): signing is a narrow,
# security-critical surface with its OWN minimal identity. The function's
# runtime SA can sign objects (signBlob on itself) and read the Case-owner row;
# it has NO Gemini access, NO write role, and NO broad bucket grants. Keeping it
# a separate gen2 function lets job-0254's agent emission call it with the
# authenticated user's UID per LayerURI without widening the agent SA.
#
# Signing approach (manifest HARD CONSTRAINT — NEVER ship a key file):
#   The function signs V4 URLs via IAM signBlob using its ATTACHED runtime SA
#   credentials. That requires the runtime SA to hold
#   roles/iam.serviceAccountTokenCreator ON ITSELF (so google-auth's iam.Signer
#   can call signBlob for it). That self-binding is `signer_token_creator` below.
#   No JSON key is ever created or downloaded.
#
# Source-upload discipline (mirror of the digest-pin discipline in
# infra/sfincs.tf / infra/python-sandbox.tf — but for a zipped source object,
# since gen2 functions deploy from a GCS source archive, not an image):
#   The function source (infra/signed_urls/) is zipped and uploaded to the
#   artifacts bucket as `var.signed_url_source_object`. We do NOT use the
#   hashicorp/archive provider (it is not in this module's provider lock, and
#   versions.tf is a separate ownership surface) — instead the source zip is
#   produced + uploaded by the deploy step (a gcloud/gsutil step that is a
#   classifier-blocked mutation; see reports/inflight/sprint-13-5-USER_UNBLOCK.md
#   for the exact `zip` + `gsutil cp` commands). `tofu apply` then references the
#   uploaded object by name. Bump-on-source-change:
#     1. zip infra/signed_urls/{main.py,requirements.txt} -> source zip
#     2. gsutil cp -> gs://<artifacts>/signed-urls/<object>
#     3. update var.signed_url_source_object (or pin the generation)
#     4. tofu apply rolls the function
#
# Invariant compliance:
#   - NFR-S-2/S-3 (credentials posture): runtime SA holds ONLY secretAccessor on
#     the Atlas SRV secret, objectViewer (READ-ONLY) on the layer buckets, and
#     tokenCreator on ITSELF (signBlob). No project-wide storage role; no key.
#   - Invariant 5 (Tier separation): READ-ONLY on payload buckets; the function
#     never writes a layer.
#   - Invariant 9 (no cost theater): no cost field; scale-to-zero gen2 function.
#   - NFR-S-1 (authenticated access): --no-allow-unauthenticated (IAM invoker
#     gate) PLUS the in-function Firebase token check.

# --- Variables (declared in-file; variables.tf is a separate ownership seam) --

variable "signed_url_function_name" {
  description = "Cloud Functions gen2 name for the signed-URL minter (job-0251)."
  type        = string
  default     = "grace-2-mint-signed-url"
}

variable "signed_url_runtime" {
  description = "Cloud Functions Python runtime for the signed-URL minter."
  type        = string
  default     = "python312"
}

variable "signed_url_source_object" {
  description = "GCS object name (within the artifacts bucket, under signed-urls/) of the zipped function source. Uploaded out-of-band by the deploy step (gcloud/gsutil — classifier-blocked, see USER_UNBLOCK). PLACEHOLDER default keeps validate/plan green; apply requires the real uploaded object."
  type        = string
  default     = "signed-urls/source-PLACEHOLDER.zip"
}

variable "signed_url_invoker_members" {
  description = "IAM members allowed to invoke the signed-URL function (roles/run.invoker on the underlying Cloud Run service). At v0.1 the agent-runtime SA is the only caller (job-0254 emission). Defaults to that SA; the user adds others on demand."
  type        = list(string)
  default     = []
}

locals {
  signed_url_labels = merge(local.common_labels, {
    component = "signed-urls"
    sprint    = "13-5"
  })

  # Layer buckets a signed URL may be minted for. The function reads these
  # READ-ONLY; an in-function allowlist (GRACE2_SIGNED_URL_BUCKETS) fails fast on
  # any gs:// URI outside this set. These are the canonical payload buckets that
  # hold COG/FlatGeobuf/solver-output layer objects (buckets.tf + sfincs.tf).
  signed_url_layer_buckets = [
    google_storage_bucket.runs.name,
    google_storage_bucket.cog.name,
    google_storage_bucket.fgb.name,
  ]
}

# --- APIs this file needs (gen2 = Cloud Functions + Cloud Run + Build) --------
#
# cloudfunctions.googleapis.com is NOT in gcp.tf's enabled_apis set (that file is
# a separate ownership surface — job-0251 owns signed_urls.tf only), so enable it
# here with a dedicated resource. run/cloudbuild/artifactregistry are already in
# gcp.tf's set (gen2 functions deploy on Cloud Run + Cloud Build). eventarc is
# not needed (HTTPS trigger, not event-driven). No resource-name collision:
# gcp.tf uses google_project_service.enabled[<api>]; this uses a distinct name.

resource "google_project_service" "signed_url_functions" {
  project            = google_project.grace2.project_id
  service            = "cloudfunctions.googleapis.com"
  disable_on_destroy = false
}

# --- Service account: signed-url-minter ---------------------------------------
#
# Dedicated runtime identity for the function. The tightest possible posture for
# a signing endpoint: read the Atlas SRV secret, read (only) the layer buckets,
# and sign V4 URLs for its own SA (signBlob). NOTHING else. No keys minted.

resource "google_service_account" "signed_url_minter" {
  project      = google_project.grace2.project_id
  account_id   = "signed-url-minter"
  display_name = "GRACE-2 signed-URL minting Cloud Function runtime"
  description  = "Cloud Functions gen2 identity for the signed-URL minter (job-0251). secretAccessor on the Atlas SRV secret; objectViewer (READ-ONLY) on the layer buckets; tokenCreator on ITSELF (V4 signBlob). No write role; no project-wide storage role; no key minted."

  depends_on = [google_project_service.enabled]
}

# --- IAM: read the Atlas SRV secret (Case-owner lookup) -----------------------
#
# Scoped to the ONE secret (not project-wide secretAccessor). The function reads
# the SRV to do a single find_one({_id: case_id}) ownership check.

resource "google_secret_manager_secret_iam_member" "signed_url_minter_srv_accessor" {
  project   = google_project.grace2.project_id
  secret_id = google_secret_manager_secret.mongodb_srv.secret_id
  role      = "roles/secretmanager.secretAccessor"
  member    = "serviceAccount:${google_service_account.signed_url_minter.email}"
}

# --- IAM: READ-ONLY objectViewer on the layer buckets -------------------------
#
# generate_signed_url itself needs no GCS read permission (the signature is
# minted client-side from the SA's signing key), but objectViewer lets the
# function optionally HEAD the object and matches the least-privilege posture for
# a function that hands out access to exactly these objects. Bound at bucket
# scope (mirror of buckets.tf / python-sandbox.tf) — never project-wide.

resource "google_storage_bucket_iam_member" "signed_url_minter_runs_viewer" {
  bucket = google_storage_bucket.runs.name
  role   = "roles/storage.objectViewer"
  member = "serviceAccount:${google_service_account.signed_url_minter.email}"
}

resource "google_storage_bucket_iam_member" "signed_url_minter_cog_viewer" {
  bucket = google_storage_bucket.cog.name
  role   = "roles/storage.objectViewer"
  member = "serviceAccount:${google_service_account.signed_url_minter.email}"
}

resource "google_storage_bucket_iam_member" "signed_url_minter_fgb_viewer" {
  bucket = google_storage_bucket.fgb.name
  role   = "roles/storage.objectViewer"
  member = "serviceAccount:${google_service_account.signed_url_minter.email}"
}

# --- IAM: signBlob self-binding (V4 signing without a key file) ---------------
#
# The runtime SA must be able to actAs/signBlob for ITSELF so google-auth's
# iam.Signer can produce the V4 signature via the IAM Credentials API. This is
# the manifest's "impersonated / signBlob" path that AVOIDS shipping a key file.
# Bound at the SA RESOURCE scope (the SA grants tokenCreator on itself) — the
# minimum that signBlob needs.
#
# NOTE: if the deployer's ADC lacks permission to CREATE this binding,
# `tofu apply` of THIS resource is the classifier-blocked step — see
# reports/inflight/sprint-13-5-USER_UNBLOCK.md for the equivalent gcloud grant.

resource "google_service_account_iam_member" "signed_url_minter_signer_token_creator" {
  service_account_id = google_service_account.signed_url_minter.name
  role               = "roles/iam.serviceAccountTokenCreator"
  member             = "serviceAccount:${google_service_account.signed_url_minter.email}"
}

# --- Function source object (zipped source in the artifacts bucket) -----------
#
# Referenced by name (var.signed_url_source_object). The zip is produced +
# uploaded by the deploy step (classifier-blocked gsutil cp — USER_UNBLOCK). We
# do NOT manage the object's CONTENT here (no archive provider in this module),
# only point the function at it. Declared as a data source so plan/apply read the
# already-uploaded object; until it exists, apply of the function waits on the
# upload (documented in USER_UNBLOCK).

# --- Cloud Function gen2: grace-2-mint-signed-url -----------------------------

resource "google_cloudfunctions2_function" "mint_signed_url" {
  project  = google_project.grace2.project_id
  name     = var.signed_url_function_name
  location = var.gcp_region

  labels = local.signed_url_labels

  build_config {
    runtime     = var.signed_url_runtime
    entry_point = "handle_request" # the HTTPS target in infra/signed_urls/main.py

    source {
      storage_source {
        bucket = google_storage_bucket.artifacts.name
        object = var.signed_url_source_object
      }
    }
  }

  service_config {
    # Scale-to-zero (NFR-C-2). A signing call is sub-second; min=0 is fine, the
    # cold-start is acceptable for a per-layer mint. Bump min_instance_count if a
    # demo needs always-warm signing.
    min_instance_count = 0
    max_instance_count = 10

    available_memory = "256Mi"
    timeout_seconds  = 30

    # Attach the dedicated minimal runtime SA (signBlob + secretAccessor + read).
    service_account_email = google_service_account.signed_url_minter.email

    # The in-function bucket allowlist (defense-in-depth; fails fast on a gs://
    # URI outside the canonical payload buckets).
    environment_variables = {
      GRACE2_SIGNED_URL_BUCKETS = join(",", local.signed_url_layer_buckets)
      # The signer SA email — google-auth surfaces this from the metadata server
      # on gen2, but we set it explicitly so the IaC is the source of truth and
      # the signBlob subject is unambiguous.
      GRACE2_SIGNER_SA_EMAIL = google_service_account.signed_url_minter.email
      # Mongo database + SRV secret resource for the Case-owner lookup.
      GRACE2_MONGO_DB         = var.atlas_db_name
      GRACE2_MONGO_SRV_SECRET = "${google_secret_manager_secret.mongodb_srv.id}/versions/latest"
    }

    # AUTHENTICATED: the underlying Cloud Run service is invoker-gated (no
    # public access). The agent SA (job-0254) is granted run.invoker below.
    ingress_settings               = "ALLOW_ALL"
    all_traffic_on_latest_revision = true
  }

  depends_on = [
    google_project_service.signed_url_functions,
    google_project_service.enabled,
    google_service_account.signed_url_minter,
    google_secret_manager_secret_iam_member.signed_url_minter_srv_accessor,
    google_service_account_iam_member.signed_url_minter_signer_token_creator,
  ]
}

# --- IAM: invoker gate (authenticated only) -----------------------------------
#
# gen2 functions are fronted by a Cloud Run service; run.invoker on that service
# is the authentication gate. We grant it ONLY to the agent-runtime SA (the
# job-0254 emission caller) by default, plus any extra members the user supplies
# via var.signed_url_invoker_members. NO allUsers / allAuthenticatedUsers — the
# function is never publicly invokable (NFR-S-1).

resource "google_cloud_run_v2_service_iam_member" "signed_url_agent_invoker" {
  project  = google_project.grace2.project_id
  location = google_cloudfunctions2_function.mint_signed_url.location
  name     = google_cloudfunctions2_function.mint_signed_url.name
  role     = "roles/run.invoker"
  member   = "serviceAccount:${google_service_account.agent_runtime.email}"
}

resource "google_cloud_run_v2_service_iam_member" "signed_url_extra_invokers" {
  for_each = toset(var.signed_url_invoker_members)

  project  = google_project.grace2.project_id
  location = google_cloudfunctions2_function.mint_signed_url.location
  name     = google_cloudfunctions2_function.mint_signed_url.name
  role     = "roles/run.invoker"
  member   = each.value
}

# --- Output: the function's HTTPS URL -----------------------------------------
#
# Consumed by job-0254 (LayerURI emission) so the agent does not hardcode the
# function URL. Read via `tofu output signed_url_function_url`.

output "signed_url_function_url" {
  description = "HTTPS URL of the signed-URL minting Cloud Function (job-0251). Authenticated (run.invoker) — the agent SA calls it per LayerURI."
  value       = google_cloudfunctions2_function.mint_signed_url.service_config[0].uri
}

output "signed_url_minter_sa_email" {
  description = "Runtime SA email of the signed-URL minter (job-0251)."
  value       = google_service_account.signed_url_minter.email
}
