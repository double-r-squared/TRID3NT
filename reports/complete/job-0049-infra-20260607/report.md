# Report: hydromt-sfincs install in agent service (closes OQ-43-HYDROMT-SFINCS-DEV-VENV-INSTALL)

**Job ID:** job-0049-infra-20260607
**Sprint:** sprint-08
**Specialist:** infra
**Task:** Add `hydromt-sfincs` to the agent service dependency set; install in `.venv-agent`; document the GPLv3 posture in `infra/THIRD_PARTY_LICENSES.md`; re-run the job-0042 / job-0043 M5 chain past the previous `HYDROMT_UNAVAILABLE` blocker and capture the honest new outcome.
**Status:** ready-for-audit

## Summary

Installed `hydromt-sfincs` + transitive `hydromt` + `fsspec[gcs]` into the dev `.venv-agent`. Live-verified the import works (`hydromt_sfincs 1.2.2`, `hydromt 0.10.1`, `fsspec 2026.4.0`). Documented the GPLv3 posture and the honest pin correction in the new `infra/THIRD_PARTY_LICENSES.md`. Re-ran the job-0043 M5 smoke harness end-to-end against the deployed substrate (`grace-2-hazard-prod`); the chain now advances past the previous `HYDROMT_UNAVAILABLE` blocker — all five fetchers cache-hit, the Invariant 7 NLCD validation gate passes on canonical class set `[11, 21, 22, 23, 24, 31, 41, 42, 43, 52, 71, 81, 82, 90, 95]`, Atlas 14 forcing loads (11.9 inches at Fort Myers), HydroMT-SFINCS initialises (`Initializing sfincs model from hydromt_sfincs (v1.2.2)`), and lands on a **new honest blocker** — `HYDROMT_BUILD_FAILED` with underlying `'str' object has no attribute 'keys'`. This is an engine-layer bug in `sfincs_builder.py` (line 692 passes a raw YAML text blob to `SfincsModel.build(opt=...)`, but hydromt-sfincs 1.2.x expects a parsed dict/mapping at that argument). Out of infra scope; surfaced as `OQ-49-HYDROMT-BUILD-OPT-ARGUMENT-SHAPE` for routing to engine. The M5 chain no longer fails for `HYDROMT_UNAVAILABLE` — that closes the OQ-43 deliverable cleanly. No agent Dockerfile exists yet (agent service is not deployed to Cloud Run today; only QGIS Server and the worker have Dockerfiles in `infra/`); pyproject.toml is the only install surface for the dev `.venv-agent` and a future agent Cloud Run service build.

## Changes Made

- **`services/agent/pyproject.toml`** (EDIT — corrective pin).
  - Replaced the pre-existing pair `"hydromt>=1.0,<2"` + `"hydromt-sfincs>=1.1.2,<2.0"` with the single line `"hydromt-sfincs>=1.1.0,<2.0"` (transitive `hydromt` is resolved automatically, currently to 0.10.1).
  - Reason: the OQ-4 §4 paper pin is internally inconsistent against the actual PyPI release sequence — `hydromt-sfincs 1.1.2` does not exist (line goes 1.1.0 → 1.2.0), and the stable `hydromt-sfincs 1.2.x` series transitively constrains `hydromt < 1`. Both constraints "hydromt-sfincs < 2.0" and "hydromt >= 1.0" can NOT be satisfied simultaneously in any published release as of 2026-06-07; resolution requires `hydromt-sfincs 2.0.0rcN`, which we explicitly defer per OQ-4 §4 ("until v2.0 exits RC"). Documented in the file's inline comment block + the new THIRD_PARTY_LICENSES.md OQ-49 note.
- **`infra/THIRD_PARTY_LICENSES.md`** (NEW). Documents the `hydromt-sfincs` GPLv3 license posture: GPLv3 applies to the plugin itself; importing the Python module in-process is dynamic-link-against-Python-library, not static linkage against compiled GPL'd binaries; the SFINCS solver binary runs out-of-process in its own Cloud Run Job (FR-CE-1 isolation). The MIT posture of GRACE-2 code is preserved. Also records the OSI license posture (NFR-L-1 `LICENSE` at repo root) and the honest pin correction above (the "what we actually shipped" record).
- **`reports/inflight/job-0049-infra-20260607/evidence/`** (NEW):
  - `smoke_demo.py` — copy of job-0043's M5 smoke harness, re-run from the inflight directory so the captured envelope reflects the post-install state.
  - `smoke_demo_log.txt` — full stdout/stderr capture of the smoke run.
  - `smoke_demo_envelope.json` — `AssessmentEnvelope` summary; `outcome=HONEST FAILURE`, `error_code=HYDROMT_BUILD_FAILED`, `elapsed=11.47 s`.
