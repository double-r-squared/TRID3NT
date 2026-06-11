# infra/firebase/variables.tf — child-module inputs.
#
# All inputs are passed from the root module's ../firebase.tf. The child module
# is deliberately decoupled from the root's globals (google_project.grace2,
# local.common_labels) so the manifest's `infra/firebase/` ownership boundary
# is real — the root wires the seams, this module owns the auth resources.

variable "project_id" {
  description = "GCP project ID hosting the Identity Platform tenant + Firestore (grace-2-hazard-prod)."
  type        = string
}

variable "region" {
  description = "Primary GCP region (carried for label/parity consistency with the root module; Firestore uses firestore_location instead)."
  type        = string
  default     = "us-central1"
}

variable "common_labels" {
  description = "Root-module common labels (project/env/sprint) for parity. Note: Identity Platform + Firebaserules resources do not accept labels; this is retained for future label-bearing resources and documentation parity."
  type        = map(string)
  default     = {}
}

variable "authorized_domains" {
  description = <<-EOT
    Domains permitted to host the Identity Platform sign-in flow. Decision #3
    (defaults only): the two free Firebase Hosting domains plus localhost for
    the dev emulator / dev server. <project>.web.app and
    <project>.firebaseapp.com are the auto-provisioned Hosting domains.
  EOT
  type        = list(string)
  default = [
    "localhost",
    "grace-2-hazard-prod.web.app",
    "grace-2-hazard-prod.firebaseapp.com",
  ]
}

variable "firestore_location" {
  description = "Firestore database location (IMMUTABLE after creation). nam5 = US multi-region, durable, co-resident with us-central1."
  type        = string
  default     = "nam5"
}

# --- Google OAuth web client (console-created; sensitive; gitignored) -------
#
# Created via the GCP console OAuth consent screen flow (console-only — see
# USER_UNBLOCK). When BOTH are non-empty the Google sign-in IdP is provisioned;
# when empty the IdP resource is skipped (count=0) so the plan stays green
# pre-credential and email/password sign-in still works.
#
# Values live ONLY in the gitignored infra/firebase/terraform.tfvars (or the
# shell env as TF_VAR_google_oauth_client_id / _secret). NEVER committed
# (NFR-S-2/S-3).

variable "google_oauth_client_id" {
  description = "OAuth 2.0 web client ID for Google sign-in (console-created). Empty = skip the Google IdP resource."
  type        = string
  default     = ""
  sensitive   = true
}

variable "google_oauth_client_secret" {
  description = "OAuth 2.0 web client secret for Google sign-in (console-created). Empty = skip the Google IdP resource."
  type        = string
  default     = ""
  sensitive   = true
}

# --- API-enablement dependency seam ----------------------------------------
#
# Passed from the root module so the child module's resources order AFTER the
# Identity Platform / Firestore / Firebaserules APIs are enabled (the root owns
# google_project_service.enabled). Value is the resource reference; the child
# only uses it for depends_on ordering.
variable "api_enable_dependency" {
  description = "Opaque dependency handle (root-module API-enablement resource) so auth resources order after API enablement."
  type        = any
  default     = null
}
