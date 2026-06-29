# Tool-description optimization -- hazard_modeling (37 tools)

**Branch:** `agent/render-honesty-audit` (rebased onto current `origin/main`).
**Scope:** docstrings + a few param TYPE ANNOTATIONS only. NO logic / param / return-shape
changes. Agent-side; needs a box deploy to go live (dark until deployed).

## Why (the mechanism)

A registered tool's DESCRIPTION sent to the LLM is its Python function docstring. On the
live Bedrock path it is HARD-TRUNCATED to the first **1000 chars** (`bedrock_adapter.py:493`).
genai's `from_callable` passes the docstring through VERBATIM (confirmed empirically -- it does
NOT strip the `Params:` block), so raw-docstring char N == what Bedrock sees at char N.
Consequence before this pass: on ~17 of 37 tools the routing-critical `Do NOT use this for:`
(and on 2 tools even `Use this when:`) sat PAST char 1000 -- invisible to the router AND to the
model's grasp of what to do with the result (publish/chain/narrate guidance dropped). This is a
plausible contributor to "fetcher ran but the agent did not surface/publish it".

Also confirmed: genai does NOT populate per-parameter descriptions from docstring prose, so the
ONLY way allowed-values reach `input_schema` is a `Literal[...]` annotation (verified: a Literal
emits a real `enum: [...]` in the tool schema).

## The standard applied to all 37

1. First ~1000 chars carry the full routing signal, in order: one-line summary (+ distinguishing
   tag) -> `Use this when:` -> `Do NOT use this for:` (names sibling tools) -> 1-line honesty/critical
   note -> 1-line action/publish/chain line pulled up from past-cut `Cross-tool`/`Returns` text.
2. Heavy internals (numbered step-chains, full `Returns:` schema, FR-XX citations, error tables)
   demoted below the routing block and trimmed.
3. Heading standardized to exactly `Use this when:` / `Do NOT use this for:` (killed bare
   `When to use:` and one bold `**When to use:**`).
4. Prose-only str enums lifted to `Literal[...]` (real schema enums). Shared
   `compute_class: ComputeClass` from `grace2_contracts.execution`.
5. ASCII-cleaned the reachable region (em/en dashes + arrows -> `--` / `->`).
6. Purged dead GCP INFRA refs from docstrings (Vertex / gcloud / Cloud Run / Cloud Workflows /
   GCS / our `gs://` run-buckets / BigQuery). KEPT public-dataset endpoints.

## Verification (done in-worktree)

- All 37: `Use this when:` AND `Do NOT use this for:` land within the first 1000 raw-docstring
  chars (Use@ 56-407, Do-NOT@ 318-662). AST-measured.
- First-1000 region: ASCII-clean, zero GCP-infra terms.
- 35 changed `.py` files pass `python -m py_compile`. Net -86 lines.
- `solver.py` carries both rewrites (`run_solver` + `wait_for_completion`) intact.
- Literal -> schema `enum` mechanism proven via a standalone genai render.

**NOT run here (deps absent in this checkout):** full pytest -- registry test,
`audit_gemini_schema_compliance.py`, per-tool tests. **Run these at integration.** The pass is
docstrings + Literal annotations only; the schema-compliance audit should be the key gate (it
allows `enum`, forbids `anyOf/oneOf/$ref`).

## Literal lifts (str -> Literal, values verified against runtime)

| param | tool(s) | values |
|---|---|---|
| compute_class | most run_* tools | ComputeClass = small/standard/large/gpu |
| rank_by | run_model_contamination_affected_fields | peak/area |
| analysis | run_landlab_susceptibility | landslide_probability/overland_flow |
| mode | run_swan_waves | stationary/nonstationary |
| boundary_side | run_swan_waves | N/S/E/W (\| None) |
| scenario | run_geoclaw_inundation | dam_break/tsunami/surge |
| building_representation | run_swmm_urban_flood | drop/raise/roughness |
| infiltration_method | run_swmm_urban_flood | none/scs_cn/green_ampt |
| building_obstacle_mode | run_model_flood_scenario | exclude/raise |
| accumulation | run_model_nws_flood_event_scenario | 1h/6h/24h/72h |
| satellite | run_model_satellite_fire_animation | goes-18/19, suomi-npp, noaa-20/21, all |
| satellite | run_model_goes_fire_animation | goes-18/goes-19 |
| satellite + base_band | run_model_glm_lightning_animation | goes-19/16/18/17 ; visible/ir |
| target_event_type | run_model_news_event_ingest | spill/flood/wildfire/hurricane |
| fragility_set | run_pelicun_with_buildings | hazus_flood_v6/fema_hazus_eq_2020 |
| catalog | fetch_fault_sources | gem |
| model_variant | compute_canopy_height | 3 CPU variants |

**Left `str` deliberately** (open/parametric/unverified): `imt` (`SA(<period>)`), `gmpe` (open
OpenQuake class set), `sector`, `solver` (registry-driven), `zone_partition`, `contaminant`,
`mobi_layer`, `rainfall_event` (`atlas14_<N>yr`), all dates / free-text query fields.

## Flags for the Orchestrator

1. **`compute_class="medium"` inconsistency (pre-existing).** A few tools default
   `compute_class="medium"`, which is NOT in `ComputeClass=[small,standard,large,gpu]`; the backend
   (`server.py:6055`) silently treats unknown values as `standard`. Those params were LEFT as `str`
   (not force-annotated) to avoid changing a default. Decision needed: normalize the default to
   `"standard"`, or add `"medium"` to the `ComputeClass` Literal. Affected: run_model_flood_scenario,
   run_model_nws_flood_event_scenario, run_solver (and any other "medium" default).
2. **Residual GCP left untouched (out of scope):** GCP mentions in a few MODULE docstrings + code
   comments (e.g. `run_modflow_tool.py:27/91`) and `gs://` output-URI CODE fallbacks
   (`run_modflow_tool.py:284`, `run_river_seepage_tool.py:300`). These are not LLM-ingested (module
   docstrings) or are runtime logic tied to the GCP->AWS infra migration -- flagged, not changed.
3. **Confusable composers** now cross-reference in their Do-NOT (MAR vs ASR; capture_zone vs
   wellhead_protection; raw run_*_job vs run_model_*_scenario composer; review-gated vs unattended
   fire animations) -- verify the disambiguation reads correctly in a live routing test.

## Files changed (35)

tools/: compute_canopy_height, fetch_fault_sources, fetch_us_drought_monitor,
fetch_usgs_earthquakes, fetch_usgs_volcano_alerts, run_geoclaw_tool, run_landlab_tool,
run_modflow_tool, run_openquake_tool, run_pelicun_damage_assessment, run_river_seepage_tool,
run_swan_tool, run_swmm_tool, solver.
workflows/: model_asr_scenario, model_capture_zone_scenario (capture_zone + wellhead_protection),
model_conservation_priority, model_contamination_affected_fields, model_flood_habitat_scenario,
model_flood_scenario, model_glm_lightning_animation, model_goes_fire_animation,
model_groundwater_contamination_scenario, model_mar_scenario, model_mine_dewatering_scenario,
model_multi_species_scenario, model_news_event_ingest, model_nws_flood_event_scenario,
model_regional_water_budget_scenario, model_river_seepage_scenario, model_saltwater_intrusion_scenario,
model_satellite_fire_animation, model_sustainable_yield_scenario, model_wetland_hydroperiod_scenario,
pelicun_damage_with_buildings.
