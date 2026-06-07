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

# --- SFINCS substrate (sprint-07 / job-0040) ------------------------------
#
# Consumed by the agent service's run_solver + wait_for_completion tools
# (job-0041) so the Cloud Run Job name + Workflows workflow name + runs
# bucket name are not hardcoded in services/agent/. Agent reads from
# `tofu output -json` at deploy time.

output "sfincs_job_name" {
  description = "Cloud Run Job name for the SFINCS solver (FR-CE-1)."
  value       = google_cloud_run_v2_job.sfincs_solver.name
}

output "sfincs_workflow_name" {
  description = "Cloud Workflows workflow name orchestrating the SFINCS Job (FR-CE-2)."
  value       = google_workflows_workflow.sfincs_orchestrator.name
}

output "runs_bucket_name" {
  description = "GCS bucket name for persisted solver outputs (FR-CE-3)."
  value       = google_storage_bucket.runs.name
}
