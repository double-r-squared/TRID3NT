# Scale-to-zero Phase 0 -- legacy deletion (EXECUTED 2026-07-06)

Blueprint: reports/design/scale-to-zero-architecture-2026-07-04.md section 2.6 Phase 0.
Operator: Claude (NATE's explicit go: "Go and proceed to next phases without my go",
after the itemized mutation list was presented and approved).

## Baseline (before any mutation)
- Cloud flood smoke (judge-code headless driver, Haiku, small-AOI SFINCS pluvial):
  PASS -- run 01KWVCPYV0XFTJ510P5M64SND0, publish_manifest offloaded, 389s wall.
- Cold paths: /case-list 200, cold tool catalog 200.

## Mutations (in order, each verified)
1. CloudFront E2L74AS56MVZ87: removed origin `origin-agent-ws` (ZERO referencing
   behaviors -- verified against the full behavior map first). `origin-catalog`
   KEPT deliberately: /api/* still references it (dead since the box stopped;
   identical 502 semantics; scheduled for deliberate handling in Phase 2).
   Backup: reports/inflight/scale-to-zero-phase0/cloudfront-backup.json.
   Rollback: restore backed-up config. Status after: Deployed.
2. EventBridge rule grace2-autostop-idle-check-schedule: DISABLED (not deleted --
   re-enable to roll back). Was polling the stopped box 288x/day.
3. Old agent box decommission:
   - Snapshot snap-0340dec7951fd0bb3 (30 GiB, "grace2-old-agent-box-final") COMPLETED
   - i-0251879a278df797f TERMINATED
   - EIP 54.185.114.233 (grace2-agent-eip) RELEASED (not recoverable as same IP;
     nothing references it -- origin removed in step 1, wake Lambda StartInstances
     on the terminated id now fails fast as a best-effort no-op client-side)
   - vol-09c517faae584abff auto-deleted via delete_on_termination (verified NotFound)
   - Rollback: launch replacement from the snapshot (new IP; CloudFront origin would
     need re-adding)
4. S3 Gateway endpoint vpce-028824a46432f1fa4 ADDED via IaC
   (infra/aws-agent-isolation/vpc_endpoints.tf aws_vpc_endpoint.s3, targeted apply).
   FREE; S3 traffic from TiTiler/agents/workers now rides the AWS backbone.
   Rollback: tofu destroy -target=aws_vpc_endpoint.s3.

## Explicitly NOT touched
Broker, ALB, ECS/Batch interface endpoints (Phases 1-2), all serverless demo
Lambdas (case-list/view-sign/export/demo-token/wake), DynamoDB tables (incl.
grace2_* orphans -- zero fixed cost, no reward for deletion risk), trid3nt-local.

## Post-change verification
- Cold paths: case-list 200, catalog 200, web-via-CloudFront 200, tiles healthz 200.
- CloudFront: Deployed.
- Post-change flood smoke: (appended below on completion)

## Savings realized
- EIP + EBS dead weight: ~$6/mo
- Wake-trap removed (a stray Wake tap can no longer start a t3.xlarge)
- Idle-check Lambda noise stopped (288 wasted invocations/day)
- S3 egress via free gateway endpoint

## Next: Phase 1 (heartbeat reaper -> delete ECS+Batch interface endpoints, ~$29/mo)
