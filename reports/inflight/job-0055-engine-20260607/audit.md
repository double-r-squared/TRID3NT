# Audit: drop `setup_river_inflow` from v0.1 pluvial deck (OQ-54 remediation b)

**Job ID:** job-0055-engine-20260607, **Sprint:** sprint-08 (mid-sprint follow-up to escalation audit job-0054), **Auditor:** Development Orchestrator, **Status:** assigned

## Task Assignment

**Specialist:** engine

**Prerequisites:**
- **job-0054 (APPROVED):** Comprehensive hydromt-sfincs 1.2.x API migration audit. Established that chain advances through all 5 setup steps cleanly (including `setup_river_inflow` finding 39 inflow points), then dies inside hydromt-sfincs 1.2.2's `set_forcing_1d` at `pd.RangeIndex.is_integer()` — removed in pandas ≥ 2.0, we run 3.0.3. Upstream library bug, not API mismatch. Routing recommendation (b): drop `setup_river_inflow` from v0.1 pluvial deck.
- job-0053 (APPROVED) — manning roughness migration substrate
- job-0042 (APPROVED) — model_flood_scenario workflow + NLCD validation gate

**SRS references** (narrow file loading only):
- `docs/decisions/oq-4-hydromt-depth.md` — OQ-4 §4 paper says "100-year 24-hour design storm" — pluvial, no river inflow required for v0.1
- DO NOT load `docs/SRS_v0.3.md` monolith.

**Required reads:**
- `reports/complete/job-0054-engine-20260607/report.md` — the audit that identified this as the smallest v0.1 path
- `services/agent/src/grace2_agent/workflows/sfincs_builder.py` lines 599-610 — the current `setup_river_inflow` emission block

### Why this job exists

job-0054 closed comprehensively with a recommendation: drop `setup_river_inflow` from the v0.1 pluvial deck. The river-inflow forcing is M5+ scope (real Hurricane Ian + ATCF + storm-surge boundary forcing); v0.1's M5 demo is pluvial-only (Atlas 14 design storm). Dropping `setup_river_inflow` should:
1. **Stop the chain from calling `set_forcing_1d`** (the pandas-3 incompat is in the 1D-forcing finalization path — exercised by river inflow's discharge-point output)
2. **Match documented v0.1 scope** — OQ-4 §4 / sprint-7 capstone / job-0042 NLCD-gate kickoff all frame M5 as pluvial-only
3. **Potentially produce the first real flood-depth COG** — the chain would then dispatch to `run_solver` and complete the M5 SUCCESS branch

### Scope

1. **`services/agent/src/grace2_agent/workflows/sfincs_builder.py`** — remove the `setup_river_inflow` emission block (lines 599-610 currently). Replace with a short comment block citing the OQ-54 v0.1 scope decision. The `river_local_path` parameter stays in the function signature (don't break call sites yet); it's just not emitted into the YAML. Document the v0.1 → v0.2+ migration path: when ATCF + real-storm-forcing lands in sprint-9, river inflow is re-enabled by adding the block back AND either downgrading pandas OR patching hydromt-sfincs upstream.

2. **`services/agent/tests/test_model_flood_scenario.py`** — add at least one new test guarding that `setup_river_inflow` is NOT emitted in `pluvial_synthetic` mode (the v0.1 scope guard). The OQ-53 + all-steps audit guards from job-0054 stay as-is.

3. **Re-run M5 chain** — use the existing `reports/complete/job-0054-engine-20260607/evidence/smoke_demo.py` harness as the reference. Capture results into `reports/inflight/job-0055-engine-20260607/evidence/`:
   - **SUCCESS (the headline):** chain produces a real flood-depth COG. **Capture detailed evidence so orchestrator captures the screenshot moment via direct Playwright per memory rule.** Log every step's wall-clock; capture `AssessmentEnvelope` JSON; capture the GCS URI of the flood-depth output; capture a sample byte-read confirming the COG is real.
   - **PARTIAL SUCCESS:** chain advances further but hits a NEW class of failure (e.g., solver setup, infrastructure). Honest disclosure.
   - **NEW CLASS OF FAILURE:** still blocked but on a different mechanism (e.g., subgrid setup, mask resolution). Document specifically.
   - **STILL THE SAME PANDAS-3 BUG:** then the upstream bug fires from another path; document which and route to either pandas pin or upstream patch.

### File ownership (exclusive)
- `services/agent/src/grace2_agent/workflows/sfincs_builder.py` — ONLY the `_generate_hydromt_yaml_config` body (drop the `setup_river_inflow` emission block + add the v0.1 scope comment)
- `services/agent/tests/test_model_flood_scenario.py` — additive tests only
- `reports/inflight/job-0055-engine-20260607/`

### FROZEN
- `services/agent/src/grace2_agent/workflows/manning_mapping.csv` — OQ-4 §4 substrate (FROZEN since job-0042)
- `services/agent/src/grace2_agent/workflows/model_flood_scenario.py` — call site stays the same; no signature change
- All other workflows/* and tools/* files (especially `tools/catalog.py` + `tools/ogc_adapter.py` from concurrent-closed job-0047)
- packages/contracts/**, infra/**, web/**, docs/srs/**, styles/**, services/workers/**, reports/complete/**

### Acceptance criteria
- [ ] `setup_river_inflow:` block no longer appears in the YAML emitted by `_generate_hydromt_yaml_config` when `forcing_type == "pluvial_synthetic"`
- [ ] At least 1 new test guards against re-introducing the block in pluvial mode
- [ ] All pre-existing tests still pass (OQ-53 + OQ-54 + all-steps audit from job-0054 stay green)
- [ ] M5 chain re-run with honest disclosure of final outcome:
   - **SUCCESS:** capture comprehensive evidence; flood-depth COG; orchestrator captures screenshot
   - **PARTIAL SUCCESS:** chain advances further; new honest blocker documented
   - **STILL BLOCKED:** specific failure documented
- [ ] No edits to FROZEN paths
- [ ] If SUCCESS, explicitly call out the screenshot moment so orchestrator captures it
- [ ] Single commit
