# job-0314 report — TiTiler render-resilience fix

**Owner:** infra · **Opened/closed:** 2026-06-16 · **State:** DONE (prod-deployed + verified) · **Priority:** P0 demo blocker

## Summary

Root-caused and fixed the "layer publishes but never paints on the map" blocker. Cause was **not** in our code or the COGs — TiTiler (the COG→tile server on EC2) had **wedged**: the service stayed up and listening, but every tile request timed out because both uvicorn workers were blocked on hung synchronous GDAL `/vsis3/` S3 reads (no read timeout), exhausting the threadpool. Restart restored service instantly (proving the diagnosis); hardening prevents recurrence and auto-recovers.

## Evidence (orchestrator-direct SSM, read-only diagnosis)

- `titiler.service` `active (running)`, listening on :8080, but local + CloudFront tile/info requests all timed out (HTTP 000 @ 25s). ~17 hung connections, 242 cgroup tasks, 4.0G RSS. Journal: normal `200 OK` until ~21:36, then silence.
- Ruled out COG validity: `compute_colored_relief` emits a real COG (`_translate_to_cog`); `publish_layer` accepts only s3:// COGs; templates correct; healthy server returns `200`.

## Fix (authorized by user 2026-06-16: "Restart + harden now")

1. **Immediate:** `systemctl restart titiler` → tiles served again: local tile `200`/0.35s (256×256 RGBA PNG), CloudFront tile `200`/0.22s. Wedge confirmed + cleared.
2. **Durable unit hardening** (`/etc/systemd/system/titiler.service`):
   - GDAL/VSI timeouts so a stalled S3 read fails fast instead of blocking a worker forever: `GDAL_HTTP_TIMEOUT=30`, `GDAL_HTTP_CONNECTTIMEOUT=10`, `GDAL_HTTP_MAX_RETRY=2`, `GDAL_HTTP_RETRY_DELAY=1`, plus `VSI_CACHE`/`GDAL_CACHEMAX`/`CPL_VSIL_CURL_CACHE_SIZE` tuning.
   - `--workers 2 → 4` for multi-Case tile-burst headroom; `RestartSec=2`.
3. **Watchdog auto-recovery** (`titiler-watchdog.{sh,service,timer}`, every 2 min): exercises the real `/cog/info` /vsis3 path against a permanent health COG (`s3://grace2-agent-bundle-226996537797/health/titiler-health.tif`); restarts titiler ONLY when it gets no HTTP response (wedge signature), never on a valid HTTP error (no false restarts).

## Post-deploy verification

| Check | Result |
|---|---|
| ExecStart | `uvicorn ... --workers 4` |
| `GDAL_HTTP_TIMEOUT` in running env | `30` |
| titiler active | yes |
| local tile | `HTTP 200`, 0.32s, 77 KB PNG |
| health endpoint | `HTTP 200`, 0.13s |
| watchdog dry-run | ran, no restart |
| watchdog timer | active |
| CloudFront tile | `HTTP 200`, 0.05s |

## Artifacts
- `titiler.service`, `titiler-watchdog.sh`, `titiler-watchdog.service`, `titiler-watchdog.timer` (this dir; staged via `s3://grace2-agent-bundle-226996537797/job-0314/`).
- Memory: `project_titiler_wedge_render_blocker.md` (durable root-cause + reusable diagnostic recipe).

## Follow-ups (separate jobs)
- Chat narration/tool-card interleave: server.py allocates one `message_id` per whole turn → all narration collapses to one bubble, tool cards bunch after it. Fix = fresh `message_id` when text resumes after a tool call (+ persistence so replay matches). Queued next.
- Roads/inline-GeoJSON non-render: re-evaluate now that TiTiler is healthy (hung raster tiles likely poisoned the reconcile); confirm on next live test.
