# Report: complete the S3 port — Pelicun + clips + analytical/chart/extract tools

**Job ID:** job-0293b-engine-20260612
**Sprint:** sprint-14-aws
**Specialist:** engine
**Task:** complete the S3 port: Pelicun + clips + analytical/chart/extract tools (port the remaining gs://-only modules to scheme-aware s3:// via the job-0290b pattern; end-of-job grep sweep with justifications)
**Status:** ready-for-audit

## Summary

Ported the last eight gs://-only modules to scheme-aware S3 via the established job-0290b pattern (shared `tools/cache.py::read_object_bytes_s3` boto3 reader; s3 branch inserted before each gs:// gate; gs:// branches byte-identical; typed errors preserved per module). The highest-impact fix is Pelicun (`_download_uri_to_local` + the gs-only reject + the `was_remote` unlink flags + the WMS reverse-map now scheme-aware), which was a confirmed live-sweep FAIL on AWS. 18 new unit tests drive every patched helper with a fake reader bound over the shared seam; the full agent suite shows only the 5 pre-existing sanctioned failures.

## Changes Made

- File: `services/agent/src/grace2_agent/tools/run_pelicun_damage_assessment.py`
  - `_download_uri_to_local` (~:636): s3:// branch before the gs:// gate — shared boto3 reader → NamedTemporaryFile (caller unlinks), with the job-0253 last-two-segment path-mangle retry mirrored for s3 and failures wrapped in `PelicunRuntimeError` ("S3 download failed ...").
  - WMS reverse-map (~:605-622): the reconstructed runs-bucket COG URI now uses `cache.storage_scheme()` — gs:// on GCP (default, byte-identical output), s3:// under `GRACE2_STORAGE_BACKEND=s3`.
  - `_fetch_pelicun_damage_bytes` (~:1098-1099): `hazard_was_remote` / `assets_was_remote` = `startswith(("gs://", "s3://"))` so s3-staged temp files are unlinked in the finally block.
  - Result upload unchanged: routed through `read_through` (scheme-aware since job-0289) — no direct upload code in the module.
- File: `services/agent/src/grace2_agent/tools/postprocess_pelicun.py`
  - `_download_uri_to_local` (~:225): s3:// staging branch before the gs:// gate; failures wrapped in `PelicunPostprocessIOError`.
  - `postprocess_pelicun` (~:761): `was_remote = startswith(("gs://", "s3://"))`.
  - No uploads in the module (returns an `ImpactEnvelope` dict).
- File: `services/agent/src/grace2_agent/tools/clip_raster_to_bbox.py`
  - `_get_source_crs` (~:185-191): s3:// header-read branch via GDAL `/vsis3/` (mirrors the module's `/vsigs/` style; EC2 instance-role creds resolve through GDAL's AWS chain). This was the live-sweep failure: the CRS-detect raised UNKNOWN_RASTER_URI before the (already-ported) download helper ever ran.
- File: `services/agent/src/grace2_agent/tools/clip_raster_to_polygon.py`
  - `_get_source_crs` (~:129-135): same `/vsis3/` branch — the kickoff's "verify nothing slipped" check found this 5th gate had the identical gap; the module's other helpers were already ported (job-0290b).
- File: `services/agent/src/grace2_agent/tools/clip_vector_to_polygon.py`
  - `_resolve_layer_to_local_path` (~:130): s3:// branch before the gs:// gate — shared reader → temp file, preserving the `(path, is_temp=True)` tuple shape; failures wrapped in `ClipVectorError("DOWNLOAD_FAILED")`.
- File: `services/agent/src/grace2_agent/tools/extract_landcover_class.py`
  - `_open_source` (~:208-213): s3:// → `/vsis3/` branch (window reads stream only the requested bytes, same as the gs:// `/vsigs/` path). Result upload already via `read_through`.
- File: `services/agent/src/grace2_agent/tools/analytical_qa.py`
  - `_download_uri_bytes` (~:115): s3:// branch via shared reader; failures wrapped in `AnalyticalQAError("DOWNLOAD_FAILED")`.
  - `_materialize_uri` (~:170): s3:// URIs are now materialized into the tmpdir (previously the `not gs://` gate returned the raw s3:// string as a "local path").
- File: `services/agent/src/grace2_agent/tools/chart_tools.py`
  - `_download_uri_bytes` (~:205): s3:// branch; failures wrapped in `ChartToolError("DOWNLOAD_FAILED", retryable=True)` (parity with the module's gs error).
  - `_materialize_uri` (~:255): same materialize fix as analytical_qa.
- File: `services/agent/src/grace2_agent/tools/compute_colored_relief.py`
  - VERIFIED, no change needed: `_download_dem_to_local` already has the job-0290b s3 staging branch; the result write goes through scheme-aware `read_through`; remaining grep hits are the byte-identical gs branch and comments (job-0269/0271 history).
- File: `services/agent/tests/test_s3_port_job0293b.py` (new)
  - 18 tests driving every patched helper with a fake reader bound over `grace2_agent.tools.cache.read_object_bytes_s3` (the single seam all per-tool s3 branches import lazily) + temp files. Includes the kickoff-required Pelicun end-to-end-shaped test (s3 hazard + asset URIs through `_download_uri_to_local`, staged-file contents asserted inside the assessment call, `was_remote` proven by post-call unlink), the s3 path-mangle retry, the WMS reverse-map under both schemes (gs default proven unchanged with an injected fake storage client), `/vsis3/` CRS-detect for both clip modules, gs `/vsigs/` regression checks, and typed-error wraps for every module.

## End-of-job grep sweep

`startswith("gs://") | startswith(("gs://" | /vsigs/` vs s3 refs per tools/+workflows/ file (files with ≥1 gs gate):

| File | gs gates | s3 refs | Status |
|---|---|---|---|
| tools/analytical_qa.py | 2 | 6 | ported (this job) — both gates have s3 siblings |
| tools/chart_tools.py | 2 | 6 | ported (this job) |
| tools/clip_raster_to_bbox.py | 6 | 8 | ported (0290b + this job) — remaining hits are gs branches/docstrings |
| tools/clip_raster_to_polygon.py | 6 | 12 | ported (0290b + this job) |
| tools/clip_vector_to_polygon.py | 1 | 4 | ported (this job) |
| tools/compute_aspect.py | 1 | 4 | ported (job-0290b) — verified s3 sibling present |
| tools/compute_colored_relief.py | 4 | 4 | ported (0290b); hits = gs branch + comments; result via scheme-aware read_through |
| tools/compute_hillshade.py | 2 | 4 | ported (job-0290b) — verified |
| tools/compute_impervious_surface.py | 1 | 4 | ported (job-0290b) — verified |
| tools/compute_slope.py | 1 | 4 | ported (job-0290b) — verified |
| tools/compute_zonal_statistics.py | 2 | 8 | ported (job-0290b) — verified both helpers |
| tools/extract_landcover_class.py | 5 | 3 | ported (this job); remaining hits = gs branch + docstrings |
| tools/postprocess_pelicun.py | 2 | 6 | ported (this job) |
| tools/publish_layer.py | 15 | 6 | DELIBERATELY GCP-ONLY: all gs gates live in the PyQGIS-worker publish path (QGIS Server + Cloud Run worker exist only on the GCP deploy); the AWS branch (job-0291b, `storage_scheme()=="s3"` at :859) fails fast + honest / takes the s3 path before any gs gate is reached |
| tools/solver.py | 4 | 31 | DELIBERATELY GCP-ONLY at :1415: that gate is inside the GCP Cloud-Workflows dispatch path, reached only when `solver_backend() != local-docker`; the AWS local-docker backend's gate at :922 is scheme-aware (s3/gs/file); :564/:582/:923 are already scheme-aware checks |
| workflows/model_flood_scenario.py | 3 | 6 | scheme-aware: precip read uses sfincs_builder's `_to_vsigs` (s3→/vsis3/ since job-0291); runs-prefix helper at :264 is explicitly scheme-aware (job-0291; gs literal is the documented GCP default); :813 checks both schemes |
| workflows/postprocess_flood.py | 1 | 12 | ported (job-0291): s3 branch at :124 precedes the gs branch at :148; upload seam scheme-aware |
| workflows/postprocess_modflow.py | 2 | 15 | ported (job-0292b): s3 read branch precedes the gs branch at :178; plume-COG upload scheme-aware (boto3 s3 branch before the gs/fsspec branch at :479) |
| workflows/sfincs_builder.py | 25 | 21 | scheme-aware: `_to_vsigs` maps gs→/vsigs/ AND s3→/vsis3/ (job-0291); :481/:544 gates check both schemes; remaining hits are docstrings/comments and gs siblings |

Also swept alternate idioms: no single-quoted 'gs://' gates; the only direct GCS-upload calls (`upload_from_*`) live in tools/cache.py's gs branch (s3 sibling `_read_through_s3` exists, job-0289); all `f"gs://..."` constructors are either inside gs branches of scheme-aware code or GCP-deploy-only (publish_layer worker path; run_modflow_tool.py:234 is a GCP-parity fallback used only when the solver returns no `output_uri` — the AWS local backend always sets it, live-verified job-0292b).

## Decisions Made

- Decision: WMS reverse-map in Pelicun made scheme-aware via `storage_scheme()`.
  - Rationale: on AWS the reverse-mapped runs-bucket COG lives at s3://; a hard-coded gs:// would re-introduce the exact live-sweep failure through the WMS-URL path (the common case per job-0255). Default env still yields gs:// — byte-identical on GCP, unit-tested with a fake storage client.
  - Alternatives considered: leave gs:// hard-coded (fails on AWS whenever the LLM passes the WMS GetMap URL).
- Decision: mirrored the job-0253 last-two-segment path-mangle retry in the Pelicun s3 branch.
  - Rationale: the guarded failure mode is LLM path reconstruction, which is scheme-independent; same typed error, ~10 lines.
  - Alternatives considered: plain single-shot read (kickoff minimum); rejected — behavior parity across schemes is cheap and the failure class is field-observed.
- Decision: `/vsis3/` header-reads (not stage-then-open) for the two `_get_source_crs` and `extract_landcover_class._open_source`.
  - Rationale: kickoff offered both; the modules' existing style is the `/vsigs/` virtual-filesystem header/window read (avoids full-file download of nationwide COGs), and GDAL resolves EC2 instance-role creds.
- Decision: s3 failures wrapped in each module's existing typed error class with existing codes (chart_tools keeps retryable=True parity).
  - Rationale: kickoff pattern rule; NFR-R-1. Note job-0290b's branches let reader exceptions propagate raw; this job follows the 0293b wrap rule for the NEW branches and did not retrofit 0290b's (gs/s3 0290b branches untouched).

## Invariants Touched

- Determinism boundary: preserves — staging/CRS-detect plumbing only; no metric paths changed.
- Deterministic workflows: preserves — zero LLM calls anywhere in the diff.
- Engine registration, not modification: preserves — no agent-core changes.
- Rendering through QGIS Server: preserves — publish path untouched.
- Metadata-payload pattern: preserves — reads keyed by URI; no bucket enumeration introduced.
- Typed errors (NFR-R-1): extends — every new s3 failure path surfaces as the module's existing typed error class/code.

## Open Questions

- Pre-existing temp-file leak (not introduced here, observed while porting): when Pelicun's hazard URI is a WMS GetMap URL (job-0255 reverse-map), the staged temp file is never unlinked because `hazard_was_remote` is computed from the original http(s) URI. Affects GCP and AWS equally; suggest a follow-up keying cleanup on `hazard_local != hazard_raster_uri`. TENTATIVE: left as-is — outside this kickoff's named bug class.
- Repo hygiene, surfaced for the orchestrator: `tools/postprocess_pelicun.py` (and its test file) were UNTRACKED in git — the Wave-4.11 P2/P3 work was never committed. This job's commit necessarily includes the whole file (785 lines), not just the 2 ported hunks. Similarly, `clip_vector_to_polygon.py` / `extract_landcover_class.py` carried unrelated uncommitted hunks (docstring restructure + register_tool annotations from another in-flight job); this job staged ONLY its own hunks for those two files (selective `git apply --cached`), leaving the foreign hunks uncommitted in the working tree.

## Dependencies and Impacts

- Depends on: job-0289 (shared S3 read-through + boto3-not-s3fs lesson), job-0290b (per-helper s3-branch pattern + shared reader), job-0291/0291b (solver local-docker seam, publish_layer AWS gate), job-0292b (MODFLOW port), job-0253/0255 (Pelicun URI guards mirrored for s3).
- Affects: orchestrator — deploy to the EC2 instance and re-run the live AWS tool sweep (Pelicun + clip_raster_to_bbox were the confirmed FAILs); this job did not touch the instance per the hard constraint.

## Verification

- Tests run:
  - tests/test_s3_port_job0293b.py — 18 passed (new).
  - Targeted modules (run_pelicun, postprocess_pelicun, clip_raster_to_bbox, clip_raster_to_polygon, clip_vector_to_polygon, extract_landcover_class, analytical_qa, chart_tools, compute_colored_relief + new file) — 164 passed, 8 skipped.
  - Full suite BEFORE: 5 failed, 4451 passed, 72 skipped, 1 xfailed (285.40s) — the 5 are exactly the sanctioned set (3x test_data_fetch docstring-tier: test_fetch_landcover_docstring_records_access_tier, test_fetch_river_geometry_docstring_records_tier_4, test_lookup_precip_return_period_docstring_records_tier_3; 2x test_model_flood_scenario: test_run_model_flood_scenario_returns_layer_uri, test_run_model_flood_scenario_triggers_loaded_layers_emit).
  - Full suite AFTER: 5 failed, 4469 passed, 72 skipped, 1 xfailed (328.48s) — identical 5 sanctioned failures; +18 = the new file.
- Live E2E evidence: qualified — the kickoff hard-constrains this job from touching the EC2 instance ("orchestrator deploys + re-runs the live probes"), so live AWS verification is explicitly the orchestrator's follow-up. The unit evidence drives every patched s3 branch through the same shared seam the live path uses; no Gemini/Vertex/Bedrock calls were made.
- Results: pass (unit + suite) / qualified (live, per kickoff constraint).
