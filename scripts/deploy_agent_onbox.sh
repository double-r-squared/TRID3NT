#!/usr/bin/env bash
# GRACE-2 agent deploy -- STEP 2 (run ON THE AGENT EC2 BOX).
#
# Downloads the source bundle (uploaded by scripts/deploy_agent_bundle.sh),
# verifies its sha256, swaps it over the INSTALLED grace2_agent + grace2_contracts
# packages, applies env-var systemd drop-ins, restarts the agent, and verifies by
# querying the RUNNING agent. Self-discovering, backs up before swapping, safe to
# re-run. Runs as root under SSM Run Command (sudo also works under Session Manager).
#
# Run it (the account blocks AWS-RunShellCommand, so this is delivered via a custom
# SSM Run Command doc or pasted into a Session Manager shell):
#   aws s3 cp s3://grace2-agent-bundle-226996537797/engine-build/deploy_agent_onbox.sh /tmp/
#   sudo bash /tmp/deploy_agent_onbox.sh
#
# Override env vars with --env KEY=VALUE (repeatable); default sets the SWAN job-def.
set -uo pipefail

BUCKET="${GRACE2_AGENT_BUNDLE_BUCKET:-grace2-agent-bundle-226996537797}"
KEY="engine-build/agent_deploy_src.tgz"
SERVICE="grace2-agent"
REGION="${AWS_REGION:-us-west-2}"
ENVVARS=("GRACE2_AWS_BATCH_JOB_DEF_SWAN=grace2-swan")

_envset=0
while [ $# -gt 0 ]; do
  case "$1" in
    --env)     if [ "$_envset" -eq 0 ]; then ENVVARS=(); _envset=1; fi; ENVVARS+=("$2"); shift 2;;
    --service) SERVICE="$2"; shift 2;;
    --key)     KEY="$2"; shift 2;;
    *) echo "unknown arg: $1" >&2; exit 2;;
  esac
done

SUDO=""; [ "$(id -u)" -ne 0 ] && SUDO="sudo"

set -e
echo "== download + verify bundle =="
aws s3 cp "s3://$BUCKET/$KEY" /tmp/agent_deploy_src.tgz --region "$REGION"
aws s3 cp "s3://$BUCKET/$KEY.sha256" /tmp/agent_deploy_src.tgz.sha256 --region "$REGION"
printf '%s  %s\n' "$(cat /tmp/agent_deploy_src.tgz.sha256)" /tmp/agent_deploy_src.tgz | sha256sum -c -
rm -rf /tmp/agent_deploy && mkdir -p /tmp/agent_deploy
tar xzf /tmp/agent_deploy_src.tgz -C /tmp/agent_deploy
set +e

echo "== locate venv + installed packages =="
# Pull the venv bin dir from the systemd ExecStart executable (whatever it is:
# grace2-agent console script or python). set +e above so a no-match cannot abort.
EXE="$(systemctl show -p ExecStart --value "$SERVICE" 2>/dev/null | grep -oE '/[^ ;]+' | head -1)"
BINDIR="$(dirname "${EXE:-/opt/grace2/.venv/bin/_}")"
PY="$BINDIR/python"; [ -x "$PY" ] || PY="$BINDIR/python3"; [ -x "$PY" ] || PY="/opt/grace2/.venv/bin/python"
[ -x "$PY" ] || PY="$(command -v python3)"
AGENT_DIR="$("$PY" -c 'import grace2_agent,os;print(os.path.dirname(grace2_agent.__file__))' 2>/dev/null)"
[ -d "$AGENT_DIR" ] || AGENT_DIR="/opt/grace2/services/agent/src/grace2_agent"
CONTRACTS_DIR="$("$PY" -c 'import grace2_contracts,os;print(os.path.dirname(grace2_contracts.__file__))' 2>/dev/null)"
[ -d "$CONTRACTS_DIR" ] || CONTRACTS_DIR="/opt/grace2/packages/contracts/src/grace2_contracts"
echo "py=$PY"; echo "agent=$AGENT_DIR"; echo "contracts=$CONTRACTS_DIR"
[ -d "$AGENT_DIR" ] || { echo "FATAL: agent dir not found"; exit 1; }
[ -d "$CONTRACTS_DIR" ] || { echo "FATAL: contracts dir not found"; exit 1; }

set -e
echo "== backup + swap source =="
TS="$(date +%Y%m%d-%H%M%S)"
$SUDO cp -a "$AGENT_DIR" "${AGENT_DIR}.bak-$TS"
$SUDO cp -a "$CONTRACTS_DIR" "${CONTRACTS_DIR}.bak-$TS"
$SUDO cp -a /tmp/agent_deploy/grace2_agent/.     "$AGENT_DIR"/
$SUDO cp -a /tmp/agent_deploy/grace2_contracts/. "$CONTRACTS_DIR"/
echo "rollback: restore *.bak-$TS over the live dirs + restart $SERVICE"

echo "== env drop-in =="
$SUDO mkdir -p "/etc/systemd/system/$SERVICE.service.d"
{ echo "[Service]"; for kv in "${ENVVARS[@]}"; do echo "Environment=$kv"; done; } \
  | $SUDO tee "/etc/systemd/system/$SERVICE.service.d/50-grace2-deploy.conf" >/dev/null
$SUDO systemctl daemon-reload

echo "== restart =="
$SUDO systemctl restart "$SERVICE"
set +e
sleep 7

echo "== verify (running agent) =="
echo "--- health ---"; curl -s -m 10 localhost:8766/api/health; echo
CAT="$(curl -s -m 20 localhost:8766/api/tool-catalog)"
echo "--- tool count ---"; printf '%s' "$CAT" | grep -oE '"name"[[:space:]]*:' | wc -l
echo "--- new tools ---"
for n in run_swan_waves fetch_wfigs_incident fetch_goes_animation fetch_viirs_day_fire run_model_satellite_fire_animation; do
  if printf '%s' "$CAT" | grep -q "\"$n\""; then echo "  $n = True"; else echo "  $n = MISSING"; fi
done
echo "--- service ---"; $SUDO systemctl is-active "$SERVICE"
echo "== done =="
