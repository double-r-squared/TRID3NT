# firebase.tf — root-module wiring for the production auth substrate.
#
# Job-0250 (sprint-13.5 Stage 1). Owner: infra specialist.
#
# This file is the ONLY seam between the flat root module and the
# `infra/firebase/` child module. It:
#   1. Instantiates the ./firebase child module.
#   2. Feeds it the root globals it needs (project_id, region, common_labels)
#      and the API-enablement dependency handle so its resources order after
#      identitytoolkit/firestore/firebaserules APIs are enabled (added to
#      local.enabled_apis in gcp.tf).
#   3. Passes the (gitignored) Google OAuth client credentials through from
#      root variables, so all sensitive material stays in one tfvars file.
#   4. Re-exports the child outputs the downstream auth jobs read
#      (job-0252 agent auth, job-0253 web auth, job-0256 web deploy).
#
# Convention note: the root module is otherwise flat (one resource-set per
# *.tf, all referencing google_project.grace2 directly). The auth surface is
# the one place a child module is justified — it gives the manifest's
# `infra/firebase/` ownership boundary a real home without forcing every auth
# resource into the root namespace. No `google-beta` provider is introduced;
# the child uses GA `google` resources only (verified against the locked
# provider 6.50.0 schema).

module "firebase" {
  source = "./firebase"

  project_id    = google_project.grace2.project_id
  region        = var.gcp_region
  common_labels = local.common_labels

  # Authorized sign-in domains: defaults only (decision #3). Derived from the
  # project id so they track the project automatically. localhost covers the
  # dev emulator + `npm run dev`.
  authorized_domains = [
    "localhost",
    "${google_project.grace2.project_id}.web.app",
    "${google_project.grace2.project_id}.firebaseapp.com",
  ]

  # Google OAuth web client (console-created; gitignored). Empty by default →
  # the child skips the Google IdP resource so the plan stays green until the
  # user supplies credentials. See infra/firebase/README.md + USER_UNBLOCK.
  google_oauth_client_id     = var.google_oauth_client_id
  google_oauth_client_secret = var.google_oauth_client_secret

  # Order the child's resources after the auth APIs are enabled. The whole
  # google_project_service.enabled map is passed as the dependency handle; the
  # child only uses it for depends_on.
  api_enable_dependency = google_project_service.enabled
}

# --- Re-exported outputs (read by downstream auth jobs) --------------------

output "firebase_identity_platform_config_name" {
  description = "Identity Platform config resource name (job-0250)."
  value       = module.firebase.identity_platform_config_name
}

output "firebase_authorized_domains" {
  description = "Domains authorized to host the sign-in flow."
  value       = module.firebase.authorized_domains
}

output "firebase_anonymous_enabled" {
  description = "Anonymous sign-in posture (MUST be false in prod — decision #6)."
  value       = module.firebase.anonymous_enabled
}

output "firebase_google_idp_enabled" {
  description = "Whether the Google sign-in IdP is provisioned (true once OAuth credentials supplied)."
  value       = module.firebase.google_idp_enabled
}

output "firebase_firestore_database_name" {
  description = "Firestore database name (custom-claims / tier-gating store)."
  value       = module.firebase.firestore_database_name
}

output "firebase_firestore_rules_release_name" {
  description = "Firebaserules release bound to the Firestore database."
  value       = module.firebase.firestore_rules_release_name
}