- **NO agent Dockerfile change.** Confirmed `services/agent/` has no Dockerfile today; `infra/` contains Dockerfiles for `qgis-server/` and `worker/` only. The agent service is not yet deployed to Cloud Run (current state per `reports/PROJECT_STATE.md` § Environment facts and `infra/README.md`); deployment is a sprint-08+ infra job. When that Cloud Run service definition lands, the build pipeline will install from `pyproject.toml` and pick up the new dep automatically.

## Decisions Made

- **Decision: relax `hydromt` upper pin and drop the explicit `hydromt>=1.0` constraint, letting `hydromt-sfincs 1.2.x` resolve `hydromt` transitively to 0.10.1.** Rationale: the OQ-4 §4 paper contract is incompatible with the published `hydromt-sfincs` v1.x line; we either (a) honour the paper pin and break the install entirely, (b) adopt the `hydromt-sfincs 2.0.0rc` line (OQ-4 §4 explicitly says "until v2.0 exits RC" — so no), or (c) ship the working combo and document the deviation. Chose (c). When `hydromt-sfincs 2.0` exits RC, both pins move forward together. Surfaced as **OQ-49-HYDROMT-SFINCS-PIN-RECONCILIATION**.

- **Decision: install in `.venv-agent` AND update `pyproject.toml` BUT do NOT introduce an agent Dockerfile in this job.** Rationale: the kickoff §1 directs "if Dockerfile exists" — it doesn't. The agent service is not currently a deployed Cloud Run service; sprint-07 closed with the agent run from the dev venv only. Authoring a Dockerfile here would be a scope leak into the sprint-08 deploy-agent infra job (which doesn't exist yet as a sprint-08 row). The pyproject.toml change is the contract; future Dockerfile / source-deploy / buildpack flows pick it up from there. Surfaced as **OQ-49-AGENT-CLOUD-RUN-DEPLOY-PENDING**.

- **Decision: do NOT capture a screenshot in this job.** Rationale: the M5 chain still produces no flood-depth COG (the new blocker is at HydroMT deck-build, BEFORE SFINCS runs, BEFORE postprocess writes a COG). The kickoff's "if SUCCESS → screenshot" branch is not triggered. Honest disclosure of the new blocker is the deliverable; job-0043's existing `final_honest_failure.png` is still the canonical M5 visual.

## Invariants Touched

- **Invariant 1 (Determinism boundary): preserves.** No LLM in the install or verification chain. The new blocker is a deterministic Python AttributeError — same input shape → same failure.
- **Invariant 7 (no silent wrong answers): preserves, witnessed live.** The NLCD validation gate fires PASS-branch again on canonical NLCD classes (live re-witnessed by this job's smoke run, identical class set to job-0043's PASS-branch observation). The new failure is a typed `SFINCSSetupError("HYDROMT_BUILD_FAILED")` with full details, NOT a silent fallback.
- **NFR-L (licensing): EXTENDS.** `infra/THIRD_PARTY_LICENSES.md` lands the GPLv3 posture statement and the OSI-license root anchor reference. First entry in this file; future copyleft-bearing deps land here too.
- **NFR-PO-3 (deployable via IaC): preserves.** No console-clicked resources created; the install is `pip install` from PyPI driven by `pyproject.toml`. When the agent Cloud Run service definition lands, the pyproject.toml IS the IaC for the dep set.

## Open Questions

- **OQ-49-HYDROMT-BUILD-OPT-ARGUMENT-SHAPE (TENTATIVE: engine fix — parse YAML before passing to `SfincsModel.build()`).** The new blocker in the M5 smoke run is `services/agent/src/grace2_agent/workflows/sfincs_builder.py:692` passes the result of `_generate_hydromt_yaml_config(...)` (a raw YAML text string) to `model.build(opt=yaml_text)`. In `hydromt-sfincs 1.2.x`, `SfincsModel.build()` expects `opt` to be a `Dict[str, Dict[str, Any]]` (parsed step configuration), not a YAML string. The fix is one-line: parse the YAML with `yaml.safe_load()` before passing, OR switch to HydroMT's `read_ini` / `parse_yaml` helper that returns the expected shape. Out of infra scope (`services/agent/src/**` is FROZEN to this job). Routes to: engine (next sprint-08 follow-up); will unblock real SFINCS deck build and either expose another honest blocker or land the first end-to-end success.

