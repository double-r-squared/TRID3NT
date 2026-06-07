# Report: HYDROMT_BUILD_FAILED — opt argument shape one-line hotfix (closes OQ-49-HYDROMT-BUILD-OPT-ARGUMENT-SHAPE)

**Job ID:** job-0052-engine-20260607
**Sprint:** sprint-08 (mid-sprint hotfix)
**Specialist:** engine
**Task:** One-line `yaml.safe_load` fix in `services/agent/src/grace2_agent/workflows/sfincs_builder.py` so `SfincsModel.build(opt=...)` receives a parsed dict (per hydromt-sfincs 1.2.x API) instead of a raw YAML text blob; re-run job-0042/0043 M5 chain; capture honest outcome (SUCCESS vs NEXT HONEST BLOCKER); add at least 2 tests.
**Status:** ready-for-audit

## Summary

Applied the one-line OQ-49 hotfix: `yaml.safe_load(yaml_text)` produces the parsed `Dict[str, Dict]` shape that hydromt-sfincs 1.2.x's `SfincsModel.build` documents for the `opt` argument; the previous raw-string pass crashed at `'str' object has no attribute 'keys'` inside HydroMT's `_parse_steps`. Added `import yaml` at the top of `sfincs_builder.py`. Added 2 regression tests (corrected dict-passing path + malformed-YAML failure path). M5 chain re-run advances past `HYDROMT_BUILD_FAILED` at the `model.build` entry — the chain now traverses `setup_grid_from_region`, reads the DEM from GCS, runs `setup_dep` (with HydroMT's IDW interpolation over 96 nodata cells), runs `setup_mask_active`, and lands on the **NEXT HONEST BLOCKER** inside `setup_manning_roughness`. Outcome: **NEXT HONEST BLOCKER**, NOT yet first-successful-M5. The new blocker is a different YAML-shape mismatch: the keyword `map_fn` is passed but hydromt-sfincs 1.2.2's `setup_manning_roughness` does not accept it. Routes to next engine hotfix follow-up.

## Changes Made

- **`services/agent/src/grace2_agent/workflows/sfincs_builder.py`** (EDIT — one-line fix + import).
  - Added `import yaml` at the top-level imports block (line 64).
  - At the `model.build(opt=...)` site (previously line 692, now line 698 after the explanatory comment block), call `opt_dict = yaml.safe_load(yaml_text)` before `model.build(opt=opt_dict)`. Inline comment block explains why (OQ-49 / job-0049 diagnosis trace + FR-FR-2 substrate-integrity routing for the malformed-YAML case).
- **`services/agent/tests/test_model_flood_scenario.py`** (EDIT — additive).
  - Added `test_build_sfincs_model_passes_parsed_dict_to_hydromt_build`: fakes the `hydromt_sfincs` module, patches `_extract_unique_nlcd_classes` to {11, 41}, supplies a 2-class Manning mapping CSV, calls `build_sfincs_model`, and asserts the captured `opt` passed to `SfincsModel.build` is a `dict` (not a `str`) and that every step value inside it is itself a mapping (the exact `.keys()` contract hydromt-sfincs requires). This is the regression guard for OQ-49.
  - Added `test_build_sfincs_model_malformed_yaml_surfaces_typed_error`: monkey-patches `_generate_hydromt_yaml_config` to return malformed YAML, asserts `build_sfincs_model` raises a typed `SFINCSSetupError("HYDROMT_BUILD_FAILED")` carrying the bbox + URIs in `details` (FR-FR-2 substrate-integrity routing), and that `SfincsModel` is never constructed (the parse failure is caught before HydroMT runs).
- **`reports/inflight/job-0052-engine-20260607/evidence/`** (NEW):
  - `smoke_demo.py` — copy of job-0049's harness, re-run from this inflight directory.
  - `smoke_demo_log.txt` — full M5 chain stdout/stderr.
  - `smoke_demo_envelope.json` — captured `AssessmentEnvelope`; `outcome=HONEST FAILURE`, `error_code=HYDROMT_BUILD_FAILED`, `elapsed=41.11s` (vs job-0049 11.47s — the chain runs ~30s longer because HydroMT now actually executes setup_grid + setup_dep + DEM read + setup_mask before the next mismatch fires).

## Decisions Made

- **Decision: place `import yaml` in the top-level imports block, not lazy inside the build call.** Rationale: PyYAML is already a transitive dep of hydromt-sfincs (HydroMT itself uses pyyaml); adding the top-level import has zero runtime cost on import and keeps the load-path obvious. Lazy imports are reserved for genuinely heavy or optional deps (hydromt_sfincs itself stays lazy below).
- **Decision: do NOT extend the fix to also fix the new `map_fn` blocker.** Rationale: the kickoff scoped this hotfix to "one-line yaml.safe_load fix" + honest disclosure of the next outcome. The `map_fn` keyword mismatch inside `setup_manning_roughness` is a separate bug in `_generate_hydromt_yaml_config` (or the manning-mapping CSV side-car protocol it composes) that is out of OQ-49's scope. Surfaced as **OQ-52-MANNING-ROUGHNESS-MAP-FN-MISMATCH** for routing to the next engine follow-up.
- **Decision: keep the `SFINCSSetupError("HYDROMT_BUILD_FAILED")` wrapper covering the `yaml.safe_load` call site, not a separate `MALFORMED_BUILD_CONFIG` code.** Rationale: a parse failure at this seam is, structurally, a HydroMT-build-pipeline failure from the caller's perspective; introducing a new error code splits the error surface for no user-visible benefit. The underlying message (`yaml.YAMLError: ...`) is threaded through `details["underlying"]` so the failed envelope still carries the diagnostic.

## Invariants Touched

- **Invariant 1 (Determinism boundary): preserves.** No LLM in the path. `yaml.safe_load` is deterministic.
- **Invariant 2 (Deterministic workflows): preserves.** Pure-Python parse; same input → same output dict.
- **Invariant 7 (no silent wrong answers): preserves.** The malformed-YAML case raises a typed `SFINCSSetupError`, not a silent fall-through; the regression test asserts the underlying details thread into the envelope. The new `map_fn` blocker is also surfaced as a typed `HYDROMT_BUILD_FAILED` (not a silent crash).

## Open Questions

- **OQ-52-MANNING-ROUGHNESS-MAP-FN-MISMATCH (TENTATIVE: engine fix in `_generate_hydromt_yaml_config`).** After the OQ-49 fix, the M5 chain advances and lands on: `SfincsModel.setup_manning_roughness() got an unexpected keyword argument 'map_fn'`. The YAML config generator emits `map_fn: ...` inside the `setup_manning_roughness` step, but hydromt-sfincs 1.2.2's API takes `datasets_rgh`, `manning_land`, `manning_sea`, `rgh_lev_land` only — `map_fn` is either a deprecated keyword from earlier 1.0.x or a confusion with `setup_subgrid` parameters. The fix is in `services/agent/src/grace2_agent/workflows/sfincs_builder.py` `_generate_hydromt_yaml_config()` — remove the `map_fn` key (or rename it to the correct 1.2.x keyword) and ensure the Manning mapping CSV is threaded via the `datasets_rgh[*]` entries the way hydromt-sfincs 1.2.x documents. Routes to: engine (next hotfix); will either unblock real SFINCS deck build → solver dispatch → first successful M5, or surface the next honest blocker. Out of scope for this job (kickoff specifies one-line yaml.safe_load fix only).

## Dependencies and Impacts

- **Depends on:**
  - **job-0049 (infra, APPROVED).** Diagnosis trace at `reports/complete/job-0049-infra-20260607/report.md` named the exact failure mode (`'str' object has no attribute 'keys'` at `sfincs_builder.py:692`) and the fix (`yaml.safe_load`). This job is the engine-side hotfix for that diagnosis.
  - **job-0042 (engine, APPROVED).** `build_sfincs_model` exists; this is a one-line surgical edit to the existing implementation.
  - **job-0043 (testing, APPROVED).** M5 smoke harness re-used.

- **Affects (downstream):**
  - **engine (next sprint-08 hotfix for OQ-52-MANNING-ROUGHNESS-MAP-FN-MISMATCH).** Fix the YAML config generator so it stops emitting `map_fn` to `setup_manning_roughness`. After that fix, the M5 chain either reaches SFINCS solver dispatch (first SUCCESS) or surfaces the next honest blocker (further setup-step kwarg mismatches, the forcing component, boundary conditions, or the solver itself).
  - **orchestrator (sprint planning).** No orchestrator screenshot moment in this job — the M5 chain still produces no flood-depth COG; the `if SUCCESS → screenshot` branch from the kickoff is NOT triggered.

## Verification

### Unit + integration test suite

```
$ PYTHONPATH=services/agent/src:packages/contracts/src \
    .venv-agent/bin/python -m pytest services/agent/tests/ -q
........................................................................ [ 54%]
............................................................             [100%]
132 passed, 4 warnings in 1.41s
```

`test_model_flood_scenario.py` alone: **13 passed** (11 pre-existing + 2 new OQ-49 regression tests).

### Live M5 chain re-run (the honest disclosure)

Command:

```
PATH=$HOME/tools/google-cloud-sdk/bin:$PATH \
  GOOGLE_CLOUD_PROJECT=grace-2-hazard-prod \
  GOOGLE_APPLICATION_CREDENTIALS=$HOME/.config/gcloud/application_default_credentials.json \
  CPL_GS_USE_GOOGLE_AUTH=YES \
  PYTHONPATH=services/agent/src:packages/contracts/src \
  .venv-agent/bin/python reports/inflight/job-0052-engine-20260607/evidence/smoke_demo.py
```

Key new log lines (`evidence/smoke_demo_log.txt`):

```
hydromt_sfincs.sfincs Initializing sfincs model from hydromt_sfincs (v1.2.2).
hydromt_sfincs.sfincs setup_grid_from_region.region: {'bbox': [-81.9126085, 26.5476424, -81.7511414, 26.689176]}
hydromt_sfincs.sfincs setup_grid_from_region.res: 30.0
hydromt_sfincs.sfincs setup_grid_from_region.crs: utm
hydromt_sfincs.sfincs setup_dep.datasets_dep: [{'elevtn': 'gs://grace-2-hazard-prod-cache/cache/static-30d/dem/87ba00463af0275d02115f7463afe6e9.tif'}]
hydromt_sfincs.sfincs Reading raster data from gs://grace-2-hazard-prod-cache/cache/static-30d/dem/...
hydromt_sfincs.sfincs Interpolate elevation at 96 cells
hydromt_sfincs.sfincs setup_mask_active.mask: None  ...  setup_mask_active.zmin: -10.0  setup_mask_active.zmax: 10.0
hydromt_sfincs.sfincs Derive region geometry based on active cells.
hydromt_sfincs.sfincs setup_manning_roughness.datasets_rgh: [{'lulc': 'gs://grace-2-hazard-prod-cache/cache/static-30d/landcover/...tif'}]
hydromt_sfincs.sfincs setup_manning_roughness.manning_land: 0.04
hydromt_sfincs.sfincs setup_manning_roughness.manning_sea: 0.02
hydromt_sfincs.sfincs setup_manning_roughness.rgh_lev_land: 0
grace2_agent.workflows.model_flood_scenario build_sfincs_model raised HYDROMT_BUILD_FAILED
  (underlying: "SfincsModel.setup_manning_roughness() got an unexpected keyword argument 'map_fn'")
smoke_demo outcome=HONEST FAILURE solver_version=failed:HYDROMT_BUILD_FAILED layers=0 elapsed=41.11s
```

**Comparison vs job-0049 baseline:** job-0049's smoke crashed inside `model.build(opt=yaml_text)` at the very first parse step with `'str' object has no attribute 'keys'`. This job's smoke runs the parsed dict cleanly into HydroMT — `setup_grid_from_region` executes, `setup_dep` reads the DEM from GCS via `gs://` (with IDW interpolation over 96 cells where the cached DEM does not cover the bbox), `setup_mask_active` executes with the documented zmin/zmax defaults, then HydroMT calls `setup_manning_roughness(**kwargs)` where `kwargs` includes `map_fn` — and hydromt-sfincs 1.2.2 raises `TypeError: SfincsModel.setup_manning_roughness() got an unexpected keyword argument 'map_fn'`. That `TypeError` is caught by our broad `except` and surfaced as `SFINCSSetupError("HYDROMT_BUILD_FAILED")` with the underlying message — **typed error, not crash**.

### Honest outcome disclosure

**NEXT HONEST BLOCKER (NOT first-successful-M5).** OQ-49 is closed by this job: the chain no longer fails at the `'str' object has no attribute 'keys'` step. The next honest blocker (OQ-52-MANNING-ROUGHNESS-MAP-FN-MISMATCH) is a different YAML-shape bug inside the same workflow file's `_generate_hydromt_yaml_config` helper — out of scope for this single-line hotfix, surfaced for routing.

### Captured envelope

`evidence/smoke_demo_envelope.json` — full `AssessmentEnvelope` shape:

- `envelope_type = "modeled"`, `hazard_type = "flood"`, `workflow_name = "model_flood_scenario"`
- `bbox = [-81.9126085, 26.5476424, -81.7511414, 26.689176]` (Fort Myers, FL)
- `forcing_type = "pluvial_synthetic"`, `forcing_source = "NOAA Atlas 14 Volume 9 Version 2 — 100-yr / 24-hr design storm"`
- `forcing_parameters.precip_inches = 11.9`
- `data_source_count = 5` — Nominatim, USGS 3DEP, NLCD 2021 (MRLC WMS), NHDPlus HR (USGS), NOAA Atlas 14
- `flood_solver_version = "failed:HYDROMT_BUILD_FAILED"`, `layer_count = 0`
- `outcome = "HONEST FAILURE"`, `error_code = "HYDROMT_BUILD_FAILED"`

### Acceptance criteria

- [x] One-line `yaml.safe_load` fix at the `SfincsModel.build(opt=...)` site in `sfincs_builder.py` + top-level `import yaml`. **PASS**.
- [x] Re-run M5 chain past `HYDROMT_BUILD_FAILED` (the previous step) with honest disclosure of next outcome. **PASS** — chain advances through `setup_grid_from_region`, `setup_dep` (DEM read from GCS + IDW interp on 96 cells), `setup_mask_active`; lands on OQ-52-MANNING-ROUGHNESS-MAP-FN-MISMATCH (NEXT HONEST BLOCKER).
- [x] At least 2 new tests. **PASS** (2 added; 13 total in `test_model_flood_scenario.py`, 132 total across `services/agent/tests/`).
- [x] No edits to FROZEN paths. **PASS** — edits scoped to `services/agent/src/grace2_agent/workflows/sfincs_builder.py`, `services/agent/tests/test_model_flood_scenario.py`, and `reports/inflight/job-0052-engine-20260607/`.
- [x] Closes OQ-49-HYDROMT-BUILD-OPT-ARGUMENT-SHAPE with cited evidence. **PASS**.
- [ ] Real flood-depth COG produced for orchestrator screenshot. **NOT TRIGGERED** — the kickoff's `if SUCCESS → screenshot` branch did not fire (the M5 chain is one more hotfix away from a real COG); the existing M5 visual continues to be job-0043's `final_honest_failure.png`.

### Results: PASS (with honest disclosure of NEXT HONEST BLOCKER)

The hotfix deliverable — `yaml.safe_load` at the `SfincsModel.build` site so the M5 chain advances past the previous `'str' object has no attribute 'keys'` failure — is met. The chain does not YET produce a real flood-depth COG; the new blocker is OQ-52-MANNING-ROUGHNESS-MAP-FN-MISMATCH (engine side, YAML config generator), routed for the next hotfix.
