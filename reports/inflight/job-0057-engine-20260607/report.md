# Report: agent emits manifest.json + worker-compliant URI for SFINCS deck (OQ-56)

**Job ID:** job-0057-engine-20260607
**Sprint:** sprint-08
**Specialist:** engine
**Task:** Fix `_default_setup_uri` to return a manifest FILE URI; emit `manifest.json` from `build_sfincs_model`; re-run M5 smoke to confirm worker reads the manifest.
**Status:** ready-for-audit

## Summary

The orchestrator-direct diagnostic correctly identified the root cause: `_default_setup_uri` was returning a trailing-slash directory URI, and no `manifest.json` was being written. The worker's `_read_manifest` called `blob.download_as_text()` on the directory URI, which hit a GCS 404. This job fixes both bugs: `_default_setup_uri` now returns a manifest file URI ending in `/manifest.json`, and `build_sfincs_model` enumerates the HydroMT-generated deck files and uploads a `manifest.json` whose `inputs[].gs_uri` values point to the actual upload locations. The re-run smoke confirms the worker read the manifest, downloaded all 11 deck files, ran SFINCS (exit=0), and produced a `sfincs_map.nc` with real flood depth values (hmax up to 3.52 m over Fort Myers, FL). The remaining failure is `COG_WRITE_FAILED` in `postprocess_flood` — a separate module outside this job's scope.

## Changes Made

- **File:** `services/agent/src/grace2_agent/workflows/sfincs_builder.py`
  - Added `import json` (stdlib, no new dep)
  - `_default_setup_uri`: changed return value from trailing-slash directory URI to manifest file URI ending in `/manifest.json`. Updated docstring to document the worker contract requirement.
  - `build_sfincs_model`: added manifest URI resolution/normalisation block before the temp-dir build (handles default, directory-override, and manifest-override paths). After HydroMT build + `model.write()`, enumerates `tmp/deck/**/*` files, computes each file's relative path from the deck root, constructs `gs_uri = deck_gcs_prefix + rel` (where `deck_gcs_prefix = deck_base_uri + "deck/"` to match fsspec upload behavior), writes `manifest.json` locally, uploads it to `manifest_uri` via `fs.upload`. `ModelSetup.setup_uri` is the manifest file URI.
  - Upload behavior note: `fs.upload(deck_dir, deck_base_uri, recursive=True)` uploads the `deck/` directory AS A CHILD of `deck_base_uri`, so files land at `deck_base_uri/deck/<relative>`. The manifest `gs_uri` values account for this.

- **File:** `services/agent/tests/test_model_flood_scenario.py`
  - Added Test 18 (`test_build_sfincs_model_emits_manifest_json_with_input_list`): asserts manifest.json is uploaded with correct `{inputs, sfincs_args, outputs}` shape; every input has `gs_uri` + `dest`; `sfincs.inp` and `dep.tif` appear in dest set; `gs_uri` values include the expected `deck/` prefix; `setup_uri` is the manifest file URI.
  - Added Test 19 (`test_build_sfincs_model_setup_uri_points_at_manifest_file`): 3-case test -- default path, directory-override path (old callers passing trailing `/`), manifest-override path (already ends in `/manifest.json`). All three must produce a `setup_uri` ending in `/manifest.json`.

- **File:** `reports/inflight/job-0057-engine-20260607/evidence/smoke_demo.py`
  - Copied from `reports/complete/job-0056-infra-20260607/evidence/smoke_demo.py` (no changes needed; same harness).

## Decisions Made

- **Decision: Use `deck_gcs_prefix = deck_base_uri + "deck/"` in manifest URIs.**
  - Rationale: fsspec's `upload(local_dir, remote_prefix, recursive=True)` uploads the source directory AS A CHILD of the target prefix, not its contents. Verified against actual GCS layout from run `01KTHRSQ66F90J32WPM4CC2WM3`. The manifest must reference the actual uploaded paths.
  - Alternatives considered: (a) uploading individual files to strip the `deck/` nesting -- rejected because it requires re-implementing recursive upload; (b) using a different target prefix to flatten -- rejected. The chosen approach documents the fsspec behavior in code and is self-consistent.

- **Decision: `dest` is the POSIX relative path from deck root (may include `gis/` subdir).**
  - Rationale: the worker does `dest = scratch / item["dest"]` with `parent.mkdir(parents=True)`, so relative paths with subdirectories work correctly. Confirmed: SFINCS ran successfully with this layout.

- **Decision: Outcome classified as PARTIAL SUCCESS (not SUCCESS).**
  - Rationale: SFINCS ran (exit=0) and produced `sfincs_map.nc` with real flood depth data. BUT `postprocess_flood` failed with `COG_WRITE_FAILED` -- a separate module, NOT this job's scope. The worker/manifest/URI chain is fully functional.

## Invariants Touched

- **Invariant 1 (Determinism boundary): preserves.** Manifest is generated deterministically from the set of HydroMT output files; no LLM in the path.
- **Invariant 2 (Deterministic workflows): preserves.** `build_sfincs_model` is still pure Python; manifest composition is deterministic given the deck contents.
- **Invariant 7 (no silent wrong answers): preserves.** The 404 was a silent-wrong-answer failure (SOLVER_FAILED with no indication why). The fix makes the wiring correct.

