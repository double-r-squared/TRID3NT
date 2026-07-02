"""
grace2-ops-watchdog -- server-side health probe for the GRACE-2 / TRID3NT stack.

Mirrors scripts/ops_health_check.sh (7 probes).  Runs every 15 min via
EventBridge.  Publishes to SNS on WARN or CRITICAL; completely silent on OK
(near-zero SNS cost on healthy days).

Each probe is individually try/excepted so one bad AWS call degrades to a
noted WARN and never crashes the whole run.

Thresholds are read from Lambda environment variables so they can be tuned
via Tofu without touching this file.
"""
import json
import logging
import os
import urllib.request
import urllib.error

import boto3

logger = logging.getLogger()
logger.setLevel(logging.INFO)

# ---------------------------------------------------------------------------
# Configuration from env (all tunable via Tofu variables -> Lambda env vars)
# ---------------------------------------------------------------------------
REGION      = os.environ.get("AWS_REGION",        "us-west-2")
CLUSTER     = os.environ.get("ECS_CLUSTER",        "grace2-agents")
FAMILY      = os.environ.get("ECS_FAMILY",         "grace2-agent-session")
ROUTES_TBL  = os.environ.get("ROUTES_TABLE",       "grace2_session_routes")
REAPER_FN   = os.environ.get("REAPER_FUNCTION",    "grace2-agent-task-reaper")
SOLVER_Q    = os.environ.get("BATCH_QUEUE",        "grace2-solvers")
AGENT_BOX   = os.environ.get("AGENT_BOX_ID",       "i-0251879a278df797f")
CF_URL      = os.environ.get("CF_URL",             "https://d125yfbyjrpbre.cloudfront.net/")
SNS_TOPIC   = os.environ["SNS_TOPIC_ARN"]          # mandatory -- set by Tofu

ORPHAN_CRIT_MIN    = int(os.environ.get("ORPHAN_CRIT_MIN",   "6"))
ORPHAN_CRIT_DELTA  = int(os.environ.get("ORPHAN_CRIT_DELTA", "3"))
ORPHAN_WARN_DELTA  = int(os.environ.get("ORPHAN_WARN_DELTA", "2"))
VCPU_QUOTA         = int(os.environ.get("VCPU_QUOTA",        "64"))
VCPU_CRIT_PCT      = int(os.environ.get("VCPU_CRIT_PCT",     "75"))
BATCH_WARN_JOBS    = int(os.environ.get("BATCH_WARN_JOBS",   "8"))
CF_TIMEOUT_S       = int(os.environ.get("CF_TIMEOUT_S",      "8"))

VCPU_CRIT_THRESHOLD = VCPU_QUOTA * VCPU_CRIT_PCT // 100


