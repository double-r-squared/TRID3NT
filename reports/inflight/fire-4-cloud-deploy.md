# FIRE-4: ELMFIRE Cloud Deploy (infra lane) - 2026-07-08

- Design: `reports/design/elmfire-engine-2026-07-07.md` (section 5/6, FIRE-4 row)
- Prior proofs: FIRE-1 `reports/inflight/fire-1-container-proof.md` (container +
  tutorial/verification repro), FIRE-2 (deck builder, commit 4390bbe), FIRE-3
  (composer chain, commit 4782e9b)
- Authorization: NATE explicit go 2026-07-08 (scale-to-zero Batch pattern, no
  always-on compute, no service disruption). **SCOPE REVISED mid-job by NATE:
  steps 1-3 ONLY (image + ECR + Batch job def); agent deploy + flood smoke
  HELD-FOR-LOCAL-VETTING** (see "Held" below).
- Verdict: **DONE (revised scope)** - image built + proven + pushed, job def
  registered, all resources inert + zero-idle-cost, live service untouched.

## 1. Worker image - grace2-elmfire

### What changed vs the FIRE-1 dev image (trid3nt/elmfire:dev)

`services/workers/elmfire/Dockerfile` reworked from the vendor-checkout dev
image into the CodeBuild-compatible Batch worker:

1. **Repo-root build context** (grace2-worker-builder parity): the pinned
   ELMFIRE release 2025.0526 (== FIRE-1 commit 23a4cbd27fc84b4b194fed48a8d933f7e7c7fdeb)
   is now FETCHED at build time from the GitHub tag archive and
   **SHA256-pinned** (`6f043180...da21590`, verified 2026-07-08) - the SWAN
   worker's digest-pin discipline.
2. **`-march=native` genericized to `-march=x86-64-v3`** (the FIRE-1
   portability caveat). Both `Makefile_elmfire` and `Makefile_elmfire_post`
   are sed-patched, and the patch is ASSERTED in the build (fails loudly if
   upstream renames the flag). AVX2 baseline is safe for the whole CE pool
   (c5/c6i/c7i/m7i/r7i, all Skylake+).
3. **NEW `services/workers/elmfire/entrypoint.py`** - the Batch envelope
   (OBJECT-STORE-IN -> SOLVE -> OBJECT-STORE-OUT), copied from the proven
   GeoClaw/SWAN workers. ELMFIRE simplification: NO in-container deck author -
   the FIRE-2 deck builds AGENT-SIDE (`run_elmfire.build_elmfire_deck`) and
   `stage_elmfire_manifest` stages a READY deck (`inputs/*.tif` +
   `inputs/elmfire.data`) to the cache bucket. The worker stages it, recreates
   `outputs/` + `scratch/` (byte-parity with the local-docker lane's
   `mkdir -p outputs scratch`), runs `elmfire_2025.0526 ./inputs/elmfire.data`,
   uploads `outputs/*.{bil,hdr,tif,csv}` + stdout/stderr + `completion.json`
   (`elmfire_stdout_uri`/`elmfire_stderr_uri` fields). Honesty gate: exit 0
   with no raster under `outputs/` -> `status=error`,
   `error_code=ELMFIRE_OUTPUT_EMPTY`.
4. **rasterio SKIPPED** (per the FIRE-4 brief check): `run_elmfire.py` confirms
   deck building is agent-side only; the worker venv carries **boto3 only**.
5. **gdal-bin KEPT deliberately** - verified against the 2025.0526 Fortran:
   `elmfire_io.f90` shells out to `gdal_translate`
   (`EXECUTE_COMMAND_LINE`, `PATH_TO_GDAL` default `/usr/bin`) to convert
   GeoTIFF inputs to ENVI BSQ for reading. Dropped from the dev image:
   python3-gdal, bc, jq, pigz (tutorial-script baggage, unused by the solver).
6. True multi-stage stays (compilers only in the builder); binaries-only COPY
   (no tutorials/verification trees in the worker).

### Size (container-hygiene pre-push inspect)

| | |
|---|---|
| Unpacked on disk | **598 MB** |
| Compressed (ECR) | **157 MB** |

Layer breakdown: 87.7 MB ubuntu:22.04 (ECR Public mirror) + 293 MB runtime apt
(gdal-bin dominates; libgfortran5/libgomp1/openmpi-bin/python3) + 56.1 MB
boto3 venv + 4.0 MB solver binaries + ~0.2 MB worker package.

### Functional proof of the genericized binary (local, pre-push)

Cross-image check: tutorial-01 inputs generated with the PROVEN dev image,
then solved with the NEW `x86-64-v3` image:

```
Meteorology band 1: Case # 1 complete.  Fire area: 3851.9 acres.
End of simulation reached successfully. Shutting down.   (5.5 s, 4 cpus)
```

**Fire area 3851.9 acres == the FIRE-1 proof's number exactly**; outputs =
`time_of_arrival/flin/vs .bil+.hdr` + `fire_size_stats.csv` (matches
`ELMFIRE_OUTPUT_GLOBS`). Entrypoint import + no-args usage path + binary
banner + `gdal_translate` presence all smoke-checked in-image at build time.
Worker tests: `services/workers/elmfire/tests/` 21 passed.

