# variables.tf — top-level inputs.
#
# Values land in `terraform.tfvars` (gitignored) or the shell environment.
# A redacted shape lives in `terraform.tfvars.example`.

variable "gcp_project_id" {
  description = "GCP project ID (created out-of-band by job-0014, then imported)."
  type        = string
}

variable "gcp_region" {
  description = "Primary GCP region for Cloud Run + GCS + Workflows."
  type        = string
  default     = "us-central1"
}

variable "gcp_billing_account" {
  description = "Billing account ID linked to the project (free-form, captured for the audit/import only)."
  type        = string
}

variable "env" {
  description = "Environment label applied to every resource (NFR-C-1 idle-cost breakdown)."
  type        = string
  default     = "dev"
}

variable "sprint" {
  description = "Sprint label for the per-resource cost-attribution scheme."
  type        = string
  default     = "03"
}

# --- MongoDB Atlas ---

variable "atlas_org_id" {
  description = "MongoDB Atlas organization ID."
  type        = string
  default     = "6a234700a0e1295958d10c99"
}

variable "atlas_project_id" {
  description = "MongoDB Atlas project ID (already exists; not managed by this tofu config)."
  type        = string
  default     = "6a234700a0e1295958d10cf9"
}

variable "atlas_flex_cluster_name" {
  description = "Name of the Flex cluster (already exists; imported by job-0014)."
  type        = string
  default     = "grace-2-dev"
}

variable "atlas_region_name" {
  description = "Atlas region alias for the backing GCP region. CENTRAL_US == GCP us-central1."
  type        = string
  default     = "CENTRAL_US"
}

variable "atlas_db_name" {
  description = "Logical database name the workers read/write."
  type        = string
  default     = "grace2_dev"
}

variable "atlas_db_username" {
  description = "SCRAM username for the workers."
  type        = string
  default     = "grace2-worker"
}

variable "dev_ip_cidr" {
  description = "Developer machine's public IPv4 as a /32 (Atlas access list entry)."
  type        = string
}

# --- Firebase / Identity Platform auth (sprint-13.5 / job-0250) ------------
#
# Google OAuth web-client credentials for the Google sign-in IdP. Created in
# the GCP console via the OAuth consent screen flow (console-only — see
# reports/inflight/sprint-13-5-USER_UNBLOCK.md). Values live ONLY in the
# gitignored terraform.tfvars (or shell env TF_VAR_google_oauth_client_id /
# _secret); NEVER committed (NFR-S-2/S-3). When empty, the Google IdP resource
# is skipped (count=0) and email/password sign-in still works — so the plan is
# green pre-credential.

variable "google_oauth_client_id" {
  description = "OAuth 2.0 web client ID for Google sign-in (console-created; gitignored). Empty = skip the Google IdP resource."
  type        = string
  default     = ""
  sensitive   = true
}

variable "google_oauth_client_secret" {
  description = "OAuth 2.0 web client secret for Google sign-in (console-created; gitignored). Empty = skip the Google IdP resource."
  type        = string
  default     = ""
  sensitive   = true
}
