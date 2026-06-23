#!/usr/bin/env bash
# GRACE-2 agent deploy -- STEP 2 (run ON THE AGENT EC2 BOX).
#
# Downloads the source bundle (uploaded by scripts/deploy_agent_bundle.sh),
# verifies its sha256, swaps it over the INSTALLED grace2_agent + grace2_contracts
# packages, applies env-var systemd drop-ins, restarts the agent, and verifies.
# Self-discovering (finds the venv + package dirs), backs up before swapping, and
# safe to re-run.
#
# Get a shell on the box first (AWS-RunShellCommand is blocked in this account, so
# use Session Manager):
#   aws ssm start-session --target i-0251879a278df797f --region us-west-2
# Then, in that shell:
#   aws s3 cp s3://grace2-agent-bundle-226996537797/engine-build/deploy_agent_onbox.sh /tmp/
#   sudo bash /tmp/deploy_agent_onbox.sh
#
# Override the env vars it sets with --env KEY=VALUE (repeatable). Default sets the
# SWAN Batch job-def so run_swan_waves can dispatch.
set -euo pipefail

BUCKET="${GRACE2_AGENT_BUNDLE_BUCKET:-grace2-agent-bundle-226996537797}"
KEY="engine-build/agent_deploy_src.tgz"
SERVICE="grace2-agent"
REGION="${AWS_REGION:-us-west-2}"
ENVVARS=("GRACE2_AWS_BATCH_JOB_DEF_SWAN=grace2-swan")

# Optional overrides: --env KEY=VAL (repeatable), --service NAME, --key S3KEY.
_envset=0
while [ $# -gt 0 ]; do
  case "$1" in
    --env)     if [ "$_envset" -eq 0 ]; then ENVVARS=(); _envset=1; fi; ENVVARS+=("$2"); shift 2;;
    --service) SERVICE="$2"; shift 2;;
    --key)     KEY="$2"; shift 2;;
    *) echo "unknown arg: $1" >&2; exit 2;;
  esac
done

echo "== download + verify bundle =="
aws s3 cp "s3://$BUCKET/$KEY" /tmp/agent_deploy_src.tgz --region "$REGION"
aws s3 cp "s3://$BUCKET/$KEY.sha256" /tmp/agent_deploy_src.tgz.sha256 --region "$REGION"
printf '%s  %s\n' "$(cat /tmp/agent_deploy_src.tgz.sha256)" /tmp/agent_deploy_src.tgz | sha256sum -c -
rm -rf /tmp/agent_deploy && mkdir -p /tmp/agent_deploy
tar xzf /tmp/agent_deploy_src.tgz -C /tmp/agent_deploy

echo "== locate venv + installed packages =="
PY="$(systemctl show -p ExecStart --value "$SERVICE" 2>/dev/null | grep -oE '/[^ ]*/python[0-9.]*' | head -1)"
[ -x "$PY" ] || PY=/opt/grace2/.venv/bin/python
AGENT_DIR="$(sudo "$PY" -c 'import grace2_agent,os;print(os.path.dirname(grace2_agent.__file__))' 2>/dev/null || echo /opt/grace2/services/agent/src/grace2_agent)"
CONTRACTS_DIR="$(sudo "$PY" -c 'import grace2_contracts,os;print(os.path.dirname(grace2_contracts.__file__))' 2>/dev/null || echo /opt/grace2/packages/contracts/src/grace2_contracts)"
echo "py=$PY"; echo "agent=$AGENT_DIR"; echo "contracts=$CONTRACTS_DIR"

echo "== backup + swap source =="
TS="$(date +%Y%m%d-%H%M%S)"
sudo cp -a "$AGENT_DIR" "${AGENT_DIR}.bak-$TS"
sudo cp -a "$CONTRACTS_DIR" "${CONTRACTS_DIR}.bak-$TS"
sudo cp -a /tmp/agent_deploy/grace2_agent/.     "$AGENT_DIR"/
sudo cp -a /tmp/agent_deploy/grace2_contracts/. "$CONTRACTS_DIR"/
echo "rollback: restore *.bak-$TS over the live dirs + restart $SERVICE"

echo "== env drop-in =="
sudo mkdir -p "/etc/systemd/system/$SERVICE.service.d"
{ echo "[Service]"; for kv in "${ENVVARS[@]}"; do echo "Environment=$kv"; done; } \
  | sudo tee "/etc/systemd/system/$SERVICE.service.d/50-grace2-deploy.conf" >/dev/null
sudo systemctl daemon-reload

echo "== restart + verify =="
sudo systemctl restart "$SERVICE"
sleep 6
echo "--- health ---"; curl -s -m 10 localhost:8766/api/health || true; echo
echo "--- tools ---"
sudo "$PY" -c 'import grace2_agent.tools as t; print("count", len(t.TOOL_REGISTRY)); [print(" ",n,"=",n in t.TOOL_REGISTRY) for n in ["run_swan_waves","fetch_wfigs_incident","fetch_goes_animation","fetch_viirs_day_fire","run_model_satellite_fire_animation"]]'
echo "== done =="