- **OQ-49-HYDROMT-SFINCS-PIN-RECONCILIATION (TENTATIVE: ship the working combo; reconcile when v2.0 exits RC).** The OQ-4 §4 paper pin (`hydromt >= 1.0, < 2` + `hydromt-sfincs >= 1.1.2, < 2.0`) is internally contradictory in the published PyPI releases. This job ships `hydromt-sfincs >= 1.1.0, < 2.0` with `hydromt` resolved transitively (currently 0.10.1) and documents the deviation in THIRD_PARTY_LICENSES.md. Routes to: orchestrator (decide whether to amend `docs/decisions/oq-4-hydromt-depth.md` to match the working combo, or hold the paper pin as aspirational against v2.0).

- **OQ-49-AGENT-CLOUD-RUN-DEPLOY-PENDING (TENTATIVE: sprint-08 deploy infra job).** The agent service has no Cloud Run service definition in `infra/*.tf` and no Dockerfile in `services/agent/`. M5 acceptance ran via the dev venv against the production substrate. Deploying the agent to Cloud Run (so the WS endpoint is reachable from a browser client, not just localhost) is the next material infra job. Routes to: orchestrator (sprint-08 scope decision); infra (next job).

## Dependencies and Impacts

- **Depends on:**
  - **job-0038 (engine, APPROVED).** `docs/decisions/oq-4-hydromt-depth.md` OQ-4 §4 contract. This job ships the install side of that decision with the honest pin correction documented.
  - **job-0042 (engine, APPROVED).** `services/agent/src/grace2_agent/workflows/sfincs_builder.py` is the consumer; it imports `hydromt_sfincs` lazily and now resolves successfully (verified by smoke run). It also surfaced the new `HYDROMT_BUILD_FAILED` blocker.
  - **job-0043 (testing, APPROVED).** `OQ-43-HYDROMT-SFINCS-DEV-VENV-INSTALL` is the open question this job closes. The M5 smoke harness re-used here is job-0043's `evidence/smoke_demo.py`.
  - **job-0040 (infra, APPROVED).** The SFINCS container substrate. This job is the agent-service side of the same dependency.

- **Affects (downstream):**
  - **engine (sprint-08+ follow-up for OQ-49-HYDROMT-BUILD-OPT-ARGUMENT-SHAPE).** The new blocker is a one-line fix in `sfincs_builder.py:692`. Once landed, the M5 chain either produces a real flood-depth COG (the first SUCCESS branch) or surfaces the next honest blocker (forcing.meteo / boundary-condition / mask component). Either way is progress.
  - **orchestrator (sprint planning).** OQ-49-HYDROMT-SFINCS-PIN-RECONCILIATION is a `docs/decisions/oq-4-hydromt-depth.md` amendment candidate.
  - **infra (sprint-08+ follow-up for OQ-49-AGENT-CLOUD-RUN-DEPLOY-PENDING).** Author the agent service Cloud Run definition + Dockerfile + build pipeline. The pyproject.toml dep set this job ships is the contract that flow consumes.

## Verification

### Install verification

```
$ .venv-agent/bin/python -m pip install --quiet "hydromt-sfincs>=1.1.2,<2.0" "fsspec[gcs]>=2024.6"
(exits 0; transitive deps resolved)

$ .venv-agent/bin/python -c "import hydromt_sfincs; print('hydromt_sfincs', hydromt_sfincs.__version__); import hydromt; print('hydromt', hydromt.__version__); import fsspec; print('fsspec', fsspec.__version__)"
hydromt_sfincs 1.2.2
hydromt 0.10.1
fsspec 2026.4.0
```

### Live M5 chain re-run (closes OQ-43)

Command:

```
$ PATH=$HOME/tools/google-cloud-sdk/bin:$PATH \
  GOOGLE_CLOUD_PROJECT=grace-2-hazard-prod \
  GOOGLE_APPLICATION_CREDENTIALS=$HOME/.config/gcloud/application_default_credentials.json \
  CPL_GS_USE_GOOGLE_AUTH=YES \
  PYTHONPATH=services/agent/src:packages/contracts/src \
  .venv-agent/bin/python reports/inflight/job-0049-infra-20260607/evidence/smoke_demo.py
```

Key log lines (`evidence/smoke_demo_log.txt`):

