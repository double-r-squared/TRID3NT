# secrets.tf — Secret Manager storage for the MongoDB SRV connection string.
#
# Discipline (NFR-S-2/S-3):
#   - Connection strings NEVER appear in code, repo, container images, env
#     files committed to git, or local plaintext.
#   - The SRV URI is constructed from the imported Flex cluster's standard
#     SRV (without credentials) + the tofu-managed `mongodbatlas_database_user`
#     credentials, and is stored only in Secret Manager.
#   - Cloud Run / agent service / workers will read this secret at runtime
#     via Workload Identity (bindings land in later infra jobs).

locals {
  # The Flex `mongodbatlas_flex_cluster.dev.connection_strings.standard_srv`
  # output looks like: mongodb+srv://grace-2-dev.tszeckl.mongodb.net
  # Insert credentials into the URI and pin the database name + retryWrites.
  srv_with_creds = format(
    "mongodb+srv://%s:%s@%s/%s?retryWrites=true&w=majority",
    mongodbatlas_database_user.worker.username,
    random_password.worker_pw.result,
    replace(mongodbatlas_flex_cluster.dev.connection_strings.standard_srv, "mongodb+srv://", ""),
    var.atlas_db_name,
  )
}

resource "google_secret_manager_secret" "mongodb_srv" {
  project   = google_project.grace2.project_id
  secret_id = "mongodb-srv-dev"

  replication {
    auto {}
  }

  labels = local.common_labels

  depends_on = [google_project_service.enabled]
}

resource "google_secret_manager_secret_version" "mongodb_srv_v1" {
  secret      = google_secret_manager_secret.mongodb_srv.id
  secret_data = local.srv_with_creds
}
