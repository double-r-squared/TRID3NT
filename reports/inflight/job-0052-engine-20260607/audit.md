# Audit: HYDROMT_BUILD_FAILED — opt argument shape one-line hotfix (closes OQ-49-HYDROMT-BUILD-OPT-ARGUMENT-SHAPE)

**Job ID:** job-0052-engine-20260607, **Sprint:** sprint-08 (mid-sprint hotfix), **Auditor:** Development Orchestrator, **Status:** assigned

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
