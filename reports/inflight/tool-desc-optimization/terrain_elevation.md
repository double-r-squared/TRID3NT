# Tool-description optimization -- terrain_elevation (9 tools)

**Branch:** `agent/render-honesty-audit`. Docstrings + type annotations only; no logic/return changes.
Same standard + mechanism as `hazard_modeling.md`.

## Verification
All 9: routing block (`Use this when:` / `Do NOT use this for:`) within first 1000 chars; ASCII-clean;
no GCP-infra in first-1000; default-in-Literal clean; `py_compile` clean. `data_fetch.py` isolated to
`fetch_dem` (single hunk; all other functions byte-identical). Full pytest left for integration.

## Literal status
Existing Literals kept (all were already lifted): `fetch_3dep_extra.resolution`, `compute_slope`
(output_unit/algorithm), `compute_aspect.algorithm`, `compute_hillshade` (style/algorithm),
`compute_colored_relief.ramp`, `compute_blended_composite.blend_mode`. `fetch_dem.resolution_m` left
`int` (parametric/interpolated). No new lifts needed.

## Dangling sibling-ref fixes (fetch_dem)
`fetch_bathymetry` -> `fetch_topobathy`; dropped nonexistent `point_elevation`; reworded
`build_sfincs_model` (a workflow fn, not a registered tool) -> "flood/hydrology model setup".

## Notes
Derivation cluster mutually disambiguated (slope=steepness, aspect=face direction, hillshade=shaded
relief, colored_relief=hypsometric tint, contours=vector lines, blended_composite=multi-raster bake);
`compute_contours` flagged VECTOR. GCP run-cache paths purged; public USGS 3DEP / Copernicus DEM /
Planetary Computer / OpenTopography sources kept.
