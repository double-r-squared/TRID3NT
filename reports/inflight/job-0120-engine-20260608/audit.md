# Audit: Pelicun composer + Fort Myers damage E2E run

**Job ID:** job-0120-engine-20260608, **Sprint:** sprint-12-mega Wave 2, **Specialist:** engine

**Required reads:**
- `services/agent/src/grace2_agent/tools/run_pelicun_damage_assessment.py` (Wave 1 stub — currently raises NotImplementedYet)
- `services/agent/src/grace2_agent/tools/fetch_administrative_boundaries.py` (asset proxy)
- `services/agent/src/grace2_agent/tools/compute_building_density.py` (Wave 1 — building footprint counts)
- Pelicun package: `pip install pelicun` (NHERI SimCenter)

### Scope

REPLACE the stub body in `services/agent/src/grace2_agent/tools/run_pelicun_damage_assessment.py` with the real implementation. (Wave 1 stub locked the API contract; now make it work.)

```python
def run_pelicun_damage_assessment(
    hazard_raster_uri: str,
    assets_uri: str,
    fragility_set: Literal["hazus_flood_v6", "fema_hazus_eq_2020"] = "hazus_flood_v6",
    component_types: list[str] | None = None,
    realization_count: int = 100,
) -> LayerURI:
    """FRAGILITY-CURVE-DRIVEN DAMAGE ASSESSMENT via Pelicun.
    [signature unchanged from Wave 1 stub]
    """
```

**Implementation**:
- Install `pelicun` (add to `services/agent/pyproject.toml` dependencies)
- Read hazard_raster_uri via rasterio
- Read assets_uri via geopandas; sample hazard at each asset (raster value at point/centroid)
- Default fragility_set="hazus_flood_v6": load HAZUS depth-damage curves from Pelicun's bundled data
- For each asset:
  - Look up curve by component_type (default "RES1" if not given)
  - Compute damage state probability vector via Pelicun's monte carlo
  - Sample `realization_count` realizations → mean + p05/p95 damage state
  - Compute repair_cost (Pelicun consequence model)
- Output: FlatGeobuf with assets + appended properties (ds_mean, ds_p05, ds_p95, repair_cost_mean, repair_cost_p95, replacement_value)
- Cache: ttl static-30d; key on (hazard_raster_uri, assets_uri, fragility_set, component_types sorted, realization_count)
- LayerURI(layer_type="vector", role="primary", units="damage_state")

**Tests** (≥6 unit + 1 live):
- Mocked hazard + assets → expected damage state probabilities
- fragility_set='hazus_flood_v6' uses correct curves
- component_types filter to ["COM1"] uses commercial curves
- realization_count effect: 1000 vs 100 → tighter CIs
- Cache miss/hit
- Live (env GRACE2_TEST_LIVE_PELICUN=1): Fort Myers job-0086 flood COG + fetch_administrative_boundaries(level='place', name='Fort Myers') → real damage assessment FlatGeobuf

**Live Fort Myers acceptance run**:
- hazard_raster_uri = `gs://grace-2-hazard-prod-runs/01KTJX71NKGDMXB9TN0DV75JWK/flood_depth_peak_0086.tif` (job-0086 Y-flip-fixed COG)
- assets_uri = output of `fetch_administrative_boundaries(level='place', bbox=fort_myers_bbox)` filtered to Fort Myers CDP
- fragility_set="hazus_flood_v6"
- realization_count=500
- Expected: FlatGeobuf with ≥1 asset feature carrying damage-state probabilities + Pelicun-computed repair_cost
- Save evidence to `reports/inflight/job-0120-engine-20260608/evidence/fort_myers_damage.fgb` + summary stats to `summary.txt`
- Optional: render damage-state choropleth screenshot via Playwright dev injection

**OQ-8 fragility-curve sourcing** — for v0.1 use Pelicun's bundled HAZUS curves. Document as OQ-8-RESOLVED-V0.1; FEMA P-58 swap is sprint-13+ work.

### File ownership (exclusive)

- `services/agent/src/grace2_agent/tools/run_pelicun_damage_assessment.py` — REPLACE stub
- `services/agent/tests/test_run_pelicun_damage_assessment.py` — extend with real-implementation tests
- `services/agent/pyproject.toml` — add pelicun dep
- `reports/inflight/job-0120-engine-20260608/`


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

