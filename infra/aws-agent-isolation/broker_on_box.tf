# broker_on_box.tf -- scale-to-zero Phase 2 (design 2.2): run the session broker
# as a systemd/docker unit on the always-on TiTiler box (:8081) instead of an
# always-on Fargate service behind an ALB. The ALB (~$20/mo) + the broker Fargate
# task (~$12/mo) are the deletion targets once CloudFront /ws* points at the box.
#
# CROSS-ROOT SEAM (documented, deliberate): the TiTiler box lives in
# infra/aws-titiler (its own root). This file grafts the broker's network + IAM
# grants onto that box's SG/role BY ID/NAME so the whole Phase 2 delta stays in
# ONE root next to the broker policy it reuses. If the titiler root is ever
# re-created, update the two locals below.

locals {
  titiler_sg_id     = "sg-0b408ac52b1e09120"      # aws_security_group.titiler (infra/aws-titiler)
  titiler_role_name = "grace2-titiler-ec2-role"   # aws_iam_role.titiler (infra/aws-titiler)
  cloudfront_pl_id  = "pl-82a045eb"               # com.amazonaws.global.cloudfront.origin-facing
  titiler_eni_id    = "eni-020fc28003047cbfc"     # the box's primary ENI (i-06cfdd3d6c66b2126)
}

# CloudFront -> box broker (:8081). Same tightest-posture pattern as the box's
# existing :8080 TiTiler rule: origin-facing managed prefix list only.
#
# DEDICATED SG (not a rule on the titiler SG): the CloudFront origin-facing
# prefix list weighs ~55 entries against the 60-rules-per-SG quota, and the
# titiler SG's existing :8080 prefix-list rule already spends that budget --
# adding a second prefix-list rule there fails RulesPerSecurityGroupLimitExceeded.
# A second SG gets its own quota; the ENI attachment below grafts it onto the
# box alongside the titiler SG.
resource "aws_security_group" "broker_box" {
  name        = "grace2-broker-box"
  description = "Box-hosted session broker :8081 from CloudFront origin-facing IPs only (Phase 2)."
  vpc_id      = var.vpc_id

  ingress {
    description     = "Session broker :8081 from CloudFront origin-facing IPs."
    from_port       = 8081
    to_port         = 8081
    protocol        = "tcp"
    prefix_list_ids = [local.cloudfront_pl_id]
  }

  egress {
    description = "All outbound (ECS/DynamoDB APIs, agent-task WS proxy)."
    from_port   = 0
    to_port     = 0
    protocol    = "-1"
    cidr_blocks = ["0.0.0.0/0"]
  }

  tags = { Name = "grace2-broker-box" }
}

# Graft the broker SG onto the TiTiler box's primary ENI. NOTE (cross-root): a
# future `tofu apply` in infra/aws-titiler will see the extra SG as drift on
# vpc_security_group_ids -- its DEPLOY_NOTE.md documents keeping this SG.
resource "aws_network_interface_sg_attachment" "broker_box" {
  security_group_id    = aws_security_group.broker_box.id
  network_interface_id = local.titiler_eni_id
}

# Box broker -> per-session agent tasks: WS proxy (8765) + provision health
# probe (8766). Mirrors the Fargate broker SG's two grants.
resource "aws_security_group_rule" "agent_ingress_ws_from_box_broker" {
  type                     = "ingress"
  from_port                = 8765
  to_port                  = 8765
  protocol                 = "tcp"
  security_group_id        = aws_security_group.agent_task.id
  source_security_group_id = local.titiler_sg_id
  description              = "WS from the box-hosted session broker (Phase 2)."
}

resource "aws_security_group_rule" "agent_ingress_health_from_box_broker" {
  type                     = "ingress"
  from_port                = 8766
  to_port                  = 8766
  protocol                 = "tcp"
  security_group_id        = aws_security_group.agent_task.id
  source_security_group_id = local.titiler_sg_id
  description              = "/api/health provision probe from the box-hosted session broker (Phase 2)."
}

# The box's instance role gets the SAME least-privilege broker policy the
# Fargate task role carries (RunTask/Stop/Describe scoped to the agents cluster,
# PassRole for the two agent roles, routes-table RW, users resolve/provision).
resource "aws_iam_role_policy" "broker_on_box" {
  name   = "grace2-broker-on-box-policy"
  role   = local.titiler_role_name
  policy = data.aws_iam_policy_document.broker_task.json
}

# Pull the grace2-broker image from ECR (the box runs the SAME tested artifact
# the Fargate service ran -- no dependency drift).
resource "aws_iam_role_policy_attachment" "titiler_ecr_read" {
  role       = local.titiler_role_name
  policy_arn = "arn:aws:iam::aws:policy/AmazonEC2ContainerRegistryReadOnly"
}
