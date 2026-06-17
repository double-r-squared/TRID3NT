#!/bin/bash
# titiler-watchdog (job-0314) — auto-recover the COG tile server from a wedge.
#
# Exercises the ACTUAL /vsis3 tile-read code path (the path that wedged on
# 2026-06-16). When TiTiler's worker threadpool is exhausted by blocked S3
# reads, /cog/info hangs and returns no HTTP response within the timeout —
# that is the wedge signature. A healthy server answers fast (200), and even a
# missing health COG answers fast (404). So we restart ONLY when curl gets no
# response at all (code 000/empty) — never on a valid HTTP error, to avoid
# false restarts.
HEALTH_URL="s3://grace2-agent-bundle-226996537797/health/titiler-health.tif"
ENDPOINT="http://localhost:8080/cog/info?url=${HEALTH_URL}"
code=$(curl -sS -m 15 -o /dev/null -w '%{http_code}' "$ENDPOINT" 2>/dev/null)
if [ -z "$code" ] || [ "$code" = "000" ]; then
  logger -t titiler-watchdog "TiTiler unresponsive (code=${code:-timeout}) — restarting titiler.service"
  systemctl restart titiler
fi
exit 0
