# Audit: Pelicun acceptance — Fort Myers damage screenshot

**Job ID:** job-0136-testing-20260608, **Sprint:** sprint-12-mega Wave 3, **Specialist:** testing

**Required reads:**
- `services/agent/src/grace2_agent/tools/run_pelicun_damage_assessment.py` (job-0120 — real impl)
- Job-0086 Fort Myers Y-flip-fixed COG: `gs://grace-2-hazard-prod-runs/01KTJX71NKGDMXB9TN0DV75JWK/flood_depth_peak_0086.tif`

### Scope

Run Pelicun on Fort Myers flood; produce damage-state choropleth screenshot.

1. **Run live**:
   - hazard_raster_uri = job-0086 flood COG
   - assets_uri = `fetch_administrative_boundaries(level='place', bbox=fort_myers_bbox)` filtered to Fort Myers CDP, OR use `compute_building_density` output as point assets
   - fragility_set="hazus_flood_v6"
   - realization_count=500
   - component_types=None (use Pelicun default RES1)
2. **Validate result**:
   - Output FlatGeobuf with damage-state probability properties (ds_mean, ds_p05, ds_p95, repair_cost_mean, replacement_value)
   - ≥1 asset feature
   - All damage states in valid range [0, 1]
3. **Playwright screenshot** at z12 dark theme:
   - Damage-state choropleth rendered on Fort Myers basemap
   - Each asset colored by ds_mean (e.g. green=low, red=high)
   - Layers panel shows the damage layer with its style preset
4. **Acceptance**: damage states distribute meaningfully (not all zero, not all one); high-flood-depth assets correlate with higher damage states

**Output**:
- Evidence dir: `reports/inflight/job-0136-testing-20260608/evidence/`
- Screenshots: `pelicun_z12_dark.png`, `pelicun_z12_dark_basemap_only.png`
- `pelicun_metrics.json`: summary stats
- `pelicun_acceptance.md`: write-up

### File ownership (exclusive)

- `reports/inflight/job-0136-testing-20260608/`

### FROZEN

- All implementation files


### FROZEN

All files outside the explicit file-ownership list. Especially: every sibling Wave 3 job's exclusive files; `reports/complete/**`.

### Codified lessons (do NOT violate)

1. **Geographic-correctness gate (job-0086)**: pixel-level evidence required for any "screenshot captured" claim. Verify actual content where it's supposed to be — wettest pixels at the river mouth, per-species layers in different colors, etc.
2. **Kickoff-front-loaded design**: execute scope, surface OQs, don't redesign.
3. **MongoDB MCP canonical persistence (job-0115 foundation)**: use Persistence.* — no custom CRUD wrappers.

### Acceptance criteria

- [ ] All deliverables landed per scope
- [ ] Geographic-correctness / pixel-level / behavioral verification per kickoff
- [ ] No FROZEN edits; single commit prefix `<job-id>:`; co-author line
- [ ] Returns commit SHA + outcome + 1-paragraph headline + evidence + OQs

