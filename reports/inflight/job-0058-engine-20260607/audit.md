# Audit: postprocess_flood squeeze singleton timemax dim before COG write (OQ-58)

**Job ID:** job-0058-engine-20260607, **Sprint:** sprint-08 (mid-sprint follow-up #6 — orthogonal to M5 SUCCESS), **Auditor:** Development Orchestrator, **Status:** assigned

## Task Assignment

**Specialist:** engine

**Prerequisites:**
- **job-0057 (APPROVED):** M5 SUCCESS — SFINCS produced `sfincs_map.nc` with hmax 3.52 m. Surfaced OQ-58 — `postprocess_flood` fails with `COG_WRITE_FAILED: Source shape (1,1,527,540) is inconsistent with given indexes 1` because hmax has shape `(timemax=1, n=527, m=540)` and the COG writer expects 2D.

**SRS references** (narrow file loading only):
- `docs/srs/03-functional-requirements.md` (FR-CE-8 cache shim; postprocess emits the COG that goes into AssessmentEnvelope.layer_uris)
- DO NOT load `docs/SRS_v0.3.md` monolith.

**Required reads:**
- `services/agent/src/grace2_agent/workflows/postprocess_flood.py` lines 170-244 — the bug site

### Why this job exists

`postprocess_flood` reads SFINCS's `hmax` variable from `sfincs_map.nc`. HydroMT-SFINCS 1.2.2 emits `hmax` with shape `(timemax=1, n=527, m=540)` — a singleton timemax dim. The COG writer at line 238 does `dst.write(arr_masked.astype("float32"), 1)` which expects a 2D array when band index is provided as int. Result: `Source shape (1, 1, 527, 540) is inconsistent with given indexes 1`.

### Scope

1. **`services/agent/src/grace2_agent/workflows/postprocess_flood.py`**: squeeze the singleton timemax dim. Single-line fix at the depth-extraction step. Apply to all three branches (hmax, zsmax/zb, zs/zb) so the contract is uniform: depth is always 2D `(n, m)` after extraction.

   Suggested implementation at line 191 (current):
   ```python
   arr = np.asarray(depth.values, dtype="float32")
   if arr.ndim > 2:
       arr = np.squeeze(arr)
       if arr.ndim != 2:
           raise PostprocessError(
               "RUN_OUTPUT_UNEXPECTED_SHAPE",
               message=f"depth array has shape {arr.shape}; expected 2D after squeeze",
               details={"netcdf_path": str(netcdf_path)},
           )
   ```

2. **Tests** in `services/agent/tests/test_postprocess_flood.py` (or wherever postprocess tests live; check first): add ≥1 test that constructs a fake `sfincs_map.nc`-like dataset with hmax shape `(1, 8, 8)` and asserts the COG write succeeds with a 2D raster.

3. **Re-run M5 smoke** — use `reports/complete/job-0057-engine-20260607/evidence/smoke_demo.py` as the harness. Copy to `reports/inflight/job-0058-engine-20260607/evidence/`. Now the chain should:
   - Run through all the steps job-0057 cleared
   - SFINCS solver dispatch + completion (as in job-0057)
   - postprocess_flood succeeds → COG uploaded to runs bucket
   - AssessmentEnvelope returned with `outcome="SUCCESS"`, non-empty `layer_uris`, populated `flood_max_depth_m`

4. **Capture comprehensive evidence**: GCS URI of the COG; sample read confirming it opens cleanly with rasterio; AssessmentEnvelope JSON with layer_uris populated; full smoke log. **"SCREENSHOT MOMENT" call-out** so the orchestrator surfaces the rasterized flood map (the production COG path, not the orchestrator-direct PNG hack from job-0057).

### File ownership (exclusive)
- `services/agent/src/grace2_agent/workflows/postprocess_flood.py` — the squeeze fix only
- `services/agent/tests/test_postprocess_flood.py` (if exists; otherwise new) — additive tests
- `reports/inflight/job-0058-engine-20260607/`

### FROZEN
- `services/workers/sfincs/entrypoint.py` — worker contract; don't touch
- `services/agent/src/grace2_agent/workflows/sfincs_builder.py` — just-edited by job-0057
- `services/agent/src/grace2_agent/workflows/manning_mapping.csv`
- `services/agent/pyproject.toml`
- All other workflows/* and tools/* files
- packages/contracts/**, infra/**, web/**, docs/srs/**, services/workers/**, reports/complete/**

### Acceptance criteria
- [ ] postprocess_flood handles `hmax` with shape `(timemax=1, n, m)` cleanly
- [ ] ≥1 regression test guards the squeeze
- [ ] M5 chain re-run produces AssessmentEnvelope with `outcome="SUCCESS"` + non-empty layer_uris + populated flood_max_depth_m
- [ ] COG GCS URI captured + rasterio.open verified live
- [ ] No edits to FROZEN paths
- [ ] Single commit
