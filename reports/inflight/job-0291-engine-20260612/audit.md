# job-0291 — SFINCS flood solver on AWS (local-docker backend + S3 runs envelope) — FROZEN KICKOFF

**Specialist:** engine
**Sprint:** sprint-14-aws
**Model:** Fable (critical path — the flood demo centerpiece)
**Opened:** 2026-06-12
**Context:** the agent runs LIVE on AWS EC2 (`i-0251879a278df797f`, Bedrock provider, `GRACE2_STORAGE_BACKEND=s3`, cache bucket `grace2-hazard-cache-226996537797`). Flood sims currently die at `run_solver` (GCP Cloud Workflows dispatch, `DefaultCredentialsError`). Docker + the upstream `deltares/sfincs-cpu` image are being installed on the instance by the orchestrator in parallel.

## Architecture decision (binding)
NO custom solver container. The GCP container (`services/workers/sfincs/entrypoint.py`) is a thin GCS-IN → sfincs → GCS-OUT shim; on AWS that envelope moves INTO the agent (testable Python), and the container is the PLAIN upstream `deltares/sfincs-cpu` binary image run via `docker run` on the same instance. Production scale-up to AWS Batch is a later job — design the backend seam so Batch slots in.

## The contract to preserve (read these first)
- `services/workers/sfincs/entrypoint.py` docstring — manifest schema (`inputs[{gs_uri,dest}]`, `sfincs_args`, `outputs[]` globs) and `completion.json` schema (`run_id,status,exit_code,output_uris,started_at,finished_at,error`). `wait_for_completion` semantics depend on completion.json presence with status ok|error.
- `services/agent/src/grace2_agent/tools/solver.py` — `run_solver(solver, model_setup_uri) -> ExecutionHandle`, `wait_for_completion(handle) -> RunResult`; run_id = new_ulid; the Cloud Workflows argument is `{"run_id", "manifest_uri"}`; cancel chain (Invariant-8, ≤30s) currently calls workflows.executions.cancel.
- `services/agent/src/grace2_agent/workflows/model_flood_scenario.py` — Step 6 deck assembly ("in GCS"), Step 7 run_solver, output read at `run_result.output_uri or gs://…runs/<run_id>/`, then postprocess_flood.
- The job-0289 storage seam: `tools/cache.py` `storage_scheme()` / `GRACE2_STORAGE_BACKEND` / boto3-not-s3fs lesson (s3fs falls back to anonymous on the instance role — use boto3 for ALL S3 I/O).

## Scope

### 1. Solver backend seam (`tools/solver.py`)
`GRACE2_SOLVER_BACKEND` env: `gcp-workflows` (default —今日's behavior verbatim) | `local-docker`. Under `local-docker`:
- `run_solver`: mint run_id; download the manifest from S3 (boto3); download every `inputs[]` object into `/opt/grace2/runs/<run_id>/` (env `GRACE2_RUNS_DIR`, default that path; manifest uris may be `s3://` — accept `gs://` keys in the schema as legacy field name `gs_uri` but resolve by scheme); launch `docker run --rm -v <rundir>:/data -w /data <GRACE2_SFINCS_IMAGE default deltares/sfincs-cpu:latest> [sfincs_args]` as a DETACHED subprocess (Popen; container name = run_id for cancel); return ExecutionHandle immediately (non-blocking — mirror the Cloud Workflows submit semantics).
- A small supervisor (thread or asyncio task) waits on the process, expands `outputs[]` globs in the rundir, uploads them to `s3://<GRACE2_RUNS_BUCKET>/<run_id>/` (boto3), writes `completion.json` (same schema, `output_uris` as s3:// uris, stdout/stderr captured + uploaded), ALWAYS writes completion.json even on crash (status=error).
- `wait_for_completion`: under local-docker, poll the completion.json object on S3 (same cadence/timeouts as today) and build RunResult (`output_uri = s3://<runs>/<run_id>/`).
- Cancel chain: `docker kill <run_id>` + status=cancelled completion.json (≤30s, Invariant-8).
- `GRACE2_RUNS_BUCKET` env (no default to a GCP name; on AWS we'll set `grace2-hazard-runs-226996537797` — the orchestrator creates the bucket).

### 2. Deck assembly to S3 (`workflows/model_flood_scenario.py` + wherever the deck uploader lives)
Find the deck/manifest upload path (Step 6, "assembles the HydroMT-SFINCS deck in GCS") and route it through the storage scheme: `s3://` + boto3 when `GRACE2_STORAGE_BACKEND=s3`, unchanged gs:// otherwise. The manifest's input uris must match the scheme so the local-docker staging can download them.

### 3. postprocess_flood read path
It downloads `sfincs_map.nc` (and friends) from the runs prefix — make the download scheme-aware (boto3 for s3://). Same for any other run-output reads in the composer.

### 4. Tests (vitest-equivalent pytest)
- Backend seam: default env → Cloud Workflows path byte-identical (existing tests stay green); local-docker run_solver with a FAKE docker binary (PATH shim) + fake S3 (moto or boto3-stub or a tmpdir-backed fake client seam) → manifest staged, process launched, completion.json written ok + error + cancel paths; wait_for_completion happy/timeout/error.
- Scheme-aware manifest staging (gs_uri field carrying s3:// value).
- Full agent suite: only the pre-existing sanctioned failures (NOTE: the 2 flood-scenario GCS failures touch these files — report exact before/after text; do not mask).

## Hard constraints
- NO Gemini/Vertex calls. NO docker on THIS dev machine (daemon blocked — all docker runs happen on the EC2 instance, which the ORCHESTRATOR drives; your tests fake the docker binary). Do NOT touch the running dev agent or the EC2 instance yourself.
- boto3 for all S3 I/O (s3fs is broken on the instance role).
- GCP paths stay byte-identical under the default backend.
- `git add` only files you touched; commit `job-0291: ...` + `Co-Authored-By: Claude Fable 5 <noreply@anthropic.com>`.

## Deliverables
`reports/inflight/job-0291-engine-20260612/{report.md,STATE=IN_REVIEW}` (if report write is harness-blocked, return the full report content in your final message); suite counts; the orchestrator deploys to the instance and runs the LIVE Fort Myers flood verification.
