# outputs.tf — module outputs consumed by deploy / verification flows.
#
# Outputs declared here are read by `tofu output` and feed downstream deploy
# scripts so they don't hardcode bucket names / URLs. Per AGENTS.md "remove
# don't shim" + the orchestrator-pinned "IaC is the source of truth" rule,
# downstream code reads from `tofu output -json` rather than referencing
# hardcoded names.

# --- Cache bucket (sprint-06 / job-0031) ----------------------------------
#
# Consumed by the agent service's cache shim (job-0032) so the bucket name
# is not hardcoded in services/agent/. The shim reads from a deploy-time
# env var sourced from `tofu output cache_bucket_name`.

output "cache_bucket_name" {
  description = "GCS bucket name for the atomic-tool fetch cache (FR-DC-1..6)."
  value       = google_storage_bucket.cache.name
}

output "cache_bucket_url" {
  description = "gs:// URL for the atomic-tool fetch cache bucket."
  value       = "gs://${google_storage_bucket.cache.name}"
}
