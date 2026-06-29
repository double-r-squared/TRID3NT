# Tool-description optimization -- weather_atmosphere (20 tools)

**Branch:** `agent/render-honesty-audit`. **Scope:** docstrings + param TYPE ANNOTATIONS only.
NO logic / param / return-shape changes. Dark until box deploy. Same standard + mechanism as
`hazard_modeling.md`.

## Verification (in-worktree)

- All 20: `Use this when:` AND `Do NOT use this for:` within the first 1000 chars (worst Do-NOT@930).
- First-1000: ASCII-clean, zero GCP-infra terms.
- **Every Literal-annotated param's DEFAULT is within its Literal set** (AST-checked -- guards the
  `compute_class="medium"` failure mode). All changed files `py_compile` clean.
- All 20 tools are single-per-file except `fetch_goes_animation.py` (animation + blend_animation),
  cleanly co-edited.
- **NOT run here:** full pytest (registry + audit_gemini_schema_compliance + per-tool). Run at integration.

## Literal lifts (str -> Literal, exact values verified vs runtime; defaults confirmed in-set)

| param | tool | values |
|---|---|---|
| status | fetch_nws_alerts_conus, fetch_nws_event | actual/exercise/system/test/draft |
| message_type | fetch_nws_event | alert/update/cancel |
| accumulation | fetch_mrms_qpe | 1h/3h/6h/12h/24h/48h/72h |
| variable | fetch_hrrr_forecast | 2m_temperature/10m_wind_speed/10m_u_wind/10m_v_wind/surface_precip_1hr |
| variable | fetch_hrrr_smoke | near_surface_smoke/smoke_column_mass/aerosol_optical_depth |
| variable | fetch_era5_reanalysis | 7 verified ERA5 vars |
| variable | fetch_gridmet | 12 verified gridMET vars |
| band / satellite | fetch_goes_satellite | visible/ir_window/water_vapor ; goes-16/17/18/19 |
| band / satellite | fetch_goes_animation | geocolor/fire_temperature ; goes-18/19 |
| satellite | fetch_goes_blend_animation | goes-18/19 |
| band / satellite | fetch_goes_archive_animation | fire_temperature/true_color/fire_hotspots/fire_baked ; goes-16/18/19 |
| satellite | fetch_glm_lightning | goes-16/17/18/19 |
| product / daynight | fetch_modis_lst | 11A2/21A2 ; day/night |

Already `Literal` (left as-is): `fetch_nexrad_reflectivity.product`, `fetch_chirps_precipitation.period`.
Left `str` (open/parametric/multi-value): all dates/bbox/cycle/valid_time, `area` (state name/code/FIPS),
`sector`, AirNow/OpenAQ `parameters` (multi-value free vocab), the GOES archive bt-threshold floats.

**Key correctness note:** each `Literal` narrows only the ADVERTISED `input_schema` enum (steers the
model to canonical values); the function bodies keep their pre-existing tolerant normalizers
(uppercase MRMS tokens, GOES alias spellings, MODIS aliases), so legacy/alias inputs still work --
NO behavior change.

## GCP purge / public endpoints kept

Purged: `gs://grace-2-hazard-prod-cache/...` run-cache paths in Returns blocks (-> `s3://<cache-bucket>`).
Kept (real public data sources): NOAA Big-Data-Program GOES buckets, Copernicus/CDS (ERA5),
EPA AirNow (airnowapi.org), OpenAQ (api.openaq.org), Iowa Environmental Mesonet (ASOS/METAR).
Secret-gated honesty notes kept: ERA5 (CDS key), AirNow (AIRNOW_KEY), OpenAQ (OPENAQ key).

## Confusable clusters disambiguated

NWS conus-sweep vs event(state/county/bbox); HRRR forecast vs smoke; the GOES quad
(satellite single-frame vs animation vs blend vs archive) + GLM; precip cross-cluster
(mrms radar-QPE vs gridmet CONUS-met vs chirps quasi-global vs era5 reanalysis);
air-quality (AirNow US-authority vs OpenAQ global); stations (ASOS airport vs RAWS fire-weather).
