# RUNBOOK — agent-box auto-stop/wake (`infra/aws-autostop`)

Self-contained OpenTofu root that makes the always-on agent EC2 box
(`i-0251879a278df797f`, t3.large, us-west-2, EIP 54.185.114.233, fronted by
CloudFront `E2L74AS56MVZ87`) **stop itself when idle** and **wake on demand**.

It provisions:

| Resource | Purpose |
|---|---|
| `grace2-autostop-idle-check` Lambda | Polls the agent `/api/health`; stops the box after N consecutive idle checks. |
| `grace2-autostop-idle-check-schedule` EventBridge rule | Fires the idle Lambda on `rate(5 minutes)`. |
| `grace2-autostop-wake` Lambda | `StartInstances` on the one agent instance. |
| `grace2-autostop-wake-api` API Gateway HTTP API | Public `ANY /wake` endpoint → wake Lambda (CORS-open, unauthenticated). |
| `grace2-autostop-state` DynamoDB table | Consecutive-idle streak (one item per instance). |
| Two IAM roles + inline policies | Least-privilege: Stop/Start **scoped to the instance ARN**; Describe/ListJobs read-only; DynamoDB get/put; Logs. No Terminate anywhere. |

The auto-stop logic is **bulletproof by construction**: the idle Lambda stops
the box ONLY when EVERY guard passes (instance `running` **AND** health
`busy==false` **AND** `active_connections==0` **AND** no in-flight Batch solve
**AND** `IDLE_THRESHOLD_CHECKS` consecutive idle polls). Any busy signal — a live
WebSocket connection, a detached in-flight turn (`busy==true`), an in-flight
Batch solve, or an unreachable/old health endpoint — **resets the streak to
zero**. See `lambda/idle_check/handler.py` for the per-guard rationale.

---

## Prerequisites (NATE's interactive steps — agents must NOT script these)

1. **AWS SSO login** (the apply credential):
   ```
   aws sso login            # or: aws configure sso  (first time)
   aws sts get-caller-identity   # confirm account 226996537797
   ```
2. **OpenTofu** ≥ 1.8 on PATH (`tofu version`).
3. The agent change that adds `active_connections` + `busy` to `/api/health`
   must be DEPLOYED to the box first (services/agent — `liveness_snapshot`).
   Until it ships, the idle Lambda sees the **old** `{"ok":true}` body, treats
   it as **busy** (fail-safe), and never stops the box — safe but a no-op.

---

## Apply steps (orchestrator)

> Do this from `infra/aws-autostop/`. The first apply leaves `dry_run=true`
> (default) so the idle Lambda **logs** its stop decision but never calls
> `StopInstances` — validate the decision against the live box before arming.

```bash
cd infra/aws-autostop

# 1. Init against the S3 backend (writes tofu-state/aws-autostop.tfstate).
tofu init

# 2. Review the plan. Expect ~14 resources, ALL new (zero changes to existing).
#    Confirm: no aws_instance is being created/modified (it is a data source).
tofu plan -out=tfplan

# 3. Apply the plan.
tofu apply tfplan

# 4. Read the wake endpoint URL for the web build:
tofu output -raw wake_endpoint_url
#    -> set the web build's VITE_GRACE2_WAKE_URL to this value, rebuild + redeploy
#       web (see infra notes: web build needs .env.local; AWS deploy facts memo).
```

### Validate in DRY-RUN (before arming auto-stop)

```bash
# Manually invoke the idle Lambda and read its decision (no stop happens — dry_run).
aws lambda invoke --function-name "$(tofu output -raw idle_check_function_name)" \
  --region us-west-2 /dev/stdout | tail -1
# Expect e.g. {"action":"noop","reason":"busy",...} while a tab is open, or
# {"action":"stop_dryrun",...} after IDLE_THRESHOLD_CHECKS idle invokes.

# Watch the scheduled runs:
aws logs tail "/aws/lambda/grace2-autostop-idle-check" --region us-west-2 --follow
```

### Arm auto-stop (after dry-run looks correct)

```bash
# Flip dry_run off and re-apply.
tofu apply -var='dry_run=false'
```

### Test wake

```bash
WAKE=$(tofu output -raw wake_endpoint_url)
curl -s -X POST "$WAKE"        # {"state":"running",...} or {"state":"starting","started":true}
```

---

## Credentials / auth the apply needs

- **AWS SSO session** for account `226996537797` in `us-west-2` (NATE's
  `aws sso login`). The provider resolves it from the default credential chain.
- **S3 backend write** to `grace2-hazard-runs-226996537797`
  (`tofu-state/aws-autostop.tfstate`) — same bucket the Batch module uses.
- No GitHub / Atlas / gcloud auth involved.

---

## Tuning (no HCL edits — pass `-var` or a tfvars file)

| Variable | Default | Meaning |
|---|---|---|
| `idle_threshold_checks` | `3` | Consecutive idle polls before stop (×5 min ≈ 15 min). |
| `schedule_expression` | `rate(5 minutes)` | How often the idle check runs. |
| `health_url` | `http://54.185.114.233:8766/api/health` | What the idle Lambda polls. Switch to the CloudFront `/api/health` once routed. |
| `batch_queues` | `grace2-solvers` | Batch queues checked for in-flight solves; `""` disables the guard. |
| `dry_run` | `false`* | When true, log the stop decision but never stop. *Default in this RUNBOOK's first apply is to pass `dry_run=true` for validation. |

---

## Rollback / teardown

```bash
tofu destroy   # removes schedule, Lambdas, API, DynamoDB, IAM — NEVER the instance
```

Destroying this module returns the box to always-on (the EC2 instance is a data
source here; it is never touched by stop/start outside the Lambdas, which are
gone after destroy).

---

## Notes / gotchas

- **Lambda reachability to the EIP**: the Lambdas run OUTSIDE a VPC by default,
  so they reach the EIP / CloudFront over the public internet — no SG ingress
  rule for the Lambda is needed when using the public `health_url`. If you ever
  move the agent behind a private endpoint, attach the idle Lambda to the VPC
  and open the SG to its subnet.
- **EventBridge is at-least-once.** A duplicate invocation in the same minute
  can only advance the idle streak faster by the duplicate count, which makes
  auto-stop SLOWER, never wrongly faster — the stop is still gated on the full
  threshold of confirmed-idle ticks.
- **`source_arn` on the wake Lambda permission** restricts invokes to this API's
  executions; do not widen it.
- The wake endpoint is intentionally **unauthenticated + CORS `*`**: it can only
  `StartInstances` on one hard-coded box (no stop/terminate/other-resource), and
  the browser must call it before a session exists. Abuse ceiling = the box
  starts (then idle-check stops it again); no data exposure.
