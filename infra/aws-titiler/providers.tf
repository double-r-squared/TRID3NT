# providers.tf — AWS provider for the isolated TiTiler tile box.
#
# Credentials resolve from the standard AWS credential chain at apply time
# (SSO profile / env vars / ~/.aws/credentials). NATE runs `aws sso login`
# before `tofu apply` — the agent never scripts around interactive auth
# (AGENTS.md invariant).
#
# default_tags tags every aws_* RESOURCE this module creates (NOT data sources —
# the VPC/subnet/COG buckets are read-only here and keep their original tags).

provider "aws" {
  region = var.region

  default_tags {
    tags = {
      project    = "grace2"
      component  = "titiler"
      managed_by = "opentofu"
    }
  }
}
