#!/usr/bin/env bash
# GRACE-2 agent deploy -- STEP 1 (run on the DEV machine, from anywhere in the repo).
#
# Stages the grace2_agent + grace2_contracts source, tars it, and uploads the
# bundle + a sha256 sidecar to the agent-bundle S3 bucket. Pair with
# scripts/deploy_agent_onbox.sh (run on the agent EC2 box).
#
# Why a bundle + an on-box script instead of SSM RunCommand: this account blocks
# the AWS-managed AWS-RunShellCommand SSM document (an org guardrail), so the
# agent CODE deploy is done by pulling this bundle on the box (Session Manager).
#
# Usage:  bash scripts/deploy_agent_bundle.sh
set -euo pipefail

REPO_ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
BUCKET="${GRACE2_AGENT_BUNDLE_BUCKET:-grace2-agent-bundle-226996537797}"
KEY="engine-build/agent_deploy_src.tgz"
REGION="${AWS_REGION:-us-west-2}"

STAGE="$(mktemp -d)"
trap 'rm -rf "$STAGE"' EXIT

cp -a "$REPO_ROOT/services/agent/src/grace2_agent" "$STAGE/"
cp -a "$REPO_ROOT/packages/contracts/src/grace2_contracts" "$STAGE/"
# sandbox-staging: ship the Python-sandbox executor harness too. It is NOT part of
# either Python package (it lives in the container build context infra/python-sandbox/),
# but code_exec_request shells out to it as a subprocess; without it on-box, the
# resolved executor path does not exist and code_exec_request fails closed with a
# FileNotFoundError. We stage it under a dedicated subdir so the on-box script can
# place it at a stable path and point GRACE2_SANDBOX_EXECUTOR at it. (executor.py is
# standalone -- stdlib only, plus an optional in-jail grace2_contracts.chart_contracts
# import handled by the executor's own PYTHONPATH.)
mkdir -p "$STAGE/python_sandbox"
cp -a "$REPO_ROOT/infra/python-sandbox/executor.py" "$STAGE/python_sandbox/executor.py"

# workers-modflow staging: the agent imports the MODFLOW deck author
# ``services/workers/modflow/gwt_adapter.py`` at runtime for LOCAL mf6 runs
# (run_modflow._import_gwt_adapter resolves it via parents[5] -> <repo>/services/
# workers/modflow). It is NOT part of either Python package, so the bundle ships
# it here and the on-box script swaps it over the box's worker dir. Without this,
# the MODFLOW archetypes (Wave 1-5: yield/dewatering/budget/MAR/ASR/wetland/
# multi_species/capture_zone/wellhead_protection/saltwater_intrusion) never reach
# the box and build_modflow_deck raises "unknown MODFLOW archetype".
mkdir -p "$STAGE/workers_modflow"
cp -a "$REPO_ROOT/services/workers/modflow/gwt_adapter.py" "$STAGE/workers_modflow/gwt_adapter.py"

find "$STAGE" -name __pycache__ -type d -prune -exec rm -rf {} + 2>/dev/null || true
find "$STAGE" -name '*.pyc' -delete 2>/dev/null || true

TGZ="$STAGE/agent_deploy_src.tgz"
tar czf "$TGZ" -C "$STAGE" grace2_agent grace2_contracts python_sandbox workers_modflow
SHA="$(sha256sum "$TGZ" | cut -d' ' -f1)"
printf '%s\n' "$SHA" > "$TGZ.sha256"

aws s3 cp "$TGZ" "s3://$BUCKET/$KEY" --region "$REGION" --only-show-errors
aws s3 cp "$TGZ.sha256" "s3://$BUCKET/$KEY.sha256" --region "$REGION" --only-show-errors

echo "uploaded  s3://$BUCKET/$KEY"
echo "sha256    $SHA"
echo "next: run scripts/deploy_agent_onbox.sh on the box (see its header)"
