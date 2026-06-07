# Audit: Comprehensive hydromt-sfincs 1.2.x API migration audit (escalation from 0049/0052/0053 hotfix chain)

**Job ID:** job-0054-engine-20260607, **Sprint:** sprint-08, **Auditor:** Development Orchestrator, **Status:** assigned

## Task Assignment

**Specialist:** engine

**Prerequisites:**
- job-0042 (sfincs_builder.py + manning_mapping.csv substrate)
- job-0049 (HYDROMT_UNAVAILABLE → installed)
- job-0052 (HYDROMT_BUILD_FAILED yaml.safe_load fix; OQ-49 closed)
- job-0053 (setup_manning_roughness map_fn fix; OQ-52 closed; recommends THIS audit)

**SRS references** (narrow file loading only):
- `docs/decisions/oq-4-hydromt-depth.md` — HydroMT contract; Invariant 7 mitigation requirement
- DO NOT load `docs/SRS_v0.3.md` monolith.

### Why this job exists
Three consecutive surface-level hotfixes (0049/0052/0053) each advanced the M5 chain by one setup step then revealed the next 1.2.x mismatch. Pattern is clear: `sfincs_builder.py` was authored against a pre-1.2.x hydromt-sfincs API and needs systematic migration, not chained one-line fixes. **Orchestrator's escalation rule from 0052's audit fires: comprehensive audit replaces a 4th hotfix.**

### Scope

1. **Live-inspect every hydromt-sfincs 1.2.x API surface our YAML emits.** Use `inspect.signature` (and source reading where signatures don't disclose semantics) on each `setup_*` method our YAML touches:
   - `setup_grid_from_region` ✓ (working — confirmed by 0053 trace; verify signature didn't change semantics)
   - `setup_dep` ✓ (working — verify)
   - `setup_mask_active` ✓ (working — verify)
   - `setup_manning_roughness` ✓ (fixed by 0053; verify the test guards hold)
   - `setup_river_inflow` — **CURRENT BLOCKER** — needs DataCatalog wiring for `merit_hydro`
   - `setup_precip_forcing` — verify signature + DataCatalog binding
   - `setup_subgrid` (if our YAML uses it) — verify
   - `setup_forcing` (if our YAML uses it) — verify
   - Any other `setup_*` our YAML emits — enumerate from `_generate_hydromt_yaml_config`

2. **Cross-walk each against the hydromt-sfincs 1.2.2 source.** Read the actual library code (`.venv-agent/lib/python3.X/site-packages/hydromt_sfincs/`) — not docs — for each method. Document the canonical 1.2.x signature + expected DataCatalog source names.

3. **DataCatalog binding audit.** The `merit_hydro` source not loading is a different class of failure (binding, not signature). Audit our DataCatalog setup:
   - Which sources does our YAML reference (`merit_hydro`, `osm_buildings`, ATCF, etc.)?
   - Are they registered in our `DataCatalog` instance?
   - If they need data files, are those files accessible (GCS, local, or hydromt-built-in)?
   - hydromt-sfincs ships with `data_catalog.yml` defaults — are they accessible to us?

4. **Land all mismatches in a single pass** in `sfincs_builder.py`. Single commit; comprehensive remediation. Tests update accordingly.

5. **Re-run job-0042/0043 M5 chain** for final outcome:
   - **SUCCESS:** First successful M5 pipeline ever — capture comprehensive evidence + GCS URI + sample bytes. Orchestrator captures screenshots via direct Playwright.
   - **PARTIAL SUCCESS:** Chain advances further but hits a *new class* of failure (not a 1.2.x mismatch — e.g., real SFINCS solver behavior, infrastructure issue, data availability). This is the legitimate end of the migration audit; honest disclosure.
   - **STILL BLOCKED ON 1.2.x MISMATCH:** Audit was insufficient; document specifically what was missed for the next-level escalation.

6. **Tests** in `services/agent/tests/test_model_flood_scenario.py` — comprehensive test coverage for each `setup_*` method's v1.2.x kwarg expectations. At least 3+ new tests; the existing manning_roughness guards from 0053 stay.

### File ownership (exclusive)
- `services/agent/src/grace2_agent/workflows/sfincs_builder.py` — comprehensive migration; all signatures + DataCatalog
- `services/agent/tests/test_model_flood_scenario.py` — additive tests
- `services/agent/pyproject.toml` — if any new dep is needed (e.g., a hydromt data-source plugin)
- `reports/inflight/job-0054-engine-20260607/`

### FROZEN
- `services/agent/src/grace2_agent/workflows/manning_mapping.csv` — OQ-4 §4 substrate (FROZEN)
- All other workflows/* and tools/* files
- packages/contracts/**, infra/**, web/**, docs/srs/**, styles/**, services/workers/**, reports/complete/**

### Acceptance criteria
- [ ] Every `setup_*` method our YAML emits has its v1.2.x signature documented in the report
- [ ] Every DataCatalog source binding our YAML references is either correctly wired OR explicitly documented as a blocker with remediation path
- [ ] One comprehensive commit; no further hotfixes chained
- [ ] M5 chain re-run with honest disclosure of final outcome (SUCCESS, partial-success-new-class, or still-blocked-document-what-was-missed)
- [ ] At least 3 new tests covering the audited signatures
- [ ] No edits to FROZEN paths (especially manning_mapping.csv)
- [ ] Closes OQ-53-MERIT-HYDRO-CATALOG-BINDING (and any other 1.2.x OQs discovered)
- [ ] If SUCCESS, capture comprehensive evidence so orchestrator captures screenshot moment
