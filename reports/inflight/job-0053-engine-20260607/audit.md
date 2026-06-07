# Audit: setup_manning_roughness `map_fn` kwarg mismatch hotfix (closes OQ-52)

**Job ID:** job-0053-engine-20260607, **Sprint:** sprint-08 (mid-sprint hotfix #2), **Auditor:** Development Orchestrator, **Status:** assigned

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
