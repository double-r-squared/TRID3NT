# RUNBOOK -- Fargate-per-session agent isolation: gated migration

The 9-step migration from the single EC2 agent box
(`i-0251879a278df797f`) to per-session Fargate isolation, from the spike
(`reports/design/agent_isolation_spike.md` section 7). **Dark-build -> canary ->
CloudFront /ws cutover -> drain + decommission**, with the single box kept
instantly startable as rollback the WHOLE way.

ASCII only. NON-DESTRUCTIVE up to step 7; the live cutover (step 7) needs NATE's
explicit go (the prod-deploy gate). The single box stays the production path
until step 8.

---

## STATUS: what this foundation delivers vs what the canary still needs

NON-DESTRUCTIVE-COMPLETE in this drop (authored, validated, NOT applied):

- `services/agent/Dockerfile` + `.dockerignore` -- the agent containerized
  byte-identical (multi-stage, ECR-Public base, deploy-facts env carried over).
- `buildspec.agent.yml` + `codebuild.tf` -- off-box agent image build.
- The full IaC scaffold (`*.tf`) -- routes table, ECS cluster, agent task def,
  ALB (4000s idle), broker service + IAM, the per-task idle reaper Lambda.
- The broker code: concrete route-decision + cognito reuse + provision logic +
  unit tests; the byte-proxy is a documented skeleton.

NEEDS THE GATED CANARY/CUTOVER (NOT done here):

- The byte-proxy plumbing (`broker/proxy.py`) + the broker server entry.
- The pre-upgrade token/session_id carrier on the WEB client (`web/src/ws.ts`).
- The live-value TODOs (below) filled in `terraform.tfvars`.
- A broker-builder CodeBuild project (mirror `grace2-agent-builder`).
- First-connect user-provisioning in the broker (or a bootstrap path).
- The actual `tofu apply` + the canary proof + the CloudFront cutover (steps 3-9).

### LIVE-VALUE TODOs (fill `terraform.tfvars` before any `tofu plan`)

| var | how to find it |
|---|---|
| `vpc_id` | `aws ec2 describe-instances --instance-ids i-0251879a278df797f --query 'Reservations[].Instances[].VpcId' --output text --region us-west-2` |
| `public_subnet_ids` | `aws ec2 describe-subnets --filters Name=vpc-id,Values=<vpc> Name=map-public-ip-on-launch,Values=true --query 'Subnets[].SubnetId' --output text --region us-west-2` |
| `task_subnet_ids` | private subnets with NAT egress in the same VPC (or the public ones with assign_public_ip) |
| `acm_certificate_arn` | `aws acm list-certificates --region us-west-2` (a us-west-2 REGIONAL cert for the ALB; NOT the us-east-1 CloudFront cert) |
| `agent_image` | the ECR ref after step 1 builds it: `226996537797.dkr.ecr.us-west-2.amazonaws.com/grace2-agent:latest` |
| `broker_image` | set after the broker-builder project + the proxy plumbing land (step 3) |

---

## Step 0 -- READ-ONLY BASELINE (non-destructive)

Confirm deployed==HEAD on the box (SWMM-offbox lesson) and snapshot the working
`/ws` path + the `/api/health` busy contract.

```
# deployed==HEAD spot check (read-only SSM):
aws ssm send-command --instance-ids i-0251879a278df797f --region us-west-2 \
  --document-name AWS-RunShellScript \
  --parameters 'commands=["grep -c HEARTBEAT_INTERVAL_SECONDS /opt/grace2/services/agent/src/grace2_agent/server.py"]'

# capture today's busy semantics (the contract every task must keep):
curl -s http://54.185.114.233:8766/api/health   # -> {"ok":true,"active_connections":N,"busy":bool}
```

ROLLBACK: none (read-only).

---

## Step 1 -- DARK-BUILD the agent image (non-destructive)

Build the agent image OFF-BOX via CodeBuild. No traffic touches it.

