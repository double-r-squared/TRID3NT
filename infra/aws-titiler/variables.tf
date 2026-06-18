# variables.tf — inputs for the isolated TiTiler tile box.
#
# Defaults match the hand-provisioned prod environment (account 226996537797,
# us-west-2) and the live agent box probed in the Investigate findings. Override
# any in a tfvars file without editing this file.

variable "region" {
  type        = string
  description = "AWS region for all resources. Must match the COG buckets + CloudFront origin region."
  default     = "us-west-2"
}

variable "account_id" {
  type        = string
  description = "AWS account id (used to build bucket ARNs for the least-privilege instance role)."
  default     = "226996537797"
}

variable "vpc_id" {
  type        = string
  description = "ID of the default VPC the tile box lives in (same VPC as the agent box + Batch). Hand-provisioned."
  default     = "vpc-01b7ce297bb3a95e9"
}

variable "subnet_id" {
  type        = string
  description = <<-EOT
    Public subnet the tile box is launched into. Must have auto-assign public IP
    (the box reaches S3 over the public internet via /vsis3, and CloudFront
    reaches it on :8080 via its public DNS/EIP — no NAT gateway). Default is the
    us-west-2a public subnet from infra/aws-batch's subnet list.
  EOT
  default     = "subnet-0e29172e519406a62" # us-west-2a
}

variable "instance_type" {
  type        = string
  description = <<-EOT
    EC2 instance type for the TiTiler box. The current agent box is x86_64
    (uname -m = x86_64, Amazon Linux 2023) and TiTiler is installed there as a
    plain Python venv (uvicorn titiler.application.main:app) — NOT a container —
    so there is no architecture lock to a prebuilt image; the venv is rebuilt
    from PyPI wheels in user-data. The workload is just /vsis3 range-read tile
    rendering (TiTiler RSS ~830 MiB under 4 workers on the agent box). t3.small
    (2 vCPU / 2 GiB) is adequate for the always-on tile load with --workers 2.
    Keeping x86_64 (t3.small) matches the live box's wheels verbatim (rasterio
    1.4.4 / titiler-* 2.0.4); switch to t4g.small only if you also confirm the
    arm64 wheels for those exact pins resolve cleanly (see DEPLOY_NOTE.md).
  EOT
  default     = "t3.small"
}

variable "titiler_workers" {
  type        = number
  description = <<-EOT
    uvicorn --workers for TiTiler. The agent box runs 4 (raised 2->4 in
    job-0314). On a 2 vCPU t3.small, 2 workers fit memory comfortably; raise to
    4 only if you size up the instance. Threaded through user-data.
  EOT
  default     = 2
}

variable "runs_bucket" {
  type        = string
  description = "COG bucket: model/run outputs TiTiler serves (read-only). Same bucket COGs are durably stored in."
  default     = "grace2-hazard-runs-226996537797"
}

variable "cache_bucket" {
  type        = string
  description = "COG bucket: cache outputs TiTiler serves (read-only)."
  default     = "grace2-hazard-cache-226996537797"
}

variable "bundle_bucket" {
  type        = string
  description = <<-EOT
    Agent-bundle bucket. Holds the watchdog health COG
    (health/titiler-health.tif) the agent box's titiler-watchdog probes, plus
    agent bundle artifacts observed in live TiTiler request logs. Read-only on
    this box (the watchdog only reads it). Covered by AmazonS3ReadOnlyAccess
    regardless; named here so the deploy note + watchdog health URL are explicit.
  EOT
  default     = "grace2-agent-bundle-226996537797"
}

variable "titiler_port" {
  type        = number
  description = "Port TiTiler/uvicorn listens on. MUST stay 8080 — CloudFront origin-titiler is wired to HTTPPort=8080 http-only."
  default     = 8080
}

