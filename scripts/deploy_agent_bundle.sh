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

# --------------------------------------------------------------------------- #
# COLD tool-catalog snapshot (NATE 2026-06-27: "I shouldn't have to start an
# agent to see tools"). Build the catalog payload from the SAME source we just
# bundled and publish it as a durable, public, agent-less JSON in the web S3
# bucket. The web app (web/src/components/ToolsCatalogPopup.tsx) reads this COLD
# snapshot FIRST -- it is served 24/7 and a plain GET does NOT wake the
# auto-stopped agent box. Republished here on every deploy, so it tracks exactly
# what the box is about to run. Best-effort: a failure here NEVER aborts the
# agent code deploy (the web falls back to the live /api/tool-catalog endpoint).
# Skip with GRACE2_SKIP_COLD_CATALOG=1.
# --------------------------------------------------------------------------- #
if [ "${GRACE2_SKIP_COLD_CATALOG:-0}" != "1" ]; then
  WEB_BUCKET="${GRACE2_WEB_BUCKET:-grace2-hazard-web-226996537797}"
  CATALOG_KEY="${GRACE2_COLD_CATALOG_KEY:-catalog/tool-catalog.json}"
  AGENT_PY="${GRACE2_AGENT_PY:-$REPO_ROOT/services/agent/.venv/bin/python}"
  [ -x "$AGENT_PY" ] || AGENT_PY="$(command -v python3 || true)"
  CATALOG_JSON="$STAGE/tool-catalog.json"
  if [ -n "$AGENT_PY" ] && PYTHONPATH="$STAGE${PYTHONPATH:+:$PYTHONPATH}" "$AGENT_PY" -c \
      'import json,sys; from grace2_agent.tool_catalog_http import build_catalog_payload; sys.stdout.write(json.dumps(build_catalog_payload(), separators=(",",":")))' \
      > "$CATALOG_JSON" 2>/dev/null && [ -s "$CATALOG_JSON" ]; then
    if aws s3 cp "$CATALOG_JSON" "s3://$WEB_BUCKET/$CATALOG_KEY" \
        --content-type application/json --cache-control "public, max-age=300" \
        --region "$REGION" --only-show-errors; then
      echo "cold catalog published  s3://$WEB_BUCKET/$CATALOG_KEY  ($(wc -c < "$CATALOG_JSON") bytes)"
    else
      echo "WARN: cold catalog upload failed (web falls back to live /api/tool-catalog)"
    fi
  else
    echo "WARN: could not build cold catalog payload via $AGENT_PY (skipping; web falls back to live endpoint)"
  fi
fi

echo "next: run scripts/deploy_agent_onbox.sh on the box (see its header)"
