# MODFLOW 6 Container + Cloud Run Job — Design Document

**Job:** job-0220-infra (sprint-13 Stage 1, MOD-1)
**Specialist:** infra
**Authored:** 2026-06-09
**Status:** DESIGN — not yet dispatched
**Gated on:** Wave 4.11 close

---

## 1. Container Basis

**Decision: official USGS binary via `modflowpy/flopy` download helper, not a community base image.**

MODFLOW 6 does not have a Deltares-style official Docker image maintained by USGS. The closest equivalent is the `modflowpy/modflow6` image published by the FloPy project, but it is updated infrequently and carries no digest guarantee matching the USGS release train. The preferred pattern — mirroring the SFINCS precedent at `services/workers/sfincs/Dockerfile:32` — is to start from a well-known Linux base and install the pinned binary explicitly.

**Base image:** `python:3.11-slim` (Debian Bookworm slim). Reasons:

- Python is a first-class runtime requirement (FloPy is needed at build-verification time and by the `gwt_adapter.py` that job-0221 writes; SFINCS did not need Python at model-run time so it used the upstream image's Ubuntu base; MODFLOW requires Python for input-deck construction via FloPy).
- `python:3.11-slim` is the canonical Debian-slim base for Python workers in this project (matches the PyQGIS-worker pattern from `infra/worker/Dockerfile`).
- Bookworm's pip 23+ requires `--break-system-packages` for system-level installs; use a `venv` at `/opt/grace2/.venv` to avoid this (matches the `infra/worker/Dockerfile` venv discipline).

**MODFLOW binary version pin:** MODFLOW 6.5.0 (latest stable as of 2026-06-09). The USGS release URL pattern is:
```
https://github.com/MODFLOW-USGS/modflow6/releases/download/6.5.0/mf6.0_linux.zip
```
The zip contains `bin/mf6` (the combined GWF + GWT binary). Pin at the zip SHA-256 checksum (to be recorded at build time; the Dockerfile RUN step must verify the checksum before extraction). The Artifact Registry digest discipline from `infra/sfincs.tf:84` applies: after `make modflow-build`, record the AR image digest in `infra/modflow.tf`.

**No Artifact Registry pull-through cache at v0.1** — same reasoning as `services/workers/sfincs/Dockerfile:9-16`: build frequency is low (~1/week max); if Docker Hub / GitHub rate limits become an issue, a one-line `FROM` change points to an AR mirror.

---

## 2. Solver Selection

MODFLOW 6 distributes a **single binary (`mf6`)** that contains both:

- **GWF (Groundwater Flow) model** — solves steady-state or transient saturated groundwater flow (the hydraulic head field that drives transport).
- **GWT (Groundwater Transport) model** — solves transient advection-dispersion-reaction solute transport. The `mf6-gwt` label used in the sprint-13 manifest (`sprint-13-manifest.md:63`) refers to the GWT package *within* the same `mf6` binary, not a separate executable.

This is a critical design simplification relative to MT3D-USGS (the alternative discussed in `sprint-13-manifest.md:281`): with `mf6`, there is exactly one binary to manage, version-pin, and smoke-test. The entry point runs `mf6` in the scratch directory; FloPy (in job-0221's `gwt_adapter.py`) assembles both the GWF and GWT input packages before the job runs.

**Enabled packages for Case 2 demo scope:**
- GWF: DIS (structured grid), IC (initial conditions), NPF (node-property flow), CHD (constant-head boundaries for inflow/outflow), OC (output control for heads).
- GWT: DSP (dispersion), SRC (mass source for the spill point), IC (initial concentration = 0), ADV (advection), OC (output control for concentration).

Reaction kinetics (biodegradation, sorption) are **out of scope** for v0.1 demo. The `gwt_adapter.py` (job-0221) should note this explicitly; the demo contaminant is a conservative tracer.

---

## 3. Dockerfile Structure

**File:** `services/workers/modflow/Dockerfile`
**Build context:** repo root (same as SFINCS, per `infra/sfincs/cloudbuild.yaml:22`).

Structure (annotated; do not write code — this is the spec):

1. `FROM python:3.11-slim` with a label block matching the SFINCS convention (`infra/sfincs/Dockerfile:34-37`): `grace-2-modflow-solver`, source repo, description scoped to the runs bucket.

2. **System deps install** (single `RUN apt-get update && apt-get install -y ... && rm -rf /var/lib/apt/lists/*`):
   - `ca-certificates` (for HTTPS downloads during build)
   - `curl` (binary download)
   - `unzip` (MF6 zip extraction)
   - No Fortran runtime packages needed: the USGS binary ships statically linked on Linux.

3. **Venv + Python deps** (single RUN, mirrors `infra/worker/Dockerfile` venv pattern):
   - Create `/opt/grace2/.venv`
   - pip install: `flopy>=3.7,<4`, `google-cloud-storage>=2.18,<4`, `numpy>=1.26,<3`, `rasterio>=1.3,<2` (for postprocess step — COG conversion of concentration output).

4. **MODFLOW binary download + verify + install** (single RUN):
   - `curl -fL <github-release-url> -o /tmp/mf6.zip`
   - sha256sum verify against the pinned checksum
   - `unzip /tmp/mf6.zip bin/mf6 -d /tmp/mf6_extracted`
   - `install -m 755 /tmp/mf6_extracted/bin/mf6 /usr/local/bin/mf6`
   - `rm -rf /tmp/mf6.zip /tmp/mf6_extracted`
   - Smoke: `mf6 --version` (exits 0 if binary runs; records the exact version string in build log — evidence the correct release is installed).

5. **COPY entrypoint:**
   ```
   COPY services/workers/modflow/ /opt/grace2/services/workers/modflow/
   ```

6. **WORKDIR / ENV** (mirrors `services/workers/sfincs/Dockerfile:64-86`):
   - `WORKDIR /opt/grace2`
   - `PYTHONPATH=/opt/grace2`, `PYTHONUNBUFFERED=1`
   - `GRACE2_MF6_BIN=/usr/local/bin/mf6`
   - `GRACE2_MF6_SCRATCH=/opt/grace2/work`
   - `GCP_PROJECT=grace-2-hazard-prod`
   - `GRACE2_CACHE_BUCKET=grace-2-hazard-prod-cache`
   - `GRACE2_RUNS_BUCKET=grace-2-hazard-prod-runs`
   - `GRACE2_RUN_ID=""`, `GRACE2_MANIFEST_URI=""`

7. **Build-time smoke** (mirrors `services/workers/sfincs/Dockerfile:91-93`):
   - Assert `mf6 --version` exits 0.
   - Assert `python3 -c "from services.workers.modflow.entrypoint import main; print('OK')"` exits 0.

8. **ENTRYPOINT:** `["python3", "-m", "services.workers.modflow.entrypoint"]`; `CMD []`.

---

## 4. Cloud Run Job Manifest (Terraform / OpenTofu)

**File:** `infra/modflow.tf` (new; mirrors `infra/sfincs.tf` section by section)

**Resource name:** `grace-2-modflow-solver`

**Sizing:**

| Parameter | Value | Rationale |
|---|---|---|
| cpu | `"4"` | Same as SFINCS (`infra/sfincs.tf:225`) — MODFLOW 6 is not parallel by default; 4 vCPU gives the OS scheduler headroom and matches the existing quota allocation. |
| memory | `"8Gi"` | **Doubled from SFINCS.** MODFLOW 6 with GWT loads the full head + concentration arrays for all time steps in working memory. A 20,000-cell grid (the demo scale) with 365 transport time steps is modest, but 4 GiB is tight once rasterio + numpy postprocess runs in the same container. 8 GiB is the safe demo-scale ceiling. Revisit for larger domains. |
| task_timeout | `"7200s"` | **Doubled from SFINCS's `1800s` (`infra/sfincs.tf:211`).** MODFLOW transport runs can take 10-60 minutes depending on grid size and time-step count. 2 hours is the demo-budget ceiling. An exit from the solver before this limit writes `completion.json`; the Workflow reads it and returns. |
| max_retries | `1` | Same as SFINCS (`infra/sfincs.tf:199`). MODFLOW is idempotent on the runs bucket (entrypoint clears scratch dir on start). |
| parallelism | `1` | Same as SFINCS. |
| task_count | `1` | Same as SFINCS. |

**Service account:** `modflow-runtime` (new, dedicated SA; no reuse of `sfincs-runtime`). IAM bindings mirror `infra/sfincs.tf:132-178`:

- `objectViewer` on `-cache` bucket (reads input FloPy-generated files uploaded by the agent's deck-uploader step).
- `objectAdmin` on `-runs` bucket (writes output binary files + `completion.json`).
- No `-qgs` viewer grant needed — MODFLOW does not interact with the QGIS project store.

**Workflow invoker SA:** `workflow-invoker-modflow` (mirrors `infra/sfincs.tf:286`). Same IAM pattern:
- `roles/run.invoker` + `roles/run.developer` on the Job resource (the `runWithOverrides` permission requirement is the same, per the diagnostic at `infra/sfincs.tf:306-314`).
- `roles/iam.serviceAccountUser` on `modflow-runtime` (actAs for override launches).
- `objectViewer` on `-runs` bucket (poll `completion.json`).
- `roles/logging.logWriter` project-scoped (same justification as `infra/sfincs.tf:362-370`).
- `roles/run.viewer` project-scoped (poll `run.operations.get` on LROs — same `infra/sfincs.tf:378-394` diagnostic applies).

**Cloud Build config:** `infra/modflow/cloudbuild.yaml` — verbatim copy of `infra/sfincs/cloudbuild.yaml` with `_IMAGE=grace-2-modflow-solver` and `--file=services/workers/modflow/Dockerfile`.

---

## 5. Cloud Workflows YAML

**Workflow name:** `grace-2-modflow-orchestrator`

**File:** defined inline in `infra/modflow.tf` (same `source_contents` heredoc pattern as `infra/sfincs.tf:438`).

**Shape** (mirrors the SFINCS 3-step structure exactly; Terraform double-dollar escaping applies):

```
steps:
  validate         → assert run_id + manifest_uri present
  invoke_mf6_job   → googleapis.run.v2.projects.locations.jobs.run
                     connector_params.timeout: 8400 (2h + 10min buffer)
                     body.overrides: GRACE2_RUN_ID + GRACE2_MANIFEST_URI env vars
  read_completion  → googleapis.storage.v1.objects.get
                     bucket: runs-bucket-name
                     object: ${args.run_id}/completion.json, alt: media
  return_completion
```

The SFINCS Workflow at `infra/sfincs.tf:450-509` is the direct precedent; the MODFLOW version differs only in the `name` field (pointing at `grace-2-modflow-solver`) and the `connector_params.timeout` (8400s vs 2400s, to give the 2-hour task budget room).

**Inputs (executions.create argument JSON):**
```json
{
  "run_id": "<ULID>",
  "manifest_uri": "gs://grace-2-hazard-prod-cache/modflow/<run_id>/manifest.json"
}
```

Identical structure to the SFINCS manifest contract (`services/workers/sfincs/entrypoint.py:17-29`).

---

## 6. Agent-Side Inputs — Deck JSON Shape

The agent's `run_modflow_job` tool (job-0227) constructs and uploads the deck manifest before calling `executions.create`. The manifest schema mirrors `services/workers/sfincs/entrypoint.py:17-29`:

```json
{
  "inputs": [
    {"gs_uri": "gs://.../gwf_model.nam",   "dest": "gwf/gwf_model.nam"},
    {"gs_uri": "gs://.../gwt_model.nam",   "dest": "gwt/gwt_model.nam"},
    {"gs_uri": "gs://.../mfsim.nam",       "dest": "mfsim.nam"},
    {"gs_uri": "gs://.../gwf/dis.dis6",    "dest": "gwf/dis.dis6"},
    {"gs_uri": "gs://.../gwf/ic.ic6",      "dest": "gwf/ic.ic6"},
    {"gs_uri": "gs://.../gwf/npf.npf6",    "dest": "gwf/npf.npf6"},
    {"gs_uri": "gs://.../gwf/chd.chd6",    "dest": "gwf/chd.chd6"},
    {"gs_uri": "gs://.../gwf/oc.oc6",      "dest": "gwf/oc.oc6"},
    {"gs_uri": "gs://.../gwt/ic.ic6",      "dest": "gwt/ic.ic6"},
    {"gs_uri": "gs://.../gwt/adv.adv6",    "dest": "gwt/adv.adv6"},
    {"gs_uri": "gs://.../gwt/dsp.dsp6",    "dest": "gwt/dsp.dsp6"},
    {"gs_uri": "gs://.../gwt/src.src6",    "dest": "gwt/src.src6"},
    {"gs_uri": "gs://.../gwt/oc.oc6",      "dest": "gwt/oc.oc6"}
  ],
  "mf6_args": [],
  "outputs": [
    "gwf/gwf_model.hds",
    "gwf/gwf_model.ddn",
    "gwt/gwt_model.ucn",
    "*.lst",
    "mfsim.lst"
  ]
}
```

The `gwt_adapter.py` (job-0221) is responsible for generating these files from `MODFLOWRunArgs` (job-0222: `spill_location_latlon`, `contaminant`, `release_rate_kg_s`, `duration_days`, `aquifer_k_ms`, `porosity`) and uploading them to the cache bucket under `modflow/<run_id>/` before writing the manifest.

**Key difference from SFINCS:** SFINCS takes a single `sfincs.inp` file in a flat directory; MODFLOW 6 uses a simulation namefile (`mfsim.nam`) that references subdirectory-namefile paths (`gwf/gwf_model.nam`, `gwt/gwt_model.nam`). The entrypoint downloads all inputs into scratch, preserving the subdirectory structure (using `item["dest"]` path with `mkdir -p`, exactly as `services/workers/sfincs/entrypoint.py:255-259` does), then runs `mf6` in the scratch root where `mfsim.nam` sits.

---

## 7. Outputs Produced

**Raw solver outputs** (glob-collected per the `outputs` manifest field, uploaded to `gs://grace-2-hazard-prod-runs/<run_id>/`):

| File | Description |
|---|---|
| `gwf/gwf_model.hds` | Binary heads file — hydraulic head at every cell, every save step |
| `gwf/gwf_model.ddn` | Binary drawdown file — optional; include if the agent narrates drawdown |
| `gwt/gwt_model.ucn` | Binary concentration file — the primary Case 2 output |
| `mfsim.lst` | MODFLOW simulation list file — contains solver convergence info |
| `gwf/gwf_model.lst` | GWF model list file |
| `gwt/gwt_model.lst` | GWT model list file |

**Postprocess step** (responsibility of `postprocess_modflow.py`, analogous to `services/agent/src/grace2_agent/workflows/postprocess_flood.py`):

The postprocess module is **not** in the solver container — it runs in the agent's Python environment, reading from the runs bucket after `completion.json` confirms success. This matches the SFINCS pattern (`model_flood_scenario.py:529-549`). Steps:

1. Download `gwt/gwt_model.ucn` from the runs bucket.
2. Read using FloPy's `flopy.utils.HeadFile` with `text='CONCENTRATION'`.
3. Extract the final time-step concentration array as a 2-D numpy array.
4. Reproject from the model's local CRS (EPSG derived from the `gwt_adapter`'s grid origin + projection) to EPSG:4326.
5. Write as Cloud-Optimized GeoTIFF (COG) with `rasterio` and `rio-cogeo`.
6. Upload to `gs://grace-2-hazard-prod-runs/<run_id>/plume_concentration_final.tif`.
7. Call `publish_layer` to register with QGIS Server.
8. Return `PlumeLayerURI` (from job-0222) with `max_concentration_mgl` + `plume_area_km2` derived from the array.

**Cache layout:**

```
gs://grace-2-hazard-prod-cache/modflow/<run_id>/
    mfsim.nam
    gwf/gwf_model.nam + *.{dis6,ic6,npf6,chd6,oc6}
    gwt/gwt_model.nam + *.{ic6,adv6,dsp6,src6,oc6}
    manifest.json

gs://grace-2-hazard-prod-runs/<run_id>/
    gwf/gwf_model.hds
    gwf/gwf_model.ddn
    gwt/gwt_model.ucn
    *.lst
    plume_concentration_final.tif   ← COG, postprocess step
    completion.json
```

---

## 8. Error Handling

### MODFLOW Exit Codes

MODFLOW 6 is not well-documented on exit codes beyond the POSIX convention. In practice:

| Exit code | Meaning | Retryable? |
|---|---|---|
| `0` | Normal termination (solver converged) | N/A — success |
| `1` | Input file error (missing package file, bad namefile path) | No — deck construction bug in `gwt_adapter`; surface as `DECK_INVALID` |
| `2` | Solver did not converge within max iterations | Tentatively no for v0.1; convergence failure usually means the model is ill-posed (too-large time step, bad aquifer params). Surface as `SOLVER_DIVERGED` |
| `nonzero other` | Runtime error (memory allocation, I/O) | Yes — one retry (same `max_retries=1` as SFINCS) |

The entrypoint detects exit code in the same `_run_mf6` function as `services/workers/sfincs/entrypoint.py:140-155`. All nonzero exits write `completion.json` with `status: "error"` and `exit_code` populated.

### Solver error threading into the envelope

The agent-side `run_modflow_job` (job-0227) reads `completion.json`. On `status: "error"`, it constructs a failed `GroundwaterPayload` with `solver_version: "failed:<exit_code_or_code>"`, matching the SFINCS pattern at `model_flood_scenario.py:176-229`. The error code bubbles to the LLM tool surface which narrates it honestly ("MODFLOW diverged — the aquifer parameters may be too extreme for the grid resolution").

### Convergence guard (entrypoint)

The entrypoint should parse `mfsim.lst` for the string `"FAILED TO MEET SOLVER CONVERGENCE CRITERIA"` before writing `completion.json`. If found, override `exit_code=2` and `error: "solver_diverged"` even if `mf6` exited 0 (MODFLOW 6 can return exit 0 with a convergence warning when tolerance is met at the last outer iteration — the list file is authoritative). This is a MODFLOW-specific guard with no SFINCS equivalent.

---

## 9. Tests Required

### Unit tests (`services/workers/modflow/tests/`)

**`test_deck_construction.py`** — deck-construction unit test (the primary gating test for job-0220 acceptance):

- Instantiate a `MODFLOWRunArgs` with synthetic demo parameters (spill_location at a known lat/lon, K=1e-4, porosity=0.3, release_rate=0.01 kg/s, duration=30 days).
- Call `gwt_adapter.build_deck(run_args, run_id="test-001", scratch_dir=tmp_path)` (this is job-0221's function, but the infra specialist must define the interface it expects).
- Assert: `mfsim.nam` exists in `tmp_path`, `gwf/` and `gwt/` subdirectories exist, `gwt/gwt_model.nam` references a `src.src6` package, concentration source rate is non-zero.
- **Does not run `mf6`** — pure Python, no binary required.

**`test_entrypoint_unit.py`** — manifest parsing + output-glob unit tests:

- Mock `google.cloud.storage.Client`, mock `subprocess.run` returning exit 0.
- Assert `completion.json` payload shape matches the contract (`services/workers/sfincs/entrypoint.py:186-217` is the reference schema).
- Assert `_run_mf6` with a synthetic `mfsim.lst` containing the convergence-failure string produces `exit_code=2`.

### Integration / smoke tests

**`test_modflow_smoke.py`** — env-gated (`GRACE2_TEST_LIVE_MODFLOW=1`):

- Requires `mf6` on PATH and real GCS credentials.
- Uses a tiny synthetic deck (3×3 grid, 1 time step, 1 transport step — FloPy can generate this in ~20 lines).
- Runs the full `main()` entrypoint against a real GCS bucket (the `-runs` dev bucket or a test prefix).
- Asserts `completion.json` appears with `status: "ok"`, `gwt/gwt_model.ucn` appears in `output_uris`.

**Cloud Workflows smoke** — also env-gated (`GRACE2_TEST_LIVE_MODFLOW=1`), lives in `infra/modflow/` as a `smoke_test.sh`:

- `gcloud workflows run grace-2-modflow-orchestrator --data='{"run_id":"smoke-001","manifest_uri":"gs://.../smoke_manifest.json"}'`
- Polls until terminal state.
- Asserts `completion.result.status == "ok"`.
- This is the live-verify evidence required by the adversarial panel for job-0220.

---

## 10. Open Questions

**OQ-MOD-1: mfsim.lst convergence-failure parse reliability.** MODFLOW 6 list file format is not officially versioned. The string `"FAILED TO MEET SOLVER CONVERGENCE CRITERIA"` appears in MODFLOW 6.4+ list files; earlier releases used a different string. Since we are pinning to 6.5.0 this is stable, but the implementation specialist should grep the actual 6.5.0 list file from a known-divergent run to confirm the exact string before hardcoding. TENTATIVE: pin the string at Dockerfile build time from the test run evidence.

**OQ-MOD-2: FloPy version compatibility with MODFLOW 6.5.0.** FloPy `>=3.7` supports MODFLOW 6.4+. The `gwt_adapter` (job-0221) must use `flopy.mf6` (not the legacy `flopy.modflow` API). This is straightforward but the implementation specialist should run `flopy.utils.MfGrdFile` against a 6.5.0 output file to confirm the binary format is recognized before committing. TENTATIVE: FloPy 3.7 is confirmed compatible with 6.5.0 per the FloPy changelog; no blocker expected.

**OQ-MOD-3: COG reprojection CRS.** The `gwt_adapter` constructs a structured grid in a local CRS (the `dis.dis6` package specifies NROW, NCOL, DELR, DELC, and origin in some projected CRS). The postprocess step must know the model's CRS to reproject to EPSG:4326. TENTATIVE: the `gwt_adapter` writes the EPSG code into the manifest's `model_crs` field (a new field, not present in the SFINCS manifest schema), and the postprocess step reads it from there.

**OQ-MOD-4: Memory ceiling for larger demo domains.** The 8 GiB memory limit was derived for a ~20,000-cell demo grid. If the Case 2 domain (job-0228) requires a finer grid to produce a visually convincing plume (e.g., 100m cell size over 5 km × 5 km = 50,000 cells × 365 transport steps), peak RSS could reach 6-7 GiB. The implementation specialist should profile memory usage on the synthetic smoke model before the adversarial verify. If >8 GiB is needed, the Tofu resource must be bumped and the sprint-13 manifest's compute-class table updated. TENTATIVE: 8 GiB is sufficient for the demo grid; escalate if the `gwt_adapter` (job-0221) adopts a finer resolution.

**OQ-MOD-5: `plume_area_km2` computation method.** `PlumeLayerURI.plume_area_km2` (from job-0222) must be derived from the concentration COG. The cleanest method is to threshold the concentration array at a meaningful level (e.g., 1 mg/L detection limit) and count cells above threshold × cell area. The threshold value should be a parameter in `postprocess_modflow.py` with a sensible default. TENTATIVE: 1 mg/L threshold, configurable via an env var `GRACE2_PLUME_THRESHOLD_MGL=1.0`.

**OQ-MOD-6: `gwt_adapter` grid-construction dependency on DEM.** The SFINCS flow (see `model_flood_scenario.py:364-386`) fetches a DEM and uses it for grid construction. MODFLOW groundwater flow grids are typically constructed from hydrogeologic data, not surface DEMs; however, the aquifer top/bottom elevations must be geologically reasonable for the spill location. The `MODFLOWRunArgs` contract (job-0222) includes `aquifer_k_ms` and `porosity` but not explicit aquifer top/bottom elevations. TENTATIVE: the `gwt_adapter` derives aquifer top from USGS 3DEP surface elevation (same `fetch_dem` tool) minus a configurable unsaturated zone depth (default 5 m), and aquifer bottom as top minus a configurable saturated thickness (default 30 m). This is a demo simplification; real models require proper hydrogeologic data.

---

## Decisions Made

| Decision | Choice | Rationale |
|---|---|---|
| Binary source | USGS GitHub release zip, version-pinned | No maintained official Docker image; thin-layer pattern from SFINCS (`Dockerfile:9-16`) |
| Solver | `mf6` single binary (GWF + GWT in one) | No separate binary for GWT; same binary as GWF — simpler than MT3D-USGS (sprint-13-manifest OQ-1) |
| Base image | `python:3.11-slim` | Python is first-class (FloPy + rasterio); differs from SFINCS Ubuntu base because MODFLOW doesn't ship its own base image |
| Memory | 8 GiB | MODFLOW transport loads full arrays; 4 GiB (SFINCS baseline) is too tight |
| Timeout | 7200s | Transport runs take 10-60 min; doubled from SFINCS's 1800s; 2h is the demo budget |
| Postprocess location | Agent-side Python, not in container | Matches SFINCS pattern (`model_flood_scenario.py:529`); keeps container single-purpose |
| Convergence check | Parse `mfsim.lst` in entrypoint | MODFLOW 6 can exit 0 with convergence warning; list file is authoritative |
| Reaction kinetics | Out of scope for v0.1 | Conservative tracer only; Case 2 demo does not require biodegradation/sorption |

---

*This design is frozen at dispatch. New directives for scope not covered here go into the next job per the GRACE-2 kickoff-freeze convention (`CLAUDE.md:18`).*
