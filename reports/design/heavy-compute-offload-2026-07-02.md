# Heavy-compute offload — thin agent, full-pipeline Batch workers (2026-07-02)

## Problem
The always-on per-session agent (Fargate, 2 vCPU / **16 GB**) runs the memory-heavy
**model BUILD** (SFINCS `build_sfincs_model`/hydromt, MODFLOW `build_modflow_deck`/FloPy)
and **ALL postprocess** (`postprocess_*` rasterize solver output -> COG) in-process.
Only the numerical **solve** is on Batch. The 16 GB exists for the in-agent build (the
Chattanooga OOM). This violates the execution-architecture norm (minimal always-on agent +
tear-down Spot Batch for bursty sim work) and blocks true scale-to-near-zero: a mostly-idle
session still holds 16 GB. Cost proof: 2026-07-01 Fargate GB-hours = $46 of $50 ECS.

## Target design
The agent is a **thin orchestrator**. Every per-run heavy stage (build + solve + postprocess)
runs inside ONE tear-down Batch Spot job per engine (monolithic per-engine worker, matching the
existing `grace2-sfincs-quadtree` which already does build+solve on Batch). Flow:

    agent: parse intent -> fetch INPUT refs (S3 COGs, for the input-layer preview only)
        -> compose job_spec JSON -> submit ONE Batch job -> poll DescribeJobs off-loop
        -> worker writes COGs + manifest.json to S3 -> agent reads manifest -> publish thin layer refs

The agent NEVER loads a DEM or a NetCDF into its own RAM. Build, solve, and rasterize all happen
in the worker container (which already has the solver + can carry hydromt/flopy/rasterio).
`completion.json` / manifest on S3 is the completion contract (already exists for solves).

Reference implementation that already works this way: **coastal quadtree** (`grace2-sfincs-quadtree`,
build_spec -> Batch build+solve). We extend it to also postprocess-in-worker and replicate the
pattern to the remaining engines.

Why monolithic (build+solve+postprocess in one job) not 3 chained jobs: fewer moving parts,
no cross-job scheduling latency, agent submits/awaits ONE handle, per-engine job def sizes for
the whole pipeline. Staged jobs are a later option only if a stage needs radically different resources.

## Migration plan (staged, verify + deploy each before the next)
1. **Pluvial SFINCS (reference/proof)** — move `build_sfincs_model` + `postprocess_flood` into the
   `grace2-sfincs` worker; thin `model_flood_scenario` to compose-spec -> submit -> read-manifest -> publish.
   Verify E2E live. This is the confirmed 16 GB driver.
2. **MODFLOW** — move `build_modflow_deck` (FloPy) + `postprocess_modflow` into `grace2-modflow` worker;
   thin `run_modflow` + the archetype composers.
3. **Postprocess for the rest** — GeoClaw/SWMM/OpenQuake/SWAN/Landlab already solve on Batch; move their
   `postprocess_*` into their workers so the agent stops rasterizing large outputs.
4. **Shrink the agent** — once no heavy build/postprocess runs in-agent, drop the `grace2-agent-session`
   task def from 16 GB -> ~4 GB (and CPU as fits). ~4x cheaper per active session; OOM on the live box
   becomes impossible.

## Keep in-agent (for now)
- **fetch_* input tools** — they return S3 COG refs and feed the input-layer previews; lighter than the
  build (though `fetch_topobathy`'s CUDEM merge is heavy — a Phase-2 offload candidate).
- orchestration, WS/session/state, publish (thin layer refs), telemetry.

## Risks / watch
- Worker image size: adding hydromt/flopy/rasterio to a solve-only worker grows the image — inspect per
  container-hygiene norm (multi-stage, minimal base) before ECR push.
- Input-preview UX: agent still fetches inputs for the preview; the worker re-reads those same S3 URIs for
  the build (no double-fetch of raw tiles — pass the merged DEM/landcover COG URIs in the job_spec).
- Per-engine live verification (drive via Haiku) before each deploy; deploys gated to NATE.
- The 4 review-audit temp-dir leaks (Theme C) largely disappear once postprocess moves to the ephemeral
  worker rundir — fold that in.

## Payoff
Uniform build+solve+postprocess-on-Batch for every engine; thin ~4 GB agent that is genuinely near-zero
when idle; no live-box OOM; per-session Fargate cost cut ~4x; matches the scale-to-near-zero north star.
