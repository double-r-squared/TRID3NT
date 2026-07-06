# vpc_endpoints.tf -- the reaper Lambda runs INSIDE the NAT-less VPC (it must, to
# probe each agent task's /api/health on its PRIVATE ENI IP). But the VPC had no
# route to the AWS control plane, so the reaper's first call (DynamoDB Scan of the
# routes table) hung and every invocation TIMED OUT at 60s -> zero reaping, idle
# tasks accumulated. These endpoints give the in-VPC Lambda a private path to the
# three services it calls: DynamoDB (routes), ECS (describe/stop task), Batch
# (in-flight-solve guard). DynamoDB is a FREE Gateway endpoint; ECS + Batch are
# Interface endpoints. (CloudWatch Logs is delivered by the Lambda service itself,
# not from the ENI, so no logs endpoint is needed; the role creds arrive via the
# Lambda env, so no STS endpoint is needed.)

# All route tables in the VPC (the task subnets use the VPC main route table) --
# the Gateway endpoint installs the DynamoDB prefix-list route into each.
data "aws_route_tables" "vpc" {
  filter {
    name   = "vpc-id"
    values = [var.vpc_id]
  }
}

# SG for the Interface endpoints: accept 443 only from the reaper Lambda's ENIs.
resource "aws_security_group" "vpce" {
  name        = "grace2-agent-isolation-vpce"
  description = "GRACE-2 isolation interface VPC endpoints. 443 from in-VPC ECS/Batch consumers: reaper + broker + agents + box."
  vpc_id      = var.vpc_id

  # Private DNS makes these ECS/Batch interface endpoints AUTHORITATIVE for the
  # whole VPC, so EVERY in-VPC consumer of the ECS/Batch control plane must be
  # allowed on 443 -- not just the reaper. A reaper-only rule (the original)
  # blackholed the broker's RunTask AND every agent/box Batch submit_job, which
  # silently broke per-session provisioning + all flood dispatch (2026-06-30).
  ingress {
    description = "HTTPS 443 from every in-VPC ECS/Batch control-plane consumer."
    from_port   = 443
    to_port     = 443
    protocol    = "tcp"
    security_groups = [
      aws_security_group.reaper.id,     # reaper Lambda -> DynamoDB/ECS/Batch
      aws_security_group.broker.id,     # broker -> ECS RunTask (provision agents)
      aws_security_group.agent_task.id, # per-session agents -> Batch submit_job
      "sg-0d15f32310c874a6e",           # EC2 rollback box agent -> Batch submit_job
      "sg-0c7dc540171538e99",           # grace2-batch-sg: Batch compute EC2 -> ECS register (else jobs stick RUNNABLE)
    ]
  }

  egress {
    description = "Return traffic."
    from_port   = 0
    to_port     = 0
    protocol    = "-1"
    cidr_blocks = ["0.0.0.0/0"]
  }

  tags = { Name = "grace2-agent-isolation-vpce" }
}

resource "aws_vpc_endpoint" "dynamodb" {
  vpc_id            = var.vpc_id
  service_name      = "com.amazonaws.${var.region}.dynamodb"
  vpc_endpoint_type = "Gateway"
  route_table_ids   = data.aws_route_tables.vpc.ids
  tags              = { Name = "grace2-agent-isolation-dynamodb" }
}

# scale-to-zero Phase 0 (2026-07-06): FREE S3 Gateway endpoint. TiTiler's
# continuous /vsis3 COG range reads + agent/worker S3 traffic previously
# exited via the IGW; the gateway endpoint keeps it on the AWS backbone at
# zero cost (same pattern as the DynamoDB endpoint above). Transparent --
# default full-access policy, no DNS change, no SG coupling.
resource "aws_vpc_endpoint" "s3" {
  vpc_id            = var.vpc_id
  service_name      = "com.amazonaws.${var.region}.s3"
  vpc_endpoint_type = "Gateway"
  route_table_ids   = data.aws_route_tables.vpc.ids
  tags              = { Name = "grace2-agent-isolation-s3" }
}

# Phase-1 scale-to-zero (design 2.3): ECS + Batch Interface endpoints.
#
# These are the ~$29/mo cost drivers that forced the reaper Lambda into the VPC.
# They are STILL REQUIRED while REAPER_HEALTH_MODE=probe or =both (the reaper
# uses the ECS API for PASS-2 orphan enumeration AND the Batch API for G3).
#
# NOTE: ECS DescribeTasks/ListTasks/StopTask (PASS-2 orphan reaping) is called
# from the Lambda regardless of health mode. However, from a Lambda NOT in the
# VPC these ECS calls reach the PUBLIC ECS API endpoint (no VPC endpoint needed)
# -- the private endpoint is only required for in-VPC access. So once the reaper
# is detached from the VPC (heartbeat-only mode + vpc_config removed), these
# interface endpoints can be destroyed.
#
# TODO(operator): once REAPER_HEALTH_MODE="heartbeat" is confirmed stable:
#   1. Remove the vpc_config block from aws_lambda_function.reaper in reaper.tf.
#   2. Destroy aws_vpc_endpoint.ecs and aws_vpc_endpoint.batch here.
#   3. Remove the aws_security_group_rule.agent_ingress_health_from_reaper rule.
#   4. The reaper SG (aws_security_group.reaper) can also be deleted.
#   Savings: ~$29/mo (two Interface endpoints at ~$14.40/mo each in us-west-2).

resource "aws_vpc_endpoint" "ecs" {
  vpc_id              = var.vpc_id
  service_name        = "com.amazonaws.${var.region}.ecs"
  vpc_endpoint_type   = "Interface"
  subnet_ids          = var.task_subnet_ids
  security_group_ids  = [aws_security_group.vpce.id]
  private_dns_enabled = true
  tags                = { Name = "grace2-agent-isolation-ecs" }
}

resource "aws_vpc_endpoint" "batch" {
  vpc_id              = var.vpc_id
  service_name        = "com.amazonaws.${var.region}.batch"
  vpc_endpoint_type   = "Interface"
  subnet_ids          = var.task_subnet_ids
  security_group_ids  = [aws_security_group.vpce.id]
  private_dns_enabled = true
  tags                = { Name = "grace2-agent-isolation-batch" }
}
