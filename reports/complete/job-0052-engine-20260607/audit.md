# Audit: HYDROMT_BUILD_FAILED — opt argument shape one-line hotfix (closes OQ-49-HYDROMT-BUILD-OPT-ARGUMENT-SHAPE)

**Job ID:** job-0052-engine-20260607, **Sprint:** sprint-08 (mid-sprint hotfix), **Auditor:** Development Orchestrator, **Status:** approved

## Task Assignment

**Specialist:** engine

**Prerequisites:**
- job-0049 diagnosis (this job IS the fix for OQ-49-HYDROMT-BUILD-OPT-ARGUMENT-SHAPE)
- job-0042 + job-0044 + job-0043 (M5 chain context)

**SRS references** (narrow file loading only):
- `docs/decisions/oq-4-hydromt-depth.md` (HydroMT contract)
- DO NOT load `docs/SRS_v0.3.md` monolith.

**Required reads:**
- `reports/complete/job-0049-infra-20260607/report.md` — diagnosis trace, exact failure mode, line number
- `services/agent/src/grace2_agent/workflows/sfincs_builder.py:692` — the actual offending line

### The bug
`sfincs_builder.py:692` passes a raw YAML text string to `SfincsModel.build(opt=...)`, but `hydromt-sfincs 1.2.x` expects a parsed dict. Error: `'str' object has no attribute 'keys'`.

### Scope (small focused hotfix)

1. **One-line fix in `services/agent/src/grace2_agent/workflows/sfincs_builder.py:692`** — call `yaml.safe_load(opt_text)` to produce a dict before passing to `SfincsModel.build(opt=...)`. Add `import yaml` at the top of the file if not present.

2. **Re-run job-0042 / job-0043 M5 chain** to verify the fix advances past `HYDROMT_BUILD_FAILED`. Honest disclosure of the next outcome:
   - **SUCCESS:** SFINCS deck builds; chain dispatches to Cloud Workflows; SFINCS solver runs; produces flood-depth COG; AssessmentEnvelope returns successfully. **This is the first successful M5 pipeline in project history.** Capture comprehensive evidence (workflow execution ID, runs bucket URI, sample raster bytes).
   - **NEXT HONEST BLOCKER:** chain advances to the next failure mode (e.g., something else in the HydroMT build sequence, the SFINCS solver itself, etc.). Capture + honestly disclose; opens follow-up hotfix.

3. **Tests** in `services/agent/tests/test_model_flood_scenario.py` — add at least 2 tests: one that exercises the corrected dict-passing path with a mocked HydroMT; one that exercises the failure path (yaml.safe_load raises on malformed YAML → surface as typed error per FR-FR-2 substrate-integrity routing).

4. **If the chain produces a real flood-depth COG**: capture screenshot of the rendered layer via orchestrator-direct Playwright (per memory feedback_orchestrator_drives_ui_verification). The orchestrator owns the screenshot capture; specialist just captures the GCS URI + sample bytes in evidence.

### File ownership (exclusive)
- `services/agent/src/grace2_agent/workflows/sfincs_builder.py` — only the offending line + import
- `services/agent/tests/test_model_flood_scenario.py` — additive tests
- `reports/inflight/job-0052-engine-20260607/`

