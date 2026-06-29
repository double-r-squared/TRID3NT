# Tool-description optimization -- hydrology (14 tools)

**Branch:** `agent/render-honesty-audit`. **Scope:** docstrings + param TYPE ANNOTATIONS only.
NO logic / param / return-shape changes. Dark until box deploy. Same standard + mechanism as
`hazard_modeling.md`.

## Verification (in-worktree)

- All 14: `Use this when:` AND `Do NOT use this for:` within the first 1000 chars (AST-measured).
- First-1000: ASCII-clean, zero GCP-infra terms. Default-in-Literal: clean. `py_compile` clean.
- `data_fetch.py` isolation: only fetch_river_geometry + lookup_precip_return_period changed
  (helper `_fetch_nhdplushr_geometry_bytes` and `fetch_dem` byte-identical).
- **NOT run here:** full pytest (registry + audit_gemini_schema_compliance + per-tool). Run at integration.

## Literal lifts (verified; defaults in-set)

| param | tool | values |
|---|---|---|
| source | fetch_river_geometry | nhdplus_hr/osm |
| version | fetch_cama_flood_discharge | v4.0.1/v4.20/v4.30 |
| band | fetch_jrc_global_surface_water | occurrence/recurrence/seasonality/change (\| None -> occurrence) |
| depth | fetch_soilgrids | 0-5cm/5-15cm/15-30cm/30-60cm/60-100cm/100-200cm |

Already `Literal` (kept): `antecedent_moisture` (gcn250), `field` (statsgo), `soil_property` (soilgrids),
`product` (nwm_streamflow), `direction` (nldi UM/UT/DM/DD). Left `str`/numeric: `waterway_type`
(combinatorial/aliased open vocab), `characteristic` (WQP open vocab), `state_code` (~59 parametric),
`return_period_years`/`duration_hours` (numeric).

## Correctness fixes (dangling tool refs)

Old docstrings named tools that are NOT in the registry -- these would mislead the router. Fixed:
`fetch_streamflow` -> `fetch_usgs_nwis_gauges` (in fetch_noaa_nwm_streamflow, fetch_nhdplus_nldi_navigate,
fetch_river_geometry), and dropped a reference to a nonexistent `delineate_watershed` tool.
**Suggest the Orchestrator run a repo-wide dangling-sibling-ref audit** -- other categories may carry
similar stale names from before tools were renamed.

## Other

- `lookup_precip_return_period` -- honesty note: returns a SCALAR dict (depth/return-period), NOT a layer;
  Do-NOT names the precip RASTER fetchers (fetch_mrms_qpe/fetch_chirps_precipitation/fetch_gridmet).
- GCP run-cache `gs://` paths purged; public sources kept (USGS NWIS/WQP, NOAA NWM, NHDPlus/NLDI, JRC +
  Planetary Computer, ISRIC SoilGrids, NRCS SNOTEL, GCN250 Figshare, USGS ScienceBase).
- Confusable clusters: gauges (nwis observed vs nwm modeled vs nws forecast), soils (statsgo US vs
  soilgrids global), USGS sites (water_quality vs groundwater_levels vs nwis_gauges).
