# grace2-ops-watchdog

Server-side ops watchdog for the GRACE-2 / TRID3NT AWS stack. Replicates
the 7 probes in `scripts/ops_health_check.sh` as a scheduled Lambda so
problems are caught even when Claude Code is closed.

## What it does

Every 15 minutes EventBridge invokes `grace2-ops-watchdog` (Python 3.12,
30 s, 128 MB). It runs 7 read-only probes:

| # | Probe | Severity |
|---|-------|----------|
| 1 | Orphan ECS task pile-up (`running > routes + delta`) | CRITICAL / WARN |
| 2 | Fargate vCPU headroom (`running*2 >= 75% of quota`) | CRITICAL |
| 3 | Reaper `DRY_RUN=true` (orphans not being stopped) | WARN |
| 4 | Broker ALB -- 0 healthy targets | CRITICAL |
| 5 | CloudFront edge reachable (urllib, 8 s) | WARN |
| 6 | Batch solver queue > 8 RUNNING jobs (runaway guard) | WARN |
| 7 | Agent fallback EC2 box unexpectedly running | WARN |

On WARN or CRITICAL the Lambda publishes to SNS topic `grace2-ops-alerts`,
which emails NATE a message listing every flagged check with numbers and a
remediation hint. On OK it publishes nothing (silent, near-zero cost).

Full run logs (every invocation, regardless of status) go to
`/aws/lambda/grace2-ops-watchdog` (30-day retention).

## How to apply

```sh
cd infra/aws-ops-watchdog
tofu init
tofu plan          # review the 9 resources
tofu apply
```

## Email confirmation (required before alerts work)

After `tofu apply`, AWS sends a confirmation email to `natealmanza3@gmail.com`
from `AWS Notifications`. Click the "Confirm subscription" link. Until you
click it the subscription is PENDING and alerts will not be delivered.

## Tune thresholds / schedule

All thresholds are Lambda environment variables set from Tofu variables.
Override them in `terraform.tfvars` (no code edit needed):

```hcl
# terraform.tfvars
schedule_rate    = "rate(5 minutes)"   # check more often
vcpu_crit_pct    = 80                  # raise the vCPU alarm threshold
batch_warn_jobs  = 12                  # allow more concurrent solver jobs
log_retention_days = 7                 # shorter log retention
```

Then `tofu apply` to push the new values.

## Disable the watchdog

Disable the EventBridge rule to stop all invocations without destroying
infrastructure:

```sh
aws events disable-rule --name grace2-ops-watchdog-schedule --region us-west-2
# re-enable:
aws events enable-rule  --name grace2-ops-watchdog-schedule --region us-west-2
```

Or set `schedule_rate = "rate(999 days)"` in tfvars + `tofu apply`.

## Manual test

```sh
aws lambda invoke --function-name grace2-ops-watchdog \
  --region us-west-2 \
  --payload '{}' /tmp/watchdog-out.json && cat /tmp/watchdog-out.json
```

## State

This module uses local state (`terraform.tfstate` in this directory).
It is independent of `infra/aws-agent-isolation` -- no shared state,
no imports, no cross-module references.
