# backend.tf — remote state on GCS.
#
# State backend decision (job-0014):
#   - GCS-backed remote state from day one (chosen over local-then-migrate).
#   - Bucket name: grace-2-tfstate-grace-2-hazard-prod
#   - Bucket bootstrap is a one-time manual `gcloud storage` step (see
#     `infra/README.md`); the bucket is a documented operational artifact,
#     not a tofu-managed resource (chicken-and-egg: state-for-state-bucket
#     would need its own state file).
#
# Rationale (recorded for the audit):
#   - Versioned GCS gives free PITR; GCS backend has object-generation-based
#     state locking since OpenTofu 1.6+ (no DynamoDB-analog needed).
#   - tfstate routinely holds connection strings, password fingerprints, etc.
#     — the laptop disk is the wrong home (NFR-S-3).
#   - A future collaborator only needs ADC + this backend block.
#
# Trade-offs (see infra/README.md for the full discussion):
#   - One-time manual bootstrap (the `gcloud storage buckets create` runbook).
#   - The state bucket itself is not in `tofu plan` — it would have to be,
#     which means accepting the bootstrap cost or accepting a stale state
#     bucket. We accept the bootstrap.

terraform {
  backend "gcs" {
    bucket = "grace-2-tfstate-grace-2-hazard-prod"
    prefix = "grace-2/dev"
  }
}
