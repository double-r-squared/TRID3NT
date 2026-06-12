# Report: SFINCS local-docker solver backend + S3 runs envelope (sprint-14-aws)

**Job ID:** job-0291-engine-20260612
**Sprint:** sprint-14-aws
**Specialist:** engine (Fable)
**Commit:** `10a8a00` (7 files, +1901/−44)
**Status:** IN_REVIEW (orchestrator runs the live Fort Myers verify on the EC2 instance)

*(Placed by the orchestrator from the runner's returned content — the runner's direct report.md write was harness-blocked; content verbatim.)*

## Backend seam design (file:line)
- `services/agent/src/grace2_agent/tools/solver.py:276` — `solver_backend()`: `GRACE2_SOLVER_BACKEND=local-docker` → local; unset/unknown → `gcp-workflows` (read at call time, mirrors `cache.storage_scheme()`).
- `solver.py:1156` — `run_solver` dispatches to `_run_solver_local_docker` **before** the gs:// check; the GCP path below is byte-identical (all 21 pre-existing solver tests untouched-green).
- `solver.py:1457` — `wait_for_completion` dispatches on `handle.workflow_name == "local-docker"` (handle pins its backend; env churn can't mis-route a poll).
- `solver.py:741` — `_run_solver_local_docker`: mint ULID → manifest read by scheme (`_read_object_bytes`: s3=boto3, gs=GCS seam, file://) → stage `inputs[]` into `$GRACE2_RUNS_DIR/<run_id>/` (legacy `gs_uri` field NAME, VALUE scheme-resolved; dest path-traversal guard) → detached `Popen(["docker","run","--rm","--name",run_id,"-v",f"{rundir}:/data","-w","/data",image,*sfincs_args], start_new_session=True)` → returns `ExecutionHandle{workflow_name="local-docker", workflows_execution_id="local-docker:<run_id>"}` immediately. Container name == run_id is the Invariant-8 seam.
- `solver.py:633` — supervisor daemon thread: `proc.wait()` → uploads stdout/stderr + glob-expanded `outputs[]` to `s3://$GRACE2_RUNS_BUCKET/<run_id>/` (boto3 put_object) → **ALWAYS** writes `completion.json` with the EXACT entrypoint.py key set `{run_id,status,exit_code,sfincs_stdout_uri,sfincs_stderr_uri,output_uris,started_at,finished_at,error}` — even on crash (`error`) or cancel (`cancelled`).
- `solver.py:929` — `_wait_for_completion_local`: polls completion.json on S3, same cadence/timeout defaults, deadline off `submitted_at`, same progress ramp/emitter; ok → `RunResult{complete, output_uri="s3://<runs>/<run_id>/"}` (prefix, kickoff-pinned); timeout → `docker kill` + `SOLVER_TIMEOUT`.
- Cancel chain (`solver.py:879` `_request_local_cancel`): `CancelledError` in poll-sleep → set cancelled flag + `docker kill <run_id>` (25s budget) → supervisor writes `status="cancelled"` completion → re-raise. Tested terminal well inside 30s.
- DI seam `set_s3_client()`/`_get_s3_client()` (boto3, NOT s3fs — job-0289 lesson) shared by sfincs_builder + postprocess_flood; `_get_local_runs_bucket()` (solver.py:474) requires `GRACE2_RUNS_BUCKET` — no GCP-named default, missing → loud `SolverDispatchError`.

## Staged-manifest / completion flow (scheme-aware items 2+3)
- `sfincs_builder.py`: `_default_setup_uri` emits `s3://$GRACE2_CACHE_BUCKET/cache/static-30d/sfincs_setup/<ulid>/manifest.json` under `GRACE2_STORAGE_BACKEND=s3`; deck+manifest upload via boto3 preserving the `deck/` sub-prefix layout (gs:// fsspec branch byte-identical); `_stage_gcs_local` stages s3:// DEM/landcover for HydroMT via the boto3 seam; `_to_vsigs` maps `s3://`→`/vsis3/`.
- `postprocess_flood.py`: `_resolve_run_output_to_local` downloads `sfincs_map.nc` from s3:// prefixes via boto3 (typed `RUN_OUTPUT_READ_FAILED`); `_upload_cog_to_runs_bucket` scheme-aware (s3 → boto3, no GCP bucket default → typed `COG_UPLOAD_FAILED`). Kickoff §3 named the read path; the COG upload is the same run-output seam and required for live AWS E2E — surfaced as a decision.
- `model_flood_scenario.py`: Step-8 fallback prefix via `_default_runs_prefix(run_id)`; Step-9 publish gate extended to `s3://` COGs (publish-or-honest-drop per job-0254 §1 — raw object-store URIs never reach the map; flagged **OQ-291-PUBLISH-LAYER-AWS**: until job-0290 lands tiles-on-AWS, a live Fort Myers run yields full envelope+metrics with the depth layer honestly dropped).

## Env matrix
| Env | Default | Meaning |
|---|---|---|
| `GRACE2_SOLVER_BACKEND` | `gcp-workflows` | `local-docker` enables the AWS path |
| `GRACE2_RUNS_BUCKET` | REQUIRED under local-docker/s3 | `grace2-hazard-runs-226996537797` on AWS |
| `GRACE2_RUNS_DIR` | `/opt/grace2/runs` | rundir = `<dir>/<run_id>`, mounted at `/data` |
| `GRACE2_SFINCS_IMAGE` | `deltares/sfincs-cpu:latest` | plain upstream image, no custom container |
| `GRACE2_STORAGE_BACKEND` | `gcs` | `s3` flips deck assembly + run-output I/O |
| `AWS_REGION` | `us-west-2` | boto3 client region |

## Suite counts
- BEFORE: `5 failed, 4414 passed, 72 skipped, 1 xfailed`; AFTER: `5 failed, 4434 passed, 72 skipped, 1 xfailed` — identical 5 sanctioned failures; +20 = `tests/test_solver_local_docker.py` (PATH-shim fake docker + dict-backed boto3-shaped fake S3; staging/argv/handle, completion schema ok+error+cancel, wait happy/timeout/error, Invariant-8 cancel <30s, legacy gs://-value resolution, traversal guard, deck-upload-via-boto3 with fsspec booby-trapped, postprocess s3 read/upload).
- The 2 sanctioned flood-GCS failures: EXACT text identical before and after (root cause both: publish_layer LAYER_URI_NOT_FOUND on the GCP runs bucket in the test env → honest drop → dict).

## E2E evidence (fake-docker shim; orchestrator runs the real live verify)
Full local-docker cycle through the REAL code paths with real NetCDF + rasterio COG: run_solver → handle, wait_for_completion → complete with s3:// prefix, completion.json status=ok, postprocess_flood → LayerURI + FloodMetrics (max 2.400m / mean 1.250m / p95 2.321m / 600 cells / EPSG:32617). `E2E LOCAL-DOCKER CYCLE: PASS`.

## Open Questions
1. **OQ-291-LOCAL-CANCEL-CROSS-PROCESS** — cancelled-completion relies on the in-process supervisor; agent restart mid-run leaves only docker kill. Accepted v0.1 single-instance; AWS Batch owns durable run state later.
2. **OQ-291-S3-PUT-SINGLE** — single put_object (≤5GB), not multipart; revisit at Batch.
3. **OQ-291-PUBLISH-LAYER-AWS** — s3:// COGs honest-drop until job-0290 tile serving; one-line revert available for raw emission interim.
4. Pre-existing uncommitted Wave-4.10 `@register_tool` annotation hunks in solver.py rode along in the file-level commit; no other foreign hunks.

**Affects:** orchestrator/infra (instance docker + image + env + runs bucket — DONE by orchestrator); job-0290 (COG tile publish on AWS); MODFLOW paths remain GCS-only by scope.
