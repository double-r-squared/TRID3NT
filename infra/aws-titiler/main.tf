# main.tf — isolated, tiny, always-on TiTiler raster-tile server.
#
# DESIGN INVARIANTS (read before modifying):
#
#   1. FAITHFUL CLONE of the live TiTiler on the agent box.
#      The agent box runs TiTiler as a systemd unit (NOT docker — `docker ps -a`
#      is empty), a plain-uvicorn launch of titiler.application.main:app on
#      :8080 with a specific set of GDAL/AWS/CPL/VSI Environment= lines (job-0290
#      install, job-0314 hardening). user-data below reproduces that unit
#      VERBATIM: same package pins, same ExecStart, same env, Restart=always,
#      boot-enabled. Drift here = tiles that render differently than today.
#
#   2. READS COGs via the INSTANCE ROLE, never static keys.
#      GDAL's /vsis3 picks up the IMDSv2 instance-profile creds. The role gets
#      AmazonS3ReadOnlyAccess (covers all three COG buckets read-only) — TiTiler
#      only READS COGs, never writes, so the agent box's inline cache-write /
#      runs-write policies are deliberately NOT replicated here (least privilege).
#      AmazonSSMManagedInstanceCore gives SSM Session Manager access (no SSH).
#
#   3. STABLE ADDRESS for the CloudFront origin.
#      An Elastic IP is attached so the box's public DNS
#      (ec2-<EIP-dashed>.us-west-2.compute.amazonaws.com) is stable across stop/
#      start/replace. CloudFront origin-titiler.DomainName is repointed at this
#      DNS in the cutover (see DEPLOY_NOTE.md / cloudfront-tiles-origin.tf.docs).
#
#   4. :8080 INGRESS FROM CLOUDFRONT ONLY (preferred).
#      The security group allows :8080 from the AWS-managed CloudFront
#      origin-facing prefix list when var.cloudfront_prefix_list_id is set;
#      otherwise it falls back to var.ingress_cidr (the agent box's broader
#      posture). No inbound SSH unless var.ssh_ingress_cidr is set.
#
#   5. AUTHOR, DO NOT APPLY. This module is authored for NATE to `tofu apply`
#      after verify-before-cutover. Nothing here mutates the live box or the
#      live CloudFront distribution.

data "aws_caller_identity" "current" {}

# Read (never manage) the VPC + subnet the tile box lives in.
data "aws_vpc" "main" {
  id = var.vpc_id
}

data "aws_subnet" "tiles" {
  id = var.subnet_id
}

# Read (never manage) the COG buckets, for ARN construction + validation. The
# instance role grants read on them via AmazonS3ReadOnlyAccess; these data
# sources also fail fast if a bucket name is wrong.
data "aws_s3_bucket" "runs" {
  bucket = var.runs_bucket
}

data "aws_s3_bucket" "cache" {
  bucket = var.cache_bucket
}

data "aws_s3_bucket" "bundle" {
  bucket = var.bundle_bucket
}

# Latest Amazon Linux 2023 AMI for the configured arch (SSM public parameter).
# Matches the live agent box OS (amzn2023, x86_64).
data "aws_ssm_parameter" "al2023" {
  name = var.ami_ssm_parameter
}

locals {
  name_prefix = "grace2-titiler"

  # Public DNS of the EIP, for the CloudFront origin cutover. AWS builds this
  # deterministically from the EIP: ec2-<a-b-c-d>.us-west-2.compute.amazonaws.com
  eip_public_dns = "ec2-${replace(aws_eip.tiles.public_ip, ".", "-")}.${var.region}.compute.amazonaws.com"
}

# ─────────────────────────────────────────────────────────────────────────────
# IAM — instance role + profile for the TiTiler box.
# Least privilege for a READ-ONLY tile server:
#   - AmazonS3ReadOnlyAccess: GetObject/ListBucket on all COG buckets (the same
#     managed policy the agent box's role carries; this is what lets /vsis3 read
#     every COG). TiTiler never writes, so no PutObject anywhere.
#   - AmazonSSMManagedInstanceCore: SSM Session Manager + run-command (ops access
#     without opening SSH), matching the agent box.
# ─────────────────────────────────────────────────────────────────────────────

resource "aws_iam_role" "titiler" {
  name        = "${local.name_prefix}-ec2-role"
  description = "Instance role for the isolated TiTiler tile box. Read-only S3 (COGs via /vsis3) + SSM. No S3 writes - TiTiler renders, it does not write."

  assume_role_policy = jsonencode({
    Version = "2012-10-17"
    Statement = [
      {
        Effect    = "Allow"
        Principal = { Service = "ec2.amazonaws.com" }
        Action    = "sts:AssumeRole"
      }
    ]
  })
}

resource "aws_iam_role_policy_attachment" "titiler_s3_read" {
  role       = aws_iam_role.titiler.name
  policy_arn = "arn:aws:iam::aws:policy/AmazonS3ReadOnlyAccess"
}

resource "aws_iam_role_policy_attachment" "titiler_ssm" {
  role       = aws_iam_role.titiler.name
  policy_arn = "arn:aws:iam::aws:policy/AmazonSSMManagedInstanceCore"
}

resource "aws_iam_instance_profile" "titiler" {
  name = "${local.name_prefix}-ec2-profile"
  role = aws_iam_role.titiler.name
}

