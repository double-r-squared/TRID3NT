# Tool-description optimization -- coastal (12 tools)

**Branch:** `agent/render-honesty-audit`. **Scope:** docstrings + param TYPE ANNOTATIONS only.
NO logic / param / return-shape changes. Dark until box deploy. Same standard + mechanism as
`hazard_modeling.md`.

## Verification (in-worktree)

- All 12: `Use this when:` AND `Do NOT use this for:` within the first 1000 chars; ASCII-clean;
  zero GCP-infra terms; default-in-Literal clean; **no dangling sibling refs in the first-1000**
  (every named sibling verified against the registered-tool set). `py_compile` clean.
- **NOT run here:** full pytest (registry + audit_gemini_schema_compliance + per-tool). Run at integration.

## Literal lifts (verified; defaults in-set)

| param | tool | values |
|---|---|---|
| output | fetch_gtsm_tide_surge | water_level/surge_only |
| product | fetch_noaa_coops_tides | water_level/predictions |
| product | fetch_noaa_coops_currents | currents/currents_predictions |
| variable | fetch_noaa_sst | sst/anomaly |
| polarization / collection | fetch_sentinel1_sar | vv/vh ; sentinel-1-rtc/sentinel-1-grd |
| observation_type | fetch_tsunami_events | events/runups |

Left `float`/`str`: `scenario_ft`/`slr_ft` (numeric SLR values), `target_crs` (free CRS).

## Dangling sibling-ref fixes

- `fetch_noaa_coops_tides`: `fetch_streamflow` -> `fetch_usgs_nwis_gauges` (nonexistent -> real);
  bare `model_flood_scenario` -> `run_model_flood_scenario`.
- `fetch_noaa_slr_scenarios`: corrected a stale "fetch_noaa_slr_marsh (not yet implemented)" note
  (it IS registered).

## Honesty / render notes

- `fetch_topobathy` returns a `TopobathyResult` (LayerURI subclass) but sets `auto_publish=False`
  + `role="input"` -- the action line now states it does NOT auto-render and is fed to
  `build_sfincs_model.setup_dep` (a pure-input intermediate). Important: prevents the model from
  claiming a topobathy layer is "on the map".
- `compute_wave_nomograph` / `compute_overtopping` return SCALARS (NOT layers) -- action line says so;
  the two now cross-reference each other.

## GCP purge / public endpoints kept

Purged `gs://grace-2-hazard-prod-cache/...` run-bucket refs (gtsm, coops_tides, slr_scenarios).
Kept: Copernicus CDS / GTSM-Deltares, NOAA CO-OPS (Tides & Currents), NOAA OCM SLR Viewer,
NOAA CoastWatch/CRW, Planetary Computer STAC Sentinel-1, NCEI tsunami DB.

Confusable clusters: CO-OPS (tides vs currents), SLR trio (scenarios vs confidence vs marsh),
wave (nomograph vs overtopping), tsunami (NCEI events vs modeled run_geoclaw_inundation).
