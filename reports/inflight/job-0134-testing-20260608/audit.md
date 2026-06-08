# Audit: Case 1 live acceptance — Everglades/Big Cypress flood + habitat E2E

**Job ID:** job-0134-testing-20260608, **Sprint:** sprint-12-mega Wave 3, **Specialist:** testing

**Required reads:**
- `services/agent/src/grace2_agent/workflows/model_flood_habitat_scenario.py` (job-0118)
- `services/agent/src/grace2_agent/tools/_species_reference.py` (job-0117 — Florida demo species)
- Memory: `project_demo_case_3_idaho_flood` (related — case-as-demo pattern)

### Scope

Run the Case 1 composer end-to-end against a real Florida bbox; produce headline screenshot evidence.

1. **Run live**: invoke `model_flood_habitat_scenario`:
   - bbox: Big Cypress region (-81.5, 25.7, -80.7, 26.5)
   - species_keys: [2435099 (Florida panther), 2481008 (Roseate spoonbill), 2436873 (American alligator)]
   - rainfall_event: "atlas14_100yr"
   - protected_area_designation: None (capture all WDPA in bbox)
   - place_clip_polygon_uri: None (no clip for the demo)
2. **Validate result**:
   - flood_layer_uri: SFINCS COG published to QGIS Server
   - species_layers: 3 LayerURIs, each its own GBIF FlatGeobuf
   - wdpa_layer_uri: FlatGeobuf with Everglades NP + Big Cypress NP + other protected areas
   - impact_metrics: zonal_statistics output dict
   - case_summary_text: human-readable summary
3. **Playwright screenshot** at z11 dark theme:
   - All 4 layers visible (flood depth COG with continuous_flood_depth style + 3 species point layers in different colors + WDPA polygons)
   - Chat shows the case_summary_text inline
   - At minimum: capture `aligned_dark_z11.png` (with overlays) + `aligned_dark_z11_basemap_only.png` (overlays hidden — for the alignment proof codified lesson)
4. **Geographic-correctness gate**: verify wet flood pixels appear over the modeled drainages (not the wrong corner), species points appear inside the requested bbox, WDPA polygons match Everglades NP outline at the basemap level

**Tests run**:
- Full agent + contracts + web suites: 0 regressions
- Specific test_model_flood_habitat_scenario.py live test (env-gated)
- Playwright dev acceptance capture (orchestrator-direct pattern)

**Output**:
- Evidence dir: `reports/inflight/job-0134-testing-20260608/evidence/`
- Screenshots: `case1_z11_dark.png`, `case1_z11_dark_basemap_only.png`, `case1_z11_dark_layers_panel.png` (showing 4 layers in the panel)
- `case1_metrics.json`: impact_metrics + case_summary_text
- `case1_acceptance.md`: write-up

### File ownership (exclusive)

- `reports/inflight/job-0134-testing-20260608/` and below
- May orchestrator-direct Playwright capture script in `evidence/case1_capture.py`

### FROZEN

- All implementation files (Wave 1/1.5/2 deliverables)


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

