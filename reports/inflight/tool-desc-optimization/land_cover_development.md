# Tool-description optimization -- land_cover_development (17 tools)

**Branch:** `agent/render-honesty-audit`. **Scope:** docstrings + param TYPE ANNOTATIONS only.
NO logic / param / return-shape changes. Dark until box deploy. Same standard + mechanism as
`hazard_modeling.md`.

## Verification (in-worktree)

- All 17: `Use this when:` AND `Do NOT use this for:` within the first 1000 chars (AST-measured).
- First-1000: ASCII-clean, zero GCP-infra terms. Repo-wide default-in-Literal check: clean.
- `list[Literal[...]]` (road_classes) confirmed to render as `type=ARRAY` + `items.enum` via genai
  `from_callable` -- valid input_schema, no crash.
- All changed files `py_compile` clean.
- `data_fetch.py` isolation confirmed: only fetch_landcover / fetch_buildings / fetch_population
  docstrings changed (+ the typing import); geocode_location and all other functions byte-identical.
- **NOT run here:** full pytest (registry + audit_gemini_schema_compliance + per-tool). Run at integration.

## Literal lifts (verified values; defaults confirmed in-set)

| param | tool | values |
|---|---|---|
| source | fetch_buildings | osm/msft (default osm) |
| source | compute_building_density | ms_footprints |
| source | fetch_hrsl_population | meta_hrsl |
| road_classes | fetch_roads_osm | list[Literal[16 verified _VALID_ROAD_CLASSES]] (\| None) |
| dataset | fetch_field_boundaries | us_usda_cropland/japan/denmark (\| None, vs FTW_DATASETS) |
| band_combo | fetch_landsat_imagery | true_color/false_color_nir/thermal (default true_color) |

**Left `str`/`int`/`float`/`list[int]` deliberately** (open/parametric or out-of-set default):
fetch_landcover.dataset + fetch_population.dataset (open `nlcd_YYYY` / `worldpop_YYYY` vocab),
fetch_esri_landcover_10m.year (default None not in 2017-2023; genai enums are string-only),
fetch_census_acs.variable (arbitrary `B#####_###E` codes) + year, extract_landcover_class.classes
(open NLCD code list), digitize_water_body.ndwi_threshold/min_area_m2 (continuous), fetch_ghsl_population.epoch.

Each `Literal` narrows only the advertised enum; runtime alias normalizers (e.g. landsat `rgb`/`cir`,
buildings spellings) still accept wider inputs -- no behavior change.

## fetch_landcover (parked render gap)

Still returns `{"layer": LayerURI, "nlcd_vintage_year":..., ...}` (consumed by model_flood_scenario +
compute_blended_composite). Per NATE the render-honesty fix is PARKED -- this pass added an HONEST note
that it returns a dict carrying the layer + vintage/class metadata; return shape/logic UNCHANGED.

## GCP purge / public endpoints kept

Purged `gs://grace-2-hazard-prod-cache/...` run-cache paths from docstrings; corrected several stale
"`gs://`/GCS" mentions in error-code + class docstrings to `s3://` (the code actually reads S3 now --
a correctness fix). Kept public sources: Microsoft Planetary Computer STAC (NAIP/Landsat/ESRI/Sentinel),
USGS, Census TIGERweb/data.census.gov, OSM, WorldPop, GHSL, USACE NSI, USDA/FTW.

## Confusable clusters disambiguated

Landcover trio (fetch_landcover NLCD/ESA full-classification vs extract_landcover_class single-class-mask
vs fetch_esri_landcover_10m ESRI-10m); population (fetch_population WorldPop/ACS vs fetch_hrsl_population
HRSL vs fetch_ghsl_population GHSL rasters vs fetch_census_acs vector tracts); buildings (fetch_buildings
OSM footprints vs compute_building_density raster vs fetch_usace_nsi structure points); imagery
(fetch_naip aerial RGB vs fetch_landsat_imagery multiband vs fetch_sentinel2_truecolor vs compute_ndvi).