# ─────────────────────────────────────────────────────────────────────────────
# SECURITY GROUP — :8080 from CloudFront only (preferred) + egress.
# Ingress source is the AWS-managed CloudFront origin-facing prefix list when
# var.cloudfront_prefix_list_id is set; otherwise var.ingress_cidr (the agent
# box's broader posture). Optional :22 SSH only if var.ssh_ingress_cidr is set
# (default: none — SSM is the access path).
# ─────────────────────────────────────────────────────────────────────────────

resource "aws_security_group" "titiler" {
  name        = "${local.name_prefix}-sg"
  description = "GRACE-2 isolated TiTiler box: :8080 from CloudFront only + all egress."
  vpc_id      = data.aws_vpc.main.id

  egress {
    description = "All outbound (S3 /vsis3 HTTPS COG range reads, PyPI for the venv build, SSM, CloudWatch)."
    from_port   = 0
    to_port     = 0
    protocol    = "-1"
    cidr_blocks = ["0.0.0.0/0"]
  }
}

# :8080 ingress from the CloudFront origin-facing prefix list (tightest posture).
# Created ONLY when var.cloudfront_prefix_list_id is provided.
resource "aws_security_group_rule" "titiler_8080_from_cloudfront" {
  count             = var.cloudfront_prefix_list_id != "" ? 1 : 0
  type              = "ingress"
  description       = "TiTiler :8080 from CloudFront origin-facing IPs only (managed prefix list)."
  security_group_id = aws_security_group.titiler.id
  from_port         = var.titiler_port
  to_port           = var.titiler_port
  protocol          = "tcp"
  prefix_list_ids   = [var.cloudfront_prefix_list_id]
}

# Fallback :8080 ingress from a CIDR when no prefix list is supplied. Reproduces
# the agent box's broader :8080 posture. Mutually exclusive with the rule above.
resource "aws_security_group_rule" "titiler_8080_from_cidr" {
  count             = var.cloudfront_prefix_list_id == "" ? 1 : 0
  type              = "ingress"
  description       = "TiTiler :8080 from var.ingress_cidr (fallback - prefer the CloudFront prefix list)."
  security_group_id = aws_security_group.titiler.id
  from_port         = var.titiler_port
  to_port           = var.titiler_port
  protocol          = "tcp"
  cidr_blocks       = [var.ingress_cidr]
}

# Optional break-glass SSH. Default off (SSM is the access path).
resource "aws_security_group_rule" "titiler_ssh" {
  count             = var.ssh_ingress_cidr != "" ? 1 : 0
  type              = "ingress"
  description       = "Break-glass SSH (:22) from var.ssh_ingress_cidr."
  security_group_id = aws_security_group.titiler.id
  from_port         = 22
  to_port           = 22
  protocol          = "tcp"
  cidr_blocks       = [var.ssh_ingress_cidr]
}

# ─────────────────────────────────────────────────────────────────────────────
# USER-DATA — reproduce the agent box's TiTiler systemd unit VERBATIM.
# Installs python3.11 + a venv at /opt/titiler/venv, pip-installs the pinned
# TiTiler stack, drops /etc/systemd/system/titiler.service with the EXACT
# ExecStart + GDAL/AWS/CPL/VSI Environment= lines from the live box (job-0290 /
# job-0314), enables + starts it. Optionally installs the titiler-watchdog
# oneshot+timer (job-0314). templatefile keeps the script readable + variable.
# ─────────────────────────────────────────────────────────────────────────────

locals {
  user_data = templatefile("${path.module}/user_data.sh.tftpl", {
    region           = var.region
    titiler_port     = var.titiler_port
    titiler_workers  = var.titiler_workers
    titiler_pip_spec = var.titiler_pip_spec
    cors_origins     = var.cors_origins
    bundle_bucket    = var.bundle_bucket
    install_watchdog = var.install_watchdog
  })
}

# ─────────────────────────────────────────────────────────────────────────────
# EC2 INSTANCE — the tiny always-on TiTiler box.
# IMDSv2 required (http_tokens=required) so the instance-profile creds GDAL uses
# for /vsis3 are fetched the secure way (matches modern Amazon Linux defaults).
# ─────────────────────────────────────────────────────────────────────────────

resource "aws_instance" "titiler" {
  ami                    = data.aws_ssm_parameter.al2023.value
  instance_type          = var.instance_type
  subnet_id              = data.aws_subnet.tiles.id
  iam_instance_profile   = aws_iam_instance_profile.titiler.name
  vpc_security_group_ids = [aws_security_group.titiler.id]

  # Public IP on launch so the box reaches S3/PyPI immediately; the EIP below is
  # the stable address CloudFront points at.
  associate_public_ip_address = true

  user_data                   = local.user_data
  user_data_replace_on_change = true

  metadata_options {
    http_endpoint               = "enabled"
    http_tokens                 = "required" # IMDSv2 only
    http_put_response_hop_limit = 1
  }

  root_block_device {
    volume_size           = var.root_volume_gb
    volume_type           = "gp3"
    encrypted             = true
    delete_on_termination = true
  }

  tags = {
    Name = "${local.name_prefix}-box"
    role = "titiler-tiles-only"
  }
}

# ─────────────────────────────────────────────────────────────────────────────
# ELASTIC IP — stable address for the CloudFront origin.
# Its public DNS (local.eip_public_dns) is what origin-titiler.DomainName is
# repointed to in the cutover. EIP survives stop/start/replace of the instance.
# ─────────────────────────────────────────────────────────────────────────────

resource "aws_eip" "tiles" {
  domain = "vpc"

  tags = {
    Name = "${local.name_prefix}-eip"
  }
}

resource "aws_eip_association" "tiles" {
  instance_id   = aws_instance.titiler.id
  allocation_id = aws_eip.tiles.id
}
