# versions.tf — OpenTofu + AWS provider version pins for the SFINCS Batch module.
#
# This is a SELF-CONTAINED root module (its own state, its own provider — no
# dependency on the GCP/Atlas infra/ root at the repo level). The GCP infra
# root uses `hashicorp/google`; this root uses `hashicorp/aws`.
#
# OpenTofu (MPL-2.0) is the IaC tool per PROJECT_STATE decision 2026-06-05.
# All HCL is written for `tofu`, not BUSL Terraform.
#
# Provider choice:
#   aws ~> 5 — current major as of 2026. The 5.x series introduced aws_batch_*
#   resources that model SPOT_CAPACITY_OPTIMIZED allocation (required for
#   scale-to-zero SPOT compute environments). Pinning at 5.x avoids unintended
#   drift if hashicorp ships a 6.x breaking series before NATE re-evaluates.

terraform {
  required_version = ">= 1.8.0"

  required_providers {
    aws = {
      source  = "hashicorp/aws"
      version = "~> 5.0"
    }
  }
}
