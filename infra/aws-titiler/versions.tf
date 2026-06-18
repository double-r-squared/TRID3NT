# versions.tf — OpenTofu + AWS provider pins for the isolated TiTiler tile box.
#
# SELF-CONTAINED root module: its own remote state, its own provider, NO
# dependency on the repo-level infra/ root, infra/aws-batch, or infra/aws-autostop.
# It reads the existing VPC/subnet/COG buckets (data sources) and CREATES one
# tiny always-on EC2 instance, its IAM instance role+profile, a security group,
# and an Elastic IP — the dedicated TiTiler raster-tile server that CloudFront's
# /tiles* + /cog/* behaviors point at, so the map stays alive 24/7 even after the
# Wave-3 auto-stop (infra/aws-autostop) parks the heavy agent box.
#
# WHY this exists (DECISION NATE 2026-06-17, option A): the agent EC2 box
# (i-0251879a278df797f, t3.large) currently CO-HOSTS grace2-agent (:8765/:8766)
# AND TiTiler (:8080). Re-arming auto-stop on that box would blank the map
# because TiTiler dies with it. Isolating TiTiler onto this tiny always-on box —
# and repointing CloudFront /tiles*+/cog/* here — lets the agent scale to zero
# while raster tiles keep serving. The catalog/health HTTP :8766 STAYS on the
# agent box (it reports the agent's WS-connection state, the signal the idle
# Lambda polls); ONLY TiTiler moves here.
#
# OpenTofu (MPL-2.0) per PROJECT_STATE decision 2026-06-05 — all HCL is written
# for `tofu`, not BUSL Terraform. aws ~> 5 matches the rest of the AWS infra.

terraform {
  required_version = ">= 1.8.0"

  required_providers {
    aws = {
      source  = "hashicorp/aws"
      version = "~> 5.0"
    }
  }

  # Remote state in the existing runs bucket so the state is durable and NATE can
  # manage this module from any machine (matches infra/aws-batch + aws-autostop).
  # Distinct key so it never collides with the other modules' state. No DynamoDB
  # lock table — single-operator for now.
  backend "s3" {
    bucket = "grace2-hazard-runs-226996537797"
    key    = "tofu-state/aws-titiler.tfstate"
    region = "us-west-2"
  }
}
