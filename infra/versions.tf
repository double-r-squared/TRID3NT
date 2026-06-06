# versions.tf — OpenTofu + provider version pins.
#
# Per PROJECT_STATE decision (2026-06-05): OpenTofu (MPL-2.0) is the IaC tool,
# not BUSL Terraform. NFR-PO-3 permits "Terraform or equivalent". All HCL in
# this directory is written for `tofu`.
#
# Provider pins:
#   - hashicorp/google ~> 6.0 — current major as of 2026
#   - mongodb/mongodbatlas ~> 1.27 — first stable line exposing
#     `mongodbatlas_flex_cluster` (GA'd in v1.18); job-0014 requires this
#     resource for the Flex import flow.

terraform {
  required_version = ">= 1.8.0"

  required_providers {
    google = {
      source  = "hashicorp/google"
      version = "~> 6.0"
    }

    mongodbatlas = {
      source  = "mongodb/mongodbatlas"
      version = "~> 1.27"
    }

    random = {
      source  = "hashicorp/random"
      version = "~> 3.6"
    }
  }
}
