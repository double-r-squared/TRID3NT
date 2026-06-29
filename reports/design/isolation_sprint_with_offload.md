# Per-user isolation sprint -- integrated with the offload prerequisites

Status: **PLAN (NATE go 2026-06-29 to fold the offload work into the isolation
sprint as required pre-cutover gates).** Supersedes the standalone
`infra/aws-agent-isolation/RUNBOOK.md` step ordering by inserting a Phase 0 that
MUST land before the dark agent-image build.

## Why these three are pre-cutover gates (not "later")

The per-user agent runs as an ephemeral **Fargate task**, not the EC2 box. That
changes two things the monolith hid:

1. **A Fargate task cannot `docker run` a sibling QGIS container** (no
   docker-in-docker). Today `passthroughs.py:_run_qgis_process_docker` shells
   `docker run grace2-qgis qgis_process run` ON the EC2 box. On an isolated agent
   that call has nowhere to run -> `qgis_process` BREAKS. So QGIS MUST dispatch
   to Batch before (or as part of) the cutover. **Hard gate.**
2. The ~1.4 GB agent image dominates Fargate cold-start (~30-40s pull) and sets
   the per-task memory floor. The loop only needs Bedrock + the 94 tool
   DEFINITIONS + the WS layer; the heavy geo/exec stack belongs on the workers.

## Phase 0 -- OFFLOAD PREREQUISITES (must precede the dark agent-image build)

**P0-A. QGIS Processing -> AWS Batch.** Execute the existing design
(`reports/design/qgis_processing_on_batch_plan.md`): `services/workers/qgis/
entrypoint.py` (clone the canopy worker), `infra/aws-batch/qgis.tf` (ECR
`grace2-qgis` + 4vCPU/8GiB job-def, reuse the shared CE/queue/job-task role),
**plus the prerequisite `canopy.tf` gap-fill** (canopy job-def is unregistered
today), `passthroughs.py` async `_run_qgis_process_batch` branch gated by
`GRACE2_QGIS_BACKEND=aws-batch` (Batch when the job-def resolves, else on-box
docker fallback for the EC2 box only), `solver.py` `'qgis':'aws-batch'`. Effort
~2.5 focused days. Gated live steps: `tofu apply infra/aws-batch`, ECR push,
the agent env flip. **Removes the docker-on-box dependency -> unblocks Fargate.**

**P0-B. Thin-loop-image split.** Split `services/agent/Dockerfile` so the LOOP
image carries only: Bedrock adapter + the 94 tool DEFINITIONS + the WS/asyncio
layer + boto3 + light deps. Move the heavy execution closure
(GDAL/rasterio/rioxarray/pyproj/shapely, pandas/numpy/scipy/xarray,
hydromt-sfincs/hydromt/flopy) OUT of the loop image -- those already run on the
Batch / QGIS / engine workers. PREREQ: confirm every tool DEFINITION imports
without its heavy runtime dep (lazy-import the heavy bits at call time; many
already do via the `_ALWAYS_OFFLOAD_SYNC_TOOLS` pattern -- audit + fix the rest).
Target: loop image ~300-500 MB (from ~1.4 GB) -> faster cold-start + smaller
task floor.

**P0-C. Drop dead GCP deps.** Remove `google-cloud-storage` + `google-cloud-run`
(~90 MB) from the agent dep closure -- GCP is decommissioned; only `google-genai`
`types` is needed for the Bedrock carve-out. Quick win; pairs with P0-B.

## Phase 1+ -- the existing RUNBOOK, with the image from Phase 0

The dark agent-image build (RUNBOOK step 1, CodeBuild) now bakes the **thin,
QGIS-on-Batch, no-dead-GCP** image. Everything after is unchanged: tofu apply the
dark data plane (cluster/ALB/routes/reaper) -> broker up dark -> canary + the
2-session crash isolation proof -> **gated CloudFront /ws cutover (NATE go,
post-judging)** -> drain + decommission the t3.medium.

## Sizing (carried from the box-shrink analysis)

- Broker (always-on): trim to **0.25 vCPU / 0.5 GB** -- pure byte-proxy + RunTask.
- Agent task (scale-to-zero, per session): launch at **1 vCPU / 2 GB**; after
  P0-B lands, re-evaluate down to **0.5 vCPU / 1 GB** (smaller image -> lower
  floor + faster cold-start). The single-user in-process peak (densify a big FC,
  topobathy merge, code_exec) sets the floor -- NOT the connection count.
- The t3.medium is decommissioned at RUNBOOK step 8 (its cost goes away).

## Sequencing / effort

P0-C (hours) || P0-B (~1 day, gated on the lazy-import audit) -> then P0-A
(~2.5 days, the long pole + the hard gate) can run in parallel with the broker
deploy-phase work that does NOT need the image. Realistic: a multi-day sprint,
**post-judging**. None of Phase 0 touches the live single box until its own
gated env flip, so the demo path is unaffected throughout.