## Open Questions

- **OQ-58 (NEW): `postprocess_flood` COG write fails with shape inconsistency.**
  - Error: `COG_WRITE_FAILED: Source shape (1, 1, 527, 540) is inconsistent with given indexes 1`
  - The `sfincs_map.nc` `hmax` variable has shape `(1, 527, 540)` (timemax=1 dimension). The postprocess_flood COG writer appears to be treating a 3D array as a 2D raster and passing wrong band indexes. This is a `postprocess_flood` bug.
  - Impact: `AssessmentEnvelope.layers` is empty. SFINCS run itself is correct.
  - Proposed resolution: open a follow-up engine job to fix `postprocess_flood` to squeeze the timemax dimension before writing COG.
  - Tag: TENTATIVE -- continuation job needed.

## Dependencies and Impacts

- Depends on: job-0056 (APPROVED), job-0040 (APPROVED), job-0041 (APPROVED), job-0042 (APPROVED)
- Affects: `postprocess_flood` (engine specialist, follow-up job needed for OQ-58 before SUCCESS outcome)

## Verification

### Tests run

```
PYTHONPATH=services/agent/src:packages/contracts/src .venv-agent/bin/python -m pytest services/agent/tests/ -q
164 passed, 4 warnings in 3.00s
```

### Live E2E evidence

**Run ID:** `01KTHS9C5HH9N15SXQ0KEKKBGX`
**Manifest URI:** `gs://grace-2-hazard-prod-cache/cache/static-30d/sfincs_setup/01KTHS8B6JPC54PSWP2B2FYXKZ/manifest.json`
**Cloud Workflows execution:** `projects/425352658356/locations/us-central1/workflows/grace-2-sfincs-orchestrator/executions/3b6de5f4-51b6-4e3e-af3d-f3673b14d5f9`

**Cloud Run log sequence (key lines):**
```
INFO grace-2-sfincs-solver starting ... manifest=gs://.../01KTHS8B6JPC54PSWP2B2FYXKZ/manifest.json
INFO reading manifest gs://.../manifest.json        <- MANIFEST READ: SUCCESS (no 404)
INFO downloading gs://.../deck/sfincs.inp           <- ALL 11 INPUTS DOWNLOADED
INFO downloading gs://.../deck/sfincs.dep
INFO downloading gs://.../deck/sfincs.ind
INFO downloading gs://.../deck/sfincs.man
INFO downloading gs://.../deck/sfincs.msk
INFO downloading gs://.../deck/sfincs.precip
INFO downloading gs://.../deck/hydromt.log
INFO downloading gs://.../deck/gis/dep.tif
INFO downloading gs://.../deck/gis/manning.tif
INFO downloading gs://.../deck/gis/msk.tif
INFO downloading gs://.../deck/gis/region.geojson
INFO exec: /usr/local/bin/sfincs (cwd=/opt/grace2/work)   <- SFINCS STARTED
INFO sfincs exit=0 stdout_bytes=3193 stderr_bytes=0        <- SFINCS SUCCEEDED
INFO uploading .../sfincs_map.nc -> gs://grace-2-hazard-prod-runs/.../sfincs_map.nc
INFO wrote completion -> gs://.../completion.json
Container called exit(0).
```

**completion.json status=ok:**
```json
{
  "run_id": "01KTHS9C5HH9N15SXQ0KEKKBGX",
  "status": "ok",
  "exit_code": 0,
  "output_uris": ["gs://grace-2-hazard-prod-runs/01KTHS9C5HH9N15SXQ0KEKKBGX/sfincs_map.nc"],
  "started_at": "2026-06-07T19:37:08Z",
  "finished_at": "2026-06-07T19:41:59Z",
  "error": null
}
```

**sfincs_map.nc sample read:**
- Size: 40,188,207 bytes (~38 MB)
- Grid: 527x540 cells, CRS: EPSG:32617 (UTM Zone 17N -- correct for Fort Myers, FL)
- `hmax` (peak flood depth): shape (1, 527, 540), max=3.5152 m, mean=0.3377 m, 284,580 non-NaN cells
- `zsmax` (peak water surface elevation): max=29.58 m, mean=4.45 m
- `zb` (bed elevation): min=-0.28 m, max=29.57 m, mean=4.11 m
- SFINCS total_runtime: 287.5 s
- status: 0

**Outcome:** PARTIAL SUCCESS
- Worker reads manifest: FIXED
- Deck file downloads: FIXED
- SFINCS binary: SUCCESS (exit=0, real flood depths produced)
- sfincs_map.nc with non-zero flood depths: SUCCESS (GCS URI: `gs://grace-2-hazard-prod-runs/01KTHS9C5HH9N15SXQ0KEKKBGX/sfincs_map.nc`)
- postprocess_flood COG conversion: FAILS (COG_WRITE_FAILED, follow-up job needed)
- AssessmentEnvelope.layers: empty

**Evidence files:**
- `evidence/smoke_demo_log.txt` -- full stdout/stderr transcript
- `evidence/smoke_demo_envelope.json` -- AssessmentEnvelope summary JSON
- `evidence/smoke_demo.py` -- smoke harness

### Results: partial-success