```
# provision the builder + ECR repo (scaffold -> apply ONLY this module):
cd infra/aws-agent-isolation
# fill terraform.tfvars first (the TODOs above)
tofu init
tofu apply -target=aws_ecr_repository.agent \
           -target=aws_codebuild_project.agent_builder \
           -target=aws_iam_role.agent_builder \
           -target=aws_iam_role_policy.agent_builder

# upload the build context (repo-root subset) + build:
cd ../..
tar czf agent_src.tgz services/agent
aws s3 cp agent_src.tgz s3://grace2-agent-bundle-226996537797/agent-build/agent_src.tgz --region us-west-2
aws codebuild start-build --project-name grace2-agent-builder --region us-west-2
# poll + READ the size/layer log lines the buildspec emits:
aws codebuild batch-get-builds --ids <build-id> --region us-west-2
```

VERIFY: the build log prints `=== IMAGE SIZE ===` (~1.2-1.6 GB expected) +
`PUSH_OK grace2-agent`. Pin `agent_image` to the pushed digest.

ROLLBACK: delete the image tag / the CodeBuild project. Nothing live changed.

---

## Step 2 -- STAND UP THE ROUTE TABLE + the ECS scaffold (non-destructive)

Apply the rest of the data plane; NO traffic routes to it yet.

```
cd infra/aws-agent-isolation
tofu apply   # routes table, cluster, agent task def, ALB, agent SG, reaper
             # (reaper_dry_run=true -- the default -- so it only LOGS)
```

VERIFY: `tofu output routes_table_name`, `ecs_cluster_name`,
`agent_task_definition_family`, `alb_dns_name` all resolve.

ROLLBACK: `tofu destroy` (this module is self-contained; the live box +
DynamoDB/S3 + TiTiler are untouched -- this module reads them, never mutates).

---

## Step 3 -- BUILD THE THIN BROKER (dark)

Land the byte-proxy plumbing (`broker/proxy.py`) + the server entry, build the
broker image off-box (a broker-builder CodeBuild project, mirror
`grace2-agent-builder`), set `broker_image`, and bring up the broker ECS service
behind the ALB -- still on a SEPARATE hostname, NOT the live `/ws` origin.

```
# after the proxy plumbing + broker-builder land:
tar czf broker_src.tgz services/agent infra/aws-agent-isolation/broker
aws s3 cp broker_src.tgz s3://grace2-agent-bundle-226996537797/broker-build/broker_src.tgz --region us-west-2
aws codebuild start-build --project-name grace2-broker-builder --region us-west-2
# set broker_image in terraform.tfvars, then:
cd infra/aws-agent-isolation && tofu apply
```

VERIFY: `aws elbv2 describe-target-health` shows the broker target healthy
(`/healthz`). Unit suite green: `python -m pytest infra/aws-agent-isolation/broker/tests -q`.

ROLLBACK: `desired_count = 0` on the broker service (or destroy). No live origin
points at it yet.

---

## Step 4 -- PROVISION-ON-CONNECT + READINESS (dark)

Confirm the broker's RunTask + `:8766` health-gate + route-write works end-to-end
against a real task, driven over the SEPARATE broker hostname.

```
# manual smoke: hit the broker hostname with a test Cognito token + a fresh sid;
# watch a task appear, go health-green, and a route row get written:
aws dynamodb scan --table-name grace2_session_routes --region us-west-2
aws ecs list-tasks --cluster grace2-agents --region us-west-2
```

VERIFY: one route row, one RUNNING task, the WS turn completes through the proxy.

ROLLBACK: `aws ecs stop-task` the smoke task; `aws dynamodb delete-item` the row.

---

## Step 5 -- CANARY ONE NON-DEFAULT ROUTE + the ISOLATION PROOF

