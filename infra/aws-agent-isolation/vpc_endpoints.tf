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

# aws_security_group.vpce: DESTROYED 2026-07-06 (Phase 1). It existed only to
# gate 443 into the ECS/Batch Interface endpoints below, which are gone -- all
# in-VPC consumers now reach the public ECS/Batch APIs via the IGW. Removed from
# code so an apply cannot recreate it. (Historical gotcha preserved: when the
# interface endpoints DID exist, private DNS made them authoritative for the
# whole VPC, so the SG had to allow reaper + broker + agents + box + batch-sg
# on 443 -- a reaper-only rule blackholed all flood dispatch, 2026-06-30.)

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

# aws_vpc_endpoint.ecs: DESTROYED 2026-07-06 (Phase 1 -- heartbeat reaper needs no VPC attachment; in-VPC consumers use the public API via IGW). Deliberately removed from code so an apply cannot recreate it.

# aws_vpc_endpoint.batch: DESTROYED 2026-07-06 (Phase 1 -- heartbeat reaper needs no VPC attachment; in-VPC consumers use the public API via IGW). Deliberately removed from code so an apply cannot recreate it.