### ECR push

Built locally (rootless docker) and pushed with local AWS creds (transient
`connection reset` on the big apt layer; succeeded on retry - no CodeBuild
fallback needed):

- `226996537797.dkr.ecr.us-west-2.amazonaws.com/grace2-elmfire:latest`
- `226996537797.dkr.ecr.us-west-2.amazonaws.com/grace2-elmfire:2025.0526-fire4`
  (same digest; release-pinned tag for repro)

## 2. Infra - infra/aws-batch/elmfire.tf (applied)

Mirrors `swan.tf`/`geoclaw.tf` exactly: ONLY ELMFIRE-scoped resources on the
EXISTING engine-agnostic substrate. **No new compute environment, no new
queue, no new IAM** - reuses `grace2-solvers` (Spot-first, scale-to-zero) +
`grace2-batch-job-task-role` (already grants exactly what the worker needs:
cache-bucket read for the staged deck, runs-bucket write for outputs).

`tofu plan` summary (aws-batch root): **3 to add, 0 to change, 0 to destroy**
- `aws_ecr_repository.elmfire` (grace2-elmfire, scan-on-push)
- `aws_ecr_lifecycle_policy.elmfire` (keep last 10 images)
- `aws_batch_job_definition.elmfire` (**name `grace2-elmfire` ==
  `run_elmfire.ELMFIRE_BATCH_JOB_DEF_NAME`**, baseline 4 vCPU / 8192 MiB with
  per-job containerOverrides, 3600 s timeout, awslogs prefix `elmfire`)

Apply result: clean; verified live -
`aws batch describe-job-definitions grace2-elmfire` -> ACTIVE rev 1, image
`.../grace2-elmfire:latest`, 4 vCPU / 8192 MiB / 3600 s.

Baseline sizing rationale: FIRE-1-calibrated county runs are
seconds-to-minutes, and the 2025.0526 `gnu_mpi` build runs single-rank (no
OpenMP flag in `Makefile_elmfire`), so "small" is honest; Monte Carlo (FIRE-5+)
maps onto array jobs on the same def.

## 3. Authored but NOT applied (file-only, awaiting the held agent deploy)

These land in git with this job but change NOTHING live until a future,
separately-gated agent deploy:

- `infra/aws-agent-isolation/ecs.tf`: adds
  `GRACE2_AWS_BATCH_JOB_DEF_ELMFIRE=grace2-elmfire` to the agent task-def env
  (the activation switch - `run_elmfire` deliberately does not seed
  `SOLVER_BATCH_JOBDEF_REGISTRY`). **NOT tofu-applied.** Until it is, the
  agent's ELMFIRE Batch lane stays inert by design.
- `services/agent/Dockerfile`: `COPY services/workers /opt/venv/services/workers`
  - the containerized agent currently ships NO `services/workers` sources, so
  the `parents[5]` repo-root discovery (`run_elmfire.load_deck_builder`,
  `run_modflow._import_gwt_adapter`) cannot resolve on Fargate sessions; the
  EC2 box used to get these via the deploy bundle. Required for the cloud
  ELMFIRE lane (agent-side deck build) AND un-breaks the MODFLOW archetypes
  on Fargate. **NOT built/pushed.**
- `infra/aws-agent-isolation/buildspec.agent.yml`: TRIGGER doc updated - the
  agent build-context tarball must now include the `services/workers` .py
  subset (`find services/workers -name '*.py' -not -path '*__pycache__*'`;
  never the multi-GB spike dirs).

## 4. HELD-FOR-LOCAL-VETTING (NATE scope revision, 2026-07-08)

- **Step 4 (agent deploy): NOT STARTED, not done.** No agent code deployed;
  no CodeBuild agent build started; no task-def revision applied; live
  sessions untouched. (Note for the eventual deploy: the EC2 box
  i-0251879a278df797f is TERMINATED - scale-to-zero Phase 0 - so
  `deploy_agent_onbox.sh` has no target; the live path is the
  grace2-agent-builder CodeBuild image + Fargate-per-session.)
- **Step 5 (flood smoke): NOT RUN** - correctly gated on the agent deploy that
  did not happen; nothing on the live serving path changed.

## 5. Cost confirmation (step 6)

**Nothing always-on was created.**

- ECR repo + image: storage-only (~157 MB compressed ~= $0.02/mo), 10-image
  lifecycle cap; zero idle compute.
- Batch job definition: free at rest; jobs (none submitted) would place on the
  EXISTING scale-to-zero Spot CE and tear down after.
- No new CE / queue / role / service / instance / endpoint. The
  aws-agent-isolation root was NOT applied.
- The lane is doubly inert: the activation env var is not set on any live
  agent, and no live agent even carries the FIRE-3 code yet.

## Rollback

`tofu destroy -target=aws_batch_job_definition.elmfire
-target=aws_ecr_lifecycle_policy.elmfire -target=aws_ecr_repository.elmfire`
in `infra/aws-batch` (plus `aws ecr batch-delete-image` for the tags), and
revert the three file-only changes. Nothing else to unwind.
