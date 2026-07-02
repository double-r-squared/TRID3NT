# providers.tf -- AWS provider for the ops-watchdog module.
#
# Credentials resolve from the standard AWS credential chain at apply time
# (SSO profile / env vars / ~/.aws/credentials).  NATE runs `aws sso login`
# before any `tofu plan/apply`.
#
# default_tags tags every aws_* resource this module creates.

provider "aws" {
  region = var.region

  default_tags {
    tags = {
      project    = "grace2"
      component  = "ops-watchdog"
      managed_by = "opentofu"
    }
  }
}