Drive a FULL live session (NATE's `claude.e2e` account) through broker->task on
the separate hostname: dual-socket App+Chat converge on ONE task, a turn + an
SFINCS Batch solve + publish, reconnect/12s-heartbeat all green.

**THE ISOLATION PROOF (the direct incident fix):** run TWO concurrent sessions,
then FORCE-CRASH one task and confirm the OTHER session is untouched.

```
# session A and session B each on their own task:
aws ecs list-tasks --cluster grace2-agents --region us-west-2     # expect 2 tasks
# crash session A's task:
aws ecs stop-task --cluster grace2-agents --task <taskArn-A> --reason "isolation proof" --region us-west-2
# B's turn must keep streaming; B's task stays RUNNING; A reconnects -> new task.
aws ecs describe-tasks --cluster grace2-agents --tasks <taskArn-B> --region us-west-2  # lastStatus RUNNING
```

PASS CRITERIA: killing A's task does NOT interrupt B's in-flight turn or
heartbeat (the cross-user blast-radius isolation the single box could not give).

ROLLBACK: stop both canary tasks; traffic is still 100% on the live box.

---

## Step 6 -- ARM THE PER-TASK IDLE REAPER

Flip `reaper_dry_run = false` and confirm an idle canary task is StopTask'd after
`IDLE_THRESHOLD_CHECKS` not-busy ticks, with the G3 Batch guard + the Stage-3
idle-open-tab rule honored.

```
# flip in terraform.tfvars: reaper_dry_run = false
cd infra/aws-agent-isolation && tofu apply -target=aws_lambda_function.reaper
# leave a canary task idle ~15 min; confirm it stops + the route row is deleted:
aws logs tail /aws/lambda/grace2-agent-task-reaper --region us-west-2 --follow
```

VERIFY: a BUSY task (in-flight turn OR an in-flight `grace2-solvers` job) is NOT
stopped; an idle task IS, after the streak. Keep the single-box autostop ARMED as
the fallback.

ROLLBACK: `reaper_dry_run = true` + re-apply (back to log-only).

---

## Step 7 -- CUT THE CLOUDFRONT /ws ORIGIN OVER  *** NATE GO REQUIRED ***

Repoint CloudFront `E2L74AS56MVZ87` `/ws*` from `EC2:8765` to the broker ALB
origin. The client URL (`wss://d125yfbyjrpbre.cloudfront.net/ws`) is UNCHANGED.

```
# add the ALB as an origin + repoint the /ws* cache behavior to it:
aws cloudfront get-distribution-config --id E2L74AS56MVZ87 > cf.json
# (edit: new origin = the broker ALB DNS; /ws* behavior -> that origin)
aws cloudfront update-distribution --id E2L74AS56MVZ87 \
  --distribution-config file://cf-updated.json --if-match <ETag>
aws cloudfront create-invalidation --distribution-id E2L74AS56MVZ87 --paths "/ws*"
```

VERIFY (live): existing sessions reconnect through the broker; new connects route
to per-session tasks. Watch ws-recv/ws-close telemetry + the route table.

ROLLBACK (instant): re-point `/ws*` back to `EC2:8765` + invalidate. The live box
is still running (not decommissioned until step 8), so this is a one-call revert.

---

## Step 8 -- DRAIN + DECOMMISSION THE SINGLE BOX

Once per-session routing is proven over a real demo window, stop routing to the
box, retire its single-box `idle_check`/`wake` Lambdas (or repurpose `wake` as the
broker's provisioner), and downsize/STOP (do NOT terminate yet) the box so it
stays an instant rollback.

```
# confirm zero traffic on the box (no /ws origin points at it), then STOP it
# (keep it startable as rollback -- do NOT terminate during the bake window):
aws ec2 stop-instances --instance-ids i-0251879a278df797f --region us-west-2
# disable the single-box autostop schedule (it has nothing to stop now):
# (infra/aws-autostop: aws events disable-rule ...) -- keep the module for rollback.
```

TiTiler tiny box + the Batch island + DynamoDB + S3 stay exactly as-is.

ROLLBACK: `aws ec2 start-instances` the box + re-point CloudFront `/ws*` to it
(step 7 rollback). Full revert to the shared-box path.

---

## Step 9 -- COST + ISOLATION VERIFY

Confirm steady-state agent cost ~unchanged at current load (mostly zero tasks),
the new broker/ALB line item is acceptable, and per-session blast-radius
isolation holds under 2+ concurrent sessions. Log the workflow cost per the
cost_tracking norm.

```
aws ce get-cost-and-usage ...    # the ALB + Fargate-task line items vs the old box
aws ecs list-tasks --cluster grace2-agents --region us-west-2   # mostly 0 at idle
```

---

## (LATER, post-Fargate) AGENTCORE SPIKE

Behind the stable isolated baseline, run the spike section-6.4 gates
A (ARM64 <=2GB image) -> B (<=2vCPU/8GB in-loop) -> C (the WS re-architecture
proven against a >60-min solve with hourly same-session_id reconnect). If all
pass, cut the agent loop to AgentCore Runtime and retire the broker/registry/
reaper (AWS owns isolation). Lock-in is LOW, so this is reversible.
