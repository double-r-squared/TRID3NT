# job-0293b — complete the S3 port: Pelicun + clips + analytical/chart/extract tools — FROZEN KICKOFF

**Specialist:** engine
**Sprint:** sprint-14-aws
**Model:** Fable
**Opened:** 2026-06-12
**Context:** the live AWS tool sweep (12/15 PASS) + an exhaustive grep found the remaining gs://-only modules. The S3 staging pattern is established (job-0290b: shared `tools/cache.py::read_object_bytes_s3` boto3 reader; one-line scheme branch before each gs:// gate; scheme-aware uploads via boto3 mirroring `_read_through_s3` / postprocess_flood's `_upload_cog_to_runs_bucket`).

## Files to port (grep-verified zero s3 refs unless noted)
1. **`tools/run_pelicun_damage_assessment.py`** (HIGHEST impact — confirmed FAIL on AWS): `_download_uri_to_local` (~:587), the `if not uri.startswith("gs://")` reject (~:630), `*_was_remote = uri.startswith("gs://")` checks (~:1057-1058). Port download to scheme-aware (s3 via the shared reader → temp file); remote-detection = startswith(("gs://","s3://")); any result/artifact UPLOADS in the module → scheme-aware boto3.
2. **`tools/postprocess_pelicun.py`** (2 gates): same treatment — reads of pelicun outputs + any uploads.
3. **`tools/clip_raster_to_bbox.py`** `_get_source_crs` (~:185): the CRS-detect open handles gs:// (`/vsigs/`) + local but not s3:// → raises UNKNOWN_RASTER_URI before the (already-ported) download helper. Add the s3 branch — `/vsis3/` rasterio open (instance role creds work with GDAL on EC2) or stage-then-open via the shared reader; match whichever the module's existing style favors. Check `clip_raster_to_polygon.py` for the same CRS-detect pattern (it has 5 gates / 4 s3 refs — verify nothing slipped).
4. **`tools/clip_vector_to_polygon.py`** (1 gate, 0 s3): download helper port.
5. **`tools/extract_landcover_class.py`** (4 gates, 0 s3): download + any vsigs paths + result upload.
6. **`tools/analytical_qa.py`** (2 gates, 0 s3): reads of layer artifacts for Q&A — scheme-aware.
7. **`tools/chart_tools.py`** (2 gates, 0 s3): server-side data reads for chart specs — scheme-aware.
8. **`tools/compute_colored_relief.py`** (4 gates, 2 s3 refs): verify the remaining gates (likely /vsigs or upload paths) — port any that can carry s3 on AWS.
9. Sweep check: re-run the grep (`startswith("gs://")` / `/vsigs/` per tools/+workflows/ file vs s3 refs) at the end and list any module you deliberately left (e.g. GCP-deploy-only paths like publish_layer's worker branch, sfincs_builder's gs branch) with one-line justification each.

## Pattern rules (from jobs 0289/0290b/0292b — follow exactly)
- boto3 ONLY for S3 (s3fs falls back to anonymous on the EC2 instance role). Reuse `tools/cache.py::read_object_bytes_s3` and the `_get_s3_client()` seam in tools/solver.py where an injectable client matters for tests.
- gs:// branches stay byte-identical (GCP path must not move).
- Per-helper shape: insert the s3 branch BEFORE the gs:// gate; preserve each helper's return shape (bytes / local path / tuple).
- Typed errors preserved: wrap s3 failures in each module's existing error class with the existing error_code where one exists.

## Tests
- Mirror job-0290b's: per-module, drive each patched helper with a fake/boto3-shaped client or moto-free dict seam + temp files; pelicun gets an end-to-end-shaped test with s3 hazard+asset URIs through `_download_uri_to_local` (fake client) proving the local staging + was_remote flags.
- Full agent suite: only the pre-existing sanctioned failures (3x test_data_fetch docstring-tier, 2x test_model_flood_scenario); report exact text for any failure in files you touch.

## Hard constraints
NO Gemini/Vertex/Bedrock calls; NO docker; do NOT touch the EC2 instance (orchestrator deploys + re-runs the live probes); never `git add -A`; commit `job-0293b: ...` + `Co-Authored-By: Claude Fable 5 <noreply@anthropic.com>`.

## Deliverables
report.md + STATE=IN_REVIEW (return full report in final message if the write is blocked); one commit. Final message: per-file change table (file:line), the end-of-job grep result with justifications, suite counts, commit hash.