```
registered 14 agent tools (M5 expects 14)
==== M5 demo: model_flood_scenario(Fort Myers, FL) ====
read_through miss-write tool=geocode_location ... bytes=281 (fresh Nominatim hit)
read_through hit tool=fetch_dem ... bytes=1924297
read_through hit tool=fetch_landcover ... bytes=289170
read_through hit tool=fetch_river_geometry ... bytes=274376
read_through hit tool=lookup_precip_return_period ... bytes=1614
lookup_precip_return_period (lat=26.616666667 lon=-81.833333333 ari=100 dur=24-hr) -> 11.900 inches cache_hit=True
manning_mapping loaded version=1.0.0 classes=20
landcover classes observed: [11, 21, 22, 23, 24, 31, 41, 42, 43, 52, 71, 81, 82, 90, 95] (vintage_year=2021)
gcsfs experimental features enabled via GCSFS_EXPERIMENTAL_ZB_HNS_SUPPORT.
hydromt_sfincs.sfincs Initializing sfincs model from hydromt_sfincs (v1.2.2).   <-- NEW: previously failed at the `import hydromt_sfincs` step
build_sfincs_model raised HYDROMT_BUILD_FAILED (details={'bbox': [...], 'dem_uri': 'gs://...', 'landcover_uri': 'gs://...', 'river_geometry_uri': 'gs://...', 'underlying': "'str' object has no attribute 'keys'"})
outcome=HONEST FAILURE solver_version=failed:HYDROMT_BUILD_FAILED layers=0 elapsed=11.47s
```

**Comparison vs job-0043 baseline:** job-0043's smoke at this exact step raised `HYDROMT_UNAVAILABLE` (`No module named 'hydromt_sfincs'`). This job's smoke loads hydromt-sfincs successfully (`Initializing sfincs model from hydromt_sfincs (v1.2.2)` is the new line), runs HydroMT's constructor, and lands on the NEXT honest blocker (`HYDROMT_BUILD_FAILED`, underlying = `'str' object has no attribute 'keys'`) — an engine-side YAML / mapping-shape mismatch at `sfincs_builder.py:692` (`model.build(opt=yaml_text)` is passing a YAML string where 1.2.x wants a parsed dict). OQ-43 is closed; the new blocker is OQ-49-HYDROMT-BUILD-OPT-ARGUMENT-SHAPE.

### Captured envelope

`evidence/smoke_demo_envelope.json` — full `AssessmentEnvelope` shape:

- `envelope_type = "modeled"`, `hazard_type = "flood"`, `workflow_name = "model_flood_scenario"`
- `bbox = [-81.9126085, 26.5476424, -81.7511414, 26.689176]` (Fort Myers, FL — geocoded fresh)
- `forcing_type = "pluvial_synthetic"`, `forcing_source = "NOAA Atlas 14 Volume 9 Version 2 — 100-yr / 24-hr design storm"`
- `forcing_parameters.precip_inches = 11.9` (live Atlas 14)
- `data_source_count = 5` — Nominatim, USGS 3DEP, NLCD 2021 (MRLC WMS), NHDPlus HR (USGS), NOAA Atlas 14
- `flood_solver_version = "failed:HYDROMT_BUILD_FAILED"`, `layer_count = 0`
- `outcome = "HONEST FAILURE"`, `error_code = "HYDROMT_BUILD_FAILED"`

### Acceptance criteria

- [x] `hydromt-sfincs >= 1.1.2, < 2.0` installed in `.venv-agent` + verified import works. **PASS** with honest pin correction: `1.1.2` does not exist on PyPI; installed `1.2.2` from the working `>= 1.1.0, < 2.0` range. The OQ-4 §4 paper contract is reconciled in THIRD_PARTY_LICENSES.md.
- [x] Re-run of job-0042 / job-0043 M5 chain past the previous HYDROMT_UNAVAILABLE failure — capture the new outcome honestly. **PASS** — chain advances through HydroMT initialisation; lands on `HYDROMT_BUILD_FAILED` (engine-layer YAML-shape bug, surfaced as OQ-49-HYDROMT-BUILD-OPT-ARGUMENT-SHAPE).
- [x] `infra/THIRD_PARTY_LICENSES.md` documents the GPLv3 dep. **PASS**.
- [x] No edits to FROZEN paths. **PASS** — edits scoped to `services/agent/pyproject.toml`, `infra/THIRD_PARTY_LICENSES.md`, `reports/inflight/job-0049-infra-20260607/`. `services/agent/src/**` and all `infra/*.tf` untouched.
- [x] Closes OQ-43-HYDROMT-SFINCS-DEV-VENV-INSTALL with cited evidence. **PASS**.

### Results: PASS (with honest disclosure of new blocker)

The infra deliverable — install hydromt-sfincs, document the GPLv3 posture, verify the install moves the M5 chain past `HYDROMT_UNAVAILABLE` — is met. The M5 chain does NOT yet produce a real flood-depth COG; the new blocker is engine-layer and routes to the next engine follow-up job.
