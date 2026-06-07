# Audit: agent emits manifest.json + worker-compliant URI for SFINCS deck (OQ-56)

**Job ID:** job-0057-engine-20260607, **Sprint:** sprint-08 (mid-sprint follow-up #5 to migration chain — addresses the OQ-56 SOLVER_FAILED real-manifest blocker), **Auditor:** Development Orchestrator, **Status:** assigned

## Task Assignment

**Specialist:** engine

**Prerequisites:**
- **job-0056 (APPROVED):** pandas pin landed; HydroMT model build completes; SFINCS deck uploaded; Cloud Workflows real-manifest execution dispatched + ran ~3.8 minutes; SOLVER_FAILED on real manifest. **Orchestrator-direct diagnostic** of the Cloud Run Job logs identified the real blocker — see "Why this job exists" below.
- job-0040 (APPROVED): SFINCS Cloud Run Job + worker entrypoint contract (services/workers/sfincs/entrypoint.py)
- job-0041 (APPROVED): run_solver + wait_for_completion (caller of the worker)
- job-0042 (APPROVED): model_flood_scenario workflow composition + build_sfincs_model deck upload

**SRS references** (narrow file loading only):
- `docs/srs/03-functional-requirements.md` (FR-CE-1/2/3 — solver dispatch contract)
- DO NOT load `docs/SRS_v0.3.md` monolith.

**Required reads:**
- `services/workers/sfincs/entrypoint.py` lines 1-130 — the WORKER CONTRACT (single JSON manifest with `{inputs: [{gs_uri, dest}], sfincs_args, outputs}`)
- `services/agent/src/grace2_agent/workflows/sfincs_builder.py` lines 492-502 (`_default_setup_uri`) + 770-860 (upload code)
- `reports/complete/job-0056-infra-20260607/report.md` — proof of the deck reaching GCS + the SOLVER_FAILED outcome

### Why this job exists (orchestrator-direct diagnostic)

Cloud Run Job execution `grace-2-sfincs-solver-4pvw9` ran 1m35s + exited 1. Cloud Run logs (via `gcloud logging read`) show the **real error**:

```
INFO grace-2-sfincs-solver starting — manifest=gs://grace-2-hazard-prod-cache/cache/static-30d/sfincs_setup/01KTHQP54XVAAF2NPGKTAMP4PV/
INFO reading manifest gs://.../sfincs_setup/01KTHQP54XVAAF2NPGKTAMP4PV/
ERROR solver entrypoint failed
google.api_core.exceptions.NotFound: 404 GET .../sfincs_setup/01KTHQP54XVAAF2NPGKTAMP4PV/: No such object
```

**The SFINCS binary never ran.** The worker entrypoint failed at the FIRST step (reading the manifest). Mismatch between:

- **Worker contract** (`entrypoint.py:9-23`): `manifest_uri` is a single JSON FILE like `gs://bucket/path/setup.json` with schema `{inputs: [{gs_uri, dest}], sfincs_args, outputs}`. The worker uses `blob.download_as_text()` on it.
- **Agent emission** (`sfincs_builder.py:492-502`): `_default_setup_uri` returns the DIRECTORY URI `gs://bucket/cache/static-30d/sfincs_setup/{setup_id}/` (trailing slash). The agent uploads deck files via `fs.upload(local_deck, setup_uri, recursive=True)` (line 844) but does NOT generate a `manifest.json`.

The trailing-slash directory URI hits a `blob_name = "cache/static-30d/sfincs_setup/{setup_id}/"` which GCS treats as a single non-existent object (404).

### Scope

1. **`services/agent/src/grace2_agent/workflows/sfincs_builder.py`**:
   - **`_default_setup_uri`**: return the manifest FILE URI, not the directory URI — `gs://{bucket}/cache/static-30d/sfincs_setup/{setup_id}/manifest.json`. The directory prefix is implicit.
   - **`build_sfincs_model`**: after the local SFINCS deck is generated (the `tmp / "deck"` directory), enumerate the files and compose a `manifest.json` that the worker can read. Schema per `services/workers/sfincs/entrypoint.py:9-23`:
     ```json
     {
       "inputs": [
         {"gs_uri": "gs://bucket/.../sfincs_setup/<id>/sfincs.inp", "dest": "sfincs.inp"},
         {"gs_uri": "gs://bucket/.../sfincs_setup/<id>/dep.tif", "dest": "dep.tif"},
         {"gs_uri": "gs://bucket/.../sfincs_setup/<id>/manning.tif", "dest": "manning.tif"},
         {"gs_uri": "gs://bucket/.../sfincs_setup/<id>/precip.csv", "dest": "precip.csv"}
         // ... every file in the deck
       ],
       "sfincs_args": [],
       "outputs": ["sfincs_map.nc", "*.nc", "*.tif"]
     }
     ```
   - **Upload behavior**: keep the existing `fs.upload(local_deck, gs://.../sfincs_setup/<id>/, recursive=True)` for the deck contents AND additionally upload the `manifest.json` to `gs://.../sfincs_setup/<id>/manifest.json`. The returned `setup_uri` is the manifest URI (NOT the directory).
   - **`sfincs_args` + `outputs`**: the v0.1 deck uses HydroMT's standard SFINCS output files. SFINCS reads `sfincs.inp` from CWD (no `sfincs_args` needed); outputs include `sfincs_map.nc` (the gridded flood depth) + `sfincs_his.nc` (timeseries) + any `.tif` rasters. Use the worker doc's glob examples: `["sfincs_map.nc", "*.nc", "*.tif"]`.

2. **`services/agent/tests/test_model_flood_scenario.py`**: add at least 2 new tests:
   - `test_build_sfincs_model_emits_manifest_json_with_input_list` — assert that after `build_sfincs_model` runs, a `manifest.json` was emitted with the expected `{inputs, sfincs_args, outputs}` shape; `inputs[]` contains every file in the deck with both `gs_uri` and `dest`.
   - `test_build_sfincs_model_setup_uri_points_at_manifest_file` — assert the returned `ModelSetup.setup_uri` ends in `/manifest.json` and is downloadable as text (per the worker's contract). Mock GCS appropriately.

3. **Re-run M5 smoke chain**:
   - Use `reports/complete/job-0056-infra-20260607/evidence/smoke_demo.py` as the harness. Copy + run from `reports/inflight/job-0057-engine-20260607/evidence/`.
   - Expect: Cloud Run Job execution should now find the manifest and proceed past line 126 (`_read_manifest`). Then SFINCS should actually run.
   - **OUTCOMES**:
     - **SUCCESS (the headline):** chain produces a real flood-depth COG. Capture: GCS URI of `sfincs_map.nc` (or rasterized `.tif`); sample read confirming non-zero depth values; AssessmentEnvelope JSON; full smoke log. **Explicitly call out "SCREENSHOT MOMENT — orchestrator captures via Playwright" in the final summary.**
     - **PARTIAL SUCCESS:** worker reads the manifest + downloads inputs + runs SFINCS but SFINCS exits non-zero (e.g., the deck contents have a real model error — sfincs.inp parse error, missing grid, etc.). Document the new failure class.
     - **STILL BLOCKED with 404:** the URI hand-off is correct but the manifest's `gs_uri` entries still don't match the actual uploaded paths. Document.

### File ownership (exclusive)
- `services/agent/src/grace2_agent/workflows/sfincs_builder.py` — `_default_setup_uri` + `build_sfincs_model` body (manifest emission + upload)
- `services/agent/tests/test_model_flood_scenario.py` — additive tests
- `reports/inflight/job-0057-engine-20260607/`

### FROZEN
- `services/workers/sfincs/entrypoint.py` — the WORKER CONTRACT is the source of truth; don't edit. The agent must conform to its `{inputs, sfincs_args, outputs}` shape.
- `services/agent/src/grace2_agent/workflows/manning_mapping.csv` (OQ-4 §4 substrate)
- `services/agent/pyproject.toml` (just-pinned pandas; don't disturb)
- All other workflows/* and tools/* files
- packages/contracts/**, infra/**, web/**, docs/srs/**, styles/**, services/workers/**, reports/complete/**

### Acceptance criteria
- [ ] `_default_setup_uri` returns a manifest file URI ending in `/manifest.json`
- [ ] `build_sfincs_model` enumerates the local deck contents + composes a `manifest.json` matching the worker contract schema
- [ ] The manifest is uploaded to GCS alongside the deck files
- [ ] ≥2 new tests guard against regression of the manifest emission + URI shape
- [ ] M5 chain re-run; worker reads the manifest successfully (no more 404)
- [ ] Honest disclosure of outcome (SUCCESS / PARTIAL / STILL-BLOCKED)
- [ ] No edits to FROZEN paths (especially `services/workers/sfincs/entrypoint.py`)
- [ ] If SUCCESS, explicit "SCREENSHOT MOMENT" call-out
- [ ] Single commit
