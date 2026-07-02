#!/bin/bash
# ops_health_check.sh -- fast read-only probe of the GRACE-2 / TRID3NT live stack.
# Emits per-check status + an overall verdict; exit 0=OK, 1=WARN, 2=CRITICAL.
# Designed to be cheap (read-only describes + short curls) so a cron can run it often
# and only escalate to a full investigation when it reports WARN/CRITICAL.
set +e
R=${GRACE2_REGION:-us-west-2}
CLUSTER=grace2-agents
FAMILY=grace2-agent-session
ROUTES=grace2_session_routes
SOLVER_Q=grace2-solvers
REAPER=grace2-agent-task-reaper
AGENT_BOX=i-0251879a278df797f
TITILER_BOX=i-06cfdd3d6c66b2126
CF=https://d125yfbyjrpbre.cloudfront.net
VCPU_QUOTA=64

WARN=0; CRIT=0
issue(){ # sev msg
  if [ "$1" = "CRIT" ]; then CRIT=$((CRIT+1)); else WARN=$((WARN+1)); fi
  echo "  [$1] $2"
}
echo "=== GRACE-2 ops health check $(date -u +%FT%TZ) region=$R ==="

# 1) Orphan session-task pile-up (the $190 leak) -----------------------------
RUNNING=$(aws ecs list-tasks --cluster $CLUSTER --family $FAMILY --desired-status RUNNING --region $R --query 'length(taskArns)' --output text 2>/dev/null); RUNNING=${RUNNING:-0}
ROUTECT=$(aws dynamodb scan --table-name $ROUTES --select COUNT --region $R --query 'Count' --output text 2>/dev/null); ROUTECT=${ROUTECT:-0}
echo "1) session tasks RUNNING=$RUNNING  live routes=$ROUTECT"
if [ "$RUNNING" -gt 6 ] && [ "$RUNNING" -gt $((ROUTECT + 3)) ]; then
  issue CRIT "orphan pile-up suspected: $RUNNING running vs $ROUTECT routes (leak? check reaper)"
elif [ "$RUNNING" -gt $((ROUTECT + 2)) ]; then
  issue WARN "more running session tasks ($RUNNING) than routes ($ROUTECT) -- watch for orphans"
fi

# 2) Fargate vCPU headroom (session tasks ~2 vCPU each) ----------------------
VCPU_EST=$((RUNNING * 2))
echo "2) est session vCPU in use ~$VCPU_EST / quota $VCPU_QUOTA"
if [ "$VCPU_EST" -ge $((VCPU_QUOTA * 75 / 100)) ]; then issue CRIT "Fargate vCPU near quota (~$VCPU_EST/$VCPU_QUOTA) -- new sessions may 4401"; fi

# 3) Reaper armed + last run OK ---------------------------------------------
DRYRUN=$(aws lambda get-function-configuration --function-name $REAPER --region $R --query 'Environment.Variables.DRY_RUN' --output text 2>/dev/null)
echo "3) reaper DRY_RUN=$DRYRUN"
[ "$DRYRUN" = "true" ] && issue WARN "reaper is DRY_RUN=true -- orphans will NOT be reaped"

# 4) Broker ALB target health ----------------------------------------------
TG=$(aws elbv2 describe-target-groups --region $R --query "TargetGroups[?contains(TargetGroupName,'broker')].TargetGroupArn|[0]" --output text 2>/dev/null)
if [ -n "$TG" ] && [ "$TG" != "None" ]; then
  HEALTHY=$(aws elbv2 describe-target-health --target-group-arn "$TG" --region $R --query "length(TargetHealthDescriptions[?TargetHealth.State=='healthy'])" --output text 2>/dev/null)
  echo "4) broker healthy targets=$HEALTHY"
  [ "${HEALTHY:-0}" -lt 1 ] && issue CRIT "broker has 0 healthy ALB targets -- /ws down"
else echo "4) broker target group not found (skip)"; fi

# 5) TiTiler tiles reachable (always-on render path) ------------------------
TCODE=$(curl -s -o /dev/null -m 8 -w '%{http_code}' "$CF/healthz" 2>/dev/null)
[ "$TCODE" = "000" ] && TCODE=$(curl -s -o /dev/null -m 8 -w '%{http_code}' "$CF/" 2>/dev/null)
echo "5) CloudFront edge HTTP=$TCODE"
[ "$TCODE" = "000" ] && issue WARN "CloudFront edge unreachable from here (may be network-local)"

# 6) Batch: stuck or recently failed solver jobs ---------------------------
RUN_JOBS=$(aws batch list-jobs --job-queue $SOLVER_Q --job-status RUNNING --region $R --query 'length(jobSummaryList)' --output text 2>/dev/null); RUN_JOBS=${RUN_JOBS:-0}
echo "6) Batch RUNNING solver jobs=$RUN_JOBS"
if [ "$RUN_JOBS" -gt 8 ]; then issue WARN "unusually many Batch jobs running ($RUN_JOBS) -- runaway submit?"; fi

# 7) Agent fallback box unexpectedly running (cost) ------------------------
BOXSTATE=$(aws ec2 describe-instances --instance-ids $AGENT_BOX --region $R --query 'Reservations[0].Instances[0].State.Name' --output text 2>/dev/null)
echo "7) agent fallback box ($AGENT_BOX) state=$BOXSTATE"
[ "$BOXSTATE" = "running" ] && issue WARN "agent fallback box is RUNNING (Fargate broker is primary; box should be stopped -- \$ drip)"

# Verdict -------------------------------------------------------------------
echo "=== verdict: WARN=$WARN CRIT=$CRIT ==="
if [ "$CRIT" -gt 0 ]; then echo "STATUS=CRITICAL"; exit 2
elif [ "$WARN" -gt 0 ]; then echo "STATUS=WARN"; exit 1
else echo "STATUS=OK"; exit 0; fi
