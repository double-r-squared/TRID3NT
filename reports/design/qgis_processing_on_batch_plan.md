# QGIS Processing on AWS Batch -- migration plan (job-0308, Batch variant)

Status: **DESIGN, awaiting NATE go.** Moves QGIS Processing OFF the downscaled agent box onto AWS Batch Spot (scale-to-zero), reusing the solver substrate. The guided digitizer + AI canopy detector converge onto this substrate (both already exist as agent-driven artifacts -- nothing built from scratch).

## Architecture
`qgis_process` dispatches to a `grace2-qgis` **AWS Batch Spot** job, reusing the EXACT solver substrate already shared by SFINCS/SWMM/OpenQuake/canopy: CE `grace2-solvers-spot` + queue `grace2-solvers` + role `grace2-batch-job-task-role`. `infra/aws-batch/main.tf` already names "QGIS-Processing" as a planned job-def on that engine-agnostic substrate -- this is the planned next user, not a new substrate.

Flow (one-shot Batch, mirrors `compute_canopy_height`): agent stages s3:// inputs + a `build_spec.json` -> `run_solver('qgis', model_setup_uri=<spec>)` -> `_run_solver_aws_batch` submits the job -> `wait_for_completion` polls `completion.json` on S3 -> agent reads `output_uris[]`. The s3://-only honesty guard, the in-flight-jobId turn-cancel (Invariant-8), and the early-FAILED DescribeJobs check are all **reused verbatim**.

**Key decision -- one-shot Batch, NOT the Fargate "coalesced warm worker"** (the in-flight HANDOFF prototype). The persistent-server shape does not fit `run_solver`'s one-shot seam and needs a whole new lifecycle layer. The coalescing speed win (~one QGIS init across an N-algo chain) is recovered later as an optional `chain` field in the build_spec (N algos, one init, inside one Batch job) -- v1 ships single-algo.

QGIS **Server** (styled OGC render) stays separate -- this migration is the Processing (compute) lane only; tiles remain TiTiler.

## Reuse vs net-new
**Reuse (~80%):** run_solver / _run_solver_aws_batch / _resolve_batch_job_def / wait_for_completion / cancel / s3-only guard (all verbatim); `services/workers/canopy/entrypoint.py` is a near-exact clone target; `openquake.tf` is the infra clone template; the existing `services/workers/qgis/Dockerfile` (qgis/qgis:ltr + grass/saga, ~695 algos) is the Batch container; shared CE/queue/IAM unchanged.

**Net-new (~20%):**
1. `services/workers/qgis/entrypoint.py` (clone canopy: read build_spec, download inputs to /data, `qgis_process run`, honesty-gate outputs, upload to the BARE `<run_id>/` prefix, write `completion.json`).
2. `infra/aws-batch/qgis.tf` (clone openquake.tf: ECR `grace2-qgis` + job-def 4vCPU/8GiB + 2 outputs; **no new IAM** -- reuses the shared job-task role).
3. `infra/aws-batch/canopy.tf` -- **PREREQUISITE GAP-FILL: canopy infra is MISSING today.** The canopy tool + worker are built but the Batch job-def is unregistered, so `compute_canopy_height` is NOT actually live-provisioned. Fill it (same clone).
4. `passthroughs.py`: async `_run_qgis_process_batch` branch + `GRACE2_QGIS_BACKEND` gate (Batch when the job-def resolves, else on-box docker fallback) + S3 stager + sync->async.
5. The build_spec schema (`{algorithm, qgis_args, inputs[], outputs[]}`, reserved `chain`).
6. `solver.py`: one line -- `'qgis':'aws-batch'` in SOLVER_WORKFLOW_REGISTRY.

## Plugins (both already exist; converge onto this substrate)
- **Guided digitizer:** the terra-draw lasso (web, shipped #129-133) + `digitize_water_body` (NDWI, on-box). Migration = the lasso supplies the AOI/seed and the NDWI+vectorize runs as a qgis build_spec chain on Batch. Client work is done; only server routing changes. (SAM/GPU variant = future.)
- **AI canopy detector** = `compute_canopy_height` (Meta HighResCanopyHeight ViT on CPU Spot Batch) -- a standalone Batch worker, the canonical template the qgis worker clones; NOT a QGIS plugin. Its only gap is the missing `canopy.tf` (above).

## NATE-gated live steps (one batched permission)
- `tofu apply infra/aws-batch` (adds qgis + canopy ECR + job-defs; additive; **no IAM widening**).
- ECR push: build + push `grace2-qgis` (and `grace2-canopy`); pre-push docker-history inspect.
- Agent-box env flip: `GRACE2_AWS_BATCH_JOB_DEF_QGIS` (+ `GRACE2_QGIS_BACKEND=aws-batch`) via the standing SSM deploy. **Reversible**: unset -> instant on-box fallback, no redeploy.
- SRS amendments (Decision Q / FR-AS-N / Appendix E GCP->AWS rewrite) -- only NATE lands.

## Top risks
- **Path-prefix delta** (highest-leverage correctness item): the worker MUST write the bare `<run_id>/` prefix (not `runs/<run_id>/`) or the agent never finds `completion.json` -> job appears to hang. Assert in the entrypoint test.
- **sync->async / offload interaction**: `qgis_process` is sync today; grep `workflows/` for sync callers before converting.
- **Image cold-pull latency**: qgis/qgis:ltr+grass+saga is multi-GB; first Spot job pays the pull (~1-2 min, unmeasured). Acceptable (matches solver cold-start); measure it.

## Effort
MEDIUM, ~3-4 focused jobs (~2.5 days autonomous build + the gated live steps). The Fargate-vs-Batch decision (resolved -> one-shot Batch) removes the ambiguity that kept the original HANDOFF blocked.
