# Audit: setup_manning_roughness `map_fn` kwarg mismatch hotfix (closes OQ-52)

**Job ID:** job-0053-engine-20260607, **Sprint:** sprint-08 (mid-sprint hotfix #2), **Auditor:** Development Orchestrator, **Status:** approved

## Task Assignment

**Specialist:** engine

**Prerequisites:**
- job-0049 + job-0052 (chained M5 unblocking work; this is the next focused fix)

**SRS references** (narrow file loading only):
- `docs/decisions/oq-4-hydromt-depth.md`
- DO NOT load `docs/SRS_v0.3.md` monolith.

**Required reads:**
- `reports/complete/job-0052-engine-20260607/report.md` — diagnosis trace + the smoke run that revealed this blocker

### The bug
After job-0052's yaml.safe_load hotfix, the M5 chain advances to `setup_manning_roughness()` which rejects the `map_fn` keyword argument. hydromt-sfincs 1.2.x changed the `setup_manning_roughness` API — either renamed `map_fn` to something else or removed it entirely. The kwarg is being generated in `_generate_hydromt_yaml_config()` inside the `datasets_rgh[*]` mapping.

### Scope (focused hotfix)

1. **Investigate hydromt-sfincs 1.2.x `setup_manning_roughness` signature.** Read the actual library source or use `inspect.signature(model.setup_manning_roughness)` to discover the canonical 1.2.x keyword arguments. Don't guess from documentation — verify from the live signature.

2. **Fix in `_generate_hydromt_yaml_config()`** — remove or rename `map_fn` to the 1.2.x-accepted kwarg. Likely candidates based on hydromt-sfincs evolution: the API may have moved to a `mapping_fn` field, OR removed it in favor of direct CSV ingestion via `lulc_mapping_fn` parameter, OR collapsed it into a `params` sub-dict. The actual fix depends on the live signature.

3. **Re-run job-0042/0043 M5 chain** to verify the fix advances past the new blocker. **HONEST DISCLOSURE** of next outcome:
   - **SUCCESS:** SFINCS deck builds completely; chain dispatches to Cloud Workflows; SFINCS solver runs; produces flood-depth COG; AssessmentEnvelope returns. **FIRST SUCCESSFUL M5 PIPELINE IN PROJECT HISTORY.** Capture comprehensive evidence (workflow execution ID, runs bucket URI, sample raster bytes).
   - **NEXT HONEST BLOCKER:** Yet another hydromt-sfincs 1.2.x API mismatch. **Capture + honestly disclose.** If this happens, the orchestrator escalates to a comprehensive `hydromt-sfincs 1.2.x API migration audit` job rather than chaining a 4th hotfix.

4. **Tests** in `services/agent/tests/test_model_flood_scenario.py` — add at least 1 test exercising the corrected setup_manning_roughness kwarg path with mocked HydroMT.

5. **If the chain produces a real flood-depth COG**: capture comprehensive evidence so orchestrator can capture the screenshot moment via direct Playwright (per memory feedback_orchestrator_drives_ui_verification).

### File ownership (exclusive)
- `services/agent/src/grace2_agent/workflows/sfincs_builder.py` — only the manning_roughness related lines + signature investigation
- `services/agent/tests/test_model_flood_scenario.py` — additive
- `reports/inflight/job-0053-engine-20260607/`

### FROZEN
- All other workflows/* files
- All tools/* files
- packages/contracts/**, infra/**, web/**, docs/srs/**, styles/**, services/workers/**, reports/complete/**

### Acceptance criteria
- [ ] `_generate_hydromt_yaml_config()` produces a kwarg shape that hydromt-sfincs 1.2.x `setup_manning_roughness` accepts
- [ ] Live signature inspection cited in the report
- [ ] M5 chain re-run; outcome honestly disclosed (SUCCESS = real flood-depth COG; NEXT BLOCKER = next mismatch)
- [ ] At least 1 new test
- [ ] No edits to FROZEN paths
- [ ] Closes OQ-52-MANNING-ROUGHNESS-MAP-FN-MISMATCH
- [ ] If new outcome reveals yet another mismatch, surface as OQ-53-* and recommend escalation to comprehensive API-migration audit

## Assessment

**Verdict:** approved (with explicit escalation per the pinned rule).

The manning_roughness fix lands cleanly with **exemplary live-signature-inspection discipline**. Specialist used `inspect.signature(SfincsModel.setup_manning_roughness)` directly to discover the v1.2.2 API: `(self, datasets_rgh: List[dict] = [], manning_land=0.04, manning_sea=0.02, rgh_lev_land=0)`. **No `map_fn` kwarg at any level.** Source-level inspection of `_parse_datasets_rgh` revealed that the LULC reclass table lives INSIDE each `datasets_rgh` entry under `reclass_table` (which HydroMT then reads via `data_catalog.get_dataframe(reclass_table, index_col=0)` then `df_map[["N"]]` — first column = LULC index, required column literally named `N`).

The fix is precise: drop `map_fn`, reshape to `datasets_rgh: [{ lulc: ..., reclass_table: ... }]`, add `_write_hydromt_reclass_table_csv` helper that materializes the in-memory `load_manning_mapping` dict to a v1.2.x-shaped `nlcd_class,N` CSV in the per-build temp dir. **The authored `manning_mapping.csv` substrate (FROZEN by OQ-4 §4 validation gate) is unchanged** — Invariant 7 protection preserved end-to-end.

**M5 chain advances yet another step**, then hits the next class of mismatch:
- Pipeline: `setup_grid_from_region` → `setup_dep` → `setup_mask_active` → **`setup_manning_roughness` CLEAR (OQ-52 closed)** → fails at `setup_river_inflow` with `No data was read from source: merit_hydro (No data available)`.
- Failure mode is **DataCatalog binding**, not signature kwarg. Different class of issue. The hydromt-sfincs `merit_hydro` source needs to be registered in our data catalog (or the YAML needs to reference a different source).

**THE ESCALATION RULE FIRES.** Three consecutive hydromt-sfincs 1.2.x mismatches in `sfincs_builder.py`:
1. OQ-49: yaml-shape on `.build(opt=...)` — fixed
2. OQ-52: `map_fn` kwarg removed from `setup_manning_roughness` — fixed
3. OQ-53: `merit_hydro` DataCatalog binding not wired — NOT fixing in a 4th hotfix per the orchestrator's pinned rule

Each hotfix has advanced the chain by ~30 seconds of additional setup-step execution. Pattern is clear: the workflow code's hydromt-sfincs API assumptions are systematically out of date with 1.2.x. **Surface-level fixes aren't converging.** The specialist correctly recommends opening a comprehensive `hydromt-sfincs 1.2.x API-migration audit` job that:
- Uses `inspect.signature` on EVERY `setup_*` method our YAML emits (`setup_river_inflow`, `setup_precip_forcing`, `setup_subgrid`, etc.)
- Cross-walks each against the 1.2.2 source
- Audits DataCatalog wiring (which sources need to be registered + where the source data files live)
- Lands ALL mismatches in a single pass

**This is the right escalation.** Three Opus-hotfix tokens (~330K total) have advanced the chain; a 4th would consume more without confidence it ends the cascade. A comprehensive audit takes the same token budget but produces a complete remediation plan rather than another partial advance.

Tests: +1 (`test_build_sfincs_model_emits_v1_2_x_manning_roughness_kwargs`) → 14 in test_model_flood_scenario.py; 133/133 agent suite total. Test guards no-top-level-map_fn + only-v1.2.x-kwargs + reclass_table-inside-datasets_rgh + helper-CSV-has-N-column.

## Invariant Check

- **Invariant 1, 2:** preserved.
- **Invariant 7:** strengthened — the chain still fails closed with typed error rather than silent-wrong. The FROZEN manning_mapping.csv substrate is unchanged; we just materialize it differently for v1.2.x.
- **§3.10 FR-FR-2 substrate-integrity routing:** the merit_hydro DataCatalog failure surfaces as typed `SFINCSSetupError("HYDROMT_BUILD_FAILED")` per the broad-except routing.

## Decisions Validated

- **Live signature inspection over documentation** — the right pattern; documentation was stale, signature was canonical.
- **Materialize CSV in per-build temp dir** — clean ephemeral storage; survives only for the build duration.
- **Don't modify FROZEN manning_mapping.csv** — sprint-7's OQ-4 validation gate substrate stays intact.

## Open Questions Resolved

**Closes:** OQ-52-MANNING-ROUGHNESS-MAP-FN-MISMATCH.

Filed for triage:
- **OQ-53-MERIT-HYDRO-CATALOG-BINDING** — NEW class of mismatch (DataCatalog wiring, not signature kwarg). Routes to the comprehensive audit job, not a 4th hotfix.

## Follow-up Actions

1. **OPEN job-0054 (engine: comprehensive hydromt-sfincs 1.2.x API-migration audit)** — Opus, larger scope than a hotfix. Inspect every `setup_*` signature + DataCatalog wiring; cross-walk to v1.2.2 source; land all mismatches in a single pass. Counter 53 → 54.
2. **OPEN job-0047 (engine: catalog_search + catalog_fetch + OGC adapter)** as Stage B — does NOT depend on the M5 success path; can launch in parallel with 0054.

## Sign-off

**Approved 2026-06-07 by Development Orchestrator.** Closes OQ-52. Escalation rule pinned in 0052's audit fires correctly here — third consecutive hotfix advances the chain but reveals yet another mismatch, this time of a different class. Comprehensive API-migration audit replaces a 4th surface-level hotfix.
