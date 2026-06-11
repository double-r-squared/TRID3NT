# infra/firebase/outputs.tf — child-module outputs.
#
# Consumed by the root module (../outputs.tf re-exports the ones downstream
# deploy/verify flows need). Per the repo "IaC is the source of truth" rule,
# downstream code reads these via `tofu output -json` rather than hardcoding.

output "identity_platform_config_name" {
  description = "Resource name of the Identity Platform config (proves the tenant is provisioned)."
  value       = google_identity_platform_config.auth.name
}

output "authorized_domains" {
  description = "Domains authorized to host the sign-in flow (echoed for verification)."
  value       = google_identity_platform_config.auth.authorized_domains
}

output "email_password_enabled" {
  description = "Whether email/password sign-in is enabled (expected true for dev/test)."
  value       = google_identity_platform_config.auth.sign_in[0].email[0].enabled
}

output "anonymous_enabled" {
  description = "Whether anonymous sign-in is enabled (MUST be false in prod — decision #6)."
  value       = google_identity_platform_config.auth.sign_in[0].anonymous[0].enabled
}

output "google_idp_enabled" {
  description = "Whether the Google sign-in IdP resource was provisioned (true once OAuth client credentials are supplied)."
  value       = length(google_identity_platform_default_supported_idp_config.google) > 0
}

output "firestore_database_name" {
  description = "Firestore database name (the custom-claims / tier-gating store)."
  value       = google_firestore_database.auth.name
}

output "firestore_location" {
  description = "Firestore database location id."
  value       = google_firestore_database.auth.location_id
}

output "firestore_rules_release_name" {
  description = "Firebaserules release name bound to the Firestore database (cloud.firestore)."
  value       = google_firebaserules_release.auth.name
}