def handler(event, context):
    """Lambda entry point.  Returns {"status": "OK|WARN|CRITICAL", "issues": N}."""
    # issues: list of (severity, probe_id, message, remediation_hint)
    issues = []

    ecs   = boto3.client("ecs",   region_name=REGION)
    ddb   = boto3.client("dynamodb", region_name=REGION)
    lmb   = boto3.client("lambda", region_name=REGION)
    elb   = boto3.client("elbv2", region_name=REGION)
    btch  = boto3.client("batch", region_name=REGION)
    ec2   = boto3.client("ec2",   region_name=REGION)
    sns   = boto3.client("sns",   region_name=REGION)

    running = 0
    routes  = 0

    # ---------------------------------------------------------------------- #
    # 1) Orphan session-task pile-up (the $190 leak)                         #
    # ---------------------------------------------------------------------- #
    try:
        task_arns = []
        paginator = ecs.get_paginator("list_tasks")
        for page in paginator.paginate(
            cluster=CLUSTER, family=FAMILY, desiredStatus="RUNNING"
        ):
            task_arns.extend(page["taskArns"])
        running = len(task_arns)

        resp   = ddb.scan(TableName=ROUTES_TBL, Select="COUNT")
        routes = resp.get("Count", 0)

        logger.info("probe-1 orphan: running=%d routes=%d", running, routes)

        if running > ORPHAN_CRIT_MIN and running > routes + ORPHAN_CRIT_DELTA:
            issues.append((
                "CRITICAL", "1-orphan-pileup",
                f"orphan pile-up: {running} running tasks vs {routes} live routes",
                "check reaper Lambda DRY_RUN=false; stop orphaned tasks manually if needed"
            ))
        elif running > routes + ORPHAN_WARN_DELTA:
            issues.append((
                "WARN", "1-orphan-pileup",
                f"session-task/route mismatch: {running} running vs {routes} routes",
                "watch for growth; reaper should catch these within 2 cycles"
            ))
    except Exception as exc:
        logger.warning("probe-1 error: %s", exc)
        issues.append(("WARN", "1-orphan-pileup",
                        f"probe error -- {exc}",
                        "check Lambda IAM or ECS/DynamoDB API"))

    # ---------------------------------------------------------------------- #
    # 2) Fargate vCPU headroom (session tasks ~2 vCPU each)                  #
    # ---------------------------------------------------------------------- #
    try:
        vcpu_est = running * 2
        logger.info("probe-2 vcpu: est=%d threshold=%d quota=%d",
                    vcpu_est, VCPU_CRIT_THRESHOLD, VCPU_QUOTA)
        if vcpu_est >= VCPU_CRIT_THRESHOLD:
            issues.append((
                "CRITICAL", "2-vcpu-quota",
                f"Fargate vCPU near quota: ~{vcpu_est}/{VCPU_QUOTA} "
                f"({VCPU_CRIT_PCT}%+ used)",
                "new sessions will fail (capacity error); stop orphaned tasks "
                "or request a quota raise"
            ))
    except Exception as exc:
        logger.warning("probe-2 error: %s", exc)
        issues.append(("WARN", "2-vcpu-quota",
                        f"probe error -- {exc}", "check task count"))

    # ---------------------------------------------------------------------- #
    # 3) Reaper armed -- DRY_RUN must be false                               #
    # ---------------------------------------------------------------------- #
    try:
        cfg = lmb.get_function_configuration(FunctionName=REAPER_FN)
        dry = (cfg.get("Environment", {})
                   .get("Variables", {})
                   .get("DRY_RUN", "false"))
        logger.info("probe-3 reaper: DRY_RUN=%s", dry)
        if dry == "true":
            issues.append((
                "WARN", "3-reaper-dry-run",
                "reaper Lambda DRY_RUN=true -- orphan tasks will NOT be stopped",
                "set DRY_RUN=false on grace2-agent-task-reaper Lambda env"
            ))
    except Exception as exc:
        logger.warning("probe-3 error: %s", exc)
        issues.append(("WARN", "3-reaper-dry-run",
                        f"probe error -- {exc}",
                        "check Lambda IAM / reaper function name"))

    # ---------------------------------------------------------------------- #
    # 4) Broker ALB target health                                             #
    # ---------------------------------------------------------------------- #
    try:
        tgs = elb.describe_target_groups()["TargetGroups"]
        broker_tg = next(
            (tg for tg in tgs if "broker" in tg["TargetGroupName"].lower()),
            None,
        )
        if broker_tg:
            health_resp = elb.describe_target_health(
                TargetGroupArn=broker_tg["TargetGroupArn"]
            )
            healthy_ct = sum(
                1
                for t in health_resp["TargetHealthDescriptions"]
                if t["TargetHealth"]["State"] == "healthy"
            )
            logger.info("probe-4 alb: tg=%s healthy=%d",
                        broker_tg["TargetGroupName"], healthy_ct)
            if healthy_ct < 1:
                issues.append((
                    "CRITICAL", "4-broker-alb",
                    "broker ALB has 0 healthy targets -- /ws endpoint is down",
                    "check ECS broker service health; may need a new task deployment"
                ))
        else:
            logger.info("probe-4 alb: no broker target group found (skip)")
    except Exception as exc:
        logger.warning("probe-4 error: %s", exc)
        issues.append(("WARN", "4-broker-alb",
                        f"probe error -- {exc}",
                        "check ELBv2 IAM permissions"))

    # ---------------------------------------------------------------------- #
    # 5) CloudFront / TiTiler edge reachable (WARN not CRIT -- VPC may       #
    #    block egress; browser test separately if this fires)                 #
    # ---------------------------------------------------------------------- #
    try:
        req = urllib.request.Request(
            CF_URL, headers={"User-Agent": "grace2-ops-watchdog/1.0"}
        )
        with urllib.request.urlopen(req, timeout=CF_TIMEOUT_S) as resp:
            code = resp.getcode()
        logger.info("probe-5 cf-edge: HTTP=%d", code)
    except Exception as exc:
        logger.warning("probe-5 cf-edge unreachable: %s", exc)
        issues.append((
            "WARN", "5-cf-edge",
            f"CloudFront edge unreachable from Lambda: {exc}",
            "verify from a browser; may be Lambda NAT/VPC egress issue -- "
            "confirm https://d125yfbyjrpbre.cloudfront.net/ loads externally"
        ))

    # ---------------------------------------------------------------------- #
    # 6) Batch -- stuck or runaway solver jobs                                #
    # ---------------------------------------------------------------------- #
    try:
        resp     = btch.list_jobs(jobQueue=SOLVER_Q, jobStatus="RUNNING")
        run_jobs = len(resp.get("jobSummaryList", []))
        logger.info("probe-6 batch: running_jobs=%d", run_jobs)
        if run_jobs > BATCH_WARN_JOBS:
            issues.append((
                "WARN", "6-batch-runaway",
                f"unusually many Batch solver jobs running: {run_jobs} "
                f"(threshold {BATCH_WARN_JOBS})",
                "check for runaway submit loop; inspect grace2-solvers queue "
                "in the AWS Batch console"
            ))
    except Exception as exc:
        logger.warning("probe-6 error: %s", exc)
        issues.append(("WARN", "6-batch-runaway",
                        f"probe error -- {exc}",
                        "check Batch IAM permissions"))

    # ---------------------------------------------------------------------- #
    # 7) Agent fallback box unexpectedly running (unexpected cost signal)     #
    # ---------------------------------------------------------------------- #
    try:
        resp  = ec2.describe_instances(InstanceIds=[AGENT_BOX])
        state = "unknown"
        if resp["Reservations"]:
            state = resp["Reservations"][0]["Instances"][0]["State"]["Name"]
        logger.info("probe-7 box: %s state=%s", AGENT_BOX, state)
        if state == "running":
            issues.append((
                "WARN", "7-fallback-box",
                f"agent fallback box {AGENT_BOX} is RUNNING -- "
                "Fargate broker is primary; this box accrues unnecessary cost",
                f"stop it if not intentionally in fallback mode: "
                f"aws ec2 stop-instances --instance-ids {AGENT_BOX} "
                f"--region {REGION}"
            ))
    except Exception as exc:
        logger.warning("probe-7 error: %s", exc)
        issues.append(("WARN", "7-fallback-box",
                        f"probe error -- {exc}",
                        "check EC2 IAM permissions"))

    # ---------------------------------------------------------------------- #
    # Aggregate verdict                                                       #
    # ---------------------------------------------------------------------- #
    crits = [i for i in issues if i[0] == "CRITICAL"]
    warns = [i for i in issues if i[0] == "WARN"]

    if crits:
        severity = "CRITICAL"
    elif warns:
        severity = "WARN"
    else:
        severity = "OK"

    lines = [
        f"GRACE-2 ops watchdog run complete",
        f"  status   : {severity}",
        f"  region   : {REGION}",
        f"  ecs tasks: {running} running  |  dynamo routes: {routes}",
        f"  issues   : {len(crits)} CRITICAL  {len(warns)} WARN",
        "",
    ]
    for sev, pid, msg, hint in issues:
        lines.append(f"[{sev}] {pid}")
        lines.append(f"  msg  : {msg}")
        lines.append(f"  hint : {hint}")
        lines.append("")

    if not issues:
        lines.append("All 7 probes passed -- stack looks healthy.")

    verdict = "\n".join(lines)
    logger.info("VERDICT %s\n%s", severity, verdict)

    if severity != "OK":
        sns.publish(
            TopicArn=SNS_TOPIC,
            Subject=f"[GRACE2 OPS {severity}]",
            Message=verdict,
        )
        logger.info("SNS alert published to %s", SNS_TOPIC)

    return {"status": severity, "issues": len(issues)}
