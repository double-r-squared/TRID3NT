# Report: setup_manning_roughness `map_fn` kwarg mismatch hotfix (closes OQ-52)

**Job ID:** job-0053-engine-20260607
**Sprint:** sprint-08 (mid-sprint hotfix #2)
**Specialist:** engine
**Task:** Verify hydromt-sfincs 1.2.x `setup_manning_roughness` live signature; fix `_generate_hydromt_yaml_config()` in `services/agent/src/grace2_agent/workflows/sfincs_builder.py` to emit the 1.2.x-accepted kwargs (no top-level `map_fn`); re-run the M5 chain and honestly disclose outcome; add ≥1 regression test.
**Status:** ready-for-audit

## Summary

Inspected hydromt-sfincs 1.2.2's live `setup_manning_roughness` signature: `(self, datasets_rgh: List[dict] = [], manning_land=0.04, manning_sea=0.02, rgh_lev_land=0)` — **no top-level `map_fn` keyword exists**. Per `_parse_datasets_rgh`, the LULC → Manning's reclass table is threaded INSIDE each `datasets_rgh` entry under the key `reclass_table`, and the table must be a CSV whose first column is the LULC class (read as `index_col=0`) plus a column literally named `N`. Fixed `_generate_hydromt_yaml_config` to drop `map_fn` and move `reclass_table` inside the `datasets_rgh[0]` dict. Added `_write_hydromt_reclass_table_csv` helper which writes a v1.2.x-shaped CSV from the in-memory `load_manning_mapping` dict into the per-build temp dir (the authored substrate `manning_mapping.csv` is unchanged — its `nlcd_class,manning_n,description` shape stays load-bearing for `load_manning_mapping` + the OQ-4 §4 validation gate). Added 1 regression test. M5 chain re-run advances past `setup_manning_roughness` cleanly — and lands on a **NEW HONEST BLOCKER inside `setup_river_inflow`**: `No data was read from source: merit_hydro (No data available)`. This is the **third distinct hydromt-sfincs 1.2.x mismatch in the same workflow** (OQ-49 yaml-shape, OQ-52 map_fn-removed, OQ-53 merit_hydro-catalog-binding). **Surfaced as ESCALATION CANDIDATE** — recommend orchestrator open a comprehensive hydromt-sfincs 1.2.x API-migration audit rather than chaining a fourth one-step hotfix.

## Changes Made

- **`services/agent/src/grace2_agent/workflows/sfincs_builder.py`** (EDIT — narrow, manning-roughness lines only).
  - Added `_write_hydromt_reclass_table_csv(mapping, out_path) -> Path` — writes a v1.2.x-shaped reclass CSV (`nlcd_class,N` header) from the in-memory mapping. Lives next to `_default_setup_uri`. Cites the live `_parse_datasets_rgh` semantics in the docstring (the substrate-version-pinned in-memory mapping is the source of truth; this helper materializes a HydroMT-readable view).
  - Edited `_generate_hydromt_yaml_config` `setup_manning_roughness` block: removed `map_fn: '...'` and re-shaped `datasets_rgh` to `[{ lulc: '<lulc_uri>', reclass_table: '<reclass_csv>' }]`. Inline comment block cites the live signature inspection.
  - Edited `build_sfincs_model` (inside the `tempfile.TemporaryDirectory` block, before the YAML write): now calls `_write_hydromt_reclass_table_csv(mapping, tmp / "manning_reclass.csv")` and threads that temp path as `mapping_csv_path` into `_generate_hydromt_yaml_config`. The authored `manning_mapping.csv` is untouched.
- **`services/agent/tests/test_model_flood_scenario.py`** (EDIT — additive only).
  - Added `test_build_sfincs_model_emits_v1_2_x_manning_roughness_kwargs`: fakes `hydromt_sfincs`, captures the parsed `opt` dict, and asserts: (1) **`map_fn` does NOT appear** as a top-level key under `setup_manning_roughness` (OQ-52 regression guard); (2) only the four 1.2.x-accepted top-level keys (`datasets_rgh`, `manning_land`, `manning_sea`, `rgh_lev_land`) are emitted; (3) each `datasets_rgh` entry carries both `lulc` and `reclass_table`; (4) the helper-written reclass CSV has the v1.2.x column shape — first column is the LULC class index, and there is a column literally named `N` (without which HydroMT's `df_map[["N"]]` indexing fails).
- **`reports/inflight/job-0053-engine-20260607/evidence/`** (NEW):
  - `smoke_demo.py` — copy of job-0052's harness, re-run unchanged from this inflight dir.
  - `smoke_demo_log.txt` — full M5 chain stdout/stderr capture from the live re-run.
  - `smoke_demo_envelope.json` — captured `AssessmentEnvelope` shape (HONEST FAILURE, HYDROMT_BUILD_FAILED, 42.71s).

## Decisions Made

- **Decision: write the v1.2.x-shaped reclass CSV to the per-build temp dir, do NOT mutate the authored `manning_mapping.csv`.** Rationale: `manning_mapping.csv` is the FROZEN substrate the OQ-4 §4 validation gate keys off (column shape `nlcd_class,manning_n,description` is load-bearing for `load_manning_mapping`). Changing the column header would silently break the gate and the existing `test_load_manning_mapping_returns_expected_classes` test. Materializing a HydroMT-readable view per build is cheap, deterministic, and keeps the substrate file pristine.
- **Decision: keep `reclass_table` INSIDE each `datasets_rgh` entry rather than promoting it to a top-level YAML key.** Rationale: the live `_parse_datasets_rgh` source code only reads `dataset.get("reclass_table", None)` from within each datasets_rgh dict — a top-level YAML key would be silently ignored and the build would fail with `IOError("Manning roughness 'reclass_table' csv file must be provided")`. The inline-dict shape mirrors hydromt-sfincs's own bundled example configs (e.g. `NLCD_SFBD_mapping.csv` referenced via `datasets_rgh: [{ lulc: ..., reclass_table: ... }]`).
- **Decision: do NOT extend this hotfix to also fix the new `setup_river_inflow.merit_hydro` blocker.** Rationale: the kickoff scoped this job to the `map_fn` fix + honest disclosure of the next outcome. The merit_hydro failure is a separate hydromt-sfincs 1.2.x data-catalog binding mismatch (we pass `hydrography: 'merit_hydro'` but no MERIT Hydro catalog entry is registered with our `SfincsModel`). Surfaced as **OQ-53-RIVER-INFLOW-MERIT-HYDRO-MISSING** + escalation recommendation.

## Invariants Touched

- **Invariant 1 (Determinism boundary): preserves.** No LLM in the path; helper writes a deterministic CSV from a deterministic in-memory mapping.
- **Invariant 2 (Deterministic workflows): preserves.** Pure-Python CSV write; same input mapping → same output bytes.
- **Invariant 7 (no silent wrong answers): preserves & strengthens.** The OQ-4 §4 NLCD validation gate continues to run BEFORE HydroMT against the authored `manning_mapping.csv` substrate; the v1.2.x-shaped CSV is a translated view of the SAME validated mapping, so HydroMT's silently-fills-defaults behavior remains gated. The new `setup_river_inflow` failure surfaces as a typed `SFINCSSetupError("HYDROMT_BUILD_FAILED")` carrying the underlying `merit_hydro` message — typed failure, not a crash.

## Open Questions

- **OQ-53-RIVER-INFLOW-MERIT-HYDRO-MISSING (TENTATIVE: escalate to comprehensive API-migration audit).** After the OQ-52 fix, the M5 chain advances past `setup_manning_roughness` and lands inside `setup_river_inflow` with `No data was read from source: merit_hydro (No data available)`. The YAML config emits `hydrography: 'merit_hydro'` and hydromt-sfincs 1.2.x tries to resolve `merit_hydro` against its `DataCatalog`, finds no entry, and raises. Resolution options:
  - **(a)** Stand up a HydroMT `DataCatalog` for the agent runtime that registers `merit_hydro` (or our NHDPlus HR equivalent) as a named entry. This is a substantive integration task — DataCatalog wiring touches `build_sfincs_model`'s constructor, the SFINCS solver container's image (job-0040 owns the build), and the OQ-4 decision (which currently leaves catalog bridging vague). Substantially out of "one focused hotfix" scope.
  - **(b)** Drop the `setup_river_inflow` step from `_generate_hydromt_yaml_config` for pluvial-only v0.1 (Hurricane Ian rainfall-driven flooding; river inflow is not load-bearing for the M5 deliverable). The river-geometry fetcher would still run, the layer would still be cached for future use, but the SFINCS deck would not include river-inflow forcing.
  - **(c)** Pass the NHDPlus FlatGeobuf directly into `setup_river_inflow` without the `hydrography` parameter — the 1.2.x signature may accept a different combination. (Requires `inspect.signature(SfincsModel.setup_river_inflow)` follow-up — same diagnostic motion as this job, applied to the next step.)
  - **Routing recommendation:** **ESCALATION CANDIDATE.** This is the third consecutive 1.2.x mismatch in the same workflow file. Pattern: OQ-49 (yaml string vs dict), OQ-52 (map_fn removed), OQ-53 (catalog-bound source). A fourth one-step hotfix probes the same problem one more step downstream and the M5 will still not succeed. Recommend orchestrator open `comprehensive hydromt-sfincs 1.2.x API migration audit` — run `inspect.signature` against every `setup_*` method our YAML config emits, cross-walk against the v1.2.2 sources to discover all kwarg/catalog mismatches AT ONCE, and land a single migration commit instead of N hotfixes. See escalation note below.

## Dependencies and Impacts

- **Depends on:**
  - **job-0049 (infra, APPROVED)** + **job-0052 (engine, APPROVED).** This is the chained fix that unblocks `setup_manning_roughness` after job-0052 unblocked `model.build(opt=...)`.
  - **job-0042 (engine, APPROVED).** `build_sfincs_model` exists.
  - **job-0043 (testing, APPROVED).** M5 smoke harness re-used.

- **Affects (downstream):**
  - **orchestrator (sprint planning, ESCALATION RECOMMENDED).** Recommend opening a **comprehensive hydromt-sfincs 1.2.x API-migration audit job** rather than a fourth single-step hotfix. The audit should: (1) run `inspect.signature` against every `SfincsModel.setup_*` method our YAML config currently invokes (`setup_config`, `setup_grid_from_region`, `setup_dep`, `setup_mask_active`, `setup_manning_roughness` ✓, `setup_river_inflow`, `setup_precip_forcing`, `setup_subgrid` if present), cross-walk each to the v1.2.2 source; (2) discover all kwarg mismatches and any DataCatalog wiring needed; (3) land a single migration commit. Scope: probably 1–2 days of focused work but resolves the API-mismatch class in one pass.
  - **engine (or whoever owns the migration).** Once the comprehensive audit lands, the M5 chain should reach SFINCS solver dispatch (the first SUCCESS) or surface a different class of blocker (solver-runtime, not setup-API).
  - **orchestrator (sprint planning).** No screenshot moment in this job — the M5 chain still produces no flood-depth COG; the `if SUCCESS → screenshot` branch from the kickoff is NOT triggered.

## Verification

### Unit + integration test suite

```
$ PYTHONPATH=services/agent/src:packages/contracts/src \
    .venv-agent/bin/python -m pytest services/agent/tests/ -q
.............................................................................. [ 58%]
.......................................................                       [100%]
133 passed, 4 warnings in 1.61s
```

`test_model_flood_scenario.py` alone: **14 passed** (13 pre-existing + 1 new OQ-52 regression).

The new test verifies:
- The setup_manning_roughness step has NO top-level `map_fn` key.
- Top-level keys are a subset of `{datasets_rgh, manning_land, manning_sea, rgh_lev_land}` (the live 1.2.2 signature).
- Each `datasets_rgh` entry carries both `lulc` and `reclass_table`.
- The helper-written reclass CSV has the v1.2.x column shape (`index_col=0` + column `N`).

### Live hydromt-sfincs 1.2.2 signature inspection

```
$ .venv-agent/bin/python -c "
import hydromt_sfincs; import inspect
sig = inspect.signature(hydromt_sfincs.SfincsModel.setup_manning_roughness)
print('VERSION:', hydromt_sfincs.__version__); print('SIG:', sig)"
VERSION: 1.2.2
SIG: (self, datasets_rgh: 'List[dict]' = [], manning_land=0.04, manning_sea=0.02, rgh_lev_land=0)
```

And the `_parse_datasets_rgh` source confirms:
```
* "lulc" is parsed into da (xr.DataArray) using reclass table in "reclass_table"
df_map = self.data_catalog.get_dataframe(reclass_table, index_col=0)
da_man = da_lulc.raster.reclassify(df_map[["N"]])["N"]
```

— exactly the kwarg shape this job emits.

### Live M5 chain re-run (the honest disclosure)

Command:

```
PATH=$HOME/tools/google-cloud-sdk/bin:$PATH \
  GOOGLE_CLOUD_PROJECT=grace-2-hazard-prod \
  GOOGLE_APPLICATION_CREDENTIALS=$HOME/.config/gcloud/application_default_credentials.json \
  CPL_GS_USE_GOOGLE_AUTH=YES \
  PYTHONPATH=services/agent/src:packages/contracts/src \
  .venv-agent/bin/python reports/inflight/job-0053-engine-20260607/evidence/smoke_demo.py
```

Key new log lines (`evidence/smoke_demo_log.txt`):

```
hydromt_sfincs.sfincs setup_grid_from_region.region: {'bbox': [-81.9126085, 26.5476424, -81.7511414, 26.689176]}
hydromt_sfincs.sfincs setup_dep.datasets_dep: [{'elevtn': 'gs://grace-2-hazard-prod-cache/.../dem/87ba00463af0275d02115f7463afe6e9.tif'}]
hydromt_sfincs.sfincs setup_mask_active.zmin: -10.0 ... zmax: 10.0
hydromt_sfincs.sfincs setup_manning_roughness.datasets_rgh: [{'lulc': 'gs://grace-2-hazard-prod-cache/.../landcover/...tif',
    'reclass_table': '/tmp/claude-1000/sfincs-build-uwygr5in/manning_reclass.csv'}]
hydromt_sfincs.sfincs setup_manning_roughness.manning_land: 0.04
hydromt_sfincs.sfincs setup_manning_roughness.manning_sea: 0.02
hydromt_sfincs.sfincs setup_manning_roughness.rgh_lev_land: 0
                                          ↑↑↑ (setup_manning_roughness COMPLETED — no error)
hydromt_sfincs.sfincs setup_river_inflow.rivers: gs://grace-2-hazard-prod-cache/.../river_geometry/4fa2fd4e5d2192020dc04d60502d6aaa.fgb
hydromt_sfincs.sfincs setup_river_inflow.hydrography: merit_hydro
grace2_agent.workflows.model_flood_scenario build_sfincs_model raised HYDROMT_BUILD_FAILED
  (underlying: 'No data was read from source: merit_hydro (No data available)')
smoke_demo outcome=HONEST FAILURE solver_version=failed:HYDROMT_BUILD_FAILED layers=0 elapsed=42.71s
```

**Comparison vs job-0052 baseline:**
- job-0052 smoke: chain crashed inside `setup_manning_roughness` at the `map_fn` TypeError, after running `setup_grid_from_region` + `setup_dep` + `setup_mask_active`.
- This job's smoke: chain runs **`setup_grid_from_region` ✓ → `setup_dep` ✓ → `setup_mask_active` ✓ → `setup_manning_roughness` ✓ (NEW — OQ-52 closed) → `setup_river_inflow` ✗** with the merit_hydro data-catalog mismatch.
- Wall-clock 42.71s (vs job-0052's 41.11s) — the chain now executes one additional setup step + a partial river-inflow lookup before failing.

### Honest outcome disclosure

**NEXT HONEST BLOCKER (NOT first-successful-M5).** OQ-52 is closed by this job: `setup_manning_roughness` now emits the 1.2.x-accepted kwarg shape and HydroMT runs the step cleanly. The next honest blocker is **OQ-53-RIVER-INFLOW-MERIT-HYDRO-MISSING** — a different class of mismatch (DataCatalog binding, not signature kwarg), inside the next setup step in the chain.

**ESCALATION CANDIDATE:** This is the third consecutive hydromt-sfincs 1.2.x mismatch in the same workflow file (`sfincs_builder.py`). Pattern: OQ-49 (yaml string-vs-dict), OQ-52 (map_fn removed), OQ-53 (catalog-bound `merit_hydro` source not registered). Each hotfix has unblocked exactly one setup step before the next mismatch fires. Recommend the orchestrator open a **comprehensive hydromt-sfincs 1.2.x API-migration audit** job: run `inspect.signature` against every `setup_*` method our YAML config emits, cross-walk to the v1.2.2 source, and discover/fix ALL mismatches plus any required DataCatalog wiring in a single pass — instead of a fourth single-step hotfix that probes the same class of problem one more step downstream.

### Captured envelope

`evidence/smoke_demo_envelope.json` — full `AssessmentEnvelope` shape:

- `envelope_type = "modeled"`, `hazard_type = "flood"`, `workflow_name = "model_flood_scenario"`
- `bbox = [-81.9126085, 26.5476424, -81.7511414, 26.689176]` (Fort Myers, FL)
- `forcing_type = "pluvial_synthetic"`, `forcing_source = "NOAA Atlas 14 Volume 9 Version 2 — 100-yr / 24-hr"`
- `forcing_parameters.precip_inches = 11.9`
- `data_source_count = 5` — Nominatim, USGS 3DEP, NLCD 2021, NHDPlus HR, NOAA Atlas 14
- `flood_solver_version = "failed:HYDROMT_BUILD_FAILED"`, `layer_count = 0`
- `outcome = "HONEST FAILURE"`, `error_code = "HYDROMT_BUILD_FAILED"`
- `elapsed_seconds = 42.705`

### Acceptance criteria

- [x] `_generate_hydromt_yaml_config()` produces a kwarg shape `setup_manning_roughness` accepts (live signature verified: `datasets_rgh, manning_land, manning_sea, rgh_lev_land`; `reclass_table` threaded inside each datasets_rgh dict per `_parse_datasets_rgh`). **PASS**.
- [x] Live signature inspection cited in the report. **PASS** (Verification §"Live hydromt-sfincs 1.2.2 signature inspection").
- [x] M5 chain re-run with honest outcome disclosure (NEXT HONEST BLOCKER = OQ-53 merit_hydro). **PASS**.
- [x] ≥1 new test exercising the corrected manning_roughness kwarg path. **PASS** (1 added, 14 total in `test_model_flood_scenario.py`, 133 total).
- [x] No edits to FROZEN paths (only `services/agent/src/grace2_agent/workflows/sfincs_builder.py` + `services/agent/tests/test_model_flood_scenario.py` + `reports/inflight/job-0053-engine-20260607/`). **PASS**.
- [x] Closes OQ-52-MANNING-ROUGHNESS-MAP-FN-MISMATCH. **PASS**.
- [x] Yet-another-mismatch surfaced as **OQ-53-RIVER-INFLOW-MERIT-HYDRO-MISSING** with explicit escalation recommendation to comprehensive 1.2.x API-migration audit. **PASS**.
- [ ] Real flood-depth COG produced for orchestrator screenshot. **NOT TRIGGERED** — the kickoff's `if SUCCESS → screenshot` branch did not fire; the existing M5 visual continues to be job-0043's `final_honest_failure.png`.

### Results: PASS (with honest disclosure of NEXT HONEST BLOCKER + ESCALATION CANDIDATE)

The hotfix deliverable — `setup_manning_roughness` emits the 1.2.x-accepted kwarg shape so the M5 chain advances past the previous `map_fn` TypeError — is met. The chain does not YET produce a real flood-depth COG; the new blocker is OQ-53-RIVER-INFLOW-MERIT-HYDRO-MISSING (DataCatalog binding for `merit_hydro`), surfaced with an explicit recommendation to escalate to a comprehensive hydromt-sfincs 1.2.x API-migration audit rather than chaining a fourth single-step hotfix.
