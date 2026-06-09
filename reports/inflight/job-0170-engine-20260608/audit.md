# Audit: rasterio /vsigs/ migration — eliminate gcsfs segfault

**Job ID:** job-0170-engine-20260608, **Specialist:** engine (Opus)

## Why

Agent crashes mid-run with `SystemError: <cyfunction DatasetBase.stop> returned a result with an exception set` from rasterio reading remote rasters via gcsfs. Happens in `hydromt_sfincs.setup_manning_roughness` → `rioxarray.open_rasterio` → gcsfs.

## Scope

Replace gcsfs raster open with rasterio native `/vsigs/` paths.

1. Find all rasterio/rioxarray usages reading `gs://` paths: `grep -rn "open_rasterio\|rasterio.open\|fsspec" services/agent/`
2. For each, switch to `rasterio.open(f"/vsigs/{bucket}/{key}")` syntax
3. Configure `CPL_GS_OAUTH2_REFRESH_TOKEN` env or use ADC via `GDAL_HTTP_HEADER_FILE`
4. Set `GDAL_NUM_THREADS=1` permanently in module init (currently env-set)
5. Add retry-with-backoff for transient GS reads

## Verify
Re-run Fort Myers flood prompt, verify no segfault, verify pipeline completes. If segfault recurs, surface OQ.

## File ownership
- `services/agent/src/grace2_agent/workflows/sfincs_builder.py`
- `services/agent/src/grace2_agent/workflows/postprocess_flood.py`
- Any other rioxarray/gcsfs touch points
- Tests
- `reports/inflight/job-0170-engine-20260608/`

## FROZEN
Single commit prefix `job-0170:`.
