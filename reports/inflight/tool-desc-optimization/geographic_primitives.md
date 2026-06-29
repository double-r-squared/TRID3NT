# Tool-description optimization -- geographic_primitives (32 tools)

**Branch:** `agent/render-honesty-audit`. **Scope:** docstrings + a few param TYPE
ANNOTATIONS only. NO logic / param / return-shape changes. Dark until box deploy.

Same standard + mechanism as `hazard_modeling.md` (Bedrock truncates the docstring to the
first 1000 chars at `bedrock_adapter.py:493`; genai passes it verbatim; `Literal` is the only
path to a real `input_schema` enum). Front-load routing block within 1000, demote/trim
internals, standardize `Use this when:` / `Do NOT use this for:`, lift verified enums, ASCII-clean,
purge dead GCP infra (keep public-data endpoints).

## Verification (in-worktree)

- All 32: `Use this when:` AND `Do NOT use this for:` within the first 1000 chars (AST-measured).
- First-1000 region: ASCII-clean, zero GCP-infra terms.
- All changed `.py` files `py_compile` clean.
- Shared-file isolation confirmed: `data_fetch.py` -- ONLY `geocode_location` docstring changed
  (single hunk @2265, zero code lines); `catalog.py` -- only catalog_search + catalog_fetch;
  `passthroughs.py` -- only qgis_process; `publish_layer.py` -- only the registered fn docstring;
  `analytical_qa.py` -- the 3 QA tools; `chart_tools.py` -- the 4 chart tools; `qgis_discovery.py`
  -- the 2 discovery tools.
- **NOT run here:** full pytest (registry + audit_gemini_schema_compliance + per-tool). Run at
  integration.

## Key fixes

- **3 clips disambiguated** (clip_raster_to_bbox / clip_raster_to_polygon / clip_vector_to_polygon)
  -- each Do-NOT now names the other two + cut_features_with_polygon (erase vs clip).
- **Chart cluster** (generate_histogram / choropleth_legend / time_series / damage_distribution)
  -- each tagged "[CHART payload, not a map layer]" + Do-NOT naming the 3 distinct siblings.
- **code_exec_request** -- Do-NOT was at ~char 2400 (invisible); now Use@78 / DoNOT@416, with a
  condensed layer_refs/no-network/returns-JSON action line above the cut.
- **enhance_satellite_image** -- Use was at ~char 1570; now @91.
- **compute_cross_section / compute_terrain_profile** -- Do-NOT lifted from ~1180/1300; pair now
  cross-references.
- **publish_layer** / **qgis_process** -- de-GCP'd (Cloud Run / our gs:// run-buckets / QGIS-Server
  WMS demoted to dormant-interim); return logic untouched.
- **discover_dataset** -- purged a dead `Vertex text-embedding` ref AND a dead `ADK tool catalog`
  ref (both decommissioned).
- **request_spatial_input** -- the only Literal lifts in this category: `mode:
  Literal["point","bbox","vector_draw"]`, `purpose: Literal["barrier","line"]` (verified vs
  `_VALID_MODES`/`_VALID_PURPOSES`).

## Literal status

Most enum-bearing tools here were ALREADY `Literal` (`compute_zonal_statistics.statistics`,
`analytical_qa.agg`, `fetch_administrative_boundaries.level`). New lifts: `request_spatial_input`
mode + purpose. Everything else is open/parametric (OSM Overpass tag/amenity/category, catalog
entry_id/source_filter, geocode query, chart configs, qgis algorithm ids, dates) -- correctly
left `str`. No tool in this category has a `compute_class` param.

## Flags for the Orchestrator

1. **catalog_fetch** is the known dict-wrap render gap (returns `{"layer": LayerURI, ...}` so the
   isinstance emit gate never fires). Per NATE the render-honesty fix is PARKED -- this pass changed
   the DOCSTRING ONLY (added an honest "returns a dict, not an auto-render layer" note); the return
   shape/logic is unchanged. Separate decision still pending (see the earlier render-honesty audit:
   the only two dict-wrap gaps are catalog_fetch + fetch_landcover; main's auto-publish wrapper
   already handles everything else).
2. Residual GCP in non-ingested MODULE docstrings + code comments + `gs://` CODE fallbacks left
   untouched (infra-migration territory, not LLM-ingested).