### FROZEN
- All other workflows/* files
- All tools/* files
- packages/contracts/**, infra/**, web/**, docs/srs/**, styles/**, services/workers/**, reports/complete/**
- Stage A concurrent jobs (0045 + 0046)

### Acceptance criteria
- [ ] One-line yaml.safe_load fix in sfincs_builder.py:692 (+ import yaml)
- [ ] Re-run M5 chain past HYDROMT_BUILD_FAILED with honest disclosure of next outcome
- [ ] ≥2 new tests
- [ ] No edits to FROZEN paths
- [ ] Closes OQ-49-HYDROMT-BUILD-OPT-ARGUMENT-SHAPE
- [ ] If real flood-depth COG produced, capture comprehensive evidence so orchestrator can capture the screenshot moment via direct Playwright

## Assessment

**Verdict:** approved.

Targeted yaml.safe_load hotfix lands cleanly. The hydromt-sfincs 1.2.x `SfincsModel.build` API calls `.keys()` on each step value inside `opt`, so the raw YAML text crashed at parse step 1. Specialist correctly diagnosed (the failure mode is precise: `'str' object has no attribute 'keys'`) and applied the minimal fix: parse the YAML text to a dict before passing.

**M5 chain advances meaningfully — from 11.47s to 41.11s execution.** The +30s delta is HydroMT actually executing setup steps: `setup_grid_from_region` (bbox + 30m + UTM) + `setup_dep` (DEM IDW interp on 96 cells from cached canonical-NLCD bytes) + `setup_mask_active` (zmin=-10, zmax=10) + region geometry derived. **Next honest blocker:** `setup_manning_roughness()` rejects `map_fn` kwarg in hydromt-sfincs 1.2.x. Routed as OQ-52-MANNING-ROUGHNESS-MAP-FN-MISMATCH for next focused hotfix.

**The substrate-integrity guard worked exactly as designed:** the manning_roughness TypeError was caught by the broad `except` block and surfaced as typed `SFINCSSetupError("HYDROMT_BUILD_FAILED")` — Invariant 7 preserved with honest typed surfacing rather than silent fall-through.

Tests: +2 (regression guard that opt is a dict; FR-FR-2 substrate-integrity routing for malformed YAML → typed error). Agent suite 130→132. Contracts 142/142 unchanged.

**Pattern observation (for orchestrator routing).** This is the second consecutive hotfix on the same M5 chain (0049 → 0052 → next). Each hotfix takes ~110K Opus tokens. If the next hotfix (0053) reveals yet another hydromt-sfincs 1.2.x API mismatch, I escalate to a comprehensive API-migration job rather than chaining a 4th. The chain is real progress — we went from "library not installed" → "library installed but build fails on YAML shape" → "build proceeds further, fails on manning_roughness kwarg signature" — but it's also showing that the workflow code's hydromt-sfincs API assumptions predate 1.2.x.

## Invariant Check

- **Invariant 1, 2:** preserved. yaml.safe_load is deterministic.
- **Invariant 7:** preserved + verified — the manning_roughness TypeError got caught + typed-error-surfaced rather than producing silently wrong Manning's grid.
- **§3.10 FR-FR-2 substrate-integrity routing:** new regression test for malformed YAML → typed HYDROMT_BUILD_FAILED routes correctly.

## Decisions Validated

- yaml.safe_load vs full yaml.load — correct (safe_load avoids arbitrary code execution from malicious YAML; standard Python YAML discipline).
- Add import yaml at top of file — clean.
- Test pair (regression guard + malformed-YAML failure path) — both correctly land.

## Open Questions Resolved

**Closes:** OQ-49-HYDROMT-BUILD-OPT-ARGUMENT-SHAPE.

Filed for triage:
- **OQ-52-MANNING-ROUGHNESS-MAP-FN-MISMATCH** — `setup_manning_roughness()` rejects `map_fn` in hydromt-sfincs 1.2.x. Fix is in `_generate_hydromt_yaml_config()` — remove the `map_fn` key OR rename to 1.2.x-accepted kwarg in `datasets_rgh[*]`. Routes to job-0053 hotfix.

## Follow-up Actions

1. **Open job-0053 (engine hotfix)** for OQ-52-MANNING-ROUGHNESS-MAP-FN-MISMATCH — one focused fix. If THIS reveals yet another 1.2.x API mismatch, escalate to a comprehensive API-migration job rather than chaining a 4th hotfix.
2. **OQ-4 decision-doc** + sprint-08 close housekeeping should capture the hydromt-sfincs 1.2.x API discovery pattern as the live-verification-pays-off lesson.

## Sign-off

**Approved 2026-06-07 by Development Orchestrator.** Closes OQ-49. M5 chain advanced meaningfully (one more focused hotfix away from attempting a real SFINCS solver run).
