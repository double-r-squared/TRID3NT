# Audit: `model_flood_habitat_scenario` workflow — Case 1 composer

**Job ID:** job-0118-engine-20260608, **Sprint:** sprint-12-mega Wave 2, **Specialist:** engine

**Required reads:**
- `services/agent/src/grace2_agent/workflows/model_flood_scenario.py` (existing SFINCS pipeline)
- `services/agent/src/grace2_agent/tools/{fetch_gbif_occurrences,fetch_inaturalist_observations,fetch_wdpa_protected_areas}.py` (Wave 1)
- `services/agent/src/grace2_agent/tools/{clip_raster_to_polygon,clip_vector_to_polygon}.py` (Wave 1.5)
- `services/agent/src/grace2_agent/tools/compute_zonal_statistics.py` (Wave 1)
- Memory: `feedback_geographic_clipping_pattern` + `project_demo_case_3_idaho_flood`

### Scope

NEW file `services/agent/src/grace2_agent/workflows/model_flood_habitat_scenario.py`

This is a HIGHER-ORDER WORKFLOW composing existing atomic tools end-to-end for Case 1 acceptance (Everglades / Big Cypress / Apalachicola flood + habitat exposure).

```python
@register_workflow(
    name="model_flood_habitat_scenario",
    description="Compose flood modeling + habitat occurrence + protected-area exposure into a single Case 1 demo workflow.",
)
async def model_flood_habitat_scenario(
    bbox: tuple[float, float, float, float],
    species_keys: list[int | str],
    rainfall_event: str = "atlas14_100yr",
    protected_area_designation: str | None = None,
    place_clip_polygon_uri: str | None = None,
    pipeline_emitter: PipelineEmitter,
) -> CaseOneResult:
    """End-to-end Case 1 workflow:
        1. fetch_wdpa_protected_areas(bbox) → wdpa_uri
        2. for each species_key: fetch_gbif_occurrences(species_key, bbox) → per-species occurrence layer (separate, per memory rule)
        3. model_flood_scenario(bbox, rainfall_event) → flood_depth_uri (existing pipeline)
        4. compute_zonal_statistics(flood_depth_uri, wdpa_uri, statistics=["max","mean","count"]) → impact_metrics
        5. if place_clip_polygon_uri: clip_raster_to_polygon(flood_depth_uri, place_clip_polygon_uri) AND clip_vector_to_polygon(...)
        6. Return: CaseOneResult with flood_uri, [species_uris], wdpa_uri, impact_metrics

    Each tool call emits a pipeline-state envelope via pipeline_emitter; the
    web client renders progress cards inline in chat (existing pattern).
    """
```

**Implementation details**:
- New result type `CaseOneResult` in `packages/contracts/src/grace2_contracts/case_results.py` (NEW small file): `flood_layer_uri`, `species_layers: list[LayerURI]`, `wdpa_layer_uri`, `impact_metrics: dict`, `case_summary_text: str`
- Each species → its own LayerURI emitted to `loaded_layers` (per-species discipline)
- `place_clip_polygon_uri` parameter: if user named a region in their prompt (e.g. "in Big Cypress"), the agent passes the polygon URI here to clip outputs
- After zonal stats: format a human-readable case_summary_text ("Within Everglades National Park: 3 species occurrences (240 panther points, 89 spoonbill, 17 alligator), max flood depth 1.2m, mean 0.4m")
- Pipeline emitter calls: emit at each major step so the web shows progress
- Auto-publish all layers via existing `publish_layer` calls

**Tests** (≥6 unit + 1 live):
- Mock each underlying tool with returns; verify orchestration order
- Empty species_keys → workflow still produces flood_layer + wdpa_layer
- protected_area_designation filter passed through to fetch_wdpa
- place_clip_polygon_uri: verify clipping calls fire after main fetches
- pipeline_emitter receives expected number of stage events
- CaseOneResult round-trip via pydantic serialization
- Live (env GRACE2_TEST_LIVE_CASE1=1): real Big Cypress bbox + Florida panther (2435099) + Roseate spoonbill (2481008) → produces flood + habitat layers + impact summary text

### File ownership (exclusive)

- `services/agent/src/grace2_agent/workflows/model_flood_habitat_scenario.py` (NEW)
- `services/agent/src/grace2_agent/workflows/__init__.py` — append registration
- `packages/contracts/src/grace2_contracts/case_results.py` (NEW small)
- `packages/contracts/src/grace2_contracts/__init__.py` — export CaseOneResult
- `services/agent/tests/workflows/test_model_flood_habitat_scenario.py` (NEW)
- `packages/contracts/tests/test_case_results.py` (NEW small)
- `reports/inflight/job-0118-engine-20260608/`


### FROZEN

All files outside the explicit file-ownership list. Especially: every sibling Wave 2 job's exclusive files; `reports/complete/**`; `docs/SRS_v0.3.md` monolith (regenerated only); all Wave 1/1.5 atomic tool files (additive use only — don't modify their signatures).

### Concurrency note (Wave 2 fan-out — 16 parallel)

Same idempotent-append pattern + `git pull --rebase` pre-commit mitigation as Wave 1.5. Files all land correctly in HEAD; only commit-message labels may drift. Use marker commits if your changes get swept into a sibling's commit hash.

### Codified lessons (do NOT violate)

1. **Geographic-correctness gate (job-0086)**: verify against real geography, not URL/render consistency.
2. **Kickoff-front-loaded design**: orchestrator did the design — execute, don't redesign. Surface OQs in your report rather than expanding scope.
3. **MongoDB MCP canonical persistence (job-0115 foundation)**: ALL CRUD goes through `Persistence.*`. Do NOT design custom collection wrappers. If your job needs a new method on Persistence, ADD it (additive) rather than bypassing.

### Acceptance criteria

- [ ] All deliverables landed per scope
- [ ] ≥4 unit tests + ≥1 live test (env-guarded if external)
- [ ] Geographic-correctness / behavioral-correctness verified
- [ ] No FROZEN edits; single commit prefix `<job-id>:`; co-author line
- [ ] Returns commit SHA + outcome + 1-paragraph headline + evidence + OQs

