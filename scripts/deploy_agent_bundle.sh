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
find "$STAGE" -name __pycache__ -type d -prune -exec rm -rf {} + 2>/dev/null || true
find "$STAGE" -name '*.pyc' -delete 2>/dev/null || true

TGZ="$STAGE/agent_deploy_src.tgz"
tar czf "$TGZ" -C "$STAGE" grace2_agent grace2_contracts
SHA="$(sha256sum "$TGZ" | cut -d' ' -f1)"
printf '%s\n' "$SHA" > "$TGZ.sha256"

aws s3 cp "$TGZ" "s3://$BUCKET/$KEY" --region "$REGION" --only-show-errors
aws s3 cp "$TGZ.sha256" "s3://$BUCKET/$KEY.sha256" --region "$REGION" --only-show-errors

echo "uploaded  s3://$BUCKET/$KEY"
echo "sha256    $SHA"
echo "next: run scripts/deploy_agent_onbox.sh on the box (see its header)"
