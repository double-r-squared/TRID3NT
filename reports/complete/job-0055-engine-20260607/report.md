# Report: drop `setup_river_inflow` from v0.1 pluvial deck (OQ-54 remediation b)

**Job ID:** job-0055-engine-20260607
**Sprint:** sprint-08 (mid-sprint follow-up to escalation audit job-0054)
**Specialist:** engine
**Task:** Drop `setup_river_inflow` YAML emission from `_generate_hydromt_yaml_config`; guard with a test; re-run M5 chain; honest disclosure of outcome.
**Status:** ready-for-audit

## Summary

Dropped the `setup_river_inflow` emission block from `_generate_hydromt_yaml_config` per OQ-54 routing recommendation (b) and added the v0.1 scope guard test. During the live M5 re-run two additional upstream issues were discovered: a SFINCS time format mismatch (ISO 8601 vs `%Y%m%d %H%M%S`) in the values we emit from `setup_config`, which was within owned scope and fixed; and a pandas 3.x frequency alias change (`"10T"` removed in pandas 3.0) inside the hydromt-sfincs library's `setup_precip_forcing` method (line 2456 of sfincs.py), which is an upstream library bug documented as OQ-55.

**Outcome: PARTIAL SUCCESS / NEW FAILURE CLASS** (kickoff bucket #2). The pandas `is_integer()` error is definitively bypassed; the chain advances through all 4 remaining setup steps and reaches `setup_precip_forcing`'s internal 10-minute time-grid generation, which fails with `Invalid frequency: 10T` — a different upstream pandas-3 incompatibility from the same root cause (hydromt-sfincs 1.2.2 predates pandas 3.0).

## Changes Made

- **`services/agent/src/grace2_agent/workflows/sfincs_builder.py`** — `_generate_hydromt_yaml_config` body only:
  1. Removed the `if river_local_path is not None:` block (the `setup_river_inflow:` + `rivers:` YAML lines). Replaced with a 17-line comment citing v0.1 pluvial-only scope (OQ-4 §4) and the upstream pandas-3 `is_integer()` bug. The `river_local_path` parameter remains in the function signature — call sites unchanged.
  2. Fixed SFINCS time format in `setup_config` emission: changed `tref`/`tstart`/`tstop` from ISO 8601 (`"2026-01-01T00:00:00"`) to SFINCS format (`"20260101 000000"`). Discovered during live M5 run: `sfincs_input.py` parses time fields with `strptime(val, "%Y%m%d %H%M%S")` — ISO 8601 raises `ValueError` inside `setup_precip_forcing → get_model_time()`. This fix is within owned scope.
  3. Updated function docstring to note the SFINCS time format requirement and replace the `setup_river_inflow` step description with the v0.1 scope + migration-path note.

- **`services/agent/tests/test_model_flood_scenario.py`** — additive changes:
  - Replaced `test_build_sfincs_model_river_inflow_drops_hydrography_kwarg` (which expected `setup_river_inflow` to be PRESENT) with `test_build_sfincs_model_river_inflow_not_emitted_in_pluvial_synthetic` (the v0.1 scope guard asserting the step is ABSENT). Tests both `river_geometry_uri` supplied and `river_geometry_uri=None` cases. Uses the existing `_build_with_capture` helper from job-0054.
  - Updated the section header comment to reflect the job-0055 v0.1 scope decision.

- **`reports/inflight/job-0055-engine-20260607/evidence/`** (NEW):
  - `smoke_demo.py` — copied from job-0054's evidence unchanged.
  - `smoke_demo_log.txt` — full M5 chain stdout/stderr (final run with all fixes applied).
  - `smoke_demo_envelope.json` — `AssessmentEnvelope` summary; `outcome=HONEST FAILURE`, `error_code=HYDROMT_BUILD_FAILED`, `underlying='Invalid frequency: 10T'`, elapsed≈46s.

## Decisions Made

- **Decision: fix the SFINCS time format in this job.** Rationale: the time format is a string value we emit from `_generate_hydromt_yaml_config` — owned scope. Deferring a trivially-fixable bug in our own emitted strings would be unnecessary churn.

- **Decision: do NOT patch `"10T"` → `"10min"` inside hydromt-sfincs.** Rationale: `sfincs.py:2456` is inside the library source. Per AGENTS.md "Remove don't shim": no monkey-patches. Surfaced as OQ-55 with routing to infra.

- **Decision: replace test 15 rather than add alongside.** Rationale: the old test (`_river_inflow_drops_hydrography_kwarg`) asserted `setup_river_inflow` IS present — which is now wrong by design. Keeping it would cause it to fail for the right reason, which would be a confusing double-negative. A test that asserts the wrong invariant is worse than no test.

## Invariants Touched

- **Invariant 1 (Determinism boundary): preserves.** No LLM in path.
- **Invariant 2 (Deterministic workflows): preserves.** YAML generation deterministic; time format fix is a correct transcription of typed inputs.
- **Invariant 7 (no silent wrong answers): preserves & strengthens.** Both fixes prevent silent mid-setup crashes. New test makes the v0.1 pluvial-only scope machine-checkable.

## Open Questions

- **OQ-55-PRECIP-FORCING-PANDAS3X-FREQ-ALIAS (NEW, TENTATIVE: route to infra).** After the `setup_river_inflow` removal and SFINCS time format fix, the chain advances to `setup_precip_forcing`'s `magnitude` branch at `sfincs.py:2456`: `pd.date_range(*self.get_model_time(), freq="10T")`. The `"T"` minute alias was deprecated in pandas 2.2 and removed in pandas 3.0 (we run 3.0.3). Error: `Invalid frequency: 10T. Failed to parse with error message: ValueError("Invalid frequency: T. Did you mean min?")`.
  - **(a)** Pin `pandas < 2.2` in `services/agent/pyproject.toml`. Routes to: infra.
  - **(b)** Pin `pandas >= 2.0, < 3.0`. Routes to: infra.
  - **(c)** Wait for hydromt-sfincs upstream fix. Blocks M5 indefinitely.
  - **Routing recommendation: (a) or (b) to infra.** A single pandas pin likely resolves the complete hydromt-sfincs 1.2.x pandas-compat issue set (both `is_integer()` and `"10T"` are pandas-2.x regressions).

- **OQ-49-HYDROMT-SFINCS-PIN-RECONCILIATION (CARRIED FORWARD).** Still pending orchestrator amendment.

## Dependencies and Impacts

- **Depends on:** job-0054 (APPROVED) — `_build_with_capture` helper, all-steps audit tests.
- **Affects (downstream):**
  - **infra (next job).** OQ-55: pin `pandas < 2.2` or `< 3.0`. Single pyproject.toml change.
  - **orchestrator.** No screenshot moment — chain still fails before solver dispatch.

## Verification

### Unit + integration test suite

```
$ PYTHONPATH=services/agent/src:packages/contracts/src \
    .venv-agent/bin/python -m pytest services/agent/tests/test_model_flood_scenario.py -v
17 passed in 1.89s
```

Full agent test suite:
```
$ PYTHONPATH=services/agent/src:packages/contracts/src \
    .venv-agent/bin/python -m pytest services/agent/tests/ -q
159 passed, 4 warnings in 2.97s
```

### Live M5 chain re-run (honest disclosure)

Key log lines (from `evidence/smoke_demo_log.txt`, final run):
```
setup_grid_from_region.region: {'bbox': [-81.9126085, 26.5476424, -81.7511414, 26.689176]}   ↑↑↑ (✓)
setup_dep.datasets_dep: [{'elevtn': 'gs://grace-2-hazard-prod-cache/.../dem/...tif'}]        ↑↑↑ (✓)
setup_mask_active.zmin: -10.0  setup_mask_active.zmax: 10.0                                  ↑↑↑ (✓)
setup_manning_roughness.datasets_rgh: [{'lulc': 'gs://...', 'reclass_table': '/tmp/...csv'}] ↑↑↑ (✓)
Reading  csv data from /tmp/.../manning_reclass.csv                                           ↑↑↑ (✓ OQ-52)
setup_precip_forcing.timeseries: None
setup_precip_forcing.magnitude: 12.594166666666666     ← setup_river_inflow COMPLETELY ABSENT
WARNING ... build_sfincs_model raised HYDROMT_BUILD_FAILED
  (underlying: 'Invalid frequency: 10T. Failed to parse ... KeyError("T"). Did you mean min?')
outcome=HONEST FAILURE solver_version=failed:HYDROMT_BUILD_FAILED layers=0 elapsed=46.32s
```

Chain progression:

| Job | Failed at | Error class |
|-----|-----------|-------------|
| job-0053 | `setup_river_inflow` | `NoDataException: merit_hydro` (Italy coverage) |
| job-0054 | `set_forcing_1d` after `setup_river_inflow` | `'RangeIndex' has no 'is_integer'` (pandas 3.x) |
| **job-0055** | `setup_precip_forcing` internal time-grid | `Invalid frequency: 10T` (pandas 3.x freq alias) |

### Results: PASS (pre-existing tests clean) + PARTIAL SUCCESS / NEW FAILURE CLASS (M5 re-run, honest new blocker documented)

The v0.1 scope guard change is complete. The chain advances two steps further than job-0054. The new blocker is a third upstream pandas-3 incompatibility in hydromt-sfincs 1.2.2, routed to infra as OQ-55. No screenshot moment — the `if SUCCESS → screenshot` branch did not fire.
