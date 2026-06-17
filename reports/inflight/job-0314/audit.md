# job-0314 — TiTiler render-resilience fix (the "layer publishes but never paints" blocker)

**Owner:** infra
**Opened:** 2026-06-16
**Priority:** P0 — demo blocker. User halted live testing until this is confidently resolved.
**Sprint:** ux-batch-1 adjunct (render-pipeline reliability)

## Problem statement (user-reported, 2026-06-16)

Across multiple fresh Cases (Columbia SC flood + hillshade; Seattle WA gradient/colored relief; roads), the agent reports `Layer published`, the layer appears in the LayerPanel, but **nothing paints on the map**. The AOI bounding box renders fine. The user: "we have been at it for hours and we had it working and now its not working... Im done testing until you can confidently say this has been resolved." Also flagged: this was being chased ad-hoc, not through the job/report/audit workflow.

## Root cause (CONFIRMED — orchestrator-direct SSM diagnosis, read-only)

TiTiler (`titiler.service`, `uvicorn ... --workers 2` on :8080, EC2 i-0251879a278df797f) is **wedged**: the service is `active (running)` and LISTENing, but **every tile/info request times out (HTTP 000 @ 25s)** — local AND via CloudFront `/cog`. ~17 hung established connections on :8080, 242 cgroup tasks, 4.0G RSS. Journal: normal `200 OK` tile serving until ~21:36, then nothing. Both uvicorn workers blocked on synchronous GDAL `/vsis3/` COG reads that never return (no read timeout) → anyio threadpool exhausted → all new requests queue forever. Does not self-recover.

Ruled out: COG validity (compute_colored_relief emits a real COG via `_translate_to_cog`; publish_layer only accepts s3:// COGs; templates correct; 200s when healthy). 404s in journal are normal out-of-bounds edge tiles.

The unit has zero resilience: no GDAL HTTP timeouts, no per-request timeout, no worker recycling, no watchdog.

Full diagnosis archived in memory: `project_titiler_wedge_render_blocker.md`.

## Scope / acceptance

1. **IMMEDIATE (restore service):** restart `titiler.service`; confirm a known COG tile serves `200` image bytes local + via CloudFront.
2. **DURABLE (prevent re-wedge):**
   - GDAL/VSI timeouts in the unit Environment: `GDAL_HTTP_TIMEOUT`, `GDAL_HTTP_CONNECTTIMEOUT`, `GDAL_HTTP_MAX_RETRY`, `GDAL_HTTP_RETRY_DELAY`, `VSI_CACHE=TRUE`, `GDAL_CACHEMAX`, `CPL_VSIL_CURL_CACHE_SIZE`, keep existing READDIR/extension/merge-ranges vars.
   - Worker recycling / request timeout: move to gunicorn w/ uvicorn workers + `--timeout`, OR add an ASGI request-timeout, so a blocked worker is killed + replaced. Raise worker count if RAM allows.
   - Auto-recovery: systemd `WatchdogSec` or an external `/cog`-healthcheck timer that restarts on hang.
3. **VERIFY:** curl tile local + CloudFront returns `200`; then a fresh live raster demo (relief or flood) paints on the map for the user.

## Out of scope (tracked separately)
- Chat narration/tool-card interleave (server.py single-message_id-per-turn) — separate agent job, see STATE.
- Roads/inline-GeoJSON non-render — re-evaluate AFTER TiTiler healthy; likely the hung-raster-tile reconcile poison, may already resolve.

## Decisions log
- 2026-06-16: diagnosed wedge; awaiting user prod-deploy authorization for restart + harden.
