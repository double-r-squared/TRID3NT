# versions.tf -- OpenTofu + provider pins for the grace2-ops-watchdog module.
#
# Self-contained root module with local state (lightweight ops tooling;
# no shared state needed -- no other module depends on its outputs).
# Does NOT touch or import infra/aws-agent-isolation state.
#
# OpenTofu (MPL-2.0) per project convention -- written for `tofu`, not BUSL
# Terraform.  aws ~> 5 matches the rest of the AWS infra.

terraform {
  required_version = ">= 1.8.0"

  required_providers {
    aws = {
      source  = "hashicorp/aws"
      version = "~> 5.0"
    }
    archive = {
      source  = "hashicorp/archive"
      version = "~> 2.4"
    }
  }

  # Local state -- intentional for this lightweight safety-net module.
  # The tfstate file is gitignored.  To migrate to S3 later:
  #   backend "s3" {
  #     bucket = "grace2-hazard-runs-226996537797"
  #     key    = "tofu-state/aws-ops-watchdog.tfstate"
  #     region = "us-west-2"
  #   }
}
