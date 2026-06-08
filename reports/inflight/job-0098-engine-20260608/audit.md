# Audit: `run_pelicun_damage_assessment` atomic tool — stub

**Job ID:** job-0098-engine-20260608, **Sprint:** sprint-12-mega Wave 1, **Auditor:** Development Orchestrator, **Status:** assigned

**Specialist:** engine

**Required reads:**
- Decision N (Pelicun impact post-processor) — see `project_pelicun_impact_postprocessor.md` memory if available
- `services/agent/src/grace2_agent/tools/compute_zonal_statistics.py` (job-0083) — pattern for analytical tools
- Pelicun docs: https://nheri-simcenter.github.io/pelicun/

### Scope — STUB form, full composer is Wave 2 (job-0106)

NEW file `services/agent/src/grace2_agent/tools/run_pelicun_damage_assessment.py`

```python
@register_tool(
    cacheable=True,
    ttl_class="static-30d",
    source_class="pelicun_damage",
)
def run_pelicun_damage_assessment(
    hazard_raster_uri: str,  # e.g. flood_depth GeoTIFF
    assets_uri: str,          # FlatGeobuf points (buildings) or polygons (parcels)
    fragility_set: Literal["hazus_flood_v6", "fema_hazus_eq_2020"] = "hazus_flood_v6",
    component_types: list[str] | None = None,  # e.g. ["RES1", "COM1"]
    realization_count: int = 100,
) -> LayerURI:
    """Fragility-curve-driven damage assessment via Pelicun.

    For each asset point/polygon:
      1. Sample the hazard raster at asset location
      2. Look up fragility function by component_type + hazard intensity
      3. Monte-Carlo sample `realization_count` damage states
      4. Aggregate to per-asset expected damage state + 95% CI

    Returns LayerURI(layer_type="vector", role="primary", units="damage_state")
    with assets as features, properties: ds_mean, ds_p05, ds_p95, repair_cost_mean,
    repair_cost_p95, replacement_value.

    fragility_set:
        "hazus_flood_v6"   — FEMA HAZUS-MH flood damage curves (depth-damage)
        "fema_hazus_eq_2020" — earthquake (sprint-13+ if hazard != flood)

    LLM guidance:
        - Pair with model_flood_scenario output (hazard_raster_uri = flood_depth COG)
        - assets_uri: use fetch_administrative_boundaries(level='place') as a coarse
          asset proxy v0.1; sprint-13 swap for actual building footprints
    """
```

**Implementation v0.1 (STUB):**
- This Wave 1 job lands the TOOL REGISTRATION + signature + a documented stub that raises `PelicunNotImplementedYet` with `retryable=False` AND a clear actionable message
- DO NOT implement the actual Pelicun integration here — that's the Wave 2 job-0106 composer
- The stub validates inputs (URIs exist, fragility_set is in allowed set), then raises `PelicunNotImplementedYet("Implementation deferred to job-0106 composer; this tool registration locks the LLM-visible API contract.")`
- Cache: ttl static-30d (real impl will produce reproducible results), source_class="pelicun_damage"

**Why a stub now**: the LLM tool registry must include the API contract so Case-1-style prompts can compile a workflow that USES this tool, even before the implementation lands. Wave 2's composer (job-0106) wires the real Pelicun runtime + fragility DB + Monte-Carlo loop.

**Tests** (≥3 unit):
- Input validation: bad fragility_set → typed error
- Input validation: missing assets_uri → typed error
- Stub raises `PelicunNotImplementedYet` with the expected message

**Live verification**: invoke against placeholder URIs → raises `PelicunNotImplementedYet` with the message text; evidence/pelicun_stub_live.txt

**Register**: `tools/__init__.py` + `main.py` 1 line each. Verify via `--startup-only`.

### File ownership (exclusive)

- `services/agent/src/grace2_agent/tools/run_pelicun_damage_assessment.py` (NEW)
- `services/agent/src/grace2_agent/tools/__init__.py` — 1 line
- `services/agent/src/grace2_agent/main.py` — 1 line
- `services/agent/tests/test_run_pelicun_damage_assessment.py` (NEW)
- `reports/inflight/job-0098-engine-20260608/`


### FROZEN

All other `tools/*` (each Wave 1 sibling has its own file ownership); all `workflows/`, `services/workers/`, `packages/contracts/`, `web/`, `infra/`, `docs/srs/`, `styles/`, `reports/complete/**`.


### Concurrency note (Wave 1 fan-out)

~15 Wave 1 jobs run concurrently. Each owns its own NEW tool file but ALL share `tools/__init__.py` + `main.py` registration sites. The idempotent-append pattern from sprint-11 Stage 1 (which handled 6 concurrent additions cleanly) applies: ADD your import line at the end of each file; if your line conflicts with a sibling's, do `git pull --rebase` style re-apply; do NOT remove other tool imports.


### Codified lesson (job-0086, do not violate)

URL/render consistency != geographic correctness. In-COG axis mirrors and similar in-file orientation bugs are invisible to every consistency check (server, client, PIL composite all faithfully serve the mirrored array). If your tool emits geometry, your acceptance test MUST verify the output against the **known geography of the bbox** (e.g. "is the deep-flood pixel at the river mouth?"), not just "did the bytes round-trip?".


### Acceptance criteria

- [ ] New tool registered + visible at `--startup-only` (count = entering_count + 1)
- [ ] ≥4 unit tests + 1 live test (with appropriate env-var guard)
- [ ] Live verification with real upstream response captured to evidence/
- [ ] Geography correctness check per the codified job-0086 lesson (where applicable)
- [ ] No FROZEN edits; single commit prefix `<job-id>:`; co-author line
- [ ] Returns commit SHA + outcome + 1-paragraph headline + evidence paths + any OQs

