#!/usr/bin/env bash
# Repoint CloudFront /ws* between the EC2 box and the per-session Fargate broker.
#
#   CUT OVER to the always-warm Fargate broker (default):
#     bash scripts/cutover_ws.sh
#   ROLL BACK to the EC2 box (instant rollback lane):
#     bash scripts/cutover_ws.sh origin-agent-ws
#
# CloudFront mutation -- NATE runs this (the Claude classifier blocks it). Run it
# inline in the session with:  ! bash scripts/cutover_ws.sh
#
# After it returns "Deployed"/"InProgress", CloudFront takes ~3-5 min to propagate.
# The per-session broker cold-provisions a Fargate agent on first connect (~40-50s,
# covered by the broker keepalive overlay); the reaper is paused so a provisioned
# session persists. Verified end-to-end GO (flood solves + renders + survives a
# reconnect) on 2026-06-30. Box stays as the instant rollback origin.
set -euo pipefail

DIST_ID="E2L74AS56MVZ87"
TARGET="${1:-origin-broker-ws}"     # origin-broker-ws (Fargate) | origin-agent-ws (box)

case "$TARGET" in
  origin-broker-ws|origin-agent-ws) ;;
  *) echo "ERROR: target must be origin-broker-ws or origin-agent-ws (got '$TARGET')"; exit 2 ;;
esac

TMP="$(mktemp -d)"; trap 'rm -rf "$TMP"' EXIT
aws cloudfront get-distribution-config --id "$DIST_ID" > "$TMP/full.json"
ETAG="$(jq -r '.ETag' "$TMP/full.json")"
CUR="$(jq -r '.DistributionConfig.CacheBehaviors.Items[] | select(.PathPattern|test("ws")) | .TargetOriginId' "$TMP/full.json")"
echo "current /ws* origin: $CUR   ->   new: $TARGET   (ETag $ETAG)"
if [ "$CUR" = "$TARGET" ]; then echo "already pointed at $TARGET -- nothing to do."; exit 0; fi

jq --arg t "$TARGET" '.DistributionConfig
  | (.CacheBehaviors.Items[] | select(.PathPattern|test("ws")) | .TargetOriginId) = $t' \
  "$TMP/full.json" > "$TMP/config.json"

STATUS="$(aws cloudfront update-distribution --id "$DIST_ID" --if-match "$ETAG" \
  --distribution-config "file://$TMP/config.json" --query 'Distribution.Status' --output text)"
echo "OK: /ws* -> $TARGET   (CloudFront status: $STATUS; ~3-5 min to propagate)"
echo "verify: aws cloudfront get-distribution-config --id $DIST_ID --query \"DistributionConfig.CacheBehaviors.Items[?contains(PathPattern,'ws')].TargetOriginId\" --output text"
