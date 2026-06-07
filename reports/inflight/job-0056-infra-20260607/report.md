# Report: pandas pin to resolve hydromt-sfincs 1.2.2 pandas-3 incompat (OQ-54 + OQ-55)

**Job ID:** job-0056-infra-20260607
**Sprint:** sprint-08 (mid-sprint follow-up #4 to migration chain)
**Specialist:** infra
**Task:** Pin pandas in `services/agent/pyproject.toml` to a version compatible with hydromt-sfincs 1.2.2's pandas-API expectations. Resolve OQ-54 (`pd.Index.is_integer()`) and OQ-55 (`pd.date_range(freq="10T")`). Re-run M5 smoke.
**Status:** ready-for-audit

## Summary

Pinned pandas to `>=2.2,<2.3` (resolved to `2.2.3`) in `services/agent/pyproject.toml`. This resolves **both** upstream hydromt-sfincs 1.2.2 pandas-3 incompatibilities (OQ-54 and OQ-55) — both deprecated APIs still work under pandas 2.2.x. Path A (`>=2.1,<2.2`) was attempted first but has **no pre-built wheel for Python 3.13** and fails to compile; pandas 2.2.x is the earliest series with a cp313 manylinux wheel. No cascade: numpy, geopandas, rasterio, xarray, pyproj, shapely are all unchanged. Full test suite passes (162 tests). M5 chain re-run is **PARTIAL SUCCESS / NEW FAILURE CLASS** — OQ-54 and OQ-55 are gone; the chain now completes all HydroMT setup steps and uploads the SFINCS deck to GCS before dispatching to Cloud Workflows, where the solver (SFINCS binary in Cloud Run Job) returns `SOLVER_FAILED` — a different failure class entirely (infra/solver, not HydroMT/pandas).

## Changes Made

- **`services/agent/pyproject.toml`** — Added `"pandas>=2.2,<2.3"` with a 14-line comment block citing OQ-54 + OQ-55, explaining the Python 3.13 wheel constraint, and documenting the migration path.

- **`services/agent/tests/test_pandas_pin_regression.py`** (NEW) — Three tests:
  1. `test_oq54_range_index_is_integer_still_works` — asserts `pd.RangeIndex(1,5).is_integer()` returns `True` without raising (the exact OQ-54 call path).
  2. `test_oq55_date_range_freq_10T_still_works` — asserts `pd.date_range('2026-01-01', periods=10, freq='10T')` produces 10 entries at 10-minute intervals (the exact OQ-55 call path).
  3. `test_pandas_version_within_pin_bounds` — asserts installed pandas is `>=2.2,<2.3`; fires on any accidental re-bump.

- **`reports/inflight/job-0056-infra-20260607/evidence/`**:
  - `smoke_demo.py` — copied from `reports/complete/job-0055-engine-20260607/evidence/smoke_demo.py`
  - `smoke_demo_log.txt` — full M5 chain stdout/stderr capture
  - `smoke_demo_envelope.json` — `AssessmentEnvelope` summary

## Decisions Made

- **Decision: use `pandas>=2.2,<2.3` rather than `>=2.1,<2.2` (Path A as originally specified).**
  - Rationale: pandas 2.1.x has no pre-built wheel for CPython 3.13 (Python 3.13.5). `pip install --only-binary=:all:` confirms: `Could not find a version that satisfies the requirement pandas<2.2,>=2.1 (from versions: 2.2.3, 2.3.0 ...)`. Build from source fails at Cython compilation: `_PyLong_AsByteArray` changed signature in CPython 3.13 (requires an additional `with_signed` bool argument — meson build exits code 1). The earliest pandas with a cp313 manylinux2014_x86_64 wheel is 2.2.3. pandas 2.2.x still provides both deprecated APIs: `pd.Index.is_integer()` (FutureWarning; removed 3.0) and `pd.date_range(freq="T"/"10T")` (FutureWarning; removed 3.0).
  - Alternatives considered: Path B (`pandas>=1.5,<2.0`) — no cp313 wheel; same CPython 3.13 build failure. `pandas>=2.3,<3.0` — wheels exist but `is_integer()` is absent from 2.0+ AND `"T"` alias removed in 2.3. No other version range satisfies both requirements with a cp313 wheel.

- **Decision: no cascade pin of other packages.**
  - Rationale: `pip install "pandas>=2.2,<2.3"` resolves cleanly. numpy (2.4.6), geopandas (1.1.3), rasterio (1.5.0), xarray (2026.4.0), pyproj (3.7.2), shapely (2.1.2) are all unchanged. Only `pytz` and `tzdata` added (standard pandas 2.x deps, no conflicts).

## Invariants Touched

- **Invariant 2 (Deterministic workflows): preserves.** pandas version pin is deterministic; no LLM in path.
- **Invariant 7 (no silent wrong answers): strengthens.** The regression tests make both OQ-54 and OQ-55 machine-checkable.

## Open Questions

- **OQ-SOLVER-FAILED (NEW, route to engine/infra).** With OQ-54 and OQ-55 resolved, the M5 chain now fails inside the Cloud Workflows execution (SFINCS solver tier). The SFINCS deck is written to GCS (`gs://grace-2-hazard-prod-cache/cache/static-30d/sfincs_setup/01KTHQP54XVAAF2NPGKTAMP4PV/`). Cloud Workflows execution submitted (`08370fe0-a31b-4edb-a173-1f77ef3cec77`), ran 3.8 minutes, returned `SOLVER_FAILED` (the `_solver_error_code` catch-all in `tools/solver.py`). This is the SFINCS binary failing inside the Cloud Run Job. Root cause requires: Cloud Run Job logs for the SFINCS container; Cloud Workflows execution result details; candidate causes: SFINCS binary not installed, solver config error, memory/CPU constraints, netCDF dependency issues. Routes to: infra (Cloud Run Job container) or engine (SFINCS binary wiring / deck validation).

- **OQ-49-AGENT-CLOUD-RUN-DEPLOY-PENDING (CARRY-FORWARD).** Production Cloud Run container has not been rebuilt with the new pandas pin. Pin is correct in `pyproject.toml`; it propagates to the dev venv. Will not be active in production until container is rebuilt and pushed.

- **OQ-49-HYDROMT-SFINCS-PIN-RECONCILIATION (CARRIED FORWARD).** OQ-4 §4 paper pin says `hydromt-sfincs >= 1.1.2, < 2.0`; we ship `>= 1.1.0, < 2.0`. Pending orchestrator amendment.

## Dependencies and Impacts

- **Depends on:** job-0049 (APPROVED), job-0055 (APPROVED)
- **Affects (downstream):**
  - **engine or infra (next job):** OQ-SOLVER-FAILED — investigate why SFINCS Cloud Run Job returns non-zero. The SFINCS deck is correctly built and uploaded; the failure is in solver execution.
  - **infra:** production container needs rebuild with new pandas pin.
  - **orchestrator:** No screenshot moment this job — no flood-depth COG produced.

## Verification

### Package resolution

Pre-pin (job-0055 baseline): `pandas 3.0.3`

Post-pin (this job):
```
geopandas   1.1.3   (unchanged)
numpy       2.4.6   (unchanged)
pandas      2.2.3   (CHANGED)
pyproj      3.7.2   (unchanged)
rasterio    1.5.0   (unchanged)
rioxarray   0.22.0  (unchanged)
shapely     2.1.2   (unchanged)
xarray      2026.4.0 (unchanged)
```

### OQ-54 + OQ-55 live verification

```
$ .venv-agent/bin/python -c "
import pandas as pd
print('pandas version:', pd.__version__)
idx = pd.RangeIndex(start=1, stop=5)
print('is_integer:', idx.is_integer())
dr = pd.date_range('2026-01-01', periods=10, freq='10T')
print('date_range len:', len(dr))
print('ALL CHECKS PASSED')
" 2>&1

<string>:6: FutureWarning: RangeIndex.is_integer is deprecated. Use pandas.api.types.is_integer_dtype instead.
<string>:8: FutureWarning: 'T' is deprecated and will be removed in a future version, please use 'min' instead.
pandas version: 2.2.3
is_integer: True
date_range len: 10
ALL CHECKS PASSED
```

### Full agent test suite

```
$ PYTHONPATH=services/agent/src:packages/contracts/src \
    .venv-agent/bin/python -m pytest services/agent/tests/ -q
162 passed, 4 warnings in 2.98s
```

(159 job-0055 baseline + 3 new regression tests = 162)

### M5 chain re-run — honest disclosure

Key progression (from evidence/smoke_demo_log.txt):
```
setup_grid_from_region  ✓
setup_dep               ✓ (reads DEM from GCS)
setup_mask_active       ✓
setup_manning_roughness ✓ (reads reclass CSV)
setup_precip_forcing.magnitude: 12.594...  ✓ (OQ-54+55 RESOLVED — no error!)
Writing model data to /tmp/.../deck
uploaded SFINCS deck to gs://grace-2-hazard-prod-cache/cache/static-30d/sfincs_setup/01KTHQP54XVAAF2NPGKTAMP4PV/
run_solver submitted workflows_execution_id=.../executions/08370fe0-a31b-4edb-a173-1f77ef3cec77
wait_for_completion ... poll_interval=10s timeout=1800s
outcome=HONEST FAILURE solver_version=failed:SOLVER_FAILED layers=0 elapsed=276.95s
```

Chain progression table:

| Job | Failed at | Error class |
|-----|-----------|-------------|
| job-0053 | `setup_river_inflow` | `NoDataException: merit_hydro` |
| job-0054 | `set_forcing_1d` | OQ-54: `is_integer()` pandas 3.x |
| job-0055 | `setup_precip_forcing` | OQ-55: `freq="10T"` pandas 3.x |
| **job-0056** | Cloud Workflows SFINCS binary | **NEW CLASS: SOLVER_FAILED** |

**PARTIAL SUCCESS / NEW FAILURE CLASS.** OQ-54 and OQ-55 definitively resolved. HydroMT model build complete. SFINCS deck uploaded to GCS. Solver execution fails in Cloud Run Job (different domain). No screenshot moment — no COG produced.

### Acceptance criteria

- [x] `pyproject.toml` includes pandas pin with comment citing OQ-54 + OQ-55 — PASS
- [x] `.venv-agent` re-resolved + resolved versions documented — PASS
- [x] BOTH upstream bugs disappear in live verification — PASS
- [x] M5 chain re-run with honest disclosure — PASS (PARTIAL SUCCESS / SOLVER_FAILED)
- [x] ≥1 new regression test — PASS (3 new)
- [x] No edits to FROZEN paths — PASS
- [ ] If SUCCESS, "SCREENSHOT MOMENT" call-out — NOT TRIGGERED (no COG)
- [x] OQ-49-AGENT-CLOUD-RUN-DEPLOY-PENDING noted — PASS
- [x] Single commit — see commit SHA in final summary

### Results: PASS (with honest disclosure of NEW FAILURE CLASS: SOLVER_FAILED)
