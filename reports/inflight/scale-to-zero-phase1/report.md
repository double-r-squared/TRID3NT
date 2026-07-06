# Scale-to-zero Phase 1 — heartbeat reaper + interface-endpoint deletion

Date: 2026-07-06
Owner: orchestrator (NATE blanket go: "Go and proceed to next phases without my go")
Blueprint: reports/design/scale-to-zero-architecture-2026-07-04.md (section 2.3)
Savings: ~$29/mo (2x Interface VPC endpoints @ ~$14.40/mo, us-west-2), on top of Phase 0's ~$6/mo.

## What changed

The reaper Lambda previously ran IN the VPC to probe each agent task's
/api/health on its private ENI IP — which forced the ECS + Batch Interface
endpoints (~$29/mo) to exist so the in-VPC Lambda could reach the control
plane. Phase 1 inverts the flow: the agent self-reports liveness into its
own grace2_session_routes row, so the reaper needs only DynamoDB + the
public ECS/Batch APIs and runs outside the VPC.

1. **Agent heartbeat writer** (c776178): every 60s (GRACE2_ROUTE_HEARTBEAT_SECONDS,
   piggybacked on the 30s idle-exit loop) the agent writes hb_last_seen /
   hb_busy / hb_active_connections / hb_inflight_batch to its route row.
   Dormant unless the env var is set. Route key from GRACE2_ROUTE_USER_ULID +
   GRACE2_ROUTE_SESSION_ID, injected by the broker at RunTask (routing.py).
2. **Reaper 3-mode health** (68f3f37): REAPER_HEALTH_MODE=probe|heartbeat|both.
   heartbeat mode reads hb_* (stale heartbeat = unreachable); per-session
   Batch guard via hb_inflight_batch (fixes the old G3 global pin). 30 unit
   tests. `both` = parallel validation, acts on probe, logs AGREE/DISAGREE.
3. **Rollout sequence** (each step gated by the judge-code Haiku flood smoke):
   - task-def rev 14 (heartbeat env) + broker redeploy -> smoke PASS, hb_*
     fields live (hb_busy=true + hb_inflight_batch=1 observed mid-solve).
   - REAPER_HEALTH_MODE=both validation window: AGREE on busy sessions; one
     transient DISAGREE (probe_busy=False / hb_busy=True) self-resolved
     within a minute — fail-safe direction (keeps sessions alive longer).
   - terraform.tfvars reaper_health_mode = "heartbeat" applied.
   - vpc_config removed from aws_lambda_function.reaper (VpcConfig confirmed
     empty); non-VPC tick verified clean (348ms, heartbeat decisions, orphan
     enumeration via the public ECS API).
   - Interface endpoints destroyed plan-first ("Plan: 0 to add, 0 to change,
     2 to destroy"); batch endpoint state confirmed "deleted".
   - Dead SGs removed from IaC + destroyed (plan showed exactly 3):
     aws_security_group.vpce, aws_security_group.reaper,
     aws_security_group_rule.agent_ingress_health_from_reaper.
   - Final `tofu plan`: **No changes.**
4. **Final acceptance**: judge-code flood smoke PASS end-to-end
   (sfincs_pluvial_flood, case 01KWVHMND6M40EWZ20KVXGEKYP, 391.7s), and
   post-cleanup reaper ticks clean at 11:05/11:10/11:15Z — heartbeat
   decisions, stale routes dropped as tasks self-idle-exit, no errors.

## Side quest (build unblock)

USGS GitLab began 403ing git clones of pfdf mid-rollout, breaking agent+broker
CodeBuild. Fixed by vendoring the pfdf 3.0.4 wheel (built from the local uv git
cache) into services/agent/vendor/ + --find-links in both Dockerfiles
(f8d323e, 6641f1e, 34e85d9).

## Rollback

Probe mode rollback = re-add vpc_config to reaper.tf + recreate the ECS/Batch
interface endpoints + the vpce/reaper SGs; all blocks preserved as comments or
in git history (c776178^..HEAD). reaper_health_mode variable flips modes with
no code change.

## Follow-ups

- **hb_busy oscillation**: idle sessions sometimes report hb_busy=true with 0
  connections (stale-busy window). Fail-safe (delays reaping), and bounded by
  the 30-min agent self-idle-exit + 90-min max-age backstop, but worth a
  tuning pass on the busy signal.
- Phase 2 next: broker onto the TiTiler box (systemd :8081), flip CloudFront
  /ws* origin, delete ALB + broker Fargate service (~$32/mo).
