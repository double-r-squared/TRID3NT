# Scale-to-zero Phase 2 — broker onto the TiTiler box, ALB deleted

Date: 2026-07-06
Owner: orchestrator (NATE explicit go: "Your are allowed to proceed with the next step" after the itemized box-install checkpoint)
Blueprint: reports/design/scale-to-zero-architecture-2026-07-04.md (section 2.2)
Savings: ~$32/mo (ALB ~$20 + broker Fargate task ~$12), on top of Phase 0 (~$6) + Phase 1 (~$29).

## What changed

The always-on Fargate broker service behind an internet-facing ALB is replaced
by the SAME grace2-broker:latest image running as a docker/systemd unit on the
always-on TiTiler box (t3.small, i-06cfdd3d6c66b2126), port 8081. CloudFront
/ws* now targets the box directly (origin-broker-box). Zero new always-on
compute — the broker rides the box that already had to exist for tiles.

## Sequence (each mutation gated)

1. **Grants (additive IaC, broker_on_box.tf)**: broker task policy + ECR read
   grafted onto grace2-titiler-ec2-role; agent-SG ingress 8765/8766 from the
   titiler SG; new grace2-broker-box SG (:8081 from the CloudFront origin-facing
   prefix list only) attached to the box ENI. Gotcha: the prefix list weighs
   ~55 entries against the 60-rules-per-SG quota, so the :8081 rule needed its
   OWN SG (RulesPerSecurityGroupLimitExceeded on the titiler SG).
2. **Box install (SSM grace2-runshell, NATE-approved)**: 2G swapfile, docker,
   ECR pull (493MB image, today's heartbeat build), /etc/grace2/broker.env
   (mirror of the Fargate task env, BROKER_PORT=8081), grace2-broker.service
   (docker run --network host --memory 700m). healthz OK; TiTiler unaffected
   (active, 200, ~930MB available after).
3. **CloudFront**: added origin-broker-box (box EIP DNS, HTTP :8081, same
   timeouts as the ALB origin) + flipped the /ws* behavior TargetOriginId.
   Backup of the pre-flip config: reports/inflight/scale-to-zero-phase2/
   cf-config-phase2-backup.json.
4. **Proof**: judge-code Haiku flood smoke PASS end-to-end through the box
   broker (sfincs_pluvial_flood, case 01KWVYYJBDM3NSJE299GKT3CR5, 318.0s).
   Box logs show the full broker lifecycle: first-connect user provisioning,
   route MISS -> RunTask, proxy to the agent task private IP, clean teardown.
5. **Teardown (plan-first, exactly 8)**: ECS service, ALB, HTTP listener,
   target group, ALB SG, broker Fargate SG + its two agent-SG ingress rules.
   `tofu plan` clean after. Kept as rollback artifacts: the broker task
   DEFINITION, log group, task role, ECR repo, CodeBuild project.
6. **Final gate**: post-teardown flood smoke (see addendum below).

## Rollback

Minutes-fast: flip CloudFront /ws* back to... the ALB is gone, so full rollback
= git revert alb.tf/broker.tf/ecs.tf/outputs.tf + apply (~5 min) + flip the
CloudFront origin back to origin-broker-ws. Fast mitigation for a box-broker
problem alone: `systemctl restart grace2-broker` on the box (SSM).

## Notes / follow-ups

- The box is now dual-role (TiTiler + broker). Memory is the watch item:
  ~1.9GB total, broker capped at 700m, 2G swap as the shock absorber.
- origin-catalog (/api/*) still points at the TERMINATED old agent box DNS
  (ec2-54-185-114-233) — dead before Phase 2, unchanged by it. Deliberate
  deferral: repoint /api/* (likely to the box or a Lambda) in a later step.
- Deploying a NEW broker build now = push image + `docker pull` + restart unit
  on the box (SSM), not an ECS rollout. Update deploy docs when the flow next
  runs.
- Remaining blueprint phases: 3 (client frame-queueing) + 4 (agent 8->4GB).

## Addendum — post-teardown smoke

PASS: sfincs_pluvial_flood, case 01KWVZMKBRRB5N6WVHW76P08WA, 355.3s
(manifest 01KWVZQA8TQW5CGNR8VCV7NDDY/publish_manifest.json). ALB deletion had
no effect on the live path.