variable "cors_origins" {
  type        = string
  description = <<-EOT
    TITILER_API_CORS_ORIGINS — comma-separated allowed origins, copied VERBATIM
    from the live agent box so the isolated box behaves identically. The site
    origin has not changed, so this stays the same after the CloudFront cutover.
  EOT
  default     = "http://grace2-hazard-web-226996537797.s3-website-us-west-2.amazonaws.com,http://localhost:5173"
}

variable "cloudfront_prefix_list_id" {
  type        = string
  description = <<-EOT
    AWS-managed prefix list for CloudFront's origin-facing IP ranges
    (com.amazonaws.global.cloudfront.origin-facing). When set, the security
    group allows :8080 ingress ONLY from CloudFront edge servers — the tightest
    posture, matching "allow :8080 from CloudFront only". Leave "" to fall back
    to var.ingress_cidr (the agent box today allows broader :8080 ingress).

    The id is REGION-SPECIFIC and account-visible; resolve it at apply time with:
      aws ec2 describe-managed-prefix-lists --region us-west-2 \
        --filters Name=prefix-list-name,Values=com.amazonaws.global.cloudfront.origin-facing \
        --query 'PrefixLists[0].PrefixListId' --output text
    Then set cloudfront_prefix_list_id = "pl-..." in a tfvars file (it is the
    same well-known global CloudFront list in every account, but the id string
    differs per region). Do NOT hard-code a guessed id here.
  EOT
  default     = ""
}

variable "ingress_cidr" {
  type        = string
  description = <<-EOT
    Fallback CIDR for :8080 ingress when cloudfront_prefix_list_id is "". The
    live agent box allows :8080 from a broad range today; 0.0.0.0/0 reproduces
    that exactly (TiTiler serves only public map tiles, no credentialed data),
    but the CloudFront prefix list (above) is strongly preferred for a
    tiles-box-only deployment. Document whichever you choose in the deploy note.
  EOT
  default     = "0.0.0.0/0"
}

variable "ssh_ingress_cidr" {
  type        = string
  description = <<-EOT
    Optional CIDR for :22 SSH ingress. Default "" = NO SSH rule (SSM Session
    Manager via AmazonSSMManagedInstanceCore is the access path, matching the
    agent box). Set to a /32 only if you need direct SSH for break-glass.
  EOT
  default     = ""
}

variable "titiler_pip_spec" {
  type        = string
  description = <<-EOT
    Exact pip install spec for TiTiler, pinned to the live agent box's resolved
    versions (titiler-application 2.0.4 pulls titiler-core/extensions/mosaic/
    xarray 2.0.4 + rasterio 1.4.4 + fastapi 0.136.3 + uvicorn 0.49.0). Pinning
    the top-level package reproduces the live tile renderer faithfully; if a
    transitive bump is observed, pin the leaves too. Threaded through user-data.
  EOT
  default     = "titiler.application==2.0.4 uvicorn==0.49.0 httpx==0.28.1"
}

variable "ami_ssm_parameter" {
  type        = string
  description = <<-EOT
    SSM public parameter for the latest Amazon Linux 2023 x86_64 AMI. Matches
    the live agent box OS (amzn2023, kernel 6.1.x, x86_64). For t4g.small swap
    this to the arm64 path: /aws/service/ami-amazon-linux-latest/al2023-ami-kernel-default-arm64.
  EOT
  default     = "/aws/service/ami-amazon-linux-latest/al2023-ami-kernel-default-x86_64"
}

variable "root_volume_gb" {
  type        = number
  description = "Root EBS gp3 volume size. TiTiler venv + OS is small; the VSI cache is in-memory. 20 GiB is generous."
  default     = 20
}

variable "install_watchdog" {
  type        = bool
  description = <<-EOT
    Install the titiler-watchdog oneshot+timer (job-0314) that restarts TiTiler
    if curl localhost:8080/cog/info on the health COG returns code 000/empty.
    The wedge it guards is real (TiTiler :8080 wedging — service up, all tiles
    time out). Recommended true on the isolated always-on box.
  EOT
  default     = true
}
