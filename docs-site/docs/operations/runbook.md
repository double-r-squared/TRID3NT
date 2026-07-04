# Operations Runbook

---

## Ops health check

**Script:** `scripts/ops_health_check.sh`
**Automated:** hourly cron dispatches a Sonnet detector; WARN/CRIT anomalies written to
`reports/ops-anomalies/anomaly-*.md`. Also: a 24/7 Lambda watchdog (`grace2-ops-watchdog`,
EventBridge 15-min schedule) does continuous monitoring.

**Exit codes:** 0=OK, 1=WARN, 2=CRITICAL

The script runs 7 checks:

| Check | WARN condition | CRIT condition |
|---|---|---|
| Orphan session pile-up | running ECS tasks significantly exceed live DynamoDB route rows | -- |
| Fargate vCPU headroom | estimated active sessions approaching 64-vCPU quota (~2 vCPU/session) | -- |
| Reaper armed | Lambda `grace2-agent-task-reaper` has `DRY_RUN=true` | -- |
| Broker ALB health | no healthy targets in any "broker" target group | -- |
| TiTiler / CloudFront reachable | `GET https://d125yfbyjrpbre.cloudfront.net/healthz` non-200 | -- |
| Batch stuck jobs | > 8 jobs running on `grace2-solvers` | -- |
| Agent fallback box state | EC2 `i-0251879a278df797f` is `running` (should be stopped) | -- |

---

## Common failure modes

### Stale route row after agent crash

**Symptom:** client connects successfully (broker returns 101), but all frames time out; no
agent response.

**Cause:** the agent task crashed; the route row in `grace2_session_routes` still has the old
private IP; broker dials a dead endpoint.

**Fix:** wait up to 5 min for the next reaper tick to clean the stale row, then reconnect. Or
manually delete the route row:
```bash
aws dynamodb delete-item \
  --table-name grace2_session_routes \
  --key '{"user_ulid": {"S": "<user_ulid>"}, "session_id": {"S": "<session_id>"}}' \
  --region us-west-2
```

---

### TiTiler wedge (tiles time out, agent healthy)

**Symptom:** map layers published successfully but tiles return 504/502; agent is running normally.

**Cause:** TiTiler Uvicorn process has wedged (GDAL `/vsis3` deadlock or connection exhaustion).
Known issue from `job-0314`; a watchdog restart loop runs on the TiTiler box.

**Fix (usually automatic):** the watchdog should restart the process within 60 s. If not:
```bash
# SSM session to TiTiler box i-06cfdd3d6c66b2126
aws ssm start-session --target i-06cfdd3d6c66b2126 --region us-west-2
# On box:
sudo systemctl restart titiler
```

---

### Batch RUNNABLE stall

**Symptom:** solver jobs stuck in `RUNNABLE` state for > 10 min; no agents start.

**Cause:** Spot CE cannot place instances (regional capacity shortage). The on-demand CE order-2
should pick up the job automatically, but may be slow.

**Fix:** verify the instance-type pool is broad (20 types x 4 sizes x 4 AZs). If stall persists,
temporarily create an on-demand-only queue and clone the job submission:
```bash
# Force on-demand for one run (temp queue method -- see project memory for details)
aws batch create-compute-environment --type MANAGED --state ENABLED \
  --compute-resources type=EC2,... --region us-west-2
```

After the run, disable + delete the temp queue.

---

### WS 1005 / heartbeat class issues

**Symptom:** WebSocket closes with code 1005 or 1006; client shows "reconnecting" repeatedly;
prompts appear to do nothing.

**Cause class:** either (a) the agent's 12 s DATA heartbeat stopped (agent process issue), or (b)
a heavy sync tool is running on the asyncio loop (blocking the heartbeat coroutine).

**Diagnosis:**
```bash
# Check agent task logs in CloudWatch
aws logs tail /ecs/grace2-agent-session --follow --region us-west-2
```

Look for: `heartbeat sent` every ~12 s. Absence = heartbeat blocked. Also look for sync tools
not in `_ALWAYS_OFFLOAD_SYNC_TOOLS` taking > 5 s.

**Fix (code):** any sync tool that can block > 1 s must be in `_ALWAYS_OFFLOAD_SYNC_TOOLS` or
use `asyncio.to_thread`. The norm: never run sync boto3/file/network/compute on the agent loop.

---

### Session orphan leak

**Symptom:** many Fargate tasks running but `grace2_session_routes` has few live rows; ECS vCPU
quota approaching 64; Batch jobs not starting.

**Cause:** reaper not running or `DRY_RUN=true`; agent tasks not self-exiting.

**Fix:**
```bash
# Check reaper state
aws lambda get-function-configuration \
  --function-name grace2-agent-task-reaper \
  --region us-west-2 \
  --query 'Environment.Variables'

# If DRY_RUN=true, arm it:
aws lambda update-function-configuration \
  --function-name grace2-agent-task-reaper \
  --environment Variables={DRY_RUN=false,...} \
  --region us-west-2

# List running session tasks
aws ecs list-tasks \
  --cluster grace2-agents \
  --family grace2-agent-session \
  --region us-west-2
```

Reference: session-agent orphan leak root cause was 37 orphan tasks consuming 63/64 vCPU
(`project_session_agent_orphan_leak.md` in project memory).

---

### Code-gate demo token fails

**Symptom:** demo access code accepted by the code-gate UI but `demo-token` Lambda returns 4xx.

**Cause:** ephemeral Cognito user creation failing, or access code mismatch after trimming.

**Demo token route:** `POST /demo-token` (API GW) -> Lambda -> creates ephemeral Cognito user in
pool `us-west-2_mIpKrr727` -> returns short-lived JWT.

The access code is `trident-demo-4db31803` (trim client + server; trailing whitespace causes mismatch).

---

## Cost model

| State | $/mo | Notes |
|---|---|---|
| Idle (current) | ~$92 | ALB + broker Fargate + VPC endpoints + TiTiler + legacy box |
| Idle (target, post migration) | ~$22-25 | Single t3.small for tiles + broker (Phase 0-2 complete) |
| Per active session | ~$0.34/hr | 2 vCPU / 8 GB Fargate agent; target ~$0.085/hr at 4 GB |
| Per solver run | Spot price | Batch Spot ~$0.04-0.08/hr per vCPU; zero between jobs |
| Bedrock LLM | Per-token | Sonnet default; Haiku ~10x cheaper for smoke tests |
| S3 / DynamoDB / CloudFront | ~$2-5/mo | On-demand; effectively zero at low traffic |

See `reports/design/scale-to-zero-architecture-2026-07-04.md` Table 1.3 for the full idle breakdown.

---

## Key resource IDs

| Resource | ID / ARN |
|---|---|
| CloudFront distribution | `E2L74AS56MVZ87` (`d125yfbyjrpbre.cloudfront.net`) |
| TiTiler EC2 | `i-06cfdd3d6c66b2126` (t3.small, EIP `44.247.187.124`) |
| Legacy agent EC2 (stopped) | `i-0251879a278df797f` (t3.xlarge, EIP `54.185.114.233`) |
| ECS cluster | `grace2-agents` |
| Agent task family | `grace2-agent-session` |
| Reaper Lambda | `grace2-agent-task-reaper` |
| Ops watchdog Lambda | `grace2-ops-watchdog` |
| Batch queue | `grace2-solvers` |
| Cognito pool | `us-west-2_mIpKrr727` |
| API GW | `9ib093sis6` |
| Route table | `grace2_session_routes` (DynamoDB) |
