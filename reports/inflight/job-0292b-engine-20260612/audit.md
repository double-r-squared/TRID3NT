# job-0292b — MODFLOW groundwater solver on AWS (mirror job-0291) — FROZEN KICKOFF

**Specialist:** engine
**Sprint:** sprint-14-aws
**Model:** Fable
**Opened:** 2026-06-12
**Context:** SFINCS runs on AWS via the job-0291 local-docker backend (`GRACE2_SOLVER_BACKEND=local-docker`, deck staged from S3, `docker run`, completion.json → S3). MODFLOW (`run_modflow_job` + `run_model_groundwater_contamination_scenario`) still dispatches a GCP Cloud Run Job → fails on AWS with credential errors. The user wants groundwater working on the AWS deployment.

## Read first
1. reports/inflight/job-0291-engine-20260612/{audit.md,report.md} — the pattern you are MIRRORING (backend seam, boto3-only S3, completion.json contract, PATH-shim fake docker tests, env matrix).
2. `services/agent/src/grace2_agent/tools/solver.py` — the job-0291 local-docker machinery (reuse it; do not fork a parallel implementation).
3. `services/workers/modflow/` — the GCP MODFLOW worker entrypoint contract (manifest/completion shapes).
4. The MODFLOW dispatch path: `run_modflow_job` tool + the groundwater composer (`run_model_groundwater_contamination_scenario`) — where the deck is built/uploaded and outputs are read.
5. `tools/cache.py` `storage_scheme()` / `read_object_bytes_s3` (boto3-not-s3fs lesson).

## Scope
1. Route MODFLOW dispatch through the SAME `GRACE2_SOLVER_BACKEND=local-docker` seam: stage deck from S3, run the solver container detached (name=run_id, Invariant-8 cancel), supervisor uploads outputs + completion.json to `s3://$GRACE2_RUNS_BUCKET/<run_id>/`. Reuse job-0291's helpers — extend, don't duplicate.
2. **Solver binary decision (yours, document it):** there is no GRACE-built MODFLOW image on AWS. Options: (a) a public mf6 docker image you can pin; (b) image-less "local-exec" variant of the seam running the `mf6` binary directly (flopy's `get-modflow` can install pinned executables to the instance — the orchestrator can run that via SSM if you specify the exact command in your report); (c) reuse the GCP Dockerfile's base. Pick the simplest path that preserves the completion contract; flag the orchestrator's instance-prep step explicitly in your final message (it deploys, you don't).
3. Deck assembly + output reads scheme-aware (s3 via boto3), mirroring sfincs_builder/postprocess_flood changes — including the groundwater composer's plume postprocess path (NOTE job-0254's PlumeLayerURI gap: the plume layer type bypasses the LayerURI seam; if the plume raster publish path needs the TiTiler template treatment for AWS rendering, wire it the same way publish_layer does — `GRACE2_TILE_SERVER_BASE` — or flag it honestly).
4. Tests: same shape as job-0291's (PATH-shim docker or exec-shim, fake S3 seam, completion ok/error/cancel, default GCP path byte-identical). Full agent suite: only the pre-existing sanctioned failures.

## Hard constraints
- NO Gemini/Vertex/Bedrock calls; NO docker on this machine; do NOT touch the EC2 instance (orchestrator deploys); boto3 for all S3 I/O; GCP default path byte-identical; never `git add -A`; commit `job-0292b: ...` + `Co-Authored-By: Claude Fable 5 <noreply@anthropic.com>`.

## Deliverables
report.md + STATE=IN_REVIEW in your job dir (harness may block the write — if so, return full report in your final message); one commit. Final message: seam design (file:line), the solver-binary decision + EXACT instance-prep commands for the orchestrator, env matrix additions, suite counts, commit hash.
