# providers.tf — google + mongodbatlas + random providers.
#
# Credentials:
#   - google: ADC (`gcloud auth application-default login` on the user side;
#     this job verified `gcloud auth application-default print-access-token`
#     succeeds before any tofu work). Service-account JSON keys are NEVER
#     committed (NFR-S-2/S-3); ADC stays in `~/.config/gcloud/`.
#   - mongodbatlas: programmatic API key supplied as env vars
#     `MONGODB_ATLAS_PUBLIC_KEY` / `MONGODB_ATLAS_PRIVATE_KEY`. The job-0014
#     specialist creates a short-lived project-scoped key (GROUP_OWNER on
#     project 6a234700a0e1295958d10cf9) for the import + apply and revokes
#     it after — documented in the job-0014 report.

provider "google" {
  project = var.gcp_project_id
  region  = var.gcp_region
}

provider "mongodbatlas" {
  # MONGODB_ATLAS_PUBLIC_KEY / MONGODB_ATLAS_PRIVATE_KEY from environment.
}
