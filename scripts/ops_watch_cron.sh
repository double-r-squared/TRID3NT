#!/usr/bin/env bash
# Zero-token ops watch (NATE 2026-07-08): tier 0 of the event-driven ladder.
# Runs the deterministic health check hourly from PLAIN cron - no LLM at all
# while healthy. Only a WARN/CRITICAL writes an anomaly file, which the
# session folder-Monitor escalates (tier 1 Sonnet triage -> tier 2 main
# model) - so tokens are spent ONLY on real anomalies.
set -u
REPO="/home/nate/Documents/GRACE-2"
OUT="$("$REPO/scripts/ops_health_check.sh" 2>&1)"
STATUS="$(printf '%s\n' "$OUT" | tail -1)"
case "$STATUS" in
  *STATUS=OK*) exit 0 ;;
esac
TS="$(date -u +%Y%m%dT%H%M%SZ)"
FILE="$REPO/reports/ops-anomalies/anomaly-$TS.md"
mkdir -p "$REPO/reports/ops-anomalies"
{
  echo "# Ops anomaly $TS (bash tier-0 detector)"
  echo
  echo "Summary: health check ended with '$STATUS'"
  echo
  echo "## Flagged lines"
  printf '%s\n' "$OUT" | grep -E "\[(WARN|CRIT)" || echo "(none matched)"
  echo
  echo "## Full stdout"
  echo '```'
  printf '%s\n' "$OUT"
  echo '```'
} > "$FILE"
